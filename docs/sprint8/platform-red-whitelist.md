# 平台红白名单（批次 2 收口门 `CP-2.12-1` 的判定依据）

> 立表：2026-10-21，`T-S8-2-12` 第三段。**本表是收口门的一部分，不是"免责声明"。**
> **最近一次修订：2026-10-21（A 类 + A2 修复后缩表 12 行，53 → 41，见 §5）。**
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
> 立表后做过一次成因复核：立表那 53 条里**只有 16 条**是这台机器根本观测不到的环境限制，
> 其余是项目自身的问题（含 1 个真产品 bug，已修）。复核表见 **§6**。

## 2. 为什么「零失败」在这台机器上不可达

受限令牌（`SeChangeNotifyPrivilege` 一条）把三类能力剥掉了，全部是**结构性**失败，
不是产品行为：

| 能力 | 症状 | 白名单条目 |
|---|---|---|
| 建符号链接 | `OSError [WinError 1314] 客户端没有所需的特权` | 11 |
| `chmod` / POSIX 权限位 | `assert 438 == 384`（`0o666` vs `0o600`） | 4 |
| 真子进程 / `venv` / 外部解释器 | `WinError 2`、`exit code 9009`、`venv python 解释器缺失` | 17 |

另有三族属**平台差异**（不同环境跑就不一样）：路径分隔符与路径形态（3）、
既有 checkpoint 库的版本语义（2）、既有 `sqlite` 线程库（2）、
以及 2 条逐条说明的（§3.P8）。

> ⚠ **本表不是"这些用例没价值"**。它们守的东西是真的，只是**在这台机器上观测不到**。
> 一旦换到有完整特权的环境（或 CI），应重跑 §1 的冷跑命令，把已转绿的条目从表里删掉
> —— 而不是继续沿用这张表。

---

## 3. 白名单（41 条，逐条）

### P1 符号链接特权（11 = 3 failed + 8 error）

| # | 用例 | 断点 | 成因 |
|---|---|---|---|
| 1 | `tests/test_sprint3_b2_strengthen.py::test_symlink_escape_write_rejected` | `pathlib.py:1198` | `WinError 1314`：`Path.symlink_to` 建不出来 |
| 2 | `tests/test_sprint3_b2_strengthen.py::test_symlink_escape_read_rejected` | `pathlib.py:1198` | 同上 |
| 3 | `tests/test_sprint3_b2_strengthen.py::test_symlink_escape_list_rejected` | `pathlib.py:1198` | 同上 |
| 4 | `tests/test_sprint7_s710_exec_locality.py::test_ac_s7_47_harness_can_really_write_files` | setup | 夹具先 `symlink` 一份 `.venv/bin/python` ⇒ `WinError 1314` |
| 5 | `tests/test_sprint7_s710_exec_locality.py::test_cp_6_6_1_original_placeholder_write_is_rejected_and_never_lands` | setup | 同上 |
| 6 | `tests/test_sprint7_s710_exec_locality.py::test_cp_6_6_3_rejected_command_pollutes_no_ledger` | setup | 同上 |
| 7 | `tests/test_sprint7_s710_exec_locality.py::test_cp_6_6_6_rejection_logs_warning_with_masked_command` | setup | 同上 |
| 8 | `tests/test_sprint7_s712_shell_metachars.py::test_cp_8_1_3_rejected_redirect_creates_no_file` | setup | 同上 |
| 9 | `tests/test_sprint7_s712_shell_metachars.py::test_cp_8_1_3_harness_can_really_create_files` | setup | 同上 |
| 10 | `tests/test_sprint7_s712_shell_metachars.py::test_cp_8_1_5_sandbox_does_not_block_the_inline_comparison_probe` | setup | 同上 |
| 11 | `tests/test_sprint7_s712_shell_metachars.py::test_cp_8_1_5_sandbox_still_supports_compound_commands` | setup | 同上 |

### P2 POSIX 权限位（4 failed）

| # | 用例 | 断点 | 成因 |
|---|---|---|---|
| 12 | `tests/test_sprint4_a3.py::test_cp_a3_1_file_permission_is_0600` | `:102` | `assert 438 == 384`（`0o666` vs `0o600`）——NTFS 上 `os.chmod` 只影响只读位 |
| 13 | `tests/test_sprint4_a3.py::test_cp_a3_4_git_credential_generates_askpass_script` | `:297` | `assert 438 == 448`（`0o666` vs `0o700`） |
| 14 | `tests/test_sprint4_a3.py::test_hardening_remember_relocks_preexisting_loose_permissions` | `:466` | `assert 438 == 384`，同上 |
| 15 | `tests/test_sprint4_e2e.py::test_cp_g2_2_mask_engaged_and_bydesign_stores` | `:893` | `.secrets` 期望 `0600`、实测 `0o666` |

### P3 子进程 / `venv` / 外部解释器（17 failed）

| # | 用例 | 断点 | 成因 |
|---|---|---|---|
| 16 | `tests/test_sprint3_b1.py::test_reinforce_reuse_no_venv_create_subprocess` | `:764` | 期望"不建 venv 而复用"，受限令牌下 `venv` 建不出解释器 |
| 17 | `tests/test_sprint4_a3.py::test_cp_a3_4_askpass_script_echoes_token` | `subprocess.py:1538` | `WinError 2`：askpass 脚本要经 `git` 子进程回显 |
| 18 | `tests/test_sprint4_a3.py::test_hardening_askpass_host_sanitized_no_path_escape[git_credential:../../evil-evil]` | `subprocess.py:1538` | 同上 |
| 19 | `tests/test_sprint4_a3.py::test_hardening_askpass_host_sanitized_no_path_escape[git_credential:-default]` | `subprocess.py:1538` | 同上 |
| 20 | `tests/test_sprint4_a3.py::test_hardening_askpass_host_sanitized_no_path_escape[git_credential:host;rm -rf /-rm]` | `subprocess.py:1538` | 同上 |
| 21 | `tests/test_sprint4_d1.py::test_cp_d1_1_not_passing_extra_env_propagates_none` | `:168` | 假 `_run_subprocess` 只造 `.venv/bin/python`，产品在 Windows 查 `Scripts/python.exe` |
| 22 | `tests/test_sprint4_d1.py::test_cp_d1_2_venv_create_and_pip_install_receive_extra_env` | `:194` | 同上 |
| 23 | `tests/test_sprint4_d1.py::test_cp_d1_2_pip_transient_retry_second_attempt_receives_extra_env` | `:214` | `venv python 解释器缺失`（同上） |
| 24 | `tests/test_sprint4_d1.py::test_cp_d1_2_collect_env_info_boundary_stays_none` | `:235` | 同族（venv 建不出来 ⇒ 没有 env 采集调用） |
| 25 | `tests/test_sprint4_d1.py::test_cp_d1_2_build_sandbox_env_merge_not_bypassed` | `:273` | 同族 |
| 26 | `tests/test_sprint4_d1.py::test_cp_d1_3_prepare_itself_never_writes_extra_env_into_install_log` | `:308` | 同族 |
| 27 | `tests/test_sprint4_d1.py::test_cp_d1_3_pip_echo_token_lands_in_install_log_characterization` | `:331` | 同族 |
| 28 | `tests/test_sprint4_d1.py::test_boundary_pip_permanent_failure_single_attempt_extra_env_intact` | `:376` | 同族（轮不到真跑） |
| 29 | `tests/test_sprint4_d2.py::test_cp_d2_2_parent_pip_credential_not_visible_in_sandbox_popen_env` | `:184` | 同族（`bin/python` vs `Scripts/python.exe`） |
| 30 | `tests/test_sprint4_d2.py::test_cp_d2_2_explicit_extra_env_injection_takes_over` | `:217` | 同族 |
| 31 | `tests/test_sprint4_e1.py::test_python_exe_fallback_to_existing_venv_after_resume` | `:317` | `\Scripts\python.exe` vs `/bin/python`：POSIX 形态的解释器路径 |
| 32 | `tests/test_sprint4_e2e.py::test_cp_g2_3_credential_env_reaches_subprocess_via_spy` | `:1125` | 同族（spy 观测的是 `.venv/bin/python`） |

### P4 路径分隔符 / 路径形态（3 failed）

| # | 用例 | 断点 | 成因 |
|---|---|---|---|
| 33 | `tests/test_realrun_evidence.py::test_ev_23_non_json_objects_are_coerced_not_crashed` | `:238` | `assert '\\a\\b' == '/a/b'`：用例写死 POSIX 渲染 |
| 34 | `tests/test_sprint3_b2_strengthen.py::test_serialize_default_str_handles_path` | `:340` | 同上（`'\\x\\y' == '/x/y'`，且 `or endswith("x/y")` 在 Windows 上不起作用） |
| 35 | `tests/test_sprint7_s7_02_coding_feedback.py::test_cp_1_3_1_log_file_path_subkey_and_error_category` | `:99` | 落盘路径按 Windows 绝对路径落，与用例的 POSIX 期望形态不同 |

### P5 既有 checkpoint 库的 `next` 语义（2 failed）

| # | 用例 | 断点 | 成因 |
|---|---|---|---|
| 36 | `tests/test_e2e2_acceptance_gaps.py::test_e2e2_acc_crash_after_resume_consumed_is_judged_awaiting_not_no_report` | `:403` | `assert ('coding',) == ()`：`__resume__` 被算进 `task.writes`，本机 `langgraph` 版本的 checkpoint 语义 |
| 37 | `tests/test_sprint5_t22_coding_gate.py::test_e2e2_second_serial_interrupt_snapshot_shape_and_controller_judgement` | `:785` | 同族（`get_state().next` 非空元组） |

### P6 既有 `sqlite` 线程库（2 failed）

| # | 用例 | 断点 | 成因 |
|---|---|---|---|
| 38 | `tests/test_sprint6_s6_07_task_status.py::test_cp_4_3_3_list_threads_20_real_db` | `:158` | `[list_threads] 枚举 checkpoints 失败` ⇒ `assert 0 == 20` |
| 39 | `tests/test_sprint6_s6_07_task_status.py::test_cp_4_3_2_list_threads_ordering_new_first` | `:178` | 同族（空列表 ⇒ `IndexError`） |

> ⚠ 这两条的**真因已查明、且不是环境**：`tests/fixtures/checkpoints_s6_full20.db` 被
> `.gitignore:27` 忽略（本地由主控 `cp` 生成），本机这份是 **4096 字节、0 张表**的空库 ⇒
> `sqlite3.OperationalError: no such table: checkpoints`。**用例缺"夹具不存在/为空 ⇒ skip"
> 的守卫**。属项目问题，见 **§6-B**。

### P8 逐条说明（2 failed）

| # | 用例 | 断点 | 成因 |
|---|---|---|---|
| 40 | `tests/test_sprint3_c1.py::test_reinforce_resolve_dir_fallback_workspace_and_thread` | `:510` | `out.endswith("/task/code")`：断言写死 POSIX 分隔符（**产品行为正确**） |
| 41 | `tests/test_sprint4_c2.py::test_cp_c2_4_credential_env_injected_into_run_command_factory` | `:444` | `endswith('/2409.05591/code')`：同上 |

---

## 4. 红面归属总账（2026-10-21 现测）

| 项 | 数 | 说明 |
|---|---|---|
| 全量回归（`-m "not e2e and not browser"`） | **33 failed / 2645 passed / 29 skipped / 8 error** | A 类 + A2 修完后的那一刻 |
| **平台红（本表）** | **41** = 33 failed + 8 error | §3 逐条 |
| **计划内红** | **0** | `T-S8-2-12` 已清零，此后未再引入 |
| **归不到的红** | **0** | 这是本门唯一不许出现非零的一项 |

> 🔴 **判定纪律**：每次全量回归都要做一次 `差集`：
> `失败集合 − 白名单` **必须为空**。差集里出现任何一条 ⇒ **真回归**，收口不通过。
> 反之，白名单里出现**绿**的条目 ⇒ 该条要从本表删掉（白名单不许留绿项，否则它会悄悄变成
> "永久豁免"）。

## 4.1 收口复测沿革

| 时点 | 全量回归 | 本表条数 | 变化 |
|---|---|---|---|
| `T-S8-2-12` 立表 | 133 failed / 2522 passed / 26 error | **53** | 计划内 106 条待清 |
| `T-S8-2-12` 收口 | 45 failed / 2605 passed / 8 error | **53** | 计划内 106 → 0 |
| **A 类 + A2 修复后**（同日） | **33 failed / 2645 passed / 8 error** | **41** | **缩 12 行**（§5） |

> 📌 **这道门在本次会话里真实红过一次**：中途删掉三条被 AC 覆盖矩阵点名的用例后，
> 差集立刻报 **3 条**（`test_cp_f1_4_ac_has_mock_test_coverage[AC-S3-04]` /
> `test_cp_g1_1_ac_has_mock_test_coverage[AC-S4-04]` / `test_cp_g1_1_mapped_tests_not_hollow[AC-S4-04]`），
> 按"把映射换发到活的用例"修掉后回到 0。⇒ **它不是恒绿的空壳。**

## 5. 缩表记录（53 → 41，2026-10-21）

12 条因为**真因被修掉**而转绿（不是"看起来绿了"）：

| 转绿条目 | 真因 | 修在哪 |
|---|---|---|
| `tests/test_sprint4_c1.py` ×5 | **产品 bug**：`shlex.split` 的 POSIX 语义吃掉 Windows 路径；外加子进程中文输出按 GBK 写 | `core/shell_tokens.py`（A 类）+ `_build_sandbox_env` 强制 `PYTHONIOENCODING`/`PYTHONUTF8`（A2） |
| `tests/test_sprint7_s712_shell_metachars.py::test_cp_8_1_5_run_command_does_not_block_legal_command` | A 类 | 同上 |
| `tests/test_sprint7_s706_probe_tool.py` ×3 | A 类（路径被吃）+ 用例写死 POSIX-only 的 `uname` / 占位符 `python3` | 同上 + 用例改成"按真跑通挑命令" |
| `tests/test_sprint3_shell_parse.py::test_injection_semicolon_rm_not_shell_interpreted` | A 类（它的断言本来就是**对的**：期望未被吃掉的 argv） | 同上 |
| `tests/test_sandbox_env_isolation.py` ×2 | 测试侧平台假设：Windows `os.environ` 键统一大写 | 用例改用平台无关取值 |

## 6. 成因复核：这 41 条里，哪些其实是**项目问题**？

> 立表时把 53 条一律标成"平台"，而那个标签只证明"不是批次 2 造成的"。复核后如实分档
> ——**§5 那 12 条就是这样被挑出来修掉的**：

| 档 | 条数 | 含义 | 处置 |
|---|---|---|---|
| **F 这台机器观测不到** | 11 | 符号链接特权（`WinError 1314`） | 保留在表；换 CI / 提权环境后应转绿 |
| **F2 宿主缺外部命令** | 5 | askpass 要 `sh`/`git`（4）、`python3` 是应用商店占位符（1） | 同上 |
| **D 产品 Windows 已知弱点** | 4 | `os.chmod(0o600/0o700)` 在 NTFS 上不生效（`secrets_store.py:155/316`、`checkpointer.py:56` **自己打 WARNING**） | 已认的 MVP 限制；真要锁权限得改用 Windows ACL（另立项） |
| **B 夹具没进版本控制** | 2 | `checkpoints_s6_*.db` 被 `.gitignore:27` 忽略，本机是空库；用例无 skip 守卫 | **项目问题**，待修 |
| **C 测试写死 POSIX 布局/分隔符** | 16 | d1/d2/b1 的假 `_run_subprocess` 只造 `.venv/bin/python`；`endswith('/x/y')`；spy 只认 `bin/python` | **项目问题**，待修（机械） |
| **G 依赖版本语义** | 2 | `__resume__` 计入 `task.writes` ⇒ `snap.next` 非空 | 待判：产品适配，还是用例跟随版本 |
| 已修（立表时在册，现已出表） | 12 | A 类产品 bug + A2 编码 + 测试侧平台假设 | ✅ §5 |

⇒ **纯环境不可观测 = F 11 + F2 5 = 16 条**；其余 **25 条是项目自身问题**
（12 条已修，**13 条待修**：B 2 + C 16 中的 10 条 + G 2 —— 逐条见上表）。
**这些待修项不改变"零计划内红"的结论**，但它们是真实技术债，**不许再被"平台"两个字盖住**。

## 7. 维护规则

1. **只许缩小**：白名单只能因为"该条转绿"而删行；**新增一行必须附 `ac60379` 冷跑证据**
   （命令 + 断点 + 异常类型），不得凭"看起来像环境问题"入表。
2. **不许写"不少于"**：本表是**精确集合**，不是下限。条数变了就要解释变了哪几条（§5）。
3. **不许用它给新红让路**：计划内红一律修，修不动的要写清"为什么改不动"，不能挪进本表。
4. **交叉核对**：本表条数必须与 `dev-plan.md` §6 `T-S8-2-12` 的 `CP-2.12-1` 记录逐数相同。
5. **分清两件事**：「不是批次 2 造成的」≠「不是项目问题」。入表只代表前者；后者按 §6 分档。
