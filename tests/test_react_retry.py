"""ReAct 主循环重试接入回归测试（bug 修复：LLM 重试层未接入 ReAct 主循环，2026-07-02）。

覆盖点：
1. ``invoke_with_retry`` 单元语义：
   - 瞬态错误 N 次后成功，调用次数 == N+1，退避 sleep 次数与序列正确；
   - PermanentError（含 LLMAuthError）立刻抛出不重试，调用次数 == 1，不 sleep；
   - LLMRateLimitError 携带 retry_after 时优先使用该等待值；
   - 重试耗尽后抛 TransientError；
   - 重试全程不修改 / 不变形传入 messages（对象身份 + 内容双重断言，
     Prompt Cache 字节稳定性硬约束）。
2. ReAct 子图集成：
   - reasoning_node 热路径：瞬态错误重试后子图正常完成；
   - reasoning_node：PermanentError 直接冒泡杀掉子图（不重试）；
   - force_finish free-form 回退路径：瞬态错误重试后正常产出 result；
   - ``_invoke_with_schema`` 结构化降级路径：瞬态先重试再成功；
     PermanentError 不重试、helper 维持"绝不外抛"契约（降级返回 None）。

注意：所有涉及重试的用例通过 autouse fixture 将 ``time.sleep`` 替换为记录桩，
测试不真实等待。
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path
from typing import Any, List

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

# 保证测试能 import 项目根模块（pytest 默认 cwd 已在项目根）
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import REACT_RESULT_TAG_CLOSE, REACT_RESULT_TAG_OPEN  # noqa: E402
from core import llm_client  # noqa: E402
from core.errors import (  # noqa: E402
    LLMAuthError,
    LLMRateLimitError,
    PermanentError,
    TransientError,
)
from core.llm_client import invoke_with_retry  # noqa: E402
from core.react_base import (  # noqa: E402
    ReActState,
    _invoke_with_schema,
    create_react_subgraph,
)


# ---------- fixtures ----------


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """将 llm_client 重试循环里的 time.sleep 替换为记录桩，返回记录列表。"""
    sleeps: List[float] = []
    monkeypatch.setattr(llm_client.time, "sleep", lambda s: sleeps.append(s))
    return sleeps


# ---------- fakes ----------


class FakeLLM:
    """前 ``fail_times`` 次 invoke 抛 ``error_factory()``，之后返回 ``response``。

    记录每次收到的 input **引用**（不 copy），供"不修改 messages"断言使用。
    """

    def __init__(self, fail_times: int, response: Any, error_factory=None):
        self.fail_times = fail_times
        self.response = response
        self.error_factory = error_factory or (
            lambda: TransientError("upstream returned status_code 503")
        )
        self.calls: List[Any] = []

    def invoke(self, input: Any, **kwargs: Any) -> Any:  # noqa: A002
        self.calls.append(input)
        if len(self.calls) <= self.fail_times:
            raise self.error_factory()
        return self.response


class ScriptedLLM:
    """按脚本表驱动的 fake LLM：每次 invoke 弹出脚本头元素，Exception 则 raise。"""

    def __init__(self, script: List[Any]):
        self.script = list(script)
        self.calls: List[Any] = []

    def invoke(self, input: Any, **kwargs: Any) -> Any:  # noqa: A002
        self.calls.append(input)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class FakeStructuredCapableLLM:
    """支持 with_structured_output 的 fake：所有 method 档共用同一个 structured 桩。"""

    def __init__(self, structured_runnable: Any):
        self._structured = structured_runnable
        self.requested_methods: List[Any] = []

    def with_structured_output(self, schema: Any, method: Any = None, **kwargs: Any):
        self.requested_methods.append(method)
        return self._structured


def _result_message(payload: str) -> AIMessage:
    return AIMessage(
        content=f"{REACT_RESULT_TAG_OPEN}{payload}{REACT_RESULT_TAG_CLOSE}"
    )


def _initial_react_state(fake_llm: Any, max_rounds: int = 4) -> ReActState:
    return {
        "messages": [SystemMessage(content="s"), HumanMessage(content="go")],
        "round": 0,
        "max_rounds": max_rounds,
        "status": "reasoning",
        "result": None,
        "context": {"_llm": fake_llm},
    }


# ========== 1. invoke_with_retry 单元语义 ==========


def test_invoke_with_retry_transient_then_success(_no_sleep):
    """瞬态错误 2 次后成功：共调用 3 次，退避 sleep 序列为指数增长。"""
    fake = FakeLLM(fail_times=2, response=AIMessage(content="ok"))
    messages = [SystemMessage(content="s"), HumanMessage(content="q")]

    result = invoke_with_retry(fake, messages, max_retries=3, initial_delay=0.5)

    assert isinstance(result, AIMessage)
    assert result.content == "ok"
    assert len(fake.calls) == 3, "2 次失败 + 1 次成功，共 3 次调用"
    assert _no_sleep == [0.5, 1.0], "指数退避：0.5s → 1.0s"


def test_invoke_with_retry_permanent_raises_immediately(_no_sleep):
    """PermanentError 立刻抛出：不重试、不 sleep、仅调用 1 次。"""
    fake = FakeLLM(
        fail_times=99,
        response=AIMessage(content="never"),
        error_factory=lambda: PermanentError("config broken"),
    )

    with pytest.raises(PermanentError, match="config broken"):
        invoke_with_retry(fake, [HumanMessage(content="q")], max_retries=3)

    assert len(fake.calls) == 1, "PermanentError 不应触发任何重试"
    assert _no_sleep == [], "PermanentError 不应触发退避等待"


def test_invoke_with_retry_auth_error_classified_permanent(_no_sleep):
    """LLMAuthError（PermanentError 子类）同样立刻抛出不重试。"""
    fake = FakeLLM(
        fail_times=99,
        response=None,
        error_factory=lambda: LLMAuthError("401 unauthorized"),
    )

    with pytest.raises(LLMAuthError):
        invoke_with_retry(fake, [HumanMessage(content="q")], max_retries=3)

    assert len(fake.calls) == 1
    assert _no_sleep == []


def test_invoke_with_retry_rate_limit_uses_retry_after(_no_sleep):
    """LLMRateLimitError 携带 retry_after 时优先按该值等待。"""
    fake = FakeLLM(
        fail_times=1,
        response=AIMessage(content="ok"),
        error_factory=lambda: LLMRateLimitError("429 rate limit", retry_after=7.0),
    )

    result = invoke_with_retry(fake, [HumanMessage(content="q")], max_retries=2)

    assert result.content == "ok"
    assert len(fake.calls) == 2
    assert _no_sleep == [7.0], "应优先使用 Retry-After 值而非指数退避"


def test_invoke_with_retry_exhausted_raises_transient(_no_sleep):
    """瞬态错误持续发生：max_retries=2 → 共 3 次调用后抛 TransientError。"""
    fake = FakeLLM(fail_times=99, response=None)

    with pytest.raises(TransientError):
        invoke_with_retry(fake, [HumanMessage(content="q")], max_retries=2,
                          initial_delay=0.1)

    assert len(fake.calls) == 3, "1 次首调 + 2 次重试"
    assert len(_no_sleep) == 2


def test_invoke_with_retry_does_not_mutate_messages(_no_sleep):
    """重试全程不修改 / 不变形传入 messages（Prompt Cache 字节稳定性硬约束）。

    断言三层：
    - 每次重试收到的都是**同一个 list 对象**（不 copy 不重建）；
    - list 内元素对象身份不变（不替换消息对象）；
    - 消息内容与深拷贝快照逐字节相等（不追加 / 不改写 content）。
    """
    messages = [
        SystemMessage(content="fixed system prompt"),
        HumanMessage(content='{"arxiv_id": "2401.00001"}'),
    ]
    ids_before = [id(m) for m in messages]
    snapshot = copy.deepcopy(messages)

    fake = FakeLLM(fail_times=2, response=AIMessage(content="ok"))
    invoke_with_retry(fake, messages, max_retries=3, initial_delay=0.1)

    assert all(call is messages for call in fake.calls), \
        "每次重试必须重发同一个 messages 对象，不得 copy / 重建"
    assert [id(m) for m in messages] == ids_before, "消息对象身份不得改变"
    assert len(messages) == len(snapshot)
    for actual, expected in zip(messages, snapshot):
        assert type(actual) is type(expected)
        assert actual.content == expected.content, "消息内容不得被改写"


# ========== 2. ReAct 子图集成 ==========


def test_reasoning_node_retries_transient_then_completes(_no_sleep):
    """reasoning 热路径：首调抛瞬态错误，重试后成功产出 result，子图不炸。"""
    fake = FakeLLM(
        fail_times=1,
        response=_result_message('{"ok": true}'),
    )
    subgraph = create_react_subgraph(
        node_name="t_retry",
        system_prompt="s",
        tools=[],
        max_rounds=4,
    )

    final = subgraph.invoke(_initial_react_state(fake))

    assert final["result"] == {"ok": True}
    assert len(fake.calls) == 2, "1 次瞬态失败 + 1 次重试成功"
    assert len(_no_sleep) == 1


def test_reasoning_node_permanent_error_propagates(_no_sleep):
    """reasoning 热路径：PermanentError 不重试、直接冒泡出子图（既有异常语义）。"""
    fake = FakeLLM(
        fail_times=99,
        response=None,
        error_factory=lambda: LLMAuthError("401 unauthorized"),
    )
    subgraph = create_react_subgraph(
        node_name="t_perm",
        system_prompt="s",
        tools=[],
        max_rounds=4,
    )

    with pytest.raises(LLMAuthError):
        subgraph.invoke(_initial_react_state(fake))

    assert len(fake.calls) == 1, "PermanentError 不应重试"
    assert _no_sleep == []


def test_force_finish_freeform_retries_transient(_no_sleep):
    """force_finish free-form 回退路径（result_schema=None）经重试层。

    max_rounds=1：reasoning 一轮（无标签无 tool_calls）→ budget_exhausted →
    force_finish 首调抛瞬态 → 重试成功 → finalize 解析 result。
    """
    fake = ScriptedLLM([
        AIMessage(content="thinking, no result yet"),          # reasoning round 1
        TransientError("upstream 502 bad gateway"),            # force_finish 首调失败
        _result_message('{"done": true}'),                     # force_finish 重试成功
    ])
    subgraph = create_react_subgraph(
        node_name="t_ff",
        system_prompt="s",
        tools=[],
        max_rounds=1,
        result_schema=None,
    )

    final = subgraph.invoke(_initial_react_state(fake, max_rounds=1))

    assert final["result"] == {"done": True}
    assert len(fake.calls) == 3, "reasoning 1 次 + force_finish 失败 1 次 + 重试 1 次"
    assert len(_no_sleep) == 1


def test_invoke_with_schema_retries_transient(_no_sleep):
    """结构化降级路径：json_schema 档瞬态错误先重试（同档内），成功即返回。"""
    structured = FakeLLM(fail_times=1, response={"title": "x"})
    fake = FakeStructuredCapableLLM(structured)

    result = _invoke_with_schema(
        llm=fake,
        messages=[HumanMessage(content="q")],
        schema={"type": "object", "properties": {}, "title": "T"},
        node_name="t_schema",
    )

    assert result == {"title": "x"}
    assert len(structured.calls) == 2, "同档内：1 次瞬态失败 + 1 次重试成功"
    assert fake.requested_methods == ["json_schema"], "第一档成功后不应降级"
    assert len(_no_sleep) == 1


def test_invoke_with_schema_permanent_no_retry_and_no_raise(_no_sleep):
    """结构化降级路径：PermanentError 不重试（每档恰 1 次调用），
    且维持 helper "绝不外抛"契约——两档全失败后返回 None。"""
    structured = FakeLLM(
        fail_times=99,
        response=None,
        error_factory=lambda: PermanentError("model rejects schema"),
    )
    fake = FakeStructuredCapableLLM(structured)

    result = _invoke_with_schema(
        llm=fake,
        messages=[HumanMessage(content="q")],
        schema={"type": "object", "properties": {}, "title": "T"},
        node_name="t_schema_perm",
    )

    assert result is None, "两档全失败应返回 None 而非抛异常"
    assert fake.requested_methods == ["json_schema", "function_calling"]
    assert len(structured.calls) == 2, "PermanentError 每档只调 1 次，不重试"
    assert _no_sleep == []
