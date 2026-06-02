"""pwc_tools.py -- Papers With Code（PwC）API 工具封装。

通过直接 requests HTTP 调用 PwC v1 公开 endpoint 查询论文-仓库映射元数据，
为 resource_scout 节点提供官方/社区仓库候选。架构决策见 sprint2/architecture.md
§4.6（Q-S2-02 RESOLVED）：直接 requests + LRU 内存缓存 + 失败即降级 web_search，
与 sp1 ``deepxiv_tools`` 工具工厂模式同构，零额外依赖。

接入路径：
    - arxiv_id 查询：GET {PWC_BASE_URL}/papers/?arxiv_id={id}
    - title 查询  ：GET {PWC_BASE_URL}/papers/?q={title}
    - 仓库列表    ：GET {PWC_BASE_URL}/papers/{paper_id}/repositories/

治理约束（吸取 sp1 教训）：
    - 所有工具工厂的 ToolMessage 输出严格使用 json.dumps(ensure_ascii=False,
      sort_keys=True, default=str) 序列化（与 ``deepxiv_tools._serialize`` /
      ``git_tools._serialize_tool_result`` 同源），禁止 str(dict)（BUG-S1-02）；
    - HTTP 失败 / 重试 / 限速一律打 WARNING 日志，非静默吞错；
    - PwC 工具层瞬态错误不写 state["node_errors"]，只在 resource_scout 节点
      整体降级 from_scratch 时由 _map_*_result 统一标记 degraded。

API key（MVP 不申请）：模块加载时读 os.getenv("PWC_API_TOKEN")，存在则自动
注入 Authorization header，缺失时匿名访问不报错。
"""

from __future__ import annotations

import functools
import json
import logging
import os
import time
from typing import Dict, List, Optional, Tuple

import requests
from langchain_core.tools import tool, BaseTool

from config import (
    PWC_BASE_URL,
    PWC_RATE_LIMIT_RPS,
    PWC_TIMEOUT_CONNECT,
    PWC_TIMEOUT_READ,
)
from core.errors import TransientError

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

# 本地节流间隔（秒）：5 req/s 即 200ms。PwC 无显式限速文档，但 GitHub issues
# 提示历史上偶发 429，故保守节流。
_THROTTLE_INTERVAL = 1.0 / PWC_RATE_LIMIT_RPS

# 指数退避序列（秒），架构 §4.6：1s / 2s / 4s，共 3 次重试（首次 + 3 次重试）。
_RETRY_BACKOFF_SECONDS = (1.0, 2.0, 4.0)

# requests timeout 元组：(connect, read)。
_HTTP_TIMEOUT = (PWC_TIMEOUT_CONNECT, PWC_TIMEOUT_READ)

# title 模糊查询返回候选上限。
_TITLE_RESULT_LIMIT = 10

# 模块级限速时间戳（time.monotonic 单调时钟，跨调用共享）。
_LAST_REQUEST_AT: float = 0.0

# 模块加载时读取 API token（缺失则匿名访问，不报错）。
_PWC_API_TOKEN: Optional[str] = os.getenv("PWC_API_TOKEN")


# ---------------------------------------------------------------------------
# JSON 序列化合规 helper（BUG-S1-02 治理范式硬约束）
# ---------------------------------------------------------------------------

def _serialize_tool_result(result: object) -> str:
    """ReAct ToolMessage 序列化合规 helper（与 git_tools / deepxiv_tools 同源）。

    沿袭 BUG-S1-02 治理：
    - ensure_ascii=False（中文不转义）
    - sort_keys=True（Prompt Cache 字节级幂等）
    - default=str（兜底未知类型）

    禁止用 ``str(dict)``（Python repr 单引号会让下游 json.loads 永久失败）。
    """
    return json.dumps(result, ensure_ascii=False, sort_keys=True, default=str)


# ---------------------------------------------------------------------------
# 限速 / headers / 标准化 helper
# ---------------------------------------------------------------------------

def _throttle() -> None:
    """5 req/s 保守节流（PwC 无显式限速文档但 GitHub issues 提示偶发 429）。

    基于模块级 ``_LAST_REQUEST_AT`` 单调时间戳，相邻请求间隔不足
    ``_THROTTLE_INTERVAL`` 时 sleep 补足差额。
    """
    global _LAST_REQUEST_AT
    elapsed = time.monotonic() - _LAST_REQUEST_AT
    if elapsed < _THROTTLE_INTERVAL:
        time.sleep(_THROTTLE_INTERVAL - elapsed)
    _LAST_REQUEST_AT = time.monotonic()


def _build_headers() -> Dict[str, str]:
    """构造请求头；存在 PWC_API_TOKEN 时注入 Authorization，缺失则匿名。"""
    headers = {"User-Agent": "auto-reproduction/0.1", "Accept": "application/json"}
    if _PWC_API_TOKEN:
        headers["Authorization"] = f"Token {_PWC_API_TOKEN}"
    return headers


def _normalize_arxiv_id(arxiv_id: str) -> str:
    """arxiv_id 标准化（trim + 去 ``arXiv:`` 前缀），与 deepxiv 规范保持一致。

    LRU 缓存键基于标准化后的字符串，避免同一论文因写法差异而缓存 miss。
    """
    cleaned = (arxiv_id or "").strip()
    # 去除可能的 arXiv: / arxiv: 前缀（大小写不敏感）。
    if cleaned.lower().startswith("arxiv:"):
        cleaned = cleaned[len("arxiv:"):].strip()
    return cleaned


def _parse_retry_after(value: Optional[str]) -> Optional[float]:
    """解析 Retry-After 头（仅支持秒数形式；HTTP-date 形式忽略走指数退避）。"""
    if not value:
        return None
    try:
        seconds = float(value.strip())
        return seconds if seconds >= 0 else None
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# 带重试的 HTTP GET（核心网络层）
# ---------------------------------------------------------------------------

def _http_get_with_retry(url: str, params: Optional[Dict[str, str]] = None) -> Dict:
    """对 PwC endpoint 发起 GET，含限速节流 + 指数退避重试。

    处理矩阵（架构 §4.6）：
        - HTTP 200          -> 解析 JSON 返回 dict。
        - HTTP 429（有 Retry-After） -> 按 Retry-After 退避；最多 3 次重试。
        - HTTP 429（无 Retry-After） -> 指数退避 1s/2s/4s；最多 3 次重试。
        - HTTP 5xx          -> 指数退避 1s/2s/4s；最多 3 次重试。
        - requests timeout / 连接异常 -> 同 5xx 退避。
        - 超过 3 次重试      -> 抛 TransientError。

    所有失败 / 重试 / 限速等待均打 WARNING 日志（非静默吞错）。
    """
    total_attempts = len(_RETRY_BACKOFF_SECONDS) + 1  # 首次 + 3 次重试
    last_reason = "unknown"
    for attempt in range(total_attempts):
        _throttle()
        backoff = _RETRY_BACKOFF_SECONDS[attempt] if attempt < len(_RETRY_BACKOFF_SECONDS) else None
        try:
            resp = requests.get(
                url,
                params=params,
                headers=_build_headers(),
                timeout=_HTTP_TIMEOUT,
            )
        except requests.exceptions.RequestException as exc:
            # timeout / 连接错误 / DNS 失败等 -> 瞬态，按指数退避重试。
            last_reason = f"request exception: {exc}"
            logger.warning(
                "pwc _http_get_with_retry: 请求异常（attempt=%d/%d）url=%s: %s",
                attempt + 1, total_attempts, url, exc,
            )
            if backoff is not None:
                time.sleep(backoff)
                continue
            break

        status = resp.status_code
        if status == 200:
            try:
                return resp.json()
            except ValueError as exc:
                # 200 但 body 非 JSON（schema 偏差容错）：不重试，返回空结构。
                logger.warning(
                    "pwc _http_get_with_retry: 200 响应非 JSON url=%s: %s", url, exc,
                )
                return {}

        if status == 429:
            retry_after = _parse_retry_after(resp.headers.get("Retry-After"))
            wait = retry_after if retry_after is not None else backoff
            last_reason = f"HTTP 429 (Retry-After={resp.headers.get('Retry-After')})"
            logger.warning(
                "pwc _http_get_with_retry: 限流 429（attempt=%d/%d）url=%s wait=%s",
                attempt + 1, total_attempts, url, wait,
            )
            if wait is not None:
                time.sleep(wait)
                continue
            break

        if 500 <= status < 600:
            last_reason = f"HTTP {status}"
            logger.warning(
                "pwc _http_get_with_retry: 服务端错误 %d（attempt=%d/%d）url=%s",
                status, attempt + 1, total_attempts, url,
            )
            if backoff is not None:
                time.sleep(backoff)
                continue
            break

        # 其它 4xx（如 404）：客户端错误，不重试，按空结果容错处理。
        logger.warning(
            "pwc _http_get_with_retry: 客户端错误 %d url=%s（不重试，返回空）",
            status, url,
        )
        return {}

    # 重试耗尽 -> 抛 TransientError（ReAct agent 已知跳 web_search 兜底）。
    logger.error(
        "pwc _http_get_with_retry: 重试 %d 次仍失败 url=%s 原因=%s",
        total_attempts, url, last_reason,
    )
    raise TransientError(
        f"PwC API 请求失败（重试 {total_attempts} 次）",
        f"url={url} reason={last_reason}",
    )


# ---------------------------------------------------------------------------
# 响应解析 helper（schema 偏差容错）
# ---------------------------------------------------------------------------

def _extract_repos(paper_id: str) -> List[Dict]:
    """查询某 paper_id 的关联仓库列表，提取 url / stars / framework 等关键字段。

    schema 偏差容错：缺失字段填 None；任意结构异常返回空列表（不抛）。
    """
    if not paper_id:
        return []
    url = f"{PWC_BASE_URL}/papers/{paper_id}/repositories/"
    data = _http_get_with_retry(url)
    results = data.get("results") if isinstance(data, dict) else None
    if not isinstance(results, list):
        return []
    repos: List[Dict] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        repos.append(
            {
                "url": item.get("url"),
                "stars": item.get("stars"),
                "framework": item.get("framework"),
                "is_official": item.get("is_official"),
            }
        )
    return repos


def _paper_entry_from_result(item: Dict, *, with_repos: bool) -> Dict:
    """把 PwC /papers 单条 result 转为标准候选条目 {paper_id, title, repos}。"""
    paper_id = item.get("id") or item.get("paper_id") or ""
    title = item.get("title") or ""
    repos = _extract_repos(str(paper_id)) if (with_repos and paper_id) else []
    return {"paper_id": str(paper_id), "title": title, "repos": repos}


# ---------------------------------------------------------------------------
# 内部 LRU 缓存函数（返回 tuple，外层转 List[Dict]）
# ---------------------------------------------------------------------------

@functools.lru_cache(maxsize=128)
def _search_pwc_by_arxiv_cached(arxiv_id: str) -> Tuple[Dict, ...]:
    """LRU 缓存键：标准化后的 arxiv_id。返回 tuple（lru_cache 要求 hashable）。

    通过 GET /papers/?arxiv_id={id} 查询，命中后对每篇论文拉取其仓库列表。
    """
    logger.debug("pwc _search_pwc_by_arxiv_cached: cache MISS arxiv_id=%s", arxiv_id)
    url = f"{PWC_BASE_URL}/papers/"
    data = _http_get_with_retry(url, params={"arxiv_id": arxiv_id})
    results = data.get("results") if isinstance(data, dict) else None
    if not isinstance(results, list):
        return tuple()
    entries = [
        _paper_entry_from_result(item, with_repos=True)
        for item in results
        if isinstance(item, dict)
    ]
    return tuple(entries)


@functools.lru_cache(maxsize=128)
def _search_pwc_by_title_cached(title: str) -> Tuple[Dict, ...]:
    """LRU 缓存键：trim 后的 title。返回前 10 条候选（tuple）。

    通过 GET /papers/?q={title} 模糊查询；title 路径仅返回论文元数据，
    不逐条拉仓库（避免一次模糊查询触发 N 次 repositories 请求放大限流风险）。
    """
    logger.debug("pwc _search_pwc_by_title_cached: cache MISS title=%s", title)
    url = f"{PWC_BASE_URL}/papers/"
    data = _http_get_with_retry(url, params={"q": title})
    results = data.get("results") if isinstance(data, dict) else None
    if not isinstance(results, list):
        return tuple()
    entries = [
        _paper_entry_from_result(item, with_repos=False)
        for item in results[:_TITLE_RESULT_LIMIT]
        if isinstance(item, dict)
    ]
    return tuple(entries)


# ---------------------------------------------------------------------------
# 公开函数（业务层调用）
# ---------------------------------------------------------------------------

def search_pwc_by_arxiv(arxiv_id: str) -> List[Dict]:
    """通过 arxiv_id 查询 PwC，返回 [{paper_id, title, repos: [...]}]。

    LRU 缓存命中时直接返回（同一任务内重复查同一 arxiv_id 不重复打 PwC）；
    HTTP 失败 3 次重试后抛 TransientError。

    Args:
        arxiv_id: arXiv 论文 ID，如 "2405.14831"（允许带 arXiv: 前缀，内部标准化）。

    Returns:
        候选论文列表，每项含 paper_id / title / repos（repos 为 url/stars/... 字典列表）。

    Raises:
        TransientError: HTTP 429 / 5xx / timeout 重试 3 次后仍失败。
    """
    key = _normalize_arxiv_id(arxiv_id)
    if not key:
        return []
    cached = _search_pwc_by_arxiv_cached(key)
    logger.debug("pwc search_pwc_by_arxiv: arxiv_id=%s -> %d 条候选", key, len(cached))
    return [dict(item) for item in cached]


def search_pwc_by_title(title: str) -> List[Dict]:
    """通过 title 模糊查询 PwC，返回前 10 条候选。

    LRU 缓存命中时直接返回；HTTP 失败 3 次重试后抛 TransientError。

    Args:
        title: 论文标题（建议用英文主字段，与架构 §2.3.5 一致）。

    Returns:
        前 10 条候选论文列表，每项含 paper_id / title / repos（title 路径 repos 为空）。

    Raises:
        TransientError: HTTP 429 / 5xx / timeout 重试 3 次后仍失败。
    """
    key = (title or "").strip()
    if not key:
        return []
    cached = _search_pwc_by_title_cached(key)
    logger.debug("pwc search_pwc_by_title: title=%s -> %d 条候选", key, len(cached))
    return [dict(item) for item in cached]


# ---------------------------------------------------------------------------
# ReAct 工具工厂（供 ReAct agent 调用）
# ---------------------------------------------------------------------------

def make_search_pwc_tool() -> BaseTool:
    """工具工厂：search_pwc，按 arxiv_id 优先、title 兜底查询 PwC 论文-仓库映射。

    ToolMessage 输出统一经 ``_serialize_tool_result``（合法 JSON，沿用 BUG-S1-02
    治理）；工具函数内部捕获 TransientError 后返回错误描述字符串（不抛异常打断
    ReAct 子图，ReAct agent 按 system prompt 优先级链跳到 web_search 兜底）。
    """

    @tool
    def search_pwc(arxiv_id: str = "", title: str = "") -> str:
        """Search Papers With Code for repositories linked to a paper.

        Query by arxiv_id (preferred) and/or title. Returns a JSON object
        {"results": [{"paper_id": ..., "title": ..., "repos": [...]}]} where
        each repo contains url / stars / framework / is_official. On transient
        API failure returns {"results": [], "error": "..."} so the agent can
        fall back to web_search instead of crashing.

        Args:
            arxiv_id: arXiv paper ID, e.g. "2405.14831" (preferred lookup key).
            title: Paper title, used for fuzzy lookup when arxiv_id is empty.
        """
        try:
            if arxiv_id and arxiv_id.strip():
                results = search_pwc_by_arxiv(arxiv_id)
            elif title and title.strip():
                results = search_pwc_by_title(title)
            else:
                return _serialize_tool_result(
                    {"results": [], "error": "至少提供 arxiv_id 或 title 之一"}
                )
            return _serialize_tool_result({"results": results})
        except TransientError as exc:
            # 瞬态错误 -> 返回错误字符串，不打断 ReAct 子图（跳 web_search 兜底）。
            logger.warning("pwc search_pwc 工具: TransientError 降级返回空结果: %s", exc)
            return _serialize_tool_result({"results": [], "error": str(exc)})
        except Exception as exc:  # noqa: BLE001 — 兜底，不打断 ReAct 子图
            logger.warning("pwc search_pwc 工具: 未预期异常降级返回空结果: %s", exc)
            return _serialize_tool_result({"results": [], "error": str(exc)})

    return search_pwc  # type: ignore[return-value]
