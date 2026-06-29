"""E2 - 执行监控页 ui/pages/execution_monitor.py 独立验收补强用例（Sprint 3，S3-10）。

由测试工程师代理独立编写，覆盖开发自测 ``tests/test_sprint3_e2.py`` 的遗漏与盲点。

核心重锤（任务书 CP-E2-3 命门）：**端到端契约重锤**——不止 mock 捕获 resume_with 实参，
还把本页 ``_build_decision_payload`` 产出的三种 payload 喂给 ``core/nodes/execution.py``
的 interrupt#2 resume 解析逻辑（``_route_user_fix_decision`` / ``_maybe_interrupt_or_return``），
用最小 self-loop StateGraph + InMemorySaver + ``Command(resume=payload)`` 端到端跑通，断言三
payload 真能被正确路由到 terminate / revise_plan / export_code 三态（与 C3 reinforce 同构）。

补强维度：
    - G1 三决策 payload 端到端路由（UI 构造 → 真实 execution 节点 resume → 三态结果）；
    - G2 契约守门双向红线（本页常量 == 节点 payload options / INTERRUPT_KIND，任一端改取值即红）；
    - G3 interrupt_kind 非 dev_loop_failure / 无 interrupt 时不误展示决策面板（多形态）；
    - G4 fix_loop_history 空 / 单轮 / 多轮 + 缺字段 + 非 dict 渲染边界；
    - G5 output_truncated 双探测路径穷举 + 顶层键优先级（ExecutionResult TypedDict 无该键的反证）；
    - G6 _should_jump_to_report 真值表补强（report_path 仅空白串 / current_step 多形态）；
    - G7 execution_errors 键名核实（payload 优先读 execution_errors，兜底 execution_result.errors，两路都不崩）；
    - G8 shadcn iframe → 核心终态/降级/截断文案改原生 st.error/warning/info（AppTest 可见可断言）；
    - G9 user_feedback 空/非空 + revise_plan payload 形态；
    - G10 决策面板 payload 形态与节点 options 完全闭环（无臆造决策值）。

约束（与开发自测一致 + 任务隔离边界）：mock GraphController（patch app._get_controller），
AppTest 跑真实 render()；端到端 resume 用 mock sandbox（patch prepare_venv/run_in_venv/
collect_artifacts）+ InMemorySaver + Command（唯一 uuid4 thread_id 防串）；不跑真实 venv/LLM/
deepxiv；只新建本文件 + 验收报告，不碰任何生产代码/对方 E3 文件。
"""

from __future__ import annotations

import importlib
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

import pytest
from streamlit.testing.v1 import AppTest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import (  # noqa: E402
    MAX_DEV_LOOP_LLM_CALLS,
    MAX_FIX_LOOP_COUNT,
    STREAMLIT_PAGE_REPORT,
    WORKSPACE_DIR,
)

# 用 importlib 取真实子模块（避免 core/nodes/__init__ 显式 export execution callable 遮蔽
# 模块——「from core.nodes import execution」拿到的是 callable 而非模块，已知坑6）。
exec_node = importlib.import_module("core.nodes.execution")


def _mod():
    """用 importlib 取页面模块（同理避免 __init__ 遮蔽子模块）。"""
    return importlib.import_module("ui.pages.execution_monitor")


# =========================================================================== #
# 共享夹具：mock state / payload / controller / AppTest 脚本
# =========================================================================== #
def _make_fix_history(rounds: int = 2) -> List[Dict[str, Any]]:
    return [
        {
            "round_number": i + 1,
            "error_summary": f"第 {i + 1} 轮：依赖装不上（numpy）",
            "error_category": "dependency",
            "fix_strategy": f"第 {i + 1} 轮：固定 numpy==1.26 重装",
            "timestamp": "2026-06-28T00:00:00Z",
        }
        for i in range(rounds)
    ]


def _make_state(
    *,
    current_step: str = "execution",
    fix_loop_count: int = 1,
    fix_loop_history: Optional[List[Dict[str, Any]]] = None,
    execution_result: Optional[Dict[str, Any]] = None,
    node_errors: Optional[List[Dict[str, Any]]] = None,
    degraded_nodes: Optional[List[str]] = None,
    report_path: Optional[str] = None,
    error: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "current_step": current_step,
        "fix_loop_count": fix_loop_count,
        "fix_loop_history": fix_loop_history if fix_loop_history is not None else _make_fix_history(),
        "execution_result": execution_result,
        "node_errors": node_errors if node_errors is not None else [],
        "degraded_nodes": degraded_nodes if degraded_nodes is not None else [],
        "report_path": report_path,
        "error": error,
    }


def _make_controller_mock(
    *,
    state: Optional[Dict[str, Any]] = None,
    is_interrupted: bool = False,
    interrupt_kind: Optional[str] = None,
    interrupt_payload: Optional[Dict[str, Any]] = None,
    worker_error: Optional[Exception] = None,
) -> MagicMock:
    controller = MagicMock()
    controller.poll_state.return_value = state
    controller.is_interrupted.return_value = is_interrupted
    controller.interrupt_kind.return_value = interrupt_kind
    controller.get_interrupt_payload.return_value = interrupt_payload
    controller.get_worker_error.return_value = worker_error
    return controller


_SCRIPT_HEADER = """
import streamlit as st
st.session_state.setdefault("thread_id", "task-e2r-001")
st.session_state.setdefault("current_page", "execution")
page = st.session_state.get("current_page", "execution")
if page == "execution":
    from ui.pages.execution_monitor import render
    render()
elif page == "report":
    st.write("REPORT_STUB")
elif page == "review":
    st.write("REVIEW_STUB")
else:
    st.write("INPUT_STUB")
"""


def _script_with_thread(thread_id: str, extra: str = "") -> str:
    return f"""
import streamlit as st
st.session_state.setdefault("thread_id", {thread_id!r})
st.session_state.setdefault("current_page", "execution")
{extra}
page = st.session_state.get("current_page", "execution")
if page == "execution":
    from ui.pages.execution_monitor import render
    render()
elif page == "report":
    st.write("REPORT_STUB")
elif page == "review":
    st.write("REVIEW_STUB")
else:
    st.write("INPUT_STUB")
"""


def _run(controller: MagicMock, script: str) -> AppTest:
    with patch("app._get_controller", return_value=controller):
        at = AppTest.from_string(script)
        at.run()
    return at


def _collect_text(at: AppTest) -> str:
    parts: List[str] = []
    for collection in (at.title, at.subheader, at.caption, at.markdown,
                       at.text, at.warning, at.info, at.error):
        for el in collection:
            parts.append(str(getattr(el, "value", "")))
    for el in getattr(at, "code", []):
        parts.append(str(getattr(el, "value", "")))
    return "\n".join(parts)


# =========================================================================== #
# 端到端 sandbox mock + 最小 self-loop 图（与 C3 reinforce 同构，独立维护）
# =========================================================================== #
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


def _e2e_base_state(**overrides: Any) -> Dict[str, Any]:
    from core.state import ExecutionMode

    state: Dict[str, Any] = {
        "code_output_dir": str(WORKSPACE_DIR / "e2r-e2e" / "code"),
        "reproduction_plan": {
            "execution_steps": [{"step_name": "run", "command": "python run.py"}],
            "environment": {},
        },
        "paper_analysis": {"metrics": ["accuracy"]},
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


def _patch_sandbox_fail(monkeypatch: pytest.MonkeyPatch, *, stderr: str) -> Dict[str, int]:
    """patch sandbox 让本回合执行失败（用于触发 interrupt#2）。"""
    cnt = {"prepare": 0, "run": 0}

    def fake_prepare_venv(*a: Any, **k: Any) -> FakePrepareResult:
        cnt["prepare"] += 1
        return FakePrepareResult()

    def fake_run_in_venv(*a: Any, **k: Any) -> FakeRunResult:
        cnt["run"] += 1
        return FakeRunResult(exit_code=1, stderr=stderr)

    def fake_collect_artifacts(*a: Any, **k: Any) -> List[str]:
        return []

    monkeypatch.setattr(exec_node, "prepare_venv", fake_prepare_venv)
    monkeypatch.setattr(exec_node, "run_in_venv", fake_run_in_venv)
    monkeypatch.setattr(exec_node, "collect_artifacts", fake_collect_artifacts)
    return cnt


def _build_self_loop_graph(checkpointer):
    """模拟 D1 _route_after_execution 的 self-loop 关键分支（await→execution，其余→END）。"""
    from langgraph.graph import END, START, StateGraph

    from core.state import GlobalState

    g = StateGraph(GlobalState)
    g.add_node("execution", exec_node.execution)
    g.add_edge(START, "execution")

    def route(state: Dict[str, Any]) -> str:
        if state.get("_dev_loop_route") == exec_node._ROUTE_AWAIT_INTERRUPT:
            return "execution"
        return "end"

    g.add_conditional_edges("execution", route, {"execution": "execution", "end": END})
    return g.compile(checkpointer=checkpointer)


def _make_saver():
    from langgraph.checkpoint.memory import InMemorySaver

    return InMemorySaver()


def _drive_to_interrupt(monkeypatch, *, stderr: str, fix_loop_count: int = MAX_FIX_LOOP_COUNT,
                        **state_overrides):
    """跑到 interrupt#2 暂停态，返回 (graph, cfg, prepare_count)。"""
    from langgraph.types import Command  # noqa: F401  (import 校验可用)

    cnt = _patch_sandbox_fail(monkeypatch, stderr=stderr)
    saver = _make_saver()
    graph = _build_self_loop_graph(saver)
    cfg = {"configurable": {"thread_id": f"e2r-{uuid.uuid4().hex[:8]}"}}
    init = _e2e_base_state(fix_loop_count=fix_loop_count, **state_overrides)
    out1 = graph.invoke(init, cfg)
    assert "__interrupt__" in out1, "应在修复耗尽 / 不可修复时暂停于 interrupt#2"
    assert cnt["prepare"] == 1, "首次失败 sandbox 副作用应恰为 1"
    return graph, cfg, cnt


# =========================================================================== #
# G1：端到端契约重锤——本页构造的 payload 真能被 execution 节点正确路由（CP-E2-3 命门）
# =========================================================================== #
def test_g1_terminate_payload_routes_to_cancelled_end_to_end(monkeypatch):
    """G1：本页 _build_decision_payload('terminate') 喂给真实 execution resume → cancelled_by_user。"""
    from langgraph.types import Command

    mod = _mod()
    payload = mod._build_decision_payload(mod._DECISION_TERMINATE)
    # 不臆造：用本页真实产出的 payload 驱动节点。
    assert payload == {"decision": "terminate"}

    graph, cfg, cnt = _drive_to_interrupt(monkeypatch, stderr="RuntimeError: boom")
    graph.invoke(Command(resume=payload), cfg)
    final = graph.get_state(cfg).values

    assert final["user_fix_decision"] == "terminate"
    assert final["current_step"] == "cancelled_by_user"  # → END
    assert cnt["prepare"] == 1  # resume 重跑幂等：sandbox 不重跑


def test_g1_export_code_payload_routes_to_degraded_end_to_end(monkeypatch):
    """G1：本页 _build_decision_payload('export_code') → 降级 + user_fix_decision=export_code（→ reporting）。"""
    from langgraph.types import Command

    mod = _mod()
    payload = mod._build_decision_payload(mod._DECISION_EXPORT_CODE)
    assert payload == {"decision": "export_code"}

    graph, cfg, cnt = _drive_to_interrupt(monkeypatch, stderr="RuntimeError: CUDA out of memory")
    graph.invoke(Command(resume=payload), cfg)
    final = graph.get_state(cfg).values

    assert final["user_fix_decision"] == "export_code"
    assert exec_node.NODE_NAME in final["degraded_nodes"]
    # 降级 → reporting：_dev_loop_route 已清空（非 await/retry）。
    assert final.get("_dev_loop_route") is None
    assert cnt["prepare"] == 1


def test_g1_revise_plan_payload_routes_to_planning_end_to_end(monkeypatch):
    """G1：本页 _build_decision_payload('revise_plan', fb) → fix_loop_count 清零 + history 保留 + approved=False。"""
    from langgraph.types import Command

    mod = _mod()
    feedback_text = "换用官方仓库训练脚本，跳过缺失数据集步骤"
    payload = mod._build_decision_payload(mod._DECISION_REVISE_PLAN, feedback_text)
    assert payload == {"decision": "revise_plan", "user_feedback": feedback_text}

    prior_hist = [
        {"round_number": 1, "error_summary": "s1", "error_category": "runtime",
         "fix_strategy": "x", "timestamp": "t1"},
        {"round_number": 2, "error_summary": "s2", "error_category": "import",
         "fix_strategy": "y", "timestamp": "t2"},
    ]
    graph, cfg, cnt = _drive_to_interrupt(
        monkeypatch, stderr="RuntimeError: boom", fix_loop_history=list(prior_hist)
    )
    graph.invoke(Command(resume=payload), cfg)
    final = graph.get_state(cfg).values

    assert final["user_fix_decision"] == "revise_plan"
    assert final["fix_loop_count"] == 0  # 回问点2：清零
    assert len(final["fix_loop_history"]) == 2  # history 保留
    assert final["reproduction_plan"]["approved"] is False  # approved 清掉
    # 节点把本页传入的 user_feedback 织进 _planning_user_feedback 修复上下文。
    assert feedback_text in final["_planning_user_feedback"]
    assert cnt["prepare"] == 1


def test_g1_revise_plan_empty_feedback_routes_end_to_end(monkeypatch):
    """G1：revise_plan + 空 user_feedback（本页 _build_decision_payload 兜底空串）端到端不崩。"""
    from langgraph.types import Command

    mod = _mod()
    payload = mod._build_decision_payload(mod._DECISION_REVISE_PLAN, "")
    assert payload == {"decision": "revise_plan", "user_feedback": ""}

    graph, cfg, _cnt = _drive_to_interrupt(monkeypatch, stderr="RuntimeError: boom")
    # 空 feedback：节点端 decision.get("user_feedback") or "" 兜底，不应抛。
    graph.invoke(Command(resume=payload), cfg)
    final = graph.get_state(cfg).values
    assert final["user_fix_decision"] == "revise_plan"
    assert final["fix_loop_count"] == 0
    # _planning_user_feedback 仍生成（不含具体反馈但含修复上下文框架）。
    assert "修订复现计划" in final["_planning_user_feedback"]


def test_g1_all_three_payloads_distinct_terminal_states(monkeypatch):
    """G1：三 payload 端到端导向三个互异终态（terminate/revise_plan/export_code），无串台。"""
    from langgraph.types import Command

    mod = _mod()
    results: Dict[str, Dict[str, Any]] = {}
    for decision in (mod._DECISION_TERMINATE, mod._DECISION_REVISE_PLAN, mod._DECISION_EXPORT_CODE):
        graph, cfg, _ = _drive_to_interrupt(monkeypatch, stderr="RuntimeError: boom")
        payload = mod._build_decision_payload(decision, "fb" if decision == mod._DECISION_REVISE_PLAN else "")
        graph.invoke(Command(resume=payload), cfg)
        results[decision] = graph.get_state(cfg).values

    # terminate → cancelled_by_user。
    assert results["terminate"]["current_step"] == "cancelled_by_user"
    assert results["terminate"]["user_fix_decision"] == "terminate"
    # revise_plan → planning（approved 清掉，未 cancelled）。
    assert results["revise_plan"]["reproduction_plan"]["approved"] is False
    assert results["revise_plan"].get("current_step") != "cancelled_by_user"
    assert results["revise_plan"]["user_fix_decision"] == "revise_plan"
    # export_code → degraded（未 cancelled，未清 approved）。
    assert exec_node.NODE_NAME in results["export_code"]["degraded_nodes"]
    assert results["export_code"]["user_fix_decision"] == "export_code"


# =========================================================================== #
# G2：契约守门双向红线——本页常量 == 节点 options / INTERRUPT_KIND（任一端改即红）
# =========================================================================== #
def test_g2_page_decisions_equal_node_options_exhaustive():
    """G2：本页三决策常量集合 == 节点 _build_dev_loop_interrupt_payload options 字段（防臆造）。"""
    mod = _mod()
    page_decisions = {
        mod._DECISION_TERMINATE,
        mod._DECISION_REVISE_PLAN,
        mod._DECISION_EXPORT_CODE,
    }
    node_options = set(exec_node._build_dev_loop_interrupt_payload(
        {"errors": ["x"]},
        exec_node.ExecutionFeedback(
            exec_node.ErrorCategory.RUNTIME, True, "s", "h", "stderr"
        ),
        {"fix_loop_count": 3, "fix_loop_history": []},
    )["options"])
    assert page_decisions == node_options
    # 数量也对齐（防一端多一项不被集合差捕获的退化）。
    assert len(page_decisions) == 3


def test_g2_interrupt_kind_constant_aligned_both_sides():
    """G2：本页 _INTERRUPT_KIND_DEV_LOOP == 节点 INTERRUPT_KIND（== payload 实际写入值）。"""
    mod = _mod()
    assert mod._INTERRUPT_KIND_DEV_LOOP == exec_node.INTERRUPT_KIND
    # 节点 payload 实际写入的 interrupt_kind 也是这个值（端到端一致，非仅常量名一致）。
    payload = exec_node._build_dev_loop_interrupt_payload(
        {"errors": []},
        exec_node.ExecutionFeedback(exec_node.ErrorCategory.RUNTIME, True, "s", "h", "e"),
        {"fix_loop_count": 1, "fix_loop_history": []},
    )
    assert payload["interrupt_kind"] == mod._INTERRUPT_KIND_DEV_LOOP


def test_g2_node_resume_router_accepts_exactly_page_decisions(monkeypatch):
    """G2：节点 _route_user_fix_decision 对本页三决策值均有专门分支（无 fallthrough 到兜底 terminate）。

    反证：若节点端某决策值改名（如 export→download），本页传该值会被兜底成 terminate（与 export 行为
    不同），本测试通过比对「export_code 走降级分支」vs「兜底 terminate 走 cancelled」捕获该退化。
    """
    mod = _mod()
    base_updates: Dict[str, Any] = {}
    base_state = {"fix_loop_count": 2, "fix_loop_history": [], "reproduction_plan": {},
                  "node_errors": [], "degraded_nodes": []}

    # export_code → 降级（degraded_nodes 含 execution，非 cancelled）。
    out_export = exec_node._route_user_fix_decision(
        mod._build_decision_payload(mod._DECISION_EXPORT_CODE), dict(base_updates), dict(base_state)
    )
    assert out_export.get("current_step") != "cancelled_by_user"
    assert out_export["user_fix_decision"] == "export_code"

    # 一个「不在本页决策集合」的臆造值 → 兜底 terminate（cancelled）。证明 export_code 走的是专门分支。
    out_bogus = exec_node._route_user_fix_decision(
        {"decision": "bogus_value"}, dict(base_updates), dict(base_state)
    )
    assert out_bogus["current_step"] == "cancelled_by_user"
    # 两者行为不同 → 证明 export_code 不是走兜底分支。
    assert out_export["user_fix_decision"] != out_bogus["user_fix_decision"]


# =========================================================================== #
# G3：interrupt_kind 非 dev_loop_failure / 无 interrupt 时不误展示决策面板
# =========================================================================== #
def test_g3_planning_interrupt_no_panel_redirects_review():
    """G3：interrupt_kind=='planning' → 不展示决策面板，跳回 review 页。"""
    controller = _make_controller_mock(
        state=_make_state(current_step="planning"),
        is_interrupted=True,
        interrupt_kind="planning",
        interrupt_payload={"interrupt_kind": "planning"},
    )
    at = _run(controller, _SCRIPT_HEADER)
    assert at.session_state["current_page"] == "review"
    assert "btn_dev_loop_terminate" not in {b.key for b in at.button}


def test_g3_interrupt_kind_none_no_panel():
    """G3：is_interrupted=True 但 interrupt_kind 返回 None（异常态）→ 不展示决策面板（兜底跳 review）。"""
    controller = _make_controller_mock(
        state=_make_state(current_step="execution"),
        is_interrupted=True,
        interrupt_kind=None,
        interrupt_payload=None,
    )
    at = _run(controller, _SCRIPT_HEADER)
    # kind != dev_loop_failure → 走 review 跳转分支，不渲染决策面板。
    assert "btn_dev_loop_terminate" not in {b.key for b in at.button}
    assert at.session_state["current_page"] == "review"


def test_g3_not_interrupted_no_panel_normal_render():
    """G3：is_interrupted=False（即便 interrupt_kind 残留 dev_loop_failure）→ 不进决策面板，正常渲染。"""
    controller = _make_controller_mock(
        state=_make_state(current_step="execution"),
        is_interrupted=False,
        interrupt_kind="dev_loop_failure",  # 残留值，但 is_interrupted=False 应优先
    )
    at = _run(controller, _SCRIPT_HEADER)
    assert not at.exception, at.exception
    assert "btn_dev_loop_terminate" not in {b.key for b in at.button}
    # 正常渲染（执行监控标题出现，is_interrupted 反例下不误判 interrupt）。
    assert "执行监控" in _collect_text(at)


def test_g3_dev_loop_failure_shows_panel_not_normal_render():
    """G3 正面：dev_loop_failure → 决策面板（不渲染正常进度页的「实时观察」caption）。"""
    controller = _make_controller_mock(
        state=_make_state(current_step="execution"),
        is_interrupted=True,
        interrupt_kind="dev_loop_failure",
        interrupt_payload={
            "interrupt_kind": "dev_loop_failure", "fix_loop_count": 3,
            "error_category": "runtime", "error_summary": "运行时异常",
            "execution_errors": ["[error_category=runtime] 运行时异常"],
            "fix_loop_history": _make_fix_history(3), "options": ["terminate", "revise_plan", "export_code"],
        },
    )
    at = _run(controller, _SCRIPT_HEADER)
    assert not at.exception, at.exception
    keys = {b.key for b in at.button}
    assert {"btn_dev_loop_terminate", "btn_dev_loop_revise_plan", "btn_dev_loop_export_code"} <= keys
    text = _collect_text(at)
    assert "执行失败决策" in text


# =========================================================================== #
# G4：fix_loop_history 空 / 单轮 / 多轮 + 缺字段 + 非 dict 渲染边界
# =========================================================================== #
def test_g4_summarize_fix_history_single_round():
    mod = _mod()
    rows = mod._summarize_fix_history(_make_fix_history(1))
    assert len(rows) == 1
    assert rows[0]["round"] == "1"


def test_g4_summarize_fix_history_many_rounds_order_preserved():
    mod = _mod()
    rows = mod._summarize_fix_history(_make_fix_history(3))
    assert [r["round"] for r in rows] == ["1", "2", "3"]


def test_g4_summarize_fix_history_missing_fields_and_non_dict():
    mod = _mod()
    history = [
        {"round_number": 1},                 # 缺 summary/strategy/category
        "not-a-dict",                        # 非 dict → 跳过
        {"error_summary": "no round", "fix_strategy": "fs", "error_category": "import"},
        None,                                # None → 跳过
        42,                                  # int → 跳过
    ]
    rows = mod._summarize_fix_history(history)
    assert len(rows) == 2
    assert rows[0]["round"] == "1"
    assert rows[0]["error_summary"] == "(无摘要)"
    assert rows[0]["fix_strategy"] == "(未记录修复策略)"
    assert rows[1]["round"] == "?"          # 缺 round_number → "?"
    assert rows[1]["error_summary"] == "no round"


def test_g4_empty_history_renders_no_history_section():
    """G4：fix_loop_history 空 → 进度页不渲染「修复历程」区块，不崩。"""
    state = _make_state(fix_loop_count=0, fix_loop_history=[])
    controller = _make_controller_mock(state=state, is_interrupted=False)
    at = _run(controller, _SCRIPT_HEADER)
    assert not at.exception, at.exception
    text = _collect_text(at)
    assert "修复历程" not in text


def test_g4_multi_round_history_renders_each_round_in_apptest():
    """G4：多轮 history → 进度页每轮摘要均出现（expander label 含轮次 + 错误摘要）。"""
    state = _make_state(fix_loop_count=3, fix_loop_history=_make_fix_history(3))
    controller = _make_controller_mock(state=state, is_interrupted=False)
    at = _run(controller, _SCRIPT_HEADER)
    assert not at.exception, at.exception
    text = _collect_text(at)
    assert "修复历程" in text
    # 三轮的错误摘要片段均渲染（expander label / body）。
    assert "依赖装不上" in text


def test_g4_panel_renders_history_when_present_in_payload():
    """G4：决策面板 payload 含 fix_loop_history → 面板内渲染「修复历程」区块。"""
    controller = _make_controller_mock(
        state=_make_state(current_step="execution"),
        is_interrupted=True,
        interrupt_kind="dev_loop_failure",
        interrupt_payload={
            "interrupt_kind": "dev_loop_failure", "fix_loop_count": 3,
            "error_category": "dependency", "error_summary": "依赖反复装不上",
            "fix_loop_history": _make_fix_history(2),
            "execution_errors": ["[error_category=dependency] 依赖装不上"],
            "options": ["terminate", "revise_plan", "export_code"],
        },
    )
    at = _run(controller, _SCRIPT_HEADER)
    assert not at.exception, at.exception
    text = _collect_text(at)
    assert "修复历程" in text


# =========================================================================== #
# G5：output_truncated 双探测路径穷举 + 顶层键优先级
# =========================================================================== #
def test_g5_truncation_path1_top_level_key_only():
    """G5：仅顶层 output_truncated=True（logs 无标记）→ 截断（路径1独立生效）。"""
    mod = _mod()
    assert mod._logs_truncated({"output_truncated": True, "logs": "干净日志无标记"}) is True


def test_g5_truncation_path2_logs_marker_only():
    """G5：顶层 output_truncated 缺失/False，仅 logs 含标记 → 截断（路径2独立生效）。"""
    mod = _mod()
    # ExecutionResult TypedDict 无 output_truncated 字段，正常 state 走路径2（logs 标记）。
    assert mod._logs_truncated({"logs": "...output_truncated..."}) is True
    assert mod._logs_truncated({"logs": "...日志已截断..."}) is True
    assert mod._logs_truncated({"logs": "...[truncated]..."}) is True
    # 顶层显式 False 但 logs 有标记 → 仍截断（两路 OR 语义）。
    assert mod._logs_truncated({"output_truncated": False, "logs": "[truncated]"}) is True


def test_g5_truncation_no_false_positive():
    """G5 反证：output_truncated 缺失 + logs 无标记 → 不截断（不误报）。"""
    mod = _mod()
    assert mod._logs_truncated({"logs": "训练完成 accuracy=0.91"}) is False
    assert mod._logs_truncated({"output_truncated": 0, "logs": "ok"}) is False  # falsy 0
    assert mod._logs_truncated({}) is False


def test_g5_truncation_defensive_non_dict():
    """G5 防御：execution_result 非 dict / None → 不崩、不截断。"""
    mod = _mod()
    assert mod._logs_truncated(None) is False
    assert mod._logs_truncated("str") is False
    assert mod._logs_truncated(["list"]) is False
    assert mod._logs_truncated(123) is False


def test_g5_truncation_path1_apptest_native_warning():
    """G5（AppTest）：顶层 output_truncated=True → 原生 st.warning 渲染「日志已截断」（路径1可见）。"""
    state = _make_state(execution_result={
        "success": False, "logs": "tail-only logs", "runtime_seconds": 3.0,
        "errors": [], "artifacts": [], "metrics": {}, "output_truncated": True,
    })
    controller = _make_controller_mock(state=state, is_interrupted=False)
    at = _run(controller, _SCRIPT_HEADER)
    assert not at.exception, at.exception
    assert "日志已截断" in _collect_text(at)


def test_g5_truncation_path2_apptest_logs_marker():
    """G5（AppTest）：仅 logs 含标记（无顶层键，模拟真实 ExecutionResult）→ 仍渲染截断标注（路径2可见）。"""
    state = _make_state(execution_result={
        "success": False, "logs": "...运行输出... [truncated]", "runtime_seconds": 3.0,
        "errors": [], "artifacts": [], "metrics": {},
    })
    controller = _make_controller_mock(state=state, is_interrupted=False)
    at = _run(controller, _SCRIPT_HEADER)
    assert not at.exception, at.exception
    assert "日志已截断" in _collect_text(at)


def test_g5_execution_result_typeddict_has_no_output_truncated_field():
    """G5：核实 ExecutionResult TypedDict 确无 output_truncated（是 SandboxRunResult 的），双探测有正当性。"""
    from core.state import ExecutionResult
    fields = set(getattr(ExecutionResult, "__annotations__", {}).keys())
    assert "output_truncated" not in fields, (
        "ExecutionResult 不应有 output_truncated 字段（页面双探测依赖此前提）"
    )
    # SandboxRunResult 才有该字段。
    from sandbox.local_venv import SandboxRunResult
    sandbox_fields = {f.name for f in __import__("dataclasses").fields(SandboxRunResult)}
    assert "output_truncated" in sandbox_fields


# =========================================================================== #
# G6：_should_jump_to_report 真值表补强（report_path 空白串 / current_step 多形态）
# =========================================================================== #
def test_g6_jump_report_path_whitespace_is_truthy_string():
    """G6：report_path 为纯空白串 → bool(' ') 为 True（页面用 bool 判定，空白串视为有路径）。

    记录实际行为：页面 _should_jump_to_report 用 bool(report_path)，' ' 是 truthy。这是已知/可接受
    口径（reporting 节点不会写纯空白路径），此测试固化该行为以便回归感知任何口径变化。
    """
    mod = _mod()
    assert mod._should_jump_to_report(
        {"current_step": "reporting", "report_path": "   "}, is_interrupted=False
    ) is True


def test_g6_jump_current_step_variants():
    """G6：current_step 非 reporting 的各形态均不跳（coding/execution/cancelled/空/None）。"""
    mod = _mod()
    for step in ("coding", "execution", "cancelled_by_user", "", "start"):
        assert mod._should_jump_to_report(
            {"current_step": step, "report_path": "/tmp/r.md"}, is_interrupted=False
        ) is False, f"current_step={step!r} 不应跳转"
    # current_step 缺失 → 不跳。
    assert mod._should_jump_to_report({"report_path": "/tmp/r.md"}, is_interrupted=False) is False


def test_g6_jump_interrupted_overrides_even_when_reporting():
    """G6：is_interrupted=True 时即便 reporting + report_path 非空也不跳（决策面板优先）。"""
    mod = _mod()
    ok = {"current_step": "reporting", "report_path": "/tmp/r.md"}
    assert mod._should_jump_to_report(ok, is_interrupted=True) is False


def test_g6_jump_apptest_full_truth_path():
    """G6（AppTest）：reporting + report_path 非空 + 非 interrupt → current_page 跳 report。"""
    state = _make_state(current_step="reporting", report_path="/tmp/report.md")
    controller = _make_controller_mock(state=state, is_interrupted=False)
    with patch("app._get_controller", return_value=controller):
        at = AppTest.from_string(_SCRIPT_HEADER)
        at.run()
    assert at.session_state["current_page"] == STREAMLIT_PAGE_REPORT


# =========================================================================== #
# G7：execution_errors 键名核实（payload 优先读 execution_errors，兜底 execution_result.errors）
# =========================================================================== #
def test_g7_node_payload_uses_execution_errors_key():
    """G7：核实 execution 节点 interrupt#2 payload 失败清单键名实为 execution_errors（非 execution_result.errors）。"""
    payload = exec_node._build_dev_loop_interrupt_payload(
        {"errors": ["[error_category=runtime] boom1", "boom2"]},
        exec_node.ExecutionFeedback(exec_node.ErrorCategory.RUNTIME, True, "s", "h", "e"),
        {"fix_loop_count": 2, "fix_loop_history": []},
    )
    assert "execution_errors" in payload
    assert payload["execution_errors"] == ["[error_category=runtime] boom1", "boom2"]
    # 节点 payload 顶层无 execution_result.errors 嵌套结构（页面优先读 execution_errors 正确）。
    assert "execution_result" not in payload


def test_g7_panel_reads_execution_errors_primary():
    """G7（AppTest）：payload 带 execution_errors → 决策面板渲染这些错误条目。"""
    controller = _make_controller_mock(
        state=_make_state(current_step="execution"),
        is_interrupted=True,
        interrupt_kind="dev_loop_failure",
        interrupt_payload={
            "interrupt_kind": "dev_loop_failure", "fix_loop_count": 3,
            "error_category": "runtime", "error_summary": "运行时异常",
            "execution_errors": ["[error_category=runtime] 第一条执行错误", "第二条执行错误"],
            "fix_loop_history": [], "options": ["terminate", "revise_plan", "export_code"],
        },
    )
    at = _run(controller, _SCRIPT_HEADER)
    assert not at.exception, at.exception
    text = _collect_text(at)
    assert "第一条执行错误" in text
    assert "第二条执行错误" in text


def test_g7_panel_fallback_to_execution_result_errors():
    """G7（AppTest）：payload 无 execution_errors 但有 execution_result.errors → 兜底读取渲染（两路都不崩）。"""
    controller = _make_controller_mock(
        state=_make_state(current_step="execution"),
        is_interrupted=True,
        interrupt_kind="dev_loop_failure",
        interrupt_payload={
            "interrupt_kind": "dev_loop_failure", "fix_loop_count": 3,
            "error_category": "runtime", "error_summary": "运行时异常",
            # 无 execution_errors 键，改用任务描述措辞的 execution_result.errors 兜底路径。
            "execution_result": {"errors": ["兜底来源的执行错误条目"]},
            "fix_loop_history": [], "options": ["terminate", "revise_plan", "export_code"],
        },
    )
    at = _run(controller, _SCRIPT_HEADER)
    assert not at.exception, at.exception
    text = _collect_text(at)
    assert "兜底来源的执行错误条目" in text


def test_g7_panel_no_errors_keys_does_not_crash():
    """G7（AppTest）：payload 既无 execution_errors 也无 execution_result → 决策面板仍渲染三按钮，不崩。"""
    controller = _make_controller_mock(
        state=_make_state(current_step="execution"),
        is_interrupted=True,
        interrupt_kind="dev_loop_failure",
        interrupt_payload={
            "interrupt_kind": "dev_loop_failure", "fix_loop_count": 3,
            "error_category": "timeout", "error_summary": "超时",
            "options": ["terminate", "revise_plan", "export_code"],
        },
    )
    at = _run(controller, _SCRIPT_HEADER)
    assert not at.exception, at.exception
    assert {"btn_dev_loop_terminate", "btn_dev_loop_revise_plan",
            "btn_dev_loop_export_code"} <= {b.key for b in at.button}


def test_g7_panel_empty_payload_does_not_crash():
    """G7（AppTest）：dev_loop_failure 但 payload 为 None（异常态）→ 面板降级渲染三按钮，不崩。"""
    controller = _make_controller_mock(
        state=_make_state(current_step="execution"),
        is_interrupted=True,
        interrupt_kind="dev_loop_failure",
        interrupt_payload=None,  # payload or {} 兜底
    )
    at = _run(controller, _SCRIPT_HEADER)
    assert not at.exception, at.exception
    assert {"btn_dev_loop_terminate", "btn_dev_loop_revise_plan",
            "btn_dev_loop_export_code"} <= {b.key for b in at.button}


# =========================================================================== #
# G8：shadcn iframe → 核心终态/降级/截断文案改原生 st.error/warning/info（AppTest 可见）
# =========================================================================== #
def test_g8_worker_error_uses_native_error():
    """G8：worker_error 终态用原生 st.error（AppTest at.error 可见），非 shadcn iframe。"""
    controller = _make_controller_mock(worker_error=RuntimeError("worker boom"))
    at = _run(controller, _SCRIPT_HEADER)
    assert not at.exception
    err_texts = "\n".join(str(getattr(e, "value", "")) for e in at.error)
    assert "工作线程异常" in err_texts


def test_g8_state_error_uses_native_error():
    """G8：state.error 终态用原生 st.error（AppTest at.error 可见）。"""
    state = _make_state(error="rate limit exceeded")
    controller = _make_controller_mock(state=state, is_interrupted=False)
    at = _run(controller, _SCRIPT_HEADER)
    err_texts = "\n".join(str(getattr(e, "value", "")) for e in at.error)
    assert "致命错误" in err_texts


def test_g8_cancelled_uses_native_warning():
    """G8：cancelled_by_user 终态用原生 st.warning（AppTest at.warning 可见）。"""
    state = _make_state(current_step="cancelled_by_user")
    controller = _make_controller_mock(state=state, is_interrupted=False)
    at = _run(controller, _SCRIPT_HEADER)
    warn_texts = "\n".join(str(getattr(w, "value", "")) for w in at.warning)
    assert "任务已终止" in warn_texts


def test_g8_degraded_uses_native_warning():
    """G8：degraded_nodes 用原生 st.warning（AppTest at.warning 可见，含降级节点名）。"""
    state = _make_state(current_step="execution", degraded_nodes=["execution"])
    controller = _make_controller_mock(state=state, is_interrupted=False)
    at = _run(controller, _SCRIPT_HEADER)
    warn_texts = "\n".join(str(getattr(w, "value", "")) for w in at.warning)
    assert "降级节点" in warn_texts
    assert "execution" in warn_texts


def test_g8_truncation_uses_native_warning():
    """G8：日志截断标注用原生 st.warning（AppTest at.warning 可见）。"""
    state = _make_state(execution_result={
        "success": False, "logs": "tail", "runtime_seconds": 1.0,
        "errors": [], "artifacts": [], "metrics": {}, "output_truncated": True,
    })
    controller = _make_controller_mock(state=state, is_interrupted=False)
    at = _run(controller, _SCRIPT_HEADER)
    warn_texts = "\n".join(str(getattr(w, "value", "")) for w in at.warning)
    assert "日志已截断" in warn_texts


def test_g8_decision_panel_uses_native_error():
    """G8：决策面板顶部用原生 st.error（AppTest at.error 可见，承载 interrupt#2 提示）。"""
    controller = _make_controller_mock(
        state=_make_state(current_step="execution"),
        is_interrupted=True,
        interrupt_kind="dev_loop_failure",
        interrupt_payload={
            "interrupt_kind": "dev_loop_failure", "fix_loop_count": 3,
            "error_category": "runtime", "error_summary": "运行时异常",
            "execution_errors": ["x"], "fix_loop_history": [],
            "options": ["terminate", "revise_plan", "export_code"],
        },
    )
    at = _run(controller, _SCRIPT_HEADER)
    err_texts = "\n".join(str(getattr(e, "value", "")) for e in at.error)
    assert "自动修复未通过" in err_texts


# =========================================================================== #
# G9：决策按钮端到端 AppTest 点击 → resume_with 实参（与 G1 端到端互补，验 UI 写路径）
# =========================================================================== #
def _interrupted_controller(thread_id_in_script: str, feedback_prefill: str = "") -> MagicMock:
    return _make_controller_mock(
        state=_make_state(current_step="execution"),
        is_interrupted=True,
        interrupt_kind="dev_loop_failure",
        interrupt_payload={
            "interrupt_kind": "dev_loop_failure", "fix_loop_count": 3,
            "error_category": "runtime", "error_summary": "运行时异常",
            "execution_errors": ["[error_category=runtime] boom"],
            "fix_loop_history": _make_fix_history(3),
            "options": ["terminate", "revise_plan", "export_code"],
        },
    )


def test_g9_revise_plan_empty_feedback_click_injects_empty_string():
    """G9（AppTest）：不填修订意见直接点「提交改计划」→ resume_with(tid, {decision, user_feedback:''})。"""
    script = _script_with_thread("task-e2r-rev-empty")
    controller = _interrupted_controller("task-e2r-rev-empty")
    with patch("app._get_controller", return_value=controller):
        at = AppTest.from_string(script)
        at.run()
        assert not at.exception, at.exception
        btns = [b for b in at.button if b.key == "btn_dev_loop_revise_plan"]
        assert len(btns) == 1
        btns[0].click().run()
    controller.resume_with.assert_called_once_with(
        "task-e2r-rev-empty", {"decision": "revise_plan", "user_feedback": ""}
    )


def test_g9_terminate_click_does_not_carry_user_feedback():
    """G9（AppTest）：点终止 → payload 恰为 {"decision":"terminate"}（不夹带 user_feedback 键）。"""
    script = _script_with_thread("task-e2r-term")
    controller = _interrupted_controller("task-e2r-term")
    with patch("app._get_controller", return_value=controller):
        at = AppTest.from_string(script)
        at.run()
        btns = [b for b in at.button if b.key == "btn_dev_loop_terminate"]
        btns[0].click().run()
    # 精确实参（无 user_feedback 键）。
    args, _kwargs = controller.resume_with.call_args
    assert args[1] == {"decision": "terminate"}
    assert "user_feedback" not in args[1]


# =========================================================================== #
# G10：决策面板渲染失败上下文摘要字段（error_category / error_summary / fix_hint / stderr）
# =========================================================================== #
def test_g10_panel_renders_context_summary_fields():
    """G10（AppTest）：决策面板渲染 error_category / error_summary / fix_hint / representative_stderr。"""
    controller = _make_controller_mock(
        state=_make_state(current_step="execution"),
        is_interrupted=True,
        interrupt_kind="dev_loop_failure",
        interrupt_payload={
            "interrupt_kind": "dev_loop_failure", "fix_loop_count": 3,
            "error_category": "hardware", "error_summary": "CUDA 显存不足",
            "fix_hint": "降低 batch size 或换更小模型",
            "representative_stderr": "RuntimeError: CUDA out of memory. Tried to allocate ...",
            "execution_errors": ["[error_category=hardware] CUDA 显存不足"],
            "fix_loop_history": _make_fix_history(3),
            "options": ["terminate", "revise_plan", "export_code"],
        },
    )
    at = _run(controller, _SCRIPT_HEADER)
    assert not at.exception, at.exception
    text = _collect_text(at)
    assert "hardware" in text             # error_category
    assert "CUDA 显存不足" in text         # error_summary
    assert "降低 batch size" in text       # fix_hint
    assert "CUDA out of memory" in text    # representative_stderr (st.code)
    # 已尝试修复回合数也展示。
    assert "已尝试修复回合数" in text


def test_g10_panel_fix_count_progress_text_in_panel():
    """G10（AppTest）：决策面板内复用 _fix_loop_progress_text（修复第 N/3 轮文案）。"""
    controller = _make_controller_mock(
        state=_make_state(current_step="execution"),
        is_interrupted=True,
        interrupt_kind="dev_loop_failure",
        interrupt_payload={
            "interrupt_kind": "dev_loop_failure", "fix_loop_count": MAX_FIX_LOOP_COUNT,
            "error_category": "runtime", "error_summary": "运行时异常",
            "execution_errors": ["x"], "fix_loop_history": _make_fix_history(MAX_FIX_LOOP_COUNT),
            "options": ["terminate", "revise_plan", "export_code"],
        },
    )
    at = _run(controller, _SCRIPT_HEADER)
    text = _collect_text(at)
    assert f"修复第 {MAX_FIX_LOOP_COUNT} / {MAX_FIX_LOOP_COUNT} 轮" in text
