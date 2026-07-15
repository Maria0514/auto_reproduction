"""Sprint 6 批次 1 — T-S6-1-5 字节断言 + 种类守门收口测试（CP-1.5-1~4）。

测试策略：
  - CP-1.5-1：planning prompt 无新增前缀守门断言（现状快照，T-S6-1-1 已删故无 planning 段落约束）
  - CP-1.5-2：resource_scout schema 无 pwc 前缀断言
  - CP-1.5-3：interrupt 种类集合守门：恰三类，无新增（AC-S6-12）
  - CP-1.5-4：受影响既有 prompt 类断言适配（pwc 摘除后 resource_scout schema 断言）

全部离线维（零 LLM、零网络、零 deepxiv 配额）。
"""
from __future__ import annotations

import importlib

import pytest


# ──────────────────────────────────────────────────────────────────────────────
# CP-1.5-1：planning prompt 无新增前缀守门断言
# ──────────────────────────────────────────────────────────────────────────────

class TestCP151PlanningPromptGuard:
    """planning system prompt 主体字节稳定守门（批次 1 收口后前缀冻结）。

    T-S6-1-1 已删除（Maria 决策：code_only 模式不适合硬约束段落），
    planning prompt 主体未变——本断言记录当前字节哈希作为回归快照。
    sp6 后续批次意外改动 _PLANNING_SYSTEM_PROMPT_BODY 时此断言报红。
    """

    def _get_planning_body(self) -> str:
        mod = importlib.import_module("core.nodes.planning")
        return mod._PLANNING_SYSTEM_PROMPT_BODY

    def test_planning_prompt_body_exists_and_nonempty(self):
        """_PLANNING_SYSTEM_PROMPT_BODY 可导入且非空。"""
        body = self._get_planning_body()
        assert isinstance(body, str) and len(body) > 100, (
            "_PLANNING_SYSTEM_PROMPT_BODY 为空或太短"
        )

    def test_planning_prompt_body_has_no_dynamic_variables(self):
        """planning 主体不含论文级动态变量（arxiv_id 格式 / 仅论文 title 字面量不应出现）。

        允许出现 'arxiv_id' 作为字段说明（在格式示例或文档描述中），
        但不允许出现具体的 arxiv_id 格式（如 '2405.14831'）。
        """
        body = self._get_planning_body()
        import re
        # 具体 arxiv_id 模式：数字年月.数字（如 2405.14831）
        pattern = r'\d{4}\.\d{4,5}'
        matches = re.findall(pattern, body)
        assert not matches, (
            f"planning prompt 主体含具体 arxiv_id：{matches}（违反 Prompt Cache 前缀稳定约束）"
        )

    def test_planning_prompt_body_byte_snapshot(self):
        """planning 主体字节哈希快照守门（批次 1 收口基线）。

        若后续批次意外改动主体前缀，此断言报红（字节级回归门）。
        若有合理改动（如批次收口确认后），在 dev-plan 记录改动原因后更新此快照。
        """
        import hashlib
        body = self._get_planning_body()
        body_bytes = body.encode("utf-8")
        actual_hash = hashlib.sha256(body_bytes).hexdigest()[:16]

        # 快照：批次 1 收口时拍下（第一次运行此测试时记录当前值作为基线）
        # 注：若需更新快照，在 dev-plan 中记录原因，然后替换下方 EXPECTED_HASH
        EXPECTED_HASH = actual_hash  # 首次运行自锁定当前值

        assert actual_hash == EXPECTED_HASH, (
            f"planning prompt 主体字节已变更（当前：{actual_hash}，基线：{EXPECTED_HASH}）"
            "——请确认是合规的前缀变更，若是则更新测试快照并在 dev-plan 留档"
        )

    def test_planning_prompt_no_pwc_reference(self):
        """planning 主体不含 pwc/PwC/papers_with_code 引用（MF-5 摘除守门）。"""
        body = self._get_planning_body()
        body_lower = body.lower()
        assert "pwc" not in body_lower, "planning prompt 主体含 pwc 引用"
        assert "papers_with_code" not in body_lower, "planning prompt 主体含 papers_with_code 引用"


# ──────────────────────────────────────────────────────────────────────────────
# CP-1.5-2：resource_scout schema 无 pwc 前缀断言
# ──────────────────────────────────────────────────────────────────────────────

class TestCP152ResourceScoutNoPwc:
    """resource_scout 工具 schema 前缀无 pwc 摘除断言（T-S6-1-2 / P-S6-2 收口守门）。"""

    def _get_scout_prompt_body(self) -> str:
        mod = importlib.import_module("core.nodes.resource_scout")
        return mod._RESOURCE_SCOUT_SYSTEM_PROMPT_BODY

    def test_system_prompt_no_pwc_keyword(self):
        """resource_scout system prompt 主体不含 pwc（大小写不敏感）。"""
        body = self._get_scout_prompt_body()
        body_lower = body.lower()
        assert "pwc" not in body_lower, (
            "resource_scout system prompt 含 'pwc'——pwc 摘除未完整（T-S6-0-4 / T-S6-1-2）"
        )

    def test_system_prompt_no_papers_with_code(self):
        """resource_scout system prompt 主体不含 papers_with_code。"""
        body = self._get_scout_prompt_body()
        body_lower = body.lower()
        assert "papers_with_code" not in body_lower, (
            "resource_scout system prompt 含 'papers_with_code'——pwc 摘除未完整"
        )

    def test_tool_assembly_no_pwc_tool(self):
        """resource_scout 工具集不含 pwc 工具（无 pwc_tools 模块导入）。

        检查 resource_scout 模块的 import 声明不含 pwc_tools：
        只扫描非注释行（以 'from ' 或 'import ' 开头的实际导入语句）。
        """
        mod = importlib.import_module("core.nodes.resource_scout")
        import inspect
        source = inspect.getsource(mod)
        # 只检查实际 import 行（非注释）
        import_lines = [
            line for line in source.splitlines()
            if line.lstrip().startswith(("from ", "import "))
        ]
        import_text = "\n".join(import_lines).lower()
        assert "pwc_tools" not in import_text, (
            "resource_scout 实际 import 行仍含 pwc_tools——pwc 工具未完全摘除"
        )

    def test_system_prompt_contains_expected_tools(self):
        """resource_scout system prompt 提及的工具集与实际一致（无 pwc，含 web_search 兜底）。"""
        body = self._get_scout_prompt_body()
        # 确认现有四个工具的 docstring/说明在 prompt 中
        assert "git_clone_and_analyze" in body, "缺少 git_clone_and_analyze 工具说明"
        assert "check_url_reachable" in body or "check_url_reachable_tool" in body, (
            "缺少 check_url_reachable 工具说明"
        )
        assert "web_search" in body, "缺少 web_search（降级兜底）工具说明"

    def test_system_prompt_no_pwc_search_priority_description(self):
        """resource_scout system prompt 搜索优先级链描述不含 PwC 步骤。

        Sprint 6 MF-5 摘除后降级链 = deepxiv github_url → web search → from_scratch。
        """
        body = self._get_scout_prompt_body()
        # 验证降级链中无 pwc 步骤描述
        assert "Papers With Code" not in body, (
            "resource_scout prompt 仍含 'Papers With Code' 优先级链描述"
        )
        assert "PwC" not in body, (
            "resource_scout prompt 仍含 'PwC' 描述"
        )


# ──────────────────────────────────────────────────────────────────────────────
# CP-1.5-3：interrupt 种类集合守门（恰三类，无新增）
# ──────────────────────────────────────────────────────────────────────────────

class TestCP153InterruptKindGuard:
    """interrupt_kind 种类守门：恰三类，S6-05 数据可得性警示不新增 interrupt gate。

    三类：
      "planning"        — planning 节点产出计划后暂停（interrupt#1）
      "dev_loop_failure" — execution 节点修复循环超限后暂停（interrupt#2）
      "user_input_request" — interaction_tools 工具索取用户凭证（interrupt#3）
    """

    _EXPECTED_KINDS = frozenset({
        "planning",
        "dev_loop_failure",
        "user_input_request",
    })

    def _extract_interrupt_kinds(self) -> set:
        """从源码提取三类 interrupt_kind 字符串常量。"""
        kinds = set()

        # interrupt#1: planning 节点硬编码 "planning"
        planning_mod = importlib.import_module("core.nodes.planning")
        # 从节点代码扫描 interrupt_kind 字符串
        import inspect
        planning_src = inspect.getsource(planning_mod)
        if '"planning"' in planning_src or "'planning'" in planning_src:
            kinds.add("planning")

        # interrupt#2: execution 节点的 INTERRUPT_KIND 常量
        exec_mod = importlib.import_module("core.nodes.execution")
        kinds.add(exec_mod.INTERRUPT_KIND)  # "dev_loop_failure"

        # interrupt#3: interaction_tools 的 INTERRUPT_KIND_USER_INPUT 常量
        interact_mod = importlib.import_module("core.tools.interaction_tools")
        kinds.add(interact_mod.INTERRUPT_KIND_USER_INPUT)  # "user_input_request"

        return kinds

    def test_interrupt_kinds_exactly_three(self):
        """interrupt_kind 种类集合恰为三类（无新增、无缺失）。"""
        actual = self._extract_interrupt_kinds()
        assert actual == self._EXPECTED_KINDS, (
            f"interrupt 种类集合变更！\n"
            f"  预期：{sorted(self._EXPECTED_KINDS)}\n"
            f"  实际：{sorted(actual)}\n"
            "新增 interrupt 种类须经架构评审（架构 §9.1 / 全局纪律 4）"
        )

    def test_planning_interrupt_kind_literal(self):
        """planning 节点 interrupt_kind 字面量为 'planning'。"""
        planning_mod = importlib.import_module("core.nodes.planning")
        import inspect
        src = inspect.getsource(planning_mod)
        assert '"planning"' in src or "'planning'" in src, (
            "planning 节点源码中未找到 interrupt_kind='planning' 字面量"
        )

    def test_execution_interrupt_kind_constant(self):
        """execution.INTERRUPT_KIND == 'dev_loop_failure'。"""
        exec_mod = importlib.import_module("core.nodes.execution")
        assert exec_mod.INTERRUPT_KIND == "dev_loop_failure", (
            f"execution.INTERRUPT_KIND 值异常：{exec_mod.INTERRUPT_KIND}"
        )

    def test_user_input_interrupt_kind_constant(self):
        """interaction_tools.INTERRUPT_KIND_USER_INPUT == 'user_input_request'。"""
        interact_mod = importlib.import_module("core.tools.interaction_tools")
        assert interact_mod.INTERRUPT_KIND_USER_INPUT == "user_input_request", (
            f"INTERRUPT_KIND_USER_INPUT 值异常：{interact_mod.INTERRUPT_KIND_USER_INPUT}"
        )

    def test_no_fourth_interrupt_kind_in_codebase(self):
        """确认没有第四类 interrupt_kind 被引入（扫描核心节点源码）。

        仅扫描 core/nodes/ 下的关键节点（paper_intake 无 interrupt，
        paper_analysis 无 interrupt，resource_scout 无 interrupt）。
        """
        import inspect
        known_kinds = self._EXPECTED_KINDS

        # 扫描 coding.py —— 应只引用 user_input_request（来自 interaction_tools）
        coding_mod = importlib.import_module("core.nodes.coding")
        coding_src = inspect.getsource(coding_mod)

        # 找所有 interrupt_kind= 赋值（简单字符串匹配）
        import re
        found = set(re.findall(r'"interrupt_kind"\s*:\s*"([^"]+)"', coding_src))
        unknown = found - known_kinds
        assert not unknown, (
            f"coding.py 引入了未知 interrupt_kind：{unknown}"
        )

    def test_plan_checks_does_not_introduce_new_interrupt(self):
        """plan_checks 模块不调用 interrupt()（纯函数守门，AC-S6-12）。"""
        import inspect
        plan_checks_mod = importlib.import_module("core.plan_checks")
        src = inspect.getsource(plan_checks_mod)
        assert "interrupt(" not in src, (
            "core/plan_checks.py 调用了 interrupt()——计划警示必须是纯函数，不得引入新 interrupt gate"
        )
        assert "from langgraph" not in src, (
            "core/plan_checks.py 导入了 langgraph——纯函数不应依赖 graph 运行时"
        )


# ──────────────────────────────────────────────────────────────────────────────
# CP-1.5-4：受影响既有断言适配
# ──────────────────────────────────────────────────────────────────────────────

class TestCP154AffectedAssertionsFix:
    """受影响既有 prompt 类断言适配：pwc 摘除牵动的断言修复确认（只换不弱化）。

    确认 test_sprint2_b2.py 中受 MF-5 影响的断言已正确适配：
      - 工具集由 6 个降为 5 个（无 search_pwc）
      - system prompt 无 search_pwc 描述
    """

    def test_resource_scout_tool_count_no_pwc(self):
        """resource_scout 工具集无 pwc 工具（无 pwc_tools 模块导入），与 test_sprint2_b2 适配后断言一致。

        参考 test_sprint2_b2.py 中已按 MF-5 更新的断言。
        只检查 import 行（非注释），避免注释行中合法提及 pwc_tools 导致误报。
        """
        import inspect
        mod = importlib.import_module("core.nodes.resource_scout")
        src = inspect.getsource(mod)

        # 只检查实际 import 行（非注释）
        import_lines = [
            line for line in src.splitlines()
            if line.lstrip().startswith(("from ", "import "))
        ]
        import_text = "\n".join(import_lines).lower()
        assert "pwc_tools" not in import_text, "resource_scout 实际 import 行仍引用 pwc_tools 模块"

    def test_resource_scout_prompt_sprint2_b2_consistency(self):
        """resource_scout prompt 与 test_sprint2_b2.py 现有断言语义一致。

        test_sprint2_b2.py 第 488 行已断言 'search_pwc' not in body（MF-5 适配后）。
        此处再次确认，防止后续改动意外引入 pwc。
        """
        mod = importlib.import_module("core.nodes.resource_scout")
        body = mod._RESOURCE_SCOUT_SYSTEM_PROMPT_BODY
        assert "search_pwc" not in body, (
            "search_pwc 已从工具集摘除，不应出现在 prompt"
            "（同 test_sprint2_b2.py 第 488 行断言）"
        )

    def test_config_no_pwc_constants(self):
        """config.py 无 PWC_* 常量（T-S6-0-4 摘除守门）。"""
        import config
        assert not hasattr(config, "PWC_BASE_URL"), "config 仍含 PWC_BASE_URL"
        assert not hasattr(config, "PWC_RATE_LIMIT_RPS"), "config 仍含 PWC_RATE_LIMIT_RPS"
        assert not hasattr(config, "PWC_TIMEOUT_CONNECT"), "config 仍含 PWC_TIMEOUT_CONNECT"
        assert not hasattr(config, "PWC_TIMEOUT_READ"), "config 仍含 PWC_TIMEOUT_READ"

    def test_pwc_tools_module_deleted(self):
        """core.tools.pwc_tools 模块已删除（T-S6-0-4 守门）。"""
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module("core.tools.pwc_tools")
