# 测试执行报告 - realrun-evidence-fix（真跑证据"跑完即灭失"的结构性修复）

- **日期**：2026-08-07 03:12（PDT）
- **执行人**：@测试工程师代理
- **Sprint**：sprint7（覆盖批次 3 = S7-05 与批次 6 = S7-10 两处同款缺陷）
- **触发原因**：本报告作者上一轮取证时报出的结构性缺陷（`2026-08-07_s705-b3-realrun-forensics.md` §4.1 第 3 条 + §7 首条建议）——**真跑证据落 `tmp_path`，跑完即灭失**。Maria 拍板："把这条从『写在 dev-plan 里的要求』变成『真跑用例里的一段代码』——写成要求已经失败过一次了。"
- **commit**：`0e250fb`（改动未提交，留工作区）
- **是否包含 e2e / 真跑**：**否**。全程零真跑、零外部调用、零 deepxiv/LLM 配额消耗。

---

## 0. 结论先行

| 事项 | 结论 |
|---|---|
| 报告归档位置纠正 | ✅ 已迁 `docs/sprint8/test-reports/`，旧路径留指针不断链 |
| 结构性缺陷修复 | ✅ 机制已落成代码（`tests/realrun_evidence.py` + 接线 + 29 条离线用例 + 6 组变异验红） |
| **是否已生效** | 🔴 **只能说"离线验过"，不能说"已生效"**——真实链路要等下一次真跑才知道（§4.2 逐条列了离线证不了的部分） |
| 历史证据 | 🔴 **两处均不可补救**：S7-05 的 2026-07-22 原始数据、S7-10 的 2026-08-01 计划原文，**本次修复救不回来**（§3.3 有磁盘实证） |
| 新发现 | ⚠ 1 个**我自己写的测试**的隔离缺陷（与 P-S8-20 同族），已在本轮修掉，详见 §5.1 |

---

## 1. 病根：为什么"写进 dev-plan"这条路失败了两次

同一个失效模式在 sprint7 里发生了两次，**两次都不是执行者疏忽**：

| 批次 | 要求写在哪 | 实际发生 | 后果 |
|---|---|---|---|
| 批次 3（S7-05） | 无明文要求 | `test_sprint7_s705_realrun.py` 把 checkpoint 库（`:82`）与 workspace（`:87`）都落 `tmp_path` | 2026-07-22 真跑的 `fix_loop_history` 原始记录**永久灭失**（`find /` 零命中，`/tmp/pytest-of-*` 只剩最近 3 轮） |
| 批次 6（S7-10） | **有**明文要求，`dev-plan.md:2739-2740`「必须把 `reproduction_plan` 全文 + 关键 state 快照落盘成 bundle JSON」 | **一次都没执行过** | `CP-6.9-1` 至今只能靠**执行侧 `exec_logs`** 作强旁证；本条明令要验的**计划原文**永久取不到 |

⇒ **病根不是"没人知道要留证"，而是"这条要求没有任何执行力"**。S7-10 那条写得清清楚楚，还给了范式（`scripts/dump_real_plan.py`），照样零执行——因为**文档不会在跑测试的时候自己跳出来**。

**修法只有一条：把它变成代码路径上绕不过去的一步。** 本次做的就是这个。

---

## 2. 做了什么

### 2.1 新增 `tests/realrun_evidence.py`（556 行，非 test 文件、不被收集）

统一的真跑证据归档机制，三个入口：

| 入口 | 用途 | 落点 |
|---|---|---|
| `durable_run_dir(request, name)` | 替代 `tmp_path` 做真跑的运行目录 | `workspace/runs/<name>_<ts>/`（**已 gitignore**：留得住、不进版本控制、不污染 `git status`） |
| `dump_realrun_bundle(request, sprint=…, name=…, state=…)` | 真跑用例内归档关键 state | `docs/<sprint>/test-reports/realrun-bundles/<日期_时分秒>_<name>.json`（**进版本控制**） |
| `python -m tests.realrun_evidence --db … --thread …` | **手工真跑**（走 app / 脚本，证据只在 checkpoint 库里）事后补档 | 同上 |

⚠ **`workspace/runs/` 不是我新造的目录**——磁盘实测它自 2026-05-25 起就在用（`spike-f3-coding-*` / `spike-g3-execution-*` / `spike-s3-prompt-cache-baseline_*.json` 等 20+ 项，取值时刻 2026-08-07 03:08）。沿用既有约定，不新增概念。

### 2.2 接线进 `tests/test_sprint7_s705_realrun.py`

| 改前 | 改后 |
|---|---|
| `_make_wal_saver(tmp_path / "s705_realrun.db")` | `run_dir = durable_run_dir(request, "s705_realrun")` → `_make_wal_saver(run_dir / _DB_NAME)` |
| `_real_initial_state(tmp_path)` | `_real_initial_state(run_dir)` |
| `finally:` 只 `conn.close()` | `finally:` 补 ①state best-effort 重取 ②`conn.commit/close` ③`_archive_evidence(...)` |
| 遵守率在用例体里现算 | 抽成 `measure_adherence()`，**打印 / 断言 / 归档物三处同源**（各算一遍必然漂移） |

**归档放 `finally` 是刻意的**：断言失败那次的证据往往最值钱。上一次白烧（`fix_loop_history` 恒空那次）如果有归档，根因排查不必靠回忆。

### 2.3 三条硬约束怎么守的

#### (a) 凭证卫生（四层，逐层可独立验红）

| 层 | 做法 | 覆盖的泄漏形态 |
|---|---|---|
| L1 **白名单导出** | 只导出 `CURATED_STATE_KEYS` 里的 state 键。`llm_config_set`（含 `api_key`）、`pending_user_input`、`messages` **根本不进 bundle** | 结构性泄漏（凭证就在 state 里） |
| L2 **敏感键名** | 键名命中 `api_key/secret/token/password/credential/authorization/cookie/…` ⇒ 值（含整个子树）替换；`max_tokens` / `token_usage` 等计数字段走白名单不误伤 | 嵌套字典里的凭证 |
| L3 **环境密钥值全文替换** | 从环境变量收集"名字像密钥"的**值**，在所有字符串里全文抹除 | **凭证被日志原样打出来**（键名无从判断，如 `curl -H 'X-Auth: sk-…'`） |
| L4 **形态正则** | `sk-…` / `ghp_…` / `hf_…` / `AKIA…` / JWT / `Bearer …` / `TOKEN=…` | 别人机器上产生的日志（该密钥不在本机环境变量里） |
| **L5 fail-closed 复扫** | 序列化成文本后**再搜一遍**已知密钥值，搜到就**拒绝写盘并抛异常**；报错信息只写密钥长度、**绝不回显密钥** | 前四层漏网 |

另：`build_bundle` 的顺序是 **jsonable → redact → truncate**，**脱敏必须在截断之前**——否则密钥骑在截断边界上会留下前缀残渣（这条有专门的用例 + 专门的变异验红，见 §3.2）。

#### (b) 默认 pytest 零副作用（两道独立防线）

1. **第一道**：`pytest.ini` 的 `addopts = -m "not e2e"` ⇒ e2e 用例体根本不执行。
2. **第二道**：`require_e2e_context(request, …)` —— 拿不到 `request.node.get_closest_marker("e2e")` 就**抛异常，且抛之前不碰任何磁盘**（连目录都不建）。防的是"有人把 marker 摘了"或"从单测里误调"。

两道防线**互相独立**：§3.2 的 gate 变异实测证明——把第二道拆掉，第一道仍然拦得住（`test_ev_40` 依旧绿）。

#### (c) 归档失败不许静默

`finally` 里捕获归档异常后，用 `sys.exc_info()` 判断**是否已有异常在传播**：
- 有（例如断言失败）⇒ 只打印归档错误，**不掩盖真正的失败**；
- 没有 ⇒ **上抛，让用例变红**（否则又变成"静默没留证"）。
两条分支都实测过（§3.3 B / §3.3 A）。

---

## 3. 离线证明（我用什么手段证的）

### 3.1 常规绿态

命令：`.venv/bin/pytest -q tests/test_realrun_evidence.py -p no:randomly`
结果：**29 passed（1.56s）**（取值时刻 2026-08-07 03:09 PDT）

分层：凭证卫生 10 条（T-EV-1x）/ 产物形状 5 条（T-EV-2x）/ e2e 闸门 5 条（T-EV-3x）/ 真 pytest 子进程 1 条（T-EV-40）/ 接线 4 条（T-EV-5x）/ 事后补档 4 条（T-EV-6x）。

### 3.2 变异验红（6 组，逐组证明断言真的受力）

**不改 `core/`**。用一次性 pytest 插件（跑完即删，未落仓库）把机制逐项打坏：

| # | 变异 | 期望 | 实测 |
|---|---|---|---|
| 1 | `require_e2e_context` → no-op（拆 e2e 闸门） | 闸门三条红 | **3 failed**：`test_ev_30` / `ev_30b` / `ev_31`；**`test_ev_40` 仍绿** ⇒ 坐实两道防线独立 |
| 2 | `redact` → 恒等函数（拆脱敏） | 脱敏四条红 | **4 failed**：`ev_13`（键名）/ `ev_15`（环境密钥值）/ `ev_16`（形态正则）/ `ev_17`（截断边界残渣） |
| 3 | `assert_no_known_secret` → no-op（拆 fail-closed） | 兜底两条红 | **2 failed**：`ev_18` / `ev_19`（写盘应被拒却写成功了） |
| 4 | `CURATED_STATE_KEYS` 加回 `llm_config_set` / `messages`（拆白名单） | 白名单一条红 | **1 failed**：`ev_12` |
| 5 | `build_bundle` 改成**先截断后脱敏**（顺序写反） | 顺序一条红 | **1 failed**：`ev_17` |
| 6a | 真跑用例里 `finally` 内的归档调用改名（模拟"归档被挪出 finally"） | 结构断言红 | **1 failed**：`ev_52` |
| 6b | 真跑用例改回 `tmp_path`（模拟根因复发） | 回归门红 | **1 failed**：`ev_53` |

6a/6b 的做法：生成变异副本到 `/tmp`，插件把 `rr.__file__` 指过去（**不动仓库里的真文件**）。

⚠ **`ev_17` 第一版不合格**：初版把密钥摆在长串正中央，截断本来就会把它掐掉 ⇒ 变异 2 下**它照样绿**（假绿）。改成把密钥摆在**头部保留窗口的边界上**（只有前 10 字符落在保留区内）后，变异 2 和变异 5 都能把它打红。**这是本轮自查抓到的第二个假绿**（第一个见 §5.1）。

### 3.3 整段函数体离线跑通（最强的一条离线证据）

用一次性插件把 `build_graph` 换成假图（零 LLM、零网络、零配额），让 **S7-05 真跑用例的整个函数体真跑一遍**——`durable_run_dir` / 真 SqliteSaver / `finally` 归档全都走真实代码路径：

**A. 成功路径**（假 state 含 3 轮 history + 一个假凭证）
```
>>> S7-05 T-S7-3-7 coder fix_note 遵守率: 2/3 = 67%
>>> 真跑证据已归档 -> /tmp/simproof_sink/2026-08-07_030638_s705-fix-note-adherence.json
>>> 原始产物（checkpoint 库 / workspace）留在 -> …/workspace/runs/s705_realrun_20260807_030638
1 passed
```
产物逐项核对（`REALRUN_EVIDENCE_DIR` 指向 `/tmp`，不污染仓库）：

| 核对项 | 实测 |
|---|---|
| `state_keys_present` | `['execution_result', 'fix_loop_count', 'fix_loop_history', 'reproduction_plan']` |
| 假凭证 `sk-SIMPROOFsecret…` 是否在文件里 | **False**（`stdout` 落盘成 `TOKEN=<redacted> leaked into logs`） |
| `llm_config_set` 是否出现 | **False**（白名单挡在门外，连键名都没有） |
| `redaction.known_secret_count` | `2`（本机 `.env` 里真有两个密钥变量；**只记条数，不记名字也不记值**） |
| `extra` | `task / paper_arxiv_id / run_dir / checkpoint_db / sandbox / adherence / thread_id / elapsed_seconds / raised` |
| 持久目录内容 | `s705_realrun.db`（4096 B，真 SqliteSaver 建的） |
| `git status` 是否被污染 | **否**（`workspace/` 已 gitignore） |

**B. 失败路径**（假 state 的 `fix_loop_history` 为空 ⇒ 断言必红）
```
>>> 真跑证据已归档 -> /tmp/simproof_sink_fail/2026-08-07_030707_…json
E   AssertionError: 应至少记录 1 轮修复（实际 0）；真实 coder 未进修复循环
1 failed
```
归档物里 `extra.raised = "AssertionError"`、`rounds = 0`、`state.error = "coder 未进修复循环"`。
⇒ **红的那次照样留证，且归档没有掩盖真正的失败**。

**C. 归档目录不可写**（`REALRUN_EVIDENCE_DIR=/proc/definitely-not-writable`）
⇒ 用例 **1 failed**（`FileNotFoundError`）⇒ **归档失败不会被静默吞掉**。

（A/B/C 的运行残留已全部清理；`workspace/runs/` 下无 `s705_realrun_*` / `should_not_appear_*` 残留，取值时刻 03:11 PDT。）

### 3.4 全量回归 + 账目精确对平

| 口径 | 结果 |
|---|---|
| `.venv/bin/pytest -q -m "not e2e and not browser"` | **2671 passed / 0 failed / 25 skipped / 58 deselected / 7 xfailed**（64.20s，取值时刻 2026-08-07 03:11 PDT） |
| 基线（本轮之前，同口径） | 2642 passed / 25 skipped / 58 deselected / 7 xfailed |
| **对平** | 2642 + **29**（`tests/test_realrun_evidence.py` 新增用例数）= **2671**，**逐条无余数** |
| e2e 收集 | `pytest --collect-only -q -m e2e` → 46 collected，含 `tests/test_sprint7_s705_realrun.py::test_s705_fix_note_adherence`（改动后仍可正常收集/导入） |
| **副作用检查** | 全量回归跑完，`docs/sprint7/test-reports/realrun-bundles` 与 `docs/sprint8/test-reports/realrun-bundles` **均不存在**；`workspace/runs/` 无新增 ⇒ **默认路径零写盘** |
| 警告 | 3 条，全部库级预存、与本次无关（langgraph `LangChainPendingDeprecationWarning` ×1 + Pydantic `.schema()` 弃用 ×2），与上一份报告一致 |

---

## 4. 诚实登记：哪些是"离线验过"，哪些**离线证不了**

### 4.1 离线已证（可随时复跑复核）

- 给定 state ⇒ 归档函数**确实写出**预期形状的 bundle JSON（§3.1 T-EV-32 / T-EV-50 + §3.3 A）；
- 凭证不进归档物（四层脱敏 + fail-closed，四组变异逐层验红）；
- **默认 pytest 下不写盘**（真 pytest 子进程两态对照：默认 `1 deselected` + 归档目录不存在 / `-m e2e` 恰好 1 份产物）；
- 归档发生在 `finally`（AST 结构断言 + 假图失败路径双证）；
- 真跑用例不再用 `tmp_path`（AST 回归门）。

### 4.2 离线**证不了**（必须等下一次真跑）

1. **真实链路上 `graph.get_state()` 拿到的 `fix_loop_history` 是否非空**——那取决于真实 LLM 是否走进修复循环。本报告全部用**构造 state**，假图不能替真实链路作证。
2. **真实日志里是否存在本模块脱敏规则没覆盖的凭证形态**——L4 是**已知形态**的白名单，未知形态天然在覆盖之外（L3 的环境值匹配是主力兜底，但只对"本机环境变量里有的值"有效）。
3. **`workspace/runs/` 在长时间真跑下的磁盘占用**——本次只跑出一个 4 KB 的空 db，真跑会带上整个 workspace（代码 + 日志 + 产物）。
4. **归档物在真实数据下的体积**——`MAX_STR_CHARS=20000` / `MAX_LIST_ITEMS=300` 是按经验拍的，真实 `execution_result` 可能大得多，会不会截掉关键信息未经真实语料检验。

> ⚠ **口径纪律**：以上任何一条都不允许把"离线验过"表述成"已生效"。**真实链路是否生效，要等下次真跑才知道。**

### 4.3 不可补救的部分（写死，不拿"可以重跑一次"当解法）

| 什么 | 状态 | 磁盘实证 |
|---|---|---|
| 2026-07-22 S7-05 真跑的 `fix_loop_history` 全文 / stdout / checkpoint 库 / 967s 分段耗时 | **永久灭失** | 上一份报告 §1.1 已实测（`find /` 零命中；`/tmp/pytest-of-*` 只剩最近 3 轮） |
| 2026-08-01 S7-10 UMAP 真跑的 `reproduction_plan` 原文 | **永久取不到** | **本轮新实测**：全仓库 `*.db`（排除 `.venv`）仅 4 个，最新的 `checkpoints.db` mtime = **2026-07-29 03:55**，早于 8/1 那次真跑 ⇒ 该次运行的 checkpoint 库不在磁盘上，事后补档入口**救不回它**（取值时刻 2026-08-07 03:10 PDT） |

⇒ 本次修复**只对今后的真跑有效**。CP-3.7-1 / CP-6.9-1 的既有结论与证据定性**一个字都不改**。

---

## 5. 失败排查 / 本轮自查抓到的问题

### 5.1 【自查缺陷】我自己写的 `test_ev_31` 犯了 P-S8-20 同款错误

- **现象**：跑完变异验红后，`workspace/runs/` 下多出 **5 个** `should_not_appear_*` 空目录（mtime 03:04）。
- **定位**：初版 `test_ev_31` 直接调 `ev.durable_run_dir(_FakeRequest(), "should_not_appear")`，落点算式写死 `PROJECT_ROOT/workspace/runs/`。**绿态没问题**（闸门先抛异常，不建目录）；但**红态**——也就是这条用例守的那个失效模式真的发生时——它会**真的在仓库路径下建目录**。5 个残留全部来自 gate 变异那几轮。
- **定性**：**测试代码 bug**，与 2026-08-07 早些时候修的 `P-S8-20`（`/tmp/evil.py`）**同族**：*用例的失败路径污染全局固定路径*。讽刺的是我上一轮刚修完这个族的缺陷。
- **处置**：已修。改为 `monkeypatch.setattr(ev, "PROJECT_ROOT", tmp_path)`，红态落点随用例隔离；并**补一条反证**断言真实仓库路径一个字节没碰。另补 `test_ev_31b` 用同样的隔离方式覆盖**落点契约**（`workspace/runs/<name>_<ts>/` + 非法 name 拒绝），断言**只增不减**。
- **复验**：重跑 gate 变异 ⇒ 仍然 **3 failed**（断言强度未降），且 `workspace/runs/` **零污染**；5 个残留目录已清。

**教训（建议立为纪律）**：**凡是"断言某个副作用不该发生"的用例，必须先把副作用的落点打到 `tmp_path`。** 判断依据不是"绿态会不会写"，而是"**红态会不会写**"——绿态不写是理所当然的，红态不写才是隔离。

### 5.2 【自查假绿】`ev_17` 初版不受力

见 §3.2 末尾。初版在变异 2 下照样绿 ⇒ 断言位置选错（密钥摆在必被截掉的位置）。已改到截断边界上，两组变异均可打红。

### 5.3 其它

无生产代码缺陷。**本轮全程未改 `core/`**。

---

## 6. 报告归档位置纠正（本次第二项任务）

| 项 | 内容 |
|---|---|
| 迁移对象 | `2026-08-07_p-s8-20-isolation-fix.md` |
| 从 | `docs/sprint7/test-reports/` |
| 到 | **`docs/sprint8/test-reports/`**（新建目录；缺陷 `P-S8-20` 登记在 `docs/sprint8/dev-plan.md:2468`） |
| 旧路径处置 | **留指针文件**（同名），正文指向新路径 + 写明迁移理由与时间 —— `docs/TODO.md` 曾引用旧路径，不让引用断链 |
| 内容改动 | 正文**一字未改**；仅页首 `Sprint` 行补迁移标注、文末 §6 把原「归档位置说明」逐字保留并追加处置段 |

新目录结构：
```
docs/sprint7/test-reports/
  … 12 份历史报告 …
  2026-08-07_s705-b3-realrun-forensics.md
  2026-08-07_s710-t69-realrun-handoff.md
  2026-08-07_realrun-evidence-fix.md            ← 本报告
  2026-08-07_p-s8-20-isolation-fix.md           ← 指针（内容已迁出）
  realrun-bundles/                              ← 真跑证据 bundle（本轮新增约定，**目前为空**，下次真跑才会长出东西）
docs/sprint8/test-reports/                      ← 本轮新建
  2026-08-07_p-s8-20-isolation-fix.md           ← 迁入的正文
```
⚠ `realrun-bundles/` **当前不存在**（不预建空目录）；第一次真跑归档时由代码自动创建。

---

## 7. dev-plan 改动（两处，均在授权边界内）

`docs/sprint7/dev-plan.md`，**不改任何 `[ ]`/`[x]` 勾选状态**，只加带署名与日期的说明块：

1. **T-S7-3-7（批次 3）**「需要实现的内容」末尾：加结构性补丁说明，并明写「**不改判 CP-3.7-1~3 的既有结论**，2026-07-22 那次永久不可补救」「机制仅离线验过，真实链路须等下次真跑」。
2. **T-S7-6-9 第 2 条（`:2739-2740`，S7-10 那条从未执行的要求）**：加**执行情况订正** —— 白纸黑字写明「**本条要求从未被执行**」「计划原文永久取不到、不可补救」「根因是要求没有执行力」，并给出今后可用的补档命令。

---

## 8. 后续动作

- **[待真跑]** 下次任何跑到修复循环的真跑（S7-05 靶或其它），顺带验证 §4.2 的四条离线证不了的项；**遵守率样本量仍是 n=1**，那次可零额外配额把样本补到 2。
- **[待接线]** 手工真跑（走 app / 脚本）目前**只能靠人记得跑一次补档命令**——这仍是"靠自觉"。若今后 S7-10 类真跑再发生，建议评估把 `dump_from_checkpoint_db` 接进运行脚本的收尾（本次未做，因 `scripts/` 不在本轮文件边界内）。
- **[建议立纪律]** §5.1 末尾那条：**"断言副作用不该发生"的用例，落点必须打到 `tmp_path`；判据是红态而非绿态。** 建议由主控写进 `.claude/agents/test-engineer.md`，否则只活在本报告里。
- **[承接上轮]** 报告纪律 **D-1**（基线值须注明取值时刻）本报告已逐条执行（所有计数、mtime、md5 类结论均带取值时刻）。
- 下次触发条件：①真跑窗口开启 ⇒ 跑完核对 `realrun-bundles/` 是否真长出产物（**这是"已生效"的唯一判据**）；②`tests/realrun_evidence.py` 或真跑用例被改 ⇒ 重跑 `tests/test_realrun_evidence.py` 全 29 条 + 6 组变异验红。
