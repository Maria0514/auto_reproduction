"""Sprint 6 批次 3 · T-S6-3-1/3-2：换代判定原语 + 阶段推导（S6-01/S6-02 controller 面）。

覆盖 dev-plan §4 批次 3 检查点：
    - CP-3.1-1 get_interrupt_token 复合三元（id:指纹）四场景（换代判定基石）；
    - CP-3.1-2 指纹只存哈希（不含 question 原文）+ interrupt.id 缺失退化纯指纹（R-S6-A1）；
    - CP-3.1-3 resume_with token 校验参（不一致→False+WARNING 不抛；缺省 None 不校验，向后兼容）；
    - CP-3.1-4 第三道防线：同 thread 已有存活 worker → resume_with 原子拒绝；has_active_worker 真值；
    - CP-3.1-5 _reset_for_tests 清空登记表（R-S6-A4 用例间无泄漏）；
    - CP-3.2-1/2 get_phase 阶段推导（active_node = snapshot.next[0] | None）+ 现场取证形态。

测试策略：纯确定性单测——自包含 FakeGraph 注入 controller._main_graph，第三道防线用**真实
存活线程**（阻塞在 Event）验 has_active_worker，避免时序 flaky。
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from typing import Any, Dict, List, Optional, Tuple

import pytest

import app as app_module
from app import GraphController


# ----------------------------------------------------------------------
# 自包含夹具
# ----------------------------------------------------------------------


class _FakeInterrupt:
    """可控 interrupt：value + 可选 id（id=None 或不设属性 → 退化纯指纹）。"""

    def __init__(self, value: Any, id_: Optional[str] = "int-1", *, has_id: bool = True) -> None:
        self.value = value
        if has_id:
            self.id = id_


class _FakeTask:
    def __init__(self, interrupts: Tuple = ()) -> None:
        self.interrupts = interrupts


class _FakeSnapshot:
    def __init__(self, values: Dict, next_: Tuple, tasks: Tuple) -> None:
        self.values = values
        self.next = next_
        self.tasks = tasks


class _FakeGraph:
    def __init__(self) -> None:
        self._snapshots: Dict[str, Optional[_FakeSnapshot]] = {}

    def set_snapshot(self, thread_id: str, snapshot: Optional[_FakeSnapshot]) -> None:
        self._snapshots[thread_id] = snapshot

    def get_state(self, config: Dict) -> Optional[_FakeSnapshot]:
        return self._snapshots.get(config["configurable"]["thread_id"])


@pytest.fixture(autouse=True)
def _reset_registry():
    """每个用例前后清空进程级登记表（R-S6-A4 隔离）。"""
    app_module._reset_for_tests()
    yield
    app_module._reset_for_tests()


@pytest.fixture
def controller() -> Tuple[GraphController, _FakeGraph]:
    """构造一个只装了 _main_graph（FakeGraph）+ _lock/_workers 的轻量 controller。

    绕开 __init__ 的 checkpointer/build_graph 重 IO（get_interrupt_token/get_phase/
    resume_with 只依赖 _main_graph + 模块级登记表 + _lock/_workers）。
    """
    ctrl = GraphController.__new__(GraphController)
    fake = _FakeGraph()
    ctrl._main_graph = fake
    ctrl._lock = threading.Lock()
    ctrl._workers = {}
    ctrl._worker_errors = {}
    return ctrl, fake


def _interrupt_snapshot(value: Any, *, next_=("execution",), id_="int-1", has_id=True) -> _FakeSnapshot:
    return _FakeSnapshot(
        values={"current_step": "execution"},
        next_=next_,
        tasks=(_FakeTask(interrupts=(_FakeInterrupt(value, id_=id_, has_id=has_id),)),),
    )


def _spawn_alive_thread(release: threading.Event) -> threading.Thread:
    """起一个真实存活线程（阻塞在 release.wait），用于第三道防线断言。"""
    t = threading.Thread(target=lambda: release.wait(timeout=5.0), daemon=True)
    t.start()
    return t


# ======================================================================
# CP-3.1-1 get_interrupt_token 复合三元四场景
# ======================================================================


def test_cp_3_1_1_token_id_and_fingerprint(controller):
    ctrl, fake = controller
    tid = "task-abc"
    fake.set_snapshot(tid, _interrupt_snapshot({"question": "缺 OPENAI_API_KEY"}, id_="int-42"))
    token = ctrl.get_interrupt_token(tid)
    assert token is not None
    assert ":" in token, "复合 token 应为 id:指纹"
    id_part, fp_part = token.split(":", 1)
    assert id_part == "int-42"
    assert len(fp_part) == 16, "指纹应为 16 字符"
    assert all(c in "0123456789abcdef" for c in fp_part), "指纹应为 sha1 十六进制"


def test_cp_3_1_1_token_none_when_no_interrupt(controller):
    ctrl, fake = controller
    tid = "task-abc"
    # tasks 无 interrupt → token None（[BUG-E2E2-03] 判定锚是 tasks，不是 next）
    fake.set_snapshot(tid, _FakeSnapshot(values={}, next_=(), tasks=()))
    assert ctrl.get_interrupt_token(tid) is None
    # 无快照
    fake.set_snapshot("task-none", None)
    assert ctrl.get_interrupt_token("task-none") is None
    # next 非空但 task 无 interrupt
    fake.set_snapshot("task-run", _FakeSnapshot(values={}, next_=("coding",), tasks=(_FakeTask(),)))
    assert ctrl.get_interrupt_token("task-run") is None


def test_e2e2_token_available_when_next_empty_with_interrupt(controller):
    """[BUG-E2E2-03] next 为空元组但 tasks 挂 interrupt（同一次节点执行内的第 2 次
    interrupt）→ token 必须照常返回 ``{id}:{指纹}``，不得退化为 None。

    token=None 会让 resume_with 的 token 校验（app.py:327-335）把用户的正常提交
    误判为「迟到提交」并拒绝，用户永远提交不上去。
    """
    ctrl, fake = controller
    tid = "task-e2e2"
    fake.set_snapshot(tid, _interrupt_snapshot({"question": "缺 GOOGLE_API_KEY"}, next_=(), id_="int-77"))
    token = ctrl.get_interrupt_token(tid)
    assert token is not None, "next 为空元组不代表没有挂起 interrupt"
    id_part, fp_part = token.split(":", 1)
    assert id_part == "int-77"
    assert len(fp_part) == 16


def test_cp_3_1_1_payload_change_changes_token(controller):
    ctrl, fake = controller
    tid = "task-abc"
    fake.set_snapshot(tid, _interrupt_snapshot({"question": "问题 A"}, id_="int-1"))
    token_a = ctrl.get_interrupt_token(tid)
    fake.set_snapshot(tid, _interrupt_snapshot({"question": "问题 B"}, id_="int-1"))
    token_b = ctrl.get_interrupt_token(tid)
    assert token_a != token_b, "payload 变化 → token 必变（换代判定基石）"


def test_cp_3_1_1_same_payload_same_token(controller):
    ctrl, fake = controller
    tid = "task-abc"
    payload = {"question": "同一问题", "options": ["a", "b"]}
    fake.set_snapshot(tid, _interrupt_snapshot(dict(payload), id_="int-1"))
    token_1 = ctrl.get_interrupt_token(tid)
    # 重新构造等价 payload（键序不同）→ 指纹应稳定（sort_keys）
    fake.set_snapshot(tid, _interrupt_snapshot({"options": ["a", "b"], "question": "同一问题"}, id_="int-1"))
    token_2 = ctrl.get_interrupt_token(tid)
    assert token_1 == token_2, "相同 payload（键序无关）→ token 相同"


# ======================================================================
# CP-3.1-2 指纹只存哈希 + interrupt.id 缺失退化纯指纹（R-S6-A1）
# ======================================================================


def test_cp_3_1_2_token_excludes_question_raw_text(controller):
    ctrl, fake = controller
    tid = "task-abc"
    secret = "非常敏感的凭证问题原文XYZ"
    fake.set_snapshot(tid, _interrupt_snapshot({"question": secret}, id_="int-1"))
    token = ctrl.get_interrupt_token(tid)
    assert secret not in token, "token 不得含 question 原文（安全纪律）"


def test_cp_3_1_2_degrade_to_pure_fingerprint_when_id_missing(controller):
    ctrl, fake = controller
    tid = "task-abc"
    value = {"question": "无 id 的 interrupt"}
    # interrupt 无 id 属性 → 退化纯指纹（无冒号前缀）
    fake.set_snapshot(tid, _interrupt_snapshot(value, has_id=True, id_=None))
    token_id_none = ctrl.get_interrupt_token(tid)
    fake.set_snapshot(tid, _interrupt_snapshot(value, has_id=False))
    token_no_attr = ctrl.get_interrupt_token(tid)
    # 两种"取不到 id"路径都退化为纯 16 字符指纹
    expected_fp = hashlib.sha1(
        json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    ).hexdigest()[:16]
    assert token_id_none == expected_fp
    assert token_no_attr == expected_fp
    assert ":" not in token_no_attr, "退化纯指纹无 id:前缀"


# ======================================================================
# CP-3.1-3 resume_with token 校验参（第二道防线）
# ======================================================================


def test_cp_3_1_3_resume_with_token_mismatch_rejects(controller, caplog):
    ctrl, fake = controller
    tid = "task-abc"
    fake.set_snapshot(tid, _interrupt_snapshot({"question": "当前问题"}, id_="int-1"))
    spy: List = []
    ctrl._resume_run = lambda t, p: spy.append((t, p))  # 不应被调
    with caplog.at_level(logging.WARNING):
        ok = ctrl.resume_with(tid, {"decision": "approve"}, expected_interrupt_token="stale:0000000000000000")
    assert ok is False, "token 不一致 → 拒绝迟到提交"
    assert spy == [], "被拒时不得起 worker"
    assert not ctrl.has_active_worker(tid)
    assert any("token 不一致" in r.message for r in caplog.records)


def test_cp_3_1_3_resume_with_token_match_proceeds(controller):
    ctrl, fake = controller
    tid = "task-abc"
    fake.set_snapshot(tid, _interrupt_snapshot({"question": "当前问题"}, id_="int-1"))
    current = ctrl.get_interrupt_token(tid)
    entered, release = threading.Event(), threading.Event()
    calls: List = []

    def fake_resume_run(thread_id, payload):
        calls.append((thread_id, payload))
        entered.set()
        release.wait(timeout=5.0)

    ctrl._resume_run = fake_resume_run
    try:
        ok = ctrl.resume_with(tid, {"decision": "approve"}, expected_interrupt_token=current)
        assert ok is True
        assert entered.wait(timeout=5.0)
        assert calls == [(tid, {"decision": "approve"})]
        assert ctrl.has_active_worker(tid), "worker 存活期间登记表命中"
    finally:
        release.set()


def test_cp_3_1_3_resume_with_default_none_skips_check(controller):
    """缺省 expected_interrupt_token=None → 不校验（plan_review 等既有调用零改动）。"""
    ctrl, fake = controller
    tid = "task-abc"
    # 故意让 snapshot 无 interrupt（token=None）——缺省不校验路径不应因此被拦
    fake.set_snapshot(tid, _FakeSnapshot(values={}, next_=(), tasks=()))
    entered, release = threading.Event(), threading.Event()
    ctrl._resume_run = lambda t, p: (entered.set(), release.wait(timeout=5.0))
    try:
        ok = ctrl.resume_with(tid, {"decision": "revise"})
        assert ok is True, "缺省 None → 不校验 token，直接发起（向后兼容）"
        assert entered.wait(timeout=5.0)
    finally:
        release.set()


# ======================================================================
# CP-3.1-4 第三道防线：存活 worker → 原子拒绝
# ======================================================================


def test_cp_3_1_4_reject_when_alive_worker_exists(controller, caplog):
    ctrl, fake = controller
    tid = "task-abc"
    release = threading.Event()
    alive = _spawn_alive_thread(release)
    app_module._register_worker(tid, alive)
    try:
        assert ctrl.has_active_worker(tid) is True, "登记表存活线程 → has_active_worker True"
        spy: List = []
        ctrl._resume_run = lambda t, p: spy.append((t, p))
        with caplog.at_level(logging.WARNING):
            ok = ctrl.resume_with(tid, {"decision": "approve"})
        assert ok is False, "已有存活 worker → 原子拒绝（防跨 tab 重复 resume）"
        assert spy == [], "被拒时不得起新 worker"
        assert any("已有存活 worker" in r.message for r in caplog.records)
    finally:
        release.set()
        alive.join(timeout=5.0)


def test_cp_3_1_4_has_active_worker_false_after_thread_dies(controller):
    ctrl, fake = controller
    tid = "task-abc"
    release = threading.Event()
    alive = _spawn_alive_thread(release)
    app_module._register_worker(tid, alive)
    assert ctrl.has_active_worker(tid) is True
    release.set()
    alive.join(timeout=5.0)
    assert ctrl.has_active_worker(tid) is False, "线程死亡后 has_active_worker 反映为 False"


# ======================================================================
# CP-3.1-5 _reset_for_tests 清空登记表
# ======================================================================


def test_cp_3_1_5_reset_clears_registry(controller):
    ctrl, fake = controller
    release = threading.Event()
    alive = _spawn_alive_thread(release)
    app_module._register_worker("task-x", alive)
    assert ctrl.has_active_worker("task-x") is True
    app_module._reset_for_tests()
    assert ctrl.has_active_worker("task-x") is False, "reset 后登记表清空"
    assert app_module._THREAD_WORKERS == {}
    release.set()
    alive.join(timeout=5.0)


# ======================================================================
# CP-3.2-1/2 get_phase 阶段推导
# ======================================================================


def test_cp_3_2_1_get_phase_active_node_from_next(controller):
    ctrl, fake = controller
    tid = "task-abc"
    fake.set_snapshot(tid, _FakeSnapshot(values={"current_step": "planning"}, next_=("execution",), tasks=()))
    phase = ctrl.get_phase(tid)
    assert phase == {"active_node": "execution", "current_step": "planning"}


def test_cp_3_2_1_get_phase_none_when_next_empty(controller):
    ctrl, fake = controller
    tid = "task-abc"
    fake.set_snapshot(tid, _FakeSnapshot(values={"current_step": "done"}, next_=(), tasks=()))
    phase = ctrl.get_phase(tid)
    assert phase["active_node"] is None
    assert phase["current_step"] == "done"


def test_cp_3_2_1_get_phase_empty_snapshot_safe_default(controller):
    ctrl, fake = controller
    # 无快照
    fake.set_snapshot("task-none", None)
    assert ctrl.get_phase("task-none") == {"active_node": None, "current_step": None}
    # 空 values（从未启动的 thread）
    fake.set_snapshot("task-empty", _FakeSnapshot(values={}, next_=(), tasks=()))
    assert ctrl.get_phase("task-empty") == {"active_node": None, "current_step": None}


def test_cp_3_2_2_get_phase_matches_forensic_shape(controller):
    """CP-3.2-2：现场取证形态（task-cdcd432cda49：next=('execution',)）→ active_node='execution'。"""
    ctrl, fake = controller
    tid = "task-cdcd432cda49"
    # 复现取证快照形态（架构 §6.1：两现场 snapshot.next=('execution',)）
    fake.set_snapshot(tid, _FakeSnapshot(values={"current_step": "execution"}, next_=("execution",), tasks=()))
    assert ctrl.get_phase(tid)["active_node"] == "execution"
