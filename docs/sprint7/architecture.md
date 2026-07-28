# Sprint 7 核心架构设计文档：修复循环失控治理族（S7-01~03）+ 修复循环记忆增强（S7-05）

**文档版本**：v1.2（v1.0 = Q-S7-1~6 六项裁决 + S7-01~03 三需求方案；v1.1 增补 §13 = S7-05 修复循环记忆增强档 B 方案；**v1.2 增补 §14 = S7-06 只读环境探测的安全底座，仅裁 Q-S7-7/Q-S7-8 两项**，Maria 2026-07-28 授权）
**日期**：2026-07-19（v1.0）／2026-07-20（v1.1 §13）
**作者**：架构师代理
**对应 PRD**：`docs/sprint7/prd.md` v0.3 §2.1~2.3（Maria 拍板 2026-07-18，Q-S7-1~6 全部在本文 §1~§6 裁决）+ v0.4 §2.5（S7-05，本文 §13）。+ v0.5 §2.6（S7-06「资源探索能实际探测本机环境」，本文 §14）。**S7-06 的 Q-S7-7（只读强制形态与防绕过）/ Q-S7-8（cwd 落点与工厂复用形态）两项已在本文 §14 裁决；Q-S7-9~12 四项仍待裁**（后续批次单独授权增补），**A-S7-9（不引入人在回路确认）待 Maria 复核**。
**体例参照**：`docs/sprint6/architecture.md` v1.0
**回归现场样本（只读勿清理）**：`checkpoints.db` thread `task-99eef17bccf2`（预算耗尽静默降级 + import 反复失败 + 烧 92 超 60 三缺陷同现场）

> **环境/契约基线（沿 sp6，直接采信）**：langgraph 1.1.10；主图 7 节点骨架不变；interrupt 三类封口（不新增种类）；`_dev_loop_route` self-loop 两段式为 interrupt#2 唯一抵达路径（`graph.py:129-145` 命门）；判定逻辑归确定性代码。
> **贯穿硬约束（PRD 红线，不可破）**：不新增 interrupt 种类 / 不加第四态 / 保 S-1 重跑幂等契约（`_has_committed_result_for_round` guard）/ 成本硬上限对齐翻倍新值 240/120 绝不突破 / S7-02 复用 `read_code_file` 不新造管道 / S7-03 不改 `_dev_loop_llm_calls` 累加口径 / 最小单一抽象。
> **本 Sprint 架构级特征（先说结论）**：**GlobalState / ExecutionResult / interrupt#2 payload 键结构零新增字段**——S7-01 复用 `_mark_degraded_for_report` 的双段式对位分支 + `_route_user_fix_decision` 三态；S7-02 复用 `code_output_dir` 落盘 + `read_code_file` + `last_error_summary` 既有子键；S7-03 在 `_effective_max_rounds` 入口收窄一个 clamp 项。三条全部落在 `execution.py` 收尾/反馈/子图装配层 + `config.py` 常量 + `coding.py` 反馈裁剪 + 面板文案，**不触碰 state 契约、不触碰 interrupt payload 键集合**。这是本治理族"最小设计"的架构底座。

---

## 0. 缺陷坐实与架构定性（据 PRD §0 + 源码级复核）

三缺陷在 `task-99eef17bccf2` 同现场，源码级复核结论：

- **S7-01**：入口预算门 `execution.py:2029-2030` 在 `budget < DEV_LOOP_MIN_CALLS_PER_ROUND` 时 `return _mark_degraded_for_report(...)`，`_dev_loop_route` 被清 None（`_mark_degraded_for_report:1886`）→ `graph.py:157` 兜底路由 reporting。**该门位于 `_maybe_interrupt_or_return` 函数体最前**（2029），先于两段式 await 分支（2055）与函数体 interrupt（2091），任何错误类别命中即静默降级。**关键时序发现（决定 Q-S7-2 改法）**：预算门命中时，`exec_result` 已由主流程 `_map_execution_result`（2214）落盘进 `updates`（首次进入路径）或已是 guard 命中复用结果（2131-2141）——即预算门执行时 sandbox 结果**已在 updates 里、sandbox 不会再跑**。这一点是 S7-01 复用两段式而不重跑 sandbox 的时序前提。
- **S7-02**：三处信息链路错位——`coding.py:264` `stderr_tail=logs[-2000:]`（尾部恰是后续成功步 stdout）、`execution.py:1121` `stderr_tail:_tail(logs)`（execution 修复回合同款）、`representative_stderr=""` 在 NO_METRICS 构造点（1659）与 guard 重建点（2253）恒为空。coder 从 `_digest_execution_feedback`（coding.py:233-265）拿到的 `last_error_summary` 全程不含真报错行。
- **S7-03**：`_run_execution_agent`（1348）算 `effective_max_rounds = _effective_max_rounds(plan)`，传入子图 `max_rounds`；子图 `budget_check_node`（react_base.py:621-629）只看**本轮子图 `round`**，对跨回合累计的 `_dev_loop_llm_calls` 无感知。`_dev_loop_llm_calls` 在回合结束的 `_map_execution_result`（1828）才累加——纯"轮边界"计量。子上限判定 `dev_calls < MAX_DEV_LOOP_LLM_CALLS`（2036）同样在轮边界。故单轮子图一口气烧 CAP（=30）轮不受跨回合累计约束 → 冲过头。

定性：S7-01 = 处置分支写反（PRD 级）；S7-02 = 信息链路 bug（构造/取值错）；S7-03 = 机制层缺陷（刹车装错粒度）。三者均可确定性修，不依赖评测数据。

---

## 1. Q-S7-1：预算耗尽复用三态确认（无需第四态）

### 1.1 结论：现有三态覆盖预算耗尽的全部用户意图，明确不加第四态

interrupt#2 三态（`_route_user_fix_decision:1935`）在预算耗尽语境下的语义映射：

| 用户意图（预算耗尽后） | 映射三态 | 现有行为 | 预算耗尽语境下是否成立 |
|---|---|---|---|
| 接受当前结果、出降级报告 | `export_code` | `_mark_degraded_for_report` → reporting（:1969） | **成立**，与旧静默降级同终点，差别是"用户知情选择"而非"系统替选" |
| 换个计划重来 | `revise_plan` | 清 approved + `fix_loop_count=0` + 写 `_planning_user_feedback` → planning（:1952） | **成立但需处置预算矛盾**，见 1.2 |
| 放弃任务 | `terminate` | `current_step=cancelled_by_user` → END（:1946） | **成立**，checkpoint 保留 |

三态无缝覆盖"接受/重来/放弃"三类意图。**第四态"追加预算"被明确否决**，理由三重：（a）预算已翻倍（§3），余量翻倍后"过早掐断"的边际已大幅缓解；（b）"追加预算"本质是把 `MAX_TOTAL_LLM_CALLS` 硬顶变成软性可协商，破坏"成本硬上限绝不可破"红线（AC-S7-04）；（c）`revise_plan` 已隐含"给一次新机会"，功能上覆盖了"追加预算继续修"的合理诉求（换计划天然重置修复回合，见 1.2），第四态与之重叠 → 过度工程。

### 1.2 唯一语义缝隙：revise_plan 在"预算已空"下要再跑修复的矛盾——处置裁决

`revise_plan` 回 planning 后，用户批准新计划将重新进入 coding→execution 修复循环。但此时 `retry_budget_remaining` 可能已耗尽（=0），新一轮 execution 入口又会立刻命中预算门。若不处置，`revise_plan` 在预算耗尽语境下会**空转**（改了计划却无预算执行，再次瞬间降级）。

**裁决（最小、确定性、复用既有语义）**：`revise_plan` 是"换一条路重来"的用户显式动作，其语义本就等价于"给任务一次新的完整机会"。既有 `_route_user_fix_decision` 的 revise_plan 分支已做 `fix_loop_count=0`（:1963，回合数清零）。**在同一分支追加一步：`retry_budget_remaining` 重置为 `MAX_TOTAL_LLM_CALLS`（翻倍后 240）的一个确定性份额**。取值方案二选一，推荐 A：

- **方案 A（推荐）：revise_plan 时 `retry_budget_remaining = MAX_TOTAL_LLM_CALLS`（全额重置）**。语义直白："换计划 = 重新开始"，与 `state.py:340` 初始化 `retry_budget_remaining=MAX_TOTAL_LLM_CALLS` 同口径。硬顶不破：`_dev_loop_llm_calls` 累计**不重置**（它是修复循环子预算的全局计数，`MAX_DEV_LOOP_LLM_CALLS=120` 硬顶继续生效于 2036/2077 判定），故 revise_plan 后即便预算重满，子上限仍拦得住（叠加 S7-03 刹车），不会突破 240/120。
- **方案 B（更保守，不推荐首选）**：重置为 `DEV_LOOP_MIN_CALLS_PER_ROUND * K`（给固定小额度）。缺点：引入新魔数、语义模糊、用户"换了计划却只给几步"体验割裂。

**采方案 A**。落点：`_route_user_fix_decision` 的 `revise_plan` 分支（execution.py:1952-1965）追加 `out["retry_budget_remaining"] = MAX_TOTAL_LLM_CALLS`。这是本 Q 唯一的行为增量，与"不加第四态"正交（仍是三态，只是 revise_plan 分支补齐预算语义自洽）。

> **对 A-S7-1 的确认**：假设成立，无需第四态。方案 A 是对 revise_plan 语义缝隙的最小补齐，非新增能力。

---

## 2. Q-S7-2（最关键）：预算耗尽经两段式抵达 interrupt#2

### 2.1 命门复述：为何不能在预算门处直接 interrupt()

interrupt#2 的抵达路径是 sp6 为满足 S-1 重跑幂等契约引入的两段式（`graph.py:129-145` L-C3-01 命门）：
1. commit 边界 return（置 `_dev_loop_route="await_dev_loop_interrupt"`，**尚未** interrupt）；
2. `graph.py:144-145` self-loop 重入 execution；
3. 重入后 `_has_committed_result_for_round` guard 命中（2126）跳过 sandbox；
4. `_maybe_interrupt_or_return` 收到 `already_committed=True`（2140），函数体内 `interrupt()`（2091）。

若在预算门（2029）直接 `interrupt()`：首次进入时 `already_committed=False`（sandbox 刚跑），此路径**从未过 checkpoint 边界落盘 execution_result**，直接 interrupt 会在 resume 重跑时重放整个 execution 函数体（含 sandbox）——破坏 S-1 幂等契约。故必须复用两段式。

### 2.2 关键时序坐实：预算门命中时 exec_result 已落盘、sandbox 不重跑

`_maybe_interrupt_or_return` 的入参 `exec_result` 与 `updates` 在两条进入路径均已含本回合 sandbox 结果：
- **首次进入路径**（execution 主流程）：`_map_execution_result`（2214）已把 exec_result 写进 `updates`，2221 才调 `_maybe_interrupt_or_return(..., already_committed=False)`。即预算门（2029）执行时，`updates["execution_result"]` **已就绪**，只是尚未过 checkpoint 边界（return 后由 LangGraph 提交）。
- **guard 命中路径**（self-loop 重入）：2131-2140 复用已落盘 `prev`，`already_committed=True`。

**结论**：预算门命中时 exec_result 已在手，sandbox 不需要、也不会重跑。改法只需让预算门"走两段式 return await 标记"而非"return 降级"，重入后 guard 天然命中（因为 `_dev_loop_route==await` 且 execution_result 非空，正是 `_has_committed_result_for_round` 的判定条件 2110-2113），跳过 sandbox，函数体 interrupt。**零 sandbox 重跑、S-1 契约不破**。

### 2.3 裁决：预算门命中时"改置 await 标记而非 return 降级"，与既有 await 分支合流

**不另开分支**。将预算门（2029-2030）从"return 降级"改为"与 2055 的 `not already_committed` await 分支同款处置"。两种等价实现，推荐实现 1：

**实现 1（推荐，最小 diff）：删除预算门的独立降级 return，让预算耗尽落入既有"修复耗尽/触顶"聚合分支**。

当前逻辑：预算门（2029）拦截 → 直接降级；未拦截才进 auto_fixable 修复分支（2033），修复分支不满足则落 interrupt 两段式（2055/2091）。

改法：预算门不再降级 return，而是**作为 auto_fixable 修复分支的一个否决条件**——预算不足一回合时不回 coding 修复（否则新回合又会立刻耗尽），直接落到"需 interrupt#2"的两段式路径。具体：

```
# 2029-2030 原：
#   if budget < DEV_LOOP_MIN_CALLS_PER_ROUND:
#       return _mark_degraded_for_report(updates, state, reason="budget_exhausted")
# 改为：删除此 return；把 budget 充足作为 auto_fixable 分支的准入条件之一。

# 2033-2038 修复分支准入增加一项 budget 门：
if (
    feedback.auto_fixable
    and fix_count < MAX_FIX_LOOP_COUNT
    and dev_calls < MAX_DEV_LOOP_LLM_CALLS
    and budget >= DEV_LOOP_MIN_CALLS_PER_ROUND      # ← 预算门下沉为修复准入条件
    and not _no_metrics_stalled(state, feedback)
):
    ... 回 coding 修复

# 修复准入不满足（含预算耗尽）→ 落既有两段式（2055 await / 2091 interrupt），
# 天然复用 commit-边界-return + self-loop-重入，零新路径。
```

这样预算耗尽与"修复耗尽/子上限触顶/不可修复"共用同一条 interrupt 两段式，`already_committed` guard 逻辑（2055/2126）一字不改。**这是最小设计：预算门从"提前降级的旁路"下沉为"修复分支的一个准入否决条件"，删掉一个 return、加一个 and 子句、加一个 reason 分支。**

**实现 2（次选，语义更显式但 diff 略大）**：保留预算门位置，但把 `return _mark_degraded_for_report(...)` 改为"若 `not already_committed` 则置 await 标记 return；否则设 `reason="budget_exhausted"` 落函数体 interrupt"。缺点：预算耗尽单独写一段两段式，与 2055/2091 既有两段式重复，违反"单一抽象"。**不推荐**。

**采实现 1**。

### 2.4 reason 分支与面板文案接线（衔接 Q-S7-4）

实现 1 下，预算耗尽会走到函数体 interrupt 前的 reason 判定链（2069-2082）。在该链追加一个预算耗尽分支（优先级：早停 > 预算耗尽 > 子上限 > 不可修复 > 修复耗尽，因为预算耗尽是更强的终态）：

```
if _no_metrics_stalled(...):          # 既有
    ...
elif budget < DEV_LOOP_MIN_CALLS_PER_ROUND:   # ← 新增：预算耗尽终态
    reason = _BUDGET_EXHAUSTED_SUMMARY
    panel_feedback = replace(feedback, summary=_BUDGET_EXHAUSTED_SUMMARY,
                             fix_hint=_BUDGET_EXHAUSTED_SUMMARY)
elif dev_calls >= MAX_DEV_LOOP_LLM_CALLS:     # 既有
    ...
```

文案常量落点见 §4。`panel_feedback` 复用 sp6 AC-S6-10 的 `replace(feedback, summary/fix_hint=...)` 范式（2069-2076 早停已用同款），零新 payload 键。

### 2.5 备选方案对比

| 方案 | 说明 | 评估 |
|---|---|---|
| A：预算门处直接 `interrupt()` | 把 interrupt 挪到 2029 | 首次进入 `already_committed=False`，直接 interrupt 破坏 S-1 幂等（resume 重跑 sandbox），**排除**（PRD §0.4 已警示） |
| B：预算门单开一段两段式（实现 2） | 保留门位置，命中时自行置 await/interrupt | 与 2055/2091 既有两段式重复，双份 await 逻辑，违反单一抽象，次选 |
| C：预算门下沉为修复准入否决条件（实现 1，**推荐**） | 删降级 return，budget 充足作 auto_fixable 准入项，耗尽自动落既有两段式 | 零新路径、零新 guard、复用既有 commit-边界-return，diff 最小；`already_committed`/self-loop 一字不动 |

### 2.6 落点文件

`core/nodes/execution.py`：`_maybe_interrupt_or_return`——删预算门降级 return（2029-2030）、修复分支准入增 budget 子句（2033-2038）、reason 链增预算耗尽分支（2069-2082）、`_route_user_fix_decision` revise_plan 分支增预算重置（1952-1965，Q-S7-1）、新增文案常量（§4）。**零 state 字段、零 interrupt payload 键、零 graph 路由改动**（`_route_after_execution` 完全不动——预算耗尽复用 `await_dev_loop_interrupt` 与 `user_fix_decision` 三态既有出边）。

---

## 3. Q-S7-3：预算翻倍 13 常量派生依赖核查

### 3.1 逐常量派生依赖清单（源码级核查）

| 常量 | 现→新 | 下游隐式依赖 | 判定 |
|---|---|---|---|
| `MAX_TOTAL_LLM_CALLS` | 120→240 | `state.py:340` `retry_budget_remaining=MAX_TOTAL_LLM_CALLS` 初值直接绑；`planning.py:881` payload `max_total_llm_calls` 展示；`graph.py:73` 兜底注释 | **安全**——全部读常量，翻倍自动传导；Q-S7-1 revise_plan 重置亦读此常量自动对齐。注：`planning.py:881` 注释"=120"、`graph.py:73`/`planning.py:11` 注释"=120"需同步改注释（非逻辑） |
| `MAX_DEV_LOOP_LLM_CALLS` | 60→120 | `_effective_max_rounds` CAP 联动（见下）；子上限判定 2036/2077；S7-03 刹车（§6）读此常量 | **需连带核对联动**（§3.2），逻辑自动 |
| `MAX_NODE_LLM_CALLS` | 10→20 | 单节点上限，独立常量，无派生公式 | **安全** |
| `MAX_FIX_LOOP_COUNT` | 10→20 | 2035 修复回合上限；面板 `fix_count / MAX_FIX_LOOP_COUNT` 展示（execution_monitor.py:639） | **安全**——展示分母翻倍自动更新，无隐式比例 |
| `DEV_LOOP_MIN_CALLS_PER_ROUND` | 2→4 | 预算门阈值（S7-01 判定基准）；S7-03 刹车下界参考（§6） | **安全**——S7-01/03 均读常量 |
| `REACT_MAX_ROUNDS_EXECUTION_CAP` | 30→60 | `_effective_max_rounds` clamp 上界（1086） | **联动公式命门**（§3.2） |
| `REACT_MAX_ROUNDS_PAPER_INTAKE` | 5→10 | 各节点子图 max_rounds，独立 | **安全** |
| `REACT_MAX_ROUNDS_PAPER_ANALYSIS` | 12→24 | 同上 | **安全** |
| `REACT_MAX_ROUNDS_RESOURCE_SCOUT` | 10→20 | 同上 | **安全** |
| `REACT_MAX_ROUNDS_PLANNING` | 8→16 | 同上 | **安全** |
| `REACT_MAX_ROUNDS_CODING` | 12→24 | 同上（coding 子图轮次） | **安全** |
| `REACT_MAX_ROUNDS_EXECUTION`（FLOOR） | 10→20 | `_effective_max_rounds` clamp 下界（1085） | **联动公式命门**（§3.2） |
| `REACT_EXECUTION_ROUNDS_MARGIN`（K） | 5→10 | `_effective_max_rounds` = clamp(n_steps + K, FLOOR, CAP)（1086） | **联动公式命门**（§3.2） |

### 3.2 联动公式翻倍后成立性验证

联动公式 `_effective_max_rounds = clamp(n_steps + K, FLOOR, CAP)`（1084-1086）：

- **强约束 `MAX_DEV_LOOP_LLM_CALLS < MAX_TOTAL_LLM_CALLS`**：翻倍后 120 < 240，**成立**。
- **联动等式 `CAP == MAX_DEV_LOOP_LLM_CALLS / 2`**：翻倍后 60 == 120/2，**成立**（AC-S7-06 守门）。
- **`FLOOR <= CAP`**（clamp 合法性）：翻倍后 20 <= 60，**成立**。
- **CAP 语义（初跑耗尽 CAP 后修复循环仍容一个完整回合）**：CAP=60 = 子预算 120 的一半，翻倍后此保守裕量语义**等比保持**，不变质。
- **K 裕量**：K=10（prepare 1 + 收尾 1 + 兜底 8），翻倍后与步数量级仍匹配（步数未翻倍，K 翻倍略宽松，无害——只是给 agent 稍多兜底轮次，仍受 CAP 封顶）。

**无隐式比例依赖被打破**：唯一有比例关系的是 `CAP = DEV_LOOP/2` 与 `DEV_LOOP < TOTAL`，两者翻倍后等式/不等式均保持。**无超时估算 / 进度条分母对旧值的硬编码依赖**——面板分母（`fix_count/MAX_FIX_LOOP_COUNT`）与预算展示（planning payload）均读常量动态渲染。

### 3.3 需连带改的非逻辑项（注释同步）

三处注释含旧值字面，翻倍时同步（非逻辑、防误导后人）：`planning.py:881`（"=120"）、`planning.py:11` 与 `graph.py:73`（"MAX_TOTAL_LLM_CALLS=120"）、`config.py:112-114` 注释内的 "60<120"、`config.py:143` 注释 "= MAX_DEV_LOOP_LLM_CALLS/2 = 60/2"。

### 3.4 硬编码断言测试同步面（AC-S7-06）

翻倍打破的硬编码断言分布（Grep 坐实，重点文件）：`tests/test_sprint3_a1.py`（35 处含 config 断言）、`test_sprint3_a_boundary.py`、`test_sprint5_t11_config.py`（18 处）、`test_sprint5_t25_budget_link.py`（联动公式断言）、`test_sprint4_e3.py`、`test_sprint3_e2*.py`（预算扣减断言）、`test_sprint2_a4.py`。**纪律（沿 sp5/sp6"断言只换不弱化"）**：改的是被断言的常量值（120→240 等）与联动等式的两边数字，**不弱化断言强度**（等式/类型/强约束断言保留，只更新数字）。翻倍批必须同步这些断言 + 全量回归零失败。

### 3.5 落点文件

`config.py`（13 常量值 + 4 处注释同步）、`planning.py`/`graph.py`（3 处注释同步）、`tests/`（十几处硬编码断言同步，§3.4 清单）。**零逻辑改动**——翻倍是纯参数批。

---

## 4. Q-S7-4：预算耗尽面板文案落点

### 4.1 结论：sp6 AC-S6-10 的 `replace(feedback, summary/fix_hint=...)` 范式直接适用

sp6 早停已在 `_maybe_interrupt_or_return`（2069-2076）用 `panel_feedback = replace(feedback, summary=_NO_METRICS_EARLY_STOP_SUMMARY, fix_hint=...)` 覆盖面板文案，走既有 `summary`/`fix_hint` 通道，零新 payload 键（面板 `error_summary`/`fix_hint` 从 `_build_dev_loop_interrupt_payload` 的 `feedback.summary`/`feedback.fix_hint` 取，1906-1907）。**预算耗尽文案完全复用同款**（§2.4 已给接线位）。

### 4.2 "预算耗尽"确定性判定依据

判定依据 = 与预算门同一表达式：`budget < DEV_LOOP_MIN_CALLS_PER_ROUND`（其中 `budget = state.get("retry_budget_remaining", 0) or 0`，2025）。此判定纯确定性、无 LLM。放在 reason 链（§2.4）中，优先级低于早停（早停是更具体的"无进展"语境，若同时命中优先报早停）、高于子上限/不可修复/修复耗尽（预算耗尽是更强的资源终态）。

### 4.3 文案常量

新增模块级常量（execution.py，与 `_NO_METRICS_EARLY_STOP_SUMMARY:1981` 同款）：

```
_BUDGET_EXHAUSTED_SUMMARY = (
    "修复循环已反复失败，重试预算已耗尽（LLM 调用额度用尽）。"
    "系统不再自动继续，请在下方三种处置中选择：接受当前结果导出报告 / "
    "重订计划再试 / 终止任务。"
)
```

面板（execution_monitor.py:628-668）**无需改渲染逻辑**——`error_summary`/`fix_hint` 已渲染，文案经 `replace` 注入即显示。区别于通用失败文案（面板顶部 `st.error` 的"execution 修复循环已耗尽自动重试"是静态兜底，与本文案正交、不冲突）。

### 4.4 对照用例（AC-S7-03 守门）

- 命中用例：`retry_budget_remaining=0` + success=False → 面板 `error_summary` 含"预算已耗尽"语义关键词。
- 对照用例：非预算耗尽情形（预算充足、子上限触顶）→ 面板不含预算耗尽文案（防文案泛化）。
- payload 键集合守门：断言 `_build_dev_loop_interrupt_payload` 键集合与 sp6 逐字一致（沿 AC-S6-10 范式）。
- 三态守门：`payload["options"] == ["terminate", "revise_plan", "export_code"]`（无第四态）。

### 4.5 落点文件

`core/nodes/execution.py`（`_BUDGET_EXHAUSTED_SUMMARY` 常量 + reason 链分支，§2.4 已含）。**面板文件零改动**（execution_monitor.py 只读消费既有键）——这是 S7-01 不进 execution_monitor 单收口窗口的原因（见 §8）。

---

## 5. Q-S7-5（最硬 II）：S7-02 日志落盘 + read_code_file 自读

### 5.1 前置坐实：read_code_file 天然能读 code_output_dir 下日志文件

`read_code_file`（code_fs_tools.py:159-195）路径约束是 `_is_within_workspace`（WORKSPACE_DIR 根，:182），而 `code_output_dir` 本身在 WORKSPACE_DIR 之下（`state.code_output_dir` 由 coding 锚定在 workspace 内）。**故 read_code_file 读 code_output_dir 下的日志文件无需任何工具微调、无需路径白名单调整、不新造管道**（A-S7-6 假设成立，Q-S7-5 子问"是否需微调工具"答：否）。

### 5.2 一处坑必须处置：read_code_file 的 8000 字符截断

`read_code_file` 用 `_truncate`（code_fs_tools.py:57-64）截到 `TOOL_RESULT_MAX_LENGTH=8000`。完整日志（B-1 现场 87KB）单次读会被截到 8000 字符——**若截断发生在头部，可能又丢掉真报错行**（真报错行可能在中段）。这与"给 coder 看真相"的理念冲突。

**裁决（最小、不新造管道、不新增工具）**：

- **主路径**：日志落盘为**多文件按轮次分离**（见 5.3），单轮日志文件通常 < 8000 字符可整读；即便超长，coder 已有 `list_dir` 工具可先列日志目录、`read_code_file` 逐个读。
- **对超长日志的兜底**：日志文件内容**按"错误优先"编排**——落盘时把 stderr 段/非零 exit 步骤前置到文件头部（见 5.3 编排规则）。这样即便 8000 截断，真报错行（stderr / `No module named` 类）落在文件头 8000 内，coder 整读一次即命中。**不改 `TOOL_RESULT_MAX_LENGTH`**（它是全局 ReAct 工具结果护栏，改它影响面过大，违反最小设计）。

> **理念红线守住**：截断决策权仍在 agent——coder 决定读哪个轮次文件、读不够再 list_dir 探索。系统只保证"真报错在文件头部可达"，不替 coder 挑 2000 字塞进反馈。

### 5.3 日志落盘：确切位置、命名、轮次编号、内容编排

- **位置**：`<code_output_dir>/exec_logs/`（code_output_dir 下固定子目录，进 workspace 天然可读）。
- **命名与轮次编号**：`round_{fix_loop_count}.log`。`fix_loop_count` 是修复回合确定性编号（首跑=0，第 N 次修复回合=N），与 `fix_loop_history` 对齐、与面板轮次口径一致。**不用时间戳/uuid**（保持确定性、可复现、Prompt Cache 无扰、coder 可从 `fix_round` 反推文件名）。
- **内容 = 完整日志**：即 `_aggregate_logs(prep, run_results)`（execution.py:1669-1691）的**未截断原文**（install_log + 各步 stdout/stderr）。注意：`execution_result.logs`（state 里）受 checkpoint 体量约束、且经 mask + 消费侧只读尾部；**落盘文件是独立的完整原文**（不进 checkpoint，落磁盘，不撑爆 state）。
- **内容编排（应对 5.2 截断）**：文件头部先写一段"错误摘要区"——非零 exit 步骤的 `[step#i exit=N cmd=...]` + 其 stderr 段前置；随后是完整时序日志。真报错行进头 8000 字符。
- **落盘时机**：在 `_build_execution_result` 之后、`_map_execution_result` 之前（execution 主流程步骤 5 与 6 之间，2210-2214 区间），确保只在真跑回合落盘（guard 命中路径不重落——guard 路径本就不重跑 sandbox，日志已在上一次真跑回合落盘）。
- **落盘用 mask 后原文还是 mask 前**：用 **mask 后**（与 `execution_result.logs` 同 mask 口径，:1744），保安全纪律一致——coder 读到的日志与 state.logs 同脱敏级别，不泄凭证。
- **落盘异常处置**：写文件失败（IO/越界）**不阻断节点**——try/except 兜底，失败时日志路径字段置 None，反馈退回既有 `errors` 摘要（沿 coding gate 工具兜底范式）。

### 5.4 反馈 payload 字段落点：`last_error_summary` 内新增路径子键，不换 stderr_tail 键名

coder 反馈链路是 `coding.py:328` `payload["last_error_summary"] = _digest_execution_feedback(exec_result)`，`_digest_execution_feedback`（coding.py:233-265）返回 `{errors, error_category, stderr_tail}`。

**裁决**：在 `_digest_execution_feedback` 返回 dict **新增 `log_file_path` 子键**，**保留 `error_category`**（快速提示，PRD 要求），**stderr_tail 改为退化提示而非删键**：

```
# _digest_execution_feedback 返回：
{
    "errors": [...],                    # 保留（摘要级）
    "error_category": error_category,   # 保留（快速提示，PRD §2.3.2）
    "log_file_path": <round_{n}.log 绝对路径 or None>,   # ← 新增：完整日志入口
    "stderr_tail": <退化：一句"完整日志见 log_file_path，请用 read_code_file 自读">,  # ← 语义改为指引
}
```

**为何不直接删 `stderr_tail` 键**：删键会打破既有 coding prompt/context 对该键的引用（若有）与既有测试面；改为"指引串"既满足 AC-S7-07（不再是系统截断产物）、又保键结构稳定（Prompt Cache 无扰）。**AC-S7-07 守门点即在此**：断言 `stderr_tail` 不再是 `logs[-2000:]` 截断产物（现 coding.py:247），而是固定指引串（不含日志内容）；断言 `log_file_path` 存在且指向含真报错的文件。

- `exec_result` 需能拿到日志路径：日志文件路径由落盘时（§5.3）写入 **`execution_result.logs` 无关的独立途径**。最小方案：落盘路径可由 `fix_loop_count` + code_output_dir 确定性重建（`<code_output_dir>/exec_logs/round_{n}.log`），`_digest_execution_feedback` 无需从 exec_result 读路径字段，**直接由 coding.py 侧已有的 `code_output_dir`（`_resolve_code_output_dir(state)`，coding.py:305）+ `fix_round` 拼出**——零 state 字段、零 ExecutionResult 字段新增。这是最小设计：路径是确定性可推导的，不必存。
  - **代价与取舍**：若某回合落盘失败（§5.3 兜底置空），确定性拼出的路径会指向不存在文件——coder 用 read_code_file 读到"文件不存在"错误串，退回 `errors` 摘要，不炸。可接受（罕见 IO 失败降级到 sp6 现状）。
- **execution 侧修复反馈同步**：`_build_execution_agent_context` 的 `last_error_summary`（execution.py:1119-1122）同款——`stderr_tail:_tail(logs)` 改为路径指引 + `log_file_path`（execution agent 也有 read 能力？execution agent 工具是 prepare/run_in_sandbox/request_user_input，**无 read_code_file**）。**裁决**：execution 修复反馈保持传 stderr_tail 尾部（execution agent 无自读工具，改路径反而使它更瞎）；**S7-02 的"改传路径"只作用于 coding 反馈链路**（coder 才有 read_code_file）。PRD §0.7 坐实的信息链路 bug 现场正是 **coder** 看不到真错，S7-02 精准修 coder 侧即可。execution 侧 stderr_tail 维持（非目标，正交）。

### 5.5 `representative_stderr` 处置

`representative_stderr=""` 恒空（1659/2253）是 sp4 遗留字段，interrupt#2 面板（execution_monitor.py:665-668）读它。S7-02 **不填充它、不删它**（AC-S7-07 只要求反馈链路不回退到系统截断，不要求填 representative_stderr）。它是 payload 键结构一部分（1914），保结构冻结。**明确非目标**：不因 S7-02 触碰 representative_stderr——那是人向面板字段，与 coder agent 向链路正交（同 sp6 MF-7 与本条正交的原则）。

### 5.6 备选方案对比

| 方案 | 说明 | 评估 |
|---|---|---|
| A：反馈里塞更长 stderr（4000/8000） | 把截断长度调大 | 仍是"系统替 coder 挑一段"，违反理念红线（AC-S7-07），且撑大反馈/context，**排除** |
| B：新造日志读取 MCP/工具 | 专用日志观测工具 | 违反"不新造管道"红线（A-S7-6），过度工程，**排除** |
| C：落盘文件 + read_code_file 自读（**推荐**） | code_output_dir 下 round_{n}.log + coder 用现有工具 + 错误优先编排应对 8000 截断 | 复用现有工具、零新管道、零 state 字段（路径确定性推导）、理念红线守住 |

### 5.7 落点文件

`core/nodes/execution.py`（`_aggregate_logs` 复用 + 新增 `_persist_round_log(work_dir, fix_count, prep, run_results)` 落盘纯函数 + 主流程 2210-2214 区间接线 + 错误优先编排 helper）、`core/nodes/coding.py`（`_digest_execution_feedback` 返回增 `log_file_path` + `stderr_tail` 改指引串 + 由 `_resolve_code_output_dir`+`fix_round` 拼路径）。**零 state 字段、零 ExecutionResult 字段、零工具改动、零新增工具、零 interrupt payload 键**。

---

## 6. Q-S7-6（最硬 III）：S7-03 单轮内刹车粒度

### 6.1 命门：budget_check 只看本轮子图 round，不看跨回合累计 _dev_loop_llm_calls

ReAct 子图 `budget_check_node`（react_base.py:621-629）判据 `round >= max_rounds - 1`，`round` 是**本轮 execution 内嵌子图**的轮次（每回合从 0 起，`_run_execution_agent` 初始化 `"round": 0`，1358）。`max_rounds = _effective_max_rounds(plan)`（1348）= `clamp(n_steps+K, FLOOR, CAP)`，CAP=`DEV_LOOP/2`。

跨回合累计的 `_dev_loop_llm_calls` 在回合结束 `_map_execution_result`（1828）累加，子上限判定 `dev_calls < MAX_DEV_LOOP_LLM_CALLS`（2036）在轮边界。**子图内部对"已累计烧了多少 dev_calls"完全无感**——它只知道本轮能烧到 max_rounds。故单轮可烧满 CAP（=60 翻倍后），而 dev_calls 已接近 120 时，本轮又烧 60 → 冲到 ~180，超上限。

### 6.2 裁决：在 _effective_max_rounds 出口收窄本轮 max_rounds = min(联动值, 剩余子预算)

**核心洞察**：`budget_check_node` 已经是"逼近 max_rounds 即停"的现成刹车——问题不在刹车机制，在**喂给它的 max_rounds 没扣掉已累计的 dev_calls**。修法 = 在 `_run_execution_agent` 计算 max_rounds 时，把"本轮可用轮次"收窄为"联动值"与"剩余子预算"的较小值：

```
# _run_execution_agent 内，1348 现：
#   effective_max_rounds = _effective_max_rounds(plan)
# 改为：
base_rounds = _effective_max_rounds(plan)                      # 联动公式，不变
dev_calls_so_far = state.get("_dev_loop_llm_calls", 0) or 0
remaining_sub_budget = max(0, MAX_DEV_LOOP_LLM_CALLS - dev_calls_so_far)
# 本轮子图轮次上限 = min(联动值, 剩余子预算)，但保底 1 轮（防 0 轮死锁/退化）
effective_max_rounds = max(1, min(base_rounds, remaining_sub_budget))
```

这样 budget_check 天然在本轮内刹住——若 `dev_calls_so_far` 已逼近 120，剩余子预算小，本轮 max_rounds 被收窄到剩余额度，子图烧到剩余额度即 budget_check 触发 force_finish 收尾。**不改任何计量口径**（`_dev_loop_llm_calls` 累加 1828 一字不动），只改"本轮子图轮次上限的计算"（入口收窄，非新埋点）。

> 因为 `_run_execution_agent` 的 max_rounds 同时喂给 HumanMessage 的 `max_rounds` 数字（`_build_execution_agent_context` 经 `_effective_max_rounds(plan)`，1109）与子图（1353）。**注意**：`_build_execution_agent_context` 是独立算的（1109 直接调 `_effective_max_rounds(plan)`，不含收窄），若要 HumanMessage 里的数字与子图实际上限一致，需把收窄值也传给 context。**裁决**：HumanMessage 里的 max_rounds 提示保持联动值（`_effective_max_rounds(plan)`）——它是给 agent 的"计划轮次预期"，收窄是护栏而非计划；两者语义不同，不强制一致（收窄是 agent 无需感知的系统级刹车）。**这避免了动态通道字节因 dev_calls 变化而抖动**（R-PC4 无扰：context 里的 max_rounds 仍是 plan 确定性产出，不随 dev_calls 变）。

### 6.3 确定性越界上界

收窄后，单轮子图最多烧 `remaining_sub_budget` 轮，但 `budget_check` 在 `round >= max_rounds-1` 触发后还有 `force_finish` **+1 轮**（react_base.py:631，收尾再调一次 LLM）。故越界上界 = **1 轮**（force_finish 的收尾轮）。加上 metrics 档 3 LLM 抽取可能 +少量（`llm_calls_used`，`_parse_metrics`），确定性越界上界 ≤ **1 + metrics 抽取轮次**（metrics 抽取有自身上限，量级 ≤ 单轮最大轮次数远小于 CAP）。

**结论（AC-S7-08）**：`_dev_loop_llm_calls` 冲过 `MAX_DEV_LOOP_LLM_CALLS` 的幅度被约束在 **≤ force_finish 1 轮 + metrics 抽取额度**（确定性小值，远小于实测的 32）。符合 PRD"约束在确定性小范围、不追求零越界"（避免逐调用拦截过度工程）。

### 6.4 与翻倍解耦、与 S7-01 预算门协同

- **与翻倍解耦**：翻倍把 `MAX_DEV_LOOP_LLM_CALLS`→120，收窄公式读常量自动适配（剩余子预算 = 120 - dev_calls）。翻倍后仍须验 S7-03 刹车生效（AC-S7-08 独立于 AC-S7-06）。
- **与 S7-01 预算门协同**：收窄用 `_dev_loop_llm_calls`（子预算计数），预算门用 `retry_budget_remaining`（总预算计数），两者独立护栏、互不干扰。收窄使单轮不冲过子上限；即便冲到子上限，轮边界判定（2036/2077）仍拦并走 interrupt#2；预算门（§2）拦总预算耗尽。三重护栏各司其职。

### 6.5 备选方案对比

| 方案 | 说明 | 评估 |
|---|---|---|
| A：改 budget_check_node 读 dev_calls | 在 react_base 子图内注入 dev_calls 并比对 MAX_DEV_LOOP | 侵入 react_base（跨节点通用子图），违反"不改 react_base"约束；且 dev_calls 需穿透进 ReActState.context，改动面大，**排除** |
| B：逐 LLM 调用拦截器 | 每次 reasoning 前查累计 | 过度工程（PRD 非目标 1 明否），**排除** |
| C：入口收窄 max_rounds = min(联动, 剩余子预算)（**推荐**） | `_run_execution_agent` 一处 clamp，复用现成 budget_check 刹车 | 零 react_base 改动、零计量口径改动、越界上界确定性 ≤1 轮 + metrics、最小 diff |

### 6.6 落点文件

`core/nodes/execution.py`（`_run_execution_agent` 的 `effective_max_rounds` 计算处，1348——增 dev_calls 收窄 clamp）。**零 react_base 改动、零 config 常量新增、零计量口径改动、零 state 字段**。

---

## 7. 变更总表（config / 反馈 payload / 落盘 / 会话键）

**GlobalState / ExecutionResult / interrupt#2 payload 键结构：零新增、零变更字段**（本 Sprint 架构特征）。全部变更如下：

| 项 | 位置 | 类型/值 | 归属 | 说明 |
|---|---|---|---|---|
| 13 常量翻倍 | config.py | 见 §3 表 | S7-01 翻倍批 | 纯值改 + 注释同步；联动等式/强约束保持 |
| `_BUDGET_EXHAUSTED_SUMMARY` | execution.py（模块级常量） | str | S7-01 | 预算耗尽面板文案，复用 replace 范式 |
| 预算门下沉为修复准入条件 | execution.py `_maybe_interrupt_or_return` | 逻辑 | S7-01 | 删降级 return + and 子句 + reason 分支 |
| revise_plan 预算重置 | execution.py `_route_user_fix_decision` | 逻辑 | S7-01/Q-S7-1 | `retry_budget_remaining=MAX_TOTAL_LLM_CALLS` |
| `_persist_round_log` | execution.py（新纯函数） | 落盘 | S7-02 | `<code_output_dir>/exec_logs/round_{n}.log`，错误优先编排，try/except 兜底 |
| `log_file_path` 子键 + stderr_tail 指引化 | coding.py `_digest_execution_feedback` | dict 键 | S7-02 | last_error_summary 内子键，路径确定性推导 |
| max_rounds 入口收窄 | execution.py `_run_execution_agent` | 逻辑 | S7-03 | `min(联动值, 剩余子预算)`，不改计量口径 |

**新增模块/目录**：无新 .py 模块；新增运行期目录 `<code_output_dir>/exec_logs/`（任务隔离、随 workspace 清理、进 .gitignore）。**旧 checkpoint 兼容**：零 state 变更 ⇒ `task-99eef17bccf2` 现场直接被新代码消费（S7-01/02/03 回归靶可用真库副本驱动）。

---

## 8. 跨需求文件收口（execution.py 单收口窗口细化）

`core/nodes/execution.py` 被 S7-01/S7-02/S7-03 共同触碰，触碰区如下（PRD §7 已提单收口窗口，此处细化落点/顺序）：

| 需求 | execution.py 触碰函数 | 是否重叠 |
|---|---|---|
| S7-01 | `_maybe_interrupt_or_return`（2029/2033/2069）、`_route_user_fix_decision`（1952）、新常量 | 与 S7-02/03 **不重叠**（不同函数） |
| S7-02 | 新增 `_persist_round_log` + 主流程 2210-2214 接线 | 与 S7-01/03 **不重叠** |
| S7-03 | `_run_execution_agent`（1348 一处 clamp） | 与 S7-01/02 **不重叠** |

**关键收口结论**：三条在 execution.py 内**函数级不重叠**（S7-01 在收尾判定层、S7-02 在落盘 helper + 主流程步骤 5-6 间、S7-03 在子图装配层）。收口顺序建议：

1. **翻倍批先行（独立、解耦）**：config.py + 断言同步 + 全量回归。改 config.py 独占，不碰 execution.py。为后续所有批提供正确常量基线（S7-03 收窄读 `MAX_DEV_LOOP_LLM_CALLS`、S7-01 revise 重置读 `MAX_TOTAL_LLM_CALLS`）。
2. **execution.py 单收口窗口（S7-01+S7-02+S7-03 同批同窗）**：因三者同文件、需一次改写避免多代理并行冲突（沿 sp6 execution_monitor 单收口先例）。窗口内子任务顺序：S7-03（一处 clamp，最小、先落）→ S7-02（落盘 helper + coding.py 反馈，跨 execution/coding 两文件）→ S7-01（收尾判定重构，改动最深，最后落，避免与 S7-02 的主流程接线互扰）。
3. **coding.py**（S7-02 `_digest_execution_feedback`）：与 execution.py S7-02 同批（反馈两端一致改）。
4. **execution_monitor.py**：**本 Sprint 不进单收口窗口**——S7-01 面板文案走 `replace(feedback,...)` 数据通道，面板渲染逻辑零改（§4.5）。这是与 sp6 的关键差异（sp6 execution_monitor 被 5 处共触碰需单收口；sp7 面板零改）。

---

## 9. 关键测试点（呼应 AC-S7-01~08）

### 9.1 现场回归靶（强制，沿 sp5/sp6 教训）

`task-99eef17bccf2` 真库字节副本（`tests/fixtures/checkpoints_s7_99eef17bccf2.db`，复制不移动）为 S7-01/02/03 天然 fixture：`retry_budget_remaining=0` / `success=False` / `_dev_loop_llm_calls=92` / `fix_loop_history` 4 条全 import / logs 含 `No module named 'src'`。**S7-01/02/03 的现场回归靶不得只靠"预算充足/日志正常"常规 mock 自证**（sp5 AC-S5-03 mock 假绿教训）。

### 9.2 逐 AC 测试点

- **AC-S7-01/02（S7-01 路由 + 两段式幂等）**：mock state（budget=0/success=False）驱动 `_maybe_interrupt_or_return`——断言 **不再** 返回 `_mark_degraded_for_report`（degraded_nodes 不含 execution 的 budget_exhausted 降级）、而是置 `_dev_loop_route="await_dev_loop_interrupt"`（首次进入）；mock 时序断言两段式（首次 return await、self-loop 重入后 `already_committed=True` 函数体 interrupt 恰一次）；既有 S-1 / interrupt#2 幂等套件零退化（guard 逻辑不动）。
- **AC-S7-03（面板文案 + 三态守门）**：预算耗尽 → 面板 `error_summary` 含预算耗尽关键词；对照用例（子上限触顶）不含该文案；payload 键集合与 sp6 逐字一致；`options==["terminate","revise_plan","export_code"]`（无第四态）。
- **AC-S7-04（硬上限守门）**：构造 `_dev_loop_llm_calls=120` / `retry_budget_remaining` 达顶 state，断言不突破 240/120；revise_plan 重置后再验子上限（2036/2077）仍拦（预算重置不越子上限硬顶）。
- **AC-S7-05（coder 见真错）**：构造 import 失败现场 mock——断言 `_persist_round_log` 落盘 `round_{n}.log` 存在且含 `No module named 'src'`（在文件头 8000 字符内，验错误优先编排）；断言 `_digest_execution_feedback` 返回含 `log_file_path` 指向该文件；断言 read_code_file 能读到该路径内容。
- **AC-S7-06（翻倍 + 联动）**：13 常量值断言；`REACT_MAX_ROUNDS_EXECUTION_CAP == MAX_DEV_LOOP_LLM_CALLS/2`（60==120/2）；`MAX_DEV_LOOP_LLM_CALLS < MAX_TOTAL_LLM_CALLS`（120<240）；十几处旧断言同步改毕 + 全量回归零失败。
- **AC-S7-07（设计取舍守门，须验红）**：断言 `_digest_execution_feedback` 的 `stderr_tail` **不再是** `logs[-2000:]` 截断产物（现 coding.py:247），而是固定指引串；断言反馈以 `log_file_path` 为准。**验红**：注掉落盘 + 路径注入后断言必须变红（防"路径写了但反馈没真指过去"假绿，沿 sp6 AC-S6-10 教训）。
- **AC-S7-08（刹车，须验红）**：构造 `_dev_loop_llm_calls=118`（逼近 120）+ 联动值 60 的 mock——断言 `_run_execution_agent` 收窄后 `effective_max_rounds == min(60, 2) == 2`；断言总冲过头幅度 ≤ force_finish 1 轮 + metrics 额度（确定性小值，远小于 32）。**验红**：注掉收窄 clamp 后断言 max_rounds 回到 60、越界回到数十级。

### 9.3 测试盲区警示（沿 sp5/sp6）

- 预算耗尽 + import 反复失败是低频边界路径，常规 e2e 未必触达——必须专门构造 `retry_budget_remaining=0` / import 现场（sp5 AC-S5-03 mock e2e 假绿、真跑记账为空的教训）。
- AC-S7-05/07/08 **必须验红**——注掉对应改动断言变红，防假绿。

### 9.4 真跑项

现场同构真实 e2e 抽验（import 反复失败闭环：预算耗尽→interrupt#2 问用户；coder 自读日志定位 import；子上限单轮刹车）合并**一次 Maria 授权窗口**（既有省配额范式：mock 守门先行、smoke fail-fast、`task-99eef17bccf2` 为天然 fixture 勿清理）。

---

## 10. 风险登记与回归防线

| 编号 | 风险 | 缓解 | 回退 |
|---|---|---|---|
| R-S7-1 | 预算门下沉（§2.3 实现 1）误伤既有"预算充足失败"路径的路由 | 现场靶 + 对照用例（预算充足/耗尽两分支各验路由）；`_route_after_execution` 零改动（复用既有出边） | 回实现 2（预算耗尽单开两段式分支，diff 略大但隔离） |
| R-S7-2 | revise_plan 预算全额重置（Q-S7-1 方案 A）被误读为"绕过硬顶" | 明确 `_dev_loop_llm_calls` 不重置、子上限硬顶继续生效；AC-S7-04 守门 | 回方案 B（固定小额度重置，一行变更） |
| R-S7-3 | 日志落盘 8000 截断致真报错行不可达 | 错误优先编排（stderr/非零 exit 前置文件头）；coder 有 list_dir 逐读兜底 | 极端超长可加"错误摘要独立 err_{n}.log"小文件（仍复用 read_code_file，不新工具） |
| R-S7-4 | 日志路径确定性推导与落盘失败不一致（拼出路径指向不存在文件） | 落盘 try/except 兜底 + coder read 到"文件不存在"退回 errors 摘要（降级到 sp6 现状，不炸） | 若失败率高，改为在 exec_result 存路径字段（破零 state 红利，仅备用） |
| R-S7-5 | max_rounds 收窄到极小值（剩余子预算=1）致单轮几乎跑不动 | `max(1, ...)` 保底 1 轮；此时 dev_calls 已逼近上限本就该走 interrupt#2（收窄+轮边界双拦是预期行为，非 bug） | N/A（收窄触发即接近应升级人工，符合设计） |
| R-S7-6 | 翻倍打破断言范围超预估（隐藏硬编码） | 全量回归 + §3.4 清单逐文件核；断言只换不弱化纪律 | 单常量若论证不宜翻可单点回调（A-S7-7，破全翻一致性须 Maria 复核） |
| R-S7-7 | execution.py 单收口窗口内三子任务互扰 | §8 顺序（S7-03→S7-02→S7-01）+ 函数级不重叠已坐实 + 页面外零触碰 | 主控收口令，一次改写 |

---

## 11. 架构假设留档（每条可单点推翻）

| 编号 | 假设 | 依据与推翻成本 |
|---|---|---|
| AA-S7-1 | S7-01 复用三态、不加第四态；revise_plan 预算全额重置（方案 A） | Q-S7-1；推翻成本低（方案 B 一行） |
| AA-S7-2 | 预算门下沉为修复准入条件（实现 1），复用既有两段式 | Q-S7-2；推翻成本低（回实现 2，隔离但重复） |
| AA-S7-3 | S7-02 只改 coding 反馈链路（coder 有 read_code_file）；execution 修复反馈维持 stderr_tail | §5.4；execution agent 无自读工具，改路径反使其更瞎；推翻成本低（若给 execution agent 加 read 工具则另议，但违反最小设计） |
| AA-S7-4 | 日志路径确定性推导（code_output_dir + fix_round），不存 state/ExecutionResult 字段 | §5.4 最小设计；推翻成本中（落盘失败率高则改存字段，破零 state 红利） |
| AA-S7-5 | S7-03 入口收窄 max_rounds，不改 react_base、不改计量口径 | Q-S7-6；越界上界确定性 ≤1 轮+metrics；推翻成本低（仅当要求零越界才需侵入 react_base，PRD 已明否） |
| AA-S7-6 | HumanMessage 里 max_rounds 保持联动值不随 dev_calls 收窄（R-PC4 无扰） | §6.2；收窄是系统护栏、agent 无需感知；推翻成本低（若要一致则传收窄值，但引入动态通道抖动） |
| AA-S7-7 | execution_monitor.py 本 Sprint 零改（面板文案走数据通道） | §4.5/§8；推翻成本低（若需面板新区块则进单收口，但 S7-01 不需要） |

---

## 12. AC-S7-01~08 → 方案组件映射

| AC | 组件 | AC | 组件 |
|---|---|---|---|
| AC-S7-01 | §2.3 预算门下沉 + §2.2 时序 | AC-S7-05 | §5.3 落盘 + §5.4 log_file_path |
| AC-S7-02 | §2.2 两段式 + §2.4 reason 接线 | AC-S7-06 | §3 翻倍 + §3.2 联动验证 |
| AC-S7-03 | §4 文案 replace 范式 + 三态守门 | AC-S7-07 | §5.4 stderr_tail 指引化（验红） |
| AC-S7-04 | §1.2 revise 重置 + 硬顶守门 | AC-S7-08 | §6.2 入口收窄 + §6.3 越界上界（验红） |

---

## 13. S7-05 修复循环记忆增强（coder 跨回合记忆，档 B）

**对应 PRD**：`docs/sprint7/prd.md` v0.4 §2.5（Maria 亲提立项，要"两极之间"的档 B）
**日期**：2026-07-20（Maria 审阅后三点修订：去窗口全保留 / 每轮加 coder 自述定位+逻辑 / 不加 execution 判定理由）
**前置事实（改变方案形态）**：S7-02 已交付——每轮真错日志已以错误优先编排落盘 `<code_output_dir>/exec_logs/round_{n}.log`、确定性命名、`_resolve_round_log_path`（coding.py:243）已能推导任意轮路径。S7-05 复用此产物，**不新造记忆管道、不新增 LLM 调用**。

> **本节架构特征（先说结论）**：记忆增强 = 每轮结构化五元组（round / category / files_touched / **coder 自述定位+逻辑** / log_path）压成一两行，**全部修复记录保留不裁剪**（受 `MAX_FIX_LOOP_COUNT=20` 硬顶，token 上界可控，§13.4 估算），渲染成单个字符串键 `fix_history_digest` 塞进 curated context 尾部（sort_keys 只排顶层键、字符串值内部顺序自控，避坑）。跨 agent 只给 coder，不破子图隔离，不加 LLM 二次摘要。**唯一 state 契约增量 = `FixLoopRecord` 加两字段（fix_note + files_touched）+ GlobalState 加两个 coding→execution 传递字段。**

### 13.1 五问裁决表（Maria 修订后）

| PRD 问题 | 裁决（修订后） |
|---|---|
| 1. 每轮记什么 | **结构化五元组**：`round` / `category`(规则标签，仅粗过滤) / `files_touched`(coder 那轮改了哪些文件) / **`fix_note`（coder 自述"本轮问题定位 + 修复逻辑"一两句——修订2新增，比规则标签丰富的核心，非系统嚼碎/非规则模板/非额外 LLM）** / `log_path`(S7-02 已落盘真错日志指针)。 |
| 2. 怎么控量 | **全部记录保留、不设滚动窗口**（Maria 修订1：要完整轨迹）。控量靠"每轮压一两行 + `fix_note` 硬性字符上限"，token 总量受 `MAX_FIX_LOOP_COUNT=20` 硬顶封死、远不到档 C 爆量级（§13.4 估算）。**不引入 LLM 二次摘要**（§13.5）。 |
| 3. 放哪 | 整个 curated context 是**一个** `json.dumps(sort_keys=True)` 块（react_base.py:854）——无字典层"尾部"可言。裁决：历史渲染成**多行字符串**塞单键 `fix_history_digest`，sort_keys 只排顶层键、字符串值内部顺序自控（§13.3）。 |
| 4. 跨 agent | **只给 coder**。execution 关键信息已在 log_path 指向的日志里，coder 自读即得。 |
| 5. 子图隔离 | **不破**。历史全来自 GlobalState 已有信号(fix_loop_history + coder result 落库)+磁盘日志(S7-02)，只往 HumanMessage 注数据，`ReActState.messages` 一字不动，不去捞子图内推理对话（§13.6）。 |

**修订3（Q3 确认）**：**不纳入 execution 判定理由**（不把 `_classify_execution` 的 fix_strategy 规则模板文案进历史段）——那是档 A 味道，已否决。execution 侧的"真错"由 log_path 自读覆盖，"判定理由"无需单列。

### 13.2 每轮记什么：五元组（修订2 核心）

```
第 N 轮修复记录 → {
  round: N,                              # 轮号（确定性，对齐 fix_loop_count）
  category: "import",                    # fix_loop_history 现有 category（仅粗过滤/分组）
  files_touched: ["src/train.py"],       # coder 那轮改了哪些文件（比标签丰富）
  fix_note: "定位：train.py 缺 sys.path 致 src 不可导入；修复：入口加 sys.path.insert",  # ★修订2：coder 自述定位+逻辑（一两句，≤_FIX_NOTE_MAX_CHARS）
  log_path: ".../exec_logs/round_{N}.log"  # S7-02 已落盘真错日志文件指针
}
```

**`fix_note` 的信息学定位（为何不是档 A、不是额外 LLM）**：
- **不是档 A 规则模板**：`fix_note` 是 **coder 本轮推理的真实意图声明**（coder 本就在推理"我判断错在哪、我打算怎么改"，只是顺带用一句话结构化输出出来），不是 `_classify_execution` 关键词匹配套的预设文案。它承载"这一轮 coder 具体怎么想的"——正是 Maria 要的"上轮为啥没成"。
- **不是额外 LLM 调用**：`fix_note` 在 coder 现有的 `<result>` 输出里**顺带产出**，不新增任何 LLM 调用、不烧 `_dev_loop_llm_calls`（与 S7-03 刚修的子上限刹车零冲突）。这是修订2 的机制关键——把"生成记忆"的成本转嫁到 coder 本就要做的单次推理上。
- **与 log_path 互补**：`fix_note` 是 coder 侧"我改了什么、为什么这么改"（主观意图），log_path 是 execution 侧"结果真错是什么"（客观事实）。coder 下轮读历史时两相对照即知"我上轮以为缺 sys.path 就修了，但真错日志说还是 No module named——我的定位或修法有漏"。这个对照正是打破"反复套无效改法"的关键。

### 13.2.1 coder 输出约定改动（R-PC4 安全性确认）

在 `_CODING_SYSTEM_PROMPT_BODY`（coding.py:126，稳定前缀）的"修复回合模式"段（现 :164-167）与 `<result>` 输出字段定义（现 :170-176）各加一条固定文案：

```
修复回合模式段新增一句（对所有修复回合字节一致）：
- 在 <result> 中额外输出 fix_note 字段：用一两句话说明"本轮问题定位 + 修复逻辑"
  （定位到什么错、打算怎么改），供后续修复回合参考你之前的尝试。首轮生成可留空/省略。

<result> 字段定义新增一行：
  "fix_note": str | null    // 本轮问题定位+修复逻辑，一两句（≤120字）；首轮可 null
```

**R-PC4 安全确认（Maria 点名要确认这条加法安全）**：
- `_CODING_SYSTEM_PROMPT_BODY` 是 Prompt Cache 稳定前缀（跨论文/跨任务字节恒定，coding.py:184-185 纪律）。新增的这两句是**对所有回合一致的固定文案**（无 f-string 插值、无论文级动态变量、无轮号——它只是"请你顺带声明定位+逻辑"这个恒定指令），**字节级稳定不破前缀**。
- coder 每轮**输出**的 `fix_note` 值是动态的，但它进的是 coder 的 `<result>`（LLM 输出）→ 经 `_map_coding_result` 落 GlobalState → 下轮进 `fix_history_digest`（HumanMessage 动态尾部），**从不进 SystemMessage**。SystemMessage 稳定前缀只多了"请声明"这条恒定指令，不含任何 fix_note 值。**R-PC4 守住。**

### 13.3 字符串渲染示例（sort_keys 避坑，全保留）

```python
payload["fix_history_digest"] = _digest_fix_loop_history(state, code_output_dir)
# 值形如（预渲染多行字符串，全部轮次、轮号升序，不裁剪）：
#   "修复历史（共4轮，全部保留）：
#    round1 [import] 改 src/train.py | 定位:缺sys.path致src不可导入 修复:入口加sys.path.insert | 真错见 exec_logs/round_1.log
#    round2 [import] 改 src/train.py | 定位:sys.path路径写错 修复:改成绝对路径 | 真错见 exec_logs/round_2.log
#    round3 [import] 改 src/train.py,src/model.py | 定位:model.py也缺导入 修复:补两处import | 真错见 exec_logs/round_3.log
#    round4 [import] 改 src/train.py | 定位:包名拼写错 修复:util->utils | 真错见 exec_logs/round_4.log"
```

避坑机制（与 v1.0 R-PC4 纪律一致）：
- sort_keys 只排**顶层键名**；`fix_history_digest` 作为一个键，其**字符串值内部**（轮号升序、每轮一行）由 `_digest_fix_loop_history` 控制，sort_keys 管不到字符串内部。
- 稳定前缀 = SystemMessage（coding.py:850 单独一条）；`fix_history_digest` 进 HumanMessage 动态尾部，碰不到稳定前缀，R-PC4 天然守住。
- 字节幂等：同一 state 下渲染确定性（轮号升序、路径确定性推导、无时间戳/uuid），与 credential_degradations 注入同款（coding.py:346-354）。
- 只在修复回合注入（`exec_result and fix_count>0` 分支内，与 last_error_summary 同守护），首轮零扰动。

### 13.4 控量可行性论证（修订1：全保留，无窗口）

**Maria 修订1去掉 K=3 窗口、要完整轨迹。控量改由硬顶 + 字符上限双重封死，token 上界估算如下**：
- **轮数上界**：修复回合受 `MAX_FIX_LOOP_COUNT=20`（翻倍后，config.py）硬顶——历史最多 20 条记录，**不可能无限增长**。
- **每轮字符量**：round + category + files_touched 约 40~70 字符；`fix_note` 硬性上限 `_FIX_NOTE_MAX_CHARS = 120`（渲染时超长截断，防 coder 长篇撑爆）；log_path 尾部提示约 30 字符（只写相对 `exec_logs/round_N.log`，段首给一次根路径即可）。**每轮合计 ≤ ~220 字符**。
- **全保留总量上界**：20 轮 × 220 字符 ≈ **4400 字符**（约 1500~2200 token，中文/路径混合），加段首说明约 4500 字符。
- **与档 C 对比**：档 C（完整对话+工具调用全塞）单轮就可能上万 token、随回合线性膨胀到十万级。本方案全保留上界 ≈ 2200 token 封顶，**差两个数量级，远不到档 C 爆量级**。且实际修复很少跑满 20 轮（现场 4 轮），典型量 < 1000 token。

**结论**：全保留在 `MAX_FIX_LOOP_COUNT=20` 硬顶 + `_FIX_NOTE_MAX_CHARS=120` 双重封死下 token 上界确定、可控，满足档 B"控量不爆"。**删除 `_MEMORY_WINDOW_K`、删除"仅显示最近K轮"提示**——不再需要窗口概念。

### 13.5 是否引入 LLM 二次摘要（保持否决）

**不引入**（Maria 修订2 明确"不能额外调 LLM 二次摘要，烧 `_dev_loop_llm_calls` 与 S7-03 冲突"）。修订2 的 `fix_note` 恰是替代方案——它把"生成丰富记忆"的成本转嫁到 coder **本就要做的单次推理**上（顺带输出一句），零新增 LLM 调用、零 `_dev_loop_llm_calls` 消耗，却拿到比规则标签丰富的"coder 自述定位+逻辑"。比"另起 LLM 嚼碎历史"成本低一个量级，且信息更真（coder 真实意图，非二次转述）。

### 13.6 子图隔离结论（不破，保持）

**不破。** 五元组全部来自：fix_loop_history（GlobalState 已有）+ coder result 落库（经 `_map_coding_result`，见 §13.7 链路）+ 磁盘日志（S7-02）。只往 HumanMessage 注数据，`ReActState.messages` 一字不动。`fix_note` 是 coder 主动在 `<result>` 声明的一句话（走正常 result 提取链路），**不是去捞子图内的推理对话 messages**——捞后者才要破隔离回写 GlobalState（档 C 病），本方案不碰。

### 13.7 落点清单（供 dev-plan，修订后）

**核心链路问题（Maria 点名重点）：coder 的 fix_note 产生在 coding 节点，但 `fix_loop_history` 由 execution 节点 `_append_fix_record`(execution.py:2167) 写——怎么传？**

**链路方案（确定）**：coder 的 fix_note 经 GlobalState 一个新字段 `last_fix_note` 由 coding 写、execution append 时取。时序：
1. **coding 节点**：coder 在 `<result>` 输出 `fix_note` → `_map_coding_result`（coding.py:523）从 result 提取，写进 `updates["last_fix_note"]`（新字段，单点写）。
2. **execution 节点下一回合**：`_run_execution_agent` 跑完、`_maybe_interrupt_or_return` 判定"可修复→回 coding"时，`_append_fix_record`(execution.py:2167) 追加本轮 FixLoopRecord——**此时 state 里的 `last_fix_note` 正是上一轮 coder 写的**（这一轮 execution 是在跑上一轮 coder 改的代码），把它写进 `FixLoopRecord.fix_note`。
3. **files_touched 同链路**：coder result 的 `files_written` 同样经 `_map_coding_result` 写 `updates["last_files_written"]`，`_append_fix_record` 取。

> **时序自洽确认**：第 N 轮 FixLoopRecord 记录的是"coder 第 N 轮改了什么(files_touched/fix_note) + execution 第 N 轮跑出什么真错(category/log_path)"。coding 先跑(写 last_fix_note)→execution 后跑(跑代码+append record 取 last_fix_note)，`_append_fix_record` 执行时 `last_fix_note` 恰是本轮对应 coder 的输出。**链路时序天然对齐，无需调整谁写/写入时机。**

| 项 | 文件:落点 | 类型 | 说明 |
|---|---|---|---|
| `_FIX_NOTE_MAX_CHARS = 120` | coding.py 模块级常量 | 新常量 | fix_note 渲染字符上限，防撑爆 |
| coder 输出约定 +fix_note | coding.py `_CODING_SYSTEM_PROMPT_BODY`(:164-176) + result_schema(:89-112) | prompt/schema 改 | 修复回合段+`<result>`字段加 fix_note（固定文案，R-PC4 安全，§13.2.1） |
| fix_note/files_written 落库 | coding.py `_map_coding_result`(:523) | 逻辑+state写 | 从 result 提取 fix_note→`updates["last_fix_note"]`；files_written→`updates["last_files_written"]`（截断到 _FIX_NOTE_MAX_CHARS） |
| `last_fix_note`/`last_files_written` | **state.py GlobalState** | 新字段(2个) | coding→execution 传递通道（单点由 coding 写、execution append 取） |
| `fix_note` + `files_touched` | **state.py `FixLoopRecord`**(:176) | state 结构 | FixLoopRecord 加 `fix_note: str` + `files_touched: List[str]` |
| append 取 fix_note/files | **execution.py `_append_fix_record`**(:1954-1970) | 写入 | 从 `state["last_fix_note"]`/`state["last_files_written"]` 取，写进新建 FixLoopRecord |
| `_digest_fix_loop_history` | coding.py 新 helper | 新纯函数 | 读 fix_loop_history 全部记录+推导 log_path，渲染多行字符串(轮号升序、全保留、fix_note 截断、确定性字节幂等)；空历史返回 None |
| `fix_history_digest` 注入 | coding.py `_build_coding_context`(:359-372) | 加1键 | 非空才注入，与 last_error_summary 同守护 |

**state 契约增量**：`GlobalState` +2 传递字段（last_fix_note / last_files_written）、`FixLoopRecord` +2 字段（fix_note / files_touched）。均 TypedDict 加键，旧 checkpoint 兼容（helper 里 `.get(..., "")`/`.get(..., [])` 兜底）。**不动 react_base、不动 interrupt payload 键、不新增 LLM 调用。**

### 13.8 AC 建议（AC-S7-09 起，修订后）

| 编号 | 归属 | 验收标准 | 可测方式 |
|---|---|---|---|
| **AC-S7-09** | S7-05 | 修复回合 coder 的 curated context 含 `fix_history_digest`，内容含**全部**历史轮的 round+category+files_touched+**fix_note**+log_path，轮号升序、多行字符串 | 构造 fix_loop_count≥2 现场 mock（`task-99eef17bccf2` 同构 4 轮 import），断言 `_build_coding_context` 返回含 `fix_history_digest`、含各轮 log_path 与 fix_note；断言首轮不注入 |
| **AC-S7-10** | S7-05 | **全保留控量生效**：历史全部保留不裁剪；fix_note 超 `_FIX_NOTE_MAX_CHARS` 被截断；digest 总量受 MAX_FIX_LOOP_COUNT 封顶、不爆 | 构造 fix_loop_count=20（顶格）mock，断言 digest 含全部 20 轮（无窗口丢弃）、每轮 fix_note ≤120 字符、总字节 ≤ 上界估算(§13.4)；断言无"仅显示最近K轮"字样（窗口已删） |
| **AC-S7-11** | S7-05 | **coder 定位+逻辑确经链路落库并注入（须验红，修订2核心可测点）**：coder `<result>` 输出 fix_note → `_map_coding_result` 写 `last_fix_note` → 下轮 `_append_fix_record` 写进 FixLoopRecord.fix_note → `_digest_fix_loop_history` 渲染进 digest | 端到端链路 mock：模拟 coder result 含 fix_note="定位X修复Y" → 断言 `_map_coding_result` 返回含 `last_fix_note`；驱动 `_append_fix_record` → 断言 FixLoopRecord.fix_note==该值；断言 digest 含该值。**验红**：注掉链路任一环（map 不写/append 不取/digest 不渲染）后断言变红（防"coder 说了但没进历史"假绿，沿 AC-S6-10 教训） |
| **AC-S7-12** | S7-05 | 注入生效非假绿（须验红）：digest 里 log_path 指向历史轮日志真实存在且含真错行；coder 可 read_code_file 读到 | 落盘 round_1..4.log 含 `No module named 'src'`，断言 digest 的 log_path 与磁盘对齐、read_code_file 读到真错。**验红**：注掉 fix_history_digest 注入后断言变红 |
| **AC-S7-13** | S7-05 | R-PC4 守门：`fix_history_digest` 只进 HumanMessage、SystemMessage 稳定前缀字节不变（含新增"请声明 fix_note"固定文案后仍跨任务恒定）；同一 state 下 digest 字节幂等（无时间戳/uuid） | 断言注入前后 `_build_coding_system_prompt` 字节一致；断言新增 fix_note 指令是固定文案（两次不同 state 下 system prompt 该段字节相同）；断言同一 state 两次 `_digest_fix_loop_history` 字节相同 |
| **AC-S7-14** | S7-05 | 回归零退化：既有 coding context 键（last_error_summary/credential_degradations/code_output_dir）不受影响；sort_keys 幂等块结构不破；`_map_coding_result` 既有字段(code_output_dir/simulation_notice 等)不变 | 既有 coding context + map_result 套件零失败；断言 human_payload 仍合法 sort_keys JSON、既有键值不变 |

**验红命门**：AC-S7-11/12 是防假绿核心——修订2 引入了 coding→execution 跨节点链路(3 环)，任一环断裂都会导致"coder 说了但历史里没有"的假绿，必须逐环验红。

### 13.9 风险 + 开放问题（修订后：去窗口 Q1、加 coder 输出捕获风险）

| 编号 | 风险 | 缓解 | 回退 |
|---|---|---|---|
| R-S7-8 | coder 不输出/乱输出 fix_note（LLM 不遵守输出约定，或输出空/无关内容） | `_map_coding_result` 提取时校验(非空字符串才落值、截断到上限)；缺失则 fix_note 留空，历史段仍保留 round+category+files_touched+log_path（仍优于档 A） | fix_note 恒退化为空，方案降级为"四元组含 log_path 自读"，不炸 |
| R-S7-9 | coder fix_note 长篇大论撑爆 | `_FIX_NOTE_MAX_CHARS=120` 渲染截断 + prompt 明写"一两句"双重约束 | 截断硬拦，上界确定 |
| R-S7-10 | 链路时序错位（last_fix_note 被下轮覆盖前未被 append 取到） | §13.7 时序自洽已坐实(coding 先写→execution 后取，append 时 last_fix_note 恰为本轮 coder 输出)；单点写、last-write-wins | 若并发异常，退化为 fix_note 空（R-S7-8） |
| R-S7-11 | 历史轮日志文件被清理致 log_path 指向不存在 | 同 S7-02 R-S7-4：coder read 到"文件不存在"退回当前轮反馈，不炸 | 降级到 sp6 现状 |
| R-S7-12 | 全保留在极端 20 轮 + files_touched 多文件时 token 偏大 | §13.4 估算上界 ≈2200 token 封顶，可接受；files_touched 只记文件名不记内容 | 若实测偏大，可对 files_touched 记数量而非全列（单点，非本 Sprint） |

**开放问题（留 Maria）**：
- ~~Q1（窗口 K）~~ **已删**（修订1 去窗口、全保留）。
- **Q2（fix_note 字符上限 120 是否合适）**：倾向 120（中文一两句足够表达"定位X+修复Y"）。单点常量随时可调，非阻塞。
- **Q3（execution 判定理由）**：**已确认不加**（修订3）——不纳入 `_classify_execution` fix_strategy 规则文案，避免档 A 味道。execution 侧信息由 log_path 自读覆盖。
- **Q4（coder 遵守输出约定的稳定性）**：fix_note 依赖 coder 遵守新输出约定。首个开发批建议对现场靶(4 轮 import)做真跑抽验确认 coder 稳定输出 fix_note（合并既有 Maria 授权窗口，省配额）；若遵守率低，R-S7-8 退化兜底不阻断功能。**这是修订2 唯一依赖 LLM 行为的软点**，但有确定性退化保护。

---

## 14. S7-06 资源探索只读环境探测（安全底座：Q-S7-7 / Q-S7-8）

**对应 PRD**：`docs/sprint7/prd.md` v0.5 §2.6（Maria 真人 e2e 复验两次提出）
**日期**：2026-07-28
**本节范围（严格）**：**只裁 Q-S7-7（只读强制形态与防绕过）+ Q-S7-8（cwd 落点与工厂复用形态）两项**。Q-S7-9（超时与输出规模）/ Q-S7-10（结论下游落点）/ Q-S7-11（R-PC4 冻结令）/ Q-S7-12（探测节制）**本节不裁**，凡依赖处一律标注"待 Q-S7-N 裁决"。A-S7-9（不引入人在回路确认）**Maria 尚未复核**，本节按其现状设计。
**前置事实（源码级已核实）**：`run_command_tool.py` 的护栏**只约束"在哪跑"、不约束"跑什么"**——`_require_within_workspace` 校验的是 cwd，命令本身零机制限制，docstring 里"禁止用于完整训练/下载大数据集"只是给模型看的文字劝阻。故"把现有工具直接绑上去"不等于需求达成，缺的正是命令侧边界。

> **本节架构特征（先说结论）**：只读 = **整条命令精确匹配的扁平允许清单**（一个 `frozenset[tuple[str,...]]` + 一条 `if`，无分级 / 无分类 / 无多档权限），判定在任何 `Popen` 之前；载体 = **新建薄封装 `core/tools/env_probe_tool.py`**，100% 复用 `_run_subprocess` 四护栏 + `_require_within_workspace` + `mask_value`，**`run_command_tool.py` 一字不动** —— coding 零影响从"靠默认参数没传"升级为"文件未被修改"的结构性保证。cwd = `state["workspace_dir"]`，闭包绑定、非工具入参。**config.py 本次零改动、state 契约零改动、执行通道零重造。**

### 14.1 Q-S7-7 裁决：整条命令精确匹配，而非命令名

**裁决**：判定对象是 `shlex.split(command)` 得到的 **argv 元组整体**；命中清单放行，未命中返回结构化拒绝且不启动任何进程。

**为什么命令名粒度必然不安全（三条硬实证，均为"看起来只读"的命令名）**：

| 命令 | argv[0] | 实际行为 | 触碰的禁止项 |
|---|---|---|---|
| `nvidia-smi -r` / `-pm 1` / `-pl 100` | `nvidia-smi` | 重置 GPU / 改持久化模式 / 改功耗上限 | 禁止项 1（改变机器状态） |
| `pip list --outdated` | `pip` | 查询 PyPI index | 禁止项 2（联网拉取） |
| `git clone <url>` | `git` | 下载仓库 | 禁止项 2 |

补救只能靠参数黑名单，而黑名单 **fail-open**（漏一个即整套失效、且不可穷举），与 PRD 红线「必须由机制强制」冲突。

**备选方案对比**：

| | A：argv[0] 命令名白名单 | B：(命令名, 子命令) 二元白名单 | **C：整条 argv 精确匹配（选定）** |
|---|---|---|---|
| 判定规则数 | 1 | 2（含子命令位提取） | **1** |
| 名单内危险子命令 / 危险开关 | **漏 / 漏** | 封 / **漏** | **封 / 封** |
| 是否需黑名单兜底 | 必需 | 大概率需要 | **不需要** |
| 代码量 | ~5 行 | ~25 行（选项混排边界坑多） | **~5 行** |
| 参数灵活性 | 高 | 中 | 低（有等价替代，见下） |

**关键权衡**：C 是唯一无需黑名单兜底的方案；C 的代码量反而最小（B 才最复杂）；灵活性损失有等价替代——`pip show <包>` 由 `pip list` 一次覆盖，`df -h <路径>` 由 cwd 锚定后的 `df -h .` 覆盖（问的恰是产物落地盘）。fail-closed 的代价是一次重试，fail-open 的代价是一次不可逆的机器改动。

**允许清单初版（15 条，产品可调常量，增删走单点、机制不动）**：

```python
_PROBE_COMMANDS = (
    "nvidia-smi", "nvidia-smi -L", "nvcc --version",          # GPU / 驱动 / CUDA
    "lscpu", "free -h", "uname -srm",                          # CPU / 内存 / 架构
    "df -h .",                                                 # 磁盘（cwd 即产物落地盘）
    "python3 --version", "python --version",
    "pip --version", "pip list",                               # Python 环境
    "git --version", "gcc --version", "make --version", "cmake --version",
)
_ALLOWED_ARGV = frozenset(tuple(shlex.split(c)) for c in _PROBE_COMMANDS)
```

覆盖 PRD §2.6「允许探测」四类全部。刻意排除 `uname -a`（带主机名等无关信息）、`conda list` / `pip list --format=json`（输出体量，**待 Q-S7-9**）、一切解释器执行形态。

**拒绝返回形态**：沿 `run_command_tool.py` 的 `_error_json` 范式（结构化 JSON、`exit_code: -1`、不抛异常）；**仅"不在清单"这一拒因**额外带 `allowed_commands`（取自同一常量，单一真相源），供 agent 当轮自纠。该返回是**给 agent 看的**；给用户看的探测文案另受 AC-S7-19 约束（`resource_scout.py` 已在 `tests/test_e2e2_message_guard.py::_GUARDED_MODULES` 内）。

### 14.2 防绕过分析（对抗性自查）

| 攻击手法 | 是否被封 | 封堵点 |
|---|---|---|
| `python -c` / `python3 -c` / `python -m pip install` | 封 | argv 元组不等（清单内 python 仅 `--version`） |
| `sh -c` / `bash -c` / `env` / `xargs` / `nohup` / `timeout` / `nice` / `setsid` | 封 | argv[0] 不出现在任何清单条目 |
| 管道 / 重定向 / `&&` / `;` / `$(...)` / 反引号 | 封（双保险） | 无 shell（`_run_subprocess` 的 popen_kwargs 无 shell 键）+ argv 不匹配 |
| 绝对路径 `/bin/sh` / 相对路径 `./nvidia-smi` | 封 | 精确匹配要求 argv[0] 与清单裸名逐字符相等，带 `/` 即不等 |
| `git clone` / `git -c protocol.ext.allow=always` / `-o ProxyCommand` | 封 | 清单只含 `git --version`；ssh/curl/wget 不在清单 |
| `pip install` / `pip download` / **`pip list --outdated`** | 封 | 精确匹配（后者是命令名粒度会漏的实证） |
| `conda install` / `apt-get` / `yum` / `brew` | 封 | argv[0] 不在清单 |
| **`nvidia-smi -r` / `-pm` / `-pl`** | 封 | 精确匹配（命令名粒度会漏的第二个实证） |
| `cat ~/.ssh/id_rsa` / `ls ~` / `find /` / `history` / `printenv` | 封（双保险） | 不在清单；且子进程环境经 `_build_sandbox_env` 白名单继承，凭证类变量本就不透传 |
| 长耗时重负载（训练 / 基准） | 封 | 不在清单 + 超时杀子树兜底 |
| cwd 越界 | 封 | cwd 闭包绑定非入参；再叠 `_require_within_workspace` |
| 大小写 / 多空白 / NBSP 混淆 | 封（fail-closed） | 不等即拒 |
| 软链接伪装 / PATH 劫持 | **部分封** | 需写权限，资源探索工具集无写能力、`ln` 不在清单；残余见 R-S7-17 |
| **清单漂移**（后人加带自由参数条目） | **未封（流程风险）** | 清单即信任根；靠 AC-S7-21 守门 + 人工评审 |
| **stdin 继承**（`_run_subprocess` 未设 `stdin=DEVNULL`） | **未封（当前无实害）** | 清单 15 条均不读 stdin；封堵需改共享执行路径，见 R-S7-18 |

**未采纳的可选加固**：对软链接 / PATH 劫持可加 `shutil.which(argv[0])` 后断言不落在 `WORKSPACE_DIR` 下（约 3 行）。**不做** —— 它只封"PATH 含 `.` 且恶意二进制恰在 workspace 根"这一窄链（克隆落点是 `workspace/repos/<name>/`，够不着），封不住真正的残余（宿主 PATH 被污染 ≡ 宿主已陷，超出威胁模型）；加之属安全剧场，与最小抽象红线冲突。

### 14.3 Q-S7-8 裁决：cwd = workspace_dir；另起薄封装，run_command_tool.py 零改动

**cwd**：`state["workspace_dir"]`（资源探索时已就绪），回退 `config.WORKSPACE_DIR`（由 `ensure_directories()` 建目录）。**闭包绑定、不作为工具入参**；再叠 `_require_within_workspace`（对 `resolved == workspace` 放行，故 workspace 根自身合法）。`code_output_dir` 此刻为 `None`，不可用（PRD §2.6 已核实）。目录不存在的极端情形由 `_run_subprocess` 的 `OSError` 兜底转 `exit_code=-1`，不炸子图。

**形态备选对比**：

| | 1：复用工厂加 `allowed_commands` 参数 | 2：抽公共内核两边薄包 | **3：另起薄封装（选定）** |
|---|---|---|---|
| 改动 coding 共享文件 | 改 | 改（重构） | **不改** |
| coding 零影响靠什么 | 靠"没传新参数"（默认 = 不限制，**fail-open 默认值**） | 靠重构无回归 | **靠文件未被修改** |
| 工具描述冲突 | **无解**（一个 `@tool` 只有一份 docstring，而它是 schema；coding 那份明写"如 `python -c` / `py_compile`"，探测侧照抄等于诱导模型用必被拒的命令） | 可解 | **可解** |
| 凭证口子 | 签名带 `extra_env`，须记得显式不传 | 同左 | **签名无此口** |
| PRD §7 建议的该文件单收口窗口 | 需要 | 需要 | **可摘除** |

**关键权衡（正面回答"同一工具两个相反边界怎么共存"）**：不共存，拆开。两个用途在命令边界、通用解释器、cwd、凭证注入、工具描述**五个维度上全部相反或不同**，共用一个 `@tool` 壳的收益只剩"少建一个文件"。真正值得复用的是执行护栏，薄封装 100% 复用（`_run_subprocess` 四护栏 + `_require_within_workspace` + `mask_value`），**不重造执行通道** —— A-S7-8 的实质诉求全部满足，被换掉的只是"复用同一个 `@tool` 壳"这一实现选择，而 A-S7-8 原文已写明该推翻路径（"若架构师论证改共享工具对 coding 风险不可控，可改为独立薄封装，产品契约不变"）。产品契约一条未动。**"最小抽象" ≠ "最少文件"**：一条判定规则 + 一个常量 + 一个工厂，小于"带模式开关、两套语义、一份自相矛盾 docstring 的共享工具"。

**实现草图（架构级，非交付代码）**：

```python
# core/tools/env_probe_tool.py（新增）
_PROBE_COMMANDS: Tuple[str, ...] = (...)                                   # 人读清单 = 唯一真相源
_ALLOWED_ARGV = frozenset(tuple(shlex.split(c)) for c in _PROBE_COMMANDS)  # 预解析，模块级一次

def make_probe_environment_tool(base_dir: str):
    @tool
    def probe_environment(command: str) -> str:
        """（docstring 内嵌清单；全静态、零论文级/任务级动态值 → R-PC4 安全）"""
        try:
            argv = shlex.split(command)
        except ValueError as exc:
            return _reject(f"命令解析失败: {exc}")
        if tuple(argv) not in _ALLOWED_ARGV:        # ← 唯一判定，在任何 Popen 之前
            return _reject_with_list()
        try:
            _require_within_workspace(base_dir, label="环境探测工作目录")
        except Exception as exc:
            return _reject(f"工作目录越界: {exc}")
        rr = _run_subprocess(argv, cwd=base_dir,
                             timeout=config.RUN_COMMAND_TIMEOUT,                # 待 Q-S7-9
                             output_max_bytes=config.SANDBOX_OUTPUT_MAX_BYTES,  # 待 Q-S7-9
                             extra_env=None)                                    # 不注凭证
        return json.dumps({...mask_value(rr.stdout)...}, ensure_ascii=False,
                          sort_keys=True, default=str)
```

**清单常量放工具模块内而非 `config.py`** —— 它是该工具的语义边界，必须与 docstring 同步；分处两文件更易漂移。

### 14.4 落点清单（供 dev-plan）

| 项 | 文件:落点 | 类型 | 说明 |
|---|---|---|---|
| `_PROBE_COMMANDS` / `_ALLOWED_ARGV` / `make_probe_environment_tool(base_dir)` | `core/tools/env_probe_tool.py` | **新文件（纯新增，约 90 行）** | 清单常量放本模块（与 docstring 同源防漂移），不放 `config.py` |
| 工具装配 5→6 | `core/nodes/resource_scout.py` 的 `get_tools` | 加 1 行 | `make_probe_environment_tool(base_dir=state.get("workspace_dir") or str(config.WORKSPACE_DIR))` |
| SystemMessage 工具说明 | `resource_scout.py` 的 `_RESOURCE_SCOUT_SYSTEM_PROMPT_BODY`（冻结区） | prompt 改 | **待 Q-S7-11 冻结令裁决**，本节不裁；仅确认工具侧 docstring 与清单均为静态常量、跨论文字节一致，不引入论文级动态值 |
| `core/tools/run_command_tool.py` | — | **零改动** | coding 零影响结构性保证；PRD §7 曾建议为该文件设单收口窗口，本裁决下**可摘除**，降低并行冲突面 |
| `config.py` | — | **零改动** | 超时 / 输出上限沿用 `RUN_COMMAND_TIMEOUT=120` 与 `SANDBOX_OUTPUT_MAX_BYTES=1MiB` —— **是否收窄待 Q-S7-9** |
| 既有 5→6 断言 | `tests/test_sprint2_b2.py`、`tests/test_sprint6_b1_prompt_guards.py` | 断言同步 | 沿"只换不弱化"纪律；两处文案均写死"5 个 / 由 6 降为 5"，须同步改为 6 |
| 探测结论落点 | `resource_info` / 规划上下文 | — | **待 Q-S7-10 裁决**，本节不裁 |
| state 契约 | — | **零改动** | 不新增字段、不动 interrupt payload |

### 14.5 AC 建议

PRD 既有 **AC-S7-15**（工具集 5→6 + cwd 锚定 / 越界被拒）与 **AC-S7-16**（只读保证，须副作用探针 + 验红）已覆盖本节主体，测试点细化：

- **AC-S7-16 的必拒集**（建议逐条覆盖）：`python -c "..."`、`sh -c "..."`、`env`、`xargs`、`pip install x`、`pip list --outdated`、`git clone <url>`、`nvidia-smi -r`、`/bin/sh`、`./nvidia-smi`、`cat ~/.ssh/id_rsa`、`df -h /home`（含自由参数）。
- **副作用探针形态**：以指向探针文件的 `rm` / 重定向写入类命令构造，执行后断言探针文件原样存在；并断言判定发生在 `Popen` 之前（可 monkeypatch `_run_subprocess` 断言未被调用 —— 这比只断返回码更强）。
- **必过集**：清单 15 条中不依赖本机可选组件的若干条（如 `python3 --version` / `df -h .` / `uname -srm`）断言 `exit_code==0` 且有输出。

**建议新增两条守门 AC（PRD 现止于 AC-S7-20，编号待 PM 回填）**：

| 编号 | 归属 | 验收标准 | 可测方式 |
|---|---|---|---|
| **AC-S7-21**（建议） | S7-06 | **清单形态守门（防漂移，绕过分析已列为唯一未封的流程风险）**：`_PROBE_COMMANDS` 每一项经 `shlex` 解析后 argv 元组完全确定（不含 `{}` / `<>` / `$` 等占位符形态）；清单内不存在通用解释器执行形态（无条目 argv 含 `-c`；无条目 argv[0] ∈ {sh,bash,zsh,env,xargs,nohup,timeout,nice,...}）；docstring 内清单文本与常量一致 | 对常量做形态断言（遍历清单逐条校验）+ docstring 与常量一致性断言。任何往清单加自由参数条目的改动必须打红本条 |
| **AC-S7-22**（建议） | S7-06 | **双用途边界互不削弱（本需求最易出事处，须对照断言）**：同一测试内，coding 装配出的 `run_command` 执行 `python -c "print(1)"` 与 `python -m py_compile <file>` **仍成功**；资源探索装配出的 `probe_environment` 执行同样两条命令**被结构化拒绝且未执行** | 一正一负对照用例（同文件相邻两条），把"边界相反且互不削弱"直接变成可测断言；比"断言 `run_command_tool.py` 未被 diff"更可执行 |

### 14.6 风险登记

| 编号 | 风险 | 缓解 | 回退 |
|---|---|---|---|
| R-S7-13 | 清单太紧，探不到某项关键事实（如需精确查单个包版本） | `pip list` 一次覆盖绝大多数场景；缺项走单点加清单条目 | 加条目，机制不动、无需重新论证安全性 |
| R-S7-14 | 模型反复写出清单外命令、浪费轮次（S7-05 真跑实测 coder 遵守率仅 75%，不服从是常态） | 拒绝返回附 `allowed_commands` 供当轮自纠；prompt 侧措辞 **待 Q-S7-11**，控量 **待 Q-S7-12** | 清单直写进 SystemMessage（代价属 Q-S7-11 冻结令范畴） |
| R-S7-15 | `pip list` 在依赖多的机器上输出数百行，吃 context / 拖 token | 现有 1MiB 字节截断兜底（不炸），但 token 成本仍在 | **待 Q-S7-9**（可换更紧凑形态或加探测专用输出上限） |
| R-S7-16 | 清单漂移：后人加入带自由参数条目重新打开缺口 | AC-S7-21 形态守门（改清单必须过守门）+ 人工评审 | 清单是唯一信任根，评审责任在人，无机制可替 |
| R-S7-17 | 宿主 PATH 被第三方污染，清单裸名解析到恶意二进制 | 资源探索工具集无写文件能力、`ln` / 改 env 全被拒；该风险等价于宿主已被攻陷 | 可选加固（`shutil.which` 后断言不在 workspace 下），本次评估为安全剧场故不做 |
| R-S7-18 | `_run_subprocess` 未设 `stdin=DEVNULL`（已核实），若将来清单加入会读 stdin 的命令（如裸 `python`）会挂到超时才被杀 | 当前清单 15 条均不读 stdin，无实害；AC-S7-21 的"无解释器形态"守门顺带压住这一类 | 若确需封堵，改共享 `_run_subprocess` 会触碰 coding 执行路径，须单独设收口窗口 |

### 14.7 本节未裁项（后续批次单独授权）

| 编号 | 待裁内容 | 本节的临时立场 |
|---|---|---|
| Q-S7-9 | 超时与输出规模 | 暂沿用 `RUN_COMMAND_TIMEOUT=120` / `SANDBOX_OUTPUT_MAX_BYTES=1MiB`；R-S7-15（`pip list` 体量）与残余风险"清单内命令偶发挂起最坏 120s"一并归该项 |
| Q-S7-10 | 探测结论的下游落点 | 本节只裁"怎么跑得安全"，不裁"探到的事实进哪个键"；AC-S7-18 的命门在该项 |
| Q-S7-11 | R-PC4 冻结令（工具清单 5→6 改 `bind_tools` 前缀 + SystemMessage 工具说明措辞） | 本节只保证工具侧零动态值（docstring 与清单均为静态常量，跨论文字节一致，不破 AC-S7-20 的根本性质）；前缀变更是否放行、prompt 怎么写，归该项 |
| Q-S7-12 | 探测节制（轮次与预算） | 本节的 fail-closed 设计会产生"写错即被拒、需重试"的轮次消耗（R-S7-14），控量归该项 |
| A-S7-9 | 不引入人在回路确认 | **Maria 尚未复核**，本节按其现状设计（无 interrupt 新种类、无逐条确认） |


*（v1.1 全文完：v1.0 六项裁决 + S7-01~03 方案，§13 增补 S7-05 记忆增强档 B（Maria 三点修订后：全保留无窗口 + coder 自述 fix_note 定位/逻辑 + 不加 execution 判定理由）。核心 = 复用 S7-02 落盘日志 + coder 顺带自述，零新管道/零新增 LLM/零 react_base 改动/子图隔离不破；state 加 4 键（2 传递+2 记录）旧 checkpoint 兼容；token 上界受 MAX_FIX_LOOP_COUNT=20 + _FIX_NOTE_MAX_CHARS=120 双封顶。待 Maria 拍板 Q2/Q4 后转 dev-plan → 开发；files_written 取值链路由 dev 实现时按 §13.7 落。）*


*（v1.2 增补完：§14 = S7-06 只读环境探测安全底座——Q-S7-7 裁「整条命令精确匹配的扁平允许清单」（命令名粒度有 `nvidia-smi -r` / `pip list --outdated` / `git clone` 三条实证会漏，黑名单 fail-open 不满足「机制强制」红线）、Q-S7-8 裁「另起薄封装 `env_probe_tool.py`，`run_command_tool.py` 零改动」（coding 侧需要 `python -c`、探测侧必须禁它，一个 @tool 只有一份 docstring，边界相反无法共存故拆开）。含对抗性绕过分析 15 类 + 两条真残余风险（清单漂移 / stdin 未设 DEVNULL）+ 建议新增 AC-S7-21/22。Q-S7-9~12 四项未裁，A-S7-9 待 Maria 复核。）*
