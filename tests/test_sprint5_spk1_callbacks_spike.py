"""Sprint 5 批次 0 / T-S5-0-1：CP-SPK-1 callbacks 传播 spike（架构 §4 Q-S5-8 / §10.1 R-1）。

验证目标（决定批次 4 主/回退路径，S5-07 前提）：
    顶层 ``build_graph().invoke(initial_state, {**config, "callbacks": [handler]})``
    注入的 callbacks，能否经 langchain-core ``var_child_runnable_config`` contextvar
    自动传播，穿透两类"节点内手动 subgraph.invoke"边界到达 handler：

    1. coding 路径：``_make_react_wrapper`` → ``subgraph.invoke(initial)``（react_base.py:873）；
    2. execution 路径：``_run_execution_agent`` → ``subgraph.invoke(initial)``（execution.py:1272）。

事件元数据可用性实证（喂 T-S5-4-1 ActivityEvent schema 落地）：
    - ``metadata["langgraph_node"]`` 能否取到节点名（取到的是内层子图节点名还是外层主图节点名）；
    - ``on_tool_start`` 入参能否做 ≤120 字符摘要；
    - ``on_llm_end`` 文本能否截断预览。

装置说明（裁剪自 tests/test_sprint4_e2e.py mock harness，纯 mock 零配额）：
    - 上游 4 节点（intake/analysis/scout/planning）patch 为 fake 纯函数（planning 直接
      approve，绕过 interrupt#1）——它们不产生 LLM/工具事件，因此 handler 收到的全部
      LLM/工具事件**必然**来自 coding/execution 内层子图，归属断言天然干净；
    - coding / execution 节点保持真实节点代码路径（wrapper → subgraph.invoke /
      手写复合 → subgraph.invoke），仅替换 create_llm 为剧本 LLM、sandbox run_in_venv
      为 CountingRunner、deepxiv 工具为惰性 mock；
    - 剧本：coding 一次 write_code_file + 收尾；execution 一次 run_in_sandbox（exit 0，
      B 档成功，无修复循环、无 interrupt#2）+ 收尾 → reporting → END，单次 invoke 全程。

不标 e2e marker：纯 mock、零 deepxiv/LLM 配额消耗，默认收集运行。
"""
from __future__ import annotations

import importlib
import json
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytest
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import config  # noqa: E402
import core.graph as graph_module  # noqa: E402
import sandbox.local_venv as lv  # noqa: E402
from core import react_base  # noqa: E402
from core.graph import build_graph  # noqa: E402
from sandbox.local_venv import SandboxRunResult  # noqa: E402

# C2 范式：显式 export 可能遮蔽子模块，统一 importlib 取模块对象。
execution_module = importlib.import_module("core.nodes.execution")
coding_module = importlib.import_module("core.nodes.coding")

PAPER_ARXIV_ID = "2405.14831"  # 与 sp4 e2e 同靶（纯 mock，不触网）

# 身份短语（与节点 system prompt 常量绑定，归属判定锚点）
_CODING_IDENTITY = "资深机器学习复现工程师"
_EXECUTION_IDENTITY = "复现执行工程师"


# ---------------------------------------------------------------------------
# 1) 最小计数型 BaseCallbackHandler（dev-plan T-S5-0-1 第 1 项）
# ---------------------------------------------------------------------------


class SpikeCallbackHandler(BaseCallbackHandler):
    """记录 on_chat_model_start / on_llm_end / on_tool_start 事件及 metadata。

    归属策略：on_chat_model_start 能看到 messages（首条 SystemMessage 含节点身份
    短语），据此把 run_id → identity 存表；on_llm_end 只有 run_id，回查表归属。
    这同时实证了 T-S5-4-1 的落地约束——on_llm_end 拿不到 metadata/messages，
    node 归属需在 *start 事件缓存。
    """

    def __init__(self) -> None:
        self.events: List[Dict[str, Any]] = []
        self._run_identity: Dict[str, str] = {}
        self._run_metadata: Dict[str, Optional[Dict[str, Any]]] = {}

    # ---- helpers ----

    @staticmethod
    def _identity_of(text: str) -> str:
        if _CODING_IDENTITY in text:
            return "coding"
        if _EXECUTION_IDENTITY in text:
            return "execution"
        return "unknown"

    def by_kind(self, kind: str) -> List[Dict[str, Any]]:
        return [e for e in self.events if e["kind"] == kind]

    # ---- LLM 事件 ----

    def on_chat_model_start(  # noqa: D102
        self, serialized, messages, *, run_id, parent_run_id=None,
        tags=None, metadata=None, **kwargs,
    ):
        first_text = ""
        try:
            batch = messages[0] if messages else []
            first_text = str(batch[0].content) if batch else ""
        except Exception:  # noqa: BLE001 - spike 观测防御
            pass
        identity = self._identity_of(first_text)
        self._run_identity[str(run_id)] = identity
        self._run_metadata[str(run_id)] = dict(metadata) if metadata else None
        self.events.append({
            "kind": "chat_model_start",
            "run_id": str(run_id),
            "parent_run_id": str(parent_run_id) if parent_run_id else None,
            "identity": identity,
            "metadata": dict(metadata) if metadata else None,
            "tags": list(tags) if tags else None,
        })

    def on_llm_start(  # noqa: D102 - 防御性兜底（chat model 正常走 on_chat_model_start）
        self, serialized, prompts, *, run_id, parent_run_id=None,
        tags=None, metadata=None, **kwargs,
    ):
        text = prompts[0] if prompts else ""
        identity = self._identity_of(str(text))
        self._run_identity[str(run_id)] = identity
        self._run_metadata[str(run_id)] = dict(metadata) if metadata else None
        self.events.append({
            "kind": "llm_start",
            "run_id": str(run_id),
            "identity": identity,
            "metadata": dict(metadata) if metadata else None,
        })

    def on_llm_end(self, response, *, run_id, parent_run_id=None, **kwargs):  # noqa: D102
        text = ""
        try:
            gen = response.generations[0][0]
            msg = getattr(gen, "message", None)
            text = str(msg.content) if msg is not None else str(gen.text)
        except Exception:  # noqa: BLE001 - spike 观测防御
            pass
        rid = str(run_id)
        self.events.append({
            "kind": "llm_end",
            "run_id": rid,
            "identity": self._run_identity.get(rid, "unknown"),
            "metadata": self._run_metadata.get(rid),
            "text": text,
        })

    # ---- 工具事件 ----

    def on_tool_start(  # noqa: D102
        self, serialized, input_str, *, run_id, parent_run_id=None,
        tags=None, metadata=None, inputs=None, **kwargs,
    ):
        name = (serialized or {}).get("name") or kwargs.get("name") or ""
        self.events.append({
            "kind": "tool_start",
            "run_id": str(run_id),
            "tool_name": str(name),
            "input_str": str(input_str),
            "inputs": inputs if isinstance(inputs, dict) else None,
            "metadata": dict(metadata) if metadata else None,
            "tags": list(tags) if tags else None,
        })


# ---------------------------------------------------------------------------
# 2) 剧本 LLM（裁剪自 sp4 DispatchScriptLLM：coding/execution 双脚本，各 2 轮）
# ---------------------------------------------------------------------------


class SpikeScriptLLM(BaseChatModel):
    """按 SystemMessage 身份短语分发脚本；真 BaseChatModel 子类 → 走 langchain
    callbacks 机制（on_chat_model_start / on_llm_end 由基类框架代发）。"""

    @property
    def _llm_type(self) -> str:
        return "spk1-scripted"

    def bind_tools(self, tools: Any, **kwargs: Any) -> "SpikeScriptLLM":
        return self

    @staticmethod
    def _count_tool(messages, name: str) -> int:
        return sum(
            1 for m in messages
            if isinstance(m, ToolMessage) and getattr(m, "name", None) == name
        )

    @staticmethod
    def _call(name: str, args: Dict[str, Any], cid: str) -> AIMessage:
        return AIMessage(content="", tool_calls=[
            {"name": name, "args": args, "id": cid, "type": "tool_call"},
        ])

    @staticmethod
    def _final(payload: Dict[str, Any]) -> AIMessage:
        body = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        return AIMessage(content=(
            f"{config.REACT_RESULT_TAG_OPEN}{body}{config.REACT_RESULT_TAG_CLOSE}"))

    def _coding_step(self, messages) -> AIMessage:
        if self._count_tool(messages, "write_code_file") == 0:
            return self._call(
                "write_code_file",
                {"path": "train.py", "content": "print('spk1')\n"},
                "spk1_w1")
        return self._final({
            "files_written": ["train.py"], "entry_script": "train.py",
            "summary": "spk1 生成 train.py", "notes": None,
        })

    def _execution_step(self, messages) -> AIMessage:
        if self._count_tool(messages, "run_in_sandbox") == 0:
            return self._call(
                "run_in_sandbox", {"command": "python train.py"}, "spk1_r1")
        return self._final({
            "steps_attempted": 1, "all_exit_zero": True,
            "summary": "spk1 执行 train 完成", "notes": None,
        })

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        sys_text = str(messages[0].content) if messages else ""
        if _CODING_IDENTITY in sys_text:
            ai = self._coding_step(messages)
        elif _EXECUTION_IDENTITY in sys_text:
            ai = self._execution_step(messages)
        else:  # pragma: no cover - 剧本分发防御
            raise AssertionError(f"未知 system prompt，无法分发脚本: {sys_text[:60]!r}")
        return ChatResult(generations=[ChatGeneration(message=ai)])


# ---------------------------------------------------------------------------
# mock 脚手架（sp4 e2e 范式裁剪）
# ---------------------------------------------------------------------------


def _isolate_workspace(monkeypatch, ws: Path) -> None:
    from core.tools import code_fs_tools

    ws.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(config, "WORKSPACE_DIR", ws)
    monkeypatch.setattr(lv, "WORKSPACE_DIR", ws)
    monkeypatch.setattr(code_fs_tools, "WORKSPACE_DIR", ws)
    monkeypatch.setattr(coding_module, "WORKSPACE_DIR", ws)


def _install_inert_coding_tools(monkeypatch) -> None:
    @tool
    def read_section(arxiv_id: str, section_name: str) -> str:
        """Mock read_section."""
        return f"section {section_name}"

    @tool
    def web_search(query: str) -> str:
        """Mock web_search."""
        return "search result"

    monkeypatch.setattr(coding_module, "read_section_tool", lambda *a, **k: read_section)
    monkeypatch.setattr(coding_module, "web_search_tool", lambda *a, **k: web_search)


_FAKE_PLAN: Dict[str, Any] = {
    "plan_summary": "spk1 剧本计划",
    "environment": {"python": "3.11"},
    "data_preparation": ["无"],
    "code_strategy": "from_scratch",
    "execution_steps": [
        {"step_name": "train", "command": "python train.py",
         "expected_output": "metrics"},
    ],
    "expected_results": {"accuracy": 0.9},
    "estimated_time": "1h",
    "deliverables": ["train.py"],
}


def _patch_upstream_fakes(monkeypatch) -> None:
    """上游 4 节点全 fake（planning 直接 approve，绕过 interrupt#1）。
    fake 均为普通纯函数，不触发任何 LLM/工具事件。"""

    def fake_intake(state):
        return {
            "paper_meta": {"arxiv_id": PAPER_ARXIV_ID, "title": "spk1 mock paper"},
            "current_step": "paper_intake",
        }

    def fake_analysis(state):
        return {
            "paper_analysis": {
                "method_summary": "mock 方法概述",
                "method_summary_en": "mock method",
                "metrics": ["accuracy"],
                "baseline_results": {"accuracy": 0.91},
                "datasets": ["mock-ds"],
                "framework": "PyTorch",
            },
            "current_step": "paper_analysis",
        }

    def fake_scout(state):
        return {"resource_info": {"selected_repo": None}, "current_step": "resource_scout"}

    def fake_planning(state):
        return {
            "reproduction_plan": {**_FAKE_PLAN, "approved": True},
            "current_step": "planning",
        }

    monkeypatch.setattr(graph_module, "paper_intake", fake_intake)
    monkeypatch.setattr(graph_module, "paper_analysis", fake_analysis)
    monkeypatch.setattr(graph_module, "resource_scout", fake_scout)
    monkeypatch.setattr(graph_module, "planning", fake_planning)


class CountingRunner:
    """run_in_venv mock（sp4 E4 范式裁剪）：train.py → exit 0，B 档成功收尾。"""

    def __init__(self) -> None:
        self.calls: List[List[str]] = []

    def __call__(self, python_exe, command, work_dir, *a, **k):
        self.calls.append(list(command))
        return SandboxRunResult(
            exit_code=0, stdout="accuracy: 0.9", stderr="",
            duration_seconds=0.1, timed_out=False, output_truncated=False,
            command=list(command),
        )


def _wire_llms(monkeypatch, llm: SpikeScriptLLM, runner: CountingRunner) -> None:
    monkeypatch.setattr(react_base, "create_llm", lambda cfg: llm)
    monkeypatch.setattr(execution_module, "create_llm", lambda cfg: llm)
    monkeypatch.setattr(execution_module, "run_in_venv", runner)
    monkeypatch.setattr(execution_module, "collect_artifacts", lambda *a, **k: [])
    monkeypatch.setattr(execution_module, "_llm_extract_metrics", lambda *a, **k: ({}, 0))


def _initial_graph_state(ws: Path) -> Dict[str, Any]:
    return {
        "user_input": PAPER_ARXIV_ID,
        "workspace_dir": str(ws),
        "llm_config_set": {
            "default": {
                "base_url": "https://example.test/v1",
                "model": "scripted-model",
                "api_key": "sk-test",
                "temperature": 0.3,
                "max_tokens": 1024,
            },
            "overrides": {},
        },
        "node_errors": [],
        "degraded_nodes": [],
        "fix_loop_history": [],
        "fix_loop_count": 0,
        "retry_budget_remaining": 40,
        "_dev_loop_llm_calls": 0,
        "_dev_loop_route": None,
        "execution_result": None,
        "messages": [],
    }


def _prepare_code_dir(ws: Path) -> Path:
    """预置 code_output_dir + .venv 痕迹（run_in_sandbox python_exe 确定性推导）。"""
    code_dir = ws / PAPER_ARXIV_ID / "code"
    (code_dir / ".venv" / "bin").mkdir(parents=True, exist_ok=True)
    (code_dir / ".venv" / "pyvenv.cfg").write_text("home = /usr\n")
    return code_dir


# ---------------------------------------------------------------------------
# spike harness：单次 invoke 全程（intake→…→coding→execution→reporting→END）
# ---------------------------------------------------------------------------


def _run_spike(monkeypatch, tmp_path: Path) -> Tuple[Dict[str, Any], SpikeCallbackHandler]:
    ws = tmp_path / "ws_spk1"
    _isolate_workspace(monkeypatch, ws)
    _install_inert_coding_tools(monkeypatch)
    _patch_upstream_fakes(monkeypatch)
    _wire_llms(monkeypatch, SpikeScriptLLM(), CountingRunner())
    _prepare_code_dir(ws)

    graph = build_graph(checkpointer=InMemorySaver())
    handler = SpikeCallbackHandler()
    cfg = {"configurable": {"thread_id": f"spk1-{uuid.uuid4().hex[:8]}"}}

    # 核心被测语句：顶层 config 注入 callbacks（app.py 未来同款注入点）。
    final_state = graph.invoke(
        _initial_graph_state(ws), {**cfg, "callbacks": [handler]})
    return final_state, handler


# ---------------------------------------------------------------------------
# 断言用例
# ---------------------------------------------------------------------------


def test_spk1_graph_reaches_reporting(monkeypatch, tmp_path):
    """前提自检：剧本驱动全图跑通到 reporting（无 interrupt 中途暂停）。"""
    final_state, _ = _run_spike(monkeypatch, tmp_path)
    assert final_state.get("current_step") == "reporting", (
        f"剧本未走完全图，current_step={final_state.get('current_step')!r}")
    assert final_state.get("report_path"), "reporting 未产出报告"


def test_spk1_coding_path_callbacks_propagate(monkeypatch, tmp_path):
    """CP-0.1-1（coding 路径）：_make_react_wrapper → subgraph.invoke（react_base.py:873）
    内层 LLM/工具事件到达顶层 handler。"""
    _, handler = _run_spike(monkeypatch, tmp_path)

    coding_llm_starts = [
        e for e in handler.by_kind("chat_model_start") if e["identity"] == "coding"]
    coding_llm_ends = [
        e for e in handler.by_kind("llm_end") if e["identity"] == "coding"]
    write_tool_starts = [
        e for e in handler.by_kind("tool_start") if e["tool_name"] == "write_code_file"]

    # 剧本 = 2 轮 LLM（1 次 tool_call + 1 次 final）+ 1 次 write_code_file
    assert len(coding_llm_starts) >= 2, (
        f"coding 内层 on_chat_model_start 未到达 handler: {len(coding_llm_starts)}")
    assert len(coding_llm_ends) >= 2, (
        f"coding 内层 on_llm_end 未到达 handler: {len(coding_llm_ends)}")
    assert len(write_tool_starts) >= 1, (
        "coding 内层 on_tool_start(write_code_file) 未到达 handler")


def test_spk1_execution_path_callbacks_propagate(monkeypatch, tmp_path):
    """CP-0.1-1（execution 路径）：_run_execution_agent → 裸 subgraph.invoke
    （execution.py:1272）内层 LLM/工具事件到达顶层 handler。"""
    _, handler = _run_spike(monkeypatch, tmp_path)

    exec_llm_starts = [
        e for e in handler.by_kind("chat_model_start") if e["identity"] == "execution"]
    exec_llm_ends = [
        e for e in handler.by_kind("llm_end") if e["identity"] == "execution"]
    sandbox_tool_starts = [
        e for e in handler.by_kind("tool_start") if e["tool_name"] == "run_in_sandbox"]

    assert len(exec_llm_starts) >= 2, (
        f"execution 内层 on_chat_model_start 未到达 handler: {len(exec_llm_starts)}")
    assert len(exec_llm_ends) >= 2, (
        f"execution 内层 on_llm_end 未到达 handler: {len(exec_llm_ends)}")
    assert len(sandbox_tool_starts) >= 1, (
        "execution 内层 on_tool_start(run_in_sandbox) 未到达 handler")


def test_spk1_event_metadata_availability(monkeypatch, tmp_path):
    """CP-0.1-2：事件元数据三项实证（T-S5-4-1 ActivityEvent schema 消费前提）。

    1. metadata["langgraph_node"] 可取；
    2. on_tool_start 入参可做 ≤120 字符摘要；
    3. on_llm_end 文本可截断预览。
    """
    _, handler = _run_spike(monkeypatch, tmp_path)

    # --- 实证 1：langgraph_node ---
    llm_starts = handler.by_kind("chat_model_start")
    tool_starts = handler.by_kind("tool_start")
    assert llm_starts and tool_starts, "无事件可实证（传播断言应已先失败）"

    llm_nodes = [
        (e["metadata"] or {}).get("langgraph_node") for e in llm_starts]
    tool_nodes = [
        (e["metadata"] or {}).get("langgraph_node") for e in tool_starts]
    # 观测输出（spike 报告素材，pytest -s 可见）
    print("\n[SPK1] llm_start langgraph_node observed:", sorted(set(map(str, llm_nodes))))
    print("[SPK1] tool_start langgraph_node observed:", sorted(set(map(str, tool_nodes))))
    print("[SPK1] llm_start metadata keys sample:",
          sorted((llm_starts[0]["metadata"] or {}).keys()))
    print("[SPK1] tool_start metadata keys sample:",
          sorted((tool_starts[0]["metadata"] or {}).keys()))
    checkpoint_ns = [
        (e["metadata"] or {}).get("checkpoint_ns") for e in llm_starts]
    print("[SPK1] llm_start checkpoint_ns observed:", sorted(set(map(str, checkpoint_ns))))
    print("[SPK1] llm_start langgraph_checkpoint_ns observed:", sorted({
        str((e["metadata"] or {}).get("langgraph_checkpoint_ns")) for e in llm_starts}))
    assert all(n is not None for n in llm_nodes), (
        f"on_chat_model_start metadata 缺 langgraph_node: {llm_nodes}")
    assert all(n is not None for n in tool_nodes), (
        f"on_tool_start metadata 缺 langgraph_node: {tool_nodes}")

    # --- 实证 1b（T-S5-4-1 直接消费）：langgraph_node 取到的是**内层子图节点名**
    # （reasoning / tool_executor），外层主图节点名（coding / execution）须从
    # metadata["checkpoint_ns"] 前缀恢复："<外层节点名>:<uuid>" → split(":")[0]。
    outer_nodes = {
        str((e["metadata"] or {}).get("checkpoint_ns", "")).split(":")[0]
        for e in llm_starts + tool_starts
    }
    assert {"coding", "execution"} <= outer_nodes, (
        f"checkpoint_ns 前缀无法恢复外层节点名: {outer_nodes}")
    inner_nodes = set(map(str, llm_nodes + tool_nodes))
    assert inner_nodes <= {"reasoning", "tool_executor", "force_finish", "finalize"}, (
        f"langgraph_node 观测值超出内层子图节点集合: {inner_nodes}")

    # --- 实证 2：on_tool_start 入参 ≤120 字符摘要 ---
    for e in tool_starts:
        args_repr = json.dumps(e["inputs"], ensure_ascii=False, default=str) \
            if e["inputs"] is not None else e["input_str"]
        summary = f"{e['tool_name']}({args_repr})"[:120]
        assert isinstance(summary, str) and 0 < len(summary) <= 120
        assert summary.startswith(e["tool_name"])  # 摘要含工具名，可渲染
    print("[SPK1] tool summary sample:",
          f"{tool_starts[0]['tool_name']}({tool_starts[0]['input_str']})"[:120])

    # --- 实证 3：on_llm_end 文本可截断 ---
    llm_ends = handler.by_kind("llm_end")
    assert llm_ends, "无 on_llm_end 事件"
    # 剧本 final 轮 content 非空（<result>JSON</result>），tool_call 轮 content 为空串
    non_empty = [e for e in llm_ends if e["text"]]
    assert non_empty, "on_llm_end 无可截断文本（final 轮 content 应非空）"
    preview = non_empty[0]["text"][:160]
    assert isinstance(preview, str) and len(preview) <= 160
    print("[SPK1] llm_end text preview sample:", preview[:80])

    # --- 附带实证：on_llm_end 事件本身不带 metadata，须 *start 缓存回查 ---
    print("[SPK1] llm_end identity attribution:",
          sorted({e["identity"] for e in llm_ends}))
