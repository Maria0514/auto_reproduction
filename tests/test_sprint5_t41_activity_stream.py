"""Sprint 5 批次 4 / T-S5-4-1：core/activity_stream.py 自测（CP-4.1-1 ~ CP-4.1-4）。

覆盖（dev-plan §批次 4 任务 T-S5-4-1 自测检查点）：
    - CP-4.1-1 事件格式：两采集点各产出 5 字段合规事件、text 截断（≤120/≤160）、
      seq 单调、kind 两值；附带固化 llm 空文本轮次跳过 + snapshot_tail 快照语义。
    - CP-4.1-2 封顶：写入 MAX+1 条 → 恰保留最新 MAX 条（AC-S5-14 封顶单测）。
    - CP-4.1-3 脱敏：哨兵 token 注入工具参数 / LLM 输出 → text 无明文（脱敏出口①，
      register_sensitive_value 注册哨兵假值走真实 mask_value 链路）。
    - CP-4.1-4 handler 内部异常吞掉 + WARNING、主流程不炸；node 归属链
      （checkpoint_ns 前缀恢复 / langgraph_node 回退 / 取不到回填 ""）逐路证。

纯单元测试：直接手动触发 callbacks（spike 已实证真图传播链路，本文件不重复跑图）。
零 LLM/deepxiv 配额、零网络外呼，不标 e2e marker，默认收集运行。
"""
from __future__ import annotations

import logging
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import config  # noqa: E402
from core import secrets_store  # noqa: E402
from core.activity_stream import (  # noqa: E402
    ActivityEvent,
    ActivityStreamHandler,
    new_event_deque,
    snapshot_tail,
)
from core.secrets_store import register_sensitive_value  # noqa: E402

_EVENT_FIELDS = {"seq", "ts", "node", "kind", "text"}
_SENTINEL = "SENTINEL-FAKE-TOKEN-a1b2c3d4e5f6"  # 哨兵假值，非任何真实凭证


# ---------------------------------------------------------------------------
# fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolated_secrets(monkeypatch):
    """隔离进程内 sensitive set 与 .secrets 文件读取（确定性 + 不污染其他测试）。"""
    monkeypatch.setattr(secrets_store, "_SENSITIVE_VALUES", set())
    monkeypatch.setattr(secrets_store, "_read_entries", lambda *a, **k: {})


def _md(ns: Optional[str] = None, node: Optional[str] = None) -> Dict[str, Any]:
    md: Dict[str, Any] = {}
    if ns is not None:
        md["checkpoint_ns"] = ns
    if node is not None:
        md["langgraph_node"] = node
    return md


def _fire_tool(handler: ActivityStreamHandler, *, name: str = "run_in_sandbox",
               inputs: Optional[Dict[str, Any]] = None, input_str: str = "",
               metadata: Optional[Dict[str, Any]] = None,
               serialized: Any = None) -> None:
    handler.on_tool_start(
        serialized if serialized is not None else {"name": name},
        input_str, run_id=uuid4(), metadata=metadata, inputs=inputs)


def _fire_llm(handler: ActivityStreamHandler, text: str,
              metadata: Optional[Dict[str, Any]] = None,
              cache_metadata: bool = True) -> None:
    """模拟一次 LLM 往返：on_chat_model_start（缓存 metadata）→ on_llm_end。"""
    run_id = uuid4()
    if cache_metadata:
        handler.on_chat_model_start({}, [[]], run_id=run_id, metadata=metadata)
    response = LLMResult(
        generations=[[ChatGeneration(message=AIMessage(content=text))]])
    handler.on_llm_end(response, run_id=run_id)


# ---------------------------------------------------------------------------
# CP-4.1-1 事件格式
# ---------------------------------------------------------------------------


def test_cp411_tool_event_schema_and_truncation():
    """on_tool_start → 5 字段合规 tool 事件；长参数摘要整体截断 ≤120。"""
    handler = ActivityStreamHandler()
    before = time.time()
    _fire_tool(handler, inputs={"command": "python train.py " + "x" * 300},
               metadata=_md(ns="execution:uuid-1", node="tool_executor"))
    after = time.time()

    events = snapshot_tail(handler.events)
    assert len(events) == 1
    e = events[0]
    assert set(e.keys()) == _EVENT_FIELDS, f"事件字段集不合规: {set(e.keys())}"
    assert e["kind"] == "tool"
    assert e["node"] == "execution"
    assert isinstance(e["seq"], int) and e["seq"] == 1
    assert isinstance(e["ts"], float) and before <= e["ts"] <= after
    assert isinstance(e["text"], str) and 0 < len(e["text"]) <= 120
    assert e["text"].startswith("⏺ run_in_sandbox(")
    assert "\n" not in e["text"], "text 须为单行压缩摘要"


def test_cp411_tool_event_input_str_fallback():
    """inputs 缺失（非 dict）时回退 input_str 做参数摘要。"""
    handler = ActivityStreamHandler()
    _fire_tool(handler, name="write_code_file", inputs=None,
               input_str="{'path': 'train.py'}", metadata=_md(ns="coding:u"))
    (e,) = snapshot_tail(handler.events)
    assert e["kind"] == "tool"
    assert e["text"] == "⏺ write_code_file({'path': 'train.py'})"


def test_cp411_llm_event_schema_and_truncation():
    """on_llm_end → 5 字段合规 llm 事件；输出预览截断 ≤160；node 经 run_id 回查。"""
    handler = ActivityStreamHandler()
    long_text = '<result>{"entry_script": "train.py"}' + "y" * 400
    _fire_llm(handler, long_text, metadata=_md(ns="coding:uuid-2", node="reasoning"))

    events = snapshot_tail(handler.events)
    assert len(events) == 1
    e = events[0]
    assert set(e.keys()) == _EVENT_FIELDS
    assert e["kind"] == "llm"
    assert e["node"] == "coding", "on_llm_end 无 metadata，须经 *start 缓存回查归属"
    assert 0 < len(e["text"]) <= 160
    assert e["text"].startswith("<result>")


def test_cp411_llm_multiline_compressed_to_single_line():
    """多行 LLM 输出压缩为单行（换行/连续空白 → 单空格）。"""
    handler = ActivityStreamHandler()
    _fire_llm(handler, "line1\nline2\n\n  line3", metadata=_md(ns="coding:u"))
    (e,) = snapshot_tail(handler.events)
    assert e["text"] == "line1 line2 line3"


def test_cp411_llm_empty_text_skipped():
    """tool_call 轮 content=='' → 跳过不产事件（spike 实证 3 落地口径）。"""
    handler = ActivityStreamHandler()
    _fire_llm(handler, "", metadata=_md(ns="coding:u"))
    assert snapshot_tail(handler.events) == ()


def test_cp411_seq_monotonic_and_kind_values():
    """seq 单调递增；kind 恒 ∈ {"tool", "llm"} 两值。"""
    handler = ActivityStreamHandler()
    for i in range(5):
        _fire_tool(handler, inputs={"command": f"step-{i}"},
                   metadata=_md(ns="execution:u"))
        _fire_llm(handler, f"output-{i}", metadata=_md(ns="execution:u"))

    events = snapshot_tail(handler.events)
    assert len(events) == 10
    seqs = [e["seq"] for e in events]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs), (
        f"seq 须严格单调递增: {seqs}")
    assert {e["kind"] for e in events} == {"tool", "llm"}


def test_cp411_snapshot_tail_semantics():
    """snapshot_tail：tuple 不可变快照、n 越界安全（T-S5-4-2 消费前提）。"""
    handler = ActivityStreamHandler()
    for i in range(3):
        _fire_tool(handler, inputs={"i": i}, metadata=_md(ns="coding:u"))

    full = snapshot_tail(handler.events)
    assert isinstance(full, tuple) and len(full) == 3
    assert snapshot_tail(handler.events, None) == full
    assert snapshot_tail(handler.events, 2) == full[-2:]
    assert snapshot_tail(handler.events, 999) == full  # n >= len → 全量
    assert snapshot_tail(handler.events, 0) == ()
    assert snapshot_tail(handler.events, -1) == ()
    # 快照与容器解耦：快照后再写入不影响已取快照
    _fire_tool(handler, inputs={"i": 3}, metadata=_md(ns="coding:u"))
    assert len(full) == 3


# ---------------------------------------------------------------------------
# CP-4.1-2 封顶（AC-S5-14 封顶单测）
# ---------------------------------------------------------------------------


def test_cp412_deque_cap_keeps_latest_max():
    """写入 MAX+1 条 → 恰保留最新 MAX 条（最老 seq=1 被挤出）。"""
    max_events = config.ACTIVITY_STREAM_MAX_EVENTS
    handler = ActivityStreamHandler()
    for i in range(max_events + 1):
        _fire_tool(handler, inputs={"i": i}, metadata=_md(ns="execution:u"))

    events = snapshot_tail(handler.events)
    assert len(events) == max_events, (
        f"封顶失效: {len(events)} != {max_events}")
    assert events[0]["seq"] == 2, "最老一条（seq=1）应被原子挤出"
    assert events[-1]["seq"] == max_events + 1
    assert handler.events.maxlen == max_events


def test_cp412_new_event_deque_maxlen_from_config():
    """模块级容器函数 maxlen 绑定 config.ACTIVITY_STREAM_MAX_EVENTS。"""
    assert new_event_deque().maxlen == config.ACTIVITY_STREAM_MAX_EVENTS


# ---------------------------------------------------------------------------
# CP-4.1-3 脱敏（脱敏出口①）
# ---------------------------------------------------------------------------


def test_cp413_tool_args_masked():
    """工具参数内嵌哨兵 token（run_in_sandbox 命令行场景）→ text 无明文。"""
    register_sensitive_value(_SENTINEL)
    handler = ActivityStreamHandler()
    _fire_tool(handler, inputs={"command": f"git clone https://x:{_SENTINEL}@host/r"},
               metadata=_md(ns="execution:u"))

    (e,) = snapshot_tail(handler.events)
    assert _SENTINEL not in e["text"], f"哨兵明文泄漏: {e['text']}"
    assert "****" in e["text"], "应有 mask 占位符替换痕迹"


def test_cp413_llm_output_masked():
    """LLM 输出内嵌哨兵 token → text 无明文。"""
    register_sensitive_value(_SENTINEL)
    handler = ActivityStreamHandler()
    _fire_llm(handler, f"已配置凭证 {_SENTINEL} 完成", metadata=_md(ns="coding:u"))

    (e,) = snapshot_tail(handler.events)
    assert _SENTINEL not in e["text"], f"哨兵明文泄漏: {e['text']}"
    assert "****" in e["text"]


def test_cp413_mask_before_truncation_no_half_leak():
    """先 mask 再截断：哨兵横跨截断线也不得残留半截明文（顺序契约）。"""
    register_sensitive_value(_SENTINEL)
    handler = ActivityStreamHandler()
    # 哨兵起点落在 110 附近，若"先截断后 mask"则截出半截哨兵且 mask 匹配不上
    prefix = "a" * 105
    _fire_tool(handler, inputs=None, input_str=f"{prefix}{_SENTINEL}tail",
               metadata=_md(ns="execution:u"))

    (e,) = snapshot_tail(handler.events)
    assert _SENTINEL not in e["text"]
    assert _SENTINEL[: len(_SENTINEL) // 2] not in e["text"], (
        f"半截哨兵明文残留（截断先于 mask？）: {e['text']}")
    assert len(e["text"]) <= 120


# ---------------------------------------------------------------------------
# CP-4.1-4 异常吞掉 + WARNING；node 归属链
# ---------------------------------------------------------------------------


def test_cp414_node_from_checkpoint_ns_prefix():
    """checkpoint_ns='coding:<uuid>' → 前缀恢复外层节点名 'coding'。"""
    handler = ActivityStreamHandler()
    _fire_tool(handler, metadata=_md(ns="coding:af31-uuid", node="tool_executor"))
    (e,) = snapshot_tail(handler.events)
    assert e["node"] == "coding", "checkpoint_ns 前缀优先于 langgraph_node"


def test_cp414_node_fallback_langgraph_node():
    """checkpoint_ns 取不到 → 回退 langgraph_node（内层子图节点名）。"""
    handler = ActivityStreamHandler()
    _fire_tool(handler, metadata=_md(node="tool_executor"))       # 无 checkpoint_ns
    _fire_tool(handler, metadata=_md(ns="", node="reasoning"))    # 空串 checkpoint_ns
    events = snapshot_tail(handler.events)
    assert events[0]["node"] == "tool_executor"
    assert events[1]["node"] == "reasoning"


def test_cp414_node_missing_backfills_empty():
    """metadata 缺失（None / {} / 两键皆无）→ node 回填 ''，不炸。"""
    handler = ActivityStreamHandler()
    _fire_tool(handler, metadata=None)
    _fire_tool(handler, metadata={})
    _fire_llm(handler, "no-metadata-cached", cache_metadata=False)  # run_id 回查 miss
    events = snapshot_tail(handler.events)
    assert len(events) == 3
    assert all(e["node"] == "" for e in events)


def test_cp414_handler_exception_swallowed_with_warning(caplog):
    """回调内部异常 → 吞掉 + WARNING、主流程不炸、不产半残事件。"""

    class BoomResponse:
        @property
        def generations(self):
            raise RuntimeError("boom-llm-end")

    handler = ActivityStreamHandler()
    with caplog.at_level(logging.WARNING, logger="core.activity_stream"):
        # on_llm_end：response.generations 访问即炸
        handler.on_llm_end(BoomResponse(), run_id=uuid4())
        # on_tool_start：serialized 无 .get（真值非 dict）→ AttributeError
        handler.on_tool_start(object(), "x", run_id=uuid4())
        # on_chat_model_start：metadata 不可 dict 化 → TypeError
        handler.on_chat_model_start({}, [[]], run_id=uuid4(), metadata=42)

    assert snapshot_tail(handler.events) == (), "异常路径不得产出半残事件"
    warned = [r for r in caplog.records
              if r.levelno == logging.WARNING and "已吞掉" in r.getMessage()]
    assert len(warned) == 3, (
        f"三处异常各须一条 WARNING: {[r.getMessage() for r in caplog.records]}")


def test_cp414_exception_then_recovery_seq_still_monotonic():
    """异常事件穿插后 handler 继续可用，seq 仍单调（主图不受影响的可用性面）。"""
    handler = ActivityStreamHandler()
    _fire_tool(handler, inputs={"i": 1}, metadata=_md(ns="coding:u"))
    handler.on_tool_start(object(), "x", run_id=uuid4())  # 异常吞掉
    _fire_llm(handler, "recovered", metadata=_md(ns="coding:u"))

    events = snapshot_tail(handler.events)
    assert [e["kind"] for e in events] == ["tool", "llm"]
    seqs = [e["seq"] for e in events]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)


def test_cp414_events_are_plain_dicts_not_in_state():
    """事件为纯内存 dict（ActivityEvent TypedDict 运行时即 dict），零 state 依赖。"""
    handler = ActivityStreamHandler()
    _fire_tool(handler, inputs={"i": 1}, metadata=_md(ns="coding:u"))
    (e,) = snapshot_tail(handler.events)
    assert type(e) is dict  # TypedDict 运行时形态
    # 模块公开面自检：不引入 state/checkpoint 依赖（AC-S5-14 三个"不"侧写）
    import core.activity_stream as mod
    src = Path(mod.__file__).read_text(encoding="utf-8")
    assert "core.state" not in src and "checkpointer" not in src
    assert isinstance(ActivityEvent.__annotations__, dict)  # schema 5 字段
    assert set(ActivityEvent.__annotations__) == _EVENT_FIELDS
