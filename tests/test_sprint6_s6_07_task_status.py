"""Sprint 6 批次 4 · T-S6-4-3：任务状态推导 + 枚举 + 显式续跑（S6-07，架构 §4）。

覆盖 dev-plan §4 批次 4 检查点：
    - CP-4.3-1 derive_task_status R1~R7 全行（mock snapshot + 登记表构造每行条件）；
    - CP-4.3-2 list_threads 只读枚举（mode=ro / GROUP BY 排序新任务在前 / 论文标识三级回退 /
      原库 md5 前后一致）；
    - CP-4.3-3 20-thread 真库枚举（checkpoints_s6_full20.db 驱动 + 坏 thread 跳过不炸整页 R-S6-A3）；
    - CP-4.3-4 resume_task 显式续跑（invoke(None) + 原子 check-and-set 拒绝存活 worker）。

测试策略：derive_task_status 纯函数直测（自包含 FakeSnapshot）；list_threads 用固化真库
fixture（monkeypatch app.CHECKPOINT_DB_PATH + 真实 controller 指向同库）；resume_task 用
真实存活线程验第三道防线。
"""

from __future__ import annotations

import hashlib
import threading
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import pytest

import app as app_module
from app import (
    GraphController,
    TASK_STATUS_AWAITING,
    TASK_STATUS_CANCELLED,
    TASK_STATUS_DONE,
    TASK_STATUS_FAILED,
    TASK_STATUS_INTERRUPTED,
    TASK_STATUS_NO_REPORT,
    TASK_STATUS_RUNNING,
    derive_task_status,
)

_FIXTURE_20 = Path("tests/fixtures/checkpoints_s6_full20.db")


# ----------------------------------------------------------------------
# 自包含夹具（derive_task_status 纯函数）
# ----------------------------------------------------------------------
class _FakeInterrupt:
    def __init__(self, value: Any = None) -> None:
        self.value = value
        self.id = "int-1"


class _FakeTask:
    def __init__(self, interrupts: Tuple = ()) -> None:
        self.interrupts = interrupts


class _FakeSnapshot:
    def __init__(self, values: Dict, next_: Tuple = (), tasks: Tuple = ()) -> None:
        self.values = values
        self.next = next_
        self.tasks = tasks


def _snap(values: Dict, next_=(), interrupt=False) -> _FakeSnapshot:
    tasks = (_FakeTask(interrupts=(_FakeInterrupt({"q": "x"}),)),) if interrupt else ()
    return _FakeSnapshot(values=values, next_=next_, tasks=tasks)


@pytest.fixture(autouse=True)
def _reset_registry():
    app_module._reset_for_tests()
    yield
    app_module._reset_for_tests()


# ======================================================================
# CP-4.3-1 derive_task_status R1~R7 全行
# ======================================================================
def test_cp_4_3_1_r1_none_when_no_snapshot_or_empty_values():
    assert derive_task_status(None, False) is None
    assert derive_task_status(_snap({}), False) is None  # values 空


def test_cp_4_3_1_r2_failed_when_error():
    assert derive_task_status(_snap({"error": "boom"}, next_=("execution",)), True) == TASK_STATUS_FAILED
    # R2 优先于 R3（error ∧ cancelled → 仍失败）
    assert derive_task_status(_snap({"error": "boom", "current_step": "cancelled_by_user"}), False) == TASK_STATUS_FAILED


def test_cp_4_3_1_r3_cancelled():
    assert derive_task_status(_snap({"current_step": "cancelled_by_user"}), False) == TASK_STATUS_CANCELLED


def test_cp_4_3_1_r4a_done_and_r4b_no_report():
    # next 空 + report_path 非空 → done
    assert derive_task_status(_snap({"current_step": "reporting", "report_path": "/x/r.md"}, next_=()), False) == TASK_STATUS_DONE
    # next 空 + report_path 空 → no_report
    assert derive_task_status(_snap({"current_step": "reporting"}, next_=()), False) == TASK_STATUS_NO_REPORT


def test_e2e2_r5_wins_over_r4_when_next_empty():
    """[BUG-E2E2-03] next 为空元组 ∧ 挂起 interrupt（同一次节点执行内的第 2 次
    interrupt）→ awaiting，而非 R4b 的 no_report「失败·未产报告」。

    现场：coding 凭证 gate 串行索要第 2 项时 LangGraph 把 __resume__ 计入 task.writes
    → next 被清空（main.py:1118-1138），旧顺序 R4 先判会把「等待输入」误报成失败。
    """
    assert derive_task_status(_snap({"current_step": "coding"}, next_=(), interrupt=True), False) == TASK_STATUS_AWAITING


def test_e2e2_r5_wins_over_r4a_done_when_next_empty():
    """[BUG-E2E2-03] 同形态 + report_path 非空 → 仍 awaiting（挂起 interrupt 压过 done）。"""
    assert derive_task_status(
        _snap({"current_step": "reporting", "report_path": "/x/r.md"}, next_=(), interrupt=True), False
    ) == TASK_STATUS_AWAITING


def test_cp_4_3_1_r5_awaiting_when_interrupt():
    # next 非空 + interrupt → awaiting（无论 worker 存活与否，R5 优先于 R6/R7）
    assert derive_task_status(_snap({"current_step": "coding"}, next_=("coding",), interrupt=True), True) == TASK_STATUS_AWAITING
    assert derive_task_status(_snap({"current_step": "coding"}, next_=("coding",), interrupt=True), False) == TASK_STATUS_AWAITING


def test_cp_4_3_1_r6_running_with_worker():
    assert derive_task_status(_snap({"current_step": "execution"}, next_=("execution",), interrupt=False), True) == TASK_STATUS_RUNNING


def test_cp_4_3_1_r7_interrupted_no_worker():
    """R7 区分锚 = 无存活 worker（进程重启后登记表必空 → next 非空即孤儿）。"""
    assert derive_task_status(_snap({"current_step": "execution"}, next_=("execution",), interrupt=False), False) == TASK_STATUS_INTERRUPTED


def test_cp_4_3_1_priority_order_short_circuit():
    """R2>R3>R5>R4>R6>R7 自上而下短路：多条件同时命中取最高优先
    （[BUG-E2E2-03] R5 已提到 R4 之前；R2/R3 仍压过 R5，产品红线）。"""
    # error + next 非空 + interrupt → 仍 R2 失败（error 最高）
    assert derive_task_status(_snap({"error": "e"}, next_=("coding",), interrupt=True), True) == TASK_STATUS_FAILED


# ======================================================================
# CP-4.3-2/3 list_threads 真库枚举
# ======================================================================
def _controller_on_fixture(monkeypatch, db_path: str) -> GraphController:
    from core.checkpointer import get_checkpointer
    from core.graph import build_graph
    monkeypatch.setattr(app_module, "CHECKPOINT_DB_PATH", db_path)
    c = GraphController.__new__(GraphController)
    c._lock = threading.Lock()
    c._workers = {}
    c._worker_errors = {}
    c._main_checkpointer = get_checkpointer(db_path)
    c._main_graph = build_graph(checkpointer=c._main_checkpointer)
    return c


def test_cp_4_3_3_list_threads_20_real_db(monkeypatch):
    assert _FIXTURE_20.exists(), "20-thread fixture 缺失"
    c = _controller_on_fixture(monkeypatch, str(_FIXTURE_20))
    threads = c.list_threads()
    # 20 thread 全部可推导（无 R1 空快照）
    assert len(threads) == 20
    # 每条含四字段
    for t in threads:
        assert set(t.keys()) == {"thread_id", "status", "status_label", "paper_label"}
        assert t["status"] in {
            TASK_STATUS_FAILED, TASK_STATUS_CANCELLED, TASK_STATUS_DONE,
            TASK_STATUS_NO_REPORT, TASK_STATUS_AWAITING, TASK_STATUS_RUNNING,
            TASK_STATUS_INTERRUPTED,
        }
    # 状态矩阵覆盖多类（真库含 awaiting / interrupted / done / failed / cancelled / no_report）
    statuses = {t["status"] for t in threads}
    assert TASK_STATUS_AWAITING in statuses
    assert TASK_STATUS_INTERRUPTED in statuses, "R7 孤儿（无 worker 的在途）应出现"
    assert TASK_STATUS_DONE in statuses


def test_cp_4_3_2_list_threads_ordering_new_first(monkeypatch):
    c = _controller_on_fixture(monkeypatch, str(_FIXTURE_20))
    threads = c.list_threads()
    # GROUP BY MAX(checkpoint_id) DESC → 新任务在前；现场 fixture task-cdcd432cda49 checkpoint 最新
    assert threads[0]["thread_id"] == "task-cdcd432cda49"
    # 论文标识三级回退：现场 thread 有 paper_meta.title_zh
    assert threads[0]["paper_label"], "论文标识非空"
    assert "HippoRAG" in threads[0]["paper_label"]


def test_cp_4_3_2_list_threads_readonly_md5_unchanged(monkeypatch):
    """只读枚举不写业务数据：主库文件 md5 前后一致（mode=ro）。"""
    c = _controller_on_fixture(monkeypatch, str(_FIXTURE_20))
    before = hashlib.md5(_FIXTURE_20.read_bytes()).hexdigest()
    c.list_threads()
    after = hashlib.md5(_FIXTURE_20.read_bytes()).hexdigest()
    assert before == after, "list_threads 不得修改主库业务数据"


def test_cp_4_3_2_list_threads_missing_db_returns_empty(monkeypatch, tmp_path):
    """库不存在 / 打不开 → 返回空列表不炸（防御）。"""
    missing = str(tmp_path / "nonexistent.db")
    c = _controller_on_fixture(monkeypatch, str(_FIXTURE_20))
    monkeypatch.setattr(app_module, "CHECKPOINT_DB_PATH", missing)
    assert c.list_threads() == []


# ======================================================================
# CP-4.3-4 resume_task 显式续跑 + 原子 check-and-set
# ======================================================================
def _bare_controller() -> GraphController:
    c = GraphController.__new__(GraphController)
    c._lock = threading.Lock()
    c._workers = {}
    c._worker_errors = {}
    c._activity_handlers = {}
    return c


def test_cp_4_3_4_resume_task_invokes_none_and_registers(monkeypatch):
    """resume_task 起 worker 执行 invoke(None)（从断点重启在途节点）+ 登记进程表。"""
    c = _bare_controller()
    entered, release = threading.Event(), threading.Event()
    calls = []

    def fake_run(thread_id):
        calls.append(thread_id)
        entered.set()
        release.wait(timeout=5.0)

    c._resume_task_run = fake_run
    try:
        ok = c.resume_task("task-orphan")
        assert ok is True
        assert entered.wait(timeout=5.0)
        assert calls == ["task-orphan"]
        assert c.has_active_worker("task-orphan"), "续跑期间登记表命中"
    finally:
        release.set()


def test_cp_4_3_4_resume_task_rejects_when_alive_worker(caplog):
    """第三道防线：已有存活 worker → 原子拒绝（防重复续跑 / TOCTOU）。"""
    c = _bare_controller()
    release = threading.Event()
    alive = threading.Thread(target=lambda: release.wait(timeout=5.0), daemon=True)
    alive.start()
    app_module._register_worker("task-orphan", alive)
    try:
        spy = []
        c._resume_task_run = lambda tid: spy.append(tid)
        ok = c.resume_task("task-orphan")
        assert ok is False, "已有存活 worker → 拒绝续跑"
        assert spy == [], "被拒时不起新 worker"
    finally:
        release.set()
        alive.join(timeout=5.0)


def test_cp_4_3_4_resume_task_real_invoke_none_call(monkeypatch):
    """resume_task 真实调用 graph.invoke(None, config)（mock build_graph 捕获实参）。"""
    c = _bare_controller()
    captured = {}
    done = threading.Event()

    class _FakeGraph:
        def invoke(self, arg, config):
            captured["arg"] = arg
            captured["config"] = config
            done.set()

    monkeypatch.setattr(app_module, "get_checkpointer", lambda db_path=None: object())
    monkeypatch.setattr(app_module, "build_graph", lambda checkpointer=None: _FakeGraph())
    # _get_activity_handler 需要真实实例
    from core.activity_stream import ActivityStreamHandler
    monkeypatch.setattr(c, "_get_activity_handler", lambda tid: ActivityStreamHandler())

    ok = c.resume_task("task-orphan")
    assert ok is True
    assert done.wait(timeout=5.0)
    assert captured["arg"] is None, "invoke 第一参必须是 None（从断点重启，非新 payload）"
    assert captured["config"]["configurable"]["thread_id"] == "task-orphan"
