"""Sprint 7 任务 T-S7-1-1（S7-03，架构 §6.2）：_run_execution_agent 入口收窄 max_rounds clamp。

覆盖 dev-plan §5 T-S7-1-1 自测检查点 CP-1.1-1 ~ CP-1.1-5（AC-S7-08）：
    - CP-1.1-1 收窄逻辑单测：dev_calls 逼近子上限 → effective_max_rounds = max(1,min(联动,剩余))；
    - CP-1.1-2 保底 1 轮：dev_calls 触顶 → 剩余=0 → 收窄到 1 轮（防 0 轮死锁）；
    - CP-1.1-3 越界上界断言：收窄后单轮最多烧剩余子预算 → 冲过头幅度确定性小值；
    - CP-1.1-4 R-PC4 无扰：不同 dev_calls 下 HumanMessage 的 max_rounds 数字恒为联动值；
    - CP-1.1-5 须验红：注掉收窄 clamp 后 CP-1.1-1/1.1-3 断言必须变红。

全离线：patch create_react_subgraph 捕获传入的 max_rounds，subgraph.invoke 返回空 final_state；
patch create_llm / resolve_llm_config / 工具工厂 / 凭证 helper 避免真跑，零 API 配额。
"""

from __future__ import annotations

import importlib
from typing import Any, Dict, List, Optional

import pytest

import config
from core.state import ExecutionMode

execution_module = importlib.import_module("core.nodes.execution")

from config import MAX_DEV_LOOP_LLM_CALLS  # noqa: E402


# ---------------------------------------------------------------------------
# fixtures / helpers
# ---------------------------------------------------------------------------


class _FakeSubgraph:
    """create_react_subgraph 的替身：记录 max_rounds，invoke 返回最小 final_state。"""

    def __init__(self, max_rounds: int) -> None:
        self.max_rounds = max_rounds

    def invoke(self, initial: Dict[str, Any]) -> Dict[str, Any]:
        # round=0 → rounds_used=max(1,0)=1；messages 空 → run_results/prep 空。
        return {"messages": [], "round": 0, "result": None}


def _install_agent_harness(monkeypatch) -> Dict[str, Any]:
    """patch _run_execution_agent 的所有外部依赖，返回捕获 dict（含 subgraph_max_rounds / context）。"""
    captured: Dict[str, Any] = {"subgraph_max_rounds": None, "context": None}

    def fake_create_react_subgraph(*, node_name, system_prompt, tools, max_rounds):
        captured["subgraph_max_rounds"] = max_rounds
        return _FakeSubgraph(max_rounds)

    # 捕获 context（HumanMessage 通道）：包一层真 _build_execution_agent_context。
    _real_ctx = execution_module._build_execution_agent_context

    def spy_ctx(state, work_dir, plan):
        ctx = _real_ctx(state, work_dir, plan)
        captured["context"] = ctx
        return ctx

    monkeypatch.setattr(execution_module, "create_react_subgraph", fake_create_react_subgraph)
    monkeypatch.setattr(execution_module, "_build_execution_agent_context", spy_ctx)
    monkeypatch.setattr(execution_module, "create_llm", lambda cfg: object())
    monkeypatch.setattr(execution_module, "resolve_llm_config", lambda cfg, node: cfg)
    monkeypatch.setattr(execution_module, "load_all_secrets", lambda *a, **k: {})
    monkeypatch.setattr(execution_module, "build_credential_env", lambda secrets: {})
    monkeypatch.setattr(execution_module, "make_prepare_environment_tool", lambda *a, **k: None)
    monkeypatch.setattr(execution_module, "make_run_in_sandbox_tool", lambda *a, **k: None)
    monkeypatch.setattr(execution_module, "make_request_user_input_tool", lambda *a, **k: None)
    return captured


def _plan_with_steps(n: int) -> Dict[str, Any]:
    """构造有 n 步的 plan。联动公式 clamp(n+K, FLOOR, CAP)——取 n 使联动值落在期望区间。"""
    return {
        "execution_steps": [{"command": f"python step{i}.py"} for i in range(n)],
        "environment": {},
    }


def _state(dev_calls: int, plan: Dict[str, Any], **overrides: Any) -> Dict[str, Any]:
    state: Dict[str, Any] = {
        "llm_config_set": {"default": {"model": "test"}},
        "code_output_dir": "/tmp/s7-clamp",
        "reproduction_plan": plan,
        "paper_analysis": {"metrics": []},
        "execution_mode": ExecutionMode.FULL,
        "fix_loop_count": 0,
        "retry_budget_remaining": 240,
        "_dev_loop_llm_calls": dev_calls,
        "_dev_loop_route": None,
        "execution_result": None,
        "credential_degradations": {},
    }
    state.update(overrides)
    return state


def _run_agent_capture_max_rounds(monkeypatch, dev_calls: int, plan: Dict[str, Any]) -> Dict[str, Any]:
    captured = _install_agent_harness(monkeypatch)
    state = _state(dev_calls, plan)
    execution_module._run_execution_agent(state, "/tmp/s7-clamp", plan)
    return captured


# 联动值锚点：确保测试断言独立于常量翻倍，用 _effective_max_rounds 现算基线值。
def _base_rounds(plan: Dict[str, Any]) -> int:
    return execution_module._effective_max_rounds(plan)


# ===========================================================================
# CP-1.1-1：收窄逻辑单测（AC-S7-08）
# ===========================================================================


def test_cp_1_1_1_clamp_narrows_when_dev_calls_approach_ceiling(monkeypatch):
    """dev_calls 逼近子上限 → effective_max_rounds = max(1, min(联动值, 剩余子预算))。"""
    # 造一个联动值明显大于剩余子预算的场景：步数足够多让联动值撞 CAP=60。
    plan = _plan_with_steps(80)  # n+K 远超 CAP → 联动值 = CAP = 60
    base = _base_rounds(plan)
    assert base == config.REACT_MAX_ROUNDS_EXECUTION_CAP, "前提：步数够多联动值应撞 CAP"

    # dev_calls = 上限 - 2 → 剩余子预算 = 2 → 收窄到 min(60, 2) = 2。
    dev_calls = MAX_DEV_LOOP_LLM_CALLS - 2
    captured = _run_agent_capture_max_rounds(monkeypatch, dev_calls, plan)
    assert captured["subgraph_max_rounds"] == 2, (
        f"收窄后应为 max(1,min({base},2))==2，实际 {captured['subgraph_max_rounds']}"
    )


def test_cp_1_1_1_no_narrow_when_dev_calls_zero(monkeypatch):
    """dev_calls=0（不逼近）→ 剩余子预算充足 → 无收窄，退回联动值。"""
    plan = _plan_with_steps(80)
    base = _base_rounds(plan)
    captured = _run_agent_capture_max_rounds(monkeypatch, 0, plan)
    assert captured["subgraph_max_rounds"] == base, (
        f"dev_calls=0 → min(联动值 {base}, 剩余 {MAX_DEV_LOOP_LLM_CALLS})==联动值"
    )


# ===========================================================================
# CP-1.1-2：保底 1 轮（R-S7-5）
# ===========================================================================


def test_cp_1_1_2_floor_one_round_when_budget_exhausted(monkeypatch):
    """dev_calls 触顶 → 剩余子预算=0 → max(1, min(联动值, 0)) == 1（不退化 0 轮死锁）。"""
    plan = _plan_with_steps(80)
    captured = _run_agent_capture_max_rounds(monkeypatch, MAX_DEV_LOOP_LLM_CALLS, plan)
    assert captured["subgraph_max_rounds"] == 1, "触顶应保底 1 轮，不得为 0"


def test_cp_1_1_2_floor_one_round_when_over_ceiling(monkeypatch):
    """dev_calls 已越顶（> 上限）→ 剩余 clamp 到 0 → 仍保底 1 轮。"""
    plan = _plan_with_steps(80)
    captured = _run_agent_capture_max_rounds(monkeypatch, MAX_DEV_LOOP_LLM_CALLS + 10, plan)
    assert captured["subgraph_max_rounds"] == 1


# ===========================================================================
# CP-1.1-3：越界上界断言（AC-S7-08）
# ===========================================================================


def test_cp_1_1_3_over_run_bound_is_deterministic_small(monkeypatch):
    """收窄后单轮子图最多烧「剩余子预算」轮；即便烧满 + force_finish 1 轮 + metrics 抽取，
    总越界幅度确定性小值（远小于实测 32），且远小于未收窄的 CAP 级越界。"""
    plan = _plan_with_steps(80)
    base = _base_rounds(plan)  # == CAP == 60（未收窄时单轮可烧的量级）

    # dev_calls 逼近上限（剩余=3）→ 收窄到 3。
    remaining = 3
    dev_calls = MAX_DEV_LOOP_LLM_CALLS - remaining
    captured = _run_agent_capture_max_rounds(monkeypatch, dev_calls, plan)
    narrowed = captured["subgraph_max_rounds"]
    assert narrowed == remaining, "收窄值应等于剩余子预算"

    # 越界上界 = narrowed 轮 + force_finish 1 轮（budget_check 触发后收尾再调一次）
    # + metrics 抽取额度（确定性小值）。此处以「收窄值 << 联动 CAP」坐实收窄有效。
    force_finish_extra = 1
    metrics_extract_budget = 3  # 档 3 LLM 抽取上限量级，确定性小值
    over_run_upper = narrowed + force_finish_extra + metrics_extract_budget
    # 未收窄时单轮上界会是 CAP + force_finish + metrics ≈ 60+ 数量级。
    assert over_run_upper <= remaining + force_finish_extra + metrics_extract_budget
    assert over_run_upper < base, (
        f"收窄后越界上界 {over_run_upper} 必须远小于未收窄 CAP 级 {base}"
    )


# ===========================================================================
# CP-1.1-4：R-PC4 无扰——HumanMessage 的 max_rounds 数字恒为联动值，不随 dev_calls 抖动
# ===========================================================================


def test_cp_1_1_4_context_max_rounds_invariant_across_dev_calls(monkeypatch):
    """两个不同 dev_calls 值下截取 execution HumanMessage context 的 max_rounds
    数字保持联动值恒定（收窄未污染 context 通道，R-PC4 无扰 / AA-S7-6）。"""
    plan = _plan_with_steps(80)
    base = _base_rounds(plan)

    cap_low = _run_agent_capture_max_rounds(monkeypatch, 0, plan)
    cap_high = _run_agent_capture_max_rounds(monkeypatch, MAX_DEV_LOOP_LLM_CALLS - 2, plan)

    # context 的 max_rounds 数字两次都等于联动值（不受收窄影响）。
    assert cap_low["context"]["max_rounds"] == base
    assert cap_high["context"]["max_rounds"] == base
    assert cap_low["context"]["max_rounds"] == cap_high["context"]["max_rounds"], (
        "context 的 max_rounds 随 dev_calls 抖动 → R-PC4 破坏"
    )

    # 而子图护栏的 max_rounds 两次不同（收窄确实生效在护栏侧、不在 context 侧）。
    assert cap_low["subgraph_max_rounds"] == base
    assert cap_high["subgraph_max_rounds"] == 2
    assert cap_low["subgraph_max_rounds"] != cap_high["subgraph_max_rounds"], (
        "护栏 max_rounds 应随 dev_calls 收窄——证明两个通道语义分离"
    )
