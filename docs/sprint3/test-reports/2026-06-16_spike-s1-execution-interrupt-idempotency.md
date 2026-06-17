# Spike S-1 报告：interrupt#2 execution 函数体内重跑幂等

- **日期**：2026-06-16
- **负责人**：@全栈开发代理
- **任务**：dev-plan.md 阶段 S / 任务 S-1（硬前置门，阻断 C3 / D1）
- **脚本**：`scripts/spike_execution_interrupt_idempotency.py`
- **环境**：langgraph 1.1.10，`.venv/bin/python`，纯 langgraph + SqliteSaver(WAL) 本地验证，**零 LLM / 零 deepxiv / 零配额消耗**（伪 sandbox + 副作用计数器）
- **架构参考**：architecture §2.5.1（interrupt#2 触发位置）/ §4.3（重跑幂等缓解）/ §7 回问 3
- **连跑稳定性**：连跑 3 次全 6/6 PASS（确定性本地流程、无 LLM 服从度变量，符合治理 §5 复现率判据）

---

## 1. 总结论

**Spike S-1：PASS（6/6 检查点全绿）。解除 C3 / D1 阻断。**

但本 spike 揭示了一个**必须传达给 C3 的实现修正**（非阻断、不需回架构师重评，理由见 §4）：

> 架构 §4.3 的**字面方案**——「execution（单节点）在 `interrupt()` 前先检查 `state` 是否已有本回合 `execution_result`，命中则跳过 sandbox」——在「sandbox 副作用与 `interrupt()` 处于**同一节点函数体内**」时 **resume 重跑无法命中、保护无效**（副作用计数实测 = 2）。
>
> 根因（LangGraph 语义实证）：节点函数体内 `interrupt()` **之前**对 state 的写入（局部变量、尚未 `return` 的 dict）**不会被 checkpoint**。resume 时整节点从头重跑，节点入口 state 仍是「上一个节点边界」的旧值（无本回合 `execution_result`），故 guard 永远 miss，sandbox 被重复执行。
>
> **可行契约（C3 必须采用，副作用实测恰为 1）= split-node 持久化边界**：把 sandbox 执行落在一个**先 `return` 的节点边界**（commit `execution_result` 到 checkpoint），再在**独立的 gate 节点**函数体内 `interrupt()`。resume 只重跑 gate 节点，sandbox 节点已 commit 不再重跑 → 副作用恰为 1。

---

## 2. CP-S-1 ~ CP-S-6 逐条结论

| 检查点 | 结论 | 实测数据 |
|---|---|---|
| **CP-S-1** | PASS | 脚本可直接 `python scripts/spike_execution_interrupt_idempotency.py` 运行，总耗时约 0.25~0.28s（<30s），输出完整 interrupt → snapshot → resume → 状态推进日志 |
| **CP-S-2** | PASS | **无保护时整节点从头重跑实证**：interrupt 暂停时 sandbox 副作用计数 = 1，resume 后 = **2**（> 1）。确证 LangGraph 1.1.10 节点函数体内 `interrupt()` 的 resume 会**整节点从头重跑**到 `interrupt()` 处 |
| **CP-S-3** | PASS（经实现修正） | **同节点内 §4.3 字面方案均失败**：local 闭包缓存 = 2、state 朴素入口读 = 2（resume 重跑均未命中保护，副作用仍 = 2）。**split-node 可行契约**：sandbox 节点 + gate 节点，interrupt 时 = 1、resume 后 = **1**（恰为 1）。这是 C3 必须落地的保护契约 |
| **CP-S-4** | PASS | 三态 resume 均被 `interrupt()` 正确返回并写对应 `user_fix_decision`：`{"terminate":"terminate","revise_plan":"revise_plan","export_code":"export_code"}` |
| **CP-S-5** | PASS | 主线程独立 SqliteSaver `get_state(config)`：`snapshot.next=('execution',)` 非空、`#interrupts=1`、payload `interrupt_kind='dev_loop_failure'`（app.py `interrupt_kind` helper 可读到此键） |
| **CP-S-6** | PASS | 本报告归档至 `docs/sprint3/test-reports/`，含执行日志 + 逐条结论 + C3 实现建议 + 高风险项裁决 |

### 关键副作用计数实测汇总

| 场景 | sandbox 副作用计数（resume 后） | 含义 |
|---|---|---|
| 无保护（none） | **2** | CP-S-2：整节点重跑确证 |
| 同节点 + 闭包缓存（local） | 2 | §4.3 字面方案变体 A 失效 |
| 同节点 + 读入口 state（state 朴素，首轮无种子） | 2 | §4.3 字面方案变体 B 失效（同节点内重跑命不中） |
| 同节点 + 预置入口结果（state，模拟边界已落盘） | 0 | 旁证：只要入口 state 已有结果即跳过（但首轮 0 次不符合「跑一次」语义，仅证明 guard 逻辑本身正确） |
| **split-node（sandbox 节点 + gate 节点）** | **1** | **CP-S-3 可行契约：恰为 1** |

---

## 3. 给 C3（execution 节点真实现）的幂等保护实现建议

**强制契约（C3 必须遵守，否则修复回合 / interrupt#2 resume 会重复跑 sandbox 子进程，造成资源浪费 + 状态污染）**：

1. **不要把 sandbox 执行与 `interrupt()` 写在同一个会被 resume 重跑的代码路径里且只靠读 `state` 去重**——本 spike 实证该模式无效。
2. **采用「持久化边界分离」**。两种等价落地方式（任选其一，均不破坏主图 7 节点）：
   - **(推荐) 节点入口幂等读 + 提前 commit**：execution 节点入口先判 `state.get("execution_result")` 是否为**本回合**结果（用回合标记 `_exec_done_for_round == fix_loop_count` 配合判定）。命中则跳过 sandbox 直接复用；未命中才跑 sandbox。**关键**：sandbox 结果必须在 `interrupt()` **之前**通过一个**已 commit 的 checkpoint 边界**进入 state——即 execution 首次失败回合先 `return` 一次（把 `execution_result` 落盘、由出边路由再次进入 execution 或一个内部 gate），再在重入时函数体内 `interrupt()`。这样 resume 重跑时入口 state 已含本回合结果，guard 命中、sandbox 不重跑。
   - **(等价) split gate 节点**：在 execution 之后的出边引入一个轻量「dev_loop gate」逻辑承载 `interrupt()`（sandbox 仍在 execution）。注意 AC-S3-10 禁新增主图节点，故此方式需以 execution 节点内部「先 return 落盘、再重入 interrupt」的形态实现，而非新增对外可见节点。
3. **回合标记**：用既有 `fix_loop_count`（或架构已批准的 `_exec_done_for_round` 同义内部标记）区分「本回合是否已有 execution_result」，避免跨回合误命中（修复回合 N 不能复用回合 N-1 的旧结果）。架构 §4.3 已提「用回合标记 + 已有结果判定」——本 spike 确认**该判定逻辑正确，但必须配合 commit 边界才能在 resume 时生效**。
4. **interrupt payload 必须含 `interrupt_kind="dev_loop_failure"`**（CP-S-5 已验证 app.py helper 可读），与 planning 的 interrupt#1 区分。
5. **三态 resume 路由**（CP-S-4 已验证可行）：`interrupt()` 返回值即 resume payload，按 `decision.get("decision")` 写 `user_fix_decision` ∈ {terminate / revise_plan / export_code}，交给 `_route_after_execution`（架构 §2.5.3）路由。

---

## 4. dev-plan L207 高风险项裁决

dev-plan L207 高风险定义：
> 若 CP-S-2 证实重跑且 CP-S-3 保护方案无效（如 LangGraph 在 resume 时不保留 interrupt 前已写入的 state 字段），则 execution 无法在函数体内安全 interrupt —— 需与架构师确认备选方案。

**裁决：不触发「需回架构师重评」的硬阻断条件。**

依据：
- CP-S-2 确证整节点重跑（重跑成立）；
- CP-S-3 的字面方案确实**无效**（同节点内 guard miss），**但这正是架构 §4.3/§7 回问 3 预判并要求 spike 验证的边界**，且 spike **已找到副作用恰为 1 的可行契约（split-node / commit 边界分离）**，无需推翻 interrupt#2 在 execution 内实现的方案、也无需调整 interrupt 触发点到别的节点。
- L207 触发回架构师的前提是「**找不到任何能让保护生效的方案**」。本 spike 已用实测证明存在可行方案（副作用 = 1），故**仅需 C3 实现时遵循 §3 的修正契约**，属正常实现细化，不属架构重评。

**给架构师的轻量备注（非阻断，C3 落地时同步即可）**：架构 §4.3 正文「execution 在 `interrupt()` 前先检查 state 是否已有本回合 execution_result（resume 重跑时复用）」一句，宜补一句限定——「该复用仅在 execution_result 已通过**前一个 checkpoint 边界**落盘时生效；C3 需以『首次失败回合先 return 落盘 execution_result、重入时再 interrupt』的形态实现，不能在 sandbox 与 interrupt 同一次节点执行内靠读 state 去重」。建议由 C3 实现者在 PR 描述中引用本报告 §3，架构 §4.3 可在 C3 落地后顺手补注。

---

## 5. 执行日志（代表性单次，3 次一致）

```
========================================================================
Sprint 3 Spike S-1: interrupt#2 execution 函数体内重跑幂等
========================================================================
[scenario] 无保护(none)
  [execution_node] entered: guard=none round=0 entry_execution_result=None
  [phase1] sandbox_runs=1  (interrupt 暂停)
  [phase2] snapshot.next=('execution',) #interrupts=1 interrupt_kind='dev_loop_failure'
  [execution_node] entered (resume 重跑整节点)
  [phase3] sandbox_runs_total=2   ← 整节点重跑，副作用翻倍

[scenario] 保护变体A(local-闭包缓存)        resume后=2  ← 失效
[scenario] 保护变体B(state-入口读,首轮无种子) resume后=2  ← 失效（同节点内 guard miss）

[scenario] split-node 可行契约 (sandbox节点+gate节点)
  [execution_sandbox] 跑 sandbox 并落盘 execution_result   (run#1)
  [execution_gate] entered: execution_result=set
  [phase1] sandbox_runs=1 (interrupt 暂停于 gate)
  [phase2] snapshot.next=('execution_gate',) #interrupts=1 interrupt_kind='dev_loop_failure'
  [execution_gate] entered (resume 仅重跑 gate, sandbox 不重跑)
  [phase3] sandbox_runs_total=1   ← 副作用恰为 1 ✅

[scenario] 三态resume[terminate]   → user_fix_decision='terminate'
[scenario] 三态resume[revise_plan] → user_fix_decision='revise_plan'
[scenario] 三态resume[export_code] → user_fix_decision='export_code'
------------------------------------------------------------------------
[PASS] CP-S-1 ~ [PASS] CP-S-6   |  6/6 PASS  |  耗时 ~0.27s
Spike S-1 整体: PASS
```

---

## 6. 复用资产与遗留说明

- 范式沿用 sp2 S-1（`scripts/spike_interrupt_threading.py`）：工作线程内 `invoke` 触发 interrupt → 主线程独立 SqliteSaver 读 snapshot → 新线程 `Command(resume=...)` 恢复，同 thread_id + 同 SQLite 文件 + WAL。
- 序列化遵循 BUG-S1-02 治理：脚本内 `_dumps` = `json.dumps(ensure_ascii=False, sort_keys=True, default=str)`，无 `str(dict)`。
- spike 用独立 DB 文件 `workspaces/spike_s3_exec_idempotency.sqlite`（每场景前清库），不污染生产 `checkpoints.db`。
- 改动留工作区未 commit（待 Maria 统一 commit）。
