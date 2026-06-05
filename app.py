"""Auto-Reproduction Streamlit 应用入口 + GraphController（Sprint 2 任务 D2 / S2-08）。

本模块持有"GraphController + 工作线程 + 主线程 Streamlit"三者引用（架构 §2.7）。

线程模型（架构 §2.7.1 / §4.3，已由 spike S-1 / S-2 验证）：
    - 主线程（Streamlit）：渲染 UI；通过 self._main_graph（持有 self._main_checkpointer）
      只读 get_state；
    - 工作线程（每个 thread_id 一个）：daemon 线程跑 graph.invoke()，内部**独立**创建
      SqliteSaver 实例 + graph，跑到 planning interrupt 自然暂停退出；
    - resume 工作线程：同样新起 daemon 线程 + 独立 SqliteSaver 实例调用
      graph.invoke(Command(resume=...))；
    - 主线程与所有工作线程**不共享 SqliteSaver 实例**，仅共享 SQLite 文件，靠
      WAL 模式实现并发读写（S-2 spike 60s 压测 PASS）。

关键落地约束：
    - LangGraph 1.1.10 的 SqliteSaver.put 强制要求 config["configurable"]["checkpoint_ns"]，
      故所有直接调 saver / graph.get_state / graph.invoke 的 config 统一经
      _make_config(thread_id) 注入 thread_id + checkpoint_ns=""（根命名空间，S-2 spike L50）；
    - is_interrupted 判定 = snapshot.next 非空 且 snapshot.tasks 含 interrupt 元数据
      （S-1 spike CP-S1-3 实证形态）；
    - 工作线程异常一律 try/except 写入 self._worker_errors[thread_id]，由 UI 检测展示
      （100% 工作线程崩溃感知率，架构 §2.7.1）。
"""

from __future__ import annotations

import logging
import threading
import uuid
from typing import Dict, Optional

from langgraph.types import Command

from core.checkpointer import get_checkpointer
from core.graph import build_graph
from core.state import GlobalState, LLMConfigSet, create_initial_state

logger = logging.getLogger(__name__)


# 支持节点级 LLM 覆写的 4 个节点名（与 core.state.NodeName / PRD §2.4 强一致）。
_OVERRIDE_NODES = ("paper_intake", "paper_analysis", "resource_scout", "planning")


def _make_config(thread_id: str) -> Dict:
    """构造 LangGraph 调用 config。

    LangGraph 1.1.10 的 SqliteSaver.put 强制要求 ``checkpoint_ns`` 字段（S-2 spike
    修复实证，TODO L50）。所有直接调 saver / graph.get_state / graph.invoke 的 config
    都必须经此 helper 注入 thread_id + checkpoint_ns=""（根命名空间），避免散落各处
    的字面量 dict 漏写 checkpoint_ns。
    """
    return {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}


def _refresh_llm_config_set(llm_config_set: LLMConfigSet) -> LLMConfigSet:
    """逐条强制刷新 default + overrides 的 api_key（架构 §2.7.2 / R-S2-11）。

    构造一份全新的 LLMConfigSet：
        - default：完整复制表单提交的 LLMConfig（含 api_key），不复用任何旧值；
        - overrides：只保留用户显式填写的节点 key（防止"空 LLMConfig 但保留 api_key
          字段"的悬挂数据），且每条均完整复制表单提交值。

    返回的对象将原样写入 initial_state.llm_config_set，确保 SqliteSaver 中不会复用
    任何过期 api_key。
    """
    default_cfg = dict(llm_config_set["default"])  # 浅拷贝，强制使用表单提交的 api_key
    raw_overrides = llm_config_set.get("overrides") or {}

    overrides: Dict[str, Dict] = {}
    for node_name, node_cfg in raw_overrides.items():
        if node_name not in _OVERRIDE_NODES:
            # 防御：忽略非法节点名（表单层已限定，但 controller 不信任入参）。
            logger.warning("[start_task] 忽略非法 override 节点名: %s", node_name)
            continue
        if not node_cfg:
            # 空 LLMConfig 视为"未覆写"，不进 overrides 字典（悬挂数据清理）。
            continue
        overrides[node_name] = dict(node_cfg)

    refreshed: LLMConfigSet = {"default": default_cfg, "overrides": overrides}
    return refreshed


class GraphController:
    """GraphController 持有所有跨线程协调逻辑（架构 §2.7.1 参考实现落地）。

    在 Streamlit 中以单例形式存放于 st.session_state["graph_controller"]，避免每次
    rerun 重建（架构 §2.7 风险标注）。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._workers: Dict[str, threading.Thread] = {}
        self._worker_errors: Dict[str, Exception] = {}
        # 主线程独占 checkpointer + graph，仅用于 poll_state / is_interrupted 读路径。
        self._main_checkpointer = get_checkpointer()
        self._main_graph = build_graph(checkpointer=self._main_checkpointer)

    # ------------------------------------------------------------------
    # 启动 / 恢复
    # ------------------------------------------------------------------

    def start_task(self, arxiv_id: str, llm_config_set: LLMConfigSet) -> str:
        """启动一个新复现任务，返回 thread_id 并异步起工作线程。

        api_key 注入（架构 §2.7.2 / R-S2-11）：表单提交的 default + 每条 override 的
        api_key 逐条强制刷新到 initial_state.llm_config_set，不复用任何 SqliteSaver
        旧值；overrides 只保留用户显式填写的节点 key。
        """
        thread_id = f"task-{uuid.uuid4().hex[:12]}"
        refreshed_config_set = _refresh_llm_config_set(llm_config_set)
        initial_state = create_initial_state(arxiv_id, refreshed_config_set)

        thread = threading.Thread(
            target=self._worker_run,
            args=(thread_id, initial_state),
            daemon=True,
            name=f"graph-worker-{thread_id}",
        )
        with self._lock:
            self._workers[thread_id] = thread
            # 重新启动同一 thread_id 前清掉旧错误（防御；sp2 单 thread_id 不会触发）。
            self._worker_errors.pop(thread_id, None)
        thread.start()
        return thread_id

    def _worker_run(self, thread_id: str, initial_state: GlobalState) -> None:
        """工作线程入口。每线程独立创建 SqliteSaver + graph（架构 §4.3 方案 A）。"""
        try:
            worker_checkpointer = get_checkpointer()  # 独立实例（不共享主线程实例）
            worker_graph = build_graph(checkpointer=worker_checkpointer)
            config = _make_config(thread_id)
            worker_graph.invoke(initial_state, config)  # 跑到 interrupt 自然暂停
        except Exception as e:  # noqa: BLE001 - 100% 崩溃感知，统一捕获写错误表
            logger.exception("[worker:%s] 异常", thread_id)
            with self._lock:
                self._worker_errors[thread_id] = e

    def resume_with(self, thread_id: str, resume_payload: Dict) -> None:
        """通过**新工作线程**调用 graph.invoke(Command(resume=...))。

        关键：不能在主线程同步调用 invoke()，否则 UI 阻塞；需要新起一个 daemon worker
        （架构 §2.7.1 / R-S2-02）。
        """
        thread = threading.Thread(
            target=self._resume_run,
            args=(thread_id, resume_payload),
            daemon=True,
            name=f"graph-resume-{thread_id}",
        )
        with self._lock:
            self._workers[thread_id] = thread
        thread.start()

    def _resume_run(self, thread_id: str, resume_payload: Dict) -> None:
        """resume 工作线程入口。又一个独立 SqliteSaver 实例（架构 §4.3）。"""
        try:
            worker_checkpointer = get_checkpointer()  # 又一个独立实例
            worker_graph = build_graph(checkpointer=worker_checkpointer)
            config = _make_config(thread_id)
            worker_graph.invoke(Command(resume=resume_payload), config)
        except Exception as e:  # noqa: BLE001
            logger.exception("[resume:%s] 异常", thread_id)
            with self._lock:
                self._worker_errors[thread_id] = e

    # ------------------------------------------------------------------
    # 主线程只读
    # ------------------------------------------------------------------

    def poll_state(self, thread_id: str) -> Optional[GlobalState]:
        """主线程通过独立 main_graph（main_checkpointer）读取 state，不阻塞工作线程。"""
        config = _make_config(thread_id)
        snapshot = self._main_graph.get_state(config)
        return snapshot.values if snapshot else None

    def is_interrupted(self, thread_id: str) -> bool:
        """判定 graph 是否处于 planning interrupt 暂停状态。

        判定形态（S-1 spike CP-S1-3 实证）：snapshot.next 元组非空 **且** snapshot.tasks
        中至少一个 task 含 interrupt 元数据。图已推进到 END 时 snapshot.next 为空元组，
        返回 False。
        """
        config = _make_config(thread_id)
        snapshot = self._main_graph.get_state(config)
        return bool(snapshot and snapshot.next and self._has_interrupt(snapshot))

    @staticmethod
    def _has_interrupt(snapshot) -> bool:
        """扫描 StateSnapshot.tasks，判定是否存在 interrupt 元数据（S-1 spike 形态）。"""
        tasks = getattr(snapshot, "tasks", None) or ()
        for task in tasks:
            interrupts = getattr(task, "interrupts", None) or ()
            if len(interrupts) > 0:
                return True
        return False

    def get_worker_error(self, thread_id: str) -> Optional[Exception]:
        """返回工作线程捕获的异常对象（无则 None），由 UI 检测展示。"""
        with self._lock:
            return self._worker_errors.get(thread_id)

    # ------------------------------------------------------------------
    # 取消
    # ------------------------------------------------------------------

    def cancel_task(self, thread_id: str) -> None:
        """用户主动终止当前任务（PRD §2.8 / AC-S2-13）。

        约束（架构 §2.7.1）：
        - **仅在 graph 处于 planning interrupt 状态时可调用**；非 interrupt 状态打
          WARNING 日志、**不抛异常**（UI 侧由按钮可见性约束保证不被点到）；
        - 实现方式：复用 resume_with 通道注入 {"decision": "cancel"} payload，planning
          节点收到后返回 current_step="cancelled_by_user"，graph 经 _route_after_planning
          的 "end" 分支自然走到 END。
        """
        if not self.is_interrupted(thread_id):
            logger.warning("[cancel:%s] 非 interrupt 状态，忽略", thread_id)
            return
        self.resume_with(thread_id, {"decision": "cancel"})


# ======================================================================
# Streamlit 主入口
# ======================================================================


def _get_controller() -> GraphController:
    """从 session_state 取 GraphController 单例，避免每次 rerun 重建（架构 §2.7 风险）。"""
    import streamlit as st

    if "graph_controller" not in st.session_state:
        st.session_state["graph_controller"] = GraphController()
    return st.session_state["graph_controller"]


def _init_session_state() -> None:
    """初始化主入口所需的 session_state 字段。"""
    import streamlit as st

    st.session_state.setdefault("thread_id", None)
    st.session_state.setdefault("llm_config_set", None)
    st.session_state.setdefault("current_page", "input")
    # graph_controller 单例由 _get_controller 惰性创建。


def _render_sidebar() -> Optional[LLMConfigSet]:
    """侧栏渲染 LLM 配置表单（D1 组件），返回其**返回值**（不直读 session_state）。

    [OBS-D1-01] 必须用 render_llm_config_form() 的返回值，禁止直接读
    st.session_state["llm_config_set"]：D1 组件校验失败返回 None 时不清除该 stale 键，
    直读会拿到过期配置（架构 §2.8.4 / dev-plan §D2「4. api_key 注入策略」末条）。
    """
    import streamlit as st

    from ui.components.llm_config_form import render_llm_config_form

    with st.sidebar:
        prefill = st.session_state.get("llm_config_set")
        cfg = render_llm_config_form(default=prefill)
    return cfg


def main() -> None:
    """Streamlit 主入口：初始化 session_state 单例 + 侧栏表单 + page 路由。

    三个业务页面（S2-05 论文输入 / S2-06 进度 / S2-07 计划审核）由任务 D3/D4/D5 实现；
    D2 仅搭建路由骨架，页面模块缺失时优雅降级提示（不让 D2 main 崩溃）。
    """
    import streamlit as st

    st.set_page_config(page_title="论文自动复现系统", layout="wide")

    _init_session_state()
    controller = _get_controller()  # noqa: F841 - 单例预热，供页面消费

    # 侧栏由各页面自行渲染（D3/D4/D5 各自调 render_llm_config_form）。
    # 此处不调 _render_sidebar()——D3 落地后 paper_input.render() 自己渲染侧栏，
    # main 里重复调用会导致 StreamlitDuplicateElementKey（key='default_base_url'）。

    current_page = st.session_state.get("current_page", "input")
    page_map = {
        "input": ("ui.pages.paper_input", "render_paper_input_page"),
        "progress": ("ui.pages.analysis_progress", "render_analysis_progress_page"),
        "review": ("ui.pages.plan_review", "render_plan_review_page"),
    }

    module_name, func_name = page_map.get(current_page, page_map["input"])
    try:
        import importlib

        page_module = importlib.import_module(module_name)
        render_fn = getattr(page_module, func_name)
    except (ImportError, AttributeError):
        # D3/D4/D5 尚未实现：D2 仅搭路由骨架，优雅降级提示，不崩溃。
        st.info(
            f"页面 `{current_page}` 尚未实现（由任务 D3/D4/D5 交付）。"
            "GraphController 已就绪，等待 UI 页面接入。"
        )
        return

    render_fn()


if __name__ == "__main__":
    main()
