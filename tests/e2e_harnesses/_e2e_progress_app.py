"""进度页 e2e harness app：按 E2E_SCENE 环境变量注入不同终态 state，render 进度页。

供 tests/test_analysis_progress_e2e.py 用 Playwright 起真浏览器读 iframe 内 ui.alert /
ui.accordion 文本（AppTest 看不到 iframe，故终态文本断言迁到 e2e）。

场景（E2E_SCENE）对应 analysis_progress.render() 终态判定链：
  - worker_error : get_worker_error 返回 RuntimeError("WORKER-BOOM") → _render_fatal_worker_error
                   （iframe ui.alert title「工作线程异常」+ st.code "WORKER-BOOM"）
  - state_error  : poll_state.error="STATE-ERR" → _render_fatal_state_error
                   （iframe ui.alert title「任务发生致命错误」+ description "STATE-ERR"）
  - cancelled    : poll_state.current_step="cancelled_by_user" → _render_cancelled_card
                   （iframe ui.alert title「任务已终止」）
  - many_errors  : 8 条 node_errors（正常渲染路径）→ _render_logs 的 ui.accordion 8 段不崩
"""
import os
import threading
from unittest.mock import MagicMock

import streamlit as st
import app as _app

SCENE = os.environ.get("E2E_SCENE", "cancelled")
TID = "tid-progress-e2e"


def _make_state(current_step="paper_analysis", error=None, node_errors=None):
    return {
        "current_step": current_step,
        "degraded_nodes": [],
        "error": error,
        "node_errors": node_errors if node_errors is not None else [],
        "paper_meta": {"arxiv_id": "2405.14831", "title": "HippoRAG", "title_zh": "中文标题"},
    }


ctrl = MagicMock()
# 默认非终态行为；下面按场景覆盖。
ctrl.get_worker_error.return_value = None
ctrl.is_interrupted.return_value = False

if SCENE == "worker_error":
    ctrl.get_worker_error.return_value = RuntimeError("WORKER-BOOM")
    ctrl.poll_state.return_value = _make_state(current_step="cancelled_by_user", error="state-err")
    ctrl.is_interrupted.return_value = True
elif SCENE == "state_error":
    ctrl.poll_state.return_value = _make_state(current_step="cancelled_by_user", error="STATE-ERR")
    ctrl.is_interrupted.return_value = True
elif SCENE == "cancelled":
    ctrl.poll_state.return_value = _make_state(current_step="cancelled_by_user")
elif SCENE == "many_errors":
    ctrl.poll_state.return_value = _make_state(
        current_step="resource_scout",
        node_errors=[
            {"node_name": "resource_scout", "error_type": "degraded",
             "error_message": "克隆失败 %d" % i, "error_detail": "detail-%d" % i}
            for i in range(8)
        ],
    )
else:
    ctrl.poll_state.return_value = _make_state()

_app._get_controller = lambda: ctrl

# 屏蔽 st_autorefresh：autorefresh 注册会让 e2e 页面反复刷新干扰断言（终态本就不该注册，
# 但 many_errors 是正常路径会注册——e2e 里把它替成 no-op 避免页面跳动）。
import ui.pages.analysis_progress as _ap
_ap.st_autorefresh = lambda *a, **k: None

st.session_state.setdefault("thread_id", TID)
st.session_state.setdefault("current_page", "progress")

_ap.render()
