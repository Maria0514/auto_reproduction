"""Sprint 6 批次 5 收口：批次 2 显式留到批次 5 的 3 个延迟检查点。

代码在批次 2 已实现（见 core/nodes/coding.py / core/nodes/execution.py），本文件只补
断言，不改生产代码、不改其它测试文件。三个 CP 均以"真验行为、不做假绿"为纪律：

- CP-2.2-3（AC-S6-07 修复轮次）：修复回合（fix_loop_count N>0）上下文仍含降级指令，
  且各轮（N=1/2/3）均可见——证明降级指令与 fix_round 分支解耦。
- CP-2.4-5（AC-S6-09）：characterization 翻转——NONE + 零指标 + exit0 经 _apply_no_metrics
  改判 NO_METRICS，落地 ExecutionResult 后 errors[0] 为 [error_category=no_metrics]，
  不再是 [error_category=none] 执行成功 的自相矛盾。四象限对照三种不翻转。
- CP-2.5-3（AC-S6-10 hint 面）：NO_METRICS 定向 hint 在 coding / execution / 面板三下游可见。

import 范式对齐同批次 tests/test_sprint6_b2.py（core.nodes.coding / core.nodes.execution
可直接 from ... import 函数级符号；模块属性访问才需 importlib，Known-bug §6）。
"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest


# ===========================================================================
# CP-2.2-3（AC-S6-07 修复轮次）：修复回合上下文仍含降级指令
# ===========================================================================


class TestCP223FixRoundStillHasDegradationDirective:
    """CP-2.2-3：修复回合（fix_loop_count N>0）_build_coding_context 仍注入降级指令，
    且指令注入与 fix_round 分支解耦（降级注入见 coding.py:316-321，与 fix_loop_count 无关；
    fix_round 注入见 coding.py:325-329）。各轮 N=1/2/3 均可见。
    """

    def _make_fix_round_state(self, fix_loop_count: int) -> Dict[str, Any]:
        """构造修复回合最小 state：降级非空 + execution_result 非空 + fix_loop_count>0。

        其余字段全走 _build_coding_context 内 .get() 兜底，无需补齐（构造最小面）。
        """
        return {
            "credential_degradations": {"env:OPENAI_API_KEY": "OpenAI API key"},
            "fix_loop_count": fix_loop_count,
            "execution_result": {
                "success": False,
                "errors": ["[error_category=runtime] boom"],
                "logs": "traceback ...",
                "metrics": {},
            },
        }

    def test_cp_2_2_3_fix_round_2_still_has_directive(self):
        """修复回合 N=2：payload 同时含降级指令 + fix_round=2（确证走了修复回合分支）。"""
        from core.nodes.coding import (
            _build_coding_context,
            _CREDENTIAL_DEGRADATIONS_DIRECTIVE,
        )

        state = self._make_fix_round_state(fix_loop_count=2)
        payload = _build_coding_context(state)

        # 降级指令：修复回合 N>0 仍注入（与首轮同源，非首轮专属）。
        assert "credential_degradations_directive" in payload
        assert (
            payload["credential_degradations_directive"]
            == _CREDENTIAL_DEGRADATIONS_DIRECTIVE
        )
        # 确证确实走了修复回合分支（非首轮）：fix_round 反映本轮回合数。
        assert payload["fix_round"] == 2
        # 修复回合裁剪反馈也应落地（与 fix_round 同分支，coding.py:328）。
        assert "last_error_summary" in payload

    def test_cp_2_2_3_directive_visible_each_round_1_2_3(self):
        """各轮可见：N=1/2/3 修复回合都注入降级指令，且 fix_round 逐轮反映真实回合数。"""
        from core.nodes.coding import (
            _build_coding_context,
            _CREDENTIAL_DEGRADATIONS_DIRECTIVE,
        )

        for n in (1, 2, 3):
            state = self._make_fix_round_state(fix_loop_count=n)
            payload = _build_coding_context(state)

            assert "credential_degradations_directive" in payload, (
                f"fix_loop_count={n} 修复回合应含降级指令"
            )
            assert (
                payload["credential_degradations_directive"]
                == _CREDENTIAL_DEGRADATIONS_DIRECTIVE
            ), f"fix_loop_count={n} 指令内容应为冻结常量"
            # fix_round 确证进入的是修复回合分支（fix_loop_count>0）且值即本轮回合数。
            assert payload["fix_round"] == n, (
                f"fix_loop_count={n} 时 fix_round 应为 {n}"
            )

    def test_cp_2_2_3_directive_semantics(self):
        """降级指令语义锚：注入的常量文案含模拟/mock 约束（与 CP-2.2-1 同源守门）。"""
        from core.nodes.coding import _build_coding_context

        state = self._make_fix_round_state(fix_loop_count=2)
        payload = _build_coding_context(state)

        directive = payload["credential_degradations_directive"]
        assert isinstance(directive, str) and directive
        assert "模拟/mock" in directive


# ===========================================================================
# CP-2.4-5（AC-S6-09）：characterization 翻转 NONE+零指标 → NO_METRICS
# ===========================================================================
#
# 既有老测试审计（不改老文件）：
#   - tests/test_sprint3_c3.py::test_build_execution_result_b_grade（L696-706）直接调
#     _build_execution_result(NONE feedback, metrics={}) 只断言 success is False，
#     不走 _apply_no_metrics、不断言 category/errors 文案 → 不受 batch2 改判影响，未挂。
#   - tests/test_sprint3_c3.py L269-271：_classify_execution 全 exit0 → NONE 是分类器
#     行为；_apply_no_metrics 是分类器之后的独立改判步，不改分类器语义 → 未挂。
#   结论：无老测试断言"NONE+零指标 exit0"的旧矛盾行为，无需改动老文件；新行为在本文
#   件内补新锚（characterization 用例锁定 batch2 改判后的语义）。


class TestCP245NoMetricsFlip:
    """CP-2.4-5：NONE + 零指标 + exit0 → NO_METRICS 翻转 characterization + 四象限对照。"""

    def _none_feedback(self):
        """旧 NONE feedback：summary 为矛盾文案 '执行成功'（改判前的自相矛盾态）。"""
        from core.nodes.execution import ErrorCategory, ExecutionFeedback

        return ExecutionFeedback(
            category=ErrorCategory.NONE,
            auto_fixable=False,
            summary="执行成功",
            fix_hint="",
            representative_stderr="",
        )

    def test_cp_2_4_5_flip_category_and_no_contradiction(self):
        """核心 characterization：NONE + exit_ok + metrics={} + metrics_groups={}
        → category 翻为 NO_METRICS，summary/fix_hint 含 '未产出指标'+'实验主入口' 语义，
        且不再出现 '执行成功' 式自相矛盾文案。
        """
        from core.nodes.execution import _apply_no_metrics, ErrorCategory

        fb = self._none_feedback()
        result = _apply_no_metrics(fb, {}, {}, exit_ok=True)

        # 1) 分类翻转。
        assert result.category == ErrorCategory.NO_METRICS
        assert result.category != ErrorCategory.NONE

        # 2) 语义锚：summary + fix_hint 都承载"未产出指标"+"实验主入口"。
        assert "未产出指标" in result.summary
        assert "实验主入口" in result.summary
        assert "未产出指标" in result.fix_hint
        assert "实验主入口" in result.fix_hint

        # 3) 不再自相矛盾：翻转后 summary/fix_hint 不含旧的 '执行成功' 文案。
        assert "执行成功" not in result.summary
        assert "执行成功" not in result.fix_hint

        # 4) 翻转后可自动修复（进 dev-loop 修复语义）。
        assert result.auto_fixable is True

    def test_cp_2_4_5_flipped_feedback_lands_no_metrics_errors0(self):
        """翻转后的 feedback 经 _build_execution_result 落地：errors[0] 为
        [error_category=no_metrics] ...，而非 [error_category=none] 执行成功。
        """
        from core.nodes.execution import _apply_no_metrics, _build_execution_result

        fb = self._none_feedback()
        flipped = _apply_no_metrics(fb, {}, {}, exit_ok=True)

        # NO_METRICS 落地：exit0 但 metrics={} → success=False → errors 非空。
        prep = _FakePrepareResult(success=True)
        runs = [_FakeRunResult(exit_code=0)]
        er = _build_execution_result(prep, runs, flipped, {}, "/tmp/s6_b5_no_metrics_wd")

        assert er["success"] is False, "零指标 → B 档 success False"
        assert len(er["errors"]) >= 1, "失败必有 errors 首条"
        errors0 = er["errors"][0]
        # 首条格式契约（execution.py:1737）：[error_category={category.value}] {summary}。
        assert errors0.startswith("[error_category=no_metrics]"), (
            f"errors[0] 应以 no_metrics 前缀开头，实际: {errors0!r}"
        )
        # 反锚：不是旧的 [error_category=none] 执行成功 自相矛盾。
        assert not errors0.startswith("[error_category=none]"), (
            f"errors[0] 不应是 none 前缀（旧矛盾态），实际: {errors0!r}"
        )
        assert "执行成功" not in errors0
        # 正锚：hint 语义仍在 errors[0] 内（面板/coding 下游数据源）。
        assert "未产出指标" in errors0

    # -------------------- 四象限对照：三种不翻转（保守） --------------------

    def test_cp_2_4_5_quadrant_exit_fail_no_flip(self):
        """exit_ok=False → 不翻转（即使 metrics/groups 全空，保守）。"""
        from core.nodes.execution import _apply_no_metrics, ErrorCategory

        fb = self._none_feedback()
        result = _apply_no_metrics(fb, {}, {}, exit_ok=False)
        assert result.category == ErrorCategory.NONE
        assert result is fb, "不满足条件应原样返回同一对象"

    def test_cp_2_4_5_quadrant_has_metrics_no_flip(self):
        """metrics 非空 → 不翻转（有真实指标即达 B 档，非无指标态）。"""
        from core.nodes.execution import _apply_no_metrics, ErrorCategory

        fb = self._none_feedback()
        result = _apply_no_metrics(fb, {"accuracy": 0.91}, {}, exit_ok=True)
        assert result.category == ErrorCategory.NONE
        assert result is fb

    def test_cp_2_4_5_quadrant_has_metrics_groups_no_flip(self):
        """metrics_groups 非空 → 不翻转（分组指标也算产出，保守不误判无指标）。"""
        from core.nodes.execution import _apply_no_metrics, ErrorCategory

        fb = self._none_feedback()
        result = _apply_no_metrics(fb, {}, {"table1": {"acc": 0.8}}, exit_ok=True)
        assert result.category == ErrorCategory.NONE
        assert result is fb


# ===========================================================================
# CP-2.5-3（AC-S6-10 hint 面）：定向 hint 三下游可见
# ===========================================================================


# NO_METRICS 定向 hint 原文（execution.py:1650-1653 _apply_no_metrics msg）。作为
# CP-2.5-3 三下游可见性验证的统一输入 errors[0]（模拟上一轮 execution 落盘结果）。
_NO_METRICS_HINT = (
    "代码跑通但未产出指标：全部命令 exit 0，但未发现 <METRICS> 输出或"
    " outputs/*/summary.json。请检查执行步骤是否调用了实验主入口，"
    "并按输出约定写出指标。"
)


class TestCP253HintVisibleThreeDownstreams:
    """CP-2.5-3：NO_METRICS 定向 hint 在 coding / execution / 面板三下游都可见。

    输入：一个 NO_METRICS 的上一轮 execution_result（errors[0] 带 no_metrics 前缀 + hint）。
    """

    def _no_metrics_exec_result(self) -> Dict[str, Any]:
        return {
            "success": False,
            "errors": [f"[error_category=no_metrics] {_NO_METRICS_HINT}"],
            "logs": "[step#0 exit=0]\n(no <METRICS> block emitted)",
            "metrics": {},
        }

    def test_cp_2_5_3_coding_downstream_digest_carries_hint(self):
        """下游 1（coding）：_digest_execution_feedback 裁剪后的 errors[0] 含 hint 语义，
        且解析出的 error_category == 'no_metrics'（驱动有针对性修复）。
        """
        from core.nodes.coding import _digest_execution_feedback

        digest = _digest_execution_feedback(self._no_metrics_exec_result())

        assert digest["error_category"] == "no_metrics", (
            f"应从 errors[0] 前缀解析出 no_metrics，实际: {digest['error_category']!r}"
        )
        assert digest["errors"], "裁剪反馈应保留 errors"
        errors0 = digest["errors"][0]
        assert "未产出指标" in errors0
        assert "实验主入口" in errors0

    def test_cp_2_5_3_execution_downstream_context_carries_hint(self):
        """下游 2（execution）：修复回合 _build_execution_agent_context 把上一轮 errors
        透传进 last_error_summary（execution.py:1113-1122），hint 语义可见。

        _build_execution_agent_context 不直接吃 errors，而是消费 state['execution_result']
        + fix_loop_count>0 → payload['last_error_summary']['errors']（agent 据此避坑）。
        """
        from core.nodes.execution import _build_execution_agent_context

        state = {
            "execution_result": self._no_metrics_exec_result(),
            "fix_loop_count": 1,  # 修复回合才注入 last_error_summary
        }
        plan = {"execution_steps": ["python train.py"], "environment": {}}
        payload = _build_execution_agent_context(state, "/tmp/s6_b5_exec_wd", plan)

        assert "last_error_summary" in payload, "修复回合应注入上一轮反馈"
        les = payload["last_error_summary"]
        assert isinstance(les.get("errors"), list) and les["errors"], (
            "last_error_summary.errors 应透传上一轮 errors"
        )
        joined = " ".join(les["errors"])
        assert "未产出指标" in joined
        assert "实验主入口" in joined
        # fix_round 确证走的是修复回合分支（非首轮，此时才透传反馈）。
        assert payload["fix_round"] == 1

    def test_cp_2_5_3_panel_downstream_datasource_carries_hint(self):
        """下游 3（面板）：面板 execution_errors 首条渲染由批次 3 测试覆盖——
        tests/test_sprint6_s6_01_execution_monitor.py::test_cp_3_3_6_dev_loop_panel_no_bare_error_category_label
        （MF-4）已验证 dev_loop 面板对 execution_errors 首条的裸标签消除渲染。

        本条只守面板的数据源链路：
          (a) _build_execution_result 产出的 ExecutionResult['errors'] 首条含 hint
              （错误数据的唯一权威来源，execution.py:1737）；
          (b) _build_dev_loop_interrupt_payload 的 'execution_errors' 首条（面板直接
              消费的 payload 字段，execution.py:1910）含 hint。
        两段闭合即证 hint 从 execution 结果流到面板数据源无丢失。
        """
        from core.nodes.execution import (
            _apply_no_metrics,
            _build_execution_result,
            _build_dev_loop_interrupt_payload,
            ErrorCategory,
            ExecutionFeedback,
        )

        # (a) 权威数据源：真跑一遍改判 + 落地，errors[0] 含 hint。
        none_fb = ExecutionFeedback(
            category=ErrorCategory.NONE,
            auto_fixable=False,
            summary="执行成功",
            fix_hint="",
            representative_stderr="",
        )
        flipped = _apply_no_metrics(none_fb, {}, {}, exit_ok=True)
        prep = _FakePrepareResult(success=True)
        runs = [_FakeRunResult(exit_code=0)]
        er = _build_execution_result(prep, runs, flipped, {}, "/tmp/s6_b5_panel_wd")

        assert er["errors"], "面板数据源 ExecutionResult.errors 应非空"
        assert "未产出指标" in er["errors"][0]
        assert "实验主入口" in er["errors"][0]

        # (b) 面板直接消费字段：payload['execution_errors'] 首条含 hint。
        state = {"fix_loop_count": 1, "fix_loop_history": []}
        payload = _build_dev_loop_interrupt_payload(er, flipped, state)

        assert payload["execution_errors"], "面板 payload.execution_errors 应非空"
        panel_errors0 = payload["execution_errors"][0]
        assert "未产出指标" in panel_errors0, (
            f"面板数据源 execution_errors[0] 应含 hint，实际: {panel_errors0!r}"
        )
        assert "实验主入口" in panel_errors0
        # error_category 面板字段仍标 no_metrics（term_map 渲染中文的键）。
        assert payload["error_category"] == "no_metrics"


# ===========================================================================
# 测试替身（对齐 tests/test_sprint3_c3.py 的 sandbox 结果替身范式）
# ===========================================================================


class _FakePrepareResult:
    """SandboxPrepareResult 最小替身：_build_execution_result 只读 success/env_info/install_log。"""

    def __init__(self, success: bool = True):
        self.success = success
        self.env_info: Dict[str, Any] = {}
        self.install_log = ""


class _FakeRunResult:
    """SandboxRunResult 最小替身：_build_execution_result / _effective_runs 读取的字段。"""

    def __init__(
        self,
        exit_code: int = 0,
        stdout: str = "",
        stderr: str = "",
        timed_out: bool = False,
        command: List[str] | None = None,
    ):
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr
        self.duration_seconds = 0.0
        self.timed_out = timed_out
        self.output_truncated = False
        self.command = command if command is not None else ["python", "train.py"]


if __name__ == "__main__":
    import sys

    sys.exit(pytest.main([__file__, "-v"]))
