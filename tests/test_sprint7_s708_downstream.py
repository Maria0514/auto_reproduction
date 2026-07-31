"""S7-08 / T-S7-5-12 收口闸门（二）：**下游贯穿面** AC-S7-38 / AC-S7-39。

对应 dev-plan §35 任务 T-S7-5-12 的 CP-5.12-4 / CP-5.12-6，架构 sp7 §18.7(f)、
PRD §10.7 AC-S7-38 / AC-S7-39。

本文件与前序任务的分工（**不重复造轮子**）
=========================================
- T-5-8 已在 `tests/test_s708_scale_reduced_directive.py` 覆盖 coding / execution
  **各自**的零扰动；T-5-9 已在 `tests/test_sprint7_s708_reporting_scale.py` 覆盖
  reporting **自己**的零扰动。两者都是**单链路**断言。
- 本文件补的是 §18.7(f) 点名却没人做的那件事：**同一份 plan、同一个 state，
  三条链路一次断完**。分链路断言的共同盲区是"三条各自跟自己的基线比都没变，
  但三条之间口径已经分叉"（例如某条改用真值判断、某条仍用 `is True`）——
  一次断完才能让分叉当场暴露。
- CP-5.12-4 是 AC-S7-38 命门的**收口复现**：验红本身由 T-5-9 执行（13 failed，
  见测试报告），本文件承载那条"缩规模 → 不得评为科学复现"的常驻断言，
  它就是"去掉 `annotations.append("scale_reduced")` 映射"后必须变红的对象。

⚠ 已知 bug 模式 #6：访问 `core.nodes.*` 模块级私有属性一律 `importlib.import_module`。
"""

from __future__ import annotations

import importlib
import json
from datetime import datetime
from typing import Any, Dict, List

import pytest

from core.state import ExecutionMode

coding_mod = importlib.import_module("core.nodes.coding")
execution_mod = importlib.import_module("core.nodes.execution")
reporting_mod = importlib.import_module("core.nodes.reporting")


# --------------------------------------------------------------------------- #
# 公共夹具：一份 plan 同时喂三条链路（这正是"一次断完"的前提）
# --------------------------------------------------------------------------- #
_ABSENT = "__absent__"

#: 零扰动的三种"假"形态：缺键（旧 checkpoint）/ False / 字符串 "false"。
#: 第三种是 `bool("false") is True` 陷阱的守门——它同时证明三条链路用的是 `is True`。
_FALSY_FORMS = (_ABSENT, False, "false")


def _plan(scale_reduced: Any = _ABSENT, local_fit_note: Any = _ABSENT) -> Dict[str, Any]:
    """一份三条链路都吃得下的 plan；`_ABSENT` 表示该键**根本不存在**（旧存档形态）。"""
    plan: Dict[str, Any] = {
        "plan_summary": "在本机复现主实验",
        "environment": {"python": "3.11", "cuda": "12.1"},
        "data_preparation": ["下载数据集"],
        "code_strategy": "use_repo",
        "execution_steps": [
            {"step_name": "训练", "command": "python train.py", "expected_output": "ckpt"},
        ],
        "expected_results": [
            {
                "description": "main 组 pass_rate 应高于 baseline",
                "trend": {"metric": "pass_rate", "greater": "main", "lesser": "baseline"},
            },
        ],
        "estimated_time": "2h",
        "deliverables": ["train.py"],
        "user_feedback": None,
        "approved": True,
        "required_credentials": [],
    }
    if scale_reduced != _ABSENT:
        plan["scale_reduced"] = scale_reduced
    if local_fit_note != _ABSENT:
        plan["local_fit_note"] = local_fit_note
    return plan


def _exec_result() -> Dict[str, Any]:
    """全干净、success=True 的执行结果——**不带任何既有标注来源**。

    这样 `_determine_conclusion` 只剩 `scale_reduced` 一条可能的标注来源，
    "结论没到 science" 就只能归因于它（否则本条会被 simulation / 凭证降级
    等既有标注顺带带过，退化成假绿）。
    """
    return {
        "success": True,
        "metrics": {"pass_rate": 0.66},
        "logs": "",
        "errors": [],
        "artifacts": [],
        "runtime_seconds": 1.0,
        "environment_info": {},
        "step_reconciliation": {
            "planned": 1, "executed": 1, "completed": 1,
            "unexecuted_steps": [], "extra_commands": [],
            "attribution_unavailable": False,
        },
        "budget_truncated": False,
        "metrics_groups": {"main": {"pass_rate": 0.9}, "baseline": {"pass_rate": 0.1}},
        "degraded_credentials": [],
    }


def _state(plan: Dict[str, Any]) -> Dict[str, Any]:
    """同一份 state 同时喂 coding / execution / reporting 三条链路。"""
    return {
        "execution_mode": ExecutionMode.FULL,
        "paper_meta": {"arxiv_id": "2403.06402", "title": "A Heavy-Compute Paper"},
        "paper_analysis": {
            "method_summary_en": "A method.", "datasets": ["C4"],
            "framework": "pytorch", "hardware_requirements_en": "1 GPU",
        },
        "reproduction_plan": plan,
        "resource_info": {"selected_repo": {"local_path": "/tmp/s708/repo"}},
        "execution_result": _exec_result(),
        "simulation_notice": None,
        "code_output_dir": "/tmp/s708/code",
        "credential_degradations": {},
        "fix_loop_count": 0,
    }


def _render_human_text(payload: Dict[str, Any]) -> str:
    """按 react_base wrapper 同款参数渲染 HumanMessage 文本（字节比对基准）。"""
    human_payload = {k: v for k, v in payload.items() if not k.startswith("_")}
    return json.dumps(human_payload, ensure_ascii=False, sort_keys=True, default=str)


@pytest.fixture()
def _frozen_now(monkeypatch: pytest.MonkeyPatch):
    """冻结报告页眉的 `datetime.now()`，让"字节一致"是真断言而不是碰运气。"""

    class _FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> "datetime":  # noqa: D102 - 测试替身
            return datetime(2026, 7, 30, 12, 0, 0)

    monkeypatch.setattr(reporting_mod, "datetime", _FrozenDateTime)
    return _FrozenDateTime


def _three_chain_outputs(plan: Dict[str, Any]) -> Dict[str, str]:
    """把同一份 plan 同时压过三条链路，返回三份**可字节比较**的产物。

    - coding：`_build_coding_context` → HumanMessage 文本；
    - execution：`_build_execution_agent_context` → HumanMessage 文本；
    - reporting：`_determine_conclusion` + `_render_report` → 完整 Markdown。
    """
    state = _state(plan)
    coding_text = _render_human_text(coding_mod._build_coding_context(state))
    execution_text = _render_human_text(
        execution_mod._build_execution_agent_context(state, "/tmp/s708/work", plan)
    )
    conclusion = reporting_mod._determine_conclusion(state, state["execution_result"], None)
    report_text = reporting_mod._render_report(state, "full_success", conclusion, None)
    return {"coding": coding_text, "execution": execution_text, "reporting": report_text}


# =========================================================================== #
# CP-5.12-4：AC-S7-38 缩规模强制降档（**命门收口复现**）
# =========================================================================== #
def test_cp_5_12_4_ac_s7_38_scale_reduced_forbids_science_conclusion(_frozen_now) -> None:
    """**AC-S7-38 命门**：缩规模标记为真 → 结论档位**不得为科学复现**。

    ⚠ 验红对象（dev-plan CP-5.9-2 / CP-5.12-4）
    ==========================================
    去掉 `reporting._determine_conclusion` 里的 `annotations.append("scale_reduced")`
    映射 → **本断言必须变红**。验红已由 T-5-9 实跑执行（13 failed，报错直读），
    本条是收口复现，使该映射此后被误删时全量回归当场打红。

    本用例刻意用"全干净 + goal_checks 全符合"的执行结果——即**具备 science 资格**、
    只差 `scale_reduced` 这一条标注。故"没到 science"只能归因于缩规模标记，
    不会被别的既有标注顺带带过（那是本条最现实的假绿形态）。
    """
    baseline_conclusion = reporting_mod._determine_conclusion(
        _state(_plan()), _exec_result(), None
    )
    assert baseline_conclusion["level"] == "science", (
        "前提自证：不带缩规模标记时这份执行结果**确实够得上**科学复现——"
        "前提不成立的话，下面那条'没到 science'什么都证不了"
    )
    assert baseline_conclusion["annotations"] == [], "前提自证：基线零标注"

    state = _state(_plan(scale_reduced=True, local_fit_note="显存不够，换了更小的模型。"))
    conclusion = reporting_mod._determine_conclusion(state, state["execution_result"], None)

    assert "scale_reduced" in conclusion["annotations"], (
        "缩规模标记为真时必须产出 scale_reduced 标注——这正是强制降档的唯一通道"
        "（reporting.py 的 `and not annotations`）"
    )
    assert conclusion["level"] != "science", (
        "AC-S7-38 命门失守：计划自报已按本机缩过规模，结论却仍评为科学复现"
    )
    assert conclusion["level"] == "engineering", (
        f"缩规模应降到工程复现档，实得 {conclusion['level']}"
    )


def test_cp_5_12_4_ac_s7_38_report_carries_visible_chinese_declaration(_frozen_now) -> None:
    """AC-S7-38 后半：标记为真 → 报告含**显著中文声明** + 计划阶段的适配说明原文。"""
    note = "这台机器只有一张显卡，按论文原始规模跑不下，改用了更小的模型并只取了十分之一数据。"
    state = _state(_plan(scale_reduced=True, local_fit_note=note))
    conclusion = reporting_mod._determine_conclusion(state, state["execution_result"], None)
    markdown = reporting_mod._render_report(state, "full_success", conclusion, None)

    assert "### 缩小规模复现" in markdown
    assert reporting_mod._SCALE_REDUCED_DECLARATION in markdown
    assert reporting_mod._SCALE_REDUCED_NOTE_LEAD in markdown
    assert note in markdown, "计划阶段的适配说明原文必须原样出现在报告里"
    # 声明必须点明"不能用来支持或否定论文结论"这层要害（红线 6：缩规模必须诚实）
    assert "不能用来支持或否定" in reporting_mod._SCALE_REDUCED_DECLARATION


# =========================================================================== #
# CP-5.12-6（§18.7(f)）：三链路零扰动，**一次断完**，正负两向
# =========================================================================== #
@pytest.mark.parametrize("falsy", _FALSY_FORMS, ids=["missing_key", "False", "str_false"])
def test_cp_5_12_6_zero_perturbation_three_chains_at_once(falsy: Any, _frozen_now) -> None:
    """**§18.7(f) 负向**：`scale_reduced` 为假的三种形态下，
    coding / execution / reporting **三条链路的产物同时与 sp5 基线字节一致**。

    基线 = 旧 checkpoint 形态（plan 里根本没有这两个键）——即 S7-08 上线前那份产物。

    只断"真时有"而不断"假时零扰动"是 §18.7(f) 最常见的漏法（§37 纪律 7）：
    真时有内容很容易做到，假时悄悄多出一个空段落 / 多一个 payload 键则完全无感，
    它会让 Prompt Cache 前缀之外的 HumanMessage 字节持续抖动、也会让老报告
    平白多出空标题。
    """
    baseline = _three_chain_outputs(_plan())  # sp5 基线：两键皆缺
    plan = _plan() if falsy == _ABSENT else _plan(scale_reduced=falsy, local_fit_note="")
    actual = _three_chain_outputs(plan)

    for chain in ("coding", "execution", "reporting"):
        assert actual[chain] == baseline[chain], (
            f"{chain} 链路在 scale_reduced={falsy!r} 时与 sp5 基线字节不一致 —— 零扰动破了"
        )

    # 交叉自证：三条链路都不含缩规模相关的任何痕迹
    assert "scale_reduced_directive" not in actual["coding"]
    assert "scale_reduced_directive" not in actual["execution"]
    assert "缩小规模复现" not in actual["reporting"]


def test_cp_5_12_6_positive_direction_three_chains_at_once(_frozen_now) -> None:
    """**§18.7(f) 正向**：同一份 `scale_reduced=True` 的 plan，
    三条链路**同时**出现对应内容 —— 任一条漏接即红。

    与上面负向条合起来构成 §18.7(f) 的"正负两向"。这里刻意用**同一份 plan**
    压三条链路：分链路各测各的，挡不住"三条对 True 的判定口径已经分叉"
    （例如某条被改成真值判断、某条仍是 `is True`）。
    """
    note = "这台机器显存 24GB，按论文的 8 卡规模跑不动，改为单卡 + 更小的模型。"
    outputs = _three_chain_outputs(_plan(scale_reduced=True, local_fit_note=note))

    assert coding_mod._SCALE_REDUCED_DIRECTIVE in outputs["coding"], "coding 链路漏接"
    assert execution_mod._SCALE_REDUCED_DIRECTIVE in outputs["execution"], "execution 链路漏接"
    assert reporting_mod._SCALE_REDUCED_DECLARATION in outputs["reporting"], "reporting 链路漏接"
    assert note in outputs["reporting"], "适配说明原文未进报告"

    # 与负向基线确实不同（防"三条都没变"的空跑假绿）
    baseline = _three_chain_outputs(_plan())
    for chain in ("coding", "execution", "reporting"):
        assert outputs[chain] != baseline[chain], f"{chain} 链路在标记为真时竟与基线相同"


def test_cp_5_12_6_ac_s7_39_directive_byte_equal_across_two_sides() -> None:
    """AC-S7-39 / §18.7(e) 收口复核：两侧 `_SCALE_REDUCED_DIRECTIVE` **逐字节相同**。

    两侧各自持有一份模块常量（沿 sp6 `_CREDENTIAL_DEGRADATIONS_DIRECTIVE` 范式），
    单边改一个字就会让编码与执行拿到不同口径的硬约束、且无任何报错。
    T-5-8 已断过一次（110 字符逐字相等），此处收口复核并把"两侧同一句"
    与上面三链路正向断言绑在同一个文件里。
    """
    left = coding_mod._SCALE_REDUCED_DIRECTIVE
    right = execution_mod._SCALE_REDUCED_DIRECTIVE
    assert left == right, "coding / execution 两侧缩规模指令已漂移"
    assert isinstance(left, str) and left.strip(), "指令常量不得为空（清空即失去约束力）"
    assert "硬约束" in left and "不得按论文原始规模放大" in left, "指令核心语义缺失"


def test_cp_5_12_6_falsy_annotation_is_absent_not_merely_invisible(_frozen_now) -> None:
    """零扰动补强：假时不只是"报告里看不见"，`annotations` 里也**根本没有**这一条。

    "渲染层过滤掉了但判定层已经加进去"是本条的隐蔽失效形态——它会经
    `and not annotations` 通道**静默把结论从科学复现降档**，报告上却什么都不写。
    """
    for falsy in _FALSY_FORMS:
        plan = _plan() if falsy == _ABSENT else _plan(scale_reduced=falsy)
        conclusion = reporting_mod._determine_conclusion(_state(plan), _exec_result(), None)
        assert "scale_reduced" not in conclusion["annotations"], falsy
        assert conclusion["level"] == "science", (
            f"scale_reduced={falsy!r} 时结论被静默降档 —— 假时零扰动破了"
        )


def test_cp_5_12_6_existing_annotations_order_and_values_unchanged(_frozen_now) -> None:
    """零扰动补强：新标注是**末尾追加**，既有三条的顺序与取值一字不动。

    插在中间会让所有按下标读 annotations 的既有消费方（若有）静默错位；
    也会让 sp5 既有报告的段落顺序变化 —— 属"功能全对、产物字节全变"的退化。
    """
    state = _state(_plan(scale_reduced=True))
    state["simulation_notice"] = "本次含模拟内容"
    result = _exec_result()
    result["degraded_credentials"] = [{"purpose_key": "hf_token"}]
    result["budget_truncated"] = True

    annotations: List[str] = reporting_mod._determine_conclusion(state, result, None)["annotations"]
    assert annotations == [
        "simulation", "credential_degraded", "incomplete_execution", "scale_reduced",
    ], f"标注顺序已变（新条目必须末尾追加）：{annotations}"
