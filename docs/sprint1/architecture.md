# Sprint 1 核心架构设计文档

**产品名称**：Auto-Reproduction -- 论文自动复现系统
**Sprint**：Sprint 1 -- 基础骨架
**版本**：v1.0
**日期**：2026-05-06
**作者**：架构师代理
**状态**：正式版

---

## 目录

1. [Sprint 1 架构总览](#1-sprint-1-架构总览)
2. [模块详细设计](#2-模块详细设计)
3. [数据流图](#3-数据流图)
4. [关键设计决策](#4-关键设计决策)
5. [测试策略](#5-测试策略)
6. [风险与缓解](#6-风险与缓解)

---

## 1. Sprint 1 架构总览

### 1.1 Sprint 1 涉及的模块和层次关系

Sprint 1 实现的模块横跨系统五层架构中的三层（编排层、节点层、工具层），以及两个横切关注点（配置管理和异常体系）。

```
Sprint 1 模块层次图

+-------------------------------------------------------------------+
|                 LangGraph 编排层                                     |
|   core/graph.py ............... 主图骨架（7 节点注册 + 顺序边）        |
|   core/checkpointer.py ........ SqliteSaver 持久化                   |
+-------------------------------------------------------------------+
                          |
                          v
+-------------------------------------------------------------------+
|                 Agent 节点层                                         |
|   core/nodes/paper_intake.py ... 论文输入与解析（ReAct agent 实现）    |
|   core/nodes/paper_analysis.py . 深度论文分析（ReAct agent 实现）      |
|   core/nodes/{其余5个}.py ...... 占位 pass-through                   |
+-------------------------------------------------------------------+
                          |
                          v
+-------------------------------------------------------------------+
|                 ReAct 基础设施层                                      |
|   core/react_base.py .......... 通用 ReAct 子图构建器                 |
+-------------------------------------------------------------------+
                          |
                          v
+-------------------------------------------------------------------+
|                 工具层                                               |
|   core/tools/deepxiv_tools.py .. deepxiv Reader 薄封装               |
|   core/llm_client.py .......... LLM 客户端封装                       |
+-------------------------------------------------------------------+

横切关注点：
  config.py .................... 配置管理
  core/state.py ................ 全局状态定义
  core/errors.py ............... 统一异常体系
  core/react_base.py ........... 通用 ReAct 子图基础设施
  requirements.txt ............. 依赖声明
```

### 1.2 模块间依赖关系和初始化顺序

```
依赖关系图（箭头表示"依赖于"）

config.py                     <-- 无前置依赖（最底层）
   ^
   |
core/state.py                 <-- 无前置依赖（最底层）
   ^
   |
core/errors.py                <-- 无前置依赖（最底层）
   ^
   |
   +------+----------+-------------------+
   |      |          |                   |
   v      v          v                   v
core/    core/       core/tools/        core/
llm_     check-      deepxiv_           react_base.py
client.  pointer.    tools.py              |
py       py             |                  |  <-- 依赖 llm_client.py
   |      |          |  |                  |
   |      |          |  |        +---------+----------+
   |      |          |  |        |                    |
   |      |          |  |        v                    v
   |      |          |  |     core/nodes/          core/nodes/
   |      |          |  |     paper_intake.py      paper_analysis.py
   |      |          |  |        |                    |
   |      |          +--+--------+--------------------+
   |      |                      |
   v      v                      v
   +------+----------------------+
                |
                v
          core/graph.py
```

**初始化顺序（运行时）**：

1. `config.py` -- 读取环境变量、设定路径常量
2. `core/state.py` + `core/errors.py` -- 纯定义模块，import 即完成
3. `core/checkpointer.py` -- 创建/打开 SQLite 数据库
4. `core/llm_client.py` + `core/tools/deepxiv_tools.py` -- 工具实例化
5. `core/react_base.py` -- ReAct 子图基础设施（依赖 llm_client.py）
6. `core/nodes/*.py` -- 节点函数注册（通过 react_base.py 构建 ReAct wrapper）
7. `core/graph.py` -- 构建并编译主图

### 1.3 与全局架构的映射关系

| 全局架构文档章节 | Sprint 1 对应模块 | 实现深度 |
|----------------|-----------------|---------|
| 2 系统架构概览 -- 编排层 | `core/graph.py` | 骨架：7 节点 + 顺序边，无条件路由 |
| 2 系统架构概览 -- 节点层 | `core/nodes/*.py` | paper_intake 和 paper_analysis 以 ReAct agent 实现，其余 5 个占位 |
| 2 系统架构概览 -- 工具层 | `core/tools/deepxiv_tools.py`, `core/llm_client.py` | deepxiv_tools 完整实现，llm_client 完整实现 |
| 3 Agent 编排设计 | `core/graph.py` | 顺序编排 + interrupt 占位，无条件路由/修复循环 |
| 3.2.1 ReAct Agent 架构 | `core/react_base.py` | 完整实现 ReActState、create_react_subgraph、_make_react_wrapper |
| 4 全局状态定义 | `core/state.py` | 完整实现全部 TypedDict 和 Enum |
| 6 deepxiv-sdk 集成 | `core/tools/deepxiv_tools.py` | 封装 Sprint 1 使用的 6 个方法 |
| 8 中断恢复方案 | `core/checkpointer.py` | SqliteSaver 初始化 + WAL 模式 |
| 10 LLM 配置策略 | `core/llm_client.py` | create_llm + structured output + token 估算 |
| 12 错误处理策略 | `core/errors.py` + 各节点 | 完整异常层次 + paper_analysis 降级链 |

---

## 2. 模块详细设计

### 2.1 `core/state.py` -- 全局状态

**文件路径**：`core/state.py`
**依赖**：仅 Python 标准库 (`typing`, `enum`)
**全局架构参考**：技术架构文档 4

#### 2.1.1 完整类型定义

```python
"""全局状态定义 -- 所有节点间数据流转的唯一契约。

本模块定义贯穿整个 LangGraph 工作流的全局状态结构。
所有 TypedDict 和 Enum 定义与技术架构文档第 4 章保持严格一致。
"""

from typing import TypedDict, Optional, List, Dict, Any
from enum import Enum


# ========== 执行模式枚举 ==========

class ExecutionMode(str, Enum):
    """执行模式枚举。

    继承 str 使枚举值可直接 JSON 序列化。
    """
    FULL = "full"           # 完整模式：编码 + 执行 + 报告
    CODE_ONLY = "code_only" # 仅编码模式：编码 + 报告（跳过执行）


# ========== LLM 配置 ==========

class LLMConfig(TypedDict):
    """LLM 服务配置，支持任何 OpenAI 兼容 API。

    注意：api_key 字段会随 GlobalState 被 SqliteSaver 序列化。
    Sprint 1 开放问题 OP-1 关注此安全性问题。
    当前方案：在 checkpointer 层不做脱敏，恢复时由用户重新提供 api_key。
    """
    base_url: str           # API 基础地址，如 "https://api.openai.com/v1"
    model: str              # 模型标识，如 "gpt-4o", "deepseek-chat"
    api_key: str            # API 密钥
    temperature: float      # 生成温度，默认 0.3
    max_tokens: int         # 最大输出 token 数，默认 4096


# ========== 论文元数据 ==========

class PaperMeta(TypedDict):
    """步骤 1 输出：论文基础元数据。"""
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
    """步骤 2 输出：深度论文分析结果。"""
    method_summary: str                 # 方法概述
    key_formulas: List[str]             # 关键公式列表
    datasets: List[str]                 # 使用的数据集
    metrics: List[str]                  # 评估指标
    hyperparams: Dict[str, Any]         # 超参数
    hardware_requirements: str          # 硬件要求描述
    framework: Optional[str]            # 推断的框架（PyTorch/TensorFlow/JAX 等）
    baseline_results: Dict[str, Any]    # 基线实验结果
    sections_read: List[str]            # 成功读取的章节列表
    analysis_notes: str                 # 分析备注（含降级说明、缺失信息等）


# ========== 资源信息（Sprint 2 使用，Sprint 1 仅定义）==========

class RepoInfo(TypedDict):
    """单个代码仓库的评估信息。"""
    url: str
    source: str                         # "deepxiv" | "paperswithcode" | "websearch"
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
    """步骤 3 输出：资源搜集与评估结果。"""
    repos: List[RepoInfo]
    selected_repo: Optional[RepoInfo]
    external_resources: List[Dict[str, str]]  # 通用外部资源，用 type 字段区分类别
    resource_strategy: str              # "use_repo" | "from_scratch" | "hybrid"


# ========== 复现计划（Sprint 2 使用，Sprint 1 仅定义）==========

class ReproductionPlan(TypedDict):
    """步骤 4 输出：复现计划。"""
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


# ========== 执行结果（Sprint 3 使用，Sprint 1 仅定义）==========

class ExecutionResult(TypedDict):
    """步骤 6 输出：执行与验证结果。"""
    success: bool
    metrics: Dict[str, Any]
    logs: str
    errors: List[str]
    artifacts: List[str]
    runtime_seconds: float
    environment_info: Dict[str, str]


# ========== 错误追踪 ==========

class NodeError(TypedDict):
    """单个节点的错误记录。"""
    node_name: str              # 发生错误的节点名
    error_type: str             # "transient" | "permanent" | "degraded"
    error_message: str          # 人类可读的错误描述
    error_detail: Optional[str] # 技术细节（堆栈、响应体等）
    timestamp: str              # ISO 8601 时间戳
    retry_count: int            # 已重试次数
    resolved: bool              # 是否已通过重试/降级解决


# ========== 修复循环追踪（Sprint 3 使用，Sprint 1 仅定义）==========

class FixLoopRecord(TypedDict):
    """单轮 execution-coding 修复循环的记录。"""
    round_number: int           # 第几轮（1/2/3）
    error_summary: str          # 本轮 execution 失败的错误摘要
    error_category: str         # "syntax" | "import" | "runtime" | "oom" | "timeout" | "other"
    fix_strategy: str           # coding 节点采用的修复策略描述
    timestamp: str              # ISO 8601 时间戳


# ========== 全局状态 ==========

class GlobalState(TypedDict):
    """LangGraph 全局状态，贯穿整个工作流。

    LangGraph 状态更新语义：
    - 节点函数返回 dict 时，采用 merge 语义（字典浅合并）
    - 返回的 key 会覆盖对应字段，未返回的 key 保持不变
    - List 类型字段（如 node_errors, degraded_nodes）：节点需先复制原列表再追加，
      因为 TypedDict 模式下 LangGraph 对 List 默认是 replace 而非 append
    """

    # --- LLM 配置 ---
    llm_config: LLMConfig

    # --- 用户输入 ---
    user_input: str                      # 原始用户输入（arXiv ID）
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

    # --- 错误追踪 ---
    node_errors: List[NodeError]
    degraded_nodes: List[str]
    retry_budget_remaining: int          # 默认 50

    # --- 修复循环追踪 ---
    fix_loop_count: int                  # 默认 0，上限 3
    fix_loop_history: List[FixLoopRecord]
    user_fix_decision: Optional[str]     # "export_code" | "revise_plan" | "terminate"

    # --- 工作目录 ---
    workspace_dir: str
```

#### 2.1.2 默认值策略

在构建初始状态传入 `graph.invoke()` 时，调用方需提供完整初始状态。以下为各字段的默认值规约：

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `llm_config` | 必须由调用方提供 | 无默认值 |
| `user_input` | 必须由调用方提供 | arXiv ID |
| `input_type` | `"arxiv_id"` | Sprint 1 仅支持 arXiv ID |
| `paper_meta` | `None` | paper_intake 填充 |
| `paper_analysis` | `None` | paper_analysis 填充 |
| `resource_info` ~ `report_path` | `None` | 后续 Sprint 填充 |
| `current_step` | `"start"` | 由各节点更新 |
| `execution_mode` | `ExecutionMode.FULL` | 默认完整模式 |
| `sandbox_type` | `"venv"` | Sprint 1 不实际使用 |
| `error` | `None` | 无错误时为 None |
| `messages` | `[]` | 空列表 |
| `node_errors` | `[]` | 空列表 |
| `degraded_nodes` | `[]` | 空列表 |
| `retry_budget_remaining` | `50` | 来自 config.MAX_TOTAL_LLM_CALLS |
| `fix_loop_count` | `0` | |
| `fix_loop_history` | `[]` | 空列表 |
| `user_fix_decision` | `None` | |
| `workspace_dir` | 来自 `config.WORKSPACE_DIR` | |

**构建初始状态的辅助函数**（建议放在 `core/state.py` 底部）：

```python
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
```

#### 2.1.3 LangGraph 状态更新语义注意事项

LangGraph 使用 `TypedDict` 作为状态时，节点返回的字典中的 key 会**覆盖**（replace）对应字段值。这意味着：

- **标量字段**（str, int, Optional[...]）：直接返回新值即可。
- **List 字段**（node_errors, degraded_nodes 等）：节点**必须先复制**原有列表再追加新元素，否则旧数据会丢失。

```python
# 正确做法
node_errors = list(state.get("node_errors", []))
node_errors.append(new_error)
return {"node_errors": node_errors}

# 错误做法 -- 会丢失历史错误
return {"node_errors": [new_error]}
```

如果后续需要 append 语义，可使用 LangGraph 的 `Annotated[List[T], operator.add]`，但 Sprint 1 暂不引入此机制，保持 TypedDict 的简单性，由节点层手动管理列表拷贝。

---

### 2.2 `core/errors.py` -- 异常体系

**文件路径**：`core/errors.py`
**依赖**：仅 Python 标准库
**全局架构参考**：技术架构文档 12.2

#### 2.2.1 完整继承树

```
AutoReproError (系统根异常)
├── TransientError (瞬态错误 -- 可重试)
├── PermanentError (永久错误 -- 不可重试)
├── LLMError (LLM 相关错误基类)
│   ├── LLMAuthError (LLMError + PermanentError)
│   ├── LLMRateLimitError (LLMError + TransientError)
│   ├── LLMContextOverflowError (LLMError + PermanentError)
│   └── LLMOutputError (LLMError + TransientError)
├── SandboxError (沙箱相关错误)
│   ├── SandboxCreationError (SandboxError + PermanentError)
│   └── CodeExecutionError (SandboxError)
│       ├── OOMError (CodeExecutionError + PermanentError)
│       └── ExecutionTimeoutError (CodeExecutionError + PermanentError)
└── DegradedResultError (降级运行完成，非致命)
```

#### 2.2.2 实现细节

```python
"""统一异常层次定义。

实现三层防御式错误处理架构（技术架构文档 §12）的基础类型。
所有系统内部异常均继承自 AutoReproError，
通过 TransientError / PermanentError 混入区分可重试性。
"""

from datetime import datetime
from typing import Optional


class AutoReproError(Exception):
    """系统根异常。

    所有 Auto-Reproduction 系统内部异常的基类。
    外部异常（如 deepxiv_sdk 的 APIError）在工具层被捕获并转换为本体系中的对应异常。

    Attributes:
        message: 人类可读的错误描述。
        detail: 技术细节（堆栈、响应体等），可选。
        timestamp: 异常创建时间，ISO 8601 格式。
    """
    def __init__(self, message: str, detail: Optional[str] = None):
        super().__init__(message)
        self.message = message
        self.detail = detail
        self.timestamp = datetime.utcnow().isoformat() + "Z"


class TransientError(AutoReproError):
    """瞬态错误，可重试。

    触发场景：网络超时、API 限流、服务端 5xx 等。
    处理方式：工具层自动指数退避重试。
    """
    pass


class PermanentError(AutoReproError):
    """永久错误，不可重试。

    触发场景：认证失败、资源不存在、上下文溢出等。
    处理方式：直接传播到节点层，记录 NodeError。
    """
    pass


# --- LLM 相关异常 ---

class LLMError(AutoReproError):
    """LLM 相关错误基类。"""
    pass


class LLMAuthError(LLMError, PermanentError):
    """LLM API 认证失败（HTTP 401）。

    触发场景：api_key 无效或过期。
    处理方式：不重试，立即提示用户检查 API 配置。
    """
    pass


class LLMRateLimitError(LLMError, TransientError):
    """LLM API 限流（HTTP 429）。

    触发场景：请求频率超出限制。
    处理方式：指数退避重试，优先解析 Retry-After 头。

    Attributes:
        retry_after: Retry-After 头中的等待秒数，可选。
    """
    def __init__(self, message: str, detail: Optional[str] = None,
                 retry_after: Optional[float] = None):
        super().__init__(message, detail)
        self.retry_after = retry_after


class LLMContextOverflowError(LLMError, PermanentError):
    """LLM 上下文窗口溢出。

    触发场景：输入 token 数超出模型的上下文窗口限制。
    处理方式：不重试，节点层切换为精简分析模式。
    """
    pass


class LLMOutputError(LLMError, TransientError):
    """LLM 输出格式不合规。

    触发场景：LLM 返回的内容无法解析为指定 JSON Schema。
    处理方式：附加错误信息后重试（最多 3 次）。
    """
    pass


# --- 沙箱相关异常（Sprint 1 仅定义，Sprint 3 使用）---

class SandboxError(AutoReproError):
    """沙箱相关错误基类。"""
    pass


class SandboxCreationError(SandboxError, PermanentError):
    """沙箱创建失败。

    触发场景：Python 版本不满足、磁盘空间不足。
    """
    pass


class CodeExecutionError(SandboxError):
    """代码执行失败。

    注意：CodeExecutionError 本身不混入 TransientError 或 PermanentError，
    因为代码执行失败可能是瞬态的（如网络下载数据失败）也可能是永久的（如 OOM），
    具体由子类决定。
    """
    pass


class OOMError(CodeExecutionError, PermanentError):
    """内存/显存溢出。"""
    pass


class ExecutionTimeoutError(CodeExecutionError, PermanentError):
    """执行超时。"""
    pass


class DegradedResultError(AutoReproError):
    """降级运行完成，非致命。

    触发场景：节点部分功能降级完成（如 paper_analysis 某些章节读取失败但仍产出了部分分析结果）。
    处理方式：不中断流程，记录到 degraded_nodes，在报告中标注。

    注意：DegradedResultError 既不是 TransientError 也不是 PermanentError。
    它表示"完成了，但质量有损"。
    """
    pass
```

#### 2.2.3 辅助函数

```python
def make_node_error(
    node_name: str,
    error_type: str,
    error_message: str,
    error_detail: Optional[str] = None,
    retry_count: int = 0,
    resolved: bool = False,
) -> "NodeError":
    """创建 NodeError TypedDict 实例的工厂函数。

    Args:
        node_name: 发生错误的节点名。
        error_type: "transient" | "permanent" | "degraded"。
        error_message: 人类可读的错误描述。
        error_detail: 技术细节，可选。
        retry_count: 已重试次数。
        resolved: 是否已通过重试/降级解决。

    Returns:
        NodeError TypedDict 实例。
    """
    from core.state import NodeError
    return NodeError(
        node_name=node_name,
        error_type=error_type,
        error_message=error_message,
        error_detail=error_detail,
        timestamp=datetime.utcnow().isoformat() + "Z",
        retry_count=retry_count,
        resolved=resolved,
    )
```

**设计说明**：

- `make_node_error()` 放在 `core/errors.py` 中而非 `core/state.py` 中，因为它的调用者通常已经导入了 `core/errors.py`（处于 except 块中），减少循环导入风险。
- `LLMRateLimitError` 额外携带 `retry_after` 属性，供 LLM 客户端的重试逻辑使用。
- `DegradedResultError` 特意不继承 `TransientError` 或 `PermanentError`，它是一个**信号异常**，表示"降级完成"而非"失败"。

---

### 2.3 `core/react_base.py` -- 通用 ReAct 子图基础设施

**文件路径**：`core/react_base.py`
**依赖**：`langgraph` (StateGraph, END), `langchain_core` (BaseMessage, BaseTool, tool), `core/llm_client.py`
**全局架构参考**：技术架构文档 3.2.1

#### 2.3.1 ReActState 类型定义

```python
class ReActState(TypedDict):
    """ReAct 子图内部状态，与 GlobalState 完全隔离。"""
    messages: Annotated[List[BaseMessage], operator.add]  # 对话历史（自动追加）
    round: int               # 当前已完成的 LLM 调用轮次
    max_rounds: int           # 最大轮次上限
    status: str               # "reasoning" | "tool_call" | "done" | "budget_exhausted"
    result: Optional[Dict[str, Any]]  # 最终结构化输出（由 finalize 解析）
    context: Dict[str, Any]   # 从 GlobalState 注入的只读上下文
```

**设计要点**：

- `messages` 使用 `Annotated[..., operator.add]` 实现自动追加语义，避免节点手动拷贝列表。
- `context` 是从 GlobalState 注入的只读上下文信息（如 arxiv_id、paper_meta 等），子图内部不修改 GlobalState。
- `status` 驱动子图路由决策，每轮 reasoning 后由 router 根据 LLM 输出更新。

#### 2.3.2 子图拓扑

```
[reasoning_node] ──router──> [tool_executor_node] → [budget_check_node] → [reasoning_node]
                    |                                         |
                    ├→ "done" → [finalize_node] → END         |
                    |                                         |
                    └→ [budget_check_node] ─"budget_exhausted"─→ [force_finish_node] → [finalize_node] → END
```

**拓扑说明**：

- `reasoning_node` 是子图入口，每轮调用 LLM（已 bind_tools）生成推理和行动。
- `router` 是条件边函数，根据 `status` 字段路由到不同分支。
- 正常路径：reasoning → tool_executor → budget_check → reasoning（循环），直到 LLM 输出 `<result>` 标签。
- 完成路径：reasoning → finalize → END。
- 超预算路径：budget_check 检测到接近上限 → force_finish 强制请求最终输出 → finalize → END。

#### 2.3.3 create_react_subgraph() 工厂函数

```python
def create_react_subgraph(
    node_name: str,
    system_prompt: str,
    tools: Sequence[BaseTool],
    max_rounds: int,
    result_schema: Optional[Dict[str, Any]] = None,
) -> "CompiledGraph":
    """构建通用 ReAct 子图。所有 ReAct 节点复用此函数。

    Args:
        node_name: 节点名称，用于日志标识。
        system_prompt: 系统级提示词，定义 agent 的角色和任务。
        tools: 可用工具列表（BaseTool 实例）。
        max_rounds: 最大 LLM 调用轮次。
        result_schema: 期望的结构化输出 JSON Schema，用于 finalize 解析校验。

    Returns:
        编译后的 ReAct 子图 CompiledGraph 实例。
    """
```

**构建流程**：

1. 创建 `StateGraph(ReActState)`。
2. 注册 `reasoning_node`、`tool_executor_node`、`budget_check_node`、`force_finish_node`、`finalize_node`。
3. 设置 `reasoning_node` 为入口节点。
4. 添加条件边：`reasoning_node` → `router` 路由。
5. 添加固定边：`tool_executor_node` → `budget_check_node` → `reasoning_node`；`budget_exhausted` → `force_finish_node` → `finalize_node` → END。
6. 编译并返回。

#### 2.3.4 通用节点函数

| 节点函数 | 职责 |
|---------|------|
| `reasoning_node` | 调用 LLM（已 bind_tools），将响应追加到 messages。检测 tool_calls 或 `<result>` 标签，更新 status 和 round。 |
| `tool_executor_node` | 执行 LLM 请求的工具调用，将工具结果作为 ToolMessage 追加到 messages。结果截断到 8000 字符防止上下文溢出，异常捕获为字符串返回（不中断子图）。 |
| `budget_check_node` | 检查当前 round 是否接近 max_rounds 上限。若 `round >= max_rounds - 1`，将 status 设为 `"budget_exhausted"`。 |
| `force_finish_node` | 预算耗尽时，向 messages 注入强制终止提示（要求 LLM 立即输出 `<result>` 标签），再调用一次 LLM 获取最终输出。 |
| `finalize_node` | 从最后一条 AIMessage 中解析 `<result>{JSON}</result>` 标签，提取结构化结果写入 `result` 字段。解析失败时记录警告并设 result 为空字典。 |
| `router` | 条件路由函数，根据 status 返回下一个节点名：`"tool_call"` → tool_executor，`"done"` → finalize，`"budget_exhausted"` → force_finish，其他 → budget_check。 |

#### 2.3.5 _make_react_wrapper() 主图适配函数

```python
def _make_react_wrapper(
    node_name: str,
    build_context: Callable[[GlobalState], Dict],
    build_system_prompt: Callable[[Dict], str],
    get_tools: Callable[[GlobalState], List[BaseTool]],
    map_result: Callable[[Dict, GlobalState], dict],
    max_rounds: int,
    result_schema: Optional[Dict] = None,
) -> Callable[[GlobalState], dict]:
    """生成主图节点的 wrapper 函数。

    自动处理：GlobalState→ReActState 映射 → 运行子图 → 结果映射回 GlobalState → 预算扣减。

    Args:
        node_name: 节点名称。
        build_context: 从 GlobalState 提取子图所需上下文的函数。
        build_system_prompt: 根据上下文生成 system prompt 的函数。
        get_tools: 根据 GlobalState 获取可用工具列表的函数。
        map_result: 将子图结果映射回 GlobalState 更新字典的函数。
        max_rounds: 最大 LLM 调用轮次。
        result_schema: 期望输出的 JSON Schema。

    Returns:
        可直接注册到主图的节点函数 (GlobalState) -> dict。
    """
```

**执行流程**：

1. 调用 `build_context(state)` 提取上下文。
2. 调用 `build_system_prompt(context)` 生成 system prompt。
3. 调用 `get_tools(state)` 获取工具列表。
4. 调用 `create_react_subgraph()` 构建子图。
5. 构造初始 `ReActState`（system prompt 作为第一条 SystemMessage）。
6. 运行子图，获取最终 ReActState。
7. 调用 `map_result(react_state["result"], state)` 将结果映射回 GlobalState 更新字典。
8. 扣减 `retry_budget_remaining`（按实际 round 数扣减）。
9. 返回 GlobalState 更新字典。

---

### 2.4 `core/graph.py` -- LangGraph 主图

**文件路径**：`core/graph.py`
**依赖**：`langgraph`, `core/state.py`, `core/checkpointer.py`, `core/nodes/*.py`
**全局架构参考**：技术架构文档 3

#### 2.4.1 图构建代码结构

```python
"""LangGraph 主图构建。

注册全部 7 个节点，建立顺序边连接。
Sprint 1 中 paper_intake 和 paper_analysis 以 ReAct wrapper 函数注册（通过
_make_react_wrapper 生成），其余 5 个节点为 pass-through 占位。
"""

import logging
from typing import Optional
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.sqlite import SqliteSaver

from core.state import GlobalState
# paper_intake 和 paper_analysis 导出的是 _make_react_wrapper 生成的 wrapper 函数
from core.nodes.paper_intake import paper_intake
from core.nodes.paper_analysis import paper_analysis

logger = logging.getLogger(__name__)


# ========== 占位节点 ==========

def _passthrough(state: GlobalState) -> dict:
    """占位节点：原样返回，不修改状态。

    后续 Sprint 中将被替换为实际业务逻辑。
    """
    return {}


def resource_scout(state: GlobalState) -> dict:
    """步骤 3：资源搜集与评估（Sprint 2 实现）。"""
    logger.info("resource_scout: pass-through (Sprint 2)")
    return {}


def planning(state: GlobalState) -> dict:
    """步骤 4：复现规划（Sprint 2 实现）。

    TODO (Sprint 2): 在此节点末尾添加 interrupt() 调用实现人在回路。
    """
    logger.info("planning: pass-through (Sprint 2)")
    # interrupt 占位 -- Sprint 1 不实际中断
    # from langgraph.types import interrupt
    # interrupt("请审核复现计划")
    return {}


def coding(state: GlobalState) -> dict:
    """步骤 5：编码与环境搭建（Sprint 3 实现）。"""
    logger.info("coding: pass-through (Sprint 3)")
    return {}


def execution(state: GlobalState) -> dict:
    """步骤 6：执行与测试验证（Sprint 3 实现）。"""
    logger.info("execution: pass-through (Sprint 3)")
    return {}


def reporting(state: GlobalState) -> dict:
    """步骤 7：报告生成（Sprint 3 实现）。"""
    logger.info("reporting: pass-through (Sprint 3)")
    return {}


# ========== 主图构建 ==========

def build_graph(checkpointer: Optional[SqliteSaver] = None) -> "CompiledGraph":
    """构建并编译 LangGraph 主图。

    Args:
        checkpointer: 可选的 SqliteSaver 实例。
                      若不传入，将从 core/checkpointer.py 获取默认实例。

    Returns:
        编译后的 CompiledGraph 实例。

    用法：
        graph = build_graph()
        result = graph.invoke(initial_state, {"configurable": {"thread_id": "task-001"}})
    """
    if checkpointer is None:
        from core.checkpointer import get_checkpointer
        checkpointer = get_checkpointer()

    # 创建 StateGraph
    graph = StateGraph(GlobalState)

    # 注册节点（paper_intake 和 paper_analysis 为 ReAct wrapper 函数）
    graph.add_node("paper_intake", paper_intake)       # ReAct wrapper
    graph.add_node("paper_analysis", paper_analysis)   # ReAct wrapper
    graph.add_node("resource_scout", resource_scout)
    graph.add_node("planning", planning)
    graph.add_node("coding", coding)
    graph.add_node("execution", execution)
    graph.add_node("reporting", reporting)

    # 建立顺序边
    graph.add_edge(START, "paper_intake")
    graph.add_edge("paper_intake", "paper_analysis")
    graph.add_edge("paper_analysis", "resource_scout")
    graph.add_edge("resource_scout", "planning")
    graph.add_edge("planning", "coding")
    graph.add_edge("coding", "execution")
    graph.add_edge("execution", "reporting")
    graph.add_edge("reporting", END)

    # TODO (Sprint 2): 在 planning 后添加 interrupt
    # TODO (Sprint 3): 添加条件路由
    #   - planning 后根据 execution_mode 决定 coding -> execution 还是 coding -> reporting
    #   - execution 后根据结果决定是否路由回 coding（修复循环）

    # 编译
    compiled = graph.compile(checkpointer=checkpointer)
    logger.info("LangGraph main graph compiled successfully with %d nodes", 7)
    return compiled
```

#### 2.4.2 占位节点的实现方式

占位节点返回空字典 `{}`，LangGraph 的 merge 语义下，空字典不会改变任何状态字段。这确保了：

1. 占位节点不会破坏上游节点写入的数据。
2. 后续 Sprint 替换占位实现时只需修改函数体，不需要改变图结构。
3. 端到端执行时，流程可以完整走通 7 个节点到 END。

#### 2.4.3 interrupt 占位方案

Sprint 1 的 `planning` 占位节点中，`interrupt()` 调用被注释掉。这意味着：

- Sprint 1 端到端执行时，图会直接从 planning 继续向下走，不会暂停。
- 验收测试不涉及中断/恢复测试。
- Sprint 2 实现 planning 节点时取消注释，并实现 `Command(resume=...)` 恢复逻辑。

如果需要在 Sprint 1 中验证 interrupt 机制本身（作为技术验证），可以在 planning 函数中有条件地启用：

```python
def planning(state: GlobalState) -> dict:
    # Sprint 1: interrupt 仅在显式开启时生效
    if state.get("_enable_interrupt", False):
        from langgraph.types import interrupt
        interrupt("请审核复现计划")
    return {}
```

但推荐做法是 Sprint 1 不启用 interrupt，保持验收路径简单。

#### 2.4.4 编译选项

```python
compiled = graph.compile(checkpointer=checkpointer)
```

Sprint 1 仅传入 `checkpointer`，不使用 `interrupt_before` / `interrupt_after` 等编译选项。后续 Sprint 根据需求添加。

---

### 2.5 `core/checkpointer.py` -- Checkpoint 管理

**文件路径**：`core/checkpointer.py`
**依赖**：`langgraph`, `sqlite3`, `config.py`
**全局架构参考**：技术架构文档 8

#### 2.5.1 实现细节

```python
"""Checkpoint 持久化管理。

封装 LangGraph SqliteSaver 的初始化，提供 WAL 模式和路径管理。
"""

import logging
import sqlite3
from pathlib import Path
from typing import Optional

from langgraph.checkpoint.sqlite import SqliteSaver

logger = logging.getLogger(__name__)


def get_checkpointer(db_path: Optional[str] = None) -> SqliteSaver:
    """获取 SqliteSaver checkpointer 实例。

    Args:
        db_path: SQLite 数据库文件路径。
                 默认从 config.CHECKPOINT_DB_PATH 获取。

    Returns:
        配置好 WAL 模式的 SqliteSaver 实例。

    Raises:
        PermanentError: 数据库路径不可写时。
    """
    if db_path is None:
        from config import CHECKPOINT_DB_PATH
        db_path = str(CHECKPOINT_DB_PATH)

    db_file = Path(db_path)

    # 确保父目录存在
    db_file.parent.mkdir(parents=True, exist_ok=True)

    # 检查路径是否可写
    if db_file.exists() and not db_file.is_file():
        from core.errors import PermanentError
        raise PermanentError(
            f"Checkpoint 路径不是文件: {db_path}",
            detail=f"{db_path} exists but is not a regular file"
        )

    # 创建 SqliteSaver 并启用 WAL 模式
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")

    checkpointer = SqliteSaver(conn)
    logger.info("Checkpointer initialized: %s (WAL mode)", db_path)
    return checkpointer
```

#### 2.5.2 WAL 模式说明

启用 WAL (Write-Ahead Logging) 模式的两个关键理由：

1. **并发读写**：Streamlit 轮询线程读 + LangGraph 工作线程写可安全并行。
2. **崩溃安全**：WAL 模式下，即使进程意外终止，数据库不会损坏。

`PRAGMA synchronous=NORMAL` 在 WAL 模式下提供足够的持久性保证，同时比 `FULL` 模式性能更好。

#### 2.5.3 api_key 安全性处理方案

**当前状况**：LangGraph SqliteSaver 序列化整个 GlobalState 为 JSON 并写入 SQLite。`LLMConfig.api_key` 会作为 GlobalState 的一部分被写入磁盘。

**Sprint 1 方案（实用主义）**：

1. **不在 checkpointer 层做脱敏**。理由：LangGraph 的序列化是自动的，在 checkpointer 层拦截需要深度侵入 SqliteSaver 内部，维护成本高。
2. **在文档中明确告知**：checkpoint 数据库中包含 api_key，用户需注意文件权限。
3. **恢复时由环境变量提供**：当从 checkpoint 恢复任务时，api_key 从环境变量重新读取，覆盖 checkpoint 中的旧值。

**后续改进（Sprint 2+）**：

- 如需更强安全性，可在 `GlobalState` 中将 `llm_config` 标记为不持久化字段，改为每次从环境变量重建。
- 或者在恢复 state 后，自动用环境变量中的 api_key 替换 checkpoint 中的值。

---

### 2.6 `core/llm_client.py` -- LLM 客户端

**文件路径**：`core/llm_client.py`
**依赖**：`langchain-openai`, `core/state.py`, `core/errors.py`
**全局架构参考**：技术架构文档 10.4, 12.4, 12.5

#### 2.6.1 `create_llm()` 实现

```python
"""LLM 客户端封装。

提供统一的 LLM 调用接口，内建指数退避重试、结构化输出解析和 token 估算。
"""

import json
import logging
import time
from typing import Any, Dict, Optional, Type

from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from core.state import LLMConfig
from core.errors import (
    LLMAuthError,
    LLMRateLimitError,
    LLMContextOverflowError,
    LLMOutputError,
    TransientError,
)

logger = logging.getLogger(__name__)


def create_llm(config: LLMConfig) -> ChatOpenAI:
    """根据配置创建 LangChain ChatOpenAI 实例。

    Args:
        config: LLM 服务配置字典。

    Returns:
        ChatOpenAI 实例，可直接调用 .invoke() 或 .ainvoke()。

    注意：
        此函数仅创建客户端实例，不发起网络请求。
        认证错误将在首次调用时抛出。
    """
    return ChatOpenAI(
        base_url=config["base_url"],
        model=config["model"],
        api_key=config["api_key"],
        temperature=config["temperature"],
        max_tokens=config["max_tokens"],
        request_timeout=60,  # 单次请求超时 60 秒
    )
```

#### 2.6.2 指数退避重试

**方案选择**：手写循环（而非 tenacity 装饰器）。

理由：
- Sprint 1 不希望引入额外依赖。
- 需要精细控制不同异常类型的处理（如 LLMRateLimitError 解析 Retry-After）。
- 代码量不大，手写循环清晰可控。

```python
def _call_llm_with_retry(
    llm: ChatOpenAI,
    prompt: str,
    max_retries: int = 3,
    initial_delay: float = 2.0,
) -> str:
    """带指数退避重试的 LLM 调用。

    Args:
        llm: ChatOpenAI 实例。
        prompt: 用户 prompt 文本。
        max_retries: 最大重试次数（默认 3）。
        initial_delay: 起始退避秒数（默认 2.0，退避序列 2s/4s/8s）。

    Returns:
        LLM 响应文本。

    Raises:
        LLMAuthError: 认证失败，不重试。
        LLMContextOverflowError: 上下文溢出，不重试。
        LLMRateLimitError: 限流后重试仍失败。
        TransientError: 其他瞬态错误重试仍失败。
    """
    last_error = None

    for attempt in range(max_retries + 1):  # 首次 + max_retries 次重试
        try:
            response = llm.invoke(prompt)
            return response.content
        except Exception as e:
            error_str = str(e).lower()
            status_code = _extract_status_code(e)

            # 不可重试的错误 -- 立即抛出
            if status_code == 401 or "auth" in error_str or "unauthorized" in error_str:
                raise LLMAuthError(
                    "LLM API 认证失败，请检查 api_key 配置",
                    detail=str(e)
                )

            if "context" in error_str and ("overflow" in error_str or "length" in error_str or "too long" in error_str):
                raise LLMContextOverflowError(
                    "输入超出 LLM 上下文窗口限制",
                    detail=str(e)
                )

            # 可重试的错误
            if attempt < max_retries:
                if status_code == 429 or "rate" in error_str:
                    retry_after = _extract_retry_after(e)
                    wait_time = retry_after if retry_after else initial_delay * (2 ** attempt)
                    logger.warning(
                        "LLM 限流 (attempt %d/%d), 等待 %.1fs...",
                        attempt + 1, max_retries, wait_time
                    )
                else:
                    wait_time = initial_delay * (2 ** attempt)
                    logger.warning(
                        "LLM 调用失败 (attempt %d/%d): %s, 等待 %.1fs...",
                        attempt + 1, max_retries, str(e)[:100], wait_time
                    )
                time.sleep(wait_time)
                last_error = e
            else:
                last_error = e

    # 所有重试均失败
    if last_error:
        error_str = str(last_error).lower()
        if "rate" in error_str or _extract_status_code(last_error) == 429:
            raise LLMRateLimitError(
                f"LLM API 限流，经 {max_retries} 次重试仍失败",
                detail=str(last_error)
            )
        raise TransientError(
            f"LLM 调用失败，经 {max_retries} 次重试仍无法恢复",
            detail=str(last_error)
        )


def _extract_status_code(error: Exception) -> Optional[int]:
    """尝试从异常中提取 HTTP 状态码。"""
    # langchain-openai 的异常通常包含 status_code 属性
    if hasattr(error, "status_code"):
        return error.status_code
    if hasattr(error, "response") and hasattr(error.response, "status_code"):
        return error.response.status_code
    # 从错误消息中提取
    import re
    match = re.search(r"(\d{3})", str(error))
    if match:
        code = int(match.group(1))
        if 400 <= code <= 599:
            return code
    return None


def _extract_retry_after(error: Exception) -> Optional[float]:
    """尝试从异常中提取 Retry-After 头的值（秒）。"""
    if hasattr(error, "response") and hasattr(error.response, "headers"):
        retry_after = error.response.headers.get("Retry-After")
        if retry_after:
            try:
                return float(retry_after)
            except (ValueError, TypeError):
                pass
    return None
```

#### 2.6.3 `call_with_structured_output()` 实现方案

**方案选择**：优先尝试 LangChain 原生 `with_structured_output()`，回退到手动 JSON 解析。

理由：
- `with_structured_output()` 利用模型的 function calling / tool use 能力，输出质量更高。
- 但并非所有 OpenAI 兼容 API 都支持此功能（如部分 Ollama 模型、vLLM 部署）。
- 因此采用 try-fallback 策略：先尝试原生能力，失败则回退到手动解析。

```python
def call_with_structured_output(
    llm: ChatOpenAI,
    prompt: str,
    output_schema: Dict[str, Any],
    max_retries: int = 3,
) -> Dict[str, Any]:
    """调用 LLM 并解析为结构化 JSON 输出。

    首先尝试使用 LangChain 原生 with_structured_output()，
    若模型不支持则回退到手动 JSON 解析 + 重试策略。

    Args:
        llm: ChatOpenAI 实例。
        prompt: 用户 prompt。
        output_schema: 期望输出的 JSON Schema 字典或 Pydantic Model 类。
        max_retries: 解析失败时的最大重试次数（默认 3）。

    Returns:
        解析后的字典。

    Raises:
        LLMOutputError: 所有重试均无法获得合规输出。
        LLMAuthError: 认证失败。
        LLMContextOverflowError: 上下文溢出。
    """
    # 策略 A：尝试 LangChain 原生 structured output
    try:
        structured_llm = llm.with_structured_output(output_schema)
        result = structured_llm.invoke(prompt)
        if isinstance(result, BaseModel):
            return result.model_dump()
        if isinstance(result, dict):
            return result
    except (NotImplementedError, TypeError, AttributeError):
        logger.info("模型不支持 with_structured_output，回退到手动解析")
    except Exception as e:
        logger.warning("with_structured_output 调用失败: %s, 回退到手动解析", e)

    # 策略 B：手动 JSON 解析 + 重试
    schema_hint = json.dumps(output_schema, ensure_ascii=False, indent=2)
    current_prompt = (
        f"{prompt}\n\n"
        f"请严格按以下 JSON Schema 格式输出，不要包含任何其他文本：\n"
        f"```json\n{schema_hint}\n```"
    )

    last_parse_error = None
    for attempt in range(max_retries):
        raw_text = _call_llm_with_retry(llm, current_prompt)
        parsed = _try_parse_json(raw_text)
        if parsed is not None:
            return parsed

        last_parse_error = _get_parse_error(raw_text)
        logger.warning(
            "结构化输出解析失败 (attempt %d/%d): %s",
            attempt + 1, max_retries, last_parse_error
        )
        current_prompt = (
            f"{prompt}\n\n"
            f"请严格按以下 JSON Schema 格式输出：\n"
            f"```json\n{schema_hint}\n```\n\n"
            f"[上次输出格式错误: {last_parse_error}]\n"
            f"请务必只输出合法 JSON，不要包含解释文字或 markdown 代码块标记。"
        )

    raise LLMOutputError(
        f"经过 {max_retries} 次尝试仍无法获得合规的结构化输出",
        detail=f"最后一次解析错误: {last_parse_error}"
    )


def _try_parse_json(text: str) -> Optional[Dict[str, Any]]:
    """尝试从 LLM 响应文本中提取并解析 JSON。

    支持处理 LLM 常见的输出格式问题：
    - 包裹在 ```json ... ``` 代码块中
    - 前后有多余的文字说明
    """
    # 尝试提取 JSON 代码块
    import re
    json_block = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
    if json_block:
        try:
            return json.loads(json_block.group(1).strip())
        except json.JSONDecodeError:
            pass

    # 尝试直接解析整段文本
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        pass

    # 尝试找到第一个 { 和最后一个 } 之间的内容
    first_brace = text.find("{")
    last_brace = text.rfind("}")
    if first_brace != -1 and last_brace > first_brace:
        try:
            return json.loads(text[first_brace:last_brace + 1])
        except json.JSONDecodeError:
            pass

    return None


def _get_parse_error(text: str) -> str:
    """获取 JSON 解析失败的描述信息。"""
    try:
        json.loads(text)
        return "未知错误"
    except json.JSONDecodeError as e:
        return f"JSON 解析失败: {e}"
```

#### 2.6.4 Token 估算

```python
def estimate_tokens(text: str) -> int:
    """估算文本的 token 数量。

    优先使用 tiktoken（如已安装），否则回退到字符数/4 近似估算。

    Args:
        text: 输入文本。

    Returns:
        估算的 token 数量。
    """
    try:
        import tiktoken
        encoding = tiktoken.get_encoding("cl100k_base")
        return len(encoding.encode(text))
    except ImportError:
        # 回退到粗略估算：英文约 4 字符/token，中文约 2 字符/token
        # 统一用 字符数/3.5 作为折中
        return max(1, int(len(text) / 3.5))


def check_context_limit(text: str, max_tokens: int) -> bool:
    """检查文本是否在上下文窗口限制内。

    Args:
        text: 输入文本。
        max_tokens: 上下文窗口的 token 上限。

    Returns:
        True 表示未超限（安全），False 表示超限。
    """
    estimated = estimate_tokens(text)
    # 留 20% 余量给输出
    safe_limit = int(max_tokens * 0.8)
    is_safe = estimated <= safe_limit
    if not is_safe:
        logger.warning(
            "Token 估算 %d 超出安全限制 %d (上下文窗口 %d)",
            estimated, safe_limit, max_tokens
        )
    return is_safe
```

#### 2.6.5 异常映射规则

| 原始异常/HTTP 状态码 | 系统内部异常 | 是否重试 |
|-------------------|------------|---------|
| HTTP 401, "auth", "unauthorized" | `LLMAuthError` | 不重试 |
| HTTP 429, "rate limit" | `LLMRateLimitError` | 重试（解析 Retry-After） |
| "context", "overflow", "too long" | `LLMContextOverflowError` | 不重试 |
| HTTP 5xx, 超时, 连接失败 | `TransientError`（重试后仍失败） | 重试 |
| JSON 解析失败 | `LLMOutputError` | 重试（附加错误提示） |

---

### 2.7 `core/tools/deepxiv_tools.py` -- deepxiv 封装

**文件路径**：`core/tools/deepxiv_tools.py`
**依赖**：`deepxiv-sdk>=0.2.5`, `core/errors.py`
**全局架构参考**：技术架构文档 6

#### 2.7.1 DeepxivTools 类设计

```python
"""deepxiv Reader 薄封装。

对 deepxiv_sdk.Reader 做薄封装，统一错误处理、日志记录和结果适配。
SDK 内建超时（60s）和重试（3 次指数退避）机制，本封装层不做额外重试。
"""

import logging
from typing import Any, Dict, List, Optional

from deepxiv_sdk import (
    Reader,
    APIError,
    AuthenticationError,
    BadRequestError,
    NotFoundError,
    RateLimitError,
    ServerError,
)

from core.errors import PermanentError, TransientError

logger = logging.getLogger(__name__)


class DeepxivTools:
    """deepxiv Reader 薄封装。

    职责：
    1. 统一错误处理：SDK 异常 -> 系统内部异常
    2. 日志记录：记录所有 API 调用
    3. 结果适配：确保返回值符合系统约定
    """

    def __init__(self, token: Optional[str] = None):
        """初始化 DeepxivTools。

        Args:
            token: deepxiv API token，可选。
                   未提供时从 config.get_deepxiv_token() 获取。
        """
        if token is None:
            from config import get_deepxiv_token
            token = get_deepxiv_token()
        self._reader = Reader(token=token)
        logger.info("DeepxivTools initialized (token=%s)", "***" if token else "None")

    def _handle_sdk_error(self, e: Exception, operation: str) -> None:
        """将 SDK 异常转换为系统内部异常并抛出。

        Args:
            e: SDK 原始异常。
            operation: 操作描述，用于日志。

        Raises:
            PermanentError: 不可重试的 SDK 错误。
            TransientError: 可重试的 SDK 错误（SDK 内部已重试过）。
        """
        logger.error("%s 失败: %s (%s)", operation, type(e).__name__, str(e))

        if isinstance(e, NotFoundError):
            raise PermanentError(
                f"论文不存在: {str(e)}", detail=str(e)
            ) from e
        elif isinstance(e, AuthenticationError):
            raise PermanentError(
                f"deepxiv API 认证失败: {str(e)}", detail=str(e)
            ) from e
        elif isinstance(e, BadRequestError):
            raise PermanentError(
                f"请求参数错误: {str(e)}", detail=str(e)
            ) from e
        elif isinstance(e, RateLimitError):
            raise TransientError(
                f"deepxiv API 限流（SDK 已重试）: {str(e)}", detail=str(e)
            ) from e
        elif isinstance(e, ServerError):
            raise TransientError(
                f"deepxiv 服务端错误（SDK 已重试）: {str(e)}", detail=str(e)
            ) from e
        elif isinstance(e, APIError):
            raise TransientError(
                f"deepxiv API 错误: {str(e)}", detail=str(e)
            ) from e
        elif isinstance(e, ValueError):
            raise PermanentError(
                f"参数无效: {str(e)}", detail=str(e)
            ) from e
        else:
            raise TransientError(
                f"deepxiv 未预期异常: {str(e)}", detail=str(e)
            ) from e
```

#### 2.7.2 各方法实现

```python
    def search_papers(self, query: str, size: int = 10) -> List[Dict]:
        """语义搜索论文。

        Sprint 1 预留，UI 层论文搜索使用。

        Args:
            query: 搜索关键词。
            size: 返回数量，默认 10。

        Returns:
            论文摘要列表，每项包含 arxiv_id, title, abstract 等。
        """
        logger.info("search_papers: query=%r, size=%d", query, size)
        try:
            result = self._reader.search(query=query, size=size)
            papers = result.get("result", [])
            logger.info("search_papers: 返回 %d 篇论文", len(papers))
            return papers
        except Exception as e:
            self._handle_sdk_error(e, f"search_papers(query={query!r})")

    def get_paper_brief(self, arxiv_id: str) -> Dict:
        """获取论文快速摘要。

        对应 SDK Reader.brief()。

        Args:
            arxiv_id: arXiv 论文 ID。

        Returns:
            包含 arxiv_id, title, tldr, keywords, citations, github_url,
            publish_at, src_url 等字段的字典。

        Raises:
            PermanentError: 论文不存在或数据为空。
        """
        logger.info("get_paper_brief: arxiv_id=%s", arxiv_id)
        try:
            result = self._reader.brief(arxiv_id)
            if not result:
                raise PermanentError(
                    f"论文数据为空: {arxiv_id}",
                    detail="brief() 返回空字典"
                )
            logger.info("get_paper_brief: 成功获取 %s", arxiv_id)
            return result
        except (PermanentError, TransientError):
            raise
        except Exception as e:
            self._handle_sdk_error(e, f"get_paper_brief({arxiv_id})")

    def get_paper_head(self, arxiv_id: str) -> Dict:
        """获取论文元数据与章节结构。

        对应 SDK Reader.head()。

        Args:
            arxiv_id: arXiv 论文 ID。

        Returns:
            包含 title, abstract, authors, sections, categories,
            publish_at, token_count 等字段的字典。
        """
        logger.info("get_paper_head: arxiv_id=%s", arxiv_id)
        try:
            result = self._reader.head(arxiv_id)
            if not result:
                raise PermanentError(
                    f"论文元数据为空: {arxiv_id}",
                    detail="head() 返回空字典"
                )
            logger.info("get_paper_head: 成功获取 %s", arxiv_id)
            return result
        except (PermanentError, TransientError):
            raise
        except Exception as e:
            self._handle_sdk_error(e, f"get_paper_head({arxiv_id})")

    def get_paper_structure(self, arxiv_id: str) -> Dict:
        """获取论文章节结构。

        实际调用 head() 并提取 sections 字段。

        Args:
            arxiv_id: arXiv 论文 ID。

        Returns:
            包含 sections 列表的字典。sections 中每项包含 name 和 token_count。
        """
        head = self.get_paper_head(arxiv_id)
        sections = head.get("sections", [])
        logger.info("get_paper_structure: %s 有 %d 个章节", arxiv_id, len(sections))
        return {"sections": sections, "token_count": head.get("token_count")}

    def read_section(self, arxiv_id: str, section_name: str) -> str:
        """读取特定章节内容。

        对应 SDK Reader.section()。SDK 内部已做模糊匹配（大小写不敏感、部分匹配）。

        Args:
            arxiv_id: arXiv 论文 ID。
            section_name: 章节名称。

        Returns:
            章节内容文本。

        Raises:
            PermanentError: 章节不存在（SDK 抛出 ValueError）。
        """
        logger.info("read_section: arxiv_id=%s, section=%s", arxiv_id, section_name)
        try:
            content = self._reader.section(arxiv_id, section_name)
            if not content:
                raise PermanentError(
                    f"章节内容为空: {arxiv_id}/{section_name}",
                    detail="section() 返回空字符串"
                )
            logger.info(
                "read_section: %s/%s 获取 %d 字符",
                arxiv_id, section_name, len(content)
            )
            return content
        except (PermanentError, TransientError):
            raise
        except ValueError as e:
            # SDK 的 section() 在章节不存在时抛 ValueError
            raise PermanentError(
                f"章节 '{section_name}' 不存在于论文 {arxiv_id}",
                detail=str(e)
            ) from e
        except Exception as e:
            self._handle_sdk_error(e, f"read_section({arxiv_id}, {section_name})")

    def get_full_paper(self, arxiv_id: str) -> str:
        """获取论文完整内容（Markdown 格式）。

        对应 SDK Reader.raw()。用于降级链兜底。

        Args:
            arxiv_id: arXiv 论文 ID。

        Returns:
            完整论文的 Markdown 文本。
        """
        logger.info("get_full_paper: arxiv_id=%s", arxiv_id)
        try:
            content = self._reader.raw(arxiv_id)
            if not content:
                raise PermanentError(
                    f"论文全文为空: {arxiv_id}",
                    detail="raw() 返回空字符串"
                )
            logger.info("get_full_paper: %s 获取 %d 字符", arxiv_id, len(content))
            return content
        except (PermanentError, TransientError):
            raise
        except Exception as e:
            self._handle_sdk_error(e, f"get_full_paper({arxiv_id})")

    def web_search(self, query: str) -> List[Dict]:
        """Web 搜索。

        Sprint 2 resource_scout 使用，Sprint 1 预留。

        Args:
            query: 搜索查询。

        Returns:
            搜索结果列表。
        """
        logger.info("web_search: query=%r", query)
        try:
            result = self._reader.websearch(query)
            logger.info("web_search: 返回结果")
            return result.get("results", []) if isinstance(result, dict) else []
        except Exception as e:
            self._handle_sdk_error(e, f"web_search(query={query!r})")
```

#### 2.7.3 SDK 异常映射表（汇总）

| SDK 异常 | 系统异常 | 重试 | 说明 |
|---------|---------|------|------|
| `NotFoundError` | `PermanentError` | 否 | 论文不存在 |
| `AuthenticationError` | `PermanentError` | 否 | token 无效 |
| `BadRequestError` | `PermanentError` | 否 | 请求参数错误 |
| `RateLimitError` | `TransientError` | SDK 已重试 | 封装层不额外重试 |
| `ServerError` | `TransientError` | SDK 已重试 | 封装层不额外重试 |
| `APIError` (其他) | `TransientError` | SDK 已重试 | 连接超时等 |
| `ValueError` | `PermanentError` | 否 | 空 ID、章节不存在等 |

#### 2.7.4 SDK 实际 API 对应关系

| DeepxivTools 方法 | SDK Reader 方法 | 返回值说明 |
|-------------------|----------------|-----------|
| `search_papers()` | `Reader.search()` | 返回 `result["result"]`（列表） |
| `get_paper_brief()` | `Reader.brief()` | 返回完整字典 |
| `get_paper_head()` | `Reader.head()` | 返回完整字典（含 sections） |
| `get_paper_structure()` | `Reader.head()` | 提取 sections 子字段 |
| `read_section()` | `Reader.section()` | 返回章节文本字符串 |
| `get_full_paper()` | `Reader.raw()` | 返回完整 Markdown 文本 |
| `web_search()` | `Reader.websearch()` | 返回搜索结果列表 |

**重要实现细节**：SDK 的 `section()` 方法内部会先调用 `head()` 获取章节列表进行模糊匹配，然后请求对应章节内容。这意味着每次 `read_section()` 调用会产生 2 次 HTTP 请求。如果需要连续读取多个章节，应先调用 `get_paper_structure()` 获取实际章节名列表，再用匹配后的精确章节名调用 `read_section()`，但由于 SDK 内部仍会重复调用 head()，这种优化效果有限。Sprint 1 接受这个开销。

#### 2.7.5 ReAct 工具工厂函数

为支持 ReAct agent 架构，`deepxiv_tools.py` 额外提供一组工厂函数，将 `DeepxivTools` 类的方法包装为符合 LangChain `BaseTool` 接口的工具实例。ReAct 子图通过这些工具函数与 deepxiv API 交互。

```python
# ReAct agent 工具工厂函数
# 在内部使用 DeepxivTools 类，外部通过 @tool 装饰器暴露为 BaseTool
def get_paper_brief_tool(token=None) -> BaseTool: ...
def get_paper_head_tool(token=None) -> BaseTool: ...
def get_paper_structure_tool(token=None) -> BaseTool: ...
def read_section_tool(token=None) -> BaseTool: ...
def get_full_paper_tool(token=None) -> BaseTool: ...
def search_papers_tool(token=None) -> BaseTool: ...
def web_search_tool(token=None) -> BaseTool: ...
```

**设计要点**：

- 每个工厂函数接受可选的 `token` 参数，内部创建或复用 `DeepxivTools` 实例。
- 使用 `@tool` 装饰器将普通函数包装为 `BaseTool`，提供 name、description 供 LLM 的 tool_calls 使用。
- 工具函数内部捕获异常并返回错误描述字符串（而非抛出异常），使 ReAct 子图中的 `tool_executor_node` 能稳定运行。

**工具-节点映射表**：

| 工具函数 | 使用节点 | 说明 |
|---------|---------|------|
| `get_paper_brief_tool` | paper_intake | 获取论文快速摘要 |
| `get_paper_head_tool` | paper_intake | 获取论文元数据与章节结构 |
| `search_papers_tool` | paper_intake | 按关键词搜索论文（ID 清洗失败时的备用方案） |
| `get_paper_structure_tool` | paper_analysis | 获取论文章节结构，制定阅读策略 |
| `read_section_tool` | paper_analysis | 按章节名读取论文内容 |
| `get_full_paper_tool` | paper_analysis | 获取论文全文（降级兜底） |
| `search_papers_tool` | paper_analysis | 补充搜索相关论文信息 |
| `web_search_tool` | （Sprint 2 预留） | Web 搜索 |

---

### 2.8 `core/nodes/paper_intake.py` -- paper_intake 节点

**文件路径**：`core/nodes/paper_intake.py`
**依赖**：`core/state.py`, `core/tools/deepxiv_tools.py`, `core/errors.py`
**全局架构参考**：技术架构文档 3.1（节点1）, 12.5（节点函数模板）

#### 2.8.1 完整流程（伪代码）

```python
"""paper_intake 节点：论文输入与解析。

接收 UI 层传入的已确认 arXiv ID，调用 deepxiv Reader 获取论文基础元数据。
本节点无降级策略——论文元数据是后续所有步骤的基础，缺失时流程无法继续。
"""

import logging
from typing import Dict

from core.state import GlobalState, PaperMeta
from core.tools.deepxiv_tools import DeepxivTools
from core.errors import PermanentError, TransientError, make_node_error

logger = logging.getLogger(__name__)

NODE_NAME = "paper_intake"


def paper_intake(state: GlobalState) -> dict:
    """论文输入与解析节点。

    流程：
    1. 从 state["user_input"] 读取 arXiv ID
    2. 调用 brief() 获取快速摘要
    3. 调用 head() 补充 brief 中可能缺失的字段（authors, abstract, categories）
    4. 将结果映射为 PaperMeta
    5. 校验学科范围（cs.* 前缀检查）
    6. 返回状态更新

    Returns:
        状态更新字典。成功时包含 paper_meta 和 current_step。
        失败时包含 error 和 node_errors。
    """
    node_errors = list(state.get("node_errors", []))
    arxiv_id = state["user_input"].strip()
    logger.info("[%s] 开始处理: arxiv_id=%s", NODE_NAME, arxiv_id)

    try:
        tools = DeepxivTools()

        # 步骤 1：获取 brief
        brief = tools.get_paper_brief(arxiv_id)

        # 步骤 2：获取 head 补充信息
        head = None
        try:
            head = tools.get_paper_head(arxiv_id)
        except Exception as e:
            logger.warning("[%s] head() 获取失败，仅使用 brief 数据: %s", NODE_NAME, e)

        # 步骤 3：字段映射（brief 优先，head 补充）
        paper_meta = _map_to_paper_meta(brief, head, arxiv_id)

        # 步骤 4：学科范围校验
        _check_cs_category(paper_meta)

        logger.info("[%s] 完成: %s", NODE_NAME, paper_meta.get("title", "")[:60])
        return {
            "paper_meta": paper_meta,
            "current_step": NODE_NAME,
            "node_errors": node_errors,
        }

    except PermanentError as e:
        logger.error("[%s] 永久错误: %s", NODE_NAME, e.message)
        node_errors.append(make_node_error(
            NODE_NAME, "permanent", e.message, e.detail
        ))
        return {
            "error": e.message,
            "current_step": NODE_NAME,
            "node_errors": node_errors,
        }

    except TransientError as e:
        logger.error("[%s] 瞬态错误（SDK 已重试）: %s", NODE_NAME, e.message)
        node_errors.append(make_node_error(
            NODE_NAME, "transient", e.message, e.detail
        ))
        return {
            "error": e.message,
            "current_step": NODE_NAME,
            "node_errors": node_errors,
        }

    except Exception as e:
        logger.error("[%s] 未预期错误: %s", NODE_NAME, str(e), exc_info=True)
        node_errors.append(make_node_error(
            NODE_NAME, "permanent", f"未预期错误: {str(e)}", str(e)
        ))
        return {
            "error": str(e),
            "current_step": NODE_NAME,
            "node_errors": node_errors,
        }
```

#### 2.8.2 brief() + head() 互补获取策略

```python
def _map_to_paper_meta(
    brief: Dict, head: Dict | None, arxiv_id: str
) -> PaperMeta:
    """将 brief + head 响应映射为 PaperMeta。

    策略：brief 字段优先使用，head 补充 brief 中缺失的字段。

    字段映射规则：
    | 来源 | brief 字段 | head 字段 | PaperMeta 字段 |
    |------|-----------|----------|---------------|
    | brief | arxiv_id | - | arxiv_id |
    | brief | title | title | title |
    | head 优先 | - | authors | authors |
    | head 优先 | - | abstract | abstract |
    | head 优先 | - | categories | categories |
    | brief | tldr | - | tldr |
    | brief | keywords | - | keywords |
    | brief | citations | - | citation_count |
    | brief | github_url | - | github_url |
    | brief/head | publish_at | publish_at | publish_date |
    | brief | src_url | - | pdf_url |
    """
    h = head or {}

    return PaperMeta(
        arxiv_id=brief.get("arxiv_id", arxiv_id),
        title=brief.get("title") or h.get("title", ""),
        authors=h.get("authors", brief.get("authors", [])),
        abstract=h.get("abstract", brief.get("abstract", "")),
        categories=h.get("categories", brief.get("categories", [])),
        tldr=brief.get("tldr"),
        keywords=brief.get("keywords"),
        citation_count=brief.get("citations"),
        github_url=brief.get("github_url"),
        publish_date=brief.get("publish_at") or h.get("publish_at"),
        pdf_url=brief.get("src_url"),
    )
```

#### 2.8.3 学科范围校验

```python
def _check_cs_category(paper_meta: PaperMeta) -> None:
    """检查论文是否属于 CS 领域。

    检查 categories 中是否包含 cs.* 前缀的分类。
    非 CS 论文不中断流程，仅记录 WARNING。
    """
    categories = paper_meta.get("categories", [])
    if not categories:
        logger.warning("[%s] 论文无分类信息，跳过学科校验", NODE_NAME)
        return

    is_cs = any(
        cat.lower().startswith("cs.") for cat in categories
    )
    if not is_cs:
        logger.warning(
            "[%s] 论文不属于 CS 领域: categories=%s。"
            "系统针对 CS 论文优化，非 CS 论文的复现效果可能不佳。",
            NODE_NAME, categories
        )
```

#### 2.8.4 ReAct Agent 实现设计

**为什么 paper_intake 需要 ReAct**：

paper_intake 看似是简单的数据获取节点，但实际存在多种需要自主决策的场景：

1. **ID 格式清洗**：用户输入可能是完整 URL（`https://arxiv.org/abs/2409.05591`）、带版本号的 ID（`2409.05591v2`）、或旧格式 ID（`cs/0601001`），agent 需要自主判断并清洗。
2. **brief 失败需自主决策**：当 `get_paper_brief` 因论文不存在失败时，agent 可以自主尝试用 `search_papers` 搜索相近标题，或尝试去除版本号重试。
3. **head 补充策略**：brief 返回数据不完整时，agent 自主决定是否调用 head 补充，以及如何合并字段。

**可用工具**：

- `get_paper_brief`：获取论文快速摘要
- `get_paper_head`：获取论文元数据与章节结构
- `search_papers`：按关键词搜索论文

**配置参数**：

- `max_rounds = 5`
- `result_schema`：与 `PaperMeta` TypedDict 对齐的 JSON Schema

**system prompt 要点**：

- 角色：论文元数据获取专家
- 任务：从用户输入中提取 arXiv ID，获取完整的论文元数据
- 策略指导：先尝试 brief，失败则 head，ID 格式异常时尝试清洗或搜索
- 输出格式：在 `<result>{...}</result>` 标签中输出符合 PaperMeta Schema 的 JSON

**主图节点注册**：

```python
# 使用 _make_react_wrapper 生成主图节点函数
paper_intake = _make_react_wrapper(
    node_name="paper_intake",
    build_context=lambda state: {"user_input": state["user_input"], "input_type": state.get("input_type", "arxiv_id")},
    build_system_prompt=_build_intake_system_prompt,
    get_tools=lambda state: [get_paper_brief_tool(), get_paper_head_tool(), search_papers_tool()],
    map_result=_map_intake_result,
    max_rounds=5,
    result_schema=PAPER_META_SCHEMA,
)
```

---

### 2.9 `core/nodes/paper_analysis.py` -- paper_analysis 节点

**文件路径**：`core/nodes/paper_analysis.py`
**依赖**：`core/state.py`, `core/tools/deepxiv_tools.py`, `core/llm_client.py`, `core/errors.py`
**全局架构参考**：技术架构文档 3.1（节点2）, 12.5（降级链）

#### 2.9.1 完整流程（伪代码）

```python
"""paper_analysis 节点：深度论文分析。

渐进式阅读论文关键章节，调用 LLM 进行结构化分析，填充 PaperAnalysis。
实现完整降级链：章节读取 -> 别名匹配 -> 全文提取 -> 标记缺失。
"""

import logging
from typing import Any, Dict, List, Optional

from core.state import GlobalState, PaperAnalysis, LLMConfig
from core.tools.deepxiv_tools import DeepxivTools
from core.llm_client import (
    create_llm,
    call_with_structured_output,
    estimate_tokens,
    check_context_limit,
)
from core.errors import (
    PermanentError,
    TransientError,
    LLMContextOverflowError,
    DegradedResultError,
    make_node_error,
)
from config import MAX_NODE_LLM_CALLS

logger = logging.getLogger(__name__)

NODE_NAME = "paper_analysis"


def paper_analysis(state: GlobalState) -> dict:
    """深度论文分析节点。

    完整流程：
    1. 前置校验：paper_meta 是否存在
    2. 获取章节结构
    3. 按优先级渐进式读取章节（含降级链）
    4. 对各章节调用 LLM 结构化分析
    5. 综合结果填充 PaperAnalysis
    6. 预算控制和错误追踪

    Returns:
        状态更新字典。
    """
    node_errors = list(state.get("node_errors", []))
    degraded_nodes = list(state.get("degraded_nodes", []))
    is_degraded = False
    llm_call_count = 0

    # 前置校验
    paper_meta = state.get("paper_meta")
    if not paper_meta:
        logger.error("[%s] paper_meta 为空，上游 paper_intake 失败", NODE_NAME)
        node_errors.append(make_node_error(
            NODE_NAME, "permanent",
            "paper_meta 为空，无法进行论文分析"
        ))
        return {
            "error": "paper_meta 为空",
            "current_step": NODE_NAME,
            "node_errors": node_errors,
        }

    arxiv_id = paper_meta["arxiv_id"]
    logger.info("[%s] 开始分析: %s", NODE_NAME, arxiv_id)

    try:
        tools = DeepxivTools()
        llm = create_llm(state["llm_config"])

        # 步骤 1：获取章节结构
        structure = _get_structure_safe(tools, arxiv_id, node_errors)
        section_names = _extract_section_names(structure)

        # 步骤 2：渐进式读取并分析各章节
        analysis_parts = {}
        analysis_notes = []
        sections_read = []
        full_paper_cache = None  # 缓存全文，避免重复请求

        for priority_group in SECTION_PRIORITY:
            target = priority_group["target"]
            aliases = priority_group["aliases"]
            extract_fn = priority_group["extract_fn"]

            if llm_call_count >= MAX_NODE_LLM_CALLS:
                logger.warning(
                    "[%s] LLM 调用预算耗尽 (%d/%d)，以当前最佳结果写入状态",
                    NODE_NAME, llm_call_count, MAX_NODE_LLM_CALLS
                )
                analysis_notes.append(
                    f"LLM 调用预算耗尽({llm_call_count}/{MAX_NODE_LLM_CALLS})，"
                    f"未分析: {target}"
                )
                is_degraded = True
                break

            # 降级链尝试读取章节内容
            content = _read_section_with_fallback(
                tools, arxiv_id, target, aliases,
                section_names, full_paper_cache
            )

            if content is None and full_paper_cache is None:
                # 尝试获取全文
                try:
                    full_paper_cache = tools.get_full_paper(arxiv_id)
                    content = full_paper_cache  # 用全文让 LLM 提取
                    analysis_notes.append(f"{target}: 章节读取失败，回退到全文提取")
                    is_degraded = True
                except Exception as e:
                    logger.warning("[%s] 全文获取失败: %s", NODE_NAME, e)
                    analysis_notes.append(
                        f"{target}: 章节和全文均读取失败，标记为缺失"
                    )
                    is_degraded = True
                    continue
            elif content is None and full_paper_cache is not None:
                content = full_paper_cache
                analysis_notes.append(f"{target}: 章节读取失败，回退到全文提取")
                is_degraded = True

            if content is None:
                continue

            # 检查 token 限制
            context_limit = state["llm_config"].get("max_tokens", 4096) * 4
            if not check_context_limit(content, context_limit):
                # 截断到安全长度
                safe_chars = int(context_limit * 0.8 * 3.5)
                content = content[:safe_chars]
                analysis_notes.append(f"{target}: 内容过长，已截断至 {safe_chars} 字符")

            # 调用 LLM 进行结构化分析
            try:
                prompt = _build_analysis_prompt(target, content, paper_meta)
                schema = extract_fn  # 对应的 JSON Schema
                result = call_with_structured_output(llm, prompt, schema)
                llm_call_count += 1
                analysis_parts[target] = result
                sections_read.append(target)
                logger.info("[%s] 成功分析章节: %s", NODE_NAME, target)
            except LLMContextOverflowError:
                # 切换为精简分析模式
                analysis_notes.append(f"{target}: LLM 上下文溢出，跳过该章节")
                is_degraded = True
            except Exception as e:
                logger.warning("[%s] LLM 分析 %s 失败: %s", NODE_NAME, target, e)
                node_errors.append(make_node_error(
                    NODE_NAME, "transient",
                    f"LLM 分析 {target} 失败: {str(e)}",
                    resolved=True
                ))
                analysis_notes.append(f"{target}: LLM 分析失败({str(e)[:50]})")
                is_degraded = True

        # 步骤 3：综合结果填充 PaperAnalysis
        paper_analysis_result = _assemble_paper_analysis(
            analysis_parts, sections_read, analysis_notes, paper_meta
        )

        if is_degraded:
            degraded_nodes.append(NODE_NAME)

        logger.info(
            "[%s] 完成: %d 个章节分析成功, %d 次 LLM 调用, %s",
            NODE_NAME, len(sections_read), llm_call_count,
            "降级" if is_degraded else "正常"
        )
        return {
            "paper_analysis": paper_analysis_result,
            "current_step": NODE_NAME,
            "node_errors": node_errors,
            "degraded_nodes": degraded_nodes,
        }

    except PermanentError as e:
        logger.error("[%s] 永久错误: %s", NODE_NAME, e.message)
        node_errors.append(make_node_error(
            NODE_NAME, "permanent", e.message, e.detail
        ))
        return {
            "error": e.message,
            "current_step": NODE_NAME,
            "node_errors": node_errors,
        }

    except Exception as e:
        logger.error("[%s] 未预期错误: %s", NODE_NAME, str(e), exc_info=True)
        node_errors.append(make_node_error(
            NODE_NAME, "permanent", f"未预期错误: {str(e)}"
        ))
        # 即使异常，也尝试返回部分结果
        return {
            "error": str(e),
            "current_step": NODE_NAME,
            "node_errors": node_errors,
        }
```

#### 2.9.2 渐进式阅读策略

```python
# 章节优先级定义
# 每项包含：目标章节名、常见别名列表、期望提取的 JSON Schema

METHOD_SCHEMA = {
    "type": "object",
    "properties": {
        "method_summary": {"type": "string", "description": "方法概述（2-5 段）"},
        "key_formulas": {"type": "array", "items": {"type": "string"}, "description": "关键公式（LaTeX 格式）"},
        "framework": {"type": "string", "description": "推断的框架: pytorch/tensorflow/jax/other/unknown"},
    },
    "required": ["method_summary", "key_formulas"],
}

EXPERIMENTS_SCHEMA = {
    "type": "object",
    "properties": {
        "datasets": {"type": "array", "items": {"type": "string"}, "description": "使用的数据集名称列表"},
        "metrics": {"type": "array", "items": {"type": "string"}, "description": "评估指标列表"},
        "hyperparams": {"type": "object", "description": "超参数键值对"},
        "hardware_requirements": {"type": "string", "description": "硬件要求描述"},
    },
    "required": ["datasets", "metrics"],
}

RESULTS_SCHEMA = {
    "type": "object",
    "properties": {
        "baseline_results": {"type": "object", "description": "基线实验结果，格式为 {指标名: 数值}"},
    },
    "required": ["baseline_results"],
}

SECTION_PRIORITY = [
    {
        "target": "Method",
        "aliases": [
            "Methodology", "Approach", "Proposed Method", "Our Approach",
            "Our Method", "Framework", "Model", "Architecture",
            "Technical Approach", "Methods",
        ],
        "extract_fn": METHOD_SCHEMA,
    },
    {
        "target": "Experiments",
        "aliases": [
            "Experimental Setup", "Experimental Settings",
            "Experimental Configuration", "Setup", "Implementation Details",
            "Implementation", "Experiment",
        ],
        "extract_fn": EXPERIMENTS_SCHEMA,
    },
    {
        "target": "Results",
        "aliases": [
            "Main Results", "Experimental Results", "Evaluation",
            "Evaluation Results", "Analysis", "Results and Analysis",
            "Results and Discussion",
        ],
        "extract_fn": RESULTS_SCHEMA,
    },
    {
        "target": "Introduction",
        "aliases": [],
        "extract_fn": METHOD_SCHEMA,  # 从 Introduction 中补充 framework 信息
    },
]
```

#### 2.9.3 降级链实现

```python
def _read_section_with_fallback(
    tools: DeepxivTools,
    arxiv_id: str,
    target: str,
    aliases: List[str],
    available_sections: List[str],
    full_paper_cache: Optional[str],
) -> Optional[str]:
    """带降级链的章节读取。

    降级路径：
    1. read_section(target)
    2. 遍历 aliases，逐个尝试 read_section(alias)
    3. 返回 None（调用方将尝试全文提取或标记缺失）

    Args:
        tools: DeepxivTools 实例。
        arxiv_id: 论文 ID。
        target: 目标章节名。
        aliases: 别名列表。
        available_sections: 论文实际章节名列表。
        full_paper_cache: 全文缓存，可选。

    Returns:
        章节内容文本，或 None 表示所有尝试均失败。
    """
    # 尝试 1：精确名称
    try:
        return tools.read_section(arxiv_id, target)
    except Exception as e:
        logger.debug("read_section(%s) 失败: %s", target, e)

    # 尝试 2：别名匹配
    for alias in aliases:
        try:
            return tools.read_section(arxiv_id, alias)
        except Exception as e:
            logger.debug("read_section(%s) 失败: %s", alias, e)

    # 尝试 3：在 available_sections 中做模糊匹配
    target_lower = target.lower()
    for sec_name in available_sections:
        sec_lower = sec_name.lower()
        if target_lower in sec_lower or sec_lower in target_lower:
            try:
                return tools.read_section(arxiv_id, sec_name)
            except Exception as e:
                logger.debug("read_section(%s) 模糊匹配失败: %s", sec_name, e)

    # 所有尝试失败
    logger.warning(
        "章节 '%s' 的所有读取尝试均失败（含 %d 个别名）",
        target, len(aliases)
    )
    return None


def _extract_section_names(structure: Optional[Dict]) -> List[str]:
    """从章节结构中提取所有章节名称。"""
    if not structure:
        return []
    sections = structure.get("sections", [])
    return [
        s["name"] if isinstance(s, dict) else str(s)
        for s in sections
    ]


def _get_structure_safe(
    tools: DeepxivTools,
    arxiv_id: str,
    node_errors: list,
) -> Optional[Dict]:
    """安全获取章节结构，失败不中断。"""
    try:
        return tools.get_paper_structure(arxiv_id)
    except Exception as e:
        logger.warning("[%s] 获取章节结构失败: %s", NODE_NAME, e)
        node_errors.append(make_node_error(
            NODE_NAME, "transient",
            f"获取章节结构失败: {str(e)}",
            resolved=True
        ))
        return None
```

#### 2.9.4 LLM Prompt 设计

```python
def _build_analysis_prompt(
    target: str, content: str, paper_meta: Dict
) -> str:
    """构建章节分析 prompt。

    根据目标章节生成针对性的分析指令。

    Args:
        target: 目标章节名称。
        content: 章节内容文本。
        paper_meta: 论文元数据。

    Returns:
        完整的 prompt 文本。
    """
    title = paper_meta.get("title", "Unknown")
    base = (
        f"你是一个专业的论文分析助手。以下是论文《{title}》的相关内容。\n"
        f"请仔细分析并以 JSON 格式输出结构化分析结果。\n\n"
    )

    section_instructions = {
        "Method": (
            "请从以下内容中提取：\n"
            "1. method_summary: 方法的详细概述（2-5 段，涵盖核心思路、创新点、关键步骤）\n"
            "2. key_formulas: 关键数学公式列表（使用 LaTeX 格式，如 $L = ...$）\n"
            "3. framework: 推断使用的深度学习框架（pytorch/tensorflow/jax/other/unknown）\n"
        ),
        "Experiments": (
            "请从以下内容中提取：\n"
            "1. datasets: 所有使用的数据集名称列表\n"
            "2. metrics: 所有评估指标名称列表\n"
            "3. hyperparams: 关键超参数（学习率、batch_size、优化器、epoch 数等）\n"
            "4. hardware_requirements: 实验使用的硬件描述（GPU 型号、数量、训练时间等）\n"
        ),
        "Results": (
            "请从以下内容中提取：\n"
            "1. baseline_results: 主要实验结果表格中的数值，"
            "格式为 {\"指标名_数据集名\": 数值} 或 {\"表格描述\": {指标: 值}}\n"
        ),
        "Introduction": (
            "请从以下引言内容中补充提取：\n"
            "1. method_summary: 方法概述的补充信息\n"
            "2. framework: 是否提到使用的框架或工具\n"
            "3. key_formulas: 引言中出现的关键公式\n"
        ),
    }

    instruction = section_instructions.get(target, (
        f"请分析以下论文 '{target}' 章节的内容，提取与复现相关的关键信息。"
    ))

    return f"{base}{instruction}\n\n---\n\n{content}"
```

#### 2.9.5 结果组装

```python
def _assemble_paper_analysis(
    parts: Dict[str, Dict],
    sections_read: List[str],
    notes: List[str],
    paper_meta: Dict,
) -> PaperAnalysis:
    """综合各章节分析结果，填充 PaperAnalysis。

    缺失字段按 PRD 规定填充默认值。
    """
    method_part = parts.get("Method", {})
    intro_part = parts.get("Introduction", {})
    exp_part = parts.get("Experiments", {})
    results_part = parts.get("Results", {})

    # method_summary：Method 优先，Introduction 补充
    method_summary = method_part.get("method_summary", "")
    if not method_summary and intro_part:
        method_summary = intro_part.get("method_summary", "")
    if not method_summary:
        method_summary = paper_meta.get("abstract", "")
        notes.append("method_summary: 回退到摘要内容")

    # framework：Method 优先，Introduction 补充
    framework = method_part.get("framework")
    if not framework or framework == "unknown":
        framework = intro_part.get("framework")

    return PaperAnalysis(
        method_summary=method_summary,
        key_formulas=method_part.get("key_formulas", []) + intro_part.get("key_formulas", []),
        datasets=exp_part.get("datasets", []),
        metrics=exp_part.get("metrics", []),
        hyperparams=exp_part.get("hyperparams", {}),
        hardware_requirements=exp_part.get("hardware_requirements", ""),
        framework=framework,
        baseline_results=results_part.get("baseline_results", {}),
        sections_read=sections_read,
        analysis_notes="\n".join(notes) if notes else "分析完成，无异常",
    )
```

#### 2.9.6 预算控制逻辑

- **单节点 LLM 调用上限**：`MAX_NODE_LLM_CALLS = 10`（来自 config.py）。
- 包含 `call_with_structured_output` 内部的重试次数。每次 `call_with_structured_output` 最多消耗 `1 + max_retries = 4` 次 LLM 调用（1 次 structured output + 3 次手动重试），但对外计为 1 次。
- 当 `llm_call_count >= MAX_NODE_LLM_CALLS` 时，停止分析更多章节，以当前最佳结果写入状态。
- 全局预算 `retry_budget_remaining` 的扣减**由节点层负责**（但 Sprint 1 暂不实现全局扣减，仅做单节点计数）。

#### 2.9.7 ReAct Agent 实现设计

**ReAct 核心价值**：

paper_analysis 是 ReAct 收益最大的节点。固定流程函数的降级链（2.9.3 所述）面临以下局限：

1. **非标准章节名识别**：论文的章节命名高度多样化（如 "Our Framework" 代替 "Method"，"Ablation Study" 代替 "Results"），硬编码别名列表无法覆盖所有变体。ReAct agent 可以先获取章节结构，理解每个章节的实际内容主题，自主决定阅读哪些章节。
2. **自主制定阅读策略**：不同类型的论文（理论型、系统型、实验型）的关键信息分布不同。agent 可以根据论文摘要和章节结构动态调整阅读顺序和重点。
3. **降级决策智能化**：从"硬编码降级链"变为"agent 自主决策 + prompt 指导策略"。当某个章节读取失败时，agent 可以自主判断是尝试别名、读取全文、还是跳过该章节。

**可用工具**：

- `get_paper_structure`：获取论文章节结构，制定阅读策略
- `read_section`：按章节名读取论文内容
- `get_full_paper`：获取论文全文（降级兜底）
- `search_papers`：补充搜索相关论文信息

**配置参数**：

- `max_rounds = 12`（需要多轮工具调用来完成渐进式阅读和分析）
- `result_schema`：与 `PaperAnalysis` TypedDict 对齐的 JSON Schema

**system prompt 要点**：

- 角色：深度论文分析专家，专注于提取复现所需的关键技术信息
- 任务：渐进式阅读论文关键章节，提取方法、实验设置、结果等结构化信息
- 策略指导：
  - 先调用 `get_paper_structure` 了解章节结构
  - 按 Method → Experiments → Results → Introduction 优先级阅读
  - 遇到非标准章节名时根据章节结构自主匹配
  - 单个章节读取失败时尝试替代方案，而非直接跳过
  - 全部章节读取失败时调用 `get_full_paper` 兜底
- 输出格式：在 `<result>{...}</result>` 标签中输出符合 PaperAnalysis Schema 的 JSON

**主图节点注册**：

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

**降级处理的变化**：

| 方面 | 旧方案（固定流程函数） | 新方案（ReAct agent） |
|------|---------------------|---------------------|
| 章节名匹配 | 硬编码别名列表 | agent 根据章节结构自主判断 |
| 读取失败处理 | 固定降级链：别名→全文→标记缺失 | agent 自主决策下一步行动 |
| 阅读优先级 | 固定顺序 | agent 根据论文类型动态调整 |
| 分析粒度 | 每章节独立 LLM 调用 | agent 在上下文中综合多章节信息 |
| 预算控制 | 手动计数 `llm_call_count` | `budget_check_node` 自动管理 |

---

### 2.10 `config.py` -- 配置管理

**文件路径**：`config.py`（项目根目录）
**依赖**：仅 Python 标准库 (`pathlib`, `os`)

```python
"""全局配置管理。

集中管理项目路径、默认值和环境变量。
所有硬编码值通过本模块统一管理，其他模块通过 import config 获取。
"""

import os
from pathlib import Path
from typing import Optional


# ========== 路径配置 ==========

PROJECT_ROOT: Path = Path(__file__).parent.resolve()
CHECKPOINT_DB_PATH: Path = PROJECT_ROOT / "checkpoints.db"
WORKSPACE_DIR: Path = PROJECT_ROOT / "workspace"
LOG_DIR: Path = WORKSPACE_DIR / "logs"


# ========== LLM 默认配置 ==========

DEFAULT_LLM_TEMPERATURE: float = 0.3
DEFAULT_LLM_MAX_TOKENS: int = 4096
DEFAULT_LLM_BASE_URL: str = "https://api.openai.com/v1"
DEFAULT_LLM_MODEL: str = "gpt-4o"
LLM_REQUEST_TIMEOUT: int = 60  # 单次 LLM 请求超时秒数


# ========== 重试预算 ==========

MAX_NODE_LLM_CALLS: int = 10       # 单节点 LLM 调用上限（含重试）
MAX_TOTAL_LLM_CALLS: int = 50      # 单任务总 LLM 调用上限
MAX_FIX_LOOP_COUNT: int = 3        # execution-coding 修复循环上限


# ========== LLM 客户端重试配置 ==========

LLM_MAX_RETRIES: int = 3           # LLM 调用最大重试次数
LLM_INITIAL_RETRY_DELAY: float = 2.0  # 起始退避秒数


# ========== 环境变量读取 ==========

def get_deepxiv_token() -> Optional[str]:
    """获取 deepxiv API token。

    读取环境变量 DEEPXIV_TOKEN。
    未设置时返回 None（deepxiv-sdk 支持无 token 使用，有免费额度）。
    """
    return os.environ.get("DEEPXIV_TOKEN")


def get_llm_api_key() -> Optional[str]:
    """获取 LLM API key。

    读取环境变量 LLM_API_KEY。
    未设置时返回 None。
    """
    return os.environ.get("LLM_API_KEY")


def get_llm_base_url() -> str:
    """获取 LLM API base URL。

    读取环境变量 LLM_BASE_URL，未设置时使用默认值。
    """
    return os.environ.get("LLM_BASE_URL", DEFAULT_LLM_BASE_URL)


def get_llm_model() -> str:
    """获取 LLM 模型名称。

    读取环境变量 LLM_MODEL，未设置时使用默认值。
    """
    return os.environ.get("LLM_MODEL", DEFAULT_LLM_MODEL)


# ========== 目录初始化 ==========

def ensure_directories() -> None:
    """确保必要的目录结构存在。"""
    WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
```

**环境变量清单**：

| 环境变量 | 用途 | 默认值 | 是否必需 |
|---------|------|--------|---------|
| `DEEPXIV_TOKEN` | deepxiv API token | `None`（免费额度） | 否 |
| `LLM_API_KEY` | LLM API 密钥 | `None` | 是（运行时必需） |
| `LLM_BASE_URL` | LLM API 基础地址 | `https://api.openai.com/v1` | 否 |
| `LLM_MODEL` | LLM 模型名称 | `gpt-4o` | 否 |

---

### 2.11 `requirements.txt` -- 依赖声明

```
# Auto-Reproduction -- 论文自动复现系统
# Python >= 3.10
# Sprint 1 依赖

# === 核心框架 ===
langgraph>=0.2.0              # Agent 编排框架（含 SqliteSaver）
langchain-openai>=0.1.0       # OpenAI 兼容 LLM 客户端
langchain-core>=0.2.0         # LangChain 核心抽象

# === 论文数据 ===
deepxiv-sdk>=0.2.5            # 论文数据获取（仅使用 Reader 类）

# === 网络 ===
requests>=2.31.0              # HTTP 客户端（deepxiv-sdk 依赖）

# === 数据验证 ===
pydantic>=2.0.0               # 结构化输出 Schema 验证

# === 可选依赖 ===
tiktoken>=0.5.0               # 精确 token 估算（可选，缺失时回退到近似估算）
```

---

## 3. 数据流图

### 3.1 Sprint 1 完整数据流

```
用户输入 arXiv ID
       |
       v
+------+------+
| 初始状态构建   |   user_input = "2409.05591"
| (调用方)      |   llm_config = {...}
+------+------+
       |
       v  graph.invoke(initial_state, config)
       |
+------+------+
| paper_intake |
|              |
|  1. 读取 state["user_input"]
|  2. tools.get_paper_brief(arxiv_id)  --> deepxiv API: /arxiv/?type=brief
|  3. tools.get_paper_head(arxiv_id)   --> deepxiv API: /arxiv/?type=head
|  4. 映射为 PaperMeta
|  5. 学科范围校验
|              |
|  输出: {"paper_meta": PaperMeta, "current_step": "paper_intake"}
+------+------+
       |
       |  [LangGraph 自动 checkpoint 写入 SQLite]
       |
       v
+------+---------+
| paper_analysis  |
|                 |
|  1. 读取 state["paper_meta"]
|  2. tools.get_paper_structure(arxiv_id) --> deepxiv API: /arxiv/?type=head
|  3. 按优先级读取章节:
|     Method -> read_section()      --> deepxiv API: /arxiv/?type=section
|     Experiments -> read_section()  --> deepxiv API: /arxiv/?type=section
|     Results -> read_section()      --> deepxiv API: /arxiv/?type=section
|     Introduction -> read_section() --> deepxiv API: /arxiv/?type=section
|  4. 对每个章节调用 LLM:
|     call_with_structured_output()  --> LLM API (OpenAI 兼容)
|  5. 降级链: read_section 失败 -> 别名 -> get_full_paper() -> 标记缺失
|  6. 综合结果 -> PaperAnalysis
|                 |
|  输出: {"paper_analysis": PaperAnalysis, "current_step": "paper_analysis",
|         "degraded_nodes": [...], "node_errors": [...]}
+------+---------+
       |
       |  [LangGraph 自动 checkpoint 写入 SQLite]
       |
       v
+------+---------+
| resource_scout  |  (占位 pass-through)
+------+---------+
       |
       v
+------+---------+
| planning        |  (占位 pass-through, interrupt 注释掉)
+------+---------+
       |
       v
+------+---------+
| coding          |  (占位 pass-through)
+------+---------+
       |
       v
+------+---------+
| execution       |  (占位 pass-through)
+------+---------+
       |
       v
+------+---------+
| reporting       |  (占位 pass-through)
+------+---------+
       |
       v
     [END]

最终状态包含:
  - paper_meta: PaperMeta (已填充)
  - paper_analysis: PaperAnalysis (已填充)
  - 其余步骤输出字段: None (占位节点未修改)
```

### 3.2 外部服务调用关系

```
paper_intake 节点
  |
  +-- DeepxivTools.get_paper_brief()
  |     --> Reader.brief()
  |           --> GET https://data.rag.ac.cn/arxiv/?type=brief&arxiv_id=...
  |
  +-- DeepxivTools.get_paper_head()
        --> Reader.head()
              --> GET https://data.rag.ac.cn/arxiv/?type=head&arxiv_id=...


paper_analysis 节点
  |
  +-- DeepxivTools.get_paper_structure()
  |     --> Reader.head()
  |           --> GET https://data.rag.ac.cn/arxiv/?type=head&arxiv_id=...
  |
  +-- DeepxivTools.read_section() [多次，每个目标章节]
  |     --> Reader.section()
  |           --> Reader.head() [SDK 内部的章节名匹配]
  |           --> GET https://data.rag.ac.cn/arxiv/?type=section&arxiv_id=...&section=...
  |
  +-- DeepxivTools.get_full_paper() [降级链兜底]
  |     --> Reader.raw()
  |           --> GET https://data.rag.ac.cn/arxiv/?type=raw&arxiv_id=...
  |
  +-- LLM API [每个章节分析一次，含重试]
        --> ChatOpenAI.invoke() 或 .with_structured_output()
              --> POST https://{base_url}/chat/completions
```

---

## 4. 关键设计决策

### 4.1 状态管理方式

| 方案 | 描述 | 优点 | 缺点 |
|------|------|------|------|
| **A. TypedDict（推荐）** | 使用 Python TypedDict 定义 GlobalState | 轻量、LangGraph 原生支持、IDE 友好 | 无运行时类型校验 |
| B. Pydantic BaseModel | 使用 Pydantic 模型定义状态 | 运行时类型校验 | 与 LangGraph TypedDict 模式需要适配 |
| C. Annotated + reducer | 使用 LangGraph Annotated 类型 | 自动 append 语义 | 增加复杂度，Sprint 1 不需要 |

**决策**：方案 A。Sprint 1 保持最简实现，手动管理 List 字段的拷贝。如果后续 Sprint 中 List 管理成为负担，再迁移到 Annotated 方案。

### 4.2 结构化输出方案

| 方案 | 描述 | 优点 | 缺点 |
|------|------|------|------|
| **A. LangChain 原生 + 手动回退（推荐）** | 先试 with_structured_output()，失败则手动 JSON 解析 | 兼容性最好，支持所有 OpenAI 兼容 API | 代码稍长 |
| B. 纯 LangChain 原生 | 仅使用 with_structured_output() | 代码简洁 | 部分模型/API 不支持 |
| C. 纯手动解析 | 所有情况手动解析 JSON | 无外部依赖 | 输出质量不如原生方案 |

**决策**：方案 A。优先利用模型的 function calling 能力获得高质量输出，回退到手动解析确保兼容性。

### 4.3 重试策略实现

| 方案 | 描述 | 优点 | 缺点 |
|------|------|------|------|
| **A. 手写循环（推荐）** | for 循环 + time.sleep | 零额外依赖、精细控制 | 代码量略多 |
| B. tenacity 装饰器 | 使用 tenacity 库 | 代码简洁、功能丰富 | 新增依赖 |
| C. LangChain 内建重试 | ChatOpenAI 的 max_retries 参数 | 零额外代码 | 无法区分异常类型、无法解析 Retry-After |

**决策**：方案 A。Sprint 1 控制依赖数量，手写循环可精确区分不同异常类型的处理逻辑。

### 4.4 SqliteSaver 初始化方式

| 方案 | 描述 | 优点 | 缺点 |
|------|------|------|------|
| **A. 手动 sqlite3.connect + WAL（推荐）** | 先创建连接、配置 WAL 模式，再传入 SqliteSaver | 完全控制 SQLite 配置 | 代码稍长 |
| B. SqliteSaver.from_conn_string | 使用 SqliteSaver 的便捷方法 | 一行代码 | 无法配置 WAL 模式 |

**决策**：方案 A。WAL 模式对并发安全至关重要（Streamlit 轮询 + LangGraph 写入），必须显式配置。

### 4.5 Token 估算方案

| 方案 | 描述 | 优点 | 缺点 |
|------|------|------|------|
| **A. tiktoken 优先 + 字符数回退（推荐）** | 尝试 import tiktoken，失败则用 len/3.5 | 精度和兼容性兼顾 | 需要 try-import |
| B. 仅 tiktoken | 强制要求 tiktoken | 精确 | 增加必需依赖 |
| C. 仅字符数 | len(text) / 4 | 零依赖 | 不够精确 |

**决策**：方案 A。tiktoken 作为可选依赖列入 requirements.txt，估算函数内部做 try-import 回退。

### 4.6 节点实现方式选择 ReAct agent

**决策**：所有节点内部采用 ReAct 循环子图实现（通过 `core/react_base.py` 工厂函数构建）。

**理由**：

1. 固定流程函数无法处理论文的长尾变异（非标准章节名、API 部分失败等），需要 agent 自主决策工具调用顺序和降级策略。
2. ReAct 让 agent 在 reasoning 阶段分析当前信息，自主决定下一步调用哪个工具、传什么参数，而非依赖硬编码的分支逻辑。
3. 通过 `max_rounds` 和 `budget_check_node` 保持可控性，防止 agent 无限循环或过度消耗 LLM 调用预算。
4. deepxiv_sdk 中已有 ReAct 参考实现（`deepxiv_sdk_repo/react_reader.py`），验证了该模式在论文处理场景中的可行性。

**影响**：Sprint 1 新增 `core/react_base.py` 模块（约 4-6 小时工时），后续 Sprint 所有节点（resource_scout、planning、coding、execution、reporting）可直接复用此基础设施，无需重复实现子图构建逻辑。

---

## 5. 测试策略

### 5.1 单元测试覆盖重点

| 模块 | 测试重点 | Mock 对象 |
|------|---------|----------|
| `core/state.py` | TypedDict 可正常实例化、Enum 值正确、create_initial_state() 默认值 | 无需 mock |
| `core/errors.py` | 继承关系正确（isinstance 检查）、make_node_error() 返回值格式 | 无需 mock |
| `core/react_base.py` | ReActState 可正常实例化；create_react_subgraph() 构建的子图拓扑正确（节点数、边连接）；budget_check_node 在 round >= max_rounds-1 时设 status 为 budget_exhausted；正常终止路径（LLM 输出 `<result>` 标签后 finalize 正确解析）；超预算终止路径（force_finish 注入提示后 finalize 完成）；_make_react_wrapper 正确映射 GlobalState <-> ReActState | Mock LLM（返回预设的 AIMessage 序列） |
| `core/llm_client.py` | create_llm() 参数传递、_call_llm_with_retry() 重试逻辑（模拟超时/限流/认证失败）、call_with_structured_output() JSON 解析（正常/markdown 包裹/非法 JSON）、estimate_tokens() 精度 | Mock `ChatOpenAI.invoke()` |
| `core/tools/deepxiv_tools.py` | 异常映射（每种 SDK 异常 -> 系统异常）、空返回值处理、get_paper_brief/read_section 正常路径；ReAct 工具工厂函数返回 BaseTool 实例、工具调用异常时返回错误字符串而非抛异常 | Mock `Reader` 实例 |
| `core/nodes/paper_intake.py` | ReAct wrapper 正确映射 user_input 到 context；agent 可通过工具调用获取 PaperMeta；ID 格式清洗场景；brief 失败后的搜索回退；error 路径 | Mock LLM + Mock DeepxivTools（通过 mock 工具） |
| `core/nodes/paper_analysis.py` | ReAct wrapper 正确映射 paper_meta 到 context；agent 自主制定阅读策略；非标准章节名识别；降级处理（agent 自主决策）；LLM 预算耗尽的优雅终止 | Mock LLM + Mock DeepxivTools（通过 mock 工具） |
| `core/checkpointer.py` | WAL 模式验证、路径不存在时自动创建、路径不可写时报错 | 使用临时文件 |
| `core/graph.py` | 图编译成功、节点数量和边连接正确、占位节点不修改状态 | Mock checkpointer |
| `config.py` | 环境变量覆盖、默认值正确、路径类型为 Path | 使用 os.environ 临时设置 |

### 5.2 端到端测试方案

**验收测试脚本**（对应 PRD AC-1 和 AC-2）：

```python
"""Sprint 1 端到端验收测试。

前置条件：
- 设置环境变量 LLM_API_KEY
- 可选设置 DEEPXIV_TOKEN
- 网络可访问 deepxiv API 和 LLM API
"""

import os
from core.state import create_initial_state, LLMConfig
from core.graph import build_graph
from core.checkpointer import get_checkpointer

def test_e2e_basic_flow():
    """AC-1: 基础流程可执行。"""
    llm_config = LLMConfig(
        base_url=os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1"),
        model=os.environ.get("LLM_MODEL", "gpt-4o"),
        api_key=os.environ["LLM_API_KEY"],
        temperature=0.3,
        max_tokens=4096,
    )
    state = create_initial_state("2409.05591", llm_config)
    graph = build_graph()
    config = {"configurable": {"thread_id": "test-e2e-001"}}
    result = graph.invoke(state, config)

    # 验证 paper_meta
    assert result["paper_meta"] is not None
    assert result["paper_meta"]["arxiv_id"] == "2409.05591"
    assert result["paper_meta"]["title"]
    assert result["paper_meta"]["abstract"]

    # 验证 paper_analysis
    assert result["paper_analysis"] is not None
    assert result["paper_analysis"]["method_summary"]

    # 验证 current_step 已更新
    assert result["current_step"]  # 不再是 "start"


def test_e2e_checkpoint_persistence():
    """AC-2: 状态持久化到 SQLite。"""
    # ... 执行 AC-1 流程 ...
    # 验证 checkpoints.db 存在且大小 > 0
    # 创建新的 graph 实例，使用相同 thread_id，验证可恢复状态
    pass
```

### 5.3 ReAct 子图集成测试

| 测试场景 | 测试目标 | Mock 策略 |
|---------|---------|----------|
| paper_intake ReAct 集成 | 验证 paper_intake ReAct wrapper 端到端流程：从 user_input 到 PaperMeta 输出，包含工具调用和结构化结果解析 | Mock LLM 返回预设的 tool_calls 和 `<result>` 响应；Mock deepxiv 工具返回 fixture 数据 |
| paper_analysis ReAct 集成 | 验证 paper_analysis ReAct wrapper 端到端流程：从 paper_meta 到 PaperAnalysis 输出，包含多轮工具调用（get_structure → read_section x N）和结构化结果解析 | Mock LLM 返回预设的多轮 tool_calls 序列；Mock deepxiv 工具返回 fixture 数据 |
| ReAct 预算耗尽路径 | 验证 max_rounds 耗尽时 force_finish + finalize 的完整路径 | Mock LLM 持续返回 tool_calls（不输出 `<result>`），验证到达 max_rounds 后强制终止并产出结果 |

### 5.4 Mock 策略

**分层 Mock 原则**：

- **单元测试**：每层只 mock 其直接依赖的下一层。
  - 节点层测试：mock 工具层（DeepxivTools / ChatOpenAI）
  - 工具层测试：mock 外部 SDK（Reader）
  - 图层测试：mock 节点函数
- **集成测试**：mock 外部服务（deepxiv API / LLM API），不 mock 内部模块。
- **端到端测试**：不 mock，使用真实 API。

**Mock 数据准备**：

准备以下 fixture 文件（建议放在 `tests/fixtures/` 下）：

- `brief_2409.05591.json`：一篇标准 CS 论文的 brief() 响应
- `head_2409.05591.json`：对应的 head() 响应
- `section_method_2409.05591.txt`：Method 章节内容
- `brief_non_cs.json`：一篇非 CS 论文的 brief() 响应（用于学科校验测试）

---

## 6. 风险与缓解

### 6.1 Sprint 1 特有技术风险

| 编号 | 风险 | 可能性 | 影响 | 缓解方案 | 验证步骤 |
|------|------|--------|------|---------|---------|
| R1 | deepxiv API 不可用或响应缓慢 | 中 | 高 -- paper_intake 和 paper_analysis 完全依赖 | SDK 内建 3 次重试；开发阶段准备 mock 响应数据 | 开发早期即接入真实 API 验证连通性 |
| R2 | LLM 结构化输出解析频繁失败 | 中 | 中 -- paper_analysis 输出质量下降 | 3 次重试 + 错误提示注入；JSON 提取支持多种格式；prompt 模板需充分测试 | 用至少 3 篇不同论文测试 paper_analysis |
| R3 | 论文章节命名不规范导致降级 | 高 | 中 -- 分析结果部分缺失 | 降级链保底（全文提取 -> 标记缺失）；别名列表覆盖常见变体 | 用章节命名不规范的论文做降级链测试 |
| R4 | LangGraph SqliteSaver 序列化 api_key | 中 | 低 -- 安全性问题但不影响功能 | Sprint 1 接受风险，文档告知用户；后续 Sprint 改为从环境变量恢复 | 检查 checkpoints.db 内容确认是否包含 api_key |
| R5 | LLM 上下文窗口溢出 | 中 | 中 -- 论文内容过长无法分析 | 调用前 token 估算 + 主动截断；降级为 brief + 关键章节摘要模式 | 用长篇论文测试截断逻辑 |
| R6 | SDK section() 每次调用内部重复请求 head() | 低 | 低 -- 性能损耗但不影响功能 | Sprint 1 接受开销；后续可考虑在 DeepxivTools 层缓存 head() 结果 | 观察 API 调用次数是否在合理范围 |
| R7 | LangGraph 版本更新导致 API 变化 | 低 | 中 -- 需要修改图构建代码 | requirements.txt 设定最低版本 >=0.2.0；checkpointer 和 graph 封装隔离直接 API | 首次集成时锁定测试通过的版本 |
| R8 | ReAct agent 工具选择不准确导致信息缺失 | 中 | 中 -- paper_analysis 输出质量下降 | system prompt 中提供明确的工具使用策略指导（优先级、降级路径）；max_rounds 留足余量允许 agent 纠错重试；finalize 阶段校验结果完整性，缺失关键字段时记录到 analysis_notes | 用多种类型论文（标准/非标准章节名）测试 agent 的工具调用决策质量 |
| R9 | ReAct 基础设施引入额外开发复杂度 | 中 | 低 -- 增加 Sprint 1 开发工时但不影响功能 | react_base.py 设计为通用基础设施，一次投入后续 Sprint 全部复用；子图与主图状态完全隔离，降低调试难度；提供充分的单元测试覆盖 | 完成 react_base.py 后立即用 paper_intake 验证集成，尽早发现设计缺陷 |

### 6.2 假设清单

| 假设 | 影响范围 | 验证方式 |
|------|---------|---------|
| deepxiv API 免费额度（日 1000 次）在开发阶段足够使用 | 开发效率 | 统计开发阶段日均 API 调用量 |
| LangGraph TypedDict 模式下 List 字段的更新语义为 replace | core/state.py 列表管理 | 编写单元测试验证 |
| 目标 LLM（如 gpt-4o）支持 function calling / tool use | call_with_structured_output 策略 A 能否生效 | 首次集成时测试 with_structured_output |
| SqliteSaver(conn) 接受外部创建的 sqlite3.Connection | checkpointer WAL 方案 | 首次集成时测试 |
| deepxiv SDK 的 section() 内部调用 head() 做章节名匹配 | read_section 的实际 HTTP 请求数 | 阅读 SDK 源码已确认 |

---

**文档结束**

*本文档为 Sprint 1 核心架构设计文档正式版。所有模块的数据结构定义以技术架构文档第 4 章为权威来源，异常层次以第 12.2 节为权威来源。开发者读完本文档后可直接开始编码，无需回头查阅全局架构文档。*

*2026-05-07 更新：架构升级为 ReAct agent 模式——新增 core/react_base.py 通用 ReAct 子图基础设施，paper_intake 和 paper_analysis 从固定流程函数改为 ReAct agent 实现。新增关键设计决策 KD-4.6。*