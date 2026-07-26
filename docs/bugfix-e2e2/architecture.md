# E2E-2 复验发现·架构评估与实施边界

> 来源：Maria 2026-07-22 第二次真实浏览器端到端复验发现的三条问题。本文档覆盖其中两条**纯 bug**
> （发现③ 中断判定死角、发现① 用户可见文本术语泄漏），发现②（资源侦察加执行命令能力）属功能需求，须另走 PRD。
>
> 评估日期：2026-07-26 ｜ 评估方：架构师代理 ×2（独立并行）｜ 主控已逐条磁盘核实关键结论
> 编号：BUG-E2E2-03（中断判定）、BUG-E2E2-01（术语泄漏）

---

# 第一部分 BUG-E2E2-03：动态 interrupt 判定死角

现场：Maria 实测 thread `task-435baf71f4cf`，coding 节点等填 `env:GOOGLE_API_KEY`，
**凭证输入面板不弹出**，任务功能性死锁；任务列表同时把它误判为 `no_report`（失败·未产报告）。

## §0 结论摘要

| 项 | 裁决 |
|---|---|
| 拟定修法（去 `next` 门槛 + R5 优先于 R4） | **方向正确，采纳** |
| 改动范围 | **必须从 2 处扩到 5 处**（+`get_interrupt_payload`、`get_interrupt_token`、`is_finished`），四个读方法必须同一原子提交 |
| 反向风险（END 后残留 interrupt 被误判 awaiting） | **已排除**：LangGraph 源码硬保证 + 内存实证 + 三个真库取证 |
| 20-thread 真库回归矩阵 | **0 翻转**，`done` 未变 `awaiting` |
| 根因边界 | **原判断需修正**：不是"节点入口 interrupt"，而是"**同一次节点执行内的第 2 次及以后 interrupt**" |
| `next` 门槛的来历 | **不是防御设计**，是 S-1 spike 断言表达式的逐字抄写 |
| 现有测试能否拦住 | **不能**。全仓零用例构造过 `next=() ∧ tasks 含 interrupt`；改动落地后全量应零红——"零红"不等于"有保护" |

## §1 根因

### §1.1 现场解剖（生产库 `checkpoints.db`，只读）

thread `task-435baf71f4cf` 最新 checkpoint（`checkpoint_ns=''`，`step=5`）三行 pending write：

```
task=000...000  idx=-4  channel=__resume__     (NULL_TASK 哨兵)
task=aae2130e   idx=-4  channel=__resume__     [{'value': <25字符>, 'remember': False}]
task=aae2130e   idx=-3  channel=__interrupt__  {'interrupt_kind','question','is_sensitive','purpose_key','allow_degrade': True}
```

task_id 反算 = 主图 `coding` 节点。payload 含 `allow_degrade=True` → 来源锁定
`core/nodes/coding.py:810` 的**前置凭证 gate**（`ui/pages/execution_monitor.py:59-65` 红线：agent 工具路径 payload 永不含该键）。
该 thread `required_credentials` 两项，第一项已答，第二项 `purpose_key` 长度 18 = `env:GOOGLE_API_KEY`。

四处判定在该现场的实际取值：

| 位置 | 表达式 | 现场取值 | 后果 |
|---|---|---|---|
| `app.py:398` | `bool(snapshot and snapshot.next and self._has_interrupt(snapshot))` | **False** | `execution_monitor.py:971` 不进 case⑤ → 面板不弹 |
| `app.py:412` | `return not snapshot.next` | **True** | `is_finished` 谎报"已到 END" |
| `app.py:437` | `if not (snapshot and snapshot.next): return None` | **None** | payload 取不到 |
| `app.py:461` | `if not (snapshot and snapshot.next): return None` | **None** | token 取不到 |
| `app.py:194-197` | R4 先于 `:198` 的 interrupt 判定 | **`no_report`** | 列表页显示"失败（未产报告）" |

### §1.2 `_has_interrupt` 判定正确（`app.py:415-422`）

全函数 8 行，扫 `tasks[*].interrupts`，**零处引用 `snapshot.next`**，全程 `getattr` 防御。
现场实测返回 **True**——它是对的，被 `app.py:398` 前面的 `snapshot.next` 合取项短路吞掉了。

### §1.3 修正①：触发边界不是"节点入口 interrupt"

`docs/TODO.md` 原记的"节点入口/缺凭证类"边界**已被实证推翻**。`InMemorySaver` 最小图复现：

| 场景 | 构造 | `get_state().next` | `tasks[0].interrupts` |
|---|---|---|---|
| A：节点**第一行**就 `interrupt()` | 单次 | **`('a',)` 非空** | 非空 |
| C：同节点内**连续两次** `interrupt()`，答完第一次停在第二次 | 串行 | **`()` 空** | 非空 |

**入口 interrupt 本身不产生该形态；"同一次节点执行内的第 2 次 interrupt"才产生。**

机制（LangGraph 1.1.10，`langgraph/pregel/main.py`，主控已逐字核实）：

```python
# :1308  get_state：不带 checkpoint_id ⇒ apply_pending_writes=True
# :1352  get_state_history：同款表达式，带 checkpoint_id ⇒ False   ← next 二义性的根
# :1118-1124
for tid, k, v in saved.pending_writes:
    if k in (ERROR, INTERRUPT):     # 只跳过 __error__ / __interrupt__；__resume__ 不在名单
        continue
    next_tasks[tid].writes.append((k, v))
# :1138
tuple(t.name for t in next_tasks.values() if not t.writes)   # 有 writes 的 task 被踢出 next
```

即 **`__resume__` 被当成"这个 task 已经有产出了"，把 `coding` 从 `next` 里划掉**。
而 `tasks[].interrupts` 走另一条装配（`:1129-1134` → `langgraph/pregel/debug.py:208-213`，
按 `chan == INTERRUPT` 单独捞），不受影响 → 出现"next 空但 interrupt 挂着"。

**修正后的真实受影响面：**

| 中断点 | 形态 | 是否中招 |
|---|---|---|
| `planning.py:884` interrupt#1 首问 | 单节点执行单次 | 否 |
| planning 的 revise / switch_repo 复问 | self-loop 重入，新 checkpoint 无 `__resume__`（`planning.py:907-940`） | 否 |
| `execution.py:2243` interrupt#2 | 走 `_ROUTE_AWAIT_INTERRUPT` self-loop 重入后单次 | 否 |
| `coding.py:810` 凭证 gate 第 1 项 | 单次 | 否 |
| **coding 凭证 gate 第 2 项及以后** | `coding.py:801-830` for 循环，同一次执行内串行 | **是（实锤现场）** |
| ~~一次执行内 agent 第 2 次调 `request_user_input`~~（`core/tools/interaction_tools.py:175`） | **机制不同源** | **否（2026-07-26 实测推翻，见勘误）** |
| 非法 resume 后 gate 重新 interrupt 同一项（`coding.py:775` 契约） | 同一次执行内第 2 次 | **是** |

> **【勘误 F1，2026-07-26 测试工程师实测，主控已核】** 上表原判 agent 工具路径"是（未直接取证，高置信）"
> **被真图实测推翻**。用真 ReAct 子图 harness 造两种子形态（两独立轮次 / 同批 tool_calls），
> 父图 `next` 均为 `('agent',)` **非空**，**不产生 BUG 形态**。pending_writes 铁证：
>
> ```
> agent 路径:  [(agent_task,'__interrupt__'), ('00000000','__resume__')]   ← resume 只挂 NULL_TASK
> coding gate: [(coding_task,'__interrupt__'), ('00000000','__resume__'),
>               (coding_task,'__resume__')]                                ← 父 task 自己有 writes ⇒ 被踢出 next
> ```
>
> 机制不同源的原因：子图内 interrupt 的 resume 消费发生在**子图命名空间**，父 task 的 writes 恒空；
> 且子图按 checkpoint 精确恢复，前序 `request_user_input` 不重放，父节点每次执行体内只发生一次 interrupt。
>
> **真实受影响面因此收敛为唯一一条：父节点函数体内串行 `interrupt()`（= coding 凭证 gate）。**
> 这不削弱修复的必要性（现场 bug 依旧成立、修法不变），但纠正了受影响面的范围。
> 覆盖用例：`tests/test_e2e2_acceptance_gaps.py::test_e2e2_acc_agent_tool_path_keeps_next_nonempty`。

这解释了 Maria 能过 planning 审核却卡在 coding。

> **值得记一笔**：`docs/sprint6/architecture.md:25` 在 S6-01 分析 interrupt token 时**已经精确描述过这个形态**
> ——"同一节点同一次任务执行内的串行 interrupt（resume 后节点重放、消费 idx=0 的 resume、随即对下一项再
> interrupt），ns 不变 → id 不变"。当时只追到"id 不变"，没追到"它同时会把 `next` 清空"。当年离这个 bug 只差一寸。

### §1.4 修正②：`next` 门槛不是"故意加的防御"

`app.py:392` 引用的"S-1 spike CP-S1-3 实证"追到底是：

- `scripts/spike_interrupt_threading.py:165-167`：`cp("CP-S1-3", ok=bool(snapshot_next) and len(interrupt_meta) > 0, ...)`
- `docs/sprint2/test-reports/2026-05-24_spike-s1-interrupt-threading.md:53`：`[PASS] CP-S1-3 — snapshot.next=('dummy_planning',), #interrupt_meta=1`

即 `next and _has_interrupt` **是 spike 断言表达式的逐字搬运**，描述"当时观测到的形态"，
**不是**为排除某个已知假阳性而设的守门。

更关键：spike 的 phase 4（`scripts/spike_interrupt_threading.py:224-228`）跑到 END 后
**只打印 `final_snapshot.next`，从未读过 `final_snapshot.tasks`**。

**所以历史上从未产生过"END 后 tasks 残留 interrupt"的证据——那个假想的防御场景当年就没被验证过。
去掉 `next` 门槛不存在"回潮"，因为它从来没挡住过什么。**

## §2 同源点裁决表

扫描范围：`app.py` 全量 + `ui/` 全量 + `core/` 全量。**`core/` 零命中**——判定面完全收敛在 `app.py` 一个文件，`ui/` 只消费布尔结果。

| # | 位置 | 裁决 | 理由 |
|---|---|---|---|
| 1 | `app.py:389-398` `is_interrupted` | **改（P0）** | 主缺陷点 |
| 2 | `app.py:176-202` `derive_task_status` R4/R5 | **改（P0）** | 误判 `no_report` |
| 3 | `app.py:424-443` `get_interrupt_payload`，门槛在 `:437` | **必须同批改（P0，阻断性）** | 见 §2.1：不改则 #1 修好也仍死锁 |
| 4 | `app.py:445-478` `get_interrupt_token`，门槛在 `:461` | **必须同批改（P0）** | token=None 破坏 S6-01 换代判定与 `resume_with` 第二道防线（`app.py:327-335`），用户提交会被"迟到提交"逻辑拒绝 |
| 5 | `app.py:400-412` `is_finished`，返回式在 `:412` | **改（P0，由 P1 上调）** | ①暂停态返回 True 是同款误判；②`:403-406` docstring 与现实矛盾；③`tests/test_sprint5_s5_08_routing.py:438` 把"两方法语义正交"写成契约，不改它这条契约会被本次修复直接打破 |
| 6 | `app.py:491-510` `get_phase`，`next[0]` 在 `:507-508` | **不改实现，只补 docstring** | 见 §2.3(b) |
| 7 | `app.py:415-422` `_has_interrupt` | **一字不改** | §1.2 已证判定正确；它是唯一抽象，改口径会造成大面积假绿（§3.5 红线） |
| 8 | `app.py:19-20`/`:392-394`/`:404-406`/`:432`/`:167-173`/`:185`/`:500-501` docstring | **必须同步改** | 不改会诱导下一个人把门槛加回去 |
| 9 | `app.py:687` `cancel_task` 守卫 | **不改**（自动受益） | 由"过严"变正确 |
| 10 | `app.py:712-732` `_should_route_to_user_input_panel` | **不改**（自动受益） | 修好 #1/#3 后自然生效 |
| 11 | `app.py:512-518` `get_task_status` / `:520-568` `list_threads` | **不改**（自动受益） | 修好 #2 即正确 |
| 12 | `app.py:745-759` `_route_for_status` | **不改**（自动受益） | 修好 #3 后 awaiting 分支路由正确 |
| 13~21 | `ui/pages/execution_monitor.py` case⑤/⑥/⑥bis/⑥ter、`analysis_progress.py` case④、`plan_review.py:834/845`、`task_list.py:57-70`、`result_report.py`、`paper_input.py`、`scripts/spike_*.py` | **全部不改** | 判定源修好即恢复；`ui/` 只消费布尔结果 |

> 协调者原点名的"`resume_with`（:461 附近）"是误记：`app.py:461` 实际属于 `get_interrupt_token`；
> `resume_with` 在 `app.py:306-356`，其自身**不含** `snapshot.next` 门槛，只在 `:328` 通过
> `get_interrupt_token` 间接受影响。（主控已用 AST 独立复核方法边界，确认此裁决。）

### §2.1 为什么"只改 `is_interrupted` + `derive_task_status`"是错的（关键）

假设只改这两处，在 §1.1 的现场走一遍：

1. `is_interrupted` → True，`execution_monitor.py:971` 进入 case⑤；
2. `get_interrupt_token(...)`（`:972`）→ **仍是 None**（`app.py:461` 未改）；
3. `interrupt_kind(...)`（`:988`）→ 内部调 `get_interrupt_payload`（`app.py:630`）→ **None** → `:631-632` `if not payload: return None` → kind = None；
4. kind 既非 `dev_loop_failure` 也非 `user_input_request` → 落到 `execution_monitor.py:1002-1008`
   "planning interrupt 防御性跳回 review 页" → `_KEY_CURRENT_PAGE = "review"` + `st.rerun()`；
5. `plan_review.py:834` payload → **None** → `:867-871` 渲染"计划尚未就绪，请稍候……" + `st_autorefresh` **永久轮询**；
6. 回主入口 `app.py:849` → kind ≠ `user_input_request` → 不回弹。

**净效果：死锁形态从"面板不弹"变成"永远停在计划审核页转圈"，且更难诊断。
因此 #1/#2/#3/#4 必须是同一个原子提交，不可拆批。**（主控已核实第 4、5 步的源码行为属实。）

### §2.2 `is_finished` 裁决细节

暂停态下 `is_finished` 现在返回 True，是同款误判。当前之所以没炸，是因为唯一生产调用点
`execution_monitor.py:1018` 有两道遮蔽：case⑤ 在它之前 return，且它要求 `current_step == "reporting"`。

改法 = 在既有空快照守卫（`app.py:410-411`）之后，把返回式加一个合取项：`next 空 ∧ 无挂起 interrupt`。
风险为零的前提正是 §3 的结论。

### §2.3 R4/R5/R6/R7 换序自洽性 + `get_phase` + 空快照守卫

**(a) R6/R7 换序后仍自洽。** 新顺序：R1 → R2 → R3 → **R5'** → R4 → R6 → R7。R4 仍在 R6/R7 之前，
到达 R6 时"next 非空 ∧ 无 interrupt"这个前提**没有任何变化**，R6/R7 实现和语义一字不动。

**"真的跑到 END"会不会被误判？** 不会：END 态 `snapshot.tasks == ()`（§3.2 源码硬保证）→ R5' 不触发 →
落 R4 → `done`/`no_report`，与现状**完全一致**。20-thread 真库实测唯一 done 新旧均判 done。

**(b) `get_phase` 不改实现。** 它在同节点第 2 次 interrupt 暂停时会返回 `active_node=None`，但两个消费方
（`execution_monitor.py:1025-1029` case⑥ter、`analysis_progress.py:613-626` case④bis）都排在 interrupt 分支之后，
修好 #1 后永远够不到该状态。硬改会引入"从 history 读 next"的第二套读栈，违背极简且踩 §3.5 的坑。

**(c) 空快照守卫不被破坏。** `docs/sprint6/architecture.md:175` 引用的"app.py:254"是**陈旧行号指针**
（今天的 `:253-255` 只是一行分隔注释）。真正的守卫在 `app.py:187-188`（R1）与 `:410-411`。
R1 是整个函数第一条，位置不动，R4/R5 换序在它之后发生，不可能触及它。

## §3 反向风险评估（放行命门）

若 END 后 `tasks` 仍残留已消费的 interrupt，去掉 `next` 门槛会让**每个已完成任务永久显示"等待输入"**
并被 `app.py:849` 强制路由到监控页。四重证据一致指向**不会**。

### §3.1 `next` 门槛当初防的是什么：**什么都没防**

见 §1.4 完整取证。CP-S1-3 只证了"planning 首问暂停时 next 非空且有 1 条 interrupt"，
spike phase 4 跑到 END 后**从未读 `tasks`**。**不存在"防的那个场景会回潮"。**

### §3.2 源码层硬保证：END 后 `tasks` 必空 ⇒ `interrupts` 必空

`langgraph/pregel/main.py`（主控已逐字核实）：

```python
# :1129-1134
tasks_with_writes = tasks_w_writes(next_tasks.values(), saved.pending_writes, ...)
# :1144-1145
interrupts=tuple([i for task in tasks_with_writes for i in task.interrupts]),
```

`tasks_w_writes` **只遍历 `next_tasks`**。图到 END 时 `prepare_next_tasks` 返回空 → `snapshot.tasks == ()`
→ `_has_interrupt` 必为 False。**即使 writes 表里真躺着 `__interrupt__` 行，只要该 checkpoint 没有可调度 task，判定就是 False。**

`StateSnapshot` 官方定义（`langgraph/types.py:553-571`）：`next` 的官方语义是
"**这一步每个 task 要执行的节点名**"，**不是**"图还没结束"。这两者只在"一次执行一次 interrupt"的世界里恰好等价
——sp5 引入串行凭证 gate 后等价关系就断了。

> `Interrupt` 在 1.1.10 只剩 `value` 和 `id` 两字段，`resumable / ns / when` 自 0.6.0 起已删除
> ——**任何基于 `resumable` 的"更精确判定"都会 AttributeError，不要走这条路。**

### §3.3 内存实证（最小图，`InMemorySaver`）

| 场景 | END 后 `next` | END 后 `tasks` | `_has_interrupt` |
|---|---|---|---|
| A：入口 interrupt → resume → END | `()` | `[]` | **False** |
| C：同节点两次 interrupt → 两次 resume → END | `()` | `[]` | **False** |
| E：END 前最后一节点 interrupt → resume → END | `()` | `[]` | **False** |

### §3.4 真库取证（三库全只读）

**方法（可复现核验）**：①记录原件 md5；②**复制到 `/tmp` 后在副本上操作**
（`get_checkpointer()` 会执行 `PRAGMA journal_mode=WAL` + `CREATE TABLE`，属写操作，绝不能对 fixture 原件施加）；
③按 `tests/test_sprint6_s6_07_task_status.py:122-132` 既有范式构造离线 controller；
④逐 thread `get_state()` 记录 next/tasks/interrupt 数/error/current_step/report_path；
⑤`status_old` 用真 `derive_task_status`，`status_new` 用脚本内本地重写的新规则函数（**不改 `app.py`**）；
⑥纯 SQL 只读复核 writes 分布（`sqlite3.connect(f"file:{db}?mode=ro", uri=True)`）；⑦收工复核 md5 + `git status`。

**结果（`checkpoints_s6_full20.db`，20 thread 逐条对拍）：翻转 0 个。**
两侧分布完全相同：`{awaiting:10, interrupted:4, no_report:3, done:1, failed:1, cancelled:1}`。

三个命门问题的直接回答：

- **有没有 thread 从 `done` 变 `awaiting`？** **没有。** 唯一的 done（`task-9208a1a4b4f5`）interrupt 数为 0，最新 checkpoint **零 pending write**。
- **有没有 `next=()` 但 tasks 含 interrupt 的组合？** **一个都没有。** 11 个带 interrupt 的 thread `next` 全部非空、
  最新 checkpoint 全部无 `__resume__`——它们都是首问态暂停。
- **`__interrupt__` 与 `__resume__` 同 checkpoint 存在吗？** 存在（s6 库 21 处、s7 库 2 处），
  但**无一例外全落在历史 checkpoint 上，没有任何一个是某 thread 的最新 checkpoint**。

另两库：`checkpoints_s7_99eef17bccf2.db` 最新 checkpoint `next=()`、0 interrupt、`report_path` 非空 → 新旧均判 done；
其唯一使用者 `tests/test_sprint7_targeted.py:70` 完全不碰 GraphController，不受影响。
生产库 `checkpoints.db` 见 §1.1。

**只读纪律核验**：两 fixture 原件 md5 前后一致，`/tmp` 副本已删，`git status` 全程只有开工前就存在的 ` M docs/TODO.md`。

### §3.5 唯一真实的残留面 + 一条红线

**唯一残留面 = 历史快照。** `get_state_history()` 走 `apply_pending_writes=False`，会把早已被消费的
interrupt 原样回放（实测：图已 END，step=0 的历史帧仍返回 `next=('a',)` + `interrupts=[…]`）。

**本项目生产代码完全不读 history**：`get_state_history` 在 `app.py` / `ui/` 下 grep **零命中**，
仅出现于 3 个测试文件。**风险不成立**，但必须写进 docstring 作为使用约束。

**推荐判定式**：仅对 `graph.get_state(config)`（不带 checkpoint_id 的最新快照）调用；
有挂起 interrupt ⇔ `any(task.interrupts for task in snapshot.tasks)` —— 即现有 `_has_interrupt`，一字不改。

> **红线：不要改用 `snapshot.interrupts` 顶层字段。** 它语义更贴切，但项目里所有测试替身
> （`tests/test_app_controller.py:50-54`、`test_sprint5_s5_08_routing.py:406-410`、
> `test_sprint6_s6_07_task_status.py:54-58`、`test_sprint6_s6_01_controller.py:48`）
> **都只定义 `values / next / tasks` 三个属性**。一旦改口径，这些替身会静默返回 False，**制造大面积绿着的假测试**。

### §3.6 一个已知的、可接受的残留角落（如实登记）

若进程在"消费了 resume、越过 gate、但整个 superstep 尚未提交"的窗口内被杀（对 coding 而言是整段 ReAct 产码期），
最新 checkpoint 会留下 `__interrupt__`(已答) + `__resume__` → 新判定式会显示"等待输入"并重弹**上一个已答过的问题**。

- 现状（旧逻辑）在同一场景下判为 `no_report`「失败·未产报告」，是个死胡同；
- 新逻辑下用户再答一次：resume 值以**列表追加**存放（生产库实测形态 `[{'value':…,'remember':…}]`），
  节点重放时按 interrupt 调用序对位消费（`coding.py:777-783` 的幂等命门），多余的一个值不会被任何
  `interrupt()` 取走，节点直接放行。**行为等价于一个"继续执行"按钮，自愈。**

**裁决：新行为严格优于现状，接受该角落，登记为已知边界，不为它加特判。**

## §4 回归影响面

盘点范围 `tests/` 全量（25 个命中文件）。**只有 5 个文件真正跑生产判定逻辑，
其余 20 个把整个 controller 换成 `MagicMock` 并脚本化布尔返回值，改动机械上穿不透。**

### §4.1 真正会穿透的 5 个文件

| 文件 | 相关用例数 | 备注 |
|---|---|---|
| `tests/test_sprint6_s6_07_task_status.py` | 12（8 derive + 4 list_threads） | |
| `tests/test_app_controller.py` | 12（`:556-577`、`:583-608`、`:614-622`、`:625-637`、`:640-643`、`:646-661`、`:667-685`） | |
| `tests/test_sprint5_s5_08_routing.py:394-472` | 5 | |
| `tests/test_sprint6_s6_01_controller.py` | 11（含 `:310-343` `get_phase` 四条） | |
| `tests/test_d1e_api_key_fallback_integration.py:116-127` | 1 | **全仓唯一一条"真图 + 真库跑到 planning interrupt 再 `assert controller.is_interrupted(...)`"的集成用例，端到端形态锚，绝对不能动** |

### §4.2 「必须改的用例」= **空**（二次独立确认）

逐条推演 + `/tmp` 副本上的 20-thread 新旧判定对拍，**没有任何既有断言会翻转**。

> 这个"空"本身就是最强的取证结论：**现有测试对这条改动完全无感——既拦不住回归，也从未验证过 bug 场景。
> 「零红」不等于「有保护」，所以 §5.4 的新增用例不是可选项，是收口门。**

### §4.3 只换不弱化 / 必须补强

| 文件:行 | 处置 |
|---|---|
| `tests/test_app_controller.py:247` | docstring「graph 已推进到 END（next 为空元组）返回 False」**因果失真**，改为「tasks 无 interrupt 元数据 → False」。断言本身不动 |
| `tests/test_sprint6_s6_01_controller.py:126` | 注释「next 空 → 无 interrupt」失真，需改 |
| `tests/test_sprint5_s5_08_routing.py:438` | docstring「两方法在同一 snapshot 上语义正交」需重新表述：改动后现实中存在 `next=()` ∧ 挂 interrupt 的快照。给 `is_finished` 加 `not _has_interrupt` 后正交性恢复——**这正是"必须改 `is_finished`"的第三条独立理由** |
| `tests/test_sprint5_s5_08_routing.py:335` | 现有 case⑥bis mock 组合在新逻辑下仍可达，**不动**；另补页面级姊妹用例（见 §5.4 L3） |

### §4.4 不该动（覆盖权重不降反升）

`test_app_controller.py:614/:625/:646`（`_has_interrupt` 三条边界）——改动后它成为**唯一**判据，权重上升；
`:640`（snapshot 空值防御，去掉 `next` 后它是唯一守门）；`:556/:583`（`cancel_task` 守门，判定放宽后更敏感）；
`test_sprint6_s6_01_execution_monitor.py:370/:408`（轮询纪律矩阵——`is_interrupted` 变真的场景变多 → case⑤ 抢到更多流量，
**这是唯一能发现"停轮询分支被误扩大导致 UI 冻死"的网**）。

## §5 施工单

### §5.1 精确改动清单（照抄，不许自由发挥）

**唯一生产文件：`app.py`。零改动：`core/` 全部、`ui/` 全部、`config.py`、`core/state.py`。**

---

**改动 1 —— `app.py:389-398` `is_interrupted`**，整个方法替换为：

```python
    def is_interrupted(self, thread_id: str) -> bool:
        """判定 graph 是否停在**挂起的 interrupt** 上（三类 interrupt 通用）。

        判定依据 = ``snapshot.tasks[*].interrupts`` 非空（_has_interrupt）。
        **严禁再引入 ``snapshot.next`` 作为前置门槛**（BUG-E2E2-03 根因）：本项目
        全程使用动态 interrupt（节点函数体内 raise），当**同一次节点执行内发生第 2 次
        及以后的 interrupt** 时（coding 凭证 gate 串行索要多项 / agent 多次调
        request_user_input），该 checkpoint 上会同时存在 ``__resume__`` 与
        ``__interrupt__`` 两类 pending write；LangGraph 的 get_state 走
        apply_pending_writes=True（langgraph/pregel/main.py:1308），把带 writes 的
        task 从 next 中剔除（main.py:1118-1124 / :1138）→ ``snapshot.next`` 变成空元组，
        而中断信息仍完整挂在 tasks[*].interrupts 上。

        适用范围：仅对 ``get_state(config)``（不带 checkpoint_id 的**最新**快照）成立。
        ``get_state_history`` 走 apply_pending_writes=False（main.py:1352），会回放
        **已被消费**的 interrupt——本方法及其同族读方法一律不得改读 history。

        图已跑到 END 时 snapshot.tasks 为空元组（tasks_w_writes 只遍历 next_tasks，
        main.py:1129-1134），故 _has_interrupt 必为 False，不存在"已完成被误判为等待输入"。
        """
        config = _make_config(thread_id)
        snapshot = self._main_graph.get_state(config)
        return bool(snapshot and self._has_interrupt(snapshot))
```

---

**改动 2 —— `app.py:437`（`get_interrupt_payload` 内）**

```python
        # 改前
        if not (snapshot and snapshot.next):
            return None
        # 改后
        if not snapshot:
            return None
```

同时把 `app.py:432` 那行 docstring
`判定与 is_interrupted 一致：snapshot.next 非空且某 task 含 interrupts；命中即返回` 改为
`判定与 is_interrupted 一致：某 task 含 interrupts（**不看 snapshot.next**，BUG-E2E2-03：同节点第 2 次 interrupt 时 next 为空元组）；命中即返回`

---

**改动 3 —— `app.py:461`（`get_interrupt_token` 内）**：同改动 2 的改法。
并在该方法 docstring 末尾（`:457` 的 R-S6-A1 段落之后）追加：

```
    判定口径（BUG-E2E2-03）：与 is_interrupted 同源，**不以 snapshot.next 为前置**——
    否则同节点第 2 次 interrupt 时 token 退化为 None，S6-01 换代判定与 resume_with
    第二道防线（app.py:327-335）会把用户的正常提交误判为"迟到提交"并拒绝。
```

---

**改动 4 —— `app.py:400-412` `is_finished`**，整个方法替换为：

```python
    def is_finished(self, thread_id: str) -> bool:
        """判定 graph 是否已运行至 END（S5-08 完成判定兜底，架构 sprint5 §7.8）。

        判定形态（与 is_interrupted 同一读路径范式，纯只读、不改 state）：snapshot
        存在 ∧ values 非空 ∧ ``snapshot.next`` 为空元组 ∧ **无挂起 interrupt**。

        [BUG-E2E2-03] 第三个合取项不可省：同一次节点执行内的第 2 次 interrupt 暂停时
        ``next`` 也是空元组（LangGraph 把 __resume__ 计入 task.writes，
        langgraph/pregel/main.py:1118-1138），只看 next 会把"暂停等输入"误判为"已结束"。
        加上 not _has_interrupt 后，与 is_interrupted **语义正交**恢复成立
        （tests/test_sprint5_s5_08_routing.py:437-448 的既有契约）。

        "存在"须校验 snapshot.values 非空——LangGraph 对从未启动的 thread_id
        返回 values={} 的空快照（next 也是空元组），不能误判为已完成。
        """
        config = _make_config(thread_id)
        snapshot = self._main_graph.get_state(config)
        if not snapshot or not getattr(snapshot, "values", None):
            return False
        return not snapshot.next and not self._has_interrupt(snapshot)
```

---

**改动 5 —— `app.py:176-202` `derive_task_status` 换序**，方法体 `:187-202` 替换为
（**只把 interrupt 判定整块上移到 `next_` 计算之前，R4/R6/R7 三块一字不动**）：

```python
    if not snapshot or not getattr(snapshot, "values", None):
        return None  # R1
    values = snapshot.values
    if values.get("error"):
        return TASK_STATUS_FAILED  # R2
    if values.get("current_step") == "cancelled_by_user":
        return TASK_STATUS_CANCELLED  # R3
    # [BUG-E2E2-03] R5 提到 R4 之前：同一次节点执行内的第 2 次 interrupt 暂停时
    # snapshot.next 为空元组（langgraph/pregel/main.py:1118-1138 把 __resume__ 计入
    # task.writes），若先判 R4 会把"等待输入"误判成 done/no_report。
    # 反向安全性：图真到 END 时 snapshot.tasks 为空（main.py:1129-1134
    # tasks_w_writes 只遍历 next_tasks）→ 本行必不命中 → 仍落 R4，行为与改动前一致。
    if GraphController._has_interrupt(snapshot):
        return TASK_STATUS_AWAITING  # R5
    next_ = getattr(snapshot, "next", None) or ()
    if not next_:
        # R4a/R4b：图已到 END（且无挂起 interrupt）
        return TASK_STATUS_DONE if values.get("report_path") else TASK_STATUS_NO_REPORT
    if has_active_worker:
        return TASK_STATUS_RUNNING  # R6
    return TASK_STATUS_INTERRUPTED  # R7
```

同时把该函数 docstring 的 `:177` 与 `:184-185` 优先级表述改为 **`R1>R2>R3>R5>R4>R6>R7`**，
并在 `:184` 段落补一句：`R5 先于 R4 是 BUG-E2E2-03 的修复面（动态 interrupt 暂停时 next 可为空元组）`。

---

**改动 6 —— `app.py:167-173` 常量行末注释**

```python
TASK_STATUS_DONE = "done"                # R4a：无挂起 interrupt ∧ next 空 ∧ report_path 非空
TASK_STATUS_NO_REPORT = "no_report"      # R4b：无挂起 interrupt ∧ next 空 ∧ report_path 空
TASK_STATUS_AWAITING = "awaiting"        # R5：有挂起 interrupt（不看 next，BUG-E2E2-03）
TASK_STATUS_RUNNING = "running"          # R6：next 非空 ∧ 无 interrupt ∧ 有存活 worker
TASK_STATUS_INTERRUPTED = "interrupted"  # R7：next 非空 ∧ 无 interrupt ∧ 无存活 worker（孤儿）
```
（`TASK_STATUS_FAILED` / `TASK_STATUS_CANCELLED` 两行不动。）

---

**改动 7 —— `app.py:19-20` 模块 docstring**

```
    - is_interrupted 判定 = snapshot.tasks 含 interrupt 元数据（**不以 snapshot.next
      为前置门槛**）。BUG-E2E2-03：动态 interrupt 在"同一次节点执行内的第 2 次暂停"
      时 get_state().next 为空元组（LangGraph 把 __resume__ 计入 task.writes），
      S-1 spike CP-S1-3 只观测过首问态形态，不构成 next 门槛的依据；
```

---

**改动 8 —— `app.py:500-501`（`get_phase` docstring）追加**

```
    [BUG-E2E2-03] 另一边界：同一次节点执行内的**第 2 次** interrupt 暂停时
    get_state().next 为空元组 → active_node 为 None。消费方必须保持"interrupt 分支
    先于在途标签分支"的 case 分发顺序（execution_monitor.py:971 先于 :1025），
    否则该状态会掉进 case⑦ 假轮询。本方法不做补偿（不引入 history 第二读栈）。
```

### §5.2 红线（不许碰）

1. **不改任何方法签名。** `tests/test_sprint3_e1.py:44-66` 逐字断言 `is_interrupted` / `get_interrupt_payload`
   等 6 个方法的 `inspect.signature` 字符串；不得加 `include_finished=` 之类开关参数，只许改函数体与 docstring。
2. **不改 `_has_interrupt`（`app.py:415-422`）的实现口径**，不得改用 `snapshot.interrupts` 顶层字段（§3.5）。
3. **不引入第二套读栈**：禁止 `get_state_history()`、禁止带 `checkpoint_id` 读、禁止 `subgraphs=True`。
4. **零 state 字段新增、interrupt payload 键集合冻结**（项目既有红线）。
5. **不动 R2（error）/ R3（cancelled）的最高优先级**——已终止/已失败的任务不得因残留 interrupt 被判成"等待输入"，产品红线。
6. **不动 `get_phase` 实现**，不动 `ui/` 任何 case 分发顺序。
7. **不新增 55MB 级 fixture**：`.gitignore:27` 使 `tests/fixtures/checkpoints_s6_*.db` 进不了版本控制 = 进不了 CI。
8. **`app.py` 本轮单收口**：改动期间不与其他 `app.py` 改动并行（沿 sp6 R-S6-1 纪律）。
9. 属**纯 bug 修复**，按项目规则不必先走 PRD；但因触及核心状态判定，回归按功能变更等级执行。
10. **不得重命名任何既有测试函数。** `tests/test_sprint5_t52_ac_matrix.py` 是按函数名硬编码的 AC 追溯矩阵
    （如 `:203` 逐字引用 `test_cp_0_2_3_case6bis_finished_no_report_renders_failure_card_stops_polling`）。
    **新增用例随便加，改名必红。** 该矩阵未引用 `test_cp_4_3_1_*` / `test_cp_d2_4_*` / `test_cp_0_2_4_*`，主战场安全。
11. **无源码字样守门**：全仓无任何测试断言 `app.py` 判定表达式的源码文本，改判定式不会撞字节级断言
    ——**但也没有静态守门能阻止未来有人把 `next` 门槛加回去**，故改动 1 的 docstring 里那句
    "严禁再引入 snapshot.next 前置"必须逐字写入，作为人读级防回潮。

### §5.3 改动顺序

1. **第 1 步（原子，不可拆）**：改动 1/2/3/4。这四处是同一个死锁的四个面，任何拆分都会产出 §2.1 那种"半修好"的中间态。
2. **第 2 步**：改动 5/6。
3. **第 3 步**：改动 7/8。
4. **第 4 步**：新增回归测试（§5.4）。**先验红**（在改动未落地的工作树上确认新用例必红，红的条数与预期一致），**再落改动、验绿**。
5. **第 5 步**：定向 + 全量回归（§5.5），并复核 20-thread 矩阵仍为
   `{awaiting:10, interrupted:4, no_report:3, done:1, failed:1, cancelled:1}`
   ——**任何 `done→awaiting` 都必须立即停手上报**（说明 §3 结论被推翻）。
6. **第 6 步（须 Maria 单独授权，耗配额）**：真实浏览器 e2e 复走"两项凭证串行 gate"闭环。
7. **第 7 步**：文档勘误（`docs/sprint6/architecture.md:169-184` R 表、`:236-251` §6、`:175` 陈旧行号指针；`docs/TODO.md` 边界表述）。

### §5.4 新增测试设计（收口门，防假绿）

**为什么必须走内存真图**：现有两个 fixture 库**都不含 `next=() ∧ tasks 含 interrupt` 的 thread**
（§3.4 逐 thread 确认）→ **真库路线靶不住这个 bug**。而生产库 `task-435baf71f4cf` 做精简副本入库会把
含真实凭证问答痕迹的 checkpoint 提交进仓库，须 Maria 单独裁决脱敏方案，**本设计不默认走该路线**。

#### L1 判定层（新建 `tests/test_e2e2_interrupt_gate_fix.py`）

用与既有替身同构的 `_FakeSnapshot`（照抄 `tests/test_sprint5_s5_08_routing.py:400-410` 的
`_FakeTask` / `_FakeSnapshot`），驱动**真实 GraphController**（照抄同文件 `:413-425` 的
`_controller_with_snapshot`：`GraphController.__new__` 绕过 `__init__`，`_main_graph` 换 `MagicMock`，
`graph.get_state.return_value = snapshot`）。

```
BUG 形态快照 = _FakeSnapshot(
    values={"current_step": "coding", "report_path": None},
    next_=(),                                        # ← 命门：空元组
    tasks=(_FakeTask(interrupts=(_FakeInterrupt({"interrupt_kind": "user_input_request",
                                                 "question": "…", "is_sensitive": True,
                                                 "purpose_key": "env:GOOGLE_API_KEY",
                                                 "allow_degrade": True}),)),),
)
```

| # | 断言 | 未修复 | 修复后 |
|---|---|---|---|
| L1-1 | `is_interrupted(tid) is True` | **红** | 绿 |
| L1-2 | `is_finished(tid) is False` | **红** | 绿 |
| L1-3 | `get_interrupt_payload(tid)["purpose_key"] == "env:GOOGLE_API_KEY"` | **红**（None → TypeError） | 绿 |
| L1-4 | `get_interrupt_token(tid)` 非 None 且形如 `"{id}:{16位指纹}"` | **红** | 绿 |
| L1-5 | `derive_task_status(snap, has_active_worker=False) == TASK_STATUS_AWAITING` | **红**（得 `no_report`） | 绿 |

**反向安全断言（防"修过头"）**：

| # | 快照 | 断言 |
|---|---|---|
| L1-6 | `next_=(), tasks=()`，`values={"current_step":"reporting","report_path":"/x/r.md"}` | `is_interrupted False` ∧ `is_finished True` ∧ `derive → done` |
| L1-7 | `next_=(), tasks=(有 interrupt,)`，`values={"error": "boom"}` | `derive → failed`（R2 仍压过 interrupt，红线 §5.2-5） |

**验红方法**：未落地改动时跑本文件，**应恰好红 5 条**（L1-1~L1-5），L1-6/L1-7 绿。条数 ≠ 5 即停手复核。

#### L2 真图层（扩写 `tests/test_sprint5_t22_coding_gate.py`）

**为什么是它**：`tests/test_sprint5_t22_coding_gate.py:374-382` 的 `_build_gate_graph` 用真实 `StateGraph` +
`InMemorySaver` 把 `coding` 单节点编成图；`test_cp_2_2_3_serial_interrupts_no_crosstalk_three_consecutive_runs`
（`:392-457`）已经**真实制造出**两次串行 interrupt——在 `:423-425` 那次 `app.invoke(Command(resume=...))` 之后、
`:426` 拿到 `intr2` 的那一刻，图就正处在 `next=()` + `tasks[0].interrupts` 非空的 bug 形态。
**它只是从来没查过 snapshot。** 全离线、零配额、确定性、已连跑 3 次稳定。

**改法：新增姊妹用例**（不动 `:392-457` 原用例的任何断言），复用 `_build_gate_graph` / `_two_missing_state` / `_ReactStub`：

```python
def test_e2e2_second_serial_interrupt_snapshot_shape_and_controller_judgement(tmp_path, monkeypatch):
    """[BUG-E2E2-03] 同一次节点执行内的第 2 次 interrupt：真图快照形态 + 五方法判定。"""
    # …与 :399-425 同样的隔离与两步 invoke（首次暂停在 A，resume A 后暂停在 B）…
    snap = app.get_state(cfg)

    # (1) 锁住 LangGraph 的真实形态——全套里唯一能防"LangGraph 升级后形态变化"的断言
    assert snap.next == (), "第 2 次串行 interrupt 暂停时 get_state().next 应为空元组"
    assert GraphController._has_interrupt(snap) is True, "中断确实挂在 tasks[].interrupts"
    assert snap.tasks[0].interrupts[0].value["purpose_key"] == _PK_B

    # (2) 真快照喂五个判定
    controller = <_controller_with_snapshot 范式，_main_graph 直接指向 app 真图>
    assert controller.is_interrupted(tid) is True
    assert controller.is_finished(tid) is False
    assert controller.get_interrupt_payload(tid)["purpose_key"] == _PK_B
    assert controller.get_interrupt_token(tid) is not None
    assert derive_task_status(snap, False) == TASK_STATUS_AWAITING
```

> `_make_config` 会注入 `checkpoint_ns=""`（`app.py:96`），与 `_build_gate_graph` 用的 `cfg`（`:410` 只有 `thread_id`）
> 在 `InMemorySaver` 下等价，不影响读取。

**验红方法**：未落地改动时，`snap.next == ()` 与 `_has_interrupt is True` 两条**绿**（它们描述现状），
五个判定断言**红 5 条**。这正是"现状形态成立、判定全错"的铁证。

**可选扩展**：用 `tests/test_sprint4_b2_interrupt3_idempotency.py:209-213` 的父图 + 真 ReAct 子图 harness
造两轮 `request_user_input`，做同样的 `snap.next == ()` 断言，闭合 §1.3 表中"未直接取证"的那一行。

#### L3 补强既有用例

| 落点 | 内容 | 验红预期 |
|---|---|---|
| `tests/test_sprint6_s6_01_controller.py`（`:123` 姊妹位） | 新增 `test_e2e2_token_available_when_next_empty_with_interrupt`：`_FakeSnapshot(values={}, next_=(), tasks=(有 interrupt,))` → `get_interrupt_token` 返回 `"{id}:{指纹}"` 而非 None | 未修复**红 1** |
| `tests/test_sprint6_s6_01_controller.py:126` | 注释「next 空 → 无 interrupt」失真，改为「tasks 无 interrupt → token None」 | — |
| `tests/test_sprint5_s5_08_routing.py`（`:335` 姊妹位，页面级） | 新增 `test_e2e2_case5_wins_over_case6bis_when_finished_and_interrupted`：controller mock `is_finished=True ∧ is_interrupted=True ∧ interrupt_kind="user_input_request"` + `state={"current_step":"reporting","report_path":None}` → 断言渲染**凭证输入面板**而非"报告未生成"卡片、且**不注册 autorefresh** | 改动前后均绿；价值是钉住"未来有人调换 case⑤/case⑥bis 顺序" |
| `tests/test_sprint5_s5_08_routing.py:438` | docstring 补充「正交性由 is_finished 的 `not _has_interrupt` 合取项保证（BUG-E2E2-03）」 | — |
| `tests/test_app_controller.py:247` | docstring 因果失真 → 改为「tasks 无 interrupt 元数据 → False」；断言 `:252-254` **不动** | — |
| `tests/test_app_controller.py`（`:246` 姊妹位） | 新增 `test_e2e2_is_interrupted_true_when_next_empty_with_interrupt` | 未修复**红 1** |
| `tests/test_sprint6_s6_07_task_status.py`（`:91` 姊妹位） | 新增 `test_e2e2_r5_wins_over_r4_when_next_empty`：`derive_task_status(_snap({"current_step":"coding"}, next_=(), interrupt=True), False) == awaiting`；再加一条 `_snap({"current_step":"reporting","report_path":"/x/r.md"}, next_=(), interrupt=True)` → `awaiting`（interrupt 压过 done）。现有 `_snap` 夹具（`:61-63`）天然支持，**无需改夹具** | 未修复**红 2** |
| `tests/test_sprint6_s6_07_task_status.py:113-116` | docstring 里 `R2>R3>R4>R5>R6>R7` 改为 `R2>R3>R5>R4>R6>R7`；断言 `:116` **不动** | — |

**全套验红总计：L1 红 5 + L2 红 5 + L3 红 4 = 14 条**。落地改动后应全部转绿，且既有用例零红。

### §5.5 回归命令（基线 2014 绿）

Python 一律用 `.venv/bin/pytest`。

**(1) 验红（改动落地前）**
```
.venv/bin/pytest -q tests/test_e2e2_interrupt_gate_fix.py tests/test_sprint5_t22_coding_gate.py \
  tests/test_sprint6_s6_07_task_status.py tests/test_app_controller.py tests/test_sprint6_s6_01_controller.py
```
**期望：恰好 14 条新用例红，既有用例全绿。** 条数不符即停手复核。

**(2) 定向回归（改动落地后）**
```
.venv/bin/pytest -q tests/test_e2e2_interrupt_gate_fix.py tests/test_app_controller.py \
  tests/test_sprint5_s5_08_routing.py tests/test_sprint6_s6_01_controller.py \
  tests/test_sprint6_s6_07_task_status.py tests/test_d1e_api_key_fallback_integration.py \
  tests/test_sprint5_t22_coding_gate.py
```

**(3) UI 分发纪律定向**（判定放宽后 case⑤ 抢到更多流量，唯一能发现"停轮询分支被误扩大导致 UI 冻死"的网）
```
.venv/bin/pytest -q tests/test_sprint6_s6_01_execution_monitor.py tests/test_sprint6_s6_02_progress.py \
  tests/test_sprint4_f1.py tests/test_sprint3_e2_reinforce.py tests/test_sprint6_s6_06_reconnect.py \
  tests/test_sprint6_s6_07_task_list_page.py
```

**(4) 契约守门定向**（签名冻结 + AC 追溯矩阵，对应红线 1 / 10）
```
.venv/bin/pytest -q tests/test_sprint3_e1.py tests/test_sprint3_e1_reinforce.py \
  tests/test_sprint5_t52_ac_matrix.py tests/test_sprint5_t52_regression_targets.py
```

**(5) 全量回归（收口）**
```
.venv/bin/pytest -q
```
**期望：≥2014 绿（新增 14 条 → 约 2028 绿），0 红、0 新增 skip。**

**(6) 20-thread 矩阵复核（反向风险持续探针）**
```
.venv/bin/pytest -q tests/test_sprint6_s6_07_task_status.py::test_cp_4_3_3_list_threads_20_real_db \
  tests/test_sprint6_s6_07_task_status.py::test_cp_4_3_2_list_threads_readonly_md5_unchanged
```
该用例硬 `assert _FIXTURE_20.exists()`（`:136`），而 `.gitignore:27` 使该库不进版本控制
——**本机可跑、CI 跑不了**，故 CI 侧保护完全由 L1/L2 离线用例承担。

**(7) e2e 说明**：`tests/conftest.py:22-25` 会 `load_dotenv` 读 `.env` 凭证，使 e2e 默认**真跑**并消耗配额。
上述 (1)~(6) 全部为非 e2e 离线用例；真跑 e2e 属 §5.3 第 6 步，**须 Maria 单独授权具体动作后才可执行**。

## §6 备选方案对比

| 方案 | 说明 | 评估 |
|---|---|---|
| **A：以 `_has_interrupt` 为唯一判定锚（采纳）** | 四个读方法统一去 `next` 门槛；`derive_task_status` 把 interrupt 判定提到 next 之前 | 零新抽象、零新读栈、零 state 变更、零签名变更；反向风险由 `tasks_w_writes` 只遍历 `next_tasks` 硬保证；20-thread 真库 0 翻转。**采纳** |
| B：改读 `get_state_history()` 首帧的 `next` | 保住 sprint6 文档口径 | 每次判定多一次 DB 读（1.5s 轮询 × 5 处判定）；历史帧会回放**已消费**的 interrupt，把明确 bug 换成更隐蔽的 bug。**排除** |
| C：最小补丁（只改 `is_interrupted` + `derive_task_status`） | 改动面最小 | §2.1 已证：会把死锁换成 review 页无限轮询。**排除**（保留作反面样本，防后来者"优化"回去） |
| D：写侧规避（coding gate 改走 self-loop 重入） | 从源头消除该形态 | 要改图结构/节点契约，破 7 节点红线；`interaction_tools.py` 的 agent 路径天生无法约束调用次数。**排除**（登记为长期演进备选） |

**关键权衡**：`next` 在 LangGraph 里的官方语义是"**这一步每个 task 要执行的节点名**"，
而不是"**图还没结束**"。两者在"一次节点执行一次 interrupt"的世界里恰好等价，所以从 sp2 spike 一路沿用到 sp6 都没出事；
sp5 引入串行凭证 gate 后等价关系断裂，判定就整体失真。方案 A 的本质是
**把判定锚从"隐含等价的代理量"换成"直接量"**——这是架构上更正确的方向，不只是打补丁。

## §7 残留不确定性

| # | 项 | 状态 | 验证步骤 |
|---|---|---|---|
| 1 | agent 工具路径（`interaction_tools.py:175`）第 2 次调用是否同形态 | **已闭合，结论推翻**：实测**不产生**该形态（机制不同源，见 §1.3 勘误 F1） | 已由 `tests/test_e2e2_acceptance_gaps.py` 的 agent 路径两条用例覆盖 |
| 2 | 生产库上真跑 `get_state()` 复现 `next=()` | **未验证**（为避免 `build_graph` 触发落盘副作用而未执行；结论由源码行 + 最小合成用例 + task_id 反算三重对齐推出） | 修复后在 `task-435baf71f4cf` 上挂回，直接看面板是否弹出——这本身就是最终验收 |
| 3 | §3.6 的"消费 resume 后崩溃"角落 | **已实测闭合**：模拟硬杀复现出 `next=() ∧ __interrupt__(已答) + __resume__`，新判定给 `awaiting`（旧给 `no_report` 死胡同）；再答一次**自愈**、零串位 | `tests/test_e2e2_acceptance_gaps.py` 的 crash 两条用例（其中一条是有效收口门，单处验红会红） |
| 4 | planning 的 revise / switch_repo 是否真的幸免 | **已实测确认幸免**：自环重入后 `next=('planning',)`、pending_writes 无 `__resume__`（反向证据成立） | `tests/test_e2e2_acceptance_gaps.py::test_e2e2_acc_planning_selfloop_reentry_keeps_next_nonempty` |
| 5 | 20-thread fixture 不在版本控制内（`.gitignore:27`） | 已确认，**已降级为非关键路径** | 新增回归不得依赖它；CI 覆盖靠 L1/L2 离线用例 |
| 6 | 两个 fixture **都不含** `next=() ∧ 挂 interrupt` 的 thread | 已逐 thread 确认 | **真库路线无法靶住本 bug**，只能走 L2 内存真图 harness。若坚持真 checkpoint 靶，须从生产库 `task-435baf71f4cf` 做单 thread 精简副本入版本控制——**但会把含真实凭证问答痕迹的 checkpoint 提交进仓库，须 Maria 单独裁决脱敏方案，本评估不建议默认执行** |

---

**一句话交底**：这个 bug 的根不在"节点入口"，而在 LangGraph 把 `__resume__` 当成了"任务已有产出"，
于是**任何一次节点执行里的第二个问题都会让 `next` 归零**；`app.py` 有五个地方拿 `next` 当 interrupt/结束的前置门槛，
必须一起改，少改任何一个都只是换个姿势死锁。而"改了会不会把已完成任务误判成等待输入"这个最大顾虑
——LangGraph 源码、内存实验、20-thread 真库逐条对拍（0 翻转、done 未动）三重指向不会：`tasks` 在 END 时必空，这是硬保证。

---

# 第二部分 BUG-E2E2-01：用户可见文本术语泄漏

现场：资源侦察（resource_scout）找不到仓库时降级，降级 message 硬编码内部枚举 `from_scratch`，
经 `make_node_error(...)` 写进 `node_errors`，UI 原样渲染给用户看。

## §8 结论 + 范围裁决

**拟定修法安全，可执行。** 三处 message 是 `_map_resource_scout_result` **函数体内的运行时字面量**，
不在被冻结的 prompt 前缀内，零测试锁定，零字符串匹配消费者。

> **§8.1 范围决定（Maria 2026-07-26 拍板）**：架构师建议扩到 P0+P1 共 19 处同族泄漏一次改完，
> **Maria 选择最小范围——只改 resource_scout 三处，守门测试也只扫这一个模块。**
> 其余 16 处（P1）逐条留档于 §11，另开 TODO 待办，本次不动。
> 理由：Maria 偏好最小改动、反对一次性扩大战场；同族泄漏危害面低于中断死锁，可分批清理。

## §9 传播链复核（已验证）

| 环节 | 位置 | 证据 |
|---|---|---|
| 产生 | `core/nodes/resource_scout.py:448 / 468 / 510` | 三条硬编码 message |
| 封装 | 同文件 `:458 / :478 / :512` → `make_node_error(NODE_NAME, "degraded", message, None)` | 工厂在 `core/errors.py:169-201`，`error_message` **原样透传，无加工** |
| 落 state | `node_errors` 列表 | `core/state.py:169` `error_message: str` |

**渲染点（均不经 `humanize`）**：

| 消费者 | 取值行 | 渲染行 |
|---|---|---|
| 分析进度页（主现场） | `ui/pages/analysis_progress.py:440` | `:450` trigger、`:453/455` content（同函数 `:445` 只对 `node_name` 调 `humanize('node', ...)`，`summary` 直拼） |
| 计划审核页"最近错误" | `ui/pages/plan_review.py:506` | `:512` `st.markdown(f"- {node_disp}：{msg}")` |
| 执行监控页 | `ui/pages/execution_monitor.py:308` → `:514` | `_parse_node_error` 剥离 `[error_category=]` 前缀后展示 |
| 最终 Markdown 报告 | `core/nodes/reporting.py:1032` | `:1038`；再经 `:1141-1142` 写盘 → `ui/pages/result_report.py:507` 全文渲染 |

**结论：message 从产生到用户眼球全程无翻译层**，Maria 的定性成立
（非 LLM 自由文本问题、非 UI 枚举渲染问题）。

**字符串匹配隐患排查（最大风险项，已排除）**：唯一对 `error_message` 做字符串匹配的代码是
`_parse_error_category`（`reporting.py:453-465`），只找 `[error_category=` 前缀 —— 该前缀
**只由 execution 节点写**（`execution.py:1910 / 1998 / 2304`），resource_scout 三条不含该前缀，
改文案对其**零影响**。全仓 grep 三条 message 原文：**只在 `resource_scout.py` 出现**，无第二处引用。

## §10 施工单

### §10.1 改动清单（文案已定稿，照抄，不许自由发挥）

节点中文名采用 `ui/term_map.py:66-72` 既有口径：`resource_scout` = **"资源侦察"**
（不是 TODO 里写的"资源探索"），避免同一折叠条出现两个名字。UI trigger 已带节点名，故 message 内**不再重复节点名**。

| 行 | 新文案（字符串字面量整体替换） |
|---|---|
| `core/nodes/resource_scout.py:448` | `"未取得资源侦察结果，已降级为从零实现"` |
| `:468`（`or` 右侧兜底） | `"资源侦察过程报错，已降级为从零实现"` |
| `:510` | `"未找到可用的开源代码仓库，已降级为从零实现"` |

> 补充事实（留档）：`:468` 是 `_coerce_str(error_msg) or "兜底"`，**LLM 返回的 error 原文优先**。
> 原文若是英文技术句仍会直显 —— 属"LLM 自由文本"，不在本次战场，本次只改兜底串。

### §10.2 红线

**红线 1 —— 冻结令裁决：改这三处不触碰冻结字节，Prompt Cache 基线不受影响（明确放行）**

冻结令覆盖的**确切字节** = `resource_scout.py:79`（`_RESOURCE_SCOUT_SYSTEM_PROMPT_BODY = """…`）
**至 `:121`**（其中 `:95` 拼接 `REPO_QUALITY_SCORING_SECTION`），经 `:165` 进 SystemMessage；
加上进入 tool schema 的工具 docstring（`core/tools/deepxiv_tools.py` / `git_tools.py`，
由 `docs/sprint6/architecture.md:349` P-S6-2 纳管）。

要改的 `:448/468/510` 位于 `_map_resource_scout_result`（`:427-549`）**函数体内**，是节点返回值构造，
**从不进入任何 Message**。守门证据：`tests/test_sprint6_b1_prompt_guards.py:91-152` 全部针对
`mod._RESOURCE_SCOUT_SYSTEM_PROMPT_BODY` 常量断言；唯一扫全模块源码的 `test_tool_assembly_no_pwc_tool`
（`:111-128`）**只取以 `from `/`import ` 开头的行**，与 message 无交集。
→ **裁决：放行，无需重建 Prompt Cache 基线。**（主控已独立磁盘核实。）

**红线 2 —— 枚举值绝对不许动**

> **只改"给人看的句子"，不改"给机器看的值"。**
>
> `resource_scout.py` 中以下 `from_scratch` 出现点**一个字节都不许改**：
> - `:49` `_VALID_STRATEGIES = ("use_repo", "hybrid", "from_scratch")` — 契约常量
> - `:63` schema `enum`、`:93 / :99 / :105 / :114` — prompt 里教 LLM 输出的枚举（且在冻结区）
> - `:277 / :454 / :474 / :494 / :505 / :506` — 写入 `resource_info["resource_strategy"]` 的 **state 值**
>
> 同理 `use_repo` / `hybrid` / `code_only` / `full_success` / `degraded` / `terminate` / `revise_plan` /
> `export_code` 作为**值**出现时一律不动。
>
> **判据一句话：`= "xxx"` / `in (...)` / `enum` 里的是值（不动）；`message = "…xxx…"` 里的是文案（要改）。**

**红线 3 —— 不许连带改动**

1. **不改任何 logger 行**（`resource_scout.py:449/469/511` 等）。前车之鉴：
   `tests/test_sprint3_c1.py:328`、`tests/test_sprint2_b1.py:186/728` 锁的正是 logger 文本。
2. **不改 `[error_category=...]` 前缀**（`execution.py:1910/1998/2304`）—— 机器契约，
   被 `tests/test_sprint3_e2.py:655`、`test_sprint3_c3.py:613`、`test_sprint3_c3_reinforce.py:477/604/620`、
   `test_sprint4_e3.py:394` 等多处锁定。

### §10.3 零测试锁定复验（独立验证，结论成立）

- `tests/test_sprint2_b2.py:232-256`（唯一覆盖该降级路径的用例）只断言 `degraded_errs` **非空**（`:255-256`），不看文本。
- 全 tests 目录 grep `from_scratch` 的 84 处命中**全部是 `resource_strategy`/`code_strategy` 枚举值断言**，无一条断言 message 文本。
- 无任何测试对 `resource_scout` 模块做源码字节哈希。
- **主控已独立 grep 复核：三条 message 原文在 `tests/` 下零命中。**

> **注意（虽本次不改，但守门若日后扩围必须知道）**：`tests/test_sprint2_b1.py:383-384` 有
> `msgs = " ".join(e["error_message"] ...)` + `assert "_en" in msgs and "缺失" in msgs`，
> **真锁 node_errors message**，对应 `paper_analysis.py:411`（含 `*_en`）与 `:487`（含"缺失"）。
> 日后若改这两处文案，必须保留 `*_en` 与"缺失"两个子串。（主控已磁盘核实该断言属实。）

### §10.4 新增防回潮测试（一条，泛化，范围限 resource_scout）

新建 `tests/test_e2e2_message_guard.py`，**单一用例**（参照 `tests/test_sprint4_g1.py:213-217`、
`tests/test_sprint5_t52_ac_matrix.py:304` 的 AST 扫描范式）：

- **扫描对象**：**仅 `core/nodes/resource_scout.py`**（Maria 拍板的最小范围）。
  `ast.parse` 找 `Call(func.id == "make_node_error")`，取第 3 个位置参数（或关键字 `error_message`）；
  `Constant` 取整串，`JoinedStr`（f-string）取其中全部 `Constant` 片段。
- **断言**：字面量片段不得命中黑名单（大小写不敏感、词边界匹配）：
  - 内部枚举：`from_scratch` / `use_repo` / `hybrid`
  - 节点名：`resource_scout`
  - 术语：`ReAct`
- **白名单豁免（唯一一条）**：字面量以 `[error_category=` 开头者整串跳过（机器契约前缀，红线 3-2）。
- **失败信息**：打印 `文件:行号 + 命中词 + 原串`，并提示"用户可见文案禁用内部标识符，请改为通俗中文；
  若为机器契约请加入豁免并说明理由"。
- **扩围预留**：用例内把扫描模块列表写成模块级常量（如 `_GUARDED_MODULES = ("resource_scout",)`），
  日后清理 §11 的 P1 时只需往列表里加名字，不必重写用例。

**不做**（过度工程，一律排除）：UI 层自动守门（需处理"中文（英文）"括注豁免，复杂度不划算）、
文案国际化框架、集中式文案常量模块。

### §10.5 验证

1. 定向回归：`.venv/bin/pytest -q tests/test_sprint2_b2.py tests/test_sprint2_b1.py tests/test_sprint2_b3.py tests/test_analysis_progress.py tests/test_plan_review_logic.py tests/test_sprint5_t35_term_map.py`
2. 冻结令守门：`.venv/bin/pytest -q tests/test_sprint6_b1_prompt_guards.py`（必须绿，佐证红线 1）
3. 人工核对 diff：`git diff -- core/nodes/resource_scout.py | grep -E '_VALID_STRATEGIES|resource_strategy=|SYSTEM_PROMPT'` 必须**无输出**
4. 全量：见 §5.5(5)，与 BUG-E2E2-03 合并一次收口

## §11 同族泄漏留档（本次不改，另开 TODO）

架构师全局扫描产出，**Maria 已决定本次不动**，逐条留档防蒸发。判定基线：项目 S5-09 已确立
**"中文主语 +（英文内部名）括注"= 有意保留的排障锚点**，不算泄漏。

**a) `make_node_error` 硬编码 message（用户可见）**

| 位置 | 原文 | 建议新文案 |
|---|---|---|
| `paper_intake.py:344` | `paper_intake ReAct agent 未返回有效结果` | 论文解析未返回有效结果 |
| `paper_intake.py:357` | `paper_intake 未能获取论文元数据` | 未能获取论文基本信息 |
| `paper_intake.py:374` | `paper_intake 结果缺少 arxiv_id 或 title` | 论文解析结果缺少 arXiv 编号或标题 |
| `paper_analysis.py:440` | `paper_analysis ReAct agent 未返回有效结果` | 论文分析未返回有效结果 |
| `paper_analysis.py:453` | `paper_analysis 未能完成论文分析` | 未能完成论文分析 |
| `paper_analysis.py:487` | `paper_analysis 部分字段缺失: {...}` | 论文分析部分字段缺失: {...}（**"缺失"必须保留**，见 §10.3 注意） |
| `paper_analysis.py:544` | `paper_analysis 前置校验失败：paper_meta 为空` | 论文分析前置校验失败：缺少上游论文信息 |
| `planning.py:630` | `planning ReAct agent 未返回有效结果，降级最简版 plan` | 制定计划未返回有效结果，已降级为最简计划 |
| `planning.py:655` | `planning 计划缺失核心字段 {missing}，标记 degraded` | 复现计划缺少核心内容 {missing}，已标记为降级 |
| `planning.py:844` | `planning ReAct 子图失败: {type}: {exc}` | 制定计划过程失败: {type}: {exc} |
| `coding.py:700` | `coding 未产出代码文件，降级` | 未产出任何代码文件，已标记为降级 |
| `execution.py:241`（`ExecutionFeedback.summary`） | `沙箱环境未准备（agent 未调用 prepare_environment 或执行子图降级）` | 运行环境未准备好（未执行环境准备步骤）。**第 4 参 `fix_hint` 不动**（它是 coder 语料） |
| `execution.py:2299-2300` | `code_output_dir 缺失（上游未产出代码目录）` / `检查 coding 节点是否产出代码` | 缺少代码目录（上游未产出代码） / 请确认代码生成阶段是否产出文件 |

**b) UI 硬编码字符串（`st.*` 直接可见）**

| 位置 | 泄漏词 | 建议新文案 |
|---|---|---|
| `execution_monitor.py:909` | `LangGraph` / `checkpoint` | （系统会从上次保存的进度点重新执行，属正常机制）。 |
| `execution_monitor.py:415` | `checkpoint` | 任务进度已保存，可稍后查询。 |
| `execution_monitor.py:955` | `checkpoint` | 等待执行启动 / 加载中…：正在保存任务进度，页面将自动刷新。 |
| `analysis_progress.py:514` | `checkpoint` | 你在计划审核页主动终止了本次复现任务。任务进度已保存，可稍后查询。 |
| `analysis_progress.py:585` | `checkpoint` | 正在保存任务进度，页面将自动刷新。 |
| `task_list.py:97 / 117 / 121` | `checkpoints` | 列出全部历史任务（新任务在前）… / 任务列表读取失败，请稍后重试。 / 暂无任务：还没有发起过任何复现任务。 |

**c) 明确"不改"（只进 logger / 内部，别扩大战场）**

全部 `logger.*` 字面量、全部 docstring / 注释、`core/plan_checks.py:150/159/170`（已合规）、
`ui/components/llm_config_form.py:318`（`api_key` 是用户实际要填的变量名，必要技术信息）、
`execution.py:328`（`import 错误（缺包 / 模块路径错误）`）、`:355`（`根据 stderr 尾部…`）——通用技术词且有中文说明。

**d) 需 Maria 单独拍板（不阻塞任何东西）**

1. `Sandbox` / `Agent 活动流`（`execution_monitor.py:141/463/466/486/840/857`）是否算可接受的产品词？
2. Markdown 报告里的枚举值（`reporting.py:486/987/1033/1071/1035/974-975/576/600` 的
   `degraded` / `export_code` / `no_metrics` / 裸节点名）是否引入 `humanize` 治理？
   —— 这是 sp6 MF-4 在报告侧的遗留同族问题（UI 已治、报告未治），需在 reporting 引入 `humanize` 依赖，
   而 `reporting.py` 有 import 白名单守门用例（`tests/test_sprint5_t33_conclusion.py:432`），
   属独立设计决策，**建议单开 PRD**。
3. 页面上展示凭证 `purpose_key` 原值（`execution_monitor.py:790`）是否保留？（键名对用户有实义）

