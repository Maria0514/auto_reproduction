"""reporting 节点（S3-05）：纯函数式生成三形态 Markdown 复现报告。

把 ``graph.py`` 中的 ``reporting`` 占位替换为真实节点。**纯读、无 LLM、无
interrupt**：只读全局状态，按形态拼装 Markdown，写入 ``report_path``，返回
``{"report_path": ..., "current_step": "reporting"}``。

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

**红线（CP-C2-5）**：reporting 是终点消费者，只读不改——返回 dict **仅含**
``report_path`` 和 ``current_step``，绝不返回 / 覆盖 ``node_errors`` /
``degraded_nodes`` / ``fix_loop_history``（避免无意覆盖上游累积的列表）。
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from config import WORKSPACE_DIR
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
    """格式化指标值（数值保留可读精度，其它原样字符串化）。"""
    if value is None:
        return "—"
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, float):
        return f"{value:.4g}"
    return _md_escape_inline(value)


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
# 三形态渲染
# ---------------------------------------------------------------------------


def _render_full_success(state: GlobalState) -> List[str]:
    """full_success：成功结论卡片 + 指标对比表 + artifact 清单 + 执行概况。"""
    lines: List[str] = []
    exec_result = state.get("execution_result") or {}

    # 结论卡片（成功）
    lines.append("## 复现结论")
    lines.append("")
    lines.append("> ✅ **复现成功**：代码已在隔离环境中成功执行并解析出关键指标。")
    lines.append(">")
    lines.append("> 判定口径（B 档）：执行退出码正常且至少解析出 1 个指标。"
                 "下方指标对比表仅做论文值与复现值的并列展示，仅供参考对比，"
                 "**不做硬性结论判定**。")
    lines.append("")

    # 指标对比表（baseline / expected vs 复现值，仅对比不判定）
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
    env_info = exec_result.get("environment_info") or {}
    if isinstance(env_info, dict) and env_info:
        lines.append("- 环境信息（environment）:")
        for k in sorted(env_info.keys()):
            lines.append(f"    - `{_md_escape_inline(k)}`: {_md_escape_inline(env_info[k])}")
    code_dir = state.get("code_output_dir")
    if code_dir:
        lines.append(f"- 代码位置（code_output_dir）: `{_md_escape_inline(code_dir)}`")
    lines.append("")
    return lines


def _render_metrics_comparison(state: GlobalState, exec_result: Dict[str, Any]) -> List[str]:
    """指标对比表：并列论文 baseline / expected 与本次复现 metrics（仅展示不判定）。"""
    lines: List[str] = []
    lines.append("## 指标对比")
    lines.append("")

    repro_metrics = exec_result.get("metrics") or {}
    analysis = state.get("paper_analysis") or {}
    baseline = analysis.get("baseline_results") if isinstance(analysis, dict) else {}
    baseline = baseline or {}
    plan = state.get("reproduction_plan") or {}
    expected = plan.get("expected_results") if isinstance(plan, dict) else {}
    expected = expected or {}

    # 指标名全集（事实层指标名保留英文，不翻译）。
    metric_names: List[str] = []
    for src in (repro_metrics, baseline, expected):
        if isinstance(src, dict):
            for k in src.keys():
                if k not in metric_names:
                    metric_names.append(str(k))

    if not metric_names:
        lines.append("（无可对比指标：论文 baseline / 计划 expected / 复现 metrics 均为空。）")
        lines.append("")
        return lines

    lines.append("> 下表并列论文报告值（baseline / expected）与本次复现值，仅供对比参考，"
                 "**不做任何硬性结论**。")
    lines.append("")
    lines.append("| 指标 (Metric) | 论文 baseline | 计划 expected | 本次复现值 |")
    lines.append("|---|---|---|---|")
    for name in metric_names:
        b = baseline.get(name) if isinstance(baseline, dict) else None
        e = expected.get(name) if isinstance(expected, dict) else None
        r = repro_metrics.get(name) if isinstance(repro_metrics, dict) else None
        lines.append(
            f"| `{_md_escape_inline(name)}` "
            f"| {_fmt_metric_value(b)} "
            f"| {_fmt_metric_value(e)} "
            f"| {_fmt_metric_value(r)} |"
        )
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


def _render_degraded(state: GlobalState) -> List[str]:
    """degraded：未成功结论 + 降级原因 + node_errors 摘要 + 修复历程 + 保留代码。"""
    lines: List[str] = []
    lines.append("## 复现结论")
    lines.append("")
    lines.append("> ⚠️ **未成功复现（降级）**：本次未能完成端到端复现，"
                 "系统保留了已生成的代码与产物供人工接管。")
    lines.append("")

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


def _render_report(state: GlobalState, form: str) -> str:
    """按形态拼装完整 Markdown。"""
    lines: List[str] = []
    lines.extend(_header(state, form))
    if form == "full_success":
        lines.extend(_render_full_success(state))
    elif form == "code_only":
        lines.extend(_render_code_only(state))
    else:  # degraded
        lines.extend(_render_degraded(state))
    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# 节点入口（纯读，仅返回 report_path + current_step；CP-C2-5 红线）
# ---------------------------------------------------------------------------


def reporting(state: GlobalState) -> dict:
    """步骤 7：生成三形态 Markdown 复现报告（纯函数式，无 LLM、无 interrupt）。

    **CP-C2-5 红线**：reporting 是终点消费者，只读不改——返回 dict **仅含**
    ``report_path`` 和 ``current_step``，绝不返回 / 覆盖 ``node_errors`` /
    ``degraded_nodes`` / ``fix_loop_history`` 等任何 list 字段。
    """
    form = _determine_report_form(state)
    markdown = _render_report(state, form)
    report_path = _write_report(state, markdown)
    logger.info("[%s] 报告生成: form=%s -> %s", NODE_NAME, form, report_path)
    return {"report_path": report_path, "current_step": NODE_NAME}
