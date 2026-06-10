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
    st.markdown("### 🗂️ 候选代码仓库")

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
            badge_list = []
            if is_selected:
                # mock §3.3 L219 选中态徽章文案"已选用"
                badge_list.append(("✅ 已选用", "default"))
            else:
                badge_list.append(("未选中", "outline"))
            badge_list.append(
                ("官方仓库" if repo.get("is_official") else "社区仓库", "secondary")
            )
            if repo.get("source"):
                badge_list.append((str(repo.get("source")), "outline"))
            ui.badges(badge_list=badge_list, key=f"b_repo_{idx}")
            st.markdown(f"**{url}**")

            mcols = st.columns(3)
            with mcols[0]:
                ui.metric_card(
                    title="质量分",
                    content=str(repo.get("quality_score") or "—"),
                    description="综合评估",
                    key=f"m_quality_{idx}",
                )
            with mcols[1]:
                ui.metric_card(
                    title="⭐ Stars",
                    content=str(repo.get("stars") or "—"),
                    description="GitHub 星标",
                    key=f"m_stars_{idx}",
                )
            with mcols[2]:
                ui.metric_card(
                    title="🍴 Forks",
                    content=str(repo.get("forks") or "—"),
                    description="GitHub 分叉",
                    key=f"m_forks_{idx}",
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
    """渲染透明化卡片：degraded_nodes / node_errors / revise_count + N>=5 软提示。"""
    payload = payload or {}
    with st.container(border=True):
        st.markdown("### 🔍 透明化信息")

        revise_count = payload.get("revise_count") or 0
        threshold = payload.get("soft_hint_threshold") or 5
        max_calls = payload.get("max_total_llm_calls")
        info_bits = [f"已修改轮次：{revise_count}"]
        if max_calls is not None:
            info_bits.append(f"LLM 调用上限：{max_calls}")
        st.info(" ｜ ".join(info_bits))

        if revise_count >= threshold:
            st.warning(
                "已多次修改计划，建议考虑直接批准或取消，以免耗尽预算。"
            )

        degraded = payload.get("degraded_nodes") or []
        if degraded:
            st.warning("降级节点：" + ", ".join(str(n) for n in degraded))

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


def _render_decision_buttons(controller, thread_id: str) -> None:
    """渲染五个决策按钮，点击后调对应 controller 方法再 st.rerun()。"""
    st.markdown("### 🎯 决策")

    cols = st.columns(2)
    with cols[0]:
        if ui.button(
            "✅ 批准计划",
            key="btn_approve",
            variant="default",
            class_name="bg-blue-600 hover:bg-blue-700 text-white",
        ):
            controller.resume_with(thread_id, {"decision": "approve"})
            st.rerun()
    with cols[1]:
        if ui.button(
            "📄 仅复现代码",
            key="btn_code_only",
            variant="outline",
            class_name="border-blue-600 text-blue-700 hover:bg-blue-50",
        ):
            controller.resume_with(thread_id, {"decision": "code_only"})
            st.rerun()

    # --- 修改计划：textarea 收集 user_feedback ---
    with st.expander("✏️ 修改计划", expanded=False):
        feedback = ui.textarea(
            default_value=st.session_state.get(_KEY_REVISE_FEEDBACK, ""),
            key=_KEY_REVISE_FEEDBACK,
            placeholder="请描述你希望如何调整复现计划……",
        )
        if ui.button(
            "提交修改",
            key="btn_revise",
            variant="outline",
            class_name="border-blue-600 text-blue-700 hover:bg-blue-50",
        ):
            controller.resume_with(
                thread_id,
                {"decision": "revise", "user_feedback": feedback or ""},
            )
            st.rerun()

    # --- 换仓库：feedback + new_repo_url ---
    with st.expander("🔄 更换仓库", expanded=False):
        sw_feedback = ui.textarea(
            default_value=st.session_state.get(_KEY_SWITCH_FEEDBACK, ""),
            key=_KEY_SWITCH_FEEDBACK,
            placeholder="说明为何更换仓库（可选）……",
        )
        new_repo_url = ui.input(
            default_value=st.session_state.get(_KEY_SWITCH_REPO_URL, ""),
            key=_KEY_SWITCH_REPO_URL,
            placeholder="https://github.com/owner/repo",
        )
        if ui.button(
            "提交更换",
            key="btn_switch_repo",
            variant="outline",
            class_name="border-blue-600 text-blue-700 hover:bg-blue-50",
        ):
            controller.resume_with(
                thread_id,
                {
                    "decision": "switch_repo",
                    "user_feedback": sw_feedback or "",
                    "new_repo_url": new_repo_url or "",
                },
            )
            st.rerun()

    # --- 取消：二次确认 ---
    st.divider()
    if not st.session_state.get(_KEY_CONFIRM_CANCEL):
        if ui.button("🛑 终止任务", key="btn_cancel", variant="destructive"):
            st.session_state[_KEY_CONFIRM_CANCEL] = True
            st.rerun()
    else:
        st.warning("确认终止本次复现任务？此操作不可撤销。")
        ccols = st.columns(2)
        with ccols[0]:
            if ui.button("确认终止", key="btn_cancel_confirm", variant="destructive"):
                controller.cancel_task(thread_id)
                st.session_state[_KEY_CONFIRM_CANCEL] = False
                st.session_state[_KEY_CURRENT_PAGE] = "progress"
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
    if payload is None:
        st.info("计划尚未就绪，请稍候……")
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
    _render_decision_buttons(controller, thread_id)


# app.py page_map 期望 render_plan_review_page（沿用 D3/D4 先例）。
render_plan_review_page = render
