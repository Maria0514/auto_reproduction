# 测试执行报告 - s706-cp-verify-red（S7-06 CP 测试 + 四道命门逐环验红 + 档 A 真机探测）

- **日期**：2026-07-29 00:26（本地时区）
- **执行人**：@全栈开发代理（S7-06 批次 4 第三程）
- **Sprint**：sprint7
- **触发原因**：dev-plan §26 **T-S7-4-8**（AC-S7-15~26 全覆盖 + 四道命门逐环验红 + 全量回归）+ **T-S7-4-9 档 A**（工具层真机探测，零 deepxiv 配额、零 LLM）+ handoff 归档
- **commit**：`c061700`（工作区含 S7-06 未提交改动，见文末"生产代码 diff 快照"）

> **档 B（T-S7-4-9 / CP-4.9-3 端到端真跑，AC-S7-25）本程未执行**——它耗 deepxiv 日配额 + 真实 LLM，须 Maria 单独授权具体动作。CP-4.9-3 在 dev-plan 中**保持 `[ ]`**，按 §25.4 容量裁剪线**延后不注销**。

---

## 1. 执行范围

| 项 | 内容 |
|---|---|
| 新增测试文件 | `tests/test_sprint7_s706_probe_tool.py`（18 用例）、`tests/test_sprint7_s706_env_facts.py`（29 用例），合计 **47 用例** |
| 命令（新增文件） | `.venv/bin/pytest -q -p no:cacheprovider --color=no tests/test_sprint7_s706_probe_tool.py tests/test_sprint7_s706_env_facts.py` |
| 命令（全量回归） | `.venv/bin/pytest -q -p no:cacheprovider --color=no -m "not e2e"` |
| 是否包含 e2e | **否**（`-m "not e2e"` 全程排除）。零 LLM、零 deepxiv API、零网络 |
| 档 A 真机探测 | 直接调 `make_probe_environment_tool(base_dir=<workspace>)` 真跑清单 15 条（纯本机 subprocess，零配额） |

**分工**：`_probe_tool` 文件覆盖工具层（AC-S7-16 / 21 / 22 / 23 / 24 / 26 + AC-S7-15 的 cwd 面）；`_env_facts` 文件覆盖节点与送达面（AC-S7-15 / 17 / 18 / 19 / 20）。AC-S7-22 的一正一负按 PRD 要求写在**同文件相邻两条**。

---

## 2. 结果摘要

| 指标 | 值 |
|---|---|
| 新增两文件 | **47 passed / 0 failed**（0.85s） |
| 全量非 e2e 回归（run 1） | **2103 passed / 0 failed / 25 skipped / 46 deselected / 139.99s** |
| 全量非 e2e 回归（run 2） | **2103 passed / 0 failed / 25 skipped / 46 deselected / 137.42s** |
| 相对基线 | 基线 **2056 绿** + 新增 **47** = **2103**，**账目精确闭合，零退化零失败** |
| 警告 | 3（`LangChainPendingDeprecationWarning` ×1 + `PydanticDeprecatedSince20` ×2，均为既有） |

**pre-existing flaky 单列**：`tests/test_plan_review_e2e.py::test_e2e_code_only`（Playwright 点 shadcn iframe「仅复现代码」按钮，dev-plan §31 **P-9** 已登记，主控已用 `git stash` 对照实验证明与 S7-06 无因果）——**本次两轮全量回归中该用例均通过**，故未进失败列。它仍属已登记的 UI harness 等待策略问题，不在本批处理范围。

---

## 3. 四道命门逐环验红（本程的全部价值所在）

**方法论**：每次验红都是**真改生产源码**（不是 monkeypatch 假装），跑测试确认变红，然后**立即还原并核对 md5 / git diff**。所有验红操作的宿主副作用被严格限制在 `tmp_path` 内（必拒集用例全程 mock `_run_subprocess`，零真实进程；副作用探针的破坏性命令全部相对 cwd = tmp 探测目录）。

### 命门 1 — AC-S7-16 只读保证

| 项 | 内容 |
|---|---|
| **改了什么** | `core/tools/env_probe_tool.py:204-209` 的 `if tuple(argv) not in _ALLOWED_ARGV: return _reject_with_list()` **整段注掉** |
| **预期** | 必拒集断言 + 副作用探针 + `_run_subprocess` 未被调用断言全部变红；必过集（对照组）仍绿 |
| **实测** | **4 failed / 14 passed** ✅ 符合预期 |
| 红掉的用例 | `test_ac_s7_16_must_reject_structured_and_no_process`（必拒 12 条全放行）<br>`test_ac_s7_16_side_effect_probe_file_intact`（探针被真删真改）<br>`test_ac_s7_16_reject_does_not_raise_on_malformed_command`<br>`test_ac_s7_22_negative_probe_rejects_same_two_commands`（探测侧解释器形态被放行） |
| 仍绿的用例 | `test_ac_s7_16_must_pass_readonly_commands`（**必过集对照组**——它证明本条不是靠"永远拒绝"变绿的）、`test_ac_s7_22_positive_...`（coding 侧不受影响） |
| 关键报错 | `AssertionError: 副作用探针被改动（删除 / 改名 / 清空 / 覆盖写 / 改权限）—— 被拒命令其实执行了，只断返回码抓不到这一档`<br>`assert ('FILE', 'UNREADABLE: PermissionError', 0) == ('FILE', 'ORIGINAL', 420)` |
| 还原核对 | `md5sum core/tools/env_probe_tool.py` = `8587ea451ad803ac3d27a67f78233be8`（与改动前一致），还原后 18 passed |

> **副作用探针的实测意义**：验红时探针文件被 `rm -f` 删掉、`truncate` 重建、`echo >` 覆盖、`chmod 000` 锁死——快照从 `('FILE','ORIGINAL',420)` 变成 `('FILE','UNREADABLE: PermissionError',0)`。**只断返回码的写法完全抓不到这一档**，这正是 §14.5 要求副作用探针的原因。

### 命门 2 — AC-S7-18 防白探四环（三次验红逐条做）

四环全通基线：`ring1`（3 用例，含三 return 点参数化）/ `ring2` / `ring3` / `ring4`（2 用例）全绿。

#### 验红 2-a：注掉 `planning.py` `build_context` lambda 第 6 实参

| 项 | 内容 |
|---|---|
| **改了什么** | `core/nodes/planning.py:723` 的 `state.get("local_env_facts"),` 注掉 |
| **文档预期** | dev-plan L1345 / architecture §15.6 L961 称 **②④必红、①③绿** |
| **实测** | **仅 ④ 红**（1 failed / 28 passed），①②③ 全绿 |
| 关键报错 | `AssertionError: ④端到端环断：模型没收到环境事实`<br>`assert 'local_env_facts' in {'arxiv_id': ..., 'method_summary': ..., 'repo_candidate_count': 1, ...}` |
| **判定** | **文档口径有一处内部矛盾，覆盖能力未受损**——详见 §5 出入 1。② 按 CP-4.6-1 / §15.6 的明文设计是**直接调 `_format_planning_context(...)`**，它拿不到 lambda 层的漏传，逻辑上不可能红；而 dev-plan L1304 自己写着"**④端到端环守的就是这一行**"。**守这一行的环（④）确实红了**，防线成立 |
| 还原核对 | 见 §6（本次还原过程中出过一次操作事故，已完整回滚并逐字节核对） |

#### 验红 2-b：注掉 `_map_resource_scout_result` 的 `local_env_facts` 写入

| 项 | 内容 |
|---|---|
| **改了什么** | `core/nodes/resource_scout.py::_with_env_facts` 里 `update["local_env_facts"] = facts` 注掉 |
| **预期** | ①②④必红 |
| **实测** | **6 failed / 23 passed** ✅ 符合预期 |
| 红掉的用例 | ①`ring1_map_result_emits_local_env_facts`、①`ring1_three_return_points_all_write[None]`、①`ring1_three_return_points_all_write[result1]`、②`ring2_planning_context_carries_env_facts`、④`ring4_model_actually_receives_env_facts`，外加连带 `test_ac_s7_17_no_gpu_is_a_valid_conclusion`（它断言"命令不可用也是事实、照常写入"） |
| 仍绿 | ③`ring3_analysis_notes_channel_never_reaches_planning` |
| 还原核对 | `md5sum core/nodes/resource_scout.py` = `e2dd62fcd0116e6ba2a25e118d40d77c`，还原后 29 passed |

#### 验红 2-c：**假解法复刻**（把 `_digest_env_probe` 产出改写进 `analysis_notes`）— AC-S7-18 的交付证据

| 项 | 内容 |
|---|---|
| **改了什么** | `_with_env_facts` 改为 `update["analysis_notes"] = f"{prev}\n{facts}"`（即"只落在给人看的备注通道"这一档假解法的忠实复刻） |
| **先证明假解法"看起来是工作的"** | 直跑 `_map_resource_scout_result(...)` → `keys = ['analysis_notes','current_step','degraded_nodes','node_errors','resource_info']`、`has local_env_facts: False`、**`analysis_notes contains A100: True`**。也就是说：**事实确实被提取出来了、人也确实能在备注里看到**——这正是这档假解法最危险的地方 |
| **预期** | ①②④必红、③绿 |
| **实测** | **6 failed / 23 passed** ✅ 完全符合预期，③ 单独跑 `1 passed` |
| 关键报错（②，最能说明问题的一条） | `AssertionError: ②送达环断：规划上下文没有环境事实落点键`<br>`assert 'local_env_facts' in {'arxiv_id': ..., 'method_summary': ..., 'repo_candidate_count': 1, ...}` |
| **结论** | **假解法过不了四环**。③ 之所以绿是设计使然：它把"备注通道到不了规划"钉成常驻断言，使任何"改回备注通道"的实现**必然同时打红 ②④ 而 ③ 恒绿**，无法靠放宽 ② 的断言绕过 |
| 还原核对 | md5 同上，还原后 29 passed |

> **测试设计上的一处主动加固**：② 与 ④ 的 state 构造从 `update["local_env_facts"]` 改为**承接 update 的全部产出**（`local_env_facts` 与 `analysis_notes` 都接）。否则假解法复刻时 ②④ 会以 `KeyError` 形态失败，看不出"事实去哪了"；改后得到的是上面那条干净的断言失败，直接指出"规划上下文没有落点键"。

### 命门 3 — AC-S7-21 清单形态守门（两次验红）

#### 验红 3-a：往 `_PROBE_COMMANDS` 加一条**带自由参数**的条目

| 项 | 内容 |
|---|---|
| **改了什么** | 清单末尾加 `"df -h {path}"` |
| **实测** | **2 failed / 16 passed** ✅ |
| 红掉的用例 | `test_ac_s7_21_command_list_shape_is_frozen`（目标）+ 连带 `test_ac_s7_24_description_has_no_task_level_dynamic_values`（未渲染花括号进了工具描述——**这是正确的连带守门**） |
| 关键报错 | `AssertionError: 清单条目 'df -h {path}' 的 token '{path}' 含占位符 / 元字符 '{'——带自由参数的条目会同时重新打开五类禁止项（R-S7-16）` |

#### 验红 3-b：往清单加一条**解释器形态**条目

| 项 | 内容 |
|---|---|
| **改了什么** | 清单末尾加 `"python -c print(1)"` |
| **实测** | **3 failed / 15 passed** ✅ |
| 红掉的用例 | `test_ac_s7_21_command_list_shape_is_frozen`（目标）+ **`test_ac_s7_16_must_reject_structured_and_no_process`** + **`test_ac_s7_22_negative_probe_rejects_same_two_commands`** |
| 关键报错 | `AssertionError: 清单条目 'python -c print(1)' 含解释器执行形态 token '-c'` |
| **额外收获（R-S7-16 的活体证明）** | 必拒集里的 `python -c "print(1)"` 经 `shlex.split` 得到 `['python','-c','print(1)']`，与新加的清单条目 argv **完全相同** ⇒ **加这一条清单，等于同时把"解释器执行任意代码"这条禁止项重新打开**。AC-S7-16 因此一起变红——这就是"清单是整条只读边界的信任根"的实测证据，也是 R-S7-16 为何必须靠本条守门的直接演示 |
| 还原核对 | md5 = `8587ea451ad803ac3d27a67f78233be8`，还原后 18 passed |

### 命门 4 — AC-S7-26 返回恒不触发 8000 截断（两种失效形态各验一次）

#### 验红 4-a：把 `_PROBE_OUTPUT_MAX_BYTES` 调到 8000 以上

| 项 | 内容 |
|---|---|
| **改了什么** | `_PROBE_OUTPUT_MAX_BYTES: int = 2500` → `9000` |
| **实测** | **1 failed / 17 passed** ✅ |
| 关键报错 | `AssertionError: 最坏两路满载下返回串 19504 字符 >= TOOL_RESULT_MAX_LENGTH(8000)——会被 react_base 截断成残缺 JSON，整条探测结果静默丢失`<br>`assert 19504 < 8000` |
| 对照 | 主控独立复核报 18752；本文件填充料换行更密（1/14 vs 主控口径）故为 19504，**同一量级、结论一致** |

#### 验红 4-b：改传 `config.SANDBOX_OUTPUT_MAX_BYTES`（1 MiB）

| 项 | 内容 |
|---|---|
| **改了什么** | `output_max_bytes=_PROBE_OUTPUT_MAX_BYTES` → `output_max_bytes=1_048_576`（= `config.SANDBOX_OUTPUT_MAX_BYTES`，§17.3 明令禁止的写法） |
| **实测** | **2 failed / 16 passed** ✅ |
| 红掉的用例 | `test_ac_s7_26_worst_case_two_way_saturation_never_truncated`（16213 字符 > 8000）+ **`test_ac_s7_23_timeout_actually_passed_down`**（它同时断言 `output_max_bytes == _PROBE_OUTPUT_MAX_BYTES` 且 `!= config.SANDBOX_OUTPUT_MAX_BYTES`，**两道守门叠上了**） |
| 还原核对 | md5 = `8587ea451ad803ac3d27a67f78233be8`，还原后 18 passed |

#### ⚠ P-8 硬约束的落实（AC-S7-26 构造口径）

dev-plan §31 **P-8** 实证：「恒不触发 8000 截断」**只对真实命令输出形态成立**。本文件严格遵守：

- 填充料 `_freeze_shaped_bytes()` 产出 `pkg000==1.2.0\n` 形态（14 字节/行，**换行密度 1/14**）；
- 本机实测 `pip list --format=freeze` 密度 **1/18.1**（见 §4），本测试用的 1/14 **更密、属保守取值**；
- P-8 实证撑破 8000 需要 **1/2.7** 的病态密度（纯换行填充），清单 15 条无一可达（主控实测 `lscpu` 1/61.5、`free -h` 1/68）；
- 该纪律已写死进测试函数 docstring，注明"**不得**改用纯换行等病态填充，否则本守门会以'设计缺陷'之名恒红，把唯一的静默失效守门废掉"；
- **验红能力未被该口径削弱**：4-a / 4-b 两次验红都在真实形态填充料下正常打红。

**防空转保险**：本用例在长度断言之后追加了「两路都真的满载了」的 sanity 断言（`len(stdout_tail.encode()) >= _PROBE_OUTPUT_MAX_BYTES` 且 `truncated is True`），防止"填充料没撑满 → 长度断言空转 → 假绿"。

---

## 4. T-S7-4-9 档 A：真机探测实测（零 deepxiv 配额、零 LLM）

**执行方式**：`make_probe_environment_tool(base_dir=<WORKSPACE_DIR>/s706-track-a)`，逐条 `invoke` 清单 15 条。

### CP-4.9-1 实测到的本机事实

| 维度 | 实测结果 |
|---|---|
| **GPU / 驱动** | `nvidia-smi` / `nvidia-smi -L` → `subprocess start failed: [Errno 2] No such file or directory` ⇒ **本机无 NVIDIA GPU 工具链**。digest 渲染为「该命令在本机不可用」（内部英文串被挡在规划上下文之外，AC-S7-19 精神） |
| **CUDA** | `nvcc --version` → 同上，**本机无 CUDA 编译器** |
| **CPU** | `lscpu` → `x86_64` / **12 核** / `AMD EPYC-Genoa Processor` / 单 socket / 单 NUMA |
| **内存** | `free -h` → `Mem 22Gi`（used 5.9Gi / available 10Gi）、`Swap 7.6Gi` |
| **内核 / 架构** | `uname -srm` → `Linux 6.1.62-4.x86_64 x86_64` |
| **磁盘** | `df -h .` → `/dev/mapper/vg_data-lv_data` **278G 总量 / 241G 可用 / 9% 使用**，挂载点 `/data` |
| **Python** | 见下方"PATH 依赖"一栏 |
| **已装包** | `pip list --format=freeze` → 90 行（`aiosqlite==0.22.1` … `zstandard==0.25.0`，含 `deepxiv-sdk==0.2.5`、`GitPython==3.1.50`），**1633 字节** |
| **工具链** | `git 2.39.3` / `gcc (GCC) 8.5.0` / `GNU Make 4.2.1` / `cmake 3.26.5` |
| 全 15 条 | `timed_out` 恒 `False`、`truncated` 恒 `False`、无一抛异常 |

### ⚠ 真机新发现：探测结果依赖宿主 PATH（如实登记，非 bug）

`_run_subprocess` 走 `_build_sandbox_env` 白名单继承，**`PATH` 在白名单内**。同一台机器两种 PATH 下结果不同：

| 命令 | 默认 PATH（未 activate venv） | PATH 前置 `.venv/bin` |
|---|---|---|
| `python3 --version` | `Python 3.6.8`（系统） | `Python 3.11.5`（项目 venv） |
| `python --version` | `Python 2.7.18`（系统） | `Python 3.11.5` |
| `pip --version` | `subprocess start failed: No such file or directory: 'pip'` | `pip 26.1.1 from .../.venv/...` |
| `pip list --format=freeze` | 同上（探不到已装包） | 90 行真实包列表 |

**性质判定**：这**不是缺陷**——清单刻意用裸命令名（`architecture.md` §14.6 R-S7-17 已把"PATH 解析"这一面登记为"等价于宿主已陷、不做安全剧场加固"）。但它意味着 **"已装包"这一项事实能否探到，取决于流水线进程自身的 PATH**。本项目以 `.venv/bin/python` 直接启动而不 activate 时，PATH 里没有 `.venv/bin` ⇒ 探测会得到「pip 在本机不可用」。**登记为观测事实，供后续按需处置（如把清单加一条 `python3 -m pip list --format=freeze`，属单点加清单条目、机制不动）；本批不扩围改动。**

### CP-4.9-2 真机 AC-S7-26（把 §17.2 对照组从 mock 推到真机）

以 PATH 含 `.venv/bin` 的真实 `pip list --format=freeze` 为准：

| 断言 | 实测 |
|---|---|
| 返回串长度 < `TOOL_RESULT_MAX_LENGTH`(8000) | **1856 < 8000** ✅ |
| `react_base._truncate_tool_result(out) == out`（原样不变） | ✅ |
| `resource_scout._parse_tool_content(...)` 解析成功 | ✅ |
| 6 键齐全 | `['command','exit_code','stderr_tail','stdout_tail','timed_out','truncated']` ✅ |
| stdout_tail 实测 | 1633 字节 / 90 换行 / **换行密度 1/18.1**（P-8 的真实形态基准） |
| `truncated` 标志 | `False`（1633 < 2500，本机包数未触及返回端上限） |

**诚实标注**：本机 90 个包（1633 字节）**未撑到 2500 字节上限**，所以真机这一跑**没有走到返回端截断分支**——"撑满后仍 < 8000"这一档由 mock 满载用例（`test_ac_s7_26_...`，含两次验红）覆盖。真机这一跑证明的是"真实机器上整条链路解析成功、6 键齐全"，两者互补。

### 真机 digest（规划上下文实际收到的文本）

15 条工具历史 → `_digest_env_probe` 产出 **2221 字符 / 82 行**（结构性上界 6KB 之内），段首为「本机环境实测（资源探索阶段真机探测所得，非论文推断）：」，逐条 `$ <命令>` + 输出。**digest 中零内部术语**（无 `probe_environment` / `resource_scout` / `from_scratch` / `subprocess start failed`），`lscpu` / `pip list` 两条被 400 字符上限正常截断。

---

## 5. AC-S7-15~26 覆盖矩阵

| AC | 归属 | 覆盖用例（文件::用例） | 是否验红 | 状态 |
|---|---|---|---|---|
| **AC-S7-15** | 工具集 5→6 + cwd 锚定 + planning 负向 | `_env_facts::test_ac_s7_15_scout_tool_set_is_six_with_probe`（正向 6 工具 + max_rounds 20）<br>`_env_facts::test_ac_s7_15_planning_tool_set_unchanged_no_probe`（负向守门）<br>`_env_facts::test_ac_s7_15_base_dir_bound_to_state_workspace_dir`<br>`_env_facts::test_ac_s7_15_base_dir_falls_back_to_workspace_dir`（P-2 import 补齐守门）<br>`_probe_tool::test_ac_s7_15_cwd_anchored_to_base_dir`<br>`_probe_tool::test_ac_s7_15_cwd_outside_workspace_rejected`（越界 + 未启动进程） | — | ✅ 绿 |
| **AC-S7-16**（命门 1） | 只读保证 | `_probe_tool::test_ac_s7_16_must_reject_structured_and_no_process`（必拒 12 条 + `_run_subprocess` 零调用）<br>`_probe_tool::test_ac_s7_16_side_effect_probe_file_intact`（副作用探针，真实执行路径）<br>`_probe_tool::test_ac_s7_16_must_pass_readonly_commands`（必过集对照组）<br>`_probe_tool::test_ac_s7_16_reject_does_not_raise_on_malformed_command` | ✅ 已验红（§3 命门 1） | ✅ 绿 |
| **AC-S7-17** | 探测失败不污染主链路 | `_env_facts::test_ac_s7_17_probe_failure_does_not_pollute_main_path`（4 形态参数化：timeout / command_not_found / rejected / tool_error）<br>`_env_facts::test_ac_s7_17_no_gpu_is_a_valid_conclusion`<br>`_env_facts::test_ac_s7_17_full_node_run_with_failing_probe`（整节点跑） | — | ✅ 绿 |
| **AC-S7-18**（命门 2） | 防白探四环 | ①`_env_facts::test_ac_s7_18_ring1_map_result_emits_local_env_facts`<br>①`..._ring1_three_return_points_all_write[None|result1]`（三 return 点）<br>①`..._ring1_absent_probe_writes_no_key`（不造哨兵值）<br>②`..._ring2_planning_context_carries_env_facts`<br>③`..._ring3_analysis_notes_channel_never_reaches_planning`<br>④`..._ring4_model_actually_receives_env_facts`（含 SystemMessage 字节一致）<br>④`..._ring4_analysis_notes_never_enters_human_message` | ✅ 三次验红全做（§3 命门 2，**含假解法复刻**） | ✅ 绿 |
| **AC-S7-18 补充守门**（§15.6） | 字节幂等 / 单一真相源 / 渲染 / 兜底 | `_env_facts::test_digest_is_byte_idempotent_and_has_no_nondeterminism`<br>`_env_facts::test_probe_tool_name_is_single_source_of_truth`<br>`_env_facts::test_digest_render_rules_order_dedup_and_cap`<br>`_env_facts::test_digest_failure_fallbacks_never_block_node`（含 WARNING 非静默 + 无目标消息时不打噪声） | — | ✅ 绿 |
| **AC-S7-19** | 用户可见文案零内部标识符 | `_env_facts::test_ac_s7_19_blacklist_scanner_is_alive`（**金丝雀**）<br>`_env_facts::test_ac_s7_19_new_user_facing_text_has_no_internal_jargon`（**R-S7-30 真守门**）<br>`_env_facts::test_ac_s7_19_digest_does_not_leak_tool_or_node_name` | — | ✅ 绿（详见 §5.1） |
| **AC-S7-20** | Prompt Cache 与预算零退化 | `_env_facts::test_ac_s7_20_scout_prompt_body_byte_identical_across_papers`<br>`_env_facts::test_ac_s7_20_new_prompt_text_has_no_interpolation_traces`（负向）<br>`_env_facts::test_ac_s7_20_probe_section_is_outside_the_three_step_chain`<br>`_env_facts::test_ac_s7_20_round_budget_unchanged`<br>既有 `tests/test_sprint2_b2.py::test_acc_tool_set_composition_six_tools`（唯一真守门，前程已同步） | — | ✅ 绿 |
| **AC-S7-21**（命门 3） | 清单形态守门 | `_probe_tool::test_ac_s7_21_command_list_shape_is_frozen`<br>`_probe_tool::test_ac_s7_21_description_matches_command_list` | ✅ 两次验红（自由参数 / 解释器形态，§3 命门 3） | ✅ 绿 |
| **AC-S7-22** | 双用途边界互不削弱 | `_probe_tool::test_ac_s7_22_positive_coding_run_command_still_executes_interpreter`（**正向**）<br>`_probe_tool::test_ac_s7_22_negative_probe_rejects_same_two_commands`（**负向 + 底层未调用**）<br>——**同文件相邻两条** | ✅ 随命门 1 / 3-b 一起验红 | ✅ 绿 |
| **AC-S7-23** | 超时独立且真的传下去 | `_probe_tool::test_ac_s7_23_timeout_constant_value_and_magnitude`（30 / int / 30<120<1800）<br>`_probe_tool::test_ac_s7_23_timeout_actually_passed_down`（**实参捕获**）<br>`_probe_tool::test_ac_s7_23_config_has_no_probe_constants`（负向） | ✅ 随命门 4-b 一起验红 | ✅ 绿 |
| **AC-S7-24** | 工具 schema 零任务级动态值 | `_probe_tool::test_ac_s7_24_two_factories_byte_identical_schema`（双工厂 name/description/args_schema 字节比对）<br>`_probe_tool::test_ac_s7_24_description_has_no_task_level_dynamic_values`（无路径串 / 无 `{}` / 清单↔描述逐条一致） | ✅ 随命门 3-a 一起验红 | ✅ 绿 |
| **AC-S7-25** | 探测节制可观测 | **本程未覆盖**——须真机端到端跑（档 B），耗 deepxiv 配额 + 真实 LLM | — | ⏸ **延后不注销**（CP-4.9-3 保持 `[ ]`，待 Maria 授权） |
| **AC-S7-26**（命门 4） | 返回恒不触发 8000 截断 | `_probe_tool::test_ac_s7_26_worst_case_two_way_saturation_never_truncated`（最坏两路满载 + 双阶段解析 6 键）<br>档 A 真机对照（§4 CP-4.9-2） | ✅ 两次验红（调大常量 / 改传 1MiB，§3 命门 4） | ✅ 绿 |
| 序列化形态（CP-4.2-8） | BUG-S1-02 规避 | `_probe_tool::test_cp_4_2_8_serialization_form`（合法 JSON / sort_keys / `command` 规范化回显对多空白免疫）<br>`_probe_tool::test_cp_4_2_8_reject_json_is_ensure_ascii_false` | — | ✅ 绿 |

### 5.1 AC-S7-19 的落实细节（R-S7-30 / §31 P-3）

既有守门 `tests/test_e2e2_message_guard.py` **只扫 `make_node_error(...)` 的 message 实参**，而 S7-06 按 AC-S7-17 **零新增该调用** ⇒ 只把 `resource_scout` 留在 `_GUARDED_MODULES` 里等于**零覆盖却 passed**。本程按 dev-plan 要求新增独立断言：

- **复用同一份口径**：`from tests.test_e2e2_message_guard import _BLACKLIST, _hits`（大小写不敏感 + 词边界，不另写一份黑名单）；
- **扫描对象**（实测共 **19 条**，全部实跑产出、非手抄）：`_digest_env_probe` 产出 1 条 + `_reject_with_list()` 的 error 文案 1 条 + `allowed_commands` 15 条 + `_reject("命令解析失败: …")` 实调 1 条 + `_reject("工作目录越界: …")` 实调 1 条；
- **"扫不到即报红"保险**：断言 `targets` 非空、条数 `>= 5`、每条非空串；
- **扫描器活性金丝雀**：`test_ac_s7_19_blacklist_scanner_is_alive` 先证明 `_hits` 对 `from_scratch` / `resource_scout` / `ReAct` 都能命中、对通俗中文「已降级为从零实现」不误报——**防"扫描器坏了导致零命中"的假绿**；
- **额外一层**：`test_ac_s7_19_digest_does_not_leak_tool_or_node_name` 断言 digest 不含 `probe_environment` / `resource_scout` / 三个策略枚举 / `subprocess start failed` / `ToolMessage`。

---

## 6. 失败排查

**新增两文件与全量回归零失败。** 本段记录**验红过程中的一次操作事故**（属执行过程，非代码缺陷），如实留档：

| 项 | 内容 |
|---|---|
| 事故 | 验红 2-a 还原时误用 `git checkout core/nodes/planning.py`。该文件的 S7-06 改动**尚未 commit**，`git checkout` 从 index 还原 = **把前两程交付的 planning.py 改动一并抹掉** |
| 发现方式 | 还原后立刻跑 `_env_facts` 全文件 → `3 failed / 26 passed`（②③④ 相关红），与"应当全绿"不符 → `grep -n "local_env_facts" core/nodes/planning.py` **零命中**，确认被抹 |
| 回滚 | `git apply --include=core/nodes/planning.py /tmp/s706/prod_diff_baseline.patch`（开工时抓的生产 diff 快照），随后 `git diff` 与快照 **md5 逐字节相同**（`c6ef7bd0fe2a9550cea6a7d958716a10`），29 passed 恢复 |
| 后续纪律 | 其余全部验红改用**文件级备份 + `cp` 还原 + md5 核对**，不再用 `git checkout` 碰未提交的文件 |
| 影响 | **零**——生产代码最终与前两程交付逐字节一致（见 §8） |

---

## 7. 回归实测数字

| 轮次 | 命令 | 结果 |
|---|---|---|
| run 1 | `.venv/bin/pytest -q -p no:cacheprovider --color=no -m "not e2e"` | **2103 passed / 0 failed / 25 skipped / 46 deselected / 3 warnings / 139.99s** |
| run 2 | 同上 | **2103 passed / 0 failed / 25 skipped / 46 deselected / 3 warnings / 137.42s** |

**账目闭合**：基线 **2056**（第二程收口数）+ 新增 **47**（18 + 29）= **2103** ✅ 精确闭合，无隐性增减。

**skipped 25 条**：为既有 skip（e2e 之外的条件跳过），与基线一致，未新增。

**pre-existing flaky（P-9）单列**：`tests/test_plan_review_e2e.py::test_e2e_code_only` 本次两轮均通过。它的失败与 S7-06 无因果（主控已 `git stash` 对照实验证明，且 TODO:443 记载 2026-07-15 在更早基线 `91f3753` 也复现过），属 UI e2e harness 等待策略问题，**本批不处理**。

**run 2 实测**：`2103 passed, 25 skipped, 46 deselected, 3 warnings in 137.42s`，`grep -c "^FAILED"` = **0**（日志 `/tmp/s706/regression_run2.log`）。两轮通过数完全一致，P-9 flaky 用例两轮均绿。

---

## 8. 生产代码与前两程交付完全一致的证据

| 文件 | 校验方式 | 结果 |
|---|---|---|
| `core/nodes/planning.py` + `core/nodes/resource_scout.py` + `core/state.py` | 开工时抓 `git diff` 快照 `/tmp/s706/prod_diff_baseline.patch`（md5 `c6ef7bd0fe2a9550cea6a7d958716a10`，311 行）；收口时重抓并 `diff` | **逐字节相同**（`PROD DIFF IDENTICAL TO BASELINE`），diffstat 恒为 `planning.py +7 / resource_scout.py +154−7 / state.py +9` |
| `core/tools/env_probe_tool.py`（untracked） | 开工时 `cp` 备份 + md5 | **`8587ea451ad803ac3d27a67f78233be8`**，收口时一致 |
| `core/tools/run_command_tool.py` / `config.py` / `core/nodes/_repo_scoring.py` | `git status --porcelain` | **未出现在改动列表中**（零改动红线成立） |
| 本程新增文件 | `git status --porcelain` | 仅 `?? tests/test_sprint7_s706_env_facts.py`、`?? tests/test_sprint7_s706_probe_tool.py` 两个**测试**文件 |

---

## 9. 与文档的出入（如实报，不自行改设计）

### 出入 1（重要，建议登记为 P-10）：AC-S7-18 验红 2-a 的"②④必红"口径与 ② 的设计自相矛盾

- **文档 A**：dev-plan L1345 与 architecture §15.6 L961 均写「注掉 `build_context` lambda 第 6 实参 → **②④必红**、①③仍绿」。
- **文档 B**：dev-plan L1304 写「**④端到端环守的就是这一行**」；CP-4.6-1 与 architecture §15.6 ② 行明文规定 ② 的做法是「把 ① 的 update 合进 state，**调 `_format_planning_context(...)`**」。
- **实测**：注掉 lambda 第 6 实参后 **只有 ④ 红**（`1 failed / 28 passed`）。
- **原因**：② 按设计**直接调 formatter**，绕过了 lambda，逻辑上不可能观测到 lambda 层的漏传。若强行让 ② 也红，只能把 ② 改成走 wrapper——那 ② 就和 ④ 重复了，**反而丢掉四环"哪一环红 → 断在哪"的定位价值**（①管 map、②管 formatter、④管接线）。
- **判定**：**属文档 A 的口径笔误，防线未受损**——为这一失效形态设计的环（④）确实红了。**本程未改设计、未改断言、未放宽任何一条**，仅如实留档。请主控裁定是否作为 P-10 回填 §31。

### 出入 2（轻微，实现口径）：AC-S7-22 正向用 `sys.executable` 而非字面 `python`

- PRD AC-S7-22 原文写「coding 侧 `run_command` 执行 `python -c "print(1)"` 与 `python -m py_compile <file>` 仍成功」。
- 本机**默认 PATH 下裸 `python` 是 Python 2.7.18**（`python3` 是 3.6.8，均非项目 venv）。用字面 `python` 会让这条正向断言的成败取决于宿主机上恰好装着哪个 py2/py3，属环境耦合。
- **实现取 `sys.executable`**：正向断言写 `{sys.executable} -c "print(1)"` 与 `{sys.executable} -m py_compile <file>`，语义（"解释器执行形态在 coding 侧仍然可用"）**完全等价且更稳**；负向侧**两种写法都断**（字面 `python …` 与 `{sys.executable} …` 各两条，共 4 条），确保"探测侧禁掉的正是 coding 侧允许的那两条形态"这层对照不打折。

### 出入 3（观测事实，非缺陷）：真机 `pip` 探测依赖宿主 PATH

见 §4「真机新发现」。默认 PATH 下 `pip` 不可解析 ⇒「已装包」这一项探不到。清单用裸名是刻意设计（R-S7-17），本批不扩围；如需覆盖，属"单点加清单条目、机制不动"（R-S7-13 回退路径）。

---

## 10. 已知限制清单（交测试工程师 handoff）

| 编号 | 限制 | 当前处置 |
|---|---|---|
| **R-S7-16** | **清单漂移**——后人往 `_PROBE_COMMANDS` 加带自由参数条目会同时重新打开五类禁止项 | **唯一机制守门 = AC-S7-21**（本程已两次验红，且实测证明加一条 `python -c print(1)` 会连带打红 AC-S7-16/22）。余下靠**人工评审**，无机制可替 |
| **R-S7-17** | 宿主 PATH 被污染 → 清单裸名解析到恶意二进制 | 评估为"等价于宿主已陷"，不做 `shutil.which` 加固（安全剧场）。**本程真机实测印证 PATH 确实决定解析结果**（见 §4），风险描述准确 |
| **R-S7-18** | `_run_subprocess` 未设 `stdin=DEVNULL`，将来清单若加入读 stdin 的命令会挂到超时 | 当前 15 条均不读 stdin，无实害；AC-S7-21 的"无解释器 / 包装器形态"守门顺带压住这一类。**本程副作用探针刻意避开 `tee`**（它读 stdin，验红时会挂满 30s），改用 `cp /dev/null` |
| **R-S7-20** | 规划 LLM 拿到本机事实却不用 | **不设硬约束**（S7-06 只负责"送达"）。AC-S7-18 四环只验送达、不验消费方式 |
| **R-S7-24** | 既有 `_format_planning_context:341` 把 `resource_strategy` 内部枚举送进规划上下文 | 既有留档，**本批不扩围**；本批新增内容全为通俗中文 + 字面 shell 命令，零新增英文枚举（AC-S7-19 已守） |
| **P-8** | AC-S7-26「恒不触发 8000 截断」**只对真实命令输出形态成立**（纯换行病态构造会恒红） | 已写死进 `_freeze_shaped_bytes()` 的 docstring 纪律；真机密度基准 1/18.1、测试用 1/14（保守）、病态阈值 1/2.7。**验红能力经两次验红确认未被削弱** |
| **P-9** | `tests/test_plan_review_e2e.py::test_e2e_code_only` 既有 flaky（Playwright / shadcn iframe 15s 超时） | 与 S7-06 无因果（主控 stash 对照实验）。本程两轮回归均绿。建议单开 TODO 修 harness 等待（提超时 / 改 `wait_for_selector`），**不要在 S7-06 批内顺手改** |
| **AC-S7-25** | 探测节制（`probe_environment` ToolMessage ≤ 5 + 未 force_finish / 未 degraded / 策略未被改写）**无 mock 替代方案** | **档 B，须 Maria 明确授权具体动作**（耗 deepxiv 日配额 + 真实 LLM）。CP-4.9-3 保持 `[ ]`，按 §25.4 **延后不注销**。**本条缺席 = Q-S7-12「只做 prompt 措辞、不加计数器」这一裁决暂时无法被证伪**；若日后观测超标，按 R-S7-27 加约 4 行闭包计数器（工厂每次节点调用重建，计数天然按任务重置） |
| 一般性 | mock 只能证"该拒的拒了"，证不了"该探到的探到了" | 由**档 A 真机探测**补齐（§4）：15 条清单命令全部在本机真跑，拿到 GPU / CUDA / CPU / 内存 / 磁盘 / Python / 已装包 / 工具链八类事实 |

---

## 11. 后续动作

1. **CP-4.9-3（档 B / AC-S7-25）待 Maria 明确授权具体动作**后执行，建议**合并进既有真跑授权窗口**（省配额范式）。授权前该条在 dev-plan 中保持 `[ ]`。
2. **出入 1** 请主控裁定是否作为 **P-10** 回填 dev-plan §31（口径笔误订正，不改设计）。
3. **出入 3** 的"真机 `pip` 依赖 PATH"建议随下次触碰清单时评估（加 `python3 -m pip list --format=freeze` 属单点、机制不动），本批不做。
4. **P-9 的 UI e2e harness 等待策略**建议单开 TODO，不在 S7-06 批内改。
5. 下次跑测试触发条件：Maria 授权档 B → 跑一次真机端到端并补 AC-S7-25 计数；或后续有人改动 `_PROBE_COMMANDS` / `_PROBE_OUTPUT_MAX_BYTES` / `planning.py` lambda → 四道命门应立即打红（这正是它们存在的意义）。
