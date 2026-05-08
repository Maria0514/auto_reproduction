# Sprint 1 开发计划

**产品名称**：Auto-Reproduction -- 论文自动复现系统
**Sprint**：Sprint 1 -- 基础骨架
**版本**：v1.1（ReAct 架构同步更新）
**日期**：2026-05-07
**作者**：全栈开发工程师代理
**状态**：正式版

---

## 计划概览

- **Sprint 目标**：搭建论文自动复现系统的基础骨架，完成核心数据结构、基础设施层（LLM 客户端、checkpoint 持久化、异常体系、配置管理、ReAct 子图基础设施）以及七步流程中前两步（paper_intake、paper_analysis）的 ReAct agent 实现。
- **总任务数**：11 个模块（S1-01 ~ S1-11）
- **总阶段数**：6 个阶段（A ~ F），分 3 个优先级
- **关键交付物**：通过代码输入 arXiv ID，经 paper_intake 和 paper_analysis 两个 ReAct agent 节点自动完成论文元数据获取与深度分析，输出结构化 `PaperAnalysis` 结果，全程状态可持久化到 SQLite

---

## 模块间依赖关系图

```
config.py (S1-09)           <-- 无前置依赖（最底层）
   ^
   |
core/state.py (S1-01)      <-- 无前置依赖（最底层）
   ^
   |
core/errors.py (S1-02)     <-- 无前置依赖（最底层）
   ^
   |
   +------+----------+-------------------+
   |      |          |                   |
   v      v          v                   v
core/    core/       core/tools/        core/
llm_     check-      deepxiv_           react_base.py (S1-11)
client.  pointer.    tools.py              |
py       py             |                  |  <-- 依赖 llm_client.py
(S1-05)  (S1-04)     (S1-06)              |
   |      |          |  |        +---------+----------+
   |      |          |  |        |                    |
   |      |          |  |        v                    v
   |      |          |  |     core/nodes/          core/nodes/
   |      |          |  |     paper_intake.py      paper_analysis.py
   |      |          |  |     (S1-07, ReAct)       (S1-08, ReAct)
   |      |          |  |        |                    |
   |      |          +--+--------+--------------------+
   |      |                      |
   v      v                      v
   +------+----------------------+
                |
                v
          core/graph.py (S1-03)

requirements.txt (S1-10)    <-- 无依赖，可在任意阶段完成
```

**关键路径**：`config.py` + `state.py` + `errors.py` -> `llm_client.py` + `deepxiv_tools.py` + `checkpointer.py` + `react_base.py` -> `paper_intake.py` + `paper_analysis.py` -> `graph.py`

---

## 优先级 P0：阻塞性基础设施（必须最先完成）

### 阶段 A：无依赖的底层模块

> **前置条件**：无
> **产出**：系统所有模块的基础依赖就绪

---

#### 任务 A1：S1-10 依赖声明 (`requirements.txt`)

- **模块名**：S1-10 依赖声明
- **产出文件**：`requirements.txt`
- **依赖项**：无
- **预计复杂度**：低

**需要实现的内容**：

- 声明 Sprint 1 所需的全部 Python 依赖及版本约束
- 核心依赖：
  - `langgraph>=0.2.0`
  - `langchain-openai>=0.1.0`
  - `langchain-core>=0.2.0`
  - `deepxiv-sdk>=0.2.5`
  - `requests>=2.31.0`
  - `pydantic>=2.0.0`
- 可选依赖：
  - `tiktoken>=0.5.0`
- 文件顶部注释说明 Python >= 3.10 要求

**自测检查点**：
- [x] `pip install -r requirements.txt` 在 Python >= 3.10 环境中无冲突安装成功
- [x] 所有声明的包可正常 import

---

#### 任务 A2：S1-09 配置管理 (`config.py`)

- **模块名**：S1-09 配置管理
- **产出文件**：`config.py`（项目根目录）
- **依赖项**：无前置依赖（仅使用 Python 标准库 `pathlib`, `os`）
- **预计复杂度**：低

**需要实现的具体内容**：

| 类别 | 内容 |
|------|------|
| 路径常量 | `PROJECT_ROOT: Path` -- 项目根目录 |
| | `CHECKPOINT_DB_PATH: Path` -- `{PROJECT_ROOT}/checkpoints.db` |
| | `WORKSPACE_DIR: Path` -- `{PROJECT_ROOT}/workspace` |
| | `LOG_DIR: Path` -- `{WORKSPACE_DIR}/logs` |
| LLM 默认配置 | `DEFAULT_LLM_TEMPERATURE: float = 0.3` |
| | `DEFAULT_LLM_MAX_TOKENS: int = 4096` |
| | `DEFAULT_LLM_BASE_URL: str = "https://api.openai.com/v1"` |
| | `DEFAULT_LLM_MODEL: str = "gpt-4o"` |
| | `LLM_REQUEST_TIMEOUT: int = 60` |
| 重试预算 | `MAX_NODE_LLM_CALLS: int = 10` |
| | `MAX_TOTAL_LLM_CALLS: int = 50` |
| | `MAX_FIX_LOOP_COUNT: int = 3` |
| LLM 重试配置 | `LLM_MAX_RETRIES: int = 3` |
| | `LLM_INITIAL_RETRY_DELAY: float = 2.0` |
| ReAct 配置 | `REACT_MAX_ROUNDS_PAPER_INTAKE: int = 5` -- paper_intake ReAct 最大轮次 |
| | `REACT_MAX_ROUNDS_PAPER_ANALYSIS: int = 12` -- paper_analysis ReAct 最大轮次 |
| | `REACT_LLM_TEMPERATURE: float = 0.3` -- ReAct agent LLM 温度 |
| | `REACT_RESULT_TAG_OPEN: str = "<result>"` -- ReAct 结果标签（开） |
| | `REACT_RESULT_TAG_CLOSE: str = "</result>"` -- ReAct 结果标签（关） |
| | `TOOL_RESULT_MAX_LENGTH: int = 8000` -- 工具返回结果最大字符数（截断保护） |
| 函数 | `get_deepxiv_token() -> Optional[str]` -- 读取 `DEEPXIV_TOKEN` 环境变量 |
| | `get_llm_api_key() -> Optional[str]` -- 读取 `LLM_API_KEY` 环境变量 |
| | `get_llm_base_url() -> str` -- 读取 `LLM_BASE_URL` 或使用默认值 |
| | `get_llm_model() -> str` -- 读取 `LLM_MODEL` 或使用默认值 |
| | `ensure_directories() -> None` -- 确保 WORKSPACE_DIR 和 LOG_DIR 存在 |

**自测检查点**：
- [x] `from config import PROJECT_ROOT, CHECKPOINT_DB_PATH` 可正常导入
- [x] 路径类型均为 `pathlib.Path`
- [x] 环境变量设置后可覆盖默认值
- [x] 环境变量未设置时函数返回默认值或 None

---

#### 任务 A3：S1-01 全局状态定义 (`core/state.py`)

- **模块名**：S1-01 全局状态定义
- **产出文件**：`core/state.py`（需同时创建 `core/__init__.py`）
- **依赖项**：无前置依赖（仅使用 Python 标准库 `typing`, `enum`）；`create_initial_state()` 辅助函数依赖 `config.py`（延迟导入）
- **预计复杂度**：中

**需要实现的具体类/类型**：

| 类型名 | 类别 | 说明 |
|--------|------|------|
| `ExecutionMode` | `str, Enum` | 值: `FULL = "full"`, `CODE_ONLY = "code_only"` |
| `LLMConfig` | `TypedDict` | 字段: `base_url`, `model`, `api_key`, `temperature`, `max_tokens` |
| `PaperMeta` | `TypedDict` | 字段: `arxiv_id`, `title`, `authors`, `abstract`, `categories`, `tldr`, `keywords`, `citation_count`, `github_url`, `publish_date`, `pdf_url` |
| `PaperAnalysis` | `TypedDict` | 字段: `method_summary`, `key_formulas`, `datasets`, `metrics`, `hyperparams`, `hardware_requirements`, `framework`, `baseline_results`, `sections_read`, `analysis_notes` |
| `RepoInfo` | `TypedDict` | 字段: `url`, `source`, `is_official`, `stars`, `forks`, `last_commit_date`, `commit_count_recent`, `has_readme`, `has_requirements`, `dir_structure`, `quality_score` |
| `ResourceInfo` | `TypedDict` | 字段: `repos`, `selected_repo`, `external_resources`, `resource_strategy` |
| `ReproductionPlan` | `TypedDict` | 字段: `plan_summary`, `environment`, `data_preparation`, `code_strategy`, `execution_steps`, `expected_results`, `estimated_time`, `deliverables`, `user_feedback`, `approved` |
| `ExecutionResult` | `TypedDict` | 字段: `success`, `metrics`, `logs`, `errors`, `artifacts`, `runtime_seconds`, `environment_info` |
| `NodeError` | `TypedDict` | 字段: `node_name`, `error_type`, `error_message`, `error_detail`, `timestamp`, `retry_count`, `resolved` |
| `FixLoopRecord` | `TypedDict` | 字段: `round_number`, `error_summary`, `error_category`, `fix_strategy`, `timestamp` |
| `GlobalState` | `TypedDict` | 包含上述所有类型的引用，分组: LLM 配置、用户输入、各步骤输出、流程控制、错误追踪、修复循环追踪、工作目录 |

**需要实现的函数**：

| 函数名 | 签名 | 说明 |
|--------|------|------|
| `create_initial_state` | `(user_input: str, llm_config: LLMConfig, workspace_dir: Optional[str] = None) -> GlobalState` | 创建带完整默认值的初始 GlobalState |

**自测检查点**：
- [ ] 所有 TypedDict 和 Enum 可正常 `from core.state import ...`
- [ ] `ExecutionMode.FULL.value == "full"`
- [ ] `create_initial_state("2409.05591", llm_config)` 返回完整 GlobalState
- [ ] 默认值正确：`retry_budget_remaining=50`, `fix_loop_count=0`, `execution_mode=ExecutionMode.FULL`

---

#### 任务 A4：S1-02 统一异常体系 (`core/errors.py`)

- **模块名**：S1-02 统一异常体系
- **产出文件**：`core/errors.py`
- **依赖项**：无前置依赖（仅使用 Python 标准库）；`make_node_error()` 辅助函数延迟导入 `core/state.py`
- **预计复杂度**：中

**需要实现的异常类**（完整继承树）：

```
AutoReproError (系统根异常)
  属性: message, detail, timestamp
  构造: __init__(self, message: str, detail: Optional[str] = None)
├── TransientError (瞬态错误，可重试)
├── PermanentError (永久错误，不可重试)
├── LLMError (LLM 相关错误基类)
│   ├── LLMAuthError (LLMError + PermanentError)
│   ├── LLMRateLimitError (LLMError + TransientError)
│   │     额外属性: retry_after: Optional[float]
│   ├── LLMContextOverflowError (LLMError + PermanentError)
│   └── LLMOutputError (LLMError + TransientError)
├── SandboxError (沙箱相关错误)
│   ├── SandboxCreationError (SandboxError + PermanentError)
│   └── CodeExecutionError (SandboxError)
│       ├── OOMError (CodeExecutionError + PermanentError)
│       └── ExecutionTimeoutError (CodeExecutionError + PermanentError)
└── DegradedResultError (降级完成，非致命，不继承 Transient 或 Permanent)
```

**需要实现的辅助函数**：

| 函数名 | 签名 | 说明 |
|--------|------|------|
| `make_node_error` | `(node_name, error_type, error_message, error_detail=None, retry_count=0, resolved=False) -> NodeError` | 创建 NodeError TypedDict 的工厂函数 |

**自测检查点**：
- [ ] 所有异常类可正常导入
- [ ] `isinstance(LLMRateLimitError("test"), TransientError)` 为 `True`
- [ ] `isinstance(LLMAuthError("test"), PermanentError)` 为 `True`
- [ ] `isinstance(DegradedResultError("test"), TransientError)` 为 `False`
- [ ] `isinstance(DegradedResultError("test"), PermanentError)` 为 `False`
- [ ] `make_node_error("paper_intake", "permanent", "test")` 返回正确的 NodeError 字典

---

### 阶段 B：依赖阶段 A 的基础设施模块

> **前置条件**：阶段 A 的 config.py, core/state.py, core/errors.py 全部完成
> **产出**：工具层、LLM 客户端、ReAct 子图基础设施和 checkpoint 管理就绪，可供节点层使用

---

#### 任务 B1：S1-04 Checkpoint 管理 (`core/checkpointer.py`)

- **模块名**：S1-04 Checkpoint 管理
- **产出文件**：`core/checkpointer.py`
- **依赖项**：`config.py`（CHECKPOINT_DB_PATH）、`core/errors.py`（PermanentError）
- **预计复杂度**：低

**需要实现的函数**：

| 函数名 | 签名 | 说明 |
|--------|------|------|
| `get_checkpointer` | `(db_path: Optional[str] = None) -> SqliteSaver` | 创建并返回配置好 WAL 模式的 SqliteSaver 实例 |

**实现要点**：
- 默认 `db_path` 从 `config.CHECKPOINT_DB_PATH` 获取
- 确保父目录存在（`db_file.parent.mkdir(parents=True, exist_ok=True)`）
- 检查路径是否为常规文件（已存在的目录等异常情况抛出 `PermanentError`）
- 创建 `sqlite3.connect(db_path, check_same_thread=False)`
- 执行 `PRAGMA journal_mode=WAL;` 和 `PRAGMA synchronous=NORMAL;`
- 将 connection 传入 `SqliteSaver(conn)`

**自测检查点**：
- [ ] `get_checkpointer("/tmp/test_checkpoint.db")` 返回 SqliteSaver 实例
- [ ] 生成的数据库文件使用 WAL 模式
- [ ] 数据库文件不存在时自动创建
- [ ] 路径为目录时抛出 PermanentError

---

#### 任务 B2：S1-05 LLM 客户端封装 (`core/llm_client.py`)

- **模块名**：S1-05 LLM 客户端封装
- **产出文件**：`core/llm_client.py`
- **依赖项**：`core/state.py`（LLMConfig）、`core/errors.py`（LLMAuthError, LLMRateLimitError, LLMContextOverflowError, LLMOutputError, TransientError）
- **预计复杂度**：**高** (风险标注)

**需要实现的函数**：

| 函数名 | 签名 | 说明 |
|--------|------|------|
| `create_llm` | `(config: LLMConfig) -> ChatOpenAI` | 根据配置创建 LangChain ChatOpenAI 实例 |
| `_call_llm_with_retry` | `(llm: ChatOpenAI, prompt: str, max_retries: int = 3, initial_delay: float = 2.0) -> str` | 带指数退避重试的 LLM 调用 |
| `call_with_structured_output` | `(llm: ChatOpenAI, prompt: str, output_schema: Dict[str, Any], max_retries: int = 3) -> Dict[str, Any]` | 调用 LLM 并解析为结构化 JSON 输出 |
| `estimate_tokens` | `(text: str) -> int` | 估算文本 token 数量（tiktoken 优先，字符数/3.5 回退） |
| `check_context_limit` | `(text: str, max_tokens: int) -> bool` | 检查文本是否在上下文窗口限制内（留 20% 余量） |
| `_try_parse_json` | `(text: str) -> Optional[Dict[str, Any]]` | 从 LLM 响应中提取并解析 JSON（支持代码块、纯文本、花括号提取） |
| `_get_parse_error` | `(text: str) -> str` | 获取 JSON 解析失败描述 |
| `_extract_status_code` | `(error: Exception) -> Optional[int]` | 从异常中提取 HTTP 状态码 |
| `_extract_retry_after` | `(error: Exception) -> Optional[float]` | 从异常中提取 Retry-After 值 |

**关键设计决策**：
- 重试策略：手写 for 循环（非 tenacity），精细控制不同异常类型
- 结构化输出：先尝试 LangChain `with_structured_output()`，失败回退手动 JSON 解析
- Token 估算：`try: import tiktoken` 优先，`except ImportError` 回退字符数/3.5

**异常映射规则**：

| 原始异常/状态码 | 系统异常 | 是否重试 |
|---------------|---------|---------|
| HTTP 401, "auth", "unauthorized" | `LLMAuthError` | 否 |
| HTTP 429, "rate limit" | `LLMRateLimitError` | 是（解析 Retry-After） |
| "context overflow/too long" | `LLMContextOverflowError` | 否 |
| HTTP 5xx, 超时 | `TransientError` | 是（指数退避） |
| JSON 解析失败 | `LLMOutputError` | 是（附加错误提示） |

**风险标注**：
- **高风险**：`call_with_structured_output` 的回退策略涉及 LLM 原生能力探测和 JSON 解析容错，需充分测试
- **高风险**：异常分类逻辑依赖字符串匹配，不同 LLM API 的错误消息格式可能不同

**自测检查点**：
- [ ] `create_llm(config)` 返回 ChatOpenAI 实例（不发起网络请求）
- [ ] `estimate_tokens("hello world")` 返回正整数
- [ ] `check_context_limit("short text", 4096)` 返回 `True`
- [ ] `_try_parse_json('```json\n{"key": "value"}\n```')` 返回 `{"key": "value"}`
- [ ] `_try_parse_json('some text {"key": "value"} more text')` 返回 `{"key": "value"}`
- [ ] Mock 测试：认证失败时抛出 `LLMAuthError`
- [ ] Mock 测试：限流时指数退避后抛出 `LLMRateLimitError`

---

#### 任务 B3：S1-06 deepxiv Reader 薄封装 + ReAct 工具工厂函数 (`core/tools/deepxiv_tools.py`)

- **模块名**：S1-06 deepxiv Reader 薄封装 + ReAct 工具工厂函数
- **产出文件**：`core/tools/deepxiv_tools.py`（需同时创建 `core/tools/__init__.py`）
- **依赖项**：`deepxiv-sdk>=0.2.5`、`core/errors.py`（PermanentError, TransientError）、`config.py`（get_deepxiv_token）、`langchain_core.tools`（tool 装饰器, BaseTool）
- **预计复杂度**：**中偏高**

**需要实现的类**：

**`DeepxivTools` 类**：

| 方法 | 签名 | SDK 对应 | 说明 |
|------|------|---------|------|
| `__init__` | `(self, token: Optional[str] = None)` | `Reader(token=token)` | 初始化，未传 token 时从 config 获取 |
| `_handle_sdk_error` | `(self, e: Exception, operation: str) -> None` | -- | SDK 异常 -> 系统异常映射 |
| `search_papers` | `(self, query: str, size: int = 10) -> List[Dict]` | `Reader.search()` | Sprint 1 预留 |
| `get_paper_brief` | `(self, arxiv_id: str) -> Dict` | `Reader.brief()` | paper_intake 使用 |
| `get_paper_head` | `(self, arxiv_id: str) -> Dict` | `Reader.head()` | paper_intake 补充元数据 |
| `get_paper_structure` | `(self, arxiv_id: str) -> Dict` | `Reader.head()` 提取 sections | paper_analysis 使用 |
| `read_section` | `(self, arxiv_id: str, section_name: str) -> str` | `Reader.section()` | paper_analysis 使用 |
| `get_full_paper` | `(self, arxiv_id: str) -> str` | `Reader.raw()` | 降级链兜底 |
| `web_search` | `(self, query: str) -> List[Dict]` | `Reader.websearch()` | Sprint 2 预留 |

**SDK 异常映射表**：

| SDK 异常 | 系统异常 | 重试 |
|---------|---------|------|
| `NotFoundError` | `PermanentError` | 否 |
| `AuthenticationError` | `PermanentError` | 否 |
| `BadRequestError` | `PermanentError` | 否 |
| `RateLimitError` | `TransientError` | SDK 已重试 |
| `ServerError` | `TransientError` | SDK 已重试 |
| `APIError` (其他) | `TransientError` | SDK 已重试 |
| `ValueError` | `PermanentError` | 否 |

**关键实现细节**：
- 每个方法内捕获已映射的系统异常（PermanentError/TransientError）时直接 re-raise
- 空返回值（brief 返回 `{}` / section 返回 `""`）抛出 `PermanentError`
- 所有 API 调用前后记录日志

**ReAct 工具工厂函数**（sprint1/architecture.md 2.7.5）：

为支持 ReAct agent 架构，额外提供 7 个工厂函数，将 `DeepxivTools` 类方法包装为符合 LangChain `BaseTool` 接口的工具实例，与 `ChatOpenAI.bind_tools()` 兼容。

| 工厂函数 | 内部调用 | 使用节点 | 说明 |
|---------|---------|---------|------|
| `get_paper_brief_tool(token=None)` | `DeepxivTools.get_paper_brief()` | paper_intake | 获取论文快速摘要 |
| `get_paper_head_tool(token=None)` | `DeepxivTools.get_paper_head()` | paper_intake | 获取论文元数据与章节结构 |
| `get_paper_structure_tool(token=None)` | `DeepxivTools.get_paper_structure()` | paper_analysis | 获取论文章节结构 |
| `read_section_tool(token=None)` | `DeepxivTools.read_section()` | paper_analysis | 按章节名读取论文内容 |
| `get_full_paper_tool(token=None)` | `DeepxivTools.get_full_paper()` | paper_analysis | 获取论文全文（降级兜底） |
| `search_papers_tool(token=None)` | `DeepxivTools.search_papers()` | paper_intake, paper_analysis | 按关键词搜索论文 |
| `web_search_tool(token=None)` | `DeepxivTools.web_search()` | （Sprint 2 预留） | Web 搜索 |

**工厂函数实现要点**：
- 每个工厂函数接受可选的 `token` 参数，内部创建或复用 `DeepxivTools` 实例
- 使用 LangChain `@tool` 装饰器将普通函数包装为 `BaseTool`，提供 name、description 供 LLM 的 tool_calls 使用
- **关键**：工具函数内部捕获异常并返回错误描述字符串（而非抛出异常），使 ReAct 子图中的 `tool_executor_node` 能稳定运行
- 每个工具函数包含完整的参数描述（docstring），供 LLM 理解参数含义

**自测检查点**：
- [ ] `DeepxivTools` 类可正常实例化
- [ ] Mock Reader 后各方法可正常调用并返回预期格式
- [ ] SDK `NotFoundError` 正确映射为 `PermanentError`
- [ ] SDK `RateLimitError` 正确映射为 `TransientError`
- [ ] 空返回值抛出 `PermanentError`
- [ ] 7 个工具工厂函数均返回 `BaseTool` 实例
- [ ] 工具函数调用异常时返回错误描述字符串（而非抛出异常）
- [ ] 工具函数的 name、description 属性正确设置

---

#### 任务 B4：S1-11 通用 ReAct 子图基础设施 (`core/react_base.py`)

- **模块名**：S1-11 通用 ReAct 子图基础设施
- **产出文件**：`core/react_base.py`
- **依赖项**：`langgraph`（StateGraph, END）、`langchain_core`（BaseMessage, BaseTool, ToolMessage, SystemMessage, AIMessage）、`core/llm_client.py`（create_llm）、`core/state.py`（GlobalState, LLMConfig）、`core/errors.py`、`config.py`（REACT_RESULT_TAG_OPEN, REACT_RESULT_TAG_CLOSE, TOOL_RESULT_MAX_LENGTH）
- **预计复杂度**：**高** (风险标注)
- **全局架构参考**：技术架构文档 3.2.1、sprint1/architecture.md 2.3

**需要实现的类型定义**：

| 类型名 | 说明 |
|--------|------|
| `ReActState` | ReAct 子图内部状态（TypedDict），字段：`messages`（`Annotated[List[BaseMessage], operator.add]`，自动追加语义）、`round`（int）、`max_rounds`（int）、`status`（str: "reasoning" / "tool_call" / "done" / "budget_exhausted"）、`result`（Optional[Dict]）、`context`（Dict[str, Any]，从 GlobalState 注入的只读上下文） |

**需要实现的函数**：

| 函数名 | 签名 | 说明 |
|--------|------|------|
| `create_react_subgraph` | `(node_name: str, system_prompt: str, tools: Sequence[BaseTool], max_rounds: int, result_schema: Optional[Dict] = None) -> CompiledGraph` | 构建通用 ReAct 子图，注册 5 个内部节点 + 条件路由，编译返回 |
| `_make_react_wrapper` | `(node_name: str, build_context: Callable, build_system_prompt: Callable, get_tools: Callable, map_result: Callable, max_rounds: int, result_schema: Optional[Dict] = None) -> Callable[[GlobalState], dict]` | 生成主图节点的 wrapper 函数，自动处理 GlobalState <-> ReActState 双向映射和预算扣减 |

**子图内部节点函数**（均为 `create_react_subgraph` 内部定义）：

| 节点函数 | 职责 |
|---------|------|
| `reasoning_node` | 调用 LLM（已 `bind_tools()`），将响应追加到 messages。检测 tool_calls 或 `<result>` 标签，更新 status 和 round |
| `tool_executor_node` | 执行 LLM 请求的工具调用，将工具结果作为 ToolMessage 追加到 messages。结果截断到 TOOL_RESULT_MAX_LENGTH 字符防止上下文溢出，异常捕获为字符串返回（不中断子图） |
| `budget_check_node` | 检查 `round >= max_rounds - 1` 时将 status 设为 `"budget_exhausted"` |
| `force_finish_node` | 预算耗尽时向 messages 注入强制终止提示（要求 LLM 立即输出 `<result>` 标签），再调用一次 LLM 获取最终输出 |
| `finalize_node` | 从最后一条 AIMessage 中解析 `<result>{JSON}</result>` 标签，提取结构化结果写入 `result` 字段。解析失败时记录警告并设 result 为空字典 |
| `router` | 条件路由函数：`"tool_call"` -> tool_executor，`"done"` -> finalize，`"budget_exhausted"` -> force_finish，其他 -> budget_check |

**子图拓扑**：

```
[reasoning_node] --router--> [tool_executor_node] -> [budget_check_node] -> [reasoning_node]
                    |                                         |
                    +-> "done" -> [finalize_node] -> END      |
                    |                                         |
                    +-> [budget_check_node] -"budget_exhausted"-> [force_finish_node] -> [finalize_node] -> END
```

**`_make_react_wrapper` 执行流程**：

1. 调用 `build_context(state)` 提取上下文
2. 调用 `build_system_prompt(context)` 生成 system prompt
3. 调用 `get_tools(state)` 获取工具列表
4. 调用 `create_react_subgraph()` 构建子图
5. 构造初始 `ReActState`（system prompt 作为第一条 SystemMessage）
6. 运行子图，获取最终 ReActState
7. 调用 `map_result(react_state["result"], state)` 将结果映射回 GlobalState 更新字典
8. 扣减 `retry_budget_remaining`（按实际 round 数扣减）
9. 返回 GlobalState 更新字典

**风险标注**：
- **高风险**：ReAct 子图是 Sprint 1 新增的核心基础设施，所有节点依赖它，设计缺陷将影响全局
- **中风险**：`<result>` 标签解析逻辑需要处理 LLM 输出的多种不规范格式

**自测检查点**：
- [ ] `ReActState` 可正常实例化，messages 字段支持 `operator.add` 追加语义
- [ ] `create_react_subgraph()` 返回 CompiledGraph 实例，包含正确的节点数和边连接
- [ ] 正常终止路径：Mock LLM 输出 `<result>{JSON}</result>` 后 finalize 正确解析
- [ ] 超预算终止路径：Mock LLM 持续返回 tool_calls，达到 max_rounds 后 force_finish 触发并产出结果
- [ ] tool_executor_node：工具执行异常时返回错误字符串追加到 messages，不中断子图
- [ ] tool_executor_node：工具返回结果超过 TOOL_RESULT_MAX_LENGTH 时正确截断
- [ ] `_make_react_wrapper` 生成的 wrapper 函数签名为 `(GlobalState) -> dict`
- [ ] `_make_react_wrapper` 正确映射 GlobalState <-> ReActState 并扣减 retry_budget_remaining

---

## 优先级 P1：核心业务逻辑（依赖 P0 完成）

### 阶段 C：核心节点实现

> **前置条件**：阶段 A 和阶段 B 全部完成（state.py, errors.py, config.py, llm_client.py, deepxiv_tools.py, checkpointer.py, react_base.py 均可用）
> **产出**：两个 ReAct agent 业务节点完整实现

---

#### 任务 C1：S1-07 paper_intake ReAct agent 节点 (`core/nodes/paper_intake.py`)

- **模块名**：S1-07 paper_intake ReAct agent 节点
- **产出文件**：`core/nodes/paper_intake.py`（需同时创建 `core/nodes/__init__.py`）
- **依赖项**：`core/state.py`（GlobalState, PaperMeta）、`core/react_base.py`（_make_react_wrapper）、`core/tools/deepxiv_tools.py`（get_paper_brief_tool, get_paper_head_tool, search_papers_tool）、`core/errors.py`（make_node_error）
- **预计复杂度**：中
- **全局架构参考**：sprint1/architecture.md 2.8.4

**实现方式**：通过 `_make_react_wrapper()` 生成主图节点函数。不再是固定流程函数 + 单次 LLM 调用，而是通过 react_base 创建 ReAct 子图，LLM 自主决定工具调用顺序和策略。

**需要实现的函数**：

| 函数名 | 签名 | 说明 |
|--------|------|------|
| `_build_intake_system_prompt` | `(context: Dict) -> str` | 生成 paper_intake 的 system prompt |
| `_map_intake_result` | `(result: Dict, state: GlobalState) -> dict` | 将 ReAct 子图结果映射回 GlobalState 更新字典 |

**模块级导出**（通过 `_make_react_wrapper` 生成）：

```python
# 使用 _make_react_wrapper 生成主图节点函数
paper_intake = _make_react_wrapper(
    node_name="paper_intake",
    build_context=lambda state: {
        "user_input": state["user_input"],
        "input_type": state.get("input_type", "arxiv_id"),
    },
    build_system_prompt=_build_intake_system_prompt,
    get_tools=lambda state: [
        get_paper_brief_tool(), get_paper_head_tool(), search_papers_tool(),
    ],
    map_result=_map_intake_result,
    max_rounds=5,
    result_schema=PAPER_META_SCHEMA,
)
```

**模块常量**：

| 常量名 | 说明 |
|--------|------|
| `NODE_NAME = "paper_intake"` | 节点名 |
| `PAPER_META_SCHEMA` | 与 PaperMeta TypedDict 对齐的 JSON Schema，供 finalize 解析校验 |

**可用工具**（max_rounds=5，预期消耗 2-3 轮）：
- `get_paper_brief`：获取论文快速摘要
- `get_paper_head`：获取论文元数据与章节结构
- `search_papers`：按关键词搜索论文（ID 格式异常时的备用方案）

**system prompt 要点**：
- 角色：论文元数据获取专家
- 任务：从用户输入中提取 arXiv ID，获取完整的论文元数据
- 策略指导：先尝试 brief，失败则 head，ID 格式异常时尝试清洗或搜索
- ID 格式清洗：处理完整 URL、带版本号 ID、旧格式 ID 等
- 输出格式：在 `<result>{...}</result>` 标签中输出符合 PaperMeta Schema 的 JSON
- 学科范围校验：检查 categories 是否包含 cs.* 前缀，非 CS 论文在结果 notes 中标注

**`_map_intake_result` 实现要点**：
- 从 ReAct 子图的 `result` 字典中提取字段，映射为 PaperMeta TypedDict
- 设置 `current_step = NODE_NAME`
- 异常/空结果时设置 `error` 字段和 `node_errors`

**字段映射规则**（与原固定流程一致，但由 LLM 在 ReAct 循环中自主完成合并）：

| brief 字段 | head 字段 | PaperMeta 字段 | 策略 |
|-----------|----------|---------------|------|
| `arxiv_id` | -- | `arxiv_id` | brief 直接映射 |
| `title` | `title` | `title` | brief 优先 |
| -- | `authors` | `authors` | head 优先 |
| -- | `abstract` | `abstract` | head 优先 |
| -- | `categories` | `categories` | head 优先 |
| `tldr` | -- | `tldr` | brief |
| `keywords` | -- | `keywords` | brief |
| `citations` | -- | `citation_count` | brief |
| `github_url` | -- | `github_url` | brief |
| `publish_at` | `publish_at` | `publish_date` | brief 优先 |
| `src_url` | -- | `pdf_url` | brief |

**ReAct 带来的增强**（相比原固定流程函数）：
- **ID 格式清洗**：agent 可自主判断并清洗用户输入（完整 URL、带版本号 ID、旧格式 ID 等）
- **brief 失败决策**：brief 因论文不存在失败时，agent 可自主用 `search_papers` 搜索相近标题
- **head 补充策略**：agent 自主决定是否调用 head 补充，以及如何合并字段

**自测检查点**：
- [ ] `paper_intake` 是 `_make_react_wrapper` 生成的 callable
- [ ] ReAct wrapper 正确映射 user_input 到 context
- [ ] Mock LLM + Mock 工具后，agent 可通过工具调用获取 PaperMeta
- [ ] 正常路径：brief + head 完整时，PaperMeta 字段全部填充
- [ ] head 获取失败：agent 仅使用 brief 数据，不中断
- [ ] 学科校验：agent 在结果中标注非 CS 论文警告
- [ ] 错误路径：论文不存在时返回 error 字段和 node_errors
- [ ] ID 格式清洗：完整 URL 输入可被正确处理

---

#### 任务 C2：S1-08 paper_analysis ReAct agent 节点 (`core/nodes/paper_analysis.py`)

- **模块名**：S1-08 paper_analysis ReAct agent 节点
- **产出文件**：`core/nodes/paper_analysis.py`
- **依赖项**：`core/state.py`（GlobalState, PaperAnalysis）、`core/react_base.py`（_make_react_wrapper）、`core/tools/deepxiv_tools.py`（get_paper_structure_tool, read_section_tool, get_full_paper_tool, search_papers_tool）、`core/errors.py`（make_node_error）、`config.py`（REACT_MAX_ROUNDS_PAPER_ANALYSIS）
- **预计复杂度**：**高** (风险标注)
- **全局架构参考**：sprint1/architecture.md 2.9.7

**实现方式**：通过 `_make_react_wrapper()` 生成主图节点函数。LLM 作为 ReAct agent 自主进行渐进式章节阅读和分析，自主决定阅读顺序、降级策略和信息综合方式，用 `<result>{JSON}</result>` 标签输出结构化结果。

**需要实现的函数**：

| 函数名 | 签名 | 说明 |
|--------|------|------|
| `_build_analysis_system_prompt` | `(context: Dict) -> str` | 生成 paper_analysis 的 system prompt |
| `_map_analysis_result` | `(result: Dict, state: GlobalState) -> dict` | 将 ReAct 子图结果映射回 GlobalState 更新字典 |

**模块级导出**（通过 `_make_react_wrapper` 生成）：

```python
paper_analysis = _make_react_wrapper(
    node_name="paper_analysis",
    build_context=lambda state: {
        "arxiv_id": state["paper_meta"]["arxiv_id"],
        "paper_meta": state["paper_meta"],
    },
    build_system_prompt=_build_analysis_system_prompt,
    get_tools=lambda state: [
        get_paper_structure_tool(), read_section_tool(),
        get_full_paper_tool(), search_papers_tool(),
    ],
    map_result=_map_analysis_result,
    max_rounds=12,
    result_schema=PAPER_ANALYSIS_SCHEMA,
)
```

**模块常量**：

| 常量名 | 说明 |
|--------|------|
| `NODE_NAME = "paper_analysis"` | 节点名 |
| `PAPER_ANALYSIS_SCHEMA` | 与 PaperAnalysis TypedDict 对齐的 JSON Schema，供 finalize 解析校验。字段：method_summary, key_formulas, datasets, metrics, hyperparams, hardware_requirements, framework, baseline_results, sections_read, analysis_notes |

**可用工具**（max_rounds=12，预期消耗 6-10 轮）：
- `get_paper_structure`：获取论文章节结构，制定阅读策略
- `read_section`：按章节名读取论文内容
- `get_full_paper`：获取论文全文（降级兜底）
- `search_papers`：补充搜索相关论文信息

**system prompt 要点**：
- 角色：深度论文分析专家，专注于提取复现所需的关键技术信息
- 任务：渐进式阅读论文关键章节，提取方法、实验设置、结果等结构化信息
- 策略指导（推荐但 agent 可自主调整）：
  - 先调用 `get_paper_structure` 了解章节结构
  - 按 Method -> Experiments -> Results -> Introduction 优先级阅读
  - 遇到非标准章节名时根据章节结构自主匹配
  - 单个章节读取失败时尝试替代方案（别名匹配、模糊匹配），而非直接跳过
  - 全部章节读取失败时调用 `get_full_paper` 兜底
- 输出格式：在 `<result>{...}</result>` 标签中输出符合 PaperAnalysis Schema 的 JSON
- 包含论文的 title 和 abstract 作为上下文信息

**`_map_analysis_result` 实现要点**：
- 从 ReAct 子图的 `result` 字典中提取字段，映射为 PaperAnalysis TypedDict
- 设置 `current_step = NODE_NAME`
- 结果不完整时（如某些字段缺失），填充默认值并将节点加入 `degraded_nodes`
- 异常/空结果时设置 `error` 字段和 `node_errors`

**PaperAnalysis 字段填充优先级**（agent 在综合分析后填充，默认值由 `_map_analysis_result` 兜底）：

| 字段 | 首选来源 | 回退来源 | 缺失默认值 |
|------|---------|---------|-----------|
| `method_summary` | Method 章节分析 | Introduction -> abstract | 空字符串 |
| `key_formulas` | Method + Introduction | -- | 空列表 |
| `datasets` | Experiments | -- | 空列表 |
| `metrics` | Experiments | -- | 空列表 |
| `hyperparams` | Experiments | -- | 空字典 |
| `hardware_requirements` | Experiments | -- | 空字符串 |
| `framework` | Method -> Introduction | -- | None |
| `baseline_results` | Results | -- | 空字典 |

**ReAct 带来的增强**（相比原固定流程函数 + 硬编码降级链）：

| 方面 | 旧方案（固定流程函数） | 新方案（ReAct agent） |
|------|---------------------|---------------------|
| 章节名匹配 | 硬编码别名列表 | agent 根据章节结构自主判断 |
| 读取失败处理 | 固定降级链：别名 -> 全文 -> 标记缺失 | agent 自主决策下一步行动 |
| 阅读优先级 | 固定顺序 | agent 根据论文类型动态调整 |
| 分析粒度 | 每章节独立 LLM 调用 | agent 在上下文中综合多章节信息 |
| 预算控制 | 手动计数 `llm_call_count` | `budget_check_node` 自动管理 |

**风险标注**：
- **高风险**：system prompt 质量直接影响 agent 的阅读策略和分析结果质量
- **高风险**：agent 工具选择准确率——可能遗漏关键章节或浪费轮次在不重要的章节上
- **中风险**：max_rounds=12 可能不够覆盖复杂论文的完整分析
- **中风险**：`<result>` 标签中的 JSON 不完整或格式不正确

**自测检查点**：
- [ ] `paper_analysis` 是 `_make_react_wrapper` 生成的 callable
- [ ] ReAct wrapper 正确映射 paper_meta 到 context
- [ ] 前置校验：`paper_meta` 为 None 时返回 error
- [ ] Mock LLM + Mock 工具后，agent 自主制定阅读策略并输出 PaperAnalysis
- [ ] agent 可通过多轮工具调用（get_structure -> read_section x N）完成分析
- [ ] 非标准章节名：agent 能根据章节结构自主匹配
- [ ] 降级处理：所有章节读取失败时 agent 调用 get_full_paper 兜底
- [ ] 预算耗尽：达到 max_rounds 后 force_finish 触发，产出部分结果
- [ ] 结果不完整时正确填充默认值并标记 degraded_nodes

---

### 阶段 D：节点集成与图编排

> **前置条件**：阶段 C 的 paper_intake 和 paper_analysis 节点完成
> **产出**：完整可运行的 LangGraph 主图

---

#### 任务 D1：S1-03 LangGraph 主图骨架 (`core/graph.py`)

- **模块名**：S1-03 LangGraph 主图骨架
- **产出文件**：`core/graph.py`
- **依赖项**：`langgraph`（StateGraph, START, END, SqliteSaver）、`core/state.py`（GlobalState）、`core/checkpointer.py`（get_checkpointer）、`core/nodes/paper_intake.py`（paper_intake -- ReAct wrapper 函数）、`core/nodes/paper_analysis.py`（paper_analysis -- ReAct wrapper 函数）
- **预计复杂度**：中
- **全局架构参考**：sprint1/architecture.md 2.4

**需要实现的函数**：

| 函数名 | 签名 | 说明 |
|--------|------|------|
| `build_graph` | `(checkpointer: Optional[SqliteSaver] = None) -> CompiledGraph` | 构建并编译主图 |
| `_passthrough` | `(state: GlobalState) -> dict` | 通用占位节点（内部使用） |
| `resource_scout` | `(state: GlobalState) -> dict` | 占位：步骤 3 |
| `planning` | `(state: GlobalState) -> dict` | 占位：步骤 4（含 interrupt 注释） |
| `coding` | `(state: GlobalState) -> dict` | 占位：步骤 5 |
| `execution` | `(state: GlobalState) -> dict` | 占位：步骤 6 |
| `reporting` | `(state: GlobalState) -> dict` | 占位：步骤 7 |

**图结构**：
```
START -> paper_intake -> paper_analysis -> resource_scout -> planning -> coding -> execution -> reporting -> END
```

**关键实现要点**：
- **paper_intake 和 paper_analysis 为 ReAct wrapper 函数**：从 `core/nodes/paper_intake.py` 和 `core/nodes/paper_analysis.py` 导入的是 `_make_react_wrapper()` 生成的 wrapper 函数，不再是原始的固定流程函数。直接注册到主图即可，签名兼容 `(GlobalState) -> dict`
- 占位节点返回空字典 `{}`，LangGraph merge 语义下不修改任何状态
- `planning` 函数中 `interrupt()` 调用被注释掉（Sprint 1 不启用）
- 留下 TODO 注释标记后续 Sprint 需要添加的条件路由（planning 后路由 + dev_loop 子图嵌入）
- 编译时传入 checkpointer：`graph.compile(checkpointer=checkpointer)`
- 若 checkpointer 未传入，从 `core/checkpointer.py` 获取默认实例

**自测检查点**：
- [ ] `build_graph()` 返回 CompiledGraph 实例
- [ ] 图包含 7 个节点
- [ ] paper_intake 和 paper_analysis 节点使用的是 ReAct wrapper 函数
- [ ] 占位节点不修改状态（返回空字典）
- [ ] 可使用 mock checkpointer 编译成功
- [ ] 全链路可通过 `graph.invoke(state, config)` 执行（使用 mock 或真实依赖）

---

## 优先级 P2：收尾与交付（依赖 P0 和 P1 完成）

### 阶段 E：代码自测与问题修复

> **前置条件**：阶段 A ~ D 全部完成
> **产出**：经过自测验证的完整代码

---

#### 任务 E1：目录结构创建与 `__init__.py` 文件

- **产出文件**：
  - `core/__init__.py`
  - `core/nodes/__init__.py`
  - `core/tools/__init__.py`
- **预计复杂度**：低

**说明**：确保 Python 包结构正确，所有模块可正常导入。这些文件在各阶段创建模块时应已同步创建，此处做最终确认。

---

#### 任务 E2：模块导入与基本功能自测

- **预计复杂度**：中

**自测清单**：

1. **导入测试**：
   - [ ] `from core.state import GlobalState, PaperMeta, PaperAnalysis, ExecutionMode, create_initial_state`
   - [ ] `from core.errors import AutoReproError, TransientError, PermanentError, LLMAuthError, LLMRateLimitError, LLMContextOverflowError, LLMOutputError, make_node_error`
   - [ ] `from core.llm_client import create_llm, call_with_structured_output, estimate_tokens, check_context_limit`
   - [ ] `from core.react_base import ReActState, create_react_subgraph, _make_react_wrapper`
   - [ ] `from core.tools.deepxiv_tools import DeepxivTools, get_paper_brief_tool, get_paper_head_tool, get_paper_structure_tool, read_section_tool, get_full_paper_tool, search_papers_tool, web_search_tool`
   - [ ] `from core.nodes.paper_intake import paper_intake`
   - [ ] `from core.nodes.paper_analysis import paper_analysis`
   - [ ] `from core.checkpointer import get_checkpointer`
   - [ ] `from core.graph import build_graph`
   - [ ] `from config import PROJECT_ROOT, CHECKPOINT_DB_PATH, get_deepxiv_token, get_llm_api_key, REACT_MAX_ROUNDS_PAPER_INTAKE, REACT_MAX_ROUNDS_PAPER_ANALYSIS, REACT_RESULT_TAG_OPEN, REACT_RESULT_TAG_CLOSE, TOOL_RESULT_MAX_LENGTH`

2. **异常继承关系验证**

3. **create_initial_state 默认值验证**

4. **estimate_tokens 基本功能验证**

5. **config.py 环境变量覆盖验证**

---

#### 任务 E3：问题修复

- **预计复杂度**：不确定

**说明**：修复 E2 自测中发现的所有问题。常见问题预判：
- 循环导入（`core/errors.py` -> `core/state.py`）
- LangGraph API 版本兼容性
- deepxiv_sdk 导入路径
- TypedDict Optional 字段的默认值处理

---

### 阶段 F：交付物整理

> **前置条件**：阶段 E 完成，所有自测通过
> **产出**：完整交付物，可供测试工程师验收

---

#### 任务 F1：交付物完整性检查

- **预计复杂度**：低

**检查清单**：

| 产出文件 | 对应模块 | 状态 |
|---------|---------|------|
| `requirements.txt` | S1-10 | |
| `config.py` | S1-09 | |
| `core/__init__.py` | 包初始化 | |
| `core/state.py` | S1-01 | |
| `core/errors.py` | S1-02 | |
| `core/react_base.py` | S1-11 | |
| `core/graph.py` | S1-03 | |
| `core/checkpointer.py` | S1-04 | |
| `core/llm_client.py` | S1-05 | |
| `core/tools/__init__.py` | 包初始化 | |
| `core/tools/deepxiv_tools.py` | S1-06 | |
| `core/nodes/__init__.py` | 包初始化 | |
| `core/nodes/paper_intake.py` | S1-07 | |
| `core/nodes/paper_analysis.py` | S1-08 | |

---

#### 任务 F2：为测试工程师准备验收上下文

- **预计复杂度**：低

**说明**：整理以下信息供测试工程师使用：
- 环境准备说明（Python 版本、依赖安装、环境变量设置）
- 端到端测试运行方式
- 各模块的 mock 测试入口点
- 已知限制和遗留问题
- 验收标准与代码模块的对应关系

---

## 风险总结

| 编号 | 风险 | 影响模块 | 严重度 | 缓解方案 |
|------|------|---------|--------|---------|
| R1 | deepxiv API 不可用或响应缓慢 | B3(deepxiv_tools), C1(paper_intake), C2(paper_analysis) | 高 | SDK 内建 3 次重试；开发阶段准备 mock 数据 |
| R2 | LLM 结构化输出解析频繁失败 | B2(llm_client), C2(paper_analysis) | 中 | 3 次重试 + 错误提示注入；JSON 解析支持多格式 |
| R3 | 论文章节命名不规范导致降级 | C2(paper_analysis) | 中 | ReAct agent 根据章节结构自主匹配（取代硬编码别名列表）；全文提取兜底 |
| R4 | LLM 上下文窗口溢出 | C2(paper_analysis) | 中 | 调用前 token 估算 + 主动截断；TOOL_RESULT_MAX_LENGTH 截断保护 |
| R5 | LangGraph SqliteSaver API 变化 | B1(checkpointer), D1(graph) | 中 | 锁定最低版本 >=0.2.0；封装层隔离 |
| R6 | 循环导入问题 | 全模块 | 低 | 延迟导入策略；E3 阶段排查 |
| R7 | api_key 被序列化到 SQLite | B1(checkpointer) | 低 | Sprint 1 接受风险；文档告知用户 |
| R8 | ReAct agent 工具选择不准确导致信息缺失 | B4(react_base), C1(paper_intake), C2(paper_analysis) | 中 | system prompt 中提供明确的工具使用策略指导；max_rounds 留足余量允许 agent 纠错重试；finalize 阶段校验结果完整性，缺失关键字段时记录到 analysis_notes；用多种类型论文测试 agent 工具调用决策质量 |
| R9 | ReAct 基础设施引入额外开发复杂度 | B4(react_base) | 低 | react_base.py 设计为通用基础设施，一次投入后续 Sprint 全部复用；子图与主图状态完全隔离，降低调试难度；提供充分单元测试覆盖；完成后立即用 paper_intake 验证集成 |

---

## 时间估算（建议顺序）

| 阶段 | 任务 | 预计复杂度 | 估算工时 |
|------|------|-----------|---------|
| A | A1: requirements.txt | 低 | 0.5h |
| A | A2: config.py（含 ReAct 配置常量） | 低 | 1h |
| A | A3: core/state.py | 中 | 2h |
| A | A4: core/errors.py | 中 | 1.5h |
| B | B1: core/checkpointer.py | 低 | 1h |
| B | B2: core/llm_client.py | **高** | 4h |
| B | B3: core/tools/deepxiv_tools.py（含 ReAct 工具工厂函数） | 中偏高 | 3.5h |
| B | B4: core/react_base.py | **高** | 5h |
| C | C1: core/nodes/paper_intake.py（ReAct agent） | 中 | 3h |
| C | C2: core/nodes/paper_analysis.py（ReAct agent） | **高** | 4h |
| D | D1: core/graph.py | 中 | 2h |
| E | E1~E3: 自测与修复 | 中 | 3.5h |
| F | F1~F2: 交付整理 | 低 | 1h |
| | **总计** | | **~32h** |

---

**文档结束**

*本开发计划基于 Sprint 1 PRD (`docs/sprint1/prd.md`) 和架构设计文档 (`docs/sprint1/architecture.md`) 生成。所有模块的接口定义、数据结构和实现细节以架构文档为权威来源。*

*2026-05-07 更新：同步 ReAct agent 架构升级——新增 S1-11 react_base.py 任务（阶段 B4），config.py 新增 ReAct 配置常量，deepxiv_tools.py 新增 7 个工具工厂函数，paper_intake 和 paper_analysis 从固定流程函数升级为 ReAct agent 实现，graph.py 使用 ReAct wrapper 函数注册节点，新增风险 R8/R9，更新时间估算。*
