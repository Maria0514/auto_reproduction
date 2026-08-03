# 测试执行报告 - s713-independent-acceptance

- **日期**：2026-08-03 00:01（PDT，本地时区；工作从 2026-08-02 23:45 起）
- **执行人**：@测试工程师代理
- **Sprint**：sprint7（批次 9 / S7-13 / T-S7-9-1）
- **触发原因**：**该批在 `tests/` 下零覆盖** —— 开发只做了 `/tmp` 自测脚本（仓库零触碰），改前改后全量回归逐格相同（2494 passed）恰恰是零防线的证据（S7-12 同款情形）。本次为独立补正式测试 + 独立重做命门验红 + 独立验收。
- **commit**：`1e2577d`（生产代码零改动，交付时工作区 `core/` 干净）

---

## 执行范围

- 新增用例文件：`tests/test_sprint7_s713_reported_metrics.py`（**141 条**，全离线，零 API 配额 / 零网络 / 零真跑）
- 新增离线夹具：`tests/fixtures/s713_realrun_20260802/`（5 个文件，抄自 2026-08-02 23:30 那次真跑的现场，见下"夹具来源"）
- 命令：
  - `.venv/bin/python -m pytest tests/test_sprint7_s713_reported_metrics.py -q -p no:randomly`
  - `.venv/bin/python -m pytest -q -m "not e2e and not browser" -p no:randomly`（固定序）
  - `.venv/bin/python -m pytest -q -m "not e2e and not browser"`（随机序）
  - 命门验红驱动：`/tmp/s713_red_drill.py`（13 处突变，`cp` + `sha256sum` 还原，**全程零 `git checkout` / `restore` / `stash`**）
- **是否包含 e2e：否**。本次未跑 `-m e2e`、未跑真跑、未 commit / push。

### 夹具来源（现场会被下次真跑覆盖，已抄走）

| 夹具文件 | 抄自 | 内容 |
|---|---|---|
| `agent_reported_metrics.json` | `/data/myproj/.umap_evidence/20260802-233011/realrun.db` 内 AIMessage 的 `<result>` 原文 | agent 自报的 **24 条** metrics（逐字节原样） |
| `expected_results.json` | 同目录 `reproduction_plan.json` | 该轮计划的 5 条 `expected_results` |
| `disk_scan_groups.json` | 现场 `workspace/1802.03426/code` 上跑**生产** `_collect_grouped_metrics` 的实测产出 | `data/COIL-20` / `data/MNIST` / `data/PenDigits` / `report` 四组 |
| `knn_results.csv` | 现场 `outputs/eval/knn_results.csv` | 12 行 = 3 数据集 × 4 方法（用于坐实"去重保留的是哪一组"） |
| `metrics_blocks.txt` | 现场 `exec_logs/round_0.log` 的 7 个 `<METRICS>` 块 | 用于钉死档 1"只取最后一块"的残留行为 |

---

## 结果摘要

| 项 | 值 |
|---|---|
| 新增用例单跑 | **141 passed**（0.86s） |
| 全量回归（固定序 `-p no:randomly`） | **2635 passed / 25 skipped / 58 deselected / 7 xfailed / 0 failed**（62.75s） |
| 全量回归（随机序） | **2635 passed / 25 skipped / 58 deselected / 7 xfailed / 0 failed**（62.78s） |
| 命门验红 | **13 处突变，13 处全部见红，零漏网** |
| 警告 | 3 条（均为既有第三方/既有用例遗留，本次零新增） |

### 账目对平（逐格）

```
改前基线（本次落盘前主控/本人各独立复跑一次）：2494 passed / 25 skipped / 58 deselected / 7 xfailed
本次新增：                                    +141 passed（新文件，零既有用例改动）
改后实测：                                     2635 passed / 25 skipped / 58 deselected / 7 xfailed
2494 + 141 = 2635 ✅ 无余数
收集总数自证：2635 + 25 + 7 + 58 = 2725 = `--collect-only` 实测总数 ✅
```

**⚠ 顺带结清 dev-plan §63 P-66 登记的"未知"**：该条记「deselected 由 58 变 46 的成因本批未追查、如实登记为未知」。本次实测结清——**与用例增减无关，只取决于 `-m` 表达式**：`-m "e2e"` 收 **46** 条、`-m "browser"` 收 **12** 条、`-m "e2e and browser"` 收 **0** 条（两族不相交）⇒ `-m "not e2e and not browser"` 必然 deselect **58**，而 `pytest -q`（`pytest.ini` 默认 `-m "not e2e"`）必然 deselect **46**。两个数字一直同时成立，不存在"变"。

### 用例分区（141 条）

| 区 | 条数 | 守什么 |
|---|---|---|
| A ★★ 主指标门控 | 9 | 主通道零指标时不采信自报；`success` 的指标分子来源不被换掉；合并方向；不采信须留痕 |
| B `metrics_groups` 三方关系 | 11 | agent 优先 / 磁盘兜底 / **禁止合并**；零汇报时节点输出与本批之前逐字节相同 |
| C 拆分纯函数 | 68 | `group` 归属、先到先得、脱敏、120 字符边界、**畸形输入恒不抛**（该函数在节点主流程上） |
| D schema 与装配 | 20 | `result_schema` 真传了（断行为不断源码）；`required` 不含 `metrics`；无 `source`；原样透传 |
| E `expected_results` 注入 | 7 | 注入逐字可见 + 到达 HumanMessage；**无该键时 payload 字节零扰动** |
| F 零改动红线 | 16 | 十函数源码字节门 + 档 1"取最后一块"行为 + 完成度真值表 + success 三合取项 |
| G 真跑重放（离线） | 10 | 24 条自报 → 4 组、回验五态复现、**两条与交付表述相左的事实** |

---

## 命门验红实据（13 处，逐条实做）

方法：`cp core/nodes/execution.py /tmp/s713_exec_backup.py` + 记录基线
`sha256 = d9176cdf8b0baed41c46c44cde3ce0a084d5094d1c12bdfee67bb285f174ed20`；每处突变
→ 跑靶向用例 → 记录 → `cp` 还原 → **重算 sha256 与基线逐字节校验** → 下一处。
13 轮结束后终值仍为该 sha256（脚本末尾自证）。**全程未使用 `git checkout` / `git restore` / `git stash`**（§56.3 P-53 事故纪律）。

| # | 突变 | 变红条数 | 首条报错原文 |
|---|---|---|---|
| **M1 ★★** | 去掉主指标门控（`if metrics and reported_main:` → `if reported_main:`） | **5** | `AssertionError: 主通道零指标时 agent 自报的主实验指标一个都不得进 metrics` |
| M2 | 合并方向反转（自报覆盖解析值） | 2 | `AssertionError: 同名键必须是真实 stdout 解析值胜出；反转合并方向这条当场红` |
| M3 | `metrics_groups` 改成合并两来源 | 2 | `AssertionError: assert {'PCA', 'UMAP...legacy_group'} == {'PCA', 'UMAP'}` |
| M4 | `metrics_groups` 退回只用磁盘扫描 | 3 | `AssertionError: assert {} == {'UMAP': {'k-...uracy': 0.62}}` |
| M5 | 去掉 `result_schema=` 实参 | 1 | `AssertionError: assert None is {'additionalProperties': True, …}` |
| M6 | 重复条目改"后覆盖前" | **5** | `AssertionError: 先到先得` |
| M7 | 畸形跳过改静默 `pass` | 1 | `assert 0 == 1` |
| M8 | 删掉 `expected_results` 注入 | 2 | `KeyError: 'expected_results'` |
| M9 | 把 `metrics` 列进 schema `required` | 1 | `AssertionError: assert 'metrics' not in ((['steps_attempted', 'all_exit_zero', 'summary', 'metrics']))` |
| M10 | schema 加回被砍掉的 `source` | 2 | `AssertionError: assert 'source' not in ['steps_attempted', …, 'name', ...]` |
| M11 | 冻结函数 `_extract_metrics_block` 内插一个空格 | 1 | `AssertionError: _extract_metrics_block 源码已变更（当前：cdf307bd75581207，基线：438257b7f5ef4283）` |
| M12 | 标量收编上限 `120 → 121` | 16 | `assert 121 == 120` |
| M13 | 透传处顺手清洗一遍（制造第二个清洗点） | 1 | `AssertionError: assert [{'name': 'ac...'value': 0.9}] == ['垃圾', {'name...'value': 0.9}]` |

**⇒ 13/13 见红，无一处"改坏后仍全绿"**（P-72 那类无牙断言本次零出现；C3 / A3 两条夹具从一开始就按"末值 ≠ 首值""两侧异值"设计，正是为了避开开发自查栽过的那两个坑）。

### ★★ M1 的活体证明（独立重做，未复用开发结论）

同一份前置条件（exit 全 0 + 计划步骤全部跑完 + 归属诚实 + 主通道零指标 + agent 自报 1 个主实验指标），只把门控那一行改掉：

```
门控在位（现行生产代码）： metrics = {}                       success = False
去掉门控（突变）：         metrics = {'best_knn_accuracy': 0.98}  success = True   ← 翻绿
```

⇒ 门控是**唯一**拦住"`success` 的指标分子被换成 agent 自报"的东西。开发结论属实，本次独立复现无出入。

---

## 失败排查

**本次无失败用例。** 唯一一次红是我自己测试代码的 helper bug（`list(0)` → `TypeError`，参数化里传了非可迭代值），当场改为 `_ABSENT` 哨兵后复绿；生产代码零涉及。

---

## ★ 独立发现（6 条，开发与主控均未判到）

> 前两批分别挖出 6~8 条，本批同样保持独立复核而非采信派单结论。以下每条都有可执行断言或实测数据支撑。

### F1（★★ 最重要，候选 **BUG-S7-13-01**）"先到先得"去重让 **2/3 条机器判定由数组顺序决定**

- **事实**：2026-08-02 那轮 agent 报了 **24 条**（4 方法 × 3 数据集），`_split_reported_metrics` 按"先到先得"坍缩成 **8 个值**，保留的全是**数组里排第一的那个数据集（COIL-20）**。`knn_results.csv` 逐行核对无误（`test_g5`）。
- **后果**：把 tie-break 换成同样任意的"保留末次"（取到 PenDigits），5 条回验里 **第 4、5 两条判定双双翻转**（`test_g6` 实测）：

  ```
  保留首次（现行）→ ['未验证','符合','未验证','符合','不符']
  保留末次        → ['未验证','符合','未验证','不符','符合']
  ```

- **因此**：真跑报告 `2026-08-02_s713-real-run.md` §1 把第 5 条那个 **❌不符** 称作「本轮最有价值的产出 / 系统第一次敢判‘论文这条我没复现出来’」，并逐条复算称「判定正确 ✅」——**这个结论站不住**。它不是复现结论，是 tie-break 的产物；换个同样没有科学含义的方向就变成 ✅符合。
- **雪上加霜**：被选中的 COIL-20 恰是 agent 自己在 `<result>.notes` 里标注过「日志实际下载的是 Olivetti faces 数据」的那份；而用户可见报告的分组指标表**没有任何数据集标注**，读者无从知道这 8 个数字来自哪一个数据集、更不知道另外 16 个被丢了。
- **定性**：这是**缺陷一（"取最后一块"取到收尾脚本元数据）的同型病**——S7-13 把"最后一个赢"换成了"第一个赢"，坍缩本身没被治，只是从 stdout 通道搬到了自报通道。dev-plan §60.4 曾判定"新方案下**根本不发生撞名**"，实测**发生了，16 处**。
- **处置**：**未自行修改生产代码**（属设计决策，且 `_split_reported_metrics` 的"先到先得"是 dev-plan 明写的规格）。已用 `test_g5` / `test_g6` 把现状与该敏感性钉死（characterization），交主控 / 架构师 / PM 决策。**交付表述必须相应改口**。

### F2 主指标 `best_knn_accuracy` 回来了，**不是本批的功劳**

- **事实**：该轮 agent 自报的 24 条**全部带 `group`**，`_split_reported_metrics` 的 `reported_main` 实测为 **`{}`**（`test_g4`）⇒ 步骤 4.4 的主指标合并分支**这一轮根本没执行**。
- **同时**：真跑日志最后一个 `<METRICS>` 块本身就是 `{"best_dataset","best_method","best_knn_accuracy","num_results"}`，`_extract_metrics_block`（本批一字未改）直接取到它（`test_f2` / `test_g4`）。
- **⇒ 主指标变好，归因于 coding 侧最后打印的那一块变对了**，与 S7-13 无关。真跑报告 §1 那张对比表把「`mean_timing_seconds=44.81` → `best_knn_accuracy=0.9766`」记在本批名下，属**归因错误**。P-70 登记的"档 1 选块缺陷仍在"依然完整成立，且这一轮它是靠运气过的。

### F3 "禁止合并"的硬证据是**靶相关**的，不是普遍事实

- dev-plan §60.6-订正 裁决 1 称合并"会把回验打坏"，证据取自 2026-08-01 那轮的磁盘组名（`umap` 与 agent 的 `UMAP` 归一撞名）。
- **实测**：2026-08-02 那轮磁盘组名换成了 `data/COIL-20` / `data/MNIST` / `data/PenDigits` / `report`，与 agent 组名一个都不撞 ⇒ **那一轮即便合并，5 条回验产出完全不变**（`test_b6`）。
- **结论不变**（不合并仍是对的：零成本消除一整类撞名风险），但**理由要说准**。把靶相关的观测写成普遍结论，正是 §63 P-61 / P-62 已经栽过两次的同一个跟头。机制本身仍有牙，由 `test_b5` 用 2026-08-01 那轮的组名形态独立守住。

### F4 用户可见文案已与事实不符（`core/nodes/reporting.py:995`）

- 报告里"分组实验指标"一节的导语写死为「按执行产物解析出的实验分组逐组展示（**组名为产物目录相对路径**）」。
- 本批之后组名来自 **agent 按计划写法的汇报**，2026-08-02 的 `report.md` 里那句话正下方紧跟着 `UMAP` / `t-SNE` / `PCA` / `laplacian_eigenmaps` —— **一个目录路径都不是**。
- 同类陈旧描述另有 `core/state.py:170`（"`metrics_groups`：…（execution `_collect_grouped_metrics` 写）"）与 `reporting.py:955`。
- **定性**：`reporting.py` 是本批声明的"零改动"文件，所以没人回头看它的**文案**是否还成立。这条踩到 MEMORY §4.2（用户可见文本纪律）。**未自行修改**（跨出本批文件边界），登记交办。

### F5 dev-plan CP-9.1-7「A 半：**3 条** trend 缺失恒未验证」的数字已随计划变化

- 该期望值取自 2026-08-01 那份计划（3 条 `trend: null`）。**2026-08-02 这份计划只有 2 条**（第 5 条新长出了 `{"metric":"k-NN classifier accuracy","greater":"UMAP","lesser":"t-SNE"}`）。
- 把"3 条"写成写死断言会随计划漂移而假红。本次的守门写成**性质断言**：先从夹具里找出所有 `trend` 非 dict 的下标，再断言它们在**任意** `metrics_groups`（空 / 真跑值 / 任意合成值）下**恒为"未验证"**（`test_g3`）⇒ 数字变了也不假红，性质变了立刻红。

### F6 `group` 为非字符串（`123` / `["UMAP"]` / `{...}`）时会被**归进主实验桶**

- 实现只认 `isinstance(raw_group, str)`，其余一律落空组 ⇒ 进 `reported_main` ⇒ 成为"主通道非空时会被合并进 `metrics`"的候选。
- 不算 bug（模型按 schema 填 `string|null` 的概率很高），但它意味着**一个畸形的 `group` 会把该指标偷偷提升为主实验指标**。已用 `test_c11` 钉死现状，使日后任何改动成为显式决策。

---

## 遗留风险（如实登记，本次不治）

| # | 风险 | 说明 |
|---|---|---|
| 1 | **R-S7-68（agent 服从度）本次仍未被证伪也未被证实** | 本文件全部离线。G 区证明的是"**机制**通了"（`t-SNE` 这种写法过得了匹配），**不是**"agent 一定会照做"。2026-08-02 那轮 4 组里 3 组按计划写法、1 组（`laplacian_eigenmaps`）按产物目录名 —— 属合规（计划未提及该方法），但**服从不是 100%，不得表述为"完全解决"**。 |
| 2 | **P-70 档 1"取最后一块"仍在** | 已由 `test_f2` 把该残留行为钉死。2026-08-02 那轮它取对了是运气（收尾脚本恰好最后打汇总块）。 |
| 3 | **P-71 / R-S7-73（`NONE + success=False` 空洞）** | 本次未新增覆盖（`_apply_no_metrics` 属零改动红线）；`test_f5` 只钉死了 success 三合取项不变。 |
| 4 | **F1 的坍缩问题无回归门可挡** | `test_g5` / `test_g6` 是 characterization：它们锁住"现状是这样、且对 tie-break 敏感"，**不会**在下一次真跑坍缩出错误结论时报警。真正的出口是数据结构（同名多值不该坍缩成一个数），属评估 B 路线，须走 PRD。 |

---

## 后续动作

- **交主控 / 架构师 / PM**：F1（候选 BUG-S7-13-01，含"对外表述必须改口"）、F4（`reporting.py:995` 用户可见文案失真）。二者都跨出本批文件边界，测试侧未动生产代码。
- **交主控**：本报告的账目（2494 → 2635）与 P-66 结清结论可直接进 `docs/TODO.md`（共享文件由主控统一收口，本次**未碰** TODO）。
- **下次跑测试的触发条件**：①F1 / F4 处置方案定下来后回归本文件；②下一次端到端真跑后，用新证据复跑 G 区（夹具目录按 `s713_realrun_<日期>` 追加，不覆盖旧的——旧夹具是历史证据）。
