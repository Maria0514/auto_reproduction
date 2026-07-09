"""Sprint 5 批次 0 任务 T-S5-0-2 单测：S5-08 UI 主路由修复全套 + 完成判定兜底（P0）。

覆盖 dev-plan §T-S5-0-2 检查点 CP-0.2-1 ~ CP-0.2-4（CP-0.2-5 为回归样本只读手动验证，
不在本文件；与测试工程师协作，见 dev-plan L254）。

架构参考：sprint5/architecture.md §7.8（最小方案裁决：两页局部 case 规则修复，
不建统一路由层；app.py:393 全局路由保持只管 user_input_request）。

测试策略（沿用 sp2 analysis_progress / sp3 execution_monitor 既有范式）：
    - 纯函数直测：analysis_progress._interrupt_route_target /
      execution_monitor._is_reporting_without_report 真值表；
    - AppTest + mock GraphController（patch("app._get_controller")）跑真实 render()，
      controller state 序列驱动路由断言（current_page 落点 + autorefresh 注册与否）；
    - GraphController.is_finished 用 fake snapshot 直测三态（运行中 / interrupted /
      完成）+ 两个防御边界（无 snapshot / 空 values 快照）。

验收锚点：
    - AC-S5-15：coding/execution → 执行监控页；reporting ∧ report_path 非空 → 报告页
      （progress 页 case④ter 与监控页 case⑥ 双通道各证一次）；
    - AC-S5-16：interrupt kind 分发——dev_loop_failure / user_input_request → 执行监控页，
      planning 形态 → review 页；
    - AC-S5-17：reporting ∧ report_path 空 ∧ is_finished → 失败/降级卡片 + 停假轮询。

运行::

    .venv/bin/python -m pytest tests/test_sprint5_s5_08_routing.py -q
"""

from __future__ import annotations

import importlib
import threading
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import MagicMock, patch

import pytest
from streamlit.testing.v1 import AppTest

from config import (
    STREAMLIT_PAGE_EXECUTION,
    STREAMLIT_PAGE_REPORT,
    STREAMLIT_PAGE_REVIEW,
)


def _progress_mod():
    """importlib 取模块（避免 __init__ 显式 export 遮蔽子模块的已知坑，CLAUDE 坑6）。"""
    return importlib.import_module("ui.pages.analysis_progress")


def _monitor_mod():
    return importlib.import_module("ui.pages.execution_monitor")


# --------------------------------------------------------------------------- #
# 夹具：state / controller mock 工厂
# --------------------------------------------------------------------------- #
def _make_state(
    *,
    current_step: str = "paper_analysis",
    report_path: Optional[str] = None,
    error: Optional[str] = None,
) -> Dict[str, Any]:
    """构造 GlobalState 形态 dict（progress / monitor 两页消费字段的最小并集）。"""
    return {
        "current_step": current_step,
        "degraded_nodes": [],
        "error": error,
        "node_errors": [],
        "paper_meta": None,
        "fix_loop_count": 0,
        "fix_loop_history": [],
        "execution_result": None,
        "report_path": report_path,
    }


def _make_controller_mock(
    *,
    state: Optional[Dict[str, Any]] = None,
    is_interrupted: bool = False,
    interrupt_payload: Optional[Dict[str, Any]] = None,
    interrupt_kind: Optional[str] = None,
    worker_error: Optional[Exception] = None,
    is_finished: bool = False,
) -> MagicMock:
    """构造 GraphController mock：显式脚本化全部只读方法（含 S5-08 新增 is_finished）。"""
    controller = MagicMock()
    controller.poll_state.return_value = state
    controller.is_interrupted.return_value = is_interrupted
    controller.get_interrupt_payload.return_value = interrupt_payload
    controller.interrupt_kind.return_value = interrupt_kind
    controller.get_worker_error.return_value = worker_error
    controller.is_finished.return_value = is_finished
    return controller


# AppTest 脚本（沿用既有范式）：render() 内跳转会改 current_page 并 st.rerun()，脚本按
# current_page 路由，非本页渲染占位 stub 跳出 rerun 循环（与 app.py _PAGE_MAP 行为对齐）。
# S5-08 新增 execution / report 两个落点 stub（既有 progress 测试脚本只有 review/input）。
_PROGRESS_SCRIPT = """
import streamlit as st
st.session_state.setdefault("thread_id", "task-s5-08-prog")
st.session_state.setdefault("current_page", "progress")
page = st.session_state.get("current_page", "progress")
if page == "progress":
    from ui.pages.analysis_progress import render
    render()
elif page == "review":
    st.write("REVIEW_STUB")
elif page == "execution":
    st.write("EXECUTION_STUB")
elif page == "report":
    st.write("REPORT_STUB")
else:
    st.write("INPUT_STUB")
"""

_MONITOR_SCRIPT = """
import streamlit as st
st.session_state.setdefault("thread_id", "task-s5-08-exec")
st.session_state.setdefault("current_page", "execution")
page = st.session_state.get("current_page", "execution")
if page == "execution":
    from ui.pages.execution_monitor import render
    render()
elif page == "report":
    st.write("REPORT_STUB")
elif page == "review":
    st.write("REVIEW_STUB")
else:
    st.write("INPUT_STUB")
"""


def _run_progress(controller: MagicMock) -> Tuple[AppTest, MagicMock]:
    """跑 progress 页 AppTest，patch autorefresh 观测注册与否。"""
    with patch("app._get_controller", return_value=controller), patch(
        "ui.pages.analysis_progress.st_autorefresh"
    ) as ar:
        at = AppTest.from_string(_PROGRESS_SCRIPT)
        at.run()
    return at, ar


def _run_monitor(controller: MagicMock) -> Tuple[AppTest, MagicMock]:
    """跑 execution_monitor 页 AppTest，patch autorefresh 观测注册与否。"""
    with patch("app._get_controller", return_value=controller), patch(
        "ui.pages.execution_monitor.st_autorefresh"
    ) as ar:
        at = AppTest.from_string(_MONITOR_SCRIPT)
        at.run()
    return at, ar


def _collect_text(at: AppTest) -> str:
    parts: List[str] = []
    for collection in (at.title, at.subheader, at.caption, at.markdown,
                       at.text, at.warning, at.info, at.error):
        for el in collection:
            parts.append(str(getattr(el, "value", "")))
    for el in getattr(at, "code", []):
        parts.append(str(getattr(el, "value", "")))
    return "\n".join(parts)


# =========================================================================== #
# 防漂移：UI 本地 kind 常量与 core 侧权威定义一致（沿用 sp3 E2 / sp4 F1 先例）
# =========================================================================== #
def test_kind_constants_aligned_with_core():
    """analysis_progress 本地 kind 字符串与 execution 节点 / interaction 工具权威值一致。"""
    mod = _progress_mod()
    from core.nodes.execution import INTERRUPT_KIND as DEV_LOOP_KIND
    from core.tools.interaction_tools import INTERRUPT_KIND_USER_INPUT

    assert mod._INTERRUPT_KIND_DEV_LOOP == DEV_LOOP_KIND == "dev_loop_failure"
    assert mod._INTERRUPT_KIND_USER_INPUT == INTERRUPT_KIND_USER_INPUT == "user_input_request"


# =========================================================================== #
# CP-0.2-1：case④ kind 分发三态（AC-S5-16）
# =========================================================================== #
def test_cp_0_2_1_interrupt_route_target_pure():
    """纯函数真值表：planning 形态（无 kind 键 / 显式 planning / None / 非 dict）→ review；
    dev_loop_failure / user_input_request → execution；未知 kind → review 兜底。"""
    mod = _progress_mod()
    rt = mod._interrupt_route_target
    # planning 形态：payload 无 interrupt_kind 键（sp2 旧形态）
    assert rt({"plan": {"steps": []}}) == STREAMLIT_PAGE_REVIEW
    # planning 显式 kind（D1 后新 payload）
    assert rt({"interrupt_kind": "planning"}) == STREAMLIT_PAGE_REVIEW
    # 防御：payload None / 非 dict → review 兜底
    assert rt(None) == STREAMLIT_PAGE_REVIEW
    assert rt("weird") == STREAMLIT_PAGE_REVIEW
    # dev_loop_failure / user_input_request → 执行监控页
    assert rt({"interrupt_kind": "dev_loop_failure"}) == STREAMLIT_PAGE_EXECUTION
    assert rt({"interrupt_kind": "user_input_request"}) == STREAMLIT_PAGE_EXECUTION
    # 未知 kind → review 兜底（不自创第三分类，架构 §7.8 极简裁决）
    assert rt({"interrupt_kind": "something_else"}) == STREAMLIT_PAGE_REVIEW


def test_cp_0_2_1_planning_payload_routes_to_review():
    """AppTest：planning 形态 interrupt（payload 无 kind 键）→ review 页 + 停本页轮询。"""
    controller = _make_controller_mock(
        state=_make_state(current_step="planning"),
        is_interrupted=True,
        interrupt_payload={"plan": {"steps": []}},  # 无 interrupt_kind 键
    )
    at, ar = _run_progress(controller)
    assert not at.exception, at.exception
    assert at.session_state["current_page"] == STREAMLIT_PAGE_REVIEW
    ar.assert_not_called()  # 跳转路径不注册 autorefresh


@pytest.mark.parametrize("kind", ["dev_loop_failure", "user_input_request"])
def test_cp_0_2_1_dev_loop_and_user_input_route_to_execution(kind):
    """AppTest：dev_loop_failure / user_input_request interrupt → 执行监控页（AC-S5-16）。"""
    controller = _make_controller_mock(
        state=_make_state(current_step="execution"),
        is_interrupted=True,
        interrupt_payload={"interrupt_kind": kind},
    )
    at, ar = _run_progress(controller)
    assert not at.exception, at.exception
    assert at.session_state["current_page"] == STREAMLIT_PAGE_EXECUTION
    ar.assert_not_called()


# =========================================================================== #
# CP-0.2-2：case④bis / case④ter（AC-S5-15 mock 部分，双通道各证一次）
# =========================================================================== #
@pytest.mark.parametrize("step", ["coding", "execution"])
def test_cp_0_2_2_case4bis_coding_execution_route_to_monitor(step):
    """progress 页 case④bis：current_step ∈ {coding, execution}（非 interrupt）→ 执行监控页。"""
    controller = _make_controller_mock(state=_make_state(current_step=step))
    at, ar = _run_progress(controller)
    assert not at.exception, at.exception
    assert at.session_state["current_page"] == STREAMLIT_PAGE_EXECUTION
    ar.assert_not_called()  # 跳转路径不注册 autorefresh


def test_cp_0_2_2_case4ter_reporting_with_report_routes_to_report():
    """progress 页 case④ter：reporting ∧ report_path 非空 → 直跳报告页（通道一）。"""
    controller = _make_controller_mock(
        state=_make_state(current_step="reporting", report_path="/tmp/report.md")
    )
    at, ar = _run_progress(controller)
    assert not at.exception, at.exception
    assert at.session_state["current_page"] == STREAMLIT_PAGE_REPORT
    ar.assert_not_called()


def test_cp_0_2_2_monitor_case6_reporting_with_report_routes_to_report():
    """监控页 case⑥：reporting ∧ report_path 非空 ∧ 非 interrupt → 报告页（通道二，双通道齐）。"""
    controller = _make_controller_mock(
        state=_make_state(current_step="reporting", report_path="/tmp/report.md")
    )
    at, ar = _run_monitor(controller)
    assert not at.exception, at.exception
    assert at.session_state["current_page"] == STREAMLIT_PAGE_REPORT
    ar.assert_not_called()


def test_cp_0_2_2_state_sequence_progress_to_monitor_to_report():
    """controller state 序列驱动：planning 正常 → coding（切监控页）→ reporting+report_path
    （监控页跳报告页）——模拟批准计划后顺利执行全程的页面流转（AC-S5-15）。"""
    seq_states = [
        _make_state(current_step="planning"),                                 # run1: progress 正常渲染
        _make_state(current_step="coding"),                                   # run2: progress → execution
        _make_state(current_step="reporting", report_path="/tmp/report.md"),  # run3: monitor → report
    ]
    controller = _make_controller_mock()
    controller.poll_state.side_effect = seq_states

    script = """
import streamlit as st
st.session_state.setdefault("thread_id", "task-s5-08-seq")
st.session_state.setdefault("current_page", "progress")
page = st.session_state.get("current_page", "progress")
if page == "progress":
    from ui.pages.analysis_progress import render
    render()
elif page == "execution":
    from ui.pages.execution_monitor import render
    render()
elif page == "report":
    st.write("REPORT_STUB")
else:
    st.write("OTHER_STUB")
"""
    with patch("app._get_controller", return_value=controller), patch(
        "ui.pages.analysis_progress.st_autorefresh"
    ), patch("ui.pages.execution_monitor.st_autorefresh"):
        at = AppTest.from_string(script)
        at.run()  # run1：planning 非终态 → 停 progress 页
        assert not at.exception, at.exception
        assert at.session_state["current_page"] == "progress"
        at.run()  # run2：coding → case④bis 切执行监控页（rerun 后监控页用 run3 state 跳报告页）
        assert not at.exception, at.exception
        assert at.session_state["current_page"] == STREAMLIT_PAGE_REPORT


def test_cp_0_2_2_progress_reporting_without_report_keeps_polling():
    """反证钉行为：progress 页 reporting ∧ report_path 空 → 不跳转、正常渲染继续轮询
    （僵死判定归监控页 case⑥bis，progress 页不重复建判定——极简边界）。"""
    controller = _make_controller_mock(
        state=_make_state(current_step="reporting", report_path=None)
    )
    at, ar = _run_progress(controller)
    assert not at.exception, at.exception
    assert at.session_state["current_page"] == "progress"
    ar.assert_called_once()


# =========================================================================== #
# CP-0.2-3：case⑥bis 失败/降级卡片 + 停假轮询（AC-S5-17）
# =========================================================================== #
def test_cp_0_2_3_is_reporting_without_report_pure():
    """纯函数真值表：reporting ∧ report_path 空（None / 空串）→ True；其余 False。"""
    mod = _monitor_mod()
    fn = mod._is_reporting_without_report
    assert fn({"current_step": "reporting", "report_path": None}) is True
    assert fn({"current_step": "reporting", "report_path": ""}) is True
    assert fn({"current_step": "reporting"}) is True  # 键缺失同空
    # report_path 非空 → False（归 case⑥ 跳报告页）
    assert fn({"current_step": "reporting", "report_path": "/tmp/r.md"}) is False
    # 非 reporting 阶段 → False
    assert fn({"current_step": "execution", "report_path": None}) is False
    assert fn({"current_step": "coding", "report_path": None}) is False
    # 防御：None / 非 dict → False
    assert fn(None) is False
    assert fn("x") is False


def test_cp_0_2_3_case6bis_finished_no_report_renders_failure_card_stops_polling():
    """case⑥bis：reporting ∧ report_path 空 ∧ is_finished=True → 失败/降级卡片渲染 +
    autorefresh 不注册（停假轮询）+ 不跳页（AC-S5-17 核心断言）。"""
    controller = _make_controller_mock(
        state=_make_state(current_step="reporting", report_path=None),
        is_finished=True,
    )
    at, ar = _run_monitor(controller)
    assert not at.exception, at.exception
    text = _collect_text(at)
    assert "报告未生成" in text  # 明确失败/降级提示（原生 st.error，AppTest 可见）
    assert at.session_state["current_page"] == "execution"  # 无报告可跳，留在本页
    ar.assert_not_called()  # 停假轮询的核心：不注册 autorefresh
    controller.is_finished.assert_called_once_with("task-s5-08-exec")


def test_cp_0_2_3_case6bis_empty_string_report_path_also_triggers():
    """case⑥bis 边界：report_path=""（空串）同样触发失败卡片（与 case⑥ bool 判定对偶）。"""
    controller = _make_controller_mock(
        state=_make_state(current_step="reporting", report_path=""),
        is_finished=True,
    )
    at, ar = _run_monitor(controller)
    assert not at.exception, at.exception
    assert "报告未生成" in _collect_text(at)
    ar.assert_not_called()


def test_cp_0_2_3_case6bis_not_finished_keeps_polling():
    """反证：reporting ∧ report_path 空但 is_finished=False（reporting 节点仍在跑）→
    正常渲染 + 注册 autorefresh（正当轮询，不误杀）。"""
    controller = _make_controller_mock(
        state=_make_state(current_step="reporting", report_path=None),
        is_finished=False,
    )
    at, ar = _run_monitor(controller)
    assert not at.exception, at.exception
    text = _collect_text(at)
    assert "报告未生成" not in text
    assert "执行监控" in text  # case⑦ 正常渲染
    ar.assert_called_once()


def test_cp_0_2_3_case6_priority_over_case6bis():
    """优先级：report_path 非空时 case⑥ 先命中跳报告页，case⑥bis 不触发
    （is_finished 不被调用——短路即省一次 checkpoint 读）。"""
    controller = _make_controller_mock(
        state=_make_state(current_step="reporting", report_path="/tmp/report.md"),
        is_finished=True,
    )
    at, _ = _run_monitor(controller)
    assert not at.exception, at.exception
    assert at.session_state["current_page"] == STREAMLIT_PAGE_REPORT
    controller.is_finished.assert_not_called()


# =========================================================================== #
# CP-0.2-4：GraphController.is_finished 三态单测（+ 防御边界）
# =========================================================================== #
class _FakeInterrupt:
    def __init__(self, value: Any = None) -> None:
        self.value = value
        self.id = "int-1"


class _FakeTask:
    def __init__(self, name: str = "planning", interrupts: Tuple = ()) -> None:
        self.name = name
        self.interrupts = interrupts


class _FakeSnapshot:
    def __init__(self, values: Dict, next_: Tuple, tasks: Tuple = ()) -> None:
        self.values = values
        self.next = next_
        self.tasks = tasks


def _controller_with_snapshot(snapshot: Optional[_FakeSnapshot]):
    """构造真实 GraphController（绕过 __init__，不建真图/真库），main_graph 换 fake。"""
    import app as app_module

    controller = app_module.GraphController.__new__(app_module.GraphController)
    controller._lock = threading.Lock()
    controller._workers = {}
    controller._worker_errors = {}
    controller._main_checkpointer = object()
    graph = MagicMock()
    graph.get_state.return_value = snapshot
    controller._main_graph = graph
    return controller


def test_cp_0_2_4_is_finished_running_false():
    """三态①（运行中）：next 非空（无 interrupt）→ is_finished=False。"""
    c = _controller_with_snapshot(
        _FakeSnapshot(values={"current_step": "coding"}, next_=("coding",))
    )
    assert c.is_finished("t1") is False
    assert c.is_interrupted("t1") is False


def test_cp_0_2_4_is_finished_interrupted_false():
    """三态②（interrupt 暂停）：next 非空 + task 含 interrupts → is_finished=False
    （is_interrupted=True，两方法在同一 snapshot 上语义正交）。"""
    c = _controller_with_snapshot(
        _FakeSnapshot(
            values={"current_step": "planning"},
            next_=("planning",),
            tasks=(_FakeTask(interrupts=(_FakeInterrupt({"plan": {}}),)),),
        )
    )
    assert c.is_finished("t2") is False
    assert c.is_interrupted("t2") is True


def test_cp_0_2_4_is_finished_completed_true():
    """三态③（已完成）：values 非空 ∧ next 为空元组 → is_finished=True（图已到 END）。"""
    c = _controller_with_snapshot(
        _FakeSnapshot(
            values={"current_step": "reporting", "report_path": None}, next_=()
        )
    )
    assert c.is_finished("t3") is True
    assert c.is_interrupted("t3") is False


def test_cp_0_2_4_is_finished_no_snapshot_false():
    """防御边界①：get_state 返回 None（FakeGraph 未知 thread 形态）→ False，不抛异常。"""
    c = _controller_with_snapshot(None)
    assert c.is_finished("t-unknown") is False


def test_cp_0_2_4_is_finished_empty_values_snapshot_false():
    """防御边界②：values={} 的空快照（LangGraph 对从未启动 thread 的真实返回形态，
    next 也是空元组）→ 不得误判为已完成。"""
    c = _controller_with_snapshot(_FakeSnapshot(values={}, next_=()))
    assert c.is_finished("t-never-started") is False
