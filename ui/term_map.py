"""S5-09 术语治理：内部枚举 → 用户可读中文的单一映射表（Sprint 5 任务 T-S5-3-5）。

架构参考：sprint5/architecture.md §7.9 / dev-plan §T-S5-3-5（AC-S5-18）。

设计契约（极简，一表一函数，不做 i18n 框架、不建分层/命名空间类）::

    - ``TERM_LABELS``：单一扁平表，key = ``"{domain}:{value}"``（如
      ``"code_strategy:from_scratch"``），value = 用户可读中文文案；
    - ``humanize(domain, value)``：查表命中返回文案；未知 domain / 未知 value
      兜底返回 ``f"{value}（内部标识）"``——**不崩、不静默丢信息**（AC-S5-18）。

数据源（各 domain 枚举值从代码如实收集，绝不臆造）::

    - code_strategy / resource_strategy ← planning.py / resource_scout.py
      ``_VALID_STRATEGIES = ("use_repo", "hybrid", "from_scratch")``；
    - error_category ← core/nodes/execution.py::ErrorCategory（11 值）+
      execution.py L1820 降级路径写入的 ``degraded`` 字面量；
    - node（节点名）← core/graph.py 七节点（中文名与 analysis_progress
      ``_NODE_DISPLAY`` / execution_monitor ``_STEP_DISPLAY`` 既有口径对齐）；
    - report_form（报告三形态）← reporting.py::_determine_report_form；
    - conclusion_level / annotation / audit_rule ← T-S5-3-4 文案定稿清单
      **逐字入表**（AC-S5-07 措辞红线：engineering 禁"复现成功"字样）；
    - user_fix_decision ← execution.py::_route_user_fix_decision 三决策值
      （interrupt#2 resume 契约）。

关于 fix_strategy domain 的说明（如实记录，非遗漏）::

    ``FixLoopRecord.fix_strategy``（core/state.py L181）的写入源是
    ``ExecutionFeedback.fix_hint``（execution.py L1802）——**自由中文文本**
    （如"调整依赖版本 / 更换等价包后重试"），代码中不存在可枚举的固定取值，
    故本表无 ``fix_strategy:*`` 条目；页面对该字段原样渲染（本就通俗），
    不经 ``humanize``（经表反而会给通俗中文错挂"（内部标识）"兜底后缀）。
"""

from __future__ import annotations

from typing import Dict

__all__ = ["TERM_LABELS", "humanize"]


TERM_LABELS: Dict[str, str] = {
    # --- code_strategy（planning.py::_VALID_STRATEGIES）---
    "code_strategy:use_repo": "使用现有仓库",
    "code_strategy:hybrid": "混合（仓库为主 + 部分自研）",
    "code_strategy:from_scratch": "从零实现",
    # --- resource_strategy（resource_scout.py::_VALID_STRATEGIES，同三值）---
    "resource_strategy:use_repo": "使用现有仓库",
    "resource_strategy:hybrid": "混合（仓库为主 + 部分自研）",
    "resource_strategy:from_scratch": "从零实现",
    # --- error_category（execution.py::ErrorCategory + "degraded" 降级字面量）---
    "error_category:syntax": "语法错误",
    "error_category:import": "导入错误",
    "error_category:dependency": "依赖错误",
    "error_category:path": "路径错误",
    "error_category:runtime": "运行时错误",
    "error_category:credential_required": "缺少凭证",
    "error_category:data_missing": "数据缺失",
    "error_category:hardware": "硬件资源不足",
    "error_category:timeout": "执行超时",
    "error_category:unresolved_resource": "资源无法解析",
    "error_category:none": "无错误",
    "error_category:no_metrics": "未产出指标",
    "error_category:degraded": "已降级",
    # --- node（graph.py 七节点，中文名与既有页面显示表口径对齐）---
    "node:paper_intake": "解析论文",
    "node:paper_analysis": "分析论文",
    "node:resource_scout": "资源侦察",
    "node:planning": "制定计划",
    "node:coding": "代码生成",
    "node:execution": "执行验证",
    "node:reporting": "汇总报告",
    # --- report_form（reporting.py 三形态骨架；full_success 的结论措辞
    #     由 conclusion_level 两级承载，此处仅描述形态本身，避 AC-S5-07 禁词）---
    "report_form:full_success": "执行成功",
    "report_form:code_only": "仅生成代码",
    "report_form:degraded": "未成功复现（降级）",
    # --- conclusion_level（T-S5-3-4 文案定稿，逐字入表，不改写）---
    "conclusion_level:science": "复现成功（科学复现）",
    "conclusion_level:engineering": "代码跑通（工程复现），论文实验结论未验证",
    "conclusion_level:none": "未成功复现（降级）",
    # --- annotation（正交标注三值，T-S5-3-4 文案定稿）---
    "annotation:simulation": "模拟/未验证内容",
    "annotation:credential_degraded": "凭证降级",
    "annotation:incomplete_execution": "执行不完整",
    # --- audit_rule（honesty_audit 三规则，T-S5-3-4 文案定稿）---
    "audit_rule:answer_leakage": "答案泄漏（非评估代码直接读取答案字段）",
    "audit_rule:hardcoded_score": "硬编码分数（评分结果由字面量写死）",
    "audit_rule:constant_outcome": "常量结局（评估函数恒返回常量）",
    # --- user_fix_decision（execution.py::_route_user_fix_decision 三决策）---
    "user_fix_decision:terminate": "终止任务",
    "user_fix_decision:revise_plan": "改计划（重新规划）",
    "user_fix_decision:export_code": "导出代码（降级交付）",
}


def humanize(domain: str, value: object) -> str:
    """内部枚举值 → 用户可读中文；未知值兜底 ``f"{value}（内部标识）"``（AC-S5-18）。

    兜底契约：不崩（任意 domain/value 均返回 str）、不静默（原值保留在兜底文案里，
    用户/测试仍能看到内部标识本身，便于反馈与排障）。
    """
    label = TERM_LABELS.get(f"{domain}:{value}")
    if label is not None:
        return label
    return f"{value}（内部标识）"
