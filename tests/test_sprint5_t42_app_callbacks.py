"""Sprint 5 批次 4 / T-S5-4-2：app.py callbacks 注入 + get_activity_tail 自测。

覆盖 dev-plan §批次 4 任务 T-S5-4-2 自测检查点（主路径，无回退档启用）：
    - CP-4.2-1 invoke / resume 两路径均带 callbacks（spy 断言）；per-thread 隔离
      （两 thread 事件不串）；resume 复用同一 handler 实例（seq 跨 invoke 连续）。
    - CP-4.2-2 端到端 mock：mock LLM 驱动主图 → coding/execution 子图内事件到达
      handler（T-S5-0-1 spike 结论在**真实 GraphController 装配**下复证，
      AC-S5-13 事件生成部分）。
    - CP-4.2-3 get_activity_tail 尾部 n 条快照语义（不可变、越界安全、未知 thread
      空 tuple 且只读不建 handler）。
    - CP-4.2-4 事件不进 checkpoint/state（AC-S5-14）：跑完 mock 流程后 checkpoint
      DB（真实 SqliteSaver 临时库，含 WAL/SHM sidecar）与 state 快照序列化内容
      grep 无活动流事件痕迹（"⏺ " 前缀为活动流 text 组装专属标记，全仓库仅
      core/activity_stream.py 产出）；react_base 与活动流零耦合（主路径红线的
      结构化常驻守门；零字节改动另由任务收口时 `git diff HEAD -- core/react_base.py`
      实证归档）。

装置说明：
    - CP-4.2-1/3 用真实 GraphController + SpyGraph（哨兵 checkpointer，零 IO）；
      SpyGraph.invoke 会向注入的 callbacks 发一条带 thread_id 标记的 tool 事件，
      实证事件确实写入 config 注入的 handler 实例（而非其它容器）。
    - CP-4.2-2/4 裁剪复用 T-S5-0-1 spike 套件的 mock 剧本装置
      （tests/test_sprint5_spk1_callbacks_spike.py：上游 4 节点 fake + 剧本 LLM +
      CountingRunner），但以**真实 GraphController.start_task → 工作线程 →
      真实 get_checkpointer(SqliteSaver 临时库)** 驱动，即 app.py 生产装配路径。

纯 mock：零 LLM/deepxiv 配额、零网络外呼，不标 e2e marker，默认收集运行。
"""
from __future__ import annotations

import json
import sys
import threading
from pathlib import Path
from typing import Any, Dict, List, Tuple
from uuid import uuid4

import pytest
from langgraph.types import Command

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import app as app_module  # noqa: E402
import config  # noqa: E402
from app import GraphController, _make_config  # noqa: E402
from core.activity_stream import ActivityStreamHandler  # noqa: E402

# 裁剪复用 T-S5-0-1 spike 的 mock 剧本装置（tests 为包，pytest prepend 同名导入）。
import tests.test_sprint5_spk1_callbacks_spike as spk1  # noqa: E402

_EVENT_FIELDS = {"seq", "ts", "node", "kind", "text"}
# 活动流 text 组装专属前缀标记（全仓库仅 core/activity_stream.py 产出，泄漏判据）。
_TOOL_MARKER = "⏺"


def _llm_config_set() -> Dict[str, Any]:
    """最小合法 LLMConfigSet（start_task 入参；剧本 LLM 已 patch，不真连）。"""
    return {
        "default": {
            "base_url": "https://example.test/v1",
            "model": "scripted-model",
            "api_key": "sk-test",
            "temperature": 0.3,
            "max_tokens": 1024,
        },
        "overrides": {},
    }


def _join_worker(controller: GraphController, thread_id: str,
                 timeout: float = 120.0) -> None:
    thread = controller._workers[thread_id]
    thread.join(timeout=timeout)
    assert not thread.is_alive(), f"工作线程未在 {timeout}s 内退出"


# ---------------------------------------------------------------------------
# SpyGraph 装置（CP-4.2-1 / CP-4.2-3：零 IO）
# ---------------------------------------------------------------------------


class SpyGraph:
    """记录 invoke(state, config) 的 mock CompiledGraph。

    invoke 时向 config 注入的 callbacks 发一条带 thread_id 标记的 tool 事件——
    实证事件写入的正是 config["callbacks"] 里的 handler 实例（per-thread 隔离与
    resume 复用实例的断言基础）。
    """

    def __init__(self) -> None:
        self.invoke_calls: List[Tuple[Any, Dict]] = []
        self._lock = threading.Lock()

    def invoke(self, state_or_command: Any, config_dict: Dict) -> Dict:
        with self._lock:
            self.invoke_calls.append((state_or_command, dict(config_dict)))
        thread_id = config_dict["configurable"]["thread_id"]
        for cb in config_dict.get("callbacks", ()):
            cb.on_tool_start(
                {"name": "spy_tool"}, f"marker-{thread_id}", run_id=uuid4(),
                metadata={"checkpoint_ns": f"execution:{uuid4().hex}"})
        return {}


@pytest.fixture
def controller_spy(monkeypatch):
    """真实 GraphController + SpyGraph（哨兵 checkpointer，不触任何 IO）。"""
    spy = SpyGraph()
    monkeypatch.setattr(app_module, "get_checkpointer", lambda db_path=None: object())
    monkeypatch.setattr(app_module, "build_graph", lambda checkpointer=None: spy)
    return GraphController(), spy


# ---------------------------------------------------------------------------
# CP-4.2-1 invoke / resume 两路径 callbacks + per-thread 隔离 + 实例复用
# ---------------------------------------------------------------------------


def test_cp421_worker_invoke_carries_activity_callbacks(controller_spy):
    """start_task → _worker_run 的 graph.invoke config 带 [ActivityStreamHandler]。"""
    controller, spy = controller_spy
    tid = controller.start_task(spk1.PAPER_ARXIV_ID, _llm_config_set())
    _join_worker(controller, tid, timeout=10.0)

    assert len(spy.invoke_calls) == 1
    _, cfg = spy.invoke_calls[0]
    # _make_config 语义零变化（thread_id + checkpoint_ns=""），仅追加 callbacks 键。
    assert cfg["configurable"] == _make_config(tid)["configurable"]
    assert set(cfg) == {"configurable", "callbacks"}
    callbacks = cfg["callbacks"]
    assert isinstance(callbacks, list) and len(callbacks) == 1
    assert isinstance(callbacks[0], ActivityStreamHandler)
    # 注入的正是 controller 持有的 per-thread 实例（get-or-create 落点）。
    assert callbacks[0] is controller._activity_handlers[tid]
    # SpyGraph 经该 handler 发的事件可从公开读侧取到（写读同一实例闭环）。
    (event,) = controller.get_activity_tail(tid)
    assert event["kind"] == "tool" and tid in event["text"]


def test_cp421_resume_reuses_same_handler_instance_seq_continuous(controller_spy):
    """resume 路径同样带 callbacks 且**复用同一 handler 实例**：seq 跨 invoke 连续。"""
    controller, spy = controller_spy
    tid = controller.start_task(spk1.PAPER_ARXIV_ID, _llm_config_set())
    _join_worker(controller, tid, timeout=10.0)
    handler_after_start = controller._activity_handlers[tid]

    controller.resume_with(tid, {"decision": "approve"})
    _join_worker(controller, tid, timeout=10.0)

    assert len(spy.invoke_calls) == 2
    resume_arg, resume_cfg = spy.invoke_calls[1]
    assert isinstance(resume_arg, Command)
    assert resume_arg.resume == {"decision": "approve"}
    assert resume_cfg["configurable"] == _make_config(tid)["configurable"]
    assert set(resume_cfg) == {"configurable", "callbacks"}

    start_handler = spy.invoke_calls[0][1]["callbacks"][0]
    resume_handler = resume_cfg["callbacks"][0]
    assert start_handler is handler_after_start
    assert resume_handler is start_handler, (
        "resume 未复用同一 handler 实例（seq 连续性契约被破坏）")

    # seq 连续性实证：invoke 与 resume 各发 1 条事件 → seq == [1, 2] 不重置。
    events = controller.get_activity_tail(tid)
    assert [e["seq"] for e in events] == [1, 2], (
        f"seq 跨 invoke/resume 须连续: {[e['seq'] for e in events]}")


def test_cp421_per_thread_isolation(controller_spy):
    """两个 thread 各持独立 handler 实例，事件不串流。"""
    controller, _spy = controller_spy
    tid1 = controller.start_task(spk1.PAPER_ARXIV_ID, _llm_config_set())
    _join_worker(controller, tid1, timeout=10.0)
    tid2 = controller.start_task(spk1.PAPER_ARXIV_ID, _llm_config_set())
    _join_worker(controller, tid2, timeout=10.0)
    assert tid1 != tid2

    assert controller._activity_handlers[tid1] is not controller._activity_handlers[tid2]
    (e1,) = controller.get_activity_tail(tid1)
    (e2,) = controller.get_activity_tail(tid2)
    assert tid1 in e1["text"] and tid2 not in e1["text"], f"thread1 流被串: {e1}"
    assert tid2 in e2["text"] and tid1 not in e2["text"], f"thread2 流被串: {e2}"
    # per-instance 计数器：两 thread 各自 seq 从 1 起，互不共享。
    assert e1["seq"] == 1 and e2["seq"] == 1


# ---------------------------------------------------------------------------
# CP-4.2-2 / CP-4.2-4 共用装置：真实 GraphController 装配的 mock e2e
# ---------------------------------------------------------------------------


def _run_controller_e2e(
    monkeypatch, tmp_path: Path,
) -> Tuple[GraphController, str, Path, "spk1.CountingRunner"]:
    """spike mock 剧本 + 真实 app.py 生产装配（start_task → worker → SqliteSaver）。

    与 spike 的差异（复证意义所在）：不裸调 graph.invoke，而是走
    GraphController.start_task → 工作线程 _worker_run → 真实 get_checkpointer
    （SqliteSaver WAL 临时库，config.CHECKPOINT_DB_PATH 重定向 tmp）→
    build_graph.invoke({**config, "callbacks": [handler]}) 全链路。
    """
    ws = tmp_path / "ws_t42"
    spk1._isolate_workspace(monkeypatch, ws)
    spk1._install_inert_coding_tools(monkeypatch)
    spk1._patch_upstream_fakes(monkeypatch)
    runner = spk1.CountingRunner()
    spk1._wire_llms(monkeypatch, spk1.SpikeScriptLLM(), runner)
    spk1._prepare_code_dir(ws)

    db_path = tmp_path / "ckpt_t42.sqlite"
    monkeypatch.setattr(config, "CHECKPOINT_DB_PATH", db_path)

    controller = GraphController()  # 真实 get_checkpointer + build_graph
    tid = controller.start_task(spk1.PAPER_ARXIV_ID, _llm_config_set())
    _join_worker(controller, tid, timeout=120.0)
    err = controller.get_worker_error(tid)
    assert err is None, f"mock e2e 工作线程异常: {err!r}"
    return controller, tid, db_path, runner


def test_cp422_e2e_mock_subgraph_events_reach_handler(monkeypatch, tmp_path):
    """spike 结论在真实 GraphController 装配下复证：coding/execution 子图内
    LLM/工具事件穿透手动 subgraph.invoke 边界到达 per-thread handler。"""
    controller, tid, _db_path, runner = _run_controller_e2e(monkeypatch, tmp_path)

    # 前提自检：剧本跑完全图（无 interrupt 中途暂停），sandbox mock 真被调用。
    final_state = controller.poll_state(tid)
    assert final_state and final_state.get("current_step") == "reporting", (
        f"剧本未走完全图: current_step={ (final_state or {}).get('current_step')!r}")
    assert runner.calls, "run_in_sandbox 未到达 CountingRunner"

    events = controller.get_activity_tail(tid)
    assert events, "活动流 handler 未收到任何事件（callbacks 传播断裂）"

    # 事件 schema 合规（T-S5-4-1 契约在真实装配下复证）。
    for e in events:
        assert set(e.keys()) == _EVENT_FIELDS, f"事件字段集不合规: {e}"
        assert e["kind"] in ("tool", "llm")
        assert 0 < len(e["text"]) <= (120 if e["kind"] == "tool" else 160)
    assert {e["kind"] for e in events} == {"tool", "llm"}

    # coding 子图（_make_react_wrapper → subgraph.invoke）与 execution 子图
    # （_run_execution_agent → subgraph.invoke）两路径事件均到达。
    tool_texts = [e["text"] for e in events if e["kind"] == "tool"]
    assert any(t.startswith("⏺ write_code_file(") for t in tool_texts), (
        f"coding 子图工具事件缺失: {tool_texts}")
    assert any(t.startswith("⏺ run_in_sandbox(") for t in tool_texts), (
        f"execution 子图工具事件缺失: {tool_texts}")

    # node 归属：checkpoint_ns 前缀恢复出外层主图节点名（spike 实证 1b 落地复证）。
    nodes = {e["node"] for e in events}
    assert {"coding", "execution"} <= nodes, f"外层节点归属缺失: {nodes}"

    # seq 单调（单 handler 实例全程）。
    seqs = [e["seq"] for e in events]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)


# ---------------------------------------------------------------------------
# CP-4.2-3 get_activity_tail 快照语义
# ---------------------------------------------------------------------------


def test_cp423_tail_snapshot_semantics(controller_spy):
    """尾部 n 条快照：tuple 不可变、n 越界安全、快照与底层 deque 解耦。"""
    controller, _spy = controller_spy
    tid = "t-cp423"
    handler = controller._get_activity_handler(tid)  # 写侧 get-or-create 落点
    for i in range(3):
        handler.on_tool_start({"name": "t"}, f"arg-{i}", run_id=uuid4(),
                              metadata={"checkpoint_ns": "coding:u"})

    full = controller.get_activity_tail(tid)
    assert isinstance(full, tuple) and len(full) == 3
    assert controller.get_activity_tail(tid, None) == full          # n=None 全量
    assert controller.get_activity_tail(tid, 2) == full[-2:]        # 尾部 n 条
    assert controller.get_activity_tail(tid, 999) == full           # n >= len 全量
    assert controller.get_activity_tail(tid, 0) == ()               # 越界安全
    assert controller.get_activity_tail(tid, -1) == ()
    # 快照解耦：取快照后再写入，不影响已取快照；新快照可见新事件。
    handler.on_tool_start({"name": "t"}, "arg-3", run_id=uuid4(), metadata=None)
    assert len(full) == 3
    assert len(controller.get_activity_tail(tid)) == 4


def test_cp423_unknown_thread_empty_tuple_and_readonly(controller_spy):
    """未知 thread → 空 tuple；只读方法**不建 handler**（不污染写侧字典）。"""
    controller, _spy = controller_spy
    assert controller.get_activity_tail("no-such-thread") == ()
    assert controller.get_activity_tail("no-such-thread", 5) == ()
    assert "no-such-thread" not in controller._activity_handlers, (
        "读侧不得 get-or-create（只读契约）")


# ---------------------------------------------------------------------------
# CP-4.2-4 事件不进 checkpoint/state（AC-S5-14）+ react_base 零耦合
# ---------------------------------------------------------------------------


def test_cp424_events_absent_from_checkpoint_and_state(monkeypatch, tmp_path):
    """跑完 mock 流程后：state 快照序列化与 checkpoint DB（含 WAL/SHM）字节内容
    均无活动流事件痕迹（"⏺" 前缀 + 完整事件 text 双判据）。"""
    controller, tid, db_path, _runner = _run_controller_e2e(monkeypatch, tmp_path)

    events = controller.get_activity_tail(tid)
    tool_events = [e for e in events if e["kind"] == "tool"]
    assert tool_events, "无 tool 事件（泄漏断言将空泛，先失败）"

    # --- state 快照序列化无事件痕迹 ---
    final_state = controller.poll_state(tid)
    assert final_state, "state 快照缺失（装配失败，断言空泛）"
    state_json = json.dumps(final_state, ensure_ascii=False, default=str)
    assert _TOOL_MARKER not in state_json, "活动流事件文本泄漏进 state 快照"
    for e in tool_events:
        assert e["text"] not in state_json

    # --- checkpoint DB 字节 grep 无事件痕迹（真实 SqliteSaver 临时库）---
    blob = b""
    for suffix in ("", "-wal", "-shm"):
        sidecar = Path(str(db_path) + suffix)
        if sidecar.exists():
            blob += sidecar.read_bytes()
    assert blob, "checkpoint DB 未落盘（装配失败，断言空泛）"
    # 非空泛自证：真实 state 内容（arxiv_id）确已入库，只是不含活动流事件。
    assert spk1.PAPER_ARXIV_ID.encode("utf-8") in blob
    assert _TOOL_MARKER.encode("utf-8") not in blob, (
        "活动流事件文本泄漏进 checkpoint DB")
    for e in tool_events:
        assert e["text"].encode("utf-8") not in blob


def test_cp424_react_base_zero_coupling_with_activity_stream():
    """主路径红线的结构化守门：react_base 与活动流零耦合（零字节改动的常驻代理断言；
    工作区 `git diff HEAD -- core/react_base.py` 为空由任务收口记录实证）。"""
    react_src = (PROJECT_ROOT / "core" / "react_base.py").read_text(encoding="utf-8")
    assert "activity_stream" not in react_src, (
        "react_base 不得出现活动流耦合（架构 sprint5 §4 方案 C 主路径红线）")
    assert _TOOL_MARKER not in react_src
    # 消费面唯一落点在 app.py（GraphController 持 handler + 注入 + 只读快照）。
    app_src = (PROJECT_ROOT / "app.py").read_text(encoding="utf-8")
    assert "from core.activity_stream import" in app_src
