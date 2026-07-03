"""Sprint 4 任务 A2 自测：core/state.py 新增 2 个用户交互通道字段（S4-10）。

覆盖 dev-plan sp4 §4 任务 A2 CP-A2-1 / CP-A2-2（CP-A2-3 全量回归由主控收口）。

参考实现：sp3 同款风格 tests/test_sprint3_a2.py（轻量结构性断言，无真实 LLM）。

约束（must-fix-1 + B2/B3 治理钉死）：
    - 绝不给 node_errors / degraded_nodes / fix_loop_history 加
      Annotated[List, operator.add]（CP-A2-2 grep 双证沿用 sp3 CP-A2-3 写法）；
    - 新增 2 字段均单值 / Dict 单点写（Optional[Dict] / Dict[str, str]），
      last-write-wins，无 reducer；
    - pending_user_input 绝不存答案、collected_inputs 绝不进敏感项
      （语义约束，由 sp4 后续任务的写入方测试保障；本文件只验通道声明与默认值）。
"""

from __future__ import annotations

import subprocess
import typing
from pathlib import Path

from core.state import (
    GlobalState,
    LLMConfig,
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


# ========== CP-A2-1：GlobalState 含两字段且类型正确 + 默认值正确 ==========


def test_cp_a2_1_globalstate_has_two_new_fields_with_correct_types() -> None:
    """CP-A2-1: GlobalState.__annotations__ 含 pending_user_input 与 collected_inputs，
    类型分别为 Optional[Dict] / Dict[str, str]。"""
    annotations = GlobalState.__annotations__

    assert "pending_user_input" in annotations, "GlobalState 应含 pending_user_input 字段"
    assert "collected_inputs" in annotations, "GlobalState 应含 collected_inputs 字段"

    # state.py 顶部无 `from __future__ import annotations`，故注解为真实类型对象。
    # Optional[Dict] 等价 Union[Dict, None]。
    assert annotations["pending_user_input"] == typing.Optional[typing.Dict], (
        f"pending_user_input 应为 Optional[Dict]，实测：{annotations['pending_user_input']}"
    )
    assert annotations["collected_inputs"] == typing.Dict[str, str], (
        f"collected_inputs 应为 Dict[str, str]，实测：{annotations['collected_inputs']}"
    )

    # 新字段自身也无 reducer（origin 为 Union / dict，而非 Annotated）
    assert typing.get_origin(annotations["collected_inputs"]) is dict


def test_cp_a2_1_create_initial_state_defaults_legacy_input() -> None:
    """CP-A2-1: 老形态 LLMConfig 入参下，pending_user_input=None / collected_inputs={}。"""
    state = create_initial_state("2103.00020", _CFG)

    assert state["pending_user_input"] is None, "pending_user_input 默认值应为 None"
    assert state["collected_inputs"] == {}, "collected_inputs 默认值应为空 dict"
    assert type(state["collected_inputs"]) is dict


def test_cp_a2_1_aux_legacy_and_set_inputs_both_get_defaults() -> None:
    """CP-A2-1 补：老形态 LLMConfig 与新形态 LLMConfigSet 入参均填充 sp4 默认值，
    且多次调用不共享同一 collected_inputs dict 实例（可变默认值别名防护）。"""
    # 老形态
    st1 = create_initial_state("2103.00020", _CFG)
    assert st1["pending_user_input"] is None
    assert st1["collected_inputs"] == {}

    # 新形态 LLMConfigSet
    st2 = create_initial_state(
        "2103.00020", {"default": _CFG, "overrides": {}}  # type: ignore[arg-type]
    )
    assert st2["pending_user_input"] is None
    assert st2["collected_inputs"] == {}

    # 两次调用的 collected_inputs 必须是独立 dict 实例
    assert st1["collected_inputs"] is not st2["collected_inputs"], (
        "collected_inputs 不得跨 create_initial_state 调用共享同一 dict 实例"
    )


def test_cp_a2_1_aux_existing_channels_unchanged() -> None:
    """CP-A2-1 补（零改动行）：dev-plan A2 列出的既有字段注解现状全部保留。"""
    ann = GlobalState.__annotations__

    assert ann["fix_loop_count"] is int
    assert ann["retry_budget_remaining"] is int
    assert ann["_dev_loop_route"] == typing.Optional[str]
    assert ann["_dev_loop_llm_calls"] is int
    # execution_result 仍为 Optional[ExecutionResult]（Union 且含 None）
    assert type(None) in typing.get_args(ann["execution_result"])

    # 既有默认值零漂移
    state = create_initial_state("2103.00020", _CFG)
    assert state["execution_result"] is None
    assert state["fix_loop_count"] == 0
    assert state["fix_loop_history"] == []
    assert state["node_errors"] == []
    assert state["degraded_nodes"] == []
    assert state["_dev_loop_route"] is None
    assert state["_dev_loop_llm_calls"] == 0


# ========== CP-A2-2：must-fix-1 grep 断言沿用（sp3 CP-A2-3 双证写法） ==========


def test_cp_a2_2_no_reducer_on_three_list_fields() -> None:
    """CP-A2-2（must-fix-1 沿用）: node_errors / degraded_nodes / fix_loop_history
    三字段绝不加 Annotated / operator.add，仍为普通 List。

    用 `grep -nE "Annotated|operator.add" core/state.py` 实证：
        - 若全文件零命中（Annotated / operator.add 都不存在），三字段必为普通 List；
        - 若文件存在命中行，则命中行不得落在三字段声明行上。
    """
    proc = subprocess.run(
        ["grep", "-nE", r"Annotated|operator\.add", str(STATE_PY)],
        capture_output=True,
        text=True,
    )
    # grep 无命中时 returncode=1 且 stdout 为空；有命中时 returncode=0
    matched_lines = proc.stdout.strip().splitlines()

    if matched_lines:
        # 若存在命中，断言命中行不涉及三个 List 字段（含 sp4 新增两字段一并保护）
        forbidden_fields = (
            "node_errors",
            "degraded_nodes",
            "fix_loop_history",
            "pending_user_input",
            "collected_inputs",
        )
        for line in matched_lines:
            for field in forbidden_fields:
                assert field not in line, (
                    f"must-fix-1 违规：{field} 出现在含 Annotated/operator.add 的行：{line}"
                )

    # 正向断言：三字段在 state.py 中以普通 `field: List[...]` 形态声明
    src = STATE_PY.read_text(encoding="utf-8")
    assert "node_errors: List[NodeError]" in src
    assert "degraded_nodes: List[str]" in src
    assert "fix_loop_history: List[FixLoopRecord]" in src


def test_cp_a2_2_aux_three_list_fields_annotation_is_plain_list() -> None:
    """CP-A2-2 补：从 __annotations__ 验证三字段为普通 List（非 Annotated）。

    typing.get_origin(Annotated[...]) 不会是 list；普通 List[X] 的 origin 是 list。
    """
    ann = GlobalState.__annotations__
    for field in ("node_errors", "degraded_nodes", "fix_loop_history"):
        origin = typing.get_origin(ann[field])
        assert origin is list, (
            f"{field} 应为普通 List（origin=list），实测 origin={origin}，注解={ann[field]}"
        )


# ========== 验收补强（test-engineer）：新通道经真实 LangGraph 图写入不被静默丢弃 ==========


def test_boundary_new_channels_writable_via_minimal_graph_not_silently_dropped() -> None:
    """边界补强（B2/B3 治理反向行为证，架构 sp4 §7.2 写法范例）：
    显式声明通道的存在理由 = 未声明的字段写入会被 LangGraph 静默丢弃。
    本用例用真实 StateGraph 实证两新通道可写、可跨节点读、last-write-wins：
        - node_a：写 pending_user_input 问题快照 + collected_inputs 收一条非敏感项；
        - node_b：断言读到 node_a 的写入 → resume 语义清 pending 为 None，
          read-modify-write 追加第二条非敏感项；
        - 终态：pending 已清 None；collected_inputs 恰 2 条无重复累加（无 reducer）。
    """
    from langgraph.graph import StateGraph, START, END

    snapshot = {
        "question": "私有源地址？", "is_sensitive": False, "purpose_key": "pip_index_url",
    }

    def node_a(state: GlobalState) -> dict:
        collected = dict(state["collected_inputs"])  # 单点 read-modify-write（§7.2 范式）
        collected["pip_index_url"] = "https://pypi.example/simple"
        return {"pending_user_input": dict(snapshot), "collected_inputs": collected}

    def node_b(state: GlobalState) -> dict:
        # 若通道未声明，node_a 的写入会被静默丢弃，这里读到的将是初始值
        assert state["pending_user_input"] == snapshot, (
            f"pending_user_input 写入被丢弃？实测 {state['pending_user_input']!r}"
        )
        assert state["collected_inputs"] == {"pip_index_url": "https://pypi.example/simple"}
        collected = dict(state["collected_inputs"])
        collected["dataset_name"] = "hotpotqa"
        return {"pending_user_input": None, "collected_inputs": collected}  # resume 后清 None

    builder = StateGraph(GlobalState)
    builder.add_node("node_a", node_a)
    builder.add_node("node_b", node_b)
    builder.add_edge(START, "node_a")
    builder.add_edge("node_a", "node_b")
    builder.add_edge("node_b", END)
    graph = builder.compile()

    final = graph.invoke(create_initial_state("2103.00020", _CFG))

    assert final["pending_user_input"] is None, "resume 后应清 None（last-write-wins）"
    assert final["collected_inputs"] == {
        "pip_index_url": "https://pypi.example/simple",
        "dataset_name": "hotpotqa",
    }, (
        f"collected_inputs 应恰 2 条（膨胀/重复表明误加 reducer，违反 must-fix-1）："
        f"实测 {final['collected_inputs']}"
    )
