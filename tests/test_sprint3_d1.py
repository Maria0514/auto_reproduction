"""Sprint 3 阶段 D / 任务 D1 自测：core/graph.py 路由编排 + planning interrupt_kind。

覆盖 dev-plan §537-543 的检查点 CP-D1-1~7：

    CP-D1-1  7 节点骨架不变性（AC-S3-10 ①④）：编译成功 + 节点集合精确 = 7，无禁止节点。
    CP-D1-2  coding/execution/reporting 注册的是真实现（非 pass-through，占位 + _passthrough 已删）。
    CP-D1-3  _route_after_coding（AC-S3-06）：CODE_ONLY → reporting / FULL → execution（Enum + str）。
    CP-D1-4  _route_after_execution 全路覆盖（AC-S3-07 / AC-S3-10 ②③）：
             ⚠️ await_dev_loop_interrupt → execution self-loop（interrupt#2 命门，L-C3-01）；
             retry_coding → coding；revise_plan/terminate/export_code 三态；成功/降级 → reporting。
    CP-D1-5  修复回边为新增条件边 + planning 3 路 + reporting→END 语义保留。
    CP-D1-6  planning interrupt payload 含 interrupt_kind=="planning"；revise_plan 回流 self。
    CP-D1-7  mock 全链路 happy path（START→…→reporting→END，断言路径）。

权威输入契约：_route_after_execution 以 core/nodes/execution.py 实际返回字段为准
（架构 §2.5.3 字面遗漏了 await_dev_loop_interrupt self-loop 一路，以 C3 交接为准）。

不触发真实 LLM / SDK / sandbox / interrupt：全部用 mock 节点 + MemorySaver。
"""

from __future__ import annotations

import importlib
import inspect
import sys
from pathlib import Path
from typing import Any, Dict
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest  # noqa: E402
from langgraph.checkpoint.memory import MemorySaver  # noqa: E402
from langgraph.graph.state import CompiledStateGraph  # noqa: E402

from core import graph as graph_module  # noqa: E402
from core.graph import (  # noqa: E402
    _route_after_coding,
    _route_after_execution,
    _route_after_planning,
    build_graph,
)
from core.state import ExecutionMode  # noqa: E402

EXPECTED_NODES = {
    "paper_intake",
    "paper_analysis",
    "resource_scout",
    "planning",
    "coding",
    "execution",
    "reporting",
}
FORBIDDEN_NODES = {"coding_only", "dev_loop", "exit_dev_loop"}


def _business_nodes(graph) -> set:
    all_nodes = set(graph.get_graph().nodes.keys())
    return {n for n in all_nodes if not n.startswith("__")}


# ===========================================================================
# CP-D1-1：7 节点骨架不变性（AC-S3-10 ①④）
# ===========================================================================


def test_cp_d1_1_build_graph_compiles():
    """build_graph() 编译成功，返回 CompiledStateGraph。"""
    g = build_graph(checkpointer=MemorySaver())
    assert isinstance(g, CompiledStateGraph)
    assert hasattr(g, "invoke") and callable(g.invoke)


def test_cp_d1_1_exactly_seven_nodes():
    """节点集合恰为 7 个业务节点（精确相等，不多不少）。"""
    g = build_graph(checkpointer=MemorySaver())
    nodes = _business_nodes(g)
    assert nodes == EXPECTED_NODES, (
        f"节点集合不匹配：缺 {EXPECTED_NODES - nodes}，多 {nodes - EXPECTED_NODES}"
    )
    assert len(nodes) == 7


def test_cp_d1_1_no_forbidden_subgraph_nodes():
    """严禁新增 coding_only / dev_loop / exit_dev_loop 节点（AC-S3-10）。"""
    g = build_graph(checkpointer=MemorySaver())
    nodes = _business_nodes(g)
    assert not (nodes & FORBIDDEN_NODES), f"出现禁止节点：{nodes & FORBIDDEN_NODES}"


# ===========================================================================
# CP-D1-2：三节点注册的是真实现（非 pass-through，占位已删）
# ===========================================================================


def test_cp_d1_2_passthrough_helper_deleted():
    """graph.py L57-72 占位函数 + _passthrough 已删除（CP-D1-2）。"""
    assert not hasattr(graph_module, "_passthrough")


def test_cp_d1_2_coding_is_react_wrapper_real_impl():
    """coding 是 core.nodes.coding 经 _make_react_wrapper 生成的真实现（非占位）。"""
    coding_module = importlib.import_module("core.nodes.coding")
    assert graph_module.coding is coding_module.coding
    # wrapper 工厂生成，__name__ 携节点名，__module__ 指向 react_base。
    assert graph_module.coding.__name__ == "react_wrapper_coding"
    assert graph_module.coding.__module__ == "core.react_base"


def test_cp_d1_2_execution_is_handwritten_real_impl():
    """execution 是 core.nodes.execution 手写复合节点真实现（非占位）。"""
    execution_module = importlib.import_module("core.nodes.execution")
    assert graph_module.execution is execution_module.execution
    assert graph_module.execution.__module__ == "core.nodes.execution"
    assert graph_module.execution.__name__ == "execution"


def test_cp_d1_2_reporting_is_real_impl():
    """reporting 是 core.nodes.reporting 真实现（非占位）。"""
    reporting_module = importlib.import_module("core.nodes.reporting")
    assert graph_module.reporting is reporting_module.reporting
    assert graph_module.reporting.__module__ == "core.nodes.reporting"


def test_cp_d1_2_real_impls_not_return_empty_dict():
    """反向证：真实现不再是返回 {} 的占位（execution 对空 state 也会产出更新）。

    execution(空 state) 走 code_output_dir 缺失降级分支，返回非空 dict（含 degraded）。
    （coding 是 ReAct wrapper 会触发 LLM，不在此处直接调用；其身份已由上面断言覆盖。）
    """
    out = graph_module.execution({})
    assert out != {}, "execution 真实现对空 state 也应返回非空更新（降级），而非占位 {}"
    assert "execution_result" in out


# ===========================================================================
# CP-D1-3：_route_after_coding（AC-S3-06）—— CODE_ONLY→reporting / FULL→execution
# ===========================================================================


@pytest.mark.parametrize(
    "state,expected",
    [
        ({"execution_mode": ExecutionMode.CODE_ONLY}, "skip_execution"),  # Enum CODE_ONLY
        ({"execution_mode": "code_only"}, "skip_execution"),              # str 取值
        ({"execution_mode": ExecutionMode.CODE_ONLY.value}, "skip_execution"),
        ({"execution_mode": ExecutionMode.FULL}, "to_execution"),         # Enum FULL
        ({"execution_mode": "full"}, "to_execution"),                     # str
        ({"execution_mode": None}, "to_execution"),                       # 缺省 → FULL 语义
        ({}, "to_execution"),                                             # 无字段 → FULL 语义
    ],
)
def test_cp_d1_3_route_after_coding(state, expected):
    """_route_after_coding：CODE_ONLY → skip_execution(→reporting)；其余 → to_execution。"""
    assert _route_after_coding(state) == expected


def test_cp_d1_3_route_after_coding_targets_in_graph():
    """coding 条件边的两个出边目的地（execution / reporting）在编译图中存在。"""
    g = build_graph(checkpointer=MemorySaver())
    nodes = _business_nodes(g)
    assert "execution" in nodes and "reporting" in nodes


# ===========================================================================
# CP-D1-4：_route_after_execution 全路覆盖（AC-S3-07 / AC-S3-10 ②③）
# ===========================================================================


def test_cp_d1_4_await_dev_loop_interrupt_self_loop():
    """⚠️ interrupt#2 命门（L-C3-01 强交接）：await_dev_loop_interrupt → execution self-loop。

    execution 首次失败回合 commit 边界 return 时置 _dev_loop_route="await_dev_loop_interrupt"
    （尚未 interrupt），必须 self-loop 重入 execution，否则第二个人在回路 interrupt 永不触发。
    架构 §2.5.3 字面遗漏此路，以 C3 交接（TODO L214 / dev-plan L492）为准。
    """
    assert (
        _route_after_execution({"_dev_loop_route": "await_dev_loop_interrupt"})
        == "execution"
    )


def test_cp_d1_4_retry_coding_to_coding():
    """retry_coding → coding（修复回边，fix_loop_count 本回合已 +1）。"""
    assert _route_after_execution({"_dev_loop_route": "retry_coding"}) == "coding"


def test_cp_d1_4_revise_plan_to_planning():
    """interrupt#2 resume revise_plan → planning。"""
    assert _route_after_execution({"user_fix_decision": "revise_plan"}) == "planning"


def test_cp_d1_4_terminate_to_end():
    """interrupt#2 resume terminate → end（execution 写 current_step=cancelled_by_user）。"""
    assert (
        _route_after_execution(
            {"user_fix_decision": "terminate", "current_step": "cancelled_by_user"}
        )
        == "end"
    )


def test_cp_d1_4_cancelled_by_user_to_end():
    """current_step=cancelled_by_user 单独也路由到 end（防御冗余）。"""
    assert _route_after_execution({"current_step": "cancelled_by_user"}) == "end"


def test_cp_d1_4_export_code_to_reporting():
    """interrupt#2 resume export_code → reporting（降级导出）。"""
    assert _route_after_execution({"user_fix_decision": "export_code"}) == "reporting"


def test_cp_d1_4_success_to_reporting():
    """B 档成功（_dev_loop_route=None + success=True）→ reporting。"""
    assert (
        _route_after_execution(
            {"_dev_loop_route": None, "execution_result": {"success": True}}
        )
        == "reporting"
    )


def test_cp_d1_4_degraded_to_reporting():
    """降级（_dev_loop_route=None + degraded_nodes，无 user_fix_decision）→ reporting。"""
    assert (
        _route_after_execution(
            {"_dev_loop_route": None, "degraded_nodes": ["execution"]}
        )
        == "reporting"
    )


def test_cp_d1_4_empty_state_fallback_reporting():
    """空 state 兜底 → reporting（不空转）。"""
    assert _route_after_execution({}) == "reporting"


def test_cp_d1_4_dev_loop_route_priority_over_decision():
    """_dev_loop_route 两路优先于 user_fix_decision（命门最先命中，防御性优先级）。

    正常流程下 await/retry 分支不写 user_fix_decision、三态分支已清 _dev_loop_route=None，
    字段互斥；此处构造同时存在的边界，断言 self-loop 命门优先。
    """
    # 即便误带 user_fix_decision，await 仍优先 self-loop（防 interrupt#2 漏触发）。
    assert (
        _route_after_execution(
            {"_dev_loop_route": "await_dev_loop_interrupt", "user_fix_decision": "terminate"}
        )
        == "execution"
    )
    assert (
        _route_after_execution(
            {"_dev_loop_route": "retry_coding", "user_fix_decision": "export_code"}
        )
        == "coding"
    )


def test_cp_d1_4_route_only_reads_state_no_mutation():
    """零 reducer 红线相关：路由函数只读 state，调用前后 state 不被修改。"""
    before: Dict[str, Any] = {
        "_dev_loop_route": "retry_coding",
        "user_fix_decision": None,
        "node_errors": [{"node": "execution"}],
        "degraded_nodes": [],
        "fix_loop_history": [],
    }
    snapshot = {
        "_dev_loop_route": before["_dev_loop_route"],
        "node_errors_id": id(before["node_errors"]),
        "node_errors_len": len(before["node_errors"]),
    }
    _route_after_execution(before)
    _route_after_coding(before)
    assert before["_dev_loop_route"] == snapshot["_dev_loop_route"]
    assert id(before["node_errors"]) == snapshot["node_errors_id"]
    assert len(before["node_errors"]) == snapshot["node_errors_len"]


# ===========================================================================
# CP-D1-5：修复回边为新增条件边 + planning 3 路 + reporting→END 语义保留
# ===========================================================================


def test_cp_d1_5_planning_three_way_route_preserved():
    """sp2 planning 3 路条件边语义零改动（end / next / self）。"""
    assert _route_after_planning({"current_step": "cancelled_by_user"}) == "end"
    assert _route_after_planning({"reproduction_plan": {"approved": True}}) == "next"
    assert _route_after_planning({"reproduction_plan": {"approved": False}}) == "self"
    assert _route_after_planning({}) == "self"
    # cancel 优先级高于 approved（sp2 既有语义）。
    assert (
        _route_after_planning(
            {"current_step": "cancelled_by_user", "reproduction_plan": {"approved": True}}
        )
        == "end"
    )


def test_cp_d1_5_edges_structure():
    """边结构核对（架构 §2.5.6）：

    - coding / execution 是条件边（branches，非单一固定后继）；
    - reporting → END 保留（reporting 无业务后继节点）；
    - planning 仍是 3 路条件边。
    用 get_graph() 的 edges 关系检查 coding/execution 的可达后继集合。
    """
    g = build_graph(checkpointer=MemorySaver())
    drawable = g.get_graph()
    # 收集每个源节点的后继集合（含条件边的所有分支目标）。
    succ: Dict[str, set] = {}
    for e in drawable.edges:
        succ.setdefault(e.source, set()).add(e.target)

    # coding 条件边 2 路：execution + reporting。
    assert succ.get("coding") == {"execution", "reporting"}, (
        f"coding 后继应为 {{execution, reporting}}，实际 {succ.get('coding')}"
    )
    # execution 条件边：self-loop(execution) + coding + planning + reporting + END。
    exec_succ = succ.get("execution", set())
    assert {"execution", "coding", "planning", "reporting"} <= exec_succ, (
        f"execution 后继缺路由分支，实际 {exec_succ}"
    )
    # execution 应能到 END（terminate 路径）。
    assert any(t == "__end__" or t.startswith("__end") for t in exec_succ), (
        f"execution 应有到 END 的分支（terminate），实际 {exec_succ}"
    )
    # reporting 只到 END（无业务后继）。
    rep_succ = succ.get("reporting", set())
    assert rep_succ == set() or all(
        t == "__end__" or t.startswith("__end") for t in rep_succ
    ), f"reporting 后继应仅为 END，实际 {rep_succ}"


def test_cp_d1_5_planning_still_conditional_three_way():
    """planning 出边仍是 3 路条件边（self / coding / END）。"""
    g = build_graph(checkpointer=MemorySaver())
    drawable = g.get_graph()
    plan_succ = {e.target for e in drawable.edges if e.source == "planning"}
    # planning -> self(planning) + coding + END。
    assert "planning" in plan_succ  # self-loop（revise/switch_repo）
    assert "coding" in plan_succ    # next（approve/code_only）
    assert any(t == "__end__" or t.startswith("__end") for t in plan_succ)  # cancel


# ===========================================================================
# CP-D1-6：planning interrupt payload 含 interrupt_kind=="planning"
# ===========================================================================


def test_cp_d1_6_planning_interrupt_kind_present():
    """planning.py 微改：interrupt payload 加 interrupt_kind=="planning"（§2.6.1）。

    用最小 StateGraph + MemorySaver 跑 planning 节点到 interrupt，捕获 payload 断言 kind。
    """
    import uuid

    from langgraph.graph import END, START, StateGraph
    from core.nodes.planning import planning as planning_node
    from core.state import GlobalState

    sg = StateGraph(GlobalState)
    sg.add_node("planning", planning_node)
    sg.add_edge(START, "planning")
    sg.add_edge("planning", END)
    compiled = sg.compile(checkpointer=MemorySaver())

    thread_id = f"d1-planning-kind-{uuid.uuid4()}"
    config = {"configurable": {"thread_id": thread_id}}

    # 用 mock 的 _planning_react 跳过真实 LLM，直接给 approved=False 的 plan 触发 interrupt。
    planning_mod = importlib.import_module("core.nodes.planning")

    def fake_react(state):
        return {
            "reproduction_plan": {
                "plan_summary": "fake",
                "approved": False,
                "code_strategy": "",
                "execution_steps": [],
                "deliverables": [],
                "environment": {},
                "risk_notes": "",
            },
            "current_step": "planning",
        }

    with patch.object(planning_mod, "_planning_react", fake_react):
        compiled.invoke({"user_input": "x", "node_errors": [], "degraded_nodes": []}, config)
        snapshot = compiled.get_state(config)

    # interrupt 暂停后，tasks 内 interrupt 元数据携 payload。
    payloads = []
    for task in snapshot.tasks:
        for itr in getattr(task, "interrupts", ()) or ():
            val = getattr(itr, "value", None)
            if isinstance(val, dict):
                payloads.append(val)
    assert payloads, "planning 应触发 interrupt 并携带 payload"
    assert any(p.get("interrupt_kind") == "planning" for p in payloads), (
        f"planning interrupt payload 应含 interrupt_kind=='planning'，实际 {payloads}"
    )


def test_cp_d1_6_revise_plan_reenters_planning_self():
    """revise_plan 回流（approved=False）→ _route_after_planning 走 self（重规划）。"""
    # execution revise_plan 出口写 approved=False；planning 重入后 _route_after_planning
    # 因 approved=False 走 self（即重新规划，不直接 next）。
    assert (
        _route_after_planning({"reproduction_plan": {"approved": False}}) == "self"
    )


# ===========================================================================
# CP-D1-7：mock 全链路 happy path（START→…→reporting→END）
# ===========================================================================


def test_cp_d1_7_full_pipeline_happy_path_full_mode():
    """FULL 模式 happy path：START → intake → analysis → scout → planning(approve)
    → coding → (FULL) execution → (成功) reporting → END。

    7 节点全部 mock 为返回固定 dict，避免真实 LLM / SDK / sandbox / interrupt。
    断言路径经过的节点（用 current_step 链 + 关键字段落点）。
    """

    def fake_intake(state):
        return {"paper_meta": {"arxiv_id": "2410.21276", "title": "P"}, "current_step": "paper_intake"}

    def fake_analysis(state):
        return {"paper_analysis": {"method_summary": "m"}, "current_step": "paper_analysis"}

    def fake_scout(state):
        return {"current_step": "resource_scout"}

    def fake_planning(state):
        return {
            "reproduction_plan": {"plan_summary": "p", "approved": True},
            "execution_mode": ExecutionMode.FULL,
            "current_step": "planning",
        }

    def fake_coding(state):
        return {"code_output_dir": "/tmp/d1/code", "current_step": "coding"}

    def fake_execution(state):
        return {
            "execution_result": {"success": True, "metrics": {"acc": 0.9}},
            "_dev_loop_route": None,
            "current_step": "execution",
        }

    def fake_reporting(state):
        return {"report_path": "/tmp/d1/report.md", "current_step": "reporting"}

    with patch.object(graph_module, "paper_intake", fake_intake), patch.object(
        graph_module, "paper_analysis", fake_analysis
    ), patch.object(graph_module, "resource_scout", fake_scout), patch.object(
        graph_module, "planning", fake_planning
    ), patch.object(graph_module, "coding", fake_coding), patch.object(
        graph_module, "execution", fake_execution
    ), patch.object(graph_module, "reporting", fake_reporting):
        g = build_graph(checkpointer=MemorySaver())
        final = g.invoke(
            {
                "user_input": "2410.21276",
                "node_errors": [],
                "degraded_nodes": [],
                "messages": [],
            },
            {"configurable": {"thread_id": "d1-happy-full"}},
        )

    # 全链路落点齐全 → 证明路径 intake→analysis→scout→planning→coding→execution→reporting。
    assert final["paper_meta"]["arxiv_id"] == "2410.21276"
    assert final["paper_analysis"]["method_summary"] == "m"
    assert final["reproduction_plan"]["approved"] is True
    assert final["code_output_dir"] == "/tmp/d1/code"
    assert final["execution_result"]["success"] is True
    assert final["report_path"] == "/tmp/d1/report.md"
    assert final["current_step"] == "reporting"  # 终态是 reporting（已到 END）


def test_cp_d1_7_full_pipeline_happy_path_code_only_mode():
    """CODE_ONLY 模式 happy path：coding → (skip_execution) reporting，跳过 execution。

    断言 execution 节点未被执行（execution mock 设置一个会失败的断言确认未触达）。
    """
    execution_called = {"hit": False}

    def fake_intake(state):
        return {"paper_meta": {"arxiv_id": "X", "title": "P"}, "current_step": "paper_intake"}

    def fake_analysis(state):
        return {"paper_analysis": {"method_summary": "m"}, "current_step": "paper_analysis"}

    def fake_scout(state):
        return {"current_step": "resource_scout"}

    def fake_planning(state):
        return {
            "reproduction_plan": {"plan_summary": "p", "approved": True},
            "execution_mode": ExecutionMode.CODE_ONLY,  # → coding 出边 skip_execution
            "current_step": "planning",
        }

    def fake_coding(state):
        return {"code_output_dir": "/tmp/d1/code_only", "current_step": "coding"}

    def fake_execution(state):
        execution_called["hit"] = True  # 不应被触达
        return {"current_step": "execution"}

    def fake_reporting(state):
        return {"report_path": "/tmp/d1/report_co.md", "current_step": "reporting"}

    with patch.object(graph_module, "paper_intake", fake_intake), patch.object(
        graph_module, "paper_analysis", fake_analysis
    ), patch.object(graph_module, "resource_scout", fake_scout), patch.object(
        graph_module, "planning", fake_planning
    ), patch.object(graph_module, "coding", fake_coding), patch.object(
        graph_module, "execution", fake_execution
    ), patch.object(graph_module, "reporting", fake_reporting):
        g = build_graph(checkpointer=MemorySaver())
        final = g.invoke(
            {
                "user_input": "X",
                "node_errors": [],
                "degraded_nodes": [],
                "messages": [],
            },
            {"configurable": {"thread_id": "d1-happy-codeonly"}},
        )

    assert execution_called["hit"] is False, "CODE_ONLY 应跳过 execution 直达 reporting"
    assert final["code_output_dir"] == "/tmp/d1/code_only"
    assert final["report_path"] == "/tmp/d1/report_co.md"
    assert final["current_step"] == "reporting"


def test_cp_d1_7_retry_coding_loop_path():
    """修复回边路径：execution(retry_coding) → coding → execution（第二次成功）→ reporting。

    execution mock：第一次返回 retry_coding，第二次返回成功；断言 coding 被调用 2 次、
    execution 被调用 2 次，最终到 reporting。
    """
    counts = {"coding": 0, "execution": 0}

    def fake_intake(state):
        return {"paper_meta": {"arxiv_id": "X"}, "current_step": "paper_intake"}

    def fake_analysis(state):
        return {"paper_analysis": {"method_summary": "m"}, "current_step": "paper_analysis"}

    def fake_scout(state):
        return {"current_step": "resource_scout"}

    def fake_planning(state):
        return {
            "reproduction_plan": {"plan_summary": "p", "approved": True},
            "execution_mode": ExecutionMode.FULL,
            "current_step": "planning",
        }

    def fake_coding(state):
        counts["coding"] += 1
        # 修复回合：清掉上一轮的 retry_coding 路由意图（模拟 coding 不写 _dev_loop_route，
        # 由 execution 决定下一步）。coding 真实现也不写 _dev_loop_route。
        return {"code_output_dir": "/tmp/d1/loop", "current_step": "coding"}

    def fake_execution(state):
        counts["execution"] += 1
        if counts["execution"] == 1:
            # 第一次：可修复失败 → 回 coding。
            return {
                "execution_result": {"success": False, "metrics": {}},
                "_dev_loop_route": "retry_coding",
                "fix_loop_count": 1,
                "current_step": "execution",
            }
        # 第二次：成功 → reporting。
        return {
            "execution_result": {"success": True, "metrics": {"acc": 0.9}},
            "_dev_loop_route": None,
            "current_step": "execution",
        }

    def fake_reporting(state):
        return {"report_path": "/tmp/d1/loop_report.md", "current_step": "reporting"}

    with patch.object(graph_module, "paper_intake", fake_intake), patch.object(
        graph_module, "paper_analysis", fake_analysis
    ), patch.object(graph_module, "resource_scout", fake_scout), patch.object(
        graph_module, "planning", fake_planning
    ), patch.object(graph_module, "coding", fake_coding), patch.object(
        graph_module, "execution", fake_execution
    ), patch.object(graph_module, "reporting", fake_reporting):
        g = build_graph(checkpointer=MemorySaver())
        final = g.invoke(
            {
                "user_input": "X",
                "node_errors": [],
                "degraded_nodes": [],
                "messages": [],
                "retry_budget_remaining": 50,
            },
            {"configurable": {"thread_id": "d1-retry-loop"}},
        )

    assert counts["coding"] == 2, f"coding 应被调用 2 次（修复回边），实际 {counts['coding']}"
    assert counts["execution"] == 2, f"execution 应被调用 2 次，实际 {counts['execution']}"
    assert final["report_path"] == "/tmp/d1/loop_report.md"
    assert final["current_step"] == "reporting"


# ===========================================================================
# 路由函数签名守护（只读 state，单参）
# ===========================================================================


def test_route_functions_single_param_read_only():
    """三个路由函数均为单参 (state) -> str（只读，不写 state；零 reducer 红线相关）。"""
    for fn in (_route_after_coding, _route_after_execution, _route_after_planning):
        sig = inspect.signature(fn)
        params = list(sig.parameters)
        assert params == ["state"], f"{fn.__name__} 应为单参 (state)，实际 {params}"
