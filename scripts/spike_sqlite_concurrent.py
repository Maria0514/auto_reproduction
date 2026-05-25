"""Sprint 2 Spike S-2: SqliteSaver 跨线程并发 60s 压力测试

验证 sp1 `core/checkpointer.py` 的 SqliteSaver（WAL + check_same_thread=False）
在主线程持续读 / 工作线程持续写的 60 秒并发负载下：
  - 是否抛 `sqlite3.ProgrammingError`（独立实例 + check_same_thread=False 方案是否生效）
  - 是否抛 `sqlite3.OperationalError: database is locked`（WAL 模式并发能力是否够用）
  - 读 / 写延迟 p99 是否分别 < 50ms / < 100ms（够不够 Streamlit 1.5s 轮询与节点级写入）

跑法：python scripts/spike_sqlite_concurrent.py
"""

from __future__ import annotations

import os
import sys
import time
import sqlite3
import threading
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from langgraph.checkpoint.base import empty_checkpoint

from core.checkpointer import get_checkpointer


SPIKE_DB_PATH = str(PROJECT_ROOT / "workspaces" / "spike_s2_checkpoints.sqlite")
THREAD_ID = "spike-s2-001"
DURATION_SEC = 60.0
READ_INTERVAL_SEC = 0.1   # 主线程目标节拍 100ms
WRITE_INTERVAL_SEC = 0.2  # 工作线程目标节拍 200ms

# 100KB 大 dict payload，模拟 paper_analysis 节点输出体积
LARGE_PAYLOAD_BYTES = 100 * 1024


# ---------------- 辅助统计工具 ----------------


def percentile(values: List[float], pct: float) -> float:
    if not values:
        return 0.0
    if pct <= 0:
        return values[0]
    if pct >= 100:
        return max(values)
    s = sorted(values)
    k = (len(s) - 1) * (pct / 100.0)
    f = int(k)
    c = min(f + 1, len(s) - 1)
    if f == c:
        return s[f]
    return s[f] + (s[c] - s[f]) * (k - f)


def ascii_histogram(values_ms: List[float], bins: int = 20, width: int = 50) -> str:
    """ASCII 直方图，避免引入 matplotlib 依赖。"""
    if not values_ms:
        return "(no samples)"
    lo = min(values_ms)
    hi = max(values_ms)
    if hi - lo < 1e-9:
        return f"all samples at {lo:.3f}ms (n={len(values_ms)})"
    step = (hi - lo) / bins
    edges = [lo + i * step for i in range(bins + 1)]
    counts = [0] * bins
    for v in values_ms:
        idx = int((v - lo) / step)
        if idx >= bins:
            idx = bins - 1
        counts[idx] += 1
    cmax = max(counts)
    lines = []
    for i in range(bins):
        bar = "#" * int(round(counts[i] / cmax * width)) if cmax else ""
        lines.append(f"  [{edges[i]:7.2f}, {edges[i + 1]:7.2f}) ms | {counts[i]:5d} {bar}")
    return "\n".join(lines)


# ---------------- 大 payload 构造（每次写入都新建，避免共享引用导致 LangGraph 内部缓存命中） ----------------


def build_large_payload(write_seq: int) -> Dict[str, Any]:
    """构造 ~100KB 的伪 paper_analysis 输出 dict。"""
    base_blob = "x" * (LARGE_PAYLOAD_BYTES // 8)  # ASCII 字符 1B 即可，但留几路冗余字段
    return {
        "method_summary": f"#{write_seq} " + base_blob[:8000],
        "sections_read": [f"Section-{i}" for i in range(20)],
        "datasets": [{"name": f"DS-{i}", "url": f"https://example.com/ds-{i}"} for i in range(15)],
        "metrics": [{"name": f"M-{i}", "value": float(i) / 10.0} for i in range(15)],
        "analysis_notes": [base_blob[:4000], base_blob[:4000]],
        "_blob_payload": base_blob,  # 主体填充字段
        "_seq": write_seq,
        "_ts": time.time(),
    }


def build_checkpoint_for_write(write_seq: int) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    """构造一次 put 调用所需的 (checkpoint, metadata, new_versions)。

    每次都新建一个 empty_checkpoint() 拿到新的 id + ts，模拟节点写入新 checkpoint。
    """
    ck = empty_checkpoint()
    payload = build_large_payload(write_seq)
    ck["channel_values"] = {"global_state": payload}
    # 给每个 write 一个递增的 channel version，模拟 LangGraph 内部行为
    version = write_seq + 1
    ck["channel_versions"] = {"global_state": version}
    ck["versions_seen"] = {"__input__": {"global_state": version}}
    metadata = {
        "source": "spike-s2",
        "step": write_seq,
        "writes": {"global_state": "spike-write"},
        "parents": {},
    }
    new_versions = {"global_state": version}
    return ck, metadata, new_versions


# ---------------- 主流程 ----------------


def reset_spike_db() -> None:
    db_file = Path(SPIKE_DB_PATH)
    Path(SPIKE_DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    if db_file.exists():
        db_file.unlink()
    for suffix in ("-wal", "-shm"):
        side = Path(SPIKE_DB_PATH + suffix)
        if side.exists():
            side.unlink()


def main() -> int:
    overall_t0 = time.perf_counter()

    print("=" * 72)
    print("Spike S-2: SqliteSaver 跨线程并发 60s 压力测试")
    print(f"DB path: {SPIKE_DB_PATH}")
    print(f"Thread id: {THREAD_ID}")
    print(f"Duration: {DURATION_SEC:.0f}s | read interval={READ_INTERVAL_SEC*1000:.0f}ms | "
          f"write interval={WRITE_INTERVAL_SEC*1000:.0f}ms | payload ~{LARGE_PAYLOAD_BYTES // 1024}KB")
    print("=" * 72)

    reset_spike_db()

    # 主线程与工作线程独立创建 SqliteSaver 实例，共享同一文件
    main_saver = get_checkpointer(SPIKE_DB_PATH)
    worker_saver = get_checkpointer(SPIKE_DB_PATH)
    different_instances = main_saver is not worker_saver

    # LangGraph 1.1.x SqliteSaver.put 强制要求 config["configurable"]["checkpoint_ns"]，
    # 沿用 LangGraph 主图默认值（空字符串表示根命名空间）
    config = {"configurable": {"thread_id": THREAD_ID, "checkpoint_ns": ""}}

    stop_event = threading.Event()

    # 共享统计容器（各自由对应线程写，主线程在 stop 之后才聚合）
    read_latencies_ms: List[float] = []
    read_count = 0
    read_seen_checkpoints: set = set()
    read_last_seq: Optional[int] = None
    read_errors: List[Tuple[str, str]] = []  # (exc_type_qualname, message)

    write_latencies_ms: List[float] = []
    write_count = 0
    write_errors: List[Tuple[str, str]] = []

    def is_locked_err(exc: BaseException) -> bool:
        return isinstance(exc, sqlite3.OperationalError) and "locked" in str(exc).lower()

    def is_thread_err(exc: BaseException) -> bool:
        return isinstance(exc, sqlite3.ProgrammingError) and "thread" in str(exc).lower()

    # ---------------- 工作线程：写 ----------------

    def worker_target() -> None:
        nonlocal write_count
        seq = 0
        next_tick = time.perf_counter()
        while not stop_event.is_set():
            ck, metadata, new_versions = build_checkpoint_for_write(seq)
            t0 = time.perf_counter()
            try:
                worker_saver.put(config, ck, metadata, new_versions)
                write_latencies_ms.append((time.perf_counter() - t0) * 1000.0)
                write_count += 1
            except BaseException as exc:  # noqa: BLE001
                write_errors.append((type(exc).__module__ + "." + type(exc).__qualname__, str(exc)))
                # 即便错也继续测，把全部异常都记录下来
            seq += 1
            next_tick += WRITE_INTERVAL_SEC
            sleep_for = next_tick - time.perf_counter()
            if sleep_for > 0:
                if stop_event.wait(sleep_for):
                    break
            else:
                # 慢了，立即下一轮
                next_tick = time.perf_counter()

    worker_thread = threading.Thread(target=worker_target, daemon=True, name="spike-s2-writer")
    worker_thread.start()

    # 早期健康探测：worker 启动 1s 内若已死且未写入任何数据，立即打印错误并继续
    # （后续仍跑满 60s，方便看读端有没有问题，但提前暴露根因）
    time.sleep(1.0)
    if not worker_thread.is_alive() and write_count == 0:
        print(f"[early-warn] worker thread died within 1s without any successful write. "
              f"#write_errors={len(write_errors)}")
        for t, m in write_errors[:5]:
            print(f"  [early write_error] {t}: {m}")

    # ---------------- 主线程：读 ----------------

    deadline = time.perf_counter() + DURATION_SEC
    next_read_tick = time.perf_counter()
    while time.perf_counter() < deadline:
        t0 = time.perf_counter()
        try:
            ck_tuple = main_saver.get_tuple(config)
            read_latencies_ms.append((time.perf_counter() - t0) * 1000.0)
            read_count += 1
            if ck_tuple is not None:
                ck_id = ck_tuple.checkpoint.get("id")
                if ck_id is not None:
                    read_seen_checkpoints.add(ck_id)
                vals = ck_tuple.checkpoint.get("channel_values") or {}
                gs = vals.get("global_state")
                if isinstance(gs, dict) and "_seq" in gs:
                    read_last_seq = gs["_seq"]
        except BaseException as exc:  # noqa: BLE001
            read_errors.append((type(exc).__module__ + "." + type(exc).__qualname__, str(exc)))

        next_read_tick += READ_INTERVAL_SEC
        sleep_for = next_read_tick - time.perf_counter()
        if sleep_for > 0:
            time.sleep(sleep_for)
        else:
            next_read_tick = time.perf_counter()

    stop_event.set()
    worker_thread.join(timeout=5.0)
    worker_alive = worker_thread.is_alive()

    # 最终读一次（worker 已停止），用于断言"读到的最新 checkpoint 计数 == 工作线程写入次数"
    final_read_ck_id: Optional[str] = None
    final_read_seq: Optional[int] = None
    final_exc: Optional[BaseException] = None
    try:
        final_tuple = main_saver.get_tuple(config)
        if final_tuple is not None:
            final_read_ck_id = final_tuple.checkpoint.get("id")
            vals = final_tuple.checkpoint.get("channel_values") or {}
            gs = vals.get("global_state")
            if isinstance(gs, dict) and "_seq" in gs:
                final_read_seq = gs["_seq"]
            if final_read_ck_id is not None:
                read_seen_checkpoints.add(final_read_ck_id)
    except BaseException as exc:  # noqa: BLE001
        final_exc = exc

    overall_t1 = time.perf_counter()
    total_elapsed = overall_t1 - overall_t0

    # ---------------- 异常分类 ----------------

    programming_errors = [(t, m) for t, m in read_errors + write_errors
                          if "ProgrammingError" in t and "thread" in m.lower()]
    locked_errors_read = [(t, m) for t, m in read_errors if "OperationalError" in t and "locked" in m.lower()]
    locked_errors_write = [(t, m) for t, m in write_errors if "OperationalError" in t and "locked" in m.lower()]

    # ---------------- 计算分布 ----------------

    def stats(vals: List[float]) -> Dict[str, float]:
        if not vals:
            return {"n": 0, "min": 0.0, "p50": 0.0, "p90": 0.0, "p99": 0.0,
                    "max": 0.0, "mean": 0.0}
        return {
            "n": len(vals),
            "min": min(vals),
            "p50": percentile(vals, 50),
            "p90": percentile(vals, 90),
            "p99": percentile(vals, 99),
            "max": max(vals),
            "mean": sum(vals) / len(vals),
        }

    rstats = stats(read_latencies_ms)
    wstats = stats(write_latencies_ms)

    # ---------------- CP 断言 ----------------

    def cp(name: str, ok: bool, detail: str = "") -> Tuple[str, bool, str]:
        tag = "PASS" if ok else "FAIL"
        line = f"[{tag}] {name}"
        if detail:
            line += f" — {detail}"
        print(line)
        return name, ok, detail

    results: List[Tuple[str, bool, str]] = []
    results.append(cp("CP-S2-1",
                      ok=len(programming_errors) == 0,
                      detail=f"#ProgrammingError(thread)={len(programming_errors)}"))
    results.append(cp("CP-S2-2",
                      ok=(len(locked_errors_read) + len(locked_errors_write)) == 0,
                      detail=f"#database_locked read={len(locked_errors_read)} write={len(locked_errors_write)}"))

    # CP-S2-3（dev-plan L241 原意）：「主线程读到的最新 checkpoint 计数 == 工作线程写入次数（最多差 1）」
    # 实操判定：worker 停止后主线程最终读到的 _seq 应当等于 write_count - 1（最后一次写的序号）。
    # 允许差 1 对应「正在写但尚未 commit」的瞬间——本测里 worker 已 join，理论上严格 == write_count - 1。
    # unique_ck_ids 仅作辅助信息（受主线程 100ms 采样间隔影响，不能用来卡 CP）。
    expected_final_seq = (write_count - 1) if write_count > 0 else None
    if write_count == 0:
        cp3_ok = False
    else:
        cp3_ok = (final_read_seq is not None
                  and abs(write_count - 1 - final_read_seq) <= 1)
    cp3_detail = (f"write_count={write_count}, final_read_seq={final_read_seq}, "
                  f"expected_final_seq={expected_final_seq} (tolerance ±1); "
                  f"aux: unique_ck_ids_seen={len(read_seen_checkpoints)} "
                  f"(受 100ms 采样间隔影响，仅辅助信息)")
    results.append(cp("CP-S2-3", ok=cp3_ok, detail=cp3_detail))

    results.append(cp("CP-S2-4",
                      ok=rstats["p99"] < 50.0,
                      detail=f"read p99={rstats['p99']:.3f}ms (< 50ms required), n={rstats['n']}"))
    results.append(cp("CP-S2-5",
                      ok=wstats["p99"] < 100.0,
                      detail=f"write p99={wstats['p99']:.3f}ms (< 100ms required), n={wstats['n']}"))

    # CP-S2-6 由报告归档行为决定，spike 脚本里只能给"具备归档所需的全部数据" pre-flight
    has_full_report_data = bool(rstats["n"] and wstats["n"])
    results.append(cp("CP-S2-6-precheck",
                      ok=has_full_report_data,
                      detail=f"distribution_data_ready={has_full_report_data} (实际归档由人工写报告完成)"))

    # ---------------- 输出汇总 ----------------

    print("=" * 72)
    print(f"total elapsed:           {total_elapsed:.3f}s")
    print(f"different_saver_instance:{different_instances}")
    print(f"worker thread alive:     {worker_alive}")
    print(f"write_count:             {write_count}")
    print(f"read_count:              {read_count}")
    print(f"unique_ck_ids_seen:      {len(read_seen_checkpoints)}")
    print(f"final_read_seq:          {final_read_seq}  (expected {write_count - 1 if write_count else 0})")
    print(f"final_read_get_exc:      {final_exc!r}")
    print("-" * 72)
    print(f"read  latency (ms): n={rstats['n']:5d}  min={rstats['min']:.3f}  p50={rstats['p50']:.3f}  "
          f"p90={rstats['p90']:.3f}  p99={rstats['p99']:.3f}  max={rstats['max']:.3f}  mean={rstats['mean']:.3f}")
    print(f"write latency (ms): n={wstats['n']:5d}  min={wstats['min']:.3f}  p50={wstats['p50']:.3f}  "
          f"p90={wstats['p90']:.3f}  p99={wstats['p99']:.3f}  max={wstats['max']:.3f}  mean={wstats['mean']:.3f}")
    print("-" * 72)
    print("read latency distribution (ascii histogram):")
    print(ascii_histogram(read_latencies_ms))
    print("-" * 72)
    print("write latency distribution (ascii histogram):")
    print(ascii_histogram(write_latencies_ms))
    print("-" * 72)
    if read_errors or write_errors:
        print("ERRORS (first 10 of each):")
        for t, m in read_errors[:10]:
            print(f"  [read]  {t}: {m}")
        for t, m in write_errors[:10]:
            print(f"  [write] {t}: {m}")
    print("=" * 72)
    for cp_name, ok, _ in results:
        print(f"  {cp_name}: {'PASS' if ok else 'FAIL'}")
    overall_ok = all(ok for _, ok, _ in results) and (final_exc is None) and (not worker_alive)
    print("=" * 72)
    print(f"Overall: {'PASS — 并发读写 60s 无错 + 延迟达标' if overall_ok else 'FAIL'}")
    print("=" * 72)

    return 0 if overall_ok else 1


if __name__ == "__main__":
    sys.exit(main())
