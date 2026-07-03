import json
import logging
import re
import time
from typing import Any, Dict, Optional

from langchain_openai import ChatOpenAI

from config import (
    LLM_ENABLE_PROMPT_CACHE,
    LLM_INITIAL_RETRY_DELAY,
    LLM_MAX_RETRIES,
    LLM_REQUEST_TIMEOUT,
    get_llm_api_key,
)
from core.errors import (
    LLMAuthError,
    LLMContextOverflowError,
    LLMOutputError,
    LLMRateLimitError,
    PermanentError,
    TransientError,
)
from core.state import LLMConfig, LLMConfigSet

logger = logging.getLogger(__name__)


def create_llm(config: LLMConfig) -> ChatOpenAI:
    """根据 LLMConfig 创建 ChatOpenAI 实例，不发起网络请求。

    api_key 回退（方案 A，架构 §2.7.2，Maria 拍板 2026-06-07）：
        当 ``config["api_key"]`` 为空（strip 后为 ""）时，回退到
        ``config.get_llm_api_key()``（读 .env 的 LLM_API_KEY）。这是 api_key 回退的
        **唯一落点**（消费层最末端，紧邻 ChatOpenAI 构造）——绝不在表单层 /
        ``_refresh_llm_config_set`` 层回退，否则真实 key 会写进 LLMConfigSet →
        SqliteSaver checkpoint，违背"真实 key 不进 UI/session_state/checkpoint"安全目标。

    约束：
        - 用户**显式填写**的 api_key（非空）优先，不被 env 覆盖（override 一致规则：
          无论 resolve_llm_config 命中 default 还是某 override，空 api_key 在此统一
          回退同一个 .env 源）；
        - 回退取到 None（env 也无 LLM_API_KEY）时**不在此抛错**，原样传给 ChatOpenAI，
          错误交由后续 invoke 的 LLMError 路径暴露（兜底校验在表单提交时已早失败拦截）；
        - 回退值**不回写入参 config dict**，仅用于本次 ChatOpenAI 构造（进程内存）。
    """
    api_key = config["api_key"]
    if not (api_key and api_key.strip()):
        # 空 / 纯空白 → 回退 .env；非空时此分支不触发（保留用户显式值）。
        api_key = get_llm_api_key()
    return ChatOpenAI(
        base_url=config["base_url"],
        model=config["model"],
        api_key=api_key,
        temperature=config["temperature"],
        max_tokens=config["max_tokens"],
        timeout=LLM_REQUEST_TIMEOUT,
    )


def resolve_llm_config(
    llm_config_set: LLMConfigSet,
    node_name: Optional[str],
) -> LLMConfig:
    """节点级 LLM 路由：优先 overrides[node_name]，缺失回退 default。

    Sprint 2 任务 A2（dev-plan §A2 / 架构 §2.1.1.bis）。本函数为纯函数，
    不创建 ChatOpenAI、不发起任何网络请求，仅在 LLMConfigSet 内做配置选路：

    - 优先返回节点级覆写 ``overrides[node_name]``（节点在覆写表中显式登记时）；
    - 否则回退到全局 ``default`` 配置（节点未覆写、或 node_name 为 None 的共用路径）。

    Args:
        llm_config_set: 多模型配置集合，必须含合法 ``default`` 子配置。
        node_name: 当前节点名；为 None 时（如 force_finish 共用路径）直接返回 default。

    Raises:
        PermanentError: llm_config_set 为 None、非 dict、或缺 ``default`` 键
            （形态错误属不可重试的配置缺陷，故归类为永久错误）。

    Returns:
        最终生效的 LLMConfig（保证非 None）。
    """
    # 边界 1：llm_config_set 为 None / 非 dict / 缺 default 键 → 形态错误，抛永久错误。
    # 注意 dict.get 对非 dict 不可用，故先做 isinstance 判定再取 default。
    if not isinstance(llm_config_set, dict) or "default" not in llm_config_set:
        raise PermanentError("llm_config_set.default 缺失或形态错误")

    default_config = llm_config_set["default"]

    # 边界 2：node_name 为 None（force_finish 等共用路径）→ 直接返回 default。
    if node_name is None:
        return default_config

    # overrides 缺失或非 dict 时按空覆写表处理（向后兼容 sp1 单一全局配置模式）。
    overrides = llm_config_set.get("overrides")
    if not isinstance(overrides, dict):
        return default_config

    # 边界 3 / 4：命中覆写表则返回节点级配置，否则回退 default。
    return overrides.get(node_name, default_config)


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


def _log_cache_metrics(response: Any) -> None:
    """只读探测 Prompt Cache 命中指标，命中时以 INFO 日志输出。

    设计原则（参见架构文档 §2.6.6 / 技术架构文档 §10.5）：
    - 仅在 LLM_ENABLE_PROMPT_CACHE 为 True 时运行。
    - 兼容多种字段命名：
        * OpenAI 风格：usage.prompt_tokens_details.cached_tokens
        * Anthropic 风格：usage.cache_read_input_tokens / cache_creation_input_tokens
        * LangChain 通用：usage_metadata.input_token_details.cache_read
    - 整个函数包在 try/except 中，绝不抛错、不阻塞主流程。
    - 读不到任何指标时静默返回，不打印日志。
    """
    if not LLM_ENABLE_PROMPT_CACHE:
        return

    try:
        cached_tokens: Optional[int] = None
        prompt_tokens: Optional[int] = None
        cache_creation_tokens: Optional[int] = None

        # 1) LangChain 通用 usage_metadata（推荐路径）
        usage_meta = getattr(response, "usage_metadata", None)
        if isinstance(usage_meta, dict):
            prompt_tokens = usage_meta.get("input_tokens") or prompt_tokens
            input_details = usage_meta.get("input_token_details") or {}
            if isinstance(input_details, dict):
                cached_tokens = (
                    input_details.get("cache_read")
                    or input_details.get("cache_read_input_tokens")
                    or cached_tokens
                )
                cache_creation_tokens = (
                    input_details.get("cache_creation")
                    or input_details.get("cache_creation_input_tokens")
                    or cache_creation_tokens
                )

        # 2) response_metadata 中的 OpenAI / Anthropic 原生字段
        resp_meta = getattr(response, "response_metadata", None)
        if isinstance(resp_meta, dict):
            usage = resp_meta.get("usage") or resp_meta.get("token_usage") or {}
            if isinstance(usage, dict):
                if prompt_tokens is None:
                    prompt_tokens = usage.get("prompt_tokens") or usage.get("input_tokens")
                # OpenAI 风格
                details = usage.get("prompt_tokens_details") or {}
                if isinstance(details, dict) and cached_tokens is None:
                    cached_tokens = details.get("cached_tokens")
                # Anthropic 风格
                if cached_tokens is None:
                    cached_tokens = usage.get("cache_read_input_tokens")
                if cache_creation_tokens is None:
                    cache_creation_tokens = usage.get("cache_creation_input_tokens")

        cached_tokens = int(cached_tokens) if cached_tokens else 0
        prompt_tokens = int(prompt_tokens) if prompt_tokens else 0
        cache_creation_tokens = int(cache_creation_tokens) if cache_creation_tokens else 0

        if cached_tokens > 0:
            if prompt_tokens > 0:
                ratio = cached_tokens / prompt_tokens
                logger.info(
                    "Prompt cache hit: cached_tokens=%d / prompt_tokens=%d (%.1f%%)"
                    + (", cache_creation=%d" if cache_creation_tokens else "%s"),
                    cached_tokens, prompt_tokens, ratio * 100,
                    cache_creation_tokens if cache_creation_tokens else "",
                )
            else:
                logger.info(
                    "Prompt cache hit: cached_tokens=%d (prompt_tokens unavailable)",
                    cached_tokens,
                )
    except Exception:  # noqa: BLE001 探测函数绝不抛错
        return


def invoke_with_retry(
    runnable: Any,
    input: Any,
    max_retries: int = LLM_MAX_RETRIES,
    initial_delay: float = LLM_INITIAL_RETRY_DELAY,
) -> Any:
    """带指数退避重试的 ``runnable.invoke(input)``，返回**原始 response 对象**。

    与 ``_call_llm_with_retry`` 的区别：不提取 ``response.content``，完整保留
    AIMessage 的 ``tool_calls`` / ``usage_metadata`` 等属性，供 ReAct 主循环
    （react_base.reasoning_node / force_finish / with_structured_output 降级路径）
    直接消费。

    约束（bug 修复"LLM 重试层未接入 ReAct 主循环"，2026-07-02）：
    - **绝不修改 / 变形传入的 input**（messages 列表原样透传，不 copy 不追加），
      保证 Prompt Cache 字节级前缀稳定性；重试时重发同一个对象。
    - PermanentError（含 LLMAuthError / LLMContextOverflowError）立刻抛出不重试。
    - TransientError（含 LLMRateLimitError）与未分类错误按指数退避重试，
      LLMRateLimitError 携带 retry_after 时优先使用该等待值。
    - 重试耗尽后抛 TransientError（与 ``_call_llm_with_retry`` 异常语义一致）。
    """
    last_error: Optional[Exception] = None
    delay = initial_delay

    for attempt in range(max_retries + 1):
        try:
            response = runnable.invoke(input)
            try:
                _log_cache_metrics(response)
            except Exception:  # noqa: BLE001 二次防御
                pass
            return response
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


def _call_llm_with_retry(
    llm: ChatOpenAI,
    prompt: str,
    max_retries: int = LLM_MAX_RETRIES,
    initial_delay: float = LLM_INITIAL_RETRY_DELAY,
) -> str:
    """带指数退避重试的 LLM 调用（返回纯文本 content）。

    委托 ``invoke_with_retry`` 执行重试循环（单一实现，避免两份退避逻辑漂移），
    仅在成功后提取 ``response.content``。签名与异常语义保持不变。
    """
    response = invoke_with_retry(
        llm, prompt, max_retries=max_retries, initial_delay=initial_delay,
    )
    content = response.content
    if isinstance(content, list):
        content = "".join(str(c) for c in content)
    return content


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
