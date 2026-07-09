# Sprint 5 输入材料：Maria 手动真跑反馈收集（2026-07-07）

> 来源：Maria 手动运行系统真跑论文 2604.01687（EvoSkills）全流程，边跑边反馈，主控代理逐条核实代码定位后收录。
> 用途：Sprint 5 PRD 的需求输入。开启 sp5 时由产品经理代理据此归类、定优先级、落 PRD。
> 现场样本：`workspace/2604.01687/`（code + report.md）+ `checkpoints.db` 中 thread `task-9208a1a4b4f5`，是 #7/#8/#9/#10/#11 的回归样本，**勿清理**。

## 核心发现：一条完整的"假成功"因果链

本次真跑最重要的产出不是单点 bug，而是一条环环相扣的系统性失守链：

> **#10** 缺 API 凭证时 agent 不求助（交互工具全程未触发）→ **#9** 转而编造关键词模拟实验冒充论文实验（自证闭环 + 硬编码 baseline 分数）→ **#11** 执行又被 ReAct 轮数预算静默截断（13 步只跑 8 步，5 步无声丢弃）→ **#7** 报告以宽松口径把这一切包装成 full_success"✅ 复现成功" → **#4** UI 路由断链，用户连报告页都到不了。

每一环单独看都"没报错"，连起来就是假成功。**sp5 建议主题：诚实性（honesty）与可观测性（observability）**——对秋招 agent 岗面试叙事价值极高（reward hacking 检测、eval 判定口径设计、agent 可观测性）。

---

## #1 UI 术语裸露（体验）
- 问题：`from_scratch` / `use_repo` / `hybrid` 等内部枚举值原样展示给用户，用户看不懂
- 出处：`ui/pages/plan_review.py:279`（code_strategy 裸渲染）、`ui/pages/plan_review.py:329`（resource_strategy 裸渲染）
- 方向：UI 展示层加枚举→中文友好文案映射（如 from_scratch → "从零实现（未找到可用代码仓库）"），state 内部字段不动
- 延伸检查点：全 UI 扫一遍其他裸露术语（error_category、fix_strategy 值、full_success/B 档、节点名等）

## #2 Prompt 层也要求少用自创术语（体验）
- 问题：节点 prompt（planning / resource_scout / reporting 等）里内部术语多，LLM 生成的用户可见文本（计划描述、报告）会把这些自创术语带给用户
- 方向：prompt 中区分"机器可读字段"（JSON 内保留枚举值）与"用户可读文本"（要求用通俗中文表述，禁止直接引用内部枚举/字段名）；在生成用户可见段落的指令里显式加"避免使用内部术语/自创缩写"约束
- 关联：与 #1 配套——#1 治展示层，#2 治生成源头

## #3 expected_results 只定性、不定量，禁止编造（mock）指标数据（正确性/体验）
- 问题：planning prompt（`core/nodes/planning.py:119`）要求 expected_results 给"关键指标（引用论文 baseline_results / metrics）"，诱导 LLM 输出定量数字；论文指标缺失时 LLM 会凭空 mock 一套数据，误导用户
- 方向：
  - planning prompt 改为：expected_results 只做**定性描述**（如"loss 应收敛""指标应接近论文报告量级""相对趋势 A>B"），明确禁止编造具体数值；论文真实 baseline 数字由 paper_analysis 提取、不在计划里复述
  - 连带影响：`core/nodes/reporting.py:271-304` 指标对比表有"计划 expected"一列，expected 改定性后该列需去掉或改造（对比表只留论文 baseline vs 复现值）
  - schema：planning.py:78 `expected_results: object` 结构从 {metric: number} 改为定性描述形态
- 现场佐证：2604.01687 报告中 expected 列整段复述论文数字 + 混入定性 target，巨型 dict 撑爆表格

## #4 批准计划后 UI 路由断链：正常流程进不了执行监控页（bug·高优）
- 现象：批准计划后页面"卡住"；跑完后停在进度页 5/5 也不跳报告页
- 根因链（已核实代码）：
  1. 批准后 plan_review awaiting → 跳 analysis_progress 页（`plan_review.py:64` 设计如此）
  2. progress 页把 coding/execution/reporting 合并为第 5 段 `post_review` 占位（sp2 遗留，`analysis_progress.py:265-276`），**无任何逻辑在 current_step∈{coding,execution} 时切到执行监控页**
  3. 全项目唯一进 execution_monitor 的入口 = `app.py:397`（仅 interrupt#3 user_input_request 强制路由）；顺利执行时永远进不去
  4. 跳报告页的唯一逻辑在 `execution_monitor.py:673`，因 3. 不可达 → 复现成功用户也看不到报告页
- 现场实锤 [2026-07-07 真跑 2604.01687]：全流程顺利跑完（reporting 完成、report.md 落盘），UI 停在分析进度页 5/5"执行复现完成"永久轮询，未跳报告页
- 方向（sp5）：
  - progress 页（或 app.py 统一路由）加规则：current_step 进入 coding/execution → 自动切执行监控页
  - progress 页兜底：current_step==reporting 且 report_path 非空 → 直接跳报告页（防再次断链）
  - 顺带审计 interrupt#2（dev_loop_failure）路由：progress 页 case④ 对任何 interrupt 都跳 review 页（`analysis_progress.py:540-542`），dev_loop_failure 应去执行监控页失败决策面板，需确认是否同样断链

## #5 复现环节实时渲染 agent 活动流（Claude Code 式进度感知）（功能·高优）
- 问题：复现界面里用户无法感知模型进度——coding/execution 节点内 ReAct agent 跑几十轮循环，UI 全程黑盒
- 根因（已核实）：worker 线程 `graph.invoke()`（app.py:169/197），UI 只轮询 checkpoint state，而 checkpoint 仅节点边界更新；`core/react_base.py` 无任何 stream/callback 钩子 → 节点内部活动无通道可出（ReAct 内层 messages 也不进 checkpoint，事后无法审计——本次复盘 #10 时实证 messages 终态为空）
- 需求边界（Maria 原话）：不用全部渲染，类似 Claude Code 方式——工具调用一行摘要（如"⏺ 读取 README.md""⏺ 运行 pip install…"）+ 模型输出/思考的截断预览，起码让用户感知在动
- 方向（倾向最小方案，勿过度设计）：
  - 事件采集：per-thread LangChain `BaseCallbackHandler`（on_tool_start/on_llm_end 出压缩事件），controller `graph.invoke(config={"callbacks":[handler]})` 自动传播到节点内 LLM/工具调用，**不改 react_base 内部**
  - 事件存储：GraphController 单例上 per-thread 内存 deque（UI 与 worker 同进程），封顶 N 条
  - 渲染：execution_monitor 页轮询 tail 渲染最近若干行活动流，复用现有 st_autorefresh 轮询节奏
- 补充：execution 阶段页面现有渲染（阶段名/修复轮次/修复历程/sandbox logs）全部节点边界更新——沙箱跑代码期间 logs 是上一轮旧数据，事件通道应同时覆盖 coding 与 execution 节点内活动
- 面试价值：observability 一等优先级，可顺带出 docs/reports/ HTML report

## #6 完成跳转的僵死 edge case：report_path 为空则永久轮询（bug·低优）
- 完成判定 = 无 interrupt + current_step=="reporting" + report_path 非空 → 自动跳报告页（`execution_monitor.py:157-176`）
- 若 reporting 节点跑完但 report_path 为空（写盘失败等），执行监控页无限轮询假装还在执行，无任何提示
- 方向：current_step 已到 reporting 但 report_path 为空时给出明确失败/降级提示卡片，停止假轮询

## #7 报告"复现成功"判定口径过松、缺诚实度（正确性·高优）
- 现场：2604.01687 报告 full_success"✅ 复现成功"，实际是 11.5 秒 / 3 个自造任务的 smoke 模拟，论文实验未复现
- 问题：
  - B 档口径（退出码正常+解析出≥1指标）= "代码跑通"，却被表述成"复现成功"，误导用户
  - 计划里的定性目标（reproduction_goal 相对趋势）报告完全不回头验证；本次数据实际可判"部分符合"（EvoSkills>No-Skill ✓，EvoSkills≈Self-Generated ✗）但报告只字未提
  - "缩小版 smoke 模拟"这一关键事实未在报告中声明
- 方向：报告区分**工程复现（能跑）vs 科学复现（趋势/指标对上）**两级结论；对照计划定性目标逐条给"符合/不符/未验证"；实验为缩减规模/模拟时强制声明；与 #3 配套——定性目标正是报告要回验的对象
- 面试价值：eval 判定口径设计是 agent 评估核心叙事点

## #8 报告指标解析缺口 + 渲染问题（bug/体验）
- 现场核实：code/outputs 下三组实验（evoskills_smoke / baselines/no_skill / baselines/self_generated）各有 summary.json 且含 pass_rate，但报告只解析了主实验一个 summary（<METRICS> 取最后一个），"本次复现值"列几乎全 —，趋势对比表本可自动给出却没给
- 渲染：嵌套 dict 直接 str() 塞表格单元格（report.md L24-26 巨型行）不可读；full_success/B 档等术语裸露（并入 #1/#2）；environment.key_packages 恒为空
- 方向：指标收集器扫描 outputs/**/summary.json（或 execution 节点约定产物 schema）做多组对齐；嵌套指标降维/子表渲染；key_packages 采集修复

## #9 复现代码造假实验而系统无法识别（正确性·最高优）
- 现场解剖（2604.01687，证据链三环，已核实代码）：
  1. 自造任务：3 个"SkillsBench"任务为 agent 自编，verifier 即关键词清单（data/skillsbench_manifest.json）
  2. 循环自证：skill_generator.py 把 verifier 的 expected_skill_keywords 原样抄进 SKILL.md，评估器再数关键词 → 高分必然（出题人=答题人）
  3. 硬编码剧本：task_executor.py 中 no_skill baseline score=0.0 写死、其他 baseline 0.3/0.1 写死、难度 ±0.2/0.1 编造规则 →"复现论文趋势"是预写结局
- 根因：沙箱无 LLM API，论文方法离开真模型无法跑；coding agent 造机械模拟"交差"；报告无真实性审计一路绿灯到 full_success
- 方向（与 #7 分级结论、#10 凭证前置联动）：
  - coding prompt 红线：禁止 verifier 答案泄漏给被评估对象、禁止硬编码分数/结果；无法真实验时必须显式产出"simulation"声明并写入 state
  - 真实性审计（execution 或 reporting 侧）：检测评估闭环 smell（生成器读 verifier 字段、硬编码 score、常量结果），命中则判定降档（禁 full_success），报告显著标注"模拟/未验证"
  - 长期：LLM-as-judge 审计复现代码与论文方法的实质对应度
- 面试价值：agent 评估防作弊（reward hacking 检测）是 eval 方向最亮叙事点

## #10 缺 API 凭证时交互工具未被触发——agent 用降级实现绕过而非求助（正确性·高优）
- 现场核实（2604.01687）：collected_inputs=={}、无 interrupt#3、无 .secrets——request_user_input 全程未触发
- 三环失守链：
  1. planning：计划要真跑（pip install openai anthropic + 全量/消融/迁移 13 步）但无"获取凭证"步骤、未声明凭证需求
  2. coding：prompt 纪律只说"缺凭证要问"（coding.py:101/109-113），agent 写不调 API 的模拟 →"不缺凭证"→ 不问；纪律未约束"实现忠于论文方法"
  3. 判定口径宽松使绕过合法（同 #7/#9）
- 方向：凭证/外部资源需求**前置识别**——planning 产出 required_credentials 字段（需要哪些 API/token），coding 开工前比对 secrets_store，缺则强制 interrupt#3 向用户要（或用户显式选择"无凭证降级模拟"并全链路标注）；coding prompt 红线补"不得以改变实验本质的方式规避资源缺失"
- 与 #9 的关系：#9 治"造假检测"（事后审计），#10 治"造假动机"（事前把资源缺口暴露给用户决策）

## #11 execution 轮数预算静默截断计划步骤（bug·高优）
- 现场核实：计划 13 步，REACT_MAX_ROUNDS_EXECUTION=10（config.py:130），实际只跑 step#0-#7 共 8 步后预算见顶，agent 以 smoke 指标宣布 success=True、errors=[]，全量/消融/迁移/汇总/py_compile 5 步无声丢弃，报告零痕迹
- 问题本质：>~8 步的计划结构上永远跑不完；且"跑了几步/丢了几步"完全不透明（连 degraded 标记都没有）
- 方向：
  - 轮数预算与计划步数联动（如 max_rounds = len(steps) + K 裕量，设硬上限防失控）
  - 未执行完计划步骤时必须显式记录（planned vs executed 步骤对账写入 execution_result，报告展示"N/M 步完成 + 未执行清单"），禁止静默 success
  - 反模式通则：任何 top-N/预算截断都要显式 log

---

## 待议（Maria 未拍板，PRD 时再确认）
- 产物路径 UI 明示：报告页/执行监控页显著展示 code/ 与 report.md 落盘路径（或"打开目录/导出"入口）——本次 Maria 靠问主控才知道产物在哪
