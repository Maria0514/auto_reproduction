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
    """planning 节点输出：经用户审批的复现计划。

    Sprint 5 变更（架构 sp5 §7.5 / §8 总表）：
        - expected_results: **sp5 唯一 breaking**——Dict[str, Any] → List[Dict[str, Any]]。
          每条形如 ``{"description": str, "trend": {"metric": str, "greater": str,
          "lesser": str} | None}``（定性描述 + 可选可机验趋势结构，禁编造数值）。
          下游（reporting 回验 / 渲染）对旧 dict 形态防御性容忍（§7.5，R-5）。
        - required_credentials: 新增。planning ReAct 产出 + map 回填默认 []；
          每条恰含 purpose_key / purpose 两键（Dict[str, str]），供 coding gate 与
          计划审核页只读展示消费。**绝不存凭证值本身**（值走 .secrets / 会话覆盖层）。

    Sprint 7 变更（S7-08，架构 sp7 §18.1 裁决 1/4 + §18.1.2）：新增两个**扁平顶层键**，
    承载"本机能不能跑得动 / 跑不动是怎么缩的"这一判断结果：
        - scale_reduced: 计划是否已按本机可跑规模缩过（更小的模型 / 数据子集 /
          减少实验组等）。缺省 False，**缺键 ≡ False**（"没缩规模"是安全默认）。
        - local_fit_note: 给用户看的一段通俗中文说明——本机够不够、缺口是什么、
          按什么方式缩的、本次预计占用（GPU 张数 / 显存 / 磁盘增量 / 预计时长）。
          缺省 ""，**缺键 ≡ ""**。
    纪律三条（与 sp5 新键同款，另加一条 S7-08 特有的）：
        1. 下游消费一律 `.get()` 防御读——旧 checkpoint 无这两键时不 KeyError、
           不造哨兵值（架构 §18.1 裁决 4）；
        2. 两键**不进** planning 输出契约的 `required`（缺省已是安全值，进 required
           会触发 react_base finalize 多烧一次 LLM 调用，架构 §18.1 裁决 3）；
        3. **scale_reduced 是模型自报的判断结果，不是系统算出的"计划与实测偏离"标记**
           （Maria 裁决 7：不留偏离痕）。系统不产出任何机器可读的偏离信号，
           两者语义不可混同。

    Sprint 8 变更（S8-01 扩围，架构 sp8 §2.5 / AR-S8-15）：新增 1 个扁平顶层键，
    承载"对这篇论文而言，论文核心结论得到印证具体指什么"这条判定依据：
        - success_criteria: 由论文分析 + 规划针对**本篇论文**推导出来、经用户在计划
          审核页审核批准的达标线。缺省 ""，**缺键 ≡ ""**（下游一律
          ``.get("success_criteria") or ""`` 防御读，旧 checkpoint 不 KeyError）。
    纪律三条：
        1. 🔴 **它是单个字符串，不是"档位→达标线"的字典**（架构 §2.5.2 方案 A）。
           结果分级的语义边界由系统统一定义、跨论文恒定，计划只填本篇达标线——
           做成字典/列表会把分级名变成计划可写的键，等于**结构上给出越权入口**；
           单个字符串则让计划**连能写越权内容的字段都没有**。任何"看起来更灵活"的
           容器形态一律否决。
        2. 它**进** planning 输出契约的 `required`——这是对 S7-08 纪律 2 的有意背离
           （架构 §2.5.5 已留档）：缺省 "" 在这里**不是**安全值，等于这篇论文没有
           判定依据、整条判定链当场断，故"缺失时多烧一次 schema 重生成"的代价正当。
        3. 它是计划字段、随计划一次落盘，下游节点**只读不写**（零幂等风险）。
    """
    plan_summary: str
    environment: Dict[str, Any]
    data_preparation: List[str]
    code_strategy: str
    execution_steps: List[Dict[str, str]]
    expected_results: List[Dict[str, Any]]
    estimated_time: str
    deliverables: List[str]
    user_feedback: Optional[str]
    approved: bool
    required_credentials: List[Dict[str, str]]
    # === Sprint 7 新增（S7-08，架构 sp7 §18.1.2）===
    scale_reduced: bool
    local_fit_note: str
    # === Sprint 8 新增（S8-01 扩围，架构 sp8 §2.5；AR-S8-15：与 planning.py 两处
    # 构造点原子同批，R-S8-42）===
    success_criteria: str


class ExecutionResult(TypedDict):
    """execution 节点输出：代码执行与验证结果。

    Sprint 5 新增 4 键（架构 sp5 §7.6 / §7.10 / §8 总表；仅此处做 TypedDict 键声明，
    execution.py 两处构造点补齐默认值属 T-S5-2-6）：
        - step_reconciliation: 步骤对账 {"planned": int, "planned_actionable": int,
          "executed": int, "completed": int,
          "unexecuted_steps": [{"index": int, "step_name": str}], "extra_commands": [str]}；
          ⚠ planned 是**原始步数**（"计划共 N 步"陈述 + agent 自报 step_index 的合法区间），
          planned_actionable 是**可执行步数**——完成度分母只认后者（BUG-S7-11-01）；
        - budget_truncated: 执行因轮次预算截断（reporting 截断声明，AC-S5-12）；
        - metrics_groups: 多组指标 {组名: {指标: 值}}（execution _collect_grouped_metrics 写）；
        - degraded_credentials: 本次执行降级的凭证 purpose_key 列表（自 state 快照）。
    下游消费一律 .get() 防御读（兼容旧 checkpoint 无新键，R-6）。
    """
    success: bool
    metrics: Dict[str, Any]
    logs: str
    errors: List[str]
    artifacts: List[str]
    runtime_seconds: float
    environment_info: Dict[str, str]
    step_reconciliation: Dict[str, Any]
    budget_truncated: bool
    metrics_groups: Dict[str, Dict[str, Any]]
    degraded_credentials: List[str]


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
    """单轮 execution↔coding 修复循环的记录。

    Sprint 7 S7-05（修复循环记忆增强，档 B，架构 v1.1 §13.7）新增 2 字段：
        - fix_note: coder 本轮自述"问题定位 + 修复逻辑"一两句（≤_FIX_NOTE_MAX_CHARS）；
          由 coding 侧 _map_coding_result 写 last_fix_note、execution 侧 _append_fix_record 取。
        - files_touched: coder 本轮改的文件列表（来自 write_code_file 成功记录）；同链路。
    两字段均 TypedDict 加键——旧 checkpoint（task-99eef17bccf2 现场无此 2 键）由消费侧
    ``.get("fix_note", "")`` / ``.get("files_touched", [])`` 兜底，不 KeyError。**既有
    round_number/error_summary/error_category/fix_strategy/timestamp 不变、顺序不动。**
    """
    round_number: int
    error_summary: str
    error_category: str
    fix_strategy: str
    timestamp: str
    # === Sprint 7 S7-05 新增（修复循环记忆增强，档 B，旧 checkpoint 兼容）===
    fix_note: str
    files_touched: List[str]


class GlobalState(TypedDict):
    """LangGraph 全局状态，贯穿整个工作流的唯一数据契约。

    Sprint 2 breaking change（架构 §2.1.1.bis / dev-plan A1+A3）：
        - llm_config_set: LLMConfigSet 是多模型权威配置源（default + 节点级 overrides）；
        - 过渡期镜像字段 llm_config 已于 A3 完成（react_base.py 改读 llm_config_set）后
          **彻底移除**——节点级 LLM 路由统一走 resolve_llm_config(llm_config_set, node_name)，
          不再存在任何 state["llm_config"] 直读路径。
    """
    llm_config_set: LLMConfigSet          # Sprint 2 权威配置源（唯一 LLM 配置入口）
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
    # 全局级人类可审核备注（与 PaperAnalysis.analysis_notes 区分：后者是论文分析内嵌字段，
    # 此处是贯穿流程的顶层追加通道）。resource_scout 的 [SEARCH_LOG]/[QUALITY_WARN]、
    # planning 的 [CANCELLED]/[PLANNING_FALLBACK] 等标记经 read-modify-write 累加到此通道。
    # 注意：必须声明为 GlobalState 通道，否则节点写入会被 LangGraph 静默丢弃（B2/B3 实证）。
    analysis_notes: str
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
    # === S2-13 用户提供仓库统一抓取分析通道（架构 §2.13.7）===
    # 必须声明为 GlobalState 通道，否则节点写入会被 LangGraph 静默丢弃（B2/B3 实证）。
    _planning_pending_repo_url: Optional[str]   # switch_repo 待 ReAct 抓取的 URL（消费后清空）
    _planning_switch_failed: bool               # 上一轮 switch_repo 抓取失败标记（UI 强制重填用）
    # === Sprint 3 新增（dev_loop 修复循环路由 + 子预算，架构 §5 / §7 回问 1+4）===
    # 下划线前缀标识"内部字段，UI 不直接展示原始字段名"（沿用 sp2 _planning_revise_count 范式）。
    # 二者均为单值，last-write-wins 正确，**不加 reducer**（must-fix-1：绝不给任何 List 字段加 reducer）。
    _dev_loop_route: Optional[str]              # 路由意图标记（execution 写，_route_after_execution 读；如 "retry_coding"）
    _dev_loop_llm_calls: int                    # 修复循环子预算累计（coding/execution 在修复回合内 read-modify-write 累加；默认 0）
    # Sprint 4 语义收窄（dev-plan sp4 §7.3 / 架构 sp4 §4.2 落点 B，A2 顺带备注）：
    # _dev_loop_llm_calls 自 sp4 起仅由 execution 编排层单点累计（coding 本就不写此字段），
    # 数值行为对 sp3 既有用例向后兼容。
    # === Sprint 4 新增（用户交互通道，S4-10，架构 sp4 §7.2 / §12.1）===
    # 两字段均单值 / Dict 单点写（编排层 read-modify-write 整 dict 回写，last-write-wins 安全），
    # **无 reducer**（must-fix-1：绝不给任何字段加 Annotated / operator.add）。
    # 必须显式声明为 GlobalState 通道 + create_initial_state 给默认值，
    # 否则节点写入会被 LangGraph 静默丢弃（B2/B3 实证）。
    # - pending_user_input：当前待回答请求的快照（question/is_sensitive/purpose_key），**绝不存答案**；
    #   MVP 为通道声明占位（编排层可观测性镜像，resume 后清 None），UI 实际渲染走
    #   interrupt payload（架构 sp4 §7.2 推荐）。
    # - collected_inputs：本任务内已收集的**非敏感**项（purpose_key → value）；
    #   敏感项（is_sensitive=True）绝不进入 state，跨任务复用只靠 .secrets（架构 sp4 §6.3）。
    pending_user_input: Optional[Dict]
    collected_inputs: Dict[str, str]
    # === Sprint 5 新增（诚实性治理三通道，架构 sp5 §8 总表）===
    # 三字段均为单值通道，last-write-wins 正确，**绝不加 reducer**
    # （must-fix-1：绝不给任何字段加 Annotated / operator.add）。
    # 必须显式声明为 GlobalState 通道 + create_initial_state 给默认值，
    # 否则节点写入会被 LangGraph 静默丢弃（B2/B3 实证）。
    # 下游消费一律 .get() 防御读（兼容旧 checkpoint 无新键，R-5/R-6）。
    # - credential_degradations：coding 前置门（gate）单点整 dict 回写
    #   {purpose_key: 降级说明}；coding 上下文 / execution 收尾 / reporting 标注消费。
    # - simulation_notice：coding _map_coding_result 单点写（LLM 自述模拟声明，
    #   缺失回填 None 属诚实语义）；reporting 标注 + 强制声明节消费。
    # - honesty_audit：reporting 单点写（诚实性审计返回契约扩展）；
    #   UI 报告页 / 测试断言消费。
    credential_degradations: Dict[str, str]
    simulation_notice: Optional[str]
    honesty_audit: Optional[Dict]
    # === Sprint 7 S7-05 新增（修复循环记忆增强，档 B，架构 v1.1 §13.7）===
    # coding→execution 单向传递通道（单点由 coding 的 _map_coding_result 写、execution 的
    # _append_fix_record 取写进 FixLoopRecord）。均单值 / List 单点写、last-write-wins 正确，
    # **绝不加 reducer**（must-fix-1）。旧 checkpoint（task-99eef17bccf2 现场无此 2 键）由
    # 消费侧 ``.get("last_fix_note", "")`` / ``.get("last_files_written", [])`` 兜底，不 KeyError。
    # - last_fix_note：coder 上轮在 <result> 自述"问题定位 + 修复逻辑"一两句（截断到
    #   _FIX_NOTE_MAX_CHARS）；R-PC4 安全（值只经 map→FixLoopRecord→HumanMessage 动态尾部，
    #   从不进 SystemMessage 稳定前缀）。
    # - last_files_written：coder 上轮 write_code_file 成功写入的文件绝对路径列表。
    last_fix_note: str
    last_files_written: List[str]

    # === Sprint 7 S7-06 新增（只读环境探测结论落点，架构 v1.3 §15）===
    # resource_scout 单点写（_map_resource_scout_result 从 ReAct 工具历史确定性提取，
    # 非 LLM <result> 字段）；planning 单点读（_format_planning_context）。单值、
    # last-write-wins 正确，**绝不加 reducer**。旧 checkpoint 无此键由消费侧
    # ``.get("local_env_facts", "")`` 兜底，不 KeyError。
    # 值 = 预渲染多行字符串（本机实测环境事实），空串表示"未知"。
    local_env_facts: str


def completion_denominator(recon: Any) -> Optional[int]:
    """``step_reconciliation`` 完成度分母的**单一取数点**（BUG-S7-11-01，2026-08-01）。

    取 ``planned_actionable``（可执行步数）；该键缺失 / 非 int（旧 checkpoint 快照、
    手工构造的对账 dict，R-6）时回落 ``planned``——回落即"退回修复前口径"，是保守行为
    不是新语义。两者都取不到 → ``None``（调用方一律按"无从判定"处理）。

    ⚠ **判定层与展示层必须都走这一个口径**：
        - 判定：``execution._completion_insufficient`` / ``_apply_incomplete_execution``；
        - 展示：``reporting._render_annotation_notices`` / ``_render_reconciliation``。
    dev-plan §49.2 第 7 条「全系统只有一个完成数、报告内不可能再自相矛盾」；两份实现
    必然漂移出"判定说成功、横幅说没跑完"的自相矛盾报告（CP-7.9-3 明令该组合为零）。

    放在本模块（而非 ``execution``）是因为 ``reporting`` 有纯度红线（CP-3.3-4：不得
    import 任何带 LLM 的模块），而 ``core.state`` 是两侧都已依赖的无副作用契约层；
    ``step_reconciliation`` 的键契约也正声明在本文件的 ``ExecutionResult`` docstring 里。
    """
    if not isinstance(recon, dict):
        return None
    for key in ("planned_actionable", "planned"):
        v = recon.get(key)
        if isinstance(v, int) and not isinstance(v, bool):
            return v
    return None


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
        - state 中仅写入 ``llm_config_set``（唯一权威配置源）；A3 完成后过渡期镜像
          字段 ``llm_config`` 已移除，节点级 LLM 路由统一走 resolve_llm_config。

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
    elif isinstance(llm_config, dict) and isinstance(llm_config.get("default"), dict):
        # 新形态 LLMConfigSet 入参；规整 overrides 字段（缺失时填空 dict）
        new_cfg = cast(LLMConfigSet, llm_config)
        overrides = new_cfg.get("overrides") or {}
        config_set = {
            "default": new_cfg["default"],
            "overrides": dict(overrides),
        }
    else:
        raise ValueError(
            "create_initial_state: llm_config 必须是 LLMConfig（含 base_url）"
            " 或 LLMConfigSet（含 default 子配置）"
        )

    return GlobalState(
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
        analysis_notes="",
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
        _planning_pending_repo_url=None,
        _planning_switch_failed=False,
        _dev_loop_route=None,
        _dev_loop_llm_calls=0,
        pending_user_input=None,
        collected_inputs={},
        credential_degradations={},
        simulation_notice=None,
        honesty_audit=None,
        last_fix_note="",
        last_files_written=[],
        local_env_facts="",
    )
