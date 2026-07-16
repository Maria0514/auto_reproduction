"""Sprint 6 批次 4 · T-S6-4-1：query params URL 重连 + main 接线（S6-06，架构 §7.6）。

覆盖 dev-plan §4 批次 4 检查点：
    - CP-4.1-1 **无参数路径字节等价**（AC-S6-14 红线）：无 task 参数 ∨ 已有 thread_id →
      _restore_from_query_params 直接 return，session_state 不被改动；
    - CP-4.1-2 **重连路由矩阵**：task 参数指向各状态 thread → 路由到对应页面（逐状态断言）；
    - CP-4.1-3 不存在 thread 的 task 参数 → 安全回退（不激活不炸）；已有 thread_id 时不覆盖；
      _restore_attempted 标志：同 session 二次不再重连（防"开启新任务"被旧 task 重激活）。

测试策略：AppTest 脚本调真实 _restore_from_query_params + patched _get_controller（mock
get_task_status/interrupt_kind），经 at.query_params / at.session_state 注入，断言路由结果。
"""

from __future__ import annotations

from typing import Optional
from unittest.mock import MagicMock, patch

from streamlit.testing.v1 import AppTest

from app import (
    TASK_STATUS_AWAITING,
    TASK_STATUS_CANCELLED,
    TASK_STATUS_DONE,
    TASK_STATUS_FAILED,
    TASK_STATUS_INTERRUPTED,
    TASK_STATUS_NO_REPORT,
    TASK_STATUS_RUNNING,
)

# 脚本：调真实 _restore_from_query_params，把 session_state 结果写成可断言文本。
_SCRIPT = """
import streamlit as st
from app import _restore_from_query_params, _get_controller
c = _get_controller()
_restore_from_query_params(c)
st.text("PAGE=" + str(st.session_state.get("current_page")))
st.text("TID=" + str(st.session_state.get("thread_id")))
"""


def _make_controller(status: Optional[str], interrupt_kind: Optional[str] = None) -> MagicMock:
    c = MagicMock()
    c.get_task_status.return_value = status
    c.interrupt_kind.return_value = interrupt_kind
    return c


def _run(controller: MagicMock, *, task: Optional[str] = None, seed_session=None):
    with patch("app._get_controller", return_value=controller):
        at = AppTest.from_string(_SCRIPT)
        # 主入口初始化 session 默认值（模拟 _init_session_state）
        at.session_state["thread_id"] = None
        at.session_state["current_page"] = "input"
        for k, v in (seed_session or {}).items():
            at.session_state[k] = v
        if task is not None:
            at.query_params["task"] = task
        at.run()
    return at


def _text(at: AppTest) -> dict:
    out = {}
    for el in at.text:
        v = str(el.value)
        if "=" in v:
            k, _, val = v.partition("=")
            out[k] = val
    return out


# ======================================================================
# CP-4.1-1 无参数路径字节等价
# ======================================================================
def test_cp_4_1_1_no_task_param_no_change():
    """无 task 参数 → 不激活：thread_id 保持 None、current_page 保持 input（字节等价红线）。"""
    c = _make_controller(TASK_STATUS_RUNNING)
    at = _run(c, task=None)
    assert not at.exception, at.exception
    t = _text(at)
    assert t["TID"] == "None"
    assert t["PAGE"] == "input"
    c.get_task_status.assert_not_called()  # 无 task 参数：连状态推导都不触发


def test_cp_4_1_1_existing_thread_id_not_overwritten():
    """session 已有 thread_id → 直接 return，不被 query task 覆盖（字节等价红线）。"""
    c = _make_controller(TASK_STATUS_DONE)
    at = _run(c, task="task-other", seed_session={"thread_id": "task-existing"})
    assert not at.exception, at.exception
    t = _text(at)
    assert t["TID"] == "task-existing", "已有 thread_id 不被覆盖"
    assert t["PAGE"] == "input"
    c.get_task_status.assert_not_called()


# ======================================================================
# CP-4.1-2 重连路由矩阵
# ======================================================================
def test_cp_4_1_2_done_routes_to_report():
    at = _run(_make_controller(TASK_STATUS_DONE), task="task-done")
    t = _text(at)
    assert t["TID"] == "task-done"
    assert t["PAGE"] == "report"


def test_cp_4_1_2_awaiting_planning_routes_to_review():
    at = _run(_make_controller(TASK_STATUS_AWAITING, interrupt_kind="planning"), task="task-plan")
    t = _text(at)
    assert t["PAGE"] == "review"


def test_cp_4_1_2_awaiting_dev_loop_routes_to_execution():
    at = _run(_make_controller(TASK_STATUS_AWAITING, interrupt_kind="dev_loop_failure"), task="task-dev")
    t = _text(at)
    assert t["PAGE"] == "execution"


def test_cp_4_1_2_awaiting_user_input_routes_to_execution():
    at = _run(_make_controller(TASK_STATUS_AWAITING, interrupt_kind="user_input_request"), task="task-ui")
    t = _text(at)
    assert t["PAGE"] == "execution"


def test_cp_4_1_2_terminal_and_running_route_to_execution():
    """failed / cancelled / no_report / running / interrupted → 执行监控页（case 分发渲染）。"""
    for status in (
        TASK_STATUS_FAILED, TASK_STATUS_CANCELLED, TASK_STATUS_NO_REPORT,
        TASK_STATUS_RUNNING, TASK_STATUS_INTERRUPTED,
    ):
        at = _run(_make_controller(status), task=f"task-{status}")
        t = _text(at)
        assert t["TID"] == f"task-{status}"
        assert t["PAGE"] == "execution", f"status={status} 应路由执行监控页"


# ======================================================================
# CP-4.1-3 安全回退 + 标志
# ======================================================================
def test_cp_4_1_3_nonexistent_thread_safe_fallback():
    """task 指向不存在 thread（get_task_status=None，R1）→ 不激活：thread_id 保持 None 不炸。"""
    at = _run(_make_controller(None), task="task-ghost")
    assert not at.exception, at.exception
    t = _text(at)
    assert t["TID"] == "None", "不存在的 thread 不激活"
    assert t["PAGE"] == "input"


def test_cp_4_1_3_restore_attempted_flag_blocks_second_restore():
    """_restore_attempted 已置位（同 session 二次）→ 不再重连（防开启新任务被旧 task 重激活）。"""
    c = _make_controller(TASK_STATUS_RUNNING)
    at = _run(c, task="task-x", seed_session={"_restore_attempted": True})
    assert not at.exception, at.exception
    t = _text(at)
    assert t["TID"] == "None", "标志已置位 → 跳过重连"
    assert t["PAGE"] == "input"
    c.get_task_status.assert_not_called()
