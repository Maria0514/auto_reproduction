"""LangGraph 主图构建。

注册全部 7 个节点，建立顺序边连接。

Sprint 1 中 ``paper_intake`` 和 ``paper_analysis`` 以 ReAct wrapper 函数注册（由
``core.react_base._make_react_wrapper`` 生成），其余 5 个节点（resource_scout /
planning / coding / execution / reporting）为 pass-through 占位实现，返回空 dict
不修改 ``GlobalState``。后续 Sprint 将替换为真实业务逻辑并补充条件路由 + dev_loop
子图嵌入（见文末 TODO 注释）。

用法:
    from core.graph import build_graph

    graph = build_graph()
    result = graph.invoke(initial_state, {"configurable": {"thread_id": "task-001"}})
"""

from __future__ import annotations

import logging
from typing import Optional

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph

# paper_intake / paper_analysis 模块级导出的是 _make_react_wrapper 生成的 wrapper 函数，
# 签名兼容 (GlobalState) -> dict，可直接注册到主图。
from core.nodes.paper_analysis import paper_analysis
from core.nodes.paper_intake import paper_intake
from core.state import GlobalState

logger = logging.getLogger(__name__)


# ========== 占位节点 ==========


def _passthrough(state: GlobalState) -> dict:
    """通用占位节点：返回空字典，不修改任何状态字段。

    LangGraph 合并语义下，空 dict 不会触发任何状态字段更新，确保占位节点对上游
    写入的数据完全透明。后续 Sprint 将以此模板逐步替换为真实业务逻辑。
    """
    return {}


def resource_scout(state: GlobalState) -> dict:
    """步骤 3：资源搜集与评估（Sprint 2 实现）。"""
    logger.info("resource_scout: pass-through (Sprint 2)")
    return {}


def planning(state: GlobalState) -> dict:
    """步骤 4：复现规划（Sprint 2 实现）。

    TODO (Sprint 2): 在此节点末尾添加 ``interrupt()`` 调用实现人在回路审核。
    """
    logger.info("planning: pass-through (Sprint 2)")
    # interrupt 占位 -- Sprint 1 不实际中断
    # from langgraph.types import interrupt
    # interrupt("请审核复现计划")
    return {}


def coding(state: GlobalState) -> dict:
    """步骤 5：编码与环境搭建（Sprint 3 实现）。"""
    logger.info("coding: pass-through (Sprint 3)")
    return {}


def execution(state: GlobalState) -> dict:
    """步骤 6：执行与测试验证（Sprint 3 实现）。"""
    logger.info("execution: pass-through (Sprint 3)")
    return {}


def reporting(state: GlobalState) -> dict:
    """步骤 7：报告生成（Sprint 3 实现）。"""
    logger.info("reporting: pass-through (Sprint 3)")
    return {}


# ========== 主图构建 ==========


def build_graph(checkpointer: Optional[SqliteSaver] = None) -> "CompiledGraph":  # noqa: F821
    """构建并编译 LangGraph 主图。

    Args:
        checkpointer: 可选的 SqliteSaver（或任意兼容 BaseCheckpointSaver 的实例，
            例如单测里的 ``MemorySaver``）。若未传入，则懒导入
            ``core.checkpointer.get_checkpointer()`` 取项目默认实例（WAL 模式 SQLite）。
            懒导入是为了避免与 ``core/checkpointer.py`` 形成循环依赖。

    Returns:
        编译后的 LangGraph CompiledGraph 实例（``langgraph.graph.state.CompiledStateGraph``）。
        返回类型采用字符串注解，避免对 LangGraph 内部具体类的硬编码耦合。
    """
    if checkpointer is None:
        # 懒导入：避免 core/graph.py 在 module import 阶段就触碰 SQLite。
        from core.checkpointer import get_checkpointer

        checkpointer = get_checkpointer()

    # 创建 StateGraph，绑定全局状态 schema。
    graph = StateGraph(GlobalState)

    # 注册节点：paper_intake / paper_analysis 为 ReAct wrapper 函数；其余 5 个为占位。
    graph.add_node("paper_intake", paper_intake)        # ReAct wrapper
    graph.add_node("paper_analysis", paper_analysis)    # ReAct wrapper
    graph.add_node("resource_scout", resource_scout)
    graph.add_node("planning", planning)
    graph.add_node("coding", coding)
    graph.add_node("execution", execution)
    graph.add_node("reporting", reporting)

    # 顺序边：Sprint 1 阶段先打通线性主干。
    graph.add_edge(START, "paper_intake")
    graph.add_edge("paper_intake", "paper_analysis")
    graph.add_edge("paper_analysis", "resource_scout")
    graph.add_edge("resource_scout", "planning")
    graph.add_edge("planning", "coding")
    graph.add_edge("coding", "execution")
    graph.add_edge("execution", "reporting")
    graph.add_edge("reporting", END)

    # TODO (Sprint 2): planning 节点末尾添加 interrupt() 实现人在回路审核。
    # TODO (Sprint 3): 在 planning 之后添加条件路由
    #   - 根据 execution_mode 决定 coding -> execution（FULL）或 coding -> reporting（CODE_ONLY）
    #   - execution 之后根据测试结果决定是否回到 coding（修复循环，最多 3 轮）
    # TODO (Sprint 3): 将 coding / execution 替换为 dev_loop 双 agent 协作子图。

    compiled = graph.compile(checkpointer=checkpointer)
    logger.info("LangGraph main graph compiled successfully with %d nodes", 7)
    return compiled
