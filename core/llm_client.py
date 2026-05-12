import json
import logging
import re
import time
from typing import Any, Dict, Optional

from langchain_openai import ChatOpenAI

from config import LLM_INITIAL_RETRY_DELAY, LLM_MAX_RETRIES, LLM_REQUEST_TIMEOUT
from core.errors import (
    LLMAuthError,
    LLMContextOverflowError,
    LLMOutputError,
    LLMRateLimitError,
    PermanentError,
    TransientError,
)
from core.state import LLMConfig

logger = logging.getLogger(__name__)


def create_llm(config: LLMConfig) -> ChatOpenAI:
    """根据 LLMConfig 创建 ChatOpenAI 实例，不发起网络请求。"""
    return ChatOpenAI(
        base_url=config["base_url"],
        model=config["model"],
        api_key=config["api_key"],
        temperature=config["temperature"],
        max_tokens=config["max_tokens"],
        timeout=LLM_REQUEST_TIMEOUT,
    )


def _extract_status_code(error: Exception) -> Optional[int]:
    """从异常中提取 HTTP 状态码。"""
    for attr in ("status_code", "code", "http_status"):
        val = getattr(error, attr, None)
        if isinstance(val, int):
            return val

    msg = str(error).lower()
    match = re.search(r"(?:status[_ ]?code|http)[:\s]*(\d{3})", msg)
    if match:
        return int(match.group(1))

    match = re.search(r"\b([1-5]\d{2})\b", msg)
    if match:
        code = int(match.group(1))
        if 400 <= code <= 599:
            return code

    return None


def _extract_retry_after(error: Exception) -> Optional[float]:
    """从异常中提取 Retry-After 值（秒）。"""
    val = getattr(error, "retry_after", None)
    if val is not None:
        try:
            return float(val)
        except (ValueError, TypeError):
            pass

    headers = getattr(error, "headers", None)
    if isinstance(headers, dict):
        ra = headers.get("retry-after") or headers.get("Retry-After")
        if ra is not None:
            try:
                return float(ra)
            except (ValueError, TypeError):
                pass

    msg = str(error)
    match = re.search(r"retry[- _]?after[:\s]*(\d+\.?\d*)", msg, re.IGNORECASE)
    if match:
        try:
            return float(match.group(1))
        except (ValueError, TypeError):
            pass

    return None


def _classify_error(error: Exception) -> Exception:
    """将原始异常映射为系统异常类型。"""
    if isinstance(error, (LLMAuthError, LLMRateLimitError,
                          LLMContextOverflowError, LLMOutputError,
                          PermanentError, TransientError)):
        return error

    status = _extract_status_code(error)
    msg_lower = str(error).lower()

    if status == 401 or "auth" in msg_lower or "unauthorized" in msg_lower:
        return LLMAuthError(str(error), detail=repr(error))

    if status == 429 or "rate limit" in msg_lower or "rate_limit" in msg_lower:
        retry_after = _extract_retry_after(error)
        return LLMRateLimitError(str(error), detail=repr(error), retry_after=retry_after)

    if ("context" in msg_lower and ("overflow" in msg_lower or "too long" in msg_lower)) \
            or "maximum context length" in msg_lower \
            or "context_length_exceeded" in msg_lower:
        return LLMContextOverflowError(str(error), detail=repr(error))

    if status is not None and 500 <= status <= 599:
        return TransientError(str(error), detail=repr(error))

    if "timeout" in msg_lower or "timed out" in msg_lower:
        return TransientError(str(error), detail=repr(error))

    return error


def _call_llm_with_retry(
    llm: ChatOpenAI,
    prompt: str,
    max_retries: int = LLM_MAX_RETRIES,
    initial_delay: float = LLM_INITIAL_RETRY_DELAY,
) -> str:
    """带指数退避重试的 LLM 调用。"""
    last_error: Optional[Exception] = None
    delay = initial_delay

    for attempt in range(max_retries + 1):
        try:
            response = llm.invoke(prompt)
            content = response.content
            if isinstance(content, list):
                content = "".join(str(c) for c in content)
            return content
        except Exception as e:
            classified = _classify_error(e)
            last_error = classified
            logger.warning(
                "LLM call attempt %d/%d failed: %s",
                attempt + 1, max_retries + 1, classified,
            )

            if isinstance(classified, PermanentError):
                raise classified from e

            if attempt >= max_retries:
                break

            if isinstance(classified, LLMRateLimitError) and classified.retry_after:
                wait = classified.retry_after
            else:
                wait = delay

            logger.info("Retrying in %.1fs...", wait)
            time.sleep(wait)
            delay *= 2

    if isinstance(last_error, TransientError):
        raise last_error
    raise TransientError(
        f"LLM call failed after {max_retries + 1} attempts: {last_error}",
        detail=repr(last_error),
    )


def _try_parse_json(text: str) -> Optional[Dict[str, Any]]:
    """从 LLM 响应中提取并解析 JSON。"""
    # 1: markdown 代码块 ```json ... ```
    match = re.search(r"```(?:json)?\s*\n?(.*?)```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1).strip())
        except json.JSONDecodeError:
            pass

    # 2: 纯 JSON 文本
    stripped = text.strip()
    if stripped.startswith("{"):
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            pass

    # 3: 从文本中提取第一对花括号内容（嵌套感知）
    start = text.find("{")
    if start == -1:
        return None

    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:i + 1])
                except json.JSONDecodeError:
                    return None
    return None


def _get_parse_error(text: str) -> str:
    """获取 JSON 解析失败的简洁描述。"""
    if not text or not text.strip():
        return "Empty response from LLM"

    if "{" not in text:
        return "Response contains no JSON object (no '{' found)"

    start = text.find("{")
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                candidate = text[start:i + 1]
                try:
                    json.loads(candidate)
                    return "JSON found but parse logic failed unexpectedly"
                except json.JSONDecodeError as e:
                    return f"JSON parse error: {e}"

    return "Unbalanced braces in response (missing closing '}')"


def call_with_structured_output(
    llm: ChatOpenAI,
    prompt: str,
    output_schema: Dict[str, Any],
    max_retries: int = LLM_MAX_RETRIES,
) -> Dict[str, Any]:
    """调用 LLM 并解析为结构化 JSON 输出。"""
    # 先尝试 LangChain with_structured_output
    try:
        structured_llm = llm.with_structured_output(output_schema)
        result = structured_llm.invoke(prompt)
        if isinstance(result, dict):
            return result
        if hasattr(result, "dict"):
            return result.dict()
        if hasattr(result, "model_dump"):
            return result.model_dump()
    except Exception as e:
        logger.info(
            "with_structured_output failed, falling back to manual JSON parse: %s", e,
        )

    # 回退：手动调用 + JSON 解析
    raw_text = _call_llm_with_retry(llm, prompt, max_retries=max_retries)
    parsed = _try_parse_json(raw_text)
    if parsed is not None:
        return parsed

    error_desc = _get_parse_error(raw_text)
    raise LLMOutputError(
        f"Failed to parse structured output: {error_desc}",
        detail=raw_text[:500],
    )


def estimate_tokens(text: str) -> int:
    """估算文本 token 数量。"""
    try:
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    except ImportError:
        return max(1, int(len(text) / 3.5))


def check_context_limit(text: str, max_tokens: int) -> bool:
    """检查文本是否在上下文窗口限制内（留 20% 余量）。"""
    estimated = estimate_tokens(text)
    return estimated <= max_tokens * 0.8
