"""S7-08 / T-S7-5-8：coding / execution 两侧 ``_SCALE_REDUCED_DIRECTIVE`` 下游贯穿。

对应 dev-plan §35 任务 T-S7-5-8 的 CP-5.8-1~4、架构 §18.1.2 落点 8 + §18.7(5)(6)、
PRD §10.7 AC-S7-39。

- CP-5.8-1 §18.7(5) 两侧**字节相等**断言（防日后单边改漂移）+ 非空 + 核心语义。
- CP-5.8-2 ``scale_reduced=True`` → 两侧 payload 均含 ``scale_reduced_directive``
  且值 ``is`` 各自模块常量。
- CP-5.8-3 §18.7(6) 零扰动**负向三形态**：``False`` / 键缺失（旧 checkpoint）/
  值为 ``"false"`` 字符串 → 两侧 payload 与基线**字节一致**、不含该键。
- CP-5.8-4 两侧 system prompt 字节零改动；``credential_degradations`` 既有注入
  路径行为不变。

**已知 bug 模式 #6**：``core/nodes/__init__.py`` 显式 export 会遮蔽同名子模块，
访问模块级私有属性一律走 ``importlib.import_module``，不得 ``from core.nodes import coding``。
"""

from __future__ import annotations

import importlib
import json
from typing import Any, Dict, Optional

coding_mod = importlib.import_module("core.nodes.coding")
execution_mod = importlib.import_module("core.nodes.execution")


# ---------------------------------------------------------------------------
# 公共夹具：与生产渲染完全一致的 HumanMessage 文本（react_base.py:851-859）
# ---------------------------------------------------------------------------


def _render_human_text(payload: Dict[str, Any]) -> str:
    """按 react_base wrapper 同款参数渲染 HumanMessage 文本（字节比对基准）。"""
    human_payload = {k: v for k, v in payload.items() if not k.startswith("_")}
    return json.dumps(human_payload, ensure_ascii=False, sort_keys=True, default=str)


_BASE_PLAN: Dict[str, Any] = {
    "code_strategy": "use_repo",
    "execution_steps": ["python train.py", "python eval.py"],
    "deliverables": ["metrics.json"],
    "environment": {"python": "3.10", "requirements": ["torch"]},
}


def _make_plan(scale_reduced: Any = "__absent__") -> Dict[str, Any]:
    """构造 plan：``__absent__`` 表示旧 checkpoint 形态（键根本不存在）。"""
    plan = dict(_BASE_PLAN)
    if scale_reduced != "__absent__":
        plan["scale_reduced"] = scale_reduced
    return plan


def _coding_state(plan: Dict[str, Any], degradations: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """coding 侧最小 state：code_output_dir 预置以避免 mkdir 副作用 + 保证字节确定。"""
    return {
        "reproduction_plan": plan,
        "resource_info": {"selected_repo": {"local_path": "/tmp/s708/repo"}},
        "paper_analysis": {
            "method_summary_en": "A method.",
            "datasets": ["CIFAR-10"],
            "framework": "pytorch",
            "hardware_requirements_en": "1 GPU",
        },
        "paper_meta": {"arxiv_id": "2403.06402"},
        "code_output_dir": "/tmp/s708/code",
        "credential_degradations": degradations or {},
        "execution_result": None,
        "fix_loop_count": 0,
    }


def _execution_state(plan: Dict[str, Any], degradations: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """execution 侧最小 state（生产路径上 state 的 plan 与入参 plan 恒为同一份）。"""
    return {
        "reproduction_plan": plan,
        "credential_degradations": degradations or {},
        "execution_result": None,
        "fix_loop_count": 0,
    }


def _coding_payload(plan: Dict[str, Any], degradations: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    return coding_mod._build_coding_context(_coding_state(plan, degradations))


def _execution_payload(plan: Dict[str, Any], degradations: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    return execution_mod._build_execution_agent_context(
        _execution_state(plan, degradations), "/tmp/s708/work", plan
    )


# ===========================================================================
# CP-5.8-1：§18.7(5) 两侧字节相等 + 非空 + 核心语义
# ===========================================================================


class TestCp581DirectiveByteEqual:
    def test_cp_5_8_1_two_sides_byte_identical(self):
        """两侧模块常量逐字节相同——单边改动即红（防漂移，§18.7(5)）。"""
        assert (
            coding_mod._SCALE_REDUCED_DIRECTIVE
            == execution_mod._SCALE_REDUCED_DIRECTIVE
        )
        assert (
            coding_mod._SCALE_REDUCED_DIRECTIVE.encode("utf-8")
            == execution_mod._SCALE_REDUCED_DIRECTIVE.encode("utf-8")
        )

    def test_cp_5_8_1_directive_nonempty_and_typed(self):
        """两常量均为非空 str（清空成 "" 不能蒙混）。"""
        for directive in (
            coding_mod._SCALE_REDUCED_DIRECTIVE,
            execution_mod._SCALE_REDUCED_DIRECTIVE,
        ):
            assert isinstance(directive, str)
            assert directive.strip()

    def test_cp_5_8_1_directive_carries_ac_s7_39_semantics(self):
        """AC-S7-39 的断言对象：规模参数是硬约束 + 不得按论文原始规模放大。"""
        directive = coding_mod._SCALE_REDUCED_DIRECTIVE
        assert "硬约束" in directive
        assert "不得按论文原始规模放大" in directive
        assert "缩小" in directive

    def test_cp_5_8_1_directive_no_placeholder_no_internal_field_names(self):
        """文案零插值（无 {}）且不泄漏内部字段名（术语纪律）。"""
        directive = coding_mod._SCALE_REDUCED_DIRECTIVE
        assert "{" not in directive and "}" not in directive
        for leaked in ("scale_reduced", "local_fit_note", "local_env_facts"):
            assert leaked not in directive


# ===========================================================================
# CP-5.8-2：正向——scale_reduced=True 时两侧注入，值 is 各自模块常量
# ===========================================================================


class TestCp582InjectedWhenTrue:
    def test_cp_5_8_2_coding_injects_directive(self):
        payload = _coding_payload(_make_plan(True))
        assert "scale_reduced_directive" in payload
        assert payload["scale_reduced_directive"] is coding_mod._SCALE_REDUCED_DIRECTIVE

    def test_cp_5_8_2_execution_injects_directive(self):
        payload = _execution_payload(_make_plan(True))
        assert "scale_reduced_directive" in payload
        assert (
            payload["scale_reduced_directive"] is execution_mod._SCALE_REDUCED_DIRECTIVE
        )

    def test_cp_5_8_2_directive_reaches_human_message_text(self):
        """指令确实进入 HumanMessage 渲染文本（走动态通道，非 system prompt）。"""
        for payload in (_coding_payload(_make_plan(True)), _execution_payload(_make_plan(True))):
            assert coding_mod._SCALE_REDUCED_DIRECTIVE in _render_human_text(payload)

    def test_cp_5_8_2_execution_source_is_plan_argument(self):
        """execution 侧读的是入参 plan（与 execution_steps / max_rounds 同源）。

        生产路径上 plan 恒等于 ``state["reproduction_plan"]``
        （execution.py 节点主体取 state → _run_execution_agent 透传），
        本用例只是把"单一 plan 来源"这一实现事实钉住，防日后两处来源分叉。
        """
        plan = _make_plan(True)
        payload = execution_mod._build_execution_agent_context(
            _execution_state(plan), "/tmp/s708/work", plan
        )
        assert "scale_reduced_directive" in payload


# ===========================================================================
# CP-5.8-3：§18.7(6) 零扰动负向三形态——与基线字节一致
# ===========================================================================


class TestCp583ZeroPerturbationNegative:
    """基线 = 旧 checkpoint 形态（plan 里根本没有 scale_reduced 键）。"""

    def test_cp_5_8_3_coding_three_forms_byte_identical_to_baseline(self):
        baseline = _render_human_text(_coding_payload(_make_plan()))
        for form in (False, "__absent__", "false"):
            plan = _make_plan() if form == "__absent__" else _make_plan(form)
            payload = _coding_payload(plan)
            assert "scale_reduced_directive" not in payload, form
            assert _render_human_text(payload) == baseline, form

    def test_cp_5_8_3_execution_three_forms_byte_identical_to_baseline(self):
        baseline = _render_human_text(_execution_payload(_make_plan()))
        for form in (False, "__absent__", "false"):
            plan = _make_plan() if form == "__absent__" else _make_plan(form)
            payload = _execution_payload(plan)
            assert "scale_reduced_directive" not in payload, form
            assert _render_human_text(payload) == baseline, form

    def test_cp_5_8_3_string_false_is_not_truthy_injected(self):
        """`bool("false") is True` 陷阱的下游对称面：`is True` 而非真值判断。"""
        assert bool("false") is True  # 陷阱本体成立，故必须用 is True
        assert "scale_reduced_directive" not in _coding_payload(_make_plan("false"))
        assert "scale_reduced_directive" not in _execution_payload(_make_plan("false"))

    def test_cp_5_8_3_truthy_non_true_values_do_not_inject(self):
        """其余真值（1 / "true" / 非空 dict）同样不注入——只认布尔真。"""
        for form in (1, "true", "是", {"x": 1}):
            assert "scale_reduced_directive" not in _coding_payload(_make_plan(form)), form
            assert "scale_reduced_directive" not in _execution_payload(_make_plan(form)), form

    def test_cp_5_8_3_none_and_missing_plan_do_not_raise(self):
        """旧 checkpoint 兼容：plan 缺失 / None 一律 .get() 防御读，不 KeyError。"""
        state = _coding_state(_make_plan())
        state["reproduction_plan"] = None
        payload = coding_mod._build_coding_context(state)
        assert "scale_reduced_directive" not in payload

        exec_payload = execution_mod._build_execution_agent_context(
            {"credential_degradations": {}, "fix_loop_count": 0, "execution_result": None},
            "/tmp/s708/work",
            None,
        )
        assert "scale_reduced_directive" not in exec_payload


# ===========================================================================
# CP-5.8-4：system prompt 零改动 + credential_degradations 既有路径行为不变
# ===========================================================================


class TestCp584SystemPromptAndCredentialPathIntact:
    def test_cp_5_8_4_directive_absent_from_system_prompts(self):
        """指令只走 HumanMessage 动态通道，绝不进 system prompt（Prompt Cache 无扰）。"""
        coding_sp = coding_mod._build_coding_system_prompt(_coding_payload(_make_plan(True)))
        exec_sp = execution_mod._build_execution_system_prompt()
        assert coding_mod._SCALE_REDUCED_DIRECTIVE not in coding_sp
        assert execution_mod._SCALE_REDUCED_DIRECTIVE not in exec_sp
        assert "scale_reduced" not in coding_sp
        assert "scale_reduced" not in exec_sp

    def test_cp_5_8_4_coding_system_prompt_byte_identical_across_flag(self):
        """coding system prompt 吃 context，仍须在标记真/假两态下字节一致。"""
        sp_true = coding_mod._build_coding_system_prompt(_coding_payload(_make_plan(True)))
        sp_false = coding_mod._build_coding_system_prompt(_coding_payload(_make_plan(False)))
        sp_absent = coding_mod._build_coding_system_prompt(_coding_payload(_make_plan()))
        assert sp_true == sp_false == sp_absent

    def test_cp_5_8_4_credential_path_unchanged_without_scale_flag(self):
        """既有降级注入路径行为不变（sp6 B2 口径）：有降级、无缩规模。"""
        degradations = {"env:HF_TOKEN": "拒绝"}
        for payload in (
            _coding_payload(_make_plan(), degradations),
            _execution_payload(_make_plan(), degradations),
        ):
            assert payload["credential_degradations"] == {"env:HF_TOKEN": "拒绝"}
            assert (
                payload["credential_degradations_directive"]
                == coding_mod._CREDENTIAL_DEGRADATIONS_DIRECTIVE
            )
            assert "scale_reduced_directive" not in payload

    def test_cp_5_8_4_two_directives_coexist_independently(self):
        """两条 directive 互不干扰：同时命中时两个键都在，且各自取值正确。"""
        degradations = {"env:HF_TOKEN": "拒绝"}
        for payload, mod in (
            (_coding_payload(_make_plan(True), degradations), coding_mod),
            (_execution_payload(_make_plan(True), degradations), execution_mod),
        ):
            assert payload["credential_degradations_directive"] is mod._CREDENTIAL_DEGRADATIONS_DIRECTIVE
            assert payload["scale_reduced_directive"] is mod._SCALE_REDUCED_DIRECTIVE

    def test_cp_5_8_4_credential_directive_two_sides_still_byte_equal(self):
        """sp6 既有"两侧字节相等"性质不被本次改动破坏（回归锚）。"""
        assert (
            coding_mod._CREDENTIAL_DEGRADATIONS_DIRECTIVE
            == execution_mod._CREDENTIAL_DEGRADATIONS_DIRECTIVE
        )
        assert (
            coding_mod._SCALE_REDUCED_DIRECTIVE
            != coding_mod._CREDENTIAL_DEGRADATIONS_DIRECTIVE
        )
