"""BUG-S4-B1-01 修复常驻断言：react_base 工具执行层不得吞 GraphInterrupt。

背景（docs/sprint4/test-reports/2026-07-03_b1-acceptance.md BUG-S4-B1-01 小节）：
`core/react_base.py::tool_executor_node` 的裸 ``except Exception``（sp1 容错设计，
把工具异常转 error ToolMessage 防子图崩溃）会把 langgraph 的 ``GraphInterrupt``
（MRO: GraphInterrupt → GraphBubbleUp → Exception）一并吞掉——B1 工具
``request_user_input``（体内调 ``langgraph.types.interrupt()``）在 ReAct 子图内
被调用时永不暂停，且吞没转写把含 question 全文的 payload 泄漏进 ToolMessage
与 WARNING 日志。

修复：tool_executor_node 在裸 ``except Exception`` 之前加
``except GraphBubbleUp: raise`` 直通放行（langgraph 1.1.10：GraphInterrupt /
NodeInterrupt / ParentCommand 的公共控制流基类均为 GraphBubbleUp）。

本文件断言矩阵：
    1. standalone 子图形态：GraphInterrupt 从 ``subgraph.invoke`` 冒泡（最小
       修复锚点，修复回退立刻翻红）；
    2. 主图闭环（真实 ``create_react_subgraph`` + 可 msgpack 序列化的脚本 LLM +
       InMemorySaver 父图）：invoke 后 ``__interrupt__`` 可见 + payload 四键契约
       完整 → ``Command(resume=...)`` 恢复后值以 ToolMessage 回到子图、跑到
       finalize 收尾；
    3. 次生问题消除：全路径日志与 ToolMessage 无 question 全文、无
       "raised GraphInterrupt" 吞没转写特征；
    4. sp1 容错语义零回归：普通工具抛 ValueError 仍转 error ToolMessage、子图
       继续跑完不被杀死。

测试策略：全离线（InMemorySaver）、不 mock interrupt（真实 langgraph interrupt
语义）。FakeLLM 为 BaseChatModel（pydantic v2）子类且脚本走纯数据字段——
langgraph JsonPlusSerializer 以 EXT_PYDANTIC_V2（model_dump →
model_construct）序列化子图 checkpoint 中的 ``context._llm``，round-trip 后
``_generate`` 仅基于输入 messages 路由（无内部计数器），行为不变（验收报告
"探针附带发现"：不可 msgpack 序列化的 FakeLLM 在带 checkpointer 父图中直接
TypeError）。
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional, TypedDict

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

import config
from core import secrets_store
from core.react_base import create_react_subgraph
from core.tools.interaction_tools import (
    INTERRUPT_KIND_USER_INPUT,
    request_user_input,
)

# 唯一标记的问题文本：日志 / ToolMessage 泄漏审计以此为探针（次生问题断言）。
_QUESTION = "QMARKER-B1FIX 请提供 HF token 用于下载数据集"
_RESUME_VALUE = "VAL-b1fix-42"


# ---------------------------------------------------------------------------
# fixtures（范式沿用 tests/test_sprint4_b1.py）
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clean_process_sensitive_set():
    """每条用例前后清空进程内 sensitive set（模块级全局，防跨用例污染）。"""
    secrets_store._SENSITIVE_VALUES.clear()
    yield
    secrets_store._SENSITIVE_VALUES.clear()


@pytest.fixture()
def secrets_workspace(tmp_path, monkeypatch):
    """config.WORKSPACE_DIR patch 到 tmp_path 受控目录，完全隔离 `.secrets` 落点。"""
    ws = tmp_path / "workspace"
    ws.mkdir()
    monkeypatch.setattr(config, "WORKSPACE_DIR", ws)
    return ws


# ---------------------------------------------------------------------------
# 可 msgpack 序列化的脚本 LLM
# ---------------------------------------------------------------------------

class ScriptedToolCallLLM(BaseChatModel):
    """两段式脚本 LLM：无 ToolMessage → 发起一次 tool_call；有 → 输出 <result>。

    可序列化性（B1 验收报告"探针附带发现"）：
    - pydantic v2 纯数据字段（str / dict），JsonPlusSerializer 走
      EXT_PYDANTIC_V2（``model_dump()`` → ``model_construct(**kwargs)``）；
    - 路由完全基于输入 messages（无内部调用计数器等私有状态），checkpoint
      round-trip 反序列化后行为逐字节一致。
    """

    tool_name: str
    tool_args: Dict[str, Any]

    @property
    def _llm_type(self) -> str:
        return "scripted-tool-call-fake"

    def bind_tools(self, tools: Any, **kwargs: Any) -> "ScriptedToolCallLLM":
        # 脚本已内置 tool_calls，绑定为幂等 no-op。
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        last_tool: Optional[ToolMessage] = None
        for m in reversed(messages):
            if isinstance(m, ToolMessage):
                last_tool = m
                break
        if last_tool is None:
            ai = AIMessage(
                content="",
                tool_calls=[{
                    "name": self.tool_name,
                    "args": dict(self.tool_args),
                    "id": "call_b1fix_1",
                    "type": "tool_call",
                }],
            )
        else:
            payload = json.dumps(
                {"tool_output": str(last_tool.content)},
                ensure_ascii=False, sort_keys=True,
            )
            ai = AIMessage(
                content=(
                    f"{config.REACT_RESULT_TAG_OPEN}{payload}"
                    f"{config.REACT_RESULT_TAG_CLOSE}"
                ),
            )
        return ChatResult(generations=[ChatGeneration(message=ai)])


def _initial_state(llm: BaseChatModel) -> dict:
    """构造 ReActState 初始值（与 _make_react_wrapper 注入形态一致）。"""
    return {
        "messages": [
            SystemMessage(content="你是测试用 ReAct agent。"),
            HumanMessage(content="开始"),
        ],
        "round": 0,
        "max_rounds": 6,
        "status": "reasoning",
        "result": None,
        "context": {"_llm": llm},
    }


class _ParentState(TypedDict):
    result: Optional[Dict[str, Any]]
    tool_contents: List[str]


def _build_parent_app(llm: BaseChatModel, tools: list, checkpointer):
    """父图：单节点内 imperative 调 ReAct 子图（与生产 _make_react_wrapper 同拓扑，
    隐式 config 传播让子图纳入父 checkpointer 命名空间）。"""

    def agent_node(state: _ParentState) -> dict:
        subgraph = create_react_subgraph(
            node_name="b1fix_agent",
            system_prompt="test",
            tools=tools,
            max_rounds=6,
        )
        final = subgraph.invoke(_initial_state(llm))
        contents = [
            str(m.content)
            for m in final.get("messages", [])
            if isinstance(m, ToolMessage)
        ]
        return {"result": final.get("result"), "tool_contents": contents}

    builder = StateGraph(_ParentState)
    builder.add_node("agent", agent_node)
    builder.add_edge(START, "agent")
    builder.add_edge("agent", END)
    return builder.compile(checkpointer=checkpointer)


def _interrupt_llm() -> ScriptedToolCallLLM:
    return ScriptedToolCallLLM(
        tool_name="request_user_input",
        tool_args={
            "question": _QUESTION,
            "is_sensitive": False,
            "purpose_key": "",
        },
    )


def _run_pause_then_resume(secrets_workspace, thread_id: str):
    """跑一次完整 pause → resume 闭环，返回 (paused, final, app, cfg)。"""
    app = _build_parent_app(
        _interrupt_llm(), [request_user_input], InMemorySaver(),
    )
    cfg = {"configurable": {"thread_id": thread_id}}
    paused = app.invoke({"result": None, "tool_contents": []}, cfg)
    final = app.invoke(
        Command(resume={"value": _RESUME_VALUE, "remember": False}), cfg,
    )
    return paused, final, app, cfg


# ===========================================================================
# 1. 最小修复锚点：GraphInterrupt 从 standalone 子图冒泡（修复回退即翻红）
# ===========================================================================

def test_fix_graphinterrupt_bubbles_out_of_standalone_subgraph(secrets_workspace):
    """无 checkpointer 的 standalone 子图：interrupt() 抛出的 GraphInterrupt
    必须穿透 tool_executor_node 的容错层冒泡（langgraph 1.1.10 顶层图形态下
    由 Pregel loop 接住并转为返回值中的 ``__interrupt__``，子图停在
    ``status='tool_call'``、零 ToolMessage）——修复前被转成 error ToolMessage、
    子图静默跑完 ``status='done'``（B1 验收探针 Part B 实证形态之一）。"""
    subgraph = create_react_subgraph(
        node_name="b1fix_standalone",
        system_prompt="test",
        tools=[request_user_input],
        max_rounds=6,
    )
    out = subgraph.invoke(_initial_state(_interrupt_llm()))

    intr = out.get("__interrupt__")
    assert intr, "GraphInterrupt 必须冒泡出 tool_executor（__interrupt__ 可见）"
    assert intr[0].value["question"] == _QUESTION
    assert out["status"] == "tool_call", (
        "子图必须停在工具执行处（修复前会静默跑完 status='done'）"
    )
    assert out["result"] is None
    tool_msgs = [m for m in out["messages"] if isinstance(m, ToolMessage)]
    assert tool_msgs == [], (
        "interrupt 不得被转写为 error ToolMessage（吞没特征）"
    )


# ===========================================================================
# 2. 主图闭环：__interrupt__ 暂停态 + payload 契约 + resume 后跑到收尾
# ===========================================================================

def test_fix_react_interrupt_pauses_parent_graph_with_payload_contract(
    secrets_workspace,
):
    """带 InMemorySaver 父图 + 真实 ReAct 子图：invoke 后图处于 interrupt
    暂停态（__interrupt__ 可见、payload 四键契约完整、agent 节点未完成）。"""
    app = _build_parent_app(
        _interrupt_llm(), [request_user_input], InMemorySaver(),
    )
    cfg = {"configurable": {"thread_id": "b1fix-pause"}}
    paused = app.invoke({"result": None, "tool_contents": []}, cfg)

    intr = paused.get("__interrupt__")
    assert intr, "修复后 interrupt 必须暂停主图（__interrupt__ 存在）"
    payload = intr[0].value
    assert set(payload.keys()) == {
        "interrupt_kind", "question", "is_sensitive", "purpose_key",
    }, "payload 必须恰为四键（§7.1 契约，经真实 __interrupt__ 通道穿透）"
    assert payload["interrupt_kind"] == INTERRUPT_KIND_USER_INPUT
    assert payload["question"] == _QUESTION
    assert payload["is_sensitive"] is False
    assert payload["purpose_key"] is None, "空串 purpose_key 必须规整为 None"

    snap = app.get_state(cfg)
    assert snap.next == ("agent",), "暂停态下 agent 节点应处于待完成（可恢复）"


def test_fix_resume_value_returns_as_toolmessage_and_runs_to_finalize(
    secrets_workspace,
):
    """Command(resume=...) 恢复后：resume 值以 ToolMessage 回到子图（恰一条、
    内容为裸值），子图继续跑到 finalize 收尾（result 含该值），主图不再暂停；
    remember=False 不落 `.secrets`。"""
    _, final, _, _ = _run_pause_then_resume(secrets_workspace, "b1fix-resume")

    assert "__interrupt__" not in final, "恢复后不得再处于暂停态"
    assert final["tool_contents"] == [_RESUME_VALUE], (
        "resume 值必须以（唯一一条）ToolMessage 裸值形态回到子图"
    )
    assert final["result"] == {"tool_output": _RESUME_VALUE}, (
        "子图必须跑到 finalize 收尾且 result 携带 resume 值"
    )
    assert not (secrets_workspace / config.SECRETS_FILE_NAME).exists(), (
        "remember=False 不得落盘 .secrets"
    )


# ===========================================================================
# 3. 次生问题消除：日志 / ToolMessage 无 question 全文、无吞没转写特征
# ===========================================================================

def test_fix_no_question_fulltext_in_logs_or_toolmessages(
    secrets_workspace, caplog,
):
    """修复前：L599 吞没转写把含 question 全文的 payload 写进 ToolMessage 与
    react_base WARNING 日志。修复后全路径（pause + resume）：
    - core.* 日志（DEBUG 全捕）与所有 WARNING+ 记录零 question 全文；
    - ToolMessage 无 question 全文、无 "raised GraphInterrupt" 转写特征。"""
    caplog.set_level(logging.DEBUG, logger="core.react_base")
    caplog.set_level(logging.DEBUG, logger="core.tools.interaction_tools")

    with caplog.at_level(logging.DEBUG, logger="core"):
        _, final, _, _ = _run_pause_then_resume(secrets_workspace, "b1fix-logs")

    own_records = [r for r in caplog.records if r.name.startswith("core")]
    for rec in own_records:
        assert _QUESTION not in rec.getMessage(), (
            f"core.* 日志泄漏 question 全文: [{rec.name}] {rec.getMessage()[:120]}"
        )
        assert "raised GraphInterrupt" not in rec.getMessage(), (
            "日志出现吞没转写特征（GraphInterrupt 被 except Exception 捕获）"
        )
    warn_records = [r for r in caplog.records if r.levelno >= logging.WARNING]
    for rec in warn_records:
        assert _QUESTION not in rec.getMessage(), (
            f"WARNING+ 日志泄漏 question 全文: [{rec.name}]"
        )

    for content in final["tool_contents"]:
        assert _QUESTION not in content, "ToolMessage 泄漏 question 全文"
        assert "raised GraphInterrupt" not in content, (
            "ToolMessage 出现吞没转写特征（interrupt 未上浮）"
        )


# ===========================================================================
# 4. sp1 容错语义零回归：普通工具异常仍转 error ToolMessage
# ===========================================================================

@tool
def _flaky_probe(x: str) -> str:
    """测试用：总是抛 ValueError 的普通工具。"""
    raise ValueError("boom-detail")


def test_sp1_semantics_plain_tool_error_still_becomes_error_toolmessage(
    secrets_workspace,
):
    """普通工具抛 ValueError：仍走 sp1 容错路径转 error ToolMessage（格式
    逐字节不变），子图不被杀死、继续跑到 done——GraphBubbleUp 直通放行
    不得影响既有容错语义。"""
    subgraph = create_react_subgraph(
        node_name="b1fix_sp1",
        system_prompt="test",
        tools=[_flaky_probe],
        max_rounds=6,
    )
    llm = ScriptedToolCallLLM(tool_name="_flaky_probe", tool_args={"x": "1"})

    final = subgraph.invoke(_initial_state(llm))  # 不抛异常 = 子图未被杀死

    tool_msgs = [m for m in final["messages"] if isinstance(m, ToolMessage)]
    assert len(tool_msgs) == 1
    assert tool_msgs[0].content == (
        "tool _flaky_probe raised ValueError: boom-detail"
    ), "error ToolMessage 转写格式必须与 sp1 逐字节一致"
    assert final["status"] == "done"
    assert final["result"] == {
        "tool_output": "tool _flaky_probe raised ValueError: boom-detail",
    }, "子图必须带着 error ToolMessage 正常收尾（容错而非崩溃）"
