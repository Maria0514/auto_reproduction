# 测试执行报告 - s710-independent-acceptance（S7-10 批次 6 独立验收）

- **日期**：2026-07-31 04:15（PDT）
- **执行人**：@测试工程师代理
- **Sprint**：sprint7（S7-10 / 批次 6 / T-S7-6-1~8）
- **触发原因**：开发交付 S7-10 批次 6 并停在真跑前，主控要求**独立把关、证伪开发自述**（"四道守门全验红、47 条新测试全绿"）。本次不复用开发结论，凡其声称的逐条自跑。
- **commit**：`03b9337`（工作区有 S7-10 未提交改动：`core/nodes/planning.py`、`core/nodes/execution.py`、`core/plan_checks.py`、3 个既有测试文件、1 个新测试文件）
- **未做**：**任何 e2e / 真跑**（耗 deepxiv 配额，须 Maria 单独授权）。本报告只含静态审查 + 离线测试 + 受控变异（mutation）验红。

## 执行范围

| 命令 | 用途 |
|---|---|
| `.venv/bin/pytest -q -m "not e2e and not browser" -p no:randomly` | 全量回归（跑两次：交付原样 / 加补测后） |
| `.venv/bin/pytest -q -m browser -p no:randomly` | UI 维（跑两次，见"失败排查"） |
| `.venv/bin/mypy` | 类型检查（scope = `core/`，`mypy.ini:43 files = core`） |
| 变异验红：手工改坏 → 跑 → `cp` 还原 → sha256 比对 | 四道命门 + 我方补测的自验红 |
| 独立取证脚本（一次性、未入仓） | 哈希实算 / 语料重标定 / 谓词绕过探针 / 新旧 `plan_checks` 对照 |

覆盖用例：`tests/test_sprint7_s710_exec_locality.py`（交付 47 条）、`tests/test_sprint5_t14_execution_prompt.py`、`tests/test_sprint6_b1_prompt_guards.py`、`tests/test_s708_user_text_guard.py`、`tests/test_sprint6_b1_plan_checks.py` + 本次新增 `tests/test_sprint7_s710_gap_audit.py`。

## 结果摘要

| 口径 | 交付原样 | 加本次补测后 |
|---|---|---|
| `-m "not e2e and not browser"` | **2262 passed / 0 failed / 25 skipped / 58 deselected**（70.02s） | **2269 passed / 0 failed / 25 skipped / 5 xfailed**（63.78s） |
| `-m browser` | 首跑 **11 passed / 1 failed**；复跑 **12 passed** | 同（本批未触碰 `ui/`） |
| `mypy` | **Success: no issues found in 27 source files** | 同（补测在 `tests/`，不在 mypy scope） |

**数字对账**：2262（主控实测基线）+ 7（本次新增通过用例）= **2269**，精确对平；另 5 条 `xfailed` 是本次为 **BUG-S7-10-01** 立的 strict-xfail 追踪位（修好会 XPASS 并当场红）。
**警告**：3 条，均为既有第三方 Deprecation（langgraph `allowed_objects` / pydantic `.schema()`），非本批引入。

---

## 一、AC-S7-44 ~ AC-S7-55 逐条判定（含我方实据）

| AC | 判定 | 我方实据（自跑，非引用开发结论） |
|---|---|---|
| **AC-S7-44** 计划侧约束 A | **通过** | 直接 import 常量实测：`` `cd <子目录>` ``、`cd <子目录>`、`仅限工作区内`、`cd ` **四个负向子串全 False**；正向 `相对代码目录`/`pip install -e`/`不要进入仓库目录`/`系统已把执行的工作目录设为`/`不要写绝对路径` **全 True**。跨论文：`_build_planning_system_prompt({"arxiv_id":"1802.03426"}) == ...("2405.14831") == _PLANNING_SYSTEM_PROMPT_BODY` → True。新增段零 `{}`／零 `arxiv`／零绝对路径 |
| **AC-S7-45** 约束 B | **通过** | 主体含 `不得生成"先写一个占位文件、再运行该占位文件"这类步骤`、`步骤形态本身`、`无论写进去的是占位符` 三条全 True ⇒ 措辞确实针对**步骤形态**而非占位内容（P-20 的要害） |
| **AC-S7-46** 执行侧收窄 | **通过（一处措辞偏差，已在 P-32 留档）** | 负向 `修正相对路径` → **False**（已删）；正向 `不得写入或修改任何代码文件`、`本工具不用于写代码`、`cd（限工作区内）`（未顺手删）、`补装缺失包`（保留项）全 True。⚠ PRD 原文要求含"交回**编码环节**"，实现写的是"交回**代码生成环节**修复"——语义一致、dev-plan §48 P-32 已登记，**不算未通过，但 PRD 与代码字面不一致的账仍挂着** |
| **AC-S7-47 ★命门** | **部分通过 —— ①②③④ 四条各自成立，但硬防线本身可被一个 flag 绕过（BUG-S7-10-01）** | ①喂 `round_0.log:121` 原命令（真起子进程）→ `tool_error=True`/`exit_code=-1`/`run_repro_basics.py` 未创建；②合法探针不误伤（我方补足到 AC 原文要求的 5 条 + 1 条脚本运行，且断言**真进了 runner**）；③被拒后 `run_results==[]`、`step_ledger==[]`；④复合命令整条被拒、`pip install` 一次没跑。**但**：`python -u -c "<同一条原始罪证载荷>"` **完全不被拦** —— 见下方 BUG-S7-10-01，我方真起子进程复现：文件**真落盘**、内容逐字为占位符、**且进 step_ledger 1 条** |
| **AC-S7-48** W4/W5 正负两向 | **通过（一处覆盖缺口）** | W4 正向（切进 selected_repo）/ 负向（纯相对路径 + `pip install -e <路径参数>` + `cd outputs`）/ 异常面（`{}`、`selected_repo=None`、非 dict、`local_path=None`）实测符合；W5 正负两向符合；`check_plan` 签名仍 `["plan","resource_info"]`、返回键仍 `{"rule","message"}`、模块内 `interrupt(` 零命中。⚠ 缺口：`resource_info` 缺失时的兜底只认 `"/repos/" in target`，**裸相对形态 `cd repos/lmcinnes__umap` 判不出 W4**（`cd ../repos/...`、`cd /a/repos/...`、`cd ../../repos/...` 均能判出）。软防线，低危 |
| **AC-S7-49** 文案零术语 + 账目精确闭合 | **通过（已验红）** | `EXPECTED_TERM_LABELS_N=42 / EXPECTED_CONSTANTS_N=12 / EXPECTED_N=54`，`==` 形态未被放宽为 `>=`（`git diff` 逐行核）。**阳性对照**：`_all_hits("策略 from_scratch / use_repo，写入 code_output_dir")` → `['from_scratch','use_repo']`（守门函数真的会命中），而 `_all_hits(_W4_MESSAGE) == _all_hits(_W5_MESSAGE) == []` ⇒ 零术语是真结论不是空扫 |
| **AC-S7-50** 仓库不接收复现代码与产物 | **通过** | `workspace/repos/lmcinnes__umap` 实测 `git status --porcelain` **输出为空**（3 条残留已清理，`test_cp_6_7_2_*` 现为真绿而非 skip——该目录 `.git` 存在故未走 skip 分支）。helper 正负两向自跑：白名单放行 `*.egg-info`/`__pycache__`/`build/`/`.eggs`/`.pyc`；拦下 3 条真实残留 + `build/repro_outputs/metrics/summary.json`（藏进白名单目录也拦） |
| **AC-S7-51 ★命门** | **通过（两道门均由我方独立验红）** | **值的真实性我方独立复算**：从 `git show HEAD:core/nodes/execution.py` 用 AST 取出改前常量 → **1560 字符 / `0dbe4143dc836e91`**，与 §48.1 记的"改前基线"逐字相符；planning 改前 **5424 / `a7cad88cdb205c5f`** 同样相符 ⇒ **"先建后改"不是事后补叙，改前值确实是那个值**。当前锁的是**改后**值：execution `1698 / f82f3938cf31f882`、planning `5900 / ef6d267030fd2a0c`，与测试写死值一致。**验红实做**（见第二节）。两处均为写死字面量，非 `EXPECTED_HASH = actual_hash` 自锁定形态 |
| **AC-S7-52** 连坐交付 | **通过（但其守门用例本身偏弱，见第三节 F2）** | A/B/C 三条我方逐条独立核实均已落地、无"某条延后"的组合。交付件那条机制化守门的 C 臂是**源码子串检查**，我把工具层判定改成 `if False and ...`（死代码）后它**仍绿** |
| **AC-S7-53** 边界不放宽、无扰动 | **通过（我方用更硬的方式复核了②）** | ①11 个零改动红线文件 `git diff` **逐一为空**（含 `coding.py`/`code_fs_tools.py`/`git_tools.py`/`sandbox/local_venv.py`/`ui/`）；②交付件是"与硬编码期望比"，我方改为**新旧实现对照**：把 `git show HEAD:core/plan_checks.py` 载成模块，对 from_scratch 计划 / use_repo 干净计划 / 空计划三组输入跑新旧两版 `check_plan`，警示序列**逐一相同**（`['W3']` / `[]` / `[]`）⇒ 无扰动是真结论；③`REPRODUCTION_PLAN_SCHEMA` required 三键不变、无 `code_output_dir` 字段、`_format_planning_context` 仍 6 形参 |
| **AC-S7-54** 真跑 A/B 真值 | **未验证（延后，非不通过）** | 须 Maria 单独授权真跑。**当前一切结论只覆盖到"机制在位"，覆盖不到"模型产的计划真的不再切目录"** |
| **AC-S7-55** 真跑主断言"孤儿消失" | **未验证（延后）** | 同上。⚠ 另见第四节：本批测试**没有踩** PRD §12.7 的三个陷阱 |

**AC 小结**：可离线判定的 10 条中，**9 条通过、1 条（AC-S7-47）部分通过**；2 条真跑项延后。

---

## 二、四道命门的独立验红（我方亲手改坏 → 跑 → 还原 → sha256 比对）

| 命门 | 我方改坏方式 | 实际表现 | 还原 |
|---|---|---|---|
| ① **约束 C 工具层硬拦截** | 整段删除 `if is_inline_code_write(command): ... return _tool_error_json(...)`（425 字符） | **5 failed / 42 passed**：`test_cp_6_6_1_*`（★ 原始罪证）/ `test_cp_6_6_3_*`（台账）/ `test_cp_6_6_4_*`（复合命令）/ `test_cp_6_6_6_rejection_logs_*`（日志）/ `test_ac_s7_52_*` | 47 passed，文件 sha256 与备份**逐字节相同** |
| ② **execution 字节门** | 主体末尾插一个空格 | **1 failed / 12 passed**（同文件其余 12 条仍绿 ⇒ 独立生效）；新文件里 `test_cp_6_6_7_*` 亦红 | 13 passed |
| ③ **planning 字节门** | 主体内插一个空格 | **1 failed / 18 passed** | 19 passed |
| ④ **术语守门计数闭合** | 从 `_GUARDED_CONSTANTS` 删掉 `_W5_MESSAGE` 一条 | **3 failed / 4 passed**（比开发自述的"两道断言"还多一道：常量缺失路径也红） | 7 passed |

**特别核实（主控点名）**：execution 字节门"锁的是改后的值且能拦住任何后续改动"——
- 锁的确是**改后**值：`f82f3938cf31f882` = 当前主体（1698 字符）实算值；
- "先建后改"的活体证明**可独立复原**：我从 git HEAD 取改前常量实算得 `0dbe4143dc836e91`/1560，与 §48.1 首行逐字相符 ⇒ 开发当初写进门里的确实是改前值，不是事后回填；
- 拦得住后续改动：插一个空格即红（上表 ②）。

**额外一记（本次新增的变异探针）**：把工具层改成 `if False and is_inline_code_write(command)`（判定还在、但不再生效）——
交付件仍有 4 条红，**但 `test_cp_6_5_4_w5_shares_one_predicate_with_tool_layer` 与 `test_ac_s7_52_all_three_constraints_landed_together` 双双保持绿**。见下节 F2 / F3。

---

## 三、假绿与测试缺口（本次最有价值的产出）

### 🔴 F1（生产缺陷，**高**）BUG-S7-10-01：在 `-c` 之前加任意解释器 flag 即可整个绕过约束 C 的**唯一硬防线**

- **复现**：`.venv/bin/pytest tests/test_sprint7_s710_gap_audit.py -k bug_s7_10_01`（现为 strict-xfail，去掉标记即红）；另有真起子进程的一次性探针实据。
- **实测（真起子进程，非 mock）**：把 `round_0.log:121` 那条**原始罪证载荷**原样放进 `python -u -c "<载荷>"` 交给 `run_in_sandbox` →
  `tool_error = None`（**未被拒**）、`exit_code = 0`、**磁盘上 `bypass_u.py` 真被创建**、内容逐字为 `print('please implement reproduction pipeline here')`、
  **且 `step_ledger` +1 / `run_results` +1** ⇒ 会被 `exit_ok` 计入、会被步骤对账当成"完成"——**正是 R-S7-49 那类假绿**。
- **同族形态**（全部实测未拦）：`python -u -c` / `python -B -c` / `python -X utf8 -c` / `python -W ignore -c` / `python3 -uc` / `env python -c`。
- **根因**：`core/plan_checks.py::is_inline_code_write` 要求 `argv[1] == "-c"`，即载荷必须**恰在下标 1**；任何前置 flag 都让判定整个短路。
- **⚠ 它不是 R-S7-57 那条"已登记接受的残留"**：R-S7-57 说的是**≤阈值的极短写码**（任何阈值都拦不住）。这里载荷同样超长（1300+ 字符也一样绕过），只是多了一个 flag。把它归进 R-S7-57 会掩盖问题。
- **现实触发概率不低**：`-u`（不缓冲输出）是本领域模型最常写的 flag 之一；而 PRD §12.5.3 明确"C 是三条里唯一违反了会**静默产出错误结论**的一条"。
- **修法不违反任何红线**：仍是**单一规则**——在 argv 里**定位 `-c` 的载荷位**（如扫描第一个 `-c` 且其后有元素）而非要求它在下标 1。**不需要动词枚举、不需要后缀白名单、不需要调阈值**，故不触碰 PRD §12.3 非目标 5 / Q-S7-24。
- **处置**：**未自行修改生产代码**（纪律）。已钉成 `@pytest.mark.xfail(strict=True)` 追踪位——修好那一刻会 XPASS 并当场红，逼修的人回来删标记。**建议转 @全栈开发代理修复，且应在 T-6-9 真跑之前修**（否则真跑观测到的"拒绝触发次数"本身就不可信）。

### 🟠 F2（假绿）"三条同批落地"守门的 C 臂是源码子串检查，死代码即可满足

`test_ac_s7_52_all_three_constraints_landed_together` 的 C 臂：
```python
"C-工具层硬拦截": "is_inline_code_write(command)" in inspect.getsource(execution_module)
```
**实测**：把该行改成 `if False and is_inline_code_write(command):` 后，本用例**仍绿**。
它被 dev-plan / PRD 定位为"AC-S7-52 的机制化守门（缺任一条当场红）"，而实际它只能证明"这段字符出现在源码里"。
真正兜住 C 的是 `test_cp_6_6_1/3/4/6` 四条行为断言（它们确实红了）⇒ **风险中等：结论没错，但这条用例的证据强度被高估**。
**建议**：C 臂改为调用工具层跑一条超阈值命令、断被拒（我方补测 `test_audit_tool_layer_and_plan_layer_agree_on_every_corpus_command` 已提供现成范式）。

### 🟠 F3（断言弱于其声称的语义）"W5 与工具层共用同一条谓词"从未跨到工具层

`test_cp_6_5_4_w5_shares_one_predicate_with_tool_layer` 逐条比对的是
`plan_checks.is_inline_code_write(cmd)` 与 `check_plan(...)` 里的 W5 —— **两侧都在 `core/plan_checks.py` 内**，而 W5 的实现本身就是直接调那个函数。
**实测**：工具层改成恒不拦后，本用例**仍绿** ⇒ 它证明的是"W5 没有另写一套"，**证明不了"工具层与计划期共用"**，而后者才是 Q-S7-19「一处定义两处调用」要的东西。
**处置**：我方补 `test_audit_tool_layer_and_plan_layer_agree_on_every_corpus_command`（9 条语料逐条比对计划期判定 ⟺ 执行期是否被拒），变异下**当场红**。

### 🟠 F4（覆盖缺口 + 文档失实）Q-S7-23 定稿的"必须命中 5 条"，交付件只落了 3 条

- 架构 §19.7 定稿：必须命中 = **127 / 144 / 183 / 510 / 1304**。
- 实测交付件：`CORPUS_MUST_HIT` 只有 **3 条（127/144/183）**，字符串 `510` 与 `1304` 在 `tests/` 下**零出现**；
  而该用例 docstring 写"真实语料里 **5 条**"、dev-plan CP-6.5-1 写"127/144/183/510/1304 **均 True**"——**两处都失实**。
- 对阈值结论无影响（510/1304 都 > 127，窗口不变），但 **ground truth 缺项会让"阈值经真实语料检验"失去可核对基准**。
- **我方独立重跑了整套标定**（从归档日志 `/data/myproj/.umap_evidence/run1_20260731/code/exec_logs/` 抽取全部 `python -c` 子命令）：两轮各 7 条、**去重 9 条**，长度分布 **[36, 46, 98, 127, 144, 181, 183, 510, 1304]** ——
  与架构 §19.5 那张表**逐行相符**，可行窗口 [98,126] 与定稿值 120 **成立**。（附带订正：dev-plan 说"去重 8 条"，实测 **9 条**；架构 §19.5 表列的正是 9 行，架构侧是对的。）
- **处置**：补 `tests/test_sprint7_s710_gap_audit.py::test_q_s7_23_predicate_hits_the_two_omitted_corpus_entries`（两条载荷脚本抽取、逐字入库）+ `::test_q_s7_23_must_hit_ground_truth_is_complete`（集合**相等**守门，禁 `issubset`）。

### 🟡 F5（覆盖缺口）AC-S7-47② 要求"5 条合法探针"，实际只喂了 3 条 + 1 条脚本运行

Q-S7-21 重标后语料里的真探针只剩 3 条，正确做法是**另补 2 条短探针补足**，而不是把 AC 的数字降到 4。
且交付件只断"没被拒"，**没断它们真的进了 runner**（静默吞掉同样不会被拒）。
**处置**：补 `::test_ac_s7_47_five_legal_probes_and_a_script_run_are_not_blocked`（5 探针 + 1 脚本运行，并断 `len(calls) == len(commands)`）。

### 🟡 F6（覆盖缺口）"形态 2"从未在工具层验过

架构 §19.5 把语料 183（载入真实数据集 + 按论文超参跑完整降维 + 打印结果）重标为**形态 2 本身**——它正是"按写文件动词判会整个漏掉"的那一类。
交付件只把它喂给纯谓词，**工具层只验过 127 那条写占位符的**。
**处置**：补 `::test_audit_tool_layer_rejects_form_two_whole_pipeline_in_one_command`（并断 runner 未被调用、两个台账容器均空）。

### 🟡 F7（覆盖缺口）W4 的兜底判定漏掉裸相对路径

`_REPO_DIR_MARKER = "/repos/"` 用 `in` 匹配，`resource_info` 缺 `local_path` 时：
`cd ../repos/x` ✅、`cd /a/repos/x` ✅、`cd ../../repos/x` ✅、**`cd repos/x` ❌（不产 W4）**。
交付件那条用例的 docstring 恰恰声称"只认精确路径会在这些场合整个失效"，却没覆盖这个形态。
**软防线、低危**（W4 只产警示，且真跑里 `resource_info` 通常带 `local_path`）⇒ **只登记不补测**（避免为软告警堆用例，反最小设计）。

### ⚪ F8（代码异味，非假绿）`assert "不阻断审批" in plan_checks.__doc__ or ""`

`tests/test_sprint7_s710_exec_locality.py:708`。运算符优先级实为 `assert (X or "")`，作者原意应是 `in (plan_checks.__doc__ or "")`。
**实测不是恒真**（缺该词时 `False or ""` 仍为假 → 断言失败），故**不构成假绿**，但读起来像"兜底"实则没有兜底——`__doc__` 为 `None` 时会抛 `TypeError` 而非优雅失败。建议顺手订正。

### ✅ 复扫结论：未发现其它恒真断言 / mock 过度

- 全文件无 `pytestmark`、无 `skip`、无 `xfail`，**未被 deselect 出默认回归**；
- 无 `assert x == x` 同族形态；两道字节门均为写死字面量（我方已独立复算其真实性）；
- `test_cp_6_6_1_*` 的"文件未被创建"**配有阳性对照** `test_ac_s7_47_harness_can_really_write_files`（短写文件命令确实落盘）⇒ 不是 S7-06 那种"夹具根本没跑"的空转；
- 术语守门 `_all_hits` 有阳性对照（实测能命中 `from_scratch`/`use_repo`）⇒ "零术语"不是空扫。

---

## 四、验收口径三陷阱的合规检查（PRD §12.7）

| 陷阱 | 是否被踩 | 实据 |
|---|---|---|
| 1. 不得引"步骤对账满分"作证 | **未踩** | `grep step_reconciliation tests/test_sprint7_s710_exec_locality.py` 只命中模块 docstring 里那句**禁令本身**，**零断言引用**。反向还更好：`test_cp_6_6_3_*` 是**用台账证明被拒命令没进对账**，方向正确 |
| 2. 主断言只能是"孤儿消失"、不能是"与论文 Table 1 对上" | **未踩** | 全文件 `Table 1` / `论文 Table` **零命中**；无任何指标数值断言 |
| 3. 首轮真跑失败是预期且正确 | **未踩** | 无用例把"跑出指标"当通过条件 |

---

## 五、补充的测试

**新增文件**：`/data/myproj/auto_reproduction/tests/test_sprint7_s710_gap_audit.py`
**条数**：**12 项**（7 passed + 5 xfailed）
**纪律**：**未改动交付件任何一条断言**（`tests/test_sprint7_s710_exec_locality.py` `git diff` 为空——它是未跟踪新文件，内容与开发交付逐字节相同）；**未改动任何生产代码**（`core/` 的 `git diff` 与开发交付一致，所有变异均已 `cp` 还原并 sha256 校验）。

| 用例 | 补的缺口 | 变异下是否红（自验红） |
|---|---|---|
| `test_q_s7_23_predicate_hits_the_two_omitted_corpus_entries[510/1304]` | F4 | — |
| `test_q_s7_23_must_hit_ground_truth_is_complete` | F4（集合相等守门） | 删语料即红 |
| `test_audit_tool_layer_and_plan_layer_agree_on_every_corpus_command` | F3 | ✅ 工具层判定改死代码 → 红 |
| `test_ac_s7_47_five_legal_probes_and_a_script_run_are_not_blocked` | F5 | — |
| `test_audit_tool_layer_rejects_form_two_whole_pipeline_in_one_command` | F6 | ✅ 同上 → 红 |
| `test_audit_rejection_payload_keeps_the_existing_tool_error_shape` | 拒绝返回不得新增字段（此前无人守） | ✅ 同上 → 红 |
| `test_bug_s7_10_01_*[5 形态]` | F1（strict xfail 追踪位） | 修好即 XPASS → 当场红 |

---

## 失败排查

### 1. `tests/test_analysis_progress_e2e.py::test_e2e_state_error_shows_fatal_text[state_error]`（browser 维，首跑红）

- **失败类型**：**环境/时序抖动（flaky）**，非生产缺陷、非本批引入。
- **关键报错**：`AssertionError: 未在 iframe 找到「致命错误」，全文本='Stop\nDeploy\n论文自...'` ——页面尚停在骨架文本，断言时 iframe 未渲染完。
- **排查步骤**：①本批 `git diff` 对 `ui/` **为空**、对 `core/nodes/analysis*`、`core/graph.py` 亦为空 ⇒ 无改动面可解释；②单跑该文件 → **4 passed**；③复跑整个 `-m browser` → **12 passed / 0 failed**（77.52s）。
- **结论**：**flaky，与 S7-10 无关**。与主控基线 12 passed 一致。
- **处置**：标记 flaky 待观察，不改断言、不加 retry（本项目正在治假绿，加 retry 是反方向）。

### 2. 交付件 `test_cp_6_7_2_shared_clone_cache_is_clean_now`（dev-plan P-33 记为"恒红"）

- 现状：**真绿**。我方实测 `workspace/repos/lmcinnes__umap` 的 `git status --porcelain` 输出为空，且该目录 `.git` 存在 ⇒ 用例**未走 skip 分支**、是真判定通过。
- 结论：残留已被清理，dev-plan §48 P-33 与 `docs/TODO.md` 里"待授权删除 3 个路径"的待办**可以关闭**（存证仍在 §48.2）。

### 3. 其余 0 失败

---

## 后续动作 / 放行意见

**我的意见：可以放行到"真跑授权"这一步之前，但 T-6-9 真跑之前必须先修 BUG-S7-10-01。**

理由：本批 12 条 AC 里可离线判定的 10 条已有 9 条硬通过，两处冻结区的门是真的（改前值可独立复原、改坏即红），三条约束确实同批落地，零改动红线逐一为空，无扰动结论用新旧实现对照复核过。
唯一不能放的是 **AC-S7-47 的硬保证成色**：约束 C 是 PRD 明写的"三条里唯一违反了会静默产出错误结论"的一条，也是唯一上硬防线的一条；而它现在可以被 `python -u -c` 这一个 token 绕过，绕过后**文件真落盘、还进台账**。
若带着这个洞去真跑：①"工具层拒绝触发次数"（架构 §19.11 要求补记的两项计数之一）本身不可信——模型只要写了带 flag 的形态，计数会是 0，而 0 会被误读成"计划变干净了"；②AC-S7-55 的"孤儿消失"可能被一次绕过的内联写码重新变成"孤儿还在"。

**待办**：
1. `- [ ] @全栈开发代理 [BUG-S7-10-01]` 修 `is_inline_code_write` 的 argv 定位方式（**仍是单一规则**，不加动词枚举/后缀白名单/不动阈值）；修完删除本报告对应 xfail 标记，重跑 `tests/test_sprint7_s710_gap_audit.py`。
2. `- [ ] @全栈开发代理` F2 / F3 两处守门加强（AC-S7-52 的 C 臂改行为断言；`w5_shares_one_predicate` 的名字与断言对齐或指向补测）。
3. `- [ ] @全栈开发代理` F8 顺手订正 `or ""` 优先级。
4. `- [ ] @架构师/PM` F4 的文档失实订正：dev-plan CP-6.5-1「均 True」与用例 docstring「5 条」需与实际一致（现已由补测把 5 条补齐，可改为指向补测）；dev-plan「去重 8 条」应为 **9 条**。
5. **下一次跑测试的触发条件**：①BUG-S7-10-01 修完 → 回归 `tests/test_sprint7_s710_gap_audit.py` + 全量；②Maria 授权真跑后 → 按 CP-6.9-1~5 判读（**主断言只认"孤儿消失"，禁引步骤对账**），并补记架构 §19.11 的两项计数。

**遗留风险**（随交付传下去）：
- **R-S7-49**：步骤对账 N/N 是 agent 自报归属，不证明计划被忠实执行；
- **R-S7-54**：编码环节那份历史产物自身有 6 处 `%` 优先级运行期缺陷，首轮真跑很可能整体失败——**那是预期且正确的**；
- **R-S7-57**：极短写码（≤阈值）漏放，已登记接受；**BUG-S7-10-01 不属于这一条**，不要合并处置；
- **R-S7-58**：长探针（语料 181）会被拒，真跑须计数观测能否 1 轮内自愈；
- **A/B 是软保证**（PRD §12.5.5 / R-S7-56）：本次所有绿灯都只覆盖到"机制在位"，覆盖不到"模型产的计划真的不再切目录"。
