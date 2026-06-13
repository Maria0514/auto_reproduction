"""Sprint 2 任务 C1 自测：core/graph.py 升级（resource_scout / planning 真节点接入 +
planning 3 路条件边）。

覆盖 dev-plan §C1 CP-C1-1 ~ CP-C1-10。

测试策略：
    - 节点形态 / 集合 / 编译选项等静态断言直接驱动 build_graph + _route_after_planning；
    - 端到端 invoke（natural pause / approve-next / cancel-end）通过 monkeypatch 上游 3 个
      ReAct wrapper（core.graph 命名空间）+ monkeypatch react_base.create_react_subgraph /
      create_llm（让 planning 内部 ReAct 子图脚本化），保留 planning 真实 interrupt 链路，
      不触发真实 LLM / SDK。
"""

from __future__ import annotations

from typing import Any, Dict
from unittest.mock import patch

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command

import core.graph as graph_module
import core.react_base as react_base
from core.graph import build_graph, _route_after_planning
from core.state import ExecutionMode


EXPECTED_NODES = {
    "paper_intake", "paper_analysis", "resource_scout",
    "planning", "coding", "execution", "reporting",
}


# ---------------------------------------------------------------------------
# CP-C1-1 / CP-C1-2：build_graph 返回 CompiledGraph + 7 节点集合
# ---------------------------------------------------------------------------

def test_cp_c1_1_returns_compiled_graph():
    g = build_graph(checkpointer=MemorySaver())
    assert isinstance(g, CompiledStateGraph)


def test_cp_c1_2_seven_business_nodes():
    g = build_graph(checkpointer=MemorySaver())
    business = {n for n in g.get_graph().nodes.keys() if not n.startswith("__")}
    assert business == EXPECTED_NODES


# ---------------------------------------------------------------------------
# CP-C1-3/4/5/6：节点形态
# ---------------------------------------------------------------------------

def test_cp_c1_3_intake_analysis_react_wrapper():
    assert graph_module.paper_intake.__name__ == "react_wrapper_paper_intake"
    assert graph_module.paper_analysis.__name__ == "react_wrapper_paper_analysis"


def test_cp_c1_4_resource_scout_react_wrapper():
    assert graph_module.resource_scout.__name__ == "react_wrapper_resource_scout"


def test_cp_c1_5_planning_handwritten():
    assert graph_module.planning.__name__ == "planning"


def test_cp_c1_6_placeholders_passthrough():
    for fn in (graph_module.coding, graph_module.execution, graph_module.reporting):
        assert fn({}) == {}
        assert fn({"user_input": "x"}) == {}
    assert graph_module._passthrough({"foo": "bar"}) == {}


# ---------------------------------------------------------------------------
# CP-C1-7：_route_after_planning 三路判定
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("state,expected", [
    ({"current_step": "cancelled_by_user"}, "end"),
    # cancel 优先级高于 approved
    ({"current_step": "cancelled_by_user", "reproduction_plan": {"approved": True}}, "end"),
    ({"reproduction_plan": {"approved": True}}, "next"),
    ({"reproduction_plan": {"approved": False}}, "self"),
    ({"reproduction_plan": {}}, "self"),
    ({}, "self"),
    ({"reproduction_plan": None}, "self"),
])
def test_cp_c1_7_route_after_planning(state, expected):
    assert _route_after_planning(state) == expected


# ---------------------------------------------------------------------------
# CP-C1-8：编译时不指定 interrupt_before / interrupt_after
# ---------------------------------------------------------------------------

def test_cp_c1_8_no_interrupt_before_after():
    """build_graph 内部不应向 compile 传 interrupt_before / interrupt_after。"""
    captured = {}
    real_compile = graph_module.StateGraph.compile

    def spy_compile(self, *args, **kwargs):
        captured.update(kwargs)
        return real_compile(self, *args, **kwargs)

    with patch.object(graph_module.StateGraph, "compile", spy_compile):
        build_graph(checkpointer=MemorySaver())
    assert "interrupt_before" not in captured
    assert "interrupt_after" not in captured


# ---------------------------------------------------------------------------
# 端到端 invoke helpers
# ---------------------------------------------------------------------------

class _FakePlanningSubgraph:
    """planning 内部 ReAct 子图脚本化：返回一份完整 plan。"""

    def invoke(self, initial):
        return {
            "result": {
                "plan_summary": "复现计划摘要",
                "code_strategy": "use_repo",
                "deliverables": ["README.md", "requirements.txt", "run.py"],
                "execution_steps": [{"step_name": "s", "command": "c", "expected_output": "o"}],
            },
            "messages": [],
            "round": 2,
            "status": "done",
        }


def _initial_state(**ov) -> Dict[str, Any]:
    s = {
        "user_input": "2405.14831",
        "llm_config_set": {
            "default": {"base_url": "b", "model": "m", "api_key": "k",
                        "temperature": 0.0, "max_tokens": 1024},
            "overrides": {},
        },
        "retry_budget_remaining": 50,
        "node_errors": [],
        "degraded_nodes": [],
        "_planning_revise_count": 0,
        "_planning_user_feedback": None,
        "analysis_notes": "",
        "messages": [],
    }
    s.update(ov)
    return s


def _patched_graph(monkeypatch):
    """上游 3 节点 fake + planning 内部子图脚本化（保留真实 interrupt）。"""
    def fi(state): return {"paper_meta": {"arxiv_id": "2405.14831"}}
    def fa(state): return {"paper_analysis": {"method_summary": "m"}}
    def frs(state):
        return {"current_step": "resource_scout",
                "resource_info": {"repos": [], "selected_repo": None,
                                  "external_resources": [], "resource_strategy": "from_scratch"}}

    monkeypatch.setattr(graph_module, "paper_intake", fi)
    monkeypatch.setattr(graph_module, "paper_analysis", fa)
    monkeypatch.setattr(graph_module, "resource_scout", frs)
    monkeypatch.setattr(react_base, "create_react_subgraph", lambda **k: _FakePlanningSubgraph())
    monkeypatch.setattr(react_base, "create_llm", lambda c: object())
    return build_graph(checkpointer=MemorySaver())


# ---------------------------------------------------------------------------
# CP-C1-9：全链路 invoke 跑到 planning 后自然暂停（无需手动 interrupt_after）
# ---------------------------------------------------------------------------

def test_cp_c1_9_natural_pause_at_planning(monkeypatch):
    g = _patched_graph(monkeypatch)
    cfg = {"configurable": {"thread_id": "c1-pause"}}
    out = g.invoke(_initial_state(), cfg)
    # interrupt() 在 planning 节点内部触发 -> graph 自然暂停
    assert "__interrupt__" in out
    snap = g.get_state(cfg)
    assert snap.next == ("planning",)


def test_cp_c1_9b_resume_approve_routes_to_next(monkeypatch):
    g = _patched_graph(monkeypatch)
    cfg = {"configurable": {"thread_id": "c1-approve"}}
    g.invoke(_initial_state(), cfg)
    out = g.invoke(Command(resume={"decision": "approve"}), cfg)
    # approve -> next -> coding -> ... -> END
    snap = g.get_state(cfg)
    assert snap.next == ()  # 已到 END
    assert out.get("reproduction_plan", {}).get("approved") is True


def test_cp_c1_9c_resume_revise_self_loop(monkeypatch):
    """revise -> self-loop 重入 planning -> 再次暂停（无次数硬上限）。"""
    g = _patched_graph(monkeypatch)
    cfg = {"configurable": {"thread_id": "c1-revise"}}
    g.invoke(_initial_state(), cfg)
    out = g.invoke(Command(resume={"decision": "revise", "user_feedback": "改"}), cfg)
    assert "__interrupt__" in out  # 又暂停在 planning
    snap = g.get_state(cfg)
    assert snap.next == ("planning",)
    assert snap.values.get("_planning_revise_count") == 1


# ---------------------------------------------------------------------------
# CP-C1-10：cancel 路径 -> 路由到 END（不进 coding）
# ---------------------------------------------------------------------------

def test_cp_c1_10_cancel_routes_to_end(monkeypatch):
    coding_hit = {"called": False}

    def spy_coding(state):
        coding_hit["called"] = True
        return {}

    # 先把 coding spy 与上游 fake / 子图脚本化全部装好，再 build_graph
    # （build_graph 在编译时捕获 core.graph 命名空间的节点引用）。
    monkeypatch.setattr(graph_module, "coding", spy_coding)
    g = _patched_graph(monkeypatch)

    cfg = {"configurable": {"thread_id": "c1-cancel"}}
    g.invoke(_initial_state(), cfg)
    out = g.invoke(Command(resume={"decision": "cancel"}), cfg)

    snap = g.get_state(cfg)
    assert snap.next == ()  # 到 END
    assert snap.values.get("current_step") == "cancelled_by_user"
    assert coding_hit["called"] is False  # cancel 不应进入 coding
    assert "[CANCELLED]" in (snap.values.get("analysis_notes") or "")


def test_cp_c1_10b_code_only_routes_next(monkeypatch):
    g = _patched_graph(monkeypatch)
    cfg = {"configurable": {"thread_id": "c1-codeonly"}}
    g.invoke(_initial_state(), cfg)
    out = g.invoke(Command(resume={"decision": "code_only"}), cfg)
    snap = g.get_state(cfg)
    assert snap.next == ()
    assert snap.values.get("execution_mode") == ExecutionMode.CODE_ONLY
    assert out.get("reproduction_plan", {}).get("approved") is True


# ===========================================================================
# 深化补强（@测试工程师代理 2026-06-03）：编译图 + 真实 SqliteSaver 持久化
# ===========================================================================

import tempfile  # noqa: E402
from pathlib import Path  # noqa: E402

from langgraph.checkpoint.sqlite import SqliteSaver  # noqa: E402


def _patched_graph_with_saver(monkeypatch, saver):
    """同 _patched_graph，但用调用方传入的 checkpointer（真实 SqliteSaver）。"""
    def fi(state): return {"paper_meta": {"arxiv_id": "2405.14831"}}
    def fa(state): return {"paper_analysis": {"method_summary": "m"}}
    def frs(state):
        return {"current_step": "resource_scout",
                "resource_info": {"repos": [], "selected_repo": None,
                                  "external_resources": [], "resource_strategy": "from_scratch"}}

    monkeypatch.setattr(graph_module, "paper_intake", fi)
    monkeypatch.setattr(graph_module, "paper_analysis", fa)
    monkeypatch.setattr(graph_module, "resource_scout", frs)
    monkeypatch.setattr(react_base, "create_react_subgraph", lambda **k: _FakePlanningSubgraph())
    monkeypatch.setattr(react_base, "create_llm", lambda c: object())
    return build_graph(checkpointer=saver)


# --- 核心修复验证：analysis_notes 是 GlobalState 通道，编译图写入不被静默丢弃 ---

def test_analysis_notes_cancel_survives_compiled_graph(monkeypatch):
    """[B2/B3 潜伏 BUG 修复回归] cancel 写顶层 analysis_notes，经编译图合并后不被丢弃。

    修复前：analysis_notes 不是 GlobalState 通道，planning 顶层写入被 LangGraph 静默丢弃。
    本用例驱动真实编译图（MemorySaver），断言 resume cancel 后 analysis_notes 真实出现在
    最终 state（snap.values + invoke 返回值），证明通道修复在编译图层面生效。
    """
    g = _patched_graph(monkeypatch)
    cfg = {"configurable": {"thread_id": "c1-notes-cancel"}}
    g.invoke(_initial_state(), cfg)
    out = g.invoke(Command(resume={"decision": "cancel"}), cfg)
    snap = g.get_state(cfg)
    # 通道修复生效：顶层 analysis_notes 写入存活到合并后的 state
    assert "[CANCELLED]" in (snap.values.get("analysis_notes") or "")
    assert "[CANCELLED]" in (out.get("analysis_notes") or "")


def test_analysis_notes_fallback_survives_compiled_graph(monkeypatch):
    """非法 resume payload 走 _finalize_approve 兜底写 [PLANNING_FALLBACK]，编译图层面存活。"""
    g = _patched_graph(monkeypatch)
    cfg = {"configurable": {"thread_id": "c1-notes-fallback"}}
    g.invoke(_initial_state(), cfg)
    g.invoke(Command(resume={"foo": "bar"}), cfg)  # 非法 payload
    snap = g.get_state(cfg)
    assert "[PLANNING_FALLBACK]" in (snap.values.get("analysis_notes") or "")
    assert snap.values.get("reproduction_plan", {}).get("approved") is True


def test_analysis_notes_survives_sqlite_reload(monkeypatch, tmp_path):
    """[核心修复 + 持久化] analysis_notes 经真实 SqliteSaver 落盘 + 新 saver 实例回读后仍保留。

    用 tmp_path 真实 SQLite 文件：第一个 graph 实例 cancel 写 analysis_notes 并落盘，
    用全新 SqliteSaver 实例（同文件）重建 graph 后 get_state 回读，断言 analysis_notes 仍在。
    这是对"通道声明 + 持久化"双重保证的端到端实证。
    """
    db = str(tmp_path / "c1_notes.sqlite")
    cfg = {"configurable": {"thread_id": "c1-sqlite-notes"}}

    with SqliteSaver.from_conn_string(db) as saver1:
        g1 = _patched_graph_with_saver(monkeypatch, saver1)
        g1.invoke(_initial_state(), cfg)
        g1.invoke(Command(resume={"decision": "cancel"}), cfg)
        live = g1.get_state(cfg).values.get("analysis_notes")
    assert "[CANCELLED]" in (live or "")

    # 全新 saver 实例 + 全新编译图，仅回读历史 checkpoint
    with SqliteSaver.from_conn_string(db) as saver2:
        g2 = _patched_graph_with_saver(monkeypatch, saver2)
        reloaded = g2.get_state(cfg).values.get("analysis_notes")
    assert "[CANCELLED]" in (reloaded or ""), "analysis_notes 未能从 SQLite 回读，通道/持久化失效"


# --- revise 计数跨 self-loop 真实持久化（多次 revise）---

def test_revise_count_increments_across_self_loops(monkeypatch):
    """连续 3 次 revise 经编译图 self-loop 重入 planning，_planning_revise_count 持久递增 1->2->3。

    验证 self-loop 边真实生效 + revise_count 通过 checkpoint 跨节点重入累加（无次数硬上限）。
    """
    g = _patched_graph(monkeypatch)
    cfg = {"configurable": {"thread_id": "c1-revise-loop"}}
    g.invoke(_initial_state(), cfg)
    for expected in (1, 2, 3):
        out = g.invoke(Command(resume={"decision": "revise", "user_feedback": f"改{expected}"}), cfg)
        snap = g.get_state(cfg)
        assert "__interrupt__" in out  # 每次都再次暂停在 planning（self-loop）
        assert snap.next == ("planning",)
        assert snap.values.get("_planning_revise_count") == expected
        assert snap.values.get("_planning_user_feedback") == f"改{expected}"
    # 多次 revise 后仍未强制 approve
    assert g.get_state(cfg).values.get("reproduction_plan", {}).get("approved") is not True


def test_switch_repo_resource_info_persists_then_approve(monkeypatch):
    """S2-13：switch_repo 未命中既有候选 → self-loop 重入 → ReAct 抓取 → 合并进 repos
    并默认选中 + 持久化，下一轮 approve 收尾仍保留 user_provided 仓库。"""
    import json as _json
    from langchain_core.messages import AIMessage, ToolMessage

    target = "https://github.com/u/v"

    class _CloningSubgraph:
        """重入后（context 含 pending_repo_url）模拟成功 clone：发 ToolMessage + result.repos 打分。"""

        def invoke(self, initial):
            messages = list(initial.get("messages") or [])
            has_pending = any(
                isinstance(getattr(m, "content", None), str) and "pending_repo_url" in m.content
                for m in messages
            )
            repos = []
            tool_msgs = []
            if has_pending:
                tool_payload = {
                    "url": target, "source": "git_clone", "local_path": "/ws/repos/u_v",
                    "has_readme": True, "has_requirements": True, "is_official": True,
                    "stars": None, "forks": None, "last_commit_date": None,
                    "commit_count_recent": None, "dir_structure": ["src"],
                    "quality_score": 0.7,
                }
                tool_msgs = [
                    AIMessage(content="", tool_calls=[{
                        "name": "git_clone_and_analyze", "args": {"url": target}, "id": "t1"}]),
                    ToolMessage(
                        content=_json.dumps(tool_payload, ensure_ascii=False, sort_keys=True),
                        name="git_clone_and_analyze", tool_call_id="t1"),
                ]
                repos = [{"url": target, "quality_score": 0.7}]
            return {
                "result": {
                    "plan_summary": "复现计划摘要",
                    "code_strategy": "use_repo",
                    "deliverables": ["README.md", "requirements.txt", "run.py"],
                    "execution_steps": [{"step_name": "s", "command": "c", "expected_output": "o"}],
                    "repos": repos,
                },
                "messages": tool_msgs,
                "round": 2,
                "status": "done",
            }

    def fi(state): return {"paper_meta": {"arxiv_id": "2405.14831"}}
    def fa(state): return {"paper_analysis": {"method_summary": "m"}}
    def frs(state):
        return {"current_step": "resource_scout",
                "resource_info": {"repos": [], "selected_repo": None,
                                  "external_resources": [], "resource_strategy": "from_scratch"}}
    monkeypatch.setattr(graph_module, "paper_intake", fi)
    monkeypatch.setattr(graph_module, "paper_analysis", fa)
    monkeypatch.setattr(graph_module, "resource_scout", frs)
    monkeypatch.setattr(react_base, "create_react_subgraph", lambda **k: _CloningSubgraph())
    monkeypatch.setattr(react_base, "create_llm", lambda c: object())
    g = build_graph(checkpointer=MemorySaver())

    cfg = {"configurable": {"thread_id": "c1-switch-then-approve"}}
    g.invoke(_initial_state(), cfg)
    # 第一轮：switch_repo（未命中 → 写 pending_url → self-loop 重入抓取）
    out1 = g.invoke(
        Command(resume={"decision": "switch_repo", "new_repo_url": target}),
        cfg,
    )
    assert "__interrupt__" in out1
    # 重入后 ReAct 抓取成功 → interrupt payload 携带合并后的 resource_info（真实 quality_score）。
    interrupt_obj = out1["__interrupt__"][0]
    payload = interrupt_obj.value
    assert payload["resource_info"]["selected_repo"]["url"] == target
    assert payload["resource_info"]["selected_repo"]["source"] == "user_provided"
    assert payload["resource_info"]["selected_repo"]["quality_score"] == 0.7
    assert payload["switch_repo_failed"] is False
    snap1 = g.get_state(cfg)
    assert snap1.values["_planning_revise_count"] == 1
    # 第二轮：approve -> 收尾到 END（合并后的 resource_info + 清理标记随本轮 return 提交）
    g.invoke(Command(resume={"decision": "approve"}), cfg)
    snap2 = g.get_state(cfg)
    assert snap2.next == ()
    assert snap2.values["reproduction_plan"]["approved"] is True
    # switch 抓取合并的 user_provided 仓库被持久化、pending/失败标记已清。
    assert snap2.values["resource_info"]["selected_repo"]["url"] == target
    assert snap2.values["resource_info"]["selected_repo"]["source"] == "user_provided"
    assert snap2.values.get("_planning_pending_repo_url") is None
    assert snap2.values.get("_planning_switch_failed") is False


def test_route_priority_cancelled_overrides_approved_via_graph(monkeypatch):
    """3 路优先级编译图实证：current_step=cancelled_by_user 优先于 approved，路由到 END。

    构造 planning 内部子图返回 plan，但模拟 cancel（写 cancelled_by_user）——
    即便 plan 存在，cancel 优先级最高，graph 不进 coding。
    """
    coding_hit = {"called": False}

    def spy_coding(state):
        coding_hit["called"] = True
        return {}

    monkeypatch.setattr(graph_module, "coding", spy_coding)
    g = _patched_graph(monkeypatch)
    cfg = {"configurable": {"thread_id": "c1-priority"}}
    g.invoke(_initial_state(), cfg)
    g.invoke(Command(resume={"decision": "cancel"}), cfg)
    snap = g.get_state(cfg)
    assert snap.next == ()
    assert snap.values["current_step"] == "cancelled_by_user"
    assert coding_hit["called"] is False
