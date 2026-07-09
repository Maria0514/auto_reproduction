"""Sprint 3 任务 E1 单测：app.py `interrupt_kind` helper + UI 路由分发两页（S3-10）。

覆盖 dev-plan §E1 检查点 CP-E1-1 ~ CP-E1-3（dev-plan.md L569-572）。

任务范围（E1，严格边界）：
    - GraphController 新增 1 个**只读** helper `interrupt_kind(thread_id)`；
    - 把 config.STREAMLIT_PAGE_EXECUTION / STREAMLIT_PAGE_REPORT 接入 sp2 既有页面路由
      分发（模块级 _PAGE_MAP，沿用 session_state current_page 范式）。
    - **不做** E2/E3 页面实现（ui/pages/execution_monitor.py / result_report.py）。

硬约束（验收红线）：
    - CP-E1-1：GraphController 既有 6 个方法签名零变化（sp2 5 方法 + cancel_task）——
      用 inspect.signature 实证，仅允许**新增** interrupt_kind。
    - 复用 config 已有两页常量，不新增页面常量。
    - 不破坏 sp2 既有页面路由（input/progress/review 三页仍正常 dispatch）。

测试策略：
    - CP-E1-1 用 inspect.signature 固化既有方法签名（防回归）；
    - CP-E1-2 mock get_interrupt_payload 三种返回（planning 含键 / planning 无键兜底 /
      execution / 无 interrupt），断言 interrupt_kind 纯只读、不触碰 state/LLM；
    - CP-E1-3 以逻辑层断言为主（sp2 UI 因 shadcn 迁移 AppTest 看不到 iframe 而部分 skip，
      逻辑层断言优先）：断言 _PAGE_MAP 接入两页常量、键用 config 常量、sp2 三页未被破坏。
"""

from __future__ import annotations

import inspect
from typing import Any, Dict, Optional
from unittest.mock import patch

import pytest

import app as app_module
import config
from app import GraphController, _PAGE_MAP


# ----------------------------------------------------------------------
# CP-E1-1：GraphController 既有 6 个方法签名零变化
# ----------------------------------------------------------------------

# sp2 5 个方法 + cancel_task 的「黄金签名」快照（对照 sp2 行为固化，防 E1 误改）。
# 注意：annotations 形态用 from __future__ import annotations，故为字符串形态字面量。
_GOLDEN_SIGNATURES = {
    "start_task": "(self, arxiv_id: 'str', llm_config_set: 'LLMConfigSet') -> 'str'",
    "resume_with": "(self, thread_id: 'str', resume_payload: 'Dict') -> 'None'",
    "poll_state": "(self, thread_id: 'str') -> 'Optional[GlobalState]'",
    "is_interrupted": "(self, thread_id: 'str') -> 'bool'",
    "get_interrupt_payload": "(self, thread_id: 'str') -> 'Optional[Dict]'",
    "cancel_task": "(self, thread_id: 'str') -> 'None'",
}


@pytest.mark.parametrize("method_name,expected_sig", sorted(_GOLDEN_SIGNATURES.items()))
def test_cp_e1_1_existing_method_signatures_unchanged(method_name, expected_sig):
    """CP-E1-1：sp2 既有 6 方法（5 + cancel_task）签名逐个零变化（inspect.signature 实证）。"""
    method = getattr(GraphController, method_name)
    actual_sig = str(inspect.signature(method))
    assert actual_sig == expected_sig, (
        f"GraphController.{method_name} 签名被改动："
        f"期望 {expected_sig!r}，实际 {actual_sig!r}（CP-E1-1 红线：既有方法签名零变化）"
    )


def test_cp_e1_1_no_existing_method_removed():
    """CP-E1-1：sp2 既有 6 个方法仍然存在且为 callable（不得删除/降级为属性）。"""
    for method_name in _GOLDEN_SIGNATURES:
        assert hasattr(GraphController, method_name), f"既有方法 {method_name} 丢失"
        assert callable(getattr(GraphController, method_name)), f"{method_name} 不再是 callable"


def test_cp_e1_1_interrupt_kind_is_new_instance_method_with_self():
    """CP-E1-1：interrupt_kind 是**新增**的实例方法（带 self，单参 thread_id，返回 Optional[str]）。"""
    assert hasattr(GraphController, "interrupt_kind")
    sig = inspect.signature(GraphController.interrupt_kind)
    # 实例方法：第一个形参必须是 self；业务参数仅 thread_id。
    params = list(sig.parameters)
    assert params == ["self", "thread_id"], f"interrupt_kind 形参异常: {params}"
    assert str(sig) == "(self, thread_id: 'str') -> 'Optional[str]'"


def test_cp_e1_1_no_unexpected_public_methods_added():
    """CP-E1-1：E1 只允许新增 interrupt_kind，不得偷偷新增其它公开方法（最小变更守门）。

    对照 sp2 公开方法集合（D2 落地的全部公开方法）+ 本次唯一新增 interrupt_kind。
    """
    public_methods = {
        name
        for name, obj in inspect.getmembers(GraphController, predicate=inspect.isfunction)
        if not name.startswith("_")
    }
    # [S5-08 适配] sprint5 T-S5-0-2 按 dev-plan 规格新增只读方法 is_finished（架构
    # sprint5 §7.8 裁决），纳入预期集合；守门语义不变——仍钉死精确公开方法集合，
    # 任何未经规格批准的新增公开方法仍会被本用例拦截。
    # [S5-07 适配] sprint5 T-S5-4-2 按 dev-plan 规格新增只读方法 get_activity_tail
    # （架构 sprint5 §4 Q-S5-8 落点：活动流尾部快照，UI 轮询消费），纳入预期集合；
    # 守门语义同上不变。
    expected = set(_GOLDEN_SIGNATURES) | {
        "interrupt_kind", "get_worker_error", "is_finished", "get_activity_tail"}
    assert public_methods == expected, (
        f"公开方法集合超出预期：多出 {public_methods - expected}，缺失 {expected - public_methods}"
    )


# ----------------------------------------------------------------------
# CP-E1-2：interrupt_kind 三态 + planning 无键兜底
# ----------------------------------------------------------------------


@pytest.fixture
def controller_no_io(monkeypatch):
    """构造一个**不触碰任何 IO**的 GraphController（mock get_checkpointer / build_graph）。

    interrupt_kind 测试只关心它如何消费 get_interrupt_payload 的返回，故 __init__ 里的
    checkpointer / graph 用哨兵替身，避免真实建图/触碰 SQLite。
    """
    monkeypatch.setattr(app_module, "get_checkpointer", lambda db_path=None: object())
    monkeypatch.setattr(app_module, "build_graph", lambda checkpointer=None: object())
    return GraphController()


def test_cp_e1_2_planning_with_explicit_kind_returns_planning(controller_no_io):
    """CP-E1-2：planning interrupt（payload 显式含 interrupt_kind="planning"）→ "planning"。"""
    payload = {"interrupt_kind": "planning", "reproduction_plan": {"x": 1}, "revise_count": 0}
    with patch.object(controller_no_io, "get_interrupt_payload", return_value=payload):
        assert controller_no_io.interrupt_kind("task-1") == "planning"


def test_cp_e1_2_planning_without_kind_falls_back_to_planning(controller_no_io):
    """CP-E1-2 兜底：旧 planning payload **无 interrupt_kind 键** → 默认 "planning"（向后兼容 sp2）。"""
    # sp2 老 planning payload：含 reproduction_plan 等键，但没有 interrupt_kind。
    legacy_payload = {"reproduction_plan": {"x": 1}, "revise_count": 0}
    with patch.object(controller_no_io, "get_interrupt_payload", return_value=legacy_payload):
        assert controller_no_io.interrupt_kind("task-legacy") == "planning"


def test_cp_e1_2_execution_returns_dev_loop_failure(controller_no_io):
    """CP-E1-2：execution interrupt（payload 含 interrupt_kind="dev_loop_failure"）→ "dev_loop_failure"。"""
    payload = {
        "interrupt_kind": "dev_loop_failure",
        "fix_loop_history": [{"round": 1, "error": "x"}],
        "execution_result": {"success": False, "errors": ["boom"]},
    }
    with patch.object(controller_no_io, "get_interrupt_payload", return_value=payload):
        assert controller_no_io.interrupt_kind("task-exec") == "dev_loop_failure"


def test_cp_e1_2_no_interrupt_payload_none_returns_none(controller_no_io):
    """CP-E1-2：无 interrupt（get_interrupt_payload 返回 None）→ interrupt_kind 返回 None。"""
    with patch.object(controller_no_io, "get_interrupt_payload", return_value=None):
        assert controller_no_io.interrupt_kind("task-running") is None


def test_cp_e1_2_empty_payload_returns_none(controller_no_io):
    """CP-E1-2 边界：payload 为空 dict（视为无有效 interrupt）→ 返回 None（not payload 短路）。"""
    with patch.object(controller_no_io, "get_interrupt_payload", return_value={}):
        assert controller_no_io.interrupt_kind("task-empty") is None


def test_cp_e1_2_interrupt_kind_is_read_only(controller_no_io):
    """CP-E1-2：interrupt_kind 纯只读——只调 get_interrupt_payload 一次，不触碰 resume/invoke/state。"""
    payload = {"interrupt_kind": "dev_loop_failure"}
    with patch.object(
        controller_no_io, "get_interrupt_payload", return_value=payload
    ) as mocked_get, patch.object(controller_no_io, "resume_with") as mocked_resume:
        result = controller_no_io.interrupt_kind("task-ro")

    assert result == "dev_loop_failure"
    # 只读：恰好读一次 payload，绝不触发任何写路径（resume_with）。
    mocked_get.assert_called_once_with("task-ro")
    mocked_resume.assert_not_called()
    # 未起任何工作线程（纯主线程只读）。
    assert controller_no_io._workers == {}


# ----------------------------------------------------------------------
# CP-E1-3：UI 路由常量 STREAMLIT_PAGE_EXECUTION / STREAMLIT_PAGE_REPORT 接入页面分发
# ----------------------------------------------------------------------


def test_cp_e1_3_execution_page_registered_in_page_map():
    """CP-E1-3：STREAMLIT_PAGE_EXECUTION 已接入 _PAGE_MAP，指向 execution_monitor 渲染入口。"""
    assert config.STREAMLIT_PAGE_EXECUTION in _PAGE_MAP
    module_name, func_name = _PAGE_MAP[config.STREAMLIT_PAGE_EXECUTION]
    assert module_name == "ui.pages.execution_monitor"
    assert func_name == "render_execution_monitor_page"


def test_cp_e1_3_report_page_registered_in_page_map():
    """CP-E1-3：STREAMLIT_PAGE_REPORT 已接入 _PAGE_MAP，指向 result_report 渲染入口。"""
    assert config.STREAMLIT_PAGE_REPORT in _PAGE_MAP
    module_name, func_name = _PAGE_MAP[config.STREAMLIT_PAGE_REPORT]
    assert module_name == "ui.pages.result_report"
    assert func_name == "render_result_report_page"


def test_cp_e1_3_page_map_keys_use_config_constants_not_literals():
    """CP-E1-3：_PAGE_MAP 键统一用 config.STREAMLIT_PAGE_* 常量（不新增页面常量，全部复用）。"""
    assert set(_PAGE_MAP.keys()) == {
        config.STREAMLIT_PAGE_INPUT,
        config.STREAMLIT_PAGE_PROGRESS,
        config.STREAMLIT_PAGE_REVIEW,
        config.STREAMLIT_PAGE_EXECUTION,
        config.STREAMLIT_PAGE_REPORT,
    }


def test_cp_e1_3_sp2_three_pages_not_broken():
    """CP-E1-3 红线：sp2 既有三页路由（input/progress/review）仍正常 dispatch（指向原模块/函数）。"""
    assert _PAGE_MAP[config.STREAMLIT_PAGE_INPUT] == (
        "ui.pages.paper_input",
        "render_paper_input_page",
    )
    assert _PAGE_MAP[config.STREAMLIT_PAGE_PROGRESS] == (
        "ui.pages.analysis_progress",
        "render_analysis_progress_page",
    )
    assert _PAGE_MAP[config.STREAMLIT_PAGE_REVIEW] == (
        "ui.pages.plan_review",
        "render_plan_review_page",
    )


def test_cp_e1_3_main_dispatches_execution_page(monkeypatch):
    """CP-E1-3（dispatch 实证）：current_page=execution 时 main() 按 _PAGE_MAP 路由到执行监控页。

    用轻量 fake streamlit + fake importlib 捕获 main() 实际 import 的模块名 + 调用的 render 函数，
    断言 execution 页被正确分发（不依赖 AppTest，逻辑层断言优先）。
    """
    _exercise_main_dispatch(
        monkeypatch,
        current_page=config.STREAMLIT_PAGE_EXECUTION,
        expected_module="ui.pages.execution_monitor",
        expected_func="render_execution_monitor_page",
    )


def test_cp_e1_3_main_dispatches_report_page(monkeypatch):
    """CP-E1-3（dispatch 实证）：current_page=report 时 main() 按 _PAGE_MAP 路由到结果报告页。"""
    _exercise_main_dispatch(
        monkeypatch,
        current_page=config.STREAMLIT_PAGE_REPORT,
        expected_module="ui.pages.result_report",
        expected_func="render_result_report_page",
    )


def test_cp_e1_3_main_unimplemented_page_degrades_gracefully(monkeypatch):
    """CP-E1-3：E2/E3 页面模块尚未实现时 main() 优雅降级（st.info 提示），不抛 import 错。

    模拟 importlib.import_module 抛 ImportError（execution_monitor 尚不存在），断言 main()
    走 except 分支调 st.info 而非崩溃——保证 `streamlit run app.py` 仍可启动。
    """
    fake_st = _FakeStreamlit(current_page=config.STREAMLIT_PAGE_EXECUTION)
    monkeypatch.setitem(__import__("sys").modules, "streamlit", fake_st)
    monkeypatch.setattr(app_module, "_get_controller", lambda: object())

    import importlib as _importlib

    def _raise_import(name):
        raise ImportError(f"No module named {name!r} (E2/E3 未实现)")

    monkeypatch.setattr(_importlib, "import_module", _raise_import)

    # 不应抛异常（优雅降级）。
    app_module.main()

    assert fake_st.info_called, "未实现页面应走 st.info 优雅降级"
    assert fake_st.rendered_func_calls == [], "未实现页面不应调用任何 render 函数"


# ----------------------------------------------------------------------
# 测试辅助：轻量 fake streamlit + main() dispatch 演练
# ----------------------------------------------------------------------


class _FakeSidebarCtx:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeStreamlit:
    """最小化 fake streamlit：仅覆盖 main() 用到的 API（set_page_config / session_state / info）。"""

    def __init__(self, current_page: str) -> None:
        self.session_state: Dict[str, Any] = {"current_page": current_page}
        self.sidebar = _FakeSidebarCtx()
        self.info_called = False
        self.rendered_func_calls: list = []

    def set_page_config(self, **kwargs):  # noqa: D401
        return None

    def info(self, *args, **kwargs):
        self.info_called = True


class _FakePageModule:
    """fake 页面模块：被注入到 fake importlib，render 函数被调用时记账到 streamlit fake。"""

    def __init__(self, fake_st: _FakeStreamlit, func_name: str) -> None:
        self._fake_st = fake_st
        self._func_name = func_name

        def _render():
            self._fake_st.rendered_func_calls.append(self._func_name)

        setattr(self, func_name, _render)


def _exercise_main_dispatch(monkeypatch, current_page, expected_module, expected_func):
    """演练 main() 路由分发：断言它按 _PAGE_MAP import 期望模块并调用期望 render 函数。"""
    fake_st = _FakeStreamlit(current_page=current_page)
    monkeypatch.setitem(__import__("sys").modules, "streamlit", fake_st)
    # 不真正建 GraphController（避免触碰 checkpointer/graph）。
    monkeypatch.setattr(app_module, "_get_controller", lambda: object())

    imported: Dict[str, str] = {}

    import importlib as _importlib

    def _fake_import_module(name):
        imported["module"] = name
        return _FakePageModule(fake_st, expected_func)

    monkeypatch.setattr(_importlib, "import_module", _fake_import_module)

    app_module.main()

    assert imported.get("module") == expected_module, (
        f"main() 应 import {expected_module}，实际 import {imported.get('module')}"
    )
    assert fake_st.rendered_func_calls == [expected_func], (
        f"main() 应调用 {expected_func}，实际 {fake_st.rendered_func_calls}"
    )
    assert fake_st.info_called is False, "已实现页面不应走优雅降级 st.info"
