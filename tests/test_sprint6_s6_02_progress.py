"""Sprint 6 批次 3 · T-S6-3-4：analysis_progress 在途切页 + 段状态（S6-02）。

覆盖 dev-plan §4 批次 3 检查点：
    - CP-3.4-1 case④bis：current_step='planning' ∧ active_node='coding' → 切执行监控页
      （AC-S6-05 approve 后在途切页）；planning interrupt（is_interrupted）不误切（case④ 先分发）；
    - CP-3.4-2 _segment_status：active_node 命中上游节点 → 该段"进行中"（AC-S6-04），
      **只向前升级不向后降级**（node_idx >= cur_idx 守卫，防 update_state 不一致快照误降级）。

测试策略：_segment_status 纯函数直测 + case④bis 页面级 AppTest（mock get_phase 脚本化 active_node）。
"""

from __future__ import annotations

import importlib
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

from streamlit.testing.v1 import AppTest

_mod = importlib.import_module("ui.pages.analysis_progress")
_segment_status = _mod._segment_status


# =========================================================================== #
# CP-3.4-2 _segment_status active_node override（纯函数）
# =========================================================================== #
def test_cp_3_4_2_active_node_upgrades_pending_to_running():
    """滞后修正：current_step='paper_intake' 但 active_node='paper_analysis'（在途）→ 该段 running。"""
    # 无 active_node：paper_analysis 依 current_step 推成 pending（滞后）。
    assert _segment_status("paper_intake", "paper_analysis", []) == "pending"
    # 有 active_node='paper_analysis'（node_idx1 >= cur_idx0）→ 升级 running。
    assert _segment_status("paper_intake", "paper_analysis", [], "paper_analysis") == "running"


def test_cp_3_4_2_active_node_does_not_downgrade_done_segment():
    """守卫：active_node 落在 current_step **之前**（node_idx < cur_idx）→ 不把已完成段降级。

    防 update_state 造出的不一致快照（current_step 领先、next 却回到图入口）误降级历史段。
    """
    # current_step='resource_scout'(idx2)，active_node='paper_intake'(idx0 < 2)：
    # paper_intake 已完成，不得被降级为 running。
    assert _segment_status("resource_scout", "paper_intake", [], "paper_intake") == "done"
    # paper_analysis(idx1 < 2) 同理仍 done。
    assert _segment_status("resource_scout", "paper_analysis", [], "paper_intake") == "done"


def test_cp_3_4_2_active_node_none_falls_back_to_index_logic():
    """active_node 缺省 None → 完全回落既有索引逻辑（既有单测语义不变）。"""
    assert _segment_status("paper_analysis", "paper_intake", []) == "done"
    assert _segment_status("paper_analysis", "paper_analysis", []) == "running"
    assert _segment_status("paper_analysis", "resource_scout", []) == "pending"
    # 已完成 + degraded 优先级不被 active_node=None 干扰。
    assert _segment_status("resource_scout", "paper_intake", ["paper_intake"]) == "degraded"


def test_cp_3_4_2_active_node_matches_current_step_still_running():
    """active_node == current_step（node_idx == cur_idx）→ running（与索引逻辑一致，不冲突）。"""
    assert _segment_status("paper_analysis", "paper_analysis", [], "paper_analysis") == "running"


# =========================================================================== #
# CP-3.4-1 case④bis 在途切页（页面级）
# =========================================================================== #
_PROGRESS_SCRIPT = """
import streamlit as st
st.session_state.setdefault("thread_id", "task-prog-001")
st.session_state.setdefault("current_page", "progress")
page = st.session_state.get("current_page", "progress")
if page == "progress":
    from ui.pages.analysis_progress import render
    render()
elif page == "execution":
    st.write("EXECUTION_STUB")   # 切到执行监控页（跳出 rerun 循环）
elif page == "review":
    st.write("REVIEW_STUB")
else:
    st.write("INPUT_STUB")
"""


def _make_state(current_step: str, **overrides: Any) -> Dict[str, Any]:
    state: Dict[str, Any] = {
        "current_step": current_step,
        "degraded_nodes": [],
        "error": None,
        "node_errors": [],
        "paper_meta": None,
        "report_path": None,
    }
    state.update(overrides)
    return state


def _make_controller(
    *,
    state: Dict[str, Any],
    is_interrupted: bool = False,
    interrupt_payload: Optional[Dict[str, Any]] = None,
    phase: Optional[Dict[str, Any]] = None,
) -> MagicMock:
    c = MagicMock()
    c.poll_state.return_value = state
    c.is_interrupted.return_value = is_interrupted
    c.get_worker_error.return_value = None
    c.get_interrupt_payload.return_value = interrupt_payload
    c.get_phase.return_value = phase if phase is not None else {"active_node": None, "current_step": None}
    return c


def _run(controller: MagicMock):
    with patch("app._get_controller", return_value=controller), patch(
        "ui.pages.analysis_progress.st_autorefresh"
    ) as ar:
        at = AppTest.from_string(_PROGRESS_SCRIPT)
        at.run()
    return at, ar


def _text(at: AppTest) -> str:
    parts: List[str] = []
    for coll in (at.title, at.caption, at.markdown, at.text, at.warning, at.info, at.error):
        parts.extend(str(getattr(el, "value", "")) for el in coll)
    return "\n".join(parts)


def _current_page(at: AppTest) -> str:
    return at.session_state["current_page"]


def test_cp_3_4_1_switch_to_execution_when_active_node_coding_but_step_planning():
    """AC-S6-05：approve 后 coding 在途（current_step 仍滞后为 'planning'）→ 切执行监控页。"""
    c = _make_controller(
        state=_make_state("planning"),
        is_interrupted=False,
        phase={"active_node": "coding", "current_step": "planning"},
    )
    at, ar = _run(c)
    assert not at.exception, at.exception
    assert _current_page(at) == "execution", "active_node=coding → 切执行监控页"
    assert "EXECUTION_STUB" in _text(at)
    ar.assert_not_called()  # 切页不注册本页轮询


def test_cp_3_4_1_switch_to_execution_when_active_node_execution():
    c = _make_controller(
        state=_make_state("planning"),
        is_interrupted=False,
        phase={"active_node": "execution", "current_step": "planning"},
    )
    at, _ = _run(c)
    assert not at.exception, at.exception
    assert _current_page(at) == "execution"


def test_cp_3_4_1_planning_interrupt_not_mis_switch():
    """planning interrupt（is_interrupted=True）→ case④ 先分发跳 review，不被 case④bis 误切执行页。"""
    c = _make_controller(
        state=_make_state("planning"),
        is_interrupted=True,
        interrupt_payload={"interrupt_kind": "planning"},
        phase={"active_node": "planning", "current_step": "planning"},
    )
    at, _ = _run(c)
    assert not at.exception, at.exception
    assert _current_page(at) == "review", "planning interrupt → review 页，不误切执行页"


def test_cp_3_4_1_upstream_running_no_switch():
    """上游在途（active_node=paper_analysis，非 coding/execution）→ 不切页，正常渲染进度。"""
    c = _make_controller(
        state=_make_state("paper_analysis"),
        is_interrupted=False,
        phase={"active_node": "paper_analysis", "current_step": "paper_analysis"},
    )
    at, ar = _run(c)
    assert not at.exception, at.exception
    assert _current_page(at) == "progress", "上游在途不切执行页"
    assert "复现进度" in _text(at)
    ar.assert_called_once()  # 正常渲染注册轮询
