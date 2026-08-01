# 测试执行报告 - s712-shell-metachars（S7-12 补测 / CP-8.1-11 兑现）

- **日期**：2026-08-01 07:20–07:50（本地时区）
- **执行人**：@测试工程师代理
- **Sprint**：sprint7（批次 8 / T-S7-8-1）
- **触发原因**：S7-12 生产代码已交付但 **`tests/` 下一条用例都没有** —— 开发批次的
  CP-8.1-9「回归 2299 前后完全一致」只证明零退化、**不证明防线在守**（dev-plan §58
  CP-8.1-11 原文点名交测试工程师补测）。
- **commit**：`c480990`（工作树含 S7-12 未提交改动 + **他人 in-flight 的 S7-11 改动**，见"重大发现①"）

---

## 执行范围

- **新增文件**：`tests/test_sprint7_s712_shell_metachars.py`（799 行，31 个测试函数 →
  参数化后 **112 条用例**）
- 命令：
  - `.venv/bin/pytest tests/test_sprint7_s712_shell_metachars.py -q -p no:randomly`
  - `.venv/bin/pytest tests/test_sprint7_s712_shell_metachars.py -q`（默认随机顺序）
  - `.venv/bin/pytest -q -p no:randomly`（全量回归）
  - 命门验红：两处拦截各改 `if False and …` 后单跑本文件
- **是否包含 e2e**：**否**。`pytest --collect-only -q -m e2e` → `no tests collected
  (112 deselected)`，本文件零 e2e。全程未设 `LANGSMITH_TRACING_IN_TESTS`，
  **零 LangSmith 上报**（`tests/conftest.py:42` 的硬关闭未被绕过）。
- **未跑真实端到端**、**未 commit / push**、**生产代码零净改动**（验红后 sha256 校验还原）。

---

## 结果摘要

| 口径 | 结果 |
|---|---|
| 本文件单跑（固定顺序） | **101 passed, 11 xfailed** in 0.97s |
| 本文件单跑（默认随机顺序） | **101 passed, 11 xfailed** in 0.97s（顺序无关） |
| 单条独立可跑抽查 | 2 passed（`::test_cp_8_1_3_ledger_count_is_unchanged_across_a_rejection` 等） |
| 全量回归 | **9 failed, 2391 passed, 25 skipped, 46 deselected, 11 xfailed, 3 warnings in 141.64s (0:02:21)** |
| 警告 | 3（均为既有项目级：langgraph `LangChainPendingDeprecationWarning` ×1、pydantic `PydanticDeprecatedSince20` ×2；**非本次引入**，与开发批次 CP-8.1-9 记录的 3 warnings 数量一致） |

### 账目对平

```
开发批次基线（CP-8.1-9）：2299 passed
本次新增           ：+101 passed  +11 xfailed
                     ------------------------
预期 passed+failed  ：2400
实测 passed+failed  ：2391 + 9 = 2400   ✅ 逐格对平
skipped / deselected：25 / 46（与基线**完全一致，零变化**）
```

⇒ **总数一格没差**，差异全部落在"其中 9 条从 passed 变成 failed"，而这 9 条**没有一条
在本文件内**（归因见下）。

---

## 失败排查

### 9 条失败**全部属于他人 in-flight 的 S7-11（批次 7）改动，与 S7-12 及本次补测无关**

| 失败用例 | 文件 |
|---|---|
| `test_cp_c3_1_importable_and_local_objects` | `tests/test_sprint3_c3.py` |
| `test_cp_g2_2_sentinel_zero_plaintext_in_code_report_caplog` | `tests/test_sprint4_e2e.py` |
| `test_cp_e3_1_deduction_rounds_plus_metric_calls` | `tests/test_sprint4_e3.py` |
| `test_cp_e3_1_deduction_rounds_only_no_metric_call` | `tests/test_sprint4_e3.py` |
| `test_cp_e3_3_success_from_real_exit_codes_and_metrics` | `tests/test_sprint4_e3.py` |
| `test_le401_fix_credential_inline_retry_success_single_round` | `tests/test_sprint4_e4_regression_gate.py` |
| `test_le401_fix_inline_retry_without_interrupt_success` | `tests/test_sprint4_e4_regression_gate.py` |
| `test_cp351_covers_error_category_enum_plus_degraded_literal` | `tests/test_sprint5_t35_term_map.py` |
| `test_cp_6_6_7_execution_prompt_hash_untouched_by_tool_layer_change` | `tests/test_sprint7_s710_exec_locality.py` |

**排查步骤与结论**：

1. **先排除"是我引入的"**：把这 9 个 node id **单独拎出来跑**（本文件不进 collection
   session）→ `9 failed in 1.60s`，**一条不少地照失败**。⇒ 与本文件无因果。
2. **再定位真因**，取两条代表：
   - `test_cp_c3_1`：
     `AssertionError: Extra items in the left set: <ErrorCategory.INCOMPLETE_EXECUTION: 'incomplete_execution'>`
     ⇒ `AUTO_FIXABLE` 里**多出一个新错误分类** `INCOMPLETE_EXECUTION`。
   - `test_cp_6_6_7`：`assert actual == "f82f3938cf31f882"` 撞红
     ⇒ `_EXECUTION_SYSTEM_PROMPT_BODY` **字节门被改**。
3. **对上号**：`git diff core/nodes/execution.py` 里赫然是 S7-11（批次 7 / T-S7-7-3、
   T-S7-7-5）的产物 —— `_build_last_fix_context` / `_completion_insufficient` /
   `_audit_declared_steps` / `_LAST_FIX_FILES_MAX` / `_AUDIT_MISMATCH_LOG_MAX`，
   以及 prompt 主体新增的第 6 条纪律与"少跑步骤不会被判成功"。这些**都不是 S7-12 的
   文件边界**（S7-12 只碰 `run_in_sandbox` 早退区 16 行）。
   `tests/test_sprint5_t14_execution_prompt.py` 也已被对方改动（`12+/7-`），
   正是在同步新的字节门。
4. **处置**：**不修、不碰**。这是批次 7 施工现场，属对方 CP 的自验范围。
   本报告只做归因与留证，避免主控把它误读成 S7-12 的退化。

**S7-12 侧结论：零退化。** 本次跑到的所有与 `plan_checks` / `run_in_sandbox` 早退区 /
`run_command` 相关的用例（含 `test_sprint4_c1.py`、`test_sprint7_s710_exec_locality.py`
除字节门外的全部）逐条通过。

---

## 命门验红（两处**分别**验，绝不合并）

> 立此规矩的原因：项目有前车之鉴 —— R-S7-41 那道断言是**恒真**的，看着绿其实什么都
> 没守。开发代理自己也指出"本次改动在测试层的证据强度与 R-S7-41 同族"。**绿不算数，
> 红了才算数。**

### 命门 A：`core/nodes/execution.py:998`（`run_in_sandbox` 早退）

改动方式（`git diff` 实况）：

```
+            if False and has_unsupported_shell_syntax(command):  # MUTATION-A
```

结果 —— **7 failed, 94 passed, 11 xfailed**：

```
FAILED ...::test_cp_8_1_3_sandbox_rejects_redirect_with_structured_error
FAILED ...::test_cp_8_1_3_rejected_command_never_reaches_the_step_runner
FAILED ...::test_cp_8_1_3_rejected_command_pollutes_neither_ledger
FAILED ...::test_cp_8_1_3_ledger_count_is_unchanged_across_a_rejection
FAILED ...::test_cp_8_1_3_rejected_redirect_creates_no_file
FAILED ...::test_cp_8_1_6_sandbox_rejection_logs_masked_and_truncated_warning
FAILED ...::test_cp_8_1_6_both_consumers_return_the_very_same_message
```

**最有价值的一条失败输出 —— 被测缺陷在断言里原形毕露**：

```
>       assert collector.run_results == [], "被拒命令进了 run_results ⇒ 会污染 exit_ok"
E       AssertionError: 被拒命令进了 run_results ⇒ 会污染 exit_ok
E         Left contains one more item: SandboxRunResult(exit_code=0, stdout='ok', ...,
E           command=[..., '.venv/bin/python', 'train.py', '>', 'train.log'])
```

`exit_code=0` + `'>'` 与 `'train.log'` 作为**普通 argv token** 进了台账 —— 这正是
dev-plan §57.1 要治的「假 exit 0 污染 `exit_ok`」，被本组用例当场逮住。

**关键点**：这一组里 `run_command` 侧的 19 条参数化用例**全部照常绿**
⇒ 证明两处拦截是**各自独立**被守住的，没有"一处绿掩护另一处"。

### 命门 B：`core/tools/run_command_tool.py:106`（`run_command` 早退）

改动方式：

```
        if False and has_unsupported_shell_syntax(command):  # MUTATION-B
```

结果 —— **23 failed, 78 passed, 11 xfailed**（19 条参数化必拒 + 3 条 run_command
专项 + 1 条两侧同源）：

```
FAILED ...::test_cp_8_1_4_run_command_rejects_before_starting_any_subprocess
FAILED ...::test_cp_8_1_4_run_command_rejection_adds_no_semantic_keys
FAILED ...::test_cp_8_1_4_run_command_rejects_every_metachar_form[python train.py > train.log]
        …（同族 19 条，逐条列在 pytest 输出中）
FAILED ...::test_cp_8_1_6_run_command_rejection_logs_masked_and_truncated_warning
FAILED ...::test_cp_8_1_6_both_consumers_return_the_very_same_message
```

同样地，`run_in_sandbox` 侧那 6 条**照常绿** ⇒ 两处互不掩护。

### 还原核验

| 文件 | 改前 sha256 | 改后还原 sha256 | 判定 |
|---|---|---|---|
| `core/tools/run_command_tool.py` | `73d5ba1cf6f7ee1c…` | `73d5ba1cf6f7ee1c…` | **逐字节相同** ✅ |
| `core/nodes/execution.py` | `e612c626dda288ab…` | `92f0c703c3d8789f…` | ⚠ 不同 —— **非我方所致**，见"重大发现①" |

`grep -n MUTATION core/nodes/execution.py core/tools/run_command_tool.py` → **零残留**；
`sed -n '989,1007p' core/nodes/execution.py` 实读确认 S7-12 早退区 16 行**逐字完好**。
还原后本文件复跑 **101 passed, 11 xfailed**。

---

## 覆盖矩阵（对应 dev-plan §58 CP-8.1-1 ~ 11）

| 用例 ID（函数名前缀省略 `test_`） | 场景 | 分层 | Mock 策略 | 关键断言 | 来源 CP |
|---|---|---|---|---|---|
| `cp_8_1_1_token_set_is_exactly_the_documented_seventeen` | 集合**内容锁** | 单元 | 无 | 集合与规格表逐格相等 | CP-8.1-1 |
| `cp_8_1_1_supported_connectors_stay_out_of_the_set` | `&&`/`;` 绝不进集合 | 单元 | 无 | 反向命门（防后人顺手补全） | §57.3 红线 |
| `cp_8_1_1_every_token_in_the_set_is_detected` ×17 | 17 token 逐条过谓词 | 单元 | 无 | 逐条 True | CP-8.1-1 |
| `cp_8_1_1_realistic_metachar_commands_are_detected` ×19 | 19 条真实形态 | 单元 | 无 | 逐条 True | CP-8.1-1 |
| `cp_8_1_1_legal_commands_are_never_falsely_rejected` ×12 | **12 条合法命令不误伤** | 单元 | 无 | 逐条 False | CP-8.1-1 / §57.5 |
| `cp_8_1_1_compound_command_hits_from_any_segment` ×3 | 复合命令任一段命中 | 单元 | 无 | 先拆分再判 | CP-8.1-1 |
| `cp_8_1_2_degenerate_input_returns_false_without_raising` ×11 | 退化输入 | 单元 | 无 | False 且零异常 | CP-8.1-2 |
| `cp_8_1_2_predicate_is_pure_and_repeatable` | 纯函数 | 单元 | 无 | 连调三次恒定 | CP-8.1-2 |
| `cp_8_1_3_sandbox_rejects_redirect_with_structured_error` | 消费点 A 返回体 | 集成 | mock runner | `exit_code=-1`/`tool_error`/**键集不新增** | CP-8.1-3 |
| `cp_8_1_3_rejected_command_never_reaches_the_step_runner` | **`_run_step_subcommands` 零调用** | 集成 | spy step runner | 早退早于执行通道 | CP-8.1-3 |
| `cp_8_1_3_rejected_command_pollutes_neither_ledger` | **不进两个台账** | 集成 | mock runner | `run_results`/`step_ledger` 恒空 | CP-8.1-3 ★★ |
| `cp_8_1_3_ledger_count_is_unchanged_across_a_rejection` | **台账条数不变** | 集成 | mock runner | 先攒 2 条再拒 → 仍 2 条 | CP-8.1-3 ★★ |
| `cp_8_1_3_rejected_redirect_creates_no_file` | 无磁盘副作用 | 集成 | **真子进程** | `out.txt` 未创建 | CP-8.1-3 |
| `cp_8_1_3_harness_can_really_create_files` | **阳性对照** | 集成 | 真子进程 | 夹具确能写盘（防上条空转） | 反 S7-06 假绿 |
| `cp_8_1_5_sandbox_does_not_block_the_inline_comparison_probe` | `print(1>2)` 不误伤 | 集成 | 真子进程 | exit 0 + stdout `False` | CP-8.1-5 |
| `cp_8_1_5_sandbox_still_supports_compound_commands` | **`&&` 仍真支持** | 集成 | 真子进程 | 两条子结果 42 / 2 | CP-8.1-5 |
| `cp_8_1_6_sandbox_rejection_logs_masked_and_truncated_warning` | 日志出口纪律 | 集成 | mock runner | WARNING + 脱敏 + 截断 | CP-8.1-6 |
| `cp_8_1_4_run_command_rejects_before_starting_any_subprocess` | **`_run_subprocess` 零调用** | 集成 | spy | 不起子进程 | CP-8.1-4 ★ |
| `cp_8_1_4_run_command_rejection_adds_no_semantic_keys` | Q-B1 红线 3 | 集成 | spy | 键集恰为 `{error, exit_code}` | CP-8.1-4 |
| `cp_8_1_4_run_command_rejects_every_metachar_form` ×19 | **消费点 B 逐条接线** | 集成 | spy | 19 形态逐条拒 + 零子进程 | CP-8.1-4 |
| `cp_8_1_5_run_command_does_not_block_legal_command` | 不误伤 | 集成 | 真子进程 | exit 0 + spy 调 1 次 | CP-8.1-5 |
| `cp_8_1_6_run_command_rejection_logs_masked_and_truncated_warning` | 日志出口纪律 | 集成 | spy | WARNING + 脱敏 + 截断 | CP-8.1-6 |
| `cp_8_1_6_both_consumers_return_the_very_same_message` | **一处定义两处调用** | 集成 | 双夹具 | 两侧文案 `==` 同一常量 | CP-8.1-6 |
| `rejection_message_is_actionable_plain_chinese` | 文案可行动 | 单元 | 无 | 三件事齐 + 零内部术语 | §57.2 第 10 条 |
| `env_probe_tool_is_fail_closed_against_redirects` | **审计闭合** | 集成 | mock subprocess | 第 4 个 shlex 站点无同族缺口 | 我方补充 |

**分层分布**：单元 66 条 / 集成 46 条 / e2e **0 条**。

---

## 已知缺口在测试层如何显形

全部以 **`@pytest.mark.xfail(strict=True)`** 落地，共 **11 条 xfailed**。
`strict=True` 是刻意的 —— 日后谁把缺口补上了，**xpass 会当场变红**，逼他回来同步
dev-plan §58 第 1 条与 §59 的登记，而不是让文档静静过期。

| # | 缺口 | 用例（`tests/test_sprint7_s712_shell_metachars.py`） | 条数 | dev-plan 登记 |
|---|---|---|---|---|
| ① | **贴写形态 `>train.log`（无空格）漏判** —— 本次最大覆盖缺口 | `test_known_gap_attached_redirect_form_is_missed` | 3 | §58 第 1 条第 2 类 ✅ |
| ② | **引号内裸元字符误拒**（`grep '\|' f.txt`）—— 唯一已知误伤形态 | `test_known_gap_quoted_metachar_is_falsely_rejected` | 2 | §59 P-50 ✅ |
| ③ | **`>&2` / `>&1` / `>&` / `<>` 漏判** | `test_known_gap_fd_dup_shorthand_is_missed` | 4 | ❌ **未登记（我方新发现）** |
| ④ | `$VAR` / `$(...)` 展开漏判 | `test_known_gap_shell_expansion_is_missed` | 1 | §58 第 1 条第 4 类 ✅ |
| ⑤ | `run_command` 侧不支持 `&&` 且不拒 | `test_known_gap_run_command_does_not_support_connectors` | 1 | §59 P-49 ✅ |

⑤ 另配一条**真绿**的补充断言
`test_known_gap_run_command_connector_fails_visibly_instead` —— 实证该形态是
**失败可见**（`cd` 磁盘上无此程序 → `_run_subprocess` 的 `OSError` 兜底转
`exit_code != 0`）而非假 exit 0，从而坐实 §59 P-49「登记不治」的依据成立。
没有这条，上面那个 xfail 会被误读成"这里还有个假成功"。

---

## 重大发现（须主控知悉）

### ① ★★ 工作树被并发写入：S7-11（批次 7）代理正在同一工作区改 `core/nodes/execution.py`

**实测证据**：

| 时刻 | `core/nodes/execution.py` |
|---|---|
| 07:43:06 | 132,301 字节（`git diff` = `216+/8-`） |
| 07:43:55 | 136,662 字节 |
| 07:44:22 | 137,597 字节（`git diff` = `295+/9-`） |

内容为 S7-11 的 T-S7-7-3 / T-S7-7-5 产物（`_build_last_fix_context` /
`_completion_insufficient` / `_audit_declared_steps` / prompt 主体第 6 条纪律 /
`AUTO_FIXABLE` 新增 `INCOMPLETE_EXECUTION`），且 `tests/test_sprint5_t14_execution_prompt.py`
已被对方同步改动。

**影响与我方处置**：

1. **命门 A 是在并发写入窗口里做的** —— Edit 工具当场提示"file had been modified on
   disk since you last read it"。我方立即停下核验：`grep MUTATION` 零残留、
   `sed -n '989,1007p'` 逐字比对，**S7-12 早退区 16 行完好无损**，验红结论有效。
   但 `execution.py` 的 sha256 无法用于还原校验（改前 `e612c626` / 还原后 `92f0c703`，
   差异 100% 来自对方的持续写入）。
2. **本次全量回归的 141.64s 窗口内工作树是稳定的**（`git diff --numstat | md5sum` 与
   三个关键文件 mtime **跑前跑后完全一致**）⇒ 数字内部自洽、可信。
3. **强烈建议**：dev-plan §57 开头已预告"本批与批次 7 的 `execution.py` 文件撞车"，
   现在**两批真的在同一分钟同时改同一个文件**。主控收口前请确认对方的改动完整、
   且 S7-12 那 16 行没在对方的读改写里被吞掉（我方已在 07:44 时点核实完好）。

### ② dev-plan §58 第 1 条散文写「16 条」，实际是 **17 条**

规格表格自己列的是 3（管道）+ 8（输出重定向）+ 3（输入/heredoc）+ 2（描述符合并）
+ 1（后台）= **17**，生产代码 `_UNSUPPORTED_SHELL_TOKENS` 也是 **17**。
主控派单沿用了这个"16"。**散文数字系笔误，代码与表格无误。**
本次测试锁的是**内容**（`EXPECTED_UNSUPPORTED_TOKENS` 逐格誊抄）而非数量，
因此不受该笔误影响。建议订正 §58 散文。

### ③ ★ 新发现的漏判形态：`>&2` / `>&1` / `>&` / `<>`（§58 / §59 均未登记）

实测（`.venv/bin/python` 亲跑）：

```
False | 'echo err >&2'          | ['echo', 'err', '>&2']
False | 'python x.py >&1'       | ['python', 'x.py', '>&1']
False | 'python x.py >& out.log'| ['python', 'x.py', '>&', 'out.log']
False | 'python x.py <> f.txt'  | ['python', 'x.py', '<>', 'f.txt']
```

其中 **`>&2`（等价 `1>&2`，把输出丢到标准错误）是脚本里相当常见的写法**，
`>& file`（等价 `&>`）在 bash 里同样合法。

**与已登记缺口的性质差异（这是本条的要害）**：贴写形态 `>train.log` 之所以"不治"，
是因为要覆盖它**必须做前缀匹配、违反红线**；而 `>&2` / `>&` / `<>` 是
**shlex 之后的独立 token**，补进集合是**零成本、零模糊匹配、零误伤**的。
⇒ 二者不是同一类取舍，把它归进"已接受残留"并不成立。

**建议**：交开发侧评估把 `>&2` / `>&1` / `>&` / `<>` 直接补入 `_UNSUPPORTED_SHELL_TOKENS`
（集合 17 → 21）。届时本文件的 `test_known_gap_fd_dup_shorthand_is_missed` 会因
`strict=True` **由 xfail 转 xpass 而变红**，正好逼改动者同步集合内容锁与规格文档 ——
这是设计目的，不是回归。**我方不擅自改生产代码**，故当前只做显形。

### ④ 审计闭合：全仓第 4 个 `shlex.split` 站点**无同族缺口**

全仓 `shlex.split` 共 4 处：`core/plan_checks.py:199`（纯解析）、
`core/nodes/execution.py:595`（`_split_top_level`）、`core/tools/run_command_tool.py:94`、
`core/tools/env_probe_tool.py:197`。前三处本批已治；第四处靠**整条 argv 白名单精确匹配**
（`env_probe_tool.py:204` `if tuple(argv) not in _ALLOWED_ARGV`）天然 fail-closed，
带元字符的命令永远匹配不上清单 ⇒ **无需接本批谓词**。
我方把这个"无需处置"的结论固化成断言
（`test_env_probe_tool_is_fail_closed_against_redirects`），防止日后白名单改成前缀
匹配时悄悄开洞。

---

## 后续动作

- **[主控]** 收口前核实 S7-11 代理对 `core/nodes/execution.py` 的并发写入未吞掉 S7-12
  的 16 行早退区（我方 07:44 核实完好，此后仍在被写）。
- **[主控 / 批次 7]** 上述 9 条失败**属批次 7 施工现场**，须由 S7-11 侧自验收口
  （尤其 `test_cp_6_6_7` 字节门与 `test_cp_c3_1` 的 `AUTO_FIXABLE` 集合）。
- **[开发代理]** 评估重大发现③：`>&2` / `>&1` / `>&` / `<>` 是否补入集合（零成本补法）。
- **[文档]** 订正 dev-plan §58 第 1 条散文「16 条」→「17 条」。
- **下一次跑测试的触发条件**：S7-11 收口后重跑全量，期望
  `2400 passed, 25 skipped, 46 deselected, 11 xfailed`（S7-12 侧 101 条应保持全绿）。
