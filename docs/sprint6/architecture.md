# Sprint 6 核心架构设计文档

**文档版本**：v1.0（第 1 段 / 共 3 段：本段为 Q-S6-1 ~ Q-S6-6 六项技术裁决；第 2 段为 S6-01~08 技术方案概要与 state/schema 变更；第 3 段为既有机制集成、风险与回归防线、测试策略、六批次开发骨架、勘误与假设留档、AC 映射表）
**日期**：2026-07-13
**作者**：架构师代理
**对应 PRD**：`docs/sprint6/prd.md` v1.0（Maria 已确认，2026-07-13；开放问题 Q-S6-1~6 全部在本文裁决）
**需求根因输入**：`docs/sprint5/test-reports/2026-07-12_master-browser-walkthrough-blockers.md`（四卡点 A/B/C/D）+ `docs/TODO.md` 2026-07-10 走查留档三条
**体例参照**：`docs/sprint5/architecture.md`
**回归现场样本（只读勿清理）**：`checkpoints.db` thread `task-cdcd432cda49`（走查卡点现场，execution 挂起 user_input interrupt）与 `task-19e21e015017`（no_metrics /"两面计划"现场）+ `workspace/2405.14831/`

> **环境实证基线（2026-07-13 只读取证，直接采信）**：langgraph 1.1.10 / langgraph-checkpoint 4.0.3 / langgraph-checkpoint-sqlite 3.0.3；`Interrupt` 为 dataclass，fields=['value','id']（无 ns/resumable/when）；checkpoints.db 现存 DISTINCT thread_id=20；只读连接 WAL 库会自动重建 -shm（32KB）/ 0 字节 -wal。
> **贯穿硬约束（沿 sp5 + PRD §2.2 产品红线）**：主图 7 节点骨架不变；不新增 interrupt 种类（三类封口）；判定逻辑归确定性代码（换代判定、降级短路、no_metrics、计划交叉检查、任务状态推导，全部不交 agent）；最小单一抽象（不引入页面状态机框架 / 事件总线 / 决策审计子系统）；沿用轮询（不上 WebSocket/SSE）；prompt 改动全部遵守 R-PC4 前缀冻结（HumanMessage 动态通道或尾部独立静态段落，静态变更集中一批一次基线重建）。
> **本 Sprint 架构级特征（先说结论）**：**GlobalState / ExecutionResult 零新增字段**——sp6 全部需求落在 UI 层 / GraphController / 工具回调层 / execution 收尾判定层 / prompt 尾部段落 / config 常量，不触碰 state 契约。这意味着两个走查现场旧 checkpoint 与全部新代码天然相容（S6-06/07 重连挂回免迁移），是"任务可恢复"主题的最大架构红利。

---

## 1. Q-S6-1：过渡态标志落点 + interrupt「换代」确定性判定（S6-01）

### 1.1 关键源码发现：interrupt.id 单锚不充分（对前期取证结论的收紧）

前期取证已证实 `Interrupt.id` 存在且可读（实例 id='f1d4386b2659d3e7929a9d41c3878bc1'）。但本次源码级核验发现其生成机制使**单锚 id 不能覆盖全部换代场景**：

- `langgraph/types.py:483/:489`：`Interrupt.from_ns(value, ns)` → `id = xxh3_128_hexdigest(ns)`——**id 只哈希任务命名空间 ns，不含 payload、不含 interrupt 序号**；
- ns = `f"{checkpoint_ns}:{task_id}"`，`task_id` 由 `(checkpoint_id, checkpoint_ns, step, node, ...)` 哈希（`langgraph/pregel/_algo.py:930/:942`）；
- 推论：**跨节点 / 跨 superstep 的 interrupt，id 必变**（node 与 checkpoint_id 均变）；但**同一节点同一次任务执行内的串行 interrupt（resume 后节点重放、消费 idx=0 的 resume、随即对下一项再 interrupt），ns 不变 → id 不变**。这正是 coding gate「一次一项、重跑再查」串行索要多凭证的既有形态（sp5 §9.2），也是 gate 对非法 resume「重新 interrupt 同一项」的形态（coding.py:585）。

即：**id 相同 ≠ 同一代提问**。若 UI 以 id 单锚判定"换代"，串行 gate 的第二问会被误判为"同一 interrupt 还没消费" → 过渡态永久驻留（新形态死锁，比误提交隐蔽）。

### 1.2 结论：复合 interrupt_token = id + payload 指纹，三道防线各司其职

**换代判定锚 = `interrupt_token`**（GraphController 新增只读方法产出）：

```python
# app.py GraphController 新增（与 get_interrupt_payload 同一读路径：
# snapshot.tasks[*].interrupts[0]，主线程 _main_graph.get_state 只读）
def get_interrupt_token(self, thread_id) -> Optional[str]:
    # token = f"{interrupt.id}:{sha1(json.dumps(interrupt.value, sort_keys=True,
    #          ensure_ascii=False, default=str))[:16]}"
    # 无 interrupt → None。payload 指纹只存哈希，敏感 question 文本不外泄。
```

**确定性判定规则（全部纯代码，AC-S6-01/02 验收面）**：

| 观测 | 判定 | UI 行为 |
|---|---|---|
| token == awaiting_token 且该 thread 有存活 resume worker | 同一代，resume 处理中 | 渲染"已收到，处理中"过渡态 + **注册 st_autorefresh** |
| token == awaiting_token 且**无**存活 worker | worker 已消费 resume 又停在同 token（同题重问，1.1 串行场景 / 非法 resume 重问） | 视为**换代**：清 awaiting，渲染新面板 |
| token != awaiting_token（含 payload 变化） | 换代 | 清 awaiting，渲染新面板（禁对新 interrupt 沿用旧提交） |
| interrupt 消失（token=None） | 过渡态退出 | 清 awaiting，落回 render() 既有分发（正常监控/终态/跳转） |

**防误提交 / "同一 interrupt 至多一次 resume"三道防线**：

1. **UI 面（第一道）**：awaiting 期间原面板不渲染（被"处理中"占位取代），物理上无按钮可点；
2. **controller 面（第二道，跨 tab 有效）**：`resume_with(thread_id, payload, expected_interrupt_token=None)` 增可选校验参——非 None 时在**发起线程前**重读当前 token，不一致即拒绝（WARNING + 返回 False，不抛异常）。挡住"迟到的提交"（interrupt 已换代/已消失后才点下的按钮）；
3. **进程面（第三道，跨 tab / 双击窗口期）**：worker/resume 线程登记表从实例属性提升为 **app.py 模块级单一登记表**（`_THREAD_WORKERS: Dict[str, Thread]` + 模块级锁；GraphController 实例共享读写）。`resume_with` 原子 check-and-set：该 thread 已有存活线程 → 拒绝。多 tab = 多 session = 多 controller 实例，但同一 Streamlit 进程——模块级登记表是唯一能横跨 tab 的确定性闸门，同时服务 Q-S6-4 的"在途 vs 孤儿"口径（§4），一个抽象两处复用（极简）。

**过渡态标志落点（session_state 单键）**：`_exec_awaiting_token: Optional[str]`（execution_monitor 页私有 key，缺省 None）。两类面板（`_render_user_input_panel` 提交/降级、`_submit_dev_loop_decision`）提交成功（resume_with 返回 True）后写入当前 token 再 `st.rerun()`；case⑤ 入口先做上表判定。**两类面板统一走同一契约**，不做两套 awaiting（对照 plan_review 的 `_begin_awaiting/_await_phase` 范式——该页锚 revise_count 基线，本页锚 interrupt_token，语义等价、各自单键）。

### 1.3 case⑤ 停轮询通则修订（落档表述，AC-S6-03）

render() 头注释与 dev_loop `_submit` 注释（execution_monitor.py:535 现注释"提交后 st.rerun() 让本页轮询自愈"与实现矛盾，须改写）统一修订为：

> **本页设计通则**：render() 分发中，凡"等待后台变化"的状态（case②等待落盘、case⑤-awaiting 过渡态、case⑦ 正常监控）**必须注册 st_autorefresh**；"停轮询"分支只允许承载"仅用户动作可改变"的状态（终态卡片、待提交的 interrupt 面板、§4 无 worker 的孤儿在途卡片）。新增分支必须先按此通则归类。

### 1.4 备选方案对比

| 方案 | 说明 | 评估 |
|---|---|---|
| A：id 单锚 | awaiting 存 interrupt.id，id 变即换代 | 1.1 已证：同任务串行 interrupt id 不变 → gate 第二问被误判同代 → 过渡态死锁，排除 |
| B：payload 指纹单锚 | 存 payload 哈希 | 同题重问（非法 resume 后 gate 重问同一项）与"未消费"不可区分；且不同节点偶发同 payload 会误判同代，排除 |
| C：id + payload 指纹 + worker 存活三元复合（**推荐**） | 上表四行判定 | 每个信息源都已有免费读路径（snapshot / 登记表）；覆盖串行 gate、同题重问、换代、消失四场景；验收可构造换代反例（AC-S6-02） |

### 1.5 落点文件

`app.py`（GraphController：get_interrupt_token / resume_with 校验参 / 模块级 `_THREAD_WORKERS` 登记表 + `has_active_worker(thread_id)` 只读方法）、`ui/pages/execution_monitor.py`（case⑤ awaiting 分支 + 两面板提交改造 + 通则注释修订）。零 state / 零节点 / 零 interrupt payload 契约改动。

---

## 2. Q-S6-2：降级贯穿——记账 schema、注入通道、短路实现位（S6-03）

### 2.1 PRD 勘误先行：记账早已存在且非空，缺的是"读"不是"写"

走查报告 §二-C 与 PRD S6-03 的立论"`degraded_nodes=[]`，降级未记账"看错了字段：`degraded_nodes` 是节点级降级清单（ReAct 失败语义），凭证降级的记账字段是 sp5 已落地的 **`credential_degradations`**（state.py:263，coding 前置 gate 单点整 dict 回写）。现场取证实证记账**健在**：thread `task-cdcd432cda49` 中 `credential_degradations={'env:OPENAI_API_KEY': '若按论文默认设置使用 GPT-3.5-turbo…需要提供对应的 API 凭证。'}`，且 `execution_result.degraded_credentials=['env:OPENAI_API_KEY']`（execution.py:2092 收尾快照链路也通）。**PRD 需求 1（记账入 state）已由 sp5 完成，sp6 零新增字段**；勘误正式留档见 §13/E-1。

据此，S6-03 的真实缺口收敛为三个（均为"消费侧"）：

1. **execution agent 上下文零注入**：coding 侧已注入（coding.py:303-311，`_build_coding_context` 非空才注入 `credential_degradations`，修复回合同样经过该函数——AC-S6-07 的 coding 半边其实已在位）；但 `_build_execution_context`（execution.py:1083-1111）payload 只有 work_dir/execution_steps/environment/max_rounds/修复反馈，**无降级信息**——走查中 execution agent"对用户拒绝零记忆"的直接原因。
2. **interaction 工具无短路**：`request_user_input`（interaction_tools.py:64）四步语义只查 `.secrets` 去重，agent 对已拒凭证再索要照样 `interrupt()`。
3. **键形态不一致**：gate 记账键来自计划声明 `purpose_key='env:OPENAI_API_KEY'`（sp5 `env:<VAR>` 约定），而 agent 工具路径自由生成 `purpose_key='openai_api_key'`（现场挂起 interrupt 实证）——不定规范化规则，短路匹配必然漏。

### 2.2 键规范化规则（确定性，短路匹配唯一口径）

```python
# interaction_tools.py 新增纯函数（两侧归一后精确比对）
def _normalize_purpose_key(key: str) -> str:
    """'env:OPENAI_API_KEY' 与 'openai_api_key' 归一为同一形态。
    规则：strip → 剥离一次 'env:' 前缀 → lower → 非 [a-z0-9] 连续段折叠为单个 '_'
    → 去首尾 '_'。例：'env:OPENAI_API_KEY' → 'openai_api_key'；
    'git_credential:github.com' → 'git_credential_github_com'。"""
```

- 归一只用于**降级短路比对**；`.secrets` 落盘 / mask / 计划声明仍用原始 purpose_key（不动 sp4 全套机制）；
- 误报防线：归一是保守单射方向的折叠——不同语义键折叠碰撞的后果是"多短路一次"（拒发 interrupt、返回降级指令），不产生副作用、不丢数据；且现实键空间（env:X / hf_token / git_credential:host）互不碰撞，碰撞用例入测试矩阵。

### 2.3 短路实现位裁决：工具层（工厂闭包），沿 make_run_in_sandbox_tool 先例

| 方案 | 说明 | 评估 |
|---|---|---|
| A：节点层（coding gate / execution 编排各拦一道） | 节点在调工具前过滤 | 拦不住：短路点在**agent 发起工具调用时**，节点层看不到 tool_calls 逐条参数，除非侵入 react_base 工具执行循环（违反"不改 react_base"约束），排除 |
| B：工具层工厂闭包（**推荐**） | `make_request_user_input_tool(degraded: Dict[str, str])`，节点装配工具集时闭包绑定 state 的 credential_degradations 快照 | 项目既有范式（execution.py:869 `make_run_in_sandbox_tool` / run_command_tool.py:59 `make_run_command_tool` 同款）；单点实现两 agent 共用；docstring **字节零改动**（CP-B1-5 Prompt Cache 纪律不受扰——工厂返回的 tool schema 与现 @tool 完全一致，以字节断言锁定） |

**短路语义（工具体内、`.secrets` 去重之后、interrupt 之前插入第 1.5 步）**：`purpose_key` 归一命中已拒集合 → **不 interrupt**，直接返回确定性降级指令串（含 purpose 说明）：`"用户已明确拒绝提供该凭证（{purpose}）。本任务全程不得再索要；请走模拟/mock 路径完成当前步骤，并如实声明模拟范围。"`——返回值仍是纯字符串（BUG-S1-02 刻意例外语义不变）。日志打 `purpose_key` 归一前后值（不打 question 全文，安全纪律不变）。模块级 `request_user_input` 保留为 `make_request_user_input_tool({})` 的默认实例（向后兼容既有 import 与测试面）。

### 2.4 execution / coding 上下文注入（HumanMessage 动态通道，R-PC4 无扰）

- `_build_execution_context`（execution.py:1083）照搬 coding.py:303-311 同款：`credential_degradations` 非空才注入 payload（零降级路径 HumanMessage 字节零扰动）；
- 两节点 payload 同时注入**静态指令常量** `credential_degradations_directive`（模块级字符串常量，两文件引用同一份，内容即 2.3 的"禁再索要、必须走模拟路径、如实声明"三句）——只随降级非空出现，字节恒定，同一 state 下 json.dumps(sort_keys) 幂等，不触任何 system prompt 主体（R-PC4 合规：纯动态通道，**本项不参与 §9.1 的一次性基线重建**）；
- 修复循环各轮可见（AC-S6-07）：两个 context builder 都是每次节点执行重建 payload，state 字段贯穿即免费获得，无需额外机制。

### 2.5 报告链路与边界

记账贯穿至报告复用 sp5 既有通道（`degraded_credentials` 快照 → reporting `credential_degraded` 正交标注 + 强制声明节），零新标注体系（PRD 需求 4 已在位，验收只需回归断言）。撤销通道不做（非目标 4）；贯穿的是结构化决策信息，不引入 agent 间对话通道。

### 2.6 落点文件

`core/tools/interaction_tools.py`（`_normalize_purpose_key` + 工厂化 + 短路步骤，docstring 字节不动）、`core/nodes/coding.py`（:352 工具装配改工厂调用 + directive 常量注入）、`core/nodes/execution.py`（工具装配处（:1312 附近）改工厂调用 + `_build_execution_context` 注入）。零 state 变更、零 prompt 主体变更。

---

## 3. Q-S6-3：no_metrics 判定落点、早停 N、定向 fix_hint 注入形态（S6-04）

### 3.1 现状机制复盘（矛盾同屏的确切成因）

`_classify_execution`（execution.py:202）只看 exit/stderr——全 exit 0 → `ErrorCategory.NONE` + summary="执行成功"（:235）；而 B 档 success 在 `_build_execution_result`（:1662）另判 `exit_ok and len(metrics) >= 1`。两判定间没有第三态：metrics 为空时 success=False，errors[0] 却被写成 `[error_category=none] 执行成功`（:1677）——B-2 现场原样复现。分类结果与指标结果**在收尾从未合流**，这就是判定落点。

### 3.2 结论：execution 收尾"步骤 4.75"确定性合流函数

- `ErrorCategory` 增成员 `NO_METRICS = "no_metrics"`（既有 enum 加一成员，不新建分类体系），**加入 `AUTO_FIXABLE`**（缺指标输出属代码/调用层可修——B-2 实证 coding 已写出带 `<METRICS>` 的 run_experiment.py 只是没被调用，回 coding 定向修复是对症的；上限由早停与既有 MAX_FIX_LOOP_COUNT 双重约束）。
- 新增纯函数（execution() 主流程步骤 4.75，`_parse_metrics`/`_collect_grouped_metrics` 之后、`_build_execution_result` 之前）：

```python
def _apply_no_metrics(feedback, metrics, metrics_groups, exit_ok) -> ExecutionFeedback:
    """exit_ok ∧ category==NONE ∧ metrics 空 ∧ metrics_groups 空 → 改判 NO_METRICS。
    summary（专属文案，即定向 hint 本体）："代码跑通但未产出指标：全部命令 exit 0，
    但未发现 <METRICS> 输出或 outputs/*/summary.json。请检查执行步骤是否调用了
    实验主入口，并按输出约定写出指标。" fix_hint 同文。其余情形原样返回。"""
```

- 判定含 `metrics_groups`（组指标非空说明实验有产出，不该扣 no_metrics 帽子——保守防误报；B-2 现场两者均空，命中）。幂等纪律③不破：改判发生在 `_build_execution_result` 之前，随 exec_result 一次 commit；guard 路径 `_feedback_from_committed_result`（:2118）经 `ErrorCategory(raw)` 解析 `no_metrics` 天然有效、auto_fixable 由 AUTO_FIXABLE 成员资格自动推导，零改动。

### 3.3 定向 fix_hint 注入形态：复用 errors[0]/summary 既有通道，零新通道

关键观察：coding 修复回合的反馈来自 `_digest_execution_feedback`（读 `execution_result.errors` 全部 + logs 尾部，coding.py:236 起）；execution 修复回合反馈来自 `_build_execution_context` 的 `last_error_summary.errors`（execution.py:1108）；面板与报告读同一 errors[0]。**把定向指令写进 NO_METRICS 的 summary/fix_hint（3.2 文案），三个下游立即全部免费收到**——不加 payload 键（interrupt#2 payload 键结构逐字冻结是 AC-S4-05 命门，不能碰）、不加 state 字段。fix_loop_history.fix_strategy（:1802）与 interrupt#2 payload.fix_hint（:1847）也随之携带。面板措辞经 term_map `error_category` 表增 `no_metrics` 中文条目（与 MF-4 同批清扫）。

### 3.4 早停：N=2，落点 `_maybe_interrupt_or_return`

- **判据（确定性）**：本轮 feedback.category==NO_METRICS ∧ `fix_loop_history` 尾部已有连续 ≥N 条 `error_category=="no_metrics"` → 跳过 retry_coding 分支，走既有 interrupt#2 路径（决策面板文案含"已连续 N+1 轮零指标，自动修复无进展"）。"无进展"口径 = 类别连续复现（no_metrics 类别本身编码了"指标仍为零"，不再另做指标 diff——极简）。
- **落点**：`_maybe_interrupt_or_return`（execution.py:1948）auto_fixable 分支的准入条件增加一项 `not _no_metrics_stalled(state, feedback)`；早停走的是**既有** interrupt#2 通道（三类 interrupt 封口不破）。
- **N=2 取值理由**：第 1 轮 no_metrics 时 coding 已收到定向 hint；若第 2 轮修复后仍 no_metrics，说明定向信息已注入仍无效（大概率是"两面计划"级结构问题，S6-05 的治理面），第 3 轮自动重试的期望收益趋零而每轮成本 ≈ coding 12 轮 + execution ≤30 轮真配额。N=2 即"定向修复恰给一次完整机会"，PRD 红线（有限值、不空烧）满足。config 常量 `NO_METRICS_EARLY_STOP_ROUNDS: int = 2`。

### 3.5 落点文件

`core/nodes/execution.py`（enum 成员 + `_apply_no_metrics` + `_no_metrics_stalled` + 步骤 4.75 接线）、`config.py`（1 常量）、`ui/term_map.py`（error_category 增 1 条）。零 state schema 变更、零 prompt 变更（hint 走 errors 数据通道非 prompt 通道）。

---

## 4. Q-S6-4：任务状态确定性推导 + 列表页枚举读路径（S6-07）

### 4.1 状态推导规则表（纯函数，输入 = snapshot + 进程级 worker 登记表）

新增纯函数 `derive_task_status(snapshot_view) -> str`，输入统一由 GraphController 只读组装（`_make_config(thread_id)` 经 `_main_graph.get_state`，checkpoint_ns="" 约束天然满足；worker 存活查 §1 模块级 `_THREAD_WORKERS`）。规则**按优先级自上而下短路**：

| # | 条件（确定性） | 状态 | 挂回行为（S6-06 通道） |
|---|---|---|---|
| R1 | snapshot 不存在 ∨ values 为空（is_finished 同款防误判，app.py:254） | （不列出） | — |
| R2 | `values.error` 非空 | **失败** | 挂回 → 监控页 case③ 终态卡片 |
| R3 | `current_step == "cancelled_by_user"` | **已终止** | 挂回 → 监控页 case④ 终态卡片 |
| R4a | `next` 为空元组 ∧ `report_path` 非空 | **已完成** | 挂回 → 报告页 |
| R4b | `next` 为空元组 ∧ `report_path` 为空 | **失败（未产报告）** | 挂回 → 监控页 case⑥bis 卡片（口径与 AC-S5-17 一致） |
| R5 | `next` 非空 ∧ 存在 interrupt | **等待输入（可挂回应答）** | 挂回 → 按 interrupt_kind 路由：planning→审核页；dev_loop/user_input→监控页面板；resume 有效性 = AC-S6-16 |
| R6 | `next` 非空 ∧ 无 interrupt ∧ 该 thread 有存活 worker | **进行中** | 挂回 → 监控页 case⑦ 正常轮询 |
| R7 | `next` 非空 ∧ 无 interrupt ∧ 无存活 worker | **已中断（在途孤儿，需显式续跑）** | 挂回 → 监控页新终态卡片：展示现状 + 显式「继续执行」按钮（见 4.2），**停轮询**（仅用户动作可改变，符合 §1.3 通则） |

R5~R7 即 PRD 问句"进程重启后无 worker 的在途任务口径"的答案：**有 interrupt 的挂起任务 = 等待输入**（挂回应答即恢复，resume 本身就是用户显式动作，无副作用重放疑虑——LangGraph 从 checkpoint 重放节点到 interrupt 点属既有 sp4 语义）；**无 interrupt 的在途任务 = 已中断**，区分锚 = 进程级 worker 登记表（进程重启后登记表必空，快照里 next 非空即孤儿，判定确定）。

### 4.2 孤儿任务显式续跑（产品红线："挂回=展示现状，推进须显式触发"）

- GraphController 新增 `resume_task(thread_id)`：新 daemon worker 执行 `graph.invoke(None, config)`（LangGraph 语义：从最后 checkpoint 重启在途节点，**该节点将从头重放、副作用重新发生**）。
- 红线落实：该方法**仅**由 R7 卡片上的显式按钮调用，按钮文案明示"当前节点将从断点重新执行（其间的命令/调用会重新发生）"；列表页"挂回"本身绝不调用它（AC-S6-16 显式动作断言面）。R6/R5 状态不渲染该按钮。

### 4.3 枚举读路径与 WAL 副作用

- **枚举**：GraphController 新增 `list_threads()`——`sqlite3.connect(f"file:{CHECKPOINT_DB_PATH}?mode=ro", uri=True)` 执行 `SELECT thread_id, MAX(checkpoint_id) FROM checkpoints GROUP BY thread_id ORDER BY 2 DESC`（checkpoint_id 时间有序，天然新任务在前；表结构 = 取证 C 段：checkpoints(thread_id, checkpoint_ns, ...)），随后逐 thread 走既有 `_main_graph.get_state` 组装状态与论文标识（`paper_meta.title_zh → title → user_input` 三级回退）。不绕过 GraphController、不新建第二套读栈。
- **WAL 副作用评估**：只读连接会重建 -shm（32KB）/ 0 字节 -wal（取证 D 段）——这是 SQLite WAL 正常行为，非损伤；量级 20 thread × 每次进页一次枚举，开销可忽略。**频控设计**：任务列表页是"仅用户动作可改变"页面（§1.3 通则），**不注册 st_autorefresh**，枚举只在进页/手动刷新按钮时发生一次；每 thread 一次 get_state（20 次读）在 WAL 并发下与 worker 写互不阻塞（sp2 S-2 spike 既证）。
- **反序列化警告**：逐 thread get_state 会对旧 checkpoint 触发 `Deserializing unregistered type core.state.ExecutionMode` 警告 ×N——功能无损但刷日志，且未来 langgraph 默认阻止（风险登记 §10/R-S6-A3，本 Sprint 不改 ExecutionMode 存储形态）。

### 4.4 落点文件

`app.py`（derive_task_status 纯函数 + list_threads + resume_task）、新增 `ui/pages/task_list.py`（枚举表 + 状态徽标 + 一键挂回；无删除/搜索/分页）、`config.py`（`STREAMLIT_PAGE_TASKS` 路由常量）、`ui/pages/execution_monitor.py`（R7 孤儿卡片，随 §12 批次 3 单收口窗口合入）、`ui/pages/paper_input.py`（入口导航链接）。

---

## 5. Q-S6-5：ExecutionResult.logs 定案（MF-3）+ MF-7 stdout 入账方案

### 5.1 消费方全清单核查（先查后裁，PRD 要求）

生产代码中 `execution_result.logs` 的全部消费方恰 4 处，**全部按 str 消费**：

| 消费方 | 用法 |
|---|---|
| `ui/pages/execution_monitor.py:163`（`_logs_truncated`） | `isinstance(logs, str)` + 子串探测 |
| `ui/pages/execution_monitor.py:429`（`_render_sandbox_info`） | `st.code(str(logs))` |
| `core/nodes/coding.py:236`（`_digest_execution_feedback`） | `logs or ""` → 尾部 ≤2000 字符 |
| `core/nodes/execution.py:1103`（`_build_execution_context` 修复反馈） | 非 str 时 `str(logs)` 防御 + `_tail` |

### 5.2 定案：**维持 str 声明**（"改声明"方向，且声明现状已经是 str——PRD 勘误）

代码实证：`core/state.py:154` 声明即 `logs: str`，`docs/technical-architecture.md:388` 同为 `logs: str`，生产写入点唯一（execution.py:1684，`mask_value(_aggregate_logs(...)) or ""`，恒 str）。**"声明 List[str] 实存 str"的违约在当前代码库不存在**（07-10 留档时点误读或其后已被顺手修正，勘误留档 §13/E-2）。故 MF-3 收敛为三件确定性工作：① 三方一致以**守门用例**锁定（类型断言 + 消费方清单核查用例，AC-S6-19）；② PRD/TODO 勘误留档；③ "stdout 完整入账"核查（下节）。

### 5.3 stdout 入账现状与 MF-7 数据源

- **入账现状（勘误 §13/E-3）**：`_aggregate_logs`（execution.py:1609）本就聚合 install_log + **每步 stdout/stderr**（含步骤头 exit/cmd）；B-1 现场 len(logs)=87161 实证多步 stdout 在账。07-10"仅 install 段 ~6KB"的印象来自 B-2 现场（14 步计划仅执行 1 步，logs=1265）——是执行覆盖率问题（S6-04/05 的病），不是聚合缺陷。
- **既知衰减点（如实留档，不在 sp6 修）**：run_results 双通道合并中"messages 回读"通道的每条 ToolMessage 受 `TOOL_RESULT_MAX_LENGTH=8000` 截断（execution.py:1126 容忍解析）；收集器通道全量（`SANDBOX_OUTPUT_MAX_BYTES=1MiB`/流，尾部保留语义，local_venv.py:333）。MF-7 只消费尾部，两级截断语义下尾部恒可用。
- **MF-7 方案**：dev_loop 决策面板增"最近一次运行输出（尾部）"区——数据源 = `state["execution_result"]["logs"]` 尾部 `DEV_LOOP_PANEL_LOG_TAIL_CHARS = 4000`（config 新常量）+ 既有 `payload.representative_stderr` expander 保留。**从 state 读而非改 interrupt#2 payload**——payload 键结构逐字冻结（AC-S4-05 命门）零触碰；可行性由既有幂等设计保证：interrupt#2 必经"先落盘 execution_result、self-loop 重入再 interrupt"路径（execution.py:1969-1977），故面板渲染时 poll_state 必能读到本回合 logs。空 logs → 占位说明（AC-S6-23）。`_render_dev_loop_decision_panel` 签名增 `state` 参数（调用点 case⑤ 已持有 state）。logs 已在写入点 mask（:1684），渲染零再脱敏。

### 5.4 落点文件

`ui/pages/execution_monitor.py`（面板渲染区 + 签名）、`config.py`（1 常量）、`tests`（三方一致守门用例）。**零生产类型改动**。

---

## 6. Q-S6-6：在途阶段标签机制——UI 以 snapshot.next 只读推断（S6-02）

### 6.1 结论与依据（按"回归面更小"裁决）

| 方案 | 回归面 | 评估 |
|---|---|---|
| A：节点入口写 current_step | LangGraph 节点**只在 return 时提交 state**，"入口先写一次"必须把 7 节点逐一拆成"写标签子节点 + 本体"或引入 pre-hook —— 图结构变更（违反 7 节点红线）+ 每节点多一次 checkpoint 写放大 + 全部节点测试面重跑 | 排除 |
| B：UI/controller 以 snapshot.next 推断（**推荐**） | 一个只读 controller 方法 + 两页渲染逻辑；零 state、零节点、零图、零 checkpoint 变更 | 两现场取证 `snapshot.next=('execution',)` 均可靠反映在途节点；读路径与 is_interrupted/poll_state 同栈 |

GraphController 新增只读方法 `get_phase(thread_id) -> Dict`：`{"active_node": snapshot.next[0]（next 非空时）| None, "current_step": values.current_step}`（与 is_finished 同一读路径与空快照防御）。

### 6.2 消费规则（确定性，注意 interrupt 时 next 也非空）

- **执行监控页 `_render_progress`**：阶段指示 = `active_node` 存在 → "「{humanize(active_node)}」进行中"；否则回落 current_step 既有口径。case 分发顺序保证：interrupt 分支（case⑤）先于 case⑦，故 case⑦ 内 active_node 必为真在途（或 R7 孤儿——文案区分交由 §4 卡片，本页 case⑦ 只在有 worker/interrupt 之外到达）。
- **进度页 case④bis**（analysis_progress.py:599）判据由 `current_step ∈ {coding, execution}` 扩为 `current_step ∈ {coding, execution} ∨ active_node ∈ {coding, execution}`——approve 后 coding 在途（current_step 仍 ='planning'）即切监控页（AC-S6-05）；case④（interrupt 分发）保持在前，planning interrupt 时 next=('planning',) 不会误切。
- **进度页四段进度条 `_segment_status`**（analysis_progress.py:121）：active_node 命中四上游节点时该段显示"进行中"而非依 current_step 推成 pending——同一只读数据源顺带修正标签滞后（AC-S6-04）。

### 6.3 落点文件

`app.py`（get_phase）、`ui/pages/execution_monitor.py`（阶段指示，随批次 3 收口）、`ui/pages/analysis_progress.py`（case④bis 判据 + 段状态）。

---

> 以下为第 2 段：S6-01~08 技术方案概要与变更总表。第 1 段已裁决项只做衔接引用；本段补齐 S6-05、S6-06 与机械修复包的文件级方案。编号续接。

---

## 7. S6-01~08 逐项技术方案

### 7.1 S6-01 过渡态统一契约（P0）＝ §1 全量裁决

awaiting 单键 + 复合 token 换代判定 + 三道防重复提交防线 + 通则落档。验收 AC-S6-01~03。

### 7.2 S6-02 在途标签 + 自动切页（P1）＝ §6 全量裁决

get_phase 只读推断。验收 AC-S6-04~05。

### 7.3 S6-03 降级贯穿（P0）＝ §2 全量裁决

记账已在位（勘误）；补 execution 注入 + 工具层短路 + 键规范化。验收 AC-S6-06~08（AC-S6-06 以 `task-cdcd432cda49` 同构场景为回归靶——mock e2e 自证教训见 PRD S6-03 测试盲区警示）。

### 7.4 S6-04 no_metrics 专属处置（P1）＝ §3 全量裁决

enum 成员 + 步骤 4.75 合流 + errors[0] 通道定向 hint + N=2 早停。验收 AC-S6-09~10。

### 7.5 S6-05 计划自洽交叉检查 + 数据可得性警示 + planning 约束（P1）

- **新增纯函数模块 `core/plan_checks.py`**（零 LLM、零 state 写入，与 honesty_audit 同范式）：`check_plan(plan, resource_info) -> List[Dict]`，每条 `{"rule": str, "message": str}`（rule 三值字符串字面量，不建 Enum）。三条规则：
  1. **W1 数据步骤脱节**：`data_preparation` 非空 ∧ 全部 `execution_steps` 的 name+command 文本均未命中数据语义关键词表（`data/dataset/download/prepare/预处理/数据` 等静态小表）→ 警示"计划声明了数据准备工作，但执行步骤中没有任何数据相关步骤"。
  2. **W2 指标产出脱节**：`expected_results` 非空（含任一 trend 结构或描述文本）∧ 全部步骤文本未命中实验/指标语义关键词表（`run/train/eval/experiment/metric/summary.json/实验/评测/指标` 等）→ 警示"计划有指标性预期，但执行步骤中没有产出指标的步骤"（"两面计划"现场 `task-19e21e015017` 的 14 步原文即命中靶，AC-S6-11）。
  3. **W3 数据不可得**：resource_info 无可用数据集线索（`external_resources` 无 dataset 类条目 ∧ selected_repo 为 None）∧ `data_preparation` 非空 → 警示"所需数据集未在资源侦察中找到，请决策"（A-S6-2：复用审核警示位，不新 gate/interrupt——审核时点信息已足够，维持 PM 假设）。
- **误报防线与命中防线同权重**（AC-S6-11）：关键词表宁窄勿宽；干净计划 fixture（含数据步骤 + 跑实验步骤）零警示为同权重验收；警示**不阻断审批**。
- **展示**：`ui/pages/plan_review.py` 既有"信息完整度评估卡片"位追加警示行（UI 渲染时对 interrupt payload 内的 plan 调纯函数，零 state 变更、零 planning 节点变更）。
- **planning prompt 约束（AC-S6-13）**：planning system prompt **尾部独立静态段落**（沿 sp5 P8 术语段先例）追加一条："执行步骤必须包含运行实验主入口并产出指标的步骤；数据准备声明必须落为对应执行步骤。"——本条是 **sp6 唯一触碰稳定前缀的变更**，构成 §9.1 一次性 Prompt Cache 基线重建批次。
- 落点：新增 `core/plan_checks.py`、改 `ui/pages/plan_review.py`、改 `core/nodes/planning.py`（尾部段落常量）。

### 7.6 S6-06 任务重连：URL 持久化（P0）

- **形态**：`st.query_params["task"] = thread_id`（Streamlit 原生，无新依赖）。写入点 = 输入页 `start_task` 成功后；清除点 = "返回输入页开启新任务"类动作。
- **重连流程（app.py main()）**：`_init_session_state` 之后新增一步 `_restore_from_query_params(controller)`——仅当 `query_params` 含 task ∧ `session_state.thread_id` 为空时激活：校验该 thread 在 checkpoints 中存在（§4 R1 判定），恢复 `thread_id`，并按 `derive_task_status` 的挂回列（§4.1 表）设置 `current_page`。**无 task 参数或 session 已有 thread_id 时函数直接 return——无参数路径与现状字节级等价**（AC-S6-14 防回归红线；R-S6-4 缓解）。
- **resume 有效性**：重连后的 controller 是新实例，但 resume_with 本就每次新建独立 SqliteSaver + graph（app.py:205-217 既有线程模型），resume 语义与原 session 等价（AC-S6-16）；活动流为纯内存尽力而为语义，重启后空属预期（sp5 既定）。
- **多 tab 红线**：同一 interrupt 至多一次 resume 由 §1 第二/三道防线（token 校验 + 进程级登记表）承载，与 AC-S6-02 同源判定，不做 tab 间状态同步（非目标 11）。
- 落点：`app.py`（_restore_from_query_params + main 接线）、`ui/pages/paper_input.py`（start_task 后写 query params）。

### 7.7 S6-07 任务列表页（P1）＝ §4 全量裁决

枚举 + 状态徽标 + 挂回（复用 7.6 通道：点击 = 写 query params + thread_id + 路由）。验收 AC-S6-15~16。

### 7.8 S6-08 机械修复包 MF-1~7

| # | 裁决与落点 |
|---|---|
| MF-1（P0） | **采 PIP_CACHE_DIR 方案、否 --no-cache-dir**：`--no-cache-dir` 只能拼进 `_pip_install_with_retry` 自建命令（local_venv.py:474），**管不住 agent 在 run_in_sandbox 里自己敲的 `pip install`**；环境变量在 `_build_sandbox_env`（local_venv.py:135）单点强制 `env["PIP_CACHE_DIR"] = str(SANDBOX_PIP_CACHE_DIR)`（**放在 extra_env 合并之后、无条件覆盖**——白名单 `PIP_*` 前缀（:124）可能从宿主继承指向 home 的 PIP_CACHE_DIR，必须压制）即可覆盖 prepare_venv / run_in_venv / 重试全部路径；`core/tools/run_command_tool.py` 的 env 构造同点注入（coding smoke 侧）。缓存目录取 `SANDBOX_PIP_CACHE_DIR: Path = WORKSPACE_DIR / "pip-cache"`（config 新常量，进 ensure_directories）：落 /data 卷零 home 占用，跨任务共享省 torch 级重复下载；与 PRD"随任务清理"措辞的偏差记 §13/A-2（PRD 本就交开发二选一，本裁决取第三变体且理由留档）。顺带更新 :107 行"HOME（pip 缓存 ~/.cache/pip）"注释。AC-S6-17 |
| MF-2（P2） | `ui/pages/paper_input.py` 卡片渲染处新增纯函数 `_humanize_authors(authors) -> str`：str 直通 / dict 取 name 键 / list 逐项递归取 name / 其余 str() 兜底截断——走查异形样本 `{'misc': {}, 'name': 'Bernal...'}` 入单测。AC-S6-18 |
| MF-3（P1） | ＝ §5 裁决（守门用例 + 勘误留档，零类型改动）。AC-S6-19 |
| MF-4（P2） | **渲染点 humanize，生成点不动**——决定性论据：errors[0] 的 `[error_category=...]` 前缀是 guard 路径 `_feedback_from_committed_result`（execution.py:2119）重建分类的**机器锚点**，生成点 humanize 会破坏幂等重建。UI 侧复用既有 `_parse_node_error` 剥前缀逻辑，抽为共享纯函数供 dev_loop 面板 `execution_errors` 渲染（execution_monitor.py:594）与报告渲染消费；term_map 已有 error_category 域兜底。AC-S6-20 |
| MF-5（P2） | 摘除 `core/tools/pwc_tools.py` 及 resource_scout 引用与 prompt/工具装配残留、删 config `PWC_*` 四常量；降级链改 deepxiv github_url → web search；相关测试与文档同步（全局 PRD R7 已应用，架构文档随批勘误）。注意 pwc 工具 docstring 曾进 resource_scout 工具 schema 前缀——**摘除属静态前缀变更，并入 §9.1 同一次基线重建批次**。AC-S6-21 |
| MF-6（P2） | `app.py main()`：`st.set_page_config` 后、`_get_controller()` 前，controller 尚未创建（`"graph_controller" not in st.session_state`）时先渲染 `st.spinner("系统初始化中（首次启动约 40 秒）…")` 包裹 controller 创建——冷启动耗时在 build_graph/checkpointer 初始化（走查 §三-5），提示先于耗时段落地即可见；只加提示不提速。AC-S6-22 |
| MF-7（P1） | ＝ §5.3 裁决（state 读 logs 尾部 + 常量，零 payload 契约变更）。AC-S6-23 |

---

## 8. 变更总表（state / schema / config / 会话键）

**GlobalState / ReproductionPlan / ExecutionResult：零新增、零变更字段**（本 Sprint 架构特征，§头部）。全部变更如下：

| 项 | 位置 | 类型/默认 | 写入方 | 消费方 |
|---|---|---|---|---|
| `NO_METRICS_EARLY_STOP_ROUNDS` | config.py | int = 2 | — | `_no_metrics_stalled`（§3.4） |
| `DEV_LOOP_PANEL_LOG_TAIL_CHARS` | config.py | int = 4000 | — | dev_loop 面板日志尾部区（§5.3） |
| `SANDBOX_PIP_CACHE_DIR` | config.py | Path = WORKSPACE_DIR/"pip-cache" | — | `_build_sandbox_env` / run_command_tool（MF-1） |
| `STREAMLIT_PAGE_TASKS` | config.py | str = "tasks" | — | _PAGE_MAP / 任务列表页路由 |
| 删除 `PWC_*` 四常量 | config.py | — | — | MF-5 摘除随批 |
| `ErrorCategory.NO_METRICS` | execution.py（节点本地 enum） | "no_metrics"，入 AUTO_FIXABLE | `_apply_no_metrics` | 分类/路由/errors 前缀/term_map |
| `_exec_awaiting_token` | session_state（execution_monitor 页私有） | Optional[str] = None | 两面板提交 | case⑤ awaiting 判定（§1.2） |
| `_THREAD_WORKERS` 登记表 | app.py 模块级 | Dict[str, Thread] + Lock | start/resume/resume_task | 防重复 resume（§1.2）/ R6-R7 口径（§4.1） |
| query param `task` | URL | str | start_task / 挂回 | `_restore_from_query_params`（7.6） |
| GraphController 新只读方法 | app.py | get_interrupt_token / has_active_worker / get_phase / list_threads / derive_task_status | — | UI 三页 |
| GraphController 新写方法 | app.py | resume_with 增校验参（向后兼容缺省 None）/ resume_task | UI 显式动作 | §1.2 / §4.2 |

**新增模块清单**：`core/plan_checks.py`（7.5）、`ui/pages/task_list.py`（§4）——共 2 个，无其他新目录/新抽象层。旧 checkpoint 兼容：零 state 变更 ⇒ 两现场 thread 直接可被新代码消费（列表页/重连/换代判定全部可用真库副本驱动验收）。

---

> 以下为第 3 段（终段）：既有机制集成 + 风险与回归防线 + 测试策略 + 六批次开发骨架 + 勘误/假设留档 + AC 映射。编号续接。

---

## 9. 既有机制集成方式

### 9.1 sp6 Prompt Cache 静态变更批次（R-PC4 集成）

sp6 触碰稳定前缀的静态变更**恰两项**，合并为一个批次（§12 批次 1）一次合入、只重建一次基线：

| # | 变更 | 文件 | 性质 |
|---|---|---|---|
| P-S6-1 | planning 尾部独立静态段落："执行步骤须含实验主入口并产出指标；数据准备须落为执行步骤"（7.5） | planning.py | 尾部段落追加，跨任务字节恒定 |
| P-S6-2 | pwc 工具摘除（resource_scout 工具 schema 前缀变更，MF-5） | resource_scout 装配 + pwc_tools 删除 | 工具 schema 摘除 |

**不进前缀的动态/数据通道变更（不触发基线重建，随各自批次合入）**：降级 directive 常量（§2.4，HumanMessage payload，非空才现）、execution 上下文降级注入（§2.4）、no_metrics 定向 hint（§3.3，errors 数据通道）、`request_user_input` 工厂化（§2.3，docstring 字节零改动 + 字节断言锁定）。守门沿 sp5 双维范式：离线字节稳定断言随批次 1 落地；在线复采（三脚本）挂 §12 批次 5 与真跑合并一次 Maria 授权。

### 9.2 interrupt / 幂等注意点

- **换代判定与 gate 串行幂等的交点**（§1.1）：gate"一次一项、重跑再查"产生的第二问 token 中 payload 指纹必变（question/purpose_key 不同）→ UI 正确渲染新面板；同题重问（非法 resume 路径）依赖"worker 已死 ∧ token 相同 → 视为换代"兜底——**该规则是防新形态死锁的关键，验收必须含此反例**（AC-S6-02 扩展场景）。
- **resume_with 校验参向后兼容**：`expected_interrupt_token` 缺省 None = 不校验（plan_review 等既有调用零改动；planning interrupt 的防重复由该页 awaiting 范式既有承载，sp6 不强制迁移——最小改动面）。
- **resume_task（§4.2）与副作用**：`invoke(None, config)` 重放在途节点属 LangGraph 既有语义；红线=仅显式按钮触达。R7 卡片渲染前须再查一次登记表（防 TOCTOU：判定与渲染间 worker 状态变化，按钮点击时 resume_task 内部再原子 check-and-set，与 §1.2 第三道防线同一闸门）。
- **interaction 工具短路与 checkpoint 重放**：短路路径**不调 interrupt**、无 checkpoint 交互，纯函数返回——与 LangGraph resume 值按调用序重放机制无交集（短路不占用 interrupt 序号，不会造成 resume 串位；须单测锁定"短路调用不推进 scratchpad interrupt 计数"这一事实——它天然成立，因为根本没调 interrupt()）。

### 9.3 脱敏与安全纪律衔接（sp4 §9.4 延伸）

- interrupt_token 的 payload 指纹只存**哈希**（§1.2），question 原文不进 session_state/日志；
- 短路返回串只含 purpose 中文说明与归一 purpose_key，无凭证值；
- MF-7 日志尾部区数据源 logs 在写入点已 mask（execution.py:1684），渲染零再处理；
- 任务列表页论文标识取 paper_meta 公开字段，不投影 collected_inputs/敏感面。

### 9.4 与 sp5 既有测试面的碰撞预估（回归修断言清单）

- execution_monitor case 矩阵用例（sp3 E2 / sp5 t43）：case⑤ 增 awaiting 分支、case⑦ 阶段指示改 get_phase——**本页全量用例 ×3 连跑防 flaky（t43 范式）**；
- analysis_progress case④bis 判据变更（AC-S5-16 用例同步）；
- `_classify_execution`/`_build_execution_result` characterization 用例：NONE+零指标场景断言将翻转为 NO_METRICS（B-2 同构 fixture 为新锚）；
- interaction_tools CP-B1-5 docstring 字节断言：改为对工厂产物断言（字节等值锁定）；
- resource_scout pwc 相关用例整组摘除/改写（AC-S6-21）；
- app.py 路由用例：query params 无参数路径回归（AC-S6-14 红线断言）。

---

## 10. 风险登记与回归防线

### 10.1 继承 PRD R-S6-1~6（应对已在各裁决内落实）

R-S6-1（execution_monitor 收口，§12 批次 3 单窗口 + case 全矩阵 ×3）；R-S6-2（换代判定，§1 复合 token + 换代反例）；R-S6-3（R-PC4，§9.1 恰两项集中一批）；R-S6-4（query params 耦合，7.6 无参数路径字节级等价红线）；R-S6-5（pwc 摘除，§9.1/§9.4 测试文档同步）；R-S6-6（真跑配额，§12 批次 5 单授权窗口）。

### 10.2 架构视角新增风险

| 编号 | 风险 | 缓解 | 回退 |
|---|---|---|---|
| R-S6-A1 | **interrupt.id 生成机制属 langgraph 私有实现**（xxh3(ns)，1.1.10 实证），升级可能改算法/语义 | 复合 token 不单赖 id（payload 指纹独立有效）；requirements 锁 langgraph 1.1.10；升级时跑换代判定用例矩阵作金丝雀 | id 取不到时 token 退化为纯 payload 指纹 + worker 存活二元判定（get_interrupt_token 内 getattr 防御） |
| R-S6-A2 | 只读枚举重建 -shm/-wal 副作用（取证 D）+ 高频枚举放大 WAL 检查点扰动 | 列表页不注册 autorefresh（§4.3 频控）；mode=ro URI 连接不写业务数据；20 thread 量级实测无感 | 枚举降级为仅 SELECT DISTINCT（不逐 thread get_state，状态列显示"点击挂回查看"） |
| R-S6-A3 | `Deserializing unregistered type core.state.ExecutionMode` 警告——langgraph 未来版本默认**阻止**未注册类型反序列化，届时全部旧 checkpoint 读取将失败 | sp6 不动存储形态（零 state 变更红利优先）；风险显式留档 + 列表页对单 thread 反序列化异常逐条捕获跳过（坏 thread 不炸整页） | Sprint 7 候选：execution_mode 改存 str 值 + 自定义 serde 注册（留 §13/A-4） |
| R-S6-A4 | 模块级 `_THREAD_WORKERS` 登记表在测试间泄漏（跨用例 thread_id 残留） | 登记表提供 `_reset_for_tests()` 或 fixture 内 monkeypatch 清空；键含 uuid 冲突概率可忽略 | — |
| R-S6-A5 | plan_checks 关键词规则误报（自洽计划被警示）伤审核信任 | 关键词表宁窄勿宽 + 干净计划 fixture 同权重验收（AC-S6-11）+ 警示不阻断（人在回路兜底） | 单规则可疑时最小 diff 收窄该规则（规则独立，无运行时开关——极简，沿 sp5 R-3 先例） |
| R-S6-A6 | no_metrics 入 AUTO_FIXABLE 改变路由行为，旧"NONE+失败"场景从 interrupt#2 直达变为先修 2 轮 | 早停 N=2 封顶增量成本；决策面板文案携带轮次上下文；B-2 真库副本回归靶锁行为 | 常量 N 调 1（一行 config） |
| R-S6-A7 | MF-1 强制覆盖 PIP_CACHE_DIR 压制用户自定义 PIP 配置 | 覆盖仅沙箱子进程环境（宿主 os.environ 不动）；注释明示；pip-cache 目录进 .gitignore | 改为 setdefault（尊重显式 extra_env）——仅当有真实内网源缓存诉求时 |

---

## 11. 测试策略要点

- **真库字节副本只读驱动（CP-0.2-5 范式，测试工程师在批次 2 前一次性固化，复制不移动·沿 sp5 §9.4）**：
  - `tests/fixtures/checkpoints_s6_cdcd432cda49.db`：卡点现场——S6-01 换代判定（挂起 interrupt id/payload 实物）、S6-03 记账/短路（credential_degradations 非空 + purpose_key 键形态不一致实物）、S6-06/07 挂回与 R5 状态推导靶；
  - `tests/fixtures/checkpoints_s6_19e21e015017.db`：no_metrics/两面计划现场——S6-04 判定翻转靶（errors=['[error_category=none] 执行成功'] ∧ success=False ∧ metrics={}）、S6-05 交叉检查命中靶（14 步计划原文）；
  - **20 thread 全量库字节副本**：任务列表页枚举/排序/状态推导矩阵 fixture（AC-S6-15），并顺带覆盖 R-S6-A3 的坏 thread 容错断言。
- **mock 时序驱动**（AC-S6-01~03）：controller mock 按"真→真(同 token)→假"与"真→真(新 token)"两序列驱动 AppTest 渲染断言；resume 调用恰一次以 mock 捕获计数。
- **确定性单测**：`_normalize_purpose_key` 双向样本（含 'env:OPENAI_API_KEY'↔'openai_api_key'）、`_apply_no_metrics` 四象限（exit_ok×metrics×groups）、`derive_task_status` R1~R7 全行、plan_checks 命中/干净双 fixture。
- **真跑项**（浏览器复走四卡点闭环 + AC-S5-13/21 挂账收口 + Prompt Cache 三维复采 + 降级同构场景真实 e2e 抽验）：全部合并 §12 批次 5 的**一次 Maria 授权窗口**（既有省配额范式：mock 守门先行、smoke fail-fast、HippoRAG 缓存靶）。

---

## 12. 六批次开发骨架（权威版；PRD §7 为输入，批次边界逐批确认制执行）

| 批次 | 内容 | 依赖 | 检查点（CP-6.B.n） | 特殊标注 |
|---|---|---|---|---|
| **0 止血独立项** | MF-1 pip 缓存（`_build_sandbox_env` + run_command_tool + config 常量）；MF-5 pwc 摘除**除 prompt 面外**的代码/测试清理预研；MF-6 冷启动提示；MF-2 作者字段 humanize | 无 | CP-6.0.1 pip 命令/env 断言（AC-S6-17）；CP-6.0.2 作者异形样本（AC-S6-18）；CP-6.0.3 spinner 落点（AC-S6-22 手动项挂批次 5） | 四项文件互不重叠可并行；MF-1 为 P0 优先合入 |
| **1 prompt 静态批次 + 计划守门** | P-S6-1 planning 尾部段落 + P-S6-2 pwc 工具 schema 摘除收口（§9.1）+ `core/plan_checks.py` 三规则 + plan_review 警示位 + 字节稳定断言更新 | 批次 0（MF-5 代码面） | CP-6.1.1 prompt 断言 + 禁动态变量审查（AC-S6-13）；CP-6.1.2 两面计划命中/干净零警示双 fixture（AC-S6-11/12）；CP-6.1.3 interrupt 种类集合守门 | **sp6 唯一 Prompt Cache 基线重建批次**（离线维随批；在线复采延批次 5 合并授权） |
| **2 贯穿批（G2）** | S6-03：interaction_tools 工厂化 + `_normalize_purpose_key` 短路 + execution 上下文注入 + directive 常量；S6-04：NO_METRICS 成员 + 步骤 4.75 + 早停 + term_map 条目 | 批次 1（现场 fixture 已固化） | CP-6.2.1 短路零 interrupt + 键归一双向（AC-S6-08）；CP-6.2.2 两节点上下文含降级指令·含修复轮（AC-S6-07）；CP-6.2.3 现场同构记账回归靶（AC-S6-06）；CP-6.2.4 no_metrics 判定/文案/早停（AC-S6-09/10） | coding.py / execution.py / interaction_tools.py 本批独占 |
| **3 过渡态批（G1）＝ execution_monitor 单收口窗口** | S6-01 awaiting 契约 + controller 增量（token/登记表/校验参/has_active_worker/get_phase）；S6-02 在途标签 + case④bis；MF-7 日志尾部区；MF-4 渲染点清扫；§4.2 R7 孤儿卡片 | 批次 2（NO_METRICS 文案入面板） | CP-6.3.1 两序列时序断言 + resume 恰一次 + 换代反例（AC-S6-01/02）；CP-6.3.2 case①~⑦ 分发审计 + 通则注记（AC-S6-03）；CP-6.3.3 在途标签/切页（AC-S6-04/05）；CP-6.3.4 日志尾部/占位（AC-S6-23）+ 裸标签扫描（AC-S6-20） | **`ui/pages/execution_monitor.py` 被 S6-01/02/MF-4/MF-7/R7 卡片共同触碰——全部收敛本批一次改写，主控收口令，页面级全量 ×3 连跑**（R-S6-1） |
| **4 恢复批（G3）** | S6-06 query params 重连 + `_restore_from_query_params`；S6-07 任务列表页 + `derive_task_status` + list_threads + resume_task | 批次 3（登记表/controller 原语） | CP-6.4.1 重连路由矩阵 + 无参数路径回归（AC-S6-14）；CP-6.4.2 20-thread 真库枚举/状态推导（AC-S6-15）；CP-6.4.3 挂回 resume 有效 + 显式续跑断言（AC-S6-16） | app.py 与批次 3 共改——两批**串行**不并行；task_list.py 新文件独占 |
| **5 收口** | 全量回归（sp5 基线 1754+ 修断言，§9.4 清单）+ 覆盖矩阵审计 + Prompt Cache 三维在线复采 + 浏览器复走（四卡点闭环 + AC-S5-13/21 挂账 + AC-S6-22 手动项）+ 降级同构真实 e2e 抽验 | 批次 1~4 | CP-6.5.1 全量绿零退化；CP-6.5.2 复走四卡点全闭环（PRD §9.1 核心度量）；CP-6.5.3 新基线 ×0.95 守门 | **全部真跑项合并一次 Maria 授权窗口**（R-S6-6） |

**容量裁剪线对照（PRD §1）**：批次 0 的 MF-2/6 与批次 3 的 MF-4 → S6-02 降规模（保切页）→ S6-05 降规模（保 W1/W2 自洽检查，W3 顺延）→ S6-04 降规模（保类别+文案，早停顺延）→ S6-07 最后动。**P0 载体（批次 0 的 MF-1、批次 2 的 S6-03、批次 3 的 S6-01、批次 4 的 S6-06）不可裁。**

---

## 13. 架构勘误与假设留档

| 编号 | 类型 | 内容 |
|---|---|---|
| E-1 | **PRD 勘误** | "降级未记账"（PRD S6-03 / 走查 §二-C）不成立：走查取证看的是 `degraded_nodes`（节点降级清单），凭证降级记账字段 `credential_degradations` 在现场 thread 中**非空且正确**（{'env:OPENAI_API_KEY': …}），`execution_result.degraded_credentials` 亦有值。S6-03 需求 1 已由 sp5 完成，sp6 工作量集中在需求 2/3（注入+短路）。PRD 该段以本条为准，AC-S6-06 语义不变（回归靶防再倒退） |
| E-2 | **PRD/TODO 勘误** | "ExecutionResult.logs 声明 List[str] 实存 str"（MF-3 / TODO 07-10 附带①）与当前代码不符：state.py:154 与 technical-architecture.md:388 声明均已是 `str`，写入点/4 消费方全 str。MF-3 收敛为守门用例 + 勘误（§5.2） |
| E-3 | **留档勘误** | "logs 仅 install 段 ~6KB"系 B-2 现场（14 步仅执行 1 步）个案；`_aggregate_logs` 本就聚合全步 stdout/stderr（B-1 实证 87KB）。真实衰减点是 messages 回读通道 ToolMessage 8KB 截断（§5.3，尾部消费语义下可接受，不在 sp6 修） |
| E-4 | **取证结论收紧** | "换代判定锚 interrupt.id"不充分：id=xxh3(ns) 不含 payload，同任务串行 interrupt 同 id（§1.1 源码级论证）。裁决为复合 token（id+payload 指纹+worker 存活） |
| E-5 | **键形态留档** | 记账键 `env:OPENAI_API_KEY`（sp5 计划声明约定）vs agent 自由生成 `openai_api_key`——短路匹配以 `_normalize_purpose_key` 归一（§2.2）为唯一口径；`.secrets`/mask/声明面不归一 |
| A-1 | 假设 | 早停 N=2（§3.4）：定向修复恰一次完整机会；可单点推翻（config 一行） |
| A-2 | 假设 | MF-1 采 `PIP_CACHE_DIR=WORKSPACE_DIR/pip-cache` 全局共享（非 PRD 列举的"随任务清理"变体）：/data 卷零 home 占用 + 跨任务省 torch 级重复下载；若 Maria 要求任务级隔离，改 `<work_dir>/pip-cache` 为一行变更 |
| A-3 | 假设 | worker 登记表用 app.py 模块级 dict（非 st.cache_resource 进程级 controller 单例）：改动面最小；若 Sprint 7 做多任务并发管理可升级为进程级 controller |
| A-4 | 假设 | ExecutionMode 反序列化警告本 Sprint 只登记不修（R-S6-A3）；"execution_mode 改存 str + serde 注册"列 Sprint 7 候选 |
| A-5 | 假设 | plan_review（interrupt#1）不强制迁移到 token 校验（§9.2）：该页 awaiting 范式已达标（走查 approve→gate 5s 正常），最小改动面优先 |

---

## 14. AC-S6-01~23 → 方案组件映射表

| AC | 组件 | AC | 组件 |
|---|---|---|---|
| S6-01 | §1.2 awaiting+token | S6-13 | 7.5 P-S6-1 尾部段落 |
| S6-02 | §1.2 三道防线+换代反例 | S6-14 | 7.6 query params+无参数红线 |
| S6-03 | §1.3 通则+case 审计 | S6-15 | §4.1 推导表+§4.3 枚举 |
| S6-04 | §6.2 get_phase 阶段指示 | S6-16 | §4.2 显式续跑+7.6 resume 等价 |
| S6-05 | §6.2 case④bis 双判据 | S6-17 | 7.8 MF-1 PIP_CACHE_DIR |
| S6-06 | §2.1 勘误+现场回归靶 | S6-18 | 7.8 MF-2 _humanize_authors |
| S6-07 | §2.4 双节点注入 | S6-19 | §5.2 三方一致守门 |
| S6-08 | §2.2/2.3 归一+工厂短路 | S6-20 | 7.8 MF-4 渲染点清扫 |
| S6-09 | §3.2 步骤 4.75 合流 | S6-21 | 7.8 MF-5 pwc 摘除 |
| S6-10 | §3.3 hint 通道+§3.4 早停 | S6-22 | 7.8 MF-6 spinner |
| S6-11 | 7.5 W1/W2+双 fixture | S6-23 | §5.3 MF-7 logs 尾部区 |
| S6-12 | 7.5 W3+种类守门 | — | — |

---

*（全文完：第 1 段 §1~§6 六项裁决；第 2 段 §7~§8 方案与变更总表；第 3 段 §9~§14 集成、风险、测试、批次、勘误、映射。docs/sprint6/architecture.md v1.0 交付完毕，待 Maria 审阅后转全栈开发代理进入批次 0——批次边界逐批确认制照旧。）*
