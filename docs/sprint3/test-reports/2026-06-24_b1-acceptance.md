# 测试执行报告 - b1-acceptance（含 CP-A1-4 脆弱用例修复）

- **日期**：2026-06-24 20:27（本地时区）
- **执行人**：@测试工程师代理
- **Sprint**：sprint3
- **触发原因**：(1) 修复阶段 A 脆弱用例 `test_cp_a1_4_git_diff_config_is_pure_append`；(2) B1（sandbox 本地 venv + 4 护栏）独立验收 + 补强边界测试
- **commit**：2415c96（HEAD，工作树含未提交测试改动，未 commit）

---

## 执行范围
- 命令：
  - `.venv/bin/pytest tests/test_sprint3_a1.py -q`（A1 修复验证）
  - `.venv/bin/pytest tests/test_sprint3_b1.py -q`（B1 验收 + 补强，含 3 次稳定性连跑）
  - `.venv/bin/pytest -q -m "not e2e" --ignore=tests/test_paper_intake.py`（全量非 e2e 回归）
- 覆盖用例：`tests/test_sprint3_a1.py`（9）、`tests/test_sprint3_b1.py`（38 = dev 23 + 补强 15）
- 是否包含 e2e：否（按红线，不跑 e2e，凭证驱动耗 deepxiv 日配额）

---

## 任务一：CP-A1-4 脆弱用例修复

### 脆弱性独立复现
`git rev-parse --short HEAD` = 2415c96，`git status --porcelain config.py` 空、`git diff HEAD -- config.py` 空。
旧用例 `test_cp_a1_4_git_diff_config_is_pure_append` 用 `git diff HEAD config.py` 证"纯追加"并 `assert additions`（diff 非空）。A1 已 commit、工作树干净后该 diff 必为空 → `assert additions` 必红。**实测复现：1 failed, 8 passed**。这是把"未提交工作树状态"错误固化为回归断言；**生产代码 config.py 完全正确，非 BUG**。

### 修复方式
重命名为 `test_cp_a1_4_intro_commit_config_is_pure_append`，改为**对引入 sp3 常量的固定提交 2415c96 做 `git show` 断言**（不再依赖 `git diff HEAD` 工作树时序）：
- 删除行（非 `---` 头）== 0 → 证该提交对 config.py 纯追加；
- 新增行存在；
- **新增行覆盖全部 10 个 sp3 常量名**（SANDBOX_EXEC_TIMEOUT … STREAMLIT_PAGE_REPORT）→ 保留"sp3 为新增"的验收意图。

独立实证 commit 2415c96 对 config.py 为 `26 insertions(+), 0 deletions`。
"既有常量零修改"由既有用例 `test_cp_a1_4_sp1_sp2_constants_unchanged`（运行时值断言 MAX_TOTAL_LLM_CALLS==50 / MAX_FIX_LOOP_COUNT==3 等）覆盖——两者互补，永久稳定。

### 结果
`tests/test_sprint3_a1.py` **9 passed**（修复后全绿）。

---

## 任务二：B1 独立验收

### CP-B1-1~9 逐条独立复核结论（运行时实证）
| CP | 结论 | 独立核验要点 |
|----|------|------------|
| CP-B1-1 prepare_venv 创建 venv | PASS | 受控 tmp workspace 真实跑 venv，success=True，python_exe 存在、pyvenv.cfg 存在、env_info 含 python_version |
| CP-B1-2 护栏1 超时杀子进程树 | PASS | 真实 `sleep(30)`+派生孙进程 + timeout=2，timed_out=True，elapsed<10s；spy `_kill_process_tree` 触发；杀后 `os.killpg(pgid,0)` 抛 ProcessLookupError 证进程组已清；`ps` 查 0 残留 |
| CP-B1-3 护栏2 输出截断保留尾部 | PASS | 真实 5000B + max=1000，output_truncated=True，TAIL_MARKER_END 在尾部，体积受控 |
| CP-B1-4 护栏3 工作目录限定 | PASS | prepare_venv/run_in_venv/collect_artifacts 越界(/etc、/usr/bin/python)抛 SandboxCreationError，spy `Popen.assert_not_called()` 证校验在 subprocess 之前 |
| CP-B1-5 护栏4 子进程隔离不逃逸 | PASS | exit(42)→exit_code=42；Popen OSError→exit_code=-1；真实缺失二进制 FileNotFoundError→-1；均不抛 |
| CP-B1-6 禁 shell=True | PASS | 全文件 grep 无 `shell=True`；spy 录制 run_in_venv + prepare_venv 全路径 Popen args 是 list 且 shell 非 True |
| CP-B1-7 reuse 幂等 | PASS | 已有 pyvenv.cfg + reuse_existing=True 时无 `-m venv` 命令；reuse=False 反向重建 |
| CP-B1-8 pip 失败降级不抛 | PASS | 非瞬态 exit≠0 记 install_failed_packages+success=False+error；瞬态退避重试；venv 创建失败降级 |
| CP-B1-9 collect_artifacts 绝对路径限 workspace | PASS | 全绝对路径、限 workspace 下、跳过 .venv/__pycache__、自定义 patterns、缺失目录返空 |

**裁决：CP-B1-1~9 全部 PASS。**

### 重点核验项（亲自验证）
- **跨平台子进程树 kill 无残留**：POSIX `start_new_session=True` + `os.killpg(getpgid, SIGKILL)`；CP-B1-2 派生孙进程实测整组被杀，连跑 3 次后 `ps` 查 `time.sleep(30)` 残留进程 = 0。R-S3-01 关键落点验证通过。
- **禁 shell=True**：grep 全文件 0 处 + spy 双证通过。
- **路径越界**：work_dir/venv_dir/requirements_files 用 `resolve()`；补强覆盖 `../` 逃逸 + 符号链接逃逸（指向 workspace 外的 symlink dir）均被拒。
- **异常不逃逸**：Popen/communicate 的 OSError 路径均转 SandboxRunResult(exit_code=-1) 返回，不冒泡。

### python_exe 符号链接偏差 —— 测试视角合理性判定（独立实测）
开发对 `python_exe` 改用 lexical（`os.path.abspath`，不解符号链接）校验，work_dir/venv_dir/requirements_files 仍用 `resolve()`。三点独立实测：
1. **venv bin/python 确是符号链接到 workspace 外**：实测 `is_symlink()=True`，`resolve()` → `/usr/bin/python3.11`（系统解释器，workspace 外）；若用 resolve 校验 `is_relative_to(workspace)=False` → 会**误判越界**误拒合法 venv python。
2. **lexical 仍能拦 `../` 逃逸**：构造 `work/.venv/../../../../etc/python`，`os.path.abspath` 规范化 `..` 后 = `/tmp/etc/python`，`is_relative_to(workspace)=False` → **被正确拒绝**（补强用例 `test_reinforce_python_exe_dotdot_escape_rejected` 自动化覆盖）。
3. **护栏意图未削弱**：lexical 校验仍要求 python_exe 字面路径落在 workspace 下，传任意系统二进制（如 `/usr/bin/python`）仍被拒（CP-B1-4c 覆盖）。

**测试视角判定：偏差合理。** lexical 校验是符号链接 venv 场景下唯一可行的越界防护方式（resolve 会误判），且对 `../` 逃逸、绝对系统路径两类攻击面均保持拦截能力。唯一理论残留：若攻击者在 workspace 内放一个字面路径合法但 symlink 指向系统二进制的 python_exe，lexical 不会解开——但该路径需先由 prepare_venv 在 workspace 下创建，攻击面极窄，属可接受。建议主控按计划回架构师备案。

### 补强用例（15 条，沿用 sp2 A5 验收范式）
- 路径越界多形态：work_dir `../` 逃逸、python_exe `../` 逃逸（lexical 核验）、work_dir 符号链接逃逸、与 git_tools 路径不变量同源一致性（4）
- 超时边界：恰好不超时不杀（timed_out=False/exit=0）、超时 timed_out+exit≠0 组合（2）
- 输出截断字节级：恰好 == 上限不截、超一字节截断且尾部精确字节保留（2）
- collect_artifacts：跳过 __pycache__、排序去重（2）
- pip 失败分级：瞬态重试次数严格 == SANDBOX_PIP_MAX_RETRIES+1（config 驱动）、瞬态某次重试成功即停（2）
- reuse 幂等：venv 创建 subprocess 0 次（手工造 pyvenv.cfg，纯结构 spy）（1）
- 异常分类：SandboxCreationError ∈ PermanentError 家族、空/非列表 command 被拒（2）

---

## 结果摘要
- **A1**：9 passed（脆弱用例修复后全绿）
- **B1**：38 passed（dev 23 + 补强 15），3 次连跑 38/38（38.40 / 38.40 / 38.67s），**0 flaky**
- **进程残留**：连跑前后 `ps` 查 sleep(30) 残留进程均 = 0
- **全量非 e2e 回归**：**624 passed / 25 skipped / 28 deselected(e2e) / 0 failed**（122.20s）
  - 对照开发自报基线 608（585 阶段A + 23 B1，其中含 1 条 A1 红）：624 = 608 + 15 补强 + 1 条 A1 修复转绿，sp1/sp2/sp3 零退化
- 警告：1（langgraph 库级预存 LangChainPendingDeprecationWarning，sp1 即有，与本次无关）
- 总耗时：A1 0.03s / B1 单跑 ~38s / 回归 122.20s

## 失败排查
无失败（A1 脆弱用例已修复转绿，B1 全部 PASS）。

## 后续动作
- python_exe lexical 校验偏差：主控回架构师备案（测试视角已判定合理，见上）。
- 本次未跑 e2e（B1 为纯基础设施 mock+真实子进程，无 LLM/deepxiv 依赖，无 e2e 需求）。
- 测试文件改动未 commit（按约束不 git commit），交主控统一提交。

## 最终裁决
**PASS**（A1 脆弱用例已修复；B1 CP-B1-1~9 全部独立复核通过，无 BUG）。
