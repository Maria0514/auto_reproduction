"""D1 - core/graph.py LangGraph 主图骨架自测（Sprint 3 D1 升级后同步更新）。

覆盖主图骨架检查点，随 Sprint 3 D1 升级调整节点形态断言（coding/execution/reporting
从 sp2 pass-through 占位升级为真实业务逻辑）：

1. build_graph() 返回 CompiledGraph 实例
2. 图包含 7 个业务节点（节点名集合一致，AC-S3-10）
3. paper_intake / paper_analysis / resource_scout / coding 节点是 ReAct wrapper 函数；
   planning / execution 是手写复合节点（内含 interrupt，非 wrapper）
4. coding / execution / reporting 已是真实现（**不再返回 {} 占位**；sp3 D1 删除占位函数 +
   _passthrough）—— 此处仅断言它们不是 graph 内定义的占位，行为细节由
   tests/test_sprint3_c1*.py / c2 / c3 覆盖；
5. 可使用 mock checkpointer 编译成功（langgraph.checkpoint.memory.MemorySaver）
6. 全链路可通过 graph.invoke(state, config) 执行 —— 用 unittest.mock.patch 把 5 个真节点
   （paper_intake / paper_analysis / resource_scout / planning + coding/execution/reporting）
   monkey-patch 成返回固定 dict 的函数，避免触发真实 LLM / SDK / sandbox 调用；
   planning fake 返回 approved plan 使 3 路条件边走 next，coding fake FULL → execution。

注：D1 路由（_route_after_coding / _route_after_execution）的完整分支覆盖见
tests/test_sprint3_d1.py（CP-D1-1~7）。本文件保留 sp1/sp2 既有骨架断言并随真实现同步。
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Any, Dict
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph.state import CompiledStateGraph

# 必须用 importlib 拿真实子模块对象 —— core/nodes/__init__.py 把同名 callable 注册为
# 包属性后，普通 `from core.nodes import paper_intake` 拿到的是 callable 而非模块，
# 无法用于 patch 子模块属性（参考 tests/test_paper_intake.py 同样模式）。
paper_intake_module = importlib.import_module("core.nodes.paper_intake")
paper_analysis_module = importlib.import_module("core.nodes.paper_analysis")

from core import graph as graph_module  # noqa: E402
from core.graph import (  # noqa: E402
    build_graph,
    coding,
    execution,
    paper_analysis,
    paper_intake,
    planning,
    reporting,
    resource_scout,
)

EXPECTED_NODES = {
    "paper_intake",
    "paper_analysis",
    "resource_scout",
    "planning",
    "coding",
    "execution",
    "reporting",
}


# ---------- 检查点 1：build_graph 返回 CompiledGraph ----------


def test_build_graph_returns_compiled_graph():
    """build_graph() 应返回 LangGraph CompiledStateGraph 实例。"""
    g = build_graph(checkpointer=MemorySaver())
    assert isinstance(g, CompiledStateGraph), (
        f"期望 CompiledStateGraph，实际 {type(g).__name__}"
    )
    # 二次防御：CompiledGraph 一定暴露 invoke 接口
    assert hasattr(g, "invoke") and callable(g.invoke)


# ---------- 检查点 2：图包含 7 个业务节点 ----------


def test_graph_contains_seven_business_nodes():
    """图应包含 paper_intake、paper_analysis、resource_scout、planning、coding、
    execution、reporting 这 7 个业务节点（不含 LangGraph 内置 __start__/__end__）。
    """
    g = build_graph(checkpointer=MemorySaver())
    all_nodes = set(g.get_graph().nodes.keys())
    business_nodes = {n for n in all_nodes if not n.startswith("__")}
    assert business_nodes == EXPECTED_NODES, (
        f"业务节点集合不匹配：缺 {EXPECTED_NODES - business_nodes}，"
        f"多 {business_nodes - EXPECTED_NODES}"
    )
    assert len(business_nodes) == 7


# ---------- 检查点 3：paper_intake / paper_analysis 是 ReAct wrapper ----------


def test_paper_intake_is_react_wrapper():
    """core.graph.paper_intake 应是 core.nodes.paper_intake.paper_intake（ReAct wrapper）
    本体；ReAct wrapper 的 __name__ 形如 `react_wrapper_<node_name>`。
    """
    assert paper_intake is paper_intake_module.paper_intake
    assert paper_intake.__name__ == "react_wrapper_paper_intake"
    assert callable(paper_intake)


def test_paper_analysis_is_react_wrapper():
    """core.graph.paper_analysis 应是 core.nodes.paper_analysis.paper_analysis
    （ReAct wrapper）本体。
    """
    assert paper_analysis is paper_analysis_module.paper_analysis
    assert paper_analysis.__name__ == "react_wrapper_paper_analysis"
    assert callable(paper_analysis)


def test_resource_scout_is_react_wrapper():
    """Sprint 2 C1：resource_scout 已接入 _make_react_wrapper 生成的 callable。"""
    assert resource_scout.__name__ == "react_wrapper_resource_scout"
    assert callable(resource_scout)


def test_coding_is_react_wrapper():
    """Sprint 3 D1：coding 已是 _make_react_wrapper 生成的 callable（真实现，非占位）。"""
    assert coding.__name__ == "react_wrapper_coding"
    assert callable(coding)


def test_planning_is_handwritten_node():
    """Sprint 2 C1：planning 是手写复合节点（含 interrupt），非 ReAct wrapper。"""
    assert planning.__name__ == "planning"
    assert callable(planning)


def test_execution_is_handwritten_node():
    """Sprint 3 D1：execution 是手写复合节点（含 interrupt#2），非 ReAct wrapper。"""
    assert execution.__name__ == "execution"
    assert execution.__module__ == "core.nodes.execution"
    assert callable(execution)


# ---------- 检查点 4：coding/execution/reporting 已是真实现（占位已删，Sprint 3 D1）----------


def test_coding_execution_reporting_are_real_implementations():
    """Sprint 3 D1：coding/execution/reporting 不再是 graph.py 内定义的 pass-through
    占位，而是从各自真实现模块 import 的对象。断言它们的来源模块正确。

    - coding：core.react_base（_make_react_wrapper 生成的 wrapper）；
    - execution：core.nodes.execution；
    - reporting：core.nodes.reporting。
    行为细节（B 档判定 / 三形态报告 / 修复循环）由 tests/test_sprint3_c*.py 覆盖。
    """
    # coding wrapper 由 react_base 工厂生成，__module__ 指向 react_base，__name__ 携节点名。
    assert coding.__name__ == "react_wrapper_coding"
    assert execution.__module__ == "core.nodes.execution"
    assert reporting.__module__ == "core.nodes.reporting"


def test_passthrough_helper_removed():
    """Sprint 3 D1：占位辅助函数 _passthrough 已从 graph.py 删除（CP-D1-2）。"""
    assert not hasattr(graph_module, "_passthrough")


# ---------- 检查点 5：mock checkpointer 编译成功 ----------


def test_build_graph_with_mock_checkpointer():
    """传入 MemorySaver 应编译成功，不触碰真实 SQLite 文件。"""
    saver = MemorySaver()
    g = build_graph(checkpointer=saver)
    assert isinstance(g, CompiledStateGraph)
    # MemorySaver 实例应被 CompiledGraph 持有
    assert g.checkpointer is saver


def test_build_graph_default_checkpointer_lazy_import():
    """checkpointer=None 时应懒导入 core.checkpointer.get_checkpointer。

    用 patch 阻止真实 SQLite 文件创建，验证 build_graph 内部确实调用了工厂函数。
    """
    fake_saver = MemorySaver()
    with patch("core.checkpointer.get_checkpointer", return_value=fake_saver) as mocked:
        g = build_graph()  # 不传 checkpointer
        mocked.assert_called_once()
        assert isinstance(g, CompiledStateGraph)
        assert g.checkpointer is fake_saver


# ---------- 检查点 6：全链路 invoke 跑通 ----------


def test_full_graph_invoke_with_patched_react_wrappers():
    """端到端 invoke：用 patch 把 7 个真节点全部替换为返回固定 dict 的轻量函数，避免触发
    真实 LLM / SDK / sandbox / interrupt 调用，验证主干顺序边 + planning 3 路条件边
    （approve -> next -> coding）+ sp3 新增 coding/execution 出边连通：
    planning(approve) -> coding -> (FULL) execution -> (成功) reporting -> END。

    - planning fake 返回 approved=True + execution_mode=FULL 的状态，使 _route_after_planning
      走 "next" 进入 coding；
    - coding fake 返回 {}（FULL 模式由 execution_mode 决定 _route_after_coding 走 to_execution）；
    - execution fake 返回 execution_result.success=True + _dev_loop_route=None，使
      _route_after_execution 走 reporting；
    - reporting fake 返回 report_path。
    """

    def fake_paper_intake(state: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "paper_meta": {
                "arxiv_id": "2410.21276",
                "title": "Fake Paper",
                "authors": ["Test Author"],
                "abstract": "",
                "categories": ["cs.AI"],
                "tldr": None,
                "keywords": None,
                "citation_count": None,
                "github_url": None,
                "publish_date": None,
                "pdf_url": None,
            }
        }

    def fake_paper_analysis(state: Dict[str, Any]) -> Dict[str, Any]:
        # 确认 paper_intake 的写入已被合并进 state，供下游观察
        assert state.get("paper_meta", {}).get("arxiv_id") == "2410.21276"
        return {
            "paper_analysis": {
                "method_summary": "fake summary",
                "key_formulas": [],
                "datasets": ["fake-dataset"],
                "metrics": ["accuracy"],
                "hyperparams": {},
                "hardware_requirements": "",
                "framework": None,
                "baseline_results": {},
                "sections_read": ["Method"],
                "analysis_notes": "",
            }
        }

    def fake_resource_scout(state: Dict[str, Any]) -> Dict[str, Any]:
        return {"current_step": "resource_scout"}

    def fake_planning(state: Dict[str, Any]) -> Dict[str, Any]:
        # 返回 approved plan + FULL 模式 -> _route_after_planning 走 "next"（coding），
        # 避免真实 interrupt#1；FULL 使 _route_after_coding 走 to_execution。
        from core.state import ExecutionMode

        return {
            "reproduction_plan": {"plan_summary": "fake plan", "approved": True},
            "execution_mode": ExecutionMode.FULL,
            "current_step": "planning",
        }

    def fake_coding(state: Dict[str, Any]) -> Dict[str, Any]:
        # coding 真实现是 ReAct wrapper（真调 LLM），mock 成轻量返回；不判 mode。
        return {"code_output_dir": "/tmp/fake/code", "current_step": "coding"}

    def fake_execution(state: Dict[str, Any]) -> Dict[str, Any]:
        # 成功路径：_dev_loop_route=None + success=True -> _route_after_execution 走 reporting。
        return {
            "execution_result": {"success": True, "metrics": {"accuracy": 0.9}},
            "_dev_loop_route": None,
            "current_step": "execution",
        }

    def fake_reporting(state: Dict[str, Any]) -> Dict[str, Any]:
        return {"report_path": "/tmp/fake/report.md", "current_step": "reporting"}

    # patch 必须打在 core.graph 命名空间（graph.py 在 module import 阶段已经把节点函数
    # 绑定到自己的全局名字；add_node 注册时存的是那些绑定的引用）。直接 patch 子模块
    # 的属性不会影响已经注册到 StateGraph 内部的引用，所以这里 patch core.graph 命名空间。
    with patch.object(graph_module, "paper_intake", fake_paper_intake), patch.object(
        graph_module, "paper_analysis", fake_paper_analysis
    ), patch.object(graph_module, "resource_scout", fake_resource_scout), patch.object(
        graph_module, "planning", fake_planning
    ), patch.object(graph_module, "coding", fake_coding), patch.object(
        graph_module, "execution", fake_execution
    ), patch.object(graph_module, "reporting", fake_reporting):
        g = build_graph(checkpointer=MemorySaver())
        initial_state: Dict[str, Any] = {
            "user_input": "2410.21276",
            "input_type": "arxiv_id",
            "retry_budget_remaining": 50,
            "node_errors": [],
            "degraded_nodes": [],
            "messages": [],
        }
        final_state = g.invoke(
            initial_state, {"configurable": {"thread_id": "test-d1-full-invoke"}}
        )

    # 全链路执行完毕：上游节点写入的字段都在最终状态中
    assert final_state.get("paper_meta", {}).get("arxiv_id") == "2410.21276"
    assert final_state["paper_meta"]["title"] == "Fake Paper"
    assert final_state.get("paper_analysis", {}).get("method_summary") == "fake summary"
    assert final_state["paper_analysis"]["datasets"] == ["fake-dataset"]
    # planning approve -> coding -> (FULL) execution -> (成功) reporting -> END
    assert final_state.get("reproduction_plan", {}).get("approved") is True
    assert final_state.get("code_output_dir") == "/tmp/fake/code"
    assert final_state.get("execution_result", {}).get("success") is True
    assert final_state.get("report_path") == "/tmp/fake/report.md"
    assert final_state.get("current_step") == "reporting"
    assert final_state["user_input"] == "2410.21276"
