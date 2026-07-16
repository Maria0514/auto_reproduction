"""Sprint 6 批次 4 · T-S6-4-2：paper_input 写/清 query params（S6-06，架构 §7.6）。

覆盖 dev-plan §4 批次 4 检查点：
    - CP-4.2-1 start_task 成功后 URL query params 含 `task=<thread_id>`（写入面，AC-S6-14）；
    - CP-4.2-2 "返回输入页开启新任务"清除 task 参数（清除后 _restore_from_query_params 不再激活）。

测试范式：
    - 写入路径在 `ui.button`（shadcn iframe）点击回调内，AppTest 看不到 iframe 点击（沿本页
      test_paper_input_logic 既有裁决）→ CP-4.2-1 用**源码扫描**固化写入语句在 start_task
      成功块内（写入面浏览器 e2e 由批次 5 覆盖）；
    - 清除逻辑在 render() 顶部（button 之前）→ CP-4.2-2 用 AppTest 直跑 render() 断言。
"""

from __future__ import annotations

import re
from pathlib import Path

from streamlit.testing.v1 import AppTest

_SRC = Path("ui/pages/paper_input.py").read_text(encoding="utf-8")

_RENDER_SCRIPT = """
import streamlit as st
st.session_state.setdefault("current_page", "input")
from ui.pages.paper_input import render
render()
"""


# ======================================================================
# CP-4.2-1 start_task 成功写 query params（源码扫描）
# ======================================================================
def test_cp_4_2_1_start_task_writes_task_query_param():
    """start_task 成功块内写 st.query_params["task"] = thread_id（写入面，源码固化）。

    定位 `controller.start_task(...)` 之后到 `st.rerun()` 之间必含 query_params 写入语句。
    """
    # 抓 start_task 调用到 rerun 之间的块
    m = re.search(
        r"thread_id\s*=\s*controller\.start_task\(.*?\)(.*?)st\.rerun\(\)",
        _SRC, re.DOTALL,
    )
    assert m, "未定位到 start_task 成功块"
    block = m.group(1)
    assert 'st.query_params["task"] = thread_id' in block, (
        "start_task 成功后必须写 st.query_params['task']=thread_id（URL 持久化，CP-4.2-1）"
    )


# ======================================================================
# CP-4.2-2 清除 task 参数（AppTest 直跑 render）
# ======================================================================
def _run_render(*, task=None, thread_id=None):
    at = AppTest.from_string(_RENDER_SCRIPT, default_timeout=15)
    at.session_state["thread_id"] = thread_id
    if task is not None:
        at.query_params["task"] = task
    at.run()
    return at


def test_cp_4_2_2_clears_stale_task_when_no_active_thread():
    """无活动任务（thread_id 空）+ URL 残留 task → render 顶部清除（CP-4.2-2）。"""
    at = _run_render(task="task-stale", thread_id=None)
    assert not at.exception, at.exception
    assert "task" not in at.query_params, "无活动任务时 stale task 参数应被清除"


def test_cp_4_2_2_keeps_task_when_active_thread_present():
    """有活动任务（thread_id 非空）→ 不清除 task 参数（活动任务保留 URL 持久化）。"""
    at = _run_render(task="task-active", thread_id="task-active")
    assert not at.exception, at.exception
    assert "task" in at.query_params, "活动任务的 task 参数不应被清除"
    # AppTest query_params 值可能是 list（多值语义）或 str，两种形态都容纳。
    val = at.query_params["task"]
    assert "task-active" in (val if isinstance(val, list) else [val])


def test_cp_4_2_2_no_task_param_no_crash():
    """无 task 参数 + 无活动任务 → 清除逻辑空跑不炸。"""
    at = _run_render(task=None, thread_id=None)
    assert not at.exception, at.exception
    assert "task" not in at.query_params
