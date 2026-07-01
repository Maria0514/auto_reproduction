# Sprint 3 dev_loop multi-agent 子图与现有错误追踪 / 修复循环 / 预算字段兼容性矩阵

> 评估代理：架构师代理（只读评估，未改动任何代码）
> 评估日期：2026-06-14
> 评估对象：将 coding↔execution 单 agent 修复循环升级为 LangGraph supervisor 模式 multi-agent 子图（Coder / Executor / Reviewer + 共享 scratchpad + supervisor 路由）
> 权威依据：`core/state.py`（字段定义以此为准）、`core/react_base.py`、`core/graph.py`、`core/checkpointer.py`、`config.py`；架构意图依据 `docs/technical-architecture.md`
> 立项判定结论：**条件性可立项**——无阻塞级数据契约冲突，但存在 2 项 must-fix-before-PRD 的设计前置（见 §D）
>
> **后续默认参数变更（2026-06-30，Maria 拍板）**：本矩阵正文中对 `config.py` 的取值引用（`MAX_FIX_LOOP_COUNT=3`、`MAX_TOTAL_LLM_CALLS=50`、新增 `MAX_DEV_LOOP_LLM_CALLS` 建议 ≤20 等）是 **2026-06-14 立项评估时刻的代码取证快照**，保留以维持审计可追溯。Sprint 3 落地后这三常量默认值已放大为 `MAX_FIX_LOOP_COUNT=10` / `MAX_DEV_LOOP_LLM_CALLS=60` / `MAX_TOTAL_LLM_CALLS=120`（强约束 60 < 120 不变）。当前生效值以 `config.py` 与 PRD/架构文档顶部注记为准；矩阵的兼容性结论（字段语义、reducer 红线、预算扣减缺口）不受默认值放大影响，依然成立。
>
> **⚠️ 方向调整（2026-06-14 Maria 决策）**：本矩阵原服务于「Sprint 3 = dev_loop 真 multi-agent」。Maria 已决定 **Sprint 3 重心改为「先打通端到端复现」（单 agent 修复循环），dev_loop 真 multi-agent 顺延到 sp4+**。本矩阵中**非 multi-agent 专属的发现对 sp3 端到端复现（单 agent 修复循环）同样适用**——尤其 §A.3/§B 的 must-fix-2（预算扣减缺口）、§A.2 修复循环死字段激活、§C.3 code_only 路由、§0.3 占位节点现状、§C.1 把 coding/execution 节点位换成真实现的嵌入范式。仅 §A.4（DevLoopState / 共享 scratchpad）、supervisor / 三 agent 路由、§D.3 的 must-fix-1（多 agent 并发写无 reducer 的 list）属 **multi-agent 专属，服务 sp4+**。

---

## §0 评估范围与方法说明

### 0.1 评估目标
回答一个 sp3 PRD 立项的前置问题：现有 GlobalState 里的错误追踪字段（`NodeError` / `node_errors` / `degraded_nodes`）、修复循环字段（`fix_loop_count` / `fix_loop_history` / `FixLoopRecord` / `user_fix_decision`）、预算字段（`retry_budget_remaining` + `MAX_TOTAL_LLM_CALLS`），在 Coder/Executor/Reviewer + supervisor 三 agent 子图语义下能否复用、需扩展还是需新增，是否存在阻塞性不兼容。

### 0.2 方法
- 字段语义与类型一律以 `core/state.py` 实际定义为准；凡架构文档与代码不一致，**以代码为准并显式标注偏差**。
- 预算机制以 `core/react_base.py::_make_react_wrapper`（预算扣减点）与 `budget_check_node`（子图内轮次上限）实证为准。
- 主图骨架不变性以 `core/graph.py::build_graph` 实证为准。
- 标注每条结论的证据等级：**[代码实证]** = state.py / *.py 直接读出；**[文档意图]** = 仅架构文档描述、代码尚未落地。

### 0.3 当前实现现状（关键前提，[代码实证]）
1. **`core/subgraphs/` 目录不存在**；`DevLoopState` / `CodingOutput` / `ExecutionFeedback` 在整个代码库中无任何定义（架构文档 §3.2.2 / §5 描述的 `core/nodes/dev_loop.py` 等均未落地）。dev_loop 当前是**纯文档意图**。
2. **`coding` / `execution` / `reporting` 是 pass-through 占位**（`core/graph.py` L57-72，返回 `{}`，不修改状态）。主图当前是 `coding → execution → reporting` 三条顺序边，**没有 execution↔coding 修复回边**。
3. **`fix_loop_count` / `fix_loop_history` / `user_fix_decision` / `FixLoopRecord` / `MAX_FIX_LOOP_COUNT` 当前为"死字段/死常量"**——除 `create_initial_state` 写默认值（`fix_loop_count=0`、`fix_loop_history=[]`、`user_fix_decision=None`）与一条 smoke 测试、一条 config 测试断言外，**无任何生产逻辑读写**（全库 grep 实证）。这是一个有利条件：sp3 重新定义其语义不会破坏任何既有行为。
4. 预算扣减**唯一发生点**是 `_make_react_wrapper._wrapper`（`core/react_base.py` L889-894）：`remaining = max(0, retry_budget_remaining - rounds_used)`，按 ReAct 子图实际 `round` 数扣减，至少扣 1。`budget_check_node` 只管子图内 `max_rounds` 轮次上限，**不读写全局 `retry_budget_remaining`**。

---

## §A 核心兼容性矩阵

字段类型/语义列严格抄自 `core/state.py`。"证据"列标注 [代码] / [文档] / [偏差]。

### A.1 错误追踪字段

| 字段 | 当前类型/语义（state.py 实证） | multi-agent 子图下的新语义 / 归属问题 | 冲突 / 扩展 / 新增 | 缓解方案 / 设计约束 | 证据 |
|---|---|---|---|---|---|
| `NodeError.node_name` | `str`，自由文本，由 `make_node_error(node_name, ...)` 写入。当前各节点传入固定节点名（如 `"paper_analysis"`） | 子图内 Coder/Executor/Reviewer 三个 agent 都可能产错。`node_name="dev_loop"` 会丢失"是哪个 agent 出错"的粒度 | **不冲突，但需约定值规范**。无需改类型（str 足够） | 约定子图内写 `node_name = "dev_loop.coder" / "dev_loop.executor" / "dev_loop.reviewer"`（点分命名空间）；`exit_dev_loop` 冒泡到主图时保留该前缀。**不新增 agent 级 NodeError 子类型**，靠 node_name 约定承载粒度 | [代码] errors.py L169 / state.py L142 |
| `NodeError.error_type` | `str`，约定取值 `"transient"\|"permanent"\|"degraded"`（架构 §12.3） | Executor 的运行期错误是新的一类（syntax/import/runtime/oom/timeout），与现有 transient/permanent/degraded 不同维度 | **不冲突**。运行期错误细分应落在**子图内部状态**（见 A.4 scratchpad），不污染 `error_type` 三态 | 子图内运行期错误分类用独立字段（`ExecutionFeedback.error_type`，子图私有）；冒泡到主图 `NodeError.error_type` 时映射为三态之一（如运行失败但还能重试→`transient`，预算耗尽放弃→`permanent`，降级交付→`degraded`） | [文档] §3.2.2 ExecutionFeedback / [代码] state.py L143 |
| `NodeError`（整条 TypedDict） | 7 字段：node_name/error_type/error_message/error_detail/timestamp/retry_count/resolved | 子图冒泡：一轮修复多 agent 多错，是逐条冒泡还是汇总成一条？`retry_count` 在子图语境下是"agent 重试次数"还是"修复轮次"？ | **不冲突，需冒泡策略**。`retry_count` 语义需约定 | 约定 `exit_dev_loop` 只冒泡"导致子图终止的代表性错误"为 NodeError（避免 node_errors 被几十条子图内部错误淹没）；逐轮明细进 `fix_loop_history`（见 A.2）。`retry_count` 在子图 NodeError 上填"修复轮次数" | [代码] state.py L140-148 |
| `node_errors: List[NodeError]` | **普通 `List`，无 `operator.add` reducer**。既有节点用读-改-写整列表替换（`list(state.get("node_errors",[]))` → append → 返回整列表） | **关键风险**：supervisor 子图若让 Coder/Executor/Reviewer 各自作为图节点并行/分别返回 `node_errors`，LangGraph 默认对无 reducer 的字段是**整值覆盖（last-write-wins）**，会丢错误记录 | **需设计约束（非字段改动）**。两条路线选一 | 路线 1（推荐）：子图内部错误**不直接写 GlobalState `node_errors`**，而是写子图私有的 conversation/feedback；仅在 `exit_dev_loop` 单点做一次读-改-写合并回 GlobalState（沿用现有 read-modify-write 范式，天然安全）。路线 2：给 `node_errors` 加 `operator.add` reducer——但这会改变全局合并语义，**牵连 sp1/sp2 所有节点的整列表替换写法**（它们 return 整列表，加 reducer 后会重复累加），属 breaking change，不推荐 | [代码] state.py L189（无 Annotated）、paper_analysis.py L436 读-改-写 |
| `degraded_nodes: List[str]` | **普通 `List`，无 reducer**，去重追加（节点内 `if NODE_NAME not in degraded_nodes: append`） | 子图降级时该写 `"dev_loop"` 还是 `"dev_loop.coder"`？多 agent 各自降级如何合并？ | 同 `node_errors`，**同一覆盖风险** | 同上路线 1：`exit_dev_loop` 单点合并写入。降级标识建议写聚合节点名 `"dev_loop"`（粗粒度足够支撑降级决策；细粒度留给 fix_loop_history） | [代码] state.py L190 / paper_analysis.py L405 |

### A.2 修复循环字段（当前为死字段，sp3 首次激活，重定义自由度高）

| 字段 | 当前类型/语义（state.py 实证） | multi-agent 子图下的新语义 / 归属问题 | 冲突 / 扩展 / 新增 | 缓解方案 / 设计约束 | 证据 |
|---|---|---|---|---|---|
| `fix_loop_count: int` | `int`，默认 0。**无任何生产代码读写**。架构文档注释"上限 5"，但 config 是 `MAX_FIX_LOOP_COUNT=3` | supervisor 整轮计数 vs 单 agent 调用计数？建议=**一个 Coder→Executor(→Reviewer) 完整修复回合 = +1**（不按单 agent 调用计数） | **可直接复用，语义需在 PRD 钉死**。无类型改动 | 钉定义为"supervisor 完成的完整修复回合数"。**与架构文档"上限 5"和 config `MAX_FIX_LOOP_COUNT=3` 存在数值偏差（见 §D 偏差表）**——sp3 PRD 必须二选一统一 | [代码] state.py L192 / config.py L32 [偏差] 文档 L1191 写 5 |
| `fix_loop_history: List[FixLoopRecord]` | `List[FixLoopRecord]`，默认 `[]`。**无生产代码读写**。同样**无 reducer** | 每个修复回合一条记录，承载"是哪个 agent、什么错误、什么策略" | **可直接复用**。但写入需走 read-modify-write 单点合并（同 A.1 list 覆盖风险） | 子图每完成一回合 append 一条 FixLoopRecord；统一在 `exit_dev_loop` 或 supervisor 节点单点读-改-写。**这是承载 multi-agent 细粒度审计的主通道**（避免 node_errors 膨胀） | [代码] state.py L193（无 Annotated） |
| `FixLoopRecord`（TypedDict） | 5 字段：round_number/error_summary/error_category/fix_strategy/timestamp | 字段是单 agent 视角（"execution 失败的错误 + coding 的修复策略"）。三 agent 下缺"哪个 agent 产出""Reviewer 评审意见""信心度" | **建议扩展（向后兼容追加 Optional 字段）** | 追加 `Optional` 字段：`reviewer_verdict: Optional[str]`、`coder_confidence: Optional[float]`、`agent_trace: Optional[str]`。**追加 Optional 字段不破坏现有默认值**（当前无人写它，零回归风险） | [代码] state.py L151-158 |
| `user_fix_decision: Optional[str]` | `Optional[str]`，默认 None。约定取值 `"export_code"\|"revise_plan"\|"terminate"`（架构 §4 注释） | dev_loop 失败 interrupt 后用户三选一的载体，与 sp2 planning 的 5 类 resume payload 是**不同 interrupt 点** | **可直接复用**，无改动。新增的是"第二个 interrupt 点"的路由逻辑，不是字段问题 | sp3 在主图层为 dev_loop 失败新增 interrupt（架构 §3.3 "dev_loop 后路由"）；resume payload 写入 `user_fix_decision`，由 dev_loop 后条件路由消费。注意与 sp2 planning interrupt 复用同一 thread_id（见 §C） | [代码] state.py L194 |

### A.3 预算字段

| 字段 | 当前类型/语义（state.py 实证） | multi-agent 子图下的新语义 / 归属问题 | 冲突 / 扩展 / 新增 | 缓解方案 / 设计约束 | 证据 |
|---|---|---|---|---|---|
| `retry_budget_remaining: int` | `int`，默认 `MAX_TOTAL_LLM_CALLS=50`。**唯一扣减点** = `_make_react_wrapper` 按 ReAct round 数扣减（react_base.py L889-894） | dev_loop 子图**不走 `_make_react_wrapper`**（它是手写 supervisor 子图，非 ReAct wrapper）。若不主动扣减，子图所有 LLM 调用**对全局预算完全透明**——预算永远扣不到 dev_loop 头上；反之若三 agent 多轮疯狂调用，无全局账本约束 | **需新增扣减逻辑（非字段改动）**。见 §B 详述 | dev_loop 子图必须在 `exit_dev_loop`（或 supervisor 每回合）**主动读-改-写 `retry_budget_remaining`**，按子图内实际 LLM 调用数扣减。这是 sp3 必须补的逻辑缺口（占位节点目前零扣减） | [代码] state.py L191 / react_base.py L889-894（扣减只在 ReAct wrapper） |
| `MAX_TOTAL_LLM_CALLS` (config=50) | 全局硬上限 | 单轮修复触发 Coder+Executor+Reviewer 多次调用，最坏情形子图独吞剩余预算 | 见 §B | 引入**子图级子预算**（见 §B），并在子图入口检查全局剩余预算是否够启动 | [代码] config.py L31 |
| `MAX_FIX_LOOP_COUNT` (config=3) | 修复轮次上限常量。**无生产代码引用**（仅一条测试断言） | 是否=supervisor 回合上限？数值 3 与架构文档"5"冲突 | **需统一数值 + 首次接线** | sp3 PRD 钉定 supervisor 回合上限取值并统一 config 与文档；首次在 dev_loop evaluate/supervisor 路由中引用该常量 | [代码] config.py L32 / tests L128 [偏差] 文档 L1155/1191 写 5 |
| `MAX_DEV_LOOP_LLM_CALLS` (=20) | **不存在于 config.py**（架构文档 §3.2.2/§12.6 声称有） | 文档假定的子图级 LLM 预算 | **需新增 config 常量** | sp3 在 config.py 新增 `MAX_DEV_LOOP_LLM_CALLS`（建议 ≤ 20 且 < 50，见 §B）。当前是文档与代码的纯缺口 | [偏差] 文档 L258/L1156 有，config 无 |

### A.4 子图新增字段需求（GlobalState 层 vs 子图私有层）

| 需求 | 放 GlobalState 还是子图私有 | 冲突/新增 | 设计约束 | 证据 |
|---|---|---|---|---|
| **共享 scratchpad / conversation** | **子图私有 DevLoopState**（`Annotated[List, operator.add]`，与 `ReActState.messages` 同范式），**不进 GlobalState** | **新增（子图私有）** | 仿 `ReActState`（react_base.py L48-56）做 `DevLoopState`，与 GlobalState 完全隔离；只在 enter/exit 做映射。**避免把高频追加的 scratchpad 灌进 GlobalState 撑爆 checkpoint** | [代码] react_base.py L48-56 ReActState 隔离范式 |
| 子图内部修复轮次 `round` / `max_rounds` | 子图私有 DevLoopState | 新增（子图私有） | 同 ReActState 的 round/max_rounds；与全局 `fix_loop_count` 区分——内部 round 是过程量，`fix_loop_count` 是冒泡后的结果量 | [代码] react_base.py L52-53 |
| Executor 结构化反馈 `ExecutionFeedback` | 子图私有 + 冒泡摘要进 `fix_loop_history` | 新增（子图私有） | 完整 ExecutionFeedback 留子图；冒泡时压缩进 FixLoopRecord 的 error_summary/error_category | [文档] §3.2.2 |
| `execution_result` / `code_output_dir` | **复用 GlobalState 既有字段** | 不冲突 | dev_loop 最终结果走既有 `ExecutionResult` / `code_output_dir`（state.py L176-177），`exit_dev_loop` 单点写入 | [代码] state.py L176-177, L129-138 |

---

## §B 预算总账

### B.1 现状（[代码实证]）
- 全局预算 `retry_budget_remaining` 初始 = `MAX_TOTAL_LLM_CALLS = 50`。
- 扣减**只发生在** `_make_react_wrapper`，按 ReAct 子图 `round` 数扣（react_base.py L889-894）。当前链路 paper_intake/paper_analysis/resource_scout/planning 共四个 ReAct/复合节点扣减，coding/execution 占位**零扣减**。
- `budget_check_node` 仅约束**子图内** `max_rounds`，不碰全局账本。

### B.2 multi-agent 子图对预算的冲击
1. **预算被快速耗尽的真实风险（中-高）**：一个完整修复回合 = Coder（1+ 次 LLM）+ Executor 结果分析（1 次 LLM）+ Reviewer（1 次 LLM）≈ 3+ 次/回合。架构文档假定上限 20 次 LLM 调用/子图。但前序四节点已消耗部分预算（典型 paper_analysis max_rounds=12），进 dev_loop 时剩余预算可能已不足 20——**子图可能在第一回合就被全局预算掐断，或反之子图把剩余预算一次吃光导致后续无预算**。
2. **量纲不一致**：现有扣减是 ReAct **round** 口径；dev_loop 文档是 LLM **调用次数**口径（同 S2-12 裁定里指出的"round 口径 vs 次数口径量纲不同"问题）。子图必须自己按"实际 LLM 调用次数"扣减，不能套用 round 口径。

### B.3 建议（需 sp3 PRD 决策）
- **必须引入子图级子预算 `MAX_DEV_LOOP_LLM_CALLS`**（config 新增，建议初值 20，且强约束 `MAX_DEV_LOOP_LLM_CALLS < MAX_TOTAL_LLM_CALLS`）。
- **双重约束语义**：子图每回合检查 `min(子图内累计调用 < MAX_DEV_LOOP_LLM_CALLS, retry_budget_remaining > 0)`，任一触顶即终止子图并置 `needs_human_decision`。
- **入口预算门**：`enter_dev_loop` 检查 `retry_budget_remaining`，若不足以启动一回合（如 < 单回合最小调用数），直接降级（标记 degraded + 走 dev_loop 失败 interrupt），不进子图空转。
- **回写约束**：子图按**实际 LLM 调用次数**主动读-改-写 `retry_budget_remaining`（单点回写，避免 list 覆盖类问题——这是 int 字段，last-write-wins 是正确语义，但要确保只有一个回写点）。
- **开放问题**：子图与主图是否共享同一 50 预算池，还是 dev_loop 独立配额？推荐共享池（dev_loop 从剩余里扣），与现有"单任务总 50"语义一致。

---

## §C 主图骨架不变性

### C.1 7 节点 DAG 如何容纳 supervisor 子图（[代码实证 graph.py]）
当前主图：`START→paper_intake→paper_analysis→resource_scout→planning →(3路条件边)→ coding→execution→reporting→END`。

**保持 7 节点的可行嵌入方式（推荐方案）**：
- 把 supervisor 子图**编译后作为一个节点函数嵌入到现有 `coding` 节点位**（或合并 coding+execution 为单一 `dev_loop` 节点）。LangGraph 支持把 `compiled_subgraph` 当节点加入。
- 推荐：**保留 `coding` 节点名作为 dev_loop 子图入口 wrapper**（`enter_dev_loop` + `compiled_dev_loop.invoke` + `exit_dev_loop`），`execution` 节点降级为 pass-through 或并入。这样**主图节点集合数量与名称尽量不变**，满足"7 节点 DAG 骨架不变"。
- 这与 `_make_react_wrapper` 把 ReAct 子图包成单个主图节点的现有范式**完全同构**（react_base.py L797-903）——sp3 只是把"单 agent ReAct 子图"换成"supervisor multi-agent 子图"，wrapper 边界不变。

### C.2 interrupt / checkpointer 协同（[代码实证 checkpointer.py + 文档意图]）
- sp2 已落地"每线程独立 SqliteSaver 实例 + 共享 SQLite 文件 + WAL"（checkpointer.py L44-48），并验证 `interrupt()` 在节点函数体内调用（graph.py L149-151 注释）。
- **dev_loop 失败 interrupt**：架构 §3.3 要求子图失败时在**主图层** `exit_dev_loop` 触发 `interrupt()`（而非子图内部）。这与 sp2 planning 的 interrupt 范式一致（在节点函数体内 interrupt），**不破坏现有 checkpointer 模型**。
- **子图 checkpoint 与主图 thread_id 协同（关键约束）**：
  - LangGraph 1.1.10 子图作为节点嵌入时，子图状态默认**不单独持久化到外部 thread**，而是作为主图节点执行的一部分。若 dev_loop 子图**自带 checkpointer**，需用 `checkpoint_ns`（命名空间）隔离——sp2 spike S-2 已踩过 `config["configurable"]["checkpoint_ns"]` 强制要求的坑（TODO L50）。
  - **推荐约束**：dev_loop 子图**不挂独立 checkpointer**，让其状态随主图节点一次性执行（子图内部循环对主图是原子的一个"超级节点"）。子图中途断点恢复时整节点重跑（与架构 §12.6 "节点中途网络断开→整节点重跑"一致）。这是最不破坏 sp2 持久化模型的做法。
  - **若**未来要求 dev_loop 内部回合级断点续跑（中途崩溃不重跑前几回合），才需给子图挂 checkpointer + `checkpoint_ns=f"dev_loop:{thread_id}"`——这是 sp3 的开放问题，**建议 MVP 不做**（复杂度陡增，收益不明）。

### C.3 条件路由扩展（[代码实证 graph.py L136-147]）
sp3 需新增（不破坏现有 3 路 planning 路由）：
- planning→ 后增 `code_only` 分支语义（架构 §3.4：full→dev_loop / code_only→coding_only→reporting）。当前 graph.py 的 `_route_after_planning` 仅 self/next/end 三路，approve 和 code_only 都走 `next→coding`，sp3 需在 coding 后或 planning 路由处区分 mode。
- dev_loop→ 后新增条件路由（success/export_code/terminate→reporting，revise_plan→回 planning）。
- 这些是**加边**，不动既有边，骨架基线不破坏。

---

## §D 总体兼容性结论 + 给 sp3 PRD 的硬约束/建议清单

### D.1 总结论
**无阻塞级数据契约冲突，可立项**——核心有利条件：修复循环全部字段（`fix_loop_count`/`fix_loop_history`/`FixLoopRecord`/`user_fix_decision`/`MAX_FIX_LOOP_COUNT`）当前是**零生产引用的死字段**，sp3 首次激活时可自由钉定语义而无回归风险；`NodeError`/`execution_result`/`code_output_dir` 等可直接复用。但存在 **2 项 must-fix-before-PRD 的设计前置**和若干需在 PRD 钉死的开放问题。

### D.2 字段处置汇总

**可直接复用（零改动）**：
- `NodeError`（TypedDict 7 字段）—— 靠 `node_name` 点分命名空间约定承载 agent 粒度
- `user_fix_decision: Optional[str]` —— dev_loop 失败 resume 载体
- `execution_result: Optional[ExecutionResult]` / `code_output_dir: Optional[str]` —— 子图最终产出
- `retry_budget_remaining`（字段本身）—— 但需新增扣减逻辑（见下）

**需扩展（向后兼容追加，零回归）**：
- `FixLoopRecord` 追加 `Optional` 字段：`reviewer_verdict` / `coder_confidence` / `agent_trace`

**需新增（config 常量）**：
- `MAX_DEV_LOOP_LLM_CALLS`（文档声称有，config 实际缺失）

**需新增（子图私有，不进 GlobalState）**：
- `DevLoopState`（含 `Annotated[List, operator.add]` 的 scratchpad/conversation + round/max_rounds/status/needs_human_decision）
- `CodingOutput` / `ExecutionFeedback`

### D.3 阻塞性 must-fix-before-PRD（2 项）

> 这 2 项不是字段不兼容，而是 PRD 若不先回答会导致设计无法收敛/埋下回归。

1. **[MUST-FIX] `node_errors` / `degraded_nodes` / `fix_loop_history` 的 list 合并策略必须钉死为"子图内部不直写、`exit_dev_loop` 单点 read-modify-write 合并"**。原因：这三个字段在 state.py 是**无 `operator.add` reducer 的普通 List**（[代码实证]），multi-agent 子图若让多 agent 各自返回这些字段，LangGraph 会整值覆盖丢数据。**严禁**为图省事给这些字段加 reducer——那会破坏 sp1/sp2 所有现有节点的"return 整列表"写法（变成重复累加），是 breaking change。

2. **[MUST-FIX] 预算扣减缺口必须补上**。dev_loop 子图不走 `_make_react_wrapper`，当前占位节点对 `retry_budget_remaining` **零扣减**。PRD 必须规定子图按实际 LLM 调用次数主动回写预算 + 引入 `MAX_DEV_LOOP_LLM_CALLS` 子预算 + 入口预算门。否则要么预算永远扣不到 dev_loop（成本失控），要么子图吃光全局预算。

### D.4 文档↔代码偏差（PRD 须先消歧，否则设计无依据）

| 项 | 架构文档 | 代码实证 | sp3 须裁定 |
|---|---|---|---|
| 修复轮次上限 | §12.6/§3.2.2 写 **5**（`MAX_DEV_LOOP_ROUNDS=5`） | `config.MAX_FIX_LOOP_COUNT = 3` | 统一为同一数值并接线引用 |
| 子图 LLM 预算常量 | §3.2.2/§12.6 写 `MAX_DEV_LOOP_LLM_CALLS=20` | **config.py 中不存在** | 新增常量 + 钉数值 |
| `fix_loop_count` 上限注释 | state.py 文档版注释"上限 5"（technical-architecture.md L519） | 实际 state.py 无上限语义，config 是 3 | 统一注释与常量 |
| dev_loop 模块路径 | §5 写 `core/nodes/dev_loop.py` | TODO/sp3 预设产出是 `core/subgraphs/dev_loop/` 目录 | 统一目录约定（建议采纳 TODO 的 `core/subgraphs/dev_loop/`） |
| agent 数量 | §3.2.2 主述"双 agent"，括号注 sp3 扩三 agent | 代码无任何实现 | PRD 钉死 Coder/Executor/Reviewer 三 agent + supervisor |

### D.5 给产品经理写 sp3 PRD 的开放问题清单

1. **`fix_loop_count` 计数粒度**：确认 = "supervisor 完整修复回合数"（Coder→Executor→Reviewer 一轮 +1），而非单 agent 调用计数？
2. **修复回合上限取值**：3（config 现值）还是 5（文档现值）？统一后填哪个数？
3. **子图预算池模型**：dev_loop 从全局 50 共享池扣（推荐），还是独立配额？`MAX_DEV_LOOP_LLM_CALLS` 取值？
4. **dev_loop 失败 interrupt 的 resume 决策集**：沿用 `user_fix_decision` 三态（export_code/revise_plan/terminate）？是否新增 Reviewer 相关决策？
5. **Reviewer 的职责边界**：Reviewer 是只读评审（产出 verdict 影响 supervisor 路由），还是可直接改代码？这决定 scratchpad 写权限设计。
6. **子图断点续跑粒度**：dev_loop 子图作为主图"原子超级节点"（中途崩溃整节点重跑，推荐 MVP）还是回合级 checkpoint 续跑（需 `checkpoint_ns`，复杂度高）？
7. **code_only 路径**：sp3 是否同期落地 `coding_only` 节点 + planning→code_only 路由分支（架构 §3.4），还是 dev_loop 优先、code_only 后置？

### D.6 给 sp3 实现的设计约束（TODO/文件级，供后续开发参考，非本期落盘代码）
- 新建 `core/subgraphs/dev_loop/`：`dev_loop_state.py`（DevLoopState/CodingOutput/ExecutionFeedback，仿 `core/react_base.py::ReActState` 隔离范式）、`dev_loop.py`（build_dev_loop_graph + supervisor + coder/executor/reviewer 节点 + enter/exit_dev_loop）。
- `core/state.py`：仅追加 `FixLoopRecord` 的 3 个 Optional 字段（向后兼容）；**不给 `node_errors`/`degraded_nodes`/`fix_loop_history` 加 reducer**。
- `config.py`：新增 `MAX_DEV_LOOP_LLM_CALLS`；统一 `MAX_FIX_LOOP_COUNT` 与文档数值。
- `core/graph.py`：`coding` 节点位替换为 dev_loop wrapper（保持节点名/数量）；新增 dev_loop 后条件路由；planning→code_only 分支。
- 唯一的 GlobalState 写回点 = `exit_dev_loop`（错误/降级/修复历史/预算/结果集中读-改-写），复用 sp1/sp2 既有 read-modify-write 范式（参 paper_analysis.py L436 起）。

---

### 相关文件路径（绝对路径，供归档与后续设计引用）
- `core/state.py`（字段权威定义，L140-194 错误/修复循环字段、L191 预算字段）
- `core/react_base.py`（L48-56 ReActState 隔离范式、L609-617 budget_check、L797-903 _make_react_wrapper 唯一预算扣减点 L889-894）
- `core/graph.py`（L57-72 coding/execution 占位、L78-93 planning 路由、L136-147 条件边、L151-152 sp3 TODO）
- `core/checkpointer.py`（L44-48 WAL + check_same_thread=False）
- `config.py`（L31 MAX_TOTAL_LLM_CALLS=50、L32 MAX_FIX_LOOP_COUNT=3，缺 MAX_DEV_LOOP_LLM_CALLS）
- `core/errors.py`（L169 make_node_error 工厂）
- `docs/technical-architecture.md`（§3.2.2 dev_loop 意图、§4 字段、§12.6 / §12.7 预算表）

---

## 附录：主控抽查验证记录（2026-06-14）

落盘前由主控对架构师的 3 项 must-fix / 偏差关键 [代码实证] 断言做了独立抽查复核，全部证实无误：

| 抽查项 | 命令 | 结果 |
|---|---|---|
| `node_errors`/`degraded_nodes`/`fix_loop_history` 无 reducer | `grep -nE "Annotated|operator.add|node_errors|degraded_nodes|fix_loop_history" core/state.py` | 三字段均为普通 `List`（L189/L190/L193），全 state.py 无 `Annotated`/`operator.add` 出现 ✓ |
| `MAX_DEV_LOOP_LLM_CALLS` 缺失 + `MAX_FIX_LOOP_COUNT=3` | `grep -nE "MAX_DEV_LOOP_LLM_CALLS|MAX_FIX_LOOP_COUNT|MAX_DEV_LOOP_ROUNDS" config.py` | `MAX_FIX_LOOP_COUNT=3`、`MAX_TOTAL_LLM_CALLS=50` 存在；`MAX_DEV_LOOP_LLM_CALLS`/`MAX_DEV_LOOP_ROUNDS` 均不存在（文档写 5/20，偏差属实）✓ |
| 修复循环字段为死字段 | `grep -rnE "fix_loop_count|fix_loop_history|user_fix_decision" core/`（排除 state.py 定义/初始化） | 零生产引用 ✓ |

结论：本矩阵的关键代码实证可信，立项判定（条件性可立项 + 2 项 must-fix + 5 处偏差 + 7 个开放问题）成立。
