# Sprint 2 产品需求文档 (PRD)

**产品名称**：Auto-Reproduction —— 论文自动复现系统
**Sprint**：Sprint 2 —— 核心链路
**版本**：v1.0
**日期**：2026-05-18
**作者**：产品经理代理
**状态**：正式版

---

## 目录

1. [Sprint 2 概述](#1-sprint-2-概述)
2. [功能需求](#2-功能需求)
3. [非功能需求](#3-非功能需求)
4. [数据结构定义](#4-数据结构定义)
5. [接口定义](#5-接口定义)
6. [验收标准](#6-验收标准)
7. [依赖与风险](#7-依赖与风险)
8. [开放问题](#8-开放问题)

---

## 1. Sprint 2 概述

### 1.1 Sprint 目标

Sprint 2 的目标是在 Sprint 1 基础骨架之上**打通从论文输入到计划审核的完整链路**，包括：

- 实现七步流程中的步骤 3（资源搜集与评估）与步骤 4（复现规划与人在回路审核）两个核心节点；
- 实现 git 仓库克隆与本地仓库质量分析的工具层；
- 实现 Streamlit Web UI 的前 3 个核心页面（论文输入、分析进度、计划审核）以及 LLM 配置表单组件；
- 实现 Streamlit 主线程与 LangGraph 工作线程的异步通信框架（基于轮询 + SqliteSaver 共享状态）；
- 完成 PRD §4.7（输出语言策略）相关字段扩展与 prompt 调整，确保 UI 友好的中文展示不破坏 Sprint 1 已落地的 Prompt Cache 字节级幂等约束。

Sprint 2 完成后，**真实用户**应能够通过浏览器打开 Streamlit 界面，输入 arXiv ID 启动复现任务，观察论文分析与资源评估进度，并在计划审核页面通过 interrupt 人在回路确认或修改复现计划。后续 coding / execution / reporting 节点保留 Sprint 1 的 pass-through 占位行为。

### 1.2 范围边界

**Sprint 2 包含**：

| 编号 | 模块 | 产出文件 |
|------|------|---------|
| S2-01 | git 工具封装 | `core/tools/git_tools.py` |
| S2-02 | resource_scout 节点 | `core/nodes/resource_scout.py` |
| S2-03 | planning 节点 + interrupt 人在回路 | `core/nodes/planning.py` |
| S2-04 | LLM 配置表单组件 | `ui/components/llm_config_form.py` |
| S2-05 | Streamlit 页面 1：论文输入 | `ui/pages/paper_input.py` |
| S2-06 | Streamlit 页面 2：分析进度 | `ui/pages/analysis_progress.py` |
| S2-07 | Streamlit 页面 3：计划审核 | `ui/pages/plan_review.py` |
| S2-08 | Streamlit 应用入口 + 工作线程 + 轮询 | `app.py` |
| S2-09 | PaperMeta / PaperAnalysis 新增字段落地 | `core/state.py` + 两节点的 schema 同步 |
| S2-10 | paper_intake / paper_analysis 输出语言策略 prompt 扩展 | `core/nodes/paper_intake.py` + `core/nodes/paper_analysis.py`（**仅 HumanMessage 通道**） |
| S2-11 | graph.py planning 节点 interrupt 真实落地与条件路由占位（不实现 code_only / dev_loop 路由分支） | `core/graph.py` |

**Sprint 2 不包含**：

- `coding` / `execution` / `reporting` 节点的业务实现（保留 Sprint 1 pass-through 占位行为）；
- `dev_loop` 子图与 `coding_only` 节点的实现；
- `core/tools/shell_tools.py` / `core/tools/file_tools.py` / `sandbox/local_venv.py`；
- code_only 模式的执行路径与对应的"交付标准 UI"交互（详见 §8 Q-S2-04 待定）；
- execution↔coding 修复循环及失败后的 A/B/C 三选项 UI；
- Streamlit 页面 4（执行监控）与页面 5（结果报告）；
- CLI 入口；
- Papers With Code API 的具体接入策略（封装接口 + mock 是 Sprint 2 范围，但是否依赖 PwC 官方 SDK / 限速策略 / 失败兜底等取决于架构师评估，详见 §8 Q-S2-02）；
- GitHub API（star / fork / issue 等元数据）—— 仍按 product-design-specification §4.2.2 决策，**MVP 不引入**，归 v2.0。

### 1.3 与整体产品的关系

```
七步流程与 Sprint 对应关系：

步骤 1: 论文输入与解析 (paper_intake)        <-- Sprint 1 已实现
步骤 2: 深度论文分析 (paper_analysis)         <-- Sprint 1 已实现
步骤 3: 资源搜集与评估 (resource_scout)       <-- Sprint 2 实现
步骤 4: 复现规划与用户审核 (planning)         <-- Sprint 2 实现（含 interrupt 人在回路）
步骤 5: 编码与环境搭建 (coding / dev_loop)    --> Sprint 3
步骤 6: 执行与测试验证 (execution)            --> Sprint 3
步骤 7: 报告生成 (reporting)                  --> Sprint 3
```

Sprint 2 对应技术架构文档（`docs/technical-architecture.md`）§13 的"阶段 2：核心链路"。

### 1.4 目标用户

Sprint 2 首次面向**最终用户**（CS 在校学生与科研工作者，详见产品设计说明书 §2）。本 Sprint 是第一个具备完整可视化交互的 Sprint，用户在 Sprint 2 之后即可在本地通过浏览器使用系统的"论文 → 分析 → 规划"半成品流程，对 UI/UX 的可用性、错误信息可读性、人在回路交互流畅度有真实感知。

---

## 2. 功能需求

### 2.1 S2-01: git 工具封装 (`core/tools/git_tools.py`)

**目的**：提供仓库克隆与本地仓库分析能力，作为 resource_scout 节点评估候选仓库质量的工具层基础设施。**MVP 不引入 GitHub API**——本模块的所有指标均通过本地 `git` 命令行 + 目录扫描完成。

**详细要求**：

- 提供 `git_clone(url: str, dest_dir: str, depth: int = 1, timeout: int = 60) -> Dict` 函数：
  - 通过子进程调用系统 `git` 命令实现浅克隆（`--depth 1`）以加速；
  - 返回结构化字典：`{"success": bool, "local_path": str, "duration_seconds": float, "error": Optional[str]}`；
  - 失败时根据 stderr 区分网络瞬态错误（`TransientError`）与永久错误（无效 URL、认证失败、磁盘空间不足）抛出 `PermanentError`；
  - 工具层重试策略遵循技术架构文档 §12.4：3 次指数退避（1s / 2s / 4s）。
- 提供 `analyze_local_repo(local_path: str) -> RepoInfo` 函数：
  - 通过 `git log` 提取 `last_commit_date`（ISO 8601）与 `commit_count_recent`（近 6 个月）；
  - 扫描顶层目录，判定 `has_readme` / `has_requirements`（含 `requirements.txt` / `environment.yml` / `pyproject.toml` 任一即视为 True）；
  - 返回顶层目录结构 `dir_structure: List[str]`（仅一级，最多 30 项，按字典序）；
  - `quality_score` 字段由 resource_scout 节点根据多个候选仓库的相对评分汇总后写入，本工具仅产出原始指标。
- 提供 `check_url_reachable(url: str, timeout: int = 5) -> bool` 函数（用于 resource_scout 在 clone 前快速过滤死链）。
- 所有函数同时以 `@tool` 工厂函数形式导出，供 ReAct agent 调用（参考 `deepxiv_tools.py` 的 7 个工具工厂模式）。
- **工具序列化必须输出合法 JSON**（吸取 BUG-S1-02 教训），使用 `json.dumps(..., ensure_ascii=False, sort_keys=True, default=str)`。

**输入**：仓库 URL、目标目录、超时时间等。

**输出**：结构化字典或 `RepoInfo`。

**边界条件**：

- 同一 URL 在同一任务内重复克隆请求时，应识别已存在的本地路径并跳过重复克隆（避免浪费时间和磁盘）；
- 克隆完成后本地目录应保留在 `workspace_dir/repos/{repo_slug}` 下，由调度方决定何时清理；
- 工具调用的所有副作用（disk write）必须严格限定在 `workspace_dir` 范围内，不允许写其它路径。

**依赖关系**：依赖 `core/errors.py` + `config.py`（`workspace_dir`）。被 `core/nodes/resource_scout.py` 依赖。

---

### 2.2 S2-02: resource_scout 节点 (`core/nodes/resource_scout.py`)

**目的**：自动完成论文相关代码仓库的搜索、克隆与质量评分，输出候选仓库列表与推荐选择。**resource_scout 全自动运行，不设独立 interrupt**——候选仓库列表合并到 planning 审核页面统一展示（决策已固化，详见 product-design-specification §3.2 步骤 3 / §4.2.2、technical-architecture §3.2 / §3.3）。

**详细要求**：

**节点函数签名**：通过 `_make_react_wrapper()` 工厂函数生成 callable，签名 `def resource_scout(state: GlobalState) -> dict`。

**ReAct 配置**：

| 配置项 | 值 | 说明 |
|---|---|---|
| `max_rounds` | 10 | 与技术架构 §3.2.1 各节点配置表一致 |
| 可用工具 | `web_search` / `search_papers` / `get_paper_brief` / `git_clone_and_analyze` / `check_url_reachable` | `git_clone_and_analyze` 为 S2-01 提供的复合工具工厂（内部封装 `git_clone` + `analyze_local_repo`） |
| system prompt 主体 | 固定模板 `_RESOURCE_SCOUT_SYSTEM_PROMPT_BODY` | Prompt Cache 字节级幂等约束 |
| 动态上下文 | 通过 HumanMessage 传入 `paper_meta` + `paper_analysis` 关键字段（**英文事实层字段优先**，遵循 PRD §4.7.5：title / datasets / keywords 用于检索） |

**搜索优先级链**（system prompt 中作为推荐策略，但允许 agent 自主调整顺序，参考 technical-architecture §12.5）：

1. **deepxiv `github_url`**：若 `paper_meta.github_url` 非空，先用此 URL 做可达性检查并直接克隆评估；
2. **Papers With Code API**：通过论文 title（英文主字段）或 arXiv ID 查询关联仓库；
3. **Web Search**：用 title + framework + "code"/"github" 等关键词做兜底搜索；
4. **从零编码兜底**：以上全部失败时，设置 `resource_info.resource_strategy = "from_scratch"` 并将候选列表写为空。

**仓库质量评估与排序**：

- 每个候选仓库克隆后通过 `analyze_local_repo` 取得原始指标，agent 综合以下维度给出 `quality_score`（0.0~1.0 浮点数）：
  - `is_official`（仓库 owner 与论文作者匹配，权重最高）；
  - `last_commit_date`（最近半年内提交加分）；
  - `commit_count_recent`（近 6 个月提交活跃度）；
  - `has_readme` / `has_requirements`（基本完整性）；
  - `dir_structure` 匹配标准 ML 项目模式（含 `src/` 或 `models/` 或 `train.py` 等）。
- 节点最终输出 `ResourceInfo`：
  - `repos`：按 `quality_score` 降序排列的全部候选；
  - `selected_repo`：默认取 `repos[0]`（用户可在 planning 审核时更换）；
  - `resource_strategy`：`"use_repo"` / `"hybrid"` / `"from_scratch"`。

**降级策略**（详见 technical-architecture §12.5 / §12.9）：

| 失败场景 | 节点行为 |
|---|---|
| deepxiv `github_url` 为空 | 跳过此源，继续 PwC + web search |
| PwC API 限流 / 超时 | 工具层重试 3 次后向上抛出 `TransientError`；ReAct agent 选择跳过并继续 web search |
| 候选仓库全部克隆失败 | 写入 `resource_strategy = "from_scratch"`，将节点加入 `degraded_nodes`，记录 `NodeError(error_type="degraded")` |
| 工具调用预算耗尽 | force_finish 路径输出当前最佳候选列表（即使为空） |

**错误处理**：遵循节点函数统一错误处理模板（technical-architecture §12.5）；BUG-S1-02 / BUG-S1-03 治理经验全部沿用（工具 JSON 序列化 + 节点层 backfill 兜底 + WARNING 日志非静默吞错）。

**输入**：`GlobalState`，其中 `paper_meta` 与 `paper_analysis` 已由 Sprint 1 节点填充。

**输出**：状态更新字典，至少包含 `resource_info` / `current_step` / 必要时的 `node_errors` / `degraded_nodes` 字段。

**边界条件**：

- 当所有候选仓库 `quality_score` 均低于 0.3 时，节点应在 `analysis_notes` 中记录 WARNING 并仍照常推荐 `repos[0]`（用户在 planning 审核时可决策是否切换 `from_scratch`）；
- `resource_info.external_resources`（数据集 / 预训练模型）在 Sprint 2 仅做字段保留，**不强制填充**，由 ReAct agent 视情况补充；
- 候选仓库的本地路径（`workspace_dir/repos/{repo_slug}`）必须写入 `RepoInfo` 中（**新增字段，详见 §4.1**），供 Sprint 3 的 coding 节点直接使用。

**依赖关系**：依赖 `core/state.py` / `core/react_base.py` / `core/llm_client.py` / `core/tools/git_tools.py` / `core/tools/deepxiv_tools.py`。被 `core/graph.py` 注册调用。

---

### 2.3 S2-03: planning 节点 + interrupt 人在回路 (`core/nodes/planning.py`)

**目的**：综合论文分析结果与资源信息，生成结构化的复现计划，并通过 LangGraph `interrupt()` 暂停流程交由用户审核。是 Sprint 2 整条链路的**唯一显式人在回路点**。

**详细要求**：

**节点函数签名**：`def planning(state: GlobalState) -> dict`，由 `_make_react_wrapper()` 工厂函数生成。

**ReAct 配置**：

| 配置项 | 值 |
|---|---|
| `max_rounds` | 8 |
| 可用工具 | `read_section` / `get_paper_structure` / `web_search` / `check_url_reachable` |
| system prompt 主体 | 固定模板 `_PLANNING_SYSTEM_PROMPT_BODY` |
| 动态上下文 | HumanMessage 传入 `paper_meta` / `paper_analysis` / `resource_info` |

**复现计划生成内容**（对齐 product-design-specification §4.3.1）：

- `plan_summary`：中文叙述的计划摘要；
- `environment`：硬件/软件要求（中文友好，引用 `hardware_requirements` 中文主字段）；
- `data_preparation`：数据集获取与预处理步骤列表（数据集名保留英文，与 PRD §4.7.5 协作约束一致）；
- `code_strategy`：基于 `resource_info.selected_repo` 的代码方案（"use_repo + 适配" / "from_scratch"）；
- `execution_steps`：按顺序排列的执行命令/脚本（每项含 `step_name` / `command` / `expected_output`）；
- `expected_results`：引用 `paper_analysis.baseline_results`；
- `estimated_time`：中文时间预估；
- `deliverables`：交付物清单。**在 Sprint 2 中，无论 `execution_mode` 是 `FULL` 还是 `CODE_ONLY`，planning 节点都必须基于最低基准线草拟 `deliverables`**（详见 product-design-specification §4.4.3 与 technical-architecture §3.4）；UI 层 code_only 模式的交付标准编辑入口归属待定（详见 §8 Q-S2-04），但字段必须填好。

**interrupt 人在回路审核**：

- planning 节点在 ReAct 子图完成、计划写入状态后，调用 LangGraph `interrupt()`，传入一个**结构化审核 payload**：
  ```python
  {
    "reproduction_plan": <ReproductionPlan>,
    "resource_info": <ResourceInfo>,   # 含候选仓库列表 + selected_repo
    "paper_analysis_summary": {...},   # 仅含 UI 展示需要的字段子集
    "degraded_nodes": [...],            # 上游降级节点，用于 UI 信息完整度提示
    "node_errors": [...]                # 最近的错误摘要（最多 5 条）
  }
  ```
- 用户在 Streamlit 计划审核页面（S2-07）完成以下任一操作后，通过 `Command(resume=...)` 恢复 graph 执行：

  | 用户决策 | resume payload | 节点后续行为 |
  |---|---|---|
  | **确认计划** | `{"decision": "approve"}` | 节点写入 `reproduction_plan.approved = True`，graph 进入下游节点（Sprint 2 即占位 coding 透传） |
  | **要求修改计划**（自由文本反馈） | `{"decision": "revise", "user_feedback": "..."}` | 节点把 user_feedback 注入 HumanMessage，重新跑一次 ReAct 子图（**可无限次触发**，由用户决定何时确认 / 切换 code_only / 终止任务；token 消耗由 `MAX_TOTAL_LLM_CALLS = 50` 总预算兜底）|
  | **更换仓库** | `{"decision": "switch_repo", "new_repo_url": "...", "user_feedback": "..."}` | 节点把新仓库写入 `resource_info.selected_repo`，重新生成计划（与 revise 同等无次数上限） |
  | **切换 code_only 模式** | `{"decision": "code_only"}` | 节点写入 `execution_mode = ExecutionMode.CODE_ONLY`；交付标准的 UI 编辑入口待定（§8 Q-S2-04） |
  | **终止当前任务** | `{"decision": "cancel"}` | 节点写入 `current_step = "cancelled_by_user"`，工作线程在当前 step 结束后退出；SQLite checkpoint 保留以便后续查看 |

- **修改计划无次数上限**：revise / switch_repo 决策可被用户无限次触发，尊重用户产品掌控权（理论上用户不满意就应继续修改）；UI 在 N ≥ 5 次时展示软提示"是否切换 code_only 模式以更快推进？"——**仅提示不锁按钮**（详见 §2.7 AC-S2-06）；任务级兜底依赖 `MAX_TOTAL_LLM_CALLS = 50` 总预算（technical-architecture §12.7），超过总预算时由 `core/react_base.py` 的 budget_check 路径自然 force_finish 当前 ReAct 子图；用户也可随时点击"终止当前任务"按钮主动退出（详见 AC-S2-13）。Q-S2-03 已于 2026-05-18 由 Maria 决策 RESOLVED（取消硬上限）。

**错误处理**：

- ReAct 子图自身失败（LLM 不可用等）：写入 `node_errors`，将节点加入 `degraded_nodes`，仍尝试输出最简版 `reproduction_plan`（仅 `plan_summary + code_strategy` 两字段），避免 interrupt 时无内容可审；
- `resource_info` 为空或 `selected_repo = None`（resource_scout 完全失败）：节点必须仍能产出 `code_strategy = "from_scratch"` 的最简计划。

**输入**：`GlobalState`，含 `paper_meta` / `paper_analysis` / `resource_info`。

**输出**：状态更新字典 + 触发 interrupt；resume 后输出 `reproduction_plan`（含 `approved = True`）+ 必要的 `execution_mode` 更新。

**边界条件**：

- `interrupt` 触发后，工作线程应阻塞但**不消耗 CPU**（LangGraph 原生 `interrupt` 行为，由 SqliteSaver 持久化）；
- Streamlit 端检测 interrupt 状态的方式由 S2-08（`app.py`）通过轮询 checkpoint 状态实现（详见 §2.8）。

**依赖关系**：依赖 `core/state.py` / `core/react_base.py` / `core/llm_client.py` / `core/tools/deepxiv_tools.py`。被 `core/graph.py` 注册调用。

---

### 2.4 S2-04: LLM 配置表单组件 (`ui/components/llm_config_form.py`)

**目的**：提供可复用的 Streamlit 组件，让用户输入 / 修改 / 切换 LLM 服务配置。

**详细要求**：

- **支持多模型配置（Q-S2-01 RESOLVED 2026-05-18）**：表单同时支持"全局默认 LLM 配置"+"按节点覆写 LLM 配置"两种模式，覆盖 4 个使用 LLM 的节点：`paper_intake` / `paper_analysis` / `resource_scout` / `planning`（technical-architecture §10.3）。UI 表单形态（独立面板 vs 折叠 override 卡片）由架构师代理在 sp2 架构文档 §2.8 细化。
- 函数签名：`def render_llm_config_form(default: Optional[LLMConfigSet] = None) -> Optional[LLMConfigSet]`，其中 `LLMConfigSet` 由架构师代理在 `core/state.py` 中定义（建议结构：`{"default": LLMConfig, "overrides": Dict[NodeName, LLMConfig]}`，具体形态以架构落地为准）；
- 表单字段（单条 LLMConfig 的字段不变）：
  - `base_url`（文本框，placeholder 给主流 base_url 示例，参考 technical-architecture §10.2）；
  - `model`（文本框）；
  - `api_key`（密码框，类型为 password mask）；
  - `temperature`（slider，0.0~1.0，默认 0.3）；
  - `max_tokens`（数字输入，默认 4096，范围 256~16384）；
- 表单提交后做基础校验（每条 LLMConfig 非空、范围合法、format check），合法时通过 `st.session_state` 存储并返回，非法时显示行内 ERROR 提示；
- **不实现"测试连接"按钮（Q-S2-01 RESOLVED 2026-05-18）**：归 Sprint 3 范围。Sprint 2 用户提交配置后直接进入论文输入流程，配置错误由节点首次调用时的 LLMError 路径暴露（沿用 sp1 错误处理机制）；
- 不持久化 `api_key` 到磁盘（仅存 `st.session_state`，刷新页面后需重新输入；多条 LLMConfig 的 api_key 均独立处理）；
- 节点未覆写时回退到全局默认配置（overrides 表为空时整个表单等同于"单一全局配置"模式，向后兼容 sp1 用法）。

**输入**：可选的默认 LLMConfigSet（含 default + overrides）。

**输出**：用户提交且校验通过后的 LLMConfigSet，否则为 None。

**边界条件**：

- 表单作为独立组件，被 S2-05 / S2-06 / S2-07 三个页面共享调用（在侧边栏渲染）；
- 表单校验失败时不应阻塞主页面渲染。

**依赖关系**：依赖 `core/state.py`（LLMConfig 类型）+ `streamlit`。

---

### 2.5 S2-05: Streamlit 页面 1 —— 论文输入 (`ui/pages/paper_input.py`)

**目的**：让用户输入 arXiv ID / 关键词 / 标题，浏览候选论文并确认目标论文，启动后端 graph 执行。

**详细要求**：

参考 product-design-specification §5.2 页面 1 设计：

- 顶部展示 LLM 配置表单（S2-04 组件）；
- 主区分两部分：
  - **arXiv ID 输入框**：用户直接粘贴 ID（如 `2409.05591`）后点击"获取论文信息"；UI 层调用 `deepxiv_sdk.Reader.brief()` 即时展示标题 / 摘要 / 作者 / TLDR / GitHub URL；
  - **关键词搜索框**（P1 优先级，Sprint 2 可选实现）：调用 `reader.search()` 展示前 10 条匹配结果，每条卡片含标题 / 摘要片段 / 引用数 / GitHub 关联标识；
- 论文确认按钮"开始复现"：
  - 校验 LLM 配置已填且通过基础校验、已选定 arXiv ID；
  - 通过 `app.py` 暴露的接口启动后台工作线程跑 graph，并跳转到 S2-06 分析进度页；
- 显示学科范围提示：当 `categories` 不属于 `cs.*` 时显示 WARNING 卡片但不阻塞（与 paper_intake 行为一致）。

**输入**：用户键盘输入。

**输出**：用户确认的 arXiv ID + LLMConfig，提交给 app.py 启动工作线程。

**边界条件**：

- 关键词搜索的多候选浏览仅支持单选，不支持批量；
- 论文确认页面不直接调用后端 graph 内部 paper_intake 节点（与 product-design-specification §3.2 步骤 1 决策一致），只调用 deepxiv SDK 做即时展示；
- 用户提交后页面应禁用所有控件，避免重复提交。

**依赖关系**：依赖 `core/state.py` / `deepxiv_sdk` / `ui/components/llm_config_form.py` / `app.py`（启动接口） / `streamlit`。

---

### 2.6 S2-06: Streamlit 页面 2 —— 分析进度 (`ui/pages/analysis_progress.py`)

**目的**：展示 paper_intake → paper_analysis → resource_scout → planning 四个节点的实时执行进度，供用户在等待时查看中间产出。

**详细要求**：

- 顶部展示论文基本信息卡片（标题、TLDR、作者、GitHub URL）—— **优先展示 `*_zh` 中文字段**（如 `title_zh` / `tldr_zh`），按 PRD §4.7.5 协作约束；
- 主区展示节点进度条（4 段）：
  - 节点名（中文）；
  - 当前状态（运行中 / 已完成 / 失败 / 降级完成）；
  - 完成时的耗时与关键产出摘要（如 paper_analysis 完成后显示已读章节列表 + 方法摘要前 200 字）；
- 实时日志流：滚动展示 `node_errors` 与 `degraded_nodes` 中的最近条目（一句话摘要 + 可展开详情，参考 product-design-specification §4.5.3 错误信息展示规范）；
- 当 graph 进入 planning interrupt 状态时，自动跳转到 S2-07 计划审核页；
- 当 graph 抛出致命错误（`state.error` 非空）时，展示 FATAL 错误卡片 + "重试" / "返回输入页" 按钮。

**输入**：当前 `thread_id`（从 session_state 读取）。

**输出**：UI 渲染。

**边界条件**：

- 页面通过 `app.py` 暴露的轮询接口每 1~2 秒拉取一次 SqliteSaver 中的最新状态（详见 §2.8）；
- 用户在此页面**不能**修改状态，仅观察；
- "终止当前任务"按钮**仅**在计划审核页（S2-07）提供（作为 revise 无上限的主动出口，详见 AC-S2-13）；分析进度页（本页 S2-06）在 Sprint 2 不提供终止按钮（节点执行中途的中断语义比 interrupt 点更复杂，归 v1.x / Sprint 3+）。

**依赖关系**：依赖 `app.py`（轮询接口） / `core/state.py` / `streamlit`。

---

### 2.7 S2-07: Streamlit 页面 3 —— 计划审核 (`ui/pages/plan_review.py`) —— **核心交互页**

**目的**：展示复现计划与候选仓库列表，承接 planning 节点 interrupt 暂停后的用户决策，恢复 graph 执行。

**详细要求**：

参考 product-design-specification §5.2 页面 3 + technical-architecture §3.3 设计：

- 顶部展示**信息完整度评估卡片**：当 `degraded_nodes` 非空时，醒目展示降级原因与建议审核重点；
- 主区分块展示：
  - **复现计划全文**（分章节可折叠，6 章节对齐 product-design-specification §4.3.1）；
  - **候选仓库卡片列表**：展示 `resource_info.repos` 全量列表，每张卡片含 URL、`quality_score`、`is_official`、`last_commit_date`、`has_readme/has_requirements`、AI 推荐理由；当前 `selected_repo` 高亮；点击其他卡片可切换为新选项；
  - **环境需求与用户环境对比卡片**：展示 `paper_analysis.hardware_requirements`（中文主字段）+ 用户在 LLM 配置侧栏自填的硬件配置（可选）；
- 操作按钮组：
  - "确认计划并开始编码" → 触发 `Command(resume={"decision": "approve"})`；
  - "要求修改计划" → 弹出文本框收集 `user_feedback`，提交后触发 `Command(resume={"decision": "revise", "user_feedback": "..."})`，UI 切回 S2-06 等待重新规划完成；
  - "更换仓库" → 选中候选列表中其他仓库后激活，提交后触发 `Command(resume={"decision": "switch_repo", "new_repo_url": "...", "user_feedback": "..."})`；
  - "只编码不运行（code_only）" → 切换模式，触发 `Command(resume={"decision": "code_only"})`；
  - **"终止当前任务"** → 弹出二次确认对话框（"确认终止？已生成的计划与中间状态会保留在 SQLite checkpoint 中，可通过 thread_id 后续查看，但 graph 执行流终止"），用户确认后触发 `Command(resume={"decision": "cancel"})` + 调用 `app.py::cancel_task(thread_id)`（详见 AC-S2-13）；
- **code_only 模式下的交付标准编辑入口**：Sprint 2 范围内**只展示 planning 节点草拟的 `deliverables` 列表**（只读），是否在 UI 上提供编辑控件归属 §8 Q-S2-04（推荐 Sprint 3 实施）；
- **计划修改进度透明化**（无次数硬上限）：常驻展示"本次任务已修改 N 次 / 累计 token 消耗 X / 总预算 `MAX_TOTAL_LLM_CALLS = 50`"；当 N ≥ 5 时新增温和提示"已修改 5 次，是否需要切换 code_only 模式（仅出代码不复现）以更快推进？"——**仅提示不锁按钮**，用户仍可继续 revise / switch_repo / approve / code_only / 终止任务。

**输入**：当前 `thread_id`，对应的 graph 处于 interrupted 状态。

**输出**：用户决策 payload，通过 `app.py` 暴露的 resume 接口注入 graph。

**边界条件**：

- 用户提交决策后，按钮禁用并提示"恢复中...";页面轮询直到 graph 状态从 interrupted 变为下一节点；
- 若用户长时间不操作（>30 分钟），不做超时处理（Sprint 2 不实现自动超时，归 §8 Q-S2-05）。

**依赖关系**：依赖 `app.py`（resume 接口 + 轮询接口） / `core/state.py` / `streamlit`。

---

### 2.8 S2-08: Streamlit 应用入口 + 工作线程 + 轮询 (`app.py`)

**目的**：作为 Streamlit 主入口，搭建主线程 UI ↔ 工作线程 LangGraph 的异步通信框架，让长耗时的 graph 执行不阻塞 UI 渲染。

**详细要求**：

参考 technical-architecture §9 设计：

- Streamlit 主入口逻辑：
  - 初始化 `st.session_state`（含 `thread_id`、`llm_config`、`current_page`、`worker_thread` 引用、`graph_status` 等）；
  - 根据 `current_page` 路由到 S2-05 / S2-06 / S2-07 三个页面；
- 暴露给 UI 页面的接口集合（建议封装为 `class GraphController`）：
  - `start_task(arxiv_id: str, llm_config: LLMConfig) -> str`：创建新 `thread_id`，启动工作线程跑 `graph.invoke(initial_state, config)`，返回 thread_id；
  - `poll_state(thread_id: str) -> GlobalState`：调用 `graph.get_state(config)` 从 SqliteSaver 读取最新状态；
  - `is_interrupted(thread_id: str) -> bool`：判断 graph 是否处于 interrupt 等待状态；
  - `resume_with(thread_id: str, resume_payload: Dict) -> None`：调用 `graph.invoke(Command(resume=...), config)` 恢复执行（在工作线程中跑，不阻塞 UI）；
  - `cancel_task(thread_id: str) -> None`：**Sprint 2 必须实现**。仅在 graph 处于 planning interrupt 状态时可调用（不支持节点执行中途强制中断，避免线程不安全的 graph 状态）；实现方式：通过 `Command(resume={"decision": "cancel"})` 让 planning 节点正常退出，节点内写 `current_step = "cancelled_by_user"` 后路由到 END。详见 AC-S2-13；
- 工作线程实现：
  - 使用 `threading.Thread(daemon=True)` 启动；
  - **关键约束**：每个工作线程**独立创建自己的 `SqliteSaver` 实例**（SQLite 连接对象不可跨线程共享）；主线程读 checkpoint 时也独立创建 SqliteSaver 实例，依赖 WAL 模式做并发读写（详见 technical-architecture §9.3）；
  - 线程异常通过 `try/except` 包裹后写入 `st.session_state["worker_error"]`，由 UI 检测并展示；
- Streamlit 轮询机制：
  - 在 S2-06 / S2-07 页面通过 `streamlit_autorefresh` 或 `time.sleep + st.rerun` 实现 1~2 秒一次的轮询（**轮询间隔默认值待回归测试后定**）；
- LLM 配置中的 `api_key` 不通过 LangGraph 状态持久化（沿用 Sprint 1 已接受的限制 L-01，但需在 app.py 中**首次启动新 thread_id 时强制刷新 api_key 到 state**，避免 SqliteSaver 中的旧 api_key 复用导致认证错乱）；
- 启动 graph 之前调用 `build_graph(checkpointer=get_checkpointer())` 即可，无需修改 graph.py 的接口契约。

**输入**：用户在 UI 上的操作。

**输出**：UI 状态切换 + 后台 graph 执行。

**边界条件**：

- 同一 Streamlit 进程内允许并发多个 thread_id（但 Sprint 2 不做 UI 上的多任务管理，每次只跑 1 个）；
- Streamlit 页面刷新（用户按 F5）后，session_state 丢失，但 SqliteSaver 仍保留状态——Sprint 2 接受此限制，**不提供"从已有 thread_id 恢复"的入口**（归 §8 Q-S2-05 任务列表功能）。

**依赖关系**：依赖 `core/graph.py` / `core/checkpointer.py` / `core/state.py` / `streamlit` / `threading`。

---

### 2.9 S2-09: PaperMeta / PaperAnalysis 新增字段落地

**目的**：把架构师已在 `docs/technical-architecture.md` §4 完成的 5 个新字段（`title_zh` / `abstract_zh` / `tldr_zh` / `method_summary_en` / `hardware_requirements_en`）同步落地到代码层。

**详细要求**：

- `core/state.py` 中 `PaperMeta` / `PaperAnalysis` TypedDict 扩展上述 5 个 Optional 字段；
- `core/nodes/paper_intake.py::PAPER_META_SCHEMA` 扩展（标识 LLM 应输出的 3 个 `*_zh` 字段，并明确语言约束）；
- `core/nodes/paper_analysis.py::PAPER_ANALYSIS_SCHEMA` 扩展（标识 LLM 应输出的 2 个 `*_en` 字段，同时 `method_summary` / `hardware_requirements` 主字段语言**反转为中文**——详见 PRD §4.7.3）；
- 节点的 `_map_*_result` 落地兜底逻辑：
  - 当 LLM 漏写 `title_zh` / `abstract_zh` / `tldr_zh` 时，**回退为对应英文主字段值**（即 `title_zh = title`），并将节点加入 `degraded_nodes` + 写 `NodeError(error_type="degraded")`；
  - 当 LLM 漏写 `method_summary_en` / `hardware_requirements_en` 时，同上回退兜底；
  - **复用 BUG-S1-02 / BUG-S1-03 已建立的 backfill + WARNING 日志模式**，禁止静默吞错。

**边界条件**：

- 老 SQLite checkpoint 数据（Sprint 1 已存在的 `checkpoints.db`）不含新字段，恢复时 TypedDict 应允许字段缺失（Optional 即可），节点首次访问时通过 `state.get(...)` 容错；
- 字段语义反转（`method_summary` / `hardware_requirements` 中英语义反转）是 **breaking change**，必须在 PRD / 架构 / 代码三处文档完全对齐，且全栈代理在落地时需扫一遍现有引用点。

**依赖关系**：被 §2.10 prompt 扩展依赖。

---

### 2.10 S2-10: 输出语言策略 prompt 扩展

**目的**：在不破坏 Sprint 1 已落地的 Prompt Cache 字节级幂等约束（R-PC4）的前提下，让 paper_intake / paper_analysis 节点产出 §2.9 新字段。

**详细要求**：

- **硬约束（沿用 PRD §4.7.4 与 Sprint 1 dev-plan L885 / R-PC4）**：
  1. **禁止修改 `_INTAKE_SYSTEM_PROMPT` / `_ANALYSIS_SYSTEM_PROMPT_BODY` 主体内容**（字节级冻结）；
  2. **输出语言策略段落必须放在 HumanMessage 通道**，与现有 `_format_paper_context` 同通道；或作为 system prompt **尾部独立段落**追加（不修改主体），通过明确分隔符（如 `--- 输出语言策略 ---`）与主体隔离；
  3. **Schema 同步更新**：`PAPER_META_SCHEMA` / `PAPER_ANALYSIS_SCHEMA` 必须扩展 `*_zh` / `*_en` 字段定义；
  4. **字段缺失兜底**：使用 §2.9 描述的回退策略（`title_zh = title` 等）+ degraded 标记，**避免引入二次 LLM 翻译调用**消耗预算。
- **落地后必须跑一次 Prompt Cache 命中率回归**：固定 arxiv_id 连跑 paper_analysis ×3 次，记录 `cached_tokens / prompt_tokens` 比值，**对照 Sprint 1 F 阶段基线**（任何回退都视为违反 R-PC4，必须修正后再交付）；
- 该回归实验由全栈代理与测试工程师代理协作落实，记录到 `docs/sprint2/test-reports/`。

**依赖关系**：依赖 §2.9。被 §6 验收标准 AC-S2-08 引用。

---

### 2.11 S2-11: graph.py planning interrupt 真实落地

**目的**：把 Sprint 1 `core/graph.py` 中 planning 节点的 interrupt 占位（注释说明）升级为真实 `interrupt()` 调用，并保留下游 coding / execution / reporting 节点的 pass-through 占位。

**详细要求**：

- 在 `core/graph.py` 中替换 planning 节点占位为真实 `core/nodes/planning.py::planning` 函数（含 `interrupt()` 调用）；
- 替换 resource_scout 节点占位为真实 `core/nodes/resource_scout.py::resource_scout`；
- **保留** coding / execution / reporting 三个节点的 pass-through 占位（沿用 Sprint 1 L-03）；
- 不实现 code_only 条件路由（仍走主路径透传到 reporting）—— Sprint 3 范围；
- 不实现 dev_loop 子图 —— Sprint 3 范围；
- 编译后的 graph 必须支持 `Command(resume=...)` 恢复执行。

**依赖关系**：依赖 §2.2 / §2.3。

---

## 3. 非功能需求

### 3.1 性能

| 指标 | 要求 | 说明 |
|------|------|------|
| resource_scout 节点执行时间（理想路径） | <= 2 分钟 | 含 1~2 次仓库 git clone（浅克隆）+ ReAct LLM 调用 |
| planning 节点执行时间（无 revise） | <= 1 分钟 | 单次 ReAct 规划 |
| Streamlit 页面渲染延迟 | <= 1 秒 | UI 主线程不被工作线程阻塞 |
| 状态轮询间隔 | 1~2 秒 | 默认 1.5 秒，UI 响应与 DB 压力平衡，最终值待测试后回归确认 |
| interrupt 恢复响应时间 | <= 5 秒 | 从用户点击"确认"到工作线程实际开始下一节点 |
| Prompt Cache 命中率 | **不低于 Sprint 1 F 阶段基线** | R-PC4 强制约束，回归测试不通过则不交付 |

### 3.2 可靠性

| 指标 | 要求 |
|------|------|
| resource_scout 降级完成率 | 100% —— 所有候选源失败时仍输出 `from_scratch` 策略，不抛致命异常 |
| planning interrupt 恢复完整性 | 100% —— 用户决策的所有 payload 类型（approve / revise / switch_repo / code_only）必须能被正确路由 |
| 新字段缺失降级率 | 100% —— LLM 漏写任何 `*_zh` / `*_en` 字段时节点必须能 backfill 兜底而不中断 |
| 工作线程崩溃感知率 | 100% —— 工作线程异常必须被 UI 主线程检测并展示，禁止静默死亡 |

### 3.3 可维护性

| 指标 | 要求 |
|------|------|
| 类型标注覆盖率 | 100%（公开接口） |
| Docstring 覆盖率 | 100%（公开接口） |
| 工具序列化合规率 | 100% —— 所有 ToolMessage 写入必须是合法 JSON（防 BUG-S1-02 回归） |
| 节点 backfill 模式 | 所有 ReAct 节点的 `_map_*_result` 函数必须用 3 参签名（含 react_messages）+ 工具历史回填（防 BUG-S1-03 回归） |

### 3.4 可测试性

| 指标 | 要求 |
|------|------|
| 单元测试 | resource_scout / planning / git_tools 节点函数与工具函数可独立 mock 测试 |
| 端到端测试 | 真实链路覆盖 paper_input → analysis_progress → plan_review 全流程；可通过 `Command(resume=...)` 模拟用户决策 |
| Streamlit UI 测试 | 至少能通过 `streamlit run app.py` 启动并完成手动 happy path |

---

## 4. 数据结构定义

### 4.1 新增 / 扩展字段一览

| 类型 | 字段 | 类型 | Sprint 2 新增 / 扩展 | 说明 |
|---|---|---|---|---|
| `PaperMeta` | `title_zh` / `abstract_zh` / `tldr_zh` | `Optional[str]` | **新增**（架构师 2026-05-17 已固化在 architecture.md §4） | UI 中文展示，缺失时降级回退 |
| `PaperAnalysis` | `method_summary_en` / `hardware_requirements_en` | `Optional[str]` | **新增** | 英文备份，缺失时降级回退；**注意主字段中英语义反转** |
| `RepoInfo` | `local_path` | `Optional[str]` | **新增**（Sprint 2 提出，需架构师确认到 architecture.md §4） | 仓库克隆后的本地路径，Sprint 3 coding 节点直接使用 |
| `ResourceInfo` | （沿用 architecture.md §4 既有定义） | — | 不新增字段 | — |
| `ReproductionPlan` | （沿用 architecture.md §4 既有定义，含 `deliverables`） | — | 不新增字段 | Sprint 2 首次实际填充 |

> `RepoInfo.local_path` 字段为 Sprint 2 新提出的字段，建议架构师代理在 Sprint 2 启动时同步到 `docs/technical-architecture.md` §4，避免不一致。

### 4.2 GlobalState 字段分组（Sprint 2 使用情况）

| 分组 | Sprint 2 使用情况 |
|---|---|
| LLM 配置 (`llm_config`) | 使用（UI 注入） |
| 用户输入 (`user_input`, `input_type`) | 使用（UI 注入 arXiv ID） |
| 各步骤输出 (`paper_meta`, `paper_analysis`, `resource_info`, `reproduction_plan`) | 全部使用 |
| 流程控制 (`current_step`, `execution_mode`, `error`, `messages`) | 使用，`execution_mode` 可由用户在 planning 审核切换 |
| 错误追踪 (`node_errors`, `degraded_nodes`, `retry_budget_remaining`) | 使用 |
| 修复循环追踪 (`fix_loop_count` 等) | 定义不使用（Sprint 3 启用） |
| 工作目录 (`workspace_dir`) | 使用（git clone 落盘 + UI 日志） |

---

## 5. 接口定义

### 5.1 core/tools/git_tools.py

```python
def git_clone(url: str, dest_dir: str, depth: int = 1, timeout: int = 60) -> Dict:
    """浅克隆仓库到指定目录。返回 {success, local_path, duration_seconds, error}"""

def analyze_local_repo(local_path: str) -> RepoInfo:
    """对本地仓库做本地指标分析（git log + 目录扫描）"""

def check_url_reachable(url: str, timeout: int = 5) -> bool:
    """快速过滤死链"""

# LangChain 工具工厂
def make_git_clone_and_analyze_tool() -> BaseTool: ...
def make_check_url_reachable_tool() -> BaseTool: ...
```

### 5.2 core/nodes/resource_scout.py

```python
def resource_scout(state: GlobalState) -> dict:
    """资源搜集与评估节点（ReAct agent，max_rounds=10）。

    Returns:
        状态更新字典，至少包含 "resource_info" 和 "current_step" 字段。
        降级时包含更新后的 "degraded_nodes" / "node_errors" 字段。
    """
```

### 5.3 core/nodes/planning.py

```python
def planning(state: GlobalState) -> dict:
    """复现规划节点（ReAct agent，max_rounds=8）+ interrupt 人在回路。

    内部触发 langgraph.interrupt(payload) 暂停 graph，等待 UI 通过
    Command(resume={...}) 注入用户决策（approve / revise / switch_repo / code_only / cancel）。
    revise/switch_repo 无次数上限，由 MAX_TOTAL_LLM_CALLS=50 总预算自然兜底。

    Returns:
        恢复后的状态更新字典，含 "reproduction_plan"（approved=True）+ 可选的
        "execution_mode" 更新。
    """
```

### 5.4 ui/components/llm_config_form.py

```python
def render_llm_config_form(default: Optional[LLMConfigSet] = None) -> Optional[LLMConfigSet]:
    """渲染 LLM 配置侧栏表单（支持多模型配置：default + per-node overrides）。
    校验通过后返回 LLMConfigSet，否则返回 None。
    LLMConfigSet 字段形态由架构师代理在 core/state.py 定义。
    """
```

### 5.5 app.py 暴露给页面的接口

```python
class GraphController:
    def start_task(self, arxiv_id: str, llm_config_set: LLMConfigSet) -> str: ...
    def poll_state(self, thread_id: str) -> GlobalState: ...
    def is_interrupted(self, thread_id: str) -> bool: ...
    def resume_with(self, thread_id: str, resume_payload: Dict) -> None: ...
    def cancel_task(self, thread_id: str) -> None: ...  # Sprint 2 新增：人在回路主动出口
```

---

## 6. 验收标准

> Sprint 2 共 13 条验收标准（AC-S2-01 ~ AC-S2-13）。除显式标注 manual-only 的项外均要求自动化测试覆盖。

### 6.1 端到端验收（核心验收项）

**AC-S2-01：完整链路 happy path**

> 通过 Streamlit UI 输入有效 arXiv ID（如 `2405.14831` HippoRAG），系统依次执行 paper_intake → paper_analysis → resource_scout → planning 四个节点；planning 触发 interrupt 后，用户在计划审核页面点击"确认计划"，graph 恢复并透传到 reporting END。

验证步骤：
1. 启动 `streamlit run app.py`；
2. 在论文输入页填入合法 arXiv ID 与 LLM 配置；
3. 观察分析进度页 4 个节点依次完成（每个节点显示已完成状态、关键产出摘要）；
4. 自动跳转到计划审核页，检查计划全文 + 候选仓库列表 + 信息完整度卡片正常展示；
5. 点击"确认计划"，graph 恢复执行，状态从 interrupted 切到下一节点；
6. 最终 `state.reproduction_plan.approved == True` 且 `current_step` 推进。

**AC-S2-02：interrupt 中断 / 恢复语义正确**

> planning 触发 interrupt 后，graph 必须真正暂停（工作线程不消耗 CPU、状态完整持久化到 SQLite）；通过 `Command(resume=...)` 恢复后，graph 必须从 interrupt 之后的边继续，且 ReproductionPlan 在状态中可被读取。

**AC-S2-03：interrupt 在 Streamlit 轮询架构下的恢复语义**

> 在工作线程模型下，主线程通过 `poll_state` 检测到 interrupt 状态、显示审核 UI；用户决策通过 `resume_with` 注入后，工作线程在新线程内继续执行而非阻塞主线程；UI 恢复轮询并最终观察到状态推进。

**AC-S2-04：resource_scout 自动选仓与候选展示**

> 输入一个有 GitHub 关联仓库的 CS 论文 arXiv ID，resource_scout 节点输出 `ResourceInfo.repos` 至少 1 条（含 `quality_score / is_official / last_commit_date / has_readme / dir_structure` 等指标），`selected_repo` 非空，候选列表在计划审核页面正确展示。

**AC-S2-05：resource_scout 完全失败时降级到 from_scratch**

> 在 mock 测试中模拟 deepxiv `github_url` 为空 + PwC 无结果 + web search 无结果，节点输出 `resource_info.resource_strategy == "from_scratch"`，`degraded_nodes` 包含 `"resource_scout"`，`node_errors` 有 degraded 记录，节点不抛致命异常。

**AC-S2-06：planning revise 无次数上限 + 软提示**

> 用户连续点击 N 次"要求修改计划"（N 任意），每次都应被 planning 节点正常处理（重跑 ReAct 子图、产出新 plan、再次 interrupt）；**不存在"强制 force_finish"路径**。当 N ≥ 5 时，UI 必须展示温和提示"已修改 5 次，是否需要切换 code_only 模式以更快推进？"——按钮不锁，用户仍可继续 revise / switch_repo / approve / code_only / 终止任务。任务级兜底由 `MAX_TOTAL_LLM_CALLS = 50` 总预算约束，触顶时由 `core/react_base.py` 的 budget_check 自然终止当前 ReAct 子图（仅终止本轮 ReAct，不强制 approve）。

**AC-S2-07：planning switch_repo 决策正确路由**

> 用户在计划审核页选择候选列表中的另一个仓库点击"更换仓库"，planning 节点收到新仓库 URL 后重新生成计划，新计划中 `code_strategy` 引用新仓库；switch_repo 与 revise 同等无次数上限。

### 6.2 模块级验收

**AC-S2-08：Prompt Cache 命中率不回退**

> 在固定 arxiv_id（与 Sprint 1 F 阶段基线相同）连跑 paper_analysis ×3 次，`cached_tokens / prompt_tokens` 比值**不低于 Sprint 1 F 阶段基线**。回归报告归档到 `docs/sprint2/test-reports/`。该 AC 对 §2.10 prompt 扩展是硬约束（R-PC4）。

**AC-S2-09：PaperMeta / PaperAnalysis 新增字段降级兜底**

> 在 mock 测试中模拟 LLM 漏写 `title_zh` / `abstract_zh` / `tldr_zh` / `method_summary_en` / `hardware_requirements_en` 任一字段，节点 `_map_*_result` 必须 backfill 兜底为对应英文/中文主字段值，节点加入 `degraded_nodes`，写 `NodeError(error_type="degraded")`，且 WARNING 日志非静默（参考 BUG-S1-02 治理模式）。

**AC-S2-10：git_tools 基础能力**

> `git_clone` 对合法仓库 URL 浅克隆成功并返回 `success=True` + 合法 `local_path`；对死链 / 私有仓库返回 `success=False` + 合法 `error`；`analyze_local_repo` 对本地仓库返回完整 `RepoInfo`（无 `quality_score` 字段，由节点综合填）。

**AC-S2-11：LLM 配置表单（多模型）**

> `render_llm_config_form()` 渲染**全局默认 + 4 个节点覆写**的配置区块（节点列表：`paper_intake` / `paper_analysis` / `resource_scout` / `planning`）；每条 LLMConfig 校验 `base_url` / `model` / `api_key` 非空、`temperature` 在 [0, 1]、`max_tokens` 在 [256, 16384]，合法时返回 `LLMConfigSet`，非法时显示行内 ERROR 提示且不返回；覆写表为空时回退为单一全局配置模式；`api_key`（含所有覆写条目）均不持久化到 SqliteSaver（首次启动新 thread_id 时由 app.py 注入到 state）；**不提供"测试连接"按钮**（Q-S2-01 RESOLVED，归 Sprint 3）。节点首次 LLM 调用失败时，sp1 既有 LLMError 路径暴露给 UI。

**AC-S2-12：Streamlit UI 三页面贯通**（**manual-only**，无自动化 UI 测试）

> 测试工程师手动跑 `streamlit run app.py`，按以下路径走完一次：论文输入页填表 → 分析进度页观察 4 节点 → 计划审核页查看计划 + 候选仓库 + 决策按钮 → 点击"确认计划" → graph 推进到 END。任一页面渲染失败、按钮不响应、状态卡死视为不通过。

**AC-S2-13：planning 终止当前任务按钮**

> 在计划审核页（S2-07）点击"终止当前任务"按钮，UI 弹出二次确认对话框（文案见 §2.7）；用户确认后通过 `Command(resume={"decision": "cancel"})` + `app.py::cancel_task(thread_id)` 在工作线程中终止 graph。最终 `state.current_step == "cancelled_by_user"`，SQLite checkpoint 完整保留（thread_id 可后续查询），分析进度页展示终止状态卡片 + "返回输入页开启新任务"按钮；工作线程不残留、不消耗 CPU。自动化测试用 mock interrupt + cancel resume 路径覆盖（不依赖 UI 渲染）。

---

## 7. 依赖与风险

### 7.1 外部依赖

| 依赖项 | 类型 | 风险等级 | 说明 |
|---|---|---|---|
| `git` 命令行工具 | 系统二进制 | 低 | 需在用户机器上预安装；resource_scout 依赖 |
| Papers With Code API | 外部 API | **中** | 是否限速 / 是否需要 API key / 失败兜底策略待架构师评估，详见 §8 Q-S2-02 |
| `streamlit` | Python 包 | 低 | 主流 UI 库，API 稳定 |
| `streamlit-autorefresh`（可选） | Python 包 | 低 | 用于轮询机制，可被 `time.sleep + st.rerun` 替代 |
| LangGraph `interrupt()` | LangGraph API | 中 | API 在 0.2.x 版本已稳定，但与 SqliteSaver + 工作线程组合的边界场景需在 Sprint 2 验证 |

### 7.2 内部依赖

| 依赖项 | 说明 |
|---|---|
| Sprint 1 已交付的全部模块 | resource_scout / planning 节点直接复用 `react_base.py` + `deepxiv_tools.py` + `llm_client.py`；新增字段依赖 `state.py` 扩展 |
| 架构师代理 | `RepoInfo.local_path` 新字段需架构师同步到 architecture.md §4；Q-S2-02 PwC API 接入策略需架构师评估方案 |
| 测试工程师代理 | Prompt Cache 回归实验、UI manual happy path、git_tools mock 用例需测试工程师协作 |

### 7.3 风险矩阵

| 编号 | 风险 | 可能性 | 影响 | 缓解策略 |
|---|---|---|---|---|
| R-S2-01 | **Streamlit 工作线程与 LangGraph SqliteSaver 的线程安全**：SqliteSaver 内部 SQLite 连接不可跨线程共享，跨线程访问会报 `Programming Error: SQLite objects created in a thread can only be used in that same thread` | 高 | 高 —— 全 UI 链路瘫痪 | 强制每个线程独立实例化 SqliteSaver；依赖 WAL 模式做并发读写；架构师代理评估是否需要更复杂的线程隔离方案（详见 §8 需架构师确认） |
| R-S2-02 | **interrupt 在 Streamlit 轮询架构下的恢复语义**：`Command(resume=...)` 必须在工作线程而非主线程中调用，且 graph 是否能从 interrupt 之后的正确边继续需实测 | 中 | 高 —— 人在回路核心交互失效 | 在 Sprint 2 早期通过最小 spike 验证 LangGraph + threading 组合的 interrupt 行为；测试工程师 e2e 用例必须覆盖 resume 后状态推进 |
| R-S2-03 | **PwC API 是否限速 / 是否需要 API key**：MVP 不引入 GitHub API 后，PwC 是主要补充源，若被限速或要求注册会影响 happy path 完成率 | 中 | 中 —— 部分 arXiv ID 找不到候选仓库 | 工具层重试 + 兜底到 web search；架构师代理评估是否需要本地缓存 PwC 响应；详见 §8 Q-S2-02 |
| R-S2-04 | **Prompt Cache 命中率因 prompt 扩展而回退**：输出语言策略段落若错误地插入 system prompt 主体会破坏字节级幂等 | 中 | 中 —— token 成本上升、延迟增加 | §2.10 硬约束 + AC-S2-08 验收强制 + 与 Sprint 1 F 阶段基线对照实验 |
| R-S2-05 | **新字段语义反转（`method_summary` / `hardware_requirements` 英→中）破坏下游引用**：Sprint 1 代码中如有任何"假定主字段是英文"的隐式依赖会出错 | 低 | 中 | 全栈代理落地时全仓 grep 引用点；AC-S2-09 验收测试覆盖兜底路径 |
| R-S2-06 | **revise 无次数上限可能导致 token 消耗较多**：用户反复打磨计划可能耗尽预算 | 中 | 低 | UI 透明展示已修改次数 + 累计 token 消耗 + 总预算（N ≥ 5 时温和提示切 code_only）；`MAX_TOTAL_LLM_CALLS = 50` 总预算自然兜底；"终止当前任务"按钮（AC-S2-13）提供主动出口。Q-S2-03 已 RESOLVED |
| R-S2-07 | **`api_key` 序列化问题（沿用 Sprint 1 L-01）**：现仍明文写入 `checkpoints.db`，Sprint 2 UI 暴露后增大泄露面 | 中 | 中 | Sprint 2 首次启动 thread_id 时由 app.py 注入（不复用 checkpoint 中旧值）；最终方案归 v2 / OP-1 待定 |

---

## 8. 开放问题

| 编号 | 问题 | 影响范围 | 建议处理方式 |
|---|---|---|---|
| **Q-S2-01** | ~~LLM 配置表单字段范围是否需要扩展？是否支持"多模型配置"？"测试连接"按钮是否在 Sprint 2 范围内？~~ | `ui/components/llm_config_form.py` | **[RESOLVED 2026-05-18]** Maria 决策：① **支持多模型配置**（全局默认 + 4 个节点 `paper_intake` / `paper_analysis` / `resource_scout` / `planning` 可独立覆写）；② **不实现"测试连接"按钮**，归 Sprint 3 范围。落地详见 §2.4 / §5.4 / AC-S2-11；UI 表单形态由架构师代理在 sp2 架构 §2.8 细化。 |
| **Q-S2-02** | ~~Papers With Code API 接入策略~~ | `core/nodes/resource_scout.py` + 工具层 | **[RESOLVED 2026-05-18]** 架构师代理评估完成（详见 sp2 架构文档 §4.6）。六要素决定：① **直接 requests HTTP（不引第三方 SDK）**；② **MVP 不申请 API key 但预留 `os.getenv("PWC_API_TOKEN")` 注入点**；③ **限速兜底**：本地 5 req/s 节流 + 429/5xx/timeout 指数退避 1-2-4s 三次重试后抛 TransientError；④ **失败不阻塞 happy path**：单点失败 → ReAct agent 跳 web_search；PwC 完全不可用时 resource_scout 仍能从 deepxiv `github_url` + `web_search` 产出非空候选；⑤ **缓存**：`functools.lru_cache(128)` 缓存 arxiv_id / title 查询，同任务内复用；⑥ **可观测性**：仅打 WARNING 日志，不写 `node_errors`。新建 `core/tools/pwc_tools.py`，与 sp1 `deepxiv_tools` 工具工厂风格一致。 |
| **Q-S2-03** | ~~planning revise 循环上限定为 3 次是否合理？~~ | `core/nodes/planning.py` + `ui/pages/plan_review.py` | **[RESOLVED 2026-05-18]** Maria 决策：取消硬上限，revise / switch_repo 可无限次触发；UI 在 N ≥ 5 时温和提示切 code_only 但不锁按钮；任务级兜底依赖 `MAX_TOTAL_LLM_CALLS = 50` 总预算；同时 Sprint 2 加 "终止当前任务" 按钮（AC-S2-13，与 Q-S2-05 联动） |
| **Q-S2-04** | ~~code_only 模式的"交付标准编辑"UI 入口是否在 Sprint 2 实现？~~ | `ui/pages/plan_review.py` | **[RESOLVED 2026-05-18]** Maria 决策：归 Sprint 3（与 coding_only 节点同期上线）。Sprint 2 计划审核页对 `deliverables` 仅做**只读展示**，不提供编辑控件。 |
| **Q-S2-05** | ~~Streamlit 端是否提供任务取消按钮~~ / 任务历史列表 / 已有 thread_id 恢复入口？ | `app.py` + 各页面 | **[部分 RESOLVED 2026-05-18]** Maria 决策：Sprint 2 实现"终止当前任务"按钮（AC-S2-13，与 Q-S2-03 联动，作为 revise 无上限的主动出口）；任务历史列表 / thread_id 恢复入口仍归 v1.x / Sprint 3+ |

---

**文档结束**

*本文档为 Sprint 2 产品需求文档正式版。数据结构与异常体系的权威定义以 `docs/technical-architecture.md` 第 4 章和第 12.2 节为准；输出语言策略以 `docs/product-design-specification.md` §4.7 为准；MVP 资源搜索"不引入 GitHub API"决策见 product-design-specification §4.2.2 与 technical-architecture §3.2 / §3.3 已固化条目。*
