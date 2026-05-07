# 论文自动复现系统 -- 技术架构文档

**产品名称**：Auto-Reproduction
**版本**：v1.0
**日期**：2026-05-06
**状态**：正式定稿

---

## 1. 技术栈总览

| 类别 | 技术选型 | 版本要求 | 说明 |
|------|---------|---------|------|
| 编程语言 | Python | >= 3.10 | 项目主语言 |
| Agent 编排框架 | LangGraph | >= 0.2.0 | 多 Agent 工作流编排与状态管理 |
| 前端框架 | Streamlit | >= 1.35.0 | 轻量 Web UI |
| 论文数据 SDK | deepxiv-sdk | >= 0.2.5 | 仅使用 Reader 类，不使用 SDK 自带 Agent |
| LLM 接口 | OpenAI 兼容 API | - | 用户自配 base_url + model + api_key |
| 状态持久化 | LangGraph SqliteSaver | 随 langgraph 版本 | 基于 SQLite 的 checkpoint 持久化 |
| 本地沙箱 | Python venv | Python 内置 | v1 本地隔离执行环境 |
| 远程沙箱 | Docker | >= 24.0 | v2 实现远程隔离执行 |
| HTTP 客户端 | requests | >= 2.31.0 | deepxiv-sdk 依赖，API 调用 |
| 类型检查 | TypedDict / dataclasses | Python 内置 | 全局状态与数据结构定义 |

---

## 2. 系统架构概览

系统采用五层分层架构，各层职责清晰、边界明确：

```
+----------------------------------------------------------+
|                    Streamlit UI 层                         |
|  页面1: 论文输入 | 页面2: 分析进度 | 页面3: 计划审核      |
|  页面4: 执行监控 | 页面5: 结果报告                         |
+----------------------------------------------------------+
                          |
                          v
+----------------------------------------------------------+
|               LangGraph 编排层 (core/graph.py)            |
|  主图定义 | 节点路由 | 中断恢复 | SqliteSaver Checkpoint   |
+----------------------------------------------------------+
                          |
                          v
+----------------------------------------------------------+
|               Agent 节点层 (core/nodes/)                  |
|  paper_intake | paper_analysis | resource_scout           |
|  planning | coding | execution | reporting                |
+----------------------------------------------------------+
                          |
                          v
+----------------------------------------------------------+
|                 工具层 (core/tools/)                       |
|  deepxiv_tools | git_tools | shell_tools | file_tools    |
+----------------------------------------------------------+
                          |
                          v
+----------------------------------------------------------+
|                   外部服务层                               |
|  deepxiv API | GitHub (git clone) | Papers With Code API | LLM API | 本地文件系统 |
+----------------------------------------------------------+
```

**数据流方向**：用户操作通过 Streamlit UI 触发 LangGraph 主图执行，主图按顺序调度各 Agent 节点，节点调用工具层完成具体任务，工具层与外部服务交互获取数据或执行操作。全局状态 (GlobalState) 在各节点间流转，由 LangGraph 统一管理和持久化。

---

## 3. Agent 编排设计

### 3.1 节点定义

系统的核心工作流包含 7 个节点，每个节点对应产品设计中的一个步骤：

| 节点名 | 对应步骤 | 核心职责 | 输入 | 输出 |
|--------|---------|---------|------|------|
| `paper_intake` | 步骤1: 论文输入与解析 | 接收 UI 层传入的已确认 arXiv ID，调用 deepxiv Reader 获取论文元数据 | 用户已确认的 arXiv ID（由 UI 层完成搜索与确认） | PaperMeta |
| `paper_analysis` | 步骤2: 深度论文分析 | 渐进式阅读论文，提取复现所需的关键信息 | PaperMeta | PaperAnalysis |
| `resource_scout` | 步骤3: 资源搜集与评估 | 通过 deepxiv github_url、Papers With Code API、web search 搜索候选仓库，git clone 后本地评估仓库质量；全自动完成仓库搜索与质量评分，不涉及用户交互 | PaperMeta + PaperAnalysis | ResourceInfo（含候选仓库列表及质量评分排序，与自动选出的推荐仓库） |
| `planning` | 步骤4: 复现规划 | 综合分析结果和资源信息，生成复现计划 | PaperAnalysis + ResourceInfo | ReproductionPlan |
| `coding` | 步骤5: 编码与环境搭建 | 适配已有仓库或从零生成复现代码；code_only 模式下按用户确认的交付标准清单编码，至少满足最低基准线 | ReproductionPlan | 代码文件 + 环境配置 |
| `execution` | 步骤6: 执行与测试验证 | 在沙箱中执行复现实验，收集结果 | 代码 + 环境 | ExecutionResult |
| `reporting` | 步骤7: 报告生成 | 对比复现结果与论文数据，生成报告 | ExecutionResult + PaperAnalysis | 复现报告 |

### 3.2 编排方式

LangGraph 主图采用**顺序编排**，节点按固定顺序依次执行。注意：paper_intake 之前的论文搜索与用户确认交互在 Streamlit UI 层完成，graph 启动时已持有用户确认的 arXiv ID。

**resource_scout 为全自动节点**：resource_scout 自动完成仓库搜索、质量评分与排序，不设置 interrupt，不涉及用户交互。候选仓库的用户确认合并到 `planning` 节点的 interrupt 审核中——planning 审核页面会展示 resource_scout 输出的候选仓库列表（含质量评分），用户可在审核计划时查看并确认仓库选择，或要求更换仓库。若用户要求更换仓库，planning 节点基于更换后的仓库信息重新生成复现计划。

```
                        [START]
                           |
                           v
                    paper_intake
                           |
                           v
                   paper_analysis
                           |
                           v
                   resource_scout
                           |
                           v
                      planning
                           |
                     [INTERRUPT]  <-- 人在回路：用户审核复现计划
                           |
                   (用户确认/修改)
                           |
                           v
                       coding  <-----+
                           |        |
                           v        | 修复循环（最多 3 轮）
                     execution -----+
                           |
                     (执行成功 或 修复次数/预算耗尽)
                           |
                           v
                     reporting
                           |
                           v
                        [END]
```

> **修复循环说明**：execution 节点执行失败时，若 `fix_loop_count < 3` 且 `retry_budget_remaining > 0`，系统自动将错误信息回传给 coding 节点进行代码修复，然后再次执行。此循环最多进行 3 轮。若 3 轮修复均失败或预算耗尽，流程暂停等待用户决策（详见 §12.6 / §12.8）。

### 3.3 人在回路机制

- **论文确认不属于 graph 内中断**：论文搜索与用户确认属于 graph 启动前的 UI 交互（在 Streamlit 页面中完成），不属于 graph 内的人在回路机制。graph 内唯一的 interrupt 点是 `planning` 节点之后。
- **resource_scout 不设中断**：resource_scout 为全自动节点，仓库搜索与评分过程不涉及用户交互。候选仓库的用户确认合并到 `planning` 节点的 interrupt 审核中。
- **中断点**：`planning` 节点完成后，使用 LangGraph 的 `interrupt()` 函数暂停图执行
- **用户审核**：Streamlit UI 展示复现计划及候选仓库列表（含质量评分），用户可进行以下操作：
  - **确认**：同意计划及仓库选择，恢复图执行，进入 `coding` 节点
  - **修改**：调整计划内容后重新提交，`planning` 节点根据用户反馈修订计划
  - **更换仓库**：查看候选仓库列表，选择不同的仓库（或要求重新搜索），`planning` 节点基于更换后的仓库信息重新生成计划
  - **切换模式**：选择"只编码不执行"，系统跳过 `execution` 节点
- **恢复执行**：用户确认后，通过 `graph.invoke(Command(resume=user_feedback))` 恢复图执行

### 3.4 条件路由

在 `planning` 节点的人在回路审核后，根据用户选择进行条件路由：

- `mode == "full"`：执行 coding -> execution -> reporting 完整链路
- `mode == "code_only"`：执行 coding -> reporting（跳过 execution，reporting 生成代码交付报告）

#### code_only 模式交付标准

code_only 模式的交付标准采用**"最低基准线 + agent 草拟 + 用户审核"**机制：

1. **最低基准线（硬性要求）**：以下为 code_only 模式必须满足的最低交付物清单，无论论文内容如何均需包含：

   | 交付物 | 说明 |
   |--------|------|
   | `README.md` | 项目说明、复现步骤、依赖说明 |
   | `requirements.txt` | Python 依赖清单 |
   | 入口脚本（如 `main.py` / `train.py`） | 可运行的主程序入口 |
   | 核心实现代码 | 论文核心方法的代码实现 |
   | 通过基础语法检查 | 代码无语法错误（`py_compile` 检查通过） |

2. **planning 节点草拟扩展**：`planning` 节点在最低基准线基础上，根据论文具体内容草拟完整的交付标准清单（如数据预处理脚本、配置文件、评估脚本、可视化脚本等），写入 `ReproductionPlan.deliverables` 字段。
3. **用户审核确认**：用户在 `planning` 节点的 interrupt 审核时，查看并确认/修改交付标准清单。最终确认的清单作为 `coding` 节点的编码依据。

---

## 4. 全局状态定义

所有节点共享一个全局状态对象，由 LangGraph 管理。以下为完整的 TypedDict 定义：

```python
from typing import TypedDict, Optional, List, Dict, Any
from enum import Enum


# ========== LLM 配置 ==========

class LLMConfig(TypedDict):
    """LLM 服务配置，支持任何 OpenAI 兼容 API"""
    base_url: str           # API 基础地址，如 "https://api.openai.com/v1"
    model: str              # 模型标识，如 "gpt-4o", "claude-sonnet-4-20250514", "deepseek-chat"
    api_key: str            # API 密钥
    temperature: float      # 生成温度，默认 0.3
    max_tokens: int         # 最大输出 token 数，默认 4096


# ========== 论文元数据 ==========

class PaperMeta(TypedDict):
    """步骤1输出：论文基础元数据"""
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


# ========== 论文分析结果 ==========

class PaperAnalysis(TypedDict):
    """步骤2输出：深度论文分析结果"""
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


# ========== 资源信息 ==========

class RepoInfo(TypedDict):
    """单个代码仓库的评估信息"""
    url: str
    source: str                     # 来源标识："deepxiv" | "paperswithcode" | "websearch"
    is_official: bool
    stars: Optional[int]            # 可选，GitHub API 不可用时为 None（后续版本通过 GitHub Search API 获取）
    forks: Optional[int]            # 可选，同上
    last_commit_date: Optional[str] # 通过 git log 获取最近提交日期（本地分析）
    commit_count_recent: Optional[int]  # 近 6 个月提交数（通过 git log 本地分析）
    has_readme: bool                # 通过检查克隆仓库目录结构获取
    has_requirements: bool          # 通过检查克隆仓库目录结构获取
    dir_structure: Optional[List[str]]  # 仓库顶层目录结构（本地分析）
    quality_score: float            # 基于本地可获取信息计算

# quality_score 计算依据（MVP 阶段，基于本地 git 分析）：
#   - is_official: 官方仓库加权
#   - last_commit_date: 最近提交越新分数越高
#   - commit_count_recent: 近期提交活跃度
#   - has_readme: 有 README 加分
#   - has_requirements: 有依赖声明加分
#   - dir_structure: 目录结构完整度（如含 src/、tests/、docs/ 等）
# 后续版本增强：引入 GitHub Search API 获取 stars、forks、issues 等社区指标

class ResourceInfo(TypedDict):
    """步骤3输出：资源搜集与评估结果"""
    repos: List[RepoInfo]               # 候选仓库列表（按 quality_score 降序排列），planning 审核时展示给用户
    selected_repo: Optional[RepoInfo]   # resource_scout 自动推荐的仓库，用户可在 planning 审核时更换
    pretrained_models: List[Dict[str, str]]
    datasets_found: List[Dict[str, str]]
    resource_strategy: str              # "use_repo" | "from_scratch" | "hybrid"


# ========== 复现计划 ==========

class ReproductionPlan(TypedDict):
    """步骤4输出：复现计划"""
    plan_summary: str
    environment: Dict[str, Any]
    data_preparation: List[str]
    code_strategy: str
    execution_steps: List[Dict[str, str]]
    expected_results: Dict[str, Any]
    estimated_time: str
    deliverables: List[str]              # 交付物清单（由 planning 节点基于最低基准线草拟，用户在 interrupt 审核时确认；code_only 模式下作为 coding 节点的编码依据）
    user_feedback: Optional[str]
    approved: bool


# ========== 执行结果 ==========

class ExecutionResult(TypedDict):
    """步骤6输出：执行与验证结果"""
    success: bool
    metrics: Dict[str, Any]
    logs: str
    errors: List[str]
    artifacts: List[str]
    runtime_seconds: float
    environment_info: Dict[str, str]


# ========== 错误追踪（§12.3）==========

class NodeError(TypedDict):
    """单个节点的错误记录"""
    node_name: str              # 发生错误的节点
    error_type: str             # "transient" | "permanent" | "degraded"
    error_message: str          # 人类可读的错误描述
    error_detail: Optional[str] # 技术细节（堆栈、响应体等）
    timestamp: str              # ISO 8601 时间戳
    retry_count: int            # 已重试次数
    resolved: bool              # 是否已通过重试/降级解决


# ========== 修复循环追踪（§12.6/12.8）==========

class FixLoopRecord(TypedDict):
    """单轮 execution↔coding 修复循环的记录"""
    round_number: int                # 第几轮（1/2/3）
    error_summary: str               # 本轮 execution 失败的错误摘要
    error_category: str              # 错误分类："syntax" | "import" | "runtime" | "oom" | "timeout" | "other"
    fix_strategy: str                # coding 节点采用的修复策略描述
    timestamp: str                   # ISO 8601 时间戳


# ========== 全局状态 ==========

class ExecutionMode(str, Enum):
    """执行模式"""
    FULL = "full"
    CODE_ONLY = "code_only"

class GlobalState(TypedDict):
    """LangGraph 全局状态，贯穿整个工作流"""

    # --- LLM 配置 ---
    llm_config: LLMConfig

    # --- 用户输入 ---
    user_input: str                      # 原始用户输入（arXiv ID / 关键词 / 标题）
    input_type: str                      # "arxiv_id" | "keyword" | "title"

    # --- 各步骤输出 ---
    paper_meta: Optional[PaperMeta]
    paper_analysis: Optional[PaperAnalysis]
    resource_info: Optional[ResourceInfo]
    reproduction_plan: Optional[ReproductionPlan]
    code_output_dir: Optional[str]
    execution_result: Optional[ExecutionResult]
    report_path: Optional[str]

    # --- 流程控制 ---
    current_step: str
    execution_mode: ExecutionMode
    sandbox_type: str                    # "venv" | "docker" | "none"
    error: Optional[str]
    messages: List[Dict[str, str]]

    # --- 错误追踪（§12.3）---
    node_errors: List[NodeError]             # 所有节点的错误历史
    degraded_nodes: List[str]                # 以降级模式完成的节点列表
    retry_budget_remaining: int              # 剩余 LLM 调用预算（默认 50）

    # --- 修复循环追踪（§12.6/12.8）---
    fix_loop_count: int                      # 已完成的 execution↔coding 修复循环轮次（默认 0，上限 3）
    fix_loop_history: List[FixLoopRecord]    # 每轮修复的结构化记录
    user_fix_decision: Optional[str]         # 3 轮失败后用户选择："export_code" | "revise_plan" | "terminate"

    # --- 工作目录 ---
    workspace_dir: str
```

---

## 5. 模块结构

```
auto_reproduction/
├── app.py                        # Streamlit 应用入口
├── config.py                     # 全局配置（路径、默认值、环境变量）
├── requirements.txt              # Python 依赖声明
├── core/
│   ├── __init__.py
│   ├── state.py                  # GlobalState 及所有 TypedDict 定义（含错误追踪字段）
│   ├── errors.py                 # 统一异常层次定义
│   ├── graph.py                  # LangGraph 主图构建、节点注册、路由逻辑（含修复循环）
│   ├── checkpointer.py           # SqliteSaver 初始化与 checkpoint 管理
│   ├── llm_client.py             # OpenAI 兼容 LLM 客户端封装
│   ├── nodes/
│   │   ├── __init__.py
│   │   ├── paper_intake.py       # 节点1: 论文输入与解析
│   │   ├── paper_analysis.py     # 节点2: 深度论文分析
│   │   ├── resource_scout.py     # 节点3: 资源搜集与评估
│   │   ├── planning.py           # 节点4: 复现规划（含 interrupt）
│   │   ├── coding.py             # 节点5: 编码与环境搭建
│   │   ├── execution.py          # 节点6: 执行与测试验证
│   │   └── reporting.py          # 节点7: 报告生成
│   └── tools/
│       ├── __init__.py
│       ├── deepxiv_tools.py      # deepxiv Reader 薄封装
│       ├── git_tools.py          # 仓库克隆与本地仓库分析操作（git clone、git log 分析提交活跃度、检查目录结构等）
│       ├── shell_tools.py        # Shell 命令执行封装
│       └── file_tools.py         # 文件读写操作封装
├── sandbox/
│   ├── __init__.py
│   ├── local_venv.py             # 本地 venv 沙箱管理
│   └── remote_docker.py          # Docker 远程沙箱（v2 实现）
├── ui/
│   ├── __init__.py
│   ├── pages/
│   │   ├── __init__.py
│   │   ├── paper_input.py        # 页面1: 论文输入
│   │   ├── analysis_progress.py  # 页面2: 分析进度
│   │   ├── plan_review.py        # 页面3: 计划审核
│   │   ├── execution_monitor.py  # 页面4: 执行监控
│   │   └── report_view.py        # 页面5: 结果报告
│   └── components/
│       ├── __init__.py
│       ├── llm_config_form.py    # LLM 配置表单组件
│       ├── paper_card.py         # 论文信息卡片组件
│       ├── progress_bar.py       # 进度指示器组件
│       └── error_display.py      # 错误信息展示组件（分层展示）
└── docs/
    ├── product-design-specification.md
    └── technical-architecture.md
```

### 各模块职责说明

| 模块 | 职责 | 关键接口 |
|------|------|---------|
| `core/state.py` | 定义全局状态和所有数据结构（含错误追踪） | `GlobalState`, `NodeError`, `PaperMeta`, `PaperAnalysis` 等 TypedDict |
| `core/errors.py` | 定义统一异常层次 | `AutoReproError`, `TransientError`, `PermanentError`, `LLMError`, `SandboxError` 等 |
| `core/graph.py` | 构建 LangGraph 主图，注册节点和边，含修复循环路由 | `build_graph() -> CompiledGraph` |
| `core/checkpointer.py` | 管理 SqliteSaver 实例 | `get_checkpointer(db_path) -> SqliteSaver` |
| `core/llm_client.py` | 封装 OpenAI 兼容 API 调用 | `create_llm(config: LLMConfig) -> ChatOpenAI` |
| `core/nodes/*` | 各步骤的 Agent 逻辑实现 | 每个模块导出 `def node_fn(state: GlobalState) -> dict` |
| `core/tools/*` | 外部服务调用的工具封装 | 各工具函数，可作为 LangGraph tool 注册 |
| `sandbox/local_venv.py` | 本地 venv 沙箱的创建、依赖安装、命令执行 | `create_venv()`, `install_deps()`, `run_in_venv()` |
| `sandbox/remote_docker.py` | Docker 容器沙箱（v2 预留） | `create_container()`, `run_in_container()` |
| `ui/pages/*` | Streamlit 各页面的 UI 逻辑 | 每个模块为独立的 Streamlit 页面 |
| `ui/components/*` | 可复用的 UI 组件 | Streamlit 组件函数 |

---

## 6. deepxiv-sdk 集成

### 6.1 安装方式

```bash
pip install deepxiv-sdk>=0.2.5
```

基础包即可，不需要 `[agent]` 或 `[all]` extras。

### 6.2 使用的 API

本系统仅使用 deepxiv-sdk 的 `Reader` 类，通过以下方法获取论文数据：

| 方法 | 用途 | 调用场景 |
|------|------|---------|
| `Reader.search(query, size, ...)` | 语义搜索论文 | 用户通过关键词或标题搜索论文 |
| `Reader.brief(arxiv_id)` | 获取论文快速摘要（标题、TLDR、GitHub URL、引用数等） | 步骤1：论文初步确认 |
| `Reader.head(arxiv_id)` | 获取论文元数据与章节结构 | 步骤2：规划阅读路径 |
| `Reader.section(arxiv_id, section_name)` | 按需读取特定章节内容 | 步骤2：逐章节深度分析 |
| `Reader.raw(arxiv_id)` | 获取论文完整 Markdown 内容 | 步骤2：需要完整论文时的兜底方案 |
| `Reader.websearch(query)` | Web 搜索 | 步骤3：搜索补充资源信息 |

### 6.3 不使用的组件

- `Agent` 模块 (`deepxiv_sdk.agent`) -- 本系统有自己的 LangGraph Agent 编排
- `trending()` 方法 -- 不需要热门论文功能
- `pmc_*` 系列方法 -- 不处理生物医学文献
- CLI (`deepxiv` 命令行工具) -- 不使用 SDK 的命令行入口
- MCP Server (`deepxiv_sdk.mcp_server`) -- 不使用 MCP 协议

### 6.4 封装方式

在 `core/tools/deepxiv_tools.py` 中对 Reader 做薄封装，主要目的：

1. **统一错误处理**：捕获 SDK 的各类异常（`APIError`, `RateLimitError`, `NotFoundError` 等），转换为系统内部的统一错误格式
2. **日志记录**：记录所有 API 调用的请求与响应
3. **结果适配**：将 SDK 返回的原始 Dict 映射为系统定义的 TypedDict 结构
4. **Token 管理**：统一管理 deepxiv API token（可选，免费额度每日 1000 次）

```python
# core/tools/deepxiv_tools.py 接口草图

from deepxiv_sdk import Reader

class DeepxivTools:
    def __init__(self, token: Optional[str] = None):
        self.reader = Reader(token=token)

    def search_papers(self, query: str, size: int = 10) -> List[PaperMeta]:
        """搜索论文，返回结构化的 PaperMeta 列表"""
        ...

    def get_paper_brief(self, arxiv_id: str) -> PaperMeta:
        """获取论文快速摘要"""
        ...

    def get_paper_structure(self, arxiv_id: str) -> Dict[str, Any]:
        """获取论文章节结构"""
        ...

    def read_section(self, arxiv_id: str, section_name: str) -> str:
        """读取特定章节内容"""
        ...

    def get_full_paper(self, arxiv_id: str) -> str:
        """获取论文完整内容"""
        ...

    def web_search(self, query: str) -> List[Dict[str, str]]:
        """Web 搜索"""
        ...
```

---

## 7. 沙箱策略

### 7.1 本地沙箱：venv（v1 实现）

使用 Python 内置的 `venv` 模块为每次复现任务创建独立的虚拟环境，确保依赖隔离。

**核心函数**：

| 函数 | 签名 | 职责 |
|------|------|------|
| `create_venv` | `(venv_path: str) -> str` | 在指定路径创建新的虚拟环境，返回 Python 解释器路径 |
| `install_deps` | `(venv_path: str, requirements: List[str]) -> bool` | 在虚拟环境中安装依赖包 |
| `run_in_venv` | `(venv_path: str, command: str, timeout: int) -> ExecutionResult` | 在虚拟环境中执行命令，捕获 stdout/stderr，支持超时控制 |

**使用流程**：

```
1. coding 节点生成 requirements.txt
2. execution 节点调用 create_venv() 创建虚拟环境
3. execution 节点调用 install_deps() 安装依赖
4. execution 节点调用 run_in_venv() 执行复现脚本
5. 收集执行结果并清理环境（可选保留）
```

### 7.2 远程沙箱：Docker（v2 实现）

v2 版本将支持 Docker 容器作为远程执行沙箱，提供更强的隔离性和环境一致性。

**预留接口**：

| 函数 | 签名 | 职责 |
|------|------|------|
| `create_container` | `(image: str, gpu: bool) -> str` | 创建 Docker 容器，返回容器 ID |
| `run_in_container` | `(container_id: str, command: str, timeout: int) -> ExecutionResult` | 在容器中执行命令 |
| `cleanup_container` | `(container_id: str) -> None` | 清理并删除容器 |

### 7.3 code_only 模式

当用户选择"只编码不执行"模式时，不需要创建任何沙箱环境。`coding` 节点直接在工作目录中生成代码文件，`execution` 节点被跳过，`reporting` 节点生成代码交付报告。

**coding 节点在 code_only 模式下的行为**：`coding` 节点应严格按照用户在 interrupt 审核中确认的交付标准清单（`ReproductionPlan.deliverables`）进行编码，至少满足最低基准线要求（详见 §3.4 code_only 模式交付标准）。生成代码后，`coding` 节点需对所有 `.py` 文件执行 `py_compile` 基础语法检查，确保交付代码无语法错误。

---

## 8. 中断恢复方案

### 8.1 Checkpoint 机制

采用 LangGraph 内置的 `SqliteSaver` 作为 checkpointer，实现状态持久化与中断恢复。

```python
from langgraph.checkpoint.sqlite import SqliteSaver

checkpointer = SqliteSaver.from_conn_string("checkpoints.db")
graph = build_graph()
compiled = graph.compile(checkpointer=checkpointer)
```

### 8.2 持久化策略

- **自动持久化**：每个节点完成后，LangGraph 自动将当前 GlobalState 写入 SQLite
- **人在回路 checkpoint**：`planning` 节点的 `interrupt()` 是天然的 checkpoint 边界，此时状态已持久化，等待用户操作
- **唯一标识**：每次复现任务通过 `thread_id` 唯一标识，支持多任务并存

### 8.3 恢复流程

1. 用户关闭浏览器或系统意外中断
2. 用户重新打开 Streamlit 界面
3. 系统从 SQLite 中读取最近的 checkpoint 列表
4. 用户选择恢复某个未完成的任务
5. 系统从该 checkpoint 恢复 GlobalState，继续执行后续节点

```python
# 恢复执行示例
config = {"configurable": {"thread_id": task_id}}
state = compiled.get_state(config)
result = compiled.invoke(None, config)
```

### 8.4 Checkpoint 数据管理

- **存储位置**：项目工作目录下的 `checkpoints.db` 文件
- **清理策略**：已完成任务的 checkpoint 可由用户手动清理，或设置自动过期（如 7 天）
- **数据安全**：SQLite 文件仅存储在本地，不包含 API 密钥等敏感信息（LLMConfig 中的 api_key 不持久化）

---

## 9. Streamlit 异步方案

### 9.1 核心挑战

Streamlit 的执行模型是同步的（每次用户交互触发脚本重新运行），而 LangGraph 工作流可能运行数分钟到数小时。需要将两者解耦。

### 9.2 方案设计

```
+-------------------+          +-------------------+
|   Streamlit 主线程 |  轮询    |  LangGraph 工作线程 |
|                   | <------> |                   |
|  - 渲染 UI        |          |  - 执行主图        |
|  - 读 checkpoint  |          |  - 更新 checkpoint |
|  - 用户交互       |          |  - 写入状态        |
+-------------------+          +-------------------+
        |                              |
        v                              v
   [SQLite checkpoint DB -- 共享状态]
```

**具体实现**：

1. **独立线程执行**：Agent 工作流在 `threading.Thread` 中运行，不阻塞 Streamlit 主线程
2. **状态轮询**：Streamlit 通过定时刷新，从 SQLite checkpoint 读取最新状态，更新 UI 展示
3. **用户操作注入**：用户在 Streamlit 界面的操作（如确认计划、终止执行）通过 `graph.update_state(config, updates)` 注入到 LangGraph 状态中
4. **人在回路同步**：`planning` 节点的 `interrupt()` 自然暂停工作线程，Streamlit 检测到中断状态后展示审核页面，用户确认后通过 `Command(resume=...)` 恢复

### 9.3 线程安全

- SQLite 的 WAL 模式支持并发读写，Streamlit 读 + 工作线程写可安全并行
- `graph.update_state()` 是线程安全的 LangGraph API
- 使用 `threading.Event` 实现工作线程的优雅终止

---

## 10. LLM 配置策略

### 10.1 配置方式

用户在 Streamlit 界面或配置文件中自行配置 LLM 服务信息：

```python
llm_config = LLMConfig(
    base_url="https://api.openai.com/v1",
    model="gpt-4o",
    api_key="sk-...",
    temperature=0.3,
    max_tokens=4096,
)
```

### 10.2 兼容性

支持任何 OpenAI 兼容 API 的 LLM 服务商：

| 服务商 | base_url 示例 | model 示例 |
|--------|-------------|-----------|
| OpenAI | `https://api.openai.com/v1` | `gpt-4o`, `gpt-4o-mini` |
| Anthropic (兼容层) | `https://api.anthropic.com/v1` | `claude-sonnet-4-20250514` |
| DeepSeek | `https://api.deepseek.com/v1` | `deepseek-chat`, `deepseek-coder` |
| Ollama (本地) | `http://localhost:11434/v1` | `llama3`, `codellama` |
| vLLM (自部署) | `http://your-server:8000/v1` | 自定义模型名 |
| 其他兼容服务 | 用户自行填写 | 用户自行填写 |

### 10.3 多模型配置（可选）

支持为不同步骤配置不同的 LLM，以平衡效果和成本。默认情况下，所有步骤使用同一个 LLM 配置。多模型配置为高级可选功能。

### 10.4 LLM 客户端封装

`core/llm_client.py` 提供统一的 LLM 调用接口：

```python
from langchain_openai import ChatOpenAI

def create_llm(config: LLMConfig) -> ChatOpenAI:
    """根据配置创建 LangChain ChatOpenAI 实例"""
    return ChatOpenAI(
        base_url=config["base_url"],
        model=config["model"],
        api_key=config["api_key"],
        temperature=config["temperature"],
        max_tokens=config["max_tokens"],
    )
```

---

## 11. 风险与缓解

| 编号 | 风险 | 影响程度 | 可能性 | 缓解策略 |
|------|------|---------|--------|---------|
| R1 | LLM 生成的代码无法正确运行 | 高 | 高 | 引入代码验证步骤（语法检查、import 检查）；execution 节点支持错误反馈和自动修复循环（最多 3 次重试） |
| R2 | deepxiv API 配额耗尽或服务不可用 | 中 | 中 | 实现请求缓存（同一论文不重复请求）；配额接近上限时提前告警；支持用户注册高级 token |
| R3 | 复现实验耗时过长导致用户体验差 | 中 | 高 | 在 planning 阶段给出准确的时间预估；支持任务中断和恢复；实时展示执行进度 |
| R4 | venv 沙箱隔离不足导致系统环境被污染 | 中 | 低 | venv 严格限定在工作目录内创建；不使用 `--system-site-packages`；v2 升级为 Docker 隔离 |
| R5 | 论文依赖的数据集无法自动下载 | 高 | 中 | 在 planning 阶段提前检测数据可用性；提供手动下载指引；支持用户自行提供数据路径 |
| R6 | Streamlit 与 LangGraph 异步协调出现竞态条件 | 中 | 低 | 使用 SQLite WAL 模式保证并发安全；工作线程使用 threading.Event 做优雅终止；关键操作加锁 |
| R7 | 用户配置的 LLM 服务不稳定或响应过慢 | 中 | 中 | 设置合理的请求超时（60 秒）；支持用户切换模型；在 UI 中展示 LLM 响应状态 |
| R8 | 论文分析遗漏关键复现信息 | 高 | 中 | 采用渐进式阅读策略（brief -> head -> section -> raw）确保信息完整；planning 阶段由用户审核补充 |
| R9 | GPU 环境差异导致复现结果不一致 | 中 | 高 | 在报告中明确记录硬件环境差异；分析差异对结果的可能影响；不将硬件差异导致的数值偏差标记为"失败" |

---

## 12. 错误处理策略

> 决策日期：2026-05-06｜决策来源：架构师方案 + 产品经理评审

### 12.1 总体架构：三层防御式错误处理

系统采用三层分离的错误处理架构，核心理念为"能重试则重试，能降级则降级，必须停则停在安全点"。

```
+--------------------------------------------------------------+
|   第三层：图层（core/graph.py）-- 节点级恢复                     |
|   checkpoint 断点续跑 | execution↔coding 修复循环 | 错误路由    |
+--------------------------------------------------------------+
                          |
+--------------------------------------------------------------+
|   第二层：节点层（core/nodes/）-- 业务级容错与降级               |
|   LLM 输出修复 | 资源降级链 | 代码语法修复 | 信息缺失标记       |
+--------------------------------------------------------------+
                          |
+--------------------------------------------------------------+
|   第一层：工具层（core/tools/）-- 网络级瞬态错误重试             |
|   deepxiv API 重试 | git clone 重试 | LLM API 重试             |
+--------------------------------------------------------------+
```

### 12.2 异常层次定义

新增 `core/errors.py`，定义系统统一的异常层次：

```python
class AutoReproError(Exception):
    """系统根异常"""

class TransientError(AutoReproError):
    """瞬态错误，可重试"""

class PermanentError(AutoReproError):
    """永久错误，不可重试"""

# --- LLM 相关 ---
class LLMError(AutoReproError):
    """LLM 相关错误基类"""

class LLMAuthError(LLMError, PermanentError):
    """LLM API 认证失败"""

class LLMRateLimitError(LLMError, TransientError):
    """LLM API 限流"""

class LLMContextOverflowError(LLMError, PermanentError):
    """LLM 上下文窗口溢出"""

class LLMOutputError(LLMError, TransientError):
    """LLM 输出格式不合规"""

# --- 沙箱相关 ---
class SandboxError(AutoReproError):
    """沙箱相关错误"""

class SandboxCreationError(SandboxError, PermanentError):
    """沙箱创建失败"""

class CodeExecutionError(SandboxError):
    """代码执行失败"""

class OOMError(CodeExecutionError, PermanentError):
    """内存/显存溢出"""

class ExecutionTimeoutError(CodeExecutionError, PermanentError):
    """执行超时"""

class DegradedResultError(AutoReproError):
    """降级运行完成（非致命，需记录）"""
```

### 12.3 GlobalState 错误追踪扩展

在 `core/state.py` 的 GlobalState 中新增以下字段：

```python
class NodeError(TypedDict):
    """单个节点的错误记录"""
    node_name: str              # 发生错误的节点
    error_type: str             # "transient" | "permanent" | "degraded"
    error_message: str          # 人类可读的错误描述
    error_detail: Optional[str] # 技术细节（堆栈、响应体等）
    timestamp: str              # ISO 8601 时间戳
    retry_count: int            # 已重试次数
    resolved: bool              # 是否已通过重试/降级解决

class GlobalState(TypedDict):
    # ... 现有字段保持不变 ...

    # --- 错误追踪（新增）---
    node_errors: List[NodeError]        # 所有节点的错误历史
    degraded_nodes: List[str]           # 以降级模式完成的节点列表
    retry_budget_remaining: int         # 剩余 LLM 调用预算
```

> **注**：以上字段已合并到第 4 章 GlobalState 正式定义中，请以第 4 章为准。修改时须同步更新两处，避免定义不同步。

### 12.4 第一层：工具层重试策略

工具层处理网络级瞬态错误，各工具的重试配置：

| 工具模块 | 重试次数 | 起始退避 | 退避策略 | 说明 |
|---------|---------|---------|---------|------|
| `deepxiv_tools` | 3 | 1s | 指数退避（1s/2s/4s） | SDK 已内置，直接透传 |
| `git_tools` | 3 | 1s | 指数退避（1s/2s/4s） | 针对 git clone 网络操作重试，依赖 git 命令行工具 |
| `shell_tools` | 0 | - | 不重试 | 命令执行结果确定性高 |
| `llm_client` | 3 | 2s | 指数退避（2s/4s/8s） | LLM 调用耗时更长，起始退避加倍 |

LLM 限流时应优先解析 `Retry-After` 响应头，据此调整等待时间。

### 12.5 第二层：节点层降级策略

核心原则："宁可带瑕前进，不可无故停下"。

#### paper_analysis 降级链

```
section("Method") 失败
  → 尝试别名匹配 section("Methodology") / section("Approach")
  → 尝试 raw() 获取全文后让 LLM 提取
  → 标记字段为缺失，在 planning 审核阶段由用户补充
```

#### resource_scout 搜索优先级链

```
1. deepxiv brief() 返回的 github_url（如果有，作为官方仓库优先使用）
  → 2. Papers With Code API 搜索论文关联仓库
  → 3. deepxiv websearch / web search 搜索 "{paper_title} github code"
  → 以上均无结果时: resource_strategy = "from_scratch"，自动继续流程
```

> 每个搜索源找到的候选仓库 URL 通过 `git clone --depth 1` 拉取后进行本地质量评估（目录结构、README、requirements、提交活跃度等）。
> **后续版本增强**：引入 GitHub Search API 实现更丰富的仓库搜索与元数据查询（stars、forks、issues 等）。

#### LLM 输出修复

所有需要结构化输出的 LLM 调用最多重试 3 次，每次将解析错误信息附加到 prompt：

```python
def call_with_structured_output(llm, prompt, output_schema, max_retries=3):
    for attempt in range(max_retries):
        response = llm.invoke(prompt)
        parsed = try_parse_json(response.content)
        if parsed and validate_schema(parsed, output_schema):
            return parsed
        prompt = f"{prompt}\n\n[上次输出格式错误: {get_parse_error(response.content)}]\n请严格按 JSON 格式输出。"
    raise LLMOutputError(f"经过 {max_retries} 次尝试仍无法获得合规输出")
```

#### LLM 上下文窗口溢出应对

```
预防层: 调用 LLM 前估算 token 数，超限时主动截断/分段
应对层: paper_analysis → 切换为 brief + 关键章节摘要
        coding → 拆分为模块逐个处理
        planning → 精简输入只保留关键字段
兜底层: 分段后仍溢出，记录错误并生成"简化版"输出
```

#### 节点函数统一错误处理模板

```python
def node_fn(state: GlobalState) -> dict:
    node_errors = list(state.get("node_errors", []))
    degraded = list(state.get("degraded_nodes", []))

    try:
        result = do_main_work(state)
        return {"field": result, "node_errors": node_errors}
    except TransientError as e:
        node_errors.append(make_node_error("node_name", "transient", str(e)))
        try:
            result = do_fallback_work(state)
            degraded.append("node_name")
            return {"field": result, "node_errors": node_errors, "degraded_nodes": degraded}
        except Exception:
            return {"error": str(e), "node_errors": node_errors}
    except PermanentError as e:
        node_errors.append(make_node_error("node_name", "permanent", str(e)))
        return {"error": str(e), "node_errors": node_errors}
    except Exception as e:
        node_errors.append(make_node_error("node_name", "permanent", f"Unexpected: {e}"))
        return {"error": str(e), "node_errors": node_errors}
```

### 12.6 第三层：图层恢复策略

#### execution ↔ coding 修复循环

在 `core/graph.py` 中通过条件路由实现：

```
coding → execution → check_execution_result
  if success: → reporting
  if failed AND fix_loop_count < 3:
    将 ExecutionResult.errors 写入 state
    → coding（携带错误上下文重新修复代码）
    → execution（重试）
  if failed AND fix_loop_count >= 3:
    → 暂停，等待用户从三个选项中选择（见 §12.8）
  if failed AND retry_budget_remaining <= 0:
    → 暂停，提示"LLM 调用预算已耗尽"，等待用户决策（见 §12.8）
```

> **边界处理**：路由判断应同时检查 `fix_loop_count < 3` **和** `retry_budget_remaining > 0`，任一条件不满足都应暂停等待用户决策。预算耗尽时的提示信息应区别于"3 轮修复均失败"——前者提示"LLM 调用预算已耗尽，无法继续自动修复"，后者提示"已完成 3 轮自动修复均未成功"。

每次循环时 coding 节点收到的 prompt 包含：上次代码、具体错误信息（stderr 关键行）、已尝试的修复方向（避免重复）、剩余重试次数。

#### Checkpoint 恢复

LangGraph SqliteSaver 在每个节点完成后自动保存 checkpoint，错误恢复场景：

| 场景 | 恢复策略 |
|------|---------|
| 节点中途网络断开 | 从上一个 checkpoint 恢复，整个节点重跑 |
| LLM API 配额用尽 | 暂停到 checkpoint，用户切换 LLM 配置后从当前节点重跑 |
| execution 代码运行失败 | 保留 errors，路由回 coding 修复 |
| 用户主动取消 | 从当前 checkpoint 恢复，用户可选择重试或跳过 |

恢复时的边界检查：
- 恢复时检查 LLM API 凭证是否仍有效
- execution 恢复时检查沙箱环境完整性（venv 是否被删除）
- 长时间后恢复时 deepxiv API token 可能过期，工具层需重新验证

### 12.7 重试预算总控

为避免无限重试消耗过多 token 和 API 配额，设置全局预算：

| 维度 | 上限 | 说明 |
|------|------|------|
| 单节点 LLM 调用次数 | 10 次（含重试） | 超出后节点以当前最佳结果写入状态 |
| execution↔coding 循环 | 3 轮 | 超出后暂停等待用户决策（对应 `GlobalState.fix_loop_count`） |
| 单任务总 LLM 调用 | 50 次（可配置） | 全局保护，防止成本失控 |

预算对用户不透明、不可调。预算接近耗尽时系统给出 WARNING 级通知；最终报告中记录总修复尝试次数。

### 12.8 execution 3 轮修复失败后的用户决策

**产品决策：不自动降级为 code_only，暂停流程由用户从三个选项中选择。**

| 选项 | 说明 | 适用场景 |
|------|------|---------|
| A. 导出代码包 + 错误诊断报告 | 将已生成代码、环境配置、详细错误诊断打包交付 | 用户有能力自行排查 |
| B. 回到计划审核，调整方案后重试 | 回退到 planning 的 INTERRUPT 点，展示失败原因，让用户修改计划后重新执行 | 错误可能源于计划层面（硬件不足、数据集不可用等） |
| C. 终止任务，导出当前所有成果 | 将论文分析、资源列表、计划、代码、日志全部打包交付 | 用户决定暂时放弃自动复现 |

选项 B 利用 LangGraph checkpoint 从 planning 节点恢复。

### 12.9 各节点错误分类全景

| 节点 | 典型瞬态错误 | 典型永久错误 | 降级策略 |
|------|------------|------------|---------|
| paper_intake | API 超时/连接失败 | NotFoundError、AuthenticationError | 无降级，致命错误需用户介入 |
| paper_analysis | LLM 超时/限流、章节读取失败 | LLM 上下文溢出 | 章节降级链 → 全文提取 → 标记缺失 |
| resource_scout | git clone 失败、websearch 失败、Papers With Code API 超时 | 零资源 | deepxiv github_url → Papers With Code → web search → from_scratch |
| planning | LLM 超时/限流 | LLM 不可用 | 用户多次拒绝时设上限（5 次） |
| coding | LLM 输出格式错误、git clone 失败 | 磁盘满/权限问题 | 语法检查 + LLM 修复循环 |
| execution | pip install 部分失败 | venv 创建失败、OOM、超时 | execution↔coding 修复循环（3 轮） |
| reporting | LLM 超时/限流 | 文件写入失败 | 降级为结构化模板报告 |

### 12.10 沙箱错误处理

#### venv 创建阶段

```
create_venv() 失败
  → 检查 Python 版本是否满足要求
  → 检查磁盘空间
  → 尝试 --clear 参数重建
  → 仍失败: 致命错误，通知用户检查系统 Python 环境
```

#### 依赖安装阶段

```
install_deps() 部分失败
  → 逐包安装（非批量安装）
  → 单包失败时: 尝试降级版本 → 无版本约束安装 → 标记失败
  → 安装完成后检查所有 import 是否可用
  → 将失败包列表反馈给 coding 节点寻找替代方案
```

#### 代码执行阶段

| 错误类型 | 检测方式 | 处理策略 |
|---------|---------|---------|
| SyntaxError | 运行前 `py_compile` | 反馈 coding 修复，不进入 execution |
| ImportError | stderr | 尝试 pip install → 反馈 coding |
| RuntimeError / ValueError | stderr | 错误 + 上下文 5 行代码反馈 coding |
| CUDA OOM | 检测 "CUDA out of memory" | 自动 batch_size 减半重试（最多 3 次） |
| MemoryError / kill signal | 检测退出信号 | 通知用户，建议减小数据规模 |
| 超时 | subprocess timeout | 默认 30 分钟，超时后 SIGTERM → 5s → SIGKILL |
| 磁盘写满 | OSError / IOError | 终止执行，通知用户清理空间 |

### 12.11 LLM 幻觉处理

不做自动检测，依赖两道防线：

1. **human-in-the-loop**：planning 审核阶段由用户验证计划的合理性
2. **执行验证**：execution 节点的实际运行结果暴露幻觉（如编造的 Python 包在 pip install 时发现）

辅助措施：coding 节点 prompt 要求 LLM 标注每段代码对应论文的哪个公式/章节；resource_scout 对每个 URL 做可达性检查。

---

## 13. 实现优先级

### 阶段 1：基础骨架（第 1-2 周）

| 任务 | 产出文件 | 说明 |
|------|---------|------|
| 定义全局状态 | `core/state.py` | 所有 TypedDict 定义，含 NodeError、degraded_nodes、retry_budget_remaining |
| 定义异常层次 | `core/errors.py` | AutoReproError 统一异常体系（见 §12.2） |
| 构建 LangGraph 主图骨架 | `core/graph.py` | 7 个节点注册、顺序边、interrupt 占位 |
| SqliteSaver 初始化 | `core/checkpointer.py` | checkpoint 管理基础设施 |
| LLM 客户端封装 | `core/llm_client.py` | OpenAI 兼容 API 调用封装，含指数退避重试、structured output、token 估算 |
| deepxiv_tools 封装 | `core/tools/deepxiv_tools.py` | Reader 薄封装 |
| paper_intake 节点 | `core/nodes/paper_intake.py` | 论文输入与解析 |
| paper_analysis 节点 | `core/nodes/paper_analysis.py` | 深度论文分析 |
| 配置管理 | `config.py` | 路径、默认值等配置 |
| 依赖声明 | `requirements.txt` | 所有 Python 依赖 |

**验收标准**：能通过代码输入 arXiv ID，经过 paper_intake 和 paper_analysis 两个节点，输出结构化的论文分析结果，状态可持久化到 SQLite。

### 阶段 2：核心链路（第 3-4 周）

| 任务 | 产出文件 | 说明 |
|------|---------|------|
| resource_scout 节点 | `core/nodes/resource_scout.py` | 仓库搜索（deepxiv github_url + Papers With Code API + web search）与本地仓库质量评估 |
| git_tools 封装 | `core/tools/git_tools.py` | 仓库克隆（git clone）与本地仓库分析工具（提交活跃度、目录结构等） |
| planning 节点 | `core/nodes/planning.py` | 复现计划生成 + interrupt 人在回路 |
| Streamlit 页面 1 | `ui/pages/paper_input.py` | 论文输入页面 |
| Streamlit 页面 2 | `ui/pages/analysis_progress.py` | 分析进度页面 |
| Streamlit 页面 3 | `ui/pages/plan_review.py` | 计划审核页面 |
| LLM 配置表单 | `ui/components/llm_config_form.py` | 用户配置 LLM |
| 异步通信框架 | `app.py` | Streamlit + 工作线程 + 轮询机制 |

**验收标准**：完整的从论文输入到计划审核的链路可在 Streamlit 界面中运行，人在回路中断/恢复机制正常工作。

### 阶段 3：执行闭环（第 5-7 周）

| 任务 | 产出文件 | 说明 |
|------|---------|------|
| 本地 venv 沙箱 | `sandbox/local_venv.py` | create_venv, install_deps, run_in_venv |
| shell_tools 封装 | `core/tools/shell_tools.py` | Shell 命令执行工具 |
| file_tools 封装 | `core/tools/file_tools.py` | 文件读写工具 |
| coding 节点 | `core/nodes/coding.py` | 代码生成与环境配置 |
| execution 节点 | `core/nodes/execution.py` | 沙箱中执行实验 |
| reporting 节点 | `core/nodes/reporting.py` | 结果对比与报告生成 |
| Streamlit 页面 4 | `ui/pages/execution_monitor.py` | 执行监控页面 |
| Streamlit 页面 5 | `ui/pages/report_view.py` | 结果报告页面 |
| 条件路由实现 | `core/graph.py` 更新 | code_only 模式路由 + execution↔coding 修复循环路由 |
| execution 失败用户选项 | `ui/pages/execution_monitor.py` 更新 | 3 轮修复失败后的 A/B/C 三选项 UI（见 §12.8） |
| 错误信息展示组件 | `ui/components/error_display.py` | 一句话摘要 + 可展开详情 + 完整日志链接 |
| 各节点降级逻辑 | `core/nodes/*.py` | paper_analysis 章节降级链、resource_scout 搜索优先级链、沙箱错误分类 |

**验收标准**：端到端完成一次完整复现（输入 arXiv ID -> 分析 -> 资源搜集 -> 计划审核 -> 编码 -> 执行 -> 报告），所有 Streamlit 页面可正常使用，execution↔coding 修复循环正常工作。

### 阶段 4：稳定化（第 8 周）

| 任务 | 说明 |
|------|------|
| 错误处理精细化 | 重试预算追踪、混沌测试（随机注入异常）、边界场景覆盖 |
| CLI 入口 | 命令行基础操作支持 |
| 集成测试 | 端到端测试用例，覆盖主要场景 |
| 文档完善 | 用户使用文档、开发者文档 |
| 性能优化 | API 请求缓存、不必要的 LLM 调用优化 |
| 代码审查 | 代码质量检查、类型标注完善 |

**验收标准**：系统达到 MVP 发布标准，核心链路稳定可靠，文档齐全，可交付给目标用户试用。

---

*本文档为正式定稿，作为开发团队的实施参考。技术方案的任何调整需通过架构评审后更新本文档。*
*2026-05-06 更新：新增 §12 错误处理策略章节（架构师方案 + 产品经理评审决策），更新 §5 模块结构和 §13 实现优先级。*
*2026-05-06 更新：调整 resource_scout 设计——MVP 阶段移除 GitHub API 依赖，仓库获取通过 git clone 完成，仓库搜索通过 deepxiv github_url + Papers With Code API + web search 替代；github_tools 重命名为 git_tools，职责调整为仓库克隆与本地分析。*
