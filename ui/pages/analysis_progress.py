"""S2-06 Streamlit 页面 2：分析进度（Sprint 2 任务 D4）。

架构参考：sprint2/architecture.md §2.10（align D4 契约权威源：状态机推断 / 终态优先级链
/ 纯函数签名 / 入口名）。
dev-plan：sprint2/dev-plan.md 任务 D4（CP-D4-1~8）。
test plan：sprint2/test-reports/2026-06-07_test-plan-d4-analysis-progress.md（L1 mock 22 项）。

页面职责（架构 §2.10）::

    纯只读观察页：autorefresh 每 1.5s rerun → controller.poll_state(thread_id) 拉最新
    state → 渲染（论文卡片 + 4 段进度条 + 实时日志）。用户**不能改状态**、**无"终止任务"
    按钮**（仅 plan_review 页提供，PRD §2.6 / §2.7）。

页面入口约定（dev-plan CP-D4-1，沿用 D3 先例）::

    主名 ``render``，模块级别名 ``render_analysis_progress_page = render``，
    ``__all__ = ["render", "render_analysis_progress_page"]``。app.py L285 page_map 用
    ("ui.pages.analysis_progress", "render_analysis_progress_page") 动态加载。

终态/跳转优先级链（架构 §2.10 align D4 + sprint5 §7.8 S5-08 路由修复，render 顶部
早返回，命中即 return）::

    1. get_worker_error 非空 → "工作线程异常" FATAL 卡片（含 str(exc)）+ 停轮询；
    2. state.error 非空    → FATAL 卡片 + "重试 / 返回输入页" + 停轮询；
    3. current_step == "cancelled_by_user" → "任务已终止"卡片 + "返回输入页" + 停轮询（AC-S2-13）；
    4. is_interrupted == True → 按 interrupt payload 的 kind 分发（AC-S5-16）：
       planning 形态（payload 无 interrupt_kind 键 / 显式 "planning"）→ review 页；
       dev_loop_failure / user_input_request → 执行监控页；
    4bis. current_step ∈ {coding, execution} → 切执行监控页（S5-08 #4 主修复）；
    4ter. current_step == "reporting" 且 report_path 非空 → 直跳报告页
          （与执行监控页 case⑥ 双通道可达，AC-S5-15）；
    5. 否则正常渲染并注册 st_autorefresh。

    **关键**：st_autorefresh(key="progress_poll") **只在 case⑤ 路径注册**——终态分支
    提前 return 即不注册定时器，这是"停轮询"正确性的根基（架构 §2.10）。
"""

from __future__ import annotations

import logging
from typing import Dict, List, Literal, Optional

import pandas as pd
import streamlit as st
import streamlit_shadcn_ui as ui
from streamlit_autorefresh import st_autorefresh

from config import (
    STREAMLIT_PAGE_EXECUTION,
    STREAMLIT_PAGE_REPORT,
    STREAMLIT_PAGE_REVIEW,
    STREAMLIT_POLL_INTERVAL,
)
# S5-09 术语治理（T-S5-3-5）：内部枚举经 humanize 转用户可读中文再渲染。
from ui.term_map import humanize

logger = logging.getLogger(__name__)

__all__ = ["render", "render_analysis_progress_page"]


# 4 段进度条节点序列，与 core/graph.py 线性拓扑同序（架构 §2.10）。
# **逻辑序列**：仅这 4 段参与状态推断与单测（_segment_status 不变）。
ORDER: List[str] = ["paper_intake", "paper_analysis", "resource_scout", "planning"]

# **展示序列**（D5 视觉对齐 mock §3.2，docs/sprint2/ui-mockup/index.html L147-152）：
# mock 把全流程画成 5 段，第 5 段 ``post_review`` 合并下游 coding/execution/reporting
# 节点（Sprint 2 占位、Sprint 3 实现）。Sprint 2 阶段第 5 段恒为 pending（review 中断
# 后才进入下游），与 mock 视觉一致；Sprint 3 落地下游节点后无需改 UI 即可激活。
# 不并入 ORDER 避免破坏 _segment_status 已通过的单测（degraded/降级语义只对 4 段生效）。
DISPLAY_ORDER: List[str] = [
    "paper_intake",
    "paper_analysis",
    "resource_scout",
    "planning",
    "post_review",  # mock §3.2 第 5 段（执行复现 + 汇总结果合一），Sprint 2 恒 pending
]

# 进度条各段状态文案：严格照产品经理 mock(ui-mockup/index.html L148-152)逐字抄。
# mock: st-done「✓ 完成」/ st-doing「● 进行中」/ st-wait「○ 待开始」。
# 第二个 tuple 元素保留（历史 emoji，部分旧调用读它），文案以第一个为准。
_SEGMENT_LABELS = {
    "pending": ("○ 待开始", "⚪"),
    "running": ("● 进行中", "🔵"),
    "done": ("✓ 完成", "🟢"),
    "degraded": ("✓ 完成（降级）", "🟡"),
}

# 节点中文显示名 + 阶段图标 emoji（D5 mock §3.2 L148-152 对齐）。
# (display_name, stage_emoji) — stage_emoji 出现在阶段卡片顶部，区别于状态徽章 emoji。
_NODE_DISPLAY: Dict[str, tuple] = {
    "paper_intake": ("解析论文", "📄"),
    "paper_analysis": ("分析论文", "🧠"),
    "resource_scout": ("资源侦察", "🔍"),
    "planning": ("制定计划", "🧩"),
    "post_review": ("执行复现", "⚙️"),  # mock 第 5 段，Sprint 2 恒 pending
}

# 兼容旧测试 / 旧引用：保留 _NODE_DISPLAY_NAMES 平面字典（仅中文名）。
_NODE_DISPLAY_NAMES = {k: v[0] for k, v in _NODE_DISPLAY.items()}

_KEY_THREAD_ID = "thread_id"
_KEY_CURRENT_PAGE = "current_page"

# interrupt kind 标识（与 core/nodes/execution.py::INTERRUPT_KIND / core/tools/
# interaction_tools.py::INTERRUPT_KIND_USER_INPUT 严格对齐——沿用 execution_monitor /
# app.py 先例：UI 侧留本地字符串 + 单测断言防漂移，不引入节点/工具模块 import）。
_INTERRUPT_KIND_DEV_LOOP: str = "dev_loop_failure"
_INTERRUPT_KIND_USER_INPUT: str = "user_input_request"

# 下游阶段 current_step 取值（S5-08：coding/execution → 执行监控页；reporting → 报告页兜底）。
_STEP_CODING: str = "coding"
_STEP_EXECUTION: str = "execution"
_STEP_REPORTING: str = "reporting"


# =========================================================================== #
# 纯函数内核（模块级，可 import 直测；test plan L1 内核直测对齐）
# =========================================================================== #
def _segment_status(
    current_step: str,
    node_name: str,
    degraded_nodes: List[str],
) -> Literal["pending", "running", "done", "degraded"]:
    """推断进度条单段状态（架构 §2.10 align D4 契约权威）。

    判定基于节点序列索引比较（current_step 只记"最后写入的节点"，无独立完成标志位）::

        节点索引 > 当前         → "pending"（待执行）
        节点索引 == 当前         → "running"（运行中）
        节点索引 < 当前 且 ∈ degraded → "degraded"（降级完成）
        节点索引 < 当前 且 ∉ degraded → "done"（已完成）

    **防御性安全索引**（绝不裸用 ``ORDER.index(current_step)``，会对 start / cancelled /
    coding 等抛 ValueError 导致线上崩溃）::

        current_step == "start"                       → 当前索引视为 -1（4 段全 pending）；
        current_step 为下游(coding/execution/reporting)或未知 → 当前索引视为 len(ORDER)（全 done）。

    注意：返回**语义枚举非颜色字符串**；颜色 / 中文文案在 render 层经 _SEGMENT_LABELS 映射。
    planning interrupt 时 planning 段仍为 "running"（不引入第 5 态，因当帧即跳转 review），
    故本函数**不接收 is_interrupted 参数**（保持纯函数性，架构 §2.10）。
    """
    degraded = degraded_nodes or []

    # --- 防御性安全索引：start / 下游 / 未知 step 均不裸用 ORDER.index ---
    if current_step == "start":
        cur_idx = -1  # 尚未进任何节点 → 4 段全 pending
    elif current_step in ORDER:
        cur_idx = ORDER.index(current_step)
    else:
        # 下游节点（coding/execution/reporting）或未知 step → 哨兵索引 len(ORDER) → 全 done
        cur_idx = len(ORDER)

    # node_name 理论上恒为 ORDER 成员（render 只对 ORDER 内节点调用），防御性兜底。
    try:
        node_idx = ORDER.index(node_name)
    except ValueError:
        # 非 ORDER 节点：保守视为 pending（不应发生，仅防御）。
        return "pending"

    if node_idx > cur_idx:
        return "pending"
    if node_idx == cur_idx:
        return "running"
    # node_idx < cur_idx：节点已完成
    if node_name in degraded:
        return "degraded"
    return "done"


def _pick_bilingual(
    meta: Optional[Dict],
    base_field: str,
    zh_field: str,
) -> str:
    """双语回退：优先 *_zh 字段，缺失 / None / 空串时回退英文主字段（架构 §2.10）。

    PRD §4.7.5：UI 展示优先中文（title_zh / tldr_zh / abstract_zh），LLM 漏写或为 None
    时回退到对应英文主字段（title / tldr / abstract）。任一字段缺失均不暴露 "None" 字面量。

    Args:
        meta: paper_meta 字典（可能为 None，render 上层守卫；此处再兜底一次）。
        base_field: 英文主字段名（如 "title"）。
        zh_field: 中文字段名（如 "title_zh"）。

    Returns:
        非 None 字符串（两者皆缺时返回空串 ""，由 render 层决定是否展示）。
    """
    meta = meta or {}
    zh_val = meta.get(zh_field)
    if zh_val:  # 非 None 且非空串
        return str(zh_val)
    base_val = meta.get(base_field)
    if base_val:
        return str(base_val)
    return ""


def _interrupt_route_target(payload: Optional[Dict]) -> str:
    """case④ interrupt kind 分发（S5-08 / 架构 sprint5 §7.8，纯函数可直测，AC-S5-16）。

    输入为 ``controller.get_interrupt_payload(thread_id)`` 的返回值，按 kind 决定跳转页::

        dev_loop_failure / user_input_request → 执行监控页（该页 case⑤ 渲染对应面板）；
        planning 形态（payload 无 "interrupt_kind" 键的 sp2 旧形态 / 显式 "planning" /
        payload 为 None 或非 dict 的防御兜底）→ 计划审核页（沿用既有行为）。

    防御式：不识别的 kind 一律落 review 兜底（与 app.py::interrupt_kind 的 planning
    默认语义一致，不自创第三分类——极简裁决，架构 §7.8）。
    """
    kind = payload.get("interrupt_kind") if isinstance(payload, dict) else None
    if kind in (_INTERRUPT_KIND_DEV_LOOP, _INTERRUPT_KIND_USER_INPUT):
        return STREAMLIT_PAGE_EXECUTION
    return STREAMLIT_PAGE_REVIEW


# =========================================================================== #
# 私有渲染区块
# =========================================================================== #
def _get_controller():
    """从 session_state 取 D2 GraphController 单例（与 app.py::_get_controller 一致）。"""
    from app import _get_controller as _app_get_controller

    return _app_get_controller()


def _render_paper_card(paper_meta: Optional[Dict]) -> None:
    """渲染论文信息卡片：标题 / TLDR / 摘要（双语回退）+ arxiv_id + 作者（架构 §2.10）。

    **不重复展示 non-CS categories 警告**（归属 D3 输入页放行前提示，架构 §2.10 / §2.6）。
    paper_meta 为 None（intake 未完成）时降级为"论文信息加载中"，不渲染空卡片、不抛
    NoneType subscript（架构 §2.10 / test plan T-D4-22）。
    """
    if not paper_meta:
        # §3.2：info 类提示统一用 ui.alert（class_name 模拟 info variant）。
        ui.alert(
            title="论文信息加载中…",
            description="paper_intake 节点尚未完成，稍候自动刷新。",
            class_name="border-sky-500 bg-sky-50 text-sky-900",
            key="alert_paper_loading",
        )
        return

    with st.container(border=True):
        title = _pick_bilingual(paper_meta, "title", "title_zh")
        st.subheader("📄 " + (title or "(无标题)"))

        arxiv_id = paper_meta.get("arxiv_id")
        authors = paper_meta.get("authors") or []
        categories = paper_meta.get("categories") or []

        # 分类徽章：mock §3.2 L29-30/L140 — cs.XX 为蓝色 pill(#eff6ff 底 + #2563eb 字)。
        # ui.badges 的 class_name 走 Tailwind，但 shadcn iframe 内 JIT 未打包 bg-blue-* 类
        # （tree-shake），徽章渲染成灰色。改用主文档原生 HTML span 画蓝色 pill，与 mock
        # 的 .tag 样式一致，且不受 iframe Tailwind 限制。
        if categories:
            pills = "".join(
                f"<span style='display:inline-block; background:#eff6ff;"
                f" color:#2563eb; border:1px solid #bfdbfe; border-radius:9999px;"
                f" padding:2px 10px; margin:0 6px 4px 0; font-size:12px;"
                f" font-weight:500;'>{str(c)}</span>"
                for c in categories[:3]
            )
            st.markdown(
                f"<div style='margin:4px 0 8px 0'>{pills}</div>",
                unsafe_allow_html=True,
            )

        meta_badges = []
        if arxiv_id:
            meta_badges.append((f"arXiv: {arxiv_id}", "outline"))
        if authors:
            meta_badges.append((
                "作者：" + ", ".join(str(a) for a in authors[:3])
                + (f" 等 {len(authors)} 人" if len(authors) > 3 else ""),
                "secondary",
            ))
        if meta_badges:
            ui.badges(badge_list=meta_badges, key="b_paper_meta")

        tldr = _pick_bilingual(paper_meta, "tldr", "tldr_zh")
        if tldr:
            st.markdown(f"**TL;DR**：{tldr}")

        abstract = _pick_bilingual(paper_meta, "abstract", "abstract_zh")
        if abstract:
            with st.expander("摘要（Abstract）", expanded=False):
                st.write(abstract)


def _render_progress_bar(state: Dict) -> None:
    """渲染 5 段进度条（D5 视觉对齐 mock §3.2 L147-152）。

    **逻辑层只用 ORDER 4 段**做状态推断（与 core/graph.py 线性拓扑同序，_segment_status
    单测不变）；**展示层用 DISPLAY_ORDER 5 段**对齐 mock —— 第 5 段 ``post_review``
    合并下游 coding/execution/reporting 节点（Sprint 2 占位），状态恒为 pending。

    顶部 ui.progress 整体百分比 = 已完成段 / DISPLAY_ORDER 总段数（与 mock 一致：mock
    完成 2 段时 50%，因总段是 5）。
    """
    current_step = str(state.get("current_step") or "start")
    degraded_nodes = state.get("degraded_nodes") or []

    st.markdown("### 🚀 复现进度")

    # 逐段计算状态：post_review 不在 ORDER，做特殊处理 ——
    #   review interrupted 之后(current_step in {coding, execution, reporting,
    #   reproduction_done})视为 running/done；Sprint 2 永远到不了，恒 pending。
    def _stage_status(node_name: str):
        if node_name in ORDER:
            return _segment_status(current_step, node_name, degraded_nodes)
        # post_review：Sprint 2 恒 pending；Sprint 3 实现后按 current_step 判断
        if current_step in {"coding", "execution"}:
            return "running"
        if current_step in {"reporting", "reproduction_done", "completed"}:
            return "done"
        return "pending"

    # --- 整体百分比：已完成段 / 总展示段数 ---
    done_count = sum(
        1 for n in DISPLAY_ORDER if _stage_status(n) in ("done", "degraded")
    )
    overall_pct = (
        int(round(done_count * 100 / len(DISPLAY_ORDER))) if DISPLAY_ORDER else 0
    )
    # 进度条：mock §3.2 L61-62 ``.progress-bar``（8px 高、#e2e8f0 灰底、#2563eb 蓝填充）。
    # ui.progress 的填充是 shadcn 默认深灰色，与 mock 蓝色不符；改用 HTML 复刻 mock 样式。
    st.markdown(
        f"""
        <div style="height:8px; background:#e2e8f0; border-radius:4px;
             overflow:hidden; margin:12px 0;">
            <div style="height:100%; width:{overall_pct}%; background:#2563eb;"></div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.caption(f"整体进度：{done_count}/{len(DISPLAY_ORDER)} 阶段完成（{overall_pct}%）")

    cols = st.columns(len(DISPLAY_ORDER))
    for col, node_name in zip(cols, DISPLAY_ORDER):
        status = _stage_status(node_name)
        label, emoji = _SEGMENT_LABELS[status]
        display_name, stage_emoji = _NODE_DISPLAY.get(node_name, (node_name, "•"))
        # 状态徽章配色：严格照 mock L57-60 写死十六进制（绿/蓝/灰/红）。
        # 不用 ui.badges + Tailwind class —— shadcn iframe 内 bg-*/text-* 被 JIT
        # tree-shake 成灰色（与 cs.XX 徽章同坑），故用主文档 HTML span 直接上色。
        badge_style = {
            # st-done: bg #dcfce7 / color #16a34a
            "done": "background:#dcfce7; color:#16a34a;",
            # st-doing: bg #2563eb 实蓝填充 / color #fff
            "running": "background:#2563eb; color:#ffffff;",
            # 降级：复用完成绿系（mock 无此态，沿用 done 视觉）
            "degraded": "background:#dcfce7; color:#16a34a;",
        }.get(status, "background:#f1f5f9; color:#64748b;")  # st-wait: bg #f1f5f9 / color #64748b

        # mock §3.2 L54-55：``stage.active`` { border-color: #2563eb; background: #eff6ff }
        # —— 进行中阶段整卡蓝边 + 浅蓝底；其他阶段普通灰边白底。
        # st.container(border=True) 边框色固定灰，无法改色；streamlit_extras 的
        # stylable_container 在 1.58 已 deprecated（会弹黄色警告框污染界面），
        # 改用原生 st.container(key=...) 生成的 ``st-key-<key>`` class 选择器注入 CSS。
        if status == "running":
            card_bg, card_border = "#eff6ff", "#2563eb"
        else:
            card_bg, card_border = "#ffffff", "#e2e8f0"
        card_key = f"stage_{node_name}_{status}"
        with col:
            st.markdown(
                f"""
                <style>
                .st-key-{card_key} {{
                    border: 1px solid {card_border};
                    background: {card_bg};
                    border-radius: 10px;
                    padding: 14px;
                    text-align: center;
                }}
                </style>
                """,
                unsafe_allow_html=True,
            )
            with st.container(key=card_key):
                # 阶段图标 emoji + 段名（mock L148-150 .stage .emoji + .stage .name）
                st.markdown(
                    f"<div style='font-size:24px;text-align:center'>{stage_emoji}</div>",
                    unsafe_allow_html=True,
                )
                st.markdown(
                    f"<div style='font-size:13px;font-weight:500;margin:6px 0;text-align:center'>{display_name}</div>",
                    unsafe_allow_html=True,
                )
                # 状态徽章：HTML span 写死颜色（mock .st-badge L57，圆角 pill）。
                st.markdown(
                    f"<div style='text-align:center'>"
                    f"<span style='display:inline-block; {badge_style}"
                    f" font-size:12px; padding:1px 8px; border-radius:10px;'>"
                    f"{label}</span></div>",
                    unsafe_allow_html=True,
                )


def _render_logs(state: Dict) -> None:
    """渲染实时日志滚动区：state["node_errors"][-10:]（架构 §2.10 / dev-plan L1412）。

    迁移到 ui.accordion（§3.2）：每条一个 item，title=节点名+错误类型+摘要+状态徽章 emoji，
    展开内嵌代码块（detail 用 markdown ``` fence；ui.accordion content 仅支持字符串，
    streamlit 控件无法嵌入，故采用 markdown code-fence 等价表达）。
    """
    st.markdown("### 📋 实时日志")
    node_errors = state.get("node_errors") or []
    if not node_errors:
        st.caption("暂无日志")
        return

    # 仅最后 10 条；shadcn accordion 前端是 data.map(o => ...)，期待 list[dict]，
    # 每个 item 形如 {"trigger": ..., "content": ...}——折叠条标题键名必须是 "trigger"
    # （组件读 o.trigger），写成 "title" 会让折叠条无标题（空白）。Python dict（非 list）
    # 会让前端 .map 直接抛 "n.map is not a function"。
    items: List[Dict[str, str]] = []
    for idx, err in enumerate(node_errors[-10:]):
        if not isinstance(err, dict):
            continue
        node_name = err.get("node_name") or "?"
        error_type = err.get("error_type") or ""
        summary = err.get("error_message") or "(无摘要)"
        # S5-09（T-S5-3-5）：节点名中文化 + 括注内部名（"?" 占位符不经表）；
        # error_type 为三态机器标识（transient/permanent/fatal，非 §7.9 列举
        # domain），保留原值作排障锚点。
        node_disp = (
            f"{humanize('node', node_name)}（{node_name}）"
            if node_name != "?" else "?"
        )
        # 状态徽章用 emoji 在折叠条标题内表达（accordion trigger 仅字符串，不能嵌组件）。
        type_part = f" [{error_type}]" if error_type else ""
        trigger = f"⚠️ {idx + 1}. {node_disp}{type_part} · {summary}"
        detail = err.get("error_detail")
        if detail:
            content = f"**摘要**：{summary}\n\n```\n{detail}\n```"
        else:
            content = f"**摘要**：{summary}\n\n_(无 error_detail)_"
        items.append({"trigger": trigger, "content": content})

    if items:
        ui.accordion(data=items, key="acc_logs")
    else:
        st.caption("暂无日志")


def _render_fatal_worker_error(exc: Exception) -> None:
    """case①：工作线程异常 FATAL 卡片（含 str(exc)）+ 返回输入页（停轮询）。

    §3.2：用 ui.alert(destructive) 替代 st.error；按钮用 ui.button(variant='outline')。
    """
    # destructive 红色边框 + 浅红底（Tailwind 语义类，shadcn alert 默认无 variant 参数）。
    ui.alert(
        title="工作线程异常",
        description="复现任务在后台线程崩溃，已停止。",
        class_name="border-red-500 bg-red-50 text-red-900",
        key="alert_worker_error",
    )
    # 详情仍用 st.code（ui.alert description 不支持代码块换行渲染）。
    st.code(str(exc))
    _render_back_to_input_button(key="btn_worker_error_back")


def _render_fatal_state_error(error_msg: str) -> None:
    """case②：state.error FATAL 卡片 + 重试 / 返回输入页（停轮询）。

    §3.2：ui.alert(destructive) + ui.button(variant='default' 重试 / 'outline' 返回)；
    所有 ui.button key 保持原值（btn_retry / btn_error_back）。
    """
    ui.alert(
        title="任务发生致命错误",
        description=error_msg,
        class_name="border-red-500 bg-red-50 text-red-900",
        key="alert_state_error",
    )
    cols = st.columns(2)
    with cols[0]:
        # variant='default' 蓝色主按钮；ui.button 返回 True 表示被点击。
        if ui.button(text="重试", key="btn_retry", variant="default"):
            # 返回输入页重新发起（sp2 不提供从 thread_id 原地恢复，Q-S2-05）。
            # BUG 修复：必须经 _reset_to_input_page() 解除提交锁——否则回到输入页时
            # 所有控件仍 disabled=submitted，用户无法交互（这正是 rate-limit 失败后
            # 点"重试"卡死的根因；原实现只切页未解锁，与"返回输入页"按钮不对称）。
            _reset_to_input_page()
            st.rerun()
    with cols[1]:
        _render_back_to_input_button(key="btn_error_back")


def _render_cancelled_card() -> None:
    """case③：任务已终止卡片 + 返回输入页（停轮询，AC-S2-13）。

    §3.2：ui.alert(warning) 替代 st.warning；按钮走 ui.button(variant='outline')。
    """
    ui.alert(
        title="任务已终止",
        description="你在计划审核页主动终止了本次复现任务。checkpoint 已保留供后续查询。",
        class_name="border-amber-500 bg-amber-50 text-amber-900",
        key="alert_cancelled",
    )
    _render_back_to_input_button(key="btn_cancelled_back", label="返回输入页开启新任务")


def _reset_to_input_page() -> None:
    """切回输入页并解除提交锁，使输入页控件恢复可交互。

    「重试」与「返回输入页」的唯一共享出口：把"切页 + 解锁"绑成一个动作，
    杜绝再次出现「切页但漏解锁 → 输入页 disabled=submitted 全控件冻结」的
    不对称 BUG（本次修复点）。调用方负责随后 st.rerun()。
    """
    st.session_state[_KEY_CURRENT_PAGE] = "input"
    # 清掉 D3 输入页提交锁（paper_input._KEY_SUBMITTED = "_input_submitted"），
    # 否则回到输入页时 arxiv 输入框 / 搜索框 / 各按钮仍 disabled=submitted。
    st.session_state["_input_submitted"] = False


def _render_back_to_input_button(
    key: str,
    label: str = "返回输入页",
) -> None:
    """通用"返回输入页"按钮：清提交标记 + 切回 input 页。

    §3.2：ui.button(variant='outline') 替代 st.button；保留原 key（btn_*_back / btn_no_task_back）。
    """
    if ui.button(text=label, key=key, variant="outline"):
        _reset_to_input_page()
        st.rerun()


# =========================================================================== #
# 页面主入口
# =========================================================================== #
def render() -> None:
    """页面主入口（dev-plan CP-D4-1：``from ui.pages.analysis_progress import render``）。

    终态/跳转优先级链 + 正常渲染（架构 §2.10 align D4）。autorefresh 仅在 case⑤ 注册。
    """
    st.title("论文自动复现 — 分析进度")

    thread_id = st.session_state.get(_KEY_THREAD_ID)
    if not thread_id:
        # 无 thread_id（未从输入页发起任务）→ 占位提示，不进任何判定。
        # §3.2：用 ui.alert(info) 替代 st.info。
        ui.alert(
            title="尚未启动任务",
            description="请先在输入页填写论文与配置并点击「开始复现」。",
            class_name="border-sky-500 bg-sky-50 text-sky-900",
            key="alert_no_task",
        )
        _render_back_to_input_button(key="btn_no_task_back")
        return

    controller = _get_controller()

    # --- case①：工作线程异常（最致命，最高优先级）→ 停轮询 ---
    worker_error = controller.get_worker_error(thread_id)
    if worker_error is not None:
        _render_fatal_worker_error(worker_error)
        return

    state = controller.poll_state(thread_id)

    # --- state 为 None（snapshot 不存在）→ 占位，不进段判定、不渲染空卡片 ---
    # （上层守卫：纯函数不接受 None state；此处仍注册 autorefresh 等待 checkpoint 落盘）
    if state is None:
        ui.alert(
            title="等待任务启动 / 加载中…",
            description="正在等待 checkpoint 落盘，页面将自动刷新。",
            class_name="border-sky-500 bg-sky-50 text-sky-900",
            key="alert_waiting",
        )
        st_autorefresh(interval=STREAMLIT_POLL_INTERVAL, key="progress_poll")
        return

    # --- case②：state.error 非空 → FATAL 卡片 + 重试 / 返回（停轮询） ---
    error_msg = state.get("error")
    if error_msg:
        _render_fatal_state_error(str(error_msg))
        return

    # --- case③：cancelled_by_user → 任务已终止卡片 + 返回（停轮询，AC-S2-13） ---
    if state.get("current_step") == "cancelled_by_user":
        _render_cancelled_card()
        return

    # --- case④：interrupt → 按 payload kind 分发跳转（S5-08 / AC-S5-16，停本页轮询）---
    #   planning 形态 → review 页；dev_loop_failure / user_input_request → 执行监控页。
    if controller.is_interrupted(thread_id):
        payload = controller.get_interrupt_payload(thread_id)
        st.session_state[_KEY_CURRENT_PAGE] = _interrupt_route_target(payload)
        st.rerun()
        return  # st.rerun() 不返回（AppTest 下会抛 RerunException），防御性 return

    current_step = str(state.get("current_step") or "")

    # --- case④bis：coding / execution 阶段 → 切执行监控页（S5-08 #4 主修复）---
    if current_step in (_STEP_CODING, _STEP_EXECUTION):
        st.session_state[_KEY_CURRENT_PAGE] = STREAMLIT_PAGE_EXECUTION
        st.rerun()
        return

    # --- case④ter：reporting 完成且 report_path 非空 → 直跳报告页（AC-S5-15 兜底，
    #     与执行监控页 case⑥ 双通道可达）---
    if current_step == _STEP_REPORTING and state.get("report_path"):
        st.session_state[_KEY_CURRENT_PAGE] = STREAMLIT_PAGE_REPORT
        st.rerun()
        return

    # --- case⑤：正常渲染 + 注册 autorefresh（仅此路径注册定时器） ---
    _render_paper_card(state.get("paper_meta"))
    st.divider()
    _render_progress_bar(state)
    st.divider()
    _render_logs(state)

    # autorefresh 只能在 case⑤ 注册：终态分支提前 return 即不注册（架构 §2.10）。
    st_autorefresh(interval=STREAMLIT_POLL_INTERVAL, key="progress_poll")


# D2 app.py 路由 page_map 期望函数名 render_analysis_progress_page（app.py L285）。
# 别名导出，对齐 app.py page_map（沿用 D3 先例）。
render_analysis_progress_page = render
