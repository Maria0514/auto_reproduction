"""Sprint 3 任务 A2 自测：core/state.py 微增 2 个下划线内部字段（S3-09）。

覆盖 dev-plan §A2 CP-A2-1 ~ CP-A2-5（程序化可验证的 5 个 checkpoint）。

参考实现：sp2 同款风格 tests/test_sprint2_a2.py（轻量结构性断言，无真实 LLM）。

约束（must-fix-1 钉死）：
    - 绝不给 node_errors / degraded_nodes / fix_loop_history 加
      Annotated[List, operator.add]（AC-S3-05 ① 强制验收，CP-A2-3 grep 断言）；
    - 新增 2 字段均为单值（Optional[str] / int），last-write-wins，无 reducer；
    - 不动 FixLoopRecord（5 字段保持，CP-A2-4）。
"""

from __future__ import annotations

import subprocess
import typing
from pathlib import Path

from core.state import (
    FixLoopRecord,
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


# ========== CP-A2-1：GlobalState 含两字段且类型正确 ==========


def test_cp_a2_1_globalstate_has_two_new_fields_with_correct_types() -> None:
    """CP-A2-1: GlobalState.__annotations__ 含 _dev_loop_route 与 _dev_loop_llm_calls，
    类型分别为 Optional[str] / int。"""
    annotations = GlobalState.__annotations__

    assert "_dev_loop_route" in annotations, "GlobalState 应含 _dev_loop_route 字段"
    assert "_dev_loop_llm_calls" in annotations, "GlobalState 应含 _dev_loop_llm_calls 字段"

    # state.py 顶部无 `from __future__ import annotations`，故注解为真实类型对象。
    # Optional[str] 等价 Union[str, None]。
    assert annotations["_dev_loop_route"] == typing.Optional[str], (
        f"_dev_loop_route 应为 Optional[str]，实测：{annotations['_dev_loop_route']}"
    )
    assert annotations["_dev_loop_llm_calls"] is int, (
        f"_dev_loop_llm_calls 应为 int，实测：{annotations['_dev_loop_llm_calls']}"
    )


# ========== CP-A2-2：create_initial_state 默认值正确 ==========


def test_cp_a2_2_create_initial_state_defaults() -> None:
    """CP-A2-2: create_initial_state(...) 返回 _dev_loop_route is None 且
    _dev_loop_llm_calls == 0。"""
    state = create_initial_state("2103.00020", _CFG)

    assert state["_dev_loop_route"] is None, "_dev_loop_route 默认值应为 None"
    assert state["_dev_loop_llm_calls"] == 0, "_dev_loop_llm_calls 默认值应为 0"
    # 严格类型：int（非 bool）
    assert type(state["_dev_loop_llm_calls"]) is int


def test_cp_a2_2_aux_legacy_and_set_inputs_both_get_defaults() -> None:
    """CP-A2-2 补：老形态 LLMConfig 与新形态 LLMConfigSet 入参均填充 sp3 默认值。"""
    # 老形态
    st1 = create_initial_state("2103.00020", _CFG)
    assert st1["_dev_loop_route"] is None
    assert st1["_dev_loop_llm_calls"] == 0

    # 新形态 LLMConfigSet
    st2 = create_initial_state(
        "2103.00020", {"default": _CFG, "overrides": {}}  # type: ignore[arg-type]
    )
    assert st2["_dev_loop_route"] is None
    assert st2["_dev_loop_llm_calls"] == 0


# ========== CP-A2-3：must-fix-1 grep 断言（AC-S3-05 ① 强制验收点） ==========


def test_cp_a2_3_no_reducer_on_three_list_fields() -> None:
    """CP-A2-3（AC-S3-05 ① 强制验收点）: node_errors / degraded_nodes /
    fix_loop_history 三字段绝不加 Annotated / operator.add，仍为普通 List。

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

    # 全文件零命中是最干净的情况
    if not matched_lines:
        # 仍需正向确认三字段以普通 List 形态存在
        pass
    else:
        # 若存在命中，断言命中行不涉及三个 List 字段
        forbidden_fields = ("node_errors", "degraded_nodes", "fix_loop_history")
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


def test_cp_a2_3_aux_three_list_fields_annotation_is_plain_list() -> None:
    """CP-A2-3 补：从 __annotations__ 验证三字段为普通 List（非 Annotated）。

    typing.get_origin(Annotated[...]) 不会是 list；普通 List[X] 的 origin 是 list。
    """
    ann = GlobalState.__annotations__
    for field in ("node_errors", "degraded_nodes", "fix_loop_history"):
        origin = typing.get_origin(ann[field])
        assert origin is list, (
            f"{field} 应为普通 List（origin=list），实测 origin={origin}，注解={ann[field]}"
        )


# ========== CP-A2-4：FixLoopRecord 仍为 5 字段 ==========


def test_cp_a2_4_fixlooprecord_unchanged_five_fields() -> None:
    """CP-A2-4: FixLoopRecord.__annotations__ 仍为 5 字段，未追加 multi-agent 字段。"""
    expected = {
        "round_number",
        "error_summary",
        "error_category",
        "fix_strategy",
        "timestamp",
    }
    actual = set(FixLoopRecord.__annotations__.keys())
    assert actual == expected, (
        f"FixLoopRecord 应保持 5 字段 {expected}，实测：{actual}"
    )
    # 显式断言未引入 multi-agent 专属字段（顺延 sp4+）
    for forbidden in ("reviewer_verdict", "coder_confidence", "agent_trace"):
        assert forbidden not in actual, f"FixLoopRecord 不应含 multi-agent 字段 {forbidden}"


# ========== CP-A2-5：state.py git diff 纯追加（不破坏既有反序列化/初始化） ==========


def test_cp_a2_5_state_py_git_diff_is_pure_append() -> None:
    """CP-A2-5 旁证：state.py 改动为纯追加（0 删除行），保证不破坏既有
    GlobalState 反序列化 / create_initial_state 既有默认值。

    全量非 e2e 回归不退化由独立 pytest 运行验证（dev-plan CP-A2-5）；
    此处用 git diff 实证 state.py 未删改既有行。
    """
    proc = subprocess.run(
        ["git", "diff", "HEAD", "--", "core/state.py"],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        check=True,
    )
    diff = proc.stdout
    deletions = [
        line
        for line in diff.splitlines()
        if line.startswith("-") and not line.startswith("---")
    ]
    assert deletions == [], (
        f"core/state.py 必须为纯追加（0 删改），实测删除行：{deletions}"
    )
