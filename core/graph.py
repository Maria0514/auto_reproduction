"""LangGraph 主图构建。

注册全部 7 个节点，建立边连接（Sprint 3：节点集合与名称严格不变，AC-S3-10）。

节点形态（Sprint 3 升级后，全部为真实业务逻辑，无 pass-through 占位）：
    - ``paper_intake`` / ``paper_analysis`` / ``resource_scout``：ReAct wrapper 函数
      （由 ``core.react_base._make_react_wrapper`` 生成）；
    - ``planning``：手写复合节点（内部 ReAct 子图 + ``interrupt()`` 人在回路 + 5 类决策
      路由），见 ``core/nodes/planning.py``；interrupt payload 含 ``interrupt_kind="planning"``；
    - ``coding``：ReAct wrapper 真实现（写代码 / 回读论文 / 修复回合反馈注入），
      见 ``core/nodes/coding.py``；
    - ``execution``：手写复合节点真实现（sandbox 执行 + 错误分类 + B 档判定 + 修复循环
      边界 + interrupt#2 dev_loop_failure），见 ``core/nodes/execution.py``；
    - ``reporting``：纯函数三形态 Markdown 报告（full_success / code_only / degraded），
      见 ``core/nodes/reporting.py``。

边结构（Sprint 3）：
    - START -> paper_intake -> paper_analysis -> resource_scout -> planning（顺序边）；
    - planning 之后 **3 路条件边**（``_route_after_planning``，sp2 既有，零改动）：
        approve / code_only -> ``coding``（next，区分点后移到 coding 出边）；
        revise / switch_repo -> ``planning`` 自环（self，无次数硬上限）；
        cancel -> ``END``（end，PRD §2.3 / AC-S2-13）；
    - coding 之后 **2 路条件边**（``_route_after_coding``，sp3 新增，替换原顺序边）：
        FULL -> ``execution``（to_execution，进入执行 + 修复循环）；
        CODE_ONLY -> ``reporting``（skip_execution，跳过 execution + 修复循环，AC-S3-06）；
    - execution 之后 **条件边**（``_route_after_execution``，sp3 新增，替换原顺序边）：
        见该函数 docstring（修复回边 self-loop / 回 coding / interrupt#2 三态 / 降级·成功）；
    - reporting -> END。

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

# paper_intake / paper_analysis / resource_scout / coding 模块级导出的是 _make_react_wrapper
# 生成的 wrapper 函数；planning / execution 是手写复合节点；reporting 是纯函数。签名均兼容
# (GlobalState) -> dict，可直接注册到主图。Sprint 3：coding/execution/reporting 已从 sp2
# pass-through 占位升级为真实业务逻辑（架构 §2.5.6，AC-S3-10 节点集合不变）。
from core.nodes.coding import coding
from core.nodes.execution import execution
from core.nodes.paper_analysis import paper_analysis
from core.nodes.paper_intake import paper_intake
from core.nodes.planning import planning
from core.nodes.reporting import reporting
from core.nodes.resource_scout import resource_scout
from core.state import ExecutionMode, GlobalState

logger = logging.getLogger(__name__)


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


# ========== coding 出边路由（2 路条件边，架构 §2.5.5） ==========


def _route_after_coding(state: GlobalState) -> str:
    """coding 节点出边路由（2 路）：按 ``execution_mode`` 区分 FULL / CODE_ONLY。

    code_only 区分点后移到 coding 出边（不在 planning 出边区分，sp2 ``_route_after_planning``
    三路条件边零改动，AC-S3-10）。coding 节点本身无论 FULL/CODE_ONLY 都正常产代码，不判 mode。

    判定规则：
        - ``execution_mode == CODE_ONLY`` -> ``"skip_execution"``（→ reporting，跳过
          execution + 修复循环，AC-S3-06）；
        - 否则（FULL / 缺省）            -> ``"to_execution"``（→ execution，进入执行 + 修复循环）。

    兼容 ``ExecutionMode`` Enum 与 str 两种取值（与 ``reporting._is_code_only`` 同范式）。
    """
    if _is_code_only_mode(state):
        return "skip_execution"
    return "to_execution"


def _is_code_only_mode(state: GlobalState) -> bool:
    """判定 execution_mode 是否 CODE_ONLY（兼容 Enum 与 str，照搬 reporting 范式）。"""
    mode = state.get("execution_mode")
    if mode is None:
        return False
    if isinstance(mode, ExecutionMode):
        return mode == ExecutionMode.CODE_ONLY
    return str(mode) == ExecutionMode.CODE_ONLY.value or str(mode) == "code_only"


# ========== execution 出边路由（条件边，架构 §2.5.3 + C3 交接契约） ==========


def _route_after_execution(state: GlobalState) -> str:
    """execution 节点出边路由。判定基于 execution 返回后写入 state 的字段（只读不写）。

    **权威输入契约 = `core/nodes/execution.py` 实际返回字段**（非架构 §2.5.3 字面，
    后者遗漏了 ``await_dev_loop_interrupt`` self-loop 一路）。execution 各出口写入的字段：
        - 修复回合（可修复 + 未触顶 + 预算够）：``_dev_loop_route="retry_coding"``；
        - 需 interrupt#2 但 sandbox 结果尚未过 checkpoint 边界（首次失败回合）：
          ``_dev_loop_route="await_dev_loop_interrupt"``（commit 边界 return，**尚未** interrupt）；
        - interrupt#2 resume 三态：``user_fix_decision`` ∈ {terminate, revise_plan, export_code}
          （此时 ``_dev_loop_route`` 已被清为 None）；terminate 另写 ``current_step="cancelled_by_user"``；
        - B 档成功 / 降级：``_dev_loop_route=None``，无 ``user_fix_decision``。

    判定优先级（从上到下；`_dev_loop_route` 两路与 `user_fix_decision` 三态在单次返回中字段互斥，
    优先判 `_dev_loop_route` 保证 self-loop 命门最先命中）：
        1. ``_dev_loop_route == "await_dev_loop_interrupt"`` -> ``"execution"``
           ⚠️ **interrupt#2 的命门（L-C3-01 强交接约束）**：commit 边界 return 后必须 self-loop
           重入 execution，guard 命中跳过 sandbox 后才函数体内 interrupt()。漏接则第二个人在回路
           interrupt 永不触发。架构 §2.5.3 字面方案遗漏此路，以 C3 交接（TODO L214 / dev-plan
           L492）为准。
        2. ``_dev_loop_route == "retry_coding"``         -> ``"coding"``（修复回边，fix_loop_count
           本回合已 +1）；
        3. ``user_fix_decision == "revise_plan"``        -> ``"planning"``（interrupt#2 改计划回流，
           execution 已清 approved=False + 写 _planning_user_feedback + fix_loop_count 清零）；
        4. ``user_fix_decision == "terminate"`` 或 ``current_step == "cancelled_by_user"``
           -> ``"end"``（interrupt#2 终止，checkpoint 保留，复用 sp2 cancel 语义）；
        5. ``user_fix_decision == "export_code"``        -> ``"reporting"``（降级导出）；
        6. 其余（B 档成功 / 降级 / 兜底）                  -> ``"reporting"``。
    """
    route = state.get("_dev_loop_route")
    if route == "await_dev_loop_interrupt":
        return "execution"          # self-loop：commit 边界后重入触发 interrupt#2（命门）
    if route == "retry_coding":
        return "coding"             # 修复回边

    decision = state.get("user_fix_decision")
    if decision == "revise_plan":
        return "planning"
    if decision == "terminate" or state.get("current_step") == "cancelled_by_user":
        return "end"
    if decision == "export_code":
        return "reporting"

    # 兜底：B 档成功 / 降级（_dev_loop_route=None 且无 user_fix_decision）→ 出报告。
    return "reporting"


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

    # 注册节点（Sprint 3：全部真实现，节点集合与名称不变=7，AC-S3-10）：
    # paper_intake / paper_analysis / resource_scout / coding 为 ReAct wrapper 函数；
    # planning / execution 为手写复合节点（内含 interrupt）；reporting 为纯函数三形态报告。
    graph.add_node("paper_intake", paper_intake)        # ReAct wrapper
    graph.add_node("paper_analysis", paper_analysis)    # ReAct wrapper
    graph.add_node("resource_scout", resource_scout)    # ReAct wrapper（Sprint 2 接入）
    graph.add_node("planning", planning)                # 手写 + interrupt#1（Sprint 2 接入）
    graph.add_node("coding", coding)                    # ReAct wrapper 真实现（Sprint 3）
    graph.add_node("execution", execution)              # 手写 + interrupt#2（Sprint 3）
    graph.add_node("reporting", reporting)              # 纯函数三形态报告（Sprint 3）

    # 顺序边：主干 paper_intake -> ... -> planning。
    graph.add_edge(START, "paper_intake")
    graph.add_edge("paper_intake", "paper_analysis")
    graph.add_edge("paper_analysis", "resource_scout")
    graph.add_edge("resource_scout", "planning")
    # planning 之后：3 路条件路由（self / next / end），见 _route_after_planning（sp2 既有，零改动）。
    graph.add_conditional_edges(
        "planning",
        _route_after_planning,
        {
            "self": "planning",   # revise / switch_repo 路径（无次数硬上限）
            "next": "coding",     # approve / code_only 路径（区分点后移到 coding 出边）
            "end": END,           # cancel 路径（PRD §2.3 / AC-S2-13）
        },
    )
    # 【sp3 新增】coding 之后：2 路条件路由（code_only 分流），替换原 coding->execution 顺序边。
    graph.add_conditional_edges(
        "coding",
        _route_after_coding,
        {
            "to_execution": "execution",   # FULL：进入执行 + 修复循环
            "skip_execution": "reporting", # CODE_ONLY：跳过 execution + 修复循环（AC-S3-06）
        },
    )
    # 【sp3 新增】execution 之后：条件路由（修复回边 self-loop / 回 coding / interrupt#2 三态 /
    # 降级·成功），替换原 execution->reporting 顺序边。见 _route_after_execution。
    # "execution": self-loop 是 interrupt#2 命门（await_dev_loop_interrupt commit 边界重入，
    # L-C3-01 强交接约束，漏接则第二个 interrupt 永不触发）。
    graph.add_conditional_edges(
        "execution",
        _route_after_execution,
        {
            "execution": "execution",   # await_dev_loop_interrupt self-loop（interrupt#2 命门）
            "coding": "coding",         # retry_coding 修复回边
            "planning": "planning",     # interrupt#2 revise_plan 回流
            "reporting": "reporting",   # 成功 / 降级 / export_code
            "end": END,                 # interrupt#2 terminate（cancelled_by_user）
        },
    )
    graph.add_edge("reporting", END)

    # 注意（架构 §2.5.2 / §2.5.6）：仍不使用 interrupt_before / interrupt_after，因为
    # interrupt() 在 planning（interrupt#1）/ execution（interrupt#2）节点函数体内部调用，
    # 而非节点边界触发（S-1 spike 已验证）。dev_loop 真 multi-agent 子图改造顺延至 sp4+
    # （TODO「后续 sprint（sp4+）」），sp3 维持单 agent coding↔execution 修复循环。

    compiled = graph.compile(checkpointer=checkpointer)
    logger.info("LangGraph main graph compiled successfully with %d nodes", 7)
    return compiled
