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

# TODO 共同维护规范
- 所有 agent 需要共同维护一份 TODO 文件，路径为 `docs/TODO.md`
- 在开始任务前，先阅读 `docs/TODO.md` 了解当前进展和待办事项
- 完成任务后，及时更新 `docs/TODO.md`，标记已完成的项目并添加新的待办事项
- TODO 条目格式：使用 `- [ ]` 表示待办，`- [x]` 表示已完成，每条附上负责人和日期
- 示例：`- [ ] [2026-05-06] @Maria 完成 SDK 接口设计`