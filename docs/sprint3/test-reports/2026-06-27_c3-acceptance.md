# 测试执行报告 - c3-acceptance（任务 C3 独立验收）

- **日期**：2026-06-27 04:35（本地时区）
- **执行人**：@测试工程师代理
- **Sprint**：sprint3
- **触发原因**：全栈开发代理交付 sp3 任务 C3（`core/nodes/execution.py` execution 节点真实现 + 修复循环边界 + interrupt#2）后的独立验收。sp3 单点技术风险最高，严苛复核。
- **commit**：78665b8（被测代码 + 测试均未 commit，待 Maria 统一提交）

## 总裁决

**PASS**（零生产 BUG，零偏差；2 项非阻断遗留项见末尾，均不影响 C3 本体）。

C3 实现质量高：interrupt#2 幂等形态正确落地了 S-1 spike 的「commit 边界分离 + self-loop」契约（而非 dev-plan 原文的「函数体内直接 interrupt」字面方案），错误分类 / 修复循环边界 / 预算 / must-fix-1/2 全部经运行时实证命中。19 条补强用例无一暴露生产缺陷。

## 执行范围

- 命令：
  - `.venv/bin/pytest tests/test_sprint3_c3.py -v`（开发自测复现）
  - `.venv/bin/pytest tests/test_sprint3_c3_reinforce.py -v`（补强）
  - `.venv/bin/pytest tests/test_sprint3_c3.py tests/test_sprint3_c3_reinforce.py -q`（C3 套件 3 次连跑 flaky 检查）
  - `.venv/bin/pytest -q -m "not e2e" --ignore=tests/test_paper_intake.py`（全量非 e2e 回归，验收前后各一次对账）
  - 多个独立 `.venv/bin/python` 探针脚本（guard 跨回合边界 / 预算门优先级 / export_code 端到端 node_errors / 裸 interrupt 行为 / `_step_to_command` 与正则边界）
- 覆盖用例：
  - `tests/test_sprint3_c3.py`（开发 25 用例，CP-C3-1~14）
  - `tests/test_sprint3_c3_reinforce.py`（本次新建 19 用例，R-1~R-15b）
- 是否包含 e2e：否（mock sandbox，零 LLM / 零 deepxiv，省配额；e2e 留 F 阶段）。

## 结果摘要

- 通过：开发 25 + 补强 19 = **44**（C3 套件）；全量非 e2e 回归 **801**
- 失败：0
- 跳过：25（全量回归中，全为 D3/D4 UI 既有 skip，与 C3 无关）
- 警告：1（`LangChainPendingDeprecationWarning`，langgraph 库级预存，sp1 即有，非 C3 引入）
- 总耗时：C3 套件单次约 0.72s；全量回归约 120s

### 全量非 e2e 回归对账（基线 782）

| 项 | 基线（C3 开发后） | 验收后（含补强） | 差值 |
|---|---|---|---|
| passed | 782 | **801** | +19（= 补强用例数，吻合） |
| failed | 0 | **0** | 0 退化 |
| skipped | 25 | **25** | 不变 |
| warning | 1 | **1** | 不变（langgraph 库级） |

验收前先独立复跑确认基线 782（防止开发声明虚高），实测 782 passed 与开发声明吻合；补强后 801 = 782 + 19，逐项零退化。

### 3 次连跑 flaky 情况

C3 套件（44 用例）连跑 3 次：44 / 44 / 44 全绿，耗时 0.72 / 0.73 / 0.72s，**0 flaky**。interrupt / checkpointer 用例均用唯一 `uuid4` thread_id 防串，未观察到任何状态污染或顺序依赖。

## CP-C3-1~14 逐条复核结论

| CP | 验收点 | 结论 | 实证方式 |
|----|--------|------|---------|
| CP-C3-1 | 可导入 + 签名 `(state)->dict` + 本地对象不在 state.py | 命中 | `inspect.signature` + `hasattr(core.state, ...)` 否定 + AUTO_FIXABLE 集合内容 |
| CP-C3-2 | B 档成功（exit 0 + ≥1 指标 → success）+ 出边 reporting | 命中 | mock exit0+`<METRICS>` 标签；反证 exit0 无指标→success=False |
| CP-C3-3 | 错误分类分流（可修复 5 类 vs 不可修复 4 类）+ 顺序敏感 | 命中 | 8 类逐条 + 硬件先于 runtime + 补 R-14/R-15 unresolved/data_missing |
| CP-C3-4 | 可修复+预算够 → fix_loop_count+1 + history append 5 字段 + retry_coding | 命中 | mock import 错误，断言自增/history/route；R-13 连续回合补强 |
| CP-C3-5 | fix_loop_count==3 上限不自增、不回 coding，转 interrupt 准备 | 命中 | 首次进入置 await、不自增 |
| CP-C3-6 | 不可修复不自增、不回 coding，转 interrupt | 命中 | hardware permanent 三态 + 前缀；R-2/R-10/R-14 补强 |
| CP-C3-7 | interrupt#2 三态 resume（terminate/revise_plan/export_code）+ 非法兜底 | 命中 | 最小 self-loop StateGraph + InMemorySaver + `Command(resume)` 端到端；R-3/R-4 端到端补强 |
| CP-C3-8 | 档3 LLM 抽取触发时单点回写预算+累加；不触发零扣减 | 命中 | mock `_llm_extract_metrics`→(1指标,1)，30→29/+1；档1 命中不写预算键 |
| CP-C3-9 | 入口预算门 `<2` 直接降级、不 interrupt | 命中 | budget=1 degraded；R-2 预算门 vs 不可修复优先级补强 |
| CP-C3-10 | 子预算 `>=20` 视同耗尽转 interrupt | 命中 | `_dev_loop_llm_calls==20`→await；R-11 端到端幂等补强 |
| CP-C3-11 | must-fix-1 三 list read-modify-write 无丢失无重复 | 命中 | prior 记录保留+追加；原 state list 未 mutate；grep 零 reducer |
| CP-C3-12 | 细分类进 error_message 前缀，error_type 严格三态 | 命中 | error_type ∈ 三态且 ∉ 细分类值；`_map_category_to_error_type` 映射 |
| CP-C3-13 | interrupt#2 重跑幂等（sandbox 计数==1） | 命中 | 端到端 prepare 恒 1；guard 跨回合边界 R-1 独立探针补强 |
| CP-C3-14 | 失败/降级打 WARNING 非静默 | 命中 | caplog 捕获「执行失败」「降级」WARNING |

## 高风险点单独说透

### 1. interrupt#2 幂等形态（最高风险）—— 对 S-1 结论落地的独立判定

- **(a) 端到端实证恒 1**：用最小 self-loop StateGraph + InMemorySaver + `Command(resume=...)` 真实跑通两步形态。首次失败回合 → `return` 落盘 `execution_result` + 置 `_dev_loop_route="await_dev_loop_interrupt"`（不 interrupt，sandbox prepare=1）；self-loop 重入 execution → `_has_committed_result_for_round` guard 命中跳过 sandbox（prepare 仍=1）→ 函数体内 `interrupt()` 暂停；resume 注入决策 → 该次进入整节点从头重跑（日志可见两次「触发 interrupt#2」），但 guard 仍命中、sandbox 不重跑 → **prepare 计数恒==1**。三态 resume（CP-C3-7）与重跑幂等（CP-C3-13）以及补强 R-4/R-10b/R-11/R-12 端到端均断言 prepare==1，全过。

- **(b) 核对「同节点内直接 interrupt 幂等无效」属实**：回溯 S-1 报告 §1/§2/§3。S-1 CP-S-2 实测「无保护整节点重跑」副作用=2；CP-S-3 实测「同节点 + 闭包缓存」=2、「同节点 + 入口读 state（首轮无种子）」=2——dev-plan 原文的「函数体内先检查 state 是否已有本回合结果再 interrupt」字面方案**确实无效**（resume 重跑时入口 state 仍是上一节点边界旧值，guard 永远 miss）。仅 split-node / commit 边界分离副作用=1。**判定：开发偏离 dev-plan 原文、改用 S-1 §3.2 推荐形态，是对 spike 结论的正确落地，非偷工**——dev-plan L210 已预留此风险（「若 S-1 证实保护方案无效，C3 需调整 interrupt 触发方案」），S-1 已找到副作用=1 的可行契约，C3 据此实现属正常实现细化。

- **(c) self-loop 回合标记不跨回合误命中**：guard 用 `_dev_loop_route=="await_dev_loop_interrupt"` 且 `execution_result` 非空双重判定。独立探针 + R-1 实证四态：`route==await`+非空 result → True（命中）；`route=None`+旧 result → False（coding 修复回合 D1 清空 route，不误命中）；`route=="retry_coding"`+旧 result → False；`route==await`+result=None → False。R-1b 进一步实证：coding 修复回合（route 被清成 None）重入 execution 时 guard 不命中 → 重跑 sandbox 拿修复后新结果（语义正确，不会把上一轮旧结果当本回合复用导致漏跑）。**跨回合无误命中。**

### 2. fix_loop_count 单点自增

代码中自增只出现在 `_maybe_interrupt_or_return` 的「可修复 + `fix_count<MAX_FIX_LOOP_COUNT` + `_dev_loop_llm_calls<MAX_DEV_LOOP_LLM_CALLS`」回 coding 分支一处（`updates["fix_loop_count"]=fix_count+1`）。其余分支（成功 / 入口预算门降级 / await 待 interrupt / 子预算触顶 / 不可修复 / resume 三态）均**不写** `fix_loop_count`。R-13 连续回合 0→1→2 单调自增、==3 转 await 不自增实证；CP-C3-5/6/9/10 + R-2/R-10/R-11/R-14 在 interrupt/降级/不可修复/子预算触顶分支均断言 `"fix_loop_count" not in out`。**单点正确。**

### 3. must-fix-1（三 list read-modify-write）

`_map_execution_result` / `_append_fix_record` / `_mark_degraded_for_report` 全部 `list(state.get(field, []))` 读出整列表 → append → return。grep 实证 `core/nodes/execution.py` 与 `core/state.py` 三字段（node_errors/degraded_nodes/fix_loop_history）零 `Annotated`/`operator.add`。R-4 端到端 export_code 验证 node_errors 三条无丢失（上游 coding 旧错误 + 本轮 hardware 失败 + 降级记录）——注意 guard 命中分支只写 `execution_result`+`current_step` 不重写 node_errors，由 `_mark_degraded_for_report` 从 `state` 兜底读取累加，实测完整。CP-C3-11 断言原 state list 未被原地 mutate。

### 4. must-fix-2（预算）

execution 主体不调 LLM。唯一 LLM 路径 = 档3 `_llm_extract_metrics`（仅 exit 0 且 stdout 非空触发），返回 `(metrics, calls_used)`，`_map_execution_result` 仅在 `llm_calls_used>0` 时单点 `retry_budget_remaining -= calls`（`max(0,...)` 防负）+ `_dev_loop_llm_calls += calls`。CP-C3-8 触发 30→29/+1、CP-C3-8b 档1 命中不写预算键、R-7 档3 exit 非0 不触发 LLM 实证。入口预算门 `retry_budget_remaining < 2` → 直接降级不 interrupt（R-2 实证压倒不可修复）；子预算 `_dev_loop_llm_calls >= 20` → 视同耗尽走 await（R-11 端到端）。

### 5. 错误分类承载位置

`_map_category_to_error_type`：可修复（in AUTO_FIXABLE）→ transient，不可修复 → permanent；降级单独 `_mark_degraded_for_report` 写 degraded。细分类 `[error_category=...]` 写进 `NodeError.error_message`，**不进 error_type**。CP-C3-12 断言 error_type ∈ {transient,permanent,degraded} 且 ∉ 细分类值集合。R-14（unresolved_resource→permanent）/ R-15（data_missing→permanent）/ R-9（prepare 抛错→dependency→transient）补强。

## 失败排查

无。全程零失败用例。19 条补强用例首次运行即全绿，未暴露任何生产缺陷。

## 对开发那处 interrupt#2 形态修正的独立判定

- **是否正确落地 S-1 结论**：是。开发偏离 dev-plan L479 原文「函数体内直接 `interrupt(...)`」，改采 S-1 报告 §3.2 推荐的「commit 边界分离 + self-loop」（首次失败 return 落盘 + 置 `await_dev_loop_interrupt` → self-loop 重入 guard 命中跳 sandbox → 函数体内 interrupt）。S-1 已实证字面方案副作用=2、split 形态=1。本次端到端 prepare 计数恒 1，证明形态正确，非偷工。dev-plan L492 开发完成声明已记录此形态，与代码一致。

- **对 D1 的影响是否如开发所述**：基本属实，但需强化为**硬交接约束**。开发声明 `_route_after_execution` 须把 `await_dev_loop_interrupt` 路由回 execution（self-loop）、`retry_coding` 回 coding。独立验证：若 D1 漏接 `await_dev_loop_interrupt → execution` 的 self-loop，则 execution 首次失败 return await 标记后**无人重入**，`_route_after_execution` 兜底返回 reporting，**interrupt#2 将永不触发**，用户得不到决策机会——这是 D1 必须正确接线的强约束（见 L-C3-01）。架构 §2.5.3 正文的 `_route_after_execution` 示例只列了 `retry_coding`，**未列 `await_dev_loop_interrupt`**，D1 实现者需依据开发交接声明补上 self-loop 分支，不能照抄架构 §2.5.3 字面代码。

## 后续动作 / 遗留非阻断项

- **L-C3-01（强交接约束，指派 D1 @全栈开发代理）**：`_route_after_execution` 必须显式新增 `_dev_loop_route=="await_dev_loop_interrupt" → "execution"` 的 self-loop 分支（架构 §2.5.3 示例代码未列此分支，仅列 retry_coding）。漏接将导致 interrupt#2 永不触发。D1 完成后建议补一条主图层集成测试：FULL 模式失败 → 走到 execution interrupt#2 暂停（验证 self-loop 真接通），而非仅靠 C3 自测里测试自己写的简化 self-loop graph。
- **L-C3-02（观察项，非阻断）**：C3 自测与补强的 checkpointer 端到端用例在 state 里放 `ExecutionMode` enum，langgraph 1.1.10 反序列化时打印 `Deserializing unregistered type core.state.ExecutionMode`（未来版本可能 block，需 `allowed_msgpack_modules` 或避免在 checkpointed state 放裸 enum）。当前仅告警不影响测试通过；属测试探针/真实 graph state 共性，建议 D1/E 阶段编排时统一处置（如 enum 存 `.value` 字符串）。此告警与全量回归的 1 个 `LangChainPendingDeprecationWarning` 不同源，仅在 checkpointer 端到端用例 stderr 出现，不计入 pytest warning 汇总。

## 交付确认

- **新增文件**：`/data/myproj/auto_reproduction/tests/test_sprint3_c3_reinforce.py`（19 用例）
- **修改文件**：`/data/myproj/auto_reproduction/docs/TODO.md`（追加 C3 验收条目）；本报告 `/data/myproj/auto_reproduction/docs/sprint3/test-reports/2026-06-27_c3-acceptance.md`
- **被测代码**：`/data/myproj/auto_reproduction/core/nodes/execution.py`（未改动，仅 Read 审查）
- **未 git commit / git add**（待 Maria 统一提交）。
- 测试用 `WORKSPACE_DIR` 子路径 + monkeypatch mock sandbox，未真正读写真实 workspace、未发任何 LLM/deepxiv 请求。
- 下一次跑测试触发条件：D1 编排完成后跑主图层 self-loop 集成验收（验证 L-C3-01 接线）；F 阶段凭证就绪后补 e2e。
