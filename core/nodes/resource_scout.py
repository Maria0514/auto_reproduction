"""resource_scout 节点（S2-02）：以 ReAct agent 形式搜集并评估论文对应代码仓库。

节点形态：纯 ReAct wrapper（通过 _make_react_wrapper 工厂生成 callable）。
搜索优先级链（架构 §2.3.1 / PRD §2.2）：
    deepxiv github_url -> Papers With Code -> Web Search -> 全部失败降级 from_scratch。

治理范式（与 sp1 BUG-S1-02 / BUG-S1-03 一致）：
    - 工具 ToolMessage 序列化均为合法 JSON（工厂层已落地，本节点不做 str(dict)）；
    - _map_resource_scout_result 用 3 参签名（含 react_messages），工具历史回填兜底；
    - degraded 标记 / 回填失败均打 WARNING 日志，非静默吞错；
    - system prompt 主体字节冻结，论文级动态上下文走 HumanMessage 通道（前缀稳定）。
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from config import REACT_MAX_ROUNDS_RESOURCE_SCOUT
from core.errors import make_node_error
from core.react_base import _make_react_wrapper
from core.state import GlobalState, NodeError, RepoInfo, ResourceInfo
from core.tools.deepxiv_tools import (
    get_paper_brief_tool,
    search_papers_tool,
    web_search_tool,
)
from core.tools.git_tools import (
    make_check_url_reachable_tool,
    make_git_clone_and_analyze_tool,
)
from core.tools.pwc_tools import make_search_pwc_tool

logger = logging.getLogger(__name__)


NODE_NAME: str = "resource_scout"

# git clone+analyze 复合工具的 @tool 函数名（ToolMessage.name），用于工具历史回填配对。
_GIT_CLONE_TOOL_NAME: str = "git_clone_and_analyze"

# backfill 回填 RepoInfo 时的默认 quality_score（degraded 兜底，无 LLM 评分时使用）。
_BACKFILL_DEFAULT_QUALITY: float = 0.5

# quality_score 全部低于此阈值时写 [QUALITY_WARN]（仍照常推荐 repos[0]）。
_QUALITY_WARN_THRESHOLD: float = 0.3

_VALID_STRATEGIES = ("use_repo", "hybrid", "from_scratch")


RESOURCE_SCOUT_SCHEMA: Dict[str, Any] = {
    # title 字段是 langchain_openai.with_structured_output 的强制要求（函数名）。
    "title": "ResourceInfo",
    "description": "资源搜集与评估结果，resource_scout 节点输出契约。",
    "type": "object",
    "properties": {
        "repos": {"type": "array", "items": {"type": "object"}},
        "selected_repo": {"type": ["object", "null"]},
        "external_resources": {"type": "array"},
        "resource_strategy": {
            "type": "string",
            "enum": list(_VALID_STRATEGIES),
        },
        # 可选：agent 自报告字段，用于 _map_*_result 写 analysis_notes（不属于 ResourceInfo）。
        "search_log": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["repos", "selected_repo", "resource_strategy"],
    "additionalProperties": True,
}


# Prompt Cache 前缀治理（方案 A，参见架构文档 §2.3.1 / §2.6.6）：
# 下面的 _RESOURCE_SCOUT_SYSTEM_PROMPT_BODY 是 SystemMessage 的稳定前缀部分。
# 严禁在此字符串中插入 arxiv_id / title / authors / github_url 等任何论文级动态变量，
# 否则会破坏多论文间的字节级前缀一致性，导致 Prompt Cache 失效。论文级动态上下文
# 由 _make_react_wrapper 通过 build_context 走 HumanMessage 通道注入（前缀稳定到主体末尾）。
# 自测会断言不同论文输入下本主体字节级一致（CP-B2-10）。
_RESOURCE_SCOUT_SYSTEM_PROMPT_BODY = """你是资源搜集与评估专家。任务是根据论文元数据，找到论文对应的开源代码仓库并评估质量。

可用工具：
- check_url_reachable_tool(url): HTTP HEAD 探测 URL 是否可达，用于在昂贵的 git clone 前过滤死链。
- git_clone_and_analyze(url): 浅克隆仓库并分析，返回 RepoInfo（含 local_path / commit 指标 / 目录结构 / README/requirements 检测）；失败返回 {"success": false, "error": ...}。
- search_pwc(arxiv_id, title): 调用 Papers With Code，按 arxiv_id（优先）或 title 检索论文对应的代码仓库候选。
- search_papers(query, size): 按关键词搜索 arXiv（可用于查相关工作 / 基线，辅助理解仓库相关性）。
- get_paper_brief(arxiv_id): 读取论文摘要信息（含 github_url 等），用于补全检索线索。
- web_search(query): 通用网页搜索，兜底寻找代码仓库 URL。

【搜索优先级链】（按顺序，前一步已得到可用仓库时可提前收敛）
1. deepxiv github_url -- 若 paper_meta.github_url 非空，先用 check_url_reachable_tool 校验可达性，
   再用 git_clone_and_analyze 克隆并取得 RepoInfo；
2. Papers With Code -- 用论文 title（英文主字段）或 arxiv_id 调用 search_pwc 补充候选仓库；
   如返回 GitHub URL，按步骤 1 流程克隆；
3. Web Search -- 用 title + framework + "code" / "github" 等关键词调用 web_search 兜底；
   找到候选 GitHub URL 后同样走克隆流程；
4. 全部失败 -- 在 <result> 中输出 resource_strategy = "from_scratch"，repos = []，selected_repo = null。

【质量评分（你给每个克隆成功的仓库打 0.0~1.0 分，写入 RepoInfo.quality_score）】
权重建议（最终自由判断）：
- is_official（owner 与 paper_meta.authors 重叠则判定 True）-- 权重 0.35
- last_commit_date（近半年；为 None 表示读不到数据，按缺失处理不加分，勿当最旧）-- 权重 0.20
- commit_count_recent（>=10 加分；为 None 表示读不到数据，用 is None 判缺失，勿当 0 活跃）-- 权重 0.15
- has_readme + has_requirements -- 权重 0.15
- dir_structure 含 src/ models/ train.py 等 ML 标准目录 -- 权重 0.15

【策略选择】
- 找到高质量官方仓库 -> resource_strategy = "use_repo"，selected_repo 设为最佳仓库；
- 找到仓库但质量一般 / 需大量适配 -> "hybrid"；
- 无任何可用仓库 -> "from_scratch"，repos=[]，selected_repo=null。

【字段填充要求】
- repos：所有克隆成功并评估过的仓库的 RepoInfo 列表（含 url / source / is_official / quality_score / local_path 等）；
- selected_repo：从 repos 中挑出的最佳候选（通常 quality_score 最高的那个），无仓库时为 null；
- external_resources：补充资源（数据集主页 / 预训练权重链接等），无则空数组；
- resource_strategy：use_repo / hybrid / from_scratch 三选一；
- search_log：自由记录你的检索过程与判定依据（如 is_official 判定理由），便于人类审核。

【输出格式】
- 完成搜集后，必须在 <result>...</result> 标签内输出严格 JSON，字段如下：
  {
    "repos": [ {RepoInfo}, ... ],
    "selected_repo": {RepoInfo} | null,
    "external_resources": [ {...}, ... ],
    "resource_strategy": "use_repo" | "hybrid" | "from_scratch",
    "search_log": [str, ...]
  }
- RepoInfo 字段：url, source, is_official, stars, forks, last_commit_date, commit_count_recent,
  has_readme, has_requirements, dir_structure, quality_score, local_path。
- 不要捏造仓库；未克隆成功的候选不要放进 repos。
- 不要在 <result> 之外再夹杂任何其它 JSON 块。
"""


# resource_scout 检索真正需要的论文级字段（英文事实层，PRD §4.7.5）。
_KEEP_META_KEYS = (
    "arxiv_id", "title", "authors", "github_url", "keywords",
)
_KEEP_ANALYSIS_KEYS = (
    "framework", "datasets", "categories",
)


def _format_resource_scout_context(
    paper_meta: Optional[Dict[str, Any]],
    paper_analysis: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """提取 ReAct agent 检索必需的字段（PRD §4.7.5：一律英文事实层）。

    只挑检索真正用到的几项，过滤 None / 空值，避免污染 HumanMessage 上下文。
    返回 dict，由 _make_react_wrapper 用 json.dumps(sort_keys=True) 渲染为
    HumanMessage（同一论文输入下字节级幂等）。
    """
    payload: Dict[str, Any] = {}
    if isinstance(paper_meta, dict):
        for key in _KEEP_META_KEYS:
            value = paper_meta.get(key)
            if value not in (None, "", [], {}):
                payload[key] = value
    if isinstance(paper_analysis, dict):
        for key in _KEEP_ANALYSIS_KEYS:
            value = paper_analysis.get(key)
            if value not in (None, "", [], {}):
                payload[key] = value
    return payload


def _build_resource_scout_system_prompt(context: Dict[str, Any]) -> str:
    """组装 resource_scout 的 system prompt。

    Prompt Cache 前缀稳定化（方案 A）：主体 _RESOURCE_SCOUT_SYSTEM_PROMPT_BODY 在不同
    论文间字节级一致；论文级动态上下文不进 system prompt，由 build_context 走
    HumanMessage 通道注入（与 paper_intake 同款，paper_analysis 用尾部段落是因为它额外
    需要把上下文放在 system 提示里强调，本节点不需要）。
    """
    return _RESOURCE_SCOUT_SYSTEM_PROMPT_BODY


# ---------- 类型补齐 helpers ----------


def _coerce_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


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


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes")
    return bool(value)


def _coerce_float(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    if isinstance(value, bool):
        return float(int(value))
    try:
        f = float(value)
    except (TypeError, ValueError):
        return default
    # 钳制到 [0.0, 1.0]
    if f < 0.0:
        return 0.0
    if f > 1.0:
        return 1.0
    return f


def _coerce_optional_str_list(value: Any) -> Optional[List[str]]:
    if value is None:
        return None
    if isinstance(value, list):
        return [_coerce_str(v) for v in value if v is not None]
    if isinstance(value, str):
        return [value] if value else []
    return [str(value)]


def _build_repo_info(raw: Dict[str, Any]) -> RepoInfo:
    """把任意来源（LLM <result> / 工具历史）的 dict 规整为严格 RepoInfo。"""
    return RepoInfo(
        url=_coerce_str(raw.get("url")),
        source=_coerce_str(raw.get("source")) or "unknown",
        is_official=_coerce_bool(raw.get("is_official")),
        stars=_coerce_optional_int(raw.get("stars")),
        forks=_coerce_optional_int(raw.get("forks")),
        last_commit_date=_coerce_optional_str(raw.get("last_commit_date")),
        commit_count_recent=_coerce_optional_int(raw.get("commit_count_recent")),
        has_readme=_coerce_bool(raw.get("has_readme")),
        has_requirements=_coerce_bool(raw.get("has_requirements")),
        dir_structure=_coerce_optional_str_list(raw.get("dir_structure")),
        quality_score=_coerce_float(raw.get("quality_score"), 0.0),
        local_path=_coerce_optional_str(raw.get("local_path")),
    )


def _build_resource_info(result: Dict[str, Any]) -> ResourceInfo:
    """从 LLM <result> 构造 ResourceInfo（不含 backfill / degraded 逻辑）。"""
    raw_repos = result.get("repos")
    repos: List[RepoInfo] = []
    if isinstance(raw_repos, list):
        for item in raw_repos:
            if isinstance(item, dict):
                repos.append(_build_repo_info(item))

    raw_selected = result.get("selected_repo")
    selected: Optional[RepoInfo] = None
    if isinstance(raw_selected, dict):
        selected = _build_repo_info(raw_selected)

    raw_external = result.get("external_resources")
    external: List[Dict[str, str]] = []
    if isinstance(raw_external, list):
        for item in raw_external:
            if isinstance(item, dict):
                external.append({_coerce_str(k): _coerce_str(v) for k, v in item.items()})

    strategy = _coerce_str(result.get("resource_strategy")).strip()
    if strategy not in _VALID_STRATEGIES:
        # 无效 / 缺失策略：按 repos 是否非空推断默认值（缺失校验在 _map 里另行处理）。
        strategy = "use_repo" if repos else "from_scratch"

    return ResourceInfo(
        repos=repos,
        selected_repo=selected,
        external_resources=external,
        resource_strategy=strategy,
    )


# ---------- 工具历史回填（BUG-S1-03 治理范式） ----------


def _parse_tool_content(content: Any) -> Optional[Dict[str, Any]]:
    """把单条 ToolMessage.content 解析为 dict（容忍截断后缀）。失败返回 None。"""
    if isinstance(content, list):
        content = "".join(
            c if isinstance(c, str) else (c.get("text") or "") if isinstance(c, dict) else ""
            for c in content
        )
    if not isinstance(content, str):
        content = str(content)
    text = content.strip()
    if not text:
        return None
    # 1) 整段 JSON
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except (TypeError, ValueError):
        pass
    # 2) 剥离截断后缀再试
    idx = text.rfind("... [truncated at")
    if idx > 0:
        try:
            parsed = json.loads(text[:idx].rstrip())
            if isinstance(parsed, dict):
                return parsed
        except (TypeError, ValueError):
            pass
    return None


def _backfill_repos_from_tools(
    payload: ResourceInfo,
    react_messages: Optional[Any],
) -> bool:
    """从 ReAct 子图工具历史回填 repos（BUG-S1-03 治理范式）。

    背景：LLM 偶发在 <result> JSON 中漏写 repos（输出空数组），即便 ReAct 子图历史里
    已成功调用了 git_clone_and_analyze 并拿到了 RepoInfo。本函数提供节点层兜底，不依赖
    LLM 服从度。

    策略：
    - 扫描 react_messages 中所有 name=git_clone_and_analyze 的 ToolMessage；
    - 解析其 content 为 dict，过滤失败记录（content 以 "Error in"/"tool " 开头，或
      解析出 success==False，或缺 local_path）；
    - 仅当 payload.repos 为空时用工具历史回填（quality_score 默认 0.5 兜底）；
    - 找到 git_clone_and_analyze ToolMessage 但无法配对任何成功克隆时打 WARNING（非静默）。

    Returns:
        True 表示触发了回填。
    """
    if not react_messages:
        return False
    if payload.get("repos"):
        # LLM 已给出 repos，无需回填。
        return False

    try:
        from langchain_core.messages import ToolMessage
    except Exception:  # pragma: no cover - defensive
        return False

    msgs = list(react_messages)
    clone_called = any(
        isinstance(m, ToolMessage) and getattr(m, "name", None) == _GIT_CLONE_TOOL_NAME
        for m in msgs
    )
    if not clone_called:
        return False

    recovered: List[RepoInfo] = []
    seen_urls: set = set()
    for m in msgs:
        if not isinstance(m, ToolMessage):
            continue
        if getattr(m, "name", None) != _GIT_CLONE_TOOL_NAME:
            continue
        content = getattr(m, "content", "")
        content_strip = content.strip() if isinstance(content, str) else str(content).strip()
        # 工厂层失败 / react_base 异常分支前缀，直接跳过。
        if content_strip.startswith("Error in ") or content_strip.startswith("tool "):
            continue
        parsed = _parse_tool_content(content)
        if not parsed:
            continue
        # 失败 RepoInfo：{"success": false, "error": ...}
        if parsed.get("success") is False:
            continue
        # 成功 RepoInfo 必须带 local_path（克隆产物的标识）。
        if not parsed.get("local_path"):
            continue
        repo = _build_repo_info(parsed)
        # 回填的 RepoInfo 默认给 degraded 质量分（无 LLM 评分），并标记来源说明。
        if repo.get("quality_score", 0.0) <= 0.0:
            repo["quality_score"] = _BACKFILL_DEFAULT_QUALITY
        url_key = repo.get("url") or repo.get("local_path") or ""
        if url_key in seen_urls:
            continue
        seen_urls.add(url_key)
        recovered.append(repo)

    if not recovered:
        logger.warning(
            "[%s] backfill skipped: git_clone_and_analyze ToolMessage exists but no "
            "successful RepoInfo (with local_path) could be derived",
            NODE_NAME,
        )
        return False

    logger.info(
        "[%s] backfill repos from tool history: %d repo(s) recovered",
        NODE_NAME, len(recovered),
    )
    payload["repos"] = recovered
    return True


# ---------- 主映射 ----------


def _select_best_repo(repos: List[RepoInfo]) -> Optional[RepoInfo]:
    """从 repos 中选 quality_score 最高的作为 selected_repo。"""
    if not repos:
        return None
    return max(repos, key=lambda r: float(r.get("quality_score") or 0.0))


def _append_search_log_note(result: Dict[str, Any], notes: str) -> str:
    """把 agent 自报告的 search_log 追加到 analysis_notes（人类可审核）。"""
    search_log = result.get("search_log")
    if isinstance(search_log, list) and search_log:
        rendered = "; ".join(_coerce_str(s) for s in search_log if _coerce_str(s))
        if rendered:
            return (f"{notes}\n[SEARCH_LOG] {rendered}" if notes else f"[SEARCH_LOG] {rendered}")
    return notes


def _map_resource_scout_result(
    result: Optional[Dict[str, Any]],
    state: GlobalState,
    react_messages: Optional[Any] = None,
) -> dict:
    """将 ReAct 子图结果映射为 GlobalState 局部更新（3 参签名，BUG-S1-03 范式）。

    职责：
    1. 校验 result 含必需字段；缺失/空时降级 from_scratch + degraded_nodes 标记；
    2. 工具历史回填（repos 为空但工具有成功克隆时回填）；
    3. quality_score 全部 <0.3 时写 analysis_notes [QUALITY_WARN]，仍照常推荐 repos[0]；
    4. 写 NodeError(degraded) + degraded_nodes 时打 WARNING（非静默吞错）。

    react_messages 由 _make_react_wrapper 通过 inspect 检测自动注入；2 参签名保持兼容。
    """
    node_errors = list(state.get("node_errors", []))
    degraded_nodes = list(state.get("degraded_nodes", []))
    notes = ""

    # 空结果 / error：不抛致命异常，降级 from_scratch。
    if not result or not isinstance(result, dict):
        message = "resource_scout ReAct agent 未返回有效结果，降级 from_scratch"
        logger.warning("[%s] %s", NODE_NAME, message)
        resource_info = ResourceInfo(
            repos=[],
            selected_repo=None,
            external_resources=[],
            resource_strategy="from_scratch",
        )
        if NODE_NAME not in degraded_nodes:
            degraded_nodes.append(NODE_NAME)
        node_errors.append(make_node_error(NODE_NAME, "degraded", message, None))
        return {
            "resource_info": resource_info,
            "current_step": NODE_NAME,
            "node_errors": node_errors,
            "degraded_nodes": degraded_nodes,
        }

    error_msg = result.get("error")
    if error_msg:
        message = _coerce_str(error_msg) or "resource_scout 报告错误，降级 from_scratch"
        logger.warning("[%s] agent 报告错误: %s（降级 from_scratch）", NODE_NAME, message)
        resource_info = ResourceInfo(
            repos=[],
            selected_repo=None,
            external_resources=[],
            resource_strategy="from_scratch",
        )
        if NODE_NAME not in degraded_nodes:
            degraded_nodes.append(NODE_NAME)
        node_errors.append(make_node_error(NODE_NAME, "degraded", message, None))
        return {
            "resource_info": resource_info,
            "current_step": NODE_NAME,
            "node_errors": node_errors,
            "degraded_nodes": degraded_nodes,
        }

    resource_info = _build_resource_info(result)

    # 工具历史回填兜底（BUG-S1-03）：LLM 漏写 repos 但工具有成功克隆记录时回填。
    backfilled = _backfill_repos_from_tools(resource_info, react_messages)
    if backfilled:
        # 回填后重新选 selected_repo（LLM 可能也漏写了），并修正策略。
        if not resource_info.get("selected_repo"):
            resource_info["selected_repo"] = _select_best_repo(resource_info["repos"])
        if resource_info.get("resource_strategy") == "from_scratch" and resource_info["repos"]:
            resource_info["resource_strategy"] = "use_repo"

    repos = resource_info.get("repos") or []

    # selected_repo 与 repos 一致性兜底：有仓库但 LLM 漏写 selected_repo 时补齐。
    if repos and not resource_info.get("selected_repo"):
        resource_info["selected_repo"] = _select_best_repo(repos)

    # 候选全部克隆失败 / 无仓库 -> from_scratch 降级标记（架构 §2.3.4）。
    if not repos:
        if resource_info.get("resource_strategy") != "from_scratch":
            resource_info["resource_strategy"] = "from_scratch"
        resource_info["selected_repo"] = None
        if NODE_NAME not in degraded_nodes:
            degraded_nodes.append(NODE_NAME)
        message = "resource_scout 未找到可用代码仓库，降级 from_scratch"
        logger.warning("[%s] %s", NODE_NAME, message)
        node_errors.append(make_node_error(NODE_NAME, "degraded", message, None))
    else:
        # quality_score 全部 <0.3：仍照常推荐 repos[0]（best），写 [QUALITY_WARN]。
        max_quality = max(float(r.get("quality_score") or 0.0) for r in repos)
        if max_quality < _QUALITY_WARN_THRESHOLD:
            marker = (
                f"[QUALITY_WARN] all repos quality_score < {_QUALITY_WARN_THRESHOLD} "
                f"(max={max_quality:.2f}); selected best-effort candidate"
            )
            logger.warning("[%s] %s", NODE_NAME, marker)
            notes = f"{marker}\n{notes}" if notes else marker
            # 确保 selected_repo 落在最佳候选上（即使分都很低）。
            if not resource_info.get("selected_repo"):
                resource_info["selected_repo"] = _select_best_repo(repos)

    # agent 自报告 search_log 透明落到 analysis_notes（不进 ResourceInfo Schema）。
    notes = _append_search_log_note(result, notes)

    logger.info(
        "[%s] 完成: repos=%d, strategy=%s, selected=%s, degraded=%s",
        NODE_NAME,
        len(repos),
        resource_info.get("resource_strategy"),
        bool(resource_info.get("selected_repo")),
        NODE_NAME in degraded_nodes,
    )

    update: dict = {
        "resource_info": resource_info,
        "current_step": NODE_NAME,
        "node_errors": node_errors,
        "degraded_nodes": degraded_nodes,
    }
    # analysis_notes 是 GlobalState 之外的展示字段，仅在有内容时追加（避免覆盖上游）。
    if notes:
        prev = state.get("analysis_notes", "") or ""
        update["analysis_notes"] = f"{prev}\n{notes}" if prev else notes
    return update


# ReAct wrapper：把 GlobalState ↔ ReActState 双向映射 + 子图编译 + 预算扣减
# 都封装好，主图直接 import 该 callable 注册节点即可（CP-B2-1：__name__ ==
# "react_wrapper_resource_scout"）。
resource_scout = _make_react_wrapper(
    node_name=NODE_NAME,
    build_context=lambda state: _format_resource_scout_context(
        state.get("paper_meta") or {},
        state.get("paper_analysis") or {},
    ),
    build_system_prompt=_build_resource_scout_system_prompt,
    get_tools=lambda state: [
        web_search_tool(),
        search_papers_tool(),
        get_paper_brief_tool(),
        make_search_pwc_tool(),
        make_git_clone_and_analyze_tool(),
        make_check_url_reachable_tool(),
    ],
    map_result=_map_resource_scout_result,
    max_rounds=REACT_MAX_ROUNDS_RESOURCE_SCOUT,
    result_schema=RESOURCE_SCOUT_SCHEMA,
)
