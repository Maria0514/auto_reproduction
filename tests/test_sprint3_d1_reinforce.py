"""D1 - core/graph.py 路由编排独立验收补强用例（Sprint 3，S3-04/06/07）。

由测试工程师代理独立编写，覆盖开发自测 ``tests/test_sprint3_d1.py`` 的遗漏与盲点，
重点是「路由函数返回字符串对、但编译图里 add_conditional_edges 映射是否真成边」的盲区
（任务书高风险重锤 L-C3-01 命门）：

    重锤组（命门）
    - H-1  用真实 build_graph() 编译的主图端到端跑 interrupt#2 self-loop：
            execution 首次不可修复失败 → return 落盘 await 标记（不 interrupt）
            → 真实 _route_after_execution 在编译图里把 "execution" 映射回 execution 节点（self-loop）
            → 重入后 guard 命中跳过 sandbox → 函数体内 interrupt() 真触发暂停；
            断言 sandbox prepare_venv 副作用恰 == 1（首次 1，self-loop 重入跳过）。
    - H-2  同上，Command(resume={"decision": ...}) 注入后图正常恢复并按三态出边走对目的地；
            resume 重跑期间 sandbox 仍不重跑（副作用仍 == 1）。
    - H-3  retry_coding 修复回边在真实编译图里成边：execution(retry) → coding → execution（真路由）。

    边界组
    - B-1  add_conditional_edges 映射键 vs 路由函数返回值集合一致性（无悬空/无未覆盖返回值）。
    - B-2  _route_after_execution 返回值全集 ⊆ 映射键全集（防 KeyError 路由）。
    - B-3  await self-loop 优先级即便误同时带 user_fix_decision/current_step 仍 self-loop（防 interrupt#2 漏触发）。
    - B-4  planning 出边与 sp2 _route_after_planning 行为等价（code_only 区分点未渗入 planning 出边）。
    - B-5  coding 出边 code_only 区分严格在 coding 出边（execution_mode 多形态）。
    - B-6  reporting 是唯一只通 END 的业务终点；CODE_ONLY 与 FULL 成功都汇聚 reporting。
    - B-7  路由函数纯读：对含 list 字段的 state 调用后原对象不被 mutate（零 reducer 红线相关）。

约束（与开发自测一致）：mock sandbox（patch prepare_venv/run_in_venv/collect_artifacts），
不跑真实 venv / LLM / deepxiv；interrupt 用真实 build_graph() 主图 + InMemorySaver +
Command(resume=...) 跑真实 interrupt；唯一 uuid4 thread_id 防串；importlib 拿真实子模块。
"""
from __future__ import annotations

import importlib
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from langgraph.checkpoint.memory import InMemorySaver  # noqa: E402
from langgraph.types import Command  # noqa: E402

from config import WORKSPACE_DIR  # noqa: E402
from core import graph as graph_module  # noqa: E402
from core.graph import (  # noqa: E402
    _route_after_coding,
    _route_after_execution,
    _route_after_planning,
    build_graph,
)
from core.state import ExecutionMode  # noqa: E402

execution_module = importlib.import_module("core.nodes.execution")


# ---------------------------------------------------------------------------
# 伪 sandbox dataclass + patch（与 C3 补强同构，独立维护避免跨文件耦合）
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
    return str(WORKSPACE_DIR / "d1-reinforce" / "code")


def _patch_sandbox(
    monkeypatch: pytest.MonkeyPatch,
    *,
    run_results: Optional[List[FakeRunResult]] = None,
    counter: Optional[Dict[str, int]] = None,
) -> Dict[str, int]:
    """patch execution 模块内的 sandbox 三入口，返回调用计数器。"""
    cnt = counter if counter is not None else {"prepare": 0, "run": 0}
    runs = run_results if run_results is not None else [
        FakeRunResult(exit_code=0, stdout='<METRICS>{"accuracy": 0.9}</METRICS>')
    ]
    run_iter = iter(runs)

    def fake_prepare_venv(*args: Any, **kwargs: Any) -> FakePrepareResult:
        cnt["prepare"] = cnt.get("prepare", 0) + 1
        return FakePrepareResult()

    def fake_run_in_venv(*args: Any, **kwargs: Any) -> FakeRunResult:
        cnt["run"] = cnt.get("run", 0) + 1
        try:
            return next(run_iter)
        except StopIteration:
            return runs[-1] if runs else FakeRunResult()

    def fake_collect_artifacts(*args: Any, **kwargs: Any) -> List[str]:
        return []

    monkeypatch.setattr(execution_module, "prepare_venv", fake_prepare_venv)
    monkeypatch.setattr(execution_module, "run_in_venv", fake_run_in_venv)
    monkeypatch.setattr(execution_module, "collect_artifacts", fake_collect_artifacts)
    return cnt


def _full_mode_initial_state(**overrides: Any) -> Dict[str, Any]:
    """直接进入 execution 的初始 state（绕开上游节点，单测 execution self-loop 命门用）。

    单独编一个最小图（START → execution → 真实路由），保证 execution 输入契约真实。
    """
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
        "messages": [],
    }
    state.update(overrides)
    return state


def _build_real_route_execution_graph(checkpointer):
    """用 **真实** _route_after_execution + 真实 execution 节点构造 START→execution→[真路由] 图。

    与 C3 补强的关键区别：C3 用模拟 route（手写 await→execution）；此处直接 import 生产
    _route_after_execution 并用与 build_graph() **逐字节相同** 的 mapping 接线，验证「真实路由
    函数 + 真实映射」端到端成边（命门：路由返回 'execution' 必须经映射 {'execution':'execution'}
    真正指回 execution 节点）。coding/planning/reporting 用轻量 stub 占位（self-loop 命门不依赖它们）。
    """
    from langgraph.graph import END, START, StateGraph

    from core.state import GlobalState

    g = StateGraph(GlobalState)

    def stub_coding(state):
        # 修复回合：清 await（模拟 coding 不写 _dev_loop_route），写新代码目录。
        return {"_dev_loop_route": None, "code_output_dir": _work_dir(), "current_step": "coding"}

    def stub_planning(state):
        return {"current_step": "planning"}

    def stub_reporting(state):
        return {"report_path": "/tmp/d1r/report.md", "current_step": "reporting"}

    g.add_node("execution", execution_module.execution)
    g.add_node("coding", stub_coding)
    g.add_node("planning", stub_planning)
    g.add_node("reporting", stub_reporting)
    g.add_edge(START, "execution")
    # 与 build_graph() 完全一致的映射（真实生产路由函数 + 真实映射键）。
    g.add_conditional_edges(
        "execution",
        _route_after_execution,
        {
            "execution": "execution",
            "coding": "coding",
            "planning": "planning",
            "reporting": "reporting",
            "end": END,
        },
    )
    g.add_edge("coding", "execution")
    g.add_edge("planning", END)
    g.add_edge("reporting", END)
    return g.compile(checkpointer=checkpointer)


# ===========================================================================
# 重锤组 H-1：真实编译图端到端 self-loop + interrupt#2 触发 + sandbox 副作用==1（命门）
# ===========================================================================


def test_h1_real_graph_await_self_loop_triggers_interrupt_sandbox_once(monkeypatch):
    """⚠️ 命门重锤：真实 _route_after_execution + 真实映射 → self-loop 成边 → interrupt#2 触发。

    构造不可修复失败（hardware）使 execution 首次进入：跑 sandbox → 落盘 execution_result +
    置 await（不 interrupt，commit 边界 return）→ self-loop 路由回 execution → guard 命中跳过
    sandbox → 函数体内 interrupt() 真暂停。断言：
      (1) 图暂停在 interrupt（snapshot 有 __interrupt__ / tasks 含 interrupt）；
      (2) prepare_venv 副作用 == 1（首次 1，self-loop 重入跳过 sandbox）。
    """
    cnt = _patch_sandbox(
        monkeypatch,
        run_results=[FakeRunResult(exit_code=1, stderr="CUDA out of memory: no GPU")],
    )
    saver = InMemorySaver()
    graph = _build_real_route_execution_graph(saver)
    thread_id = f"d1-h1-{uuid.uuid4()}"
    cfg = {"configurable": {"thread_id": thread_id}}

    out = graph.invoke(_full_mode_initial_state(), cfg)

    # interrupt#2 真触发（图暂停）。
    assert "__interrupt__" in out, "self-loop 重入后应在 execution 函数体内触发 interrupt#2 暂停"
    # sandbox 只跑一次（首次进入 1 次；self-loop 重入 guard 命中跳过）。
    assert cnt["prepare"] == 1, (
        f"sandbox prepare_venv 副作用应恰为 1（首次跑 + self-loop 重入跳过），实际 {cnt['prepare']}"
    )


def test_h1b_interrupt_payload_is_dev_loop_failure(monkeypatch):
    """self-loop 重入触发的 interrupt payload 应携 interrupt_kind=='dev_loop_failure'（与 planning 区分）。"""
    _patch_sandbox(
        monkeypatch,
        run_results=[FakeRunResult(exit_code=1, stderr="CUDA out of memory")],
    )
    saver = InMemorySaver()
    graph = _build_real_route_execution_graph(saver)
    cfg = {"configurable": {"thread_id": f"d1-h1b-{uuid.uuid4()}"}}
    graph.invoke(_full_mode_initial_state(), cfg)
    snapshot = graph.get_state(cfg)
    payloads = []
    for task in snapshot.tasks:
        for itr in getattr(task, "interrupts", ()) or ():
            val = getattr(itr, "value", None)
            if isinstance(val, dict):
                payloads.append(val)
    assert payloads, "self-loop 重入应触发 interrupt 并携 payload"
    assert any(p.get("interrupt_kind") == "dev_loop_failure" for p in payloads), (
        f"interrupt#2 payload 应含 interrupt_kind=='dev_loop_failure'，实际 {payloads}"
    )


# ===========================================================================
# 重锤组 H-2：resume 三态在真实图里走对目的地 + resume 期间 sandbox 不重跑
# ===========================================================================


def test_h2_resume_terminate_reaches_end_sandbox_still_once(monkeypatch):
    """interrupt#2 resume terminate → 经真实路由到 END；resume 重跑期间 sandbox 仍不重跑（==1）。"""
    cnt = _patch_sandbox(
        monkeypatch,
        run_results=[FakeRunResult(exit_code=1, stderr="CUDA out of memory")],
    )
    saver = InMemorySaver()
    graph = _build_real_route_execution_graph(saver)
    cfg = {"configurable": {"thread_id": f"d1-h2t-{uuid.uuid4()}"}}
    out1 = graph.invoke(_full_mode_initial_state(), cfg)
    assert "__interrupt__" in out1
    final = graph.invoke(Command(resume={"decision": "terminate"}), cfg)
    assert final.get("current_step") == "cancelled_by_user", "terminate 应写 cancelled_by_user 终止态"
    # resume 整节点重跑期间 guard 命中，sandbox 仍只跑那 1 次。
    assert cnt["prepare"] == 1, (
        f"resume 重跑不应重新跑 sandbox，prepare 应恒 1，实际 {cnt['prepare']}"
    )


def test_h2b_resume_export_code_reaches_reporting(monkeypatch):
    """interrupt#2 resume export_code → 经真实路由到 reporting（降级导出）。"""
    cnt = _patch_sandbox(
        monkeypatch,
        run_results=[FakeRunResult(exit_code=1, stderr="CUDA out of memory")],
    )
    saver = InMemorySaver()
    graph = _build_real_route_execution_graph(saver)
    cfg = {"configurable": {"thread_id": f"d1-h2e-{uuid.uuid4()}"}}
    graph.invoke(_full_mode_initial_state(), cfg)
    final = graph.invoke(Command(resume={"decision": "export_code"}), cfg)
    assert final.get("report_path") == "/tmp/d1r/report.md", "export_code 应经路由到 reporting 节点产报告"
    assert cnt["prepare"] == 1


def test_h2c_resume_revise_plan_reaches_planning(monkeypatch):
    """interrupt#2 resume revise_plan → 经真实路由到 planning（改计划回流）。"""
    _patch_sandbox(
        monkeypatch,
        run_results=[FakeRunResult(exit_code=1, stderr="CUDA out of memory")],
    )
    saver = InMemorySaver()
    graph = _build_real_route_execution_graph(saver)
    cfg = {"configurable": {"thread_id": f"d1-h2r-{uuid.uuid4()}"}}
    graph.invoke(_full_mode_initial_state(), cfg)
    final = graph.invoke(
        Command(resume={"decision": "revise_plan", "user_feedback": "换更小模型"}), cfg
    )
    # revise_plan 路由到 planning（stub_planning 写 current_step=planning，再 → END）。
    assert final.get("current_step") == "planning", "revise_plan 应经路由进入 planning 节点"
    # execution 出口 revise_plan 应清 approved=False（供 _route_after_planning 走 self）。
    plan = final.get("reproduction_plan") or {}
    assert plan.get("approved") is False, "revise_plan 出口应清 approved=False"


# ===========================================================================
# 重锤组 H-3：retry_coding 修复回边在真实编译图里成边（execution→coding→execution）
# ===========================================================================


def test_h3_retry_coding_back_edge_in_real_graph(monkeypatch):
    """可修复失败 → retry_coding → 真实路由把 'coding' 映射回 coding 节点（修复回边成边）。

    第一次 run 可修复失败（ModuleNotFoundError=import 可修复）→ execution 置 retry_coding
    → 路由 'coding' → stub_coding 清 await → 回 execution，第二次 run 成功 → reporting。
    断言 execution 被进入 2 次（首次失败 + 修复后成功），最终到 reporting。
    """
    cnt = _patch_sandbox(
        monkeypatch,
        run_results=[
            FakeRunResult(exit_code=1, stderr="ModuleNotFoundError: No module named 'torch'"),
            FakeRunResult(exit_code=0, stdout='<METRICS>{"accuracy": 0.9}</METRICS>'),
        ],
    )
    saver = InMemorySaver()
    graph = _build_real_route_execution_graph(saver)
    cfg = {"configurable": {"thread_id": f"d1-h3-{uuid.uuid4()}"}}
    final = graph.invoke(_full_mode_initial_state(), cfg)
    # 修复回边走通：sandbox 跑了 2 次（首次失败 + 修复后成功），prepare 2 次。
    assert cnt["prepare"] == 2, (
        f"修复回边应使 execution 进入 2 次（sandbox prepare 2 次），实际 {cnt['prepare']}"
    )
    assert final.get("report_path") == "/tmp/d1r/report.md", "修复成功后应到 reporting"


# ===========================================================================
# 边界组 B-1/B-2：路由函数返回值集合 vs add_conditional_edges 映射键一致性
# ===========================================================================


def _conditional_branch_mappings(compiled):
    """从编译图提取 execution / coding 条件边的 path_map（返回值 → 目的地）。

    langgraph 编译图把条件边存于 builder.branches[source][name].ends（path_map）。
    """
    builder = compiled.builder  # StateGraph
    branches = builder.branches
    out: Dict[str, Dict[str, str]] = {}
    for source, named in branches.items():
        for _name, branch in named.items():
            ends = getattr(branch, "ends", None)
            if ends:
                out[source] = dict(ends)
    return out


def test_b1_execution_branch_mapping_keys_match_router_returns():
    """_route_after_execution 所有可能返回值 ⊆ execution 条件边映射键（无悬空返回值致 KeyError）。"""
    g = build_graph(checkpointer=InMemorySaver())
    mappings = _conditional_branch_mappings(g)
    exec_keys = set(mappings.get("execution", {}).keys())
    # 路由函数全部返回值（穷举源码分支）。
    router_returns = {"execution", "coding", "planning", "reporting", "end"}
    assert router_returns <= exec_keys, (
        f"execution 路由返回值 {router_returns} 必须全部在映射键 {exec_keys} 内（防 KeyError）"
    )
    # 命门：'execution' 键必须映射回 execution 节点自身（self-loop 成边）。
    assert mappings["execution"]["execution"] == "execution", (
        "execution 条件边 'execution' 键必须指回 execution 节点形成 self-loop（interrupt#2 命门）"
    )


def test_b2_coding_branch_mapping_keys_match_router_returns():
    """_route_after_coding 返回值 ⊆ coding 条件边映射键。"""
    g = build_graph(checkpointer=InMemorySaver())
    mappings = _conditional_branch_mappings(g)
    coding_keys = set(mappings.get("coding", {}).keys())
    router_returns = {"to_execution", "skip_execution"}
    assert router_returns <= coding_keys, (
        f"coding 路由返回值 {router_returns} 必须全部在映射键 {coding_keys} 内"
    )
    assert mappings["coding"]["to_execution"] == "execution"
    assert mappings["coding"]["skip_execution"] == "reporting"


def test_b2b_exhaustive_router_returns_no_unmapped_branch():
    """穷举 _route_after_execution 在各种 state 下的返回值，确保全部落在映射键内（运行时正向证）。"""
    g = build_graph(checkpointer=InMemorySaver())
    mappings = _conditional_branch_mappings(g)
    exec_keys = set(mappings.get("execution", {}).keys())

    sample_states = [
        {"_dev_loop_route": "await_dev_loop_interrupt"},
        {"_dev_loop_route": "retry_coding"},
        {"user_fix_decision": "revise_plan"},
        {"user_fix_decision": "terminate", "current_step": "cancelled_by_user"},
        {"current_step": "cancelled_by_user"},
        {"user_fix_decision": "export_code"},
        {"_dev_loop_route": None, "execution_result": {"success": True}},
        {"_dev_loop_route": None, "degraded_nodes": ["execution"]},
        {},
    ]
    for st in sample_states:
        ret = _route_after_execution(st)
        assert ret in exec_keys, f"路由返回 {ret!r}（state={st}）不在映射键 {exec_keys} 内，会 KeyError"


# ===========================================================================
# 边界组 B-3：await self-loop 优先级（防 interrupt#2 漏触发的防御性优先级）
# ===========================================================================


@pytest.mark.parametrize(
    "state,expected",
    [
        # await + 误带 terminate：仍 self-loop（命门优先）。
        ({"_dev_loop_route": "await_dev_loop_interrupt", "user_fix_decision": "terminate"}, "execution"),
        # await + 误带 cancelled_by_user：仍 self-loop。
        ({"_dev_loop_route": "await_dev_loop_interrupt", "current_step": "cancelled_by_user"}, "execution"),
        # await + 误带 export_code：仍 self-loop。
        ({"_dev_loop_route": "await_dev_loop_interrupt", "user_fix_decision": "export_code"}, "execution"),
        # retry + 误带 revise_plan：仍回 coding（retry 优先于 user_fix_decision）。
        ({"_dev_loop_route": "retry_coding", "user_fix_decision": "revise_plan"}, "coding"),
    ],
)
def test_b3_dev_loop_route_priority_defensive(state, expected):
    """_dev_loop_route 两路严格优先于 user_fix_decision 三态（self-loop 命门最先命中，防漏触发）。"""
    assert _route_after_execution(state) == expected


# ===========================================================================
# 边界组 B-4：planning 出边与 sp2 行为等价（code_only 区分点未渗入 planning 出边）
# ===========================================================================


@pytest.mark.parametrize(
    "state,expected",
    [
        ({"current_step": "cancelled_by_user"}, "end"),
        ({"reproduction_plan": {"approved": True}}, "next"),
        ({"reproduction_plan": {"approved": False}}, "self"),
        ({"reproduction_plan": {}}, "self"),
        ({}, "self"),
        # approve 但 cancel 优先（sp2 既有优先级）。
        ({"current_step": "cancelled_by_user", "reproduction_plan": {"approved": True}}, "end"),
        # ⚠️ code_only 模式不应在 planning 出边区分：approved=True 仍走 next（区分点在 coding 出边）。
        (
            {"reproduction_plan": {"approved": True}, "execution_mode": ExecutionMode.CODE_ONLY},
            "next",
        ),
    ],
)
def test_b4_route_after_planning_behavior_unchanged(state, expected):
    """sp2 _route_after_planning 行为等价：3 路 + code_only 不渗入 planning 出边（AC-S3-10）。"""
    assert _route_after_planning(state) == expected


def test_b4b_planning_route_ignores_execution_mode():
    """显式反证：planning 出边对 execution_mode 完全不敏感（FULL 与 CODE_ONLY 同 approved 走同一路）。"""
    full = {"reproduction_plan": {"approved": True}, "execution_mode": ExecutionMode.FULL}
    code_only = {"reproduction_plan": {"approved": True}, "execution_mode": ExecutionMode.CODE_ONLY}
    assert _route_after_planning(full) == _route_after_planning(code_only) == "next"


# ===========================================================================
# 边界组 B-5：code_only 区分点严格在 coding 出边（多形态 execution_mode）
# ===========================================================================


@pytest.mark.parametrize(
    "mode,expected",
    [
        (ExecutionMode.CODE_ONLY, "skip_execution"),
        (ExecutionMode.CODE_ONLY.value, "skip_execution"),
        ("code_only", "skip_execution"),
        (ExecutionMode.FULL, "to_execution"),
        (ExecutionMode.FULL.value, "to_execution"),
        ("full", "to_execution"),
        (None, "to_execution"),
        ("unknown_mode", "to_execution"),  # 非法值兜底 FULL（不误跳 execution）
    ],
)
def test_b5_code_only_split_strictly_in_coding_out_edge(mode, expected):
    """coding 出边 code_only 分流多形态：CODE_ONLY→skip_execution；其余/非法→to_execution。"""
    assert _route_after_coding({"execution_mode": mode}) == expected


def test_b5b_coding_route_no_field_defaults_full():
    """coding 出边无 execution_mode 字段 → FULL 语义（to_execution，不误跳 execution）。"""
    assert _route_after_coding({}) == "to_execution"


# ===========================================================================
# 边界组 B-6：reporting 是唯一只通 END 的业务终点；两 mode 成功都汇聚 reporting
# ===========================================================================


def test_b6_reporting_only_to_end():
    """reporting 出边只有 END（无业务后继）。"""
    g = build_graph(checkpointer=InMemorySaver())
    drawable = g.get_graph()
    rep_succ = {e.target for e in drawable.edges if e.source == "reporting"}
    assert rep_succ and all(
        t == "__end__" or t.startswith("__end") for t in rep_succ
    ), f"reporting 后继应仅为 END，实际 {rep_succ}"


def test_b6b_both_modes_converge_reporting():
    """FULL 成功（_route_after_execution）与 CODE_ONLY（_route_after_coding）都最终到 reporting。"""
    # FULL 成功兜底 → reporting。
    assert _route_after_execution({"_dev_loop_route": None, "execution_result": {"success": True}}) == "reporting"
    # CODE_ONLY → skip_execution → reporting。
    g = build_graph(checkpointer=InMemorySaver())
    mappings = _conditional_branch_mappings(g)
    assert mappings["coding"]["skip_execution"] == "reporting"


# ===========================================================================
# 边界组 B-7：路由函数纯读（零 reducer 红线相关）—— 调用后 state list 字段原对象不被 mutate
# ===========================================================================


def test_b7_routers_do_not_mutate_state_lists():
    """三路由函数调用后，state 的 list 字段同一对象、长度不变、内容不变（只读证）。"""
    node_errors = [{"node": "execution", "error_message": "[error_category=hardware] x"}]
    degraded = ["coding"]
    history = [{"round": 1}]
    state = {
        "_dev_loop_route": "retry_coding",
        "user_fix_decision": "revise_plan",
        "current_step": "execution",
        "execution_mode": ExecutionMode.CODE_ONLY,
        "reproduction_plan": {"approved": False},
        "node_errors": node_errors,
        "degraded_nodes": degraded,
        "fix_loop_history": history,
    }
    ne_id, dg_id, hi_id = id(node_errors), id(degraded), id(history)

    _route_after_execution(state)
    _route_after_coding(state)
    _route_after_planning(state)

    assert id(state["node_errors"]) == ne_id and len(state["node_errors"]) == 1
    assert id(state["degraded_nodes"]) == dg_id and state["degraded_nodes"] == ["coding"]
    assert id(state["fix_loop_history"]) == hi_id and len(state["fix_loop_history"]) == 1
    # 标量字段也不被改。
    assert state["_dev_loop_route"] == "retry_coding"
    assert state["user_fix_decision"] == "revise_plan"
