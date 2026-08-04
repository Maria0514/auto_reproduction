# Sprint 8 核心架构设计文档：输出契约驱动的复现结果链路 + 结论档位重新定义

**文档版本**：v2.0（**跟改 Maria 第四轮拍板：成功标准改由计划针对本篇论文写明**。v1.0 = Q-S8-01 ~ Q-S8-06 六项全裁 + 新识别 Q-S8-07 / Q-S8-08；**v2.0 = Q-S8-02 / Q-S8-05 扩围 + 新增 Q-S8-09（护栏 3 落点）+ 编号撞车换发 + 「零新计划字段 / 唯一状态契约新增」两处表述作废**，逐条跟改清单见 §14）
**日期**：2026-08-03
**作者**：架构师代理
**对应 PRD**：`docs/sprint8/prd.md` **v3.0**（Maria 四轮拍板已回填；**§4.5.2「两层分离」是本次跟改的总纲**）
**体例参照**：`docs/sprint7/architecture.md` v1.3
**推翻的既有裁决**：`docs/sprint5/architecture.md` §7.10 的二选一裁决（"弃选扩展 `<METRICS>` 多块约定、选文件扫描"）——本次两条路**都不走**：`<METRICS>` 通道整体退场，文件扫描 `_collect_grouped_metrics` 降为兜底（S7-13 已先行降级），主通道改为 agent 汇报 + 系统验钞。

> **本文档的裁定范围**：只裁"怎么实现"。Maria 四轮拍板的产品决策（六条决策 / 四档制 / 判定落点搬迁 / 第四态作废 / 造假审计改判 / 三条封顶 / 审计盲区不治 / **成功标准由计划针对本篇写明** / **两层分离** / **护栏 3 不做阻断门**）**一律照办，不改、不优化、不加回被砍项**。凡在架构上落不了地或存在产品口径冲突的，一律列进 §9「须 Maria 复裁」，不自行调和。
>
> **贯穿硬约束**：不新增 interrupt 种类 / 不改编排图 / 不改人在回路三个交互点 / 保 S-1 重跑幂等契约（`_has_committed_result_for_round` guard）/ **状态契约新增严格限两处**（`ExecutionResult.conclusion` + `ReproductionPlan.success_criteria`，Q-S8-02）/ **护栏 3 只产警示、不阻断审批** / 反过度工程（MEMORY §4.1）：零新模块、零新枚举类、零"将来可能用得上"的扩展点。
>
> **先说本 Sprint 的架构级结论**：**新增抽象总量 = 2 个状态契约键（跨两个结构）+ 1 个 `ErrorCategory` 成员 + 4 个 execution 侧纯函数 + 1 个 reporting 侧纯函数 + 1 条 `check_plan` 警示 + 1 组 term_map 换发**。`_SandboxRunCollector` 一字不动；`code_fs_tools.py` 一字不动；`react_base.py` 一字不动；`graph.py` 一字不动；`config.py` 零新增常量；`check_plan` 既有五条警示行为与既有两个调用点一字不动。
>
> 🔴 **v2.0 的总纲（落地时最容易搞砸的一件事）**：**第一层（四档语义边界，所有论文共用）与第二层（本篇达标线，计划写）必须落在两个物理位置** —— 第一层写在**系统提示词（稳定前缀）+ execution 模块级常量**里，第二层走 **HumanMessage 动态通道**。**两者不得混在同一段文本里。** 混了要么退回硬编码（第二层被写死），要么允许越权（第一层被计划改动）。详见 §2.5.4。

---

## 0. 裁定总表（先给结论；**v2.0 已按第四轮拍板更新**）

| 编号 | 裁定结论一句话 | 主落点 | 阻塞批次 |
|---|---|---|---|
| **Q-S8-01** | **判定不进收集器**：走 `final_state["result"]` 为权威 + messages 回读为**存在性兜底**（`_merge_with_collector` 的镜像应用），判定缺失时走封顶而非判失败 | `execution.py` `_run_execution_agent` / 新 `_resolve_agent_report` | 批次 2 |
| **Q-S8-02** 🔴 **v2.0 扩围** | **跨两个结构、共两个新增键**：①`ExecutionResult` 加 `conclusion: Dict`（`{level, goal_checks, evidence}`），沿 `step_reconciliation` 嵌套字典范式，`level` 存的**就是用户可见的四个中文档名**、无第二套值；②`ReproductionPlan` 加 **`success_criteria: str`（单个字符串，不是"档位→达标线"的字典）**——四档名因此**不出现在计划里**，两层分离由结构本身守住（§2.5） | `core/state.py:159-184` + `:115-157` | 批次 1a（字段名）／1b（生产者）／2（消费者） |
| **Q-S8-03** | **验钞函数内联自判**（4 行 resolve + is_relative_to，与 `reporting._resolve_report_path` 同范式）；**工具层 `_is_within_workspace` 一字不动** ⇒ 两个闸物理分处两文件，不可能被合成一个 | `execution.py` 新 `_verify_evidence` | 批次 2 |
| **Q-S8-04** | 新增 `ErrorCategory.NO_VERIFIABLE_OUTPUT`；**早停轮数常量复用 `NO_METRICS_EARLY_STOP_ROUNDS`（config 零新增）**；早停在优先级链中**原位继承**，`:2817-2840` 的顺序一字不动 | `execution.py:132-171` / `:2729` / `:2817` | 批次 2 |
| **Q-S8-05** 🔴 **v2.0 扩围** | `_verify_trend` / `_lookup_metric_value` / `_match_metrics_group` **三个全退场**；`_verify_expected_results` 退化为**旧快照兼容读**；`_determine_conclusion` 改名 `_assemble_conclusion`（只算 annotations + 取执行环节判定）；审计**脱离 `simulation` 标注、独立成节**，execution 侧就地调 `audit_code_dir` 注入上下文；**v2.0 加一条：新增 `_render_success_criteria`，把本篇成功标准原文照登进报告（§5.7）** | `reporting.py:136-324` / `:587-704` | 批次 3 |
| **Q-S8-06** | 沿"非空才注入"，键名 `baseline_results` 与 state 同名透传；**无该值时 payload 字节零扰动 ⇒ 既有基线不换发，只新增"有该值"一条基线**；系统提示词哈希基线本批必换发一次（原因是判定纪律段改写，不是本项） | `execution.py:_build_execution_agent_context` | 批次 1a |
| **Q-S8-07**（v1.0 新识别） | `ErrorCategory.NO_METRICS` **枚举成员必须保留**（旧 checkpoint 反序列化面），只删唯一生产者 `_apply_no_metrics` | `execution.py:132-171` / `:3026` | 批次 2 |
| **Q-S8-08**（v1.0 新识别） | 七处随四档制作废的**用户可见文案**须同批换发并进守门面（清单见 §5.5） | `reporting.py` / `execution.py:2715` / `ui/term_map.py` | 批次 3 |
| **Q-S8-09** 🔴 **v2.0 新增**（= PRD v3.0 里那个撞号的 Q-S8-07，**已换发**，见 §14.2） | 护栏 3 落在 `check_plan` 新增第 6 条警示；**只产警示、不阻断审批**（产品决策，不推翻）；⚠ 判据要用论文分析的事实层名词，而现签名拿不到 ⇒ **加一个带默认值的关键字形参**，既有两个调用点与既有五条警示**一字不动**（§15） | `core/plan_checks.py:483` / `ui/pages/plan_review.py:786` | 批次 1b |

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
  步骤 4.4  _split_reported_metrics（保留，S8-06 改组名语义 + 撞名处置）
  步骤 4.5  metrics_groups（保留，agent 汇报优先、扫盘兜底）
  步骤 4.6  _reconcile_steps（位置不动）
  步骤 4.65 _audit_declared_steps（位置不动）
  步骤 4.7  _apply_incomplete_execution（保留）
  步骤 4.75 ★ 新增 _verify_evidence + _decide_conclusion   ← 本次唯一新增步骤
  步骤 4.8  ★ 新增 _apply_no_verifiable_output（取代被删的 _apply_no_metrics 的位置）
  步骤 5    _build_execution_result（多收一个 conclusion 参数，随 exec_result 一次 commit）
  ```

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

```jsonc
{
  "level": "复现成功",              // 四档字面量之一，就是用户可见文案本身
  "goal_checks": [                  // 逐条预期三态结论（与 reporting 既有渲染入参同形）
    {"description": "…计划预期原文…",
     "verdict": "印证上了",         // 三态之一（字面量见 §9 复裁项 1）
     "evidence": [{"path": "outputs/umap/summary.json", "value": "0.62",
                   "ok": true, "reason": ""}]}
  ],
  "evidence": [                     // 支撑档位本身的物证（封顶 3 判据的输入）
    {"path": "...", "value": "...", "ok": false, "reason": "路径越出本次代码目录"}
  ]
}
```

### 2.2 三个备选与取舍

| 方案 | 形态 | 优点 | 否决理由 |
|---|---|---|---|
| **A（采纳）** | 单键 `conclusion: Dict`，内含 level / goal_checks / evidence | **只加 1 个 TypedDict 键**；与 `step_reconciliation` 完全同范式（既有先例，`.get()` 防御读一条就够）；**与 `reporting._determine_conclusion` 现有返回结构 `{level, annotations, goal_checks}` 同形** ⇒ 报告侧渲染函数 `_render_goal_checks(conclusion)` **入参零改动** | — |
| B | 三个平键 `conclusion_level` / `goal_checks` / `evidence_ledger` | 扁平、读起来直白 | 三处 TypedDict 加键、三处降级构造点补默认值、三处旧快照防御读——**违反"唯一状态契约新增"**，且 `evidence` 与 `goal_checks.evidence` 语义同族却被拆散 |
| C | 复用 `metrics_groups` 塞判定 | 零新增键 | 语义污染（指标容器承载结论）；`metrics_groups` 有独立消费者（对比表），混装必然互相干扰；踩"字段被复用到变形"同一族过度设计病 |

### 2.3 档名：一套值，不做内部枚举 + 展示名两套（A-S8-05 的架构兑现）

- `level` 落盘的**字面量就是**「复现成功」/「部分复现」/「仅代码跑通」/「失败」四个中文串之一。**不引入 `ConclusionLevel` Enum、不引入 `"success"/"partial"` 之类的英文内部值。**
- 四个字面量在 `execution.py` 收敛为**四个模块级常量**（`_LEVEL_SUCCESS` 等）+ 一个 `_LEVELS: Tuple[str, ...]` 顺序元组（从高到低，供封顶做"取较低者"比较）。**封顶 = 按元组下标取更低档，不写 if 链**——这同时天然满足 AC-S8-08④「只压低不抬高」。
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
- **消费侧一律 `.get("conclusion") or {}`**：旧 checkpoint（10 键 / 7 键快照）读到 `{}` ⇒ `level` 缺失 ⇒ 报告侧走"旧快照兼容分支"（§5.4），**不崩、不假装有结论**。
- **`success` 由 `level` 派生**（PRD §4.5.4）：`success = level in {"复现成功", "部分复现"}`。旧快照 `conclusion` 为空时 `success` 仍读既有 `success` 键原值（它在旧快照里是有的）⇒ 旧报告可重放。

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
3. 静态可断言：`success_criteria` 在 `core/` 下的出现点**只允许三处**——`state.py`（声明）、`planning.py`（生产）、`_build_execution_agent_context` + `coding.py` 上下文（注入）。**判定函数体内零出现。**

#### 2.5.5 默认值、防御读、required 归属

- **默认 `""`，缺键 ≡ `""`**（沿 S7-08 `scale_reduced` / `local_fit_note` 范式）；下游一律 `.get("success_criteria") or ""`，旧 checkpoint 不 KeyError。
- 🔴 **进 planning 输出契约的 `required`——这是对 S7-08 纪律 2 的有意背离，须留档**：纪律 2（新键不进 required，避免 `react_base` finalize 多烧一次 schema 重生成）的成立前提是"缺省已是安全值"。而这里缺省 `""` **不是**安全值——等于这篇论文没有判定依据，整条判定链当场断。⇒ **代价（缺失时多烧一次调用）正当且可接受**，与 `scale_reduced` 的情形性质相反，不构成对该纪律的推翻。
- **注入范式**：`_build_execution_agent_context` 末尾追加 `success_criteria`，**非空才注入**（与 `baseline_results` 同款）⇒ 无该字段的旧计划 payload 字节零扰动。`coding.py` 上下文同款（PRD §4.2 第 3 条）。
- **幂等**：它是计划字段、随计划一次落盘，execution / coding **只读不写** ⇒ 零幂等风险，不进 `_build_execution_result`。

#### 2.5.6 标准缺失时的档位语义（**是既有封顶的推论，不是新增第四条封顶**）

`success_criteria` 为空（旧计划 / 规划没写 / 用户删空）⇒ agent 没有可核验的「印证」判据 ⇒ 它所报的「复现成功」/「部分复现」**没有成立的支撑物证** ⇒ **落既有封顶 3「仅代码跑通」**（§4.5.3 第三条，A-S8-08）。

🔴 **架构在此明确不自造新规则**：不新增"第四条封顶"，因为既有第三条已经覆盖。**开发不得在代码里另写一条"标准为空则降档"的分支**——那会变成两处定义同一件事，日后必然打架。

---

## 3. Q-S8-03：证据路径限死本次代码目录（与工具层边界是两个闸，不许合并）

### 3.1 结论：验钞函数内联自判，工具层一字不动

```
_verify_evidence(evidence_item, code_output_dir, extra_commands) -> (ok, reason)
    ①路径真实存在  ②可读  ③数值前缀匹配可查  ④落在 code_output_dir 之下  ⑤未在计划外命令参数里字面出现
```

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

### 5.1 三个函数的去留

| 函数 | 裁定 | 理由 |
|---|---|---|
| `_verify_trend`（`:179-198`） | **整体删除** | 复裁 6，Maria 知情后拍板 |
| `_lookup_metric_value`（`:160-176`） | **整体删除** | 唯一调用点是 `_verify_trend`；留着就是死代码 |
| `_match_metrics_group`（`:136-157`） | **整体删除** | 同上；且它的"归一化模糊匹配"正是 S7-13 真跑挖出的歧义源 |
| `_normalize_group_key`（`:130-133`） | **随之删除** | 上两者的唯一依赖 |
| `_verify_expected_results`（`:201-242`） | **退化保留**，语义收窄为「旧快照兼容读」 | 见 §5.4 |
| `_determine_conclusion`（`:245-324`） | **改名 `_assemble_conclusion`，判定职责退场** | 见 §5.2 |

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

另有两处**非文案但同源**的说明须一并订正（PRD §4.7 第 1 条已点名）：`reporting.py:955` 与 `:995` 的"组名为产物目录相对路径"、`core/state.py:170` 的同款注释——方案 A 之后组名由 agent 按计划写法填，与目录无关。

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

---

## 7. Q-S8-07（新识别）：`ErrorCategory.NO_METRICS` 枚举成员必须保留

**发现**：`_apply_no_metrics` 删除后 `NO_METRICS` 无生产者，看起来该一并删枚举成员。**不能删**——`_feedback_from_committed_result`（`execution.py:3026`）从已落盘 `ExecutionResult.errors[0]` 的 `[error_category=xxx]` 前缀**反序列化**重建 `ErrorCategory`。旧 checkpoint（含 `task-99eef17bccf2` 等回归现场样本）里存着 `error_category=no_metrics` 的字符串，删成员会让**旧任务 resume 当场炸**。

**裁定**：`ErrorCategory.NO_METRICS`（`execution.py:151`）成员**保留**，加注释「Sprint 8 起无生产者，仅供旧 checkpoint 反序列化」；`AUTO_FIXABLE`（`:161-169`）中的归属**不动**；`ui/term_map.py` 的 `error_category:no_metrics` 文案**保留不删**（旧报告仍要能渲染）。

**验证**：一条旧 checkpoint 反序列化用例（构造 `errors=["[error_category=no_metrics] ..."]` 的 `ExecutionResult` → `_feedback_from_committed_result` 不抛异常）。

⚠ 这条同时是 AC-S8-15「`_apply_no_metrics` 已删除且无残留引用」的**边界澄清**：清零断言的对象是**函数与其调用点**，**不是枚举成员**。写测试时若把枚举成员一并清零，会当场把旧快照兼容打掉。

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

## 10. 风险与验证（架构侧新增，PRD R-S8-01~15 不重复）

| 编号 | 风险 | 缓解 / 验证 |
|---|---|---|
| **AR-S8-01** 🔴 | **批次 1 单独落盘会把系统打进"全判失败"的不可用中间态**：S8-02 删掉 `<METRICS>` 三处后 `metrics` 恒空，而 `success` 的第二合取项 `len(metrics) >= 1` 要到批次 2 才被四档判据取代（PRD §4.5.5 已论证）。PRD §10 把 S8-02 放批次 1、判据放批次 2 ⇒ 两批之间的任何一次真跑 / 演示都会一律判失败 | **调整拆分**：见 §11。批次 1 只做 S8-01 / S8-03 / S8-10 |
| **AR-S8-02** | **`_resolve_agent_report` 的回读兜底本身可能成为"假绿通道"**：若回读放宽到"任意 AIMessage 里的 JSON 块" | 写死：只认 `<result>` 标签包裹、只取最后一条、解析失败即空。V1/V2 用例守 |
| **AR-S8-03** | **物证核验读盘的 IO 异常炸节点** | `_verify_evidence` 全程 try/except，异常 ⇒ 该条判**不成立**（保守方向，不是"放行"）+ WARNING |
| **AR-S8-04** 🔴 | **"一条统一判据"在落地时长回两套**（R-S8-12 的架构对偶）：开发极可能按"数值 / 趋势 / 定性"给 `_decide_conclusion` 写三个分支 | 架构写死：`_decide_conclusion` **只读 `level` + 数封顶**，**不读证据形态、不解析证据语义**；AC-S8-07④ 的负向静态断言对象就是这个函数 |
| **AR-S8-05** | **`conclusion` 键与 reporting 局部变量 `conclusion` 同名**，易在阅读 / 改动时串味 | 有意为之（同形同名，降低认知成本）；在两处 docstring 互相点名 |
| **AR-S8-06** | **审计在 execution 每回合跑一次**，修复循环多轮即多次 AST 全目录扫描 | 纯本地、无 LLM / 配额；`honesty_audit` 已有排除目录清单。**登记不治**；若真跑观测到耗时异常，再议 |
| **AR-S8-07** | **`EXECUTION_OUTPUT_SCHEMA` 新增字段若列进 `required`**，会让"跑挂了、没判定"的回合每次白烧一次 schema 重生成调用（`react_base._missing_required_fields`） | **写死：新增字段一律不列 `required`**——与 `metrics` 刻意不列 required 的既有理由逐字同源（`execution.py:1090-1091`） |
| **AR-S8-08** | **`_split_reported_metrics` 现行"先到先得"与 S8-06 的"撞名两条都丢弃"直接冲突**（`execution.py:1796-1797` 逐字写着先到先得） | 该函数的去重策略须同批改为"值不同则两条都丢弃 + WARNING"，**值相同的重复仍按一条收**（AC-S8-16 的验红对象） |

---

## 11. 批次与开工顺序（**v2.0 重写**：v1.0 的调整案已撤回，改为接受 PRD v3.0 的 1a / 1b 拆分）

> **v1.0 曾建议把 S8-02 从批次 1 移到批次 2**（理由：AR-S8-01 中间态）。PRD v3.0 已把批次 1 拆成 1a / 1b，**1a 是纯"能力接入 + 通道退场"、1b 是计划侧**——这个拆法与我的顾虑不冲突，且粒度更细。⇒ **撤回原调整案，接受 PRD 拆分**，只追加两条前置约束（见下）。

| 批次 | 内容 | 架构意见 | 依赖 |
|---|---|---|---|
| **1a** | S8-02 / S8-03 / S8-10（编码产出约定 + 通道退场、执行只读工具、论文报告值送达） | ✅ **可最先开工，且可与本次跟改并行** | Q-S8-06（已裁）+ **Q-S8-02 的字段名**（见前置①） |
| **1b** | S8-01 扩围 + S8-11 三道护栏 | ✅ 可开工 | Q-S8-02（§2.5 已裁）+ **Q-S8-09**（§15 已裁） |
| **2** | S8-04 / S8-05 / S8-06，**内部不得拆分** | ✅ | Q-S8-01 / Q-S8-02（均已裁） |
| **3** | S8-07 / S8-08 / S8-09 + 档名文案换发 | ✅ | Q-S8-05（已裁，含 §5.7 扩围） |
| **4 / 5** | 回归对平 / 真跑取证 | 不变 | — |

🔴 **两条前置约束（不写清会出事）**：

1. **1a 已经触及新计划字段，不是"只依赖 Q-S8-06"**——PRD v3.0 §4.2 第 3 条把「本篇成功标准送进编码环节上下文」放进了 S8-02。⇒ 1a 依赖 `success_criteria` 这个**字段名**。**本文档 §2.5 已裁定该字段名，依赖即刻解除**；1a 按"非空才注入 + `.get()` 防御读"实现即可——此时字段还没有生产者（生产者在 1b），读到空 ⇒ 不注入 ⇒ 字节零扰动，**不会因为字段没人写而出错**。
2. **AR-S8-01 依然成立**：1a 落盘后、批次 2 完成前，`<METRICS>` 通道已退场而新判据尚未上线 ⇒ `metrics` 恒空 ⇒ 成功判据第二合取项恒假 ⇒ **系统处于"一律判失败"的中间态**。⇒ **代码可以并行写、可以并行落盘，但在批次 2 交付前不得做端到端真跑、不得对外演示**。这不是能不能开工的问题，是可用性恢复时间点的问题，须在开发计划里写明。

---

## 12. 开发交接清单（文件级，含函数名 / 行号；架构不写实现代码）

### `core/state.py`
- `ExecutionResult`（`:159-184`）加 1 键 `conclusion: Dict[str, Any]` + docstring 补 Sprint 8 段（沿 sp5 / sp7 加键注释体例）。**其余键、顺序一字不动。**
- 🔴 **`ReproductionPlan`（`:115-157`）加 1 键 `success_criteria: str`** + docstring 补第四批段（§2.5）。**既有 13 键、顺序一字不动。**
- `:170` 的 `metrics_groups` 注释订正（组名不再是产物目录，见 §5.5）。

### `core/nodes/execution.py`
- `ErrorCategory`（`:132-157`）：新增 `NO_VERIFIABLE_OUTPUT` 并入 `AUTO_FIXABLE`；`NO_METRICS` **保留**加注释（§7）。
- `_extract_metrics_block`（`:402`）/ `_regex_scan_metrics`（`:426`）/ `_llm_extract_metrics`（`:452`）/ `_parse_metrics`（`:517`，含死参数 `plan`）：**判定链路解绑**（PRD §4.2 第 4 / 5 条）。
- `_EXECUTION_SYSTEM_PROMPT_BODY`（`:1144`）：改写"成功判定纪律（强约束）"三句（`:1159-1162`）+ "输出要求"段；⚠ 措辞按 PRD §4.9.5 措施 1，**不得回灌判定规则、不得写成"报了就算成功"**。
- `EXECUTION_OUTPUT_SCHEMA`（`:1092`）：新增 `conclusion_level` / `goal_checks` / `evidence` 三字段；`metrics[].group` 的 description 改为"把维度写进组名"（S8-06 方案 A）；**新增字段一律不进 `required`**（AR-S8-07）。
- `_build_execution_agent_context`（`:1299`）：末尾追加两处"非空才注入"——`baseline_results`（Q-S8-06）、`code_audit_findings`（§5.6 A）。**既有键的构造顺序与取值一字不动。**
- `_run_execution_agent`（`:1551`）：绑入 `make_read_code_file_tool()` / `make_list_dir_tool()`（`:1581-1584` 工具列，**不新造工具**）；调 `audit_code_dir` 并透传；收尾改调新 `_resolve_agent_report`；`ExecAgentOutput`（`:1186`）加 `report` 字段。
- `_split_reported_metrics`（`:1781`）：撞名策略改为"值不同则两条都丢弃 + WARNING"（AR-S8-08）。
- **新增（四个纯函数，紧邻既有同族函数放置）**：`_resolve_agent_report`（放 `_merge_with_collector` 之后，共用范式注释）、`_verify_evidence`、`_decide_conclusion`（放 `_split_reported_metrics` 附近）、`_apply_no_verifiable_output`（放 `_apply_incomplete_execution` 之后）。
- **删除**：`_apply_no_metrics`（`:2242-2271`，零改动红线已由 Maria 解锁，留档在 PRD §4.5.4 第 4 条）。
- `_no_metrics_stalled`（`:2729`）→ `_no_progress_stalled`；`_NO_METRICS_EARLY_STOP_SUMMARY`（`:2715`）文案换发。
- `_build_execution_result`（`:2395`）：新增形参 `conclusion`；`success` 改为由 `level` 派生（`:2428-2432` 的三合取判据整体退场）。
- `execution()`（`:2874`）：插入步骤 4.75 / 4.8（§1.5）；降级构造点（`:2908-2917`）补 `conclusion={}`。
- **不动**：`_SandboxRunCollector`（`:805-826`）、`_merge_with_collector`（`:1517`）、`_reconcile_steps`、`_completion_insufficient`、`_has_committed_result_for_round`、`:995` 不得写代码防线、`:1010` 管道 / 重定向拒绝、`:2817-2840` 优先级链顺序。

### `core/nodes/reporting.py`
- 删：`_normalize_group_key` / `_match_metrics_group` / `_lookup_metric_value` / `_verify_trend`（`:130-198`）。
- `_verify_expected_results`（`:201`）：退化为旧快照兼容读（§5.4）。
- `_determine_conclusion`（`:245`）→ `_assemble_conclusion`（§5.2）；`:281-282` 审计析取项删除；`:313-322` 判定段整体删除。
- `_render_annotation_notices`（`:587`）：审计 hits 表（`:629-652`）搬出；`:612-613` 导语改写。
- **新增** `_render_audit_findings(audit)`（独立节，空则早退返 `[]`）；`_render_report`（`:1176`）并列调用。
- `_render_goal_checks`（`:707`）：icons 三 key 换发；`:722-723` 与 `:741-747` 文案改写。
- `_render_metrics_comparison`（`:949`）：`:955` / `:995` 组名说明改写；复现侧无数据时**不渲染主实验表**（`:980-989`，PRD §4.7 第 3 条）。
- `_SUCCESS_CRITERIA_NOTE`（`:560-563`）换发。
- **不动**：`_determine_report_form`（`:92-106`）、`audit_code_dir` 调用点与三键返回契约（`:1224-1249`）。

### `core/nodes/coding.py`
- 清除三处 `<METRICS>` 教学文本：`:113`（`entry_script` 结构声明 description）、`:181-186`（整段）、`:191`（修复回合那句）——**三处一起，漏一处就仍在教 agent 写标签**。
- 补产出约定（结果文件写在计划声明的位置、结构自定、合法 JSON 顶层对象）。
- 上下文补 `expected_results` **与 `success_criteria`**（两者今天均零命中；前者是定性物证的生产者，后者让编码环节知道"这次要拿出什么才算成功"）。两处均**非空才注入**。

### `core/nodes/planning.py`
- 提示词：交付清单语义扩为"本次复现应当落地的产物"（复裁 2）；`expected_output` 要求写清相对代码目录的产出文件路径。**`:196` 那句产出目录约定保留不动。**
- 🔴 **v2.0 推翻 v1.0 此处的「不新增计划字段」**（A-S8-02 已被 PRD v3.0 显式推翻）：新增 `success_criteria`，**进输出契约的 `required`**（§2.5.5）。
- 🔴 提示词须同时立三条约束：①**只写本篇达标线、不得改动四档的含义**（两层分离，§2.5.4）；②**必须引用论文的具体主张**（点名指标或论文结论），**禁止"能运行即可"这类空话**（护栏 3 的提示词侧）；③**四档的语义边界不得写进计划提示词的可填内容里**——它属第一层，写进去就等于把第一层交给计划改。

### `core/plan_checks.py`（**v2.0 新增落点**）
- 新增 **W6**（§15）：成功标准未引用论文任何具体指标或结论 → 报警示。
- `check_plan`（`:483`）**加一个带默认值的关键字形参** `paper_analysis: Optional[Dict[str, Any]] = None`；**既有五条 W 的 rule 字符串、message、触发条件一字不动**；既有两个调用点不改也能跑（默认 `None` ⇒ W6 不触发）。
- **零改动红线本次再解锁，范围严格限于上述两项**；`_INLINE_PY_MAX_CHARS` 的可行窗口 `[98, 126]` 与「单一规则、不做动词枚举、不做后缀白名单」两条红线（`:76-89`）**一字不动**。

### `ui/`
- `ui/term_map.py:84-86` → 四条恒等映射（§2.3）+ 注释说明"存在理由是守门通道"。
- `ui/pages/result_report.py:178`：数据源从 `metrics` 改读 `metrics_groups`（全 `ui/` 对 `metrics_groups` 今天零命中 ⇒ 不改则结果页永远显示"无可对比指标"）；结论档位改读 `execution_result.conclusion.level`。
- 🔴 **`ui/pages/plan_review.py`（护栏 1 + 护栏 3 展示，v2.0 新增）**：①成功标准在计划展示区**顶部**只读展示，**不得埋在一堆字里**（PRD §4.11.2），沿用既有"用户可调整任何部分"通道，**不新增交互种类、不新增按钮**；②`:1015` 的 `_render_plan_check_warnings` 调用**多传一个已在 payload 里的 `paper_analysis_summary`**（`:1005` 就在读它）⇒ **警示展示通道零改动、"不阻断审批"契约一字不动**。

### `config.py`
- **零改动**（新早停常量复用既有取值，Q-S8-04）。

---

## 13. 与 Sprint 5 §7.10 裁决的关系（显式留痕）

`docs/sprint5/architecture.md:323` 的二选一裁决（选文件扫描、弃扩展 `<METRICS>` 多块）**本次两条路都不再是主通道**：

- `<METRICS>` 通道：**整体退场**（决策 3）。
- 文件扫描 `_collect_grouped_metrics`：**保留为兜底**（S7-13 已把它降为"agent 一组都没报时才扫盘"，`execution.py:2961`），本次**不删、不改**——它是 agent 完全不服从时唯一还剩的数据来源（R-S8-09 提示词服从率实测约 75%）。

当年三条弃选理由今天的状态（PRD §0.2 发现②已实证，此处只作架构留痕）：①"需改 coding 产出约定"——本次正是要做的事；②"对已有回归样本不可用"——已过期（S7-13 真跑夹具已建）；③"解析仍依赖 agent 服从度"——选了文件扫描后依赖不但没消除反而更糟（那个约定从没进过编码提示词）。**⇒ 本次不是推翻当年的判断力，是推翻当年的前提。**

---

## 14. v1.0 → v2.0 跟改说明（第四轮拍板）

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
2. **命中判定**：`success_criteria` 文本中出现任一候选（**大小写不敏感的子串匹配**）⇒ **不报**；一个都没出现 ⇒ **报 W6**。
3. **两条边界（沿 `plan_checks` 既有"宁窄勿宽"误报防线 R-S6-A5）**：
   - **候选集为空**（论文分析没产出任何事实层名词）⇒ **不报**。无从比对时报警只会制造噪声。
   - **`success_criteria` 为空串** ⇒ **报**。空标准是最该被用户看到的一种，不能因为"没内容所以没法判"就沉默。
4. `rule` 字符串用 `"W6"`（沿既有字面量风格，**不建 Enum**）；`message` 用通俗中文、**不得出现内部字段名**（MEMORY §4.2）。

### 15.4 局限（**必须如实登记，不得包装**）

**它挡的是空话，挡不住"具体但宽松"** ——「knn_accuracy 大于 0 即算成功」引用了具体指标名，照样过（R-S8-17）。

⇒ **真正兜底的是护栏 1（人眼在计划审核页看到并可改）。** 🔴 **不得把 W6 对外宣传成"防止标准画低"的保证**——它只是把最粗暴的那一档挡在门外。这条与 R-S8-01 的对外表述纪律同族。

### 15.5 验证

| # | 验证 | 期望 |
|---|---|---|
| G1 | 正向：成功标准里写了论文分析中的某个指标名 | 不报 W6 |
| G2 | 负向：成功标准 = "只要代码能跑起来就算成功" | 报 W6 |
| G3 | 边界：候选集为空 | 不报（宁窄勿宽） |
| G4 | 边界：成功标准为空串 | 报 |
| G5 ★契约回归 | 两参调用 `check_plan(plan, resource_info)` | 不抛异常、**既有五条 W 的输出与改前逐字节相同**、W6 不出现 |
| G6 ★产品契约 | UI 上出现 W6 警示时 | **审批按钮仍可用**（不阻断，AC-S8-13③） |
| G7 ★验红 | 去掉 W6 判据 | G2 / G4 必红 |
