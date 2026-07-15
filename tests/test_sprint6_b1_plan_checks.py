"""Sprint 6 批次 1 — T-S6-1-3 plan_checks 三规则测试（CP-1.3-1~4）。

测试策略：
  - 纯函数，零 LLM，全部 mock-free，运行秒级完成。
  - CP-1.3-1：两面计划 fixture（模拟 task-19e21e015017 现场：14 步全是骨架无跑实验）→ W1/W2 均命中
  - CP-1.3-2：干净计划 fixture → 三规则零警示（误报防线）
  - CP-1.3-3：W3 单测：无 dataset ∧ selected_repo=None ∧ data_preparation 非空 → 命中
  - CP-1.3-4：边界各分支不误触发
"""
from __future__ import annotations

import pytest
from core.plan_checks import check_plan


# ──────────────────────────────────────────────────────────────────────────────
# Fixture 数据
# ──────────────────────────────────────────────────────────────────────────────

# 两面计划（模拟 task-19e21e015017 现场：14 个骨架步骤，无跑实验、无数据步骤）
_TWO_FACED_PLAN = {
    "plan_summary": "复现 HippoRAG 论文基线实验",
    "data_preparation": ["下载 HippoRAG 原始数据集", "预处理为标准格式"],
    "execution_steps": [
        {"step_name": "安装依赖", "command": "pip install -r requirements.txt"},
        {"step_name": "配置环境变量", "command": "cp .env.example .env"},
        {"step_name": "初始化项目结构", "command": "mkdir -p outputs logs"},
        {"step_name": "验证 Python 环境", "command": "python --version"},
        {"step_name": "检查 CUDA 可用性", "command": "python -c 'import torch'"},
        {"step_name": "安装额外依赖", "command": "pip install faiss-cpu"},
        {"step_name": "创建输出目录", "command": "mkdir -p outputs/results"},
        {"step_name": "检查配置文件", "command": "cat config.yaml"},
        {"step_name": "验证模型路径", "command": "ls models/"},
        {"step_name": "初始化日志", "command": "touch logs/app.log"},
        {"step_name": "检查网络连接", "command": "ping -c 1 google.com"},
        {"step_name": "备份原始文件", "command": "cp -r . backup/"},
        {"step_name": "清理临时文件", "command": "rm -rf /tmp/cache"},
        {"step_name": "完成准备", "command": "echo 'setup done'"},
    ],
    "expected_results": {
        "description": "在 MuSiQue 基准上 F1 达到 0.45 以上",
        "trend": "higher_is_better",
    },
    "code_strategy": "use_repo",
}

_TWO_FACED_RESOURCE_INFO: dict = {
    "repos": [
        {"url": "https://github.com/GraphRAG-Bench/HippoRAG", "quality_score": 0.8}
    ],
    "selected_repo": {"url": "https://github.com/GraphRAG-Bench/HippoRAG"},
    "external_resources": [],
    "resource_strategy": "use_repo",
}

# 干净计划（含数据步骤 + 跑实验步骤 + resource_info 有 dataset）
_CLEAN_PLAN = {
    "plan_summary": "复现 HippoRAG 实验",
    "data_preparation": ["下载 MuSiQue 数据集"],
    "execution_steps": [
        {"step_name": "安装依赖", "command": "pip install -r requirements.txt"},
        {
            "step_name": "下载并准备数据集",
            "command": "python scripts/download_dataset.py --name musique",
        },
        {
            "step_name": "运行实验",
            "command": "python run_experiment.py --config configs/base.yaml",
        },
        {
            "step_name": "评测结果",
            "command": "python eval.py --output outputs/results/summary.json",
        },
    ],
    "expected_results": {
        "description": "F1 ≥ 0.45",
        "trend": "higher_is_better",
    },
}

_CLEAN_RESOURCE_INFO = {
    "repos": [{"url": "https://github.com/GraphRAG-Bench/HippoRAG"}],
    "selected_repo": {"url": "https://github.com/GraphRAG-Bench/HippoRAG"},
    "external_resources": [
        {
            "type": "dataset",
            "name": "MuSiQue",
            "url": "https://huggingface.co/datasets/musique",
        }
    ],
    "resource_strategy": "use_repo",
}


# ──────────────────────────────────────────────────────────────────────────────
# CP-1.3-1：两面计划 → W1 / W2 均命中
# ──────────────────────────────────────────────────────────────────────────────

class TestCP131TwoFacedPlan:
    """两面计划 fixture 驱动，W1/W2 均命中（AC-S6-11）。"""

    def setup_method(self):
        self.warnings = check_plan(_TWO_FACED_PLAN, _TWO_FACED_RESOURCE_INFO)
        self.rules = {w["rule"] for w in self.warnings}

    def test_w1_data_step_decoupled(self):
        """data_preparation 非空 ∧ 步骤无数据关键词 → W1 命中。"""
        assert "W1" in self.rules, f"W1 未命中，实际警示：{self.warnings}"

    def test_w2_metric_step_decoupled(self):
        """expected_results 非空 ∧ 步骤无实验/指标关键词 → W2 命中。"""
        assert "W2" in self.rules, f"W2 未命中，实际警示：{self.warnings}"

    def test_warnings_have_rule_and_message(self):
        """每条警示都有 rule 和 message 字段。"""
        for w in self.warnings:
            assert "rule" in w and "message" in w, f"警示缺字段：{w}"
            assert w["rule"] in ("W1", "W2", "W3"), f"未知 rule：{w['rule']}"
            assert w["message"], f"message 为空：{w}"

    def test_w3_not_triggered_when_repo_selected(self):
        """有 selected_repo 时 W3 不应触发（resource_info 有选中仓库）。"""
        assert "W3" not in self.rules, f"W3 误触发，实际警示：{self.warnings}"


# ──────────────────────────────────────────────────────────────────────────────
# CP-1.3-2：干净计划 → 三规则零警示
# ──────────────────────────────────────────────────────────────────────────────

class TestCP132CleanPlan:
    """干净计划 fixture → 三规则零警示（误报防线，AC-S6-11）。"""

    def test_zero_warnings_clean_plan(self):
        """含数据步骤 + run experiment 步骤 + resource_info 有 dataset → 零警示。"""
        result = check_plan(_CLEAN_PLAN, _CLEAN_RESOURCE_INFO)
        assert result == [], f"误报警示：{result}"

    def test_w1_not_triggered_when_data_steps_present(self):
        """步骤中有 'dataset' 关键词时 W1 不触发。"""
        result = check_plan(_CLEAN_PLAN, _CLEAN_RESOURCE_INFO)
        rules = {w["rule"] for w in result}
        assert "W1" not in rules, f"W1 误触发"

    def test_w2_not_triggered_when_experiment_steps_present(self):
        """步骤中有 'run' / 'eval' / 'summary.json' 关键词时 W2 不触发。"""
        result = check_plan(_CLEAN_PLAN, _CLEAN_RESOURCE_INFO)
        rules = {w["rule"] for w in result}
        assert "W2" not in rules, f"W2 误触发"

    def test_w3_not_triggered_when_dataset_in_resource_info(self):
        """resource_info.external_resources 含 dataset 条目时 W3 不触发。"""
        result = check_plan(_CLEAN_PLAN, _CLEAN_RESOURCE_INFO)
        rules = {w["rule"] for w in result}
        assert "W3" not in rules, f"W3 误触发"


# ──────────────────────────────────────────────────────────────────────────────
# CP-1.3-3：W3 单测
# ──────────────────────────────────────────────────────────────────────────────

class TestCP133W3DataUnavailable:
    """W3 单测：无 dataset 线索 ∧ selected_repo=None ∧ data_preparation 非空 → 命中（AC-S6-12）。"""

    def _w3_resource_info(self, selected_repo=None, external_resources=None):
        return {
            "repos": [],
            "selected_repo": selected_repo,
            "external_resources": external_resources or [],
            "resource_strategy": "from_scratch",
        }

    def _plan_with_data_prep(self):
        return {
            "data_preparation": ["下载 HippoRAG 原始数据集"],
            "execution_steps": [
                {"step_name": "安装依赖", "command": "pip install -r requirements.txt"}
            ],
            "expected_results": {},
        }

    def test_w3_triggered_no_dataset_no_repo(self):
        """无 dataset ∧ selected_repo=None ∧ data_prep 非空 → W3 命中。"""
        plan = self._plan_with_data_prep()
        ri = self._w3_resource_info(selected_repo=None, external_resources=[])
        result = check_plan(plan, ri)
        rules = {w["rule"] for w in result}
        assert "W3" in rules, f"W3 未命中，实际警示：{result}"

    def test_w3_not_triggered_when_repo_selected(self):
        """selected_repo 非 None → W3 不触发（有仓库可克隆数据）。"""
        plan = self._plan_with_data_prep()
        ri = self._w3_resource_info(
            selected_repo={"url": "https://github.com/a/b"},
            external_resources=[],
        )
        result = check_plan(plan, ri)
        rules = {w["rule"] for w in result}
        assert "W3" not in rules, f"W3 误触发（有 selected_repo）：{result}"

    def test_w3_not_triggered_when_dataset_in_external_resources(self):
        """external_resources 含 dataset → W3 不触发。"""
        plan = self._plan_with_data_prep()
        ri = self._w3_resource_info(
            selected_repo=None,
            external_resources=[{"type": "dataset", "name": "MuSiQue", "url": "http://x"}],
        )
        result = check_plan(plan, ri)
        rules = {w["rule"] for w in result}
        assert "W3" not in rules, f"W3 误触发（有 dataset 资源）：{result}"

    def test_w3_not_triggered_when_data_prep_empty(self):
        """data_preparation 为空 → W3 不触发（无需数据）。"""
        plan = {
            "data_preparation": [],
            "execution_steps": [],
            "expected_results": {},
        }
        ri = self._w3_resource_info(selected_repo=None, external_resources=[])
        result = check_plan(plan, ri)
        rules = {w["rule"] for w in result}
        assert "W3" not in rules, f"W3 误触发（data_preparation 为空）：{result}"

    def test_w3_huggingface_url_counts_as_dataset(self):
        """external_resources 条目 url 含 'huggingface' → 视作 dataset，W3 不触发。"""
        plan = self._plan_with_data_prep()
        ri = self._w3_resource_info(
            selected_repo=None,
            external_resources=[{
                "type": "model",  # type 不是 dataset，但 url 含 huggingface
                "url": "https://huggingface.co/datasets/squad",
                "name": "SQuAD",
            }],
        )
        result = check_plan(plan, ri)
        rules = {w["rule"] for w in result}
        assert "W3" not in rules, f"W3 误触发（huggingface URL 应被识别为 dataset）：{result}"


# ──────────────────────────────────────────────────────────────────────────────
# CP-1.3-4：边界条件 — 各分支不误触发
# ──────────────────────────────────────────────────────────────────────────────

class TestCP134Boundaries:
    """边界条件：各分支空/list 形态/缺字段等不误触发。"""

    def test_empty_plan_no_warnings(self):
        """全空 plan + 全空 resource_info → 零警示。"""
        assert check_plan({}, {}) == []

    def test_none_inputs_no_crash(self):
        """plan=None, resource_info=None → 零警示（防御式处理）。"""
        # 实际调用时传 None 应被兜底为 {}
        assert check_plan(None, None) == []  # type: ignore[arg-type]

    def test_w1_not_triggered_when_data_prep_empty_list(self):
        """data_preparation=[] → W1 不触发。"""
        plan = {
            "data_preparation": [],
            "execution_steps": [{"step_name": "x", "command": "echo hi"}],
            "expected_results": {},
        }
        result = check_plan(plan, {})
        rules = {w["rule"] for w in result}
        assert "W1" not in rules

    def test_w1_not_triggered_when_data_prep_none(self):
        """data_preparation=None → W1 不触发。"""
        plan = {
            "data_preparation": None,
            "execution_steps": [{"step_name": "x", "command": "echo hi"}],
        }
        result = check_plan(plan, {})
        rules = {w["rule"] for w in result}
        assert "W1" not in rules

    def test_w2_not_triggered_when_expected_results_empty_dict(self):
        """expected_results={} → W2 不触发。"""
        plan = {
            "data_preparation": None,
            "execution_steps": [{"step_name": "x", "command": "echo hi"}],
            "expected_results": {},
        }
        result = check_plan(plan, {})
        rules = {w["rule"] for w in result}
        assert "W2" not in rules

    def test_w2_not_triggered_when_expected_results_empty_list(self):
        """expected_results=[] → W2 不触发。"""
        plan = {
            "data_preparation": None,
            "execution_steps": [{"step_name": "x", "command": "echo hi"}],
            "expected_results": [],
        }
        result = check_plan(plan, {})
        rules = {w["rule"] for w in result}
        assert "W2" not in rules

    def test_w2_list_form_expected_results(self):
        """expected_results 为 list 形态（[{description, trend}]）时正确解析。

        有实质内容的 list 形态 + 无指标步骤 → W2 命中。
        """
        plan = {
            "data_preparation": None,
            "execution_steps": [{"step_name": "安装", "command": "pip install ."}],
            "expected_results": [
                {"description": "F1 ≥ 0.45", "trend": "higher_is_better"}
            ],
        }
        result = check_plan(plan, {})
        rules = {w["rule"] for w in result}
        assert "W2" in rules, f"list 形态 expected_results 应触发 W2，实际：{result}"

    def test_w2_list_form_empty_items_no_trigger(self):
        """expected_results=[{}] → 空 dict 视作无实质内容，W2 不触发。"""
        plan = {
            "data_preparation": None,
            "execution_steps": [{"step_name": "安装", "command": "pip install ."}],
            "expected_results": [{}],
        }
        result = check_plan(plan, {})
        rules = {w["rule"] for w in result}
        assert "W2" not in rules

    def test_w1_w2_empty_execution_steps(self):
        """execution_steps=[] ∧ data_prep 非空 ∧ expected_results 非空 → W1 W2 均触发。"""
        plan = {
            "data_preparation": ["下载数据"],
            "execution_steps": [],
            "expected_results": {"description": "F1 ≥ 0.4"},
        }
        result = check_plan(plan, {})
        rules = {w["rule"] for w in result}
        assert "W1" in rules and "W2" in rules, f"空 steps 应触发 W1+W2，实际：{result}"

    def test_keyword_not_false_positive_on_irrelevant_data_word(self):
        """步骤文本中 'metadata' 含子串 'data' 但语义无关——宁窄勿宽策略下仍命中。

        注：W1 关键词 'data' 是宽义关键词，'metadata' 中含 'data' 会命中。
        这是设计取舍（宁窄勿宽的"宽"边界），测试记录此行为使其显式可知。
        """
        plan = {
            "data_preparation": ["处理元数据"],
            "execution_steps": [
                {"step_name": "提取元数据", "command": "python extract_metadata.py"}
            ],
            "expected_results": {},
        }
        result = check_plan(plan, {})
        rules = {w["rule"] for w in result}
        # 'metadata' 含 'data'，W1 不触发（步骤命中了关键词）
        assert "W1" not in rules, (
            "'metadata' 含 'data' 子串，W1 关键词命中——属设计预期（宁窄勿宽）"
        )

    def test_w3_not_triggered_when_no_data_prep(self):
        """W3 仅在 data_preparation 非空时才可能触发。"""
        plan = {"data_preparation": None, "execution_steps": [], "expected_results": {}}
        result = check_plan(plan, {"selected_repo": None, "external_resources": []})
        rules = {w["rule"] for w in result}
        assert "W3" not in rules

    def test_return_type_is_list_of_dicts(self):
        """check_plan 返回类型恒为 list[dict]。"""
        result = check_plan({}, {})
        assert isinstance(result, list)
        result2 = check_plan(_TWO_FACED_PLAN, {})
        assert isinstance(result2, list)
        for item in result2:
            assert isinstance(item, dict)

    def test_rule_values_are_string_literals(self):
        """rule 字段为字符串字面量，不是 Enum 成员。"""
        result = check_plan(_TWO_FACED_PLAN, _TWO_FACED_RESOURCE_INFO)
        for w in result:
            assert isinstance(w["rule"], str), f"rule 应为 str，实际：{type(w['rule'])}"
