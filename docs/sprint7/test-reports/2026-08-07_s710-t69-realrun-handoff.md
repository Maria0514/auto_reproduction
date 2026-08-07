# 测试执行报告 - s710-t69-realrun-handoff（S7-10 T-S7-6-9 UMAP 真跑取证补档 + handoff 归档）

- **日期**：2026-08-07 02:45（PDT）
- **执行人**：@测试工程师代理
- **Sprint**：sprint7（批次 6 = S7-10 计划与编码/执行落点对齐）
- **触发原因**：`CP-6.9-5`（handoff 归档）留空 —— 真跑做了但归档物不存在。主控 2026-08-06 已把 `CP-6.9-1~4` 逐条补证并勾上，只剩本条。本报告判定"补档的原料够不够"，够则补
- **commit**：`0e250fb`（改动未提交）
- **是否包含 e2e**：**否**。**未重跑任何真跑**、零 deepxiv 配额、零 LLM 调用。全部结论来自**磁盘现场取证 + 离线用例复跑**

> ⚠ **本报告不是** `2026-07-31_s710-independent-acceptance.md` 的续篇。那一份自己写着「**未做**：任何 e2e / 真跑」「AC-S7-54 / AC-S7-55 **未验证（延后）**」——**它是真跑之前的离线验收，不能当真跑报告用**。本报告补的正是它缺的那一块。

---

## 0. 结论先行

**原料够，补档成立。** 现场 `workspace/1802.03426_archive_run3_20260801/code/` 留存的 `exec_logs/round_0.log` + `round_1.log` **逐条记着实际执行的 argv**，可支撑本批主判据。

同时挖出**两件事**：
- **① 现场里有两拨运行，主控 2026-08-06 的 CP-6.9-2 证据清单把两拨混着引了**（详见 §1.2）。订正后主断言**反而更干净**。
- **② `docs/sprint7/dev-plan.md:2964` 要求"真跑收口须补记两项计数"，这两项从来没人记过。我补记了，其中第一项是红信号**（详见 §3）。

---

## 1. 取证：这份现场到底是哪一次跑的

### 1.1 三次 UMAP 真跑的现场归属（已逐一钉死）

| 现场目录 | `exec_logs` 特征 | 归属 | 依据 |
|---|---|---|---|
| `workspace/1802.03426_archive_run3_20260801/` | `round_0.log` 36255B / `round_1.log` 693B | **S7-10（本批）** | `2026-08-02_s711-real-run.md:11` + `:40` 明写「上一次现场已归档为 `1802.03426_archive_run3_20260801`」 |
| `workspace/1802.03426_archive_run4_20260801/` | `round_0/1/2.log` = 21419 / 17080 / 17878 B | **S7-11** | 与 `2026-08-02_s711-real-run.md:260` 记的三个字节数**逐字节吻合** |
| `workspace/1802.03426/`（live） | 仅 `round_0.log` 12666B | **S7-13** | 与 `2026-08-02_s713-real-run.md:147`「仅一轮」吻合 |

### 1.2 ⚠ **订正：run3 目录里叠了两拨运行，不是一拨**

同一个目录被**先后两次运行覆写**（同名文件被原地重写，故 `code/` 目录自身 mtime 停在第一拨）：

| 波次 | 时间窗 | 产物特征 | 是哪一次 |
|---|---|---|---|
| **W1** | 2026-07-31 03:21–03:28 | `scripts/{prepare_data,run_benchmark,plot_embeddings}.py`；`outputs/{pendigits,mnist,coil20,figures,data}/` + `outputs/summary.json` | **S7-10 之前的"首次真跑"** —— 即挖出「编码产物成孤儿」、且**执行环节违规写代码才救回结果**的那一次（R-S7-47 说的就是它） |
| **W2** | 2026-08-01 03:01–03:12 | `scripts/{prepare_datasets,run_visualization_benchmarks,run_knn_evaluation,run_runtime_benchmarks,summarize_results}.py`；`outputs/{data_cache,visualization,knn_eval,runtime,summary}/`；**现存的 `round_0.log` / `round_1.log`** | **S7-10 T-6-9 的验收真跑** |

**三条独立证据锁定 W2 = 本批验收真跑**：
1. `docs/sprint7/dev-plan.md:2979` 白纸黑字：「S7-10 是刚提交的批次 6（HEAD `c480990`，**2026-07-31 交付、2026-08-01 完成真跑**）」；
2. commit `c480990` 时间为 **2026-08-01 02:42**，W2 起跑于 **03:01**——**交付后 19 分钟**，与"三条约束落地后再跑验收"的顺序完全自洽（W1 在交付前 23 小时，不可能是验收跑）；
3. W2 的 `outputs/` 子目录名与 `exec_logs` 里的 `--output_dir` 参数**逐个对得上**（`outputs/data_cache` ← step#8、`outputs/visualization` ← step#10、`outputs/knn_eval` ← step#12、`outputs/runtime` ← step#14、`outputs/summary` ← step#16），mtime 也按执行顺序单调递增（03:04 → 03:06 → 03:08 → 03:10 → 03:10）。

**⇒ 对 `dev-plan.md:2750`（CP-6.9-2 补勾注）的订正**：那条把 `coil20/ mnist/ pendigits/`（五份 `*_embedding.npz` + `results.json`）也列为"孤儿消失"的物证 —— **那三个目录属 W1，是"违规写码救回结果"那一波的产物，恰恰是本批要治的病，不能当疗效**。真正的疗效证据是 W2 的 `visualization/ knn_eval/ runtime/ summary/` 四个目录：它们由**编码环节产出的 `scripts/*.py` 被计划步骤直接调用**而生成。

**订正的方向是"证据变干净"而非"结论翻案"**：CP-6.9-2 的主断言「孤儿消失」**依然成立，且比原来引得更准**。另：那条注里自陈"后半截（`run_*.py` 内容与 `files_written` 比对哈希/mtime）今日已验不了"，其成因也随之清楚了——**是 W1/W2 两拨叠在同一目录**，不是文件被 execution 改写。

---

## 2. 真跑报告本体（W2，2026-08-01 03:01–03:12）

### 2.1 实际执行的命令全表（round_0，17 条；路径已缩写去掉 `<code_dir>/` 前缀）

| step | exit | 命令 | 性质 |
|---|---|---|---|
| 0 | 0 | `.venv/bin/python -m pip install -e <repos>/lmcinnes__umap` | 装依赖，**仓库以路径参数出现** |
| 1 | 0 | `.venv/bin/python -m pip install numpy scipy scikit-learn numba matplotlib pandas seaborn` | 装依赖 |
| 2 | 0 | `.venv/bin/python -c import umap; print('umap_import_ok')` | 只读探测 |
| **3** | **1** | `.venv/bin/python scripts/prepare_datasets.py --datasets … --output_dir outputs/data_cache` | **失败：`ModuleNotFoundError: No module named 'repro_umap'`** |
| 4 | 0 | `.venv/bin/python -c import os; print(os.listdir('.'))` | 只读探测（agent 自查） |
| 5 | 0 | `.venv/bin/python -c import os; print(os.path.exists('repro_umap/__init__.py'))` | 只读探测 |
| 6 | 0 | `.venv/bin/python -c import sys; print(sys.path[0])` | 只读探测 |
| 7 | 0 | `.venv/bin/python -c import repro_umap; print('repro_umap_ok')` | 只读探测（验证假设） |
| 8 | 0 | `.venv/bin/python -m scripts.prepare_datasets …` | **改形态后成功** |
| **9** | **1** | `.venv/bin/python scripts/run_visualization_benchmarks.py …` | 失败（同根因） |
| 10 | 0 | `.venv/bin/python -m scripts.run_visualization_benchmarks …` | 改形态后成功 |
| **11** | **1** | `.venv/bin/python scripts/run_knn_evaluation.py …` | 失败（同根因） |
| 12 | 0 | `.venv/bin/python -m scripts.run_knn_evaluation …` | 改形态后成功 |
| **13** | **1** | `.venv/bin/python scripts/run_runtime_benchmarks.py …` | 失败（同根因） |
| 14 | 0 | `.venv/bin/python -m scripts.run_runtime_benchmarks …` | 改形态后成功 |
| **15** | **1** | `.venv/bin/python scripts/summarize_results.py …` | 失败（同根因） |
| 16 | 0 | `.venv/bin/python -m scripts.summarize_results …` | 改形态后成功 |

round_1（修复轮，2 条）：

| step | exit | 命令 | 结果 |
|---|---|---|---|
| 0 | 0 | `.venv/bin/python -m scripts.summarize_results …` | **`<METRICS>{"best_knn_accuracy": 0.8302814666666667, "fastest_runtime_sec": 0.07277125, "visualization_runs": 20}</METRICS>`** |
| 1 | 0 | `.venv/bin/python -m py_compile scripts/*.py`（5 个脚本） | 语法自检通过 |

### 2.2 逐条判据（对齐 DA-S7-10-7/8 与 CP-6.9-1~4）

| 判据 | 结论 | 证据 |
|---|---|---|
| **零条命令含 `cd` 进 `workspace/repos/**`**（约束 A/B） | ✅ | 全 19 条逐条核过，**一条 `cd` 都没有**；全部在 `code_output_dir` 下以该目录 `.venv/bin/python` + 相对路径执行 |
| **零条命令命中内联写码谓词**（约束 C 语义面） | ✅ | 5 条 `python -c`（step#2/4/5/6/7）**全是只读探测**，无一条把文件内容当字面量写盘 |
| **`pip install -e` 以路径参数出现而非 `cd`** | ✅ | step#0 |
| **主断言：孤儿消失** | ✅ | 编码产出的 5 个 `scripts/*.py` **逐条被计划步骤调用**，并在 `code_output_dir/outputs/` 下产出 `visualization/ knn_eval/ runtime/ summary/`（见 §1.2 订正） |
| **首轮失败归因不是编码产物自身 bug**（CP-6.9-4①，对照 R-S7-54 的 6 处 `%`） | ✅ | 5 次失败全是 `ModuleNotFoundError: No module named 'repro_umap'`——**包导入路径形态问题**（`python scripts/x.py` 进不到包），与 `%` 优先级 bug 无关 |
| **NO_METRICS 正确回修复循环**（CP-6.9-4②） | ✅ | `round_1.log` 存在即证进了第二轮 |
| **修复轮 coder 拿到真 stderr**（CP-6.9-4③） | ✅ | `round_0.log` 文件头就是「错误摘要区（error-first，真报错前置）」，5 条失败步 stderr 原文前置（S7-02 机制生效） |
| **`metrics_groups` 是否仍为空**（CP-6.9-4④） | 指标**非空** | round_1 step#0 的 `<METRICS>` 块见上 |

### 2.3 附带坐实的一条既有欠账

step#11/#13/#15 用 `python scripts/x.py` 报错 → step#12/#14/#16 改 `python -m scripts.x` 即成功。**这正是 `docs/TODO.md:719③` / `:658` 记的「计划命令形态与编码产出包结构无契约」的实证现场**：计划侧按脚本路径写命令，编码侧却把代码组织成包，两边没有约定，靠 execution agent 现场试错弥合（本次试了 4 次、每次多烧一轮）。

---

## 3. ⚠ **补记两项计数**（`dev-plan.md:2964` 要求，此前从未有人记）

原文：「**真跑收口须补记两项计数**（架构 §19.11，S7-06/S7-07 教训）：①工具层拒绝**触发次数**；②每次拒绝后 agent 是否**在 1 轮内**改出合规命令。**触发 0 次与触发后不能自愈，同样是红信号。**」

| 计数 | 实测值 | 判读 |
|---|---|---|
| **① 工具层拒绝触发次数** | **0** | `grep -c "拒绝\|不得用于写代码\|tool_error\|exit=-1"` 对 `round_0.log` / `round_1.log` **双双为 0**。按 dev-plan 自己定的口径，**这是红信号**——约束 C 的硬防线在本次真跑中**一次都没被实际行使** |
| **② 拒绝后 1 轮内自愈** | **不适用**（分母为 0） | 无拒绝可观测 |

**怎么判读这个 0**（我方结论，供架构师/PM 复核）：

- **它更可能是好消息而非坏消息**：约束 A/B（planning 侧不再授权 `cd` / 不再产占位符步骤）同批生效后，**agent 压根没试图内联写码** ⇒ 硬防线没被触发是"上游治好了"的表现，不是"防线失灵"。
- **但它确实意味着"约束 C 在生产路径上零实战验证"**：本批对 C 的全部信心来自离线用例（`test_cp_6_6_1_*` 起子进程喂 `round_0.log:121` 原始罪证载荷 → 真拒；`test_bug_s7_10_01_*` 三组各 6 形态守正负边界）。这些**够强**（真起子进程、非 mock），但**不能替代生产路径的一次实际触发**。
- **建议的处置**：**不要**为了把这个 0 变成非 0 而专门烧一次配额。改为**在下次任何一次真跑里顺带统计这两项**（零额外成本），累计 2~3 次真跑仍为 0，即可把结论从"未验证"升级为"上游约束已使 C 成为兜底而非常规路径"。

**另记一条相邻的自愈证据**（不是 §3 要求的那一项，但同源）：step#3 失败后 agent 用 4 条只读探测定位根因，随后**在同一轮内**改出合规形态并成功（step#4~#8），此后遇到同类失败**一步即改**（#9→#10、#11→#12、#13→#14、#15→#16）。⇒ agent 的**失败自愈能力**在本次真跑中是实测成立的。

---

## 4. handoff 归档（CP-6.9-5 的五个组成部分）

### 4.1 本报告 ✅ —— 即 §1~§3。

### 4.2 T-6-8 覆盖矩阵 ✅ —— 落点 `dev-plan.md §48.3`（AC → CP → 用例名三列）。

**我方独立核实（不是转抄）**：程序化抽出 §48.3 引用的**全部 40 个用例名**，逐个到 `tests/test_*.py` 里查 `def <name>(`：

- **38 个具名用例全部命中，0 缺失**；
- 另 2 个是通配写法：`test_cp_6_5_3_w4_*` 实际展开为 **4 条**（`_fires_on_cd_into_selected_repo` / `_fires_on_repos_marker_without_resource_info` / `_silent_on_clean_relative_plan` / `_survives_missing_resource_info`），`test_cp_6_5_4_w5_*` 展开为 **3 条**（`_fires_on_real_corpus_command` / `_shares_one_predicate_with_tool_layer` / `_silent_on_script_run`）——**与矩阵里标注的「4 条」「3 条」精确对平**。

今日实测：`.venv/bin/pytest -q tests/test_sprint7_s710_exec_locality.py tests/test_sprint7_s710_gap_audit.py -p no:randomly` → **72 passed**（0.78s）。

### 4.3 四道命门验红证据 ✅ —— 落点 `dev-plan.md:2715`（CP-6.8-2），红绿两态齐全：

| 命门 | 红态 → 绿态 |
|---|---|
| ① C 硬拦截（CP-6.6-5） | 5 failed + 磁盘副作用独立探针 → 8 passed |
| ② execution 字节门（CP-6.2-2） | 1 failed / 12 passed → 13 passed（**外加 CP-6.4-1 的天然当场红**） |
| ③ planning 字节门（CP-6.3-3） | 1 failed / 18 passed → 19 passed |
| ④ 术语守门 W4/W5 + `EXPECTED_N`（CP-6.5-6） | 两断言同红 → 7 passed |

补充：`2026-07-31_s710-independent-acceptance.md` 记的独立验收又在此之上报出 **1 个生产缺陷（BUG-S7-10-01）+ 4 处假绿**，逐条处置见 `dev-plan.md:2721`（CP-6.8-9）。**其中 F2 那条尤其值得传下去**：`test_ac_s7_52_*` 的 C 臂原本是**源码子串检查**——死代码也能满足，改成真调工具层的行为断言后才具备杀伤力。

### 4.4 已知限制（**必须随交付传下去**）✅ —— 落点 `dev-plan.md §48.4`，此处重述要害两条 + 补三条：

1. **R-S7-49（点名要求含）**：`step_reconciliation` 的 N/N 是 agent **自报归属**，**不证明计划被忠实执行**。任何验收**不得引它作证**。用户在报告里看到的"已完成 N/M 步"可能虚高。
2. **R-S7-54（点名要求含）**：编码环节那份历史产物**自身跑不通**（6 处 `%` 优先级 bug，`run_repro_basics.py:127/146/149/152/156/169`，`py_compile` 过得去、运行期必崩）⇒ 真跑首轮很可能整体失败，**那是预期且正确的**。
3. **R-S7-57**：极短写码漏放（`open('x.py','w').write('pass')` 类）任何可行阈值都拦不住，**已知且被接受**。⚠ 边界：**只有"载荷短于阈值"这一种**算 R-S7-57；"载荷超长却没被拦"是**谓词缺陷**（BUG-S7-10-01 即 1304 字符照样绕过），归错会掩盖生产缺陷。
4. **R-S7-58**：长探针被拒后 agent 可能空转（已知语料中 181 字符那条三连 mkdir 会被拒）。
5. **本次新登记（取证副产物）**：**真跑的 `reproduction_plan` 全文 bundle 从未落盘** —— `dev-plan.md:2739-2740` 明确要求过（"否则本次验收会重蹈证据回收覆辙"），**但没执行**。后果：`CP-6.9-1` 要求验的是**落盘 `execution_steps` 原文**，而现存证据是**执行侧日志**。执行侧日志比自报对账硬得多（记的是真 argv），但**严格说是强旁证不是计划原文**，且这条**永久无法补救**。

### 4.5 未跑项显式登记 ✅

| 未跑项 | 状态 | 依据 |
|---|---|---|
| **对照篇**（`dev-plan.md:2746`，"第 3 顺位可砍"：再跑一篇有公开仓库的论文交叉验证 A/B 不是 umap 特例） | **未跑** | `workspace/` 下 2026-07-31~08-01 时间窗内**只有 1802.03426 一篇**有运行痕迹，其余论文目录 mtime 最近的是 2026-07-17。⇒ A/B 约束**只在 UMAP 一篇上验过，"不是特例"这一条至今未被证明** |
| **`CP-6.7-2` 共享克隆缓存清理** | 当时红、后被处理 | `dev-plan.md:2963` 要求"T-6-9 真跑前必须完成"；主控 2026-08-06 实测 `workspace/repos/lmcinnes__umap` 的 `git status --porcelain` **为空**。⚠ 但那是**今日状态**，中间隔着 2026-08-01 的授权清理 + S7-11/S7-13 两轮真跑 ⇒ 证明"仓库现在干净"，**不完全等于"真跑当场就干净"** |
| **§3 的两项计数** | 本报告补记 | 见 §3 |

---

## 5. 结果摘要（本次执行）

- 命令：`.venv/bin/pytest -q tests/test_sprint7_s710_exec_locality.py tests/test_sprint7_s710_gap_audit.py -p no:randomly` → **72 passed**；全量回归见 `2026-08-07_p-s8-20-isolation-fix.md`（**2642 passed / 0 failed / 25 skipped / 58 deselected / 7 xfailed**，2026-08-07 02:33 PDT）
- 失败：0；跳过：0（本文件范围内）；警告：1 条 langgraph 库级预存
- 真跑：**未执行**（本报告为取证补档）

## 6. 失败排查

无失败。§1.2 的证据归属订正**不是失败**，是补档过程中对既有补勾注的精度修正（结论方向不变、证据更准）。

## 7. 检查点处置

| CP | 处置 | 依据 |
|---|---|---|
| **CP-6.9-5**（handoff 归档） | **✅ 勾** | 本条要求的五个组成部分（本报告 / 覆盖矩阵 / 四道命门验红证据 / 已知限制含 R-S7-49+R-S7-54 / 未跑项显式登记）**逐条齐备**，见 §4.1~§4.5。**标注：2026-08-07 补做归档，交付时（2026-08-01）确实未归档**；且档内如实登记了 §4.4 第 5 条那项**永久不可补救**的缺口 |
| CP-6.9-1~4 | 维持主控 2026-08-06 的勾 | 另附 §1.2 证据归属订正（CP-6.9-2） |

**没有为了变绿而放宽的地方**：`CP-6.9-1` 那句"证据是执行侧日志而非计划原文"我在 §4.4 第 5 条里再次写死，并注明**永久不可补救**；`CP-6.9-3` 的"今日干净 ≠ 当场干净"也原样保留在 §4.5。

## 8. 后续动作

- **[红信号，需表态]** §3 的"工具层拒绝触发 0 次"按 dev-plan 自己的口径属红信号。**建议**：不为它单独烧配额，改为在**下次任何一次真跑里顺带统计**，累计 2~3 次仍为 0 即可结案。**需架构师/PM 认可这个处置口径。**
- **[缺口，无法补救]** 真跑 plan bundle 未落盘（§4.4 第 5 条）。**建议**：把"真跑前先落盘 `reproduction_plan` 全文 bundle"从 dev-plan 的一句要求，变成**真跑用例里的一段代码**——写成要求已经失败过一次了。
- **[未证明]** 对照篇未跑 ⇒ 「A/B 约束不是 umap 特例」至今无证据。若日后有真跑窗口，这是**优先级最高的加分项**。
- **[待迁]** 本报告与 `2026-08-07_s705-b3-realrun-forensics.md` 立的**报告纪律 D-1**（记基线值须注明取值时刻）同样适用于本报告：§1/§2 引的所有 mtime / 字节数取值时刻均为 **2026-08-07 02:2x~02:45 PDT**。
- 下次触发条件：`core/nodes/planning.py` / `execution.py` / `core/plan_checks.py` 三者任一被改 ⇒ 重跑 §4.2 的 72 条并回归本档判据。
