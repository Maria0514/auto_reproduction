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

def test_acc_tool_set_composition_five_tools(monkeypatch):
    """Sprint 6 MF-5 摘除 PwC 后，resource_scout 工具集由 6 个降为 5 个（无 search_pwc）。

    降级链变更：deepxiv github_url -> web search（PwC 通道移除，PwC 网站 2025 年中下线）。
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
        "search_papers", "web_search",
    ], names
    assert captured["max_rounds"] == 10
    assert captured["result_schema"]["title"] == "ResourceInfo"


# --- 补强 2：prompt 主体覆盖搜索优先级链四级 + 工具名 ------------------------

def test_acc_prompt_body_describes_priority_chain():
    """system prompt 主体必须描述降级链 + 关键工具名。

    Sprint 6 MF-5：PwC 摘除后降级链变更为 deepxiv github_url -> web_search -> from_scratch。
    search_pwc / Papers With Code 相关描述已从 prompt 中移除。
    """
    body = resource_scout_module._RESOURCE_SCOUT_SYSTEM_PROMPT_BODY
    for needle in (
        "github_url", "web_search",
        "from_scratch", "check_url_reachable_tool", "git_clone_and_analyze",
        "quality_score",
    ):
        assert needle in body, needle
    # PwC 相关字段已摘除，不应再出现在 prompt 主体
    assert "search_pwc" not in body, "search_pwc 已从工具集摘除，不应出现在 prompt"
    assert "Papers With Code" not in body, "Papers With Code 已从优先级链移除"


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


# ===========================================================================
# 测试工程师深化补全（2026-06-02，第二轮）：在 34 用例之上补优先级链组合 /
# 评分缺失语义 / backfill 边角 / SCHEMA 健壮性 / Prompt Cache 极端 context。
# 命名前缀 test_deep_* 以区分上一轮 test_acc_*。
# ===========================================================================

# --- 深化 1：搜索优先级链——死链(check_url_reachable=False)路径下不产生 repos ----

def test_deep_dead_url_reachable_false_then_from_scratch(monkeypatch):
    """步骤 1 github_url 经 check_url_reachable 判死链(False)，agent 跳过昂贵 clone，
    后续 PwC/web 也未命中 -> from_scratch。

    节点层无法强制 LLM 行为，但可验证：工具历史里只有 check_url_reachable=False
    且无任何成功 git_clone_and_analyze 记录时，_map 走 from_scratch 降级、
    不误把 reachable 探测结果当成 repo 回填。
    """
    ai = AIMessage(
        content="",
        tool_calls=[{"name": "check_url_reachable_tool",
                     "args": {"url": "https://github.com/dead/link"}, "id": "u1"}],
    )
    tm = ToolMessage(
        content=json.dumps({"url": "https://github.com/dead/link", "reachable": False},
                           sort_keys=True),
        name="check_url_reachable_tool", tool_call_id="u1",
    )
    result = {"repos": [], "selected_repo": None, "external_resources": [],
              "resource_strategy": "from_scratch",
              "search_log": ["github_url unreachable, skipped clone"]}
    state = _base_state()
    _patch_wrapper(monkeypatch, result, [ai, tm])
    update = resource_scout(state)
    ri = update["resource_info"]
    assert ri["resource_strategy"] == "from_scratch"
    assert ri["repos"] == []
    assert ri["selected_repo"] is None
    # check_url_reachable ToolMessage 不应被 backfill 误当成 git clone 成功记录。
    assert NODE_NAME in update["degraded_nodes"]


def test_deep_backfill_ignores_non_clone_toolmessages():
    """backfill 只认 name=git_clone_and_analyze 的 ToolMessage，
    check_url_reachable / search_pwc 的 ToolMessage 一律忽略（即便含 url/local_path 字样）。"""
    payload = ResourceInfo(repos=[], selected_repo=None, external_resources=[],
                           resource_strategy="from_scratch")
    # 伪造一条 search_pwc ToolMessage，其 content 含 url 但 name 不匹配。
    pwc_tm = ToolMessage(
        content=json.dumps({"results": [{"repos": [{"url": "https://github.com/x/y"}]}],
                            "local_path": "/fake"}, sort_keys=True),
        name="search_pwc", tool_call_id="p1",
    )
    reach_tm = ToolMessage(
        content=json.dumps({"url": "https://github.com/x/y", "reachable": True,
                            "local_path": "/fake"}, sort_keys=True),
        name="check_url_reachable_tool", tool_call_id="r1",
    )
    did = _backfill_repos_from_tools(payload, [pwc_tm, reach_tm])
    assert did is False
    assert payload["repos"] == []


# --- 深化 2：backfill 多条成功 clone 全部回填 + 去重 -------------------------

def test_deep_backfill_multiple_success_all_recovered():
    """工具历史含 3 条成功 clone（2 个唯一 + 1 个重复 url）-> 回填 2 个去重后的 repo。"""
    payload = ResourceInfo(repos=[], selected_repo=None, external_resources=[],
                           resource_strategy="from_scratch")
    url_a = "https://github.com/o/a"
    url_b = "https://github.com/o/b"
    msgs: List[Any] = []
    for i, u in enumerate((url_a, url_b, url_a)):  # url_a 出现两次
        msgs += _clone_messages(u, _repo_info_json(u, local_path=f"/ws/{i}", quality=0.0),
                                call_id=f"c{i}")
    did = _backfill_repos_from_tools(payload, msgs)
    assert did is True
    urls = sorted(r["url"] for r in payload["repos"])
    assert urls == [url_a, url_b], "重复 url 应去重，2 个唯一 repo"


def test_deep_backfill_parses_list_content_toolmessage():
    """ToolMessage.content 为 list 形态（部分 LLM provider 产出 [{'type':'text','text':..}]）
    时，_parse_tool_content 仍能拼接还原并回填——验证 list-content 分支。"""
    url = "https://github.com/o/listcontent"
    payload = ResourceInfo(repos=[], selected_repo=None, external_resources=[],
                           resource_strategy="from_scratch")
    json_str = _repo_info_json(url, local_path="/ws/lc", quality=0.0)
    ai = AIMessage(content="",
                   tool_calls=[{"name": "git_clone_and_analyze", "args": {"url": url}, "id": "lc1"}])
    tm = ToolMessage(
        content=[{"type": "text", "text": json_str}],  # list 形态 content
        name="git_clone_and_analyze", tool_call_id="lc1",
    )
    did = _backfill_repos_from_tools(payload, [ai, tm])
    assert did is True
    assert payload["repos"][0]["url"] == url


def test_deep_backfill_skips_when_missing_local_path():
    """成功结构但缺 local_path（非克隆产物，可能是其它 dict）的记录被跳过。"""
    payload = ResourceInfo(repos=[], selected_repo=None, external_resources=[],
                           resource_strategy="from_scratch")
    no_path = json.dumps({"url": "https://github.com/o/x", "success": True,
                          "has_readme": True}, sort_keys=True)  # 无 local_path
    msgs = _clone_messages("https://github.com/o/x", no_path)
    did = _backfill_repos_from_tools(payload, msgs)
    assert did is False
    assert payload["repos"] == []


# --- 深化 3：评分缺失语义——None 字段不被当 0/最旧（is None 保真透传） ----------

def test_deep_repo_info_preserves_none_metrics_not_zero():
    """commit_count_recent=None / last_commit_date=None 必须保真为 None，
    不能被 _build_repo_info 当成 0 / 空串（架构 §2.3.1：用 is None 判缺失，勿当 0 活跃）。"""
    repo = resource_scout_module._build_repo_info({
        "url": "u", "source": "git_clone",
        "commit_count_recent": None, "last_commit_date": None,
        "stars": None, "forks": None, "dir_structure": None,
    })
    assert repo["commit_count_recent"] is None
    assert repo["last_commit_date"] is None
    assert repo["stars"] is None
    assert repo["forks"] is None
    # dir_structure=None 保真（不强转空列表，便于下游区分"读不到"与"空目录"）。
    assert repo["dir_structure"] is None
    # 缺失 quality_score 默认 0.0（非 None，因 RepoInfo.quality_score 为 float）。
    assert repo["quality_score"] == pytest.approx(0.0)


def test_deep_repo_info_zero_metrics_kept_distinct_from_none():
    """commit_count_recent=0（真实活跃为 0）与 None（读不到）必须区分保真。"""
    zero = resource_scout_module._build_repo_info({"url": "u", "commit_count_recent": 0})
    assert zero["commit_count_recent"] == 0
    assert zero["commit_count_recent"] is not None


# --- 深化 4：SCHEMA / 字段健壮性 ----------------------------------------------

def test_deep_external_resources_all_malformed_to_empty():
    """external_resources 全为畸形(非 dict)项 -> 过滤后为空数组，不抛错。"""
    result = {"repos": [], "selected_repo": None,
              "external_resources": ["x", 1, None, ["nested"]],
              "resource_strategy": "from_scratch"}
    update = _map_resource_scout_result(result, _base_state(), None)
    assert update["resource_info"]["external_resources"] == []


def test_deep_invalid_strategy_empty_repos_to_from_scratch():
    """无效 strategy + repos 为空 -> _build_resource_info 推断 from_scratch（非 use_repo）。"""
    result = {"repos": [], "selected_repo": None, "external_resources": [],
              "resource_strategy": "GARBAGE"}
    update = _map_resource_scout_result(result, _base_state(), None)
    ri = update["resource_info"]
    assert ri["resource_strategy"] == "from_scratch"
    assert NODE_NAME in update["degraded_nodes"]


def test_deep_schema_required_subset_of_properties():
    """RESOURCE_SCOUT_SCHEMA.required 必须是 properties 的子集（否则 with_structured_output 报错）。"""
    props = set(RESOURCE_SCOUT_SCHEMA["properties"].keys())
    required = set(RESOURCE_SCOUT_SCHEMA["required"])
    assert required.issubset(props), required - props
    # strategy enum 与节点内 _VALID_STRATEGIES 严格一致（防文档/实现漂移）。
    enum = RESOURCE_SCOUT_SCHEMA["properties"]["resource_strategy"]["enum"]
    assert tuple(enum) == resource_scout_module._VALID_STRATEGIES


def test_deep_strategy_enum_matches_valid_strategies():
    """三合法策略恒等校验（use_repo/hybrid/from_scratch），任一被改即 fail。"""
    assert resource_scout_module._VALID_STRATEGIES == ("use_repo", "hybrid", "from_scratch")


# --- 深化 5：degraded_nodes 二次去重（repos 空降级路径，非 None-result 路径） ---

def test_deep_degraded_dedup_on_empty_repos_path():
    """走 repos 为空降级路径时，若上游已含 resource_scout（罕见重入），不重复追加。"""
    state = _base_state(degraded_nodes=["resource_scout"])
    result = {"repos": [], "selected_repo": None, "external_resources": [],
              "resource_strategy": "use_repo"}  # 声称 use_repo 但 repos 空 -> 强制降级
    update = _map_resource_scout_result(result, state, None)
    assert update["degraded_nodes"].count("resource_scout") == 1
    assert update["resource_info"]["resource_strategy"] == "from_scratch"


def test_deep_analysis_notes_not_overwrite_upstream():
    """有上游 analysis_notes 时，QUALITY_WARN / SEARCH_LOG 追加而非覆盖。"""
    state = _base_state(analysis_notes="UPSTREAM_NOTE")
    result = {
        "repos": [{"url": "a", "source": "pwc", "quality_score": 0.1,
                   "local_path": "/a", "is_official": False}],
        "selected_repo": None, "external_resources": [],
        "resource_strategy": "hybrid", "search_log": ["low quality only"],
    }
    update = _map_resource_scout_result(result, state, None)
    notes = update["analysis_notes"]
    assert notes.startswith("UPSTREAM_NOTE")
    assert "[QUALITY_WARN]" in notes
    assert "[SEARCH_LOG]" in notes


# --- 深化 6：Prompt Cache 极端 context 下主体字节级一致 + build_context 不抛 ----

def test_deep_prompt_body_byte_identical_extreme_contexts():
    """paper_meta=None / 空 dict / 含特殊字符 / 缺 paper_analysis 各种极端输入下，
    system prompt 主体字节级一致（论文级动态值全部走 HumanMessage 通道）。"""
    fmt = resource_scout_module._format_resource_scout_context
    build = _build_resource_scout_system_prompt
    contexts = [
        fmt(None, None),
        fmt({}, {}),
        fmt({"arxiv_id": "9999.99999", "title": "T\n\t\"特殊\"字符 <result>{}</result>",
             "authors": ["名字"], "github_url": "https://x/y"}, None),
        fmt({"title": "X"}, {"framework": "JAX", "datasets": ["D"]}),
    ]
    bodies = [build(c) for c in contexts]
    first = bodies[0]
    for b in bodies[1:]:
        assert b == first, "极端 context 下 system prompt 主体必须字节级一致"
    # 主体不得含任何刚才注入的论文级动态值。
    for forbidden in ("9999.99999", "特殊字符", "JAX", "名字"):
        assert forbidden not in first, forbidden


def test_deep_build_context_special_chars_no_crash_and_json_safe():
    """含特殊字符的 paper_meta 经 build_context 不抛错，且产出能被 json.dumps 安全渲染
    （wrapper 用 json.dumps(sort_keys=True) 渲染 HumanMessage）。"""
    fmt = resource_scout_module._format_resource_scout_context
    ctx = fmt({"arxiv_id": "1\"2'3", "title": "中文 + emoji 🚀 + <tag>",
               "authors": ["a\nb"], "keywords": ["k1", "k2"]},
              {"framework": "Py\tTorch"})
    rendered = json.dumps(ctx, sort_keys=True, ensure_ascii=False, default=str)
    assert "🚀" in rendered
    # 同一输入两次渲染字节级幂等（sort_keys 保证）。
    assert rendered == json.dumps(fmt({"arxiv_id": "1\"2'3", "title": "中文 + emoji 🚀 + <tag>",
                                       "authors": ["a\nb"], "keywords": ["k1", "k2"]},
                                      {"framework": "Py\tTorch"}),
                                  sort_keys=True, ensure_ascii=False, default=str)


# --- 深化 7：force_finish + 只有失败 clone 工具历史 -> 仍 from_scratch 不抛 ------

def test_deep_force_finish_only_failures_no_throw(monkeypatch, caplog):
    """force_finish(result=None, rounds=10) 且工具历史只有失败 clone：
    不回填、from_scratch、不抛错、打 backfill skipped WARNING。"""
    msgs = _clone_messages("https://github.com/dead/x",
                           json.dumps({"success": False, "error": "404"}, sort_keys=True))
    _patch_wrapper(monkeypatch, None, msgs, rounds=10)
    with caplog.at_level(logging.WARNING):
        update = resource_scout(_base_state())
    ri = update["resource_info"]
    assert ri["resource_strategy"] == "from_scratch"
    assert ri["repos"] == []
    assert NODE_NAME in update["degraded_nodes"]


# --- 深化 8：回填后 selected_repo 已被 LLM 给出时不被 best 覆盖 ----------------

def test_deep_backfill_keeps_existing_selected_repo():
    """LLM 漏写 repos 但写了 selected_repo（罕见不一致），回填 repos 后
    若 selected_repo 已存在则保留（仅缺失时才补 best）。"""
    url = "https://github.com/o/repo"
    given_sel = {"url": "https://github.com/o/manual", "source": "pwc",
                 "quality_score": 0.9, "local_path": "/m", "is_official": True}
    result = {"repos": [], "selected_repo": given_sel, "external_resources": [],
              "resource_strategy": "use_repo"}
    msgs = _clone_messages(url, _repo_info_json(url, local_path="/ws/repo", quality=0.0))
    update = _map_resource_scout_result(result, _base_state(), msgs)
    ri = update["resource_info"]
    assert len(ri["repos"]) == 1
    # selected_repo 是 LLM 显式给出的那个，不被回填 best 覆盖。
    assert ri["selected_repo"]["url"] == "https://github.com/o/manual"


# ===========================================================================
# 测试工程师深化补全（2026-06-02，第三轮）：复验收时找到的上一轮 deep 仍未覆盖
# 的边角——selected_repo 与 repos 不一致、quality 并列最高的确定性、quality 缺失/
# 非数值钳制、回填 RepoInfo 12 字段完备性、degraded NodeError 计数不重复。
# 命名前缀 test_deep2_* 以区分前两轮。
# ===========================================================================

# --- 深化 9：多候选并列最高分时 selected_repo 选取的确定性（稳定取首个） -------

def test_deep2_select_best_tie_deterministic_first():
    """两个候选 quality_score 并列最高时，_select_best_repo 稳定返回列表中靠前者
    （max 取首个命中）；多次调用结果一致（确定性，无随机）。"""
    repos = [
        RepoInfo(url="first", source="pwc", is_official=False, stars=None, forks=None,
                 last_commit_date=None, commit_count_recent=None, has_readme=True,
                 has_requirements=True, dir_structure=None, quality_score=0.8, local_path="/1"),
        RepoInfo(url="second", source="pwc", is_official=True, stars=None, forks=None,
                 last_commit_date=None, commit_count_recent=None, has_readme=True,
                 has_requirements=True, dir_structure=None, quality_score=0.8, local_path="/2"),
    ]
    sel = resource_scout_module._select_best_repo
    picked = [sel(repos)["url"] for _ in range(5)]
    assert set(picked) == {"first"}, "并列最高分必须确定性返回靠前者，且多次一致"


def test_deep2_map_selected_repo_tie_uses_first_in_repos():
    """经 _map：repos 两个并列 0.7、LLM 漏写 selected_repo -> 补的 best 是 repos 中靠前者。"""
    result = {
        "repos": [
            {"url": "alpha", "source": "pwc", "quality_score": 0.7, "local_path": "/a", "is_official": False},
            {"url": "beta", "source": "pwc", "quality_score": 0.7, "local_path": "/b", "is_official": True},
        ],
        "selected_repo": None, "external_resources": [], "resource_strategy": "use_repo",
    }
    update = _map_resource_scout_result(result, _base_state(), None)
    assert update["resource_info"]["selected_repo"]["url"] == "alpha"


# --- 深化 10：selected_repo 指向不在 repos 里的仓库时——LLM 显式值被保留 ---------

def test_deep2_selected_repo_not_in_repos_is_preserved():
    """LLM 给出的 selected_repo 即便 url 不在 repos 列表里（不一致），当前实现
    透传保留该显式值（不强制替换为 repos 内的 best）。

    本用例锚定**当前实际语义**：selected_repo 信任 LLM 显式给值，仅在缺失/repos 空时
    才介入。若未来产品要求强制 selected∈repos，应改为 BUG/需求项再调整本断言。
    """
    result = {
        "repos": [
            {"url": "in-list", "source": "pwc", "quality_score": 0.6, "local_path": "/in", "is_official": False},
        ],
        "selected_repo": {"url": "NOT-in-list", "source": "web_search",
                          "quality_score": 0.9, "local_path": "/out", "is_official": True},
        "external_resources": [], "resource_strategy": "use_repo",
    }
    update = _map_resource_scout_result(result, _base_state(), None)
    ri = update["resource_info"]
    # 当前语义：保留 LLM 显式 selected_repo（即便不在 repos 内）。
    assert ri["selected_repo"]["url"] == "NOT-in-list"
    assert [r["url"] for r in ri["repos"]] == ["in-list"]
    assert NODE_NAME not in update["degraded_nodes"]


# --- 深化 11：quality_score 非数值 / 缺失的钳制 + 不破坏 best 排序稳定性 ---------

def test_deep2_quality_non_numeric_clamped_to_default_zero():
    """quality_score 为非数值字符串 / None / dict 时，_coerce_float 退回默认 0.0，
    不抛错（防 LLM 输出脏数据导致 max() 比较崩溃）。"""
    bi = resource_scout_module._build_repo_info
    assert bi({"url": "u", "quality_score": "abc"})["quality_score"] == pytest.approx(0.0)
    assert bi({"url": "u", "quality_score": None})["quality_score"] == pytest.approx(0.0)
    assert bi({"url": "u", "quality_score": {"x": 1}})["quality_score"] == pytest.approx(0.0)
    # 缺键同样默认 0.0。
    assert bi({"url": "u"})["quality_score"] == pytest.approx(0.0)


def test_deep2_select_best_mixed_invalid_quality_no_crash():
    """repos 中混入 quality_score 非数值（经 _map -> _build_repo_info 已钳为 0.0），
    _map 的 max()/_select_best_repo 不崩溃，best 落在合法高分者。"""
    result = {
        "repos": [
            {"url": "bad", "source": "pwc", "quality_score": "garbage", "local_path": "/bad", "is_official": False},
            {"url": "good", "source": "pwc", "quality_score": 0.55, "local_path": "/good", "is_official": True},
        ],
        "selected_repo": None, "external_resources": [], "resource_strategy": "use_repo",
    }
    update = _map_resource_scout_result(result, _base_state(), None)
    ri = update["resource_info"]
    assert ri["selected_repo"]["url"] == "good"
    # 脏分被钳为 0.0，不触发 QUALITY_WARN（good=0.55 >= 0.3）。
    assert "[QUALITY_WARN]" not in update.get("analysis_notes", "")


# --- 深化 12：回填 RepoInfo 的 12 字段完备性（含 source/is_official/local_path） --

def test_deep2_backfilled_repo_has_all_12_fields():
    """backfill 产出的 RepoInfo 必须是严格 12 字段（与 RepoInfo TypedDict 完全一致），
    且 source/is_official/local_path 这三个易漏字段被正确填充。"""
    url = "https://github.com/o/full"
    payload = ResourceInfo(repos=[], selected_repo=None, external_resources=[],
                           resource_strategy="from_scratch")
    # 工厂层 git_clone 成功记录：source=git_clone、is_official、local_path 齐全。
    content = _repo_info_json(url, local_path="/ws/full", quality=0.0, official=True)
    msgs = _clone_messages(url, content)
    did = _backfill_repos_from_tools(payload, msgs)
    assert did is True
    repo = payload["repos"][0]
    assert set(repo.keys()) == set(RepoInfo.__annotations__.keys())
    assert repo["source"] == "git_clone"
    assert repo["is_official"] is True
    assert repo["local_path"] == "/ws/full"
    # 缺 source 时回填给 "unknown"（_build_repo_info 兜底），仍是合法 12 字段。
    payload2 = ResourceInfo(repos=[], selected_repo=None, external_resources=[],
                            resource_strategy="from_scratch")
    no_source = json.dumps({"url": url, "local_path": "/ws/x", "has_readme": True},
                           sort_keys=True)
    did2 = _backfill_repos_from_tools(payload2, _clone_messages(url, no_source, call_id="c9"))
    assert did2 is True
    assert payload2["repos"][0]["source"] == "unknown"
    assert set(payload2["repos"][0].keys()) == set(RepoInfo.__annotations__.keys())


# --- 深化 13：degraded 路径 NodeError 只计 1 次（不重复累加本节点 degraded） ------

def test_deep2_degraded_nodeerror_counted_once_empty_repos():
    """repos 空降级路径：本节点 degraded NodeError 只追加 1 条（非每次重入翻倍），
    degraded_nodes 不重复——叠加上游 degraded 时计数干净。"""
    from core.errors import make_node_error
    state = _base_state(
        degraded_nodes=["paper_analysis"],
        node_errors=[make_node_error("paper_analysis", "degraded", "upstream", None)],
    )
    result = {"repos": [], "selected_repo": None, "external_resources": [],
              "resource_strategy": "use_repo"}  # repos 空但声称 use_repo -> 强制降级
    update = _map_resource_scout_result(result, state, None)
    rs_errs = [e for e in update["node_errors"]
               if e["node_name"] == "resource_scout" and e["error_type"] == "degraded"]
    assert len(rs_errs) == 1, "本节点 degraded NodeError 应恰好 1 条"
    assert update["degraded_nodes"].count("resource_scout") == 1
    # 上游 paper_analysis degraded 条目保真不丢。
    assert any(e["node_name"] == "paper_analysis" for e in update["node_errors"])


def test_deep2_error_field_degraded_counted_once():
    """result 含 error 字段降级路径同样只写 1 条 degraded NodeError、不污染上游计数。"""
    from core.errors import make_node_error
    state = _base_state(
        node_errors=[make_node_error("paper_analysis", "transient", "x", None)],
    )
    update = _map_resource_scout_result({"error": "boom"}, state, None)
    rs_errs = [e for e in update["node_errors"] if e["node_name"] == "resource_scout"]
    assert len(rs_errs) == 1
    assert rs_errs[0]["error_type"] == "degraded"
    # 上游 transient 条目保真（不被改成 degraded、不丢）。
    pa = [e for e in update["node_errors"] if e["node_name"] == "paper_analysis"]
    assert pa and pa[0]["error_type"] == "transient"


# --- 深化 14：external_resources 字段值统一被 _coerce_str 化（非字符串值不破坏 JSON）--

def test_deep2_external_resources_values_coerced_to_str():
    """external_resources 中 dict 项的非字符串值被 _coerce_str 化（如 int/None），
    保证后续 json 渲染安全、不残留非法类型。"""
    result = {
        "repos": [], "selected_repo": None,
        "external_resources": [{"name": "weights", "size": 1024, "verified": None}],
        "resource_strategy": "from_scratch",
    }
    update = _map_resource_scout_result(result, _base_state(), None)
    ext = update["resource_info"]["external_resources"][0]
    assert ext["name"] == "weights"
    assert ext["size"] == "1024"           # int -> str
    assert ext["verified"] == ""            # None -> ""（_coerce_str(None)）
    # 渲染为 JSON 不抛错。
    json.dumps(ext)


# --- 深化 15：backfill 在 react_messages=None / 空序列时安全返回 False -----------

def test_deep2_backfill_none_and_empty_messages_safe():
    payload = ResourceInfo(repos=[], selected_repo=None, external_resources=[],
                           resource_strategy="from_scratch")
    assert _backfill_repos_from_tools(payload, None) is False
    assert _backfill_repos_from_tools(payload, []) is False
    assert payload["repos"] == []
