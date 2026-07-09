"""Sprint 5 任务 T-S5-2-6（S5-10）：多组指标解析 + env_info 回读修复 + ExecutionResult 构造点补齐。

覆盖 dev-plan 批次 2 任务 T-S5-2-6 自测检查点：
    - CP-2.6-1 固化 fixture 三组解析全对齐（evoskills_smoke / baselines/no_skill /
      baselines/self_generated），组内 pass_rate 等顶层字段收编正确（AC-S5-20 解析部分）；
      并经 execution() 主路径 e2e 断言 metrics_groups 落 ExecutionResult 且 <METRICS>
      主通道语义零改动；
    - CP-2.6-2 容错：损坏 JSON / 非 dict 顶层 / 深层嵌套只收顶层——容忍 + WARNING
      不炸；无 outputs 目录 → {}；str 值脱敏出口自查；
    - CP-2.6-3 env_info 回读：mock prepare ToolMessage 带 env_info → 重建后
      key_packages 非空（AC-S5-20 environment 部分）；失败 ToolMessage 过滤仍生效
      （BUG-S1-03 范式自查）；
    - CP-2.6-4 两构造点（主路径 + work_dir 缺失降级路径）4 新键齐全断言；旧
      checkpoint 快照无新键 .get() 防御读通（R-5/R-6）。

CP-2.6-1 使用测试工程师已固化的 fixture：
    tests/fixtures/regression_2604_01687/code/outputs/**/summary.json
（三文件 md5 已与 workspace/2604.01687 源比对一致；本文件对 fixture 只读消费。）

全离线（mock agent + tmp_path / 固化 fixture），零 API 配额。
"""

from __future__ import annotations

import importlib
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest
from langchain_core.messages import ToolMessage

import config
from core import secrets_store
from core.state import ExecutionMode

execution_module = importlib.import_module("core.nodes.execution")

from core.nodes.execution import execution  # noqa: E402  # 常量走 execution_module（callable 遮蔽陷阱）
from sandbox.local_venv import SandboxPrepareResult, SandboxRunResult  # noqa: E402

ExecAgentOutput = execution_module.ExecAgentOutput

_TOKEN = "ghp_SECRET_t26_token_1234567890"

# 固化 fixture（测试工程师落盘，md5 已验证与 workspace 源一致；只读消费）。
_FIXTURE_WORK_DIR = (
    Path(__file__).parent / "fixtures" / "regression_2604_01687" / "code"
)

# ExecutionResult 契约键集合：sp3 7 键 + sp5 4 新键恰为 11 键（与 test_cp_e3_5 对齐）。
_EXPECTED_RESULT_KEYS = {
    "success", "metrics", "logs", "errors", "artifacts",
    "runtime_seconds", "environment_info", "step_reconciliation",
    "budget_truncated", "metrics_groups", "degraded_credentials",
}


# ---------------------------------------------------------------------------
# fixtures / helpers（沿用 test_sprint4_e3 约定）
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_process_sensitive_set():
    secrets_store._SENSITIVE_VALUES.clear()
    yield
    secrets_store._SENSITIVE_VALUES.clear()


@pytest.fixture(autouse=True)
def secrets_workspace(tmp_path, monkeypatch):
    """`.secrets` / mask 落点隔离到 tmp_path（secrets_store 动态读 config.WORKSPACE_DIR）。"""
    ws = tmp_path / "workspace"
    ws.mkdir()
    monkeypatch.setattr(config, "WORKSPACE_DIR", ws)
    return ws


@pytest.fixture(autouse=True)
def _no_artifacts(monkeypatch):
    monkeypatch.setattr(execution_module, "collect_artifacts", lambda *a, **k: [])


def _prep(env_info: Optional[Dict[str, str]] = None) -> SandboxPrepareResult:
    return SandboxPrepareResult(
        success=True, venv_dir="/w/.venv", python_exe="/w/.venv/bin/python", pip_exe="",
        env_info=env_info if env_info is not None else {"python_version": "Python 3.11"},
        install_log="ok", install_failed_packages=[], error=None,
    )


def _run(exit_code: int = 0, stdout: str = "") -> SandboxRunResult:
    return SandboxRunResult(
        exit_code=exit_code, stdout=stdout, stderr="",
        duration_seconds=0.1, timed_out=False,
        output_truncated=False, command=["python", "x.py"],
    )


def _agent_out(
    prep: Optional[SandboxPrepareResult],
    runs: List[SandboxRunResult],
    rounds: int = 2,
) -> ExecAgentOutput:
    return ExecAgentOutput(
        prep=prep, run_results=runs, rounds_used=rounds, llm_calls=rounds,
    )


def _base_state(**overrides: Any) -> Dict[str, Any]:
    state: Dict[str, Any] = {
        "llm_config_set": {"default": {"model": "test"}},
        "code_output_dir": "/tmp/t26-workdir",
        "reproduction_plan": {
            "execution_steps": [{"command": "python x.py"}],
            "environment": {"dependencies": ["numpy"]},
        },
        "paper_analysis": {"metrics": []},
        "execution_mode": ExecutionMode.FULL,
        "node_errors": [],
        "degraded_nodes": [],
        "fix_loop_history": [],
        "fix_loop_count": 0,
        "retry_budget_remaining": 40,
        "_dev_loop_llm_calls": 0,
        "_dev_loop_route": None,
        "execution_result": None,
        "current_step": "coding",
    }
    state.update(overrides)
    return state


def _patch_agent(monkeypatch, out: ExecAgentOutput) -> Dict[str, int]:
    cnt = {"agent": 0}

    def fake_agent(state, work_dir, plan):
        cnt["agent"] += 1
        return out

    monkeypatch.setattr(execution_module, "_run_execution_agent", fake_agent)
    return cnt


def _prep_tool_message(payload: Dict[str, Any], tool_call_id: str = "call-1") -> ToolMessage:
    """按 P6 工具真实序列化纪律构造 prepare_environment ToolMessage。"""
    return ToolMessage(
        content=json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str),
        name=execution_module._PREPARE_TOOL_NAME,
        tool_call_id=tool_call_id,
    )


# ===========================================================================
# CP-2.6-1 固化 fixture 三组解析全对齐（AC-S5-20 解析部分）
# ===========================================================================


def test_cp_2_6_1_fixture_three_groups_aligned():
    """固化 fixture 直跑纯函数：三组全对齐，组内顶层字段收编正确。"""
    assert _FIXTURE_WORK_DIR.is_dir(), (
        f"固化 fixture 缺失（应由测试工程师落盘）: {_FIXTURE_WORK_DIR}"
    )
    groups = execution_module._collect_grouped_metrics(str(_FIXTURE_WORK_DIR))

    # 组名 = 相对 outputs/ 的父目录 POSIX 路径，三组恰为精确集合。
    assert set(groups.keys()) == {
        "evoskills_smoke", "baselines/no_skill", "baselines/self_generated",
    }

    smoke = groups["evoskills_smoke"]
    assert smoke["pass_rate"] == pytest.approx(2 / 3)
    assert smoke["num_tasks"] == 3
    assert smoke["experiment_name"] == "evoskills_smoke"
    assert smoke["mean_oracle_score"] == pytest.approx(0.9666666666666667)

    no_skill = groups["baselines/no_skill"]
    assert no_skill["pass_rate"] == 0.0
    assert no_skill["baseline_type"] == "no_skill"
    assert no_skill["mean_score"] == pytest.approx(0.06666666666666667)

    self_gen = groups["baselines/self_generated"]
    assert self_gen["pass_rate"] == pytest.approx(2 / 3)
    assert self_gen["baseline_type"] == "self_generated"
    assert self_gen["mean_score"] == pytest.approx(0.9666666666666667)


def test_cp_2_6_1_e2e_metrics_groups_and_main_channel_intact(monkeypatch, tmp_path):
    """execution() 主路径 e2e：metrics_groups 落 ExecutionResult，且 <METRICS>
    三档主通道语义零改动（metrics 仍来自 stdout 结构化标签，不混入分组数据）。"""
    work_dir = tmp_path / "code"
    grp = work_dir / "outputs" / "exp_a"
    grp.mkdir(parents=True)
    (grp / "summary.json").write_text(
        json.dumps({"pass_rate": 0.5, "num_tasks": 4}), encoding="utf-8",
    )
    _patch_agent(monkeypatch, _agent_out(
        _prep(), [_run(exit_code=0, stdout='<METRICS>{"accuracy": 0.91}</METRICS>')],
    ))
    out = execution(_base_state(code_output_dir=str(work_dir)))
    er = out["execution_result"]

    assert er["success"] is True
    assert er["metrics"] == {"accuracy": 0.91}, "主通道 metrics 语义零改动"
    assert er["metrics_groups"] == {"exp_a": {"pass_rate": 0.5, "num_tasks": 4}}


# ===========================================================================
# CP-2.6-2 容错：损坏 JSON / 非 dict 顶层 / 深层只收顶层 / 无 outputs 目录
# ===========================================================================


def test_cp_2_6_2_tolerates_corrupt_nondict_and_collects_top_level_only(tmp_path, caplog):
    outputs = tmp_path / "outputs"
    good = outputs / "good"
    good.mkdir(parents=True)
    good.joinpath("summary.json").write_text(json.dumps({
        "pass_rate": 0.5,
        "converged": True,
        "name": "exp",
        "nested": {"inner_metric": 1.0},   # 深层嵌套：只收顶层，跳过
        "arr": [1, 2, 3],                  # list：跳过
        "none_v": None,                    # None：跳过
        "long_str": "x" * 500,             # 超长 str：跳过
    }), encoding="utf-8")
    broken = outputs / "broken"
    broken.mkdir()
    broken.joinpath("summary.json").write_text("{not valid json", encoding="utf-8")
    toplist = outputs / "toplist"
    toplist.mkdir()
    toplist.joinpath("summary.json").write_text("[1, 2, 3]", encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger="core.nodes.execution"):
        groups = execution_module._collect_grouped_metrics(str(tmp_path))

    # 不炸 + 只留有效组；嵌套/list/None/超长 str 不进组内字段。
    assert set(groups.keys()) == {"good"}
    assert groups["good"] == {"pass_rate": 0.5, "converged": True, "name": "exp"}

    # 损坏 JSON 与非 dict 顶层各一条 WARNING（非静默吞错）。
    messages = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("broken" in m for m in messages), "损坏 JSON 应有 WARNING"
    assert any("toplist" in m for m in messages), "顶层非 dict 应有 WARNING"


def test_cp_2_6_2_missing_outputs_dir_and_empty_work_dir(tmp_path):
    assert execution_module._collect_grouped_metrics(str(tmp_path)) == {}
    assert execution_module._collect_grouped_metrics("") == {}


def test_cp_2_6_2_str_value_masked(tmp_path):
    """脱敏出口自查：组内 str 值内嵌哨兵 token → 落 state 前 mask。"""
    secrets_store.register_sensitive_value(_TOKEN)
    grp = tmp_path / "outputs" / "leaky"
    grp.mkdir(parents=True)
    grp.joinpath("summary.json").write_text(
        json.dumps({"note": f"token={_TOKEN}", "pass_rate": 1.0}), encoding="utf-8",
    )
    groups = execution_module._collect_grouped_metrics(str(tmp_path))
    dumped = json.dumps(groups, ensure_ascii=False, default=str)
    assert _TOKEN not in dumped, "metrics_groups 落 state 不得含哨兵 token 明文"
    assert "****" in groups["leaky"]["note"]
    assert groups["leaky"]["pass_rate"] == 1.0


# ===========================================================================
# CP-2.6-3 env_info 回读重建（AC-S5-20 environment 部分 + BUG-S1-03 范式自查）
# ===========================================================================


def test_cp_2_6_3_env_info_rebuilt_key_packages_nonempty():
    """P6 payload 带 env_info → 回读重建后 key_packages 非空（恒空根因修复）。"""
    msgs = [_prep_tool_message({
        "success": True,
        "python_exe": "/w/.venv/bin/python",
        "venv_dir": "/w/.venv",
        "install_failed_packages": [],
        "env_info": {
            "python_version": "Python 3.11.9",
            "key_packages": "numpy==1.26.0, torch==2.3.0",
        },
        "error": None,
    })]
    preps = execution_module._rebuild_prep_results_from_messages(msgs)
    assert len(preps) == 1
    assert preps[0].env_info["key_packages"] == "numpy==1.26.0, torch==2.3.0"
    assert preps[0].env_info["python_version"] == "Python 3.11.9"

    # 回读 prep 经主构造点 → environment_info 随 exec_result 落盘非空（消费面闭环）。
    er = execution_module._build_execution_result(
        preps[0], [_run(exit_code=0)],
        execution_module.ExecutionFeedback(
            execution_module.ErrorCategory.NONE, False, "", "", "",
        ),
        {"accuracy": 0.9}, "/tmp/nonexistent-t26",
    )
    assert er["environment_info"].get("key_packages") == "numpy==1.26.0, torch==2.3.0"


def test_cp_2_6_3_env_info_non_dict_tolerated():
    """env_info 非 dict（畸形 payload）→ 空占位容忍，不炸。"""
    msgs = [_prep_tool_message({
        "success": True, "python_exe": "/py", "venv_dir": "/v",
        "install_failed_packages": [], "env_info": "oops-not-a-dict", "error": None,
    })]
    preps = execution_module._rebuild_prep_results_from_messages(msgs)
    assert len(preps) == 1
    assert preps[0].env_info == {}


def test_cp_2_6_3_failed_tool_message_still_filtered(caplog):
    """BUG-S1-03 范式自查：失败前缀 / tool_error ToolMessage 仍被过滤，
    且存在目标 ToolMessage 但零成功记录时打 WARNING（禁静默吞错）。"""
    msgs = [
        ToolMessage(
            content="Error in prepare_environment: boom",
            name=execution_module._PREPARE_TOOL_NAME, tool_call_id="c1",
        ),
        _prep_tool_message(
            {"tool_error": True, "error": "SandboxCreationError: x", "success": False},
            tool_call_id="c2",
        ),
    ]
    with caplog.at_level(logging.WARNING, logger="core.nodes.execution"):
        preps = execution_module._rebuild_prep_results_from_messages(msgs)
    assert preps == []
    assert any(
        execution_module._PREPARE_TOOL_NAME in r.getMessage()
        for r in caplog.records if r.levelno >= logging.WARNING
    ), "存在目标 ToolMessage 但零成功记录必须 WARNING"


# ===========================================================================
# CP-2.6-4 两构造点 4 新键齐全 + 旧 checkpoint 快照防御读通（R-5/R-6）
# ===========================================================================


def test_cp_2_6_4_main_constructor_all_new_keys(monkeypatch, tmp_path):
    """主构造点（_build_execution_result 经 execution() 主路径）：11 键恰为精确集合。"""
    work_dir = tmp_path / "code"
    work_dir.mkdir()
    _patch_agent(monkeypatch, _agent_out(
        _prep(), [_run(exit_code=0, stdout='<METRICS>{"acc": 0.9}</METRICS>')],
    ))
    out = execution(_base_state(code_output_dir=str(work_dir)))
    er = out["execution_result"]

    assert set(er.keys()) == _EXPECTED_RESULT_KEYS
    assert isinstance(er["step_reconciliation"], dict)
    assert isinstance(er["budget_truncated"], bool)
    assert er["metrics_groups"] == {}  # 无 outputs 目录 → 空 dict 默认
    assert er["degraded_credentials"] == []


def test_cp_2_6_4_degraded_path_all_new_keys(monkeypatch):
    """降级路径构造点（work_dir 缺失，不进 sandbox）：4 新键齐全且为空默认。"""
    cnt = _patch_agent(monkeypatch, _agent_out(_prep(), []))
    out = execution(_base_state(code_output_dir=None))
    er = out["execution_result"]

    assert cnt["agent"] == 0, "降级路径不得进 sandbox agent"
    assert er["success"] is False
    assert set(er.keys()) == _EXPECTED_RESULT_KEYS
    assert er["step_reconciliation"] == {}
    assert er["budget_truncated"] is False
    assert er["metrics_groups"] == {}
    assert er["degraded_credentials"] == []


def test_cp_2_6_4_old_checkpoint_snapshot_defensive_read(monkeypatch):
    """旧 checkpoint 快照（sp3 7 键，无 sp5 新键）经 guard 命中路径复用：
    .get() 防御读通、不炸、不重算（零 sandbox 调用）、原样透传。"""
    old_snapshot = {
        "success": True, "metrics": {"acc": 0.9}, "logs": "", "errors": [],
        "artifacts": [], "runtime_seconds": 1.0, "environment_info": {},
    }
    cnt = _patch_agent(monkeypatch, _agent_out(_prep(), []))
    out = execution(_base_state(
        _dev_loop_route="await_dev_loop_interrupt",
        execution_result=dict(old_snapshot),
    ))

    assert cnt["agent"] == 0, "guard 命中不得重跑 sandbox"
    er = out["execution_result"]
    assert er == old_snapshot, "旧快照原样复用（guard 路径零重算，不补写新键）"
    # 下游消费口径：新键一律 .get() 防御读，旧快照读得到默认语义。
    assert er.get("metrics_groups") is None
    assert (er.get("metrics_groups") or {}) == {}
    assert bool(er.get("budget_truncated")) is False
    assert list(er.get("degraded_credentials") or []) == []
