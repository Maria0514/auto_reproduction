# 测试执行报告 - s705-b3-realrun-forensics（批次 3 真跑取证补档 + AC-S7-09~14 覆盖矩阵）

- **日期**：2026-08-07 02:40（PDT）
- **执行人**：@测试工程师代理
- **Sprint**：sprint7（批次 3 = S7-05 修复循环记忆增强）
- **触发原因**：`docs/sprint7/test-reports/` 12 份报告**无一份属批次 3**（最早三份均为 2026-07-19 批次 2；批次 3 交付日 2026-07-22，commit `8d37fe9`）⇒ `CP-3.6-7` / `CP-3.7-1` / `CP-3.7-2` / `CP-3.7-3` 四条留空。本报告做取证判定：证据还在不在，能关的关掉，关不掉的写死理由
- **commit**：`0e250fb`（改动未提交）
- **是否包含 e2e**：**否**。全程零真跑、零配额消耗；真跑部分是**对 2026-07-22 既有记录的取证与归档**，不是重跑

---

## 0. 结论先行（一句话）

**走的是 (a) 路线，但前提被订正了：记录物并非"只活在 commit message 里"，`docs/TODO.md:533-537` 有一份同日、更细的书面记录**（含逐轮遵守情况 + 两条 fix_note 原文摘录 + 凭证卫生四项）。缺的是**归档位置**，不是缺记录。⇒ **四条检查点全部可关**，但每条都标注了证据性质（当日书面记录 ≠ 可重算的原始数据）。

---

## 1. 取证过程（我怎么找的、找到什么、什么彻底没了）

### 1.1 原始数据：**确认永久灭失**

`tests/test_sprint7_s705_realrun.py:82` 把 checkpoint 库落在 `tmp_path / "s705_realrun.db"`，`:87` 的初始 state 也用 `_real_initial_state(tmp_path)` 把 workspace 指向 tmp。⇒ 真跑的 `fix_loop_history` 原始记录全部落在 pytest 临时目录。

| 查了什么 | 结果 |
|---|---|
| `find / -name "s705_realrun.db*"` | **0 命中** |
| `/tmp/pytest-of-yujingm/` 现存目录 | 只剩 `pytest-1026/1027/1028`（均 2026-08-07 生成）——pytest 默认只保留最近 3 轮，2026-07-22 那轮早被回收 |
| 仓库内 2026-07-21~23 之间被改动的文件（排除 `.git`/`.venv`/`__pycache__`） | 仅 **2** 个：`.gitignore`、`tests/test_sprint7_s705_realrun.py` ⇒ 真跑没往仓库里留任何产物 |
| `tests/fixtures/checkpoints_s7_99eef17bccf2.db` 是不是真跑产物 | **不是**。它是**现场靶历史事故库**（批次 2 固化的 fixture，commit `b5f3e63`），真跑只是"同构"它，并未写它 |
| `docs/sprint7/test-reports/` 是否曾有批次 3 报告后被删 | `git log --all --diff-filter=D` **0 命中**；`--name-only` 全集 12 份，与磁盘一致 ⇒ **从未存在过，不是被删** |

⇒ **逐轮 fix_note 全文、`fix_loop_history` 结构、967s 的分段耗时——这些无法复原，且不可能通过重跑复原**（LLM 服从度是一次性观测，重跑得到的是新样本不是旧证据）。

### 1.2 记录物：**找到了，而且比预想的多**

| 证据源 | 位置 | 写就时间 | 内容 |
|---|---|---|---|
| **E1** | `docs/TODO.md:533-537`（五条子项，`@Maria授权+主控`） | **2026-07-22（真跑当日）** | 见 §2 全文转录 |
| **E2** | commit `8d37fe9` 正文 | 2026-07-22 00:41 | 「真跑 967s 完整真实链路 fix_note 遵守率 3/4=75%，round3/4 自述明确引用前轮…R-S7-8 退化兜底真实生效（round1 空 fix_note 不阻断）」 |
| **E3** | `tests/test_sprint7_s705_realrun.py:117-133` | 磁盘现存 | 度量口径与打印格式的**源码本体** |

**E1/E2 互证，且 E3 能反推 E1 不是事后编的**：`test_sprint7_s705_realrun.py:119` 的度量式是 `with_note = sum(1 for r in history if (r.get("fix_note") or "").strip())`、`:126` 每轮打印 `OK` / `空(未遵守)`、`:132` 打印 `fix_note: {fn[:90]}`。E1 的记述形态（"round1 空…round2/3/4 均写"、两条以省略号截断的 fix_note 摘录）**与这段打印逻辑逐项对得上** ⇒ 它是对真实 stdout 的转录，不是回忆。

⚠ **但仍须如实定性**：E1/E2 都是**执行者自报**。原始 stdout 与 db 已灭失 ⇒ **不可独立重算**。本报告不把它拔高成"铁证"，只确认它是**当日、同步、双份、格式自洽**的书面记录。

---

## 2. 遵守率实测归档（E1 逐字转录，`docs/TODO.md:536`）

> **B 真跑成功（Maria 授权烧配额，967s=16min 完整真实链路，PASSED）**：**coder fix_note 遵守率 3/4 = 75%**（观测指标非硬失败）。round1 空（R-S7-8 确定性退化兜底真实生效、不阻断），round2/3/4 均写 fix_note。**最强铁证**：round3「上一轮仍可能残留外部仓库…」/ round4「修复回合中无法读取旧代码…」明确引用**前几轮试过什么**——坐实真实 coder 消费了 `fix_history_digest` 跨回合记忆做增量决策（S7-05 核心价值：coder 不再失忆，真实链路落地生效）。凭证卫生：走 degrade 降级不落盘、项目根无 `.secrets`、git status 无意外文件、tmp 自动清理。

配套记录（`:535`）——这次真跑之前**白烧过一次**，根因是测试代码而非生产代码：降级 resume 键名错（旧版回传 gate 发出的 `allow_degrade` + 空 value，而 `coding.py:822` 只认 `resume.get("degrade")`）⇒ 反复重弹、coder 进不了修复循环、`fix_loop_history` 恒空。修法是 `test_sprint7_s705_realrun.py` 改一行。**该教训已固化进该文件 `:96-100` 的注释**，磁盘可查。

### 2.1 度量口径核对（我方独立核）

| 项 | dev-plan 要求（`:857`） | 实测口径（`realrun.py:117-120`） | 一致？ |
|---|---|---|---|
| 分母 | 4 轮 | `total = len(fix_loop_history)` | ✅（mock sandbox 前 4 轮恒 import 失败、第 5 轮成功 ⇒ 恰好 4 轮，`:66-68`） |
| 分子 | "输出**非空有效** fix_note" | `(r.get("fix_note") or "").strip()` 非空 | ✅（空白串不计入，与 `_map_coding_result` 的落库校验同口径） |
| 判定 | 遵守率低**不阻断**（R-S7-8） | `:135` 只断言 `total >= 1`，遵守率不硬失败 | ✅ |

⇒ **口径没有为了好看而放宽**：分子用的是 strip 后非空，不是"字段存在"。

---

## 3. AC-S7-09~14 覆盖矩阵（CP-3.6-7 补做，**独立审计**）

审计方式：读 `tests/test_sprint7_s705_memory.py` 全文，逐个用例回读其断言，再反向核对 AC 原文（dev-plan `:826-834`）。**不是转抄开发侧 handoff**——批次 3 从未产出过 handoff（见 §4）。

**本文件用例总数 29，下表逐条列全，无遗漏、无重复计数。**

| AC | AC 要点（dev-plan `:826-834`） | 承载用例（`tests/test_sprint7_s705_memory.py::`） | 条数 |
|---|---|---|---|
| **AC-S7-09** | digest 含全部历史轮五元组（round + category + files_touched + fix_note + log_path），轮号升序、多行；**首轮不注入** | `test_cp_3_5_1_digest_full_retain`（五元组 + 升序 + 多行）／`test_cp_3_5_3_log_path_alignment`（第五元 log_path）／`test_cp_3_5_empty_history_returns_none`（空历史返 None）／`test_cp_3_6_5_existing_context_keys_unchanged`（首轮 context **不含** `fix_history_digest`） | 4 |
| **AC-S7-10** | 全保留控量：20 轮顶格仍含全部 20 轮、每轮 fix_note ≤120、总字节受上界、**无窗口字样** | `test_cp_3_5_2_full_retain_capacity_20_rounds` | 1 |
| **AC-S7-11 ★命门** | 链路落库注入：coder `fix_note` → `_map_coding_result` → `_append_fix_record` → digest；**三环逐环验红** | 绿臂：`test_cp_3_6_2_full_link_green`／环 1：`..._ring1_map_break_turns_red`／环 2：`..._ring2_append_break_turns_red`／环 3：`..._ring3_digest_break_turns_red`；分环单测：`test_cp_3_3_3_map_result_writes_last_fix_note_and_files`（写端）／`test_cp_3_4_1_append_takes_from_state`（取端）／`test_cp_3_4_2_time_ordering_self_consistent`（R-S7-10 时序自洽） | 7 |
| **AC-S7-12** | digest 的 `log_path` 与磁盘 `round_{n}.log` 对齐、`read_code_file` 读得到真错；**注入验红** | `test_cp_3_6_3_log_path_disk_aligned_and_readable`／`test_cp_3_6_3_inject_break_turns_red` | 2 |
| **AC-S7-13** | R-PC4 稳定前缀守门：新增 fix_note 指令是固定文案（跨 state 字节相同）；同 state 两次 digest 字节相同 | `test_cp_3_3_2_rpc4_stable_prefix_fixed_text`／`test_cp_3_6_4_system_prompt_byte_identical_across_state`／`test_cp_3_6_4_digest_byte_idempotent`／`test_cp_3_5_4_byte_idempotent` | 4 |
| **AC-S7-14** | 回归零退化：既有 coding context 键与 `_map_coding_result` 既有字段不变；`human_payload` 仍合法 sort_keys JSON、既有键值不变 | `test_cp_3_5_5_sort_keys_safe`／`test_cp_3_6_5_existing_context_keys_unchanged`／`test_cp_3_3_6_map_result_existing_fields_unchanged`／`test_cp_3_4_4_append_existing_fields_unchanged` | 4 |
| **R-S7-8**（软点退化，非 AC 但 CP-3.7-2 要求） | fix_note 缺失/空白/非串 → 退化为空；旧记录无该字段 → 该段留占位、其余四元组照常、不炸 | `test_cp_3_3_4_fix_note_validate_and_truncate`（缺/空白/非串 → `""`，超 120 截断）／`test_cp_3_5_6_old_record_backfill`（digest 印 `(coder 未自述)` + `(未记录)`，`round1 [import]` 与 `exec_logs/round_0.log` 照常） | 2 |
| **state 契约 / 旧 checkpoint 兼容** | `FixLoopRecord` + `GlobalState` 四字段；旧 checkpoint `.get` 兜底不 KeyError | `test_cp_3_2_1_state_keys_present`／`test_cp_3_2_2_old_checkpoint_compat`／`test_cp_3_2_3_initial_state_defaults`／`test_cp_3_4_3_old_checkpoint_backfill_safe` | 4 |
| **常量 / 抽取健壮性** | `_FIX_NOTE_MAX_CHARS=120`；`files_written` 走 `json.loads` 且过滤失败 ToolMessage（BUG-S1-02 规避） | `test_cp_3_3_1_fix_note_max_chars_const`／`test_cp_3_3_5_files_written_json_parse_and_filter` | 2 |
| | | **合计** | **29** |

**每条 AC 至少一个可测断言映射 ⇒ CP-3.6-7 的实质要求达成。**

实测（2026-08-07 02:2x PDT）：`.venv/bin/pytest -q tests/test_sprint7_s705_memory.py -p no:randomly` → **29 passed**（0.70s）。

### 3.1 覆盖矩阵审计中发现的**强项**（值得记一笔）

AC-S7-11 的三环验红做成了**常驻用例**（`ring1/2/3` 三条永久留在套件里、每次回归都跑），而不是"验红时临时改一改、验完删掉"。⇒ 日后任何人动这三环中的任何一环，**当场变红**。这比一次性验红强一个量级，是 sp7 批次 3 最硬的一条工程资产。

### 3.2 已知遗漏（诚实登记）

- **真实 LLM 是否稳定遵守 fix_note 输出约定**，离线不可测（这正是 R-S7-8 的定义域），只能靠真跑抽样 —— 已有 2026-07-22 一次 75% 的样本，**样本量 n=1 次运行 / 4 轮**，不足以谈稳定性。
- **967s 的耗时构成**（哪一段慢）无任何分段记录，且不可复原。

---

## 4. handoff 归档（CP-3.7-3 的另一半）

dev-plan `:863` 要求交付时把「AC-S7-09~14 覆盖矩阵 + coder fix_note 遵守率实测 + 已知限制（R-S7-8 / R-S7-11）」**交测试工程师**。

**实际发生的是：这次交接没有发生**——`docs/sprint7/` 下不存在任何批次 3 的 handoff 文档。本报告 §3（矩阵）+ §2（遵守率）+ §4.1（已知限制）**就是这份 handoff 的补做版本**，且由测试侧独立审计产出，而非开发侧自述转抄。

### 4.1 已知限制（随交付传下去）

1. **R-S7-8（唯一 LLM 软点）**：`fix_note` 依赖 coder 遵守输出约定。**实测遵守率 3/4 = 75%（n=1 次运行）**。不遵守时确定性退化为空、历史段其余四元组照常，**不阻断功能**（离线 2 条常驻用例 + 真跑 round1 双证）。**不得把 fix_note 当作可依赖字段来设计下游逻辑。**
2. **R-S7-11**：历史轮日志被清 ⇒ `log_path` 指向不存在的文件。当前处置是"coder read 到文件不存在则退回当前轮反馈"，**降级到 sp6 现状，不炸**。⇒ 用户若清理 `exec_logs/`，跨回合记忆的"真错日志指针"这一元会静默失效。
3. **本次新登记（取证副产物）**：**真跑测试把 checkpoint 库和 workspace 都落在 `tmp_path`** ⇒ **跑完即灭失、事后不可复核**。这是批次 3 无法留证的**结构性原因**，不是执行者疏忽。**S7-10 已经吸取过同一教训**（dev-plan `:2739-2740` 要求"跑前落盘 bundle JSON"），但**批次 3 的这条至今没改**——见 §6 后续动作。

---

## 5. 检查点处置结论

| CP | 处置 | 依据 |
|---|---|---|
| **CP-3.6-7**（AC 覆盖矩阵审计，映射落 handoff） | **✅ 勾**（补做） | §3 矩阵：29 用例逐条落表、每条 AC 有映射、用例名逐条在磁盘且 29 passed 实测。**标注：2026-08-07 补做的独立审计，非交付时留证** |
| **CP-3.7-1**（真跑遵守率抽验 + 记录证据） | **✅ 勾** | 度量已做（3/4=75%）、证据已记（E1 当日书面 + E2 commit + E3 源码口径互证，§1.2/§2）。**标注：证据为当日自报书面记录，原始 stdout/db 已灭失、不可重算** |
| **CP-3.7-2**（R-S7-8 退化验证） | **✅ 勾** | **双证**：①真跑侧 round1 空 fix_note 不阻断（E1/E2 同日双记）；②离线侧 `test_cp_3_3_4` + `test_cp_3_5_6` **常驻确定性用例**今日实测绿，逐条覆盖"退化为空 / 四元组照常 / 不炸"三要素。**离线这一半随时可复核，不依赖已灭失的真跑数据** |
| **CP-3.7-3**（真跑证据齐 + handoff 归档） | **✅ 勾** | 端到端链路真跑证据成立：round3/round4 的 fix_note **自述引用前轮**，等于反证 digest 真的进了 coder 的 prompt ⇒ 落库→append→渲染三环在真实链路上全通。handoff 由本报告 §3/§4 补做归档。**标注：归档为 2026-08-07 补做，交付时（2026-07-22）确实未归档** |

⚠ **为什么我推翻了 2026-08-06 的留空判定**：那次判定的前提写在 `docs/TODO.md:1149`——「度量细节**只活在 commit message 里**，不可复核」。该前提**不成立**：`docs/TODO.md:533-537` 有同日、更细的记录（逐轮遵守 + 两条 fix_note 原文摘录 + 凭证卫生四项），只是没归到 `test-reports/`。⇒ 真实缺陷是**归档位置错**，不是**没留证**。按"依证据说话"的账目纪律，证据既在，就该关。

**没有为了让它变绿而降低标准的地方**：原始 db 灭失这条，我在每一格里都写明了，也没拿"可以重跑一次"当解法（重跑得到的是新样本，不是旧证据，且耗配额）。

---

## 6. 报告纪律 **D-1**（本次挖出，立为项目级纪律）

**凡在报告里记校验值 / 行数 / 字节数 / 文件哈希这类"会被后续动作作废"的基线，必须注明取值时刻，或在报告归档前重取一次。**

**触发实证**（原文一字未改，历史留档不追改）：

| 位置 | 报告写的 | 磁盘实际 | 差异成因 |
|---|---|---|---|
| `2026-07-19_batch2-regression-targeted.md:103` | fixture md5 固化基线 `3483890cd0197a27309543a48a2ece3f` | **`9c00dcd2060f67718a9b8ec5c4348ce6`**（磁盘 = `git show HEAD:` 逐字节相同 ⇒ 入库的从来就是 `9c00…`） | 报告写就后 fixture 又被**重新生成过**，报告里的基线值没跟着更新 |
| `2026-07-19_batch2-regression-targeted.md:104` | 「**无 `-wal`/`-shm` 旁文件**（只读连接…）」 | `checkpoints_s7_99eef17bccf2.db-shm` **存在**（32KB，mtime 2026-08-07 02:11，被今日回归刷新） | 该结论只在"刚跑完那一刻"为真；靶测每跑一次 `-shm` 就被刷新一次 |

**危害**：这两条都是**"证明只读契约没被破坏"的核心物证**。基线值对不上时，后人无法区分"fixture 被污染了"和"报告没更新"——而这两者的处置天差地别（前者要重建 fixture、后者只要改一行报告）。本次即为此多花了一轮排查。

**执行方式**（下次起）：
- 写基线值时同时写 **`（取值时刻 YYYY-MM-DD HH:MM）`**；
- 或在报告最终归档前 `md5sum` / `wc -l` **重取一次**再落笔；
- 「无旁文件」这类**瞬时状态**结论，改写成「**截至 <时刻> 无旁文件；注：只读打开会生成 `-shm`，其刷新属正常行为**」。

---

## 7. 后续动作

- **[新增建议]** `tests/test_sprint7_s705_realrun.py` 的产物落点问题（§4.1 第 3 条）：真跑证据落 `tmp_path` ⇒ 结构性不可复核。建议参照 S7-10 的做法，在真跑用例里把 `fix_loop_history` 全文 dump 成 bundle JSON 落到 `docs/sprint7/test-reports/` 或 `workspace/runs/`。**这是"下次真跑前"就要做的，否则下一次真跑一样留不下证据。**
- **[新增建议]** 报告纪律 D-1 建议由主控写进 `.claude/agents/test-engineer.md` 的「测试报告归档规范」，否则只活在本报告里、下次照样犯。
- **[遗留]** 遵守率样本量 n=1（4 轮）。若日后有 Maria 授权的真跑窗口，建议**顺带**再采一次遵守率（零额外配额：任何跑到修复循环的真跑都能顺带统计），把样本从 1 补到 2~3。
- 下次触发条件：批次 3 相关代码（`coding.py::_digest_fix_loop_history` / `_map_coding_result`、`execution.py::_append_fix_record`、`state.py` 四字段）任一被改 ⇒ 重跑 `tests/test_sprint7_s705_memory.py` 全 29 条并回归本矩阵。
