"""[BUG-E2E2-03 验收补测] 闭合架构评估 §7「残留不确定性」中标为"未验证"的三项。

背景：`docs/bugfix-e2e2/architecture.md` §7 逐条登记了三项未取证的残留项。开发交付的
用例（L1/L2/L3）只覆盖了**已取证**的 coding 凭证 gate 路径；本文件补齐剩余三项，
并加一条比既有守门更强的红线 2 行为级探针。

| 本文件覆盖 | 架构 §7 项 | 结论 |
|---|---|---|
| §7-1 agent 工具路径（interaction_tools.py:175）一次节点执行内第 2 次 RUI | 标"未直接取证，高置信=是" | **实测推翻**：父图 next **非空**，不产生 bug 形态 |
| §7-4 planning revise / switch_repo 自环重入 | 标"源码层已确认，未实测" | **实测确认**：next 非空，反向证据成立 |
| §3.6 消费 resume 后 superstep 未提交就崩 | 标"已论证自愈，未实测" | **实测闭合**：形态复现 + 再答一次自愈、零串位 |

**§7-1 是本轮验收最重要的修正**：架构 §1.3「真实受影响面」表把
`core/tools/interaction_tools.py:175`（agent 多次调 request_user_input）判为「是（未直接取证，
高置信）」。实测证明该行判断**错误**——机制不同源：

- coding 凭证 gate（`coding.py:801-830`）：``interrupt()`` 在**父节点函数体**内串行调用两次，
  resume 被记为**父 task 自己的 `__resume__` write** → LangGraph 把带 writes 的 task 踢出 next
  （`main.py:1118-1138`）→ ``next=()``；
- agent 工具路径：``interrupt()`` 在 **ReAct 子图**内 raise，resume 消费发生在子图自己的命名空间；
  父图 checkpoint 上的 `__resume__` 只挂在 NULL_TASK（`00000000…`）哨兵上，**父 task 的 writes 恒空**
  → 父 task 留在 next 里 → ``next=('节点名',)``。
  且子图按 checkpoint 精确恢复到 interrupt 所在的 tool_executor（`test_sprint4_b2_*` 已证），
  前序 RUI 不重放，故父节点每次执行体内**只发生一次** interrupt。

修正后的真实受影响面收敛为**唯一一条**：父节点函数体内串行 ``interrupt()``，即 coding 凭证 gate
（及其"非法 resume 后重新 interrupt 同一项"变体）。这不削弱修复的必要性（现场实锤仍在），
但把"受影响面"从两条收敛到一条，且本文件把另一条**反向钉死**，防止未来有人据错误的表扩大改动面。

全离线（InMemorySaver + 脚本 LLM / stub ReAct），零 LLM、零网络、零 deepxiv 配额。

运行::

    .venv/bin/pytest -q tests/test_e2e2_acceptance_gaps.py
"""

from __future__ import annotations

import importlib
import json
import threading
import uuid
from typing import Any, Dict, List, Optional, TypedDict

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

import config
from app import (
    GraphController,
    TASK_STATUS_AWAITING,
    derive_task_status,
)
from core import secrets_store
from core.react_base import create_react_subgraph
from core.tools.interaction_tools import request_user_input

coding_module = importlib.import_module("core.nodes.coding")
planning_module = importlib.import_module("core.nodes.planning")

# 哨兵假值（带可辨识后缀防误撞真值）。
_PK_A = "env:E2E2ACC_KEY_A"
_PK_B = "env:E2E2ACC_KEY_B"
_VAL_A = "e2e2acc-fake-A-do-not-leak"
_VAL_B = "e2e2acc-fake-B-do-not-leak"


@pytest.fixture(autouse=True)
def _isolate_module_state(tmp_path, monkeypatch):
    """隔离 config.WORKSPACE_DIR + secrets_store 模块级 dict（绝不碰真实 .secrets）。"""
    ws = tmp_path / "workspace"
    ws.mkdir()
    monkeypatch.setattr(config, "WORKSPACE_DIR", ws)
    secrets_store._SESSION_SECRETS.clear()
    secrets_store._SENSITIVE_VALUES.clear()
    yield ws
    secrets_store._SESSION_SECRETS.clear()
    secrets_store._SENSITIVE_VALUES.clear()


def _controller_on(graph_app) -> GraphController:
    """真实 GraphController（绕过 __init__），_main_graph 指向传入的真图。"""
    c = GraphController.__new__(GraphController)
    c._lock = threading.Lock()
    c._workers = {}
    c._worker_errors = {}
    c._main_checkpointer = object()
    c._main_graph = graph_app
    return c


def _pending_writes(graph_app, cfg) -> List[tuple]:
    tup = graph_app.checkpointer.get_tuple(cfg)
    return list(tup.pending_writes or ())


# =========================================================================== #
# §7-1  agent 工具路径：一次父节点执行内第 2 次 request_user_input
# =========================================================================== #


class _TwoRUILLM(BaseChatModel):
    """脚本 LLM：按输入 messages 里的 ToolMessage 计数路由（replay 安全，无内部计数器）。

    mode="rounds"：第 1 轮问 A、第 2 轮问 B（两个独立轮次）。
    mode="batch"：第 1 轮把 A、B 两个 request_user_input 放进**同一批** tool_calls
                  （tool_executor 单次节点执行内串行两次 interrupt——最贴"一次执行内第 2 次调用"字面）。
    """

    mode: str

    @property
    def _llm_type(self) -> str:
        return "e2e2acc-two-rui"

    def bind_tools(self, tools: Any, **kwargs: Any) -> "_TwoRUILLM":
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        n_tool = sum(1 for m in messages if isinstance(m, ToolMessage))

        def _rui(pk: str, call_id: str) -> dict:
            return {
                "name": "request_user_input",
                "args": {"question": f"请提供 {pk}", "is_sensitive": True,
                         "purpose_key": pk},
                "id": call_id,
                "type": "tool_call",
            }

        if self.mode == "batch":
            if n_tool == 0:
                ai = AIMessage(content="", tool_calls=[_rui(_PK_A, "c_a"), _rui(_PK_B, "c_b")])
            else:
                ai = self._finish()
        else:
            if n_tool == 0:
                ai = AIMessage(content="", tool_calls=[_rui(_PK_A, "c_a")])
            elif n_tool == 1:
                ai = AIMessage(content="", tool_calls=[_rui(_PK_B, "c_b")])
            else:
                ai = self._finish()
        return ChatResult(generations=[ChatGeneration(message=ai)])

    @staticmethod
    def _finish() -> AIMessage:
        payload = json.dumps({"ok": True}, ensure_ascii=False, sort_keys=True)
        return AIMessage(
            content=f"{config.REACT_RESULT_TAG_OPEN}{payload}{config.REACT_RESULT_TAG_CLOSE}"
        )


class _AgentParentState(TypedDict, total=False):
    result: Optional[Dict[str, Any]]
    current_step: Optional[str]
    report_path: Optional[str]


def _build_agent_graph(llm: BaseChatModel):
    """父图单节点内调**真实** create_react_subgraph（与生产 _make_react_wrapper 同拓扑：
    react_base.py:828 建子图 / :873 subgraph.invoke，隐式 config 传播纳入父 checkpointer）。"""

    def agent_node(state: _AgentParentState) -> dict:
        subgraph = create_react_subgraph(
            node_name="e2e2acc_agent", system_prompt="t",
            tools=[request_user_input], max_rounds=8,
        )
        final = subgraph.invoke({
            "messages": [SystemMessage(content="s"), HumanMessage(content="go")],
            "round": 0, "max_rounds": 8, "status": "reasoning",
            "result": None, "context": {"_llm": llm},
        })
        return {"result": final.get("result"), "current_step": "coding"}

    builder = StateGraph(_AgentParentState)
    builder.add_node("agent", agent_node)
    builder.add_edge(START, "agent")
    builder.add_edge("agent", END)
    return builder.compile(checkpointer=InMemorySaver())


def _run_agent_to_second_interrupt(mode: str):
    """跑到"同一次父节点执行内的第 2 次 request_user_input"，返回 (graph, cfg, tid, snapshot)。"""
    graph_app = _build_agent_graph(_TwoRUILLM(mode=mode))
    tid = f"e2e2acc-agent-{mode}-{uuid.uuid4().hex[:8]}"
    cfg = {"configurable": {"thread_id": tid}}

    paused1 = graph_app.invoke(
        {"result": None, "current_step": None, "report_path": None}, cfg)
    intr1 = paused1.get("__interrupt__")
    assert intr1 and intr1[0].value["purpose_key"] == _PK_A, "前置：首次必须停在 A"

    paused2 = graph_app.invoke(
        Command(resume={"value": _VAL_A, "remember": False}), cfg)
    intr2 = paused2.get("__interrupt__")
    assert intr2 and intr2[0].value["purpose_key"] == _PK_B, "前置：resume A 后必须停在 B"

    return graph_app, cfg, tid, graph_app.get_state(cfg)


@pytest.mark.parametrize("mode", ["rounds", "batch"])
def test_e2e2_acc_agent_tool_path_keeps_next_nonempty(mode):
    """[架构 §7-1 取证·结论=否] agent 工具路径第 2 次 request_user_input **不产生**
    ``next=()`` 形态——父图 next 保持非空。

    这条推翻架构 §1.3 表里 `interaction_tools.py:175` 那行「是（未直接取证，高置信）」。
    机制锚见下方 pending_writes 断言：resume 只挂在 NULL_TASK 哨兵上，父 task 的 writes
    恒空，故 `main.py:1138` 的 "有 writes 的 task 被踢出 next" 不触发。
    """
    graph_app, cfg, _tid, snap = _run_agent_to_second_interrupt(mode)

    assert snap.next == ("agent",), (
        "agent 工具路径（子图内 interrupt）不产生 next=() 形态；"
        f"若此断言变红，说明 LangGraph 行为变化，架构 §1.3 受影响面表需重评。实际 next={snap.next!r}"
    )
    assert GraphController._has_interrupt(snap) is True

    # 机制锚：父 task 无 __resume__ write（resume 消费发生在子图命名空间）。
    parent_task_id = snap.tasks[0].id
    writes = _pending_writes(graph_app, cfg)
    assert any(ch == "__interrupt__" for _tid_, ch, _v in writes), "父 checkpoint 应有 __interrupt__"
    assert not any(t == parent_task_id and ch == "__resume__" for t, ch, _v in writes), (
        "父 task 不得有 __resume__ write——这正是它与 coding 凭证 gate 的分水岭"
    )


@pytest.mark.parametrize("mode", ["rounds", "batch"])
def test_e2e2_acc_agent_tool_path_all_five_judgements_correct(mode):
    """[架构 §7-1] 该形态下五个判定同样必须全对（面板要弹、状态要 awaiting）。

    形态虽与 coding gate 不同（next 非空），但用户可见结果必须一致——这条是把
    "另一条真实中断路径"整体锁住，与 t22 的 coding gate 姊妹组构成完整覆盖。
    """
    graph_app, _cfg, tid, snap = _run_agent_to_second_interrupt(mode)
    controller = _controller_on(graph_app)

    assert controller.is_interrupted(tid) is True
    assert controller.is_finished(tid) is False
    assert controller.get_interrupt_payload(tid)["purpose_key"] == _PK_B
    assert controller.get_interrupt_token(tid) is not None
    assert derive_task_status(snap, False) == TASK_STATUS_AWAITING
    # agent 工具路径 payload 永不含 allow_degrade（execution_monitor.py:59-65 既有红线）。
    assert "allow_degrade" not in controller.get_interrupt_payload(tid)


# =========================================================================== #
# §7-4  planning 的 revise / switch_repo 自环重入（反向证据：确实幸免）
# =========================================================================== #


def _fake_planning_react(state):
    return {
        "reproduction_plan": {
            "plan_summary": f"acc-fake-{state.get('_planning_revise_count') or 0}",
            "approved": False, "code_strategy": "", "execution_steps": [],
            "deliverables": [], "environment": {}, "risk_notes": "",
        },
        "current_step": "planning",
    }


def _build_planning_selfloop_graph():
    """planning 单节点 + **真实** _route_after_planning 三路条件边（含 self 自环）。"""
    from core.graph import _route_after_planning
    from core.state import GlobalState

    builder = StateGraph(GlobalState)
    builder.add_node("planning", planning_module.planning)
    builder.add_edge(START, "planning")
    builder.add_conditional_edges(
        "planning", _route_after_planning,
        {"self": "planning", "next": END, "end": END},
    )
    return builder.compile(checkpointer=InMemorySaver())


@pytest.mark.parametrize(
    "decision",
    [
        {"decision": "revise", "user_feedback": "把第 3 步拆细一点"},
        {"decision": "switch_repo", "new_repo_url": "https://github.com/acc/other"},
    ],
    ids=["revise", "switch_repo"],
)
def test_e2e2_acc_planning_selfloop_reentry_keeps_next_nonempty(decision, monkeypatch):
    """[架构 §7-4 取证·结论=幸免] planning 的 revise / switch_repo 走 self-loop 重入，
    产生**新 checkpoint**、无 `__resume__` pending write → ``next`` 非空，不属 BUG 形态。

    这是**反向证据**：证明修复的边界判断准确——受影响面确实没有蔓延到 planning，
    §5.2-5 之外无需任何额外特判。
    """
    monkeypatch.setattr(planning_module, "_planning_react", _fake_planning_react)
    graph_app = _build_planning_selfloop_graph()
    tid = f"e2e2acc-planning-{uuid.uuid4().hex[:8]}"
    cfg = {"configurable": {"thread_id": tid}}

    graph_app.invoke({"user_input": "x", "node_errors": [], "degraded_nodes": []}, cfg)
    first = graph_app.get_state(cfg)
    assert first.next == ("planning",), "前置：首问态 next 非空"

    graph_app.invoke(Command(resume=decision), cfg)
    snap = graph_app.get_state(cfg)

    assert snap.next == ("planning",), (
        "self-loop 重入是新 checkpoint，不该出现 next=()；"
        f"实际 {snap.next!r}——若变红说明架构 §1.3「planning 幸免」结论被推翻"
    )
    assert GraphController._has_interrupt(snap) is True
    assert not any(ch == "__resume__" for _t, ch, _v in _pending_writes(graph_app, cfg)), (
        "self-loop 重入后的最新 checkpoint 不得残留 __resume__（这正是它幸免的机制）"
    )

    controller = _controller_on(graph_app)
    assert controller.is_interrupted(tid) is True
    assert controller.is_finished(tid) is False
    assert derive_task_status(snap, False) == TASK_STATUS_AWAITING


# =========================================================================== #
# §3.6  "消费了 resume、越过 gate，但 superstep 未提交就崩" 的已知角落
# =========================================================================== #


class _SimulatedProcessKill(BaseException):
    """继承 BaseException：LangGraph 只捕 Exception，故不写 `__error__`，
    等价于进程被 kill（与真实崩溃同形态）。刻意不用 KeyboardInterrupt——那会中断 pytest 会话。"""


class _KillOnceReactStub:
    """第 1 次调用模拟进程被杀，之后正常返回（用于观察"再答一次"能否自愈）。"""

    def __init__(self) -> None:
        self.calls = 0
        self.seen: List[dict] = []

    def __call__(self, state):
        self.calls += 1
        self.seen.append(state)
        if self.calls == 1:
            raise _SimulatedProcessKill("simulated hard kill mid-superstep")
        return {"current_step": "coding", "code_output_dir": "/tmp/e2e2acc"}


class _GateState(TypedDict, total=False):
    reproduction_plan: Dict[str, Any]
    credential_degradations: Dict[str, str]
    current_step: str
    code_output_dir: str


def _two_missing_state() -> Dict[str, Any]:
    return {
        "reproduction_plan": {
            "approved": True,
            "required_credentials": [
                {"purpose_key": _PK_A, "purpose": "A 用途（验收假场景）"},
                {"purpose_key": _PK_B, "purpose": "B 用途（验收假场景）"},
            ],
        },
        "credential_degradations": {},
    }


def _run_gate_to_crash(monkeypatch):
    """跑到 §3.6 角落：A 已答、B 已答并落盘、gate 放行后进 ReAct 时进程被杀。

    返回 (graph, cfg, tid, stub)。
    """
    stub = _KillOnceReactStub()
    monkeypatch.setattr(coding_module, "_coding_react", stub)

    builder = StateGraph(_GateState)
    builder.add_node("coding", coding_module.coding)
    builder.add_edge(START, "coding")
    builder.add_edge("coding", END)
    graph_app = builder.compile(checkpointer=InMemorySaver())

    tid = f"e2e2acc-crash-{uuid.uuid4().hex[:8]}"
    cfg = {"configurable": {"thread_id": tid}}

    graph_app.invoke(_two_missing_state(), cfg)
    graph_app.invoke(Command(resume={"value": _VAL_A, "remember": False}), cfg)
    with pytest.raises(_SimulatedProcessKill):
        graph_app.invoke(Command(resume={"value": _VAL_B, "remember": True}), cfg)

    return graph_app, cfg, tid, stub


def test_e2e2_acc_crash_after_resume_consumed_is_judged_awaiting_not_no_report(monkeypatch):
    """[架构 §3.6 取证] 崩溃后最新 checkpoint = `__interrupt__`(已答) + `__resume__`，
    ``next=()`` → 新判定式给 **awaiting**（可自愈），而旧判定式给 no_report（死胡同）。

    这是 §3.6 登记的"已知可接受角落"的**首次实测**：形态真实存在，且新行为严格优于现状。
    """
    graph_app, cfg, tid, _stub = _run_gate_to_crash(monkeypatch)
    snap = graph_app.get_state(cfg)

    assert snap.next == (), "崩溃后最新 checkpoint 的 next 应为空元组（__resume__ 计入 task.writes）"
    assert GraphController._has_interrupt(snap) is True
    writes = _pending_writes(graph_app, cfg)
    assert any(ch == "__interrupt__" for _t, ch, _v in writes)
    assert any(ch == "__resume__" for _t, ch, _v in writes)

    controller = _controller_on(graph_app)
    assert controller.is_interrupted(tid) is True
    assert controller.is_finished(tid) is False
    assert derive_task_status(snap, False) == TASK_STATUS_AWAITING, (
        "旧逻辑在此形态判 no_report「失败·未产报告」，是死胡同；新逻辑必须判 awaiting"
    )
    # 如实钉住已知边界：重弹的是**上一个已答过的问题**（§3.6 明确接受该表现）。
    assert controller.get_interrupt_payload(tid)["purpose_key"] == _PK_B


def test_e2e2_acc_crash_corner_self_heals_on_reanswer_without_crosstalk(monkeypatch):
    """[架构 §3.6 取证] 用户"再答一次"即自愈：节点放行完成、多余 resume 值不被消费、
    A/B 两项值不串位、不产生虚假降级标记。

    额外清空进程内会话层，忠实模拟"真进程重启"（会话层是进程内 dict，重启后必空）——
    此时 A 会被重新索要并由 task 级 `__resume__` 列表**按调用序对位**补上，
    B 因崩溃前已落盘 `.secrets` 而不再索要。
    """
    ws = config.WORKSPACE_DIR
    graph_app, cfg, tid, stub = _run_gate_to_crash(monkeypatch)

    # 崩溃前 B 的落盘副作用已提交（这正是 §3.6 描述的窗口）。
    secrets_path = ws / ".secrets"
    assert secrets_path.exists(), "前置：崩溃前 remember=True 的 B 已落盘"

    secrets_store._SESSION_SECRETS.clear()  # 模拟真进程重启

    final = graph_app.invoke(
        Command(resume={"value": "再答一次-会被丢弃", "remember": False}), cfg)

    assert "__interrupt__" not in final, "再答一次后必须放行完成（自愈），不得再次暂停"
    assert stub.calls == 2, "ReAct 必须真正被执行（gate 已放行）"
    assert not final.get("credential_degradations"), "自愈路径不得留下虚假降级标记"

    # 不串位：A 按调用序对位消费回会话层；B 保持崩溃前落盘的原值（不被"再答"覆盖）。
    assert secrets_store._SESSION_SECRETS.get(_PK_A) == _VAL_A
    entries = json.loads(secrets_path.read_text(encoding="utf-8"))
    assert entries[_PK_B]["value"] == _VAL_B, "多余的 resume 值不得覆盖已落盘的 B"
    assert _PK_A not in entries, "A（不记住）绝不落盘"

    snap = graph_app.get_state(cfg)
    assert snap.next == () and snap.tasks == ()
    assert GraphController._has_interrupt(snap) is False


# =========================================================================== #
# 红线 §5.2-2 行为级强守门（比"替身不定义该属性"更强：正反双向钉死判定口径）
# =========================================================================== #


class _TrapSnapshot:
    """陷阱替身：**同时**定义 tasks 与顶层 interrupts，且两者刻意取反。

    既有守门（tests/test_e2e2_interrupt_gate_fix.py:194-203）靠"替身不定义 interrupts
    属性"间接生效，其末条 ``assert not hasattr(...)`` 只是对替身自身的重言式；一旦有人
    给替身补上该属性，守门就静默失效。本类把口径正反双向钉死，不依赖属性缺失。
    """

    def __init__(self, tasks, top_level_interrupts):
        self.values = {"current_step": "coding"}
        self.next = ()
        self.tasks = tasks
        self.interrupts = top_level_interrupts


class _T:
    def __init__(self, interrupts=()):
        self.name = "coding"
        self.interrupts = interrupts


class _I:
    def __init__(self):
        self.value = {"question": "q"}
        self.id = "trap-1"


def test_e2e2_acc_has_interrupt_anchors_on_tasks_not_top_level_field():
    """[红线 §5.2-2 强守门] ``_has_interrupt`` 的唯一判定锚必须是 ``tasks[*].interrupts``，
    改读 ``snapshot.interrupts`` 顶层字段会让全仓只定义 values/next/tasks 的替身静默返回
    False，制造大面积假绿（架构 §3.5）。

    正反双向：
      (a) tasks 有 interrupt ∧ 顶层 interrupts 为空 → 必须 True；
      (b) tasks 无 interrupt ∧ 顶层 interrupts 非空 → 必须 False。
    任一方向都能单独杀死"改读顶层字段"的实现，且不依赖替身缺失该属性。
    """
    only_in_tasks = _TrapSnapshot(tasks=(_T(interrupts=(_I(),)),), top_level_interrupts=())
    assert GraphController._has_interrupt(only_in_tasks) is True, (
        "判定锚必须是 tasks[*].interrupts；顶层 interrupts 为空不代表没有挂起 interrupt"
    )

    only_top_level = _TrapSnapshot(tasks=(_T(interrupts=()),), top_level_interrupts=(_I(),))
    assert GraphController._has_interrupt(only_top_level) is False, (
        "顶层 interrupts 非空但 tasks 无 interrupt 时必须为 False（不得改读顶层字段）"
    )
