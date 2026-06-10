"""D4 分析进度页 L2 集成测试（test plan 2026-06-07 §L2，7 项）。

与 L1（test_analysis_progress.py 纯 MagicMock）互补：本层用**真实读路径对接**，
降低纯 mock 的盲区——
  - I1：真实 GraphController + 真实 SqliteSaver(tmp_path) 预置 checkpoint，验 poll_state
        真读路径读出的 current_step 与页面渲染态一致（非 MagicMock 桩）。
  - I2：thread_id 经 session_state 透传 poll_state（非硬编码）。
  - I3：current_page 跨 rerun 流转（is_interrupted 第 2 次 run 才 True → 切 review）。
  - I4/I5/I6：三类终态停轮询（error / cancelled / interrupted）→ st_autorefresh 不注册。
  - I7：同一非终态 state 连 run 3 次幂等（无累积污染、无重复 widget key 异常）。

纯 mock / 真实 SqliteSaver（tmp_path 库），**不烧 token、不连网、不写默认库**。
真实 GraphController 的 poll_state/is_interrupted 走真实 langgraph get_state 读路径。
"""
from __future__ import annotations

from typing import Dict, List, Optional
from unittest.mock import MagicMock, patch

import pytest
from streamlit.testing.v1 import AppTest


# --------------------------------------------------------------------------- #
# AppTest 脚本（与 L1 同款路由，progress 调 render，review/input 渲染 stub 跳出循环）
# --------------------------------------------------------------------------- #
_APP_SCRIPT = """
import streamlit as st
st.session_state.setdefault("thread_id", "task-int-001")
st.session_state.setdefault("current_page", "progress")
page = st.session_state.get("current_page", "progress")
if page == "progress":
    from ui.pages.analysis_progress import render
    render()
elif page == "review":
    st.write("REVIEW_STUB")
else:
    st.write("INPUT_STUB")
"""


def _make_state(
    current_step: str = "paper_analysis",
    degraded_nodes: Optional[List[str]] = None,
    error: Optional[str] = None,
    node_errors: Optional[List[Dict]] = None,
    paper_meta: Optional[Dict] = None,
) -> Dict:
    return {
        "current_step": current_step,
        "degraded_nodes": degraded_nodes if degraded_nodes is not None else [],
        "error": error,
        "node_errors": node_errors if node_errors is not None else [],
        "paper_meta": paper_meta,
    }


def _collect_text(at: AppTest) -> str:
    parts: List[str] = []
    for collection in (at.title, at.subheader, at.caption, at.markdown, at.text,
                       at.warning, at.info, at.error):
        for el in collection:
            parts.append(str(getattr(el, "value", "")))
    for el in getattr(at, "code", []):
        parts.append(str(getattr(el, "value", "")))
    return "\n".join(parts)


# =========================================================================== #
# I1：真实 GraphController + 真实 SqliteSaver(tmp_path) 预置 checkpoint，真读路径
# =========================================================================== #
def test_i1_real_sqlite_saver_poll_state_roundtrip(tmp_path):
    """T-D4-I1：真实 SqliteSaver(tmp_path) 写入 state → 真实 GraphController.poll_state 读出一致。

    不跑 graph 节点（不烧 token）：直接用真实 SqliteSaver.put 预置一个 checkpoint，
    再用 GraphController（main_graph 指向同一 tmp 库）的真实 poll_state 读回，证明
    页面消费的 poll_state 不是 MagicMock 桩，而是真实 langgraph get_state 读路径。
    """
    from core.checkpointer import get_checkpointer
    from core.graph import build_graph
    import app as app_module

    db_path = str(tmp_path / "ckpt_int.db")
    thread_id = "task-int-roundtrip"

    # 真实 GraphController，但把主线程读图指向 tmp 库（不碰默认 checkpoints.db）。
    controller = app_module.GraphController.__new__(app_module.GraphController)
    import threading
    controller._lock = threading.Lock()
    controller._workers = {}
    controller._worker_errors = {}
    controller._main_checkpointer = get_checkpointer(db_path)
    controller._main_graph = build_graph(checkpointer=controller._main_checkpointer)

    # 用一个独立 saver + graph 写入一个 checkpoint（模拟工作线程写，跑到 paper_analysis）。
    writer_saver = get_checkpointer(db_path)
    writer_graph = build_graph(checkpointer=writer_saver)
    cfg = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
    seeded = _make_state(
        current_step="paper_analysis",
        paper_meta={"arxiv_id": "2405.14831", "title": "HippoRAG", "title_zh": "中文标题"},
    )
    # update_state 直接落一个 checkpoint（不触发节点执行 → 不烧 token）。
    writer_graph.update_state(cfg, seeded)

    # 真实 poll_state 读路径读回
    read_state = controller.poll_state(thread_id)
    assert read_state is not None, "真实 SqliteSaver 读不到预置 checkpoint"
    assert read_state.get("current_step") == "paper_analysis", "真读路径 current_step 不一致"
    assert read_state.get("paper_meta", {}).get("title_zh") == "中文标题"

    # 把该真实 controller 注入页面，验渲染态与真实 checkpoint 一致
    with patch("app._get_controller", return_value=controller), patch(
            "ui.pages.analysis_progress.st_autorefresh") as ar:
        script = _APP_SCRIPT.replace("task-int-001", thread_id)
        at = AppTest.from_string(script)
        at.run()
    assert not at.exception, at.exception
    text = _collect_text(at)
    assert "● 进行中" in text, "paper_analysis 段应运行中（真实 checkpoint 驱动）"
    assert "✓ 完成" in text, "paper_intake 段应已完成"
    assert "中文标题" in text, "真实 checkpoint 的 title_zh 应渲染"
    ar.assert_called_once()  # 非终态 → 注册轮询


# =========================================================================== #
# I2：thread_id 经 session_state 透传 poll_state（非硬编码）
# =========================================================================== #
def test_i2_thread_id_passthrough_from_session_state():
    """T-D4-I2：页面用 session_state["thread_id"] 调 poll_state（透传正确，非硬编码）。"""
    controller = MagicMock()
    controller.get_worker_error.return_value = None
    controller.poll_state.return_value = _make_state()
    controller.is_interrupted.return_value = False

    custom_tid = "task-CUSTOM-xyz-789"
    with patch("app._get_controller", return_value=controller), patch(
            "ui.pages.analysis_progress.st_autorefresh"):
        script = _APP_SCRIPT.replace("task-int-001", custom_tid)
        at = AppTest.from_string(script)
        at.run()
    assert not at.exception
    # poll_state / get_worker_error / is_interrupted 均应用 session_state 里的 thread_id
    controller.get_worker_error.assert_called_with(custom_tid)
    controller.poll_state.assert_called_with(custom_tid)
    controller.is_interrupted.assert_called_with(custom_tid)


# =========================================================================== #
# I3：current_page 跨 rerun 流转（第 2 次 run 才 interrupt → 切 review）
# =========================================================================== #
def test_i3_current_page_transition_across_reruns():
    """T-D4-I3：第 1 次 run 停 progress；第 2 次 run interrupt → current_page 切 review。"""
    controller = MagicMock()
    controller.get_worker_error.return_value = None
    controller.poll_state.return_value = _make_state(current_step="planning")
    # 第 1 次 False，第 2 次 True（模拟工作线程跑到 interrupt）
    controller.is_interrupted.side_effect = [False, True, True, True]

    with patch("app._get_controller", return_value=controller), patch(
            "ui.pages.analysis_progress.st_autorefresh"):
        at = AppTest.from_string(_APP_SCRIPT)
        at.run()  # 第 1 次：非终态，停 progress
        assert not at.exception
        assert at.session_state["current_page"] == "progress"
        # 第 2 次：interrupt → 切 review
        at.run()
        assert not at.exception
        assert at.session_state["current_page"] == "review"


# =========================================================================== #
# I4/I5/I6：三类终态停轮询 → st_autorefresh 不注册
# =========================================================================== #
@pytest.mark.parametrize(
    "scene_state,scene_interrupted,scene_worker_error,marker",
    [
        (_make_state(current_step="paper_analysis", error="LLM 不可用"), False, None, "致命错误"),
        (_make_state(current_step="cancelled_by_user"), False, None, "任务已终止"),
        (_make_state(current_step="planning"), True, None, None),
    ],
    ids=["I4_error", "I5_cancelled", "I6_interrupted"],
)
def test_i456_terminal_states_stop_polling(scene_state, scene_interrupted, scene_worker_error, marker):
    """T-D4-I4/I5/I6：error / cancelled / interrupted 三类终态 → st_autorefresh 不注册（停轮询）。"""
    controller = MagicMock()
    controller.get_worker_error.return_value = scene_worker_error
    controller.poll_state.return_value = scene_state
    controller.is_interrupted.return_value = scene_interrupted

    with patch("app._get_controller", return_value=controller), patch(
            "ui.pages.analysis_progress.st_autorefresh") as ar:
        at = AppTest.from_string(_APP_SCRIPT)
        at.run()
    assert not at.exception
    if marker:
        assert marker in _collect_text(at)
    ar.assert_not_called()  # 终态停轮询的核心


# =========================================================================== #
# I7：同一非终态 state 连 run 3 次幂等（无累积污染、无重复 widget key 异常）
# =========================================================================== #
def test_i7_repeated_rerun_idempotent():
    """T-D4-I7：同一非终态 state 连 run 3 次 → 每次无异常、渲染稳定、无重复 key 报错。"""
    controller = MagicMock()
    controller.get_worker_error.return_value = None
    controller.poll_state.return_value = _make_state(
        current_step="resource_scout",
        degraded_nodes=["paper_intake"],
        node_errors=[
            {"node_name": "resource_scout", "error_type": "degraded",
             "error_message": "msg-A", "error_detail": "detail-A"},
            {"node_name": "resource_scout", "error_type": "degraded",
             "error_message": "msg-B", "error_detail": "detail-B"},
        ],
        paper_meta={"arxiv_id": "2405.14831", "title": "X", "title_zh": "标题"},
    )
    controller.is_interrupted.return_value = False

    with patch("app._get_controller", return_value=controller), patch(
            "ui.pages.analysis_progress.st_autorefresh"):
        at = AppTest.from_string(_APP_SCRIPT)
        prev_text = None
        for _ in range(3):
            at.run()
            assert not at.exception, "rerun 出现异常（可能重复 widget key）"
            text = _collect_text(at)
            assert "复现进度" in text
            assert "✓ 完成（降级）" in text
            if prev_text is not None:
                assert text == prev_text, "多次 rerun 渲染不一致（state 累积污染）"
            prev_text = text


# =========================================================================== #
# 终态优先级链严格序补强（架构师点名 test plan 缺口：超过开发的两两交叉，做四态全交叉）
# =========================================================================== #
def _mk_chain_ctrl(state=None, interrupted=False, worker_error=None):
    c = MagicMock()
    c.poll_state.return_value = state
    c.is_interrupted.return_value = interrupted
    c.get_worker_error.return_value = worker_error
    return c


def _run_chain(controller):
    with patch("app._get_controller", return_value=controller), patch(
            "ui.pages.analysis_progress.st_autorefresh") as ar:
        at = AppTest.from_string(_APP_SCRIPT)
        at.run()
    return at, ar


def test_priority_all_four_terminal_true_picks_worker_error():
    """四态全为真（worker_error∧error∧cancelled∧interrupted）→ 严格命中最高优先 worker_error。

    超过开发的两两交叉：构造全交叉态实证链首端短路——worker_error 在 poll_state 之前判定，
    命中即 return，poll_state / is_interrupted 均不应被调用。
    """
    controller = _mk_chain_ctrl(
        state=_make_state(current_step="cancelled_by_user", error="state-err"),
        interrupted=True,
        worker_error=RuntimeError("WORKER-BOOM"),
    )
    at, ar = _run_chain(controller)
    assert not at.exception
    text = _collect_text(at)
    assert "工作线程异常" in text and "WORKER-BOOM" in text
    assert "state-err" not in text and "任务已终止" not in text
    controller.poll_state.assert_not_called()
    controller.is_interrupted.assert_not_called()
    ar.assert_not_called()


def test_priority_error_over_cancelled_and_interrupted():
    """error∧cancelled∧interrupted（无 worker_error）→ 命中 error；is_interrupted 不应被调用。"""
    controller = _mk_chain_ctrl(
        state=_make_state(current_step="cancelled_by_user", error="STATE-ERR"),
        interrupted=True,
    )
    at, ar = _run_chain(controller)
    assert not at.exception
    text = _collect_text(at)
    assert "致命错误" in text and "STATE-ERR" in text
    assert "任务已终止" not in text
    controller.is_interrupted.assert_not_called()
    ar.assert_not_called()


# =========================================================================== #
# 同 label 多 expander DuplicateElementId 回归守门（L3 真机已实证不崩，这里 AppTest 守门）
# =========================================================================== #
def test_many_same_label_detail_expanders_no_exception():
    """8 条 node_errors 均含 error_detail → 8 个同 label '详情' expander，AppTest 不抛异常。

    BUG-S2-D3-01 同型家族守门（重复 element id）。L3 真机 streamlit run 已实证 streamlit 1.58
    自动消歧不崩；本用例作 AppTest 层回归守门（截断后最多渲染 10 条 detail）。
    """
    controller = _mk_chain_ctrl(
        state=_make_state(
            current_step="resource_scout",
            node_errors=[
                {"node_name": "resource_scout", "error_type": "degraded",
                 "error_message": "克隆失败 %d" % i, "error_detail": "detail-%d" % i}
                for i in range(8)
            ],
        ),
    )
    at, ar = _run_chain(controller)
    assert not at.exception, "多个同 label '详情' expander 触发异常（重复 element id 隐患）"
    # 8 个 detail expander 全部渲染（<=10 截断阈值内）
    assert len(getattr(at, "expander", [])) == 8
    ar.assert_called_once()
