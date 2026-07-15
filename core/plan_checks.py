"""plan_checks.py — 计划自洽确定性交叉检查（S6-05，架构 §7.5）。

零 LLM、零 state 写入的纯函数模块。
调用 check_plan(plan, resource_info) 返回警示列表，由 UI 渲染消费（不阻断审批）。

三条规则（rule 用字符串字面量，不建 Enum）：
  W1 数据步骤脱节 ── data_preparation 非空 ∧ 执行步骤无数据关键词
  W2 指标产出脱节 ── expected_results 非空 ∧ 执行步骤无实验/指标关键词
  W3 数据不可得   ── resource_info 无 dataset ∧ selected_repo=None ∧ data_preparation 非空

误报防线（R-S6-A5）：关键词宁窄勿宽；警示不阻断审批；纯函数调用方决定展示方式。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

# ──────────────────────────────────────────────────────────────────────────────
# 关键词静态小表（宁窄勿宽——宁漏报不误报）
# ──────────────────────────────────────────────────────────────────────────────

# W1：数据准备相关关键词（英文全小写匹配，中文直接 in 判断）
_DATA_KEYWORDS: List[str] = [
    "data",
    "dataset",
    "download",
    "prepare",
    "预处理",
    "数据",
]

# W2：实验/指标相关关键词
_METRIC_KEYWORDS: List[str] = [
    "run",
    "train",
    "eval",
    "experiment",
    "metric",
    "summary.json",
    "实验",
    "评测",
    "指标",
]


def _step_text(step: Any) -> str:
    """将单个执行步骤提取为可搜索文本（name + command 拼接，忽略 None）。"""
    if not isinstance(step, dict):
        return ""
    parts = [
        str(step.get("name") or ""),
        str(step.get("step_name") or ""),
        str(step.get("command") or ""),
    ]
    return " ".join(parts).strip()


def _any_step_matches(steps: List[Any], keywords: List[str]) -> bool:
    """任意步骤的文本命中关键词列表中的任意一个则返回 True。

    匹配策略：文本转小写后 in 判断（避免大小写误差）。
    中文关键词本身不区分大小写，也走相同路径不特殊处理。
    """
    for step in steps:
        text = _step_text(step).lower()
        for kw in keywords:
            if kw.lower() in text:
                return True
    return False


def _expected_results_nonempty(expected_results: Any) -> bool:
    """判断 expected_results 字段是否「非空」（有实质内容）。

    兼容两种形态：
      - dict 形态：{"trend": ..., "description": ...} —— 至少一键有非空值
      - list 形态：[{"description": ..., "trend": ...}, ...] —— 至少一项非空 dict

    空 dict / 空 list / None / "" 均视作空。
    """
    if not expected_results:
        return False
    if isinstance(expected_results, list):
        for item in expected_results:
            if isinstance(item, dict) and any(v for v in item.values()):
                return True
        return False
    if isinstance(expected_results, dict):
        return any(v for v in expected_results.values())
    # 其他类型（str 等）：转 bool
    return bool(expected_results)


def _has_dataset_resource(resource_info: Dict[str, Any]) -> bool:
    """resource_info 的 external_resources 中是否含有 dataset 类条目。

    判断逻辑（宁窄勿宽）：
      - external_resources 为 list
      - 至少一条 entry 的 type/category/kind 字段（大小写不敏感）含 "dataset"，
        或 url/name 字段含 "dataset"/"huggingface" 等数据集强信号关键词。
    """
    if not resource_info:
        return False
    ext = resource_info.get("external_resources")
    if not isinstance(ext, list) or not ext:
        return False
    _dataset_signals = {"dataset", "huggingface", "kaggle", "zenodo"}
    for entry in ext:
        if not isinstance(entry, dict):
            continue
        # 检查 type / category / kind 字段
        for field in ("type", "category", "kind"):
            val = str(entry.get(field) or "").lower()
            if "dataset" in val:
                return True
        # 检查 url / name 字段含数据集强信号
        for field in ("url", "name", "description"):
            val = str(entry.get(field) or "").lower()
            if any(sig in val for sig in _dataset_signals):
                return True
    return False


def check_plan(plan: Dict[str, Any], resource_info: Dict[str, Any]) -> List[Dict[str, str]]:
    """对复现计划做确定性交叉检查，返回警示列表。

    Args:
        plan: ReproductionPlan dict（来自 interrupt payload）。
        resource_info: ResourceInfo dict（来自 interrupt payload 或 state）。

    Returns:
        警示列表，每项为 {"rule": str, "message": str}。
        空列表表示无警示（干净计划）。
        零 LLM 调用、零 state 写入。
    """
    plan = plan or {}
    resource_info = resource_info or {}

    warnings: List[Dict[str, str]] = []

    data_preparation: Any = plan.get("data_preparation")
    execution_steps: List[Any] = plan.get("execution_steps") or []
    expected_results: Any = plan.get("expected_results")

    # ── W1：数据步骤脱节 ──────────────────────────────────────────────────────
    # data_preparation 非空 AND 全部执行步骤均无数据关键词
    data_prep_nonempty = bool(data_preparation)  # None/[]/""/"" → False
    if data_prep_nonempty:
        if not _any_step_matches(execution_steps, _DATA_KEYWORDS):
            warnings.append({
                "rule": "W1",
                "message": "计划声明了数据准备工作，但执行步骤中没有任何数据相关步骤",
            })

    # ── W2：指标产出脱节 ──────────────────────────────────────────────────────
    # expected_results 非空 AND 全部执行步骤均无实验/指标关键词
    if _expected_results_nonempty(expected_results):
        if not _any_step_matches(execution_steps, _METRIC_KEYWORDS):
            warnings.append({
                "rule": "W2",
                "message": "计划有指标性预期，但执行步骤中没有产出指标的步骤",
            })

    # ── W3：数据不可得 ────────────────────────────────────────────────────────
    # resource_info 无 dataset 类条目 AND selected_repo=None AND data_preparation 非空
    if data_prep_nonempty:
        selected_repo = resource_info.get("selected_repo")
        has_dataset = _has_dataset_resource(resource_info)
        if not has_dataset and selected_repo is None:
            warnings.append({
                "rule": "W3",
                "message": "所需数据集未在资源侦察中找到，请决策",
            })

    return warnings
