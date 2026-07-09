"""Sprint 5 任务 T-S5-2-3 单测：execution_monitor 显式降级按钮（S5-01 / Q-S5-10）。

覆盖 dev-plan §T-S5-2-3 检查点：
    - CP-2.3-1：allow_degrade 有/无两态渲染分支——gate 五键 payload（四键 +
      allow_degrade=True）→ 渲染按钮「无此凭证，降级为模拟实验」；agent 工具路径
      四键 payload / 老 payload（无键）→ 无按钮（红线的 UI 面：agent 路径永不出现
      降级按钮）；键值非严格 True（False / truthy 字符串）同样无按钮；
    - CP-2.3-2：点击降级 → resume 三键契约 {"value": "", "remember": False,
      "degrade": True} 经 resume_with 透传；固定值不受输入框内容 / 「记住」勾选
      状态影响（一次点击只降当前询问项）；降级不做非空校验（空输入框可直接点）；
    - CP-2.3-3：既有 user_input_request 面板零回归——五键 payload 下普通提交仍为
      两键契约（degrade 不出现）、敏感 password 单输入框 + 「记住」勾选（默认不勾）
      不变、空值提交仍被拒（L-B1-01 防线）。
      （case⑥bis / case⑥ 等页面级零回归由既有 test_sprint5_s5_08_routing /
      test_sprint3_e2 系列 / test_sprint4_f1 全量复跑收口，不在本文件重复。）

测试策略（沿用 sp4 F1 范式）：纯函数直测 + AppTest + mock GraphController
（patch("app._get_controller")）跑真实 render()，原生组件可见可点。

运行::

    .venv/bin/pytest tests/test_sprint5_t23_degrade_button.py -q
"""

from __future__ import annotations

import importlib
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

from streamlit.proto.TextInput_pb2 import TextInput as TextInputProto
from streamlit.testing.v1 import AppTest

# 降级按钮 key / resume 三键契约字面量（与页面实现严格对齐，防漂移锚点）。
_BTN_DEGRADE_KEY = "btn_user_input_degrade"
_DEGRADE_RESUME = {"value": "", "remember": False, "degrade": True}


def _mod():
    """importlib 取模块（避免 __init__ 显式 export 遮蔽子模块的已知坑，见 CLAUDE 坑6）。"""
    return importlib.import_module("ui.pages.execution_monitor")


# --------------------------------------------------------------------------- #
# 夹具：mock state / payload / controller 工厂（沿用 test_sprint4_f1 范式）
# --------------------------------------------------------------------------- #
def _make_state(current_step: str = "coding") -> Dict[str, Any]:
    """构造 GlobalState 形态 dict（本页消费字段的最小集）。"""
    return {
        "current_step": current_step,
        "fix_loop_count": 0,
        "fix_loop_history": [],
        "execution_result": None,
        "node_errors": [],
        "degraded_nodes": [],
        "report_path": None,
        "error": None,
    }


def _make_gate_payload(**overrides: Any) -> Dict[str, Any]:
    """gate 路径五键 payload（T-S5-2-2 契约：四键 + allow_degrade=True）。"""
    payload: Dict[str, Any] = {
        "interrupt_kind": "user_input_request",
        "question": "复现计划声明需要凭证「env:OPENAI_API_KEY」，请提供该凭证。",
        "is_sensitive": True,
        "purpose_key": "env:OPENAI_API_KEY",
        "allow_degrade": True,
    }
    payload.update(overrides)
    return payload


def _make_agent_payload() -> Dict[str, Any]:
    """agent 工具路径四键 payload（interaction_tools 契约，永不含 allow_degrade）。"""
    return {
        "interrupt_kind": "user_input_request",
        "question": "请提供 github.com 的访问令牌（用于 clone 私有仓库）",
        "is_sensitive": True,
        "purpose_key": "git_credential:github.com",
    }


def _make_controller_mock(
    *,
    state: Optional[Dict[str, Any]] = None,
    interrupt_payload: Optional[Dict[str, Any]] = None,
) -> MagicMock:
    controller = MagicMock()
    controller.poll_state.return_value = state or _make_state("coding")
    controller.is_interrupted.return_value = True
    controller.interrupt_kind.return_value = "user_input_request"
    controller.get_interrupt_payload.return_value = interrupt_payload
    controller.get_worker_error.return_value = None
    return controller


# AppTest 脚本：按 current_page 路由，防 st.rerun 无限循环（沿用 sp3 E2 / sp4 F1 范式）。
_SCRIPT = """
import streamlit as st
st.session_state.setdefault("thread_id", "task-exec-001")
st.session_state.setdefault("current_page", "execution")
page = st.session_state.get("current_page", "execution")
if page == "execution":
    from ui.pages.execution_monitor import render
    render()
elif page == "review":
    st.write("REVIEW_STUB")
else:
    st.write("OTHER_STUB")
"""


def _run(controller: MagicMock) -> AppTest:
    with patch("app._get_controller", return_value=controller):
        at = AppTest.from_string(_SCRIPT)
        at.run()
    return at


def _button_keys(at: AppTest) -> List[Optional[str]]:
    return [b.key for b in at.button]


# =========================================================================== #
# 纯函数：降级 resume 三键契约（CP-2.3-2 契约锚点）
# =========================================================================== #
def test_build_degrade_resume_contract_pure():
    """恰三键 {"value", "remember", "degrade"}，固定值 ""/False/True。"""
    mod = _mod()
    payload = mod._build_degrade_resume()
    assert payload == _DEGRADE_RESUME
    assert set(payload.keys()) == {"value", "remember", "degrade"}
    assert payload["value"] == ""
    assert payload["remember"] is False
    assert payload["degrade"] is True


# =========================================================================== #
# CP-2.3-1：allow_degrade 有/无两态渲染分支
# =========================================================================== #
def test_cp_2_3_1_gate_payload_renders_degrade_button():
    """gate 五键 payload → 降级按钮渲染（文案与架构 §6 一致）。"""
    controller = _make_controller_mock(interrupt_payload=_make_gate_payload())
    at = _run(controller)
    assert not at.exception, at.exception
    assert _BTN_DEGRADE_KEY in _button_keys(at)
    btn = at.button(key=_BTN_DEGRADE_KEY)
    assert btn.label == "无此凭证，降级为模拟实验"
    # 面板本体仍在（问题正文 + 提交按钮），降级按钮是纯增量。
    assert "btn_user_input_submit" in _button_keys(at)


def test_cp_2_3_1_agent_payload_no_degrade_button():
    """agent 工具路径四键 payload（无 allow_degrade 键）→ 无按钮（红线的 UI 面）。"""
    controller = _make_controller_mock(interrupt_payload=_make_agent_payload())
    at = _run(controller)
    assert not at.exception, at.exception
    assert _BTN_DEGRADE_KEY not in _button_keys(at)
    # 面板本体（提交按钮）正常渲染，仅缺降级按钮。
    assert "btn_user_input_submit" in _button_keys(at)


def test_cp_2_3_1_non_true_allow_degrade_no_button():
    """键存在但非严格 True（False / truthy 字符串）→ 无按钮（`is True` 严格判定）。"""
    for bad_value in (False, "yes", 1, None):
        controller = _make_controller_mock(
            interrupt_payload=_make_gate_payload(allow_degrade=bad_value)
        )
        at = _run(controller)
        assert not at.exception, at.exception
        assert _BTN_DEGRADE_KEY not in _button_keys(at), f"allow_degrade={bad_value!r}"


# =========================================================================== #
# CP-2.3-2：点击降级 → resume 三键契约经 resume_with 透传
# =========================================================================== #
def test_cp_2_3_2_click_degrade_resumes_with_three_key_contract():
    """空输入框直接点降级 → resume_with 三键固定契约（降级不做非空校验）。"""
    controller = _make_controller_mock(interrupt_payload=_make_gate_payload())
    with patch("app._get_controller", return_value=controller):
        at = AppTest.from_string(_SCRIPT)
        at.run()
        at.button(key=_BTN_DEGRADE_KEY).click().run()
    controller.resume_with.assert_called_once_with("task-exec-001", _DEGRADE_RESUME)
    # 降级路径不触发 L-B1-01 空值拒绝文案。
    assert not any("输入不能为空" in str(el.value) for el in at.error)


def test_cp_2_3_2_degrade_ignores_input_and_remember_state():
    """输入框有内容 + 勾选「记住」后点降级 → 仍是固定三键（一次点击只降当前项）。"""
    controller = _make_controller_mock(interrupt_payload=_make_gate_payload())
    with patch("app._get_controller", return_value=controller):
        at = AppTest.from_string(_SCRIPT)
        at.run()
        at.text_input(key="_exec_user_input_value").set_value("half-typed-token")
        at.checkbox(key="_exec_user_input_remember").check()
        at.button(key=_BTN_DEGRADE_KEY).click().run()
    controller.resume_with.assert_called_once_with("task-exec-001", _DEGRADE_RESUME)


# =========================================================================== #
# CP-2.3-3：既有 user_input_request 面板零回归（allow_degrade=True 共存态）
# =========================================================================== #
def test_cp_2_3_3_normal_submit_still_two_key_contract_with_degrade_button():
    """五键 payload 下普通提交 → 仍两键契约（degrade 不出现，sp4 语义零变）。"""
    controller = _make_controller_mock(interrupt_payload=_make_gate_payload())
    with patch("app._get_controller", return_value=controller):
        at = AppTest.from_string(_SCRIPT)
        at.run()
        at.text_input(key="_exec_user_input_value").set_value("sk-real-key")
        at.checkbox(key="_exec_user_input_remember").check()
        at.button(key="btn_user_input_submit").click().run()
    controller.resume_with.assert_called_once_with(
        "task-exec-001", {"value": "sk-real-key", "remember": True}
    )
    args = controller.resume_with.call_args.args
    assert "degrade" not in args[1]


def test_cp_2_3_3_sensitive_password_single_input_and_remember_unchanged():
    """五键 payload：就一个输入框（password）+ 「记住」勾选默认不勾（sp4 硬约束不变）。"""
    controller = _make_controller_mock(interrupt_payload=_make_gate_payload())
    at = _run(controller)
    assert not at.exception, at.exception
    assert len(at.text_input) == 1                        # 就一个输入框（Maria 硬约束）
    assert at.text_input[0].proto.type == TextInputProto.PASSWORD
    assert len(at.checkbox) == 1
    assert at.checkbox[0].value is False                  # 默认不勾
    assert "记住此凭证" in at.checkbox[0].label


def test_cp_2_3_3_empty_submit_still_rejected_with_degrade_button_present():
    """五键 payload 下空值走「提交」仍被拒（L-B1-01 防线零回归），resume_with 不被调用。"""
    controller = _make_controller_mock(interrupt_payload=_make_gate_payload())
    with patch("app._get_controller", return_value=controller):
        at = AppTest.from_string(_SCRIPT)
        at.run()
        at.button(key="btn_user_input_submit").click().run()   # 输入框留空直接提交
    controller.resume_with.assert_not_called()
    assert any("输入不能为空" in str(el.value) for el in at.error)
