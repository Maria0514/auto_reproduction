"""Sprint 4 任务 B2：interrupt#3 重跑幂等 harness 首验（Q-C1，软前置门，R-S4-02）。

覆盖 dev-plan §4 任务 B2 CP-B2-1 ~ CP-B2-4（architecture §8 机理差异表 +
三层幂等保证 + AC-S4-14 断言设计）。本文件是架构 §8.2 留白机理事实的
**第一手实证**，结论决定 C2/E2 是否可以挂载 request_user_input。

实证结论速览（2026-07-04 首验，langgraph 1.1.10 + InMemorySaver）：
    1. 断言点 1（独立轮次，门禁 CP-B2-2）：前序独立轮次的副作用工具在
       resume 后**副作用恰为 1**——子图经隐式 config 传播纳入父 checkpointer
       命名空间，resume 时从子图自身 checkpoint 精确恢复到 interrupt 所在的
       tool_executor 节点，已完成的 reasoning / tool_executor 节点**均不重放**
       （LLM 调用数 pause=2 → final=3，只补收尾一跳）。→ 架构 §8.3 缓解 1
       成立，门禁 PASS。
    2. 断言点 2（同轮混调）：同一轮 tool_calls 同时含副作用工具 +
       request_user_input 时，**同批 tool_calls 整体重放**（副作用=2）——
       首次执行中 interrupt 中止整个 tool_executor 节点，该批次零 ToolMessage
       落 checkpoint；resume 重跑整批，副作用工具第二次执行的结果才被采纳
       （ToolMessage 中 call_no=2）。→ 架构 §8.3 缓解 2（docstring 单独一轮
       纪律）+ 缓解 3（工具自身幂等）的必要性实证成立。
    3. 断言点 3（messages 完整性）：resume 后子图 messages 历史完整——前序
       独立轮次的 ToolMessage 不丢、顺序保留，resume 值以 ToolMessage 回到
       agent 并进入 finalize result。
    4. R-S4-10（collector 可见性，供 E1/E2）：**节点体内新建的闭包收集器在
       resume 后丢失前序收集值**——resume 重跑节点函数体（node_runs=2）会
       重建 collector，而前序轮次工具不重放、无法重新填充；跨 interrupt 的
       完整序列必须从子图 messages（checkpoint 恢复，完整）回读，或把
       collector 放在节点体外的持久作用域。

测试策略：全离线（InMemorySaver + 脚本 LLM），零 API 配额。FakeLLM 范式沿用
tests/test_sprint4_b1_fix.py：BaseChatModel 纯数据字段（msgpack round-trip
安全），路由完全基于输入 messages 的 ToolMessage 计数（无内部计数器，replay
安全）；LLM 调用观测走**模块级 dict**（进程全局，跨 checkpoint 序列化存活）。
副作用双通道观测：进程内 dict 计数 + tmp_path 磁盘 append（防闭包对象重建
造成观测盲区）。
"""

from __future__ import annotations

import json
from pathlib import Path
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

_RESUME_VALUE = "USERVAL-b2"

# 进程级 LLM 调用观测通道：跨 checkpoint round-trip 存活（脚本 LLM 实例会被
# JsonPlusSerializer 序列化进子图 checkpoint，实例字段计数不可靠；模块级 dict
# 以 tag 区分用例，反序列化后的新实例仍指向同一 dict）。
_LLM_CALLS: Dict[str, int] = {}


# ---------------------------------------------------------------------------
# fixtures（范式沿用 tests/test_sprint4_b1.py / b1_fix）
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clean_process_sensitive_set():
    secrets_store._SENSITIVE_VALUES.clear()
    yield
    secrets_store._SENSITIVE_VALUES.clear()


@pytest.fixture(autouse=True)
def secrets_workspace(tmp_path, monkeypatch):
    """`.secrets` 落点隔离到 tmp_path（request_user_input 走 config.WORKSPACE_DIR）。"""
    ws = tmp_path / "workspace"
    ws.mkdir()
    monkeypatch.setattr(config, "WORKSPACE_DIR", ws)
    return ws


# ---------------------------------------------------------------------------
# 可 msgpack 序列化的脚本 LLM：路由完全基于输入 messages（replay 安全）
# ---------------------------------------------------------------------------

class ScriptedLLM(BaseChatModel):
    """三段式脚本（mode="independent"）：
        0 条 ToolMessage → tool_calls=[side_probe]（第 1 轮，副作用）
        1 条 ToolMessage → tool_calls=[request_user_input]（第 2 轮，interrupt#3）
        ≥2 条 ToolMessage → <result>{"user_value": 最后一条 ToolMessage}</result>

    两段式脚本（mode="mixed"）：
        0 条 ToolMessage → tool_calls=[side_probe, request_user_input]（同轮混调）
        ≥1 条 ToolMessage → <result> 收尾
    """

    mode: str
    tag: str

    @property
    def _llm_type(self) -> str:
        return "b2-scripted"

    def bind_tools(self, tools: Any, **kwargs: Any) -> "ScriptedLLM":
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        _LLM_CALLS[self.tag] = _LLM_CALLS.get(self.tag, 0) + 1
        n_tool = sum(1 for m in messages if isinstance(m, ToolMessage))

        def _side_call() -> dict:
            return {"name": "side_probe", "args": {"content": "v1"},
                    "id": "call_side_1", "type": "tool_call"}

        def _rui_call() -> dict:
            return {"name": "request_user_input",
                    "args": {"question": "Q-B2 需要一个参数",
                             "is_sensitive": False, "purpose_key": ""},
                    "id": "call_rui_1", "type": "tool_call"}

        def _finish() -> AIMessage:
            last = [m for m in messages if isinstance(m, ToolMessage)][-1]
            payload = json.dumps({"user_value": str(last.content)},
                                 ensure_ascii=False, sort_keys=True)
            return AIMessage(content=(
                f"{config.REACT_RESULT_TAG_OPEN}{payload}"
                f"{config.REACT_RESULT_TAG_CLOSE}"))

        if self.mode == "independent":
            if n_tool == 0:
                ai = AIMessage(content="", tool_calls=[_side_call()])
            elif n_tool == 1:
                ai = AIMessage(content="", tool_calls=[_rui_call()])
            else:
                ai = _finish()
        else:  # mixed
            if n_tool == 0:
                ai = AIMessage(content="", tool_calls=[_side_call(), _rui_call()])
            else:
                ai = _finish()
        return ChatResult(generations=[ChatGeneration(message=ai)])


# ---------------------------------------------------------------------------
# harness：父图（InMemorySaver）单节点 imperative 调真实 create_react_subgraph
# （与生产 _make_react_wrapper 同拓扑：隐式 config 传播纳入父 checkpointer）
# ---------------------------------------------------------------------------

class _ParentState(TypedDict):
    result: Optional[Dict[str, Any]]
    tool_contents: List[str]
    collector_snapshot: List[str]


def _make_side_probe(counter: Dict[str, int], disk_file: Path,
                     collector_ref: Optional[Dict[str, Any]] = None):
    """带计数副作用的 fake 工具（模拟 write_code_file）：
    进程内计数 +1、磁盘 append 一行（双通道观测）、可选喂闭包收集器。"""

    @tool
    def side_probe(content: str) -> str:
        """写一个标记文件（副作用探针，模拟 write_code_file）。"""
        counter["n"] += 1
        with open(disk_file, "a", encoding="utf-8") as f:
            f.write(content + "\n")
        if collector_ref is not None and collector_ref.get("c") is not None:
            collector_ref["c"].append(content)
        return json.dumps({"call_no": counter["n"], "written": content},
                          ensure_ascii=False, sort_keys=True)

    return side_probe


def _build_app(llm: BaseChatModel, tools: list,
               collector_ref: Optional[Dict[str, Any]] = None):
    """父图：单节点内调 ReAct 子图。collector_ref 非 None 时，节点体每次执行
    都**新建**一个 collector list 并写入 ref（R-S4-10 的"节点体内闭包收集器"
    形态），节点返回时快照进父 state。"""

    def agent_node(state: _ParentState) -> dict:
        collector: List[str] = []
        if collector_ref is not None:
            collector_ref["c"] = collector
        subgraph = create_react_subgraph(
            node_name="b2_agent", system_prompt="test",
            tools=tools, max_rounds=8)
        final = subgraph.invoke({
            "messages": [SystemMessage(content="你是测试用 ReAct agent。"),
                         HumanMessage(content="开始")],
            "round": 0, "max_rounds": 8, "status": "reasoning",
            "result": None, "context": {"_llm": llm},
        })
        contents = [str(m.content) for m in final.get("messages", [])
                    if isinstance(m, ToolMessage)]
        return {"result": final.get("result"), "tool_contents": contents,
                "collector_snapshot": list(collector)}

    builder = StateGraph(_ParentState)
    builder.add_node("agent", agent_node)
    builder.add_edge(START, "agent")
    builder.add_edge("agent", END)
    return builder.compile(checkpointer=InMemorySaver())


def _run_scenario(mode: str, tag: str, tmp_path: Path,
                  with_collector: bool = False) -> Dict[str, Any]:
    """跑一次完整 pause → resume 闭环，返回全部观测量。"""
    _LLM_CALLS.pop(tag, None)
    counter = {"n": 0}
    collector_ref: Optional[Dict[str, Any]] = {"c": None} if with_collector else None
    disk_file = tmp_path / f"side_{tag}.log"
    side = _make_side_probe(counter, disk_file, collector_ref)
    llm = ScriptedLLM(mode=mode, tag=tag)
    app = _build_app(llm, [side, request_user_input], collector_ref)

    cfg = {"configurable": {"thread_id": f"b2-{tag}"}}
    paused = app.invoke(
        {"result": None, "tool_contents": [], "collector_snapshot": []}, cfg)
    obs: Dict[str, Any] = {
        "interrupt": paused.get("__interrupt__"),
        "side_count_at_pause": counter["n"],
        "llm_calls_at_pause": _LLM_CALLS.get(tag, 0),
        "collector_at_pause": (list(collector_ref["c"])
                               if with_collector and collector_ref["c"] is not None
                               else None),
    }
    final = app.invoke(
        Command(resume={"value": _RESUME_VALUE, "remember": False}), cfg)
    obs.update({
        "final": final,
        "side_count_final": counter["n"],
        "disk_lines": (disk_file.read_text(encoding="utf-8").splitlines()
                       if disk_file.exists() else []),
        "llm_calls_final": _LLM_CALLS.get(tag, 0),
    })
    return obs


# ===========================================================================
# CP-B2-1 harness 跑通：interrupt#3 → resume → 值回到 agent → 收尾
# （AC-S4-06 mock 层首证）
# ===========================================================================

def test_cp_b2_1_harness_pause_resume_value_reaches_finalize(tmp_path):
    obs = _run_scenario("independent", "cp1", tmp_path)

    intr = obs["interrupt"]
    assert intr, "interrupt#3 必须暂停主图（__interrupt__ 可见）"
    assert intr[0].value["interrupt_kind"] == INTERRUPT_KIND_USER_INPUT
    final = obs["final"]
    assert "__interrupt__" not in final, "resume 后不得再暂停"
    assert final["result"] == {"user_value": _RESUME_VALUE}, \
        "resume 值必须回到 agent 并进入 finalize result"


# ===========================================================================
# CP-B2-2 断言点 1（门禁）：前序独立轮次副作用工具在 resume 后副作用恰为 1
# （AC-S4-14 核心断言；若 >1 → 架构 §8.3 缓解 1 失效 → 架构师咨询 + C2/E2 暂停）
# ===========================================================================

def test_cp_b2_2_gate_prior_round_side_effect_exactly_once(tmp_path):
    obs = _run_scenario("independent", "cp2", tmp_path)

    assert obs["side_count_at_pause"] == 1, "暂停时第 1 轮副作用应已执行恰一次"
    # —— 门禁断言（进程内计数 + 磁盘双通道）——
    assert obs["side_count_final"] == 1, (
        "门禁 FAIL：前序独立轮次副作用工具在 resume 后被重放（副作用 > 1），"
        "架构 §8.3 缓解 1（LangGraph 节点级 resume 定位）失效，"
        "触发架构师咨询，C2/E2 暂停挂载 request_user_input"
    )
    assert obs["disk_lines"] == ["v1"], "磁盘通道：resume 后仍只有 1 行写入"


def test_cp_b2_2_mechanism_reasoning_nodes_not_replayed(tmp_path):
    """机理锁定：resume 从子图自身 checkpoint 恢复——已完成的 reasoning 节点
    不重放。pause 时 LLM 恰调 2 次（round1 + round2 reasoning）；resume 后
    仅补收尾 1 次（总 3 次）。若未来 langgraph 升级改变子图 checkpoint 粒度
    （如整子图重放），本用例翻红即门禁预警。"""
    obs = _run_scenario("independent", "cp2m", tmp_path)
    assert obs["llm_calls_at_pause"] == 2
    assert obs["llm_calls_final"] == 3, (
        "resume 后 LLM 总调用数应为 3（只补收尾 reasoning）；若 >3 说明"
        "reasoning 节点被重放，子图 checkpoint 恢复粒度已变化"
    )


# ===========================================================================
# 断言点 2：同轮混调（同一轮 tool_calls = [副作用工具, request_user_input]）
# → 同批 tool_calls 整体重放（架构 §8.3 缓解 1 风险点的实证锚定）
# ===========================================================================

def test_b2_same_round_mixed_batch_is_replayed_side_effect_twice(tmp_path):
    """实证锚定（characterization，非期望行为背书）：同轮混调时 interrupt 中止
    整个 tool_executor 节点（该批零 ToolMessage 落 checkpoint），resume 重跑
    **整批** tool_calls → 副作用工具执行 2 次。这正是 request_user_input
    docstring "单独一轮调用" 纪律（缓解 2）+ 工具自身幂等（缓解 3）存在的理由。"""
    obs = _run_scenario("mixed", "mix", tmp_path)

    assert obs["interrupt"], "混调场景 interrupt 仍须暂停主图"
    assert obs["side_count_at_pause"] == 1, \
        "首次执行：side_probe 先于 interrupt 执行了一次"
    assert obs["side_count_final"] == 2, (
        "同批 tool_calls 重放实证：resume 后副作用 = 2（若 = 1 说明 langgraph "
        "已实现 batch 内断点续跑，架构 §8.3 缓解 2 可放宽，需更新报告结论）"
    )
    assert obs["disk_lines"] == ["v1", "v1"], "磁盘通道同步观测到 2 次写入"


def test_b2_same_round_mixed_idempotent_overwrite_end_state_safe(tmp_path):
    """幂等工具（覆盖写语义）在同批重放下的末态安全：最终 messages 中副作用
    工具的 ToolMessage 恰 1 条且内容为第 2 次执行结果（call_no=2，首批结果
    整体丢弃不 commit），resume 值 ToolMessage 恰 1 条——无重复消息、无脏
    半批状态；对覆盖写工具末态与单次执行等价。"""
    obs = _run_scenario("mixed", "mix2", tmp_path)
    final = obs["final"]
    assert final["result"] == {"user_value": _RESUME_VALUE}

    contents = final["tool_contents"]
    assert len(contents) == 2, f"应恰 2 条 ToolMessage（side + resume 值）: {contents}"
    side_msg = json.loads(contents[0])
    assert side_msg == {"call_no": 2, "written": "v1"}, (
        "首批执行结果不落 checkpoint，采纳的是 resume 重放（第 2 次）的结果"
    )
    assert contents[1] == _RESUME_VALUE


# ===========================================================================
# 断言点 3：messages 完整性——前序 ToolMessage 不丢、顺序保留、值到收尾
# ===========================================================================

def test_b2_messages_integrity_prior_toolmessage_preserved_after_resume(tmp_path):
    obs = _run_scenario("independent", "msgs", tmp_path)
    final = obs["final"]

    contents = final["tool_contents"]
    assert len(contents) == 2, f"应恰 2 条 ToolMessage: {contents}"
    # 第 1 条：前序轮次 side_probe 的 ToolMessage（call_no=1，未被重放/覆盖）
    assert json.loads(contents[0]) == {"call_no": 1, "written": "v1"}, \
        "前序独立轮次的 ToolMessage 必须原样保留（checkpoint 恢复，不重算）"
    # 第 2 条：resume 值以裸串 ToolMessage 回到 agent
    assert contents[1] == _RESUME_VALUE
    assert final["result"] == {"user_value": _RESUME_VALUE}, \
        "agent 必须带着 resume 值继续到收尾"


# ===========================================================================
# R-S4-10（供 E1/E2）：节点体内新建的闭包收集器在 resume 后丢失前序收集值
# ===========================================================================

def test_b2_r_s4_10_node_local_collector_loses_pre_interrupt_values(tmp_path):
    """实证锚定：resume 重跑节点函数体 → 节点体内新建的 collector 被重建为空；
    前序轮次工具不重放 → 新 collector 收不到 pre-interrupt 的输出。
    ⇒ E1/E2 若用闭包收集器，跨 interrupt 的完整序列必须从子图 messages 回读
    （messages 经 checkpoint 恢复是完整的，见 messages_integrity 用例），或把
    collector 放在节点体外持久作用域。"""
    obs = _run_scenario("independent", "coll", tmp_path, with_collector=True)

    assert obs["collector_at_pause"] == ["v1"], \
        "首次执行：collector 收到 pre-interrupt 输出"
    final = obs["final"]
    assert final["collector_snapshot"] == [], (
        "resume 后节点重跑重建 collector，且前序工具不重放 → 闭包收集器"
        "丢失 pre-interrupt 值（R-S4-10 实证；若翻红说明 langgraph 行为已变）"
    )
    # 对照：同一次 resume 执行中 messages 通道是完整的（正确回读姿势）
    assert json.loads(final["tool_contents"][0]) == {"call_no": 1, "written": "v1"}


# ===========================================================================
# CP-B2-3 断言点 4：核心场景连跑 3 次结论一致（机理类稳定性）
# ===========================================================================

def test_cp_b2_3_core_scenario_stable_across_3_runs(tmp_path):
    for i in range(3):
        obs = _run_scenario("independent", f"stab{i}", tmp_path)
        assert obs["interrupt"], f"run#{i}: interrupt 必须出现"
        assert obs["side_count_final"] == 1, f"run#{i}: 门禁副作用恰为 1"
        assert obs["llm_calls_final"] == 3, f"run#{i}: reasoning 不重放"
        assert obs["final"]["result"] == {"user_value": _RESUME_VALUE}, \
            f"run#{i}: 值到收尾"


def test_cp_b2_3_mixed_scenario_stable_across_3_runs(tmp_path):
    for i in range(3):
        obs = _run_scenario("mixed", f"mstab{i}", tmp_path)
        assert obs["side_count_final"] == 2, f"run#{i}: 同批重放结论一致"
        assert obs["final"]["result"] == {"user_value": _RESUME_VALUE}
