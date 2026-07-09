"""Sprint 3 任务 E1 独立验收补强用例（测试工程师代理）。

在开发自测 `tests/test_sprint3_e1.py`（22 用例）之上补强边界与红线深证，覆盖：
    - CP-E1-1 红线深证：把 sp2 既有公开方法**全集**（含 get_worker_error）纳入黄金签名 +
      运行时反证 interrupt_kind 是 E1 唯一新增（不止验签名字符串，AST 段对照见验收脚本）。
    - CP-E1-2 兜底/只读重锤：interrupt_kind 取值非字符串/None 等畸形 payload 的透传语义、
      只读性更彻底反证（不触碰 poll_state/_main_graph/build_graph，不起线程，state 不变）。
    - CP-E1-3 一致性：_PAGE_MAP 键与 config 五常量值一致、值结构完整（2-tuple 非空 str）、
      dispatch 到 sp2 三页也走对路由、非法/缺失 current_page 降级回 input、模块级单例性。

设计约束：
    - 每条用例独立可跑（pytest tests/test_sprint3_e1_reinforce.py::test_x）。
    - 不触发真实 LLM / deepxiv / 网络 / 文件持久写（controller_no_io 用哨兵替身建图）。
    - 不修改生产代码；发现 bug 即停并报告。
"""

from __future__ import annotations

import inspect
from typing import Any, Dict, List

import pytest

import app as app_module
import config
from app import GraphController, _PAGE_MAP


# ----------------------------------------------------------------------
# CP-E1-1 红线深证：sp2 既有公开方法全集签名零变化
# ----------------------------------------------------------------------

# 任务书点名 6 个核心方法，但 sp2 GraphController 公开方法全集还含 get_worker_error。
# 这里固化 sp2 公开方法**全集**（7 个）的签名，确保 E1 没有顺手改动 get_worker_error。
_SP2_PUBLIC_GOLDEN = {
    "start_task": "(self, arxiv_id: 'str', llm_config_set: 'LLMConfigSet') -> 'str'",
    "resume_with": "(self, thread_id: 'str', resume_payload: 'Dict') -> 'None'",
    "poll_state": "(self, thread_id: 'str') -> 'Optional[GlobalState]'",
    "is_interrupted": "(self, thread_id: 'str') -> 'bool'",
    "get_interrupt_payload": "(self, thread_id: 'str') -> 'Optional[Dict]'",
    "get_worker_error": "(self, thread_id: 'str') -> 'Optional[Exception]'",
    "cancel_task": "(self, thread_id: 'str') -> 'None'",
}


@pytest.mark.parametrize("name,expected", sorted(_SP2_PUBLIC_GOLDEN.items()))
def test_sp2_full_public_signature_unchanged(name, expected):
    """CP-E1-1 深证：sp2 公开方法全集（含 get_worker_error）签名逐个零变化。"""
    actual = str(inspect.signature(getattr(GraphController, name)))
    assert actual == expected, (
        f"GraphController.{name} 签名被改：期望 {expected!r}，实际 {actual!r}"
    )


def test_sp2_private_helpers_still_present():
    """CP-E1-1：sp2 既有私有协调方法（_worker_run/_resume_run/_has_interrupt）仍在（未被误删）。"""
    for name in ("__init__", "_worker_run", "_resume_run", "_has_interrupt"):
        assert callable(getattr(GraphController, name)), f"既有方法 {name} 丢失"


def test_interrupt_kind_is_the_only_new_public_method():
    """CP-E1-1：相对 sp2 公开方法全集，仅多出规格批准的新增方法（不得多/少其它公开方法）。

    [S5-08 适配] 预期新增集从 {interrupt_kind}（sp3 E1）扩为 {interrupt_kind,
    is_finished}——is_finished 为 sprint5 T-S5-0-2 按 dev-plan 规格新增的只读方法
    （架构 sprint5 §7.8 裁决）。
    [S5-07 适配] 预期新增集再扩入 get_activity_tail——sprint5 T-S5-4-2 按 dev-plan
    规格新增的只读方法（架构 sprint5 §4 Q-S5-8 落点：活动流尾部快照）。
    守门语义不变：仍钉死精确新增集合，未经规格批准的公开方法仍会被拦截。
    """
    public = {
        n
        for n, _ in inspect.getmembers(GraphController, predicate=inspect.isfunction)
        if not n.startswith("_")
    }
    sp2_public = set(_SP2_PUBLIC_GOLDEN)
    new_methods = public - sp2_public
    assert new_methods == {"interrupt_kind", "is_finished", "get_activity_tail"}, (
        f"相对 sp2 应只新增 interrupt_kind + is_finished + get_activity_tail，"
        f"实际新增 {new_methods}；缺失 {sp2_public - public}"
    )


# ----------------------------------------------------------------------
# 不触碰 IO 的 controller（与开发文件同范式，但独立定义，便于本文件单独跑）
# ----------------------------------------------------------------------


@pytest.fixture
def controller_no_io(monkeypatch):
    """构造不触碰任何 IO 的 GraphController（哨兵替身 checkpointer/graph）。"""
    monkeypatch.setattr(app_module, "get_checkpointer", lambda db_path=None: object())
    monkeypatch.setattr(app_module, "build_graph", lambda checkpointer=None: object())
    return GraphController()


# ----------------------------------------------------------------------
# CP-E1-2 兜底/透传/畸形 payload
# ----------------------------------------------------------------------


def _patch_payload(monkeypatch, controller, payload):
    monkeypatch.setattr(controller, "get_interrupt_payload", lambda thread_id: payload)


@pytest.mark.parametrize(
    "payload,expected",
    [
        # 显式 kind 原样透传（即便是 sp3 之外的未知值，helper 不做白名单过滤——纯读取）。
        ({"interrupt_kind": "planning"}, "planning"),
        ({"interrupt_kind": "dev_loop_failure"}, "dev_loop_failure"),
        ({"interrupt_kind": "some_future_kind"}, "some_future_kind"),
        # 无键 → 兜底 planning（护旧 checkpoint）。
        ({"reproduction_plan": {"x": 1}}, "planning"),
        ({"fix_loop_history": []}, "planning"),  # 非空 payload 但无 kind 键
    ],
)
def test_interrupt_kind_value_passthrough_and_fallback(
    monkeypatch, controller_no_io, payload, expected
):
    """CP-E1-2：含 kind 键原样透传（不做白名单），无 kind 键非空 payload 兜底 planning。"""
    _patch_payload(monkeypatch, controller_no_io, payload)
    assert controller_no_io.interrupt_kind("t") == expected


@pytest.mark.parametrize("empty_payload", [None, {}])
def test_interrupt_kind_no_interrupt_returns_none(monkeypatch, controller_no_io, empty_payload):
    """CP-E1-2：无 interrupt（payload 为 None 或空 dict，not payload 短路）→ None。"""
    _patch_payload(monkeypatch, controller_no_io, empty_payload)
    assert controller_no_io.interrupt_kind("t") is None


def test_interrupt_kind_explicit_none_value_passthrough(monkeypatch, controller_no_io):
    """CP-E1-2 畸形：payload 显式 interrupt_kind=None → 透传 None（dict.get 命中键取值 None）。

    这是合理的「键存在但值为 None」语义：not payload 为 False（payload 非空），故进入
    payload.get("interrupt_kind", "planning")，键存在取到 None（不走 default 兜底）。
    记录此行为为契约：上游若写入 None 值，helper 不会改写成 "planning"。
    """
    _patch_payload(monkeypatch, controller_no_io, {"interrupt_kind": None, "x": 1})
    assert controller_no_io.interrupt_kind("t") is None


def test_interrupt_kind_non_string_value_passthrough(monkeypatch, controller_no_io):
    """CP-E1-2 畸形：interrupt_kind 值为非字符串（如 int）→ 原样透传（helper 不做类型强转）。

    固化「纯读取、不做类型/白名单清洗」契约，避免 helper 偷偷吞掉异常上游数据而掩盖问题。
    """
    _patch_payload(monkeypatch, controller_no_io, {"interrupt_kind": 123})
    assert controller_no_io.interrupt_kind("t") == 123


def test_interrupt_kind_read_only_does_not_touch_read_or_write_paths(monkeypatch, controller_no_io):
    """CP-E1-2 只读重锤：interrupt_kind 不触碰 poll_state / build_graph / 工作线程，state 不变。

    只允许调 get_interrupt_payload（唯一读路径）一次；poll_state（另一读路径）/ resume_with
    （写路径）/ start_task / cancel_task 一律零调用，_workers/_worker_errors 不被改动。
    """
    calls: Dict[str, int] = {
        "get_interrupt_payload": 0,
        "poll_state": 0,
        "resume_with": 0,
        "start_task": 0,
        "cancel_task": 0,
    }

    def make_spy(name, ret=None):
        def _spy(*a, **k):
            calls[name] += 1
            return ret

        return _spy

    monkeypatch.setattr(
        controller_no_io,
        "get_interrupt_payload",
        make_spy("get_interrupt_payload", {"interrupt_kind": "dev_loop_failure"}),
    )
    monkeypatch.setattr(controller_no_io, "poll_state", make_spy("poll_state"))
    monkeypatch.setattr(controller_no_io, "resume_with", make_spy("resume_with"))
    monkeypatch.setattr(controller_no_io, "start_task", make_spy("start_task", "tid"))
    monkeypatch.setattr(controller_no_io, "cancel_task", make_spy("cancel_task"))

    workers_before = dict(controller_no_io._workers)
    errors_before = dict(controller_no_io._worker_errors)

    result = controller_no_io.interrupt_kind("task-ro")

    assert result == "dev_loop_failure"
    assert calls["get_interrupt_payload"] == 1, "应恰好读一次 payload"
    assert calls["poll_state"] == 0, "interrupt_kind 不得触碰 poll_state 读路径"
    assert calls["resume_with"] == 0, "interrupt_kind 不得触发 resume_with 写路径"
    assert calls["start_task"] == 0
    assert calls["cancel_task"] == 0
    # 无任何工作线程/错误表副作用。
    assert controller_no_io._workers == workers_before == {}
    assert controller_no_io._worker_errors == errors_before == {}


def test_interrupt_kind_does_not_mutate_payload(monkeypatch, controller_no_io):
    """CP-E1-2 只读：interrupt_kind 不修改它读到的 payload dict（纯读，不 pop/写回）。"""
    payload = {"interrupt_kind": "planning", "reproduction_plan": {"x": 1}}
    snapshot = dict(payload)
    _patch_payload(monkeypatch, controller_no_io, payload)
    controller_no_io.interrupt_kind("t")
    assert payload == snapshot, "interrupt_kind 不应修改 payload"


# ----------------------------------------------------------------------
# CP-E1-3 一致性 / 结构完整 / dispatch 全路
# ----------------------------------------------------------------------


def test_page_map_keys_match_config_constant_values():
    """CP-E1-3：_PAGE_MAP 的键 == config 五个 STREAMLIT_PAGE_* 常量的**值**（无字面量漂移）。"""
    expected_values = {
        config.STREAMLIT_PAGE_INPUT,
        config.STREAMLIT_PAGE_PROGRESS,
        config.STREAMLIT_PAGE_REVIEW,
        config.STREAMLIT_PAGE_EXECUTION,
        config.STREAMLIT_PAGE_REPORT,
    }
    assert set(_PAGE_MAP.keys()) == expected_values
    # 五个常量值互不相同（没有两页撞键）。
    assert len(expected_values) == 5, "config 五个页面常量值应互不相同"


def test_page_map_no_new_page_constants_introduced():
    """CP-E1-3 红线：E1 未在 config 偷偷新增页面常量（仍恰好 5 个 STREAMLIT_PAGE_*）。"""
    page_consts = {n for n in dir(config) if n.startswith("STREAMLIT_PAGE_")}
    assert page_consts == {
        "STREAMLIT_PAGE_INPUT",
        "STREAMLIT_PAGE_PROGRESS",
        "STREAMLIT_PAGE_REVIEW",
        "STREAMLIT_PAGE_EXECUTION",
        "STREAMLIT_PAGE_REPORT",
    }, f"config 页面常量集合异常：{page_consts}"


def test_page_map_values_are_well_formed_tuples():
    """CP-E1-3：_PAGE_MAP 每个值是 (module_name, func_name) 二元组，模块/函数名均非空 str。"""
    for key, value in _PAGE_MAP.items():
        assert isinstance(value, tuple) and len(value) == 2, f"{key} 值非二元组: {value!r}"
        module_name, func_name = value
        assert isinstance(module_name, str) and module_name, f"{key} 模块名非法: {module_name!r}"
        assert isinstance(func_name, str) and func_name, f"{key} 函数名非法: {func_name!r}"
        # 模块名应位于 ui.pages 包下（路由约定）。
        assert module_name.startswith("ui.pages."), f"{key} 模块名不在 ui.pages 下: {module_name}"


def test_page_map_is_module_level_singleton():
    """CP-E1-3：_PAGE_MAP 是模块级常量（同一对象，不在 main() 内每次 rerun 重建字面量）。"""
    from app import _PAGE_MAP as ref_a
    from app import _PAGE_MAP as ref_b

    assert ref_a is ref_b is _PAGE_MAP


# --- dispatch 全路演练（轻量 fake streamlit + fake importlib，不依赖 AppTest）---


class _FakeSidebarCtx:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeStreamlit:
    def __init__(self, session_state: Dict[str, Any]) -> None:
        self.session_state = session_state
        self.sidebar = _FakeSidebarCtx()
        self.info_called = False
        self.rendered: List[str] = []

    def set_page_config(self, **kwargs):
        return None

    def info(self, *a, **k):
        self.info_called = True


class _FakePageModule:
    def __init__(self, fake_st: _FakeStreamlit, func_name: str) -> None:
        def _render():
            fake_st.rendered.append(func_name)

        setattr(self, func_name, _render)


def _run_main(monkeypatch, session_state, expected_module, expected_func):
    """跑 main()，返回 (实际 import 的模块名, fake_st)。"""
    fake_st = _FakeStreamlit(session_state)
    monkeypatch.setitem(__import__("sys").modules, "streamlit", fake_st)
    monkeypatch.setattr(app_module, "_get_controller", lambda: object())

    imported: Dict[str, str] = {}
    import importlib as _importlib

    def _fake_import(name):
        imported["module"] = name
        return _FakePageModule(fake_st, expected_func)

    monkeypatch.setattr(_importlib, "import_module", _fake_import)
    app_module.main()
    return imported.get("module"), fake_st


@pytest.mark.parametrize(
    "page_const,module,func",
    [
        (config.STREAMLIT_PAGE_INPUT, "ui.pages.paper_input", "render_paper_input_page"),
        (config.STREAMLIT_PAGE_PROGRESS, "ui.pages.analysis_progress", "render_analysis_progress_page"),
        (config.STREAMLIT_PAGE_REVIEW, "ui.pages.plan_review", "render_plan_review_page"),
        (config.STREAMLIT_PAGE_EXECUTION, "ui.pages.execution_monitor", "render_execution_monitor_page"),
        (config.STREAMLIT_PAGE_REPORT, "ui.pages.result_report", "render_result_report_page"),
    ],
)
def test_main_dispatches_all_five_pages(monkeypatch, page_const, module, func):
    """CP-E1-3：main() 对全部五页 current_page 均按 _PAGE_MAP 正确分发（含 sp2 三页未被破坏）。"""
    mod, fake_st = _run_main(monkeypatch, {"current_page": page_const}, module, func)
    assert mod == module, f"page={page_const} 应 import {module}，实际 {mod}"
    assert fake_st.rendered == [func], f"page={page_const} 应调用 {func}，实际 {fake_st.rendered}"
    assert fake_st.info_called is False


def test_main_unknown_page_falls_back_to_input(monkeypatch):
    """CP-E1-3 降级：非法 current_page → _PAGE_MAP.get(..., input) 回退到论文输入页（不崩溃）。"""
    mod, fake_st = _run_main(
        monkeypatch,
        {"current_page": "totally-bogus-page"},
        "ui.pages.paper_input",
        "render_paper_input_page",
    )
    assert mod == "ui.pages.paper_input", "非法页应回退到 input 页"
    assert fake_st.rendered == ["render_paper_input_page"]


def test_main_missing_current_page_defaults_to_input(monkeypatch):
    """CP-E1-3 降级：session_state 无 current_page 键 → 默认 STREAMLIT_PAGE_INPUT。

    main() 在 _init_session_state() 后会 setdefault current_page；这里直接给一个**已含
    current_page 但缺失场景**等价的空 session（_init_session_state 会补 "input"），断言
    最终 dispatch 到 input 页。
    """
    # 不预置 current_page；_init_session_state 会 setdefault("current_page","input")。
    mod, fake_st = _run_main(
        monkeypatch,
        {},
        "ui.pages.paper_input",
        "render_paper_input_page",
    )
    assert mod == "ui.pages.paper_input"
    assert fake_st.rendered == ["render_paper_input_page"]


def test_main_attribute_error_also_degrades_gracefully(monkeypatch):
    """CP-E1-3：页面模块存在但缺 render 函数（AttributeError）也走优雅降级，不崩溃。

    模拟 import_module 成功但模块没有期望的 render 函数（getattr 抛 AttributeError），
    断言 main() 走 except (ImportError, AttributeError) → st.info，不向上抛。
    """
    fake_st = _FakeStreamlit({"current_page": config.STREAMLIT_PAGE_REPORT})
    monkeypatch.setitem(__import__("sys").modules, "streamlit", fake_st)
    monkeypatch.setattr(app_module, "_get_controller", lambda: object())

    import importlib as _importlib

    class _EmptyModule:  # 没有 render_result_report_page 属性
        pass

    monkeypatch.setattr(_importlib, "import_module", lambda name: _EmptyModule())

    app_module.main()  # 不应抛 AttributeError

    assert fake_st.info_called, "缺 render 函数应走 st.info 优雅降级"
    assert fake_st.rendered == [], "降级时不应调用任何 render 函数"
