"""Sprint 6 批次 3 · T-S6-3-3：execution_monitor 单收口窗口页面级验收（S6-01/02 + MF-4/7 + R7）。

覆盖 dev-plan §4 批次 3 检查点（页面级 AppTest + mock GraphController 驱动真实 render()）：
    - CP-3.3-1 换代过渡态：token==awaiting 且有存活 worker → "处理中"过渡态 + 注册 autorefresh；
    - CP-3.3-2 换代反例（防死锁 + 防误提交）：token 变 → 新面板；同题重问无 worker → 视为换代不死锁；
    - CP-3.3-3 case 分发通则：过渡态"等后台变化"注册 autorefresh，停轮询分支（面板/孤儿/终态）不注册；
    - CP-3.3-4 在途标签：active_node 存在 → 阶段指示"「…」进行中"不滞后；
    - CP-3.3-5 MF-7：dev_loop 面板渲染 execution_result.logs 尾部 + 空 logs 占位 + payload 键零触碰；
    - CP-3.3-6 MF-4：dev_loop 面板 execution_errors 无 [error_category=...] 裸标签（_format_exec_error_line 纯函数）；
    - CP-3.3-7 R7 孤儿卡片：无 worker ∧ active_node 非空 ∧ 无 interrupt → 孤儿卡片 + 「继续执行」+ 停轮询；
    - CP-3.3-8 case 全矩阵 ×3 连跑防 flaky（R-S6-1）。

测试策略：沿用 sp3 E2 / sp5 t36/t43 范式——AppTest.from_string + patch app._get_controller +
patch execution_monitor.st_autorefresh 观测注册与否（"停轮询"正确性根基）。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import MagicMock, patch

import pytest
from streamlit.testing.v1 import AppTest

from config import DEV_LOOP_PANEL_LOG_TAIL_CHARS
from ui.pages.execution_monitor import _format_exec_error_line


# --------------------------------------------------------------------------- #
# 夹具
# --------------------------------------------------------------------------- #
def _make_state(current_step: str = "execution", **overrides: Any) -> Dict[str, Any]:
    state: Dict[str, Any] = {
        "current_step": current_step,
        "fix_loop_count": 1,
        "fix_loop_history": [],
        "execution_result": None,
        "node_errors": [],
        "degraded_nodes": [],
        "report_path": None,
        "error": None,
    }
    state.update(overrides)
    return state


def _dev_loop_payload(**overrides: Any) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "interrupt_kind": "dev_loop_failure",
        "fix_loop_count": 3,
        "error_category": "runtime",
        "error_summary": "运行时异常",
        "fix_hint": "检查依赖",
        "execution_errors": [],
        "fix_loop_history": [],
        "options": ["terminate", "revise_plan", "export_code"],
    }
    payload.update(overrides)
    return payload


def _make_controller(
    *,
    state: Optional[Dict[str, Any]] = None,
    is_interrupted: bool = False,
    interrupt_kind: Optional[str] = None,
    interrupt_payload: Optional[Dict[str, Any]] = None,
    worker_error: Optional[Exception] = None,
    is_finished: bool = False,
    interrupt_token: Optional[str] = None,
    has_active_worker: bool = False,
    phase: Optional[Dict[str, Any]] = None,
) -> MagicMock:
    c = MagicMock()
    c.poll_state.return_value = state
    c.get_worker_error.return_value = worker_error
    c.is_interrupted.return_value = is_interrupted
    c.interrupt_kind.return_value = interrupt_kind
    c.get_interrupt_payload.return_value = interrupt_payload
    c.is_finished.return_value = is_finished
    c.get_interrupt_token.return_value = interrupt_token
    c.has_active_worker.return_value = has_active_worker
    c.get_phase.return_value = (
        phase if phase is not None else {"active_node": None, "current_step": None}
    )
    return c


_SCRIPT = """
import streamlit as st
st.session_state.setdefault("thread_id", "task-exec-001")
st.session_state.setdefault("current_page", "execution")
page = st.session_state.get("current_page", "execution")
if page == "execution":
    from ui.pages.execution_monitor import render
    render()
else:
    st.write("OTHER_STUB")
"""


def _run(
    controller: MagicMock, session_seed: Optional[Dict[str, Any]] = None
) -> Tuple[AppTest, MagicMock]:
    with patch("app._get_controller", return_value=controller), patch(
        "ui.pages.execution_monitor.st_autorefresh"
    ) as ar:
        at = AppTest.from_string(_SCRIPT)
        for k, v in (session_seed or {}).items():
            at.session_state[k] = v
        at.run()
    return at, ar


def _text(at: AppTest) -> str:
    parts: List[str] = []
    for collection in (at.title, at.subheader, at.caption, at.markdown,
                       at.warning, at.info, at.error):
        parts.extend(str(getattr(el, "value", "")) for el in collection)
    for el in getattr(at, "code", []):
        parts.append(str(getattr(el, "value", "")))
    return "\n".join(parts)


def _button_keys(at: AppTest) -> List[str]:
    return [getattr(b, "key", None) for b in getattr(at, "button", [])]


_AWAIT_KEY = "_exec_awaiting_token"


# =========================================================================== #
# CP-3.3-1 换代过渡态（token==awaiting ∧ 有存活 worker → "处理中" + 注册 autorefresh）
# =========================================================================== #
def test_cp_3_3_1_awaiting_transition_renders_and_registers_autorefresh():
    c = _make_controller(
        state=_make_state(),
        is_interrupted=True,
        interrupt_kind="dev_loop_failure",
        interrupt_payload=_dev_loop_payload(),
        interrupt_token="tok-1",
        has_active_worker=True,  # resume worker 存活
    )
    at, ar = _run(c, session_seed={_AWAIT_KEY: "tok-1"})
    assert not at.exception, at.exception
    text = _text(at)
    assert "处理中" in text, "同一代 token + 存活 worker → 过渡态"
    assert "已收到你的决策" in text
    # 过渡态"等后台变化" → 必须注册 autorefresh（通则）。
    ar.assert_called_once()
    # 决策面板此刻不渲染（被占位取代，第一道防线）。
    assert "执行失败决策" not in text


def test_cp_3_3_1_awaiting_auto_transitions_when_interrupt_consumed():
    """worker 消费完 resume → interrupt 消失（is_interrupted False）→ 落回后续分发（非过渡态）。"""
    c = _make_controller(
        state=_make_state(current_step="reporting", report_path="/x/report.md"),
        is_interrupted=False,  # interrupt 已被消费
        interrupt_token=None,
        has_active_worker=True,
        phase={"active_node": "reporting", "current_step": "reporting"},
    )
    at, ar = _run(c, session_seed={_AWAIT_KEY: "tok-1"})
    assert not at.exception, at.exception
    # 不再是过渡态；此 state 触发 case⑥ 跳报告页（不注册 autorefresh）。
    assert "处理中" not in _text(at)


# =========================================================================== #
# CP-3.3-2 换代反例（防死锁 + 防误提交）
# =========================================================================== #
def test_cp_3_3_2_new_token_renders_new_panel_not_transition():
    """token 变（新问题）→ 渲染新决策面板，不误沿用旧过渡态（防误提交）。"""
    c = _make_controller(
        state=_make_state(),
        is_interrupted=True,
        interrupt_kind="dev_loop_failure",
        interrupt_payload=_dev_loop_payload(),
        interrupt_token="tok-NEW",
        has_active_worker=True,
    )
    at, ar = _run(c, session_seed={_AWAIT_KEY: "tok-OLD"})
    assert not at.exception, at.exception
    text = _text(at)
    assert "执行失败决策" in text, "token 变 → 渲染新面板"
    assert "处理中" not in text
    ar.assert_not_called()  # 决策面板停轮询


def test_cp_3_3_2_same_token_no_worker_treated_as_regen_not_deadlock():
    """**防死锁关键**：token 相同但无存活 worker（同题重问 / worker 已死）→ 视为换代渲染面板，
    不卡在过渡态死锁（架构 §1.2 第二行）。"""
    c = _make_controller(
        state=_make_state(),
        is_interrupted=True,
        interrupt_kind="dev_loop_failure",
        interrupt_payload=_dev_loop_payload(),
        interrupt_token="tok-1",
        has_active_worker=False,  # 无存活 worker → 不进过渡态
    )
    at, ar = _run(c, session_seed={_AWAIT_KEY: "tok-1"})
    assert not at.exception, at.exception
    text = _text(at)
    assert "执行失败决策" in text, "同 token 无 worker → 渲染面板不死锁"
    assert "处理中" not in text
    ar.assert_not_called()


# =========================================================================== #
# CP-3.3-4 在途标签（active_node 存在 → "「…」进行中"）
# =========================================================================== #
def test_cp_3_3_4_in_transit_label_from_active_node():
    c = _make_controller(
        state=_make_state(current_step="planning"),  # current_step 滞后
        is_interrupted=False,
        has_active_worker=True,  # 运行中（非孤儿）
        phase={"active_node": "coding", "current_step": "planning"},
    )
    at, ar = _run(c)
    assert not at.exception, at.exception
    text = _text(at)
    assert "进行中" in text, "active_node 存在 → 在途标签"
    assert "执行监控" in text  # case⑦ 正常渲染
    ar.assert_called_once()  # 正常监控注册 autorefresh


# =========================================================================== #
# CP-3.3-5 MF-7 logs 尾部 + 空占位
# =========================================================================== #
def test_cp_3_3_5_dev_loop_panel_renders_logs_tail():
    long_logs = "A" * 100 + "结尾标记_TAIL_MARKER"
    state = _make_state(execution_result={"logs": long_logs})
    c = _make_controller(
        state=state,
        is_interrupted=True,
        interrupt_kind="dev_loop_failure",
        interrupt_payload=_dev_loop_payload(),
        interrupt_token="tok-1",
    )
    at, ar = _run(c)
    assert not at.exception, at.exception
    text = _text(at)
    assert "最近一次运行输出" in text
    assert "结尾标记_TAIL_MARKER" in text, "logs 尾部渲染"


def test_cp_3_3_5_dev_loop_panel_logs_tail_truncated_to_config():
    """logs 超长 → 仅渲染尾部 DEV_LOOP_PANEL_LOG_TAIL_CHARS 字符。"""
    head = "HEAD_SHOULD_BE_DROPPED"
    logs = head + "B" * (DEV_LOOP_PANEL_LOG_TAIL_CHARS + 500)
    state = _make_state(execution_result={"logs": logs})
    c = _make_controller(
        state=state,
        is_interrupted=True,
        interrupt_kind="dev_loop_failure",
        interrupt_payload=_dev_loop_payload(),
        interrupt_token="tok-1",
    )
    at, _ = _run(c)
    assert not at.exception, at.exception
    code_values = [str(getattr(el, "value", "")) for el in getattr(at, "code", [])]
    joined = "\n".join(code_values)
    assert head not in joined, "超出尾部窗口的头部应被截掉"
    # 尾部窗口长度不超过配置值。
    assert any(len(cv) <= DEV_LOOP_PANEL_LOG_TAIL_CHARS for cv in code_values if "B" in cv)


def test_cp_3_3_5_dev_loop_panel_empty_logs_placeholder():
    state = _make_state(execution_result={"logs": ""})
    c = _make_controller(
        state=state,
        is_interrupted=True,
        interrupt_kind="dev_loop_failure",
        interrupt_payload=_dev_loop_payload(),
        interrupt_token="tok-1",
    )
    at, _ = _run(c)
    assert not at.exception, at.exception
    text = _text(at)
    assert "最近一次运行输出" in text
    assert "暂无运行输出" in text, "空 logs → 占位不静默空白"


def test_cp_3_3_5_mf7_reads_state_not_payload_key_structure_untouched():
    """MF-7 从 state.execution_result.logs 读，interrupt#2 payload 键结构零触碰（AC-S4-05）。"""
    payload = _dev_loop_payload()
    payload_keys_before = set(payload.keys())
    state = _make_state(execution_result={"logs": "some_logs_TAIL"})
    c = _make_controller(
        state=state, is_interrupted=True, interrupt_kind="dev_loop_failure",
        interrupt_payload=payload, interrupt_token="tok-1",
    )
    at, _ = _run(c)
    assert not at.exception, at.exception
    # payload 未被渲染函数改键（读的是 state 不是 payload）。
    assert set(payload.keys()) == payload_keys_before
    assert "logs" not in payload, "logs 不应被塞进 interrupt payload"


# =========================================================================== #
# CP-3.3-6 MF-4 裸标签消除（纯函数 + 面板级）
# =========================================================================== #
def test_cp_3_3_6_format_exec_error_line_translates_bare_label():
    assert _format_exec_error_line("[error_category=none] 执行成功") == "[无错误] 执行成功"
    assert _format_exec_error_line("[error_category=hardware] CUDA 显存不足") == "[硬件资源不足] CUDA 显存不足"
    # 无标签 → 原样
    assert _format_exec_error_line("纯文本错误") == "纯文本错误"
    # 空分类 / 畸形 → 不崩
    assert "error_category" not in _format_exec_error_line("[error_category=none] X")
    assert _format_exec_error_line("[error_category=] Y") == "Y" or "Y" in _format_exec_error_line("[error_category=] Y")


def test_cp_3_3_6_dev_loop_panel_no_bare_error_category_label():
    payload = _dev_loop_payload(
        execution_errors=["[error_category=none] 执行成功", "[error_category=hardware] 显存不足"],
    )
    c = _make_controller(
        state=_make_state(), is_interrupted=True, interrupt_kind="dev_loop_failure",
        interrupt_payload=payload, interrupt_token="tok-1",
    )
    at, _ = _run(c)
    assert not at.exception, at.exception
    text = _text(at)
    assert "[error_category=none]" not in text, "裸标签不泄漏（AC-S6-20）"
    assert "[error_category=hardware]" not in text
    assert "无错误" in text and "硬件资源不足" in text, "经 term_map 翻译"


# =========================================================================== #
# CP-3.3-7 R7 孤儿卡片（无 worker ∧ active_node 非空 ∧ 无 interrupt）
# =========================================================================== #
def test_cp_3_3_7_orphan_card_when_no_worker_and_active_node():
    c = _make_controller(
        state=_make_state(current_step="execution"),
        is_interrupted=False,
        has_active_worker=False,  # 进程重启后登记表空
        phase={"active_node": "execution", "current_step": "execution"},
    )
    at, ar = _run(c)
    assert not at.exception, at.exception
    text = _text(at)
    assert "任务已中断（可继续）" in text, "孤儿在途卡片"
    assert "继续执行" in text, "显式续跑按钮"
    assert "重新执行" in text, "重放语义文案明示"
    # R7 孤儿卡片停轮询（仅用户动作可改变）。
    ar.assert_not_called()
    assert "btn_orphan_resume" in _button_keys(at)


def test_cp_3_3_7_running_task_with_worker_is_not_orphan():
    """有存活 worker（正常运行中）→ 不是孤儿，走 case⑦ 正常渲染 + autorefresh。"""
    c = _make_controller(
        state=_make_state(current_step="execution"),
        is_interrupted=False,
        has_active_worker=True,
        phase={"active_node": "execution", "current_step": "execution"},
    )
    at, ar = _run(c)
    assert not at.exception, at.exception
    text = _text(at)
    assert "任务已中断" not in text
    assert "执行监控" in text
    ar.assert_called_once()


# =========================================================================== #
# CP-3.3-3 case 分发通则：等后台变化 → autorefresh；停轮询分支 → 不注册
# =========================================================================== #
def test_cp_3_3_3_polling_discipline_matrix():
    """通则守门：过渡态/正常监控注册 autorefresh；决策面板/孤儿卡片/终态停轮询。"""
    # (a) 过渡态 → 注册
    c_await = _make_controller(
        state=_make_state(), is_interrupted=True, interrupt_kind="dev_loop_failure",
        interrupt_payload=_dev_loop_payload(), interrupt_token="tok-1", has_active_worker=True,
    )
    _, ar_await = _run(c_await, session_seed={_AWAIT_KEY: "tok-1"})
    ar_await.assert_called_once()

    # (b) 决策面板 → 不注册（停轮询等用户决策）
    c_panel = _make_controller(
        state=_make_state(), is_interrupted=True, interrupt_kind="dev_loop_failure",
        interrupt_payload=_dev_loop_payload(), interrupt_token="tok-1", has_active_worker=False,
    )
    _, ar_panel = _run(c_panel)
    ar_panel.assert_not_called()

    # (c) 孤儿卡片 → 不注册（停轮询等用户动作）
    c_orphan = _make_controller(
        state=_make_state(), is_interrupted=False, has_active_worker=False,
        phase={"active_node": "execution", "current_step": "execution"},
    )
    _, ar_orphan = _run(c_orphan)
    ar_orphan.assert_not_called()

    # (d) 正常监控 → 注册
    c_normal = _make_controller(
        state=_make_state(), is_interrupted=False, has_active_worker=True,
        phase={"active_node": "execution", "current_step": "execution"},
    )
    _, ar_normal = _run(c_normal)
    ar_normal.assert_called_once()


# =========================================================================== #
# CP-3.3-8 case 全矩阵 ×3 连跑防 flaky（R-S6-1）
# =========================================================================== #
_MATRIX = [
    # (label, controller_kwargs, session_seed, expect_text, expect_autorefresh)
    ("await", dict(state=_make_state(), is_interrupted=True, interrupt_kind="dev_loop_failure",
                   interrupt_payload=_dev_loop_payload(), interrupt_token="tok-1", has_active_worker=True),
     {_AWAIT_KEY: "tok-1"}, "处理中", True),
    ("dev_panel", dict(state=_make_state(), is_interrupted=True, interrupt_kind="dev_loop_failure",
                       interrupt_payload=_dev_loop_payload(), interrupt_token="tok-1", has_active_worker=False),
     None, "执行失败决策", False),
    ("orphan", dict(state=_make_state(), is_interrupted=False, has_active_worker=False,
                    phase={"active_node": "execution", "current_step": "execution"}),
     None, "任务已中断（可继续）", False),
    ("normal", dict(state=_make_state(), is_interrupted=False, has_active_worker=True,
                    phase={"active_node": "execution", "current_step": "execution"}),
     None, "执行监控", True),
    ("cancelled", dict(state=_make_state(current_step="cancelled_by_user")),
     None, "任务已终止", False),
]


@pytest.mark.parametrize("run_idx", [0, 1, 2])
@pytest.mark.parametrize("label,ckw,seed,expect_text,expect_ar", _MATRIX)
def test_cp_3_3_8_case_matrix_stable_x3(run_idx, label, ckw, seed, expect_text, expect_ar):
    """页面级 case 全矩阵 ×3 连跑：文案 + autorefresh 注册与否稳定（防 flaky）。"""
    c = _make_controller(**ckw)
    at, ar = _run(c, session_seed=seed)
    assert not at.exception, f"[{label}#{run_idx}] {at.exception}"
    assert expect_text in _text(at), f"[{label}#{run_idx}] 期望文案 {expect_text!r}"
    if expect_ar:
        ar.assert_called_once()
    else:
        ar.assert_not_called()
