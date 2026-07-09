"""reporting 节点（S3-05）：纯函数式生成三形态 Markdown 复现报告。

把 ``graph.py`` 中的 ``reporting`` 占位替换为真实节点。**纯读、无 LLM、无
interrupt**：只读全局状态，按形态拼装 Markdown，写入 ``report_path``，返回
``{"report_path": ..., "current_step": "reporting", "honesty_audit": ...}``。

设计要点（架构 §2.4）：
    - ``_determine_report_form``：三形态判定（优先级从上到下）——
        1. ``execution_mode == CODE_ONLY`` → ``"code_only"``；
        2. ``execution_result.success == True`` → ``"full_success"``；
        3. 其余（含 ``execution_result is None`` 但非 code_only / success=False /
           export_code 降级）→ ``"degraded"``。
    - 三形态内容映射（**只读字段，绝不写 state 的任何 list 字段**）：
        - full_success：成功结论卡片 + 指标对比表（baseline/expected vs 复现值，
          仅展示对比、不硬判定达标，Q-S3-01 B 档）+ artifact 清单 + 执行概况；
        - code_only：仅生成代码结论卡片 + 代码位置 + deliverables 清单，无指标章节，
          标注"仅生成代码、未执行"（``execution_result is None`` 时仍产有效报告）；
        - degraded：未成功复现结论卡片 + 降级原因 + node_errors 摘要（解析
          ``[error_category=...]`` 前缀）+ fix_loop_history 修复历程 + 保留代码与
          产物 + user_fix_decision。
    - 报告路径：优先从 ``state["code_output_dir"]`` 推导
      ``Path(code_output_dir).parent / "report.md"``（报告与代码同目录，与 C1
      的 ``workspace_dir/<arxiv_id>/code`` 天然一致）；缺失时回退用 arxiv_id 拼
      ``workspace_dir/<thread>/report.md``。经 ``resolve()+is_relative_to`` 校验。
    - 语言策略（sp2）：叙述用中文，事实层（数据集名 / 指标名 / 仓库 URL / 框架名）
      保留英文。

sp5 渲染改造（T-S5-3-4，S5-04/05/06/10，架构 §7.4/§7.5/§7.6/§7.10）：
    - **两级措辞（AC-S5-07 红线）**：full_success 结论卡片按 conclusion.level 输出——
      science → "复现成功（科学复现）"；engineering → "代码跑通（工程复现），论文
      实验结论未验证"，engineering 报告全文禁"复现成功"字样；三形态骨架不动。
    - **正交标注声明块（AC-S5-03/11/12）**：conclusion.annotations 非空 → 报告顶部
      "重要声明"节（simulation → notice 原文 + 审计 hits 证据表；credential_degraded
      → 降级凭证清单，purpose 中文说明查 plan.required_credentials；
      incomplete_execution → 截断/缺步声明）。
    - **计划目标回验节（AC-S5-08）**：goal_checks 三态表（符合/不符/未验证），存在
      不符/未验证 → 明示不作科学复现级别宣告。
    - **步骤对账节（AC-S5-10）**："已完成 N/M 步" + 未执行清单 + budget_truncated
      截断声明；attribution_unavailable=True 时不做未执行统计、如实列原始命令清单
      （红线：禁"0 步未执行"式误导）。
    - **对比表改造（AC-S5-09/20）**：删"计划 expected"列，只留论文 baseline vs
      本次复现值；metrics_groups 非空时按组展开；旧 dict expected_results 防御容忍。
    - **嵌套降维（AC-S5-20）**：dict/list 指标值逐键行降维渲染（``_flatten_entries``），
      禁 ``str()`` 整塞单元格；执行概况节 key_packages 逐包渲染。

**红线（CP-C2-5，sp5 T-S5-3-2 显式扩展）**：reporting 是终点消费者，只读不改——
返回 dict **仅含** ``report_path``、``current_step`` 和 ``honesty_audit``（S5-03
诚实性审计结果，单值 last-write-wins，架构 §1 / §10.1 R-7），绝不返回 / 覆盖
``node_errors`` / ``degraded_nodes`` / ``fix_loop_history`` 等任何 list 字段
（避免无意覆盖上游累积的列表——红线原意完整保留）。
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from config import WORKSPACE_DIR
from core.honesty_audit import audit_code_dir
from core.state import ExecutionMode, GlobalState

logger = logging.getLogger(__name__)


NODE_NAME: str = "reporting"


# ---------------------------------------------------------------------------
# 三形态判定（架构 §2.4，优先级从上到下）
# ---------------------------------------------------------------------------


def _determine_report_form(state: GlobalState) -> str:
    """三形态判定（优先级严格从上到下）。

    1. ``execution_mode == CODE_ONLY`` → ``"code_only"``（即便 success=True 也走
       code_only，因为本就未走 execution）；
    2. ``execution_result.success == True`` → ``"full_success"``；
    3. 其余（含 ``execution_result is None`` 但非 code_only、success=False、
       export_code 降级）→ ``"degraded"``。
    """
    if _is_code_only(state):
        return "code_only"
    exec_result = state.get("execution_result")
    if isinstance(exec_result, dict) and exec_result.get("success") is True:
        return "full_success"
    return "degraded"


def _is_code_only(state: GlobalState) -> bool:
    """判定是否 code_only 模式（兼容 Enum 与 str 两种 execution_mode 取值）。"""
    mode = state.get("execution_mode")
    if mode is None:
        return False
    if isinstance(mode, ExecutionMode):
        return mode == ExecutionMode.CODE_ONLY
    return str(mode) == ExecutionMode.CODE_ONLY.value or str(mode) == "code_only"


# ---------------------------------------------------------------------------
# S5-04 结论判定 + 定性目标回验（T-S5-3-3，架构 §7.4 / §7.5）
# 纯确定性函数，零 LLM、零猜测——判定只依赖 state / exec_result / audit 的既有事实。
# ---------------------------------------------------------------------------

# goal_checks 三态字面量（AC-S5-08；不建 Enum，T-S5-3-4 渲染直接复用）。
_VERDICT_MATCH = "符合"
_VERDICT_MISMATCH = "不符"
_VERDICT_UNVERIFIED = "未验证"


def _normalize_group_key(name: Any) -> str:
    """组名/指标名归一化：小写 + 非字母数字压成下划线（"baselines/no_skill" →
    "baselines_no_skill"），供确定性子串匹配。"""
    return re.sub(r"[^a-z0-9]+", "_", str(name).lower()).strip("_")


def _match_metrics_group(target: str, metrics_groups: Dict[str, Any]) -> Optional[str]:
    """在 metrics_groups 中按归一化名匹配 trend 组名（架构 §7.4）。

    匹配顺序（确定性，按组名排序遍历）：
        1. 归一化精确匹配，唯一命中 → 返回；
        2. 归一化子串匹配（双向包含），唯一命中 → 返回；
        3. 零命中或歧义（多命中）→ None（保守失配，回验判"未验证"）。
    """
    norm_target = _normalize_group_key(target)
    if not norm_target:
        return None
    names = sorted(str(n) for n in metrics_groups.keys())
    exact = [n for n in names if _normalize_group_key(n) == norm_target]
    if exact:
        return exact[0] if len(exact) == 1 else None
    fuzzy = [
        n
        for n in names
        if norm_target in _normalize_group_key(n)
        or _normalize_group_key(n) in norm_target
    ]
    return fuzzy[0] if len(fuzzy) == 1 else None


def _lookup_metric_value(fields: Any, metric: str) -> Optional[float]:
    """从组内顶层字段取数值指标（精确键优先，归一化键相等兜底）。

    仅接受 int/float（bool 排除）；取不到 / 非数值 → None（回验判"未验证"）。
    """
    if not isinstance(fields, dict):
        return None
    value: Any = fields.get(metric)
    if value is None:
        norm_metric = _normalize_group_key(metric)
        for key in sorted(str(k) for k in fields.keys()):
            if _normalize_group_key(key) == norm_metric:
                value = fields[key]
                break
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _verify_trend(trend: Dict[str, Any], metrics_groups: Dict[str, Any]) -> str:
    """对单条 trend（{"metric","greater","lesser"}）做确定性相对比较。

    三态：greater 组指标 > lesser 组指标 → "符合"；两组均取到值但不满足 → "不符"；
    组名失配 / 歧义 / 同组 / 指标缺失或非数值 → "未验证"（保守）。
    """
    metric = str(trend.get("metric") or "").strip()
    greater = str(trend.get("greater") or "").strip()
    lesser = str(trend.get("lesser") or "").strip()
    if not (metric and greater and lesser and metrics_groups):
        return _VERDICT_UNVERIFIED
    greater_name = _match_metrics_group(greater, metrics_groups)
    lesser_name = _match_metrics_group(lesser, metrics_groups)
    if greater_name is None or lesser_name is None or greater_name == lesser_name:
        return _VERDICT_UNVERIFIED
    greater_val = _lookup_metric_value(metrics_groups.get(greater_name), metric)
    lesser_val = _lookup_metric_value(metrics_groups.get(lesser_name), metric)
    if greater_val is None or lesser_val is None:
        return _VERDICT_UNVERIFIED
    return _VERDICT_MATCH if greater_val > lesser_val else _VERDICT_MISMATCH


def _verify_expected_results(
    expected_results: Any,
    exec_result: Optional[Dict[str, Any]],
) -> List[Dict[str, str]]:
    """计划定性预期逐条回验（AC-S5-08，纯确定性，绝不让 LLM 或猜测参与判定）。

    - 新 list 形态（S5-05）：带 trend 结构条目用 ``exec_result.metrics_groups``
      确定性比较；纯文本条目（trend 缺失/None/畸形）一律"未验证"（诚实保守）；
    - 旧 dict 形态（R-5 防御容忍，旧 checkpoint）：逐键降为"未验证"，不比较不崩；
    - 其余形态（None / 非 dict 非 list）→ ``[]``。
    """
    result = exec_result if isinstance(exec_result, dict) else {}
    metrics_groups = result.get("metrics_groups")
    metrics_groups = metrics_groups if isinstance(metrics_groups, dict) else {}

    checks: List[Dict[str, str]] = []

    if isinstance(expected_results, dict):
        for key, value in expected_results.items():
            checks.append({
                "description": f"{key} = {value}（旧形态数值预期，不做机验）",
                "verdict": _VERDICT_UNVERIFIED,
            })
        return checks

    if not isinstance(expected_results, list):
        return checks

    for entry in expected_results:
        if not isinstance(entry, dict):
            description = str(entry).strip() or "（空预期条目）"
            checks.append({"description": description, "verdict": _VERDICT_UNVERIFIED})
            continue
        description = str(entry.get("description") or "").strip() or "（未提供描述）"
        trend = entry.get("trend")
        verdict = (
            _verify_trend(trend, metrics_groups)
            if isinstance(trend, dict)
            else _VERDICT_UNVERIFIED
        )
        checks.append({"description": description, "verdict": verdict})
    return checks


def _determine_conclusion(
    state: GlobalState,
    exec_result: Optional[Dict[str, Any]],
    audit: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """两级结论 + 正交标注判定（AC-S5-07/11，架构 §7.4，纯确定性）。

    返回 ``{"level": "science"|"engineering"|"none", "annotations": List[str],
    "goal_checks": [{"description", "verdict"}]}``：
        - ``engineering`` ⇔ ``exec_result.success == True``（B 档语义原封不动）；
        - ``science`` ⇔ engineering ∧ goal_checks 全"符合"且非空 ∧ annotations 为空；
        - 其余 ``none``。
    正交标注来源映射（任一标注 → 禁 science，AC-S5-11 强制降档通道）：
        - ``simulation`` ← simulation_notice 非空 ∨ 审计 hits 非空
          （**audit=None = 未审计**，不视为命中，区别于 ``{"clean": True}``）；
        - ``credential_degraded`` ← exec_result.degraded_credentials 快照非空；
        - ``incomplete_execution`` ← step_reconciliation 存在未执行步骤 ∨
          budget_truncated（attribution_unavailable **不触发**——R-2 保守语义下
          其成立时 unexecuted_steps 已置空，"无法归属 ≠ 未执行"）。
    exec_result 新键一律 ``.get()`` 防御读（旧 checkpoint 7 键快照兼容，R-6）。
    """
    result = exec_result if isinstance(exec_result, dict) else {}
    success = result.get("success") is True

    annotations: List[str] = []

    notice = state.get("simulation_notice")
    has_notice = notice is not None and bool(str(notice).strip())
    audit_hits = audit.get("hits") if isinstance(audit, dict) else None
    if has_notice or bool(audit_hits):
        annotations.append("simulation")

    if result.get("degraded_credentials"):
        annotations.append("credential_degraded")

    reconciliation = result.get("step_reconciliation")
    unexecuted = (
        reconciliation.get("unexecuted_steps")
        if isinstance(reconciliation, dict)
        else None
    )
    if bool(unexecuted) or result.get("budget_truncated") is True:
        annotations.append("incomplete_execution")

    plan = state.get("reproduction_plan")
    expected_results = plan.get("expected_results") if isinstance(plan, dict) else None
    goal_checks = _verify_expected_results(expected_results, result)

    if not success:
        level = "none"
    elif (
        goal_checks
        and all(check.get("verdict") == _VERDICT_MATCH for check in goal_checks)
        and not annotations
    ):
        level = "science"
    else:
        level = "engineering"

    return {"level": level, "annotations": annotations, "goal_checks": goal_checks}


# ---------------------------------------------------------------------------
# 路径解析与校验（resolve + is_relative_to(WORKSPACE_DIR)，与 C1 code_output_dir 对齐）
# ---------------------------------------------------------------------------


def _workspace_root(state: GlobalState) -> Path:
    """取本次任务的 workspace 根（state.workspace_dir 优先，回退 config.WORKSPACE_DIR）。"""
    workspace = state.get("workspace_dir") or str(WORKSPACE_DIR)
    return Path(workspace)


def _resolve_report_path(state: GlobalState) -> str:
    """解析报告落盘绝对路径并校验落在 workspace 下。

    优先级：
        1. 从 ``state["code_output_dir"]`` 推导 ``Path(code_output_dir).parent /
           "report.md"`` —— 报告与代码同目录（C1 把代码写到
           ``workspace_dir/<arxiv_id>/code``，故报告落
           ``workspace_dir/<arxiv_id>/report.md``，天然对齐，不另起目录段命名）；
        2. ``code_output_dir`` 缺失时回退 ``workspace_dir/<thread>/report.md``，
           ``<thread>`` 取 paper_meta.arxiv_id（缺失回退 "task"），与 C1
           ``_resolve_code_output_dir`` 的 thread 代理一致。

    路径经 ``resolve() + is_relative_to`` 校验（基准为 state 优先的 workspace 根，
    与 C1 ``_resolve_code_output_dir`` 一致）；越界时退回到 workspace 根下的
    ``<thread>/report.md`` 安全落点（绝不越界写）。
    """
    workspace = _workspace_root(state)
    # 校验基准与退回落点统一用 state 优先的 workspace 根（与 C1 _resolve_code_output_dir
    # 一致），避免自定义 workspace_dir 时「校验用模块级、退回用 state」基准错配（DEV-C2-01）。
    workspace_resolved = workspace.resolve()

    code_output_dir = state.get("code_output_dir")
    candidate: Optional[Path] = None
    if code_output_dir:
        candidate = Path(code_output_dir).parent / "report.md"
    else:
        thread = ""
        paper_meta = state.get("paper_meta") or {}
        if isinstance(paper_meta, dict):
            thread = str(paper_meta.get("arxiv_id") or "").strip()
        thread = thread or "task"
        candidate = workspace / thread / "report.md"

    resolved = candidate.resolve()
    if not (resolved == workspace_resolved or resolved.is_relative_to(workspace_resolved)):
        # 越界（如 code_output_dir 被构造到 workspace 外）→ 退回 workspace 下安全落点。
        thread = ""
        paper_meta = state.get("paper_meta") or {}
        if isinstance(paper_meta, dict):
            thread = str(paper_meta.get("arxiv_id") or "").strip()
        thread = thread or "task"
        safe = (workspace / thread / "report.md").resolve()
        logger.warning(
            "[%s] report_path 候选 %s 越界 workspace，退回安全落点 %s",
            NODE_NAME, resolved, safe,
        )
        resolved = safe

    return str(resolved)


def _write_report(state: GlobalState, markdown: str) -> str:
    """把 Markdown 写到校验后的 report_path（父目录幂等创建）。"""
    report_path = _resolve_report_path(state)
    path = Path(report_path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(markdown, encoding="utf-8")
    except OSError as exc:  # noqa: BLE001 — 写失败不应炸节点
        logger.warning("[%s] 报告写入失败 %s: %s", NODE_NAME, report_path, exc)
    return report_path


# ---------------------------------------------------------------------------
# 渲染辅助
# ---------------------------------------------------------------------------


def _md_escape_inline(value: Any) -> str:
    """把单元格值转成单行字符串（管道符转义，避免破坏 Markdown 表格）。"""
    if value is None:
        return "—"
    text = str(value)
    text = text.replace("\n", " ").replace("|", "\\|")
    return text or "—"


def _fmt_metric_value(value: Any) -> str:
    """格式化**标量**指标值（数值保留可读精度，其它原样字符串化）。

    sp5 T-S5-3-4（AC-S5-20 嵌套降维）：dict / list 不再 ``str()`` 整塞单元格——
    正常路径应先经 ``_flatten_entries`` 逐键降维成标量行；此处仅作防御兜底，
    返回条目数摘要而非巨型 repr。
    """
    if value is None:
        return "—"
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, float):
        return f"{value:.4g}"
    if isinstance(value, (dict, list)):
        kind = "嵌套映射" if isinstance(value, dict) else "嵌套列表"
        return f"（{kind}，共 {len(value)} 项，未展开）"
    return _md_escape_inline(value)


#: 嵌套降维最大深度（超过后以省略占位，防病态深嵌套撑爆表格）。
_NEST_MAX_DEPTH: int = 4
#: 标量列表内联展示的最大项数（超过截断并标注总数）。
_LIST_INLINE_MAX: int = 12


def _flatten_entries(label: str, value: Any, depth: int = 0) -> List[tuple]:
    """把嵌套 dict / list 值降维成 ``(标签, 标量值)`` 行序列（AC-S5-20）。

    - dict → 逐键行，标签用 ``.`` 级联（``main_comparison.EvoSkills``）；
    - 纯标量 list → 内联逗号串（截断到 ``_LIST_INLINE_MAX`` 项）；
    - 含嵌套的 list → ``label[i]`` 逐项展开；
    - 深度超过 ``_NEST_MAX_DEPTH`` → 省略占位（绝不 ``str()`` 整塞）。
    """
    if isinstance(value, dict):
        if not value:
            return [(label, "（空映射）")]
        if depth >= _NEST_MAX_DEPTH:
            return [(label, "（嵌套过深，已省略）")]
        rows: List[tuple] = []
        for key in value.keys():
            rows.extend(_flatten_entries(f"{label}.{key}", value[key], depth + 1))
        return rows
    if isinstance(value, list):
        if not value:
            return [(label, "（空列表）")]
        if all(not isinstance(item, (dict, list)) for item in value):
            joined = ", ".join(_fmt_metric_value(item) for item in value[:_LIST_INLINE_MAX])
            if len(value) > _LIST_INLINE_MAX:
                joined += f" …（共 {len(value)} 项）"
            return [(label, joined)]
        if depth >= _NEST_MAX_DEPTH:
            return [(label, "（嵌套过深，已省略）")]
        rows = []
        for i, item in enumerate(value):
            rows.extend(_flatten_entries(f"{label}[{i}]", item, depth + 1))
        return rows
    return [(label, value)]


def _flatten_mapping(mapping: Any) -> Dict[str, Any]:
    """把顶层 dict 的每个键经 ``_flatten_entries`` 降维成扁平 ``标签 → 标量`` 映射。

    非 dict（脏数据）→ ``{}``（防御容忍）；重复标签首见优先（确定性）。
    """
    flat: Dict[str, Any] = {}
    if not isinstance(mapping, dict):
        return flat
    for key in mapping.keys():
        for label, leaf in _flatten_entries(str(key), mapping[key]):
            if label not in flat:
                flat[label] = leaf
    return flat


def _parse_error_category(error_message: str) -> Optional[str]:
    """从 node_errors 的 error_message 解析 ``[error_category=...]`` 前缀（§2.3.2）。"""
    if not isinstance(error_message, str):
        return None
    marker = "[error_category="
    idx = error_message.find(marker)
    if idx == -1:
        return None
    start = idx + len(marker)
    end = error_message.find("]", start)
    if end == -1:
        return None
    return error_message[start:end].strip() or None


def _header(state: GlobalState, form: str) -> List[str]:
    """报告头部（标题 + 元信息），事实层（arxiv_id / title）保留英文/原文。"""
    paper_meta = state.get("paper_meta") or {}
    arxiv_id = ""
    title = ""
    if isinstance(paper_meta, dict):
        arxiv_id = str(paper_meta.get("arxiv_id") or "").strip()
        title = str(paper_meta.get("title") or "").strip()

    lines: List[str] = []
    heading = title or arxiv_id or "论文复现报告"
    lines.append(f"# 论文复现报告：{heading}")
    lines.append("")
    if arxiv_id:
        lines.append(f"- arXiv ID: `{arxiv_id}`")
    if title:
        lines.append(f"- 论文标题（Title）: {title}")
    lines.append(f"- 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"- 报告形态: `{form}`")
    lines.append("")
    return lines


# ---------------------------------------------------------------------------
# S5-04/06 声明块 + 回验节 + 对账节渲染（T-S5-3-4）
# 报告是用户可读文本：内部枚举值不裸露正文，一律配通俗中文说明。
# ---------------------------------------------------------------------------

#: 审计规则内部值 → 用户可读中文说明（架构 §7.3 三规则字面量）。
_AUDIT_RULE_LABELS: Dict[str, str] = {
    "answer_leakage": "答案泄漏（非评估代码直接读取答案字段）",
    "hardcoded_score": "硬编码分数（评分结果由字面量写死）",
    "constant_outcome": "常量结局（评估函数恒返回常量）",
}


def _credential_purpose_map(state: GlobalState) -> Dict[str, str]:
    """从 plan.required_credentials 建 purpose_key → purpose 中文说明映射（S5-01）。

    畸形条目（非 dict / 缺键）防御跳过；查不到的 purpose_key 由调用方降级展示原值。
    """
    plan = state.get("reproduction_plan")
    creds = plan.get("required_credentials") if isinstance(plan, dict) else None
    mapping: Dict[str, str] = {}
    if not isinstance(creds, list):
        return mapping
    for item in creds:
        if not isinstance(item, dict):
            continue
        key = str(item.get("purpose_key") or "").strip()
        purpose = str(item.get("purpose") or "").strip()
        if key and purpose and key not in mapping:
            mapping[key] = purpose
    return mapping


def _render_annotation_notices(
    state: GlobalState,
    conclusion: Dict[str, Any],
    audit: Optional[Dict[str, Any]],
) -> List[str]:
    """正交标注声明块：任一标注 → 报告顶部显著声明（AC-S5-03 第③落点 / AC-S5-11/12）。

    数据源（架构 §7.4）：simulation ← ``state["simulation_notice"]`` 原文 +
    ``audit["hits"]`` 证据（snippet 已脱敏）；credential_degraded ←
    ``exec_result["degraded_credentials"]``（purpose 中文说明查 plan）；
    incomplete_execution ← ``exec_result["step_reconciliation"]`` + ``budget_truncated``。
    """
    annotations = list((conclusion or {}).get("annotations") or [])
    if not annotations:
        return []
    exec_result = state.get("execution_result")
    result = exec_result if isinstance(exec_result, dict) else {}

    lines: List[str] = []
    lines.append("## ⚠️ 重要声明")
    lines.append("")
    lines.append("> 本次复现存在以下需要特别注意的事实，结论口径已据此降档"
                 "（详见\"复现结论\"节）：")
    lines.append("")

    if "simulation" in annotations:
        lines.append("### 模拟/未验证内容")
        lines.append("")
        lines.append("> ⚠️ **模拟/未验证**：本次生成的代码包含模拟实现或未经证实的内容，"
                     "相关产出不能作为论文实验结论的依据。")
        lines.append("")
        notice = state.get("simulation_notice")
        if notice is not None and str(notice).strip():
            lines.append("代码生成阶段的自述声明（原文）：")
            lines.append("")
            for raw in str(notice).splitlines():
                lines.append(f"> {raw}")
            lines.append("")
        hits = audit.get("hits") if isinstance(audit, dict) else None
        if hits:
            lines.append("代码诚实性审计命中证据（片段已脱敏）：")
            lines.append("")
            lines.append("| 命中规则 | 文件 | 行号 | 证据片段 |")
            lines.append("|---|---|---|---|")
            for hit in hits:
                if not isinstance(hit, dict):
                    continue
                rule = str(hit.get("rule") or "").strip()
                label = _AUDIT_RULE_LABELS.get(rule)
                if label:
                    rule_cell = f"{label}（`{rule}`）"
                elif rule:
                    rule_cell = f"`{_md_escape_inline(rule)}`"
                else:
                    rule_cell = "—"
                lines.append(
                    f"| {rule_cell} "
                    f"| `{_md_escape_inline(hit.get('file'))}` "
                    f"| {_md_escape_inline(hit.get('line'))} "
                    f"| {_md_escape_inline(hit.get('snippet'))} |"
                )
            lines.append("")

    if "credential_degraded" in annotations:
        lines.append("### 凭证降级")
        lines.append("")
        lines.append("> ⚠️ 用户已显式选择在缺失以下凭证的情况下降级执行，"
                     "相关环节可能以模拟或跳过方式实现：")
        lines.append("")
        purpose_map = _credential_purpose_map(state)
        for purpose_key in result.get("degraded_credentials") or []:
            key = str(purpose_key)
            purpose = purpose_map.get(key)
            if purpose:
                lines.append(f"- {_md_escape_inline(purpose)}（`{_md_escape_inline(key)}`）")
            else:
                lines.append(f"- `{_md_escape_inline(key)}`")
        lines.append("")

    if "incomplete_execution" in annotations:
        lines.append("### 执行不完整")
        lines.append("")
        recon = result.get("step_reconciliation")
        planned = recon.get("planned") if isinstance(recon, dict) else None
        completed = recon.get("completed") if isinstance(recon, dict) else None
        if isinstance(planned, int) and isinstance(completed, int):
            lines.append(f"> ⚠️ 计划步骤未全部执行完成（已完成 {completed}/{planned} 步），"
                         "详见\"步骤对账\"节。")
        else:
            lines.append("> ⚠️ 计划步骤未全部执行完成，详见\"步骤对账\"节。")
        if result.get("budget_truncated") is True:
            lines.append(">")
            lines.append("> 本次执行因轮次预算耗尽被提前截断，其后的计划步骤未执行。")
        lines.append("")

    return lines


def _render_goal_checks(conclusion: Dict[str, Any]) -> List[str]:
    """"计划目标回验"节：goal_checks 三态表（AC-S5-08）。

    存在"不符/未验证"→ 明示整体结论不作科学复现（完全成功）级别宣告；
    三态字面量复用模块常量（``_VERDICT_MATCH/_VERDICT_MISMATCH/_VERDICT_UNVERIFIED``）。
    """
    checks = (conclusion or {}).get("goal_checks") or []
    lines: List[str] = []
    lines.append("## 计划目标回验")
    lines.append("")
    if not checks:
        lines.append("（复现计划未提供可回验的预期结果，无回验条目。）")
        lines.append("")
        return lines

    lines.append("> 对照复现计划的预期结果逐条回验（三态：符合 / 不符 / 未验证）。"
                 "回验为确定性比较，仅依据本次执行解析出的指标，绝不猜测。")
    lines.append("")
    lines.append("| 计划预期 | 回验结果 |")
    lines.append("|---|---|")
    icons = {
        _VERDICT_MATCH: "✅",
        _VERDICT_MISMATCH: "❌",
        _VERDICT_UNVERIFIED: "⚠️",
    }
    verdicts: List[str] = []
    for check in checks:
        if not isinstance(check, dict):
            continue
        desc = str(check.get("description") or "").strip() or "（未提供描述）"
        verdict = str(check.get("verdict") or _VERDICT_UNVERIFIED)
        verdicts.append(verdict)
        lines.append(f"| {_md_escape_inline(desc)} | {icons.get(verdict, '⚠️')} {verdict} |")
    lines.append("")
    if verdicts and all(v == _VERDICT_MATCH for v in verdicts):
        lines.append("回验小结：全部条目符合计划预期。")
    else:
        # 注意措辞：engineering 报告全文禁"复现成功"字样（AC-S5-07），此处用
        # "科学复现（完全成功）"表达同义且不触碰禁词。
        lines.append("> ⚠️ 回验存在「不符」或「未验证」条目：论文实验结论未得到完全验证，"
                     "整体结论不作科学复现（完全成功）级别的宣告。")
    lines.append("")
    return lines


def _render_step_reconciliation(exec_result: Any) -> List[str]:
    """"步骤对账"节（AC-S5-10 渲染部分 + AC-S5-12 截断声明）。

    - 正常归属："已完成 N/M 步" + 未执行步骤清单 + 计划外命令清单；
    - ``attribution_unavailable=True``（R-2 保守语义，unexecuted_steps 恒空）：
      **不做**"已完成 N/M / 未执行步骤"统计（无法归属 ≠ 未执行，也 ≠ 已执行），
      如实展示 ``extra_commands`` 原始命令清单——红线：禁"0 步未执行"式误导；
    - ``budget_truncated=True`` → 截断显式声明；
    - 旧 checkpoint 快照（无对账数据且未截断，R-6）→ 整节省略，不崩。
    """
    result = exec_result if isinstance(exec_result, dict) else {}
    recon = result.get("step_reconciliation")
    recon = recon if isinstance(recon, dict) and recon else None
    budget_truncated = result.get("budget_truncated") is True
    if recon is None and not budget_truncated:
        return []

    lines: List[str] = []
    lines.append("## 步骤对账")
    lines.append("")
    if recon is not None:
        planned = recon.get("planned")
        executed = recon.get("executed")
        completed = recon.get("completed")
        if recon.get("attribution_unavailable") is True:
            if isinstance(planned, int):
                lines.append(f"- 计划步骤数：{planned}")
                lines.append("")
            lines.append("> ⚠️ **命令归属不可用**：本次实际执行的命令无法与计划步骤"
                         "一一对应，因此不做\"已完成步数\"与\"未执行步骤\"统计"
                         "（无法归属不等于未执行，也不等于已执行）。"
                         "以下如实列出本次真实执行的原始命令清单：")
            lines.append("")
            extra = recon.get("extra_commands") or []
            if extra:
                for cmd in extra:
                    lines.append(f"- `{_md_escape_inline(cmd)}`")
            else:
                lines.append("- （无命令记录）")
            lines.append("")
        else:
            if isinstance(planned, int) and isinstance(completed, int):
                executed_note = (
                    f"，可归属执行 {executed} 步" if isinstance(executed, int) else ""
                )
                lines.append(
                    f"- 已完成 {completed}/{planned} 步（计划 {planned} 步{executed_note}；"
                    "\"已完成\"= 该步归属的全部命令均成功退出）。"
                )
            unexecuted = recon.get("unexecuted_steps") or []
            if unexecuted:
                lines.append("- 未执行的计划步骤：")
                for step in unexecuted:
                    if isinstance(step, dict):
                        idx = step.get("index")
                        name = str(step.get("step_name") or "").strip() or "（未命名步骤）"
                        no = idx + 1 if isinstance(idx, int) else "?"
                        lines.append(f"    - 第 {no} 步：{_md_escape_inline(name)}")
                    else:
                        lines.append(f"    - {_md_escape_inline(step)}")
            else:
                lines.append("- 未执行的计划步骤：无。")
            extra = recon.get("extra_commands") or []
            if extra:
                lines.append("- 计划外命令（真实执行过、但未归属到任何计划步骤）：")
                for cmd in extra:
                    lines.append(f"    - `{_md_escape_inline(cmd)}`")
            lines.append("")
    if budget_truncated:
        lines.append("> ⚠️ **执行被截断**：本次执行因轮次预算耗尽被提前结束，"
                     "其后的计划步骤未执行。")
        lines.append("")
    return lines


# ---------------------------------------------------------------------------
# 三形态渲染
# ---------------------------------------------------------------------------


def _render_full_success(state: GlobalState, conclusion: Dict[str, Any]) -> List[str]:
    """full_success：两级结论卡片 + 回验节 + 对账节 + 指标对比 + artifact + 执行概况。

    sp5 T-S5-3-4（AC-S5-07 措辞红线）：结论卡片按 conclusion.level 输出——
    science → "复现成功（科学复现）"；其余（engineering，full_success 下
    success=True 不会是 none）→ "代码跑通（工程复现），论文实验结论未验证"，
    engineering 报告全文不得出现"复现成功"字样。
    """
    lines: List[str] = []
    exec_result = state.get("execution_result") or {}
    level = (conclusion or {}).get("level")

    # 结论卡片（两级措辞）
    lines.append("## 复现结论")
    lines.append("")
    if level == "science":
        lines.append("> ✅ **复现成功（科学复现）**：代码已在隔离环境中成功执行，"
                     "且\"计划目标回验\"全部条目符合计划预期、无诚实性标注。")
    else:
        # 降档原因指引：有标注 → 指向顶部声明块；否则 → 指向回验节（避免
        # "回验全符合却被标注降档"时指引失准）。
        pointer = (
            "详见报告顶部\"重要声明\"与\"计划目标回验\"节"
            if (conclusion or {}).get("annotations")
            else "详见\"计划目标回验\"节"
        )
        lines.append("> ☑️ **代码跑通（工程复现），论文实验结论未验证**：代码已在"
                     f"隔离环境中成功执行并解析出指标，但论文的实验结论尚未得到验证（{pointer}）。")
    lines.append(">")
    lines.append("> 判定口径（B 档）：执行退出码正常且至少解析出 1 个指标即视为代码跑通。"
                 "下方指标对比表仅做论文值与复现值的并列展示，仅供参考对比，"
                 "**不做硬性结论判定**。")
    lines.append("")

    # 计划目标回验（AC-S5-08）+ 步骤对账（AC-S5-10）
    lines.extend(_render_goal_checks(conclusion))
    lines.extend(_render_step_reconciliation(exec_result))

    # 指标对比表（baseline vs 复现值，仅对比不判定；多组按组展开）
    lines.extend(_render_metrics_comparison(state, exec_result))

    # artifact 清单
    artifacts = exec_result.get("artifacts") or []
    lines.append("## 产物清单（Artifacts）")
    lines.append("")
    if artifacts:
        for art in artifacts:
            lines.append(f"- `{_md_escape_inline(art)}`")
    else:
        lines.append("- （本次执行未收集到产物文件）")
    lines.append("")

    # 执行概况（runtime / env）
    lines.append("## 执行概况")
    lines.append("")
    runtime = exec_result.get("runtime_seconds")
    if runtime is not None:
        lines.append(f"- 执行总耗时（runtime）: {_fmt_metric_value(runtime)} 秒")
    lines.extend(_render_environment_lines(exec_result.get("environment_info")))
    code_dir = state.get("code_output_dir")
    if code_dir:
        lines.append(f"- 代码位置（code_output_dir）: `{_md_escape_inline(code_dir)}`")
    lines.append("")
    return lines


def _render_environment_lines(env_info: Any) -> List[str]:
    """执行概况的环境信息行（S5-10：key_packages 逐包渲染 + 嵌套值降维）。"""
    lines: List[str] = []
    if not isinstance(env_info, dict) or not env_info:
        return lines
    lines.append("- 环境信息（environment）:")
    for key, value in sorted(env_info.items(), key=lambda kv: str(kv[0])):
        k = str(key)
        if k == "key_packages":
            pkgs = [p.strip() for p in str(value or "").split(",") if p.strip()]
            if pkgs:
                lines.append("    - 关键依赖包（key_packages）:")
                for pkg in pkgs:
                    lines.append(f"        - `{_md_escape_inline(pkg)}`")
            else:
                lines.append("    - 关键依赖包（key_packages）: —")
        elif isinstance(value, (dict, list)):
            for label, leaf in _flatten_entries(k, value):
                lines.append(f"    - `{_md_escape_inline(label)}`: {_fmt_metric_value(leaf)}")
        else:
            lines.append(f"    - `{_md_escape_inline(k)}`: {_md_escape_inline(value)}")
    return lines


def _comparison_table(
    labels: List[str],
    baseline_flat: Dict[str, Any],
    repro_flat: Dict[str, Any],
) -> List[str]:
    """两列对比表（论文 baseline vs 本次复现值，AC-S5-09 无 expected 列）。"""
    rows: List[str] = []
    rows.append("| 指标 (Metric) | 论文 baseline | 本次复现值 |")
    rows.append("|---|---|---|")
    for label in labels:
        rows.append(
            f"| `{_md_escape_inline(label)}` "
            f"| {_fmt_metric_value(baseline_flat.get(label))} "
            f"| {_fmt_metric_value(repro_flat.get(label))} |"
        )
    return rows


def _render_metrics_comparison(state: GlobalState, exec_result: Any) -> List[str]:
    """指标对比：并列论文 baseline 与本次复现 metrics（仅展示不判定）。

    sp5 T-S5-3-4（AC-S5-09/20）：
        - 删"计划 expected"列（S5-05 定性化后计划不再有数值预期；旧 dict 形态
          也不渲染 expected 列，防御容忍）；
        - ``metrics_groups`` 非空 → 按组展开子表（组名 = 产物目录相对路径）；
        - 嵌套指标值经 ``_flatten_entries`` 逐键行降维，无巨型 dict 字符串。
    """
    lines: List[str] = []
    lines.append("## 指标对比")
    lines.append("")

    result = exec_result if isinstance(exec_result, dict) else {}
    repro_flat = _flatten_mapping(result.get("metrics"))
    metrics_groups = result.get("metrics_groups")
    metrics_groups = metrics_groups if isinstance(metrics_groups, dict) else {}
    analysis = state.get("paper_analysis") or {}
    baseline = analysis.get("baseline_results") if isinstance(analysis, dict) else {}
    baseline_flat = _flatten_mapping(baseline)

    if not repro_flat and not baseline_flat and not metrics_groups:
        lines.append("（无可对比指标：论文 baseline / 复现 metrics 均为空。）")
        lines.append("")
        return lines

    lines.append("> 下表并列论文报告值（baseline）与本次复现值，仅供对比参考，"
                 "**不做任何硬性结论**。")
    lines.append("")

    # 主实验表（指标名全集 = 复现 ∪ baseline，事实层指标名保留英文不翻译）。
    main_labels: List[str] = list(repro_flat.keys())
    for label in baseline_flat.keys():
        if label not in main_labels:
            main_labels.append(label)
    if main_labels:
        if metrics_groups:
            lines.append("### 主实验指标")
            lines.append("")
        lines.extend(_comparison_table(main_labels, baseline_flat, repro_flat))
        lines.append("")

    # 多组展开（S5-10：组名 = summary.json 相对 outputs/ 的父目录路径）。
    if metrics_groups:
        lines.append("### 分组实验指标")
        lines.append("")
        lines.append("按执行产物解析出的实验分组逐组展示（组名为产物目录相对路径）：")
        lines.append("")
        for group_name in sorted(str(n) for n in metrics_groups.keys()):
            group_flat = _flatten_mapping(metrics_groups.get(group_name))
            lines.append(f"#### 组 `{_md_escape_inline(group_name)}`")
            lines.append("")
            if group_flat:
                lines.extend(
                    _comparison_table(list(group_flat.keys()), baseline_flat, group_flat)
                )
            else:
                lines.append("（该组未解析出指标字段。）")
            lines.append("")
    return lines


def _render_code_only(state: GlobalState) -> List[str]:
    """code_only：仅生成代码结论卡片 + 代码位置 + deliverables，无指标章节。"""
    lines: List[str] = []
    lines.append("## 复现结论")
    lines.append("")
    lines.append("> 📦 **仅生成代码、未执行**：本次运行处于 code_only 模式，"
                 "系统仅生成复现代码，未在沙箱中实际执行，因此无执行指标。")
    lines.append("")

    # 代码位置
    lines.append("## 代码位置")
    lines.append("")
    code_dir = state.get("code_output_dir")
    if code_dir:
        lines.append(f"- 代码目录（code_output_dir）: `{_md_escape_inline(code_dir)}`")
    else:
        lines.append("- （未记录代码目录 code_output_dir）")
    lines.append("")

    # deliverables 清单
    plan = state.get("reproduction_plan") or {}
    deliverables = plan.get("deliverables") if isinstance(plan, dict) else []
    deliverables = deliverables or []
    lines.append("## 交付物清单（Deliverables）")
    lines.append("")
    if deliverables:
        for d in deliverables:
            lines.append(f"- {_md_escape_inline(d)}")
    else:
        lines.append("- （复现计划未列出 deliverables）")
    lines.append("")
    return lines


def _render_degraded(state: GlobalState, conclusion: Dict[str, Any]) -> List[str]:
    """degraded：未成功结论 + 回验/对账节 + 降级原因 + node_errors + 修复历程 + 保留代码。

    sp5 T-S5-3-4：回验节与对账节插在结论卡片之后、"降级原因"之前（降级路径同样
    需要"计划走到哪一步/预期是否验证"的诚实呈现；置于"修复历程"之前也避免扰动
    既有表格行数断言）。
    """
    lines: List[str] = []
    lines.append("## 复现结论")
    lines.append("")
    lines.append("> ⚠️ **未成功复现（降级）**：本次未能完成端到端复现，"
                 "系统保留了已生成的代码与产物供人工接管。")
    lines.append("")

    # 计划目标回验 + 步骤对账（S5-04/06，降级路径同样渲染）
    lines.extend(_render_goal_checks(conclusion))
    lines.extend(_render_step_reconciliation(state.get("execution_result")))

    # 降级原因 + 降级节点
    degraded_nodes = state.get("degraded_nodes") or []
    lines.append("## 降级原因")
    lines.append("")
    if degraded_nodes:
        lines.append("- 降级节点（degraded_nodes）: "
                     + ", ".join(f"`{_md_escape_inline(n)}`" for n in degraded_nodes))
    else:
        lines.append("- 降级节点（degraded_nodes）: （无显式降级节点记录）")
    exec_result = state.get("execution_result")
    if isinstance(exec_result, dict):
        errs = exec_result.get("errors") or []
        if errs:
            lines.append("- 执行错误摘要（execution_result.errors）:")
            for e in errs:
                lines.append(f"    - {_md_escape_inline(e)}")
    user_decision = state.get("user_fix_decision")
    if user_decision:
        lines.append(f"- 用户处置决策（user_fix_decision）: `{_md_escape_inline(user_decision)}`")
    lines.append("")

    # node_errors 摘要（解析 [error_category=...] 前缀）
    lines.extend(_render_node_errors(state))

    # fix_loop_history 修复历程
    lines.extend(_render_fix_loop_history(state))

    # 保留的代码与产物
    lines.append("## 保留的代码与产物")
    lines.append("")
    code_dir = state.get("code_output_dir")
    if code_dir:
        lines.append(f"- 代码目录（code_output_dir）: `{_md_escape_inline(code_dir)}`")
    else:
        lines.append("- （未记录代码目录 code_output_dir）")
    if isinstance(exec_result, dict):
        artifacts = exec_result.get("artifacts") or []
        if artifacts:
            lines.append("- 已保留产物（artifacts）:")
            for art in artifacts:
                lines.append(f"    - `{_md_escape_inline(art)}`")
    lines.append("")
    return lines


def _render_node_errors(state: GlobalState) -> List[str]:
    """node_errors 摘要表（解析 error_category 细分类前缀）。"""
    lines: List[str] = []
    node_errors = state.get("node_errors") or []
    lines.append("## 节点错误摘要（Node Errors）")
    lines.append("")
    if not node_errors:
        lines.append("（无 node_errors 记录。）")
        lines.append("")
        return lines

    lines.append("| 节点 | 错误类型 | 错误分类 (error_category) | 摘要 |")
    lines.append("|---|---|---|---|")
    for err in node_errors:
        if not isinstance(err, dict):
            continue
        node_name = err.get("node_name", "")
        error_type = err.get("error_type", "")
        message = err.get("error_message", "")
        category = _parse_error_category(message) or "—"
        lines.append(
            f"| `{_md_escape_inline(node_name)}` "
            f"| `{_md_escape_inline(error_type)}` "
            f"| `{_md_escape_inline(category)}` "
            f"| {_md_escape_inline(message)} |"
        )
    lines.append("")
    return lines


def _render_fix_loop_history(state: GlobalState) -> List[str]:
    """fix_loop_history 修复历程（修复几轮、每轮什么错、什么策略）。"""
    lines: List[str] = []
    history = state.get("fix_loop_history") or []
    fix_count = state.get("fix_loop_count", 0) or 0
    lines.append("## 修复历程（Fix Loop History）")
    lines.append("")
    if not history:
        lines.append(f"- 累计修复回合数（fix_loop_count）: {fix_count}")
        lines.append("- （无逐轮修复记录 fix_loop_history。）")
        lines.append("")
        return lines

    lines.append(f"共经历 {len(history)} 轮自动修复（fix_loop_count = {fix_count}）：")
    lines.append("")
    lines.append("| 轮次 | 错误分类 (error_category) | 错误摘要 | 修复策略 |")
    lines.append("|---|---|---|---|")
    for rec in history:
        if not isinstance(rec, dict):
            continue
        round_no = rec.get("round_number", "")
        category = rec.get("error_category", "")
        summary = rec.get("error_summary", "")
        strategy = rec.get("fix_strategy", "")
        lines.append(
            f"| {_md_escape_inline(round_no)} "
            f"| `{_md_escape_inline(category)}` "
            f"| {_md_escape_inline(summary)} "
            f"| {_md_escape_inline(strategy)} |"
        )
    lines.append("")
    return lines


def _render_report(
    state: GlobalState,
    form: str,
    conclusion: Optional[Dict[str, Any]] = None,
    audit: Optional[Dict[str, Any]] = None,
) -> str:
    """按形态拼装完整 Markdown（sp5 T-S5-3-4：显式消费 conclusion + audit）。

    ``conclusion`` / ``audit`` 由 ``reporting()`` 入口显式传入（不塞 state）；
    两参带默认值以兼容既有 2 参调用（sp3 e3_reinforce 交叉验证用例）——判定为
    纯确定性函数，缺省时安全重算（audit 缺省 None = 未审计，不触发 simulation）。
    """
    if conclusion is None:
        conclusion = _determine_conclusion(state, state.get("execution_result"), audit)
    lines: List[str] = []
    lines.extend(_header(state, form))
    # 正交标注声明块：任一标注 → 报告顶部显著声明（AC-S5-11，三形态一致）。
    lines.extend(_render_annotation_notices(state, conclusion, audit))
    if form == "full_success":
        lines.extend(_render_full_success(state, conclusion))
    elif form == "code_only":
        lines.extend(_render_code_only(state))
    else:  # degraded
        lines.extend(_render_degraded(state, conclusion))
    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# 节点入口（纯读，仅返回 report_path + current_step + honesty_audit；CP-C2-5 红线）
# ---------------------------------------------------------------------------


def reporting(state: GlobalState) -> dict:
    """步骤 7：生成三形态 Markdown 复现报告（纯函数式，无 LLM、无 interrupt）。

    **CP-C2-5 红线（sp5 T-S5-3-2 显式扩展）**：reporting 是终点消费者，只读不改
    ——返回 dict **仅含** ``report_path``、``current_step`` 和 ``honesty_audit``
    （单值 last-write-wins），绝不返回 / 覆盖 ``node_errors`` /
    ``degraded_nodes`` / ``fix_loop_history`` 等任何 list 字段。

    **诚实性审计（S5-03，架构 §1）**：入口处、``_determine_report_form`` 之前对
    最终 ``code_output_dir`` 调用 ``audit_code_dir`` **恰好一次**（修复循环收敛后
    审计最终代码）。判空契约在 reporting 侧：``code_output_dir`` 缺失 / None /
    空串 → ``honesty_audit=None``（未审计语义），不调用；目录不存在等异常路径由
    ``audit_code_dir`` 自身容忍（WARNING + 空结果），命中绝不阻断报告生成。

    **结论判定（S5-04，T-S5-3-3，架构 §7.4）**：审计之后、三形态判定之前调用
    ``_determine_conclusion``（纯确定性，零 LLM）算出两级结论 + 正交标注 +
    goal_checks；conclusion 经 ``_render_report`` 显式参数消费（T-S5-3-4 渲染：
    两级措辞 / 声明块 / 回验节 / 对账节 / 删列 / 降维），**不进返回契约**。
    """
    code_output_dir = state.get("code_output_dir")
    honesty_audit: Optional[Dict[str, Any]] = (
        audit_code_dir(code_output_dir) if code_output_dir else None
    )

    # S5-04（T-S5-3-3）：两级结论 + 正交标注 + 定性目标回验——紧随审计之后、
    # 三形态判定之前算好（纯确定性，audit 直接喂本地变量；conclusion 为报告内
    # 消费，经 _render_report 显式参数传递（T-S5-3-4），不进返回契约）。
    exec_result = state.get("execution_result")
    conclusion = _determine_conclusion(state, exec_result, honesty_audit)

    form = _determine_report_form(state)
    markdown = _render_report(state, form, conclusion, honesty_audit)
    report_path = _write_report(state, markdown)
    audit_summary = "skipped" if honesty_audit is None else (
        "clean" if honesty_audit.get("clean")
        else f"{len(honesty_audit.get('hits') or [])} hits"
    )
    logger.info(
        "[%s] 报告生成: form=%s audit=%s conclusion=%s annotations=[%s] -> %s",
        NODE_NAME, form, audit_summary, conclusion["level"],
        ",".join(conclusion["annotations"]), report_path,
    )
    return {
        "report_path": report_path,
        "current_step": NODE_NAME,
        "honesty_audit": honesty_audit,
    }
