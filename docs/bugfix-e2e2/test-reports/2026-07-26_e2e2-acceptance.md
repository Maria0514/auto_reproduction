# 测试执行报告 · BUG-E2E2-03 / BUG-E2E2-01 独立验收

- **日期**：2026-07-26 09:51（本地时区）
- **执行人**：@测试工程师代理（独立验收，非开发自测复述）
- **范围**：`docs/bugfix-e2e2/architecture.md` 第一部分（BUG-E2E2-03 动态 interrupt 判定死角）
  + 第二部分（BUG-E2E2-01 用户可见文本术语泄漏）
- **触发原因**：两个纯 bug 修复落地后的独立验收 —— 验红真实性核查 + 架构 §7 残留项补测 + 覆盖矩阵
- **commit**：`8d37fe9`（两处修复均为未提交工作树改动）
- **接手基线**：`app.py` md5 `de883a7b0ab70256197a65fa5caa1f63`；
  `core/nodes/resource_scout.py` md5 `393ce3cff4d2bf33b7ebfb2d5efd4bb2`
- **收工核对**：两文件 `diff` 与接手时**逐字节一致**（所有临时验红改动均已还原）

## 一句话结论

**修复正确、验红真实、开发的那处断言修正合理；但架构 §7-1 的"高置信"判断被我实测推翻**——
agent 工具路径（`interaction_tools.py:175`）**不产生** BUG 形态，真实受影响面比架构文档写的少一条。
另发现 3 处覆盖缺口（均非阻断），已在 §5 逐条列明。

---

## 1. 执行范围

| # | 命令 | 次数 |
|---|---|---|
| 基线 / 收口 | `.venv/bin/pytest -q -m "not e2e and not browser" -p no:cacheprovider` | 2 |
| 逐处验红（每处单独改回 → 跑**全量** → 单独还原） | 同上 | 5 |
| 五处同时改回验红 | 同上 | 1 |
| 红线 §5.2-2 探针（`_has_interrupt` 改读顶层字段） | 同上 + 定向 | 2 |
| 第二部分文案验红（`resource_scout.py:510` 改回泄漏版） | `pytest -q tests/test_e2e2_message_guard.py` | 1 |
| 新增补测稳定性 | `pytest -q tests/test_e2e2_acceptance_gaps.py` ×3 | 3 |
| 20-thread 真库新旧对拍 | `/tmp` 副本上的离线脚本（**不改 `app.py`**） | 1 |

**是否包含 e2e：否。** 全程 `-m "not e2e and not browser"`，零 LLM / 零网络 / 零 deepxiv 配额。

**只读纪律**：20-thread 真库先 `md5sum` 原件 → 复制到 `/tmp` → 只在副本上跑 `get_checkpointer()`（含
`PRAGMA journal_mode=WAL` 写操作）→ 收工 `md5sum -c` 复核原件 **OK** → 删除 `/tmp` 副本。

## 2. 结果摘要

| 项 | 接手基线 | 收工 |
|---|---|---|
| 通过 | 2035 | **2044**（+9 = 本次新增补测） |
| 失败 | 0 | **0** |
| 跳过 | 25 | 25（未新增） |
| deselect | 58 | 58 |
| 警告 | 3 | 3（未新增；均为 langgraph / pydantic 上游 Deprecation，与本次改动无关） |
| 耗时 | 60.81s | 61.04s |

---

## 3. ① 独立验红（不信开发报告，自己改回旧实现逐处实测）

方法：用 Edit 精确把生产代码改回旧实现（**不用 `git stash`**，防丢并行改动），**每处单独改回 → 跑全量 →
单独还原并 md5 核对**。全量只需 61s，故五次验红全部走**全量**而非定向子集，把"某处改动无测试保护"的判定
覆盖到全仓 2035 条用例。

| # | 改回的位置 | 改回内容 | 红几条 | 具体用例 |
|---|---|---|---|---|
| 1 | `is_interrupted` 返回式 | `bool(snapshot and self._has_interrupt(snapshot))` → `bool(snapshot and snapshot.next and self._has_interrupt(snapshot))` | **3** | `test_app_controller.py::test_e2e2_is_interrupted_true_when_next_empty_with_interrupt`<br>`test_e2e2_interrupt_gate_fix.py::test_e2e2_l1_1_is_interrupted_true_when_next_empty_with_interrupt`<br>`test_sprint5_t22_coding_gate.py::test_e2e2_second_serial_interrupt_snapshot_shape_and_controller_judgement` |
| 2 | `is_finished` 返回式 | `not snapshot.next and not self._has_interrupt(snapshot)` → `not snapshot.next` | **2** | `test_e2e2_interrupt_gate_fix.py::test_e2e2_l1_2_is_finished_false_when_next_empty_with_interrupt`<br>`test_sprint5_t22_coding_gate.py::test_e2e2_second_serial_interrupt_is_finished_false` |
| 3 | `get_interrupt_payload` 门槛 | `if not snapshot:` → `if not (snapshot and snapshot.next):` | **2** | `test_e2e2_interrupt_gate_fix.py::test_e2e2_l1_3_get_interrupt_payload_available_when_next_empty`<br>`test_sprint5_t22_coding_gate.py::test_e2e2_second_serial_interrupt_payload_available` |
| 4 | `get_interrupt_token` 门槛 | 同上 | **3** | `test_e2e2_interrupt_gate_fix.py::test_e2e2_l1_4_get_interrupt_token_available_when_next_empty`<br>`test_sprint5_t22_coding_gate.py::test_e2e2_second_serial_interrupt_token_available`<br>`test_sprint6_s6_01_controller.py::test_e2e2_token_available_when_next_empty_with_interrupt` |
| 5 | `derive_task_status` R5 块 | R5 移回 R4 之后 | **4** | `test_e2e2_interrupt_gate_fix.py::test_e2e2_l1_5_derive_task_status_awaiting_when_next_empty_with_interrupt`<br>`test_sprint5_t22_coding_gate.py::test_e2e2_second_serial_interrupt_derive_status_awaiting`<br>`test_sprint6_s6_07_task_status.py::test_e2e2_r5_wins_over_r4_when_next_empty`<br>`test_sprint6_s6_07_task_status.py::test_e2e2_r5_wins_over_r4a_done_when_next_empty` |
| — | **五处同时改回** | — | **14** | 上述五组的并集，**互不重叠**，条数逐字吻合 |

### 3.1 裁决

- **五处改动全部有测试保护，无"零保护"改动点。** 最少的两处（`is_finished` / `get_interrupt_payload`）
  各有 2 条守门，且分布在「Fake 判定层」+「真图层」两层，不是同一层重复计数。
- **开发声明的"未修复时红 14 条"属实**，我独立复现到**恰好 14**，用例名逐条对得上。
- **既有用例（改动前就存在的）零命中。** 五次验红的失败集合里**没有一条**是历史用例
  —— 独立坐实架构 §4.2「现有测试对这条改动完全无感，零红 ≠ 有保护」的结论，也说明
  §5.4 新增用例确实是唯一收口门。
- **`is_finished` 的保护是四处里最薄的一处**（仅 2 条，且都是本次新增）：它唯一的生产调用点
  `execution_monitor.py:1018` 被 case⑤ 提前 return 遮蔽，页面级用例天然打不到。可接受，但登记在案。

### 3.2 还原核验

`md5sum app.py` = `de883a7b0ab70256197a65fa5caa1f63`，与验红前一致；`diff /tmp/acc_app_baseline.py app.py` 无输出。

## 4. ② 独立核查开发那处"可疑修正"（`test_e2e2_has_interrupt_unchanged_contract`）

开发承认：它第一次验红出 **15 failed**，多的一条是自己新加的守门用例，原断言写的是源码文本
`assert ".interrupts" in src`，后改成行为断言 `assert not hasattr(_bug_snapshot(), "interrupts")`。

### (a) 原断言是否真的与生产 bug 无关 —— **是，与 bug 无关，改掉合理**

磁盘取证：

```
修复前（git show HEAD:app.py）：全文件字面 ".interrupts" 出现 0 次
修复后（工作树 app.py）：      出现 2 次，均在新增 docstring（:402 / :410），可执行代码 0 次
_has_interrupt 函数体：        修复前后逐字节相同（红线 §5.2-2 被遵守），
                               实现是 getattr(task, "interrupts", None)，不含字面 ".interrupts"
```

原断言若读的是 `_has_interrupt` 的源码（开发的说法），则该函数体前后一字未改 ⇒ 断言前后**恒红**，
不具备任何判别力，属开发自己写错的断言。**不是"为了凑 14 条而弱化"，是修掉一条本来就写错的断言。**

> 需要留一句提醒：若原断言读的是**整个 `app.py`** 而非 `_has_interrupt`，那它修复前红、修复后绿
> ——会"意外地"有判别力，但判别的是**新写的 docstring 文本**而非行为，属误导性守门，同样该改。
> 两种情形下"改掉"都是对的。原始代码未进版本控制，我无法直接读到，此点如实标注为**不可磁盘核实**。

### (b) 新断言是否真能守住红线 2 —— **能守住，但守门的不是它改的那一行**

实测：把 `_has_interrupt` 临时改成 `return bool(getattr(snapshot, "interrupts", None))`（即改读顶层字段），
跑全量：

```
12 failed, 2023 passed —— 其中包括 tests/test_e2e2_interrupt_gate_fix.py::test_e2e2_has_interrupt_unchanged_contract
```

单跑该用例看 traceback，红在**第 197 行的第 1 条断言**：

```
>       assert GraphController._has_interrupt(_bug_snapshot()) is True
E       assert False is True
tests/test_e2e2_interrupt_gate_fix.py:197: AssertionError
```

**所以：守门有效，红线 2 守得住 —— 但生效的是 `:197`，不是开发改的 `:203`。**

`:203` 的 `assert not hasattr(_bug_snapshot(), "interrupts")` 是对**本文件自己定义的 `_FakeSnapshot`**
（`:53-57`）的重言式，任何生产代码改动都不可能让它变红。它的真实作用是**夹具不变量守门**：
防止未来有人给 `_FakeSnapshot` 补上 `interrupts` 属性、从而把 `:197` 静默解除武装。这个作用是成立的，
但注释/docstring 把它写得像是主守门，容易误导。

**结论：修正合理，未掩盖任何东西；但守门写法偏弱。**

### (c) 我给出的更强写法（已作为新用例落地，未改动开发的任何断言）

`tests/test_e2e2_acceptance_gaps.py::test_e2e2_acc_has_interrupt_anchors_on_tasks_not_top_level_field`
用一个**同时定义 `tasks` 与顶层 `interrupts`、且两者刻意取反**的陷阱替身，正反双向钉死判定口径：

| 方向 | 构造 | 断言 | 杀死的错误实现 |
|---|---|---|---|
| (a) | tasks 有 interrupt ∧ 顶层 `interrupts=()` | 必须 `True` | 改读顶层字段 |
| (b) | tasks 无 interrupt ∧ 顶层 `interrupts=(...)` | 必须 `False` | 改读顶层字段（反向，既有守门**完全未覆盖**） |

不依赖"替身缺失该属性"，即使有人给所有替身补上 `interrupts` 也照样红。实测在 `_has_interrupt`
改读顶层字段时**确实变红**。

> 附带发现（值得记一笔）：`_has_interrupt` 改读顶层字段时，`test_sprint5_t22_coding_gate.py` 的
> 5 条真图用例**全部保持绿**——因为真实 `StateSnapshot` 顶层 `interrupts` 与 `tasks[*].interrupts` 同时非空。
> **真图层天然测不出这条红线，只有 Fake 替身层能测。** 这反过来印证了架构 §3.5 保留 Fake 层的必要性。

## 5. ③ 补测（架构 §7 标"未验证"项逐条闭合）

新增**单一文件** `tests/test_e2e2_acceptance_gaps.py`（9 条用例，全离线，连跑 3 次稳定：`9 passed / 0.76s`）。
**只加不改**：未修改任何既有用例的断言、未重命名任何既有测试函数（已用 `git show HEAD:<file>` 逐文件
比对函数名集合，5 个被改测试文件**删除/改名 = 0**）。

### 5.1 §7-1 agent 工具路径 —— **架构结论被实测推翻（本轮最重要的发现）**

架构 §1.3「真实受影响面」表把 `core/tools/interaction_tools.py:175`（agent 一次执行内第 2 次调
`request_user_input`）判为「**是**（未直接取证，高置信）」。

用 `test_sprint4_b2_interrupt3_idempotency.py` 的父图 + **真实 `create_react_subgraph`** harness
（与生产 `_make_react_wrapper` 同拓扑：`react_base.py:828` 建子图 / `:873` `subgraph.invoke`）
造两轮 `request_user_input`，实测两种子形态：

| 子形态 | 父图 `get_state().next` | `_has_interrupt` | 是否 BUG 形态 |
|---|---|---|---|
| 两个**独立轮次**各调一次 RUI | `('agent',)` **非空** | True | **否** |
| **同一批 tool_calls** 里放两个 RUI | `('agent',)` **非空** | True | **否** |

**机制（实测 pending_writes 铁证）：**

```
agent 工具路径 pause#2：  [('0aa27235', '__interrupt__'), ('00000000', '__resume__')]
                          ↑ 父 task 有 interrupt        ↑ resume 只挂在 NULL_TASK 哨兵上
coding 凭证 gate pause#2：[('4529135b', '__interrupt__'), ('00000000', '__resume__'),
                           ('4529135b', '__resume__')]   ← 父 task 自己也有 __resume__ ⇒ 被踢出 next
```

- coding gate：`interrupt()` 在**父节点函数体**内串行调用两次，resume 记为**父 task 自己的 `__resume__` write**
  → `main.py:1138` 把有 writes 的 task 踢出 `next` → `next=()`；
- agent 路径：`interrupt()` 在**子图**内 raise，resume 消费发生在子图命名空间，父 task 的 writes **恒空**
  → 留在 `next` 里。且子图按 checkpoint 精确恢复到 `tool_executor`（`test_sprint4_b2_*` 已证前序不重放），
  故父节点每次执行体内**只发生一次** interrupt。同批 tool_calls 的情形里两次 interrupt 的 `id` 相同
  （实测 `e8ce137222…` 两次一致，正是 sprint6 §S6-01 记的"同执行内串行 → id 不变"），**仍不清空父 `next`**。

**裁决：架构 §1.3 表的这一行应从「是」改为「否」。** 真实受影响面收敛为**唯一一条机制**：
父节点函数体内串行 `interrupt()`（= coding 凭证 gate 及其"非法 resume 后重问同一项"变体）。

这**不削弱**修复的必要性（现场 `task-435baf71f4cf` 实锤仍在），但纠正了受影响面，防止后来者据错误的表扩大改动面。
两条用例（各 2 个参数化）把这条**反向钉死**，并同时断言该形态下五个判定仍全对（面板照弹、状态 `awaiting`）。

### 5.2 §7-4 planning revise / switch_repo —— **实测确认幸免（反向证据）**

用 `planning` 单节点 + **真实 `_route_after_planning`** 三路条件边（含 self 自环）建离线真图：

| 场景 | `next` | pending_writes | 判定 |
|---|---|---|---|
| 首问态 | `('planning',)` | `[__interrupt__]` | awaiting |
| `revise` 自环重入后第 2 次 interrupt | `('planning',)` **非空** | `[__interrupt__]`，**无 `__resume__`** | awaiting |
| `switch_repo` 自环重入后 | `('planning',)` **非空** | `[__interrupt__]`，**无 `__resume__`** | awaiting |

架构源码层判断（`planning.py:907-940` 走 self-loop 产生新 checkpoint）**成立**，无需额外特判。

### 5.3 §3.6 "消费 resume 后 superstep 未提交就崩" —— **实测闭合（架构原标"已论证自愈，未实测"）**

模拟手段：自定义 `_SimulatedProcessKill(BaseException)` 在 gate 放行后的 ReAct 阶段抛出
（LangGraph 只捕 `Exception`，`BaseException` 直穿**不写 `__error__`**，等价于进程被 kill；
刻意不用 `KeyboardInterrupt`——那会中断整个 pytest 会话）。

**崩溃后形态（首次实测，与 §3.6 描述完全吻合）：**

```
next: ()            tasks: [('coding', 1)]        _has_interrupt: True
pending_writes: [('d6b54acf','__interrupt__',…), ('00000000','__resume__',{'value':'VAL-B'}),
                 ('d6b54acf','__resume__',[{'value':'VAL-A','remember':False}])]
新判定 → awaiting（可自愈）   ｜   旧判定 → no_report（死胡同）
重弹的问题 = 上一个已答过的 B（§3.6 明确接受的已知边界，已如实钉进断言）
```

**自愈实测（额外清空进程内 `_SESSION_SECRETS`，忠实模拟真进程重启）：** 用户再答一次后
—— 节点放行完成、无二次暂停、ReAct 真正执行（stub calls = 2）、且**零串位**：

- `A` 由 task 级 `__resume__` 列表**按 interrupt 调用序对位**补回会话层（`_SESSION_SECRETS[A] == VAL_A`）；
- `B` 保持崩溃前已落盘的**原值**（`.secrets[B].value == VAL_B`），未被"再答一次"的值覆盖；
- `A`（不记住）绝不落盘；`credential_degradations` 为空，无虚假降级标记。

**架构 §3.6 的"行为等价于一个继续执行按钮，自愈"逐字成立**，可从"未实测"升级为"已实测闭合"。

### 5.4 新增用例的敏感度（诚实标注）

在"五处同时改回旧实现"下重跑本文件：**1 red / 8 passed**。

| 用例 | 未修复时 | 说明 |
|---|---|---|
| `..._crash_after_resume_consumed_is_judged_awaiting_not_no_report` | **红** | 真 BUG 形态，是有效收口门（把全套验红总数从 14 抬到 **15**） |
| `..._agent_tool_path_*`（4 条）/ `..._planning_selfloop_*`（2 条） | 绿 | **设计如此**：它们是反向证据，描述"本来就没坏"的形态，价值在于**防未来蔓延**，不在于验红 |
| `..._crash_corner_self_heals_*` | 绿 | 只测行为自愈，不测判定式 |
| `..._has_interrupt_anchors_on_tasks_not_top_level_field` | 绿 | 守的是红线 §5.2-2，与五处改动正交；改 `_has_interrupt` 时才红（已实测） |

---

## 6. ④ 验收覆盖矩阵

### 6.1 §5.1 精确改动清单（8 处）

| 改动 | 落地？ | 测试覆盖 | 判定 |
|---|---|---|---|
| 1 `is_interrupted` 去 `next` 门槛 + docstring | ✅ 逐字 | 3 条（验红实证） | **已覆盖** |
| 2 `get_interrupt_payload:437` 门槛 + `:432` docstring | ✅ | 2 条 | **已覆盖** |
| 3 `get_interrupt_token:461` 门槛 + docstring 追加 | ✅ | 3 条 | **已覆盖** |
| 4 `is_finished` 加 `not _has_interrupt` 合取项 | ✅ | 2 条（最薄，见 §3.1） | **已覆盖** |
| 5 `derive_task_status` R5 上移 + docstring 优先级改写 | ✅ | 4 条 | **已覆盖** |
| 6 `TASK_STATUS_*` 行末注释 | ✅ | 无（注释，不可测） | **缺口·可接受** |
| 7 模块 docstring `:19-22` | ✅ | 无 | **缺口·可接受** |
| 8 `get_phase` docstring 追加 | ✅ 且实现零改动 | `test_sprint6_s6_01_controller.py:310-343` 四条锁 `get_phase` 行为 | **实现已覆盖 / 文案不可测** |

> 三处纯文案改动无自动化守门，属固有限制（架构 §5.2-11 自己也承认"没有静态守门能阻止把 `next` 门槛加回去"）。
> 但**行为**有守门：谁把门槛加回去，§3 表里那 14 条立刻红。

### 6.2 §5.2 红线（11 条）

| # | 红线 | 遵守？ | 守门 | 判定 |
|---|---|---|---|---|
| 1 | 不改方法签名 | ✅ | `test_sprint3_e1.py::test_cp_e1_1_existing_method_signatures_unchanged` | **部分覆盖**：黄金签名集只含 `is_interrupted` / `get_interrupt_payload` 等 6 个 sp2 方法，**`is_finished` / `get_interrupt_token` 只被"方法存在性"冻结（`:107-108`），签名未冻结** → 见缺口 G1 |
| 2 | 不改 `_has_interrupt` 口径 | ✅ 函数体逐字节未变 | `test_e2e2_has_interrupt_unchanged_contract:197` + 我新增的正反双向陷阱 | **已覆盖（本轮加强）** |
| 3 | 不引入第二套读栈（`get_state_history` / `checkpoint_id` / `subgraphs=True`） | ✅ `app.py` 仅 docstring 提及，代码零命中 | **无任何测试守门** | **缺口 G2** |
| 4 | 零 state 字段新增 + interrupt payload 键集冻结 | ✅ | `test_sprint4_b1.py:136/420`、`test_sprint4_e3.py:529`、`test_sprint6_b2.py:712`；我新增 agent 路径 `allow_degrade not in payload` | **已覆盖** |
| 5 | 不动 R2/R3 最高优先级 | ✅ | `test_e2e2_l1_7_*`、`test_e2e2_l1_7b_*`、`test_cp_4_3_1_priority_order_short_circuit` | **已覆盖** |
| 6 | 不动 `get_phase` 实现 / `ui/` case 分发顺序 | ✅ | `s6_01_controller:310-343`（get_phase）+ 新增 `test_e2e2_case5_wins_over_case6bis_when_finished_and_interrupted`（分发顺序） | **已覆盖** |
| 7 | 不新增 55MB 级 fixture | ✅ | `git status --porcelain tests/fixtures/` 空 | **已核实** |
| 8 | `app.py` 本轮单收口 | ✅ | 流程纪律，不可测 | — |
| 9 | 纯 bug 不走 PRD，回归按功能变更等级 | ✅ 已跑全量 | — | — |
| 10 | 不得重命名既有测试函数 | ✅ **5 个文件删除/改名 = 0**（逐文件函数名集合对拍） | `test_sprint5_t52_ac_matrix.py` 全绿 | **已覆盖** |
| 11 | 无源码字样守门 + 防回潮 docstring 逐字写入 | ✅ `grep -c "严禁再引入" app.py` = 1 | 无（架构自认无法静态守门） | **缺口·架构已承认** |

### 6.3 §5.4 新增测试设计（L1 / L2 / L3）

| 设计项 | 交付？ | 实测验红 | 判定 |
|---|---|---|---|
| L1-1 ~ L1-5（判定层五条） | ✅ 全部落在 `test_e2e2_interrupt_gate_fix.py` | 各自 1 条，合计 5 | **符合设计** |
| L1-6 / L1-7 反向安全 | ✅（另加 L1-7b cancelled + 空快照防御两条，超交付） | 改动前后均绿 | **符合设计** |
| L2 真图层五条（`test_sprint5_t22_coding_gate.py` 姊妹用例） | ✅ 5 条，复用 `_build_gate_graph` / `_two_missing_state` / `_ReactStub` | 合计 5 | **符合设计**；形态断言 `snap.next == ()` 是全套唯一能防"LangGraph 升级后形态变化"的锚 |
| L2 可选扩展（agent 路径闭合 §7-1） | ❌ 开发未做 | — | **本次由我补齐，且结论与设计预期相反**（见 §5.1） |
| L3 `s6_01_controller` token 用例 + `:126` 注释 | ✅ | 红 1 | **符合设计** |
| L3 `s5_08_routing` 页面级 case⑤ vs case⑥bis + `:438` docstring | ✅ | 改动前后均绿（设计即如此） | **符合设计** |
| L3 `test_app_controller.py:247` docstring 改因果 + 姊妹用例 | ✅ | 红 1 | **符合设计** |
| L3 `s6_07_task_status` 两条 + `:113-116` docstring | ✅ | 红 2 | **符合设计** |
| **合计验红 14 条** | ✅ | **独立复现 = 恰好 14，用例名逐条吻合** | **属实** |

### 6.4 §5.5(6) 20-thread 反向风险探针

架构 §5.3 第 5 步要求"复核 20-thread 矩阵仍为 `{awaiting:10, interrupted:4, no_report:3, done:1, failed:1, cancelled:1}`，
任何 `done→awaiting` 立即停手上报"。

我在 `/tmp` 副本上用**本地重写的旧规则函数**（不改 `app.py`）对 20 个 thread 逐条新旧对拍：

```
旧规则分布: {'awaiting': 10, 'cancelled': 1, 'done': 1, 'failed': 1, 'interrupted': 4, 'no_report': 3}
新规则分布: {'awaiting': 10, 'cancelled': 1, 'done': 1, 'failed': 1, 'interrupted': 4, 'no_report': 3}
翻转数: 0      done→awaiting: False      next=() 且 tasks 含 interrupt 的 thread 数: 0
```

**架构 §3.4 结论独立复现，反向风险确认不成立。**

**但**：`test_cp_4_3_3_list_threads_20_real_db` 只断言 `len==20` + 三个状态存在，**不断言分布**
→ 这个"0 翻转"命门**没有任何自动化守门**（且该 fixture 被 `.gitignore` 排除，CI 本来就跑不到）→ 缺口 G3。

### 6.5 第二部分 §10.1 ~ §10.4（BUG-E2E2-01）

| 项 | 状态 | 证据 |
|---|---|---|
| §10.1 三处文案逐字替换（`:448` / `:468` / `:510`） | ✅ 与施工单**逐字一致** | `git diff` 核对 |
| §10.2 红线 1（不触冻结字节） | ✅ | `tests/test_sprint6_b1_prompt_guards.py` 全绿（全量内） |
| §10.2 红线 2（枚举值一字不动） | ✅ | `git diff` 中 `_VALID_STRATEGIES` / `resource_strategy=` / `SYSTEM_PROMPT` **零命中**；全仓 84 处枚举值断言全绿 |
| §10.2 红线 3（不改 logger / 不改 `[error_category=` 前缀） | ✅ | diff 显示 `logger.warning` 行只有参数不变的原样保留；`test_sprint3_c1.py:328` / `test_sprint2_b1.py:186/728` 全绿 |
| §10.3 零测试锁定复验 | ✅ | 全量 0 红即证 |
| §10.4 AST 守门（新建 `tests/test_e2e2_message_guard.py`） | ✅ 且**实测有效** | 见下 |

**§10.4 守门有效性 —— 我独立验红**：把 `resource_scout.py:510` 改回泄漏版 `"resource_scout 未找到可用代码仓库，降级 from_scratch"`：

```
E  core/nodes/resource_scout.py:510 命中 ['from_scratch', 'resource_scout']
   -> 'resource_scout 未找到可用代码仓库，降级 from_scratch'
FAILED tests/test_e2e2_message_guard.py::test_node_error_messages_have_no_internal_jargon
```

**报错信息含文件:行号 + 命中词 + 原串，符合 §10.4 要求。** 覆盖完整性核查：守门实际扫到
**3 条** message 字面量，与文件内 `make_node_error` 调用数 **3** 完全对齐，三处改动**全部在守门射程内**。
`_GUARDED_MODULES` 扩围钩子已按 §10.4 预留。

---

## 7. 失败排查

**本次全量执行 0 失败。** 所有出现过的红都是我**主动注入**的验红，注入源与还原均记录在 §3 / §4。

唯一需要排查判定的是"开发那条 15→14 的断言修正"，判定过程见 §4：
**判定 = 开发自身断言写错，与生产 bug 无关，修正合理；但新写法偏弱，已由我补一条更强的正反双向守门（只加不改）。**

## 8. 发现清单（按严重度）

| ID | 严重度 | 内容 | 建议 |
|---|---|---|---|
| **F1** | **中·文档失真** | 架构 §1.3「真实受影响面」表把 `interaction_tools.py:175`（agent 路径）判为「是（高置信）」，**实测为否**（父图 `next` 保持非空，两种子形态均如此）。受影响面应收敛为唯一一条：父节点函数体内串行 `interrupt()` | 架构师更新 §1.3 表与 §7-1 行；已由 `test_e2e2_acceptance_gaps.py` 反向钉死 |
| **F2** | 低·守门偏弱 | `test_e2e2_has_interrupt_unchanged_contract:203` 是对自家替身的重言式，真正守门的是 `:197`；注释表述容易误导 | 已由我新增正反双向陷阱用例覆盖（未动开发的断言）。开发可择机把 `:203` 的注释表述改准 |
| **G1** | 低·覆盖缺口 | `is_finished` / `get_interrupt_token` 只被"方法存在性"冻结，**签名未进黄金签名集** | 可选：把两者加进 `test_sprint3_e1.py::_GOLDEN_SIGNATURES` |
| **G2** | 低·覆盖缺口 | 红线 §5.2-3（禁 `get_state_history` / `checkpoint_id` / `subgraphs=True`）**全仓无守门** | 可选：仿 `test_e2e2_message_guard.py` 的 AST 范式加一条扫 `app.py` 的守门 |
| **G3** | 低·覆盖缺口 | 20-thread「0 翻转 / 分布不变」这个反向风险命门**无断言守护**（现有用例只断言存在性），且 fixture 不进 CI | 已由 L1-6 / L1-7 离线反向安全用例兜底，接受；如需强化可把分布写成断言（仅本机有效） |
| **G4** | 低 | `test_e2e2_message_guard.py` 的元守门 `assert literals` 只要求**≥1** 条 —— 若三处 message 里有两处被改成非字面量（如从常量表取），守门会**部分失效而不报** | 可选：改成 `assert len(literals) >= 3` 或按 `make_node_error` 调用数对齐 |
| **G5** | 低·可接受 | §5.1 改动 6/7/8 三处纯文案 + 红线 §5.2-11 防回潮 docstring 无静态守门 | 架构已自认不可静态守；行为层由 14 条用例兜底，接受 |

**无阻断级问题，无需回退任何改动。**

## 9. 后续动作

- **需 Maria / 架构师决策**：F1 的架构文档勘误（§1.3 表 + §7-1 行）。
- **未做（须单独授权）**：架构 §5.3 第 6 步真实浏览器 e2e 复走"两项凭证串行 gate"闭环
  —— 耗 deepxiv 日配额，本次全程未跑任何 e2e。这也是 §7-2「生产库 `task-435baf71f4cf` 挂回看面板是否弹出」
  的最终验收，**目前仍未验证**。
- **TODO 更新**：按分工由主控收口（本次我只新增本报告 + 一个测试文件，未改 `docs/` 下任何既有文件）。
- 下次跑测试的触发条件：Maria 授权真跑窗口后，先跑 §5.5(2)(3)(4) 定向回归再进 e2e。

## 10. 本次产出文件

| 路径 | 类型 |
|---|---|
| `/data/myproj/auto_reproduction/tests/test_e2e2_acceptance_gaps.py` | 新增（9 条补测用例） |
| `/data/myproj/auto_reproduction/docs/bugfix-e2e2/test-reports/2026-07-26_e2e2-acceptance.md` | 本报告 |

**未改动任何生产代码，未改动任何既有测试文件，未 commit / add / push。**
