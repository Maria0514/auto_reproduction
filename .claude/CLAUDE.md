# 本项目为一个基于multi-agent架构的论文自动复现系统，团队成员相关产出与项目进展等文档都放在./docs/文件夹内

# 项目架构速览
- 流水线：paper_intake → paper_analysis → resource_scout → planning（人在回路）→ coding → execution（↔coding 修复循环）→ reporting
- 编排：LangGraph + SqliteSaver | LLM：LangChain ChatOpenAI | 论文读取：deepxiv-sdk（参考仓库 `./deepxiv_sdk_repo`，代码中通过 pip 包 `deepxiv_sdk` 导入）
- 依赖关键路径：`config.py` + `state.py` + `errors.py` → `llm_client.py` + `deepxiv_tools.py` + `checkpointer.py` → `paper_intake.py` + `paper_analysis.py` → `graph.py`
- 每个 Sprint 的文档在 `docs/sprint{N}/` 下（prd.md、architecture.md、dev-plan.md），进度跟踪在 `docs/TODO.md`

# 与用户的每次对话均需要显式称呼用户为Maria

# 与用户的沟通均使用中文

# TODO 共同维护规范
- 所有 agent 需要共同维护一份 TODO 文件，路径为 `docs/TODO.md`
- 在开始任务前，先阅读 `docs/TODO.md` 了解当前进展和待办事项
- 完成任务后，及时更新 `docs/TODO.md`，标记已完成的项目并添加新的待办事项
- TODO 条目格式：使用 `- [ ]` 表示待办，`- [x]` 表示已完成，每条附上负责人和日期
- 示例：`- [ ] [2026-05-06] @Maria 完成 SDK 接口设计`