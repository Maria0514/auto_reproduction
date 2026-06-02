"""Sprint 2 任务 B2 自测：core/nodes/resource_scout.py。

覆盖 dev-plan §B2 CP-B2-1 ~ CP-B2-10（pytest 标准函数风格，参考 tests/test_sprint2_a5.py）。

测试策略（mock 覆盖核心路径，不依赖真实网络 / LLM；真实 e2e 留 E 阶段）：
    - 大多数 CP 直接驱动 _map_resource_scout_result（确定性、无 LLM）；
    - 需要"走 ReAct wrapper"的 CP（路由 / max_rounds / Prompt Cache 前缀）通过 monkeypatch
      core.react_base.create_react_subgraph + create_llm 注入脚本化子图结果；
    - 工具历史回填用真实 langchain_core AIMessage / ToolMessage 构造。

硬约束验证：
    - BUG-S1-03 治理范式：_map 用 3 参签名（含 react_messages），backfill 兜底 + WARNING；
    - Prompt Cache：_RESOURCE_SCOUT_SYSTEM_PROMPT_BODY 主体字节级一致、无论文级动态值。
"""

from __future__ import annotations

import importlib
import inspect
import json
import logging
from typing import Any, Dict, List, Optional

import pytest

from langchain_core.messages import AIMessage, ToolMessage

import core.react_base as react_base
from core.state import RepoInfo, ResourceInfo

resource_scout_module = importlib.import_module("core.nodes.resource_scout")
resource_scout = resource_scout_module.resource_scout
_map_resource_scout_result = resource_scout_module._map_resource_scout_result
_backfill_repos_from_tools = resource_scout_module._backfill_repos_from_tools
_build_resource_scout_system_prompt = resource_scout_module._build_resource_scout_system_prompt
RESOURCE_SCOUT_SCHEMA = resource_scout_module.RESOURCE_SCOUT_SCHEMA
NODE_NAME = resource_scout_module.NODE_NAME


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _base_state(**overrides: Any) -> Dict[str, Any]:
    state: Dict[str, Any] = {
        "llm_config_set": {
            "default": {
                "base_url": "http://x",
                "model": "m",
                "api_key": "k",
                "temperature": 0.0,
                "max_tokens": 1024,
            },
            "overrides": {},
        },
        "paper_meta": {
            "arxiv_id": "2405.14831",
            "title": "HippoRAG",
            "authors": ["Alice", "Bob"],
            "github_url": None,
        },
        "paper_analysis": {"framework": "PyTorch", "datasets": ["MuSiQue"]},
        "node_errors": [],
        "degraded_nodes": [],
        "retry_budget_remaining": 50,
    }
    state.update(overrides)
    return state


def _repo_info_json(url: str, *, local_path: str, quality: float = 0.0,
                    commits: Optional[int] = 12, official: bool = False) -> str:
    """构造 git_clone_and_analyze 成功 ToolMessage 的合法 JSON（工厂层同款序列化）。"""
    repo = {
        "url": url,
        "source": "git_clone",
        "is_official": official,
        "stars": None,
        "forks": None,
        "last_commit_date": "2025-12-01T00:00:00+00:00",
        "commit_count_recent": commits,
        "has_readme": True,
        "has_requirements": True,
        "dir_structure": ["README.md", "src", "train.py"],
        "quality_score": quality,
        "local_path": local_path,
    }
    return json.dumps(repo, ensure_ascii=False, sort_keys=True, default=str)


def _clone_messages(url: str, content: str, *, call_id: str = "c1") -> List[Any]:
    """构造一对 AIMessage(tool_calls) + ToolMessage(git_clone_and_analyze)。"""
    ai = AIMessage(
        content="",
        tool_calls=[{"name": "git_clone_and_analyze", "args": {"url": url}, "id": call_id}],
    )
    tm = ToolMessage(content=content, name="git_clone_and_analyze", tool_call_id=call_id)
    return [ai, tm]


class _FakeSubgraph:
    """脚本化 ReAct 子图：invoke 直接返回预设 result + messages + round。"""

    def __init__(self, result: Optional[Dict[str, Any]], messages: List[Any], rounds: int):
        self._result = result
        self._messages = messages
        self._rounds = rounds
        self.captured_initial: Optional[Dict[str, Any]] = None

    def invoke(self, initial):
        self.captured_initial = initial
        return {
            "result": self._result,
            "messages": self._messages,
            "round": self._rounds,
            "status": "done",
        }


def _patch_wrapper(monkeypatch, result, messages, rounds=3):
    """让 resource_scout wrapper 走脚本化子图 + 假 LLM。"""
    fake = _FakeSubgraph(result, messages, rounds)
    monkeypatch.setattr(react_base, "create_react_subgraph", lambda **kw: fake)
    monkeypatch.setattr(react_base, "create_llm", lambda cfg: object())
    return fake


# ---------------------------------------------------------------------------
# CP-B2-1：resource_scout 是 _make_react_wrapper 生成的 callable
# ---------------------------------------------------------------------------

def test_cp_b2_1_wrapper_callable_name():
    assert callable(resource_scout)
    assert resource_scout.__name__ == "react_wrapper_resource_scout"


# ---------------------------------------------------------------------------
# CP-B2-2：SCHEMA properties 与 ResourceInfo 字段对齐（除 agent 自报告 search_log）
# ---------------------------------------------------------------------------

def test_cp_b2_2_schema_aligns_resource_info():
    schema_props = set(RESOURCE_SCOUT_SCHEMA["properties"].keys())
    schema_props.discard("search_log")  # agent 自报告字段，不属于 ResourceInfo
    resource_fields = set(ResourceInfo.__annotations__.keys())
    assert schema_props == resource_fields, (schema_props, resource_fields)
    # required 子集校验
    assert set(RESOURCE_SCOUT_SCHEMA["required"]) == {"repos", "selected_repo", "resource_strategy"}


# ---------------------------------------------------------------------------
# CP-B2-7：3 参签名（含 react_messages）
# ---------------------------------------------------------------------------

def test_cp_b2_7_three_arg_signature():
    params = list(inspect.signature(_map_resource_scout_result).parameters.keys())
    assert params == ["result", "state", "react_messages"], params


# ---------------------------------------------------------------------------
# CP-B2-3：有 github_url 路径——git_clone_and_analyze 成功 -> use_repo
# ---------------------------------------------------------------------------

def test_cp_b2_3_github_url_path_use_repo(monkeypatch):
    url = "https://github.com/OSU-NLP-Group/HippoRAG"
    result = {
        "repos": [{
            "url": url, "source": "github_url", "is_official": True,
            "quality_score": 0.82, "local_path": "/ws/repos/HippoRAG",
            "has_readme": True, "has_requirements": True,
            "commit_count_recent": 30, "last_commit_date": "2025-12-01T00:00:00+00:00",
            "dir_structure": ["README.md", "src", "train.py"],
        }],
        "selected_repo": {
            "url": url, "source": "github_url", "is_official": True,
            "quality_score": 0.82, "local_path": "/ws/repos/HippoRAG",
            "has_readme": True, "has_requirements": True,
        },
        "external_resources": [],
        "resource_strategy": "use_repo",
        "search_log": ["found github_url in paper_meta, cloned, owner matches authors"],
    }
    msgs = _clone_messages(url, _repo_info_json(url, local_path="/ws/repos/HippoRAG", quality=0.82, official=True))
    state = _base_state(paper_meta={
        "arxiv_id": "2405.14831", "title": "HippoRAG", "authors": ["Bob"],
        "github_url": url,
    })
    _patch_wrapper(monkeypatch, result, msgs)
    update = resource_scout(state)

    ri = update["resource_info"]
    assert ri["resource_strategy"] == "use_repo"
    assert len(ri["repos"]) == 1
    assert ri["selected_repo"] is not None
    assert ri["selected_repo"]["url"] == url
    assert ri["repos"][0]["source"] == "github_url"
    assert NODE_NAME not in update["degraded_nodes"]


# ---------------------------------------------------------------------------
# CP-B2-4：无 github_url + PwC 成功路径——source 标识 pwc
# ---------------------------------------------------------------------------

def test_cp_b2_4_pwc_path_source_pwc(monkeypatch):
    url = "https://github.com/some/pwc-repo"
    result = {
        "repos": [{
            "url": url, "source": "pwc", "is_official": False,
            "quality_score": 0.6, "local_path": "/ws/repos/pwc-repo",
            "has_readme": True, "has_requirements": True,
            "commit_count_recent": 15,
        }],
        "selected_repo": {"url": url, "source": "pwc", "quality_score": 0.6,
                          "local_path": "/ws/repos/pwc-repo", "is_official": False},
        "external_resources": [],
        "resource_strategy": "use_repo",
    }
    msgs = _clone_messages(url, _repo_info_json(url, local_path="/ws/repos/pwc-repo", quality=0.6))
    state = _base_state()  # github_url=None
    _patch_wrapper(monkeypatch, result, msgs)
    update = resource_scout(state)

    ri = update["resource_info"]
    assert ri["repos"][0]["source"] == "pwc"
    assert ri["selected_repo"]["url"] == url
    assert ri["resource_strategy"] == "use_repo"


# ---------------------------------------------------------------------------
# CP-B2-5：全失败降级路径——from_scratch + degraded + 不抛错
# ---------------------------------------------------------------------------

def test_cp_b2_5_all_fail_degrade_from_scratch(monkeypatch, caplog):
    result = {
        "repos": [],
        "selected_repo": None,
        "external_resources": [],
        "resource_strategy": "from_scratch",
        "search_log": ["github_url empty", "pwc timeout", "web_search no github links"],
    }
    # 工具历史含一条失败的 clone（应被回填忽略）
    msgs = _clone_messages(
        "https://github.com/dead/link",
        json.dumps({"success": False, "error": "Repository not found"}, sort_keys=True),
    )
    state = _base_state()
    _patch_wrapper(monkeypatch, result, msgs)
    with caplog.at_level(logging.WARNING):
        update = resource_scout(state)

    ri = update["resource_info"]
    assert ri["resource_strategy"] == "from_scratch"
    assert ri["repos"] == []
    assert ri["selected_repo"] is None
    assert NODE_NAME in update["degraded_nodes"]
    degraded_errs = [e for e in update["node_errors"] if e["error_type"] == "degraded"]
    assert degraded_errs, "应写 degraded NodeError"


# ---------------------------------------------------------------------------
# CP-B2-6：LLM 漏写 repos 但工具有 1 个成功 clone -> 回填 + WARNING 非静默
# ---------------------------------------------------------------------------

def test_cp_b2_6_backfill_repos_from_tools(monkeypatch, caplog):
    url = "https://github.com/owner/repo"
    # LLM 漏写 repos（空数组），但工具历史有 1 个成功克隆
    result = {"repos": [], "selected_repo": None, "external_resources": [],
              "resource_strategy": "from_scratch"}
    msgs = _clone_messages(url, _repo_info_json(url, local_path="/ws/repos/repo", quality=0.0))
    state = _base_state()
    update = _map_resource_scout_result(result, state, msgs)

    ri = update["resource_info"]
    assert len(ri["repos"]) == 1
    assert ri["repos"][0]["url"] == url
    # 回填默认质量分 0.5
    assert ri["repos"][0]["quality_score"] == pytest.approx(0.5)
    # 回填后策略修正 + selected_repo 补齐
    assert ri["resource_strategy"] == "use_repo"
    assert ri["selected_repo"] is not None


def test_cp_b2_6_backfill_warns_when_only_failures(caplog):
    """工具历史只有失败 clone 时不回填，且打 WARNING（非静默）。"""
    payload: ResourceInfo = ResourceInfo(
        repos=[], selected_repo=None, external_resources=[], resource_strategy="from_scratch"
    )
    msgs = _clone_messages(
        "https://github.com/dead/x",
        json.dumps({"success": False, "error": "not found"}, sort_keys=True),
    )
    with caplog.at_level(logging.WARNING):
        did = _backfill_repos_from_tools(payload, msgs)
    assert did is False
    assert payload["repos"] == []
    assert any("backfill skipped" in r.message for r in caplog.records)


def test_cp_b2_6_backfill_no_clone_no_warn(caplog):
    """无 git_clone ToolMessage 时不打 WARNING（避免噪声）。"""
    payload: ResourceInfo = ResourceInfo(
        repos=[], selected_repo=None, external_resources=[], resource_strategy="from_scratch"
    )
    with caplog.at_level(logging.WARNING):
        did = _backfill_repos_from_tools(payload, [AIMessage(content="hi")])
    assert did is False
    assert not any("backfill skipped" in r.message for r in caplog.records)


def test_cp_b2_6_backfill_skipped_when_llm_gave_repos():
    """LLM 已给 repos 时不回填（即便工具历史有记录）。"""
    payload: ResourceInfo = ResourceInfo(
        repos=[_build := RepoInfo(  # type: ignore[misc]
            url="x", source="github_url", is_official=False, stars=None, forks=None,
            last_commit_date=None, commit_count_recent=None, has_readme=True,
            has_requirements=True, dir_structure=None, quality_score=0.7, local_path="/p",
        )],
        selected_repo=None, external_resources=[], resource_strategy="use_repo",
    )
    msgs = _clone_messages("https://github.com/o/r",
                           _repo_info_json("https://github.com/o/r", local_path="/q"))
    did = _backfill_repos_from_tools(payload, msgs)
    assert did is False
    assert len(payload["repos"]) == 1


# ---------------------------------------------------------------------------
# CP-B2-8：quality_score 全部 <0.3 -> selected_repo==repos[0](best) + [QUALITY_WARN]
# ---------------------------------------------------------------------------

def test_cp_b2_8_low_quality_warn(monkeypatch, caplog):
    result = {
        "repos": [
            {"url": "https://github.com/a/x", "source": "web_search", "quality_score": 0.1,
             "local_path": "/ws/x", "is_official": False},
            {"url": "https://github.com/a/y", "source": "web_search", "quality_score": 0.25,
             "local_path": "/ws/y", "is_official": False},
        ],
        "selected_repo": None,
        "external_resources": [],
        "resource_strategy": "hybrid",
    }
    state = _base_state()
    with caplog.at_level(logging.WARNING):
        update = _map_resource_scout_result(result, state, None)

    ri = update["resource_info"]
    # 推荐 best 候选（quality 最高 0.25 那个）
    assert ri["selected_repo"] is not None
    assert ri["selected_repo"]["quality_score"] == pytest.approx(0.25)
    assert "[QUALITY_WARN]" in update.get("analysis_notes", "")
    assert any("QUALITY_WARN" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# CP-B2-9：max_rounds=10 耗尽 force_finish 输出最佳已知候选（可空，不抛错）
# ---------------------------------------------------------------------------

def test_cp_b2_9_force_finish_empty_no_throw(monkeypatch):
    # 模拟 force_finish：result 为 None（子图未产出结构化 result）+ rounds=10
    msgs: List[Any] = []
    state = _base_state()
    fake = _patch_wrapper(monkeypatch, None, msgs, rounds=10)
    update = resource_scout(state)  # 不应抛错

    ri = update["resource_info"]
    assert ri["resource_strategy"] == "from_scratch"
    assert ri["repos"] == []
    assert ri["selected_repo"] is None
    assert NODE_NAME in update["degraded_nodes"]
    # max_rounds 透传到子图
    assert fake.captured_initial["max_rounds"] == 10


def test_cp_b2_9_force_finish_with_partial_candidate(monkeypatch):
    """force_finish 但工具历史有成功候选时仍能回填出最佳候选。"""
    url = "https://github.com/o/partial"
    msgs = _clone_messages(url, _repo_info_json(url, local_path="/ws/partial", quality=0.0))
    # result 漏写 repos
    result = {"repos": [], "selected_repo": None, "external_resources": [],
              "resource_strategy": "from_scratch"}
    state = _base_state()
    _patch_wrapper(monkeypatch, result, msgs, rounds=10)
    update = resource_scout(state)
    ri = update["resource_info"]
    assert len(ri["repos"]) == 1
    assert ri["selected_repo"]["url"] == url


# ---------------------------------------------------------------------------
# CP-B2-10：system prompt 主体无论文级动态变量 + 字节级一致
# ---------------------------------------------------------------------------

def test_cp_b2_10_prompt_body_byte_identical_across_papers():
    ctx_a = {"arxiv_id": "2405.14831", "title": "HippoRAG", "github_url": "https://github.com/a/b"}
    ctx_b = {"arxiv_id": "1706.03762", "title": "Attention Is All You Need", "github_url": None}
    body_a = _build_resource_scout_system_prompt(ctx_a)
    body_b = _build_resource_scout_system_prompt(ctx_b)
    assert body_a == body_b, "system prompt 主体在不同论文间必须字节级一致"


def test_cp_b2_10_prompt_body_no_dynamic_values():
    body = resource_scout_module._RESOURCE_SCOUT_SYSTEM_PROMPT_BODY
    # 主体不得出现任何具体论文 id / 标题等动态值
    for forbidden in ("2405.14831", "1706.03762", "HippoRAG", "Attention Is All You Need"):
        assert forbidden not in body, forbidden
    # build 返回值即主体本身（动态上下文走 HumanMessage 通道，不进 system prompt）
    assert _build_resource_scout_system_prompt({"arxiv_id": "9999.99999"}) == body


# ---------------------------------------------------------------------------
# 补充：空结果 / error 字段降级（不抛错）
# ---------------------------------------------------------------------------

def test_aux_none_result_degrades():
    state = _base_state()
    update = _map_resource_scout_result(None, state, None)
    assert update["resource_info"]["resource_strategy"] == "from_scratch"
    assert NODE_NAME in update["degraded_nodes"]


def test_aux_error_field_degrades():
    state = _base_state()
    update = _map_resource_scout_result({"error": "all tools failed"}, state, None)
    assert update["resource_info"]["resource_strategy"] == "from_scratch"
    assert NODE_NAME in update["degraded_nodes"]


def test_aux_repoinfo_strict_fields():
    """_build_repo_info 输出严格 12 字段 RepoInfo。"""
    raw = {"url": "u", "quality_score": "0.7", "stars": "5", "is_official": "true"}
    repo = resource_scout_module._build_repo_info(raw)
    assert set(repo.keys()) == set(RepoInfo.__annotations__.keys())
    assert repo["quality_score"] == pytest.approx(0.7)
    assert repo["stars"] == 5
    assert repo["is_official"] is True


# ===========================================================================
# 测试工程师独立验收补强（2026-06-02）：覆盖 dev 自测未触及的边界
# ===========================================================================

# --- 补强 1：工具集组成（6 工具含 search_pwc）+ wrapper 参数透传 -------------

def test_acc_tool_set_composition_six_tools(monkeypatch):
    """dev-plan B2 / 架构 §4.6 要求 get_tools 含 6 工具（含 search_pwc）。

    架构 §2.3.2 prompt 示例只列 5 个（漏 search_pwc），dev-plan + §4.6/§2.3.5
    权威要求含 search_pwc——本用例锚定实现按 dev-plan 落地 6 工具。
    """
    captured: Dict[str, Any] = {}

    class _F:
        def invoke(self, init):
            return {"result": None, "messages": [], "round": 10, "status": "done"}

    def _fake(**kw):
        captured.update(kw)
        return _F()

    monkeypatch.setattr(react_base, "create_react_subgraph", _fake)
    monkeypatch.setattr(react_base, "create_llm", lambda cfg: object())
    resource_scout(_base_state())

    names = sorted(t.name for t in captured["tools"])
    assert names == [
        "check_url_reachable_tool", "get_paper_brief", "git_clone_and_analyze",
        "search_papers", "search_pwc", "web_search",
    ], names
    assert captured["max_rounds"] == 10
    assert captured["result_schema"]["title"] == "ResourceInfo"


# --- 补强 2：prompt 主体覆盖搜索优先级链四级 + 工具名 ------------------------

def test_acc_prompt_body_describes_priority_chain():
    """system prompt 主体必须描述 deepxiv github_url->PwC->web_search->from_scratch
    四级降级链 + 关键工具名（防 prompt 被裁剪掉降级链导致 LLM 不知降级路径）。"""
    body = resource_scout_module._RESOURCE_SCOUT_SYSTEM_PROMPT_BODY
    for needle in (
        "github_url", "Papers With Code", "search_pwc", "web_search",
        "from_scratch", "check_url_reachable_tool", "git_clone_and_analyze",
        "quality_score",
    ):
        assert needle in body, needle


# --- 补强 3：_format_resource_scout_context 英文事实层过滤（PRD §4.7.5） ------

def test_acc_context_filters_empty_and_keeps_english_facts():
    """build_context 只挑英文事实层字段，过滤 None/空值（含 github_url=None）。"""
    fmt = resource_scout_module._format_resource_scout_context
    meta = {
        "arxiv_id": "2405.14831", "title": "HippoRAG", "authors": ["A", "B"],
        "github_url": None, "keywords": [], "abstract": "should-not-leak",
        "title_zh": "中文标题不应进入",
    }
    analysis = {"framework": "PyTorch", "datasets": ["MuSiQue"], "categories": None}
    ctx = fmt(meta, analysis)
    # 保留的英文事实层字段
    assert ctx["arxiv_id"] == "2405.14831"
    assert ctx["title"] == "HippoRAG"
    assert ctx["authors"] == ["A", "B"]
    assert ctx["framework"] == "PyTorch"
    assert ctx["datasets"] == ["MuSiQue"]
    # None / 空值被过滤
    assert "github_url" not in ctx
    assert "keywords" not in ctx
    assert "categories" not in ctx
    # 非检索字段 / 中文备份字段不泄漏到检索上下文
    assert "abstract" not in ctx
    assert "title_zh" not in ctx


def test_acc_context_handles_none_inputs():
    """paper_meta / paper_analysis 为 None 时不抛错，返回空 dict。"""
    fmt = resource_scout_module._format_resource_scout_context
    assert fmt(None, None) == {}
    assert fmt({}, {}) == {}


# --- 补强 4：3 参签名真实经 wrapper 透传 final_messages 到 backfill ----------

def test_acc_wrapper_passes_messages_to_backfill(monkeypatch):
    """端到端经 wrapper：LLM 漏写 repos，子图 messages 有成功 clone，
    wrapper 必须把 final_messages 透传给 _map 第三参，触发回填。
    这是 BUG-S1-03 范式的真实链路验证（非直接调 _map）。"""
    url = "https://github.com/owner/repo"
    result = {"repos": [], "selected_repo": None, "external_resources": [],
              "resource_strategy": "from_scratch"}
    msgs = _clone_messages(url, _repo_info_json(url, local_path="/ws/repo", quality=0.0))
    _patch_wrapper(monkeypatch, result, msgs)
    update = resource_scout(_base_state())
    ri = update["resource_info"]
    assert len(ri["repos"]) == 1, "wrapper 未把 messages 透传给 backfill"
    assert ri["repos"][0]["url"] == url
    assert ri["resource_strategy"] == "use_repo"


# --- 补强 5：selected_repo LLM 漏写时从 repos 选 best（quality 最高） ---------

def test_acc_selected_repo_picks_best_when_missing():
    """repos 非空但 LLM 漏写 selected_repo -> 自动补 quality 最高者。"""
    result = {
        "repos": [
            {"url": "a", "source": "pwc", "quality_score": 0.4, "local_path": "/a", "is_official": False},
            {"url": "b", "source": "pwc", "quality_score": 0.9, "local_path": "/b", "is_official": True},
        ],
        "selected_repo": None, "external_resources": [], "resource_strategy": "use_repo",
    }
    update = _map_resource_scout_result(result, _base_state(), None)
    assert update["resource_info"]["selected_repo"]["url"] == "b"
    assert NODE_NAME not in update["degraded_nodes"]


# --- 补强 6：无效 strategy 字符串 + repos 非空 -> fallback use_repo ----------

def test_acc_invalid_strategy_falls_back():
    result = {
        "repos": [{"url": "a", "source": "pwc", "quality_score": 0.5, "local_path": "/a", "is_official": False}],
        "selected_repo": None, "external_resources": [], "resource_strategy": "WAT",
    }
    update = _map_resource_scout_result(result, _base_state(), None)
    assert update["resource_info"]["resource_strategy"] == "use_repo"


# --- 补强 7：degraded_nodes / NodeError 去重与累加（不污染上游） -------------

def test_acc_degraded_nodes_dedup_preserves_upstream():
    """已有上游 degraded_nodes 时不重复追加 resource_scout，且保留上游条目。"""
    from core.errors import make_node_error
    state = _base_state(degraded_nodes=["paper_analysis", "resource_scout"],
                        node_errors=[make_node_error("paper_analysis", "degraded", "x", None)])
    update = _map_resource_scout_result(None, state, None)
    assert update["degraded_nodes"].count("resource_scout") == 1
    assert "paper_analysis" in update["degraded_nodes"]
    # 上游 node_error 保留 + 新增本节点 degraded（NodeError 用 node_name 键）
    nodes = [e["node_name"] for e in update["node_errors"]]
    assert "paper_analysis" in nodes
    assert "resource_scout" in nodes


# --- 补强 8：backfill 截断 JSON 后缀可恢复（与 react_base 截断契约一致） -----

def test_acc_backfill_recovers_truncated_json():
    url = "https://github.com/o/trunc"
    full = _repo_info_json(url, local_path="/ws/trunc", quality=0.0)
    truncated = full + "... [truncated at 50 chars]"
    payload = ResourceInfo(repos=[], selected_repo=None, external_resources=[],
                           resource_strategy="from_scratch")
    msgs = _clone_messages(url, truncated)
    did = _backfill_repos_from_tools(payload, msgs)
    assert did is True
    assert payload["repos"][0]["url"] == url
    assert payload["repos"][0]["quality_score"] == pytest.approx(0.5)


# --- 补强 9：backfill 跳过 'tool ... raised' 与 'Error in' 失败前缀 ---------

@pytest.mark.parametrize("bad_content", [
    "tool git_clone_and_analyze raised ValueError: boom",
    "Error in git_clone_and_analyze: connection refused",
])
def test_acc_backfill_skips_failure_prefixes(bad_content, caplog):
    payload = ResourceInfo(repos=[], selected_repo=None, external_resources=[],
                           resource_strategy="from_scratch")
    msgs = _clone_messages("https://github.com/o/x", bad_content)
    with caplog.at_level(logging.WARNING):
        did = _backfill_repos_from_tools(payload, msgs)
    assert did is False
    assert payload["repos"] == []
    # ToolMessage 存在但无成功记录 -> WARNING 非静默
    assert any("backfill skipped" in r.message for r in caplog.records)


# --- 补强 10：backfill 多条 clone 中 1 成功 1 失败 -> 只回填成功者 -----------

def test_acc_backfill_mixed_success_failure():
    ok_url = "https://github.com/o/ok"
    payload = ResourceInfo(repos=[], selected_repo=None, external_resources=[],
                           resource_strategy="from_scratch")
    ai1 = AIMessage(content="", tool_calls=[{"name": "git_clone_and_analyze", "args": {"url": "bad"}, "id": "c1"}])
    tm1 = ToolMessage(content=json.dumps({"success": False, "error": "404"}),
                      name="git_clone_and_analyze", tool_call_id="c1")
    ai2 = AIMessage(content="", tool_calls=[{"name": "git_clone_and_analyze", "args": {"url": ok_url}, "id": "c2"}])
    tm2 = ToolMessage(content=_repo_info_json(ok_url, local_path="/ws/ok", quality=0.0),
                      name="git_clone_and_analyze", tool_call_id="c2")
    did = _backfill_repos_from_tools(payload, [ai1, tm1, ai2, tm2])
    assert did is True
    assert len(payload["repos"]) == 1
    assert payload["repos"][0]["url"] == ok_url


# --- 补强 11：external_resources 畸形条目过滤（非 dict 项丢弃，不抛错） ------

def test_acc_external_resources_malformed_filtered():
    result = {
        "repos": [], "selected_repo": None,
        "external_resources": ["not-a-dict", {"name": "dataset", "url": "http://d"}, 42],
        "resource_strategy": "from_scratch",
    }
    update = _map_resource_scout_result(result, _base_state(), None)
    ext = update["resource_info"]["external_resources"]
    assert ext == [{"name": "dataset", "url": "http://d"}]


# --- 补强 12：search_log 透明落 analysis_notes（人类可审核） -----------------

def test_acc_search_log_appended_to_notes():
    result = {
        "repos": [], "selected_repo": None, "external_resources": [],
        "resource_strategy": "from_scratch",
        "search_log": ["github_url empty", "pwc 0 hits", "web_search no github"],
    }
    update = _map_resource_scout_result(result, _base_state(), None)
    notes = update.get("analysis_notes", "")
    assert "[SEARCH_LOG]" in notes
    assert "pwc 0 hits" in notes


# --- 补强 13：quality 边界——恰好 0.3 不触发 QUALITY_WARN（< 严格小于） -------

def test_acc_quality_boundary_exactly_threshold_no_warn():
    result = {
        "repos": [{"url": "a", "source": "pwc", "quality_score": 0.3, "local_path": "/a", "is_official": False}],
        "selected_repo": None, "external_resources": [], "resource_strategy": "hybrid",
    }
    update = _map_resource_scout_result(result, _base_state(), None)
    assert "[QUALITY_WARN]" not in update.get("analysis_notes", "")
    # 恰好 0.3 不降级、策略保留
    assert NODE_NAME not in update["degraded_nodes"]


# --- 补强 14：quality_score 钳制到 [0,1]（越界输入由 _build_repo_info 钳制） --

def test_acc_quality_score_clamped():
    over = resource_scout_module._build_repo_info({"url": "u", "quality_score": 9.9})
    under = resource_scout_module._build_repo_info({"url": "u", "quality_score": -3.0})
    assert over["quality_score"] == pytest.approx(1.0)
    assert under["quality_score"] == pytest.approx(0.0)
