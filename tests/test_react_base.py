"""B4 - react_base Prompt Cache 子任务单测。

覆盖 Prompt Cache 方案 A 前缀稳定化（参见架构文档 §2.6.6 / 技术架构文档 §10.5）：
- _make_react_wrapper 在连续两轮不同动态输入下，初始 SystemMessage.content 字节级一致
- _truncate_tool_result 对超长输入产生字节级幂等且不含动态片段（ISO 时间戳 / UUID / /tmp/ 等）
"""
from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from langchain_core.messages import HumanMessage, SystemMessage


FIXED_SYSTEM_PROMPT = (
    "You are paper_analysis ReAct agent. Read sections via tools and emit "
    "<result>{json}</result> when ready."
)


def _make_global_state(user_input: str, arxiv_id: str) -> Dict[str, Any]:
    """构造一个伪 GlobalState，仅含 wrapper 所需字段。"""
    return {
        "user_input": user_input,
        "arxiv_id": arxiv_id,
        "retry_budget_remaining": 50,
        "llm_config": {
            "base_url": "https://example.test/v1",
            "model": "test-model",
            "api_key": "sk-test",
            "temperature": 0.3,
            "max_tokens": 1024,
        },
    }


# ---------- 用例 1：SystemMessage.content 字节级一致 ----------

def test_system_message_content_byte_stable_across_runs(monkeypatch):
    """连续两轮调用，输入不同 user_input / arxiv_id，SystemMessage.content 字节级一致。"""
    from core import react_base

    captured_initials: List[Dict[str, Any]] = []

    class FakeSubgraph:
        def invoke(self, initial):
            captured_initials.append(initial)
            return {
                "messages": initial["messages"],
                "round": 1,
                "max_rounds": initial["max_rounds"],
                "status": "done",
                "result": {"ok": True},
                "context": initial["context"],
            }

    def fake_create_react_subgraph(**kwargs):
        return FakeSubgraph()

    def fake_create_llm(config):
        return object()  # 不会真正调用

    monkeypatch.setattr(react_base, "create_react_subgraph", fake_create_react_subgraph)
    monkeypatch.setattr(react_base, "create_llm", fake_create_llm)

    def build_context(state):
        # 故意把 arxiv_id / user_input 等动态变量放到 context，
        # wrapper 必须把这些放入 HumanMessage，不得污染 SystemMessage。
        return {
            "user_input": state["user_input"],
            "arxiv_id": state["arxiv_id"],
        }

    def build_system_prompt(context):
        # 固定模板，不依赖 context 中的动态字段
        return FIXED_SYSTEM_PROMPT

    def get_tools(state):
        return []

    def map_result(result, state):
        return {"some_field": result}

    wrapper = react_base._make_react_wrapper(
        node_name="paper_analysis",
        build_context=build_context,
        build_system_prompt=build_system_prompt,
        get_tools=get_tools,
        map_result=map_result,
        max_rounds=12,
    )

    state_a = _make_global_state(user_input="reproduce paper A", arxiv_id="2401.00001")
    state_b = _make_global_state(user_input="reproduce paper B totally different", arxiv_id="2502.99999")

    wrapper(state_a)
    wrapper(state_b)

    assert len(captured_initials) == 2

    sys_msgs = []
    human_msgs = []
    for initial in captured_initials:
        msgs = initial["messages"]
        assert isinstance(msgs[0], SystemMessage), "第一条必须是 SystemMessage"
        assert isinstance(msgs[1], HumanMessage), "第二条必须是 HumanMessage（携带动态上下文）"
        sys_msgs.append(msgs[0].content)
        human_msgs.append(msgs[1].content)

    # SystemMessage.content 必须字节级一致
    assert sys_msgs[0] == sys_msgs[1]
    assert hashlib.sha256(sys_msgs[0].encode("utf-8")).hexdigest() == \
           hashlib.sha256(sys_msgs[1].encode("utf-8")).hexdigest()

    # HumanMessage 应包含动态变量（验证动态变量被正确外移到 HumanMessage）
    assert "2401.00001" in human_msgs[0]
    assert "2502.99999" in human_msgs[1]
    # SystemMessage 中不应出现任一 arxiv_id
    assert "2401.00001" not in sys_msgs[0]
    assert "2502.99999" not in sys_msgs[0]


# ---------- 用例 2：工具结果截断幂等且不含动态片段 ----------

def test_truncate_tool_result_idempotent_and_no_dynamic_fragments():
    from core import react_base
    from config import TOOL_RESULT_MAX_LENGTH

    # 构造一个超长输入（远超 TOOL_RESULT_MAX_LENGTH=8000）
    long_text = "abcdefghij" * 2000  # 20000 chars
    assert len(long_text) > TOOL_RESULT_MAX_LENGTH

    out1 = react_base._truncate_tool_result(long_text)
    out2 = react_base._truncate_tool_result(long_text)

    # 字节级幂等
    assert out1 == out2
    assert hashlib.sha256(out1.encode("utf-8")).hexdigest() == \
           hashlib.sha256(out2.encode("utf-8")).hexdigest()

    # 输出长度应严格小于等于 limit
    assert len(out1) <= TOOL_RESULT_MAX_LENGTH

    # 不得含 ISO 时间戳 (YYYY-MM-DDTHH:MM:SS 或 YYYY-MM-DD HH:MM:SS 风格)
    iso_pattern = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}")
    assert not iso_pattern.search(out1), f"截断结果包含 ISO 时间戳: {out1[-200:]!r}"

    # 不得含 UUID
    uuid_pattern = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
    assert not uuid_pattern.search(out1), f"截断结果包含 UUID: {out1[-200:]!r}"

    # 不得含临时路径前缀
    assert "/tmp/" not in out1
    assert "/var/tmp/" not in out1

    # 不得含 epoch 时间戳样式的大整数（10-13 位的孤立数字）
    epoch_pattern = re.compile(r"\b1[6-9]\d{8,11}\b")
    assert not epoch_pattern.search(out1), f"截断结果疑似包含 epoch 时间戳: {out1[-200:]!r}"


def test_truncate_tool_result_uses_fixed_marker():
    """截断标记应是固定字符串（不含 len(text) 等输入相关的动态值）。"""
    from core import react_base
    from config import TOOL_RESULT_MAX_LENGTH

    # 两个不同长度但都触发截断的输入
    text_a = "A" * (TOOL_RESULT_MAX_LENGTH + 1000)
    text_b = "B" * (TOOL_RESULT_MAX_LENGTH + 5000)

    out_a = react_base._truncate_tool_result(text_a)
    out_b = react_base._truncate_tool_result(text_b)

    # 两次截断的"尾巴标记"必须相同（不能依赖输入长度）
    marker = f"... [truncated at {TOOL_RESULT_MAX_LENGTH} chars]"
    assert out_a.endswith(marker), f"截断标记不符: {out_a[-100:]!r}"
    assert out_b.endswith(marker), f"截断标记不符: {out_b[-100:]!r}"


def test_truncate_tool_result_short_input_unchanged():
    """短输入应原样返回，不触发截断。"""
    from core import react_base

    short = "hello world"
    assert react_base._truncate_tool_result(short) == short
