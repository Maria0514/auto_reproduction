"""Sprint 7 T-S7-3-7 真跑抽验：coder fix_note 遵守率（现场靶 4 轮 import 同构）。

省配额设计：mock sandbox 前 4 轮恒 import 失败（No module named 'src'，对齐现场靶
task-99eef17bccf2 的 4 轮 import）、第 5 轮成功，让真实 LLM coder 正好修 4 轮就退出
（不跑满 20 轮）。跑完统计 fix_loop_history 各轮 fix_note 遵守率——验 S7-05 后真实
coder 是否遵守"<result> 输出 fix_note"新约定（R-S7-8 软点）。

用真实 SandboxRunResult / SandboxPrepareResult 构造 mock 执行结果（字段经 dataclasses.fields
坐实），patch execution 的 prepare_venv / run_in_venv / collect_artifacts 三入口。

须 Maria 授权真跑（-m e2e，耗 deepxiv/LLM 配额）。遵守率为观测指标（LLM 服从度），
不硬失败——R-S7-8 确定性退化兜底已就位，遵守率低不阻断功能。
"""
from __future__ import annotations

import importlib

import pytest
from langgraph.types import Command

from sandbox.local_venv import SandboxRunResult, SandboxPrepareResult
from tests.test_sprint3_e2e import (
    _make_wal_saver,
    _new_e2e_config,
    _real_initial_state,
)
from core.graph import build_graph

execution_module = importlib.import_module("core.nodes.execution")

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


@pytest.mark.e2e
def test_s705_fix_note_adherence(monkeypatch, tmp_path):
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

    conn, saver = _make_wal_saver(tmp_path / "s705_realrun.db")
    try:
        graph = build_graph(checkpointer=saver)
        config = _new_e2e_config()

        graph.invoke(_real_initial_state(tmp_path), config)  # → planning interrupt#1
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
        history = snap.values.get("fix_loop_history") or []
        total = len(history)
        with_note = sum(1 for r in history if (r.get("fix_note") or "").strip())
        rate = (with_note / total) if total else 0.0

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
        try:
            conn.close()
        except Exception:
            pass
