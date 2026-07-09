"""Sprint 5 任务 T-S5-2-5（S5-06）：预算联动 _effective_max_rounds + budget_truncated 显式化。

覆盖 dev-plan §批次2 任务 T-S5-2-5 自测检查点：
    - CP-2.5-1 联动参数化断言：13 步→18 / 1 步→10（FLOOR）/ 40 步→30（CAP）/
      空计划→10（AC-S5-12 联动部分）；含畸形 plan 防御 + 全局账本对账
      （CAP=30 下修复循环子预算 60 仍容一个完整回合，入口门 =2 不变）；
    - CP-2.5-2 截断场景 mock（剧本跑满轮次）→ budget_truncated=True + INFO 日志
      caplog；正常收尾（round = max_rounds-1 边界）→ False——判据两侧各证；
      另证降级路径（子图异常 rounds=0）默认 False + 全节点接线（未 patch agent，
      exec_result 真实透传截断标记）；
    - CP-2.5-3 预算扣减落点 B 零回归：rounds_used 扣减语义（rounds=12 > 旧常量 10，
      联动后合法）、guard 重入零扣减（sp4 CP-E3-1 断言面同款）；HumanMessage 预算
      数字与 helper 产出一致（两处子图消费点同源同值）；
    - CP-2.5-4 exec_result 含 budget_truncated 随一次 commit 落盘；guard 命中 /
      resume 重跑复用已落盘结果含新字段、agent 恰 1 次零重算（幂等纪律③）。

全离线（InMemorySaver + mock agent / 剧本子图），零 API 配额。
陷阱 6：core.nodes.execution 模块经 importlib 导入（callable 遮蔽）。
"""

from __future__ import annotations

import importlib
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
_effective_max_rounds = execution_module._effective_max_rounds

_TRUNCATION_LOG_MARK = "轮次预算截断"


# ---------------------------------------------------------------------------
# fixtures / helpers（沿用 test_sprint5_t24_reconcile / test_sprint4_e3 范式）
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
) -> SandboxRunResult:
    return SandboxRunResult(
        exit_code=exit_code, stdout=stdout, stderr=stderr,
        duration_seconds=0.1, timed_out=False,
        output_truncated=False, command=command,
    )


def _agent_out(
    prep: Optional[SandboxPrepareResult],
    runs: List[SandboxRunResult],
    rounds: int,
    ledger: Optional[List[Tuple[int, List[str], int]]] = None,
    budget_truncated: bool = False,
) -> ExecAgentOutput:
    return ExecAgentOutput(
        prep=prep, run_results=runs, rounds_used=rounds, llm_calls=rounds,
        step_ledger=list(ledger or []), budget_truncated=budget_truncated,
    )


def _plan_steps(n: int) -> List[Dict[str, str]]:
    return [
        {"step_name": f"步骤{i}", "command": f"python step{i}.py", "expected_output": ""}
        for i in range(n)
    ]


def _base_state(**overrides: Any) -> Dict[str, Any]:
    state: Dict[str, Any] = {
        "llm_config_set": {"default": {"model": "test"}},
        "code_output_dir": "/tmp/t25-workdir",
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


class _ScriptedSubgraph:
    """剧本子图：捕获 initial state，按预设 final round 收尾（LLM 不会被真正调用）。"""

    def __init__(self, capture: Dict[str, Any], final_round_fn):
        self._capture = capture
        self._final_round_fn = final_round_fn

    def invoke(self, initial: Dict[str, Any]) -> Dict[str, Any]:
        self._capture["initial"] = initial
        return {
            **initial,
            "round": int(self._final_round_fn(int(initial["max_rounds"]))),
            "status": "finished",
        }


def _run_agent_scripted(monkeypatch, state, work_dir, plan, final_round_fn):
    """跑一次真 _run_execution_agent（只 mock LLM 与子图工厂），返回 (输出, 装配捕获)。"""
    capture: Dict[str, Any] = {}
    monkeypatch.setattr(execution_module, "resolve_llm_config", lambda cfg, node: cfg)
    monkeypatch.setattr(execution_module, "create_llm", lambda cfg: object())

    def fake_factory(node_name, system_prompt, tools, max_rounds, result_schema=None):
        capture["factory_max_rounds"] = max_rounds
        return _ScriptedSubgraph(capture, final_round_fn)

    monkeypatch.setattr(execution_module, "create_react_subgraph", fake_factory)
    out = execution_module._run_execution_agent(state, work_dir, plan)
    return out, capture


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
# CP-2.5-1 联动参数化断言（Q-S5-7 公式，AC-S5-12 联动部分）
# ===========================================================================


@pytest.mark.parametrize(
    "n_steps, expected",
    [
        (13, 18),  # 回归样本：13 步 + K(5) = 18（现状 10 轮只够 8 步的结构根因治点）
        (1, 10),   # 1 + 5 = 6 < FLOOR → 钳到 10
        (40, 30),  # 40 + 5 = 45 > CAP → 钳到 30
        (0, 10),   # 空计划 → 0 + 5 = 5 < FLOOR → 10
    ],
)
def test_cp_2_5_1_linkage_parametrized(n_steps, expected):
    """clamp(len(steps) + K, FLOOR, CAP) 四点位参数化（dev-plan 给定值逐一核对）。"""
    plan = {"execution_steps": _plan_steps(n_steps), "environment": {}}
    assert _effective_max_rounds(plan) == expected


def test_cp_2_5_1_formula_constants_and_defensive():
    """公式常量来源 = config 三常量（不写死）；畸形 plan 防御回落 FLOOR 不炸。"""
    # 常量语义：K=5 / FLOOR=10 / CAP=30（T-S5-1-1 已落，此处对账消费面）。
    assert config.REACT_EXECUTION_ROUNDS_MARGIN == 5
    assert config.REACT_MAX_ROUNDS_EXECUTION == 10
    assert config.REACT_MAX_ROUNDS_EXECUTION_CAP == 30
    # 公式与常量一致性（任取一点核对，防止 helper 写死数字）。
    n = 13
    assert _effective_max_rounds({"execution_steps": _plan_steps(n)}) == max(
        config.REACT_MAX_ROUNDS_EXECUTION,
        min(n + config.REACT_EXECUTION_ROUNDS_MARGIN, config.REACT_MAX_ROUNDS_EXECUTION_CAP),
    )
    # 防御：plan 缺键 / None / 非 dict / steps 非 list → 一律按 0 步回落 FLOOR。
    for bad_plan in ({}, None, "not-a-dict", {"execution_steps": None},
                     {"execution_steps": "oops"}):
        assert _effective_max_rounds(bad_plan) == config.REACT_MAX_ROUNDS_EXECUTION


def test_cp_2_5_1_global_ledger_reconciliation():
    """全局账本对账（架构 §3）：CAP=30 = MAX_DEV_LOOP_LLM_CALLS/2——最坏初跑耗尽
    CAP 后修复循环子预算仍容一个完整回合；入口门 DEV_LOOP_MIN_CALLS_PER_ROUND=2 不变。"""
    assert config.REACT_MAX_ROUNDS_EXECUTION_CAP * 2 == config.MAX_DEV_LOOP_LLM_CALLS
    remaining_after_worst_first_run = (
        config.MAX_DEV_LOOP_LLM_CALLS - config.REACT_MAX_ROUNDS_EXECUTION_CAP
    )
    assert remaining_after_worst_first_run >= config.DEV_LOOP_MIN_CALLS_PER_ROUND, \
        "耗尽 CAP 后子预算余量必须仍能通过入口预算门（容一个修复回合）"
    assert config.DEV_LOOP_MIN_CALLS_PER_ROUND == 2, "入口门不变（T-S5-2-5 零改动面）"


# ===========================================================================
# CP-2.5-2 截断判据两侧各证（AC-S5-12：显式 log + state 记录）
# ===========================================================================


def test_cp_2_5_2_truncated_scenario_flag_and_info_log(monkeypatch, caplog):
    """剧本跑满轮次（round = effective_max_rounds，force_finish 路径）→
    budget_truncated=True + INFO 日志留痕。"""
    plan = {"execution_steps": _plan_steps(13), "environment": {}}
    with caplog.at_level(logging.INFO):
        out, capture = _run_agent_scripted(
            monkeypatch, _base_state(), "/tmp/t25-wd", plan,
            final_round_fn=lambda max_rounds: max_rounds,  # 跑满 → force_finish 收尾
        )
    assert capture["factory_max_rounds"] == 18, "13 步计划联动 → 子图预算 18"
    assert out.rounds_used == 18
    assert out.budget_truncated is True
    truncation_logs = [
        r for r in caplog.records
        if _TRUNCATION_LOG_MARK in r.message and r.name == "core.nodes.execution"
    ]
    assert truncation_logs, "预算截断必须显式 INFO 日志（项目通则 sp5 首落点）"
    assert all(r.levelno == logging.INFO for r in truncation_logs)


def test_cp_2_5_2_normal_finish_boundary_not_truncated(monkeypatch, caplog):
    """正常收尾边界（round = max_rounds - 1，budget_check 未触发 force_finish 的
    最大合法值）→ budget_truncated=False 且无截断日志——判据另一侧。"""
    plan = {"execution_steps": _plan_steps(13), "environment": {}}
    with caplog.at_level(logging.INFO):
        out, _ = _run_agent_scripted(
            monkeypatch, _base_state(), "/tmp/t25-wd", plan,
            final_round_fn=lambda max_rounds: max_rounds - 1,
        )
    assert out.rounds_used == 17
    assert out.budget_truncated is False
    assert not any(_TRUNCATION_LOG_MARK in r.message for r in caplog.records), \
        "正常收尾不得产生截断日志（避免误报噪声）"


def test_cp_2_5_2_degraded_path_default_false(monkeypatch, caplog):
    """子图降级路径（异常 → rounds_used=0）→ budget_truncated 走 dataclass 默认 False。"""
    monkeypatch.setattr(execution_module, "resolve_llm_config", lambda cfg, node: cfg)
    monkeypatch.setattr(
        execution_module, "create_llm",
        lambda cfg: (_ for _ in ()).throw(RuntimeError("llm init boom")),
    )
    with caplog.at_level(logging.INFO):
        out = execution_module._run_execution_agent(
            _base_state(), "/tmp/t25-wd", {"execution_steps": _plan_steps(3)},
        )
    assert out.rounds_used == 0 and out.budget_truncated is False
    assert not any(_TRUNCATION_LOG_MARK in r.message for r in caplog.records)


def test_cp_2_5_2_full_node_wiring_truncation_into_exec_result(monkeypatch, caplog):
    """全节点接线（不 patch _run_execution_agent）：剧本跑满 → 真实截断判据产出
    经 execution() 步骤 4.6/5 透传，exec_result["budget_truncated"]=True 落盘。"""
    plan = {"execution_steps": _plan_steps(13), "environment": {}}
    capture: Dict[str, Any] = {}
    monkeypatch.setattr(execution_module, "resolve_llm_config", lambda cfg, node: cfg)
    monkeypatch.setattr(execution_module, "create_llm", lambda cfg: object())

    def fake_factory(node_name, system_prompt, tools, max_rounds, result_schema=None):
        capture["factory_max_rounds"] = max_rounds
        return _ScriptedSubgraph(capture, lambda mr: mr)  # 跑满轮次

    monkeypatch.setattr(execution_module, "create_react_subgraph", fake_factory)
    with caplog.at_level(logging.INFO):
        out = execution(_base_state(reproduction_plan=plan))

    er = out["execution_result"]
    assert er["budget_truncated"] is True, "截断标记必须随 exec_result 一次 commit 落盘"
    assert any(_TRUNCATION_LOG_MARK in r.message for r in caplog.records)
    # 剧本子图无工具调用 → 空 run_results 走既有降级分类，节点不炸（零回归面）。
    assert er["success"] is False


# ===========================================================================
# CP-2.5-3 预算扣减落点 B 零回归 + HumanMessage 预算数字同源（sp4 CP-E3-1 断言面）
# ===========================================================================


def test_cp_2_5_3_rounds_deduction_beyond_old_constant(monkeypatch):
    """扣减语义零回归：rounds=12（> 旧常量 10，联动后合法轮数）→ 递减恰 = 12；
    _dev_loop_llm_calls 累加同额（落点 B 单点扣减）。"""
    cmd = ["/w/.venv/bin/python", "step0.py"]
    _patch_agent(monkeypatch, _agent_out(
        _prep(), [_run(cmd, stdout='<METRICS>{"acc": 0.9}</METRICS>')],
        rounds=12, ledger=[(0, cmd, 0)],
    ))
    state = _base_state(retry_budget_remaining=40, _dev_loop_llm_calls=2)
    out = execution(state)
    assert out["retry_budget_remaining"] == 40 - 12
    assert out["_dev_loop_llm_calls"] == 2 + 12
    assert out["execution_result"]["success"] is True


def test_cp_2_5_3_degrade_rounds_zero_no_deduction(monkeypatch):
    """降级路径零扣减零回归（sp4 CP-E3-1 同款）：rounds=0 → 不写预算键。"""
    _patch_agent(monkeypatch, _agent_out(None, [], rounds=0))
    out = execution(_base_state(retry_budget_remaining=40, _dev_loop_llm_calls=3))
    assert "retry_budget_remaining" not in out, "rounds=0 绝不扣预算"
    assert "_dev_loop_llm_calls" not in out


def test_cp_2_5_3_guard_reentry_zero_deduction(monkeypatch):
    """guard 重入零扣减（sp4 CP-E3-1 断言面复跑）：已落盘结果 + await 标记重入 →
    agent 零调用、预算/子预算保持入口值。"""

    def forbidden_agent(*a, **k):
        raise AssertionError("guard 命中路径不得调用 _run_execution_agent")

    monkeypatch.setattr(execution_module, "_run_execution_agent", forbidden_agent)
    committed = {
        "success": False, "metrics": {}, "logs": "masked-earlier",
        "errors": ["[error_category=data_missing] 数据集缺失"],
        "artifacts": [], "runtime_seconds": 0.1, "environment_info": {},
        "step_reconciliation": {}, "degraded_credentials": [],
        "budget_truncated": True,
    }
    graph = _build_self_loop_graph(InMemorySaver())
    cfg = {"configurable": {"thread_id": f"t25-guard-{uuid.uuid4().hex[:8]}"}}
    out1 = graph.invoke(
        _base_state(
            execution_result=committed,
            _dev_loop_route="await_dev_loop_interrupt",
            retry_budget_remaining=25,
            _dev_loop_llm_calls=7,
        ),
        cfg,
    )
    assert "__interrupt__" in out1, "guard 命中 + 不可修复 → interrupt#2"
    values = graph.get_state(cfg).values
    assert values["retry_budget_remaining"] == 25, "guard 重入零扣减"
    assert values["_dev_loop_llm_calls"] == 7
    # guard 复用路径不得二次写入 / 丢失新字段（交接注意事项 1）。
    assert values["execution_result"]["budget_truncated"] is True


def test_cp_2_5_3_human_message_budget_matches_helper(monkeypatch):
    """HumanMessage 预算数字与 helper 产出一致，且与两处子图消费点同源同值
    （factory max_rounds == initial["max_rounds"] == payload["max_rounds"]）。"""
    plan = {"execution_steps": _plan_steps(13), "environment": {}}
    state = _base_state(reproduction_plan=plan)
    _, capture = _run_agent_scripted(
        monkeypatch, state, "/tmp/t25-wd", plan, final_round_fn=lambda mr: 3,
    )
    expected = _effective_max_rounds(plan)
    assert expected == 18
    assert capture["factory_max_rounds"] == expected, "消费点 1：create_react_subgraph"
    initial = capture["initial"]
    assert initial["max_rounds"] == expected, "消费点 2：ReActState 初始化"
    human_payload = json.loads(initial["messages"][1].content)
    assert human_payload["max_rounds"] == expected, \
        "HumanMessage 动态通道预算数字必须与 _effective_max_rounds 产出一致（接 T-S5-1-4 占位）"
    assert isinstance(human_payload["max_rounds"], int)


# ===========================================================================
# CP-2.5-4 一次 commit 落盘 + guard/resume 复用零重算（幂等纪律③）
# ===========================================================================


def test_cp_2_5_4_one_commit_and_guard_resume_reuse(monkeypatch):
    """interrupt#2 路径：首跑一次 commit 即含 budget_truncated；self-loop 重入
    guard 命中与 resume 重跑均复用已落盘结果——agent 恰 1 次、标记零漂移。"""
    cmd = ["/w/.venv/bin/python", "step0.py"]
    runs = [_run(cmd, exit_code=1, stderr="HTTP 401 unauthorized")]  # 不可修复 → interrupt#2
    agent_cnt = _patch_agent(monkeypatch, _agent_out(
        _prep(), runs, rounds=18, ledger=[(0, cmd, 1)], budget_truncated=True,
    ))

    graph = _build_self_loop_graph(InMemorySaver())
    cfg = {"configurable": {"thread_id": f"t25-commit-{uuid.uuid4().hex[:8]}"}}
    out1 = graph.invoke(
        _base_state(
            reproduction_plan={"execution_steps": _plan_steps(13), "environment": {}},
            retry_budget_remaining=40,
        ),
        cfg,
    )
    assert "__interrupt__" in out1

    values = graph.get_state(cfg).values
    er = values["execution_result"]
    assert er["budget_truncated"] is True, "首跑一次 commit 即含截断标记"
    assert er["step_reconciliation"]["planned"] == 13, "与对账字段同一次 commit"
    assert values["retry_budget_remaining"] == 40 - 18, "首跑如实扣减；重入 guard 零重扣"
    assert agent_cnt["agent"] == 1, "guard 命中路径不得重跑 sandbox 子图（零重算）"

    graph.invoke(Command(resume={"decision": "terminate"}), cfg)
    final = graph.get_state(cfg).values
    assert final["user_fix_decision"] == "terminate"
    assert final["execution_result"]["budget_truncated"] is True, \
        "resume 重跑必须零扰动复用同一份 exec_result（含新字段）"
    assert final["execution_result"] == er
    assert agent_cnt["agent"] == 1 and final["retry_budget_remaining"] == 40 - 18


def test_cp_2_5_4_normal_path_flag_false_committed(monkeypatch):
    """判据另一侧随 commit 落盘：正常收尾（budget_truncated=False）→ exec_result
    显式含 False（显式化 ≠ 只在截断时写键，reporting 可无条件 .get 消费）。"""
    cmd = ["/w/.venv/bin/python", "step0.py"]
    _patch_agent(monkeypatch, _agent_out(
        _prep(), [_run(cmd, stdout='<METRICS>{"acc": 0.9}</METRICS>')],
        rounds=3, ledger=[(0, cmd, 0)],
    ))
    out = execution(_base_state())
    er = out["execution_result"]
    assert "budget_truncated" in er and er["budget_truncated"] is False
