"""D1 - core/graph.py LangGraph 主图骨架自测（Sprint 2 C1 升级后同步更新）。

覆盖 dev-plan.md D1 任务的 6 个自测检查点，并随 Sprint 2 C1 升级调整节点形态断言：

1. build_graph() 返回 CompiledGraph 实例
2. 图包含 7 个业务节点（节点名集合一致）
3. paper_intake / paper_analysis / resource_scout 节点是 ReAct wrapper 函数；
   planning 是手写复合节点（内含 interrupt，非 wrapper）
4. 仅 coding / execution / reporting 三个占位节点返回 {}
   （Sprint 2 起 resource_scout / planning 已接入真节点，见 tests/test_sprint2_b2.py /
   tests/test_sprint2_b3.py / tests/test_sprint2_c1.py）
5. 可使用 mock checkpointer 编译成功（langgraph.checkpoint.memory.MemorySaver）
6. 全链路可通过 graph.invoke(state, config) 执行
   —— 用 unittest.mock.patch 把 4 个真节点 monkey-patch 成返回固定 dict 的函数，
   避免触发真实 LLM / SDK 调用；planning fake 返回 approved plan 使 3 路条件边走 next
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


def test_planning_is_handwritten_node():
    """Sprint 2 C1：planning 是手写复合节点（含 interrupt），非 ReAct wrapper。"""
    assert planning.__name__ == "planning"
    assert callable(planning)


# ---------- 检查点 4：3 个占位节点返回空字典（Sprint 2 起仅 coding/execution/reporting）----------


@pytest.mark.parametrize(
    "placeholder_fn",
    [coding, execution, reporting],
    ids=["coding", "execution", "reporting"],
)
def test_placeholder_nodes_return_empty_dict(placeholder_fn):
    """占位节点接受任意 GlobalState（含空 dict），均应返回空 dict —— LangGraph
    merge 语义下空 dict 不会触发任何状态字段更新。

    注意（Sprint 2 C1）：resource_scout / planning 已升级为真节点，不再属于占位集合，
    其行为分别由 tests/test_sprint2_b2.py / tests/test_sprint2_b3.py 覆盖。
    """
    # 空 state
    assert placeholder_fn({}) == {}

    # 有数据的 state：返回值仍应是空 dict（不污染上游写入）
    state_with_data: Dict[str, Any] = {
        "user_input": "2410.21276",
        "retry_budget_remaining": 50,
        "node_errors": [],
    }
    assert placeholder_fn(state_with_data) == {}


def test_passthrough_helper_returns_empty_dict():
    """内部 _passthrough 通用占位也应返回空 dict。"""
    assert graph_module._passthrough({}) == {}
    assert graph_module._passthrough({"foo": "bar"}) == {}


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
    """端到端 invoke：用 patch 把 4 个真节点（paper_intake / paper_analysis /
    resource_scout / planning）替换为返回固定 dict 的轻量函数，避免触发真实 LLM / SDK /
    interrupt 调用，验证主干顺序边 + planning 3 路条件边（approve -> next -> coding）连通。

    planning fake 返回 approved=True 的 reproduction_plan，使 _route_after_planning 走
    "next" 进入 coding；coding / execution / reporting 占位返回 {} 不修改状态。
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
        # 返回 approved plan -> _route_after_planning 走 "next"（coding），避免真实 interrupt。
        return {
            "reproduction_plan": {"plan_summary": "fake plan", "approved": True},
            "current_step": "planning",
        }

    # patch 必须打在 core.graph 命名空间（graph.py 在 module import 阶段已经把节点函数
    # 绑定到自己的全局名字；add_node 注册时存的是那些绑定的引用）。直接 patch 子模块
    # 的属性不会影响已经注册到 StateGraph 内部的引用，所以这里 patch core.graph 命名空间。
    with patch.object(graph_module, "paper_intake", fake_paper_intake), patch.object(
        graph_module, "paper_analysis", fake_paper_analysis
    ), patch.object(graph_module, "resource_scout", fake_resource_scout), patch.object(
        graph_module, "planning", fake_planning
    ):
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
    # planning approve -> 条件边走 coding -> execution -> reporting -> END
    assert final_state.get("reproduction_plan", {}).get("approved") is True
    # 占位节点没有写入任何字段，原始 user_input 被保留
    assert final_state["user_input"] == "2410.21276"
