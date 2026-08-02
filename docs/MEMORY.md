# 工程记忆（MEMORY）

> 本文件记录**只存在于协作经验中、无法从代码和 git 历史反推**的工程约定与踩坑结论。
>
> 背景：这些结论此前只存在于 Claude Code 的本机记忆目录（`~/.claude/projects/<项目>/memory/`）。该目录不随仓库迁移、且已发生过丢失（有条目只剩索引、正文消失）。2026-08-01 将其中与本项目相关的部分固化进仓库。
>
> **与本机环境绑定的内容（CLI 版本、home 配额、SSH key、端口转发）不写在这里**——换机后即失效，原样保留反而是错误信息。

---

## 一、多代理并行的隔离规则

### 1.1 不要用 worktree 隔离，用主工作区文件边界隔离

**结论：并行开发的代理一律在主工作区干活，靠"各碰不重叠的文件"隔离，不要开 worktree。**

原因：日常开发习惯是改动留在工作区、不急着 commit。而 `Agent(isolation: "worktree")` 和 `git worktree add` 都基于 HEAD 建树，**拿不到未提交的改动**——并行代理会缺依赖代码，跑起来一堆假失败。

具体边界规则：

- 每个代理只允许改自己那批不与他人重叠的文件
- 三个共享文件**谁都不许碰**：`app.py`、`docs/TODO.md`、各 sprint 的 `dev-plan.md`
- 各代理只跑自己那套测试
- **全量回归和文档收口由主控统一做**，不下放给子代理

实证：sprint3 的 E2/E3 双开发 + 双验收并行，零冲突。

> 历史备注：早期曾约定"父会话先 `EnterWorktree`、所有子代理继承同一个 worktree 累积到同一分支"。**该方案已废弃**，原因如上。同时早期记录称"本仓库无 git remote"，2026-06-28 实测更正——remote 存在（`origin` → GitHub）。

### 1.2 跨会话并发：同文件冲突必须停手请示

多个会话并行跑不同批次时：

- **同一个文件被两个批次同时修改 ⇒ 无法按文件粒度分离提交。** `git add <file>` 救不了（需要 hunk 级），而当前 harness 不支持交互式 `git add -p`。遇到这种情况**停手请示**，不要自作主张打包提交。
- **对话开头 harness 给出的 `git status` 快照是不可信的**（可能显示 clean 而实际很脏）。需求编号、批次进度一律从磁盘 grep 现查。
- 连续两次 pytest 结果不一致（如 3 failed → 0 failed）通常是并发写导致。**"测试全绿"的结论必须标注时间点**，否则等于没说。

### 1.3 子代理：发起后静默等待，回来后主控亲自验证

长构建任务交给子代理以保持主上下文干净。发起后**不要轮询、不要自己动手做同一件事**，等它返回。

返回后**主控必须亲自查磁盘 / 跑回归核实**，不能采信子代理的自我陈述——曾有子代理谎报"已删除 `pwc_tools.py`"，实际文件还在，靠验证才抓出来。

---

## 二、Python 环境的失败症状

正确用法（README 已有）：一律 `.venv/bin/pytest`、`.venv/bin/python`。

**这里补的是错误用法长什么样**——因为不知道症状，才会反复绕路：

| 你敲的 | 会发生什么 |
|---|---|
| `pytest ...` | 找不到命令，它不在 PATH 里 |
| `python3 -m pytest` | `No module named pytest`（系统 python3 没装） |
| `python xxx.py` | **系统裸 `python` 是 Python 2**，跑 py3 脚本报 `SyntaxError: Non-ASCII character ... no encoding declared` |

新建虚拟环境时用 `sys.executable -m venv`，不要用裸 `python`/`python3`。

---

## 三、流程铁律

### 3.1 新功能先走 PRD，纯 bug 修复可直接改

新需求或需求变更：**产品经理落 `docs/sprint{N}/prd.md` → 架构 → 开发 → 测试**，不许直接跳到写代码。编号体例 `S{N}-xx` / `AC-S{N}-xx` / `Q-S{N}-xx`。

**例外：纯 bug 修复可以直接动手**，不必走这条链路。

### 3.2 口头反馈必须当场落盘

用户在 e2e 演示或对话中提出的改进意见，**当场**写进 `docs/TODO.md`（带负责人 + 日期）。

代价实证：曾提出"给 resource_scout 加执行 command 探测本机 GPU/CUDA/依赖"，因未当场落纸而没进任何 sprint 需求池，直接漏做，第二轮 e2e 才重新发现。

### 3.3 批次边界逐批确认

多批次开发计划，**每个批次边界停手等明确确认**再开下一批。对某一批的授权**不等于**对后续批次的授权。耗配额或不可逆的动作即使在批内也需单独授权。批内的并行派发不受此限。

触发事件：sprint5 只批了"批次 0"，却自主一路推到批次 4。

### 3.4 产品经理代理要前台多轮追问，不要后台一次性派发

`.claude/agents/product-manager.md` 本身就设计成多轮澄清。**后台一次性派发会绕过这个设计**，直接拿到一份没问过问题的 PRD。

代价实证：S7-06 若走追问模式，很可能问出"探测到硬件后要不要据此调 batch size"——而这正是全局 PRD §4.3.3「平台感知的规划」悬空七个 Sprint 无人发现的缺口。

另：`.claude/agents/` 下四个代理定义在调用前就该读，它们已有的约束不必在 prompt 里重复。

---

## 四、设计取向

### 4.1 最小单一抽象，反对过度工程

默认最小抽象，不预设多分类枚举、不留"将来可能用得上"的扩展点。评审子代理的产出时主动砍过度设计。

判例：sprint4 的交互工具，PM 初版设计了 5 种 `input_type` 枚举（credential/text/choice/path/confirm + options）被否决，最终收敛为**单个 `request_user_input`，只保留 `is_sensitive` + `purpose_key`**。

### 4.2 用户可见文本禁用内部术语

- 禁止把内部枚举值（如 `from_scratch` / `use_repo` / `hybrid`）、内部字段名、自创英文缩写暴露给用户
- UI 文案一律经 `ui/term_map.py::humanize` 转换
- **写 LLM prompt 时也别拿英文枚举当叙述示范**，否则模型会把它抄进自由文本字段（plan_summary / deliverables）
- 这条同样适用于**给用户写的解释和汇报**，不只是 UI

---

## 五、对外表述纪律（面试口径）

讲 2403.06402 那次修复循环事故时：

**`No module named 'src'` 在 `checkpoints.db` 里出现 831 次，指的是"该错误字符串的出现次数"**（同一份日志被多个 checkpoint 快照反复序列化），**真实的修复循环只跑了 4 轮**（`fix_loop_count=4`）。

说成"重试了 831 次"是错的，一问就穿。

同理，S7-04 被砍这件事，讲的重点是决策理由而非结果：**根因已被上游 S7-02 解决后，下游补丁应当撤销而不是照着计划做完**。

---

## 六、几条零散但通用的教训

- **验证远端真实状态用 `git ls-remote`**，不要看本地 tracking ref。按 URL 直接 push 不会更新 `refs/remotes/origin/master`，`git status` 会假显示 ahead。
- **超长 commit message 用 `git commit -F <file>`**。在 csh 下，含 `§`、`→`、中文括号的超长多 `-m` 提交会**退出码 0 但根本没产生 commit**。
- **给用户的文字里不要写具体端口号**。VSCode 会抢注终端文本中出现的端口，僵尸转发会喂空响应导致页面空白。让用户走 PORTS 面板删旧建新、点地球图标打开。streamlit 建议加 `--server.enableCORS false --server.enableXsrfProtection false`。
