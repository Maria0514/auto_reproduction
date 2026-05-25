"""Sprint 2 Spike S-1: interrupt + threading 最小可行 demo

验证 LangGraph interrupt() 在工作线程内 invoke 时能否正确暂停 / 主线程能否用独立
SqliteSaver 实例读到 snapshot.next + interrupt 元数据 / 新工作线程能否用
Command(resume=...) 恢复执行并让 interrupt() 返回 resume payload。

跑法：python scripts/spike_interrupt_threading.py
"""

from __future__ import annotations

import sys
import time
import threading
import traceback
from pathlib import Path
from typing import Any, Dict, Optional, TypedDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt, Command

from core.checkpointer import get_checkpointer


SPIKE_DB_PATH = str(PROJECT_ROOT / "workspaces" / "spike_s1_checkpoints.sqlite")
THREAD_ID = "spike-001"


class SpikeState(TypedDict, total=False):
    decision: Optional[Dict[str, Any]]
    resumed: bool
    pre_interrupt_marker: str
    post_interrupt_marker: str


def dummy_planning(state: SpikeState) -> Dict[str, Any]:
    print(f"[node] dummy_planning entered, state keys={list(state.keys())}")
    decision = interrupt({"hint": "test", "stage": "spike-s1"})
    print(f"[node] dummy_planning resumed with decision={decision!r}")
    return {
        "decision": decision,
        "resumed": True,
        "post_interrupt_marker": "node-continued-past-interrupt",
    }


def build_graph(checkpointer):
    g = StateGraph(SpikeState)
    g.add_node("dummy_planning", dummy_planning)
    g.add_edge(START, "dummy_planning")
    g.add_edge("dummy_planning", END)
    return g.compile(checkpointer=checkpointer)


def reset_spike_db() -> None:
    db_file = Path(SPIKE_DB_PATH)
    if db_file.exists():
        db_file.unlink()
    for suffix in ("-wal", "-shm"):
        side = Path(SPIKE_DB_PATH + suffix)
        if side.exists():
            side.unlink()


def main() -> int:
    overall_t0 = time.perf_counter()
    results: Dict[str, str] = {}
    errors: Dict[str, str] = {}

    def cp(name: str, ok: bool, detail: str = "") -> None:
        results[name] = "PASS" if ok else "FAIL"
        tag = "PASS" if ok else "FAIL"
        line = f"[{tag}] {name}"
        if detail:
            line += f" — {detail}"
        print(line)

    print("=" * 72)
    print(f"Spike S-1: interrupt + threading 最小可行 demo")
    print(f"DB path: {SPIKE_DB_PATH}")
    print(f"Thread id: {THREAD_ID}")
    print("=" * 72)

    reset_spike_db()

    # ------------------------------------------------------------------
    # 阶段 1：工作线程 #1 跑到 interrupt 自然退出
    # ------------------------------------------------------------------
    t_phase1_start = time.perf_counter()
    worker1_checkpointer = get_checkpointer(SPIKE_DB_PATH)
    graph_worker1 = build_graph(worker1_checkpointer)
    config = {"configurable": {"thread_id": THREAD_ID}}

    worker1_result: Dict[str, Any] = {}

    def worker1_target() -> None:
        try:
            ret = graph_worker1.invoke({"pre_interrupt_marker": "pre"}, config)
            worker1_result["ret"] = ret
        except BaseException as exc:  # noqa: BLE001
            worker1_result["exc"] = exc
            worker1_result["tb"] = traceback.format_exc()

    worker1 = threading.Thread(target=worker1_target, daemon=True, name="spike-worker-1")
    worker1.start()
    worker1.join(timeout=10.0)
    t_phase1_end = time.perf_counter()

    worker1_alive = worker1.is_alive()
    worker1_exc = worker1_result.get("exc")
    worker1_ret = worker1_result.get("ret")
    print(f"[phase1] worker1 joined in {t_phase1_end - t_phase1_start:.3f}s, "
          f"alive={worker1_alive}, exc={type(worker1_exc).__name__ if worker1_exc else None}, "
          f"ret_keys={list(worker1_ret.keys()) if isinstance(worker1_ret, dict) else worker1_ret}")
    if worker1_result.get("tb"):
        print(f"[phase1][traceback]\n{worker1_result['tb']}")

    cp("CP-S1-5",
       ok=(not worker1_alive) and worker1_exc is None,
       detail=f"worker1 alive_after_join={worker1_alive}, exc={worker1_exc!r}")
    cp("CP-S1-2",
       ok=(not worker1_alive),
       detail=f"thread.is_alive()={worker1_alive}")

    # ------------------------------------------------------------------
    # 阶段 2：主线程用新 SqliteSaver 读 snapshot
    # ------------------------------------------------------------------
    time.sleep(0.5)
    t_phase2_start = time.perf_counter()
    main_checkpointer = get_checkpointer(SPIKE_DB_PATH)
    graph_main = build_graph(main_checkpointer)
    try:
        snapshot = graph_main.get_state(config)
        snapshot_exc = None
    except BaseException as exc:  # noqa: BLE001
        snapshot = None
        snapshot_exc = exc
        errors["snapshot"] = traceback.format_exc()
    t_phase2_end = time.perf_counter()

    if snapshot is not None:
        snapshot_next = snapshot.next
        snapshot_tasks = snapshot.tasks
        interrupt_meta = []
        for task in snapshot_tasks:
            interrupts = getattr(task, "interrupts", None) or ()
            for it in interrupts:
                interrupt_meta.append({
                    "task_name": getattr(task, "name", None),
                    "interrupt_value": getattr(it, "value", None),
                    "interrupt_id": getattr(it, "id", None),
                })
        print(f"[phase2] snapshot.next={snapshot_next}, "
              f"#tasks={len(snapshot_tasks)}, interrupt_meta={interrupt_meta}")
    else:
        snapshot_next = None
        snapshot_tasks = ()
        interrupt_meta = []
        print(f"[phase2][error] get_state failed: {snapshot_exc!r}")

    cp("CP-S1-3",
       ok=bool(snapshot_next) and len(interrupt_meta) > 0,
       detail=f"snapshot.next={snapshot_next}, #interrupt_meta={len(interrupt_meta)}")
    cp("CP-S1-6",
       ok=(snapshot_exc is None) and (main_checkpointer is not worker1_checkpointer),
       detail=f"snapshot_exc={snapshot_exc!r}, different_instances={main_checkpointer is not worker1_checkpointer}")

    # ------------------------------------------------------------------
    # 阶段 3：起新工作线程 resume
    # ------------------------------------------------------------------
    t_phase3_start = time.perf_counter()
    worker2_checkpointer = get_checkpointer(SPIKE_DB_PATH)
    graph_worker2 = build_graph(worker2_checkpointer)

    worker2_result: Dict[str, Any] = {}

    resume_payload = {"decision": "ok", "spike": "s1"}

    def worker2_target() -> None:
        try:
            ret = graph_worker2.invoke(Command(resume=resume_payload), config)
            worker2_result["ret"] = ret
        except BaseException as exc:  # noqa: BLE001
            worker2_result["exc"] = exc
            worker2_result["tb"] = traceback.format_exc()

    worker2 = threading.Thread(target=worker2_target, daemon=True, name="spike-worker-2")
    worker2.start()
    worker2.join(timeout=10.0)
    t_phase3_end = time.perf_counter()

    worker2_alive = worker2.is_alive()
    worker2_exc = worker2_result.get("exc")
    worker2_ret = worker2_result.get("ret") or {}
    if worker2_result.get("tb"):
        print(f"[phase3][traceback]\n{worker2_result['tb']}")
    print(f"[phase3] worker2 joined in {t_phase3_end - t_phase3_start:.3f}s, "
          f"alive={worker2_alive}, ret={worker2_ret}")

    decision_in_ret = worker2_ret.get("decision") if isinstance(worker2_ret, dict) else None
    resumed_flag = worker2_ret.get("resumed") if isinstance(worker2_ret, dict) else None
    post_marker = worker2_ret.get("post_interrupt_marker") if isinstance(worker2_ret, dict) else None

    cp("CP-S1-1",
       ok=(not worker2_alive) and worker2_exc is None and resumed_flag is True,
       detail=f"worker2_alive={worker2_alive}, exc={worker2_exc!r}, resumed={resumed_flag}")
    cp("CP-S1-4",
       ok=(post_marker == "node-continued-past-interrupt") and (resumed_flag is True),
       detail=f"post_marker={post_marker!r}, resumed={resumed_flag}")

    # interrupt() 返回值 = resume payload
    decision_match = (decision_in_ret == resume_payload)
    cp("CP-S1-7-precheck-resume-value",
       ok=decision_match,
       detail=f"decision_in_state={decision_in_ret!r}, expected={resume_payload!r}")

    # ------------------------------------------------------------------
    # 阶段 4：复读 snapshot 确认图已走到 END
    # ------------------------------------------------------------------
    final_snapshot = graph_main.get_state(config)
    final_next = final_snapshot.next
    print(f"[phase4] final snapshot.next={final_next}, "
          f"final values keys={list(final_snapshot.values.keys()) if final_snapshot.values else None}")
    final_at_end = (not final_next) or final_next == ()

    overall_t1 = time.perf_counter()
    total_elapsed = overall_t1 - overall_t0

    # ------------------------------------------------------------------
    # 总结
    # ------------------------------------------------------------------
    print("=" * 72)
    print(f"phase1 (worker1 → interrupt): {t_phase1_end - t_phase1_start:.3f}s")
    print(f"phase2 (main read snapshot):  {t_phase2_end - t_phase2_start:.3f}s")
    print(f"phase3 (worker2 resume):      {t_phase3_end - t_phase3_start:.3f}s")
    print(f"total elapsed:                {total_elapsed:.3f}s")
    print(f"final snapshot.next empty:    {final_at_end}")
    print("-" * 72)
    for cp_name, status in results.items():
        print(f"  {cp_name}: {status}")
    overall_ok = all(v == "PASS" for v in results.values()) and final_at_end and total_elapsed < 30.0
    print("=" * 72)
    print(f"Overall: {'PASS — 成功 resume + 状态推进' if overall_ok else 'FAIL'}")
    print("=" * 72)

    return 0 if overall_ok else 1


if __name__ == "__main__":
    sys.exit(main())
