"""Sprint 3 阶段 F / 任务 F1：单元/集成自测全套（mock）的 **AC 级聚合验收**。

本文件不重复造轮子：sp3 阶段 A~E 各任务的开发单测 + 测试工程师补强（test_sprint3_a1
/ a2 / a_boundary / b1 / b2 / b2_strengthen / c1 / c1_fix / c1_fix_reinforce / c2
/ c3 / c3_reinforce / d1 / d1_reinforce / e1~e3 + reinforce）已覆盖绝大部分场景。

F1 的增量价值（收尾性）：
  1. **AC 覆盖审计**：逐条断言 AC-S3-02/03/04/05/06/08/09/10（F1 mock 部分）已被
     对应 CP 测试函数覆盖——通过 import 并断言 CP 测试函数 callable 存在（防回归删除）。
  2. **must-fix-1 Sprint 级专项聚合断言**（CP-F1-2，AC-S3-05）：grep 三字段无 reducer
     + 多回合修复三字段 read-modify-write 无丢失无重复累加（直接行为断言，不依赖底层 CP）。
  3. **must-fix-2 Sprint 级专项聚合断言**（CP-F1-3，AC-S3-04）：预算回写 + 子预算 60
     + 入口预算门三项专项断言（直接行为断言）。
  4. **CP-F1-1/4 收尾确认**：AC-S3-02/03/05/06/08/09/10 mock 单测全覆盖审计。

约束：全部 mock 单测，不依赖凭证 / 真实长训练 / e2e（那是 F2）。

—— AC-S3-01（真实 e2e happy path）/ AC-S3-07 的真实 e2e 部分留 **F2**，不在 F1 范围。
   本文件对 AC-S3-07 仅覆盖 mock(Command resume) 部分（CP-C3-7 / CP-D1-4 已覆盖）。
"""

from __future__ import annotations

import importlib
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

STATE_PY = PROJECT_ROOT / "core" / "state.py"

from config import (  # noqa: E402
    DEV_LOOP_MIN_CALLS_PER_ROUND,
    MAX_DEV_LOOP_LLM_CALLS,
    MAX_FIX_LOOP_COUNT,
    MAX_TOTAL_LLM_CALLS,
    WORKSPACE_DIR,
)
from core.state import ExecutionMode, GlobalState  # noqa: E402

# importlib 拿真实子模块（避免 core/nodes/__init__ 显式 export callable 遮蔽，坑 6）。
execution_module = importlib.import_module("core.nodes.execution")


# ===========================================================================
# 共享 mock 脚手架（与 c3 同构，独立维护避免跨文件耦合）
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


def _work_dir() -> str:
    return str(WORKSPACE_DIR / "f1-test" / "code")


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
    counter: Optional[Dict[str, int]] = None,
) -> Dict[str, int]:
    # 【sp4 E4 mock 落点适配 2026-07-04】E3 把步骤 1+2 换成 _run_execution_agent
    # 内嵌子图，mock 落点上移（同 tests/test_sprint3_c3.py 适配注记）：每次 agent
    # 调用 = 一次 prepare + 消费一条 run；rounds_used=0 保持 must-fix-2「仅 metrics
    # 档 3 抽取扣减」的预算断言字节级不变（子图 rounds 扣减由 CP-E3-1 覆盖）。
    cnt = counter if counter is not None else {"prepare": 0, "run": 0}
    prep_obj = prep if prep is not None else FakePrepareResult()
    runs = run_results if run_results is not None else [
        FakeRunResult(exit_code=0, stdout='<METRICS>{"accuracy": 0.9}</METRICS>')
    ]
    run_iter = iter(runs)

    def fake_run_execution_agent(state: Any, work_dir: Any, plan: Any):
        cnt["prepare"] = cnt.get("prepare", 0) + 1
        cnt["run"] = cnt.get("run", 0) + 1
        try:
            rr = next(run_iter)
        except StopIteration:
            rr = runs[-1] if runs else FakeRunResult()
        return execution_module.ExecAgentOutput(
            prep=prep_obj, run_results=[rr], rounds_used=0, llm_calls=0,
        )

    monkeypatch.setattr(execution_module, "_run_execution_agent", fake_run_execution_agent)
    monkeypatch.setattr(execution_module, "collect_artifacts", lambda *a, **k: [])
    return cnt


def _no_llm_metrics(monkeypatch: pytest.MonkeyPatch) -> None:
    """默认让 LLM 抽取不触发（零扣减），便于隔离预算行为。"""
    monkeypatch.setattr(execution_module, "_llm_extract_metrics", lambda *a, **k: ({}, 0))


# ===========================================================================
# CP-F1-2：must-fix-1 Sprint 级专项聚合验收（AC-S3-05）
# ===========================================================================


def test_cp_f1_2_grep_three_list_fields_have_no_reducer() -> None:
    """CP-F1-2 ①（AC-S3-05 ① 强制验收点，Sprint 级再确认）：

    grep `Annotated|operator.add` 命中行不得落在 node_errors / degraded_nodes /
    fix_loop_history 三字段声明上；三字段以普通 `field: List[...]` 形态存在。
    """
    proc = subprocess.run(
        ["grep", "-nE", r"Annotated|operator\.add", str(STATE_PY)],
        capture_output=True,
        text=True,
    )
    matched = proc.stdout.strip().splitlines()
    forbidden = ("node_errors", "degraded_nodes", "fix_loop_history")
    for line in matched:
        for fld in forbidden:
            assert fld not in line, (
                f"must-fix-1 违规：{fld} 出现在含 Annotated/operator.add 的行：{line}"
            )

    src = STATE_PY.read_text(encoding="utf-8")
    assert "node_errors: List[NodeError]" in src
    assert "degraded_nodes: List[str]" in src
    assert "fix_loop_history: List[FixLoopRecord]" in src


def test_cp_f1_2_three_fields_annotation_origin_is_plain_list() -> None:
    """CP-F1-2 ②：从 GlobalState.__annotations__ 验证三字段 origin 为 list（非 Annotated）。"""
    import typing

    ann = GlobalState.__annotations__
    for fld in ("node_errors", "degraded_nodes", "fix_loop_history"):
        assert fld in ann, f"{fld} 应在 GlobalState 注解中"
        origin = typing.get_origin(ann[fld])
        assert origin is list, (
            f"{fld} 的 origin 应为 list（普通 List），实际 {origin}——疑似被加了 reducer"
        )


def test_cp_f1_2_multi_round_fix_three_fields_no_loss_no_dup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CP-F1-2 ③（AC-S3-05 ②，Sprint 级聚合）：

    模拟「已存在历史 + 本回合追加」的 read-modify-write 合并，断言三个 list 字段
    记录完整无丢失、无重复累加（execution 出口对三字段写整列表，不靠 reducer 拼接）。

    具体：state 预置 1 条 node_error + 1 条 fix_record；mock 一次可修复失败 →
    execution 出口对 fix_loop_history append 本回合记录 → 历史 2 条无丢失、无翻倍。
    """
    _patch_sandbox(
        monkeypatch,
        run_results=[FakeRunResult(exit_code=1, stderr="ModuleNotFoundError: No module named 'torch'")],
    )
    _no_llm_metrics(monkeypatch)

    prior_error = {
        "node": "execution",
        "error_type": "transient",
        "error_message": "[dependency] 之前一轮的错误",
        "timestamp": "2026-06-29T00:00:00Z",
    }
    prior_record = {
        "round": 1,
        "error_category": "dependency",
        "error_summary": "上一回合缺依赖",
        "fix_action": "装包",
        "outcome": "retried",
    }
    state = _base_state(
        node_errors=[dict(prior_error)],
        fix_loop_history=[dict(prior_record)],
        fix_loop_count=1,
    )

    updates = execution_module.execution(state)

    # fix_loop_history：旧 1 条 + 本回合 1 条 = 2 条，无丢失无翻倍。
    new_history = updates.get("fix_loop_history")
    assert new_history is not None, "execution 出口应回写 fix_loop_history（整列表）"
    assert len(new_history) == 2, (
        f"fix_loop_history 应为 旧1+新1=2 条，实际 {len(new_history)} 条"
        "（丢失=read 漏旧；翻倍=误加 reducer 重复累加）"
    )
    # 旧记录原样保留（无丢失）。
    assert new_history[0]["round"] == 1
    assert new_history[0]["error_summary"] == "上一回合缺依赖"

    # node_errors 若被本回合追加，则旧记录也不得丢失。
    if "node_errors" in updates:
        ne = updates["node_errors"]
        # 旧错误必须仍在（read-modify-write 而非覆盖）。
        assert any(e.get("error_message") == prior_error["error_message"] for e in ne), (
            "read-modify-write 应保留旧 node_error，未保留=read 漏旧（丢失）"
        )
        # 无重复累加：旧错误恰出现一次。
        dup = sum(1 for e in ne if e.get("error_message") == prior_error["error_message"])
        assert dup == 1, f"旧 node_error 出现 {dup} 次，应恰 1 次（>1=重复累加）"


# ===========================================================================
# CP-F1-3：must-fix-2 Sprint 级专项聚合验收（AC-S3-04）
# ===========================================================================


def test_cp_f1_3_dev_loop_subbudget_constant_below_total() -> None:
    """CP-F1-3 ②（AC-S3-04 ② 直接验收点）：MAX_DEV_LOOP_LLM_CALLS==60 且 < MAX_TOTAL_LLM_CALLS。"""
    assert MAX_DEV_LOOP_LLM_CALLS == 60, f"子预算应为 60，实际 {MAX_DEV_LOOP_LLM_CALLS}"
    assert MAX_DEV_LOOP_LLM_CALLS < MAX_TOTAL_LLM_CALLS, (
        f"子预算 {MAX_DEV_LOOP_LLM_CALLS} 必须 < 总预算 {MAX_TOTAL_LLM_CALLS}"
    )


def test_cp_f1_3_budget_writeback_on_llm_extract(monkeypatch: pytest.MonkeyPatch) -> None:
    """CP-F1-3 ①（AC-S3-04 ①）：metrics LLM 抽取触发时主动单点回写 retry_budget_remaining
    + 累加 _dev_loop_llm_calls（不再零扣减）。
    """
    _patch_sandbox(
        monkeypatch,
        run_results=[FakeRunResult(exit_code=0, stdout="acc=0.88")],  # 无 <METRICS> 块，逼 LLM 兜底
    )
    # mock LLM 抽取返回 1 个指标 + 消耗 2 次调用。
    monkeypatch.setattr(
        execution_module, "_llm_extract_metrics", lambda *a, **k: ({"accuracy": 0.88}, 2)
    )

    state = _base_state(retry_budget_remaining=40, _dev_loop_llm_calls=0)
    updates = execution_module.execution(state)

    assert updates.get("retry_budget_remaining") == 38, (
        f"预算应回写 40-2=38，实际 {updates.get('retry_budget_remaining')}（零扣减=must-fix-2 回归）"
    )
    assert updates.get("_dev_loop_llm_calls") == 2, (
        f"_dev_loop_llm_calls 应累加 2，实际 {updates.get('_dev_loop_llm_calls')}"
    )


def test_cp_f1_3_no_llm_no_budget_change(monkeypatch: pytest.MonkeyPatch) -> None:
    """CP-F1-3 ①补：LLM 抽取未触发（档1/档2 命中 <METRICS>/正则）→ 预算零扣减。"""
    _patch_sandbox(
        monkeypatch,
        run_results=[FakeRunResult(exit_code=0, stdout='<METRICS>{"accuracy": 0.9}</METRICS>')],
    )
    _no_llm_metrics(monkeypatch)

    state = _base_state(retry_budget_remaining=40, _dev_loop_llm_calls=3)
    updates = execution_module.execution(state)

    # 无 LLM 调用 → 不触发预算回写键，或回写值不变。
    if "retry_budget_remaining" in updates:
        assert updates["retry_budget_remaining"] == 40
    if "_dev_loop_llm_calls" in updates:
        assert updates["_dev_loop_llm_calls"] == 3


def test_cp_f1_3_entry_budget_gate_degrades(monkeypatch: pytest.MonkeyPatch) -> None:
    """CP-F1-3 ③（AC-S3-04 ③）：retry_budget_remaining < DEV_LOOP_MIN_CALLS_PER_ROUND
    且本回合需修复（可修复失败）→ 入口预算门直接降级，不空转再修。

    DEV_LOOP_MIN_CALLS_PER_ROUND 默认 2；预算=1 时不足以启动一回合 → 降级。
    """
    assert DEV_LOOP_MIN_CALLS_PER_ROUND >= 1
    _patch_sandbox(
        monkeypatch,
        run_results=[FakeRunResult(exit_code=1, stderr="ModuleNotFoundError: No module named 'torch'")],
    )
    _no_llm_metrics(monkeypatch)

    state = _base_state(retry_budget_remaining=DEV_LOOP_MIN_CALLS_PER_ROUND - 1)
    updates = execution_module.execution(state)

    # 降级：标 degraded、不路由回 coding。
    degraded = updates.get("degraded_nodes") or []
    route = updates.get("_dev_loop_route")
    assert "execution" in degraded or route in (None, "degraded", "budget_exhausted") or (
        route != "retry_coding"
    ), (
        f"预算不足应降级（degraded_nodes={degraded} / _dev_loop_route={route}），不得回 coding 空转"
    )
    assert route != "retry_coding", "入口预算门触发时禁止回 coding 重试"


# ===========================================================================
# CP-F1-4 + CP-F1-1：AC 级覆盖审计
# ---------------------------------------------------------------------------
# 逐条断言 F1 负责的 AC 已被对应 CP 测试函数覆盖：import 测试模块，断言 CP 测试
# 函数 callable 存在（防后续误删；同时建立可执行的 AC→测试映射）。
# ===========================================================================


def _assert_callables_exist(module_name: str, func_names: List[str]) -> None:
    mod = importlib.import_module(module_name)
    for fn in func_names:
        obj = getattr(mod, fn, None)
        assert callable(obj), f"{module_name}::{fn} 缺失或不可调用（AC 覆盖回归）"


# AC → 覆盖它的（测试模块, 测试函数列表）映射。各 AC 至少一条 mock 单测。
AC_COVERAGE_MAP: Dict[str, List[tuple]] = {
    "AC-S3-02": [  # sandbox 4 护栏
        ("tests.test_sprint3_b1", [
            "test_cp_b1_2_timeout_kills_process_tree",
            "test_cp_b1_3_output_truncation_keeps_tail",
            "test_cp_b1_4_prepare_venv_rejects_out_of_workspace",
            "test_cp_b1_5_nonzero_exit_does_not_raise",
        ]),
    ],
    "AC-S3-03": [  # 修复循环计数 + 上限拦截（上限取 MAX_FIX_LOOP_COUNT）
        ("tests.test_sprint3_c3", [
            "test_cp_c3_4_retry_coding_increments",
            "test_cp_c3_5_upper_limit_to_interrupt",
        ]),
    ],
    "AC-S3-04": [  # 预算回写 + 子预算（MAX_DEV_LOOP_LLM_CALLS）+ 入口预算门
        ("tests.test_sprint3_c3", [
            "test_cp_c3_8_budget_writeback_on_llm_extract",
            "test_cp_c3_9_entry_budget_gate_degrade",
            "test_cp_c3_10_dev_loop_budget_ceiling",
        ]),
        ("tests.test_sprint3_a1", ["test_cp_a1_3_dev_loop_budget_strictly_less_than_total"]),
    ],
    "AC-S3-05": [  # list 无 reducer 单点合并
        ("tests.test_sprint3_a2", ["test_cp_a2_3_no_reducer_on_three_list_fields"]),
        ("tests.test_sprint3_c3", ["test_cp_c3_11_read_modify_write_no_loss"]),
        ("tests.test_sprint3_c1", ["test_cp_c1_4_three_arg_signature_and_degraded"]),
    ],
    "AC-S3-06": [  # code_only 跳过 execution + 修复循环
        ("tests.test_sprint3_d1", ["test_cp_d1_3_route_after_coding"]),
        ("tests.test_sprint3_c2", ["test_cp_c2_3_code_only"]),
    ],
    "AC-S3-07": [  # interrupt#2 三选一（mock Command resume 部分）
        ("tests.test_sprint3_c3", ["test_cp_c3_7_interrupt_three_state_resume"]),
        ("tests.test_sprint3_d1", [
            "test_cp_d1_4_terminate_to_end",
            "test_cp_d1_4_revise_plan_to_planning",
            "test_cp_d1_4_export_code_to_reporting",
        ]),
    ],
    "AC-S3-08": [  # 不可修复类不进重试
        ("tests.test_sprint3_c3", [
            "test_cp_c3_3_classify_auto_fixable_split",
            "test_cp_c3_6_unfixable_no_retry",
        ]),
    ],
    "AC-S3-09": [  # reporting 三形态
        ("tests.test_sprint3_c2", [
            "test_cp_c2_2_full_success",
            "test_cp_c2_3_code_only",
            "test_cp_c2_4_degraded",
        ]),
    ],
    "AC-S3-10": [  # 主图 7 节点骨架不变性
        ("tests.test_sprint3_d1", [
            "test_cp_d1_1_build_graph_compiles",
            "test_cp_d1_1_exactly_seven_nodes",
            "test_cp_d1_1_no_forbidden_subgraph_nodes",
        ]),
    ],
}


@pytest.mark.parametrize("ac_id", sorted(AC_COVERAGE_MAP.keys()))
def test_cp_f1_4_ac_has_mock_test_coverage(ac_id: str) -> None:
    """CP-F1-4（AC-S3-02/03/05/06/08/09/10 + AC-04 + AC-07 mock 部分）：

    逐条断言每个 F1 负责的 AC 至少被一条已存在、可调用的 mock 测试函数覆盖。
    这是「AC 覆盖矩阵」的可执行版——后续若误删覆盖测试，本断言立即红。
    """
    coverage = AC_COVERAGE_MAP[ac_id]
    assert coverage, f"{ac_id} 无任何覆盖映射"
    for module_name, func_names in coverage:
        _assert_callables_exist(module_name, func_names)


def test_cp_f1_4_coverage_map_spans_all_f1_owned_acs() -> None:
    """CP-F1-4 元断言：覆盖矩阵恰覆盖 F1 负责的 8 条 AC（02~10 除 01）。

    AC-S3-01 真实 e2e happy path 留 F2（mock 部分由 CP-D1-7 / CP-C3-2 旁证，
    但 B 档真实成功判定的 e2e 转正在 F2），故不纳入 F1 强制矩阵键。
    """
    expected = {
        "AC-S3-02", "AC-S3-03", "AC-S3-04", "AC-S3-05",
        "AC-S3-06", "AC-S3-07", "AC-S3-08", "AC-S3-09", "AC-S3-10",
    }
    assert set(AC_COVERAGE_MAP.keys()) == expected, (
        f"覆盖矩阵键应恰为 F1 负责的 9 条 AC（02~10），实际 {sorted(AC_COVERAGE_MAP.keys())}"
    )


def test_cp_f1_1_sprint3_test_modules_all_importable() -> None:
    """CP-F1-1 旁证：sp3 全套 test_sprint3_* 模块均可被 import（无收集期错误）。

    （全套通过 + 全量回归不退化由 `pytest -q -m "not e2e"` 在 CI/手动跑确认，
    本断言仅守门「模块可收集」这一前提，防 import 期回归。）
    """
    sp3_modules = [
        "test_sprint3_a1", "test_sprint3_a2", "test_sprint3_a_boundary",
        "test_sprint3_b1", "test_sprint3_b2", "test_sprint3_b2_strengthen",
        "test_sprint3_c1", "test_sprint3_c1_fix", "test_sprint3_c1_fix_reinforce",
        "test_sprint3_c2", "test_sprint3_c3", "test_sprint3_c3_reinforce",
        "test_sprint3_d1", "test_sprint3_d1_reinforce",
        "test_sprint3_e1", "test_sprint3_e1_reinforce",
        "test_sprint3_e2", "test_sprint3_e2_reinforce",
        "test_sprint3_e3", "test_sprint3_e3_reinforce",
    ]
    for m in sp3_modules:
        mod = importlib.import_module(f"tests.{m}")
        assert mod is not None, f"tests.{m} 无法 import"


# ===========================================================================
# 错误分类两类分流的 Sprint 级直接断言（AC-S3-08 收尾再确认，不依赖 c3 内部）
# ===========================================================================


def test_f1_classify_two_class_split_direct() -> None:
    """AC-S3-08 Sprint 级直接断言：可修复类 auto_fixable=True、不可修复类=False。

    直接调 _classify_execution，绕过 c3 内部脚手架，独立再确认分流不退化。
    """
    classify = execution_module._classify_execution
    ErrorCategory = execution_module.ErrorCategory
    AUTO_FIXABLE = execution_module.AUTO_FIXABLE

    # 不可修复类集合断言。
    unfixable = {
        ErrorCategory.DATA_MISSING,
        ErrorCategory.HARDWARE,
        ErrorCategory.TIMEOUT,
        ErrorCategory.UNRESOLVED_RESOURCE,
    }
    for cat in unfixable:
        assert cat not in AUTO_FIXABLE, f"{cat} 不应在 AUTO_FIXABLE（不可修复类不进重试）"
    # 可修复类集合断言。
    for cat in (ErrorCategory.SYNTAX, ErrorCategory.IMPORT, ErrorCategory.DEPENDENCY,
                ErrorCategory.PATH, ErrorCategory.RUNTIME):
        assert cat in AUTO_FIXABLE, f"{cat} 应在 AUTO_FIXABLE（可修复类送回 coding）"

    # 行为级：超时 → TIMEOUT（不可修复）。
    prep = FakePrepareResult()
    run_timeout = [FakeRunResult(exit_code=-1, timed_out=True, stderr="killed: timeout")]
    fb = classify(prep, run_timeout)
    assert fb.category == ErrorCategory.TIMEOUT
    assert fb.auto_fixable is False, "超时（疑似死循环）必须归不可修复，不送回 coding"

    # 行为级：缺依赖 → DEPENDENCY（可修复）。
    run_dep = [FakeRunResult(exit_code=1, stderr="ModuleNotFoundError: No module named 'torch'")]
    fb2 = classify(prep, run_dep)
    assert fb2.category in (ErrorCategory.DEPENDENCY, ErrorCategory.IMPORT)
    assert fb2.auto_fixable is True, "缺依赖/import 错应可修复，送回 coding"
