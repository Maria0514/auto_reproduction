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
    - is_interrupted 判定 = snapshot.tasks 含 interrupt 元数据（**不以 snapshot.next
      为前置门槛**）。BUG-E2E2-03：动态 interrupt 在"同一次节点执行内的第 2 次暂停"
      时 get_state().next 为空元组（LangGraph 把 __resume__ 计入 task.writes），
      S-1 spike CP-S1-3 只观测过首问态形态，不构成 next 门槛的依据；
    - 工作线程异常一律 try/except 写入 self._worker_errors[thread_id]，由 UI 检测展示
      （100% 工作线程崩溃感知率，架构 §2.7.1）。
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading
import uuid
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from langgraph.types import Command

from config import (
    CHECKPOINT_DB_PATH,
    PROJECT_ROOT,
    STREAMLIT_PAGE_EXECUTION,
    STREAMLIT_PAGE_INPUT,
    STREAMLIT_PAGE_PROGRESS,
    STREAMLIT_PAGE_REPORT,
    STREAMLIT_PAGE_REVIEW,
    STREAMLIT_PAGE_TASKS,
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
    # --- Sprint 6 新增：任务列表页（S6-07，枚举 + 挂回）---
    STREAMLIT_PAGE_TASKS: ("ui.pages.task_list", "render_task_list_page"),
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


# ======================================================================
# [S6-01/T-S6-3-1] 进程级 worker 登记表（架构 §1.2 第三道防线 / §5）
# ======================================================================
# 多 tab = 多 Streamlit session = 多 GraphController 实例，但同一进程——实例属性
# self._workers 无法横跨 tab。故把"存活 worker"权威登记表提升为 **模块级单一登记表**：
#     - resume_with 原子 check-and-set（防跨 tab / 双击窗口重复 resume，第三道防线）；
#     - §4 任务状态推导的"在途 vs 孤儿"区分锚（进程重启后登记表必空 → next 非空即孤儿）。
# 一个抽象两处复用（架构 §1.2 极简裁决）。self._workers 保留为实例侧兼容 shim（既有
# _join_worker / 测试读点零改动），生产存活判定一律走本登记表 has_active_worker。
_THREAD_WORKERS: Dict[str, threading.Thread] = {}
_THREAD_WORKERS_LOCK = threading.Lock()


def _register_worker(thread_id: str, thread: threading.Thread) -> None:
    """登记 thread_id → worker 线程（start_task / resume_with 起线程前调用）。"""
    with _THREAD_WORKERS_LOCK:
        _THREAD_WORKERS[thread_id] = thread


def _unregister_worker(thread_id: str, thread: threading.Thread) -> None:
    """worker 线程结束时注销（仅当登记的仍是自己才删——避免误删后续 resume 覆盖的新线程）。

    在 _worker_run / _resume_run 的 finally 调用，保证登记表反映真实存活（架构 §5 纪律）。
    """
    with _THREAD_WORKERS_LOCK:
        if _THREAD_WORKERS.get(thread_id) is thread:
            del _THREAD_WORKERS[thread_id]


def _reset_for_tests() -> None:
    """清空进程级 worker 登记表（R-S6-A4：用例间 thread_id 泄漏防护）。"""
    with _THREAD_WORKERS_LOCK:
        _THREAD_WORKERS.clear()


# ======================================================================
# [S6-07/T-S6-4-3] 任务状态确定性推导（架构 §4.1，纯函数 R1~R7）
# ======================================================================
# 状态取值（UI 徽标 + 挂回路由消费；与 derive_task_status 返回值强一致）。
TASK_STATUS_FAILED = "failed"          # R2：values.error 非空
TASK_STATUS_CANCELLED = "cancelled"    # R3：current_step==cancelled_by_user
TASK_STATUS_DONE = "done"                # R4a：无挂起 interrupt ∧ next 空 ∧ report_path 非空
TASK_STATUS_NO_REPORT = "no_report"      # R4b：无挂起 interrupt ∧ next 空 ∧ report_path 空
TASK_STATUS_AWAITING = "awaiting"        # R5：有挂起 interrupt（不看 next，BUG-E2E2-03）
TASK_STATUS_RUNNING = "running"          # R6：next 非空 ∧ 无 interrupt ∧ 有存活 worker
TASK_STATUS_INTERRUPTED = "interrupted"  # R7：next 非空 ∧ 无 interrupt ∧ 无存活 worker（孤儿）


def derive_task_status(snapshot, has_active_worker: bool) -> Optional[str]:
    """按优先级自上而下短路推导任务状态（架构 §4.1 规则表，纯函数）。

    优先级 ``R1>R2>R3>R5>R4>R6>R7``（[BUG-E2E2-03] R5 已上移到 R4 之前）。

    输入 = snapshot（GraphController._main_graph.get_state 只读组装）+ 该 thread 是否有
    存活 worker（查进程级 `_THREAD_WORKERS`）。返回状态串；R1（快照不存在/values 空）
    → None（不列出，与 is_finished 同款空快照防误判 app.py:254）。

    R5~R7 是 PRD"进程重启后无 worker 的在途任务口径"的答案：有 interrupt=等待输入
    （挂回应答即恢复，resume 是用户显式动作无副作用重放疑虑）；无 interrupt 的在途=已中断，
    区分锚 = 进程级 worker 登记表（进程重启后必空，next 非空即孤儿，判定确定）。
    R5 先于 R4 是 BUG-E2E2-03 的修复面（动态 interrupt 暂停时 next 可为空元组）。
    """
    if not snapshot or not getattr(snapshot, "values", None):
        return None  # R1
    values = snapshot.values
    if values.get("error"):
        return TASK_STATUS_FAILED  # R2
    if values.get("current_step") == "cancelled_by_user":
        return TASK_STATUS_CANCELLED  # R3
    # [BUG-E2E2-03] R5 提到 R4 之前：同一次节点执行内的第 2 次 interrupt 暂停时
    # snapshot.next 为空元组（langgraph/pregel/main.py:1118-1138 把 __resume__ 计入
    # task.writes），若先判 R4 会把"等待输入"误判成 done/no_report。
    # 反向安全性：图真到 END 时 snapshot.tasks 为空（main.py:1129-1134
    # tasks_w_writes 只遍历 next_tasks）→ 本行必不命中 → 仍落 R4，行为与改动前一致。
    if GraphController._has_interrupt(snapshot):
        return TASK_STATUS_AWAITING  # R5
    next_ = getattr(snapshot, "next", None) or ()
    if not next_:
        # R4a/R4b：图已到 END（且无挂起 interrupt）
        return TASK_STATUS_DONE if values.get("report_path") else TASK_STATUS_NO_REPORT
    if has_active_worker:
        return TASK_STATUS_RUNNING  # R6
    return TASK_STATUS_INTERRUPTED  # R7


# 状态 → 中文徽标（任务列表页 / 状态展示消费）。
TASK_STATUS_LABELS: Dict[str, str] = {
    TASK_STATUS_FAILED: "失败",
    TASK_STATUS_CANCELLED: "已终止",
    TASK_STATUS_DONE: "已完成",
    TASK_STATUS_NO_REPORT: "失败（未产报告）",
    TASK_STATUS_AWAITING: "等待输入",
    TASK_STATUS_RUNNING: "进行中",
    TASK_STATUS_INTERRUPTED: "已中断",
}


def _extract_paper_label(values: Dict) -> str:
    """论文标识三级回退（架构 §4.3）：``paper_meta.title_zh → title → user_input``。

    列表页每条 thread 的可读标识；paper_meta 缺失（早期阶段未拉取）时回退原始输入
    （user_input = arxiv_id）。任一层非空即返回，全空兜底空串（调用方决定占位）。
    """
    meta = values.get("paper_meta") or {}
    if isinstance(meta, dict):
        for key in ("title_zh", "title"):
            val = meta.get(key)
            if val:
                return str(val)
    user_input = values.get("user_input")
    return str(user_input) if user_input else ""


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
        # [T-S6-3-1] 进程级登记表注册（在途任务判定锚）+ 实例侧兼容 shim。
        _register_worker(thread_id, thread)
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
        finally:
            # [T-S6-3-1] 线程结束注销进程级登记表（登记表反映真实存活，架构 §5 纪律）。
            _unregister_worker(thread_id, threading.current_thread())

    def resume_with(
        self,
        thread_id: str,
        resume_payload: Dict,
        expected_interrupt_token: Optional[str] = None,
    ) -> bool:
        """通过**新工作线程**调用 graph.invoke(Command(resume=...))，返回是否已发起。

        关键：不能在主线程同步调用 invoke()，否则 UI 阻塞；需要新起一个 daemon worker
        （架构 §2.7.1 / R-S2-02）。

        [S6-01/T-S6-3-1] 防误提交 / "同一 interrupt 至多一次 resume"三道防线（架构 §1.2）：
            - 第二道（token 校验，跨 tab 有效）：``expected_interrupt_token`` 非 None 时，
              发起线程前重读当前 interrupt_token，不一致 → 拒绝（WARNING + 返回 False，
              不抛异常），挡住"迟到的提交"（interrupt 已换代/已消失后才点下的按钮）；
              缺省 None → 不校验（plan_review 等既有调用零改动，向后兼容）。
            - 第三道（进程级原子 check-and-set，跨 tab / 双击窗口）：模块级 ``_THREAD_WORKERS``
              该 thread 已有存活线程 → 拒绝（返回 False），防重复 resume。
        返回值：成功发起 worker → True；被任一防线拒绝 → False（既有调用忽略返回值仍安全）。
        """
        # 第二道防线：token 校验（非 None 时；发起前重读当前 token）。
        if expected_interrupt_token is not None:
            current_token = self.get_interrupt_token(thread_id)
            if current_token != expected_interrupt_token:
                logger.warning(
                    "[resume:%s] interrupt_token 不一致，拒绝迟到提交"
                    "（期望=%s 实际=%s）",
                    thread_id, expected_interrupt_token, current_token,
                )
                return False

        thread = threading.Thread(
            target=self._resume_run,
            args=(thread_id, resume_payload),
            daemon=True,
            name=f"graph-resume-{thread_id}",
        )
        # 第三道防线：进程级原子 check-and-set（check 与 set 同一临界区）。
        with _THREAD_WORKERS_LOCK:
            existing = _THREAD_WORKERS.get(thread_id)
            if existing is not None and existing.is_alive():
                logger.warning(
                    "[resume:%s] 已有存活 worker，拒绝重复 resume（防跨 tab / 双击重放）",
                    thread_id,
                )
                return False
            _THREAD_WORKERS[thread_id] = thread
        with self._lock:  # 实例侧兼容 shim（既有 _join_worker / 测试读点）。
            self._workers[thread_id] = thread
        thread.start()
        return True

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
        finally:
            # [T-S6-3-1] 线程结束注销进程级登记表（架构 §5 纪律）。
            _unregister_worker(thread_id, threading.current_thread())

    # ------------------------------------------------------------------
    # 主线程只读
    # ------------------------------------------------------------------

    def poll_state(self, thread_id: str) -> Optional[GlobalState]:
        """主线程通过独立 main_graph（main_checkpointer）读取 state，不阻塞工作线程。"""
        config = _make_config(thread_id)
        snapshot = self._main_graph.get_state(config)
        return snapshot.values if snapshot else None

    def is_interrupted(self, thread_id: str) -> bool:
        """判定 graph 是否停在**挂起的 interrupt** 上（三类 interrupt 通用）。

        判定依据 = ``snapshot.tasks[*].interrupts`` 非空（_has_interrupt）。
        **严禁再引入 ``snapshot.next`` 作为前置门槛**（BUG-E2E2-03 根因）：本项目
        全程使用动态 interrupt（节点函数体内 raise），当**同一次节点执行内发生第 2 次
        及以后的 interrupt** 时（coding 凭证 gate 串行索要多项 / agent 多次调
        request_user_input），该 checkpoint 上会同时存在 ``__resume__`` 与
        ``__interrupt__`` 两类 pending write；LangGraph 的 get_state 走
        apply_pending_writes=True（langgraph/pregel/main.py:1308），把带 writes 的
        task 从 next 中剔除（main.py:1118-1124 / :1138）→ ``snapshot.next`` 变成空元组，
        而中断信息仍完整挂在 tasks[*].interrupts 上。

        适用范围：仅对 ``get_state(config)``（不带 checkpoint_id 的**最新**快照）成立。
        ``get_state_history`` 走 apply_pending_writes=False（main.py:1352），会回放
        **已被消费**的 interrupt——本方法及其同族读方法一律不得改读 history。

        图已跑到 END 时 snapshot.tasks 为空元组（tasks_w_writes 只遍历 next_tasks，
        main.py:1129-1134），故 _has_interrupt 必为 False，不存在"已完成被误判为等待输入"。
        """
        config = _make_config(thread_id)
        snapshot = self._main_graph.get_state(config)
        return bool(snapshot and self._has_interrupt(snapshot))

    def is_finished(self, thread_id: str) -> bool:
        """判定 graph 是否已运行至 END（S5-08 完成判定兜底，架构 sprint5 §7.8）。

        判定形态（与 is_interrupted 同一读路径范式，纯只读、不改 state）：snapshot
        存在 ∧ values 非空 ∧ ``snapshot.next`` 为空元组 ∧ **无挂起 interrupt**。

        [BUG-E2E2-03] 第三个合取项不可省：同一次节点执行内的第 2 次 interrupt 暂停时
        ``next`` 也是空元组（LangGraph 把 __resume__ 计入 task.writes，
        langgraph/pregel/main.py:1118-1138），只看 next 会把"暂停等输入"误判为"已结束"。
        加上 not _has_interrupt 后，与 is_interrupted **语义正交**恢复成立
        （tests/test_sprint5_s5_08_routing.py:437-448 的既有契约）。

        "存在"须校验 snapshot.values 非空——LangGraph 对从未启动的 thread_id
        返回 values={} 的空快照（next 也是空元组），不能误判为已完成。
        """
        config = _make_config(thread_id)
        snapshot = self._main_graph.get_state(config)
        if not snapshot or not getattr(snapshot, "values", None):
            return False
        return not snapshot.next and not self._has_interrupt(snapshot)

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

        判定与 is_interrupted 一致：某 task 含 interrupts（**不看 snapshot.next**，
        BUG-E2E2-03：同节点第 2 次 interrupt 时 next 为空元组）；命中即返回
        interrupts[0].value（planning 节点 interrupt(payload) 注入的 dict）。
        """
        config = _make_config(thread_id)
        snapshot = self._main_graph.get_state(config)
        if not snapshot:
            return None
        for task in (getattr(snapshot, "tasks", None) or ()):
            interrupts = getattr(task, "interrupts", None) or ()
            if interrupts:
                return interrupts[0].value
        return None

    def get_interrupt_token(self, thread_id: str) -> Optional[str]:
        """返回当前 interrupt 的**复合换代 token** = ``id:指纹``，无 interrupt → None。

        [S6-01/T-S6-3-1，架构 §1.2] 复合三元判定锚：``interrupt.id``（锚）+ payload 的
        16 字符 sha1 指纹拼成 ``{id}:{fingerprint}``。与 get_interrupt_payload 同一读路径
        （主线程 _main_graph.get_state 只读）。用于 UI 换代判定：

            - payload 变化 → 指纹变 → token 变（新问题，禁沿用旧 resume 提交）；
            - 相同 payload → token 相同（同一代，配合 worker 存活判过渡态）。

        安全纪律：token 只含 payload 哈希，敏感 question 原文不外泄（CP-3.1-2）。
        防御（R-S6-A1）：``interrupt.id`` 取不到（getattr 失败 / 为 None）时退化为**纯指纹**，
        判定仍可用（同 payload 仍同 token）。

        判定口径（BUG-E2E2-03）：与 is_interrupted 同源，**不以 snapshot.next 为前置**——
        否则同节点第 2 次 interrupt 时 token 退化为 None，S6-01 换代判定与 resume_with
        第二道防线（app.py:327-335）会把用户的正常提交误判为"迟到提交"并拒绝。
        """
        config = _make_config(thread_id)
        snapshot = self._main_graph.get_state(config)
        if not snapshot:
            return None
        for task in (getattr(snapshot, "tasks", None) or ()):
            interrupts = getattr(task, "interrupts", None) or ()
            if not interrupts:
                continue
            interrupt = interrupts[0]
            value = getattr(interrupt, "value", None)
            fingerprint = hashlib.sha1(
                json.dumps(
                    value, sort_keys=True, ensure_ascii=False, default=str
                ).encode("utf-8")
            ).hexdigest()[:16]
            interrupt_id = getattr(interrupt, "id", None)
            if interrupt_id is None:
                return fingerprint  # R-S6-A1 退化：无 id → 纯指纹
            return f"{interrupt_id}:{fingerprint}"
        return None

    def has_active_worker(self, thread_id: str) -> bool:
        """该 thread 是否有**存活** worker 线程（进程级登记表只读，架构 §1.2 第三道 / §5）。

        判定 = 模块级 ``_THREAD_WORKERS`` 命中 ∧ 线程 ``.is_alive()``。服务两处（一个抽象）：
            - UI 换代过渡态判定（token 相同 ∧ 有存活 worker → "处理中"，否则视为换代）；
            - §4 任务状态推导"在途 vs 孤儿"（进程重启后登记表必空 → next 非空即孤儿）。
        """
        with _THREAD_WORKERS_LOCK:
            thread = _THREAD_WORKERS.get(thread_id)
        return bool(thread is not None and thread.is_alive())

    def get_phase(self, thread_id: str) -> Dict:
        """只读推导在途阶段（架构 §6.1，S6-02）：``{active_node, current_step}``。

        - ``active_node`` = ``snapshot.next[0]``（next 非空时的在途节点标签）| None；
        - ``current_step`` = ``snapshot.values["current_step"]``（回落既有口径用）。

        与 is_finished 同一读路径 + 空快照防御（从未启动的 thread 返回 values={} 空快照）：
        无快照 / 无 values → ``{active_node: None, current_step: None}`` 安全默认。
        **纯只读无副作用**（不碰登记表、不碰 checkpoint），仅供 UI 阶段标签只读消费。
        注意：interrupt 暂停时 next 也非空，active_node 会等于 interrupt 节点——消费方靠
        case 分发顺序（interrupt 分支先于在途标签）区分（架构 §6.2）。

        [BUG-E2E2-03] 另一边界：同一次节点执行内的**第 2 次** interrupt 暂停时
        get_state().next 为空元组 → active_node 为 None。消费方必须保持"interrupt 分支
        先于在途标签分支"的 case 分发顺序（execution_monitor.py:971 先于 :1025），
        否则该状态会掉进 case⑦ 假轮询。本方法不做补偿（不引入 history 第二读栈）。
        """
        config = _make_config(thread_id)
        snapshot = self._main_graph.get_state(config)
        if not snapshot or not getattr(snapshot, "values", None):
            return {"active_node": None, "current_step": None}
        next_ = getattr(snapshot, "next", None) or ()
        active_node = next_[0] if next_ else None
        current_step = snapshot.values.get("current_step")
        return {"active_node": active_node, "current_step": current_step}

    def get_task_status(self, thread_id: str) -> Optional[str]:
        """组装 snapshot + worker 存活 → derive_task_status（R1~R7，S6-07，架构 §4.1）。

        单 thread 的状态推导入口（重连路由 / 列表页共用）。R1（快照不存在）返回 None。
        """
        snapshot = self._main_graph.get_state(_make_config(thread_id))
        return derive_task_status(snapshot, self.has_active_worker(thread_id))

    def list_threads(self) -> List[Dict]:
        """只读枚举 checkpoints 库全部 thread → 状态 + 论文标识（S6-07，架构 §4.3）。

        - 读路径：``sqlite3`` **mode=ro** URI 连接跑 ``SELECT thread_id, MAX(checkpoint_id)
          GROUP BY thread_id ORDER BY 2 DESC``（checkpoint_id 时间有序 → 新任务在前）；
        - 随后逐 thread 走既有 ``_main_graph.get_state`` 组装状态（不新建第二套读栈）+
          论文标识三级回退（``paper_meta.title_zh → title → user_input``）；
        - 坏 thread（get_state 异常 / 反序列化失败）**逐条捕获跳过**，不炸整页（R-S6-A3）；
          R1（空快照）状态为 None 的 thread 不列出。

        返回：``[{thread_id, status, status_label, paper_label}, ...]``（新任务在前）。
        """
        db_path = str(CHECKPOINT_DB_PATH)
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        except sqlite3.Error:
            logger.exception("[list_threads] 打开只读连接失败: %s", db_path)
            return []
        try:
            rows = conn.execute(
                "SELECT thread_id, MAX(checkpoint_id) FROM checkpoints "
                "GROUP BY thread_id ORDER BY 2 DESC"
            ).fetchall()
        except sqlite3.Error:
            logger.exception("[list_threads] 枚举 checkpoints 失败")
            return []
        finally:
            conn.close()

        result: List[Dict] = []
        for row in rows:
            thread_id = row[0]
            try:
                snapshot = self._main_graph.get_state(_make_config(thread_id))
                status = derive_task_status(
                    snapshot, self.has_active_worker(thread_id))
                if status is None:
                    continue  # R1：不列出
                values = getattr(snapshot, "values", None) or {}
                result.append({
                    "thread_id": thread_id,
                    "status": status,
                    "status_label": TASK_STATUS_LABELS.get(status, status),
                    "paper_label": _extract_paper_label(values),
                })
            except Exception:  # noqa: BLE001 - 坏 thread 逐条跳过不炸整页（R-S6-A3）
                logger.exception("[list_threads] thread %s 组装失败，跳过", thread_id)
                continue
        return result

    def resume_task(self, thread_id: str) -> bool:
        """孤儿在途任务显式续跑（R7 卡片显式按钮**唯一**调用，S6-07，架构 §4.2）。

        新 daemon worker 执行 ``graph.invoke(None, config)``——LangGraph 语义：从最后
        checkpoint 重启在途节点，**该节点从头重放、其间命令/调用重新发生**（产品红线：
        推进须用户显式触发，列表页"挂回"绝不调用本方法）。

        并发防护（与 §1.2 第三道防线同一闸门）：进程级原子 check-and-set——已有存活 worker
        → 拒绝（返回 False），防 TOCTOU / 重复续跑。成功发起 → True。
        """
        thread = threading.Thread(
            target=self._resume_task_run,
            args=(thread_id,),
            daemon=True,
            name=f"graph-resume-{thread_id}",
        )
        with _THREAD_WORKERS_LOCK:
            existing = _THREAD_WORKERS.get(thread_id)
            if existing is not None and existing.is_alive():
                logger.warning(
                    "[resume_task:%s] 已有存活 worker，拒绝重复续跑", thread_id)
                return False
            _THREAD_WORKERS[thread_id] = thread
        with self._lock:
            self._workers[thread_id] = thread
        thread.start()
        return True

    def _resume_task_run(self, thread_id: str) -> None:
        """孤儿续跑 worker 入口：独立 SqliteSaver + graph，``invoke(None)`` 从断点重启。"""
        try:
            worker_checkpointer = get_checkpointer()
            worker_graph = build_graph(checkpointer=worker_checkpointer)
            config = _make_config(thread_id)
            handler = self._get_activity_handler(thread_id)
            worker_graph.invoke(None, {**config, "callbacks": [handler]})
        except Exception as e:  # noqa: BLE001
            logger.exception("[resume_task:%s] 异常", thread_id)
            with self._lock:
                self._worker_errors[thread_id] = e
        finally:
            _unregister_worker(thread_id, threading.current_thread())

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


def _route_for_status(controller: "GraphController", thread_id: str, status: str) -> str:
    """[S6-06/T-S6-4-1] 任务状态 → 挂回目标页（架构 §4.1 挂回列）。

    done→报告页；awaiting 按 interrupt_kind 分（planning→审核页；dev_loop/user_input→监控页）；
    failed/cancelled/no_report/running/interrupted → 执行监控页（该页 case 分发渲染对应
    终态卡片 / 正常轮询 / R7 孤儿卡片）。
    """
    if status == TASK_STATUS_DONE:
        return STREAMLIT_PAGE_REPORT
    if status == TASK_STATUS_AWAITING:
        kind = controller.interrupt_kind(thread_id)
        if kind in (None, "planning"):
            return STREAMLIT_PAGE_REVIEW
        return STREAMLIT_PAGE_EXECUTION
    return STREAMLIT_PAGE_EXECUTION


def _restore_from_query_params(controller: "GraphController") -> None:
    """[S6-06/T-S6-4-1] URL 重连（架构 §7.6）：``query_params['task']`` → 恢复 thread_id + 路由。

    **每个 session 首次加载只尝试一次**（``_restore_attempted`` 标志）：置位后同 session 内
    rerun / "返回输入页开启新任务"（清空 thread_id）不再重连——否则旧 task 参数会把清空的
    thread_id 重新激活（CP-4.2-2）。F5 刷新 = 新 session = 标志缺失 → 正常重连。

    **无参数路径字节等价红线**（AC-S6-14 / R-S6-4）：无 task 参数 ∨ session 已有 thread_id
    → 直接 return，main() 行为与现状完全一致（回归红线）。thread 不存在（R1 空快照）→ 安全
    回退不激活（不炸）。resume 有效性：重连后 controller 新实例，但 resume_with/resume_task
    本就每次新建独立 SqliteSaver + graph，resume 语义与原 session 等价（AC-S6-16）。
    """
    import streamlit as st

    if st.session_state.get("_restore_attempted"):
        return
    st.session_state["_restore_attempted"] = True
    if st.session_state.get("thread_id"):
        return  # 字节等价红线：session 已有任务，不覆盖
    task = st.query_params.get("task")
    if not task:
        return  # 字节等价红线：无 task 参数，与现状完全一致
    status = controller.get_task_status(task)
    if status is None:
        logger.info("[restore] query task=%s 不存在或空快照，忽略", task)
        return
    st.session_state["thread_id"] = task
    st.session_state["current_page"] = _route_for_status(controller, task, status)
    logger.info(
        "[restore] 重连 thread=%s status=%s → page=%s",
        task, status, st.session_state["current_page"],
    )


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

    # Sprint 6 MF-6：冷启动 spinner（架构 §7.8 MF-6 / AC-S6-22）。
    # controller 尚未创建（首次进入 / 进程重启）时用 spinner 包裹创建——
    # build_graph/checkpointer 初始化可能耗时约 40s，提示先于耗时段落地即可见。
    # 热路径（controller 已在 session_state）不渲染 spinner（无感）。
    if "graph_controller" not in st.session_state:
        with st.spinner("系统初始化中（首次启动约 40 秒）…"):
            controller = _get_controller()  # noqa: F841 - 单例预热，供页面消费
    else:
        controller = _get_controller()  # noqa: F841 - 单例预热，供页面消费

    # [S6-06/T-S6-4-1] URL 重连：仅 query_params 含 task ∧ session 无 thread_id 时激活
    # （每 session 一次）；无参数路径字节等价（AC-S6-14 红线）。
    _restore_from_query_params(controller)

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
