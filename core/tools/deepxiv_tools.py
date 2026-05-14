"""deepxiv SDK 薄封装 + ReAct 工具工厂函数。

将 deepxiv_sdk.Reader 的方法映射为系统异常体系，并提供 7 个
LangChain BaseTool 工厂函数供 ReAct agent 节点使用。
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

from deepxiv_sdk import (
    Reader,
    NotFoundError,
    AuthenticationError,
    BadRequestError,
    RateLimitError,
    ServerError,
    APIError,
)
from langchain_core.tools import tool, BaseTool

from config import get_deepxiv_token, TOOL_RESULT_MAX_LENGTH
from core.errors import PermanentError, TransientError

logger = logging.getLogger(__name__)


class DeepxivTools:
    """deepxiv SDK Reader 的薄封装层。

    将 SDK 异常统一映射为 PermanentError / TransientError，
    并在每次 API 调用前后记录日志。
    """

    def __init__(self, token: Optional[str] = None):
        resolved_token = token if token is not None else get_deepxiv_token()
        self._reader = Reader(token=resolved_token)
        logger.info("DeepxivTools initialized (token=%s)", "***" if resolved_token else "None")

    def _handle_sdk_error(self, e: Exception, operation: str) -> None:
        """将 SDK 异常映射为系统异常并抛出。"""
        detail = f"[{operation}] {type(e).__name__}: {e}"

        if isinstance(e, NotFoundError):
            raise PermanentError(f"{operation}: resource not found", detail) from e
        if isinstance(e, AuthenticationError):
            raise PermanentError(f"{operation}: authentication failed", detail) from e
        if isinstance(e, BadRequestError):
            raise PermanentError(f"{operation}: bad request", detail) from e
        if isinstance(e, RateLimitError):
            raise TransientError(f"{operation}: rate limit exceeded (SDK already retried)", detail) from e
        if isinstance(e, ServerError):
            raise TransientError(f"{operation}: server error (SDK already retried)", detail) from e
        if isinstance(e, APIError):
            raise TransientError(f"{operation}: API error (SDK already retried)", detail) from e
        if isinstance(e, ValueError):
            raise PermanentError(f"{operation}: invalid input — {e}", detail) from e

        raise TransientError(f"{operation}: unexpected error — {e}", detail) from e

    def search_papers(self, query: str, size: int = 10) -> List[Dict]:
        logger.info("search_papers: query=%r, size=%d", query, size)
        try:
            resp = self._reader.search(query=query, size=size)
        except (PermanentError, TransientError):
            raise
        except Exception as exc:
            self._handle_sdk_error(exc, "search_papers")
        results: List[Dict] = resp.get("result", [])
        logger.info("search_papers: returned %d results", len(results))
        return results

    def get_paper_brief(self, arxiv_id: str) -> Dict:
        logger.info("get_paper_brief: arxiv_id=%s", arxiv_id)
        try:
            result = self._reader.brief(arxiv_id=arxiv_id)
        except (PermanentError, TransientError):
            raise
        except Exception as exc:
            self._handle_sdk_error(exc, "get_paper_brief")
        if not result:
            raise PermanentError(f"get_paper_brief: empty response for {arxiv_id}")
        logger.info("get_paper_brief: success for %s", arxiv_id)
        return result

    def get_paper_head(self, arxiv_id: str) -> Dict:
        logger.info("get_paper_head: arxiv_id=%s", arxiv_id)
        try:
            result = self._reader.head(arxiv_id=arxiv_id)
        except (PermanentError, TransientError):
            raise
        except Exception as exc:
            self._handle_sdk_error(exc, "get_paper_head")
        if not result:
            raise PermanentError(f"get_paper_head: empty response for {arxiv_id}")
        logger.info("get_paper_head: success for %s", arxiv_id)
        return result

    def get_paper_structure(self, arxiv_id: str) -> Dict:
        logger.info("get_paper_structure: arxiv_id=%s", arxiv_id)
        try:
            head = self._reader.head(arxiv_id=arxiv_id)
        except (PermanentError, TransientError):
            raise
        except Exception as exc:
            self._handle_sdk_error(exc, "get_paper_structure")
        if not head:
            raise PermanentError(f"get_paper_structure: empty response for {arxiv_id}")
        sections = head.get("sections", [])
        structure = {
            "arxiv_id": arxiv_id,
            "title": head.get("title", ""),
            "sections": sections,
            "token_count": head.get("token_count"),
        }
        logger.info("get_paper_structure: %d sections for %s", len(sections), arxiv_id)
        return structure

    def read_section(self, arxiv_id: str, section_name: str) -> str:
        logger.info("read_section: arxiv_id=%s, section=%s", arxiv_id, section_name)
        try:
            content = self._reader.section(arxiv_id=arxiv_id, section_name=section_name)
        except (PermanentError, TransientError):
            raise
        except Exception as exc:
            self._handle_sdk_error(exc, "read_section")
        if not content:
            raise PermanentError(
                f"read_section: empty content for section '{section_name}' in {arxiv_id}"
            )
        logger.info("read_section: got %d chars for %s/%s", len(content), arxiv_id, section_name)
        return content

    def get_full_paper(self, arxiv_id: str) -> str:
        logger.info("get_full_paper: arxiv_id=%s", arxiv_id)
        try:
            content = self._reader.raw(arxiv_id=arxiv_id)
        except (PermanentError, TransientError):
            raise
        except Exception as exc:
            self._handle_sdk_error(exc, "get_full_paper")
        if not content:
            raise PermanentError(f"get_full_paper: empty content for {arxiv_id}")
        logger.info("get_full_paper: got %d chars for %s", len(content), arxiv_id)
        return content

    def web_search(self, query: str) -> List[Dict]:
        logger.info("web_search: query=%r", query)
        try:
            resp = self._reader.websearch(query=query)
        except (PermanentError, TransientError):
            raise
        except Exception as exc:
            self._handle_sdk_error(exc, "web_search")
        results: List[Dict] = resp.get("result", []) if isinstance(resp, dict) else []
        logger.info("web_search: returned %d results", len(results))
        return results


# ---------------------------------------------------------------------------
# ReAct 工具工厂函数
# ---------------------------------------------------------------------------

def _truncate(text: str) -> str:
    """将文本截断到 TOOL_RESULT_MAX_LENGTH 字符。

    Prompt Cache 友好（方案 A，参见架构文档 §2.6.6 / 技术架构文档 §10.5）：
    - 截断标记使用固定字符串，不含输入长度 / 时间戳 / 临时路径 / 随机 id 等动态片段。
    - 同一输入永远产出同一输出文本，保证工具返回值在多轮 ReAct 中字节级幂等。
    - 调用方注意：deepxiv 各方法的返回字段（brief/section/raw 等）禁止包含动态片段；
      若 SDK 上游污染了这些字段，需在此层做净化，否则会破坏前缀稳定导致 Prompt Cache 失效。
    """
    if len(text) <= TOOL_RESULT_MAX_LENGTH:
        return text
    return text[:TOOL_RESULT_MAX_LENGTH] + f"\n... [truncated at {TOOL_RESULT_MAX_LENGTH} chars]"


def get_paper_brief_tool(token: Optional[str] = None) -> BaseTool:
    """工厂函数：返回 get_paper_brief BaseTool 实例。"""
    client = DeepxivTools(token=token)

    @tool
    def get_paper_brief(arxiv_id: str) -> str:
        """Get a brief summary of an arXiv paper including title, TLDR, keywords,
        citation count, GitHub URL, and publication date.

        Args:
            arxiv_id: arXiv paper ID, e.g. "2409.05591".
        """
        try:
            result = client.get_paper_brief(arxiv_id)
            return _truncate(str(result))
        except Exception as exc:
            return f"Error in get_paper_brief: {exc}"

    return get_paper_brief  # type: ignore[return-value]


def get_paper_head_tool(token: Optional[str] = None) -> BaseTool:
    """工厂函数：返回 get_paper_head BaseTool 实例。"""
    client = DeepxivTools(token=token)

    @tool
    def get_paper_head(arxiv_id: str) -> str:
        """Get detailed paper metadata and structure including title, abstract,
        authors, sections list, token count, categories, and publication date.

        Args:
            arxiv_id: arXiv paper ID, e.g. "2409.05591".
        """
        try:
            result = client.get_paper_head(arxiv_id)
            return _truncate(str(result))
        except Exception as exc:
            return f"Error in get_paper_head: {exc}"

    return get_paper_head  # type: ignore[return-value]


def get_paper_structure_tool(token: Optional[str] = None) -> BaseTool:
    """工厂函数：返回 get_paper_structure BaseTool 实例。"""
    client = DeepxivTools(token=token)

    @tool
    def get_paper_structure(arxiv_id: str) -> str:
        """Get the section structure of an arXiv paper. Returns a list of section
        names and the total token count. Useful for planning which sections to read.

        Args:
            arxiv_id: arXiv paper ID, e.g. "2409.05591".
        """
        try:
            result = client.get_paper_structure(arxiv_id)
            return _truncate(str(result))
        except Exception as exc:
            return f"Error in get_paper_structure: {exc}"

    return get_paper_structure  # type: ignore[return-value]


def read_section_tool(token: Optional[str] = None) -> BaseTool:
    """工厂函数：返回 read_section BaseTool 实例。"""
    client = DeepxivTools(token=token)

    @tool
    def read_section(arxiv_id: str, section_name: str) -> str:
        """Read a specific section from an arXiv paper. Section name matching is
        case-insensitive and supports partial matches.

        Args:
            arxiv_id: arXiv paper ID, e.g. "2409.05591".
            section_name: Name of the section to read, e.g. "Introduction", "Method".
        """
        try:
            content = client.read_section(arxiv_id, section_name)
            return _truncate(content)
        except Exception as exc:
            return f"Error in read_section: {exc}"

    return read_section  # type: ignore[return-value]


def get_full_paper_tool(token: Optional[str] = None) -> BaseTool:
    """工厂函数：返回 get_full_paper BaseTool 实例。"""
    client = DeepxivTools(token=token)

    @tool
    def get_full_paper(arxiv_id: str) -> str:
        """Get the full paper content in markdown format. This is a fallback option
        when section-by-section reading fails. Warning: may be very long.

        Args:
            arxiv_id: arXiv paper ID, e.g. "2409.05591".
        """
        try:
            content = client.get_full_paper(arxiv_id)
            return _truncate(content)
        except Exception as exc:
            return f"Error in get_full_paper: {exc}"

    return get_full_paper  # type: ignore[return-value]


def search_papers_tool(token: Optional[str] = None) -> BaseTool:
    """工厂函数：返回 search_papers BaseTool 实例。"""
    client = DeepxivTools(token=token)

    @tool
    def search_papers(query: str, size: int = 10) -> str:
        """Search for arXiv papers by keyword query. Returns a list of matching
        papers with their IDs, titles, and other metadata.

        Args:
            query: Search query string, e.g. "attention mechanism transformer".
            size: Number of results to return (1-100, default 10).
        """
        try:
            results = client.search_papers(query, size=size)
            return _truncate(str(results))
        except Exception as exc:
            return f"Error in search_papers: {exc}"

    return search_papers  # type: ignore[return-value]


def web_search_tool(token: Optional[str] = None) -> BaseTool:
    """工厂函数：返回 web_search BaseTool 实例。"""
    client = DeepxivTools(token=token)

    @tool
    def web_search(query: str) -> str:
        """Search the web using the DeepXiv web search endpoint. Useful for finding
        supplementary resources, code repositories, or related information.

        Args:
            query: Web search query string.
        """
        try:
            results = client.web_search(query)
            return _truncate(str(results))
        except Exception as exc:
            return f"Error in web_search: {exc}"

    return web_search  # type: ignore[return-value]
