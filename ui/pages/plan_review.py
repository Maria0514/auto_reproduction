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

import streamlit as st

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
    st.markdown("## 复现计划")

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
        with st.expander("环境依赖（environment）", expanded=False):
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
        st.table(rows)

    expected = plan.get("expected_results") or {}
    if expected:
        with st.expander("预期结果（expected_results）", expanded=False):
            st.json(expected)

    deliverables = plan.get("deliverables") or []
    if deliverables:
        st.markdown("**交付物**")
        for d in deliverables:
            st.markdown(f"- {d}")


def _render_repos(resource_info: Dict) -> None:
    """渲染候选仓库列表，高亮 selected_repo。"""
    resource_info = resource_info or {}
    st.markdown("## 候选代码仓库")

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
        prefix = "✅ " if is_selected else ""
        header = f"{prefix}{url}"
        with st.expander(header, expanded=is_selected):
            bits = []
            if repo.get("source"):
                bits.append(f"来源：{repo.get('source')}")
            bits.append(f"官方仓库：{'是' if repo.get('is_official') else '否'}")
            if repo.get("stars") is not None:
                bits.append(f"⭐ {repo.get('stars')}")
            if repo.get("forks") is not None:
                bits.append(f"🍴 {repo.get('forks')}")
            if repo.get("quality_score") is not None:
                bits.append(f"质量分：{repo.get('quality_score')}")
            st.markdown(" ｜ ".join(str(b) for b in bits))
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
    st.markdown("## 透明化信息")

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
    st.markdown("## 决策")

    cols = st.columns(2)
    if cols[0].button("✅ 批准计划", key="btn_approve", type="primary"):
        controller.resume_with(thread_id, {"decision": "approve"})
        st.rerun()
    if cols[1].button("📄 仅复现代码", key="btn_code_only"):
        controller.resume_with(thread_id, {"decision": "code_only"})
        st.rerun()

    # --- 修改计划：text_area 收集 user_feedback ---
    with st.expander("✏️ 修改计划", expanded=False):
        feedback = st.text_area(
            "修改意见",
            key=_KEY_REVISE_FEEDBACK,
            placeholder="请描述你希望如何调整复现计划……",
        )
        if st.button("提交修改", key="btn_revise"):
            controller.resume_with(
                thread_id,
                {"decision": "revise", "user_feedback": feedback or ""},
            )
            st.rerun()

    # --- 换仓库：feedback + new_repo_url ---
    with st.expander("🔄 更换仓库", expanded=False):
        sw_feedback = st.text_area(
            "更换原因 / 备注",
            key=_KEY_SWITCH_FEEDBACK,
            placeholder="说明为何更换仓库（可选）……",
        )
        new_repo_url = st.text_input(
            "新仓库 URL",
            key=_KEY_SWITCH_REPO_URL,
            placeholder="https://github.com/owner/repo",
        )
        if st.button("提交更换", key="btn_switch_repo"):
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
        if st.button("🛑 终止任务", key="btn_cancel"):
            st.session_state[_KEY_CONFIRM_CANCEL] = True
            st.rerun()
    else:
        st.warning("确认终止本次复现任务？此操作不可撤销。")
        ccols = st.columns(2)
        if ccols[0].button("确认终止", key="btn_cancel_confirm", type="primary"):
            controller.cancel_task(thread_id)
            st.session_state[_KEY_CONFIRM_CANCEL] = False
            st.session_state[_KEY_CURRENT_PAGE] = "progress"
            st.rerun()
        if ccols[1].button("再想想", key="btn_cancel_abort"):
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

    _render_transparency(payload)
    st.divider()
    _render_decision_buttons(controller, thread_id)


# app.py page_map 期望 render_plan_review_page（沿用 D3/D4 先例）。
render_plan_review_page = render
