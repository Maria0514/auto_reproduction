"""全局状态定义 -- 所有节点间数据流转的唯一契约。

本模块定义贯穿整个 LangGraph 工作流的全局状态结构。
所有 TypedDict 和 Enum 定义与技术架构文档第 4 章保持严格一致。
"""

from typing import TypedDict, Optional, List, Dict, Any, Literal, Union, cast
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


# Sprint 2 新增：支持节点级 LLM 覆写的 4 个节点名（与 PRD §2.4 / AC-S2-11 强一致）
NodeName = Literal["paper_intake", "paper_analysis", "resource_scout", "planning"]


class LLMConfigSet(TypedDict):
    """多模型 LLM 配置集合（Sprint 2 新增，架构 §2.1.1.bis）。

    - default: 全局默认配置，**必填**；任何节点未在 overrides 中显式覆写时回退到此条。
    - overrides: 节点级覆写表，key 限定为 4 个支持覆写的节点名。**允许为空 dict**
                  （等同于"单一全局配置"模式，向后兼容 sp1 既有 UX）。
    """
    default: LLMConfig
    overrides: Dict[str, LLMConfig]


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
    # === Sprint 2 新增（C 双语字段，PRD §4.7.3）===
    # 英文为主字段（保留原文供下游检索），新增 *_zh 字段给 UI 展示中文。
    # LLM 漏写时由 _map_intake_result backfill 回退为对应英文主字段值并标记 degraded。
    title_zh: Optional[str]
    abstract_zh: Optional[str]
    tldr_zh: Optional[str]


class PaperAnalysis(TypedDict):
    """paper_analysis 节点输出：论文深度分析结果。

    注意（Sprint 2 起，PRD §4.7.3 字段语义反转 / R-S2-05）：
        - method_summary / hardware_requirements 主字段语义自 sp2 起由英文反转为**中文**，
          供 planning / reporting 等中文 prompt 节点直接消费；
        - method_summary_en / hardware_requirements_en 为新增**英文备份**字段，
          coding 节点等跨语言检索路径消费，避免中文 prompt 喂代码生成造成注释中英混杂；
        - datasets / metrics / framework / sections_read 等事实层字段**保持英文**，
          禁止翻译，下游 resource_scout / coding 用英文做检索匹配（PRD §4.7.5）。
    """
    method_summary: str  # Sprint 2 起为中文主字段（语义反转，PRD §4.7.3）
    key_formulas: List[str]
    datasets: List[str]
    metrics: List[str]
    hyperparams: Dict[str, Any]
    hardware_requirements: str  # Sprint 2 起为中文主字段（语义反转，PRD §4.7.3）
    framework: Optional[str]
    baseline_results: Dict[str, Any]
    sections_read: List[str]
    analysis_notes: str
    # === Sprint 2 新增（D 中优英备字段，PRD §4.7.3）===
    # 主字段中文，*_en 备份英文；LLM 漏写时回退为对应中文主字段值并标记 degraded。
    method_summary_en: Optional[str]
    hardware_requirements_en: Optional[str]


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
    # === Sprint 2 新增（PRD §4.1 / technical-architecture §4 联动）===
    # git clone 后的本地绝对路径，sp3 coding 节点直接使用。
    local_path: Optional[str]


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
    """LangGraph 全局状态，贯穿整个工作流的唯一数据契约。

    Sprint 2 breaking change（架构 §2.1.1.bis / dev-plan A1）：
        - 新增 llm_config_set: LLMConfigSet（多模型权威配置源）；
        - 保留 llm_config: LLMConfig 作为**过渡期向后兼容字段**——
          create_initial_state 兜底层始终把 llm_config_set["default"] 镜像写入 llm_config，
          让 sp1 测试断言（如 test_sprint1_smoke.py:229）与 react_base.py:825
          的老路径在 A3 单行 diff 之前继续工作；
        - sp3 待 A3 完成 react_base 单行 diff 后，可彻底移除 llm_config 字段（仅保留 llm_config_set）。
    """
    llm_config: LLMConfig                 # 过渡期向后兼容字段（A3 后可移除）
    llm_config_set: LLMConfigSet          # Sprint 2 权威配置源
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
    # === Sprint 2 新增（planning revise 透明计数 + 用户反馈，架构 §4.7）===
    # 下划线前缀标识"内部字段，UI 不直接展示原始字段名"；
    # 语义仅为透明展示与软提示判定（PLANNING_SOFT_HINT_THRESHOLD=5），**不做硬上限拦截**
    # （PRD §2.3 / Q-S2-03 RESOLVED，硬上限语义已废弃）。
    _planning_revise_count: int
    _planning_user_feedback: Optional[str]


def _is_legacy_llm_config(value: Any) -> bool:
    """判定入参是否为 sp1 老形态 LLMConfig（dict 含 base_url 但不含 default）。"""
    if not isinstance(value, dict):
        return False
    if "default" in value:
        return False
    return "base_url" in value


def create_initial_state(
    user_input: str,
    llm_config: Union[LLMConfig, LLMConfigSet],
    workspace_dir: Optional[str] = None,
) -> GlobalState:
    """创建初始 GlobalState，填充全部默认值。

    Sprint 2 升级（架构 §2.1.1.bis 兼容性兜底）：
        - 形参 ``llm_config`` 同时接受 sp1 老形态 LLMConfig 与 sp2 新形态 LLMConfigSet；
        - 老形态入参自动包装为 ``{"default": cfg, "overrides": {}}``；
        - 新形态入参直接透传，但要求至少含合法 ``default`` 字段；
        - state 中同时写入 ``llm_config_set``（权威）与 ``llm_config``（过渡期兼容镜像，
          值取 ``llm_config_set["default"]``），保 sp1 168/168 测试基线零退化。

    Args:
        user_input: 用户输入（如 arxiv_id 字符串）。
        llm_config: sp1 单条 LLMConfig 或 sp2 LLMConfigSet。
        workspace_dir: 自定义工作目录路径；缺省走 config.WORKSPACE_DIR。

    Returns:
        填充全部默认值的 GlobalState 实例。
    """
    from config import WORKSPACE_DIR, MAX_TOTAL_LLM_CALLS

    if _is_legacy_llm_config(llm_config):
        legacy_cfg = cast(LLMConfig, llm_config)
        config_set: LLMConfigSet = {
            "default": legacy_cfg,
            "overrides": {},
        }
        default_cfg: LLMConfig = legacy_cfg
    elif isinstance(llm_config, dict) and isinstance(llm_config.get("default"), dict):
        # 新形态 LLMConfigSet 入参；规整 overrides 字段（缺失时填空 dict）
        new_cfg = cast(LLMConfigSet, llm_config)
        overrides = new_cfg.get("overrides") or {}
        config_set = {
            "default": new_cfg["default"],
            "overrides": dict(overrides),
        }
        default_cfg = config_set["default"]
    else:
        raise ValueError(
            "create_initial_state: llm_config 必须是 LLMConfig（含 base_url）"
            " 或 LLMConfigSet（含 default 子配置）"
        )

    return GlobalState(
        llm_config=default_cfg,
        llm_config_set=config_set,
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
        _planning_revise_count=0,
        _planning_user_feedback=None,
    )
