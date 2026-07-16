"""Sprint 6 批次 4 · T-S6-4-4：任务列表页 + 一键挂回（S6-07，架构 §4.4）。

覆盖 dev-plan §4 批次 4 检查点：
    - CP-4.4-1 枚举渲染：每条含论文标识 + 状态徽标；页面无删除/搜索/分页（边界断言）；
    - CP-4.4-2 一键挂回：点击 → 写 query_params + thread_id + 路由正确；**挂回不调 resume_task**
      （显式动作红线 AC-S6-16——挂回不静默重复执行副作用节点）；
    - CP-4.4-3 列表页不注册 autorefresh（频控断言）+ 入口链接可达；
    - CP-4.4-4 R7 孤儿卡片「继续执行」显式触发 resume_task（批次 3 卡片接通批次 4 续跑）。

测试范式：AppTest 驱动 task_list.render()（原生 st.button 可点）+ mock controller
（list_threads 脚本化）；挂回红线用 resume_task.assert_not_called()。
"""

from __future__ import annotations

from pathlib import Path
from typing import List
from unittest.mock import MagicMock, patch

from streamlit.testing.v1 import AppTest

_TASK_LIST_SRC = Path("ui/pages/task_list.py").read_text(encoding="utf-8")
_PAPER_INPUT_SRC = Path("ui/pages/paper_input.py").read_text(encoding="utf-8")

_SCRIPT = """
import streamlit as st
st.session_state.setdefault("current_page", "tasks")
from ui.pages.task_list import render
render()
"""


def _threads():
    return [
        {"thread_id": "task-aaa", "status": "awaiting", "status_label": "等待输入", "paper_label": "论文A标题"},
        {"thread_id": "task-bbb", "status": "interrupted", "status_label": "已中断", "paper_label": "论文B标题"},
        {"thread_id": "task-ccc", "status": "done", "status_label": "已完成", "paper_label": "论文C标题"},
    ]


def _make_controller(threads=None) -> MagicMock:
    c = MagicMock()
    c.list_threads.return_value = _threads() if threads is None else threads
    # _route_for_status 会调 interrupt_kind（awaiting 分支）——脚本化避免 Mock 干扰路由
    c.interrupt_kind.return_value = "dev_loop_failure"
    return c


def _run(controller: MagicMock):
    with patch("app._get_controller", return_value=controller):
        at = AppTest.from_string(_SCRIPT, default_timeout=15)
        at.run()
    return at


def _text(at: AppTest) -> str:
    parts: List[str] = []
    for coll in (at.title, at.caption, at.markdown, at.text, at.info, at.warning, at.error):
        parts.extend(str(getattr(el, "value", "")) for el in coll)
    return "\n".join(parts)


def _button_keys(at: AppTest) -> List[str]:
    return [getattr(b, "key", None) for b in getattr(at, "button", [])]


# ======================================================================
# CP-4.4-1 枚举渲染 + 边界（无删除/搜索/分页）
# ======================================================================
def test_cp_4_4_1_enumerates_rows_with_label_and_badge():
    at = _run(_make_controller())
    assert not at.exception, at.exception
    text = _text(at)
    assert "任务列表" in text
    # 每条含论文标识 + 状态徽标（中文 status_label）
    for label in ("论文A标题", "论文B标题", "论文C标题"):
        assert label in text
    for badge in ("等待输入", "已中断", "已完成"):
        assert badge in text
    assert "共 3 个任务" in text


def test_cp_4_4_1_empty_list_placeholder():
    at = _run(_make_controller(threads=[]))
    assert not at.exception, at.exception
    assert "暂无任务" in _text(at)


def test_cp_4_4_1_no_delete_search_pagination():
    """边界断言：页面无删除/搜索/分页功能（非目标 5）——检查**渲染后**元素，非源码字样。"""
    at = _run(_make_controller())
    assert not at.exception, at.exception
    # 无搜索框（text_input）
    assert len(list(getattr(at, "text_input", []))) == 0, "任务列表页不应有搜索框"
    # 按钮标签不含删除/搜索/分页（只有刷新/返回/挂回）
    labels = [str(getattr(b, "label", "")) for b in at.button]
    for lbl in labels:
        assert "删除" not in lbl and "分页" not in lbl and "搜索" not in lbl, f"越界按钮: {lbl}"


# ======================================================================
# CP-4.4-2 一键挂回（不调 resume_task 红线）
# ======================================================================
def test_cp_4_4_2_reattach_writes_state_and_routes_without_resume_task():
    controller = _make_controller()
    with patch("app._get_controller", return_value=controller):
        at = AppTest.from_string(_SCRIPT, default_timeout=15)
        at.run()
        assert "btn_reattach_task-bbb" in _button_keys(at)
        # 点击「已中断」任务的挂回按钮
        for b in at.button:
            if b.key == "btn_reattach_task-bbb":
                b.click()
                break
        at.run()
    # 挂回：写 thread_id + query_params + 路由（interrupted → 执行监控页）
    assert at.session_state["thread_id"] == "task-bbb"
    assert at.session_state["current_page"] == "execution"
    assert "task" in at.query_params
    # 红线：挂回绝不调 resume_task（不静默重放副作用节点，AC-S6-16）
    controller.resume_task.assert_not_called()


def test_cp_4_4_2_reattach_done_routes_to_report():
    controller = _make_controller()
    with patch("app._get_controller", return_value=controller):
        at = AppTest.from_string(_SCRIPT, default_timeout=15)
        at.run()
        for b in at.button:
            if b.key == "btn_reattach_task-ccc":
                b.click()
                break
        at.run()
    assert at.session_state["thread_id"] == "task-ccc"
    assert at.session_state["current_page"] == "report"  # done → 报告页
    controller.resume_task.assert_not_called()


# ======================================================================
# CP-4.4-3 不注册 autorefresh + 入口链接可达
# ======================================================================
def test_cp_4_4_3_no_autorefresh_registered():
    """频控断言：任务列表页不 import/调用 autorefresh（仅用户动作可改变）。"""
    # 扫实际 import 模块名（避免误伤 docstring 里"不注册 st_autorefresh"的说明字样）。
    assert "streamlit_autorefresh" not in _TASK_LIST_SRC, "任务列表页不得 import autorefresh"
    assert "st_autorefresh(" not in _TASK_LIST_SRC, "任务列表页不得调用 st_autorefresh"


def test_cp_4_4_3_entry_link_from_paper_input():
    """入口链接可达：paper_input 有导航到任务列表页（current_page='tasks'）的按钮。"""
    assert "btn_to_task_list" in _PAPER_INPUT_SRC
    assert '"tasks"' in _PAPER_INPUT_SRC


def test_cp_4_4_3_refresh_and_back_buttons_present():
    at = _run(_make_controller())
    keys = _button_keys(at)
    assert "btn_task_list_refresh" in keys
    assert "btn_task_list_to_input" in keys


# ======================================================================
# CP-4.4-4 R7 孤儿卡片「继续执行」接通 resume_task（批次 3 → 批次 4）
# ======================================================================
_EXEC_SCRIPT = """
import streamlit as st
st.session_state.setdefault("thread_id", "task-orphan")
st.session_state.setdefault("current_page", "execution")
from ui.pages.execution_monitor import render
render()
"""


def _make_exec_state():
    return {
        "current_step": "execution", "fix_loop_count": 0, "fix_loop_history": [],
        "execution_result": None, "node_errors": [], "degraded_nodes": [],
        "report_path": None, "error": None,
    }


def test_cp_4_4_4_r7_card_continue_button_calls_resume_task():
    """R7 孤儿卡片「继续执行」→ 显式调 resume_task（批次 3 卡片 + 批次 4 续跑接通）。"""
    controller = MagicMock()
    controller.poll_state.return_value = _make_exec_state()
    controller.get_worker_error.return_value = None
    controller.is_interrupted.return_value = False
    controller.is_finished.return_value = False
    controller.has_active_worker.return_value = False  # 无 worker → R7 孤儿
    controller.get_phase.return_value = {"active_node": "execution", "current_step": "execution"}
    controller.resume_task.return_value = True

    with patch("app._get_controller", return_value=controller):
        at = AppTest.from_string(_EXEC_SCRIPT, default_timeout=15)
        at.run()
        assert "btn_orphan_resume" in [getattr(b, "key", None) for b in at.button]
        for b in at.button:
            if b.key == "btn_orphan_resume":
                b.click()
                break
        at.run()
    controller.resume_task.assert_called_once_with("task-orphan")
