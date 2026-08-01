# Sprint 7 核心架构设计文档：修复循环失控治理族（S7-01~03）+ 修复循环记忆增强（S7-05）+ 资源探索只读环境探测（S7-06）

**文档版本**：v1.3（v1.0 = Q-S7-1~6 六项裁决 + S7-01~03 三需求方案；v1.1 增补 §13 = S7-05 修复循环记忆增强档 B 方案；v1.2 增补 §14 = S7-06 只读环境探测的安全底座，仅裁 Q-S7-7/Q-S7-8 两项；**v1.3 增补 §15/§16/§17 = S7-06 剩余四项裁决 Q-S7-9~12 全部收口 + 主控跨节合并裁定**，Maria 2026-07-28 授权）
**日期**：2026-07-19（v1.0）／2026-07-20（v1.1 §13）／2026-07-28（v1.2 §14、v1.3 §15~§17）
**作者**：架构师代理（§15 与 §16 由两位架构师并行独立裁决，§17 为主控跨节收口）
**对应 PRD**：`docs/sprint7/prd.md` v0.3 §2.1~2.3（Maria 拍板 2026-07-18，Q-S7-1~6 全部在本文 §1~§6 裁决）+ v0.4 §2.5（S7-05，本文 §13）。+ v0.5 §2.6（S7-06「资源探索能实际探测本机环境」，本文 §14~§17）。**S7-06 的 Q-S7-7~12 六项已全部裁决**：Q-S7-7 / Q-S7-8 见 §14（安全底座，2026-07-28 上午）；**Q-S7-10 见 §15（探测结论下游落点，AC-S7-18 命门）；Q-S7-9 / Q-S7-11 / Q-S7-12 见 §16（超时与输出规模 / 冻结令 / 探测节制）；§17 = 主控对 §15 与 §16 在「探测输出上限」上的冲突收口（含实测证据，推翻 §16.1 裁决 1）**。**A-S7-9（不引入人在回路确认）已由 Maria 于 2026-07-28 复核确认维持**，推翻路径关闭，由"可单点推翻的产品判断"转为已确认前提。
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

> **本节架构特征（先说结论）**：只读 = **整条命令精确匹配的扁平允许清单**（一个 `frozenset[tuple[str,...]]` + 一条 `if`，无分级 / 无分类 / 无多档权限），判定在任何 `Popen` 之前；载体 = **新建薄封装 `core/tools/env_probe_tool.py`**，100% 复用 `_run_subprocess` 四护栏 + `_require_within_workspace` + `mask_value`，**`run_command_tool.py` 一字不动** —— coding 零影响从"靠默认参数没传"升级为"文件未被修改"的结构性保证。cwd = `state["workspace_dir"]`，闭包绑定、非工具入参。**config.py 本次零改动、执行通道零重造；state 契约在 §14 自身范围内零改动。**
>
> **⚠ 2026-07-28 订正（v1.3）**：上句原文为「state 契约零改动」，**属越界**——它在 Q-S7-10（探测结论落点）尚未裁决时，就替该项锁死了结论范围（同节 §14.4 落点表却把该项标为"待 Q-S7-10 裁决"，自相矛盾）。**S7-06 整体的 state 契约增量以 §15.7 为准：`GlobalState` +1 键 `local_env_facts: str`。**

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
| 探测结论落点 | `resource_info` / 规划上下文 | — | ~~待 Q-S7-10 裁决~~ → **已于 2026-07-28 裁决，见 §15**：落 `GlobalState.local_env_facts` 单键，**不进 `resource_info`** |
| state 契约 | — | **§14 自身零改动；S7-06 整体 +1 键** | §14 三项裁决本身不新增字段、不动 interrupt payload；**探测结论落点由 §15（Q-S7-10）裁出 `GlobalState.local_env_facts: str`**，见 §15.7 |

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
| Q-S7-9 | 超时与输出规模 | ~~暂沿用 `RUN_COMMAND_TIMEOUT=120` / `SANDBOX_OUTPUT_MAX_BYTES=1MiB`~~ → **已裁，见 §16.1 + §17**：超时收窄为 `_PROBE_TIMEOUT_SECONDS=30`（工具模块内）；**输出上限经 §17 主控裁定为工具返回端新增 `_PROBE_OUTPUT_MAX_BYTES=2500`**（§16.1 原判"沿用、零新常量"已被实测推翻） |
| Q-S7-10 | 探测结论的下游落点 | **已裁，见 §15**：落 `GlobalState.local_env_facts: str`，确定性从工具历史提取（零 LLM 依赖），经 `_format_planning_context` 第 6 形参单键增补进规划上下文 |
| Q-S7-11 | R-PC4 冻结令（工具清单 5→6 改 `bind_tools` 前缀 + SystemMessage 工具说明措辞） | **已裁，见 §16.2：放行「破一次」**。连带面经核实**小于**本节预估：三条 Prompt Cache 基线脚本无一跑 resource_scout ⇒ **零基线作废、零真跑复采、零 deepxiv 配额** |
| Q-S7-12 | 探测节制（轮次与预算） | **已裁，见 §16.3**：只做 prompt 措辞、不加机制计数器（无"措辞不够"的实证）；探测作**链外补充步**，三步降级链 1/2/3 字节不动 |
| A-S7-9 | 不引入人在回路确认 | ~~Maria 尚未复核~~ → **2026-07-28 Maria 复核确认维持**（不弹窗、不新增中断种类）。推翻路径关闭，由"可单点推翻的产品判断"转为**已确认前提** |

> **2026-07-28（v1.3）状态更新**：本表所列 Q-S7-9~12 四项 + A-S7-9 **已全部收口**，S7-06 设计侧无待裁项。上表保留原文并加删除线，是**有意的历史留痕**（体例同 §14.4），不是残留。


*（v1.1 全文完：v1.0 六项裁决 + S7-01~03 方案，§13 增补 S7-05 记忆增强档 B（Maria 三点修订后：全保留无窗口 + coder 自述 fix_note 定位/逻辑 + 不加 execution 判定理由）。核心 = 复用 S7-02 落盘日志 + coder 顺带自述，零新管道/零新增 LLM/零 react_base 改动/子图隔离不破；state 加 4 键（2 传递+2 记录）旧 checkpoint 兼容；token 上界受 MAX_FIX_LOOP_COUNT=20 + _FIX_NOTE_MAX_CHARS=120 双封顶。待 Maria 拍板 Q2/Q4 后转 dev-plan → 开发；files_written 取值链路由 dev 实现时按 §13.7 落。）*


*（v1.2 增补完：§14 = S7-06 只读环境探测安全底座——Q-S7-7 裁「整条命令精确匹配的扁平允许清单」（命令名粒度有 `nvidia-smi -r` / `pip list --outdated` / `git clone` 三条实证会漏，黑名单 fail-open 不满足「机制强制」红线）、Q-S7-8 裁「另起薄封装 `env_probe_tool.py`，`run_command_tool.py` 零改动」（coding 侧需要 `python -c`、探测侧必须禁它，一个 @tool 只有一份 docstring，边界相反无法共存故拆开）。含对抗性绕过分析 15 类 + 两条真残余风险（清单漂移 / stdin 未设 DEVNULL）+ 建议新增 AC-S7-21/22。Q-S7-9~12 四项未裁，A-S7-9 待 Maria 复核。**【v1.3 更新：该句已过时——四项均已于同日裁决，见 §15~§17；A-S7-9 已由 Maria 复核确认维持。】**）*

---

## 15. S7-06 探测结论的下游落点（Q-S7-10 裁决）

**对应 PRD**：`docs/sprint7/prd.md` v0.5 §2.6 契约 5 / §5 A-S7-13 / §6 Q-S7-10；命门 AC 为 **AC-S7-18（防白探，须逐环验红）**
**日期**：2026-07-28（v1.3）
**本节范围（严格）**：只裁 **Q-S7-10（探测结论的下游落点）**。Q-S7-9 / Q-S7-11 / Q-S7-12 见 §16。**本节 R-S7-19 与 §16.1 的输出上限裁决存在冲突，已由 §17 收口——阅读 §15.10 前请先读 §17。**
**承诺边界（2026-07-28 Maria 新裁，直接框定本节）**：S7-06 只负责"硬件事实真正到达规划可见上下文"，**不负责规划怎么用**——全局 PRD §6.2 的"据硬件约束自动调参"已转 backlog，本节不设任何"必须据此调 batch size"的硬约束。

> **本节架构特征（先说结论）**：落点 = **`GlobalState` 顶层单键 `local_env_facts: str`**（预渲染多行字符串，缺省 `""`）。产出 = **确定性从 ReAct 工具历史提取**（沿 BUG-S1-03 `_backfill_repos_from_tools` 范式），**零 LLM 依赖、零 `<result>` 字段、零 schema 改动**。送达 = `_format_planning_context` 新增第 6 形参（尾部带默认值），非空才写单键。**`ResourceInfo` 零改动、`RESOURCE_SCOUT_SCHEMA` 零改动、冻结区 SystemMessage 的【输出格式】段零改动、interrupt payload 零改动。** 唯一契约增量 = `GlobalState` +1 键（沿 S7-05 `last_fix_note` 先例，旧 checkpoint `.get` 兜底）。

### 15.1 前置核实（源码级，含对既有文档的三处订正）

| # | 事实 | 位置 | 对裁决的作用 |
|---|---|---|---|
| 1 | `_format_planning_context` 签名在 **302-308**、字段选取体在 **314-354**；确认**不读 `analysis_notes`** | `core/nodes/planning.py:302-354` | PRD 结论方向成立；**行号 302-307 订正为 302-354** |
| 2 | 该函数实际还取 **`resource_strategy`**（:340 无条件写入）与 **`pending_repo_url`**（:351-352） | 同上 | **订正 PRD §2.6 契约 5 / §6 Q-S7-10 的枚举**；同时坐实：`resource_info` 的字段**必须被该函数显式白名单**才能到规划，没有自动搭车 |
| 3 | `RESOURCE_SCOUT_SCHEMA["properties"]`（去 `search_log`）**集合恒等于** `ResourceInfo.__annotations__` | `tests/test_sprint2_b2.py:141-147` | **决定性**：往 `ResourceInfo` 加字段 ⇒ 必须加 LLM 输出 schema ⇒ 事实变成 LLM 产物（S7-05 真跑遵守率 75%） |
| 4 | `resource_info` 在 planning 侧有 3 处按显式键整体重建：`_merge_user_repos_from_tools`(:455/:565)、`_switch_selected_repo`(:778)，结果写回 state(:674/:930) | `core/nodes/planning.py` | **决定性**：进 `ResourceInfo` 会在 revise / switch_repo / S2-13 合并路径上静默丢失探测结论 |
| 5 | `GlobalState.__annotations__` **无任何精确集合 / 计数断言**，现存断言全是 `field in ann` 形态 | `tests/test_sprint3_a2.py:47`、`test_sprint4_a2.py:48`、`test_sprint5_t12_state.py:57/142`、`test_sprint7_s705_memory.py:109`；`test_sprint1_smoke.py:205-237` 只断个别默认值 | 加 GlobalState 键**零既有断言被打红** |
| 6 | `_map_resource_scout_result` 已是 3 参签名、拿得到 `react_messages`；`_parse_tool_content` 有"剥离截断后缀再试"路径 | `core/nodes/resource_scout.py:427-431`、`:290-318`（守门 `test_sprint2_b2.py:154-156`） | 确定性提取**零新增管道**，直接复用现成范式。**⚠ 见下方主控订正** |
| 7 | planning 的冻结前缀**只有 SystemMessage**：`_build_planning_system_prompt` 直接 `return _PLANNING_SYSTEM_PROMPT_BODY`、忽略 context；`initial_messages=[SystemMessage(...)]` 后才追加 HumanMessage | `core/nodes/planning.py:285-291`；`core/react_base.py:850-862` | `_format_planning_context` 的**全部**产出落在动态区，加键**不污染冻结前缀**（详见 §15.4） |
| 8 | `run_command` 返回 JSON 恰 5 键 `{exit_code, stdout_tail, stderr_tail, timed_out, truncated}`——**不回显命令**；`_run_subprocess` 对"命令不存在"返回 `exit_code=-1` + `stderr="subprocess start failed: ..."` | `core/tools/run_command_tool.py:121-132`；`sandbox/local_venv.py:391-405` | 触发 §15.3 对 §14.3 草图的一处必要增补（返回须回显命令），并决定 §15.5 的失败呈现规则 |
| 9 | `architecture.md` §14 / §14.4 已写死"state 契约零改动"，同表却把落点标为"待 Q-S7-10" | 本文 §14 开头、§14.4 落点表；`docs/TODO.md` | **订正**：§14 越界覆盖了未裁项。本裁决为 `GlobalState` +1 键，两处表述**已于 v1.3 同批订正** |

> **⚠ 主控核实订正（2026-07-28，对上表 #6）**：架构师原文表述为"`_parse_tool_content` **已能容忍** `... [truncated at` 后缀"，**过于乐观**。主控实测：该函数（`resource_scout.py:309-317`）确实会剥掉截断后缀再 `json.loads`，**但剥完剩下的是缺闭合括号的残缺 JSON，照样解析失败、返回 `None`**。它容忍的是**后缀那行字**，不是残缺 JSON 本体——项目里能修残缺 JSON 的 `_repair_truncated_json_prefix`（sp1 BUG-S1-02 加在 `react_base.py`）**并未被 `_parse_tool_content` 复用**。实测数据与由此产生的处置见 **§17**。

> **⚠ 主控核实订正（2026-07-28，行号）**：架构师原文将 `_map_resource_scout_result` 的第三个 return 点记为 `:539`，**实测为 `:549`**（`grep -n "return update"`）。下文 §15.3(b) 与 §15.7 已按实测值订正。

### 15.2 裁决：`GlobalState.local_env_facts`，确定性产出，规划侧单键增补

**存在哪**：`core/state.py::GlobalState` 新增

```python
# === Sprint 7 S7-06 新增（只读环境探测结论落点，架构 v1.3 §15）===
# resource_scout 单点写（_map_resource_scout_result 从 ReAct 工具历史确定性提取，
# 非 LLM <result> 字段）；planning 单点读（_format_planning_context）。单值、
# last-write-wins 正确，**绝不加 reducer**。旧 checkpoint 无此键由消费侧
# ``.get("local_env_facts", "")`` 兜底，不 KeyError。
# 值 = 预渲染多行字符串（本机实测环境事实），空串表示"未知"。
local_env_facts: str
```

`create_initial_state` 追加 `local_env_facts=""`（沿 S7-05 `last_fix_note=""` 先例）。

**为什么不进 `ResourceInfo`**（正面回答 PRD 给的二选一）：`ResourceInfo` 在本仓库不是一个自由的数据袋，它被两条硬约束绑死——(a) 与 `RESOURCE_SCOUT_SCHEMA` 集合恒等（§15.1 #3），加字段等于把机器事实降格为 LLM 产物；(b) planning 侧 3 处按显式键重建（§15.1 #4），加字段等于在 revise/switch_repo 路径上埋一个静默数据丢失。两条都不是"多改几行"的代价，是**把 S7-06 的立意反过来**（只读靠机制→改成靠 75% 遵守率；防白探→改成 revise 后重新变白探）。而 A-S7-13 的实质诉求是"最小单键、不新建结构"，`GlobalState` +1 个 `str` 键**完全满足**：无新 TypedDict、无嵌套、无枚举、无环境画像结构（PRD §2.6 非目标 3 守住）。

**怎么产出（零 LLM 依赖，本裁决的另一半）**：不要求 agent 在 `<result>` 里写任何新字段。`_map_resource_scout_result` 拿到的 `react_messages` 里已经有全部 `probe_environment` 的 ToolMessage，直接确定性提取即可——这与本仓库既有的 `_backfill_repos_from_tools`（"LLM 漏写就从工具历史捞"）是同一条治理线。收益：AC-S7-18 变成**确定性可测**，不受 R-S7-14（模型不服从）影响；且 SystemMessage 的【输出格式】段（`resource_scout.py:108-121`，冻结区）**不必为本条改动**——Q-S7-11 的冻结令范围因此**不被 Q-S7-10 扩大**（仍只含工具说明段，见 §16.2）。

### 15.3 最小 diff 草图（接口级，非交付代码）

**(a) `core/nodes/planning.py`——共 4 行**

```python
def _format_planning_context(
    paper_meta, paper_analysis, resource_info, user_feedback,
    pending_repo_url: Optional[str] = None,
    local_env_facts: Optional[str] = None,      # ← 新增第 6 形参，尾部 + 默认值，既有 5 参调用零破坏
) -> Dict[str, Any]:
    ...
    # （紧接现有 pending_repo_url 分支之后，:352 之下）
    # S7-06：资源探索阶段实测的本机环境事实（来源 = 只读探测工具历史，非论文推断）。
    # 为空时不写——"未知"在规划上下文里就是"这个键不存在"，不造哨兵值（§15.5）。
    if local_env_facts:
        payload["local_env_facts"] = _coerce_str(local_env_facts)
    return payload
```

```python
# :711-717 build_context lambda 追加第 6 实参
build_context=lambda state: _format_planning_context(
    state.get("paper_meta") or {},
    state.get("paper_analysis") or {},
    state.get("resource_info") or {},
    state.get("_planning_user_feedback"),
    state.get("_planning_pending_repo_url"),
    state.get("local_env_facts"),            # ← 新增
),
```

**(b) `core/nodes/resource_scout.py`——1 常量 + 1 纯函数 + 3 处返回接线**

```python
_PROBE_OUTPUT_MAX_CHARS: int = 400     # 单条探测输出渲染上限（沿 coding.py:78 _FIX_NOTE_MAX_CHARS 范式）

def _digest_env_probe(react_messages: Optional[Any]) -> str:
    """从 ReAct 工具历史确定性提取本机环境事实，渲染为单个多行字符串。

    沿 _backfill_repos_from_tools（BUG-S1-03）范式：不依赖 LLM 服从度，直接扫
    name == PROBE_TOOL_NAME 的 ToolMessage，用 _parse_tool_content 解析。
    渲染规则见 §15.5。空 / 全不可解析 / 任意异常 → 返回 ""。
    """
```

`_map_resource_scout_result` 顶部算一次 `facts = _digest_env_probe(react_messages)`，并在**全部 3 个 return 点**（**:459 空结果降级 / :479 agent 报错降级 / :549 正常路径**——第三点原稿记 :539，主控实测订正为 :549）非空时写入 `local_env_facts`。
**三个 return 点都要写**：agent 的 `<result>` 崩了不代表机器没被探到，探到的事实照样对规划有用——这正是 BUG-S1-03 范式的原意。实现上建议一个 3 行的收尾 helper `_with_env_facts(update, facts)`，避免三处复制。

**(c) `core/tools/env_probe_tool.py`——对 §14.3 草图的一处必要增补（不改变 Q-S7-8 裁决实质）**

§14.3 的返回草图沿用了 `run_command` 的 5 键结构，而该结构**不回显命令**（§15.1 #8）。下游 digest 就无法给每段输出标出"这是哪条命令问出来的"，一堆无主的表格对规划毫无价值。故 `probe_environment` 的返回 JSON **须增一个 `command` 键**，取值 **`" ".join(argv)`（规范化回显，而非原始入参串）**：

```python
return json.dumps(
    {
        "command": " ".join(argv),      # ← 增补：规范化回显，digest 的唯一命令来源
        "exit_code": rr.exit_code,
        "stdout_tail": mask_value(rr.stdout),
        "stderr_tail": mask_value(rr.stderr),
        "timed_out": rr.timed_out,
        "truncated": rr.output_truncated,
    },
    ensure_ascii=False, sort_keys=True, default=str,
)
```

**为什么取规范化回显而不是原始 `command` 入参**：`shlex.split` 会折叠多余空白，模型写 `df  -h  .` 与 `df -h .` 得到同一个 argv 元组、同样命中清单，但原样回显会让 digest 字节抖动。用 `" ".join(argv)` 后，digest **对模型的书写变体免疫**，字节幂等（§15.4 的硬要求）。清单条目本身无引号，`" ".join` 与清单文本逐字符相等。
本增补不触碰 `run_command_tool.py`、不改执行通道、不改清单机制——Q-S7-8 的裁决实质（薄封装 / `run_command_tool.py` 零改动 / 100% 复用护栏）全部保持。

同时 `env_probe_tool.py` 导出 `PROBE_TOOL_NAME: str = "probe_environment"`，`resource_scout.py` 直接 import 使用（工具装配本就要 import 该模块，零额外 import），**杜绝"工具改名 → digest 悄悄失效 → 白探回潮"这一类静默漂移**。

### 15.4 数据形态：预渲染字符串，且落在动态尾部——Prompt Cache 已核实无污染

**形态 = `str`（预渲染多行），不是 dict**。四条理由：
1. **避开 sort_keys 结构问题**：整个 curated context 是一个 `json.dumps(sort_keys=True)` 块（`react_base.py:854-859`），sort_keys 只排**顶层键名**，字符串值内部顺序完全自控——与 S7-05 `fix_history_digest` 同款（§13.3）。
2. **dict 会长出结构**：一旦是 `{"gpu": ..., "cuda": ..., "disk": ...}`，下一步必然是"再加一个字段"，直通 PRD §2.6 **非目标 3（不做环境画像持久化）**。
3. **结构化需要按命令写解析器**：把 `nvidia-smi` 表格、`lscpu`、`df -h` 解析成字段 = 一套 per-command parser 动物园，正是本项目反过度工程红线要避的那类分类体系。
4. **单键 = 单个可测断言点**，AC-S7-18 的验红面最干净。

**Prompt Cache 核实（回答"是动态尾部还是会污染冻结前缀"）——结论：不污染，且顾虑本身在 planning 侧不成立**：
- planning 的冻结前缀**只有第一条 SystemMessage**：`_build_planning_system_prompt` 忽略 context、直接返回 `_PLANNING_SYSTEM_PROMPT_BODY`（`planning.py:285-291`），`react_base.py:850` 以它单独构成 `initial_messages[0]`。
- `_format_planning_context` 的**全部**产出进第二条 HumanMessage（`react_base.py:851-862`），而该消息**本就携带 `arxiv_id` / `title` / `method_summary` 等论文级动态值**（`_KEEP_META_KEYS`/`_KEEP_ANALYSIS_KEYS`，`planning.py:295-299`）——它整条**本来就是动态区**，跨论文根本不参与共享前缀。新增一个排序键把字节插在 `hyperparams` 与 `method_summary` 之间，**冻结前缀一个字节不变**。
- §13.3 那条"sort_keys 插中间打乱前缀字节"的顾虑，针对的是 coding 侧**同一任务跨修复回合**复用 HumanMessage 前缀的场景；planning 的 `build_context` 在每次子图调用中**只执行一次**（`react_base.py:824`），子图内多轮之间 HumanMessage 不重建，故不存在该问题。
- **唯一残余**：同一篇论文重跑时，HumanMessage 字节因本次代码改动而与历史不同 → 一次性 miss。属"破一次"，与 §16.2 的一次性前缀变更同性质、且量级更小（在动态区）。
- **配套硬纪律（写进实现约束）**：`local_env_facts` 的值必须在 `_map_resource_scout_result` 落 state 时**一次性冻结**，之后所有消费方只读；**planning 不得触发任何探测**（A-S7-11 已定工具只给资源探索）。渲染中**禁止任何非确定性成分**——不写时间戳、不写耗时、不写 uuid。否则 checkpoint 重放 / revise 重入会字节抖动，"破一次"退化成"破每次"。

**渲染样例**（字节确定：命令按首次出现顺序、同命令去重保留最后一次结果、每段输出截断到 `_PROBE_OUTPUT_MAX_CHARS`）：

```
本机环境实测（资源探索阶段真机探测所得，非论文推断）：
$ nvidia-smi -L
GPU 0: NVIDIA A100-SXM4-80GB (UUID: GPU-xxxx)
$ nvcc --version
该命令在本机不可用
$ df -h .
Filesystem      Size  Used Avail Use% Mounted on
/dev/sda1       1.8T  1.2T  520G  70% /data
```

**体量上界**：允许清单 15 条 × `_PROBE_OUTPUT_MAX_CHARS=400` ≈ 6KB 绝对上界（结构性封顶，因为清单外的命令根本跑不了），典型 3~6 条约 1.5KB。与 S7-05 的 `MAX_FIX_LOOP_COUNT × _FIX_NOTE_MAX_CHARS` 双封顶同款结构。

### 15.5 失败与缺席：不区分、不造状态机

| 情形 | `local_env_facts` 表现 | 规划上下文表现 |
|---|---|---|
| 根本没探（agent 没调该工具） | `""` | **键不存在** |
| 探了但全被拒 / 全不可解析 | `""` | **键不存在** |
| 探了、某条命令本机没有（如无 `nvidia-smi`） | 该条渲染为 `该命令在本机不可用` | **有键**，规划看到"本机没有 NVIDIA GPU"这条**有效结论** |
| 探了、某条命令跑了但报错（如有 nvidia-smi 无驱动） | 该条渲染其 stderr（截断） | 同上，是机器在说话，属事实 |
| 渲染过程抛任何异常 | `""`（try/except 兜底） | 键不存在，**不阻断节点** |

**明确裁决：不区分"探了但没结果"与"根本没探"。** 对规划节点而言两者的决策后果**完全相同**——环境未知、按论文/通用假设规划。要区分就得引入第三种状态值 + 消费侧对该值的解释分支，那正是 PRD 提醒的状态机，收益为零。**过程留痕走既有 `search_log` → `analysis_notes` 通道（PRD 明说二者不互斥）**，这恰好把分工钉死：**机器通道只放事实，人通道放过程**（探了几条、哪条被拒、为什么换招）。

**呈现"未知"的形态 = 键不存在**，不造 `"unknown"` / `"N/A"` 哨兵值。沿本函数既有范式（`if user_feedback:` :346、`if pending_repo_url:` :351 都是"为空时不写，保持上下文整洁"）。

**单条渲染规则**（2 分支 + 1 归一，防术语泄漏）：
```
out = stdout_tail.strip() or stderr_tail.strip()
若 out 为空 或 out 以 "subprocess start failed" 开头 → out = "该命令在本机不可用"
渲染 out[:_PROBE_OUTPUT_MAX_CHARS]
```
第二条把 `_run_subprocess` 的内部英文兜底串（`sandbox/local_venv.py:400`）挡在规划上下文之外——因为规划 LLM 写出的 `plan_summary` 是**用户可见**的，任何进它上下文的英文内部串都有被原样引用的风险（沿 AC-S7-19 的精神）。

**降级不阻断（AC-S7-17 零冲突）**：本裁决**不触碰 `resource_info`**，故"mock 探测恒失败 → `resource_info` 与基线一致 / `degraded_nodes` 不含该节点 / `resource_strategy` 不被改写"三条断言与本裁决**互不影响**。

### 15.6 AC-S7-18 逐环验红测试设计（防假解法的机制保证）

**四条断言构成闭链，任一环断裂都有对应断言立刻变红**：

| 环 | 断言 | 若落点退化回 `analysis_notes` 会怎样 |
|---|---|---|
| **① 产出环** | 构造 `react_messages=[ToolMessage(name="probe_environment", content=<真实返回 JSON，含 "A100">)]` 驱动 `_map_resource_scout_result`，断言返回 update 含 `local_env_facts` 且值含 `"A100"` 与 `"nvidia-smi -L"` | **红**（只写 `analysis_notes` 的实现根本不产出该键） |
| **② 送达环（命门）** | 把 ① 的 update 合进 state，调 `_format_planning_context(...)`，断言返回 payload **含 `local_env_facts` 键**且值含 `"A100"` | **红**（`_format_planning_context` 不读 `analysis_notes`，:314-354 已核实） |
| **③ 反证环（负向守门）** | 构造 `analysis_notes` 含 `"A100"` 但 `local_env_facts=""` 的 state，断言 planning payload **不含**该事实、且不含 `local_env_facts` 键 | 把"备注通道到不了规划"这一事实**钉成常驻断言**——它使得任何"改回备注通道"的实现必然同时打红 ② 与 ③，无法靠调 ② 的断言绕过 |
| **④ 端到端环（防接线漏）** | monkeypatch `react_base.create_react_subgraph` 捕获 `initial["messages"][1].content`，`json.loads` 后断言 `local_env_facts` 在其中且含 `"A100"`；并断言 `initial["messages"][0]`（SystemMessage）字节与不带该键时**完全一致** | 防"`_format_planning_context` 改对了、但 `build_context` lambda 忘了传第 6 参"的假绿——这一环验的是**模型真的收到了**。手法有现成先例：`tests/test_sprint5_t25_budget_link.py:404` 已同款读 `initial["messages"][1].content` |

**验红操作（写进测试报告，逐环各断一次）**：
- 注掉 `build_context` lambda 的第 6 实参 → **④ 必红**、①②③ 仍绿（定位到"接线漏"）。**2026-07-29 实测订正**（原文写"②④必红"有误）：② 送达环按本表定义是**直接调 `_format_planning_context(...)` 并自传第 6 实参**，绕过 lambda，逻辑上不可能红；本表 ④ 行"防 `build_context` lambda 忘了传第 6 参"才是这一形态的守门环。**防线未受损**，且四环的分层定位价值正依赖 ②④ 不重叠。详见 dev-plan §31 P-10。
- 注掉 `_map_resource_scout_result` 里的 `local_env_facts` 写入 → **①②④ 必红**（定位到"产出环断"）。
- 把 `_digest_env_probe` 的产出改写进 `analysis_notes`（即复刻假解法）→ **①②④ 必红、③ 绿**——这正是"假解法必须过不了"的直接演示，**建议在测试报告里显式做这一次，作为 AC-S7-18 的交付证据**。

**补充守门（可并入 AC-S7-18 或 AC-S7-21）**：
- **字节幂等**：同一 state 两次 `_digest_env_probe` 字节相同；digest 不含 `duration` / 时间戳 / uuid 子串。
- **工具名单一真相源**：`make_probe_environment_tool(base_dir=...).name == PROBE_TOOL_NAME`，且 `resource_scout` 侧的扫描用的就是这个常量（防工具改名导致白探静默回潮）。
- **术语不泄漏**：digest 不含 `probe_environment` / `resource_scout` / `from_scratch` / `use_repo` / `hybrid` 任一串。
- **三 return 点全覆盖**：`result=None` 与 `result={"error": ...}` 两条降级路径下，只要工具历史有成功探测，`local_env_facts` 仍被写入（防"agent 崩了顺带把机器事实也丢了"）。

### 15.7 落点清单（供 dev-plan）

| 项 | 文件:落点 | 类型 | 说明 |
|---|---|---|---|
| `local_env_facts: str` | `core/state.py` `GlobalState`（接 `last_files_written` 之后） | **state +1 键** | **订正 §14.4 的"state 契约零改动"**；旧 checkpoint `.get` 兜底 |
| `local_env_facts=""` | `core/state.py` `create_initial_state` | 默认值 | 沿 S7-05 先例 |
| `_PROBE_OUTPUT_MAX_CHARS = 400` | `core/nodes/resource_scout.py` 模块级 | 新常量 | 单条输出**渲染**上限（≠ §17 的工具**返回端**字节上限，两者并存不冲突）；沿 `coding.py:78` 范式 |
| `_digest_env_probe(react_messages)` | `core/nodes/resource_scout.py` 新纯函数 | 新 helper | 复用 `_parse_tool_content`（:290）；异常兜底返 `""` |
| 3 个 return 点写 `local_env_facts` | `core/nodes/resource_scout.py` `_map_resource_scout_result`（**:459 / :479 / :549**） | 逻辑 | 建议一个 `_with_env_facts(update, facts)` 收尾 helper。**:549 为主控实测订正值（原稿 :539）** |
| `command` 键 + `PROBE_TOOL_NAME` 导出 | `core/tools/env_probe_tool.py`（本就纯新增） | **§14.3 草图增补** | 规范化回显 `" ".join(argv)`；不改 Q-S7-8 裁决实质 |
| 第 6 形参 + 单键增补 | `core/nodes/planning.py` `_format_planning_context`（:302-354） | +1 参 +2 行 | 尾部带默认值，既有 5 参调用零破坏 |
| lambda 第 6 实参 | `core/nodes/planning.py`（:711-717） | +1 行 | **④ 端到端环守的就是这一行** |
| `ResourceInfo` / `RESOURCE_SCOUT_SCHEMA` | — | **零改动** | 避开 `test_sprint2_b2.py:141-147` 恒等耦合与 planning 3 处重建蒸发 |
| `_RESOURCE_SCOUT_SYSTEM_PROMPT_BODY` 的【输出格式】段 | — | **零改动** | 本条零 LLM 依赖；Q-S7-11 冻结令范围**不因 Q-S7-10 扩大** |
| `run_command_tool.py` / `config.py` / interrupt payload | — | **零改动** | 与 §14 保持一致 |

### 15.8 文件边界与冲突面

- **新进 S7-06 触碰面**：`core/state.py`（此前标零改动，**已订正**）、`core/nodes/planning.py`（PRD §7 已预留"可能触碰，待 Q-S7-10"，**现确认触碰**）。
- **`core/nodes/resource_scout.py` 必须设单收口窗口**：S7-06 批内有三处改动落在该文件——工具装配 5→6（§14.4）、SystemMessage 工具说明与探测节制段落（冻结区，§16.2/§16.3）、本节的 digest helper + 3 处 return 接线。**且与 TODO「其余 16 处同族术语泄漏」余项文件重叠**。**结论：整个 S7-06 批次对 `resource_scout.py` 走一个主控收口窗口，16 处泄漏清理不得同期开工。**
- **`core/nodes/planning.py` 无并行冲突**：S7-01/02/03/05 均已交付且落在 `execution.py` / `coding.py` / `config.py`；本轮不碰这三个文件，**与 §8 的 `execution.py` 单收口窗口零交集**。
- **`core/tools/env_probe_tool.py`** 仍是纯新增、无共享冲突面。

### 15.9 连带断言影响（"只换不弱化"纪律同款核查）

| 断言 | 位置 | 本裁决影响 |
|---|---|---|
| `GlobalState` 字段断言（全为 `in ann` 形态，无精确集合/计数） | `test_sprint3_a2.py:47`、`test_sprint4_a2.py:48`、`test_sprint5_t12_state.py:57/142/249`、`test_sprint7_s705_memory.py:109` | **零影响**（加键不打红） |
| `create_initial_state` 默认值断言（只断个别字段） | `test_sprint1_smoke.py:205-237` | **零影响** |
| `FixLoopRecord` 精确 7 字段冻结 | `test_sprint3_a2.py:146-169` | **零影响**（不碰该结构） |
| `RESOURCE_SCOUT_SCHEMA ≡ ResourceInfo.__annotations__` | `test_sprint2_b2.py:141-147` | **零影响**——**因为选了 GlobalState 单键方案**；若选"进 `resource_info`"则必打红并被迫扩 LLM 输出契约 |
| `RepoInfo` 严格字段 | `test_sprint2_b2.py:428-435`、`:1067`、`:1079` | **零影响** |
| `_map_resource_scout_result` 3 参签名 | `test_sprint2_b2.py:154-156` | **零影响**（签名不变） |
| 工具集 5→6 断言 | `test_sprint2_b2.py:444-467` | 由 §14.4 的工具装配项承担，与本节无关。**注：`test_sprint6_b1_prompt_guards.py:271` 经核实不是断言，见 §16.6** |
| planning context 相关用例（无精确键集断言） | `test_sprint5_t15_planning_prompt.py:47-57` 等 | **零影响**（新增键为可选、空时不写） |

**结论：本裁决的既有断言连带面为零**，新增用例数与增量精确闭合（基线 2044 绿）。

### 15.10 风险登记

| 编号 | 风险 | 缓解 | 回退 |
|---|---|---|---|
| **R-S7-19** | **探测输出撑爆 `TOOL_RESULT_MAX_LENGTH=8000` 后，ToolMessage 不再是合法 JSON，`_parse_tool_content` 返回 `None` ⇒ 该条事实在 digest 里静默缺失** | **已由 §17 主控裁定根治**：在工具**返回端**加 `_PROBE_OUTPUT_MAX_BYTES=2500`，令包装后 JSON 恒 < 8000、永不触发截断。原稿写的"交接给 Q-S7-9"已闭环 | 见 §17.4 |
| R-S7-20 | 规划 LLM 拿到本机事实却不用（PRD 已明说"调不调参交给规划 LLM 自主判断"） | 本条**不设硬约束**，符合 2026-07-28 Maria 新裁的承诺边界；AC-S7-18 只验"送达"，不验"消费方式" | N/A（超出 S7-06 范围，属 backlog 的自动调参项） |
| R-S7-21 | `probe_environment` 工具改名 / 返回结构改动导致 digest 静默失效（白探回潮） | `PROBE_TOOL_NAME` 单一真相源 + §15.6 补充守门断言；AC-S7-18 ④ 端到端环也会红 | 无机制可替单一真相源，评审责任在人 |
| R-S7-22 | digest 体量在极端情形（跑满 15 条清单）挤占规划 context | 结构性封顶 ≈6KB（清单条数 × 400 字符），典型 1.5KB；控量另见 §16.3（探测节制） | 调小 `_PROBE_OUTPUT_MAX_CHARS`（单常量） |
| R-S7-23 | 探测事实与论文 `hardware_requirements` 冲突时规划无所适从（论文要 8×A100、本机 1 卡） | 两者作为并列事实同时进 payload（`hardware_requirements` 已在 `_KEEP_ANALYSIS_KEYS`，:297），digest 段首明写"本机实测…非论文推断"，语义不打架 | N/A（这正是本需求要制造的对照） |
| R-S7-24（**既有、非本条引入，仅留档**） | `_format_planning_context:340` 把 `resource_strategy` 的**内部枚举值**（`from_scratch`/`use_repo`/`hybrid`）送进规划 LLM 上下文；`plan_summary` 是用户可见自由文本且无 humanize 兜底（`ui/pages/plan_review.py:285/337`） → 存在 LLM 原样引用枚举的泄漏面 | **本裁决不扩围处理**，仅登记；本节新增内容全为通俗中文 + 字面 shell 命令，不新增英文枚举 | 归入 TODO「其余 16 处同族术语泄漏」余项一并处置 |

### 15.11 需同步回填的文档欠账（v1.3 已处置状态）

1. ✅ 本文 §14 开头与 §14.4："state 契约零改动" **已订正**为"§14 自身零改动；S7-06 整体 +1 键，见 §15.7"。
2. ✅ `docs/TODO.md`："`config.py` 与 `state.py` 本次零改动" **已订正**。
3. ⬜ `prd.md` §2.6 契约 5 / §6 Q-S7-10：行号 `planning.py:302-307` → `302-354`；枚举补上 `resource_strategy` 与 `pending_repo_url`。
4. ⬜ `prd.md` §5 A-S7-13：补"**已裁：落 `GlobalState.local_env_facts` 单键**"。
5. ⬜ `docs/technical-architecture.md` §7.5：末尾补"探测结论落点 = `GlobalState.local_env_facts` → planning 上下文单键"，保留"代码未交付"状态行。
6. ⬜ `prd.md` §7 S7-06 文件边界：`core/nodes/planning.py` 从"可能触碰"改为**确认触碰**，`core/state.py` **新增触碰**。

---

## 16. S7-06 超时与输出规模 / 冻结令 / 探测节制（Q-S7-9 / Q-S7-11 / Q-S7-12 裁决）

**对应 PRD**：`docs/sprint7/prd.md` v0.5 §6 Q-S7-9 / Q-S7-11 / Q-S7-12；§5 A-S7-12
**日期**：2026-07-28（v1.3）
**本节范围（严格）**：只裁 Q-S7-9 / Q-S7-11 / Q-S7-12 三项。Q-S7-10（探测结论下游落点）见 §15。
**编号说明**：本节由第二位架构师并行独立产出，原稿自编为 §15、风险自编为 R-S7-19~23；因与 §15 撞号，主控统一重排为 **§16 / R-S7-25~29**，内容未改。

> **本节结论先行**：Q-S7-9 = 超时收窄为工具模块内单常量 `_PROBE_TIMEOUT_SECONDS=30`（`config.py` 仍零改动，与 §14.3 清单常量落点一致）；**输出规模原判"沿用、零新常量"已被 §17 主控实测推翻，以 §17 为准**。Q-S7-11 = 冻结令**放行**，且连带面经核实为**零基线作废 / 零真跑复采 / 零 deepxiv 配额**。Q-S7-12 = **prompt 措辞**（不加计数器）+ 探测作**链外补充步**（三步降级链 1/2/3 字节不动）。全节 **state 契约零改动、`config.py` 零改动、`run_command_tool.py` 零改动**，与 §14 已裁两项完全兼容。

### 16.1 Q-S7-9 裁决：超时收窄（一个常量）+ 输出规模（转 §17）

**核实的真实当前值（PRD §6 所引数值均仍准确，但漏了决定性的第三个常量）**

| 常量 | 真实值 | 定义位置 | 对探测是否**实际生效** |
|---|---|---|---|
| `RUN_COMMAND_TIMEOUT` | 120 | `config.py:132` | 生效（`env_probe_tool` 若沿用即为其超时） |
| `SANDBOX_OUTPUT_MAX_BYTES` | 1_048_576 | `config.py:107` | 生效但**永不成为约束**（1MiB 远大于下一行的 8000） |
| **`TOOL_RESULT_MAX_LENGTH`** | **8000** | **`config.py:63`** | **这才是真正的封顶**——`react_base.py:599` 对工具返回串统一施加 `_truncate_tool_result`（`react_base.py:63-74`） |

> **⚠ 本小节的"裁决 1"已被推翻，见 §17。** 原裁决为"**输出规模沿用 `SANDBOX_OUTPUT_MAX_BYTES=1MiB`，不新增任何输出常量**"，论据是"任何 < 1MiB 的探测专用上限都排在 8000 字符截断之后才可能触发，永远轮不到它生效（新常量必然是死代码）"。**该论据只在新上限 > 8000 时成立**；主控实测证明设一个 **< 8000** 的返回端上限会**抢在** `_truncate_tool_result` 之前生效，恰恰是根治手段。完整实测数据与新裁决见 §17。

**本小节仍然成立、且被 §17 采纳的部分**——两级截断方向相反（新增 **R-S7-25**）：
`_truncate_output` 保留**尾部**（`sandbox/local_venv.py:343-355`，"错误栈在末尾"），
`_truncate_tool_result` 保留**头部**（`react_base.py:70-74`）。
`pip list` 输出按字母序，8000 字符约装 200 行；在依赖多的宿主机上，
**`torch` / `torchvision` / `transformers` 这批字母序靠后的包会被静默切掉**——而它们恰是复现最需要知道的那几个。

**最小处置（不动机制、不加参数，只改清单一条）**：把清单内 `pip list` 替换为 `pip list --format=freeze`。同为字母序、同样不联网，但每行由约 40 字符降到约 15~20 字符，同样字符预算下可容纳条目约翻倍。本调整属 §14.1 已明示的"产品可调常量，增删走单点、机制不动"，清单仍为 15 条，形态守门（AC-S7-21）不受影响。**该处置与 §17 的返回端上限叠加使用，二者互补不冲突。**

**裁决 2（成立，未被推翻）：超时不沿用 120s，改为 `env_probe_tool.py` 内的模块级常量 `_PROBE_TIMEOUT_SECONDS: int = 30`。**

沿用的坏处是确定的、收窄的代价是零：

1. **120s 是为另一个用途标定的**：`config.py:132` 与 `run_command_tool.py:13-14` 的注释都写明它是"coding run_command 短超时（机制上防跑重活）"——标的是"跑一段脚本"；探测清单 15 条全是 `--version` / `lscpu` / `df -h .` 这类秒级查询，两者不是同一量级的活。
2. **§14.7 已把"清单内命令偶发挂起最坏 120s"登记为 Q-S7-9 的待处置项**，本节必须给出处置而不是原样接收。挂起是本需求头号命令 `nvidia-smi` 的已知失效形态（驱动/GPU 处于坏状态时可长时间不返回）——**此为外部工程经验，非本仓库可核实事实，如实标注**。
3. **最坏情形收窄 4 倍**：单次挂起从 120s 降到 30s；在模型反复重试的病态路径下，节点上界从 20×120s≈40min 降到 20×30s≈10min。护栏行为不变（`_run_subprocess` 超时杀进程组 `local_venv.py:410-414`，返回 `timed_out=True` 结构化结果，不抛异常）。
4. **30 而非更小**：清单里最慢的合法命令是 `pip list`（冷 FS 上可到秒级十位数）。假超时比慢成功更坏——它会白白吃掉一轮**且拿不到事实**，故留 3~6 倍余量。

**为什么放工具模块内而不是 `config.py`（与 §14.3 保持一致而非打破）**：超时值在这里是**清单语义的直接推论**（"清单里全是秒级命令"），与清单常量同属该工具的语义边界，分处两文件必然漂移——这正是 §14.3 把 `_PROBE_COMMANDS` 放工具模块内的原文理由。附带收益：`config.py` 零改动 ⇒ 不触碰 `tests/test_sprint3_a_boundary.py` / `tests/test_sprint2_a4.py` 一类 config 常量清单断言，回归面为零。

**备选方案对比**

| | A：全沿用（120s / 1MiB） | **B：超时单常量 30s（选定）** | C：两侧都新造探测专用常量 |
|---|---|---|---|
| `config.py` 改动 | 无 | **无** | 有（连带常量清单断言） |
| 单次挂起最坏等待 | 120s | **30s** | 30s |
| 输出侧处置 | 沿用 | 见 §17（返回端上限） | 见 §17 |

### 16.2 Q-S7-11 裁决：冻结令**放行**（一次性前缀版本变更）

#### ① 机制核实：属实，但须精确表述

- **属实的部分**：`react_base.py:520-529` 的 `_bind_llm` 无条件 `llm.bind_tools(list(tools))`，工具定义（名称 + 描述 + args schema）随**每一次** LLM 请求发出；本项目自身的纪律文档已把工具定义算作可缓存前缀的一部分（`docs/technical-architecture.md:923`："system prompt、工具定义、固定 few-shot 放在 message 序列前部"），且 `run_command_tool.py:28-29` 已立下同款成文纪律："工具 docstring 是工具 schema 的一部分，作为稳定前缀参与 Prompt Cache——docstring 内零论文级 / 任务级动态变量"。**工具清单 5→6 改变请求的静态部分，这一点确定成立，且与写不写 SystemMessage 无关。**
- **不可在磁盘核实的部分（如实标注）**：服务端把 tools 渲染在 messages **之前**还是之后，属 provider / 网关内部行为，仓库内无证据。**但该不确定性不影响裁决**：若 tools 在前 → 整条前缀一次性重建；若 tools 在后 → 仅工具段重建、SystemMessage 段照常命中。两种情形都是**一次性**，都不产生持续成本。取保守（前者）估价即可。
- **另需记入的既有事实**：`docs/technical-architecture.md:931` 记 OpenAI 自动型缓存门槛为**前缀 ≥ 1024 tokens**、命中约 5 折。resource_scout 前缀（`resource_scout.py:79-121` + 共享评分段 + 5 份工具 schema）**估算**已越过门槛（未实测，见 ④）。

#### ② 零动态值的文案草案与其防线

**（a）工具描述——单一真相源写法（同时满足 AC-S7-21 的"描述文本与清单常量一致"）**

`langchain_core` 已装版本的 `tool()` 支持 `description=` 且优先级高于 docstring（`.venv/lib/python3.11/site-packages/langchain_core/tools/convert.py:76-88`、:113-120），故描述可由清单常量**渲染**而来，杜绝"清单改了、描述没改"的两份真相：

```python
_PROBE_TOOL_DESCRIPTION = _PROBE_TOOL_DESCRIPTION_TEMPLATE.format(
    commands="\n".join(f"  - {c}" for c in _PROBE_COMMANDS)
)

@tool(description=_PROBE_TOOL_DESCRIPTION)   # description 优先于 docstring，成为送进模型的 schema
def probe_environment(command: str) -> str:
    ...
```

描述正文草案（**全静态**）：

> 在本机运行一条【只读环境探测】命令，用来问清这台机器的真实情况：有没有 GPU、显存与驱动 / CUDA 版本、CPU 与内存、磁盘可用空间、Python 与常用工具链版本、已安装的包。
>
> 只接受下列固定命令中的一条，且必须逐字一致（多一个参数、少一个参数、换一种写法都会被拒绝）：
> （此处由 `_PROBE_COMMANDS` 逐条渲染）
>
> 拒绝的都是同一类原因：本工具只能"查"，不能"改"，也不能"下载"，更不能借解释器执行任意代码。安装 / 卸载 / 删除 / 改配置、任何联网拉取、`python -c` 这类任意代码执行，一律不会被执行。命令在固定的工作目录下运行，工作目录不可指定。
>
> Args:
>     command: 上面清单中的一条命令原文。
> Returns:
>     JSON 字符串 {command, exit_code, stdout_tail, stderr_tail, timed_out, truncated}；被拒绝时返回 {error, exit_code:-1, allowed_commands}，可据此改写后重试。

**"破成每次"的唯一现实路径，以及封堵办法**：工厂签名是 `make_probe_environment_tool(base_dir)`，而 `base_dir` 取 `state["workspace_dir"]`（`core/state.py:232`，可由 `create_initial_state` 覆写）——**任务级动态值**。开发最自然的一个动作就是在描述里写"工作目录为 {base_dir}"，那一刻前缀就变成"每个任务一个样"，功能全对、账单持续渗漏、无人察觉。**因此上文措辞刻意写"工作目录不可指定"而不给出路径**，与 `run_command_tool.py:76` 的既有写法（"工作目录固定为代码输出目录"，同样不插值）同款。守门形态见 §16.5 AC-S7-24。

**（b）SystemMessage 段落草案（`_RESOURCE_SCOUT_SYSTEM_PROMPT_BODY` 内，全静态）**

改动**恰两处**，均在 `resource_scout.py` 自己的字符串字面量内：

*处 1 —— 可用工具清单追加一行（现 `:81-86` 之后）*：

> - probe_environment(command)：在本机跑一条【只读】环境探测命令（只接受固定清单内的整条命令，如 nvidia-smi / nvcc --version / pip list --format=freeze），用来问清这台机器有没有 GPU、CUDA 版本、已装依赖、磁盘余量；只能查，不能装、不能删、不能下载。

*处 2 —— 新增独立段落，插在三步链之后、拼接 `REPO_QUALITY_SCORING_SECTION` 之前（现 `:94` 空行处）*：段落正文见 §16.3②。

**红线（写进 dev-plan）**：新增文案**一个字都不许写进 `core/nodes/_repo_scoring.py`**。`REPO_QUALITY_SCORING_SECTION` 由 planning 与 resource_scout **共享同一对象**（`planning.py:36/210`、`resource_scout.py:22/95`，`tests/test_sprint2_s2_13.py:148-149` 断言 `is` 同一）——改它等于**同时**改掉 planning 的冻结前缀，把"改一个节点"扩大成"改两个节点"。

#### ③ 连带核查：只影响 resource_scout 一个节点，且**零基线作废**

- **其它带工具的 ReAct 节点全部不受影响**：各节点 `get_tools` 相互独立、无共享工具列表——`paper_intake.py:411`、`paper_analysis.py:517`、`planning.py:719-725`、`coding.py:864`、`execution.py:1362`。本次新增的是**纯新增文件**，不改任何被复用的工具模块 ⇒ 其余五个节点的 tool schema 与 system prompt 一字不动。
- **三条 Prompt Cache 基线脚本无一跑 resource_scout**（逐个核实）：`scripts/spike_prompt_cache_baseline.py` 只 import 并连跑 `paper_intake` + `paper_analysis`×3（:60-61、:236）；`scripts/spike_coding_prompt_cache.py`、`scripts/spike_execution_prompt_cache.py` 分别针对 coding / execution。全 `scripts/` 目录内 "resource_scout" 仅出现在 `run_paper.py:13` 的一句注释里。
  ⇒ **sp6 T-S6-5-3 记录的三条 R_baseline（coding 0.9623 / execution 0.8762 / analysis 0.9243，见 `docs/sprint6/test-reports/2026-07-16_t53-real-run-window.md:11-13`）全部继续有效，本次变更不作废任何基线、不需要真跑复采窗口、不消耗 deepxiv 配额。**
  这是本次与 sp6 P-S6-2（同样改 scout 工具 schema）处置不同的关键：sp6 那次复采是为 coding/execution/analysis 三维**重建自身基线**并与 planning 变更合批，并非为 scout 而采。
- **建议不为 scout 新建缓存基线维度**：没有旧基线可比对，新建一条只为看一眼命中率，属为观测而观测，与最小抽象冲突。

#### ④ "破一次"的量化代价

- **受影响请求族**：仅 resource_scout 节点的 LLM 调用（每任务 ≤ ~20 次，`config.py:66` → `resource_scout.py:579` → `react_base.py:621-629`）。
- **净增成本 = 一次缓存创建**。单任务内约 20 次调用共享同一前缀，第 1 次创建、其余复用；跨任务前缀字节一致（AC-S7-20 守门）。自动型缓存本就按闲置期自然过期重建（`docs/technical-architecture.md:916/931`），**前缀版本切换只是提前触发了一次本来也会发生的重建**，不产生任何持续成本。
- **绝对量级未实测**：resource_scout 从未被任何基线脚本采过，仓库内无该节点的命中率数据。如实标注为估算，不编造数字。
- **"破成每次"的代价则完全不同**（故必须靠 AC-S7-24 守门封死）：前缀每任务一版 ⇒ 每任务全部 ~20 次调用中的第 1 次必 miss、且缓存永远无法跨任务复用；功能全对、无任何报错，只有账单在渗漏——这正是 PRD §2.6 判定为 bug 的那一档。

**裁决：放行一次性前缀版本变更。** 条件有三，缺一不可：
(1) 工具描述由 `_PROBE_COMMANDS` 渲染，零插值任务级 / 论文级值；
(2) SystemMessage 新增文案只落在 `resource_scout.py` 自有字面量内，不碰 `_repo_scoring.py`；
(3) AC-S7-20 增加下方"双工厂字节比对"测试点（这条是"破成每次"的**唯一**可执行防线）。

### 16.3 Q-S7-12 裁决：prompt 措辞（不加计数器）+ 探测作链外补充步

#### ① 先纠正一处 PRD 与代码脱节的约束表述

**`MAX_NODE_LLM_CALLS` 在运行期不约束任何东西。** 全仓核实：该常量仅出现在 `config.py:30`（定义）、若干测试的值断言（`tests/test_sprint3_a1.py:138`、`test_sprint2_a4.py:122`、`test_sprint5_t11_config.py:57`）与 sp1 架构文档的设计稿（`docs/sprint1/architecture.md:2079`）——**`core/` 下零消费点**（主控 `grep -rn "MAX_NODE_LLM_CALLS" core/ config.py` 复验：仅 `config.py:30` 一处命中）。故 PRD §6 Q-S7-12 把它与 `REACT_MAX_ROUNDS_RESOURCE_SCOUT` 并列为"节点预算"，属失真表述。

**真实的两道约束是**：
1. **节点轮次硬顶**：`REACT_MAX_ROUNDS_RESOURCE_SCOUT=20`（`config.py:66`）→ `resource_scout.py:579` → `react_base.py:621-629` `budget_check_node`。`round` 仅在 `reasoning_node` 自增（`react_base.py:544`），故 **1 轮 ≈ 1 次 LLM 调用 ≈ 1 条探测命令**。
2. **全局预算（跨节点，本条才是真痛点）**：`retry_budget_remaining` 初值 = `MAX_TOTAL_LLM_CALLS`=240（`core/state.py:363`），**每个 ReAct 节点按实际轮数从同一个池子里扣**（`react_base.py:901-906`）。而修复循环的入口判定读的正是这个池子（`core/nodes/execution.py:2161`，S7-01 已把它下沉为修复分支准入条件）。
   ⇒ **资源探索每多探一条命令，就直接从下游修复循环的预算里扣一格。** 这条因果链是"探测须节制"最硬的理由，比"吃掉本节点轮次"强得多，此前未被记录。

#### ② 裁决：只做 prompt 措辞，**不加机制计数器**

**为什么不加（按纪律，只认实证）**：手上唯一相关实证是 S7-05 真跑 coder 约定遵守率 75%（3/4），但那是**输出格式约定**的不遵守，与"探几条"不同构；本项目**没有**任何"探测无节制"的观测数据（功能尚未交付）。凭这条数据推断计数器必要，属凭感觉。更关键的是**失控代价已被确定性封顶**：轮次硬顶 20 是机制（`react_base.py:621-629`），探测再放飞也不可能突破；最坏情形是 force_finish 兜底出结论。⇒ 现阶段加计数器是为一个尚未观测到的问题预造机制，与最小抽象红线冲突。

**但最坏情形不是无害的，须写进风险**：若探测吃满 20 轮，scout 来不及克隆仓库 → `_map_resource_scout_result` 判定无可用仓库 → 改写 `resource_strategy="from_scratch"` 并把节点计入 `degraded_nodes`（`resource_scout.py:503-510`）。这与 AC-S7-17 的**精神**冲突（探测不得导致从零实现降级），故须有明确的升级触发条件（见 R-S7-27 与 AC-S7-25）。

**措辞草案（即 §16.2(b) 处 2 的段落正文，全静态、零动态值、通俗中文）**：

> 【环境探测（可选补充步，不属于上面的优先级链）】
> - 上面三步是主线；探测只是给结论补事实，不改变三步的顺序与判定，任何探测结果都不构成"找不到仓库"。
> - 只在探测结果会改变你的判断时才探。典型场景：候选仓库要求某个 CUDA 或框架版本、或者需要判断权重与数据能不能在本机落地。
> - 全程最多探 3~5 条，尽量集中在一轮里一次性给出。轮次要留给仓库检索与克隆——轮次耗尽会导致你来不及给出仓库结论。
> - 命令必须与清单逐字一致。被拒绝时不要反复猜写法，看返回里的清单换一条，或者直接放弃探测。
> - 探不到、命令在这台机器上不存在、没有 GPU，都是有效结论；照常继续，不要因此改成从零实现。

**"最多 3~5 条"的定量依据**：主线检索本身需约 4~8 轮（`check_url_reachable` → `git_clone_and_analyze` → 可能的 `web_search` → 收尾 `<result>`）；20 轮预算下留给探测 3~5 轮仍有安全余量，且叠加 fail-closed 写错重试（R-S7-14）后仍不逼近硬顶。

#### ③ 探测放降级链**外**（链内会破坏 §2.6 契约 4）

**先核实降级链的真实实现位置**：三步链**没有任何代码状态机**——它是 `_RESOURCE_SCOUT_SYSTEM_PROMPT_BODY` 里的编号文本（`resource_scout.py:88-93`），配一段模块 docstring 的同款表述（`:4-5`），以及**事后**归一化兜底（`_map_resource_scout_result`，`:435-510`）。因此"链内 vs 链外"完全是**文案位置问题**。

**裁决：链外补充步。** 把探测写成编号第 4 步或插进 1/2/3 之间，都会改动那三行的编号与语义，直接违反 PRD §2.6 契约 4 / 非目标 5，且会牵动既有断言（`tests/test_sprint2_b2.py:474-484` 断言链关键词齐全）。链外段落紧接三步块之后，既保证 1/2/3 字节不动，又让"补充而非主线"的定位一读即明。

### 16.4 落点清单（供 dev-plan，接续 §14.4 与 §15.7）

| 项 | 文件:落点 | 类型 | 说明 |
|---|---|---|---|
| `_PROBE_TIMEOUT_SECONDS: int = 30` | `core/tools/env_probe_tool.py` | 新增常量 | 与清单常量同模块（§14.3 同款理由）；`_run_subprocess(timeout=...)` 传它，**不再传** `config.RUN_COMMAND_TIMEOUT` |
| `_PROBE_OUTPUT_MAX_BYTES: int = 2500` | `core/tools/env_probe_tool.py` | 新增常量 | **由 §17 裁定**（本节原判"沿用、零新常量"已被推翻）；传给 `_run_subprocess(output_max_bytes=...)` |
| `pip list` → `pip list --format=freeze` | `core/tools/env_probe_tool.py` 的 `_PROBE_COMMANDS` | 清单单点调整 | 清单仍 15 条；与 §17 的返回端上限**叠加**使用（R-S7-25） |
| `_PROBE_TOOL_DESCRIPTION` + `@tool(description=...)` | `core/tools/env_probe_tool.py` | 新增 | 由 `_PROBE_COMMANDS` 渲染；**禁止**把 `base_dir` 插进描述 |
| 可用工具清单 +1 行 | `resource_scout.py:81-86` 段末 | prompt 改（冻结区，**本节放行**） | 文案见 §16.2(b) 处 1 |
| 【环境探测（可选补充步…）】段落 | `resource_scout.py:94`（三步链之后、`:95` 拼接点之前） | prompt 改（冻结区，**本节放行**） | 文案见 §16.3② |
| `core/nodes/_repo_scoring.py` | — | **零改动（红线）** | 与 planning 共享同一对象，改它 = 同时改两个节点的冻结前缀，并打红 `tests/test_sprint2_s2_13.py:148-149` |
| `config.py` | — | **零改动** | 超时与输出上限均落工具模块 |
| 既有 5→6 断言 | `tests/test_sprint2_b2.py:444-467` | 断言同步（**唯一真守门**） | 函数名 `..._five_tools`、docstring `:445`、`sorted` 名称列表 `:463-467` 三处一并改；沿"只换不弱化" |
| `tests/test_sprint6_b1_prompt_guards.py:267-273` | 同文件 | **仅文档字符串同步** | **勘误**：该处**不是**断言（见 §16.6） |

### 16.5 AC 建议（接续 AC-S7-22）

| 编号 | 归属 | 验收标准 | 可测方式 |
|---|---|---|---|
| **AC-S7-23**（建议） | S7-06 / Q-S7-9 | 探测超时为独立常量且量级正确：`_PROBE_TIMEOUT_SECONDS == 30`、类型 `int`，且 `_PROBE_TIMEOUT_SECONDS < RUN_COMMAND_TIMEOUT < SANDBOX_EXEC_TIMEOUT`（30 < 120 < 1800）；`config.py` 未新增任何探测相关常量；探测工具**实际把该值**传给底层执行（非只定义不用） | 常量值/类型断言 + 不等式断言 + monkeypatch 底层执行函数捕获 `timeout` 实参断言等于该常量 + `assert not hasattr(config, "PROBE_*")` 类负向断言 |
| **AC-S7-24**（建议，**"破成每次"的唯一防线**） | S7-06 / Q-S7-11 | **工具 schema 零任务级动态值**：用两个**不同** `base_dir` 各造一次探测工具，二者的 `name` / `description` / `args_schema` **字节级一致**；且 `description` 中不出现 `str(config.WORKSPACE_DIR)`、不出现未渲染的 `{` / `}`；`description` 由 `_PROBE_COMMANDS` 渲染（每条清单命令原文均出现在 `description` 中） | 双工厂比对断言 + 子串负向断言 + 清单↔描述逐条一致断言（可与 AC-S7-21 合并落在同一文件） |
| **AC-S7-20 测试点细化** | S7-06 | 既有"跨论文 SystemMessage 主体字节一致"口径不变；**补一条负向**：新增的两处 prompt 文案中不含论文级字段名对应的插值痕迹（无 `{`/`}`、不含 `arxiv`、不含绝对路径） | 在既有 CP-B2-10 用例旁增断言，零新文件 |
| **AC-S7-25**（建议，Q-S7-12 的可证伪出口） | S7-06 | **探测节制可观测**：真机验证跑一次资源探索，从子图 messages 统计 `probe_environment` 的 ToolMessage 条数 ≤ 5，且 `resource_scout` 未因轮次耗尽走 force_finish、未进 `degraded_nodes` | 真机一次跑（工具层不耗 deepxiv 配额；端到端部分合并进既有授权窗口）；统计口径 = `final_state["messages"]` 中 `name == "probe_environment"` 的 ToolMessage 计数。**本条超标即为"prompt 措辞不够"的实证**，届时再加机制计数器（单点、约 4 行闭包计数） |

### 16.6 本节挖出的三处文档失真（建议随 v1.3 一并回填 PRD）

**1（较严重，会导致守门落空）** — PRD §2.6 与本文 §14.4 均称"`tests/test_sprint6_b1_prompt_guards.py:271` 锁资源探索工具集恰 5 个"。**核实为不成立**：`:271` 是 `TestCP154AffectedAssertionsFix` 的**类 docstring** 文字（"工具集由 6 个降为 5 个"），该类的实际断言只查 pwc 相关（`:275-317`）。**新增第 6 个工具不会打红该文件。** 真正会打红的**只有一处**：`tests/test_sprint2_b2.py:444-467`。若开发照文档只改 `:271` 的文字，会误以为"两处守门都在"，实际只剩一处。
> **主控复验（2026-07-28）**：Read `test_sprint6_b1_prompt_guards.py:263-302` 确认——`:267` 为 `class TestCP154AffectedAssertionsFix:`，`:268-273` 为类 docstring，两个测试方法（`:275` / `:293`）分别只断 `"pwc_tools" not in import_text` 与 `"search_pwc" not in body`。**勘误成立。**

**2** — PRD §6 Q-S7-12 把 `MAX_NODE_LLM_CALLS=20` 列为探测要避免吃掉的"节点预算"。该常量在 `core/` 下**零消费点**。真实约束见 §16.3①。
> **主控复验（2026-07-28）**：`grep -rn "MAX_NODE_LLM_CALLS" core/ config.py` 仅命中 `config.py:30` 定义行。**勘误成立。**

**3（顺手发现，不在本次范围）** — `config.py:139` 注释仍写"FLOOR = `REACT_MAX_ROUNDS_EXECUTION`（值 10 不变…）"，而 `:131` 实际值已在 S7 翻倍批改为 20。属注释与代码脱节，建议随下次触碰 `config.py` 时顺手改（本次 S7-06 不碰该文件，故不塞进本批）。

**已复核确认仍准确的**：`resource_scout.py:571-577`（工具集 5）、`planning.py:719-725`、`react_base.py:520-528`（`bind_tools`）、`RUN_COMMAND_TIMEOUT=120`（`config.py:132`）、`SANDBOX_OUTPUT_MAX_BYTES=1MiB`（`config.py:107`）、`REACT_MAX_ROUNDS_RESOURCE_SCOUT=20`（`config.py:66`）、`state.py:232` workspace_dir。

### 16.7 风险登记（接续 §15.10；编号经主控重排，原稿 R-S7-19~23 → R-S7-25~29）

| 编号 | 风险 | 缓解 | 回退 |
|---|---|---|---|
| **R-S7-25** | **两级截断方向相反**：`_run_subprocess` 保尾（`local_venv.py:353`）、`_truncate_tool_result` 保头（`react_base.py:73-74`）。依赖多的宿主机上 `pip list` 被头部截断，**字母序靠后的 torch / transformers 被静默丢弃** | 清单内换用 `pip list --format=freeze`（每行更短，同预算容量约翻倍）+ **§17 的返回端字节上限**（二者叠加）；返回体带 `truncated` 标志，agent 可感知 | 若目标机 pip 不支持 freeze 形态 → 单点加回 `pip list`（机制不动） |
| R-S7-26 | 描述插值失守：开发在工具描述里写 `工作目录为 {base_dir}` 或类似任务级值 → 前缀"破成每次"，**功能全对、账单持续渗漏、零告警** | 措辞草案刻意不给路径（沿 `run_command_tool.py:76` 既有写法）；AC-S7-24 双工厂字节比对断言 | 无机制可替代该断言；断言缺失即防线失守 |
| R-S7-27 | 探测吃满 20 轮 → scout 来不及克隆 → `resource_scout.py:503-510` 改写 `from_scratch` + 进 `degraded_nodes`，与 AC-S7-17 精神冲突 | prompt 明写"最多 3~5 条 + 轮次要留给检索"；轮次硬顶 20 是确定性兜底，不会无限烧 | AC-S7-25 观测超标 → 加闭包计数器（约 4 行：`nonlocal` 计数 + 一条 if，返回结构化"探测次数已用尽"）。工厂每次节点调用重建（`react_base.py:826`），计数天然按次任务重置 |
| R-S7-28 | 探测轮次从**全局** `retry_budget_remaining`（240）扣（`react_base.py:901-906`），与下游修复循环共用同一池子（`execution.py:2161`）——探测挥霍会缩小修复循环余量 | 3~5 条上界下净增 ≤ 5/240 ≈ 2%，可忽略；预算已在 §2.1 翻倍 | 若 AC-S7-25 观测到显著挥霍，同 R-S7-27 处置 |
| R-S7-29 | `nvidia-smi` 挂起（驱动/GPU 处于坏状态）——**外部工程经验，仓库内无证据，如实标注** | 超时收窄至 30s + 杀进程组（`local_venv.py:410-414`）+ 结构化 `timed_out` 返回不炸子图 | 若真机观测到 30s 仍被误杀，单点上调该常量 |

---

## 17. 主控跨节合并裁定：探测输出上限（收口 §15 R-S7-19 与 §16.1 的冲突）

**日期**：2026-07-28（v1.3）
**性质**：§15 与 §16 由两位架构师**并行独立**产出，各自只看自己那半边问题。主控合并时发现二者在"探测输出要不要设上限"上**结论相反**，且**两边的论据各有一处不成立**。本节以实测为准收口，**推翻 §16.1 的裁决 1**。
**为什么必须收口而不能各留各的**：这不是文风分歧——两条路子一条会让 S7-06 做成"探到了但静默丢失"，另一条能根治。留着不裁，开发会照 §16.1 实现，缺陷直接进代码。

### 17.1 冲突陈述

| | §15（Q-S7-10，落点） | §16.1（Q-S7-9，输出规模） |
|---|---|---|
| 立场 | 登记 **R-S7-19**：若不收窄输出，长输出经截断后 JSON 不可解析，该条事实在 digest 里**静默缺失**；明确"交接给 Q-S7-9 处置" | **裁决 1：输出沿用 1MiB、零新常量**。论据："任何 < 1MiB 的探测专用上限都排在 8000 字符截断之后才触发，**永远轮不到它生效**（新常量必然是死代码）" |
| 各自的漏洞 | 称 `_parse_tool_content` "**已能容忍**截断后缀" —— **过于乐观**（见 17.2） | "永远轮不到生效"**只在新上限 > 8000 时成立** —— 设一个 **< 8000** 的上限恰恰是**抢在**它之前生效（见 17.2 对照组） |

### 17.2 实测证据（主控亲跑，非推断）

用 `.venv/bin/python` 走真实代码路径：构造一条 600 行的 `pip list --format=freeze` 输出，按 §15.3(c) 的 6 键结构包成 JSON，依次过 `react_base._truncate_tool_result` 与 `resource_scout._parse_tool_content`。

| 组别 | 工具返回端处理 | JSON 长度 | 经 8000 截断后 | `_parse_tool_content` 结果 |
|---|---|---|---|---|
| **实验组** | 无上限（= §16.1 裁决 1） | **16111 字符** | 截到 8000，尾部为 `...==1.2.299\n... [truncated at 8000 chars]` | **`None` ← 整条静默丢失** |
| **对照组** | 返回端先压到 2500 字节 | **2737 字符** | **未触发截断** | **解析成功**，6 键齐全 |

**两条论据的裁定**：
- §15 的"已能容忍截断后缀"**不成立**。`_parse_tool_content`（`resource_scout.py:309-317`）确实会 `text.rfind("... [truncated at")` 剥掉后缀再 `json.loads`，**但剥完剩下的是缺闭合括号的残缺 JSON，`json.loads` 照样抛错、返回 `None`**。它容忍的是**后缀那行字**，不是残缺 JSON 本体。项目里唯一能修残缺 JSON 的 `_repair_truncated_json_prefix`（sp1 BUG-S1-02 为同类问题所加，在 `react_base.py`）**并未被 `_parse_tool_content` 复用**。
- §16.1 的"新常量必然是死代码"**不成立**。它把"上限"默认理解成"更大的上限"，因而推出"排在 8000 之后"；但本场景需要的恰是**更小**的上限，它会**先于** 8000 生效，使截断根本不发生。

**失效形态的严重性**：不是"丢一部分输出"，是**整条探测结果消失**，且全程无异常、无日志、无红。这正是 S7-06 立项要防的"白探"，只不过是更难查的静默版——AC-S7-18 的四环断言全用短输出构造，**也不会打红**。

### 17.3 裁定

**在 `core/tools/env_probe_tool.py` 增加返回端输出上限常量 `_PROBE_OUTPUT_MAX_BYTES: int = 2500`，传给 `_run_subprocess(output_max_bytes=...)`，不再传 `config.SANDBOX_OUTPUT_MAX_BYTES`。**

- **它不是死代码**：2500 < 8000，必然先于 `_truncate_tool_result` 生效，令包装后 JSON 恒 < 8000 ⇒ `_parse_tool_content` **永不失败** ⇒ digest 永不静默丢失。
- **取值依据**：`output_max_bytes` 对 stdout / stderr **各自**生效，最坏两路满载 = 5000 字节；叠加 JSON 转义（换行 `\n`→`\\n`，`pip list --format=freeze` 换行占比约 1/16，膨胀 ≈6%）与其余 4 键开销（实测 ≈237 字符），最坏约 5.6k 字符，距 8000 仍有约 30% 余量。
- **与既有裁决的关系**：`config.py` 仍**零改动**（常量落工具模块，与 §14.3 清单常量、§16.1 超时常量同址，三者同属该工具的语义边界）；§16.1 的 `pip list --format=freeze` 调整**保留**——它管"同样预算装下更多条目"，本裁定管"预算本身不许溢出"，二者**互补**：前者提高信息密度，后者保证不丢整条。
- **与 §15 的关系**：`_PROBE_OUTPUT_MAX_CHARS=400`（§15.7）是 **digest 渲染端**的单条上限，本常量是**工具返回端**的字节上限，**两者并存、职责不同**，不合并（合并会让"给模型看的"和"给规划看的"两个上限互相绑架）。

### 17.4 落点与守门

| 项 | 文件:落点 | 类型 | 说明 |
|---|---|---|---|
| `_PROBE_OUTPUT_MAX_BYTES: int = 2500` | `core/tools/env_probe_tool.py` 模块级 | 新增常量 | 传 `_run_subprocess(output_max_bytes=...)`；**不传** `config.SANDBOX_OUTPUT_MAX_BYTES` |
| **AC-S7-26（建议新增，本裁定的唯一守门）** | 测试 | 验收标准 | **探测工具返回的 JSON 字符串长度恒 < `TOOL_RESULT_MAX_LENGTH`**：mock 底层执行返回一个撑满 `_PROBE_OUTPUT_MAX_BYTES` 的 stdout **且** 同样撑满的 stderr（最坏两路满载），断言 `len(tool_return) < config.TOOL_RESULT_MAX_LENGTH`；再把该返回串依次过 `_truncate_tool_result` 与 `_parse_tool_content`，断言**解析成功且 6 键齐全**（即 17.2 对照组的固化） |

**为什么这条守门不可省**：它是唯一能在"有人把 `_PROBE_OUTPUT_MAX_BYTES` 调大到 8000 以上"或"给返回结构再加一个大字段"时立刻打红的断言。没有它，本裁定退化为一句注释，而失效形态是**静默**的——没有任何其它测试会发现。

### 17.5 方法论留痕（为什么并行独立裁决仍需主控收口）

两位架构师**各自的核实都没有偷懒**：§15 读了 `_parse_tool_content` 的源码并正确指出它有"剥截断后缀"的分支；§16 读了 `_truncate_tool_result` 与 `_truncate_output` 并正确指出两级截断方向相反。**问题出在结合部**——
- §15 看到"有剥后缀的分支"就判定"能容忍"，**没有真跑一次**验证剥完之后还能不能解析；
- §16 在推理"新常量是不是死代码"时，**只考虑了上限变大的情形**，因为它那半边问题的语境是"要不要为探测放宽/新造参数体系"。

**教训（写进后续流程）**：并行独立裁决能防止互相污染、拿到两个真正独立的视角，但**跨边界的失效形态天然落在两人的盲区交集里**。主控合并时不能只做"编号重排 + 拼接"，必须**主动找两份产出互相引用的那几个点**（本例即 §15 的"交接给 Q-S7-9"这句话），并对结合部**实跑验证**而非读码推断。本次若只做拼接，缺陷会原样进 dev-plan。

---

## 18. S7-08 planning 平台感知规划（Q-S7-13 / Q-S7-14 / Q-S7-15 裁决）

> **对应 PRD**：`docs/sprint7/prd.md` §10（S7-08 v1.0，2026-07-29）。架构 v1.3 → **v1.4**。
> **裁决日期**：2026-07-29，架构师代理产出、主控逐条上磁盘核实（含对主控自身给错行号的纠正）。

### 18.1 Q-S7-13 计划新子键：落点与形态

| # | 裁决点 | 结论 | 依据 |
|---|---|---|---|
| 1 | 落点层级 | `ReproductionPlan` **顶层两个扁平键**：`scale_reduced: bool` + `local_fit_note: str` | 最小抽象；不新增 `GlobalState` 顶层键 |
| 2 | 是否进 LLM 输出契约 | **进**（schema properties +2、prompt【输出格式】JSON 示例 +2 键） | 判断的唯一持有者是模型（PRD §10.5 三条理由），**无确定性提取通道**——这正是与 Q-S7-10 的分水岭 |
| 3 | 是否进 `required` | **不进** | `core/react_base.py:697-705` `finalize_node` 对 required 缺失会再跑一次 `with_structured_output` = **多烧一次 LLM 调用**；而漏写时缺省 `False` 已是安全值 |
| 4 | 缺省 | `scale_reduced=False`、`local_fit_note=""`；缺键 ≡ False/""；下游一律 `.get()` | 旧 checkpoint 兼容；"没缩规模"是安全默认 |
| 5 | 三处重建路径会不会丢 | **不会；且 PRD §10.10 Q-S7-13 里"`planning.py:455/565/778`"三处指错了对象** | 见 18.1.1 |
| 6 | 与 Q-S7-10 是否同构 | **不同构，结论不可复用**；可复用的只有"缺省安全值 + 防御读 + 不造哨兵值"那半边 | 见 18.1.1 |
| 7 | 标注串命名 | reporting 第 4 条标注值 **复用同名 `scale_reduced`** | 与 plan 键 1:1，省掉一张映射表 |

#### 18.1.1 重建路径核实（本问核心；主控原怀疑方向对、坐标错）

逐点核实的真实拓扑：

- **plan 的显式 kwargs 构造点只有 2 处**：`core/nodes/planning.py:384`（`_build_reproduction_plan`）与 `:589`（`_minimal_plan`）。**这两处不改就必丢**（LLM 产出的键不显式取就被丢弃）。
- **plan 的复制点 2 处，全键透传、零改动安全**：`planning.py:806` `plan = dict(out.get("reproduction_plan") or {})`；`core/nodes/execution.py:2076` `{**(state.get("reproduction_plan") or {}), "approved": False}`。
- **主控点名的 `planning.py:455/565/778` 重建的是 `ResourceInfo`**（真实构造行为 `:461` / `:571` / `:785`），与 plan 新键无关——那正是 Q-S7-10 当年踩坑的对象。
- **revise / switch_repo self-loop 根本不携带旧 plan**：这两个分支 return 的 dict 里没有 `reproduction_plan` 键（`tests/test_sprint2_b3.py:210/224/247/286/557` 五处已断言 `"reproduction_plan" not in out`，主控核实）⇒ **不存在"合并路径静默丢键"这一形态**，只存在"模型这轮没输出 → 回落缺省 False"。

**与 Q-S7-10 的关系（不同构，务必别照抄）**：Q-S7-10 是**跨节点写-读**（resource_scout 写 `ResourceInfo`，planning 三处显式重建抹掉），本次是**同节点内构造**。故 Q-S7-10 的结论（改走确定性提取、不进 LLM 输出契约）**不可复用**——`local_env_facts` 来源是工具历史（确定性可提取），而"缩没缩规模"是**判断**，规则拿不到分子。

**新增机制性防线（把这类风险一次性关死，Q-S7-10 当年没有）**：

```
断言 set(ReproductionPlan.__annotations__)
   == set(_build_reproduction_plan({}, state).keys())
   == set(_minimal_plan(state, "x").keys())
```

三方键集合相等。以后任何人"加键只改一处"当场红。**已写进 AC-S7-35 的判定方式**。

#### 18.1.2 文件级落点

| 文件 | 改动 |
|---|---|
| `core/state.py:115-137` | `ReproductionPlan` +2 键 + 缺省语义注释 |
| `core/nodes/planning.py:67-118` | `REPRODUCTION_PLAN_SCHEMA.properties` +2；`required` 不动 |
| `core/nodes/planning.py:384` | `_build_reproduction_plan` +2 kwargs；新增 `_coerce_bool`（宽松：`True`/`"true"`/`"是"`/`1` → True；**`"false"` 必须判 False**） |
| `core/nodes/planning.py:589` | `_minimal_plan` +2 缺省（`False` / `""`）——降级路径不得冒充"已做本机适配" |
| `core/nodes/planning.py:141-210` | **冻结区静态改写**：替换 `:151-152` 那条无条件"引用论文 hardware_requirements"、加三级优先级 + 禁编造 + 两键契约 + 缩法举例（A-S7-19）；**【输出格式】JSON 示例必须同步 +2 键**（不改则模型不知道要输出） |
| `core/nodes/planning.py:877-890` | interrupt payload +1 键 `local_env_facts`（既有 10 键一字不动） |
| `core/nodes/reporting.py:253-273` | annotations **末尾**追加第 4 条（保持既有三条顺序 ⇒ 假时零扰动）；`plan` 变量当前在 `:273` 才取，**需上移** |
| `core/nodes/reporting.py:536-619` | 声明块 +第 4 段（静态中文常量，受 §18.2 新守门覆盖） |
| `core/nodes/coding.py:428-436` / `execution.py` 对应处 | 沿 `_CREDENTIAL_DEGRADATIONS_DIRECTIVE` 范式（`coding.py:82-86` / `execution.py:98-103`）：**非空才注入**；两侧各自模块常量 + 新增"两常量字节相等"断言防漂移 |
| `ui/term_map.py:82-85` | +`"annotation:scale_reduced": "缩小规模复现"` |
| `ui/pages/plan_review.py` | 新增只读展示块（本机实测原文 + 适配说明 + 预计占用）；`local_fit_note` 为空时用静态兜底句 |

**顺带确认的好消息**：`ui/pages/result_report.py:59` 直接 import 复用 `_determine_conclusion` ⇒ **UI 结论卡片自动跟随降档，零改动**。

### 18.2 Q-S7-14 术语守门：新写独立守门，**不扩 `_GUARDED_MODULES`**

**决定性论据不是取舍，是扫描面错配**：现有守门 `tests/test_e2e2_message_guard.py:85-129` 扫的是 `make_node_error(...)` **第 3 实参的字面量**。而本次新增的用户可见文案**一条都不在这个面上**——reporting 声明块是 markdown `lines.append(...)`、UI 是 `st.markdown` 字面量、term_map 是表值。所以扩围会同时导致：①**扫不到本次新增文案**（产品红线直接落空）；②**连带打红既有文案**，与 TODO 登记的"其余 16 处不得同期开工"正面冲突。两头不讨好 ⇒ 否。

**方案 A 形态**——新文件 `tests/test_s708_user_text_guard.py`：

1. **复用不复制黑名单**：`from tests.test_e2e2_message_guard import _BLACKLIST, _hits`（`tests/__init__.py` 已存在，跨模块 import 可行——已核实）。本次新增词单独放 `_S708_EXTRA`（`scale_reduced` / `local_fit_note` / `local_env_facts` / `probe_environment` / `code_only` …），**不改共享 `_BLACKLIST`**，避免连带影响 resource_scout 既有扫描面。
2. **三个扫描源，全部"数据源全量"而非抽样**：
   - `ui/term_map.py::TERM_LABELS.values()` —— 全量扫值（key 天然是内部枚举，只能扫值）。顺带把既有 50 条纳入守门，已逐条目测清白，**零连带打红风险**。
   - reporting / plan_review / 讨论助手边界语的新增文案 —— **要求全部提为模块级具名常量**，守门按名 import。
   - coding/execution 的 `_SCALE_REDUCED_DIRECTIVE` 是给模型看的，**不入用户文案守门**，只入"两侧字节相等"断言。
3. **"扫不到必报红"三重机制**（对准 S7-06 那次"扫 0 条却 passed"）：
   - 按名 import ⇒ 常量删除/改名 → `AttributeError` → **红**（不是 skip、不是 0 条 passed）；
   - `assert scanned == EXPECTED_N`（硬编码期望条数）⇒ **少扫一条即红**；
   - 每条 `assert literal.strip()` ⇒ 常量被清空成 `""` 不能蒙混。
4. **与 TODO 零冲突**：不碰 `_GUARDED_MODULES`，那 16 处保持原状，日后清理路径完全不变。

**备选 B**（扩围 + 既有违规基线豁免表）：仍然扫错面、基线表会腐坏、且是新抽象 ⇒ 否。
**备选 C**（全模块字面量扫描）：`term_map` 的 key、`humanize(...)` 的 domain 名都是合法字面量 ⇒ 大面积假阳 ⇒ 否。

### 18.3 Q-S7-15 探测摘要上限：400 → 2600，并新增总长上限 8000

#### 18.3.1 值是推导出来的，不是拍的

核实到一条 PRD 未记的机制事实：`sandbox/local_venv.py:353` 返回端截断是 **`raw[-max_bytes:]` 保留尾部**（注释原文"错误信息通常在末尾"）+ 42 字符 marker 行；而 digest 端 `resource_scout.py:496` 是 **`out[:cap]` 保留头部**。

⇒ **两级截断方向相反。** `env_probe_tool.py:72-74` 注释里 R-S7-25 记的正是这件事，但当时只在返回端处置了一半（改用 `--format=freeze` 让条目翻倍），**渲染端没动**。于是返回端刻意"保尾"把 `torch`/`transformers` 留下来，又被渲染端"取头"原样作废。

> **结构性原则（新立，写进架构正表）：外层上限必须 ≥ 内层上限，否则内层的截断方向选择被外层作废。**

- 返回端 2500 **字节**；UTF-8 下字符数 ≤ 字节数，加 42 字符 marker ⇒ 硬上界 2542 字符 ⇒ 取 **2600**（留余量给 `mask_value` 替换后的长度浮动）。
- 这样 AC-S7-42 从"调大点碰运气"变成**结构上必然成立**。
- **反过来说：调到 800 / 1200 这类中间值是错的**——仍低于 2500，`torch` 进不进 digest 取决于该机 venv 包数，用例会退化成运气测试。

#### 18.3.2 为什么必须再加一个总长常量（这不是多一层抽象）

- 现结构性上界 = 15（清单条数）× 400 ≈ 6.2KB，既有断言 `tests/test_sprint7_s706_env_facts.py:492` 就是这么写的。单条抬到 2600 后变 15 × 2600 ≈ **39KB，过松**。
- **决定性理由：S7-09 一旦放开白名单，"清单条数 = 15"这个分母直接消失，结构性上界不复存在。** 显式总长常量既是 AC-S7-42 的答案，也是 **S7-09 的前置防波堤**。
- 取 `_PROBE_DIGEST_MAX_CHARS = 8000` 字符：6 项必探维度典型合计约 5.2KB（`nvidia-smi` 满输出 ~2KB + pip freeze 2.5KB + 其余四项 <0.7KB），留 ~50% 余量，正常路径不咬。
- 截断方式：整份渲染完**按总上界截尾** + 末尾追加一行中文说明（"环境探测摘要过长，后续内容已省略"）——**不静默**。截尾而非截头：抬头行与前几条必探维度更重要。

#### 18.3.3 token 代价界定（R-S7-37 的答案）

digest 经 `_format_planning_context` 进 HumanMessage，位于 planning ReAct 会话的**稳定前缀**（System+Human 在 ≤16 轮内不变）⇒ 首调全价、其后走 Prompt Cache 命中价。以 8000 字符硬顶计，最坏 **≈2.2K token / 每次进 planning**（每次 revise 重入再算一次）。写进 dev-plan 并在 AC-S7-43 真跑时用 LangSmith 核对实际值。

#### 18.3.4 断言同步点（已核实，2 处）

`tests/test_sprint7_s706_env_facts.py:472-492`：
- `:490` 逐行 `len(line) <= max(cap, 60)` —— cap 变大后仍成立但**退化成几乎不可能失败**，应改为对"单条命令块整体"断言；
- `:492` 结构性上界断言必须换成新的总长常量断言；
- 用例内 `"X" * (cap * 3)` × 15 条会触发新的总长截断 ⇒ **用例语义需一并更新**。

### 18.4 两条冻结区定性：均背书，但各带一条修正

#### (1) planning 冻结令语义：**推理成立，但理由要换更硬的**

PRD §10.8 援引 `planning.py:139-140` 的注释属"**拿文档证明文档**"。真正的证据是**守门断言本身**：`tests/test_sprint2_b3.py:315-322` CP-B3-10 断的是 `_build_planning_system_prompt(不同论文上下文) == 同一 body` + 主体无论文级动态值。**这条断言在"静态改写主体"时不会红，只在"注入动态值"时红** ⇒ 冻结令的可执行语义确实是"跨论文字节一致"，判 bug 标准确实是"是否引入论文级/任务级动态值"。**PM 推理成立，本次属合法的一次性静态变更。**

> **⚠️ R-S7-41（新登记）：背书过程中挖出一条 PRD 未登记的假绿守门，就压在本次要改的那段前缀上。**
>
> `tests/test_sprint6_b1_prompt_guards.py:56-74` `test_planning_prompt_body_byte_snapshot`，第 69 行：
> ```python
> EXPECTED_HASH = actual_hash  # 首次运行自锁定当前值
> ```
> 随后 `assert actual_hash == EXPECTED_HASH`。**这是 `x == x`，恒真，永远不可能红**（主控已上磁盘核实原文）。而其 docstring 自称"若后续批次意外改动主体前缀，此断言报红（字节级回归门）"——**实际零守门能力**，与 S7-06"扫 0 条却 passed"同族。
>
> **本次必须一并处置**：改完 prompt 后把哈希**写死为真实值**、在 dev-plan 留档基线。不处置的话，"planning 主体的静态变更必须是有意为之"这条纪律**在机制上根本不存在**。

#### (2) resource_scout 第三次改动：放行成立，但"破一次"这个说法本次已不准确

"破一次"原本指 **Prompt Cache 前缀的一次性静态变更**，代价不是"次数"而是"每次静态变更让历史前缀缓存作废一次"。三次改动 = 三次冷启动，线性叠加但仍是**常数级**，不会退化成"破每次"（那要求前缀含动态值）。故放行成立。真正的守门仍是 `test_sprint6_b1_prompt_guards.py:295` 跨论文一致 + AC-S7-27 负向断言。

> **节制建议（钉死触发条件，把 R-S7-35 回退栏的模糊表述变成硬触发）**：三个 Sprint 内同一段落改三次，说明该段落缺一条稳定验收锚——每次都是"真跑发现没照做 → 改措辞"。**若 AC-S7-43 真跑后仍需第四次改这段，就不再改措辞，直接回头找 Maria 重议手段。**

### 18.5 对 PRD 三条 AC 口径的修正（架构侧认为原口径工程上不成立）

1. **AC-S7-36「两版计划执行步骤规模参数出现差异」在 mock 层不可证伪**——用 mock LLM 就得预设两份不同的假输出 ⇒ 断言的是 mock 自己，**纯自证**。**改判定口径**：mock 层只断言"两组本机事实产生**不同的 HumanMessage**"（确定性可测），把"计划规模真的变了"整体交给 AC-S7-43 真跑。否则这条会成为一条看起来很硬、实际什么都没证的绿。
2. **AC-S7-41「覆盖 6 项必探维度」的判定口径必须钉死为"digest 中存在该命令的记录"**，而非"出现该维度的数值"。否则本机缺 `free` 时 digest 只会写"该命令在本机不可用"，AC **永远不过且无法修**（红线 3 已冻结 `env_probe_tool.py`）。
3. **AC-S7-42 的构造用例要说清是否走真实工具**。若绕过工具直接造 ToolMessage，就绕过了返回端 2500 字节尾部截断——那测的是渲染端单独行为。**两条都该测**：绕过的验渲染端上限，走工具的验两级截断方向合成后 `torch` 仍在。

### 18.6 风险增补（R-S7-41 ~ R-S7-43）

| 编号 | 风险 | 缓解 | 回退 |
|---|---|---|---|
| **R-S7-41** | `test_planning_prompt_body_byte_snapshot` 是恒真断言（`EXPECTED_HASH = actual_hash`），planning 冻结区的"字节回归门"实际不存在 | 本次改完 prompt 后写死真实哈希 + dev-plan 留档基线 | **无**——不修则该守门永久为零 |
| **R-S7-42** | 总长上限 8000 在多卡机（`nvidia-smi` 输出随卡数线性增长）+ 大 venv 下可能咬到最后一条探测记录 | 截尾时追加显式中文说明（**不静默**）；真跑核对实际长度 | 单点调值 |
| **R-S7-43** | interrupt payload 增 `local_env_facts`（≤8KB）后每次 revise 都会把它再存一份进 checkpoint | 已知增量，备案；payload 指纹（`app.py:479-488`）随之变化属正常语义 | 改为只放截断摘要 |

### 18.7 验证方式（收口清单）

1. **键集合三方相等**断言（§18.1.1 的机制性防线）；
2. `local_env_facts` 值**不出现在 system prompt** 的负向断言（AC-S7-34，R-S7-38 的唯一防线）；
3. 新守门的"**删常量必红 / 少扫一条必红 / 常量清空必红**"三重自证 —— 这三条本身要在开发时**逐条验红**，否则又是一次 S7-06；
4. `_PROBE_OUTPUT_MAX_CHARS ≥ _PROBE_OUTPUT_MAX_BYTES` 的**关系断言**（比断言字面量 2600 更抗腐坏，S7-09 改返回端时自动跟随）；
5. `_SCALE_REDUCED_DIRECTIVE` 两侧**字节相等**断言；
6. 标记为假时：coding/execution HumanMessage + reporting 报告**与 sp5 基线字节一致**（零扰动正负两向）。

### 18.8 移交产品侧的三条缺口（架构不代决）

> **⟦2026-07-29 Maria 当日全部拍板，三条均按架构倾向裁定，详见 PRD §10.9 第 8/9/10 行⟧**
> ①**只产代码路径要带缩规模声明**（红线 6 在该路径上同样成立）；②**讨论助手要能看到本机事实**（`_format_plan_context` 加第 4 键）；③**接受动态文案的守门残留**，不为此松开红线 4——静态文案 100% 覆盖，动态那段只靠 prompt 契约 + 真跑人眼，泄漏后果仅为一个英文字段名出现在中文里。

原始移交内容如下：

1. **场景 B 的 (b) 分支（只产代码）报告要不要带缩规模声明？** PRD §10.4 没说 code_only 形态的报告要不要声明"这份代码是按缩小规模写的"。工程上 `_determine_conclusion` 的 annotations 与 execution 无关，但报告三形态里 code_only 是否渲染声明块需另定。**直接关系到红线 6「缩规模必须诚实」在 (b) 路径上成不成立。**
2. **`ui/pages/plan_review.py:133-147` `_format_plan_context` 只喂讨论助手 3 个键，不含 `local_env_facts`。** 用户讨论"换一种缩法"时助手看不到本机事实，可能建议这台机器跑不动的方案。加 1 键成本极低，但改的是 UI 侧 LLM 上下文范围，属产品体验决策。
3. **AC-S7-40 的红线在动态文案上物理不可达。** 三项静态文案方案 A 100% 覆盖；但**用户在审核页看到的最主要那段字（模型生成的 `local_fit_note`）是运行时产物，任何静态守门都扫不到**；而红线 4（不加 gate）与红线 7（不留痕）同时排除了运行时拦截与运行时标记 ⇒ 动态文案的唯一防线是 prompt 契约（`_PLANNING_TERMINOLOGY_SECTION`，实测服从率 75%）+ AC-S7-43 真跑人眼。**这不是能靠工程补上的洞。** 附带泄漏渠道：`_format_plan_context` 会把整份 plan（含 `scale_reduced` 等英文键名）dump 进讨论助手 system prompt，助手可能在中文回复里复述字段名——缓解办法是在 `_build_chat_system_prompt` 边界语补一句"不要复述字段名/英文标识"（该句是新增静态文案，会被新守门覆盖）。

---

*（v1.3 增补完：§15 = Q-S7-10 探测结论下游落点——落 `GlobalState.local_env_facts` 单键、确定性从工具历史提取（零 LLM 依赖）、经 `_format_planning_context` 第 6 形参送达规划；不进 `resource_info`，因其与 `RESOURCE_SCOUT_SCHEMA` 集合恒等（加字段=降格为 75% 遵守率的 LLM 产物）且在 planning 侧有 3 处整体重建（revise/switch_repo 会静默丢失）。§16 = Q-S7-9 超时收窄 `_PROBE_TIMEOUT_SECONDS=30` + Q-S7-11 冻结令放行「破一次」（连带面经核实为零基线作废/零复采/零配额）+ Q-S7-12 只做 prompt 措辞不加计数器、探测作链外补充步。§17 = 主控实测收口两节冲突，**推翻 §16.1 裁决 1**，加返回端 `_PROBE_OUTPUT_MAX_BYTES=2500` 根治"长输出致整条探测结果静默丢失"。三节合计：state +1 键、`config.py` 零改动、`run_command_tool.py` 零改动、`_repo_scoring.py` 零改动（红线）；建议新增 AC-S7-23~26。**S7-06 设计侧六问全部收口，可转 dev-plan**——开发批次待 Maria 确认批次边界后启动。）*

---

## 19. S7-10 计划与编码/执行的落点对齐（Q-S7-17 ~ Q-S7-20 四问裁决 + Q-S7-21 ~ Q-S7-24 标定收口）

> **补落章说明**：本章的 Q-S7-17~20 四问由架构师代理于 2026-07-31 裁决，此前**只记在 `dev-plan.md` §41.2、未落本文件**（PRD §12.12 已登记该缺口）。本次补齐。
> **⚠ 编号勘误（务必先读）**：dev-plan §41.2 把这四问暂记为 **Q-S7-16 ~ Q-S7-19**，但 **`Q-S7-16` 已在 PRD §10.10 被占用并随 Maria「连留痕也不要」的裁决撤销**。复用一个已撤销的编号会让后人分不清指的是哪件事 ⇒ **本文件落章统一改用 Q-S7-17 ~ Q-S7-20**，`Q-S7-16` 保留「已撤销」状态。
> **Q-S7-21 ~ Q-S7-24** 是 2026-07-31 开发实施 T-S7-6-1 时**实测推翻了 Q-S7-17(a) 的一条设计假设**后，架构师当场追加的裁决，**内容不与四问重复**。

### 19.0 编号映射表（dev-plan 旧号 → 本章新号）

| dev-plan §41.2 旧号 | 本章新号 | 一句话内容 |
|---|---|---|
| Q-S7-16(a) | **Q-S7-17** | 约束 C 的**可判定界线**：走「内容来源」不走「文件类型」 |
| Q-S7-16(b) | **Q-S7-18** | 约束 C 的**实现层次**：工具层硬拦截为主 + prompt 同批收窄，**不接受 prompt-only** |
| Q-S7-17 | **Q-S7-19** | **计划期确定性告警** W4/W5 + `core/plan_checks.py` 零改动红线**不延伸** |
| Q-S7-18 | **Q-S7-20(a)** | execution prompt **字节基线守门本批补上**（先建后改） |
| Q-S7-19 | **Q-S7-20(b)** | **共享克隆缓存污染**：仓库不变量口径 + 否决硬拦 `cd` |
| （无，新增） | **Q-S7-21** | **阈值定稿 120** + 真实语料 ground truth **重标** |
| （无，新增） | **Q-S7-22** | 提示词写**形态表述、不写阈值数字**；落点唯一 |
| （无，新增） | **Q-S7-23** | 「必须命中」清单勘误：`round_1.log:106`（510 字符）补入 |
| （无，新增） | **Q-S7-24** | **R-S7-48 回退条款作废**（撤 OR 动词分支）+ 文档层级裁定 |

> dev-plan §41.2 / §44 / §45 中已写下的 `Q-S7-16~19` 引用**不逐处改写**（它们是历史留痕），按本表换算即可。

### 19.1 Q-S7-17 裁决：可判定界线走「内容来源」，不走「文件类型」

> **违规（写代码）**：文件内容以**字面量形式出现在执行环节提交的命令字符串里**。
> **合规（写实验产出）**：文件内容由**被执行的既有脚本在运行时计算产生**，命令串里只有脚本路径与参数。

**判定对象是命令字符串本身，而非文件系统副作用** ⇒ 纯函数 `str -> bool`，零 IO、零时序、可单测。
`python run_repro_basics.py` 写多少 `summary.json` / `figures/*.png` 都**永远合规**（零误伤正常复现）——这是本界线的产品意义，也是它优于"按文件类型 / 按写文件动词判"的根本原因。

**落成单一规则，不做动词枚举、不做后缀白名单**：
`某条顶层子命令 argv 形如 [<python>, "-c", <payload>] 且 len(payload) > _INLINE_PY_MAX_CHARS`。

**为什么不按"写文件的动词"判**：该规则**同时覆盖形态 2**——执行环节不写文件、直接把 `python run_x.py` 换成 `python -c "<整段实现，算完直接 print 指标>"`。按动词判会整个漏掉，按载荷体量判必命中。

**为什么本项目只剩这一条路**：沙箱不经 shell（`execution.py:578-590` `_split_top_level` 用 `shlex.split` 拆顶层 `&&` / `;`，之后每条子命令以 argv 直接 `run_in_venv`）⇒ `cat > x.py` 的 `>` 只是普通 token、`python - <<EOF` 的 `<<EOF` 会被当成位置参数。**heredoc / 重定向形态在本项目结构上不成立**，故拦截谓词是单一规则，不是形态枚举。

### 19.2 Q-S7-18 裁决：实现层次 = 工具层硬拦截为主 + prompt 同批收窄，**不接受 prompt-only**

prompt-only 在测试层的证据强度**等于零**（只能证"prompt 里写了这句话"，与 R-S7-41 那道 `x == x` 同族），而本项目实测 prompt 服从率 **75%**，S7-06 / S7-07 已两次栽在"模拟层全绿、真实行为没达成"。约束 C 是三条里**唯一"违反了会静默产出错误结论"**的一条（本次真跑自判成功却与论文对不上，就是它干的）。

**拦截早退点位置是硬要求**：必须在 `_resolve_python_exe()` 之后、`_run_step_subcommands` 之前。
理由：`execution.py` 既有三处早退分支全部在 `collector.run_results.extend(results)` **之前** ⇒ 被拒命令**不进 `run_results`、不进 `step_ledger`**，因而**不污染 `exit_ok`、不被步骤对账当成"完成"**。**放错位置，这条硬防线会自己制造 R-S7-49 那类假绿。**

**拒绝时必须返回结构化错误并明确指路**（误伤可恢复、防 agent 空转）。

### 19.3 Q-S7-19 裁决：计划期告警 W4/W5，且 `plan_checks` 零改动红线**不延伸**

- **W4 计划步骤进入参考仓库目录**、**W5 计划步骤内联写代码**，**只产警示不阻断**——`plan_checks` 既有"由 UI 渲染消费（不阻断审批）"契约一字不动，`check_plan` 签名不变，不新增决策类型 / 按钮 / 流程分支。人在回路的计划审核本身就是那道硬门。
- **W5 与工具层拦截共用同一个纯谓词，一处定义两处调用**——同一条不变量在**计划期**与**执行期**各查一次，**不是造两套机制**。
- **红线不延伸的理由**：S7-08 立那条红线时的语义边界是**模型的语义判断**（"缩得够不够"），Maria 原话「这种不是需要你硬性 gate 的问题」针对的是那一类；而 A/B 是**字符串确定性事实**（前缀比对 + 长度比较），正是 `plan_checks`「零 LLM、确定性交叉检查、只产 warning」这一定位的**靶心用例**。把针对语义判断的克制延伸到确定性事实上属**误引先例**。
  ⚠ 但这毕竟推翻了上一批写进红线的一条 ⇒ 须 Maria 显式点头。**（Maria 已于 2026-07-31 拍板解除，本批据此实施。）**
- **谓词住哪**：`core/plan_checks.py`。它位于 `core/` 顶层、零项目内依赖，被 `core.nodes.execution` import **不成环**；反向 import（`plan_checks` → `execution`）会造成 `execution → plan_checks → execution` 环，**明令禁止**，故顶层拆分逻辑在 `plan_checks` 内用 `shlex` 重述一次而不是复用 `execution._split_top_level`。

### 19.4 Q-S7-20 裁决

#### (a) execution prompt 字节基线守门本批补上，且必须「先建后改」

`_EXECUTION_SYSTEM_PROMPT_BODY` 在本批之前**没有任何 sha256 基线**：既有两条 `assert head == _EXECUTION_SYSTEM_PROMPT_BODY`（`tests/test_sprint5_t14_execution_prompt.py` 与 `tests/test_sprint4_e2.py`）是把「渲染出的 SystemMessage 头部」和「常量自己」比——能证组装没串味，但**常量本身被改成什么样它都恒绿**，与 R-S7-41 同族。

**建门时机是唯一的**：本批正在改它 ⇒ 必须用**改前哈希**先把门建好并验绿，改 prompt 时它**当场红**——这一红既是"门是真的"的活体证明，又天然完成了"改动前后各跑一次"的验红窗口。**顺序颠倒则永远拿不到这个证明**（只能锁一个已经漂移的值）。

#### (b) 共享克隆缓存污染：正不变量是「仓库不接收复现代码与复现产物」，不是「仓库只读」

- **方向确认**：约束 A 生效后污染源自然消失；但**磁盘上现存的残留不会自己消失** ⇒ 须写成**验收前置人工动作**（先存证后清理），**不为一次性残留造清理机制**。
- **`git status --short` 为空这个口径过强且跨仓库不稳**：约束 A 明确允许 `pip install -e <repo>`，而可编辑安装**必然**在仓库源码树里落构建残留；本次真跑只见 3 条，是该仓库忽略规则恰好盖住了其余的——**是仓库特定的运气，不是系统性质**。⇒ 断言口径改为「untracked 条目过滤掉构建残留白名单（`*.egg-info` / `__pycache__` / `build/` / `.eggs`）后为空，**特别地不得出现复现产物目录、任何复现入口脚本、任何指标汇总文件**」。
- **⛔ 明确否决：在 `_resolve_cd` 里硬拦 `cd` 进 `workspace/repos/**`。** 看似是约束 A 的对称硬化，但**误伤面真实存在**——部分仓库依赖以仓库根为工作目录的相对资源路径（配置、数据软链），硬拦会**打死这类复现**。**约束 A 只走软防线（删授权 + W4 告警），硬防线只给约束 C。**
- **范围外备选**：「克隆缓存改只读 + 每篇论文一份独立工作副本」是将来真要**硬保证**仓库不被污染时的正解，**本批范围外**，登记为备选。

### 19.5 Q-S7-21 裁决：阈值定稿 **120**，并**重标**真实语料的 ground truth

> **本条推翻了 Q-S7-17 落地时的一个隐含假设**：四问裁决给出"推荐阈值 120"时所依据的实证样本（"95 字符依赖探针放行"）**没有覆盖到语料里那两条 180+ 字符的长命令**，隐含假设了"合法探针"与"内联写码"两个分布可被一个长度切开。**开发 T-S7-6-1 用两轮真实日志全量标定后，该假设被实测证伪。**

**实测（`workspace/1802.03426/code/exec_logs/round_0.log` + `round_1.log` 全部 `python -c` 子命令，两轮各 7 条，去重 9 条）**：

> **⚠ 条数勘误（2026-08-01，S7-10 验收 P-35）**：本行原写「去重 8 条」，与下表 9 行不符——该「8」是从 dev-plan T-6-1 目测值（见 §19.7「-c 形态实测 8 条/轮」，实测 7 条/轮）带进来的旧数，Q-S7-23 补入 510 后表格已定稿 9 条，正文此处漏同步。已订正为 9 条，长度 `[36, 46, 98, 127, 144, 181, 183, 510, 1304]` 与下表逐行相符。阈值 120 与窗口 [98,126] 不受影响（510、1304 均 >127）。

| 载荷长度 | 命令要旨 | 原标注 | **裁决后分类** |
|---|---|---|---|
| 36 | `import umap; print('UMAP import ok')` | 放行 | **放行**（真·短探针） |
| 46 | `os.listdir('.')` 打印 | 放行 | **放行** |
| 98 | 载入 digits 打印形状 | 放行 | **放行** |
| **181** | 三连 `os.makedirs` + `print('dirs ready')` | 放行 | **预期命中且可恢复**（不计误伤） |
| **183** | 载入真实数据集 + 按论文超参跑完整降维 + 打印结果 | 放行 | **必须命中**（形态 2） |
| 127 | 写 `eval_knn_on_embeddings.py` 占位符 | 命中 | **必须命中** |
| 144 | 写 `run_repro_basics.py` 占位符 | 命中 | **必须命中** |
| 510 | 写 `eval_knn_on_embeddings.py` 真实实现 | （漏列，见 Q-S7-23） | **必须命中** |
| 1304 | 写 `run_repro_basics.py` 真实实现 | 命中 | **必须命中** |

**重标的两条，理由分列**：

1. **183 那条不是探针，是形态 2 本身。** 它加载真实数据集、用论文的超参跑完真正的降维、打印结果——距 Q-S7-17 亲手定义的形态 2（`python -c "<整段实现，算完直接 print 指标>"`）只差最后一个 `print` 换成指标。**任何放行它的阈值，等于宣布"只要你算完别打印指标，就随便在命令行里跑论文实验"。**
2. **181 那条（三连 mkdir）在可行窗口内任何取值下都会被拒**，属**预期命中且可恢复**，不计入误伤。补一条实质判断：产出目录本来就不该由执行环节在命令行里建——那是代码产物自己的职责（对照 §19.4 的产出目录口径与 R-S7-55）。**拒了它，反而把这件事推回正确的责任方。**

⇒ 改标后可行窗口 = **T ∈ [98, 126]**（98 须放行 ⇒ T≥98；127 须命中 ⇒ T≤126）。**120 落在窗口内，定稿。**

**「40% 长探针误伤」这个触发条件不成立**（三条理由，任一条独立成立）：
① 分母错——真实误伤是 1/4，不是 2/5；
② **它对阈值不敏感**——181 在整个可行窗口内恒被拒；**一个在所有可选值下都不变的量，逻辑上不可能是"调阈值"的触发条件**，R-S7-48 那句"若误伤则上调 200"预设了两类可被长度切开，实测证伪了这个预设，触发器随预设一起失效；
③ 它被产品层预期——PRD §12.5.3 的结构化拒绝文案原文就写着"探针类命令请精简"。

**为什么定 120 而不是 110 / 126**：120 已在 PRD 与 dev-plan 双份文档里作为推荐值流通，且落在唯一可行窗口内 ⇒ 标定结论是"推荐值经真实语料检验成立"，文档连续性最干净；改成窗口内其它值是在**没有证据的方向上再拍一次脑袋**（语料在 98 与 127 之间是空的）。压低阈值也买不到想要的东西——真正的残留是**极短写码**（约 30 字符），110 与 120 一样拦不住（见 R-S7-57）。

**机械护栏**：可行窗口的两个端点已钉进 `tests/test_sprint7_s710_exec_locality.py::test_q_s7_21_threshold_is_inside_the_calibrated_window`，日后任何人调值只要出窗就当场红，逼他回去重跑标定。

### 19.6 Q-S7-22 裁决：提示词写**形态表述**、**不写阈值数字**；落点唯一

**形式上不冲突**（先纠正一条常被误引的纪律）：`_EXECUTION_SYSTEM_PROMPT_BODY` 本身**已含数字字面量**（"下标从 0 起"、`401 unauthorized`），所以"冻结区不写死数字"不是既有纪律。R-PC4 的真实语义窄得多：**随任务变化的值**（`_effective_max_rounds` 依赖 `len(execution_steps)` 与剩余预算）必须迁出主体走 HumanMessage。`_INLINE_PY_MAX_CHARS` 是模块级常量、跨论文跨任务恒等 ⇒ 零插值红线与"跨任务主体字节一致"都不破。

**但仍裁定不写数字**，两条独立理由：

1. **双源真相**：正文里的 `120` 与代码里的 `_INLINE_PY_MAX_CHARS = 120` 是同一事实的两份拷贝，**没有任何机械链条绑定**。字节基线只锁正文、锁不住常量 ⇒ 下一个调值的人改了常量，提示词无声说谎，而所有守门全绿——**这是 R-S7-41 那道恒真断言换了层皮**。要救只能加一条 `str(_INLINE_PY_MAX_CHARS) in BODY` 的耦合断言，但那会让"调一次阈值"升级成冻结区改动、必须走三件套，**R-S7-48 回退列写的"单点调值，不改机制"当场作废**。为一句提示词付这个税不值。
2. **数字对模型无效**：LLM 数不准自己正要生成的载荷有多少字符。给一个它算不出来的预算等于没给，反而诱导它猜和辩。**能改变行为的是它能自检的形态约束。**

**定稿写法**（落在 `run_in_sandbox` 工具说明里，与"本工具不用于写代码"同句续写）：

> 本工具不用于写代码；行内 `-c` 只用于简短探针（导入检查、打印版本或数据形状）。凡是需要写文件、或把成段实现塞进命令行的，一律先落成脚本再运行——超长载荷会被直接拒绝。

**落点必须唯一**：只写进 `_EXECUTION_SYSTEM_PROMPT_BODY`（新基线覆盖它）。**不要**同时写进 `run_in_sandbox` 的 docstring——那是第二块喂给模型的文本、当前不在任何字节基线覆盖下，两处同义副本必然漂移。**一句话，一个地方。**

**最后一条纪律**：这句提示词是**降低误伤成本的效率措施，永远不是保证**。硬保证 100% 来自工具层拒绝。**任何 AC 不得把这句提示词的存在当作"约束 C 成立"的证据**——AC-S7-46 是子串断言，只证"写了这句话"，这个定位不许被偷换。

### 19.7 Q-S7-23 裁决：「必须命中」清单勘误——`round_1.log:106`（510 字符）补入

**属 dev-plan 事实遗漏，不是有意排除。** 证据链：dev-plan T-6-1 自述"`-c` 形态实测 8 条/轮"、实测是 7 条/轮 ⇒ 枚举是目测的；同处给 `:121/:131` 的估长是"≈150"、实测 144/127 ⇒ 长度也是目测的；而 `round_1.log:106`（510）与 `round_1.log:92`（1304）**同一轮、相邻行、同一形态**（`p.write_text('<真实实现>')`），给出任何"收 92 不收 106"的原则性理由都不可能。

⇒ **必须命中清单定稿 5 条**：127 / 144 / **183** / **510** / 1304；**必须放行定稿 3 条**：36 / 46 / 98；**181 单列为"预期命中且可恢复"**。本条对阈值零影响（510 > 127，窗口不变）。

### 19.8 Q-S7-24 裁决：R-S7-48 回退条款**作废**；文档层级裁定

**冲突不在"两份文档之间"，而在 dev-plan 内部，且是三比一**：

| 出处 | 立场 |
|---|---|
| PRD §12.3 非目标 5 + §12.5.3 | 禁止动词 / 后缀枚举 |
| **dev-plan §41.3 红线末条** | **"不做……按动词 / 后缀枚举的拦截规则"** |
| 主控派单指令 | 不做动词枚举 |
| dev-plan §44 T-6-1 第 1 条 + §45 R-S7-48 回退列 | 预授权补 OR 分支 |

**层级裁定**：**红线优先于同文档的回退建议；PRD 优先于 dev-plan**（dev-plan 是 PRD 的下游实现文档，无权推翻上游非目标）。

⇒ **R-S7-48 回退列的"上调 200 + 补 OR 分支（含写文件动词且目标 `.py`）"予以撤销**，改写为：
> 阈值在可行窗口 **[98, 126]** 内单点调整；**不得新增第二条规则**；若窗口本身被新语料证伪，回头找 Maria 重议手段，**不得自行加规则**。

**方案 2（T=200 + OR 分支）另有一条独立否决理由**：T=200 在 [120, 200] 区间为**形态 2 开了一扇门**，而 183 那条正是这扇门里**真实存在的样本**。用一条禁令换一个漏洞，净负。

**另外两个备选，均否决**：
- **按语句数判**（数 `;` / 换行）：不可分——98 字符探针 3 条语句，127 字符占位符写入约 2 条语句，分布同样重叠且更严重。
- **AST 解析载荷、按最大字符串字面量体量判**：语义上最准，但**恰好漏掉原始罪证**（127/144 那两条占位符的字面量内容极短），且违背"单一规则、反过度工程"。

### 19.9 四块文本的职责边界（不许串味）

| # | 文本 | 职责 | 保证强度 |
|---|---|---|---|
| 1 | `_EXECUTION_SYSTEM_PROMPT_BODY` 里的形态表述 | **事前告知** | 效率措施，**非保证**（服从率 75%） |
| 2 | 工具层拒绝（`run_in_sandbox` 早退） | **唯一硬保证** | 命中即拒、不进台账 |
| 3 | 结构化拒绝文案 | **事后指路** | 使误伤可恢复、防 agent 空转 |
| 4 | W5 计划期告警 | **同一不变量的前移观测** | 只产警示，签名不动 |

`run_in_sandbox` 的 **docstring 本批不动**——避免第二份无基线覆盖的同义文本。

### 19.10 风险增补（R-S7-57 / R-S7-58）

| 编号 | 风险 | 缓解 | 回退 |
|---|---|---|---|
| **R-S7-57** | **极短写码漏放**：≤ 阈值的最小写入（如 `open('x.py','w').write('pass')` 约 30 字符）**任何可行阈值都拦不住** | 这是"单一规则、拒绝动词枚举"**已知且被接受的残留**。缓解 = 约束 B（计划不写占位步骤）+ W5 + 人在回路审核 | **不得以此为由回头加动词枚举**；若真跑观测到该形态，按 PRD §12.5.5 纪律回头找 Maria 重议手段 |
| **R-S7-58** | **长探针被拒后 agent 空转**：已知语料中 181 那条会被拒 | 提示词形态表述（事前）+ 结构化指路（事后） | **验证 = 真跑计数**（见 §19.11），若不能自愈，触发的是"提示词 / 文案要改"，**不是"阈值要改"** |

### 19.11 真跑收口须补记的两项计数（S7-06 / S7-07 教训）

T-S7-6-9 真跑后**必须记录**：①**工具层拒绝触发次数**；②**每次拒绝后 agent 是否在 1 轮内改出合规命令**。
S7-06 / S7-07 栽过一次"机制上了但触发 0 次"——**触发次数为 0 与触发后不能自愈，同样是红信号**。
判读口径：拒绝触发 ≥1 次且均在 1 轮内恢复 ⇒ 误伤可恢复假设成立。

---

*（v1.5 增补完：§19 = S7-10 落点对齐的完整架构裁决。Q-S7-17 内容来源界线 / Q-S7-18 工具层硬拦截 + 早退点位置硬要求 / Q-S7-19 W4·W5 与 `plan_checks` 红线不延伸 / Q-S7-20 execution 字节门先建后改 + 仓库不变量口径与否决硬拦 `cd`；Q-S7-21 阈值定稿 120 与语料 ground truth 重标（**推翻四问落地时的隐含可分离假设**）/ Q-S7-22 提示词写形态不写数字 / Q-S7-23 必须命中清单勘误 / Q-S7-24 R-S7-48 回退条款作废与文档层级裁定。含 §19.0 dev-plan 旧号→新号映射表、§19.9 四块文本职责边界、§19.10 R-S7-57~58、§19.11 真跑须补记的两项计数。本增补不覆盖 §1~§18 任何既有内容。）*
