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

终态/跳转优先级链（架构 §2.10 align D4，render 顶部早返回，命中即 return）::

    1. get_worker_error 非空 → "工作线程异常" FATAL 卡片（含 str(exc)）+ 停轮询；
    2. state.error 非空    → FATAL 卡片 + "重试 / 返回输入页" + 停轮询；
    3. current_step == "cancelled_by_user" → "任务已终止"卡片 + "返回输入页" + 停轮询（AC-S2-13）；
    4. is_interrupted == True → current_page="review" + st.rerun()；
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

from config import STREAMLIT_POLL_INTERVAL

logger = logging.getLogger(__name__)

__all__ = ["render", "render_analysis_progress_page"]


# 4 段进度条节点序列，与 core/graph.py 线性拓扑同序（架构 §2.10）。
ORDER: List[str] = ["paper_intake", "paper_analysis", "resource_scout", "planning"]

# 进度条各段中文文案 + 颜色 emoji（语义枚举 → 展示映射，纯函数只返回语义枚举）。
_SEGMENT_LABELS = {
    "pending": ("待执行", "⚪"),
    "running": ("运行中", "🔵"),
    "done": ("已完成", "🟢"),
    "degraded": ("降级完成", "🟡"),
}

# 节点中文显示名（进度条展示用）。
_NODE_DISPLAY_NAMES = {
    "paper_intake": "论文摄取",
    "paper_analysis": "论文分析",
    "resource_scout": "资源搜集",
    "planning": "复现规划",
}

_KEY_THREAD_ID = "thread_id"
_KEY_CURRENT_PAGE = "current_page"


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
        badge_list = []
        if arxiv_id:
            badge_list.append((f"arXiv: {arxiv_id}", "default"))
        if authors:
            badge_list.append(("作者：" + ", ".join(str(a) for a in authors), "secondary"))
        if badge_list:
            ui.badges(badge_list=badge_list, key="b_paper_meta")

        tldr = _pick_bilingual(paper_meta, "tldr", "tldr_zh")
        if tldr:
            st.markdown(f"**TL;DR**：{tldr}")

        abstract = _pick_bilingual(paper_meta, "abstract", "abstract_zh")
        if abstract:
            with st.expander("摘要（Abstract）", expanded=False):
                st.write(abstract)


def _render_progress_bar(state: Dict) -> None:
    """渲染 4 段进度条（paper_intake / paper_analysis / resource_scout / planning）。

    各段状态由纯函数 _segment_status 推断（与 core/graph.py 线性拓扑同序，架构 §2.10）。
    顶部追加 ui.progress 整体百分比 = done / total（§3.2 设计要求；degraded 也算"已结束"段）。
    """
    current_step = str(state.get("current_step") or "start")
    degraded_nodes = state.get("degraded_nodes") or []

    st.markdown("### 🚀 复现进度")

    # --- 整体百分比（§3.2 顶部 ui.progress）：done + degraded 视为已完成段 ---
    done_count = sum(
        1
        for n in ORDER
        if _segment_status(current_step, n, degraded_nodes) in ("done", "degraded")
    )
    overall_pct = int(round(done_count * 100 / len(ORDER))) if ORDER else 0
    # ui.progress 0.1.19 形参是 data（百分比 int 0~100）。
    ui.progress(data=overall_pct, key="prog_overall")
    st.caption(f"整体进度：{done_count}/{len(ORDER)} 阶段完成（{overall_pct}%）")

    cols = st.columns(len(ORDER))
    for col, node_name in zip(cols, ORDER):
        status = _segment_status(current_step, node_name, degraded_nodes)
        label, emoji = _SEGMENT_LABELS[status]
        display_name = _NODE_DISPLAY_NAMES.get(node_name, node_name)
        # 每段一张卡片：emoji + 段名 + 状态 badge（Tailwind 上色，绿/蓝/灰区分）。
        badge_class = {
            "done": "bg-green-100 text-green-700 border border-green-300",
            "running": "bg-blue-600 text-white border border-blue-600",
            "degraded": "bg-red-100 text-red-700 border border-red-300",
        }.get(status, "bg-slate-100 text-slate-500 border border-slate-300")
        with col:
            with st.container(border=True):
                st.markdown(f"### {emoji}")
                st.markdown(f"**{display_name}**")
                ui.badges(
                    badge_list=[(label, "outline")],
                    class_name=badge_class,
                    key=f"b_seg_{node_name}",
                )
                # label 同步进主文档（shadcn badge 在 iframe，AppTest 读不到；
                # caption 作为可见兜底，供测试/无障碍读取）。
                st.caption(label)


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

    # 仅最后 10 条；shadcn accordion 前端是 data.map(r => ...)，期待 list[dict]，
    # 每个 item 形如 {"title": ..., "content": ...}。Python dict 会让前端 .map 直接抛
    # "n.map is not a function"。
    items: List[Dict[str, str]] = []
    for idx, err in enumerate(node_errors[-10:]):
        if not isinstance(err, dict):
            continue
        node_name = err.get("node_name") or "?"
        error_type = err.get("error_type") or ""
        summary = err.get("error_message") or "(无摘要)"
        # 状态徽章用 emoji 在 title 内表达（accordion title 仅字符串，不能嵌组件）。
        type_part = f" [{error_type}]" if error_type else ""
        title = f"⚠️ {idx + 1}. {node_name}{type_part} · {summary}"
        detail = err.get("error_detail")
        if detail:
            content = f"**摘要**：{summary}\n\n```\n{detail}\n```"
        else:
            content = f"**摘要**：{summary}\n\n_(无 error_detail)_"
        items.append({"title": title, "content": content})

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
            st.session_state[_KEY_CURRENT_PAGE] = "input"
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


def _render_back_to_input_button(
    key: str,
    label: str = "返回输入页",
) -> None:
    """通用"返回输入页"按钮：清提交标记 + 切回 input 页。

    §3.2：ui.button(variant='outline') 替代 st.button；保留原 key（btn_*_back / btn_no_task_back）。
    """
    if ui.button(text=label, key=key, variant="outline"):
        st.session_state[_KEY_CURRENT_PAGE] = "input"
        # 清掉输入页提交锁，允许重新发起新任务（D3 _KEY_SUBMITTED）。
        st.session_state["_input_submitted"] = False
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

    # --- case④：interrupt → 跳转 plan_review 页（停本页轮询） ---
    if controller.is_interrupted(thread_id):
        st.session_state[_KEY_CURRENT_PAGE] = "review"
        st.rerun()
        return  # st.rerun() 不返回（AppTest 下会抛 RerunException），防御性 return

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
