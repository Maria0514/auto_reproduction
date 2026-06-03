"""LangGraph 主图构建。

注册全部 7 个节点，建立边连接。

节点形态（Sprint 2 升级后）：
    - ``paper_intake`` / ``paper_analysis`` / ``resource_scout``：ReAct wrapper 函数
      （由 ``core.react_base._make_react_wrapper`` 生成）；
    - ``planning``：手写复合节点（内部 ReAct 子图 + ``interrupt()`` 人在回路 + 5 类决策
      路由），见 ``core/nodes/planning.py``；
    - ``coding`` / ``execution`` / ``reporting``：pass-through 占位实现，返回空 dict
      不修改 ``GlobalState``（Sprint 3 替换为真实业务逻辑 + dev_loop 子图）。

planning 之后是 **3 路条件边**（``_route_after_planning``）：
    - approve / code_only -> ``coding``（next）；
    - revise / switch_repo -> ``planning`` 自环（self，无次数硬上限）；
    - cancel -> ``END``（end，PRD §2.3 / AC-S2-13）。

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

# paper_intake / paper_analysis / resource_scout 模块级导出的是 _make_react_wrapper
# 生成的 wrapper 函数；planning 是手写复合节点。签名均兼容 (GlobalState) -> dict，
# 可直接注册到主图。
from core.nodes.paper_analysis import paper_analysis
from core.nodes.paper_intake import paper_intake
from core.nodes.planning import planning
from core.nodes.resource_scout import resource_scout
from core.state import GlobalState

logger = logging.getLogger(__name__)


# ========== 占位节点 ==========


def _passthrough(state: GlobalState) -> dict:
    """通用占位节点：返回空字典，不修改任何状态字段。

    LangGraph 合并语义下，空 dict 不会触发任何状态字段更新，确保占位节点对上游
    写入的数据完全透明。后续 Sprint 将以此模板逐步替换为真实业务逻辑。
    """
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


# ========== planning 出边路由（3 路条件边，架构 §2.5.1） ==========


def _route_after_planning(state: GlobalState) -> str:
    """planning 节点出边路由（3 路）。

    判定规则（优先级从上到下）：
        1. current_step == "cancelled_by_user"  -> "end"   （cancel 决策，AC-S2-13）
        2. reproduction_plan.approved == True    -> "next"  （approve / code_only）
        3. 其它情况（含 revise / switch_repo）    -> "self"  （自环重入 planning）

    revise / switch_repo 路径无次数硬上限（Q-S2-03 RESOLVED）：由 interrupt 暂停 +
    MAX_TOTAL_LLM_CALLS 总预算 + cancel 主动出口三重自然兜底。
    """
    if state.get("current_step") == "cancelled_by_user":
        return "end"
    plan = state.get("reproduction_plan") or {}
    return "next" if plan.get("approved") else "self"


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

    # 注册节点：paper_intake / paper_analysis / resource_scout 为 ReAct wrapper 函数；
    # planning 为手写复合节点（内含 interrupt）；coding / execution / reporting 仍占位。
    graph.add_node("paper_intake", paper_intake)        # ReAct wrapper
    graph.add_node("paper_analysis", paper_analysis)    # ReAct wrapper
    graph.add_node("resource_scout", resource_scout)    # ReAct wrapper（Sprint 2 接入）
    graph.add_node("planning", planning)                # 手写 + interrupt（Sprint 2 接入）
    graph.add_node("coding", coding)
    graph.add_node("execution", execution)
    graph.add_node("reporting", reporting)

    # 顺序边：主干 paper_intake -> ... -> planning。
    graph.add_edge(START, "paper_intake")
    graph.add_edge("paper_intake", "paper_analysis")
    graph.add_edge("paper_analysis", "resource_scout")
    graph.add_edge("resource_scout", "planning")
    # planning 之后：3 路条件路由（self / next / end），见 _route_after_planning。
    graph.add_conditional_edges(
        "planning",
        _route_after_planning,
        {
            "self": "planning",   # revise / switch_repo 路径（无次数硬上限）
            "next": "coding",     # approve / code_only 路径
            "end": END,           # cancel 路径（PRD §2.3 / AC-S2-13）
        },
    )
    graph.add_edge("coding", "execution")
    graph.add_edge("execution", "reporting")
    graph.add_edge("reporting", END)

    # 注意（架构 §2.5.2）：Sprint 2 仍不使用 interrupt_before / interrupt_after，因为
    # interrupt() 在 planning 节点函数体内部调用，而非节点边界触发（S-1 spike 已验证）。
    # TODO (Sprint 3): code_only 条件路由（coding -> reporting）+ execution↔coding 修复循环。
    # TODO (Sprint 3): 将 coding / execution 替换为 dev_loop 双 agent 协作子图。

    compiled = graph.compile(checkpointer=checkpointer)
    logger.info("LangGraph main graph compiled successfully with %d nodes", 7)
    return compiled
