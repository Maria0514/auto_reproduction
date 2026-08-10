# 测试执行报告 - w6-criteria-guard-02（双口径塌成单口径 + 验红实测）

- **日期**：2026-08-09 22:53 PDT
- **执行人**：@测试工程师代理
- **Sprint**：sprint8
- **触发原因**：主控实测坐实上一轮交付的 W6 护栏「**验红条件不成立**」——架构 §15.5 G8 写死「把
  `_digest_paper_analysis` 的 `baseline_results` 键去掉 → 本条必红」，实际摘行后 **133 条一条没红**。
  根因：上一轮按指令把第三候选源相关用例写成**双口径分支**（裁定并行进行中，不押注落地时点），
  裁定落地后**无人负责把它塌回单口径** ⇒ 摘键只是让分支静默切回 A 支继续绿。
- **commit**：`8a03bbc`（工作区含未提交改动：`core/nodes/planning.py` 的 `AR-S8-16` 那一行 +
  两份架构文档 + 本轮两个测试文件）

---

## 一、本轮做了什么

### 1. 选路：**两条都做**（主控给的是二选一，我选合并）

| 路 | 单独做的问题 | 结论 |
|---|---|---|
| A. 塌掉双口径、只留硬断言 | 摘行即红 ✅，但**没有任何东西证明它以后还红** —— 后人再软化一次（改回 `if/else`、或把断言换成 `.get()` 容忍式）不会有红灯 | **必做**，它是 `AR-S8-16` 裁定的落地承载 |
| B. 把突变夹具扩到 `planning.py` 的 digest | 让「摘键必翻面」成为**常驻用例**（每轮回归都在内存里摘一次），但**正向断言仍是双口径**，G8 的期望值没有单口径落点 | **必做**，它是防复发的机制 |

⇒ A 给「现在是对的」，B 给「以后跑偏会有人知道」。**成本极低**：B 复用了本文件已有的 AST 内存突变
骨架，净增 ~50 行，零磁盘写入、不碰 `core/` 一个字节。

### 2. 塌掉的两条双口径分支

| 原用例 | 原写法 | 现写法 |
|---|---|---|
| `test_w6_third_candidate_source_baseline_results_adapts_to_either_ruling` | `if baseline in terms: assert 不报 else: assert 报` | `test_w6_third_candidate_source_is_baseline_results_keys`：**白盒硬断言** `baseline in _paper_fact_terms(...)` + 行为断言不报 |
| `test_w6_on_paper_reported_baseline_through_real_digest` | `if reaches_production: assert 不报 else: assert 报` | 拆成 `test_g8_...`（架构 G8）+ `test_g9_...`（架构 G9）+ `test_production_digest_key_set_is_exact`（G10 内层面），全部单口径 |

### 3. 新增（净 +3 条用例）

- `test_g8_paper_reported_baseline_reaches_check_plan` —— 架构 §15.5 **G8** 的唯一承载。输入逐字
  照架构（`metrics: []` / `datasets: []` / `baseline_results: {"BM25_R2": 0.43}`），走**真实**
  `_digest_paper_analysis`，禁止手搭 payload。
- `test_g9_vague_criteria_warns_when_only_baseline_present` —— 架构 §15.5 **G9**（反向）。
- `test_production_digest_key_set_is_exact` —— 精确 5 键 `==`，补 `AR-S8-16` 自己点名的
  「`paper_analysis_summary` **内层键无人守**」缺口（外层 11 键守门在
  `test_sprint7_s708_payload_probe.py`，架构 G10 要求它零改动，本键是内层子键，不受影响）。
- `test_red_state_digest_without_baseline_results` —— **常驻验红**（替换掉「靠人手工摘一行去试」）。
- `test_mutation_harness_is_side_effect_free` 扩到覆盖 `planning.py`（磁盘字节 + 真身返回双核）。

---

## 二、🔴 唯一判据实测：摘掉那一行 → 红几条

**方法**（不碰 `core/`，不写真实工作区）：`mktemp -d` 建**完整源码沙箱副本**（rsync 排除
`.venv` / `.git` / `workspace` / `workspaces` / `deepxiv_sdk_repo` / `checkpoints.db` / `.env`，
237M），在**副本**里摘掉 `core/nodes/planning.py:896` 那一行，前后各跑一次全量默认域，取 **delta**
（沙箱因缺 `.git` / `workspace` 固有 3 条失败，delta 法自动抵消）。跑完 `rm -rf` 沙箱，
主工作区 `git status` 与 `planning.py` 的 sha256 已核实未变。

| 沙箱运行 | 命令 | 结果 |
|---|---|---|
| 基线（5 键在） | `pytest -q -m "not e2e and not browser"` | **3 failed / 2803 passed / 26 skipped**（3 条为沙箱固有） |
| 摘掉那一行 | 同上 | **8 failed / 2798 passed / 26 skipped** |

### ⇒ **新红 5 条**（8 − 3），全部在 `tests/test_sprint8_s811_w6_criteria_guard.py`：

```
FAILED ::test_g8_paper_reported_baseline_reaches_check_plan
       - `_digest_paper_analysis` 的返回里没有 `baseline_results` 键…
FAILED ::test_g9_vague_criteria_warns_when_only_baseline_present
       - 指标与数据集皆空、只有论文自报基线时，空话达标线未被警示 ⇒ …
FAILED ::test_production_digest_key_set_is_exact
       - 生产摘要键集合变了：多出 set()，缺少 {'baseline_results'}
FAILED ::test_red_state_digest_without_baseline_results
       - 突变器在 `_digest_paper_analysis` 里找到 0 个 `baseline_results` 键…
FAILED ::test_mutation_harness_is_side_effect_free
       - 真身 `_digest_paper_analysis` 被突变污染了（返回里少了第 5 键）
```

**改前同一动作 = 0 红**（主控 2026-08-09 手工实测：133 passed）。⇒ 判据达成。

---

## 三、🔴 实测顺带推翻了架构 §15.5 G8 对**自己验红条件**的表述

G8 那一行同时写了两句：**期望 = 「不报 W6」** ＋ **验红 = 「去掉该键 → 本条必红」**。
**这两句不能同时成立。** 两态并列实测（`_paper_fact_terms` + `check_plan` 直跑）：

```
真身(5 键)   候选集=['BM25_R2']   G8 达标线 → []      G9 空话 → ['W6']
摘键后(4 键) 候选集=[]            G8 达标线 → []  ←同  G9 空话 → []    ←唯一翻面
```

原因：键没了 ⇒ 候选集**空** ⇒ `check_plan` 在读达标线之前就早退（G3「宁窄勿宽」）⇒ **照样不报 W6**。

⇒ **若谁按 G8 字面只写一句 `assert "W6" not in rules`，那条用例是恒绿的、没有牙** —— 和本轮要治的
病一模一样，只是换了一层。真正扛住这条验红的是两样：

- **行为面**：`test_g9_...`（护栏从「报」变成「静音」，这是唯一可观测的行为翻面）；
- **结构面**：`test_g8_...` 的白盒断言（键在 / 候选集里有它）+ `test_production_digest_key_set_is_exact`。

我已按此实现，并把这段实测写进 `test_red_state_digest_without_baseline_results` 的 docstring。
**架构 §15.5 G8 那一行的验红条件需要订正**（我不碰 `architecture.md`，交主控转架构师）。

---

## 四、顺带修：`test_sprint7_s710_exec_locality.py` 的失准计数

- 位置：`tests/test_sprint7_s710_exec_locality.py:817`（主控简报写 822，现查为 817）
- 原文：`"paper_analysis 必须带默认值 None，否则既有 36 处两参调用当场全红"`
- 现查（AST，2026-08-09 22:4x）：`check_plan(` 字面两参 **38** / 三参 **7** / `*args` 解包 **5**
- 处置：**不再写死处数**。原「36」取自 `P-S8-23`（当时 = 生产 1 + 测试 36），本文件补 133 条后失准。
  且两个口径不能混写：**静态口径会低估参数化用例**（新文件里 `check_plan(*case.args)` 运行期展开
  成几十次两参调用，AST 数不出），运行期口径要靠 pytest 拦截观测（`P-S8-23` 落档 82 次 / 37 调用点）。
  改为定性表述 + 三行注释交代口径与出处。
- 同源跟改（全文 grep `36 处` 收网，`MEMORY` §3.10）：`test_sprint8_s811_w6_criteria_guard.py` 内
  另 3 处同样表述已一并处理 —— 头注那处**保留数字但注明口径**（=「本文件加入之前」的 `tests/`
  静态清点、取值时刻 2026-08-09，`MEMORY` D-1），另 2 处改为定性。
- grep 复核（22:5x 现查，全仓 `*.py` + `*.md`，排除 `.venv`）：`36 处` 剩余命中 **7 条**，逐条已判定
  **不需改**：`docs/sprint8/dev-plan.md` 的 `P-S8-23` 原始登记（历史）｜ 上一轮报告
  `2026-08-09_w6-criteria-guard.md:22/48/190`（历史证据，规范要求旧报告不覆盖；其中 `:22`
  本就注明了「开工时 21:15」的取值时刻）｜ `docs/TODO.md:917` 与 `docs/sprint7/dev-plan.md:4251/4256`
  的「36 处 fixture」（**同数字不同主语**，讲的是 `metrics` fixture，与 `check_plan` 无关）。
  ⇒ 点名清单原本只有 1 处（主控点的 `exec_locality`），grep 收网实得 **4 处需改**，两数不等 ——
  又一次印证 `MEMORY` §3.10。

---

## 五、结果摘要

| 口径 | 命令 | 结果 | 时间点 |
|---|---|---|---|
| 全量默认域（真实工作区） | `.venv/bin/pytest -q -m "not e2e and not browser"` | **2807 passed / 0 failed / 25 skipped / 58 deselected / 7 xfailed**，66.26s | 2026-08-09 22:5x PDT |
| 本文件单跑 | `pytest -q tests/test_sprint8_s811_w6_criteria_guard.py` | **136 passed**，3.18s | 同上 |
| 独立可跑性抽查 | 三条用例单点直跑（含新增两条） | 3 passed | 同上 |

- 基线对照：主控给的基线 **2804 passed**（含上一轮 133 条）→ 本轮 **2807**，**+3** = 新增 G9 /
  精确键集合 / 常驻验红三条（另有 2 条为原用例改名与拆分，不计净增）。
- 是否包含 e2e：**否**。全程零真跑、零外部调用、零配额消耗。`-m "not e2e and not browser"` 排除
  58 条；沙箱副本刻意**不含 `.env`**，凭证不可达。
- 警告：3 条，全部为既有第三方 DeprecationWarning（langgraph `allowed_objects` /
  pydantic `schema()` 等），非本轮引入。
- mypy：本轮只改 `tests/`，`mypy.ini` 的 `files = core` **不覆盖测试目录**，不影响交付门。

## 六、失败排查

真实工作区全量运行**无失败**。沙箱基线的 3 条固有失败均为环境缺失（`.git` / `workspace` 未复制），
非生产代码问题，且在 delta 法下抵消：

| 用例 | 失败类型 | 结论 |
|---|---|---|
| `test_sprint3_a1.py::test_cp_a1_4_intro_commit_config_is_pure_append` | 环境（沙箱无 `.git`） | 不入账 |
| `test_sprint4_a3.py::test_cp_a3_2_secrets_covered_by_gitignore` | 环境（沙箱无 `workspace/`） | 不入账 |
| `test_sprint5_t22_coding_gate.py::test_cp_2_2_5_graph_py_zero_change` | 环境（沙箱无 `.git`） | 不入账 |

排查过程中真实修掉的一条**本轮自造** bug：`_build_digest_mutant` 初版用
`from core.nodes import planning` 取模块，运行期 `NameError: _coerce_str`。定位后确认是
`core/nodes/__init__.py` 把 7 个节点函数按**与子模块同名**的方式重导出（`__all__` 里就是
`planning` 等 7 个名字）⇒ 该写法拿到的是**节点函数**而非模块。已改走 `importlib.import_module`
并把成因写进 docstring。详见「后续动作」第 3 条。

## 七、后续动作

1. **交架构师**：`docs/sprint8/architecture.md` §15.5 **G8** 的验红条件表述需订正（见本报告第三节）。
   建议改法：G8 保留「不报 W6」为期望，**验红条件挪到 G9**（或在 G8 上补一句「本条须带白盒断言：
   键在摘要里 + 候选集里取得到，否则验红条件不成立」）。
2. **过渡态没人塌**这件事本身建议进 `docs/MEMORY.md`（见返回报告的建议条目），比这次修复本身通用。
3. **已发现未动**：`from core.nodes import planning` 静默给出**节点函数**而非模块，是个全仓
   footgun（7 个节点全中）。本轮只在测试侧绕开，**未碰 `core/`**。是否值得处理交主控判断。
4. 下次跑测试的触发条件：批次 2 落地 `T-S8-2-8b`（execution 侧注入同一份 `baseline_results`，
   架构 §15.6 路 β）后，回归本文件 + `payload_probe` 的 11 键守门。
