"""Sprint 5 任务 T-S5-3-6 单测：产物路径只读展示区（S5-11 / AC-S5-21，P2）。

覆盖 dev-plan §T-S5-3-6 检查点：
    - CP-3.6-1 两页渲染断言：路径展示区存在（区块标题「产物路径（可复制）」+
      st.code 块）、值来自 state 字段（code_output_dir / report_path 精确相等）、
      字段缺失时不崩（``.get()`` 防御 → 占位 caption，页面无 exception）；
    - 保护令守门（附带）：展示区仅在 case⑦ 正常渲染路径出现——interrupt#3 用户输入
      面板（含 T-S5-2-3 降级按钮共存态）不渲染该区，降级按钮零回归。
    - CP-3.6-2 手动 happy path 走查（AC-S5-21）**不在本文件**——留待批次 5 与
      测试工程师协作（本代理无法做浏览器手动走查）。

测试策略（沿用 sp3 E2/E3 + sp5 T-2-3 范式）：AppTest + mock GraphController
（patch("app._get_controller")）跑真实 render()；result_report 页当前唯一的
st.code 来源即本展示区，可对 at.code 做精确断言。

运行::

    .venv/bin/pytest tests/test_sprint5_t36_artifact_paths.py -q
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

from streamlit.testing.v1 import AppTest

# 展示区文案锚点（与两页 _render_artifact_paths_section 实现严格对齐，防漂移）。
_SECTION_HEADER = "产物路径（可复制）"
_CODE_DIR = "/data/myproj/auto_reproduction/workspace/2405.14831/code"
_REPORT_PATH = "/data/myproj/auto_reproduction/workspace/2405.14831/report.md"


# --------------------------------------------------------------------------- #
# 夹具：mock state / controller 工厂（沿用 test_sprint5_t23 范式）
# --------------------------------------------------------------------------- #
def _make_monitor_state(**overrides: Any) -> Dict[str, Any]:
    """执行监控页 case⑦ 正常渲染态的最小 state（current_step=execution，不触发跳转）。"""
    state: Dict[str, Any] = {
        "current_step": "execution",
        "fix_loop_count": 0,
        "fix_loop_history": [],
        "execution_result": None,
        "node_errors": [],
        "degraded_nodes": [],
        "report_path": None,
        "error": None,
    }
    state.update(overrides)
    return state


def _make_report_state(**overrides: Any) -> Dict[str, Any]:
    """结果报告页最小 state（degraded 形态兜底判定，不依赖 report 文件真实存在）。"""
    state: Dict[str, Any] = {
        "report_path": None,
        "execution_result": None,
        "fix_loop_count": 0,
        "fix_loop_history": [],
    }
    state.update(overrides)
    return state


def _make_monitor_controller(state: Optional[Dict[str, Any]]) -> MagicMock:
    """执行监控页 case⑦ controller：无 worker 异常、非 interrupt、未结束。"""
    controller = MagicMock()
    controller.poll_state.return_value = state
    controller.get_worker_error.return_value = None
    controller.is_interrupted.return_value = False
    controller.is_finished.return_value = False
    return controller


def _make_interrupt_controller(payload: Dict[str, Any], state: Dict[str, Any]) -> MagicMock:
    """执行监控页 interrupt#3 controller（保护令守门用例）。"""
    controller = MagicMock()
    controller.poll_state.return_value = state
    controller.get_worker_error.return_value = None
    controller.is_interrupted.return_value = True
    controller.interrupt_kind.return_value = "user_input_request"
    controller.get_interrupt_payload.return_value = payload
    return controller


def _make_report_controller(state: Optional[Dict[str, Any]]) -> MagicMock:
    controller = MagicMock()
    controller.poll_state.return_value = state
    return controller


# AppTest 脚本：按 current_page 路由防 st.rerun 无限循环（沿用 sp3 E2 / sp5 T-2-3 范式）。
_MONITOR_SCRIPT = """
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

_REPORT_SCRIPT = """
import streamlit as st
st.session_state.setdefault("thread_id", "task-report-001")
st.session_state.setdefault("current_page", "report")
page = st.session_state.get("current_page", "report")
if page == "report":
    from ui.pages.result_report import render
    render()
else:
    st.write("OTHER_STUB")
"""


def _run(script: str, controller: MagicMock) -> AppTest:
    with patch("app._get_controller", return_value=controller):
        at = AppTest.from_string(script)
        at.run()
    return at


def _code_values(at: AppTest) -> List[str]:
    return [str(c.value) for c in at.code]


def _caption_text(at: AppTest) -> str:
    return "\n".join(str(c.value) for c in at.caption)


def _page_text(at: AppTest) -> str:
    parts: List[str] = []
    for collection in (at.title, at.subheader, at.caption, at.markdown,
                       at.error, at.warning, at.info, at.code):
        parts.extend(str(el.value) for el in collection)
    return "\n".join(parts)


# =========================================================================== #
# CP-3.6-1（页 1/2）：结果报告页
# =========================================================================== #
def test_cp_3_6_1_report_page_shows_both_paths_from_state():
    """两字段齐备 → 展示区存在，st.code 值与 state 字段精确相等（值来自 state）。"""
    state = _make_report_state(code_output_dir=_CODE_DIR, report_path=_REPORT_PATH)
    at = _run(_REPORT_SCRIPT, _make_report_controller(state))
    assert not at.exception, at.exception
    assert _SECTION_HEADER in _page_text(at)
    codes = _code_values(at)
    # result_report 页当前唯一 st.code 来源即本展示区 → 可做精确断言。
    assert codes == [_CODE_DIR, _REPORT_PATH]


def test_cp_3_6_1_report_page_missing_fields_no_crash():
    """两字段均缺失（键不存在）→ .get() 防御不崩，渲染「未记录」占位。"""
    state = _make_report_state()
    state.pop("report_path", None)  # 键整体缺失（比 None 更苛刻的缺失形态）
    at = _run(_REPORT_SCRIPT, _make_report_controller(state))
    assert not at.exception, at.exception
    assert _SECTION_HEADER in _page_text(at)
    captions = _caption_text(at)
    assert "代码目录（code_output_dir）：（未记录）" in captions
    assert "报告文件（report_path）：（未记录）" in captions
    assert _code_values(at) == []  # 缺失不渲染 st.code 行


def test_cp_3_6_1_report_page_poll_state_none_no_crash():
    """poll_state 返回 None（render 内 or {} 兜底）→ 展示区占位不崩。"""
    at = _run(_REPORT_SCRIPT, _make_report_controller(None))
    assert not at.exception, at.exception
    assert _SECTION_HEADER in _page_text(at)
    assert "（未记录）" in _caption_text(at)


# =========================================================================== #
# CP-3.6-1（页 2/2）：执行监控页（case⑦ 正常渲染路径）
# =========================================================================== #
def test_cp_3_6_1_monitor_page_shows_both_paths_from_state():
    """case⑦ 两字段齐备（current_step=execution 不触发 case⑥ 跳转）→ st.code 值精确相等。"""
    state = _make_monitor_state(code_output_dir=_CODE_DIR, report_path=_REPORT_PATH)
    at = _run(_MONITOR_SCRIPT, _make_monitor_controller(state))
    assert not at.exception, at.exception
    assert _SECTION_HEADER in _page_text(at)
    codes = _code_values(at)
    assert _CODE_DIR in codes
    assert _REPORT_PATH in codes


def test_cp_3_6_1_monitor_page_partial_fields_placeholder():
    """coding 进行中常态：code_output_dir 有值、report_path 为 None → 一行 code + 一行占位。"""
    state = _make_monitor_state(code_output_dir=_CODE_DIR)  # report_path=None
    at = _run(_MONITOR_SCRIPT, _make_monitor_controller(state))
    assert not at.exception, at.exception
    codes = _code_values(at)
    assert _CODE_DIR in codes
    assert _REPORT_PATH not in codes
    assert "报告文件（report_path）：（尚未生成）" in _caption_text(at)


def test_cp_3_6_1_monitor_page_missing_fields_no_crash():
    """两字段均缺失（含键整体不存在）→ .get() 防御不崩，双占位 caption。"""
    state = _make_monitor_state()
    state.pop("report_path", None)
    at = _run(_MONITOR_SCRIPT, _make_monitor_controller(state))
    assert not at.exception, at.exception
    assert _SECTION_HEADER in _page_text(at)
    captions = _caption_text(at)
    assert "代码目录（code_output_dir）：（尚未生成）" in captions
    assert "报告文件（report_path）：（尚未生成）" in captions


def test_cp_3_6_1_monitor_page_empty_state_dict_no_crash():
    """poll_state 返回空 dict（极端缺失形态）→ 页面整体不崩，展示区占位。"""
    at = _run(_MONITOR_SCRIPT, _make_monitor_controller({}))
    assert not at.exception, at.exception
    assert _SECTION_HEADER in _page_text(at)


# =========================================================================== #
# 保护令守门（附带）：展示区仅在 case⑦，interrupt#3 面板零回归
# =========================================================================== #
def test_artifact_paths_absent_in_user_input_panel_and_degrade_button_intact():
    """interrupt#3（含 allow_degrade=True 五键 payload）→ 无产物路径区；降级按钮原样。"""
    payload = {
        "interrupt_kind": "user_input_request",
        "question": "复现计划声明需要凭证「env:OPENAI_API_KEY」，请提供该凭证。",
        "is_sensitive": True,
        "purpose_key": "env:OPENAI_API_KEY",
        "allow_degrade": True,
    }
    state = _make_monitor_state(
        current_step="coding", code_output_dir=_CODE_DIR, report_path=_REPORT_PATH
    )
    at = _run(_MONITOR_SCRIPT, _make_interrupt_controller(payload, state))
    assert not at.exception, at.exception
    # 展示区不进 interrupt 面板（保护令：只加独立展示区，面板一个字节不动）。
    assert _SECTION_HEADER not in _page_text(at)
    assert _CODE_DIR not in _code_values(at)
    # T-S5-2-3 降级按钮零回归。
    button_keys = [b.key for b in at.button]
    assert "btn_user_input_degrade" in button_keys
    assert "btn_user_input_submit" in button_keys
