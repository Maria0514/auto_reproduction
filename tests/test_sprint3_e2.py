"""Sprint 3 任务 E2 单测：ui/pages/execution_monitor.py 执行监控页（S3-10）。

覆盖 dev-plan §E2 检查点 CP-E2-1 ~ CP-E2-5（dev-plan.md L592-597）。

测试策略（沿用 sp2 plan_review_logic / analysis_progress 范式）：
    - 纯函数直测（_fix_loop_progress_text / _logs_truncated / _should_jump_to_report /
      _build_decision_payload / _summarize_fix_history / _parse_node_error）——CP-E2-2/4/5
      逻辑层断言优先；
    - AppTest + mock GraphController（patch("app._get_controller")）跑真实 render()，断言
      渲染文案 + 原生 st.button 点击捕获 resume_with 实参（CP-E2-2/3/4）。

决策面板用原生 st.button + st.text_area（AppTest 可见可点），故 CP-E2-3 可在 AppTest 内
直接 .click().run() 捕获 resume_with 实参，无需 Playwright。

interrupt#2 resume 契约（与 core/nodes/execution.py::_route_user_fix_decision 对齐）：
    - decision ∈ {"terminate", "revise_plan", "export_code"}；
    - revise_plan 额外带 "user_feedback"。
    本文件 test_cp_e2_3_* 实证三按钮注入的 key/取值与该契约一致。

运行::

    .venv/bin/python -m pytest tests/test_sprint3_e2.py -q
"""

from __future__ import annotations

import importlib
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

import pytest
from streamlit.testing.v1 import AppTest

from config import MAX_FIX_LOOP_COUNT, STREAMLIT_PAGE_REPORT


def _mod():
    """用 importlib 取模块（避免 __init__ 显式 export 遮蔽子模块的已知坑，见 CLAUDE 坑6）。"""
    return importlib.import_module("ui.pages.execution_monitor")


# --------------------------------------------------------------------------- #
# 测试夹具：mock state / payload 工厂
# --------------------------------------------------------------------------- #
def _make_execution_result(
    *,
    success: bool = False,
    logs: str = "[stdout]\nrun ok\n",
    runtime_seconds: float = 12.5,
    errors: Optional[List[str]] = None,
    artifacts: Optional[List[str]] = None,
    output_truncated: Optional[bool] = None,
) -> Dict[str, Any]:
    """构造一份 ExecutionResult 形态 dict（含可选 output_truncated 额外键）。"""
    res: Dict[str, Any] = {
        "success": success,
        "metrics": {} if not success else {"accuracy": 0.91},
        "logs": logs,
        "errors": errors if errors is not None else (["[error_category=runtime] 运行时异常"] if not success else []),
        "artifacts": artifacts if artifacts is not None else [],
        "runtime_seconds": runtime_seconds,
        "environment_info": {},
    }
    if output_truncated is not None:
        res["output_truncated"] = output_truncated
    return res


def _make_fix_history(rounds: int = 2) -> List[Dict[str, Any]]:
    """构造 fix_loop_history（FixLoopRecord 形态，与 core/state.py 对齐）。"""
    return [
        {
            "round_number": i + 1,
            "error_summary": f"第 {i + 1} 轮：import 错误（缺包）",
            "error_category": "import",
            "fix_strategy": f"第 {i + 1} 轮：补充缺失依赖 numpy",
            "timestamp": "2026-06-28T00:00:00Z",
        }
        for i in range(rounds)
    ]


def _make_state(
    *,
    current_step: str = "execution",
    fix_loop_count: int = 1,
    fix_loop_history: Optional[List[Dict[str, Any]]] = None,
    execution_result: Optional[Dict[str, Any]] = None,
    node_errors: Optional[List[Dict[str, Any]]] = None,
    degraded_nodes: Optional[List[str]] = None,
    report_path: Optional[str] = None,
    error: Optional[str] = None,
) -> Dict[str, Any]:
    """构造一份 GlobalState 形态 dict（execution_monitor 页消费字段）。"""
    return {
        "current_step": current_step,
        "fix_loop_count": fix_loop_count,
        "fix_loop_history": fix_loop_history if fix_loop_history is not None else _make_fix_history(),
        "execution_result": execution_result if execution_result is not None else _make_execution_result(),
        "node_errors": node_errors if node_errors is not None else [],
        "degraded_nodes": degraded_nodes if degraded_nodes is not None else [],
        "report_path": report_path,
        "error": error,
        "llm_config_set": {"default": {"base_url": "x", "model": "m", "api_key": "", "temperature": 0.3, "max_tokens": 4096}, "overrides": {}},
    }


def _make_dev_loop_payload(
    *,
    fix_loop_count: int = 3,
    error_category: str = "runtime",
    error_summary: str = "运行时异常（多轮未修复）",
    fix_hint: str = "根据 stderr 尾部定位异常",
    execution_errors: Optional[List[str]] = None,
    fix_loop_history: Optional[List[Dict[str, Any]]] = None,
    representative_stderr: str = "Traceback ... RuntimeError: boom",
) -> Dict[str, Any]:
    """构造 interrupt#2 payload（与 execution.py::_build_dev_loop_interrupt_payload 对齐）。"""
    return {
        "interrupt_kind": "dev_loop_failure",
        "fix_loop_count": fix_loop_count,
        "error_category": error_category,
        "error_summary": error_summary,
        "fix_hint": fix_hint,
        "auto_fixable": True,
        "fix_loop_history": fix_loop_history if fix_loop_history is not None else _make_fix_history(3),
        "execution_errors": execution_errors if execution_errors is not None else ["[error_category=runtime] 运行时异常"],
        "representative_stderr": representative_stderr,
        "options": ["terminate", "revise_plan", "export_code"],
    }


def _make_controller_mock(
    *,
    state: Optional[Dict[str, Any]] = None,
    is_interrupted: bool = False,
    interrupt_kind: Optional[str] = None,
    interrupt_payload: Optional[Dict[str, Any]] = None,
    worker_error: Optional[Exception] = None,
) -> MagicMock:
    """构造 GraphController mock：脚本化 poll_state / is_interrupted / interrupt_kind /
    get_interrupt_payload / get_worker_error，其余为桩。"""
    controller = MagicMock()
    controller.poll_state.return_value = state
    controller.is_interrupted.return_value = is_interrupted
    controller.interrupt_kind.return_value = interrupt_kind
    controller.get_interrupt_payload.return_value = interrupt_payload
    controller.get_worker_error.return_value = worker_error
    return controller


def _run(controller: MagicMock, script: str) -> AppTest:
    """patch app._get_controller（页面 from app import _get_controller），跑一次 AppTest。"""
    with patch("app._get_controller", return_value=controller):
        at = AppTest.from_string(script)
        at.run()
    return at


def _collect_text(at: AppTest) -> str:
    """聚合 AppTest 元素树所有可读文本，便于断言渲染内容。"""
    parts: List[str] = []
    for collection in (at.title, at.subheader, at.caption, at.markdown,
                       at.text, at.warning, at.info, at.error):
        for el in collection:
            parts.append(str(getattr(el, "value", "")))
    for el in getattr(at, "code", []):
        parts.append(str(getattr(el, "value", "")))
    return "\n".join(parts)


# 关键（沿用 analysis_progress 范式）：render() 内 reporting 跳转 / planning interrupt
# 会改 current_page 并 st.rerun()。AppTest 下 st.rerun() 重跑整个脚本，若脚本无条件再调
# execution 页 render() 且 mock 状态不变，会陷入无限 rerun 循环（真实 app.py 由 _PAGE_MAP
# 路由到别的页面而跳出）。故脚本按 current_page 路由：仅 execution 调本页 render；切到
# report/review/input 后渲染占位 stub 跳出循环（与 app.py _PAGE_MAP 行为对齐）。
_SCRIPT_HEADER = """
import streamlit as st
st.session_state.setdefault("thread_id", "task-exec-001")
st.session_state.setdefault("current_page", "execution")
page = st.session_state.get("current_page", "execution")
if page == "execution":
    from ui.pages.execution_monitor import render
    render()
elif page == "report":
    st.write("REPORT_STUB")   # 模拟 result_report 页（跳出 rerun 循环）
elif page == "review":
    st.write("REVIEW_STUB")   # 模拟 plan_review 页
else:
    st.write("INPUT_STUB")    # 模拟 input 页
"""

_SCRIPT_NO_THREAD = """
from ui.pages.execution_monitor import render
render()
"""


def _script_with_thread(thread_id: str, extra: str = "") -> str:
    """构造按 current_page 路由的 AppTest 脚本（指定 thread_id + 额外 session 预置）。"""
    return f"""
import streamlit as st
st.session_state.setdefault("thread_id", {thread_id!r})
st.session_state.setdefault("current_page", "execution")
{extra}
page = st.session_state.get("current_page", "execution")
if page == "execution":
    from ui.pages.execution_monitor import render
    render()
elif page == "report":
    st.write("REPORT_STUB")
elif page == "review":
    st.write("REVIEW_STUB")
else:
    st.write("INPUT_STUB")
"""


# =========================================================================== #
# CP-E2-1：页面模块可导入（轻量 import 冒烟）
# =========================================================================== #
def test_cp_e2_1_importable():
    """CP-E2-1：模块可导入，render 可 callable + 别名 + __all__ 约定。"""
    mod = _mod()
    assert callable(mod.render)
    assert mod.render_execution_monitor_page is mod.render
    assert mod.__all__ == ["render", "render_execution_monitor_page"]


def test_cp_e2_1_no_thread_id_fallback():
    """CP-E2-1：无 thread_id → 兜底提示并 return，不崩、不触达 controller 读路径。"""
    controller = _make_controller_mock()
    at = _run(controller, _SCRIPT_NO_THREAD)
    assert not at.exception
    assert "尚未启动任务" in _collect_text(at)
    controller.poll_state.assert_not_called()


def test_cp_e2_1_renders_without_exception_normal_path():
    """CP-E2-1：正常 state（execution 阶段、非 interrupt）→ render 不抛异常。"""
    controller = _make_controller_mock(state=_make_state(), is_interrupted=False)
    at = _run(controller, _SCRIPT_HEADER)
    assert not at.exception, at.exception


# =========================================================================== #
# CP-E2-2：进度展示读 fix_loop_count / MAX_FIX_LOOP_COUNT / fix_loop_history
# =========================================================================== #
def test_cp_e2_2_fix_loop_progress_text_pure():
    """CP-E2-2（纯函数）：_fix_loop_progress_text 渲染「修复第 N / MAX_FIX_LOOP_COUNT 轮」+ 边界。"""
    mod = _mod()
    assert mod._fix_loop_progress_text(1) == f"修复第 1 / {MAX_FIX_LOOP_COUNT} 轮"
    assert mod._fix_loop_progress_text(2) == f"修复第 2 / {MAX_FIX_LOOP_COUNT} 轮"
    assert mod._fix_loop_progress_text(MAX_FIX_LOOP_COUNT) == f"修复第 {MAX_FIX_LOOP_COUNT} / {MAX_FIX_LOOP_COUNT} 轮"
    # 越界封顶（不出现「第 (MAX+1) / MAX 轮」这种越界文案）。
    assert mod._fix_loop_progress_text(MAX_FIX_LOOP_COUNT + 5) == f"修复第 {MAX_FIX_LOOP_COUNT} / {MAX_FIX_LOOP_COUNT} 轮"
    # N==0 / 非数 → 尚未进入修复循环。
    assert "尚未进入修复循环" in mod._fix_loop_progress_text(0)
    assert "尚未进入修复循环" in mod._fix_loop_progress_text("x")
    assert "尚未进入修复循环" in mod._fix_loop_progress_text(None)


def test_cp_e2_2_summarize_fix_history_pure():
    """CP-E2-2（纯函数）：_summarize_fix_history 抽出每轮「错了什么 + 修复策略」+ 防御非 dict。"""
    mod = _mod()
    history = _make_fix_history(2) + ["not-a-dict", {"round_number": 3}]
    rows = mod._summarize_fix_history(history)
    # 非 dict 被跳过；3 条 dict 保留。
    assert len(rows) == 3
    assert rows[0]["round"] == "1"
    assert "import 错误" in rows[0]["error_summary"]
    assert "补充缺失依赖" in rows[0]["fix_strategy"]
    assert rows[0]["error_category"] == "import"
    # 缺字段项有兜底文案，不 KeyError。
    assert rows[2]["error_summary"] == "(无摘要)"
    assert rows[2]["fix_strategy"] == "(未记录修复策略)"
    # None / 空 → 空列表。
    assert mod._summarize_fix_history(None) == []
    assert mod._summarize_fix_history([]) == []


def test_cp_e2_2_progress_text_rendered_in_apptest():
    """CP-E2-2（AppTest）：渲染含「修复第 N / MAX_FIX_LOOP_COUNT 轮」+ 每轮摘要文案。"""
    state = _make_state(fix_loop_count=2, fix_loop_history=_make_fix_history(2))
    controller = _make_controller_mock(state=state, is_interrupted=False)
    at = _run(controller, _SCRIPT_HEADER)
    assert not at.exception, at.exception
    text = _collect_text(at)
    assert f"修复第 2 / {MAX_FIX_LOOP_COUNT} 轮" in text
    # 阶段名（execution）已展示。
    assert "执行验证" in text
    # 每轮摘要的修复策略至少出现一条（expander label 含错误摘要）。
    assert "import 错误" in text


def test_cp_e2_2_zero_fix_loop_shows_not_started():
    """CP-E2-2（AppTest）：fix_loop_count==0 → 展示「尚未进入修复循环」，不误报修复轮。"""
    state = _make_state(fix_loop_count=0, fix_loop_history=[])
    controller = _make_controller_mock(state=state, is_interrupted=False)
    at = _run(controller, _SCRIPT_HEADER)
    assert not at.exception, at.exception
    text = _collect_text(at)
    assert "尚未进入修复循环" in text
    assert "修复第" not in text


# =========================================================================== #
# CP-E2-3：dev_loop 失败决策面板——三按钮注入对应 {"decision": ...} payload
# =========================================================================== #
def test_cp_e2_3_build_decision_payload_contract_pure():
    """CP-E2-3（纯函数）：_build_decision_payload 三态 key/取值与 execution.py 契约严格一致。"""
    mod = _mod()
    # terminate / export_code：仅 decision 键。
    assert mod._build_decision_payload("terminate") == {"decision": "terminate"}
    assert mod._build_decision_payload("export_code") == {"decision": "export_code"}
    # revise_plan：附 user_feedback。
    assert mod._build_decision_payload("revise_plan", "换官方仓库") == {
        "decision": "revise_plan",
        "user_feedback": "换官方仓库",
    }
    # revise_plan 空 feedback → user_feedback 为空串（execution.py 端 .get 兜底空串，一致）。
    assert mod._build_decision_payload("revise_plan") == {
        "decision": "revise_plan",
        "user_feedback": "",
    }


def test_cp_e2_3_decision_values_match_execution_node_options():
    """CP-E2-3：本页决策取值集合与 execution 节点 interrupt#2 options 严格对齐（防臆造）。"""
    # 坑6：core/nodes/__init__.py 显式 export execution callable，`from core.nodes import
    # execution` 会拿到 callable 而非模块；用 importlib.import_module 取模块属性。
    exec_node = importlib.import_module("core.nodes.execution")

    mod = _mod()
    # 本页三个决策常量。
    page_decisions = {
        mod._DECISION_TERMINATE,
        mod._DECISION_REVISE_PLAN,
        mod._DECISION_EXPORT_CODE,
    }
    # execution 节点 payload 的 options（_build_dev_loop_interrupt_payload）。
    node_options = set(exec_node._build_dev_loop_interrupt_payload(
        {"errors": ["x"]},  # exec_result
        exec_node.ExecutionFeedback(
            exec_node.ErrorCategory.RUNTIME, True, "s", "h", "stderr"
        ),
        {"fix_loop_count": 3, "fix_loop_history": []},  # state
    )["options"])
    assert page_decisions == node_options, (
        f"本页决策取值 {page_decisions} 必须与 execution 节点 options {node_options} 一致"
    )
    # interrupt_kind 也对齐。
    assert mod._INTERRUPT_KIND_DEV_LOOP == exec_node.INTERRUPT_KIND


def _run_dev_loop_panel():
    """渲染 dev_loop 失败决策面板（is_interrupted + interrupt_kind==dev_loop_failure）。

    返回 (AppTest, controller_mock)。注意：_run 内 patch 上下文在 at.run() 后退出，故返回的
    controller 仅用于只读断言，不可在返回后再点击按钮触发其方法（点击类测试自带 patch 上下文）。
    """
    controller = _make_controller_mock(
        state=_make_state(current_step="execution"),
        is_interrupted=True,
        interrupt_kind="dev_loop_failure",
        interrupt_payload=_make_dev_loop_payload(),
    )
    at = _run(controller, _SCRIPT_HEADER)
    return at, controller


def test_cp_e2_3_panel_shows_three_buttons():
    """CP-E2-3（AppTest）：dev_loop_failure interrupt 时展示三个决策按钮（原生 st.button 可见）。"""
    at, _controller = _run_dev_loop_panel()
    assert not at.exception, at.exception
    btn_keys = {b.key for b in at.button}
    assert "btn_dev_loop_terminate" in btn_keys
    assert "btn_dev_loop_revise_plan" in btn_keys
    assert "btn_dev_loop_export_code" in btn_keys
    # 失败上下文摘要已展示（错误摘要 + 修复历程）。
    text = _collect_text(at)
    assert "运行时异常" in text


def test_cp_e2_3_terminate_button_injects_terminate_payload():
    """CP-E2-3（AppTest）：点「终止任务」→ resume_with(tid, {"decision": "terminate"})。"""
    controller = _make_controller_mock(
        state=_make_state(current_step="execution"),
        is_interrupted=True,
        interrupt_kind="dev_loop_failure",
        interrupt_payload=_make_dev_loop_payload(),
    )
    with patch("app._get_controller", return_value=controller):
        at = AppTest.from_string(_SCRIPT_HEADER)
        at.run()
        assert not at.exception, at.exception
        btns = [b for b in at.button if b.key == "btn_dev_loop_terminate"]
        assert len(btns) == 1
        btns[0].click().run()
    controller.resume_with.assert_called_once_with(
        "task-exec-001", {"decision": "terminate"}
    )


def test_cp_e2_3_export_code_button_injects_export_payload():
    """CP-E2-3（AppTest）：点「导出代码」→ resume_with(tid, {"decision": "export_code"})。"""
    controller = _make_controller_mock(
        state=_make_state(current_step="execution"),
        is_interrupted=True,
        interrupt_kind="dev_loop_failure",
        interrupt_payload=_make_dev_loop_payload(),
    )
    with patch("app._get_controller", return_value=controller):
        at = AppTest.from_string(_SCRIPT_HEADER)
        at.run()
        assert not at.exception, at.exception
        btns = [b for b in at.button if b.key == "btn_dev_loop_export_code"]
        assert len(btns) == 1
        btns[0].click().run()
    controller.resume_with.assert_called_once_with(
        "task-exec-001", {"decision": "export_code"}
    )


def test_cp_e2_3_revise_plan_button_injects_revise_payload_with_feedback():
    """CP-E2-3（AppTest）：填修订意见 + 点「提交改计划」→
    resume_with(tid, {"decision": "revise_plan", "user_feedback": <文本>})。"""
    fb = "换用官方仓库的训练脚本并跳过缺失数据集步骤"
    script = _script_with_thread(
        "task-exec-rev",
        extra=f'st.session_state["_exec_revise_feedback"] = {fb!r}',
    )
    controller = _make_controller_mock(
        state=_make_state(current_step="execution"),
        is_interrupted=True,
        interrupt_kind="dev_loop_failure",
        interrupt_payload=_make_dev_loop_payload(),
    )
    with patch("app._get_controller", return_value=controller):
        at = AppTest.from_string(script)
        at.run()
        assert not at.exception, at.exception
        # 修订意见文本框为原生 st.text_area（AppTest 可见）。
        ta_keys = {ta.key for ta in at.text_area}
        assert "_exec_revise_feedback" in ta_keys
        btns = [b for b in at.button if b.key == "btn_dev_loop_revise_plan"]
        assert len(btns) == 1
        btns[0].click().run()
    controller.resume_with.assert_called_once_with(
        "task-exec-rev", {"decision": "revise_plan", "user_feedback": fb}
    )


def test_cp_e2_3_panel_only_when_dev_loop_failure_kind():
    """CP-E2-3 反证：interrupt 但 kind 非 dev_loop_failure（planning）→ 不展示决策面板，跳回 review。"""
    controller = _make_controller_mock(
        state=_make_state(current_step="planning"),
        is_interrupted=True,
        interrupt_kind="planning",
        interrupt_payload={"interrupt_kind": "planning"},
    )
    with patch("app._get_controller", return_value=controller):
        at = AppTest.from_string(_SCRIPT_HEADER)
        at.run()
    # planning interrupt → 跳回 review 页（current_page 被改写），不渲染 dev_loop 按钮。
    assert at.session_state["current_page"] == "review"
    btn_keys = {b.key for b in at.button}
    assert "btn_dev_loop_terminate" not in btn_keys


# =========================================================================== #
# CP-E2-4：output_truncated=True 时日志展示标注截断
# =========================================================================== #
def test_cp_e2_4_logs_truncated_pure():
    """CP-E2-4（纯函数）：_logs_truncated 两条探测路径 + 防御非 dict / None。"""
    mod = _mod()
    # 路径1：execution_result 顶层 output_truncated 真值。
    assert mod._logs_truncated({"output_truncated": True, "logs": "x"}) is True
    assert mod._logs_truncated({"output_truncated": False, "logs": "x"}) is False
    # 路径2：logs 文本含截断标记。
    assert mod._logs_truncated({"logs": "... output_truncated ..."}) is True
    assert mod._logs_truncated({"logs": "... 日志已截断 ..."}) is True
    assert mod._logs_truncated({"logs": "[truncated]"}) is True
    # 普通日志不误判。
    assert mod._logs_truncated({"logs": "run ok"}) is False
    # 防御：None / 非 dict → False。
    assert mod._logs_truncated(None) is False
    assert mod._logs_truncated("not-a-dict") is False
    assert mod._logs_truncated({}) is False


def test_cp_e2_4_truncated_notice_rendered():
    """CP-E2-4（AppTest）：output_truncated=True 的 execution_result → 渲染「日志已截断」标注。"""
    exec_result = _make_execution_result(output_truncated=True, logs="tail logs")
    state = _make_state(execution_result=exec_result)
    controller = _make_controller_mock(state=state, is_interrupted=False)
    at = _run(controller, _SCRIPT_HEADER)
    assert not at.exception, at.exception
    text = _collect_text(at)
    assert "日志已截断" in text


def test_cp_e2_4_no_truncation_no_notice():
    """CP-E2-4（AppTest）反证：output_truncated 缺失/False + 普通日志 → 无截断标注。"""
    exec_result = _make_execution_result(output_truncated=False, logs="run ok")
    state = _make_state(execution_result=exec_result)
    controller = _make_controller_mock(state=state, is_interrupted=False)
    at = _run(controller, _SCRIPT_HEADER)
    assert not at.exception, at.exception
    # 截断标注的描述文案不应出现（标题「日志已截断」由 alert 渲染，普通路径不触发）。
    text = _collect_text(at)
    assert "日志已截断" not in text


# =========================================================================== #
# CP-E2-5：reporting 完成自动跳转结果报告页（状态判定单测 + AppTest 路由实证）
# =========================================================================== #
def test_cp_e2_5_should_jump_to_report_pure():
    """CP-E2-5（纯函数）：_should_jump_to_report 真值表（reporting + report_path + 非 interrupt）。"""
    mod = _mod()
    ok_state = {"current_step": "reporting", "report_path": "/tmp/report.md"}
    # 全部满足 → True。
    assert mod._should_jump_to_report(ok_state, is_interrupted=False) is True
    # interrupt 时不跳（决策面板优先）。
    assert mod._should_jump_to_report(ok_state, is_interrupted=True) is False
    # current_step 非 reporting → 不跳。
    assert mod._should_jump_to_report(
        {"current_step": "execution", "report_path": "/tmp/r.md"}, is_interrupted=False
    ) is False
    # report_path 空 → 不跳（reporting 尚未产出报告）。
    assert mod._should_jump_to_report(
        {"current_step": "reporting", "report_path": None}, is_interrupted=False
    ) is False
    assert mod._should_jump_to_report(
        {"current_step": "reporting", "report_path": ""}, is_interrupted=False
    ) is False
    # 防御：None / 非 dict → False。
    assert mod._should_jump_to_report(None, is_interrupted=False) is False
    assert mod._should_jump_to_report("x", is_interrupted=False) is False


def test_cp_e2_5_auto_jump_to_report_page():
    """CP-E2-5（AppTest）：reporting 完成 + report_path 非空 + 非 interrupt → current_page 跳 report。"""
    state = _make_state(current_step="reporting", report_path="/tmp/report.md")
    controller = _make_controller_mock(state=state, is_interrupted=False)
    with patch("app._get_controller", return_value=controller):
        at = AppTest.from_string(_SCRIPT_HEADER)
        at.run()
    assert at.session_state["current_page"] == STREAMLIT_PAGE_REPORT


def test_cp_e2_5_no_jump_when_report_path_missing():
    """CP-E2-5（AppTest）反证：reporting 但 report_path 为空 → 不跳转，留在执行监控页正常渲染。"""
    state = _make_state(current_step="reporting", report_path=None)
    controller = _make_controller_mock(state=state, is_interrupted=False)
    with patch("app._get_controller", return_value=controller):
        at = AppTest.from_string(_SCRIPT_HEADER)
        at.run()
    assert not at.exception, at.exception
    # 仍停在 execution 页（未跳 report）。
    assert at.session_state["current_page"] == "execution"


def test_cp_e2_5_no_jump_when_interrupted():
    """CP-E2-5（AppTest）反证：reporting + report_path 非空但仍 interrupt（dev_loop）→ 决策面板优先，不跳。"""
    state = _make_state(current_step="reporting", report_path="/tmp/report.md")
    controller = _make_controller_mock(
        state=state,
        is_interrupted=True,
        interrupt_kind="dev_loop_failure",
        interrupt_payload=_make_dev_loop_payload(),
    )
    with patch("app._get_controller", return_value=controller):
        at = AppTest.from_string(_SCRIPT_HEADER)
        at.run()
    assert not at.exception, at.exception
    # interrupt 优先：展示决策面板（不跳 report）。
    assert at.session_state["current_page"] == "execution"
    btn_keys = {b.key for b in at.button}
    assert "btn_dev_loop_terminate" in btn_keys


# =========================================================================== #
# 补充：node_error 前缀解析 + 终态优先级链（错误/降级展示 + 防御）
# =========================================================================== #
def test_parse_node_error_extracts_category_prefix():
    """_parse_node_error 抽出 [error_category=...] 前缀 + 剥离后纯摘要（execution.py 写入格式）。"""
    mod = _mod()
    err = {
        "node_name": "execution",
        "error_type": "transient",
        "error_message": "[error_category=import] import 错误（缺包 / 模块路径错误）",
        "error_detail": "ModuleNotFoundError: numpy",
    }
    parsed = mod._parse_node_error(err)
    assert parsed["node"] == "execution"
    assert parsed["type"] == "transient"
    assert parsed["category"] == "import"
    assert parsed["summary"] == "import 错误（缺包 / 模块路径错误）"
    assert "ModuleNotFoundError" in parsed["detail"]
    # 无前缀 → category 空，summary 为原文。
    plain = mod._parse_node_error({"node_name": "coding", "error_message": "纯文本错误"})
    assert plain["category"] == ""
    assert plain["summary"] == "纯文本错误"
    # 非 dict → 降级，不崩。
    weird = mod._parse_node_error("boom")
    assert weird["summary"] == "boom"


def test_worker_error_fatal_card_stops_polling():
    """终态①：worker_error 非空 → FATAL 卡片 + 不调 poll_state（最高优先级早返回）。"""
    controller = _make_controller_mock(worker_error=RuntimeError("worker crashed"))
    at = _run(controller, _SCRIPT_HEADER)
    assert not at.exception
    assert "工作线程异常" in _collect_text(at)
    controller.poll_state.assert_not_called()


def test_state_error_fatal_card():
    """终态③：state.error 非空 → FATAL 卡片（重试 / 返回）。"""
    state = _make_state(error="致命错误：rate limit")
    controller = _make_controller_mock(state=state, is_interrupted=False)
    at = _run(controller, _SCRIPT_HEADER)
    assert not at.exception
    assert "致命错误" in _collect_text(at)


def test_cancelled_card():
    """终态④：current_step==cancelled_by_user → 任务已终止卡片。"""
    state = _make_state(current_step="cancelled_by_user")
    controller = _make_controller_mock(state=state, is_interrupted=False)
    at = _run(controller, _SCRIPT_HEADER)
    assert not at.exception
    assert "任务已终止" in _collect_text(at)


def test_degraded_nodes_alert_rendered():
    """错误/降级：degraded_nodes 非空 → 渲染降级提示（含降级节点名）。"""
    state = _make_state(
        current_step="execution",
        degraded_nodes=["execution"],
        node_errors=[{
            "node_name": "execution",
            "error_type": "degraded",
            "error_message": "[error_category=degraded] execution 降级: export_code",
            "error_detail": None,
        }],
    )
    controller = _make_controller_mock(state=state, is_interrupted=False)
    at = _run(controller, _SCRIPT_HEADER)
    assert not at.exception, at.exception
    text = _collect_text(at)
    assert "降级节点" in text
