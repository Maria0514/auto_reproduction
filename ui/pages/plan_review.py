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

import json
import logging
from typing import Dict, List, Optional

import pandas as pd
import streamlit as st
import streamlit_shadcn_ui as ui
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from streamlit_autorefresh import st_autorefresh

from config import STREAMLIT_POLL_INTERVAL
# S5-09 术语治理（T-S5-3-5）：内部枚举经 humanize 转用户可读中文再渲染。
from ui.term_map import humanize

logger = logging.getLogger(__name__)

__all__ = ["render", "render_plan_review_page"]


_KEY_THREAD_ID = "thread_id"
_KEY_CURRENT_PAGE = "current_page"
# 取消二次确认标记：第一次点取消置 True，确认后才真正调 cancel_task。
_KEY_CONFIRM_CANCEL = "_review_confirm_cancel"
# 换仓库的文本输入 widget keys（revise 已迁到多轮对话面板，见 S2-12）。
_KEY_SWITCH_FEEDBACK = "_review_switch_feedback"
_KEY_SWITCH_REPO_URL = "_review_switch_repo_url"

# === S2-12：与规划模型多轮对话敲定修改方向（替换一次性 revise 文本框）===
# 对话历史仅存 session_state、不持久化（与 api_key 不落盘同档；刷新/重启后丢失，可接受）。
_KEY_CHAT_MESSAGES = "_review_chat_messages"  # list[{"role": "user"|"assistant", "content": str}]
# 记录对话归属的 thread_id：thread 变更（新任务）时清空对话，避免跨任务串话。
_KEY_CHAT_THREAD = "_review_chat_thread"
# 本轮对话累计的 LLM 调用次数（每轮讨论 + 那次「修改方向纪要」总结都计 +1）。
# 架构已裁定（方案 A，technical-architecture.md §12.7 注脚）：UI 侧对话在 graph 之外，
# 不回写 graph 的 retry_budget_remaining / MAX_TOTAL_LLM_CALLS——回写需在主线程引入写
# 路径，破坏 S-2 spike 验证的「主线程只读 + worker 独写」线程安全模型，收益不抵风险。
# 「计入总预算」为产品认知语义（与 revise 共用 N≥5 软提示线），非账本级扣减；此计数为
# UI session 级「展示 + 软提示」。属设计选择，非待修缺口。
_KEY_CHAT_CALLS = "_review_chat_calls"
# 轻量兜底输入框（对话不可用时退化为现状一次性 revise）的 widget key。
_KEY_FALLBACK_FEEDBACK = "_review_fallback_feedback"
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


# =========================================================================== #
# S2-12：与规划模型多轮对话敲定修改方向（纯函数，模块级可直测）
#
# 设计契约（与 PRD §2.12 / AC-S2-14~18 对齐）：
# - 对话只在 UI 侧直连 planning 模型「讨论敲定方向」，**不直接产出完整复现计划**——
#   完整计划仍由 planning 节点重规划生成（硬边界，避免对话与 graph 出两份不一致计划）。
# - grounding 上下文 == planning 节点重规划喂的同一批字段（reproduction_plan +
#   paper_analysis_summary + resource_info），避免讨论与重规划上下文割裂。
# - 点「确定方案并重新生成计划」时额外调一次 planning 模型，把本轮讨论敲定的方向
#   总结成一段简洁中文「修改方向纪要」，用该纪要（**不是整段对话原文**）作 user_feedback，
#   复用现有 revise awaiting 落定路径。
# =========================================================================== #


def _format_plan_context(payload: Optional[Dict]) -> str:
    """把 interrupt payload 的 grounding 字段格式化为对话上下文文本（纯函数）。

    取 planning 重规划同款的三类字段（reproduction_plan / paper_analysis_summary /
    resource_info），用 ``json.dumps(..., sort_keys=True, ensure_ascii=False)`` 稳定渲染。
    防御式 .get：满 / 空 / partial payload 均不抛（payload 为 None 视作空 dict）。
    """
    payload = payload or {}
    context = {
        "reproduction_plan": payload.get("reproduction_plan") or {},
        "paper_analysis_summary": payload.get("paper_analysis_summary") or {},
        "resource_info": payload.get("resource_info") or {},
    }
    # default=str 兜底不可序列化对象；sort_keys 保证同一 payload 渲染稳定（便于直测）。
    return json.dumps(context, ensure_ascii=False, sort_keys=True, indent=2, default=str)


def _build_chat_system_prompt(payload: Optional[Dict]) -> str:
    """构造对话 system prompt：角色 = 复现规划讨论助手 + 明确边界 + 注入 grounding（纯函数）。

    边界语（硬约束，对应 PRD §2.12「对话不直接落计划」）：帮用户澄清 / 敲定修改方向、
    用中文简洁讨论，**不要现在就重写完整计划或输出大段 JSON / 代码**——敲定后系统另行重规划。
    """
    return (
        "你是论文复现计划的「修改方向讨论助手」。当前一份复现计划已生成并等待用户审核，"
        "用户希望在正式重新规划之前，先和你讨论清楚「这份计划要怎么改」。\n\n"
        "你的职责边界（务必遵守）：\n"
        "1. 帮用户澄清意图、敲定本轮要修改的方向，用中文简洁地与用户讨论；\n"
        "2. 每次回复聚焦讨论与建议，**不要现在就重写完整复现计划、不要输出大段 JSON 或代码**；\n"
        "3. 真正的完整重规划由系统在用户点击「确定方案并重新生成计划」后另行触发——"
        "你现在只负责把方向讨论清楚，不要越俎代庖直接产出最终计划。\n\n"
        "以下是当前复现计划与相关上下文（供你理解现状，不要原样复述）：\n"
        "--- 当前计划上下文 ---\n"
        f"{_format_plan_context(payload)}\n"
        "--- 上下文结束 ---"
    )


def _history_to_messages(history: List[Dict]) -> List[BaseMessage]:
    """把 session 对话历史（list[{role, content}]）转为 LangChain 消息序列（纯函数）。

    role == "assistant" → AIMessage，其余（含 "user"）→ HumanMessage。
    """
    messages: List[BaseMessage] = []
    for turn in history or []:
        if not isinstance(turn, dict):
            continue
        role = turn.get("role")
        content = str(turn.get("content") or "")
        if role == "assistant":
            messages.append(AIMessage(content=content))
        else:
            messages.append(HumanMessage(content=content))
    return messages


def _build_chat_messages(
    payload: Optional[Dict], history: List[Dict]
) -> List[BaseMessage]:
    """构造一次对话 invoke 的完整消息序列（纯函数）。

    首条恒为 SystemMessage（角色 + 边界 + grounding），其后为历史消息（role ↔ 消息类型）。
    调用方在 append 完用户最新输入后再传 history，故此处不额外追加。
    """
    return [SystemMessage(content=_build_chat_system_prompt(payload))] + (
        _history_to_messages(history)
    )


def _build_summary_messages(
    payload: Optional[Dict], history: List[Dict]
) -> List[BaseMessage]:
    """构造「修改方向纪要」总结 invoke 的消息序列（纯函数）。

    让模型基于本轮讨论产出一段简洁中文纪要，作为重规划的 user_feedback。
    首条 SystemMessage 复用对话 grounding（同一上下文），末条 HumanMessage 给出总结指令。
    """
    summary_instruction = (
        "请基于以上我们的全部讨论，总结出一段简洁、明确、可执行的「修改方向纪要」，"
        "用中文书写，直接陈述这份复现计划需要如何调整（例如：更换数据集 / 调整执行步骤 / "
        "更换代码方案等）。只输出纪要正文本身，不要输出寒暄、不要输出完整计划或代码、"
        "不要分点编号之外的多余内容。该纪要将作为重新规划的唯一修改依据。"
    )
    return (
        [SystemMessage(content=_build_chat_system_prompt(payload))]
        + _history_to_messages(history)
        + [HumanMessage(content=summary_instruction)]
    )


def _sync_chat_thread(thread_id: Optional[str]) -> None:
    """thread_id 变更（切到新任务）→ 清空对话历史与计数，避免跨任务串话（副作用）。

    首次进入（_KEY_CHAT_THREAD 为 None）也视作绑定当前 thread，不清空（对话本就空）。
    """
    bound = st.session_state.get(_KEY_CHAT_THREAD)
    if bound != thread_id:
        st.session_state[_KEY_CHAT_MESSAGES] = []
        st.session_state[_KEY_CHAT_CALLS] = 0
        st.session_state[_KEY_CHAT_THREAD] = thread_id


def _build_planning_chat_llm(llm_config_set: Optional[Dict]):
    """按 planning 节点的 LLMConfig 构造 ChatOpenAI（副作用：可能抛 PermanentError）。

    复用用户已配置的 planning 节点 override（缺失回退 default），与 graph 内 planning
    重规划用同一份配置，保证讨论与重规划模型一致。``resolve_llm_config`` 在
    llm_config_set 形态错误时抛 PermanentError，由上层 st.error 兜底（不崩页）。
    """
    # 延迟 import：避免页面模块在无 LLM 依赖的轻量测试中过早拉起 langchain 重链路。
    from core.llm_client import create_llm, resolve_llm_config

    return create_llm(resolve_llm_config(llm_config_set, "planning"))


def _get_controller():
    """从 session_state 取 D2 GraphController 单例（与 app.py::_get_controller 一致）。"""
    from app import _get_controller as _app_get_controller

    return _app_get_controller()


def _init_page_state() -> None:
    """初始化本页 session_state 字段（不覆盖已有值）。"""
    st.session_state.setdefault(_KEY_THREAD_ID, None)
    st.session_state.setdefault(_KEY_CURRENT_PAGE, "review")
    st.session_state.setdefault(_KEY_CONFIRM_CANCEL, False)
    st.session_state.setdefault(_KEY_SWITCH_FEEDBACK, "")
    st.session_state.setdefault(_KEY_SWITCH_REPO_URL, "")
    st.session_state.setdefault(_KEY_AWAITING, False)
    st.session_state.setdefault(_KEY_AWAIT_KIND, "")
    st.session_state.setdefault(_KEY_AWAIT_BASELINE, 0)
    # S2-12 对话面板状态（不持久化）。
    st.session_state.setdefault(_KEY_CHAT_MESSAGES, [])
    st.session_state.setdefault(_KEY_CHAT_THREAD, None)
    st.session_state.setdefault(_KEY_CHAT_CALLS, 0)


def _render_plan(plan: Dict) -> None:
    """渲染复现计划全文（ReproductionPlan 各字段，防御式 .get）。"""
    plan = plan or {}
    with st.container(border=True):
        st.markdown("### 📋 复现计划")

        summary = plan.get("plan_summary")
        if summary:
            st.markdown(f"**计划概述**：{summary}")

        cols = st.columns(2)
        # S5-09：code_strategy 为内部枚举（use_repo/hybrid/from_scratch），经 humanize
        # 渲染用户可读中文，不再裸露原值（T-S5-3-5；state 字段本身零改动）。
        raw_strategy = plan.get("code_strategy")
        code_strategy = humanize("code_strategy", raw_strategy) if raw_strategy else "(未指定)"
        cols[0].markdown(f"**代码策略**：{code_strategy}")
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
    # S5-09：resource_strategy 内部枚举经 humanize 渲染（T-S5-3-5）。
    strategy = resource_info.get("resource_strategy")
    if strategy:
        st.caption(f"资源策略：{humanize('resource_strategy', strategy)}")

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
    """渲染透明化 info-bar：revise_count / LLM 调用上限 / degraded_nodes + N>=5 软提示。

    S2-12 增列「本轮对话已消耗 X 次调用」（session 计数器 _KEY_CHAT_CALLS：每轮对话 +
    那次总结都计）。

    架构裁定（方案 A，technical-architecture.md §12.7 注脚）：UI 侧对话在 graph 之外、
    不回写 graph 的 ``retry_budget_remaining`` / ``MAX_TOTAL_LLM_CALLS``（回写会破坏
    S-2 spike 的「主线程只读 + worker 独写」线程安全模型）；此计数为 UI session 级
    「展示 + 软提示」，与 revise 共用同一条 N≥5 软提示线，**不硬锁**。属设计选择非缺口。
    """
    payload = payload or {}

    revise_count = payload.get("revise_count") or 0
    threshold = payload.get("soft_hint_threshold") or 5
    max_calls = payload.get("max_total_llm_calls")
    degraded = payload.get("degraded_nodes") or []
    # 本轮对话累计 LLM 调用次数（session 计数器，纯展示 + 软提示，不回写 graph 预算）。
    chat_calls = int(st.session_state.get(_KEY_CHAT_CALLS, 0))

    # mock §3.3 L236：单条 .info-bar，文案逐字「ℹ️ 已修改 N 轮 ｜ LLM 调用上限 M 次
    # ｜ 降级节点 X」，样式 .info-bar { background:#eff6ff; border:1px solid #bfdbfe;
    # color:#1e40af; border-radius:8px; padding:12px 16px }。st.info 是蓝灰默认样式
    # 且配色不可控 —— 改用主文档原生 HTML div 写死十六进制。
    info_bits = [f"已修改 {revise_count} 轮"]
    if max_calls is not None:
        info_bits.append(f"LLM 调用上限 {max_calls} 次")
    # S2-12：本轮对话已消耗的调用次数（与 revise 共用同一总预算，UI 侧软提示口径）。
    info_bits.append(f"本轮对话已消耗 {chat_calls} 次调用")
    if degraded:
        # S5-09：节点名经 humanize + 括注内部名（与 execution_monitor _STEP_DISPLAY
        # "代码生成（coding）"既有口径一致：中文为主、机器可读标识保留作锚点）。
        info_bits.append(
            "降级节点 "
            + ", ".join(f"{humanize('node', str(n))}（{n}）" for n in degraded)
        )
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

    # S2-12 软提示：对话轮次本质是「反复折腾」信号，与 revise 同质，故复用同一条
    # N≥5 软提示线（threshold = PLANNING_SOFT_HINT_THRESHOLD，来自 payload）。
    # 架构裁定：不用 max_total_llm_calls 比例阈值——预算是 ReAct round 口径、对话是次数
    # 口径（量纲不同），且长对话上下文早膨胀到不可用，比例线永不触发=伪阈值。
    # 详见 technical-architecture.md §12.7 注脚。
    if chat_calls >= threshold:
        st.warning(
            f"本轮对话已消耗 {chat_calls} 次 LLM 调用，建议尽快敲定方向并重新生成计划。"
        )

    node_errors = payload.get("node_errors") or []
    if node_errors:
        with st.expander(f"最近错误（{len(node_errors)} 条）", expanded=False):
            for err in node_errors[-5:]:
                if isinstance(err, dict):
                    node_name = err.get("node_name") or "?"
                    msg = err.get("error_message") or str(err)
                    # S5-09：节点名中文化 + 括注内部名（"?" 占位符不经表）。
                    node_disp = (
                        f"{humanize('node', node_name)}（{node_name}）"
                        if node_name != "?" else "?"
                    )
                    st.markdown(f"- {node_disp}：{msg}")
                else:
                    st.markdown(f"- {err}")


def _handle_chat_turn(
    user_text: str, payload: Optional[Dict], llm_config_set: Optional[Dict]
) -> None:
    """处理一轮对话：append 用户消息 → invoke planning 模型 → 成功 append 助手回复 + 计数+1。

    失败容错（AC-S2-18）：invoke 抛错时 st.error 展示降级文案（含「下一步」指引），
    **不追加坏 assistant、不污染历史、不崩页**（用户输入已 append，可重试或改走兜底框）。
    同步 invoke + 调用方包 st.spinner 给反馈（不引入后台线程）。
    """
    user_text = (user_text or "").strip()
    if not user_text:
        return
    history: List[Dict] = st.session_state[_KEY_CHAT_MESSAGES]
    history.append({"role": "user", "content": user_text})

    try:
        llm = _build_planning_chat_llm(llm_config_set)
        messages = _build_chat_messages(payload, history)
        response = llm.invoke(messages)
        # 仅成功才追加助手回复并计数；content 兜底为 str（不同 LLM 返回结构差异）。
        history.append({"role": "assistant", "content": str(getattr(response, "content", response))})
        st.session_state[_KEY_CHAT_CALLS] = int(st.session_state.get(_KEY_CHAT_CALLS, 0)) + 1
    except Exception as exc:  # noqa: BLE001 —— 任何 LLM 异常都降级，不崩页
        logger.warning("对话轮次调用 planning 模型失败：%s", exc)
        # 错误三层（product-design-specification §4.5.3）+ 必给「下一步」。
        st.error(
            "讨论助手暂时不可用，本轮消息未能得到回复。\n\n"
            "下一步：你可以稍后重试，或使用下方「轻量兜底输入框」直接填一句修改方向并重新生成计划。"
        )


def _apply_chat_revision(
    controller, thread_id: str, payload: Optional[Dict], llm_config_set: Optional[Dict]
) -> None:
    """敲定方向：调 planning 模型总结「修改方向纪要」→ 作 user_feedback 复用 revise 落定。

    流程（AC-S2-16）：
    1. st.spinner 内 invoke 总结模型，计数 +1，得到一段简洁中文纪要；
    2. 以纪要作 user_feedback，调 ``controller.resume_with(thread_id, {"decision":"revise", ...}})``
       恰好一次，复用 ``_begin_awaiting("revise", payload)`` 进入现有 awaiting 轮询态；
    3. 清空对话历史 + st.rerun()，落定后由现有 _await_phase 驱动展示新计划。

    总结失败兜底：退化用「拼接最近用户发言」作 user_feedback（不空跑），仍触发重规划，不崩页。
    """
    history: List[Dict] = st.session_state[_KEY_CHAT_MESSAGES]
    feedback = ""
    try:
        with st.spinner("正在总结本轮讨论的修改方向并准备重新生成计划……"):
            llm = _build_planning_chat_llm(llm_config_set)
            messages = _build_summary_messages(payload, history)
            response = llm.invoke(messages)
            feedback = str(getattr(response, "content", response) or "").strip()
            st.session_state[_KEY_CHAT_CALLS] = int(st.session_state.get(_KEY_CHAT_CALLS, 0)) + 1
    except Exception as exc:  # noqa: BLE001
        logger.warning("总结修改方向纪要失败，退化为拼接用户发言：%s", exc)

    if not feedback:
        # 兜底：模型总结失败 / 返回空 → 拼接本轮所有用户发言作 user_feedback，保证重规划不空跑。
        user_lines = [
            str(t.get("content") or "")
            for t in history
            if isinstance(t, dict) and t.get("role") == "user"
        ]
        feedback = "\n".join(line for line in user_lines if line.strip())
        if not feedback:
            # 连用户发言都没有（理论上按钮在空对话时已 disabled，不应到此）→ 提示重试，不落定。
            st.error("未能生成修改方向纪要，且没有可用的讨论内容。下一步：请先在对话中描述修改方向，或使用兜底输入框。")
            return

    # 复用现有 awaiting 落定路径（与一次性 revise 完全一致的下游链路）。
    controller.resume_with(thread_id, {"decision": "revise", "user_feedback": feedback})
    _begin_awaiting("revise", payload)
    # 落定后清空对话（与 switch_repo 落定对称）：本轮讨论已转化为重规划输入，历史不再保留。
    st.session_state[_KEY_CHAT_MESSAGES] = []
    st.session_state[_KEY_CHAT_CALLS] = 0
    st.rerun()


def _render_revise_chat(
    controller, thread_id: str, payload: Optional[Dict], llm_config_set: Optional[Dict]
) -> None:
    """渲染「与规划模型多轮对话敲定修改方向」面板（替换原一次性修改文本框，S2-12）。

    用原生 st.chat_message / st.chat_input / st.button（AppTest 可见，避开 shadcn iframe 坑）：
    - 回放对话历史（区分 user / assistant 角色）；
    - st.chat_input 收新消息 → _handle_chat_turn 实时讨论；
    - 「✅ 确定方案并重新生成计划」按钮（空对话 disabled）→ _apply_chat_revision；
    - 轻量兜底输入框（对话不可用时直接填一句方向走 revise），保证核心 revise 能力不丢。
    """
    with st.expander("✏️ 与规划助手讨论修改方向", expanded=True):
        st.caption(
            "先和规划助手讨论清楚「这份计划要怎么改」，敲定后点下方按钮一次性重新生成计划。"
            "（讨论内容不会持久化，刷新或重启后丢失）"
        )

        history: List[Dict] = st.session_state[_KEY_CHAT_MESSAGES]
        # 回放历史消息（原生 st.chat_message，AppTest 可见）。
        for turn in history:
            if not isinstance(turn, dict):
                continue
            role = "assistant" if turn.get("role") == "assistant" else "user"
            with st.chat_message(role):
                st.markdown(str(turn.get("content") or ""))

        # 新消息输入（Enter 提交）。chat_input 返回非空即本轮有新输入。
        user_text = st.chat_input("和规划助手讨论你想怎么改这份计划……")
        if user_text:
            with st.spinner("规划助手正在思考……"):
                _handle_chat_turn(user_text, payload, llm_config_set)
            st.rerun()

        # 「确定方案并重新生成计划」：空对话 disabled（AC：空对话不可点）。
        has_history = bool(history)
        if st.button(
            "✅ 确定方案并重新生成计划",
            key="btn_apply_chat_revision",
            use_container_width=True,
            disabled=not has_history,
        ):
            _apply_chat_revision(controller, thread_id, payload, llm_config_set)

        # 轻量兜底输入框（AC-S2-18）：对话不可用时仍能填一句方向直接走 revise。
        st.divider()
        st.caption("讨论助手不可用？也可以直接在下方填一句修改方向并重新生成：")
        fallback = st.text_input(
            "一句话修改方向（兜底）",
            key=_KEY_FALLBACK_FEEDBACK,
            placeholder="例如：把数据集换成 2WikiMultiHopQA",
        )
        if st.button("直接用这句话重新生成计划", key="btn_fallback_revise"):
            text = (fallback or "").strip()
            if not text:
                st.warning("请先填写一句修改方向，再点击重新生成。")
            else:
                # 退化为现状一次性 revise：直接用这句话作 user_feedback，不经模型总结。
                controller.resume_with(
                    thread_id, {"decision": "revise", "user_feedback": text}
                )
                _begin_awaiting("revise", payload)
                st.session_state[_KEY_CHAT_MESSAGES] = []
                st.session_state[_KEY_CHAT_CALLS] = 0
                st.rerun()


def _render_decision_buttons(
    controller, thread_id: str, payload: Dict, llm_config_set: Optional[Dict]
) -> None:
    """渲染决策区，点击后调对应 controller 方法 + 进入 awaiting 轮询态再 st.rerun()。

    payload：当前 interrupt 的审核数据，用于记录提交时的 revise_count 基线（_begin_awaiting）。
    llm_config_set：planning 节点 LLM 配置（poll_state 权威源），下传给对话面板构造模型。

    S2-12：原「✏️ 修改计划」一次性 textarea 已替换为 _render_revise_chat 多轮对话面板
    （revise 的 user_feedback 改由对话敲定后的「修改方向纪要」产出）；批准 / 仅复现代码 /
    切换仓库 / 终止任务四个按钮文案与行为不变。

    mock §3.3 L239-244：决策区按钮文案逐字为
    「✅ 批准计划 / 📄 仅复现代码 / 🔁 切换仓库 / ⛔ 终止任务」（修改改为对话面板）。
    配色照 mock .btn-* : 批准=primary(#2563eb 蓝底白字)、仅复现/切换=secondary
    (#fff 白底深字灰边)、终止=danger(#fff 白底 #dc2626 红字)。
    ——shadcn ui.button 的 variant 默认配色恰好对应:default=蓝、outline=白描边、
    destructive=红。故用 variant 命中颜色,**不再传 class_name**(Tailwind class 在
    shadcn iframe 内被 JIT tree-shake 成灰色,是本项目反复踩的坑)。
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

    # --- 修改计划：S2-12 多轮对话面板（替换原一次性 textarea）---
    # 对话敲定方向 → 模型总结「修改方向纪要」作 user_feedback → 复用 revise awaiting 落定。
    _render_revise_chat(controller, thread_id, payload, llm_config_set)

    # --- 切换仓库：feedback + new_repo_url（mock 文案逐字「🔁 切换仓库」）---
    # S2-13：上一轮 switch_repo 克隆/分析失败时（payload.switch_repo_failed），强制重填——
    # expander 展开 + st.error 提示 + 清空已填 URL（让用户重填或改选其它候选）。
    _switch_failed = bool((payload or {}).get("switch_repo_failed"))
    if _switch_failed:
        st.session_state[_KEY_SWITCH_REPO_URL] = ""
    with st.expander("🔁 切换仓库", expanded=_switch_failed):
        if _switch_failed:
            st.error("仓库克隆/分析失败，请核对链接或换一个；也可改选下方其它候选仓库")
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
            # 与 revise 落定对称：换仓库重规划后旧讨论已失效，清空对话历史避免串话。
            st.session_state[_KEY_CHAT_MESSAGES] = []
            st.session_state[_KEY_CHAT_CALLS] = 0
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

    # thread 变更（新任务）→ 清空对话历史，避免跨任务串话（S2-12）。
    _sync_chat_thread(thread_id)

    # planning 节点的 LLM 配置（poll_state 权威源；session_state 那份非权威）。
    # 防御式 .get：poll_state 可能返回 None / 缺键，对话面板内 resolve_llm_config 兜底。
    state = controller.poll_state(thread_id) or {}
    llm_config_set = state.get("llm_config_set")

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
            # S2-13：switch_repo 含 clone 更慢，文案静态切换给用户「这次更慢」预期（§2.13.6）。
            if kind == "switch_repo":
                msg = "正在克隆并分析仓库、重新生成计划……"
            elif kind in _AWAIT_RETURN_KINDS:
                msg = "正在根据你的修改意见重新生成计划……"
            else:
                msg = "正在处理你的决策……"
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
    _render_decision_buttons(controller, thread_id, payload, llm_config_set)


# app.py page_map 期望 render_plan_review_page（沿用 D3/D4 先例）。
render_plan_review_page = render
