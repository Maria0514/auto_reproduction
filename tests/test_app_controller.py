"""Sprint 2 任务 D2 单测：app.py::GraphController（S2-08）。

覆盖 dev-plan §D2 检查点 CP-D2-1 ~ CP-D2-10。

测试策略（dev-plan §D2 自测要求）：
- CP-D2-1~9 尽量用**真实 GraphController** + **mock 图**（FakeGraph），仅 CP-D2-9 用
  真实 SqliteSaver（tempfile）回读验证每线程独立实例 + WAL 并发；
- 工作线程是 daemon 线程，测试中用 thread.join(timeout) 同步等待其自然退出后再断言；
- CP-D2-10 是 sp1 回归守门，由 `pytest -q -m "not e2e"` 全量回归覆盖，不在本文件内重复跑
  （A3/C1 已守门，D2 仅新增 app.py），此处仅留说明性占位测试断言新代码可与 sp1 并存导入。
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

import pytest

import app as app_module
from app import (
    GraphController,
    _make_config,
    _refresh_llm_config_set,
    _OVERRIDE_NODES as _OVERRIDE_NODES_FROM_APP,
)
from core.activity_stream import ActivityStreamHandler
from core.errors import LLMError


# ----------------------------------------------------------------------
# 测试夹具：FakeGraph / FakeSnapshot / FakeTask
# ----------------------------------------------------------------------


class _FakeInterrupt:
    def __init__(self, value: Any = None) -> None:
        self.value = value
        self.id = "int-1"


class _FakeTask:
    def __init__(self, name: str = "planning", interrupts: Tuple = ()) -> None:
        self.name = name
        self.interrupts = interrupts


class _FakeSnapshot:
    def __init__(self, values: Dict, next_: Tuple, tasks: Tuple) -> None:
        self.values = values
        self.next = next_
        self.tasks = tasks


class FakeGraph:
    """可控的 mock CompiledGraph。

    - invoke(state_or_command, config)：记录调用；可注入异常或副作用回调；
    - get_state(config)：返回预置的 snapshot（按 thread_id），模拟 interrupt / END 状态。
    """

    def __init__(self) -> None:
        self.invoke_calls: List[Tuple[Any, Dict]] = []
        self.invoke_exc: Optional[Exception] = None
        self.invoke_side_effect = None  # callable(state_or_command, config) -> None
        self._snapshots: Dict[str, _FakeSnapshot] = {}
        self._lock = threading.Lock()

    def set_snapshot(self, thread_id: str, snapshot: Optional[_FakeSnapshot]) -> None:
        with self._lock:
            if snapshot is None:
                self._snapshots.pop(thread_id, None)
            else:
                self._snapshots[thread_id] = snapshot

    def invoke(self, state_or_command: Any, config: Dict) -> Dict:
        with self._lock:
            self.invoke_calls.append((state_or_command, config))
        if self.invoke_side_effect is not None:
            self.invoke_side_effect(state_or_command, config)
        if self.invoke_exc is not None:
            raise self.invoke_exc
        return {}

    def get_state(self, config: Dict) -> Optional[_FakeSnapshot]:
        thread_id = config["configurable"]["thread_id"]
        with self._lock:
            return self._snapshots.get(thread_id)


def _make_llm_config_set(api_key_default: str = "sk-default") -> Dict:
    """构造一份含 default + 4 个 override 的 LLMConfigSet（每条独立 api_key）。"""
    def _cfg(key: str) -> Dict:
        return {
            "base_url": "https://example.com/v1",
            "model": "gpt-test",
            "api_key": key,
            "temperature": 0.3,
            "max_tokens": 4096,
        }

    return {
        "default": _cfg(api_key_default),
        "overrides": {
            "paper_intake": _cfg("sk-intake"),
            "paper_analysis": _cfg("sk-analysis"),
            "resource_scout": _cfg("sk-scout"),
            "planning": _cfg("sk-planning"),
        },
    }


@pytest.fixture
def patched_controller(monkeypatch, tmp_path):
    """构造一个 GraphController，build_graph 返回 FakeGraph，get_checkpointer 返回哨兵。

    每次调用 get_checkpointer 返回一个**新的**哨兵对象，便于断言"每线程独立实例"。
    build_graph 始终返回同一个 FakeGraph（便于测试集中观测 invoke / get_state）。
    """
    fake_graph = FakeGraph()
    created_checkpointers: List[object] = []

    def _fake_get_checkpointer(db_path=None):
        sentinel = object()
        created_checkpointers.append(sentinel)
        return sentinel

    def _fake_build_graph(checkpointer=None):
        return fake_graph

    monkeypatch.setattr(app_module, "get_checkpointer", _fake_get_checkpointer)
    monkeypatch.setattr(app_module, "build_graph", _fake_build_graph)

    controller = GraphController()
    return controller, fake_graph, created_checkpointers


def _join_workers(controller: GraphController, timeout: float = 5.0) -> None:
    """等待 controller 内所有 worker 线程自然退出。"""
    with controller._lock:
        threads = list(controller._workers.values())
    for t in threads:
        t.join(timeout=timeout)


# ----------------------------------------------------------------------
# CP-D2-1：导入 + 实例化
# ----------------------------------------------------------------------


def test_cp_d2_1_import_and_instantiate(patched_controller):
    """CP-D2-1：from app import GraphController 可导入；GraphController() 可实例化。"""
    controller, fake_graph, created = patched_controller
    assert isinstance(controller, GraphController)
    # __init__ 应建立主线程独占 checkpointer + graph。
    assert controller._main_checkpointer is created[0]
    assert controller._main_graph is fake_graph
    assert controller._workers == {}
    assert controller._worker_errors == {}


# ----------------------------------------------------------------------
# CP-D2-2：start_task 返回 thread_id + 启动 worker；invoke 完成后 worker 自然退出
# ----------------------------------------------------------------------


def test_cp_d2_2_start_task_returns_thread_id_and_worker_exits(patched_controller):
    """CP-D2-2：start_task 返回 task-XXX thread_id，启动 worker，invoke 后自然退出。"""
    controller, fake_graph, _ = patched_controller

    # 用 side_effect 阻塞 invoke 一小段，确保能观察到 is_alive()==True。
    started = threading.Event()
    release = threading.Event()

    def _block(state_or_command, config):
        started.set()
        release.wait(timeout=5.0)

    fake_graph.invoke_side_effect = _block

    thread_id = controller.start_task("2405.14831", _make_llm_config_set())
    assert isinstance(thread_id, str)
    assert thread_id.startswith("task-")

    assert started.wait(timeout=5.0)
    worker = controller._workers[thread_id]
    assert worker.is_alive() is True  # invoke 进行中

    release.set()
    worker.join(timeout=5.0)
    assert worker.is_alive() is False  # invoke 完成后自然退出

    # invoke 用了 _make_config 注入的 config（含 checkpoint_ns）。
    # [S5-07/T-S5-4-2 适配] _worker_run 现按规格追加 callbacks=[ActivityStreamHandler]
    # （活动流注入，架构 sprint5 §4 Q-S5-8）：configurable 部分仍严格全等（语义不降），
    # 并钉死 config 键集合 = {configurable, callbacks}（不得再夹带其它键）。
    assert len(fake_graph.invoke_calls) == 1
    _, cfg = fake_graph.invoke_calls[0]
    assert cfg["configurable"] == _make_config(thread_id)["configurable"]
    assert set(cfg) == {"configurable", "callbacks"}
    assert [type(h) for h in cfg["callbacks"]] == [ActivityStreamHandler]


# ----------------------------------------------------------------------
# CP-D2-3：poll_state 走 main_graph 独立读取
# ----------------------------------------------------------------------


def test_cp_d2_3_poll_state_uses_main_graph(patched_controller):
    """CP-D2-3：poll_state 通过独立 main_graph 读 state，返回 snapshot.values。"""
    controller, fake_graph, _ = patched_controller
    thread_id = "task-poll"
    fake_graph.set_snapshot(
        thread_id,
        _FakeSnapshot(values={"current_step": "paper_analysis"}, next_=("paper_analysis",), tasks=()),
    )
    state = controller.poll_state(thread_id)
    assert state == {"current_step": "paper_analysis"}

    # 无 snapshot 时返回 None。
    assert controller.poll_state("task-unknown") is None


# ----------------------------------------------------------------------
# CP-D2-4：is_interrupted 在 interrupt 状态 True；END 状态 False
# ----------------------------------------------------------------------


def test_cp_d2_4_is_interrupted_true_at_planning_interrupt(patched_controller):
    """CP-D2-4：planning interrupt 状态返回 True（next 非空 + tasks 含 interrupt 元数据）。"""
    controller, fake_graph, _ = patched_controller
    thread_id = "task-int"
    fake_graph.set_snapshot(
        thread_id,
        _FakeSnapshot(
            values={"current_step": "planning"},
            next_=("planning",),
            tasks=(_FakeTask(name="planning", interrupts=(_FakeInterrupt({"hint": "x"}),)),),
        ),
    )
    assert controller.is_interrupted(thread_id) is True


def test_cp_d2_4_is_interrupted_false_at_end(patched_controller):
    """CP-D2-4：graph 已推进到 END（next 为空元组）返回 False。"""
    controller, fake_graph, _ = patched_controller
    thread_id = "task-end"
    fake_graph.set_snapshot(
        thread_id,
        _FakeSnapshot(values={"current_step": "reporting"}, next_=(), tasks=()),
    )
    assert controller.is_interrupted(thread_id) is False


def test_cp_d2_4_is_interrupted_false_when_next_but_no_interrupt(patched_controller):
    """CP-D2-4 补充：next 非空但 tasks 无 interrupt 元数据（普通节点边界）返回 False。"""
    controller, fake_graph, _ = patched_controller
    thread_id = "task-running"
    fake_graph.set_snapshot(
        thread_id,
        _FakeSnapshot(values={}, next_=("coding",), tasks=(_FakeTask(name="coding", interrupts=()),)),
    )
    assert controller.is_interrupted(thread_id) is False


# ----------------------------------------------------------------------
# CP-D2-5：resume_with 起新工作线程 + 独立 SqliteSaver 实例
# ----------------------------------------------------------------------


def test_cp_d2_5_resume_with_spawns_new_worker_and_saver(patched_controller):
    """CP-D2-5：resume_with 起新 daemon worker，新线程独立创建 SqliteSaver 实例。"""
    controller, fake_graph, created = patched_controller
    thread_id = "task-resume"

    n_savers_before = len(created)
    controller.resume_with(thread_id, {"decision": "approve"})
    worker = controller._workers[thread_id]
    assert worker.daemon is True
    worker.join(timeout=5.0)
    assert worker.is_alive() is False

    # resume worker 独立创建了一个新的 checkpointer 实例。
    assert len(created) == n_savers_before + 1

    # invoke 收到的是 Command(resume=...)，且 config 经 _make_config 注入。
    assert len(fake_graph.invoke_calls) == 1
    arg, cfg = fake_graph.invoke_calls[0]
    from langgraph.types import Command

    assert isinstance(arg, Command)
    assert arg.resume == {"decision": "approve"}
    # [S5-07/T-S5-4-2 适配] 同 CP-D2-2：resume 路径亦按规格注入 callbacks，
    # configurable 严格全等 + config 键集合钉死（语义不降）。
    assert cfg["configurable"] == _make_config(thread_id)["configurable"]
    assert set(cfg) == {"configurable", "callbacks"}
    assert [type(h) for h in cfg["callbacks"]] == [ActivityStreamHandler]


# ----------------------------------------------------------------------
# CP-D2-6：工作线程异常 → _worker_errors + get_worker_error
# ----------------------------------------------------------------------


def test_cp_d2_6_worker_error_captured(patched_controller):
    """CP-D2-6：mock graph.invoke 抛 LLMError，_worker_errors 含异常对象，可被读出。"""
    controller, fake_graph, _ = patched_controller
    err = LLMError("boom", detail="mock invoke failure")
    fake_graph.invoke_exc = err

    thread_id = controller.start_task("2405.14831", _make_llm_config_set())
    controller._workers[thread_id].join(timeout=5.0)

    captured = controller.get_worker_error(thread_id)
    assert captured is err
    assert isinstance(captured, LLMError)


def test_cp_d2_6_resume_worker_error_captured(patched_controller):
    """CP-D2-6 补充：resume worker 异常同样被捕获（100% 崩溃感知）。"""
    controller, fake_graph, _ = patched_controller
    err = LLMError("resume-boom")
    fake_graph.invoke_exc = err

    thread_id = "task-resume-err"
    controller.resume_with(thread_id, {"decision": "revise"})
    controller._workers[thread_id].join(timeout=5.0)

    assert controller.get_worker_error(thread_id) is err


# ----------------------------------------------------------------------
# CP-D2-7：cancel_task 两路径
# ----------------------------------------------------------------------


def test_cp_d2_7_cancel_task_in_interrupt_routes_to_cancel(patched_controller):
    """CP-D2-7：interrupt 状态下 cancel_task 走 resume_with({"decision":"cancel"}) 通道。"""
    controller, fake_graph, _ = patched_controller
    thread_id = "task-cancel"
    # 先让 is_interrupted 返回 True。
    fake_graph.set_snapshot(
        thread_id,
        _FakeSnapshot(
            values={"current_step": "planning"},
            next_=("planning",),
            tasks=(_FakeTask(name="planning", interrupts=(_FakeInterrupt(),)),),
        ),
    )

    # cancel 后 graph 推进 → 用 side_effect 模拟 planning 写 current_step=cancelled_by_user。
    def _on_cancel(arg, config):
        from langgraph.types import Command

        if isinstance(arg, Command) and arg.resume == {"decision": "cancel"}:
            tid = config["configurable"]["thread_id"]
            fake_graph.set_snapshot(
                tid,
                _FakeSnapshot(values={"current_step": "cancelled_by_user"}, next_=(), tasks=()),
            )

    fake_graph.invoke_side_effect = _on_cancel

    controller.cancel_task(thread_id)
    controller._workers[thread_id].join(timeout=5.0)

    # invoke 收到 cancel payload。
    arg, _ = fake_graph.invoke_calls[-1]
    from langgraph.types import Command

    assert isinstance(arg, Command)
    assert arg.resume == {"decision": "cancel"}

    # 最终 poll_state 反映 cancelled_by_user，且不再 interrupt。
    final = controller.poll_state(thread_id)
    assert final["current_step"] == "cancelled_by_user"
    assert controller.is_interrupted(thread_id) is False


def test_cp_d2_7_cancel_task_non_interrupt_warns_no_raise(patched_controller, caplog):
    """CP-D2-7：非 interrupt 状态调 cancel_task 打 1 条 WARNING、不抛异常、不起线程。"""
    controller, fake_graph, _ = patched_controller
    thread_id = "task-not-int"
    # 无 snapshot（任务未启动或已结束）→ is_interrupted False。
    with caplog.at_level(logging.WARNING, logger="app"):
        controller.cancel_task(thread_id)  # 不应抛异常

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING and "非 interrupt" in r.getMessage()]
    assert len(warnings) == 1
    # 未起任何工作线程。
    assert thread_id not in controller._workers
    assert len(fake_graph.invoke_calls) == 0


# ----------------------------------------------------------------------
# CP-D2-8：start_task api_key 逐条强制刷新
# ----------------------------------------------------------------------


def test_cp_d2_8_api_key_refreshed_into_initial_state(patched_controller, monkeypatch):
    """CP-D2-8：default + 4 个 override 的 api_key 全部刷新到 initial_state，与表单值一致。"""
    controller, fake_graph, _ = patched_controller

    captured: Dict[str, Any] = {}

    def _block(state_or_command, config):
        # worker 的 initial_state 即首参（start_task 路径）。
        captured["state"] = state_or_command

    fake_graph.invoke_side_effect = _block

    form_config = _make_llm_config_set(api_key_default="sk-FRESH-default")
    form_config["overrides"]["paper_intake"]["api_key"] = "sk-FRESH-intake"
    form_config["overrides"]["paper_analysis"]["api_key"] = "sk-FRESH-analysis"
    form_config["overrides"]["resource_scout"]["api_key"] = "sk-FRESH-scout"
    form_config["overrides"]["planning"]["api_key"] = "sk-FRESH-planning"

    thread_id = controller.start_task("2405.14831", form_config)
    controller._workers[thread_id].join(timeout=5.0)

    state = captured["state"]
    cfg_set = state["llm_config_set"]
    assert cfg_set["default"]["api_key"] == "sk-FRESH-default"
    assert cfg_set["overrides"]["paper_intake"]["api_key"] == "sk-FRESH-intake"
    assert cfg_set["overrides"]["paper_analysis"]["api_key"] == "sk-FRESH-analysis"
    assert cfg_set["overrides"]["resource_scout"]["api_key"] == "sk-FRESH-scout"
    assert cfg_set["overrides"]["planning"]["api_key"] == "sk-FRESH-planning"


def test_cp_d2_8_refresh_drops_empty_overrides():
    """CP-D2-8 补充：_refresh_llm_config_set 丢弃空 LLMConfig override（悬挂数据清理）。"""
    config_set = {
        "default": {
            "base_url": "u", "model": "m", "api_key": "sk-d",
            "temperature": 0.3, "max_tokens": 4096,
        },
        "overrides": {
            "paper_intake": {
                "base_url": "u", "model": "m", "api_key": "sk-i",
                "temperature": 0.3, "max_tokens": 4096,
            },
            "planning": {},  # 空 → 应被丢弃
        },
    }
    refreshed = _refresh_llm_config_set(config_set)
    assert "paper_intake" in refreshed["overrides"]
    assert "planning" not in refreshed["overrides"]
    # 返回的是新对象（不复用原 default dict 引用）。
    assert refreshed["default"] is not config_set["default"]


# ----------------------------------------------------------------------
# CP-D2-9：并发 2 个 thread_id，独立 SqliteSaver + 真实 SQLite 回读
# ----------------------------------------------------------------------


def test_cp_d2_9_two_threads_independent_savers_real_sqlite(monkeypatch, tmp_path):
    """CP-D2-9：同进程并发 2 个 thread_id，各自独立 SqliteSaver 实例，checkpoint 真实回读。

    用真实 get_checkpointer（tempfile DB）+ 真实最小 graph（单节点写 current_step），
    验证两个工作线程互不阻塞、各自 thread_id 的 checkpoint 在 SQLite 中可独立读回。
    """
    db_path = str(tmp_path / "cp_d2_9.sqlite")

    from core.checkpointer import get_checkpointer as real_get_checkpointer
    from langgraph.graph import StateGraph, START, END

    class _MiniState(dict):
        pass

    from typing import TypedDict

    class MiniState(TypedDict, total=False):
        user_input: str
        current_step: str
        llm_config_set: dict

    def _node(state):
        # 模拟节点写入：记录自己的 user_input 到 current_step，便于回读区分两条线程。
        return {"current_step": f"done:{state.get('user_input')}"}

    def _real_build_graph(checkpointer=None):
        g = StateGraph(MiniState)
        g.add_node("only", _node)
        g.add_edge(START, "only")
        g.add_edge("only", END)
        return g.compile(checkpointer=checkpointer)

    created: List[object] = []

    def _get_cp(db=None):
        cp = real_get_checkpointer(db_path)
        created.append(cp)
        return cp

    monkeypatch.setattr(app_module, "get_checkpointer", _get_cp)
    monkeypatch.setattr(app_module, "build_graph", _real_build_graph)

    controller = GraphController()

    tid_a = controller.start_task("paperA", _make_llm_config_set())
    tid_b = controller.start_task("paperB", _make_llm_config_set())

    controller._workers[tid_a].join(timeout=10.0)
    controller._workers[tid_b].join(timeout=10.0)

    assert controller.get_worker_error(tid_a) is None
    assert controller.get_worker_error(tid_b) is None

    # 主线程独立读回两条 thread_id 的 checkpoint，互不串扰。
    state_a = controller.poll_state(tid_a)
    state_b = controller.poll_state(tid_b)
    assert state_a is not None and state_b is not None
    assert state_a["current_step"] == "done:paperA"
    assert state_b["current_step"] == "done:paperB"

    # main + 2 worker 至少创建了 3 个独立 checkpointer 实例（不共享 Python 实例）。
    assert len(created) >= 3
    assert len(set(id(c) for c in created)) == len(created)


# ----------------------------------------------------------------------
# CP-D2-10：sp1 + sp2 既有基线零退化守门（说明性占位）
# ----------------------------------------------------------------------


def test_cp_d2_10_new_module_coexists_with_existing():
    """CP-D2-10：app.py 新代码可与 sp1/sp2 既有模块并存导入，不破坏 import 图。

    全量回归（pytest -q -m "not e2e"）由 CI/手动执行守门；此处仅断言关键符号可导入，
    证明 D2 新增 app.py 不引入 import 副作用（如循环依赖 / 模块级 SQLite 触碰）。
    """
    import importlib

    m = importlib.import_module("app")
    assert hasattr(m, "GraphController")
    assert hasattr(m, "main")
    assert hasattr(m, "_make_config")
    # _make_config 注入 checkpoint_ns（S-2 spike 约束）。
    cfg = m._make_config("task-x")
    assert cfg["configurable"]["thread_id"] == "task-x"
    assert cfg["configurable"]["checkpoint_ns"] == ""


# ======================================================================
# 测试工程师补强用例（D2 验收 2026-06-04 @测试工程师代理）
# 覆盖 dev-plan / 架构 §2.7 未被开发 15 用例显式覆盖的边界。
# ----------------------------------------------------------------------


# ---- BND-1：cancel_task 在 END 状态被正确忽略（高风险守门补强）----


def test_bnd_cancel_at_end_state_ignored_warns(patched_controller, caplog):
    """cancel_task 在 END 状态（next=()）：打 1 条 WARNING、不起线程、不调 invoke。

    cancel 的前置守门 = is_interrupted；END 时 is_interrupted False，cancel 必须无副作用。
    这是开发 CP-D2-7「非 interrupt」分支的具体形态补强（END vs 无 snapshot 区分）。
    """
    controller, fake_graph, _ = patched_controller
    thread_id = "task-end-cancel"
    fake_graph.set_snapshot(
        thread_id,
        _FakeSnapshot(values={"current_step": "reporting"}, next_=(), tasks=()),
    )
    with caplog.at_level(logging.WARNING, logger="app"):
        controller.cancel_task(thread_id)  # 不应抛异常

    warnings = [
        r for r in caplog.records
        if r.levelno == logging.WARNING and "非 interrupt" in r.getMessage()
    ]
    assert len(warnings) == 1
    assert thread_id not in controller._workers
    assert len(fake_graph.invoke_calls) == 0


# ---- BND-2：cancel_task 在普通节点暂停（next 非空但无 interrupt）被正确忽略 ----


def test_bnd_cancel_at_running_node_ignored_warns(patched_controller, caplog):
    """cancel_task 在普通节点暂停状态（next 非空、tasks 无 interrupt 元数据）被忽略。

    这是最容易判错的边界：next 非空容易被误判为"可 cancel"，但只有真 interrupt 才
    允许 cancel（架构 §2.7.1：不支持节点执行中途强制中断）。
    """
    controller, fake_graph, _ = patched_controller
    thread_id = "task-running-cancel"
    fake_graph.set_snapshot(
        thread_id,
        _FakeSnapshot(
            values={"current_step": "coding"},
            next_=("coding",),
            tasks=(_FakeTask(name="coding", interrupts=()),),
        ),
    )
    with caplog.at_level(logging.WARNING, logger="app"):
        controller.cancel_task(thread_id)

    warnings = [
        r for r in caplog.records
        if r.levelno == logging.WARNING and "非 interrupt" in r.getMessage()
    ]
    assert len(warnings) == 1
    assert thread_id not in controller._workers
    assert len(fake_graph.invoke_calls) == 0


# ---- BND-3：is_interrupted 区分 tasks 为空 vs interrupts 为空 ----


def test_bnd_is_interrupted_empty_tasks_returns_false(patched_controller):
    """next 非空但 tasks 为空元组：_has_interrupt 遍历不到任何 task → False。"""
    controller, fake_graph, _ = patched_controller
    thread_id = "task-empty-tasks"
    fake_graph.set_snapshot(
        thread_id,
        _FakeSnapshot(values={}, next_=("planning",), tasks=()),
    )
    assert controller.is_interrupted(thread_id) is False


def test_bnd_is_interrupted_task_with_empty_interrupts_returns_false(patched_controller):
    """next 非空、tasks 非空但每个 task 的 interrupts 为空：→ False（与 BND-3 不同路径）。"""
    controller, fake_graph, _ = patched_controller
    thread_id = "task-empty-interrupts"
    fake_graph.set_snapshot(
        thread_id,
        _FakeSnapshot(
            values={},
            next_=("planning",),
            tasks=(_FakeTask(name="planning", interrupts=()),),
        ),
    )
    assert controller.is_interrupted(thread_id) is False


def test_bnd_is_interrupted_no_snapshot_returns_false(patched_controller):
    """无 snapshot（get_state 返回 None）：is_interrupted 短路返回 False，不抛异常。"""
    controller, fake_graph, _ = patched_controller
    assert controller.is_interrupted("task-never-started") is False


def test_bnd_is_interrupted_multiple_tasks_one_with_interrupt(patched_controller):
    """多 task 中只要任一含 interrupt 元数据即 True（_has_interrupt 任一命中）。"""
    controller, fake_graph, _ = patched_controller
    thread_id = "task-multi"
    fake_graph.set_snapshot(
        thread_id,
        _FakeSnapshot(
            values={},
            next_=("planning",),
            tasks=(
                _FakeTask(name="other", interrupts=()),
                _FakeTask(name="planning", interrupts=(_FakeInterrupt(),)),
            ),
        ),
    )
    assert controller.is_interrupted(thread_id) is True


# ---- BND-4：_make_config 在 get_state 与 invoke 两路径都注入 checkpoint_ns ----


def test_bnd_make_config_injected_on_get_state_path(patched_controller):
    """poll_state / is_interrupted 走 get_state，config 必须含 checkpoint_ns=""。"""
    controller, fake_graph, _ = patched_controller
    seen_configs: List[Dict] = []
    orig_get_state = fake_graph.get_state

    def _spy_get_state(config):
        seen_configs.append(config)
        return orig_get_state(config)

    fake_graph.get_state = _spy_get_state

    controller.poll_state("task-cfg")
    controller.is_interrupted("task-cfg")

    assert len(seen_configs) == 2
    for cfg in seen_configs:
        assert cfg["configurable"]["thread_id"] == "task-cfg"
        assert cfg["configurable"]["checkpoint_ns"] == ""


def test_bnd_make_config_injected_on_invoke_paths(patched_controller):
    """start_task 与 resume_with 两条 invoke 路径的 config 都含 checkpoint_ns=""。"""
    controller, fake_graph, _ = patched_controller

    tid_start = controller.start_task("2405.14831", _make_llm_config_set())
    controller._workers[tid_start].join(timeout=5.0)

    controller.resume_with("task-resume-cfg", {"decision": "approve"})
    controller._workers["task-resume-cfg"].join(timeout=5.0)

    assert len(fake_graph.invoke_calls) == 2
    for _, cfg in fake_graph.invoke_calls:
        assert cfg["configurable"]["checkpoint_ns"] == ""
        assert "thread_id" in cfg["configurable"]


# ---- BND-5：_refresh_llm_config_set 三态（全空 / 部分空 / 全填）----


def _bare_cfg(key: str = "sk") -> Dict:
    return {
        "base_url": "u", "model": "m", "api_key": key,
        "temperature": 0.3, "max_tokens": 4096,
    }


def test_bnd_refresh_overrides_all_empty():
    """全空 overrides：refreshed.overrides 为空 dict，仅 default 保留。"""
    config_set = {"default": _bare_cfg("sk-d"), "overrides": {}}
    refreshed = _refresh_llm_config_set(config_set)
    assert refreshed["overrides"] == {}
    assert refreshed["default"]["api_key"] == "sk-d"


def test_bnd_refresh_overrides_partial():
    """部分空 overrides：只保留非空节点（planning 空被清理，paper_intake 保留）。"""
    config_set = {
        "default": _bare_cfg("sk-d"),
        "overrides": {
            "paper_intake": _bare_cfg("sk-i"),
            "paper_analysis": {},  # 空 → 清理
            "resource_scout": {},  # 空 → 清理
            "planning": {},        # 空 → 清理
        },
    }
    refreshed = _refresh_llm_config_set(config_set)
    assert set(refreshed["overrides"].keys()) == {"paper_intake"}
    assert refreshed["overrides"]["paper_intake"]["api_key"] == "sk-i"


def test_bnd_refresh_overrides_all_filled():
    """全填 4 个 override：全部保留且各自独立 dict（非引用复用）。"""
    config_set = {
        "default": _bare_cfg("sk-d"),
        "overrides": {n: _bare_cfg(f"sk-{n}") for n in _OVERRIDE_NODES_FROM_APP},
    }
    refreshed = _refresh_llm_config_set(config_set)
    assert set(refreshed["overrides"].keys()) == set(_OVERRIDE_NODES_FROM_APP)
    for n in _OVERRIDE_NODES_FROM_APP:
        assert refreshed["overrides"][n]["api_key"] == f"sk-{n}"
        # 新 dict，不复用原引用。
        assert refreshed["overrides"][n] is not config_set["overrides"][n]


def test_bnd_refresh_drops_illegal_node_name(caplog):
    """非法 override 节点名被忽略并打 WARNING（controller 不信任入参）。"""
    config_set = {
        "default": _bare_cfg("sk-d"),
        "overrides": {
            "paper_intake": _bare_cfg("sk-i"),
            "execution": _bare_cfg("sk-bad"),  # 非法节点名
        },
    }
    with caplog.at_level(logging.WARNING, logger="app"):
        refreshed = _refresh_llm_config_set(config_set)
    assert set(refreshed["overrides"].keys()) == {"paper_intake"}
    warns = [r for r in caplog.records if "非法 override 节点名" in r.getMessage()]
    assert len(warns) == 1


def test_bnd_refresh_handles_missing_overrides_key():
    """overrides 键缺失（None）时安全降级为空 dict，不抛 KeyError。"""
    config_set = {"default": _bare_cfg("sk-d")}
    refreshed = _refresh_llm_config_set(config_set)
    assert refreshed["overrides"] == {}


# ---- BND-6：poll_state 对不存在 thread_id 返回 None ----


def test_bnd_poll_state_unknown_thread_returns_none(patched_controller):
    """从未启动的 thread_id：get_state 返回 None → poll_state 返回 None。"""
    controller, fake_graph, _ = patched_controller
    assert controller.poll_state("task-ghost") is None


# ---- BND-7：并发 N=4 thread_id 独立 SqliteSaver 隔离（CP-D2-9 扩展到 N>2）----


def test_bnd_four_threads_independent_savers_real_sqlite(monkeypatch, tmp_path):
    """并发 4 个 thread_id：真实 SqliteSaver + 真实最小 graph，各自 checkpoint 独立回读。

    CP-D2-9 验 N=2，此处扩展到 N=4 进一步压实"每线程独立 Python 实例 + 共享文件"隔离。
    """
    db_path = str(tmp_path / "bnd_4threads.sqlite")

    from core.checkpointer import get_checkpointer as real_get_checkpointer
    from langgraph.graph import StateGraph, START, END
    from typing import TypedDict

    class MiniState(TypedDict, total=False):
        user_input: str
        current_step: str
        llm_config_set: dict

    def _node(state):
        return {"current_step": f"done:{state.get('user_input')}"}

    def _real_build_graph(checkpointer=None):
        g = StateGraph(MiniState)
        g.add_node("only", _node)
        g.add_edge(START, "only")
        g.add_edge("only", END)
        return g.compile(checkpointer=checkpointer)

    created: List[object] = []

    def _get_cp(db=None):
        cp = real_get_checkpointer(db_path)
        created.append(cp)
        return cp

    monkeypatch.setattr(app_module, "get_checkpointer", _get_cp)
    monkeypatch.setattr(app_module, "build_graph", _real_build_graph)

    controller = GraphController()

    papers = ["paperA", "paperB", "paperC", "paperD"]
    tids = [controller.start_task(p, _make_llm_config_set()) for p in papers]
    for tid in tids:
        controller._workers[tid].join(timeout=10.0)

    for tid in tids:
        assert controller.get_worker_error(tid) is None

    # 每条 thread_id 回读出自己的 user_input，互不串扰。
    for tid, paper in zip(tids, papers):
        state = controller.poll_state(tid)
        assert state is not None
        assert state["current_step"] == f"done:{paper}"

    # main + 4 worker ≥ 5 个独立实例，id 全唯一（不共享 Python 实例）。
    assert len(created) >= 5
    assert len(set(id(c) for c in created)) == len(created)
    # thread_id 全唯一（uuid 生成无碰撞）。
    assert len(set(tids)) == 4


# ---- BND-8：_get_controller 单例不被 rerun 重建 ----


def test_bnd_get_controller_singleton_not_rebuilt(monkeypatch):
    """_get_controller 多次调用（模拟 rerun）返回同一 GraphController 实例。

    用一个轻量 FakeSessionState 模拟 st.session_state（支持 in / 读 / 写），patch
    streamlit.session_state，断言第二次调用不重建（避免 §2.7 风险标注的 rerun 重建）。
    """
    fake_graph = FakeGraph()
    monkeypatch.setattr(app_module, "get_checkpointer", lambda db_path=None: object())
    monkeypatch.setattr(app_module, "build_graph", lambda checkpointer=None: fake_graph)

    class _FakeSessionState(dict):
        pass

    import streamlit as st

    fake_ss = _FakeSessionState()
    monkeypatch.setattr(st, "session_state", fake_ss)

    c1 = app_module._get_controller()
    c2 = app_module._get_controller()
    assert c1 is c2
    assert fake_ss["graph_controller"] is c1
    # 单例只构造一次：第二次不应再覆盖 session_state 中的实例。
    assert isinstance(c1, GraphController)
