# Sprint 5 核心架构设计文档

**文档版本**：v1.0（第 1 段 / 共 3 段：本段为 Q-S5-5 ~ Q-S5-10 六项技术裁决；第 2 段为 S5-01~11 技术方案概要与 state/schema 变更；第 3 段为既有机制集成、风险与回归防线、任务拆分）
**日期**：2026-07-08
**作者**：架构师代理
**对应 PRD**：`docs/sprint5/prd.md` v1.0（Q-S5-1~4 已由 Maria 确认，2026-07-08）
**需求根因输入**：`docs/sprint5/manual-run-feedback.md`（11 条已核实反馈）
**体例参照**：`docs/sprint4/architecture.md`
**回归现场样本（只读勿清理）**：`workspace/2604.01687/` + `checkpoints.db` thread `task-9208a1a4b4f5`

> **常量取值校准（2026-07-08 代码实证）**：`config.py` 现值 `MAX_TOTAL_LLM_CALLS=120`（:31）/ `MAX_DEV_LOOP_LLM_CALLS=60`（:116）/ `DEV_LOOP_MIN_CALLS_PER_ROUND=2`（:117）/ `REACT_MAX_ROUNDS_CODING=12`（:118）/ `REACT_MAX_ROUNDS_EXECUTION=10`（:130）/ `MAX_FIX_LOOP_COUNT=10`（:33）。本文预算联动公式一律以此为准。
> **贯穿硬约束**：主图 7 节点骨架不变；判定逻辑归确定性代码；降级必须用户显式动作；最小单一抽象；R-PC4 prompt 主体冻结（改动走 HumanMessage 通道或 system prompt 尾部独立段落，涉及主体的一次性变更须重建 Prompt Cache 基线）。

---

## 1. Q-S5-5：真实性审计的落点与最小规则集边界（S5-03）

### 结论

**审计落在 reporting 侧入口**：新增独立纯函数模块 `core/honesty_audit.py`（确定性 AST/文本规则，零 LLM 调用），由 `core/nodes/reporting.py` 的 `reporting()` 在 `_determine_report_form()`（reporting.py:54）之前调用一次，对最终 `code_output_dir` 做静态审计；审计结果对象在 reporting 内直接喂给 S5-04 的结论分级判定，并作为新增单值 state 字段 `honesty_audit` 随 reporting 返回落 state（对 CP-C2-5"仅返回 report_path + current_step"契约做一次显式扩展——新增字段为单值 last-write-wins，不触碰任何 list 通道，契约红线的原意——不得覆盖 node_errors/degraded_nodes 等 list 字段——完整保留）。

### 备选方案对比

| 方案 | 说明 | 评估 |
|---|---|---|
| A：execution 收尾（步骤 5.5） | 在 `_build_execution_result`（execution.py:1349）后审计，结果嵌入 execution_result | 每个修复回合重复扫描（廉价但无谓）；execution.py 已 1800+ 行且 S5-06 也落在此处，职责继续膨胀；须兼容 interrupt#2 重跑幂等 guard 路径（execution.py:1702）的复用语义 |
| B：reporting 侧入口（**推荐**） | 修复循环收敛后、结论判定前，对**最终代码**恰好审计一次 | 与 S5-04 结论分级同处消费、零状态回传绕路；reporting 是纯函数节点（无 LLM、无 interrupt），确定性审计天然同质；唯一代价是扩展一次返回契约 |
| C：独立审计节点 | 图中插入 audit 节点 | 违背 7 节点骨架不变硬约束，直接排除 |

### 关键取舍

审计对象是"最终交付的代码"，而 execution 在修复循环中会多次进入、代码每回合都在变；只有 reporting 入口能保证"恰好一次、对最终产物、紧邻结论判定"的因果位置。审计命中不用于驱动修复（PRD 未要求），故不需要提前到 execution。

### 最小规则集边界（三类起步，误报防线同权重）

规则引擎总纪律：**只认字面量证据，不做跨文件数据流推断，不做 LLM 判断**；每条命中必须携带 `(file, line, snippet)` 证据，无证据不降档；命中只降档 + 标注，绝不阻断流程。扫描范围 = `code_output_dir` 下 `*.py`（排除 venv / outputs / tests）+ 数据清单类 JSON。

1. **R1 答案泄漏**：定义"答案字段名集合"（`expected_*` / `answer*` / `ground_truth*` / `*_keywords` / `label*` 等字面量键名）。命中条件 = **非评估角色文件**（文件/函数名不含 verify/eval/score/judge 语义）以 subscript/`.get()` 字面量键读取答案字段。误报防线：评估器角色文件读答案是合法行为，一律豁免（干净的 verifier 必然读答案，这是 AC-S5-06 的第一道防线）。
2. **R2 硬编码分数**：AST 检测 (a) 评分语义标识符（score/accuracy/pass_rate/f1/reward 等）在评分/执行函数中被**直接 return 数字字面量**；(b) baseline/实验名 → 数字字面量的 dict 字面量映射。误报防线：`score = 0.0` 类初始化赋值若同函数内存在后续更新（增量赋值/重绑定）则豁免——只有"绑定后不再变"的字面量才算剧本。
3. **R3 常量结局**：评估角色函数的**所有 return 路径均为常量表达式且函数入参未参与任何 return 值计算**（AST 内单函数可判定）→ 评估结果与输入无关。误报防线：任一 return 引用了入参或非常量中间值即不命中。

回归样本 `workspace/2604.01687/code`（skill_generator.py 抄 verifier 关键词 = R1、task_executor.py baseline 0.0/0.3/0.1 写死 = R2/R3）须命中 ≥2 类（AC-S5-05）；干净 fixture 零命中（AC-S5-06）。

### 落点文件

- 新增 `core/honesty_audit.py`（纯函数：`audit_code_dir(code_dir) -> HonestyAuditResult`）
- 改 `core/nodes/reporting.py`（入口调用 + 结论分级消费 + 返回契约扩展）
- 改 `core/state.py`（新增 `honesty_audit` 单值通道 + `create_initial_state` 默认 None，无 reducer）

---

## 2. Q-S5-6：计划步骤执行对账的数据来源（S5-06）

### 结论

**对账事实源 = 编排层确定性工具台账，agent 只提供可交叉核验的"步骤归属标签"，对账判定 100% 由确定性代码计算**。具体：`run_in_sandbox` 工具签名新增可选参数 `step_index: int = -1`（agent 执行计划第 i 步时声明）；编排层已有的真实执行台账（`_SandboxRunCollector` + messages 回读合并，execution.py:1294，R-S4-01"只认工具真实结果"既有机制）逐条记录 `(step_index, command, exit_code)`；execution 收尾新增确定性函数 `_reconcile_steps(plan.execution_steps, run_results)` 计算 `planned/executed/completed/未执行清单`，写入 `execution_result.step_reconciliation`。agent `<result>` 中的 `steps_attempted` 自报字段（execution.py:982）降级为仅供参考，**不再参与任何判定**。

归属规则（确定性，优先级从上到下）：① 台账条目带合法 `step_index`（越界丢弃 + WARNING）→ 归属该步；② 无 step_index 的条目，与计划步骤 command 做同一套规范化（复用 `_split_top_level`/`_rewrite_interpreter` 归一后精确匹配）→ 归属；③ 仍不匹配 → 计为"计划外命令"，不折算成步骤。"已完成" = 该步归属的 effective run（同命令最后一次，`_effective_runs` 既有口径）exit_code==0。

### 备选方案对比

| 方案 | 说明 | 评估 |
|---|---|---|
| A：agent 自报（现状 steps_attempted） | 信 `<result>` 数字 | 直接违反产品红线（被审计者自己打分），排除 |
| B：纯命令规范化匹配 | 计划 command vs 台账 command 归一比对 | 零工具改动，但 agent 合法适配命令（改路径/拆步/补参）会产生"假未执行"→ 错误降档，误报防线不达标 |
| C：声明归属 + 真实台账 + 匹配兜底（**推荐**） | step_index 只是标签；"是否真的执行、exit 多少"来自编排层台账，agent 无法凭空标记未运行的步骤 | 满足红线：造假需真实调用工具且 exit_code 真实；错标可在报告中暴露（步骤旁展示实际 command）；B 作为无标签时的兜底 |

### 关键取舍

红线的本质是"执行事实不得来自 agent 单方声明"。方案 C 中事实（工具调用发生过、exit_code 真实）全部来自编排层台账；agent 声明的只是归属映射，且每条归属都附带真实 command 可人工核验——即使 agent 错标/滥标，也无法把"没跑"伪装成"跑成"。工具签名/prompt 增量属于一次性 Prompt Cache 基线重建（sp5 集中一次，见第 3 段）。

### 落点文件

- 改 `core/nodes/execution.py`：`make_run_in_sandbox_tool`（:858，签名 + 台账记 step_index）、新增 `_reconcile_steps`、`_build_execution_result`（:1349，写入 step_reconciliation）、`_EXECUTION_SYSTEM_PROMPT_BODY`（:961，工具用法一行说明，与 Q-S5-7 的 prompt 改动合并为同一次基线重建）
- 改 `core/state.py`：`ExecutionResult` 增 `step_reconciliation` 字段
- 报告渲染消费在 S5-04/S5-06（第 2 段详述）

---

## 3. Q-S5-7：execution 轮数预算与计划步数联动公式（S5-06）

### 结论

**`effective_max_rounds = clamp(len(execution_steps) + K, FLOOR, CAP)`，K=5、FLOOR=10（沿用现常量）、CAP=30**。落点：`_run_execution_agent`（execution.py:1214）不再直接用 `REACT_MAX_ROUNDS_EXECUTION` 常量（:1261/:1267），改调确定性 helper `_effective_max_rounds(plan)`；config 新增 `REACT_EXECUTION_ROUNDS_MARGIN: int = 5` 与 `REACT_MAX_ROUNDS_EXECUTION_CAP: int = 30`，`REACT_MAX_ROUNDS_EXECUTION=10` 语义收窄为 FLOOR。

**K=5 的构成**（对照子图轮次语义：react_base.py 中 1 轮 = 1 次 reasoning LLM 调用，工具执行不耗轮）：prepare_environment 1 轮 + 收尾 `<result>` 1 轮 + 凭证/重试/pip 兜底裕量 3 轮。回归样本 13 步计划 → 18 轮（现状 10 轮只够 8 步，正是 #11 事故结构根因）。

**与全局账本对账**：
- `CAP=30 = MAX_DEV_LOOP_LLM_CALLS(60) / 2`：保证最坏情况下初跑耗尽 CAP 后，修复循环子预算仍容得下至少一个完整修复回合（coding 12 + execution ≤30 超出时由既有子预算门自然拦截，`DEV_LOOP_MIN_CALLS_PER_ROUND=2` 入口门不变）；
- 全局 120：上游四 ReAct 节点最坏 5+12+10+8 = 35，coding 12，execution ≤30+metrics LLM 抽取 ≤3 → 首轮全链 ≤80 < 120，余量给修复循环，由 60 子预算天花板约束，不需要新增任何全局账本机制。

**截断显式化（AC-S5-12，零 react_base 改动）**：利用既有轮次语义的确定性代理判据——`budget_check` 在 `round >= max_rounds-1` 触发（react_base.py:621-629），`force_finish` 再 +1 轮，故 **`rounds_used >= effective_max_rounds` ⇔ 走了 force_finish 截断路径**（正常收尾 round 恒 ≤ max_rounds-1）。`_run_execution_agent` 据此产出 `budget_truncated: bool`，写入 `execution_result`（state 记录）+ INFO 日志（"任何预算截断必须显式 log + state 记录"通则的 sp5 首个落点），并与 Q-S5-6 对账联动强制降档。

连带修正：`_EXECUTION_SYSTEM_PROMPT_BODY` 内写死的"max_rounds=10"（execution.py:973）改为非数字表述，实际预算数字随 HumanMessage 动态上下文注入（动态值进冻结主体违反 R-PC4，必须走动态通道）。

### 备选方案对比

| 方案 | 评估 |
|---|---|
| A：静态调大到 25/30 | 不联动步数：短计划浪费预算空转，超长计划仍结构性跑不完，未治根因，排除 |
| B：每步独立轮次配额（per-step 记账+强制推进） | 需侵入子图循环控制，改 react_base 或重写编排，过度工程，排除 |
| C：线性联动 + 双端 clamp（**推荐**） | 一行公式 + 两个常量，确定性、可单测（AC-S5-12 构造长计划断言联动），与账本可验证对账 |

### 落点文件

`config.py`（两常量 + 一处语义注释）、`core/nodes/execution.py`（`_effective_max_rounds` + `budget_truncated` + prompt 修正）、`core/state.py`（`ExecutionResult` 增 `budget_truncated`）。

---

## 4. Q-S5-8：agent 活动流事件 schema、缓存上限、渲染行数（S5-07）

### 结论

**采纳反馈 #5 方向，经代码实况核验可行**：per-thread `BaseCallbackHandler` 采集压缩事件 → `GraphController` 上 per-thread 内存 deque → 执行监控页尾部轮询渲染。关键可行性论证（对照 app.py 现状）：worker 线程 `graph.invoke(initial_state, config)`（app.py:169）与 resume 线程（app.py:197）都有 config 注入点，改为 `{**config, "callbacks": [handler]}` 即可；节点内嵌套的 `subgraph.invoke(initial)`（react_base.py:873 / execution.py:1272）与 `llm.invoke` 虽未显式传 config，但 langchain-core 通过 `var_child_runnable_config` contextvar 自动向嵌套 Runnable 传播父级 callbacks，LangGraph 节点执行时复制 contextvars——**这正是唯一能穿透"节点内手动 invoke 子图"边界且零 react_base 改动的通道**（须以 spike 检查点先行实证，风险与回退见第 3 段）。GraphController 是 `st.session_state` 单例、与 worker 同进程，UI 直接读内存，天然不进 checkpoint。

**事件 schema（单一 TypedDict，5 字段，不做事件类型枚举体系）**：

```python
class ActivityEvent(TypedDict):
    seq: int          # 线程内单调递增序号（UI 增量渲染用）
    ts: float         # time.time()
    node: str         # metadata["langgraph_node"]，取不到时 ""
    kind: str         # "tool" | "llm" 两值，字符串字面量，不建 Enum
    text: str         # 单行压缩摘要，已过 mask_value 脱敏
```

- **采集点两个**：`on_tool_start`（"⏺ 工具名(参数摘要≤120 字符)"）+ `on_llm_end`（模型输出文本截断预览 ≤160 字符）；`text` 生成时一律过 `secrets_store.mask_value`（run_in_sandbox 命令行可能内嵌凭证，与 sp4 §9.4 脱敏口径对齐）。
- **缓存上限**：`ACTIVITY_STREAM_MAX_EVENTS = 500`（per-thread `deque(maxlen=500)`，单事件 ≤~300B，内存上界 ~150KB/任务，进程级封顶由单任务运行模式保证）。
- **渲染行数**：`ACTIVITY_STREAM_RENDER_TAIL = 30`（执行监控页复用既有 `st_autorefresh` 1500ms 节奏，`st.code`/等宽块渲染最近 30 行）。
- **生命周期**：纯内存、进程重启即失（可观测性尽力而为语义）；不持久化、不进 checkpoint、不进 state——三个"不"即 AC-S5-14 验收面。

### 备选方案对比

| 方案 | 评估 |
|---|---|
| A：react_base 内插桩发事件 | 直接违反"不改 react_base 内部"产品约束，排除 |
| B：LangGraph `stream(subgraphs=True)` / stream_mode=custom | 须把 worker 的 invoke 全改为 stream 消费循环，且嵌套手动 `subgraph.invoke` 的内层事件不经父图 stream 通道暴露，覆盖不了 coding/execution 节点内活动，排除 |
| C：config callbacks + contextvar 传播（**推荐**） | 落点全在 app.py/新模块/UI 层，react_base 零字节改动；LangChain 原生机制，事件天然带 run 元数据 |

### 落点文件

- 新增 `core/activity_stream.py`（`ActivityEvent` + `ActivityStreamHandler(BaseCallbackHandler)` + per-thread deque 容器；线程安全靠 deque 原子 append + 快照读）
- 改 `app.py`：GraphController 持有 `{thread_id: handler}`（get-or-create），`_worker_run`/`_resume_run` 注入 callbacks，新增只读方法 `get_activity_tail(thread_id, n)`
- 改 `ui/pages/execution_monitor.py`：活动流尾部渲染区
- 改 `config.py`：两常量

---

## 5. Q-S5-9：required_credentials 最小 schema 与 coding 开工前比对落点（S5-01）

### 结论

**schema = 计划内一个可选列表字段，每项两键，复用 sp4 purpose_key 体系**：

```python
# ReproductionPlan 增量（state.py:115；additive，默认 []）
required_credentials: List[Dict[str, str]]
# 每项：{"purpose_key": str, "purpose": str}
# purpose_key 沿用 sp4 既有约定："git_credential:<host>" / "hf_token"，
# 新增一条通用约定 "env:<ENV_VAR>"（如 "env:OPENAI_API_KEY"）覆盖 API key 类；
# purpose 为给用户看的中文用途说明（"论文方法依赖真实 LLM 调用"）。
```

`env:<VAR>` 约定同时解决注入问题：`secrets_store.build_credential_env`（secrets_store.py:297，现状未知 purpose_key 被忽略）增加一条通用规则——`env:X` → `env["X"]=value`——凭证经 sp4 既有 extra_env 通道进入 coding `run_command` 与 execution sandbox，不为任何 provider 做枚举映射（PRD 非目标：不建 API 代理层）。

**比对落点 = coding 节点入口的确定性前置门（gate）**：`core/nodes/coding.py` 将节点从裸 wrapper（coding.py:467）改为"手写前置门 + 既有 ReAct wrapper"的复合函数（与 planning"手写复合"同范式，节点名/节点数/graph.py 边结构零改动）。gate 逐项 `lookup_secret(purpose_key)`（`.secrets` 命中即静默通过，跨任务复用），缺失 → 直接走 Q-S5-10 的显式交互；**比对与放行判定全在确定性代码，agent 的 prompt 纪律只是第二道软防线**（S5-02），"agent 不问就绕过"的路径（feedback #10：coding.py:101/109-113 只靠 prompt）被机制性堵死。

### 备选方案对比

| 方案 | 评估 |
|---|---|
| A：三键+类型分级枚举（name/type/level…） | PM 已倾向两键；类型枚举是 sp4 被 Maria 否掉的 input_type 同款过度设计，排除 |
| B：比对放 planning 收尾（interrupt#1 附带） | 计划审核页确实是天然交互点，但凭证需求在 revise/switch_repo 循环中反复变动、且 plan_review 页与 interrupt#1 payload 契约改动面大；更重要的是它守不住"coding 开工前"这道最后闸门（用户批准计划 ≠ 凭证已到位），排除为主落点（计划审核页只做只读展示，见第 2 段） |
| C：coding 入口确定性 gate（**推荐**） | 最后闸门语义正确；复合节点范式 sp2 planning 已踩通；`.secrets` 命中零打扰 |

### 关键取舍

purpose_key 是 sp4 已建成的"稳定标识 + 去重 + 落盘 + 脱敏"全套机制的锚点，声明字段直接以它为主键即让 S5-01 的比对、interrupt 索要、「记住」复用、mask 全部免费获得；`env:<VAR>` 一条约定同时闭合了"收集后如何进沙箱"的注入链，避免另起任何新机制。

### 落点文件

`core/state.py`（ReproductionPlan 增字段）、`core/nodes/planning.py`（REPRODUCTION_PLAN_SCHEMA:67 增属性 + prompt 增量，R-PC4 合规落点见第 2 段）、`core/nodes/coding.py`（前置门复合）、`core/secrets_store.py`（`build_credential_env` 增 `env:` 规则，纯追加）。

---

## 6. Q-S5-10："用户显式降级为模拟"的交互形态（S5-01）

### 结论

**复用 interrupt#3 `user_input_request` 通道原样承载，做两处纯增量扩展**，降级动作只存在于"确定性 gate 发起的 interrupt"路径上：

1. **payload 增量第 5 键**：coding 前置门缺凭证时，由 gate 代码（非 agent 工具）直接 `interrupt()`，payload 复用 interaction_tools.py:102 的四键契约（`interrupt_kind="user_input_request"` / question / is_sensitive=True / purpose_key），**追加 `"allow_degrade": True`**。既有 app.py:393-398 全局路由与执行监控页输入面板按 kind 分发的链路零改动即可达。
2. **resume 契约增量第 3 键**：UI 面板检测到 `allow_degrade=True` 时，在既有"提交 + 记住"之外渲染一个显式按钮「无此凭证，降级为模拟实验」，点击后 `Command(resume={"value": "", "remember": False, "degrade": True})`。gate（确定性代码）解读 `degrade=True` → 将该 purpose_key 写入降级标记 state 字段（形态第 2 段定），并继续放行 coding（降级事实经 HumanMessage 上下文告知 agent，触发 S5-02 simulation 声明义务）；`degrade` 缺省 False，普通提交语义与 sp4 完全一致。

**红线保障（agent 无降级入口）**：`allow_degrade` 只由 gate 设置；agent 经工具调用 `request_user_input` 产生的 payload 永远不含该键 → UI 不渲染降级按钮 → 用户无法在 agent 路径上"降级"，agent 更无法自行决定——降级判定与标记写入全部发生在确定性编排代码中。`request_user_input` 工具的 docstring/schema 零字节改动（Prompt Cache 纪律 CP-B1-5 不受扰动）。

### 备选方案对比

| 方案 | 评估 |
|---|---|
| A：新增 interrupt kind（如 "credential_gate"）+ 专属面板 | 新 kind = app.py 路由、UI 面板、GraphController 判定三处新表面 + 新测试矩阵；语义上它仍是"向用户要一条输入"，不值得第四类 interrupt，排除 |
| B：复用 kind + payload/resume 纯增量（**推荐**） | 既有路由/面板/幂等机制全部免费复用；增量键向后兼容（老 payload 无键 = 无按钮；老 resume 无键 = 不降级） |
| C：降级选择前移到计划审核页（interrupt#1 附带） | 与 Q-S5-9 方案 B 同病：守不住最后闸门、interrupt#1 契约改动面大；且用户在批准计划时未必能预判"到底缺不缺"，排除 |

### 关键取舍

sp4 花整个 Sprint 踩通的 interrupt#3 全链（工具内 interrupt、GraphBubbleUp 直通、resume 幂等、敏感值脱敏、UI 面板）是本项最大资产；两个可选键的纯增量扩展让 S5-01 的"索要"与"显式降级"共用同一条已验证通道，新增代码集中在 gate 判定与一个按钮上。gate 在 resume 后节点函数重跑时，`.secrets` 命中（用户已提交并记住）与 LangGraph interrupt 按序重放机制共同保证幂等，多缺失项逐个串行 interrupt（LangGraph 单节点多 interrupt 既有语义，sp4 已实证同款）。

### 落点文件

`core/nodes/coding.py`（gate 内 `interrupt()` 发起 + degrade 解读 + 标记写入）、`ui/pages/execution_monitor.py`（输入面板增降级按钮，仅 `allow_degrade=True` 时渲染）、`core/state.py`（降级标记字段，第 2 段定形态）；`core/tools/interaction_tools.py` **零改动**。

---

> 以下为第 2 段：S5-01~11 技术方案概要与 state/schema 变更总表。承接第 1 段（Q-S5-5~10 六项裁决，下文以 §1~§6 引用），本段逐项给出 S5-01~11 的文件级方案与数据契约变更；第 1 段已裁决项只做衔接引用不重复展开。编号续接第 1 段。

---

## 7. S5-01~11 逐项技术方案

### 7.1 S5-01 凭证前置识别 + 用户显式降级（P0）

方案 = §5（schema/gate）+ §6（交互形态）的组合落地，本段定案**降级标记 state 字段形态**：

```python
# GlobalState 新增（单值 Dict、gate 单点整 dict 回写，无 reducer）
credential_degradations: Dict[str, str]   # purpose_key → purpose 中文用途说明；非空即"用户已显式降级"
```

- **写入方唯一**：coding 前置门（确定性代码）在收到 `resume={"degrade": True}` 时写入；agent 无任何写入路径。
- **全链路传导（AC-S5-03 三落点）**：① state 字段本体；② execution 收尾 `_build_execution_result`（execution.py:1349）把 `sorted(credential_degradations.keys())` 快照进 `ExecutionResult.degraded_credentials: List[str]`；③ reporting 结论判定将其映射为正交标注 `credential_degraded` + 报告强制声明节（见 7.4）。
- gate 放行后把降级事实注入 coding 的 HumanMessage 动态上下文（`_build_coding_context`），触发 agent 的 simulation 声明义务（7.2）——上下文走动态通道，R-PC4 无扰。
- 落点：`core/nodes/coding.py`（gate）、`core/nodes/planning.py`（REPRODUCTION_PLAN_SCHEMA:67 增 `required_credentials` 可选属性 + `_map_planning_result` 缺失回填 `[]`）、`core/secrets_store.py`（`build_credential_env` 增 `env:<VAR>` 通用规则）、`ui/pages/execution_monitor.py`（降级按钮）。

### 7.2 S5-02 coding 诚实红线 + simulation 声明（P0）

- **红线落点（R-PC4 合规形态）**：在 `_CODING_SYSTEM_PROMPT_BODY`（coding.py:92）与尾部"--- 当前任务上下文 ---"之间插入**独立静态段落常量** `_CODING_HONESTY_SECTION`（三条红线：禁 verifier 答案泄漏 / 禁硬编码分数与常量结局 / 不得以改变实验本质规避资源缺失 + "无法真实验时必须在 result 中给出 simulation_notice"）。段落跨任务字节级恒定；属 sp5 一次性 prompt 静态变更批次（与 §2/§3 的 execution prompt 修正合并，只重建一次 Prompt Cache 基线，详见第 3 段）。
- **simulation 声明形态（最小单字段）**：`CODING_OUTPUT_SCHEMA`（coding.py:54）增可选属性 `simulation_notice: string|null`（中文说明"哪部分是模拟、为什么"）；`_map_coding_result` 透传为 GlobalState 新字段 `simulation_notice: Optional[str]`（默认 None，coding 单点写）。消费方：7.4 正交标注 + 报告强制声明。
- 边界：prompt 红线是软防线（降发生率），硬防线在 §1 审计与 §5 gate；AC-S5-04 以 prompt 断言 + mock state 断言验收。

### 7.3 S5-03 真实性审计 + 降档（P0）

方案已由 §1 全量裁决（`core/honesty_audit.py`，reporting 入口调用，三规则 + 三重误报防线）。本段补数据契约：

```python
# honesty_audit.py 返回形态（同时作为 state 字段 honesty_audit 的值）
{"clean": bool, "hits": [{"rule": "answer_leakage"|"hardcoded_score"|"constant_outcome",
                          "file": str, "line": int, "snippet": str}]}
```

任一 hit → 7.4 结论判定强制加 `simulation` 标注（报告显著标注"模拟/未验证"）+ 禁科学复现档。`rule` 三值为字符串字面量，不建 Enum 类。

### 7.4 S5-04 两级结论 + 正交标注 + 定性目标回验（P0）

**判定函数**（reporting.py 新增，纯确定性，紧随 §1 审计调用之后、`_determine_report_form`（reporting.py:54）之前）：

```python
_determine_conclusion(state, exec_result, audit) -> {
    "level": "science" | "engineering" | "none",   # 两级 + 未达成
    "annotations": List[str],                       # ⊆ {"simulation", "credential_degraded", "incomplete_execution"}
    "goal_checks": [{"description": str, "verdict": "符合"|"不符"|"未验证"}],
}
```

- **判定规则（确定性）**：`engineering` ⇔ `exec_result.success == True`（B 档语义原封不动，execution.py:1367 不改）；`science` ⇔ engineering ∧ `goal_checks` 全部"符合"且非空 ∧ `annotations` 为空；其余 `none`。
- **正交标注来源映射**：`simulation` ← `simulation_notice` 非空 ∨ 审计 hits 非空；`credential_degraded` ← `credential_degradations` 非空；`incomplete_execution` ← `step_reconciliation` 存在未执行步骤 ∨ `budget_truncated`。任一标注 → 禁 science + 报告顶部显著声明块（AC-S5-11 的"强制降档"即此通道）。
- **goal_checks 回验**：新增纯函数 `_verify_expected_results(expected_results, exec_result)` —— 带 `trend` 结构的条目用 `metrics_groups`（7.10）做确定性比较（组名归一化子串匹配，失配保守判"未验证"）；纯文本条目一律"未验证"（诚实保守，绝不让 LLM 或猜测参与判定）。
- **现状档位映射**：三形态报告骨架（full_success/code_only/degraded 内部值）保留不改（不动 sp3 测试面）；`full_success` 渲染器改为按 conclusion 输出——science → "复现成功（科学复现）"；engineering → **"代码跑通（工程复现），论文实验结论未验证"**（AC-S5-07 措辞红线，全文禁"复现成功"字样）；code_only/degraded 文案经 7.9 映射表。新增"计划目标回验"渲染节（三态表）。
- 落点：`core/nodes/reporting.py`（判定 + 回验 + 渲染节 + 声明块）。

### 7.5 S5-05 expected_results 定性化（P1）

**schema 定案（最小两键，一键可选）**：

```python
# ReproductionPlan.expected_results：Dict[str, Any] → List[Dict]（breaking，见 §8 兼容注记）
[{"description": str,                                  # 定性中文描述（"loss 应收敛"）
  "trend": {"metric": str, "greater": str, "lesser": str} | None}]  # 可机验的相对趋势（组名对齐 metrics_groups）
```

- planning prompt：主体【6 章节】第 6 节改写（禁编造数值、只给定性描述与趋势结构、论文数字不复述）——属 sp5 一次性静态 prompt 批次；`REPRODUCTION_PLAN_SCHEMA`（planning.py:67）`expected_results` 改 array 形态。
- reporting 对比表（reporting.py:270-313）**删"计划 expected"列**，只留论文 baseline vs 本次复现值（多组时按组展开，7.10）；渲染器对旧 dict 形态防御性容忍（回归样本 thread 是旧形态：不渲染 expected 列、回验全"未验证"，不崩）。
- 落点：`core/nodes/planning.py`、`core/state.py`、`core/nodes/reporting.py`。

### 7.6 S5-06 步骤对账 + 预算联动 + 截断显式（P0）

方案已由 §2（对账数据源）+ §3（联动公式与截断判据）全量裁决。数据契约：

```python
# ExecutionResult 新增
step_reconciliation: Dict   # {"planned": int, "executed": int, "completed": int,
                            #  "unexecuted_steps": [{"index": int, "step_name": str}],
                            #  "extra_commands": [str]}   # 计划外命令（归属失败的真实执行）
budget_truncated: bool
```

报告新增"步骤对账"渲染节（"已完成 N/M 步"+ 未执行清单 + 截断声明）；`incomplete_execution` 标注经 7.4 消费。落点见 §2/§3 落点表。

### 7.7 S5-07 agent 活动流（P1）

方案已由 §4 全量裁决（`core/activity_stream.py` + GraphController 注入 + 监控页尾部渲染，schema/上限/行数均已定）。**零 state/schema/checkpoint 变更**（AC-S5-14 验收面即此）；仅 config 增两常量（§8）。

### 7.8 S5-08 UI 主路由修复 + 完成判定兜底（P0）

**取舍**：不建统一路由层（改动面大、须重构三页轮询骨架），在两页既有 case 分支结构内做**局部规则修复**（最小方案）；app.py:393 全局路由保持只管 `user_input_request`。

- `ui/pages/analysis_progress.py`：
  1. case④（:540-542）由"任何 interrupt → review"改为按 `controller.get_interrupt_payload(thread_id)` 的 kind 分发：planning（payload 无 kind 键的既有形态）→ review；`dev_loop_failure` / `user_input_request` → 执行监控页（AC-S5-16 修复点）。
  2. 新增 case④bis：`current_step ∈ {coding, execution}` → 切执行监控页（#4 主修复）。
  3. 新增 case④ter：`current_step == "reporting"` ∧ `report_path` 非空 → 直跳报告页（兜底，与监控页 case⑥ 双通道可达，AC-S5-15）。
- `ui/pages/execution_monitor.py`：case⑥（:671-675，`_should_jump_to_report`）保留；新增 case⑥bis（#6 兜底，AC-S5-17）：`current_step == "reporting"` ∧ `report_path` 为空 ∧ `controller.is_finished(thread_id)` → 渲染明确失败/降级提示卡片并**不注册 autorefresh**（停假轮询）。
- `app.py` GraphController：新增只读方法 `is_finished(thread_id)`（`snapshot.next` 为空元组 ∧ snapshot 存在——与 is_interrupted（app.py:213）同一读路径范式）。
- 零 state 变更。

### 7.9 S5-09 术语治理（P2）

- **展示层**：新增 `ui/term_map.py` —— 单一扁平表 `TERM_LABELS: Dict[str, str]`（key = `"{domain}:{value}"`，如 `"code_strategy:from_scratch"`）+ 单函数 `humanize(domain, value) -> str`，未知值兜底返回 `f"{value}（内部标识）"`（不崩、不静默丢信息，AC-S5-18）。覆盖 domain：code_strategy / resource_strategy / error_category / fix_strategy / 节点名 / 报告三形态 / 7.4 新结论档位与标注。plan_review.py:279/:329 等裸渲染点全部改经 `humanize`（全 UI 扫描清单归开发任务，第 3 段）。
- **生成源头**：planning prompt 尾部**独立静态段落**加"机器可读字段保留枚举原值、用户可读文本用通俗中文、禁内部枚举/自创缩写"约束（AC-S5-19；reporting 无 LLM，其正文术语由模板 + 映射表治理，不涉 prompt）。并入 sp5 一次性 prompt 静态批次。
- 零 state 变更。

### 7.10 S5-10 指标多组解析 + 渲染修复（P1）

- **多组解析（二选一裁决：选文件扫描约定）**：execution 收尾新增确定性步骤 4.5 `_collect_grouped_metrics(work_dir)` —— 扫描 `<work_dir>/outputs/**/summary.json`，每文件收编顶层数值/布尔/短字符串字段，组名 = 相对 `outputs/` 的父目录路径（回归样本即 `evoskills_smoke` / `baselines/no_skill` / `baselines/self_generated`，AC-S5-20 直接可测）。写入 `ExecutionResult.metrics_groups: Dict[str, Dict[str, Any]]`。既有 `<METRICS>` 三档主通道（execution.py:358/:382/:408）语义不动（仍是主实验 `metrics`）。**弃选**"扩展 <METRICS> 多块约定"方案：需改 coding 产出约定、对已有回归样本不可用、且解析仍依赖 agent 服从度。
- **嵌套降维渲染**：reporting `_fmt_metric_value`（reporting.py:169）对 dict/list 值改为"子表/逐键行"降维渲染，禁止 `str()` 整塞单元格；对比表按组展开"本次复现值"列。
- **key_packages 修复（根因已定位）**：`sandbox/local_venv.py:716` 本身正确产出 `key_packages`，但 execution 的 messages 回读重建把 prep 的 `env_info` 置空占位（execution.py:1148/:1165），R-S4-10"回读为权威"合并后覆盖了收集器真值 → 恒空。**修复**：`prepare_environment` 工具返回 payload 增带 `env_info`（回读即可重建）；`_rebuild_prep_results_from_messages` 对应解析。
- 落点：`core/nodes/execution.py`、`core/nodes/reporting.py`、`sandbox/local_venv.py` 零改动。

### 7.11 S5-11 产物路径明示（P2，Q-S5-2 已确认）

`ui/pages/result_report.py` 与 `ui/pages/execution_monitor.py` 各加一处只读展示区：`st.code(state["code_output_dir"])` + `st.code(state["report_path"])`（`st.code` 自带一键复制按钮，零新组件、零新依赖）。数据源均为既有 state 字段，**零 state 变更**；不做打开目录/导出（PRD 非目标）。

---

## 8. state / schema 变更总表

| 字段 | 所在结构 | 类型 | 默认值 | 写入方（单点） | 消费方 |
|---|---|---|---|---|---|
| `credential_degradations` | GlobalState | `Dict[str, str]` | `{}` | coding 前置门（gate） | coding 上下文 / execution 收尾 / reporting 标注 |
| `simulation_notice` | GlobalState | `Optional[str]` | `None` | coding `_map_coding_result` | reporting 标注 + 强制声明节 |
| `honesty_audit` | GlobalState | `Optional[Dict]` | `None` | reporting（返回契约扩展，§1） | UI 报告页 / 测试断言 |
| `required_credentials` | ReproductionPlan | `List[Dict[str,str]]`（purpose_key/purpose 两键） | `[]`（map 回填） | planning ReAct + 回填 | coding gate / 计划审核页只读展示 |
| `expected_results` | ReproductionPlan | `Dict → List[Dict]`（description + 可选 trend，**breaking**） | `[]` | planning ReAct | reporting 回验 `_verify_expected_results` |
| `step_reconciliation` | ExecutionResult | `Dict`（planned/executed/completed/unexecuted_steps/extra_commands） | `{}` | execution `_reconcile_steps` | reporting 对账节 + `incomplete_execution` 标注 |
| `budget_truncated` | ExecutionResult | `bool` | `False` | execution `_run_execution_agent` | reporting 标注 + 截断声明（AC-S5-12） |
| `metrics_groups` | ExecutionResult | `Dict[str, Dict[str, Any]]` | `{}` | execution `_collect_grouped_metrics` | reporting 对比表 + 趋势回验 |
| `degraded_credentials` | ExecutionResult | `List[str]` | `[]` | execution `_build_execution_result`（自 state 快照） | reporting 强制声明（AC-S5-03 第②落点） |
| `REACT_EXECUTION_ROUNDS_MARGIN` | config.py | `int` | `5` | — | `_effective_max_rounds`（§3） |
| `REACT_MAX_ROUNDS_EXECUTION_CAP` | config.py | `int` | `30` | — | 同上 |
| `ACTIVITY_STREAM_MAX_EVENTS` | config.py | `int` | `500` | — | `core/activity_stream.py`（§4） |
| `ACTIVITY_STREAM_RENDER_TAIL` | config.py | `int` | `30` | — | 执行监控页活动流区 |

**通用纪律**：所有新 GlobalState 字段均为单值通道、显式声明 + `create_initial_state` 给默认值（B2/B3 静默丢弃实证教训）、**绝不加 reducer**（must-fix-1）；ExecutionResult 为 TypedDict 必填键，两处构造点（execution.py:1386 与 :1750 降级路径）同步补齐，下游一律 `.get()` 防御读（兼容 `task-9208a1a4b4f5` 等旧 checkpoint 无新键）。`expected_results` 形态变更为唯一 breaking 项：reporting/回验对旧 dict 形态防御性容忍（7.5）。

**新增模块清单**：`core/honesty_audit.py`（§1）、`core/activity_stream.py`（§4）、`ui/term_map.py`（7.9）——共 3 个，无其他新目录/新抽象层。

> 以下为第 3 段（终段）：既有机制集成 + 风险与回归防线 + 任务拆分。承接第 1 段（§1~§6 六项裁决）与第 2 段（§7~§8 方案与数据契约），编号续接。

---

## 9. 既有机制集成方式

### 9.1 sp5 一次性 Prompt Cache 基线重建批次（R-PC4 集成）

sp5 全部**静态** prompt / 工具 schema 变更**合并为一个批次**（批次 1，见 §11）一次合入，只触发一次基线重建，此后 sp5 内不再动任何稳定前缀。批次清单：

| # | 变更 | 文件 | 性质 |
|---|---|---|---|
| P1 | coding 增 `_CODING_HONESTY_SECTION` 静态段落（7.2 三红线 + simulation 义务） | coding.py（:92 主体与尾部段落之间） | 主体追加，跨任务字节恒定 |
| P2 | coding `CODING_OUTPUT_SCHEMA` 增 `simulation_notice` | coding.py:54 | result_schema（仅 finalize 消费，不进前缀，随批合入） |
| P3 | execution 主体"max_rounds=10"字面量改非数字表述；实际预算随 HumanMessage 注入 | execution.py:973 | 主体修正（§3） |
| P4 | execution 主体增 `step_index` 用法一行说明 | execution.py:961 段内 | 主体追加（§2） |
| P5 | `run_in_sandbox` 签名增 `step_index: int = -1`（docstring 即工具 schema，进前缀） | execution.py:858 | 工具 schema 变更（§2） |
| P6 | `prepare_environment` 返回 payload 增 `env_info` | execution.py:805 | 仅 ToolMessage 内容，不进前缀（7.10，随批合入） |
| P7 | planning 主体第 6 节改写（expected_results 定性化、禁编造数值）+ required_credentials 声明指令 | planning.py:94 主体 | 主体修正（7.5 / 7.1） |
| P8 | planning 尾部增术语约束静态段落（7.9） | planning.py | 尾部独立段落 |
| P9 | `REPRODUCTION_PLAN_SCHEMA` expected_results 改 array + 增 required_credentials | planning.py:67 | result_schema |

`interaction_tools.request_user_input` docstring **零字节改动**（§6，CP-B1-5 不受扰）。

**基线操作顺序（双维守门，沿用 sp4 范式）**：
1. **离线维（mock，随批次 1 落地）**：更新/复跑既有字节稳定断言（CP-B3-10 planning / CP-C1-6 coding / CP-E2-1 execution 同款：不同论文输入下 SystemMessage 字节级一致、尾部段落常量、主体无动态变量），并对三份新版主体做一次"禁动态变量"审查断言。
2. **在线维（真跑复采，授权点）**：批次收尾用 `scripts/spike_coding_prompt_cache.py` / `scripts/spike_execution_prompt_cache.py` / `scripts/spike_prompt_cache_baseline.py` 复采命中率，记录新 R_baseline（旧基线因前缀变更作废属预期）；后续以新基线 × 0.95 守门。**真跑耗配额，须 Maria 明确授权具体动作**，可与批次 5 的回归真跑合并为一次授权（省配额范式）。

### 9.2 interrupt 幂等注意点

- **coding gate（§5/§6）单项串行纪律**：每次节点执行只对 missing 列表**第一个**缺失项 `interrupt()`；resume 后节点重跑，gate 重算 missing（「记住」项经 `.secrets` 命中消失；未记住项由 LangGraph 按 interrupt 调用序重放已录 resume 值）。"一次一项 + 重跑再查"避免多 interrupt 并发时缺失集合漂移导致的 resume 值错位（LangGraph 按调用序匹配 resume，集合变化会串位——这是本机制最大的幂等陷阱）。
- **GraphBubbleUp 红线延伸**：gate 的 `interrupt()` 周围严禁 try/except 兜底捕获（BUG-S4-B1-01 同款红线，从工具层扩展到 gate 层）。
- **未记住的敏感值进沙箱**：`build_credential_env` 只读 `.secrets`，"不记住"的凭证将无法注入 env。为不强迫用户记住，`secrets_store` 增一个进程内会话覆盖层：`stash_session_secret(purpose_key, value)` + `load_all_secrets` 合并覆盖（不落盘、进程重启即失、值同步 `register_sensitive_value`）。这是对"极简六接口"的一次显式扩接（第 7 接口），替代方案是"强制记住"（损害用户选择权）——两害相权取其轻，须在 dev-plan 记录。**[2026-07-09 T-S5-2-1 实现期架构师裁决补记]**：`lookup_secret` 同步感知会话层（会话层优先，与 `load_all_secrets` 合并语义严格一致）——否则"不记住"的凭证在 gate 重跑重算 missing 时永不命中，形成无限 interrupt 死循环；安全关键模块内不允许读取面语义分叉。
- **execution interrupt#2 guard 零扰动**：S5-06 对账/截断/多组指标全部在 `_build_execution_result` 之前完成、随 exec_result 一次 commit；guard 命中路径（execution.py:1702）复用已落盘结果即含新字段，不需要任何重算。

### 9.3 脱敏纪律衔接（sp4 §9.4 落点延伸）

新增的四个"文本出口"全部过 `mask_value` 后再落地：① 活动流事件 `text`（§4，生成即脱敏）；② `step_reconciliation.extra_commands` 与未执行清单中的命令串（命令可能内嵌 token）；③ `honesty_audit.hits[].snippet`（生成代码里可能硬编码 key）；④ gate 日志只打 purpose_key（interaction_tools 同款纪律）。降级标记 `credential_degradations` 只存 purpose_key/purpose 说明，天然无敏感值。

### 9.4 回归样本 fixture 化建议（PRD §8）

由测试工程师在开发批次 1 前执行一次性固化（**复制，不移动**，原样本保持只读）：
- `tests/fixtures/regression_2604_01687/`：`code/skill_generator.py`、`code/task_executor.py`、`code/data/skillsbench_manifest.json`（AC-S5-05 审计命中靶）+ `outputs/` 三组 `summary.json`（AC-S5-20）+ `report.md`（措辞对照）；
- `tests/fixtures/clean_code_sample/`：新造 3~4 个文件的干净复现代码（真读输入、真算分）作 AC-S5-06 误报防线靶；
- `checkpoints.db` thread `task-9208a1a4b4f5` 只读引用，用于 AC-S5-15 路由断言与 AC-S5-07 报告重生成断言（tests 内以 state 快照 mock 为主，真库只做手动验证）。

---

## 10. 风险清单与回归防线

### 10.1 风险与缓解/回退

| 编号 | 风险 | 缓解 | 回退 |
|---|---|---|---|
| R-1 | **Q-S5-8 callbacks contextvar 不传播**到节点内手动 `subgraph.invoke`（react_base.py:873 / execution.py:1272） | **批次 0 spike 检查点 CP-SPK-1**：mock LLM 驱动 `build_graph().invoke(state, {"callbacks": [handler]})`，断言收到 coding/execution 子图内 on_llm_end/on_tool_start 事件 | 回退 R-B1：execution 侧在编排层 `subgraph.invoke(initial, {"callbacks": ...})` 一行透传；coding 侧若仍不达，援引 BUG-S4-B1-01 勘误先例对 react_base 做**一行纯追加** config 透传并记 dev-plan 勘误注记（"不改 react_base"约束的受控豁免，预算/循环逻辑零触碰） |
| R-2 | 对账归属失效（agent 不给 step_index 且命令深度改写）→ 假"未执行"→ 误降档 | 归一化匹配兜底（§2）；**全零归属但 run_results 非空 → 记 `attribution_unavailable: true`，不触发 `incomplete_execution` 标注**，报告如实展示原始命令清单（误报防线优先于命中） | 无需回退（保守语义本身即回退态） |
| R-3 | 审计误报降档干净代码（AC-S5-06 红线） | 三重豁免防线（§1）+ 干净 fixture 单测 + hits 必附证据可人工复核 + 只降档不阻断 | 单条规则可疑时以最小 diff 收紧该规则触发条件（规则彼此独立，不设运行时开关——极简） |
| R-4 | prompt 批次后 Prompt Cache 命中率跌破新基线预期 | 批次集中一次 + 离线字节断言先行 + 真跑复采对照（9.1） | 逐段 bisect 新增段落找出误入的动态片段（断言应先拦住） |
| R-5 | `expected_results` dict→list breaking 触碰旧 checkpoint/旧测试 | reporting/回验对旧 dict 防御容忍（7.5）+ 兼容单测 | 无需回退（消费侧全部 `.get()`+isinstance） |
| R-6 | ExecutionResult 新增键触碰既有构造点 | TypedDict 无运行时强制，旧构造不炸；execution.py 两处构造点（:1386/:1750）补默认值；消费侧一律 `.get()` | — |
| R-7 | reporting 返回契约扩展与 CP-C2-5 断言冲突（reporting.py:486 "仅两键"） | 更新该断言为"两键 + honesty_audit 单值"，红线原意（不碰 list 通道）以显式断言保留 | — |
| R-8 | UI 路由行为变更破坏 sp2/sp3 AppTest 断言 | 受影响用例清单（10.2）逐条改断言，AC-S5-15/16/17 新断言取代旧行为断言 | — |
| R-9 | 活动流跨线程读写竞态 | `deque(maxlen)` 原子 append + UI `tuple()` 快照读；事件为不可变 dict | 极端情况丢尾部若干行（尽力而为语义，可接受） |

### 10.2 受影响的既有测试面（全量回归时重点核对）

- **prompt/schema 断言**：`test_sprint2_b3.py`（planning 主体/CP-B3-10）、sprint3 coding prompt 断言（CP-C1-6 所在）、`test_sprint4_e2.py`（CP-E2-1、含 "max_rounds=10" 类文面断言）、`test_sprint4_e3.py` / `test_sprint4_d1.py`（run_in_sandbox / prepare 工具契约）。
- **reporting 渲染断言**：`test_sprint3_c3.py`（含 CP-C2-5 返回契约与 "✅ 复现成功" 措辞类断言，AC-S5-07 措辞变更必触碰）、`test_sprint3_c3_reinforce.py`、`test_sprint4_e1.py`。
- **UI 路由断言**：`test_plan_review_logic.py`、analysis_progress 相关用例（case④ 行为变更）、execution_monitor 完成判定用例（case⑥bis 新增）。
- **e2e mock 全套**：`test_sprint3_e2e.py`、`test_sprint4_e2e.py`（新 state 字段默认值适配）。

### 10.3 AC → 方案组件映射

| AC | 组件 | AC | 组件 |
|---|---|---|---|
| S5-01 | 7.1 planning 声明（P7/P9） | S5-11 | 7.4 标注 + §2 对账 |
| S5-02 | §5 gate + §6 降级交互 | S5-12 | §3 联动公式 + budget_truncated |
| S5-03 | 7.1 三落点传导 | S5-13 | §4 活动流采集/渲染 |
| S5-04 | 7.2 红线段落 + simulation_notice | S5-14 | §4 三个"不"+ 封顶单测 + 全量回归 |
| S5-05/06 | §1 + 7.3 审计（命中/误报双 fixture） | S5-15/16 | 7.8 progress 页 case④/④bis/④ter |
| S5-07 | 7.4 结论判定 + 措辞渲染 | S5-17 | 7.8 case⑥bis + is_finished |
| S5-08 | 7.4 goal_checks 三态节 | S5-18 | 7.9 term_map + humanize 兜底 |
| S5-09 | 7.5 定性 schema + 对比表删列 | S5-19 | 7.9 prompt 约束段（P8）+ 基线对照 |
| S5-10 | §2 `_reconcile_steps` + 报告对账节 | S5-20 | 7.10 metrics_groups + 降维渲染 + env_info 回读 |
| — | — | S5-21 | 7.11 st.code 路径展示 |

---

## 11. 给全栈开发代理的任务拆分（依赖序）

| 批次 | 内容 | 依赖 | 验收锚点 | 特殊标注 |
|---|---|---|---|---|
| **0** | ① CP-SPK-1 callbacks 传播 spike（mock，定 S5-07 主/回退路径）；② S5-08 路由修复全套（7.8，纯 bug 无依赖）+ GraphController.is_finished | 无 | AC-S5-15/16/17 | **含 spike 检查点**；两项可并行（文件不重叠） |
| **1** | Prompt/schema 静态批次 P1~P9 一次合入 + state.py 全部新字段声明（§8 总表）+ 字节稳定断言更新 | 无 | AC-S5-01/04/09/19 的 prompt/schema 断言部分 | **触发 Prompt Cache 基线重建**（离线维随批落地；在线维复采挂授权点，可延至批次 5 合并真跑） |
| **2** | 诚实链编排层：S5-01 gate + 降级按钮 + `env:` 规则 + 会话覆盖层（9.2）；S5-06 对账/联动/截断；S5-10 多组解析 + env_info 回读修复 | 批次 1（字段/schema） | AC-S5-02/03/10/11/12/20 | gate 幂等纪律见 9.2；execution.py 与 coding.py 为本批独占文件 |
| **3** | 收口判定与展示：S5-03 `core/honesty_audit.py`；S5-04 结论判定/回验/渲染；S5-09 `ui/term_map.py` + 全 UI 裸露点清扫；S5-11 路径展示 | 批次 2（metrics_groups / 对账 / 标记字段） | AC-S5-05/06/07/08/18/21 | fixture 固化（9.4）须在本批前由测试工程师完成 |
| **4** | S5-07 活动流：`core/activity_stream.py` + app.py 注入 + 监控页渲染区 | 批次 0 spike 结论 | AC-S5-13/14 | 可与批次 2/3 并行，但 `ui/pages/execution_monitor.py` 被 0/2/3/4 四批触碰——该文件改动由主控统一收口，避免并行冲突（沿用文件边界隔离范式） |
| **5** | 全量回归（10.2 清单逐条修断言）+ 回归样本靶测（AC-S5-05/07/15/20）+ mock e2e 适配 + Prompt Cache 在线维复采 + 真实链路抽验 | 批次 1~4 | AC-S5-01~21 全覆盖 + sp2/3/4 回归全绿 | **真跑项（基线复采 + 真实 e2e）须 Maria 明确授权，合并为一次授权动作省配额** |

**容量对照（Q-S5-4 已确认裁剪线）**：若进度吃紧，批次 3 的 S5-11 → 批次 3 的 S5-09 → 批次 4 的 S5-07（降规模：只渲染 execution 节点事件）依序顺延；批次 0/1/2 与批次 3 的 S5-03/04 为 P0 六项载体，不可裁。

---

*（全文完：第 1 段 §1~§6 六项裁决；第 2 段 §7~§8 方案与数据契约；第 3 段 §9~§11 集成、风险与拆分。docs/sprint5/architecture.md v1.0 交付完毕，待 Maria 审阅后转全栈开发代理进入批次 0。）*
