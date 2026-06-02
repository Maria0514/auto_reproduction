"""paper_analysis 节点：以 ReAct agent 形式深度阅读并分析论文。"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from config import REACT_MAX_ROUNDS_PAPER_ANALYSIS
from core.errors import make_node_error
from core.react_base import _make_react_wrapper, extract_last_tool_result
from core.state import GlobalState, NodeError, PaperAnalysis
from core.tools.deepxiv_tools import (
    get_full_paper_tool,
    get_paper_structure_tool,
    read_section_tool,
    search_papers_tool,
)

logger = logging.getLogger(__name__)


NODE_NAME: str = "paper_analysis"


PAPER_ANALYSIS_SCHEMA: Dict[str, Any] = {
    # title 字段是 langchain_openai.with_structured_output 的强制要求（函数名）。
    "title": "PaperAnalysis",
    "description": "论文深度分析结果，paper_analysis 节点输出契约。",
    "type": "object",
    "properties": {
        "method_summary": {
            "type": "string",
            "description": "方法概述（中文主字段，自 Sprint 2 起语义反转为中文，给 planning/reporting 中文 prompt 消费）。",
        },
        "key_formulas": {"type": "array", "items": {"type": "string"}},
        "datasets": {"type": "array", "items": {"type": "string"}},
        "metrics": {"type": "array", "items": {"type": "string"}},
        "hyperparams": {"type": "object", "additionalProperties": True},
        "hardware_requirements": {
            "type": "string",
            "description": "硬件需求（中文主字段，自 Sprint 2 起语义反转为中文）。",
        },
        "framework": {"type": ["string", "null"]},
        "baseline_results": {"type": "object", "additionalProperties": True},
        "sections_read": {"type": "array", "items": {"type": "string"}},
        "analysis_notes": {"type": "string"},
        "method_summary_en": {
            "type": ["string", "null"],
            "description": "方法概述英文备份字段（给 coding 节点消费，避免中英混杂；Optional）。",
        },
        "hardware_requirements_en": {
            "type": ["string", "null"],
            "description": "硬件需求英文备份字段（Optional）。",
        },
    },
    "required": [
        "method_summary",
        "datasets",
        "metrics",
        "hyperparams",
        "sections_read",
        "analysis_notes",
    ],
    "additionalProperties": True,
}


# Prompt Cache 前缀治理（方案 A，参见架构文档 §2.6.6 / 技术架构文档 §10.5）：
# 下面的 _ANALYSIS_SYSTEM_PROMPT_BODY 是 SystemMessage 的稳定前缀部分。
# 严禁在此字符串中插入 arxiv_id / title / abstract / authors / categories 等
# 动态变量，否则会破坏多论文间的字节级前缀一致性，导致 Prompt Cache 失效。
# 动态上下文（arxiv_id、paper_meta）由 _build_analysis_system_prompt 拼接到
# 尾部独立段落 "--- 当前论文上下文 ---" 之后；自测会断言截去尾部段落后两篇
# 不同论文的 system prompt 主体完全相同。
_ANALYSIS_SYSTEM_PROMPT_BODY = """你是深度论文分析专家，专注于从 arXiv 论文中提取复现所需的关键技术信息。请基于工具反馈的内容产出符合 PaperAnalysis Schema 的结构化分析结果。

可用工具：
- get_paper_structure(arxiv_id): 返回论文章节结构（章节名列表与 token_count），用于制定阅读策略。
- read_section(arxiv_id, section_name): 按章节名读取论文内容，支持大小写不敏感和部分匹配。
- get_full_paper(arxiv_id): 返回论文全文 markdown（兜底，长度可能很大，慎用）。
- search_papers(query, size): 按关键词搜索 arXiv，可用于查找相关工作或基线论文。

工作策略（推荐顺序，agent 可自主调整）：
1. 先调用 get_paper_structure 了解章节结构，决定阅读优先级。
2. 按 Method -> Experiments -> Results -> Introduction 的优先级调用 read_section 渐进式读取。
3. 非标准章节名（如 "Our Framework" 代替 "Method"、"Ablation Study" 代替 "Results"）时，依据章节结构语义自主匹配最接近的章节。
4. 单个 read_section 调用失败（返回 "Error in read_section: ..." 或空内容）时，先尝试章节别名 / 模糊匹配，再考虑 get_full_paper 兜底，不要直接跳过关键章节。
5. 全部章节读取失败时调用 get_full_paper 兜底；仍失败则在 analysis_notes 中说明缺失原因。
6. 必要时通过 search_papers 补充基线论文或相关工作的信息（非必须，按需调用）。
7. 预算意识：max_rounds=12，正常 6-10 轮即可完成；不要重复调用同一 (tool, args)。

字段填充优先级：
- method_summary：来自 Method 章节；缺失时回退到 Introduction / abstract，并在 analysis_notes 中标注降级来源。
- key_formulas：来自 Method + Introduction，使用 LaTeX 字符串数组；无明确公式时给空数组。
- datasets / metrics / hyperparams / hardware_requirements / baseline_results：优先来自 Experiments / Results 章节。
- framework：从 Method / Introduction 中识别（PyTorch / TensorFlow / JAX 等），未提及时设为 null。
- sections_read：实际成功读取的章节名列表（与工具调用历史一致）。
- analysis_notes：自由文本，记录读取过程中的降级、缺失字段、不确定性等元信息。

输出要求：
- 完成数据收集后，必须在 <result>...</result> 标签内输出严格的 JSON，字段如下：
  {
    "method_summary": str,
    "key_formulas": [str, ...],
    "datasets": [str, ...],
    "metrics": [str, ...],
    "hyperparams": {...},
    "hardware_requirements": str,
    "framework": str | null,
    "baseline_results": {...},
    "sections_read": [str, ...],
    "analysis_notes": str
  }
- 缺失字段使用空字符串 / 空数组 / 空字典占位，不要捏造数据；不确定的信息请在 analysis_notes 中说明。
- 不要在 <result> 之外再夹杂任何其它 JSON 块。
- 若彻底无法获取论文内容（所有工具均失败），输出 {"error": "<原因>"} 并仍包在 <result>...</result> 中。
"""


# Sprint 2 追加：输出语言策略段落（架构 §2.6.2 / §4.5 首选方案 A）。
# 必须是 module-level 静态常量，禁止 f-string / 动态生成（R-PC4 字节级幂等）。
# 拼接在 _ANALYSIS_SYSTEM_PROMPT_BODY 主体之后、"--- 当前论文上下文 ---" 动态段落
# 之前。该常量字节稳定，论文间共享，前缀仍稳定到本段落末尾。一旦定稿，sp2 内部
# 不允许微调（架构 §4.8 冻结期；任何字节修改 = Prompt Cache 全 miss）。
_LANGUAGE_POLICY_SECTION = """--- 输出语言策略 ---
请在 <result> JSON 中按以下规则填写字段语言：
- method_summary：中文叙述（主字段，给 planning/reporting 中文 prompt 消费）；
- method_summary_en：英文叙述（备份字段，给 coding 节点消费，避免中英混杂）；
- hardware_requirements / hardware_requirements_en：同上；
- datasets / metrics / framework / sections_read：英文事实层，禁止翻译；
- analysis_notes：中文自由文本 + 英文机器标签（如 [DEGRADED] missing=...）。
"""


def _format_paper_context(arxiv_id: str, paper_meta: Optional[Dict[str, Any]]) -> str:
    """把 arxiv_id / paper_meta 渲染为稳定的尾部上下文段落。

    sort_keys + ensure_ascii=False 保证同一论文输入下字节级幂等；不同论文间
    主体仍然字节级一致（差异只出现在本段落内）。
    """
    payload: Dict[str, Any] = {"arxiv_id": arxiv_id}
    if isinstance(paper_meta, dict):
        # 只挑 ReAct agent 真正需要的几项，过滤掉 None 与 deepxiv 内部字段，避免污染。
        keep_keys = (
            "arxiv_id", "title", "authors", "abstract",
            "categories", "tldr", "keywords", "publish_date",
        )
        for key in keep_keys:
            value = paper_meta.get(key)
            if value not in (None, "", [], {}):
                payload[key] = value
    try:
        rendered = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError):
        rendered = str(payload)
    return rendered


def _build_analysis_system_prompt(context: Dict[str, Any]) -> str:
    """组装 paper_analysis 的 system prompt。

    Prompt Cache 前缀稳定化（方案 A）：
    - 主体 _ANALYSIS_SYSTEM_PROMPT_BODY 在不同论文间字节级一致。
    - 论文上下文（arxiv_id / paper_meta）放在尾部独立段落，便于人类阅读且
      不会污染主体前缀。
    - 自测必须断言截去尾部段落后两次返回值完全相同。
    """
    arxiv_id = str(context.get("arxiv_id") or "")
    paper_meta = context.get("paper_meta") if isinstance(context, dict) else None
    tail = _format_paper_context(arxiv_id, paper_meta)
    return (
        _ANALYSIS_SYSTEM_PROMPT_BODY                       # ← 主体冻结（sp1 字节级一致）
        + "\n" + _LANGUAGE_POLICY_SECTION                  # ← Sprint 2 追加（字节稳定常量）
        + "\n--- 当前论文上下文 ---\n"
        + tail                                              # ← 论文级动态
    )


# ---------- 类型补齐与字段兜底 helpers ----------


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
        return [_coerce_str(v) for v in value if v is not None and _coerce_str(v)]
    if isinstance(value, str):
        return [value] if value else []
    return [str(value)]


def _coerce_optional_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        return value or None
    return str(value)


def _coerce_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    return {}


def _build_paper_analysis(result: Dict[str, Any]) -> PaperAnalysis:
    return PaperAnalysis(
        method_summary=_coerce_str(result.get("method_summary")),
        key_formulas=_coerce_str_list(result.get("key_formulas")),
        datasets=_coerce_str_list(result.get("datasets")),
        metrics=_coerce_str_list(result.get("metrics")),
        hyperparams=_coerce_dict(result.get("hyperparams")),
        hardware_requirements=_coerce_str(result.get("hardware_requirements")),
        framework=_coerce_optional_str(result.get("framework")),
        baseline_results=_coerce_dict(result.get("baseline_results")),
        sections_read=_coerce_str_list(result.get("sections_read")),
        analysis_notes=_coerce_str(result.get("analysis_notes")),
        method_summary_en=_coerce_optional_str(result.get("method_summary_en")),
        hardware_requirements_en=_coerce_optional_str(
            result.get("hardware_requirements_en")
        ),
    )


def _missing_core_fields(analysis: PaperAnalysis) -> List[str]:
    """核心字段缺失检测：用于决定是否标记 degraded。

    method_summary 空 / datasets+metrics 全空 / sections_read 全空 -> 视为降级。
    """
    missing: List[str] = []
    if not analysis.get("method_summary"):
        missing.append("method_summary")
    if not analysis.get("datasets") and not analysis.get("metrics"):
        missing.append("datasets+metrics")
    if not analysis.get("sections_read"):
        missing.append("sections_read")
    return missing


def _backfill_analysis_from_tools(
    analysis: PaperAnalysis,
    react_messages: Optional[Any],
) -> PaperAnalysis:
    """从 ReAct 子图最终 messages 中的工具调用历史回填 PaperAnalysis 缺失字段。

    背景（BUG-S1-03）：LLM 偶发在最终 ``<result>`` JSON 中漏写 ``sections_read``
    字段（输出空数组），即便 ReAct 子图历史里已经成功调用了多次 ``read_section``。
    这与 BUG-S1-02 在 paper_intake 节点上的形态一致：LLM 漏写本应能从工具历史
    推导出的字段。当前函数提供节点层兜底，不依赖 LLM 服从度。

    策略：
    - 扫描 ``react_messages`` 中所有 ``name=read_section`` 的成功 ToolMessage
      （过滤掉 ``Error in ...`` / ``tool ... raised ...`` 前缀的失败结果）；
    - 配对其前序 AIMessage.tool_calls 中同 ``id`` 的 ``read_section`` 调用，
      抽取 ``args.section_name``；
    - 在 LLM 输出的 ``sections_read`` 为空时回填工具历史中已读章节；
    - method_summary 严格漏写时也用第一段成功 ``read_section`` 内容作为兜底
      （仅在分析能给出意义最小的非空摘要时）。
    """
    if not react_messages:
        return analysis

    # 局部导入避免循环依赖
    try:
        from langchain_core.messages import AIMessage, ToolMessage
    except Exception:  # pragma: no cover - defensive
        return analysis

    # 首先看看工具历史里是否有 read_section ToolMessage
    msgs = list(react_messages)
    read_section_called = any(
        isinstance(m, ToolMessage) and getattr(m, "name", None) == "read_section"
        for m in msgs
    )
    if not read_section_called:
        return analysis

    # 收集所有 tool_calls：{call_id -> (tool_name, args)}
    tool_call_index: Dict[str, Dict[str, Any]] = {}
    for m in msgs:
        if not isinstance(m, AIMessage):
            continue
        calls = getattr(m, "tool_calls", None) or []
        for call in calls:
            if isinstance(call, dict):
                name = call.get("name")
                args = call.get("args") or {}
                cid = call.get("id")
            else:
                name = getattr(call, "name", None)
                args = getattr(call, "args", {}) or {}
                cid = getattr(call, "id", None)
            if cid:
                tool_call_index[cid] = {"name": name, "args": args}

    # 扫描成功的 read_section ToolMessage，按出现顺序记录 section_name + content
    successful_reads: List[Dict[str, Any]] = []
    seen_section_names: set = set()
    for m in msgs:
        if not isinstance(m, ToolMessage):
            continue
        if getattr(m, "name", None) != "read_section":
            continue
        content = getattr(m, "content", "")
        if isinstance(content, list):
            # content parts 兼容
            content = "".join(
                c if isinstance(c, str) else (c.get("text") or "")
                if isinstance(c, dict) else ""
                for c in content
            )
        if not isinstance(content, str):
            content = str(content)
        content_strip = content.strip()
        # 过滤失败 ToolMessage：deepxiv_tools 工厂层返回 "Error in read_section: ..."；
        # react_base.tool_executor_node 异常分支写入 "tool <name> raised ...".
        if (not content_strip
                or content_strip.startswith("Error in ")
                or content_strip.startswith("tool ")):
            continue

        tool_call_id = getattr(m, "tool_call_id", None)
        section_name: Optional[str] = None
        if tool_call_id and tool_call_id in tool_call_index:
            args = tool_call_index[tool_call_id].get("args") or {}
            section_name = _coerce_str(args.get("section_name")) or None
        if not section_name:
            # 无法配对到 tool_call args 时跳过该条（不胡乱构造章节名）
            continue
        if section_name in seen_section_names:
            continue
        seen_section_names.add(section_name)
        successful_reads.append({
            "section_name": section_name,
            "content": content_strip,
        })

    if not successful_reads:
        logger.warning(
            "[%s] backfill skipped: read_section ToolMessage exists but no "
            "successful (section_name, content) pair could be derived",
            NODE_NAME,
        )
        return analysis

    # 回填 sections_read：LLM 漏写空数组但工具历史非空 → 用工具历史填充
    if not analysis.get("sections_read"):
        recovered = [r["section_name"] for r in successful_reads]
        logger.info(
            "[%s] backfill sections_read from tool history: %s",
            NODE_NAME, recovered,
        )
        analysis["sections_read"] = recovered

    # method_summary 严格漏写时的兜底：仅在完全为空时，用第一段成功 read_section
    # 内容的前若干字符作为最小占位摘要，并在 analysis_notes 标记降级来源。
    if not analysis.get("method_summary"):
        first = successful_reads[0]
        snippet = first["content"][:800]
        logger.info(
            "[%s] backfill method_summary from first read_section (%s, len=%d)",
            NODE_NAME, first["section_name"], len(snippet),
        )
        analysis["method_summary"] = snippet

    return analysis


def _backfill_en_fields(
    analysis: PaperAnalysis,
    degraded_nodes: List[str],
    node_errors: List[NodeError],
) -> bool:
    """LLM 漏写 *_en 英文备份字段时回退对应中文主字段值并标记 degraded（架构 §2.6.3）。

    沿用 BUG-S1-02 / BUG-S1-03 治理范式：非静默——触发回退时写 degraded NodeError +
    打 WARNING 日志。**严禁引入二次 LLM 翻译调用**（PRD §4.7.4 硬约束），仅做主字段兜底。

    Args:
        analysis: 待回填的 PaperAnalysis（原地修改）。
        degraded_nodes: degraded 节点列表（原地去重追加）。
        node_errors: NodeError 列表（原地追加一条 degraded 记录）。

    Returns:
        True 表示触发了至少一项回退。
    """
    fell_back = False
    if not analysis.get("method_summary_en") and analysis.get("method_summary"):
        analysis["method_summary_en"] = analysis["method_summary"]
        fell_back = True
    if (not analysis.get("hardware_requirements_en")
            and analysis.get("hardware_requirements")):
        analysis["hardware_requirements_en"] = analysis["hardware_requirements"]
        fell_back = True
    if fell_back:
        if NODE_NAME not in degraded_nodes:
            degraded_nodes.append(NODE_NAME)
        node_errors.append(
            make_node_error(
                NODE_NAME,
                "degraded",
                "LLM 漏写英文备份字段 *_en，已回退中文主字段值（未触发二次翻译）",
                None,
            )
        )
        logger.warning(
            "[%s] *_en 字段缺失回退为中文主字段（避免静默吞错，未触发二次 LLM 翻译）",
            NODE_NAME,
        )
    return fell_back


def _map_analysis_result(
    result: Optional[Dict[str, Any]],
    state: GlobalState,
    react_messages: Optional[Any] = None,
) -> dict:
    """将 ReAct 子图结果映射为 GlobalState 局部更新。

    - 空结果 / error 字段：写入 error + node_errors。
    - 正常结果：构造 PaperAnalysis；核心字段缺失时优先从 ReAct 工具调用历史回填
      （参见 :func:`_backfill_analysis_from_tools`），仍不足时才加入 degraded_nodes。

    ``react_messages`` 由 ``_make_react_wrapper`` 通过 ``inspect`` 检测自动注入，
    调用方不需关心；2 参签名仍保持兼容（既有单测）。
    """
    node_errors = list(state.get("node_errors", []))
    degraded_nodes = list(state.get("degraded_nodes", []))

    if not result or not isinstance(result, dict):
        message = "paper_analysis ReAct agent 未返回有效结果"
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
        message = _coerce_str(error_msg) or "paper_analysis 未能完成论文分析"
        logger.error("[%s] agent 报告错误: %s", NODE_NAME, message)
        node_errors.append(
            make_node_error(NODE_NAME, "permanent", message, None)
        )
        return {
            "error": message,
            "current_step": NODE_NAME,
            "node_errors": node_errors,
        }

    analysis = _build_paper_analysis(result)

    # 工具历史回填兜底（BUG-S1-03 修复）：LLM 偶发漏写 sections_read（≈25%
    # 复现率），但 ReAct 子图已经成功调用过 read_section。此处先从工具调用
    # 历史回填可推导字段，再判定 degraded，避免误标记。
    analysis = _backfill_analysis_from_tools(analysis, react_messages)

    # Sprint 2：LLM 漏写 *_en 英文备份字段时回退中文主字段值（非静默 + degraded 标记）。
    # 放在工具历史回填之后——此时 method_summary 已尽可能完整，再做 en 兜底。
    _backfill_en_fields(analysis, degraded_nodes, node_errors)

    missing = _missing_core_fields(analysis)
    if missing:
        logger.warning(
            "[%s] 核心字段缺失，标记 degraded: missing=%s",
            NODE_NAME, missing,
        )
        if NODE_NAME not in degraded_nodes:
            degraded_nodes.append(NODE_NAME)
        node_errors.append(
            make_node_error(
                NODE_NAME,
                "degraded",
                f"paper_analysis 部分字段缺失: {', '.join(missing)}",
                None,
            )
        )
        # 在 analysis_notes 中追加机器可读的降级标记，便于下游节点感知
        note_prefix = analysis.get("analysis_notes") or ""
        marker = f"[DEGRADED] missing={','.join(missing)}"
        if marker not in note_prefix:
            analysis["analysis_notes"] = (
                f"{marker}\n{note_prefix}" if note_prefix else marker
            )

    logger.info(
        "[%s] 完成: sections_read=%d, method_len=%d, degraded=%s",
        NODE_NAME,
        len(analysis.get("sections_read") or []),
        len(analysis.get("method_summary") or ""),
        bool(missing),
    )

    return {
        "paper_analysis": analysis,
        "current_step": NODE_NAME,
        "node_errors": node_errors,
        "degraded_nodes": degraded_nodes,
    }


# ReAct wrapper：把 GlobalState ↔ ReActState 双向映射 + 子图编译 + 预算扣减
# 都封装好，主图直接 import 该 callable 注册节点即可。
_react_wrapper = _make_react_wrapper(
    node_name=NODE_NAME,
    build_context=lambda state: {
        "arxiv_id": state["paper_meta"]["arxiv_id"],
        "paper_meta": state["paper_meta"],
    },
    build_system_prompt=_build_analysis_system_prompt,
    get_tools=lambda state: [
        get_paper_structure_tool(),
        read_section_tool(),
        get_full_paper_tool(),
        search_papers_tool(),
    ],
    map_result=_map_analysis_result,
    max_rounds=REACT_MAX_ROUNDS_PAPER_ANALYSIS,
    result_schema=PAPER_ANALYSIS_SCHEMA,
)


def paper_analysis(state: GlobalState) -> dict:
    """主图节点入口：先做前置校验，再委托给 ReAct wrapper。

    `build_context` 直接读 ``state["paper_meta"]["arxiv_id"]``，若 paper_meta 为
    None 会触发 TypeError；这里前置拦截，统一返回 error + NodeError，避免污染
    上游 paper_intake 失败路径的语义。
    """
    if not state.get("paper_meta"):
        message = "paper_analysis 前置校验失败：paper_meta 为空"
        logger.error("[%s] %s", NODE_NAME, message)
        node_errors = list(state.get("node_errors", []))
        node_errors.append(make_node_error(NODE_NAME, "permanent", message, None))
        return {
            "error": message,
            "current_step": NODE_NAME,
            "node_errors": node_errors,
        }
    return _react_wrapper(state)


paper_analysis.__name__ = f"react_wrapper_{NODE_NAME}"
