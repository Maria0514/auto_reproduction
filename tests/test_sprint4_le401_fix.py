"""L-E4-01 裁决落地（2026-07-04）单元级边界锚定。

裁决口径：B 档判定 / 错误分类 / metrics 解析的输入视图先经 ``_effective_runs``
过滤——同命令（argv 精确匹配）多次尝试只保留最后一次（保序）；logs 聚合与
runtime_seconds 仍用全量序列（失败证据 / 真实耗时不丢）。

本文件覆盖裁决第 (5) 节清单的单元级三条：
    ① 同命令重试仍失败 → failure，且 representative_stderr 取**末次**尝试；
    ② 命令变体重试成功（argv 不同）→ 仍 failure（锚定 argv 精确匹配口径，
       防止未来误改成「按脚本名/前缀模糊匹配」）；
    ④ ``_effective_runs`` 本体：保序 + last-wins + 空 command 容错。

端到端两条（主叙事翻转 / 无 interrupt 子图内重试 = 边界③）在
tests/test_sprint4_e4_regression_gate.py（复用 CP-E4-2 剧本基建）。

全离线，零 API 配额。
"""

from __future__ import annotations

import importlib
from typing import List, Optional

import pytest

execution_module = importlib.import_module("core.nodes.execution")

from sandbox.local_venv import SandboxRunResult  # noqa: E402

_effective_runs = execution_module._effective_runs
_classify_execution = execution_module._classify_execution
_build_execution_result = execution_module._build_execution_result
ErrorCategory = execution_module.ErrorCategory


@pytest.fixture(autouse=True)
def _no_artifacts(monkeypatch):
    monkeypatch.setattr(execution_module, "collect_artifacts", lambda *a, **k: [])


def _rr(
    command: Optional[List[str]],
    exit_code: int = 0,
    stdout: str = "",
    stderr: str = "",
) -> SandboxRunResult:
    return SandboxRunResult(
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        duration_seconds=0.1,
        timed_out=False,
        output_truncated=False,
        command=command,  # type: ignore[arg-type] - ④ 故意喂 None 验证容错
    )


# ---------------------------------------------------------------------------
# ④ _effective_runs 本体：保序 + last-wins + 空 command 容错
# ---------------------------------------------------------------------------


def test_effective_runs_last_wins_and_order_preserved():
    a1 = _rr(["python", "fetch.py"], exit_code=1, stderr="first fail")
    b = _rr(["python", "prep.py"], exit_code=0)
    a2 = _rr(["python", "fetch.py"], exit_code=0, stdout="retry ok")
    out = _effective_runs([a1, b, a2])
    # last-wins：a1 被 a2 淘汰；保序：按原序列 index 顺序输出（b 在 a2 前）。
    assert out == [b, a2]


def test_effective_runs_empty_and_none_command_tolerated():
    assert _effective_runs([]) == []
    # command=None / [] 归并为同一空键，last-wins 不抛异常。
    r1 = _rr(None, exit_code=1)
    r2 = _rr([], exit_code=0)
    out = _effective_runs([r1, r2])
    assert out == [r2]
    # 单条空 command 也原样保留。
    assert _effective_runs([r1]) == [r1]


def test_effective_runs_distinct_commands_all_kept():
    runs = [_rr(["a"], 1), _rr(["b"], 0), _rr(["c"], 0)]
    assert _effective_runs(runs) == runs


# ---------------------------------------------------------------------------
# ① 同命令重试仍失败 → failure + representative_stderr 取末次
# ---------------------------------------------------------------------------


def test_same_command_retry_still_failing_uses_last_attempt_stderr():
    runs = [
        _rr(["python", "fetch.py"], exit_code=1, stderr="ValueError: boom-first"),
        _rr(["python", "fetch.py"], exit_code=1, stderr="ValueError: boom-second"),
        _rr(["python", "train.py"], exit_code=0,
            stdout='<METRICS>{"acc": 0.9}</METRICS>'),
    ]
    feedback = _classify_execution(None, runs)
    assert feedback.category is not ErrorCategory.NONE, "末次尝试仍失败 → 非成功分类"
    # L-E4-01 副产品：representative_stderr 取同命令最后一次的 stderr。
    assert "boom-second" in feedback.representative_stderr
    assert "boom-first" not in feedback.representative_stderr

    er = _build_execution_result(None, runs, feedback, {"acc": 0.9}, work_dir=".")
    assert er["success"] is False, "effective 视图仍含失败 run → failure"
    # logs 全量：两次失败尝试的证据都在。
    assert "boom-first" in er["logs"] and "boom-second" in er["logs"]
    # runtime 全量：3 条 run 的耗时求和。
    assert er["runtime_seconds"] == pytest.approx(0.3)


# ---------------------------------------------------------------------------
# ② 命令变体重试成功（argv 不同）→ 仍 failure（argv 精确匹配口径锚定）
# ---------------------------------------------------------------------------


def test_command_variant_retry_success_still_failure():
    runs = [
        _rr(["python", "fetch.py", "--branch", "main"], exit_code=1,
            stderr="ValueError: variant boom"),
        # 重试改了参数 → argv 不同 → 不构成「同命令重试」，首败仍进 effective 视图。
        _rr(["python", "fetch.py", "--branch", "dev"], exit_code=0, stdout="ok"),
    ]
    effective = _effective_runs(runs)
    assert effective == runs, "argv 不同 → 两条都保留（精确匹配口径）"

    feedback = _classify_execution(None, runs)
    assert feedback.category is not ErrorCategory.NONE

    er = _build_execution_result(None, runs, feedback, {"acc": 0.9}, work_dir=".")
    assert er["success"] is False, \
        "命令变体（argv 不同）重试成功不闭环——锚定 argv 精确匹配，防口径漂移"


# ---------------------------------------------------------------------------
# 同命令重试成功 → 判定层直接 success（③ 的单元级对照；端到端版在 e4 gate 文件）
# ---------------------------------------------------------------------------


def test_same_command_retry_success_classified_none():
    runs = [
        _rr(["python", "fetch.py"], exit_code=1, stderr="ConnectionResetError"),
        _rr(["python", "fetch.py"], exit_code=0, stdout="ok"),
    ]
    feedback = _classify_execution(None, runs)
    assert feedback.category is ErrorCategory.NONE, "同 argv 末次成功 → NONE"

    er = _build_execution_result(None, runs, feedback, {"acc": 0.9}, work_dir=".")
    assert er["success"] is True
    assert "ConnectionResetError" in er["logs"], "失败证据仍留全量 logs"


if __name__ == "__main__":
    import sys

    sys.exit(pytest.main([__file__, "-v"]))
