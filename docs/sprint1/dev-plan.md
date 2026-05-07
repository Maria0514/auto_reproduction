# Sprint 1 开发计划

**产品名称**：Auto-Reproduction -- 论文自动复现系统
**Sprint**：Sprint 1 -- 基础骨架
**版本**：v1.0
**日期**：2026-05-07
**作者**：全栈开发工程师代理
**状态**：正式版

---

## 计划概览

- **Sprint 目标**：搭建论文自动复现系统的基础骨架，完成核心数据结构、基础设施层（LLM 客户端、checkpoint 持久化、异常体系、配置管理）以及七步流程中前两步（paper_intake、paper_analysis）的完整实现。
- **总任务数**：10 个模块（S1-01 ~ S1-10）
- **总阶段数**：6 个阶段（A ~ F），分 3 个优先级
- **关键交付物**：通过代码输入 arXiv ID，自动完成论文元数据获取与深度分析，输出结构化 `PaperAnalysis` 结果，全程状态可持久化到 SQLite

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
   +--------+-----------+--------------------+
   |        |           |                    |
   v        v           v                    v
core/      core/        core/tools/         core/nodes/
llm_       check-       deepxiv_            paper_intake.py (S1-07)
client.py  pointer.py   tools.py            paper_analysis.py (S1-08)
(S1-05)    (S1-04)      (S1-06)                  |
   |        |           |  |                     |
   |        |           +--+---------------------+
   |        |                      |
   v        v                      v
   +--------+----------------------+
                  |
                  v
            core/graph.py (S1-03)

requirements.txt (S1-10)    <-- 无依赖，可在任意阶段完成
```

**关键路径**：`config.py` + `state.py` + `errors.py` -> `llm_client.py` + `deepxiv_tools.py` + `checkpointer.py` -> `paper_intake.py` + `paper_analysis.py` -> `graph.py`

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
| `ResourceInfo` | `TypedDict` | 字段: `repos`, `selected_repo`, `pretrained_models`, `datasets_found`, `resource_strategy` |
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
> **产出**：工具层和基础设施层就绪，可供节点层使用

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

#### 任务 B3：S1-06 deepxiv Reader 薄封装 (`core/tools/deepxiv_tools.py`)

- **模块名**：S1-06 deepxiv Reader 薄封装
- **产出文件**：`core/tools/deepxiv_tools.py`（需同时创建 `core/tools/__init__.py`）
- **依赖项**：`deepxiv-sdk>=0.2.5`、`core/errors.py`（PermanentError, TransientError）、`config.py`（get_deepxiv_token）
- **预计复杂度**：中

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

**自测检查点**：
- [ ] `DeepxivTools` 类可正常实例化
- [ ] Mock Reader 后各方法可正常调用并返回预期格式
- [ ] SDK `NotFoundError` 正确映射为 `PermanentError`
- [ ] SDK `RateLimitError` 正确映射为 `TransientError`
- [ ] 空返回值抛出 `PermanentError`

---

## 优先级 P1：核心业务逻辑（依赖 P0 完成）

### 阶段 C：核心节点实现

> **前置条件**：阶段 A 和阶段 B 全部完成（state.py, errors.py, config.py, llm_client.py, deepxiv_tools.py, checkpointer.py 均可用）
> **产出**：两个业务节点完整实现

---

#### 任务 C1：S1-07 paper_intake 节点 (`core/nodes/paper_intake.py`)

- **模块名**：S1-07 paper_intake 节点
- **产出文件**：`core/nodes/paper_intake.py`（需同时创建 `core/nodes/__init__.py`）
- **依赖项**：`core/state.py`（GlobalState, PaperMeta）、`core/tools/deepxiv_tools.py`（DeepxivTools）、`core/errors.py`（PermanentError, TransientError, make_node_error）
- **预计复杂度**：中

**需要实现的函数**：

| 函数名 | 签名 | 说明 |
|--------|------|------|
| `paper_intake` | `(state: GlobalState) -> dict` | 主节点函数 |
| `_map_to_paper_meta` | `(brief: Dict, head: Optional[Dict], arxiv_id: str) -> PaperMeta` | brief + head 字段映射 |
| `_check_cs_category` | `(paper_meta: PaperMeta) -> None` | 学科范围校验 |

**模块常量**：`NODE_NAME = "paper_intake"`

**核心行为流程**：
1. 从 `state["user_input"]` 读取 arXiv ID
2. 调用 `DeepxivTools.get_paper_brief(arxiv_id)` 获取快速摘要
3. 调用 `DeepxivTools.get_paper_head(arxiv_id)` 补充 authors/abstract/categories（head 获取失败不中断，仅 warning）
4. 通过 `_map_to_paper_meta()` 将 brief + head 映射为 PaperMeta
5. 通过 `_check_cs_category()` 校验学科范围（仅记录警告，不中断）
6. 返回 `{"paper_meta": paper_meta, "current_step": NODE_NAME, "node_errors": node_errors}`

**字段映射规则**（brief 优先 + head 补充）：

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

**错误处理**：
- `PermanentError`（论文不存在/数据为空）：记录 NodeError，设置 `error`，流程终止
- `TransientError`（API 超时/限流，SDK 已重试）：记录 NodeError，设置 `error`
- 未预期异常：记录 NodeError，设置 `error`
- 本节点无降级策略

**自测检查点**：
- [ ] 正常路径：brief + head 完整时，PaperMeta 字段全部填充
- [ ] brief + head 互补：brief 缺少 authors 时从 head 补充
- [ ] head 获取失败：仅使用 brief 数据，不中断
- [ ] 学科校验：CS 论文无 warning，非 CS 论文记录 warning
- [ ] 错误路径：论文不存在时返回 error 字段和 node_errors

---

#### 任务 C2：S1-08 paper_analysis 节点 (`core/nodes/paper_analysis.py`)

- **模块名**：S1-08 paper_analysis 节点
- **产出文件**：`core/nodes/paper_analysis.py`
- **依赖项**：`core/state.py`（GlobalState, PaperAnalysis, LLMConfig）、`core/tools/deepxiv_tools.py`（DeepxivTools）、`core/llm_client.py`（create_llm, call_with_structured_output, estimate_tokens, check_context_limit）、`core/errors.py`（PermanentError, TransientError, LLMContextOverflowError, DegradedResultError, make_node_error）、`config.py`（MAX_NODE_LLM_CALLS）
- **预计复杂度**：**高** (风险标注)

**需要实现的函数**：

| 函数名 | 签名 | 说明 |
|--------|------|------|
| `paper_analysis` | `(state: GlobalState) -> dict` | 主节点函数 |
| `_read_section_with_fallback` | `(tools, arxiv_id, target, aliases, available_sections, full_paper_cache) -> Optional[str]` | 带降级链的章节读取 |
| `_extract_section_names` | `(structure: Optional[Dict]) -> List[str]` | 从章节结构提取章节名列表 |
| `_get_structure_safe` | `(tools, arxiv_id, node_errors) -> Optional[Dict]` | 安全获取章节结构 |
| `_build_analysis_prompt` | `(target: str, content: str, paper_meta: Dict) -> str` | 构建章节分析 prompt |
| `_assemble_paper_analysis` | `(parts, sections_read, notes, paper_meta) -> PaperAnalysis` | 综合结果填充 PaperAnalysis |

**模块常量和 JSON Schema**：

| 常量名 | 说明 |
|--------|------|
| `NODE_NAME = "paper_analysis"` | 节点名 |
| `METHOD_SCHEMA` | Method 章节 LLM 输出 Schema（method_summary, key_formulas, framework） |
| `EXPERIMENTS_SCHEMA` | Experiments 章节 LLM 输出 Schema（datasets, metrics, hyperparams, hardware_requirements） |
| `RESULTS_SCHEMA` | Results 章节 LLM 输出 Schema（baseline_results） |
| `SECTION_PRIORITY` | 章节优先级列表（Method > Experiments > Results > Introduction） |

**章节优先级和别名**：

| 优先级 | 目标章节 | 别名列表 | 对应 Schema |
|--------|---------|---------|------------|
| 1 | Method | Methodology, Approach, Proposed Method, Our Approach, Our Method, Framework, Model, Architecture, Technical Approach, Methods | METHOD_SCHEMA |
| 2 | Experiments | Experimental Setup, Experimental Settings, Experimental Configuration, Setup, Implementation Details, Implementation, Experiment | EXPERIMENTS_SCHEMA |
| 3 | Results | Main Results, Experimental Results, Evaluation, Evaluation Results, Analysis, Results and Analysis, Results and Discussion | RESULTS_SCHEMA |
| 4 | Introduction | （无别名） | METHOD_SCHEMA（补充 framework） |

**降级链**（每个章节依次尝试）：
```
read_section(target)
  -> 失败 -> 遍历 aliases 逐个 read_section(alias)
    -> 全部失败 -> available_sections 模糊匹配
      -> 失败 -> 返回 None
        -> 调用方尝试 get_full_paper() 全文提取
          -> 失败 -> 标记缺失
```

**核心行为流程**：
1. 前置校验：`paper_meta` 为空则直接记录错误返回
2. 获取章节结构（`_get_structure_safe`，失败不中断）
3. 按 SECTION_PRIORITY 顺序遍历章节：
   a. 检查 LLM 调用预算（`llm_call_count >= MAX_NODE_LLM_CALLS` 则停止）
   b. 降级链读取章节内容（`_read_section_with_fallback`）
   c. 若章节读取失败且未缓存全文，尝试 `get_full_paper()` 作为兜底
   d. 检查 token 限制，超出则截断
   e. 调用 `call_with_structured_output()` 进行 LLM 结构化分析
   f. LLMContextOverflowError -> 跳过该章节
   g. 其他异常 -> 记录 NodeError，标记降级
4. 综合结果（`_assemble_paper_analysis`）
5. 降级时将节点加入 `degraded_nodes`

**PaperAnalysis 字段填充优先级**：

| 字段 | 首选来源 | 回退来源 | 缺失默认值 |
|------|---------|---------|-----------|
| `method_summary` | Method 分析结果 | Introduction 分析结果 -> abstract | 空字符串 |
| `key_formulas` | Method + Introduction | -- | 空列表 |
| `datasets` | Experiments | -- | 空列表 |
| `metrics` | Experiments | -- | 空列表 |
| `hyperparams` | Experiments | -- | 空字典 |
| `hardware_requirements` | Experiments | -- | 空字符串 |
| `framework` | Method -> Introduction | -- | None |
| `baseline_results` | Results | -- | 空字典 |

**风险标注**：
- **高风险**：LLM prompt 工程质量直接影响结构化分析结果的可用性
- **高风险**：论文章节命名不规范时降级链的覆盖度
- **中风险**：LLM 调用次数可能超预算，需要预算控制逻辑
- **中风险**：长论文 token 超限导致截断后信息丢失

**自测检查点**：
- [ ] 前置校验：`paper_meta` 为 None 时返回 error
- [ ] 降级链测试：read_section 失败 -> 别名匹配成功
- [ ] 降级链测试：所有别名失败 -> 全文提取成功
- [ ] 降级链测试：全文提取也失败 -> 标记缺失，节点正常返回
- [ ] 预算控制：`llm_call_count` 达到上限时停止分析
- [ ] 所有章节失败时返回带缺失标记的 PaperAnalysis，加入 degraded_nodes
- [ ] Mock LLM 后结构化输出解析正确

---

### 阶段 D：节点集成与图编排

> **前置条件**：阶段 C 的 paper_intake 和 paper_analysis 节点完成
> **产出**：完整可运行的 LangGraph 主图

---

#### 任务 D1：S1-03 LangGraph 主图骨架 (`core/graph.py`)

- **模块名**：S1-03 LangGraph 主图骨架
- **产出文件**：`core/graph.py`
- **依赖项**：`langgraph`（StateGraph, START, END, SqliteSaver）、`core/state.py`（GlobalState）、`core/checkpointer.py`（get_checkpointer）、`core/nodes/paper_intake.py`（paper_intake）、`core/nodes/paper_analysis.py`（paper_analysis）
- **预计复杂度**：中

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
- 占位节点返回空字典 `{}`，LangGraph merge 语义下不修改任何状态
- `planning` 函数中 `interrupt()` 调用被注释掉（Sprint 1 不启用）
- 留下 TODO 注释标记后续 Sprint 需要添加的条件路由
- 编译时传入 checkpointer：`graph.compile(checkpointer=checkpointer)`
- 若 checkpointer 未传入，从 `core/checkpointer.py` 获取默认实例

**自测检查点**：
- [ ] `build_graph()` 返回 CompiledGraph 实例
- [ ] 图包含 7 个节点
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
   - [ ] `from core.tools.deepxiv_tools import DeepxivTools`
   - [ ] `from core.nodes.paper_intake import paper_intake`
   - [ ] `from core.nodes.paper_analysis import paper_analysis`
   - [ ] `from core.checkpointer import get_checkpointer`
   - [ ] `from core.graph import build_graph`
   - [ ] `from config import PROJECT_ROOT, CHECKPOINT_DB_PATH, get_deepxiv_token, get_llm_api_key`

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
| R3 | 论文章节命名不规范导致降级 | C2(paper_analysis) | 中 | 降级链保底（全文提取 -> 标记缺失）；扩充别名列表 |
| R4 | LLM 上下文窗口溢出 | C2(paper_analysis) | 中 | 调用前 token 估算 + 主动截断 |
| R5 | LangGraph SqliteSaver API 变化 | B1(checkpointer), D1(graph) | 中 | 锁定最低版本 >=0.2.0；封装层隔离 |
| R6 | 循环导入问题 | 全模块 | 低 | 延迟导入策略；E3 阶段排查 |
| R7 | api_key 被序列化到 SQLite | B1(checkpointer) | 低 | Sprint 1 接受风险；文档告知用户 |

---

## 时间估算（建议顺序）

| 阶段 | 任务 | 预计复杂度 | 估算工时 |
|------|------|-----------|---------|
| A | A1: requirements.txt | 低 | 0.5h |
| A | A2: config.py | 低 | 1h |
| A | A3: core/state.py | 中 | 2h |
| A | A4: core/errors.py | 中 | 1.5h |
| B | B1: core/checkpointer.py | 低 | 1h |
| B | B2: core/llm_client.py | **高** | 4h |
| B | B3: core/tools/deepxiv_tools.py | 中 | 2.5h |
| C | C1: core/nodes/paper_intake.py | 中 | 2.5h |
| C | C2: core/nodes/paper_analysis.py | **高** | 5h |
| D | D1: core/graph.py | 中 | 2h |
| E | E1~E3: 自测与修复 | 中 | 3h |
| F | F1~F2: 交付整理 | 低 | 1h |
| | **总计** | | **~26h** |

---

**文档结束**

*本开发计划基于 Sprint 1 PRD (`docs/sprint1/prd.md`) 和架构设计文档 (`docs/sprint1/architecture.md`) 生成。所有模块的接口定义、数据结构和实现细节以架构文档为权威来源。*
