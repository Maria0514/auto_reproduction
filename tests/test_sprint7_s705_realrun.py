"""Sprint 7 T-S7-3-7 真跑抽验：coder fix_note 遵守率（现场靶 4 轮 import 同构）。

省配额设计：mock sandbox 前 4 轮恒 import 失败（No module named 'src'，对齐现场靶
task-99eef17bccf2 的 4 轮 import）、第 5 轮成功，让真实 LLM coder 正好修 4 轮就退出
（不跑满 20 轮）。跑完统计 fix_loop_history 各轮 fix_note 遵守率——验 S7-05 后真实
coder 是否遵守"<result> 输出 fix_note"新约定（R-S7-8 软点）。

用真实 SandboxRunResult / SandboxPrepareResult 构造 mock 执行结果（字段经 dataclasses.fields
坐实），patch execution 的 prepare_venv / run_in_venv / collect_artifacts 三入口。

须 Maria 授权真跑（-m e2e，耗 deepxiv/LLM 配额）。遵守率为观测指标（LLM 服从度），
不硬失败——R-S7-8 确定性退化兜底已就位，遵守率低不阻断功能。

⚠ **2026-08-07 结构性修复（读改这个文件前先看这一段）**
------------------------------------------------------------------
本文件原先把 checkpoint 库与 workspace 都落在 ``tmp_path``。pytest 只保留最近 3 轮临时
目录 ⇒ **真跑一结束，证据就开始倒计时灭失**。2026-07-22 那次真跑的 `fix_loop_history`
原始记录已实测**永久不可复原**（取证过程见
`docs/sprint7/test-reports/2026-08-07_s705-b3-realrun-forensics.md` §1.1）。
S7-10 吃过同一教训并写进了计划（`docs/sprint7/dev-plan.md:2739-2740` 要求跑前把关键 state
落盘成 bundle JSON），**但那条要求从未被执行**——因为它只是文档里的一句话。

⇒ 现在它是**代码**：
  1. `durable_run_dir()` 把 checkpoint 库 + workspace 落到 ``workspace/runs/``（已 gitignore，
     不进版本控制、不污染 git status，但**不会被回收**）；
  2. `_archive_evidence()` 在 ``finally`` 里把关键 state 落成**脱敏后**的 bundle JSON 到
     ``docs/sprint7/test-reports/realrun-bundles/``（进版本控制）。**放在 finally 是刻意的**
     ——断言失败那次的证据往往最值钱。
两个动作都只在 ``@pytest.mark.e2e`` 上下文下允许（见 `tests/realrun_evidence.py` 的闸门），
默认 pytest 跑不到这里、也写不出任何东西。
"""
from __future__ import annotations

import importlib
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

import pytest
from langgraph.types import Command

from sandbox.local_venv import SandboxRunResult, SandboxPrepareResult
from tests.realrun_evidence import dump_realrun_bundle, durable_run_dir
from tests.test_sprint3_e2e import (
    PAPER_ARXIV_ID,
    _make_wal_saver,
    _new_e2e_config,
    _real_initial_state,
)
from core.graph import build_graph

execution_module = importlib.import_module("core.nodes.execution")

_DB_NAME = "s705_realrun.db"
_RUN_NAME = "s705_realrun"
_BUNDLE_NAME = "s705-fix-note-adherence"

_IMPORT_ERR = (
    "Traceback (most recent call last):\n"
    '  File "scripts/train.py", line 12, in <module>\n'
    "    from src.utils import load_config\n"
    "ModuleNotFoundError: No module named 'src'"
)


def _run(exit_code: int, stdout: str = "", stderr: str = "") -> SandboxRunResult:
    return SandboxRunResult(
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        duration_seconds=0.1,
        timed_out=False,
        output_truncated=False,
        command=["python", "scripts/train.py"],
    )


def _prep() -> SandboxPrepareResult:
    return SandboxPrepareResult(
        success=True,
        venv_dir="/w/.venv",
        python_exe="/w/.venv/bin/python",
        pip_exe="/w/.venv/bin/pip",
        env_info={"python_version": "3.11"},
        install_log="ok",
        install_failed_packages=[],
        error=None,
    )


def measure_adherence(history: Any) -> Dict[str, Any]:
    """fix_note 遵守率度量（与 §2.1 口径一致：strip 后非空才算遵守）。

    抽成函数是为了让**归档物里的度量**与**打印/断言用的度量**同源——两处各算一遍必然漂移。
    """
    rounds = list(history or [])
    total = len(rounds)
    with_note = sum(1 for r in rounds if (r.get("fix_note") or "").strip())
    return {
        "rounds": total,
        "with_fix_note": with_note,
        "rate": (with_note / total) if total else None,
        "per_round": [
            {
                "round_number": r.get("round_number"),
                "error_category": r.get("error_category"),
                "has_fix_note": bool((r.get("fix_note") or "").strip()),
                "files_touched": r.get("files_touched"),
            }
            for r in rounds
        ],
    }


def _archive_evidence(
    request: Any,
    *,
    state_values: Dict[str, Any],
    run_dir: Path,
    meta: Dict[str, Any],
    sink_dir: Optional[Path] = None,
) -> Path:
    """把本次真跑的关键 state 落成脱敏 bundle JSON，返回落盘路径。

    ⚠ 不在这里做任何"证据好不好看"的判断——归档只负责**把当时的事实原样留下**，
    判读留给报告。度量（遵守率）随附是为了让报告不必重新解析 history。
    """
    return dump_realrun_bundle(
        request,
        sprint="sprint7",
        name=_BUNDLE_NAME,
        state=state_values,
        extra={
            "task": "T-S7-3-7",
            "paper_arxiv_id": PAPER_ARXIV_ID,
            "run_dir": str(run_dir),
            "checkpoint_db": str(run_dir / _DB_NAME),
            "sandbox": "mock（前 4 轮 import 失败 + 第 5 轮成功；真实 LLM + 真实 deepxiv）",
            "adherence": measure_adherence(state_values.get("fix_loop_history")),
            **meta,
        },
        sink_dir=sink_dir,
    )


@pytest.mark.e2e
def test_s705_fix_note_adherence(monkeypatch, request):
    # mock sandbox：前 4 轮 import 失败 + 第 5 轮成功（coder 修 4 轮省配额）
    runs = [_run(1, stderr=_IMPORT_ERR) for _ in range(4)]
    runs.append(_run(0, stdout='<METRICS>{"accuracy": 0.9}</METRICS>'))
    run_iter = iter(runs)

    monkeypatch.setattr(execution_module, "prepare_venv", lambda *a, **k: _prep())

    def fake_run(*a, **k):
        try:
            return next(run_iter)
        except StopIteration:
            return runs[-1]

    monkeypatch.setattr(execution_module, "run_in_venv", fake_run)
    monkeypatch.setattr(execution_module, "collect_artifacts", lambda *a, **k: [])

    # 持久运行目录（替代 tmp_path）：checkpoint 库与 workspace 都落这里，跑完不回收
    run_dir = durable_run_dir(request, _RUN_NAME)
    conn, saver = _make_wal_saver(run_dir / _DB_NAME)
    graph = None
    config = _new_e2e_config()
    state_values: Dict[str, Any] = {}
    started = time.monotonic()
    try:
        graph = build_graph(checkpointer=saver)

        graph.invoke(_real_initial_state(run_dir), config)  # → planning interrupt#1
        out = graph.invoke(Command(resume={"decision": "approve"}), config)  # 修复循环

        # 真实 LLM 可能中途凭证 interrupt#3（方差）→ 降级继续，最多 6 次
        for _ in range(6):
            if "__interrupt__" not in out:
                break
            iv = out["__interrupt__"][0].value
            if iv.get("interrupt_kind") == "user_input_request":
                try:
                    # 凭证门降级放行的 resume 契约是 {"degrade": True}（coding.py:822
                    # 只认 resume.get("degrade")）；旧版误回传 gate 发出的 allow_degrade
                    # 键 + 空 value → 命中"非法 resume（缺 value 且非 degrade）"反复重弹，
                    # coder 进不了修复循环、fix_loop_history 恒空（T-S7-3-7 上次白烧根因）。
                    out = graph.invoke(
                        Command(resume={"degrade": True}),
                        config,
                    )
                except Exception as e:  # noqa: BLE001
                    print(f"降级 resume 出错（不影响抽验取数）: {e}")
                    break
            else:
                break  # dev_loop interrupt#2 等 → 停

        try:
            conn.commit()
        except Exception:
            pass

        snap = graph.get_state(config)
        state_values = dict(snap.values)
        history = state_values.get("fix_loop_history") or []
        stats = measure_adherence(history)
        total, with_note = stats["rounds"], stats["with_fix_note"]
        rate = stats["rate"] or 0.0

        print(f"\n{'=' * 64}")
        print(f">>> S7-05 T-S7-3-7 coder fix_note 遵守率: {with_note}/{total} = {rate:.0%}")
        for r in history:
            fn = (r.get("fix_note") or "").strip()
            mark = "OK" if fn else "空(未遵守)"
            print(
                f"  round{r.get('round_number')} [{r.get('error_category')}] {mark} "
                f"files_touched={r.get('files_touched')}"
            )
            if fn:
                print(f"      fix_note: {fn[:90]}")
        print(f"{'=' * 64}")

        assert total >= 1, f"应至少记录 1 轮修复（实际 {total}）；真实 coder 未进修复循环"
        # 遵守率为观测指标（LLM 服从度），不硬失败——R-S7-8 退化兜底保护。
    finally:
        in_flight = sys.exc_info()[0]  # finally 里能看到正在传播的异常（用于决定是否上抛归档错误）

        # 断言失败 / 中途抛错时也要留证：连接还没关，再取一次 state（best-effort）
        if not state_values and graph is not None:
            try:
                state_values = dict(graph.get_state(config).values)
            except Exception:  # noqa: BLE001
                pass
        try:
            conn.commit()
        except Exception:
            pass
        try:
            conn.close()
        except Exception:
            pass

        try:
            bundle = _archive_evidence(
                request,
                state_values=state_values,
                run_dir=run_dir,
                meta={
                    "thread_id": config["configurable"]["thread_id"],
                    "elapsed_seconds": round(time.monotonic() - started, 1),
                    "raised": in_flight.__name__ if in_flight else None,
                },
            )
            print(f">>> 真跑证据已归档 -> {bundle}")
            print(f">>> 原始产物（checkpoint 库 / workspace）留在 -> {run_dir}")
        except Exception as exc:  # noqa: BLE001
            print(f"!!! 真跑证据归档失败: {exc!r}")
            if in_flight is None:
                # 用例本身没别的异常在传播 ⇒ 归档失败必须让用例红（否则又变成"静默没留证"）
                raise
