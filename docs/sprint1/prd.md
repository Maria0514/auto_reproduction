# Sprint 1 产品需求文档 (PRD)

**产品名称**：Auto-Reproduction -- 论文自动复现系统
**Sprint**：Sprint 1 -- 基础骨架
**版本**：v1.0
**日期**：2026-05-06
**作者**：产品经理代理
**状态**：正式版

> **[历史快照声明 2026-07-05]** 本文档为 Sprint 1 立项时点快照。文中预算数值（总预算 50 / 修复循环 3 轮）已于 2026-06-30 放大为 120/10；现状以 technical-architecture.md 与 config.py 为准。

---

## 目录

1. [Sprint 1 概述](#1-sprint-1-概述)
2. [功能需求](#2-功能需求)
3. [非功能需求](#3-非功能需求)
4. [数据结构定义](#4-数据结构定义)
5. [接口定义](#5-接口定义)
6. [验收标准](#6-验收标准)
7. [依赖与风险](#7-依赖与风险)
8. [开放问题](#8-开放问题)

---

## 1. Sprint 1 概述

### 1.1 Sprint 目标

Sprint 1 的目标是搭建整个论文自动复现系统的**基础骨架**，完成系统核心数据结构定义、基础设施层（LLM 客户端、checkpoint 持久化、异常体系、配置管理）以及七步流程中前两步（论文输入与解析、深度论文分析）的完整实现。

Sprint 1 完成后，开发团队应能够通过代码（非 UI）输入一个 arXiv ID，自动完成论文元数据获取与深度论文分析，输出结构化的 `PaperAnalysis` 结果，且全程状态可持久化到 SQLite。

### 1.2 范围边界

**Sprint 1 包含**：

| 编号 | 模块 | 产出文件 |
|------|------|---------|
| S1-01 | 全局状态定义 | `core/state.py` |
| S1-02 | 统一异常体系 | `core/errors.py` |
| S1-03 | LangGraph 主图骨架 | `core/graph.py` |
| S1-04 | Checkpoint 管理 | `core/checkpointer.py` |
| S1-05 | LLM 客户端封装 | `core/llm_client.py` |
| S1-06 | deepxiv Reader 薄封装 | `core/tools/deepxiv_tools.py` |
| S1-07 | paper_intake 节点 | `core/nodes/paper_intake.py` |
| S1-08 | paper_analysis 节点 | `core/nodes/paper_analysis.py` |
| S1-09 | 配置管理 | `config.py` |
| S1-10 | 依赖声明 | `requirements.txt` |

**Sprint 1 不包含**：

- 任何 Streamlit UI 页面或组件
- resource_scout / planning / coding / execution / reporting 节点的业务实现（graph.py 中仅做占位注册）
- git_tools / shell_tools / file_tools 工具封装
- sandbox 沙箱模块
- CLI 入口
- 条件路由、修复循环路由（graph.py 中仅注册顺序边和 interrupt 占位）

### 1.3 与整体产品的关系

本系统是一个七步流程的论文自动复现系统（详见产品设计说明书 `docs/product-design-specification.md` 第 3 章）。Sprint 1 对应技术架构文档（`docs/technical-architecture.md` 第 13 章）的"阶段 1：基础骨架"，负责奠定整个系统的数据基础和基础设施层，后续 Sprint 在此骨架之上逐步实现完整链路。

```
七步流程与 Sprint 对应关系：

步骤 1: 论文输入与解析 (paper_intake)       <-- Sprint 1 实现
步骤 2: 深度论文分析 (paper_analysis)        <-- Sprint 1 实现
步骤 3: 资源搜集与评估 (resource_scout)      --> Sprint 2
步骤 4: 复现规划与用户审核 (planning)        --> Sprint 2
步骤 5: 编码与环境搭建 (coding)             --> Sprint 3
步骤 6: 执行与测试验证 (execution)           --> Sprint 3
步骤 7: 报告生成 (reporting)                --> Sprint 3
```

### 1.4 目标用户

Sprint 1 的直接用户为**开发团队自身**。本阶段产出的是系统骨架和前两个节点的完整实现，通过 Python 代码调用验证，尚无面向终端用户的界面。终端用户画像（CS 领域在校学生和科研工作者）详见产品设计说明书第 2 章。

---

## 2. 功能需求

### 2.1 S1-01: 全局状态定义 (`core/state.py`)

**目的**：定义贯穿整个 LangGraph 工作流的全局状态结构，作为所有节点间数据流转的唯一契约。

**详细要求**：

- 定义完整的 `GlobalState` TypedDict 及其所有嵌套类型，包括：
  - `LLMConfig` -- LLM 服务配置
  - `PaperMeta` -- 论文基础元数据（步骤 1 输出）
  - `PaperAnalysis` -- 深度论文分析结果（步骤 2 输出）
  - `RepoInfo` -- 单个代码仓库评估信息
  - `ResourceInfo` -- 资源搜集结果（步骤 3 输出）
  - `ReproductionPlan` -- 复现计划（步骤 4 输出）
  - `ExecutionResult` -- 执行与验证结果（步骤 6 输出）
  - `NodeError` -- 单个节点的错误记录
  - `FixLoopRecord` -- 单轮修复循环记录
  - `ExecutionMode` 枚举 -- `FULL` / `CODE_ONLY`
  - `GlobalState` 本身 -- 包含所有步骤输出、流程控制字段、错误追踪字段、修复循环追踪字段
- 所有字段的类型、默认值和语义必须与技术架构文档第 4 章保持严格一致
- 使用 Python `typing.TypedDict` 和 `enum.Enum` 实现
- 要求 Python >= 3.10

**输入**：无（纯定义模块）

**输出**：可从 `core/state` 导入的全部 TypedDict 和 Enum 类型

**边界条件**：
- Sprint 1 阶段 `RepoInfo`、`ResourceInfo`、`ReproductionPlan`、`ExecutionResult` 等后续步骤使用的类型在本阶段定义但不在业务逻辑中使用，需确保定义完整以便后续 Sprint 直接引用
- `GlobalState` 中 `Optional` 标注的字段初始值为 `None`；`List` 标注的字段初始值为空列表；`retry_budget_remaining` 默认值为 50

**依赖关系**：无前置依赖。本模块是其他所有模块的基础依赖。

---

### 2.2 S1-02: 统一异常体系 (`core/errors.py`)

**目的**：定义系统统一的异常层次结构，供三层防御式错误处理架构使用。

**详细要求**：

- 定义以下异常层次（完整继承树）：
  ```
  AutoReproError (系统根异常)
  ├── TransientError (瞬态错误，可重试)
  ├── PermanentError (永久错误，不可重试)
  ├── LLMError (LLM 相关错误基类)
  │   ├── LLMAuthError (继承 LLMError + PermanentError)
  │   ├── LLMRateLimitError (继承 LLMError + TransientError)
  │   ├── LLMContextOverflowError (继承 LLMError + PermanentError)
  │   └── LLMOutputError (继承 LLMError + TransientError)
  ├── SandboxError (沙箱相关错误)
  │   ├── SandboxCreationError (继承 SandboxError + PermanentError)
  │   └── CodeExecutionError (继承 SandboxError)
  │       ├── OOMError (继承 CodeExecutionError + PermanentError)
  │       └── ExecutionTimeoutError (继承 CodeExecutionError + PermanentError)
  └── DegradedResultError (降级运行完成，非致命)
  ```
- 每个异常类需包含清晰的 docstring，说明其语义和触发场景
- 使用 Python 标准多重继承实现 `TransientError`/`PermanentError` 的混入
- 异常层次必须与技术架构文档第 12.2 节保持一致

**输入**：无

**输出**：可从 `core/errors` 导入的全部异常类

**边界条件**：
- `SandboxError` 系列异常在 Sprint 1 中仅做定义，实际使用在 Sprint 3（execution 节点）
- `DegradedResultError` 为非致命异常，用于标记降级完成的节点，不应中断流程

**依赖关系**：无前置依赖。被 `core/llm_client.py`、`core/tools/deepxiv_tools.py`、`core/nodes/*.py` 依赖。

---

### 2.3 S1-03: LangGraph 主图骨架 (`core/graph.py`)

**目的**：构建 LangGraph 主图，注册全部 7 个节点并建立顺序边连接，为后续 Sprint 逐步填充业务逻辑奠定编排基础。

**详细要求**：

- 提供 `build_graph()` 函数，返回一个 `CompiledGraph` 实例
- 注册全部 7 个节点：`paper_intake`、`paper_analysis`、`resource_scout`、`planning`、`coding`、`execution`、`reporting`
- 建立顺序边：`START -> paper_intake -> paper_analysis -> resource_scout -> planning -> coding -> execution -> reporting -> END`
- 在 `planning` 节点后预留 `interrupt()` 占位（人在回路审核点）
- 后续 Sprint 需要实现的节点（resource_scout ~ reporting）在本阶段使用 pass-through 占位函数（接收 state，原样返回，不修改状态）
- 编译图时集成 `SqliteSaver` checkpointer（通过参数传入或从 `core/checkpointer.py` 获取）
- 支持通过 `thread_id` 区分不同复现任务

**输入**：可选的 checkpointer 实例

**输出**：`CompiledGraph` 实例，可通过 `graph.invoke(initial_state, config)` 启动执行

**边界条件**：
- Sprint 1 中图的实际可执行路径仅覆盖 `paper_intake -> paper_analysis`，后续节点为占位
- 条件路由（code_only 分支、execution-coding 修复循环）在 Sprint 1 中不实现，仅保留注释说明
- `interrupt()` 在 Sprint 1 验收中不涉及恢复测试，但需确保占位不阻塞顺序执行（可通过配置跳过 interrupt 或在验收脚本中模拟 resume）

**依赖关系**：依赖 `core/state.py`（GlobalState 类型）、`core/checkpointer.py`（SqliteSaver）、`core/nodes/*.py`（节点函数）。

---

### 2.4 S1-04: Checkpoint 管理 (`core/checkpointer.py`)

**目的**：封装 LangGraph SqliteSaver 的初始化逻辑，提供 checkpoint 持久化基础设施。

**详细要求**：

- 提供 `get_checkpointer(db_path: str) -> SqliteSaver` 函数
- 默认 `db_path` 为项目工作目录下的 `checkpoints.db`（路径从 `config.py` 获取）
- 确保 SQLite 使用 WAL 模式以支持并发读写
- 不持久化敏感信息（`LLMConfig.api_key` 的处理策略需在实现时考虑——LangGraph 会自动序列化整个 GlobalState，需评估是否需要在 state 中将 api_key 标记为非持久化字段，或在恢复时由用户重新提供）

**输入**：可选的数据库文件路径

**输出**：`SqliteSaver` 实例

**边界条件**：
- 数据库文件不存在时自动创建
- 数据库文件已存在时正常打开（支持断点续跑场景）
- 文件路径不可写时应抛出明确错误

**依赖关系**：依赖 `config.py`（默认路径配置）。被 `core/graph.py` 依赖。

---

### 2.5 S1-05: LLM 客户端封装 (`core/llm_client.py`)

**目的**：封装 OpenAI 兼容 API 的 LLM 调用，提供统一接口，内建指数退避重试、结构化输出解析和 token 估算能力。

**详细要求**：

**基础创建**：
- 提供 `create_llm(config: LLMConfig) -> ChatOpenAI` 函数
- 基于 `langchain_openai.ChatOpenAI` 实现
- 支持任意 OpenAI 兼容 API（OpenAI、DeepSeek、Ollama、vLLM 等），通过 `base_url` + `model` + `api_key` 配置

**指数退避重试**：
- LLM API 调用失败时自动重试，最多 3 次
- 退避策略：指数退避，起始 2 秒（2s / 4s / 8s）
- 限流场景（HTTP 429）优先解析 `Retry-After` 响应头
- 区分可重试错误（超时、限流、服务端 5xx）和不可重试错误（认证失败 401、上下文溢出）
- 认证失败抛出 `LLMAuthError`，限流抛出 `LLMRateLimitError`，上下文溢出抛出 `LLMContextOverflowError`

**结构化输出 (Structured Output)**：
- 提供 `call_with_structured_output(llm, prompt, output_schema, max_retries=3)` 函数
- 调用 LLM 后尝试将响应解析为指定 JSON Schema
- 解析失败时将错误信息附加到 prompt 重试（最多 `max_retries` 次）
- 全部重试失败后抛出 `LLMOutputError`

**Token 估算**：
- 提供 `estimate_tokens(text: str) -> int` 函数
- 用于在调用 LLM 前预判是否会超出上下文窗口
- 可使用简单的字符数/4 近似估算，或接入 `tiktoken` 做精确估算
- 提供 `check_context_limit(text: str, max_tokens: int) -> bool` 辅助函数

**输入**：`LLMConfig` 配置字典

**输出**：`ChatOpenAI` 实例或 LLM 调用结果

**边界条件**：
- `api_key` 为空或无效时，首次调用即抛出 `LLMAuthError`，不重试
- `base_url` 不可达时，经过 3 次重试后抛出 `TransientError`
- 全局重试预算（`retry_budget_remaining`）的扣减由节点层负责，LLM 客户端层仅负责单次调用级别的重试

**依赖关系**：依赖 `core/state.py`（LLMConfig 类型）、`core/errors.py`（LLMError 系列异常）。被 `core/nodes/paper_analysis.py` 依赖。

---

### 2.6 S1-06: deepxiv Reader 薄封装 (`core/tools/deepxiv_tools.py`)

**目的**：对 `deepxiv_sdk.Reader` 做薄封装，统一错误处理、日志记录和结果适配，屏蔽 SDK 原生异常。

**详细要求**：

**封装类**：`DeepxivTools`

**构造函数**：
- `__init__(self, token: Optional[str] = None)`
- 内部创建 `Reader(token=token)` 实例

**需要封装的方法**（Sprint 1 使用的 API）：

| 方法 | 签名 | 说明 | 调用场景 |
|------|------|------|---------|
| `search_papers` | `(query: str, size: int = 10) -> List[Dict]` | 语义搜索论文 | UI 层论文搜索（Sprint 1 不直接使用，预留） |
| `get_paper_brief` | `(arxiv_id: str) -> Dict` | 获取论文快速摘要 | paper_intake 节点 |
| `get_paper_structure` | `(arxiv_id: str) -> Dict` | 获取论文章节结构 | paper_analysis 节点规划阅读路径 |
| `read_section` | `(arxiv_id: str, section_name: str) -> str` | 读取特定章节 | paper_analysis 节点逐章节分析 |
| `get_full_paper` | `(arxiv_id: str) -> str` | 获取完整论文内容 | paper_analysis 降级链兜底 |
| `web_search` | `(query: str) -> List[Dict]` | Web 搜索 | Sprint 2 resource_scout 使用，Sprint 1 预留 |

**错误处理映射**：

| SDK 原生异常 | 系统内部异常 | 处理方式 |
|-------------|------------|---------|
| `NotFoundError` | `PermanentError` | 不重试，论文不存在 |
| `AuthenticationError` | `PermanentError` | 不重试，token 无效 |
| `RateLimitError` | `TransientError` | SDK 内部已有重试机制（3 次指数退避），封装层不额外重试 |
| `BadRequestError` | `PermanentError` | 不重试，请求参数错误 |
| `ServerError` | `TransientError` | SDK 内部已有重试机制 |
| `APIError`（其他） | `TransientError` | SDK 内部已有重试机制 |

**日志记录**：
- 每次 API 调用记录请求参数和响应状态
- 错误调用记录完整异常信息
- 使用 Python `logging` 模块

**输入**：arXiv ID 或搜索关键词

**输出**：结构化的论文数据字典（字段映射见第 5 章接口定义）

**边界条件**：
- arXiv ID 格式不合法时（如空字符串），SDK 层会抛出 `ValueError`，封装层应捕获并转换为 `PermanentError`
- SDK 返回空结果时（如 `brief()` 返回 `{}`），封装层应抛出 `PermanentError` 并提示"论文数据为空"
- deepxiv_sdk 的 `Reader` 已内建超时（60s）和重试（3 次指数退避）机制，封装层利用 SDK 内建重试，不做额外重试层叠

**依赖关系**：依赖 `deepxiv-sdk>=0.2.5`、`core/errors.py`。被 `core/nodes/paper_intake.py` 和 `core/nodes/paper_analysis.py` 依赖。

---

### 2.7 S1-07: paper_intake 节点 (`core/nodes/paper_intake.py`)

**目的**：接收 UI 层传入的已确认 arXiv ID，调用 deepxiv Reader 获取论文基础元数据，填充 `PaperMeta`。

**详细要求**：

**节点函数签名**：`def paper_intake(state: GlobalState) -> dict`

**核心行为**：
1. 从 `state["user_input"]` 读取 arXiv ID（UI 层已完成论文搜索与确认，graph 接收的是已确认的 arXiv ID）
2. 调用 `DeepxivTools.get_paper_brief(arxiv_id)` 获取论文快速摘要
3. 将返回结果映射为 `PaperMeta` TypedDict
4. 返回状态更新字典：`{"paper_meta": paper_meta, "current_step": "paper_intake"}`

**字段映射**（`brief()` 返回值 -> `PaperMeta`）：

| brief() 返回字段 | PaperMeta 字段 | 说明 |
|-----------------|---------------|------|
| `arxiv_id` | `arxiv_id` | 直接映射 |
| `title` | `title` | 直接映射 |
| `authors`（来自 `head()`） | `authors` | brief 可能不含完整作者列表，需从 head 补充 |
| `abstract`（来自 `head()`） | `abstract` | brief 可能不含完整摘要，需从 head 补充 |
| `categories`（来自 `head()`） | `categories` | 论文分类 |
| `tldr` | `tldr` | AI 生成摘要 |
| `keywords` | `keywords` | 关键词列表 |
| `citations` | `citation_count` | 引用数 |
| `github_url` | `github_url` | 关联 GitHub 仓库 URL |
| `publish_at` | `publish_date` | 发布日期 |
| `src_url` | `pdf_url` | PDF 直链 |

**补充获取逻辑**：
- `brief()` 返回的信息可能不完整（如缺少 `authors`、`abstract`、`categories`），需额外调用 `head()` 获取完整元数据
- 采用 `brief()` 优先 + `head()` 补充的策略，减少不必要的 API 调用

**学科范围校验**：
- 检查 `categories` 字段，确认论文属于 CS 领域（前缀为 `cs.*`）
- 非 CS 论文时，在 `PaperMeta` 中标记但不中断流程（用户已在 UI 层确认过论文，此处仅做记录）

**错误处理**：
- 论文不存在（`NotFoundError`）：记录到 `node_errors`，设置 `error` 字段，流程终止
- API 超时/连接失败：SDK 内部重试，封装层向上传播异常，记录到 `node_errors`
- paper_intake 节点无降级策略——论文元数据是后续所有步骤的基础，缺失时流程无法继续

**输入**：`GlobalState`，其中 `user_input` 包含已确认的 arXiv ID

**输出**：状态更新字典，包含填充完整的 `paper_meta` 字段

**边界条件**：
- arXiv ID 不存在时，必须设置 `error` 字段并记录 `NodeError`，标记为 permanent 错误
- arXiv ID 格式合法但 API 返回空数据时，同样视为致命错误

**依赖关系**：依赖 `core/state.py`、`core/tools/deepxiv_tools.py`。被 `core/graph.py` 注册调用。

---

### 2.8 S1-08: paper_analysis 节点 (`core/nodes/paper_analysis.py`)

**目的**：对论文进行深度分析，逐章节渐进式阅读，提取与复现相关的关键信息，填充 `PaperAnalysis`。

**详细要求**：

**节点函数签名**：`def paper_analysis(state: GlobalState) -> dict`

**核心行为**：
1. 从 `state["paper_meta"]` 读取论文元数据
2. 调用 `DeepxivTools.get_paper_structure(arxiv_id)` 获取章节结构
3. 按优先级渐进式读取关键章节（Method、Experiments、Results 等）
4. 对每个章节内容调用 LLM 进行结构化分析
5. 综合各章节分析结果，填充 `PaperAnalysis` TypedDict
6. 返回状态更新字典

**渐进式阅读策略**：

按以下优先级顺序读取章节（遇到同名章节采用模糊匹配）：

| 优先级 | 目标章节 | 常见别名 | 提取内容 |
|--------|---------|---------|---------|
| 1 | Method / Methodology | "Method", "Methodology", "Approach", "Proposed Method", "Our Approach" | 方法描述、核心算法、关键公式 |
| 2 | Experiments | "Experiments", "Experimental Setup", "Experimental Settings" | 数据集、评估指标、超参数、硬件要求 |
| 3 | Results | "Results", "Main Results", "Experimental Results" | 基线结果、关键数值 |
| 4 | Introduction | "Introduction" | 框架选择线索、问题定义补充 |
| 5 | Abstract | （来自 PaperMeta） | 方法概述补充 |

**LLM 分析 Prompt 策略**：
- 每个章节使用针对性的 prompt，要求 LLM 以 JSON 格式输出结构化分析结果
- 使用 `call_with_structured_output()` 确保输出格式合规
- 调用 LLM 前使用 `estimate_tokens()` 检查输入是否超出上下文窗口
- 超出时采用截断或分段策略处理

**降级链（Sprint 1 必须实现）**：

当通过 `read_section()` 获取某章节内容失败时，按以下顺序降级：

```
read_section("Method") 失败
  -> 尝试别名匹配：read_section("Methodology") / read_section("Approach") / ...
  -> 别名均失败：调用 get_full_paper() 获取全文，让 LLM 从全文中提取对应信息
  -> 全文获取也失败：标记该字段为缺失（在 PaperAnalysis 对应字段中写入空值或特定标记），
     将节点名加入 degraded_nodes，记录 NodeError
```

**PaperAnalysis 字段填充规则**：

| 字段 | 来源章节 | 缺失处理 |
|------|---------|---------|
| `method_summary` | Method | 降级链 -> 标记缺失 |
| `key_formulas` | Method | 降级链 -> 空列表 |
| `datasets` | Experiments | 降级链 -> 空列表 |
| `metrics` | Experiments | 降级链 -> 空列表 |
| `hyperparams` | Experiments | 降级链 -> 空字典 |
| `hardware_requirements` | Experiments | 降级链 -> 空字符串 |
| `framework` | Method / Introduction | 降级链 -> None |
| `baseline_results` | Results | 降级链 -> 空字典 |
| `sections_read` | （自动记录） | 记录成功读取的章节列表 |
| `analysis_notes` | （综合） | 记录分析过程中的备注、降级说明、缺失信息 |

**错误处理**：
- 遵循节点函数统一错误处理模板（技术架构文档第 12.5 节）
- 瞬态错误（LLM 超时/限流、章节读取网络失败）：记录 NodeError，尝试降级
- 永久错误（LLM 上下文溢出）：切换为 brief + 关键章节摘要的精简分析模式
- 预算控制：单节点 LLM 调用不超过 10 次（含重试），超出时以当前最佳结果写入状态

**输入**：`GlobalState`，其中 `paper_meta` 已由 paper_intake 填充

**输出**：状态更新字典，包含填充完整的 `paper_analysis` 字段，以及更新后的 `node_errors`、`degraded_nodes`

**边界条件**：
- `paper_meta` 为 None 或缺失时（上游 paper_intake 失败），直接记录错误并返回
- 论文无 Method 章节（如综述类论文）：降级链走到全文提取 -> 标记缺失，但不中断流程
- LLM 调用预算接近耗尽时（`retry_budget_remaining` 低于阈值），输出 WARNING 级日志
- 所有章节均读取失败时，节点仍应返回带有"全部缺失"标记的 PaperAnalysis，加入 degraded_nodes，不抛出致命异常

**依赖关系**：依赖 `core/state.py`、`core/tools/deepxiv_tools.py`、`core/llm_client.py`、`core/errors.py`。被 `core/graph.py` 注册调用。

---

### 2.9 S1-09: 配置管理 (`config.py`)

**目的**：集中管理项目路径、默认值和环境变量，避免硬编码分散在各模块中。

**详细要求**：

- 定义项目根目录路径
- 定义 checkpoint 数据库默认路径（如 `{project_root}/checkpoints.db`）
- 定义工作空间默认目录路径（如 `{project_root}/workspace/`）
- 定义日志目录路径（如 `{workspace}/logs/`）
- 定义 LLM 默认配置（temperature: 0.3, max_tokens: 4096）
- 定义重试预算默认值（单节点 LLM 调用上限: 10, 总 LLM 调用上限: 50, 修复循环上限: 3）
- 定义 deepxiv API token 的读取方式（环境变量 `DEEPXIV_TOKEN`）
- 定义 LLM API key 的读取方式（环境变量 `LLM_API_KEY`）
- 支持通过环境变量覆盖默认值

**输入**：环境变量

**输出**：可导入的配置常量和配置函数

**边界条件**：
- 环境变量未设置时使用默认值
- 路径配置应使用 `pathlib.Path` 确保跨平台兼容

**依赖关系**：无前置依赖。被几乎所有模块依赖。

---

### 2.10 S1-10: 依赖声明 (`requirements.txt`)

**目的**：声明项目所有 Python 依赖及版本约束。

**详细要求**：

Sprint 1 所需的 Python 依赖：

| 依赖包 | 版本要求 | 用途 |
|--------|---------|------|
| `langgraph` | >= 0.2.0 | Agent 编排框架 |
| `langchain-openai` | >= 0.1.0 | OpenAI 兼容 LLM 客户端 |
| `langchain-core` | >= 0.2.0 | LangChain 核心抽象 |
| `deepxiv-sdk` | >= 0.2.5 | 论文数据获取 |
| `requests` | >= 2.31.0 | HTTP 客户端（deepxiv-sdk 依赖） |

可选/推荐依赖：

| 依赖包 | 版本要求 | 用途 |
|--------|---------|------|
| `tiktoken` | >= 0.5.0 | 精确 token 估算（可选，有则用，无则回退到近似估算） |
| `pydantic` | >= 2.0.0 | 结构化输出 Schema 验证（如 LangChain 的 structured output 依赖） |

**输入**：无

**输出**：`requirements.txt` 文件

**边界条件**：
- 版本约束使用 `>=` 下限约束，不锁定具体版本，以保持灵活性
- Python 版本要求（>= 3.10）在文件顶部注释说明

**依赖关系**：无。

---

## 3. 非功能需求

### 3.1 性能

| 指标 | 要求 | 说明 |
|------|------|------|
| paper_intake 节点执行时间 | <= 30 秒 | 含 deepxiv API 调用（brief + head），不含网络异常重试时间 |
| paper_analysis 节点执行时间 | <= 5 分钟 | 含多次 deepxiv API 调用和多次 LLM 调用 |
| Checkpoint 写入延迟 | <= 1 秒 | 每个节点完成后的状态持久化 |
| LLM 单次调用超时 | 60 秒 | 超时后触发重试 |

### 3.2 可靠性

| 指标 | 要求 | 说明 |
|------|------|------|
| API 瞬态错误自愈率 | >= 90% | 网络超时、限流等瞬态错误应通过重试机制自动恢复 |
| 降级完成率 | 100% | paper_analysis 的降级链必须保证节点不因章节读取失败而完全中断 |
| 状态持久化完整性 | 100% | 每个节点完成后状态必须成功写入 SQLite |
| 进程崩溃恢复 | 支持 | 进程意外退出后，可从最近 checkpoint 恢复继续执行 |

### 3.3 可维护性

| 指标 | 要求 | 说明 |
|------|------|------|
| 类型标注覆盖率 | 100% | 所有公开函数的参数和返回值必须有类型标注 |
| Docstring 覆盖率 | 100%（公开接口） | 所有公开类、函数、方法必须有 docstring |
| 模块间耦合 | 低耦合 | 节点层仅通过 GlobalState 传递数据，不直接调用其他节点；工具层不依赖节点层 |
| 日志规范 | 统一使用 `logging` | 所有模块使用 Python 标准 logging，日志级别合理划分 |

### 3.4 可测试性

| 指标 | 要求 | 说明 |
|------|------|------|
| 单元测试支持 | 所有模块可独立测试 | 工具层可 mock 外部 API，节点层可 mock 工具层，图层可 mock 节点 |
| 端到端测试支持 | 支持脚本化验收 | 可通过 Python 脚本完成 Sprint 1 验收标准中描述的端到端流程 |

---

## 4. 数据结构定义

Sprint 1 需要在 `core/state.py` 中定义完整的数据结构体系。以下为概览，完整字段定义请参见技术架构文档（`docs/technical-architecture.md`）第 4 章。

### 4.1 TypedDict 总览

| 类型名 | 用途 | Sprint 1 使用情况 |
|--------|------|------------------|
| `LLMConfig` | LLM 服务配置 | Sprint 1 使用（paper_analysis 调用 LLM） |
| `PaperMeta` | 论文基础元数据 | Sprint 1 使用（paper_intake 输出） |
| `PaperAnalysis` | 深度论文分析结果 | Sprint 1 使用（paper_analysis 输出） |
| `RepoInfo` | 单个代码仓库评估信息 | Sprint 1 定义，Sprint 2 使用 |
| `ResourceInfo` | 资源搜集结果 | Sprint 1 定义，Sprint 2 使用 |
| `ReproductionPlan` | 复现计划 | Sprint 1 定义，Sprint 2 使用 |
| `ExecutionResult` | 执行与验证结果 | Sprint 1 定义，Sprint 3 使用 |
| `NodeError` | 单个节点的错误记录 | Sprint 1 使用（错误追踪） |
| `FixLoopRecord` | 单轮修复循环记录 | Sprint 1 定义，Sprint 3 使用 |

### 4.2 Enum 定义

| 类型名 | 值 | 说明 |
|--------|-----|------|
| `ExecutionMode` | `FULL = "full"`, `CODE_ONLY = "code_only"` | 执行模式枚举 |

### 4.3 GlobalState 字段分组

| 分组 | 包含字段 | Sprint 1 使用情况 |
|------|---------|------------------|
| LLM 配置 | `llm_config` | 使用 |
| 用户输入 | `user_input`, `input_type` | 使用 |
| 各步骤输出 | `paper_meta`, `paper_analysis`, `resource_info`, `reproduction_plan`, `code_output_dir`, `execution_result`, `report_path` | paper_meta 和 paper_analysis 使用，其余定义不使用 |
| 流程控制 | `current_step`, `execution_mode`, `sandbox_type`, `error`, `messages` | 使用 |
| 错误追踪 | `node_errors`, `degraded_nodes`, `retry_budget_remaining` | 使用 |
| 修复循环追踪 | `fix_loop_count`, `fix_loop_history`, `user_fix_decision` | 定义不使用 |
| 工作目录 | `workspace_dir` | 使用 |

---

## 5. 接口定义

### 5.1 core/graph.py

```python
def build_graph(checkpointer: Optional[SqliteSaver] = None) -> CompiledGraph:
    """
    构建并编译 LangGraph 主图。

    Args:
        checkpointer: 可选的 SqliteSaver 实例，用于状态持久化。
                      若不传入，将从 core/checkpointer.py 获取默认实例。

    Returns:
        编译后的 CompiledGraph 实例，可通过 invoke() 执行。
    """
```

### 5.2 core/checkpointer.py

```python
def get_checkpointer(db_path: Optional[str] = None) -> SqliteSaver:
    """
    获取 SqliteSaver checkpointer 实例。

    Args:
        db_path: SQLite 数据库文件路径。默认从 config.py 获取。

    Returns:
        SqliteSaver 实例。
    """
```

### 5.3 core/llm_client.py

```python
def create_llm(config: LLMConfig) -> ChatOpenAI:
    """根据配置创建 LangChain ChatOpenAI 实例。"""

def call_with_structured_output(
    llm: ChatOpenAI,
    prompt: str,
    output_schema: Dict[str, Any],
    max_retries: int = 3
) -> Dict[str, Any]:
    """调用 LLM 并解析为结构化 JSON 输出，失败时自动重试。"""

def estimate_tokens(text: str) -> int:
    """估算文本的 token 数量。"""

def check_context_limit(text: str, max_tokens: int) -> bool:
    """检查文本是否超出上下文窗口限制。返回 True 表示未超限。"""
```

### 5.4 core/tools/deepxiv_tools.py

```python
class DeepxivTools:
    def __init__(self, token: Optional[str] = None): ...

    def search_papers(self, query: str, size: int = 10) -> List[Dict]: ...
    def get_paper_brief(self, arxiv_id: str) -> Dict: ...
    def get_paper_structure(self, arxiv_id: str) -> Dict: ...
    def read_section(self, arxiv_id: str, section_name: str) -> str: ...
    def get_full_paper(self, arxiv_id: str) -> str: ...
    def web_search(self, query: str) -> List[Dict]: ...
```

### 5.5 core/nodes/paper_intake.py

```python
def paper_intake(state: GlobalState) -> dict:
    """
    论文输入与解析节点。

    读取 state["user_input"] 中的 arXiv ID，获取论文元数据。

    Returns:
        状态更新字典，至少包含 "paper_meta" 和 "current_step" 字段。
        失败时包含 "error" 和更新后的 "node_errors" 字段。
    """
```

### 5.6 core/nodes/paper_analysis.py

```python
def paper_analysis(state: GlobalState) -> dict:
    """
    深度论文分析节点。

    基于 state["paper_meta"] 渐进式阅读论文，提取复现所需信息。

    Returns:
        状态更新字典，至少包含 "paper_analysis" 和 "current_step" 字段。
        降级时包含更新后的 "degraded_nodes" 和 "node_errors" 字段。
    """
```

### 5.7 config.py

```python
# 路径配置
PROJECT_ROOT: Path
CHECKPOINT_DB_PATH: Path
WORKSPACE_DIR: Path
LOG_DIR: Path

# LLM 默认配置
DEFAULT_LLM_TEMPERATURE: float     # 0.3
DEFAULT_LLM_MAX_TOKENS: int        # 4096

# 重试预算
MAX_NODE_LLM_CALLS: int            # 10
MAX_TOTAL_LLM_CALLS: int           # 50
MAX_FIX_LOOP_COUNT: int            # 3

# 环境变量读取
def get_deepxiv_token() -> Optional[str]: ...
def get_llm_api_key() -> Optional[str]: ...
```

---

## 6. 验收标准

### 6.1 端到端验收（核心验收项）

**AC-1: 基础流程可执行**

> 通过 Python 脚本输入一个有效的 arXiv ID（如 `2409.05591`），系统依次执行 `paper_intake` 和 `paper_analysis` 两个节点，成功输出结构化的 `PaperAnalysis` 结果。

验证步骤：
1. 创建包含有效 LLM 配置和 arXiv ID 的初始 `GlobalState`
2. 调用 `build_graph()` 构建图，通过 `graph.invoke(state, config)` 执行
3. 检查返回状态中 `paper_meta` 不为 None 且关键字段（`arxiv_id`, `title`, `abstract`）已填充
4. 检查返回状态中 `paper_analysis` 不为 None 且关键字段（`method_summary`, `datasets`, `metrics`）已填充
5. 检查 `current_step` 已更新

**AC-2: 状态持久化到 SQLite**

> paper_intake 和 paper_analysis 完成后，状态已自动持久化到 SQLite checkpoint 数据库。系统重启后可从 checkpoint 恢复状态。

验证步骤：
1. 执行 AC-1 的流程
2. 确认 `checkpoints.db` 文件已创建且大小 > 0
3. 通过 `graph.get_state(config)` 可读取到完整状态
4. 新建 graph 实例，使用相同 `thread_id`，可恢复到上次执行后的状态

### 6.2 模块级验收

**AC-3: GlobalState 完整性**

> `core/state.py` 中定义的所有 TypedDict 和 Enum 可正常导入，字段定义与技术架构文档第 4 章一致。

**AC-4: 异常体系完整性**

> `core/errors.py` 中定义的所有异常类可正常导入，继承关系正确（如 `isinstance(LLMRateLimitError(), TransientError)` 为 `True`）。

**AC-5: LLM 客户端功能**

> `create_llm()` 可创建有效的 ChatOpenAI 实例；`call_with_structured_output()` 可成功调用 LLM 并解析 JSON 输出；LLM 不可用时正确抛出对应异常。

**AC-6: deepxiv_tools 封装功能**

> `DeepxivTools` 的 `get_paper_brief()` 和 `read_section()` 可正确调用 deepxiv API 获取论文数据；SDK 异常可正确映射为系统内部异常。

**AC-7: paper_analysis 降级链**

> 当 `read_section("Method")` 失败时，paper_analysis 节点尝试别名匹配 -> 全文提取 -> 标记缺失的完整降级链，不抛出致命异常，节点正常完成并标记为降级。

**AC-8: 配置管理**

> `config.py` 中的路径配置和默认值可正确读取；环境变量设置后可覆盖默认值。

**AC-9: 依赖可安装**

> `pip install -r requirements.txt` 可在 Python >= 3.10 环境中成功安装所有依赖，无冲突。

---

## 7. 依赖与风险

### 7.1 外部依赖

| 依赖项 | 类型 | 风险等级 | 说明 |
|--------|------|---------|------|
| deepxiv-sdk >= 0.2.5 | Python 包 | 低 | 已引入项目，API 稳定 |
| deepxiv API 服务 | 外部 API | 中 | 免费额度每日 1000 次请求；API 不可用时 paper_intake 和 paper_analysis 均无法工作 |
| OpenAI 兼容 LLM API | 外部 API | 中 | paper_analysis 依赖 LLM 进行结构化分析；API 不可用时 paper_analysis 无法完成 |
| LangGraph >= 0.2.0 | Python 包 | 低 | LangGraph API 基本稳定，但需关注版本更新的 breaking changes |
| langchain-openai | Python 包 | 低 | ChatOpenAI 接口稳定 |

### 7.2 内部依赖

| 依赖项 | 说明 |
|--------|------|
| 技术架构文档 | 数据结构和异常层次的权威定义来源，实现必须与文档保持一致 |
| 产品设计说明书 | 业务行为的权威定义来源（如学科范围校验、渐进式阅读策略） |

### 7.3 风险矩阵

| 编号 | 风险 | 可能性 | 影响 | 缓解策略 |
|------|------|--------|------|---------|
| R1 | deepxiv API 服务不可用或响应缓慢 | 中 | 高 -- paper_intake 和 paper_analysis 完全依赖 | SDK 内建重试机制（3 次指数退避）；开发和测试阶段可考虑 mock deepxiv 响应 |
| R2 | LLM API 响应质量不稳定，结构化输出解析频繁失败 | 中 | 中 -- 影响 paper_analysis 输出质量 | `call_with_structured_output` 3 次重试 + 错误提示注入；prompt 工程需充分测试 |
| R3 | 论文章节命名不规范，别名匹配覆盖不全 | 高 | 中 -- paper_analysis 部分字段缺失 | 降级链保底（全文提取 -> 标记缺失）；逐步积累常见章节命名模式扩充别名列表 |
| R4 | LangGraph SqliteSaver API 在版本更新中发生 breaking change | 低 | 中 -- 需要修改 checkpointer 封装 | 锁定 LangGraph 最低版本；封装层隔离直接 API 调用 |
| R5 | LLM 上下文窗口溢出（论文内容过长） | 中 | 中 -- paper_analysis 无法分析完整内容 | 调用前 token 估算 + 主动截断/分段；降级为 brief + 关键章节摘要模式 |
| R6 | deepxiv brief() 返回数据字段不完整 | 中 | 低 -- paper_intake 字段部分为空 | brief() + head() 互补策略；允许 Optional 字段为 None |

---

## 8. 开放问题

| 编号 | 问题 | 影响范围 | 建议处理方式 |
|------|------|---------|------------|
| OP-1 | `LLMConfig.api_key` 是否需要避免被 SqliteSaver 持久化到 SQLite？如果需要，技术方案是什么（如在序列化前剔除、或恢复时由用户重新提供）？ | `core/checkpointer.py`、`core/state.py` | 开发阶段由开发者评估 LangGraph SqliteSaver 的序列化行为，确认 api_key 是否会被写入磁盘。如会写入，需在 checkpointer 层或 state 层做脱敏处理 |
| OP-2 | `call_with_structured_output()` 是否使用 LangChain 原生的 `with_structured_output()` 方法，还是自行实现 JSON 解析+重试？ | `core/llm_client.py` | 建议优先使用 LangChain 原生能力（如模型支持 function calling / tool use），回退到手动解析。具体方案由开发者根据目标 LLM 的能力确定 |
| OP-3 | paper_analysis 的 LLM prompt 模板是否需要在 Sprint 1 中固化，还是允许后续 Sprint 持续优化？ | `core/nodes/paper_analysis.py` | Sprint 1 产出可用的初版 prompt，后续 Sprint 可持续迭代优化。Prompt 建议单独存放在模块内的常量或独立文件中，便于修改 |
| OP-4 | Sprint 1 验收时需要使用的具体 arXiv ID 论文是哪篇？是否需要准备多篇测试论文（覆盖有 GitHub 仓库 / 无 GitHub 仓库 / 章节命名不规范等场景）？ | 验收测试 | 建议至少准备 2 篇：1 篇主流 CS 论文（章节结构规范）用于主路径验收，1 篇章节命名不规范的论文用于降级链验收 |

---

**文档结束**

*本文档为 Sprint 1 产品需求文档正式版。所有功能需求的数据结构细节和异常层次定义以技术架构文档（`docs/technical-architecture.md`）为权威来源。*
