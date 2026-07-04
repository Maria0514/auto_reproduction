"""Sprint 4 任务 F1 单测：execution_monitor user_input_request 面板 + app.py 路由（S4-09）。

覆盖 dev-plan §F1 检查点：
    - CP-F1-1：三类 interrupt_kind 分发正确（planning / dev_loop_failure /
      user_input_request 互不误触）；敏感 → password + 「记住」勾选出现、
      非敏感 → 普通输入 + 无勾选；
    - CP-F1-2：提交 payload 契约 {"value", "remember"} 两键、类型正确、经 resume_with 透传；
    - 附加（L-B1-01 防线）：空值 / 纯空白提交被拒绝，resume_with 不被调用；
    - 附加：app.py::_should_route_to_user_input_panel 全局路由判定（interrupt#3 在
      非执行监控页触发时强制路由，planning / dev_loop_failure 不经此分支）。

测试策略（沿用 sp3 E2 范式）：纯函数直测 + AppTest + mock GraphController
（patch("app._get_controller")）跑真实 render()，原生组件可见可点。

运行::

    .venv/bin/pytest tests/test_sprint4_f1.py -q
"""

from __future__ import annotations

import importlib
from typing import Any, Dict, Optional
from unittest.mock import MagicMock, patch

from streamlit.proto.TextInput_pb2 import TextInput as TextInputProto
from streamlit.testing.v1 import AppTest


def _mod():
    """importlib 取模块（避免 __init__ 显式 export 遮蔽子模块的已知坑，见 CLAUDE 坑6）。"""
    return importlib.import_module("ui.pages.execution_monitor")


# --------------------------------------------------------------------------- #
# 夹具：mock state / payload / controller 工厂（沿用 test_sprint3_e2 范式）
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


def _make_user_input_payload(
    *,
    question: str = "请提供 github.com 的访问令牌（用于 clone 私有仓库）",
    is_sensitive: bool = True,
    purpose_key: Optional[str] = "git_credential:github.com",
) -> Dict[str, Any]:
    """构造 interrupt#3 payload（architecture §7.1 四键契约）。"""
    return {
        "interrupt_kind": "user_input_request",
        "question": question,
        "is_sensitive": is_sensitive,
        "purpose_key": purpose_key,
    }


def _make_controller_mock(
    *,
    state: Optional[Dict[str, Any]] = None,
    is_interrupted: bool = False,
    interrupt_kind: Optional[str] = None,
    interrupt_payload: Optional[Dict[str, Any]] = None,
) -> MagicMock:
    controller = MagicMock()
    controller.poll_state.return_value = state
    controller.is_interrupted.return_value = is_interrupted
    controller.interrupt_kind.return_value = interrupt_kind
    controller.get_interrupt_payload.return_value = interrupt_payload
    controller.get_worker_error.return_value = None
    return controller


# AppTest 脚本：按 current_page 路由，防 st.rerun 无限循环（沿用 sp3 E2 范式）。
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


def _collect_text(at: AppTest) -> str:
    parts = []
    for collection in (at.title, at.subheader, at.caption, at.markdown,
                       at.text, at.warning, at.info, at.error):
        for el in collection:
            parts.append(str(getattr(el, "value", "")))
    return "\n".join(parts)


# =========================================================================== #
# 契约对齐：本页 / app.py 常量与 interaction_tools 严格一致（防漂移）
# =========================================================================== #
def test_interrupt_kind_constant_aligned_with_interaction_tools():
    """页面 / app.py 的 user_input_request 常量与 B1 工具模块字节级一致。"""
    tools_mod = importlib.import_module("core.tools.interaction_tools")
    app_mod = importlib.import_module("app")
    mod = _mod()
    assert mod._INTERRUPT_KIND_USER_INPUT == tools_mod.INTERRUPT_KIND_USER_INPUT
    assert app_mod._INTERRUPT_KIND_USER_INPUT == tools_mod.INTERRUPT_KIND_USER_INPUT


# =========================================================================== #
# 纯函数：非空校验（L-B1-01 防线）+ resume payload 构造（CP-F1-2 契约）
# =========================================================================== #
def test_is_valid_user_input_pure():
    mod = _mod()
    assert mod._is_valid_user_input("token-abc") is True
    assert mod._is_valid_user_input("  x  ") is True
    # 空 / 纯空白 / 非 str 一律拒绝。
    assert mod._is_valid_user_input("") is False
    assert mod._is_valid_user_input("   ") is False
    assert mod._is_valid_user_input("\t\n") is False
    assert mod._is_valid_user_input(None) is False
    assert mod._is_valid_user_input(123) is False


def test_build_user_input_resume_contract_pure():
    """CP-F1-2（纯函数）：恰两键 {"value", "remember"}，类型 str/bool。"""
    mod = _mod()
    payload = mod._build_user_input_resume("tok", True)
    assert payload == {"value": "tok", "remember": True}
    assert set(payload.keys()) == {"value", "remember"}
    # remember 真值强转 bool；value 原样透传（不 strip，凭证以用户所见为准）。
    p2 = mod._build_user_input_resume(" tok ", 1)
    assert p2["value"] == " tok "
    assert p2["remember"] is True
    assert mod._build_user_input_resume("x", False) == {"value": "x", "remember": False}


# =========================================================================== #
# CP-F1-1：三类 interrupt_kind 分发互不误触
# =========================================================================== #
def test_cp_f1_1_user_input_kind_renders_input_panel():
    """user_input_request → 用户输入面板（不出 dev_loop 决策面板、不跳 review）。"""
    controller = _make_controller_mock(
        state=_make_state("coding"),
        is_interrupted=True,
        interrupt_kind="user_input_request",
        interrupt_payload=_make_user_input_payload(),
    )
    at = _run(controller)
    assert not at.exception, at.exception
    text = _collect_text(at)
    assert "需要你补充信息" in text
    assert "github.com 的访问令牌" in text            # question 正文
    assert "代码生成" in text                          # current_step 一句上下文
    assert "执行失败决策" not in text                  # 不误触 dev_loop 面板
    assert "REVIEW_STUB" not in text                   # 不误跳 review
    # 就一个输入框（Maria 硬约束）+ 一个提交按钮（key 不存在时 at.button 抛异常）。
    assert len(at.text_input) == 1
    assert at.button(key="btn_user_input_submit") is not None


def test_cp_f1_1_dev_loop_kind_still_renders_decision_panel():
    """dev_loop_failure → 仍走既有决策面板（零退化，不误触输入面板）。"""
    controller = _make_controller_mock(
        state=_make_state("execution"),
        is_interrupted=True,
        interrupt_kind="dev_loop_failure",
        interrupt_payload={"interrupt_kind": "dev_loop_failure", "fix_loop_count": 3,
                           "fix_loop_history": [], "execution_errors": ["boom"]},
    )
    at = _run(controller)
    assert not at.exception, at.exception
    text = _collect_text(at)
    assert "执行失败决策" in text
    assert "需要你补充信息" not in text


def test_cp_f1_1_planning_kind_jumps_to_review():
    """planning → 跳回 review 页（既有防御路径零退化）。"""
    controller = _make_controller_mock(
        state=_make_state("execution"),
        is_interrupted=True,
        interrupt_kind="planning",
        interrupt_payload={"interrupt_kind": "planning"},
    )
    at = _run(controller)
    assert not at.exception, at.exception
    # st.write(str) 渲染为 markdown 元素。
    assert "REVIEW_STUB" in "\n".join(str(el.value) for el in at.markdown)


def test_cp_f1_1_sensitive_password_input_and_remember_checkbox():
    """敏感 → password 输入 + 「记住」勾选出现（默认不勾）。"""
    controller = _make_controller_mock(
        state=_make_state("coding"),
        is_interrupted=True,
        interrupt_kind="user_input_request",
        interrupt_payload=_make_user_input_payload(is_sensitive=True),
    )
    at = _run(controller)
    assert not at.exception, at.exception
    assert len(at.text_input) == 1
    assert at.text_input[0].proto.type == TextInputProto.PASSWORD
    assert len(at.checkbox) == 1
    assert at.checkbox[0].value is False                # 默认不勾
    assert "记住此凭证" in at.checkbox[0].label
    # remember 语义绑定 purpose_key：caption 说明含 purpose_key。
    assert "git_credential:github.com" in _collect_text(at)


def test_cp_f1_1_non_sensitive_plain_input_no_checkbox():
    """非敏感 → 普通输入 + 无「记住」勾选。"""
    controller = _make_controller_mock(
        state=_make_state("coding"),
        is_interrupted=True,
        interrupt_kind="user_input_request",
        interrupt_payload=_make_user_input_payload(
            question="训练用哪个数据集切分？", is_sensitive=False, purpose_key=None,
        ),
    )
    at = _run(controller)
    assert not at.exception, at.exception
    assert len(at.text_input) == 1
    assert at.text_input[0].proto.type == TextInputProto.DEFAULT
    assert len(at.checkbox) == 0


# =========================================================================== #
# CP-F1-2：提交 payload 契约经 resume_with 透传
# =========================================================================== #
def test_cp_f1_2_submit_non_sensitive_value_remember_false():
    """非敏感提交 → resume_with(thread_id, {"value": <输入>, "remember": False})。"""
    controller = _make_controller_mock(
        state=_make_state("coding"),
        is_interrupted=True,
        interrupt_kind="user_input_request",
        interrupt_payload=_make_user_input_payload(is_sensitive=False, purpose_key=None),
    )
    with patch("app._get_controller", return_value=controller):
        at = AppTest.from_string(_SCRIPT)
        at.run()
        at.text_input(key="_exec_user_input_value").set_value("cifar10")
        at.button(key="btn_user_input_submit").click().run()
    controller.resume_with.assert_called_once_with(
        "task-exec-001", {"value": "cifar10", "remember": False}
    )


def test_cp_f1_2_submit_sensitive_with_remember_checked():
    """敏感 + 勾选「记住」→ resume_with(..., {"value": <输入>, "remember": True})。"""
    controller = _make_controller_mock(
        state=_make_state("coding"),
        is_interrupted=True,
        interrupt_kind="user_input_request",
        interrupt_payload=_make_user_input_payload(is_sensitive=True),
    )
    with patch("app._get_controller", return_value=controller):
        at = AppTest.from_string(_SCRIPT)
        at.run()
        at.text_input(key="_exec_user_input_value").set_value("ghp_secret")
        at.checkbox(key="_exec_user_input_remember").check()
        at.button(key="btn_user_input_submit").click().run()
    controller.resume_with.assert_called_once_with(
        "task-exec-001", {"value": "ghp_secret", "remember": True}
    )


def test_cp_f1_2_empty_value_rejected_no_resume():
    """L-B1-01 防线：空值提交被拒绝——resume_with 不被调用 + 明确报错文案。"""
    controller = _make_controller_mock(
        state=_make_state("coding"),
        is_interrupted=True,
        interrupt_kind="user_input_request",
        interrupt_payload=_make_user_input_payload(is_sensitive=False, purpose_key=None),
    )
    with patch("app._get_controller", return_value=controller):
        at = AppTest.from_string(_SCRIPT)
        at.run()
        at.button(key="btn_user_input_submit").click().run()   # 输入框留空直接提交
    controller.resume_with.assert_not_called()
    assert any("输入不能为空" in str(el.value) for el in at.error)


def test_cp_f1_2_whitespace_only_rejected_no_resume():
    """L-B1-01 防线：纯空白提交同样被拒绝。"""
    controller = _make_controller_mock(
        state=_make_state("coding"),
        is_interrupted=True,
        interrupt_kind="user_input_request",
        interrupt_payload=_make_user_input_payload(is_sensitive=True),
    )
    with patch("app._get_controller", return_value=controller):
        at = AppTest.from_string(_SCRIPT)
        at.run()
        at.text_input(key="_exec_user_input_value").set_value("   ")
        at.button(key="btn_user_input_submit").click().run()
    controller.resume_with.assert_not_called()
    assert any("输入不能为空" in str(el.value) for el in at.error)


# =========================================================================== #
# app.py 全局路由判定（interrupt#3 在非执行监控页触发 → 强制路由）
# =========================================================================== #
def _route(current_page: str, controller, thread_id: Optional[str]) -> bool:
    app_mod = importlib.import_module("app")
    return app_mod._should_route_to_user_input_panel(current_page, controller, thread_id)


def test_app_route_hits_only_for_user_input_request_off_execution_page():
    """review/progress 页 + user_input_request interrupt → 强制路由 True。"""
    for page in ("review", "progress", "input"):
        controller = _make_controller_mock(
            is_interrupted=True, interrupt_kind="user_input_request"
        )
        assert _route(page, controller, "t-1") is True, page


def test_app_route_no_hit_on_execution_page_or_without_thread():
    """已在执行监控页 / 无 thread_id → False（且不触达 controller 读路径）。"""
    controller = _make_controller_mock(
        is_interrupted=True, interrupt_kind="user_input_request"
    )
    assert _route("execution", controller, "t-1") is False
    assert _route("review", controller, None) is False
    controller.is_interrupted.assert_not_called()


def test_app_route_no_hit_for_other_kinds_and_lazy_kind_read():
    """planning / dev_loop_failure 不经此分支；未 interrupt 时不读 interrupt_kind（惰性）。"""
    for kind in ("planning", "dev_loop_failure"):
        controller = _make_controller_mock(is_interrupted=True, interrupt_kind=kind)
        assert _route("review", controller, "t-1") is False, kind
    # 未 interrupt → False 且 interrupt_kind 不被调用。
    controller = _make_controller_mock(is_interrupted=False)
    assert _route("review", controller, "t-1") is False
    controller.interrupt_kind.assert_not_called()
