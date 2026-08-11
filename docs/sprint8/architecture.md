# Sprint 8 核心架构设计文档：输出契约驱动的复现结果链路 + 结论档位重新定义

**文档版本**：**v2.8**（🔴 **v2.8 = 订正 `BUG-S8-G8`：v2.7 自己新增的 G8 那一行，期望值与它自带的验红条件互斥**（2026-08-09，Maria 授权架构师直接落盘；测试工程师实测挖出、主控两态对照复验）—— G8 同时写死「期望 = **不报 W6**」＋「验红 = 把 `_digest_paper_analysis` 的 `baseline_results` 键去掉 → **本条必红**」，而**键一没，候选集就空，`check_plan` 在读达标线之前就走 §15.3 第 3 条早退，照样不报** ⇒ 按字面只写一句 `assert "W6" not in rules` 的用例**恒绿没牙**。**订正 = G8 期望值改为三条按序断言（①摘要含键 / ②候选集含 `BM25_R2` / ③不报 W6），验红只由结构面的 ①② 承载；G9 升格为「摘键验红在行为面的唯一红灯」；新增 G11 守内层 5 键**（补 §15.3.1 自己点名却没进验证表的缺口）。由此立通用判据 **`R-S8-43`**：**凡期望值形如「不报 / 不发生 / 无副作用」的验证项，验红条件不得落在同一条行为断言上；写下它时必须点名"该红的那一次由哪句断言红"。** ⇒ **立完当场按该判据全表回扫**（`MEMORY` §3.10 跟改收尾），**当场捞出同族第二处**：G1 / G3 / G5 三行同样是 negative 期望，而 v2.7 的 G7 只写了一种突变 ⇒ 这三行在文档层面一条验红都没有 ⇒ **G7 由一种突变扩为三种（删判据 / 无条件上报 / 早退失效），并逐一点名各自会红的条目**。⚠ **这是 v2.7 刚治好的病换一层复发**（那次治的是输入侧手搭 payload，这次栽在输出侧期望值是 negative 的）。🔴 **`tests/` `core/` `ui/` 一字不动，本次是文档追认已落地的测试实现 —— 错的是 v2.7 的文档，不是测试。** 全文见 **§15.5.1**，跟改清单见 **§14.9**。**v2.7 = 裁定 `AR-S8-16`：W6 的第三个候选源在生产链路上恒为空**（2026-08-09，Maria 授权架构师直接落盘）—— §15.3 第 1 条写的三源候选集，`core/plan_checks.py:505` / `:522` **三条都实现了**，但**生产链路喂给 `check_plan` 的不是 `paper_analysis`**，而是 `core/nodes/planning.py::_digest_paper_analysis` 压出来的 **4 键摘要**（`method_summary` / `datasets` / `metrics` / `framework`）—— **`baseline_results` 被丢掉**。⇒ 两个危害方向：①「复现出论文 Table 2 里 `BM25_R2` 报的 0.43」这类**最扎实**的达标线（直接引用论文自报基线）反被 W6 判为"没点到论文里任何具体指标"，与 `R-S6-A5`「宁窄勿宽 / 宁漏报不误报」取向**正好相反**；②`metrics` 与 `datasets` 皆空、只有 `baseline_results` 的论文，候选集空 ⇒ 早退 ⇒ **纯空话标准也不报**。**裁定：`_digest_paper_analysis` 加第 5 键 `baseline_results`，原样透传 / 恒常给键 / 不截断 / 不改名 / 不改型；`core/plan_checks.py` 与 W6 判据一字不动、外层 payload 键集合一字不动。** 另**否决**三条看似合理的替代路（收窄 W6 口径 / 截断键 / 把 `method_summary` 收进候选集），逐条堵死见 §15.3.1。落点 §10 / §12 / §15.3 / §15.5 / §14.8。**v2.6 = 架构侧文档失真收口三处，均为「dev-plan 已登记 / 架构文档零跟改」的欠账，Maria 2026-08-09 授权直接落盘、零生产改动**：①**§16.5① 补全 `mask_value` 对非 str 的三种行为**（`P-S8-21`）—— 原文只写"过 `mask_value`"、未说非 str 怎么办，而实测是**三种**行为（无凭证静默原样返回 / 有凭证+**falsy** 静默原样返回 / 有凭证+truthy 抛 `AttributeError`），其中**两种静默漏过脱敏**，且 `0`/`False` 恰是指标场景最自然的取值 ⇒ 写死「**按 `isinstance` 判定、不得依赖抛不抛异常分支**」+「验红夹具必须含 falsy 组」，新增验证项 **B22**；②**§2.5.4 红线 3 处数订正**（`P-S8-34`）—— 由作废的"三处"（`P-S8-10` 订正的"四处/五处"同样错）改为**逐文件 6 处**并给出落点表，🔴 补登记漏掉的 `core/plan_checks.py`（W6 判据）并显式注明它属"**警示**"不属"判定"、**红线 2 未被破**，§15.3 末同批加反向交叉引用；③**`mask_value` docstring 与实际覆盖面不符**一并登记（只登记、`core/` 零改动）。**v2.5 = 开工期裁定 `AR-S8-15` 补落章 + 一次编号撞车换发**：①2026-08-07 对「`state.py` 两键迁出」的裁定此前**只写进 dev-plan、本文档零改动** —— 与 sp7 `Q-S7-25~31` 完全同型的病，**本版补落章为 §17**；②该裁定原编号 `AR-S8-13` **与本文档 v2.2 已占用的编号撞车**（那是「schema 重生成路径的块内容」），按先占先得**换发为 `AR-S8-15`**，登记见 §17.0；③裁定内容：`TypedDict` 默认 `total=True` ⇒ 加必填键让既有构造点全红（实测 4 处），三条退路全堵死 ⇒ **`T-S8-1a-2` 注销，两键声明形态一字不改、改为与构造点原子同批**（`success_criteria` → `T-S8-1b-2`；`conclusion` → `T-S8-2-8`），升格通用纪律 `R-S8-42`。落点 §10 / §11 前置① / §12 / §14.6 / §17。v2.4 = 跟改 Maria 对 PRD §13 五条的拍板：第 2/3/4/5 条**确认架构默认取值**；🔴 **第 1 条推翻默认取值——`ExecutionResult.metrics` / `metrics_groups` 本 Sprint 删键**（原话「旧字段要是确认没有用了就删掉」）。落点 §0 / §2.6 / §5.9 / §7 / §11 / §12 / §13 / §16.6；**两处被突破的自设约束已显式处置、不默默绕过**（"状态契约新增上限两处" + `dev-plan.md:1358` CP-2.10-3），见 §2.6.4；并**上磁盘复核了 v2.3 那笔"71 处回归面"的账 —— 算高了，真实净增量是 4 处断言 / 1 个文件**，见 §2.6.2。v2.3 = 重裁 AR-S8-10：论文报告值改为「换个东西验」，不是「不验」**——Maria 2026-08-05 追问「论文值难道在前期的计划和分析节点没记下来？」查实**记录链完整**（`paper_analysis.py:45`/`:96`/`:224` → `state.py:80` → `planning.py:381`）⇒ v2.2 的「不进台账、不参与验钞」**治好了误判、却打开了编造**（agent 把论文值往低了编，自己跑出来的数就"对上了"）。**重裁为：论文值物证按 `paper_analysis.baseline_results` 核验**（§16.3.2），落点 §10 / §12 / §16.1 / §16.2 / §16.3 / §16.7；并在 §14.3.0 立**第三条机制性自查**——「我说的『做不到』，是只有这一条路做不到，还是所有路都做不到？」。v2.2 = 跟改 PRD v4.0 的 S8-06 / S8-07 回炉：结果的形状由执行环节决定——①**新增 §16 裁 Q-S8-10**（结果块契约 / 证据台账 / 通用渲染 / 展示上限 / 截断检测 / 两个折叠函数的去留）；②**Q-S8-05 重裁**（报告侧由「改造指标对比表」改为「删除对比表 + 通用块渲染」，见 §5.9）；③**本版显式纠正 v0.2 那次架构误判**（把「停止压扁嵌套」改判为方案 A），留痕见 §14.3.0；④§0 / §2.1 / §10 / §11 / §12 / §13 同步跟改。v2.1 = 跟改 2026-08-04 两项裁定 + Maria 加拍：①**三档 + `_parse_metrics` 四函数由「判定链路解绑」改为「整体删除」**（§12 / §7 / §13）；②**`S8-10`（execution 侧注入）与 `S8-02` 的编码侧部分由批次 1a 移到批次 2**（§11），并新增 **§6.2⑥「注入与其配套提示词约束必须同批落地」**。v2.0 = **跟改 Maria 第四轮拍板：成功标准改由计划针对本篇论文写明**。v1.0 = Q-S8-01 ~ Q-S8-06 六项全裁 + 新识别 Q-S8-07 / Q-S8-08；**v2.0 = Q-S8-02 / Q-S8-05 扩围 + 新增 Q-S8-09（护栏 3 落点）+ 编号撞车换发 + 「零新计划字段 / 唯一状态契约新增」两处表述作废**，逐条跟改清单见 §14）
**日期**：2026-08-03（**v2.1 跟改：2026-08-04；v2.2 跟改：2026-08-05；v2.3 重裁：2026-08-05；v2.4 跟改：2026-08-06；v2.5 补落章 + 编号换发：2026-08-07；v2.6 失真收口：2026-08-09；v2.7 裁定 `AR-S8-16`：2026-08-09；v2.8 订正 `BUG-S8-G8`：2026-08-09**）
**作者**：架构师代理
**对应 PRD**：`docs/sprint8/prd.md` **v4.0**（Maria 四轮拍板 + 2026-08-05 S8-06 / S8-07 回炉已回填；**§4.5.2「两层分离」与 §4.6.2「语义层 / 形状层分离」是本文档两条跟改总纲**）
**体例参照**：`docs/sprint7/architecture.md` v1.3
**推翻的既有裁决**：`docs/sprint5/architecture.md` §7.10 的二选一裁决（"弃选扩展 `<METRICS>` 多块约定、选文件扫描"）——本次两条路**都不走**：`<METRICS>` 通道整体退场，文件扫描 `_collect_grouped_metrics` **v2.2 起由"降为兜底"改判为整体删除**（v2.1 原文"降为兜底"保留在 §13 里划删，理由见 §16.6），主通道改为 agent 汇报 + 系统验钞。**v2.2 另推翻本文档自己的一条 v0.2 裁决** —— "结构自由化由『停止压扁嵌套』改为方案 A（把维度写进组名）"，误判剖析见 **§14.3.0**（不许省，是本版必须留给后人的一节）。

> **本文档的裁定范围**：只裁"怎么实现"。Maria 四轮拍板的产品决策（六条决策 / 四档制 / 判定落点搬迁 / 第四态作废 / 造假审计改判 / 三条封顶 / 审计盲区不治 / **成功标准由计划针对本篇写明** / **两层分离** / **护栏 3 不做阻断门**）**一律照办，不改、不优化、不加回被砍项**。凡在架构上落不了地或存在产品口径冲突的，一律列进 §9「须 Maria 复裁」，不自行调和。
>
> **贯穿硬约束**：不新增 interrupt 种类 / 不改编排图 / 不改人在回路三个交互点 / 保 S-1 重跑幂等契约（`_has_committed_result_for_round` guard）/ **状态契约新增严格限两处**（`ExecutionResult.conclusion` + `ReproductionPlan.success_criteria`，Q-S8-02）**＋ 🔴 v2.4 删除两处**（`ExecutionResult.metrics` / `metrics_groups`，Maria 2026-08-06 拍板，处置与留痕见 §2.6.4）/ **护栏 3 只产警示、不阻断审批** / 反过度工程（MEMORY §4.1）：零新模块、零新枚举类、零"将来可能用得上"的扩展点。
>
> 🔴 **v2.2 新增的第三条总纲（与「两层分离」并列，落地时同样容易搞砸）**：**语义层与形状层必须分处两地** —— **语义层**（每块要有中文标题 / 要说清数据来自哪个产物哪一步 / 若上下文给了论文报告值则同块内可对照 / 不得覆盖合并不同来源的同名数字）写在**系统提示词**里；**形状层**（分几块、几列、列叫什么、行怎么排、用不用表）**只存在于 agent 的汇报数据里，代码侧一行都不许有**。⇒ **`core/` 下不得再出现任何写死的结果表头字符串、写死的结果分节标题、以及对结果块 / 块内行列的 `sorted()`**（§16.7 给出可静态断言的清单）。**这条与「状态契约新增限两处」同级，是本版的红线。**
>
> 🔴 **v2.2 另一条口径澄清（防止把红线读反）**：「代码不预设形状」**不等于**「代码不设上限」。**上限、转义、脱敏、对齐、显式截断标注属于确定性与安全，必须由代码强制**（§16.5）；被禁的是**代码替 agent 决定"结果长什么样"**（表头、列数、分节、顺序）。两者的分界线：**代码可以拒绝渲染得下不去手的东西并如实标注，但不许规定它应该长什么样。**
>
> **先说本 Sprint 的架构级结论（v2.2 重算）**：**新增抽象总量 = 2 个状态契约键（跨两个结构，`conclusion` 的子键不占额度）+ 1 个 `ErrorCategory` 成员 + 5 个 execution 侧纯函数 + 3 个 reporting 侧纯函数 + 4 个 execution 侧模块常量 + 1 条 `check_plan` 警示 + 1 组 term_map 换发**；**同批删除 8 个 execution 侧函数 / 7 个 reporting 侧函数 / 1 个 execution 侧模块常量**（清单见 §12）。⇒ 🔴 **本版是净删大于净增**——这一条直接回应 PRD 非目标 2 的澄清：新方案**不建任何容器**，它比已作废的方案 A **更少**抽象。`_SandboxRunCollector` 一字不动；`code_fs_tools.py` 一字不动；`react_base.py` 一字不动；`graph.py` 一字不动；`config.py` 零新增常量；`check_plan` 既有五条警示行为与既有两个调用点一字不动。
>
> ⚠ **v2.1 的这句话当时就少算了**：它写"1 个 reporting 侧纯函数"，而 v2.0 的 §5.6（`_render_audit_findings`）与 §5.7（`_render_success_criteria`）已经是两个。**v2.2 一并订正为 3 个**（新增 `_render_result_blocks`）。
>
> 🔴 **v2.0 的总纲（落地时最容易搞砸的一件事）**：**第一层（四档语义边界，所有论文共用）与第二层（本篇达标线，计划写）必须落在两个物理位置** —— 第一层写在**系统提示词（稳定前缀）+ execution 模块级常量**里，第二层走 **HumanMessage 动态通道**。**两者不得混在同一段文本里。** 混了要么退回硬编码（第二层被写死），要么允许越权（第一层被计划改动）。详见 §2.5.4。

---

## 0. 裁定总表（先给结论；**v2.2 已按 PRD v4.0 回炉更新**）

| 编号 | 裁定结论一句话 | 主落点 | 阻塞批次 |
|---|---|---|---|
| **Q-S8-01** | **判定不进收集器**：走 `final_state["result"]` 为权威 + messages 回读为**存在性兜底**（`_merge_with_collector` 的镜像应用），判定缺失时走封顶而非判失败 | `execution.py` `_run_execution_agent` / 新 `_resolve_agent_report` | 批次 2 |
| **Q-S8-02** 🔴 **v2.0 扩围 / v2.4 加删除面** | 🔴 **v2.4 追加：`ExecutionResult.metrics` 与 `metrics_groups` 两键删除**（Maria 2026-08-06 推翻架构"保留停产"默认取值；前置条件已核实、真实改动面已复核 = 4 处断言 / 1 个文件，见 **§2.6**）⇒ `ExecutionResult` 由 11 键变 10 键。以下为 v2.0 原文：**跨两个结构、共两个新增键**：①`ExecutionResult` 加 `conclusion: Dict`（`{level, goal_checks, evidence}`），沿 `step_reconciliation` 嵌套字典范式，`level` 存的**就是用户可见的四个中文档名**、无第二套值；②`ReproductionPlan` 加 **`success_criteria: str`（单个字符串，不是"档位→达标线"的字典）**——四档名因此**不出现在计划里**，两层分离由结构本身守住（§2.5） | `core/state.py:159-184` + `:115-157` | ~~批次 1a（字段名）／1b（生产者）／2（消费者）~~ 🔴 **v2.5 改判（`AR-S8-15` / §17）**：**批次 1a 对本文件零改动**。两个加键各自与其构造点**原子同批** —— `success_criteria` 落 **1b**（`T-S8-1b-2`）、`conclusion` 落 **2**（`T-S8-2-8`，与两键删除同任务）。消费侧仍可留到 **3**（读侧不受 `R-S8-42` 约束） |
| **Q-S8-03** | **验钞函数内联自判**（4 行 resolve + is_relative_to，与 `reporting._resolve_report_path` 同范式）；**工具层 `_is_within_workspace` 一字不动** ⇒ 两个闸物理分处两文件，不可能被合成一个 | `execution.py` 新 `_verify_evidence` | 批次 2 |
| **Q-S8-04** | 新增 `ErrorCategory.NO_VERIFIABLE_OUTPUT`；**早停轮数常量复用 `NO_METRICS_EARLY_STOP_ROUNDS`（config 零新增）**；早停在优先级链中**原位继承**，`:2817-2840` 的顺序一字不动 | `execution.py:132-171` / `:2729` / `:2817` | 批次 2 |
| **Q-S8-05** 🔴 **v2.2 重裁**（v2.0 已扩围一次） | `_verify_trend` / `_lookup_metric_value` / `_match_metrics_group` **三个全退场**；`_verify_expected_results` 退化为**旧快照兼容读**；`_determine_conclusion` 改名 `_assemble_conclusion`（只算 annotations + 取执行环节判定）；审计**脱离 `simulation` 标注、独立成节**；`_render_success_criteria` 把本篇成功标准原文照登（§5.7）；🔴 **v2.2 新增**：`_render_metrics_comparison` / `_comparison_table` / `_flatten_mapping` **三个一并删除**（不是改造），报告侧结果呈现改为 `_render_result_blocks(conclusion)` 按 agent 给的块顺序渲染、**代码不排序**；`_render_goal_checks` 新增"按证据台账渲染引用"职责。**与 v2.1 的逐条差异见 §5.9** | `reporting.py:130-324` / `:474-486` / `:587-704` / `:931-1008` | 批次 3 |
| **Q-S8-06** | 沿"非空才注入"，键名 `baseline_results` 与 state 同名透传；**无该值时 payload 字节零扰动 ⇒ 既有基线不换发，只新增"有该值"一条基线**；系统提示词哈希基线本批必换发一次（原因是判定纪律段改写，不是本项） | `execution.py:_build_execution_agent_context` | 批次 1a |
| **Q-S8-07**（v1.0 新识别） | `ErrorCategory.NO_METRICS` **枚举成员必须保留**（旧 checkpoint 反序列化面），只删唯一生产者 `_apply_no_metrics` | `execution.py:132-171` / `:3026` | 批次 2 |
| **Q-S8-08**（v1.0 新识别） | 七处随四档制作废的**用户可见文案**须同批换发并进守门面（清单见 §5.5） | `reporting.py` / `execution.py:2715` / `ui/term_map.py` | 批次 3 |
| **Q-S8-09** 🔴 **v2.0 新增**（= PRD v3.0 里那个撞号的 Q-S8-07，**已换发**，见 §14.2） | 护栏 3 落在 `check_plan` 新增第 6 条警示；**只产警示、不阻断审批**（产品决策，不推翻）；⚠ 判据要用论文分析的事实层名词，而现签名拿不到 ⇒ **加一个带默认值的关键字形参**，既有两个调用点与既有五条警示**一字不动**（§15）。🔴 **v2.7 追加（`AR-S8-16`，别只读本行）**：判据三条候选源**已全部实现**，但**第 3 条在生产链路上恒为空** —— 喂给 `check_plan` 的是 `planning.py::_digest_paper_analysis` 的 4 键摘要，`baseline_results` 从未进去 ⇒ **引用论文自报基线的达标线必被误报**。裁定 = **改 digest 加第 5 键，不动 W6**，全裁见 **§15.3.1**，与 `T-S8-2-8b` 的口径关系见 **§15.6** | `core/plan_checks.py:483` / `ui/pages/plan_review.py:786` 🔴 **+ v2.7：`core/nodes/planning.py::_digest_paper_analysis`** | 批次 1b（**v2.7 修补项亦属 1b 收尾，可与批次 2 并行，文件边界零重叠**） |
| **Q-S8-10** 🔴 **v2.2 新增**（PRD v4.0 §8 交裁，编号与 PRD 一致、无撞车） | **结果块落 `conclusion` 的子键**（`conclusion` 本就是 `Dict[str, Any]`，加子键**零 TypedDict 改动、零状态契约额度**）；**证据台账由系统去重生成、id 由系统生成，agent 一个 id 都不写** ⇒ 悬空 id 在结构上不可能发生（R-S8-23 不适用）；🔴 **v2.3：台账收两种出处的物证，各走各的核验** —— 产物物证走既有五重，**论文报告值走「与 `state["paper_analysis"]["baseline_results"]` 对得上」两重**（记录链完整：`paper_analysis.py:45`/`:96`/`:224` → `state.py:80` → `planning.py:381`），堵住 **AR-S8-14「把对照基准往低了编」**；**块的收编（脱敏 / 长度截断 / 列数对齐 / 四个上限 / 畸形标注）全部落 execution 侧单个纯函数 `_collect_result_blocks`，落盘即可渲染**，reporting 侧只做 Markdown 转义与拼装；`_split_reported_metrics` / `_coerce_reported_value` / `_collect_grouped_metrics` / `_GROUP_METRIC_STR_MAX_LEN` **四个一并删除**（折叠动作是本次病根，扫盘兜底的硬编码前提已被同批拆除）；**截断检测用"有 `<result>` 开标签、无闭标签"的确定性信号**，不用长度启发式，`react_base` 一字不动。**全裁见 §16** | `execution.py:1092` / `:1706-1856` / `:2938` / `:2961`；`reporting.py:931-1008`；`ui/pages/result_report.py:163-201` / `:315-330` | 批次 2 + 批次 3 |

---

## 1. Q-S8-01（最硬）：判定结果跨中断的保真

### 1.1 先把矛盾拆准：收集器的丢失面**不覆盖**判定，把判定塞进收集器等于人为引入丢失

PRD 的担忧原文是"档位/逐条结论/物证清单全部来自 agent 一次汇报，而既有结果收集器正是为绕开自述而建，且它在中断恢复后会丢失前半段"。这两件事必须分开看，因为**丢失机理不同**：

| 数据 | 产生方式 | 存活介质 | 跨 interrupt 行为 |
|---|---|---|---|
| `_SandboxRunCollector.run_results` / `prep_results` / `step_ledger` | 工具体内**逐次 append，累积型** | `_run_execution_agent` 函数体内 new 出来的**普通 Python 对象** | resume 重跑函数体 → **对象重建 → 前半段全丢**（R-S4-10 实证，`execution.py:812-817`） |
| agent 的收尾汇报 `final_state["result"]` | `finalize_node` / `force_finish_node` 在子图**终态一次性写入**（`react_base.py:677` / `:751-757`） | 子图 `ReActState` 的 `result` 通道，随子图 checkpoint 持久化 | resume 后子图从 checkpoint 恢复、继续跑到 finalize → **必然是完整的一次产物**，不存在"前半段" |

**判定天然不是累积型数据**。它由 agent 在最后一轮一次交出，前面若干轮的中断只影响"它看到了多少工具结果"，不影响"它交出的那一份汇报是否完整"。

⇒ **裁定 1（否决式）：档位 / 逐条结论 / 物证清单一律不进 `_SandboxRunCollector`，`_SandboxRunCollector` dataclass 一字不动（含 `:812-817` 那段 R-S4-10 注记，它记的是收集器的边界、依旧准确）。** 走收集器不但拿不到额外保真度，反而会把一个"终态一次写"的数据降级成"累积型"，从而**主动获得**收集器的前半段丢失面——这正是 Q-S8-01 最需要避免的结果。

> 反过来说明为什么 `run_results` 当初必须走收集器：它要的是**未截断的 stdout/stderr 原文**，而 messages 回读只有 `_tail()` 后的尾部（`execution.py:1433-1435`）。判定这边**没有这个保真度差**——回读的是同一份 JSON 文本的同一份字节，只有"在不在"的差别，没有"全不全"的差别。**这一句是本项裁定的技术核心。**

### 1.2 真正要治的是三条"判定拿不到"的路径（不是"拿到一半"）

上磁盘复核后，`final_state["result"]` 有且只有三条缺失路径：

| # | 路径 | 源码 | 结果 |
|---|---|---|---|
| (a) | 子图抛异常 → `_run_execution_agent` 降级 return | `execution.py:1633-1638` | 整个 `ExecAgentOutput` 无判定 |
| (b) | finalize 标签解析失败 **且** schema 重生成也失败 | `react_base.py:754-755` `return {"result": {}, ...}` | `result` 为空 dict |
| (c) | `force_finish` 走 free-form 回退分支（schema 强制失败），且最后一条 AIMessage 无 `<result>` 标签 | `react_base.py:680-688` → 落回 (b) | 同 (b) |

其中 (c) 的**另一半是好消息**：`force_finish` 的 schema 成功分支**已经把结果同步写了一条 `<result>` 包裹的 AIMessage**（`react_base.py:666-672`）⇒ **messages 通道天然携带同一份判定**，回读兜底不需要 `react_base` 做任何改动。

### 1.3 裁定 2（方案）：`_resolve_agent_report` 单点，与 `_merge_with_collector` 同范式、方向镜像

新增一个纯函数（`execution.py`，紧邻 `_merge_with_collector` 放置，共用同一段范式注释）：

```
def _resolve_agent_report(final_state, final_messages) -> Dict[str, Any]:
    """agent 收尾汇报的取数单点（Q-S8-01）。

    与 _merge_with_collector 同一范式家族、方向镜像：
      - _merge_with_collector 治的是"保真度差"（收集器全文 > 回读尾部）⇒ 收集器优先；
      - 本函数治的是"存在性差"（两边字节同源、无截断差）⇒ 子图 result 优先，
        缺失/空/必填不全时用 messages 末条 <result> 回读补位。
    两条都拿不到 → 返回 {}，由调用方走 §4.5.3 封顶（绝不因此判失败）。
    """
```

- **优先级**：`final_state["result"]` 是 dict 且非空 → 直接采用；否则逆序扫 `final_messages` 找**最后一条**含 `<result>...</result>` 的 `AIMessage`，`json.loads` 解析。
- **解析纪律沿 `_rebuild_*_from_messages`**：解析不出的条目跳过；**存在 `<result>` 标签却一条都解析不出时打 WARNING**（陷阱 3：禁静默吞错）；两条通道都空时打 WARNING（这一条与 `reported_metrics` 的"零指标不打 WARNING"相反——档位缺失不是合法常态）。
- **零新依赖**：正则复用 `config.REACT_RESULT_TAG_OPEN/CLOSE`（`react_base._RESULT_TAG_PATTERN` 是私有的，execution 侧按同一对常量自建一个模块级 pattern，**不 import 私有符号**，与 `reporting._resolve_report_path` 自写边界判定同一取向）。
- **`ExecAgentOutput` 扩一个字段** `report: Dict[str, Any] = field(default_factory=dict)`（默认值 ⇒ 降级路径与既有构造点天然为空，与 `reported_metrics` 的加法逐字同款）。`reported_metrics` 改为从 `report.get("metrics")` 取，**不再单独读 `final_state["result"]`**（消除两个取数口径）。

### 1.4 裁定 3：判定缺失时的终局语义——**绝不因"没读到汇报"把跑通判成失败**

这是 Q-S8-01 的产品级出口，必须写死：

| 客观事实 | agent 汇报 | 最终档位 | 依据 |
|---|---|---|---|
| `exit_ok` 为假 | 有/无 | **失败** | §4.5.3 封顶 1（客观事实压低，与汇报无关） |
| `exit_ok` 为真、步骤没跑完 | 有/无 | **仅代码跑通** | §4.5.3 封顶 2 |
| `exit_ok` 为真、步骤跑完 | **汇报缺失（两通道皆空）** | **仅代码跑通** | 本裁定：等价于"支撑物证一条都不成立"（A-S8-08），走封顶 3 |
| `exit_ok` 为真、步骤跑完 | 有汇报、物证全不过验 | **仅代码跑通** | §4.5.3 封顶 3 |

⇒ **「复现成功」→「失败」这条 PRD 点名的失真路径在架构上被物理切断**：汇报缺失只可能落到「仅代码跑通」，且它是 `auto_fixable` 的（回编码环节补产出），不是打断用户的终态。落「失败」的**唯一**入口是 `exit_ok` 为假，而 `exit_ok` 来自收集器 + 回读的真实 `exit_code`——这条链路 Sprint 4 起就没变过。

### 1.5 裁定 4：判定与物证核验必须在**同一次 `execution()` 函数体内、`_build_execution_result` 之前**完成（幂等纪律③）

- 落点顺序（在既有七步骨架里插入，不新增步骤号层级）：

  ```
  步骤 4.4  ✂ 删除  _split_reported_metrics（v2.1 写的是"保留 + 改组名语义 + 撞名处置"，
                    v2.2 整条作废：折叠动作本身就是病根，见 §16.1）
  步骤 4.5  ✂ 删除  metrics_groups（自报折叠 or 扫盘兜底，两条来源一并退场，见 §16.6）
  步骤 4.6  _reconcile_steps（位置不动）
  步骤 4.65 _audit_declared_steps（位置不动）
  步骤 4.7  _apply_incomplete_execution（保留）
  步骤 4.75 ★ 新增 _verify_evidence + _collect_result_blocks + _decide_conclusion
                    ← 本次唯一新增步骤；三者同一次调用内完成、共用同一份 agent 汇报
  步骤 4.8  ★ 新增 _apply_no_verifiable_output（取代被删的 _apply_no_metrics 的位置）
  步骤 5    _build_execution_result（多收一个 conclusion 参数，随 exec_result 一次 commit）
  ```

  🔴 **v2.2 说明：删掉 4.4 / 4.5 之后，"结果"这条数据流在 execution 侧只剩一个出口** —— `_resolve_agent_report` → `_verify_evidence`（建台账、逐条验钞）→ `_collect_result_blocks`（归一化 + 回填 `evidence_ids`）→ `_decide_conclusion`（只读 `level` + 数封顶）→ 随 `conclusion` 一次落盘。**零折叠、零扫盘、零第二条来源。** 三者顺序不可换：台账必须先于块建好，块才能回填引用。

- **磁盘同刻性**：`_verify_evidence` 在此处读盘，与 agent 跑命令是同一次节点调用、同一份 `code_output_dir` 现场。报告环节读的是已落盘的 `conclusion`，**不重算、不重读盘**（PRD §4.5.1 落点理由①的架构兑现）。
- **interrupt#2 幂等**：`_has_committed_result_for_round` guard 命中路径（`execution.py:2884-2899`）**复用已落盘 `execution_result`**，其中已含 `conclusion` 键 ⇒ 重入不重判、档位不会二次变化。**guard 函数一字不改。**
- **interrupt#3 幂等**：resume 后函数体整体重跑、子图从 checkpoint 恢复跑到 finalize，`_resolve_agent_report` 拿到完整判定，`_verify_evidence` 重新读盘一次——**这正是我们要的**（磁盘就该是收尾时刻的磁盘）。

### 1.6 怎么证明它真的不丢（验证方式，逐条可落成测试）

| # | 验证 | 构造 | 期望 | 属性 |
|---|---|---|---|---|
| V1 | 结构性回读兜底 | `final_state` 无 `result`，messages 末尾带 `<result>{...}</result>` | `_resolve_agent_report` 取出完整档位 | 覆盖 (b)(c) |
| V2 | 优先级 | 两通道都有且**内容不同** | 取 `final_state["result"]` | 单一权威 |
| V3 ★命门 | **收集器截断不改判定** | 同一份 messages 跑两遍：①收集器满载 ②收集器只留尾段（模拟 R-S4-10 resume） | **两次 `conclusion.level` 逐字相同** | 直接证否"一丢就变失败" |
| V4 | 汇报缺失兜底 | 两通道皆空 + `exit_ok=True` + 步骤跑完 | 档位 =「仅代码跑通」，`ErrorCategory.NO_VERIFIABLE_OUTPUT`，`auto_fixable=True` | §1.4 |
| V5 | 汇报缺失 + 命令跑挂 | 两通道皆空 + `exit_ok=False` | 档位 =「失败」 | 封顶 1 优先 |
| V6 | 幂等 | guard 命中路径重入 | `conclusion` 与上一次落盘**逐键相同**，`_verify_evidence` **零次调用** | 幂等纪律③ |
| V7 | 异常降级 | 子图抛非 `GraphBubbleUp` 异常 | 不炸节点、`report={}`、落 V4/V5 语义 | 覆盖 (a) |
| V8 ⚠真跑 | 端到端（AC-S8-21 内） | 跑到一半触发一次 `request_user_input` 后 resume | 最终档位非「失败」，物证路径可溯源到本次代码目录下的真实文件 | 现场证据 |

V3 建议直接用既有真跑夹具 `tests/fixtures/s713_realrun_20260802/` 重放，与 AC-S8-16 共用夹具、不新建现场。

---

## 2. Q-S8-02：状态契约（**v2.0 扩围**：跨 `ExecutionResult` 与 `ReproductionPlan` 两个结构）

> **v2.0 变更提要**：v1.0 时本节标题是"本次唯一的状态契约新增"。第四轮拍板把成功标准挪进计划后，**该表述作废**——现为**两处、共两个键**，且**上限就是两处**（见头部贯穿硬约束）。§2.1~§2.4 是执行结果侧（v1.0 原文，不变），**§2.5 是本次新增的计划侧**。

### 2.1 结论：`ExecutionResult` 只加一个键 `conclusion`，沿 `step_reconciliation` 的嵌套字典范式

```python
class ExecutionResult(TypedDict):
    ...既有 10 键一字不动...
    # === Sprint 8 新增（S8-04/05/08，架构 sp8 §2）===
    conclusion: Dict[str, Any]
```

形态（写入方单点 = execution 的 `_decide_conclusion`）：

> 🔴 **v2.2 改写**：v1.0 定的是**内联证据**形态，已被 PRD v4.0 §4.6.4 修订为**唯一证据台账 + 引用**（PRD §13 待拍板第 5 条，默认取值「修订」）。**v1.0 原文保留在本节末尾的折叠留痕里，不删。**

```jsonc
{
  "level": "复现成功",              // 四档字面量之一，就是用户可见文案本身
  "evidence_ledger": [              // 🔴 v2.2：唯一证据台账。id 由**系统**生成（"E1"/"E2"…），
                                    //    agent 一个 id 都不写；验钞每条只跑一次
    {"id": "E1", "path": "outputs/umap/summary.json", "value": "0.62",
     "source_note": "第 3 步 训练脚本产出",     // agent 自述来源，纯展示、不参与判定
     "ok": true, "reason": ""},
    {"id": "E2", "path": "…", "value": "…",
     "ok": false, "reason": "路径越出本次代码目录"},
    // 🔴 v2.3：论文值物证——出处不是产物文件，带 metric 不带 path，
    //    按 paper_analysis.baseline_results 核验两重（§16.3.2）
    {"id": "E3", "metric": "knn_accuracy", "value": "0.62",
     "source_note": "论文 Table 2", "ok": true, "reason": ""}
  ],
  "goal_checks": [                  // 逐条预期三态结论（渲染入参仍是整个 conclusion，签名不变）
    {"description": "…计划预期原文…",
     "verdict": "印证上了",         // 三态之一（字面量见 §9 复裁项 1）
     "evidence_ids": ["E1"]}        // 🔴 只持引用，不内联
  ],
  "level_evidence_ids": ["E1"],     // 支撑**档位本身**的物证引用（封顶 3 判据的输入）
  "result_blocks": [                // 🔴 v2.2 新增：结果的形状由执行环节决定（§16）
    {"title": "kNN 准确率：本次复现 vs 论文报告",
     "note": "取自第 3 步产出的 summary.json；论文值来自论文分析注入的上下文",
     "columns": ["方法", "数据集", "本次复现", "论文报告值"],
     "rows": [["UMAP", "MNIST", "0.61", "0.62"]],
     "evidence_ids": ["E1"],
     "caveats": []}                 // 块级畸形/截断标注，由收编函数填中文串
  ],
  "report_caveats": []              // 全局级标注（疑似截断 / 预算耗尽 / 结构不合法），同上
}
```

**四条形态纪律（可静态断言，落地时最容易被改回去）**：

1. **`evidence_ledger` 是全 `conclusion` 内唯一的证据数组。** **落盘后**的 `goal_checks` 与 `result_blocks` **只允许出现 `evidence_ids`**，不允许出现 `path` / `metric` / `value` / `ok` / `reason` 任何一个键 —— 一旦允许，两处就会各自演化成一份证据，**这正是 PRD §4.6.4 要防的"漂移成两套"**。
   > ⚠ **注意区分两层**：agent **汇报时**是就地写 `{path…}` 或 `{metric…}`（§16.2 schema），**收编函数把它们抽成台账并回填 `evidence_ids`** —— 上面这条纪律约束的是**落盘结构**，不是 agent 的书写形式。搞混会得出"agent 也要写 id"的错误结论（那正是 §16.3.1 否决的方案 B）。
2. **id 由系统生成、agent 不写**（§16.3）⇒ 悬空引用在结构上不可能发生。
3. **`level_evidence_ids` 与 `goal_checks[].evidence_ids` 可以指向同一条台账记录** —— 这正是台账的意义（**同一份物证只读一次盘、只验一次**）。
4. **`report_caveats` / `caveats` 里装的是**已经写好的中文句子**，渲染层原样印、零判断逻辑**（用户可见文本的措辞归 execution 侧模块常量，进术语守门面；MEMORY §4.2）。

<details><summary>v1.0 原形态（已被 v2.2 修订，保留供追溯）</summary>

```jsonc
{
  "level": "复现成功",
  "goal_checks": [
    {"description": "…计划预期原文…", "verdict": "印证上了",
     "evidence": [{"path": "outputs/umap/summary.json", "value": "0.62",
                   "ok": true, "reason": ""}]}
  ],
  "evidence": [
    {"path": "...", "value": "...", "ok": false, "reason": "路径越出本次代码目录"}
  ]
}
```

**修订理由（PRD §4.6.4，架构复核后同意）**：①粒度不同——物证的粒度是每条判断，结果块的粒度是一张表，一张表可能有 12 行来自 2 个文件；②内联导致同一个结果文件在 N 处重复出现 ⇒ **验钞读盘跑 N 次**；③逐条结论的证据与块的证据成为**两处独立数组** ⇒ 漂移被写进结构本身。

</details>

### 2.2 三个备选与取舍

| 方案 | 形态 | 优点 | 否决理由 |
|---|---|---|---|
| **A（采纳）** | 单键 `conclusion: Dict`，内含 level / goal_checks / evidence | **只加 1 个 TypedDict 键**；与 `step_reconciliation` 完全同范式（既有先例，`.get()` 防御读一条就够）；**与 `reporting._determine_conclusion` 现有返回结构 `{level, annotations, goal_checks}` 同形** ⇒ 报告侧渲染函数 `_render_goal_checks(conclusion)` **入参零改动** | — |
| B | 三个平键 `conclusion_level` / `goal_checks` / `evidence_ledger` | 扁平、读起来直白 | 三处 TypedDict 加键、三处降级构造点补默认值、三处旧快照防御读——**违反"唯一状态契约新增"**，且 `evidence` 与 `goal_checks.evidence` 语义同族却被拆散 |
| C | 复用 `metrics_groups` 塞判定 | 零新增键 | 语义污染（指标容器承载结论）；`metrics_groups` 有独立消费者（对比表），混装必然互相干扰；踩"字段被复用到变形"同一族过度设计病 |

> 🔴 **v2.2 防同名误读（PRD v4.0 §12.6 与 §14 两处点名，此处正式登记）**：**本节这个「方案 A」指的是状态契约方案（`ExecutionResult` 只加一个 `conclusion` 键），与 PRD §4.6 那个已作废的「方案 A（把维度写进组名）」是两回事、同名而已。**
> - **本节的方案 A 不受回炉影响、不得回滚。** §5.2 末句"这是选方案 A（§2.2）换来的最大红利"指的也是本节这个 —— 它说的是「`conclusion` 与 `_determine_conclusion` 现有返回结构同形 ⇒ `_render_goal_checks` 入参零改动」，**v2.2 之后这条红利依然成立**（`_render_goal_checks(conclusion)` 单参签名不变，见 §5.9）。
> - PRD §4.6 那个方案 A **已于 2026-08-05 由 Maria 拍板作废**，本文档中受其影响的三处（§1.5 步骤 4.4、§10 AR-S8-08、§12 `EXECUTION_OUTPUT_SCHEMA` 条目）**已在 v2.2 逐处标注作废**，不是漏改。
>
> 🔴 **v2.2 补一句额度口径**：**`conclusion` 的类型是 `Dict[str, Any]`，往里加子键（`evidence_ledger` / `result_blocks` / `report_caveats`）不构成状态契约新增** —— 它一个 TypedDict 键都不加、一处旧快照防御读都不多、一次迁移都不要。**所以"状态契约新增严格限两处"在 v2.2 之后仍然成立、没有被悄悄突破。** 反过来说：**顶层加键要付的账（三处构造点补默认值 + 三处防御读 + 回归面）子键一分不付**，这正是 §2.2 当初选 A 而不是 B 的收益在本版的第二次兑现。

### 2.3 档名：一套值，不做内部枚举 + 展示名两套（A-S8-05 的架构兑现）

- `level` 落盘的**字面量就是**「复现成功」/「部分复现」/「仅代码跑通」/「失败」四个中文串之一。**不引入 `ConclusionLevel` Enum、不引入 `"success"/"partial"` 之类的英文内部值。**
- 四个字面量在 `execution.py` 收敛为**四个模块级常量**（`_LEVEL_SUCCESS` 等）+ 一个 `_LEVELS: Tuple[str, ...]` 顺序元组（从高到低，供封顶做"取较低者"比较）。**封顶 = 按元组下标取更低档，不写 if 链**——这同时天然满足 ~~AC-S8-08④~~ **`AC-S8-09④`**「只压低不抬高」⟦🔴 **2026-08-11 主控订正（`P-S8-50`）**：原写 `AC-S8-08④` 是**悬空引用** —— `AC-S8-08` 只有①②（两层分离 / 禁形态分支，PRD `:478`），**根本没有④**；「agent 报低档但客观事实良好时档位不得被抬高」是 `AC-S8-09` 的第④项（PRD `:479`）。`dev-plan:1348` 写的一直是 `AC-S8-09④`（对）⇒ **两份文档对同一句话引了两个不同编号**。发现于 `T-S8-2-3` 落地时逐个核实注释里引用的编号是否真实存在⟧。
- `ui/term_map.py`：`conclusion_level:science/engineering/none` **三条整体删除**，换发为四条 **恒等映射**（key 的 value 部分与 label **逐字相同**）：

  ```python
  "conclusion_level:复现成功": "复现成功",
  "conclusion_level:部分复现": "部分复现",
  "conclusion_level:仅代码跑通": "仅代码跑通",
  "conclusion_level:失败": "失败",
  ```

  这不是"两套值"——它是同一个值，`humanize` 调用点因此**一个都不用改**，术语守门扫描面与计数保持"相等断言"闭合（AC-S8-17⑤）。**恒等映射的存在理由是保住守门通道，不是做转换**，须在 term_map 里写一行注释说明，防后人当冗余删掉。

### 2.4 旧快照防御读（R-6 范式）

- **写入方单点**：`execution._build_execution_result`（新增形参 `conclusion: Optional[Dict] = None`，落盘 `dict(conclusion or {})`，与 `step_reconciliation` 逐字同款）。
- **降级构造点同步补默认值**：`execution.py:2908-2917`（`code_output_dir` 缺失路径）补 `conclusion={}`。
- **消费侧一律 `.get("conclusion") or {}`**：旧 checkpoint（11 键 / 7 键快照）读到 `{}` ⇒ `level` 缺失 ⇒ 报告侧走"旧快照兼容分支"（§5.4），**不崩、不假装有结论**。
- **`success` 由 `level` 派生**（PRD §4.5.4）：`success = level in {"复现成功", "部分复现"}`。旧快照 `conclusion` 为空时 `success` 仍读既有 `success` 键原值（它在旧快照里是有的）⇒ 旧报告可重放。
- 🔴 **v2.4 补一条反方向的防御读（删键带来的新面，不补会被漏掉）**：**旧 checkpoint 里含有本次已删除的 `metrics` / `metrics_groups` 两键**。**这不会崩**（`TypedDict` 运行时就是 `dict`、零校验，多余键读得出来、也不必读），**但也不许有人"顺手用一下"** —— 🔴 **写死：`core/` 与 `ui/` 交付后对这两个键的读取点必须为零**（静态断言对象，与 AC-S8-20② 的"不得兜底回退"同一条防线）。**旧报告重放时结果节不渲染**（块为空 → 早退），**不得改成"旧快照就读旧键渲染旧表"** —— 那是把删掉的格子从旧数据那一侧请回来，取向与 §5.4 / AR-S8-12 逐字同源。

### 2.5 🔴 v2.0 扩围：`ReproductionPlan` 承载本篇成功标准

#### 2.5.1 结论：新增**单个字符串**字段 `success_criteria: str`

```python
class ReproductionPlan(TypedDict):
    ...既有 13 键一字不动...
    # === Sprint 8 新增（S8-01 扩围，第四批拍板，架构 sp8 §2.5）===
    success_criteria: str
```

语义：**对这篇论文而言，「论文核心结论得到印证」具体指什么。** 由论文分析 + 规划推导，经用户在计划审核页审核批准。

#### 2.5.2 三个备选与取舍（**这一节是两层分离能不能守住的关键**）

| 方案 | 形态 | 优点 | 取舍 |
|---|---|---|---|
| **A（采纳）** | `success_criteria: str` 单个字符串 | 🔴 **四档名根本不出现在计划里 ⇒ 计划在结构上就没有改动第一层的入口**——不是靠提示词去劝它别越权，是**它连能写越权内容的字段都没有**；且只加 1 个键、`.get()` 防御读一条就够 | — |
| B | `success_criteria: Dict[str, str]`（档位名 → 达标线） | 看起来"四档各有各的线"，直观 | 🔴 **否决**：四个档位名成了**计划可写的键**，计划可以增键、删键、改键名 ⇒ **越权入口是被结构造出来的**，AC-S8-08 的"越权表述无效"只能靠运行时兜，属最弱的一档防线。且四档里只有「复现成功」需要本篇达标线（见 2.5.3），另三档填什么都是重复定义、必然与第一层打架 |
| C | `List[Dict]`（`{level, criterion}` 列表） | 可扩展 | 🔴 **否决**：B 的全部问题 + 多一层容器；"可扩展"在这里是纯负债（PRD 明写不加第五档） |

#### 2.5.3 为什么一条就够（**驳"四档各要一条线"**）

四档里**只有一档需要本篇专属信息**：

| 档 | 边界由谁定 | 要不要计划填 |
|---|---|---|
| 复现成功 | 第一层定"承诺产出都落地 **且** 论文核心结论得到印证" | **要**——「印证」对本篇具体指什么，只有计划知道 |
| 部分复现 | 第一层定"**部分**预期没印证上" | **不要**——它是"复现成功"的部分否定，同一条达标线取部分即可 |
| 仅代码跑通 | 第一层定"承诺的产出没落地" | **不要**——产物有没有落地由 `deliverables` / `expected_output` 对照，与达标线无关 |
| 失败 | 第一层定"没跑通" | **不要**——`exit_ok` 客观封顶，计划写什么都压不动 |

⇒ **单条字符串既够用又天然守住两层分离。**

#### 2.5.4 🔴 两层分离的**物理落点对位表**（红线，可静态断言）

| 层 | 落在代码的哪里 | 跨论文变不变 | 谁能改 |
|---|---|---|---|
| **第一层：四档语义边界** | ①`execution.py` 四个模块级档名常量 + `_LEVELS` 顺序元组（§2.3）；②**系统提示词主体里的四档语义段**（稳定前缀，进提示词哈希基线） | **恒定** | 只有改代码 |
| **第二层：本篇达标线** | `plan["success_criteria"]` → 经 **HumanMessage 动态通道**注入执行上下文 | **每篇不同** | 规划环节写、用户在审核页改 |

**红线（AC-S8-08 的架构级断言对象）**：
1. **四档语义段必须在系统提示词里，达标线必须在 HumanMessage 里，两者不得混在同一段文本。** 混了就是：要么把第一层做成动态的（= 允许越权），要么把第二层做成静态的（= 退回硬编码）。
2. **`_decide_conclusion` 不得读 `success_criteria`。** 达标线是**给 agent 看的判断依据**，不是给代码看的判据——代码只做三条客观封顶（§4.5.3）。代码一旦开始解析达标线文本，就是在把第二层重新硬编码回代码里，直接复发病③。
3. 静态可断言：~~`success_criteria` 在 `core/` 下的出现点**只允许三处**——`state.py`（声明）、`planning.py`（生产）、`_build_execution_agent_context` + `coding.py` 上下文（注入）。~~ 🔴 **v2.6 订正（`P-S8-34`；`P-S8-10` 那次订正为"逐文件 4 处 / 加 reporting 读点共 5 处"同样漏了一处）**：全 Sprint 完成后 `core/` 下**逐文件实为 6 处**——

   | # | 文件 | 角色 | 落点批次 | 磁盘现状（2026-08-09 复核） |
   |---|---|---|---|---|
   | 1 | `core/state.py` | **声明** | 1b | ✅ 已交付（`:175`；docstring 另有两处说明性提及 `:145` / `:147`，**不计入本表**——本表数的是**功能落点**，不是 grep 行数） |
   | 2 | `core/nodes/planning.py` | **生产** | 1b | ✅ 已交付（schema `:119` / required `:133` / 提示词 `:265` / `:297` / `:302` / 构造点 `:530` / `:748`） |
   | 3 | **`core/plan_checks.py`** | 🔴 **W6 判据（本次补登记）** | 1b | ✅ 已交付（`:636` `raw_criteria = plan.get("success_criteria")`） |
   | 4 | `core/nodes/execution.py` | 注入（`_build_execution_agent_context`） | 2 | ⬜ 未交付（现 0 命中） |
   | 5 | `core/nodes/coding.py` | 注入（编码侧上下文） | 2 | ⬜ 未交付（现 0 命中） |
   | 6 | `core/nodes/reporting.py` | 读点（`_render_success_criteria`，§5.7） | 3 | ⬜ 未交付（现 0 命中） |

   ⇒ **静态断言一律按"逐文件的实际处数"写，不得照抄"三处 / 五处"这两个作废的数**（照抄会当场红）。原"只允许三处"的算法有两处病：把 `execution` 与 `coding` 两个文件并成一处、且**根本没把 `plan_checks.py` 算进去**。

   🔴 **`plan_checks.py` 那一处属"警示"不属"判定"，红线 2 未被破。** W6（§15）读 `plan["success_criteria"]` 是为了产**给人看的一条 `check_plan` 警示**，它**不阻断审批、不参与档位判定**（护栏 3 的产品决策，见 §15.1）；而红线 2 守的是**判定链路** —— 它禁的是 `_decide_conclusion`（`core/nodes/execution.py`）读达标线去解析、把第二层重新硬编码回代码。**两者物理分处两个文件、两条链路，不冲突。** ⚠ 这一句不许删：不写明，后人看到"判据里读了 `success_criteria`"就会以为红线 2 已被破，进而去把 W6 拆掉。

   **判定函数体内零出现这一条一字不改**：`_decide_conclusion` 函数体内 `success_criteria` 恒为 **0 命中**，这才是红线 2 的静态断言对象。

#### 2.5.5 默认值、防御读、required 归属

- **默认 `""`，缺键 ≡ `""`**（沿 S7-08 `scale_reduced` / `local_fit_note` 范式）；下游一律 `.get("success_criteria") or ""`，旧 checkpoint 不 KeyError。
- 🔴 **进 planning 输出契约的 `required`——这是对 S7-08 纪律 2 的有意背离，须留档**：纪律 2（新键不进 required，避免 `react_base` finalize 多烧一次 schema 重生成）的成立前提是"缺省已是安全值"。而这里缺省 `""` **不是**安全值——等于这篇论文没有判定依据，整条判定链当场断。⇒ **代价（缺失时多烧一次调用）正当且可接受**，与 `scale_reduced` 的情形性质相反，不构成对该纪律的推翻。
- **注入范式**：`_build_execution_agent_context` 末尾追加 `success_criteria`，**非空才注入**（与 `baseline_results` 同款）⇒ 无该字段的旧计划 payload 字节零扰动。`coding.py` 上下文同款（PRD §4.2 第 3 条）。
- **幂等**：它是计划字段、随计划一次落盘，execution / coding **只读不写** ⇒ 零幂等风险，不进 `_build_execution_result`。

#### 2.5.6 标准缺失时的档位语义（**是既有封顶的推论，不是新增第四条封顶**）

`success_criteria` 为空（旧计划 / 规划没写 / 用户删空）⇒ agent 没有可核验的「印证」判据 ⇒ 它所报的「复现成功」/「部分复现」**没有成立的支撑物证** ⇒ **落既有封顶 3「仅代码跑通」**（§4.5.3 第三条，A-S8-08）。

🔴 **架构在此明确不自造新规则**：不新增"第四条封顶"，因为既有第三条已经覆盖。**开发不得在代码里另写一条"标准为空则降档"的分支**——那会变成两处定义同一件事，日后必然打架。

### 2.6 🔴 v2.4：`ExecutionResult.metrics` / `metrics_groups` **删键**（Maria 推翻架构默认取值）

> **拍板**：PRD §13 第 1 条，Maria 2026-08-06。原话「**旧字段要是确认没有用了就删掉**」。
> **架构 v2.2/v2.3 的默认取值是"保留声明、停产停消费一轮"，本条被推翻。** 原表述保留在 §12 / §16.6 的划删原文里，不删。

#### 2.6.1 前置条件「确认没有用了」已核实成立

她的裁定带条件。`metrics` / `metrics_groups` 在**交付后**是否真的零生产者零消费者：

| 侧 | 今天的命中点 | 本 Sprint 去向 | 依据 |
|---|---|---|---|
| 生产 | `_collect_grouped_metrics` | **删除** | §16.6 |
| 生产 | `_split_reported_metrics`（自报折叠） | **删除** | §16.6 |
| 生产 | `_parse_metrics` 三档 | **删除** | §12 / §7 |
| 生产 | `_build_execution_result` 的两个形参 | **形参保留带默认值、调用点不再传** ⇒ 🔴 **v2.4 改为形参一并删除**（见 §2.6.3） | §12 |
| 消费 | `reporting._match_metrics_group` / `_lookup_metric_value` / `_verify_trend` | **删除** | §5.1 |
| 消费 | `reporting._render_metrics_comparison` | **删除** | §5.9 第 1 条 |
| 消费 | `execution._apply_no_metrics` | **删除** | §12 |
| 消费 | `ui/pages/result_report.py:178` `_metric_comparison_rows` | **整体替换** | §12 `ui/` |

⇒ **交付后 `core/` 与 `ui/` 对两个键的生产与消费全部归零，条件成立。**

#### 2.6.2 🔴 复核 v2.3 那笔「71 处回归面」的账 —— **算高了，且高得多**

> v2.3 在 §11 前置③ 里给的保留理由是「`grep metrics_groups tests/` 上百处回归面，不成比例」。**主控质疑这笔账、要求上磁盘复核。复核结论：质疑成立，我那笔账错了。**

**实测**：`metrics_groups` 在 `tests/` 下共 **71 处 / 18 个文件**（数字本身对）。但**按"删键相对停产的净增量"分类后，71 这个数字几乎全部与本议题无关**：

| 类 | 含义 | 删键 vs 停产的差别 | 实例（已逐条上磁盘核对） |
|---|---|---|---|
| **甲：内容断言** | 断言 `metrics_groups` 的**取值** | 🔴 **零差别** —— 字段**停产之后**（不再被任何生产代码写入）这些用例照样红，TypedDict 里那行声明留不留**完全不影响** | `test_sprint7_s713_reported_metrics.py` 全部 18 处（`:318` `er["metrics_groups"] == {...}`、`:385` 与 `_collect_grouped_metrics` 对比、`:1146`/`:1254` 夹具比对等）；`test_sprint5_t26_grouped_metrics.py:207`/`:353`/`:368`；`test_sprint5_t33_conclusion.py` 的 trend 回验组 |
| **乙：构造夹具里填了一个键** | `{"metrics_groups": {}, …}` 字面量 | 🔴 **零差别** —— **TypedDict 运行时就是普通 dict、零校验**，字面量里多一个已删的键**不报错、不变红** | `test_sprint7_s7_02_coding_feedback.py:68`、`s711_gap_audit.py:624`、`s7_01_budget_gate_sink.py:57`、`s7_02_persist_log.py:302`、`s711_actionable_denominator.py:315`、`test_sprint6_b2.py:581`/`:663`/`:735` 等 |
| **丙：精确键集合断言** | `set(er.keys()) == {…11 键…}` / `set(ExecutionResult.__annotations__) == {…}` | 🔴 **零净增量** —— **本 Sprint 因新增 `conclusion` 已经必红**；删两个键只是把同一行的期望集合从"11+1"改成"9+1"，**同一处改动、同一行** | `test_sprint4_e3.py:572-576`；`test_sprint7_s711_completion.py:458-462` 与 `:774-778`；`test_sprint5_t26_grouped_metrics.py:53-57`（`_EXPECTED_RESULT_KEYS`，用在 `:350`/`:365`） |
| **丁：`.get()` 防御读 / 负向断言 / docstring** | `exec_result.get("metrics_groups", {}) == {}`、`forbidden_fields` 元组、注释 | 🔴 **删键后仍绿** —— `.get` 有兜底；负向断言在字段消失后**空真成立** | `test_sprint5_t12_state.py:185`、`:221`、`:13` |
| 🔴 **戊：真正的净增量 —— 类型签名断言** | 断言键存在 / 类型 / **源码字符串** | **删键才红，保留声明则绿** | **只有 4 处，全在 1 个文件**：`test_sprint5_t12_state.py:99`（`"metrics_groups" in ann`）、`:108`（`== typing.Dict[str, typing.Dict[str, typing.Any]]`）、`:242`（`assert "metrics_groups: Dict[str, Dict[str, Any]]" in src`）、`:269`（`get_origin(...) is dict`） |

**⇒ 删除相对保留的真实净增量 = 4 处断言 / 1 个测试文件**，不是 71 处、也不是"上百处"。

**另外两笔本以为要付的账，实测也不用付**：

- **mypy 面：零新增错误。** `mypy.ini:43` `files = core` ⇒ **它根本不检查 `tests/`**，那 71 处与 mypy 无关；而 `core/` 内所有读取点都在本 Sprint 的删除清单里（上表）⇒ 删键后无残留引用。
- **旧 checkpoint 反序列化：不受影响。** `TypedDict` 运行时就是 `dict`、零校验，旧快照里多两个键读得出来、少两个键 `.get()` 兜得住 —— **这也是 §7「`ErrorCategory.NO_METRICS` 成员必须保留」那条裁定不受本次影响的原因**：那条治的是 `Enum(值)` 反序列化会 `ValueError`，**TypedDict 没有这回事，两者不是一类问题，不可类推。**

🔴 **这笔账错在哪（记下来，比结论有用）**：我把「`grep` 命中数」直接当成了「改动面」。**命中数 ≠ 改动面** —— 71 处里绝大多数**在停产那一刻就已经要改了**，把它们记在"删键"头上，等于**把一笔本来就要付的账重复计了一次**，用来支撑"不要删"。**下次给改动面报数，必须先按"这条改动 vs 不做这条改动"做差，不能拿总命中数当差值。**

#### 2.6.3 落点

```python
class ExecutionResult(TypedDict):
    """…docstring 同批改写…"""
    success: bool
    # metrics: Dict[str, Any]                          ← 🔴 v2.4 删除
    logs: str
    errors: List[str]
    artifacts: List[str]
    runtime_seconds: float
    environment_info: Dict[str, str]
    step_reconciliation: Dict[str, Any]
    budget_truncated: bool
    # metrics_groups: Dict[str, Dict[str, Any]]        ← 🔴 v2.4 删除
    degraded_credentials: List[str]
    conclusion: Dict[str, Any]                         # Sprint 8 新增
```

- **`core/state.py:175` `metrics` 与 `:183` `metrics_groups` 两行删除**；`:170` 那条 `metrics_groups` 的 docstring 说明**随之删除**（v2.2 裁的是"改写为停产说明"，🔴 **v2.4 改判为删除**）。
- **docstring 同批加一段留痕**（沿 sp5/sp7 加键注释体例）：「Sprint 8 删除 2 键（`metrics` / `metrics_groups`）—— 三档 `<METRICS>` 通道与分组折叠一并退场，本次执行结果改由 `conclusion.result_blocks` 承载」。**留痕必须在文件里**，否则后人读旧 checkpoint 看到两个不在声明里的键会以为是脏数据。
- 🔴 **`_build_execution_result` 的两个形参一并删除**（v2.2 裁的是"保留带默认值、调用点不再传"）。**改判理由**：既然键都没了，留两个永远不被传、传了也无处可落的形参就是**没有消费点的空壳**，与 `metrics[].source` 被砍时逐字记下的理由同款（`execution.py:1083-1089`）。`:2419` docstring 里点名 `_collect_grouped_metrics` 的那句**随之删除**（v2.2 裁的是"改写"）。
- **`ExecutionResult` 键数账**：今天 11 键 → 删 2 加 1 → **10 键**。⇒ 🔴 **四处精确键集合断言必须同批换发**（清单见 §2.6.2 丙类），**禁止把 `==` 放宽成 `>=` 或"包含"来规避**（同 AC-S8-21 的既有红线）。

#### 2.6.4 🔴 两处被突破的自设约束：显式处置，不默默绕过

| # | 被突破的东西 | 性质判定 | 处置 |
|---|---|---|---|
| 1 | 本文档头部贯穿硬约束「**状态契约新增严格限两处**」 | ⚠ **字面未破、隐含含义已破**：删除不是"新增"，两处新增仍然只有 `conclusion` + `success_criteria`。但这句话的**实际用意**是"本 Sprint 状态契约的改动面严格受控" | **改写为「新增严格限两处 ＋ 删除两处」**（头部已改），并在此留痕：**上限的性质没变（仍是硬上限、仍不许再加第三处新增）**，变的是它现在还管住了删除面。**开发不得以本次删除为先例扩大任何其它状态改动。** |
| 2 | `docs/sprint8/dev-plan.md:1358` **CP-2.10-3「类型签名逐字未变」** | 🔴 **真突破**：删两行签名，该检查点当场不成立 | **判定：推翻该检查点，换发，不是放宽。** ⚠ **本文档不改 `dev-plan.md`（铁律：架构只改本文档）** ⇒ **交主控派开发跟改**，换发口径写死为：「`ExecutionResult` 由 11 键变为 10 键（删 `metrics` / `metrics_groups`，加 `conclusion`）；**其余 8 键的签名逐字未变**」。🔴 **禁止直接删掉 CP-2.10-3 了事** —— 那样"其余键没被顺手改动"就没有任何检查点守着了，而那才是这条检查点真正的价值 |

#### 2.6.5 一条顺带发现（连带面，交开发核对）

**`mypy.ini` 的债务清单逐行标注了行号，本 Sprint 大改后大面积失真** —— 例如 `:124` 记的 `core.nodes.execution` 的 `call-overload ×1 L520`，**L520 正在 `_parse_metrics`（`:517-550`）函数体内，该函数本次整体删除** ⇒ 该 code 很可能不再被触发；`:146` 记的 `core.nodes.reporting` `var-annotated ×5 L354/495/814/930/995` 里，**L995 在 `_render_metrics_comparison` 内，同样本次删除**。

- **功能上不会红**（`disable_error_code` 是文件级，多压制不报错；`warn_unused_ignores = False`，`warn_unused_configs` 只管配置节不管 code）。
- 但 `mypy.ini:23-27` 自己立的 **ratchet 规矩**是「债务清单里每一行都是一条可以删掉的 TODO……只准往严了走」⇒ **本 Sprint 是一次零成本收紧的机会**：交付后重跑 `.venv/bin/mypy`，把不再触发的 code 从清单里删掉。
- ⚠ **架构不裁"必须收紧"**（那是开发计划的排期问题，且不属本文档射程）；**这里只登记事实与机会**，交主控转开发。**至少要做的是：行号注释同批订正，否则它就是一条新的"文档与代码不符"。**

---

## 3. Q-S8-03：证据路径限死本次代码目录（与工具层边界是两个闸，不许合并）

### 3.1 结论：验钞函数内联自判，工具层一字不动

```
_verify_evidence(evidence_item, code_output_dir, extra_commands, baseline_results) -> (ok, reason)
    产物物证（带 path）→ 既有五重：
      ①路径真实存在  ②可读  ③数值前缀匹配可查  ④落在 code_output_dir 之下  ⑤未在计划外命令参数里字面出现
    🔴 论文值物证（带 metric，v2.3 新增）→ 另两重：
      ①metric 能在 baseline_results 里查到（精确匹配）  ②value 与该键的值双向前缀匹配
```

> 🔴 **v2.3 两处跟改**：①**形参多一个 `baseline_results`**（`state["paper_analysis"]["baseline_results"]`，`core/state.py:80`）——它是纯入参、不是新读盘面，**本节"工具层一字不动 / 两个闸物理分处两文件"的裁定完全不受影响**；②**上面这五重只管产物物证**，论文值物证走 §16.3.2 的两重。**"什么能当判定物证"这个闸的性质没变，只是它现在知道有两种钞票。**

第④重的实现 = **4 行自写**，与 `reporting._resolve_report_path`（`reporting.py:371-372`）、`code_fs_tools._is_within_base`（`:82-91`）**同一判定路径**（`resolve()` 后 `== base or is_relative_to(base)`）：

```
resolved = Path(candidate).resolve(); base = Path(code_output_dir).resolve()
ok = (resolved == base or resolved.is_relative_to(base))
```

### 3.2 备选对比

| 方案 | 做法 | 取舍 |
|---|---|---|
| **A（采纳）** | 验钞函数内联自判，`import` 一个都不加 | 零耦合；`reporting` 已有同款自写先例；**物理上不可能影响工具层** |
| B | `from core.tools.code_fs_tools import _is_within_base` 复用 | 跨模块 import 私有符号；且会造成"改工具层边界会连带改判定"的隐性耦合——**恰恰是本项最要提防的事** |
| C | 给 `make_read_code_file_tool` 加 `base_dir` 参数、在工具层收窄 | 🔴 **直接违反 PRD §4.3**：执行环节要读参考仓库诊断问题，收窄工具层会把这个能力砍掉。**明确否决** |

### 3.3 两个闸的边界表述（须逐字进开发交接文档，防落地时被合并）

| 闸 | 管什么 | 落点 | 边界 |
|---|---|---|---|
| 工具边界 | **agent 能读什么** | `code_fs_tools._is_within_workspace`（`:71-79`） | **整个工作区**（含参考仓库 `selected_repo.local_path`）——**本次一字不改** |
| 证据边界 | **什么能当判定物证** | `execution._verify_evidence` 第④重 | **仅 `code_output_dir` 之下** |

⇒ agent 读参考仓库里的结果表 **不被拒绝**，但**拿它当物证一律不成立**（R-S8-03 的落地形态：堵的是"从官方仓库抄一个对得上的数"）。**测试须有一条正向用例证明"读参考仓库成功"与一条负向用例证明"引用参考仓库路径作物证不成立"同时为真**——两条一起才叫验完（AC-S8-05④ + AC-S8-04）。

### 3.4 第⑤重的数据源

`extra_commands` 取 `step_reconciliation["extra_commands"]`（`execution.py:2004` 已产出，**只查计划外命令**）。匹配口径：**字面子串包含**（证据路径的原样串出现在任一条计划外命令的任一参数里即判不成立）。计划步骤写出的文件完全不受影响 ⇒ 正常复现零误伤（PRD §4.9.5 措施 3）。

---

## 4. Q-S8-04：新错误类别的早停范式

### 4.1 结论

| 项 | 裁定 | 理由 |
|---|---|---|
| 错误类别 | **新增 `ErrorCategory.NO_VERIFIABLE_OUTPUT`**（用户可见文案："跑通了，但计划里说好要产出的东西没落地"） | PRD §4.5.4 第 2 条；`execution.py:152-156` 已为 `INCOMPLETE_EXECUTION` 写死同款理由（"对用户撒谎比技术债更贵"） |
| 是否进 `AUTO_FIXABLE` | **进**（`auto_fixable=True`） | 产出没落地正是编码环节能修的（PRD §4.5.4 第 1 条） |
| 早停轮数常量 | **复用 `NO_METRICS_EARLY_STOP_ROUNDS` 现有取值，config.py 零新增** | 语义继承（同为"连续同类无进展"）；新增第二个常量是无消费差异的重复抽象（MEMORY §4.1） |
| config 常量改名 | **不改** | 常量名不是用户可见文本（MEMORY §4.2 不适用）；改名收益为零、回归面为正。在 execution 侧消费点加一行注释说明现语义 |
| 早停判定函数 | `_no_metrics_stalled` → 改名 `_no_progress_stalled`，**匹配类别改为新类别**，函数体结构一字不动 | 单点谓词，改一处 |
| 优先级顺序 | **`:2817-2840` 的 elif 链顺序一字不动**：早停 > 预算耗尽 > 子预算触顶 > 不可修复 > 修复耗尽。新类别早停**原位继承**旧早停的位置 | 原论据（"早停是更具体的无进展语境"，`:2825-2826` 逐字记着）在新类别下同样成立 |
| 终态文案 | `_NO_METRICS_EARLY_STOP_SUMMARY`（`:2715-2718`）**必须改写** | §3 核对表 #4 点名："请检查执行步骤或更换论文"对纯定性论文是错误建议，踩 MEMORY §4.2 |

### 4.2 触发条件（唯一，写死）

`exit_ok ∧ feedback.category == NONE ∧ level == "仅代码跑通"` → 改判 `NO_VERIFIABLE_OUTPUT`。

- 结构与 `_apply_incomplete_execution` / 已删的 `_apply_no_metrics` **逐字同款**（纯函数、命中才改判、其余原样返回）。
- **顺序即优先级**：排在 `_apply_incomplete_execution` **下游**（步骤 4.8）⇒ "步骤没跑完"命中后 category 不再是 `NONE`，本函数自动让位，报的是真因而不是果（沿 Q-S7-30 的既有裁决，`:2204-2206`）。

---

## 5. Q-S8-05：报告侧的收敛面 + 审计的双落点

### 5.1 报告侧函数的去留（**v2.2 由三个扩为七个**）

| 函数 | 裁定 | 理由 |
|---|---|---|
| `_verify_trend`（`:179-198`） | **整体删除** | 复裁 6，Maria 知情后拍板 |
| `_lookup_metric_value`（`:160-176`） | **整体删除** | 唯一调用点是 `_verify_trend`；留着就是死代码 |
| `_match_metrics_group`（`:136-157`） | **整体删除** | 同上；且它的"归一化模糊匹配"正是 S7-13 真跑挖出的歧义源 |
| `_normalize_group_key`（`:130-133`） | **随之删除** | 上两者的唯一依赖 |
| `_verify_expected_results`（`:201-242`） | **退化保留**，语义收窄为「旧快照兼容读」 | 见 §5.4 |
| `_determine_conclusion`（`:245-324`） | **改名 `_assemble_conclusion`，判定职责退场** | 见 §5.2 |
| 🔴 **`_render_metrics_comparison`（`:949-1008`）** | **v2.2 改判：整体删除**（v2.1 裁的是"改两处组名说明 + 复现侧无数据时不渲染主实验表"） | 它整个函数体就是**代码预设格子**：`:938` 写死表头「\| 指标 (Metric) \| 论文 baseline \| 本次复现值 \|」、`:986` 写死「### 主实验指标」、`:993` 写死「### 分组实验指标」、`:995` 写死组名说明、`:997` 用 `sorted()` 替 agent 决定组的先后。**Maria 点名的就是这一段。** 见 §5.9 |
| 🔴 **`_comparison_table`（`:931-946`）** | **v2.2 新增：随之删除** | 三列表头的唯一生产者（`:938`），唯一调用点是 `_render_metrics_comparison`（`:988` / `:1003`）⇒ 留着就是死代码 |
| 🔴 **`_flatten_mapping`（`:474-486`）** | **v2.2 新增：随之删除** | 唯一调用点是 `_render_metrics_comparison`（`:963` / `:968` / `:998`）⇒ 同上。⚠ **`_flatten_entries`（`:440`）与 `_fmt_metric_value`（`:415`）不删** —— 它们另有消费者（`_render_environment_lines:924-925`、`:898` 运行时长），且 `_flatten_entries` 正是"渲染路径一直是通用的"那个函数（§14.3.0） |
| 🔴 **`_render_goal_checks`（`:707-749`）** | **v2.2 扩围：新增"按台账渲染证据引用"职责** | v2.1 只裁了"icons 三 key 换发 + `:722-723`/`:741-747` 文案改写"；台账化之后它还要把 `evidence_ids` 查回台账、把**引用到未过验证据的条目显著标注**。**签名 `(conclusion)` 单参不变**（台账就在同一个 dict 里）⇒ §2.2 方案 A 的红利保住 |

### 5.2 `_assemble_conclusion`：报告侧只剩"取 + 算标注"

```
新职责（三件事，一件不多）：
  1. level / goal_checks ← state["execution_result"]["conclusion"]（.get() 防御读）
  2. annotations ← 既有四条标注逻辑（credential_degraded / incomplete_execution /
     scale_reduced / simulation）—— 除审计那半句外，一字不动
  3. 组装 {"level", "annotations", "goal_checks"} 返回 —— 返回结构与今天逐字相同
```

⇒ `_render_report` / `_render_goal_checks` / `_render_annotation_notices` 的**入参契约零改动**，改的只有各自的文案（§5.5）。这是选方案 A（§2.2）换来的最大红利。

**AC-S8-06③"报告环节不再自行判定档位"的负向断言落点**：`_assemble_conclusion` 函数体内**不得出现任何 `level = ...` 的条件赋值**（今天 `:313-322` 那段 if/elif/else 整段删除）。静态审查即可断言。

### 5.3 「符合」三个消费点的改造

| 消费点 | 现状 | 改造 |
|---|---|---|
| `:317`（档位判定 `all(check == "符合")`） | 判定门槛 | **随 `:313-322` 整段删除**——判定已搬到执行环节 |
| `:728`（`_render_goal_checks` 的 icons 表） | `{符合: ✅, 不符: ❌, 未验证: ⚠️}` | **三个 key 换发为新三态字面量**（值不变，仍是三个 emoji）。`.get(verdict, "⚠️")` 兜底一字不动 ⇒ 旧快照里的旧字面量渲染成 ⚠️ 不崩 |
| `:741`（回验小结 `all(v == 符合)`） | 汇总口径 | 改为按新三态字面量汇总；**小结文案须改写**（§5.5），不得再出现"科学复现（完全成功）" |

三态字面量在 `reporting.py` 已是三个模块常量 `_VERDICT_MATCH/_MISMATCH/_UNVERIFIED`（`:125-127`）⇒ **只改三个常量的取值 + 一处 icons key**，改动面收敛在 4 行。执行侧则把同三个字面量落进 `EXECUTION_OUTPUT_SCHEMA` 的 `verdict` 字段 `enum`（**这是 JSON Schema 的取值约束，不是 Python Enum 类，不算新枚举抽象**）。

### 5.4 `_verify_expected_results` 退化为旧快照兼容读

- **新快照**（`conclusion.goal_checks` 非空）：报告侧**根本不调它**，直接用执行环节判出的三态。
- **旧快照**（`conclusion` 为空 / 旧 checkpoint）：调它，`trend` 相关分支已随 `_verify_trend` 删除 ⇒ **所有条目一律落"无法核实"**，如实标注"本次结论来自旧版本记录，未做逐条核实"。函数体因此从 42 行缩到约 15 行（三个形态分支保留：dict / list / 其它）。
- **绝不为了让旧报告好看而在报告侧重新判定**——那正是 AC-S8-06③ 要禁的。

### 5.5 用户可见文案换发清单（Q-S8-08 展开，全部进 `tests/test_s708_user_text_guard.py` 守门面）

| # | 位置 | 现文案问题 |
|---|---|---|
| 1 | `reporting.py:560-563` `_SUCCESS_CRITERIA_NOTE` | 逐字描述的是旧三合取判据（"至少解析出 1 个指标"），四档制下整条失真 |
| 2 | `reporting.py:744-747` 回验小结 | "整体结论不作科学复现（完全成功）级别的宣告"——档名已作废，且报告侧不再宣告档位 |
| 3 | `reporting.py:612-613` 重要声明导语 | "结论口径已据此降档"——annotations 不再降档（档位由执行环节判），这句变成假话 |
| 4 | `reporting.py:722-723` 回验表导语 | "回验为确定性比较，仅依据本次执行解析出的指标，绝不猜测"——判者已换成 agent，须如实改为"由执行环节逐条判断并交出物证，系统核验物证真伪" |
| 5 | `execution.py:2715-2718` 早停终态 | "更换论文"对定性论文是错误建议（§4.1） |
| 6 | `ui/term_map.py:84-86` | 三条档名换发四条（§2.3） |
| 7 | 审计命中节文案（新） | 须中性，见 §5.6 |

~~另有两处**非文案但同源**的说明须一并订正（PRD §4.7 第 1 条已点名）：`reporting.py:955` 与 `:995` 的"组名为产物目录相对路径"、`core/state.py:170` 的同款注释——方案 A 之后组名由 agent 按计划写法填，与目录无关。~~

🔴 **v2.2 改判（原文保留划删）**：这三处**不是"改措辞"，是随所在物一起消失**——
- `reporting.py:955`（函数 docstring）与 `:995`（正文说明）**随 `_render_metrics_comparison` 整体删除**（§5.9 第 1 条）；
- ~~`core/state.py:170` 的注释**改写为"Sprint 8 起停产停消费，仅供旧快照反序列化"**（§12）~~ 🔴 **v2.4 改判：随 `metrics_groups` 键一并删除**（§2.6.3）——「组名」这个概念在本版之后整个不存在了（分组折叠已删，§16.6），**连承载它的键也不存在了**。
- ⚠ **`:938` 那行把内部词 `baseline` 直接暴露给用户的表头（PRD §4.7 顺带点名、违反 MEMORY §4.2）同理**：不是改文案，是**这行字不存在了**。⇒ **文案换发清单里不新增这三项**，测试也不要去找它们的"新文案"。

### 5.6 审计的双落点（裁定 2 的落地形态）

**(A) 进 agent 上下文**

- **落点**：`execution._run_execution_agent` 在构造 context **之前**调 `audit_code_dir(work_dir)`（`core/honesty_audit.py:528` 现成，纯静态 AST 扫描、零 LLM、零网络、同输入同输出、目录不存在自带容忍），结果作为第 4 个入参传给 `_build_execution_agent_context`。
- **注入范式**：沿"非空才注入"——**只在 `hits` 非空时**注入 `payload["code_audit_findings"]`（含 rule / file / line / snippet，`snippet` 在审计内部已过 `mask_value`），clean 或未审计时**不注入** ⇒ 与基线字节零扰动。
- **提示词措辞（R-S8-13 的直接对冲，必须写死）**：告知 agent 这是"代码里发现的若干写法，供你结合上下文判断，**命中不等于造假**"，并**明确点出**"把论文报告值写进代码做对照是复现的正当写法，会命中本项"。⚠ **不得**写成"怎么写才不被审计命中"（PRD 非目标 5）。
- **异常兜底**：`audit_code_dir` 抛异常 → try/except 吞掉 + WARNING + 视同未审计，**绝不阻断执行**（沿 `_persist_round_log` 的 R-S7-4 兜底范式）。

**(B) 进报告渲染**

- `reporting()` 的 `audit_code_dir(code_output_dir)` **调用点、次数、返回契约一字不动**（CP-C2-5 红线：reporting 纯读、只返 3 键）。
- **改的是消费方式，两条**：
  1. `_determine_conclusion` → `_assemble_conclusion` 中 `:281-282` 的 `audit_hits` 析取项**删除** ⇒ `simulation` 标注恢复为"只由 `simulation_notice` 触发"（那本来就是它的原意）。
  2. 审计命中**脱离 `simulation` 小节、独立成节**：`:629-652` 的 hits 表整体搬进新纯函数 `_render_audit_findings(audit)`，在 `_render_report` 中与 `_render_annotation_notices` **并列调用**；`hits` 空 / audit 为 None → 返回 `[]`（零扰动早退，与 `_render_annotation_notices:604` 同款）。
  - 新节文案要点：中性标题（不用"⚠️ 重要声明"那一档）、明说"以下写法**不影响本次结论档位**"、明说"命中不等于造假，常见正当写法（如把论文报告值写进代码做对照）也会命中"。
- **为什么不把审计结果塞进 state 让两边共读**：那会突破 Q-S8-02 的状态契约新增上限（v2.0 后为两处，且两处都已被 `conclusion` 与 `success_criteria` 占满）。`audit_code_dir` 是**同一目录 → 同一结果的确定性纯函数**，且最后一次 execution 之后不再有 coding 改代码（路由：成功 → reporting）⇒ 两次独立调用结果必然一致。代价是每回合多一次 AST 扫描（纯本地、无 LLM、无配额），可接受。

### 5.7 🔴 v2.0 扩围：报告须展示本篇成功标准

**结论**：新增纯函数 `_render_success_criteria(state)`，数据源 `state["reproduction_plan"].get("success_criteria")`，空则返回 `[]`（零扰动早退，与 `_render_annotation_notices:604` 同款）。

| 项 | 裁定 | 理由 |
|---|---|---|
| **位置** | **紧接「复现结论」档位之后、「计划目标回验」之前** | 用户的阅读顺序应当是"判了哪一档 → 这一档是按什么标准判的 → 逐条对照"。放在回验之后就成了事后解释 |
| **加工** | 🔴 **原文照登：不摘要、不截断、不改写** | 它是**用户批准过的原文**，任何二次加工都等于篡改判定依据。超长时用 Markdown 引用块原样展示，不加省略号 |
| **措辞** | 须明说**这份标准来自你审核批准过的复现计划** | 责任链闭合（PRD §4.1.2 第 3 点）。**不得写成"系统认为"或"系统判定标准"**——那是把用户批准过的东西说成系统的，既不实也卸了责任链 |
| **旧快照** | 字段缺失 → 整节不渲染 | R-6 范式，旧报告可重放 |
| **界面结果页** | **本次不扩** | PRD Q-S8-05 只要求报告；`ui/pages/result_report.py` 本批已有多处改动，不再扩面（若 Maria 要求，属追加，非本裁定的遗漏） |

⚠ **与 §5.2 的关系**：`_render_success_criteria` **不进 `_assemble_conclusion`**，它是独立渲染函数、直接读计划。理由同 §2.5.4 红线 2——报告侧也不得解析达标线文本，只负责原样呈现。

### 5.8 🔴 v2.2 新识别：结果块在报告里的位置，以及 degraded 形态的一个真缺口

**磁盘事实（三条，均已复核）**：

1. `_render_metrics_comparison` 在全仓**只有一个调用点**：`reporting.py:880`，在 `_render_full_success` 里。
2. `_render_degraded`（`:1045-1105`）**只渲染** `_render_goal_checks`（`:1060`）+ `_render_step_reconciliation`（`:1061`）+ 降级原因（`:1063` 起）+ node_errors + 修复历程 + 保留代码 —— **一个指标 / 结果章节都没有**。
3. `_determine_report_form`（`:92-106`）：`success is True` → `full_success`，其余 → `degraded`。

**推论（这是本版新挖出来的问题）**：四档派生的成功布尔（PRD §4.5.4）里，**「仅代码跑通」→ `success=False` → `degraded`**。而「仅代码跑通」恰恰是"命令跑通了、产物可能确实写出来了、只是没达标或步骤没跑完"的那一档。⇒ **照 v2.1 的落点原样搬，会出现：执行环节辛辛苦苦汇报的结果块，在最需要看它的那一档里整节消失。**

⚠ **注意这不是新引入的 bug** —— 今天 degraded 就不渲染指标对比表。但**今天它无伤大雅**（degraded 意味着基本没结果），**本版之后它变致命**：块是结果的**唯一**载体（`metrics` / `metrics_groups` **v2.4 起直接删除**，§2.6），块不渲染 = 用户看不到任何结果。

**裁定**：

| 形态 | 渲染结果块？ | 理由 |
|---|---|---|
| `full_success` | **是** —— `:880` 的 `_render_metrics_comparison` 原位换成 `_render_result_blocks(conclusion)` | 原位替换，报告结构零扰动 |
| `degraded` | 🔴 **是（新增一处并列调用）** —— 插在 `_render_step_reconciliation`（`:1061`）之后、"降级原因"（`:1063`）之前 | 上述推论。位置选择沿 sp5 T-S5-3-4 的既有理由（回验/对账置于"降级原因"之前，且不扰动既有表格行数断言） |
| `code_only` | **否** | `_is_code_only` 意味着**根本没走 execution**（`:95-96` 逐字写着）⇒ `conclusion` 里不可能有块。**不是遗漏，是前提不成立** |

⇒ `_render_result_blocks` 的**空块早退**（返回 `[]`）因此必须做对：degraded 路径上块为空是常态（跑挂了），**不得印一个空的"## 实验结果"标题**。与 `_render_annotation_notices:604` 的零扰动早退同款。

### 5.9 🔴 v2.2 重裁 Q-S8-05：与 v2.1 原裁定的逐条差异

> **为什么要重裁**：v2.1 的 Q-S8-05 是**围绕"保留指标对比表、只改它的措辞和数据源"**做的。PRD v4.0 把报告侧从"代码预设表头 + agent 填格子"倒转为"agent 决定怎么呈现 + 代码通用渲染"⇒ **原裁定的对象（那张表）本身要没了**，措辞怎么改都不成立。**下表逐条列差异，原裁定文字全部保留在 §5.1 / §12 里并就地标注作废，不删。**

| # | 事项 | v2.1 原裁定 | 🔴 v2.2 重裁 | 差异性质 |
|---|---|---|---|---|
| 1 | `_render_metrics_comparison`（`:949`） | 保留；改 `:955` / `:995` 两处"组名为产物目录相对路径"的说明 | **整体删除** | **改判**（改措辞 → 删函数） |
| 2 | `_comparison_table`（`:931`）与它写死的三列表头（`:938`） | 未提及（默认保留） | **随之删除** | **新增删除面** |
| 3 | `_flatten_mapping`（`:474`） | 未提及（默认保留） | **随之删除**（唯一消费者是第 1 条） | **新增删除面** |
| 4 | `:938` 表头把内部词 `baseline` 暴露给用户（PRD §4.7 顺带点名，违反 MEMORY §4.2） | 未登记 | **随整表删除而消解** —— 不是"改文案"，是**这行字不存在了** | **议题消失** |
| 5 | PRD §4.7 第 3 条「复现侧无数据时不渲染整列空白的主实验表」（原 `:980-988` 的缺陷） | v2.1 §12 列为"复现侧无数据时不渲染主实验表" | 🔴 **议题随主实验表一起消失** —— 主实验表不存在，"整列空白"无从发生 | **议题消失（不是漏做）** |
| 6 | `:997` 的 `sorted()` 替 agent 决定组的先后 | 未登记（v2.1 没意识到它是"代码替 agent 决定形状") | 🔴 **随删除消失；新函数按 `result_blocks` 数组顺序渲染，全函数体内不得出现 `sorted()`** | **新增红线** |
| 7 | 报告侧结果呈现的数据源 | `exec_result["metrics"]` + `exec_result["metrics_groups"]` | 🔴 **`conclusion["result_blocks"]`**；~~`metrics` / `metrics_groups` **停产停消费**~~ ⇒ 🔴 **v2.4：两键直接删除**（Maria 2026-08-06 推翻默认取值，§2.6） | **改判** |
| 8 | 新函数 | 无 | 🔴 **新增 `_render_result_blocks(conclusion) -> List[str]`**（§16.5） | **新增** |
| 9 | `_render_goal_checks`（`:707`） | icons 三 key 换发 + `:722-723` / `:741-747` 文案改写 | **上述全部保留**，**再加**：按 `evidence_ids` 查台账渲染、引用到未过验证据的条目显著标注 | **扩围** |
| 10 | 渲染入口 | `_render_full_success:880` 一处 | 🔴 **`full_success` + `degraded` 两处**（§5.8） | **扩围** |
| 11 | 界面结果页（`ui/pages/result_report.py`） | v2.1 §12 写"`:178` 数据源从 `metrics` 改读 `metrics_groups`" | 🔴 **整条作废**（那是接到一个已停产的字段上）。改为读 `conclusion["result_blocks"]` 逐块出表，`_metric_comparison_rows`（`:163-201`，`:196-198` 同样写死三列）整体替换 | **改判** |
| 12 | 三个已裁的删除（`_verify_trend` / `_lookup_metric_value` / `_match_metrics_group` / `_normalize_group_key`） | 全删 | **不变** | 不变 |
| 13 | `_assemble_conclusion` / `_verify_expected_results` / 审计双落点 / `_render_success_criteria` | §5.2 / §5.4 / §5.6 / §5.7 | **一字不变** | 不变 |

🔴 **第 5 / 6 条要单独喊一声**：它们是"**议题随前提消失**"而不是"被忽略"。**开发与测试不要去找它们的落地物**——找不到是对的。这与 PRD §4.6.6 里「撞名两条都丢弃这条处置随折叠一起消失」是同一种情形，登记在此以免日后被当成漏做。

---

## 6. Q-S8-06：论文报告值注入的字节影响

### 6.1 结论

| 项 | 裁定 |
|---|---|
| 数据源 | `state["paper_analysis"]["baseline_results"]`（`paper_analysis.py:224` 产出；execution 侧今天零命中） |
| payload 键名 | **`baseline_results`**，与 state 同名透传（既有 payload 键全是英文机器键，模型可读；不另起名，省一层映射） |
| 注入条件 | **非空才注入**（`isinstance(dict) and 非空`），与 `credential_degradations` / `scale_reduced_directive` / `expected_results` 三处先例逐字同款 |
| 通道 | HumanMessage（`json.dumps(sort_keys=True)` 字节幂等），系统提示词主体不因本项改动 |
| 送多少 | **只送 `baseline_results`，不送整个 `paper_analysis`**（A-S8-07，反过度工程） |
| 配套约束 | 提示词须明说「论文没报这个数也是合法结论，不得硬凑一个"对上了"」（PRD §4.10 第 4 条 / R-S8-03） |

### 6.2 字节基线与 Prompt Cache 影响（逐条给账）

1. **无 `baseline_results` 的路径**：payload 与 sp7 基线**字节零扰动** ⇒ **既有 HumanMessage 字节基线不换发**（AC-S8-12② 的断言对象就是这一条）。
2. **有 `baseline_results` 的路径**：新增一条基线，**新立**而非替换。
3. **Prompt Cache**：缓存命中面挂在**稳定前缀 = SystemMessage**（`_build_execution_system_prompt`，整条常量、跨任务字节一致）。HumanMessage 是前缀之后的动态段 ⇒ **本项对 cache 命中率的影响为零**。
4. ⚠ **但系统提示词哈希基线本批必须换发一次**——原因**不是**本项，而是 S8-04/05 要求改写"成功判定纪律（强约束）"三句（`:1159-1162`，现文明写"你不判定复现是否成功"）+ "输出要求"段（新增档位/逐条结论/物证字段）。这两处属**同一次改写**，哈希基线**只换发一次**，须在开发计划里预先列为预期改动（AC-S8-18②），**禁止事后补记**。
5. **`sort_keys=True` 必须保持**：`baseline_results` 是 dict，键序不定则字节抖动，Prompt Cache 与回归基线双双失效。
6. 🔴 **（v2.1 新增）注入与其配套提示词约束必须同批落地。** §6.1 末行那条配套约束（「论文没报这个数也是合法结论，不得硬凑一个『对上了』」）是本项注入的**语义护栏** —— **只注数据不给护栏，等于把诱导递过去却不给约束**。⇒ **本项因此与「成功判定纪律改写」（S8-04/05）同批**；`_EXECUTION_SYSTEM_PROMPT_BODY` 的**哈希基线仍只换发一次**（第 4 条不变）。
   > ⚠ **§6.1 那行配套约束的原文不动** —— 它在 v2.0 时就写着，只是当时注入与它分处两批、约束落不了地；**移到同批之后它才真正被满足**。

---

## 7. Q-S8-07（新识别）：`ErrorCategory.NO_METRICS` 枚举成员必须保留

**发现**：`_apply_no_metrics` 删除后 `NO_METRICS` 无生产者，看起来该一并删枚举成员。**不能删**——`_feedback_from_committed_result`（`execution.py:3026`）从已落盘 `ExecutionResult.errors[0]` 的 `[error_category=xxx]` 前缀**反序列化**重建 `ErrorCategory`。旧 checkpoint（含 `task-99eef17bccf2` 等回归现场样本）里存着 `error_category=no_metrics` 的字符串，删成员会让**旧任务 resume 当场炸**。

**裁定**：`ErrorCategory.NO_METRICS`（`execution.py:151`）成员**保留**，加注释「Sprint 8 起无生产者，仅供旧 checkpoint 反序列化」；`AUTO_FIXABLE`（`:161-169`）中的归属**不动**；`ui/term_map.py` 的 `error_category:no_metrics` 文案**保留不删**（旧报告仍要能渲染）。

**验证**：一条旧 checkpoint 反序列化用例（构造 `errors=["[error_category=no_metrics] ..."]` 的 `ExecutionResult` → `_feedback_from_committed_result` 不抛异常）。

⚠ 这条同时是 AC-S8-15「`_apply_no_metrics` 已删除且无残留引用」的**边界澄清**：清零断言的对象是**函数与其调用点**，**不是枚举成员**。写测试时若把枚举成员一并清零，会当场把旧快照兼容打掉。

🔴 **v2.1 补充（跟改「四函数整体删除」）**：**本次删除面 = 四个三档函数**（`_extract_metrics_block` / `_regex_scan_metrics` / `_llm_extract_metrics` / `_parse_metrics`）**及其 `<METRICS>` 标签常量与 pattern**；**`ErrorCategory.NO_METRICS` 枚举成员与 ~~`_collect_grouped_metrics`~~ 均不在删除面内**。

🔴 **v2.2 订正上一段的后半句**：**`_collect_grouped_metrics` 已改判为整体删除**（§16.6），因此**删除面由四个函数扩为八个**（四个三档函数 + `_split_reported_metrics` + `_coerce_reported_value` + `_collect_grouped_metrics` + `_apply_no_metrics`）。⚠ **不变的是本节的核心裁定**：**`ErrorCategory.NO_METRICS` 枚举成员仍然必须保留**（旧 checkpoint 反序列化面，`execution.py:3026`）——**"删函数"与"删枚举成员"是两件事，本节这条边界澄清在 v2.2 之后一字不变，反而更要紧**（删除面扩大了，越容易顺手把枚举成员一起删掉）。

🔴 **v2.4 再加一层边界（删除面又扩了，这次扩到状态键，最容易顺手连坐的就是本节）**：**`ExecutionResult.metrics` / `metrics_groups` 两个状态键本次删除（§2.6），`ErrorCategory.NO_METRICS` 枚举成员仍然必须保留。** 三者名字里都有 metrics，但**不是一类东西、不可类推**：

| 对象 | 本次去向 | 删了会怎样 |
|---|---|---|
| `ErrorCategory.NO_METRICS`（`execution.py:151`） | 🔴 **保留** | 旧 checkpoint 里存着 `error_category=no_metrics` 字符串，`_feedback_from_committed_result`（`:3026`）用它**反序列化重建 Enum** ⇒ **`Enum(值)` 找不到成员会当场 `ValueError`，旧任务 resume 直接炸** |
| `ExecutionResult.metrics` / `metrics_groups`（`state.py:175` / `:183`） | 🔴 **删除** | **什么都不会怎样** —— `TypedDict` 运行时就是普通 `dict`、**零校验**，旧快照里多两个键读得出来、新代码 `.get()` 兜得住 |

⇒ **判别式一句话：`Enum` 有运行时成员查找，`TypedDict` 没有。** 前者删成员会炸，后者删声明不会。**AC-S8-15 那条"`_apply_no_metrics` 已删除且无残留引用"的清零断言，射程仍然是"函数与其调用点"，既不含枚举成员、也不含状态键。**

---

## 8. Q-S8-08（新识别）：四档制的用户可见文案连带面

清单见 §5.5（七处 + 两处注释订正）。独立编号的理由：这些**不是**任一 Q 的附属改动，而是四档制的连带面，漏改会产出"档位说复现成功、正文说未验证 / 已据此降档"的自相矛盾报告（MEMORY §4.2 的直接连带）。须在开发计划里**单列一条任务 + 单列验收**，不许挂在别的任务下顺手做。

---

## 9. 须 Maria 复裁（均不阻塞开工，架构已给默认取值，改动面已量化）

### 复裁项 1：逐条结论三态的字面量——PRD 内部不自洽

- **PRD §4.8 第 2 条**写「判定态恢复为三态『**印证上了 / 没印证上 / 无法核实**』」，同一行括号里又写「**零新枚举**——可直接复用既有三态词与现成渲染，**不新造文案**」。
- 既有三态词是「**符合 / 不符 / 未验证**」。**两句话互斥**：要么用新词（则"不新造文案"不成立），要么复用旧词（则 §4.8 第 5 条"「符合」的唯一生产者将消失"就不成立——它会有新生产者）。
- **架构默认取值：采新三态词**。理由：①「未验证」在新机制下是**错的**——现在是"agent 判过、但物证核实不了"，不是"没验过"；②「印证上了」直接对应四档判据的"论文核心结论得到印证"，语义一线贯通；③"零新枚举"的实质是不新增 Python Enum 类 / 不新增分类维度，这一点在新词方案下同样成立（仍是三个模块常量）。
- **若 Maria 要求复用旧词**：改动面 = `reporting.py:125-127` 三个常量取值改回 + `EXECUTION_OUTPUT_SCHEMA` 的 enum 三值 + 术语守门清单三行。**三处、可单点替换、不影响任何结构设计**。

### 复裁项 2：档名「失败」与既有报告形态文案「未成功复现（降级）」在同一份报告里并存

- PRD §4.5.4 第 5 条明写 `_determine_report_form` **函数逻辑零改动** ⇒ 报告顶部仍会印 `report_form` 的三条文案（`ui/term_map.py:80-82`：执行成功 / 仅生成代码 / 未成功复现（降级）），而结论节印四档名。
- 于是可能出现「形态：执行成功」+「结论：部分复现」这类**两套口径并列**的观感；「失败」与「未成功复现（降级）」则是**两个词说同一件事**。
- **这是产品文案决策，不由架构裁**。架构默认：**照 PRD 执行，两套文案并存不动**，并在报告里让结论节位置**先于**形态措辞出现，减轻歧义。若 Maria 认为须统一，最小改法是把 `report_form` 三条文案降级为纯结构描述（不含结论意味），**不需要改任何判定逻辑**。

### 复裁项 3（仅登记，架构无异议）

A-S8-08（支撑物证一条都不成立 → 封顶「仅代码跑通」）PM 已标可单点推翻。架构复核后**认为该口径成立且必要**：没有它，验钞对档位无强制力（PRD §4.9.3 已论证）。落点为 §2.3 的"取较低档"比较，若 Maria 改判到别的档，改的是一个常量下标，**零结构影响**。

---

## 10. 风险与验证（架构侧新增，PRD R-S8-01 ~ R-S8-24 不重复）

| 编号 | 风险 | 缓解 / 验证 |
|---|---|---|
| **AR-S8-01** 🔴 | **批次 1 单独落盘会把系统打进"全判失败"的不可用中间态**：S8-02 删掉 `<METRICS>` 三处后 `metrics` 恒空，而 `success` 的第二合取项 `len(metrics) >= 1` 要到批次 2 才被四档判据取代（PRD §4.5.5 已论证）。PRD §10 把 S8-02 放批次 1、判据放批次 2 ⇒ 两批之间的任何一次真跑 / 演示都会一律判失败 | **调整拆分**：见 §11。批次 1 只做 S8-01 / S8-03 / S8-10 |
| **AR-S8-02** | **`_resolve_agent_report` 的回读兜底本身可能成为"假绿通道"**：若回读放宽到"任意 AIMessage 里的 JSON 块" | 写死：只认 `<result>` 标签包裹、只取最后一条、解析失败即空。V1/V2 用例守 |
| **AR-S8-03** | **物证核验读盘的 IO 异常炸节点** | `_verify_evidence` 全程 try/except，异常 ⇒ 该条判**不成立**（保守方向，不是"放行"）+ WARNING |
| **AR-S8-04** 🔴 | **"一条统一判据"在落地时长回两套**（R-S8-12 的架构对偶）：开发极可能按"数值 / 趋势 / 定性"给 `_decide_conclusion` 写三个分支 | 架构写死：`_decide_conclusion` **只读 `level` + 数封顶**，**不读证据形态、不解析证据语义**；~~AC-S8-07④~~ 🔴 **v2.5 订正：应为 `AC-S8-08②`**（PRD `:477` 的 **AC-S8-07 只有 ①②、根本没有 ④**；`:478` 的 **AC-S8-08② 才是「代码里不存在按证据形态分支的逻辑」那条负向静态断言**）—— 本文档 §14.4 与 §16.3.2 早已在用 `AC-S8-08②` 这个号，**只有本行没跟上**；`dev-plan.md` 两处（`:184` / `:1267`）写的一直是对的。**⇒ 三份文档里唯独架构这一行错，且错了三版无人对表 —— 与 BUG-S7-11-01（两份文档同一个量措辞不同、实现照抄窄的那份）同型。** ⇒ **`AC-S8-08②` 的负向静态断言对象就是这个函数。** |
| **AR-S8-05** | **`conclusion` 键与 reporting 局部变量 `conclusion` 同名**，易在阅读 / 改动时串味 | 有意为之（同形同名，降低认知成本）；在两处 docstring 互相点名 |
| **AR-S8-06** | **审计在 execution 每回合跑一次**，修复循环多轮即多次 AST 全目录扫描 | 纯本地、无 LLM / 配额；`honesty_audit` 已有排除目录清单。**登记不治**；若真跑观测到耗时异常，再议 |
| **AR-S8-07** | **`EXECUTION_OUTPUT_SCHEMA` 新增字段若列进 `required`**，会让"跑挂了、没判定"的回合每次白烧一次 schema 重生成调用（`react_base._missing_required_fields`） | **写死：新增字段一律不列 `required`**——与 `metrics` 刻意不列 required 的既有理由逐字同源（`execution.py:1090-1091`） |
| ~~**AR-S8-08**~~ 🔴 **v2.2 作废** | ~~`_split_reported_metrics` 现行"先到先得"与 S8-06 的"撞名两条都丢弃"直接冲突（`execution.py:1796-1797` 逐字写着先到先得）~~ | 🔴 **整条作废，原文保留供追溯。** 作废理由：**撞名是折叠动作自己制造出来的问题** —— agent 汇报的本来就是平坦记录数组（`EXECUTION_OUTPUT_SCHEMA.metrics`，`execution.py:1114-1135`），是 `_split_reported_metrics` 用 `collected.setdefault(group, {})`（`:1827`）把它折成二维、并在撞名时 `continue  # 先到先得`（`:1831`）丢弃。**v2.2 删除该函数 ⇒ 不折叠 ⇒ 不撞名 ⇒ 没有"撞名怎么办"这个议题。** 与 PRD §4.6.6 的同款登记一致 |
| **AR-S8-09** 🔴 **v2.2 新增（本版最该被读到的一条风险）** | **块里的数字不受验钞约束。** 验钞验的是 `evidence_ledger` 里的记录（路径/数值/边界），**不是 `result_blocks[].rows` 里的单元格**。⇒ agent 完全可以在块里印 `0.61`、同时在台账里放一条 `0.62` 的合法物证，**两者不一致，系统无任何机制发现**。旧路径至少还有"`metrics` 与 `metrics_groups` 同源收编"这一点微弱约束，本版连这点也没了 | **不做"cell 必须能在 sources 里找到"的校验** —— 那是"渲染层做结构推导"的近亲（PRD 非目标 11），对纯文字块无意义，且会诱导 agent 只报能匹配的数。⇒ **改为如实标注**：结果块节的导语**必须**写明「下表由执行环节汇报，系统核验的是它标注的来源文件与逐条结论的物证，**未逐格核对表内每一个数字**」。这是 R-S8-01 对外表述纪律在报告正文里的落地，**不得省略、不得软化** |
| **AR-S8-10** 🔴 **v2.2 新增 / v2.3 重裁** | **论文报告值没有产物物证，会被验钞第④重必然判不成立。** S8-10 把 `baseline_results` 注入上下文（§6），语义层约束③又要求"论文值与复现值同块内可对照"⇒ agent 会把论文值写进 cell。**论文值的出处是注入的上下文，不是 `code_output_dir` 下的文件** ⇒ 若开发要求块的每个来源都过**产物**验钞，**论文值那一列必然被标红**，报告会印出"论文报的 0.62 来源不可信"这种荒谬结论 | ~~**写死：`evidence_ledger` 只登记本次复现产物的物证。论文报告值不进台账、不参与验钞、不因此标注异常。**~~ 🔴 **v2.3 后半句整条改判（原文划删保留）**：**「不按产物文件验」这一步是对的，「那就不验了」是错的** —— 中间少走了一步「**那就换个东西验**」。⇒ **论文值物证照样进台账，按 `state["paper_analysis"]["baseline_results"]` 核验两重**（§16.3.2）。提示词侧**保留**"不必也不应为它编一个产物路径"（这半句仍然正确），**新增**"引用论文值时须写明它对应论文分析里的哪个指标名"。**这是 S8-06 与 S8-10 的接口，PRD 未写，本版补上** |
| **AR-S8-14** 🔴 **v2.3 新增（它是上一条被推翻的直接原因，单列以免读漏）** | 🔴 **把对照基准往低了编 —— S7-11 反向激励的第三个变种，也是最隐蔽的一个。** 四档里「复现成功」的达标线常写成「数值与论文报告对上」⇒ **agent 只要把论文值报低，自己跑出来的数就"对上了"**。**前两个变种都还在"标准"那一侧**（S7-11：执行环节少做几步；R-S8-02：规划把及格线画低），**这一个直接改了"事实"那一侧**，而基准看起来是"论文说的"、**没人会去质疑**。⚠ v2.2 的"不验"裁定**正好为它敞着门** | **论文值物证按 `baseline_results` 核验**（§16.3.2）。编低的论文值**对不上论文分析** ⇒ 该条 `ok=false` ⇒ ①引用它的逐条结论落「**无法核实**」（PRD §4.8 第 3 条既有保守出口）；②档位的支撑物证若全不成立 → **既有封顶 3「仅代码跑通」**（§4.5.3）。🔴 **不新增第四条封顶**（既有两个出口已完全覆盖，取向与 §2.5.6"架构在此明确不自造新规则"逐字同源）。局限如实登记见 §16.3.2 末 |
| **AR-S8-11** 🔴 **v2.2 新增** | **`conclusion` 体积上界与 checkpoint 膨胀（PRD 未算过账）**。四个上限的最坏乘积：12 块 × 50 行 × 12 列 × 120 字符 ≈ **864 KB / 次**；`execution_result` 每个修复回合落盘一次，20 轮上限 ⇒ 单任务最坏十几 MB 进 `checkpoints.db`。旧 `metrics_groups` 的上界远小于此 | **登记 + 量化，不预先加机制**（反过度工程）。实际值远低于上界（真实指标单元格 < 20 字符 ⇒ 约 144 KB/次）。**处置顺序写死：若真跑观测到膨胀，先单点调低"每块行数"上限，不改结构**（与 A-S8-12"单点调值不改结构"同款）。**禁止**为此把块搬出 state 或做外部文件引用 —— 那会破幂等与可重放 |
| **AR-S8-12** 🔴 **v2.2 新增** | **旧快照重放的报告不再含指标对比表**。`_render_metrics_comparison` 删除后，旧 checkpoint 里存着的 `metrics` / `metrics_groups` **再也不会被渲染**，而既有回归夹具（`tests/fixtures/s713_realrun_20260802/`）与旧报告快照断言正是建立在那张表上 | **如实登记为预期行为变更，写进 AC-S8-23 的增减账**。🔴 **绝不为了让旧报告好看而保留旧渲染分支** —— 那等于把被删掉的预设格子留一半，取向与 §5.4「绝不为了让旧报告好看而在报告侧重新判定」逐字同源。旧快照的正确表现是：结果节整节不渲染（块为空 → 早退） |
| **AR-S8-13** 🔴 **v2.2 新增**（⚠ **不是**那条「两键迁出」的裁定 —— 那条已换发为 **`AR-S8-15`**，撞车登记见 §17.0） | **schema 重生成路径产出的块内容可能与真实产物不符**。`max_tokens` 截断后走的是 `react_base` finalize 的 schema 重生成（`:727-752`），**那是让模型重说一遍**，不是修补原文 ⇒ 重说的那份块可能更少、更简、或与第一次不同；且 `result_blocks` 不进 `required`（AR-S8-07）⇒ 重说时它可以整个不给 | 与 R-S8-19（预算耗尽）同族，**共用同一条 caveat 通道**（`conclusion["report_caveats"]`）。检测口径见 §16.4（**确定性信号，不用长度启发式**） |
| **AR-S8-15** 🔴 **v2.5 新增（开工期实测触发；全裁见 §17）** | **`TypedDict` 加必填键会让所有既有构造点当场 mypy 红。** `TypedDict` 默认 `total=True` ⇒ 在批次 1a 单独给 `ReproductionPlan` / `ExecutionResult` 各加一个必填键，会在 `planning.py:467`/`:676`、`execution.py:2451`/`:2908` 打出 **4 个 `[typeddict-item]`**（已实测，主控独立复现），而这四处的补默认值动作**都排在后续批次** ⇒ 与 `CP-1a.5-6`（mypy 零错误）**设计上互斥**。⚠ **`reporting.py:581` 的同类构造点不会报**（`mypy.ini:150` 已压制该文件的 `typeddict-item`）⇒ 静态检查**不会替批次 3 把漏补的构造点报出来** | 🔴 **`T-S8-1a-2` 整条注销**，**两键声明形态一字不改**（不改 `NotRequired`、不动 `total`），改为**与各自构造点原子同批**：`success_criteria` → `T-S8-1b-2`；`conclusion` → `T-S8-2-8`。升格通用纪律 **`R-S8-42`**（加/删双向适用，读侧不受此限），**须有验红活体证明**。三条退路的穷尽性论证、六条检查点的去向、元教训见 **§17** |
| **AR-S8-16** 🔴 **v2.7 新增（已交付代码里的活缺陷，不是排期问题）** | **判据的候选源在生产链路上恒为空 —— 护栏会误伤最扎实的那一档达标线。** W6 的候选集设计为 `metrics` + `datasets` + `baseline_results` 的键（§15.3 第 1 条），`core/plan_checks.py:505`/`:522` 三条**都实现了**；但生产链路上 `check_plan` 拿到的第三参是 `planning.py::_digest_paper_analysis` 的 **4 键摘要**（`method_summary`/`datasets`/`metrics`/`framework`），**`baseline_results` 从未被放进去** ⇒ 引用论文自报基线（`BM25_R2` 这类键名）的达标线**必被误报**，而这恰是最不该被误报的一种。⚠ **它躲过了全部既有绿灯**：`tests/test_sprint7_s708_payload_probe.py` 的精确 11 键守门断的是**外层** payload，`paper_analysis_summary` 的**内层键无人守**；`dev-plan` `CP-1b.4-3` 的 UI 用例用的是**手搭 payload**（直接塞了 `baseline_results`）⇒ **测试证明的是"这个能力存在"，不是"这条链路通"** | 🔴 **裁定（v2.7）：`_digest_paper_analysis` 加第 5 键 `baseline_results`，原样透传 / 恒常给键 / 不截断 / 不改名 / 不改型**；`core/plan_checks.py` 与 W6 判据**一字不动**，外层 payload 键集合**一字不动**（⇒ 11 键守门不受影响）。**三条替代路已逐条否决**（收窄 W6 口径 = 把文档改成与 bug 一致；截断键 = 把缺陷改小而非改掉、且按论文基线规模复发；收 `method_summary` 进候选集 = 要么零效果要么毁掉判据）。**新增一条链路级验证 G8**（走真实 `_digest_paper_analysis`，不许手搭 payload）+ **G9**。全裁见 **§15.3.1**，落点见 §12 |

---

## 11. 批次与开工顺序（**v2.0 重写**：v1.0 的调整案已撤回，改为接受 PRD v3.0 的 1a / 1b 拆分）

> **v1.0 曾建议把 S8-02 从批次 1 移到批次 2**（理由：AR-S8-01 中间态）。PRD v3.0 已把批次 1 拆成 1a / 1b，**1a 是纯"能力接入 + 通道退场"、1b 是计划侧**——这个拆法与我的顾虑不冲突，且粒度更细。⇒ **撤回原调整案，接受 PRD 拆分**，只追加两条前置约束（见下）。

| 批次 | 内容 | 架构意见 | 依赖 |
|---|---|---|---|
| **1a** | 🔴 **v2.1 收窄**：**S8-03**（执行只读工具接入）+ 状态契约两键声明 + **编码侧 prompt 字节门新建**（`S8-02` 的**守门**部分）—— **本批全部「加了但不改变行为」** | ✅ **可最先开工，且可与本次跟改并行**；**落盘后系统行为与今天完全一致，可真跑、可演示** | Q-S8-06（已裁）+ **Q-S8-02 的字段名**（见前置①） |
| **1b** | S8-01 扩围 + S8-11 三道护栏 | ✅ 可开工 | Q-S8-02（§2.5 已裁）+ **Q-S8-09**（§15 已裁） |
| **2** | 🔴 **v2.2 再扩围**：S8-04 / S8-05 / **S8-06（结果块的执行侧：schema 换发 + 语义层提示词 + `_verify_evidence` 建台账 + `_collect_result_blocks` 收编 + 四个上限常量 + 截断检测）** + **四个折叠/扫盘函数删除**（§16.6）+ `S8-02` 的通道退场与编码侧改词（v2.1 由 1a 移入）+ `S8-10` execution 侧注入（v2.1 由 1a 移入），**内部不得拆分** | ✅ **一切会改变行为的事全部收进本批那个已经开着的不可用窗口内一次做完** | Q-S8-01 / Q-S8-02 / **Q-S8-10（§16 已裁）**；⚠ **另须 PRD §13 第 1 / 3 / 5 条 Maria 确认**（见下前置③） |
| **3** | 🔴 **v2.2 扩围**：S8-07 / S8-08 / S8-09 + 档名文案换发 + **报告侧通用块渲染**（`_render_result_blocks` + 两处调用点 + 三个函数删除，§5.9）+ **界面结果页块表**（`result_report.py`） | ✅ | Q-S8-05（**v2.2 已重裁**，含 §5.7 / §5.8 / §5.9）/ **Q-S8-10**；⚠ 界面落点另须 PRD §13 第 2 条确认 |
| **4 / 5** | 回归对平 / 真跑取证 | 不变 | — |

🔴 **两条前置约束（不写清会出事）**：

1. ~~🔴 **（v2.1 改写）1a 仍依赖 `success_criteria` 这个「字段名」，但依赖的性质已变**：两处注入（编码侧上下文 = `S8-02` 第 3 条、执行侧上下文 = `S8-10`）**已随本次跟改一并移到批次 2** ⇒ **1a 只保留 `core/state.py` 的键声明**（TypedDict 加键，无运行时约束）。**本文档 §2.5 已裁定该字段名，依赖即刻解除**；**1a 加完键即止、不做任何注入** ⇒ **payload 与今天字节零扰动、行为零变化**。~~
   > 🔴 **v2.5 改判（原文划删保留，`AR-S8-15` / §17）**：括号里那句「**TypedDict 加键，无运行时约束**」**只在运行时成立、在 mypy 层不成立** —— 默认 `total=True`，加必填键会让既有构造点当场 `[typeddict-item]` 红（实测 4 处）。⇒ **`T-S8-1a-2` 整条注销，`core/state.py` 的键声明也移出批次 1a**：`success_criteria` 随 `T-S8-1b-2`、`conclusion` 随 `T-S8-2-8`，**各自与构造点原子同批**（`R-S8-42`）。
   > **⇒ 本条前置约束的当前口径**：**批次 1a 对 `core/state.py` 零改动、对两个字段名零依赖**（两处注入本就已移到批次 2）。「payload 字节零扰动、行为零变化」的结论**不变且更强** —— 1a 现在连声明都不加。
   > **两处注入为什么必须移走**：1a 若保留注入，则在「1a 可演示」的状态下 agent **同时握有论文目标值 + 本篇及格线 + 读全工作区的两个只读工具**，而**验钞与证据边界都在批次 2** ⇒ `metrics_groups` 走「自报优先」、**零验钞直通报告对比表**，一次演示就可能产出「结论：未成功复现」+「回验表一片符合」的自相矛盾报告。另 🔴 **`success_criteria` 的诱导性比 `baseline_results` 更强**（它直接告诉 agent「达到什么算过」），而 1a 期间系统提示词**还在说「你不判定复现是否成功」** —— **给了及格线又说你不判**，自相矛盾。⇒ 整体后移一并消解。
2. **AR-S8-01 依然成立**：1a 落盘后、批次 2 完成前，`<METRICS>` 通道已退场而新判据尚未上线 ⇒ `metrics` 恒空 ⇒ 成功判据第二合取项恒假 ⇒ **系统处于"一律判失败"的中间态**。⇒ **代码可以并行写、可以并行落盘，但在批次 2 交付前不得做端到端真跑、不得对外演示**。这不是能不能开工的问题，是可用性恢复时间点的问题，须在开发计划里写明。
3. ✅ **（v2.2 新增 / 🔴 v2.4 结清）批次 2 / 3 的外部前置已全部解除** —— **PRD §13 五条 Maria 已于 2026-08-06 全部拍完，批次 2 与批次 3 的卡口解除，无剩余待拍项。**

   | # | 事项 | 架构默认取值 | Maria 裁定 | 对本文档的影响 |
   |---|---|---|---|---|
   | 1 | `metrics` / `metrics_groups` 删键 vs 停产 | 保留停产 | 🔴 **推翻 ⇒ 删键** | **§2.6 新增**；§0 / §5.9 / §12 / §16.6 跟改 |
   | 2 | 界面结果页 A 自建块表 vs B 净删 | A | ✅ 同默认（A） | §12 `ui/` 条目的"落点前置"注解**解除**，按 A 落地 |
   | 3 | 单元格一律字符串 vs 联合类型 | 一律字符串 | ✅ 同默认 | §16.2③ **原样生效** |
   | 4 | 是否接受丢失扫盘兜底 | 接受并登记（R-S8-20） | ✅ 同默认（接受） | §13 / §16.6 **原样生效**；§16.6 备选 B **不启用** |
   | 5 | 是否修订内联证据形态为 id 台账 | 修订 | ✅ 同默认 | §2.1 / §16.3 **原样生效** |

   > ~~**若五条默认取值全部被确认，本文档无需再改一个字**~~ —— 🔴 **第 1 条被推翻，故本文档出 v2.4。其余四条确认，相关章节一字未动。**
   >
   > ⚠ **第 4 条提请确认时已转达的补充依据（留档）**：§16.6 查实 —— 那个扫盘兜底的硬编码前提（目录 `outputs/`、文件名 `summary.json`、只收顶层标量）**在本 Sprint 之后不再由任何契约保证**。⇒ 它不是"一次干净的能力回退"，更接近"**前提被同批拆除后留下的空壳**"。**这条改变的是拍板依据，不是拍板归属** —— Maria 知情后仍按"接受并登记"拍板。

---

## 12. 开发交接清单（文件级，含函数名 / 行号；架构不写实现代码）

### `core/state.py`

> 🔴 **v2.5 批次归属（`AR-S8-15` / §17，本节此前未指明，`P-S8-13` 曾就删键侧点过一次）**：**本文件在批次 1a 零改动。** 两个加键动作**各自与其构造点原子同批**（`R-S8-42`）——
> - `ReproductionPlan.success_criteria` → **`T-S8-1b-2`**（与 `planning.py:467` / `:676` 同批同 commit）
> - `ExecutionResult.conclusion` + 两键删除 → **`T-S8-2-8`**（与 `_build_execution_result` 换发 + `:2908` 降级构造点同批同 commit）
>
> ⇒ **本文件全 Sprint 被触碰恰两次，分属两个批次**（原 dev-plan「1a 一次收口后全 Sprint 零改动」的排期已作废）。**这不是"要改三次"**：MEMORY §1.2「同一文件被两批次同时改无法分离提交」说的是**并行**，而 1b 与 2 是**串行**批次，逐批 commit 不冲突。

- `ExecutionResult`（`:159-184`）加 1 键 `conclusion: Dict[str, Any]` + docstring 补 Sprint 8 段（沿 sp5 / sp7 加键注释体例）。**其余键、顺序一字不动。**（**落 `T-S8-2-8`**）
- 🔴 **`ReproductionPlan`（`:115-157`）加 1 键 `success_criteria: str`** + docstring 补第四批段（§2.5）。**既有 13 键、顺序一字不动。**（**落 `T-S8-1b-2`**）
- ~~`:170` 的 `metrics_groups` 注释订正（组名不再是产物目录，见 §5.5）~~ ~~🔴 **v2.2 改判**：`:170` 现文「多组指标 {组名: {指标: 值}}（execution `_collect_grouped_metrics` 写）」在本版之后**整句失真**——该函数已删、组名概念已消失。改写为「**Sprint 8 起停产停消费，仅供旧快照反序列化；本次执行结果见 `ExecutionResult.conclusion.result_blocks`**」。`metrics` 的同款注释一并改。**两个键本身保留声明**（PRD §13 第 1 条默认取值：保留停产，不删键）。~~
- 🔴 **v2.4 再改判（Maria 2026-08-06 推翻上一条，原文划删保留）**：**`:175` `metrics` 与 `:183` `metrics_groups` 两行声明删除**；`:170` 的 `metrics_groups` docstring 说明**随之删除**（不是改写）；docstring 同批加一段 **Sprint 8 删键留痕**（形态与理由见 §2.6.3）。⇒ `ExecutionResult` 由 11 键变 **10 键**（删 2 加 1）。🔴 **四处精确键集合断言须同批换发，禁止把 `==` 放宽成 `>=`**（§2.6.2 丙类清单）。

### `core/nodes/execution.py`
- `ErrorCategory`（`:132-157`）：新增 `NO_VERIFIABLE_OUTPUT` 并入 `AUTO_FIXABLE`；`NO_METRICS` **保留**加注释（§7）。
- 🔴 **`_extract_metrics_block`（`:402-423`）/ `_regex_scan_metrics`（`:426-449`）/ `_llm_extract_metrics`（`:452-514`）/ `_parse_metrics`（`:517-550`，含调用点 `:2935`）：整体删除**（**v2.1 跟改：由 v2.0 的「判定链路解绑」改判**）。**连同 `<METRICS>` 标签常量与 pattern 一并删除**（`:393-399` 的 `_METRICS_TAG_OPEN` / `_METRICS_TAG_CLOSE` / `_METRICS_TAG_PATTERN` —— 其唯一消费者就是 `_extract_metrics_block`）。
  - **与 PRD 的关系**：与 **PRD §4.2 第 4 条**（「三档退场」）**字面一致**；**PRD §4.2 第 5 条**（「顺带清掉 `_parse_metrics` 的死参数 `plan`」）**被超越而非违反** —— **删函数严格强于清死参**。**PRD 不改（铁律）**，此行即留痕。
  - **模块 docstring 同批订正**：`:9-10`（七步骨架第 4 / 5 步）与 `:26-27`（「仅 metrics 档 3 LLM 抽取兜底触发时…」）在本 Sprint 后**全是假话**，须一并改写。
  - ✨ **附带不变量强化**：`_llm_extract_metrics` 是 `execution` 主体在 ReAct 子图**之外唯一的 LLM 调用入口** ⇒ 删除后「执行主体不调 LLM」由「目前恒成立」变为「**结构上不可能不成立**」。
- `_EXECUTION_SYSTEM_PROMPT_BODY`（`:1144`）：改写"成功判定纪律（强约束）"三句（`:1159-1162`）+ "输出要求"段；⚠ 措辞按 PRD §4.9.5 措施 1，**不得回灌判定规则、不得写成"报了就算成功"**。🔴 **v2.2 追加 / v2.3 修订**：**结果块的四条语义层约束一并写进本常量**（§16.1；第③条**必须是条件句**、须说明"不必也不应为论文值编一个产物路径"、并 🔴 **v2.3 新增一句「引用论文报告值时须写明它对应上下文 `baseline_results` 里的哪个指标名（用原键名），并原样使用那里的数值」**——这句是 §16.3.2 两重核验能通过的前提，**漏写则 agent 报的论文值会大面积判不成立**）；🔴 **形状层一个字都不许写进来** —— 不举"列应该有哪几列"的例子、不给样板表头，**否则模型会把示范当成必须遵守的格式，等于把预设表头搬进提示词**（MEMORY §4.2 已记同型：写 prompt 时别拿枚举当叙述示范，模型会抄进自由文本）。四档语义段仍按 §2.5.4 红线留在本常量内、达标线仍走 HumanMessage —— **两层分离不受本次影响**。哈希基线**仍只换发一次**（§6.2 第 4 条不变，本项与判定纪律改写属同一次改写）。
- `EXECUTION_OUTPUT_SCHEMA`（`:1092`）：新增 `conclusion_level` / `goal_checks` / `evidence` **/ 🔴 `result_blocks`（v2.2 新增）** 四字段；~~`metrics[].group` 的 description 改为"把维度写进组名"（S8-06 方案 A）~~ 🔴 **v2.2 整条作废**（方案 A 已被 Maria 拍板废除）——改为 **`metrics` 属性整体从 schema 移除**（它已停产停消费，留在 schema 里只会继续教 agent 报一份没人看的扁平指标，白占每回合的 schema 与提示词字节）；**新增字段一律不进顶层 `required`**（AR-S8-07；⚠ **`items` 内部的 `required` 不受此限**——`_missing_required_fields` 只读顶层 `schema.get("required")`，`react_base.py:483` 逐字可查 ⇒ 块的 `required: ["title"]` 写在 `items` 里是安全的，不会引发重生成）。**形态细节见 §16.2。**
- `_build_execution_agent_context`（`:1299`）：末尾追加两处"非空才注入"——`baseline_results`（Q-S8-06）、`code_audit_findings`（§5.6 A）。**既有键的构造顺序与取值一字不动。**
- `_run_execution_agent`（`:1551`）：绑入 `make_read_code_file_tool()` / `make_list_dir_tool()`（`:1581-1584` 工具列，**不新造工具**）；调 `audit_code_dir` 并透传；收尾改调新 `_resolve_agent_report`；`ExecAgentOutput`（`:1186`）加 `report` 字段。🔴 **v2.2 追加**：`ExecAgentOutput.reported_metrics`（`:1205` 注释点名）**随 `_split_reported_metrics` 一并删除**——它在本版之后零消费者；`report` 字段即其继任者（且是唯一取数口径，与 §1.3"消除两个取数口径"同向）。
- 🔴 **v2.2 删除面（四项一并，理由见 §16.6）**：~~`_split_reported_metrics`（`:1781`）：撞名策略改为"值不同则两条都丢弃 + WARNING"（AR-S8-08）~~ **整条作废，改判为整体删除**。
  - `_split_reported_metrics`（`:1781-1856`，含调用点 `:2938`）：**整体删除**。折叠动作（`:1827` `collected.setdefault(group, {})`）+ 撞名丢弃（`:1831` `continue  # 先到先得`）**就是本次要治的病根本身**。
  - `_coerce_reported_value`（`:1764-1778`）：**随之删除**（唯一调用点 `:1821`）。
  - `_collect_grouped_metrics`（`:1709-1756`，含调用点 `:2961`）：**整体删除**。⚠ **这推翻了本文档 §13 v2.1 的「不删、不改」**，留痕与理由见 §16.6 与 §13。
  - `_GROUP_METRIC_STR_MAX_LEN`（`:1706`）：**随之删除**（两个消费者 `:1752` / `:1774` 都没了）；其取值 **120 由新的 `_BLOCK_CELL_MAX_LEN` 继承**（§16.5）——**是改名继任，不是新造第二个常量**。
- **新增（🔴 v2.2 由四个改为五个纯函数，紧邻既有同族函数放置）**：`_resolve_agent_report`（放 `_merge_with_collector` 之后，共用范式注释）、`_verify_evidence`（🔴 **v2.3：形参多一个 `baseline_results`；按 `path` / `metric` 二选一走两套核验**，§16.3.2。⚠ 调用方须从 `state["paper_analysis"]` 取该值传入 —— 这是 execution 侧对 `paper_analysis` 的**第二个**消费点，第一个是 §6 的上下文注入，**两处取的是同一个字段，不新增状态读取面**）、**`_collect_result_blocks`（v2.2 新增，放已删的 `_split_reported_metrics` 原位——那一段的模块注释「步骤 4.4：agent 自报指标拆分」同批改写为「步骤 4.75：agent 汇报的结果块收编」。🔴 **v2.6 补一条实现纪律**：`title` / `note` / `cell` 的非 str 处置**必须按 `isinstance` 判定，不得依赖 `mask_value` 抛不抛异常** —— `mask_value` 对非 str 有**三种**行为、其中两种静默漏过脱敏，全裁见 §16.5①）**、`_decide_conclusion`、`_apply_no_verifiable_output`（放 `_apply_incomplete_execution` 之后）。
- **新增四个模块常量**（块展示上限，`_collect_result_blocks` 附近，§16.5）：`_BLOCK_MAX=12` / `_BLOCK_COL_MAX=12` / `_BLOCK_ROW_MAX=50` / `_BLOCK_CELL_MAX_LEN=120`。**不进 `config.py`**（PRD 非目标 10）。
- **删除**：`_apply_no_metrics`（`:2242-2271`，零改动红线已由 Maria 解锁，留档在 PRD §4.5.4 第 4 条）。
- `_no_metrics_stalled`（`:2729`）→ `_no_progress_stalled`；`_NO_METRICS_EARLY_STOP_SUMMARY`（`:2715`）文案换发。
- `_build_execution_result`（`:2395`）：新增形参 `conclusion`；`success` 改为由 `level` 派生（`:2428-2432` 的三合取判据整体退场）。~~🔴 **v2.2 追加**：`metrics` / `metrics_groups` 两个形参**保留带默认值、`execution()` 侧不再传**（两键落盘为空）——改动面最小、`ExecutionResult` 结构与旧快照形状不变；`:2419` docstring 里点名 `_collect_grouped_metrics` 的那句同批改写。~~ 🔴 **v2.4 改判（原文划删保留）**：两个形参**一并删除**（键都没了，留两个永远不被传、传了也无处可落的形参就是空壳，理由同 `metrics[].source` 被砍，`execution.py:1083-1089`）；`:2419` docstring 那句**随之删除**（不是改写）。见 §2.6.3。
- `execution()`（`:2874`）：插入步骤 4.75 / 4.8（§1.5）；**删除 `:2938` 与 `:2961` 两处调用及其上方的 `:2940-2952` / `:2954-2960` 大段注释**（🔴 其中 `:2945-2952` 正是 S7-13 自律门控，PRD §4.5.5 留档 2 已由 Maria 拍板废止，删除即其落地）；降级构造点（`:2908-2917`）补 `conclusion={}`。
- **不动**：`_SandboxRunCollector`（`:805-826`）、`_merge_with_collector`（`:1517`）、`_reconcile_steps`、`_completion_insufficient`、`_has_committed_result_for_round`、`:995` 不得写代码防线、`:1010` 管道 / 重定向拒绝、`:2817-2840` 优先级链顺序。

### `core/nodes/reporting.py`
- 删：`_normalize_group_key` / `_match_metrics_group` / `_lookup_metric_value` / `_verify_trend`（`:130-198`）。
- 🔴 **v2.2 新增删除面（三个，§5.9 第 1/2/3 条）**：`_render_metrics_comparison`（`:949-1008`）、`_comparison_table`（`:931-946`，含 `:938` 写死的三列表头）、`_flatten_mapping`（`:474-486`）。**⚠ `_flatten_entries`（`:440`）与 `_fmt_metric_value`（`:415`）不删**（另有消费者 `:898` / `:924-925`）。
- `_verify_expected_results`（`:201`）：退化为旧快照兼容读（§5.4）。
- `_determine_conclusion`（`:245`）→ `_assemble_conclusion`（§5.2）；`:281-282` 审计析取项删除；`:313-322` 判定段整体删除。
- `_render_annotation_notices`（`:587`）：审计 hits 表（`:629-652`）搬出；`:612-613` 导语改写。
- **新增** `_render_audit_findings(audit)`（独立节，空则早退返 `[]`）；`_render_report`（`:1176`）并列调用。
- `_render_goal_checks`（`:707`）：icons 三 key 换发（`:727-731`）；`:722-723` 与 `:741-747` 文案改写；🔴 **v2.2 扩围**：行渲染（`:739`）新增一列或一段，按 `evidence_ids` 回查 `evidence_ledger` 展示物证路径，**引用到 `ok=false` 记录的条目显著标注**（§5.9 第 9 条）。**签名 `(conclusion)` 单参不变。**
- 🔴 **v2.2 新增** `_render_result_blocks(conclusion) -> List[str]`（§16.5）：**唯一入参是 `conclusion`，不得取 `state`、不得取 `exec_result`**（取了就会有人从 `paper_analysis.baseline_results` 再拼一列论文值 = 预设表头复发，静态断言对象）；块为空 → 早退返 `[]`。
  - 调用点**两处**：`_render_full_success`（`:880` 原位替换 `_render_metrics_comparison`）+ 🔴 **`_render_degraded`（`:1061` 之后、`:1063` 之前，新增）**，理由见 §5.8。`_render_code_only` **不调**。
- ~~`_render_metrics_comparison`（`:949`）：`:955` / `:995` 组名说明改写；复现侧无数据时**不渲染主实验表**（`:980-989`，PRD §4.7 第 3 条）~~ 🔴 **v2.2 整条作废**（改判为整体删除；两个子项的议题随之消失，见 §5.9 第 1/5 条 —— **找不到落地物是对的**）。
- `_SUCCESS_CRITERIA_NOTE`（`:560-563`）换发。
- **不动**：`_determine_report_form`（`:92-106`）、`audit_code_dir` 调用点与三键返回契约（`:1224-1249`）、`_md_escape_inline`（`:406-412`）、`_flatten_entries`（`:440`）、`_fmt_metric_value`（`:415`）、`_NEST_MAX_DEPTH` / `_LIST_INLINE_MAX`（`:435` / `:437`）。

### `core/nodes/coding.py`
- 清除三处 `<METRICS>` 教学文本：`:113`（`entry_script` 结构声明 description）、`:181-186`（整段）、`:191`（修复回合那句）——**三处一起，漏一处就仍在教 agent 写标签**。
- 补产出约定（结果文件写在计划声明的位置、结构自定、合法 JSON 顶层对象）。
- 上下文补 `expected_results` **与 `success_criteria`**（两者今天均零命中；前者是定性物证的生产者，后者让编码环节知道"这次要拿出什么才算成功"）。两处均**非空才注入**。

### `core/nodes/planning.py`
- 提示词：交付清单语义扩为"本次复现应当落地的产物"（复裁 2）；`expected_output` 要求写清相对代码目录的产出文件路径。**`:196` 那句产出目录约定保留不动。**
- 🔴 **v2.0 推翻 v1.0 此处的「不新增计划字段」**（A-S8-02 已被 PRD v3.0 显式推翻）：新增 `success_criteria`，**进输出契约的 `required`**（§2.5.5）。
- 🔴 提示词须同时立三条约束：①**只写本篇达标线、不得改动四档的含义**（两层分离，§2.5.4）；②**必须引用论文的具体主张**（点名指标或论文结论），**禁止"能运行即可"这类空话**（护栏 3 的提示词侧）；③**四档的语义边界不得写进计划提示词的可填内容里**——它属第一层，写进去就等于把第一层交给计划改。
- 🔴 **v2.7 新增（`AR-S8-16`，全裁见 §15.3.1）**：`_digest_paper_analysis`（**现 `:884-896`，行号须现查**）的返回 dict **加第 5 键**：`"baseline_results": paper_analysis.get("baseline_results") or {}`。**四条实现纪律，一条都不许打折**：
  - **原样透传**——不截断、不改名、不改型（仍是 `Dict[str, Any]`）、不做 `str()` 强转；
  - **恒常给键**——与既有四键同款（`datasets` / `metrics` 空时给 `[]`、`framework` 可为 `None`），**不走"非空才注入"**（那是**给模型看的上下文通道**的规矩，本 payload 是**给人看的展示通道**，两者在本项目里刻意不同，`planning.py:1032-1039` 已逐字写过一次）；
  - **只加这一个键**——🔴 **不得顺手把 `_KEEP_ANALYSIS_KEYS`（`:422-425`，7 键）整份搬进 digest**。`hyperparams` / `hardware_requirements` / `key_formulas` 与 W6 无关，且会改变审核页「论文分析摘要」展开块的内容与体积；
  - **不得为此新增外层 payload 键**——`baseline_results` 是 `paper_analysis_summary` 的**内层子键**，外层仍是 11 键（否则 `tests/test_sprint4_e2e.py` 的精确键集合守门当场红，那才是真正贵的改动）。
  - ⚠ **`_digest_paper_analysis` 的既有截断纪律不变、也不扩围**：它只截 `method_summary`（叙述型长文本，800 字），`datasets` / `metrics` 两个事实层结构字段**从来不截** ⇒ `baseline_results` 同属事实层，**照既有口径就该不截**。这不是新开口子，是归位。

### `core/plan_checks.py`（**v2.0 新增落点**）
- 新增 **W6**（§15）：成功标准未引用论文任何具体指标或结论 → 报警示。
- `check_plan`（`:483`）**加一个带默认值的关键字形参** `paper_analysis: Optional[Dict[str, Any]] = None`；**既有五条 W 的 rule 字符串、message、触发条件一字不动**；既有两个调用点不改也能跑（默认 `None` ⇒ W6 不触发）。
- **零改动红线本次再解锁，范围严格限于上述两项**；`_INLINE_PY_MAX_CHARS` 的可行窗口 `[98, 126]` 与「单一规则、不做动词枚举、不做后缀白名单」两条红线（`:76-89`）**一字不动**。

### `ui/`
- `ui/term_map.py:84-86` → 四条恒等映射（§2.3）+ 注释说明"存在理由是守门通道"。
- ~~`ui/pages/result_report.py:178`：数据源从 `metrics` 改读 `metrics_groups`（全 `ui/` 对 `metrics_groups` 今天零命中 ⇒ 不改则结果页永远显示"无可对比指标"）~~ 🔴 **v2.2 整条作废**——那是把界面接到一个本 Sprint 已停产的字段上，且照做等于把旧的二维格子重新实现一遍（PRD §4.7 第 2 条已同款点名 `dev-plan.md` 的 T-S8-3-9 整条作废）。**改判为**：
  - `_metric_comparison_rows`（`:163-201`，其 `:196-198` 与 `reporting._comparison_table:938` 是**同一套写死的三列**，是"代码预设表头"的第二处）**整体替换**为按 `conclusion["result_blocks"]` 逐块出表的函数；
  - `_render_metrics_section`（`:315-330`，调用点 `:486`）改为**逐块**渲染「标题 → 可选说明 → `st.table(rows)` → 可选来源与 caveats」，**按数组顺序、不排序**；
  - `:320` 的空文案「无可对比指标：论文 baseline / 复现 metrics 均为空。」换发为「本次执行未汇报可展示的结果块。」，🔴 **不得兜底回退到 `metrics` / `metrics_groups`**（负向断言对象，AC-S8-20②）；
  - 结论档位改读 `execution_result.conclusion.level`。
  - ✅ ~~⚠ **落点前置**：本条按 PRD §13 第 2 条的默认取值（方案 A：界面自建块表）写；若 Maria 改判为 B（净删、只留报告全文），则本条收缩为"删 `_metric_comparison_rows` + `_render_metrics_section` + `:486` 调用点"，**其余不变**。~~ 🔴 **v2.4：前置解除** —— **Maria 2026-08-06 已确认方案 A（界面自建块表）**，本条按上述原样落地，备选 B 不启用。
  - 🔴 **v2.4 追加**：`:178` 读的 `exec_result.get("metrics")` —— 该键已随 §2.6 删除 ⇒ **这不再只是"改数据源"，是"读一个不存在的键"**。②里那条"**不得兜底回退到 `metrics` / `metrics_groups`**"的负向断言因此**从约定升级为事实**（键都没了，想回退也无处可退），但**断言仍要写**：它守的是"不许换个名字把旧格子重建回来"。
- 🔴 **`ui/pages/plan_review.py`（护栏 1 + 护栏 3 展示，v2.0 新增）**：①成功标准在计划展示区**顶部**只读展示，**不得埋在一堆字里**（PRD §4.11.2），沿用既有"用户可调整任何部分"通道，**不新增交互种类、不新增按钮**；②`:1015` 的 `_render_plan_check_warnings` 调用**多传一个已在 payload 里的 `paper_analysis_summary`**（`:1005` 就在读它）⇒ **警示展示通道零改动、"不阻断审批"契约一字不动**。

### `config.py`
- **零改动**（新早停常量复用既有取值，Q-S8-04）。

---

## 13. 与 Sprint 5 §7.10 裁决的关系（显式留痕）

`docs/sprint5/architecture.md:323` 的二选一裁决（选文件扫描、弃扩展 `<METRICS>` 多块）**本次两条路都不再是主通道**：

- `<METRICS>` 通道：**整体退场**（决策 3）。
- ~~文件扫描 `_collect_grouped_metrics`：**保留为兜底**（S7-13 已把它降为"agent 一组都没报时才扫盘"，`execution.py:2961`），本次**不删、不改**——它是 agent 完全不服从时唯一还剩的数据来源（R-S8-09 提示词服从率实测约 75%）。~~
  - ~~🔴 **v2.1 补充（防止把「三档删了它为什么不删」读成标准不一致）**：**`_collect_grouped_metrics` 与三档不同类，不可类推** —— 前者**有生产调用者**，且其**数据源在本 Sprint 被强化**（编码侧新增结果文件产出约定）；后者的**输入源（`<METRICS>` 教学文本）本批被同批拆除**。⇒ **三档删、它留，是同一条标准的两种结论，不是双标。**~~
- 🔴 **v2.2 推翻上面两条：`_collect_grouped_metrics` 改判为整体删除。原文保留在上，不删，因为它记录的是一次判断的转向。**
  - **v2.1 那段补充里有一句话现在不成立了**：它说该函数「**数据源在本 Sprint 被强化**（编码侧新增结果文件产出约定）」。**恰恰相反** —— 编码侧的新产出约定是「结果文件**落在计划声明的位置**、**结构由编码环节自己定**」（决策 4 / PRD §4.2 第 2 条），而该函数硬编码了三条前提：目录必须是 `outputs/`（`execution.py:1730`）、文件名必须是 `summary.json`（`:1733`）、**只收顶层标量**（`:1749-1754`）。⇒ **新约定不是强化了它的数据源，是取消了对它那三条前提的保证。**
  - ⇒ 本版之后它的正确定性是：**从"有契约保证的兜底"降级为"碰运气才命中的兜底"**。留着它，等于留一个"看着像防线"的空壳 —— 与 `metrics[].source` 被砍时逐字记下的理由同款（`execution.py:1083-1089`：「**一个没有消费点的字段就是过度工程本身**」「不要先摆一个看着像防线的空壳」）。
  - **并且它自带预设形状**：组名 = 目录路径、结构 = `{组名: {指标: 值}}` 二维、只收顶层标量 —— 这三条正是本次回炉要拆掉的东西。**把它留作兜底 = 把旧格子留一半，在 agent 不服从时自动请回来**，与 PRD §4.6.5 #4「绝不兜底回旧的二维表」直接冲突。
  - 🔴 **代价如实登记，不粉饰**：删掉之后，**agent 完全不汇报结果块时，系统确实没有任何结果数据**（R-S8-20 的能力回退是真的）。**唯一的缓解不是重建兜底，而是既有的产物清单** —— `collect_artifacts`（`sandbox/local_venv.py:786-806`）的默认 glob 含 `*.png` / `*.json` / `*.csv` 等（`:797` docstring 逐字列着，取值在 `_DEFAULT_ARTIFACT_PATTERNS`），报告的「产物清单（Artifacts）」节（`reporting.py:884`）本来就在印这些路径。⇒ **裁定：块为空且 artifacts 非空时，结果节印一句「本次执行未汇报可展示的结果块；系统在产物清单中发现 N 个产物文件，请前往「产物清单」节自行查看」**。这是"如实说没有 + 指路"，不是"替 agent 编一张表"。
  - ⚠ **归属提醒**：**是否接受这次能力回退是 PRD §13 第 4 条、归 Maria 拍板**；本节提供的是**拍板依据的更正**（前提已被拆除），不是替她拍板。若她要求保留兜底，最小改法见 §16.6 的备选 B。

当年三条弃选理由今天的状态（PRD §0.2 发现②已实证，此处只作架构留痕）：①"需改 coding 产出约定"——本次正是要做的事；②"对已有回归样本不可用"——已过期（S7-13 真跑夹具已建）；③"解析仍依赖 agent 服从度"——选了文件扫描后依赖不但没消除反而更糟（那个约定从没进过编码提示词）。**⇒ 本次不是推翻当年的判断力，是推翻当年的前提。**

---

## 14. 跟改说明（§14.1/14.2 = v1.0 → v2.0 第四轮拍板；**§14.3 = v2.1 → v2.2 回炉；§14.4 = v2.2 → v2.3 重裁 AR-S8-10；§14.5 = v2.3 → v2.4 §13 五条拍板**）

### 14.1 逐条跟改清单

| # | 跟改项 | 落点 | 性质 |
|---|---|---|---|
| 1 | **Q-S8-02 扩围**：状态契约由"一处一键"变为"两处两键"，新增 `ReproductionPlan.success_criteria` | **新增 §2.5**（六个小节：结论 / 三备选 / 为什么一条就够 / 两层分离物理落点 / 默认值与 required / 标准缺失语义） | 扩围 |
| 2 | **Q-S8-05 扩围**：报告须展示本篇成功标准 | **新增 §5.7** | 扩围 |
| 3 | **「零新计划字段」表述作废** | §12 `planning.py` 条目改写（显式标注推翻 A-S8-02） | 表述订正 |
| 4 | **「唯一状态契约新增」表述作废** | 头部贯穿硬约束 / 架构级结论 / §2 标题与提要 / §5.6 末条 —— **全文四处已逐处清查改写** | 表述订正 |
| 5 | **新增 Q-S8-09**：护栏 3 的落点与判据 | **新增 §15** | 新增 |
| 6 | **批次与开工顺序重写**：撤回 v1.0 的调整案，接受 PRD v3.0 的 1a / 1b 拆分，追加两条前置约束 | §11 整节重写 | 重写 |
| 7 | **两层分离的架构级红线**：第一层进系统提示词 + 模块常量，第二层走动态通道，判定函数体内不得出现达标线 | 头部总纲 + §2.5.4 | 新增红线 |

**明确不重裁的一项**：**Q-S8-01（判定不进收集器）不受第四轮影响，本版一字不动。** 其论证基于**数据的产生方式**（终态一次写 vs 逐次累积），与"判据从哪来"正交——判据来源换成计划，agent 的收尾汇报仍然是终态一次写。主控已亲自复核关键论据（`react_base.py:665-672` 确实把 schema 强制结果同步追加成一条带结果标签的消息），结论成立。

### 14.2 🔴 编号撞车的登记与换发结果

**事实**：本文档 v1.0（先）与 PRD v3.0（后）各自新增了一个 `Q-S8-07`，**同一编号指两件事**。PM 出 v3.0 时不知道架构文档已占用该号。

| 编号 | 本文档 v1.0 已占用 | PRD v3.0 新增 | **换发结果** |
|---|---|---|---|
| `Q-S8-07` | 旧错误类别枚举成员必须保留（本文档 §7） | 护栏 3 落点 + `plan_checks.py` 红线再解锁 | **架构文档保持 §7 = `Q-S8-07`（先占先得）；PRD 那一项换发为 `Q-S8-09`** |
| `Q-S8-08` | 四档制用户可见文案连带面（本文档 §8） | — | 不变 |
| `Q-S8-09` | — | — | **新号，= PRD v3.0 的护栏 3 那项**（本文档 §15） |

**给开发的读法（务必按此对照，否则会去架构文档里找错条目）**：

- **一律以本文档编号为准。**
- 读 PRD v3.0 §8 表时，把那一行 **"Q-S8-07（护栏 3 落点与 `plan_checks.py` 红线再次解锁）" 读作 "Q-S8-09"**。
- PRD 里其余 `Q-S8-01` ~ `Q-S8-06` 与本文档**逐一对应，无偏差**。
- **架构不改 PRD**（铁律：只改本文档）⇒ 这处编号偏差**已知且留档在此**，不是遗漏。若 PM 后续改版 PRD，建议直接采用 `Q-S8-09`。
- 🔴 **v2.2 追记**：PRD v4.0 新增的 **`Q-S8-10`** 与本文档**不撞车**（本文档 v2.1 只占到 `Q-S8-09`），**编号直接沿用、无须换发**。本文档 §16 即 `Q-S8-10`。

### 14.3 🔴 v2.1 → v2.2 跟改说明（PRD v4.0 的 S8-06 / S8-07 回炉）

### 14.3.0 🔴 先记这一条：**本版是在纠正一次架构侧的误判**（不许省，留给后人看）

> **这一节写的是我自己判错的一次。** 不写下来，下次还会照同样的机制再判错一遍。

**被纠正的裁决**：v0.1 PRD 里 PM 提的「**停止压扁嵌套**」，被 **v0.2 的架构评估推翻**，改判为方案 A「把维度写进组名」。否决理由逐字记在 `docs/sprint8/prd.md` §12.1：「`reporting._lookup_metric_value`（`:160-176`）只从组内顶层取且只认数值」。

**这条否决有两处硬伤，均已上磁盘验实**：

| # | 硬伤 | 磁盘依据 |
|---|---|---|
| 1 🔴 | **拿来当约束的那个"现状"，在同一个 Sprint 内被我自己判了死刑。** `_lookup_metric_value` 正是本文档 §5.1 裁定「**整体删除**」的函数（理由写得很清楚："唯一调用点是 `_verify_trend`，留着就是死代码"）。⇒ **为了迁就一个下个批次就要拆掉的东西，把新设计改窄了；拆掉之后没有人回头把设计改回来。** | 否决理由指向 `reporting.py:160-176`；本文档 §5.1 同一行裁"整体删除" |
| 2 🔴 | **把「判定路径」与「渲染路径」混为一谈。** 窄的**只有判定路径**（`_lookup_metric_value` 只从组内顶层取、`:174` 排除 bool 与非数值）；**渲染路径一直是通用的** —— `_flatten_entries`（`reporting.py:440-471`）自 Sprint 5（AC-S5-20）起就能把任意嵌套 dict / list 递归降维成标量行，自带 `_NEST_MAX_DEPTH=4` 深度上限（`:435` / `:451` / `:465`）与显式省略占位（`:452` / `:466` "（嵌套过深，已省略）"）。⇒ **"下游读不了"这句话，对报告的渲染层从来就不成立。** | `reporting.py:435-471` 全文；`:174` 的数值过滤 |

**⇒ 我要留给后人的那条机制性教训（比结论重要）**：

🔴 **用"现有代码做不到"去否决一个新设计、或去缩小一个新设计之前，必须先问三个问题**：

1. **那段代码在本 Sprint 之后还活着吗？** 活着才算约束。**本 Sprint 自己要删的代码，不构成对本 Sprint 新设计的约束。** —— 这是一条**可机械执行的自查**：否决理由引用的每一个 `文件:行号`，都要去本文档的删除清单（§12）里搜一遍。
2. **做不到的是哪条路径？** 判定路径读不了 ≠ 渲染路径读不了。**"下游"不是一个整体**，笼统地说"下游"就是在把两条不同生命周期的代码路径捆在一起当理由。
3. 🔴 **（v2.3 新增，Maria 点名要求写进来）「我说的『做不到』，是只有这一条路做不到，还是所有路都做不到？」**
   - **可机械执行的自查**：凡是得出"**验不了 / 读不了 / 拿不到**"这类结论，必须**列出已经检查过的路径清单**，并说明**为什么这份清单是穷尽的**。**列不出清单的，那个结论不成立。**
   - **并且不许在"做不到"处停下** —— "按 A 做不到"的正确下一步是"**那按 B 呢**"，不是"那就不做了"。**"不做"是一个需要独立论证的结论，不能由"这一条路走不通"直接推出。**

🔴 **这三条不是三件事，是同一个毛病的三种长相：把"我检查过的那一条路"当成了"全部的路"。** 磁盘上已经有两次同形的实例：

| 次 | 我验证了什么 | 我下的结论 | 我没走的那条路 |
|---|---|---|---|
| 一（v0.2，本节上文） | **判定路径**读不了嵌套（`_lookup_metric_value` 只从组内顶层取、`:174` 排除非数值） | "下游读不了" ⇒ 把新设计改窄成方案 A | **渲染路径一直是通的**：`_flatten_entries`（`reporting.py:440-471`）自 Sprint 5 起就能递归降维任意嵌套 |
| 二（v2.2 的 AR-S8-10，本次） | **产物路径**验不了论文值（它不在 `code_output_dir` 下，验钞第④重必然不成立） | "验不了" ⇒ **论文值不进台账、不验钞** | **状态里的分析结果一直在那**：`baseline_results` 有完整记录链（`paper_analysis.py:45`/`:96`/`:224` → `state.py:80` → `planning.py:381`） |

⚠ **第二次比第一次更贵**：第一次的后果是"设计被改窄"（能力损失），**第二次的后果是"防线被打开"**（AR-S8-14：把对照基准往低了编 —— S7-11 反向激励的第三个变种，直接改的是"事实"那一侧，而基准看起来是"论文说的"、没人会去质疑）。**同一个思维毛病，第二次踩到的是安全面。**

**第三条（PRD §4.6.1 第 3 条独立挖出，架构复核同意）**：方案 A 还**违背了它自己引用的决策 4** —— 决策 4 原文（`docs/TODO.md:913`）后半句明写「维度信息可直接写进文件，**不必再靠目录名当组名**」，而方案 A 恰恰就是靠名字串承载维度。**⇒ 三条理由指向同一个结论：PM 的 v0.1 方向本来是对的。**

**并且坍缩根本不是 agent 造成的，是系统折的（已上磁盘验实）**：agent 汇报的本来就是**平坦记录数组**（`EXECUTION_OUTPUT_SCHEMA.metrics`，`execution.py:1114-1135`，每条是 `{name, value, group}`）；是 `_split_reported_metrics` 用 `collected.setdefault(group, {})`（`:1827`）把它折成二维、并在撞名时 `continue  # 先到先得`（`:1831`）**丢弃**。⇒ **"三维装不下"是折叠动作自己制造出来的伤，方案 A 是在给这道自伤打补丁；"撞名怎么办"（AR-S8-08）也是同一个动作制造出来的问题。不折叠，两个问题一起消失。**

**⇒ 本版遵守的新硬约束（Maria 2026-08-05 定为红线，非背景资料）**：

1. **结果长什么样由执行环节决定，代码不许预设格子与表头**（已进头部贯穿硬约束第三条总纲）。
2. 🔴 **不许再用"现有下游代码读不了"去约束新设计** —— 报告侧本 Sprint 本来就要大改。**这正是上次翻车的机制。**
3. ⚠ **也不许滑到另一个极端**：约束定在**语义层**，不得定在**形状层**。本文档 §16.1 的四条语义约束逐条标注了"为什么它是语义而不是形状"，就是为了守住这条边界的**两个方向**。

### 14.3.1 逐条跟改清单

| # | 跟改项 | 落点 | 性质 |
|---|---|---|---|
| 1 🔴 | **新增 Q-S8-10 全裁**：结果块契约 / 证据台账 / 收编落点 / 通用渲染 / 展示上限 / 截断检测 / 四个折叠扫盘函数的去留 | **新增 §16**（七个小节） | 新增 |
| 2 🔴 | **Q-S8-05 重裁**：报告侧由"改造指标对比表"改为"删除对比表 + 通用块渲染" | §0 表 / **§5.1 加四行** / **新增 §5.9 逐条差异表** | **重裁** |
| 3 🔴 | **新识别：degraded 形态不渲染任何结果章节** ⇒「仅代码跑通」那一档的结果块会整节消失 | **新增 §5.8** | **新识别缺陷** |
| 4 🔴 | **`conclusion` 形态由"内联证据"改为"台账 + 引用"**，并加 `result_blocks` / `report_caveats` 两个子键 | §2.1 重写（v1.0 原形态折叠保留） | 改判 |
| 5 🔴 | **`_collect_grouped_metrics` 由"保留为兜底"改为"整体删除"** —— 推翻 v2.1 §13 的补充 | §13 重写（原文保留划删）+ §16.6 | **推翻 v2.1** |
| 6 | **AR-S8-08（撞名策略）整条作废** —— 随折叠动作消失 | §10 表（划删保留） | 议题消失 |
| 7 | **新增 AR-S8-09 ~ AR-S8-13** 五条架构侧风险（块内数字不受验钞约束 / 论文值无产物物证 / `conclusion` 体积上界 / 旧快照重放不再含对比表 / schema 重生成路径的块内容） | §10 表 | 新增 |
| 8 | **步骤骨架 4.4 / 4.5 删除，4.75 扩为三函数** | §1.5 | 改判 |
| 9 | **批次 2 / 3 扩围 + 新增第三条前置约束**（PRD §13 五条拍板与批次的卡口关系） | §11 | 扩围 |
| 10 | **§12 开发交接清单跟改**：`state.py` / `execution.py` / `reporting.py` / `ui/` 四节 | §12 | 跟改 |
| 11 | **头部三处订正**：新增第三条总纲（语义层 / 形状层分离）、新增"上限不等于预设形状"的口径澄清、架构级结论重算（并订正 v2.1 少算的 reporting 函数数） | 头部 | 订正 |
| 12 | **§2.2 加同名防误读登记 + 子键不占状态契约额度的口径** | §2.2 表后 | 订正 |

**明确不重裁的三项**：

- **Q-S8-01（判定不进收集器）**：**本版一字不动。** 其论证基于**数据的产生方式**（终态一次写 vs 逐次累积），结果块与档位同源于**同一次 `<result>`** ⇒ 论证原样适用，块**同样**不进收集器。
- **Q-S8-02 §2.5（`ReproductionPlan.success_criteria`）**：**一字不动。** 本次回炉动的是"结果怎么呈现"，与"达标线由谁写"正交。
- **Q-S8-03 / Q-S8-04 / Q-S8-06 / Q-S8-07 / Q-S8-08 / Q-S8-09**：**均一字不动。**

### 14.3.2 本版**没有**做的事（避免下一位以为是漏做）

1. **不碰 `docs/sprint8/prd.md`**（PM 刚定稿 v4.0）、**不碰 `docs/sprint8/dev-plan.md`**（开发随后跟改）、**不碰两份全局文档**、**不碰 `docs/TODO.md`**、**不碰任何生产代码与测试**。
2. ~~**Sprint 8 的全局架构文档（`docs/technical-architecture.md`）回填仍不做** —— 代码零行，时机未到~~（架构 2026-07-28 自定的规矩：全局架构文档走**代码交付后回填**，与全局 PRD 走"先写计划"不同，MEMORY §3.5 末段已固化两者时机不同）。
   > 🔴 **v2.8 补注（`MEMORY` §3.9「处置栏写了 ≠ 磁盘改了」的同族：口径被推翻了，原句却原样躺着）**：**"时机未到"这个笼统口径已于 2026-08-07 被 Maria 当场推翻**，且**本句正是那次推翻的代价实证**（`MEMORY` §3.7 逐字引用了它）—— 同一天上午写下"本次不含任何 Sprint 8 回填"，下午批次 1a 就把 execution 工具列从 3 改成 5，全局文档的**现状表当场停在错的数字上**。<br>⇒ **现行口径（不许再读成一句"时机未到"）**：**现状数字（工具数 / 字段数 / 状态种类 / 签名形参）与磁盘不符属事实错误，任何时候即时订正，不受批次边界与提交状态约束；「代码交付后回填」只约束"要不要写这个功能的设计说明"。** 见 `docs/technical-architecture.md` v1.6⟦同日追加⟧段与 `MEMORY` §3.7。<br>⚠ **本条保留划删原文、不洗白** —— 它是本项目"等一等再改"这类失真的标本。
3. **不替 Maria 拍 PRD §13 那五条。** 本文档按其默认取值裁定，卡口关系写在 §11 前置③。

### 14.4 🔴 v2.2 → v2.3 跟改说明（重裁 AR-S8-10：论文报告值「换个东西验」）

**起因**：Maria 读 v2.2 的 AR-S8-10 后问「论文报告的数值会被系统自己判成"来源不可信"这一条，**论文值难道在前期的计划和分析节点没记下来**？」——**记下来了，四条记录链已上磁盘核实**（`paper_analysis.py:45`/`:96`/`:224` → `state.py:80` → `planning.py:381`）。

**v2.2 那条裁定的问题**：它写「论文值**不进证据台账、不参与验钞、不因此被标注异常**」——**治好了误判**（不会再印"论文报的 0.62 来源不可信"），**却打开了编造**：按此方案 agent 在块里写「论文报的是 0.95」时系统一个字都不核验，而四档里「复现成功」的达标线常写成「数值与论文报告对上」⇒ **把论文值往低了编，自己跑出来的数就"对上了"**（AR-S8-14）。

**思维毛病的定位**：「论文值不该按产物文件验」这一步**没错**，错在由此**直接推出**「那就不验了」——**中间少了一步「那就换个东西验」**。剖析与第三条自查见 §14.3.0。

| # | 跟改项 | 落点 | 性质 |
|---|---|---|---|
| 1 🔴 | **AR-S8-10 后半句改判**：论文值**进台账、按 `baseline_results` 核验两重** | §10 表（v2.2 原文划删保留） | **推翻 v2.2** |
| 2 🔴 | **新增 AR-S8-14**：把对照基准往低了编（S7-11 反向激励第三变种） | §10 表 | 新增风险 |
| 3 🔴 | **新增 §16.3.2**：两种出处两套验法 + 三个细节裁定（键名精确匹配 / 对不上只标注不封顶 / 空值一律不成立）+ 两条局限登记 + 「不违反 AC-S8-08②」的边界澄清 | §16.3 拆为 16.3.1 / **16.3.2** | 新增 |
| 4 | **`_verify_evidence` 多一个形参 `baseline_results`** | §3.1 / §12 | 扩围 |
| 5 | **schema 的 `evidence` items：加 `metric`、去掉 `required: ["path"]`** | §16.2 | 订正 |
| 6 | **§16.1 第③条接口补丁重写**；提示词新增"用原键名引用论文值"一句 | §16.1 / §12 | 改判 |
| 7 | **§16.7 验证：B12 重写 + 新增 B18（★命门·须验红）/ B19 / B20** | §16.7 | 扩围 |
| 8 🔴 | **§14.3.0 立第三条机制性自查**：「我说的『做不到』，是只有这一条路做不到，还是所有路都做不到？」+ 两次同形错误对照表 | §14.3.0 | **新增红线** |

**不变的三项（明确登记，免得以为顺手改了）**：

- 🔴 **AR-S8-04 一字不动**：`_decide_conclusion` 仍然**只读 `level` + 数封顶**，不读证据形态、**不读出处**、不解析证据语义。新分支只落在 `_verify_evidence` 里。
- 🔴 **不新增第四条封顶**：编低的论文值走**既有两个出口**（逐条落「无法核实」/ 支撑物证全不成立时封顶 3），零新机制。取向与 §2.5.6 逐字同源。
- **§3 两个闸的边界不变**：工具层 `_is_within_workspace` 一字不动；证据边界仍限 `code_output_dir`（**它管的本来就只是产物物证**）。

**交主控派单的一条（架构不改 PRD）**：PRD §4.6.2 语义层约束②「每块要说清数据来自哪个产物、哪一步」**只覆盖了产物那一侧**。建议 PM 扩为覆盖两种出处（或新增约束⑤：引用论文报告值时须指明它对应论文分析里的哪个指标名）。**约束③的条件句写法不必改**——本次裁定使它从劝导变成可执行。

### 14.5 🔴 v2.3 → v2.4 跟改说明（Maria 拍完 PRD §13 五条）

**拍板结果**：第 2 / 3 / 4 / 5 条**确认架构默认取值**（相关章节一字未动，逐条对照见 §11 前置③ 的表）；🔴 **第 1 条推翻默认取值 —— `ExecutionResult.metrics` / `metrics_groups` 本 Sprint 删键**，原话「**旧字段要是确认没有用了就删掉**」。

| # | 跟改项 | 落点 | 性质 |
|---|---|---|---|
| 1 🔴 | **两键删除**（含前置条件核实、落点、键数账） | **新增 §2.6**（五个小节） | **Maria 推翻架构默认取值** |
| 2 🔴 | **复核并推翻我自己那笔"71 处回归面"的账** | §2.6.2 | **自我订正** |
| 3 🔴 | **两处被突破的自设约束显式处置**：「状态契约新增上限两处」改写为「新增两处 + 删除两处」；**`dev-plan.md:1358` CP-2.10-3 判定为推翻并换发**（不是放宽、更不是删掉） | §2.6.4 + 头部 | **例外登记 + 派单** |
| 4 | `_build_execution_result` 两个形参**由"保留带默认值"改判为一并删除** | §12（v2.2 原文划删保留） | 改判 |
| 5 | `core/state.py` 落点由"注释改写为停产说明"改判为"两行声明删除 + docstring 加删键留痕" | §12（同上） | 改判 |
| 6 🔴 | **§7 加一层边界**：`Enum` 成员必须保留 vs `TypedDict` 键可以删 —— **判别式是"有没有运行时成员查找"**，三个带 metrics 的东西不可类推 | §7 | 新增边界澄清 |
| 7 | §0 表 Q-S8-02 行、§5.9 第 7 条、§16.6 末行、§12 `ui/` 条目跟改；§11 前置③ 由"三条卡口"改写为"已全部结清"表 | 各处 | 跟改 |
| 8 | **顺带发现**：`mypy.ini` 债务清单的行号注释本 Sprint 后大面积失真（`:124` L520 在被删的 `_parse_metrics` 内、`:146` L995 在被删的 `_render_metrics_comparison` 内），且这是一次零成本 ratchet 收紧机会 | §2.6.5 | **新登记，交开发** |

**🔴 本次自我订正里最该被记住的一条（§2.6.2 末段）**：我把 **`grep` 命中数当成了改动面**。71 处里绝大多数**在"停产"那一刻就已经要改了**，把它们记在"删键"头上，等于**把一笔本来就要付的账重复计了一次**，再用它去支撑"不要删"。⇒ **给改动面报数，必须先按"做这条改动 vs 不做这条改动"做差，不能拿总命中数当差值。**

**不变的三项**：

- **§16 全部裁定**（结果块契约 / 台账 / 收编 / 渲染 / 上限 / 截断检测）**一字未动** —— 第 2/3/4/5 条确认了默认取值。
- **§16.3.2（v2.3 论文值两重核验）一字未动** —— 与本次删键正交。
- **§7 的核心裁定一字未动**：`ErrorCategory.NO_METRICS` 枚举成员**仍然必须保留**（新增的只是它与状态键的边界澄清）。

### 14.6 🔴 v2.4 → v2.5 跟改说明（开工期裁定 `AR-S8-15` 补落章 + 一次编号撞车换发）

**起因（两条，均由主控 2026-08-07 上磁盘核实挖出，开发代理与我均未报）**：

1. 🔴 **该裁定压根没落进本文档。** 2026-08-07 我对「`state.py` 两键迁出」作了裁定，**但只写进了 `docs/sprint8/dev-plan.md`，`git diff --stat docs/sprint8/architecture.md` 为空**。⇒ **与 sp7 的 `Q-S7-25~31`「只活在 dev-plan、architecture.md grep 零命中」完全同型** —— 而那条 2026-08-06 才刚补完落章（`caacafc`）。**同一个病，隔一天在同一个人身上复发。**
2. 🔴 **编号撞车**：该裁定被编成 `AR-S8-13`，而本文档 v2.2 早已把 `AR-S8-13` 分配给「schema 重生成路径的块内容」（§10 表 + §14.3.1 第 7 条 + §16.4 三处）。**同一编号指两件完全不同的事**，且新号已在 dev-plan 八处以上被引。

| # | 跟改项 | 落点 | 性质 |
|---|---|---|---|
| 1 🔴 | **新增 §17：`AR-S8-15` 全裁**（触发事实 / 三条退路穷尽性论证 / 裁定内容 / 旁证 / `R-S8-42` / 六条检查点去向 / 元教训 / 连带清单） | **新增 §17**（九个小节） | **补落章** |
| 2 🔴 | **编号换发**：本文档 §10 的 `AR-S8-13` **保持不动**（先占先得，同 §14.2 处置 `Q-S8-07` 的规则）；开工期裁定换发为 **`AR-S8-15`**（`grep` 全仓核实未占用） | §17.0 + §10 表 `AR-S8-13` 行加防误读标注 | **换发** |
| 3 🔴 | **§11 前置约束 1 改判**：原文「1a 只保留 `core/state.py` 的键声明（TypedDict 加键，无运行时约束）」——**括号里那句只在运行时成立**。改判为 **1a 对 `state.py` 零改动**（原文划删保留） | §11 | **推翻自己的 v2.1 表述** |
| 4 | **§12 `core/state.py` 条目补批次归属**：两个加键各自落 `T-S8-1b-2` / `T-S8-2-8`；并澄清「本文件被触碰两次」不违反 MEMORY §1.2（那条治的是**并行**，1b 与 2 是**串行**） | §12 | 补明确 |
| 5 | **§10 新增 `AR-S8-15` 行**（含 `reporting.py:581` 被 `mypy.ini:150` 压制这个此前未记的例外面） | §10 表 | 新增风险 |
| 6 | **`P-S8-17` 要求的「§5.5 末段订正」判为无须再做** —— **v2.4 已经改过**（`:534-537`）。`docs/TODO.md:1129` 那条是照 dev-plan 转述记账、未核对本文档 | §17.8 末 | **销账，非跟改** |
| 7 🔴 | **顺带订正一处悬空交叉引用**：§10 `AR-S8-04` 行写「**AC-S8-07④** 的负向静态断言对象」，而 **PRD `:477` 的 AC-S8-07 只有 ①②、没有 ④**；该断言的正身是 **`AC-S8-08②`**（PRD `:478`「代码里不存在按证据形态分支的逻辑」）。**本文档 §14.4 / §16.3.2 与 `dev-plan.md:184` / `:1267` 用的一直是 `AC-S8-08②`，唯独 §10 这一行没跟上，且错了三版无人对表** —— 与 BUG-S7-11-01 同型（同一个东西两处措辞不同，实现照抄错的那份）。原文划删保留 | §10 表 `AR-S8-04` 行 | **订正悬空引用** |

**不变的三项（明确登记，免得以为顺手改了）**：

- 🔴 **两键的声明形态一字未动**：仍是**普通必填键**，**不改 `NotRequired`、不动 `total`**（§2.1 `:156` / §2.5.1 `:269`）。本次动的**只是排期**。
- 🔴 **§16 全部裁定、§16.3.2、§2.6 删键裁定一字未动** —— 与本次排期调整正交。
- **零检查点被放宽**：六条迁出的 `CP-1a.2-*` **逐条给去向**，另新增两条验红要求（`R-S8-42` 的活体证明）。⇒ **本次是"账搬了个地方"，不是"账少了"**（旁证见 §17.4）。

🔴 **本次最该被记住的一条（§17.0 末段）**：**编号是本文档的资源，发号必须当场落回本文档。** 这次撞车与 §14.2 那次不同 —— 那次是两位作者互不知情，**这次是同一个作者在同一份文档里隔两版把自己的号发了两遍**，病根就是**裁定当场没回本文档登记**。⇒ **"裁定不落章"不只是欠一份文档，它连自己的编号都守不住。**

### 14.7 🔴 v2.5 → v2.6 跟改说明（架构侧文档失真收口，2026-08-09 Maria 授权直接落盘）

**起因**：批次 1a/1b 执行期，开发代理与主控在 `dev-plan.md` §15 登记了一批落点勘误，其中**三条的对象是本文档**（`P-S8-21` / `P-S8-34` + 一条上游 docstring 失真），**而本文档一直零跟改** —— 与 §14.6 记的 `AR-S8-15`「只写进 dev-plan、架构文档 grep 零命中」**是同一个病**。⇒ **本次一次性收口。零生产改动。**

| # | 跟改项 | 落点 | 性质 |
|---|---|---|---|
| 1 🔴🔴 | **§16.5① 补全 `mask_value` 对非 str 的三种行为 + 三条写死的处置口径**（`P-S8-21`）：原文只写"每个 `title` / `note` / `cell` 过 `mask_value`"，**未说非 str 怎么办**；`P-S8-12` 登记为两种行为，**磁盘实测是三种** —— 第三种（有凭证 + **falsy** 非 str）被 `core/secrets_store.py:268-269` `if not text: return text` 提前拦下，**不抛异常、静默漏过脱敏**。🔴 而 `0` / `False` 恰是指标场景最自然的取值 | §16.5①（代码块 + 新增"①的展开"整段） | **补覆盖面 + 立实现纪律** |
| 2 🔴 | **新增验证项 `B22`**：非 str 单元格 truthy / falsy / str 三组齐验，**并把"夹具只留 truthy 组即为假绿"写进验红条件** | §16.7 表（B7 之后） | **新增验证** |
| 3 | **登记一处上游 docstring 失真**（只登记、`core/` 零改动）：`mask_value` docstring（`:266`）只写「text 为 None / 空串返回原值」，**与实际 `not text` 的覆盖面不符**（还吃掉 `0`/`False`/`[]`/`{}`）。**这正是 `P-S8-12` 只登记到两种行为的来源** —— 读 docstring 会以为非 str 必定走到 `.replace` | §16.5① 末 | **登记，交主控排期** |
| 4 🔴 | **§2.5.4 红线 3 处数订正**（`P-S8-34`）：~~"只允许三处"~~ / ~~`P-S8-10` 的"四处 / 五处"~~ 两版**都漏了 `core/plan_checks.py`** ⇒ 改为**逐文件 6 处**并给出「文件 / 角色 / 批次 / 磁盘现状」落点表（原文划删留痕） | §2.5.4 红线 3 | **订正现状数字** |
| 5 🔴 | **显式注明 `plan_checks.py` 那一处属"警示"不属"判定"、红线 2 未被破**，并在 §15.3 末加**反向交叉引用**（防单向可读：从红线 3 能读到 W6，从 W6 也能读到红线 3） | §2.5.4 + §15.3 末 | **防误读** |

**明确不动的（免得以为顺手改了）**：

- 🔴 **红线 2 一字未动。** 它守的是判定链路（`_decide_conclusion` 不得读达标线），W6 只产不参与判定的警示 —— **两者不冲突，不是"红线被放宽"**。
- 🔴 **`core/` `ui/` `tests/` 零改动**（本轮为文档轮次），三条需要动代码的（`_collect_result_blocks` 的 isinstance 处置 / `CP-2.10b-7` 夹具 / `mask_value` docstring）**只出纪律与落点，交主控排期**。
- §16 其余全部裁定、§16.3.2、§2.6 删键裁定、批次划分**一字未动**。

🔴 **本次最该被记住的一条**：**`P-S8-21` 的价值不在于"多发现一种行为"，而在于它指出「按抛不抛异常来分支」这个最自然的写法恰好会漏掉整个 falsy 组，且该漏洞在红态与绿态下都看不见。** ⇒ 立一条通用取向：**凡处置"外部函数对畸形输入的反应"，判据必须是输入的类型/取值，不能是该函数的失败表现** —— 失败表现是它的实现细节，会因为一个早退分支而整片消失。（与 MEMORY §6「一道防线正确的设计可能恰恰让它不可观测」同族：那条讲仪器恒读 0，这条讲**异常恒不抛**。）

### 14.8 🔴 v2.6 → v2.7 跟改说明（裁定 `AR-S8-16`：W6 的第三个候选源在生产链路上恒为空）

**起因**：2026-08-09 主控上磁盘核出一条**已交付代码里的活缺陷**（不是排期问题、不是文档失真）——§15.3 第 1 条的三源候选集在 `core/plan_checks.py` 里**三条全实现**，而生产链路喂给它的是 `planning.py::_digest_paper_analysis` 的 **4 键摘要**，`baseline_results` 从未进过那个 dict。⇒ 护栏在它最该沉默的地方最吵（引用论文自报基线的达标线必被误报），在它最该出声的地方沉默（`metrics`/`datasets` 皆空时护栏静音）。

| # | 跟改项 | 落点 | 性质 |
|---|---|---|---|
| 1 🔴🔴 | **新增 §15.3.1：`AR-S8-16` 全裁**（事实三层表 / 两个危害方向 / 三条替代路逐条否决 / 一条新登记的局限） | **新增 §15.3.1** | **裁定** |
| 2 🔴 | **§12 `core/nodes/planning.py` 加一条落点**：`_digest_paper_analysis` 加第 5 键 `baseline_results`，附**四条实现纪律**（原样透传 / 恒常给键 / 只加这一个键 / 不新增外层 payload 键）+ 一条截断纪律澄清 | §12 | **交接清单** |
| 3 🔴 | **§15.5 新增 G8 / G9 / G10 三条验证**：G8 是本裁定的**唯一承载**，写死「**输入必须由 `_digest_paper_analysis` 真实产出，禁止手搭 payload**」+ 验红条件<br>🔴 **v2.8 订正（`BUG-S8-G8`，见 §14.9 / §15.5.1）**：~~"+ 验红条件"那半句作废~~ —— G8 自带的验红条件（「摘键 → 本条必红」）**与它的期望值「不报 W6」互斥**，按字面实现必得恒绿用例。**「输入必须由 digest 真实产出」这半句完全正确、一字不动。** | §15.5 表 | **新增验证**（🔴 **v2.8 已订正**） |
| 4 🔴 | **新增 §15.6：与 `T-S8-2-8b` 的口径关系** —— **取值统一（同字段 / 同键名 / 同语义），注入形态刻意分开**（恒常给键 vs 非空才注入），并写死**不抽公共 helper**、**不得借本裁定放开 execution 侧注入面** | **新增 §15.6** | **裁定（Q3）** |
| 5 | **§10 新增 `AR-S8-16` 行** | §10 表 | 新增风险 |

**明确不动的（免得以为顺手改了）**：

- 🔴 **`core/plan_checks.py` 一字不动**：W6 判据、`_paper_fact_terms` 的三条候选源、`check_plan` 签名、既有五条 W —— 全部原样。**判据没错，错的是喂它的管子。**
- 🔴 **外层 payload 键集合一字不动**（仍 11 键）⇒ `tests/test_sprint4_e2e.py` 与 `tests/test_sprint7_s708_payload_probe.py` 两道守门**零改动、零换发**。
- 🔴 **§15.4 的原局限一字不动**（"挡的是空话，挡不住具体但宽松"）；§15.3.1 末新登记的是**另一条**局限（"引用论文文字结论、不带专有名词的达标线仍会被误报"），两者并列、不互相取代。
- 🔴 **`T-S8-2-8b` 的注入面、`A-S8-07`、§6.1 / §16.3.2 全部一字不动。**
- **批次归属**：本项属**批次 1b 的收尾修补**（W6 是 1b 交付物），**不开新批次、不改批次划分**；改动面 = **1 个文件 / 1 行新增**，可与批次 2 并行落盘（文件边界与批次 2 的 `execution.py` / `reporting.py` 零重叠）。

🔴 **本次最该被记住的一条**（⚠ **v2.8 补一句：这条没错，但它只覆盖"输入侧"，输出侧的同族病见 §14.9**）：**"判据实现了"与"判据在生产链路上拿得到原料"是两笔账，只核前一笔必然漏。** 本缺陷躲过了 11 键守门（守外层、不守内层）、躲过了 `CP-1b.3-*` 与 `CP-1b.4-3`（用例**手搭**了消费侧的输入，把接缝整个测掉）。⇒ 立一条通用取向：**凡"A 产出、B 消费"的判据，验证用例的输入必须由 A 真实产出；手搭 B 的输入，测的是 B 的能力，不是这条链路。** 这与 MEMORY §3.8「清账必须双向」同族——那条讲文档与代码要互查，这条讲**判据与它的喂料口要互查**。

### 14.9 🔴 v2.7 → v2.8 跟改说明（订正 `BUG-S8-G8`：G8 的期望值与它自带的验红条件互斥）

**起因**：测试工程师在实现 v2.7 新增的 G8 时实测发现，**G8 那一行同时写死的两句话不能同时成立** —— 期望「不报 W6」＋ 验红「摘掉 `_digest_paper_analysis` 的 `baseline_results` 键 → 本条必红」。摘键后候选集塌成空，`check_plan` 走 §15.3 第 3 条早退，**照样不报** ⇒ 谁按 G8 字面只写一句 `assert "W6" not in rules`，那条用例**恒绿没牙**。主控已做两态对照复验。取证：`docs/sprint8/test-reports/2026-08-09_w6-criteria-guard-02.md` §三。

⚠ **这是 v2.7 自己刚治好的病，换了一层复发**：v2.7 治的是「用例的**输入**手搭了消费侧」；G8 输入接对了，栽在**输出侧的期望值是 negative 的**。

| # | 跟改项 | 落点 | 性质 |
|---|---|---|---|
| 1 🔴🔴 | **G8 期望值由一句行为断言改为三条按序断言**（①摘要含键 / ②候选集含 `BM25_R2` / ③不报 W6），并写死「**验红只由 ①② 承载，③ 在摘键前后同为不报、不承载任何验红**」 | §15.5 表 G8 行 | **订正**（推翻 v2.7 自己的表述） |
| 2 🔴 | **G9 升格**：由"顺带的反向补充"升为「**摘键验红在行为面的唯一红灯**」，写死不得删 / 不得弱化 / 不得与 G8 合并成双口径用例，且**必须与 G8 共用同一份 raw 输入与 digest** | §15.5 表 G9 行 | **升格 + 加约束** |
| 3 🔴 | **新增 G11（内层键集合精确 5 键 + 恒常给键）**，并在 G10 上标明"只管外层" | §15.5 表 | **新增验证**（补 §15.3.1 自己点名却没进表的缺口） |
| 4 🔴🔴 | **新增 §15.5.1**：两态对照实测表 + **「为什么当初会写成互斥」三层成因** + 通用判据 **`R-S8-43`** + 与 MEMORY §6「过渡态设计」的辨析（那条是双口径变体，本条是**单口径**变体） | **新增 §15.5.1** | **裁定（通用判据）** |
| 5 | **§15.3 第 3 条第一个边界加一条副作用注**：该早退位于"读达标线"之前 ⇒ **任何上游断供型失效在行为面都表现为"整个沉默"而非"误报"**，不得拿"W6 没报"证明喂料是通的 | §15.3 | **交叉引用** |
| 6 | **§15.3.1「为什么躲过全部绿灯」补第 4 条**：一条裁定当场为自己写的验证条目，是最容易漏审的一处 | §15.3.1 | **复盘补录** |
| 7 | §14.8 表第 3 行「+ 验红条件」半句划删留痕；§14.8 末「最该被记住的一条」补一句作用域澄清（它只覆盖输入侧） | §14.8 | **划删留痕** |
| 8 🔴 | **`R-S8-43` 立完当场全表回扫（`MEMORY` §3.10 跟改收尾）**，捞出**同族的第二处**：**G1 / G3 / G5 三行都是 negative 期望，而 v2.7 的 G7 只写了一种突变（删判据）** ⇒ 这三行在文档层面**一条验红都没有**。⇒ **G7 由一种突变扩为三种**（删判据 / 无条件上报 / 早退失效），并**逐一点名各自会红的条目**；G1 / G3 / G5 行内加验红指针 | §15.5 表 G1 / G3 / G5 / G7 行 | **扩围**（`R-S8-43` 的首次应用） |
| 9 | **顺带清一处口径失真**（本轮全文 grep 收网时捞出，与 `BUG-S8-G8` 无关）：§14.3.2 第 2 条「全局架构文档回填仍不做 —— 时机未到」，该口径已于 **2026-08-07 被 Maria 当场推翻**（且本句正是 `MEMORY` §3.7 逐字引用的代价实证），而**原句一直原样躺着、零跟改** ⇒ **划删留痕 + 补现行口径**（现状数字即时订正 / 回填制只约束设计说明） | §14.3.2 第 2 条 | **划删留痕 + 口径订正** |

**明确不动的（免得以为顺手改了）**：

- 🔴 **`tests/` 一字不动，`core/` `ui/` 一字不动。** 测试工程师**已按订正后的口径实现**（`tests/test_sprint8_s811_w6_criteria_guard.py`，136 条，摘键后新红 5 条）⇒ **本次是文档追认实现，错的是 v2.7 的文档，不是测试。**
- 🔴 **`AR-S8-16` 的裁定本体（加第 5 键、不动 W6、三条替代路的否决）一字不动。** 本次订正的只是**它的验证条目怎么写**，不是它裁得对不对。
- 🔴 **§15.3 第 3 条「候选集为空则不报」这条早退一字不动。** 它是 `R-S6-A5` 名下正确的误报防线 —— **正因为它是对的，才轮到验证条目去适应它**，而不是反过来把设计改成能被那条用例验到。⚠ 谁要是读完 §15.5.1 得出"应该取消早退让它红"的结论，那是把本条读反了：取消早退 ⇒ 既有 37 处两参调用集体被打上 W6 ⇒ G5 契约当场破。
- 🔴 **§15.4 的原局限、§15.3.1 末新登记的那条局限、§15.6 的路 α / 路 β 口径 —— 全部一字不动。**
- **批次归属**：仍属**批次 1b 的收尾**（同 §14.8），**不开新批次、不改批次划分**；本次为**纯文档轮次，改动面 = 1 个文件（本文档）**。

🔴 **本次最该被记住的一条**：**一条"期望什么都不发生"的验证，它自己不会告诉你它有没有牙。** 表里写一句"本条必红"是**零成本**的，而它会被后来的实现者当成"已经验过了" ⇒ **凡写下 negative 期望，必须同时点名"该红的那一次由哪句断言红"，并把那句断言也写进表里**（`R-S8-43`）。⚠ 更贵的一层：**这一行是裁定自己为自己配的验证** —— 裁定正文被三条替代路夹着反复推敲，而验证表那一行一遍写成就再没人看。**给自己的裁定配验证时，要像审别人的裁定那样审它。**

---

## 15. Q-S8-09（v2.0 新增）：护栏 3 的落点与判据

> **产品决策不推翻**：护栏 3 **只产警示、不阻断审批**（PRD §4.11.3 + A-S8-10）。本节只裁"落在哪、怎么判、怎么验"。

### 15.1 结论

判据落在 `core/plan_checks.py` 新增 **W6**，走既有 `check_plan` → `ui/pages/plan_review.py:786` 那条**「只产警示、不阻断审批」的现成通道**：**零新机制、零新展示通道、零新交互种类**。

### 15.2 🔴 一处必须处置的实现冲突（PRD 未察觉）

**PRD §8 要求「须保证 `check_plan` 函数签名与既有五条警示行为一字不变」，但 W6 的判据要用论文分析的事实层名词，而现签名 `check_plan(plan, resource_info)` 拿不到 `paper_analysis`。** 这两条不能同时满足。

| 方案 | 做法 | 取舍 |
|---|---|---|
| **A（采纳）** | 加**第三个带默认值的关键字形参** `paper_analysis: Optional[Dict[str, Any]] = None` | ✅ 既有两个调用点（`plan_review.py:786` + 既有测试）**不改也能跑**，默认 `None` ⇒ W6 不触发 ⇒ **既有行为字节级零扰动**；✅ 既有五条 W 的 rule 字符串、message、触发条件**一字不动**；✅ 判据强度与 PRD 原意一致。**代价：签名"逐字不变"不成立** ⇒ 精确表述应为「**向后兼容、既有调用零改动、既有警示行为一字不变**」 |
| B | 判据改为只看计划内部（`expected_results` 的指标名 / `expected_output` 的文件名） | ❌ 签名真的一字不变，但**判据被掏空**：计划自己引用自己，论文里报了什么它根本不看；PRD 判据原文明写"来自**论文分析**的事实层名词" |
| C | 把 W6 放进 UI 侧（`plan_review.py` 手上就有 `paper_analysis_summary`） | ❌ 把确定性判定塞进展示层：不可单测复用、无法被其他调用方共享，且违反 `plan_checks` 作为"零 LLM 纯函数集中点"的既有取向 |

⇒ **采 A。** 这是对 PRD 一句实现约束的**精确化**（属"怎么实现"层，架构可裁），已如实登记在此，不静默通过。

### 15.3 判据实现（纯字符串、零 IO、可单测、低误伤）

1. **候选集** = `paper_analysis` 的 `metrics`（列表元素）+ `datasets`（列表元素）+ `baseline_results`（**字典的键**）三处的事实层英文名，去空白、去空串。
   > 🔴 **v2.7 必读，不许单独读本条**：这三条在 `core/plan_checks.py:505`/`:522` **都实现了**，但**第 3 条在生产链路上恒为空** —— 喂进来的不是 `paper_analysis`，是 `planning.py::_digest_paper_analysis` 的 **4 键摘要**。裁定与三条替代路的否决见 **§15.3.1（`AR-S8-16`）**。
2. **命中判定**：`success_criteria` 文本中出现任一候选（**大小写不敏感的子串匹配**）⇒ **不报**；一个都没出现 ⇒ **报 W6**。
3. **两条边界（沿 `plan_checks` 既有"宁窄勿宽"误报防线 R-S6-A5）**：
   - **候选集为空**（论文分析没产出任何事实层名词）⇒ **不报**。无从比对时报警只会制造噪声。
     > 🔴 **v2.8 补一条必读的副作用（`BUG-S8-G8`，全文见 §15.5.1）**：这条早退**位于"读 `success_criteria`"之前**，因此**任何"上游断供"型失效模式**（喂料被摘、字段改名、摘要漏键）在**行为面都表现为"整个沉默"，而不是"误报"**。⇒ **不要拿"W6 没报"去证明喂料是通的** —— 喂料断了它也不报。**这类失效必须靠结构面断言（候选集里有没有那个名词）或靠一条正向验证（空话标准还报不报）去抓**，判据见 `R-S8-43`。
   - **`success_criteria` 为空串** ⇒ **报**。空标准是最该被用户看到的一种，不能因为"没内容所以没法判"就沉默。
4. `rule` 字符串用 `"W6"`（沿既有字面量风格，**不建 Enum**）；`message` 用通俗中文、**不得出现内部字段名**（MEMORY §4.2）。

---

#### 15.3.1 🔴 v2.7 裁定 `AR-S8-16`：第 3 个候选源在生产链路上恒为空（判据没错，喂它的管子断了）

**事实（已上磁盘核实，2026-08-09，HEAD=`8a03bbc`）**：

| 层 | 现状 |
|---|---|
| 判据侧 | `core/plan_checks.py::_paper_fact_terms`（`:499-528`）**三条候选源全实现**：`metrics` 元素（`:515-520`）、`datasets` 元素（同上）、`baseline_results` 的**键**（`:522-527`） |
| 生产侧 | `ui/pages/plan_review.py:1080` 传的是 `payload["paper_analysis_summary"]`，而它由 `core/nodes/planning.py::_digest_paper_analysis`（`:884-896`）产出 —— **只输出 4 键**：`method_summary` / `datasets` / `metrics` / `framework`。**`baseline_results` 被丢掉** |
| ⇒ 后果 | 第 3 条候选源**在生产链路上恒为空**。判据本身一行都没错，**错的是喂它的那根管子** |

**两个危害方向，误报那个更难受**：

1. 🔴 **误报（主害）**：达标线写「复现出论文 Table 2 里 `BM25_R2` 报告的 0.43」—— **这是最扎实的一种达标线**（直接引用论文自报基线，可逐字对着论文核），而护栏跳出来说"你没点到论文里任何具体指标"。**与本模块 `R-S6-A5`「宁窄勿宽 / 宁漏报不误报」的取向正好相反**：这条护栏在它最该沉默的地方最吵。⚠ 真实语料佐证：`tests/test_paper_analysis.py:912` 的夹具正是 `{"BM25_R2": 0.43}`。
2. **漏报（次害）**：论文分析 `metrics` / `datasets` 都空、只有 `baseline_results` 时，候选集空 ⇒ 第 3 条边界早退 ⇒ **纯空话标准也不报**（G3 边界被误触发）。

🔴 **它为什么躲过了全部绿灯（这一条比缺陷本身更该被记住）**：

- `tests/test_sprint7_s708_payload_probe.py:60-66` 的**精确 11 键**守门断的是**外层** payload；`paper_analysis_summary` 的**内层键无人守** ⇒ 它可以少一个键而全仓无一处变红。
- `dev-plan` `CP-1b.3-*` / `CP-1b.4-3` 的用例**手搭 payload**、直接把 `baseline_results` 塞进 `paper_analysis_summary` ⇒ 用例证明的是「**这个能力存在**」，不是「**这条链路通**」。⇒ 通用教训：**凡"A 产出、B 消费"的判据，用例的输入必须由 A 真实产出，手搭 B 的输入等于把接缝测掉了。**
- 提示词侧的**同源反证**：`planning.py:270` 明写达标线的推导原料是「`metrics`、`datasets`、`baseline_results`、`method_summary`」⇒ **系统一边教模型引用论文自报基线，一边用一个看不见基线的护栏罚它。** 提示词与判据取的原料清单不一致，本身就是缺陷的独立证据。

🔴 **v2.8 追加的第 4 条（`BUG-S8-G8`，全文见 §15.5.1；不许当成"顺带一提"）**：**本裁定为自己配的那条验证（v2.7 的 G8）犯的是同一族的错，只是换了一层。** 上面第 2 条讲的是「用例的**输入**手搭了消费侧、把接缝测掉」；G8 把输入接对了，却栽在**输出侧** —— 它的期望值是 negative 的（"不报 W6"），而摘掉喂料后由于 §15.3 第 3 条早退，**答案不变**，用例恒绿。⇒ **"输入由上游真实产出"只是必要条件，不是充分条件；还得问一句「该红的那一次由哪句断言红」**（`R-S8-43`）。⚠ **这也说明：一条裁定当场为自己写的验证条目，是最容易漏审的一处** —— 裁定正文被反复读、被三条替代路夹着推敲，而验证表那一行往往一遍写成就没人再看。

**裁定：加键。改 `_digest_paper_analysis`，不动 W6。** 实现纪律见 §12 `core/nodes/planning.py` 条目（四条，一条都不许打折）。

🔴 **三条替代路逐条否决（"加键是不是错的"已当场自问，答案是没错；但下面三条都错）**：

| 替代路 | 为什么否决 |
|---|---|
| **B：收窄 W6 口径**（候选集去掉 `baseline_results`，只留 `metrics` + `datasets`），同步改 §15.3 第 1 条 | ❌ **这是把文档改成与 bug 一致，不是修 bug。** ①它**根本不解决主害**：`BM25_R2` 那条达标线在 B 之下**照样被误报**（基线键名不会出现在 `metrics` 列表里）；②它把危害 2 从"缺陷"追认成"设计"——`metrics`/`datasets` 皆空的论文，护栏**永久静音**且无人知情；③它与 `planning.py:270` 的提示词原料清单**继续对不上**，缺陷换个位置活着 |
| **C：截断**（只送前 N 个键 / 只送键不送值） | ❌ **截键 = 把缺陷改小而非改掉**：基线表大的论文（真实 e2e 里 HippoRAG 的 baseline 就是"5 大类对比表"）恰好被截掉尾部键 ⇒ **误报按论文规模重新出现，且不可观测**。❌ **截值（保键去值）= 同名不同型跨路径**：`baseline_results` 在 state / execution 注入 / 本 payload 三处将有两种形状，而本项目已因"同一个东西两处口径不同"栽过（`BUG-S7-11-01`）；且审核页「论文分析摘要」与讨论助手会拿到**一份数值全被抹掉的假表**，比不给更糟 |
| **D：把 `method_summary` 收进候选集**（理由：`_W6_MESSAGE` 与提示词都说"或者论文正文里的哪一条结论") | ❌ **要么零效果、要么毁掉判据。** 整段中文作单个候选 ⇒ 子串匹配恒不命中（零效果，只让候选集看起来更全）；**若有人为此改成切词，则灾难** —— `method_summary` 里的"模型"/"数据"/"实验"会成为候选，**任何**达标线只要提到"模型"就过 W6，护栏当场归零。⇒ **写死禁止：候选集只收"列表元素"与"字典键"这类天然离散的短名词，永不引入任何形式的分词** |

⚠ **随之如实登记一条局限（`R-S6-A5` 家族，与 §15.4 并列，不得包装）**：`_W6_MESSAGE` 与规划提示词都把「论文正文里的**某条结论**」列为合格写法，**而判据在结构上只能核验"指标名 / 数据集名 / 基线键名"三类离散名词**。⇒ **纯引用论文文字结论、一个专有名词都不带的达标线，仍会被 W6 误报。** 这条**本次不治**（治它只有分词一条路，而分词已被上表 D 否决），**兜底仍是护栏 1 的人眼 + "只产警示、不阻断审批"**。⇒ 这也是「W6 必须不阻断」的第二个独立理由：**判据的表达力天生窄于文案承诺的范围。**

---

🔴 **与 §2.5.4 红线 2 / 红线 3 的关系（v2.6 补，`P-S8-34`；不许删）**：本判据第 2 条**要读 `plan["success_criteria"]`**（已交付，`core/plan_checks.py:636`），因此 `plan_checks.py` 是 §2.5.4 红线 3 那张表里的**第 3 处**。**这不构成对红线 2 的破坏** —— 红线 2 禁的是**判定链路**（`_decide_conclusion`）解析达标线去算档位；W6 读它只为产**一条给人看的、不阻断审批的警示**，**不参与任何档位判定**。⇒ 两条链路物理分处 `core/plan_checks.py` 与 `core/nodes/execution.py` 两个文件，各查各的。**红线 3 原文"只允许三处"曾把本处整个漏掉**，照它写静态断言会把这条正当实现判红。

### 15.4 局限（**必须如实登记，不得包装**）

**它挡的是空话，挡不住"具体但宽松"** ——「knn_accuracy 大于 0 即算成功」引用了具体指标名，照样过（R-S8-17）。

⇒ **真正兜底的是护栏 1（人眼在计划审核页看到并可改）。** 🔴 **不得把 W6 对外宣传成"防止标准画低"的保证**——它只是把最粗暴的那一档挡在门外。这条与 R-S8-01 的对外表述纪律同族。

### 15.5 验证

| # | 验证 | 期望 |
|---|---|---|
| G1 | 正向：成功标准里写了论文分析中的某个指标名 | 不报 W6（🔴 **negative 期望，验红挂在 G7 突变②**，见 `R-S8-43`） |
| G2 | 负向：成功标准 = "只要代码能跑起来就算成功" | 报 W6 |
| G3 | 边界：候选集为空 | 不报（宁窄勿宽）（🔴 **negative 期望，验红挂在 G7 突变②③**） |
| G4 | 边界：成功标准为空串 | 报 |
| G5 ★契约回归 | 两参调用 `check_plan(plan, resource_info)` | 不抛异常、**既有五条 W 的输出与改前逐字节相同**、W6 不出现（🔴 **"W6 不出现"是 negative 期望，验红挂在 G7 突变②③**；"逐字节相同"那半句是等值断言，自带牙） |
| G6 ★产品契约 | UI 上出现 W6 警示时 | **审批按钮仍可用**（不阻断，AC-S8-13③） |
| G7 ★验红（🔴 **v2.8 扩围：由一种突变扩为三种，并逐一点名"哪几条会红"**，`R-S8-43`） | **三种突变，各跑一次全表**：**①** 整块 W6 判定删掉；**②** W6 改成无条件上报；**③** "候选集为空则不报"的早退失效（改成照报） | **①** ⇒ G2 / G4 必红；**②** ⇒ **G1 / G3 / G5 必红**；**③** ⇒ **G3 / G5 必红**。<br>🔴 **v2.8 扩围的理由**：v2.7 只写了突变①，于是**三条 negative 期望的行**（G1 / G3 / G5 的"W6 不出现"）**在文档层面一条验红都没有** —— 与 `BUG-S8-G8` 同族，只是没炸出来（测试侧其实早已实现了这三种突变，是**文档欠了实现**）。<br>⚠ **突变必须"咬得动"**：验红夹具本身要有一条自检，断言突变体与原件**确实分道**（否则拿一个和原件一模一样的"突变体"跑出绿灯 = 验红自己假绿）。<br>⚠ **探针取整张 case 表、不手挑单条输入** —— 突变③只在候选集为空时才与原件分道，手挑必然挑错 |
| **G8** 🔴 **v2.7 新增·链路级·`AR-S8-16` 的唯一承载**（🔴 **v2.8 订正期望值与验红口径，`BUG-S8-G8`，成因见 §15.5.1**） | **走真实 `_digest_paper_analysis`，禁止手搭 payload**：构造 `paper_analysis = {"metrics": [], "datasets": [], "baseline_results": {"BM25_R2": 0.43}}` → 调 `_digest_paper_analysis` → 把返回值当第三参传 `check_plan`，达标线 = 「复现出论文报告的 `BM25_R2` 0.43」 | **三条断言按序全中，缺一不可**：**①结构面** —— 返回的摘要里**有** `baseline_results` 键；**②结构面** —— `_paper_fact_terms(摘要)` 里**有** `BM25_R2`（论文自报基线的名字确实穿过摘要抵达了候选集）；**③行为面** —— `check_plan(...)` **不报 W6**。<br>🔴 **验红只由 ①② 承载**：摘掉 `_digest_paper_analysis` 的 `baseline_results` 键 ⇒ **①② 必红**。<br>🔴 **③ 不承载任何验红** —— 摘键前后它同为"不报"：键没了则候选集空，`check_plan` **在读达标线之前**就走 §15.3 第 3 条的早退，照样不报。<br>⚠ **谁把本条只写成一句 `assert "W6" not in rules`，它就是一条恒绿没牙的用例**（v2.7 原文正是这么写的）。<br>🔴 **输入必须由 `_digest_paper_analysis` 真实产出** —— 手搭 `{"baseline_results": ...}` 会把接缝整个测掉（那正是本缺陷藏了一整批的原因） |
| **G9** 🔴 **v2.7 新增·反向**（🔴 **v2.8 升格：摘键验红在行为面的唯一红灯**） | 同上 digest 产出的摘要 + 空话达标线（"能跑起来就算成功"） | **报 W6**（证明危害 2 已闭：`metrics`/`datasets` 皆空时护栏不再静音）。<br>🔴 **v2.8：本条是「摘掉 `baseline_results` 键」这一失效模式在行为面**唯一**会翻面的验证项**（真身报 → 摘键后不报）⇒ **不得删除、不得弱化成"顺带的反向补充"、不得与 G8 合并成一条能适应两种口径的用例**。<br>⚠ 它与 G8 **必须共用同一份 raw 输入与同一份 digest**：一正一反才夹得住这条链路，拆成两份输入则两条各自都可能恒绿 |
| **G10** 🔴 **v2.7 新增·负向边界·外层面** | `_digest_paper_analysis` 调用方产出的**外层 payload 键集合** | 仍为 **11 键**（`baseline_results` 是 `paper_analysis_summary` 的内层子键）；`tests/test_sprint4_e2e.py` 与 `test_sprint7_s708_payload_probe.py` 两道守门**零改动、零换发**。⚠ **本条只管外层，管不到内层** —— 内层由下面新增的 G11 管，两者互不替代 |
| **G11** 🔴 **v2.8 新增·内层面**（补 §15.3.1 自己点名、v2.7 却没进验证表的那个缺口） | `_digest_paper_analysis` 返回的**内层键集合** | **精确等于 5 键**（`method_summary` / `datasets` / `metrics` / `framework` / `baseline_results`）；且**论文没报基线时该键仍恒常存在、值为 `{}`**（§15.6 路 α 的形态，不是缺席）。<br>⚠ **判据用精确 `==`，禁止写成 `>=` / `issubset`**：少键 = `AR-S8-16` 复发，多键 = 展示 payload 悄悄变胖，**两个方向都该有人看见**。<br>🔴 **立本条的理由**：§15.3.1 复盘时把「外层 11 键守门守的是外层、`paper_analysis_summary` 的内层键无人守」列为本缺陷躲过全部既有绿灯的**第一条**原因，而 v2.7 的验证表**只补了"外层零改动"、没补"内层要有守门"** —— **自己点了缺口却没堵**。<br>⚠ 将来若架构裁定增删内层键，**改本条的同时须按 `P-S8-24` 全文 grep 其它精确键集合断言**（外层那道在 `test_sprint7_s708_payload_probe.py`） |

#### 15.5.1 🔴 v2.8 订正 `BUG-S8-G8`：G8 的期望值与它自带的验红条件互斥（不是笔误，是一类会复发的错）

**缺陷**：v2.7 的 G8 一行里同时写死了两句 —— **期望 =「不报 W6」** ＋ **验红 =「把 `_digest_paper_analysis` 的 `baseline_results` 键去掉 → 本条必红」**。**这两句在本项目的实现下不可能同时成立。**

**两态对照**（测试工程师实测挖出、主控上磁盘复验，2026-08-09；取证见 `docs/sprint8/test-reports/2026-08-09_w6-criteria-guard-02.md` §三）：

| 摘要形态 | `_paper_fact_terms` 候选集 | G8 那条达标线 | G9 那条空话标准 |
|---|---|---|---|
| **真身（5 键）** | `['BM25_R2']` | `[]` → **不报** | `['W6']` → **报** |
| **摘键后（4 键）** | `[]` | `[]` → **不报（与真身同）** | `[]` → **不报（唯一翻面）** |

⇒ 键一没，候选集就空，`check_plan` **在读达标线之前**就走 §15.3 第 3 条的早退（G3「宁窄勿宽」）—— **危害 1（误报）根本没有复现，护栏是整个沉默了（危害 2）。** G8 的行为断言在两态下答案相同 ⇒ **按字面只写一句 `assert "W6" not in rules` 的用例是恒绿的、没有牙。**

🔴 **为什么当初会写成互斥（这一条比订正本身值钱）**：三层，每一层单独看都成立。

1. **把「缺陷方向」直接当成了「验红方向」。** `AR-S8-16` 的危害 1 是「键缺席 ⇒ 误报」，写 G8 的人据此默认「摘掉键就会重现误报」。**但那条因果只在 `metrics`/`datasets` 非空时成立** —— 而 G8 的输入被**刻意**选成两者皆空（为了让候选只能来自第三源、把接缝夹死）。这个刻意的选择恰好把失效模式从危害 1 换成了危害 2。⇒ **取证输入选得越干净，缺陷复现的路径反而越可能改道；不能拿"缺陷是怎么发生的"直接推"摘掉它会怎样"。**
2. **§15.3 第 3 条与 G8 分处两节，各自都对，交叉后互斥。** 早退是一条正确的、挂在 `R-S6-A5` 名下的误报防线；G8 的输入构造也是一条正确的取证设计。**没有人把早退代入 G8 的输入手跑一遍。** ⇒ 与 §14.7 那条「按抛不抛异常来分支恰好会漏掉整个 falsy 组」**同族**：**一个早退分支会让一整片行为消失，而它在红态与绿态下都不显形。**
3. **默认了「最重要的那一条」就是「实际扛验红的那一条」。** v2.7 给 G8 挂上"本裁定的唯一承载"，于是顺手把验红条件也挂在它身上；而真正会翻面的是同一批新增的 **G9** —— **答案当时就在同一张表里，隔着一行。**

🔴 **由此立一条通用判据 `R-S8-43`（与 §14.7 那条并列，落地时同样容易搞砸）**：

> **凡期望值形如「不报 / 不发生 / 无副作用」的验证项，其验红条件不得落在同一条行为断言上。**
>
> 定验证项时必须多问一句：**「该红的那一次，具体由哪一句断言变红？」** 答不上来、或者答案还是那句 negative 断言本身 ⇒ **这条验证是恒绿的**。
>
> 处置只有两条路，**二选一、都要写进表里**：**①给它配结构面断言**（断言上游产物本身 / 中间量本身，如新 G8 的 ①②）；**②把验红条件明确改挂到会翻面的那条正向验证上**（如 G9）。
>
> ⚠ **不许两条都不做、却在表里留一句"本条必红"** —— 那句话会被后来的实现者当成"已经验过了"，于是缺口带着一张绿灯继续活着。

⚠ **与 MEMORY §6「过渡态设计必须当场登记谁在什么时点塌回去」的关系（别把两者当成同一条）**：那条讲的是**双口径用例**（写成 if/else，裁定落地后静默退化成"记录当前行为"）；**本条是它的单口径变体** —— 用例只有一个口径、一个分支都没有，**照样恒绿**，因为它守的那个失效模式**在行为面根本不产生可观察差异**。⇒ 两条的判据是同一条：**问它「该红的那一次会不会红」，而不是看它现在绿不绿。**（本轮实测佐证：塌成单口径 + 补上结构面断言后，同一个摘键动作立刻 **5 failed**。）

**磁盘现状（2026-08-09 核实，`tests/test_sprint8_s811_w6_criteria_guard.py` 136 条）**：🔴 **测试侧已按订正后的口径实现，本次是文档追认实现，不是要求改测试 —— 错的是 v2.7 的文档，不是测试。** 对应落点：

| 本表条目 | 用例 |
|---|---|
| 新 G8（三条断言） | `test_g8_paper_reported_baseline_reaches_check_plan` |
| G9（行为面唯一红灯） | `test_g9_vague_criteria_warns_when_only_baseline_present` |
| 新增 G11（内层 5 键） | `test_production_digest_key_set_is_exact` |
| 上述三条的**常驻验红** | `test_red_state_digest_without_baseline_results`（在内存里摘键，断言上述三处翻面；**不触碰 `planning.py` 源文件**） |

---

### 15.6 🔴 v2.7：与 `T-S8-2-8b`（execution 侧注入）的口径关系 —— **取值统一，形态刻意不同**

同一个 `paper_analysis["baseline_results"]` 现在有两条下游路，**用途不同、消费者不同**：

| | 路 α（本裁定 / 护栏侧） | 路 β（`T-S8-2-8b` / 判定侧，§6） |
|---|---|---|
| 落点 | `planning.py::_digest_paper_analysis` → interrupt payload | `execution.py::_build_execution_agent_context` → HumanMessage |
| 消费者 | **人**（审核页 JSON 展开）+ **确定性纯函数**（W6 取键） | **模型**（执行 agent）+ `_verify_evidence` 的核验基准 |
| 注入形态 | **恒常给键**（空则 `{}`） | **非空才注入**（键缺席） |
| 用途 | 判"达标线有没有点到论文的具体主张" | 判"agent 报的论文值是不是编的" |

🔴 **裁定：取值口径统一，注入形态刻意分开。**

- **必须统一的三件事**（不许分裂）：**同一个 state 字段**（`paper_analysis["baseline_results"]`）、**同一个键名**（`baseline_results`，与 state 同名透传，§6.1 已定）、**同一条"键 = 事实层名词、值 = 论文报的数"的语义**。⇒ 日后若有人改了 `paper_analysis` 的这个字段形状，**两条路一起变、不会各自解释一套**。
- **不许统一的一件事**：注入形态。理由**不是"这次特殊"，而是本项目已有一条成文二分法**（`planning.py:1032-1039` 逐字写过、并明标"刻意不同"）：**给模型看的上下文通道 ⇒ 为空则不写键**（不造哨兵值，`_format_planning_context` / `_build_execution_agent_context` 同款）；**给人看的展示 payload ⇒ 恒常给键、空值兜底**（UI 恒常展示需要一个确定存在的形状）。路 α 属后者、路 β 属前者。
- ⚠ **而且这个差别在两侧都是零功能代价**：W6 眼里"键缺席"与"键存在但为 `{}`"**完全等价**（都产不出候选、都走同一个早退）；路 β 那侧"非空才注入"是 `CP-2.8b-2`「字节零扰动」的硬要求。⇒ **强行统一只会有一侧付出代价、另一侧一无所得。**
- 🔴 **明确不做的事（反过度工程，MEMORY §4.1）**：**不为两条路抽公共 accessor / helper**。两边各是一句 `.get()`，抽出来只会新增一个跨 `core/nodes/` 的依赖点，换不到任何不变量。
- ⚠ **一条必须留给路 β 的边界提醒（防止本裁定被误读成"放开注入面"）**：`dev-plan.md:1431` 与 §16.3.2 写死 **A-S8-07「execution 侧只送 `baseline_results`、不送整个 `paper_analysis`」已从"反过度工程"升格为一条防线** —— 正因为不送论文原文，"agent 报的论文值必须对得上注入"才是**完备**核验。**本裁定只动路 α（planning 的展示 payload），路 β 的注入面一字不动**；**谁都不许拿"护栏侧已经在传论文分析摘要了"当理由，去把整份 `paper_analysis` 塞进 execution 上下文**——那会在毫无察觉的情况下把 `AR-S8-14`（把对照基准编低）的防线掏空。

---

## 16. Q-S8-10（v2.2 新增；**§16.3.2 为 v2.3 重裁**）：结果块的契约、收编与通用渲染

> **产品决策不推翻**：「结果的形状由执行环节决定、报告只提供通用渲染容器」是 Maria 2026-08-05 的拍板（PRD §4.6 / §4.7）。本节只裁"契约长什么样、收编落在哪、渲染怎么通用、上限怎么设、异常怎么标"。
>
> 🔴 **本节全程遵守两条方向相反的红线**：**既不许代码预设形状**（上次翻车的方向），**也不许滑向"什么都不约束"**（另一个极端）。**每条约束都标注了它属于语义层还是形状层**，越界的一律不写进设计。

### 16.1 结论：约束定在语义层，形状层一行代码都没有

| 层 | 谁定 | 落在哪 | 定什么 |
|---|---|---|---|
| **语义层** | 产品（PRD §4.6.2） | **execution 的系统提示词**（稳定前缀，进提示词哈希基线） | ①每块要有给人看的**中文标题**；②每块要说清**数据来自哪个产物、哪一步**；③**若上下文给了论文报告值**，论文值与本次复现值**在同一块内可对照**；④**不得覆盖或合并**不同来源的同名数字 |
| **形状层** | **执行环节 agent** | **只存在于它汇报的数据里** | 分几块、每块叫什么、用表格还是文字、表有**哪几列**、列叫什么、**行怎么排序**、块与块的先后 |

**四条语义约束逐条自查"它是语义还是形状"**（这一栏是本节最该被审的地方——PM 初稿在第③条上越过了自己划的线，已被指出并订正）：

| # | 约束 | 是语义还是形状？ | 判据 |
|---|---|---|---|
| ① | 标题必须是给人看的中文 | **语义** | 它约束的是"这段文字对谁说话"，不约束标题里必须出现什么词、也不规定标题分几级 |
| ② | 说清数据来自哪个产物、哪一步 | **语义** | 它约束的是"必须可溯源"，不规定用什么字段装、也不规定溯源信息放表里还是放说明里 |
| ③ | **若上下文给了论文报告值**，论文值与复现值**同块内可对照** | **语义（订正后）** | 🔴 措辞是"**同一块内可对照**"，**不是"必须有两列"**——agent 可以直接写一句"论文报 0.62，本次 0.61"。**规定列 = 又在预设表头，正是本次要禁的。**（PM v4.0 初稿写的是"必须并排给出复现值与论文值"，那是形状约束，已订正） |
| ④ | 不得覆盖或合并不同来源的同名数字 | **语义** | 它约束的是"不许伪造同一性"，不规定该用什么结构避免——**这条正是被删掉的折叠动作干过的事**（`:1827` 折 + `:1831` 丢），现在写进提示词，禁的是 agent 重蹈系统的覆辙 |

🔴 **第③条必须写成条件句，不许写成无条件的"必须"。** 依据：`baseline_results` 走的是"非空才注入"（§6.1）⇒ **论文分析没抽出报告值时，这条约束在结构上不可能被满足**；无条件措辞会诱导 agent **编一个论文值**出来。措辞固定为「**若上下文给出了论文报告值，则……**」。

🔴 **第③条的接口补丁（AR-S8-10 / AR-S8-14，PRD 未写，本节补；v2.3 重裁）**：

- **前提不变**：论文报告值**没有产物物证**（它来自注入的上下文，不在 `code_output_dir` 下）⇒ **按产物验钞的五重对它不适用**。提示词须说明"**不必也不应为它编一个产物路径**"——漏写这句，验钞第④重会把论文值那一列全判不成立，报告会印出自相矛盾的东西。
- ~~⇒ 它不进证据台账、不参与验钞、不因此被标注异常。~~ 🔴 **v2.3 改判**：**"不按产物验"不等于"不验"** —— 论文值有**另一个**核对物：`state["paper_analysis"]["baseline_results"]`（`core/state.py:80`，由 `paper_analysis.py:224` 的 `_coerce_dict` 落库，schema 见 `:45`，提示词 `:96` 明写"优先来自 Experiments / Results 章节"，且 `planning.py:381` 的透传名单里就有它）。**记录链完整、有出处、有留存，不是黑箱。**
- ⇒ **论文值物证照样进台账，走另一套两重核验**（§16.3.2）。**不验的代价是 AR-S8-14**：达标线常写「数值与论文报告对上」，**不验 ⇒ agent 把论文值报低，自己跑出来的数就"对上了"**。
- ⇒ **提示词第③条同批新增一句**：「**引用论文报告值时，须写明它对应上下文 `baseline_results` 里的哪个指标名（用原键名），并原样使用那里的数值。**」
- ⚠ **这句是语义约束不是形状约束**（自查过）：它约束的是"**论文值也要可溯源**"（与约束②"说清数据来自哪个产物哪一步"同族，只是溯源对象换成论文分析），**不规定块要有几列、也不规定论文值放表里还是放文字里**。声明落在 `evidence` 数组，**不落在表格里**。
- ⚠ **给 PM 的一条建议（架构不改 PRD）**：PRD §4.6.2 语义层约束②的措辞只覆盖了"产物"这一侧，建议扩为覆盖两种出处，或新增一条约束⑤。**约束③的条件句写法不变** —— 且本次裁定**使它从"劝导"变成"可执行"**：没有原料时不但不许编，编了也过不了验钞。

**另有一条用户可见文本的新入口**：块标题 / 说明 / 单元格**直通报告与界面**，而 `humanize` 只作用于内部枚举、自由文本绕过它（MEMORY §4.2）。⇒ 提示词写死"标题用中文、不要用代码变量名或英文缩写"；术语守门只能做**负向抽样断言**（断言"不含已知内部枚举串"），**不得做正向白名单**——正向白名单就是在预设内容。

### 16.2 契约落点：`conclusion["result_blocks"]`，单元格一律字符串

**① 放哪（Q-S8-10①）**

| 方案 | 形态 | 取舍 |
|---|---|---|
| **A（采纳）** | `conclusion["result_blocks"]`（子键） | ✅ **零 TypedDict 改动、零状态契约额度、零新增旧快照防御读点**（`conclusion` 已是 `Dict[str, Any]`，且消费侧已统一 `.get("conclusion") or {}`）；✅ 与 `evidence_ledger` **同一个 dict** ⇒ "引用同一份台账"不跨键，`_render_goal_checks(conclusion)` 与 `_render_result_blocks(conclusion)` 各自单参就够；✅ 与档位同源于**同一次 `<result>`**、同刻由 `_decide_conclusion` 单点写入、随 `exec_result` 一次 commit ⇒ Q-S8-01 的取数单点与幂等纪律③**原样适用，不需要第二套论证** |
| B | `ExecutionResult` 顶层新键 `result_blocks` | ❌ 突破"状态契约新增严格限两处"；且要付三处降级构造点补默认值 + 三处防御读 + 回归面的账；且台账与块跨键 ⇒ 渲染函数要收两个入参，"引用不漂移"从结构保证退化成调用约定 |
| C | 复用 `metrics_groups` 装块 | ❌ 与 §2.2 方案 C 同族的语义污染；且 🔴 **v2.4 起该键本 Sprint 直接删除**（§2.6）⇒ 方案 C **物理上不再存在** |

⚠ **采 A 的代价（如实说）**：`conclusion` 这个名字从"档位判定"扩为"**执行环节收尾汇报经核验后的落盘产物**"。⇒ **`ExecutionResult.conclusion` 的 docstring 与 `_decide_conclusion` 的 docstring 必须同批把这层语义写清楚**，否则下一位会以为块是判定依据。**并写死：`_decide_conclusion` 不得读 `result_blocks`**（AR-S8-04 的同款静态断言对象——否则"块里有几行"会悄悄变成隐性判据）。

**② schema 形态（Q-S8-10③ 的前半，技术可行性已核实）**

```jsonc
"result_blocks": {
  "type": "array",
  "description": "本次执行的结果，按你认为最便于人阅读的方式自行分块与排版。",
  "items": {
    "type": "object",
    "properties": {
      "title":   {"type": "string"},                                   // 中文标题
      "note":    {"type": ["string", "null"]},                         // 可选说明：数据来自哪个产物、哪一步
      "columns": {"type": "array", "items": {"type": "string"}},       // 列名由你定，列数不限
      "rows":    {"type": "array", "items": {"type": "array",
                                             "items": {"type": "string"}}},
      "evidence": {"type": "array", "items": {"type": "object",        // 本块的来源物证
                    "properties": {
                      "path":   {"type": ["string", "null"]},          // 产物物证：本次产出文件相对路径
                      "metric": {"type": ["string", "null"]},          // 🔴 v2.3：论文值物证：baseline_results 的原键名
                      "value":  {"type": ["string", "null"]},
                      "source_note": {"type": ["string", "null"]}}
                    }}                                                 // 🔴 v2.3：items 内不设 required（见下）
    },
    "required": ["title"]
  }
}
```

🔴 **v2.3 订正两处**（v2.2 此处写的是 `"required": ["path"]`）：

1. **`evidence` 的 items 里不再设 `required: ["path"]`** —— 论文值物证**没有** `path`（§16.3.2）。
2. **改为"`path` 与 `metric` 互斥且必居其一"，由收编函数在代码里判，不靠 schema 判**。理由：JSON Schema 表达"二选一"要用 `oneOf`，而 `oneOf` **不在 strict 子集内**（与 §16.2③ 拒绝 union type 同一条理由），且这里的 schema 只是兜底路径、主通道是自由 JSON ⇒ **放代码里判更强也更简单**。两者都有 / 都无 ⇒ 该条不成立 + WARNING（畸形，不静默吞）。

**四条已上磁盘核实的技术依据**：

1. **主通道本来就是自由 JSON**：finalize 解析的是 `<result>` 标签里的整段 JSON（`react_base.py:713-716` → `_extract_result_payload:93-100`）⇒ **任意形状今天就能流过来**；`EXECUTION_OUTPUT_SCHEMA` 只在"预算耗尽强制产出"与"解析失败重生成"两条**兜底路径**生效。
2. **实测为非 strict**：`react_base.py:431` 调 `with_structured_output(normalized, method=method)` **未传 `strict`**；旁证是 `execution.py:1138` 的 `"additionalProperties": True` 在 OpenAI strict 子集里非法，而它今天跑得通。
3. **列名做成数据、不做成 schema 键**：`columns: [str]` + `rows: [[str]]` 的**位置化**表示 ⇒ 任意列数可表达，**且在 strict 子集内也合法**（将来要开 strict 不必重做）。**反例（否决）**：`rows: [{列名: 值}]` 会把列名变成 schema 键，要么写死列名（= 预设表头），要么依赖 `additionalProperties`（strict 下非法）。
4. **顶层 `required` 不含 `result_blocks`**（AR-S8-07）：`_missing_required_fields`（`react_base.py:486-497`）**把空数组也算缺失** ⇒ 列进去会让"跑挂了、零结果"的回合每次白烧一次 schema 重生成调用。⚠ **`items` 内部的 `required: ["title"]` 不受此限** —— 该函数只读顶层 `schema.get("required")`（`:483`），嵌套 required 它根本不看。

**③ 单元格一律字符串（Q-S8-10 与 PRD §13 第 3 条，默认取值「一律字符串」，架构同意）**

三条理由：①union type 不在 strict 子集内；②渲染终点本就是 Markdown 表 / `st.table`；③🔴 **一旦 cell 是数值，下个 Sprint 必然有人写"取 cell 数值做比较"——那就是本次病根的原样复发**（`_lookup_metric_value:174` 排除非数值、`_verify_trend` 拿它比大小，正是这条路走过一遍的证据）。

**代价说实话**：失去"值是数值"的类型保证。**该保证本 Sprint 已无消费者**——`_decide_conclusion` 只读 `level` + 数封顶（AR-S8-04 写死），验钞第③重做的是**前缀字符串匹配**（复裁 8：`0.6201` 匹配 `0.62014732`），本来就不需要数值类型。

### 16.3 证据台账（Q-S8-10②）

> **PRD §4.6.4 定的原则**（唯一台账 / 验钞只跑一次 / 引用不漂移）**全部保留**。本节裁两件它没裁的事：**§16.3.1 id 从哪来**、**🔴 §16.3.2（v2.3 新增）论文报告值拿什么去验**。

#### 16.3.1 id 由**系统**生成，agent 一个 id 都不写

| 方案 | 做法 | 取舍 |
|---|---|---|
| **A（采纳）** | **agent 在每处只写 `{path, value?, source_note?}`；系统在 `_verify_evidence` 里按 `(path, value)` 去重成台账、生成 `E1`/`E2`… 序号 id、逐条验钞一次；`_collect_result_blocks` 与 goal_checks 收编时把各处的 `{path, value}` 回填成 `evidence_ids`** | ✅ **悬空 id 在结构上不可能发生**（R-S8-23 不适用，不是"被缓解"是"不存在"）；✅ **id 冲突不可能发生**（`(path, value)` 相同 ⇒ 就是同一条记录 ⇒ 验钞结果必然相同，**天然无撞名**）；✅ agent 少一项服从要求——在一个实测服从率约 75% 的系统里，**每砍掉一项对 agent 的格式要求都是净收益**；✅ 台账只跑一次验钞、引用不漂移两条原则**满足得比 PRD 原案更强** |
| B（PRD 原案） | agent 自己生成 id，块与逐条引用它 | ❌ 引入一个**agent 自造的命名空间**，随之而来三种失效：悬空 id、两条不同记录同 id、id 拼写错。🔴 **而"自造命名空间 + 撞名怎么办"正是本次要拆掉的那套东西的同构物**（方案 A 靠组名串承载维度、撞名先到先得）——**在同一次回炉里重建一个同型问题，说不过去** |
| C | 保持内联，不建台账 | ❌ PRD §4.6.4 已论证：验钞读盘跑 N 次 + 两处独立数组必然漂移 |

⚠ **采 A 的代价（如实说）**：payload 里同一个路径串会在多处重复出现（块级 + 逐条级），比短 id 费字节。**量化**：块级引用的粒度是"一张表"不是"一行"（PRD §4.6.4 已定），12 块 × 约 2 条来源 × 约 40 字符 ≈ 1 KB，相对 `DEFAULT_LLM_MAX_TOKENS=8192`（`config.py:19`）约 3%。⇒ **可接受**，且换掉了三种失效形态。

> 🔴 **v2.3 追记（上表写于 v2.2，此处补齐，表内原文不改）**：上表里的 `{path, value?, source_note?}` 与去重键 `(path, value)` **说的是产物物证这一种**。v2.3 之后台账还收**论文值物证** `{metric, value, source_note?}`，去重键相应扩为 `(("P", path) 或 ("B", metric), value)`。**方案 A 的三条优点逐条仍然成立**（悬空不可能 / 撞车不可能 / 少一项对 agent 的服从要求），**选型不变**。详见 §16.3.2。

**去重与验钞的确定性口径（写死，否则重放不字节一致）**：

1. **台账键 = `(path 原样串, value 原样串或 None)`**；`source_note` **不进键**（同一条物证不同措辞不该拆成两条）；同键的第一个 `source_note` 胜出（**首见优先**，与 `_flatten_mapping:484` 已有的"重复标签首见优先（确定性）"同款取向）。
2. **id 按台账**首次出现顺序**分配 `E1`、`E2`…**；台账顺序 = agent 汇报里的出现顺序（先逐条结论、后结果块，固定遍历序），**不排序**。
3. 🔴 **`value` 为 `None` 时，验钞第③重（数值前缀匹配）不适用，其余四重照跑。** 这是**定性物证的正路**——"图产出了、文件存在且可读"本来就没有数值可查（AC-S8-12 的构造前提）。**不是漏洞**：无数值的物证支撑不了数值主张，而它能支撑的定性主张正是本 Sprint 要让它支撑的。**这条必须写进代码注释**，否则开发要么让它崩、要么让它无条件通过。

#### 16.3.2 🔴 v2.3 新增：**两种出处，两套验法** —— 论文报告值按 `baseline_results` 核验

> **本小节推翻的是 v2.2 自己写的 AR-S8-10 后半句「论文值不进台账、不参与验钞」。** 推翻理由不是"考虑不周"，是**推理少走了一步**：从"论文值不该按产物文件验"直接跳到了"那就不验了"，中间漏掉「**那就换个东西验**」。剖析见 §14.3.0 第三条自查。

**磁盘事实（四条，均已核实，构成本裁定的全部依据）**：

| # | 事实 | 依据 |
|---|---|---|
| 1 | 论文分析的输出契约里就有 `baseline_results`（object） | `core/nodes/paper_analysis.py:45` |
| 2 | 提示词明写它"**优先来自 Experiments / Results 章节**" | `paper_analysis.py:96` |
| 3 | 经 `_coerce_dict` 落库，进 `PaperAnalysis` | `paper_analysis.py:224` → `core/state.py:80` |
| 4 | 规划节点的透传名单里就有它（不是孤字段） | `core/nodes/planning.py:381` |

⇒ **论文值有出处、有留存、跨节点被消费，是一份完整的记录链，不是凭空注入的黑箱。** ⇒ **它完全可以当核对物。**

**裁定：`evidence_ledger` 的记录按「出处」二选一，各走各的核验**

| 出处 | 记录形态 | 核验 | 依据 |
|---|---|---|---|
| **本次跑出来的** | `{path, value?, source_note?}` | **既有五重**（§3.1）：①存在②可读③数值前缀可查④落在 `code_output_dir` 之下⑤未在计划外命令参数里字面出现 | 不变 |
| 🔴 **论文报告的** | `{metric, value, source_note?}` | **两重（新）**：①`metric` 能在 `state["paper_analysis"]["baseline_results"]` 里查到；②`value` 与该键的值**双向前缀匹配** | 本小节 |

**`path` 与 `metric` 互斥且必居其一**；两者都有 / 都无 ⇒ 该条不成立 + WARNING（畸形，不静默吞）。

**三个 Maria 点名交给架构裁的细节**：

1. **键名匹配的宽严 ⇒ 精确匹配（仅大小写与首尾空白不敏感），此外一字不差。**
   - 🔴 **绝不做归一化模糊匹配**：`_normalize_group_key`（`reporting.py:130-133`，`re.sub(r"[^a-z0-9]+", "_", …)`）+ `_match_metrics_group` 的那套**正是 S7-13 真跑挖出的歧义源**，本 Sprint §5.1 正在删它们 —— **不能在隔壁重建一个同型物**。
   - **不做模糊匹配的成本被 S8-10 抵消了**：`baseline_results` 的**原键名已经在 agent 眼前**（§6.1 整份 dict 注入、`json.dumps(sort_keys=True)`）⇒ 抄准是零成本。**提示词写死"用原键名"。**
   - **多个候选键归一后同时命中 ⇒ 判歧义、该条不成立 + WARNING，不做任何 tie-break**（沿 `_match_metrics_group` 当年"命中 2 条判歧义返 None"的保守取向 —— **那条取向本身是对的，被删的是它的模糊匹配前提，不是它的保守出口**）。
2. **对不上时：标注，不封顶 —— 🔴 不新增第四条封顶。**
   - 该条 `ok=false` ⇒ 自动落进**两个既有出口**：①**引用它的逐条结论落「无法核实」**（PRD §4.8 第 3 条"物证不过验 ⇒ 该条落无法核实"，保守出口，已写死）；②**档位的支撑物证若全不成立 → 既有封顶 3「仅代码跑通」**（§4.5.3 第三条 / A-S8-08）。
   - ⇒ **AR-S8-14 那条路被堵在"逐条结论"这一层**：把论文值编低 ⇒ 论文值物证不成立 ⇒ 「数值与论文报告对上」这条预期判不成「印证上了」⇒ 拿不到它想要的那一档。**零新机制。**
   - 🔴 **明确不自造新规则**（取向与 §2.5.6 逐字同源）：**开发不得另写一条"论文值对不上则降档"的分支** —— 既有两个出口已完全覆盖，写第二处必然与第一处打架。
3. **空值时的行为：`baseline_results` 为空 ⇒ agent 报的任何论文值物证一律不成立。**
   - 🔴 **这条零误伤，理由是结构性的**：`_build_execution_agent_context` **只注入 `baseline_results`，不注入整个论文分析、更没有论文原文**（A-S8-07 / §6.1"只送 `baseline_results`"）⇒ **agent 手上唯一的合法论文值来源就是那份注入**。注入为空时它**没有任何合法途径**知道论文报了什么 ⇒ 此时它报出来的数**只可能是编的**。
   - ✨ **附带收获：A-S8-07（只送 `baseline_results`）从"反过度工程"升级为一条防线** —— 正因为不送论文原文，"报的值必须对得上注入"才是一个**完备**核验。**这条关系须写进代码注释**，否则日后有人为了"让 agent 看得更全"把整份 `paper_analysis` 塞进去，会**在毫无察觉的情况下把这条核验掏空**。
   - **`baseline_results` 非空但不含该键** ⇒ 同样不成立，reason 用「论文分析里没有这个指标的报告值」。**文案必须中性**：这不是造假指控，是"无从核对"（同 §5.6 审计文案的中性要求）。
   - 与 PRD §4.6.2 约束③的条件句写法**严丝合缝**：**有原料才做，没原料不许编** —— 本裁定使这句话从劝导变成可执行。
4. **数值匹配口径：双向前缀匹配**（`"0.62"` 与 `0.6201` 互相成立），与复裁 8 的前缀口径同族。**严格相等不可取** —— 浮点字符串化（`0.62` vs `0.6200000000000001`）会造成大面积误伤。

⚠ **局限如实登记（不得包装成"杜绝编造"）**：**它挡的是"把 0.95 编成 0.61"这一档量级级别的改动，挡不住"把 0.62 报成 0.6"** —— 后者仍能通过前缀匹配。⇒ **正确表述**：「agent 引用的论文值必须与论文分析抽出来的对得上，**但前缀口径下小数位级别的放宽仍可能通过**。」登记体例同 §15.4（W6 挡空话挡不住"具体但宽松"）、口径纪律同 R-S8-01。

⚠ **另一条真实残留**：**论文分析自己抽错了值**，则 agent 抄它抄得再准也是错的 —— **本条核验保证的是"agent 没有二次编造"，不保证"论文值本身是对的"**。⇒ 追溯责任落在论文分析节点，不在本环节；**不得对外说成"论文值已核实"**，正确说法是"**agent 引用的论文值与论文分析记录一致**"。

⚠ **它不是"按证据形态分支"，不违反 AC-S8-08②（必须写清，否则测试会误伤）**：
- AC-S8-08② 禁的是**按证据的内容形态**（数值 / 趋势 / 定性）分支 —— 禁它是为了守病③（照着某类论文设计，某类论文结构性拿不到高档）。
- 本条分的是**出处**（这个数字自称来自产物文件，还是自称来自论文），**出处决定的是"拿什么去核对它"，不是"这篇论文属于哪一类"**；两种出处对**所有**论文同时存在，**不会让任何一类论文结构性拿不到高档** ⇒ **不复发病③**。
- 且它**只落在 `_verify_evidence` 里**：🔴 **AR-S8-04 的红线一字不动 —— `_decide_conclusion` 仍然只读 `level` + 数封顶，不读证据形态、不读出处、不解析证据语义。**

**去重键随之扩一位**：`(("P", path) 或 ("B", metric), value)` —— 两个命名空间分开，防止某个路径串恰好等于某个指标名。**其余确定性口径（首见优先 / 按出现顺序分配 id / 不排序）一字不变。**

### 16.4 疑似截断的检测（Q-S8-10⑥）：🔴 用确定性信号，不用长度启发式

**先订正一处 PRD 的机理描述（已上磁盘验实，PRD §4.6.5 #1 / R-S8-18 说的路径不对）**：

- PRD 说：`max_tokens` 截断后 `_repair_truncated_json_prefix`（`react_base.py:268`）会把它修成合法 JSON 且不留痕。
- **磁盘事实**：`_RESULT_TAG_PATTERN`（`react_base.py:87-89`）是 `re.escape(OPEN) + "(.*?)" + re.escape(CLOSE)`，**必须同时匹配开、闭两个标签**。⇒ 输出被 `max_tokens` 从中间切断时，`</result>` **根本没来得及吐出来** ⇒ `_extract_result_payload` 返回 `None`（`:97-99`）⇒ **`_repair_truncated_json_prefix` 这条路压根走不到**，走的是 `:721-724` 的 WARNING「`<result>` tag not found」→ `:727-752` 的 **schema 重生成**。
- ⇒ **真实风险不是"静默修补成一个更短的 JSON"，而是"让模型重说一遍"**（AR-S8-13）：重说的那份块可能更少、更简，且 `result_blocks` 不进 `required` ⇒ 它可以整个不给。
- ⇒ **"静默"这个词也要订正**：日志层**有痕**（一条 WARNING + 一条 INFO `finalize: schema-enforce regen`，`:733-738`）；**无痕的是报告** —— 用户侧确实看不出来。**风险成立，机理不同，检测手段因此可以做得更硬。**

**裁定（检测口径，纯确定性、零阈值、零启发式）**：

- **落点**：execution 侧 `_resolve_agent_report`（§1.3，它本来就在逆序扫 messages 找 `<result>`），**顺带**判一个布尔：**末条 AIMessage 文本里 `REACT_RESULT_TAG_OPEN` 出现过、而 `REACT_RESULT_TAG_CLOSE` 没出现过** ⇒ 疑似输出被截断。
- **零新依赖**：两个常量就是 `config.REACT_RESULT_TAG_OPEN` / `REACT_RESULT_TAG_CLOSE`（`config.py:61-62`），§1.3 已裁"execution 侧按同一对常量自建 pattern，不 import 私有符号"，**本项复用同一对常量，不新建任何东西**。
- **产物**：往 `conclusion["report_caveats"]` 追加一条中文句子（execution 侧模块常量），渲染层原样印在结果节**之前**。
- 🔴 **`react_base.py` 一字不动**（四个节点共用的基础设施，PRD §4.6.5 #1 已定此边界）。
- **同一条 caveat 通道另收两个来源**：`budget_truncated=True`（R-S8-19，`force_finish_node` 强制产出、agent 根本没跑完）、以及**块结构不合法**（下节）。**三个来源、一条通道、三句不同的中文**。

⚠ **为什么不用 PRD 建议的"自比 `<result>` 原文长度与解析后长度"**：那要选一个阈值（差多少算截断？），而 JSON 反序列化本来就会因空白与转义产生长度差 ⇒ **必然要调参、必然有假阳/假阴**。上面那条是**布尔事实**，没有可调的东西。

### 16.5 通用渲染与展示上限（Q-S8-10③④）：🔴 收编在 execution 侧，渲染层只转义与拼装

**① 落点裁定（本节最容易被写反的一条）**

```
execution 侧  _collect_result_blocks(raw_blocks, ledger_index) -> List[Dict]
    ① 脱敏：每个 title / note / cell **先按 isinstance 确定性归一成 str，再**过 mask_value
            （core/secrets_store.py:261）。🔴 归一必须在前，理由见下方「①的展开」——
            mask_value 对非 str 有三种行为，其中两种**静默漏过脱敏**
    ② 长度：每个 title / note / cell 截断到 _BLOCK_CELL_MAX_LEN，超长**截断不丢弃**并留标注
    ③ 对齐：len(row) != len(columns) → 短的补占位、长的截断，块级 caveats 记"N 行已按表头对齐"
    ④ 上限：块数 / 列数 / 行数各自截断，**每次截断都往 caveats 写一句中文并指向产物路径**
    ⑤ 引用：把块自带的 {path, value} 回填成 evidence_ids（§16.3）
    ⑥ 不合法：非 dict / 无 title / columns 与 rows 都缺 → 该块降级为"原样文本块"（先截断再放），
              块级 caveats 记"未按可渲染结构汇报"，**绝不兜底回旧的二维表**

reporting 侧  _render_result_blocks(conclusion) -> List[str]
    只做三件事：Markdown 行内转义（_md_escape_inline，reporting.py:406-412）、拼装、印 caveats。
    **零判断、零裁剪、零排序、零默认值。**
```

**🔴 ①的展开（v2.6 新增，`P-S8-21`）：`mask_value` 对非 str 有三种行为，而不是两种 —— 其中两种静默漏过脱敏**

`P-S8-12` 曾把它登记为两种（无凭证静默原样返回 / 有凭证抛 `AttributeError`）。**磁盘实测是三种**（`HEAD=1a28e93`，开发代理注册凭证前后各十例实跑，主控已独立复核源码行号）：

| # | 前置 | 输入示例 | 实际行为 | 源码 |
|---|---|---|---|---|
| ① | **无凭证注册** | 任何非 str | **静默原样返回，脱敏被完全跳过**（`out is val` 为真、零日志） | `core/secrets_store.py:280-281` `if not known: return text` |
| ② | 有凭证 + **falsy** 非 str | `0` / `0.0` / `False` / `[]` / `{}` | 🔴 **被第一道早退拦下 ⇒ 不抛异常、静默漏过脱敏** | `:268-269` `if not text: return text` |
| ③ | 有凭证 + **truthy** 非 str | `42` / `3.14` / `True` / `["a"]` / `{"k":"v"}` | 抛 `AttributeError: 'X' object has no attribute 'replace'` | `:283-284` `masked.replace(...)` |

🔴 **`0` 和 `False` 恰是指标场景里最自然的取值**（一个指标值就是 0、一个布尔标志就是 False）⇒ ② 不是边角情形，是主路径。

**⇒ 三条写死的处置口径（`T-S8-2-10b` 的实现纪律，落地时最容易写反）**：

1. 🔴 **`_collect_result_blocks` 必须按 `isinstance` 判定类型，绝不得依赖"`mask_value` 抛不抛异常"来分支。** 若写成「`try: mask_value(x) except AttributeError: 转字符串`」，**①②两组（含全部 falsy 非 str）整条绕过脱敏与畸形 WARNING** —— 而它在测试里**红态绿态都看不出来**。
2. **归一在前、脱敏在后**：标量（int / float / bool）确定性转字符串；容器（list / dict / 其他）**置占位符、不做 `str()` 强转**（`str({})` = `"{}"` 是 truthy，会绕过兜底句原样印给用户 —— `ui/pages/plan_review.py::_local_fit_note_text` 已有同款现成陷阱，见 `P-S8-29`）。畸形一律 WARNING（`DA-S8-1` 换发后的落点）。
3. 🔴 **`T-S8-2-10b` / `CP-2.10b-7` 的验红夹具必须同时含 truthy 与 falsy 两组非 str 取值**（至少 `42` 与 `0`、`{"k":"v"}` 与 `{}`）。**只取 truthy 的验红是假绿** —— 人写用例的自然习惯恰恰是取 truthy，这条不写死就会漏。

⚠ **顺带登记一处上游文档失真（本轮只登记，`core/` 零改动）**：`mask_value` 的 docstring（`core/secrets_store.py:266`）只写「**text 为 None / 空串返回原值**」，**与实际 `if not text` 的覆盖面不符** —— 它还吃掉 `0` / `0.0` / `False` / `[]` / `{}`。⇒ 该 docstring 应订正为「**falsy 值（None / 空串 / 0 / False / 空容器）一律返回原值**」，落点交主控排期。**这一处正是 `P-S8-12` 只登记到两种行为的来源**：读 docstring 会以为非 str 一定走到 `.replace` 那行。

**四条理由（为什么收编不放在渲染侧——PRD §4.7 第 6 条的字面写法是"落 reporting.py"）**：

1. 🔴 **脱敏纪律要求 `mask_value` 在落 state 之前做** —— 既有两处先例逐字如此（`execution.py:1753` / `:1777`）。⇒ **execution 侧本来就必须有一个收编函数**；把其余归一化也放进去是**零新增抽象**，放到渲染侧则变成"两处都要碰"。
2. 🔴 **它让"给定同一份状态重放 → 报告字节一致"在结构上成立**（AC-S8-19③ 换发后的确定性口径）。反之若在渲染侧收编，`mask_value` 依赖 `secrets_store` 的运行时状态 ⇒ 同一份 state 在不同时刻可能渲染出不同的报告。**这一条是决定性的。**
3. **checkpoint 体积可控**：state 里存的就是已截断的结果，不是 agent 吐的原始巨物（AR-S8-11）。
4. **"标注了什么"集中在一处**：截断、对齐、降级三类标注同源同格式，不会一半在 execution、一半在 reporting。

⚠ **这是对 PRD §4.7 第 6 条一句实现措辞的精确化**（属"怎么实现"层，架构可裁），**PRD 的规范内容原样保留**：上限是**模块级常量、不做成 `config.py` 可配置项**（非目标 10）。**已如实登记，不静默通过**（体例同 §15.2）。

**代价说实话**：上限日后调值**不影响旧快照重放**（旧快照按当时的上限已裁剪好）。**架构认为这反而是对的**——报告应当忠实于当时落盘的状态。

**② 四个常量（Q-S8-10④）**

| 常量 | 取值 | 依据 |
|---|---|---|
| `_BLOCK_MAX` | **12** | 真跑现场最细切法 4 方法 × 3 数据集 正好 12（PRD §4.7 第 6 条） |
| `_BLOCK_COL_MAX` | **12** | 同上量级；超过 12 列的表在 Markdown 与 `st.table` 里都已不可读 |
| `_BLOCK_ROW_MAX` | **50** | 真跑实测 24 条的 2 倍余量 |
| `_BLOCK_CELL_MAX_LEN` | **120** | 🔴 **它是 `_GROUP_METRIC_STR_MAX_LEN`（`execution.py:1706`）的改名继任者**：后者的两个消费者（`:1752` / `:1774`）随 §16.6 一并删除，取值 120 原样搬到新名下。**不是新造第二个常量，是同一个常量换了个准确的名字。** ⚠ **也不是"从 reporting 侧 import execution 私有符号"**——那条路已由 §1.3 / §3.2 方案 B 明令否决（跨模块 import 私有符号造成隐性耦合）。**PRD §4.7 第 6 条那句"逐字复用 `execution.py:1706` 的常量"指的是复用取值，不是复用符号**，此处一并说清 |

**③ 渲染纪律（可静态断言，AC-S8-19② 的对象）**

- **块按 `result_blocks` 的数组顺序渲染**；`_render_result_blocks` 函数体内**不得出现 `sorted()`**（今天 `reporting.py:997` 那个 `sorted()` 就是代码在替 agent 决定组的先后）。
- 函数体内**不得出现任何写死的表头字符串 / 结果分节标题**（今天 `:938` / `:986` / `:993` / `:995` 四处）。
- **入参只有 `conclusion`**，不得取 `state`、不得取 `exec_result`（取了就会有人从 `paper_analysis.baseline_results` 再拼一列论文值，预设表头当场复发）。
- 🔴 **导语必须写明核验边界（AR-S8-09）**：「下表由执行环节汇报，系统核验的是它标注的来源文件与逐条结论的物证，**未逐格核对表内每一个数字**。」**不得省略、不得软化。**
- **空块早退返 `[]`**，不印空标题（§5.8：degraded 路径上块为空是常态）。

**④ 确定性口径的换发（不许把"不排序"读成"确定性不重要"）**

- **仍然成立**：「**给定同一份状态重放 → 报告字节一致**」。顺序**已经落盘在状态里**，渲染是纯函数。
- **不再成立**：「两次真跑的结果可以直接 diff」。⇒ **测试口径必须按前者写**（AC-S8-19③）。

### 16.6 四个折叠 / 扫盘函数的去留（Q-S8-10⑤）

| 函数 / 常量 | 裁定 | 理由 |
|---|---|---|
| `_split_reported_metrics`（`execution.py:1781-1856`，调用点 `:2938`） | 🔴 **整体删除** | **折叠动作就是病根**：`:1827` `collected.setdefault(group, {})` 把平坦记录数组折成二维、`:1831` `continue  # 先到先得` 在撞名时丢弃。**不折叠 ⇒ 不撞名 ⇒ 没有"撞名怎么办"** ⇒ AR-S8-08 与 PRD 原方案 A 的"撞名两条都丢弃"**一并作废** |
| `_coerce_reported_value`（`:1764-1778`） | **随之删除** | 唯一调用点 `:1821` |
| `_collect_grouped_metrics`（`:1709-1756`，调用点 `:2961`） | 🔴 **整体删除**（**推翻本文档 v2.1 §13 的"不删、不改"**） | 三条硬编码前提（目录 `outputs/`：`:1730`；文件名 `summary.json`：`:1733`；只收顶层标量：`:1749-1754`）在本 Sprint 之后**不再由任何契约保证**（新约定是"落在计划声明的位置、结构自定"）；且它自带的三样东西（组名=目录路径 / 二维结构 / 只收顶层标量）**正是本次回炉要拆的**。留作兜底 = **在 agent 不服从时把旧格子自动请回来**，与 PRD §4.6.5 #4 直接冲突。详见 §13 |
| `_GROUP_METRIC_STR_MAX_LEN`（`:1706`） | **随之删除**，取值 120 由 `_BLOCK_CELL_MAX_LEN` 继承 | 两个消费者都没了 |
| `ExecAgentOutput.reported_metrics`（`:1205` 注释点名） | **随之删除** | 零消费者；`report` 字段是唯一取数口径（§1.3） |
| `ExecutionResult.metrics` / `metrics_groups`（`core/state.py:175` / `:183`） | ~~**键保留声明、停产停消费**，注释改写为"仅供旧快照反序列化"~~ 🔴 **v2.4 改判：两键删除** | ~~PRD §13 第 1 条**默认取值**。⚠ **此项归 Maria**；若改判为"删键"，则连带 `dev-plan.md:1358` CP-2.10-3「类型签名逐字未变」失效、`grep metrics_groups tests/` 上百处回归面 —— **架构建议维持默认**（改动面不成比例）~~ 🔴 **Maria 2026-08-06 拍板删键**（「旧字段要是确认没有用了就删掉」）。**架构那句"上百处回归面"的账已上磁盘复核、是错的** —— 真实净增量 4 处断言 / 1 个文件；CP-2.10-3 的处置见 §2.6.4。全账见 **§2.6** |

🔴 **备选 B（仅在 Maria 否掉 PRD §13 第 4 条、要求保留扫盘兜底时启用，架构不推荐）**：保留 `_collect_grouped_metrics`，在 `result_blocks` 为空时把扫到的每个 `summary.json` 转成**一个块**（`title` = 文件相对路径、`columns` = `["字段", "值"]`、`rows` = 顶层标量）。
**不推荐的理由**：那个 `["字段", "值"]` 就是**代码替 agent 决定的形状**，虽然是退化形状，性质与被禁的预设表头相同；而它换来的能力，在新的产出约定下**没有契约保证会命中**。**如实登记两难，不替 Maria 拍。**

### 16.7 验证（逐条可落成测试；与 AC-S8-19 / AC-S8-20 对齐，**v2.3 另牵动 AC-S8-06 / AC-S8-08 / AC-S8-15**）

> 🔴 **v2.3 的 AC 连带影响（交主控转 PM / 测试，架构不改 PRD）**：
> - **AC-S8-06「五重验钞逐重成立 + 逐一放宽每一重必红」**：射程须扩为「**产物物证五重 + 论文值物证两重**，**共七重逐重成立、逐一放宽各自必红**」（B18 / B19 / B20 即新增的三条）。**不扩，AR-S8-14 那条路就没有验收对象。**
> - **AC-S8-08②「代码里不存在按证据形态分支的逻辑」**：须**加一句边界澄清** —— 禁的是**按证据内容形态**（数值/趋势/定性）分支，**按出处**（产物 / 论文）二选一走不同核验**不在禁列**（理由见 §16.3.2 末）。**不澄清，测试会把 §16.3.2 的正当实现判红。**
> - **AC-S8-15「论文报告值确已送达执行环节」**：现有三条断言不变，建议**补一条** —— 论文值不只是"送到了"，而是"**送到的那份成了核对物**"（即 B18 的行为断言）。

| # | 验证 | 构造 | 期望 | 属性 |
|---|---|---|---|---|
| B1 ★命门 | **零丢弃** | 真跑夹具 24 条（4 方法 × 3 数据集 × 2 指标）以块形态汇报 | **24 条全部呈现、零丢弃**；报告里不存在"撞名"概念 | 直接证否坍缩 |
| B2 ★验红 | **代码不预设形状** | 静态审查 `reporting.py` / `result_report.py` | **不存在写死的结果表头字符串、写死的结果分节标题、对块或块内行列的 `sorted()`** | **验红：加回任一 → 必红** |
| B3 ★验红 | **入参边界** | 静态审查 `_render_result_blocks` | 入参只有 `conclusion`；函数体不出现 `state` / `paper_analysis` / `baseline_results` | **验红：改成取 state → 必红** |
| B4 | **列数不符** | `len(row) != len(columns)`（短、长各一） | 短的补占位、长的截断，**块级 caveats 有中文标注**，无静默 | §16.5① |
| B5 | **超上限** | 13 块 / 13 列 / 51 行 / 121 字符 各一 | 各自截断 + **各自有显式中文标注并指向产物路径**，不是省略号、不是脚注 | §16.5② |
| B6 | **结构不合法** | 块非 dict / 无 title / columns 与 rows 都缺 | 降级为**先截断后原样打印**的文本块 + 标注"未按可渲染结构汇报" + 指向完整日志；🔴 **绝不出现旧的三列表头** | R-S8-08 |
| B7 ★验红 | **转义与脱敏** | cell 含 `\|`、含换行、含敏感串各一 | 全部过 `_md_escape_inline` + `mask_value`，表不破形、敏感串已掩码 | **验红：去掉任一处理 → 对应用例必红** |
| B22 ★验红·命门 🔴 **v2.6 新增**（`P-S8-21`） | **非 str 单元格三组齐验** | 已注册凭证 + cell 取三组：**truthy 非 str**（`42` / `{"k":"v"}`）、🔴 **falsy 非 str**（`0` / `False` / `{}` / `[]`）、str 对照组 | 三组**全部**先归一再脱敏：不抛异常、敏感串已掩码、容器组走占位符**不是** `"{}"`、畸形有 WARNING | **验红：①把处置改成"捕获 `AttributeError` 再转字符串" → falsy 组必红；②夹具只留 truthy 组 → 该用例退化为假绿（此项须在 CP 里写死禁止）**。⚠ **只验 truthy 的用例即便全绿也不构成通过** |
| B8 ★命门 | **截断检测** | 末条 AIMessage 有 `<result>` 无 `</result>` | `report_caveats` 有"本次汇报可能不完整"一句；报告显著印出 | §16.4 |
| B9 | **预算耗尽标注** | `budget_truncated=True` | 块渲染带醒目前置标注 | R-S8-19 |
| B10 ★命门 | **台账不漂移** | 同一条 `(path, value)` 同时被 1 条 goal_check 与 2 个块引用 | 台账里**只有一条**记录、**验钞只跑一次**（可用调用计数断言）、三处 `evidence_ids` 指向同一个 id | §16.3 |
| B11 | **无数值物证** | `value` 为 `None` 的图产物物证 | 第③重跳过、其余四重照跑；可支撑「印证上了」 | §16.3 第 3 条 |
| B12 🔴 **v2.3 重写** | **论文值走另一套验法（不是不验）** | 块引用 `{metric: "knn_accuracy", value: "0.62"}`，而 `baseline_results = {"knn_accuracy": 0.6201}` | 该条**在台账里**、**不被判"路径越出代码目录"**（五重不适用）、**两重通过 `ok=true`**、报告不标红 | AR-S8-10 |
| B18 ★命门·须验红 🔴 **v2.3 新增** | **把对照基准编低 → 判不成功** | 同一份产物 + 达标线「数值与论文报告对上」+ `baseline_results = {"knn_accuracy": 0.95}`，agent 报 `{metric: "knn_accuracy", value: "0.61"}` 并自称「印证上了」 | 该条证据 `ok=false`（值对不上）⇒ 逐条结论落「**无法核实**」⇒ **拿不到「复现成功」**；报告中性标注"引用的论文值与论文分析记录不一致" | **验红：让论文值物证无条件通过 → 本用例必红。** AR-S8-14 |
| B19 🔴 **v2.3 新增** | **无原料时不许编** | `baseline_results` 为空 / 不含该键，agent 仍报论文值物证 | 该条 `ok=false`，reason 为**中性**措辞（"论文分析里没有这个指标的报告值"，**不得暗示造假**）；⚠ 另一向：`baseline_results` 为空且 agent **没报**论文值 ⇒ **零告警、零标注**（条件句语义，没原料不做不算错） | §16.3.2 第 3 条 |
| B20 🔴 **v2.3 新增** | **键名精确匹配、歧义不猜** | ①键名大小写/首尾空白不同 → 命中；②归一后多个候选键同时命中 → **判歧义、不成立 + WARNING、不做 tie-break**；③键名少一个字符 → 不命中 | 三向如期；🔴 **静态审查：`_verify_evidence` 内不得出现 `re.sub(r"[^a-z0-9]+"…)` 那类归一化模糊匹配** | §16.3.2 第 1 条（防在隔壁重建 `_normalize_group_key`） |
| B13 ★验红 | **确定性口径换发** | 同一份 state 重放两次 | **报告字节一致**；🔴 **不得**断言"两次真跑结果可 diff" | AC-S8-19③ |
| B14 | **degraded 形态** | `level="仅代码跑通"`（`success=False`）且块非空 | **degraded 报告里能看到结果块** | §5.8 |
| B15 | **空块早退** | 块为空 | 结果节整节不渲染，**不印空标题**；界面结果页文案为"本次执行未汇报可展示的结果块"，**不回退 `metrics` / `metrics_groups`** | §5.8 / §12 |
| B16 🔴 **v2.4 扩围** | **旧快照重放（含已删键）** | 旧 checkpoint 里**有 `metrics` / `metrics_groups`（本次已删的两键）、无 `conclusion`** | 不崩（TypedDict 零校验）；**结果节不渲染**；🔴 **不再出现指标对比表**（AR-S8-12，预期行为变更，须换发既有快照断言）；🔴 **v2.4 加一向静态断言：交付后 `core/` 与 `ui/` 对这两个键的读取点为零**（不许"旧快照就读旧键渲染旧表"） | AR-S8-12 + §2.4 末条 |
| B21 🔴 **v2.4 新增** | **状态契约键数账对平** | `ExecutionResult` 的键集合 | **恰为 10 键**（今天 11 键 − `metrics` − `metrics_groups` + `conclusion`）；🔴 **四处精确键集合断言同批换发，且仍用 `==` 精确语义**（清单见 §2.6.2 丙类）——**禁止放宽成 `>=` / "包含"来规避** | §2.6.3 + AC-S8-21 红线 |
| B17 ★验红 | **块不参与判定** | 同一份物证 + 两份块数完全不同的汇报 | **`conclusion.level` 逐字相同** | AR-S8-04 同款 |

---

## 17. AR-S8-15（v2.5 新增）：两键的声明必须与其构造点原子同批 —— `T-S8-1a-2` 注销并迁出

> **本节是一次开工期裁定的落章。** 裁定作出于 2026-08-07（开发代理执行批次 1a 时上磁盘实测触发，未自行拍板），**当时只写进了 `docs/sprint8/dev-plan.md`，本文档零改动** —— 那与 sp7 的 `Q-S7-25~31`「只活在 dev-plan、architecture.md grep 零命中」是**完全同型的病**，而那条 2026-08-06 才刚补完落章（`caacafc`）。**本节即补落章。**

### 17.0 🔴 编号撞车的登记与换发结果（体例沿 §14.2）

**事实**：本文档 v2.2（先）已把 **`AR-S8-13`** 分配给「**schema 重生成路径产出的块内容可能与真实产物不符**」这条架构侧风险（定义在 §10 表，另在 §14.3.1 第 7 条与 §16.4 被引）。而 2026-08-07 那次「两键迁出」的开工期裁定**也被编成了 `AR-S8-13`**，并已在 `dev-plan.md` 八处以上被引用 ⇒ **同一个编号指两件完全不同的事**。

| 编号 | 本文档 v2.2 已占用 | 2026-08-07 开工期裁定 | **换发结果** |
|---|---|---|---|
| `AR-S8-13` | schema 重生成路径的块内容可能与真实产物不符（§10 表） | ~~两键声明与构造点原子同批~~ | **本文档保持 §10 的 `AR-S8-13` 不动（先占先得，与 §14.2 处置 `Q-S8-07` 同一条规则）** |
| `AR-S8-14` | 把对照基准往低了编（v2.3 新增，§10 表） | — | 不变 |
| **`AR-S8-15`** | — | — | 🔴 **新号 = 本节。** 换发前已 `grep -rn "AR-S8-1[5-9]\|AR-S8-2[0-9]"` 全仓核实：**零命中**，`AR-S8-15` 及其后未被任何文档占用 |

**给开发 / 测试的读法（务必按此对照）**：

- **一律以本文档编号为准。**
- 读 `dev-plan.md` 时，凡指向「两键迁出 / `T-S8-1a-2` 注销 / TypedDict 原子同批」的 **`AR-S8-13`，一律读作 `AR-S8-15`**；`dev-plan.md` 的引用点换发由主控收口（清单见 §17.8）。
- 凡指向「schema 重生成后块内容变样」的 `AR-S8-13`，**就是本文档 §10 那一条，不必换发**。
- ⚠ **`dev-plan.md:616` 引的「AR-S8-13 §6 第 2 条」是一个悬空引用** —— 裁定原文从未有过 §6。其所指内容即**本节 §17.6 第 2 条**，换发时一并改。

> 🔴 **这次撞车的机制性成因（与 §14.2 那次不同，值得单记）**：§14.2 那次是**两份文档的两位作者互不知情**（架构 v1.0 与 PRD v3.0 各自占了 `Q-S8-07`）。**这次是同一个作者、同一份文档，隔了两版把自己的号发了两遍** —— 病根是**裁定当场没回本文档登记，编号只在脑子里 / 在 dev-plan 里**。⇒ **编号是本文档的资源，发号必须当场落回本文档**；只在别处引用一个新号，等于没发。**这条与 §17.7 的元教训是同一件事的两面：裁定不落章，连它自己的编号都守不住。**

### 17.1 触发事实（开发代理实测，2026-08-06/07，HEAD=`0e250fb`；主控已独立复现）

按 `T-S8-1a-2` 原文给两个 TypedDict 各加一个**普通必填键**后，`rm -rf .mypy_cache && .venv/bin/mypy` **出 4 个错误**：

```
core/nodes/planning.py:467:12: error: Missing key "success_criteria" for TypedDict "ReproductionPlan"  [typeddict-item]
core/nodes/planning.py:676:12: error: Missing key "success_criteria" for TypedDict "ReproductionPlan"  [typeddict-item]
core/nodes/execution.py:2451:12: error: Missing key "conclusion" for TypedDict "ExecutionResult"  [typeddict-item]
core/nodes/execution.py:2908:23: error: Missing key "conclusion" for TypedDict "ExecutionResult"  [typeddict-item]
Found 4 errors in 2 files (checked 27 source files)
```

**根因**：`TypedDict` 默认 `total=True` ⇒ **加一个必填键，会让该 TypedDict 的所有既有构造点当场 `[typeddict-item]` 红**。而这四个构造点的「补默认值」动作，两份文档**都排在后续批次**（`planning.py` 两处 → `T-S8-1b-2`；`execution.py:2451` 在**冻结表函数** `_build_execution_result` 内、`:2908` 是降级构造点 → `T-S8-2-8`）。

⇒ **`T-S8-1a-2` 与 `CP-1a.5-6`（mypy 清缓存后零错误）在设计上互斥**：批次 1a 只要落这个任务，收口门就必红。

⚠ **另注（本文档此前未记）**：`reporting.py:581` 的同类构造点**不会报**，因 `mypy.ini:150` 已为该文件压制 `typeddict-item`（既有债务，非本次新增）⇒ **reporting 侧不会替你把漏补的构造点报出来**。这条对批次 3 有效：**该文件的构造点缺键，静态检查一句话都不会说。**

### 17.2 三条退路，逐条堵死（**这一节是 §14.3.0 第三条自查的执行结果**）

> §14.3.0 第 3 条要求：凡得出「做不到」，必须**列出已检查过的路径清单**并说明**为什么这份清单是穷尽的**。本节即该清单。

| # | 退路 | 撞上什么 | 判定 |
|---|---|---|---|
| 1 | 在批次 1a 内顺手给 `planning.py` 两处构造点补上 `success_criteria` | **撞批次 1a 文件边界**（本批红线明写不碰 `core/nodes/planning.py`），且等于把 `T-S8-1b-2` 的生产者部分偷偷提前 | ❌ 不可走 |
| 2 | 在批次 1a 内顺手改 `execution.py:2451` | **撞冻结表红线** —— 该行在 `_build_execution_result` 内，属 §12 明列的冻结区 | ❌ 不可走 |
| 3 | 往 `mypy.ini` 债务清单加 `typeddict-item` 豁免 | **撞 `mypy.ini:25` ratchet 纪律「禁止反向新增 code」** | ❌ 不可走 |
| 4 | 把两键改成 `NotRequired` / `total=False` | ❌ **本裁定明确不走**：会**改变架构 §2.1 / §2.5.1 已裁定的声明形态**，且 `total=False` 会连既有 13 / 11 个键一起放松，**代价远大于一次排期调整**。⇒ **形态不动，动排期。** | ❌ 主动否决 |

**为什么这份清单是穷尽的**：`[typeddict-item]` 只有三个消除方式 —— ①**让构造点不缺键**（退路 1/2，按缺键的两个文件穷举）；②**让检查器不看**（退路 3，唯一入口是 `mypy.ini`）；③**让键不必填**（退路 4，唯一手段是 `NotRequired` / `total=False`）。三类之外无第四类。⇒ **只剩"改排期"这一条，而它恰好零成本**（见 §17.4 旁证）。

### 17.3 裁定内容

🔴 **两键仍为普通必填键 —— 架构 §2.1 与 §2.5.1 的声明形态一字不改**，但**各自与其构造点原子同批**：

| 原 `T-S8-1a-2` 的内容 | 去向 |
|---|---|
| 第 1 条：`ReproductionPlan` 加 `success_criteria: str` + docstring | → **`T-S8-1b-2` 新增第 0 条**（与 `planning.py:467` / `:676` **同批同 commit**）；该任务产出文件扩为 `core/nodes/planning.py` **+ `core/state.py`** |
| 第 2 条：`ExecutionResult` 加 `conclusion: Dict[str, Any]` + docstring | → **`T-S8-2-8` 新增第 0 条**（与 `_build_execution_result` 换发 + `:2908` 降级构造点同批同 commit，**与已裁的 `metrics` / `metrics_groups` 删键落在同一任务**）；产出文件扩为 `core/nodes/execution.py` **+ `core/state.py`** |
| 第 3 条：`state.py:170` `metrics_groups` 注释订正 | 🔴 **整条注销**（dev-plan `P-S8-17`）：该注释**所依附的键在 `T-S8-2-8` 被整体删除**（§2.6 / §12 v2.4 条目）⇒ **订正一个即将被删掉的注释是净负工作** |

**批次 1a 由 5 个任务变 4 个**（`T-S8-1a-1` / `-3` / `-4` / `-5`）。**批次 1a 对 `core/state.py` 零改动。**

> ✅ **开发代理已实测确认**：`T-S8-1a-2` 不执行后，批次 1a 的 mypy 面**零变化**，`CP-1a.5-6` 直接绿（`Success: no issues found in 27 source files`，2026-08-07）；草稿已按 `cp` / `git apply -R` 纪律完整还原，`core/state.py` **逐字节回到 HEAD 原样**（`git diff` 为空自证）。**主控已独立复跑复现该 4 errors 并还原核实**（TODO `:1116`）。

### 17.4 旁证：批次 2 的键数账**本来就把这事算进去了**（本裁定不是新造一套）

- **`dev-plan.md:1378` 的 `CP-2.8-10` 早就写着**：「`set(ExecutionResult.__annotations__)` **恰为 10 键**（11 − `metrics` − `metrics_groups` **+ `conclusion`**）」 ⇒ **批次 2 的键数账本来就假定「`conclusion` 是在 `T-S8-2-8` 加的」。** 本裁定是把**排期改回与既有账目一致**，不是新增约束。
- **`CP-2.8-16`** 已写着「删形参与删声明**在同一次提交内完成**……若拆成两步，中间态必报 `typeddict-unknown-key`」 —— **那是同一条原理的另一半**（删键方向）。

⇒ 🔴 **这两条旁证是本裁定"零成本"的依据**：改的只是一个任务的归属，**没有任何检查点因此需要放宽**。

### 17.5 升格为通用纪律 `R-S8-42`

> 🔴 **`R-S8-42`：`TypedDict` 的键声明必须与它的构造点原子同批（同一次 commit）。加键、删键**双向适用**；**读侧不受此限**。

- **加键方向**（本次实测）：加必填键 ⇒ 既有构造点全 `[typeddict-item]` 红。
- **删键方向**（`P-S8-13` 已实测）：声明先删而传参还在 ⇒ `[typeddict-unknown-key]` 红。
- **读侧不受此限**（同次实测）：`r.get("metrics")` 读一个未声明的键，mypy **不报错** ⇒ reporting / ui 的消费点**可以留到批次 3**，不必与声明同批。
- ⚠ **例外面**：`mypy.ini:150` 已为 `reporting.py` 压制 `typeddict-item` ⇒ **该文件的构造点缺键不会被静态检查抓到**，须靠人工与用例守（见 §17.1 末）。

**验红要求（不做则该纪律无门）**：`R-S8-42` 必须有**活体证明**——
- `CP-1b.2-11` 加一项「⑥ 把 `success_criteria` 从 `planning.py` 任一处构造点删掉 → `.venv/bin/mypy` **必红** `[typeddict-item]`」；
- `CP-2.8-*` 的验红条同款追加一项，针对 `execution.py:2908` 降级构造点。

### 17.6 被迁出的检查点：处置纪律（**`dev-plan.md:616` 引的就是本小节第 2 条**）

`T-S8-1a-2` 名下原有 6 条检查点（`CP-1a.2-1 ~ 6`）。处置如下：

1. **逐条给去向，不做整体注销。** 拆分落点：`CP-1a.2-1` → `CP-1b.2-12` + `CP-2.8-17`；`CP-1a.2-2` → `CP-1b.2-13` + **并入既有 `CP-2.8-11`**（不新设）；`CP-1a.2-3` → `CP-1b.2-7` 吸收 + `T-S8-2-8` 承担；`CP-1a.2-4` → `CP-1b.2-14`；`CP-1a.2-5` → **注销**，换发 `CP-2.8-20`；`CP-1a.2-6` → **一分为二** `CP-1b.2-15` + `CP-2.8-19`（两个碰 `state.py` 的批次各自证一次）。
2. 🔴 **六条一律「不勾、不留空、标注去向」。** —— **留空会被后人当成"没做完"，标注去向才是可追溯的账。** 这是账目纪律，不是格式偏好：`docs/sprint7/dev-plan.md` 批次 0~3 那次 71 条未勾，正是因为「留空」与「没做」在磁盘上长得一模一样，才付了一次全量清账的代价。
3. **不新设自相矛盾的门。** `CP-1a.2-2`「既有键与顺序零扰动」在执行结果侧**不能**照搬 —— `T-S8-2-8` 同任务内既加 `conclusion` 又删两键，"前 11 键逐字相同"在任务内部**就是假的**；`CP-2.8-11` 现有口径「11→10 键 + 其余 8 键逐字未变」已把加键算进账。

### 17.7 🔴 元教训：勘误流程缺一步「对称面复查」

**`P-S8-13` 把「删键」方向实测了、裁了、留档了，但没有人回头检查「加键」方向。** 同一份文档在同一次 v1.3 修订里，**对同一个机制只查了一半**。

⇒ **这不是疏忽，是流程缺一步**：**凡实测出某个方向的静态约束，须当场问一句「反方向呢」。**

🔴 **它与 §14.3.0 那三条自查是同一个毛病的第四种长相**：

| 次 | 我检查了什么 | 我下的结论 | 我没检查的那一面 |
|---|---|---|---|
| 一（v0.2） | **判定路径**读不了嵌套 | "下游读不了" ⇒ 设计改窄 | **渲染路径**一直是通的 |
| 二（v2.2 的 AR-S8-10） | **产物路径**验不了论文值 | "验不了" ⇒ 不验 | **状态里的 `baseline_results`** 一直在那 |
| **三（本次，P-S8-13）** | **删键**方向会红 | 记下删键纪律 | **加键**方向同样会红 —— **同一个机制的另一半** |

**前两次是「只走了一条路」，这次是「只查了一个方向」。**⇒ **`AR-S8-15` 的元教训不是新的一条，是 §14.3.0 那条自查的一个新适用面**：**"穷尽路径"里的"路径"，也包括同一机制的反方向。**

### 17.8 连带跟改点（本文档已改的 / 交主控落 `dev-plan.md` 的）

**本文档 v2.5 已同批改**：§0 表 Q-S8-02 行（阻塞批次口径）、§10 表（新增 `AR-S8-15` 行）、§11 前置约束 1（1a 的 `state.py` 依赖已归零）、§12 `core/state.py` 条目（两键各自的批次归属）、§14.6（跟改说明）、本节。

**交主控落 `dev-plan.md`（架构不改开发计划）**：编号换发的引用点清单 + `R-S8-42` 入 §12 关键纪律 + §3.1 批次总览 1a/1b 两行 + §3.2 依赖图 `A2` 节点 + `:498` 批次 1a 文件边界 + §5 批次 1b 与 `T-S8-1b-2` 正文 + `T-S8-2-8` 正文 + §13 CP 索引。**逐点行号与改后文本随本次交付一并给出，不在本文档内展开。**

> ✅ **`P-S8-17` 要求的「架构 §5.5 末段订正」已无须再做** —— 本文档 **v2.4 已经改过**（§5.5 `:534-537`：三处注释「不是改措辞，是随所在物一起消失」，其中 `core/state.py:170` 明标「v2.4 改判：随 `metrics_groups` 键一并删除」）。`docs/TODO.md:1129` 把「architecture.md §5.5」列进待办，**是照 dev-plan 的转述记的账，未上磁盘核对本文档** ⇒ 该子项可直接销账。
