# 平台红白名单（批次 2 收口门 `CP-2.12-1` 的判定依据）

> 立表：2026-10-21，`T-S8-2-12` 第三段。**本表是收口门的一部分，不是"免责声明"。**
> **最近一次修订：2026-10-21（A / A2 / B / C 四类修完后缩表至 22 条，53 → 22，见 §5）。**
> 一句话：**这台机器上跑不过去的用例并不都是回归。** 收口门由「全量零失败」改为
> 「**零计划内红 + 平台红白名单**」——白名单**只许缩小**，白名单外任何一条红（含 `error`）
> 都按真回归处理。

---

## 1. 口径：什么叫「平台」

**判据（可复现、可反驳）**：一条红属于「平台」，当且仅当它在**批次 2 动工之前的提交
`ac60379`** 上、在**同一台机器、同一个受限令牌（workspace-write）环境**下、
**同一个断点、同一个异常类型**上同样红。

- `ac60379` = `2544c4b`（批次 2 开工：`T-S8-2-2` 记基线 + `T-S8-2-1` 通道退场）的父提交，
  即**一行批次 2 代码都没进来**的那个提交。
- 复现方式（**worktree 冷跑，不污染主工作区**；这也是本表唯一被接受的举证方式）：

  ```powershell
  git worktree add .tmp_base_wt ac60379
  cd .tmp_base_wt
  $env:PYTHONUTF8=1
  ..\.venv\Scripts\python.exe -m pytest -m "not e2e and not browser" `
      --tb=line -q -p no:cacheprovider -rfE
  ```

- **2026-10-21 实测结果**：`45 failed / 2748 passed / 31 skipped / 58 deselected / 7 xfailed / 8 errors`
  ⇒ 立表时 **53 条 = 45 `failed` + 8 `error`**。
- 🔴 **反向判据（同样重要，防止本表变成"什么都往里塞"）**：
  **在 `ac60379` 上是绿的、现在红了 ⇒ 一律算计划内，不许进白名单。**

> ⚠ **本表只回答"是不是批次 2 造成的"，不回答"是不是项目问题"** —— 这两件事必须分开。
> 立表后做过两次成因复核（§6）：立表那 53 条里**只有 22 条**是这台机器真的观测不到；
> 其余 **31 条是项目自身的问题**（含 1 个真产品 bug），**已全部修掉并出表**。

## 2. 为什么「零失败」在这台机器上不可达

受限令牌（`SeChangeNotifyPrivilege` 一条）把三类能力剥掉了，全部是**结构性**失败：

| 能力 | 症状 | 白名单条目 |
|---|---|---|
| 建符号链接 | `OSError [WinError 1314] 客户端没有所需的特权` | 11 |
| `chmod` / POSIX 权限位 | `assert 438 == 384` / `438 == 448`（`0o666` vs `0o600`/`0o700`） | 5 |
| 外部命令不存在（`git` 子进程 / `sh`） | `FileNotFoundError [WinError 2]` | 4 |

另有一族属**依赖版本语义**（2 条）。**这 22 条之外，本机已无其它红。**

> ⚠ **本表不是"这些用例没价值"**。它们守的东西是真的，只是**在这台机器上观测不到**。
> 一旦换到有完整特权的环境（或 CI），应重跑 §1 的冷跑命令，把已转绿的条目从表里删掉。

---

## 3. 白名单（22 条，逐条）

### P1 符号链接特权（11 = 3 failed + 8 error）

| # | 用例 | 断点 | 成因 |
|---|---|---|---|
| 1 | `tests/test_sprint3_b2_strengthen.py::test_symlink_escape_write_rejected` | `pathlib.py:1198` | `WinError 1314`：`Path.symlink_to` 建不出来 |
| 2 | `tests/test_sprint3_b2_strengthen.py::test_symlink_escape_read_rejected` | `pathlib.py:1198` | 同上 |
| 3 | `tests/test_sprint3_b2_strengthen.py::test_symlink_escape_list_rejected` | `pathlib.py:1198` | 同上 |
| 4 | `tests/test_sprint7_s710_exec_locality.py::test_ac_s7_47_harness_can_really_write_files` | setup | 夹具先 `symlink` 一份 `.venv` 解释器 ⇒ `WinError 1314` |
| 5 | `tests/test_sprint7_s710_exec_locality.py::test_cp_6_6_1_original_placeholder_write_is_rejected_and_never_lands` | setup | 同上 |
| 6 | `tests/test_sprint7_s710_exec_locality.py::test_cp_6_6_3_rejected_command_pollutes_no_ledger` | setup | 同上 |
| 7 | `tests/test_sprint7_s710_exec_locality.py::test_cp_6_6_6_rejection_logs_warning_with_masked_command` | setup | 同上 |
| 8 | `tests/test_sprint7_s712_shell_metachars.py::test_cp_8_1_3_rejected_redirect_creates_no_file` | setup | 同上 |
| 9 | `tests/test_sprint7_s712_shell_metachars.py::test_cp_8_1_3_harness_can_really_create_files` | setup | 同上 |
| 10 | `tests/test_sprint7_s712_shell_metachars.py::test_cp_8_1_5_sandbox_does_not_block_the_inline_comparison_probe` | setup | 同上 |
| 11 | `tests/test_sprint7_s712_shell_metachars.py::test_cp_8_1_5_sandbox_still_supports_compound_commands` | setup | 同上 |

### P2 POSIX 权限位（5 failed）

| # | 用例 | 断点 | 成因 |
|---|---|---|---|
| 12 | `tests/test_sprint4_a3.py::test_cp_a3_1_file_permission_is_0600` | `:102` | `assert 438 == 384`（`0o666` vs `0o600`）——NTFS 上 `os.chmod` 只影响只读位 |
| 13 | `tests/test_sprint4_a3.py::test_cp_a3_4_git_credential_generates_askpass_script` | `:297` | `assert 438 == 448`（`0o666` vs `0o700`） |
| 14 | `tests/test_sprint4_a3.py::test_hardening_remember_relocks_preexisting_loose_permissions` | `:466` | `assert 438 == 384`，同上 |
| 15 | `tests/test_sprint4_e2e.py::test_cp_g2_2_mask_engaged_and_bydesign_stores` | `:893` | `.secrets` 期望 `0600`、实测 `0o666` |
| 16 | `tests/test_sprint4_e2e.py::test_cp_g2_3_credential_env_reaches_subprocess_via_spy` | `:1185` | askpass 脚本期望 `0700`、实测 `0o666`。⚠ **成因已变**：修 C 类之前它红在"解释器路径"上，修完才露出现在这个真因（**不是新增红**） |

### P3 外部命令不存在（4 failed）

| # | 用例 | 断点 | 成因 |
|---|---|---|---|
| 17 | `tests/test_sprint4_a3.py::test_cp_a3_4_askpass_script_echoes_token` | `subprocess.py:1538` | `WinError 2`：askpass 脚本要经 `git` 子进程回显 |
| 18 | `tests/test_sprint4_a3.py::test_hardening_askpass_host_sanitized_no_path_escape[git_credential:../../evil-evil]` | `subprocess.py:1538` | 同上 |
| 19 | `tests/test_sprint4_a3.py::test_hardening_askpass_host_sanitized_no_path_escape[git_credential:-default]` | `subprocess.py:1538` | 同上 |
| 20 | `tests/test_sprint4_a3.py::test_hardening_askpass_host_sanitized_no_path_escape[git_credential:host;rm -rf /-rm]` | `subprocess.py:1538` | 同上 |

### P5 既有 checkpoint 库的 `next` 语义（2 failed）

| # | 用例 | 断点 | 成因 |
|---|---|---|---|
| 21 | `tests/test_e2e2_acceptance_gaps.py::test_e2e2_acc_crash_after_resume_consumed_is_judged_awaiting_not_no_report` | `:403` | `assert ('coding',) == ()`：`__resume__` 被算进 `task.writes`，本机 `langgraph` 版本的 checkpoint 语义 |
| 22 | `tests/test_sprint5_t22_coding_gate.py::test_e2e2_second_serial_interrupt_snapshot_shape_and_controller_judgement` | `:785` | 同族（`get_state().next` 非空元组） |

> ⚠ 22 条里 **P2 的 5 条**严格说不是"环境差异"而是**产品在 Windows 上的已知弱点**
> （`os.chmod` 锁不住权限，代码自己打 WARNING 认了）：它们"在这台机器上不可能绿"，
> 但不是"产品没毛病"。见 §6-D。

---

## 4. 红面归属总账（2026-10-21 现测）

| 项 | 数 | 说明 |
|---|---|---|
| 全量回归（`-m "not e2e and not browser"`） | **14 failed / 2662 passed / 31 skipped / 8 error** | A / A2 / B / C 四类修完后 |
| **平台红（本表）** | **22** = 14 failed + 8 error | §3 逐条 |
| **计划内红** | **0** | `T-S8-2-12` 已清零，此后未再引入 |
| **归不到的红** | **0** | 这是本门唯一不许出现非零的一项 |

> 🔴 **判定纪律**：每次全量回归都要做一次 `差集`：
> `失败集合 − 白名单` **必须为空**。差集里出现任何一条 ⇒ **真回归**，收口不通过。
> 反之，白名单里出现**绿**的条目 ⇒ 该条要从本表删掉（白名单不许留绿项）。

## 4.1 收口复测沿革

| 时点 | 全量回归 | 本表条数 | 变化 |
|---|---|---|---|
| `T-S8-2-12` 立表 | 133 failed / 2522 passed / 26 error | **53** | 计划内 106 条待清 |
| `T-S8-2-12` 收口 | 45 failed / 2605 passed / 8 error | **53** | 计划内 106 → 0 |
| A 类 + A2 修复后 | 33 failed / 2645 passed / 8 error | **41** | 缩 12 行 |
| **B 类 + C 类修复后（当前）** | **14 failed / 2662 passed / 31 skipped / 8 error** | **22** | **再缩 19 行** |

> 📌 **这道门在本次会话里真实红过一次**：中途删掉三条被 AC 覆盖矩阵点名的用例后，
> 差集立刻报 **3 条**，按"把映射换发到活的用例"修掉后回到 0。⇒ **它不是恒绿的空壳。**

## 5. 缩表记录（53 → 22，共 31 条出表）

**每一次出表都是"真因被修掉"，不是"看起来绿了"。**

### 5.1 第一批（12 条，A 类 + A2）

| 转绿条目 | 真因 | 修在哪 |
|---|---|---|
| `tests/test_sprint4_c1.py` ×5 | **产品 bug**：`shlex.split` 的 POSIX 语义吃掉 Windows 路径；外加子进程中文输出按 GBK 写 | `core/shell_tokens.py`（A）+ `_build_sandbox_env` 强制 `PYTHONIOENCODING`/`PYTHONUTF8`（A2） |
| `test_sprint7_s712_shell_metachars.py::test_cp_8_1_5_run_command_does_not_block_legal_command` | A 类 | 同上 |
| `tests/test_sprint7_s706_probe_tool.py` ×3 | A 类 + 用例写死 POSIX-only 的 `uname` / 占位符 `python3` | 同上 + 用例改成"按真跑通挑命令" |
| `tests/test_sprint3_shell_parse.py::test_injection_semicolon_rm_not_shell_interpreted` | A 类（它的断言本来就是对的） | 同上 |
| `tests/test_sandbox_env_isolation.py` ×2 | 测试侧平台假设：Windows `os.environ` 键统一大写 | 用例改用平台无关取值 |

### 5.2 第二批（19 条，B 类 + C 类）

| 转绿条目 | 真因 | 修在哪 |
|---|---|---|
| `tests/test_sprint4_d1.py` ×8 / `d2.py` ×2 / `sprint3_b1.py` ×1 | **C**：假 `_run_subprocess` 只造 `.venv/bin/python`，产品在 Windows 查 `Scripts/python.exe` | 夹具改用产品单点 `_venv_bin_dir` / `_venv_python_exe` |
| `tests/test_sprint4_e1.py` ×1 | **C**：`PY` 常量与落盘路径写死 `bin/python` | 同上 |
| `tests/test_sprint4_c2.py` ×1 / `sprint3_c1.py` ×1 | **C**：`endswith("/x/y")` 写死 POSIX 分隔符 | 改断 `Path(...).parts[-2:]`（更严） |
| `tests/test_realrun_evidence.py` ×1 / `sprint3_b2_strengthen.py` ×1 | **C**：`str(Path("/a/b"))` 与字面量 `"/a/b"` 比 | 改断同一个 `Path` 对象 |
| `tests/test_sprint7_s7_02_coding_feedback.py` ×1 | **C**：日志路径后缀写死 `/` | 改断 `Path(...).parts[-2:]` |
| `tests/test_sprint6_s6_07_task_status.py` ×2 | **B**：20-thread 夹具**不在版本控制里**（`.gitignore:27`），本机是 0 张表的空库 | 加**可用性守卫**：缺失 / 空库 ⇒ `skip`（并如实登记"这条防线在本机未验证"） |

## 6. 成因复核：那 53 条里，哪些其实是**项目问题**？

> 立表时把 53 条一律标成"平台"，而那个标签只证明"**不是批次 2 造成的**"。
> 两次复核后按"修不修得动"重新分档 —— **31 条已修出表，22 条留下**：

| 档 | 条数 | 含义 | 处置 |
|---|---|---|---|
| **F 这台机器观测不到** | 11 | 符号链接特权（`WinError 1314`） | 保留；换 CI / 提权环境后应转绿 |
| **F2 宿主缺外部命令** | 4 | askpass 要经 `git` 子进程回显 | 保留；同上 |
| **D 产品 Windows 已知弱点** | 5 | `os.chmod(0o600/0o700)` 在 NTFS 上不生效（`secrets_store.py:155/316`、`checkpointer.py:56` **自己打 WARNING**） | 已认的 MVP 限制；真要锁权限得改用 Windows ACL（另立项） |
| **G 依赖版本语义** | 2 | `__resume__` 计入 `task.writes` ⇒ `snap.next` 非空 | 待判：产品适配，还是用例跟随版本 |
| ~~**B 夹具没进版本控制**~~ | ~~2~~ | 已加可用性守卫 ⇒ skip | ✅ §5.2 |
| ~~**C 测试写死 POSIX**~~ | ~~19~~ | 假 venv 布局 / 路径后缀 / `Path` 渲染 | ✅ §5.2 |
| ~~**A 产品 bug（分词）**~~ | ~~8~~ | `shlex.split` 吃 Windows 路径 | ✅ §5.1 |
| ~~**E 测试环境前提**~~ | ~~2~~ | 环境变量大小写 | ✅ §5.1 |

⇒ **剩下的 22 条 = 纯环境不可观测 15（F 11 + F2 4）+ 产品已知弱点 5（D）+ 依赖语义 2（G）**。
后两类**不改变"零计划内红"的结论**，但它们是真实技术债，**不许再被"平台"两个字盖住**。

## 7. 维护规则

1. **只许缩小**：白名单只能因为"该条转绿"而删行；**新增一行必须附 `ac60379` 冷跑证据**
   （命令 + 断点 + 异常类型），不得凭"看起来像环境问题"入表。
2. **不许写"不少于"**：本表是**精确集合**，不是下限。条数变了就要解释变了哪几条（§5）。
3. **不许用它给新红让路**：计划内红一律修，修不动的要写清"为什么改不动"，不能挪进本表。
4. **交叉核对**：本表条数必须与 `dev-plan.md` §6 `T-S8-2-12` 的 `CP-2.12-1` 记录逐数相同。
5. **分清两件事**：「不是批次 2 造成的」≠「不是项目问题」。入表只代表前者；后者按 §6 分档。
6. **`skip` 不等于通过**：§5.2 里 `s6_07` 那两条靠"夹具缺失 ⇒ skip"出表，
   它们守的防线在本机**未验证** —— 补夹具后必须重跑一遍并把结果记回来。
