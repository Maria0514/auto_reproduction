# Sprint 4 PRD 草案 v2（路线丙）：coding / execution 双 agent 松耦合升级 + 通用用户交互能力

> 状态：**PRD 草案 v2（路线丙）**，待 Maria 确认 §11 开放问题后再进架构 → 开发 → 测试。
> 作者：产品经理代理｜日期：2026-07-01
> 术语与 `docs/product-design-specification.md`、`docs/technical-architecture.md`、`docs/sprint3/prd.md`、`docs/sprint3/architecture.md`、`docs/sprint3/dev-loop-compatibility-matrix.md` 保持一致；本文一切以代码/文档实证为准，不臆造已有机制。
> 本 v2 取代 v1 窄草案（v1 只覆盖交互能力 `request_user_input`）。v1 §0「决策更新」中 Maria 拍板的极简决策**已全部并入本文** §5.1 / §7；v1 其余章节被本文吸收或作废。

---

## 0. 版本决策记录（v2 相对 v1 的方向变更，Maria 拍板）

v1 只做「agent 缺信息时问用户」这一条窄能力。经与 Maria 多轮讨论，Sprint 4 的**目标方向已扩展为「路线丙」**——把主图里的 `coding` 与 `execution` 两个节点升级成**两个松耦合的真 agent**。本文承载这个完整方向。

- **否掉路线甲（真 multi-agent）**：不做 supervisor + 共享 scratchpad 的三 agent 子图。`docs/sprint3/dev-loop-compatibility-matrix.md` 那份评估服务于路线甲，其 must-fix / 开放问题大多是 supervisor/scratchpad 特有的（见本文 §6 逐条说明如何被绕开或简化）。
- **否掉路线乙（单一全能 agent）**：不把 coding+execution 合并成一个全能 agent。
- **采纳路线丙（两个独立 agent + state 结构化反馈通信）**：
  - `coding` 现状**已是 ReAct agent**（`_make_react_wrapper` 生成，5 个工具），本次**补两样工具**：`run_command` + `request_user_input`。
  - `execution` 现状**是手写确定性七步复合节点**（非 agent），本次**改造成有 agent loop 的 ReAct execution agent**，挂 sandbox 工具（把 `prepare_venv`/`run_in_venv` 包成工具）+ `request_user_input`。
  - 通信沿用现状：execution 把错误摘要 + 分类经 GlobalState 单点写回、经修复回边送回 coding。**松耦合、不共享 scratchpad、不上 supervisor**。
  - **主图 7 节点骨架不变**（沿用 sp3 AC-S3-10：节点集合与名称严格不变）。
- **吸收合并了三条候选线**：通用交互能力（`request_user_input`）+ sandbox 能力工具化（`run_command`）+「两个都是 agent」（去掉 multi-agent 的 supervisor/scratchpad）。

---

## 1. 问题定义 / 目标

### 1.1 当前形态与三个真实缺陷（代码实证）

现状（`core/graph.py` L189-235 实证）：主图 7 节点，`coding` 是 ReAct wrapper（`core/nodes/coding.py`），`execution` 是手写复合节点（`core/nodes/execution.py`），二者经 `_route_after_coding` / `_route_after_execution` 条件边构成修复循环。缺陷有三：

1. **coding 是「盲写」agent，不能自验**：`coding` 的 5 个工具（`_get_coding_tools` L255-268：write_code_file / read_code_file / list_dir / read_section / web_search）**没有任何"运行命令"能力**。coding 只能写代码、把入口脚本交给下游 execution 去跑；写→跑→看报错→改的闭环被硬切成两个节点，每一次「跑」都要付出一整个 execution 节点 + 一次修复回边的成本。轻量语法/import 错误本可在 coding 内自查，现状必须绕一整圈修复循环。

2. **execution 是「一次性确定性管线」，缺乏 agent 的自适应能力**：`execution` 七步骨架（`core/nodes/execution.py` L1020-1121）把 execution_steps 逐条跑一遍、分类、判定，全程无 LLM 决策能力。它无法在执行中途"想一想换个跑法"、无法针对环境差异自适应调整命令、也无法在缺信息时主动问用户——只能重试或降级。

3. **缺信息时 agent 无路可走**（v1 已识别，真跑 arXiv:2202.12837 暴露）：agent 遇到"缺凭证 / 缺数据路径 / 需澄清"时，只能在循环里干转、最终降级。典型：`git clone` 私有仓库认证失败（非交互 subprocess 读不到 stdin，`fatal: could not read Username`），修复循环对这个**缺凭证**的失败原地打转直到耗尽 `MAX_FIX_LOOP_COUNT=10`。现状**没有"我缺一条具体信息、问到就能继续"这一档**。

### 1.2 目标能力（路线丙）

把 coding / execution 升级为两个各自具备 agent loop、能自主决策、缺信息时能主动问用户、通过 state 结构化反馈松耦合协作的 agent：

- **coding agent**：能"写→跑→看→改"自成闭环（补 `run_command`），缺信息时能问用户（补 `request_user_input`）。
- **execution agent**：从确定性管线升级为 ReAct agent，把 sandbox 能力工具化后自主编排执行、自适应处理环境差异，缺信息时能问用户。
- **通用交互能力**：像 Claude Code 那样，任一 agent 在执行中缺任何信息（凭证 / 参数 / 决策 / 输入）都能主动暂停流程问用户、拿到后继续。凭证是最迫切的首要用例，但能力本身通用。

### 1.3 与现有机制的天然契合点（务必复用，不另起炉灶）

| 现有机制 | 位置（代码实证） | 复用方式 |
|---|---|---|
| ReAct 子图 + wrapper 预算扣减 | `core/react_base.py` `create_react_subgraph` / `_make_react_wrapper`（唯一预算扣减点 L889-894） | execution 改造为 ReAct agent 后自动走此扣减点（见 §6.2） |
| 工具内 `interrupt()` 暂停主图 | LangGraph human-in-the-loop tool 模式 | agent 调 `request_user_input` → 工具内 `interrupt(payload)` → UI 收集 → `Command(resume=值)` → agent 继续 |
| `interrupt_kind` 分发 | `planning.py` L778 `"planning"`；`execution.py` L68 `"dev_loop_failure"`；`GraphController.interrupt_kind()` 只读 helper（sp3 E1 落地） | 新增第三类 `interrupt_kind="user_input_request"`，helper 天然支持按 kind 分发 |
| UI 轮询 + resume | `GraphController.is_interrupted` / `poll_state` / `resume_with` / `interrupt_kind` | 新交互复用同一套通道，无需新通道 |
| sandbox 子进程环境注入 | `sandbox/local_venv.py` `_run_subprocess` L304 `env = {**os.environ, **(extra_env or {})}`；`run_in_venv` L658 已带 `extra_env` 形参 | 凭证以 env var 经 `extra_env` 注入子进程（注意 `prepare_venv` L444 现状**无** `extra_env` 形参，见 §7.3 缺口） |
| 修复循环边界字段 | `_dev_loop_route` / `fix_loop_count` / `fix_loop_history` / `execution_result`（`state.py` L192-210） | 路线丙沿用，仅重新安置到 execution ReAct wrapper 的编排层（见 §5.3 / Q-A） |
| List 字段单点 read-modify-write 范式 | `node_errors` / `degraded_nodes` / `fix_loop_history` 均无 reducer（`state.py` L189-193 实证） | 两 agent 各自单点写 state，绕开 must-fix-1（见 §6.1） |

---

## 2. 功能范围（MVP 必做 vs 非目标）

### 2.1 MVP 必做

| 编号 | 项 | 说明 |
|---|---|---|
| S4-01 | **coding agent 补 `run_command` 工具** | 让 coding 能自己运行命令（轻量验证/smoke），闭合"写→跑→看→改"（见 §4.1 / Q-B 职责边界） |
| S4-02 | **coding agent 补 `request_user_input` 工具** | agent 缺信息时调用即工具内 `interrupt()` 暂停问用户；语义见 §5.1 |
| S4-03 | **execution 改造为 ReAct execution agent** | 把手写七步节点改为 ReAct agent（挂 sandbox 工具 + `request_user_input`）；错误分类 / B 档判定 / 修复循环边界 / interrupt#2 重新安置（见 §4.2 / Q-A） |
| S4-04 | **sandbox 能力工具化** | 把 `prepare_venv` / `run_in_venv` 包成 execution agent 的工具（见 §4.2.2）；`run_command`（S4-01）与之复用同一套 sandbox 底座 + 护栏 |
| S4-05 | **通用交互工具 `request_user_input`（单一）** | LangChain `@tool`，coding / execution 两 agent 共用同一个工具实现；极简三字段（`question` + `is_sensitive` + 可选 `purpose_key`），单一输入框（见 §5.1） |
| S4-06 | **凭证注入 sandbox** | 拿到凭证经 `extra_env` 注入 sandbox 子进程；`prepare_venv` 补 `extra_env` 形参（见 §7.3） |
| S4-07 | **凭证存储（独立 `.secrets`）+ 全程脱敏** | 敏感值存独立 `.secrets`（0600 + gitignore，MVP 不加密），完全不进 checkpoint/state，日志/报告/代码全程脱敏（见 §7 / §8） |
| S4-08 | **两 agent 松耦合通信契约** | execution agent 产出的结构化反馈 schema（喂回 coding），沿用 `execution_result` + `[error_category=...]` 前缀 + `fix_loop_history`（见 §4.3 / Q-D） |
| S4-09 | **UI 承载 `user_input_request` 交互** | 执行监控页响应 `interrupt_kind="user_input_request"`，渲染问题 + 单输入框（敏感用 password）+「记住」勾选，提交走 `resume_with`（见 §5.4） |
| S4-10 | **state / config 落地** | 新增交互相关最小 state 字段 + 必要 config 常量（见 §5.2） |

### 2.2 范围边界（防发散，关键）

- **挂 `request_user_input` 的节点（MVP）**：`coding` + `execution` 两个 agent。paper_intake / paper_analysis / resource_scout / planning **不挂**（这些阶段已有既有降级/审核机制，过早引入打断自动化流畅性）。
- **挂 `run_command` 的节点（MVP）**：仅 `coding`（execution 有专门的 sandbox 执行工具，见 §4.2.2；职责分工见 Q-B）。
- **允许阶段**：仅在 planning 审核**通过之后**的 coding / execution 阶段允许发起 `user_input_request`（与既有 planning interrupt#1 不冲突）。
- **单次交互形态**：一次 `request_user_input` 调用 = 问**一个**信息项（逐条问，Maria 已定）。
- **主图 7 节点骨架不变**：节点集合与名称严格保持 `paper_intake / paper_analysis / resource_scout / planning / coding / execution / reporting`。execution 从手写节点改为 ReAct wrapper，**节点名不变**（与 sp3 把 coding 从占位换成 ReAct wrapper 时"节点名不变"同范式）。

### 2.3 非目标（MVP 不做）

- **真 multi-agent（supervisor + 共享 scratchpad + Reviewer 三 agent）**——本次明确否掉（路线甲），留作 v2+ 若有需要再评估。
- **合并 coding+execution 为单一全能 agent**——否掉（路线乙）。
- **远程 / SSH / 云服务器场景的凭证**（v2 云服务器模式才有）。
- **OAuth / 浏览器跳转授权流**（只做"用户在 UI 输入框贴 token/值"）。
- **凭证自动轮换、过期检测、刷新**。
- **多用户 / 团队级密钥库共享**（单机本地）。
- **凭证加密存储**（MVP 明文 0600 + gitignore，加密留 v1.x）。
- **批量一次问多条**（MVP 逐条问）。
- **input_type 类型枚举 / options / choice / confirm / path 等类型区分**——Maria 已明确否掉，工具单一通用（见 §5.1）。
- **给交互工具挂到全部 7 个节点**（仅 coding + execution）。

---

## 3. 两个 agent 的目标形态总览

| | coding agent | execution agent |
|---|---|---|
| **现状** | 已是 ReAct agent（`_make_react_wrapper` 生成，`core/nodes/coding.py` L436-444） | 手写确定性七步复合节点（`core/nodes/execution.py`，非 agent） |
| **本次变更** | 补 2 个工具（不改 wrapper 形态） | **改造成 ReAct execution agent**（挂 sandbox 工具 + 交互工具） |
| **工具集（目标）** | write_code_file / read_code_file / list_dir / read_section / web_search（现有 5 个）+ **`run_command`** + **`request_user_input`** | sandbox 执行工具（包 `prepare_venv` / `run_in_venv`）+ **`request_user_input`** |
| **职责边界（Q-B）** | 写代码 + 轻量验证/smoke（跑一下入口脚本能不能 import、能不能起、语法对不对）+ 缺信息问用户 | 完整复现执行（跑 execution_steps）+ 收产物（collect_artifacts）+ 错误分类 + 指标解析 + 缺信息问用户 |
| **预算扣减** | wrapper 唯一扣减点（`react_base.py` L889-894，现状即如此） | 改造后**自动获得同一扣减点**（这正是 must-fix-2 被简化的机理，见 §6.2） |
| **对外通信** | 产出 `code_output_dir` + coding 结果（现状 `_map_coding_result`） | 产出 `execution_result` + `[error_category=...]` 前缀 + `fix_loop_history`（现状字段，见 §4.3） |
| **interrupt** | `request_user_input` 工具内 interrupt#3（`user_input_request`） | 保留 interrupt#2（`dev_loop_failure`）+ `request_user_input` 工具内 interrupt#3 |

**通信方式（松耦合，不共享 scratchpad、不上 supervisor）**：execution agent 把错误摘要 + 分类经 GlobalState 单点写回（沿用 `_map_execution_result` 的 `execution_result` + `NodeError.error_message` 的 `[error_category=...]` 前缀，`execution.py` L737-795），经修复回边（`_route_after_execution` 的 `retry_coding` 分支，`graph.py` L146）送回 coding；coding 的 `_build_coding_context`（L197-247）已会读 `execution_result` + `fix_loop_count>0` 注入修复反馈。**这套通信通道现状已存在，路线丙沿用即可，无需新建 scratchpad。**

---

## 4. 工具与形态设计

### 4.1 coding agent 补 `run_command`（S4-01）

新增工具，让 coding agent 能在其 ReAct loop 内运行命令，闭合"写→跑→看报错→改"。

- **底座复用**：`run_command` 复用 `sandbox/local_venv.py` 的子进程护栏（`_run_subprocess`：禁 shell=True、进程组隔离、超时杀子树、输出字节截断、cwd 限定 WORKSPACE_DIR）。**不新造执行通道**。
- **入参（agent 自主填写，最小）**：`command`（要运行的命令）。工作目录锚定 `code_output_dir`（与写工具同基准），越界拒绝（复用 `_is_within_workspace` 校验）。
- **返回**：exit_code + stdout/stderr 尾部（截断），供 agent 判断下一步。
- **职责边界（Q-B）**：`run_command` 用于**轻量验证 / smoke**（import 能否通过、脚本能否启动、语法是否正确），**不用于完整复现执行**（完整跑训练/评估仍是 execution agent 的职责）。产品层建议见 Q-B。
- **凭证注入**：`run_command` 也应支持经 `extra_env` 注入已收集凭证（如 coding 在 smoke 时需要 clone），复用 §7.3 的注入通道。

> **交架构师细化**：`run_command` 与 execution sandbox 工具是否共用同一 venv（coding 的 code_output_dir 下 `.venv`），还是 coding 只做无需 venv 的极轻量 smoke（如 `python -c "import ..."` 用系统解释器）；工具 docstring 如何约束 agent 只做轻量验证不做重活（防越界干 execution 的事）。

### 4.2 execution 改造为 ReAct execution agent（S4-03 / S4-04）

#### 4.2.1 形态

把 `execution` 从手写七步节点改为**用 `_make_react_wrapper` 生成的 ReAct agent**（与 coding 同范式）。节点名 `execution` 不变（AC-S3-10）。ReAct agent 自主编排：准备环境 → 跑 execution_steps → 看结果 → 必要时调整 / 问用户 → 收产物 → 判定。

#### 4.2.2 sandbox 工具化

把现有 `prepare_venv` / `run_in_venv` 包成 execution agent 的工具（LangChain `@tool`），复用现有全部护栏：

- `prepare_environment`（包 `prepare_venv`）：在 code_output_dir 下建/复用 venv + 装依赖。
- `run_in_sandbox`（包 `run_in_venv`）：在 venv 中跑一条命令，返回 exit_code + stdout/stderr（截断）。
- 现有的确定性辅助逻辑（`_step_to_command` 顶层 `&&`/`;` 拆分、`_rewrite_interpreter` 裸 python/pip 改写、`_resolve_cd` 边界校验、`_expand_globs`）**保留为工具内部实现或独立纯函数**，供 agent 调用——即"命令解析/改写"这类确定性能力仍是工具能力，agent 负责"何时跑什么"的决策。
- `collect_artifacts`、`_parse_metrics`（三档指标解析）、`_classify_execution`（错误分类）保留为**确定性能力**，在 map_result / 工具层落地（见 Q-A）。

> **产品建议**：把 sandbox 底层"怎么安全地跑一条命令"（护栏、解析、改写）继续留在确定性代码里（**不是** agent 的自由裁量），agent 只决策"跑哪些命令、看到结果后怎么办、要不要问用户"。这样既让 execution 获得 agent 的自适应性，又不牺牲 sp3 已踩通的 sandbox 安全护栏。

#### 4.2.3 交互工具

execution agent 挂 `request_user_input`（与 coding 共用同一工具实现），使 execution 在缺数据路径 / 缺凭证 / 需人工决策时能就地问用户（而非只能回 coding 或降级）。

### 4.3 两 agent 松耦合通信契约（S4-08 / Q-D）

execution agent 喂回 coding 的结构化反馈，**沿用现状字段，不新建 scratchpad**：

| 反馈载体 | 字段/schema（现状，路线丙沿用） | 来源 |
|---|---|---|
| 执行结果 | `execution_result: ExecutionResult`（success / metrics / logs / errors / artifacts / runtime_seconds / environment_info） | `state.py` L129-137 |
| 错误细分类 | `ExecutionResult.errors[0]` 的 `[error_category=...]` 前缀（syntax/import/dependency/path/runtime/data_missing/hardware/timeout/unresolved_resource/credential_required*） | `execution.py` `_build_execution_result` L709 |
| 修复历史 | `fix_loop_history: List[FixLoopRecord]`（round_number / error_summary / error_category / fix_strategy / timestamp） | `state.py` L151-158 |
| coding 侧消费 | `_digest_execution_feedback`（裁剪 errors + 解析 error_category + stderr_tail）→ 注入修复回合 HumanMessage | `coding.py` L162-194 |

（*`credential_required` 为本次 S4-06 新增分类，见 §7.2。）

> **Q-D 产品结论**：通信契约就是「`execution_result`（含 `[error_category=...]` 前缀）+ `fix_loop_history` 经 GlobalState 单点写回 → 修复回边 → coding 的 `_build_coding_context` 消费」这套现成链路。路线丙**不新增** DevLoopState / CodingOutput / ExecutionFeedback（那是路线甲的子图私有 schema）。execution agent ReAct 化后，`ExecutionFeedback` 从"手写节点局部 dataclass"降为"agent 收尾时构造 ExecutionResult 的中间产物"，语义不变。

---

## 5. 交互能力设计（承接 v1 §0 极简决策）

### 5.1 工具语义：单一通用工具（极简，Maria 硬约束）

新增**一个** LangChain `@tool` `request_user_input`，coding / execution 两 agent 共用。它就是"获取用户输入"的工具——agent 缺任何信息时调它、传一段 `question`、拿回一段文本继续。**不做 input_type 枚举、不做 options / choice / confirm / path 类型区分。**

| 参数 | 类型 | 说明 |
|---|---|---|
| `question` | str | 给用户看的问题文本（中文叙述，遵循 sp2 输出语言策略；URL/包名等事实层保留英文） |
| `is_sensitive` | bool（默认 false） | 凭证/密钥类置 true → UI 用 password 输入、全程脱敏、可「记住」 |
| `purpose_key` | str（可选） | 信息项稳定标识（如 `"git_credential:github.com"` / `"hf_token"`），用作 `.secrets` 的 key + 去重（同 key 不重复问）+ 跨任务复用 |

UI 就一个输入框（敏感时 password + 「记住」勾选），无需按类型分渲染。

### 5.2 对 state / config 的影响（S4-10，最小）

新增字段（沿用 must-fix-1：**严禁给任何 List 字段加 reducer**，走单点 read-modify-write）：

| 字段 | 类型 | 说明 |
|---|---|---|
| `pending_user_input` | `Optional[Dict]` | 当前待回答的交互请求快照（question / is_sensitive / purpose_key），供 UI 渲染；**绝不存敏感答案** |
| `collected_inputs` | `Dict[str, str]` | 本次任务内已收集的**非敏感**信息（purpose_key → value）；**敏感值不进此字段、不进 checkpoint**（§8） |

config：MVP 交互能力**不设硬超时**（Maria 已定，一直暂停），故无需新增超时常量。仅需新增 `.secrets` 路径常量（见 §7.4）。`MAX_DEV_LOOP_LLM_CALLS=60` / `MAX_FIX_LOOP_COUNT=10` / `DEV_LOOP_MIN_CALLS_PER_ROUND=2` 已在 config 落地，路线丙沿用（见 §6.2）。

### 5.3 暂停与恢复数据契约

复用 human-in-the-loop tool 模式（与 sp2 planning / sp3 dev_loop 同构）：

1. agent 在 ReAct loop 里调 `request_user_input(question=..., is_sensitive=..., purpose_key=...)`；
2. 工具体内调 `interrupt(payload)`，payload 含 `interrupt_kind="user_input_request"`（与 `"planning"` / `"dev_loop_failure"` 区分）+ `question` / `is_sensitive` / `purpose_key`；
3. 主图暂停，checkpoint 落盘（沿用 sp2 每线程独立 SqliteSaver + WAL）；
4. UI 检测 `interrupt_kind == "user_input_request"` → 渲染问题 + 单输入框 +（敏感时）「记住」勾选；
5. 用户提交 → `Command(resume={"value": "<用户输入>", "remember": <bool>})`（与 sp2 `{"decision": ...}` 范式同构）；
6. 工具从 `interrupt()` 返回值取 `value`，作为 ToolMessage 结果返回给 agent，agent 带着信息继续 loop。

**重跑幂等（Q-C，Maria 已定不单独 spike、边开发边验）**：工具内 `interrupt()` 在 resume 时的重跑语义（ReAct 子图重跑范围、前面已执行的工具调用是否重放、messages 历史是否完整恢复）与 execution 手写节点已踩通的 commit-边界契约（`_has_committed_result_for_round` guard，`execution.py` L1006）**机理不同**——**列为开发中头号重点验证项**（见 §11 Q-C1）。若开发中实测行为异常，再补 spike。

### 5.4 UI 形态（S4-09）

执行监控页（`ui/pages/execution_monitor.py`，sp3 已落地）新增对 `interrupt_kind == "user_input_request"` 的分支：

- 展示 `question` + 一句话上下文（当前在做什么、为什么需要）；
- 单输入框（`is_sensitive=True` 时用 password 类型）；
- `is_sensitive=True` 时显示「记住此凭证供后续复现复用」勾选（默认不勾）；
- 提交 → `resume_with(thread_id, {"value": ..., "remember": ...})`。
- `GraphController` 的 `resume_with` / `is_interrupted` / `interrupt_kind` **零改动**即可承载（payload 通道通用，interrupt_kind helper 已按 kind 分发）。

---

## 6. 与既有 dev-loop 兼容性矩阵的关系（路线丙如何绕开/简化 must-fix）

`docs/sprint3/dev-loop-compatibility-matrix.md` 是为**路线甲（supervisor + 共享 scratchpad 三 agent 子图）**做的评估。它的 2 项 must-fix + 7 开放问题大多是 supervisor/scratchpad 特有的。路线丙的处置：

### 6.1 must-fix-1（无 reducer 的 List 字段并发写覆盖）→ 基本绕开

- must-fix-1 的风险来自"supervisor 子图让多 agent 各自作为图节点、分别返回 `node_errors`/`degraded_nodes`/`fix_loop_history`，LangGraph 对无 reducer 的字段整值覆盖丢数据"（矩阵 §A.1 / §D.3）。
- **路线丙不存在这个场景**：coding 与 execution 仍是主图两个**独立节点**，各自单点 read-modify-write 写 state（现状 `coding.py` `_map_coding_result` L403-431、`execution.py` `_map_execution_result` L750-795 已如此），**不并发写同一字段**。
- 代码实证：`node_errors`（L189）/ `degraded_nodes`（L190）/ `fix_loop_history`（L193）在 `state.py` 确认为无 `Annotated`/`operator.add` 的普通 List。**路线丙沿用"严禁加 reducer + 单点写"红线即可**，无新增并发写点 → 基本绕开 must-fix-1。

### 6.2 must-fix-2（dev_loop 预算扣减缺口）→ 因 execution ReAct 化而大幅简化

- must-fix-2 的缺口来自"路线甲的 supervisor 子图**不走** `_make_react_wrapper`，是手写子图，对 `retry_budget_remaining` 零扣减，需手动补回写逻辑"（矩阵 §B / §D.3）。
- **路线丙让 execution 变成 ReAct agent（`_make_react_wrapper` 生成）→ 自动走唯一预算扣减点**。代码实证：`_make_react_wrapper._wrapper` L889-894——
  ```
  rounds_used = int(final_state.get("round", 0)); rounds_used = max(1, rounds_used)
  remaining = max(0, int(state.get("retry_budget_remaining", 0)) - rounds_used)
  update.setdefault("retry_budget_remaining", remaining)
  ```
  execution ReAct 化后，它的每一轮 agent 推理**自动按 round 数扣 `retry_budget_remaining`**，与 paper_intake/paper_analysis/resource_scout/coding 完全同机理。**不再需要手动补预算回写逻辑**——这正是路线丙相对路线甲的一大简化。
- **残留需接的点（较小）**：
  1. 现状 execution 手写节点的预算扣减是"仅 metrics 档 3 LLM 抽取触发时单点回写"（`_map_execution_result` L780-793）。ReAct 化后，这部分被 wrapper 的 round 扣减**替代/合并**——需在改造时确认不双重扣减（wrapper setdefault 已保证 map_result 若已写 `retry_budget_remaining` 则不覆盖）。
  2. `MAX_DEV_LOOP_LLM_CALLS=60` 子预算天花板 + `DEV_LOOP_MIN_CALLS_PER_ROUND=2` 入口预算门（`config.py` L116-117）现由 execution 手写节点在 `_maybe_interrupt_or_return`（L949-957）消费。ReAct 化后，"修复循环回合子预算"如何与"execution agent 自身 ReAct round 预算"对账，需架构师在 map_result / 编排层重新接（见 Q-A）。倾向：execution agent 的 `max_rounds` 承担子图内轮次上限，`retry_budget_remaining` 承担全局账本，`_dev_loop_llm_calls` 继续累计修复循环跨回合子预算——三者语义不变，只是扣减点从手写迁到 wrapper。

### 6.3 其余矩阵条目

- 矩阵 §A.4（DevLoopState / 共享 scratchpad）、supervisor / 三 agent 路由、Reviewer 职责、code_only 子图嵌入等——**均为路线甲专属，路线丙不涉及**。
- `FixLoopRecord` 的 3 个 Optional 扩展字段（reviewer_verdict / coder_confidence / agent_trace，矩阵 §D.2）——路线丙**不需要**（无 Reviewer），保持现状 5 字段。

---

## 7. 凭证用例专项（首要用例）

### 7.1 索要

由 coding / execution agent 在遇到缺凭证时调 `request_user_input(question="需要 GitHub 访问 token 以 clone 私有仓库 ...", is_sensitive=True, purpose_key="git_credential:github.com")`，走 §5.3 数据契约。

### 7.2 检测 / 分类止损（配合 §1.1 缺陷 3）

- **execution 侧**：`ErrorCategory`（`execution.py` L87-101）新增 `CREDENTIAL_REQUIRED`，归**不可自动修复类**（不进 `AUTO_FIXABLE` L105-111，故不消耗 `fix_loop_count`）；在硬件/数据缺失同层前增加凭证关键字匹配（`could not read username` / `authentication failed` / `terminal prompts disabled` / `permission denied (publickey)` 等）。命中后 execution agent **就地调 `request_user_input` 问凭证**（execution 现在是 agent，可直接问，不必再绕回 coding）。
- **git 子进程**：clone 时设 `GIT_TERMINAL_PROMPT=0` 经 `extra_env` 注入，让认证失败**立即返回**而非挂起等 stdin。
- 这样把"缺凭证打转直到 `MAX_FIX_LOOP_COUNT=10` 耗尽"改成"识别 → 就地问用户 → 注入 → 继续"。

### 7.3 注入 sandbox（S4-06）+ 现状缺口

拿到凭证后经 `extra_env` 注入执行子进程：

- **`run_in_venv`（L658）与 `_run_subprocess`（L304）已支持 `extra_env`**——`env = {**os.environ, **(extra_env or {})}`，可直接注入。
- **缺口（须补）**：`prepare_venv`（L444-451）签名**无 `extra_env` 形参**，且现状 execution 调用 `prepare_venv` / `run_in_venv` 时**均未传 extra_env**。S4-06 需：(a) 给 `prepare_venv` 补 `extra_env` 形参并透传给内部 pip install 子进程（pip 装私有源依赖也可能需凭证）；(b) sandbox 工具（§4.2.2）把已收集凭证注入 `extra_env`。
- git 凭证注入方式（`GIT_ASKPASS` vs 带 token 的 remote URL 改写）+ `purpose_key → env var 名` 映射 → 交架构师细化。

### 7.4 凭证存储（S4-07，Maria 已定）

- 用户勾「记住」+ 提交 → 凭证写入**独立 `.secrets` 文件**（明文，权限 **0600**，**必须在 `.gitignore`**，MVP **不加密**）。新增 config 常量指向该路径（建议项目级或 `workspace_dir/.secrets`）。当前仓库 `.gitignore` 已被修改，需确认 `.secrets` 路径在忽略清单内。
- key 用 `purpose_key`；后续复现遇同一 `purpose_key` 先查 `.secrets`，命中则直接注入、**不再问用户**（去重 + 跨任务复用，Maria 已定按 purpose_key 粒度）。
- 不勾「记住」→ 仅本次任务内存中使用，任务结束即丢弃，不落盘。
- **敏感值完全不进 checkpoint / state**（Maria 已定，见 §8）。

---

## 8. 安全

| 项 | 要求 |
|---|---|
| 脱敏-代码 | 凭证**绝不写进生成的代码文件**，只经 `extra_env` 注入子进程 |
| 脱敏-日志 | coding / execution 的日志、`ExecutionResult.logs`、`run_command`/`run_in_venv` 的 stdout/stderr、git stderr 含 token 的 URL 中凭证值必须脱敏（`****`） |
| 脱敏-checkpoint / state | 敏感值**完全不进 GlobalState / SqliteSaver checkpoint**（Maria 已定，扩展技术架构 §8.4 "api_key 不持久化"约定到所有凭证）。`collected_inputs` 只存非敏感项；`pending_user_input` 只存问题快照不存答案 |
| 脱敏-报告 | reporting 生成的 Markdown 不泄露任何凭证明文 |
| `.secrets` 权限 | 0600、在 `.gitignore`、仅本地（MVP 不加密，不上传） |
| 注入边界 | 凭证 env var 只注入复现任务子进程（sandbox `extra_env`），不污染主进程长期环境 |
| sandbox 护栏不退化 | `run_command` / sandbox 工具复用现有 4 护栏（禁 shell=True / 进程组隔离 / 超时杀子树 / 输出截断 / cwd 限定 WORKSPACE_DIR），agent 化不得放松 |

> **交架构师**：checkpoint 脱敏落地方式（Maria 已定"完全不进 state"，架构师确认敏感值内存旁路的传递路径不经任何 checkpoint 落盘点）、日志脱敏 filter 的统一落点，是安全关键实现，须架构师设计 + 测试工程师验证（AC-S4-11/12）。

---

## 9. 四个"丙方向特有"设计问题的回答

### Q-A：execution 变 agent 后，interrupt#2（dev_loop_failure）+ 修复循环边界怎么安置？

**背景（代码实证）**：现状这些逻辑在 execution 手写节点体内——错误分类 `_classify_execution`（L167）、B 档成功判定 `_build_execution_result`（L687，success = exit 全 0 且 ≥1 指标）、修复循环边界 `_maybe_interrupt_or_return`（L925：入口预算门 → auto_fixable 且 `fix_loop_count < MAX_FIX_LOOP_COUNT` 且 `dev_calls < MAX_DEV_LOOP_LLM_CALLS` → 回 coding；否则 interrupt#2）、`fix_loop_count` 单点自增（L958）、interrupt#2 重跑幂等的 commit 边界（`_dev_loop_route="await_dev_loop_interrupt"` + self-loop 重入 + `_has_committed_result_for_round` guard L1006）。

**产品层结论（倾向"薄编排包住 ReAct 子图"，最终由架构师定）**：

- **保留一层薄手写编排 wrapper 包住 execution ReAct 子图**（而非把这些逻辑塞进 ReAct 的 map_result）。理由：interrupt#2 的重跑幂等契约（sandbox 副作用恰为 1）依赖 sp3 已踩通的"commit 边界 + self-loop 重入"机制（`execution.py` L12-20 文档化的 S-1 CP-S-3），这套机制是**主图节点级**的（依赖 `_dev_loop_route` self-loop `graph.py` L144-145），**不适合下沉到 ReAct 子图内部**。
- 分层建议：
  - **ReAct 子图内**（execution agent）：自主决策"跑哪些命令、看结果、要不要问用户"，产出 sandbox 运行结果 + 收产物。
  - **确定性收尾**（map_result / 编排层）：`_classify_execution` 错误分类、`_parse_metrics` 指标解析、`_build_execution_result` B 档判定——保持确定性，**不交给 agent 自由裁量**（避免 agent 谎报成功）。
  - **主图编排层**（薄 wrapper）：修复循环边界 + `fix_loop_count` 自增 + interrupt#2 + commit 边界 self-loop——**原样保留** sp3 的 `_maybe_interrupt_or_return` + `_route_after_execution` + guard 机制。
- **列为待架构师细化的开放问题**（见 §11 表）：ReAct wrapper 的标准形态是"子图 invoke → map_result → return"，如何在其外再套一层"修复循环边界 + interrupt#2 + self-loop commit 边界"而不破坏 `_make_react_wrapper` 的预算扣减契约（L889-894），是本次最需要架构师给方案的点。倾向：execution 不直接用裸 `_make_react_wrapper`，而是"手写 execution 节点函数（保留七步骨架的第 3/5/7 步 = 分类/判定/边界+interrupt），内部第 1/2 步（准备+执行）替换为调用一个 ReAct execution agent 子图"——即**手写编排 + 内嵌 ReAct 子图**，与 planning 节点"手写复合 + 内嵌 ReAct 子图 + interrupt#1"（`planning.py`）**完全同范式**。

### Q-B：coding 有 `run_command` 后，与 execution agent 的职责边界怎么划？

**产品层建议（非定论，供架构师/Maria 确认）**：

- **coding agent**：写代码 + **轻量验证/smoke**——用 `run_command` 做"这段代码 import 得通吗 / 入口脚本能启动吗 / 语法/依赖有没有低级错误"这类**秒级、无副作用、不跑真数据**的自查，就地修掉低级错误，减少绕整个修复循环的成本。
- **execution agent**：**完整复现执行**——跑 `execution_steps`（可能是长时训练/评估）、`collect_artifacts` 收产物、`_parse_metrics` 解析指标、`_classify_execution` 分类、B 档判定。这是"真正的复现"，成本高、有副作用、产出是最终交付依据。
- **边界红线（防两 agent 干重叠的事）**：
  1. `run_command` 的工具 docstring 明确"仅用于轻量验证，禁止用于完整训练/评估/下载大数据集"；建议给 `run_command` 设一个远小于 `SANDBOX_EXEC_TIMEOUT=1800s` 的短超时（如 60-120s），从机制上防 coding 拿它跑重活。
  2. **B 档成功判定只认 execution agent 的产出**（`execution_result`），coding 的 smoke 成功**不算复现成功**——避免 coding 用 run_command 跑出个假指标就宣告成功。
  3. coding 若在 smoke 中发现缺凭证/缺数据，可就地 `request_user_input`，但**完整执行仍交 execution**。

### Q-C：`request_user_input` 在 ReAct loop 内 interrupt 的重跑幂等

Maria 已定：**不单独 spike、边开发边验**。本 PRD 将其**显式列为开发中头号重点验证项**：

- 风险：工具内 `interrupt()` 在 `Command(resume=...)` 恢复时，LangGraph 对 ReAct 子图的重跑范围（reasoning 节点从头重跑？前面已执行的 write_code_file / run_command 是否重放？messages 历史是否完整恢复？）与 execution 手写节点的"整节点重跑"机理不同（`execution.py` L12-20）。
- 若前面的写文件/运行命令在 resume 后被重放，可能产生副作用重复（如重复写文件、重复 clone）。
- **开发中必须验证**：在 coding / execution 两 agent 上各构造一个"调 write_code_file / run_command → 再调 request_user_input → resume"的场景，断言 resume 后不重复执行前序有副作用工具（副作用恰为 1）。若实测异常，再补 spike（类比 sp2 S-1 spike）。

### Q-D：两 agent 松耦合的通信契约

见 §4.3。**结论**：沿用现状 `execution_result`（含 `[error_category=...]` 前缀）+ `fix_loop_history` 经 GlobalState 单点写回 → 修复回边 → coding `_build_coding_context` 消费。路线丙**不引入** DevLoopState / CodingOutput / ExecutionFeedback（路线甲子图私有 schema）。新增的唯一 schema 变化是 `ErrorCategory` 增 `CREDENTIAL_REQUIRED`（§7.2）。

---

## 10. 验收标准（AC）

| 编号 | 验收标准 | 可测方式 |
|---|---|---|
| AC-S4-01 | coding agent 工具集含 `run_command`；能在 code_output_dir 下运行命令、拿回 exit_code + stdout/stderr，越界命令被拒 | mock：断言工具存在 + 越界拒绝 + 正常返回 |
| AC-S4-02 | `run_command` 复用 sandbox 护栏（禁 shell=True / 超时 / 输出截断 / cwd 限定 WORKSPACE_DIR），且超时短于 execution 的 `SANDBOX_EXEC_TIMEOUT` | mock：构造越界/超时断言护栏生效 |
| AC-S4-03 | `execution` 节点改造为内嵌 ReAct execution agent（保留节点名 `execution`，7 节点集合不变，AC-S3-10 不破坏） | 结构测试：`build_graph` 仍 7 节点、节点名集合不变 |
| AC-S4-04 | execution ReAct 化后自动走 `_make_react_wrapper` 预算扣减（或等价的 round 扣减），`retry_budget_remaining` 按实际 round 递减，不双重扣减 | mock：断言一次 execution 后 `retry_budget_remaining` 递减 = round 数 |
| AC-S4-05 | interrupt#2（`dev_loop_failure`）+ 修复循环边界（`fix_loop_count` 自增 / B 档判定 / commit 边界 self-loop 重跑幂等）在 execution agent 化后行为不回归 | 复用 sp3 C3/D1/E2 用例回归全绿 |
| AC-S4-06 | `request_user_input`（单一工具，字段仅 question/is_sensitive/purpose_key）在 coding / execution 调用均触发 `interrupt_kind="user_input_request"` 暂停，`Command(resume={"value":...})` 后 agent 拿到值继续 | mock + e2e（Command(resume) 模拟输入） |
| AC-S4-07 | git clone 认证失败被分类为 `credential_required`（不进 AUTO_FIXABLE、不消耗 fix_loop_count），触发 `request_user_input` | mock：构造认证失败 stderr 断言分类 + 不自增 fix_loop_count |
| AC-S4-08 | 拿到凭证经 `extra_env` 注入 sandbox（`prepare_venv` 补 `extra_env` 形参 + `run_in_venv` 均注入），后续 clone/下载成功 | e2e（需授权真凭证，省配额范式）或 mock 注入断言 |
| AC-S4-09 | 用户勾「记住」→ 凭证写入 `.secrets`（0600、在 .gitignore）；同 purpose_key 复跑不再问、直接注入 | mock + 文件权限/gitignore 断言 |
| AC-S4-10 | UI 执行监控页对 `user_input_request` 渲染问题 + 单输入框（敏感 password）+「记住」勾选，提交走 resume_with | 手动 happy path（`streamlit run`） |
| AC-S4-11 | 凭证不出现在：生成代码 / `ExecutionResult.logs` / checkpoint DB / 报告 Markdown（grep 全链路无明文） | e2e：注入已知 token 后 grep 工作目录 + checkpoint + 报告 |
| AC-S4-12 | 日志中凭证值脱敏为占位符 | mock：caplog 断言无明文 |
| AC-S4-13 | 三类 interrupt（planning / dev_loop_failure / user_input_request）在同一 thread_id 下互不干扰、各自正确路由 | e2e：覆盖三 interrupt 串行 |
| AC-S4-14 | Q-C 重跑幂等：coding / execution 中"有副作用工具（write/run_command）→ request_user_input → resume"后前序工具不重放（副作用恰为 1） | 开发中重点验证 + mock 断言 |

---

## 11. 开放问题清单（待 Maria 拍板 / 交架构师）

| 编号 | 问题 | PM 倾向建议 |
|---|---|---|
| **Q-A1** | execution ReAct 化后，interrupt#2 + 修复循环边界 + commit 边界 self-loop 的**安置形态**：薄手写编排包住 ReAct 子图（类比 planning），还是塞进 ReAct 的 map_result？ | 强烈建议**手写编排 + 内嵌 ReAct 子图**（与 planning 同范式），保留 sp3 已踩通的 commit 边界 self-loop 重跑幂等机制。交架构师给具体嵌入方案 |
| **Q-A2** | execution agent 自身 ReAct `max_rounds` 与修复循环子预算（`MAX_DEV_LOOP_LLM_CALLS=60` / `DEV_LOOP_MIN_CALLS_PER_ROUND=2` / `fix_loop_count`）如何对账，避免双重扣减 | 交架构师：新增 `REACT_MAX_ROUNDS_EXECUTION` 常量承担子图内轮次上限；`retry_budget_remaining` 由 wrapper 扣减；`_dev_loop_llm_calls` 继续累计跨回合子预算 |
| **Q-B1** | coding `run_command` 与 execution sandbox 工具是否共用同一 venv？`run_command` 的短超时取值？ | 建议 run_command 只做无需 venv 的极轻量 smoke（或复用 code_output_dir 的 venv），超时 60-120s（远小于 1800s） |
| **Q-C1** | ReAct 工具内 interrupt 的重跑幂等是否需要正式 spike？ | Maria 已定：**不单独 spike、边开发边验**（AC-S4-14 强制断言）；实测异常再补 |
| **Q-D1** | 通信契约是否需要在 `ErrorCategory` 外新增任何字段？ | 不需要，沿用现状 `execution_result` + `[error_category=...]` + `fix_loop_history`，仅增 `CREDENTIAL_REQUIRED` 分类 |
| **Q-E1** | `.secrets` 具体路径（项目级 vs `workspace_dir/.secrets`）+ 读取优先级 + 与 config LLM api_key "不持久化"约定的一致性 | 交 Maria + 架构师共拍；PM 倾向 `workspace_dir/.secrets`（0600 + gitignore，MVP 不加密），敏感值完全不进 state |
| **Q-F1** | 交互超时 / 无人应答 | Maria 已定：**一直暂停**（checkpoint 天然保留），不设硬超时；超时降级列 v1.x |
| **Q-F2** | 无交互前端（CLI/CI）时 `request_user_input` 行为 | Maria 已定：**按信息缺失降级**、不死等；完整 CI 策略后置 |
| **Q-F3** | 批量缺信息 | Maria 已定：**逐条问**；批量表单列 v1.x |

---

## 12. Sprint 归属与拆分建议

### 12.1 归属

本 PRD = **Sprint 4 的核心方向**（路线丙）。它既修复了真跑 arXiv:2202.12837 暴露的端到端硬伤（缺凭证打转），又完成了"两个 agent 松耦合"的形态升级，是后续任何增强（若将来重议 multi-agent）的形态基础。

### 12.2 拆分建议（依赖顺序）

1. **交互工具地基**：S4-05（单一 `request_user_input` 工具，含 interrupt#3 payload + Command(resume) 契约）+ S4-10（state 字段 `pending_user_input` / `collected_inputs`）——风险低、被后续复用。开发中即验 Q-C（AC-S4-14）。
2. **coding 补工具**：S4-01/S4-02（`run_command` + 挂 `request_user_input`）——coding 已是 ReAct，改动局部（`_get_coding_tools`）。
3. **execution 改造（最大工作量 + 最高风险）**：S4-03/S4-04（ReAct execution agent + sandbox 工具化 + Q-A 编排安置）——须先由架构师给 Q-A1/Q-A2 方案，再开发；用 sp3 C3/D1/E2 回归守门（AC-S4-05）。
4. **凭证闭环**：S4-06/S4-07（`prepare_venv` 补 `extra_env` + 注入 + `credential_required` 分类 + `.secrets` 存储 + 全程脱敏）。
5. **UI**：S4-09（执行监控页 `user_input_request` 分支）。
6. **验收**：AC-S4-01~14 全覆盖。

---

## 产品结论小结（对照工作方式的四段式）

- **当前理解**：Sprint 4 确定走路线丙——coding（补 run_command + request_user_input）与 execution（改造为内嵌 ReAct 的 execution agent，挂 sandbox 工具 + request_user_input）升级为两个松耦合真 agent，经 state 结构化反馈通信，7 节点骨架不变，交互工具单一极简。
- **待确认问题**：§11 表中 Q-A1/Q-A2（execution ReAct 化的编排安置与预算对账，须架构师方案）、Q-B1（run_command 边界参数）、Q-E1（.secrets 路径）为进开发前的关键缺口。
- **需求结论**：见 §2（MVP 10 项 + 非目标）、§3-§5（两 agent 形态与工具）、§9（四设计问题回答）、§10（AC-S4-01~14）。
- **下一步建议**：Q-A1/Q-A2 是 execution ReAct 化能否安全落地的命门，明确触发"需要架构师参与的技术方案"条件（多方案权衡 + 跨组件编排 + 预算对账），建议本 PRD 落盘后**立即转架构师**评估 execution 的"手写编排 + 内嵌 ReAct 子图 + 保留 commit 边界 self-loop"方案、run_command/sandbox 工具的 venv 与护栏落点、以及凭证注入的 extra_env 映射与脱敏 filter 统一落点。
