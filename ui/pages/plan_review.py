"""S2-07 Streamlit 页面 3：计划审核（Sprint 2 任务 D5）。

人工审核中断点（HITL）页面：当 planning 节点产出 ReproductionPlan 并触发
LangGraph interrupt 后，本页展示复现计划全文 / 候选仓库 / 透明化信息，并提供
五个决策按钮（approve / code_only / revise / switch_repo / cancel）恢复或终止执行。

页面入口约定（沿用 D3/D4 先例）::

    主名 ``render``，模块级别名 ``render_plan_review_page = render``，
    ``__all__ = ["render", "render_plan_review_page"]``。app.py page_map 用
    ("ui.pages.plan_review", "render_plan_review_page") 动态加载，current_page="review"。

防御式编码：payload 各字段一律 .get(...) 取默认空值，绝不让 KeyError 崩页面。
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

import pandas as pd
import streamlit as st
import streamlit_shadcn_ui as ui
from streamlit_autorefresh import st_autorefresh

from config import STREAMLIT_POLL_INTERVAL

logger = logging.getLogger(__name__)

__all__ = ["render", "render_plan_review_page"]


_KEY_THREAD_ID = "thread_id"
_KEY_CURRENT_PAGE = "current_page"
# 取消二次确认标记：第一次点取消置 True，确认后才真正调 cancel_task。
_KEY_CONFIRM_CANCEL = "_review_confirm_cancel"
# 修改 / 换仓库的文本输入 widget keys。
_KEY_REVISE_FEEDBACK = "_review_revise_feedback"
_KEY_SWITCH_FEEDBACK = "_review_switch_feedback"
_KEY_SWITCH_REPO_URL = "_review_switch_repo_url"
# 决策提交后的"等待图推进"态：resume_with 只是起后台线程，本页无轮询会僵在静态页
# （"提交修改后没动静 / 一直在计划尚未就绪"的根因）。awaiting 期间 render() 走轮询分支，
# 直到状态明确转移再路由——避免读到残留旧 interrupt 误判。
_KEY_AWAITING = "_review_awaiting"
_KEY_AWAIT_KIND = "_review_await_kind"
_KEY_AWAIT_BASELINE = "_review_await_baseline"
# 这两类决策完成后返回 review 展示"重新生成的新计划"（靠 revise_count 前进判定）；
# 其余（approve / code_only / cancel）完成后去 progress 看执行/终态（靠 interrupt 被消费判定）。
_AWAIT_RETURN_KINDS = ("revise", "switch_repo")


def _safe_int(value: object, default: int = -1) -> int:
    """容错转 int（payload.revise_count 可能缺失/非数）。"""
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _await_phase(
    kind: str,
    payload: Optional[Dict],
    baseline: int,
    has_worker_error: bool,
    is_interrupted: bool,
) -> str:
    """决策提交后的等待阶段判定（纯函数，模块级可直测）。

    返回 "error" | "to_review" | "to_progress" | "waiting"：
    - 任意时刻 worker 崩 → "error"（去 progress 看致命卡）。
    - 修改/换仓库（_AWAIT_RETURN_KINDS）：payload.revise_count 超过提交时基线 → 新计划
      已生成 → "to_review"（天然忽略尚未消费的旧 interrupt：其 revise_count==baseline）。
    - 批准/仅代码/取消：planning interrupt 已被消费（不再 is_interrupted）→ "to_progress"。
    - 其余 → "waiting"（继续轮询）。
    """
    if has_worker_error:
        return "error"
    if kind in _AWAIT_RETURN_KINDS:
        if payload is not None and _safe_int(payload.get("revise_count")) > baseline:
            return "to_review"
        return "waiting"
    return "waiting" if is_interrupted else "to_progress"


def _begin_awaiting(kind: str, payload: Optional[Dict]) -> None:
    """提交决策后进入 awaiting 轮询态：记录决策类型 + 当前 revise_count 基线。"""
    st.session_state[_KEY_AWAITING] = True
    st.session_state[_KEY_AWAIT_KIND] = kind
    st.session_state[_KEY_AWAIT_BASELINE] = _safe_int(
        (payload or {}).get("revise_count"), default=0
    )


def _clear_awaiting() -> None:
    st.session_state[_KEY_AWAITING] = False
    st.session_state[_KEY_AWAIT_KIND] = ""


def _get_controller():
    """从 session_state 取 D2 GraphController 单例（与 app.py::_get_controller 一致）。"""
    from app import _get_controller as _app_get_controller

    return _app_get_controller()


def _init_page_state() -> None:
    """初始化本页 session_state 字段（不覆盖已有值）。"""
    st.session_state.setdefault(_KEY_THREAD_ID, None)
    st.session_state.setdefault(_KEY_CURRENT_PAGE, "review")
    st.session_state.setdefault(_KEY_CONFIRM_CANCEL, False)
    st.session_state.setdefault(_KEY_REVISE_FEEDBACK, "")
    st.session_state.setdefault(_KEY_SWITCH_FEEDBACK, "")
    st.session_state.setdefault(_KEY_SWITCH_REPO_URL, "")
    st.session_state.setdefault(_KEY_AWAITING, False)
    st.session_state.setdefault(_KEY_AWAIT_KIND, "")
    st.session_state.setdefault(_KEY_AWAIT_BASELINE, 0)


def _render_plan(plan: Dict) -> None:
    """渲染复现计划全文（ReproductionPlan 各字段，防御式 .get）。"""
    plan = plan or {}
    with st.container(border=True):
        st.markdown("### 📋 复现计划")

        summary = plan.get("plan_summary")
        if summary:
            st.markdown(f"**计划概述**：{summary}")

        cols = st.columns(2)
        code_strategy = plan.get("code_strategy") or "(未指定)"
        cols[0].markdown(f"**代码策略**：`{code_strategy}`")
        estimated_time = plan.get("estimated_time") or "(未估算)"
        cols[1].markdown(f"**预估耗时**：{estimated_time}")

        environment = plan.get("environment") or {}
        if environment:
            with st.expander("环境依赖", expanded=False):
                st.json(environment)

        data_prep = plan.get("data_preparation") or []
        if data_prep:
            st.markdown("**数据准备**")
            for item in data_prep:
                st.markdown(f"- {item}")

        steps = plan.get("execution_steps") or []
        if steps:
            st.markdown("**执行步骤**")
            rows = [
                {
                    "步骤": str((s or {}).get("step_name") or ""),
                    "命令": str((s or {}).get("command") or ""),
                    "预期输出": str((s or {}).get("expected_output") or ""),
                }
                for s in steps
                if isinstance(s, dict)
            ]
            ui.table(data=pd.DataFrame(rows), key="t_steps")

        expected = plan.get("expected_results") or {}
        if expected:
            with st.expander("预期结果", expanded=False):
                st.json(expected)

        deliverables = plan.get("deliverables") or []
        if deliverables:
            st.markdown("**交付物**")
            for d in deliverables:
                st.markdown(f"- {d}")


def _render_repos(resource_info: Dict) -> None:
    """渲染候选仓库列表，高亮 selected_repo。"""
    resource_info = resource_info or {}
    # mock §3.3 L216：卡片标题逐字为「候选代码仓库」(h2)。
    st.markdown("### 候选代码仓库")

    repos = resource_info.get("repos") or []
    strategy = resource_info.get("resource_strategy")
    if strategy:
        st.caption(f"资源策略：{strategy}")

    selected = resource_info.get("selected_repo") or {}
    selected_url = (selected or {}).get("url")

    if not repos:
        st.info("未发现候选仓库。")
        return

    for idx, repo in enumerate(repos):
        repo = repo or {}
        url = repo.get("url") or "(无 URL)"
        is_selected = bool(selected_url) and repo.get("url") == selected_url

        # mock §3.3 L71：``.repo-card.selected`` { background:#eff6ff;
        # border-left: 4px solid #2563eb } —— 选中态整卡浅蓝底 + 左粗蓝边。
        # st.container(border=True) 边框固定灰，无法实现 mock 视觉；streamlit_extras 的
        # stylable_container 在 1.58 已 deprecated（弹黄色警告框污染界面），改用原生
        # st.container(key=...) 生成的 ``st-key-<key>`` class 选择器注入 CSS。
        card_key = f"repo_card_{idx}_{('sel' if is_selected else 'unsel')}"
        if is_selected:
            card_css = (
                "background:#eff6ff; border:1px solid #bfdbfe;"
                " border-left:4px solid #2563eb; border-radius:10px; padding:16px;"
            )
        else:
            card_css = (
                "background:#ffffff; border:1px solid #e2e8f0;"
                " border-radius:10px; padding:16px;"
            )
        st.markdown(
            f"<style>.st-key-{card_key} {{ {card_css} }}</style>",
            unsafe_allow_html=True,
        )

        with st.container(key=card_key):
            # mock §3.3 L219：选中态徽章文案逐字「已选用」，样式 .badge
            # { background:#eff6ff; color:#2563eb; border-radius:6px; padding:2px 10px;
            # font-size:12px }。ui.badges 走 Tailwind class，在 shadcn iframe 内被
            # JIT tree-shake 成灰色 —— 改用主文档原生 HTML span 写死十六进制（与
            # analysis_progress.py 分类 pill 同范式），不受 iframe Tailwind 限制。
            if is_selected:
                badge_html = (
                    "<span style='display:inline-block; background:#eff6ff;"
                    " color:#2563eb; border-radius:6px; padding:2px 10px;"
                    " font-size:12px; font-weight:500;'>已选用</span>"
                )
            else:
                # 非选中：灰色描边 pill（mock 无对应态，沿用中性 .muted 配色）。
                badge_html = (
                    "<span style='display:inline-block; background:#f8fafc;"
                    " color:#64748b; border:1px solid #e2e8f0; border-radius:6px;"
                    " padding:2px 10px; font-size:12px; font-weight:500;'>未选中</span>"
                )
            kind = "官方仓库" if repo.get("is_official") else "社区仓库"
            kind_badge = (
                "<span style='display:inline-block; background:#f8fafc;"
                " color:#64748b; border:1px solid #e2e8f0; border-radius:6px;"
                f" padding:2px 10px; font-size:12px; font-weight:500;"
                f" margin-left:6px;'>{kind}</span>"
            )
            st.markdown(
                f"**{url}** {badge_html}{kind_badge}",
                unsafe_allow_html=True,
            )

            # mock §3.3 L222-225：三个 metric 顺序为 ⭐ Stars / 🍴 Forks / 质量分，
            # .num{font-size:22px;font-weight:700;color:#0f172a};.lbl{font-size:12px;
            # color:#64748b}。ui.metric_card 渲染在 iframe 且配色不可控，改用主文档
            # HTML 写死十六进制，顺序严格照 mock。
            stars = repo.get("stars")
            forks = repo.get("forks")
            quality = repo.get("quality_score")
            metric_specs = [
                (f"⭐ {stars}" if stars is not None else "⭐ —", "Stars"),
                (f"🍴 {forks}" if forks is not None else "🍴 —", "Forks"),
                (str(quality) if quality is not None else "—", "质量分"),
            ]
            metric_cells = "".join(
                "<div style='flex:1; min-width:120px; border:1px solid #e2e8f0;"
                " border-radius:10px; padding:14px; text-align:center;'>"
                f"<div style='font-size:22px; font-weight:700; color:#0f172a;'>{num}</div>"
                f"<div style='font-size:12px; color:#64748b;'>{lbl}</div>"
                "</div>"
                for num, lbl in metric_specs
            )
            st.markdown(
                "<div style='display:flex; gap:12px; flex-wrap:wrap;"
                f" margin:12px 0;'>{metric_cells}</div>",
                unsafe_allow_html=True,
            )

            extra = []
            if repo.get("last_commit_date"):
                extra.append(f"最近提交：{repo.get('last_commit_date')}")
            if repo.get("has_readme") is not None:
                extra.append(f"README：{'有' if repo.get('has_readme') else '无'}")
            if repo.get("has_requirements") is not None:
                extra.append(
                    f"依赖清单：{'有' if repo.get('has_requirements') else '无'}"
                )
            if extra:
                st.caption(" ｜ ".join(extra))


def _render_transparency(payload: Dict) -> None:
    """渲染透明化 info-bar：revise_count / LLM 调用上限 / degraded_nodes + N>=5 软提示。"""
    payload = payload or {}

    revise_count = payload.get("revise_count") or 0
    threshold = payload.get("soft_hint_threshold") or 5
    max_calls = payload.get("max_total_llm_calls")
    degraded = payload.get("degraded_nodes") or []

    # mock §3.3 L236：单条 .info-bar，文案逐字「ℹ️ 已修改 N 轮 ｜ LLM 调用上限 M 次
    # ｜ 降级节点 X」，样式 .info-bar { background:#eff6ff; border:1px solid #bfdbfe;
    # color:#1e40af; border-radius:8px; padding:12px 16px }。st.info 是蓝灰默认样式
    # 且配色不可控 —— 改用主文档原生 HTML div 写死十六进制。
    info_bits = [f"已修改 {revise_count} 轮"]
    if max_calls is not None:
        info_bits.append(f"LLM 调用上限 {max_calls} 次")
    if degraded:
        info_bits.append("降级节点 " + ", ".join(str(n) for n in degraded))
    st.markdown(
        "<div style='background:#eff6ff; border:1px solid #bfdbfe; color:#1e40af;"
        " border-radius:8px; padding:12px 16px; font-size:14px; margin:16px 0;'>"
        f"ℹ️ {' ｜ '.join(info_bits)}</div>",
        unsafe_allow_html=True,
    )

    if revise_count >= threshold:
        st.warning(
            "已多次修改计划，建议考虑直接批准或取消，以免耗尽预算。"
        )

    node_errors = payload.get("node_errors") or []
    if node_errors:
        with st.expander(f"最近错误（{len(node_errors)} 条）", expanded=False):
            for err in node_errors[-5:]:
                if isinstance(err, dict):
                    node_name = err.get("node_name") or "?"
                    msg = err.get("error_message") or str(err)
                    st.markdown(f"- `{node_name}`：{msg}")
                else:
                    st.markdown(f"- {err}")


def _render_decision_buttons(controller, thread_id: str, payload: Dict) -> None:
    """渲染五个决策按钮，点击后调对应 controller 方法 + 进入 awaiting 轮询态再 st.rerun()。

    payload：当前 interrupt 的审核数据，用于记录提交时的 revise_count 基线（_begin_awaiting）。


    mock §3.3 L239-244：决策区五个按钮,文案逐字为
    「✅ 批准计划 / 📄 仅复现代码 / ✏️ 修改计划 / 🔁 切换仓库 / ⛔ 终止任务」。
    配色照 mock .btn-* : 批准=primary(#2563eb 蓝底白字)、仅复现/修改/切换=secondary
    (#fff 白底深字灰边)、终止=danger(#fff 白底 #dc2626 红字)。
    ——shadcn ui.button 的 variant 默认配色恰好对应:default=蓝、outline=白描边、
    destructive=红。故用 variant 命中颜色,**不再传 class_name**(Tailwind class 在
    shadcn iframe 内被 JIT tree-shake 成灰色,是本项目反复踩的坑)。
    修改/切换仍需文本输入,故各自配 expander 收集 feedback,但触发按钮文案/颜色照 mock。
    """
    st.markdown("### 🎯 决策")

    # mock .btn-primary{background:#2563eb;color:#fff} / .btn-danger{background:#fff;
    # color:#dc2626;border:#fecaca}。shadcn ui.button 的 variant 默认色不等于 mock
    # (default=深黑底、destructive=红底白字),class_name 又被 iframe tree-shake。
    # 故批准/终止改用原生 st.button(key=) + ``.st-key-<key>`` CSS 注入写死十六进制
    # (原生 button 在主文档,CSS 命中,不进 iframe)。仅复现/修改/切换为白底(mock
    # .btn-secondary),shadcn outline 默认即白底深字,沿用 ui.button(variant=outline)。
    st.markdown(
        """
        <style>
        .st-key-btn_approve button {
            background:#2563eb !important; color:#ffffff !important;
            border:1px solid #2563eb !important;
        }
        .st-key-btn_approve button:hover { background:#1d4ed8 !important; }
        .st-key-btn_cancel button, .st-key-btn_cancel_confirm button {
            background:#ffffff !important; color:#dc2626 !important;
            border:1px solid #fecaca !important;
        }
        .st-key-btn_cancel button:hover,
        .st-key-btn_cancel_confirm button:hover { background:#fef2f2 !important; }
        </style>
        """,
        unsafe_allow_html=True,
    )

    cols = st.columns(2)
    with cols[0]:
        # 批准=mock .btn-primary 蓝底白字（原生 button + CSS 注入）。
        if st.button("✅ 批准计划", key="btn_approve", use_container_width=True):
            controller.resume_with(thread_id, {"decision": "approve"})
            # 进入 awaiting：本页轮询直到旧 interrupt 被消费，再去 progress 看执行/终态。
            # 不能直接切 progress——resume 异步，切过去时旧 interrupt 常未消费，progress
            # 读到残留 interrupt 会立刻把用户弹回 review（"提交后没动静"误判根因）。
            _begin_awaiting("approve", payload)
            st.rerun()
    with cols[1]:
        if ui.button("📄 仅复现代码", key="btn_code_only", variant="outline"):
            controller.resume_with(thread_id, {"decision": "code_only"})
            _begin_awaiting("code_only", payload)
            st.rerun()

    # --- 修改计划：textarea 收集 user_feedback ---
    with st.expander("✏️ 修改计划", expanded=False):
        # 单源治理：反馈框由 shadcn ui.textarea 迁到原生 st.text_area（仅 key、无
        # default_value）。原 ui.textarea 是双源——default_value 读
        # session_state[_KEY_REVISE_FEEDBACK] + key 又写同一键——且 shadcn 组件在
        # iframe 内每敲一个字符就回传值触发 Streamlit rerun，rerun 时把刚写入的值当
        # React defaultValue 回灌，组件内部状态与回灌值打架 → 光标跳、字符错位、输入
        # 框抖动（用户报告的就是此框）。与 paper_input 搜索框 L233 / arXiv ID 框 L311
        # 同款决策。st.text_area 仅在 Enter/失焦时 rerun，无 iframe 逐键 rerun 风暴，
        # 不抖动；键名 _KEY_REVISE_FEEDBACK 保持不变（AppTest 与 session_state 流转依赖）。
        # 值读取路径不破：button handler 仍读本函数返回值变量 feedback，st.text_area
        # 返回当前输入值且同步写 session_state[_KEY_REVISE_FEEDBACK]。
        feedback = st.text_area(
            "修改意见",
            key=_KEY_REVISE_FEEDBACK,
            placeholder="请描述你希望如何调整复现计划……",
        )
        if ui.button("提交修改", key="btn_revise", variant="outline"):
            controller.resume_with(
                thread_id,
                {"decision": "revise", "user_feedback": feedback or ""},
            )
            # 进入 awaiting：留在本页轮询后台重规划（planning self-loop），直到
            # revise_count 超过基线（= 新计划真的生成）再展示新计划。靠 revise_count
            # 区分新旧 interrupt，天然忽略尚未消费的旧 interrupt，不再死在"计划尚未就绪"。
            _begin_awaiting("revise", payload)
            st.rerun()

    # --- 切换仓库：feedback + new_repo_url（mock 文案逐字「🔁 切换仓库」）---
    with st.expander("🔁 切换仓库", expanded=False):
        # 单源治理（同「修改计划」框）：shadcn ui.textarea/ui.input 迁到原生
        # st.text_area/st.text_input，仅用 key、删 default_value 双源，规避 iframe
        # 逐键 rerun 回灌导致的打字抖动。键名 _KEY_SWITCH_FEEDBACK /
        # _KEY_SWITCH_REPO_URL 保持不变。值读取路径不破：button handler 仍读返回值
        # 变量 sw_feedback / new_repo_url，原生组件返回当前值并写同名 session_state 键。
        sw_feedback = st.text_area(
            "修改意见",
            key=_KEY_SWITCH_FEEDBACK,
            placeholder="说明为何更换仓库（可选）……",
        )
        new_repo_url = st.text_input(
            "新仓库 URL",
            key=_KEY_SWITCH_REPO_URL,
            placeholder="https://github.com/owner/repo",
        )
        if ui.button("提交切换", key="btn_switch_repo", variant="outline"):
            controller.resume_with(
                thread_id,
                {
                    "decision": "switch_repo",
                    "user_feedback": sw_feedback or "",
                    "new_repo_url": new_repo_url or "",
                },
            )
            # 同 revise：留在本页轮询，靠 revise_count 前进判定新计划就绪再展示。
            _begin_awaiting("switch_repo", payload)
            st.rerun()

    # --- 取消：二次确认（mock 文案逐字「⛔ 终止任务」，.btn-danger 白底红字）---
    st.divider()
    if not st.session_state.get(_KEY_CONFIRM_CANCEL):
        # 原生 st.button + .st-key-btn_cancel CSS 写死 mock .btn-danger 白底红字。
        if st.button("⛔ 终止任务", key="btn_cancel", use_container_width=True):
            st.session_state[_KEY_CONFIRM_CANCEL] = True
            st.rerun()
    else:
        st.warning("确认终止本次复现任务？此操作不可撤销。")
        ccols = st.columns(2)
        with ccols[0]:
            if st.button(
                "确认终止", key="btn_cancel_confirm", use_container_width=True
            ):
                controller.cancel_task(thread_id)
                st.session_state[_KEY_CONFIRM_CANCEL] = False
                # 进入 awaiting：轮询直到 interrupt 被消费（图走到 cancelled_by_user→END），
                # 再去 progress 看"任务已终止"卡。避免 cancel 后停在残留 interrupt 的旧计划页。
                _begin_awaiting("cancel", payload)
                st.rerun()
        with ccols[1]:
            if ui.button("再想想", key="btn_cancel_abort", variant="outline"):
                st.session_state[_KEY_CONFIRM_CANCEL] = False
                st.rerun()


def render() -> None:
    """页面主入口（计划审核 HITL）。"""
    _init_page_state()

    st.title("论文自动复现 — 计划审核")
    st.caption(
        "请审核下方复现计划与候选仓库，并选择批准、仅复现代码、修改、更换仓库或终止任务。"
    )

    thread_id = st.session_state.get(_KEY_THREAD_ID)
    if not thread_id:
        st.info("尚未启动任务。请先在输入页发起复现。")
        return

    controller = _get_controller()

    payload = controller.get_interrupt_payload(thread_id)

    # --- 决策提交后：等待图推进（轮询自愈，避免停在静态页"没动静"）---
    if st.session_state.get(_KEY_AWAITING):
        kind = st.session_state.get(_KEY_AWAIT_KIND, "")
        baseline = _safe_int(st.session_state.get(_KEY_AWAIT_BASELINE), default=0)
        phase = _await_phase(
            kind=kind,
            payload=payload,
            baseline=baseline,
            has_worker_error=controller.get_worker_error(thread_id) is not None,
            is_interrupted=controller.is_interrupted(thread_id),
        )
        if phase == "to_review":
            # 修改/换仓库：新计划已生成（revise_count 前进）→ 清 awaiting，落到下方渲染新计划。
            _clear_awaiting()
        elif phase in ("to_progress", "error"):
            # 批准/仅代码/取消已离开 planning 暂停，或后台 worker 崩 → 去 progress 看执行/终态/致命卡。
            _clear_awaiting()
            st.session_state[_KEY_CURRENT_PAGE] = "progress"
            st.rerun()
        else:  # waiting：继续轮询
            msg = (
                "正在根据你的修改意见重新生成计划……"
                if kind in _AWAIT_RETURN_KINDS
                else "正在处理你的决策……"
            )
            st.info(f"⏳ {msg}（页面会自动刷新，请稍候）")
            st_autorefresh(interval=STREAMLIT_POLL_INTERVAL, key="review_await_poll")
            return

    if payload is None:
        # 安全网：非 awaiting 的 None（如初次还没到 planning interrupt）也轮询，绝不死页。
        st.info("计划尚未就绪，请稍候……")
        st_autorefresh(interval=STREAMLIT_POLL_INTERVAL, key="review_idle_poll")
        return

    _render_plan(payload.get("reproduction_plan") or {})
    st.divider()
    _render_repos(payload.get("resource_info") or {})
    st.divider()

    analysis = payload.get("paper_analysis_summary") or {}
    if analysis:
        with st.expander("论文分析摘要", expanded=False):
            st.json(analysis)
    st.divider()
    _render_transparency(payload)
    st.divider()
    _render_decision_buttons(controller, thread_id, payload)


# app.py page_map 期望 render_plan_review_page（沿用 D3/D4 先例）。
render_plan_review_page = render
