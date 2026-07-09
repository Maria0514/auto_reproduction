"""Sprint 5 S5-07：agent 活动流——事件 schema + callbacks handler + per-thread deque。

架构依据（docs/sprint5/architecture.md §4 Q-S5-8 全量裁决 / §9.3 出口①）：

- 顶层 ``graph.invoke(state, {**config, "callbacks": [handler]})`` 注入的 callbacks
  经 langchain-core ``var_child_runnable_config`` contextvar 自动穿透节点内手动
  ``subgraph.invoke`` 边界（T-S5-0-1 spike 已实证：coding / execution 两路径均传播，
  react_base **零字节改动**，见 test-reports/2026-07-09_spk1-callbacks-propagation.md）。
- 采集点恰两个（不做事件类型枚举体系）：
    * ``on_tool_start`` → kind="tool"，text = "⏺ 工具名(参数摘要)"（整体 ≤120 字符）；
    * ``on_llm_end``   → kind="llm"，text = 模型输出截断预览（≤160 字符；空文本轮次
      即 tool_call 轮 content=="" 跳过不产事件，spike 报告实证 3 的落地口径）。
- **node 归属（spike 实证 1b 落地）**：``metadata["langgraph_node"]`` 取到的是内层
  子图节点名（reasoning / tool_executor），外层主图节点名须从
  ``metadata["checkpoint_ns"]`` 前缀恢复（``"coding:<uuid>"`` → ``split(":")[0]``），
  取不到再回退 ``langgraph_node``，仍无则回填 ""。
  ``on_llm_end`` 本身不携带 metadata（langchain callbacks 契约），node 归属须在
  ``on_chat_model_start`` 缓存 ``run_id → metadata`` 回查（spike 附带实证）。
- **脱敏出口①（§9.3）**：``text`` 生成即过 ``secrets_store.mask_value``
  （run_in_sandbox 命令行可能内嵌凭证），顺序为 先 mask → 再压缩单行 → 再截断
  （若先截断可能把敏感值切半导致 mask 匹配不上，残留半截明文）。
- **三个"不"（AC-S5-14 验收面）**：不持久化、不进 checkpoint、不进 state；
  纯内存生命周期，进程重启即失（可观测性尽力而为语义）。
- **线程安全（R-9）**：CPython GIL 下 ``deque.append`` 原子 + 读侧 ``tuple()`` 快照；
  极端竞态丢尾部若干行属尽力而为语义，可接受。
- handler 回调内部异常一律吞掉 + WARNING 日志，绝不打断主图执行。

per-thread 语义：GraphController（app.py，T-S5-4-2）按 thread_id get-or-create 一个
``ActivityStreamHandler`` 实例，每实例持有自己的 deque——per-thread deque 由
per-thread handler 自然达成，本模块不建 thread_id 注册表。
"""
from __future__ import annotations

import json
import logging
import re
import time
from collections import deque
from itertools import count
from typing import Any, Deque, Dict, Optional, Tuple, TypedDict

from langchain_core.callbacks import BaseCallbackHandler

from config import ACTIVITY_STREAM_MAX_EVENTS
from core.secrets_store import mask_value

logger = logging.getLogger(__name__)

# text 截断上限（架构 §4：tool 摘要 ≤120 / llm 输出预览 ≤160，含前缀整体计）
_TOOL_TEXT_LIMIT: int = 120
_LLM_TEXT_LIMIT: int = 160

_WS_RE = re.compile(r"\s+")


class ActivityEvent(TypedDict):
    """活动流事件（架构 §4 单一 TypedDict，5 字段，kind 不建 Enum）。"""

    seq: int    # 线程内单调递增序号（UI 增量渲染用）
    ts: float   # time.time()
    node: str   # 外层主图节点名（checkpoint_ns 前缀 → langgraph_node → ""）
    kind: str   # "tool" | "llm" 两值，字符串字面量
    text: str   # 单行压缩摘要，已过 mask_value 脱敏


# ---------------------------------------------------------------------------
# 模块级容器函数（per-thread deque + 快照读）
# ---------------------------------------------------------------------------

def new_event_deque() -> Deque[ActivityEvent]:
    """新建 per-thread 事件容器：``deque(maxlen=ACTIVITY_STREAM_MAX_EVENTS)``。

    封顶语义（AC-S5-14）：写满后原子挤出最老事件，恰保留最新 MAX 条。
    """
    return deque(maxlen=ACTIVITY_STREAM_MAX_EVENTS)


def snapshot_tail(
    events: Deque[ActivityEvent], n: Optional[int] = None,
) -> Tuple[ActivityEvent, ...]:
    """``tuple()`` 快照读（R-9 线程安全读侧；T-S5-4-2 get_activity_tail 消费）。

    - ``n=None`` → 全量快照；
    - ``n`` 越界安全：``n <= 0`` 返回空 tuple，``n >= len`` 返回全量；
    - 返回不可变 tuple，UI 侧只读。
    """
    snap = tuple(events)
    if n is None or n >= len(snap):
        return snap
    if n <= 0:
        return ()
    return snap[-n:]


# ---------------------------------------------------------------------------
# 内部 helpers
# ---------------------------------------------------------------------------

def _node_from_metadata(metadata: Optional[Dict[str, Any]]) -> str:
    """外层主图节点名恢复链（spike 实证 1b）。

    优先 ``checkpoint_ns`` 前缀（``"coding:<uuid>"`` → ``"coding"``），
    回退 ``langgraph_node``（内层子图节点名，聊胜于无），再取不到回填 ""。
    """
    if not metadata:
        return ""
    ns = str(metadata.get("checkpoint_ns") or "")
    if ns:
        return ns.split(":")[0]
    return str(metadata.get("langgraph_node") or "")


def _compose_text(raw: str, limit: int) -> str:
    """text 生成三步：先 mask_value 脱敏 → 再压缩单行 → 再截断 ≤limit。

    顺序不可换：截断在前可能把敏感值切半，mask_value 子串匹配失败 → 明文残留。
    """
    masked = mask_value(raw) or ""
    one_line = _WS_RE.sub(" ", masked).strip()
    return one_line[:limit]


def _llm_output_text(response: Any) -> str:
    """从 LLMResult 提取首条生成文本（spike 实证 3 的取法，防御式）。"""
    generations = getattr(response, "generations", None) or []
    first = generations[0][0] if generations and generations[0] else None
    if first is None:
        return ""
    message = getattr(first, "message", None)
    content = message.content if message is not None else getattr(first, "text", "")
    if isinstance(content, str):
        return content
    return str(content) if content else ""


# ---------------------------------------------------------------------------
# ActivityStreamHandler
# ---------------------------------------------------------------------------

class ActivityStreamHandler(BaseCallbackHandler):
    """per-thread 活动流采集 handler（每 thread_id 一个实例，由 app.py 侧持有）。

    产事件的采集点恰两个：``on_tool_start`` / ``on_llm_end``；
    ``on_chat_model_start``（及 ``on_llm_start`` 防御兜底）仅缓存
    ``run_id → metadata`` 供 ``on_llm_end`` 回查 node 归属，不产事件。
    所有回调 body 整体 try/except：异常吞掉 + WARNING，绝不打断主图执行。
    """

    def __init__(self, events: Optional[Deque[ActivityEvent]] = None) -> None:
        self.events: Deque[ActivityEvent] = (
            events if events is not None else new_event_deque()
        )
        self._seq = count(1)
        self._run_metadata: Dict[str, Optional[Dict[str, Any]]] = {}

    # ---- 写入 ----

    def _append(self, node: str, kind: str, text: str) -> None:
        event: ActivityEvent = {
            "seq": next(self._seq),
            "ts": time.time(),
            "node": node,
            "kind": kind,
            "text": text,
        }
        self.events.append(event)  # deque(maxlen) 原子 append（R-9）

    # ---- metadata 缓存（不产事件） ----

    def on_chat_model_start(  # noqa: D102
        self, serialized: Any, messages: Any, *, run_id: Any,
        parent_run_id: Any = None, tags: Any = None,
        metadata: Optional[Dict[str, Any]] = None, **kwargs: Any,
    ) -> None:
        try:
            self._run_metadata[str(run_id)] = dict(metadata) if metadata else None
        except Exception as exc:  # noqa: BLE001 - 吞掉，绝不打断主图
            logger.warning("活动流 on_chat_model_start 异常已吞掉: %r", exc)

    def on_llm_start(  # noqa: D102 - 防御兜底（chat model 正常走 on_chat_model_start）
        self, serialized: Any, prompts: Any, *, run_id: Any,
        parent_run_id: Any = None, tags: Any = None,
        metadata: Optional[Dict[str, Any]] = None, **kwargs: Any,
    ) -> None:
        try:
            self._run_metadata[str(run_id)] = dict(metadata) if metadata else None
        except Exception as exc:  # noqa: BLE001
            logger.warning("活动流 on_llm_start 异常已吞掉: %r", exc)

    # ---- 采集点 1：on_tool_start → kind="tool" ----

    def on_tool_start(  # noqa: D102
        self, serialized: Any, input_str: Any, *, run_id: Any,
        parent_run_id: Any = None, tags: Any = None,
        metadata: Optional[Dict[str, Any]] = None,
        inputs: Optional[Dict[str, Any]] = None, **kwargs: Any,
    ) -> None:
        try:
            name = (serialized or {}).get("name") or kwargs.get("name") or ""
            if isinstance(inputs, dict):
                args_repr = json.dumps(
                    inputs, ensure_ascii=False, sort_keys=True, default=str)
            else:
                args_repr = str(input_str)
            text = _compose_text(f"⏺ {name}({args_repr})", _TOOL_TEXT_LIMIT)
            self._append(_node_from_metadata(metadata), "tool", text)
        except Exception as exc:  # noqa: BLE001 - 吞掉，绝不打断主图
            logger.warning("活动流 on_tool_start 异常已吞掉: %r", exc)

    # ---- 采集点 2：on_llm_end → kind="llm" ----

    def on_llm_end(  # noqa: D102
        self, response: Any, *, run_id: Any, parent_run_id: Any = None,
        **kwargs: Any,
    ) -> None:
        try:
            # on_llm_end 不带 metadata（langchain 契约）→ run_id 回查 *start 缓存
            metadata = self._run_metadata.pop(str(run_id), None)
            text = _compose_text(_llm_output_text(response), _LLM_TEXT_LIMIT)
            if not text:  # tool_call 轮 content=="" → 跳过（spike 实证 3 口径）
                return
            self._append(_node_from_metadata(metadata), "llm", text)
        except Exception as exc:  # noqa: BLE001 - 吞掉，绝不打断主图
            logger.warning("活动流 on_llm_end 异常已吞掉: %r", exc)
