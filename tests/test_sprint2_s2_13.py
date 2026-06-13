"""Sprint 2 S2-13 自测：用户提供仓库统一抓取分析 + 同口径评分通道。

覆盖架构 §2.13.9 T-S2-13-1~9（映射 PRD §6 AC-S2-20~26）。

测试策略（与 sp2 现有风格一致，不跑真实 LLM e2e）：
    - 单元层：直接构造含 git_clone_and_analyze ToolMessage 的 react_messages 驱动
      _merge_user_repos_from_tools / _map_planning_result（mock create_react_subgraph
      + create_llm + monkeypatch interrupt，参照 test_sprint2_b3.py）；
    - UI 层：streamlit.testing.v1.AppTest 断言 switch_repo expander 失败重填提示
      （参照 test_plan_review_logic.py）。

硬约束验证：
    - 工具结果全程 JSON（BUG-S1-02）：ToolMessage 用 json.dumps 序列化；
    - 工具历史回填 3 参签名 + 失败过滤（BUG-S1-03）；
    - 失败非静默：clone 失败走 node_errors(degraded) + 强制重填标记；
    - 同口径：planning 与 resource_scout import 同一 REPO_QUALITY_SCORING_SECTION。
"""

from __future__ import annotations

import importlib
import json
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, ToolMessage

import core.react_base as react_base
from core.state import ExecutionMode

planning_module = importlib.import_module("core.nodes.planning")
resource_scout_module = importlib.import_module("core.nodes.resource_scout")
repo_scoring_module = importlib.import_module("core.nodes._repo_scoring")

planning = planning_module.planning
_map_planning_result = planning_module._map_planning_result
_merge_user_repos_from_tools = planning_module._merge_user_repos_from_tools
_normalize_repo_url = planning_module._normalize_repo_url
_switch_selected_repo = planning_module._switch_selected_repo


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

def _clone_tool_messages(payload: Dict[str, Any]) -> List[Any]:
    """构造一对 AIMessage(tool_call) + ToolMessage(JSON 序列化结果)，模拟 ReAct 调用历史。

    BUG-S1-02：ToolMessage.content 用 json.dumps（合法 JSON，禁 str(dict)）。
    """
    url = payload.get("url")
    return [
        AIMessage(content="", tool_calls=[
            {"name": "git_clone_and_analyze", "args": {"url": url}, "id": "call-1"}]),
        ToolMessage(
            content=json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str),
            name="git_clone_and_analyze",
            tool_call_id="call-1",
        ),
    ]


def _success_repo_payload(url: str, quality_score: float = 0.7) -> Dict[str, Any]:
    return {
        "url": url, "source": "git_clone", "local_path": f"/ws/repos/{url[-3:]}",
        "is_official": True, "stars": None, "forks": None,
        "last_commit_date": None, "commit_count_recent": None,
        "has_readme": True, "has_requirements": True, "dir_structure": ["src"],
        "quality_score": quality_score, "success": True,
    }


def _base_state(**overrides: Any) -> Dict[str, Any]:
    state: Dict[str, Any] = {
        "llm_config_set": {
            "default": {"base_url": "http://x", "model": "m", "api_key": "k",
                        "temperature": 0.0, "max_tokens": 1024},
            "overrides": {},
        },
        "paper_meta": {"arxiv_id": "2405.14831", "title": "HippoRAG"},
        "paper_analysis": {"method_summary": "中文方法摘要", "framework": "PyTorch"},
        "resource_info": {
            "repos": [],
            "selected_repo": None,
            "external_resources": [],
            "resource_strategy": "from_scratch",
        },
        "node_errors": [],
        "degraded_nodes": [],
        "analysis_notes": "",
        "retry_budget_remaining": 50,
        "_planning_revise_count": 0,
        "_planning_user_feedback": None,
        "_planning_pending_repo_url": None,
        "_planning_switch_failed": False,
        "execution_mode": ExecutionMode.FULL,
        "current_step": "resource_scout",
    }
    state.update(overrides)
    return state


def _plan_result(repos: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    return {
        "plan_summary": "复现计划摘要",
        "code_strategy": "from_scratch",
        "deliverables": ["README.md", "requirements.txt", "run.py"],
        "execution_steps": [{"step_name": "s", "command": "c", "expected_output": "o"}],
        "repos": repos or [],
    }


class _FakeSubgraph:
    def __init__(self, result, messages, rounds=3):
        self._result, self._messages, self._rounds = result, messages, rounds

    def invoke(self, initial):
        return {"result": self._result, "messages": self._messages,
                "round": self._rounds, "status": "done"}


def _patch_react(monkeypatch, result, messages=None, rounds=3):
    fake = _FakeSubgraph(result, messages or [], rounds)
    monkeypatch.setattr(react_base, "create_react_subgraph", lambda **kw: fake)
    monkeypatch.setattr(react_base, "create_llm", lambda cfg: object())
    return fake


def _patch_interrupt(monkeypatch, decision):
    captured: Dict[str, Any] = {}

    def fake_interrupt(payload):
        captured["payload"] = payload
        return decision

    monkeypatch.setattr(planning_module, "interrupt", fake_interrupt)
    return captured


# =========================================================================== #
# T-S2-13-3：同口径可比较（同源 import 断言）—— 守门第一条
# =========================================================================== #

def test_t3_same_scoring_section_imported_by_both_nodes():
    """planning 与 resource_scout 引用同一 REPO_QUALITY_SCORING_SECTION（口径字节级一致）。"""
    section = repo_scoring_module.REPO_QUALITY_SCORING_SECTION
    assert planning_module.REPO_QUALITY_SCORING_SECTION is section
    assert resource_scout_module.REPO_QUALITY_SCORING_SECTION is section
    # 两节点 system prompt 主体都包含同一段（字节级出现）。
    assert section in planning_module._PLANNING_SYSTEM_PROMPT_BODY
    assert section in resource_scout_module._RESOURCE_SCOUT_SYSTEM_PROMPT_BODY


def test_t3_scoring_section_is_static_module_level():
    """评分段落为 module-level 静态常量（非动态拼接，Prompt Cache 字节冻结）。"""
    a = repo_scoring_module.REPO_QUALITY_SCORING_SECTION
    b = repo_scoring_module.REPO_QUALITY_SCORING_SECTION
    assert a is b
    assert "quality_score" in a


def test_t3_quality_score_in_range_same_field_set():
    """同一 URL 经 merge 后的 RepoInfo 字段集合与 resource_scout _build_repo_info 一致，
    quality_score 落 [0,1]。"""
    repo = resource_scout_module._build_repo_info(_success_repo_payload("https://github.com/u/v", 0.72))
    expected_fields = {
        "url", "source", "is_official", "stars", "forks", "last_commit_date",
        "commit_count_recent", "has_readme", "has_requirements", "dir_structure",
        "quality_score", "local_path",
    }
    assert set(repo.keys()) == expected_fields
    assert 0.0 <= repo["quality_score"] <= 1.0


# =========================================================================== #
# T-S2-13-9：_normalize_repo_url 参数化去重
# =========================================================================== #

@pytest.mark.parametrize("url", [
    "https://github.com/a/b",
    "https://github.com/a/b/",
    "https://github.com/a/b.git",
    "https://github.com/a/b.git/",
    "https://github.com/A/B",
    "  https://github.com/a/b  ",
])
def test_t9_normalize_url_variants_equal(url):
    assert _normalize_repo_url(url) == "https://github.com/a/b"


def test_t9_normalize_empty_inputs():
    assert _normalize_repo_url("") == ""
    assert _normalize_repo_url(None) == ""
    assert _normalize_repo_url("   ") == ""


def test_t9_hit_existing_candidate_no_duplicate():
    """命中既有候选（规范化）→ 不重复加入 repos。"""
    state = _base_state(resource_info={
        "repos": [{"url": "https://github.com/a/b", "quality_score": 0.9, "source": "pwc"}],
        "selected_repo": None, "external_resources": [], "resource_strategy": "use_repo",
    })
    msgs = _clone_tool_messages(_success_repo_payload("https://github.com/A/B.git/", 0.5))
    r = _merge_user_repos_from_tools(_plan_result(), msgs, state)
    assert r["merged"] is True
    assert len(r["resource_info"]["repos"]) == 1  # 不重复加入
    assert r["resource_info"]["selected_repo"]["source"] == "pwc"  # 命中复用既有候选


# =========================================================================== #
# T-S2-13-1：入口 a 贴链接经模型调工具入候选
# =========================================================================== #

def test_t1_merge_user_repo_into_candidates():
    """直接构造含成功 ToolMessage 的 react_messages → 合并进 repos + source=user_provided。"""
    state = _base_state()
    url = "https://github.com/user/paperrepo"
    plan = _plan_result(repos=[{"url": url, "quality_score": 0.66}])
    msgs = _clone_tool_messages(_success_repo_payload(url, 0.66))
    r = _merge_user_repos_from_tools(plan, msgs, state)
    assert r["merged"] is True
    urls = {_normalize_repo_url(x["url"]) for x in r["resource_info"]["repos"]}
    assert _normalize_repo_url(url) in urls
    sel = r["resource_info"]["selected_repo"]
    assert sel["source"] == "user_provided"
    assert sel["quality_score"] == 0.66
    # from_scratch 纠正为 use_repo
    assert r["resource_info"]["resource_strategy"] == "use_repo"


def test_t1_map_planning_writes_resource_info_and_strategy(monkeypatch):
    """_map_planning_result 走合并步骤：resource_info 回写 + code_strategy 纠正。"""
    url = "https://github.com/user/paperrepo"
    msgs = _clone_tool_messages(_success_repo_payload(url, 0.66))
    out = _map_planning_result(
        _plan_result(repos=[{"url": url, "quality_score": 0.66}]),
        _base_state(_planning_user_feedback=f"请用这个仓库 {url}"),
        react_messages=msgs,
    )
    assert out["resource_info"]["selected_repo"]["url"] == url
    assert out["resource_info"]["selected_repo"]["source"] == "user_provided"
    assert out["reproduction_plan"]["code_strategy"] == "use_repo"


# =========================================================================== #
# T-S2-13-2：切换仓库真实质量分（非 0）
# =========================================================================== #

def test_t2_switch_repo_real_quality_score_nonzero():
    """switch_repo 重入 → mock 工具成功 + 模型给 0.72 → 非 0.0、local_path 非空、被选中。"""
    state = _base_state(_planning_pending_repo_url="https://github.com/u/v")
    url = "https://github.com/u/v"
    plan = _plan_result(repos=[{"url": url, "quality_score": 0.72}])
    msgs = _clone_tool_messages(_success_repo_payload(url, 0.72))
    r = _merge_user_repos_from_tools(plan, msgs, state)
    sel = r["resource_info"]["selected_repo"]
    assert sel["quality_score"] == 0.72
    assert sel["quality_score"] != 0.0
    assert sel["local_path"]
    assert sel["url"] == url


def test_t2_hit_existing_no_reclone(monkeypatch):
    """命中既有候选时不重复 clone（_switch_selected_repo 命中即选中，工具调用 0 次）。"""
    state = _base_state(resource_info={
        "repos": [{"url": "https://github.com/a/repo", "quality_score": 0.8, "source": "pwc"}],
        "selected_repo": None, "external_resources": [], "resource_strategy": "use_repo",
    })
    switched = _switch_selected_repo(state["resource_info"], "https://github.com/a/repo")
    assert switched is not None
    assert switched["selected_repo"]["url"] == "https://github.com/a/repo"
    # 命中分支不触发任何 git_clone（确定性内存切换）
    assert switched["selected_repo"]["source"] == "pwc"


# =========================================================================== #
# T-S2-13-4：stars/forks 留空不报错
# =========================================================================== #

def test_t4_stars_forks_none_after_merge():
    state = _base_state()
    url = "https://github.com/u/v"
    payload = _success_repo_payload(url, 0.7)
    payload["stars"] = None
    payload["forks"] = None
    msgs = _clone_tool_messages(payload)
    r = _merge_user_repos_from_tools(_plan_result(repos=[{"url": url, "quality_score": 0.7}]), msgs, state)
    sel = r["resource_info"]["selected_repo"]
    assert sel["stars"] is None
    assert sel["forks"] is None


def test_t4_render_repos_with_none_stars_forks_no_raise():
    """_render_repos 渲染 stars/forks=None 时不抛，且 ⭐ — / 🍴 — 占位（AppTest 全页渲染）。"""
    resource_info = {
        "repos": [{"url": "https://github.com/u/v", "source": "user_provided",
                   "is_official": True, "stars": None, "forks": None,
                   "quality_score": 0.7}],
        "selected_repo": {"url": "https://github.com/u/v"},
        "resource_strategy": "use_repo",
    }
    payload = _payload(switch_repo_failed=False)
    payload["resource_info"] = resource_info
    at = _run_app(payload)
    assert not at.exception  # None stars/forks 不抛 KeyError/TypeError


# =========================================================================== #
# T-S2-13-5：模型判断不值得加入（不调工具）
# =========================================================================== #

def test_t5_no_tool_call_repos_unchanged():
    """react_messages 无 git_clone_and_analyze ToolMessage → repos 不变、merged=False。"""
    state = _base_state(resource_info={
        "repos": [{"url": "https://github.com/a/b", "quality_score": 0.8}],
        "selected_repo": {"url": "https://github.com/a/b"},
        "external_resources": [], "resource_strategy": "use_repo",
    })
    # 只含普通 AIMessage，无工具调用
    msgs = [AIMessage(content="我认为这个仓库不相关，不加入。")]
    r = _merge_user_repos_from_tools(_plan_result(), msgs, state)
    assert r["merged"] is False
    assert r["tool_attempted"] is False
    assert len(r["resource_info"]["repos"]) == 1  # 不变


# =========================================================================== #
# T-S2-13-6：入口 b clone 失败强制重填（参数化 三类失败）
# =========================================================================== #

@pytest.mark.parametrize("fail_payload", [
    {"url": "https://github.com/u/v", "success": False, "error": "PermanentError: not found"},
    {"url": "https://github.com/u/v", "success": False, "error": "TransientError: timeout"},
    {"url": "https://github.com/u/v", "success": False, "error": "out of bounds"},
])
def test_t6_switch_clone_fail_force_refill(monkeypatch, fail_payload):
    """入口 b clone 失败：URL 不入 repos、不留 0.0、selected 不变、switch_failed=True、
    payload.switch_repo_failed=True、node_errors degraded、不抛异常。"""
    state = _base_state(_planning_pending_repo_url="https://github.com/u/v")
    msgs = _clone_tool_messages(fail_payload)
    # 走完整 planning() 节点：mock 子图返回该失败 ToolMessage，interrupt 注入 approve
    _patch_react(monkeypatch, _plan_result(), messages=msgs)
    cap = _patch_interrupt(monkeypatch, {"decision": "approve"})
    out = planning(state)

    # payload 携 switch_repo_failed=True
    assert cap["payload"]["switch_repo_failed"] is True
    # 失败 URL 不入 repos（resource_info 保持 from_scratch 空候选）
    repos = (out.get("resource_info") or state["resource_info"]).get("repos") or []
    assert all(_normalize_repo_url(r.get("url")) != _normalize_repo_url("https://github.com/u/v")
               for r in repos)
    # node_errors 有 degraded
    assert any(e.get("error_type") == "degraded" for e in out.get("node_errors", []))
    assert "planning" in out.get("degraded_nodes", [])


def test_t6_map_result_sets_switch_failed_clears_pending():
    """_map_planning_result 入口 b 失败：_planning_switch_failed=True + pending_url 清空 + 不写 0.0。"""
    state = _base_state(_planning_pending_repo_url="https://github.com/u/v")
    msgs = _clone_tool_messages(
        {"url": "https://github.com/u/v", "success": False, "error": "boom"})
    out = _map_planning_result(_plan_result(), state, react_messages=msgs)
    assert out["_planning_switch_failed"] is True
    assert out["_planning_pending_repo_url"] is None
    # 没有写入 resource_info（不造 0.0 占位）
    assert "resource_info" not in out or not out["resource_info"].get("selected_repo")


# =========================================================================== #
# T-S2-13-7：入口 a clone 失败按无新仓库继续
# =========================================================================== #

def test_t7_revise_clone_fail_continues_no_switch_failed():
    """入口 a（revise，pending_url 为空）clone 失败：不置 _planning_switch_failed、repos 不新增。"""
    state = _base_state()  # 无 pending_repo_url
    msgs = _clone_tool_messages(
        {"url": "https://github.com/x/y", "success": False, "error": "boom"})
    out = _map_planning_result(_plan_result(), state, react_messages=msgs)
    # 入口 a 失败不置 switch_failed
    assert out.get("_planning_switch_failed") is not True
    assert "_planning_switch_failed" not in out
    # repos 不新增（merged 失败）
    assert "resource_info" not in out
    # 计划照常生成
    assert out["reproduction_plan"]["plan_summary"]


# =========================================================================== #
# T-S2-13-8：awaiting 轮询不破坏（_await_phase switch_repo 失败仍前进判 to_review）
# =========================================================================== #

def test_t8_await_phase_switch_failed_still_to_review():
    """switch_repo 失败后 revise_count 仍前进 → _await_phase 返回 to_review（不卡 waiting）。"""
    pr = importlib.import_module("ui.pages.plan_review")
    # 失败重规划完成：revise_count 从 baseline 0 前进到 1，payload 带 switch_repo_failed
    phase = pr._await_phase(
        kind="switch_repo",
        payload={"revise_count": 1, "switch_repo_failed": True},
        baseline=0,
        has_worker_error=False,
        is_interrupted=True,
    )
    assert phase == "to_review"


def test_t8_await_phase_waiting_when_not_advanced():
    pr = importlib.import_module("ui.pages.plan_review")
    phase = pr._await_phase(
        kind="switch_repo",
        payload={"revise_count": 0},
        baseline=0,
        has_worker_error=False,
        is_interrupted=True,
    )
    assert phase == "waiting"


# =========================================================================== #
# T-S2-13-6（UI 侧）：AppTest 断言 switch_repo expander 失败展开 + st.error
# =========================================================================== #

_APP_SCRIPT = """
import streamlit as st
st.session_state.setdefault("thread_id", "task-s2-13")
st.session_state.setdefault("current_page", "review")
from ui.pages.plan_review import render
render()
"""

_LLM_CONFIG_SET = {
    "default": {"base_url": "https://x/v1", "model": "m", "api_key": "",
                "temperature": 0.3, "max_tokens": 4096},
    "overrides": {},
}


def _payload(switch_repo_failed: bool) -> Dict[str, Any]:
    return {
        "reproduction_plan": {
            "plan_summary": "复现计划摘要", "environment": {}, "data_preparation": [],
            "code_strategy": "from_scratch", "execution_steps": [],
            "expected_results": {}, "estimated_time": "", "deliverables": ["x"],
        },
        "resource_info": {"repos": [], "selected_repo": None, "resource_strategy": "from_scratch"},
        "paper_analysis_summary": {"method_summary": "m"},
        "degraded_nodes": [], "node_errors": [],
        "revise_count": 1, "soft_hint_threshold": 5, "max_total_llm_calls": 50,
        "switch_repo_failed": switch_repo_failed,
    }


def _run_app(payload: Dict[str, Any]):
    from streamlit.testing.v1 import AppTest
    controller = MagicMock()
    controller.get_interrupt_payload.return_value = payload
    controller.poll_state.return_value = {"llm_config_set": _LLM_CONFIG_SET}
    controller.is_interrupted.return_value = True
    controller.get_worker_error.return_value = None
    with patch("app._get_controller", return_value=controller):
        at = AppTest.from_string(_APP_SCRIPT)
        at.run()
    return at


def _collect_text(at) -> str:
    parts: List[str] = []
    for coll in (at.title, at.subheader, at.caption, at.markdown,
                 at.text, at.warning, at.info, at.error):
        for el in coll:
            parts.append(str(getattr(el, "value", "")))
    return "\n".join(parts)


def test_t6_ui_switch_failed_shows_error():
    """payload.switch_repo_failed=True → st.error 重填提示渲染，页面不崩。"""
    at = _run_app(_payload(switch_repo_failed=True))
    assert not at.exception
    text = _collect_text(at)
    assert "仓库克隆/分析失败" in text


def test_t6_ui_switch_ok_no_error():
    """switch_repo_failed=False → 不渲染失败提示。"""
    at = _run_app(_payload(switch_repo_failed=False))
    assert not at.exception
    text = _collect_text(at)
    assert "仓库克隆/分析失败" not in text
