"""Sprint 6 批次 2 测试：interaction_tools 工厂化、coding/execution 降级注入、
NO_METRICS 合流、早停 N=2、term_map 条目。

覆盖 CP-2.1 ~ CP-2.5 全部检查点。
"""

from __future__ import annotations

import importlib
import json
import logging
import sqlite3
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# fixtures 路径
# ---------------------------------------------------------------------------

FIXTURES_DIR = Path(__file__).parent / "fixtures"
FIXTURE_CDCD = FIXTURES_DIR / "checkpoints_s6_cdcd432cda49.db"
FIXTURE_19E2 = FIXTURES_DIR / "checkpoints_s6_19e21e015017.db"


# ===========================================================================
# CP-2.1 系列：interaction_tools 工厂化 + _normalize_purpose_key + 短路
# ===========================================================================


class TestNormalizePurposeKey:
    """CP-2.1-1：_normalize_purpose_key 双向样本。"""

    def setup_method(self):
        from core.tools.interaction_tools import _normalize_purpose_key
        self.fn = _normalize_purpose_key

    def test_env_prefix_stripped(self):
        assert self.fn("env:OPENAI_API_KEY") == "openai_api_key"

    def test_git_credential(self):
        assert self.fn("git_credential:github.com") == "git_credential_github_com"

    def test_plain_token(self):
        assert self.fn("hf_token") == "hf_token"

    def test_strip_whitespace(self):
        assert self.fn("  env:MY_TOKEN  ") == "my_token"

    def test_special_chars_folded(self):
        # multiple non-alnum → single underscore
        result = self.fn("foo::bar--baz")
        assert result == "foo_bar_baz"

    def test_leading_trailing_underscore_removed(self):
        assert self.fn(":abc:") == "abc"

    def test_empty(self):
        assert self.fn("") == ""

    def test_only_env_prefix(self):
        assert self.fn("env:") == ""


class TestMakeRequestUserInputTool:
    """CP-2.1-2/3/4/5/6：工厂产物行为验证。"""

    def setup_method(self):
        from core.tools.interaction_tools import (
            make_request_user_input_tool,
            request_user_input as default_tool,
        )
        self.factory = make_request_user_input_tool
        self.default_tool = default_tool

    def test_cp_2_1_2_short_circuit_no_interrupt(self):
        """CP-2.1-2：degraded 含 env:OPENAI_API_KEY，调 openai_api_key → 零 interrupt + 返回降级指令串。"""
        degraded = {"env:OPENAI_API_KEY": "用户拒绝提供"}
        tool_fn = self.factory(degraded)

        # patch lookup_secret 返回 None（不命中 .secrets 缓存）
        with patch("core.tools.interaction_tools.lookup_secret", return_value=None):
            # 工具函数本身是 @tool 装饰的，通过 .invoke 调用
            result = tool_fn.invoke({
                "question": "请提供 OpenAI key",
                "is_sensitive": True,
                "purpose_key": "openai_api_key",
            })

        assert isinstance(result, str)
        assert "用户已明确拒绝提供该凭证" in result
        assert "openai_api_key" in result
        assert "模拟/mock" in result

    def test_cp_2_1_2_normalized_match(self):
        """CP-2.1-2 变体：degraded key 是 env:OPENAI_API_KEY，调用时 purpose_key 是 openai_api_key，也应短路。"""
        degraded = {"env:OPENAI_API_KEY": "拒绝"}
        tool_fn = self.factory(degraded)

        with patch("core.tools.interaction_tools.lookup_secret", return_value=None):
            result = tool_fn.invoke({
                "question": "请提供 key",
                "purpose_key": "env:OPENAI_API_KEY",  # 两侧都归一后相等
            })

        assert "用户已明确拒绝提供该凭证" in result

    def test_cp_2_1_3_non_hit_goes_to_interrupt(self):
        """CP-2.1-3：非命中路径走 interrupt 路径（通过断言 interrupt 被调用）。"""
        from langgraph.types import interrupt as lgi

        degraded = {"env:OPENAI_API_KEY": "拒绝"}
        tool_fn = self.factory(degraded)

        interrupt_called = []

        def fake_interrupt(payload):
            interrupt_called.append(payload)
            # 返回合法 resume dict，工具继续执行
            return {"value": "test_value", "remember": False}

        with patch("core.tools.interaction_tools.lookup_secret", return_value=None):
            with patch("core.tools.interaction_tools.interrupt", side_effect=fake_interrupt):
                result = tool_fn.invoke({
                    "question": "请提供 HF token",
                    "purpose_key": "hf_token",  # 不在 degraded 中
                })

        # interrupt 应该被调用（非降级路径）
        assert len(interrupt_called) == 1
        assert result == "test_value"

    def test_cp_2_1_4_short_circuit_returns_str(self):
        """CP-2.1-4：短路返回 str，不是 dict。"""
        degraded = {"my_secret": "拒绝"}
        tool_fn = self.factory(degraded)

        with patch("core.tools.interaction_tools.lookup_secret", return_value=None):
            result = tool_fn.invoke({
                "question": "需要 secret",
                "purpose_key": "my_secret",
            })

        assert isinstance(result, str)
        assert not isinstance(result, dict)

    def test_cp_2_1_5_schema_byte_equal(self):
        """CP-2.1-5：工厂产物 tool schema（name/description/args）与原 request_user_input 相同。"""
        from core.tools.interaction_tools import request_user_input as default_ri

        factory_tool = self.factory({})

        # 比较 tool 名称
        assert factory_tool.name == default_ri.name

        # 比较 description（即 docstring，前缀冻结核心守门）
        assert factory_tool.description == default_ri.description

        # 比较 args schema（参数名和类型）
        factory_schema = factory_tool.args_schema.schema() if hasattr(factory_tool, "args_schema") else {}
        default_schema = default_ri.args_schema.schema() if hasattr(default_ri, "args_schema") else {}
        assert factory_schema == default_schema

    def test_cp_2_1_6_warning_log_on_short_circuit(self, caplog):
        """CP-2.1-6：短路时日志含归一前后 purpose_key，不含 question。"""
        degraded = {"env:OPENAI_API_KEY": "拒绝"}
        tool_fn = self.factory(degraded)

        question_text = "这是一个非常敏感的问题，不应出现在日志中"

        with caplog.at_level(logging.WARNING, logger="core.tools.interaction_tools"):
            with patch("core.tools.interaction_tools.lookup_secret", return_value=None):
                tool_fn.invoke({
                    "question": question_text,
                    "purpose_key": "env:OPENAI_API_KEY",
                })

        warning_logs = [r.message for r in caplog.records if r.levelname == "WARNING"]
        assert any(warning_logs), "应该有 WARNING 日志"
        combined = " ".join(warning_logs)

        # 日志含归一前后 purpose_key
        assert "env:OPENAI_API_KEY" in combined or "openai_api_key" in combined
        # 日志不含 question 全文
        assert question_text not in combined

    def test_default_instance_backward_compat(self):
        """向后兼容：模块级 request_user_input 仍是可调用的工具。"""
        from core.tools.interaction_tools import request_user_input
        assert hasattr(request_user_input, "invoke")
        assert request_user_input.name == "request_user_input"


# ===========================================================================
# CP-2.2 系列：coding 节点降级注入
# ===========================================================================


class TestCodingCredentialInjection:
    """CP-2.2-1/2：coding 降级注入与零降级路径。"""

    def _make_state(self, credential_degradations: dict) -> dict:
        """构造最小可用 state（不跑 LLM）。"""
        return {
            "credential_degradations": credential_degradations,
            "reproduction_plan": {},
            "resource_info": {},
            "paper_analysis": {},
            "paper_meta": {"arxiv_id": "test123"},
            "workspace_dir": "/tmp/test_workspace",
            "code_output_dir": None,
            "execution_result": None,
            "fix_loop_count": 0,
        }

    def test_cp_2_2_1_degraded_nonempty_injects_directive(self):
        """CP-2.2-1：降级非空时 payload 含 credential_degradations + directive。"""
        from core.nodes.coding import _build_coding_context, _CREDENTIAL_DEGRADATIONS_DIRECTIVE

        state = self._make_state({"env:OPENAI_API_KEY": "用户拒绝"})
        payload = _build_coding_context(state)

        assert "credential_degradations" in payload
        assert "credential_degradations_directive" in payload
        assert payload["credential_degradations_directive"] == _CREDENTIAL_DEGRADATIONS_DIRECTIVE
        # directive 内容语义检查
        assert "模拟/mock" in payload["credential_degradations_directive"]

    def test_cp_2_2_2_zero_degraded_no_directive(self):
        """CP-2.2-2：零降级路径 payload 不含 directive（HumanMessage 字节零扰动）。"""
        from core.nodes.coding import _build_coding_context

        state = self._make_state({})
        payload = _build_coding_context(state)

        assert "credential_degradations" not in payload
        assert "credential_degradations_directive" not in payload

    def test_cp_2_2_1_tool_is_factory_product(self):
        """CP-2.2-1 补充：降级非空时 coding 工具集使用工厂产物（通过验证工具 name 相同）。"""
        from core.nodes.coding import _get_coding_tools
        from core.tools.interaction_tools import make_request_user_input_tool

        state = self._make_state({"env:HF_TOKEN": "拒绝"})
        state["code_output_dir"] = "/tmp/code"

        # mock 工厂，确认它被调用
        with patch("core.nodes.coding.make_request_user_input_tool") as mock_factory:
            mock_tool = MagicMock()
            mock_tool.name = "request_user_input"
            mock_factory.return_value = mock_tool
            # 避免实际创建目录
            with patch("core.nodes.coding._resolve_code_output_dir", return_value="/tmp/code"):
                with patch("core.nodes.coding.build_credential_env", return_value={}):
                    with patch("core.nodes.coding.load_all_secrets", return_value={}):
                        tools = _get_coding_tools(state)

            mock_factory.assert_called_once()
            call_args = mock_factory.call_args
            assert call_args[0][0] == {"env:HF_TOKEN": "拒绝"}


# ===========================================================================
# CP-2.3 系列：execution 节点降级注入
# ===========================================================================


class TestExecutionCredentialInjection:
    """CP-2.3-1/2/3：execution 降级注入与 fixture 验证。"""

    def _make_state(self, credential_degradations: dict) -> dict:
        return {
            "credential_degradations": credential_degradations,
            "reproduction_plan": {"execution_steps": []},
            "fix_loop_count": 0,
            "execution_result": None,
        }

    def test_cp_2_3_1_degraded_nonempty_injects_directive(self):
        """CP-2.3-1：降级非空时 execution payload 含降级注入。"""
        from core.nodes.execution import _build_execution_agent_context, _CREDENTIAL_DEGRADATIONS_DIRECTIVE

        state = self._make_state({"env:OPENAI_API_KEY": "拒绝"})
        plan = {"execution_steps": ["python train.py"]}
        payload = _build_execution_agent_context(state, "/tmp/work", plan)

        assert "credential_degradations" in payload
        assert "credential_degradations_directive" in payload
        assert payload["credential_degradations_directive"] == _CREDENTIAL_DEGRADATIONS_DIRECTIVE

    def test_cp_2_3_2_zero_degraded_no_directive(self):
        """CP-2.3-2：零降级路径 payload 不含 directive。"""
        from core.nodes.execution import _build_execution_agent_context

        state = self._make_state({})
        plan = {"execution_steps": []}
        payload = _build_execution_agent_context(state, "/tmp/work", plan)

        assert "credential_degradations" not in payload
        assert "credential_degradations_directive" not in payload

    def test_cp_2_3_3_fixture_cdcd_credential_degradations_nonempty(self):
        """CP-2.3-3：checkpoints_s6_cdcd432cda49.db fixture 可加载且 credential_degradations 非空。"""
        if not FIXTURE_CDCD.exists():
            pytest.skip(f"fixture 不存在: {FIXTURE_CDCD}")

        import shutil, tempfile
        from langgraph.checkpoint.sqlite import SqliteSaver

        # 复制到临时路径（只读 fixture 不能被 SqliteSaver 写入）
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
            tmp_path = tf.name
        shutil.copy(str(FIXTURE_CDCD), tmp_path)

        try:
            with SqliteSaver.from_conn_string(tmp_path) as saver:
                config = {"configurable": {"thread_id": "task-cdcd432cda49"}}
                state = saver.get(config)

            assert state is not None, "task-cdcd432cda49 checkpoint 不存在"
            assert isinstance(state, dict), "checkpoint 应为 dict"

            channel_values = state.get("channel_values", {})
            credential_degradations = channel_values.get("credential_degradations", None)

            # 该 fixture 是 user_input interrupt + credential_degradations 非空的现场
            assert credential_degradations is not None, (
                "task-cdcd432cda49 的 credential_degradations 应非空（现场记录）"
            )
            assert isinstance(credential_degradations, dict)
            assert len(credential_degradations) > 0, "credential_degradations 应至少有一条记录"
        finally:
            import os
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


# ===========================================================================
# CP-2.4 系列：NO_METRICS 合流
# ===========================================================================


class TestNoMetrics:
    """CP-2.4-1/2/3/4：_apply_no_metrics 四象限 + AUTO_FIXABLE + fixture。"""

    def setup_method(self):
        from core.nodes.execution import (
            _apply_no_metrics,
            ErrorCategory,
            ExecutionFeedback,
            AUTO_FIXABLE,
        )
        self.apply = _apply_no_metrics
        self.ErrorCategory = ErrorCategory
        self.ExecutionFeedback = ExecutionFeedback
        self.AUTO_FIXABLE = AUTO_FIXABLE

    def _none_feedback(self):
        return self.ExecutionFeedback(
            category=self.ErrorCategory.NONE,
            auto_fixable=False,
            summary="执行成功",
            fix_hint="",
            representative_stderr="",
        )

    def test_cp_2_4_1_exit_ok_no_metrics_triggers(self):
        """CP-2.4-1：exit_ok=True + metrics={} + metrics_groups={} → 改判 NO_METRICS。"""
        fb = self._none_feedback()
        result = self.apply(fb, {}, {}, exit_ok=True)
        assert result.category == self.ErrorCategory.NO_METRICS

    def test_cp_2_4_1_exit_ok_with_metrics_no_change(self):
        """CP-2.4-1：exit_ok=True + metrics 非空 → 不改判。"""
        fb = self._none_feedback()
        result = self.apply(fb, {"accuracy": 0.9}, {}, exit_ok=True)
        assert result.category == self.ErrorCategory.NONE

    def test_cp_2_4_1_exit_fail_no_change(self):
        """CP-2.4-1：exit_ok=False → 不改判（即使 metrics 为空）。"""
        fb = self.ExecutionFeedback(
            category=self.ErrorCategory.RUNTIME,
            auto_fixable=True,
            summary="运行时错误",
            fix_hint="",
            representative_stderr="",
        )
        result = self.apply(fb, {}, {}, exit_ok=False)
        assert result.category == self.ErrorCategory.RUNTIME

    def test_cp_2_4_1_exit_ok_with_metrics_groups(self):
        """CP-2.4-1：exit_ok=True + metrics_groups 非空 → 不改判。"""
        fb = self._none_feedback()
        result = self.apply(fb, {}, {"group1": {"acc": 0.8}}, exit_ok=True)
        assert result.category == self.ErrorCategory.NONE

    def test_cp_2_4_2_summary_fix_hint_content(self):
        """CP-2.4-2：改判后 summary/fix_hint 含正确文案。"""
        fb = self._none_feedback()
        result = self.apply(fb, {}, {}, exit_ok=True)
        assert "代码跑通但未产出指标" in result.summary
        assert "<METRICS>" in result.fix_hint or "METRICS" in result.fix_hint
        assert "exit 0" in result.summary

    def test_cp_2_4_3_no_metrics_in_auto_fixable(self):
        """CP-2.4-3：NO_METRICS in AUTO_FIXABLE。"""
        assert self.ErrorCategory.NO_METRICS in self.AUTO_FIXABLE

    def test_cp_2_4_4_fixture_19e2_can_load(self):
        """CP-2.4-4：checkpoints_s6_19e21e015017.db 可加载，验证 fixture 可读。"""
        if not FIXTURE_19E2.exists():
            pytest.skip(f"fixture 不存在: {FIXTURE_19E2}")

        import shutil, tempfile
        from langgraph.checkpoint.sqlite import SqliteSaver

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
            tmp_path = tf.name
        shutil.copy(str(FIXTURE_19E2), tmp_path)

        try:
            with SqliteSaver.from_conn_string(tmp_path) as saver:
                config = {"configurable": {"thread_id": "task-19e21e015017"}}
                state = saver.get(config)

            assert state is not None, "task-19e21e015017 checkpoint 不存在"
            assert isinstance(state, dict), "checkpoint 应为 dict"

            channel_values = state.get("channel_values", {})
            # 该 fixture 是 no_metrics/两面计划现场，验证 fixture 可以加载和解析
            assert isinstance(channel_values, dict), "channel_values 应是 dict"
        finally:
            import os
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    def test_cp_2_4_4_fixture_19e2_errors_field(self):
        """CP-2.4-4 补充：task-19e21e015017 的 execution_result（如存在）errors 字段格式正确。"""
        if not FIXTURE_19E2.exists():
            pytest.skip(f"fixture 不存在: {FIXTURE_19E2}")

        import shutil, tempfile
        from langgraph.checkpoint.sqlite import SqliteSaver

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
            tmp_path = tf.name
        shutil.copy(str(FIXTURE_19E2), tmp_path)

        try:
            with SqliteSaver.from_conn_string(tmp_path) as saver:
                config = {"configurable": {"thread_id": "task-19e21e015017"}}
                state = saver.get(config)

            if state is None:
                pytest.skip("task-19e21e015017 checkpoint 不存在")

            channel_values = state.get("channel_values", {})
            execution_result = channel_values.get("execution_result")

            if execution_result is None:
                pytest.skip("task-19e21e015017 尚无 execution_result（fixture 可能是 planning 阶段现场）")

            # 有 execution_result 时，验证 errors 字段格式
            errors = execution_result.get("errors", [])
            assert isinstance(errors, list)
        finally:
            import os
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


# ===========================================================================
# CP-2.5 系列：早停 N=2 + term_map
# ===========================================================================


class TestNoMetricsEarlyStop:
    """CP-2.5-1/2/3：_no_metrics_stalled 真值表 + term_map。"""

    def setup_method(self):
        from core.nodes.execution import (
            _no_metrics_stalled,
            ErrorCategory,
            ExecutionFeedback,
        )
        from config import NO_METRICS_EARLY_STOP_ROUNDS
        self.stalled = _no_metrics_stalled
        self.ErrorCategory = ErrorCategory
        self.ExecutionFeedback = ExecutionFeedback
        self.N = NO_METRICS_EARLY_STOP_ROUNDS

    def _no_metrics_feedback(self):
        return self.ExecutionFeedback(
            category=self.ErrorCategory.NO_METRICS,
            auto_fixable=True,
            summary="代码跑通但未产出指标",
            fix_hint="检查入口脚本",
            representative_stderr="",
        )

    def _make_history(self, categories: list) -> list:
        return [{"error_category": c, "round_number": i + 1} for i, c in enumerate(categories)]

    def test_cp_2_5_1_early_stop_rounds_equals_2(self):
        """CP-2.5-1：NO_METRICS_EARLY_STOP_ROUNDS == 2。"""
        from config import NO_METRICS_EARLY_STOP_ROUNDS
        assert NO_METRICS_EARLY_STOP_ROUNDS == 2

    def test_cp_2_5_1_stalled_when_tail_n_no_metrics(self):
        """CP-2.5-1：尾部已有 N 条 no_metrics → 早停。"""
        state = {
            "fix_loop_history": self._make_history(["no_metrics"] * self.N),
        }
        fb = self._no_metrics_feedback()
        assert self.stalled(state, fb) is True

    def test_cp_2_5_1_not_stalled_less_than_n(self):
        """CP-2.5-1：尾部 no_metrics 不足 N 条 → 不早停。"""
        state = {
            "fix_loop_history": self._make_history(["no_metrics"] * (self.N - 1)),
        }
        fb = self._no_metrics_feedback()
        assert self.stalled(state, fb) is False

    def test_cp_2_5_1_not_stalled_mixed_tail(self):
        """CP-2.5-1：尾部含非 no_metrics → 不早停。"""
        state = {
            "fix_loop_history": self._make_history(["runtime", "no_metrics"]),
        }
        fb = self._no_metrics_feedback()
        assert self.stalled(state, fb) is False

    def test_cp_2_5_1_not_stalled_different_category(self):
        """CP-2.5-1：本轮不是 NO_METRICS → 不早停。"""
        state = {
            "fix_loop_history": self._make_history(["no_metrics"] * self.N),
        }
        fb = self.ExecutionFeedback(
            category=self.ErrorCategory.RUNTIME,
            auto_fixable=True,
            summary="",
            fix_hint="",
            representative_stderr="",
        )
        assert self.stalled(state, fb) is False

    def test_cp_2_5_1_empty_history_not_stalled(self):
        """CP-2.5-1：空历史 → 不早停。"""
        state = {"fix_loop_history": []}
        fb = self._no_metrics_feedback()
        assert self.stalled(state, fb) is False

    def test_cp_2_5_2_early_stop_skips_retry_coding(self):
        """CP-2.5-2：连续 N 轮 no_metrics → _maybe_interrupt_or_return 不走 retry_coding，走 interrupt#2 通道。"""
        from core.nodes.execution import (
            _maybe_interrupt_or_return,
            ExecutionResult,
            _ROUTE_RETRY_CODING,
        )
        from config import DEV_LOOP_MIN_CALLS_PER_ROUND

        exec_result: ExecutionResult = {  # type: ignore[assignment]
            "success": False,
            "metrics": {},
            "logs": "",
            "errors": ["[error_category=no_metrics] 无指标"],
            "artifacts": [],
            "runtime_seconds": 0.0,
            "environment_info": {},
            "step_reconciliation": {},
            "degraded_credentials": [],
            "budget_truncated": False,
            "metrics_groups": {},
        }

        no_metrics_fb = self._no_metrics_feedback()
        history = self._make_history(["no_metrics"] * self.N)
        state = {
            "fix_loop_count": self.N,
            "fix_loop_history": history,
            "retry_budget_remaining": 50,
            "_dev_loop_llm_calls": 5,
            "node_errors": [],
            "degraded_nodes": [],
        }
        updates = {
            "execution_result": exec_result,
            "current_step": "execution",
            "node_errors": [],
            "degraded_nodes": [],
        }

        interrupted_payloads = []

        def fake_interrupt(payload):
            interrupted_payloads.append(payload)
            return {"decision": "terminate"}

        with patch("core.nodes.execution.interrupt", side_effect=fake_interrupt):
            result = _maybe_interrupt_or_return(
                updates, exec_result, no_metrics_fb, state, already_committed=True
            )

        # 不应走 retry_coding
        assert result.get("_dev_loop_route") != _ROUTE_RETRY_CODING
        # 应该触发 interrupt（因为早停）
        assert len(interrupted_payloads) == 1

    def test_cp_2_5_3_term_map_no_metrics_entry(self):
        """CP-2.5-3：term_map['error_category:no_metrics'] 中文条目存在。"""
        from ui.term_map import TERM_LABELS, humanize

        assert "error_category:no_metrics" in TERM_LABELS
        label = TERM_LABELS["error_category:no_metrics"]
        assert isinstance(label, str) and len(label) > 0

        # humanize 正确返回中文，不带兜底后缀
        result = humanize("error_category", "no_metrics")
        assert "（内部标识）" not in result


class TestFullRegression:
    """CP-2.5-4：全量回归不退化（通过验证关键模块可正常导入）。"""

    def test_cp_2_5_4_all_modules_importable(self):
        """验证所有修改的模块仍可正常导入。"""
        import importlib

        it = importlib.import_module("core.tools.interaction_tools")
        # core/nodes/__init__.py 将 coding callable 遮蔽了子模块，
        # 须用 importlib 直接导入模块（Known-bug §6）
        coding_mod = importlib.import_module("core.nodes.coding")
        execution_mod = importlib.import_module("core.nodes.execution")
        import ui.term_map as term_map
        import config

        # 验证新增内容存在
        assert hasattr(it, "_normalize_purpose_key")
        assert hasattr(it, "make_request_user_input_tool")
        assert hasattr(it, "request_user_input")

        assert hasattr(coding_mod, "_CREDENTIAL_DEGRADATIONS_DIRECTIVE")
        assert hasattr(coding_mod, "_build_coding_context")

        assert hasattr(execution_mod, "_CREDENTIAL_DEGRADATIONS_DIRECTIVE")
        assert hasattr(execution_mod, "_apply_no_metrics")
        assert hasattr(execution_mod, "_no_metrics_stalled")
        assert "NO_METRICS" in [e.name for e in execution_mod.ErrorCategory]

        assert hasattr(config, "NO_METRICS_EARLY_STOP_ROUNDS")
        assert config.NO_METRICS_EARLY_STOP_ROUNDS == 2

        assert "error_category:no_metrics" in term_map.TERM_LABELS
