"""paper_intake 节点：以 ReAct agent 形式从用户输入获取论文元数据。"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from core.errors import make_node_error
from core.react_base import _make_react_wrapper
from core.state import GlobalState, PaperMeta
from core.tools.deepxiv_tools import (
    get_paper_brief_tool,
    get_paper_head_tool,
    search_papers_tool,
)

logger = logging.getLogger(__name__)


NODE_NAME: str = "paper_intake"


PAPER_META_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "arxiv_id": {"type": "string"},
        "title": {"type": "string"},
        "authors": {"type": "array", "items": {"type": "string"}},
        "abstract": {"type": "string"},
        "categories": {"type": "array", "items": {"type": "string"}},
        "tldr": {"type": ["string", "null"]},
        "keywords": {
            "type": ["array", "null"],
            "items": {"type": "string"},
        },
        "citation_count": {"type": ["integer", "null"]},
        "github_url": {"type": ["string", "null"]},
        "publish_date": {"type": ["string", "null"]},
        "pdf_url": {"type": ["string", "null"]},
        "notes": {"type": ["string", "null"]},
    },
    "required": ["arxiv_id", "title", "authors", "abstract", "categories"],
    "additionalProperties": True,
}


_INTAKE_SYSTEM_PROMPT = """你是论文元数据获取专家。任务是从用户给出的输入中确定一篇 arXiv 论文，并产出符合 PaperMeta Schema 的完整元数据。

可用工具：
- get_paper_brief(arxiv_id): 返回论文快速摘要（title/tldr/keywords/citations/github_url/publish_at/src_url 等）。
- get_paper_head(arxiv_id): 返回论文头部元数据（title/authors/abstract/categories/sections/publish_at 等）。
- search_papers(query, size): 按关键词搜索 arXiv，返回候选论文列表（含 arxiv_id）。

工作策略（按以下顺序自主决策）：
1. 输入清洗：
   - 若输入是完整 URL（如 https://arxiv.org/abs/<NEW_ID> 或 .pdf 链接），抽取末尾 arXiv ID。
   - 若带版本号（如 <NEW_ID>v2），去除版本后缀。
   - 若是旧格式 ID（如 cs/<OLD_ID>），直接保留原始形式。
   - 若输入像论文标题或关键词，先用 search_papers 找到匹配的 arxiv_id。
2. 获取元数据：先调用 get_paper_brief 获取快速摘要；若 brief 失败或字段不足（缺 authors/abstract/categories），再调用 get_paper_head 补充。
3. 失败回退：若 brief 因论文不存在失败，且输入像 ID，尝试去除版本号后重试；仍失败则改用 search_papers 搜索相近标题；如最终仍无法获取，直接输出 error 字段说明。
4. 字段合并规则：
   - arxiv_id 来自 brief（无则用清洗后的输入）。
   - title 优先 brief，其次 head。
   - authors / abstract / categories 优先 head，其次 brief。
   - tldr / keywords / citation_count（来自 brief.citations）/ github_url / pdf_url（来自 brief.src_url）来自 brief。
   - publish_date 优先 brief.publish_at，其次 head.publish_at。
5. 学科范围校验：检查 categories 是否包含以 "cs." 开头的分类；若不属于 CS 领域，在结果 notes 字段中写一行 WARNING 说明（不要因此中断流程）。
6. 预算意识：max_rounds=5，正常路径 2-3 轮即可完成；不要重复调用同一工具。

输出要求：
- 完成数据收集后，必须在 <result>...</result> 标签内输出严格的 JSON，字段如下：
  {
    "arxiv_id": str,
    "title": str,
    "authors": [str, ...],
    "abstract": str,
    "categories": [str, ...],
    "tldr": str | null,
    "keywords": [str, ...] | null,
    "citation_count": int | null,
    "github_url": str | null,
    "publish_date": str | null,
    "pdf_url": str | null,
    "notes": str | null
  }
- 缺失字段用 null（数组用 null 或空数组均可），不要捏造数据。
- 不要在 <result> 之外再夹杂任何其它 JSON 块。
- 若彻底无法获取论文，输出 {"error": "<原因>", "arxiv_id": "<清洗后的输入或原输入>"} 并仍包在 <result>...</result> 中。
"""


def _build_intake_system_prompt(context: Dict[str, Any]) -> str:
    """返回固定 system prompt 模板。

    Prompt Cache 前缀稳定化：context 中的 user_input / input_type 由 react_base 在
    HumanMessage 中携带，这里不拼接任何动态内容，保证 SystemMessage 字节级幂等。
    """
    return _INTAKE_SYSTEM_PROMPT


def _coerce_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def _coerce_str_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [_coerce_str(v) for v in value if v is not None]
    if isinstance(value, str):
        return [value] if value else []
    return [str(value)]


def _coerce_optional_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        return value or None
    return str(value)


def _coerce_optional_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_optional_str_list(value: Any) -> Optional[List[str]]:
    if value is None:
        return None
    if isinstance(value, list):
        items = [_coerce_str(v) for v in value if v is not None and _coerce_str(v)]
        return items or None
    if isinstance(value, str):
        return [value] if value else None
    return None


def _build_paper_meta(result: Dict[str, Any], fallback_arxiv_id: str) -> PaperMeta:
    arxiv_id = _coerce_str(result.get("arxiv_id")) or fallback_arxiv_id
    citation_count = _coerce_optional_int(
        result.get("citation_count", result.get("citations"))
    )
    publish_date = _coerce_optional_str(
        result.get("publish_date", result.get("publish_at"))
    )
    pdf_url = _coerce_optional_str(result.get("pdf_url", result.get("src_url")))
    return PaperMeta(
        arxiv_id=arxiv_id,
        title=_coerce_str(result.get("title")),
        authors=_coerce_str_list(result.get("authors")),
        abstract=_coerce_str(result.get("abstract")),
        categories=_coerce_str_list(result.get("categories")),
        tldr=_coerce_optional_str(result.get("tldr")),
        keywords=_coerce_optional_str_list(result.get("keywords")),
        citation_count=citation_count,
        github_url=_coerce_optional_str(result.get("github_url")),
        publish_date=publish_date,
        pdf_url=pdf_url,
    )


def _map_intake_result(
    result: Optional[Dict[str, Any]],
    state: GlobalState,
) -> dict:
    """将 ReAct 子图结果映射为 GlobalState 局部更新。"""
    node_errors = list(state.get("node_errors", []))
    fallback_arxiv_id = (state.get("user_input") or "").strip()

    if not result or not isinstance(result, dict):
        message = "paper_intake ReAct agent 未返回有效结果"
        logger.error("[%s] %s", NODE_NAME, message)
        node_errors.append(
            make_node_error(NODE_NAME, "permanent", message, None)
        )
        return {
            "error": message,
            "current_step": NODE_NAME,
            "node_errors": node_errors,
        }

    error_msg = result.get("error")
    if error_msg:
        message = _coerce_str(error_msg) or "paper_intake 未能获取论文元数据"
        logger.error("[%s] agent 报告错误: %s", NODE_NAME, message)
        node_errors.append(
            make_node_error(NODE_NAME, "permanent", message, None)
        )
        return {
            "error": message,
            "current_step": NODE_NAME,
            "node_errors": node_errors,
        }

    paper_meta = _build_paper_meta(result, fallback_arxiv_id)

    if not paper_meta["arxiv_id"] or not paper_meta["title"]:
        message = "paper_intake 结果缺少 arxiv_id 或 title"
        logger.error(
            "[%s] %s: arxiv_id=%r, title=%r",
            NODE_NAME, message, paper_meta["arxiv_id"], paper_meta["title"],
        )
        node_errors.append(
            make_node_error(NODE_NAME, "permanent", message, None)
        )
        return {
            "error": message,
            "current_step": NODE_NAME,
            "node_errors": node_errors,
        }

    categories = paper_meta.get("categories") or []
    if categories and not any(c.lower().startswith("cs.") for c in categories):
        logger.warning(
            "[%s] 论文非 CS 领域: categories=%s，复现效果可能不佳",
            NODE_NAME, categories,
        )

    logger.info(
        "[%s] 完成: arxiv_id=%s, title=%s",
        NODE_NAME, paper_meta["arxiv_id"], (paper_meta["title"] or "")[:60],
    )

    return {
        "paper_meta": paper_meta,
        "current_step": NODE_NAME,
        "node_errors": node_errors,
    }


paper_intake = _make_react_wrapper(
    node_name=NODE_NAME,
    build_context=lambda state: {
        "user_input": state["user_input"],
        "input_type": state.get("input_type", "arxiv_id"),
    },
    build_system_prompt=_build_intake_system_prompt,
    get_tools=lambda state: [
        get_paper_brief_tool(),
        get_paper_head_tool(),
        search_papers_tool(),
    ],
    map_result=_map_intake_result,
    max_rounds=5,
    result_schema=PAPER_META_SCHEMA,
)
