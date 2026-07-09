"""Sprint 5 任务 T-S5-1-2 自测：core/state.py 新字段全量声明（架构 sp5 §8 总表）。

覆盖 dev-plan sp5 T-S5-1-2 CP-1.2-1 / CP-1.2-2（CP-1.2-3 全量回归由主控批次收口统一跑）。

参考实现：sp4 同款风格 tests/test_sprint4_a2.py（轻量结构性断言，无真实 LLM）。

字段清单（一次声明齐，供批次 2/3 消费）：
    - GlobalState：credential_degradations (Dict[str, str] / {})、
      simulation_notice (Optional[str] / None)、honesty_audit (Optional[Dict] / None)；
    - ReproductionPlan：required_credentials (List[Dict[str, str]] / [])、
      expected_results (Dict → List[Dict]，**sp5 唯一 breaking**)；
    - ExecutionResult：step_reconciliation (Dict) / budget_truncated (bool) /
      metrics_groups (Dict[str, Dict[str, Any]]) / degraded_credentials (List[str])
      —— 仅 TypedDict 键声明，构造点补齐属 T-S5-2-6。

约束（must-fix-1 + B2/B3 治理钉死）：
    - 三 List 字段（node_errors/degraded_nodes/fix_loop_history）与全部新字段
      均无 Annotated / operator.add（CP-1.2-2 grep 双证沿用 sp3/sp4 写法）；
    - 新 GlobalState 字段均单值通道、显式声明 + create_initial_state 默认值；
    - 下游消费一律 .get() 防御读（兼容旧 checkpoint 无新键，本文件含防御读演练）。
"""

from __future__ import annotations

import subprocess
import typing
from pathlib import Path

from core.state import (
    ExecutionResult,
    GlobalState,
    LLMConfig,
    ReproductionPlan,
    create_initial_state,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
STATE_PY = PROJECT_ROOT / "core" / "state.py"


# 构造一份合法 LLMConfig 供 create_initial_state 使用（纯结构，不发起网络请求）
_CFG: LLMConfig = {
    "base_url": "https://a.example/v1",
    "model": "model-a",
    "api_key": "sk-a",
    "temperature": 0.3,
    "max_tokens": 8192,
}


# ========== CP-1.2-1：三结构含全部新字段且类型正确 + create_initial_state 默认值 ==========


def test_cp_1_2_1_globalstate_has_three_new_fields_with_correct_types() -> None:
    """CP-1.2-1: GlobalState.__annotations__ 含 sp5 三新字段，类型分别为
    Dict[str, str] / Optional[str] / Optional[Dict]。"""
    ann = GlobalState.__annotations__

    assert "credential_degradations" in ann, "GlobalState 应含 credential_degradations"
    assert "simulation_notice" in ann, "GlobalState 应含 simulation_notice"
    assert "honesty_audit" in ann, "GlobalState 应含 honesty_audit"

    # state.py 顶部无 `from __future__ import annotations`，故注解为真实类型对象。
    assert ann["credential_degradations"] == typing.Dict[str, str], (
        f"credential_degradations 应为 Dict[str, str]，实测：{ann['credential_degradations']}"
    )
    assert ann["simulation_notice"] == typing.Optional[str], (
        f"simulation_notice 应为 Optional[str]，实测：{ann['simulation_notice']}"
    )
    assert ann["honesty_audit"] == typing.Optional[typing.Dict], (
        f"honesty_audit 应为 Optional[Dict]，实测：{ann['honesty_audit']}"
    )


def test_cp_1_2_1_reproduction_plan_new_fields_and_breaking_shape() -> None:
    """CP-1.2-1: ReproductionPlan 含 required_credentials（List[Dict[str, str]]）；
    expected_results 已从 Dict 变为 List[Dict[str, Any]]（sp5 唯一 breaking）。"""
    ann = ReproductionPlan.__annotations__

    assert "required_credentials" in ann, "ReproductionPlan 应含 required_credentials"
    assert ann["required_credentials"] == typing.List[typing.Dict[str, str]], (
        f"required_credentials 应为 List[Dict[str, str]]，实测：{ann['required_credentials']}"
    )

    assert "expected_results" in ann
    assert ann["expected_results"] == typing.List[typing.Dict[str, typing.Any]], (
        f"expected_results 应为 List[Dict[str, Any]]（breaking），实测：{ann['expected_results']}"
    )
    # 反向证：origin 必须是 list 而非 dict（防止误留旧形态）
    assert typing.get_origin(ann["expected_results"]) is list


def test_cp_1_2_1_execution_result_has_four_new_keys_with_correct_types() -> None:
    """CP-1.2-1: ExecutionResult 含 4 新键且类型正确（仅键声明；构造点补齐属 T-S5-2-6）。"""
    ann = ExecutionResult.__annotations__

    assert "step_reconciliation" in ann
    assert "budget_truncated" in ann
    assert "metrics_groups" in ann
    assert "degraded_credentials" in ann

    assert ann["step_reconciliation"] == typing.Dict[str, typing.Any], (
        f"step_reconciliation 应为 Dict[str, Any]，实测：{ann['step_reconciliation']}"
    )
    assert ann["budget_truncated"] is bool, (
        f"budget_truncated 应为 bool，实测：{ann['budget_truncated']}"
    )
    assert ann["metrics_groups"] == typing.Dict[str, typing.Dict[str, typing.Any]], (
        f"metrics_groups 应为 Dict[str, Dict[str, Any]]，实测：{ann['metrics_groups']}"
    )
    assert ann["degraded_credentials"] == typing.List[str], (
        f"degraded_credentials 应为 List[str]，实测：{ann['degraded_credentials']}"
    )


def test_cp_1_2_1_create_initial_state_defaults() -> None:
    """CP-1.2-1: create_initial_state 对三新 GlobalState 字段填充正确默认值
    （老形态 LLMConfig 与新形态 LLMConfigSet 入参双验）。"""
    # 老形态
    st1 = create_initial_state("2103.00020", _CFG)
    assert st1["credential_degradations"] == {}, "credential_degradations 默认值应为空 dict"
    assert type(st1["credential_degradations"]) is dict
    assert st1["simulation_notice"] is None, "simulation_notice 默认值应为 None"
    assert st1["honesty_audit"] is None, "honesty_audit 默认值应为 None"

    # 新形态 LLMConfigSet
    st2 = create_initial_state(
        "2103.00020", {"default": _CFG, "overrides": {}}  # type: ignore[arg-type]
    )
    assert st2["credential_degradations"] == {}
    assert st2["simulation_notice"] is None
    assert st2["honesty_audit"] is None

    # 两次调用的 credential_degradations 必须是独立 dict 实例（可变默认值别名防护）
    assert st1["credential_degradations"] is not st2["credential_degradations"], (
        "credential_degradations 不得跨 create_initial_state 调用共享同一 dict 实例"
    )


def test_cp_1_2_1_aux_existing_channels_unchanged() -> None:
    """CP-1.2-1 补（零改动行）：既有关键字段注解与默认值零漂移。"""
    ann = GlobalState.__annotations__

    assert ann["fix_loop_count"] is int
    assert ann["retry_budget_remaining"] is int
    assert ann["pending_user_input"] == typing.Optional[typing.Dict]
    assert ann["collected_inputs"] == typing.Dict[str, str]
    assert type(None) in typing.get_args(ann["execution_result"])
    assert type(None) in typing.get_args(ann["reproduction_plan"])

    state = create_initial_state("2103.00020", _CFG)
    assert state["reproduction_plan"] is None
    assert state["execution_result"] is None
    assert state["node_errors"] == []
    assert state["degraded_nodes"] == []
    assert state["fix_loop_history"] == []
    assert state["pending_user_input"] is None
    assert state["collected_inputs"] == {}


def test_cp_1_2_1_aux_old_checkpoint_snapshot_get_reads_through() -> None:
    """CP-1.2-1 补（R-5/R-6 防御读演练）：模拟旧 checkpoint state 快照
    （无任何 sp5 新键），下游 .get() 读一律不抛 KeyError 且拿到安全默认值。"""
    old_state: dict = {
        "user_input": "2103.00020",
        "current_step": "reporting",
        "reproduction_plan": {"expected_results": {"acc": 0.92}},  # 旧 dict 形态
        "execution_result": {"success": True, "metrics": {}},
    }

    # GlobalState 层
    assert old_state.get("credential_degradations", {}) == {}
    assert old_state.get("simulation_notice") is None
    assert old_state.get("honesty_audit") is None

    # ReproductionPlan 层（旧 dict 形态 expected_results 容忍归消费侧，此处仅验不抛）
    plan = old_state.get("reproduction_plan") or {}
    assert plan.get("required_credentials", []) == []
    assert isinstance(plan.get("expected_results"), dict)  # 旧形态原样读出，不崩

    # ExecutionResult 层
    exec_result = old_state.get("execution_result") or {}
    assert exec_result.get("step_reconciliation", {}) == {}
    assert exec_result.get("budget_truncated", False) is False
    assert exec_result.get("metrics_groups", {}) == {}
    assert exec_result.get("degraded_credentials", []) == []


# ========== CP-1.2-2：must-fix-1 grep 双证沿用（sp3 CP-A2-3 / sp4 CP-A2-2 写法） ==========


def test_cp_1_2_2_no_reducer_grep_evidence() -> None:
    """CP-1.2-2（must-fix-1 grep 证）：三 List 字段与全部 sp5 新字段
    绝不加 Annotated / operator.add。

    用 `grep -nE "Annotated|operator.add" core/state.py` 实证：
        - 若全文件零命中，全部字段必为普通声明；
        - 若存在命中行，命中行不得落在受保护字段声明行上。
    """
    proc = subprocess.run(
        ["grep", "-nE", r"Annotated|operator\.add", str(STATE_PY)],
        capture_output=True,
        text=True,
    )
    matched_lines = proc.stdout.strip().splitlines()

    if matched_lines:
        forbidden_fields = (
            # must-fix-1 三 List 字段
            "node_errors",
            "degraded_nodes",
            "fix_loop_history",
            # sp5 全部新字段
            "credential_degradations",
            "simulation_notice",
            "honesty_audit",
            "required_credentials",
            "expected_results",
            "step_reconciliation",
            "budget_truncated",
            "metrics_groups",
            "degraded_credentials",
        )
        for line in matched_lines:
            for field in forbidden_fields:
                assert field not in line, (
                    f"must-fix-1 违规：{field} 出现在含 Annotated/operator.add 的行：{line}"
                )

    # 正向断言：受保护字段在 state.py 中以普通形态声明
    src = STATE_PY.read_text(encoding="utf-8")
    assert "node_errors: List[NodeError]" in src
    assert "degraded_nodes: List[str]" in src
    assert "fix_loop_history: List[FixLoopRecord]" in src
    assert "credential_degradations: Dict[str, str]" in src
    assert "simulation_notice: Optional[str]" in src
    assert "honesty_audit: Optional[Dict]" in src
    assert "required_credentials: List[Dict[str, str]]" in src
    assert "expected_results: List[Dict[str, Any]]" in src
    assert "step_reconciliation: Dict[str, Any]" in src
    assert "budget_truncated: bool" in src
    assert "metrics_groups: Dict[str, Dict[str, Any]]" in src
    assert "degraded_credentials: List[str]" in src


def test_cp_1_2_2_aux_annotations_have_no_annotated_origin() -> None:
    """CP-1.2-2 补：从 __annotations__ 验证全部受保护字段无 Annotated 包装
    （typing.get_origin(Annotated[...]) 不会是 list/dict/Union/bool 基元）。"""
    gs_ann = GlobalState.__annotations__
    plan_ann = ReproductionPlan.__annotations__
    exec_ann = ExecutionResult.__annotations__

    # 三 List 字段：origin 必须是 list
    for field in ("node_errors", "degraded_nodes", "fix_loop_history"):
        assert typing.get_origin(gs_ann[field]) is list, f"{field} 应为普通 List"

    # sp5 GlobalState 新字段
    assert typing.get_origin(gs_ann["credential_degradations"]) is dict
    assert typing.get_origin(gs_ann["simulation_notice"]) is typing.Union  # Optional
    assert typing.get_origin(gs_ann["honesty_audit"]) is typing.Union  # Optional

    # sp5 ReproductionPlan 新/变更字段
    assert typing.get_origin(plan_ann["required_credentials"]) is list
    assert typing.get_origin(plan_ann["expected_results"]) is list

    # sp5 ExecutionResult 新键
    assert typing.get_origin(exec_ann["step_reconciliation"]) is dict
    assert exec_ann["budget_truncated"] is bool  # 裸 bool 无 origin
    assert typing.get_origin(exec_ann["metrics_groups"]) is dict
    assert typing.get_origin(exec_ann["degraded_credentials"]) is list
