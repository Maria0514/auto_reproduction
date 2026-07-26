"""[BUG-E2E2-03] 动态 interrupt 判定死角：``next=() ∧ tasks 挂 interrupt`` 判定层守门。

现场（E2E-2 复验，thread ``task-435baf71f4cf``）：coding 前置凭证 gate 在**同一次节点执行内**
串行索要第 2 项凭证时，LangGraph 把 ``__resume__`` 计入 task.writes
（``langgraph/pregel/main.py:1118-1138``）→ ``get_state().next`` 变成空元组，而中断信息仍
完整挂在 ``tasks[*].interrupts`` 上。``app.py`` 曾有四处判定拿 ``snapshot.next`` 当
interrupt/结束的前置门槛，于是凭证输入面板不弹、任务被误判 ``no_report``，功能性死锁。

本文件是**判定层收口门**（L1）：用与既有替身同构的 FakeSnapshot 直接构造该 BUG 形态，
驱动**真实 GraphController**（绕过 ``__init__``，``_main_graph`` 换 MagicMock）逐个方法钉死。

为什么必须新建而不是靠既有用例：架构评估 §4.2 已逐条确认——全仓**零**用例构造过
``next=() ∧ tasks 含 interrupt`` 的快照，两个 fixture 真库也都不含该形态的 thread
（§3.4 逐 thread 确认）。「零红」不等于「有保护」。

运行::

    .venv/bin/pytest -q tests/test_e2e2_interrupt_gate_fix.py
"""

from __future__ import annotations

import threading
from typing import Any, Dict, Optional, Tuple
from unittest.mock import MagicMock

from app import (
    GraphController,
    TASK_STATUS_AWAITING,
    TASK_STATUS_DONE,
    TASK_STATUS_FAILED,
    derive_task_status,
)


# --------------------------------------------------------------------------- #
# 夹具：与 tests/test_sprint5_s5_08_routing.py:394-425 同构的替身
#（红线 §5.2-2：替身只定义 values / next / tasks 三个属性，判定不得改读
#  snapshot.interrupts 顶层字段，否则这些替身会静默返回 False 制造大面积假绿）
# --------------------------------------------------------------------------- #
class _FakeInterrupt:
    def __init__(self, value: Any = None, id_: str = "int-e2e2") -> None:
        self.value = value
        self.id = id_


class _FakeTask:
    def __init__(self, name: str = "coding", interrupts: Tuple = ()) -> None:
        self.name = name
        self.interrupts = interrupts


class _FakeSnapshot:
    def __init__(self, values: Dict, next_: Tuple, tasks: Tuple = ()) -> None:
        self.values = values
        self.next = next_
        self.tasks = tasks


def _controller_with_snapshot(snapshot: Optional[_FakeSnapshot]) -> GraphController:
    """构造真实 GraphController（绕过 __init__，不建真图/真库），main_graph 换 fake。"""
    controller = GraphController.__new__(GraphController)
    controller._lock = threading.Lock()
    controller._workers = {}
    controller._worker_errors = {}
    controller._main_checkpointer = object()
    graph = MagicMock()
    graph.get_state.return_value = snapshot
    controller._main_graph = graph
    return controller


# 现场 payload（coding.py:810 前置凭证 gate 第 2 项，allow_degrade=True 是其来源指纹）。
_BUG_PAYLOAD: Dict[str, Any] = {
    "interrupt_kind": "user_input_request",
    "question": "请提供 GOOGLE_API_KEY（复现需要真实 LLM 调用）",
    "is_sensitive": True,
    "purpose_key": "env:GOOGLE_API_KEY",
    "allow_degrade": True,
}

_TID = "task-e2e2-second-interrupt"


def _bug_snapshot() -> _FakeSnapshot:
    """BUG 形态快照：next 空元组（命门）+ tasks 挂 interrupt。"""
    return _FakeSnapshot(
        values={"current_step": "coding", "report_path": None},
        next_=(),  # ← 命门：同一次节点执行内的第 2 次 interrupt，next 被清空
        tasks=(_FakeTask(interrupts=(_FakeInterrupt(_BUG_PAYLOAD),)),),
    )


# =========================================================================== #
# L1-1 ~ L1-5：BUG 形态下五个判定必须全部正确
# =========================================================================== #
def test_e2e2_l1_1_is_interrupted_true_when_next_empty_with_interrupt():
    """L1-1：next=() ∧ 挂 interrupt → is_interrupted 必为 True（面板弹出的总闸）。"""
    c = _controller_with_snapshot(_bug_snapshot())
    assert c.is_interrupted(_TID) is True, (
        "同节点第 2 次 interrupt 暂停时 next 为空元组，判定不得以 snapshot.next 为前置门槛"
    )


def test_e2e2_l1_2_is_finished_false_when_next_empty_with_interrupt():
    """L1-2：同形态下 is_finished 必为 False（暂停等输入 ≠ 已到 END）。"""
    c = _controller_with_snapshot(_bug_snapshot())
    assert c.is_finished(_TID) is False, (
        "有挂起 interrupt 时不得判为已完成，否则与 is_interrupted 语义正交性被打破"
    )


def test_e2e2_l1_3_get_interrupt_payload_available_when_next_empty():
    """L1-3：payload 必须取得到——取不到会让 interrupt_kind 退化 None，
    UI 落到「防御性跳回计划审核页」死循环（评估 §2.1 半修好中间态）。"""
    c = _controller_with_snapshot(_bug_snapshot())
    payload = c.get_interrupt_payload(_TID)
    assert payload is not None, "payload 为 None 会把死锁换成计划审核页无限轮询"
    assert payload["purpose_key"] == "env:GOOGLE_API_KEY"
    assert payload["allow_degrade"] is True


def test_e2e2_l1_4_get_interrupt_token_available_when_next_empty():
    """L1-4：token 必须取得到且形如 ``{id}:{16位指纹}``——token=None 会让
    resume_with 第二道防线（app.py:327-335）把用户正常提交误判为「迟到提交」拒绝。"""
    c = _controller_with_snapshot(_bug_snapshot())
    token = c.get_interrupt_token(_TID)
    assert token is not None, "token 为 None 会导致用户提交被拒"
    id_part, _, fp_part = token.partition(":")
    assert id_part == "int-e2e2"
    assert len(fp_part) == 16, "指纹应为 16 字符 sha1 前缀"
    assert all(ch in "0123456789abcdef" for ch in fp_part)


def test_e2e2_l1_5_derive_task_status_awaiting_when_next_empty_with_interrupt():
    """L1-5：任务列表状态推导必须给 awaiting（R5 先于 R4），而非误报 no_report「失败·未产报告」。"""
    assert derive_task_status(_bug_snapshot(), False) == TASK_STATUS_AWAITING
    # 有无存活 worker 都一样（R5 优先于 R6/R7 的既有语义不变）。
    assert derive_task_status(_bug_snapshot(), True) == TASK_STATUS_AWAITING


# =========================================================================== #
# L1-6 / L1-7：反向安全（防"修过头"）——改动前后均应绿
# =========================================================================== #
def test_e2e2_l1_6_real_end_state_still_done_not_awaiting():
    """L1-6 反向安全：图真到 END 时 snapshot.tasks 为空元组
    （tasks_w_writes 只遍历 next_tasks，main.py:1129-1134）→ 判定与改动前完全一致。"""
    snap = _FakeSnapshot(
        values={"current_step": "reporting", "report_path": "/x/r.md"},
        next_=(),
        tasks=(),
    )
    c = _controller_with_snapshot(snap)
    assert c.is_interrupted(_TID) is False, "已完成任务绝不能显示为等待输入"
    assert c.is_finished(_TID) is True
    assert derive_task_status(snap, False) == TASK_STATUS_DONE


def test_e2e2_l1_7_error_still_wins_over_pending_interrupt():
    """L1-7 反向安全（产品红线 §5.2-5）：R2（error）仍压过 interrupt——
    已失败的任务不得因残留 interrupt 被判成「等待输入」。"""
    snap = _FakeSnapshot(
        values={"error": "boom"},
        next_=(),
        tasks=(_FakeTask(interrupts=(_FakeInterrupt(_BUG_PAYLOAD),)),),
    )
    assert derive_task_status(snap, False) == TASK_STATUS_FAILED
    assert derive_task_status(snap, True) == TASK_STATUS_FAILED


def test_e2e2_l1_7b_cancelled_still_wins_over_pending_interrupt():
    """L1-7 姊妹（产品红线 §5.2-5）：R3（cancelled_by_user）同样压过 interrupt。"""
    snap = _FakeSnapshot(
        values={"current_step": "cancelled_by_user"},
        next_=(),
        tasks=(_FakeTask(interrupts=(_FakeInterrupt(_BUG_PAYLOAD),)),),
    )
    from app import TASK_STATUS_CANCELLED

    assert derive_task_status(snap, False) == TASK_STATUS_CANCELLED


def test_e2e2_l1_defensive_empty_snapshot_unchanged():
    """防御边界不被放宽破坏：无快照 / values={} 空快照（从未启动的 thread）行为不变。"""
    assert _controller_with_snapshot(None).is_interrupted("t-unknown") is False
    assert _controller_with_snapshot(None).is_finished("t-unknown") is False
    assert _controller_with_snapshot(None).get_interrupt_payload("t-unknown") is None
    assert _controller_with_snapshot(None).get_interrupt_token("t-unknown") is None
    empty = _FakeSnapshot(values={}, next_=(), tasks=())
    assert _controller_with_snapshot(empty).is_finished("t-never") is False
    assert derive_task_status(empty, False) is None


def test_e2e2_has_interrupt_unchanged_contract():
    """红线 §5.2-2：``_has_interrupt`` 是唯一判定锚，只看 tasks[*].interrupts，
    不得改读 snapshot.interrupts 顶层字段（全仓替身都只定义 values/next/tasks 三属性）。"""
    assert GraphController._has_interrupt(_bug_snapshot()) is True
    assert GraphController._has_interrupt(_FakeSnapshot({}, (), ())) is False
    assert GraphController._has_interrupt(_FakeSnapshot({}, ("coding",), (_FakeTask(),))) is False
    assert GraphController._has_interrupt(None) is False
    # 本文件的 _FakeSnapshot **不定义** interrupts 顶层属性——若判定改读
    # snapshot.interrupts，上面第一条断言会直接红（行为级守门，无源码字节断言的脆性）。
    assert not hasattr(_bug_snapshot(), "interrupts")
