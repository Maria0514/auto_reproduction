"""S6-07 任务列表页（Sprint 6 批次 4，架构 §4.4）：枚举 checkpoints + 状态徽标 + 一键挂回。

页面职责（架构 §4.3/§4.4）::

    进程重启 / F5 刷新后 thread_id 丢失、运行中任务成 UI 层孤儿——本页从 checkpoints.db
    枚举全部历史任务，展示论文标识 + 状态徽标，提供**一键挂回**（回到该任务对应页面）。

设计红线（产品红线，架构 §4.2）::

    - **挂回 = 展示现状**：点击"挂回"仅写 query_params + thread_id + 路由，**绝不调用
      resume_task**（不静默重复执行副作用节点，AC-S6-16）；孤儿任务（已中断）的推进由
      挂回落地的执行监控页 R7 卡片上的显式「继续执行」按钮触发（那里才调 resume_task）。
    - **不注册 st_autorefresh**（架构 §4.3 频控）：本页是"仅用户动作可改变"页面，枚举只在
      进页 / 点手动刷新按钮时发生一次。
    - **无删除 / 搜索 / 分页 / 归档**（非目标 5）。

页面入口约定（沿用各页先例）::

    主名 ``render``，模块级别名 ``render_task_list_page = render``，
    ``__all__ = ["render", "render_task_list_page"]``。app.py _PAGE_MAP 用
    ("ui.pages.task_list", "render_task_list_page") 动态加载，current_page = "tasks"。
"""

from __future__ import annotations

import logging
from typing import Dict, List

import streamlit as st

logger = logging.getLogger(__name__)

__all__ = ["render", "render_task_list_page"]

_KEY_THREAD_ID = "thread_id"
_KEY_CURRENT_PAGE = "current_page"

# 状态 → 徽标 emoji（文案用 controller 提供的 status_label 中文，emoji 仅作视觉锚点）。
_STATUS_EMOJI: Dict[str, str] = {
    "running": "🔵",
    "awaiting": "🟠",
    "done": "🟢",
    "failed": "🔴",
    "no_report": "🔴",
    "cancelled": "⚪",
    "interrupted": "🟡",
}


def _get_controller():
    """从 session_state 取 GraphController 单例（与 app.py::_get_controller 一致）。"""
    from app import _get_controller as _app_get_controller

    return _app_get_controller()


def _reattach(controller, thread_id: str, status: str) -> None:
    """一键挂回：写 query_params + thread_id + 路由到该任务对应页（**绝不调 resume_task**）。

    路由复用 app._route_for_status（架构 §4.1 挂回列）。挂回本身只"展示现状"——孤儿任务的
    推进须落地页 R7 卡片显式按钮触发（红线，AC-S6-16）。
    """
    from app import _route_for_status

    st.session_state[_KEY_THREAD_ID] = thread_id
    st.query_params["task"] = thread_id  # URL 持久化（复用 §7.6 通道）
    st.session_state[_KEY_CURRENT_PAGE] = _route_for_status(controller, thread_id, status)
    logger.info(
        "[task_list] 挂回 thread=%s status=%s → page=%s",
        thread_id, status, st.session_state[_KEY_CURRENT_PAGE],
    )
    st.rerun()


def _render_task_row(controller, task: Dict) -> None:
    """渲染单条任务：论文标识 + 状态徽标 + 挂回按钮。"""
    thread_id = task["thread_id"]
    status = task["status"]
    label = task.get("paper_label") or "（未知论文）"
    emoji = _STATUS_EMOJI.get(status, "⚪")
    with st.container(border=True):
        cols = st.columns([6, 2, 2])
        cols[0].markdown(f"**{label}**")
        cols[0].caption(f"`{thread_id}`")
        cols[1].markdown(f"{emoji} {task['status_label']}")
        # 原生 st.button（AppTest 可见可点）；key 以 thread_id 保唯一。
        if cols[2].button("挂回", key=f"btn_reattach_{thread_id}", use_container_width=True):
            _reattach(controller, thread_id, status)


def render() -> None:
    """任务列表页主入口：枚举 checkpoints + 状态徽标 + 一键挂回（不注册 autorefresh）。"""
    controller = _get_controller()

    st.title("任务列表")
    st.caption(
        "从 checkpoints 枚举全部历史任务（新任务在前）。点击「挂回」回到该任务页面查看现状；"
        "**挂回只展示现状，不会自动推进**——已中断任务的续跑需在其页面显式确认。"
    )

    cols = st.columns([1, 1])
    if cols[0].button("🔄 刷新列表", key="btn_task_list_refresh"):
        st.rerun()
    if cols[1].button("🚀 返回输入页开启新任务", key="btn_task_list_to_input"):
        st.session_state[_KEY_THREAD_ID] = None
        if "task" in st.query_params:
            del st.query_params["task"]
        st.session_state[_KEY_CURRENT_PAGE] = "input"
        st.rerun()

    st.divider()

    try:
        threads: List[Dict] = controller.list_threads()
    except Exception:  # noqa: BLE001 - 枚举整体失败也不炸页面
        logger.exception("[task_list] list_threads 失败")
        st.error("任务枚举失败：checkpoints 库读取异常，请稍后重试。")
        return

    if not threads:
        st.info("暂无任务：还没有发起过任何复现任务（或 checkpoints 库为空）。")
        return

    st.markdown(f"#### 共 {len(threads)} 个任务")
    for task in threads:
        _render_task_row(controller, task)

    # 本页不注册 st_autorefresh（架构 §4.3 频控：仅用户动作可改变）。


# app.py 路由 page_map 期望函数名 render_task_list_page（与各页别名导出范式一致）。
render_task_list_page = render
