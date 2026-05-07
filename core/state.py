"""全局状态定义 -- 所有节点间数据流转的唯一契约。

本模块定义贯穿整个 LangGraph 工作流的全局状态结构。
所有 TypedDict 和 Enum 定义与技术架构文档第 4 章保持严格一致。
"""

from typing import TypedDict, Optional, List, Dict, Any
from enum import Enum


class ExecutionMode(str, Enum):
    """执行模式：FULL 完整复现，CODE_ONLY 仅生成代码。"""
    FULL = "full"
    CODE_ONLY = "code_only"


class LLMConfig(TypedDict):
    """LLM 服务连接配置，支持任何 OpenAI 兼容 API。"""
    base_url: str
    model: str
    api_key: str
    temperature: float
    max_tokens: int


class PaperMeta(TypedDict):
    """paper_intake 节点输出：论文基础元数据。"""
    arxiv_id: str
    title: str
    authors: List[str]
    abstract: str
    categories: List[str]
    tldr: Optional[str]
    keywords: Optional[List[str]]
    citation_count: Optional[int]
    github_url: Optional[str]
    publish_date: Optional[str]
    pdf_url: Optional[str]


class PaperAnalysis(TypedDict):
    """paper_analysis 节点输出：论文深度分析结果。"""
    method_summary: str
    key_formulas: List[str]
    datasets: List[str]
    metrics: List[str]
    hyperparams: Dict[str, Any]
    hardware_requirements: str
    framework: Optional[str]
    baseline_results: Dict[str, Any]
    sections_read: List[str]
    analysis_notes: str


class RepoInfo(TypedDict):
    """单个代码仓库的评估信息。"""
    url: str
    source: str
    is_official: bool
    stars: Optional[int]
    forks: Optional[int]
    last_commit_date: Optional[str]
    commit_count_recent: Optional[int]
    has_readme: bool
    has_requirements: bool
    dir_structure: Optional[List[str]]
    quality_score: float


class ResourceInfo(TypedDict):
    """resource_scout 节点输出：资源搜集与评估结果。"""
    repos: List[RepoInfo]
    selected_repo: Optional[RepoInfo]
    external_resources: List[Dict[str, str]]
    resource_strategy: str


class ReproductionPlan(TypedDict):
    """planning 节点输出：经用户审批的复现计划。"""
    plan_summary: str
    environment: Dict[str, Any]
    data_preparation: List[str]
    code_strategy: str
    execution_steps: List[Dict[str, str]]
    expected_results: Dict[str, Any]
    estimated_time: str
    deliverables: List[str]
    user_feedback: Optional[str]
    approved: bool


class ExecutionResult(TypedDict):
    """execution 节点输出：代码执行与验证结果。"""
    success: bool
    metrics: Dict[str, Any]
    logs: str
    errors: List[str]
    artifacts: List[str]
    runtime_seconds: float
    environment_info: Dict[str, str]


class NodeError(TypedDict):
    """单个节点的错误记录，用于错误追踪与降级决策。"""
    node_name: str
    error_type: str
    error_message: str
    error_detail: Optional[str]
    timestamp: str
    retry_count: int
    resolved: bool


class FixLoopRecord(TypedDict):
    """单轮 execution↔coding 修复循环的记录。"""
    round_number: int
    error_summary: str
    error_category: str
    fix_strategy: str
    timestamp: str


class GlobalState(TypedDict):
    """LangGraph 全局状态，贯穿整个工作流的唯一数据契约。"""
    llm_config: LLMConfig
    user_input: str
    input_type: str
    paper_meta: Optional[PaperMeta]
    paper_analysis: Optional[PaperAnalysis]
    resource_info: Optional[ResourceInfo]
    reproduction_plan: Optional[ReproductionPlan]
    code_output_dir: Optional[str]
    execution_result: Optional[ExecutionResult]
    report_path: Optional[str]
    current_step: str
    execution_mode: ExecutionMode
    sandbox_type: str
    error: Optional[str]
    messages: List[Dict[str, str]]
    node_errors: List[NodeError]
    degraded_nodes: List[str]
    retry_budget_remaining: int
    fix_loop_count: int
    fix_loop_history: List[FixLoopRecord]
    user_fix_decision: Optional[str]
    workspace_dir: str


def create_initial_state(
    user_input: str,
    llm_config: LLMConfig,
    workspace_dir: Optional[str] = None,
) -> GlobalState:
    """创建初始 GlobalState，填充全部默认值。"""
    from config import WORKSPACE_DIR, MAX_TOTAL_LLM_CALLS
    return GlobalState(
        llm_config=llm_config,
        user_input=user_input,
        input_type="arxiv_id",
        paper_meta=None,
        paper_analysis=None,
        resource_info=None,
        reproduction_plan=None,
        code_output_dir=None,
        execution_result=None,
        report_path=None,
        current_step="start",
        execution_mode=ExecutionMode.FULL,
        sandbox_type="venv",
        error=None,
        messages=[],
        node_errors=[],
        degraded_nodes=[],
        retry_budget_remaining=MAX_TOTAL_LLM_CALLS,
        fix_loop_count=0,
        fix_loop_history=[],
        user_fix_decision=None,
        workspace_dir=workspace_dir or str(WORKSPACE_DIR),
    )
