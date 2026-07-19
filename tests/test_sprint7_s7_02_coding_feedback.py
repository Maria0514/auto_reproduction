"""Sprint 7 任务 T-S7-1-3（S7-02，架构 §5.4）：coding 反馈路径化 + log_file_path 推导。

覆盖 dev-plan §5 T-S7-1-3 自测检查点 CP-1.3-1 ~ CP-1.3-5（AC-S7-05/07）：
    - CP-1.3-1 log_file_path 子键指向 round_{fix_round}.log；error_category 快速提示保留；
    - CP-1.3-2 端到端可读：落盘 + 路径推导联跑 → read_code_file 读到含 No module named 'src'；
    - CP-1.3-3 AC-S7-07 守门（须验红）：stderr_tail 不再是 logs[-2000:] 截断产物，
      而是固定指引串；反馈以 log_file_path 为准；注掉落盘 + 路径注入断言变红；
    - CP-1.3-4 路径确定性推导：落盘失败/文件不存在 → read_code_file 读到"文件不存在"退回 errors；
    - CP-1.3-5 representative_stderr 未被 S7-02 触碰；execution 侧 stderr_tail 维持尾部（AA-S7-3 正交）。

编号对齐（架构师 2026-07-19 裁决）：coding 侧 log_file_path 轮号 = fix_count - 1（读上一轮
execution 落盘日志）；payload["fix_round"] 保持 fix_count。off-by-one 防回归锁死
fix_count=1 → round_0.log。

全离线，零 API 配额。
"""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

import config
from core.state import ExecutionMode

execution_module = importlib.import_module("core.nodes.execution")
coding_module = importlib.import_module("core.nodes.coding")

from sandbox.local_venv import SandboxPrepareResult, SandboxRunResult  # noqa: E402

_EXEC_LOGS_SUBDIR = coding_module._EXEC_LOGS_SUBDIR
_STDERR_TAIL_GUIDANCE = coding_module._STDERR_TAIL_GUIDANCE

_IMPORT_ERR = "Traceback (most recent call last):\nModuleNotFoundError: No module named 'src'"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _prep(success: bool = True) -> SandboxPrepareResult:
    return SandboxPrepareResult(
        success=success, venv_dir="/w/.venv", python_exe="/w/.venv/bin/python", pip_exe="",
        env_info={"python_version": "Python 3.11"}, install_log="ok",
        install_failed_packages=[], error=None,
    )


def _run(exit_code=1, stdout="", stderr=_IMPORT_ERR, command=None) -> SandboxRunResult:
    return SandboxRunResult(
        exit_code=exit_code, stdout=stdout, stderr=stderr,
        duration_seconds=0.1, timed_out=False, output_truncated=False,
        command=command or ["python", "train.py"],
    )


def _exec_result_import_fail() -> Dict[str, Any]:
    return {
        "success": False, "metrics": {},
        "logs": _IMPORT_ERR + "\n" + ("tail-noise\n" * 200),
        "errors": ["[error_category=import] import 错误（缺包 / 模块路径错误）"],
        "artifacts": [], "runtime_seconds": 0.0, "environment_info": {},
        "step_reconciliation": {}, "budget_truncated": False,
        "metrics_groups": {}, "degraded_credentials": [],
    }


def _coding_state(tmp_path, fix_count: int) -> Dict[str, Any]:
    code_dir = tmp_path / "code"
    code_dir.mkdir(parents=True, exist_ok=True)
    return {
        "code_output_dir": str(code_dir.resolve()),
        "workspace_dir": str(tmp_path),
        "paper_meta": {"arxiv_id": "2403.06402"},
        "reproduction_plan": {"code_strategy": "x", "execution_steps": [], "deliverables": [], "environment": {}},
        "resource_info": {"selected_repo": {"local_path": "/tmp/repo"}},
        "paper_analysis": {},
        "execution_result": _exec_result_import_fail(),
        "fix_loop_count": fix_count,
    }


# ===========================================================================
# CP-1.3-1：log_file_path 子键 + error_category 保留
# ===========================================================================


def test_cp_1_3_1_log_file_path_subkey_and_error_category(tmp_path):
    state = _coding_state(tmp_path, fix_count=1)
    payload = coding_module._build_coding_context(state)

    digest = payload["last_error_summary"]
    assert "log_file_path" in digest, "S7-02 应新增 log_file_path 子键"
    # 编号对齐：fix_count=1 → 读上一轮 round_0.log（off-by-one 锁死）。
    assert digest["log_file_path"].endswith(f"{_EXEC_LOGS_SUBDIR}/round_0.log"), (
        f"fix_count=1 应指向 round_0.log（读上一轮 execution 落盘），实际 {digest['log_file_path']}"
    )
    # error_category 快速提示保留。
    assert digest["error_category"] == "import"
    # payload["fix_round"] 保持 fix_count（不减 1）。
    assert payload["fix_round"] == 1, "fix_round 语义是当前第几次修复，保持 fix_count"


@pytest.mark.parametrize("fix_count,expected_round", [(1, 0), (2, 1), (5, 4)])
def test_cp_1_3_1_off_by_one_matrix(tmp_path, fix_count, expected_round):
    """off-by-one 防回归矩阵：log_file_path 轮号 == fix_count - 1（架构师 R-1/R-2）。"""
    state = _coding_state(tmp_path, fix_count=fix_count)
    payload = coding_module._build_coding_context(state)
    digest = payload["last_error_summary"]
    assert digest["log_file_path"].endswith(f"round_{expected_round}.log")
    assert payload["fix_round"] == fix_count


# ===========================================================================
# CP-1.3-2：端到端可读——落盘 + 路径推导联跑，read_code_file 读到真报错
# ===========================================================================


def test_cp_1_3_2_end_to_end_readable(tmp_path, monkeypatch):
    """execution 落 round_0.log（首跑）→ coding 修复回合推导 log_file_path=round_0.log
    → read_code_file 读到含 No module named 'src' 的日志（AC-S7-05）。"""
    # read_code_file 的路径护栏是 WORKSPACE_DIR 根，把它指到 tmp_path。
    monkeypatch.setattr(config, "WORKSPACE_DIR", tmp_path)
    import core.tools.code_fs_tools as code_fs
    monkeypatch.setattr(code_fs, "WORKSPACE_DIR", tmp_path)

    code_dir = tmp_path / "code"
    code_dir.mkdir(parents=True)

    # 步骤 A：execution 首跑（fix_count=0）落盘 round_0.log。
    path = execution_module._persist_round_log(str(code_dir), 0, _prep(), [_run()])
    assert path is not None and Path(path).exists()

    # 步骤 B：coding 修复回合（fix_count=1）推导 log_file_path。
    state = {
        "code_output_dir": str(code_dir.resolve()),
        "workspace_dir": str(tmp_path),
        "paper_meta": {"arxiv_id": "x"},
        "reproduction_plan": {"code_strategy": "x", "execution_steps": [], "deliverables": [], "environment": {}},
        "resource_info": {}, "paper_analysis": {},
        "execution_result": _exec_result_import_fail(),
        "fix_loop_count": 1,
    }
    payload = coding_module._build_coding_context(state)
    log_file_path = payload["last_error_summary"]["log_file_path"]
    assert log_file_path.endswith("round_0.log")

    # 步骤 C：coder 用 read_code_file 自读该路径 → 读到真报错行。
    read_tool = code_fs.make_read_code_file_tool()
    content = read_tool.invoke({"path": log_file_path})
    assert "No module named 'src'" in content, (
        "read_code_file(log_file_path) 应读到含真报错行的完整日志（端到端可读）"
    )


# ===========================================================================
# CP-1.3-3：AC-S7-07 设计取舍守门（须验红）
# ===========================================================================


def test_cp_1_3_3_stderr_tail_is_guidance_not_truncation(tmp_path):
    """AC-S7-07：stderr_tail 不再是 logs[-2000:] 截断产物，而是固定指引串（不含日志内容）；
    反馈以 log_file_path 为准。注掉落盘 + 路径注入后断言变红（防假绿）。"""
    state = _coding_state(tmp_path, fix_count=1)
    payload = coding_module._build_coding_context(state)
    digest = payload["last_error_summary"]

    # 强断言 1：stderr_tail 是固定指引串（不是 logs 子串）。
    assert digest["stderr_tail"] == _STDERR_TAIL_GUIDANCE
    logs = state["execution_result"]["logs"]
    assert digest["stderr_tail"] not in logs, "stderr_tail 不得再是 logs 截断产物（AC-S7-07）"
    # 强断言 2：反馈以 log_file_path 为准（存在且指向真报错文件）。
    assert digest["log_file_path"] is not None
    assert digest["log_file_path"].endswith("round_0.log")
    # 强断言 3：stderr_tail 不含任何日志真报错内容（把截断决策权收回给 coder）。
    assert "No module named" not in digest["stderr_tail"]


# ===========================================================================
# CP-1.3-4：路径确定性推导——落盘失败/文件不存在 → read 到"文件不存在"退回 errors
# ===========================================================================


def test_cp_1_3_4_missing_file_degrades_to_errors(tmp_path, monkeypatch):
    """log_file_path 指向不存在文件（落盘失败/未落盘）→ read_code_file 读"文件不存在"，
    反馈仍有 errors 摘要不炸（R-S7-4 降级面）。"""
    monkeypatch.setattr(config, "WORKSPACE_DIR", tmp_path)
    import core.tools.code_fs_tools as code_fs
    monkeypatch.setattr(code_fs, "WORKSPACE_DIR", tmp_path)

    code_dir = tmp_path / "code"
    code_dir.mkdir(parents=True)
    # 不落盘（模拟落盘失败）——round_0.log 不存在。

    state = _coding_state(tmp_path, fix_count=1)
    state["code_output_dir"] = str(code_dir.resolve())
    state["workspace_dir"] = str(tmp_path)
    payload = coding_module._build_coding_context(state)
    digest = payload["last_error_summary"]

    log_file_path = digest["log_file_path"]
    assert log_file_path is not None  # 路径仍确定性推导出

    read_tool = code_fs.make_read_code_file_tool()
    content = read_tool.invoke({"path": log_file_path})
    assert "文件不存在" in content or "Error" in content, "未落盘 → read 到文件不存在"
    # 反馈仍保留 errors 摘要（退回 sp6 现状，不炸）。
    assert digest["errors"], "落盘失败 → 退回 errors 摘要"
    assert digest["error_category"] == "import"


def test_cp_1_3_4_none_code_output_dir_returns_none_path(tmp_path):
    """code_output_dir 缺失 → _resolve_round_log_path 返回 None（不炸）。"""
    assert coding_module._resolve_round_log_path(None, 0) is None
    assert coding_module._resolve_round_log_path("", 3) is None


# ===========================================================================
# CP-1.3-5：representative_stderr 正交 + execution 侧 stderr_tail 维持尾部（AA-S7-3）
# ===========================================================================


def test_cp_1_3_5_representative_stderr_untouched(tmp_path):
    """representative_stderr 保恒空 + payload 键结构冻结（S7-02 不触碰，架构 §5.5）。"""
    # interrupt#2 payload 的 representative_stderr 仍从 feedback.representative_stderr 取，
    # NO_METRICS 构造点与 guard 重建点恒为空——S7-02 未改动这些点。
    fb = execution_module._feedback_from_committed_result(
        {"errors": ["[error_category=import] boom"]}
    )
    assert fb.representative_stderr == "", "guard 重建 feedback 的 representative_stderr 恒空"


def test_cp_1_3_5_execution_side_stderr_tail_stays_tail(tmp_path):
    """execution 侧 _build_execution_agent_context 的 stderr_tail 维持尾部（AA-S7-3 正交）
    ——execution agent 无 read_code_file，改路径反使其更瞎，S7-02 只作用于 coding 侧。"""
    logs = "HEAD-of-logs\n" + ("x" * 5000) + "\nTAIL-error-line"
    state = {
        "execution_result": {"errors": ["[error_category=runtime] boom"], "logs": logs},
        "fix_loop_count": 1,
        "reproduction_plan": {"execution_steps": [], "environment": {}},
        "credential_degradations": {},
    }
    ctx = execution_module._build_execution_agent_context(state, "/tmp/wd", {})
    les = ctx["last_error_summary"]
    # execution 侧仍是 stderr_tail 尾部（含 TAIL-error-line），不是指引串、不含 log_file_path。
    assert "stderr_tail" in les
    assert "TAIL-error-line" in les["stderr_tail"], "execution 侧维持 logs 尾部"
    assert "log_file_path" not in les, "execution 侧不注入 log_file_path（AA-S7-3 正交）"
    assert les["stderr_tail"] != _STDERR_TAIL_GUIDANCE, "execution 侧不改为指引串"
