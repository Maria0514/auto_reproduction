"""BUG-S7-11-01 修复守门（@全栈开发代理，2026-08-01）——完成度分母改为「可执行步数」。

缺陷：`_reconcile_steps` 的 `planned = len(plan_steps)` 是**原始步数**，
`_completion_insufficient` 直接拿它做分母。计划里只要有一条 agent 无从执行的步骤
（无 `command` 键 / 空串 / 纯 `cd`），它**永远进不了分子** ⇒ 即便 agent 完全照做、
全 exit 0、指标齐全、诚实自报 `step_index`，也恒判 INCOMPLETE、烧满
`MAX_FIX_LOOP_COUNT` 轮修复、推到 interrupt#2；而下一轮 coding 变不出「查看图表」
的命令 ⇒ **循环无解**。dev-plan §49.2 第 6 条与 R-S7-59 正文写的都是
`planned_actionable`（**actionable**），实现落成了 `planned` —— 设计对、实现错。

本文件守的是修复之后**不能再退回去**的四件事：
    ① 分母判据（`_is_actionable_step`）确定性、与归属规则②同一取数点；
    ② 分母**只有一个取数点**（`core.state.completion_denominator`）——判定层与展示层
       共用同一个函数对象，杜绝 `auto_fixable` 那种双真相源（§56.3 P-51 的教训）；
    ③ **判定与报告不得分叉**：success=True 时报告绝不出现「没跑完」横幅
       （CP-7.9-3 明令该组合为零），且「已完成 N/M」的 M 与判定分母同一个数；
    ④ 旧 checkpoint（无 `planned_actionable` 键）回落 `planned` ⇒ 报告字节零变化。

全离线（mock agent + tmp_path），零 API 配额。
"""

from __future__ import annotations

import copy
import importlib
from pathlib import Path
from typing import Any, Dict, List

import pytest

import config
from core import secrets_store
from core.state import ExecutionMode

execution_module = importlib.import_module("core.nodes.execution")
reporting_module = importlib.import_module("core.nodes.reporting")
state_module = importlib.import_module("core.state")

from core.nodes.execution import execution  # noqa: E402
from core.nodes.reporting import reporting  # noqa: E402
from sandbox.local_venv import SandboxPrepareResult, SandboxRunResult  # noqa: E402

_METRICS_LINE = '<METRICS>{"acc": 0.9}</METRICS>'


# ---------------------------------------------------------------------------
# fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_process_sensitive_set():
    secrets_store._SENSITIVE_VALUES.clear()
    yield
    secrets_store._SENSITIVE_VALUES.clear()


@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    ws = tmp_path / "workspace"
    ws.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(config, "WORKSPACE_DIR", ws)
    monkeypatch.setattr(reporting_module, "WORKSPACE_DIR", ws)
    monkeypatch.setattr(execution_module, "collect_artifacts", lambda *a, **k: [])
    return ws


def _prep() -> SandboxPrepareResult:
    return SandboxPrepareResult(
        success=True, venv_dir="/w/.venv", python_exe="/w/.venv/bin/python", pip_exe="",
        env_info={"python_version": "Python 3.11"},
        install_log="ok", install_failed_packages=[], error=None,
    )


def _run(command: List[str], exit_code: int = 0, stdout: str = "") -> SandboxRunResult:
    return SandboxRunResult(
        exit_code=exit_code, stdout=stdout, stderr="",
        duration_seconds=0.1, timed_out=False,
        output_truncated=False, command=list(command),
    )


def _patch_agent(monkeypatch, runs, ledger) -> None:
    out = execution_module.ExecAgentOutput(
        prep=_prep(), run_results=list(runs), rounds_used=2, llm_calls=2,
        step_ledger=list(ledger),
    )
    monkeypatch.setattr(
        execution_module, "_run_execution_agent", lambda state, wd, plan: out,
    )


def _exec_state(workspace: Path, steps: List[Any]) -> Dict[str, Any]:
    arxiv_id = "2604.01687"
    code_dir = workspace / arxiv_id / "code"
    code_dir.mkdir(parents=True, exist_ok=True)
    return {
        "llm_config_set": {"default": {"model": "test"}},
        "workspace_dir": str(workspace),
        "code_output_dir": str(code_dir.resolve()),
        "paper_meta": {"arxiv_id": arxiv_id, "title": "T"},
        "reproduction_plan": {
            "execution_steps": steps,
            "environment": {"dependencies": []},
            "expected_results": [],
            "deliverables": [],
        },
        "paper_analysis": {"metrics": [], "baseline_results": {}},
        "execution_mode": ExecutionMode.FULL,
        "node_errors": [],
        "degraded_nodes": [],
        "fix_loop_history": [],
        "fix_loop_count": 0,
        "retry_budget_remaining": 40,
        "_dev_loop_llm_calls": 0,
        "_dev_loop_route": None,
        "execution_result": None,
        "simulation_notice": None,
        "user_fix_decision": None,
        "current_step": "coding",
    }


def _render(state: Dict[str, Any]) -> str:
    out = reporting(state)
    return Path(out["report_path"]).read_text(encoding="utf-8")


# ===========================================================================
# ① 分母判据：确定性 + 与归属规则②同一取数点
# ===========================================================================


def test_actionable_predicate_shares_one_parser_with_attribution_rule_2():
    """`_is_actionable_step` 必须是 `_plan_step_keys` 的布尔投影（同一把尺子）。

    这是修复正确性的地基：**进得了分母**与**进得了分子**若用两套解析，就会重新出现
    "某步永远进不了分子却算在分母里"的不可达 success。任何人把判据另写一遍（哪怕逻辑
    等价）都会让这条断言失守。
    """
    samples = [
        {"command": "python train.py"},
        {"command": "cd repo && python run.py"},
        {"command": "cd ."},
        {"command": ""},
        {"step_name": "看图"},
        "python x.py",
        None,
    ]
    for step in samples:
        assert execution_module._is_actionable_step(step) is bool(
            execution_module._plan_step_keys(step)
        ), f"判据与归属解析分叉：{step!r}"


def test_reconcile_exposes_both_numbers_and_never_confuses_them():
    """`planned`（原始步数）与 `planned_actionable`（分母）两套编号必须并存且不混用。

    ⚠ 越界丢弃逻辑用的是**原始步数**：agent 自报的 `step_index` 指向它看到的那份计划
    的原始步序，剔除不可执行步骤只改分母、不重排步序。若有人把上界换成 actionable 数，
    下面这条"末位步骤的合法自报"会被误丢，`completed` 掉到 1 ⇒ 本条当场红。
    """
    steps = [
        {"step_name": "看图"},                                  # idx 0：不可执行
        {"step_name": "更多说明", "command": "cd ."},            # idx 1：不可执行
        {"step_name": "训练", "command": "python train.py"},     # idx 2：可执行
    ]
    runs = [_run(["python", "train.py"])]
    recon = execution_module._reconcile_steps(
        steps, runs, [(2, ["python", "train.py"], 0)],
    )
    assert recon["planned"] == 3
    assert recon["planned_actionable"] == 1
    assert recon["completed"] == 1, "末位步骤（下标 2）的合法自报必须仍被采信"
    assert recon["unexecuted_steps"] == []
    assert execution_module._completion_insufficient(recon) is False


def test_out_of_range_declaration_still_uses_raw_step_count(caplog):
    """越界判据仍以原始步数为上界：下标 2 在 3 步计划里合法，**不得**被 actionable 丢掉。"""
    steps = [
        {"step_name": "看图"},
        {"step_name": "看图2"},
        {"step_name": "训练", "command": "python train.py"},
    ]
    recon = execution_module._reconcile_steps(
        steps, [_run(["python", "other.py"])], [(2, ["python", "other.py"], 0)],
    )
    # 声明归属生效（否则该命令会落进 extra_commands）。
    assert recon["extra_commands"] == []
    assert recon["completed"] == 1


# ===========================================================================
# ② 分母只有一个取数点（反 P-51 式双真相源）
# ===========================================================================


def test_completion_denominator_is_one_single_function_object():
    """判定层与展示层引用的必须是**同一个函数对象**，不是两份等价实现。

    §56.3 P-51 的教训：`_apply_*` 硬编码 `auto_fixable=True` 与 `AUTO_FIXABLE` 集合
    推导构成双真相源，摘掉集合后两条路径判两样、验红手法整个失效。分母是同型高危点
    （判定说"没跑完"、报告显示另一个分母 ⇒ 用户看到自相矛盾），此处一次性焊死。
    """
    assert execution_module._completion_denominator is state_module.completion_denominator
    assert reporting_module.completion_denominator is state_module.completion_denominator


@pytest.mark.parametrize(
    "recon, expected",
    [
        ({"planned": 5, "planned_actionable": 3}, 3),
        ({"planned": 5}, 5),                       # 旧 checkpoint 回落
        ({"planned_actionable": 0, "planned": 4}, 0),
        ({"planned": 5, "planned_actionable": True}, 5),   # bool 不是 int
        ({"planned": "5", "planned_actionable": None}, None),
        ({}, None),
        (None, None),
        ("不是 dict", None),
    ],
)
def test_completion_denominator_truth_table(recon, expected):
    assert state_module.completion_denominator(recon) == expected


# ===========================================================================
# ③ 判定与报告不分叉（CP-7.9-3：success=True ∧「没跑完」横幅 的组合必须为零）
# ===========================================================================


def test_success_report_has_no_incomplete_banner_and_matching_denominator(
    workspace, monkeypatch,
):
    """端到端：计划 2 步（1 步不可执行）+ agent 把可执行的跑完 ⇒ 判成功、报告不矛盾。"""
    steps = [
        {"step_name": "训练", "command": "python train.py"},
        {"step_name": "查看 outputs/ 下的图表确认可视化正常"},
    ]
    _patch_agent(monkeypatch, [_run(["python", "train.py"], stdout=_METRICS_LINE)],
                 [(0, ["python", "train.py"], 0)])
    state = _exec_state(workspace, steps)
    state.update(execution(state))

    er = state["execution_result"]
    assert er["success"] is True
    assert er["step_reconciliation"]["planned"] == 2
    assert er["step_reconciliation"]["planned_actionable"] == 1

    md = _render(state)
    # 判定与展示同分母：不得出现旧的 1/2。
    assert "已完成 1/1 步" in md
    assert "已完成 1/2 步" not in md
    # 分母与"计划共几步"对不上时必须给出理由，别让用户自己猜。
    assert "计划 2 步" in md
    assert "其中 1 步没有可执行的命令、不计入完成度" in md
    # CP-7.9-3 红线：判成功就不许再挂"没跑完"的横幅。
    assert "执行不完整" not in md
    assert "计划步骤未全部执行完成" not in md


def test_incomplete_report_denominator_is_actionable_too(workspace, monkeypatch):
    """阴性对照：真少跑一步 ⇒ 判不成功，横幅与对账节的分母同样是 actionable。"""
    steps = [
        {"step_name": "训练", "command": "python train.py"},
        {"step_name": "评测", "command": "python eval.py"},
        {"step_name": "查看图表"},
    ]
    _patch_agent(monkeypatch, [_run(["python", "train.py"], stdout=_METRICS_LINE)],
                 [(0, ["python", "train.py"], 0)])
    state = _exec_state(workspace, steps)
    state.update(execution(state))

    er = state["execution_result"]
    assert er["success"] is False
    assert er["step_reconciliation"]["planned_actionable"] == 2
    # 给 coder 的反馈文案也走同一分母（不是 1/3）。
    assert "已跑完 1/2 步" in er["errors"][0]
    # 不可执行步骤不进"未执行清单"（coder 变不出它的命令，列出来是伪修复目标）。
    assert [s["step_name"] for s in er["step_reconciliation"]["unexecuted_steps"]] == ["评测"]

    md = _render(state)
    assert "已完成 1/2 步" in md
    assert "计划 3 步" in md
    assert "执行不完整" in md
    assert "计划步骤未全部执行完成（已完成 1/2 步）" in md


# ===========================================================================
# ④ 旧 checkpoint 回落：报告字节零变化
# ===========================================================================


_LEGACY_RECON = {
    "planned": 13,
    "executed": 8,
    "completed": 8,
    "unexecuted_steps": [{"index": i, "step_name": f"步骤_{i}"} for i in range(8, 13)],
    "extra_commands": [],
    "attribution_unavailable": False,
}


def test_legacy_checkpoint_without_actionable_key_renders_identically(workspace):
    """无 `planned_actionable` 键的旧快照 ⇒ 回落 `planned`，渲染与修复前一模一样。

    回落是**保守行为**（退回修复前口径），不是新语义；旧 checkpoint 兼容 R-6。
    """
    state = _exec_state(workspace, [])
    state["execution_result"] = {
        "success": False, "metrics": {}, "logs": "", "errors": [], "artifacts": [],
        "runtime_seconds": 1.0, "environment_info": {},
        "step_reconciliation": copy.deepcopy(_LEGACY_RECON),
        "budget_truncated": False, "metrics_groups": {}, "degraded_credentials": [],
    }
    md = _render(state)
    assert "已完成 8/13 步" in md
    assert "计划 13 步" in md
    # 两数相等 ⇒ 不得冒出多余的解释性小句。
    assert "不计入完成度" not in md
