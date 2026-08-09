# 本项目是一个基于 LangGraph 的 agentic workflow 论文自动复现系统（流水线骨架 + 节点内 ReAct agent），未来将在 coding ↔ execution 修复循环引入局部 multi-agent 子图。团队成员相关产出与项目进展等文档都放在 ./docs/ 文件夹内。

# 项目架构速览
- 流水线：paper_intake → paper_analysis → resource_scout → planning（人在回路）→ coding → execution（↔coding 修复循环）→ reporting
- 编排：LangGraph + SqliteSaver | LLM：LangChain ChatOpenAI | 论文读取：deepxiv-sdk（参考仓库 `./deepxiv_sdk_repo`，代码中通过 pip 包 `deepxiv_sdk` 导入）
- 依赖关键路径：`config.py` + `core/state.py` + `core/errors.py` → `core/llm_client.py` + `core/checkpointer.py` + `core/react_base.py` + `core/secrets_store.py` → `core/tools/*`（deepxiv/git/pwc/code_fs/run_command/interaction）→ `core/nodes/*`（7 节点）→ `core/graph.py` → `app.py`/`ui/`
- 每个 Sprint 的文档在 `docs/sprint{N}/` 下（prd.md、architecture.md、dev-plan.md），进度跟踪在 `docs/TODO.md`，测试执行报告归档在 `docs/sprint{N}/test-reports/`（由测试工程师代理在每次跑测试后落盘，详见 `.claude/agents/test-engineer.md` "测试报告归档规范"）

# 工程记忆
- `docs/MEMORY.md` 记录了只存在于协作经验中、无法从代码和 git 历史反推的工程约定与踩坑结论（多代理并行的隔离规则、Python 环境的失败症状、流程铁律、设计取向、对外表述纪律）
- **所有 agent 在开始任务前必须先读 `docs/MEMORY.md`**，其中的约定与本文件同等效力
- 新踩到的坑、用户新给的工作方式纠正，若属于"换台机器仍然成立、但代码里看不出来"的知识，追加进 `docs/MEMORY.md`；只与本机环境绑定的内容（CLI 版本、磁盘配额、SSH 配置、端口转发）不要写进去

# 与用户的每次对话均需要显式称呼用户为Maria

# 与用户的沟通均使用中文

# TODO 共同维护规范（2026-08-09 改版，起因见 `docs/MEMORY.md` §1.4）

`docs/TODO.md` 分两区：**顶部「活区」**（还没了结的）+ **下方「归档区」**（已了结的，只读）。

## 怎么读
- **默认只读活区**（文件顶部到「归档区」标题为止），再看归档区最新一段即可了解进展。**不要通读全文**——它是档案馆，93% 是历史。
- ⚠ **唯一例外：改一个数字 / 口径 / 设计之后的跟改收尾，必须按关键词全文 grep 收网**（`MEMORY` §3.10）。"只读活区"是省读的默认值，**不是省掉 grep 的理由**——本项目挖出的失真绝大多数来自 grep，无一来自点名清单。

## 四个符号（只有这四个，不要自创）
| 符号 | 含义 | 允许出现的区 |
|---|---|---|
| `- [ ]` | **真待办**：还要有人动手做 | 仅活区 |
| `- [?]` | 待 Maria 拍板 / 待授权，我方无法推进 | 仅活区 |
| `- [~]` | 已裁定不做 / 已作废 / 仅留档备查 | 仅归档区 |
| `- [x]` | 已完成 | 两区皆可 |

🔴 **`- [ ]` 只表示"真待办"。** 作废原文、有意不做、纪律提醒、永久备忘一律不得用它——否则每次打开文件都会看到一堆假的"没做完"。

## 单条限长：≤ 2 行 / ≤ 200 字
一条 TODO 只写四件事：**谁 / 做什么 / 现状 / 详情在哪**。详情各归各家，不要抄进来：
- 交付细节 → commit message + `docs/sprint{N}/dev-plan.md` 的检查点
- 架构裁定 → `docs/sprint{N}/architecture.md`
- 换机仍成立的踩坑结论 → `docs/MEMORY.md`
- 测试取证 → `docs/sprint{N}/test-reports/`

## 谁能改（🔴 与 `MEMORY` §1.1 的文件边界隔离配套，别只读一边）
- **各代理有一项窄权限**：把**属于自己**那条的 `- [ ]` 改成 `- [x]`，并在行尾追加 `⇒ ✅ [日期] 一句话结论 + 证据指针`。仅此一种编辑。
- **禁止**：新增条目、删除任何行、改别人的条目、改分区结构、把条目在两区之间搬动。这四件由主控做。
- 需要新开条目的，写进返回报告交主控落盘，**不要自己往文件里加**（并行时会在同一位置撞车）。
- 示例：`- [x] [2026-05-06] @Maria 完成 SDK 接口设计 ⇒ ✅ [2026-05-08] 已定稿，见 docs/sprint1/architecture.md §3`