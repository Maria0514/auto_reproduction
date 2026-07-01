# Sprint 4 PRD 草案：通用 Human-in-the-Loop 交互能力（agent 缺信息时主动问用户）

> 状态：**PRD 草案**，待 Maria 确认 §8 开放问题后再进架构 → 开发 → 测试。
> 作者：产品经理代理｜日期：2026-06-30
> 术语与 `docs/product-design-specification.md`、`docs/technical-architecture.md`、`docs/sprint3/prd.md`、`docs/sprint3/dev-loop-compatibility-matrix.md` 保持一致；本文不臆造已有机制。

---

## 0. 决策更新（2026-06-30，Maria 拍板）

本草案经 Maria 拍板，以下为最终决策；正文与之冲突处**以本节为准**：

- **工具极简化（覆盖 §3.1 / §3.4 的 `input_type` 设计——作废）**：不做 5 种 `input_type` 枚举。`request_user_input` 就是**一个获取用户输入的工具**——agent 缺任何信息时调它、传一段 `question` 给用户、拿回一段文本继续。仅保留最小必要的 `is_sensitive`（bool，默认 false；凭证类置 true → UI password 输入 + 脱敏 + 可记住）与可选 `purpose_key`（密钥库 key + 去重）。去掉 `input_type` / `options` / choice / confirm / path 等类型区分。UI 就一个输入框（敏感时 password + 「记住」勾选）。
- **Q2**：同上，工具单一通用，不按类型拆。
- **Q5（凭证存储 / checkpoint 脱敏）**：独立 `.secrets` 文件（明文 0600 + gitignore，MVP 不加密），敏感值**完全不进 checkpoint / state**。
- **Q6（spike）**：**不单独 spike**，直接开发、边做边验 ReAct 工具内 interrupt 的 resume 重跑幂等（§5.3 列为开发中重点验证项；若开发中发现行为异常再补 spike）。
- **采纳 PM 默认**：Q1 不设硬超时、一直暂停等用户 / Q3 无交互前端时按"信息缺失"降级不死等 / Q4 逐条问（一次一个）/ Q7 按 `purpose_key`（含 host）粒度、跨任务可复用。

---

## 1. 问题定义 / 目标场景

### 1.1 当前缺陷（真实证据）

真跑 arXiv:2202.12837 端到端时，execution 节点遇 `git clone` 私有/不存在仓库认证失败：非交互式 sandbox subprocess 读不到 stdin（`fatal: could not read Username for 'https://github.com'`），coding↔execution 修复循环对这个**缺凭证**的失败原地打转 10 轮（每轮同样的报错），最后降级。

根因有三层，逐层在现有代码里都能定位：

1. **缺信息时 agent 无路可走**：现状 agent 遇到"缺凭证/缺参数/需澄清/缺输入"时，只能在 ReAct 循环里干转、在修复循环里重试、最终降级（`degraded`）或 permanent。`core/nodes/execution.py` 的 `_maybe_interrupt_or_return` 只在「修复耗尽/不可修复/子预算触顶」时才 interrupt#2 让用户三选一，**没有"我缺一条具体信息、问到就能继续"这一档**。
2. **环境性失败被误分类**：`core/tools/git_tools.py::_classify_clone_failure`（L144）只分 transient/permanent；认证失败（`could not read Username` / `Authentication failed`）若落进 `_TRANSIENT_STDERR_KEYWORDS`（L44）会被当瞬态白重试。execution 节点的 `_classify_execution`（L167）也没有"缺凭证类"分类——这类失败靠重试和降级都解决不了，应快速止损转交互。
3. **from_scratch 仍 clone 幻觉仓库的逻辑矛盾**：当 `resource_strategy == "from_scratch"`（无可用仓库）时，理论上不应再去 clone 任何仓库；但 agent 仍可能生成/执行 clone 一个 LLM 幻觉出来的仓库 URL 的命令，触发上述认证失败。

### 1.2 目标能力

像 Claude Code 那样：**流水线里的 agent 在执行过程中缺任何信息时，都能主动调用一个工具发起用户交互、暂停流程让用户输入，拿到后继续往下跑**。

- 凭证（git token / HF_TOKEN / API key 等）是**最迫切的首要用例**，但能力本身**通用**（缺参数、缺决策、需澄清、缺输入都能问）。
- 凭证可选「记住到密钥库」供后续复现复用，并全程脱敏。

### 1.3 与现有机制的天然契合点（务必复用，不另起炉灶）

| 现有机制 | 位置 | 复用方式 |
|---|---|---|
| 工具内 `interrupt()` 暂停主图 | LangGraph human-in-the-loop tool 模式 | agent 调"问用户"工具 → 工具内 `interrupt(payload)` → UI 收集 → `Command(resume=值)` 回传给工具 → agent 继续 |
| 两个既有 interrupt 点 + `interrupt_kind` 分发 | `planning.py` L778 `interrupt_kind="planning"`；`execution.py` L68 `INTERRUPT_KIND="dev_loop_failure"` | 新增第三类 `interrupt_kind="user_input_request"`，app.py L255 `interrupt_kind()` helper 已支持按 kind 分发 |
| UI 轮询 + resume | `app.py` `is_interrupted`/`poll_state`/`resume_with`/`interrupt_kind` | 新交互复用同一套，无需新通道 |
| sandbox 子进程环境注入 | `sandbox/local_venv.py` L292/304 `extra_env`（`{**os.environ, **extra_env}`），`run_in_venv` L658 / `prepare_venv` 已支持 | 凭证以 env var 经 `extra_env` 注入 sandbox，无需改子进程执行骨架 |

---

## 2. 功能范围（MVP 必做 vs 非目标）

### 2.1 MVP 必做

| 编号 | 项 | 说明 |
|---|---|---|
| S4-01 | 通用交互工具 `request_user_input` | LangChain `@tool`，agent 调用即在工具内 `interrupt()` 暂停，等用户输入；语义见 §3 |
| S4-02 | 凭证检测/分类止损 | git_tools + execution 错误分类新增"缺凭证类"（`credential_required`），快速止损不白重试，触发交互（见 §4） |
| S4-03 | 凭证注入 sandbox | 拿到凭证后以 env var 经 `extra_env` 注入 `prepare_venv`/`run_in_venv`（见 §4.3） |
| S4-04 | 可选记住到密钥库 + 脱敏 | 用户选「记住」则存 secrets 文件供后续复现复用；脚本/日志/checkpoint/报告全程脱敏（见 §4.4 / §6） |
| S4-05 | from_scratch 不 clone 矛盾修复 | `resource_strategy == "from_scratch"` 时禁止 clone 幻觉仓库（见 §4.5） |
| S4-06 | UI 承载交互 | 执行监控页/计划审核页响应 `interrupt_kind="user_input_request"`，渲染问题 + 输入框（敏感输入用 password 类型）+「记住」勾选；复用 `resume_with`（见 §3.4 / §5） |
| S4-07 | state/config 落地 | 新增交互相关 state 字段 + 超时/密钥库路径等 config 常量（见 §5） |

### 2.2 挂载交互工具的范围边界（防发散，关键）

「缺任何信息都能问」必须有清晰边界，否则 agent 会滥用、流程被打断到不可用：

- **挂载节点（MVP）**：**只在 `coding` 节点的 ReAct agent 挂 `request_user_input` 工具**。理由：缺信息的真实痛点集中在编码/执行修复循环（凭证、缺数据路径、缺超参决策）；`coding` 是修复回合内被反复触发的节点，是首要用例的发生地。
  - `execution` 节点是**手写复合节点**（非 ReAct wrapper，`execution.py` 注释明确），其交互应通过 **execution 错误分类 → 路由回 coding → coding agent 调工具问** 的路径间接达成，而非在 execution 节点体内直接 interrupt（避免与 interrupt#2 的重跑幂等 commit 边界契约冲突，见 §5.3）。
  - paper_intake / paper_analysis / resource_scout / planning 节点 MVP **不挂**交互工具（这些阶段的缺信息已有既有降级/审核机制，且过早引入会打断自动化流畅性）。
- **允许阶段**：仅在 planning 审核**通过之后**的 coding/execution 阶段允许发起 `user_input_request` 交互（与现有"planning interrupt#1 在 coding 之前"不冲突）。
- **单次交互形态**：一次 `request_user_input` 调用 = 问**一个**信息项（一个问题 + 期望类型 + 是否敏感），逐条问。批量缺信息列入开放问题（§8 Q4）。

### 2.3 非目标（MVP 不做）

- **远程 / SSH / 云服务器场景的凭证**（v2 云服务器模式才有，技术架构 §6.3 待确认）；
- **OAuth / 浏览器跳转授权流**（只做"用户在 UI 输入框里贴 token/值"这一形态）；
- **凭证自动轮换、过期检测、刷新**；
- **多用户 / 团队级密钥库共享**（单机本地，技术架构 §6.5）；
- **CI / 非交互模式的完整自动化降级策略**（列入开放问题 §8 Q3，MVP 给最小兜底）；
- **给交互工具挂到全部 7 个节点**（仅 coding，见 §2.2）；
- **批量一次问多条**（§8 Q4）。

---

## 3. 交互工具设计（S4-01）

### 3.1 工具语义：agent 怎么表达"我缺什么"

新增 LangChain `@tool` `request_user_input`，挂在 coding 节点 ReAct agent 的工具集（与现有 coding 工具如 `read_code_file`/`list_dir`/`write_file` 同级，由工具工厂注入）。入参（agent 自主填写）：

> **注（§0 决策）**：工具已极简化——去掉 `input_type` 5 种枚举与 `options`，只保留下表三个字段。

| 参数 | 类型 | 说明 |
|---|---|---|
| `question` | str | 给用户看的问题文本（中文叙述，遵循 sp2 输出语言策略；事实层如 URL/包名保留英文） |
| `is_sensitive` | bool（默认 false） | 是否敏感（凭证/密钥）→ UI 用 password 输入、全程脱敏、可记住 |
| `purpose_key` | str（可选） | 信息项的稳定标识（如 `"git_credential:github.com"` / `"hf_token"`），用作密钥库的 key + 去重避免重复问同一项 |

> **建议转架构师**：工具 docstring（决定 LLM 何时/如何调它，避免滥用）、与现有 coding 工具集的注入方式，属实现细节，交架构师细化。UI 单一输入框即可（敏感时 password + 「记住」勾选），无需按类型分渲染。

### 3.2 暂停与恢复的数据契约

复用 §1.3 的 human-in-the-loop tool 模式：

1. agent 在 ReAct 循环里调 `request_user_input(question=..., input_type=..., is_sensitive=..., ...)`；
2. 工具体内调 `interrupt(payload)`，payload **必须含 `interrupt_kind="user_input_request"`**（与 `"planning"` / `"dev_loop_failure"` 区分，app.py `interrupt_kind()` helper 据此分发），并携带 `question`/`input_type`/`is_sensitive`/`purpose_key`/`options`；
3. 主图在此暂停，checkpoint 落盘（沿用 sp2 每线程独立 SqliteSaver + WAL）；
4. UI 检测到 `interrupt_kind == "user_input_request"` → 渲染问题 + 对应输入控件 + （敏感时）「记住此凭证」勾选；
5. 用户提交 → `Command(resume={"value": <用户输入>, "remember": <bool>})`；
6. 工具从 `interrupt()` 返回值拿到 `value`，作为 ToolMessage 结果返回给 agent，agent 带着这条信息继续 ReAct 循环。

**resume payload 契约**（与 sp2 的 `{"decision": ...}` 范式同构）：

```
Command(resume={"value": "<用户输入的字符串>", "remember": false})
```

- 敏感输入（`is_sensitive=True`）时 `remember` 才生效（§4.4）；
- **重跑幂等关键约束**：工具内 interrupt 的重跑幂等问题（resume 时整节点从头重跑）必须遵守 execution.py 已踩通的 commit-边界契约（`execution.py` L12-20 文档化的 S-1 spike CP-S-3）。coding 节点是 ReAct wrapper，工具内 interrupt 的重跑语义与 execution 手写节点不同——**这是必须转架构师确认的高风险点**（见 §5.3 与 §8 Q6）。

### 3.3 与重试预算的关系

`request_user_input` 工具调用**本身不消耗 LLM 预算**（它是工具执行，不是 LLM 调用），但触发它的 coding agent 的 ReAct round 会照常按 `_make_react_wrapper` 既有逻辑扣 `retry_budget_remaining`。等待用户输入期间流程暂停，不计预算。

### 3.4 UI 形态（S4-06）

执行监控页（`ui/pages/execution_monitor.py`，sp3 已落地）新增对 `interrupt_kind == "user_input_request"` 的分支：

- 展示 `question` 文本 + 一句话上下文（当前在做什么、为什么需要这个信息）；
- 按 `input_type` 渲染：`credential` → password 输入框；`choice` → 单选；`confirm` → 是/否；`text`/`path` → 普通输入框；
- `is_sensitive=True` 时显示「记住此凭证供后续复现复用」勾选（默认不勾）；
- 提交按钮 → `resume_with(thread_id, {"value": ..., "remember": ...})`。

---

## 4. 凭证用例专项（首要用例）

### 4.1 检测 / 分类（S4-02）：怎么识别"这步需要凭证"

两个止损点，对应 §1.1 的误分类 bug：

**(a) git_tools 层**（`core/tools/git_tools.py::_classify_clone_failure` L144）：
- 新增**缺凭证类**关键字识别（优先级高于 transient）：`could not read username`、`authentication failed`、`could not read password`、`terminal prompts disabled`、`403`、`permission denied (publickey)` 等；
- 命中 → **不按 transient 白重试**（修复 §1.1 bug a），分类为 `credential_required`，快速止损；
- 同时 git clone 子进程应设 `GIT_TERMINAL_PROMPT=0` 经 `extra_env` 注入，让认证失败**立即返回**而非挂起等 stdin（现状非交互 subprocess 读不到 stdin 才报 `could not read Username`）。

**(b) execution 节点层**（`core/nodes/execution.py::_classify_execution` L167 + `ErrorCategory` L87）：
- `ErrorCategory` 新增 `CREDENTIAL_REQUIRED`（归**不可自动修复类**——纯靠 coding 改代码解决不了，需外部信息），**不进 `AUTO_FIXABLE`**；
- 在硬件/数据缺失之前/同层增加凭证关键字匹配；
- 命中后**不再无脑回 coding 干转**：路由回 coding 时附带"这是缺凭证类失败，请调 `request_user_input` 向用户索要凭证"的结构化反馈（注入 coding 的 HumanMessage），由 coding agent 调工具发起交互。

> 这样把"环境性失败白重试 10 轮"改成"识别缺凭证 → 回 coding → coding agent 问用户 → 拿到 token → 注入 → 继续"。

### 4.2 索要

由 coding agent 调 `request_user_input(input_type="credential", is_sensitive=True, purpose_key="git_credential:<host>")` 发起，走 §3 数据契约。

### 4.3 注入 sandbox（S4-03）

拿到凭证后注入执行环境，**复用 `sandbox/local_venv.py` 已有的 `extra_env` 通道**（L304 `env = {**os.environ, **(extra_env or {})}`）：

- git 凭证：注入 `GIT_ASKPASS` / 或构造带 token 的 remote URL（具体方式交架构师）、`GIT_TERMINAL_PROMPT=0`；
- HF_TOKEN / API key：注入对应 env var（`HF_TOKEN` / `HUGGINGFACE_TOKEN` 等）；
- 注入只在子进程 env 生效，**不写进代码文件、不写进 checkpoint 明文**（见 §6）。

> **建议转架构师**：凭证从"用户输入值"到"sandbox extra_env / git remote URL 改写"的具体映射、`purpose_key` → env var 名的映射表、git token 注入用 askpass 还是 URL 改写，属实现细节，交架构师细化。

### 4.4 可选记住密钥库（S4-04）

- 用户在 UI 勾「记住」+ 提交 → 凭证写入**单独的 secrets 文件**（建议 `workspace_dir/.secrets` 或项目级 `.env`，权限 0600，**且必须在 `.gitignore`**——注意当前仓库 `.gitignore` 已被修改，需确认 secrets 路径在内）；
- key 用 `purpose_key`，后续复现遇同一 `purpose_key` 时**先查密钥库**，命中则直接注入、**不再问用户**（去重，提升复跑体验）；
- 不勾「记住」→ 仅本次任务内存中使用，任务结束即丢弃，不落盘。

> **建议转架构师**：密钥库具体形态（`.env` 追加 vs 独立 secrets 文件 vs 加密文件）、与 `config.py` LLM api_key 现有"不持久化"约定（技术架构 §8.4）的一致性、读取优先级，属技术决策，交架构师 + Maria 共同拍（§8 Q5）。

### 4.5 from_scratch 不 clone 矛盾修复（S4-05）

- 当 `resource_info.resource_strategy == "from_scratch"`（或 `selected_repo is None`）时：
  - coding 节点 prompt 明确"无可用仓库，从零生成代码，**禁止 clone 任何仓库 URL**"；
  - planning 生成的 `execution_steps` 不应含 clone 命令；若含，execution 应跳过/拒绝执行幻觉 clone（避免触发认证失败空转）；
- 这是逻辑矛盾修复，与交互能力解耦，但同属本次"缺信息止损"主题，一并做。

---

## 5. 与现有机制的关系

### 5.1 复用 interrupt/resume + 新增 interrupt_kind

- 新增**第三类 interrupt**：`interrupt_kind="user_input_request"`，与既有 `"planning"`（interrupt#1）/ `"dev_loop_failure"`（interrupt#2）并存；
- `app.py::interrupt_kind()`（L255）已按 payload 的 `interrupt_kind` 键分发，**天然支持**第三类，仅需 UI 增分支；
- 不与既有两个 interrupt 点冲突：planning interrupt 在 coding 之前；dev_loop_failure interrupt 在修复耗尽时；user_input_request 在 coding/execution 阶段且"缺一条具体信息"时——三者**触发条件互斥**，共用同一 thread_id 的 checkpoint 模型（sp2 已验证）。

### 5.2 对 state 的影响（S4-07）

新增字段（沿用 must-fix-1：**严禁给 List 字段加 reducer**，写入走单点 read-modify-write）：

| 字段 | 类型 | 说明 |
|---|---|---|
| `pending_user_input` | `Optional[Dict]` | 当前待回答的交互请求快照（question/input_type/purpose_key/is_sensitive），供 UI 渲染；**不存敏感答案** |
| `collected_inputs` | `Dict[str, str]` | 本次任务内已收集的非敏感信息（purpose_key → value）；**敏感值不进此字段、不入 checkpoint 明文**（§6） |

> 敏感凭证的内存传递不经 GlobalState 明文落盘——这是必须转架构师确认的 checkpoint 脱敏方案（§6 / §8 Q5）。

### 5.3 对 retry_budget / fix_loop 的影响

- `request_user_input` 工具本身不扣 LLM 预算（§3.3）；
- 缺凭证类（`CREDENTIAL_REQUIRED`）**不进 `AUTO_FIXABLE`**，故**不消耗 `fix_loop_count`**——避免重演"缺凭证打转 10 轮耗尽 fix_loop"；
- **高风险点（转架构师）**：coding 是 ReAct wrapper，工具内 `interrupt()` 在 resume 时的"整节点从头重跑"语义，与 execution.py 已用 commit-边界契约（`_has_committed_result_for_round` guard，L1006）解决的重跑幂等问题**机理不同**。coding 工具内 interrupt 重跑时，前面已执行的工具调用是否重放、ReAct messages 历史是否完整恢复，**必须由架构师验证 LangGraph 工具内 interrupt 在 ReAct 子图中的重跑行为**（可能需要一个 spike，类比 sp2 的 S-1 spike）。

### 5.4 对 UI / GraphController 的影响

- 执行监控页新增 `user_input_request` 分支（§3.4）；
- `GraphController` 的 `resume_with`/`is_interrupted`/`interrupt_kind` **零改动**即可承载（payload 通道通用）；
- 计划审核页若未来要在 planning 阶段也支持交互，留作 §8 开放问题，MVP 不动。

---

## 6. 安全

| 项 | 要求 |
|---|---|
| 脱敏-脚本/代码 | 凭证**绝不写进生成的代码文件**（如硬编码 token），只经 `extra_env` 注入子进程 |
| 脱敏-日志 | execution/coding 的 WARNING/INFO 日志、`logs` 字段、`ExecutionResult.logs` 中凭证值必须脱敏（如 `****`）；git stderr 含 token 的 URL 也要脱敏 |
| 脱敏-checkpoint | 敏感值**不进 GlobalState 明文**、不进 SqliteSaver checkpoint（沿用技术架构 §8.4 "api_key 不持久化"约定，扩展到所有凭证）。`collected_inputs` 只存非敏感项 |
| 脱敏-报告 | reporting 生成的 Markdown 报告不泄露任何凭证明文 |
| 密钥库权限 | secrets 文件权限 0600、在 `.gitignore`、仅本地（不上传） |
| 注入边界 | 凭证 env var 只注入复现任务子进程，不污染主进程长期环境 |

> **建议转架构师**：checkpoint 脱敏的落地方式（敏感值用占位符进 checkpoint + 内存旁路真实值 vs 完全不进 state）、日志脱敏 filter 的统一落点，是安全关键实现，必须由架构师设计并由测试工程师验证（AC-S4-06/07）。

---

## 7. 验收标准（AC）

| 编号 | 验收标准 | 可测方式 |
|---|---|---|
| AC-S4-01 | coding agent 调 `request_user_input` 能触发 `interrupt_kind="user_input_request"` 暂停主图，`Command(resume={"value":...})` 后 agent 拿到值继续 | mock + e2e（`Command(resume=...)` 模拟用户输入） |
| AC-S4-02 | git clone 认证失败（`could not read Username` / `Authentication failed`）被分类为 `credential_required`，**不白重试**、**不消耗 fix_loop_count** | mock：构造认证失败 stderr 断言分类 + 路由 |
| AC-S4-03 | 拿到 git token / HF_TOKEN 后经 `extra_env` 注入 sandbox 子进程，后续 clone/下载成功 | e2e（需授权真凭证，省配额范式）或 mock 注入断言 |
| AC-S4-04 | 用户勾「记住」→ 凭证写入 secrets 文件（0600、在 .gitignore）；同 `purpose_key` 复跑时不再问、直接注入 | mock + 文件断言 |
| AC-S4-05 | `resource_strategy == "from_scratch"` 时不执行任何 clone 命令 | mock：from_scratch 状态断言无 clone |
| AC-S4-06 | 凭证不出现在：生成代码文件 / `ExecutionResult.logs` / checkpoint DB / 报告 Markdown（grep 全链路无明文） | e2e：注入已知 token 后 grep 工作目录 + checkpoint + 报告 |
| AC-S4-07 | 日志中凭证值被脱敏为占位符 | mock：caplog 断言无明文 |
| AC-S4-08 | UI 执行监控页对 `user_input_request` 渲染问题 + 输入框（敏感用 password）+「记住」勾选，提交走 resume_with | 手动 happy path（`streamlit run`） |
| AC-S4-09 | 三类 interrupt（planning / dev_loop_failure / user_input_request）在同一 thread_id 下互不干扰、各自正确路由 | e2e：覆盖三 interrupt 串行场景 |

---

## 8. 开放问题清单（待 Maria 拍板）

| 编号 | 问题 | PM 倾向建议 |
|---|---|---|
| **Q1** | **交互超时 / 无人应答怎么办？** 用户长时间不回 `user_input_request` 时，流程一直暂停（checkpoint 保留）还是设超时后自动降级？ | 建议：**一直暂停**（checkpoint 天然保留，用户回来继续），不设硬超时——与 sp2 planning interrupt 一致；超时降级列为 v1.x |
| **Q2** | **MVP 是否先只做凭证用例，再推广到通用？** 即 S4-01 工具 MVP 是否只支持 `input_type="credential"`，其余类型后置？ | 建议：**工具一次做成通用**（5 种 input_type），但**只在凭证路径接线打通端到端**（S4-02~04），其余 input_type 留作 agent 自主使用、不强测——风险可控且不返工 |
| **Q3** | **非交互 / CI 模式的降级行为？** 无 UI（CLI/自动化）时 agent 调 `request_user_input` 该怎样？ | 建议：MVP 给最小兜底——检测无交互前端时，`request_user_input` 直接返回"无法获取，按缺失处理"+ 标 degraded，不死等；完整 CI 策略后置 |
| **Q4** | **批量缺信息 vs 逐条问？** 一次缺多个信息（token + 数据路径 + 超参）时，逐条 interrupt 还是攒一批一次问？ | 建议：MVP **逐条问**（一次一个 `request_user_input`）；批量表单列为 v1.x |
| **Q5** | **密钥库具体形态？** 独立 secrets 文件 / 追加 `.env` / 加密文件？与现有 api_key "不持久化"约定如何统一？checkpoint 脱敏用占位符旁路还是完全不进 state？ | 需 Maria + 架构师共同拍；PM 倾向独立 `.secrets` 文件（明文 0600 + gitignore，MVP 不加密），checkpoint 敏感值完全不进 state |
| **Q6** | **coding（ReAct wrapper）工具内 interrupt 的重跑幂等**：是否需要先跑一个 spike 验证 LangGraph 工具内 interrupt 在 ReAct 子图中 resume 重跑行为（类比 sp2 S-1 spike）？ | 强烈建议：**先 spike 再开发**（这是最大技术不确定性，见 §5.3） |
| **Q7** | **凭证作用域**：记住的凭证按 host（`github.com`）粒度，还是按任务粒度？跨论文复现能否复用同一 GitHub token？ | 建议：按 `purpose_key`（含 host）粒度，跨任务可复用——这正是"记住供后续复现复用"的价值 |

---

## 9. Sprint 归属建议

### 9.1 与现有 sp4 方向的关系

`docs/sprint3/dev-loop-compatibility-matrix.md` 已明确 sp4+ 方向是：**dev_loop 真 multi-agent（Coder/Executor/Reviewer + supervisor + 共享 scratchpad）** 与 **coding agent 全权管环境**。本交互能力与之关系：

- **强协同**：multi-agent dev_loop 里的 Coder/Executor agent 同样会缺信息（缺凭证、缺数据、缺决策），`request_user_input` 工具天然是它们的能力补充；"coding agent 全权管环境"更需要主动向用户要环境凭证。
- **解耦可独立先行**：本能力**不依赖** multi-agent 落地，挂在当前**单 agent** coding 节点即可端到端打通——是 multi-agent 的前置基础设施。

### 9.2 排期建议

建议作为 **Sprint 4 的第一批工作（先于 multi-agent）**，理由：

1. 它修复了真跑 arXiv:2202.12837 暴露的**端到端硬伤**（认证失败打转 10 轮降级），优先级高于 multi-agent 增强；
2. 它是 multi-agent dev_loop 的前置能力（multi-agent 的各 agent 也要会问用户）；
3. 范围可控（只挂 coding 节点 + 复用既有 interrupt/resume/extra_env 三套现成机制）。

**建议拆分**：先做 **Q6 的 spike**（验证 ReAct 工具内 interrupt 重跑幂等）→ 再 S4-02（凭证止损分类，纯逻辑、风险低，可与 spike 并行）→ S4-01/03/04（交互工具 + 注入 + 密钥库）→ S4-05（from_scratch 修复）→ S4-06（UI）→ 验收。
