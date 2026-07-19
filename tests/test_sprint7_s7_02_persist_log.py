"""Sprint 7 任务 T-S7-1-2（S7-02，架构 §5.3）：_persist_round_log 落盘 + 主流程接线。

覆盖 dev-plan §5 T-S7-1-2 自测检查点 CP-1.2-1 ~ CP-1.2-6（AC-S7-05 落盘面）：
    - CP-1.2-1 落盘：import 失败现场 → round_{n}.log 存在且含真报错行；
    - CP-1.2-2 错误优先编排：真报错行落在文件头 8000 字符内（尾部为成功步 stdout）；
    - CP-1.2-3 命名确定性：fix_count=0 → round_0.log；=2 → round_2.log（无时间戳/uuid）；
    - CP-1.2-4 mask 口径一致：落盘内容与 execution_result.logs 同脱敏级别（凭证不泄）；
    - CP-1.2-5 落盘兜底不炸：写文件 IO 失败 → try/except 兜底，节点不阻断；
    - CP-1.2-6 guard 命中路径不重落：self-loop 重入（already_committed）不触发 _persist_round_log。

全离线：mock sandbox agent + patch collect_artifacts，零 API 配额。
"""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

import config
from core import secrets_store
from core.state import ExecutionMode

execution_module = importlib.import_module("core.nodes.execution")

from sandbox.local_venv import SandboxPrepareResult, SandboxRunResult  # noqa: E402

NODE_NAME = execution_module.NODE_NAME
_EXEC_LOGS_SUBDIR = execution_module._EXEC_LOGS_SUBDIR


# ---------------------------------------------------------------------------
# fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_sensitive():
    secrets_store._SENSITIVE_VALUES.clear()
    yield
    secrets_store._SENSITIVE_VALUES.clear()


@pytest.fixture(autouse=True)
def _no_artifacts(monkeypatch):
    monkeypatch.setattr(execution_module, "collect_artifacts", lambda *a, **k: [])


def _prep(success: bool = True, install_log: str = "ok") -> SandboxPrepareResult:
    return SandboxPrepareResult(
        success=success, venv_dir="/w/.venv", python_exe="/w/.venv/bin/python", pip_exe="",
        env_info={"python_version": "Python 3.11"}, install_log=install_log,
        install_failed_packages=[], error=None,
    )


def _run(exit_code=0, stdout="", stderr="", command=None) -> SandboxRunResult:
    return SandboxRunResult(
        exit_code=exit_code, stdout=stdout, stderr=stderr,
        duration_seconds=0.1, timed_out=False, output_truncated=False,
        command=command or ["python", "run.py"],
    )


_IMPORT_ERR = "Traceback (most recent call last):\nModuleNotFoundError: No module named 'src'"


# ===========================================================================
# CP-1.2-1：落盘 import 失败现场 → round_{n}.log 存在且含真报错行
# ===========================================================================


def test_cp_1_2_1_persist_import_failure(tmp_path):
    work_dir = str(tmp_path / "code")
    Path(work_dir).mkdir(parents=True)
    prep = _prep()
    runs = [_run(exit_code=1, stderr=_IMPORT_ERR, command=["python", "train.py"])]

    path = execution_module._persist_round_log(work_dir, 0, prep, runs)

    assert path is not None
    log_file = Path(work_dir) / _EXEC_LOGS_SUBDIR / "round_0.log"
    assert log_file.exists(), "round_0.log 应落盘"
    content = log_file.read_text(encoding="utf-8")
    assert "No module named 'src'" in content, "落盘内容应含真报错行"


# ===========================================================================
# CP-1.2-2：错误优先编排——真报错行落在文件头 8000 字符内
# ===========================================================================


def test_cp_1_2_2_error_first_within_8000(tmp_path):
    """尾部为大段成功步 stdout；错误优先编排把真报错前置到文件头 8000 字符内。"""
    work_dir = str(tmp_path / "code")
    Path(work_dir).mkdir(parents=True)
    prep = _prep()
    # 尾部一个成功步产生巨量 stdout（> 8000），真报错在前一个失败步。
    big_ok_stdout = "OK-line\n" * 5000  # ~40000 字符
    runs = [
        _run(exit_code=1, stderr=_IMPORT_ERR, command=["python", "prep.py"]),
        _run(exit_code=0, stdout=big_ok_stdout, command=["python", "train.py"]),
    ]

    path = execution_module._persist_round_log(work_dir, 0, prep, runs)
    content = Path(path).read_text(encoding="utf-8")

    idx = content.find("No module named 'src'")
    assert idx != -1, "真报错行应存在"
    assert idx < 8000, (
        f"真报错行须落在文件头 8000 字符内（错误优先编排），实际位于 {idx}"
    )
    # 头部应是错误摘要区。
    assert content.startswith("===== 错误摘要区")


def test_cp_1_2_2_no_error_no_prefix(tmp_path):
    """全成功步（无非零 exit）→ 无错误摘要区前缀，退回完整时序日志。"""
    work_dir = str(tmp_path / "code")
    Path(work_dir).mkdir(parents=True)
    runs = [_run(exit_code=0, stdout="all good", command=["python", "run.py"])]
    path = execution_module._persist_round_log(work_dir, 0, _prep(), runs)
    content = Path(path).read_text(encoding="utf-8")
    assert not content.startswith("===== 错误摘要区"), "无错误时不应有摘要区前缀"


# ===========================================================================
# CP-1.2-3：命名确定性——round_{fix_count}.log，无时间戳/uuid
# ===========================================================================


@pytest.mark.parametrize("fix_count,expected", [(0, "round_0.log"), (2, "round_2.log"), (5, "round_5.log")])
def test_cp_1_2_3_deterministic_naming(tmp_path, fix_count, expected):
    work_dir = str(tmp_path / "code")
    Path(work_dir).mkdir(parents=True)
    runs = [_run(exit_code=1, stderr=_IMPORT_ERR)]
    path = execution_module._persist_round_log(work_dir, fix_count, _prep(), runs)
    assert Path(path).name == expected, f"fix_count={fix_count} 应产出 {expected}"
    # 无时间戳/uuid：同 fix_count 二次落盘覆盖同名文件（确定性可复现）。
    path2 = execution_module._persist_round_log(work_dir, fix_count, _prep(), runs)
    assert Path(path).name == Path(path2).name


# ===========================================================================
# CP-1.2-4：mask 口径一致——落盘内容与 execution_result.logs 同脱敏级别
# ===========================================================================


def test_cp_1_2_4_mask_parity(tmp_path):
    """注入已知敏感值到 stderr → 落盘文件与 _aggregate_logs 经 mask 后同口径，凭证不泄。"""
    work_dir = str(tmp_path / "code")
    Path(work_dir).mkdir(parents=True)
    token = "ghp_SECRET_s7_persist_token_XYZ"
    secrets_store.register_sensitive_value(token)
    runs = [_run(exit_code=1, stderr=f"auth failed with {token}", command=["git", "clone"])]

    path = execution_module._persist_round_log(work_dir, 0, _prep(), runs)
    content = Path(path).read_text(encoding="utf-8")

    assert token not in content, "落盘内容不得含凭证明文（mask 口径一致）"
    assert "****" in content, "敏感值应被 mask 为 ****"
    # 与 execution_result.logs 的 mask 口径一致（后者也是 mask_value(_aggregate_logs)）。
    masked_state_logs = secrets_store.mask_value(execution_module._aggregate_logs(_prep(), runs)) or ""
    assert token not in masked_state_logs


# ===========================================================================
# CP-1.2-5：落盘兜底不炸——IO 失败 → try/except 兜底返回 None，不抛
# ===========================================================================


def test_cp_1_2_5_persist_io_failure_no_crash(tmp_path, monkeypatch):
    """makedirs 抛 OSError → _persist_round_log 兜底返回 None，不抛异常（R-S7-4）。"""
    work_dir = str(tmp_path / "code")
    Path(work_dir).mkdir(parents=True)
    runs = [_run(exit_code=1, stderr=_IMPORT_ERR)]

    import os as _os
    orig_makedirs = _os.makedirs

    def boom_makedirs(*a, **k):
        raise OSError("disk full / read-only fs")

    monkeypatch.setattr(_os, "makedirs", boom_makedirs)
    # 不抛异常，返回 None。
    result = execution_module._persist_round_log(work_dir, 0, _prep(), runs)
    monkeypatch.setattr(_os, "makedirs", orig_makedirs)
    assert result is None, "落盘失败应兜底返回 None，不阻断节点"


def test_cp_1_2_5_persist_failure_does_not_block_node(tmp_path, monkeypatch):
    """主流程中落盘失败不阻断 execution 节点——节点仍完成边界判定返回 updates。"""
    from core.state import ExecutionMode as _EM

    def boom_persist(*a, **k):
        raise OSError("落盘炸了")

    # 直接让 _persist_round_log 抛（模拟兜底之外的极端），主流程不应崩——
    # 但我们的接线调用点本身不 try，故这里验证 _persist_round_log 内部已兜底。
    # 用真 _persist_round_log + patch makedirs 抛，走真接线路径。
    import os as _os
    monkeypatch.setattr(_os, "makedirs", lambda *a, **k: (_ for _ in ()).throw(OSError("boom")))

    work_dir = str(tmp_path / "code")
    Path(work_dir).mkdir(parents=True)

    def fake_agent(state, wd, plan):
        return execution_module.ExecAgentOutput(
            prep=_prep(), run_results=[_run(exit_code=1, stderr=_IMPORT_ERR)],
            rounds_used=1, llm_calls=0,
        )

    monkeypatch.setattr(execution_module, "_run_execution_agent", fake_agent)
    monkeypatch.setattr(execution_module, "_llm_extract_metrics", lambda *a, **k: ({}, 0))

    state = {
        "llm_config_set": {"default": {"model": "test"}},
        "code_output_dir": work_dir,
        "reproduction_plan": {"execution_steps": [{"command": "python train.py"}], "environment": {}},
        "paper_analysis": {"metrics": []},
        "execution_mode": _EM.FULL,
        "node_errors": [], "degraded_nodes": [], "fix_loop_history": [],
        "fix_loop_count": 0, "retry_budget_remaining": 40,
        "_dev_loop_llm_calls": 0, "_dev_loop_route": None,
        "execution_result": None, "current_step": "coding",
    }
    # 不抛异常 → 节点完成。
    out = execution_module.execution(state)
    assert "execution_result" in out, "落盘失败不应阻断节点"


# ===========================================================================
# CP-1.2-1b：主流程接线——execution() 首次真跑回合经步骤 5-6 间接线落盘（须验红：注掉接线断言变红）
# ===========================================================================


def test_cp_1_2_1b_mainflow_wiring_persists(tmp_path, monkeypatch):
    """execution() 首次真跑（already_committed=False）→ 主流程步骤 5-6 间接线
    调 _persist_round_log，round_0.log 落盘含真报错行。注掉接线断言必须变红。"""
    work_dir = str(tmp_path / "code")
    Path(work_dir).mkdir(parents=True)

    def fake_agent(state, wd, plan):
        return execution_module.ExecAgentOutput(
            prep=_prep(), run_results=[_run(exit_code=1, stderr=_IMPORT_ERR, command=["python", "train.py"])],
            rounds_used=1, llm_calls=0,
        )

    monkeypatch.setattr(execution_module, "_run_execution_agent", fake_agent)
    monkeypatch.setattr(execution_module, "_llm_extract_metrics", lambda *a, **k: ({}, 0))

    state = {
        "llm_config_set": {"default": {"model": "test"}},
        "code_output_dir": work_dir,
        "reproduction_plan": {"execution_steps": [{"command": "python train.py"}], "environment": {}},
        "paper_analysis": {"metrics": []},
        "execution_mode": ExecutionMode.FULL,
        "node_errors": [], "degraded_nodes": [], "fix_loop_history": [],
        "fix_loop_count": 0, "retry_budget_remaining": 40,
        "_dev_loop_llm_calls": 0, "_dev_loop_route": None,
        "execution_result": None, "current_step": "coding",
    }
    execution_module.execution(state)

    log_file = Path(work_dir) / _EXEC_LOGS_SUBDIR / "round_0.log"
    assert log_file.exists(), "主流程接线应在首次真跑回合落盘 round_0.log"
    assert "No module named 'src'" in log_file.read_text(encoding="utf-8")


# ===========================================================================
# CP-1.2-6：guard 命中路径不重落——已落盘回合 self-loop 重入不触发 _persist_round_log
# ===========================================================================


def test_cp_1_2_6_guard_reentry_no_repersist(tmp_path, monkeypatch):
    """already_committed 路径（guard 命中）不重跑 sandbox、不调 _persist_round_log。"""
    work_dir = str(tmp_path / "code")
    Path(work_dir).mkdir(parents=True)

    persist_cnt = {"n": 0}
    real_persist = execution_module._persist_round_log

    def counting_persist(*a, **k):
        persist_cnt["n"] += 1
        return real_persist(*a, **k)

    monkeypatch.setattr(execution_module, "_persist_round_log", counting_persist)

    def forbidden_agent(state, wd, plan):
        raise AssertionError("guard 命中路径不得调用 _run_execution_agent")

    monkeypatch.setattr(execution_module, "_run_execution_agent", forbidden_agent)

    # 构造 guard 命中入口 state：_dev_loop_route=await + execution_result 非空。
    prev_result = {
        "success": False, "metrics": {},
        "logs": "prev logs", "errors": ["[error_category=import] ModuleNotFoundError"],
        "artifacts": [], "runtime_seconds": 0.0, "environment_info": {},
        "step_reconciliation": {}, "budget_truncated": False,
        "metrics_groups": {}, "degraded_credentials": [],
    }
    state = {
        "llm_config_set": {"default": {"model": "test"}},
        "code_output_dir": work_dir,
        "reproduction_plan": {"execution_steps": [{"command": "python train.py"}], "environment": {}},
        "paper_analysis": {"metrics": []},
        "execution_mode": ExecutionMode.FULL,
        "node_errors": [], "degraded_nodes": [], "fix_loop_history": [],
        "fix_loop_count": 1,
        "retry_budget_remaining": 40,
        "_dev_loop_llm_calls": 0,
        "_dev_loop_route": execution_module._ROUTE_AWAIT_INTERRUPT,
        "execution_result": prev_result,
        "current_step": "execution",
    }
    # guard 命中路径会走到函数体 interrupt()——无 checkpointer 时 interrupt 抛控制流异常。
    # 只捕获控制流类异常，AssertionError（forbidden_agent 被误调）应直通冒泡。
    from langgraph.errors import GraphBubbleUp
    try:
        execution_module.execution(state)
    except GraphBubbleUp:
        pass  # interrupt() 控制流，预期
    except AssertionError:
        raise  # forbidden_agent 被误调 → guard 未命中，直通失败
    except Exception:
        pass  # 无 checkpointer 下 interrupt 的其它包装异常，非本 CP 关注点

    assert persist_cnt["n"] == 0, "guard 命中路径不得触发 _persist_round_log（日志上轮已落）"
