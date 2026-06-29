"""S3-10 Streamlit 页面 5：结果报告页（Sprint 3 任务 E3）。

架构参考：sprint3/architecture.md §2.6.3 / PRD §2.10 页面 5。
dev-plan：sprint3/dev-plan.md 任务 E3（CP-E3-1~4，L601-621）。

页面职责（架构 §2.6.3 / PRD §2.10）::

    纯只读终态页：``poll_state`` 读 ``report_path`` → 读该 Markdown 文件 →
    ``st.markdown`` 完整渲染。在 Markdown 全文之上额外提供结构化区块：
        - 顶部三形态结论卡片（复现成功 / 仅生成代码 / 未成功复现 + 降级原因）；
        - 指标对比表（论文 baseline / 计划 expected vs 本次复现，**B 档仅展示不硬判定**，
          与 Q-S3-01 一致，绝不出现「达标 / 不达标」结论）；
        - artifact 清单（``execution_result.artifacts``，可定位）；
        - 修复历程（``fix_loop_history`` 逐轮）；
        - 代码位置（``code_output_dir``）+ deliverables（``reproduction_plan.deliverables``）。
    底部「返回输入页开启新任务」出口（沿用 sp2 终止后出口范式：切 input 页 +
    解除提交锁 ``_input_submitted``，使输入页控件恢复可交互）。

页面入口约定（沿用 sp2 D3/D4/D5 先例）::

    主名 ``render``，模块级别名 ``render_result_report_page = render``，
    ``__all__ = ["render", "render_result_report_page"]``。app.py ``_PAGE_MAP`` 已按
    ("ui.pages.result_report", "render_result_report_page") 预留（E1 接入，
    current_page == config.STREAMLIT_PAGE_REPORT）。

三形态判定契约（与 C2 reporting 对齐，**不臆造**）::

    直接复用 ``core.nodes.reporting._determine_report_form``（reporting.py 是写
    ``report_path`` 的权威源，UI 形态卡片必须与报告正文同一判定，避免两份不一致结论）：
        1. execution_mode == CODE_ONLY → "code_only"（仅生成代码）；
        2. execution_result.success is True → "full_success"（复现成功）；
        3. 其余（含 execution_result is None / success=False / export_code 降级）→ "degraded"。

F5 限制（沿用 sp2 §2.10 边界）::

    页面刷新（F5）后 session_state 丢失但 SqliteSaver 保留状态——**不提供从已有
    thread_id 恢复入口**（归 v1.x），与 sp2 input/progress/review 三页行为一致。

防御式编码：state / report_path / 各嵌套字段一律 .get(...) 取默认空值，绝不让
KeyError / NoneType subscript 崩页面。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import streamlit as st

from config import STREAMLIT_PAGE_INPUT
# 复用 reporting 节点的三形态判定（reporting.py 仅 import config + core.state，轻量，
# 不拉起 LLM / langchain 重链路）——形态卡片与报告正文同一判定，杜绝两份不一致结论。
from core.nodes.reporting import _determine_report_form

logger = logging.getLogger(__name__)

__all__ = ["render", "render_result_report_page"]


# session_state 键约定（与 sp2 app.py / paper_input / analysis_progress 一致）。
_KEY_THREAD_ID = "thread_id"
_KEY_CURRENT_PAGE = "current_page"
# 输入页提交锁（paper_input._KEY_SUBMITTED）——返回输入页时必须解锁，否则输入页
# 控件仍 disabled=submitted（与 analysis_progress._reset_to_input_page 同款）。
_KEY_INPUT_SUBMITTED = "_input_submitted"


# 三形态 → 结论卡片文案 / 配色（mock 风格：原生 HTML div 写死十六进制，避开 shadcn
# iframe Tailwind tree-shake 坑，与 plan_review / analysis_progress 同范式）。
# (标题, 描述, 背景, 边框, 文字色)。
_FORM_CARD_SPEC: Dict[str, Tuple[str, str, str, str, str]] = {
    "full_success": (
        "✅ 复现成功",
        "代码已在隔离环境中成功执行并解析出关键指标。下方指标对比仅做论文值与复现值的"
        "并列展示，仅供参考对比，不做硬性结论判定。",
        "#dcfce7",
        "#16a34a",
        "#166534",
    ),
    "code_only": (
        "📦 仅生成代码",
        "本次运行处于 code_only 模式，系统仅生成复现代码，未在沙箱中实际执行，因此无执行指标。",
        "#eff6ff",
        "#2563eb",
        "#1e40af",
    ),
    "degraded": (
        "⚠️ 未成功复现（降级）",
        "本次未能完成端到端复现，系统保留了已生成的代码与产物供人工接管。降级原因见下方。",
        "#fef2f2",
        "#dc2626",
        "#991b1b",
    ),
}


# =========================================================================== #
# 纯函数内核（模块级，可 import 直测；逻辑层断言优先，与 sp2 范式一致）
# =========================================================================== #
def _load_report_markdown(report_path: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """读取 report_path 指向的 Markdown 文件，返回 (markdown | None, error | None)。

    防御式：report_path 为空 / 文件不存在 / 读失败均不抛，转为可读错误文案，由
    render 层 st.warning / st.error 兜底（不崩页）。
    """
    if not report_path:
        return None, "报告路径尚未生成（report_path 为空）。"
    try:
        path = Path(report_path)
    except (TypeError, ValueError) as exc:  # noqa: BLE001 — 非法路径不应炸页
        return None, f"报告路径非法：{exc}"
    if not path.exists():
        return None, f"报告文件不存在：{report_path}"
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:  # noqa: BLE001 — 读失败/编码错均仅降级提示（BUG-S3-E3-01）
        logger.warning("[result_report] 读取报告失败 %s: %s", report_path, exc)
        return None, f"报告文件读取失败：{exc}"
    return text, None


def _metric_comparison_rows(state: Dict) -> List[Dict[str, Any]]:
    """构造指标对比表行（论文 baseline / 计划 expected vs 本次复现，仅并列不判定）。

    与 reporting._render_metrics_comparison 同口径：指标名全集取三方并集（事实层指标名
    保留英文不翻译），任一方缺该指标用 None 占位（render 层渲染为「—」）。**绝不**计算
    达标/不达标——B 档展示口径（Q-S3-01）。

    防御式 .get：state 为 None / 各子结构非 dict 均不抛，返回空列表。
    """
    state = state or {}
    exec_result = state.get("execution_result") or {}
    repro_metrics = exec_result.get("metrics") if isinstance(exec_result, dict) else {}
    repro_metrics = repro_metrics or {}

    analysis = state.get("paper_analysis") or {}
    baseline = analysis.get("baseline_results") if isinstance(analysis, dict) else {}
    baseline = baseline or {}

    plan = state.get("reproduction_plan") or {}
    expected = plan.get("expected_results") if isinstance(plan, dict) else {}
    expected = expected or {}

    metric_names: List[str] = []
    for src in (repro_metrics, baseline, expected):
        if isinstance(src, dict):
            for k in src.keys():
                if str(k) not in metric_names:
                    metric_names.append(str(k))

    rows: List[Dict[str, Any]] = []
    for name in metric_names:
        rows.append(
            {
                "指标 (Metric)": name,
                "论文 baseline": baseline.get(name) if isinstance(baseline, dict) else None,
                "计划 expected": expected.get(name) if isinstance(expected, dict) else None,
                "本次复现值": repro_metrics.get(name) if isinstance(repro_metrics, dict) else None,
            }
        )
    return rows


def _fix_loop_rows(state: Dict) -> List[Dict[str, Any]]:
    """构造修复历程逐轮表行（fix_loop_history，每轮：轮次 / 分类 / 摘要 / 策略）。

    防御式：history 为 None / 非 list / 元素非 dict 均跳过，不抛。
    """
    state = state or {}
    history = state.get("fix_loop_history") or []
    rows: List[Dict[str, Any]] = []
    for rec in history:
        if not isinstance(rec, dict):
            continue
        rows.append(
            {
                "轮次": rec.get("round_number", ""),
                "错误分类 (error_category)": rec.get("error_category", ""),
                "错误摘要": rec.get("error_summary", ""),
                "修复策略": rec.get("fix_strategy", ""),
            }
        )
    return rows


def _artifact_list(state: Dict) -> List[str]:
    """取 execution_result.artifacts（产物路径清单，可定位）。防御式：缺失返回空列表。"""
    state = state or {}
    exec_result = state.get("execution_result") or {}
    if not isinstance(exec_result, dict):
        return []
    artifacts = exec_result.get("artifacts") or []
    return [str(a) for a in artifacts if a]


def _deliverables_list(state: Dict) -> List[str]:
    """取 reproduction_plan.deliverables（交付物清单）。防御式：缺失返回空列表。"""
    state = state or {}
    plan = state.get("reproduction_plan") or {}
    if not isinstance(plan, dict):
        return []
    deliverables = plan.get("deliverables") or []
    return [str(d) for d in deliverables if d]


def _degradation_reasons(state: Dict) -> Dict[str, Any]:
    """汇总降级原因（degraded_nodes / execution_result.errors / user_fix_decision）。

    返回 dict（render 层据此展示降级原因区块）；防御式 .get，缺失给空值。
    """
    state = state or {}
    exec_result = state.get("execution_result")
    errors: List[str] = []
    if isinstance(exec_result, dict):
        errors = [str(e) for e in (exec_result.get("errors") or []) if e]
    return {
        "degraded_nodes": [str(n) for n in (state.get("degraded_nodes") or [])],
        "execution_errors": errors,
        "user_fix_decision": state.get("user_fix_decision"),
    }


# =========================================================================== #
# 私有渲染区块
# =========================================================================== #
def _get_controller():
    """从 session_state 取 D2 GraphController 单例（与 app.py::_get_controller 一致）。"""
    from app import _get_controller as _app_get_controller

    return _app_get_controller()


def _reset_to_input_page() -> None:
    """切回输入页并解除提交锁，使输入页控件恢复可交互（与 sp2 analysis_progress 同款）。

    「返回输入页开启新任务」唯一出口：把「切页 + 解锁」绑成一个动作，杜绝「切页但漏解锁
    → 输入页 disabled=submitted 全控件冻结」的不对称 BUG（sp2 analysis_progress 已踩过）。
    调用方负责随后 st.rerun()。
    """
    st.session_state[_KEY_CURRENT_PAGE] = STREAMLIT_PAGE_INPUT
    # 清掉输入页提交锁（paper_input._KEY_SUBMITTED = "_input_submitted"），否则回到输入页
    # 时 arxiv 输入框 / 搜索框 / 各按钮仍 disabled=submitted。
    st.session_state[_KEY_INPUT_SUBMITTED] = False
    # 复现已结束：thread_id 仅承载「当前任务」语义，开启新任务前清空，避免本页 / 其它页
    # 在新任务尚未发起前误读到旧 thread 的终态（与 sp2「不提供 thread_id 恢复入口」一致）。
    st.session_state[_KEY_THREAD_ID] = None


def _render_conclusion_card(form: str) -> None:
    """渲染顶部三形态结论卡片（HTML div 写死十六进制，AppTest 看不到 iframe 故用原生 HTML）。"""
    title, desc, bg, border, color = _FORM_CARD_SPEC.get(
        form, _FORM_CARD_SPEC["degraded"]
    )
    # 关键文案同时落到 st.markdown（AppTest 可见纯文本）+ HTML 卡片（视觉）。
    st.markdown(f"### {title}")
    st.markdown(
        f"<div style='background:{bg}; border:1px solid {border};"
        f" border-left:4px solid {border}; color:{color}; border-radius:10px;"
        f" padding:16px 18px; margin:8px 0 16px 0; font-size:14px;'>"
        f"<strong>{title}</strong><br/>{desc}</div>",
        unsafe_allow_html=True,
    )


def _render_metrics_section(state: Dict) -> None:
    """渲染指标对比表（B 档仅并列展示，不硬判定）。"""
    st.markdown("### 📊 指标对比")
    rows = _metric_comparison_rows(state)
    if not rows:
        st.caption("无可对比指标：论文 baseline / 计划 expected / 复现 metrics 均为空。")
        return
    st.caption(
        "下表并列论文报告值（baseline / expected）与本次复现值，仅供对比参考，"
        "不做任何硬性达标结论。"
    )
    # 缺值用「—」占位（与 reporting 报告正文一致），数值原样字符串化。
    display_rows = [
        {k: ("—" if v is None else v) for k, v in row.items()} for row in rows
    ]
    st.table(display_rows)


def _render_artifacts_section(state: Dict) -> None:
    """渲染 artifact 清单（execution_result.artifacts，可定位）。"""
    st.markdown("### 📦 产物清单（Artifacts）")
    artifacts = _artifact_list(state)
    if not artifacts:
        st.caption("本次执行未收集到产物文件。")
        return
    for art in artifacts:
        st.markdown(f"- `{art}`")


def _render_fix_history_section(state: Dict) -> None:
    """渲染修复历程（fix_loop_history 逐轮）。"""
    st.markdown("### 🔧 修复历程（Fix Loop History）")
    fix_count = state.get("fix_loop_count", 0) or 0
    rows = _fix_loop_rows(state)
    if not rows:
        st.caption(f"累计修复回合数（fix_loop_count）：{fix_count}；无逐轮修复记录。")
        return
    st.caption(f"共经历 {len(rows)} 轮自动修复（fix_loop_count = {fix_count}）：")
    st.table(rows)


def _render_code_and_deliverables_section(state: Dict) -> None:
    """渲染代码位置（code_output_dir）+ 交付物清单（deliverables）。"""
    st.markdown("### 📁 代码位置与交付物")
    code_dir = state.get("code_output_dir")
    if code_dir:
        st.markdown(f"- 代码目录（code_output_dir）：`{code_dir}`")
    else:
        st.markdown("- 代码目录（code_output_dir）：（未记录）")

    deliverables = _deliverables_list(state)
    if deliverables:
        st.markdown("**交付物（Deliverables）**")
        for d in deliverables:
            st.markdown(f"- {d}")
    else:
        st.caption("复现计划未列出 deliverables。")


def _render_degradation_section(state: Dict) -> None:
    """渲染降级原因区块（degraded 形态专属：降级节点 / 执行错误 / 用户处置决策）。"""
    st.markdown("### ⚠️ 降级原因")
    reasons = _degradation_reasons(state)

    degraded_nodes = reasons["degraded_nodes"]
    if degraded_nodes:
        st.markdown(
            "- 降级节点（degraded_nodes）："
            + ", ".join(f"`{n}`" for n in degraded_nodes)
        )
    else:
        st.markdown("- 降级节点（degraded_nodes）：（无显式降级节点记录）")

    errors = reasons["execution_errors"]
    if errors:
        st.markdown("- 执行错误摘要（execution_result.errors）：")
        for e in errors:
            st.markdown(f"    - {e}")

    decision = reasons["user_fix_decision"]
    if decision:
        st.markdown(f"- 用户处置决策（user_fix_decision）：`{decision}`")


def _render_full_report_markdown(report_path: Optional[str]) -> None:
    """读 report_path → st.markdown 完整渲染报告全文（CP-E3-1 核心契约）。"""
    st.markdown("### 📄 复现报告全文")
    markdown, err = _load_report_markdown(report_path)
    if markdown is None:
        st.warning(err)
        return
    with st.container(border=True):
        st.markdown(markdown)


def _render_back_to_input_button(key: str = "btn_report_new_task") -> None:
    """「返回输入页开启新任务」出口（沿用 sp2 终止后出口范式）。"""
    st.divider()
    if st.button("🚀 返回输入页开启新任务", key=key, use_container_width=True):
        _reset_to_input_page()
        st.rerun()


# =========================================================================== #
# 页面主入口
# =========================================================================== #
def render() -> None:
    """页面主入口（结果报告页，纯只读终态）。

    渲染顺序：标题 → 三形态结论卡片 → 指标对比表 → artifact 清单 → 修复历程 →
    代码位置 + deliverables →（degraded 形态额外）降级原因 → 报告 Markdown 全文 →
    「返回输入页开启新任务」出口。

    无 thread_id / state / report_path 时均防御式降级提示并提供出口，不崩、不死页
    （F5 后 session_state 丢失即此路径——沿用 sp2 限制，不提供 thread_id 恢复入口）。
    """
    st.title("论文自动复现 — 复现报告")

    thread_id = st.session_state.get(_KEY_THREAD_ID)
    if not thread_id:
        # 无 thread_id（F5 丢 session_state / 未发起任务）→ 占位提示 + 出口，不死页。
        st.info(
            "尚未有可展示的复现任务（刷新页面会丢失会话状态）。"
            "请返回输入页开启新任务。"
        )
        _render_back_to_input_button(key="btn_report_no_task_back")
        return

    controller = _get_controller()
    state = controller.poll_state(thread_id) or {}

    report_path = state.get("report_path")

    # --- 顶部三形态结论卡片（与 reporting._determine_report_form 同一判定）---
    form = _determine_report_form(state)
    _render_conclusion_card(form)

    # --- 降级原因（degraded 形态专属，置于结论卡片之后醒目展示）---
    if form == "degraded":
        _render_degradation_section(state)
        st.divider()

    # --- 指标对比表：full_success 展示（B 档并列不判定）；code_only 无指标章节 ---
    if form != "code_only":
        _render_metrics_section(state)
        st.divider()

    # --- artifact 清单（full_success / degraded 均展示保留产物）---
    if form != "code_only":
        _render_artifacts_section(state)
        st.divider()

    # --- 修复历程（逐轮）---
    _render_fix_history_section(state)
    st.divider()

    # --- 代码位置 + deliverables（三形态均展示）---
    _render_code_and_deliverables_section(state)
    st.divider()

    # --- 报告 Markdown 全文（CP-E3-1 核心契约：读 report_path → st.markdown 渲染）---
    _render_full_report_markdown(report_path)

    # --- 「返回输入页开启新任务」出口 ---
    _render_back_to_input_button(key="btn_report_new_task")


# app.py _PAGE_MAP 期望 render_result_report_page（E1 已按此名预留 dispatch）。
render_result_report_page = render
