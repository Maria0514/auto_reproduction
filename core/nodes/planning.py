"""planning 节点（S2-03 + S2-11）：复现规划 + interrupt 人在回路。

节点形态：**复合而非纯 ReAct wrapper**（架构 §2.4.1）。
    1. 内部调用 _planning_react(state) 跑一次 ReAct 子图产出 reproduction_plan；
    2. 调用 langgraph.types.interrupt(payload) 暂停 graph，UI 通过 Command(resume=...)
       注入用户决策；
    3. resume 后从 interrupt 返回值路由 5 类决策（approve / revise / switch_repo /
       code_only / cancel）。

关键设计（架构 §2.4.3 / Q-S2-03 RESOLVED）：
    - revise / switch_repo **无次数硬上限**，任务级兜底依赖 MAX_TOTAL_LLM_CALLS=50
      总预算（react_base.budget_check 自然 force_finish）+ cancel 主动出口；
    - _planning_revise_count 仅供 UI 透明展示与 N>=5 软提示判定，节点层不做任何拦截；
    - cancel 决策写 current_step="cancelled_by_user"，由 graph 条件边路由到 END
      （不抛异常，让 SqliteSaver 完整持久化最后一次 checkpoint）。

治理范式（与 sp1 BUG-S1-02 / BUG-S1-03、sp2 resource_scout 一致）：
    - _map_planning_result 用 3 参签名（含 react_messages），兜底不依赖 LLM 服从度；
    - degraded 标记 / 降级路径均打 WARNING 日志，非静默吞错；
    - system prompt 主体字节冻结，论文级动态上下文走 HumanMessage 通道（前缀稳定）。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from langgraph.types import interrupt

from config import (
    MAX_TOTAL_LLM_CALLS,
    PLANNING_SOFT_HINT_THRESHOLD,
    REACT_MAX_ROUNDS_PLANNING,
)
from core.errors import make_node_error
from core.nodes._repo_scoring import REPO_QUALITY_SCORING_SECTION
from core.react_base import _make_react_wrapper
from core.state import (
    ExecutionMode,
    GlobalState,
    RepoInfo,
    ReproductionPlan,
    ResourceInfo,
)
from core.tools.deepxiv_tools import (
    get_paper_structure_tool,
    read_section_tool,
    web_search_tool,
)
from core.tools.git_tools import (
    make_check_url_reachable_tool,
    make_git_clone_and_analyze_tool,
)

logger = logging.getLogger(__name__)


NODE_NAME: str = "planning"

# code_strategy 合法取值（架构 §2.4.2 第 4 章节）。
_VALID_STRATEGIES = ("use_repo", "hybrid", "from_scratch")

# git clone+analyze 复合工具的 @tool 函数名（ToolMessage.name），用于工具历史合并配对。
_GIT_CLONE_TOOL_NAME: str = "git_clone_and_analyze"


REPRODUCTION_PLAN_SCHEMA: Dict[str, Any] = {
    # title 字段是 langchain_openai.with_structured_output 的强制要求（函数名）。
    "title": "ReproductionPlan",
    "description": "经用户审批前的复现计划，planning 节点 ReAct 子图输出契约。",
    "type": "object",
    "properties": {
        "plan_summary": {"type": "string"},
        "environment": {"type": "object"},
        "data_preparation": {"type": "array", "items": {"type": "string"}},
        "code_strategy": {"type": "string"},
        "execution_steps": {"type": "array", "items": {"type": "object"}},
        "expected_results": {"type": "object"},
        "estimated_time": {"type": "string"},
        # 最低交付基准线（PRD §2.3）：无论 execution_mode 都必填。
        "deliverables": {"type": "array", "items": {"type": "string"}},
    },
    # approved / user_feedback 由 planning 节点根据 resume payload 写入，不强制 LLM 产出。
    "required": ["plan_summary", "code_strategy", "deliverables"],
    "additionalProperties": True,
}


# Prompt Cache 前缀治理（方案 A，架构 §2.4.2 / §2.6.6）：
# _PLANNING_SYSTEM_PROMPT_BODY 是 SystemMessage 的稳定前缀部分，严禁插入 arxiv_id /
# title / user_feedback 等任何论文级动态变量，否则破坏多论文间字节级前缀一致性导致
# Prompt Cache 失效。论文级动态上下文（含 user_feedback）由 build_context 走
# HumanMessage 通道注入。自测断言不同论文输入下本主体字节级一致（CP-B3-10）。
_PLANNING_SYSTEM_PROMPT_BODY = """你是论文复现规划专家。任务是综合论文方法、资源信息与用户反馈（可能为空），产出结构化的复现计划。

可用工具：
- read_section(arxiv_id, section_name): 回读论文指定章节原文，核对实现细节 / 超参 / 数据处理。
- get_paper_structure(arxiv_id): 获取论文章节结构，定位需要回读的章节。
- web_search(query): 通用网页搜索，查数据集下载地址 / 依赖安装方式等。
- check_url_reachable_tool(url): HTTP HEAD 探测 URL 是否可达，校验数据集 / 仓库链接有效性。

【计划必含 6 章节，对齐 product-design-spec §4.3.1】
1. plan_summary（中文叙述）：用一段中文概述复现思路与关键步骤。
2. environment（硬件 / 软件 / 预估时间）：引用论文分析的 hardware_requirements 中文主字段，
   列出 GPU / 内存 / Python 与关键依赖版本。
3. data_preparation（步骤列表）：数据集获取与预处理步骤；数据集名保留英文（PRD §4.7.5）。
4. code_strategy：基于 resource_info.selected_repo 判定——
   - 有高质量官方仓库 -> "use_repo"（说明需要适配的点）；
   - 仓库质量一般需大量适配 -> "hybrid"；
   - 无可用仓库 / resource_info 为空 -> "from_scratch"（从零实现）。
5. execution_steps（step_name / command / expected_output 三元组列表）：可执行的步骤序列，
   每步含命令与预期输出。
6. expected_results + estimated_time + deliverables：
   - expected_results：复现应达到的关键指标（引用论文 baseline_results / metrics）；
   - estimated_time：总预估耗时；
   - deliverables（最低交付基准线，**必填，无论是否完整复现都要给**）：至少含
     README.md / requirements.txt / 入口脚本 / 核心实现文件 / `py_compile` 通过。

【输出格式】
- 完成规划后，必须在 <result>...</result> 标签内输出严格 JSON，字段如下：
  {
    "plan_summary": str,
    "environment": {...},
    "data_preparation": [str, ...],
    "code_strategy": "use_repo" | "hybrid" | "from_scratch",
    "execution_steps": [ {"step_name": str, "command": str, "expected_output": str}, ... ],
    "expected_results": {...},
    "estimated_time": str,
    "deliverables": [str, ...]
  }
- deliverables 字段无论 code_strategy 取值都必须填写（最低交付基准线）。
- 不要在 <result> 之外再夹杂任何其它 JSON 块。

【用户提供的仓库（来自修改意见 / 切换仓库）】
- 若 user_feedback 中出现仓库链接，或上下文 pending_repo_url 字段非空：
  先用 check_url_reachable_tool 校验，再用 git_clone_and_analyze(url) 克隆并分析；
- 对成功克隆的用户提供仓库，按下方【质量评分】同一套权重给出 quality_score，
  作为一条候选放进 <result>.repos（与自动候选同列，可比较），并据其更新 code_strategy；
- 链接不可达 / 克隆失败 / 与本论文明显不相关时，不要把它放进 repos，
  在 plan_summary 中说明「该仓库未能克隆分析，建议核对链接」；
- stars / forks 始终留空（null），不要捏造。
- 单次重规划最多克隆 3 个用户提供仓库。
""" + REPO_QUALITY_SCORING_SECTION


# ---------- 类型补齐 helpers ----------


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


def _coerce_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _coerce_step_list(value: Any) -> List[Dict[str, str]]:
    """把 execution_steps 规整为 List[Dict[str, str]]（容忍字符串元素）。"""
    if not isinstance(value, list):
        return []
    steps: List[Dict[str, str]] = []
    for item in value:
        if isinstance(item, dict):
            steps.append({_coerce_str(k): _coerce_str(v) for k, v in item.items()})
        elif item is not None:
            # 非 dict 步骤：降级包装为单字段，避免下游类型错误。
            steps.append({"step_name": _coerce_str(item)})
    return steps


# ---------- system prompt / context ----------


def _build_planning_system_prompt(context: Dict[str, Any]) -> str:
    """组装 planning 的 system prompt（Prompt Cache 前缀稳定化：主体冻结）。

    论文级动态上下文（含 user_feedback）不进 system prompt，由 build_context 走
    HumanMessage 通道注入（与 resource_scout 同款）。
    """
    return _PLANNING_SYSTEM_PROMPT_BODY


# planning 规划真正需要的上下文字段（curated，避免把整份大对象塞进 HumanMessage）。
_KEEP_META_KEYS = ("arxiv_id", "title")
_KEEP_ANALYSIS_KEYS = (
    "method_summary", "datasets", "metrics", "hyperparams",
    "hardware_requirements", "framework", "baseline_results",
)


def _format_planning_context(
    paper_meta: Optional[Dict[str, Any]],
    paper_analysis: Optional[Dict[str, Any]],
    resource_info: Optional[Dict[str, Any]],
    user_feedback: Optional[str],
    pending_repo_url: Optional[str] = None,
) -> Dict[str, Any]:
    """提取 planning 规划必需的上下文（HumanMessage 通道，含 user_feedback）。

    返回 dict，由 _make_react_wrapper 用 json.dumps(sort_keys=True) 渲染为
    HumanMessage（同一输入下字节级幂等）。
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

    # resource_info：planning 关心 selected_repo（决定 code_strategy）与候选数量。
    if isinstance(resource_info, dict):
        selected = resource_info.get("selected_repo")
        if isinstance(selected, dict):
            payload["selected_repo"] = {
                "url": selected.get("url"),
                "is_official": selected.get("is_official"),
                "quality_score": selected.get("quality_score"),
                "local_path": selected.get("local_path"),
                "has_readme": selected.get("has_readme"),
                "has_requirements": selected.get("has_requirements"),
            }
        payload["resource_strategy"] = resource_info.get("resource_strategy")
        repos = resource_info.get("repos")
        if isinstance(repos, list):
            payload["repo_candidate_count"] = len(repos)

    # user_feedback（revise / switch_repo 路径回流）；为空时不写，保持上下文整洁。
    if user_feedback:
        payload["user_feedback"] = _coerce_str(user_feedback)

    # pending_repo_url（switch_repo 入口 b 确定性告诉模型这个 URL 要抓）；HumanMessage 通道，
    # 不进 system prompt（保持 §4.5 字节稳定）。为空时不写。
    if pending_repo_url:
        payload["pending_repo_url"] = _coerce_str(pending_repo_url)

    return payload


# ---------- 计划构造 / 降级 ----------


def _build_reproduction_plan(
    result: Dict[str, Any],
    state: GlobalState,
) -> ReproductionPlan:
    """从 LLM <result> 构造 ReproductionPlan（approved 默认 False，由节点后续写入）。"""
    code_strategy = _coerce_str(result.get("code_strategy")).strip()

    # resource_info 为空时强制 from_scratch（架构 §2.4.4）。
    resource_info = state.get("resource_info")
    has_repo = (
        isinstance(resource_info, dict)
        and bool(resource_info.get("selected_repo") or resource_info.get("repos"))
    )
    if not has_repo:
        code_strategy = "from_scratch"
    elif code_strategy not in _VALID_STRATEGIES:
        code_strategy = "use_repo"

    return ReproductionPlan(
        plan_summary=_coerce_str(result.get("plan_summary")),
        environment=_coerce_dict(result.get("environment")),
        data_preparation=_coerce_str_list(result.get("data_preparation")),
        code_strategy=code_strategy,
        execution_steps=_coerce_step_list(result.get("execution_steps")),
        expected_results=_coerce_dict(result.get("expected_results")),
        estimated_time=_coerce_str(result.get("estimated_time")),
        deliverables=_coerce_str_list(result.get("deliverables")),
        user_feedback=state.get("_planning_user_feedback"),
        approved=False,
    )


# ---------- S2-13 用户提供仓库合并（去重 + 默认选中 + 同口径分） ----------


def _normalize_repo_url(url: Any) -> str:
    """规范化仓库 URL 用于去重比对（大小写、尾斜杠、.git 后缀）。

    归一规则（保守，仅消除明显等价差异，不做语义解析，架构 §2.13.4）：
    - strip + 去尾部 "/" 与 ".git"；整体 .lower() 比对。
    空 / 非字符串输入返回 ""。
    """
    if not isinstance(url, str):
        url = _coerce_str(url)
    s = url.strip()
    if not s:
        return ""
    s = s.rstrip("/")
    if s.lower().endswith(".git"):
        s = s[: -len(".git")]
    s = s.rstrip("/")
    return s.lower()


def _merge_user_repos_from_tools(
    payload: ReproductionPlan,
    react_messages: Optional[Any],
    state: GlobalState,
) -> Dict[str, Any]:
    """把工具历史中成功克隆的用户提供仓库合并进 resource_info.repos（S2-13，架构 §2.13.4）。

    沿用 resource_scout `_backfill_repos_from_tools` 的 ToolMessage 解析 + 失败过滤 + 去重
    范式（BUG-S1-03 治理一致），但升级为「合并」而非「仅空时回填」：
    - 扫 react_messages 中所有 name==git_clone_and_analyze 的 ToolMessage；
    - 跳过失败记录（Error/tool 前缀 / success==False / 缺 local_path）；
    - 对每条成功 RepoInfo：按规范化 URL 与既有 repos 比对——命中则直接选中既有候选、
      不重复加入；未命中则 source 改写为 "user_provided"、加入 repos、设为 selected_repo；
    - quality_score 取模型在 result.repos[] 中对该 URL 给出的同口径分；模型漏写时用
      `_BACKFILL_DEFAULT_QUALITY` 兜底并打 WARNING（非静默）；
    - 合并入选中仓库后若 code_strategy=="from_scratch" 纠正为 "use_repo"。

    Returns:
        dict: {
            "merged": bool,          # 是否合并/选中了任何用户提供仓库
            "resource_info": ResourceInfo,   # 更新后的 resource_info（merged=False 时为基底原样）
            "tool_attempted": bool,  # 工具历史中是否存在 git_clone_and_analyze ToolMessage
        }
    """
    # 延迟 import 复用 resource_scout 解析范式（resource_scout 不依赖 planning，无环）。
    from core.nodes.resource_scout import (
        _BACKFILL_DEFAULT_QUALITY,
        _build_repo_info,
        _parse_tool_content,
    )

    base_info = state.get("resource_info")
    base: Dict[str, Any] = dict(base_info) if isinstance(base_info, dict) else {}
    repos: List[RepoInfo] = list(base.get("repos") or [])
    selected: Optional[RepoInfo] = base.get("selected_repo")  # type: ignore[assignment]
    strategy = _coerce_str(base.get("resource_strategy")).strip()

    result_info = {
        "merged": False,
        "resource_info": ResourceInfo(
            repos=repos,
            selected_repo=selected,
            external_resources=list(base.get("external_resources") or []),
            resource_strategy=strategy if strategy in _VALID_STRATEGIES else (
                "use_repo" if (selected or repos) else "from_scratch"
            ),
        ),
        "tool_attempted": False,
    }

    if not react_messages:
        return result_info

    try:
        from langchain_core.messages import ToolMessage
    except Exception:  # pragma: no cover - defensive
        return result_info

    msgs = list(react_messages)
    clone_msgs = [
        m for m in msgs
        if isinstance(m, ToolMessage) and getattr(m, "name", None) == _GIT_CLONE_TOOL_NAME
    ]
    if not clone_msgs:
        return result_info
    result_info["tool_attempted"] = True

    # 模型在 <result>.repos[] 中给出的同口径分（按规范化 URL 索引）。
    model_scores: Dict[str, float] = {}
    if isinstance(payload, dict):
        for r in payload.get("repos") or []:
            if isinstance(r, dict):
                key = _normalize_repo_url(r.get("url"))
                if key:
                    try:
                        model_scores[key] = float(r.get("quality_score"))
                    except (TypeError, ValueError):
                        pass

    existing_keys = {_normalize_repo_url(r.get("url")) for r in repos if isinstance(r, dict)}

    recovered = 0
    last_selected: Optional[RepoInfo] = None
    for m in clone_msgs:
        content = getattr(m, "content", "")
        content_strip = content.strip() if isinstance(content, str) else str(content).strip()
        if content_strip.startswith("Error in ") or content_strip.startswith("tool "):
            continue
        parsed = _parse_tool_content(content)
        if not parsed:
            continue
        if parsed.get("success") is False:
            continue
        if not parsed.get("local_path"):
            continue

        url_key = _normalize_repo_url(parsed.get("url"))
        # 命中既有候选：直接选中，不重复加入（_switch_selected_repo 命中语义）。
        if url_key and url_key in existing_keys:
            for r in repos:
                if isinstance(r, dict) and _normalize_repo_url(r.get("url")) == url_key:
                    last_selected = r  # type: ignore[assignment]
                    break
            recovered += 1
            continue

        repo = _build_repo_info(parsed)
        repo["source"] = "user_provided"  # 统一覆盖（架构 §2.13.7）
        # quality_score：优先模型同口径分；漏写则兜底 + WARNING（非静默）。
        if url_key in model_scores:
            repo["quality_score"] = model_scores[url_key]
        elif repo.get("quality_score", 0.0) <= 0.0:
            logger.warning(
                "[%s] user-provided repo missing LLM quality_score, "
                "fallback to default: %s",
                NODE_NAME, parsed.get("url"),
            )
            repo["quality_score"] = _BACKFILL_DEFAULT_QUALITY
        repos.append(repo)
        existing_keys.add(url_key)
        last_selected = repo
        recovered += 1

    if recovered == 0:
        # 工具历史有 git_clone_and_analyze 但无任何成功结果可合并（全失败）。
        logger.warning(
            "[%s] merge skipped: git_clone_and_analyze ToolMessage exists but no "
            "successful user-provided RepoInfo (with local_path) could be derived",
            NODE_NAME,
        )
        return result_info

    # 默认选中最新合并/命中的用户提供仓库（Q-S2-07）。
    if last_selected is not None:
        selected = last_selected

    # code_strategy 纠正：有了选中仓库则不应再 from_scratch。
    if selected is not None and strategy == "from_scratch":
        strategy = "use_repo"
    if strategy not in _VALID_STRATEGIES:
        strategy = "use_repo" if (selected or repos) else "from_scratch"

    logger.info(
        "[%s] merged user-provided repo(s) from tool history: %d, repos=%d, selected=%s",
        NODE_NAME, recovered, len(repos),
        (selected.get("url") if isinstance(selected, dict) else None),
    )

    result_info["merged"] = True
    result_info["resource_info"] = ResourceInfo(
        repos=repos,
        selected_repo=selected,
        external_resources=list(base.get("external_resources") or []),
        resource_strategy=strategy,
    )
    return result_info


def _minimal_plan(state: GlobalState, reason: str) -> ReproductionPlan:
    """ReAct 子图失败时的最简版计划（仅 plan_summary + code_strategy，架构 §2.4.4）。

    仍触发 interrupt（避免用户审核页空白），code_strategy 固定 from_scratch。
    """
    summary = (
        "规划阶段未能生成完整计划（"
        f"{reason}），已降级为最简复现策略：从零实现，待用户审核后决定后续。"
    )
    return ReproductionPlan(
        plan_summary=summary,
        environment={},
        data_preparation=[],
        code_strategy="from_scratch",
        execution_steps=[],
        expected_results={},
        estimated_time="",
        deliverables=[
            "README.md",
            "requirements.txt",
            "入口脚本",
            "核心实现文件",
            "py_compile 通过",
        ],
        user_feedback=state.get("_planning_user_feedback"),
        approved=False,
    )


# planning ReAct 子图缺失核心字段判定（degraded 兜底，治理范式）。
_CORE_PLAN_FIELDS = ("plan_summary", "code_strategy")


def _map_planning_result(
    result: Optional[Dict[str, Any]],
    state: GlobalState,
    react_messages: Optional[Any] = None,
) -> dict:
    """将 ReAct 子图结果映射为 GlobalState 局部更新（3 参签名，治理范式）。

    职责：
    1. 缺失 / 空结果时降级最简版 plan + degraded_nodes 标记；
    2. 类型补齐构造 ReproductionPlan（approved=False，由 planning 节点后续写入）；
    3. 缺失核心字段（plan_summary / code_strategy）时 degraded 兜底 + WARNING。

    react_messages 由 _make_react_wrapper 通过 inspect 自动注入（S2-13 激活：合并用户提供
    仓库进 resource_info，工具历史回填兜底不依赖 LLM 服从度，BUG-S1-03 范式）。
    """
    node_errors = list(state.get("node_errors", []))
    degraded_nodes = list(state.get("degraded_nodes", []))
    # 入口 b 标识：switch_repo resume 分支写入的待抓取 URL（入口 a 不写）。
    pending_url = _coerce_str(state.get("_planning_pending_repo_url")).strip()

    # 空结果 / 非 dict：降级最简版 plan（不抛致命异常）。
    if not result or not isinstance(result, dict):
        message = "planning ReAct agent 未返回有效结果，降级最简版 plan"
        logger.warning("[%s] %s", NODE_NAME, message)
        if NODE_NAME not in degraded_nodes:
            degraded_nodes.append(NODE_NAME)
        node_errors.append(make_node_error(NODE_NAME, "degraded", message, None))
        out = {
            "reproduction_plan": _minimal_plan(state, "agent 无有效输出"),
            "current_step": NODE_NAME,
            "node_errors": node_errors,
            "degraded_nodes": degraded_nodes,
        }
        # 入口 b：空结果视为抓取失败，置强制重填标记 + 清 pending_url。
        if pending_url:
            out["_planning_pending_repo_url"] = None
            out["_planning_switch_failed"] = True
        return out

    plan = _build_reproduction_plan(result, state)

    # S2-13：合并工具历史中成功克隆的用户提供仓库进 resource_info（去重 + 默认选中 + 同口径分）。
    merge = _merge_user_repos_from_tools(plan, react_messages, state)

    # 缺失核心字段：degraded 兜底（不阻断 interrupt）。
    missing = [f for f in _CORE_PLAN_FIELDS if not plan.get(f)]
    if missing:
        message = f"planning 计划缺失核心字段 {missing}，标记 degraded"
        logger.warning("[%s] %s", NODE_NAME, message)
        if NODE_NAME not in degraded_nodes:
            degraded_nodes.append(NODE_NAME)
        node_errors.append(make_node_error(NODE_NAME, "degraded", message, None))
        # plan_summary 缺失时给一句兜底，避免审核页空白。
        if not plan.get("plan_summary"):
            plan["plan_summary"] = "（规划摘要缺失，请结合下方资源信息与论文分析审核）"

    out: Dict[str, Any] = {
        "reproduction_plan": plan,
        "current_step": NODE_NAME,
        "node_errors": node_errors,
        "degraded_nodes": degraded_nodes,
    }

    if merge["merged"]:
        # 合并成功：回写更新后的 resource_info（携真实 quality_score 回审核页）；
        # 若计划仍为 from_scratch 但已有选中仓库，纠正为 use_repo。
        out["resource_info"] = merge["resource_info"]
        if plan.get("code_strategy") == "from_scratch" and merge["resource_info"].get(
            "selected_repo"
        ):
            plan["code_strategy"] = "use_repo"
        # 入口 b 成功：清 pending_url + 清失败标记。
        if pending_url:
            out["_planning_pending_repo_url"] = None
            out["_planning_switch_failed"] = False
    elif pending_url:
        # 入口 b：指定了待抓取 URL 但本轮未成功合并任何用户仓库 → 强制重填（不写 0.0 占位）。
        message = f"用户提供仓库克隆/分析失败: {pending_url}"
        logger.warning("[%s] %s", NODE_NAME, message)
        if NODE_NAME not in degraded_nodes:
            degraded_nodes.append(NODE_NAME)
        node_errors.append(make_node_error(NODE_NAME, "degraded", message, None))
        out["node_errors"] = node_errors
        out["degraded_nodes"] = degraded_nodes
        out["_planning_pending_repo_url"] = None
        out["_planning_switch_failed"] = True

    logger.info(
        "[%s] 完成: code_strategy=%s, steps=%d, deliverables=%d, degraded=%s",
        NODE_NAME,
        plan.get("code_strategy"),
        len(plan.get("execution_steps") or []),
        len(plan.get("deliverables") or []),
        NODE_NAME in degraded_nodes,
    )

    return out


# ReAct 子图：通过 _make_react_wrapper(node_name="planning", ...) 生成，自动获得节点级
# LLM 路由能力（CP-B3-2）。**由 planning 节点函数内部调用**，不直接注册到主图。
_planning_react = _make_react_wrapper(
    node_name=NODE_NAME,
    build_context=lambda state: _format_planning_context(
        state.get("paper_meta") or {},
        state.get("paper_analysis") or {},
        state.get("resource_info") or {},
        state.get("_planning_user_feedback"),
        state.get("_planning_pending_repo_url"),
    ),
    build_system_prompt=_build_planning_system_prompt,
    get_tools=lambda state: [
        read_section_tool(),
        get_paper_structure_tool(),
        web_search_tool(),
        make_check_url_reachable_tool(),
        make_git_clone_and_analyze_tool(),
    ],
    map_result=_map_planning_result,
    max_rounds=REACT_MAX_ROUNDS_PLANNING,
    result_schema=REPRODUCTION_PLAN_SCHEMA,
)


# ---------- interrupt payload 辅助 ----------


def _digest_paper_analysis(paper_analysis: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """把 paper_analysis 压缩为审核 payload 用的精简摘要（避免 payload 过大）。"""
    if not isinstance(paper_analysis, dict):
        return {}
    summary = _coerce_str(paper_analysis.get("method_summary"))
    if len(summary) > 800:
        summary = summary[:800] + "…"
    return {
        "method_summary": summary,
        "datasets": paper_analysis.get("datasets") or [],
        "metrics": paper_analysis.get("metrics") or [],
        "framework": paper_analysis.get("framework"),
    }


def _switch_selected_repo(
    resource_info: Optional[Dict[str, Any]],
    new_repo_url: Optional[str],
) -> Optional[ResourceInfo]:
    """switch_repo 决策：尝试把 selected_repo 切换为用户指定的新仓库 URL（仅命中既有候选）。

    S2-13 改造（架构 §2.13.4 / §2.13.10）：
    - 若新 URL（规范化比对）命中 repos 中已有候选，直接选中该候选并返回更新后的 ResourceInfo；
    - 未命中既有候选时返回 None——表示「需交 ReAct 在重入后抓取分析」（不再造 0.0 占位 RepoInfo）。
    """
    target = _normalize_repo_url(new_repo_url)
    if not target:
        return None
    base: Dict[str, Any] = dict(resource_info) if isinstance(resource_info, dict) else {}
    repos: List[RepoInfo] = list(base.get("repos") or [])

    selected: Optional[RepoInfo] = None
    for repo in repos:
        if isinstance(repo, dict) and _normalize_repo_url(repo.get("url")) == target:
            selected = repo  # type: ignore[assignment]
            break
    if selected is None:
        return None

    strategy = _coerce_str(base.get("resource_strategy")).strip()
    if strategy not in _VALID_STRATEGIES:
        strategy = "use_repo"

    return ResourceInfo(
        repos=repos,
        selected_repo=selected,
        external_resources=list(base.get("external_resources") or []),
        resource_strategy=strategy,
    )


def _finalize_approve(
    updates: dict,
    execution_mode: Optional[ExecutionMode] = None,
    reason: Optional[str] = None,
) -> dict:
    """approve / code_only / 兜底路径：把 reproduction_plan.approved 置 True 并收尾。

    Args:
        updates: _planning_react(state) 返回的局部更新（含 reproduction_plan）。
        execution_mode: code_only 决策时传 ExecutionMode.CODE_ONLY。
        reason: 兜底路径（非法 / 未知 decision）的说明，写入 analysis_notes 供审计。
    """
    out = dict(updates)
    plan = dict(out.get("reproduction_plan") or {})
    plan["approved"] = True
    out["reproduction_plan"] = plan
    out["current_step"] = NODE_NAME
    if execution_mode is not None:
        out["execution_mode"] = execution_mode
    if reason:
        logger.warning("[%s] finalize approve via fallback path: %s", NODE_NAME, reason)
        prev = out.get("analysis_notes")
        if not isinstance(prev, str):
            prev = ""
        marker = f"[PLANNING_FALLBACK] approved due to {reason}"
        out["analysis_notes"] = f"{prev}\n{marker}" if prev else marker
    return out


# ---------- 主节点函数（手写，含 interrupt + 5 类决策路由） ----------


def planning(state: GlobalState) -> dict:
    """复现规划节点 + interrupt 人在回路（架构 §2.4.3）。

    流程：
        1. 内部调用 _planning_react(state) 跑 ReAct 子图产出 reproduction_plan
           （失败时降级最简版 plan，仍触发 interrupt）；
        2. interrupt(payload) 暂停 graph，UI 通过 Command(resume=decision) 注入决策；
        3. 5 类决策路由（approve / code_only / cancel / revise / switch_repo）。
    """
    revise_count = state.get("_planning_revise_count", 0) or 0

    # 步骤 1：跑 ReAct 子图（含失败降级，避免审核页空白）。
    try:
        react_updates = _planning_react(state)
    except Exception as exc:  # noqa: BLE001 - 子图任何失败都降级为最简版 plan
        logger.warning(
            "[%s] ReAct 子图执行失败，降级最简版 plan: %s: %s",
            NODE_NAME, type(exc).__name__, exc,
        )
        degraded_nodes = list(state.get("degraded_nodes", []))
        node_errors = list(state.get("node_errors", []))
        if NODE_NAME not in degraded_nodes:
            degraded_nodes.append(NODE_NAME)
        node_errors.append(
            make_node_error(
                NODE_NAME, "degraded",
                f"planning ReAct 子图失败: {type(exc).__name__}: {exc}", None,
            )
        )
        react_updates = {
            "reproduction_plan": _minimal_plan(state, f"{type(exc).__name__}"),
            "current_step": NODE_NAME,
            "node_errors": node_errors,
            "degraded_nodes": degraded_nodes,
        }

    updates: dict = dict(react_updates) if isinstance(react_updates, dict) else {}
    # 防御：reproduction_plan 缺失时补最简版（避免 payload 取不到键）。
    if not updates.get("reproduction_plan"):
        updates["reproduction_plan"] = _minimal_plan(state, "missing_plan")

    # 步骤 2：构造审核 payload + interrupt（阻塞等待 Command(resume=decision)）。
    # S2-13：本轮 _map_planning_result 合并用户提供仓库后会把更新的 resource_info /
    # _planning_switch_failed 写进 updates；payload 优先取本轮 updates（携真实 quality_score
    # 与失败标记回审核页），否则回落到 state（首次进入 planning 时）。
    if "resource_info" in updates:
        resource_info_for_payload = updates["resource_info"]
    else:
        resource_info_for_payload = state.get("resource_info")
    switch_repo_failed = updates.get(
        "_planning_switch_failed", state.get("_planning_switch_failed", False)
    )
    payload = {
        # sp3 §2.6.1：显式标注 interrupt 类型，供 app.py interrupt_kind helper / UI
        # 路由分发区分 planning（interrupt#1）与 dev_loop_failure（execution interrupt#2）。
        "interrupt_kind": "planning",
        "reproduction_plan": updates["reproduction_plan"],
        "resource_info": resource_info_for_payload,
        "paper_analysis_summary": _digest_paper_analysis(state.get("paper_analysis")),
        "degraded_nodes": updates.get("degraded_nodes", state.get("degraded_nodes", [])),
        "node_errors": (updates.get("node_errors", state.get("node_errors", [])) or [])[-5:],
        "revise_count": revise_count,                          # UI 透明展示
        "soft_hint_threshold": PLANNING_SOFT_HINT_THRESHOLD,   # =5；UI 软提示判定
        "max_total_llm_calls": MAX_TOTAL_LLM_CALLS,            # =50；总预算参考
        "switch_repo_failed": bool(switch_repo_failed),        # S2-13：UI 强制重填标记
    }
    decision = interrupt(payload)

    # 步骤 3：5 类决策路由（PRD §2.3）。
    if not isinstance(decision, dict) or "decision" not in decision:
        return _finalize_approve(updates, reason="invalid_resume_payload")

    kind = decision["decision"]

    if kind == "approve":
        return _finalize_approve(updates)

    if kind == "code_only":
        return _finalize_approve(updates, execution_mode=ExecutionMode.CODE_ONLY)

    if kind == "cancel":
        # 用户主动终止：写 current_step 后由条件边路由到 END（不抛异常，保留 checkpoint）。
        prev_notes = state.get("analysis_notes", "") or ""
        marker = "[CANCELLED] user requested cancel at planning"
        return {
            "current_step": "cancelled_by_user",
            "analysis_notes": f"{prev_notes}\n{marker}" if prev_notes else marker,
        }

    if kind in ("revise", "switch_repo"):
        # 无次数硬上限（Q-S2-03 RESOLVED）：revise_count 仅供 UI 透明展示 / 软提示。
        # 不写 reproduction_plan.approved=True -> graph 走 self-loop 重入 planning。
        new_state_update: dict = {
            "_planning_user_feedback": decision.get("user_feedback", ""),
            "_planning_revise_count": revise_count + 1,
        }
        # S2-13：本轮 _map_planning_result 合并/消费了用户提供仓库时，把更新后的
        # resource_info / switch 标记 / pending_url 清理一并提交（否则 self-loop 重入会丢失）。
        if "resource_info" in updates:
            new_state_update["resource_info"] = updates["resource_info"]
        if "_planning_switch_failed" in updates:
            new_state_update["_planning_switch_failed"] = updates["_planning_switch_failed"]
        if "_planning_pending_repo_url" in updates:
            new_state_update["_planning_pending_repo_url"] = updates["_planning_pending_repo_url"]

        if kind == "switch_repo":
            # 命中既有候选 → 直接选中（确定性，无需重抓）；未命中 → 只写 pending_url 走
            # self-loop 重入，由 ReAct 抓取分析打分（架构 §2.13.1 方案 A）。
            switched = _switch_selected_repo(
                state.get("resource_info"), decision.get("new_repo_url"),
            )
            if switched is not None:
                new_state_update["resource_info"] = switched
                # 命中既有候选成功切换：清失败标记 / pending_url。
                new_state_update["_planning_switch_failed"] = False
                new_state_update["_planning_pending_repo_url"] = None
            else:
                url = _coerce_str(decision.get("new_repo_url")).strip()
                if url:
                    new_state_update["_planning_pending_repo_url"] = url
                    # 新一轮 switch 提交即清旧失败标记（等本轮重入抓取结果再定）。
                    new_state_update["_planning_switch_failed"] = False
        return new_state_update

    # 未知 decision 兜底（UI 不应发出此类 payload，仅防御性兜底）。
    return _finalize_approve(updates, reason=f"unknown_decision:{kind}")
