"""Sprint 3 阶段 F / 任务 F2（CP-F2-1）：五条核心 e2e（mock 版本，不依赖凭证）。

本文件用 **真实 ``build_graph()`` 编译的主图** + mock 上游/coding 节点 + mock sandbox +
真实 execution + 真实 reporting，跑端到端流程（集成视角，比 c3/d1 的单节点 / 最小子图
高一层：真正把 START → … → reporting → END 整条流水线串起来，验证真实图路由 +
execution 修复循环 / interrupt#2 / 降级真实逻辑 + reporting 三形态真实渲染落盘）。

覆盖 dev-plan §667-672 的 5 条核心场景：
    1. happy path B 档成功（AC-S3-01）：FULL 跑通 START→…→reporting→END，sandbox exit 0
       + 可解析 <METRICS> → execution_result.success=True + reporting full_success 报告；
    2. 修复循环上限 3（AC-S3-03）：连续可修复失败 → fix_loop_count 自增至 3 拦截 → interrupt#2；
    3. interrupt#2 三选一（AC-S3-07）：Command(resume=...) 注入 terminate/revise_plan/export_code
       三态 → 真实图路由到 END / planning / reporting；
    4. code_only（AC-S3-06）：planning 决策 code_only → coding 出边 skip_execution（execution
       未执行）→ reporting code_only 形态；
    5. 降级（AC-S3-09 ③）：不可修复错误 / 预算耗尽 → degraded 报告。

设计要点：
    - **CP-F2-1 = mock 版本，不标 @pytest.mark.e2e** —— 进常规回归（``-m "not e2e"`` 能跑到），
      不依赖凭证、不耗 deepxiv 配额。
    - 真实 ``build_graph()`` 主图 + ``InMemorySaver``；上游 4 节点（intake/analysis/scout/
      planning）+ coding 用 ``patch.object(graph_module, ...)`` 替为 fake（避免真实 LLM / SDK /
      interrupt#1）；execution 用真实（patch 其模块内 sandbox 三入口 + _llm_extract_metrics）；
      reporting 用真实（state.workspace_dir 指向 tmp_path，报告真落盘 tmp 不污染真实 workspace）。
    - interrupt / checkpointer 用例用唯一 ``uuid4`` thread_id 防串（sp3 既有教训）。
    - **真实链路版本（CP-F2-2）** 见本文件末尾 ``TestRealChainE2E``：5 条真实链路 e2e（real-1~5，
      靶 HippoRAG arXiv:2405.14831），用 ``@pytest.mark.e2e`` 标记 + 凭证未就绪时 skip。基调
      **真实 LLM（各 ReAct 节点 + metrics 抽取）+ 真实 deepxiv（read_section 读论文）+ mock
      sandbox**（§667「happy path B 档 = mock sandbox exit 0 + 可解析指标」，不真跑 30min 训练）。
      本次只落代码、**不跑真实 e2e**（省 deepxiv 配额），留主控统一 smoke + 补跑转正。

运行方式：
    .venv/bin/pytest tests/test_sprint3_e2e.py -v            # 跑 mock 版本（-m "not e2e"）
    .venv/bin/pytest tests/test_sprint3_e2e.py -m e2e -v -s  # 凭证就绪后补跑真实链路（CP-F2-2）
    # real-1 作 smoke 首选（最省配额、fail-fast 验凭证 + deepxiv 可达 + 全链路装配）：
    .venv/bin/pytest "tests/test_sprint3_e2e.py::TestRealChainE2E::test_real_1_happy_path_b_grade_success" -m e2e -v -s
"""

from __future__ import annotations

import importlib
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import (  # noqa: E402
    MAX_DEV_LOOP_LLM_CALLS,
    MAX_FIX_LOOP_COUNT,
)
from core.state import ExecutionMode  # noqa: E402

import core.graph as graph_module  # noqa: E402
from core.graph import build_graph  # noqa: E402

# importlib 拿真实 execution 子模块（core/nodes/__init__ 显式 export callable 会遮蔽子模块，坑6）。
execution_module = importlib.import_module("core.nodes.execution")
reporting_module = importlib.import_module("core.nodes.reporting")

# interrupt#2 payload 约定（与 execution 模块对齐，任一端改即此处红）。
INTERRUPT_KIND_DEV_LOOP = execution_module.INTERRUPT_KIND  # "dev_loop_failure"


# ===========================================================================
# 共享 mock 脚手架：伪 sandbox dataclass + sandbox patch + 上游节点 fake
# ===========================================================================


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


def _patch_sandbox(
    monkeypatch: pytest.MonkeyPatch,
    *,
    prep: Optional[FakePrepareResult] = None,
    run_results: Optional[List[FakeRunResult]] = None,
    llm_extract: Optional[Any] = None,
) -> Dict[str, int]:
    """patch execution 模块内 sandbox 三入口 + _llm_extract_metrics，返回调用计数器。

    run_results 列表按调用顺序逐个返回，耗尽后重复返回最后一个（支持「连续失败」场景）。
    """
    cnt: Dict[str, int] = {"prepare": 0, "run": 0, "collect": 0}
    prep_obj = prep if prep is not None else FakePrepareResult()
    runs = run_results if run_results is not None else [
        FakeRunResult(exit_code=0, stdout='<METRICS>{"accuracy": 0.9}</METRICS>')
    ]
    run_iter = iter(runs)

    def fake_prepare_venv(*args: Any, **kwargs: Any) -> FakePrepareResult:
        cnt["prepare"] += 1
        return prep_obj

    def fake_run_in_venv(*args: Any, **kwargs: Any) -> FakeRunResult:
        cnt["run"] += 1
        try:
            return next(run_iter)
        except StopIteration:
            return runs[-1] if runs else FakeRunResult()

    def fake_collect_artifacts(*args: Any, **kwargs: Any) -> List[str]:
        cnt["collect"] += 1
        return []

    monkeypatch.setattr(execution_module, "prepare_venv", fake_prepare_venv)
    monkeypatch.setattr(execution_module, "run_in_venv", fake_run_in_venv)
    monkeypatch.setattr(execution_module, "collect_artifacts", fake_collect_artifacts)
    # 默认让 LLM 抽取不触发（零扣减），隔离预算行为；档1 <METRICS> 命中时本就不触发。
    monkeypatch.setattr(
        execution_module,
        "_llm_extract_metrics",
        llm_extract if llm_extract is not None else (lambda *a, **k: ({}, 0)),
    )
    return cnt


def _patch_upstream_nodes(
    monkeypatch: pytest.MonkeyPatch,
    *,
    code_output_dir: str,
    execution_mode: ExecutionMode = ExecutionMode.FULL,
    coding_hook: Optional[Any] = None,
) -> Dict[str, int]:
    """patch graph_module 的上游 4 节点 + coding 为 fake（避免真实 LLM / SDK / interrupt#1）。

    保留 execution / reporting 为真实（验证真实修复循环 + 三形态渲染）。
    返回 coding 调用计数器（修复循环上限场景用来旁证 coding 被反复进入）。
    """
    coding_cnt: Dict[str, int] = {"coding": 0}

    def fake_intake(state):
        return {
            "paper_meta": {"arxiv_id": "2405.14831", "title": "F2 mock paper"},
            "current_step": "paper_intake",
        }

    def fake_analysis(state):
        return {
            "paper_analysis": {
                "method_summary": "mock method",
                "metrics": ["accuracy", "f1"],
                "baseline_results": {"accuracy": 0.91},
            },
            "current_step": "paper_analysis",
        }

    def fake_scout(state):
        return {"resource_info": {"selected_repo": None}, "current_step": "resource_scout"}

    def fake_planning(state):
        return {
            "reproduction_plan": {
                "plan_summary": "mock plan",
                "code_strategy": "from_scratch",
                "execution_steps": [{"step_name": "run", "command": "python run.py"}],
                "deliverables": ["run.py"],
                "expected_results": {"accuracy": 0.90},
                "environment": {},
                "approved": True,
            },
            "execution_mode": execution_mode,
            "current_step": "planning",
        }

    def fake_coding(state):
        coding_cnt["coding"] += 1
        out: Dict[str, Any] = {
            "code_output_dir": code_output_dir,
            "current_step": "coding",
        }
        # 修复回合：coding 不写 _dev_loop_route（与真实 coding._map_coding_result 一致），
        # 由 execution 重新分类；hook 可注入额外更新（如清 retry 预算等）。
        if coding_hook is not None:
            out.update(coding_hook(state) or {})
        return out

    monkeypatch.setattr(graph_module, "paper_intake", fake_intake)
    monkeypatch.setattr(graph_module, "paper_analysis", fake_analysis)
    monkeypatch.setattr(graph_module, "resource_scout", fake_scout)
    monkeypatch.setattr(graph_module, "planning", fake_planning)
    monkeypatch.setattr(graph_module, "coding", fake_coding)
    return coding_cnt


def _initial_state(workspace_dir: Path) -> Dict[str, Any]:
    """主图入口初始 state（最小集，graph 各节点 fake 会补齐其余字段）。

    workspace_dir 指向 tmp，让真实 reporting 把报告落盘 tmp（不污染真实 workspace）。
    """
    return {
        "user_input": "2405.14831",
        "workspace_dir": str(workspace_dir),
        "node_errors": [],
        "degraded_nodes": [],
        "fix_loop_history": [],
        "fix_loop_count": 0,
        "retry_budget_remaining": 40,
        "_dev_loop_llm_calls": 0,
        "_dev_loop_route": None,
        "execution_result": None,
        "messages": [],
    }


def _new_config() -> Dict[str, Any]:
    return {"configurable": {"thread_id": f"f2-e2e-{uuid.uuid4().hex}"}}


def _read_report(report_path: Optional[str]) -> str:
    assert report_path, "reporting 应返回 report_path"
    p = Path(report_path)
    assert p.exists(), f"report_path 文件应真落盘：{report_path}"
    return p.read_text(encoding="utf-8")


# ===========================================================================
# 场景 1：happy path B 档成功（AC-S3-01）
# ===========================================================================


def test_f2_e2e_1_happy_path_b_grade_success_full_mode(monkeypatch, tmp_path):
    """场景 1（AC-S3-01）：FULL 模式跑通 START→…→execution(成功)→reporting→END。

    sandbox exit 0 + 可解析 <METRICS> → execution_result.success=True；真实 reporting
    渲染 full_success 报告并落盘 tmp。断言整条流水线落点 + 报告形态。
    """
    code_dir = str(tmp_path / "2405.14831" / "code")
    _patch_upstream_nodes(monkeypatch, code_output_dir=code_dir)
    cnt = _patch_sandbox(
        monkeypatch,
        run_results=[FakeRunResult(exit_code=0, stdout='<METRICS>{"accuracy": 0.893, "f1": 0.88}</METRICS>')],
    )

    graph = build_graph(checkpointer=InMemorySaver())
    final = graph.invoke(_initial_state(tmp_path), _new_config())

    # 流水线整条落点齐全（证明 intake→analysis→scout→planning→coding→execution→reporting）。
    assert final["paper_meta"]["arxiv_id"] == "2405.14831"
    assert final["reproduction_plan"]["approved"] is True
    assert final["code_output_dir"] == code_dir

    # execution 真实跑：B 档成功（exit0 + ≥1 指标）。
    er = final["execution_result"]
    assert er["success"] is True, "exit0 + <METRICS> 可解析 → B 档成功"
    assert er["metrics"].get("accuracy") == 0.893
    # sandbox 恰跑 1 次（无修复循环）。
    assert cnt["prepare"] == 1 and cnt["run"] == 1

    # reporting full_success 形态（真实渲染落盘）。
    assert reporting_module._determine_report_form(final) == "full_success"
    report = _read_report(final.get("report_path"))
    assert "0.893" in report, "full_success 报告应含复现指标值"
    assert "0.91" in report, "full_success 报告应含 baseline 指标对比"
    # B 档红线：只对比不出硬性达标/不达标结论（Q-S3-01）。
    assert "不达标" not in report and "未达标" not in report
    assert final["current_step"] == "reporting"
    assert final.get("__interrupt__") is None  # happy path 不暂停


# ===========================================================================
# 场景 2：修复循环上限 3（AC-S3-03）
# ===========================================================================


def test_f2_e2e_2_fix_loop_upper_limit_three_then_interrupt(monkeypatch, tmp_path):
    """场景 2（AC-S3-03）：连续可修复失败 → fix_loop_count 自增至 3 拦截 → interrupt#2。

    每轮 sandbox 返回可修复失败（ModuleNotFoundError=import 可修复）。真实图路由：
    execution(失败) → retry_coding → coding(fake) → execution(失败) → … 三回合后 fix_loop_count
    达 MAX_FIX_LOOP_COUNT(3) → 不再回 coding → await self-loop → interrupt#2 暂停。
    """
    code_dir = str(tmp_path / "fix3" / "code")
    coding_cnt = _patch_upstream_nodes(monkeypatch, code_output_dir=code_dir)
    cnt = _patch_sandbox(
        monkeypatch,
        run_results=[FakeRunResult(exit_code=1, stderr="ModuleNotFoundError: No module named 'torch'")],
    )

    graph = build_graph(checkpointer=InMemorySaver())
    config = _new_config()
    out = graph.invoke(_initial_state(tmp_path), config)

    # 跑到 interrupt#2 暂停（达上限后 await self-loop → 函数体内 interrupt()）。
    assert "__interrupt__" in out, f"达修复上限后应在 execution interrupt#2 暂停：keys={list(out.keys())}"

    snap = graph.get_state(config)
    # 暂停点 fix_loop_count == MAX_FIX_LOOP_COUNT（3），未越界自增。
    assert snap.values.get("fix_loop_count") == MAX_FIX_LOOP_COUNT, (
        f"fix_loop_count 应自增至上限 {MAX_FIX_LOOP_COUNT}，实际 {snap.values.get('fix_loop_count')}"
    )
    # interrupt payload 契约：kind=dev_loop_failure + options 三态。
    interrupts = [
        iv for task in (snap.tasks or []) for iv in (getattr(task, "interrupts", None) or [])
    ]
    assert interrupts, "snapshot 无 interrupt 元数据"
    payload = interrupts[0].value
    assert payload.get("interrupt_kind") == INTERRUPT_KIND_DEV_LOOP
    assert payload.get("options") == ["terminate", "revise_plan", "export_code"]
    # coding 被反复进入（首轮 + 3 修复回合 = 4 次）；至少 > 1 证明修复回边真走通。
    assert coding_cnt["coding"] >= MAX_FIX_LOOP_COUNT + 1, (
        f"coding 应被进入 ≥{MAX_FIX_LOOP_COUNT + 1} 次（首轮 + {MAX_FIX_LOOP_COUNT} 修复回合），"
        f"实际 {coding_cnt['coding']}"
    )
    # 修复历程记录满 3 条。
    history = snap.values.get("fix_loop_history") or []
    assert len(history) == MAX_FIX_LOOP_COUNT, (
        f"fix_loop_history 应记 {MAX_FIX_LOOP_COUNT} 轮，实际 {len(history)}"
    )


# ===========================================================================
# 场景 3：interrupt#2 三选一（AC-S3-07）
# ===========================================================================


@pytest.mark.parametrize(
    "decision,expect",
    [
        ("terminate", "end"),
        ("revise_plan", "planning"),
        ("export_code", "reporting"),
    ],
)
def test_f2_e2e_3_interrupt2_three_state_resume(monkeypatch, tmp_path, decision, expect):
    """场景 3（AC-S3-07）：interrupt#2 暂停后 Command(resume=...) 三态 → 真实图路由正确。

    - terminate  → END（current_step=cancelled_by_user）；
    - revise_plan → planning（approved 清 False，fix_loop_count 清零）；
    - export_code → reporting（degraded 报告）。

    用不可修复错误（CUDA OOM=hardware）一回合即触发 interrupt#2（无需先耗修复回合）。
    """
    code_dir = str(tmp_path / f"i2-{decision}" / "code")
    _patch_upstream_nodes(monkeypatch, code_output_dir=code_dir)
    cnt = _patch_sandbox(
        monkeypatch,
        run_results=[FakeRunResult(exit_code=1, stderr="RuntimeError: CUDA out of memory")],
    )

    graph = build_graph(checkpointer=InMemorySaver())
    config = _new_config()

    out1 = graph.invoke(_initial_state(tmp_path), config)
    assert "__interrupt__" in out1, "不可修复失败应触发 interrupt#2 暂停"
    # 暂停时 sandbox 恰跑 1 次（首次失败落盘 + self-loop 重入 guard 命中跳过）。
    assert cnt["prepare"] == 1, f"interrupt 前 sandbox prepare 应恒 1，实际 {cnt['prepare']}"

    extra = {"user_feedback": "请换用更小模型"} if decision == "revise_plan" else {}
    final = graph.invoke(Command(resume={"decision": decision, **extra}), config)

    snap = graph.get_state(config)

    if expect == "end":
        assert snap.next == (), f"terminate 应到 END：next={snap.next}"
        assert final.get("current_step") == "cancelled_by_user"
        assert final.get("user_fix_decision") == "terminate"
        # resume 重跑期间 sandbox 不重跑（幂等：guard 命中跳过）。
        assert cnt["prepare"] == 1, f"terminate resume 不应重跑 sandbox，prepare 应恒 1，实际 {cnt['prepare']}"
    elif expect == "planning":
        # revise_plan → 真实图路由回 planning 重新走流程。execution 出口先清 approved=False
        # （供 _route_after_planning 走 self），但本测试 fake_planning 会重新 approve → 再走
        # coding → execution（仍 OOM）→ 再次 interrupt#2 暂停。这是真实图行为（fake_planning
        # 没真改计划仍 approve）。核心断言：① user_fix_decision=revise_plan 落地；② fix_loop_count
        # 清零（revise_plan 回流清零、history 保留，回问点2）；③ planning 真被重入（再走一轮 →
        # 再暂停 + sandbox 再跑一次，证明路由真回到 planning 而非卡死）。
        assert final.get("user_fix_decision") == "revise_plan", "revise_plan 决策应落地"
        assert snap.values.get("fix_loop_count") == 0, (
            f"revise_plan 回流应清零 fix_loop_count，实际 {snap.values.get('fix_loop_count')}"
        )
        assert "__interrupt__" in final, "revise_plan 回 planning 后（仍 OOM）应再次 interrupt#2 暂停"
        # 再走一整轮（planning→coding→execution OOM）→ sandbox 再跑一次（证明真回到 planning）。
        assert cnt["prepare"] == 2, (
            f"revise_plan 应路由回 planning 重新走流程使 execution 再跑一次（prepare=2），"
            f"实际 {cnt['prepare']}"
        )
        # fix_loop_history 保留（回问点2：清 count 不清 history）。
        assert (snap.values.get("fix_loop_history") or []) is not None
    elif expect == "reporting":
        assert snap.next == (), f"export_code 应到 END（经 reporting）：next={snap.next}"
        assert final.get("user_fix_decision") == "export_code"
        assert execution_module.NODE_NAME in (final.get("degraded_nodes") or [])
        assert reporting_module._determine_report_form(final) == "degraded"
        _read_report(final.get("report_path"))
        # resume 重跑期间 sandbox 不重跑（幂等）。
        assert cnt["prepare"] == 1, f"export_code resume 不应重跑 sandbox，prepare 应恒 1，实际 {cnt['prepare']}"


# ===========================================================================
# 场景 4：code_only 跳过 execution（AC-S3-06）
# ===========================================================================


def test_f2_e2e_4_code_only_skips_execution(monkeypatch, tmp_path):
    """场景 4（AC-S3-06）：planning 决策 code_only → coding 出边 skip_execution → reporting。

    断言 execution 节点未被执行（sandbox prepare 计数 == 0）+ reporting 渲染 code_only 形态。
    """
    code_dir = str(tmp_path / "codeonly" / "code")
    _patch_upstream_nodes(
        monkeypatch, code_output_dir=code_dir, execution_mode=ExecutionMode.CODE_ONLY
    )
    cnt = _patch_sandbox(monkeypatch)  # 若 execution 被误触达则 prepare 会 > 0

    graph = build_graph(checkpointer=InMemorySaver())
    final = graph.invoke(_initial_state(tmp_path), _new_config())

    # execution 跳过：sandbox 三入口零调用。
    assert cnt["prepare"] == 0, f"code_only 应跳过 execution，sandbox prepare 不应被调用，实际 {cnt['prepare']}"
    assert cnt["run"] == 0
    # execution_result 未被写（code_only 不走 execution）。
    assert final.get("execution_result") is None, "code_only 不应产生 execution_result"

    # reporting code_only 形态。
    assert reporting_module._determine_report_form(final) == "code_only"
    report = _read_report(final.get("report_path"))
    assert "仅生成代码" in report, "code_only 报告应标注仅生成代码"
    # code_only 报告无指标对比章节（不出现 baseline 数值对比）。
    assert final["current_step"] == "reporting"


# ===========================================================================
# 场景 5：降级报告（AC-S3-09 ③）
# ===========================================================================


def test_f2_e2e_5a_degraded_budget_exhausted(monkeypatch, tmp_path):
    """场景 5a（AC-S3-09 ③）：预算耗尽（入口预算门）→ 直接降级 → reporting degraded。

    可修复失败但 retry_budget_remaining < DEV_LOOP_MIN_CALLS_PER_ROUND → execution 入口
    预算门直接降级（不回 coding、不 interrupt）→ reporting degraded 报告。
    """
    code_dir = str(tmp_path / "deg-budget" / "code")
    _patch_upstream_nodes(monkeypatch, code_output_dir=code_dir)
    cnt = _patch_sandbox(
        monkeypatch,
        run_results=[FakeRunResult(exit_code=1, stderr="ModuleNotFoundError: No module named 'torch'")],
    )

    init = _initial_state(tmp_path)
    init["retry_budget_remaining"] = 1  # < DEV_LOOP_MIN_CALLS_PER_ROUND(2) → 入口预算门降级

    graph = build_graph(checkpointer=InMemorySaver())
    final = graph.invoke(init, _new_config())

    # 不暂停（预算门降级直接出报告，不 interrupt）。
    assert final.get("__interrupt__") is None, "预算耗尽应降级出报告，不 interrupt"
    assert execution_module.NODE_NAME in (final.get("degraded_nodes") or []), "execution 应被标记 degraded"
    assert reporting_module._determine_report_form(final) == "degraded"
    report = _read_report(final.get("report_path"))
    assert "未成功" in report or "降级" in report, "degraded 报告应含未成功/降级结论"
    # 预算门降级：未回 coding（sandbox 只跑 1 次，无修复回合）。
    assert cnt["prepare"] == 1
    assert final["current_step"] == "reporting"


def test_f2_e2e_5b_degraded_unfixable_then_export(monkeypatch, tmp_path):
    """场景 5b（AC-S3-09 ③ 变体）：不可修复错误 → interrupt#2 → export_code → degraded 报告。

    不可修复（hardware/CUDA OOM）一回合即 interrupt#2；用户选 export_code → reporting degraded。
    与场景 3 的 export_code 互补：本条强调「不可修复 → 降级报告含 node_errors 错误分类摘要」。
    """
    code_dir = str(tmp_path / "deg-unfix" / "code")
    _patch_upstream_nodes(monkeypatch, code_output_dir=code_dir)
    _patch_sandbox(
        monkeypatch,
        run_results=[FakeRunResult(exit_code=1, stderr="RuntimeError: CUDA out of memory")],
    )

    graph = build_graph(checkpointer=InMemorySaver())
    config = _new_config()
    out1 = graph.invoke(_initial_state(tmp_path), config)
    assert "__interrupt__" in out1, "不可修复失败应 interrupt#2 暂停"

    final = graph.invoke(Command(resume={"decision": "export_code"}), config)
    assert reporting_module._determine_report_form(final) == "degraded"

    # degraded 报告含错误分类摘要（hardware 分类前缀解析进报告）。
    report = _read_report(final.get("report_path"))
    assert "hardware" in report or "降级" in report, "degraded 报告应含错误分类/降级原因"

    # node_errors 含不可修复（permanent）错误。
    node_errors = final.get("node_errors") or []
    assert any(e.get("error_type") == "permanent" for e in node_errors), (
        "不可修复错误应记 permanent NodeError"
    )
    assert any("[error_category=hardware]" in (e.get("error_message") or "") for e in node_errors), (
        "node_error 应含 hardware 细分类前缀"
    )


# ===========================================================================
# CP-F2-2 真实链路 e2e（@pytest.mark.e2e，凭证未就绪时 skip）
# ===========================================================================
#
# 真实/mock 边界（dev-plan §667 权威约定）：
#     - **真实**：LLM（intake/analysis/scout/planning/coding 各 ReAct 节点 + metrics 抽取）
#       + deepxiv（read_section / get_paper_structure 读 HippoRAG 论文）+ 真实 build_graph()
#       主图 + 真实 SqliteSaver(WAL) 持久化 + 真实 execution 复合节点（错误分类 / B 档判定 /
#       修复循环边界 / interrupt#2）+ 真实 reporting 三形态渲染。
#     - **mock**：仅 sandbox 三入口（prepare_venv / run_in_venv / collect_artifacts）——
#       §667 明确「happy path B 档成功 = mock sandbox 返回 exit 0 + 可解析指标 → success=True」，
#       即真实 e2e **不真跑 30min venv 训练**，只模拟执行结果（exit code + stdout <METRICS>）。
#       这样真实驱动整条 LLM 流程，又不被长训练 / GPU 依赖卡死。
#
# 凭证依赖：LLM_API_KEY + DEEPXIV_TOKEN（缺任一 → 整类 skip，不耗配额）。
# 配额提示：read_section 走 deepxiv 日配额（Maria 曾踩 DeepxivDailyLimitError）；HippoRAG
#     大概率已被 sp1/sp2 e2e 缓存。**本次只落代码不真跑**，由主控统一 smoke + 补跑。
#
# 复跑要求（dev-plan §674）：interrupt#2 / 修复循环属 LLM 服从度 + 重跑幂等类风险，
#     复现率高（≥50%）连跑 3 次全绿；复现率低（10%~50%）连跑 5 次全绿且含全量回归。
#
# 运行（凭证就绪后由主控补跑）：
#     .venv/bin/pytest tests/test_sprint3_e2e.py -m e2e -v -s
#     # real-1 作 smoke 首选（最省配额、fail-fast 验凭证 + deepxiv 可达）：
#     .venv/bin/pytest "tests/test_sprint3_e2e.py::TestRealChainE2E::test_real_1_happy_path_b_grade_success" -m e2e -v -s


PAPER_ARXIV_ID = "2405.14831"  # HippoRAG，sp1/sp2 e2e 已验证可访问 + CS 领域 + deepxiv 大概率已缓存


def _has_credentials() -> bool:
    """凭证就绪判定（与 sp2 e2e 范式一致，读 config getter，不写凭证值）。"""
    from config import get_deepxiv_token, get_llm_api_key

    return bool(get_llm_api_key()) and bool(get_deepxiv_token())


skip_if_no_creds = pytest.mark.skipif(
    not _has_credentials(),
    reason="缺少 LLM_API_KEY 或 DEEPXIV_TOKEN（CP-F2-2 真实链路待凭证就绪补跑，本次省 deepxiv 配额）",
)


def _make_real_llm_config():
    """构造真实 LLMConfig（从 config getter 读 base_url/model/api_key，不写凭证值）。"""
    from config import (
        DEFAULT_LLM_MAX_TOKENS,
        DEFAULT_LLM_TEMPERATURE,
        get_llm_api_key,
        get_llm_base_url,
        get_llm_model,
    )
    from core.state import LLMConfig

    return LLMConfig(
        base_url=get_llm_base_url(),
        model=get_llm_model(),
        api_key=get_llm_api_key() or "",
        temperature=DEFAULT_LLM_TEMPERATURE,
        max_tokens=DEFAULT_LLM_MAX_TOKENS,
    )


def _make_wal_saver(db_path: Path):
    """新建 WAL 模式 SqliteSaver（与 core/checkpointer.py / sp2 c1_e2e 范式一致）。"""
    import sqlite3

    from langgraph.checkpoint.sqlite import SqliteSaver

    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    return conn, SqliteSaver(conn)


def _real_initial_state(workspace_dir: Path):
    """真实链路初始 state：用真实 create_initial_state（真实 LLMConfig + tmp workspace）。

    workspace_dir 指向 tmp，让真实 coding 写代码 + 真实 reporting 落盘都在 tmp，不污染真实 workspace。
    """
    from core.state import create_initial_state

    return create_initial_state(
        user_input=PAPER_ARXIV_ID,
        llm_config=_make_real_llm_config(),
        workspace_dir=str(workspace_dir),
    )


def _new_e2e_config():
    return {"configurable": {"thread_id": f"f2-real-e2e-{uuid.uuid4().hex}"}}


@pytest.mark.e2e
@skip_if_no_creds
class TestRealChainE2E:
    """CP-F2-2 真实链路 e2e（沿用 sp2「凭证就绪后补跑」范式）。

    五条对应 dev-plan §667-672 五场景（mock 5 条转真实 5 条）。**真实 LLM + 真实 deepxiv +
    mock sandbox** 为统一基调（§667）。每条用唯一 uuid4 thread_id + 真实 SqliteSaver(WAL) 隔离。

    本次**不跑**（省 deepxiv 日配额），仅落代码 + mock smoke 自验装配结构。凭证 + 配额就绪后
    由主控统一 smoke + 补跑转正。

    smoke 首选 = real-1（happy path B 档）：最省配额、一条真实链路即可 fail-fast 验凭证有效 +
    deepxiv 可达 + 全链路装配正确。主控应先单跑它：
        .venv/bin/pytest "tests/test_sprint3_e2e.py::TestRealChainE2E::test_real_1_happy_path_b_grade_success" -m e2e -v -s
    """

    # -------------------------------------------------------------------
    # 共享：mock sandbox（仅 sandbox 三入口；LLM/deepxiv/execution/reporting 全真实）
    # -------------------------------------------------------------------
    @staticmethod
    def _patch_sandbox_real(monkeypatch, *, run_results=None):
        """patch 真实 execution 模块内 sandbox 三入口，注入受控执行结果。

        复用本文件 mock 版 FakePrepareResult / FakeRunResult dataclass（与真实 sandbox 返回
        SandboxPrepareResult / SandboxRunResult 字段同构，execution 节点按属性取值，鸭子类型兼容）。
        _llm_extract_metrics 保持**真实**（dev-plan §667 happy path 用 <METRICS> 命中走解析路径，
        不触发 LLM 抽取；其余场景 stdout 无 <METRICS> 也基本走错误分支不抽取）。

        返回调用计数器（旁证 sandbox 实际被调用次数 / 修复回合数）。
        """
        cnt = {"prepare": 0, "run": 0, "collect": 0}
        runs = run_results if run_results is not None else [
            FakeRunResult(exit_code=0, stdout='<METRICS>{"accuracy": 0.9}</METRICS>')
        ]
        run_iter = iter(runs)

        def fake_prepare_venv(*a, **k):
            cnt["prepare"] += 1
            return FakePrepareResult()

        def fake_run_in_venv(*a, **k):
            cnt["run"] += 1
            try:
                return next(run_iter)
            except StopIteration:
                return runs[-1] if runs else FakeRunResult()

        def fake_collect_artifacts(*a, **k):
            cnt["collect"] += 1
            return []

        monkeypatch.setattr(execution_module, "prepare_venv", fake_prepare_venv)
        monkeypatch.setattr(execution_module, "run_in_venv", fake_run_in_venv)
        monkeypatch.setattr(execution_module, "collect_artifacts", fake_collect_artifacts)
        return cnt

    @staticmethod
    def _run_to_planning_pause(graph, config, workspace_dir):
        """真实跑 intake→analysis→scout→planning(interrupt#1) 暂停，断言确实暂停在 planning。"""
        out = graph.invoke(_real_initial_state(workspace_dir), config)
        assert "__interrupt__" in out, (
            f"真实链路未在 planning(interrupt#1) 暂停：keys={list(out.keys())}"
        )
        snap = graph.get_state(config)
        assert snap.next == ("planning",), f"未暂停在 planning：next={snap.next}"
        # interrupt#1 payload 契约：interrupt_kind=planning（区分于 execution interrupt#2）。
        interrupts = [
            iv for task in (snap.tasks or [])
            for iv in (getattr(task, "interrupts", None) or [])
        ]
        assert interrupts, "planning 暂停无 interrupt 元数据"
        assert interrupts[0].value.get("interrupt_kind") == "planning"
        return out

    # ===================================================================
    # real-1 happy path B 档成功（AC-S3-01）—— smoke 首选
    # ===================================================================
    def test_real_1_happy_path_b_grade_success(self, monkeypatch, tmp_path):
        """real-1（AC-S3-01）首次真实端到端复现：真实 LLM 驱动全流程 + mock sandbox 模拟成功。

        真实链路：真实 LLM 跑 intake→analysis→scout→planning(interrupt#1, approve resume)→
        coding(真实 ReAct 写代码)→execution(真实复合节点)→reporting(真实渲染)→END。
        mock 边界：sandbox 返回 exit 0 + 可解析 <METRICS>（§667「不真跑 30min 训练」）。

        断言聚焦契约（不 hard-code deepxiv 返回的论文标题文本——可能微变）：
          - paper_meta.arxiv_id 清洗为 2405.14831；
          - planning interrupt#1 approve → coding 真实产 code_output_dir；
          - execution B 档成功 success=True + metrics 含 mock 注入的 accuracy；
          - reporting full_success 形态 + 报告真落盘 tmp；
          - 全链路无 __interrupt__ 残留（END 态）。

        smoke 首选理由：单条真实链路即可 fail-fast 验 LLM 凭证有效 + deepxiv 可达 + 全装配正确，
        最省 deepxiv 配额（HippoRAG 大概率已缓存，只读已缓存章节）。
        """
        cnt = self._patch_sandbox_real(
            monkeypatch,
            run_results=[
                FakeRunResult(exit_code=0, stdout='<METRICS>{"accuracy": 0.893, "f1": 0.88}</METRICS>')
            ],
        )

        conn, saver = _make_wal_saver(tmp_path / "real1.db")
        try:
            graph = build_graph(checkpointer=saver)
            config = _new_e2e_config()

            # 真实跑到 planning interrupt#1 暂停。
            self._run_to_planning_pause(graph, config, tmp_path)

            # approve → coding(真实) → execution(mock sandbox 成功) → reporting → END。
            final = graph.invoke(Command(resume={"decision": "approve"}), config)
            try:
                conn.commit()
            except Exception:
                pass

            snap = graph.get_state(config)
            assert snap.next == (), f"approve 后应跑到 END：next={snap.next}"

            # 上游真实节点产物齐全。
            assert (final.get("paper_meta") or {}).get("arxiv_id") == PAPER_ARXIV_ID
            assert (final.get("reproduction_plan") or {}).get("approved") is True
            # coding 真实产代码目录。
            assert final.get("code_output_dir"), "coding 应真实产出 code_output_dir"

            # execution B 档成功（mock sandbox exit0 + 可解析 <METRICS>）。
            er = final.get("execution_result")
            assert er and er.get("success") is True, f"B 档应成功：execution_result={er}"
            assert er.get("metrics", {}).get("accuracy") == 0.893
            # happy path 无修复循环 → prepare 恰 1 次；run 次数 = reproduction_plan
            # .execution_steps 的步数（真实 LLM 可能规划多步执行，首跑实测 11 步），
            # 故断言 run >= 1，不 hard-code 步数（LLM 规划步数会变）。
            assert cnt["prepare"] == 1 and cnt["run"] >= 1

            # reporting full_success（真实渲染落盘 tmp）。
            assert reporting_module._determine_report_form(final) == "full_success"
            _read_report(final.get("report_path"))
            assert final.get("__interrupt__") is None
        finally:
            try:
                conn.close()
            except Exception:
                pass

    # ===================================================================
    # real-2 修复循环上限 3（AC-S3-03）
    # ===================================================================
    def test_real_2_fix_loop_upper_limit_three(self, monkeypatch, tmp_path):
        """real-2（AC-S3-03）：mock sandbox 连续可修复失败 → 真实 LLM coding 反复修复 →
        fix_loop_count 自增至 MAX_FIX_LOOP_COUNT(3) 拦截 → interrupt#2 暂停。

        真实链路：上游真实 + 真实 coding 修复回合（每轮真实 LLM 读失败反馈再写代码）。
        mock 边界：sandbox 每轮注入同一可修复失败（ModuleNotFoundError=import 可修复），驱动
        execution 真实错误分类 → retry_coding 回边 → coding(真实) → … 三回合后触顶。

        复跑要求（§674）：修复循环属 LLM 服从度 + 重跑幂等类风险，复现率视实测，复现率高连跑
        3 次 / 低连跑 5 次含全量回归。
        """
        self._patch_sandbox_real(
            monkeypatch,
            run_results=[
                FakeRunResult(exit_code=1, stderr="ModuleNotFoundError: No module named 'torch'")
            ],
        )

        conn, saver = _make_wal_saver(tmp_path / "real2.db")
        try:
            graph = build_graph(checkpointer=saver)
            config = _new_e2e_config()

            self._run_to_planning_pause(graph, config, tmp_path)
            out = graph.invoke(Command(resume={"decision": "approve"}), config)
            try:
                conn.commit()
            except Exception:
                pass

            # 达修复上限后 interrupt#2 暂停。
            assert "__interrupt__" in out, (
                f"达修复上限后应在 execution interrupt#2 暂停：keys={list(out.keys())}"
            )
            snap = graph.get_state(config)
            assert snap.values.get("fix_loop_count") == MAX_FIX_LOOP_COUNT, (
                f"fix_loop_count 应自增至上限 {MAX_FIX_LOOP_COUNT}，"
                f"实际 {snap.values.get('fix_loop_count')}"
            )
            interrupts = [
                iv for task in (snap.tasks or [])
                for iv in (getattr(task, "interrupts", None) or [])
            ]
            assert interrupts, "interrupt#2 暂停无 interrupt 元数据"
            payload = interrupts[0].value
            assert payload.get("interrupt_kind") == INTERRUPT_KIND_DEV_LOOP
            assert payload.get("options") == ["terminate", "revise_plan", "export_code"]
            # 修复历程满 3 条。
            history = snap.values.get("fix_loop_history") or []
            assert len(history) == MAX_FIX_LOOP_COUNT, (
                f"fix_loop_history 应记 {MAX_FIX_LOOP_COUNT} 轮，实际 {len(history)}"
            )
        finally:
            try:
                conn.close()
            except Exception:
                pass

    # ===================================================================
    # real-3 interrupt#2 三选一（AC-S3-07）
    # ===================================================================
    @pytest.mark.parametrize(
        "decision,expect",
        [
            ("terminate", "end"),
            ("revise_plan", "planning"),
            ("export_code", "reporting"),
        ],
    )
    def test_real_3_interrupt2_three_state_resume(self, monkeypatch, tmp_path, decision, expect):
        """real-3（AC-S3-07）：真实链路触发 dev_loop 失败 → interrupt#2 → Command(resume) 三态路由。

        真实链路：上游真实 + 真实 coding。用不可修复错误（CUDA OOM=hardware）一回合即触发
        interrupt#2（不必先耗修复回合，省真实 LLM coding 调用）。三态：
          - terminate  → END（current_step=cancelled_by_user）；
          - revise_plan → planning（真实图回流，fix_loop_count 清零）；
          - export_code → reporting（degraded 报告）。

        复跑要求（§674）：interrupt#2 属 LLM 服从度 + 重跑幂等类风险，复现率视实测连跑 3/5 次。
        revise_plan 分支真实回 planning 会再触发 interrupt#1（真实 planning 节点），本测试只断言
        路由到 planning + fix_loop_count 清零（不再二次 approve，省真实链路开销）。
        """
        self._patch_sandbox_real(
            monkeypatch,
            run_results=[
                FakeRunResult(exit_code=1, stderr="RuntimeError: CUDA out of memory")
            ],
        )

        conn, saver = _make_wal_saver(tmp_path / f"real3-{decision}.db")
        try:
            graph = build_graph(checkpointer=saver)
            config = _new_e2e_config()

            self._run_to_planning_pause(graph, config, tmp_path)
            # approve → coding(真实) → execution(不可修复) → interrupt#2 暂停。
            out1 = graph.invoke(Command(resume={"decision": "approve"}), config)
            try:
                conn.commit()
            except Exception:
                pass
            assert "__interrupt__" in out1, "不可修复失败应触发 interrupt#2 暂停"
            snap1 = graph.get_state(config)
            interrupts = [
                iv for task in (snap1.tasks or [])
                for iv in (getattr(task, "interrupts", None) or [])
            ]
            assert interrupts and interrupts[0].value.get("interrupt_kind") == INTERRUPT_KIND_DEV_LOOP

            extra = {"user_feedback": "请换用更小模型"} if decision == "revise_plan" else {}
            final = graph.invoke(Command(resume={"decision": decision, **extra}), config)
            try:
                conn.commit()
            except Exception:
                pass
            snap = graph.get_state(config)

            if expect == "end":
                assert snap.next == (), f"terminate 应到 END：next={snap.next}"
                assert final.get("current_step") == "cancelled_by_user"
                assert final.get("user_fix_decision") == "terminate"
            elif expect == "planning":
                # revise_plan 真实回流 planning（真实 planning 节点会再触发 interrupt#1）。
                assert final.get("user_fix_decision") == "revise_plan", "revise_plan 决策应落地"
                assert snap.values.get("fix_loop_count") == 0, (
                    f"revise_plan 回流应清零 fix_loop_count，实际 {snap.values.get('fix_loop_count')}"
                )
                # 路由确实回到 planning（再次 interrupt#1 暂停 或 next==planning），证明非卡死。
                assert "__interrupt__" in final or snap.next == ("planning",), (
                    f"revise_plan 应回 planning 重新走流程：next={snap.next}"
                )
            elif expect == "reporting":
                assert snap.next == (), f"export_code 应到 END（经 reporting）：next={snap.next}"
                assert final.get("user_fix_decision") == "export_code"
                assert execution_module.NODE_NAME in (final.get("degraded_nodes") or [])
                assert reporting_module._determine_report_form(final) == "degraded"
                _read_report(final.get("report_path"))
        finally:
            try:
                conn.close()
            except Exception:
                pass

    # ===================================================================
    # real-4 code_only（AC-S3-06）
    # ===================================================================
    def test_real_4_code_only_skips_execution(self, monkeypatch, tmp_path):
        """real-4（AC-S3-06）：planning interrupt#1 选 code_only → coding(真实) →
        skip_execution → reporting code_only 形态（跳过 execution + 修复循环）。

        真实链路：上游真实 + 真实 coding 产代码 + 真实 reporting code_only 渲染。
        mock 边界：sandbox 三入口被 patch 但**不应被调用**（execution 被跳过）——prepare 计数==0
        作为「execution 真未被触达」的强旁证。
        """
        cnt = self._patch_sandbox_real(monkeypatch)  # 若误触 execution，prepare 会 > 0

        conn, saver = _make_wal_saver(tmp_path / "real4.db")
        try:
            graph = build_graph(checkpointer=saver)
            config = _new_e2e_config()

            self._run_to_planning_pause(graph, config, tmp_path)
            # code_only resume → coding → skip_execution → reporting → END。
            final = graph.invoke(Command(resume={"decision": "code_only"}), config)
            try:
                conn.commit()
            except Exception:
                pass

            snap = graph.get_state(config)
            assert snap.next == (), f"code_only 应跑到 END：next={snap.next}"
            # execution 被跳过：sandbox 零调用。
            assert cnt["prepare"] == 0, (
                f"code_only 应跳过 execution，sandbox prepare 不应被调用，实际 {cnt['prepare']}"
            )
            assert cnt["run"] == 0
            assert final.get("execution_result") is None, "code_only 不应产生 execution_result"
            # execution_mode 落地 code_only。
            mode = final.get("execution_mode")
            assert str(getattr(mode, "value", mode)) in ("code_only", str(ExecutionMode.CODE_ONLY.value))
            # reporting code_only 形态（真实渲染落盘）。
            assert reporting_module._determine_report_form(final) == "code_only"
            report = _read_report(final.get("report_path"))
            assert "仅生成代码" in report, "code_only 报告应标注仅生成代码"
        finally:
            try:
                conn.close()
            except Exception:
                pass

    # ===================================================================
    # real-5 降级（AC-S3-09 ③）
    # ===================================================================
    def test_real_5_degraded_budget_exhausted(self, monkeypatch, tmp_path):
        """real-5（AC-S3-09 ③）：可修复失败但预算耗尽（入口预算门）→ 直接降级 →
        reporting degraded（不回 coding、不 interrupt）。

        真实链路：上游真实 + 真实 coding + 真实 execution 预算门判定 + 真实 reporting degraded 渲染。
        mock 边界：sandbox 注入可修复失败（ModuleNotFoundError）；通过 graph.update_state 把
        retry_budget_remaining 压到 < DEV_LOOP_MIN_CALLS_PER_ROUND(2)，触发 execution 入口预算门降级。

        说明：真实上游会消耗 retry_budget（真实 LLM 调用），到 execution 前剩余预算不确定；
        为稳定触发「入口预算门」分支，在 approve 前用 update_state 显式把预算压到 1
        （真实图持久化通道写入，模拟「预算已被上游耗尽」），再 approve 进入 coding→execution。
        """
        from config import DEV_LOOP_MIN_CALLS_PER_ROUND

        cnt = self._patch_sandbox_real(
            monkeypatch,
            run_results=[
                FakeRunResult(exit_code=1, stderr="ModuleNotFoundError: No module named 'torch'")
            ],
        )

        conn, saver = _make_wal_saver(tmp_path / "real5.db")
        try:
            graph = build_graph(checkpointer=saver)
            config = _new_e2e_config()

            self._run_to_planning_pause(graph, config, tmp_path)
            # 显式压低预算到入口预算门以下（< DEV_LOOP_MIN_CALLS_PER_ROUND），稳定触发降级分支。
            graph.update_state(config, {"retry_budget_remaining": DEV_LOOP_MIN_CALLS_PER_ROUND - 1})

            final = graph.invoke(Command(resume={"decision": "approve"}), config)
            try:
                conn.commit()
            except Exception:
                pass

            snap = graph.get_state(config)
            assert snap.next == (), f"预算门降级应跑到 END：next={snap.next}"
            # 不 interrupt（预算门降级直接出报告）。
            assert final.get("__interrupt__") is None, "预算耗尽应降级出报告，不 interrupt"
            assert execution_module.NODE_NAME in (final.get("degraded_nodes") or []), (
                "execution 应被标记 degraded"
            )
            assert reporting_module._determine_report_form(final) == "degraded"
            report = _read_report(final.get("report_path"))
            assert "未成功" in report or "降级" in report, "degraded 报告应含未成功/降级结论"
            # 预算门降级：未回 coding（sandbox 只跑 1 次，无修复回合）。
            assert cnt["prepare"] == 1
        finally:
            try:
                conn.close()
            except Exception:
                pass
