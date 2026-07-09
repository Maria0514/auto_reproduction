"""S3-10 Streamlit 页面 4：执行监控（Sprint 3 任务 E2）。

架构参考：sprint3/architecture.md §2.6.2 / PRD §2.10 页面 4 / dev-plan §E2（CP-E2-1~5）。

页面职责（沿用 sp2 analysis_progress 轮询范式，主线程只读不阻塞工作线程）::

    coding → execution（↔coding 修复循环）→ reporting 阶段的**只读观察页 + 一个 HITL
    决策面板**。autorefresh 每 1.5s rerun → controller.poll_state(thread_id) 拉最新 state →
    渲染进度（修复第 N/3 轮 + 每轮摘要）/ sandbox 实时信息（logs + runtime）/ 错误降级滚动；
    当 execution 节点触发 interrupt#2（修复耗尽 / 不可修复 / 子预算触顶）时，展示
    **dev_loop 失败决策面板**（终止 / 改计划 / 导出代码），用户决策经 resume_with 注入恢复。

页面入口约定（沿用 D3/D4/D5 先例 + E1 _PAGE_MAP 预留）::

    主名 ``render``，模块级别名 ``render_execution_monitor_page = render``，
    ``__all__ = ["render", "render_execution_monitor_page"]``。app.py _PAGE_MAP 用
    ("ui.pages.execution_monitor", "render_execution_monitor_page") 动态加载，
    current_page = config.STREAMLIT_PAGE_EXECUTION（"execution"）。

终态/跳转优先级链（render 顶部早返回，命中即 return；沿用 analysis_progress §2.10 范式）::

    1. get_worker_error 非空        → "工作线程异常" FATAL 卡片 + 返回输入页 + 停轮询；
    2. state 为 None                → 等待 checkpoint 落盘占位 + 继续轮询；
    3. state.error 非空             → FATAL 卡片 + 重试 / 返回输入页 + 停轮询；
    4. current_step=="cancelled_by_user" → "任务已终止" 卡片 + 返回输入页 + 停轮询；
    5. is_interrupted 且 interrupt_kind=="dev_loop_failure" → dev_loop 失败决策面板（停轮询）；
       interrupt_kind=="user_input_request" → 用户输入面板（S4-09，停轮询）；
       （其余，即 planning interrupt → 跳回 review 页）
    6. _should_jump_to_report（reporting 完成且 report_path 非空且非 interrupt）→ 跳结果报告页；
    6bis. current_step=="reporting" 且 report_path 为空 且 controller.is_finished →
       "报告未生成"失败/降级提示卡片 + 停轮询（S5-08 #6 兜底，AC-S5-17：图已跑到 END
       但没有报告可跳，继续轮询是假轮询，永远等不来状态变化）；
    7. 否则正常渲染（进度 + sandbox + 错误降级）+ 注册 st_autorefresh（仅此路径注册定时器）。

interrupt#3 resume 契约（S4-09，与 core/tools/interaction_tools.py::request_user_input 严格对齐）::

    payload = {"interrupt_kind": "user_input_request", "question", "is_sensitive",
    "purpose_key"}（architecture §7.1 四键）。UI 渲染 question + 当前阶段一句上下文 +
    **单输入框**（is_sensitive=True → type="password"）+ 敏感时「记住此凭证」勾选
    （默认不勾），提交 resume_with(thread_id, {"value": str, "remember": bool})。
    **非空校验（L-B1-01 防线）**：value.strip() 为空时拒绝提交（不调 resume_with），
    防止空值经工具端去重/降级路径卡死任务。

    S5-01 增量（T-S5-2-3，architecture §6 Q-S5-10）：payload 含第 5 键
    ``allow_degrade=True``（**只由 coding 前置 gate 设置**，agent 经 request_user_input
    工具产生的 payload 永不含该键——红线的 UI 面）时，额外渲染显式按钮
    「无此凭证，降级为模拟实验」，点击 → resume_with(thread_id,
    {"value": "", "remember": False, "degrade": True})（resume 三键契约，一次点击只降
    当前询问项）。无该键（老 payload / agent 路径）→ 无按钮；普通提交仍为两键契约
    （degrade 缺省 False），与 sp4 完全一致。

interrupt#2 resume 契约（与 core/nodes/execution.py::_route_user_fix_decision 严格对齐）::

    resume payload 必须是 dict 且含 "decision" 键，取值 ∈ {"terminate", "revise_plan",
    "export_code"}（未知值 / 非法 payload 节点端兜底为 terminate）。"revise_plan" 额外读
    decision.get("user_feedback")（缺失时节点端兜底为空串，不崩）。本页三个按钮分别注入：
        - 终止任务   → {"decision": "terminate"}
        - 改计划     → {"decision": "revise_plan", "user_feedback": <文本框内容>}
        - 导出代码   → {"decision": "export_code"}

防御式编码：state / payload 各字段一律 .get(...) 取默认空值，绝不让 KeyError 崩页面。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import streamlit as st
import streamlit_shadcn_ui as ui
from streamlit_autorefresh import st_autorefresh

from config import (
    ACTIVITY_STREAM_RENDER_TAIL,
    MAX_FIX_LOOP_COUNT,
    STREAMLIT_PAGE_REPORT,
    STREAMLIT_POLL_INTERVAL,
)
# S5-09 术语治理（T-S5-3-5）：内部枚举经 humanize 转用户可读中文再渲染。
from ui.term_map import humanize

logger = logging.getLogger(__name__)

__all__ = ["render", "render_execution_monitor_page"]


# interrupt#2 决策取值（与 core/nodes/execution.py::INTERRUPT_KIND / _route_user_fix_decision
# 严格对齐——这是本页与 execution 节点的硬契约，不得臆造）。
_INTERRUPT_KIND_DEV_LOOP: str = "dev_loop_failure"
# interrupt#3 类型标识（与 core/tools/interaction_tools.py::INTERRUPT_KIND_USER_INPUT
# 严格对齐——沿用 dev_loop 常量先例：页面留本地字符串，单测断言与工具模块一致防漂移）。
_INTERRUPT_KIND_USER_INPUT: str = "user_input_request"
_DECISION_TERMINATE: str = "terminate"
_DECISION_REVISE_PLAN: str = "revise_plan"
_DECISION_EXPORT_CODE: str = "export_code"

# 当前页 current_step 取值（execution 节点写 current_step="execution"，coding 写 "coding"，
# reporting 节点写 "reporting"；cancelled_by_user 为终止终态）。
_STEP_CODING: str = "coding"
_STEP_EXECUTION: str = "execution"
_STEP_REPORTING: str = "reporting"
_STEP_CANCELLED: str = "cancelled_by_user"

_KEY_THREAD_ID = "thread_id"
_KEY_CURRENT_PAGE = "current_page"
# 「改计划」决策的修改意见文本框（原生 st.text_area，AppTest 可见可读）。
_KEY_REVISE_FEEDBACK = "_exec_revise_feedback"
# user_input_request 面板：单输入框 + 「记住」勾选（原生组件，AppTest 可见可点）。
_KEY_USER_INPUT_VALUE = "_exec_user_input_value"
_KEY_USER_INPUT_REMEMBER = "_exec_user_input_remember"

# 阶段中文显示名（coding/execution/reporting + 终止/未知兜底）。
_STEP_DISPLAY: Dict[str, str] = {
    _STEP_CODING: "代码生成（coding）",
    _STEP_EXECUTION: "执行验证（execution）",
    _STEP_REPORTING: "汇总报告（reporting）",
    _STEP_CANCELLED: "已终止",
}

# 日志截断标注文案（CP-E2-4：output_truncated 时展示）。
_TRUNCATED_NOTICE: str = "日志已截断（仅展示尾部，完整日志见 sandbox 工作目录）"


# =========================================================================== #
# 纯函数内核（模块级，可 import 直测；CP-E2-2 / E2-4 / E2-5 逻辑层断言对齐）
# =========================================================================== #
def _safe_int(value: object, default: int = 0) -> int:
    """容错转 int（fix_loop_count 等可能缺失 / 非数）。"""
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _fix_loop_progress_text(fix_loop_count: object) -> str:
    """构造「修复第 N / MAX 轮」进度文案（CP-E2-2，纯函数可直测）。

    fix_loop_count 是 execution 节点回 coding 修复时单点自增的回合数（0 表示尚未进入修复，
    1 表示正在第 1 轮修复）。展示口径：以 1-based 当前轮 / 上限呈现。N==0 时无修复轮次。
    """
    n = _safe_int(fix_loop_count, default=0)
    if n <= 0:
        return f"尚未进入修复循环（上限 {MAX_FIX_LOOP_COUNT} 轮）"
    # n 已是「已发起的修复回合数」，直接作为当前轮显示（封顶到 MAX，防越界文案）。
    cur = min(n, MAX_FIX_LOOP_COUNT)
    return f"修复第 {cur} / {MAX_FIX_LOOP_COUNT} 轮"


def _logs_truncated(execution_result: Optional[Dict[str, Any]]) -> bool:
    """判定 sandbox 日志是否被 output_truncated 护栏截断（CP-E2-4，纯函数）。

    探测两条路径（任一命中即视为截断），不依赖任何未来才提升的契约键：
        1. execution_result 顶层 ``output_truncated`` 真值（ExecutionResult TypedDict 未声明
           该键，但 dict 允许额外键；上游若把 SandboxRunResult.output_truncated 提升至此即兼容）；
        2. logs 文本中含截断标注子串（sandbox / execution 在聚合 logs 时写入的标记）。

    防御式：execution_result 为 None / 非 dict 时返回 False（无结果即无截断）。
    """
    if not isinstance(execution_result, dict):
        return False
    if execution_result.get("output_truncated"):
        return True
    logs = execution_result.get("logs")
    if isinstance(logs, str) and (
        "output_truncated" in logs or "日志已截断" in logs or "[truncated]" in logs
    ):
        return True
    return False


def _should_jump_to_report(
    state: Optional[Dict[str, Any]],
    is_interrupted: bool,
) -> bool:
    """判定是否应自动跳转结果报告页（CP-E2-5，纯函数可直测）。

    条件（全部满足）：
        - 非 interrupt（dev_loop interrupt 决策面板优先，不能在 interrupt 时跳走）；
        - current_step == "reporting"（reporting 节点已执行/产出）；
        - report_path 非空（报告文件路径已写入 state，结果报告页才有内容可渲染）。

    防御式：state 为 None / 非 dict 时返回 False（无 state 不跳）。
    """
    if is_interrupted:
        return False
    if not isinstance(state, dict):
        return False
    if str(state.get("current_step") or "") != _STEP_REPORTING:
        return False
    return bool(state.get("report_path"))


def _is_reporting_without_report(state: Optional[Dict[str, Any]]) -> bool:
    """case⑥bis 状态侧前置判定（S5-08 #6 兜底 / AC-S5-17，纯函数可直测）。

    current_step == "reporting" 且 report_path 为空（None / 空串）→ True。
    render() 中须再与 ``controller.is_finished(thread_id)`` 合取：图仍在跑（reporting
    节点尚未写完 report_path）时继续轮询是正当的，只有图已到 END 才判"报告未生成"。

    防御式：state 为 None / 非 dict 时返回 False（无 state 不判）。
    """
    if not isinstance(state, dict):
        return False
    if str(state.get("current_step") or "") != _STEP_REPORTING:
        return False
    return not state.get("report_path")


def _build_decision_payload(
    decision: str,
    user_feedback: str = "",
) -> Dict[str, Any]:
    """构造 interrupt#2 resume payload（与 execution.py::_route_user_fix_decision 严格对齐）。

    - terminate / export_code → {"decision": <kind>}（节点端只读 decision["decision"]）；
    - revise_plan            → {"decision": "revise_plan", "user_feedback": <文本>}
      （节点端 decision.get("user_feedback") 取修复失败上下文反馈；缺失则空串兜底）。
    """
    payload: Dict[str, Any] = {"decision": decision}
    if decision == _DECISION_REVISE_PLAN:
        payload["user_feedback"] = user_feedback or ""
    return payload


def _is_valid_user_input(value: object) -> bool:
    """interrupt#3 提交前非空校验（L-B1-01 防线，纯函数可直测）。

    空值 / 纯空白 / 非 str 一律拒绝——空 value 若放行，工具端会以空串继续（resume 契约
    的降级路径），既可能把空凭证 remember 进 .secrets 污染去重，也让 agent 拿空值卡死。
    """
    return bool(isinstance(value, str) and value.strip())


def _build_user_input_resume(value: str, remember: bool) -> Dict[str, Any]:
    """构造 interrupt#3 resume payload（与 interaction_tools.request_user_input 严格对齐）。

    两键契约（architecture §7.1）：{"value": str, "remember": bool}。value 原样透传
    （不 strip——凭证内容以用户所见为准），remember 强制 bool。
    """
    return {"value": value, "remember": bool(remember)}


def _build_degrade_resume() -> Dict[str, Any]:
    """构造显式降级 resume payload（S5-01 / Q-S5-10 三键契约，纯函数可直测）。

    三键契约：{"value": "", "remember": False, "degrade": True}——coding 前置 gate
    （确定性代码）解读 degrade=True → 将当前 purpose_key 写入 credential_degradations
    并放行。value/remember 固定空值/False，不受输入框与勾选状态影响（一次点击只降
    当前询问项；多缺失项由 gate 串行逐项弹出）。
    """
    return {"value": "", "remember": False, "degrade": True}


def _summarize_fix_history(fix_loop_history: object) -> List[Dict[str, str]]:
    """把 fix_loop_history 规整为每轮摘要行（CP-E2-2，纯函数；防御式跳过非 dict 项）。

    每行：轮次 / 错了什么（error_summary + error_category）/ 修复策略（fix_strategy）。
    与 core/state.py::FixLoopRecord 字段对齐（round_number / error_summary /
    error_category / fix_strategy / timestamp）。
    """
    out: List[Dict[str, str]] = []
    for rec in fix_loop_history or []:
        if not isinstance(rec, dict):
            continue
        out.append(
            {
                "round": str(rec.get("round_number") or "?"),
                "error_summary": str(rec.get("error_summary") or "(无摘要)"),
                "error_category": str(rec.get("error_category") or ""),
                "fix_strategy": str(rec.get("fix_strategy") or "(未记录修复策略)"),
            }
        )
    return out


def _parse_node_error(err: object) -> Dict[str, str]:
    """解析单条 node_error，抽出 [error_category=...] 前缀做一句话摘要（纯函数）。

    execution 节点把执行细分类写进 NodeError.error_message 的 ``[error_category=xxx]`` 前缀
    （core/nodes/execution.py L605）。本函数抽出该分类 + 剥离前缀后的纯摘要，便于 UI 滚动展示。
    err 非 dict 时降级为 {"node": "?", "category": "", "summary": str(err)}。
    """
    if not isinstance(err, dict):
        return {"node": "?", "type": "", "category": "", "summary": str(err), "detail": ""}

    node = str(err.get("node_name") or "?")
    etype = str(err.get("error_type") or "")
    raw_msg = str(err.get("error_message") or "")
    detail = str(err.get("error_detail") or "")

    category = ""
    summary = raw_msg
    marker = "[error_category="
    idx = raw_msg.find(marker)
    if idx != -1:
        start = idx + len(marker)
        end = raw_msg.find("]", start)
        if end != -1:
            category = raw_msg[start:end].strip()
            summary = raw_msg[end + 1:].strip() or raw_msg
    return {
        "node": node,
        "type": etype,
        "category": category,
        "summary": summary,
        "detail": detail,
    }


# =========================================================================== #
# 私有渲染区块
# =========================================================================== #
def _get_controller():
    """从 session_state 取 GraphController 单例（与 app.py::_get_controller 一致）。"""
    from app import _get_controller as _app_get_controller

    return _app_get_controller()


def _init_page_state() -> None:
    """初始化本页 session_state 字段（不覆盖已有值）。"""
    st.session_state.setdefault(_KEY_THREAD_ID, None)
    st.session_state.setdefault(_KEY_CURRENT_PAGE, "execution")
    st.session_state.setdefault(_KEY_REVISE_FEEDBACK, "")
    st.session_state.setdefault(_KEY_USER_INPUT_VALUE, "")
    st.session_state.setdefault(_KEY_USER_INPUT_REMEMBER, False)


def _reset_to_input_page() -> None:
    """切回输入页并解除提交锁，使输入页控件恢复可交互（沿用 analysis_progress 范式）。"""
    st.session_state[_KEY_CURRENT_PAGE] = "input"
    st.session_state["_input_submitted"] = False


def _render_back_to_input_button(key: str, label: str = "返回输入页") -> None:
    """通用"返回输入页"按钮：清提交标记 + 切回 input 页（沿用 analysis_progress 范式）。"""
    if ui.button(text=label, key=key, variant="outline"):
        _reset_to_input_page()
        st.rerun()


def _render_fatal_worker_error(exc: Exception) -> None:
    """case①：工作线程异常 FATAL 卡片（含 str(exc)）+ 返回输入页（停轮询）。

    关键文案用原生 st.error（AppTest 可断言）；shadcn ui.alert 文本在 iframe 内 AppTest
    看不到（sp2 实证），故核心终态文案一律走原生组件，保留可测性。
    """
    st.error("工作线程异常：复现任务在后台线程崩溃，已停止。")
    st.code(str(exc))
    _render_back_to_input_button(key="btn_exec_worker_error_back")


def _render_fatal_state_error(error_msg: str) -> None:
    """case③：state.error FATAL 卡片 + 重试 / 返回输入页（停轮询）。"""
    st.error(f"任务发生致命错误：{error_msg}")
    cols = st.columns(2)
    with cols[0]:
        if ui.button(text="重试", key="btn_exec_retry", variant="default"):
            _reset_to_input_page()
            st.rerun()
    with cols[1]:
        _render_back_to_input_button(key="btn_exec_error_back")


def _render_cancelled_card() -> None:
    """case④：任务已终止卡片 + 返回输入页（停轮询，沿用 AC-S2-13 范式）。"""
    st.warning(
        "任务已终止：本次复现任务已终止（用户在决策面板选择终止 / 计划审核页主动取消）。"
        "checkpoint 已保留供后续查询。"
    )
    _render_back_to_input_button(key="btn_exec_cancelled_back", label="返回输入页开启新任务")


def _render_progress(state: Dict[str, Any]) -> None:
    """进度展示：current_step 阶段 + 修复第 N/3 轮 + fix_loop_history 每轮摘要（CP-E2-2）。"""
    current_step = str(state.get("current_step") or "start")
    fix_loop_count = state.get("fix_loop_count", 0)

    st.markdown("### ⚙️ 执行进度")

    # S5-09：未知/上游 step 兜底经 humanize（不裸露内部值；"start" 等内部标记
    # 会得到 "start（内部标识）" 兜底文案——不崩不静默）。
    step_display = _STEP_DISPLAY.get(current_step) or humanize("node", current_step)
    cols = st.columns(2)
    cols[0].markdown(f"**当前阶段**：{step_display}")
    cols[1].markdown(f"**{_fix_loop_progress_text(fix_loop_count)}**")

    history = _summarize_fix_history(state.get("fix_loop_history"))
    if history:
        st.markdown("**修复历程（每轮：错了什么 + 修复策略）**")
        for row in history:
            # S5-09：error_category 内部枚举经 humanize 转中文再入折叠标题。
            cat = (
                f" [{humanize('error_category', row['error_category'])}]"
                if row["error_category"] else ""
            )
            with st.expander(
                f"第 {row['round']} 轮{cat} · {row['error_summary']}", expanded=False
            ):
                st.markdown(f"**错误摘要**：{row['error_summary']}")
                st.markdown(f"**修复策略**：{row['fix_strategy']}")


def _render_sandbox_info(state: Dict[str, Any]) -> None:
    """sandbox 实时信息：logs（受 output_truncated 护栏标注）+ runtime_seconds（CP-E2-4）。"""
    exec_result = state.get("execution_result")

    st.markdown("### 🖥️ Sandbox 执行信息")

    if not isinstance(exec_result, dict):
        st.caption("尚无执行结果（execution 节点尚未跑完 sandbox）。")
        return

    runtime = exec_result.get("runtime_seconds")
    success = exec_result.get("success")
    cols = st.columns(2)
    if runtime is not None:
        try:
            cols[0].markdown(f"**运行耗时**：{float(runtime):.2f} 秒")
        except (TypeError, ValueError):
            cols[0].markdown(f"**运行耗时**：{runtime}")
    status_txt = "成功" if success else "失败 / 进行中"
    cols[1].markdown(f"**执行状态**：{status_txt}")

    # CP-E2-4：output_truncated 护栏命中 → 明确标注日志已截断（原生 st.warning，AppTest 可断言）。
    if _logs_truncated(exec_result):
        st.warning(f"日志已截断：{_TRUNCATED_NOTICE}")

    logs = exec_result.get("logs")
    if logs:
        with st.expander("Sandbox 日志（尾部）", expanded=False):
            st.code(str(logs))
    else:
        st.caption("暂无日志输出。")


def _render_errors_and_degraded(state: Dict[str, Any]) -> None:
    """错误/降级：滚动展示 node_errors（解析 [error_category=...] 前缀）/ degraded_nodes（CP-E2 §3）。"""
    node_errors = state.get("node_errors") or []
    degraded_nodes = state.get("degraded_nodes") or []

    st.markdown("### ⚠️ 错误与降级")

    if degraded_nodes:
        # 原生 st.warning（AppTest 可断言「降级节点」文案）。
        # S5-09：节点名中文化 + 括注内部名（与 _STEP_DISPLAY "代码生成（coding）"
        # 既有口径一致；内部名保留作测试/排障锚点）。
        st.warning(
            "降级节点："
            + ", ".join(f"{humanize('node', str(n))}（{n}）" for n in degraded_nodes)
        )

    if not node_errors:
        st.caption("暂无错误记录。")
        return

    # 滚动展示最近 10 条（一句话摘要 + 可展开详情，沿用 analysis_progress accordion 范式）。
    items: List[Dict[str, str]] = []
    for idx, err in enumerate(node_errors[-10:]):
        parsed = _parse_node_error(err)
        # S5-09：节点名中文化 + 括注内部名（"?" 占位符不经表）；error_category
        # 经 humanize；error_type 为三态机器标识（transient/permanent/fatal，
        # 非 §7.9 列举 domain），保留原值作排障锚点。
        node_disp = (
            f"{humanize('node', parsed['node'])}（{parsed['node']}）"
            if parsed["node"] != "?" else "?"
        )
        cat_part = (
            f" [{humanize('error_category', parsed['category'])}]"
            if parsed["category"] else ""
        )
        type_part = f" ({parsed['type']})" if parsed["type"] else ""
        trigger = f"⚠️ {idx + 1}. {node_disp}{cat_part}{type_part} · {parsed['summary']}"
        if parsed["detail"]:
            content = f"**摘要**：{parsed['summary']}\n\n```\n{parsed['detail']}\n```"
        else:
            content = f"**摘要**：{parsed['summary']}\n\n_(无 error_detail)_"
        items.append({"trigger": trigger, "content": content})

    if items:
        ui.accordion(data=items, key="acc_exec_errors")
    else:
        st.caption("暂无错误记录。")


def _render_artifact_paths_section(state: Dict[str, Any]) -> None:
    """S5-11 产物路径只读展示区（T-S5-3-6 / AC-S5-21，architecture §7.11）。

    ``st.code`` 自带一键复制按钮（零新组件、零新依赖）。防御式 ``.get``：字段缺失
    （如 coding 未完成时 code_output_dir 为空 / 报告尚未产出时 report_path 为空）
    渲染占位 caption，不炸页面。只读展示——不做打开目录 / 导出打包 / 文件浏览器
    （PRD 非目标），零 state 变更。仅 case⑦ 正常渲染路径调用（独立展示区，不进
    任何 interrupt 面板 / 终态卡片）。
    """
    state = state or {}
    st.markdown("### 📋 产物路径（可复制）")
    code_dir = state.get("code_output_dir")
    if code_dir:
        st.caption("代码目录（code_output_dir）")
        st.code(str(code_dir))
    else:
        st.caption("代码目录（code_output_dir）：（尚未生成）")
    report_path = state.get("report_path")
    if report_path:
        st.caption("报告文件（report_path）")
        st.code(str(report_path))
    else:
        st.caption("报告文件（report_path）：（尚未生成）")


def _render_report_missing_card(state: Dict[str, Any]) -> None:
    """case⑥bis：任务已结束但未产出报告 → 明确失败/降级提示卡片（AC-S5-17，停假轮询）。

    关键文案用原生 st.error（AppTest 可断言，沿用本页终态卡片先例）。调用方**不注册
    autorefresh**——图已到 END，state 不会再变化，继续轮询是假轮询。
    附带错误/降级上下文（复用 _render_errors_and_degraded）帮助用户定位失败原因。
    """
    st.title("论文自动复现 — 执行监控")
    st.error(
        "报告未生成：任务已结束，但未产出报告文件（report_path 为空）。"
        "本次复现可能失败或降级结束，请查看下方错误与降级信息。"
    )
    _render_errors_and_degraded(state)
    _render_back_to_input_button(
        key="btn_exec_no_report_back", label="返回输入页开启新任务"
    )


def _submit_dev_loop_decision(
    controller,
    thread_id: str,
    decision: str,
    user_feedback: str = "",
) -> None:
    """提交 dev_loop 失败决策：构造 payload → resume_with → st.rerun()（CP-E2-3 核心写路径）。

    payload 由 _build_decision_payload 构造，key/取值与 execution.py::_route_user_fix_decision
    严格对齐。resume_with 异步起后台 worker，提交后 st.rerun() 让本页轮询自愈直到状态转移
    （沿用 plan_review 决策提交后范式）。
    """
    payload = _build_decision_payload(decision, user_feedback)
    logger.info(
        "[execution_monitor] 提交 dev_loop 决策 thread=%s decision=%s", thread_id, decision
    )
    controller.resume_with(thread_id, payload)
    st.rerun()


def _render_dev_loop_decision_panel(
    controller,
    thread_id: str,
    payload: Optional[Dict[str, Any]],
) -> None:
    """dev_loop 失败决策面板（承载 interrupt#2，本任务重点，CP-E2-3）。

    展示失败上下文摘要（payload 取 fix_loop_history / execution_errors|execution_result.errors）
    + 三个原生按钮（AppTest 可见可点）：终止任务 / 改计划（配 user_feedback 文本框）/ 导出代码。
    """
    payload = payload or {}

    st.title("论文自动复现 — 执行失败决策")
    st.error(
        "自动修复未通过，需要你决策：execution 修复循环已耗尽自动重试 / "
        "遇到不可自动修复的错误，请在下方三种处置中选择其一。"
    )

    # --- 失败上下文摘要 ---
    with st.container(border=True):
        st.markdown("### 📌 失败上下文")
        fix_count = _safe_int(payload.get("fix_loop_count"), default=0)
        st.markdown(
            f"**已尝试修复回合数**：{fix_count} / {MAX_FIX_LOOP_COUNT}"
            f"（{_fix_loop_progress_text(fix_count)}）"
        )
        category = payload.get("error_category")
        if category:
            # S5-09：error_category 内部枚举经 humanize 转中文。
            st.markdown(f"**最近错误分类**：{humanize('error_category', category)}")
        error_summary = payload.get("error_summary")
        if error_summary:
            st.markdown(f"**最近错误摘要**：{error_summary}")
        fix_hint = payload.get("fix_hint")
        if fix_hint:
            st.markdown(f"**修复建议（曾尝试）**：{fix_hint}")

        # execution 错误清单：payload 键为 execution_errors（execution.py L699），
        # 兜底兼容 execution_result.errors（任务描述措辞）。
        exec_errors = payload.get("execution_errors")
        if not exec_errors:
            exec_result = payload.get("execution_result") or {}
            exec_errors = exec_result.get("errors") if isinstance(exec_result, dict) else None
        if exec_errors:
            st.markdown("**执行错误**")
            for e in exec_errors:
                st.markdown(f"- {e}")

        rep_stderr = payload.get("representative_stderr")
        if rep_stderr:
            with st.expander("代表性 stderr 片段", expanded=False):
                st.code(str(rep_stderr))

    # --- 修复历程（每轮：错了什么 + 修复策略）---
    history = _summarize_fix_history(payload.get("fix_loop_history"))
    if history:
        st.markdown("### 🔁 修复历程")
        for row in history:
            # S5-09：error_category 内部枚举经 humanize 转中文再入折叠标题。
            cat = (
                f" [{humanize('error_category', row['error_category'])}]"
                if row["error_category"] else ""
            )
            with st.expander(
                f"第 {row['round']} 轮{cat} · {row['error_summary']}", expanded=False
            ):
                st.markdown(f"**错误摘要**：{row['error_summary']}")
                st.markdown(f"**修复策略**：{row['fix_strategy']}")

    # --- 三个决策按钮（原生 st.button，AppTest 可见可点；CP-E2-3 捕获 resume_with）---
    st.markdown("### 🎯 决策")
    cols = st.columns(2)
    with cols[0]:
        # 终止任务 → {"decision": "terminate"}
        if st.button(
            "⛔ 终止任务", key="btn_dev_loop_terminate", use_container_width=True
        ):
            _submit_dev_loop_decision(controller, thread_id, _DECISION_TERMINATE)
    with cols[1]:
        # 导出代码 → {"decision": "export_code"}（降级导出已生成代码）
        if st.button(
            "📄 导出代码", key="btn_dev_loop_export_code", use_container_width=True
        ):
            _submit_dev_loop_decision(controller, thread_id, _DECISION_EXPORT_CODE)

    # --- 改计划 → {"decision": "revise_plan", "user_feedback": <文本框内容>} ---
    with st.expander("✏️ 改计划（回 planning 重新规划）", expanded=True):
        st.caption(
            "填写修订方向后回到计划审核，规划模型会结合修复失败上下文重新生成复现计划。"
        )
        feedback = st.text_area(
            "修订意见（user_feedback）",
            key=_KEY_REVISE_FEEDBACK,
            placeholder="例如：换用官方仓库的训练脚本 / 降低 batch size / 跳过缺失数据集的步骤……",
        )
        if st.button("🔁 提交改计划", key="btn_dev_loop_revise_plan"):
            _submit_dev_loop_decision(
                controller, thread_id, _DECISION_REVISE_PLAN, feedback or ""
            )


def _render_user_input_panel(
    controller,
    thread_id: str,
    payload: Optional[Dict[str, Any]],
    current_step: object,
) -> None:
    """interrupt#3 用户输入面板（S4-09 / CP-F1-1~2，Maria 硬约束：就一个输入框）。

    渲染 question + 当前阶段一句上下文 + 单输入框（is_sensitive → password）+
    敏感时「记住此凭证供后续复现复用」勾选（默认不勾）→ 非空校验通过才
    resume_with(thread_id, {"value", "remember"})。

    安全纪律（与 interaction_tools 一致）：logger 只打 purpose_key / is_sensitive，
    绝不打 value / question 全文。
    """
    payload = payload or {}
    question = str(payload.get("question") or "（任务需要你补充一项信息才能继续）")
    is_sensitive = bool(payload.get("is_sensitive"))
    purpose_key = payload.get("purpose_key")

    st.title("论文自动复现 — 需要你补充信息")
    step = str(current_step or "")
    # S5-09：未知 step 兜底经 humanize（不裸露内部值）；空 step 保持"执行中"。
    step_display = _STEP_DISPLAY.get(step) or (
        humanize("node", step) if step else "执行中"
    )
    st.caption(f"当前阶段：{step_display} · 任务已暂停，提交后自动继续。")

    # 问题正文（原生 st.info，AppTest 可断言）。
    st.info(question)

    # --- 单输入框（敏感 → password；Maria 硬约束：无按类型分渲染） ---
    value = st.text_input(
        "你的回答",
        key=_KEY_USER_INPUT_VALUE,
        type="password" if is_sensitive else "default",
    )

    # --- 敏感时「记住」勾选（默认不勾；remember 语义绑定 purpose_key） ---
    remember = False
    if is_sensitive:
        remember = st.checkbox(
            "记住此凭证供后续复现复用",
            key=_KEY_USER_INPUT_REMEMBER,
            value=False,
        )
        if purpose_key:
            st.caption(
                f"勾选后将以 `{purpose_key}` 为键保存到本地凭证存储（0600 权限），"
                "后续任务命中即不再询问。"
            )

    # --- 提交（原生 st.button；非空校验不过 → 拒绝 resume，L-B1-01 防线） ---
    if st.button("提交", key="btn_user_input_submit", use_container_width=True):
        if not _is_valid_user_input(value):
            st.error("输入不能为空：请填写内容后再提交（空值无法恢复任务）。")
            return
        logger.info(
            "[execution_monitor] 提交 user_input resume thread=%s purpose_key=%s "
            "is_sensitive=%s remember=%s",
            thread_id, purpose_key, is_sensitive, bool(remember),
        )
        controller.resume_with(thread_id, _build_user_input_resume(value, remember))
        st.rerun()

    # --- S5-01 显式降级按钮（T-S5-2-3）：仅 gate 发起的 payload 含 allow_degrade=True
    #     时渲染；键缺失（agent 工具路径 / 老 payload）→ 无按钮（红线的 UI 面）。
    #     点击不做非空校验（降级即"无凭证可填"），resume 三键契约见 _build_degrade_resume。---
    if payload.get("allow_degrade") is True:
        st.caption(
            "确实无法提供该凭证？可显式降级：相关实验将以模拟方式进行，"
            "并在最终报告中强制声明。"
        )
        if st.button(
            "无此凭证，降级为模拟实验",
            key="btn_user_input_degrade",
            use_container_width=True,
        ):
            logger.info(
                "[execution_monitor] 提交显式降级 resume thread=%s purpose_key=%s",
                thread_id, purpose_key,
            )
            controller.resume_with(thread_id, _build_degrade_resume())
            st.rerun()


# 活动流空态占位文案（进程重启即失属预期语义——尽力而为可观测性，非错误）。
_ACTIVITY_EMPTY_NOTICE: str = (
    "暂无活动：任务尚未产生 agent 活动事件（进程重启后活动流清空属预期）。"
)


def _render_activity_stream_section(controller, thread_id: str) -> None:
    """S5-07 agent 活动流尾部渲染区（T-S5-4-3 / AC-S5-13，architecture §4）。

    ``controller.get_activity_tail(thread_id, ACTIVITY_STREAM_RENDER_TAIL)`` 返回
    不可变 tuple 快照（T-S5-4-2 只读接口）；事件 ``text`` 已在采集侧完成
    「先 mask 脱敏 → 单行压缩 → 截断」（架构 §9.3 出口①），**渲染侧零再处理**——
    仅按行拼 ``#{seq} [{node}]`` 前缀后 ``st.code`` 等宽块整体输出（seq 天然递增，
    node 内部名保留作排障锚点，等宽日志区口径与 §7.9 非列举 domain 豁免一致）。

    空态（未知 thread / 尚无事件 / 进程重启后 handler 即失）→ 占位 caption 不空白。
    零 state 变更、零新轮询：复用 case⑦ 既有 st_autorefresh 1500ms 节奏，每次
    rerun 重取尾部快照即可。防御式：非 tuple/list 返回值（异常形态）按空态处理。
    """
    st.markdown(f"### 📡 Agent 活动流（最近 {ACTIVITY_STREAM_RENDER_TAIL} 行）")
    events = controller.get_activity_tail(thread_id, ACTIVITY_STREAM_RENDER_TAIL)
    if not isinstance(events, (list, tuple)):
        events = ()
    lines: List[str] = []
    for ev in events:
        if not isinstance(ev, dict):
            continue
        seq = ev.get("seq", "?")
        node = str(ev.get("node") or "-")
        text = str(ev.get("text") or "")
        lines.append(f"#{seq:>4} [{node}] {text}")
    if not lines:
        st.caption(_ACTIVITY_EMPTY_NOTICE)
        return
    st.code("\n".join(lines), language=None)


# =========================================================================== #
# 页面主入口
# =========================================================================== #
def render() -> None:
    """页面主入口（执行监控 + dev_loop 失败决策 HITL）。

    终态/跳转优先级链 + 正常渲染（沿用 analysis_progress §2.10 范式）。
    st_autorefresh(key="execution_poll") 仅在 case⑦ 正常渲染路径注册——终态/决策/跳转
    分支提前 return 即不注册定时器（"停轮询"正确性根基）。
    """
    _init_page_state()

    thread_id = st.session_state.get(_KEY_THREAD_ID)
    if not thread_id:
        # 无 thread_id（未从输入页发起任务）→ 占位提示，不进任何判定。
        st.info("尚未启动任务：请先在输入页填写论文与配置并点击「开始复现」。")
        _render_back_to_input_button(key="btn_exec_no_task_back")
        return

    controller = _get_controller()

    # --- case①：工作线程异常（最致命，最高优先级）→ 停轮询 ---
    worker_error = controller.get_worker_error(thread_id)
    if worker_error is not None:
        _render_fatal_worker_error(worker_error)
        return

    state = controller.poll_state(thread_id)

    # --- case②：state 为 None（snapshot 不存在）→ 等待 checkpoint 落盘占位 + 继续轮询 ---
    if state is None:
        st.info("等待执行启动 / 加载中…：正在等待 checkpoint 落盘，页面将自动刷新。")
        st_autorefresh(interval=STREAMLIT_POLL_INTERVAL, key="execution_poll")
        return

    # --- case③：state.error 非空 → FATAL 卡片 + 重试 / 返回（停轮询） ---
    error_msg = state.get("error")
    if error_msg:
        _render_fatal_state_error(str(error_msg))
        return

    # --- case④：cancelled_by_user → 任务已终止卡片 + 返回（停轮询） ---
    if state.get("current_step") == _STEP_CANCELLED:
        _render_cancelled_card()
        return

    # --- case⑤：interrupt 判定（dev_loop_failure → 决策面板；planning → 跳回 review） ---
    if controller.is_interrupted(thread_id):
        kind = controller.interrupt_kind(thread_id)
        if kind == _INTERRUPT_KIND_DEV_LOOP:
            payload = controller.get_interrupt_payload(thread_id)
            _render_dev_loop_decision_panel(controller, thread_id, payload)
            return  # 决策面板分支不注册 autorefresh（停轮询，等用户决策）
        if kind == _INTERRUPT_KIND_USER_INPUT:
            # interrupt#3（S4-09）：用户输入面板，同页不同面板，同样停轮询等提交。
            payload = controller.get_interrupt_payload(thread_id)
            _render_user_input_panel(
                controller, thread_id, payload, state.get("current_step")
            )
            return
        # planning interrupt（不应在执行监控页出现，但防御性跳回计划审核页）。
        logger.info(
            "[execution_monitor] interrupt_kind=%s 非本页可承载面板，跳回 review 页", kind
        )
        st.session_state[_KEY_CURRENT_PAGE] = "review"
        st.rerun()
        return

    # --- case⑥：reporting 完成且 report_path 非空且非 interrupt → 跳结果报告页 ---
    if _should_jump_to_report(state, is_interrupted=False):
        st.session_state[_KEY_CURRENT_PAGE] = STREAMLIT_PAGE_REPORT
        st.rerun()
        return

    # --- case⑥bis：reporting 但 report_path 为空 ∧ 图已到 END → 失败/降级卡片
    #     （S5-08 #6 兜底 / AC-S5-17，停假轮询）---
    if _is_reporting_without_report(state) and controller.is_finished(thread_id):
        _render_report_missing_card(state)
        return

    # --- case⑦：正常渲染 + 注册 autorefresh（仅此路径注册定时器） ---
    st.title("论文自动复现 — 执行监控")
    st.caption("实时观察代码生成 / 执行验证 / 修复循环进度；页面每 1.5 秒自动刷新。")

    _render_progress(state)
    st.divider()
    _render_sandbox_info(state)
    st.divider()
    _render_errors_and_degraded(state)
    st.divider()
    _render_artifact_paths_section(state)
    st.divider()
    _render_activity_stream_section(controller, thread_id)

    # autorefresh 只能在 case⑦ 注册：终态/决策/跳转分支提前 return 即不注册。
    st_autorefresh(interval=STREAMLIT_POLL_INTERVAL, key="execution_poll")


# app.py _PAGE_MAP 期望 render_execution_monitor_page（沿用 D3/D4/D5 先例 + E1 预留）。
render_execution_monitor_page = render
