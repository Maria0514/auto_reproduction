"""通用 ReAct 子图基础设施。

提供两个公开入口：

- ``create_react_subgraph()``：构建 ReAct 子图（reasoning → tool_executor →
  budget_check 循环，终态走 finalize → END，超预算走 force_finish → finalize → END）。
- ``_make_react_wrapper()``：将子图包装成一个 ``(GlobalState) -> dict`` 主图节点。

并暴露一个通用 helper ``extract_last_tool_result(messages, tool_name)``，供
下游节点在 LLM 输出漏字段时从工具调用历史回填兜底。

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
    HumanMessage,
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
    """工具返回内容超过 limit 时截断并附加固定省略提示，防止上下文溢出。

    Prompt Cache 友好（方案 A，参见架构文档 §2.6.6）：
    - 截断标记使用固定字符串（不含输入长度 / 时间戳 / 临时路径 / 随机 id 等动态片段）。
    - 同一输入永远产出同一输出文本，保证 ToolMessage.content 在多轮 ReAct 中字节级幂等。
    """
    if len(text) <= limit:
        return text
    suffix = f"\n... [truncated at {limit} chars]"
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


# ========== 公共 helper：从 ToolMessage 历史回填字段 ==========


def extract_last_tool_result(
    messages: Sequence[BaseMessage],
    tool_name: str,
) -> Optional[Dict[str, Any]]:
    """从 messages 列表里反向查找最后一条 ``name=tool_name`` 的 ToolMessage，
    将其 content 反序列化为 dict 返回。

    注意：
    - ToolMessage.content 由 ``_stringify_tool_result`` 序列化为 JSON 字符串，
      并经过 ``_truncate_tool_result`` 截断处理（可能在末尾附加
      ``... [truncated at N chars]``）。本 helper 在解析失败时自动剥离截断后缀
      再次尝试，仍失败则返回 ``None``，不抛错。
    - 该 helper 设计为基础设施级能力（paper_intake、paper_analysis、
      resource_scout 都可能用到从工具历史回填字段的兜底）。
    """
    if not messages or not tool_name:
        return None

    for msg in reversed(list(messages)):
        if not isinstance(msg, ToolMessage):
            continue
        if getattr(msg, "name", None) != tool_name:
            continue

        content = getattr(msg, "content", None)
        if content is None:
            return None
        if isinstance(content, list):
            # 兼容 content parts 形式
            content = "".join(
                c if isinstance(c, str) else (c.get("text") or "")
                if isinstance(c, dict)
                else ""
                for c in content
            )
        if not isinstance(content, str):
            content = str(content)

        text = content.strip()
        if not text:
            return None

        # 1) 直接尝试整段 JSON
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                return parsed
        except (TypeError, ValueError):
            pass

        # 2) 剥离截断后缀再试（_truncate_tool_result 附加的固定标记）
        trunc_marker_idx = text.rfind("... [truncated at")
        candidate_after_strip: Optional[str] = None
        if trunc_marker_idx > 0:
            candidate_after_strip = text[:trunc_marker_idx].rstrip()
            try:
                parsed = json.loads(candidate_after_strip)
                if isinstance(parsed, dict):
                    return parsed
            except (TypeError, ValueError):
                pass

        # 3) 抓第一对平衡花括号
        start = text.find("{")
        if start == -1:
            return None
        depth = 0
        for i in range(start, len(text)):
            ch = text[i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        parsed = json.loads(text[start : i + 1])
                        if isinstance(parsed, dict):
                            return parsed
                    except (TypeError, ValueError):
                        break

        # 4) 截断 JSON 修复（BUG-S1-02）：工具层先 json.dumps 再 truncate 时，
        #    尾部 } 可能被切掉。回退到一个已闭合的"良好前缀"再 json.loads。
        repair_source = candidate_after_strip if candidate_after_strip is not None else text
        repaired = _repair_truncated_json_prefix(repair_source)
        if repaired is not None:
            try:
                parsed = json.loads(repaired)
                if isinstance(parsed, dict):
                    return parsed
            except (TypeError, ValueError):
                return None
        return None

    return None


def _repair_truncated_json_prefix(text: str) -> Optional[str]:
    """对被截断的 JSON 文本做"最长良好前缀"修复，返回可被 ``json.loads`` 的对象字符串。

    BUG-S1-02 修复配套：工具层先 ``json.dumps`` 后 ``_truncate`` 会切掉若干结尾闭合符号，
    导致顶层对象不闭合（截断点可能在字符串中间 / 数组中间 / 嵌套对象中间）。

    算法：
    1) 先用 ``json.JSONDecoder.raw_decode`` 试一次，能解则直接返回。
    2) 否则手写迷你 JSON 状态机扫描整段文本，维护 ``{`` / ``[`` 栈以及"是否在
       字符串内"标记。记录每次"刚好回到顶层对象首尾边界" / "完成一个键值对"的
       安全截断点。从最靠后的安全点回退：截掉尾部不完整片段，依据栈状态追加
       对应数量的 ``]`` / ``}``，构造合法 JSON 字符串。
    3) 用 ``raw_decode`` 校验，命中即返回；不命中则继续向前找下一个安全点。

    注意：sort_keys 序列化保证 ``abstract`` / ``arxiv_id`` / ``authors`` /
    ``categories`` 等关键字段出现在文本前部（按字母序），即使尾部丢失若干字段，
    paper_intake 兜底也能拿到 categories。
    """
    if not text:
        return None
    start = text.find("{")
    if start == -1:
        return None

    body = text[start:]
    decoder = json.JSONDecoder()

    # 路径 4a：raw_decode 抓取从 start 起的最长良好 JSON 对象。
    try:
        obj, end = decoder.raw_decode(body)
        if isinstance(obj, dict):
            return body[:end]
    except json.JSONDecodeError:
        pass

    # 路径 4b：状态机扫描记录安全截断点。
    # 安全点定义：当前不在字符串内、不在转义后、且刚刚消费完一个完整的「value 后」
    # 或「}/] 闭合符号」位置——此时截断后只需补齐栈剩余闭合符号即可。
    safe_points: List[tuple] = []  # (cut_idx_exclusive, stack_snapshot)
    stack: List[str] = []           # 元素为 '{' 或 '['
    in_string = False
    escape = False
    i = 0
    n = len(body)
    while i < n:
        ch = body[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
                # 字符串闭合 → 值已完整，记录安全点
                if stack:
                    safe_points.append((i + 1, tuple(stack)))
            i += 1
            continue

        if ch == '"':
            in_string = True
        elif ch == "{":
            stack.append("{")
        elif ch == "[":
            stack.append("[")
        elif ch == "}":
            if stack and stack[-1] == "{":
                stack.pop()
                safe_points.append((i + 1, tuple(stack)))
            else:
                # 结构错乱，放弃 path 4b
                return None
        elif ch == "]":
            if stack and stack[-1] == "[":
                stack.pop()
                safe_points.append((i + 1, tuple(stack)))
            else:
                return None
        elif ch in ("0", "1", "2", "3", "4", "5", "6", "7", "8", "9", "-",
                    "t", "f", "n"):
            # 标量值开始：贪婪扫描到分隔符或结构符号
            j = i
            while j < n and body[j] not in (",", "}", "]", " ", "\t", "\n", "\r"):
                j += 1
            if j == n:
                # 标量未终止 → 不能作为安全点
                i = n
                break
            # 标量已完整
            if stack:
                safe_points.append((j, tuple(stack)))
            i = j
            continue
        # 其它字符（: , 空白）不更新安全点
        i += 1

    # 从最靠后的安全点回退尝试构造闭合 JSON
    for cut_idx, stack_snapshot in reversed(safe_points[-64:]):
        prefix = body[:cut_idx]
        # 闭合：顶层必须是 '{' 起步（PaperMeta-like dict）
        if not stack_snapshot:
            # 已经闭合到顶层，直接验证
            try:
                obj, end = decoder.raw_decode(prefix)
                if isinstance(obj, dict):
                    return prefix[:end]
            except json.JSONDecodeError:
                continue
            continue
        # 自栈顶向栈底反向追加对应闭合符号
        closers = "".join(
            "}" if s == "{" else "]" for s in reversed(stack_snapshot)
        )
        candidate = prefix + closers
        try:
            obj, end = decoder.raw_decode(candidate)
            if isinstance(obj, dict):
                return candidate[:end]
        except json.JSONDecodeError:
            continue
    return None


# ========== Schema 强制输出 helper ==========


def _normalize_schema_for_structured_output(
    schema: Dict[str, Any],
    fallback_title: str,
) -> Dict[str, Any]:
    """langchain_openai.with_structured_output 要求 schema 顶层包含 ``title``。

    若 schema 缺少 ``title``，注入 ``fallback_title``。不修改原 dict（返回浅拷贝）。
    """
    if "title" in schema:
        return schema
    out = dict(schema)
    out["title"] = fallback_title
    return out


def _invoke_with_schema(
    llm: Any,
    messages: List[BaseMessage],
    schema: Dict[str, Any],
    node_name: str,
) -> Optional[Dict[str, Any]]:
    """用 ``llm.with_structured_output(schema)`` 强制 LLM 输出 JSON Schema 合规 dict。

    多档降级：
    1. ``method="json_schema"``（OpenAI 严格模式）
    2. ``method="function_calling"``（普遍兼容，绝大多数 OpenAI 兼容网关支持）
    3. 失败返回 None，由调用方决定后续处理（如保留原 <result> 解析结果）。

    禁止在此 helper 内抛异常——任何失败都返回 None。
    """
    if llm is None or not schema:
        return None

    normalized = _normalize_schema_for_structured_output(schema, node_name)

    for method in ("json_schema", "function_calling"):
        try:
            structured = llm.with_structured_output(normalized, method=method)
            result = structured.invoke(messages)
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "[%s] with_structured_output(method=%s) failed: %s",
                node_name, method, exc,
            )
            continue

        # Runnable 可能返回 dict / pydantic / BaseModel
        if isinstance(result, dict):
            return result
        if hasattr(result, "model_dump"):
            try:
                return result.model_dump()
            except Exception:  # noqa: BLE001
                pass
        if hasattr(result, "dict"):
            try:
                return result.dict()  # type: ignore[no-any-return]
            except Exception:  # noqa: BLE001
                pass
        # 其它情况尽力而为
        try:
            return dict(result)  # type: ignore[arg-type]
        except Exception:  # noqa: BLE001
            continue

    logger.warning(
        "[%s] with_structured_output all methods failed; falling back to tag parse",
        node_name,
    )
    return None


def _missing_required_fields(
    parsed: Optional[Dict[str, Any]],
    schema: Optional[Dict[str, Any]],
) -> List[str]:
    """检查 parsed 是否缺少 schema.required 中的字段（缺失 / None / 空容器视为缺失）。

    无 schema 或无 required 时返回空 list。允许 parsed 是 error 报告（含 ``error`` 字段）：
    此时跳过必填校验，由调用方决定是否走错误路径。
    """
    if not parsed or not isinstance(parsed, dict):
        return []
    if parsed.get("error"):
        return []
    if not schema or not isinstance(schema, dict):
        return []
    required = schema.get("required") or []
    if not isinstance(required, list):
        return []
    missing: List[str] = []
    for field in required:
        if field not in parsed:
            missing.append(field)
            continue
        value = parsed.get(field)
        if value is None:
            missing.append(field)
            continue
        # 必填的 list/dict 为空容器，认为信息缺失
        if isinstance(value, (list, dict)) and len(value) == 0:
            missing.append(field)
    return missing


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
        """预算耗尽：注入强制终止提示，再调一次 LLM 得到最终输出。

        若提供了 ``result_schema``，优先使用 ``with_structured_output`` 强制 LLM
        直接输出 schema 合规 JSON（不再依赖 ``<result>`` 标签解析）；schema 强制
        失败时回退到原 free-form 路径，由 finalize_node 再做一次校验。
        """
        instruction = SystemMessage(
            content=(
                "已达到最大推理轮次预算。请立刻基于当前已收集的信息，直接输出 "
                f"{REACT_RESULT_TAG_OPEN}{{JSON 结构化结果}}{REACT_RESULT_TAG_CLOSE} "
                "标签包裹的最终结果，不要再调用任何工具。若信息不足，请在 JSON 中"
                "使用空值或 null 字段占位。"
            )
        )
        llm = state["context"].get("_llm")
        if llm is None:
            raise TransientError(
                f"[{node_name}] react subgraph missing _llm in context",
            )

        # schema 优先：直接拿到 dict，绕过 <result> 标签解析
        if result_schema:
            forced = _invoke_with_schema(
                llm=llm,
                messages=state["messages"] + [instruction],
                schema=result_schema,
                node_name=node_name,
            )
            if forced is not None:
                logger.info(
                    "[%s] force_finish: structured output via schema (round=%d)",
                    node_name, state["round"] + 1,
                )
                # 同步追加一条 AIMessage 反映该结果，便于调试追溯
                ai_repr = AIMessage(
                    content=(
                        f"{REACT_RESULT_TAG_OPEN}"
                        f"{json.dumps(forced, ensure_ascii=False, default=str)}"
                        f"{REACT_RESULT_TAG_CLOSE}"
                    )
                )
                return {
                    "messages": [instruction, ai_repr],
                    "round": state["round"] + 1,
                    "status": "done",
                    "result": forced,
                }

        # 回退：与原行为一致——再调一次 free-form LLM
        response = llm.invoke(state["messages"] + [instruction])
        if not isinstance(response, AIMessage):
            response = AIMessage(content=str(getattr(response, "content", response)))
        return {
            "messages": [instruction, response],
            "round": state["round"] + 1,
            "status": "done",
        }

    def finalize_node(state: ReActState) -> dict:
        """解析最后一条 AIMessage 中的 <result>JSON</result> 并写入 result。

        若 force_finish_node 已经通过 ``with_structured_output`` 直接写入了
        ``state["result"]``，本节点不会覆盖该值，仅在标签解析能提供更完整结果
        时才接管（schema 优先级最高）。

        若提供了 ``result_schema`` 且解析后必填字段不全（缺失 / None / 空容器），
        再用 ``with_structured_output`` 重新生成一次（确保 schema 合规）。
        """
        # 若 force_finish_node 已经写入 schema 强制结果，直接沿用
        existing_result = state.get("result")
        if isinstance(existing_result, dict) and existing_result:
            missing = _missing_required_fields(existing_result, result_schema)
            if not missing:
                return {"result": existing_result, "status": "done"}

        last_ai_text = ""
        for msg in reversed(state["messages"]):
            if isinstance(msg, AIMessage):
                last_ai_text = _ai_message_text(msg)
                break

        payload = _extract_result_payload(last_ai_text)
        parsed: Optional[Dict[str, Any]] = None
        if payload is not None:
            parsed = _parse_result_payload(payload)
            if parsed is None:
                logger.warning(
                    "[%s] finalize: failed to parse JSON inside <result> tag", node_name,
                )
        else:
            logger.warning(
                "[%s] finalize: <result> tag not found in last AIMessage", node_name,
            )

        # schema 兜底：标签解析失败 / 必填字段不全 → 用 with_structured_output 重生成
        if result_schema:
            missing = _missing_required_fields(parsed, result_schema)
            need_reforce = parsed is None or bool(missing)
            if need_reforce:
                llm = state["context"].get("_llm")
                if llm is not None:
                    logger.info(
                        "[%s] finalize: schema-enforce regen (parsed=%s, missing=%s)",
                        node_name,
                        "ok" if parsed is not None else "none",
                        missing or "n/a",
                    )
                    forced = _invoke_with_schema(
                        llm=llm,
                        messages=state["messages"],
                        schema=result_schema,
                        node_name=node_name,
                    )
                    if forced is not None:
                        # 合并策略：schema 强制结果优先，但保留原 parsed 中
                        # schema 未覆盖的字段（如 notes / pdf_url 等可选字段）
                        if isinstance(parsed, dict):
                            merged = dict(parsed)
                            merged.update(forced)
                            return {"result": merged, "status": "done"}
                        return {"result": forced, "status": "done"}

        if parsed is None:
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

        # Prompt Cache 前缀稳定化（方案 A，参见架构文档 §2.6.6 / 技术架构文档 §10.5）：
        # - 第一条 SystemMessage 仅包含 build_system_prompt 返回的固定模板，
        #   不得插入 arxiv_id / URL / 时间戳 / user_input 等动态变量。
        # - 第二条 HumanMessage 携带 build_context 返回的动态上下文，
        #   序列化为稳定 JSON（sort_keys=True）以保证同一输入下字节级幂等。
        # - 注入 LLM 实例的内部键 "_llm" 不写入 HumanMessage（不可 JSON 序列化也无意义）。
        initial_messages: List[BaseMessage] = [SystemMessage(content=system_prompt)]
        human_payload = {k: v for k, v in context.items() if not k.startswith("_")}
        if human_payload:
            try:
                human_text = json.dumps(
                    human_payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    default=str,
                )
            except (TypeError, ValueError):
                human_text = str(human_payload)
            initial_messages.append(HumanMessage(content=human_text))

        initial: ReActState = {
            "messages": initial_messages,
            "round": 0,
            "max_rounds": max_rounds,
            "status": "reasoning",
            "result": None,
            "context": injected_context,
        }

        final_state: ReActState = subgraph.invoke(initial)  # type: ignore[assignment]

        result_payload = final_state.get("result") if isinstance(final_state, dict) else None
        final_messages = final_state.get("messages") if isinstance(final_state, dict) else None
        # 向后兼容：若 map_result 仅接受 2 个位置参数（既有节点 / 单测），按 2 参调用；
        # 若声明了第三个位置参数（如 react_messages），把子图最终 messages 列表传入，
        # 供节点做"工具结果回填"等兜底（架构 §2.8.2 head 优先契约）。
        update: dict
        try:
            import inspect

            sig = inspect.signature(map_result)
            positional = [
                p for p in sig.parameters.values()
                if p.kind in (
                    inspect.Parameter.POSITIONAL_ONLY,
                    inspect.Parameter.POSITIONAL_OR_KEYWORD,
                )
            ]
            if len(positional) >= 3:
                update = map_result(result_payload, state, final_messages)  # type: ignore[call-arg]
            else:
                update = map_result(result_payload, state)
        except (TypeError, ValueError):
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
