# 解决方案：命令串分词在 Windows 上吃掉反斜杠（A 类）

> 立案：2026-10-21。**状态：✅ 已落地（2026-10-21，同日）。**
> 落地内容 = 新增 `core/shell_tokens.py`（跨平台分词单点）+ 五处改指向 + A2 编码约定 +
> 28 条新回归门（`tests/test_sprint8_shell_tokens.py`）+ 两处测试侧平台假设修正 + 白名单缩表。
> 实测：全量红 **53 → 41**（**12 条真因被修掉**），mypy 28 源文件零错误。
> 触发来源：`T-S8-2-12` 收口时复核「53 条平台红到底是不是项目问题」，查到这一条**是真产品 bug**。
> 相关：`docs/sprint8/platform-red-whitelist.md`（白名单，已缩表 53 → 41，见其 §5）。

---

## 0. 一句话

**产品用 POSIX 语义（`shlex.split`）解析命令串，而 POSIX 语义把反斜杠当转义符**
⇒ 在 Windows 上，任何**带反斜杠路径**的命令都被拆坏，子进程以 `WinError 2` 失败，
而报错与真正的病因（路径被吃掉）看不出关系。

实测一条：

```
输入  D:\study\venv\Scripts\python.exe -c "print(1)"
实收  ['D:studyvenvScriptspython.exe', '-c', 'print(1)']
报错  Popen 启动失败 cmd=['D:studyauto_reproduction.venvScriptspython.exe', ...]: [WinError 2]
```

## 1. 根因：同一段解析逻辑被复制了 5 份

全仓 `shlex` 的**分词**调用点（`shlex.quote` 不算）：

| # | 位置 | 作用 | 现状后果 |
|---|---|---|---|
| 1 | `core/tools/run_command_tool.py:94` | coding 侧 `run_command`：把命令串拆成 argv 后直跑 | 🔴 **已经在红**（6 条用例）。带 Windows 路径的 smoke 命令必失败 |
| 2 | `core/nodes/execution.py:547`（`_split_top_level`） | execution 侧把 plan 步骤的 `command` 拆成子命令 argv | 🔴 **静默同类 bug**：`python D:\x\train.py` 会被拆成 `python` + `D:xtrain.py`，报"文件不存在"，**看不出是分词问题** |
| 3 | `core/plan_checks.py:209`（`_split_top_level_argv`） | 喂 `has_unsupported_shell_syntax` / `is_inline_code_write` | 🟡 让元字符判定与"内联代码载荷长度"判定拿到被吃过的 token ⇒ 判定可能失真 |
| 4 | `core/tools/env_probe_tool.py:86` | 导入期把 `_PROBE_COMMANDS` 算成 allowlist argv | 🟢 固定 ASCII 清单，暂不受影响（但契约上同源） |
| 5 | `core/tools/env_probe_tool.py:197` | 用户命令 → argv 后与 allowlist 精确比对 | 🟡 失败方向是 fail-closed（不会误放行），但合法命令会因为被吃而进不了清单 |

🔴 **#2 是最值得警惕的一格**：它没有红用例守着（现有测试的 plan 步骤都是 `python train.py` 这类无反斜杠命令），
**真实复现跑一旦在命令里写绝对路径就会静默走偏**，而症状是"文件不存在 / 模块找不到"，会把人引去查代码而不是查分词。

## 2. 触发面（谁写命令、什么时候踩）

| 触发形态 | 例子 | 结果 |
|---|---|---|
| Windows 绝对路径（最常见） | `python D:\论文\code\train.py` | 🔴 路径被吃 |
| 参数里带路径 | `python prep.py --data D:\corpus\v1` | 🔴 同上 |
| `=` 连着路径 | `--cfg=C:\cfg\a.yaml` | 🔴 同上 |
| UNC 路径 | `python \\server\share\run.py` | 🔴 同上 |
| 中文目录 | `python D:\数据\train.py` | 🔴 同上（吃反斜杠，不是编码问题） |
| 引号内带空格的路径 | `python "C:\Program Files\x.py"` | 🟢 恰好没事（引号内反斜杠不被当转义——**纯属运气**，别当契约） |

⇒ 只要 agent 或用户在 Windows 上写了一次绝对路径，就必然失败。

## 3. 三方案对比（探针实测矩阵）

探针脚本跑 15 个代表性输入 × 3 种实现（`≤` = 行为是否为本方案要求）：

| 输入 | 现状 `shlex.split` | 方案 A `escape=""` 恒定 | 方案 B 按平台（Windows 空 / POSIX `\`） |
|---|---|---|---|
| `D:\study\venv\Scripts\python.exe -c "print(1)"` | ❌ `D:studyvenvScriptspython.exe` | ✅ 原样 | ✅ 原样 |
| `D:\数据\train.py --epochs 3` | ❌ `D:数据train.py` | ✅ 原样 | ✅ 原样 |
| `python prep.py --data D:\corpus\v1 --out out\r.json` | ❌ 两处路径都被吃 | ✅ 原样 | ✅ 原样 |
| `python x.py --cfg=C:\cfg\a.yaml` | ❌ `--cfg=C:cfga.yaml` | ✅ 原样 | ✅ 原样 |
| `python "C:\Program Files\x.py"` | ✅ | ✅ | ✅ |
| `python my\ file.py`（POSIX 转义空格） | ✅ `my file.py` | ⚠️ `['my\','file.py']` | Windows ⚠️ / POSIX ✅ |
| `python -c "print(1>2)"`（引号内元字符） | ✅ 不误判 | ✅ 不误判 | ✅ 不误判 |
| `python train.py > out.log`（裸重定向） | ✅ 判出 | ✅ 判出 | ✅ 判出 |
| `python a.py 2>&1`（描述符合并） | ✅ 判出 | ✅ 判出 | ✅ 判出 |
| `python a.py && python b.py`（顶层 `&&`） | ✅ 拆成 2 条 | ✅ 同 | ✅ 同 |
| `python -c "a; b"`（引号内 `;`） | ✅ 不拆 | ✅ 不拆 | ✅ 不拆 |
| `python "abc`（未闭合引号） | ✅ `ValueError` | ✅ `ValueError` | ✅ `ValueError` |
| 空串 / 多空白 | ✅ | ✅ | ✅ |

**结论：三种实现在「引号语义 / 元字符识别 / 连接符拆分 / 未闭合引号降级」上完全一致**，
差异只有两条：
1. 反斜杠是不是转义符（A/B 都说"不是"，现状说是）；
2. **A 与 B 的唯一分歧**：POSIX 上的 `\ `（转义空格）。

## 4. 推荐：方案 A（posix 词法 + `escape=""` 恒定）

```python
def split_argv(command: str) -> list[str]:
    """按 POSIX 引号规则切词，但**反斜杠是普通字符**（不做转义）。"""
    lex = shlex.shlex(command, posix=True)
    lex.whitespace_split = True   # 只按空白切（等价 shlex.split 的主体行为）
    lex.commenters = ""           # 不把 # 当注释（命令里 # 可能是参数）
    lex.escape = ""               # 🔴 关键：反斜杠不是转义符
    return list(lex)
```

**为什么不选 B（按平台分支）**：

- B 会让**同一条命令在两个平台上切出不同 argv**：`my\ file.py` 在 Linux 是 1 个 token、在 Windows 是 2 个。
  这直接破坏"复现可对账"——本项目最核心的价值是**同一条计划换台机器还能跑出同一件事**。
- A 的失败方向是**明确失败**：谁真写了 `my\ file.py`，会得到"找不到文件"，一眼能改；
  B 的失败方向是"在 A 机器能跑、B 机器不能"，最难排查。
- 产品从不用 shell（全程 argv 直跑），POSIX 的 `\ ` 转义本来就不是它该支持的语义；
  需要含空格的参数请用引号（**这条要写进工具描述与提示词**）。

**为什么不选 `shlex.split(s, posix=False)`**：非 posix 模式**保留引号**，
`python -c "print(1>2)"` 会切出 `'"print(1>2)"'`（带引号）⇒ 程序收到带引号的参数，行为错。

**顺带修好的两件事**（同一次改动，零额外成本）：
- `--cfg=C:\cfg\a.yaml`、UNC `\\server\share`、中文目录一律原样保留；
- 引号内反斜杠从"碰巧没事"变成**契约保证**。

## 5. 实施清单（建议 4 个提交，逐个小步回滚）

### 5.1 新增单点：`core/shell_tokens.py`（~50 行，零依赖纯函数）

```
split_argv(command)              -> list[str]        # 抛 ValueError（未闭合引号）
split_top_level(command)         -> list[tuple[list[str], str]]
                                    # [(argv, connector)]，connector ∈ {"", "&&", ";"}
                                    # 解析失败退化为 whitespace split 单条（与现状同款）
```
零 import（除 `shlex`）⇒ `plan_checks` 可以放心 import 它，**不会造成
`execution → plan_checks → execution` 循环**（这正是 `plan_checks:203` 当初"刻意不 import"的原因，
那条 docstring 需同步订正为"已抽到零依赖模块，故 import 它是安全的"）。

### 5.2 五处改指向（每处 1~3 行）

| 位置 | 改成 |
|---|---|
| `run_command_tool.py:94` | `argv = shell_tokens.split_argv(command)` |
| `execution.py:547` | `_split_top_level` 整体委托 `shell_tokens.split_top_level`（保留函数名与返回契约，冻结表里没有它） |
| `plan_checks.py:209` | `_split_top_level_argv` 委托（保留公开函数与 docstring 语义） |
| `env_probe_tool.py:86 / :197` | `shell_tokens.split_argv` |

⚠ **不许只改 #1**：那会留下 #2 这颗静默炸雷（本次就是先只改 #1 做验证，
实测 6 条转绿，但 #2 的同类 bug 仍在）。

### 5.3 提示词/工具描述订正（3 处短句）

- `run_command` 的 docstring、`execution` 主体里"命令解析"相关段落、
  `UNSUPPORTED_SHELL_SYNTAX_MESSAGE` 附近注释：
  加一句 **"路径里的反斜杠按原样传递，不做转义；参数含空格请用引号包起来"**。
- ⚠ 改 `execution` 主体 ⇒ **动到 prompt 字节基线**（两道哈希门：
  `test_sprint5_t14_execution_prompt.py` / `test_sprint7_s710_exec_locality.py`，现值
  `60549c438adb3714` / 3767）。**要么不改主体、只改工具 docstring，要么按三件套流程换发哈希。**
  ⇒ **建议本方案只改工具 docstring**（不进 prompt 主体），prompt 那句留到批次 3 一并改。

## 6. 必须守住的不变量（改完逐条复核）

| 不变量 | 复核方式 |
|---|---|
| 裸 `>` / `>>` / `2>&1` / `|` / `&` 仍被拒 | 复用 `_UNSUPPORTED_SHELL_TOKENS` 精确相等语义，加负向用例 |
| 顶层 `&&` / `;` 仍是连接符（不拒、要拆） | 现有 `test_sprint3_shell_parse` / s712 用例 |
| 引号内 `>` / `;` / `&&` **不拆不拒** | 现有用例 + 新增 `"print(1>2)"` |
| 未闭合引号仍降级为 whitespace split | 现有 fallback，加一条断言 |
| 冻结表 5 行哈希不变 | `tests/test_sprint8_exec_contract.py::test_f1_*` —— 5 个函数**都不在本次改动面**，跑一遍确认 |
| `mypy` 零错误 | 清缓存后跑 |
| 生产侧只碰 4 个文件（+1 新文件） | `git status --porcelain` |

## 7. 对现有测试的影响（**已实测**，不是估算）

验证方式：只把 `run_command_tool.py` 改成方案 A（其余不动），跑相关 4 个文件，然后
`Copy-Item` 还原并核对 `sha256` 与改动前**逐字节相同**（`0175D092…6E2FB1`，7628 bytes）。

| 用例 | 改前 | 改后 | 说明 |
|---|---|---|---|
| `test_sprint4_c1.py`（5 条） | 5 红 | **1 红** | 4 条转绿 = 产品 bug 被修好 |
| `test_sprint7_s712_shell_metachars.py::test_cp_8_1_5_run_command_does_not_block_legal_command` | 红 | **绿** | 同上 |
| `test_sprint7_s706_probe_tool.py::test_ac_s7_22_...executes_interpreter` | 红 | **绿** | 同上 |
| `test_sprint7_s706_probe_tool.py::test_ac_s7_16_must_pass_readonly_commands` | 红 | 红 | **不同根因**：`python3` 本机不存在（见 A2/A3） |
| `test_sprint7_s706_probe_tool.py::test_cp_4_2_8_serialization_form` | 红 | 红 | **不同根因**：探针跑 `uname -srm`，Windows 没有 `uname` |
| `test_sprint4_c1.py::test_cp_c1_1_ensure_ascii_false_non_ascii_verbatim` | 红 | 红 | **不同根因**：子进程中文输出编码（见 A2） |
| `test_sprint3_shell_parse.py::test_injection_semicolon_rm_not_shell_interpreted` | 红 | 红 | **需改期望**：该用例把"反斜杠被吃掉后的 argv"当正确值（POSIX 假设），要改成平台无关断言 |

⇒ **6 条转绿**（`c1` ×4 + `t712` ×1 + `t706` ×1）。
⇒ 落实后 **`platform-red-whitelist.md` 必须缩 6 行**（白名单只许缩小），条数 53 → 47。

## 8. 新增测试（建议新文件 `tests/test_sprint8_shell_tokens.py`）

表驱动，**期望值与平台无关**（这正是方案 A 的卖点，用例里不出现 `os.name` 分支）：

1. Windows 绝对路径 / 中文路径 / `=` 连路径 / UNC —— 逐格断言 `split_argv` 原样保留；
2. `python -c "print(1>2)"` —— 1 个 token 且 `has_unsupported_shell_syntax` 为 False；
3. 裸 `>` / `2>&1` / `|` / `&` —— `has_unsupported_shell_syntax` 为 True；
4. `&&` / `;` 连接符 —— `split_top_level` 的 `(argv, connector)` 逐格断言；
5. 引号内 `;` / `&&` 不拆；
6. 未闭合引号 —— `split_argv` 抛 `ValueError`、`split_top_level` 降级单条；
7. 空串 / 纯空白 —— 空列表；
8. 🔴 **静态断言**：`inspect.getsource(core.shell_tokens)` 里**不出现 `os.name` / `platform`**
   —— 把"跨平台一致"从一句承诺变成一条会红的断言（防止后人"顺手"加平台分支）。

**验红（三条，缺一不算）**：
- 把 `lex.escape = ""` 删掉 ⇒ 用例 1 全红；
- 把 `posix=True` 改成 `False` ⇒ 用例 2/5 红（引号未剥离）；
- 给实现加一个 `os.name == "nt"` 分支 ⇒ 用例 8 红。

## 9. 风险与回滚

| 风险 | 处置 |
|---|---|
| POSIX 上 `\ ` 转义失效 | 契约明写"用引号"；若日后确需，**只在 POSIX** 打开转义会重新引入跨平台分歧 ⇒ 建议永不做，改文档 |
| 某个历史 plan 依赖 `\ ` 转义 | 失败方向是"找不到文件"，一眼可改；本轮无存量 plan（`workspaces/` 里无 `\ ` 形态，已 grep） |
| 动到 prompt 基线 | 本方案**不动 prompt 主体**，只改工具 docstring ⇒ 两道哈希门不受影响 |
| 冻结表 | 5 个冻结函数不在改动面；落地后跑 `test_f1` 复核 |
| 回滚 | 每个提交独立（新模块 / 5 处委托 / 测试 / 白名单），`git revert` 任一即可 |

## 10. 建议的提交切分

1. `feat(core): 新增 core/shell_tokens.py（跨平台命令分词单点）+ 单测 + 验红`;
2. `fix(core): 五处 shlex 分词改指向单点（Windows 路径不再被吃）`;
3. `test: 修 test_sprint3_shell_parse 的 POSIX 期望 + 六条转绿复核`;
4. `docs: 白名单缩 6 行（53 → 47）+ 本方案留档 + §15 勘误一条`。

## 11. 顺带发现的两个**不同根因**问题（不要混进本方案）

### A2 · 子进程中文输出变乱码（建议单独立项）
`test_sprint4_c1::test_cp_c1_1_ensure_ascii_false_non_ascii_verbatim` 实测：

```
子进程 print('中文输出') → stdout_tail = "�������"   （GBK 字节被当 UTF-8 解码）
```
**根因已核到两行**（不是猜测）：
- `sandbox/local_venv.py:352/355` 把子进程字节**写死按 UTF-8 解码**：`raw.decode("utf-8", errors="replace")`；
- 而 `_build_sandbox_env` 的白名单**故意不放 `PYTHON*`**（`docs/TODO.md:577` 逐字：
  "剔除一切凭证且特意不放 `PYTHON*`（`PYTHONPATH` 继承会反向破坏隔离，有专门测试）"），
  全仓 `core/` + `config.py` 里 `PYTHONUTF8` / `PYTHONIOENCODING` **零命中**。
  ⇒ 子进程按 Windows 控制台代码页（cp936）写，父侧按 UTF-8 读 ⇒ 双向不成对，出乱码。

**修复方向（推荐 i）**：给沙箱环境**只加** `PYTHONIOENCODING=utf-8`（必要时加 `PYTHONUTF8=1`）——
这两个变量**不涉及导入路径**，不会破坏 `PYTHONPATH` 那条隔离红线；解码侧保持 UTF-8 不变。
⚠ 连带：`tests/test_sandbox_env_isolation.py` 的白名单断言需同步（那两条本来就在平台白名单里）。
🔴 **影响用户可见面**：中文数据集名 / 中文报错会以乱码进 `logs`、`errors`、报告。
严重度**高于 A 类**（A 是明确失败，这个是把错误信息本身毁掉，还不会被当成失败）。**建议优先级 P1**。

### A3 · 探针清单含 POSIX-only 命令（测试侧）
`test_cp_4_2_8_serialization_form` 跑 `uname -srm`，Windows 没有该命令（`WinError 2`）。
处置二选一：给 `_PROBE_COMMANDS` 做平台分组（产品侧），或让用例按平台挑（测试侧）。

## 12. 登记建议（TODO 活区，供主控粘贴；按 TODO 规范「各代理不新增」，本文件不直接改 TODO）

```
- [ ] [2026-10-21] @全栈开发代理 🔴 A 类：`shlex.split` 的 POSIX 语义吃掉 Windows 路径反斜杠，
      五处同款（`run_command_tool:94` / `execution:547` / `plan_checks:209` / `env_probe_tool:86,197`）⇒
      方案见 `docs/solutions/2026-10-21_windows-command-tokenization.md`；落地后白名单缩 6 行。
- [ ] [2026-10-21] @全栈开发代理 🔴 A2：子进程中文输出乱码（`_build_sandbox_env` 过滤掉
      `PYTHONUTF8`/`PYTHONIOENCODING`）⇒ 中文日志/报错进报告即毁容，建议 P1。
```
