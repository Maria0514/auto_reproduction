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
| Agent 编排框架 | LangGraph | >= 0.2.0 | Agentic workflow 编排与状态管理（静态 DAG + 节点内 ReAct；coding↔execution 经条件边构成修复循环，非子图） |
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
| `coding` | 步骤5: 编码 | ReAct agent（`_make_react_wrapper` 生成）：依据复现计划与资源信息生成/修复代码文件；接收 execution 经 state 回传的结构化错误反馈进行修复 | ReproductionPlan + ResourceInfo（+ execution 反馈） | 代码文件（`code_output_dir`） |
| `execution` | 步骤6: 执行与验证 | 手写确定性节点（七步管线：准备环境→执行 execution_steps→收产物→指标解析→错误分类→B 档判定→修复循环边界/interrupt#2）；失败且可自动修复时经条件边回退 coding | 代码文件 + ReproductionPlan | ExecutionResult + 错误分类 + `fix_loop_history` |
| `reporting` | 步骤7: 报告生成 | 对比复现结果与论文数据，生成报告 | ExecutionResult + PaperAnalysis | 复现报告 |

### 3.2 编排方式

LangGraph 主图采用**顺序编排**，节点按固定顺序依次执行。注意：paper_intake 之前的论文搜索与用户确认交互在 Streamlit UI 层完成，graph 启动时已持有用户确认的 arXiv ID。

**resource_scout 为全自动节点**：resource_scout 自动完成仓库搜索、质量评分与排序，不设置 interrupt，不涉及用户交互。候选仓库的用户确认合并到 `planning` 节点的 interrupt 审核中——planning 审核页面会展示 resource_scout 输出的候选仓库列表（含质量评分），用户可在审核计划时查看并确认仓库选择，或要求更换仓库。若用户要求更换仓库，planning 节点基于更换后的仓库信息重新生成复现计划。

```
                        [START]
                           |
                           v
                  paper_intake (ReAct)
                           |
                           v
                 paper_analysis (ReAct)
                           |
                           v
                 resource_scout (ReAct)
                           |
                           v
                    planning (手写 + 内嵌 ReAct)
                           |
                     [INTERRUPT#1]  <-- 人在回路：用户审核复现计划（必触发）
                           |
                   (用户确认/修改)
                           |
                           v
                    coding (ReAct)  <───────────┐
                           |                    │ retry_coding（可自动修复 + 未触顶 + 预算够）
                           v                    │
                  execution (手写节点)───────────┘
                           |
                     [INTERRUPT#2]  <-- 仅在修复循环触顶/不可修复时触发（kind="dev_loop_failure"）
                           |
                   (用户三选一决策)
                           |
                           v
                   reporting (纯函数三形态报告)
                           |
                           v
                        [END]
```

> **修复循环说明（当前代码实证）**：coding 与 execution 是主图上两个**独立节点**，经条件边构成修复循环——**没有 dev_loop 子图、没有共享对话历史、没有 coding_only 节点**。`coding`（ReAct wrapper）生成/修复代码后进入 `execution`；`execution`（手写确定性节点）在 venv 沙箱中跑 execution_steps、分类错误、做 B 档成功判定。若失败且错误属可自动修复类（syntax/import/dependency/path/runtime）、`fix_loop_count < MAX_FIX_LOOP_COUNT(10)` 且修复循环子预算未耗尽（`MAX_DEV_LOOP_LLM_CALLS=60`），则经 `_route_after_execution` 的 `retry_coding` 分支回边送回 coding，错误摘要 + 分类经 GlobalState（`execution_result` + `[error_category=...]` 前缀 + `fix_loop_history`）单点写回，coding 下一回合读取并修复。若 10 轮修复触顶、错误不可自动修复或预算耗尽，`execution` 节点触发 interrupt#2（`interrupt_kind="dev_loop_failure"`），等待用户三选一决策（详见 §3.3 / §12.8）。路由函数：`_route_after_coding` / `_route_after_execution`（`core/graph.py`）。

> **Sprint 4 方向（路线丙，已确认）**：coding / execution 升级为**两个松耦合的真 agent**，**7 节点骨架与名称不变、不上 supervisor、不共享 scratchpad**（明确否掉"真 multi-agent 子图"路线甲）。`coding` 补两工具（`run_command` 自验闭环 + `request_user_input`）；`execution` 从确定性七步节点升级为**手写编排 + 内嵌 ReAct 子图的 execution agent**（sandbox 能力 `prepare_venv`/`run_in_venv` 工具化 + 挂 `request_user_input`），修复循环边界 / interrupt#2 / commit 边界 self-loop 保留在薄编排层（与 planning 同范式）。通信沿用现状 state 结构化反馈通道。具体编排安置形态与 ReAct 轮次/修复循环子预算对账为 Sprint 4 架构文档细化项，全局文档不锁定具体方案。详见 `docs/sprint4/prd.md`。

#### 3.2.1 ReAct Agent 架构

主图中 paper_intake / paper_analysis / resource_scout / coding 四个节点统一使用通用 ReAct 子图基础设施 `core/react_base.py` 实现；planning 为手写复合节点内嵌 ReAct 子图 + interrupt#1；execution 为手写确定性节点（Sprint 4 方向将升级为手写编排 + 内嵌 ReAct 子图）；reporting 为纯函数。每个 ReAct 节点内部是一个独立的 ReAct 循环，LLM 通过 LangChain `bind_tools()` 自主决定每一步调用哪个工具、何时输出最终结果。

**ReActState 定义**：

```python
class ReActState(TypedDict):
    messages: Annotated[List[BaseMessage], add_messages]  # 完整对话历史
    round: int                    # 当前推理轮次
    max_rounds: int               # 最大推理轮次上限
    status: str                   # "running" | "finished" | "force_finished"
    result: Optional[Dict]        # 结构化最终输出
    context: Dict[str, Any]       # 从 GlobalState 注入的上下文信息
```

**子图拓扑**：

```
         reasoning_node
              |
              v
           router ──────────────────┐
           /    \                   |
          v      v                  |
 tool_executor  finalize            |
      |             |               |
      v             v               |
  (回到 reasoning)  [END]           |
                                    v
                              force_finish
                                    |
                                    v
                                  [END]
```

- `reasoning_node`：将 messages 传给 LLM（已通过 `bind_tools()` 绑定可用工具），LLM 返回文本推理或工具调用请求
- `router`：检查 LLM 响应——若包含工具调用则路由到 `tool_executor`；若包含 `<result>{JSON}</result>` 标签则路由到 `finalize`；若 `round >= max_rounds` 则路由到 `force_finish`
- `tool_executor`：执行 LLM 请求的工具调用，将结果作为 ToolMessage 追加到 messages，回到 `reasoning_node`
- `finalize`：从 `<result>` 标签中提取 JSON，校验后写入 `result` 字段，状态设为 `"finished"`
- `force_finish`：轮次耗尽时强制结束，尝试从已有对话中提取部分结果，状态设为 `"force_finished"`

**工厂函数**：`_make_react_wrapper(node_name, system_prompt, tools, max_rounds, input_mapper, output_mapper)` 自动处理 GlobalState 与 ReActState 的双向映射，使每个节点只需定义自己的 system prompt、工具列表和映射逻辑。

**各节点配置一览**：

| 节点 | max_rounds | 预期消耗 | 可用工具 |
|------|-----------|---------|---------|
| paper_intake | 5 | 2-3 | get_paper_brief, get_paper_head, search_papers |
| paper_analysis | 12 | 6-10 | get_paper_structure, read_section, get_full_paper, search_papers |
| resource_scout | 10 | 4-7 | web_search, search_papers, get_paper_brief, git_clone_and_analyze, check_url_reachable |
| planning | 8 | 3-5 | read_section, get_paper_structure, web_search, check_url_reachable |
| coding | （见 config） | 视修复轮次 | write_code_file, read_code_file, list_dir, read_section, web_search（Sprint 4 补 run_command + request_user_input） |

> 注：`reporting` 现为纯函数三形态报告（非 ReAct），不在此表；`execution` 现为手写确定性节点（Sprint 4 方向升级为内嵌 ReAct 子图后再纳入本表）。

#### 3.2.2 coding ↔ execution 修复循环（当前代码实证）

系统**没有 dev_loop 子图**，也没有共享对话历史的 multi-agent 区域。编码与执行是主图上两个独立节点，经条件边构成修复循环：

- `coding`（`core/nodes/coding.py`，ReAct wrapper）：依据 `reproduction_plan` + `resource_info` 生成/修复代码，工具集为 write_code_file / read_code_file / list_dir / read_section / web_search。修复回合（`fix_loop_count > 0`）时，`_build_coding_context` 读取上一轮 `execution_result` 与错误分类，注入修复反馈 HumanMessage。产出写回 `code_output_dir`。
- `execution`（`core/nodes/execution.py`，手写确定性节点）：七步管线——准备 venv 环境 → 逐条跑 `execution_steps` → 收产物 → 指标解析（`_parse_metrics` 三档）→ 错误分类（`_classify_execution` / `ErrorCategory`）→ B 档成功判定（`_build_execution_result`：exit 全 0 且 ≥1 指标）→ 修复循环边界 / interrupt#2（`_maybe_interrupt_or_return`）。
- 修复循环边界：错误属可自动修复类（`AUTO_FIXABLE` = syntax / import / dependency / path / runtime）、`fix_loop_count < MAX_FIX_LOOP_COUNT(10)` 且修复循环子预算未耗尽（`MAX_DEV_LOOP_LLM_CALLS = 60`、入口门 `DEV_LOOP_MIN_CALLS_PER_ROUND = 2`）时，经 `_route_after_execution` 的 `retry_coding` 分支回边送回 coding（`fix_loop_count` 单点自增）。否则触发 interrupt#2。
- interrupt#2 重跑幂等：`execution` 用 `_dev_loop_route="await_dev_loop_interrupt"` 标记 + 主图 self-loop 重入（`graph.py`）+ `_has_committed_result_for_round` guard 保证 sandbox 副作用恰为 1。

**通信契约（松耦合，经 GlobalState 单点写回，无 scratchpad）**：`execution` 产出 `execution_result: ExecutionResult` + `ExecutionResult.errors[0]` 的 `[error_category=...]` 前缀 + `fix_loop_history: List[FixLoopRecord]`；coding 侧 `_build_coding_context` 消费。`ErrorCategory` / `ExecutionFeedback` / `AUTO_FIXABLE` 为 `execution.py` 节点本地对象，**不进 `core/state.py`**。

**Sprint 4 方向（路线丙）**：coding / execution 升级为两个松耦合真 agent（见 §3.2 编排方式末尾说明块与 `docs/sprint4/prd.md`）。`execution` 拟采用"手写编排 + 内嵌 ReAct 子图"范式（与 planning 同构），保留本节的修复循环边界 / interrupt#2 / commit 边界 self-loop 于薄编排层；sandbox 能力工具化。具体编排与预算对账留待 Sprint 4 架构文档。

### 3.3 人在回路机制

- **论文确认不属于 graph 内中断**：论文搜索与用户确认属于 graph 启动前的 UI 交互（在 Streamlit 页面中完成），不属于 graph 内的人在回路机制。graph 内当前有两个 interrupt 点：interrupt#1 在 `planning` 节点之后（必定触发，`interrupt_kind="planning"`）；interrupt#2 在 `execution` 节点内（仅在修复循环触顶/不可自动修复时触发，`interrupt_kind="dev_loop_failure"`）。`GraphController.interrupt_kind()` 为只读 helper，按 payload 的 `interrupt_kind` 分发 UI 渲染。**Sprint 4 将新增第三类 `interrupt_kind="user_input_request"`**（见本节末）。
- **resource_scout 不设中断**：resource_scout 为全自动节点，仓库搜索与评分过程不涉及用户交互。候选仓库的用户确认合并到 `planning` 节点的 interrupt 审核中。
- **中断点**：`planning` 节点完成后，使用 LangGraph 的 `interrupt()` 函数暂停图执行
- **用户审核**：Streamlit UI 展示复现计划及候选仓库列表（含质量评分），用户可进行以下操作：
  - **确认**：同意计划及仓库选择，恢复图执行，进入 `coding` 节点
  - **修改**：调整计划内容后重新提交，`planning` 节点根据用户反馈修订计划
  - **更换仓库**：查看候选仓库列表，选择不同的仓库（或要求重新搜索），`planning` 节点基于更换后的仓库信息重新生成计划
  - **切换模式**：选择"只编码不执行"（code_only）时，coding 完成后不进入 execution 直接出报告（当前无独立 `coding_only` 节点）
- **恢复执行**：用户确认后，通过 `graph.invoke(Command(resume=user_feedback))` 恢复图执行
- **execution 失败中断（interrupt#2）**：当修复循环触顶（`fix_loop_count` 达 `MAX_FIX_LOOP_COUNT=10`）、错误不可自动修复或修复循环子预算耗尽时，`execution` 节点触发 `interrupt(interrupt_kind="dev_loop_failure")`，暂停图执行。Streamlit UI 展示失败详情（各轮错误摘要与修复尝试，来自 `fix_loop_history`），用户从以下三个选项中选择：
  - **A. 导出代码包 + 错误诊断报告**：将已生成代码和详细错误诊断打包交付，流程进入 reporting 节点
  - **B. 回退到计划审核**：利用 LangGraph checkpoint 回退到 planning 节点的 interrupt 点，用户修改计划后重新执行
  - **C. 终止任务**：导出当前所有成果（论文分析、资源列表、计划、代码、日志），流程进入 reporting 节点生成终止报告

- **通用用户交互中断（interrupt#3，Sprint 4 方向）**：coding / execution 两 agent 在缺信息（凭证 / 参数 / 决策 / 输入）时，经 `request_user_input` 工具内 `interrupt(payload)` 暂停主图（`interrupt_kind="user_input_request"`）；UI 收集用户输入后经 `Command(resume=值)` 恢复，agent 拿到值继续。凭证认证失败（如 git clone 私有仓库）是首要用例。交互细则：一直暂停不设硬超时（checkpoint 天然保留）；无交互前端时按信息缺失降级；一次问一个信息项（逐条问）。敏感值经 sandbox `extra_env` 注入子进程、不进 state（详见 §7.4 / `docs/sprint4/prd.md`）。

### 3.4 条件路由

在 `planning` 节点的人在回路审核后，以及在 `coding`/`execution` 之间，根据条件进行路由（`core/graph.py`）：

**planning 后**：条件边进入 `coding` 节点（planning 是手写复合节点，interrupt#1 在其内部）。

**coding 后路由（`_route_after_coding`，2 路）**：
- 常规：进入 `execution` 节点执行验证。
- code_only 模式或已达交付：直接进入 `reporting`（当前无独立 `coding_only` 节点，code_only 即"coding 完成后不进入 execution"）。

**execution 后路由（`_route_after_execution`）**：
- `_dev_loop_route == "await_dev_loop_interrupt"` → self-loop 回 `execution`（interrupt#2 的 commit 边界重入）。
- `_dev_loop_route == "retry_coding"`（可自动修复 + `fix_loop_count < 10` + 子预算够）→ 回边送回 `coding`（`fix_loop_count` 自增）。
- 用户三选一后（export_code / terminate）或 B 档成功 / 降级 → 进入 `reporting`；revise_plan → 经 checkpoint 回退到 planning interrupt#1。

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
from typing import TypedDict, Optional, List, Dict, Any, Literal
from enum import Enum


# ========== LLM 配置 ==========

class LLMConfig(TypedDict):
    """LLM 服务配置，支持任何 OpenAI 兼容 API"""
    base_url: str           # API 基础地址，如 "https://api.openai.com/v1"
    model: str              # 模型标识，如 "gpt-4o", "claude-sonnet-4-20250514", "deepseek-chat"
    api_key: str            # API 密钥
    temperature: float      # 生成温度，默认 0.3
    max_tokens: int         # 最大输出 token 数，默认 4096


# Sprint 2 新增：支持节点级 LLM 覆写的 4 个节点名（与 PRD §2.4 / AC-S2-11 强一致）
NodeName = Literal["paper_intake", "paper_analysis", "resource_scout", "planning"]


class LLMConfigSet(TypedDict):
    """多模型 LLM 配置集合（Sprint 2 新增，sprint2/architecture.md §2.1.1.bis）。

    - default: 全局默认配置，**必填**；任何节点未在 overrides 中显式覆写时回退到此条。
    - overrides: 节点级覆写表，key 限定为 4 个支持覆写的节点名（NodeName）。**允许为空 dict**
                  （等同于"单一全局配置"模式，向后兼容 Sprint 1 既有 UX）。
    """
    default: LLMConfig
    overrides: Dict[str, LLMConfig]


# ========== 论文元数据 ==========

class PaperMeta(TypedDict):
    """步骤1输出：论文基础元数据"""
    arxiv_id: str
    title: str                              # 英文主字段，C 双语，主消费方：resource_scout 用英文 title 检索 PwC/GitHub（PRD §4.7.3）
    title_zh: Optional[str]                 # C 双语，主消费方：UI 卡片/详情页中文展示；兜底：LLM 漏写时 title_zh=title + degraded_nodes 标记（PRD §4.7.4）
    authors: List[str]
    abstract: str                           # 英文主字段，C 双语，主消费方：paper_analysis 英文回退（保持术语精度）（PRD §4.7.3）
    abstract_zh: Optional[str]              # C 双语，主消费方：UI 论文详情页中文展示；兜底：LLM 漏写时 abstract_zh=abstract + degraded_nodes 标记
    categories: List[str]
    tldr: Optional[str]                     # 英文主字段（deepxiv 原值），C 双语
    tldr_zh: Optional[str]                  # C 双语，主消费方：UI 卡片高频展示位中文；兜底：LLM 漏写时 tldr_zh=tldr + degraded_nodes 标记
    keywords: Optional[List[str]]
    citation_count: Optional[int]
    github_url: Optional[str]
    publish_date: Optional[str]
    pdf_url: Optional[str]


# ========== 论文分析结果 ==========

class PaperAnalysis(TypedDict):
    """步骤2输出：深度论文分析结果

    注意：自 PRD §4.7 决策（2026-05-17）起，method_summary / hardware_requirements 主语言
    由英文反转为中文（D 中优英备），原英文文本迁移至新增的 *_en 字段。Sprint 2 落地，
    Sprint 1 保持现状不动 prompt。下游开发者请勿沿用旧语义。
    """
    method_summary: str                     # 中文主字段，D 中优英备，主消费方：planning（中文 prompt）+ reporting 报告正文叙述。语义反转自 Sprint 2，参见 PRD §4.7.3
    method_summary_en: Optional[str]        # D 中优英备，主消费方：coding 节点（避免中文喂代码生成造成注释中英混杂）+ 跨语言检索。兜底：LLM 漏写时降级 degraded_nodes
    key_formulas: List[str]
    datasets: List[str]                     # B 英文事实层，主消费方：resource_scout/coding 用英文数据集名匹配开源资源（"ImageNet" 不可译）（PRD §4.7.2）
    metrics: List[str]                      # B 英文事实层，主消费方：reporting 与论文原文 metric 名对齐
    hyperparams: Dict[str, Any]
    hardware_requirements: str              # 中文主字段，D 中优英备，主消费方：UI + planning 双消费，中文友好。语义反转自 Sprint 2，参见 PRD §4.7.3
    hardware_requirements_en: Optional[str] # D 中优英备，英文备份保留；兜底：LLM 漏写时降级 degraded_nodes
    framework: Optional[str]                # B 英文事实层枚举："PyTorch" / "TensorFlow" / "JAX"
    baseline_results: Dict[str, Any]
    sections_read: List[str]                # B 英文事实层，内部审计字段，与论文章节原名对齐
    analysis_notes: str                     # E 混合：中文自由文本 + 英文机器标签（如 [DEGRADED] missing=...），机器标签保留英文语法不变


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
    local_path: Optional[str]       # Sprint 2 新增：git clone 后的本地绝对路径，coding 节点直接使用（PRD §4.1 + sprint2/architecture.md §2.1.1）

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
    external_resources: List[Dict[str, str]]  # 通用外部资源列表，用 type 字段区分类别（dataset/pretrained_model/benchmark 等）
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
    """单轮 coding↔execution 修复循环的记录"""
    round_number: int                # 第几轮（1..MAX_FIX_LOOP_COUNT=10）
    error_summary: str               # 本轮 execution 节点失败的错误摘要
    error_category: str              # 错误分类："syntax" | "import" | "runtime" | "oom" | "timeout" | "other"
    fix_strategy: str                # coding 节点采用的修复策略描述
    timestamp: str                   # ISO 8601 时间戳


# ========== 全局状态 ==========

class ExecutionMode(str, Enum):
    """执行模式"""
    FULL = "full"
    CODE_ONLY = "code_only"

class GlobalState(TypedDict):
    """LangGraph 全局状态，贯穿整个工作流的唯一数据契约。

    Sprint 2 breaking change（sprint2/architecture.md §2.1.1.bis / dev-plan A1+A3）：
        llm_config_set 是多模型权威配置源（default + 节点级 overrides）；过渡期镜像字段
        llm_config 已于 A3 彻底移除——节点级 LLM 路由统一走
        resolve_llm_config(llm_config_set, node_name)（core/llm_client.py），
        不再存在任何 state["llm_config"] 直读路径。
    """

    # --- LLM 配置（Sprint 2 breaking change，见上方 docstring）---
    llm_config_set: LLMConfigSet         # Sprint 2 权威配置源（唯一 LLM 配置入口）

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
    analysis_notes: str                  # 顶层人类可审核备注通道（resource_scout [SEARCH_LOG]/[QUALITY_WARN]、planning [CANCELLED]/[PLANNING_FALLBACK] 经 read-modify-write 累加）；必须声明为通道否则节点写入被 LangGraph 丢弃
    messages: List[Dict[str, str]]

    # --- 错误追踪（§12.3）---
    node_errors: List[NodeError]             # 所有节点的错误历史
    degraded_nodes: List[str]                # 以降级模式完成的节点列表
    retry_budget_remaining: int              # 剩余 LLM 调用预算（默认 50）

    # --- 修复循环追踪（§12.6/12.8）---
    fix_loop_count: int                      # 已完成的 coding↔execution 修复循环轮次（默认 0，上限 MAX_FIX_LOOP_COUNT=10）
    fix_loop_history: List[FixLoopRecord]    # 每轮修复的结构化记录
    user_fix_decision: Optional[str]         # 修复循环失败（interrupt#2）后用户选择："export_code" | "revise_plan" | "terminate"

    # --- 工作目录 ---
    workspace_dir: str

    # --- planning revise 透明计数 + 用户反馈（Sprint 2 新增，sprint2/architecture.md §4.7）---
    # 下划线前缀标识"内部字段，UI 不直接展示原始字段名"；语义仅为透明展示与软提示判定
    # （PLANNING_SOFT_HINT_THRESHOLD=5），**不做硬上限拦截**（PRD §2.3 / Q-S2-03 RESOLVED）。
    _planning_revise_count: int          # 已发生的 revise/switch_repo 次数（默认 0）
    _planning_user_feedback: Optional[str]  # 用户最近一次 revise 反馈文本
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
│   ├── graph.py                  # LangGraph 主图构建、节点注册、条件路由（coding↔execution 修复循环边 _route_after_coding/_route_after_execution）
│   ├── checkpointer.py           # SqliteSaver 初始化与 checkpoint 管理
│   ├── llm_client.py             # OpenAI 兼容 LLM 客户端封装
│   ├── react_base.py             # 通用 ReAct 子图基础设施（ReActState, create_react_subgraph, _make_react_wrapper）
│   ├── nodes/
│   │   ├── __init__.py
│   │   ├── paper_intake.py       # 节点1: 论文输入与解析（ReAct agent）
│   │   ├── paper_analysis.py     # 节点2: 深度论文分析（ReAct agent）
│   │   ├── resource_scout.py     # 节点3: 资源搜集与评估（ReAct agent）
│   │   ├── planning.py           # 节点4: 复现规划（手写复合 + 内嵌 ReAct + interrupt#1）
│   │   ├── coding.py             # 节点5: 编码（ReAct wrapper；Sprint 4 补 run_command + request_user_input）
│   │   ├── execution.py          # 节点6: 执行与验证（手写确定性七步 + interrupt#2；Sprint 4 升级为手写编排 + 内嵌 ReAct）
│   │   └── reporting.py          # 节点7: 报告生成（纯函数三形态报告）
│   └── tools/
│       ├── __init__.py
│       ├── deepxiv_tools.py      # deepxiv Reader 薄封装 + LangChain @tool 工厂函数
│       ├── git_tools.py          # 仓库克隆与本地仓库分析操作（git clone、git log 分析提交活跃度、检查目录结构等）
│       ├── search_tools.py       # URL 可达性检查等搜索辅助工具
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
| `core/graph.py` | 构建 LangGraph 主图，注册 7 个节点与边，管理 coding↔execution 修复循环条件路由 | `build_graph() -> CompiledGraph`, `_route_after_coding`, `_route_after_execution` |
| `core/checkpointer.py` | 管理 SqliteSaver 实例 | `get_checkpointer(db_path) -> SqliteSaver` |
| `core/llm_client.py` | 封装 OpenAI 兼容 API 调用 | `create_llm(config: LLMConfig) -> ChatOpenAI` |
| `core/react_base.py` | 通用 ReAct 子图基础设施，供所有非 dev_loop 节点复用 | `ReActState`, `create_react_subgraph()`, `_make_react_wrapper()` |
| `core/nodes/*` | 各步骤的节点逻辑实现；ReAct 节点通过 `_make_react_wrapper()` 生成 wrapper 接入主图；planning 手写复合 + 内嵌 ReAct + interrupt#1；execution 手写确定性节点 + interrupt#2；reporting 纯函数 | wrapper 函数签名 `def node_fn(state: GlobalState) -> dict` |
| `core/nodes/execution.py` | 执行与验证手写节点：venv 准备/执行/收产物/指标解析/错误分类（`ErrorCategory`）/B 档判定/修复循环边界/interrupt#2 | `execution`, `_classify_execution`, `_maybe_interrupt_or_return`（节点本地） |
| `core/tools/*` | 外部服务调用的工具封装，同时提供 LangChain `@tool` 工厂函数供 ReAct agent 使用 | 各工具函数 + `BaseTool` 工厂函数，与 `bind_tools()` 兼容 |
| `sandbox/local_venv.py` | 本地 venv 沙箱的创建、依赖安装、命令执行（子进程护栏：禁 shell=True / 进程组隔离 / 超时杀子树 / 输出截断 / cwd 限定 WORKSPACE_DIR；`_run_subprocess` / `run_in_venv` 支持 `extra_env` 注入） | `prepare_venv()`, `run_in_venv()`（Sprint 4：`prepare_venv` 补 `extra_env` 形参用于凭证注入） |
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


# ReAct agent 工具工厂函数（返回 LangChain BaseTool 实例）
def get_paper_brief_tool(token: Optional[str] = None) -> BaseTool: ...
def get_paper_head_tool(token: Optional[str] = None) -> BaseTool: ...
def get_paper_structure_tool(token: Optional[str] = None) -> BaseTool: ...
def read_section_tool(token: Optional[str] = None) -> BaseTool: ...
def get_full_paper_tool(token: Optional[str] = None) -> BaseTool: ...
def search_papers_tool(token: Optional[str] = None) -> BaseTool: ...
def web_search_tool(token: Optional[str] = None) -> BaseTool: ...
```

上述工厂函数在内部使用 `DeepxivTools` 类实例，外部通过 LangChain `@tool` 装饰器暴露为 `BaseTool` 实例，与 `ChatOpenAI.bind_tools()` 兼容。每个工厂函数接收 `token` 参数用于初始化底层 `DeepxivTools`，返回的 `BaseTool` 包含完整的函数签名、docstring 和参数描述，供 LLM 在 ReAct 循环中自主选择调用。

---

## 7. 沙箱策略

### 7.1 本地沙箱：venv（v1 实现）

使用 Python 内置的 `venv` 模块为每次复现任务创建独立的虚拟环境，确保依赖隔离。

**核心函数**：

| 函数 | 签名 | 职责 |
|------|------|------|
| `prepare_venv` | `(venv_path: str, requirements, ...) -> str` | 在指定路径创建/复用虚拟环境并安装依赖，返回 Python 解释器路径（Sprint 4：补 `extra_env` 形参用于凭证注入） |
| `run_in_venv` | `(venv_path: str, command: str, timeout: int, extra_env=None) -> ExecutionResult` | 在虚拟环境中执行命令，捕获 stdout/stderr，支持超时控制与 `extra_env` 注入 |

**使用流程（当前代码：execution 手写节点编排）**：

```
1. coding 节点生成 requirements.txt 及代码文件（写入 code_output_dir）
2. execution 节点准备 venv 环境（prepare_venv）
3. execution 节点安装依赖并逐条执行 execution_steps（run_in_venv）
4. 收集产物、解析指标、分类错误、判定成功
5. 失败且可自动修复则经回边送回 coding，否则 interrupt#2
```

> **Sprint 4 方向**：`prepare_venv` / `run_in_venv` 将被包成 execution agent 的 sandbox 工具（`prepare_environment` / `run_in_sandbox`），命令解析/改写等确定性能力保留在工具内部；agent 只决策"跑哪些命令、看结果怎么办、要不要问用户"。

### 7.2 远程沙箱：Docker（v2 实现）

v2 版本将支持 Docker 容器作为远程执行沙箱，提供更强的隔离性和环境一致性。

**预留接口**：

| 函数 | 签名 | 职责 |
|------|------|------|
| `create_container` | `(image: str, gpu: bool) -> str` | 创建 Docker 容器，返回容器 ID |
| `run_in_container` | `(container_id: str, command: str, timeout: int) -> ExecutionResult` | 在容器中执行命令 |
| `cleanup_container` | `(container_id: str) -> None` | 清理并删除容器 |

### 7.3 code_only 模式

当用户选择"只编码不执行"模式时，不需要创建任何沙箱环境。`coding` 节点在工作目录中生成代码文件后不进入 `execution`，直接由 `reporting` 节点生成代码交付报告（当前无独立 `coding_only` 节点，code_only 由路由实现）。

**code_only 模式下的行为**：`coding` 节点应严格按照用户在 interrupt#1 审核中确认的交付标准清单（`ReproductionPlan.deliverables`）进行编码，至少满足最低基准线要求（详见 §3.4 上方 code_only 模式交付标准）。

### 7.4 凭证注入（Sprint 4 方向）

Sprint 4 引入通用用户交互能力后，凭证以环境变量经 sandbox `extra_env` 注入执行子进程（`_run_subprocess` 的 `env = {**os.environ, **(extra_env or {})}`）。现状 `run_in_venv` 已带 `extra_env` 形参，`prepare_venv` **无** `extra_env` 形参——补该形参并透传给内部 pip install 子进程是 Sprint 4 缺口。git 认证失败经 `GIT_TERMINAL_PROMPT=0` 让子进程立即返回而非挂起等 stdin。敏感值**完全不进 GlobalState / checkpoint**，勾"记住"时写入独立 `.secrets`（0600 + gitignore，MVP 不加密）；全链路（生成代码 / 日志 / 报告）脱敏。`.secrets` 具体路径与 `purpose_key → env var 名` 映射待 Sprint 4 架构细化。

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

### 10.3 多模型配置（Sprint 2 落地）

支持为不同步骤配置不同的 LLM，以平衡效果和成本。Sprint 2 起，全局状态以 `LLMConfigSet`（见 §4）作为**权威配置源**承载多模型配置：

- `default`：全局默认配置，必填；
- `overrides`：节点级覆写表，key 限定为 4 个支持覆写的节点名（`NodeName` = paper_intake / paper_analysis / resource_scout / planning）。**允许为空 dict**——等同于"所有步骤共用同一配置"的单模型模式，向后兼容 Sprint 1 既有 UX。

详见 sprint2/architecture.md §2.1.1.bis。

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

`create_llm` 接收**单条** `LLMConfig`。节点如何从 `LLMConfigSet` 解析到单条配置，由 Sprint 2 新增的路由层负责：

```python
def resolve_llm_config(
    llm_config_set: LLMConfigSet,
    node_name: Optional[str],
) -> LLMConfig:
    """节点级 LLM 路由：优先 overrides[node_name]，缺失回退 default。"""
```

ReAct 子图（`core/react_base.py`）统一通过 `create_llm(resolve_llm_config(state["llm_config_set"], node_name))` 获取节点级 LLM，无 `state["llm_config"]` 直读路径（Sprint 2 A3 已移除镜像字段）。

### 10.5 Prompt Cache（KV Cache）友好性约束

> 决策日期：2026-05-13｜决策来源：架构师 Prompt Cache 调研（方案 A：最小改动 / 前缀治理）

#### 10.5.1 背景与目标

ReAct 节点（paper_intake、paper_analysis 等）会在多轮交互中反复发送高度相似的 system prompt 与工具结果上下文，输入 token 占总成本与延迟的主导部分。主流 OpenAI 兼容服务（OpenAI、DeepSeek、Qwen、vLLM 等）已普遍提供**自动型** Prompt Cache：只要请求的前缀字节级一致，服务端会自动命中 KV Cache 并按折扣计费；Anthropic Claude 则采用**显式型** `cache_control` 标记。

本期目标：在不引入 provider 分支、不改变 `create_llm` 签名的前提下，通过"前缀稳定化 + 命中率可观测"两步让默认链路零改动受益于自动型 Prompt Cache；同时为后续接入显式型缓存打底。

#### 10.5.2 设计原则

1. **不在 LLM 客户端做 provider 分支**：兼容性矩阵（§10.2）保持不变，缓存能力由服务端按 base_url 自行决定。
2. **前缀稳定优先**：所有可缓存的内容（system prompt、工具定义、固定 few-shot）放在 message 序列前部；易变量（arxiv_id、paper_meta、user_input、时间戳）放在尾部 HumanMessage 中。
3. **工具结果幂等**：工具返回文本不得携带时间戳、随机 id、临时绝对路径等动态片段。
4. **命中率可观测**：通过响应 metadata（`prompt_tokens_details.cached_tokens`、`cache_creation_input_tokens` 等字段）以 INFO 日志输出，无需新数据表。

#### 10.5.3 厂商兼容性

| 厂商 | 缓存类型 | 当前默认链路是否启用 | 备注 |
|------|---------|------------------|------|
| OpenAI（`gpt-4o` 等） | 自动型，前缀 ≥ 1024 tokens | 是 | 命中后输入按约 50% 折扣 |
| DeepSeek | 自动型 | 是 | 官方文档明示 cache hit / miss 字段 |
| Qwen / vLLM 自部署 | 自动型（vLLM Automatic Prefix Caching） | 是 | 需服务端开启 APC |
| Anthropic Claude（经 NVIDIA 网关，默认 `aws/anthropic/claude-opus-4-6`） | 显式型 `cache_control` | 否（不在本期落地） | 是否经网关透传缓存指标存在不确定性，留待方案 B |

默认链路通过 NVIDIA inference 网关访问 Claude，方案 A 假设网关本身不会改写前缀；前缀稳定改造对所有自动型 provider 立即生效，对 Claude 也为后续显式标记打底。

#### 10.5.4 与 LangGraph Checkpointer 的解耦

LangGraph SqliteSaver 缓存的是 **GlobalState 与 ReActState 快照**（业务状态层），与 LLM 服务端 Prompt Cache（推理层 KV）完全正交：

- Checkpoint 用于中断恢复、人在回路；命中后整段节点跳过执行。
- Prompt Cache 仅在节点真正调用 LLM 时由服务端命中，无 SDK 层介入。
- 两者不互相替代，也不会相互失效。

#### 10.5.5 落地范围

本期落地（详见 Sprint 1 架构文档 §2.6.6 Prompt Cache 友好约束）：

- `core/llm_client.py` 增加只读开关 `LLM_ENABLE_PROMPT_CACHE`（env，默认 True），不改 `create_llm` 签名；响应回包中尝试读取 `cached_tokens` 类字段并 INFO 日志输出。
- `core/react_base.py` 与 `core/nodes/paper_analysis.py` 强制 `SystemMessage(固定) -> HumanMessage(动态)` 顺序，禁止把动态变量插入 system prompt 主体。
- `tool_executor_node` 工具结果截断保持幂等，禁止注入时间戳/随机 id/临时路径。
- `with_structured_output` 推荐使用 JSON mode 而非 function-calling tool 包裹，避免引入不稳定前缀。

不在本期落地（留待方案 B）：

- Anthropic 风格显式 `cache_control` 标记的多块缓存设计。
- 按节点维度的缓存命中率仪表盘（仅日志，不上报指标系统）。
- 跨节点共享 system prompt 模板的版本化注册中心。

---

## 11. 风险与缓解

| 编号 | 风险 | 影响程度 | 可能性 | 缓解策略 |
|------|------|---------|--------|---------|
| R1 | LLM 生成的代码无法正确运行 | 高 | 高 | 引入代码验证步骤（语法检查、import 检查）；coding↔execution 经条件边构成多轮修复循环（上限 MAX_FIX_LOOP_COUNT=10），错误分类驱动"可自动修复则回边、否则 interrupt#2" |
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
|   checkpoint 断点续跑 | coding↔execution 修复循环条件边 | 错误路由 |
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

> **Sprint 4 新增**：`execution.py` 的节点本地 `ErrorCategory` 枚举新增 `CREDENTIAL_REQUIRED`（缺凭证类，如 git clone 私有仓库认证失败）。它归**不可自动修复类**（不进 `AUTO_FIXABLE`、不消耗 `fix_loop_count`）；命中凭证关键字（`could not read username` / `authentication failed` / `terminal prompts disabled` / `permission denied (publickey)` 等）后，execution agent 就地调 `request_user_input` 问用户，取代"缺凭证在修复循环里打转到耗尽"。`ErrorCategory` 为节点本地对象，不写入 `core/errors.py` 的异常层次、也不写入 `NodeError.error_type`。

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

#### paper_analysis 阅读策略

paper_analysis 节点采用 ReAct agent 自主决策阅读策略。system prompt 中包含推荐的渐进式阅读路径（brief → head → 关键章节 → 全文兜底）作为指导，但 agent 可根据论文的实际结构自主调整阅读顺序和策略，能够处理预定义降级链未覆盖的长尾情况（如非标准章节命名、章节缺失、跨章节引用等）。当工具调用失败时，agent 自主选择替代方案（如章节别名匹配、全文提取、标记缺失等），无需硬编码的 if-else 降级链。

#### resource_scout 搜索策略

resource_scout 节点采用 ReAct agent 自主组合搜索策略。system prompt 中包含推荐的搜索优先级（deepxiv github_url → Papers With Code → web search）作为指导，但 agent 可根据前序搜索结果自主调整策略——例如当已找到高质量官方仓库时可跳过后续搜索，当所有渠道均无结果时自动设置 `resource_strategy = "from_scratch"`。agent 通过 `check_url_reachable` 和 `git_clone_and_analyze` 工具自主验证和评估每个候选仓库。

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

#### coding ↔ execution 修复循环

编码与执行的修复循环由主图条件边实现（`core/graph.py` 的 `_route_after_coding` / `_route_after_execution`），**不是** dev_loop 子图、无共享对话历史。coding（ReAct wrapper）生成/修复代码后进入 execution（手写确定性节点）；execution 在 venv 沙箱跑 execution_steps、分类错误、做 B 档成功判定。

**终止/路由条件**（`execution` 节点 `_maybe_interrupt_or_return` + `_route_after_execution` 判断）：
- 执行成功（B 档：exit 全 0 且 ≥1 指标）或降级 → 出 `reporting`。
- 失败且错误属可自动修复类（`AUTO_FIXABLE` = syntax / import / dependency / path / runtime）、`fix_loop_count < MAX_FIX_LOOP_COUNT(10)` 且修复循环子预算未耗尽（`MAX_DEV_LOOP_LLM_CALLS(60)`、入口门 `DEV_LOOP_MIN_CALLS_PER_ROUND(2)`）→ 经 `retry_coding` 回边送回 coding（`fix_loop_count` 单点自增）。
- 触顶 / 不可自动修复 / 子预算耗尽 → 触发 interrupt#2（`interrupt_kind="dev_loop_failure"`），等待用户三选一决策（§12.8）。

coding 修复回合经 GlobalState 获取上下文：上一轮 `execution_result` + `[error_category=...]` 前缀 + `fix_loop_history`（各轮错误摘要与修复策略），由 `_build_coding_context` 注入修复反馈 HumanMessage。

> **Sprint 4 方向**：coding/execution 升级为两松耦合真 agent，execution 采用手写编排 + 内嵌 ReAct 子图，本节修复循环边界 / interrupt#2 / commit 边界 self-loop 保留于薄编排层（见 §3.2 / `docs/sprint4/prd.md`）。

#### Checkpoint 恢复

LangGraph SqliteSaver 在每个节点完成后自动保存 checkpoint，错误恢复场景：

| 场景 | 恢复策略 |
|------|---------|
| 节点中途网络断开 | 从上一个 checkpoint 恢复，整个节点重跑 |
| LLM API 配额用尽 | 暂停到 checkpoint，用户切换 LLM 配置后从当前节点重跑 |
| 代码运行失败 | execution 分类为可自动修复时经回边送回 coding，进入下一轮修复循环 |
| 用户主动取消 | 从当前 checkpoint 恢复，用户可选择重试或跳过 |

恢复时的边界检查：
- 恢复时检查 LLM API 凭证是否仍有效
- execution 恢复时检查沙箱环境完整性（venv 是否被删除）
- 长时间后恢复时 deepxiv API token 可能过期，工具层需重新验证

### 12.7 重试预算总控

为避免无限重试消耗过多 token 和 API 配额，设置全局预算：

| 维度 | 上限 | 说明 |
|------|------|------|
| 单节点 LLM 调用次数（ReAct） | 各节点差异化配置（详见 §3.2.1 各节点 max_rounds 表） | 超出后节点 force_finish，以当前最佳结果写入状态 |
| coding↔execution 修复循环 | MAX_FIX_LOOP_COUNT=10 轮 / 子预算 MAX_DEV_LOOP_LLM_CALLS=60 次 LLM 调用 | 超出后触发 interrupt#2 等待用户决策（对应 `GlobalState.fix_loop_count`） |
| 单任务总 LLM 调用 | 50 次（可配置） | 全局保护，防止成本失控 |

预算对用户不透明、不可调。预算接近耗尽时系统给出 WARNING 级通知；最终报告中记录总修复尝试次数。

> **S2-12 对话调用与预算的关系（架构裁定 2026-06-11，方案 A）**：审核页"对话式改计划"（PRD §2.12）的 LLM 调用发生在 graph 之外的 Streamlit 主线程（planning interrupt 暂停期间），由 UI session 计数器（`_review_chat_calls`）独立计数并在 info-bar 展示「本轮对话已消耗 X 次调用」，**不回写** `retry_budget_remaining` 账本。这是经评估的设计选择，非待修缺口：回写需在主线程引入写路径（`update_state`），破坏 spike S-2 验证的"主线程只读 + worker 独写 + 每线程独立 SqliteSaver"线程安全模型，并在 interrupt 暂停态有 checkpoint 一致性 / 恢复点漂移风险；收益（精确化一个对用户本就不透明、不可调的内部字段）不抵风险。PRD §2.12「对话调用计入 `MAX_TOTAL_LLM_CALLS` 总预算」为**产品认知/软提示语义**——对话轮次与 revise 同质，**共用同一条 `PLANNING_SOFT_HINT_THRESHOLD`（N≥5）软提示线**（不用 `MAX_TOTAL_LLM_CALLS` 比例阈值：预算是 ReAct round 口径、对话是次数口径，量纲不同且长对话上下文早膨胀使比例线永不触发）。**演进口**：若未来需把对话成本纳入统一硬控上限，按 C 方案（resume 时由 planning 节点合并扣减、仍在 worker 线程写）演进，本期不做。

### 12.8 修复循环失败（interrupt#2）后的用户决策

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
| planning | LLM 超时/限流 | LLM 不可用 | 用户拒绝不设硬上限（理论上用户不满意就要一直修改），由总 LLM 预算 `MAX_TOTAL_LLM_CALLS = 50` 自然兜底；UI 在 N ≥ 5 时软提示切 code_only 但不锁按钮；提供"终止当前任务"按钮作为主动出口（详见 Sprint 2 PRD AC-S2-06 / AC-S2-13） |
| coding | LLM 输出格式错误、git clone 失败 | 磁盘满/权限问题 | 经 state 接收 execution 错误反馈，修复回合重新生成代码 |
| execution | pip install 部分失败 | venv 创建失败、OOM、超时 | 错误分类驱动 coding↔execution 修复循环（上限 10 轮），不可修复则 interrupt#2 |
| reporting | LLM 超时/限流 | 文件写入失败 | 降级为结构化模板报告 |

### 12.10 沙箱错误处理

#### venv 创建阶段

```
prepare_venv() 失败
  → 检查 Python 版本是否满足要求
  → 检查磁盘空间
  → 尝试 --clear 参数重建
  → 仍失败: 致命错误，通知用户检查系统 Python 环境
```

#### 依赖安装阶段

```
依赖安装部分失败
  → 逐包安装（非批量安装）
  → 单包失败时: 尝试降级版本 → 无版本约束安装 → 标记失败
  → 安装完成后检查所有 import 是否可用
  → 将失败包列表经 state 反馈给 coding 节点寻找替代方案
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
| 通用 ReAct 子图基础设施 | `core/react_base.py` | ReActState 定义、create_react_subgraph()、_make_react_wrapper() 工厂函数 |
| 构建 LangGraph 主图骨架 | `core/graph.py` | 7 节点注册、顺序边 + coding↔execution 修复循环条件边、interrupt 占位 |
| SqliteSaver 初始化 | `core/checkpointer.py` | checkpoint 管理基础设施 |
| LLM 客户端封装 | `core/llm_client.py` | OpenAI 兼容 API 调用封装，含指数退避重试、structured output、token 估算 |
| deepxiv_tools 封装 | `core/tools/deepxiv_tools.py` | Reader 薄封装 + LangChain @tool 工厂函数 |
| paper_intake 节点 | `core/nodes/paper_intake.py` | 论文输入与解析（ReAct agent，max_rounds=5） |
| paper_analysis 节点 | `core/nodes/paper_analysis.py` | 深度论文分析（ReAct agent，max_rounds=12） |
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
| 本地 venv 沙箱 | `sandbox/local_venv.py` | prepare_venv, run_in_venv（含子进程护栏与 extra_env 注入） |
| shell_tools 封装 | `core/tools/shell_tools.py` | Shell 命令执行工具 |
| file_tools 封装 | `core/tools/file_tools.py` | 文件读写工具 |
| coding 节点 | `core/nodes/coding.py` | 编码 ReAct agent（write_code_file / read_code_file / list_dir / read_section / web_search） |
| execution 节点 | `core/nodes/execution.py` | 执行与验证手写节点：venv 准备/执行/收产物/指标解析/错误分类/B 档判定/修复循环边界/interrupt#2 |
| reporting 节点 | `core/nodes/reporting.py` | 结果对比与报告生成（纯函数三形态报告） |
| Streamlit 页面 4 | `ui/pages/execution_monitor.py` | 执行监控页面 |
| Streamlit 页面 5 | `ui/pages/report_view.py` | 结果报告页面 |
| 条件路由实现 | `core/graph.py` 更新 | `_route_after_coding`（coding→execution / code_only→reporting）+ `_route_after_execution`（retry_coding 回边 / self-loop / 出 reporting） |
| execution 失败用户选项 | `ui/pages/execution_monitor.py` 更新 | 修复循环触顶（interrupt#2）后的 A/B/C 三选项 UI（见 §12.8） |
| 错误信息展示组件 | `ui/components/error_display.py` | 一句话摘要 + 可展开详情 + 完整日志链接 |
| 各节点降级逻辑 | `core/nodes/*.py` | ReAct agent 自主决策阅读/搜索策略、execution 沙箱错误检测分类与修复循环边界 |

**验收标准**：端到端完成一次完整复现（输入 arXiv ID -> 分析 -> 资源搜集 -> 计划审核 -> coding↔execution 编码执行修复循环 -> 报告），所有 Streamlit 页面可正常使用，修复循环与 interrupt#2 三选项决策正常工作。

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
*2026-05-07 更新：架构升级为 ReAct agent 自主模式——所有节点内部从"单次 LLM 调用"改为 ReAct 循环子图，能自主选择工具和多轮推理；coding + execution 合并为 dev_loop 双 agent 协作子图，通过共享对话历史实现真正的 multi-agent 交互。新增 §3.2.1 ReAct Agent 架构、§3.2.2 dev_loop 双 Agent 协作子图。*
*2026-05-24 更新：架构定位校准——明确本系统是"基于 LangGraph 的 agentic workflow"（流水线骨架 + 节点内 ReAct agent），而非完整的 multi-agent 系统；真 multi-agent 交互仅存在于 dev_loop 子图（coding ↔ execution）。Sprint 3 计划将 dev_loop 升级为 Coder / Executor / Reviewer 三 agent supervisor 模式，详见 docs/TODO.md "未来计划" 区段。*
*2026-07-02 更新（重大校准）：使文档与 Sprint 3 已落地代码及 Sprint 4 已确认方向（路线丙）对齐。（1）删除全文对 `dev_loop` 子图 / `coding_agent`+`execution_agent` 共享对话历史 multi-agent / `coding_only` 节点的描述——这些从未落地，且路线甲已被 Sprint 4 明确否掉。（2）修正为当前真实形态：主图 7 个独立节点，coding（ReAct wrapper）↔ execution（手写确定性七步节点 + interrupt#2）经条件边（`_route_after_coding` / `_route_after_execution`）构成修复循环，上限 `MAX_FIX_LOOP_COUNT=10`（原文档误写 5 轮）+ 子预算 `MAX_DEV_LOOP_LLM_CALLS=60`。（3）interrupt 点纠正为两个：interrupt#1（planning 后，`kind="planning"`）、interrupt#2（execution 失败，`kind="dev_loop_failure"`）。（4）叠加 Sprint 4 路线丙方向：coding/execution 升级为两个松耦合真 agent（7 节点骨架不变、不上 supervisor、不共享 scratchpad），coding 补 run_command + request_user_input，execution 升级为手写编排 + 内嵌 ReAct 子图的 execution agent + sandbox 工具化；新增第三类 interrupt#3（`kind="user_input_request"`）通用交互能力；新增 `ErrorCategory.CREDENTIAL_REQUIRED`；凭证经 sandbox `extra_env` 注入、敏感值不进 state（§7.4）。Q-A1/Q-A2（execution ReAct 化编排安置与预算对账）、Q-B1（run_command venv/超时）、Q-E1（.secrets 路径）等具体方案留待 Sprint 4 架构文档细化，本全局文档只写到方向层。依据 docs/sprint4/prd.md。*
