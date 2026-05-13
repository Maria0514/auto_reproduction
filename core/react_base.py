"""通用 ReAct 子图基础设施。

提供两个公开入口：

- ``create_react_subgraph()``：构建 ReAct 子图（reasoning → tool_executor →
  budget_check 循环，终态走 finalize → END，超预算走 force_finish → finalize → END）。
- ``_make_react_wrapper()``：将子图包装成一个 ``(GlobalState) -> dict`` 主图节点。

参考：sprint1/architecture.md 2.3、技术架构文档 3.2.1。
"""

from __future__ import annotations

import json
import logging
import operator
import re
from typing import Annotated, Any, Callable, Dict, List, Optional, Sequence, TypedDict

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.tools import BaseTool
from langgraph.graph import END, StateGraph

from config import (
    REACT_RESULT_TAG_CLOSE,
    REACT_RESULT_TAG_OPEN,
    TOOL_RESULT_MAX_LENGTH,
)
from core.errors import TransientError
from core.llm_client import create_llm
from core.state import GlobalState

logger = logging.getLogger(__name__)


# ========== 类型定义 ==========


class ReActState(TypedDict):
    """ReAct 子图内部状态，与 GlobalState 完全隔离。"""

    messages: Annotated[List[BaseMessage], operator.add]
    round: int
    max_rounds: int
    status: str  # "reasoning" | "tool_call" | "done" | "budget_exhausted"
    result: Optional[Dict[str, Any]]
    context: Dict[str, Any]


# ========== 辅助函数 ==========


def _truncate_tool_result(text: str, limit: int = TOOL_RESULT_MAX_LENGTH) -> str:
    """工具返回内容超过 limit 时截断并附加省略提示，防止上下文溢出。"""
    if len(text) <= limit:
        return text
    suffix = f"\n... [truncated, total {len(text)} chars]"
    head_len = max(0, limit - len(suffix))
    return text[:head_len] + suffix


def _stringify_tool_result(value: Any) -> str:
    """将任意工具返回值转为字符串以便写入 ToolMessage。"""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(value)


_RESULT_TAG_PATTERN = re.compile(
    re.escape(REACT_RESULT_TAG_OPEN) + r"(.*?)" + re.escape(REACT_RESULT_TAG_CLOSE),
    re.DOTALL,
)


def _extract_result_payload(text: str) -> Optional[str]:
    """从 LLM 输出中抓取 <result>...</result> 间的原始文本，未命中返回 None。"""
    if not text:
        return None
    match = _RESULT_TAG_PATTERN.search(text)
    if not match:
        return None
    return match.group(1).strip()


def _has_result_tag(text: str) -> bool:
    return _extract_result_payload(text) is not None


def _parse_result_payload(payload: str) -> Optional[Dict[str, Any]]:
    """从 <result> 内的文本解析 JSON。支持纯 JSON、```json ...``` 代码块、嵌套花括号。"""
    if not payload:
        return None

    stripped = payload.strip()

    # 1) ```json ... ``` / ``` ... ``` 代码块
    fence = re.search(r"```(?:json)?\s*\n?(.*?)```", stripped, re.DOTALL)
    if fence:
        candidate = fence.group(1).strip()
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass

    # 2) 直接是 JSON 对象
    if stripped.startswith("{"):
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            pass

    # 3) 兜底：抓第一对平衡的花括号
    start = stripped.find("{")
    if start == -1:
        return None
    depth = 0
    for i in range(start, len(stripped)):
        ch = stripped[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(stripped[start : i + 1])
                except json.JSONDecodeError:
                    return None
    return None


def _ai_message_text(msg: BaseMessage) -> str:
    """提取 AIMessage 文本内容（兼容 content 为 list[parts] 的情况）。"""
    content = getattr(msg, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for c in content:
            if isinstance(c, str):
                parts.append(c)
            elif isinstance(c, dict):
                # 兼容 anthropic / openai 工具风格的 content parts
                t = c.get("text") or c.get("content") or ""
                if isinstance(t, str):
                    parts.append(t)
        return "".join(parts)
    return str(content)


# ========== 工厂：create_react_subgraph ==========


def create_react_subgraph(
    node_name: str,
    system_prompt: str,
    tools: Sequence[BaseTool],
    max_rounds: int,
    result_schema: Optional[Dict[str, Any]] = None,
):
    """构建通用 ReAct 子图。

    所有 ReAct 节点复用此函数，根据传入的 system_prompt、工具集和最大轮次
    生成一个 CompiledStateGraph 子图。
    """

    # 子图依赖外部注入 LLM 实例（在 ReActState.context 中），这里准备工具映射。
    tool_map: Dict[str, BaseTool] = {t.name: t for t in tools}

    def _bind_llm(state: ReActState):
        """从 context 取出 LLM 并 bind_tools；未注入则报错。"""
        llm = state["context"].get("_llm")
        if llm is None:
            raise TransientError(
                f"[{node_name}] react subgraph missing _llm in context",
            )
        if tools:
            return llm.bind_tools(list(tools))
        return llm

    # ---------- 节点实现 ----------

    def reasoning_node(state: ReActState) -> dict:
        """调用 LLM 生成下一步行动，更新 status / round。"""
        llm = _bind_llm(state)
        response = llm.invoke(state["messages"])

        if not isinstance(response, AIMessage):
            # 容错：包装为 AIMessage
            response = AIMessage(content=str(getattr(response, "content", response)))

        next_round = state["round"] + 1
        tool_calls = getattr(response, "tool_calls", None) or []
        text = _ai_message_text(response)

        if tool_calls:
            status = "tool_call"
        elif _has_result_tag(text):
            status = "done"
        else:
            # 无工具调用也无 result 标签：交给 budget_check 兜底，下一轮继续推理
            status = "reasoning"

        logger.debug(
            "[%s] reasoning round=%d -> status=%s, tool_calls=%d",
            node_name, next_round, status, len(tool_calls),
        )
        return {
            "messages": [response],
            "round": next_round,
            "status": status,
        }

    def tool_executor_node(state: ReActState) -> dict:
        """执行 LLM 请求的工具调用，结果作为 ToolMessage 追加。"""
        # 最后一条 AIMessage 应当带 tool_calls
        last_ai: Optional[AIMessage] = None
        for msg in reversed(state["messages"]):
            if isinstance(msg, AIMessage):
                last_ai = msg
                break

        new_messages: List[BaseMessage] = []
        if last_ai is None or not getattr(last_ai, "tool_calls", None):
            logger.warning("[%s] tool_executor invoked but no tool_calls found", node_name)
            return {"messages": new_messages, "status": "reasoning"}

        for call in last_ai.tool_calls:
            tool_name = call.get("name") if isinstance(call, dict) else getattr(call, "name", None)
            tool_args = call.get("args") if isinstance(call, dict) else getattr(call, "args", {})
            tool_id = call.get("id") if isinstance(call, dict) else getattr(call, "id", None)

            if not tool_name or tool_name not in tool_map:
                err = f"unknown tool: {tool_name!r}"
                logger.warning("[%s] %s", node_name, err)
                new_messages.append(
                    ToolMessage(
                        content=err,
                        tool_call_id=tool_id or "",
                        name=tool_name or "unknown",
                    )
                )
                continue

            try:
                raw = tool_map[tool_name].invoke(tool_args or {})
                content = _truncate_tool_result(_stringify_tool_result(raw))
            except Exception as exc:  # noqa: BLE001 ReAct 容错的关键：不让工具异常杀掉子图
                content = f"tool {tool_name} raised {type(exc).__name__}: {exc}"
                logger.warning("[%s] tool %s error: %s", node_name, tool_name, exc)

            new_messages.append(
                ToolMessage(
                    content=content,
                    tool_call_id=tool_id or "",
                    name=tool_name,
                )
            )

        return {"messages": new_messages, "status": "reasoning"}

    def budget_check_node(state: ReActState) -> dict:
        """检查轮次预算是否耗尽。"""
        if state["round"] >= state["max_rounds"] - 1:
            logger.info(
                "[%s] budget exhausted: round=%d, max=%d",
                node_name, state["round"], state["max_rounds"],
            )
            return {"status": "budget_exhausted"}
        return {"status": "reasoning"}

    def force_finish_node(state: ReActState) -> dict:
        """预算耗尽：注入强制终止提示，再调一次 LLM 得到最终输出。"""
        instruction = SystemMessage(
            content=(
                "已达到最大推理轮次预算。请立刻基于当前已收集的信息，直接输出 "
                f"{REACT_RESULT_TAG_OPEN}{{JSON 结构化结果}}{REACT_RESULT_TAG_CLOSE} "
                "标签包裹的最终结果，不要再调用任何工具。若信息不足，请在 JSON 中"
                "使用空值或 null 字段占位。"
            )
        )
        # 强制最终输出阶段不再绑定工具，避免 LLM 再次发起 tool_call
        llm = state["context"].get("_llm")
        if llm is None:
            raise TransientError(
                f"[{node_name}] react subgraph missing _llm in context",
            )
        response = llm.invoke(state["messages"] + [instruction])
        if not isinstance(response, AIMessage):
            response = AIMessage(content=str(getattr(response, "content", response)))
        return {
            "messages": [instruction, response],
            "round": state["round"] + 1,
            "status": "done",
        }

    def finalize_node(state: ReActState) -> dict:
        """解析最后一条 AIMessage 中的 <result>JSON</result> 并写入 result。"""
        last_ai_text = ""
        for msg in reversed(state["messages"]):
            if isinstance(msg, AIMessage):
                last_ai_text = _ai_message_text(msg)
                break

        payload = _extract_result_payload(last_ai_text)
        if payload is None:
            logger.warning(
                "[%s] finalize: <result> tag not found in last AIMessage", node_name,
            )
            return {"result": {}, "status": "done"}

        parsed = _parse_result_payload(payload)
        if parsed is None:
            logger.warning(
                "[%s] finalize: failed to parse JSON inside <result> tag", node_name,
            )
            return {"result": {}, "status": "done"}

        return {"result": parsed, "status": "done"}

    def router(state: ReActState) -> str:
        status = state.get("status", "reasoning")
        if status == "tool_call":
            return "tool_executor"
        if status == "done":
            return "finalize"
        if status == "budget_exhausted":
            return "force_finish"
        return "budget_check"

    # ---------- 图编排 ----------

    graph = StateGraph(ReActState)
    graph.add_node("reasoning", reasoning_node)
    graph.add_node("tool_executor", tool_executor_node)
    graph.add_node("budget_check", budget_check_node)
    graph.add_node("force_finish", force_finish_node)
    graph.add_node("finalize", finalize_node)

    graph.set_entry_point("reasoning")

    graph.add_conditional_edges(
        "reasoning",
        router,
        {
            "tool_executor": "tool_executor",
            "finalize": "finalize",
            "force_finish": "force_finish",
            "budget_check": "budget_check",
        },
    )
    graph.add_edge("tool_executor", "budget_check")
    # budget_check 之后：根据 status 决定回 reasoning 还是去 force_finish
    graph.add_conditional_edges(
        "budget_check",
        lambda s: "force_finish" if s.get("status") == "budget_exhausted" else "reasoning",
        {
            "force_finish": "force_finish",
            "reasoning": "reasoning",
        },
    )
    graph.add_edge("force_finish", "finalize")
    graph.add_edge("finalize", END)

    return graph.compile()


# ========== 工厂：_make_react_wrapper ==========


def _make_react_wrapper(
    node_name: str,
    build_context: Callable[[GlobalState], Dict[str, Any]],
    build_system_prompt: Callable[[Dict[str, Any]], str],
    get_tools: Callable[[GlobalState], List[BaseTool]],
    map_result: Callable[[Optional[Dict[str, Any]], GlobalState], dict],
    max_rounds: int,
    result_schema: Optional[Dict[str, Any]] = None,
) -> Callable[[GlobalState], dict]:
    """生成可注册到主图的 ReAct 节点 wrapper。

    自动处理 GlobalState ↔ ReActState 双向映射、子图编译与预算扣减。
    """

    def _wrapper(state: GlobalState) -> dict:
        context = build_context(state)
        system_prompt = build_system_prompt(context)
        tools = get_tools(state)

        subgraph = create_react_subgraph(
            node_name=node_name,
            system_prompt=system_prompt,
            tools=tools,
            max_rounds=max_rounds,
            result_schema=result_schema,
        )

        # 创建 LLM 实例并通过 context 注入子图（避免每个内部节点重复构造）
        llm = create_llm(state["llm_config"])
        injected_context = dict(context)
        injected_context["_llm"] = llm

        initial: ReActState = {
            "messages": [SystemMessage(content=system_prompt)],
            "round": 0,
            "max_rounds": max_rounds,
            "status": "reasoning",
            "result": None,
            "context": injected_context,
        }

        final_state: ReActState = subgraph.invoke(initial)  # type: ignore[assignment]

        result_payload = final_state.get("result") if isinstance(final_state, dict) else None
        update = map_result(result_payload, state)
        if not isinstance(update, dict):
            update = {}

        # 预算扣减：按实际 round 数扣减（至少 1，避免子图空转时不扣预算）
        rounds_used = int(final_state.get("round", 0)) if isinstance(final_state, dict) else 0
        rounds_used = max(1, rounds_used)
        remaining = max(0, int(state.get("retry_budget_remaining", 0)) - rounds_used)
        # 不要覆盖 map_result 已经返回的 retry_budget_remaining（若有）
        update.setdefault("retry_budget_remaining", remaining)

        logger.info(
            "[%s] react wrapper done: rounds=%d, remaining_budget=%d",
            node_name, rounds_used, update["retry_budget_remaining"],
        )
        return update

    _wrapper.__name__ = f"react_wrapper_{node_name}"
    return _wrapper
