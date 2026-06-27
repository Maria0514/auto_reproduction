"""C3 - execution 节点真实现 + 修复循环边界 + interrupt#2 自测（Sprint 3，S3-03/04/07）。

覆盖 dev-plan.md 任务 C3 的 14 个自测检查点（CP-C3-1 ~ CP-C3-14）。

约束（CLAUDE 指令 + 任务要求）：
    - **mock sandbox**（patch prepare_venv / run_in_venv / collect_artifacts），不跑真实
      venv/子进程，不发 LLM/deepxiv 请求（省配额，e2e 留 F 阶段）；
    - interrupt#2 三态 resume / 重跑幂等用最小 StateGraph + InMemorySaver + Command(resume=...)
      跑真实 interrupt（参考 S-1 spike 与 sp2 planning e2e 范式，但 sandbox 全 mock）；
    - 用 importlib.import_module 拿真实子模块（core/nodes/__init__.py 显式 export execution
      callable 会遮蔽子模块属性，BUG-S1-02/C2 教训）。
"""
from __future__ import annotations

import importlib
import inspect
import logging
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# importlib 拿真实子模块（避免 __init__ callable 遮蔽）。
execution_module = importlib.import_module("core.nodes.execution")

from config import (  # noqa: E402
    DEV_LOOP_MIN_CALLS_PER_ROUND,
    MAX_DEV_LOOP_LLM_CALLS,
    MAX_FIX_LOOP_COUNT,
    WORKSPACE_DIR,
)
from core.nodes.execution import (  # noqa: E402
    AUTO_FIXABLE,
    INTERRUPT_KIND,
    NODE_NAME,
    ErrorCategory,
    ExecutionFeedback,
    _build_execution_result,
    _classify_execution,
    _extract_metrics_block,
    _map_category_to_error_type,
    _map_execution_result,
    _parse_metrics,
    _regex_scan_metrics,
    execution,
)
from core.state import ExecutionMode  # noqa: E402


# ---------------------------------------------------------------------------
# 伪 sandbox dataclass（与 sandbox.local_venv 的 SandboxRunResult/SandboxPrepareResult 同构）
# ---------------------------------------------------------------------------


@dataclass
class FakeRunResult:
    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""
    duration_seconds: float = 0.5
    timed_out: bool = False
    output_truncated: bool = False
    command: List[str] = field(default_factory=lambda: ["python", "run.py"])


@dataclass
class FakePrepareResult:
    success: bool = True
    venv_dir: str = "/tmp/ws/.venv"
    python_exe: str = "/tmp/ws/.venv/bin/python"
    pip_exe: str = "/tmp/ws/.venv/bin/pip"
    env_info: Dict[str, str] = field(default_factory=lambda: {"python_version": "3.11"})
    install_log: str = ""
    install_failed_packages: List[str] = field(default_factory=list)
    error: Optional[str] = None


def _work_dir() -> str:
    """构造一个 workspace 下的合法 work_dir（mock 不真正读写）。"""
    d = WORKSPACE_DIR / "c3-test" / "code"
    return str(d)


def _base_state(**overrides: Any) -> Dict[str, Any]:
    """构造 execution 节点最小可用 state（FULL 模式，预算充足）。"""
    state: Dict[str, Any] = {
        "code_output_dir": _work_dir(),
        "reproduction_plan": {
            "execution_steps": [{"step_name": "run", "command": "python run.py"}],
            "environment": {},
        },
        "paper_analysis": {"metrics": ["accuracy", "f1"]},
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


def _patch_sandbox(
    monkeypatch: pytest.MonkeyPatch,
    *,
    prep: Optional[FakePrepareResult] = None,
    run_results: Optional[List[FakeRunResult]] = None,
    artifacts: Optional[List[str]] = None,
    counter: Optional[Dict[str, int]] = None,
) -> Dict[str, int]:
    """patch execution_module 内引用的 sandbox 三函数。返回调用计数 dict。"""
    cnt = counter if counter is not None else {"prepare": 0, "run": 0}
    prep_obj = prep if prep is not None else FakePrepareResult()
    runs = run_results if run_results is not None else [FakeRunResult(exit_code=0, stdout="<METRICS>{\"accuracy\": 0.9}</METRICS>")]
    arts = artifacts if artifacts is not None else []
    run_iter = iter(runs)

    def fake_prepare_venv(*args: Any, **kwargs: Any) -> FakePrepareResult:
        cnt["prepare"] = cnt.get("prepare", 0) + 1
        return prep_obj

    def fake_run_in_venv(*args: Any, **kwargs: Any) -> FakeRunResult:
        cnt["run"] = cnt.get("run", 0) + 1
        try:
            return next(run_iter)
        except StopIteration:
            return runs[-1] if runs else FakeRunResult()

    def fake_collect_artifacts(*args: Any, **kwargs: Any) -> List[str]:
        return list(arts)

    monkeypatch.setattr(execution_module, "prepare_venv", fake_prepare_venv)
    monkeypatch.setattr(execution_module, "run_in_venv", fake_run_in_venv)
    monkeypatch.setattr(execution_module, "collect_artifacts", fake_collect_artifacts)
    return cnt


# ===========================================================================
# CP-C3-1：可导入 + 签名 + 本地对象不在 state.py
# ===========================================================================


def test_cp_c3_1_importable_and_local_objects():
    assert callable(execution)
    sig = inspect.signature(execution)
    params = list(sig.parameters)
    assert params == ["state"], f"execution 签名应为 (state)，实际 {params}"

    # ErrorCategory / ExecutionFeedback / AUTO_FIXABLE 为节点本地对象，不在 core/state.py。
    import core.state as st

    assert not hasattr(st, "ErrorCategory"), "ErrorCategory 不应出现在 core/state.py"
    assert not hasattr(st, "ExecutionFeedback"), "ExecutionFeedback 不应出现在 core/state.py"
    assert not hasattr(st, "AUTO_FIXABLE"), "AUTO_FIXABLE 不应出现在 core/state.py"
    # AUTO_FIXABLE 集合内容正确。
    assert AUTO_FIXABLE == {
        ErrorCategory.SYNTAX,
        ErrorCategory.IMPORT,
        ErrorCategory.DEPENDENCY,
        ErrorCategory.PATH,
        ErrorCategory.RUNTIME,
    }


# ===========================================================================
# CP-C3-2：B 档成功（AC-S3-01）—— exit 0 + 可解析 metrics → success=True，出边到 reporting
# ===========================================================================


def test_cp_c3_2_b_grade_success(monkeypatch):
    cnt = _patch_sandbox(
        monkeypatch,
        run_results=[FakeRunResult(exit_code=0, stdout="<METRICS>{\"accuracy\": 0.91, \"f1\": 0.88}</METRICS>")],
    )
    state = _base_state()
    out = execution(state)

    er = out["execution_result"]
    assert er["success"] is True
    assert len(er["metrics"]) >= 1
    assert er["metrics"]["accuracy"] == 0.91
    assert out["current_step"] == NODE_NAME
    # 成功 → 不回 coding、不 interrupt：无 retry_coding 路由、无 fix_loop_count 自增。
    assert out.get("_dev_loop_route") is None
    assert "fix_loop_count" not in out
    assert "user_fix_decision" not in out
    # 成功不写 node_errors。
    assert out["node_errors"] == []
    assert cnt["prepare"] == 1


def test_cp_c3_2b_exit0_no_metrics_not_success(monkeypatch):
    """exit 0 但无任何指标（B 档要求 ≥1 指标）→ success=False。"""
    _patch_sandbox(
        monkeypatch,
        run_results=[FakeRunResult(exit_code=0, stdout="done, nothing to report")],
        # 避免 LLM 抽取兜底真发请求：mock 它返回空。
    )
    monkeypatch.setattr(execution_module, "_llm_extract_metrics", lambda *a, **k: ({}, 1))
    state = _base_state(paper_analysis={"metrics": []})
    out = execution(state)
    assert out["execution_result"]["success"] is False


# ===========================================================================
# CP-C3-3：错误分类分流（AC-S3-08）—— 可修复 vs 不可修复两类断言
# ===========================================================================


def test_cp_c3_3_classify_auto_fixable_split():
    prep = FakePrepareResult(success=True)

    fixable_cases = {
        "modulenotfounderror: no module named 'torch'": ErrorCategory.IMPORT,
        "  File x, line 1\nSyntaxError: invalid syntax": ErrorCategory.SYNTAX,
        "FileNotFoundError: [Errno 2] No such file: 'config.yaml'": ErrorCategory.PATH,
        "ValueError: something went wrong at runtime": ErrorCategory.RUNTIME,
    }
    for stderr, expected_cat in fixable_cases.items():
        fb = _classify_execution(prep, [FakeRunResult(exit_code=1, stderr=stderr)])
        assert fb.category == expected_cat, f"{stderr!r} -> {fb.category}"
        assert fb.auto_fixable is True

    # 依赖装不上（prep 失败）。
    prep_fail = FakePrepareResult(success=False, install_failed_packages=["torch==9.9"])
    fb_dep = _classify_execution(prep_fail, [])
    assert fb_dep.category == ErrorCategory.DEPENDENCY
    assert fb_dep.auto_fixable is True

    # 不可修复类。
    unfixable_cases = {
        "RuntimeError: CUDA out of memory": ErrorCategory.HARDWARE,
        "Please download the dataset from http://...": ErrorCategory.DATA_MISSING,
    }
    for stderr, expected_cat in unfixable_cases.items():
        fb = _classify_execution(prep, [FakeRunResult(exit_code=1, stderr=stderr)])
        assert fb.category == expected_cat, f"{stderr!r} -> {fb.category}"
        assert fb.auto_fixable is False

    # 超时（不可修复）。
    fb_to = _classify_execution(prep, [FakeRunResult(exit_code=-1, timed_out=True, stderr="killed")])
    assert fb_to.category == ErrorCategory.TIMEOUT
    assert fb_to.auto_fixable is False

    # 全 exit 0 → NONE。
    fb_ok = _classify_execution(prep, [FakeRunResult(exit_code=0)])
    assert fb_ok.category == ErrorCategory.NONE


def test_cp_c3_3b_hardware_before_runtime():
    """关键字顺序敏感：硬件/数据缺失先于通用 runtime（同时含 runtime 字样时仍判硬件）。"""
    prep = FakePrepareResult(success=True)
    fb = _classify_execution(
        prep,
        [FakeRunResult(exit_code=1, stderr="RuntimeError: CUDA out of memory; tried to allocate")],
    )
    assert fb.category == ErrorCategory.HARDWARE


# ===========================================================================
# CP-C3-4：修复回边计数（AC-S3-03 ①②）—— 可修复 + 未超限 + 预算够
#           → fix_loop_count+1 + fix_loop_history append + _dev_loop_route=retry_coding
# ===========================================================================


def test_cp_c3_4_retry_coding_increments(monkeypatch):
    _patch_sandbox(
        monkeypatch,
        run_results=[FakeRunResult(exit_code=1, stderr="ModuleNotFoundError: No module named 'foo'")],
    )
    state = _base_state(fix_loop_count=0, retry_budget_remaining=40, _dev_loop_llm_calls=0)
    out = execution(state)

    assert out["execution_result"]["success"] is False
    assert out["fix_loop_count"] == 1  # 单点自增
    assert out["_dev_loop_route"] == "retry_coding"
    assert "user_fix_decision" not in out  # 不 interrupt
    history = out["fix_loop_history"]
    assert len(history) == 1
    rec = history[0]
    # FixLoopRecord 5 字段。
    assert set(rec.keys()) == {
        "round_number", "error_summary", "error_category", "fix_strategy", "timestamp"
    }
    assert rec["round_number"] == 1
    assert rec["error_category"] == "import"


# ===========================================================================
# CP-C3-5：上限拦截（AC-S3-03 ③）—— fix_loop_count==MAX 时不再回 coding、不自增，转 interrupt#2
# ===========================================================================


def test_cp_c3_5_upper_limit_to_interrupt(monkeypatch):
    """fix_loop_count==3（已达上限）+ 可修复失败 → 不回 coding、不自增。

    首次进入（已 commit=False）→ 置 await 标记 return（不 interrupt），不自增 fix_loop_count。
    """
    _patch_sandbox(
        monkeypatch,
        run_results=[FakeRunResult(exit_code=1, stderr="RuntimeError: oops")],
    )
    state = _base_state(fix_loop_count=MAX_FIX_LOOP_COUNT)  # ==3
    out = execution(state)
    # 不回 coding（无 retry_coding），不自增。
    assert out.get("_dev_loop_route") == "await_dev_loop_interrupt"
    assert "fix_loop_count" not in out  # 绝不自增
    assert out["execution_result"]["success"] is False


# ===========================================================================
# CP-C3-6：不可修复不重试（AC-S3-08 ②）—— 不可修复类 → 不自增、不回 coding → 转 interrupt#2 路径
# ===========================================================================


def test_cp_c3_6_unfixable_no_retry(monkeypatch):
    _patch_sandbox(
        monkeypatch,
        run_results=[FakeRunResult(exit_code=1, stderr="RuntimeError: CUDA out of memory")],
    )
    state = _base_state(fix_loop_count=0)
    out = execution(state)
    assert out["execution_result"]["success"] is False
    assert "fix_loop_count" not in out  # 不可修复不自增
    assert out.get("_dev_loop_route") == "await_dev_loop_interrupt"  # 走 interrupt 准备，不回 coding
    # 不可修复类映射 permanent。
    last_err = out["node_errors"][-1]
    assert last_err["error_type"] == "permanent"
    assert "[error_category=hardware]" in last_err["error_message"]


# ===========================================================================
# 最小 StateGraph：模拟 D1 的 self-loop 路由（execution await → execution；否则 → END）
# 用于 CP-C3-7（interrupt#2 三态 resume）+ CP-C3-13（重跑幂等）。
# ===========================================================================


def _build_self_loop_graph(checkpointer):
    from langgraph.graph import START, END, StateGraph

    from core.state import GlobalState

    g = StateGraph(GlobalState)
    g.add_node("execution", execution)
    g.add_edge(START, "execution")

    def route(state: Dict[str, Any]) -> str:
        # 模拟 D1 _route_after_execution 的 self-loop 关键分支：
        #   _dev_loop_route == "await_dev_loop_interrupt" → 重入 execution（commit 边界后再 interrupt）；
        #   其余（retry_coding / None / 三态决策）→ END（测试只关心 interrupt 暂停 + resume）。
        if state.get("_dev_loop_route") == "await_dev_loop_interrupt":
            return "execution"
        return "end"

    g.add_conditional_edges("execution", route, {"execution": "execution", "end": END})
    return g.compile(checkpointer=checkpointer)


def _make_saver():
    try:
        from langgraph.checkpoint.memory import InMemorySaver

        return InMemorySaver()
    except Exception:  # pragma: no cover
        from langgraph.checkpoint.memory import MemorySaver

        return MemorySaver()


def _run_to_interrupt(graph, state, config, monkeypatch_counter):
    """invoke 跑到 interrupt 暂停，返回 invoke 输出。"""
    return graph.invoke(state, config)


# ===========================================================================
# CP-C3-7：interrupt#2 三态 resume（AC-S3-07）
# ===========================================================================


@pytest.mark.parametrize("decision", ["terminate", "revise_plan", "export_code"])
def test_cp_c3_7_interrupt_three_state_resume(monkeypatch, decision):
    from langgraph.types import Command

    # mock sandbox：可修复失败但 fix_loop_count 已达上限 → 走 interrupt#2。
    cnt = _patch_sandbox(
        monkeypatch,
        run_results=[FakeRunResult(exit_code=1, stderr="RuntimeError: boom")],
    )

    saver = _make_saver()
    graph = _build_self_loop_graph(saver)
    thread_id = f"c3-7-{decision}-{uuid.uuid4().hex[:8]}"
    config = {"configurable": {"thread_id": thread_id}}

    init = _base_state(fix_loop_count=MAX_FIX_LOOP_COUNT)
    out1 = graph.invoke(init, config)
    assert "__interrupt__" in out1, f"未在 execution 暂停：keys={list(out1.keys())}"

    # interrupt payload 含 interrupt_kind="dev_loop_failure"。
    snap = graph.get_state(config)
    interrupts = [
        iv for task in snap.tasks for iv in (getattr(task, "interrupts", None) or [])
    ]
    assert interrupts, "snapshot 无 interrupt 元数据"
    payload = interrupts[0].value
    assert isinstance(payload, dict)
    assert payload.get("interrupt_kind") == INTERRUPT_KIND

    # resume 注入三态决策。
    extra = {}
    if decision == "revise_plan":
        extra = {"user_feedback": "请改用更小的模型"}
    out2 = graph.invoke(Command(resume={"decision": decision, **extra}), config)

    final = graph.get_state(config).values

    if decision == "terminate":
        assert final["user_fix_decision"] == "terminate"
        assert final["current_step"] == "cancelled_by_user"
    elif decision == "revise_plan":
        assert final["user_fix_decision"] == "revise_plan"
        assert final["_planning_user_feedback"]  # 修复上下文非空
        assert final["reproduction_plan"]["approved"] is False
        # 回问点 2：fix_loop_count 清零、fix_loop_history 保留。
        assert final["fix_loop_count"] == 0
    elif decision == "export_code":
        assert final["user_fix_decision"] == "export_code"
        assert NODE_NAME in final["degraded_nodes"]

    # sandbox 重跑幂等：整个流程 sandbox 只跑 1 次（重入命中 guard + resume 重跑命中 guard）。
    assert cnt["prepare"] == 1, f"sandbox prepare 调用 {cnt['prepare']} 次（期望 1）"


def test_cp_c3_7b_illegal_resume_defaults_terminate(monkeypatch):
    """非法 resume payload 兜底视为 terminate（不空转）。"""
    from langgraph.types import Command

    _patch_sandbox(monkeypatch, run_results=[FakeRunResult(exit_code=1, stderr="RuntimeError: x")])
    saver = _make_saver()
    graph = _build_self_loop_graph(saver)
    config = {"configurable": {"thread_id": f"c3-7b-{uuid.uuid4().hex[:8]}"}}
    graph.invoke(_base_state(fix_loop_count=MAX_FIX_LOOP_COUNT), config)
    graph.invoke(Command(resume={"garbage": "no decision key"}), config)
    final = graph.get_state(config).values
    assert final["user_fix_decision"] == "terminate"
    assert final["current_step"] == "cancelled_by_user"


# ===========================================================================
# CP-C3-8：预算回写（AC-S3-04 ①）—— metrics LLM 抽取触发时单点回写；不触发时零扣减
# ===========================================================================


def test_cp_c3_8_budget_writeback_on_llm_extract(monkeypatch):
    # exit 0 + stdout 非空 + 无结构化标签 + 无正则命中 → 触发 LLM 抽取兜底。
    _patch_sandbox(
        monkeypatch,
        run_results=[FakeRunResult(exit_code=0, stdout="final result printed but unstructured")],
    )
    # mock LLM 抽取返回 1 个指标 + 消耗 1 次。
    monkeypatch.setattr(
        execution_module, "_llm_extract_metrics", lambda *a, **k: ({"accuracy": 0.8}, 1)
    )
    state = _base_state(retry_budget_remaining=30, _dev_loop_llm_calls=2, paper_analysis={"metrics": ["nonexistent"]})
    out = execution(state)
    assert out["retry_budget_remaining"] == 29  # 30 - 1
    assert out["_dev_loop_llm_calls"] == 3  # 2 + 1
    # 抽到指标 + exit 0 → 成功。
    assert out["execution_result"]["success"] is True


def test_cp_c3_8b_no_llm_no_budget_change(monkeypatch):
    """档 1 结构化命中（不触发 LLM 抽取）→ execution 对预算零扣减。"""
    _patch_sandbox(
        monkeypatch,
        run_results=[FakeRunResult(exit_code=0, stdout="<METRICS>{\"acc\": 0.9}</METRICS>")],
    )
    state = _base_state(retry_budget_remaining=30, _dev_loop_llm_calls=2)
    out = execution(state)
    assert "retry_budget_remaining" not in out  # 不写 = 不覆盖 = 零扣减
    assert "_dev_loop_llm_calls" not in out


# ===========================================================================
# CP-C3-9：入口预算门（AC-S3-04 ③）—— retry_budget_remaining < 2 → 直接降级、不 interrupt
# ===========================================================================


def test_cp_c3_9_entry_budget_gate_degrade(monkeypatch):
    _patch_sandbox(
        monkeypatch,
        run_results=[FakeRunResult(exit_code=1, stderr="ModuleNotFoundError: x")],
    )
    state = _base_state(retry_budget_remaining=DEV_LOOP_MIN_CALLS_PER_ROUND - 1)  # =1 < 2
    out = execution(state)
    assert out["execution_result"]["success"] is False
    assert NODE_NAME in out["degraded_nodes"]
    assert out.get("_dev_loop_route") is None  # 降级 → reporting，不进修复循环
    assert "fix_loop_count" not in out  # 不自增
    assert "user_fix_decision" not in out  # 不 interrupt
    # 降级 NodeError 三态 degraded。
    assert any(e["error_type"] == "degraded" for e in out["node_errors"])


# ===========================================================================
# CP-C3-10：子预算触顶（AC-S3-04 ②）—— _dev_loop_llm_calls >= 20 → 视同修复耗尽，转 interrupt#2
# ===========================================================================


def test_cp_c3_10_dev_loop_budget_ceiling(monkeypatch):
    _patch_sandbox(
        monkeypatch,
        run_results=[FakeRunResult(exit_code=1, stderr="RuntimeError: boom")],
    )
    # 可修复失败 + fix_loop_count 未超限 + 预算够，但子预算触顶 → 不回 coding，转 interrupt 准备。
    state = _base_state(
        fix_loop_count=1,
        retry_budget_remaining=30,
        _dev_loop_llm_calls=MAX_DEV_LOOP_LLM_CALLS,  # ==20
    )
    out = execution(state)
    assert "fix_loop_count" not in out  # 不回 coding、不自增
    assert out.get("_dev_loop_route") == "await_dev_loop_interrupt"


# ===========================================================================
# CP-C3-11：must-fix-1 —— node_errors/degraded_nodes/fix_loop_history read-modify-write
# ===========================================================================


def test_cp_c3_11_read_modify_write_no_loss(monkeypatch):
    _patch_sandbox(
        monkeypatch,
        run_results=[FakeRunResult(exit_code=1, stderr="ModuleNotFoundError: x")],
    )
    # 预置上游已有的 list 内容。
    prior_err = {
        "node_name": "coding", "error_type": "degraded", "error_message": "prior",
        "error_detail": None, "timestamp": "t", "retry_count": 0, "resolved": False,
    }
    prior_hist = {
        "round_number": 99, "error_summary": "old", "error_category": "runtime",
        "fix_strategy": "s", "timestamp": "t",
    }
    state = _base_state(
        node_errors=[prior_err],
        degraded_nodes=["coding"],
        fix_loop_history=[prior_hist],
        fix_loop_count=0,
    )
    out = execution(state)
    # 旧内容保留 + 新内容追加（读出整列表→append→return），不丢不重复累加。
    assert out["node_errors"][0] == prior_err
    assert out["fix_loop_history"][0] == prior_hist
    assert out["fix_loop_history"][-1]["round_number"] == 1
    assert "coding" in out["degraded_nodes"]
    # 原 state list 未被原地 mutate（read-modify-write 用了 list(...)拷贝）。
    assert len(state["node_errors"]) == 1
    assert len(state["fix_loop_history"]) == 1


# ===========================================================================
# CP-C3-12：细分类承载位置 —— ErrorCategory 进 error_message 前缀，error_type 严格三态
# ===========================================================================


def test_cp_c3_12_category_in_message_not_error_type(monkeypatch):
    _patch_sandbox(
        monkeypatch,
        run_results=[FakeRunResult(exit_code=1, stderr="SyntaxError: invalid syntax")],
    )
    state = _base_state(fix_loop_count=0)
    out = execution(state)
    last = out["node_errors"][-1]
    # error_type 严格三态，不含 syntax/import 等细分类。
    assert last["error_type"] in {"transient", "permanent", "degraded"}
    assert last["error_type"] not in {c.value for c in ErrorCategory}
    # 细分类进 error_message 的 [error_category=...] 前缀。
    assert "[error_category=syntax]" in last["error_message"]
    # 映射检查：可修复→transient，不可修复→permanent。
    assert _map_category_to_error_type(ErrorCategory.SYNTAX) == "transient"
    assert _map_category_to_error_type(ErrorCategory.HARDWARE) == "permanent"
    assert _map_category_to_error_type(ErrorCategory.TIMEOUT) == "permanent"


# ===========================================================================
# CP-C3-13：interrupt#2 重跑幂等（S-1 契约）—— resume 重跑 sandbox 调用计数 == 1
# ===========================================================================


def test_cp_c3_13_interrupt_rerun_idempotent(monkeypatch):
    from langgraph.types import Command

    cnt = _patch_sandbox(
        monkeypatch,
        run_results=[FakeRunResult(exit_code=1, stderr="RuntimeError: boom")],
    )
    saver = _make_saver()
    graph = _build_self_loop_graph(saver)
    config = {"configurable": {"thread_id": f"c3-13-{uuid.uuid4().hex[:8]}"}}

    # 跑到 interrupt：第一次进入跑 sandbox（prepare=1）+ 落盘 await + self-loop 重入 execution
    # （guard 命中跳过 sandbox）+ interrupt 暂停。
    out1 = graph.invoke(_base_state(fix_loop_count=MAX_FIX_LOOP_COUNT), config)
    assert "__interrupt__" in out1
    assert cnt["prepare"] == 1, f"暂停时 sandbox prepare={cnt['prepare']}（期望 1）"

    # resume：重跑 interrupt 所在的这次进入（guard 命中，sandbox 不重跑）。
    graph.invoke(Command(resume={"decision": "terminate"}), config)
    assert cnt["prepare"] == 1, f"resume 后 sandbox prepare={cnt['prepare']}（期望恒为 1，S-1 CP-S-3 契约）"


# ===========================================================================
# CP-C3-14：非静默吞错 —— 失败分类 / 降级均打 WARNING 日志（caplog）
# ===========================================================================


def test_cp_c3_14_warning_on_failure_and_degrade(monkeypatch, caplog):
    _patch_sandbox(
        monkeypatch,
        run_results=[FakeRunResult(exit_code=1, stderr="ModuleNotFoundError: x")],
    )
    with caplog.at_level(logging.WARNING, logger="core.nodes.execution"):
        execution(_base_state(fix_loop_count=0))
    warnings = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("执行失败" in m for m in warnings), f"未见执行失败 WARNING: {warnings}"

    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="core.nodes.execution"):
        execution(_base_state(retry_budget_remaining=1, fix_loop_count=0))
    warnings = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("降级" in m for m in warnings), f"未见降级 WARNING: {warnings}"


# ===========================================================================
# 补充：metrics 三档解析单元
# ===========================================================================


def test_metrics_tier1_structured_block():
    m = _extract_metrics_block("noise\n<METRICS>{\"accuracy\": 0.91, \"f1\": 0.8}</METRICS>\ntail")
    assert m == {"accuracy": 0.91, "f1": 0.8}


def test_metrics_tier1_takes_last_block():
    m = _extract_metrics_block("<METRICS>{\"a\": 1}</METRICS> x <METRICS>{\"b\": 2}</METRICS>")
    assert m == {"b": 2}


def test_metrics_tier2_regex_scan():
    m = _regex_scan_metrics("Final accuracy: 0.873\nF1 = 81.2%", ["accuracy", "F1"])
    assert m["accuracy"] == 0.873
    assert abs(m["F1"] - 0.812) < 1e-9  # 81.2% -> 0.812


def test_metrics_tier_priority(monkeypatch):
    """档 1 命中则不走档 2/3（不调 LLM）。"""
    calls = {"llm": 0}

    def fake_llm(*a, **k):
        calls["llm"] += 1
        return ({}, 1)

    monkeypatch.setattr(execution_module, "_llm_extract_metrics", fake_llm)
    rr = [FakeRunResult(exit_code=0, stdout="<METRICS>{\"acc\": 0.5}</METRICS>")]
    metrics, used = _parse_metrics(rr, {}, {"paper_analysis": {"metrics": ["acc"]}})
    assert metrics == {"acc": 0.5}
    assert used == 0
    assert calls["llm"] == 0


def test_build_execution_result_b_grade(monkeypatch):
    monkeypatch.setattr(execution_module, "collect_artifacts", lambda *a, **k: [])
    prep = FakePrepareResult(success=True)
    runs = [FakeRunResult(exit_code=0)]
    fb = ExecutionFeedback(ErrorCategory.NONE, False, "ok", "", "")
    # 有指标 → 成功。
    er = _build_execution_result(prep, runs, fb, {"acc": 0.9}, _work_dir())
    assert er["success"] is True
    # 无指标 → 失败（B 档要求 ≥1）。
    er2 = _build_execution_result(prep, runs, fb, {}, _work_dir())
    assert er2["success"] is False


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
