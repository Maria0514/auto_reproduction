"""Sprint 7 任务 T-S7-1-4（S7-01，架构 §2.3/§2.4/§1.2）：预算门下沉 + reason 链 + revise 预算重置。

覆盖 dev-plan §5 T-S7-1-4 自测检查点 CP-1.4-1 ~ CP-1.4-6（AC-S7-01/02/03/04）：
    - CP-1.4-1 路由不再静默降级：budget=0/success=False → 不再 _mark_degraded_for_report，
      而是置 _dev_loop_route="await_dev_loop_interrupt"（首次进入 already_committed=False）；
    - CP-1.4-2 两段式幂等：首次 return await 标记；self-loop 重入 already_committed=True 函数体 interrupt 恰一次；
    - CP-1.4-3 面板文案 + 三态守门：预算耗尽 → error_summary 含预算耗尽关键词；对照用例不含；
      payload 键集合与 sp6 逐字一致；options==["terminate","revise_plan","export_code"]；
    - CP-1.4-4 硬上限守门：dev_calls=120 / retry_budget 达顶 → 不突破 240/120；revise 后子上限仍拦；
    - CP-1.4-5 revise 预算重置：revise_plan → retry_budget_remaining==240 + fix_loop_count==0；_dev_loop_llm_calls 未重置；
    - CP-1.4-6 R-S7-1 对照防误伤：预算充足失败 → 仍正常回 coding 修复（路由未被误伤）。

全离线（InMemorySaver + mock sandbox agent），零 API 配额。
"""

from __future__ import annotations

import importlib
from typing import Any, Dict, List, Optional

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

import config
from core.state import ExecutionMode

execution_module = importlib.import_module("core.nodes.execution")

from config import (  # noqa: E402
    DEV_LOOP_MIN_CALLS_PER_ROUND,
    MAX_DEV_LOOP_LLM_CALLS,
    MAX_TOTAL_LLM_CALLS,
)
from sandbox.local_venv import SandboxPrepareResult, SandboxRunResult  # noqa: E402

ErrorCategory = execution_module.ErrorCategory
ExecutionFeedback = execution_module.ExecutionFeedback
NODE_NAME = execution_module.NODE_NAME
INTERRUPT_KIND = execution_module.INTERRUPT_KIND
_ROUTE_AWAIT_INTERRUPT = execution_module._ROUTE_AWAIT_INTERRUPT
_BUDGET_EXHAUSTED_SUMMARY = execution_module._BUDGET_EXHAUSTED_SUMMARY


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _exec_result(success: bool = False, errors=None) -> Dict[str, Any]:
    return {
        "success": success, "metrics": {},
        "logs": "ModuleNotFoundError: No module named 'src'",
        "errors": errors or ["[error_category=import] import 错误"],
        "artifacts": [], "runtime_seconds": 0.0, "environment_info": {},
        "step_reconciliation": {}, "budget_truncated": False,
        "metrics_groups": {}, "degraded_credentials": [],
    }


def _feedback(category=ErrorCategory.IMPORT, auto_fixable=True) -> ExecutionFeedback:
    return ExecutionFeedback(category, auto_fixable, "import 错误摘要", "装缺失的包", "")


def _state(**overrides: Any) -> Dict[str, Any]:
    state: Dict[str, Any] = {
        "fix_loop_count": 0,
        "retry_budget_remaining": 40,
        "_dev_loop_llm_calls": 0,
        "_dev_loop_route": None,
        "fix_loop_history": [],
        "node_errors": [],
        "degraded_nodes": [],
        "execution_result": None,
        "reproduction_plan": {"approved": True},
    }
    state.update(overrides)
    return state


def _prep(success: bool = True) -> SandboxPrepareResult:
    return SandboxPrepareResult(
        success=success, venv_dir="/w/.venv", python_exe="/w/.venv/bin/python", pip_exe="",
        env_info={"python_version": "Python 3.11"}, install_log="ok",
        install_failed_packages=[], error=None,
    )


def _run(exit_code=1, stderr="ModuleNotFoundError: No module named 'src'") -> SandboxRunResult:
    return SandboxRunResult(
        exit_code=exit_code, stdout="", stderr=stderr,
        duration_seconds=0.1, timed_out=False, output_truncated=False,
        command=["python", "train.py"],
    )


# ===========================================================================
# CP-1.4-1：路由不再静默降级（AC-S7-01）
# ===========================================================================


def test_cp_1_4_1_no_silent_degrade_first_entry():
    """budget<MIN/success=False 首次进入（already_committed=False）→ 不再 _mark_degraded_for_report
    （degraded_nodes 不含 execution budget_exhausted 降级），而是置 await 标记。以同构 mock state 为回归靶。"""
    updates: Dict[str, Any] = {"execution_result": _exec_result(), "current_step": NODE_NAME}
    # checkpoints_s7_99eef17bccf2.db 同构 state：budget=0 / success=False / _dev_loop_llm_calls=92 /
    # fix_loop_history 4 条全 import（此处用同构 mock，不依赖尚不存在的 fixture db）。
    state = _state(
        retry_budget_remaining=0,
        _dev_loop_llm_calls=92,
        fix_loop_count=4,
        fix_loop_history=[{"error_category": "import"} for _ in range(4)],
    )
    out = execution_module._maybe_interrupt_or_return(
        updates, _exec_result(), _feedback(), state, already_committed=False
    )
    # AC-S7-01 核心：不再静默降级。
    assert NODE_NAME not in out.get("degraded_nodes", []), "不再 _mark_degraded_for_report"
    assert not any(
        "budget_exhausted" in (e.get("error_message") or "") for e in out.get("node_errors", [])
    ), "不再写 budget_exhausted 降级 NodeError"
    # 首次进入置两段式 await 标记。
    assert out.get("_dev_loop_route") == _ROUTE_AWAIT_INTERRUPT
    assert "user_fix_decision" not in out  # 首次进入尚未 interrupt


def test_cp_1_4_1_degrade_return_deleted_from_function():
    """预算门降级 return 已从 _maybe_interrupt_or_return 删除——预算耗尽不再有独立降级旁路。"""
    import inspect
    src = inspect.getsource(execution_module._maybe_interrupt_or_return)
    # 删除的旧代码：`if budget < DEV_LOOP_MIN_CALLS_PER_ROUND: return _mark_degraded_for_report(...`
    assert 'reason="budget_exhausted"' not in src, "budget_exhausted 降级 return 应已删除"
    # 预算门下沉为修复准入条件（and budget >= DEV_LOOP_MIN_CALLS_PER_ROUND）。
    assert "budget >= DEV_LOOP_MIN_CALLS_PER_ROUND" in src, "预算门应下沉为修复准入否决条件"


# ===========================================================================
# CP-1.4-2：两段式幂等（AC-S7-02）——首次 return await；重入 already_committed 函数体 interrupt 恰一次
# ===========================================================================


def _build_self_loop_graph(checkpointer):
    from langgraph.graph import START, END, StateGraph
    from core.state import GlobalState

    g = StateGraph(GlobalState)
    g.add_node("execution", execution_module.execution)
    g.add_edge(START, "execution")

    def route(state: Dict[str, Any]) -> str:
        if state.get("_dev_loop_route") == _ROUTE_AWAIT_INTERRUPT:
            return "execution"
        return "end"

    g.add_conditional_edges("execution", route, {"execution": "execution", "end": END})
    return g.compile(checkpointer=checkpointer)


def _graph_state(tmp_path, **overrides) -> Dict[str, Any]:
    code_dir = tmp_path / "code"
    code_dir.mkdir(parents=True, exist_ok=True)
    state = {
        "llm_config_set": {"default": {"model": "test"}},
        "code_output_dir": str(code_dir),
        "reproduction_plan": {"execution_steps": [{"command": "python train.py"}], "environment": {}, "approved": True},
        "paper_analysis": {"metrics": []},
        "execution_mode": ExecutionMode.FULL,
        "node_errors": [], "degraded_nodes": [], "fix_loop_history": [],
        "fix_loop_count": 4, "retry_budget_remaining": 0,
        "_dev_loop_llm_calls": 92, "_dev_loop_route": None,
        "execution_result": None, "current_step": "coding",
    }
    state.update(overrides)
    return state


def _patch_agent_count(monkeypatch) -> Dict[str, int]:
    cnt = {"agent": 0}

    def fake_agent(state, wd, plan):
        cnt["agent"] += 1
        return execution_module.ExecAgentOutput(
            prep=_prep(), run_results=[_run()], rounds_used=1, llm_calls=0,
        )

    monkeypatch.setattr(execution_module, "_run_execution_agent", fake_agent)
    monkeypatch.setattr(execution_module, "collect_artifacts", lambda *a, **k: [])
    monkeypatch.setattr(execution_module, "_llm_extract_metrics", lambda *a, **k: ({}, 0))
    return cnt


def test_cp_1_4_2_two_phase_idempotent_budget_exhausted(tmp_path, monkeypatch):
    """预算耗尽经两段式抵达 interrupt#2：首次 return await 标记（sandbox 跑 1 次），
    self-loop 重入 already_committed=True 函数体 interrupt 恰一次（guard 命中零重跑）。"""
    cnt = _patch_agent_count(monkeypatch)
    graph = _build_self_loop_graph(InMemorySaver())
    cfg = {"configurable": {"thread_id": "s7-14-2"}}

    out = graph.invoke(_graph_state(tmp_path), cfg)
    assert "__interrupt__" in out, "预算耗尽应经两段式抵达 interrupt#2"
    # guard 命中：agent 恰跑 1 次（重入跳过 sandbox）。
    assert cnt["agent"] == 1, "self-loop 重入必须 guard 命中、sandbox 不重跑"

    payload = out["__interrupt__"][0].value
    assert payload["interrupt_kind"] == INTERRUPT_KIND


# ===========================================================================
# CP-1.4-3：面板文案 + 三态守门（AC-S7-03）
# ===========================================================================


def _interrupt_payload_via_guard(state, feedback) -> Dict[str, Any]:
    """构造 already_committed=True 直接触发函数体 interrupt，捕获 payload。"""
    from langgraph.errors import GraphBubbleUp

    captured = {}
    real_interrupt = execution_module.interrupt

    def fake_interrupt(payload):
        captured["payload"] = payload
        raise GraphBubbleUp("stop")

    execution_module.interrupt = fake_interrupt
    try:
        updates = {"execution_result": _exec_result(), "current_step": NODE_NAME}
        try:
            execution_module._maybe_interrupt_or_return(
                updates, _exec_result(), feedback, state, already_committed=True
            )
        except GraphBubbleUp:
            pass
    finally:
        execution_module.interrupt = real_interrupt
    return captured.get("payload", {})


def test_cp_1_4_3_budget_exhausted_panel_text_and_three_state():
    """预算耗尽 → 面板 error_summary 含预算耗尽关键词；options 三态无第四态；payload 键与 sp6 一致。"""
    state = _state(retry_budget_remaining=0, _dev_loop_llm_calls=50, fix_loop_count=4)
    payload = _interrupt_payload_via_guard(state, _feedback())

    assert "预算已耗尽" in payload["error_summary"], "面板文案应含预算耗尽语义（AC-S7-03）"
    assert payload["fix_hint"] == _BUDGET_EXHAUSTED_SUMMARY, "fix_hint 走 replace 注入"
    assert payload["options"] == ["terminate", "revise_plan", "export_code"], "三态无第四态"
    # payload 键集合与 sp6 逐字一致（防新增 payload 键）。
    assert set(payload.keys()) == {
        "interrupt_kind", "fix_loop_count", "error_category", "error_summary",
        "fix_hint", "auto_fixable", "fix_loop_history", "execution_errors",
        "representative_stderr", "options",
    }, "interrupt#2 payload 键集合须与 sp6 逐字一致（零新键）"


def test_cp_1_4_3_control_non_budget_no_budget_text():
    """对照用例（AC-S7-03 防文案泛化）：预算充足 + 子上限触顶 → error_summary 不含预算耗尽文案。"""
    state = _state(
        retry_budget_remaining=40,  # 预算充足
        _dev_loop_llm_calls=MAX_DEV_LOOP_LLM_CALLS,  # 子上限触顶
        fix_loop_count=2,
    )
    payload = _interrupt_payload_via_guard(state, _feedback())
    assert "预算已耗尽" not in payload["error_summary"], "非预算耗尽情形不得含预算耗尽文案"


# ===========================================================================
# CP-1.4-4：硬上限守门（AC-S7-04）
# ===========================================================================


def test_cp_1_4_4_dev_loop_ceiling_still_blocks_after_revise():
    """revise 预算重置后再验子上限：dev_calls>=120 → 仍不回 coding（子上限硬顶不被预算重置绕过）。"""
    # revise 后 retry_budget_remaining=240（重满），但 _dev_loop_llm_calls 未重置=120（触顶）。
    updates: Dict[str, Any] = {"execution_result": _exec_result(), "current_step": NODE_NAME}
    state = _state(
        retry_budget_remaining=MAX_TOTAL_LLM_CALLS,  # revise 重满
        _dev_loop_llm_calls=MAX_DEV_LOOP_LLM_CALLS,  # 子上限触顶（未被重置）
        fix_loop_count=1,
    )
    out = execution_module._maybe_interrupt_or_return(
        updates, _exec_result(), _feedback(), state, already_committed=False
    )
    # 子上限触顶 → 不回 coding（fix_loop_count 不自增），落两段式（await）。
    assert "fix_loop_count" not in out, "子上限触顶不回 coding（不突破 120）"
    assert out.get("_dev_loop_route") == _ROUTE_AWAIT_INTERRUPT


def test_cp_1_4_4_budget_reset_does_not_exceed_total_cap():
    """revise 全额重置为 MAX_TOTAL_LLM_CALLS（240），不突破 240 硬顶。"""
    out = execution_module._route_user_fix_decision(
        {"decision": "revise_plan"}, {}, _state(retry_budget_remaining=0, _dev_loop_llm_calls=100)
    )
    assert out["retry_budget_remaining"] == MAX_TOTAL_LLM_CALLS
    assert out["retry_budget_remaining"] <= 240, "不突破 240 硬顶"


# ===========================================================================
# CP-1.4-5：revise 预算重置（AC-S7-04）
# ===========================================================================


def test_cp_1_4_5_revise_resets_budget_not_dev_calls():
    """revise_plan → retry_budget_remaining==240 + fix_loop_count==0；_dev_loop_llm_calls 累计未重置。"""
    state = _state(retry_budget_remaining=0, _dev_loop_llm_calls=92, fix_loop_count=4)
    out = execution_module._route_user_fix_decision(
        {"decision": "revise_plan"}, {}, state
    )
    assert out["retry_budget_remaining"] == MAX_TOTAL_LLM_CALLS, "revise 全额重置=240"
    assert out["fix_loop_count"] == 0, "revise 清零 fix_loop_count"
    # _dev_loop_llm_calls 累计不重置（子上限硬顶继续生效，R-S7-2）。
    assert "_dev_loop_llm_calls" not in out, "revise 不重置 _dev_loop_llm_calls（子上限硬顶继续生效）"


def test_cp_1_4_5_terminate_export_no_budget_reset():
    """对照：terminate / export_code 分支不做预算重置（只有 revise_plan 重置）。"""
    out_t = execution_module._route_user_fix_decision(
        {"decision": "terminate"}, {}, _state(retry_budget_remaining=0)
    )
    assert "retry_budget_remaining" not in out_t, "terminate 不重置预算"
    out_e = execution_module._route_user_fix_decision(
        {"decision": "export_code"}, {}, _state(retry_budget_remaining=0)
    )
    assert "retry_budget_remaining" not in out_e, "export_code 不重置预算"


# ===========================================================================
# CP-1.4-6：R-S7-1 对照防误伤——预算充足失败仍正常回 coding 修复
# ===========================================================================


def test_cp_1_4_6_sufficient_budget_still_retries_coding():
    """预算充足（budget >= MIN）+ auto_fixable → 仍正常回 coding 修复（路由未被预算门下沉误伤）。"""
    updates: Dict[str, Any] = {"execution_result": _exec_result(), "current_step": NODE_NAME}
    state = _state(
        retry_budget_remaining=40,  # 预算充足
        _dev_loop_llm_calls=10,  # 子上限未触顶
        fix_loop_count=1,  # 未超修复回合上限
    )
    out = execution_module._maybe_interrupt_or_return(
        updates, _exec_result(), _feedback(), state, already_committed=False
    )
    # 正常回 coding 修复。
    assert out["_dev_loop_route"] == "retry_coding", "预算充足失败应回 coding 修复（未被误伤）"
    assert out["fix_loop_count"] == 2, "回 coding 单点自增"


def test_cp_1_4_6_budget_gate_lowered_boundary():
    """边界：budget 恰等于 MIN → 满足准入（>= MIN），仍回 coding；budget = MIN-1 → 不回 coding。"""
    # budget == MIN → 回 coding。
    out_ok = execution_module._maybe_interrupt_or_return(
        {"execution_result": _exec_result()}, _exec_result(), _feedback(),
        _state(retry_budget_remaining=DEV_LOOP_MIN_CALLS_PER_ROUND, _dev_loop_llm_calls=0, fix_loop_count=0),
        already_committed=False,
    )
    assert out_ok["_dev_loop_route"] == "retry_coding", "budget==MIN 应满足准入回 coding"

    # budget == MIN-1 → 不回 coding（预算门下沉否决），落 await。
    out_no = execution_module._maybe_interrupt_or_return(
        {"execution_result": _exec_result()}, _exec_result(), _feedback(),
        _state(retry_budget_remaining=DEV_LOOP_MIN_CALLS_PER_ROUND - 1, _dev_loop_llm_calls=0, fix_loop_count=0),
        already_committed=False,
    )
    assert out_no.get("_dev_loop_route") == _ROUTE_AWAIT_INTERRUPT, "budget<MIN 不回 coding"
    assert "fix_loop_count" not in out_no
