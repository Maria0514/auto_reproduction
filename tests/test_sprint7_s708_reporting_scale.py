"""T-S7-5-9 自测：reporting 第 4 条标注 `scale_reduced` + 声明块第 4 段 + term_map +1 条。

覆盖 dev-plan §35 T-S7-5-9 的 CP-5.9-1 ~ CP-5.9-5（架构 sp7 §18.1.2 落点 7/9 + §18.7(6)）：

    - CP-5.9-1 `scale_reduced=True` → annotations **末尾**含 `"scale_reduced"`，既有三条
      顺序与取值一字不动；`level` **不得为 `"science"`**（即便 goal_checks 全"符合"）；
    - CP-5.9-2 **AC-S7-38 验红命门**：本文件里"缩规模不得评为科学复现"那条断言，正是
      删掉 `annotations.append("scale_reduced")` 映射后必须变红的对象（红/绿两态由
      开发时手工验红并落测试报告，见 dev-plan CP-5.9-2）；
    - CP-5.9-3 报告含"### 缩小规模复现"段与 `_SCALE_REDUCED_DECLARATION` 原文，且
      **三形态（full_success / code_only / degraded）均带**（Maria 裁决 8，§40 P-14）；
    - CP-5.9-4 **零扰动（§18.7(6)）**：缺键 / `False` / 字符串 `"false"` 三种取值下报告
      Markdown 与 sp5 基线**字节一致**；`_render_code_only` 源码零改动；
    - CP-5.9-5 `term_map` +1 条、`humanize` 命中、`len(TERM_LABELS) == 43`（供 T-5-11
      的 `EXPECTED_N` 对账）；`ui/pages/result_report.py` 零改动且结论卡片自动跟随降档。

全部离线纯函数直测，零 LLM、零网络。报告头部含 `datetime.now()`，字节比较类用例统一
冻结时间（`_frozen_now` fixture），使"字节一致"是真断言而非碰运气。
"""

from __future__ import annotations

import copy
import importlib
import inspect
from datetime import datetime
from typing import Any, Dict, List, Optional

import pytest

from core.state import ExecutionMode

# core/nodes/__init__.py 显式 export 同名 callable 会遮蔽子模块（sp1 已知坑 #6），
# 必须 importlib 取模块对象。
reporting_module = importlib.import_module("core.nodes.reporting")
_determine_conclusion = reporting_module._determine_conclusion
_render_annotation_notices = reporting_module._render_annotation_notices
_render_report = reporting_module._render_report
_SCALE_REDUCED_DECLARATION = reporting_module._SCALE_REDUCED_DECLARATION
_SCALE_REDUCED_NOTE_LEAD = reporting_module._SCALE_REDUCED_NOTE_LEAD

_SCALE_HEADING = "### 缩小规模复现"
_THREE_FORMS = ("full_success", "code_only", "degraded")

# 缺省语义三形态：缺键 ≡ False ≡ 字符串 "false"（后者是 `bool("false") is True` 陷阱，
# 反过来证明判定用的是 `is True` 而不是真值判断）。
_FALSY_VARIANTS = ("__missing__", False, "false")


# --------------------------------------------------------------------------- #
# fixtures / helpers
# --------------------------------------------------------------------------- #
@pytest.fixture()
def _frozen_now(monkeypatch: pytest.MonkeyPatch):
    """冻结 `_header` 里的 `datetime.now()`，使跨次渲染的字节比较确定可复现。"""

    class _FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> "datetime":  # noqa: D102 - 测试替身
            return datetime(2026, 7, 30, 12, 0, 0)

    monkeypatch.setattr(reporting_module, "datetime", _FrozenDateTime)
    return _FrozenDateTime


def _clean_exec_result(**overrides: Any) -> Dict[str, Any]:
    """全干净、success=True 的 ExecutionResult（无任何既有标注来源）。"""
    result: Dict[str, Any] = {
        "success": True,
        "metrics": {"pass_rate": 0.66},
        "logs": "",
        "errors": [],
        "artifacts": [],
        "runtime_seconds": 1.0,
        "environment_info": {},
        "step_reconciliation": {
            "planned": 2,
            "executed": 2,
            "completed": 2,
            "unexecuted_steps": [],
            "extra_commands": [],
            "attribution_unavailable": False,
        },
        "budget_truncated": False,
        "metrics_groups": {
            "main": {"pass_rate": 0.9},
            "baseline": {"pass_rate": 0.1},
        },
        "degraded_credentials": [],
    }
    result.update(overrides)
    return result


#: 对上面 metrics_groups 恒判"符合"的定性预期（goal_checks 全"符合"→ 具备 science 资格）。
_GOOD_EXPECTED: List[Dict[str, Any]] = [
    {
        "description": "main 组 pass_rate 应高于 baseline",
        "trend": {"metric": "pass_rate", "greater": "main", "lesser": "baseline"},
    },
]


def _plan(scale_reduced: Any = "__missing__", local_fit_note: Any = "__missing__") -> Dict[str, Any]:
    """构造 plan：``"__missing__"`` 表示该键**根本不存在**（旧 checkpoint 形态）。"""
    plan: Dict[str, Any] = {
        "plan_summary": "在本机复现主实验",
        "environment": {},
        "data_preparation": [],
        "code_strategy": "use_repo",
        "execution_steps": [],
        "expected_results": copy.deepcopy(_GOOD_EXPECTED),
        "estimated_time": "2h",
        "deliverables": ["train.py"],
        "user_feedback": None,
        "approved": True,
        "required_credentials": [],
    }
    if scale_reduced != "__missing__":
        plan["scale_reduced"] = scale_reduced
    if local_fit_note != "__missing__":
        plan["local_fit_note"] = local_fit_note
    return plan


def _state(
    *,
    plan: Optional[Dict[str, Any]] = None,
    exec_result: Optional[Dict[str, Any]] = None,
    simulation_notice: Optional[str] = None,
    execution_mode: Any = ExecutionMode.FULL,
) -> Dict[str, Any]:
    return {
        "execution_mode": execution_mode,
        "paper_meta": {"arxiv_id": "2604.01687", "title": "EvoSkills"},
        "reproduction_plan": _plan() if plan is None else plan,
        "execution_result": _clean_exec_result() if exec_result is None else exec_result,
        "simulation_notice": simulation_notice,
        "code_output_dir": "",
    }


# =========================================================================== #
# CP-5.9-1：第 4 条标注末尾追加 + 强制降档（AC-S7-38 判定侧）
# =========================================================================== #
def test_cp_5_9_1_scale_reduced_appended_as_fourth_annotation():
    """`scale_reduced=True` → annotations 恰含该值，且既有三条一条不多（干净 exec）。"""
    state = _state(plan=_plan(scale_reduced=True))
    out = _determine_conclusion(state, state["execution_result"], None)
    assert out["annotations"] == ["scale_reduced"]


def test_cp_5_9_1_scale_reduced_is_last_and_existing_three_order_intact():
    """四标注同时点火：既有三条**顺序与取值一字不动**，`scale_reduced` **在末尾**。"""
    exec_result = _clean_exec_result(
        degraded_credentials=["hf_token"],
        budget_truncated=True,
    )
    state = _state(
        plan=_plan(scale_reduced=True),
        exec_result=exec_result,
        simulation_notice="部分数据加载为占位实现",
    )
    out = _determine_conclusion(state, exec_result, None)
    assert out["annotations"] == [
        "simulation",
        "credential_degraded",
        "incomplete_execution",
        "scale_reduced",
    ]
    assert out["annotations"][-1] == "scale_reduced", "第 4 条必须末尾追加，不得插在中间"
    assert out["annotations"][:3] == [
        "simulation",
        "credential_degraded",
        "incomplete_execution",
    ]


def test_cp_5_9_1_scale_reduced_forbids_science_level():
    """**AC-S7-38 命门断言（CP-5.9-2 的验红对象）**：缩规模的复现强制不得评为科学复现。

    本用例刻意把除 `scale_reduced` 外的一切都调成"够格 science"：success=True、
    goal_checks 全"符合"、无 simulation / 无降级凭证 / 无缺步 / 无截断。**唯一**把
    档位从 science 打下来的就是第 4 条标注映射 —— 删掉
    ``annotations.append("scale_reduced")`` 后本条必须变红（dev-plan CP-5.9-2）。
    """
    state = _state(plan=_plan(scale_reduced=True))
    out = _determine_conclusion(state, state["execution_result"], None)

    # 前提自证：goal_checks 非空且全"符合"，success=True —— 即"若无第 4 条标注就该是
    # science"。这两行让本用例在断言变红时能一眼区分"前提坏了"还是"映射被删了"。
    assert out["goal_checks"], "前提：goal_checks 不应为空"
    assert all(c.get("verdict") == "符合" for c in out["goal_checks"]), "前提：应全符合"

    # 命门本体放最前，使验红时的失败信息直接读作"缩规模仍被评为科学复现"。
    assert out["level"] != "science", "缩规模复现强制不得评为科学复现（AC-S7-38）"
    assert out["level"] == "engineering"
    assert "scale_reduced" in out["annotations"]


def test_cp_5_9_1_baseline_without_scale_reduced_still_reaches_science():
    """对照组（证明上一条的降档确实来自第 4 条标注）：同样的 state 不带该键 → science。"""
    state = _state(plan=_plan())
    out = _determine_conclusion(state, state["execution_result"], None)
    assert out["annotations"] == []
    assert out["level"] == "science"


@pytest.mark.parametrize("value", _FALSY_VARIANTS)
def test_cp_5_9_1_falsy_variants_do_not_annotate(value: Any):
    """缺键 / False / 字符串 `"false"` 三形态均**不**点火（`is True` 而非真值判断）。"""
    state = _state(plan=_plan(scale_reduced=value))
    out = _determine_conclusion(state, state["execution_result"], None)
    assert out["annotations"] == []
    assert out["level"] == "science"


def test_cp_5_9_1_defensive_read_on_missing_or_malformed_plan():
    """旧 checkpoint 兼容：plan 缺失 / 非 dict 均不 KeyError、不造哨兵值。"""
    for bad_plan in (None, "not-a-dict", 42, []):
        state = _state()
        state["reproduction_plan"] = bad_plan
        out = _determine_conclusion(state, state["execution_result"], None)
        assert "scale_reduced" not in out["annotations"]


# =========================================================================== #
# CP-5.9-3：声明块第 4 段 + 三形态共用（Maria 裁决 8 / §40 P-14）
# =========================================================================== #
def test_cp_5_9_3_declaration_is_module_level_named_constant():
    """声明文案必须是模块级具名常量（T-5-11 按名 import），且非空、无内部字段名。"""
    assert isinstance(_SCALE_REDUCED_DECLARATION, str) and _SCALE_REDUCED_DECLARATION.strip()
    assert isinstance(_SCALE_REDUCED_NOTE_LEAD, str) and _SCALE_REDUCED_NOTE_LEAD.strip()
    for literal in (_SCALE_REDUCED_DECLARATION, _SCALE_REDUCED_NOTE_LEAD):
        for leaked in ("scale_reduced", "local_fit_note", "local_env_facts", "code_only"):
            assert leaked not in literal, f"用户可见文案禁裸露内部字段名：{leaked}"


def test_cp_5_9_3_declaration_states_not_evidence_for_original_scale():
    """文案要点：按本机规模缩小 + **不能作为论文原始规模实验结论的依据**。"""
    assert "缩小" in _SCALE_REDUCED_DECLARATION
    assert "不能" in _SCALE_REDUCED_DECLARATION
    assert "论文" in _SCALE_REDUCED_DECLARATION and "原始规模" in _SCALE_REDUCED_DECLARATION


@pytest.mark.parametrize("form", _THREE_FORMS)
def test_cp_5_9_3_all_three_forms_carry_declaration(form: str, _frozen_now):
    """三形态（full_success / code_only / degraded）报告**均带**该声明。

    声明块在 `_render_report` 中位于三形态分支**之前**、三形态共用 ⇒ code_only 路径
    自动覆盖（Maria 裁决 8），`_render_code_only` 零改动。
    """
    exec_result = _clean_exec_result(success=(form == "full_success"))
    state = _state(
        plan=_plan(scale_reduced=True),
        exec_result=None if form == "code_only" else exec_result,
        execution_mode=ExecutionMode.CODE_ONLY if form == "code_only" else ExecutionMode.FULL,
    )
    markdown = _render_report(state, form)
    assert _SCALE_HEADING in markdown
    assert _SCALE_REDUCED_DECLARATION in markdown
    # 只渲染一遍（防"按三形态各写一遍"造成重复渲染，§40 P-14）。
    assert markdown.count(_SCALE_HEADING) == 1
    assert markdown.count(_SCALE_REDUCED_DECLARATION) == 1


def test_cp_5_9_3_local_fit_note_rendered_verbatim_when_present():
    """`local_fit_note` 非空 → 原样附上（逐行引用，不改写不截断）。"""
    note = "这台机器只有 1 张显卡、显存 24GB。\n论文用 8 卡训练，本次改成单卡 + 数据取十分之一。"
    state = _state(plan=_plan(scale_reduced=True, local_fit_note=note))
    conclusion = _determine_conclusion(state, state["execution_result"], None)
    lines = _render_annotation_notices(state, conclusion, None)
    text = "\n".join(lines)
    assert _SCALE_REDUCED_NOTE_LEAD in text
    for raw in note.splitlines():
        assert f"> {raw}" in lines


@pytest.mark.parametrize("note", ["__missing__", "", "   "])
def test_cp_5_9_3_empty_local_fit_note_omits_lead(note: Any):
    """`local_fit_note` 缺键 / 空 / 纯空白 → 不渲染引出语（不留空引用块）。"""
    state = _state(plan=_plan(scale_reduced=True, local_fit_note=note))
    conclusion = _determine_conclusion(state, state["execution_result"], None)
    text = "\n".join(_render_annotation_notices(state, conclusion, None))
    assert _SCALE_HEADING in text
    assert _SCALE_REDUCED_NOTE_LEAD not in text


def test_cp_5_9_3_declaration_section_is_last_of_four():
    """四段同时渲染时，"缩小规模复现"段位于既有三段**之后**（末尾追加口径）。"""
    exec_result = _clean_exec_result(
        degraded_credentials=["hf_token"], budget_truncated=True
    )
    state = _state(
        plan=_plan(scale_reduced=True),
        exec_result=exec_result,
        simulation_notice="部分数据加载为占位实现",
    )
    conclusion = _determine_conclusion(state, exec_result, None)
    text = "\n".join(_render_annotation_notices(state, conclusion, None))
    order = [
        text.index("### 模拟/未验证内容"),
        text.index("### 凭证降级"),
        text.index("### 执行不完整"),
        text.index(_SCALE_HEADING),
    ]
    assert order == sorted(order)


# =========================================================================== #
# CP-5.9-4：零扰动正负两向（§18.7(6)）
# =========================================================================== #
@pytest.mark.parametrize("form", _THREE_FORMS)
@pytest.mark.parametrize("value", _FALSY_VARIANTS)
def test_cp_5_9_4_falsy_report_byte_identical_to_sp5_baseline(
    form: str, value: Any, _frozen_now
):
    """**零扰动**：缺键（= sp5 基线形态）/ `False` / `"false"` 三者报告 Markdown 字节一致。

    基线取"plan 里根本没有这两个新键"的渲染结果 —— 那正是 sp5 期间的 plan 形态。
    """
    def _render(scale_value: Any) -> str:
        exec_result = _clean_exec_result(success=(form == "full_success"))
        state = _state(
            plan=_plan(scale_reduced=scale_value, local_fit_note="本机足够，未缩规模"),
            exec_result=None if form == "code_only" else exec_result,
            execution_mode=(
                ExecutionMode.CODE_ONLY if form == "code_only" else ExecutionMode.FULL
            ),
        )
        return _render_report(state, form)

    baseline = _render("__missing__")
    actual = _render(value)
    assert actual.encode("utf-8") == baseline.encode("utf-8")
    assert _SCALE_HEADING not in baseline


@pytest.mark.parametrize("value", _FALSY_VARIANTS)
def test_cp_5_9_4_falsy_annotation_notices_stay_empty(value: Any):
    """负向：无其它标注时声明块早退返回 `[]`（零扰动的结构性保证）。"""
    state = _state(plan=_plan(scale_reduced=value, local_fit_note="任意说明"))
    conclusion = _determine_conclusion(state, state["execution_result"], None)
    assert conclusion["annotations"] == []
    assert _render_annotation_notices(state, conclusion, None) == []


def test_cp_5_9_4_positive_direction_true_changes_report():
    """正向：同一 state 仅把标记翻真 → 报告必须出现差异（防"改了等于没改"）。"""
    def _render(scale_value: Any) -> str:
        state = _state(plan=_plan(scale_reduced=scale_value))
        return _render_report(state, "full_success")

    assert _render(True) != _render(False)


def test_cp_5_9_4_render_code_only_untouched():
    """`_render_code_only` 零改动：其源码不含任何 S7-08 新增标识（声明由共用块承载）。"""
    src = inspect.getsource(reporting_module._render_code_only)
    assert "scale_reduced" not in src
    assert "local_fit_note" not in src
    assert "_SCALE_REDUCED" not in src


# =========================================================================== #
# CP-5.9-5：term_map +1 条 + UI 结论卡片零改动自动跟随降档
# =========================================================================== #
def test_cp_5_9_5_term_map_has_new_entry_and_expected_total():
    """term_map +1 条；总数 43，供 T-5-11 `EXPECTED_N` 对账。

    账目：S7-08 本任务加 `annotation:scale_reduced` ⇒ 41 + 1 = 42；
    S7-11 / T-S7-7-7 加 `error_category:incomplete_execution` ⇒ 43。
    """
    from ui.term_map import TERM_LABELS, humanize

    assert TERM_LABELS["annotation:scale_reduced"] == "缩小规模复现"
    assert humanize("annotation", "scale_reduced") == "缩小规模复现"
    assert len(TERM_LABELS) == 43, (
        "term_map 条数变了：T-5-11 的 EXPECTED_N 与本断言须同步更新"
    )


def test_cp_5_9_5_ui_conclusion_card_follows_downgrade_without_ui_change():
    """`ui/pages/result_report.py` 零改动：其卡片判定复用 `_determine_conclusion`，
    故 `scale_reduced=True` 时 full_success 卡片自动落到"工程复现"档。"""
    result_report = importlib.import_module("ui.pages.result_report")

    state_science = _state(plan=_plan())
    state_scaled = _state(plan=_plan(scale_reduced=True))
    assert result_report._conclusion_card_key("full_success", state_science) == (
        "full_success_science"
    )
    assert result_report._conclusion_card_key("full_success", state_scaled) == (
        "full_success_engineering"
    )
    # 该模块确实未被本任务改动：仍是从 reporting 直接 import 同一判定函数。
    assert result_report._determine_conclusion is _determine_conclusion
