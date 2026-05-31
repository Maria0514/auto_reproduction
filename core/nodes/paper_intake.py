"""paper_intake 节点：以 ReAct agent 形式从用户输入获取论文元数据。"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from core.errors import make_node_error
from core.react_base import _make_react_wrapper, extract_last_tool_result
from core.state import GlobalState, PaperMeta
from core.tools.deepxiv_tools import (
    get_paper_brief_tool,
    get_paper_head_tool,
    search_papers_tool,
)

logger = logging.getLogger(__name__)


NODE_NAME: str = "paper_intake"


PAPER_META_SCHEMA: Dict[str, Any] = {
    # title 字段是 langchain_openai.with_structured_output 的强制要求（函数名）。
    "title": "PaperMeta",
    "description": "arXiv 论文元数据，paper_intake 节点输出契约。",
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
        "title_zh": {"type": ["string", "null"]},
        "abstract_zh": {"type": ["string", "null"]},
        "tldr_zh": {"type": ["string", "null"]},
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
4. 字段合并规则（每一项都要完整写入最终 JSON，不得遗漏）：
   - arxiv_id 来自 brief（无则用清洗后的输入）。
   - title 优先 brief，其次 head。
   - authors 优先 head，其次 brief。
   - abstract 优先 head，其次 brief。
   - categories 优先 head，其次 brief；当 get_paper_head 返回的 categories 为非空数组时，最终 JSON 的 categories 必须包含 head 返回的全部分类，禁止输出空数组。
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


def _backfill_paper_meta_from_tools(
    paper_meta: PaperMeta,
    react_messages: Optional[Any],
) -> PaperMeta:
    """从 ReAct 子图最终 messages 中的工具调用历史回填 PaperMeta 缺失字段。

    背景：LLM 在输入需要清洗时（URL / 带版本号 ID），可能在最终 ``<result>``
    JSON 中漏写 ``categories`` 字段（输出 ``[]``），即便 ``get_paper_head``
    已成功返回 ``categories=['cs.CL', 'cs.AI']``。架构 §2.8.2 明确"head 优先"
    契约：head 已返回 categories 时不应被 LLM 服从度问题覆盖为空。

    本函数只做"head 工具结果回填"——head 优先字段（authors/abstract/categories）
    若为空而 head 实际返回了非空值，则用 head 值兜底。
    """
    if not react_messages:
        return paper_meta

    head_dict = extract_last_tool_result(react_messages, "get_paper_head")
    if not head_dict or not isinstance(head_dict, dict):
        # BUG-S1-02 复盘：head ToolMessage 存在但解析失败时必须告警，避免静默吞错。
        # 只有"工具确实被调用过"才发 WARNING；未调用时不告警（属正常路径）。
        try:
            from langchain_core.messages import ToolMessage  # 局部导入避免循环
            head_called = any(
                isinstance(m, ToolMessage) and getattr(m, "name", None) == "get_paper_head"
                for m in react_messages
            )
        except Exception:  # pragma: no cover - defensive
            head_called = False
        if head_called:
            reason = (
                "head_dict is None (extract_last_tool_result returned None — "
                "ToolMessage content unparseable)"
                if head_dict is None
                else f"head_dict is not dict (got {type(head_dict).__name__})"
            )
            logger.warning(
                "[%s] backfill skipped: tool=%s, reason=%s",
                NODE_NAME, "get_paper_head", reason,
            )
        return paper_meta

    # head 优先字段：categories / authors / abstract（架构 §2.8.2 表格 L1885-1887）
    head_categories = _coerce_str_list(head_dict.get("categories"))
    if not paper_meta.get("categories") and head_categories:
        logger.info(
            "[%s] backfill categories from get_paper_head tool result: %s",
            NODE_NAME, head_categories,
        )
        paper_meta["categories"] = head_categories

    head_authors = _coerce_str_list(head_dict.get("authors"))
    if not paper_meta.get("authors") and head_authors:
        logger.info(
            "[%s] backfill authors from get_paper_head tool result (%d items)",
            NODE_NAME, len(head_authors),
        )
        paper_meta["authors"] = head_authors

    head_abstract = _coerce_str(head_dict.get("abstract"))
    if not paper_meta.get("abstract") and head_abstract:
        logger.info(
            "[%s] backfill abstract from get_paper_head tool result (len=%d)",
            NODE_NAME, len(head_abstract),
        )
        paper_meta["abstract"] = head_abstract

    return paper_meta


def _map_intake_result(
    result: Optional[Dict[str, Any]],
    state: GlobalState,
    react_messages: Optional[Any] = None,
) -> dict:
    """将 ReAct 子图结果映射为 GlobalState 局部更新。

    react_messages 为 ReAct 子图运行结束时的 messages 列表，用于在 LLM 漏写
    必填字段时从工具调用历史兜底（参见 ``_backfill_paper_meta_from_tools``）。
    该参数由 ``_make_react_wrapper`` 自动通过 inspect 检测注入，调用方无需关心。
    """
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

    # head 工具结果回填兜底（针对 LLM 在清洗类输入下漏写 categories 的服从度问题）
    paper_meta = _backfill_paper_meta_from_tools(paper_meta, react_messages)

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
