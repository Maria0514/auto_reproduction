"""Sprint 4 任务 E2（S4-03）：`_run_execution_agent` 内嵌子图装配单测。

覆盖 dev-plan §4 任务 E2 自测检查点：
    - CP-E2-1 装配清单逐项断言：_llm 注入（缺 llm_config_set 时报错路径）、
      SystemMessage 主体字节级一致（两个不同任务 state 去尾部后 == 常量，沿用
      CP-F3-1 断言范式）、HumanMessage sort_keys 幂等；
    - CP-E2-2 mock LLM 剧本（prepare → run×2 → 收尾）：收集器取回真实结果、
      rounds_used 与剧本轮数一致；REACT_MAX_ROUNDS_EXECUTION=10 到顶 force_finish
      不越界（budget_check_node 消费断言）；
    - CP-E2-3 agent 谎报拦截（E2 层）：LLM <result> 自称 success 但真实 exit_code
      非 0 → ExecAgentOutput 携带真实非 0 结果（端到端判定在 E3 装配后补跑）；
    - CP-E2-4 子图异常降级：WARNING + 空结果返回、节点不炸；GraphBubbleUp
      （interrupt#3 控制流）必须直通上浮不被吞；
    - CP-E2-5 execution 侧 request_user_input 挂载：run（认证失败）→
      request_user_input → interrupt#3 → resume → 再 run（成功）走通，且
      R-S4-10 合并路径（messages 回读补全 pre-interrupt 段 + 收集器尾段）生效。

全离线（InMemorySaver + 脚本 LLM + mock sandbox），零 API 配额。
脚本 LLM 沿用 B2 范式：BaseChatModel 纯数据字段（msgpack round-trip 安全），
路由完全基于输入 messages 的 ToolMessage 计数（replay 安全）。
"""

from __future__ import annotations

import importlib
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, TypedDict

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.errors import GraphBubbleUp
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

import config
from core import secrets_store
from core.tools.interaction_tools import INTERRUPT_KIND_USER_INPUT

execution_module = importlib.import_module("core.nodes.execution")

from config import REACT_MAX_ROUNDS_EXECUTION  # noqa: E402
from sandbox.local_venv import SandboxPrepareResult, SandboxRunResult  # noqa: E402


# ---------------------------------------------------------------------------
# fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_process_sensitive_set():
    secrets_store._SENSITIVE_VALUES.clear()
    yield
    secrets_store._SENSITIVE_VALUES.clear()


@pytest.fixture(autouse=True)
def secrets_workspace(tmp_path, monkeypatch):
    """`.secrets` / mask 落点隔离到 tmp_path（secrets_store 动态读 config.WORKSPACE_DIR）。"""
    ws = tmp_path / "workspace"
    ws.mkdir()
    monkeypatch.setattr(config, "WORKSPACE_DIR", ws)
    return ws


def _base_state(**overrides: Any) -> Dict[str, Any]:
    state: Dict[str, Any] = {
        "llm_config_set": {"default": {"model": "test"}},
        "fix_loop_count": 0,
    }
    state.update(overrides)
    return state


def _plan(steps: Optional[List[Any]] = None) -> Dict[str, Any]:
    return {
        "execution_steps": steps if steps is not None else [
            {"command": "python train.py"}, {"command": "python eval.py"},
        ],
        "environment": {"dependencies": ["numpy"]},
    }


def _patch_llm_plumbing(monkeypatch, llm: Any):
    """绕开真实 llm_client：resolve 原样透传、create 返回脚本 LLM。"""
    monkeypatch.setattr(execution_module, "resolve_llm_config", lambda cfg, node: cfg)
    monkeypatch.setattr(execution_module, "create_llm", lambda cfg: llm)


def _make_workdir_with_venv(tmp_path: Path) -> str:
    """带已建好 .venv 痕迹的 work_dir（python_exe 确定性推导路径）。"""
    wd = tmp_path / "wd"
    (wd / ".venv" / "bin").mkdir(parents=True)
    (wd / ".venv" / "pyvenv.cfg").write_text("home = /usr\n")
    return str(wd)


def _make_prep(python_exe: str) -> SandboxPrepareResult:
    return SandboxPrepareResult(
        success=True, venv_dir=str(Path(python_exe).parent.parent),
        python_exe=python_exe, pip_exe="", env_info={"python_version": "Python 3.11"},
        install_log="ok", install_failed_packages=[], error=None,
    )


class SeqRunner:
    """按调用序返回预设 SandboxRunResult 的 run_in_venv mock（跨节点重跑持久）。"""

    def __init__(self, specs: List[Dict[str, Any]]) -> None:
        self.specs = specs
        self.calls: List[List[str]] = []

    def __call__(self, python_exe, command, work_dir, *a, **k):
        self.calls.append(list(command))
        i = min(len(self.calls) - 1, len(self.specs) - 1)
        spec = self.specs[i]
        return SandboxRunResult(
            exit_code=spec.get("exit_code", 0),
            stdout=spec.get("stdout", ""),
            stderr=spec.get("stderr", ""),
            duration_seconds=0.1,
            timed_out=False,
            output_truncated=False,
            command=list(command),
        )


class ScriptedExecLLM(BaseChatModel):
    """脚本 LLM（B2 范式：纯数据字段、路由基于 ToolMessage 计数，replay 安全）。

    mode:
        "full":     0→prepare_environment, 1→run(train), 2→run(eval), ≥3→<result>
        "liar":     0→run(broken), ≥1→<result> 自称 all_exit_zero=true（谎报）
        "loop":     永远 run（force_finish 到顶断言用）
        "credential": 0→run(fetch), 1→request_user_input, 2→run(fetch), ≥3→<result>
    """

    mode: str

    @property
    def _llm_type(self) -> str:
        return "e2-scripted"

    def bind_tools(self, tools: Any, **kwargs: Any) -> "ScriptedExecLLM":
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        n_tool = sum(1 for m in messages if isinstance(m, ToolMessage))

        def _call(name: str, args: Dict[str, Any], cid: str) -> AIMessage:
            return AIMessage(content="", tool_calls=[
                {"name": name, "args": args, "id": cid, "type": "tool_call"},
            ])

        def _finish(payload: Dict[str, Any]) -> AIMessage:
            body = json.dumps(payload, ensure_ascii=False, sort_keys=True)
            return AIMessage(content=(
                f"{config.REACT_RESULT_TAG_OPEN}{body}{config.REACT_RESULT_TAG_CLOSE}"
            ))

        if self.mode == "full":
            if n_tool == 0:
                ai = _call("prepare_environment", {}, "c_prep")
            elif n_tool == 1:
                ai = _call("run_in_sandbox", {"command": "python train.py"}, "c_run1")
            elif n_tool == 2:
                ai = _call("run_in_sandbox", {"command": "python eval.py"}, "c_run2")
            else:
                ai = _finish({"steps_attempted": 2, "all_exit_zero": True,
                              "summary": "两步执行完成", "notes": None})
        elif self.mode == "liar":
            if n_tool == 0:
                ai = _call("run_in_sandbox", {"command": "python broken.py"}, "c_run1")
            else:
                ai = _finish({"steps_attempted": 1, "all_exit_zero": True,
                              "summary": "复现成功（谎报）", "notes": None})
        elif self.mode == "loop":
            ai = _call("run_in_sandbox", {"command": "python spin.py"}, f"c_{n_tool}")
        else:  # credential
            if n_tool == 0:
                ai = _call("run_in_sandbox", {"command": "python fetch.py"}, "c_run1")
            elif n_tool == 1:
                ai = _call("request_user_input", {
                    "question": "克隆私有仓库需要 git token，请提供",
                    "is_sensitive": True, "purpose_key": "",
                }, "c_rui")
            elif n_tool == 2:
                ai = _call("run_in_sandbox", {"command": "python fetch.py"}, "c_run2")
            else:
                ai = _finish({"steps_attempted": 2, "all_exit_zero": False,
                              "summary": "凭证补齐后重试成功", "notes": None})
        return ChatResult(generations=[ChatGeneration(message=ai)])


# ===========================================================================
# CP-E2-1 装配清单逐项断言（patch create_react_subgraph 捕获真实装配产物）
# ===========================================================================


class _CapturingSubgraph:
    def __init__(self, capture: Dict[str, Any], final: Optional[Dict[str, Any]] = None):
        self._capture = capture
        self._final = final

    def invoke(self, initial: Dict[str, Any]) -> Dict[str, Any]:
        self._capture["initial"] = initial
        if self._final is not None:
            return self._final
        return {"round": 3, "messages": list(initial["messages"]),
                "result": {}, "status": "done"}


def _capture_assembly(monkeypatch, state, work_dir, plan, llm=None):
    """跑一次 _run_execution_agent，捕获 create_react_subgraph 入参与 initial state。"""
    capture: Dict[str, Any] = {}
    llm = llm if llm is not None else ScriptedExecLLM(mode="full")
    _patch_llm_plumbing(monkeypatch, llm)

    def fake_factory(node_name, system_prompt, tools, max_rounds, result_schema=None):
        capture.update(node_name=node_name, system_prompt=system_prompt,
                       tools=list(tools), max_rounds=max_rounds)
        return _CapturingSubgraph(capture)

    monkeypatch.setattr(execution_module, "create_react_subgraph", fake_factory)
    out = execution_module._run_execution_agent(state, work_dir, plan)
    capture["output"] = out
    capture["llm"] = llm
    return capture


def test_cp_e2_1_llm_injected_into_subgraph_context(monkeypatch, tmp_path):
    cap = _capture_assembly(monkeypatch, _base_state(), str(tmp_path / "wd"), _plan())
    initial = cap["initial"]
    # 装配项 1：_llm 注入 context（子图 _bind_llm 硬依赖）。
    assert initial["context"]["_llm"] is cap["llm"]
    # ReActState 初始化五要素（wrapper 复刻清单）。
    assert initial["round"] == 0
    assert initial["status"] == "reasoning"
    assert initial["result"] is None
    assert initial["max_rounds"] == REACT_MAX_ROUNDS_EXECUTION
    assert cap["max_rounds"] == REACT_MAX_ROUNDS_EXECUTION
    # rounds 提取口径：fake 子图 round=3 → rounds_used=3=llm_calls。
    assert cap["output"].rounds_used == 3
    assert cap["output"].llm_calls == 3


def test_cp_e2_1_missing_llm_config_set_error_path(caplog):
    """缺 llm_config_set → KeyError → WARNING + 空结果降级（报错路径非静默）。"""
    with caplog.at_level(logging.WARNING):
        out = execution_module._run_execution_agent({}, "/tmp/wd", _plan())
    assert out.prep is None
    assert out.run_results == []
    assert out.rounds_used == 0 and out.llm_calls == 0
    assert any("execution ReAct 子图执行失败" in r.message for r in caplog.records)


def test_cp_e2_1_three_tools_mounted(monkeypatch, tmp_path):
    cap = _capture_assembly(monkeypatch, _base_state(), str(tmp_path / "wd"), _plan())
    names = {t.name for t in cap["tools"]}
    assert names == {"prepare_environment", "run_in_sandbox", "request_user_input"}


def test_cp_e2_1_system_message_byte_identical_across_tasks(monkeypatch, tmp_path):
    """CP-F3-1 断言范式：两个不同任务 state 的 SystemMessage 去尾部段落后 == 常量。

    execution 更强：连尾部段落都是常量 → 整条 SystemMessage 字节级一致。
    """
    cap_a = _capture_assembly(
        monkeypatch, _base_state(), str(tmp_path / "wd_a"),
        _plan([{"command": "python a.py"}]),
    )
    cap_b = _capture_assembly(
        monkeypatch,
        _base_state(fix_loop_count=2, execution_result={
            "success": False, "errors": ["[error_category=runtime] 运行时异常"],
            "logs": "Traceback ...",
        }),
        str(tmp_path / "wd_b"),
        _plan([{"command": "python b.py --seed 42"}]),
    )
    sys_a = cap_a["initial"]["messages"][0]
    sys_b = cap_b["initial"]["messages"][0]
    assert isinstance(sys_a, SystemMessage) and isinstance(sys_b, SystemMessage)
    assert sys_a.content == sys_b.content, "不同任务的 SystemMessage 必须字节级一致"
    # 去尾部段落后 == 主体常量（沿用 CP-F3-1 范式）。
    head = sys_a.content.split("\n--- 当前任务上下文 ---\n")[0]
    assert head == execution_module._EXECUTION_SYSTEM_PROMPT_BODY
    # 主体常量内零动态变量的物证：不含任何 work_dir / 命令片段。
    assert str(tmp_path) not in sys_a.content


def test_cp_e2_1_human_message_sort_keys_idempotent(monkeypatch, tmp_path):
    state = _base_state(
        fix_loop_count=1,
        execution_result={"success": False,
                          "errors": ["[error_category=import] import 错误"],
                          "logs": "ModuleNotFoundError: x"},
    )
    wd = str(tmp_path / "wd")
    plan = _plan()
    cap1 = _capture_assembly(monkeypatch, state, wd, plan)
    cap2 = _capture_assembly(monkeypatch, state, wd, plan)
    h1 = cap1["initial"]["messages"][1]
    h2 = cap2["initial"]["messages"][1]
    assert isinstance(h1, HumanMessage)
    assert h1.content == h2.content, "同一 state 两次装配的 HumanMessage 必须字节级幂等"
    # 内容 = 动态上下文的 sort_keys 稳定序列化。
    expected = json.dumps(
        execution_module._build_execution_agent_context(state, wd, plan),
        ensure_ascii=False, sort_keys=True, default=str,
    )
    assert h1.content == expected
    # 修复回合反馈摘要在 HumanMessage（动态通道）而非 SystemMessage。
    payload = json.loads(h1.content)
    assert payload["fix_round"] == 1
    assert payload["work_dir"] == wd
    assert "import 错误" in json.dumps(payload["last_error_summary"], ensure_ascii=False)


# ===========================================================================
# CP-E2-2 mock LLM 剧本：prepare → run×2 → 收尾（真实 create_react_subgraph）
# ===========================================================================


def test_cp_e2_2_full_script_collector_and_rounds(monkeypatch, tmp_path):
    wd = str(tmp_path / "wd")
    prep = _make_prep(str(Path(wd) / ".venv" / "bin" / "python"))
    runner = SeqRunner([
        {"exit_code": 0, "stdout": "train done"},
        {"exit_code": 0, "stdout": "<METRICS>{\"acc\": 0.9}</METRICS>"},
    ])
    monkeypatch.setattr(execution_module, "prepare_venv", lambda **kw: prep)
    monkeypatch.setattr(execution_module, "run_in_venv", runner)
    _patch_llm_plumbing(monkeypatch, ScriptedExecLLM(mode="full"))

    out = execution_module._run_execution_agent(_base_state(), wd, _plan())

    # 收集器取回的是真实 dataclass 结果（全保真：stdout 未截断原文）。
    assert out.prep is prep, "prep 必须是工具真跑返回的同一 dataclass 实例"
    assert len(out.run_results) == 2
    assert all(isinstance(r, SandboxRunResult) for r in out.run_results)
    assert [r.exit_code for r in out.run_results] == [0, 0]
    assert out.run_results[0].stdout == "train done"
    assert "<METRICS>" in out.run_results[1].stdout
    # rounds_used 与剧本轮数一致（prepare/run/run/finish = 4 轮）= llm_calls。
    assert out.rounds_used == 4
    assert out.llm_calls == 4
    assert runner.calls == [
        [prep.python_exe, "train.py"], [prep.python_exe, "eval.py"],
    ]


def test_cp_e2_2_max_rounds_force_finish_no_overrun(monkeypatch, tmp_path):
    """永远 tool_call 的 LLM：budget_check 到顶 → force_finish，rounds 不越界。"""
    wd = _make_workdir_with_venv(tmp_path)
    runner = SeqRunner([{"exit_code": 0, "stdout": "spin"}])
    monkeypatch.setattr(execution_module, "run_in_venv", runner)
    _patch_llm_plumbing(monkeypatch, ScriptedExecLLM(mode="loop"))

    out = execution_module._run_execution_agent(_base_state(), wd, _plan())

    # budget_check_node 消费 REACT_MAX_ROUNDS_EXECUTION：round 到顶 force_finish。
    assert out.rounds_used == REACT_MAX_ROUNDS_EXECUTION, \
        "到顶后 rounds_used 应恰为 max_rounds（reasoning×(max-1) + force_finish）"
    assert out.llm_calls == REACT_MAX_ROUNDS_EXECUTION
    # 工具最多执行 max_rounds-1 次（最后一轮被 force_finish 拦下），绝不越界。
    assert len(runner.calls) == REACT_MAX_ROUNDS_EXECUTION - 1
    assert len(out.run_results) == REACT_MAX_ROUNDS_EXECUTION - 1


# ===========================================================================
# CP-E2-3 agent 谎报拦截（E2 层：真实结果不被 agent 自述覆盖）
# ===========================================================================


def test_cp_e2_3_agent_lie_real_exit_code_preserved(monkeypatch, tmp_path):
    wd = _make_workdir_with_venv(tmp_path)
    runner = SeqRunner([{"exit_code": 2, "stderr": "RuntimeError: boom"}])
    monkeypatch.setattr(execution_module, "run_in_venv", runner)
    _patch_llm_plumbing(monkeypatch, ScriptedExecLLM(mode="liar"))

    out = execution_module._run_execution_agent(_base_state(), wd, _plan())

    # LLM <result> 自称 all_exit_zero=true，但 ExecAgentOutput 携带真实非 0 结果
    # ——编排层收尾（E3 _classify_execution / _build_execution_result）只认这里。
    assert len(out.run_results) == 1
    assert out.run_results[0].exit_code == 2
    assert "RuntimeError" in out.run_results[0].stderr
    assert out.prep is None  # 剧本未 prepare，不捏造 prep


# ===========================================================================
# CP-E2-4 子图异常降级 + GraphBubbleUp 直通
# ===========================================================================


def test_cp_e2_4_subgraph_exception_degrades_with_warning(monkeypatch, tmp_path, caplog):
    _patch_llm_plumbing(monkeypatch, ScriptedExecLLM(mode="full"))

    def boom_factory(*a, **k):
        raise RuntimeError("subgraph build exploded")

    monkeypatch.setattr(execution_module, "create_react_subgraph", boom_factory)
    with caplog.at_level(logging.WARNING):
        out = execution_module._run_execution_agent(
            _base_state(), str(tmp_path / "wd"), _plan(),
        )
    assert out.prep is None and out.run_results == []
    assert out.rounds_used == 0 and out.llm_calls == 0
    assert any(
        "execution ReAct 子图执行失败" in r.message and "RuntimeError" in r.message
        for r in caplog.records
    )


def test_cp_e2_4_graph_bubble_up_passes_through(monkeypatch, tmp_path):
    """interrupt#3 等 LangGraph 控制流（GraphBubbleUp）绝不被降级 except 吞掉
    （BUG-S4-B1-01 同一条红线：吞掉则主图永不暂停）。"""
    _patch_llm_plumbing(monkeypatch, ScriptedExecLLM(mode="full"))

    class _BubblingSubgraph:
        def invoke(self, initial):
            raise GraphBubbleUp()

    monkeypatch.setattr(
        execution_module, "create_react_subgraph", lambda *a, **k: _BubblingSubgraph(),
    )
    with pytest.raises(GraphBubbleUp):
        execution_module._run_execution_agent(_base_state(), str(tmp_path / "wd"), _plan())


# ===========================================================================
# CP-E2-5 execution 侧 request_user_input：认证失败 → interrupt#3 → resume →
# 重试成功（AC-S4-06 execution 侧 mock 证 + R-S4-10 合并路径实证）
# ===========================================================================


class _ParentState(TypedDict):
    exit_codes: List[int]
    n_runs: int
    rounds_used: int
    prep_none: bool
    second_stdout: str


def test_cp_e2_5_credential_flow_interrupt_resume_and_r_s4_10_merge(
    monkeypatch, tmp_path, caplog,
):
    wd = _make_workdir_with_venv(tmp_path)
    runner = SeqRunner([
        {"exit_code": 128, "stderr": "fatal: Authentication failed for 'https://github.com/x/y'"},
        {"exit_code": 0, "stdout": "fetched ok (full fidelity stdout)"},
    ])
    monkeypatch.setattr(execution_module, "run_in_venv", runner)
    _patch_llm_plumbing(monkeypatch, ScriptedExecLLM(mode="credential"))

    state = _base_state()
    plan = _plan([{"command": "python fetch.py"}])

    def agent_node(_: _ParentState) -> dict:
        out = execution_module._run_execution_agent(state, wd, plan)
        return {
            "exit_codes": [r.exit_code for r in out.run_results],
            "n_runs": len(out.run_results),
            "rounds_used": out.rounds_used,
            "prep_none": out.prep is None,
            "second_stdout": out.run_results[-1].stdout if out.run_results else "",
        }

    builder = StateGraph(_ParentState)
    builder.add_node("agent", agent_node)
    builder.add_edge(START, "agent")
    builder.add_edge("agent", END)
    app = builder.compile(checkpointer=InMemorySaver())
    cfg = {"configurable": {"thread_id": "e2-cred"}}

    # 第一段：run（认证失败）→ request_user_input → interrupt#3 暂停主图。
    paused = app.invoke(
        {"exit_codes": [], "n_runs": 0, "rounds_used": 0,
         "prep_none": True, "second_stdout": ""},
        cfg,
    )
    intr = paused.get("__interrupt__")
    assert intr, "缺凭证时必须以 interrupt#3 暂停主图"
    assert intr[0].value["interrupt_kind"] == INTERRUPT_KIND_USER_INPUT
    assert intr[0].value["is_sensitive"] is True
    assert runner.calls == [["python", "fetch.py"]] or len(runner.calls) == 1, \
        "暂停前恰执行一次 run（认证失败那次）"

    # 第二段：resume 注入凭证 → 再 run（成功）→ 收尾。
    with caplog.at_level(logging.WARNING):
        final = app.invoke(
            Command(resume={"value": "TOKEN-e2", "remember": False}), cfg,
        )
    assert "__interrupt__" not in final

    # 走通：两次 run（前序失败不重放：B2 门禁 → run_in_venv 总调用恰 2 次）。
    assert len(runner.calls) == 2
    assert final["n_runs"] == 2
    assert final["exit_codes"] == [128, 0], \
        "完整序列 = pre-interrupt 失败 run + post-resume 成功 run"

    # R-S4-10 合并路径实证：resume 重建收集器只有尾段（成功 run），
    # 前段（认证失败 run）从子图 messages 回读补全，且打 WARNING 留痕。
    assert any("收集器缺失前段" in r.message for r in caplog.records), \
        "R-S4-10 合并路径必须 WARNING 留痕（非静默）"
    # 尾段用收集器 → 全保真 stdout（未截断原文）。
    assert final["second_stdout"] == "fetched ok (full fidelity stdout)"
    assert final["prep_none"] is True  # 剧本未 prepare

    # 敏感值旁路：resume 值登记进程内 sensitive set（is_sensitive=True 契约）。
    assert "TOKEN-e2" in set(secrets_store.iter_sensitive_values())


def test_cp_e2_5_flow_stable_across_3_runs(monkeypatch, tmp_path):
    """LLM 服从度类场景连跑 3 次结论一致（回归验收纪律第 5 条，机理类离线剧本）。"""
    for i in range(3):
        wd = _make_workdir_with_venv(tmp_path / f"r{i}")
        runner = SeqRunner([
            {"exit_code": 128, "stderr": "fatal: Authentication failed"},
            {"exit_code": 0, "stdout": "ok"},
        ])
        monkeypatch.setattr(execution_module, "run_in_venv", runner)
        _patch_llm_plumbing(monkeypatch, ScriptedExecLLM(mode="credential"))
        state = _base_state()
        plan = _plan([{"command": "python fetch.py"}])

        class _S(TypedDict):
            exit_codes: List[int]

        def node(_: _S) -> dict:
            out = execution_module._run_execution_agent(state, wd, plan)
            return {"exit_codes": [r.exit_code for r in out.run_results]}

        b = StateGraph(_S)
        b.add_node("agent", node)
        b.add_edge(START, "agent")
        b.add_edge("agent", END)
        app = b.compile(checkpointer=InMemorySaver())
        cfg = {"configurable": {"thread_id": f"e2-stab-{i}"}}
        paused = app.invoke({"exit_codes": []}, cfg)
        assert paused.get("__interrupt__"), f"run#{i}: interrupt 必须出现"
        final = app.invoke(Command(resume={"value": "T", "remember": False}), cfg)
        assert final["exit_codes"] == [128, 0], f"run#{i}: 完整序列一致"
