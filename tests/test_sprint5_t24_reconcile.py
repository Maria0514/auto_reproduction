"""Sprint 5 任务 T-S5-2-4（S5-06）：步骤对账——台账 step_index 消费 + _reconcile_steps。

覆盖 dev-plan §批次2 任务 T-S5-2-4 自测检查点：
    - CP-2.4-1 13 步计划 / 8 步执行 → planned=13 / executed=8 / completed 与
      exit_code 一致 / unexecuted_steps 5 条含 index+step_name（AC-S5-10 对账部分）；
      兼验 degraded_credentials 同点快照（AC-S5-03 第②落点）；
    - CP-2.4-2 归属三级各证：step_index 优先 / 越界丢弃+WARNING / 无标签归一匹配
      兜底 / agent 改写命令（改路径补参）经归一仍归属 / 完全不匹配入 extra_commands；
      另证 effective runs 口径（同命令最后一次定完成）；
    - CP-2.4-3 attribution_unavailable 保守断言（R-2）：全零归属非空台账 →
      unexecuted_steps 置空（下游 incomplete_execution 规则自然不点火）、原始命令
      如实保留 extra_commands；无 runs 时不误触保守语义；
    - CP-2.4-4 自报字段不参与判定（结构守门：字面量只在 prompt 常量内 + 行为面
      对账以台账为准）；哨兵 token → 对账字段无明文（脱敏出口②，架构 §9.3）；
    - CP-2.4-5 guard 幂等零扰动（幂等纪律③）：interrupt#2 self-loop 重入 + resume
      重跑均复用已落盘 exec_result 含 step_reconciliation，agent / 对账各恰 1 次。

全离线（InMemorySaver + mock agent），零 API 配额。
陷阱 6：core.nodes.execution 模块经 importlib 导入（callable 遮蔽）。
"""

from __future__ import annotations

import importlib
import inspect
import json
import logging
import uuid
from typing import Any, Dict, List, Optional, Tuple

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

import config
from core import secrets_store
from core.state import ExecutionMode

execution_module = importlib.import_module("core.nodes.execution")

from core.nodes.execution import execution  # noqa: E402
from sandbox.local_venv import SandboxPrepareResult, SandboxRunResult  # noqa: E402

ExecAgentOutput = execution_module.ExecAgentOutput
NODE_NAME = execution_module.NODE_NAME
INTERRUPT_KIND = execution_module.INTERRUPT_KIND
_reconcile_steps = execution_module._reconcile_steps

_TOKEN = "ghp_SENTINEL_t24_token_0987654321"


# ---------------------------------------------------------------------------
# fixtures / helpers（沿用 test_sprint4_e3 范式）
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


def _prep(success: bool = True) -> SandboxPrepareResult:
    return SandboxPrepareResult(
        success=success, venv_dir="/w/.venv", python_exe="/w/.venv/bin/python",
        pip_exe="", env_info={"python_version": "Python 3.11"}, install_log="ok",
        install_failed_packages=[], error=None,
    )


def _run(
    command: List[str],
    exit_code: int = 0,
    stdout: str = "",
    stderr: str = "",
    timed_out: bool = False,
) -> SandboxRunResult:
    return SandboxRunResult(
        exit_code=exit_code, stdout=stdout, stderr=stderr,
        duration_seconds=0.1, timed_out=timed_out,
        output_truncated=False, command=command,
    )


def _agent_out(
    prep: Optional[SandboxPrepareResult],
    runs: List[SandboxRunResult],
    rounds: int,
    ledger: Optional[List[Tuple[int, List[str], int]]] = None,
) -> ExecAgentOutput:
    return ExecAgentOutput(
        prep=prep, run_results=runs, rounds_used=rounds, llm_calls=rounds,
        step_ledger=list(ledger or []),
    )


def _plan_steps(n: int) -> List[Dict[str, str]]:
    return [
        {"step_name": f"步骤{i}", "command": f"python step{i}.py", "expected_output": ""}
        for i in range(n)
    ]


def _base_state(**overrides: Any) -> Dict[str, Any]:
    state: Dict[str, Any] = {
        "llm_config_set": {"default": {"model": "test"}},
        "code_output_dir": "/tmp/t24-workdir",
        "reproduction_plan": {
            "execution_steps": _plan_steps(1),
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


def _build_self_loop_graph(checkpointer):
    """sp3 CP-C3-7 同款最小 self-loop 图（模拟 D1 _route_after_execution 关键分支）。"""
    from langgraph.graph import END, START, StateGraph

    from core.state import GlobalState

    g = StateGraph(GlobalState)
    g.add_node("execution", execution)
    g.add_edge(START, "execution")

    def route(state: Dict[str, Any]) -> str:
        if state.get("_dev_loop_route") == "await_dev_loop_interrupt":
            return "execution"
        return "end"

    g.add_conditional_edges("execution", route, {"execution": "execution", "end": END})
    return g.compile(checkpointer=checkpointer)


# ===========================================================================
# CP-2.4-1 13 步计划 / 8 步执行（AC-S5-10 对账部分 + AC-S5-03 第②落点）
# ===========================================================================


def test_cp_2_4_1_thirteen_planned_eight_executed(monkeypatch):
    """planned=13 / executed=8 / completed 与真实 exit_code 一致 / 未执行 5 条含 index+step_name。"""
    steps = _plan_steps(13)
    runs: List[SandboxRunResult] = []
    ledger: List[Tuple[int, List[str], int]] = []
    for i in range(8):
        cmd = ["/w/.venv/bin/python", f"step{i}.py"]
        ec = 1 if i == 5 else 0  # 第 5 步真实失败 → 不计完成
        runs.append(_run(cmd, exit_code=ec, stderr="RuntimeError: boom" if ec else ""))
        ledger.append((i, cmd, ec))
    _patch_agent(monkeypatch, _agent_out(_prep(), runs, rounds=3, ledger=ledger))

    state = _base_state(
        reproduction_plan={"execution_steps": steps, "environment": {}},
        credential_degradations={
            "hf_token": "user_skipped",
            "git_credential:github.com": "user_skipped",
        },
    )
    out = execution(state)

    exec_result = out["execution_result"]
    recon = exec_result["step_reconciliation"]
    assert recon["planned"] == 13
    assert recon["executed"] == 8
    assert recon["completed"] == 7, "completed 必须与真实 exit_code 一致（step5 exit=1）"
    assert len(recon["unexecuted_steps"]) == 5
    assert [e["index"] for e in recon["unexecuted_steps"]] == [8, 9, 10, 11, 12]
    for entry in recon["unexecuted_steps"]:
        assert entry["step_name"] == f"步骤{entry['index']}"
    assert recon["extra_commands"] == []
    assert recon["attribution_unavailable"] is False

    # AC-S5-03 第②落点：coding gate 降级凭证 purpose_key 同点快照（排序、无敏感值）。
    assert exec_result["degraded_credentials"] == ["git_credential:github.com", "hf_token"]


def test_cp_2_4_1_degraded_credentials_defensive_default(monkeypatch):
    """旧 checkpoint 无 credential_degradations 键 → .get() 防御读 → 快照为 []。"""
    cmd = ["/w/.venv/bin/python", "step0.py"]
    _patch_agent(monkeypatch, _agent_out(
        _prep(), [_run(cmd, stdout='<METRICS>{"acc": 0.9}</METRICS>')],
        rounds=2, ledger=[(0, cmd, 0)],
    ))
    out = execution(_base_state())  # base state 不含 credential_degradations
    exec_result = out["execution_result"]
    assert exec_result["degraded_credentials"] == []
    assert exec_result["step_reconciliation"]["executed"] == 1
    assert exec_result["step_reconciliation"]["completed"] == 1


# ===========================================================================
# CP-2.4-2 归属三级各证（Q-S5-6 归属规则）
# ===========================================================================


def test_cp_2_4_2_level1_step_index_priority_over_normalized_match():
    """规则①优先：命令归一可匹配步骤 1，但台账声明 step_index=0 → 归属 0。"""
    steps = [
        {"step_name": "A", "command": "python a.py"},
        {"step_name": "B", "command": "python b.py"},
    ]
    cmd = ["/w/.venv/bin/python", "b.py"]
    recon = _reconcile_steps(steps, [_run(cmd)], [(0, cmd, 0)])
    assert recon["executed"] == 1
    assert [e["index"] for e in recon["unexecuted_steps"]] == [1], \
        "声明归属必须抢占归一匹配（步骤 1 应标未执行）"


def test_cp_2_4_2_out_of_range_discarded_with_warning_then_level2(caplog):
    """越界 step_index 丢弃 + WARNING；该 run 回落规则②归一匹配仍归属。"""
    steps = [{"step_name": "A", "command": "python a.py"}]
    cmd = ["/w/.venv/bin/python", "a.py"]
    with caplog.at_level(logging.WARNING):
        recon = _reconcile_steps(steps, [_run(cmd)], [(99, cmd, 0)])
    assert any("越界丢弃" in r.message for r in caplog.records), "越界丢弃必须打 WARNING"
    assert recon["executed"] == 1 and recon["completed"] == 1
    assert recon["unexecuted_steps"] == []


def test_cp_2_4_2_level2_unlabeled_normalized_match():
    """规则②：无标签（step_index=-1）条目经归一精确匹配归属。"""
    steps = [{"step_name": "训练", "command": "python3 train.py --epochs 3"}]
    cmd = ["/w/.venv/bin/python3.11", "train.py", "--epochs", "3"]
    recon = _reconcile_steps(steps, [_run(cmd)], [(-1, cmd, 0)])
    assert recon["executed"] == 1 and recon["completed"] == 1
    assert recon["extra_commands"] == []


def test_cp_2_4_2_rewritten_command_still_attributed():
    """agent/工具改写命令（改路径：裸 python→venv 绝对路径；补参：pip→python -m pip）
    经同套归一仍归属；复合步骤（cd && python）的 cd 子命令不参与匹配。"""
    steps = [
        {"step_name": "装依赖", "command": "pip install torch==2.1"},
        {"step_name": "复合步", "command": "cd repo && python run.py"},
    ]
    runs = [
        _run(["/w/.venv/bin/python", "-m", "pip", "install", "torch==2.1"]),
        _run(["/w/.venv/bin/python", "run.py"]),
    ]
    recon = _reconcile_steps(steps, runs, [])  # 无台账声明，全走规则②
    assert recon["executed"] == 2 and recon["completed"] == 2
    assert recon["unexecuted_steps"] == [] and recon["extra_commands"] == []


def test_cp_2_4_2_level3_no_match_goes_extra_commands():
    """规则③：完全不匹配 → extra_commands（不折算步骤）；部分归属不触发保守语义。"""
    steps = [
        {"step_name": "A", "command": "python a.py"},
        {"step_name": "B", "command": "python b.py"},
    ]
    runs = [_run(["/w/.venv/bin/python", "a.py"]), _run(["ls", "-la"])]
    recon = _reconcile_steps(steps, runs, [])
    assert recon["executed"] == 1
    assert recon["extra_commands"] == ["ls -la"]
    assert [e["index"] for e in recon["unexecuted_steps"]] == [1]
    assert recon["attribution_unavailable"] is False, "部分归属成功不得触发 R-2 保守语义"


def test_cp_2_4_2_effective_runs_last_attempt_decides_completion():
    """同命令重试以最后一次为准（_effective_runs 既有口径）：先败后成 → 已完成。"""
    steps = [{"step_name": "A", "command": "python a.py"}]
    cmd = ["/w/.venv/bin/python", "a.py"]
    runs = [_run(cmd, exit_code=1, stderr="boom"), _run(cmd, exit_code=0)]
    recon = _reconcile_steps(steps, runs, [(0, cmd, 1), (0, cmd, 0)])
    assert recon["executed"] == 1 and recon["completed"] == 1


# ===========================================================================
# CP-2.4-3 attribution_unavailable 保守语义（R-2，误报防线优先于命中）
# ===========================================================================


def test_cp_2_4_3_attribution_unavailable_conservative(caplog):
    """全零归属 ∧ run_results 非空 → attribution_unavailable=True：
    unexecuted_steps 置空（下游 incomplete_execution 规则「存在未执行步骤」自然不点火），
    原始命令如实保留在 extra_commands。"""
    steps = _plan_steps(3)
    runs = [
        _run(["bash", "custom_a.sh"]),
        _run(["bash", "custom_b.sh"], exit_code=1, stderr="x"),
    ]
    with caplog.at_level(logging.WARNING):
        recon = _reconcile_steps(steps, runs, [])
    assert recon["attribution_unavailable"] is True
    assert recon["unexecuted_steps"] == [], \
        "保守语义：无法归属 ≠ 未执行，不得给 incomplete_execution 标注供火"
    assert recon["executed"] == 0 and recon["completed"] == 0
    assert recon["extra_commands"] == ["bash custom_a.sh", "bash custom_b.sh"], \
        "原始命令清单必须如实保留（报告展示用）"
    assert any("attribution_unavailable" in r.message for r in caplog.records), \
        "保守语义激活必须打 WARNING 留痕"


def test_cp_2_4_3_no_runs_not_conservative():
    """边界：真没执行任何命令（run_results 空）≠ 归属失效 → 如实标注全部未执行。"""
    recon = _reconcile_steps(_plan_steps(2), [], [])
    assert recon["attribution_unavailable"] is False
    assert [e["index"] for e in recon["unexecuted_steps"]] == [0, 1]
    assert recon["executed"] == 0


# ===========================================================================
# CP-2.4-4 自报字段不参与判定 + 脱敏出口②（架构 §9.3）
# ===========================================================================


def test_cp_2_4_4_steps_attempted_not_consumed_structural():
    """结构守门：字面量 steps_attempted 在模块源码中只允许出现在冻结 prompt 常量内
    （零代码消费点 → 自报字段结构性无法参与任何判定，产品红线）。"""
    src = inspect.getsource(execution_module)
    literal = "steps_" + "attempted"  # 避免本断言自身被计入源码扫描面
    prompt_count = execution_module._EXECUTION_SYSTEM_PROMPT_BODY.count(literal)
    assert prompt_count >= 1, "prompt 常量应保留自报字段（仅供参考）"
    assert src.count(literal) == prompt_count, \
        "steps_attempted 出现在 prompt 常量之外 → 存在代码消费点，违反产品红线"


def test_cp_2_4_4_ledger_authoritative_and_masked(monkeypatch):
    """行为面：agent 自报无输入通道（见结构守门），对账以台账 8 条为准；
    哨兵 token 注入 extra_commands 与 unexecuted_steps 命令串 → 落 state 无明文。"""
    secrets_store.register_sensitive_value(_TOKEN)
    steps = _plan_steps(13)
    # 第 12 步无 step_name 且命令内嵌 token → 未执行清单回落命令串，必须脱敏。
    steps[12] = {"command": f"python download.py --token={_TOKEN}"}

    runs: List[SandboxRunResult] = []
    ledger: List[Tuple[int, List[str], int]] = []
    for i in range(8):
        cmd = ["/w/.venv/bin/python", f"step{i}.py"]
        runs.append(_run(cmd))
        ledger.append((i, cmd, 0))
    # 计划外命令内嵌 token → extra_commands 必须脱敏。
    extra_cmd = ["bash", "push.sh", f"--token={_TOKEN}"]
    runs.append(_run(extra_cmd))
    ledger.append((-1, extra_cmd, 0))
    _patch_agent(monkeypatch, _agent_out(_prep(), runs, rounds=3, ledger=ledger))

    out = execution(_base_state(
        reproduction_plan={"execution_steps": steps, "environment": {}},
    ))
    recon = out["execution_result"]["step_reconciliation"]

    # 台账为准：executed=8（agent <result> 无论自报多少都无从进入判定）。
    assert recon["planned"] == 13 and recon["executed"] == 8

    dumped = json.dumps(recon, ensure_ascii=False, default=str)
    assert _TOKEN not in dumped, "对账字段落 state 不得含哨兵 token 明文（脱敏出口②）"
    assert len(recon["extra_commands"]) == 1 and "****" in recon["extra_commands"][0]
    step12 = next(e for e in recon["unexecuted_steps"] if e["index"] == 12)
    assert "****" in step12["step_name"] and _TOKEN not in step12["step_name"]


# ===========================================================================
# CP-2.4-5 guard 幂等零扰动（幂等纪律③ / 架构 §9.2）
# ===========================================================================


def test_cp_2_4_5_guard_and_resume_reuse_committed_reconciliation(monkeypatch):
    """interrupt#2 路径：首跑一次 commit 即含 step_reconciliation；self-loop 重入
    guard 命中与 resume 重跑均复用已落盘结果——agent 与 _reconcile_steps 各恰 1 次。"""
    steps = _plan_steps(2)
    cmd = ["/w/.venv/bin/python", "step0.py"]
    runs = [_run(cmd, exit_code=1, stderr="HTTP 401 unauthorized")]  # 不可修复 → interrupt#2
    agent_cnt = _patch_agent(
        monkeypatch, _agent_out(_prep(), runs, rounds=2, ledger=[(0, cmd, 1)]),
    )

    real_reconcile = execution_module._reconcile_steps
    cnt = {"reconcile": 0}

    def counting_reconcile(*a, **k):
        cnt["reconcile"] += 1
        return real_reconcile(*a, **k)

    monkeypatch.setattr(execution_module, "_reconcile_steps", counting_reconcile)

    graph = _build_self_loop_graph(InMemorySaver())
    cfg = {"configurable": {"thread_id": f"t24-guard-{uuid.uuid4().hex[:8]}"}}
    out1 = graph.invoke(
        _base_state(reproduction_plan={"execution_steps": steps, "environment": {}}),
        cfg,
    )
    assert "__interrupt__" in out1, "credential_required 不可修复 → self-loop 重入后 interrupt#2"

    values = graph.get_state(cfg).values
    recon = values["execution_result"]["step_reconciliation"]
    assert recon["planned"] == 2 and recon["executed"] == 1 and recon["completed"] == 0
    assert [e["index"] for e in recon["unexecuted_steps"]] == [1]
    assert agent_cnt["agent"] == 1, "guard 命中路径不得重跑 sandbox 子图"
    assert cnt["reconcile"] == 1, "guard 命中路径不得重算对账（复用已落盘 exec_result）"

    graph.invoke(Command(resume={"decision": "terminate"}), cfg)
    final = graph.get_state(cfg).values
    assert final["user_fix_decision"] == "terminate"
    assert final["execution_result"]["step_reconciliation"] == recon, \
        "resume 重跑必须零扰动复用同一份对账结果"
    assert agent_cnt["agent"] == 1 and cnt["reconcile"] == 1, "resume 重跑零重算"
