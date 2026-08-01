# 测试执行报告 - s711-independent-acceptance（S7-11 批次 7 独立测试设计与验收）

- **日期**：2026-08-01
- **执行人**：@测试工程师代理
- **Sprint**：sprint7
- **触发原因**：S7-11（批次 7「执行完整度接入成功判定」）开发交付、停在真跑前，Maria 指派**独立验收 + 证伪**。要求：凡开发代理声称的，一律自己重跑一遍才认。
- **commit**：验收开始时工作区未提交（基线 `c480990`）；验收进行中主控把 S7-11 + S7-12 合并提交为 **`f5a68d7`**（我方文件 `sha256` 逐字节未变，验收结论不受影响；此后 diff 一律对 `c480990..f5a68d7`）
- **环境红线遵守**：**全程零 `git checkout` / `git restore` / `git stash`**；所有验红均为 `cp` 文件级备份 → 改坏 → `cp` 还原 → `sha256sum -c` 校验。并发会话（S7-12）的 `core/tools/run_command_tool.py` / `tests/test_sprint7_s712_shell_metachars.py` / `core/plan_checks.py` shell 谓词段 / 其测试报告 **一次都没有被我修改**（收口时全量 `sha256sum -c` 逐条 OK）。

---

## 执行范围

| 命令 | 用途 |
|---|---|
| `.venv/bin/pytest -q -m "not e2e and not browser" -p no:randomly` | 全量回归（基线 + 每次验红 + 收口，共跑 **11 次**） |
| `.venv/bin/pytest -q -m browser -p no:randomly` | UI 维 |
| `.venv/bin/mypy` | 类型检查 |
| `.venv/bin/python`（`importlib` 走遮蔽陷阱） | prompt 哈希独立复算、`_reconcile_steps` 退化输入探针 |

- **未跑 e2e**（`-m e2e` 一次都没有执行），**未跑真跑**，**零 deepxiv / LLM 配额消耗**。
- **未 commit、未 push**。
- **零生产代码改动**（所有生产文件收口 `sha256` 与验收开始时逐字节相同）。
- **新增测试文件**：`tests/test_sprint7_s711_gap_audit.py`（**33 条**：30 passed + 3 xfailed）。

---

## 结果摘要

| 口径 | 我方实测 | 开发/主控声称 | 判定 |
|---|---|---|---|
| `not e2e and not browser`（验收开始，未含我方新增） | **2445 passed / 25 skipped / 58 deselected / 7 xfailed / 0 failed**（62.78s） | 2445 / 25 / 7 | ✅ 逐字相符 |
| `-m browser` | **12 passed / 2523 deselected**（79.55s，未加 retry，已知 flaky 未复现） | 12 passed | ✅ 相符 |
| `mypy` | **Success: no issues found in 27 source files** | 27 files 0 error | ✅ 相符 |
| 收口（含我方新增 33 条） | **2475 passed / 25 skipped / 58 deselected / 10 xfailed / 0 failed** | — | 账目对平：2445 + 30 = 2475，xfailed 7 + 3 = 10，**无余数** |
| commit message 的「2457 绿」 | 2445 + browser 12 = **2457** | — | ✅ 口径可对上，非虚报 |

- 警告：3 条，**全部为既有第三方/既有用例告警**（`LangChainPendingDeprecationWarning` ×1、`PydanticDeprecatedSince20` ×2），本批零新增。
- 跳过 25 条：与基线一致，未逐条复核原因（不在本次范围）。

---

## 一、CP / 验收点逐条判定（含我方实据）

> 只列本批交付面。**「实据」一栏全部是我自己跑出来的**，不引用开发代理的记录。

| CP | 声称 | 我方实据 | 判定 |
|---|---|---|---|
| CP-7.3-1~4（修法 A） | payload 注入 `last_fix.note/files`、三形态零扰动、字节幂等、截断 | 逐条读源码 + 跑 `test_da_1_*` 全绿；`_build_last_fix_context` 走 `PurePosixPath(...).name` 取 basename、截断 `_LAST_FIX_FILES_MAX=10`、note 用 coding 侧 `_FIX_NOTE_MAX_CHARS` | ✅ |
| CP-7.3-5（注释订正） | 「避开」不再出现 | 源码 docstring 已改为「使 agent 有依据重跑验证修复，而不是绕开」 | ✅ |
| CP-7.3-6 / CP-7.4-1/2（字节门） | 改 prompt 当场红、基线更新为 `c73e1e6e3cfc1280` / 1979 | **独立复算**（`importlib`）：`len=1979`、`sha256[:16]=c73e1e6e3cfc1280`，与写死基线一致；**基线是硬编码字面量、非 `EXPECTED=actual` 自锁定**（已读源码确认） | ✅ |
| **P-48（两道字节门）** | 改 prompt 后**两道**同时红 | **我方实测**：body 内插一个空格 → `test_cp_6_2_1_execution_prompt_body_byte_baseline` + `test_cp_6_6_7_execution_prompt_hash_untouched_by_tool_layer_change` **恰好 2 红**，报错逐字 `（当前：0c8298d6ad88672c，基线：c73e1e6e3cfc1280）`；`cp` 还原后 `sha256` OK | ✅ P-48 属实 |
| CP-7.4-3/4/5/6（提示词） | 三句正向在位、旧串消失、AC-S7-46 三句仍在、`plan_steps_finished` 零命中、零插值 | 逐条读 prompt 主体确认；`grep -rn plan_steps_finished` 全仓零命中 | ✅ |
| CP-7.5-1/2（单点谓词） | 真值表 + 防御 | 跑通；**另补 7 组既有真值表未覆盖的退化输入**（见下） | ✅（有补角） |
| CP-7.5-3（防伪留痕正负四向） | 正/负/写法变通/不可判 四向 | 跑通；`-m` 形态确实打 WARNING 且 `completed` 照常算 1 | ✅ |
| **CP-7.5-4（纯观测红线）** | 打桩报大量不符 → `success` 与 feedback 一字不变 | ❌ **守门有盲区，见「假绿①」** | ⚠ **不充分** |
| CP-7.5-5（脱敏） | 日志过 `mask_value` | 跑通 | ✅ |
| CP-7.6-1（四格真值表） | 顺序即优先级 | **我方验红**：把 `_apply_incomplete_execution` 挪到 `_apply_no_metrics` 之后 → 恰 **1 红**（`nometrics_incomplete` 格），还原复绿 | ✅ |
| CP-7.6-2（单点谓词守门） | 绕过谓词写内联比较 → 红 | **我方验红**：把 `success` 处换成等价内联比较 → 恰 **1 红**（`[True]` 参数格），还原复绿 | ✅ 真门 |
| CP-7.6-3（路由回 coding） | 完成度不足 → `retry_coding` | 跑通（真调 `execution()` 的行为断言，非源码子串） | ✅ |
| **CP-7.6-4（撞上限 interrupt#2）** | **P-52 自认未实做** | ✅ 属实，**我方已补 3 条**（见下） | ⚠ 原缺，已补 |
| CP-7.6-5/6/7（round-trip / 早停 / 三映射点） | 零改动即正确 | 跑通 | ✅ |
| CP-7.6-8（插槽重排无副作用） | `_reconcile_steps` / `_apply_no_metrics` 函数体 diff 为空 | `git diff c480990 HEAD -- core/nodes/execution.py` 逐段核：两函数体**确实一行未改**，只有调用位置移动 | ✅ |
| CP-7.7-1/2/5/6（对外口径） | 「B 档」消失、三条件、term_map 加一条、账目 43/15/58 | 跑通；**我方验红**：`_GUARDED_CONSTANTS` 去掉 `_SUCCESS_CRITERIA_NOTE` → **3 红**，报错逐字 `实际 14 条，EXPECTED_CONSTANTS_N=15` / `本次实际扫描 57 条，期望 58 条`；`==` 形态未被放宽 | ✅ |
| CP-7.7-3 / 7.7-4 | **P-47 撤回作废** | 已核实撤回理由成立（R-2 契约由 t33/t34 两条用例守着）；但**残留矛盾仍在**，我方补了一条钉死用例（见下） | ⚠ 作废合理，代价已钉住 |
| CP-7.8-2（禁弱化自查） | 4 个文件零弱化 | ❌ **文件数是 8 不是 4**，但**弱化确实零新增**（见「二」） | ⚠ 结论对、口径记错 |
| CP-7.8-4（回归对平） | 2287 + 49 = 2336 / 全量 2445 | 2445 实测相符 | ✅ |
| CP-7.8-5（mypy + 零改动红线文件） | 27 files 0 error | 实测相符；`git diff c480990 HEAD` 里 `core/graph.py` / `core/nodes/planning.py` / `core/nodes/coding.py` / `core/nodes/resource_scout.py` / `sandbox/local_venv.py` / `core/nodes/_repo_scoring.py` / `core/state.py` **全部无改动**。⚠ `core/plan_checks.py` / `core/tools/**` 有改动，但**全部属并发的 S7-12 批次**，与 S7-11 无关 | ✅ |

---

## 二、各处验红的实际表现（全部我方亲手改坏 + `cp`/`sha256` 还原）

| # | 改坏方式 | 我方实测红的条数 | 开发声称 | 一致？ |
|---|---|---|---|---|
| ① | 删掉 `success` 里的 `and not _completion_insufficient(...)` | **6 红**（全在 `test_sprint7_s711_completion.py`：四格表 `metrics_incomplete` / 单点谓词 `[True]` / 路由 / 文案 / 结论同向 / 四条同批） | 6 | ✅ |
| ② | `success` 处改内联比较（绕过谓词） | **1 红** | 1 | ✅ |
| ③ | prompt body 内插一个空格 | **2 红**（两道字节门） | 2 | ✅ |
| ④ | `_apply_incomplete_execution` 的 `auto_fixable=True` → `False` | **3 红**（路由由 `retry_coding` 翻为 `await_dev_loop_interrupt`） | 3 | ✅ |
| ④b | 把 `INCOMPLETE_EXECUTION` 从 `AUTO_FIXABLE` 摘掉 | **3 红**（`test_sprint3_c3` 集合断言 / guard round-trip / 三映射点）；**路由命门用例仍绿** | 3，且路由不红 | ✅ **P-51 独立证实** |
| ⑤ | `_GUARDED_CONSTANTS` 去掉一条 | **3 红** | 3 | ✅ |
| 附 | `_apply_incomplete_execution` 挪到 `_apply_no_metrics` 之后 | **1 红** | 1 | ✅ |
| 附 | 把留痕结论接进 **`success`** | （开发记 1 红，我方未重复此形态） | 1 | — |
| **附★** | 把留痕结论接进 **`feedback`**（我方新增形态） | **0 红 —— 既有 49 条全绿** | 未测 | ❌ **盲区，见假绿①** |

**还原完整性**：每一次验红后立即 `cp` 还原 + `sha256sum -c`，全部 OK；收口时对 19 个文件做一次全量 `sha256sum -c`，**零差异**。

### P-51 独立结论

**开发代理的自述属实，dev-plan CP-7.6-3 里写的验红手法确实是错的。** 我方实测：

- **把枚举从 `AUTO_FIXABLE` 摘掉** → 路由命门 `test_da_6_cp_7_6_3_incomplete_routes_back_to_coding` **仍然绿**（1 passed）。红的是另外 3 条（`AUTO_FIXABLE` 精确集合断言、guard 重入 round-trip、三映射点）。
- **把 `auto_fixable=True` 置 `False`** → 路由断言**翻转为 `await_dev_loop_interrupt`**，3 红。

**根因（dev-plan §56.3 P-51 只记了现象、没记这一层）**：`_apply_incomplete_execution` 是**硬编码** `auto_fixable=True` 构造 `ExecutionFeedback`，而 guard 重入路径的 `_feedback_from_committed_result` 是 `category in AUTO_FIXABLE` **推导** ⇒ **两个真值源**。摘集合之后，**首跑路径照样回 coding、guard 重入路径却判成不可修复**——同一份落盘结果两条路径判两样。我方已补两条一致性守门把这条缺口机制化（补完后「摘集合」这种改坏方式也会红，dev-plan 原本设想的验红手法重新有效）。

---

## 三、★ 我找到的假绿与测试缺口（本次最重要产出）

### 【BUG-S7-11-01】★★ 计划里只要有一条 agent 无从执行的步骤，`success` 就变成**不可达**

- **严重度**：高（可致每次真跑白烧 20 轮修复预算并被推到 interrupt#2）
- **性质**：**生产缺陷**，非测试问题。**我未修改生产代码。**
- **复现**：`.venv/bin/pytest tests/test_sprint7_s711_gap_audit.py -k bug_s711_01`

**根因**：`_reconcile_steps` 的 `planned = len(steps)` 是**原始步数**，`_completion_insufficient` 直接拿它做分母。而下列四种步骤**永远进不了分子**：

| 形态 | 为什么进不了分子 |
|---|---|
| 无 `command` 键（`{"step_name": "查看 outputs/ 下的图表确认可视化正常"}`） | `_extract_command_str` 返回 `None` ⇒ 进不了归属规则②的 `plan_index`；agent 没东西可跑 ⇒ 也拿不到规则①的自报 |
| `command` 为空串 | 同上 |
| 纯 `cd`（`{"command": "cd ."}`） | 规则②显式跳过 `cd`/`source`/`.` 头 |
| `command` 写成中文描述（`"人工查看 outputs/figures 下的图是否正常"`） | 能进索引，但 agent 永远不会真去执行它 |

**实测（真调 `execution()`，agent 完全照做、命令全 exit 0、指标齐全、还诚实自报了 `step_index`）**：

```
success: False
recon: {'planned': 2, 'executed': 1, 'completed': 1,
        'unexecuted_steps': [{'index': 1, 'step_name': '查看 outputs/ 下的图表确认可视化正常'}], ...}
errors: ['[error_category=incomplete_execution] 命令都正常结束了，但计划里的步骤没跑完（已跑完 1/2 步）：还没跑的有 查看 outputs/ 下的图表确认可视化正常']
route: retry_coding   fix_loop_count: 1
```

**为什么这是缺陷而不是"设计如此"**：

1. **dev-plan 自己写的是 `planned_actionable`**——§49.2 第 6 条与 §53 R-S7-59 正文两处逐字写「本轮 `completed < planned_actionable`」（**actionable**），实现落成了 `planned`。规格与实现在这一个词上分岔。
2. **回修复循环解不开**：下一轮 coding 无论怎么改代码，都变不出一条「查看图表」步骤的命令 ⇒ 每轮都判 INCOMPLETE，直到烧满 `MAX_FIX_LOOP_COUNT=20` 才走 interrupt#2。**这正是 R-S7-59 的后果（假红 + 白烧预算），但成因与 agent 听不听话完全无关。**
3. **全链路无任何一处强制 `command` 非空可执行**：`planning.py:182` 只是提示词「要求」；`plan_checks.py:542` 对空命令是 `continue` **放行**；`_coerce_step_list` 还容忍纯字符串元素；`ReproductionPlan` schema 无约束。⇒ 触发概率不是理论值。
4. **它推翻了 R-S7-59「mock 层证不到」的一半**：该后果链在 mock 层**完全可复现**，我已用 `test_gap_r_s7_59_disobedient_agent_burns_every_fix_round` 连跑 20 轮机械钉死。

**修复方向须由架构师 / PM 裁决（我不擅自决定）**，候选两条：

- (a) 判定分母改为 **actionable 步数**（`_plan_step_keys(step)` 非空的步数），与 dev-plan 原文的 `planned_actionable` 对齐；
- (b) 在 planning 侧强制每步 command 非空可执行（`check_plan` 由 `continue` 改为产错），把问题挡在上游。

⚠ 二者都动到本批的红线文件（`core/plan_checks.py` 零改动红线 / `_reconcile_steps` 函数体零改动红线），**必须走架构裁决**。

**我落的测试**：3 条 `xfail(strict=True)`（期望行为，修好即由 xfail 转正）+ 4 条现状钉死 + 1 条谓词层最小复现 + 1 条阴性对照（全步骤可执行时照常成功）。已用「把分母临时改成 actionable」验证耦合：3 条 xfail 全部 **XPASS→strict 判红**、4 条现状条同时红 ⇒ 修复必然经过这些用例，不会被悄悄改掉。

---

### 【假绿①】CP-7.5-4「纯观测红线」守门存在盲区，可被完整绕过

- **复现**：把主流程 4.65 槽改成消费返回值并影响 **feedback**（而不是 `success`）：

```python
_audit_res = _audit_declared_steps(...)
if _audit_res:
    feedback = ExecutionFeedback(ErrorCategory.RUNTIME, True, "自报不符", "查", "")
```

- **实测结果**：`tests/test_sprint7_s711_completion.py` → **49 passed（全绿）**；全量回归 → **2445 passed（全绿）**。红线「返回值不得被判定 / 渲染 / state 消费」被实质破坏而**一条用例都不响**。
- **根因**：既有守门 `test_da_4_cp_7_5_4_audit_is_pure_observation` 只断言 `success is True` 与 `errors == []`，而 `_build_execution_result` **只在 `not success` 时才把 feedback 写进 `errors`** ⇒ 在它选的**成功场景**里 feedback 根本不可观测。守门的强度低于它声称的语义（dev-plan §49.3「返回值不得被任何判定/渲染消费」+ §54 纪律 3b）。
- **我方补强**：`test_gap_pure_observation_output_is_identical_when_audit_screams`，改为**打桩前后整份判定投影逐字节相同**（`execution_result` 全键 + `_dev_loop_route` + `fix_loop_count` + `fix_loop_history` 分类 + `node_errors` 类型），并覆盖 **success / incomplete / failed 三种场景**。
- **补强后重做同一处改坏**：**2 红**（`incomplete` / `failed` 场景），报错逐字 `留痕函数（failed 场景）的结论泄漏进了判定 / 路由 / 落盘 —— 它是纯观测，返回值不得被任何人消费`；还原后复绿。

---

### 【缺口②】`auto_fixable` 双真相源无一致性守门（P-51 的根因层）

见上文 P-51 结论。已补 `test_gap_apply_chain_auto_fixable_agrees_with_the_auto_fixable_set` + `test_gap_first_pass_and_guard_reentry_agree_on_auto_fixable`。**红绿实据**：摘掉 `AUTO_FIXABLE` 里的新枚举 → 这 2 条红，而开发交付的路由命门仍绿。

### 【缺口③】CP-7.6-4 未实做（P-52 自认），且撞上限回归也无人守

已补 3 条（撞 `MAX_FIX_LOOP_COUNT` / 预算不足一回合 / 子预算触顶 → 均落 `_ROUTE_AWAIT_INTERRUPT` 两段式）。**红绿实据**：把 `fix_count < MAX_FIX_LOOP_COUNT` 放宽成 `<=` → 我方 2 条红，**开发交付的 49 条全绿**。

### 【缺口④】留痕函数的退化输入无守门

`_audit_declared_steps` 一旦在畸形入参上抛异常会**炸掉整个 execution 节点**（它排在主流程 4.65 槽，无 try/except 包裹）。已补 6 组参数化（空 / `None` / 畸形下标 / 畸形条目 / 空命令）断言恒返回 `None` 且不抛。实测当前实现全部通过（`try/except (TypeError, ValueError)` 覆盖到位）。

### 【缺口⑤】谓词退化输入补角

既有真值表未覆盖：`completed > planned`（计划外命令被规则②误归属时真的会出现）、负数、`float`、非 dict/字符串入参。已补 7 组，全部 `False` 且零异常。

### 【已知落差（非缺陷，钉死留档）】`attribution_unavailable` 时判定与报告分叉

P-47 撤回标注析取项后，`attribution_unavailable` 那一格仍是：**判定层判不成功、报告不打 `incomplete_execution` 标注** ⇒ 用户看到"没成功"但看不到"哪一步没跑完"。撤回理由（R-2 保守契约由 `test_sprint5_t33/t34` 两条用例守着）成立，我不推翻；但已补 `test_gap_attribution_unavailable_judgement_and_report_diverge` 把这个落差钉住——日后有人想单点补析取项，会先撞到这条用例、必须连同 R-2 契约一起重议。

---

## 四、「禁弱化」自查的独立核实结论

**结论：弱化确实零新增，但「4 个既有测试文件」这个口径记错了，实际是 8 个（+ `tests/conftest.py`）。**

`git diff c480990 HEAD -- <每个测试文件>` 逐个扫「被删除的 assert / 新增的 `>=` / `issubset` / `pytest.skip` / `xfail`」：

- **新增弱化形态：0 处**（8 个文件全部命中零）。
- **被删除的 assert 行：仅 3 行**，逐条核实：
  1. `test_sprint7_s708_reporting_scale.py`：`len(TERM_LABELS) == 42` → `== 43`（**同形态改数**，非弱化）；
  2. `test_sprint7_s710_exec_locality.py`：哈希基线 `f82f3938cf31f882` → `c73e1e6e3cfc1280`（**同形态改值**，非弱化）；
  3. `test_sprint4_e2e.py`：`assert "exit=128" in logs`（循环内，对每一个含哨兵帧）→ `assert "exit=128" in evidence[-1]`（只对 exec#1 那一帧）。

**第 3 条即开发代理自称的"订正对象不是强度"，我独立核实的结论是：可以接受，但它是本批 diff 里唯一一处形式上收窄的断言，理由链必须成立才算数——我逐环验过，成立：**

- 该场景的计划有 `fetch` + `train` 两步，S7-11 起修复回合要求全量重跑 ⇒ 夹具里的 `DispatchScriptLLM` 被同步改成"修复回合跑 fetch + train 两条"（否则 `completed=1<2`，本用例会因新判定而红——这属**夹具债**，改夹具是正确处置）；
- 改完之后 exec#2 也会重跑 `fetch`，**这次凭证已到手、exit=0** ⇒ 它同样含哨兵原文但**本就不该有失败证据**。继续要求"每一帧都有 exit=128"会变成一条**要求错误行为**的断言；
- **mask 阳性对照（`_SENTINEL not in logs` + `"****" in logs`）仍对每一帧逐帧成立，一字未动**——这才是该用例的核心强度，没有被削；
- `evidence[-1]` 确实是 exec#1：`obs["history_exec_logs"]` 由 `graph.get_state_history(cfg)` 构造，**新帧在前** ⇒ 最后一个含哨兵的帧就是最老的 exec#1。我读了构造代码确认，不是靠开发陈述。

⇒ **判定：这一处是订正断言的对象，不是放松强度。** 但我要指出：新写法把"哪一帧该有失败证据"这个前提**隐含在下标里**，若日后剧本再加一轮，`evidence[-1]` 的语义会静默漂移。建议后续改成按帧内容显式定位（不阻塞放行）。

---

## 五、R-S7-59 可测性评估（不跑真跑的前提下能测到什么程度）

R-S7-59 = 「判定层正确性硬挂在提示词纪律 6（每回合从头全量重跑）上，而实测服从率仅 75%」。dev-plan 写「**唯一证伪手段是真跑，mock 层证不到**」。

**我的结论：这句话对了一半。R-S7-59 可拆成三段，前两段 mock 层完全可测，只有第三段必须真跑。**

| 分段 | 能否 mock 层测 | 我设计的最强验证 | 状态 |
|---|---|---|---|
| **① 约束是否真的落进了提示词** | ✅ 完全可测 | 逐字串断言 + **两道字节门**（改一个空格即 2 红），锁的是"这句话不会被后人悄悄删掉" | 开发已覆盖，我复核通过 |
| **② agent 不听话会导致什么后果** | ✅ **完全可测**（dev-plan 说测不到，不成立） | `test_gap_r_s7_59_disobedient_agent_burns_every_fix_round`：构造"只重跑第 0 步"的不听话 agent，**连跑 `MAX_FIX_LOOP_COUNT=20` 轮**，逐轮断言 `success is False` + `route == retry_coding` + `fix_loop_count` 精确 +1，第 21 次断言落 `_ROUTE_AWAIT_INTERRUPT`；再配**阳性对照**（听话的全量重跑 agent **一轮即收敛为 success**），使红能归因到"没全量重跑"而不是"判定层根本判不出成功" | **我方新增，已绿** |
| **③ agent 到底听不听话（服从率）** | ❌ **只能真跑证伪** | 无法 mock —— 这是 LLM 行为的统计量，任何 mock agent 的服从率都是我自己写的 | **只能靠真跑** |

**额外发现（★ 直接抬高该风险的优先级）**：BUG-S7-11-01 说明 **②的后果链不只在 agent 不听话时触发**——只要计划里有一条不可执行的步骤，**即使 agent 100% 听话也照样触发**。⇒ R-S7-59 的缓解措施（改提示词措辞）对这条成因**完全无效**。真跑之前应先裁决 BUG-S7-11-01，否则真跑很可能红在这条上，而观测量①（"agent 是否全量重跑"）会被误读为"agent 不听话"。

**真跑时的判读建议**（写给 T-S7-7-9）：观测量①出现"恒判未完成"时，**先量计划里有几条不可执行步骤**再下结论——顺序反了会把生产缺陷误记成"提示词服从率不够"，与 S7-10 的 R-S7-57/BUG-S7-10-01 误判风险同型。

---

## 六、补充的测试文件与条数

- **文件**：`/data/myproj/auto_reproduction/tests/test_sprint7_s711_gap_audit.py`（约 480 行）
- **条数**：**33 条**（30 passed + 3 xfailed(strict)）
- **分区**：
  | 区段 | 条数 | 守什么 |
  |---|---|---|
  | ① BUG-S7-11-01 | 8（含 3 strict-xfail） | 不可执行步骤致 success 不可达（4 形态 + 谓词层复现 + 阴性对照） |
  | ② 纯观测红线补强 | 9 | 打桩前后判定投影逐字节相同（3 场景）+ 退化输入恒返 `None`（6 组） |
  | ③ `auto_fixable` 双真相源 | 2 | 构造值与集合口径一致；首跑与 guard 重入一致 |
  | ④ CP-7.6-4 补齐 | 3 | 撞轮次上限 / 预算不足 / 子预算触顶 → 两段式 interrupt#2 |
  | ⑤ R-S7-59 后果链 | 2 | 不听话 agent 烧满 20 轮 + 听话 agent 一轮收敛（阳性对照） |
  | ⑥ 谓词退化 + P-47 残留落差 | 8 | 7 组退化输入 + 1 条判定/报告分叉钉死 |
- **零弱化**：新文件不含任何 `>=` / `issubset` / `pytest.skip`；3 处 `xfail` 全部 `strict=True`（缺陷修复后必然转红提醒），符合 S7-10 `BUG-S7-10-01` 的既有范式。
- **可独立运行**：每条均可 `pytest <file>::<test>` 单跑；默认随机顺序下同结果。

---

## 七、失败排查

本次全量回归**零失败**。所有"红"均为我主动改坏后的验红，逐条已在「二」中列出，并全部 `cp` 还原 + `sha256sum -c` 校验通过。

唯一一次需要排查的异常是**验收中途 `git status` 突然变空**：经 `git log` 核实系主控把 S7-11 + S7-12 合并提交为 `f5a68d7`（非文件丢失），我方全部文件 `sha256` 与验收开始时逐字节相同，验红结论不受影响；此后 diff 基准改为 `c480990..f5a68d7`。

---

## 八、放行意见

**结论：本批的四条修法 A/B/C/D 实现正确、验红扎实、回归零退化，主体质量高于上一批；但我认为在 BUG-S7-11-01 得到裁决前，不应进入 T-S7-7-9 真跑。**

**不能放行真跑的理由（一条）**：

- **BUG-S7-11-01 会让真跑高概率白烧一整轮 deepxiv + LLM 配额**。真跑靶 UMAP 的计划是 9 步 LLM 生成的步骤，只要其中一条不可执行（无 command / 空 command / 纯描述），判定层就恒判未完成、烧满 20 轮修复、最后推到 interrupt#2 ——而 dev-plan 明写"真跑判读口径是「不再出现少跑步骤却判成功」"，这条缺陷会让真跑结果**无法区分"agent 不听话"与"判定分母算错"**，观测量①直接失效。**建议：先由架构师裁决分母口径（actionable vs 原始步数），改完再跑。**

**可以放行的部分**：代码可以保留在 `f5a68d7`（已 commit），非真跑路径的功能与回归均无问题；上述缺陷的修复是一处**局部改动**，不需要回退本批。

**次要建议（不阻塞）**：

1. 把 `_apply_*` 系列的 `auto_fixable` 由硬编码改为 `category in AUTO_FIXABLE` 推导，消掉双真相源（`_apply_no_metrics` 是既有同款写法，一并处理）；
2. `test_sprint4_e2e.py` 的 `evidence[-1]` 建议改成按帧内容显式定位，避免剧本再加一轮时语义静默漂移；
3. dev-plan §56.3 CP-7.8-2 的「4 个既有测试文件」订正为 **8 个 + conftest**。

---

## 后续动作

- [ ] BUG-S7-11-01 → 架构师/PM 裁决口径，再由 @全栈开发代理 落地；修好后我方 3 条 `xfail(strict=True)` 会自动转红提醒转正。
- [ ] 真跑（T-S7-7-9）在 BUG-S7-11-01 裁决后再申请 Maria 授权。
- [ ] 下次跑测试的触发条件：BUG-S7-11-01 修复后回归 `tests/test_sprint7_s711_gap_audit.py` + 全量。
