# TODO

所有 agent 共同维护此文件，请在开始和完成任务时及时更新。

格式：`- [ ] [日期] @负责人 任务描述` / `- [x] [日期] @负责人 任务描述`

---

## 阶段 1：基础骨架（技术架构 §12，第 1-2 周）

- [ ] [2026-05-06] 创建项目目录结构（core/ 已创建，ui/, sandbox/ 等待后续 Sprint）
- [x] [2026-05-07] @全栈开发代理 编写 `requirements.txt`，声明所有 Python 依赖
- [x] [2026-05-07] @全栈开发代理 A1 自测通过：pip install 无冲突 + pip check 无问题 + 全部 7 个包 import 成功
- [x] [2026-05-07] @全栈开发代理 实现 `config.py`——全局配置（路径、默认值、环境变量）
- [x] [2026-05-08] @全栈开发代理 补齐 `config.py` ReAct 配置常量（REACT_MAX_ROUNDS_PAPER_INTAKE, REACT_MAX_ROUNDS_PAPER_ANALYSIS, REACT_LLM_TEMPERATURE, REACT_RESULT_TAG_OPEN, REACT_RESULT_TAG_CLOSE, TOOL_RESULT_MAX_LENGTH）——同步 dev-plan v1.1 要求
- [x] [2026-05-07] @全栈开发代理 实现 `core/state.py`——GlobalState 及所有 TypedDict 定义（含 NodeError、degraded_nodes、retry_budget_remaining 错误追踪字段）
- [x] [2026-05-10] @全栈开发代理 A3 自测通过：11 个 TypedDict/Enum 导入正常 + ExecutionMode 枚举值正确 + create_initial_state 返回完整 GlobalState + 默认值全部正确（retry_budget_remaining=50, fix_loop_count=0, execution_mode=FULL）
- [ ] [2026-05-06] 实现 `core/graph.py`——LangGraph 主图骨架（7 节点注册、顺序边、interrupt 占位）
- [x] [2026-05-11] @全栈开发代理 实现 `core/checkpointer.py`——SqliteSaver 初始化与 checkpoint 管理（WAL 模式，4 项自测全部通过）
- [x] [2026-05-11] @全栈开发代理 实现 `core/errors.py`——统一异常层次定义（AutoReproError / TransientError / PermanentError / LLMError / SandboxError 等 + make_node_error 工厂函数），A4 自测全部通过
- [x] [2026-05-12] @全栈开发代理 实现 `core/llm_client.py`——OpenAI 兼容 LLM 客户端封装（含指数退避重试、structured output 调用、token 估算），B2 自测全部通过（9 项函数导入 + create_llm + estimate_tokens + check_context_limit + JSON 解析 + 错误分类）
- [x] [2026-05-12] @全栈开发代理 实现 `core/tools/deepxiv_tools.py`——deepxiv Reader 薄封装 + ReAct 工具工厂函数（7 个 BaseTool），B3 自测全部通过
- [x] [2026-05-13] @全栈开发代理 实现 `core/react_base.py`——通用 ReAct 子图基础设施（ReActState、create_react_subgraph、_make_react_wrapper），B4 自测全部 8 项通过（ReActState 实例化与 operator.add 追加 / 子图节点编译完整 / <result> 正常解析 / 超预算 force_finish / 工具异常容错 / 工具结果截断 / wrapper 签名 / GlobalState 双向映射与预算扣减）
- [x] [2026-05-14] @全栈开发代理 C1 实现 `core/nodes/paper_intake.py`——节点1：论文输入与解析（_make_react_wrapper 生成 callable + PAPER_META_SCHEMA + _build_intake_system_prompt 固定 prompt 模板 + _map_intake_result 字段兜底/类型补齐/非 CS 警告）；同时创建 `core/nodes/__init__.py`；自测 8/8 通过（callable / context→HumanMessage 映射 / 工具调用路径 / 全字段填充 / head 失败仅 brief / 非 CS 警告 / 论文不存在 error+node_errors / URL 清洗），B4 react_base 回归 4/4 通过
- [ ] [2026-05-06] 实现 `core/nodes/paper_analysis.py`——节点2：深度论文分析
- [ ] [2026-05-06] **阶段 1 验收**：能通过代码输入 arXiv ID，经 paper_intake 和 paper_analysis 输出结构化分析结果，状态可持久化到 SQLite

## 阶段 2：核心链路（技术架构 §12，第 3-4 周）

- [ ] [2026-05-06] 实现 `core/tools/git_tools.py`——仓库克隆（git clone）与本地仓库分析操作（提交活跃度、目录结构等）
- [ ] [2026-05-06] 实现 `core/nodes/resource_scout.py`——节点3：资源搜集与评估
- [ ] [2026-05-06] 实现 `core/nodes/planning.py`——节点4：复现规划 + interrupt 人在回路
- [ ] [2026-05-06] 实现 `ui/pages/paper_input.py`——Streamlit 页面1：论文输入
- [ ] [2026-05-06] 实现 `ui/pages/analysis_progress.py`——Streamlit 页面2：分析进度
- [ ] [2026-05-06] 实现 `ui/pages/plan_review.py`——Streamlit 页面3：计划审核
- [ ] [2026-05-06] 实现 `ui/components/llm_config_form.py`——LLM 配置表单组件
- [ ] [2026-05-06] 实现 `app.py`——Streamlit 应用入口 + 工作线程 + 轮询机制
- [ ] [2026-05-06] **阶段 2 验收**：从论文输入到计划审核的完整链路可在 Streamlit 界面运行，人在回路中断/恢复正常工作

## 阶段 3：执行闭环（技术架构 §12，第 5-7 周）

- [ ] [2026-05-06] 实现 `sandbox/local_venv.py`——本地 venv 沙箱管理
- [ ] [2026-05-06] 实现 `core/tools/shell_tools.py`——Shell 命令执行工具
- [ ] [2026-05-06] 实现 `core/tools/file_tools.py`——文件读写工具
- [ ] [2026-05-06] 实现 `core/nodes/coding.py`——节点5：编码与环境搭建
- [ ] [2026-05-06] 实现 `core/nodes/execution.py`——节点6：执行与测试验证
- [ ] [2026-05-06] 实现 `core/nodes/reporting.py`——节点7：报告生成
- [ ] [2026-05-06] 实现 `ui/pages/execution_monitor.py`——Streamlit 页面4：执行监控（含自动修复状态展示区）
- [ ] [2026-05-06] 实现 `ui/pages/report_view.py`——Streamlit 页面5：结果报告
- [ ] [2026-05-06] 更新 `core/graph.py`——实现 code_only 模式条件路由 + execution↔coding 修复循环路由（最多 3 轮）
- [ ] [2026-05-06] 实现 execution 3 轮修复失败后的用户选项 UI（A: 导出代码包+诊断报告 / B: 回退到计划审核 / C: 终止并导出所有成果）
- [ ] [2026-05-06] 实现基础错误信息展示组件（一句话摘要 + 可展开详情 + 完整日志链接）
- [ ] [2026-05-06] 实现各节点降级逻辑（paper_analysis 章节降级链、resource_scout 搜索优先级链、沙箱错误检测分类）
- [ ] [2026-05-06] **阶段 3 验收**：端到端完成一次完整复现（arXiv ID → 分析 → 资源 → 计划 → 编码 → 执行 → 报告），execution↔coding 修复循环正常工作

## 阶段 4：稳定化（技术架构 §12，第 8 周）

- [ ] [2026-05-06] 错误处理精细化——重试预算追踪、混沌测试（随机注入异常验证系统不卡死）、边界场景覆盖
- [ ] [2026-05-06] CLI 入口实现（命令行基础操作支持）
- [ ] [2026-05-06] 集成测试（端到端测试用例，覆盖主要场景）
- [ ] [2026-05-06] 用户文档与开发者文档
- [ ] [2026-05-06] 性能优化（API 请求缓存、减少不必要的 LLM 调用）
- [x] [2026-05-13] @Maria 实施 Prompt Cache 方案 A：`core/llm_client.py` 增加 `LLM_ENABLE_PROMPT_CACHE` 开关（env，默认 True），并在 `_call_llm_with_retry` 后从 response metadata 读取 `cached_tokens` 以 INFO 日志输出（不改 `create_llm` 签名）
- [x] [2026-05-13] @Maria 实施 Prompt Cache 方案 A（B4 部分）：`core/react_base.py` 前缀稳定化改造（SystemMessage 固定模板 + HumanMessage 动态上下文）+ tool_executor 工具结果幂等净化（固定截断标记）
- [ ] [2026-05-13] @Maria 实施 Prompt Cache 方案 A（C2 部分）：`core/nodes/paper_analysis.py` `_build_analysis_system_prompt` 把 arxiv_id / paper_meta 抽到尾部独立段落或 HumanMessage（本 Sprint 最高 ROI）
- [ ] [2026-05-13] @Maria 跑 Prompt Cache 命中率基线实验：固定 arxiv_id 在 5 分钟内连续跑 paper_analysis × 3 次，记录 `cached_tokens / prompt_tokens` 比值；对照组在 system prompt 尾部追加随机后缀
- [ ] [2026-05-13] @Maria Prompt Cache 跨 provider AB 实验：切到 DeepSeek 等自动型 OpenAI 兼容端点，验证前缀稳定改造在脱离 NVIDIA 网关后仍能命中缓存
- [ ] [2026-05-06] 代码审查与类型标注完善

---

## 已完成

- [x] [2026-05-06] @产品经理代理 完成产品设计说明书（docs/product-design-specification.md）
- [x] [2026-05-06] @架构师代理 完成技术架构文档（docs/technical-architecture.md）
- [x] [2026-05-06] deepxiv_sdk 已引入项目
- [x] [2026-05-12] @Maria 重命名 `./deepxiv_sdk/` → `./deepxiv_sdk_repo/`，消除本地目录与 pip 包名的 namespace package 冲突。代码通过 `from deepxiv_sdk import ...` 使用 pip 安装的 SDK，本地仓库仅供参考
- [x] [2026-05-12] @架构师代理 更新架构文档中涉及 `./deepxiv_sdk` 本地路径的引用为 `./deepxiv_sdk_repo`（参考仓库路径变更）——`docs/sprint1/architecture.md` 第 2804 行 `deepxiv_sdk/react_reader.py`→`deepxiv_sdk_repo/react_reader.py`；`docs/technical-architecture.md` 无需修改
- [x] [2026-05-12] @全栈开发代理 更新 Sprint 1 开发计划中涉及 `deepxiv_sdk` 导入路径的说明（B3 任务已不再需要 try/except fallback）——E3 常见问题中 deepxiv_sdk 导入路径已标注为已解决
- [x] [2026-05-06] @Maria 更新 PRD：resource_scout 仓库候选确认改为方案 B——resource_scout 全自动选择最优仓库，候选仓库列表（含评分）合并到 planning 审核页面展示，不在 resource_scout 后增加单独中断点（涉及 §3.2 步骤3、§4.2.2、§4.3.2、§5.2 页面3）
- [x] [2026-05-06] @Maria 更新技术架构文档：明确 resource_scout 全自动运行不设 interrupt，候选仓库确认合并到 planning 的人在回路审核中（涉及 §3.1 节点定义、§3.2 编排方式、§3.3 人在回路机制、§4 ResourceInfo 数据结构注释）
- [x] [2026-05-06] @Maria 更新技术架构文档：补全 GlobalState 中缺失字段并统一术语（§4 补充 NodeError/FixLoopRecord TypedDict 及错误追踪与修复循环追踪字段；§12.6 术语 retry_count→fix_loop_count；§12.3 添加引用说明；§12.6 补充预算耗尽边界处理；§3.2 编排图补充修复循环回退箭头；§12.7 预算表加注 fix_loop_count）
- [x] [2026-05-06] @Maria 更新技术架构文档：补充 code_only 模式最低交付基准线定义及相关节点行为（§3.4 新增 code_only 交付标准说明含最低基准线表格和"最低基准线+agent草拟+用户审核"机制；§4 ReproductionPlan 新增 deliverables 字段；§7.3 补充 coding 节点在 code_only 模式下按交付标准清单编码的行为说明；§3.1 coding 节点职责补充 code_only 描述）
- [x] [2026-05-06] @Maria 更新技术架构文档：调整 resource_scout 设计，MVP 阶段移除 GitHub API 依赖（§2 系统架构图外部服务层改为 GitHub (git clone) + Papers With Code API；§3.1 resource_scout 节点职责更新；§4 RepoInfo 字段调整为本地 git 分析可获取指标，新增 quality_score 计算说明；§5 github_tools.py 重命名为 git_tools.py 并调整职责；§12.1 三层防御图更新；§12.4 工具层重试表更新；§12.5 resource_scout 降级链改为搜索优先级链；§12.9 错误分类表更新；§13 阶段2任务表更新）
- [x] [2026-05-06] @Maria 更新 PRD：调整 resource_scout 资源搜索策略，MVP 阶段不引入 GitHub API 依赖（§4.2.1 资源来源改为 deepxiv github_url → PwC → web search 优先级链；§4.2.2 仓库质量评估简化为 git clone 后本地检查指标；§4.2.3 资源抓取入口去掉 GitHub API；§3.2 步骤3 Agent 描述更新；§4.5.3 错误处理表 GitHub 搜索改为仓库搜索降级链；§7.1 MVP 功能表更新；§8.2 v2 增加 GitHub API 增强项；§9.2 新增 Q9 GitHub API 后续版本待定项）
- [x] [2026-05-06] @产品经理代理 完成 Sprint 1 产品需求文档（docs/sprint1/prd.md）
- [x] [2026-05-06] @架构师代理 完成 Sprint 1 核心架构设计文档（docs/sprint1/architecture.md）
- [x] [2026-05-07] @Maria 创建全栈开发工程师代理（.claude/agents/fullstack-developer.md）
- [x] [2026-05-07] @Maria 更新架构师代理：增加 Agent 工具，新增产品经理和全栈开发工程师为子代理
- [x] [2026-05-07] @Maria 更新产品经理代理：新增全栈开发工程师为子代理
- [x] [2026-05-07] @Maria 三个 agent（产品经理、架构师、全栈开发工程师）实现互通调用
- [x] [2026-05-07] @全栈开发代理 完成 Sprint 1 开发计划（docs/sprint1/dev-plan.md）——含 6 阶段 12 项任务，覆盖 10 个模块的详细实现规格、依赖关系、自测检查点和风险标注
- [x] [2026-05-06] Q1: 确定错误处理策略——采用三层防御式架构（详见技术架构文档 §13、产品设计说明书 §4.5.3）
- [x] [2026-05-06] Q2: 确定报告格式——MVP 阶段仅支持 Markdown 格式
- [x] [2026-05-06] Q3: 确定"只编码不复现"模式的默认交付标准——最低交付标准在 planning 节点人在回路审核时与用户沟通明确
- [x] [2026-05-07] @全栈开发代理 更新技术架构文档：架构升级为 ReAct agent + dev_loop 双 agent 协作（§3.2 编排图更新、§3.2.1 ReAct Agent 架构、§3.2.2 dev_loop 双 Agent 协作子图、§3.3 dev_loop 失败中断、§3.4 条件路由更新、§5 模块结构更新、§6.4 工具工厂函数、§12.5 节点降级策略更新、§12.6 修复循环更新、§12.7 预算表更新、§12.8 标题更新、§13 实现优先级更新、文档末尾更新日志）
- [x] [2026-05-07] @全栈开发代理 更新 Sprint 1 开发计划同步 ReAct 架构升级——新增 S1-11 react_base.py 任务（阶段 B4），config.py 新增 ReAct 配置常量，deepxiv_tools.py 新增 7 个工具工厂函数，paper_intake 和 paper_analysis 升级为 ReAct agent 实现，graph.py 使用 ReAct wrapper 注册节点，新增风险 R8/R9，更新时间估算（v1.0 ~26h -> v1.1 ~32h），总任务数 10->11
