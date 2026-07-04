"""C3 - execution 节点独立验收补强用例（Sprint 3，S3-03/04/07）。

由测试工程师代理独立编写，覆盖开发自测 ``tests/test_sprint3_c3.py`` 的遗漏与盲点：

    - guard 跨回合误命中边界（route=None / retry_coding / result=None 三态均不误命中，CP-C3-13 补强）；
    - 入口预算门 vs 不可修复的优先级（budget<2 时降级压倒 interrupt，CP-C3-6/9 交叉）；
    - revise_plan 端到端 fix_loop_history 真保留（开发仅断言 fix_loop_count==0，CP-C3-7 补强）；
    - export_code 端到端 node_errors 完整性（旧错误 + 本轮 + 降级三条无丢失，CP-C3-7/11 交叉）；
    - _step_to_command 边界（非 python / 空 / 引号 / shlex 异常兜底，骨架步骤②）；
    - 正则元字符指标名（top-1 acc / F1@5 / mAP(0.5)，CP-C3-2 档2 补强）；
    - 档1 嵌套对象过滤；
    - work_dir 缺失降级（execution.py 上游防御）；
    - prepare_venv 抛 SandboxCreationError 的降级处理；
    - timeout 不可修复端到端走 await（区别于硬件）；
    - 子预算 await 重入幂等（dev_loop_llm_calls 触顶后 resume 仍幂等）。

约束（与开发自测一致）：mock sandbox（patch prepare_venv/run_in_venv/collect_artifacts），不跑真实
venv/LLM/deepxiv；interrupt 用最小 self-loop StateGraph + InMemorySaver + Command(resume=...) 跑真实
interrupt；唯一 uuid4 thread_id 防串。用 importlib 拿真实子模块（避免 __init__ callable 遮蔽）。
"""
from __future__ import annotations

import importlib
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

execution_module = importlib.import_module("core.nodes.execution")

from config import (  # noqa: E402
    DEV_LOOP_MIN_CALLS_PER_ROUND,
    MAX_DEV_LOOP_LLM_CALLS,
    MAX_FIX_LOOP_COUNT,
    WORKSPACE_DIR,
)
from core.errors import SandboxCreationError  # noqa: E402
from core.nodes.execution import (  # noqa: E402
    INTERRUPT_KIND,
    NODE_NAME,
    ErrorCategory,
    _ROUTE_AWAIT_INTERRUPT,
    _ROUTE_RETRY_CODING,
    _extract_metrics_block,
    _has_committed_result_for_round,
    _regex_scan_metrics,
    _step_to_command,
    execution,
)
from core.state import ExecutionMode  # noqa: E402


# ---------------------------------------------------------------------------
# 伪 sandbox dataclass + 工具（与开发自测同构，独立维护以避免跨文件耦合）
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
    return str(WORKSPACE_DIR / "c3-reinforce" / "code")


def _base_state(**overrides: Any) -> Dict[str, Any]:
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
    prep_raises: Optional[Exception] = None,
    counter: Optional[Dict[str, int]] = None,
) -> Dict[str, int]:
    # 【sp4 E4 mock 落点适配 2026-07-04】E3 把步骤 1+2 换成 _run_execution_agent
    # 内嵌子图，mock 落点上移（同 tests/test_sprint3_c3.py 适配注记）：
    #   - 每次 agent 调用 = 一次 prepare + 消费一条 run（1 step/回合），计数语义等价；
    #   - rounds_used=0 保持 sp3 预算断言不变（子图 rounds 扣减由 CP-E3-1 覆盖）；
    #   - prep_raises（R-9）：sp4 真实链路中 prepare_environment 工具捕获
    #     SandboxCreationError 转结构化错误 → 收集器无 prep → agent 收尾
    #     prep=None + 空 run_results（等价于 sp3 的 prepare 抛错分支，
    #     _classify_execution 步骤 0 同样归 DEPENDENCY 可修复）。
    cnt = counter if counter is not None else {"prepare": 0, "run": 0}
    prep_obj = prep if prep is not None else FakePrepareResult()
    runs = run_results if run_results is not None else [
        FakeRunResult(exit_code=0, stdout='<METRICS>{"accuracy": 0.9}</METRICS>')
    ]
    arts = artifacts if artifacts is not None else []
    run_iter = iter(runs)

    def fake_run_execution_agent(state: Any, work_dir: Any, plan: Any):
        cnt["prepare"] = cnt.get("prepare", 0) + 1
        if prep_raises is not None:
            # 工具层吞掉 SandboxCreationError → 无 prep、不再跑 run（run 计数保持 0）。
            return execution_module.ExecAgentOutput(
                prep=None, run_results=[], rounds_used=0, llm_calls=0,
            )
        cnt["run"] = cnt.get("run", 0) + 1
        try:
            rr = next(run_iter)
        except StopIteration:
            rr = runs[-1] if runs else FakeRunResult()
        return execution_module.ExecAgentOutput(
            prep=prep_obj, run_results=[rr], rounds_used=0, llm_calls=0,
        )

    def fake_collect_artifacts(*args: Any, **kwargs: Any) -> List[str]:
        return list(arts)

    monkeypatch.setattr(execution_module, "_run_execution_agent", fake_run_execution_agent)
    monkeypatch.setattr(execution_module, "collect_artifacts", fake_collect_artifacts)
    return cnt


def _build_self_loop_graph(checkpointer):
    """模拟 D1 _route_after_execution 的 self-loop 关键分支（await→execution，其余→END）。"""
    from langgraph.graph import END, START, StateGraph

    from core.state import GlobalState

    g = StateGraph(GlobalState)
    g.add_node("execution", execution)
    g.add_edge(START, "execution")

    def route(state: Dict[str, Any]) -> str:
        if state.get("_dev_loop_route") == _ROUTE_AWAIT_INTERRUPT:
            return "execution"
        return "end"

    g.add_conditional_edges("execution", route, {"execution": "execution", "end": END})
    return g.compile(checkpointer=checkpointer)


def _make_saver():
    from langgraph.checkpoint.memory import InMemorySaver

    return InMemorySaver()


# ===========================================================================
# R-1：guard 跨回合误命中边界（CP-C3-13 补强）—— 仅 await+非空 result 才命中
# ===========================================================================


def test_r1_guard_only_hits_on_await_with_result():
    # 命中态：await + 非空 result。
    assert _has_committed_result_for_round(
        {"_dev_loop_route": _ROUTE_AWAIT_INTERRUPT, "execution_result": {"success": False}}
    ) is True
    # 不误命中：coding 修复回合（D1 清空 route 为 None）即便残留旧 result。
    assert _has_committed_result_for_round(
        {"_dev_loop_route": None, "execution_result": {"success": False}}
    ) is False
    # 不误命中：retry_coding 标记 + 旧 result。
    assert _has_committed_result_for_round(
        {"_dev_loop_route": _ROUTE_RETRY_CODING, "execution_result": {"success": False}}
    ) is False
    # 不误命中：await 但 result 为 None（异常态）。
    assert _has_committed_result_for_round(
        {"_dev_loop_route": _ROUTE_AWAIT_INTERRUPT, "execution_result": None}
    ) is False
    # 不误命中：全空 state。
    assert _has_committed_result_for_round({}) is False


def test_r1b_retry_round_reentry_reruns_sandbox(monkeypatch):
    """coding 修复回合重入 execution（route 已被 D1 清空）→ guard 不命中 → 重跑 sandbox（语义正确）。

    反证 guard 不会把上一轮旧 execution_result 当本回合复用（跨回合误命中会导致漏跑修复后的代码）。
    """
    cnt = _patch_sandbox(
        monkeypatch,
        run_results=[FakeRunResult(exit_code=0, stdout='<METRICS>{"accuracy": 0.95}</METRICS>')],
    )
    # 模拟修复回合 2 重入：上一轮失败 result 还在 state，但 route 已被 D1 清成 None。
    stale = {
        "success": False, "metrics": {}, "logs": "", "errors": ["[error_category=runtime] old"],
        "artifacts": [], "runtime_seconds": 0.0, "environment_info": {},
    }
    state = _base_state(fix_loop_count=1, _dev_loop_route=None, execution_result=stale)
    out = execution(state)
    # guard 未命中 → 重跑 sandbox → 拿到修复后的新成功结果。
    assert cnt["prepare"] == 1
    assert out["execution_result"]["success"] is True
    assert out["execution_result"]["metrics"]["accuracy"] == 0.95


# ===========================================================================
# R-2：入口预算门 vs 不可修复优先级（CP-C3-6/9 交叉）
#       budget<2 即便不可修复也走降级（budget 门在最前），不 interrupt
# ===========================================================================


def test_r2_budget_gate_beats_unfixable(monkeypatch):
    _patch_sandbox(
        monkeypatch,
        run_results=[FakeRunResult(exit_code=1, stderr="RuntimeError: CUDA out of memory")],
    )
    state = _base_state(retry_budget_remaining=DEV_LOOP_MIN_CALLS_PER_ROUND - 1, fix_loop_count=0)
    out = execution(state)
    # 预算门压倒不可修复：降级到 reporting，不 interrupt、不置 await。
    assert NODE_NAME in out["degraded_nodes"]
    assert out.get("_dev_loop_route") is None
    assert "user_fix_decision" not in out
    # 失败本身记 permanent（hardware 不可修复），降级另记 degraded。
    types = {e["error_type"] for e in out["node_errors"]}
    assert "permanent" in types and "degraded" in types


# ===========================================================================
# R-3：revise_plan 端到端 fix_loop_history 真保留（CP-C3-7 补强）
# ===========================================================================


def test_r3_revise_plan_preserves_history_end_to_end(monkeypatch):
    from langgraph.types import Command

    _patch_sandbox(
        monkeypatch,
        run_results=[FakeRunResult(exit_code=1, stderr="RuntimeError: boom")],
    )
    saver = _make_saver()
    graph = _build_self_loop_graph(saver)
    cfg = {"configurable": {"thread_id": f"r3-{uuid.uuid4().hex[:8]}"}}

    prior_hist = [
        {"round_number": 1, "error_summary": "s1", "error_category": "runtime",
         "fix_strategy": "x", "timestamp": "t1"},
        {"round_number": 2, "error_summary": "s2", "error_category": "import",
         "fix_strategy": "y", "timestamp": "t2"},
    ]
    init = _base_state(fix_loop_count=MAX_FIX_LOOP_COUNT, fix_loop_history=list(prior_hist))
    out1 = graph.invoke(init, cfg)
    assert "__interrupt__" in out1

    graph.invoke(Command(resume={"decision": "revise_plan", "user_feedback": "用更小模型"}), cfg)
    final = graph.get_state(cfg).values
    # fix_loop_count 清零。
    assert final["fix_loop_count"] == 0
    # fix_loop_history 完整保留（2 条历史，未被清空——last-write-wins 不写即留）。
    assert len(final["fix_loop_history"]) == 2
    assert final["fix_loop_history"][0]["round_number"] == 1
    assert final["fix_loop_history"][1]["error_category"] == "import"
    # approved 清掉 + 修复上下文写入。
    assert final["reproduction_plan"]["approved"] is False
    assert "修订复现计划" in final["_planning_user_feedback"]


# ===========================================================================
# R-4：export_code 端到端 node_errors 完整性（CP-C3-7/11 交叉）
#       guard 命中分支不重写 node_errors，依赖 _mark_degraded_for_report 从 state 兜底累加
# ===========================================================================


def test_r4_export_code_node_errors_complete(monkeypatch):
    from langgraph.types import Command

    cnt = _patch_sandbox(
        monkeypatch,
        run_results=[FakeRunResult(exit_code=1, stderr="RuntimeError: CUDA out of memory")],
    )
    saver = _make_saver()
    graph = _build_self_loop_graph(saver)
    cfg = {"configurable": {"thread_id": f"r4-{uuid.uuid4().hex[:8]}"}}

    prior = {
        "node_name": "coding", "error_type": "degraded", "error_message": "prior_coding",
        "error_detail": None, "timestamp": "t", "retry_count": 0, "resolved": False,
    }
    out1 = graph.invoke(
        _base_state(fix_loop_count=MAX_FIX_LOOP_COUNT, node_errors=[prior]), cfg
    )
    assert "__interrupt__" in out1
    assert cnt["prepare"] == 1

    graph.invoke(Command(resume={"decision": "export_code"}), cfg)
    final = graph.get_state(cfg).values
    msgs = [e["error_message"] for e in final["node_errors"]]
    # 三条无丢失：上游 coding 旧错误 + 本轮执行失败 + 降级记录。
    assert any(m == "prior_coding" for m in msgs)
    assert any("[error_category=hardware]" in m for m in msgs)
    assert any("execution 降级" in m for m in msgs)
    assert final["user_fix_decision"] == "export_code"
    assert NODE_NAME in final["degraded_nodes"]
    # 幂等：sandbox 恒 1。
    assert cnt["prepare"] == 1


# ===========================================================================
# R-5：_step_to_command 边界（骨架步骤②）
# ===========================================================================


def test_r5_step_to_command_edges():
    # 新契约（方向 2 复合命令拆分）：_step_to_command 返回 List[(argv, connector)]，
    # 仅做顶层 && / ; 拆分；裸 python/pip 改写延后到执行循环（_run_step_subcommands），此层保留原 token。
    PE = "/ws/.venv/bin/python"
    # 单条原子命令：一条子命令、connector 为 ""（python 此层尚未改写）。
    assert _step_to_command({"command": "python train.py --epochs 5"}, PE) == [
        (["python", "train.py", "--epochs", "5"], "")
    ]
    assert _step_to_command("python3 eval.py", PE) == [(["python3", "eval.py"], "")]
    # 非 python 命令原样。
    assert _step_to_command({"command": "bash run.sh"}, PE) == [(["bash", "run.sh"], "")]
    # 空 / 缺键 → None（被节点跳过）。
    assert _step_to_command({"command": ""}, PE) is None
    assert _step_to_command({"step_name": "x"}, PE) is None
    assert _step_to_command("   ", PE) is None
    # 引号内 -c 表达式保持完整（不被顶层拆分误切）。
    assert _step_to_command({"command": 'python -c "print(1)"'}, PE) == [
        (["python", "-c", "print(1)"], "")
    ]
    # shlex 异常（不平衡引号）兜底 split 不抛。
    out = _step_to_command({"command": 'python -c "unclosed'}, PE)
    assert out is not None and out[0][0][0] == "python"
    # 备用键 cmd / run。
    assert _step_to_command({"cmd": "python a.py"}, PE) == [(["python", "a.py"], "")]
    assert _step_to_command({"run": "python b.py"}, PE) == [(["python", "b.py"], "")]
    # 顶层 && 拆分为两条子命令。
    assert _step_to_command({"command": "git clone X && cd X"}, PE) == [
        (["git", "clone", "X"], ""), (["cd", "X"], "&&")
    ]


# ===========================================================================
# R-6：metrics 档2 正则元字符指标名（CP-C3-2 档2 补强）
# ===========================================================================


def test_r6_regex_scan_special_metric_names():
    assert _regex_scan_metrics("top-1 acc: 0.85", ["top-1 acc"]) == {"top-1 acc": 0.85}
    assert _regex_scan_metrics("F1@5 = 0.7", ["F1@5"]) == {"F1@5": 0.7}
    # 百分号归一化 + 括号元字符。
    m = _regex_scan_metrics("mAP(0.5): 88.2%", ["mAP(0.5)"])
    assert abs(m["mAP(0.5)"] - 0.882) < 1e-9
    # 非字符串 / 空指标名跳过不炸。
    assert _regex_scan_metrics("acc: 0.9", [None, 123, ""]) == {}
    # 无匹配 → 空 dict。
    assert _regex_scan_metrics("no numbers here", ["accuracy"]) == {}


# ===========================================================================
# R-7：档1 嵌套对象过滤（只保留扁平数值/字符串）
# ===========================================================================


def test_r7_metrics_tier1_filters_nested():
    # 嵌套 dict 被过滤，扁平值保留。
    assert _extract_metrics_block('<METRICS>{"acc": 0.9, "detail": {"a": 1}}</METRICS>') == {"acc": 0.9}
    # 纯嵌套（无扁平值）→ 空 → 视为未命中（让档2/3 接手）。
    assert _extract_metrics_block('<METRICS>{"detail": {"a": 1}}</METRICS>') == {}
    # 非法 JSON → 空。
    assert _extract_metrics_block("<METRICS>not json</METRICS>") == {}
    # 列表值被过滤。
    assert _extract_metrics_block('<METRICS>{"acc": 0.5, "arr": [1,2]}</METRICS>') == {"acc": 0.5}
    # bool 值保留（isinstance int/float/str/bool 允许）。
    assert _extract_metrics_block('<METRICS>{"passed": true}</METRICS>') == {"passed": True}


# ===========================================================================
# R-8：work_dir 缺失降级（上游 C1 防御，execution.py 入口）
# ===========================================================================


def test_r8_missing_work_dir_degrades(monkeypatch):
    cnt = _patch_sandbox(monkeypatch)
    state = _base_state(code_output_dir=None)
    out = execution(state)
    # 不进 sandbox。
    assert cnt["prepare"] == 0
    assert out["execution_result"]["success"] is False
    assert NODE_NAME in out["degraded_nodes"]
    assert out.get("_dev_loop_route") is None  # → reporting
    assert "[error_category=path]" in out["execution_result"]["errors"][0]


def test_r8b_empty_work_dir_string_degrades(monkeypatch):
    cnt = _patch_sandbox(monkeypatch)
    out = execution(_base_state(code_output_dir=""))
    assert cnt["prepare"] == 0
    assert NODE_NAME in out["degraded_nodes"]


# ===========================================================================
# R-9：prepare_venv 抛 SandboxCreationError 的降级处理（execution.py 步骤1 try/except）
# ===========================================================================


def test_r9_prepare_venv_raises_degrades_as_dependency(monkeypatch):
    cnt = _patch_sandbox(
        monkeypatch,
        prep_raises=SandboxCreationError("python_exe 越界", "detail"),
    )
    state = _base_state(fix_loop_count=0, retry_budget_remaining=40)
    out = execution(state)
    # prepare 被调用（抛错），但 run 未被调用。
    assert cnt["prepare"] == 1
    assert cnt["run"] == 0
    assert out["execution_result"]["success"] is False
    # 归 DEPENDENCY（可修复）→ 可回 coding（首次进入：fix_loop_count 自增 + retry_coding）。
    assert "[error_category=dependency]" in out["execution_result"]["errors"][0]
    assert out["fix_loop_count"] == 1
    assert out["_dev_loop_route"] == _ROUTE_RETRY_CODING


# ===========================================================================
# R-10：timeout 不可修复端到端走 interrupt（区别于硬件，验证 timeout 也置 await）
# ===========================================================================


def test_r10_timeout_unfixable_to_await(monkeypatch):
    _patch_sandbox(
        monkeypatch,
        run_results=[FakeRunResult(exit_code=-1, timed_out=True, stderr="killed after timeout")],
    )
    state = _base_state(fix_loop_count=0)  # 未超限但 timeout 不可修复
    out = execution(state)
    assert out["execution_result"]["success"] is False
    assert "fix_loop_count" not in out  # 不可修复不自增
    assert out.get("_dev_loop_route") == _ROUTE_AWAIT_INTERRUPT
    last = out["node_errors"][-1]
    assert last["error_type"] == "permanent"
    assert "[error_category=timeout]" in last["error_message"]


def test_r10b_timeout_end_to_end_interrupt(monkeypatch):
    """timeout 端到端：await 重入后 guard 命中 → interrupt#2 暂停，payload 含 kind。"""
    from langgraph.types import Command

    cnt = _patch_sandbox(
        monkeypatch,
        run_results=[FakeRunResult(exit_code=-1, timed_out=True, stderr="killed")],
    )
    saver = _make_saver()
    graph = _build_self_loop_graph(saver)
    cfg = {"configurable": {"thread_id": f"r10b-{uuid.uuid4().hex[:8]}"}}
    out1 = graph.invoke(_base_state(fix_loop_count=0), cfg)
    assert "__interrupt__" in out1
    assert cnt["prepare"] == 1
    snap = graph.get_state(cfg)
    interrupts = [iv for task in snap.tasks for iv in (getattr(task, "interrupts", None) or [])]
    assert interrupts and interrupts[0].value.get("interrupt_kind") == INTERRUPT_KIND
    assert interrupts[0].value.get("error_category") == "timeout"
    assert interrupts[0].value.get("auto_fixable") is False
    # resume 幂等。
    graph.invoke(Command(resume={"decision": "terminate"}), cfg)
    assert cnt["prepare"] == 1


# ===========================================================================
# R-11：子预算触顶 await 重入端到端幂等（CP-C3-10 端到端补强）
# ===========================================================================


def test_r11_dev_loop_ceiling_end_to_end_idempotent(monkeypatch):
    from langgraph.types import Command

    cnt = _patch_sandbox(
        monkeypatch,
        run_results=[FakeRunResult(exit_code=1, stderr="RuntimeError: boom")],
    )
    saver = _make_saver()
    graph = _build_self_loop_graph(saver)
    cfg = {"configurable": {"thread_id": f"r11-{uuid.uuid4().hex[:8]}"}}
    # 可修复 + 未超 fix_loop_count + 预算够，但子预算触顶 → 视同修复耗尽。
    init = _base_state(
        fix_loop_count=1, retry_budget_remaining=30,
        _dev_loop_llm_calls=MAX_DEV_LOOP_LLM_CALLS,
    )
    out1 = graph.invoke(init, cfg)
    assert "__interrupt__" in out1
    assert cnt["prepare"] == 1
    # 子预算触顶时 fix_loop_count 不自增（断言最终未自增）。
    snap_vals = graph.get_state(cfg).values
    assert snap_vals["fix_loop_count"] == 1
    graph.invoke(Command(resume={"decision": "terminate"}), cfg)
    assert cnt["prepare"] == 1
    final = graph.get_state(cfg).values
    assert final["current_step"] == "cancelled_by_user"


# ===========================================================================
# R-12：interrupt#2 payload 完整性（含 fix_loop_history / execution_errors / options）
# ===========================================================================


def test_r12_interrupt_payload_shape(monkeypatch):
    cnt = _patch_sandbox(
        monkeypatch,
        run_results=[FakeRunResult(exit_code=1, stderr="RuntimeError: boom")],
    )
    saver = _make_saver()
    graph = _build_self_loop_graph(saver)
    cfg = {"configurable": {"thread_id": f"r12-{uuid.uuid4().hex[:8]}"}}
    hist = [{"round_number": 1, "error_summary": "s", "error_category": "runtime",
             "fix_strategy": "x", "timestamp": "t"}]
    graph.invoke(_base_state(fix_loop_count=MAX_FIX_LOOP_COUNT, fix_loop_history=list(hist)), cfg)
    snap = graph.get_state(cfg)
    payload = [iv for task in snap.tasks for iv in (getattr(task, "interrupts", None) or [])][0].value
    assert payload["interrupt_kind"] == INTERRUPT_KIND
    assert payload["fix_loop_count"] == MAX_FIX_LOOP_COUNT
    assert payload["options"] == ["terminate", "revise_plan", "export_code"]
    assert payload["fix_loop_history"] == hist
    assert isinstance(payload["execution_errors"], list)
    assert payload["error_category"] == "runtime"
    assert cnt["prepare"] == 1


# ===========================================================================
# R-13：连续修复回合 fix_loop_count 单调推进（0→…→MAX_FIX_LOOP_COUNT），触顶后转 await
# ===========================================================================


def test_r13_sequential_fix_rounds_monotonic(monkeypatch):
    """逐回合模拟：fix_loop_count 0→…→MAX-1 都回 coding，==MAX 时转 await（单点自增、上限拦截）。"""
    for fc in range(MAX_FIX_LOOP_COUNT):
        _patch_sandbox(
            monkeypatch,
            run_results=[FakeRunResult(exit_code=1, stderr="ModuleNotFoundError: x")],
        )
        out = execution(_base_state(fix_loop_count=fc))
        assert out["fix_loop_count"] == fc + 1, f"fc={fc} 应自增到 {fc+1}"
        assert out["_dev_loop_route"] == _ROUTE_RETRY_CODING
        assert len(out["fix_loop_history"]) == 1  # 本回合 append 一条（基于空 history）
    # fc==MAX_FIX_LOOP_COUNT：上限，不自增、转 await。
    _patch_sandbox(
        monkeypatch,
        run_results=[FakeRunResult(exit_code=1, stderr="ModuleNotFoundError: x")],
    )
    out = execution(_base_state(fix_loop_count=MAX_FIX_LOOP_COUNT))
    assert "fix_loop_count" not in out
    assert out["_dev_loop_route"] == _ROUTE_AWAIT_INTERRUPT


# ===========================================================================
# R-14：unresolved_resource 不可修复分类（开发自测未单独覆盖该类）
# ===========================================================================


def test_r14_unresolved_resource_unfixable(monkeypatch):
    _patch_sandbox(
        monkeypatch,
        run_results=[FakeRunResult(exit_code=1, stderr="Error: pretrained weights not found, request access")],
    )
    out = execution(_base_state(fix_loop_count=0))
    assert "fix_loop_count" not in out  # 不可修复不自增
    assert out.get("_dev_loop_route") == _ROUTE_AWAIT_INTERRUPT
    last = out["node_errors"][-1]
    assert last["error_type"] == "permanent"
    assert "[error_category=unresolved_resource]" in last["error_message"]


# ===========================================================================
# R-15：data_missing 与 path 关键字优先级（数据集 FileNotFound 归 data_missing 不可修复）
# ===========================================================================


def test_r15_data_missing_before_path(monkeypatch):
    # 含 data 目录关键字的 FileNotFound → data_missing（不可修复），不归 path。
    _patch_sandbox(
        monkeypatch,
        run_results=[FakeRunResult(exit_code=1, stderr="FileNotFoundError: [Errno 2] No such file or directory: 'data/train.csv'")],
    )
    out = execution(_base_state(fix_loop_count=0))
    last = out["node_errors"][-1]
    assert "[error_category=data_missing]" in last["error_message"]
    assert last["error_type"] == "permanent"
    assert "fix_loop_count" not in out


def test_r15b_non_data_path_is_fixable(monkeypatch):
    # 非数据集的路径错（config.yaml）→ path（可修复）。
    _patch_sandbox(
        monkeypatch,
        run_results=[FakeRunResult(exit_code=1, stderr="FileNotFoundError: No such file: 'config.yaml'")],
    )
    out = execution(_base_state(fix_loop_count=0))
    assert out["fix_loop_count"] == 1
    assert out["_dev_loop_route"] == _ROUTE_RETRY_CODING


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
