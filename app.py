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
from pathlib import Path
from typing import Dict, Optional, Tuple

from langgraph.types import Command

from config import (
    PROJECT_ROOT,
    STREAMLIT_PAGE_EXECUTION,
    STREAMLIT_PAGE_INPUT,
    STREAMLIT_PAGE_PROGRESS,
    STREAMLIT_PAGE_REPORT,
    STREAMLIT_PAGE_REVIEW,
)
from core.activity_stream import ActivityEvent, ActivityStreamHandler, snapshot_tail
from core.checkpointer import get_checkpointer
from core.graph import build_graph
from core.state import GlobalState, LLMConfigSet, create_initial_state

# 自动加载 .env（与 tests/conftest.py 范式一致，架构 §2.7.2 末条）：
# 项目根优先 > ~/.env（deepxiv CLI 自动注册写入位置）。已存在的 env 变量不被覆盖。
# 必须在导入期注入，否则 create_llm 的 api_key 回退取到的 os.environ 无 LLM_API_KEY。
try:
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env", override=False)
    load_dotenv(Path.home() / ".env", override=False)
except ImportError:
    pass

logger = logging.getLogger(__name__)


# 支持节点级 LLM 覆写的 4 个节点名（与 core.state.NodeName / PRD §2.4 强一致）。
_OVERRIDE_NODES = ("paper_intake", "paper_analysis", "resource_scout", "planning")


# UI 页面路由表（架构 §2.6.1）：current_page(config 常量) → (模块名, render 函数名)。
# 键统一用 config.STREAMLIT_PAGE_* 常量，避免字面量散落（A1 阶段已落地两页常量）。
# sp2 三页（input/progress/review）已实现；sp3 两页（execution/report）由任务 E2/E3
# 实现，页面模块/函数尚不存在时由 main() 的 ImportError/AttributeError 优雅降级兜底，
# 保证 `streamlit run app.py` 仍可启动（不报 import 错）。
_PAGE_MAP: Dict[str, tuple] = {
    STREAMLIT_PAGE_INPUT: ("ui.pages.paper_input", "render_paper_input_page"),
    STREAMLIT_PAGE_PROGRESS: ("ui.pages.analysis_progress", "render_analysis_progress_page"),
    STREAMLIT_PAGE_REVIEW: ("ui.pages.plan_review", "render_plan_review_page"),
    # --- Sprint 3 新增两页（E2/E3 将提供下列模块/函数；当前为预留路由入口）---
    STREAMLIT_PAGE_EXECUTION: ("ui.pages.execution_monitor", "render_execution_monitor_page"),
    STREAMLIT_PAGE_REPORT: ("ui.pages.result_report", "render_result_report_page"),
}


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
        # [S5-07/T-S5-4-2] per-thread 活动流 handler（架构 sprint5 §4 Q-S5-8）：
        # 纯内存 dict，每 thread_id 一个 ActivityStreamHandler（自带 deque(maxlen)），
        # per-thread 隔离由"每 thread 一个实例"自然达成；不持久化、不进 checkpoint、
        # 不进 state（AC-S5-14 三个"不"），不建额外锁/清理线程/TTL（极简裁决）。
        self._activity_handlers: Dict[str, ActivityStreamHandler] = {}

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
        """工作线程入口。每线程独立创建 SqliteSaver + graph（架构 §4.3 方案 A）。

        [S5-07/T-S5-4-2] config 注入 per-thread 活动流 callbacks：langchain-core
        经 ``var_child_runnable_config`` contextvar 自动向嵌套 Runnable 传播父级
        callbacks，穿透节点内手动 ``subgraph.invoke`` 边界（coding/execution 两
        路径，T-S5-0-1 spike 实证主路径，react_base/execution 编排层零改动）。
        """
        try:
            worker_checkpointer = get_checkpointer()  # 独立实例（不共享主线程实例）
            worker_graph = build_graph(checkpointer=worker_checkpointer)
            config = _make_config(thread_id)
            handler = self._get_activity_handler(thread_id)
            # 跑到 interrupt 自然暂停
            worker_graph.invoke(initial_state, {**config, "callbacks": [handler]})
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
        """resume 工作线程入口。又一个独立 SqliteSaver 实例（架构 §4.3）。

        [S5-07/T-S5-4-2] resume 路径**复用同一 handler 实例**（get-or-create 命中
        既有实例）：seq 连续性靠实例内计数器，跨 invoke/resume 单调不重置。
        """
        try:
            worker_checkpointer = get_checkpointer()  # 又一个独立实例
            worker_graph = build_graph(checkpointer=worker_checkpointer)
            config = _make_config(thread_id)
            handler = self._get_activity_handler(thread_id)
            worker_graph.invoke(
                Command(resume=resume_payload), {**config, "callbacks": [handler]})
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

    def is_finished(self, thread_id: str) -> bool:
        """判定 graph 是否已运行至 END（S5-08 完成判定兜底，架构 sprint5 §7.8）。

        判定形态（与 is_interrupted 同一读路径范式，纯只读、不改 state）：snapshot
        存在 **且** snapshot.next 为空元组。运行中 / interrupt 暂停时 next 非空 →
        False。"存在"须校验 snapshot.values 非空——LangGraph 对从未启动的 thread_id
        返回 values={} 的空快照（next 也是空元组），不能误判为已完成。
        """
        config = _make_config(thread_id)
        snapshot = self._main_graph.get_state(config)
        if not snapshot or not getattr(snapshot, "values", None):
            return False
        return not snapshot.next

    @staticmethod
    def _has_interrupt(snapshot) -> bool:
        """扫描 StateSnapshot.tasks，判定是否存在 interrupt 元数据（S-1 spike 形态）。"""
        tasks = getattr(snapshot, "tasks", None) or ()
        for task in tasks:
            interrupts = getattr(task, "interrupts", None) or ()
            if len(interrupts) > 0:
                return True
        return False

    def get_interrupt_payload(self, thread_id: str) -> Optional[Dict]:
        """返回 planning interrupt 的 payload(interrupts[0].value)，无 interrupt 时 None。

        主线程只读，走 self._main_graph.get_state（与 poll_state / is_interrupted 同一
        读路径，独立 main_checkpointer，不阻塞工作线程）。审核数据(reproduction_plan 等)
        在 interrupt 暂停时尚未写入 snapshot.values，只存在于 interrupt payload dict 中
        （C1 e2e 实证），故 plan_review 页须经本方法取审核数据，而非 poll_state（S2-07 / D5）。

        判定与 is_interrupted 一致：snapshot.next 非空且某 task 含 interrupts；命中即返回
        interrupts[0].value（planning 节点 interrupt(payload) 注入的 dict）。
        """
        config = _make_config(thread_id)
        snapshot = self._main_graph.get_state(config)
        if not (snapshot and snapshot.next):
            return None
        for task in (getattr(snapshot, "tasks", None) or ()):
            interrupts = getattr(task, "interrupts", None) or ()
            if interrupts:
                return interrupts[0].value
        return None

    def interrupt_kind(self, thread_id: str) -> Optional[str]:
        """区分当前 interrupt 是 planning(interrupt#1) 还是 dev_loop_failure(interrupt#2)。

        Sprint 3 任务 E1（架构 §2.6.1）。纯只读 helper：复用 get_interrupt_payload
        的读路径（主线程 self._main_graph.get_state，独立 main_checkpointer，不阻塞工作
        线程），**不改 state、不调 LLM**。

        读 get_interrupt_payload(thread_id) 的 payload，返回 payload.get("interrupt_kind")：
            - "planning"          → 计划审核页（sp2 plan_review）；
            - "dev_loop_failure"  → 执行监控页 dev_loop 失败决策面板（sp3 execution_monitor）。

        判定逻辑：
            - 无 interrupt（payload 为 None / 空 dict）→ 返回 None；
            - 有 interrupt 但 payload 无 "interrupt_kind" 键 → 默认 "planning" 兜底
              （向后兼容 sp2 老 planning payload；D1 后新 planning payload 已显式带
              "interrupt_kind"="planning"，此兜底仅护旧 checkpoint）。
        """
        payload = self.get_interrupt_payload(thread_id)
        if not payload:
            return None
        return payload.get("interrupt_kind", "planning")

    def get_worker_error(self, thread_id: str) -> Optional[Exception]:
        """返回工作线程捕获的异常对象（无则 None），由 UI 检测展示。"""
        with self._lock:
            return self._worker_errors.get(thread_id)

    # ------------------------------------------------------------------
    # 活动流（S5-07 / T-S5-4-2，架构 sprint5 §4 Q-S5-8）
    # ------------------------------------------------------------------

    def _get_activity_handler(self, thread_id: str) -> ActivityStreamHandler:
        """get-or-create per-thread 活动流 handler（**写侧专用**，worker/resume 调用）。

        resume 必须复用 start 时的同一实例——seq 连续性靠实例内计数器（T-S5-4-1
        契约）。``dict.setdefault`` 在 CPython GIL 下原子，极简方案不另建锁（与
        deque 原子 append 同一 R-9 尽力而为语义）。
        """
        handler = self._activity_handlers.get(thread_id)
        if handler is None:
            handler = self._activity_handlers.setdefault(
                thread_id, ActivityStreamHandler())
        return handler

    def get_activity_tail(
        self, thread_id: str, n: Optional[int] = None,
    ) -> Tuple[ActivityEvent, ...]:
        """返回该 thread 活动流尾部 n 条事件的不可变快照（UI 轮询消费，纯内存只读）。

        - ``n=None`` 全量；越界安全语义由 snapshot_tail 保证（``n<=0`` 空 tuple、
          ``n>=len`` 全量）；
        - thread 无 handler（从未启动 / 进程重启后）→ 返回空 tuple，**只读方法
          不建 handler**（可观测性尽力而为语义，进程重启即失属预期）；
        - 返回 tuple 快照与底层 deque 解耦（R-9 线程安全读侧），UI 侧只读。
        """
        handler = self._activity_handlers.get(thread_id)
        if handler is None:
            return ()
        return snapshot_tail(handler.events, n)

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


# interrupt#3 类型标识（与 core/tools/interaction_tools.py::INTERRUPT_KIND_USER_INPUT
# 对齐，S4-09/F1；沿用 UI 侧本地字符串 + 单测断言防漂移的先例，不引入工具模块 import）。
_INTERRUPT_KIND_USER_INPUT: str = "user_input_request"


def _should_route_to_user_input_panel(
    current_page: str,
    controller: "GraphController",
    thread_id: Optional[str],
) -> bool:
    """[S4-09/F1] 判定是否需把路由强制切到执行监控页的用户输入面板（纯逻辑可直测）。

    interrupt#3（user_input_request）可能在 coding / execution 任一阶段由工具触发，
    彼时用户可能仍停在 review / progress 页（如 approve 后 plan_review 的 awaiting
    轮询态）——这些页面不认识第三类 interrupt，会一直「等待中」。故在 main() 页面
    分发前统一判定：有任务 + 非执行监控页 + 处于 user_input_request interrupt →
    强制路由到执行监控页（该页 case⑤ 渲染用户输入面板）。

    惰性求值：仅 is_interrupted 为真才读 interrupt_kind（省一次 checkpoint 读）。
    planning / dev_loop_failure 两类不经本分支（沿用 sp2/sp3 各页自身路由）。
    """
    if not thread_id or current_page == STREAMLIT_PAGE_EXECUTION:
        return False
    if not controller.is_interrupted(thread_id):
        return False
    return controller.interrupt_kind(thread_id) == _INTERRUPT_KIND_USER_INPUT


def _init_session_state() -> None:
    """初始化主入口所需的 session_state 字段。"""
    import streamlit as st

    st.session_state.setdefault("thread_id", None)
    st.session_state.setdefault("llm_config_set", None)
    st.session_state.setdefault("current_page", STREAMLIT_PAGE_INPUT)
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

    页面路由表 = 模块级 _PAGE_MAP（架构 §2.6.1）：
        - sp2 三页（S2-05 论文输入 / S2-06 进度 / S2-07 计划审核）由任务 D3/D4/D5 实现；
        - sp3 两页（S3-10 执行监控 / 结果报告）由任务 E2/E3 实现，E1 仅把两页常量接入
          路由分发并预留渲染入口——页面模块/函数尚不存在时由下方 ImportError/AttributeError
          优雅降级提示兜底，保证 `streamlit run app.py` 仍可启动（不报 import 错）。
    """
    import streamlit as st

    st.set_page_config(page_title="论文自动复现系统", layout="wide")

    _init_session_state()
    controller = _get_controller()  # noqa: F841 - 单例预热，供页面消费

    # 侧栏由各页面自行渲染（D3/D4/D5 各自调 render_llm_config_form）。
    # 此处不调 _render_sidebar()——D3 落地后 paper_input.render() 自己渲染侧栏，
    # main 里重复调用会导致 StreamlitDuplicateElementKey（key='default_base_url'）。

    current_page = st.session_state.get("current_page", STREAMLIT_PAGE_INPUT)

    # [S4-09/F1] interrupt#3 全局路由：user_input_request → 执行监控页用户输入面板。
    if _should_route_to_user_input_panel(
        current_page, controller, st.session_state.get("thread_id")
    ):
        st.session_state["current_page"] = STREAMLIT_PAGE_EXECUTION
        current_page = STREAMLIT_PAGE_EXECUTION

    module_name, func_name = _PAGE_MAP.get(current_page, _PAGE_MAP[STREAMLIT_PAGE_INPUT])
    try:
        import importlib

        page_module = importlib.import_module(module_name)
        render_fn = getattr(page_module, func_name)
    except (ImportError, AttributeError):
        # 页面尚未实现（sp2 D3/D4/D5 早期 / sp3 execution/report 由 E2/E3 交付）：
        # 路由骨架优雅降级提示，不崩溃。
        st.info(
            f"页面 `{current_page}` 尚未实现（由后续 UI 任务交付）。"
            "GraphController 已就绪，等待 UI 页面接入。"
        )
        return

    render_fn()


if __name__ == "__main__":
    main()
