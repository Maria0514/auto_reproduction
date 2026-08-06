# Sprint 8 开发计划

**产品名称**：Auto-Reproduction —— 论文自动复现系统
**Sprint**：Sprint 8 —— 输出契约驱动的复现结果链路 + 本篇专属的成功标准
**版本**：**v1.3**（🔴 **v1.3 = 跟改 `prd.md` v4.1 + `architecture.md` v2.4**：①**S8-06 / S8-07 回炉**——结果形状由执行环节决定，原方案 A「把维度写进组名」作废，新增结果块契约 / 证据台账 / 通用渲染；②**验钞五重扩为七重**（论文值侧两重，AR-S8-14）；③**`ExecutionResult.metrics` / `metrics_groups` 本 Sprint 删键**（Maria 推翻停产默认取值）；④四个折叠 / 扫盘函数整体删除；⑤`T-S8-3-9` 整条作废并换发。连带见 §14.3 / §15 / §16。v1.2 = v1.0 = 2026-08-04 初稿；v1.1 = 同日回填 Maria 拍板 1/2；**v1.2 = 同日再回填架构师两项裁定 + Maria 加拍**：①**三档 + `_parse_metrics` 四函数整体删除**（推翻本计划默认取值 A）；②**execution 上下文注入迁入批次 2**（`T-S8-2-8b`）；③**Maria 加拍：coding 侧改词一并迁入批次 2**（`T-S8-2-1b`）⇒ **批次 1a 收敛为「只加不改行为」的 5 个任务**。连带同步见 §0.0 / §3.1 / §3.4 / §14.2 / §15 / §16；架构文档同批跟改至 **v2.1**）
**日期**：2026-08-04
**作者**：全栈开发工程师代理
**状态**：草案（待 Maria 审阅后**逐批**授权执行）
**对应 PRD**：`docs/sprint8/prd.md` **v4.1**（AC-S8-01 ~ **AC-S8-26**；R-S8-01 ~ **R-S8-26**）（S8-01 ~ S8-11 十一条全 P0；AC-S8-01 ~ AC-S8-25；R-S8-01 ~ R-S8-17；A-S8-01 ~ A-S8-10；Q-S8-01 ~ Q-S8-07）
**对应架构**：`docs/sprint8/architecture.md` **v2.4**（Q-S8-01 ~ **Q-S8-10**；AR-S8-01 ~ **AR-S8-14**；**§2.6 删键 / §5.8 degraded 缺口 / §5.9 报告侧重裁 / §16 结果块全节**）（Q-S8-01 ~ Q-S8-09 九项裁决 + §12 文件级开发交接清单 + AR-S8-01 ~ AR-S8-08 + §14 跟改说明 + §15 护栏 3 落点）
**体例参照**：`docs/sprint7/dev-plan.md` v1.0（§0 全局纪律 → 概述 → 任务清单总表 → 批次划分与依赖图 → 批次正文逐任务带 CP → 交付物 → 风险登记 → 关键纪律汇总 → 落点核对结论 → 待确认项 → 编号速查 → CP 索引 → 落点勘误留档）

> **本计划性质**：忠实落地 PRD v3.0 + 架构 v2.0，**不重新决策、不改设计**。所有取值 / 落点 / 顺序均取自两份定稿。凡在磁盘上核不实的落点，一律进 §15「落点勘误留档」；凡两份文档之间或与既有实现存在张力的，一律进 §16「待架构 / PM 确认项」并给默认取值，**不自行拍板改设计**。
>
> **⚠ 编号对照（架构 §14.2 已裁，务必先读）**：PRD v3.0 §8 表里那一行 **「Q-S8-07（护栏 3 落点与 `plan_checks.py` 红线再次解锁）」，本计划一律读作 `Q-S8-09`**（架构文档 §7 先占了 `Q-S8-07`）。**本计划全文以架构文档编号为准。**

---

## 0. 全局纪律（贯穿所有批次，不再逐任务复述）

### 0.0 ★★★ 可用性中间态告示（AR-S8-01 / 架构 §11 前置约束 2）——**开工前必须读这一条**

> **🔴 窗口已被压缩进批次 2 内部（Maria 2026-08-04 拍板 2）：从批次 2 内部合入 T-S8-2-1 那一刻起、到批次 2 收口门通过为止，本系统处于「一律判失败」的不可用中间态。**
>
> ⇒ ✅ **批次 1a 落盘后系统仍然可用、可真跑、可演示。** 这正是本次拍板要买到的东西。

因果链（架构 §11 前置②、PRD §4.5.6 已各自论证一次，此处逐环写死）：

| 环 | 事实 | 源码依据 |
|---|---|---|
| ① | **批次 2 首位**的 T-S8-2-1 把 `<METRICS>` 三档从判定链路解绑 | `execution.py:2935` `metrics, llm_calls_used = _parse_metrics(...)` 解绑后 `metrics` 只剩 agent 自报一条来源 |
| ② | 而 S7-13 的自律门控要到 T-S8-2-11 才废止 —— **门控的条件是"主通道非空才采信自报"** | `execution.py:2939-2952`（`if metrics and reported_main` / `elif reported_main:` 打 WARNING 不采信） |
| ③ | ⇒ 主通道恒空 ⇒ 自报恒不采信 ⇒ **`metrics` 恒为 `{}`** | 上两条的合取 |
| ④ | 而 `success` 的第二合取项 `len(metrics) >= 1` 要到 T-S8-2-8 才被四档判据取代 | `execution.py:2428-2432` |
| ⑤ | ⇒ **`success` 恒 `False` ⇒ 所有论文一律判失败**，报告顶部一律印降级形态 | — |

**因此，从批次 2 内部合入 T-S8-2-1 的那一刻起，到批次 2 收口门通过为止（⚠ 窗口整体落在批次 2 内部；批次 1a 期间不受此约束）：**

1. 🔴 **不得做任何端到端真跑**（`-m e2e`、真实 LLM、真实 deepxiv 配额一律不动）；
2. 🔴 **不得对外演示、不得截图、不得拿这期间的结果写进任何报告或简历材料**；
3. 🔴 **不得据此判断"哪里坏了"**——这期间看到的"全判失败"是**计划内的**，不是回归；
4. 单元 / 集成回归**照常跑**（它们喂的是构造 fixture，不依赖真跑），但**涉及 `success is True` 的既有用例会成片变红**，这是预期的（见 §3.3 账目表），**不得为了让它们变绿而回滚 T-S8-2-1**。

**这不是"能不能开工"的问题，是"可用性什么时候恢复"的问题。** **可用性恢复点 = T-S8-2-11（节点主体接线 + S7-13 门控废止）完成**；收口点 = 批次 2 收口门通过。窗口期间若有真跑 / 演示需求，**唯一正确的做法是把批次 2 做完**，不是回滚 T-S8-2-1。

> ### 🔴 排期留痕：窗口为什么被压进批次 2（**Maria 2026-08-04 拍板 2 —— 已裁定，不再是开放项**）
>
> | 项 | 内容 |
> |---|---|
> | **原方案**（本计划 v1.0） | 照 PRD §10 把 S8-02 整体划进批次 1a ⇒ 解绑发生在批次 1a，**不可用窗口横跨「批次 1a 收口 → 批次 2 收口」两个批次边界**，其间还夹着一次"停手等确认"，窗口被人为拉长 |
> | **Maria 拍板** | 把解绑任务（**v1.0 原编号 `T-S8-1a-7`**）**挪到批次 2 首位**，现编号 **`T-S8-2-1`** |
> | **拍板意图（逐字）** | 把「一律判失败」的不可用窗口**压缩进批次 2 内部**，让**批次 1a 落盘后系统仍然可真跑、可演示** |
> | **技术上无障碍** | 该任务与批次 2 同在 `core/nodes/execution.py` 单收口窗口内，本就串行 |
> | **代价** | 批次 2 由 11 个任务变 **12 个**，是全 Sprint 最大的一批 —— 但它**本就受 R-S8-06「内部不得拆分」约束**，多一个任务不改变交付单元的粒度 |
> | **连带同步** | ①批次 1a 收口门由"允许带红"**恢复为要求全绿**（§3.3 / §4）；②§3.3 预期变红面**整体后移进批次 2**；③批次 1a 7 个任务 / 批次 2 **12** 个任务；④编号已重排，迁移留痕见 §14 |
> | **⚠ v1.2 更新（主控复核订正，2026-08-04）** | 本表记录的是 **v1.1 当时**的账，**上方"代价"与"连带同步③"两格的任务数已过期**：v1.2 又把 `T-S8-1a-4`（coding 改词）与 `T-S8-1a-6`（execution 注入）一并迁入批次 2 ⇒ **现为批次 1a 5 个任务 / 批次 2 14 个任务**，以 §3.1 为准。⟦另订正一处**事实错误**：v1.1 此表把解绑任务的旧号写作 `T-S8-1a-5`，实为 **`T-S8-1a-7`**（与 §6 `T-S8-2-1` 迁移留痕、§14.2 编号迁移表三处互证）。成因与 §14.2 记的两处残留同型——**改号时的机械替换连历史留痕一起改了**，而历史留痕恰恰不该跟着动⟧ |
> | **裁定人 / 日期** | **Maria，2026-08-04** |

### 0.1 批次边界逐批确认制（MEMORY §3.3，铁律）

- **每批收口门后停手**，等 Maria 明确确认再开下一批。
- **对某一批的授权 ≠ 对后续批次的授权。**
- **耗配额 / 不可逆的动作即使在批内也须单独授权**（批次 5 全部属此类，且**严禁预授权**）。
- 触发这条纪律的历史事件：sprint5 只批了「批次 0」却被一路推到批次 4。**本 Sprint 五个批次，五道停手线。**

### 0.2 编号约定与起点（防撞号）

| 号段 | 已占用 | 本计划起点 | 说明 |
|---|---|---|---|
| 需求 `S8-xx` | PRD §1：S8-01 ~ S8-11 | — | 本计划不新增需求号 |
| 验收 `AC-S8-xx` | PRD §5：AC-S8-01 ~ AC-S8-25 | — | 本计划不新增验收号；新登记的缺陷用 `DA-S8-N`（见下） |
| 架构问题 `Q-S8-xx` | 架构 §0：Q-S8-01 ~ Q-S8-09 | — | 一律以架构文档编号为准（PRD 的 Q-S8-07 读作 Q-S8-09） |
| PRD 风险 `R-S8-xx` | PRD §6：R-S8-01 ~ R-S8-17 | **R-S8-18 起** | 本计划新登记的风险接续此号段（见 §11） |
| 架构风险 `AR-S8-xx` | 架构 §10：AR-S8-01 ~ AR-S8-08 | — | **本计划不扩这个号段**（它是架构自留段） |
| 任务 `T-S8-{批次}-N` | — | **`T-S8-1a-1` 起** | 批次名用 `1a` / `1b` / `2` / `3` / `4` / `5`，**从 1a 起编，不接 sprint7 的批次 9** |
| 检查点 `CP-{批次}.{任务序}-N` | — | **`CP-1a.1-1` 起** | 例：T-S8-2-5 的检查点是 `CP-2.5-1`、`CP-2.5-2` … |
| 落点勘误 `P-S8-N` | — | **`P-S8-1` 起** | 与 sprint7 的裸 `P-N`（已用到 P-73）**显式分段**，避免跨 Sprint 撞号 |
| 本计划新登记缺陷 `BUG-S8-NN` | — | **`BUG-S8-01` 起** | 沿 Sprint 1 的 `BUG-S1-02` 体例 |
| dev-plan 自定验收点 `DA-S8-N` | — | **`DA-S8-1` 起** | 只给"PRD 未覆盖但本计划要交付"的项（目前仅 BUG-S8-01），**不占用 `AC-S8-*`**（沿 sprint7 S7-10/11/12/13 同款处置） |

### 0.3 测试与回归口径

1. **命令口径**：一律 `.venv/bin/pytest`。裸 `pytest` 不在 PATH；`python3 -m pytest` 报 `No module named pytest`；裸 `python` 是 Python 2（MEMORY §2）。全量非 e2e 回归 = `.venv/bin/pytest -q -m "not e2e"`。
2. **零退化基线 = 2635 passed / 25 skipped / 58 deselected / 7 xfailed**（`docs/TODO.md:845`，2026-08-03 S7-13 独立验收后实测，PRD AC-S8-23 引的就是这个数）。
   - ⚠ **该基线必须在批次 1a 开工第一件事重新实测一次并落档**（T-S8-1a-1 / CP-1a.1-1）。理由：MEMORY §1.2 明写"harness 给的 `git status` 快照不可信、全绿结论必须标时间点"；且本计划落盘与开工之间可能隔着别的会话的改动。
   - ⚠ `deselected` 数只取决于 `-m` 表达式（`e2e` 46 条 + `browser` 12 条，两族不相交，sprint7 §63 P-66 已结清），**与用例增减无关**，对账时不要拿它当分子。
3. **禁弱化自查**（每批收口门必查）：本批 `git diff tests/` 中 `>=` / `issubset` / `pytest.skip` / `xfail` / 删除断言 **零新增**。删除的断言必须是**同处替换成等强或更严的版本**，且逐条能说出替换关系。
4. **验红纪律**：一切验红还原走 **`cp` 文件级备份 + `sha256sum -c` 校验**，**全程禁用 `git checkout` / `git restore` / `git stash`**（sprint7 P-53① 的教训：验红时用 `git checkout` 把并发代理未提交的改动一次性冲掉）。
5. **"写了断言" ≠ "断言有牙"**（sprint7 P-72）：验红首轮没变红的，一律当场加固 fixture 后复验，**并如实登记"首轮无牙"这件事**，不粉饰。
6. **新增正式测试不在开发侧**（沿 sprint7 §61.0 既定分工，交测试工程师）。开发侧只做自测脚本，**只落 `/tmp`，仓库零触碰**。本计划的 CP 是**开发自测检查点**，不是正式用例清单。
7. **一切测试与探针取模块用 `importlib.import_module`**，禁 `import core.nodes.xxx as m`（已知坑 #6：`core/nodes/__init__.py` 的显式 export 会遮蔽子模块，sprint7 P-67 在本仓库实证有效）。
8. **真跑授权红线**：一切耗 deepxiv 配额 / 真实 LLM 的动作**须 Maria 明确授权具体动作**，**严禁预授权**，全部归集到批次 5。泛泛的"好"不构成授权（MEMORY / 记忆条目「真实 e2e 须明确授权」）。

### 0.4 架构贯穿硬约束（红线，任一任务不得破；来源：架构头部「贯穿硬约束」）

- **不新增 interrupt 种类**（三类封口：planning#1 / dev_loop#2 / user_input#3）；
- **不改编排图**（`core/graph.py` 本 Sprint **零改动**）；
- **不改人在回路三个交互点**；
- **保 S-1 重跑幂等契约**（`_has_committed_result_for_round` guard 函数**一字不改**）；
- **状态契约新增严格限两处两键**：`ExecutionResult.conclusion` + `ReproductionPlan.success_criteria`，**上限就是两处**（架构 Q-S8-02）；
- **护栏 3 只产警示、不阻断审批**（`check_plan` 既有五条 W 的 rule / message / 触发条件**一字不动**，既有两个调用点不改也能跑）；
- **`config.py` 零改动**（新早停常量复用 `NO_METRICS_EARLY_STOP_ROUNDS` 现有取值，架构 Q-S8-04）；
- **`core/react_base.py` 一字不动**（架构 §1.2 已论证：`force_finish` schema 成功分支已把结果同步写成一条 `<result>` 包裹的 AIMessage，回读兜底不需要它改）；
- **`core/tools/code_fs_tools.py` 一字不动**（架构 Q-S8-03 方案 A：验钞函数内联自判，工具层边界不收窄）；
- **`_SandboxRunCollector` dataclass 一字不动**（含 `:812-817` 那段 R-S4-10 注记，架构 §1.1 裁定 1）；
- **反过度工程**（MEMORY §4.1）：**零新模块、零新 Python Enum 类、零"将来可能用得上"的扩展点**。四档档名落盘的字面量**就是**四个中文串，不做"内部枚举 + 展示名"两套（架构 §2.3 / A-S8-05）。

### 0.5 🔴 两层分离总纲（本 Sprint 落地时最容易搞砸的一件事，架构头部 v2.0 总纲 + §2.5.4）

**一句话记法（PRD §4.5.2）**：**PRD 定义「什么算复现成功」这句话的含义；计划定义「对这篇论文来说，满足什么就算达到了那个含义」。**

| 层 | 落在代码的哪里（**物理位置必须分开**） | 跨论文 | 谁能改 |
|---|---|---|---|
| **第一层：四档语义边界** | ①`execution.py` 四个模块级档名常量 + `_LEVELS` 顺序元组；②**系统提示词主体里的四档语义段**（稳定前缀，进提示词哈希基线） | **恒定** | 只有改代码 |
| **第二层：本篇达标线** | `plan["success_criteria"]` → 经 **HumanMessage 动态通道**注入执行上下文（与 coding 上下文） | **每篇不同** | 规划环节写、用户在审核页改 |

**三条可静态断言的红线（架构 §2.5.4，AC-S8-08 的断言对象）**：

1. **四档语义段必须在系统提示词里，达标线必须在 HumanMessage 里，两者不得混在同一段文本。** 混了就是：要么把第一层做成动态的（= 允许计划越权），要么把第二层做成静态的（= 退回硬编码，直接复发病③）。
2. **`_decide_conclusion` 不得读 `success_criteria`。** 达标线是**给 agent 看的判断依据**，不是给代码看的判据——代码只做三条客观封顶。**代码一旦开始解析达标线文本，就是在把第二层重新硬编码回代码里。**
3. **静态可断言**：`success_criteria` 在 `core/` 下的出现点**只允许四处**——`state.py`（声明）、`planning.py`（生产）、`execution._build_execution_agent_context`（注入）、`coding.py` 上下文（注入）。**判定函数体内零出现。**
   > ⚠ 架构 §2.5.4 红线 3 原文写"只允许三处"，但把 `_build_execution_agent_context` 与 `coding.py` 上下文并成了一处 ⇒ 逐文件数实为**四处**（另加批次 3 的 `reporting._render_success_criteria` 一处读点，共五处）。**这是数法差异不是设计差异**，已登记 §15 **P-S8-10**，断言按逐文件的实际处数写。

**为什么 `success_criteria` 是单个字符串而不是"档位→达标线"的字典**（架构 §2.5.2，落地时最容易被"优化"掉的一条）：**四档名根本不出现在计划里 ⇒ 计划在结构上就没有改动第一层的入口**——不是靠提示词去劝它别越权，是**它连能写越权内容的字段都没有**。任何"看起来更灵活"的字典 / 列表形态**一律否决**。

### 0.6 已知 bug 模式对照（每个新增函数 / 新增注入点开工前逐条自查）

| # | 模式 | 本 Sprint 的具体作用面 |
|---|---|---|
| **#1** | ToolMessage 序列化必须是合法 JSON（`json.dumps(..., ensure_ascii=False, sort_keys=True, default=str)`，禁 `str(dict)`） | 本 Sprint **不新造工具**（只绑既有 `read_code_file` / `list_dir`），该模式**无新增作用面**；但 `_resolve_agent_report` 回读 `<result>` 时的 JSON 解析纪律同源（沿 `_rebuild_*_from_messages` 的截断容忍路径，**不自行另写解析**） |
| **#2** | 3 参签名 + 工具历史回填 | **本 Sprint 不适用**：execution 走的是 `result_schema` 结构化出口（S7-13 已建），不是 `_map_xxx_result`。`_resolve_agent_report` 的"子图 result 优先、messages 末条 `<result>` 回读补位"是它的对偶形态，已由架构 §1.3 裁定 |
| **#3 🔴** | **禁止静默吞错** | **本 Sprint 命中三处**：①`_resolve_agent_report` 两通道皆空 / 有标签但解析不出 → **必须 WARNING**（架构 §1.3，且与 `reported_metrics` 的"零指标不打 WARNING"相反——**档位缺失不是合法常态**）；②`_verify_evidence` IO 异常 → 该条判**不成立** + WARNING（AR-S8-03）；③🔴 **`_split_reported_metrics` 的非 str `group` 今天就是静默吞**（BUG-S8-01，见 §1.6） |
| **#4 🔴** | Prompt Cache 字节级幂等不能被动态拼接破坏 | **本 Sprint 命中三处 prompt 主体改写**（coding / planning / execution）：新增文本必须是**纯静态文案**，零 `arxiv_id` / 论文标题 / 路径 / 时间戳；动态内容一律走 HumanMessage（`json.dumps(sort_keys=True)`）。三处各配一条"主体无论文级动态变量"断言（正则 `\d{4}\.\d{4,5}` 零命中） |
| **#5** | 回归验收必须连跑足够次数 | 本 Sprint 的 LLM 服从度类风险（R-S8-02 规划把及格线画低 / R-S8-08 组名服从度 / R-S8-10 服从率约 75%）**只能靠批次 5 真跑观测**，且**单次真跑不构成服从率证据**（sprint7 §8 已明载）。⇒ 交付表述纪律见 §12 第 1 条 |
| **#6** | 改 `__init__.py` 显式 export 时小心遮蔽子模块 | 本 Sprint **不改** `core/nodes/__init__.py`；但一切测试 / 探针**强制** `importlib.import_module`（§0.3 第 7 条） |

### 0.7 文件边界与并发纪律（MEMORY §1.1 / §1.2）

1. **共享文件谁都不许碰**：`app.py`、`docs/TODO.md`、本 dev-plan —— **由主控统一收口**。
2. **单收口窗口**（同一文件被同批多个任务触碰时，任务**串行**、主控收口令，不并行派子代理）：
   - `core/nodes/execution.py`：**批次 1a（3 任务）+ 批次 2（10 任务）+ 批次 3（1 任务）** 共触碰。三批之间是串行的批次边界，批内串行。
   - `core/nodes/reporting.py`：**批次 3 独占**（5 任务串行）。
   - `core/state.py`：🔴 **批次 1a 一次收口，此后全 Sprint 零改动**（见 T-S8-1a-2 的理由）。
3. **跨会话并发**：开工前 `git status --porcelain` **现查留痕**（harness 开头给的快照不可信）。**同一个文件被两个批次同时改 ⇒ 无法按文件粒度分离提交 ⇒ 停手请示**，不要自作主张打包提交。
4. **全量回归与文档收口由主控统一做**，不下放给子代理。

---

## 1. 概述

### 1.1 Sprint 目标

Sprint 8 治的是**一条从头断到尾的输出契约链**，以及**"一套判据套所有论文"这个前提本身**：

- **链路断裂（PRD §0.1）**：计划的 `expected_output` **全仓零判定消费点**；编码环节**从没有人告诉过它 `summary.json` 或 `outputs/` 的存在**（`coding.py` 全文两者零命中，本计划已复核属实）；执行环节在等一个没人被要求生产的文件、且**一个读文件的工具都没绑**；报告靠一条即将失效的扁平字段渲染。
- **判据的三个病（PRD §0.2）**：①全局文档承诺的"与论文报告一致"代码从来没实现过（唯一实现 `_verify_trend` 只做组间比大小）；②全有全无（`all(check == "符合")`，三个实验对上两个与一条没对上评价完全相同）；③标准照着"有多组对照实验的论文"设计，无对照组的论文**结构性永远拿不到最高档**。
- **根因（PRD §0.3，Maria 第四轮拍板）**：以上全部问题的根源是**一套判据套所有论文**。⇒ **成功标准改由论文分析 + 规划针对本篇论文推导出来、写进计划**，四档只作统一表达框架。

**本 Sprint 交付后应当成立的四件事**：

1. **一篇纯定性论文，代码跑通、图画出来，能判复现成功**（AC-S8-12）。
2. **部分复现有名分**：三个实验对上两个，不再与一条没对上同等评价（AC-S8-10）。
3. **同一份执行产物 + 两份不同达标线的计划 → 判出不同档位**（AC-S8-07，本版主断言，直接证明"标准生效了"）。
4. **判断权交给执行环节 agent，代码退居验钞**，并守住三条只压不抬的客观封顶（AC-S8-09）。

### 1.2 范围对齐

- **PRD 权威**：S8-01 ~ S8-11 十一条，**全部 P0、同批交付**（S8-05 是硬约束，批次 2 内部不得拆分）；AC-S8-01 ~ AC-S8-25；十一条"明确不做"（看图判断 / 通用多维容器 / 图片内嵌 / 成功率 KPI / 步骤对账归属 / 命令层新增拦截规则 / 人工确认交互入口 / 治审计盲区 / 教代码绕开审计 / **计划改动四档语义边界** / **护栏 3 做成阻断门**）。
- **架构权威**：Q-S8-01 ~ Q-S8-09 九项裁决全部落地为可执行任务，**本计划不重新决策**。
- **本计划新增（Maria 2026-08-04 已同意，纯 bug 修复不走 PRD）**：**BUG-S8-01**（非 str `group` 静默升格），见 §1.6。
- **新增模块**：**0 个新 `.py` 模块**。新增抽象总量（架构头部已量化，本计划逐条对上）：
  - **2 个状态契约键**（跨 `ReproductionPlan` 与 `ExecutionResult` 两个结构）
  - **1 个 `ErrorCategory` 成员**（`NO_VERIFIABLE_OUTPUT`；`NO_METRICS` 成员**保留**，架构 Q-S8-07）
  - **4 个 execution 侧纯函数**（`_resolve_agent_report` / `_verify_evidence` / `_decide_conclusion` / `_apply_no_verifiable_output`）
  - **2 个 reporting 侧纯函数**（`_render_audit_findings` / `_render_success_criteria`）
    > ⚠ 架构头部写"1 个 reporting 侧纯函数"，但 §5.6(B) 与 §5.7 各裁了一个，逐条数实为 **2 个**。已登记 §15 **P-S8-11**（表述差，非设计差）。
  - **1 条 `check_plan` 警示**（W6）+ **1 组 term_map 换发**
- **breaking 面**：两个新状态键均**缺省安全 + `.get()` 防御读**，旧 checkpoint 不 KeyError（架构 §2.4 / §2.5.5）；`ErrorCategory.NO_METRICS` 保留成员保住旧 checkpoint 反序列化面（架构 §7）。**真正的 breaking 是"档位语义与判据来源双变更 ⇒ 历史结论不可跨版本比较"**（R-S8-11），属产品口径而非数据契约。

### 1.3 关键风险一句话

**批次 2 是全 Sprint 的风险顶点**，且它同时是**可用性恢复点**（§0.0）：十个任务全部落在 `execution.py` 一个文件、全部串行、其中 `_decide_conclusion` + 三条封顶 + `success` 由 `level` 派生这三件事**同时改写成功判定的分子与分母**——而这块区域正是 S7-11「做得越少越容易成功」反向激励刚刚治好的地方（`execution.py:2423-2426` 逐字记着：真跑 17 条挂 5 条判失败、只跑 2 条全 0 反而判成功）。**任一封顶被写漏 / 写成"可以抬高"，S7-11 的反向激励当场复活，且这次连"分子是不是 agent 自报"这道自律门控也一并废止了。** 缓解 = 三条封顶做成**按 `_LEVELS` 元组下标取更低档**（不写 if 链，架构 §2.3，天然满足"只压不抬"）+ AC-S8-09 四向验红 + 批次 2 收口门强制全量回归。

次高风险是 **R-S8-13「两层分离落地时混成一层」**（§0.5）与 **AR-S8-04「一条统一判据长回两套」**：开发极可能顺手给 `_decide_conclusion` 按"数值 / 趋势 / 定性"写三个分支——**那正是病③的复发形态**，AC-S8-08② 的负向静态断言对象就是这个函数。

### 1.4 容量裁剪线（超限时依序执行；**上面的绝不砍**）

> **不可裁的三条**（缺一环整个 Sprint 的立项目标不成立）：批次 1a 的 S8-03（读文件能力，没它整件事无从谈起）、批次 1b 的 `success_criteria` 生产链、批次 2 全部（PRD 明令内部不得拆分）。

1. **先降批次 5 规模**：AC-S8-25（护栏真值）可与 AC-S8-24 合并在**同一次真跑**里取证（同一份计划落盘后既核成功标准是否引用具体主张、又核 W6 是否未命中），省一次配额。
2. **再降批次 3 的展示面**：`_render_success_criteria`（§5.7 报告展示本篇成功标准）与 `ui/pages/result_report.py` 的卡片跟改可分两次落——但 🔴 **`_determine_conclusion` 改名连带的 `result_report.py` import 跟改（P-S8-4）不可延后**，它是**模块导入即崩**级别的连带。
3. 🔴 **coding prompt 字节门（T-S8-1a-3）不可裁、不可顺延**。理由：它是 T-S8-2-1b 的**前置**——“先建后改”一旦变成“改完再建”，建出来的基线是照着改后字节写死的、**永远绿**，与自锁定形态等价（R-S7-41 / sprint7 P-27 两次实证）。**要么在改 prompt 之前建，要么这道门根本不该记进交付。**
4. **绝不裁**：三条封顶（护栏 2）、五重验钞、护栏 1 的审核页展示。护栏 1 是**三道护栏里唯一真正的兜底**（PRD §4.11.2：唯有人眼能判断"这条标准对这篇论文算不算宽"）。

### 1.5 前置事实（本计划落盘时逐条上磁盘 Read / grep 核实，2026-08-04；凡与 PRD / 架构描述有出入的一律进 §15）

> 核实时点：2026-08-04，`git status --porcelain` **为空**（干净工作区），`HEAD = 191bd93`。

| # | 事实 | 核实方式 | 结论 |
|---|---|---|---|
| 1 | `ReproductionPlan` 在 `core/state.py:115-157`，**既有 13 键**（plan_summary / environment / data_preparation / code_strategy / execution_steps / expected_results / estimated_time / deliverables / user_feedback / approved / required_credentials / scale_reduced / local_fit_note） | Read | ✅ 与架构 §12 一致 |
| 2 | `ExecutionResult` 在 `core/state.py:159-184`，**既有 11 键**（success / metrics / logs / errors / artifacts / runtime_seconds / environment_info / step_reconciliation / budget_truncated / metrics_groups / degraded_credentials） | Read + 逐键数 | ❌ 架构 §2.1 写"既有 **10** 键"，**实为 11 键**。见 §15 **P-S8-1** |
| 3 | `core/nodes/coding.py` 内 `summary.json` / `outputs/` / `expected_results` / `success_criteria` **四者全部零命中** | `grep -n` | ✅ PRD §0.1 与 §4.2 第 3 条属实 |
| 4 | execution 工具列 `:1581-1585` 恰三个工具（`prepare_environment` / `run_in_sandbox` / `request_user_input`） | Read | ✅ 属实（架构写 `:1581-1584`，列表右括号在 `:1585`） |
| 5 | `coding.py:515-516` 今天就绑了 `make_read_code_file_tool()` + `make_list_dir_tool()` | Read | ✅ PRD §4.3「读取面不是本次新开的」属实 |
| 6 | `_parse_metrics(run_results, plan, state)` 的 `plan` 形参**函数体一次没用过**（`:517-550` 全文只用 `run_results` 与 `state`） | Read | ✅ PRD §4.2 第 5 条属实 |
| 7 | `_parse_metrics` 的第二返回值 `llm_calls_used` 流向 `_map_execution_result(..., llm_calls_used=...)`（`:3016`）→ 预算扣减 `total_calls = react_rounds_used + llm_calls_used`（`:2533`） | grep 全 8 处出现点 | ⚠ **架构 §12 未提**：解绑三档后这条预算支路要显式归零，见 T-S8-2-1 与 §15 **P-S8-6** |
| 8 | `_split_reported_metrics` docstring `:1796-1797` 写"先到先得"，**真正的代码在 `:1828-1831`** | Read | ✅ 架构 AR-S8-08 引的行号是 docstring，属实；改策略时**两处都要动** |
| 9 | 🔴 `execution.py:1826` `group = str(raw_group).strip() if isinstance(raw_group, str) else ""` —— 非 str `group` **静默**吞成空串 ⇒ 归主实验桶 | Read | ❌ **PRD 与架构全文零登记**（`非 str` / `畸形` / `F6` 三词全文零命中）。⇒ **BUG-S8-01**，见 §1.6 |
| 10 | 同函数 docstring `:1798-1799` 明写「畸形条目（非 dict / 无 name / 值非标量）跳过并**打 WARNING**（已知 bug 模式 #3：禁止静默吞错）」 | Read | ✅ **说明书与实现自相矛盾**：非 dict / 无 name / 值非标量三种确实打了 WARNING，**唯独 group 类型不对是静默吞** |
| 11 | `success` 三合取项在 `:2428-2432`；S7-11 反向激励注记在 `:2423-2426` | Read | ✅ 与架构 §12 一致 |
| 12 | S7-13 自律门控在 `:2939-2952`；`_collect_grouped_metrics` 兜底在 `:2961` | Read | ✅ 与 PRD §4.5.5 留档 2 / 架构 §13 一致 |
| 13 | `ui/` 全目录对 `metrics_groups` **零命中** | `grep -rn metrics_groups ui/` | ✅ PRD §4.7 第 2 条属实 |
| 14 | 🔴 `humanize("report_form", ...)` 与 `humanize("conclusion_level", ...)` **全仓零调用点** | `grep -rn` 全仓（排除 `.venv`） | ❌ **实质性勘误**：改 `ui/term_map.py:80-86` **用户看到的一个字都不会变**。真正的文案在别处三处。见 §15 **P-S8-3** |
| 15 | 🔴 `ui/pages/result_report.py:59` `from core.nodes.reporting import _determine_conclusion, _determine_report_form` | Read | ❌ **实质性勘误**：架构 §5.2 要把 `_determine_conclusion` 改名，`ui/` 条目**没提这个 import** ⇒ 改名后结果页**模块导入即崩**。见 §15 **P-S8-4** |
| 16 | `ui/pages/result_report.py:121-135` `_conclusion_card_key` 按 `level == "science"` 分卡 | Read | ⚠ 四档制下该分支**恒不成立** ⇒ 所有 full_success 都印 `full_success_engineering` 卡。架构未提。见 §15 **P-S8-4** |
| 17 | 🔴 编码侧 prompt **今天没有任何字节门**：全仓 `hexdigest` 命中的**真 prompt 门只有 3 处**（planning 1 = `test_sprint6_b1_prompt_guards.py:79`；execution 2 = `test_sprint5_t14_execution_prompt.py` + `test_sprint7_s710_exec_locality.py`），**coding 零**；唯一沾边的 `tests/test_sprint5_t13_coding_prompt.py:180-183` 是 `expected_prefix = _CODING_SYSTEM_PROMPT_BODY + _CODING_HONESTY_SECTION` 的**自锁定形态**（等号两边同源，常量改成什么都恒绿） | grep 全仓 + Read（复核 sprint7 P-64 与 `docs/TODO.md:633`） | ❌ AC-S8-21② 说“编码侧提示词哈希基线**换发**”，**实为新建**。见 §15 **P-S8-5**、任务 **T-S8-1a-3** |
| 18 | `tests/test_s708_user_text_guard.py`：`EXPECTED_TERM_LABELS_N = 43` / `EXPECTED_CONSTANTS_N = 15` / `EXPECTED_N = 58`，三个数**必须 `==`，禁改 `>=`** | Read `:95-133` | ✅ AC-S8-20⑤"计数精确闭合、禁止放宽"有具体落点 |
| 19 | `ui/term_map.py::TERM_LABELS` 实测 **43 条**（error_category 14 / node 7 / annotation 4 / code_strategy 3 / resource_strategy 3 / report_form 3 / conclusion_level 3 / audit_rule 3 / user_fix_decision 3） | `.venv/bin/python` 实算 | ✅ 与守门 `EXPECTED_TERM_LABELS_N` 对平 |
| 20 | 函数源码字节冻结表在 `tests/test_sprint7_s713_reported_metrics.py:994-1005`，**恰 10 个函数** | Read | ✅ PRD §4.2 引的落点属实；必红行数见 §15 **P-S8-9** |
| 21 | `check_plan` 现签名 `(plan, resource_info)`（`core/plan_checks.py:483`）；调用点共 **19 处**（生产 1：`ui/pages/plan_review.py:786`；测试 18：全在 `tests/test_sprint6_b1_plan_checks.py`），**全部两参** | grep 全仓 | ✅ 架构 Q-S8-09 方案 A（加带默认值的第三个关键字形参）**既有调用零改动**成立 |
| 22 | 🔴 `ui/pages/plan_review.py` **没有任何计划字段的就地编辑控件**：`revise` 一次性文本框已于 S2-12 迁到多轮对话面板，用户"调整计划"走的是**讨论助手 → 修改方向纪要 → 重新规划**这条路 | Read + grep | ⚠ 与 AC-S8-14「可编辑」的字面读法有张力。架构 §12 已裁"**只读展示**"。✅ **Maria 2026-08-04 拍板 1 已确认按只读展示验收**，见 §16.A |
| 23 | `SandboxRunResult`（`sandbox/local_venv.py:176-189`）只有 `duration_seconds`，**无任何挂钟时间戳** | Read | ✅ PRD §4.9.6「mtime 比对数据源不存在」属实 |
| 24 | `core/react_base.py:665-672` `force_finish` 的 schema 成功分支**确实把结果同步追加成一条 `<result>` 包裹的 AIMessage** | Read | ✅ 架构 §1.2 (c) 的"另一半好消息"属实 ⇒ 回读兜底不需要改 `react_base` |
| 25 | `REACT_RESULT_TAG_OPEN/CLOSE` 在 `config.py:61-62`；`react_base._RESULT_TAG_PATTERN`（`:87`）是私有符号 | Read | ✅ 架构 §1.3「execution 侧按同一对常量自建模块级 pattern，不 import 私有符号」可执行 |
| 26 | `TOOL_RESULT_MAX_LENGTH = 8000`（`config.py:63`），`code_fs_tools._truncate`（`:57-64`）读它 | Read | ✅ R-S8-09 的 8000 截断属实（**登记不治**） |
| 27 | `_build_execution_agent_context` 现签名恰 **3 个形参**（state / work_dir / plan） | Read `:1299-1303` | ✅ 架构 §5.6(A)「审计结果作为第 4 个入参」可执行 |
| 28 | `_INCOMPLETE_EXECUTION_SUMMARY_LEAD`（`:2180`）/ `_INCOMPLETE_EXECUTION_FIX_HINT`（`:2181`）已是模块级具名常量**且已进术语守门** | Read + 守门表 `:113-114` | ✅ 新错误类别的文案按同款范式提常量 + 进守门 |
| 29 | ⚠ `_NO_METRICS_EARLY_STOP_SUMMARY`（`:2715-2718`）**不在** `_GUARDED_CONSTANTS` 里 | 比对守门表 `:95-115` | ⚠ 它是直达用户的终态面板文案却从未被守门扫到（与 sprint7 P-13 / S7-11 `_SUCCESS_CRITERIA_NOTE` 同款失效模式）。本批要改它 ⇒ **顺手补进守门**。见 §15 **P-S8-8** |
| 30 | `reporting._header`（`:522`）打印 `` - 报告形态: `{form}` `` —— **直接把内部键印给用户** | Read | ⚠ MEMORY §4.2 的**既有违反**，架构未提。见 §15 **P-S8-3** |

### 1.6 🔴 BUG-S8-01：畸形字段被静默吞（本计划新登记，纯 bug 修复；**v1.3 已重判并换发落点**）

> **📌 v1.3 重判结论（上磁盘重新判定，不是想当然作废）**：
> - **原始缺陷的原址消失**：`execution.py:1826` 位于 `_split_reported_metrics` 内，该函数随 S8-06 回炉**整体删除**（T-S8-2-10）⇒ 那一行不复存在，`group` 这个概念本身也不复存在。
> - 🔴 **但缺陷类别转移了，而且转移后更危险**：新的 `_collect_result_blocks` 对 agent 汇报的 `title` / `note` / `cell` 做同款处理，而**架构 §16.5① 只写了"过 `mask_value`"、没说非 str 怎么办**。实测 `mask_value(非 str)`：**无凭证时静默原样返回（脱敏被跳过）、有凭证时抛 `AttributeError`（炸节点）** ⇒ **测试环境全绿、真跑当场炸**。详见 §15 **P-S8-12**。
> - **⇒ DA-S8-1 换发（不注销）**：由「非 str `group` 跳过 + WARNING」换发为「**非 str 的 `title` / `note` / `cell` 必须先确定性转字符串再过脱敏，容器形态置占位符不做 `str()` 强转，畸形一律 WARNING**」，落点 **T-S8-2-10b / CP-2.10b-7**。
> - **下方原文保留供追溯，不删。**

**来源**：sprint7 测试工程师在 S7-13 独立验收中登记的 **F6**（`docs/TODO.md:849`，原话：「`group` 为非 str 时静默归入主实验桶 ⇒ 等于"畸形 `group` 把指标偷偷升格为 `success` 分子的候选"」）。**主控 2026-08-04 上磁盘核实、本计划落盘时复核属实。**

**缺陷本体**（`core/nodes/execution.py:1826`）：

```python
raw_group = item.get("group")
group = str(raw_group).strip() if isinstance(raw_group, str) else ""
```

agent 把 `group` 填成**数字 / 数组 / dict** 时，`isinstance(raw_group, str)` 为假 ⇒ 落 `""` ⇒ 而 `""` 在本函数里的含义正是「**这是主实验指标**」（docstring `:1789` 逐字写着"`group` 缺省 / null / 去空白后为空 ⇒ **主实验指标**"）⇒ **该指标被悄悄归进主实验桶**，全程零日志。

**为什么它是缺陷而不是设计**（三条，缺一条都不足以定性）：

1. **同函数 docstring `:1798-1799` 明写**「畸形条目（非 dict / 无 name / 值非标量）跳过并**打 WARNING**（已知 bug 模式 #3：禁止静默吞错）」——非 dict、无 name、值非标量三种**确实都打了 WARNING**（`:1811` / `:1817` / `:1823` 三处 `skipped.append`），**唯独 group 类型不对是静默吞** ⇒ **说明书与实现自相矛盾**。
2. **静默吞的方向是"升格"不是"降格"**：吞成 `""` 等于把一条来路不明的指标塞进主实验桶——sp8 之前它是 `len(metrics) >= 1` 这个成功合取项的分子候选，sp8 之后主实验桶是**判档位的证据之一**。⇒ **危害变形但不消失。**
3. **PRD 与架构全文零登记**：本计划落盘时 grep sp8 两份文档，`非 str` / `畸形` / `F6` 三词**零命中** ⇒ 确认未被接住。

**定性**：**纯 bug 修复**（MEMORY §3.1：纯 bug 修复可直接改，不走 PRD）。Maria 2026-08-04 已同意。

**落点**：与架构 AR-S8-08 要改的是**同一个函数的同一段代码**（`_split_reported_metrics` 的撞名策略改为"值不同则两条都丢弃 + WARNING"）⇒ **顺手带上，不单开任务**，落在 **T-S8-2-10**。

**修法**（最小改动，不引入新分支形态）：把 `else ""` 这条静默路径拆成两条——`raw_group is None` **仍归主实验**（`null` 是合法的"没有组"，docstring 已写明）；`raw_group` 非 None 且非 str ⇒ **该条目跳过 + WARNING**（与既有三种畸形同款处置，走同一个 `skipped` 列表）。

> ⚠ **不要把它改成 `str(raw_group)` 强转**：那会把 `{"a":1}` 变成组名 `"{'a': 1}"`，制造一个 Python repr 形态的组名进报告——踩已知 bug 模式 #1 的同族坑，且 MEMORY §4.2 禁止内部表示裸露给用户。

**验收点**：**DA-S8-1**（不占用 `AC-S8-*` 号段）—— `group` 为 `int` / `list` / `dict` 三形态时该条目**不进主实验桶、不进任何分组桶、且打 WARNING**；`group` 为 `None` / 缺键 / 全空白字符串三形态**仍归主实验桶**（既有行为一字不变）。

---

## 2. 任务清单总表

| 任务编号 | 承载 | 任务名 | 产出文件 | 依赖前置 | 复杂度 | 风险 |
|---|---|---|---|---|---|---|
| **T-S8-1a-1** | 前置 | 实现前核实 + 回归基线重测落档（**零生产改动**） | `/tmp` 自测脚本 + §15 勘误回填 | 无 | 低 | 低 |
| **T-S8-1a-2** | Q-S8-02 | **`core/state.py` 两键一次收口** + `:170` 注释订正 | `core/state.py` | T-S8-1a-1 | 低 | 低 |
| **T-S8-1a-3** | AC-S8-21② | 🔴 **新建** coding 侧 system prompt 字节哈希门（**先建后改，零生产改动**） | `tests/`（新门）+ §15.1 留档 | T-S8-1a-1 | 中 | 中（P-S8-5：AC 写“换发”实为新建；自锁定坑 R-S7-41） |
| **T-S8-1a-4** | S8-03 | execution 只读工具接入（`read_code_file` / `list_dir`）—— **只加工具，判定链路一字不动** | `core/nodes/execution.py` | T-S8-1a-2 | 低 | 中（两个闸不许合并，Q-S8-03） |
| **T-S8-1a-5** | 收口 | 既有断言同步 + 批次 1a 自测收口门（**要求全绿**） | `tests/`（同步面） | T-S8-1a-3 + T-S8-1a-4 | 中 | 中 |
| **T-S8-1b-1** | 前置 | 实现前核实（planning 两处构造点 / W1~W5 行为基线 / plan_review payload 键） | `/tmp` 自测脚本 | 无（可与 1a 并行） | 低 | 低 |
| **T-S8-1b-2** | S8-01 | planning 冻结区改写 + schema `success_criteria` 进 `required` + 两处构造点 + 哈希三件套 | `core/nodes/planning.py` | T-S8-1b-1 + T-S8-1a-2 | **高** | **高**（两层分离命门 + 冻结区 + required 破例） |
| **T-S8-1b-3** | S8-11 / Q-S8-09 | `plan_checks` W6 + 带默认值的第三个关键字形参 | `core/plan_checks.py` | T-S8-1b-1 | 中 | 中（零改动红线再解锁，范围严格限两项） |
| **T-S8-1b-4** | S8-11 | 审核页护栏 1 只读展示 + W6 展示通道多传一参 | `ui/pages/plan_review.py` | T-S8-1b-2/3 | 中 | 中（"可编辑"口径 ✅ 已由拍板 1 裁定为只读展示，§16.A） |
| **T-S8-1b-5** | 收口 | 既有断言同步 + 批次 1b 自测收口门 | `tests/`（同步面） | T-S8-1b-2/3/4 | 中 | 中 |
| **T-S8-2-1** | S8-02 | 🔴 三档判定链路解绑 + `_parse_metrics` 死参 + `llm_calls_used` 归零 —— **★不可用窗口起点**（拍板 2 由批次 1a 迁入） | `core/nodes/execution.py` | **批次 1a + 1b 全部收口** | 中 | **高**（AR-S8-01 窗口；回归成片变红） |
| **T-S8-2-1b** | S8-02 编码侧 | coding 侧：三处 `<METRICS>` 清除 + 产出约定 + 上下文补两键 + 字节门基线换发（**v1.2 由 1a 迁入**） | `core/nodes/coding.py` | **T-S8-2-1** + T-S8-1a-3（门已在 1a 建好）+ T-S8-1a-2 | 中 | 中（首次改 coding 冻结区） |
| **T-S8-2-2** | 前置 | 实现前核实 + **三处 prompt 旧哈希先记后改** | `/tmp` + §15 回填 | T-S8-2-1b | 低 | 低（不做等于门白建） |
| **T-S8-2-3** | S8-05 | 四档名模块常量 + `_LEVELS` 顺序元组 + `ErrorCategory.NO_VERIFIABLE_OUTPUT`（`NO_METRICS` 保留加注释） | `core/nodes/execution.py` | T-S8-2-2 | 低 | 中（Q-S8-07：删枚举成员会炸旧 checkpoint） |
| **T-S8-2-4** | Q-S8-01 | `_resolve_agent_report` + `ExecAgentOutput.report` + `reported_metrics` 改从 `report` 取 | `core/nodes/execution.py` | T-S8-2-3 | 中 | **高**（跨中断保真，Q-S8-01 最硬） |
| **T-S8-2-5** | S8-04 | `_verify_evidence` 五重验钞（内联自判，工具层一字不动） | `core/nodes/execution.py` | T-S8-2-4 | **高** | **高**（AC-S8-06 命门须验红） |
| **T-S8-2-6** | S8-05 | `_decide_conclusion` + 三条封顶（按 `_LEVELS` 下标取更低档，**不写 if 链**） | `core/nodes/execution.py` | T-S8-2-5 | **高** | **高**（AR-S8-04 长回两套 / 只压不抬） |
| **T-S8-2-7** | Q-S8-04 | `_apply_no_verifiable_output` + 删 `_apply_no_metrics` + `_no_progress_stalled` + 早停文案换发 | `core/nodes/execution.py` | T-S8-2-6 | 中 | 中（29 + 10 处既有断言） |
| **T-S8-2-8** | S8-05 | `_build_execution_result` +`conclusion` 形参 + `success` 由 `level` 派生 + 降级构造点补默认 | `core/nodes/execution.py` | T-S8-2-7 | **高** | **高**（改动最深，S7-11 反向激励复活风险） |
| **T-S8-2-8b** | S8-10 + 前置① | execution 上下文补 `baseline_results` + `success_criteria`（非空才注入）—— **v1.2 由 1a 迁入，须早于 2-9** | `core/nodes/execution.py` | **T-S8-2-8** | 中 | 中（字节零扰动是命门；AC-S8-15③ 落点） |
| **T-S8-2-9** | S8-04/05 | execution 冻结区 prompt 改写（判定纪律 + 输出要求）+ `EXECUTION_OUTPUT_SCHEMA` +3 字段 + 哈希三件套 | `core/nodes/execution.py` | T-S8-2-8 | **高** | **高**（两层分离第一层的物理落点；AR-S8-07 不进 required） |
| **T-S8-2-10** | S8-06 | 🔴 **四个折叠 / 扫盘函数整体删除**（`_split_reported_metrics` / `_coerce_reported_value` / `_collect_grouped_metrics` / `_GROUP_METRIC_STR_MAX_LEN` + `reported_metrics` 字段）+ **BUG-S8-01 重判留痕**（v1.3 整条改判） | `core/nodes/execution.py` | T-S8-2-9 | 中 | 中（R-S8-20 能力回退如实登记） |
| **T-S8-2-10b** | S8-06 | 🔴 **新增 `_collect_result_blocks` + 四个上限常量**（脱敏 / 长度 / 对齐 / 上限 / 引用 / 不合法 六道处置）+ **DA-S8-1 换发落点**（v1.3 新增） | `core/nodes/execution.py` | T-S8-2-10 + T-S8-2-5 | **高** | **高**（P-S8-12 非 str 环境相关坑） |
| **T-S8-2-11** | S8-04/05/06 | 节点主体接线（插入步骤 4.75 / 4.8）+ **S7-13 自律门控废止** —— ★可用性恢复点 | `core/nodes/execution.py` | T-S8-2-10 | **高** | **高**（幂等纪律③ / 步骤顺序即优先级） |
| **T-S8-2-12** | 收口 | 既有断言同步 + 冻结表逐行换发 + 批次 2 自测收口门 | `tests/`（同步面） | T-S8-2-3 ~ 2-10 | **高** | **高** |
| **T-S8-3-1** | 前置 | 实现前核实 + 术语守门账目预清点（43/15/58 → 目标值） | `/tmp` + §15 回填 | 批次 2 | 低 | 低 |
| **T-S8-3-2** | S8-08 | reporting 四个函数删除 + `_verify_expected_results` 退化 + 三态字面量换发 | `core/nodes/reporting.py` | T-S8-3-1 | 中 | 中 |
| **T-S8-3-3** | S8-07 | `_determine_conclusion` → `_assemble_conclusion` + 判定段删除 + audit 析取删除 + 🔴 **`result_report.py` import 跟改** | `core/nodes/reporting.py` + `ui/pages/result_report.py` | T-S8-3-2 | **高** | **高**（P-S8-4：不跟改则结果页导入即崩） |
| **T-S8-3-4** | S8-09 | `_render_audit_findings` 独立成节 + hits 表搬出 + 导语改写 | `core/nodes/reporting.py` | T-S8-3-3 | 中 | 中（文案不得暗示作弊） |
| **T-S8-3-5** | S8-07 | `_render_success_criteria`（原文照登，位置紧接档位之后） | `core/nodes/reporting.py` | T-S8-3-3 | 低 | 低 |
| **T-S8-3-6** | S8-07/08 | `_render_goal_checks` icons + 文案 / `_render_metrics_comparison` 组名说明 + 复现侧无数据不渲染主表 / `_SUCCESS_CRITERIA_NOTE` 换发 | `core/nodes/reporting.py` | T-S8-3-3 | 中 | 中 |
| **T-S8-3-6b** | S8-07 | 🔴 **新增 `_render_result_blocks` + 两处调用点**（`full_success` 原位 + **`degraded` 新增**）+ 核验边界导语（v1.3 新增） | `core/nodes/reporting.py` | T-S8-3-2 | 中 | **高**（§5.8 degraded 真缺口；AR-S8-09 导语不得软化） |
| **T-S8-3-7** | S8-09 | execution 侧审计注入（`audit_code_dir` + `code_audit_findings` + directive 常量 + 异常兜底） | `core/nodes/execution.py` | T-S8-3-1 | 中 | 中（字节零扰动 + 不阻断执行） |
| **T-S8-3-8** | Q-S8-08 + **Maria 拍板 2** | `term_map` 四档换发 + **形态文案降级为纯结构描述（三落点）** + `error_category` +1 | `ui/term_map.py` + `core/nodes/reporting.py` | T-S8-3-3/6 | 中 | **高**（P-S8-3：只改 term_map 等于什么都没改） |
| **T-S8-3-9** | S8-07 | 🔴 **界面结果页按结果块渲染**（`_metric_comparison_rows` 整体替换 + `_render_metrics_section` 逐块 + 空文案换发）—— **v1.3 整条作废并换发** | `ui/pages/result_report.py` | T-S8-3-3/3-6b/3-8 | 中 | 中（P-S8-4 下半 + 旧任务照做会重建旧格子） |
| **T-S8-3-10** | 收口 | 术语守门账目对平 + 既有断言同步 + 批次 3 自测收口门 | `tests/`（同步面） | T-S8-3-2 ~ 3-9 | 中 | 中 |
| **T-S8-4-1** | AC-S8-23 | 全量回归修断言 + 增减账清单逐条对平 | `tests/` | 批次 1a/1b/2/3 | **高** | **高** |
| **T-S8-4-2** | AC-S8-21 | **三类基线账目显式对平**（函数字节冻结表 / 三处 prompt 哈希 / 术语守门计数） | `tests/` + 本 dev-plan §15 留档 | T-S8-4-1 | 中 | 中（红线：禁整表删除 / 禁改"不少于"） |
| **T-S8-4-3** | AC-S8-22 | AC 覆盖矩阵审计 + **五条留档文字核对** + handoff 交接文档 | 交接文档 | T-S8-4-2 | 中 | 低 |
| **T-S8-5-1** | AC-S8-24 | ⚠ **端到端真跑取证**（须 Maria 单独授权具体动作，严禁预授权） | `docs/sprint8/test-reports/` | 批次 4 | 中 | 中 |
| **T-S8-5-2** | AC-S8-25 | ⚠ **护栏真值核对**（计划全文落盘后人工核对 + W6 未命中） | 同上 | T-S8-5-1 | 低 | 低 |

**任务总数**：**41 个**（1a×5 + 1b×5 + 2×**15** + 3×**11** + 4×3 + 5×2）
**批次数**：**6 个**（1a / 1b / 2 / 3 / 4 / 5）
**检查点总数**：**328 个**（分布见 §13 CP 索引。逐批小计：1a=32 / 1b=41 / 2=144 / 3=83 / 4=16 / 5=12）
**落点勘误**：**15 条**（§15；v1.0~v1.2 的 P-S8-1~11 + **v1.3 新增 P-S8-12 ~ P-S8-15**。实质性 5 条：P-S8-3 / P-S8-4 / P-S8-5 / P-S8-9 / 🔴 **P-S8-12（架构未覆盖）**）
**待确认项**：**4 项**（§16，均**不阻塞批次 1a 开工**）

---

## 3. 批次划分与依赖图

### 3.1 批次总览（= PRD §10 拆分 + 架构 §11 两条前置约束）

| 批次 | 名称 | 任务 | 前置条件 | AC 映射 | 特殊标注 |
|---|---|---|---|---|---|
| **1a** | 🔴 **只加不改行为** | T-S8-1a-1 → 1a-5（**串行**，**5 个任务**） | Q-S8-06 已裁 + Q-S8-02 字段名已裁（架构 §2.5 ⇒ 依赖即刻解除） | AC-S8-05/21② | 🔴 **五个任务全部「加了但不改变行为」** ⇒ **落盘后系统行为与今天完全一致、可真跑可演示**；🔴 **T-S8-1a-3（建门）必须早于 T-S8-2-1b（改词）——该次序跨批次成立**；**收口门要求全绿**；`core/state.py` **本批一次收口，此后全 Sprint 零改动** |
| **1b** | 计划侧成功标准 + 三道护栏 | T-S8-1b-1 → 1b-5（**串行**） | Q-S8-02 §2.5 已裁 + Q-S8-09 §15 已裁；`state.py` 两键须 T-S8-1a-2 先落 | AC-S8-01/02/13/14 | **可与 1a 并行开工**（文件边界不重叠：1b 只碰 planning / plan_checks / plan_review），但 **T-S8-1b-2 须等 T-S8-1a-2 的 state 键落盘** |
| **2** | 🔴 **一切会改变行为的事**：通道退场（执行侧删函数 + 编码侧改词）+ 上下文注入 + 验钞 + 四档判定 + 维度不坍缩 | T-S8-2-1 → 2-12（**串行**，含 `2-1b` / `2-8b`，**14 个任务**） | 批次 1a + 1b 全部收口 | **AC-S8-03/04/15** + AC-S8-06/07/08/09/10/11/12/16/18/19/22 | 🔴 **PRD 明令内部不得拆分**（R-S8-06），**14 个任务是一个交付单元**；🔴 **不可用窗口整体落在本批内部**：T-S8-2-1 起、**T-S8-2-11 是可用性恢复点**，**窗口不因迁入两个任务而变长**；🔴 **T-S8-2-1（删通道）与 T-S8-2-8（装新判据）是天然配对，中间不得留缝** |
| **3** | 报告与结果页 + 三态回验 + 审计改证据 | T-S8-3-1 → 3-10（`reporting.py` 独占 5 任务串行） | 批次 2 收口（`conclusion` 已能落盘） | AC-S8-16/17/20 | Q-S8-08 文案连带面**单列任务**（架构 §8 明令"不许挂在别的任务下顺手做"） |
| **4** | 全量回归 + 三类基线账目对平 | T-S8-4-1 → 4-3 | 批次 1a/1b/2/3 全部 | AC-S8-21/22/23 全覆盖 | 账目**红线**：禁整表删除、禁改"不少于" |
| **5** | 真跑取证 | T-S8-5-1 → 5-2 | 批次 4 收口 | AC-S8-24/25 | ⚠ **须 Maria 单独授权具体动作，严禁预授权**；耗 deepxiv 配额 |

### 3.2 依赖关系图

```mermaid
graph TD
    subgraph B1a["批次 1a · 只加不改行为"]
        A1[T-S8-1a-1 实现前核实 + 基线重测]
        A2[T-S8-1a-2 state 两键一次收口]
        AG["T-S8-1a-3 ★新建 coding 字节门（先建后改）"]
        A4[T-S8-1a-4 execution 只读工具接入]
        A7[T-S8-1a-5 断言同步 + 1a 收口门（要求全绿）]
    end
    subgraph B1b["批次 1b · 计划侧成功标准 + 三道护栏"]
        B1[T-S8-1b-1 实现前核实]
        B2[T-S8-1b-2 planning 冻结区 + schema + 哈希三件套]
        B3[T-S8-1b-3 plan_checks W6 + 关键字形参]
        B4[T-S8-1b-4 审核页护栏 1 + W6 接线]
        B5[T-S8-1b-5 断言同步 + 1b 收口门]
    end
    subgraph B2["批次 2 · 通道退场 + 验钞 + 四档判定（不得拆分）"]
        C0["T-S8-2-1 ★四函数整体删除（★窗口起点）"]
        CB["T-S8-2-1b coding 改词（v1.2 迁入）"]
        C1[T-S8-2-2 核实 + 旧哈希先记]
        C2[T-S8-2-3 档名常量 + 新错误类别]
        C3[T-S8-2-4 _resolve_agent_report]
        C4[T-S8-2-5 _verify_evidence 五重验钞]
        C5[T-S8-2-6 _decide_conclusion + 三条封顶]
        C6[T-S8-2-7 新改判 + 删 _apply_no_metrics + 早停]
        C7[T-S8-2-8 _build_execution_result + success 派生]
        CI["T-S8-2-8b 上下文注入（v1.2 迁入·须早于 2-9）"]
        C8[T-S8-2-9 prompt 改写 + schema +3 + 哈希三件套]
        C9["T-S8-2-10 撞名策略 + BUG-S8-01"]
        C10["T-S8-2-11 ★节点接线（可用性恢复）"]
        C11[T-S8-2-12 断言同步 + 2 收口门]
    end
    subgraph B3["批次 3 · 报告与结果页"]
        D1[T-S8-3-1 核实 + 守门账目预清点]
        D2[T-S8-3-2 四删 + 退化 + 三态字面量]
        D3["T-S8-3-3 改名 + 判定段删 + result_report import 跟改"]
        D4[T-S8-3-4 _render_audit_findings 独立成节]
        D5[T-S8-3-5 _render_success_criteria]
        D6[T-S8-3-6 回验/对比表文案与渲染]
        D7[T-S8-3-7 execution 侧审计注入]
        D8["T-S8-3-8 term_map 四档 + 形态文案降级（三落点）"]
        D9[T-S8-3-9 result_report 数据源 + 卡片]
        D10[T-S8-3-10 守门账目对平 + 3 收口门]
    end
    E1[T-S8-4-1 全量回归 + 增减账]
    E2[T-S8-4-2 三类基线账目对平]
    E3[T-S8-4-3 AC 矩阵 + 留档核对 + handoff]
    F1["T-S8-5-1 ⚠端到端真跑（Maria 授权点）"]
    F2["T-S8-5-2 ⚠护栏真值核对"]

    A1 --> AG --> A7
    A1 --> A2 --> A4 --> A7
    B1 --> B2
    A2 --> B2
    B1 --> B3
    B2 --> B4
    B3 --> B4
    B4 --> B5
    A7 --> C0
    B5 --> C0
    C0 --> CB --> C1 --> C2 --> C3 --> C4 --> C5 --> C6 --> C7 --> CI --> C8 --> C9 --> C10 --> C11
    C11 --> D1
    D1 --> D2 --> D3
    D3 --> D4
    D3 --> D5
    D3 --> D6
    D1 --> D7
    D3 --> D8
    D6 --> D8
    D8 --> D9
    D4 --> D10
    D5 --> D10
    D7 --> D10
    D9 --> D10
    D10 --> E1 --> E2 --> E3 --> F1 --> F2
```

**关键路径**：`T-S8-1a-2`（state 两键）→ 批次 1a 收口（**此时系统仍可用**）→ `T-S8-2-1`（解绑，**不可用窗口起点**）→ 批次 2 **十四任务**串行 → `T-S8-2-11`（**可用性恢复**）→ `T-S8-2-12`（批次 2 收口）→ 批次 3 → 批次 4 → 批次 5。

**可并行的两处**（且**仅此两处**）：

1. **批次 1a 与批次 1b 可并行**（文件边界零重叠：1a 碰 `state.py` / `coding.py` / `execution.py`；1b 碰 `planning.py` / `plan_checks.py` / `plan_review.py`）。唯一的交点是 **T-S8-1b-2 依赖 T-S8-1a-2 的 `success_criteria` 键声明**——因此 **T-S8-1a-2 必须最先落**。
2. **批次 3 内 T-S8-3-7（execution 侧审计注入）与 T-S8-3-2~3-6（reporting 侧）可并行**——但两者都要在 T-S8-3-10 前汇合。**若派子代理并行，必须遵守 §0.7 的单收口窗口令**。

**为什么批次 2 十四个任务全部串行**：主体全部落在 `core/nodes/execution.py` 一个文件（`T-S8-2-1b` 落在 `coding.py`，但它在次序上依赖 `T-S8-2-1`），且后一个任务的输入是前一个任务的产物（**删通道 → 编码侧改词 → 档名常量 → 判定函数 → 改判 → 结果构造 → 上下文注入 → prompt → 接线**）。sprint7 批次 1 的教训（§5 单收口窗口令）在这里加倍成立。

🔴 **T-S8-2-1 与 T-S8-2-8 是天然配对，中间不得留缝**：前者**撤掉旧判据的分子**（`<METRICS>` 三档解绑 ⇒ `metrics` 恒空），后者**装上新判据**（`success` 改由 `level` 派生）。**只做前者不做后者 = 系统一律判失败；只做后者不做前者 = 旧通道还在教 agent 写标签、两套判据并存。** 这正是 PRD §4.5.6「禁止拆批」与 **R-S8-06** 在批次 2 内部的具体形态 —— 🔴 **批次 2 本就受"内部不得拆分"约束，本次迁入一个任务不改变这条约束，只是让它多管一个任务。**

### 3.3 🔴 中间态期间的预期变红面（AR-S8-01 的可执行形态，**不得当成回归**）

> 🔴 **本表整体落在批次 2 内部**（Maria 2026-08-04 拍板 2 之后）。**批次 1a 期间不产生本表任何一族的红。**

**批次 2 内部**合入 T-S8-2-1 后、T-S8-2-8 完成前，以下用例族**会成片变红，这是计划内的**：

| 断言族 | 为什么变红 | 何时复绿 |
|---|---|---|
| 任何断言 `execution_result["success"] is True` 的用例 | `metrics` 恒空 ⇒ `len(metrics) >= 1` 恒假 | **T-S8-2-8**（`success` 改由 `level` 派生） |
| 任何断言 `metrics` 非空的用例（喂 `<METRICS>` stdout fixture 的） | 三档已解绑，stdout 不再进 `metrics` | T-S8-2-11（agent 自报无门控直通） |
| `_apply_no_metrics` 相关（29 处 / 5 文件） | 解绑后条件更容易命中；随后函数被删 | **T-S8-2-7 / T-S8-2-12** |
| `_no_metrics_stalled` 相关（10 处 / 2 文件） | 改名 `_no_progress_stalled` + 类别换 | **T-S8-2-7 / T-S8-2-12** |
| 冻结表 `_parse_metrics` 一行 | 死参数被清 ⇒ 源码字节变 | **T-S8-2-12**（逐行换发 + 写明原因） |

⇒ 🔴 **本表的红全部发生在批次 2 内部**（拍板 2 之后）。因此：

- ✅ **批次 1a 收口门：要求全量回归零失败。** ⚠ 本计划 v1.0 曾把 1a 定为"本 Sprint 唯一一个允许带红收口的门"，**该表述随拍板 2 整条作废**；
- **批次 2 的中途**（T-S8-2-1 合入后到 T-S8-2-8 完成前）允许带红，但**红面必须 ⊆ 上表且逐条能说出归属** —— 🔴 **归不到的红 = 真回归，当场查清**，不得记账挂起；
- **批次 2 收口门仍要求全绿**（T-S8-2-12）；**批次 1b / 3 / 4 的收口门一律要求全绿**。

### 3.4 三类基线账目面（AC-S8-21 的预先列示，**禁止事后补记**）

架构 §6.2④ 明令"须在开发计划里**预先**列为预期改动，禁止事后补记"。逐类列示：

**A. 函数源码字节冻结表**（`tests/test_sprint7_s713_reported_metrics.py:994-1005`，10 行）

| 函数 | 本 Sprint 是否改动 | 处置 | 落在 |
|---|---|---|---|
| `_completion_insufficient` | **否** | 一字不动，哈希不变 | — |
| `_apply_no_metrics` | 🔴 **删除** | **整行移出冻结表** | T-S8-2-7 / T-S8-2-12 |
| `_apply_incomplete_execution` | **否** | 一字不动，哈希不变 | — |
| `_build_execution_result` | **是**（+`conclusion` 形参、`success` 改派生） | **换发哈希 + 写明原因** | T-S8-2-8 / T-S8-2-12 |
| `_reconcile_steps` | **否** | 一字不动 | — |
| `_audit_declared_steps` | **否** | 一字不动 | — |
| `_extract_metrics_block` | 🔴 **删除**（v1.2 裁定 1） | **整行移出冻结表** | T-S8-2-1 / T-S8-2-12 |
| `_parse_metrics` | 🔴 **删除**（v1.2 裁定 1；**删函数严格强于清死参** ⇒ PRD §4.2 第 5 条被**超越**而非违反） | **整行移出冻结表** | T-S8-2-1 / T-S8-2-12 |
| `_collect_grouped_metrics` | 🔴 **删除**（**v1.3 跟改架构 §16.6，推翻架构 v2.1 §13 的「不删、不改」**） | **整行移出冻结表** | T-S8-2-10 / T-S8-2-12 |
| `_regex_scan_metrics` | 🔴 **删除**（v1.2 裁定 1） | **整行移出冻结表** | T-S8-2-1 / T-S8-2-12 |

⇒ 🔴 **v1.3 账目（跟改架构 v2.4 §16.6，v1.2 的「10 → 6」已作废）：5 行移出**（`_apply_no_metrics` / `_extract_metrics_block` / `_parse_metrics` / `_regex_scan_metrics` / **`_collect_grouped_metrics`**）**+ 1 行换发**（`_build_execution_result`）**= 6 行动；冻结表 10 行 → 4 行**（剩 `_completion_insufficient` / `_apply_incomplete_execution` / `_reconcile_steps` / `_audit_declared_steps`）。

⚠ **两处口径澄清（v1.2 订正，本计划 v1.1 曾算错，如实留痕）**：
- 🔴 **v1.1 的保留理由③（现 §16.C 表第 ③ 行）写的是「删除会把冻结表必红行数从 3 推到 6，账目翻倍」——这个数算错了**：`_parse_metrics` 删了就**不存在「换发」**（是移出），`_llm_extract_metrics` **本来就不在冻结表 10 行里**。实际是 **5 行动 / 表由 10 行缩到 6 行**。
- **PRD §4.2 的「至少 6 行必红」**同样不是本表的靶。**账目一律以本表逐行为准**（AC-S8-21① 的「逐一换发并写明原因」）。
🔴 **红线**：禁止整表删除、禁止把断言改成"不少于"来规避（AC-S8-21 原文）。

**B. 三处 prompt 主体哈希基线**

| 侧 | 现状 | 本 Sprint | 落在 | 备注 |
|---|---|---|---|---|
| **planning** | 已有门（`tests/test_sprint6_b1_prompt_guards.py:70`） | **换发一次** | T-S8-1b-2 | ⚠ 该门历史上曾是 `EXPECTED = actual` 自锁定形态（R-S7-41 留档），换发时**必须确认右侧是硬编码字面量** |
| **execution** | 已有门**两处**（`test_sprint5_t14_execution_prompt.py:219` + `test_sprint7_s710_exec_locality.py:653`） | **换发一次**（两处同步） | T-S8-2-9 | 架构 §6.2④：判定纪律段 + 输出要求段属**同一次改写**，哈希**只换发一次** |
| **coding** | 🔴 **零字节门**（sprint7 P-64 + `docs/TODO.md:633` 开放遗留，本计划复核仍成立） | 🔴 **新建**（**单列任务 T-S8-1a-3**，先建后改） | 建门 **T-S8-1a-3** / 换发 **T-S8-2-1b** | AC-S8-21② 说“换发”，**实为新建**（P-S8-5）。唯一沾边的 `test_sprint5_t13_coding_prompt.py:180-183` 是**自锁定形态、零守门效力**。走三件套：旧哈希先记（T-S8-1a-1）→ 建门并验红（T-S8-1a-3）→ 改后重算写死字面量 + §15.1 留档（T-S8-2-1b） |

**C. 术语守门计数**（`tests/test_s708_user_text_guard.py`，三个数必须 `==`）

| 项 | 现值 | 目标值（预测） | 增量来源 |
|---|---|---|---|
| `EXPECTED_TERM_LABELS_N` | **43** | **45** | `conclusion_level` 3 → 4（**+1**）；`error_category` 14 → 15（新增 `no_verifiable_output`，**+1**；`no_metrics` **保留不删**，架构 §7）。`report_form` 3 条**只换文案不改条数**（+0） |
| `EXPECTED_CONSTANTS_N` | **15** | **≥ 19**（开工时按实际提取的具名常量精确定） | 预计新增：execution 新错误类别的 summary/fix_hint 两常量、`_NO_METRICS_EARLY_STOP_SUMMARY` **补进守门**（P-S8-8，它今天不在表里）、reporting 的 `_render_success_criteria` 措辞常量、`_render_audit_findings` 中性节文案常量 |
| `EXPECTED_N` | **58** | 两项之和 | — |

⚠ **`EXPECTED_CONSTANTS_N` 的目标值必须在 T-S8-3-1 按实际提取的常量清单精确定**，不得在本计划阶段写死一个数当靶——**写死一个猜的数，等于给自己造一条注定要改的断言**。
🔴 **禁止把 `==` 放宽为 `>=`**（AC-S8-20⑤ 原文 + 该测试模块 docstring `:36-38` 已写明"用 `==` 是刻意的，不是写死忘了改"）。

---

## 4. 批次 1a：能力接入（S8-02 编码侧 / S8-03 / S8-10）

> **前置条件**：Q-S8-06（已裁，架构 §6）+ Q-S8-02 的**字段名**（架构 §2.5 已裁定 `success_criteria`，依赖即刻解除）。
> **产出**：state 两键落盘 + 编码环节知道要产出什么 + 执行环节能读文件 + 论文报告值与本篇成功标准送达。
> ⚠ **`<METRICS>` 执行侧通道退场已随拍板 2 迁出本批**（现 `T-S8-2-1`，批次 2 首位）⇒ **本批落盘后系统仍然可真跑、可演示**。
> **文件边界**：`core/state.py`（T-S8-1a-2 **一次收口**）+ `tests/`（T-S8-1a-3 建门，**零生产改动**）+ `core/nodes/execution.py`（**仅 T-S8-1a-4 一处：工具列 3→5**）+ `tests/`（T-S8-1a-5）。🔴 **本批不碰 `core/nodes/coding.py`**（改词已迁走）；**不碰** `planning.py` / `plan_checks.py` / `reporting.py` / `ui/` / `graph.py` / `config.py`。
> **红线**：🔴 **本批不得改变任何运行时行为** —— 判定链路、`_EXECUTION_SYSTEM_PROMPT_BODY`、上下文 payload、编码提示词**一字不动**；工具层 `code_fs_tools.py` 一字不动；`_collect_grouped_metrics` 一字不动。
> ✅ **本批收口门要求全量回归零失败**（拍板 2 之后，§3.3 的预期变红面整体后移进批次 2 ⇒ 本批不产生计划内的红）。

### 任务 T-S8-1a-1：实现前核实 + 回归基线重测落档（**零生产改动**）

- **产出文件**：`/tmp` 自测脚本（仓库零触碰）+ 本 dev-plan §15 勘误回填（由主控收口写入）
- **依赖项**：无
- **预计复杂度**：低
- **参考**：本计划 §1.5 前置事实 30 条 + §15 勘误 11 条

**需要做的事**：

1. `git status --porcelain` **现查**并留痕（MEMORY §1.2：harness 开头的快照不可信）。若生产代码非干净 ⇒ **停手请示**（可能有别的会话在跑别的批次）。
2. **重测回归基线并落档时间点**：`.venv/bin/pytest -q -m "not e2e"`，与 `docs/TODO.md:845` 的 **2635 passed / 25 skipped / 58 deselected / 7 xfailed** 对账。不一致时**先查清原因再开工**，不要带着不明差额往下走。
3. **逐条复核 §15 的 11 条勘误**（尤其 P-S8-3 / P-S8-4 / P-S8-5 / P-S8-9 四条实质性的），凡与本计划记载不符的**以磁盘为准**并回报主控更新 §15。
4. **预清点本批的既有断言同步面**（精确 `grep -rn`，不照抄本计划的估数）：
   - `<METRICS>` / 三档函数 / 零指标相关：本计划落盘时实测 **268 处 / 36 文件**（PRD §4.2 写"117 处 / 17 个文件"，见 §15 **P-S8-7**）；
   - `_apply_no_metrics`：**29 处 / 5 文件**；`_no_metrics_stalled`：**10 处 / 2 文件**。
5. **记下 coding prompt 主体的旧哈希**（三件套第①件，不做等于 T-S8-2-1b 的门白建）：`sha256(_CODING_SYSTEM_PROMPT_BODY.encode())[:16]` + 字符长度。sprint7 §60.2 事实 15 记的是 `37ec6ee2b1606715` / 3052 字符，**须现算复核**。

**自测检查点**：
- [ ] **CP-1a.1-1** 回归基线重测落档：实测数字 + **时间点** + 与 2635 的差额归因（MEMORY §1.2：全绿结论必须标时间点，否则等于没说）
- [ ] **CP-1a.1-2** 工作区干净性现查留痕；`git status --porcelain` 生产侧为空（或差异已请示）
- [ ] **CP-1a.1-3** §15 十一条勘误逐条复核，实测与本计划记载**逐条对上或已订正**
- [ ] **CP-1a.1-4** 三处断言同步面精确清点（`<METRICS>` 族 / `_apply_no_metrics` / `_no_metrics_stalled`），数字落档
- [ ] **CP-1a.1-5** coding prompt 主体旧哈希 + 长度现算落档（三件套第①件）

### 任务 T-S8-1a-2：`core/state.py` 两键一次收口 + `:170` 注释订正

- **产出文件**：`core/state.py`
- **依赖项**：T-S8-1a-1
- **预计复杂度**：低
- **架构参考**：§2.1（`ExecutionResult.conclusion`）+ §2.5.1（`ReproductionPlan.success_criteria`）+ §12 `core/state.py` 条目

> **🔴 为什么两个键在同一个任务里一次加齐**（这是本计划相对架构 §11 的一处**排期**判断，非设计变更）：
> 1. `core/state.py` 是**跨批次共享文件**。架构 Q-S8-02 的"阻塞批次"栏写「批次 1a（字段名）／1b（生产者）／2（消费者）」——那说的是**依赖解除的时点**，不是"要改三次"。若真分三次改，`state.py` 会在 1a / 1b / 2 三个批次里被反复触碰，正撞 MEMORY §1.2「同一个文件被两个批次同时改 ⇒ 无法按文件粒度分离提交」。
> 2. TypedDict 键**无运行时约束**：声明一个还没有生产者的键，零副作用（`.get()` 读到 `None`，下游按缺省处理）。
> 3. 架构 §12 把两个键**并列写在同一个 `core/state.py` 条目下**，一次改完与架构文本一致。
> ⇒ **本批一次收口后，`core/state.py` 全 Sprint 零改动。** 这一条写进 §12 关键纪律。

**需要实现的内容**（取值取自架构，不自创）：

1. `ReproductionPlan`（`:115-157`）**加 1 键**：`success_criteria: str`，放在既有 13 键之后，**既有 13 键与顺序一字不动**。docstring 补一段（沿 sp5 / sp7 加键注释体例）说明：
   - 语义：**对这篇论文而言，「论文核心结论得到印证」具体指什么**；由论文分析 + 规划推导，经用户在计划审核页审核批准；
   - 🔴 **单个字符串，不是"档位→达标线"的字典**——**四档名因此根本不出现在计划里，两层分离由结构本身守住**（架构 §2.5.2）。这句必须写进 docstring，防后人"优化"成字典；
   - 缺省 `""`，**缺键 ≡ `""`**（沿 S7-08 `scale_reduced` / `local_fit_note` 范式），下游一律 `.get("success_criteria") or ""`。
2. `ExecutionResult`（`:159-184`）**加 1 键**：`conclusion: Dict[str, Any]`，放在既有 **11** 键之后（⚠ 不是架构 §2.1 写的 10 键，见 P-S8-1），**既有键与顺序一字不动**。docstring 补 Sprint 8 段，写明形态：
   ```jsonc
   {"level": "复现成功",              // 四档字面量之一，就是用户可见文案本身
    "goal_checks": [{"description": "…", "verdict": "印证上了",
                     "evidence": [{"path": "...", "value": "...", "ok": true, "reason": ""}]}],
    "evidence": [{"path": "...", "value": "...", "ok": false, "reason": "路径越出本次代码目录"}]}
   ```
   并写明：**写入方单点 = `execution._decide_conclusion`**；**消费侧一律 `.get("conclusion") or {}`**（旧 checkpoint 读到 `{}` ⇒ 报告侧走旧快照兼容分支，不崩、不假装有结论）。
3. `:170` 的 `metrics_groups` 注释订正：现文「多组指标 {组名: {指标: 值}}（execution `_collect_grouped_metrics` 写）」**今天就已失真**——S7-13 起 agent 汇报优先、磁盘扫描降为兜底（`execution.py:2954-2961`），且组名是 agent 按计划写法填的、**与产物目录无关**。改为如实描述（架构 §5.5 末段点名的两处注释订正之一；另两处 `reporting.py:955` / `:995` 在批次 3）。

**红线**：
- 🔴 **不得给 `ReproductionPlan` 或 `ExecutionResult` 加第三个键**（架构贯穿硬约束：状态契约新增严格限两处两键）。
- 🔴 `GlobalState` **零改动**。

**自测检查点**：
- [ ] **CP-1a.2-1** 两键声明存在且类型正确：`ReproductionPlan.__annotations__["success_criteria"] is str`；`ExecutionResult.__annotations__["conclusion"]` 为 `Dict[str, Any]`
- [ ] **CP-1a.2-2** **既有键与顺序零扰动**：`list(ReproductionPlan.__annotations__)[:13]` 与改前逐字相同；`list(ExecutionResult.__annotations__)[:11]` 同（**⚠ 是 11 不是 10**）
- [ ] **CP-1a.2-3** **旧快照防御读不崩**：构造缺这两键的旧形态 dict，`plan.get("success_criteria") or ""` → `""`；`result.get("conclusion") or {}` → `{}`，全程零 `KeyError`
- [ ] **CP-1a.2-4** `success_criteria` docstring 含"不是字典"这条结构性红线的文字（**元检查**：防后人优化成字典；`inspect.getdoc` 子串断言）
- [ ] **CP-1a.2-5** `:170` 注释无"`_collect_grouped_metrics` 写"与"产物目录"两处失真表述残留（`grep` 零命中）
- [ ] **CP-1a.2-6** `GlobalState` 键集合与改前**逐字相同**（负向：零新增）

### 任务 T-S8-1a-3：🔴 **新建** coding 侧 system prompt 字节哈希门（**先建后改，零生产改动**）

- **产出文件**：`tests/`（新建门的落点，见下）+ 本 dev-plan §15.1 基线留档
- **依赖项**：T-S8-1a-1（旧哈希已现算落档）
- **预计复杂度**：中（改动小，**但它是 T-S8-2-1b 的前置，顺序错了这道门就白建**）
- **参考**：**AC-S8-21②**（口径须按 P-S8-5 订正）+ `docs/TODO.md:633`（sp7 已登记的开放遗留）+ sprint7 **T-S7-6-2「先建后改」**先例 + **R-S7-41** 自锁定坑留档

> **🔴 这是一个独立任务，不是 T-S8-2-1b 的一个子项。** 三条理由，缺一条都不足以单列：
>
> 1. **AC-S8-21② 的字面写法会让这件事落空。** PRD `:411` 原文是「执行侧、编码侧、规划侧提示词哈希基线**换发**」——但**编码侧根本没有基线可换发**（P-S8-5，证据见下）。照"换发"去做，开发会去找一个不存在的基线，任务当场落空，事后又多一条"写了没人做"。
> 2. **它是 sp7 已登记的开放条件，Sprint 8 正是它说的那个"日后"。** `docs/TODO.md:633` 逐字写着：「`coding.py` / `resource_scout.py` 两侧 system prompt 主体仍无字节基线守门……本批**不扩围**（超范围），**日后改那两处 prompt 时须一并补齐"写死哈希 + 留档 + 验红"三件套**」。⇒ **本任务不是扩围，是履行前批留下的前置条件。**
> 3. **"先建后改"是本项目验证门真实性的既有范式**（sprint7 T-S7-6-2 / §48 P-27）：用**改前**哈希建门 → 下一个任务一改 prompt，门**当场红** ⇒ **那次红本身就是"这道门是真的"的活体证明**。反过来"改完再建门"永远证明不了门有牙。
>
> **磁盘证据（本计划落盘时实测，2026-08-04）**：
> - 全仓 `grep -rn "hexdigest" tests/` 命中的**真 prompt 字节门只有三处**：`test_sprint6_b1_prompt_guards.py:79`（planning，`EXPECTED_HASH = "ef6d267030fd2a0c"`）+ `test_sprint5_t14_execution_prompt.py`（execution）+ `test_sprint7_s710_exec_locality.py`（execution 第二处）。其余 `hexdigest` 命中（`test_react_base.py` / `test_sprint6_s6_01_controller.py` / `test_sprint6_s6_07_task_status.py` / `test_sprint5_t52_regression_targets.py` / `test_sprint7_s713_reported_metrics.py`）**均非 prompt 门**（分别是子图 / 控制器 / 任务态 / 回归靶 / 函数源码冻结表）。**coding 侧零。**
> - 唯一沾边的 `tests/test_sprint5_t13_coding_prompt.py:180-183` 是**自锁定形态**：
>   ```python
>   expected_prefix = _CODING_SYSTEM_PROMPT_BODY + _CODING_HONESTY_SECTION
>   assert prefix_a == expected_prefix
>   ```
>   等号两边都从同一组常量算出 ⇒ **常量改成什么它都恒绿**，与 R-S7-41 那道 `x == x`、以及 sprint7 P-27 记的 execution 侧旧门**完全同族**，**零守门效力**。
> - sp7 S7-13 已用非侵入探针实证：改 coding 主体后全量 **2506 passed / 0 failed，零红**（§63 P-64）。

**需要实现的内容**：

1. **确定被守对象 = coding 的完整稳定前缀**：`_CODING_SYSTEM_PROMPT_BODY + _CODING_HONESTY_SECTION` 两个常量的拼接体。
   - 理由：`test_sprint5_t13_coding_prompt.py:167` 已确立"稳定前缀 = 主体 + 诚实红线段"这一口径（`_CODING_HONESTY_SECTION` **进稳定前缀**，R-PC4）。只守 `_CODING_SYSTEM_PROMPT_BODY` 会漏掉诚实红线段被改的情形。
   - **默认取值**：建**两条**断言——①主体单独一条（本批要改的就是它，红得精准）；②拼接体一条（覆盖诚实红线段）。若嫌冗余，最少也要有主体那一条。
2. **哈希写死为字面量**：`sha256(body.encode())[:16] == "<改前实测值>"`，取值来自 **CP-1a.1-5** 落档的旧哈希。
   - 🔴 **严禁 `EXPECTED_HASH = actual` 的自锁定形态**——这正是 R-S7-41 与本任务立项证据里那两道假门的死法。断言右侧**必须是硬编码字面量**。
   - 配一条**元断言**（沿 sprint7 CP-9.2-5 范式）：用 `inspect.getsource` 扫该测试函数，断言其中**不含**把 `EXPECTED_*` 赋成运行时算出值的形态。
3. **配套补一条"主体无论文级动态变量"断言**（已知 bug 模式 #4）：正则 `\d{4}\.\d{4,5}` 在主体零命中，与 planning / resource_scout 的既有守门对齐。
4. **失败信息必须同时打出新旧哈希**（沿 `test_sprint6_b1_prompt_guards.py:82` 范式）：`f"coding prompt 主体字节已变更（当前：{actual}，基线：{EXPECTED}）"`。门红的时候，后人要能一眼看出"该重算基线了"而不是"哪里坏了"。
5. **§15.1 留档**（三件套第③件）：旧哈希 + 长度 + 建门时点 + 建门原因。**T-S8-2-1b 改完 prompt 后回来把新哈希补上同一行。**

**红线**：
- 🔴 **本任务零生产改动**：`core/nodes/coding.py` 逐字节未改（`git diff` 为空自证）。这道门必须建在**改动之前**的字节上。
- 🔴 **不扩围到 `resource_scout.py`**：`docs/TODO.md:633` 登记的是**两侧**都缺门，但 **Sprint 8 不动 `resource_scout` 的 prompt** ⇒ 按同一条纪律（"日后改那处 prompt 时再补"）**本批不建它的门**。已在 §15 **P-S8-5** 注明"另一侧仍无门，属 sp7 遗留、本批不扩围"。
- 🔴 **不动 `test_sprint5_t13_coding_prompt.py:180-183` 那条自锁定断言**：它虽零守门效力，但它证明的另一件事（"稳定前缀 == 两常量拼接"这个**结构**关系）仍然成立且有价值。**新门是补充，不是替换。** 若要顺手改造它，属独立事项，登记不做。

**自测检查点**：
- [ ] **CP-1a.3-1** 门存在且**当前绿**：以 CP-1a.1-5 落档的改前哈希建门，跑一次 → **绿**（证明基线取值正确，不是抄错了一个数）
- [ ] **CP-1a.3-2** 断言右侧**是硬编码字面量**（元断言，`inspect.getsource` 扫；专防 R-S7-41 自锁定坑）
- [ ] **CP-1a.3-3** 失败信息含**新旧两个哈希**（构造一次假红验证信息可读）
- [ ] **CP-1a.3-4** "主体无论文级动态变量"断言：正则 `\d{4}\.\d{4,5}` 在主体零命中
- [ ] **CP-1a.3-5** ★**验红（这一步就是"门是真的"的活体证明）**：`cp` 备份 `coding.py` → 主体内**插一个空格** → 门**必须红**并打出新旧哈希 → `cp` 还原 → `sha256sum -c` 校验 → 复绿。**全程禁 `git checkout`**
- [ ] **CP-1a.3-6** ★**证否自锁定**：把门临时改成 `EXPECTED = actual` 形态 → 再插空格 → **它不会红**（把"假门长什么样"实测一遍，留档在 §15.1）→ 还原
- [ ] **CP-1a.3-7** **零生产改动**：`git status --porcelain` 中 `core/nodes/coding.py` **不出现**；`resource_scout.py` 同（不扩围自证）
- [ ] **CP-1a.3-8** §15.1 留档已写（旧哈希 + 长度 + 时点 + 原因；新哈希留空待 T-S8-2-1b 回填）

> **⚠ 顺序纪律（本批最容易做反的一件事）**：**T-S8-1a-3 必须在 T-S8-2-1b 之前完成并绿**。若先改了 `coding.py` 再建门，建出来的就是"照着改后字节写死的基线"——它**永远绿**，与自锁定形态等价，**这道门等于没建**。

### 任务 T-S8-1a-4：execution 只读工具接入（S8-03）

- **产出文件**：`core/nodes/execution.py`（`_run_execution_agent` 工具列 `:1581-1585`）
- **依赖项**：T-S8-1a-2
- **预计复杂度**：低（但边界纪律是命门）
- **架构参考**：Q-S8-03 §3.1/§3.3 + PRD §4.3

**需要实现的内容**：

1. 工具列 **3 → 5**：加入 `make_read_code_file_tool()` 与 `make_list_dir_tool()`（`core/tools/code_fs_tools.py:159` / `:198`）。**不新造工具**（PRD §4.3 明令）。
2. 🔴 **`core/tools/code_fs_tools.py` 一字不动**（架构 Q-S8-03 方案 A / 贯穿硬约束）。**明确否决**给 `make_read_code_file_tool` 加 `base_dir` 参数在工具层收窄（架构 §3.2 方案 C：那会砍掉"执行环节读参考仓库诊断问题"的能力，**直接违反 PRD §4.3**）。
3. 🔴 **两个闸物理分处两文件，不许合并**（架构 §3.3，**须逐字进交接文档**）：

   | 闸 | 管什么 | 落点 | 边界 |
   |---|---|---|---|
   | 工具边界 | **agent 能读什么** | `code_fs_tools._is_within_workspace`（`:71-79`） | **整个工作区**（含参考仓库 `selected_repo.local_path`）——**本次一字不改** |
   | 证据边界 | **什么能当判定物证** | `execution._verify_evidence` 第④重（**批次 2** T-S8-2-5） | **仅 `code_output_dir` 之下** |

   ⇒ agent 读参考仓库里的结果表**不被拒绝**，但**拿它当物证一律不成立**。
4. 🔴 **执行环节不得写代码这条硬防线一字不动**（`execution.py:995` `is_inline_code_write`）；`:1010` 管道/重定向/后台拒绝一字不动。
5. ⚠ **本任务不动 prompt 主体**（工具说明段的改写随 T-S8-2-9 一次落，见本批红线）。⇒ 本任务落盘后到批次 2 之间，agent 手上有工具但系统提示词没介绍它——**这期间不真跑，无实际影响**（§0.0）。

**自测检查点**：
- [ ] **CP-1a.4-1** 工具列 **3 → 5**，且新增的恰是 `read_code_file` / `list_dir` 两个（按 `tool.name` 断言，**不按下标**）
- [ ] **CP-1a.4-2** 既有三个工具的**装配顺序与构造实参一字不动**（`prepare_environment` / `run_in_sandbox` / `request_user_input`）
- [ ] **CP-1a.4-3** ★**正向**：执行环节**能读参考仓库**（构造 `selected_repo.local_path` 下的文件 → `read_code_file` 成功返回内容）
- [ ] **CP-1a.4-4** ★**负向**：工作区之外的路径**被拒**（`_is_within_workspace` 既有拦截用例全绿，`code_fs_tools.py` 逐字节未改，`git diff` 为空自证）
- [ ] **CP-1a.4-5** **不得写代码的硬防线零改动**（AC-S8-05）：`execution.py:995` 与 `:1010` 两处逐字节未改；既有内联写码拒绝用例 + 管道拒绝用例全绿
- [ ] **CP-1a.4-6 逐条验红**：①去掉 `read_code_file` 装配 → CP-1a.4-1 红；②把 `_is_within_workspace` 改成永远返回 True → CP-1a.4-4 红（**验后立即 `cp` 还原 + `sha256sum -c`**）

### 任务 T-S8-1a-5：既有断言同步 + 批次 1a 自测收口门

- **产出文件**：`tests/`（同步面，只换不弱化）
- **依赖项**：T-S8-1a-3 + T-S8-1a-4（**本批全部生产任务**）　⚠ **v1.2 订正**：v1.1 此处写的是「T-S8-1a-4 + T-S8-2-1」——批次 1a 的收口任务**前向依赖批次 2 首位任务**，是拍板 2 改号时的机械替换残留（悬空依赖）。两个被依赖任务本次又双双迁进批次 2，故按迁移后的实际组成重算
- **预计复杂度**：中
- **参考**：§3.3 预期变红面 + §0.3 禁弱化自查

**需要做的事**：

1. **精确清点变红面**（不照抄本计划的估数，`.venv/bin/pytest -q -m "not e2e"` 实跑取全量红名单）。
2. **逐条归因**：每一条红必须能归到 §3.3 表的某一行。🔴 **归不到的红 = 真回归，必须当场查清**，不得记账挂起。
3. **本批只同步"归属明确且在本批就该改"的断言**：
   - `coding.py` 三处清除引发的正向断言（原来断言 prompt 里有 `<METRICS>` 的）→ **改为负向断言**（断言不存在），**这是"只换不弱化"的典型形态**：强度不降，方向反转；
   - 🔴 **coding prompt 字节门基线**（T-S8-1a-3 建、T-S8-2-1b 换发）→ 确认门**当前是绿的**且断言右侧仍是硬编码字面量；确认 `test_sprint5_t13_coding_prompt.py:180-183` 那条自锁定断言**仍在且仍绿**（它守的是"稳定前缀 == 两常量拼接"的结构关系，本批未改该结构）。
4. 🔴 **本批不碰判定链路，因此不产生 §3.3 的任何一族红**（拍板 2 之后解绑已迁到 T-S8-2-1）⇒ 一切 `success is True` 族、`metrics` 非空族、`_apply_no_metrics` / `_no_metrics_stalled` 族、冻结表 `_parse_metrics` 一行 **本批全部保持原样、保持绿**，挂到批次 2（T-S8-2-12）处理。**本批不得提前改它们**——改了就是改两遍，且会把批次 2 的账目搅乱。
5. **禁弱化自查**：`git diff tests/` 中 `>=` / `issubset` / `pytest.skip` / `xfail` **零新增**。

**自测检查点**：
- [ ] **CP-1a.5-1** ★**全量非 e2e 回归零失败**（含时间点）—— 拍板 2 之后本批**不允许带红收口**
- [ ] **CP-1a.5-2** ★**可用性自证**：本批落盘后系统**仍可判出 `success is True`**（构造 exit 全 0 + 步骤跑完 + `<METRICS>` stdout → `metrics` 非空、`success is True`）⇒ **证明不可用窗口确实没有在本批开启**
- [ ] **CP-1a.5-3** 本批该同步的断言已同步（coding 三处负向 / coding 字节门基线已换发且绿）；🔴 **`_parse_metrics` 相关与冻结表逐行保持原样**（负向：本批 `git diff` 中冻结表零改动）
- [ ] **CP-1a.5-3b** ★**三件套闭合自证**：coding 侧的旧哈希（CP-1a.1-5）、建门时的红（CP-2.1b-7）、换发后的新哈希（CP-2.1b-8）**三个证据齐全并落在 §15.1 同一行**——缺任一件，这道门在交付说明里**不得声称"已建"**
- [ ] **CP-1a.5-4** 禁弱化自查通过（`git diff tests/` 四类零新增；删除的断言逐条能说出等强或更严的替换关系）
- [ ] **CP-1a.5-5** **文件边界自查**：`git status --porcelain` 生产侧**只有** `core/state.py` / `core/nodes/coding.py` / `core/nodes/execution.py` 三个文件；`planning.py` / `plan_checks.py` / `reporting.py` / `ui/` / `graph.py` / `config.py` **逐一零改动**
- [ ] **CP-1a.5-6** `mypy` 清缓存后零错误（`rm -rf .mypy_cache` 再跑）

> **批次 1a 收口门**：CP-1a.1-\* ~ CP-1a.5-\* 全绿 + ✅ **全量非 e2e 回归零失败**。
> 🔴 **停手等 Maria 确认再开下一批。**
> ✅ **收口后系统仍然可用**（拍板 2）：可真跑、可演示 —— 但真跑仍须 Maria 单独授权（§0.3 第 8 条）。

---

## 5. 批次 1b：计划侧成功标准 + 三道护栏（S8-01 扩围 / S8-11）

> **前置条件**：Q-S8-02 §2.5 已裁 + Q-S8-09 §15 已裁；**T-S8-1a-2 的 `state.py` 两键须先落**。
> **产出**：计划针对本篇论文写明成功标准 + 三道护栏（审核页显眼可见可改 / 客观封顶 / W6 空话告警）。
> **文件边界**：`core/nodes/planning.py`（T-S8-1b-2 单收口）+ `core/plan_checks.py`（T-S8-1b-3）+ `ui/pages/plan_review.py`（T-S8-1b-4）+ `tests/`（T-S8-1b-5）。**不碰** `state.py`（1a 已收口）/ `execution.py` / `coding.py` / `reporting.py` / `term_map.py` / `graph.py` / `config.py`。
> **可与批次 1a 并行**（文件边界零重叠），但 T-S8-1b-2 须等 T-S8-1a-2。
> **红线**：护栏 3 **只产警示、不阻断审批**；`check_plan` 既有五条 W 的 rule / message / 触发条件**一字不动**；`_INLINE_PY_MAX_CHARS` 的可行窗口 `[98,126]` 与「单一规则、不做动词枚举、不做后缀白名单」两条红线（`plan_checks.py:76-89`）**一字不动**。

### 任务 T-S8-1b-1：实现前核实（**零生产改动**）

- **产出文件**：`/tmp` 自测脚本 + §15 勘误回填
- **依赖项**：无
- **预计复杂度**：低

**需要做的事**：

1. **核实 planning 的两处 plan 构造点**（新增键必须两处都补，漏一处则某条路径下 `success_criteria` 恒缺失）：本计划实测为 `planning.py:473-483`（`_map_planning_result` 的 ReAct 产出映射）与 `:683-697`（降级兜底计划）。**开工时逐处 Read 复核行号。**
2. **核实 planning 输出契约的 `required`**：本计划实测 `:122` = `["plan_summary", "code_strategy", "deliverables"]`。
3. **核实 `check_plan` 的既有五条 W 行为基线**（Q-S8-09 方案 A 的"既有行为字节级零扰动"是命门）：跑 `tests/test_sprint6_b1_plan_checks.py` 全绿并**把 18 处两参调用的返回值逐条 dump 落档**——这是 G5 契约回归的对照基准。
4. **核实 `plan_review` 的 payload 键与展示区结构**：`paper_analysis_summary` 在 `:1005` 就在读（架构 §12 `ui/` 条目所指）；`_render_plan_check_warnings` 在 `:779`、调用点 `:1015`；计划展示区的"顶部"在哪一段（护栏 1 的落点）。
5. 🔴 **核实"可编辑"的既有通道**（§16.A 拍板 1 的事实依据，开工时复核）：本计划实测 `plan_review.py` **没有任何计划字段的就地编辑控件**，S2-12 起 revise 一次性文本框已迁到多轮对话面板（`:47-59` 注释逐字写着）。**开工时复核，若已变化以磁盘为准。**
6. **记下 planning prompt 主体旧哈希**（三件套第①件）：`tests/test_sprint6_b1_prompt_guards.py:70` 的 `EXPECTED_HASH`。⚠ **同时核实该断言右侧是不是硬编码字面量**——R-S7-41 留档记着它曾是 `EXPECTED = actual` 自锁定形态、零守门能力地存在了两个 sprint。**若现在仍是自锁定形态，本批必须顺手改成字面量**（属配套回归保护，不算扩围）。

**自测检查点**：
- [ ] **CP-1b.1-1** planning 两处构造点行号 + `required` 列表现查落档
- [ ] **CP-1b.1-2** `check_plan` 18 处两参调用的返回值**逐条 dump 落档**（G5 对照基准）
- [ ] **CP-1b.1-3** `plan_review` payload 键 / 展示区结构 / `_render_plan_check_warnings` 调用点现查落档
- [ ] **CP-1b.1-4** ★"可编辑"既有通道核实：就地编辑控件**有无**、revise 走哪条路 —— 结论回填 §16.A（若磁盘已变化，须回报主控）
- [ ] **CP-1b.1-5** planning prompt 旧哈希落档 + **自锁定形态检查**（`inspect.getsource` 扫该断言）

### 任务 T-S8-1b-2：planning 冻结区改写 + schema `success_criteria` 进 `required` + 两处构造点 + 哈希三件套

- **产出文件**：`core/nodes/planning.py`
- **依赖项**：T-S8-1b-1 + T-S8-1a-2
- **预计复杂度**：**高**（两层分离命门 + 冻结区 + `required` 破例）
- **架构参考**：§2.5.1/§2.5.4/§2.5.5 + §12 `core/nodes/planning.py` 条目 + PRD §4.1

**需要实现的内容**：

1. **交付清单语义扩围**（复裁 2 / PRD §4.1.1）：`deliverables` 提示词措辞扩为「**本次复现应当落地的产物**」（原为最低交付基准线）；`expected_output` 要求**写清产出文件相对代码目录的路径**（作为给编码环节看的说明）。
   - 🔴 **`:196` 那句产出目录约定保留不动**（架构 §12 明令）：「实验产出统一落在代码目录下的 `outputs/` 目录里」。
   - 🔴 **这份清单不是档位判据**（PRD §4.1.1 第 3 条），提示词里**不得写成"照这个清单判成功"**。
2. 🔴 **新增 `success_criteria` 输出字段**（S8-01 扩围，第四批拍板；**推翻了 A-S8-02「不需要新增计划字段」**）：
   - schema 加 `"success_criteria": {"type": "string"}`；
   - 🔴 **进输出契约的 `required`** —— **这是对 S7-08 纪律 2 的有意背离，须留档**（架构 §2.5.5 逐字）：纪律 2（新键不进 required，避免 `react_base` finalize 多烧一次 schema 重生成）的成立前提是"缺省已是安全值"，而这里缺省 `""` **不是**安全值——等于这篇论文没有判定依据，整条判定链当场断。⇒ **代价（缺失时多烧一次调用）正当且可接受**，与 `scale_reduced` 的情形**性质相反，不构成对该纪律的推翻**。
   - `required` 由 `["plan_summary", "code_strategy", "deliverables"]` 变为 **+`"success_criteria"`**（四项）。
3. **两处构造点各补一个 kwarg**（`:473-483` 与 `:683-697`，实际行号以 CP-1b.1-1 为准）：`success_criteria=_coerce_str(result.get("success_criteria"))` / 兜底计划里给一句**明确说明"本篇标准缺失"的静态中文**（⚠ **不得编造一条达标线**——兜底计划编一条标准出来比留空更危险，那是系统在替用户批准一条没人看过的及格线）。
   > **默认取值**：兜底路径 `success_criteria=""`。理由：架构 §2.5.6 已裁「标准缺失 ⇒ agent 没有可核验的「印证」判据 ⇒ 落既有封顶 3「仅代码跑通」」，且明令**不得在代码里另写一条"标准为空则降档"的分支**。⇒ 留空是**已被架构定义过语义**的状态，编一条则不是。
4. 🔴 **提示词须同时立三条约束**（架构 §12 逐字，一条不能少）：
   - ①**只写本篇达标线、不得改动四档的含义**（两层分离，§0.5）；
   - ②**必须引用论文的具体主张**（点名指标或论文结论），**禁止"能运行即可"这类空话**（护栏 3 的提示词侧）；
   - ③**四档的语义边界不得写进计划提示词的可填内容里**——它属第一层，写进去就等于把第一层交给计划改。
5. **推导原料的说明**：提示词要点明这份标准由**论文分析已产出的 `metrics` / `datasets` / `baseline_results` / `method_summary`** 推导（`planning.py:379-382` 的透传清单**今天就含 `baseline_results`**，§1.5 已核）⇒ **本次不新增上游能力**。
6. **证据形态是举例，不是清单**（PRD §4.5.2 逐字）：达标线可表述为「数值与论文报告对上」「组间趋势对上」「定性产物支持论文说法」等——**但这是举例，不是可选项清单**。沿 `planning.py:233` 已有的同款措辞范式（「缩规模的常见做法（**举例，不是可选项清单**）」）。
   - 🔴 **系统不实现"证据形态分类"**：不得引入证据类型枚举、不得按形态分支——**一旦按形态分支，就又变成照着某类论文设计，直接复发病③**（AC-S8-08② 的负向静态审查对象）。
7. **哈希三件套**（planning 侧）：旧哈希（CP-1b.1-5）→ 新哈希写死字面量 → §15.1 留档。⚠ 若发现该门是自锁定形态，**同批改成字面量**。

**红线**：
- 🔴 **四档档名（复现成功 / 部分复现 / 仅代码跑通 / 失败）不得出现在 `planning.py` 里**（架构 §2.5.2 方案 A 的全部价值就在这——**计划连能写越权内容的字段都没有**）。⇒ 本任务加一条负向断言。
- 🔴 新增文本纯静态，零论文级动态变量（已知 bug 模式 #4）。
- 🔴 `local_fit_note` / `scale_reduced` 相关段落、`:196` 产出目录约定、既有 13 键与顺序**一字不动**。
- 🔴 用户可见文本禁内部术语（MEMORY §4.2）：**别拿英文枚举当叙述示范**，否则模型会把它抄进自由文本字段。

**自测检查点**：
- [ ] **CP-1b.2-1** schema 含 `success_criteria: string` 且**在 `required` 里**（AC-S8-02①）
- [ ] **CP-1b.2-2** ★**两处构造点都补齐**：ReAct 正常产出路径与降级兜底路径**各自**产出的 plan 都含 `success_criteria` 键（**逐路径构造用例，不用一条通用断言代替**）
- [ ] **CP-1b.2-3** 兜底路径 `success_criteria == ""`，**不编造达标线**（负向：兜底计划文本中不出现任何数值门槛表述）
- [ ] **CP-1b.2-4** ★**提示词三条约束正向断言**（AC-S8-02②）：主体含"只写本篇达标线 / 不改档位含义"、"必须引用论文的具体指标或结论"、"禁止空话"三层意思的文字
- [ ] **CP-1b.2-5** ★★**命门·负向断言：四档档名零出现**（AC-S8-02③ / AC-S8-08①）：`grep -c "复现成功\|部分复现\|仅代码跑通"` 在 `core/nodes/planning.py` **零命中**（"失败"一词是常用词，**单独用上下文人工审 + 断言不出现"四档"/"档位"这类元表述**）
- [ ] **CP-1b.2-6** ★**负向：无证据形态枚举 / 无按形态分支**（AC-S8-08②）：`planning.py` 与新增提示词中不出现证据类型枚举；三种形态以"举例"措辞出现（正向断言含"举例"字样）
- [ ] **CP-1b.2-7** **旧快照兼容**（AC-S8-02④）：缺 `success_criteria` 的旧计划 dict 经下游 `.get(...) or ""` 读**不崩**；`plan_review` 展示 / `execution` 注入 / `coding` 注入三处均按空处理
- [ ] **CP-1b.2-8** **Prompt Cache 幂等**：正则 `\d{4}\.\d{4,5}` 在主体零命中；两次不同 state 下主体逐字节相同
- [ ] **CP-1b.2-9** 哈希三件套齐全：新哈希 + 长度算出、断言右侧是**硬编码字面量**（元检查）、§15.1 留档已写
- [ ] **CP-1b.2-10** `:196` 产出目录约定与 `local_fit_note` / `scale_reduced` 段落**逐字节未改**
- [ ] **CP-1b.2-11 逐条验红**：①主体插空格 → 字节门必红；②删掉"不改档位含义"那条 → CP-1b.2-4 红；③在提示词里塞一个四档档名 → CP-1b.2-5 红；④`success_criteria` 移出 `required` → CP-1b.2-1 红；⑤只补一处构造点 → CP-1b.2-2 红

### 任务 T-S8-1b-3：`plan_checks` W6 + 带默认值的第三个关键字形参（护栏 3，Q-S8-09）

- **产出文件**：`core/plan_checks.py`
- **依赖项**：T-S8-1b-1
- **预计复杂度**：中
- **架构参考**：§15 全节（Q-S8-09）+ PRD §4.11.3

> **零改动红线本次再解锁，范围严格限于两项**（架构 §12）：①新增 W6；②`check_plan` 加一个带默认值的关键字形参。**其余一字不动。** 先例：S7-10 曾解锁过一次（W4/W5 就是那次加的）。

**需要实现的内容**：

1. **签名扩展**（架构 §15.2 方案 A）：`check_plan(plan, resource_info, paper_analysis: Optional[Dict[str, Any]] = None)`。
   - ✅ 既有 **19 个调用点**（生产 1 + 测试 18，§1.5 事实 21）**不改也能跑**；默认 `None` ⇒ W6 不触发 ⇒ **既有行为字节级零扰动**。
   - 🔴 **精确表述**（架构 §15.2 已如实登记，交接文档须逐字照抄）：PRD §8 那句"函数签名一字不变"**不成立**，正确表述是「**向后兼容、既有调用零改动、既有五条警示行为一字不变**」。
   - ❌ **明确否决**方案 B（判据只看计划内部）：判据被掏空，计划自己引用自己，论文里报了什么根本不看；❌ 方案 C（W6 放 UI 侧）：把确定性判定塞进展示层，不可单测复用。
2. **W6 判据**（架构 §15.3，纯字符串、零 IO、可单测、低误伤）：
   - **候选集** = `paper_analysis` 的 `metrics`（列表元素）+ `datasets`（列表元素）+ `baseline_results`（**字典的键**）三处的事实层英文名，**去空白、去空串**；
   - **命中判定**：`success_criteria` 文本中出现任一候选（**大小写不敏感的子串匹配**）⇒ **不报**；一个都没出现 ⇒ **报 W6**；
   - **两条边界**（沿既有"宁窄勿宽"误报防线 R-S6-A5）：**候选集为空** ⇒ **不报**（无从比对时报警只会制造噪声）；**`success_criteria` 为空串** ⇒ **报**（空标准是最该被用户看到的一种，不能因为"没内容所以没法判"就沉默）；
   - `rule` 字符串用 `"W6"`（沿既有字面量风格，**不建 Enum**）；
   - `message` 用**通俗中文**、**不得出现内部字段名**（MEMORY §4.2）——不得写"success_criteria 未引用 paper_analysis.metrics"这种。
3. 🔴 **既有五条 W 一字不动**：W1~W5 的 `rule` 字符串、`message`、触发条件逐字节未改。
4. 🔴 **`_INLINE_PY_MAX_CHARS`（`:90`）与 `:76-89` 的两条红线一字不动**（可行窗口 `[98,126]` / 单一规则不做动词枚举不做后缀白名单）。本任务与内联写码那条规则**完全不相干**，不得顺手动它。
5. **局限如实登记**（架构 §15.4，**不得包装**）：它挡的是空话，**挡不住"具体但宽松"**——「knn_accuracy 大于 0 即算成功」引用了具体指标名，照样过（R-S8-17）。⇒ **真正兜底的是护栏 1（人眼）**。这句要写进 W6 的实现注释里，防后人把它当成"防止标准画低"的保证。

**自测检查点**（对应架构 §15.5 的 G1~G7）：
- [ ] **CP-1b.3-1（G1 正向）** 成功标准里写了论文分析中的某个指标名 → **不报 W6**
- [ ] **CP-1b.3-2（G2 负向）** 成功标准 = "只要代码能跑起来就算成功" → **报 W6**
- [ ] **CP-1b.3-3（G3 边界）** 候选集为空（`paper_analysis` 无 metrics/datasets/baseline_results）→ **不报**（宁窄勿宽）
- [ ] **CP-1b.3-4（G4 边界）** `success_criteria` 为空串 → **报**
- [ ] **CP-1b.3-5** 大小写不敏感 + 三处候选源各自生效：`metrics` 元素 / `datasets` 元素 / `baseline_results` **键**各构造一条命中用例
- [ ] **CP-1b.3-6（G5 ★契约回归）** 两参调用 `check_plan(plan, resource_info)` **不抛异常**；**既有五条 W 的输出与 CP-1b.1-2 落档的 18 条 dump 逐字节相同**；W6 不出现
- [ ] **CP-1b.3-7** W6 的 `message` **零内部字段名**（负向断言：不含 `success_criteria` / `paper_analysis` / `metrics` 这类字段名字面量）
- [ ] **CP-1b.3-8** 既有五条 W 与 `_INLINE_PY_MAX_CHARS` / `:76-89` **逐字节未改**（`ast.get_source_segment` + `sha256`）
- [ ] **CP-1b.3-9（G7 验红）** 去掉 W6 判据 → CP-1b.3-2 / CP-1b.3-4 **必红**；把边界"候选集为空则不报"改成"照报" → CP-1b.3-3 必红

### 任务 T-S8-1b-4：审核页护栏 1 只读展示 + W6 展示通道多传一参

- **产出文件**：`ui/pages/plan_review.py`
- **依赖项**：T-S8-1b-2 + T-S8-1b-3
- **预计复杂度**：中
- **架构参考**：§12 `ui/` 条目第三项 + PRD §4.11.2 / AC-S8-14
- **产品裁定**：🔴 **Maria 2026-08-04 拍板 1 —— AC-S8-14「可编辑」按只读展示验收**（详见下方"裁定留痕"，**已裁定，不再是开放项**）

> ### 🔴 裁定留痕：AC-S8-14「成功标准可编辑」怎么验（**Maria 2026-08-04 拍板 1**）
>
> | 项 | 内容 |
> |---|---|
> | **争点** | **AC-S8-14**（PRD §5）要求成功标准「独立可见 **且可编辑**；用户改后以改后版本为准」，判定方式含「**改后取值断言**」；而**架构 §12** 裁的是「顶部**只读展示**……**不新增交互种类、不新增按钮**」。两句字面冲突 |
> | **磁盘事实**（§1.5 事实 22，开工时须复核） | `ui/pages/plan_review.py` **没有任何计划字段的就地编辑控件**。S2-12 起 revise 一次性文本框已迁到**多轮对话讨论面板**（`:47-59` 注释逐字写着），用户"调整计划"走的是「讨论助手 → 修改方向纪要（作 `user_feedback`）→ 重新规划」这一条路 |
> | **裁定** | ✅ **按只读展示验收**：成功标准在计划审核页**顶部、显眼、独立**只读展示；「可改」**走现成的多轮对话修订通道**，**不新增任何交互控件种类** |
> | **理由 1** | 新增就地编辑控件**直接违反 PRD 非目标 8**（"不新增中断种类 / 决策按钮 / 流程分支"）与 **PRD §4.11.2**（"不新增交互种类、不新增按钮"）—— 一条 AC 的字面读法不能反过来推翻同一份 PRD 的非目标 |
> | **理由 2** | **本项目一贯反对为单个字段新增交互形态**（`docs/MEMORY.md` §4.1 最小单一抽象；判例：sprint4 交互工具的 5 种 `input_type` 枚举被否，收敛为单个 `request_user_input`）。为 `success_criteria` 一个字段单开一套编辑控件，正是那条判例要防的形态 |
> | **AC-S8-14 的可执行读法** | 「**可编辑**」读作「**可经既有修订通道调整**」；「**改后取值断言**」读作「**计划经 revise 重生成后，审核页展示与下游注入取的都是新版本的 `success_criteria`**」（⇒ CP-1b.4-2b） |
> | **裁定人 / 日期** | **Maria，2026-08-04** |

**需要实现的内容**：

1. **护栏 1：成功标准在计划展示区顶部只读展示**（架构 §12 逐字："**顶部**只读展示，**不得埋在一堆字里**"；**Maria 拍板 1 已确认此口径**）：
   - 独立小节 + 独立标题，**不与其它字段挤在一段**（AC-S8-14"独立可见"）；
   - 原文照登（不摘要、不截断）；
   - 缺失 / 空时给一句**通俗中文兜底句**（如"这份计划没有写明本篇论文什么样算复现成功"），提为**模块级具名常量**并进术语守门（沿 `_LOCAL_ENV_FACTS_FALLBACK` 既有范式，§1.5 事实 28）；
   - 🔴 **沿用既有"用户可调整任何部分"契约，不新增交互种类、不新增按钮**（PRD §4.11.2 + 非目标 8）。
   - ✅ **AC-S8-14 的"可编辑"按拍板 1 的口径验收**：本任务**只做只读展示**，"可改"由既有的「讨论助手 → 修改方向纪要 → 重新规划」通道承担。🔴 **不得为此新增任何交互控件种类**（PRD 非目标 8 + MEMORY §4.1）。
2. **W6 展示通道接线**（架构 §12："`:1015` 的 `_render_plan_check_warnings` 调用**多传一个已在 payload 里的 `paper_analysis_summary`**（`:1005` 就在读它）"）：
   - `_render_plan_check_warnings(plan=..., resource_info=..., paper_analysis=payload.get("paper_analysis_summary") or {})`；
   - 🔴 **警示展示通道零改动、"不阻断审批"契约一字不动**（`:787-792` 的渲染循环逐字节未改；approve 按钮仍正常可用）。
3. **零新 payload 键**：`paper_analysis_summary` 本来就在 payload 里（`:1005`），**不新增 interrupt#1 payload 键**。

**红线**：
- 🔴 **不新增交互种类 / 不新增按钮 / 不新增中断种类 / 不新增流程分支**（PRD 非目标 8）。
- 🔴 **护栏 3 不做阻断门**（PRD 非目标 7 / A-S8-10）：W6 命中时 approve 按钮**必须仍可用**。
- 🔴 用户可见文本禁内部术语（MEMORY §4.2）：兜底句 / 小节标题一律通俗中文，不出现 `success_criteria` 这类字段名。

**自测检查点**：
- [ ] **CP-1b.4-1** ★**独立可见**（AC-S8-14）：成功标准渲染为**独立小节**（独立标题 + 独立块），且位置在计划展示区**顶部**（相对既有各块的顺序断言）
- [ ] **CP-1b.4-2** **原文照登**：展示文本与 `plan["success_criteria"]` **逐字相同**（不摘要、不截断、不加省略号）
- [ ] **CP-1b.4-2b（AC-S8-14「改后取值」，按拍板 1 口径）★** **走既有修订通道改后以新版为准**：模拟一次 revise 重生成（新计划的 `success_criteria` 与旧版不同）→ 审核页展示区**取的是新版本**；🔴 **负向：本任务未新增任何交互控件种类**（`plan_review.py` 的 `st.text_input` / `st.text_area` / 按钮**计数与改前逐一相同**）
- [ ] **CP-1b.4-3** 缺失 / 空串 / 非 str 三形态 → 走兜底句，**不崩、不显示空块**
- [ ] **CP-1b.4-4** 兜底句已提为模块级具名常量并进术语守门候选清单（交 T-S8-3-10 账目对平）；文本**零内部字段名**
- [ ] **CP-1b.4-5** ★**W6 接线生效**：payload 含 `paper_analysis_summary` 时，空话式 `success_criteria` **确实在审核页出现 W6 警示**；引用了具体指标名时**不出现**
- [ ] **CP-1b.4-6（G6 ★产品契约）** ★**只产警示、不阻断审批**（AC-S8-13③）：W6 出现时 **approve 按钮仍可用**（`disabled` 为假）
- [ ] **CP-1b.4-7** 既有警示渲染循环（`:787-792`）与既有五条 W 的展示行为**逐字节未改**
- [ ] **CP-1b.4-8** **零新 payload 键**：interrupt#1 payload 键集合与改前**逐字相同**
- [ ] **CP-1b.4-9 逐条验红**：①去掉 `paper_analysis` 实参 → CP-1b.4-5 红（W6 因默认 `None` 不触发）；②把成功标准塞回既有某个块里 → CP-1b.4-1 红；③给 W6 加 `st.stop()` 阻断 → CP-1b.4-6 红

### 任务 T-S8-1b-5：既有断言同步 + 批次 1b 自测收口门

- **产出文件**：`tests/`（同步面）
- **依赖项**：T-S8-1b-2/3/4
- **预计复杂度**：中

**需要做的事**：

1. planning 侧同步面：schema `required` 长度 / 键集合断言、两处构造点产出的 plan 键集合断言、prompt 哈希基线（两处若有）。
2. `plan_checks` 侧同步面：`tests/test_sprint6_b1_plan_checks.py` **18 处两参调用全绿且返回值与 CP-1b.1-2 落档逐字节相同**（这就是 G5）。
3. `plan_review` 侧同步面：payload 键集合断言、展示块顺序断言（若有）。
4. **禁弱化自查**（§0.3 第 3 条）。
5. **本批要求全量回归全绿**（1b 不碰判定链路，不受 §0.0 中间态影响）。
   - ⚠ **若与批次 1a 并行**，全量回归会带上 1a 的预期红面 ⇒ 此时的口径是「**1b 触碰的文件相关用例全绿 + 全量红面 ⊆ §3.3**」。**两批并行时的收口口径必须在交接记录里写清是哪一种**，不能含糊。

**自测检查点**：
- [ ] **CP-1b.5-1** planning / plan_checks / plan_review 三侧同步面逐条完成
- [ ] **CP-1b.5-2** ★`tests/test_sprint6_b1_plan_checks.py` 全绿，18 条返回值与落档基准**逐字节相同**
- [ ] **CP-1b.5-3** 全量非 e2e 回归：**1b 相关用例全绿**；红面（若有）⊆ §3.3 且逐条归因
- [ ] **CP-1b.5-4** 禁弱化自查通过
- [ ] **CP-1b.5-5** **文件边界自查**：`git status --porcelain` 生产侧**只有** `core/nodes/planning.py` / `core/plan_checks.py` / `ui/pages/plan_review.py` 三个文件；`state.py` / `execution.py` / `coding.py` / `reporting.py` / `term_map.py` / `graph.py` / `config.py` **逐一零改动**
- [ ] **CP-1b.5-6** `mypy` 清缓存后零错误

> **批次 1b 收口门**：CP-1b.1-\* ~ CP-1b.5-\* 全绿。🔴 **停手等 Maria 确认再开批次 2。**

---

## 6. 批次 2：验钞 + 四档判定 + 维度不坍缩（S8-04 / S8-05 / S8-06）🔴 **内部不得拆分**

> **前置条件**：批次 1a + 批次 1b **全部收口**。
> **产出**：**`<METRICS>` 通道整体退场（执行侧四函数删除 + 编码侧教学文本清除）** + **执行上下文注入** + `_resolve_agent_report` + `_verify_evidence` 五重验钞 + `_decide_conclusion` 四档判定 + 三条封顶 + 新错误类别 + `success` 由 `level` 派生 + 撞名两条都丢弃 + **BUG-S8-01**。
> **文件边界**：`core/nodes/execution.py`（**主体，单收口窗口，主控收口令**）+ **`core/nodes/coding.py`（仅 T-S8-2-1b）** + `tests/`（T-S8-2-12）。**十四任务串行。不碰** `state.py`（1a 已收口）/ `planning.py` / `plan_checks.py` / `reporting.py` / `ui/` / `graph.py` / `config.py`。
> 🔴 **PRD 明令批次 2 内部不得拆分**（R-S8-06）：档位判定与本篇标准**互为前提**——没有标准无从判档，没有判档标准写了也没人用。**十四个任务是一个交付单元，不得只交前几个。**
>
> 🔴 **v1.2 迁入两个任务**：`T-S8-2-1b`（编码侧改词，Maria 加拍）与 `T-S8-2-8b`（执行上下文注入，裁定 2）。**窗口不因此变长**——起点仍是 T-S8-2-1、恢复点仍是 T-S8-2-11，两个迁入任务都落在窗口内部。⇒ **批次 2 现在承载「一切会改变行为的事」。**
>
> 🔴 **本批含全 Sprint 唯一的不可用窗口**（Maria 2026-08-04 拍板 2 把它压进了本批内部）：
> - **窗口起点** = **T-S8-2-1**（三档解绑，本批首位任务，由批次 1a 迁入）；
> - **窗口终点 / 可用性恢复点** = **T-S8-2-11**（节点主体接线 + S7-13 门控废止）；
> - **窗口期间**：不得端到端真跑、不得对外演示、不得据此判断"哪里坏了"、不得为让回归变绿而回滚 T-S8-2-1（§0.0 全文）。
>
> 🔴 **T-S8-2-1 与 T-S8-2-8 是天然配对，中间不得留缝**：前者撤掉旧判据的分子（`metrics` 恒空），后者装上新判据（`success` 由 `level` 派生）。**解绑与取代必须同批完成** —— 这正是 R-S8-06「内部不得拆分」在本批内部的具体形态。**任何"先交前几个任务"的想法都会把系统停在一律判失败上。**
> **红线**：不新增 interrupt 种类 / 不改编排图 / 保 S-1 幂等（`_has_committed_result_for_round` **一字不改**）/ `_SandboxRunCollector` **一字不动** / `react_base.py` **一字不动** / `code_fs_tools.py` **一字不动** / `config.py` **零改动** / 状态契约**不得再加第三个键** / `:995` 与 `:1010` 两条硬防线一字不动 / `:2817-2840` 早停优先级链**顺序**一字不动 / `_reconcile_steps` / `_completion_insufficient` / `_audit_declared_steps` / `_collect_grouped_metrics` **四个函数一字不动**。

### 任务 T-S8-2-1：🔴 三档 + `_parse_metrics` 四个函数**整体删除** + `<METRICS>` 标签常量删除 + `llm_calls_used` 归零 —— ★**不可用窗口起点**

> **📌 迁移与裁定留痕（两轮）**：
> 1. 本任务在 v1.0 中编号 **`T-S8-1a-7`**（批次 1a 第 7 位）。**Maria 2026-08-04 拍板 2** 把它挪到**批次 2 首位**（现号 `T-S8-2-1`），把不可用窗口压进批次 2 内部。
> 2. 🔴 **v1.2 裁定 1（架构师裁定 + Maria 已认，2026-08-04）**：由「**解绑但保留**」**改为「四个函数整体删除**」——**推翻本计划 v1.0/v1.1 的默认取值 A**。结案论证见 **§16.C**。

- **产出文件**：`core/nodes/execution.py`（四个函数 + 标签常量 + 调用点 + 模块 docstring 两处）
- **依赖项**：**批次 1a + 批次 1b 全部收口**（本任务是批次 2 的首位任务）
- **预计复杂度**：中（删除本身简单，**但引用清零面是全 Sprint 最广的一次**）
- **架构参考**：架构 v2.1 §12 `execution.py` 第 2 条（**已同批改为"整体删除"**）+ §7 末段边界澄清 + §13 + PRD §4.2 第 4 条
- **⚠⚠ 本任务合入即开启 AR-S8-01 不可用窗口，见 §0.0。开工前必须再读一遍 §0.0。**
- 🔴 **配对约束**：本任务与 **T-S8-2-8**（`success` 由 `level` 派生）**是天然配对，中间不得留缝**——前者撤掉旧判据的分子，后者装上新判据。**批次 2 受 R-S8-06「内部不得拆分」约束**，本任务尤其不得单独交付。

**需要实现的内容**：

0. 🔴 **开工第一步（零生产改动，因本任务被前移到批次 2 首位而随之前移）**：先记下**冻结表 10 行的当前值**。理由：本任务会删掉冻结表里的三个函数 ⇒ 那三行必须移出；而记录冻结表基线的 T-S8-2-2 现在排在本任务**之后**，若不前移这一步，T-S8-2-12 的逐行对账当场失去参照。⚠ **其余基线（execution prompt 两处旧哈希等）不受本任务影响，仍由 T-S8-2-2 记录。**

1. 🔴 **四个函数整体删除**（落点已逐处上磁盘核实，2026-08-04）：

   | 函数 | 落点 | 备注 |
   |---|---|---|
   | `_extract_metrics_block` | `:402-423` | 档 1 结构化标签 |
   | `_regex_scan_metrics` | `:426-449` | 档 2 正则兜底 |
   | `_llm_extract_metrics` | `:452-514` | 档 3 LLM 抽取兜底 |
   | `_parse_metrics` + **调用点** | `:517-550` / **`:2935`** | 三档调度 |

2. 🔴 **`<METRICS>` 标签常量与 pattern 一并删除**（`:393-399`：`_METRICS_TAG_OPEN` / `_METRICS_TAG_CLOSE` / `_METRICS_TAG_PATTERN`）。**已核实其唯一消费者就是 `_extract_metrics_block`** ⇒ 留着即死代码。⚠ 连同 `:390-392` 那三行"步骤 4：metrics 三档解析"的分节注释一并订正。

3. 🔴 **模块 docstring 两处订正**（**本 Sprint 之后这两处全是假话**）：
   - **`:9-10`**（七步骨架第 4/5 步）：现文「4. `_parse_metrics` 三档解析（结构化标签 → 正则 → LLM 抽取兜底）；5. `_build_execution_result` B 档 success 判定（exit 0 且 ≥1 指标）」—— 两句**同批失真**（第 4 步的函数不存在了、第 5 步的判据换成四档派生）。
   - **`:26-27`**（治理范式）：现文「execution 主体不调 LLM（零扣减）；仅 metrics 档 3 LLM 抽取兜底触发时按实际次数单点回写 `retry_budget_remaining` + 累加 `_dev_loop_llm_calls`」—— 后半句所指的通道**已不存在**。
   - ✨ **附带红利（须在此处与交接文档各留一行）**：`_llm_extract_metrics` 是 `execution` 主体在 ReAct 子图**之外唯一的 LLM 调用入口**。删除后，「**执行主体不调 LLM**」这句从"目前恒成立"升级为"**结构上不可能不成立**"——这是本次删除白拿的一条不变量强化。

4. **`llm_calls_used` 归零**（⚠ **架构 §12 未提，本计划实测补出，见 §15 P-S8-6**）：原 `metrics, llm_calls_used = _parse_metrics(...)` 的第二返回值流向 `_map_execution_result(..., llm_calls_used=llm_calls_used)`（`:3016`）→ 预算扣减 `total_calls = react_rounds_used + llm_calls_used`（`:2533`）。四函数删除后该支路**恒 0**。
   - **实做**：`_map_execution_result` 的 `llm_calls_used` 形参**保留默认值 `0`、签名一字不动**，调用点**不再传该 kwarg**（或显式传 `0`，二选一，**在实现里注释说明选了哪个及为什么**）。
   - 🔴 **不得顺手改预算扣减公式**（`:2533`）——那是另一件事，本 Sprint 非目标。

5. **在删除处写一条注释**，逐字写明：`<METRICS>` 通道退场（Maria 决策 3，是废掉不是收窄）、`metrics` 现在的唯一来源、**以及"在 T-S8-2-8 装上新判据之前 `success` 会恒假、这是计划内的"**（**给后人看的**，防止有人在窗口期看到全判失败就来"修"）。

🔴 **边界澄清：以下三样不在删除面内，一并清零会当场打掉旧快照兼容与唯一的扫盘兜底**（架构 v2.1 §7 末段已同批补入）：

| 不删 | 理由 |
|---|---|
| `_collect_grouped_metrics`（`:1709`） | **有生产调用者**（`:2961` 扫盘兜底），且**数据源本 Sprint 被强化**（编码侧产出约定 T-S8-2-1b）；与三档不同类——三档的**输入源本批被同批拆除**，它的没有（架构 v2.1 §13 已补此句） |
| `ErrorCategory.NO_METRICS` 枚举成员（`:151`） | `_feedback_from_committed_result`（`:3026`）从旧 checkpoint 的 `[error_category=no_metrics]` **反序列化**重建；删成员 ⇒ 旧任务 resume 当场炸（架构 Q-S8-07） |
| `ui/term_map.py` 的 `error_category:no_metrics` | 旧报告仍要能渲染 |

⇒ **清零断言的对象是"四个函数及其全部引用"，不是枚举成员、不是扫盘兜底。**

**红线**：
- 🔴 **不得顺手把 `success` 的 `len(metrics) >= 1` 合取项一起改掉**——那是 T-S8-2-8 的事。
- 🔴 **不得为了让回归变绿而回滚本任务**（§0.0 第 4 条）。
- 🔴 `_collect_grouped_metrics` **一字不动**（架构 §13）。

**自测检查点**：
- [ ] **CP-2.1-0** ★**改前基线前移记录**（零生产改动）：冻结表 10 行当前值**现算落档**（T-S8-2-12 逐行对账的参照）
- [ ] **CP-2.1-1（★由 v1.1 的「调用链断言」升级换发）** ★★**符号不存在 + 全仓引用清零**：①四个函数在 `core/nodes/execution.py` 中**符号不存在**（`hasattr` 为假 / `ast` 扫模块顶层 def 无此四名）；②`_METRICS_TAG_OPEN` / `_METRICS_TAG_CLOSE` / `_METRICS_TAG_PATTERN` 同；③**全仓**（`core/` + `ui/` + `app.py`）四名**零引用**。⚠ **删除是 AC-S8-03「不再被判定链路调用」的加强版，不冲突**
- [ ] **CP-2.1-2（★v1.1 原条整条作废并换发）** ~~旧签名调用报 TypeError~~ → 换发为：**导入即失败自证** —— `from core.nodes.execution import _parse_metrics` **抛 `ImportError`**（证明是真删除，不是留了个空壳或别名）
- [ ] **CP-2.1-3** ★`llm_calls_used` 支路归零：`_map_execution_result` 收到的 `llm_calls_used` 恒为 `0`；预算扣减 `total_calls == react_rounds_used`（**本计划实测补出，架构没写，必须验**）
- [ ] **CP-2.1-4（★v1.1 原条整条作废并换发）** ~~三档函数本体逐字节未改~~ → 换发为：**模块 docstring 两处已订正** —— `:9-10` 不含"`_parse_metrics` 三档解析"、`:26-27` 不含"metrics 档 3 LLM 抽取兜底"（负向断言）；且**"执行主体不调 LLM"的表述已升级为结构性保证**（正向断言 + 元检查）
- [ ] **CP-2.1-4b** ✨**红利自证**：`core/nodes/execution.py` 中**除 ReAct 子图装配外零 LLM 调用入口**（`ast` 扫：无 `invoke_with_retry` / `with_structured_output` / LLM client 的直接调用点，除 `_run_execution_agent` 的子图装配路径外）
- [ ] **CP-2.1-5** ★**不可用窗口如实开启**（不是"验证它坏了"，是**验证它按预期地坏**）：构造"exit 全 0 + 步骤跑完 + agent 自报 1 个主实验指标"→ `metrics == {}`（门控不采信）+ `success is False` + WARNING 已打。**这条 CP 在 T-S8-2-11 之后期望值会翻转**，届时换发（沿 sprint7 §49.0「删原内容→换发新 CP」先例）；**配对翻转 CP = CP-2.11-2**
- [ ] **CP-2.1-6** 删除处注释含窗口告示的指路文字（元检查，防后人误"修"）
- [ ] **CP-2.1-7** ★**边界澄清三样均未被误删**：`_collect_grouped_metrics` **逐字节未改**且 `:2961` 调用点仍在；`ErrorCategory.NO_METRICS` 成员仍在（构造旧 `errors` 前缀 → `_feedback_from_committed_result` 不抛）；`ui/term_map.py` 的 `error_category:no_metrics` 仍在
- [ ] **CP-2.1-8 逐条验红**：①把四个函数中任一个加回去并接进 `execution()` → CP-2.1-1 红；②留一个同名空壳（`def _parse_metrics(...): return {}, 0`）→ CP-2.1-2 **仍须红**（证明断言的是"符号不存在"而不是"调用链不通"）；③顺手删掉 `_collect_grouped_metrics` → CP-2.1-7 红；④顺手删掉 `NO_METRICS` 枚举成员 → CP-2.1-7 红

### 任务 T-S8-2-1b：coding 侧三处 `<METRICS>` 清除 + 产出约定 + 上下文补两键

> **📌 迁移留痕（v1.2，Maria 2026-08-04 加拍）**：本任务原为 **`T-S8-1a-4`**（批次 1a）。**Maria 加拍迁入批次 2**，置于 **T-S8-2-1 之后**。
>
> **理由（架构师新识别 1）**：清掉三处 `<METRICS>` 教学文本后，编码 agent **不再被告知打标签** ⇒ **档 1 从批次 1a 起就实际失效**；而此时解绑（T-S8-2-1）尚未做、`success` 仍要求 `len(metrics) >= 1` ⇒ **批次 1a 之后判失败概率显著上升**，拍板 2 买到的「1a 可演示」**名不副实**。迁入批次 2 后，1a 期间教学文本**原样保留** ⇒ 档 1 照常有效 ⇒ 系统**真**可用。
>
> 🔴 **不得排在 T-S8-2-1 之前**——否则它自己就成了新的不可用窗口起点。
>
> 🔴 **T-S8-1a-3（新建 coding 侧字节门）留在批次 1a 不动**：它**零生产改动、零行为变化**，先建好等在那里。⇒ **「先建后改」的强制次序由「同批建门→改词」改为「批次 1a 建门 → 批次 2 改词」，跨批次仍然成立且更稳**（门在批次 1a 收口时就已经是**全绿基线**，中间隔一个批次边界不影响它守什么）。**不要以为门和改词必须同批。**

- **产出文件**：`core/nodes/coding.py`
- **依赖项**：**T-S8-2-1**（不得排在其前）+ **T-S8-1a-3（字节门须在批次 1a 已建好并绿）** + T-S8-1a-2（`success_criteria` 键名）
- **预计复杂度**：中
- **架构参考**：§12 `core/nodes/coding.py` 条目 + PRD §4.2

**需要实现的内容**：

1. 🔴 **三处 `<METRICS>` 教学文本全部清除**（PRD §4.2 明写"只删中间一段会留下两处仍在教 agent 写标签的文本"）：
   - `:113` —— `entry_script` 的**结构声明 description**（"（运行后末尾打印 `<METRICS>{...}</METRICS>`）"），**每回合都进提示词**；
   - `:181-186` —— "入口脚本指标输出约定（强约束，下游执行节点依赖）"**整段 6 行**；
   - `:191` —— 修复回合那句"保持入口脚本的 `<METRICS>` 输出约定不变"。
   > ⚠ **`:113` 在 `CODING_OUTPUT_SCHEMA` 里，不在 prompt 主体里** —— 它不影响主体哈希，但它**确实每回合进提示词**（schema 经 `with_structured_output` 下发）。三处的落点性质不同，**不要因为改主体时没看见 `:113` 就漏掉它**。
2. **补上取代它的产出约定**（PRD §4.2 第 2 条 + 架构 §12）：每组实验跑完写结果文件（`summary.json`），**落在计划声明的位置**；**结构由编码环节自己定**（Maria 决策 4），只要求**合法 JSON、顶层是对象**。
   - 🔴 **不得写成"字段必须叫什么"**——那是回到"代码猜产物长什么样"的老路（sprint7 §60.0 已整条作废那条路线）。
   - 写法沿 `<METRICS>` 段被实证有效的三要素结构（**格式 + 例子 + 边界**，sprint7 真跑实测该写法服从度 9/9）：格式（顶层 JSON 对象）+ 一份最小样例 + 边界（放在 `expected_output` 指定的相对路径下；没有可写的就不写，不要写空文件）。
3. **上下文补两键**（今天 `coding.py` 对两者**均零命中**，§1.5 事实 3）：
   - `expected_results` —— 定性物证的生产者；不补则编码环节**根本不知道这次要拿出什么证据**；
   - `success_criteria` —— 让编码环节知道"这次要拿出什么才算成功"；
   - **两者均"非空才注入"**（架构 §2.5.5 + §12），沿 execution 侧 `credential_degradations` / `scale_reduced_directive` / `expected_results` 三处既有先例。⇒ 无该数据的旧计划下 payload 与基线**字节零扰动**。
   - ⚠ **此时 `success_criteria` 还没有生产者**（生产者在批次 1b 的 T-S8-1b-2）⇒ 读到空 ⇒ 不注入 ⇒ 字节零扰动，**不会因为字段没人写而出错**（架构 §11 前置①逐字如此）。
4. **字节门基线换发**（三件套的第②③件；第①件"记旧哈希"在 T-S8-1a-1、**门本身在 T-S8-1a-3 已建好并绿**）：
   - 🔴 **改 prompt 的第一件事，是先跑一次 T-S8-1a-3 建的门，确认它当场变红。** 这一红**不是故障**，它是"门有牙"的活体证明（sprint7 T-S7-6-2「先建后改」范式）。**没红 = 门是假的，停手回查 T-S8-1a-3。**
   - 重算新哈希并**写死为字面量基线**替换旧值——🔴 **严禁改成 `EXPECTED = actual` 的自锁定形态**（R-S7-41 留档：planning 那道门曾以此形态**零守门能力**地存在了两个 sprint）。
   - 在本 dev-plan §15.1 把新哈希 + 新长度 + 变更原因补进 T-S8-1a-3 留下的那一行（三件套第③件闭合）。
   > ⚠ **分工**：正式测试文件的最终归属交测试工程师（§0.3 第 6 条）。但**这道门必须在 T-S8-1a-3 就真实存在并可跑**——否则"先建后改"无从谈起。开发侧先落一个可跑的门（位置建议与 `tests/test_sprint5_t13_coding_prompt.py` 同族或新建 sp8 文件），测试工程师后续可搬家但**不得降级为自锁定形态**。

**红线**：
- 🔴 新增文本必须是**纯静态文案**：零 `arxiv_id` / 论文标题 / 路径变量 / 时间戳（已知 bug 模式 #4；`coding.py:143-149` 的既有注释已明写主体是 SystemMessage 稳定前缀、严禁插入论文级/任务级动态变量）。
- 🔴 **不得在编码提示词里教"怎么写才不被审计命中"**（PRD 非目标 5 / §4.9.2 三做一不做的"一不做"）。
- `CODING_OUTPUT_SCHEMA` 除 `:113` 那句 description 外**其余字段一字不动**；`create_react_subgraph(..., result_schema=CODING_OUTPUT_SCHEMA)`（`:894`）不动。

**自测检查点**：
- [ ] **CP-2.1b-1** **三处清除逐处验**（AC-S8-03 负向）：`grep -n "METRICS" core/nodes/coding.py` **零命中**；且分别断言 `:113` description、`:181-186` 段、`:191` 那句**三处各自不存在**（**逐处断言，不用一条全局 grep 代替**——全局 grep 过了不代表三处都清了，可能是清了一处另两处被改写成别的写法）
- [ ] **CP-2.1b-2** 产出约定三要素齐全（AC-S8-04 提示词面）：主体含 `summary.json` 字面量 + "顶层"对象约束措辞 + 至少一个 JSON 例子 + "落在计划声明的位置"这层边界
- [ ] **CP-2.1b-3** **负向：不得规定字段名**（防复发"代码猜产物"路线）：新增段落中不出现任何具体指标字段名的强制要求措辞（人工审 + 关键词负向断言）
- [ ] **CP-2.1b-4** 上下文补两键（AC-S8-04 上下文面）：计划含 `expected_results` / `success_criteria` 时，`_build_coding_context`（或等价上下文构造点）产出的 payload **含这两键**
- [ ] **CP-2.1b-5** ★**字节零扰动**：两键均缺 / 均为空 / 一有一无 三种形态下，payload 与改前基线**逐字节相同**（`json.dumps(sort_keys=True)` 比对）
- [ ] **CP-2.1b-6** **Prompt Cache 幂等**（已知 bug 模式 #4）：正则 `\d{4}\.\d{4,5}` 在主体零命中；两次不同 state 下 `_build_coding_system_prompt(...)` 的**主体部分逐字节相同**（沿 `test_paper_analysis_e2e.py` 的"截 SystemMessage 去尾部段落后比较"范式）
- [ ] **CP-2.1b-7** ★★**先建后改的活体证明**：改 prompt **之前**先跑一次 T-S8-1a-3 建的门 → **实测变红**并打出新旧两个哈希，**红的证据落档**。🔴 **没红就停手回查 T-S8-1a-3**——那说明门是假的（自锁定或守错了对象），此时继续改 prompt 等于门白建
- [ ] **CP-2.1b-8** 字节门基线换发闭合（三件套第②③件）：新哈希 + 新长度算出并**写死为字面量**；断言右侧仍是硬编码字面量（元检查复跑 CP-1a.3-2）；门**复绿**；§15.1 那一行补齐新哈希
- [ ] **CP-2.1b-9** **逐条验红**（`cp` 备份 → 改坏 → 记录 → `cp` 还原 → `sha256sum -c` → 复绿）：①主体再插一个空格 → 字节门必红并打出新旧哈希；②删掉新增产出约定段 → CP-2.1b-2 红；③把例子换成含 `arxiv_id` 的动态形态 → CP-2.1b-6 红；④删掉 `success_criteria` 注入 → CP-2.1b-4 红

### 任务 T-S8-2-2：实现前核实 + **旧基线先记后改**（**零生产改动**）

- **产出文件**：`/tmp` 自测脚本 + §15.1 基线留档
- **依赖项**：批次 1a + 1b 收口
- **预计复杂度**：低（**但不做等于后面三道门全白建**）

**需要做的事**：

1. `git status --porcelain` 现查留痕；确认批次 1a / 1b 的改动已全部合入且工作区无第三方改动。
0. ⚠ **冻结表 `_parse_metrics` 一行的改前值已由 T-S8-2-1 / CP-2.1-0 前移记录**（原因见该任务第 0 条）；本任务负责其余基线。
2. 🔴 **execution prompt 主体旧哈希先记**（两处门：`test_sprint5_t14_execution_prompt.py:219` + `test_sprint7_s710_exec_locality.py:653`）——T-S8-2-9 要改它，**不先记就没有"改前"这个参照**。同时**核实两处断言右侧是不是硬编码字面量**（sprint7 P-27 记着 execution 侧曾有过 `assert head == _EXECUTION_SYSTEM_PROMPT_BODY` 这种常量与自身比的假门；S7-10 的 T-S7-6-2 已改造，**复核它没退化回去**）。
3. 🔴 **函数源码字节冻结表 10 行的当前哈希全部现算落档**（`tests/test_sprint7_s713_reported_metrics.py:994-1005`）——T-S8-2-12 要逐行对账，**没有改前值就对不了账**。
4. **核实 §1.5 事实 7~12 与 24~27**（`llm_calls_used` 支路 / 撞名代码真实位置 / BUG-S8-01 现场 / `react_base` 回读通道 / `_build_execution_agent_context` 形参数）。
5. **核实 `EXECUTION_OUTPUT_SCHEMA`（`:1092`）的 `required` 列表**——AR-S8-07 要求新增字段**一律不列 required**，须知道现在 required 里有什么。
6. **核实早停优先级链 `:2817-2840` 的 elif 顺序**并逐行抄录落档（T-S8-2-7 要"原位继承"，改动前后要能逐行比对证明**顺序一字未动**）。

**自测检查点**：
- [ ] **CP-2.2-1** 工作区干净性现查；批次 1a/1b 改动已合入自证
- [ ] **CP-2.2-2** ★execution prompt 两处门的旧哈希现算落档 + **断言右侧是字面量**的元检查（防退化回假门）
- [ ] **CP-2.2-3** ★冻结表基线**复核**（10 行中 `_parse_metrics` 那行的改前值已由 **CP-2.1-0** 前移记录；其余 9 行本任务未受影响，在此现算落档并与 CP-2.1-0 的记录对齐）
- [ ] **CP-2.2-4** §1.5 事实 7~12 / 24~27 逐条复核，不符者回填 §15
- [ ] **CP-2.2-5** `EXECUTION_OUTPUT_SCHEMA.required` 现值落档
- [ ] **CP-2.2-6** 早停优先级链 `:2817-2840` elif 顺序逐行抄录落档

### 任务 T-S8-2-3：四档名模块常量 + `_LEVELS` 顺序元组 + `ErrorCategory.NO_VERIFIABLE_OUTPUT`

- **产出文件**：`core/nodes/execution.py`（`ErrorCategory` `:132-157` / `AUTO_FIXABLE` `:161-169` / 新增四个模块级常量）
- **依赖项**：T-S8-2-2
- **预计复杂度**：低（**但 Q-S8-07 那条不删枚举成员的纪律是命门**）
- **架构参考**：§2.3（档名一套值）+ Q-S8-04 §4.1 + **Q-S8-07 §7**

**需要实现的内容**：

1. **四个模块级档名常量**（架构 §2.3）：`_LEVEL_SUCCESS = "复现成功"` / `_LEVEL_PARTIAL = "部分复现"` / `_LEVEL_CODE_ONLY = "仅代码跑通"` / `_LEVEL_FAILED = "失败"`。
   - 🔴 **落盘的字面量就是这四个中文串**。**不引入 `ConclusionLevel` Enum、不引入 `"success"/"partial"` 之类的英文内部值**（A-S8-05 / 反过度工程）。
2. **`_LEVELS: Tuple[str, ...]` 顺序元组**，**从高到低**：`(_LEVEL_SUCCESS, _LEVEL_PARTIAL, _LEVEL_CODE_ONLY, _LEVEL_FAILED)`。
   - 🔴 **封顶 = 按元组下标取更低档，不写 if 链**（架构 §2.3）——这**同时天然满足 AC-S8-09④「只压低不抬高」**。这一条要写进常量旁的注释，它是全 Sprint 最容易被"优化"成 if 链的地方。
3. **`ErrorCategory.NO_VERIFIABLE_OUTPUT`**（Q-S8-04）：
   - 加成员 + **并入 `AUTO_FIXABLE`**（`auto_fixable=True`——产出没落地正是编码环节能修的）；
   - 用户可见文案："跑通了，但计划里说好要产出的东西没落地"（架构 §4.1）。**提为模块级具名常量**（summary / fix_hint 两条，沿 `_INCOMPLETE_EXECUTION_SUMMARY_LEAD` / `_INCOMPLETE_EXECUTION_FIX_HINT` 既有范式）**并登记进术语守门候选**（交 T-S8-3-10 账目）。
   - 🔴 **不复用 `NO_METRICS`**：`execution.py:152-156` 已为 `INCOMPLETE_EXECUTION` 写死过同款理由的三条——①会被无进展早停误伤；②fix_hint 指错方向；③**「对用户撒谎比技术债更贵」**。
4. 🔴 **`ErrorCategory.NO_METRICS`（`:151`）成员必须保留**（架构 Q-S8-07）：
   - 理由：`_feedback_from_committed_result`（`:3026`）从已落盘 `ExecutionResult.errors[0]` 的 `[error_category=xxx]` 前缀**反序列化**重建 `ErrorCategory`。旧 checkpoint 里存着 `error_category=no_metrics` 的字符串，**删成员会让旧任务 resume 当场炸**。
   - 加注释：「Sprint 8 起无生产者，仅供旧 checkpoint 反序列化」；
   - `AUTO_FIXABLE` 中的**归属不动**；`ui/term_map.py` 的 `error_category:no_metrics` 文案**保留不删**（旧报告仍要能渲染，落在 T-S8-3-8）。
   - ⚠ **这条同时是 AC-S8-18「`_apply_no_metrics` 已删除且无残留引用」的边界澄清**：清零断言的对象是**函数与其调用点**，**不是枚举成员**。写测试时若把枚举成员一并清零，会当场把旧快照兼容打掉。

**自测检查点**：
- [ ] **CP-2.3-1** 四个档名常量取值**逐字等于**四个中文串；`_LEVELS` 顺序**从高到低**且长度为 4
- [ ] **CP-2.3-2** ★**负向：无第二套值**（AC-S8-08 同族）：`execution.py` 中**不存在** `ConclusionLevel` Enum，也不存在 `"success"` / `"partial"` / `"code_only"` 之类的档位英文内部值
- [ ] **CP-2.3-3** `ErrorCategory.NO_VERIFIABLE_OUTPUT` 存在且**在 `AUTO_FIXABLE` 里**；文案已提为模块级具名常量、通俗中文、零内部字段名
- [ ] **CP-2.3-4** ★**`NO_METRICS` 成员仍在**（Q-S8-07）：构造 `errors=["[error_category=no_metrics] ..."]` 的旧形态 `ExecutionResult` → `_feedback_from_committed_result` **不抛异常**且重建出 `ErrorCategory.NO_METRICS`
- [ ] **CP-2.3-5** `ErrorCategory` 既有成员集合**零删除**（负向：改前成员 ⊆ 改后成员）
- [ ] **CP-2.3-6 验红**：删掉 `NO_METRICS` 成员 → CP-2.3-4 **必红**（把"删了会炸"实测一遍，留档）

### 任务 T-S8-2-4：`_resolve_agent_report` + `ExecAgentOutput.report`（Q-S8-01，最硬的一项）

- **产出文件**：`core/nodes/execution.py`（新纯函数，**紧邻 `_merge_with_collector`（`:1517`）放置，共用同一段范式注释**；`ExecAgentOutput`（`:1186`）加字段；`_run_execution_agent`（`:1551`）收尾改调）
- **依赖项**：T-S8-2-3
- **预计复杂度**：中
- **架构参考**：§1 全节（Q-S8-01）

> **先把矛盾拆准**（架构 §1.1 裁定 1，落地时最容易做反的一件事）：**判定天然不是累积型数据。** `_SandboxRunCollector` 治的是"保真度差"（收集器全文 > 回读尾部）；判定这边**没有这个保真度差**——回读的是同一份 JSON 文本的同一份字节，只有"在不在"的差别，没有"全不全"的差别。
> ⇒ 🔴 **档位 / 逐条结论 / 物证清单一律不进 `_SandboxRunCollector`**。把判定塞进收集器**不但拿不到额外保真度，反而会把一个"终态一次写"的数据降级成"累积型"，从而主动获得收集器的前半段丢失面**——那正是 Q-S8-01 最需要避免的结果。

**需要实现的内容**：

1. **新纯函数 `_resolve_agent_report(final_state, final_messages) -> Dict[str, Any]`**，docstring **逐字写明与 `_merge_with_collector` 的方向镜像关系**（架构 §1.3 已给出原文，照抄）：
   - `_merge_with_collector` 治"保真度差" ⇒ **收集器优先**；
   - 本函数治"存在性差"（两边字节同源、无截断差）⇒ **子图 `result` 优先**，缺失/空/必填不全时用 messages 末条 `<result>` 回读补位；
   - 两条都拿不到 → 返回 `{}`，由调用方走封顶（**绝不因此判失败**）。
2. **优先级**：`final_state["result"]` 是 dict 且非空 → 直接采用；否则**逆序**扫 `final_messages` 找**最后一条**含 `<result>...</result>` 的 `AIMessage`，`json.loads` 解析。
3. 🔴 **写死三条防"假绿通道"**（AR-S8-02）：**只认 `<result>` 标签包裹**、**只取最后一条**、**解析失败即空**。不得放宽到"任意 AIMessage 里的 JSON 块"。
4. 🔴 **禁静默吞错**（已知 bug 模式 #3 / 架构 §1.3）：
   - 存在 `<result>` 标签**却一条都解析不出**时打 **WARNING**；
   - **两条通道都空时也打 WARNING** —— ⚠ **这一条与 `reported_metrics` 的"零指标不打 WARNING"相反**：**档位缺失不是合法常态**。这个反差要写进注释，否则后人会照 `reported_metrics` 的先例把它"统一"掉。
5. **零新依赖**：正则复用 `config.REACT_RESULT_TAG_OPEN/CLOSE`（`config.py:61-62`），execution 侧**自建一个模块级 pattern**，🔴 **不 import `react_base._RESULT_TAG_PATTERN` 这个私有符号**（与 `reporting._resolve_report_path` 自写边界判定同一取向）。
6. **`ExecAgentOutput` 扩一个字段** `report: Dict[str, Any] = field(default_factory=dict)`（**有默认值** ⇒ 降级路径 `:1638` 与既有构造点天然为空，与 `reported_metrics` 的加法逐字同款）。
7. **`reported_metrics` 改为从 `report.get("metrics")` 取**，**不再单独读 `final_state["result"]`** —— 消除两个取数口径。
8. 🔴 **`react_base.py` 一字不动**：`force_finish` 的 schema 成功分支**已经**把结果同步写了一条 `<result>` 包裹的 AIMessage（`:665-672`，§1.5 事实 24 已核）⇒ messages 通道天然携带同一份判定。

**自测检查点**（对应架构 §1.6 的 V1~V7；V8 真跑在批次 5）：
- [ ] **CP-2.4-1（V1）** 结构性回读兜底：`final_state` 无 `result`、messages 末尾带 `<result>{...}</result>` → 取出完整档位（覆盖架构 §1.2 的缺失路径 (b)(c)）
- [ ] **CP-2.4-2（V2）** 优先级：两通道都有且**内容不同** → 取 `final_state["result"]`（单一权威）
- [ ] **CP-2.4-3（V3）★★命门** **收集器截断不改判定**：同一份 messages 跑两遍——①收集器满载 ②收集器只留尾段（模拟 R-S4-10 resume）→ **两次 `conclusion.level` 逐字相同**。**这条直接证否"一丢就变失败"**（建议直接用既有真跑夹具 `tests/fixtures/s713_realrun_20260802/` 重放，与 AC-S8-19 共用夹具、不新建现场）
- [ ] **CP-2.4-4（V7）** 异常降级：子图抛非 `GraphBubbleUp` 异常 → 不炸节点、`report={}`
- [ ] **CP-2.4-5** ★**禁静默吞错**：①有 `<result>` 标签但内容非法 JSON → WARNING 已打；②两通道皆空 → WARNING 已打；③正常路径 **不打** WARNING（避免噪声）
- [ ] **CP-2.4-6** ★**防假绿通道**（AR-S8-02）：①非 `<result>` 包裹的 JSON 块**不被采信**；②有多条 `<result>` 时**只取最后一条**；③解析失败返回 `{}` 而非部分结果
- [ ] **CP-2.4-7** `reported_metrics` 改从 `report` 取后，四种畸形形态（`result=None` / 无 `metrics` 键 / `metrics` 非 list / `result` 非 dict）**逐条降级空数组且不炸**（S7-13 既有行为不退化）
- [ ] **CP-2.4-8** ★**`_SandboxRunCollector` 逐字节未改**（含 `:812-817` 的 R-S4-10 注记）；`react_base.py` 逐字节未改（`git diff` 为空自证）
- [ ] **CP-2.4-9** **不 import 私有符号**：`execution.py` 中不出现 `_RESULT_TAG_PATTERN` 的跨模块 import
- [ ] **CP-2.4-10 逐条验红**：①把回读放宽到"任意 AIMessage 里的 JSON" → CP-2.4-6① 红；②把"取最后一条"改成"取第一条" → CP-2.4-6② 红；③把两通道皆空的 WARNING 去掉 → CP-2.4-5② 红

### 任务 T-S8-2-5：`_verify_evidence` **七重验钞**（产物侧五重 + **论文值侧两重**）（S8-04，AC-S8-06 命门）

- **产出文件**：`core/nodes/execution.py`（新纯函数）
- **依赖项**：T-S8-2-4
- **预计复杂度**：**高**
- **架构参考**：Q-S8-03 §3 全节 + PRD §4.4

**需要实现的内容**：

> **📌 v1.3 扩围留痕（架构 v2.4 §16.3.2 / AR-S8-14，Maria 已认）**：由**五重**扩为**七重**。新增的是**论文报告值侧两重** —— 架构 v2.2 曾裁「论文值不进台账、不参与验钞」，**v2.3 自我推翻**：「不按产物文件验」是对的，「那就不验了」是错的，中间漏了「**换个东西验**」。**不验的代价是 AR-S8-14：agent 把论文值报低，自己跑出来的数就"对上了"。**

🔴 **形参多一个 `baseline_results`**（架构 §12）：`_verify_evidence(evidence_item, code_output_dir, extra_commands, baseline_results)`。调用方从 `state["paper_analysis"]` 取——这是 execution 侧对 `paper_analysis` 的**第二个**消费点（第一个是 T-S8-2-8b 的上下文注入），**两处取同一字段，不新增状态读取面**。

🔴 **按「出处」二选一，各走各的核验**（`path` 与 `metric` **互斥且必居其一**；两者都有 / 都无 ⇒ 该条不成立 + WARNING，**畸形不静默吞**）：

| 出处 | 记录形态 | 核验 |
|---|---|---|
| **本次跑出来的** | `{path, value?, source_note?}` | **产物侧五重**（下表①~⑤） |
| 🔴 **论文报告的** | `{metric, value, source_note?}` | **论文值侧两重**（下表⑥⑦） |

⚠ **这不违反 AC-S8-08②**（PRD v4.1 已加边界澄清，**不写清测试会把正当实现判红**）：AC-S8-08② 禁的是按**证据内容形态**（数值 / 趋势 / 定性）分支——那是病③的根因；**按「出处」二选一不在禁列**——出处决定的是"拿什么去核对它"，不是"这篇论文属于哪一类"，两种出处对**所有**论文同时存在，**不会让任何一类论文结构性拿不到高档**。且它**只落在 `_verify_evidence` 里**：🔴 **AR-S8-04 红线一字不动——`_decide_conclusion` 仍只读 `level` + 数封顶，不读出处、不读形态。**

`_verify_evidence(...) -> (ok, reason)`，**该出处对应的各重全过才采信；任一不过，该条判断不成立**：

| 重 | 判据 | 实现要点 |
|---|---|---|
| ① | **路径真实存在** | — |
| ② | **文件可读** | — |
| ③ | **数值能在该文件里查到** | 🔴 **前缀匹配**（复裁 8）：`0.6201` 可匹配 `0.62014732` |
| ④ | 🔴 **路径落在 `code_output_dir` 之下** | **4 行自写内联判断**（见下），**不复用工具层符号** |
| ⑤ | **路径未以字面量出现在本回合任何一条计划外命令的参数里** | 数据源 `step_reconciliation["extra_commands"]`（`:2004` 已产出）；口径 = **字面子串包含** |
| 🔴 ⑥ | **（论文值侧·新）`metric` 能在 `state["paper_analysis"]["baseline_results"]` 里查到** | **精确匹配，仅大小写与首尾空白不敏感，此外一字不差**。🔴 **绝不做归一化模糊匹配**——`_normalize_group_key`（`reporting.py:130-133` 的 `re.sub(r"[^a-z0-9]+", "_", …)`）+ `_match_metrics_group` 那套**正是 S7-13 真跑挖出的歧义源**，本 Sprint 正在删它们，**不能在隔壁重建一个同型物**。不做模糊匹配**零成本**：`baseline_results` 原键名**已经在 agent 眼前**（整份 dict 注入、`json.dumps(sort_keys=True)`），提示词写死"用原键名"。**归一后多个候选键同时命中 ⇒ 判歧义、不成立 + WARNING、不做任何 tie-break** |
| 🔴 ⑦ | **（论文值侧·新）`value` 与该键的值双向前缀匹配** | `"0.62"` 与 `0.6201` **互相**成立。**严格相等不可取**——浮点字符串化（`0.62` vs `0.6200000000000001`）会大面积误伤 |

1. **第④重的实现 = 4 行自写**，与 `reporting._resolve_report_path`（`:371-372`）、`code_fs_tools._is_within_base`（`:82-91`）**同一判定路径**：
   ```
   resolved = Path(candidate).resolve(); base = Path(code_output_dir).resolve()
   ok = (resolved == base or resolved.is_relative_to(base))
   ```
   - ❌ **明确否决**方案 B（`from core.tools.code_fs_tools import _is_within_base`）：跨模块 import 私有符号，且会造成"改工具层边界会连带改判定"的隐性耦合——**恰恰是本项最要提防的事**。
   - 🔴 **两个闸物理分处两文件，不可能被合成一个**（架构 §3.3，与 T-S8-1a-4 表同一份，须逐字进交接文档）。
2. **第⑤重只查计划外命令**（PRD §4.9.5 措施 3）：**计划步骤写出的文件完全不受影响** ⇒ 正常复现零误伤。
3. 🔴 **IO 异常全程 try/except**（AR-S8-03）：异常 ⇒ 该条判**不成立**（**保守方向，不是"放行"**）+ WARNING。
3b. 🔴 **`baseline_results` 为空 ⇒ agent 报的任何论文值物证一律不成立，且这条零误伤**（架构 §16.3.2 第 3 条，理由是**结构性**的）：`_build_execution_agent_context` **只注入 `baseline_results`，不注入整个论文分析、更没有论文原文**（A-S8-07）⇒ **agent 手上唯一的合法论文值来源就是那份注入**；注入为空时它**没有任何合法途径**知道论文报了什么 ⇒ 此时报出来的数**只可能是编的**。
   - ✨ **附带收获（必须写进代码注释）**：**A-S8-07「只送 `baseline_results`」从"反过度工程"升级为一条防线** —— 正因为不送论文原文，"报的值必须对得上注入"才是**完备**核验。**日后若有人为了"让 agent 看得更全"把整份 `paper_analysis` 塞进去，会在毫无察觉的情况下把这条核验掏空。**
   - **`baseline_results` 非空但不含该键** ⇒ 同样不成立，reason 用「论文分析里没有这个指标的报告值」。🔴 **文案必须中性**——这不是造假指控，是"无从核对"（同审计文案的中性要求）。
3c. 🔴 **对不上时：标注，不封顶——不新增第四条封顶**（架构 §16.3.2 第 2 条，取向与 §2.5.6 逐字同源）。该条 `ok=false` 自动落进**两个既有出口**：①引用它的**逐条结论落「无法核实」**（PRD §4.8 第 3 条保守出口）；②**档位的支撑物证若全不成立 → 既有封顶 3「仅代码跑通」**。⇒ **AR-S8-14 那条路被堵在"逐条结论"这一层，零新机制。** 🔴 **开发不得另写一条"论文值对不上则降档"的分支**——既有两个出口已完全覆盖，写第二处必然打架。
3d. 🔴 **`value` 为 `None` 时第③重不适用，其余四重照跑**（架构 §16.3 第 3 条）：这是**定性物证的正路**——"图产出了、文件存在且可读"本来就没有数值可查（AC-S8-12 的构造前提）。**不是漏洞**：无数值的物证支撑不了数值主张，而它能支撑的定性主张正是本 Sprint 要让它支撑的。**这条必须写进代码注释**，否则开发要么让它崩、要么让它无条件通过。
3e. 🔴 **本函数同时是证据台账的建账点**（架构 §16.3.1 方案 A）：**id 由系统生成，agent 一个 id 都不写**。按去重键 `(("P", path) 或 ("B", metric), value)` 去重成台账、按**首次出现顺序**分配 `E1`/`E2`…、**逐条只验钞一次**。`source_note` **不进键**，同键**首见优先**。⇒ **悬空 id 与 id 撞车在结构上不可能发生**（R-S8-23 不是"被缓解"，是"不存在"）。**台账顺序不排序**（先逐条结论、后结果块，固定遍历序）。
4. 🔴 **验钞机的能力边界不许对外含糊**（PRD §4.4 第 5 条 / R-S8-01）：核验**能验物证真伪**，**验不了"这些结果够不够格叫复现成功"**——那一步由 agent 照计划标准判。这句要写进函数 docstring。
5. 🔴 **不得读 `success_criteria`**（§0.5 红线 2）。

**自测检查点**（AC-S8-06 **须验红**）：
- [ ] **CP-2.5-1** 五重**逐重构造用例**：①路径不存在 → 不成立；②不可读（权限/目录）→ 不成立；③数值查不到 → 不成立；④路径越出 `code_output_dir` → 不成立；⑤路径出现在计划外命令参数里 → 不成立
- [ ] **CP-2.5-2** 五重**全过** → 成立（正向路径存在，不是只会说不）
- [ ] **CP-2.5-3** ★**前缀匹配口径**（复裁 8）：`0.6201` 能匹配文件里的 `0.62014732`；反向（`0.62014732` 对文件里的 `0.6201`）**不匹配**（前缀是单向的，这一条最容易实现成双向）
- [ ] **CP-2.5-4** ★★**两个闸同时为真**（AC-S8-05④ + AC-S8-04，架构 §3.3 明令"两条一起才叫验完"）：**正向**——`read_code_file` 读参考仓库**成功**；**负向**——引用参考仓库路径作物证**不成立**
- [ ] **CP-2.5-5** 第⑤重只查计划外命令：同一路径出现在**计划步骤命令**里 → **仍成立**（零误伤）；出现在 `extra_commands` 里 → 不成立
- [ ] **CP-2.5-6** IO 异常 → 该条**不成立** + WARNING（**保守方向**，不是放行）
- [ ] **CP-2.5-7** ★**负向：不读 `success_criteria`**（§0.5 红线 2 / AC-S8-08②）：`_verify_evidence` 函数体内 `success_criteria` 零出现（`ast` 静态审查）
- [ ] **CP-2.5-8** `code_fs_tools.py` 逐字节未改（`git diff` 为空）；`execution.py` 中不出现 `_is_within_base` 的跨模块 import
- [ ] **CP-2.5-10（B18）★★命门·须验红** **把对照基准编低 → 判不成功**（AR-S8-14）：同一份产物 + 达标线「数值与论文报告对上」+ `baseline_results = {"knn_accuracy": 0.95}`，agent 报 `{metric: "knn_accuracy", value: "0.61"}` 并自称「印证上了」 ⇒ 该条 `ok=false` ⇒ 逐条结论落「**无法核实**」⇒ **拿不到「复现成功」**；报告中性标注。**验红：让论文值物证无条件通过 → 本用例必红**
- [ ] **CP-2.5-11（B19）** **无原料时不许编**：`baseline_results` 为空 / 不含该键、agent 仍报论文值物证 ⇒ `ok=false` + **中性** reason；⚠ **另一向**：`baseline_results` 为空且 agent **没报**论文值 ⇒ **零告警、零标注**（条件句语义，没原料不做不算错）
- [ ] **CP-2.5-12（B20）** **键名精确匹配、歧义不猜**：①大小写 / 首尾空白不同 → 命中；②归一后多个候选同时命中 → **判歧义、不成立 + WARNING、不做 tie-break**；③少一个字符 → 不命中。🔴 **静态审查：`_verify_evidence` 内不得出现 `re.sub(r"[^a-z0-9]+"…)` 那类归一化模糊匹配**（防在隔壁重建 `_normalize_group_key`）
- [ ] **CP-2.5-13（B10）★命门** **台账不漂移**：同一条 `(path, value)` 同时被 1 条 goal_check 与 2 个块引用 ⇒ 台账**只有一条**记录、**验钞只跑一次**（调用计数断言）、三处 `evidence_ids` 指向**同一个 id**
- [ ] **CP-2.5-14（B11）** **无数值物证**：`value` 为 `None` 的图产物 ⇒ 第③重跳过、其余四重照跑；可支撑「印证上了」
- [ ] **CP-2.5-15** **`path` / `metric` 互斥必居其一**：两者都有 / 都无 ⇒ 不成立 + WARNING（**畸形不静默吞**，已知 bug 模式 #3）
- [ ] **CP-2.5-9** ★★**AC-S8-06 验红（逐一放宽每一重）**：逐一把**七重**各改成恒真 → **对应用例各自必红**（🔴 **七次验红，一次都不能省**——v1.3 由五次扩为七次）。⚠ 沿 sprint7 P-72 的教训：**首轮没变红的，当场加固 fixture 后复验，并如实登记"首轮无牙"**

### 任务 T-S8-2-6：`_decide_conclusion` + 三条封顶（S8-05，AR-S8-04 的正面战场）

- **产出文件**：`core/nodes/execution.py`（新纯函数，**放 `_split_reported_metrics`（`:1781`）附近**）
- **依赖项**：T-S8-2-5
- **预计复杂度**：**高**
- **架构参考**：§2.3（取较低档）+ PRD §4.5.2/§4.5.3 + **AR-S8-04**

> 🔴 **AR-S8-04 是本任务的头号风险**：「一条统一判据」在落地时**极易长回两套**——开发极可能按"数值 / 趋势 / 定性"给 `_decide_conclusion` 写三个分支。**那正是病③的复发形态。** 架构写死：`_decide_conclusion` **只读 `level` + 数封顶**，**不读证据形态、不解析证据语义**。**AC-S8-08② 的负向静态断言对象就是这个函数。**

**需要实现的内容**：

1. **输入**：agent 汇报的 `level`（来自 `_resolve_agent_report`）+ 逐条 `goal_checks` + 物证核验结果（`_verify_evidence` 的逐条产出）+ 客观事实（`exit_ok` / `step_reconciliation`）。
2. **三条封顶**（PRD §4.5.3，**代码从不抬高档位，只压低**）：

   | 触发条件 | 档位封顶 | 来源 |
   |---|---|---|
   | `exit_ok` 为假（命令跑挂了） | 封顶「**失败**」 | 裁定 3 |
   | 计划步骤没跑完（`_completion_insufficient`） | 封顶「**仅代码跑通**」 | 裁定 3 |
   | **agent 所报档位的支撑物证一条都不成立** | 封顶「**仅代码跑通**」 | A-S8-08（架构 §9 复裁项 3 复核后**认为该口径成立且必要**） |

3. 🔴 **封顶 = 按 `_LEVELS` 元组下标取更低档，不写 if 链**（架构 §2.3）：
   ```
   final_idx = max(_LEVELS.index(agent_level), _LEVELS.index(cap_level_1), ...)
   ```
   —— 这**天然满足 AC-S8-09④「agent 报低档但客观事实良好时，档位不得被抬高」**：取 `max` 下标只会往低走。
4. 🔴 **不读 `success_criteria`**（§0.5 红线 2）：达标线是**给 agent 看的判断依据**，不是给代码看的判据。**代码一旦开始解析达标线文本，就是在把第二层重新硬编码回代码里，直接复发病③。**
5. 🔴 **不读证据形态、不按形态分支**（AR-S8-04 / AC-S8-08②）：不得引入证据类型枚举、不得写 `if 是数值 / elif 是趋势 / else 定性` 这类分支。
6. **agent 报的 `level` 不在四档字面量内**（拼错 / 编了一个新档名）⇒ 视同**汇报缺失**，走封顶（架构 §1.4 第三行）。**不做模糊匹配、不做归一化**——那正是 S7-13 真跑挖出的歧义源（`_match_metrics_group` 的教训）。
7. **`goal_checks` 的三态字面量**（架构 §9 复裁项 1 的**默认取值，Maria 已确认采纳**）：「**印证上了** / **没印证上** / **无法核实**」。
   - 🔴 **不复用旧三态词「符合 / 不符 / 未验证」**。理由（架构 §9 复裁项 1）：①「未验证」在新机制下是**错的**——现在是"agent 判过、但物证核实不了"，不是"没验过"；②「印证上了」直接对应四档判据的"论文核心结论得到印证"，语义一线贯通；③"零新枚举"的实质是不新增 Python Enum 类 / 不新增分类维度，新词方案下同样成立（仍是三个模块常量）。
   - **物证不过验 ⇒ 该条落「无法核实」**（保守出口，PRD §4.8 第 3 条）。
8. **产出结构** = `state.py` docstring 里写的那份（`{level, goal_checks, evidence}`）。

**自测检查点**（AC-S8-07 / AC-S8-08 / AC-S8-09 三条命门都在这）：
- [ ] **CP-2.6-1（AC-S8-09①）** `exit_ok` 为假 + **agent 判「复现成功」** → 最终「**失败**」
- [ ] **CP-2.6-2（AC-S8-09②）** 步骤没跑完 + agent 判「复现成功」 → 最终「**仅代码跑通**」
- [ ] **CP-2.6-3（AC-S8-09③）** 支撑物证**一条都不成立** + agent 判「复现成功」 → 最终「**仅代码跑通**」
- [ ] **CP-2.6-4（AC-S8-09④）★** **agent 报低档但客观事实良好** → 档位**不得被抬高**（agent 报「仅代码跑通」，exit 全 0 + 步骤跑完 + 物证全过 → 仍是「仅代码跑通」）
- [ ] **CP-2.6-5** 汇报缺失（`report={}`）+ `exit_ok=True` + 步骤跑完 → 「仅代码跑通」；+ `exit_ok=False` → 「失败」（架构 §1.4 表逐行）
- [ ] **CP-2.6-6** agent 报了一个**不在四档内**的档名 → 视同缺失走封顶，**不做模糊匹配**（负向：`_decide_conclusion` 中无归一化/子串匹配逻辑）
- [ ] **CP-2.6-7（AC-S8-10）★** **部分复现档可达**：三条预期**印证两条** → 判「部分复现」，且**与"一条都没印证"结果不同**（治病②）
- [ ] **CP-2.6-8（AC-S8-11）★** **无对照组的定量论文可达最高档**：单组实验、无对照结构、达标线为"与论文报告对上" → **可判「复现成功」**（治病③）
- [ ] **CP-2.6-9（AC-S8-12）★★主断言** **纯定性论文可达最高档**：跑通 + **零数值** + 计划声明的图产物已落地且物证过验 + 达标线为定性表述 → 判「**复现成功**」
- [ ] **CP-2.6-10（AC-S8-08②）★★命门·负向静态审查** `_decide_conclusion` 函数体内：①`success_criteria` **零出现**；②**无证据类型枚举**；③**无按证据形态的 if/elif 分支**（`ast` 扫分支条件，人工复核每一个 `if` 的判据都只涉及 `level` / `exit_ok` / `step_reconciliation` / 物证 `ok` 布尔）
- [ ] **CP-2.6-11** ★**封顶实现形态**：确认是**按 `_LEVELS` 下标取 max**，**不是 if 链**（`ast` 静态审查 + 人工复核）
- [ ] **CP-2.6-12** 三态字面量为**新三态**（印证上了 / 没印证上 / 无法核实）；**旧三态词零出现**；物证不过验 → 落「无法核实」
- [ ] **CP-2.6-13** ★★**逐条验红**：①去掉封顶 1 → CP-2.6-1 必红；②去掉封顶 2 → CP-2.6-2 必红；③去掉封顶 3 → CP-2.6-3 必红；④把 `max` 下标改成"取 agent 报的" → CP-2.6-4 必红；⑤**引入一个按形态的 if 分支** → CP-2.6-10③ 必红（**这条是守病③根因的活体证明**）

### 任务 T-S8-2-7：`_apply_no_verifiable_output` + 删 `_apply_no_metrics` + `_no_progress_stalled` + 早停文案换发

- **产出文件**：`core/nodes/execution.py`
- **依赖项**：T-S8-2-6
- **预计复杂度**：中（**回归面 39 处**）
- **架构参考**：Q-S8-04 §4 + PRD §4.5.4 第 4 条

**需要实现的内容**：

1. **新纯函数 `_apply_no_verifiable_output`**，**放 `_apply_incomplete_execution`（`:2189`）之后**：
   - **触发条件（唯一，写死）**：`exit_ok ∧ feedback.category == NONE ∧ level == "仅代码跑通"` → 改判 `NO_VERIFIABLE_OUTPUT`；
   - 结构与 `_apply_incomplete_execution` / 已删的 `_apply_no_metrics` **逐字同款**（纯函数、命中才改判、其余原样返回）；
   - 🔴 **顺序即优先级**：排在 `_apply_incomplete_execution` **下游**（步骤 4.8）⇒ "步骤没跑完"命中后 category 不再是 `NONE`，本函数**自动让位**，报的是**真因而不是果**（沿 Q-S7-30 既有裁决，`:2204-2206` 逐字记着）。
2. **删除 `_apply_no_metrics`（`:2242-2271`）**：
   > **零改动红线解锁留档**（PRD §4.5.4 第 4 条逐字，须进交接文档）：解锁并删除。理由：决策 3 使其语义错位、文案 `:2260-2262` 还在提已废通道；**红线禁的是"改函数体"不是"删函数"**。授权人：Maria，2026-08-03。
   - ⚠ **枚举成员 `NO_METRICS` 不删**（T-S8-2-3 / Q-S8-07）。
3. **`_no_metrics_stalled`（`:2729`）→ 改名 `_no_progress_stalled`**：**匹配类别改为新类别**，**函数体结构一字不动**（单点谓词，改一处）。
4. **早停轮数常量复用 `NO_METRICS_EARLY_STOP_ROUNDS` 现有取值，`config.py` 零新增**（Q-S8-04）：语义继承（同为"连续同类无进展"）；新增第二个常量是无消费差异的重复抽象。
   - **config 常量不改名**（常量名不是用户可见文本，MEMORY §4.2 不适用；改名收益为零、回归面为正）。**在 execution 侧消费点加一行注释说明现语义。**
5. **`_NO_METRICS_EARLY_STOP_SUMMARY`（`:2715-2718`）文案必须改写**（架构 §4.1 末行 + PRD §3 核对表 #4）：现文「…请检查执行步骤或**更换论文**」——**对纯定性论文是错误建议**，踩 MEMORY §4.2。
   - ⚠ **它今天不在术语守门表里**（§1.5 事实 29 / P-S8-8）⇒ **本任务顺手补进守门候选**（交 T-S8-3-10 账目对平）。
6. 🔴 **早停优先级链 `:2817-2840` 的 elif 顺序一字不动**：新类别早停**原位继承**旧早停的位置。原论据（"早停是更具体的无进展语境"，`:2825-2826` 逐字记着）在新类别下同样成立。

**自测检查点**：
- [ ] **CP-2.7-1** `_apply_no_verifiable_output` 触发条件三合取**逐项必要**：任一不满足 → 原样返回
- [ ] **CP-2.7-2** ★**顺序即优先级**：步骤没跑完 + 档位「仅代码跑通」→ 报的是 `INCOMPLETE_EXECUTION`（**真因**）而不是 `NO_VERIFIABLE_OUTPUT`
- [ ] **CP-2.7-3** `NO_VERIFIABLE_OUTPUT` 的 `auto_fixable is True` ⇒ 路由回编码环节（不是打断用户）
- [ ] **CP-2.7-4** `_apply_no_metrics` **函数与其调用点均已删除**（AC-S8-18）；⚠ **枚举成员仍在**（复跑 CP-2.3-4）
- [ ] **CP-2.7-5** `_no_progress_stalled` 改名完成，**函数体结构与改前逐行同构**（只改类别匹配那一处）
- [ ] **CP-2.7-6** `config.py` **逐字节未改**（`git diff` 为空自证）
- [ ] **CP-2.7-7** ★早停文案已改写：**不含"更换论文"**（负向断言）；文案通俗中文、对定性论文成立；已提为具名常量并进守门候选
- [ ] **CP-2.7-8** ★**早停优先级链顺序一字未动**：`:2817-2840` 的 elif 顺序与 CP-2.2-6 落档的抄录**逐行相同**
- [ ] **CP-2.7-9 验红**：①把 `_apply_no_verifiable_output` 排到 `_apply_incomplete_execution` 上游 → CP-2.7-2 必红；②改动早停链顺序 → CP-2.7-8 必红

### 任务 T-S8-2-8：`_build_execution_result` +`conclusion` + `success` 由 `level` 派生 + 🔴 **`ExecutionResult` 删两键**（v1.3 新增）

> **📌 v1.3 扩围留痕（Maria 2026-08-06 拍板，推翻架构 v2.2/v2.3 的"保留停产"默认取值）**：原话「**旧字段要是确认没有用了就删掉**」。**`ExecutionResult.metrics` / `metrics_groups` 本 Sprint 直接删键。**
>
> 🔴 **为什么删键落在本任务、而不是批次 1a 的 `T-S8-1a-2`**（**本计划的批次归属判断，架构未明裁，理由已上磁盘验实**）：**mypy 会对多余的 TypedDict 键报错。** 实测（2026-08-06，`/tmp` 最小复现）：`R(success=True, logs="x", metrics={})` → `error: Extra key "metrics" for TypedDict "R" [typeddict-unknown-key]`。⇒ **只要 `_build_execution_result` 还在传这两个形参，声明就不能先删**；反之删了形参就必须同时删声明。**两者必须原子同批。** 而 `mypy.ini:43` `files = core` 覆盖 `core/state.py` 与 `core/nodes/execution.py`，各批收口门都要求 `mypy` 零错误 ⇒ **拆到两批之间必然红一整批**。
> ⚠ **读侧不受此限**（同一次实测）：`r.get("metrics")` 读未声明键 mypy **不报错** ⇒ reporting / ui 的消费点可以留到批次 3 再清，不构成本批阻塞。

- **产出文件**：`core/nodes/execution.py`（`_build_execution_result` `:2395` + 降级构造点 `:2908-2917`）
- **依赖项**：T-S8-2-7
- **预计复杂度**：**高**（改动最深，S7-11 反向激励复活风险的正面）
- **架构参考**：§2.4（旧快照防御读）+ **§2.6 全节（删键）** + §12 `core/state.py` v2.4 条目 + PRD §4.5.4 / §13 第 1 条 / AC-S8-26

**需要实现的内容**：

1. **新增形参** `conclusion: Optional[Dict] = None`，落盘 `dict(conclusion or {})`，与 `step_reconciliation` **逐字同款**。
2. 🔴 **`success` 改为由 `level` 派生**（PRD §4.5.4 表）：`success = level in {"复现成功", "部分复现"}`。
   - `:2428-2432` 的**三合取判据整体退场**（`exit_ok and len(metrics) >= 1 and not _completion_insufficient(...)`）。
   - ⚠ **`:2423-2426` 那段 S7-11 反向激励注记不要整段删掉**——它记录的是"为什么必须有完整度这一项"，而**完整度现在活在封顶 2 里**。**改写为指向新落点的注记**，不要让这段教训随代码一起消失。
   - 🔴 **P-71 空洞自动消解**（PRD §4.5.4 第 3 条）：新判据与 `metrics_groups` 无关 ⇒ sprint7 R-S7-73 登记的"`NONE + success=False` 少一次修复机会"那个空洞随之消失。**在交接文档里如实登记这一条已被消解**（它是本 Sprint 的顺带收益，不是新风险）。
3. **旧快照防御读**（架构 §2.4）：`success` 由 `level` 派生；**旧快照 `conclusion` 为空时 `success` 仍读既有 `success` 键原值**（它在旧快照里是有的）⇒ **旧报告可重放**。
4. **降级构造点同步补默认值**：`:2908-2917`（`code_output_dir` 缺失路径）补 `conclusion={}`。
   - ⚠ **这是 sp5 T-S5-2-6 同款动作**：漏补则该路径下 `ExecutionResult` 少一个键，下游 `.get()` 虽不崩但契约不齐。
5. 🔴 **`core/state.py` 删两键**（架构 §2.6.3）：
   - **`:175` `metrics` 与 `:183` `metrics_groups` 两行声明删除**；**`:170` 那条 `metrics_groups` 的 docstring 说明随之删除**（⚠ 架构 v2.2 裁的是"改写为停产说明"，**v2.4 改判为删除**）。
   - **docstring 同批加一段删键留痕**（沿 sp5 / sp7 加键注释体例）：「Sprint 8 删除 2 键（`metrics` / `metrics_groups`）—— 三档 `<METRICS>` 通道与分组折叠一并退场，本次执行结果改由 `conclusion.result_blocks` 承载」。🔴 **留痕必须在文件里**，否则后人读旧 checkpoint 看到两个不在声明里的键会以为是脏数据。
6. 🔴 **`_build_execution_result` 的两个形参 `metrics` / `metrics_groups` 一并删除**（架构 §2.6.3；⚠ v2.2 裁的是"保留带默认值、调用点不再传"，**v2.4 改判为删除**）。**改判理由**：键都没了，留两个永远不被传、传了也无处可落的形参就是**没有消费点的空壳**，与 `metrics[].source` 被砍时逐字记下的理由同款（`execution.py:1083-1089`）。`:2419` docstring 里点名 `_collect_grouped_metrics` 的那句**随之删除**（不是改写）。
7. **`ExecutionResult` 键数账**：今天 **11 键** → 删 2 加 1 → 🔴 **10 键**。
8. 🔴 **前置条件「确认没有用了」已由架构 §2.6.1 逐条核实成立**（交付后 `core/` 与 `ui/` 对两键的生产与消费全部归零）：生产侧 `_collect_grouped_metrics` / `_split_reported_metrics` / `_parse_metrics` 三档 / `_build_execution_result` 两形参**全删**；消费侧 `reporting` 四函数 + `_render_metrics_comparison` + `execution._apply_no_metrics` **全删**、`ui` 的 `_metric_comparison_rows` **整体替换**。

**自测检查点**：
- [ ] **CP-2.8-1** `conclusion` 随 `exec_result` **一次 commit**；落盘内容与 `_decide_conclusion` 产出**逐键相同**
- [ ] **CP-2.8-2（AC-S8-18）** **成功布尔与路由由档位派生**：复现成功 → `True` → 报告环节；部分复现 → `True` → 报告环节；仅代码跑通 → `False` → 回编码环节；失败 → `False` → 回编码环节（**四向逐条**）
- [ ] **CP-2.8-3** ★**三合取判据已退场**：`_build_execution_result` 中 `len(metrics) >= 1` **零出现**（负向静态断言）
- [ ] **CP-2.8-4** ★**S7-11 教训未随代码消失**：`:2423-2426` 那段注记**已改写为指向封顶 2 的新落点**，而不是被删掉（人工审 + 关键词正向断言）
- [ ] **CP-2.8-5** **旧快照可重放**：`conclusion` 缺失的旧 `ExecutionResult` → `success` 读原值、报告侧不崩
- [ ] **CP-2.8-6** 降级构造点（`:2908-2917`）已补 `conclusion={}` **且不再传两个已删形参**；该路径产出的 `ExecutionResult` **键数为 10**
- [ ] **CP-2.8-10（B21）★** **状态契约键数账对平**：`set(ExecutionResult.__annotations__)` **恰为 10 键**（11 − `metrics` − `metrics_groups` + `conclusion`）
- [ ] **CP-2.8-11** 🔴★★**CP-2.10-3 换发（不是删除）**：v1.2 的 CP-2.10-3「`metrics_groups` 类型签名逐字未变」**已被本次删键真突破，判定为推翻并换发**（架构 §2.6.4 第 2 条写死的口径）⇒ 新口径：「**`ExecutionResult` 由 11 键变 10 键（删 `metrics` / `metrics_groups`，加 `conclusion`）；其余 8 键的签名逐字未变**」。🔴 **禁止直接删掉这条检查点了事**——那样"其余键没被顺手改动"就**没有任何东西守着了**，而那才是它真正的价值
- [ ] **CP-2.8-12** ★**四处精确键集合断言同批换发，且仍用 `==` 精确语义**（架构 §2.6.2 丙类清单，已逐处上磁盘核对）：`test_sprint4_e3.py:572-576`、`test_sprint7_s711_completion.py:458-462` 与 `:774-778`、`test_sprint5_t26_grouped_metrics.py:53-57`（`_EXPECTED_RESULT_KEYS`，用在 `:350`/`:365`）。🔴 **禁止放宽成 `>=` 或"包含"来规避**（同 AC-S8-21 红线）
- [ ] **CP-2.8-13** ★**戊类 4 处类型签名断言换发**（**这是删键相对停产的唯一真实净增量**，全在 1 个文件，已逐行核实）：`tests/test_sprint5_t12_state.py:99`（`"metrics_groups" in ann`）、`:108`（`== typing.Dict[str, typing.Dict[str, typing.Any]]`）、`:242`（`assert "metrics_groups: Dict[str, Dict[str, Any]]" in src`）、`:269`（`get_origin(...) is dict`）
- [ ] **CP-2.8-14（B16）★** **旧快照重放（含已删键）**：旧 checkpoint 里**有 `metrics` / `metrics_groups`、无 `conclusion`** ⇒ **不崩**（TypedDict 运行时就是 dict、零校验）；结果节不渲染；🔴 **静态断言：交付后 `core/` 与 `ui/` 对这两个键的读取点为零**（不许"旧快照就读旧键渲染旧表"）
- [ ] **CP-2.8-15** 🔴**防连坐边界**：`ErrorCategory.NO_METRICS` 枚举成员**仍在**（复跑 CP-2.3-4）。⚠ **判别式（架构 §2.6.2 末段写死，不可类推）**：`Enum(值)` 反序列化会 `ValueError` ⇒ **枚举成员必须留**；**TypedDict 没有这回事**（运行时零校验）⇒ **两者不是一类问题**。删 TypedDict 键**不构成**删枚举成员的先例
- [ ] **CP-2.8-16** ★**mypy 零错误**（本任务的原子性自证）：删形参与删声明**在同一次提交内完成** ⇒ `rm -rf .mypy_cache && .venv/bin/mypy` 零错误。**若拆成两步，中间态必报 `typeddict-unknown-key`**（已实测）
- [ ] **CP-2.8-7** ★**幂等纪律③**：`_has_committed_result_for_round` guard 命中路径复用已落盘 `execution_result`，其中已含 `conclusion` 键 ⇒ **重入不重判、档位不二次变化**；且 `_verify_evidence` **零次调用**（架构 §1.6 V6）
- [ ] **CP-2.8-8** `_has_committed_result_for_round` **逐字节未改**
- [ ] **CP-2.8-9 逐条验红**：①`success` 改回三合取 → CP-2.8-2 必红；②去掉降级构造点的 `conclusion={}` → CP-2.8-6 必红；③在 guard 命中路径里重算 conclusion → CP-2.8-7 必红

### 任务 T-S8-2-8b：execution 上下文补 `baseline_results` + `success_criteria`（S8-10 + 架构 §11 前置①）

> **📌 迁移留痕（v1.2 裁定 2，架构师裁定 + Maria 已认，2026-08-04）**：本任务原为 **`T-S8-1a-6`**（批次 1a）。**迁入批次 2**，置于 **T-S8-2-8 之后、T-S8-2-9 之前**。
>
> **为什么原理由失效**：本计划 v1.1 把它留在 1a 的理由**逐字**是「1a 之后本来就不得真跑」——而**拍板 2 之后 §0.0 的结论恰好相反**（1a 落盘后系统可真跑、可演示）⇒ **论据整条失效**。
>
> **迁移的实质理由**：1a 可演示态下，agent **同时握有**论文目标值 + 本篇及格线 + 读全工作区的两个只读工具；而**验钞（T-S8-2-5）与判定纪律 / 证据边界（T-S8-2-9）全在批次 2**。`metrics_groups` 走"自报优先"⇒ **1a 期间零验钞直通报告对比表** ⇒ 一次演示就可能产出「结论：未成功复现」+「回验表一片符合」的**自相矛盾报告**（R-S8-03 要堵的正是这个）。
> ⚠ **须如实写明危害的边界**：**未验钞的自报分组指标是 S7-13 以来的既有状态**，本次 1a 新引入的只是"**把论文目标数值递到它手上**"这层**诱导**——危害是**增量的、有界的**，不是新开一个洞。
>
> **架构师另指出一条本计划未察觉的**：🔴 **`success_criteria` 的诱导性比 `baseline_results` 更强**（它直接告诉 agent"达到什么算过"），而 1a 期间系统提示词**还在说「你不判定复现是否成功」** ⇒ **给了及格线又说你不判**，自相矛盾。整体后移一并消解。
>
> 🔴 **必须保持在 T-S8-2-9 之前**：**CP-2.8b-4（本任务前后 `_EXECUTION_SYSTEM_PROMPT_BODY` 逐字节相同）是 AC-S8-15③ 的落点**——若排到 T-S8-2-9（prompt 改写）之后，这条断言就无从成立。
>
> ⚠ **`success_criteria` 送进编码环节上下文那一侧**（PRD §4.2 第 3 条）**不在本任务**，它跟着 `T-S8-2-1b` 走。

- **产出文件**：`core/nodes/execution.py`（`_build_execution_agent_context`，`:1299`，末尾追加两处注入）
- **依赖项**：**T-S8-2-8**（同文件串行；🔴 **必须排在 T-S8-2-9 之前**，理由见下方迁移留痕与 CP-2.8b-4）
- **预计复杂度**：中（**字节零扰动是命门**）
- **架构参考**：Q-S8-06 §6 + §2.5.5 注入范式 + §12

**需要实现的内容**：

1. **`baseline_results` 注入**（S8-10）：
   - 数据源 `state["paper_analysis"]["baseline_results"]`（`core/state.py:80`，由 `paper_analysis.py:224` 产出；execution 侧今天**零命中**）；
   - payload 键名 **`baseline_results`**，与 state **同名透传**（既有 payload 键全是英文机器键；不另起名，省一层映射）；
   - **非空才注入**：`isinstance(dict) and 非空`，与 `credential_degradations`（`:1349-1354`）/ `scale_reduced_directive`（`:1367-1368`）/ `expected_results`（`:1378-1380`）三处先例**逐字同款**；
   - **只送 `baseline_results`，不送整个 `paper_analysis`**（A-S8-07，反过度工程）。
2. **`success_criteria` 注入**（架构 §11 前置① + §2.5.5）：`plan.get("success_criteria") or ""`，**非空才注入**。⚠ 此时字段还没有生产者（在 1b），读到空 ⇒ 不注入 ⇒ 字节零扰动。
3. 🔴 **两者都走 HumanMessage 动态通道**（`json.dumps(sort_keys=True)` 字节幂等），**系统提示词主体不因本项改动**（架构 §6.2③：缓存命中面挂在稳定前缀 SystemMessage，HumanMessage 是前缀之后的动态段 ⇒ **本项对 cache 命中率的影响为零**）。
4. 🔴 **`sort_keys=True` 必须保持**：`baseline_results` 是 dict，键序不定则字节抖动，Prompt Cache 与回归基线双双失效（架构 §6.2⑤）。
5. ⚠ **配套提示词约束（「论文没报这个数也是合法结论」）不在本任务**——它属 prompt 主体，随 T-S8-2-9 一次落（**§16.D 已裁定：注入与其配套约束必须同批落地**）。**本任务只做通道，不做措辞。**
6. 🔴 **既有 payload 键的构造顺序与取值一字不动**（架构 §12）。

**自测检查点**：
- [ ] **CP-2.8b-1** 有 `baseline_results` 时 payload 含该键，值与 state 中的**逐字相同**（原样透传，不加工）
- [ ] **CP-2.8b-2** ★**命门·字节零扰动**（AC-S8-15②）：`baseline_results` 缺失 / 为 `None` / 为 `{}` / 为非 dict **四种形态**下，payload 与 sp7 基线**逐字节相同**（⇒ **既有 HumanMessage 字节基线不换发**，只新增"有该值"一条基线）
- [ ] **CP-2.8b-3** `success_criteria` 同款四形态字节零扰动；有值时注入且**原文照传**（不摘要、不截断）
- [ ] **CP-2.8b-4** ★**系统提示词主体零改动**（AC-S8-15③）：`_EXECUTION_SYSTEM_PROMPT_BODY` 本任务前后**逐字节相同**（`sha256` 比对，值与 T-S8-2-2 记录的旧基线一致）
- [ ] **CP-2.8b-5** 字节幂等：同一 state 连跑 3 次 `_build_execution_agent_context` 的 `json.dumps(sort_keys=True)` **逐字节相同**；`baseline_results` 键序打乱后重建，序列化结果仍相同（证明 `sort_keys` 生效）
- [ ] **CP-2.8b-6 逐条验红**：①去掉 `baseline_results` 注入 → CP-2.8b-1 红；②把"非空才注入"改成无条件注入 → CP-2.8b-2 红；③去掉 `sort_keys=True` → CP-2.8b-5 后半红

### 任务 T-S8-2-9：execution 冻结区 prompt 改写 + `EXECUTION_OUTPUT_SCHEMA` +3 字段 + 哈希三件套

- **产出文件**：`core/nodes/execution.py`（`_EXECUTION_SYSTEM_PROMPT_BODY` `:1144` / `EXECUTION_OUTPUT_SCHEMA` `:1092`）+ 两处哈希基线
- **依赖项**：T-S8-2-8
- **预计复杂度**：**高**（**两层分离第一层的物理落点**）
- **架构参考**：§2.5.4（两层分离物理落点）+ §6.2④（哈希只换发一次）+ AR-S8-07 + §12

> 🔴 **本任务是"第一层"的唯一物理落点之一**（另一处是 T-S8-2-3 的模块常量）。**四档语义段必须写在系统提示词主体里**（稳定前缀，进哈希基线），**达标线必须在 HumanMessage 里**（T-S8-2-8b 已建通道）。**两者不得混在同一段文本。**

**需要实现的内容**：

1. **改写"成功判定纪律（强约束）"三句**（`:1159-1162`）。现文是：
   - 「你不判定复现是否成功——成功与否由编排层基于工具执行的真实 exit_code 与指标做确定性判定。」
   - 「编排层还会检查计划步骤是否全部跑完——少跑步骤不会被判成功。」
   - 「不得在结果中宣称"复现成功"；只如实汇报…」
   🔴 **改法要紧**（PRD §4.9.5 措施 1，逐字）：只要求它「**照计划写的标准如实判断属于哪一档，并说明你在哪个文件读到了什么**」，**不得写成"报了就算成功"**。**判断权交出去之后，措辞是唯一还能约束动机的地方。**
2. **写入四档语义段**（第一层，PRD §4.5.2 第一层表逐字）：「复现成功」= 计划承诺的产出都落地了 **且** 论文核心结论得到印证；「部分复现」= 跑通、产物齐，但**部分**预期没印证上或核实不了；「仅代码跑通」= 跑通但承诺的产出没落地；「失败」= 没跑通。
   - 🔴 **不得把达标线写进主体**（那是第二层，走 HumanMessage）。
3. **改写"输出要求"段**：`<result>` 新增档位 / 逐条结论 / 物证三类字段的填写纪律 + **每条判断必须附物证**（结果文件真实路径 + 读到的数值/证据；判「复现成功」必须指出**哪个文件里的哪个数**支撑）。
4. **配套约束（`baseline_results` 那条）**：明说「**论文没报这个数也是合法结论，不得硬凑一个"对上了"**」（PRD §4.10 第 4 条 / R-S8-04）。
   > ⚠ 这条本属批次 1a 的 S8-10，但**架构 v2.1 §6.2⑥ 已明裁「注入与其配套提示词约束必须同批落地」** ⇒ 随注入任务 `T-S8-2-8b` 一并进批次 2，与本次改写同批（§16.D）。
5. **`EXECUTION_OUTPUT_SCHEMA`（`:1092`）新增三字段**：`conclusion_level` / `goal_checks` / `evidence`。
   - 🔴 **新增字段一律不进 `required`**（AR-S8-07）——与 `metrics` 刻意不列 required 的既有理由**逐字同源**（`:1090-1091`：必填的 list/dict 为空容器会被 `react_base._missing_required_fields`（`:496`）判成缺失 ⇒ **每个跑挂了、没判定的回合白烧一次 schema 重生成调用**）。
   - `verdict` 字段用 **JSON Schema 的 `enum` 约束**三个新三态字面量 —— ⚠ **这是 JSON Schema 的取值约束，不是 Python Enum 类，不算新枚举抽象**（架构 §5.3 末行）。
   - `metrics[].group` 的 description 改为"**把维度写进组名**"（S8-06 方案 A）。
6. **工具说明段同步**：介绍 T-S8-1a-4 新绑的两个只读工具，并写明 **`code_output_dir` 之外的路径可以读、但不能当物证**（第④重的口语化表述）。
7. **哈希三件套**（execution 侧）：旧哈希（CP-2.2-2）→ 新哈希写死字面量（**两处基线同步更新**）→ §15.1 留档。
   - 🔴 **哈希基线只换发一次**（架构 §6.2④）：本任务是全 Sprint 唯一一次改 execution prompt 主体。**若后续任务发现还要改，须回来合并到本任务，不得二次换发。**

**红线**：
- 🔴 **不得回灌判定规则**、**不得写成"报了就算成功"**（PRD §4.9.5 措施 1）。
- 🔴 **不得在主体里出现任何论文级动态变量**（已知 bug 模式 #4）。
- 🔴 **执行环节不得写代码那条工作纪律一字不动**。

**自测检查点**：
- [ ] **CP-2.9-1** ★**先改前验红**：改 prompt 前先跑两处门 → **必红**并打出新旧哈希（沿 T-S8-2-1b 同款"活体证明"）
- [ ] **CP-2.9-2** ★★**两层分离物理落点**（AC-S8-08①）：**四档语义段在系统提示词主体里**（正向断言四档名 + 语义关键词）；**达标线在 HumanMessage 里**（正向：payload 含 `success_criteria`）；🔴 **主体中 `success_criteria` 的具体内容零出现**（负向：主体是静态常量，不可能含某篇论文的达标线——用"主体跨论文字节一致"证明）
- [ ] **CP-2.9-3** ★**判定纪律措辞正负两向**：**正向**含"照计划写的标准""说明你在哪个文件读到了什么"；🔴 **负向**不含"报了就算成功"这类免责措辞，也不含把判据回灌给 agent 的具体阈值
- [ ] **CP-2.9-4** "论文没报这个数也是合法结论"这条约束**在主体里存在**（正向断言）
- [ ] **CP-2.9-5** `EXECUTION_OUTPUT_SCHEMA` 新增三字段；🔴 **三者均不在 `required` 里**（AR-S8-07，负向断言）；`required` 其余内容与 CP-2.2-5 落档**逐字相同**
- [ ] **CP-2.9-6** `verdict` 的 `enum` 恰为三个**新**三态字面量；**无 Python Enum 类**新增（负向）
- [ ] **CP-2.9-7** `metrics[].group` description 已改为"把维度写进组名"
- [ ] **CP-2.9-8** **Prompt Cache 幂等**：正则 `\d{4}\.\d{4,5}` 在主体零命中；两篇不同论文下 `_build_execution_system_prompt()` 产出**逐字节相同**
- [ ] **CP-2.9-9** 哈希三件套闭合：**两处基线同步换发**、右侧均为硬编码字面量（元检查）、§15.1 留档已写
- [ ] **CP-2.9-10** "执行环节不得写代码"那条工作纪律**逐字未动**
- [ ] **CP-2.9-11 逐条验红**：①主体插一个空格 → **两处门同时红**；②把四档语义段挪进 HumanMessage → CP-2.9-2 红；③把新增 schema 字段列进 `required` → CP-2.9-5 红；④把判定纪律写成"报了就算成功" → CP-2.9-3 红

### 任务 T-S8-2-10：🔴 **四个折叠 / 扫盘函数整体删除**（v1.3 整条改判）

> **📌 v1.3 改判留痕（原任务原文保留在下方划删块，不删）**：本任务在 v1.2 中是「`_split_reported_metrics` 撞名两条都丢弃 + BUG-S8-01 非 str `group`」。**PRD v4.0 / 架构 v2.4 §16.6 把 `_split_reported_metrics` 整体删除** ⇒ **不折叠就不会撞名** ⇒ 「撞名怎么办」这个议题**随折叠一起消失**（AR-S8-08 已整条作废）。
>
> 🔴 **坍缩根本不是 agent 造成的**（PRD §4.6.1 末段）：agent 汇报的本来就是**平坦记录数组**（`EXECUTION_OUTPUT_SCHEMA.metrics`，`execution.py:1114-1135`）；是系统在 `_split_reported_metrics`（`:1827` `setdefault(group, {})`）把它折成二维、并在撞名时（`:1831` `continue  # 先到先得`）丢弃。⇒ **三维装不下是系统自己折出来的伤，原方案 A 是在给这道自伤打补丁。**

- **产出文件**：`core/nodes/execution.py`
- **依赖项**：T-S8-2-9
- **预计复杂度**：中
- **架构参考**：**架构 v2.4 §16.6 全表** + §12 「v2.2 删除面（四项一并）」 + PRD §4.6.1 / §4.6.6

**需要实现的内容 —— 四个函数 / 常量整体删除**（落点已逐处上磁盘核实，2026-08-06）：

| # | 函数 / 常量 | 落点 | 删除理由（架构 §16.6 逐字） |
|---|---|---|---|
| 1 | `_split_reported_metrics` | `:1781-1856`，**调用点 `:2938`** | **折叠动作就是病根**：`:1827` 折成二维、`:1831` 撞名丢弃 |
| 2 | `_coerce_reported_value` | `:1764-1778` | **随之删除**（唯一调用点 `:1821`） |
| 3 | 🔴 `_collect_grouped_metrics` | `:1709-1756`，**调用点 `:2961`** | **整体删除**（⚠ **推翻架构 v2.1 §13 的"不删、不改"**）：三条硬编码前提（目录 `outputs/` `:1730`、文件名 `summary.json` `:1733`、只收顶层标量 `:1749-1754`）在本 Sprint 后**不再由任何契约保证**（新约定是"落在计划声明的位置、结构自定"）；且它自带的三样东西（组名=目录路径 / 二维结构 / 只收顶层标量）**正是本次回炉要拆的**。**留作兜底 = 在 agent 不服从时把旧格子自动请回来**，与 PRD §4.6.5 #4 直接冲突 |
| 4 | `_GROUP_METRIC_STR_MAX_LEN` | `:1706` | **随之删除**（两个消费者 `:1752` / `:1774` 都没了）；取值 **120 由 `_BLOCK_CELL_MAX_LEN` 继承**（T-S8-2-10b）—— 🔴 **是改名继任，不是新造第二个常量**；⚠ **也不是"从 reporting 侧 import execution 私有符号"**（那条路已由架构 §3.2 方案 B 明令否决），**PRD §4.7 第 6 条那句"逐字复用"指的是复用取值、不是复用符号** |
| 5 | `ExecAgentOutput.reported_metrics` | `:1218`（注释 `:1203-1205`） | **随之删除**：零消费者；`report` 字段是唯一取数口径（架构 §1.3"消除两个取数口径"同向） |

🔴 **能力回退如实登记，不得包装成"更自由了"**（R-S8-20，Maria 2026-08-06 知情后拍板"接受并登记"）：删掉 `_collect_grouped_metrics` 意味着**丢失扫盘兜底**——块是纯自报、没有 `execution.py:2961` 那个 `or` 兜底。⚠ **架构 §11 前置③ 补充的依据须一并转达**：那个兜底的三条硬编码前提在本 Sprint 后不再由任何契约保证 ⇒ 它**不是"一次干净的能力回退"，更接近"前提被同批拆除后留下的空壳"**。
🔴 **架构 §16.6 备选 B（块为空时用扫盘结果转成一个块）不启用**——那个 `["字段", "值"]` 就是**代码替 agent 决定的形状**，虽是退化形状，性质与被禁的预设表头相同。

#### 🔴 BUG-S8-01 的重判（**不是想当然作废，是上磁盘重新判定**）

| 项 | 结论 |
|---|---|
| **原始缺陷是否还存在** | ❌ **原址消失**：缺陷本体在 `execution.py:1826` `group = str(raw_group).strip() if isinstance(raw_group, str) else ""`，位于 `_split_reported_metrics` 函数体内。**该函数本任务整体删除** ⇒ **该行不复存在，`group` 这个概念本身也不复存在** |
| **缺陷类别是否转移** | 🔴 **是，而且转移后更危险** —— 见 §1.6 改写与 §15 **P-S8-12**。新的 `_collect_result_blocks`（T-S8-2-10b）对 agent 汇报的 `title` / `note` / `cell` 做同款处理，而**架构 §16.5① 只写了"过 `mask_value`"、没说非 str 怎么办** |
| **验收点 DA-S8-1 的去向** | **换发**（不注销）：由「非 str `group` 跳过 + WARNING」换发为「**非 str 的 `title` / `note` / `cell` 必须先确定性转字符串再过脱敏，且畸形形态打 WARNING**」，落在 **T-S8-2-10b / CP-2.10b-7** |

⚠ **本任务不实现新缺陷的修复**（那在 T-S8-2-10b）；本任务只负责**删干净**，并在删除处留一行注释指向 DA-S8-1 的新落点。

**需要实现的内容**：

1. **撞名策略改为"值不同则两条都丢弃 + WARNING"**（AR-S8-08 / PRD §4.6）：
   - 现行 `:1828-1831` 是"**先到先得**"（`continue  # 先到先得`）；
   - 改为：同一 `(组, 名)` 重复且**值不同** ⇒ **两条都丢弃** + WARNING；**值相同的重复仍按一条收**（AR-S8-08 逐字）；
   - 🔴 **绝不做任意 tie-break**——教训是「把最后一个赢换成第一个赢，任意性没消除只是换了方向」（PRD §4.6，sprint7 S7-13 独立验收 F1 的直接产物）。
   - ⚠ **docstring `:1796-1797` 与代码 `:1828-1831` 两处都要改**（§1.5 事实 8）。**只改 docstring 等于没改；只改代码则说明书当场失真。**
2. 🔴 **BUG-S8-01（本计划新登记，纯 bug 修复，DA-S8-1）**：`:1826` 的 `else ""` 静默路径拆成两条——
   - `raw_group is None` / 缺键 ⇒ **仍归主实验**（`null` 是合法的"没有组"，docstring `:1789` 已写明）；
   - `raw_group` 非 None 且**非 str** ⇒ **该条目跳过 + WARNING**（与既有三种畸形同款处置，走同一个 `skipped` 列表）。
   - 🔴 **不要改成 `str(raw_group)` 强转**：那会把 `{"a":1}` 变成组名 `"{'a': 1}"`——Python repr 形态的组名进报告，踩已知 bug 模式 #1 同族坑 + MEMORY §4.2。
   - **docstring `:1798-1799` 同步补上 group 类型这一条**（当前说明书只列了"非 dict / 无 name / 值非标量"三种畸形，**唯独漏了 group**，这正是缺陷能藏三个月的原因）。
3. **组名语义改为"把维度写进组名"**（S8-06 方案 A）：**类型一个字不动**（`Dict[str, Dict[str, Any]]`），状态契约零改动、旧快照天然兼容；只改执行侧 schema 里 `group` 的说明（T-S8-2-9 已做）+ 提示词一句话。
   - 真跑实测：12 个组名 × 2 指标 = **24 条全装下、零撞名**。
4. **既有行为不退化**：值只收标量（口径同 `_collect_grouped_metrics`）、`str` 过 `mask_value` + 120 字符上限、产出按组名与指标名 `sorted`、同一输入连跑逐字节相同。

**自测检查点**：
- [ ] **CP-2.10-1** ★**五处符号不存在 + 全仓引用清零**：`_split_reported_metrics` / `_coerce_reported_value` / `_collect_grouped_metrics` / `_GROUP_METRIC_STR_MAX_LEN` / `ExecAgentOutput.reported_metrics` 在 `core/nodes/execution.py` **符号不存在**；`core/` `ui/` 对五者**零引用**
- [ ] **CP-2.10-2** ★**两处调用点已删**：`:2938`（`_split_reported_metrics`）与 `:2961`（`_collect_grouped_metrics`）在 `execution()` 主体中**零出现**
- [ ] **CP-2.10-3（★v1.2 原条已被 T-S8-2-8 换发，此处保留指向）** ⇒ 见 **CP-2.8-11**（键数账由「类型签名逐字未变」换发为「11→10 键 + 其余 8 键逐字未变」）
- [ ] **CP-2.10-4** 🔴**能力回退已如实登记**：交接文档含 R-S8-20 原文，**不含"更自由了"这类包装措辞**（负向断言）；架构 §11 前置③ 的补充依据（"前提被同批拆除后留下的空壳"）已一并转达
- [ ] **CP-2.10-5** 🔴**备选 B 未被启用**（负向）：代码中不存在"块为空时用扫盘结果拼一个 `["字段","值"]` 块"的分支
- [ ] **CP-2.10-6** **BUG-S8-01 重判留痕**：删除处注释指向 DA-S8-1 的新落点（T-S8-2-10b / CP-2.10b-7）；§1.6 与 §15 P-S8-12 已同批更新
- [ ] **CP-2.10-7 逐条验红**：①把 `_collect_grouped_metrics` 加回并接进 `:2961` → CP-2.10-1/2 红；②留一个同名空壳 → CP-2.10-1 仍须红（断言的是"符号不存在"）

<details><summary>~~v1.2 原任务内容（撞名两条都丢弃 + 非 str group）—— 已随折叠动作删除而整条作废，原文保留供追溯~~</summary>

> ~~撞名策略改为「值不同则两条都丢弃 + WARNING」（AR-S8-08）；BUG-S8-01 把 `:1826` 的 `else ""` 静默路径拆成"`None` 归主实验 / 非 str 跳过 + WARNING"。~~
> **作废理由**：`_split_reported_metrics` 整体删除 ⇒ 不折叠 ⇒ 不撞名 ⇒ 「撞名怎么办」议题消失（AR-S8-08 已整条作废）；`group` 概念本身消失。**这与 §5.9 第 5/6 条「议题随前提消失」是同一种情形——开发与测试不要去找它们的落地物，找不到是对的。**

</details>

---

### 任务 T-S8-2-10b：🔴 **新增 `_collect_result_blocks` + 四个上限常量**（v1.3 新增，S8-06 执行侧的核心）

- **产出文件**：`core/nodes/execution.py`（**放已删的 `_split_reported_metrics` 原位**——那一段的模块注释「步骤 4.4：agent 自报指标拆分」同批改写为「**步骤 4.75：agent 汇报的结果块收编**」）
- **依赖项**：T-S8-2-10（原位腾空）+ T-S8-2-5（台账已建）
- **预计复杂度**：**高**
- **架构参考**：**§16.5 全节** + §16.2 + §16.3

> 🔴 **收编落在 execution 侧、不落渲染侧**（架构 §16.5 四条理由，⚠ 这是对 PRD §4.7 第 6 条"落 `reporting.py`"一句实现措辞的**精确化**，属"怎么实现"层，**已如实登记不静默通过**）：
> 1. **脱敏纪律要求 `mask_value` 在落 state 之前做**（既有两处先例 `execution.py:1753` / `:1777` 逐字如此）⇒ execution 侧本来就必须有收编函数，其余归一化放进去是**零新增抽象**；
> 2. 🔴 **决定性的一条**：它让「**给定同一份状态重放 → 报告字节一致**」在**结构上**成立。反之若在渲染侧收编，`mask_value` 依赖 `secrets_store` 的**运行时状态** ⇒ 同一份 state 在不同时刻可能渲染出不同的报告；
> 3. **checkpoint 体积可控**（state 里存的是已截断的结果，不是 agent 吐的原始巨物，AR-S8-11）；
> 4. **"标注了什么"集中在一处**。

**需要实现的内容**（六道处置，架构 §16.5① 逐条）：

| # | 处置 | 要点 |
|---|---|---|
| ① | **脱敏** | 每个 `title` / `note` / `cell` 过 `mask_value`（`core/secrets_store.py:261`） |
| ② | **长度** | 每个 `title` / `note` / `cell` 截断到 `_BLOCK_CELL_MAX_LEN`，**超长截断不丢弃**并留标注 |
| ③ | **对齐** | `len(row) != len(columns)` → **短的补占位、长的截断**，块级 caveats 记「本块有 N 行列数与表头不符，已按表头对齐展示」 |
| ④ | **上限** | 块数 / 列数 / 行数各自截断，**每次截断都往 caveats 写一句中文并指向产物路径**（**不是省略号、不是脚注**） |
| ⑤ | **引用** | 把块自带的 `{path, value}` / `{metric, value}` 回填成 `evidence_ids`（查 T-S8-2-5 建的台账） |
| ⑥ | **不合法** | 非 dict / 无 title / `columns` 与 `rows` 都缺 → **降级为"原样文本块"（先截断再放）**，块级 caveats 记「未按可渲染结构汇报」+ 指向完整日志。🔴 **绝不兜底回旧的二维表** |

🔴 **四个模块常量**（`_collect_result_blocks` 附近，**不进 `config.py`**，PRD 非目标 10）：

| 常量 | 取值 | 依据 |
|---|---|---|
| `_BLOCK_MAX` | **12** | 真跑最细切法 4 方法 × 3 数据集 正好 12 |
| `_BLOCK_COL_MAX` | **12** | 超 12 列在 Markdown 与 `st.table` 里都已不可读 |
| `_BLOCK_ROW_MAX` | **50** | 真跑实测 24 条的 2 倍余量 |
| `_BLOCK_CELL_MAX_LEN` | **120** | 🔴 **`_GROUP_METRIC_STR_MAX_LEN` 的改名继任者**（取值原样搬），**不是新造第二个常量** |

#### 🔴 DA-S8-1 换发：**非 str 字段必须先确定性转字符串再脱敏**（本计划新发现，架构 §16.5 未覆盖）

> **发现方式**：本次跟改落盘时上磁盘实测 `mask_value` 对非 str 输入的行为（2026-08-06）。**这是 BUG-S8-01 缺陷类别的转移落点，且比原形态更危险。**

**实测事实（可复现）**：`core/secrets_store.py:261` 的 `mask_value` 对**非 str** 输入，行为**取决于当前有没有注册凭证**：

| 前置 | `mask_value(123)` 的实际行为 | 后果 |
|---|---|---|
| **无凭证注册**（`known` 为空，`:280-281` 提前 `return text`） | **静默原样返回 `123`** | **脱敏被完全跳过**，且无任何日志 |
| **有凭证注册**（走到 `:283-284` 的 `masked.replace(...)`） | 🔴 **抛 `AttributeError: 'int' object has no attribute 'replace'`** | **炸节点** |

🔴 **为什么这比 BUG-S8-01 原形态更危险**：**测试环境通常没有注册凭证 ⇒ 单测与集成回归全绿；真跑时凭证已注册 ⇒ 当场 `AttributeError`。** 这是一条**环境相关、测试抓不到**的路径。而 agent 完全可能给出非 str 单元格——**主通道是自由 JSON、实测非 strict**（架构 §16.2 技术依据 2），schema 里写 `"type": "string"` 拦不住。

**处置（写死）**：

1. 🔴 **`_collect_result_blocks` 必须在调 `mask_value` **之前**把每个 `title` / `note` / `cell` 确定性转成字符串**。
2. **转换口径**：标量（`str` / `int` / `float` / `bool`）→ `str(x)`；**`None` → 空串**；**容器（`list` / `dict`）→ 不做 `str()` 强转**，而是**该单元格置为占位符 + 该块 caveats 记一句**——⚠ 理由与 BUG-S8-01 当初写死的那条**逐字同源**：`str({'a':1})` 会把 **Python repr 形态**（单引号 dict）印进报告，踩已知 bug 模式 #1 同族坑 + MEMORY §4.2（用户可见文本禁内部表示）。
3. 🔴 **畸形形态必须打 WARNING，不得静默**（已知 bug 模式 #3）：非标量单元格、非 list 的 `columns` / `rows`、非 str 的 `title` —— 逐类记数并 WARNING。
4. ⚠ **`_md_escape_inline`（`reporting.py:406-412`）自己会 `str(value)`**（`:410` 实测），所以渲染侧不会崩——**但那已经太晚了**：脱敏在 execution 侧、渲染在 reporting 侧，**没脱掉的敏感串已经落进 state 了**。⇒ **必须在 execution 侧解决，不能靠渲染侧兜。**

**自测检查点**：
- [ ] **CP-2.10b-1（B4）** **列数不符**：短、长各一 → 短的补占位、长的截断，**块级 caveats 有中文标注**，无静默
- [ ] **CP-2.10b-2（B5）** **超上限**：13 块 / 13 列 / 51 行 / 121 字符 各一 → 各自截断 + **各自有显式中文标注并指向产物路径**
- [ ] **CP-2.10b-3（B6）** **结构不合法**：块非 dict / 无 title / columns 与 rows 都缺 → 降级为**先截断后原样打印**的文本块 + 标注 + 指向日志；🔴 **绝不出现旧的三列表头**（负向）
- [ ] **CP-2.10b-4（B7）★验红** **转义与脱敏**：cell 含 `\|`、含换行、含敏感串各一 → 全部过 `mask_value`（execution 侧）+ `_md_escape_inline`（渲染侧），表不破形、敏感串已掩码。**验红：去掉任一处理 → 对应用例必红**
- [ ] **CP-2.10b-5（B10）** **引用回填**：块自带的 `{path, value}` 被回填成台账 id，与 goal_checks 指向**同一个 id**
- [ ] **CP-2.10b-6** **常量取值**：四个常量值为 12 / 12 / 50 / 120；🔴 **`_BLOCK_CELL_MAX_LEN` 是改名继任**（负向：`_GROUP_METRIC_STR_MAX_LEN` 已不存在，且 `reporting.py` **未 import execution 私有符号**）
- [ ] **CP-2.10b-7 ★★DA-S8-1 换发（BUG-S8-01 的新落点）** **非 str 字段处置**：①`cell` 为 `int` / `float` / `bool` → 转成字符串后脱敏，**不抛异常**；②`cell` 为 `list` / `dict` → **置占位符 + caveats 有记录**，🔴 **负向：报告里不出现 `{'a': 1}` 这类 Python repr 形态**；③`cell` 为 `None` → 空串；④`title` 非 str / `columns` 非 list / `rows` 非 list → 各自 WARNING，**无一静默**。
  🔴 **⑤ 环境相关性验红（这条是本 CP 的命门）**：**在"已注册凭证"的前置下**重跑 ①②③ ⇒ **仍不抛 `AttributeError`**。⚠ **必须显式构造"有凭证"的前置**——默认测试环境无凭证时 `mask_value` 会提前返回，**这条用例会假绿**
- [ ] **CP-2.10b-8** ★**确定性**：同一份输入连跑 3 次，`json.dumps(sort_keys=True)` **逐字节相同**；**不排序**（负向：函数体内无 `sorted()`）

---

### 任务 T-S8-2-11：节点主体接线（插入步骤 4.75 / 4.8）+ **S7-13 自律门控废止** —— ★**可用性恢复点**

- **产出文件**：`core/nodes/execution.py`（`execution()` `:2874` 主体）
- **依赖项**：T-S8-2-10
- **预计复杂度**：**高**（幂等纪律③ / 顺序即优先级）
- **架构参考**：§1.5 裁定 4（落点顺序）+ PRD §4.5.5 留档 2

**需要实现的内容**：

1. **在既有七步骨架里插入两步**（架构 §1.5，**不新增步骤号层级**）：
   ```
   步骤 4.4  _split_reported_metrics（保留，S8-06 改组名语义 + 撞名处置 + BUG-S8-01）
   步骤 4.5  metrics_groups（保留，agent 汇报优先、扫盘兜底）
   步骤 4.6  _reconcile_steps（位置不动）
   步骤 4.65 _audit_declared_steps（位置不动）
   步骤 4.7  _apply_incomplete_execution（保留）
   步骤 4.75 ★ 新增 _verify_evidence + _decide_conclusion   ← 本次唯一新增步骤
   步骤 4.8  ★ 新增 _apply_no_verifiable_output（取代被删的 _apply_no_metrics 的位置）
   步骤 5    _build_execution_result（多收一个 conclusion 参数，随 exec_result 一次 commit）
   ```
2. 🔴 **磁盘同刻性**（幂等纪律③ / PRD §4.5.1 落点理由①的架构兑现）：`_verify_evidence` **在此处读盘**，与 agent 跑命令是**同一次节点调用、同一份 `code_output_dir` 现场**。报告环节读的是已落盘的 `conclusion`，**不重算、不重读盘**。
3. 🔴 **废止 S7-13 自律门控**（`:2939-2952`，PRD §4.5.5 留档 2）：
   > **留档 2**（逐字，须进交接文档）：S7-13 自律门控（原注释自称"本批最要紧的一条自律"）→ **废止**，防假绿职责**由五重验钞接管**。授权人：Maria，2026-08-03。
   - 废止后 agent 自报的主实验指标**直通** `metrics`（不再要求"主通道非空才采信"）；
   - ⚠ **`metrics` 从此不再是 `success` 的分子**（T-S8-2-8 已把 `success` 改由 `level` 派生）⇒ 门控当初防的那件事（"成功合取项的分子变成 agent 自报"）**已被 T-S8-2-8 从根上消解**，这是废止它的技术前提。**这句要写进废止处的注释**，否则后人会以为这里放松了防线。
   - 🔴 **★ 这一步完成 = 可用性恢复**（§0.0）：`metrics` 不再恒空、`success` 不再恒假。
4. **`metrics_groups` 的来源优先级不变**：`reported_groups or _collect_grouped_metrics(work_dir)`（S7-13 既有裁决，**禁止合并两来源**——sprint7 实测合并会把回验打坏）。
5. **interrupt#2 幂等**：`_has_committed_result_for_round` guard 命中路径（`:2884-2899`）**复用已落盘 `execution_result`**，其中已含 `conclusion` 键 ⇒ 重入不重判。**guard 函数一字不改。**
6. **interrupt#3 幂等**：resume 后函数体整体重跑、子图从 checkpoint 恢复跑到 finalize，`_resolve_agent_report` 拿到完整判定，`_verify_evidence` **重新读盘一次**——**这正是我们要的**（磁盘就该是收尾时刻的磁盘）。

**自测检查点**：
- [ ] **CP-2.11-1** 步骤顺序与架构 §1.5 骨架**逐行一致**（4.75 在 4.7 之后、4.8 之前；步骤 5 收 `conclusion`）
- [ ] **CP-2.11-2** ★**可用性恢复自证**：与 **CP-2.1-5**（T-S8-2-1 的窗口开启自证）**同一份构造输入**（exit 全 0 + 步骤跑完 + agent 自报 1 个主实验指标）→ 现在 `metrics` **非空**、档位判出、`success` 按 `level` 派生。**CP-2.1-5 在此换发（期望值翻转），并在 §15.2 留档「翻转时点」**。⚠ **v1.2 订正**：v1.1 此处误指 `CP-1a.6-5`（该 CP 是 `_build_execution_agent_context` 的字节幂等检查，与本条毫无关系），属拍板 2 改号时的机械替换残留
- [ ] **CP-2.11-3** ★**门控废止处有注释说明"分子已不再是 metrics"**（元检查，防后人误以为放松了防线）
- [ ] **CP-2.11-4** `metrics_groups` 来源优先级不变；**禁止合并两来源**（负向：agent 有汇报时磁盘组名**不掺入**）
- [ ] **CP-2.11-5** ★**幂等纪律③**（架构 §1.6 V6）：guard 命中路径重入 → `conclusion` 与上一次落盘**逐键相同**，`_verify_evidence` **零次调用**
- [ ] **CP-2.11-6** interrupt#3 resume 后：`_resolve_agent_report` 拿到完整判定；`_verify_evidence` 重新读盘一次（**不是零次**）
- [ ] **CP-2.11-7** `_has_committed_result_for_round` / `_reconcile_steps` / `_audit_declared_steps` / `_completion_insufficient` / `_collect_grouped_metrics` **五个函数逐字节未改**
- [ ] **CP-2.11-8 逐条验红**：①把 `_verify_evidence` 挪到 `_build_execution_result` 之后 → CP-2.11-5 必红；②把两来源改成合并 → CP-2.11-4 必红；③恢复 S7-13 门控 → CP-2.11-2 必红

### 任务 T-S8-2-12：既有断言同步 + 冻结表逐行换发 + 批次 2 自测收口门

- **产出文件**：`tests/`（同步面）
- **依赖项**：T-S8-2-3 ~ T-S8-2-11
- **预计复杂度**：**高**
- **参考**：§3.3 变红面 + §3.4 A 表 + §0.3 禁弱化自查

**需要做的事**：

1. **§3.3 全部五族变红面在本批复绿**：`success is True` 族、`metrics` 非空族、`_apply_no_metrics` 族（29 处 / 5 文件）、`_no_metrics_stalled` 族（10 处 / 2 文件）、冻结表族。
2. 🔴 **冻结表逐行对账：10 行 → 6 行**（§3.4 A 表，AC-S8-21①，**v1.2 裁定 1 之后**）：
   - **4 行移出**：`_apply_no_metrics` / `_extract_metrics_block` / `_parse_metrics` / `_regex_scan_metrics` —— **逐行在测试文件注释写明「S8-02 整体删除；v1.2 裁定 1，架构师裁定 + Maria 2026-08-04 已认」**；
   - **1 行换发**：`_build_execution_result`（+`conclusion` 形参、`success` 改派生）—— **换发哈希 + 写明原因**；
   - **其余 5 行哈希不变**逐行核对（`_completion_insufficient` / `_apply_incomplete_execution` / `_reconcile_steps` / `_audit_declared_steps` / `_collect_grouped_metrics`）。
   - 🔴 **红线：禁止整表删除、禁止改成「不少于」来规避**（AC-S8-21 原文）。
3. 🔴 **四函数在 `tests/` 的引用面清理**（引用面已上磁盘实测，2026-08-04：`_extract_metrics_block` 18 处 / 4 文件、`_regex_scan_metrics` 9 处 / 3 文件、`_llm_extract_metrics` 18 处 / 9 文件、`_parse_metrics` 3 处 / 2 文件）：
   - **三档纯单测整体删除** —— ⚠ **这不是弱化断言，是被测物消失**（§0.3 第 3 条的边界情形），须在 `git diff` 说明里逐条写清；
   - **9 处 patch 行摘除**：`test_sprint3_c3.py` / `test_sprint3_c3_reinforce.py` / `test_sprint3_f1.py` / `test_sprint3_e2e.py:146` / `test_sprint4_e3.py` / `test_sprint4_e2e.py` / `test_sprint5_spk1_callbacks_spike.py:342` / `test_sprint7_s7_01_budget_gate_sink.py:188` / `test_sprint7_s7_02_persist_log.py` / `test_sprint7_targeted.py:218`。
4. **execution prompt 两处哈希基线**已在 T-S8-2-9 换发，本批复核**两处一致**。
5. **`ExecutionResult` 键集合断言**：11 → 12，逐处同步。
6. **禁弱化自查**（§0.3 第 3 条）。
7. 🔴 **本批要求全量回归零失败**（可用性已恢复，不再允许带红收口）。

**自测检查点**：
- [ ] **CP-2.12-1** ★**全量非 e2e 回归零失败**（含时间点）；与批次 1a 的红名单逐条对照，**§3.3 五族全部复绿**
- [ ] **CP-2.12-2** ★**冻结表逐行对账**（AC-S8-21①，**v1.2 订正**）：**10 行 → 6 行**（**移出 4** + **换发 1** + **不变 5**）；**逐行写明原因**；表结构未被整表删除、断言未被改成「不少于」
- [ ] **CP-2.12-2b** 🔴★★**e2e 哑弹清零（收口门抓不到的那一颗）**：显式跑 `grep -rn "_extract_metrics_block\|_regex_scan_metrics\|_llm_extract_metrics\|_parse_metrics" tests/` ⇒ **必须为空，或逐条有归属**（如仅出现在「已删除」注释里）。
  > ⚠ **为什么必须单列这一条**：`tests/test_sandbox_real_e2e.py:236` 的 `from core.nodes.execution import _extract_metrics_block` 是**函数级 import** —— **模块收集期不失败**；而批次 2 / 批次 4 的收口门跑的是 `-m "not e2e"`，**这条 e2e 根本不会被执行** ⇒ 删除后它会带着哑弹**全绿通过**，直到某次真跑（批次 5 或日后）才炸。**「全量回归零失败」在这一条上零覆盖力，只能靠显式 grep。**
- [ ] **CP-2.12-2c** 三档纯单测删除 + 9 处 patch 行摘除**逐处完成**；`git diff` 说明逐条写清「被测物消失 ≠ 弱化断言」
- [ ] **CP-2.12-3** execution prompt **两处哈希基线一致**且均为字面量
- [ ] **CP-2.12-4** `ExecutionResult` 键数 12 的断言逐处同步
- [ ] **CP-2.12-5** 禁弱化自查通过
- [ ] **CP-2.12-6** **文件边界自查**：`git status --porcelain` 生产侧**只有** `core/nodes/execution.py` 一个文件；`state.py` / `coding.py` / `planning.py` / `plan_checks.py` / `reporting.py` / `ui/` / `graph.py` / `config.py` / `react_base.py` / `code_fs_tools.py` **逐一零改动**
- [ ] **CP-2.12-7** `mypy` 清缓存后零错误
- [ ] **CP-2.12-8** ★**批次 2 验红总账**：CP-2.3-6 / 2.3-10 / 2.4-9（五次）/ 2.5-13（五条）/ 2.6-9 / 2.7-9 / 2.8-11 / 2.9-10 / 2.10-8 **逐条实做、逐条见红**；**首轮无牙的如实登记**（sprint7 P-72 教训）

> **批次 2 收口门**：CP-2.2-\* ~ CP-2.12-\* 全绿 + **全量非 e2e 回归零失败**。
> ★ **可用性已恢复**（§0.0 的中间态在此结束）。此后可做真跑——但**仍须 Maria 单独授权**（§0.3 第 8 条）。
> 🔴 **停手等 Maria 确认再开批次 3。**

---

## 7. 批次 3：报告与结果页 + 三态回验 + 审计改为证据输入（S8-07 / S8-08 / S8-09 + Q-S8-08）

> **前置条件**：批次 2 收口（`conclusion` 已能落盘）。
> **产出**：报告环节降为渲染 + 三态回验统一 + 审计改为证据输入 + 四档文案换发 + 结果页数据源重定。
> **文件边界**：`core/nodes/reporting.py`（**独占，5 任务串行**）+ `core/nodes/execution.py`（**仅 T-S8-3-7 一处**：审计注入）+ `ui/term_map.py` + `ui/pages/result_report.py` + `tests/`。**不碰** `state.py` / `coding.py` / `planning.py` / `plan_checks.py` / `plan_review.py` / `graph.py` / `config.py`。
> **红线**：`_determine_report_form`（`:92-106`）**函数逻辑零改动**（PRD §4.5.4 第 5 条）；`reporting()` 的 `audit_code_dir` **调用点、次数、返回契约一字不动**（CP-C2-5 红线：reporting 纯读、只返 3 键）；`_render_report` / `_render_goal_checks` / `_render_annotation_notices` 的**入参契约零改动**（选方案 A 换来的最大红利）。
> ⚠ **Q-S8-08 的文案连带面须单列任务**（架构 §8 明令："**不许挂在别的任务下顺手做**"）⇒ T-S8-3-8。

### 任务 T-S8-3-1：实现前核实 + 术语守门账目预清点（**零生产改动**）

- **产出文件**：`/tmp` 自测脚本 + §15 回填
- **依赖项**：批次 2 收口
- **预计复杂度**：低

**需要做的事**：

1. **逐条复核 §15 的 P-S8-2 / P-S8-3 / P-S8-4 三条**（本批的三条实质性勘误全在这）：
   - `_render_report` def 实际行号（本计划实测 **`:1172`**，架构 §12 写 `:1176`）；
   - `humanize("report_form"/"conclusion_level")` **全仓零调用点**，真文案在 `_FORM_CARD_SPEC`（`result_report.py:85-118`）/ `reporting.py:859/:869/:1055` / `reporting.py:522`（**裸键**）；
   - `ui/pages/result_report.py:59` **import 了 `_determine_conclusion`**（改名会导致模块导入即崩）+ `_conclusion_card_key`（`:121-135`）按 `level == "science"` 分卡。
2. 🔴 **术语守门目标值精确定档**（§3.4 C 表，**不得在计划阶段写死一个猜的数**）：
   - 现值：`EXPECTED_TERM_LABELS_N = 43` / `EXPECTED_CONSTANTS_N = 15` / `EXPECTED_N = 58`；
   - **逐条列出本 Sprint 要新增/换发的 TERM_LABELS 条目**与**要新提为具名常量的用户可见文案**（含批次 1b 的审核页兜底句、批次 2 的新错误类别两常量 + 早停文案），算出目标值并落档。
3. **核实批次 1b / 2 已提但尚未进守门的常量清单**（P-S8-8：`_NO_METRICS_EARLY_STOP_SUMMARY` 今天不在表里）。
4. **核实 `_render_annotation_notices` / `_render_goal_checks` / `_render_metrics_comparison` 的现签名与入参契约**（改造后必须零改动）。

**自测检查点**：
- [ ] **CP-3.1-1** P-S8-2 / P-S8-3 / P-S8-4 三条逐条复核，实测行号与结论落档
- [ ] **CP-3.1-2** ★术语守门三个目标值**精确定档**（逐条列出增量来源，不写估数）
- [ ] **CP-3.1-3** 待进守门的具名常量清单落档（含 1b / 2 遗留）
- [ ] **CP-3.1-4** 三个渲染函数的现签名落档（改造后零改动的对照基准）

### 任务 T-S8-3-2：reporting **七个**函数删除 + `_verify_expected_results` 退化 + 三态字面量换发（**v1.3 由四删扩为七删**）

> **📌 v1.3 扩围留痕（架构 v2.4 §5.9 第 1/2/3 条）**：原四删（`_normalize_group_key` / `_match_metrics_group` / `_lookup_metric_value` / `_verify_trend`）**之外，再删三个**：
> | 函数 | 落点（已上磁盘核实） | 理由 |
> |---|---|---|
> | `_render_metrics_comparison` | `:949-1008`，**唯一调用点 `:880`** | v2.1 裁的是"改措辞"，**v2.2 改判为整体删除**——报告侧由"代码预设表头 + agent 填格子"倒转为"agent 决定怎么呈现" |
> | `_comparison_table` | `:931-946`（含 `:938` 写死的三列表头 `\| 指标 (Metric) \| 论文 baseline \| 本次复现值 \|`） | **随之删除**（新增删除面） |
> | `_flatten_mapping` | `:474-486` | **随之删除**（唯一消费者是第 1 条，`:963` / `:968` / `:998`） |
>
> ⚠ 🔴 **`_flatten_entries`（`:440`）与 `_fmt_metric_value`（`:415`）不删** —— 另有消费者（`:898` / `:924-925`，已上磁盘核实）。**误删会打掉环境信息节的渲染。**
>
> 🔴 **两条"议题随前提消失"，开发与测试不要去找落地物**（架构 §5.9 第 4/5 条，**找不到是对的**）：①`:938` 表头把内部词 `baseline` 暴露给用户（违反 MEMORY §4.2）—— **随整表删除而消解，不是"改文案"**；②PRD §4.7 第 3 条「复现侧无数据时不渲染整列空白的主实验表」（原 `:980-988`）—— **主实验表不存在了，"整列空白"无从发生**。

- **产出文件**：`core/nodes/reporting.py`
- **依赖项**：T-S8-3-1
- **预计复杂度**：中
- **架构参考**：Q-S8-05 §5.1 / §5.3 / §5.4

**需要实现的内容**：

1. **四个函数整体删除**（架构 §5.1）：
   | 函数 | 落点 | 删除理由 |
   |---|---|---|
   | `_verify_trend` | `:179-198` | **复裁 6**，Maria 知情后拍板 |
   | `_lookup_metric_value` | `:160-176` | 唯一调用点是 `_verify_trend`；留着就是死代码 |
   | `_match_metrics_group` | `:136-157` | 同上；且它的"归一化模糊匹配"**正是 S7-13 真跑挖出的歧义源** |
   | `_normalize_group_key` | `:130-133` | 上两者的唯一依赖 |
2. **`_verify_expected_results`（`:201-242`）退化为「旧快照兼容读」**（架构 §5.4）：
   - **新快照**（`conclusion.goal_checks` 非空）：报告侧**根本不调它**，直接用执行环节判出的三态；
   - **旧快照**（`conclusion` 为空 / 旧 checkpoint）：调它，`trend` 相关分支已随 `_verify_trend` 删除 ⇒ **所有条目一律落「无法核实」**，如实标注"本次结论来自旧版本记录，未做逐条核实"；
   - 函数体从 42 行缩到约 15 行（**三个形态分支保留**：dict / list / 其它）；
   - 🔴 **绝不为了让旧报告好看而在报告侧重新判定**——那正是"报告环节不再自行判定档位"要禁的。
3. **三态字面量换发**（架构 §5.3 + §9 复裁项 1）：`_VERDICT_MATCH` / `_VERDICT_MISMATCH` / `_VERDICT_UNVERIFIED`（`:125-127`）**三个常量的取值**改为「印证上了」/「没印证上」/「无法核实」。
   - ⚠ **改的是取值，不是常量名**——常量名保持不动，改动面收敛在 3 行。
   - ⚠ 与 T-S8-2-9 落进 `EXECUTION_OUTPUT_SCHEMA` 的 `verdict` enum **必须逐字一致**（两处同源，本批要交叉断言）。

**自测检查点**：
- [ ] **CP-3.2-1（AC-S8-16④）** `_verify_trend` **已删除且无残留引用**（全仓 grep 零命中，含 `ui/`）；`_lookup_metric_value` / `_match_metrics_group` / `_normalize_group_key` 同
- [ ] **CP-3.2-2** `_verify_expected_results` 退化：新快照路径**零调用**；旧快照路径调它且**全部落「无法核实」**
- [ ] **CP-3.2-3** ★**旧快照如实标注**：旧 checkpoint 渲染出的报告含"来自旧版本记录、未做逐条核实"的说明，**不假装有结论**
- [ ] **CP-3.2-4** 三个形态分支（dict / list / 其它）保留，防御读不崩
- [ ] **CP-3.2-5** ★三态字面量已换发为新三态；**旧三态词（符合 / 不符 / 未验证）在 `reporting.py` 作为判定字面量零出现**（负向；⚠ 允许出现在"旧快照兼容"的**说明文字**里，人工区分）
- [ ] **CP-3.2-6** ★**两处同源交叉断言**：`reporting._VERDICT_*` 三值与 `execution.EXECUTION_OUTPUT_SCHEMA` 里 `verdict` 的 `enum` **逐字相同**
- [ ] **CP-3.2-7 验红**：①保留 `_verify_trend` → CP-3.2-1 红；②旧快照路径改成重新判定 → CP-3.2-2 红；③把 execution 侧 enum 改一个字 → CP-3.2-6 红

### 任务 T-S8-3-3：`_determine_conclusion` → `_assemble_conclusion` + 判定段删除 + 🔴 **`result_report.py` import 跟改**

- **产出文件**：`core/nodes/reporting.py` + **`ui/pages/result_report.py`**（P-S8-4）
- **依赖项**：T-S8-3-2
- **预计复杂度**：**高**
- **架构参考**：Q-S8-05 §5.2 + §5.6(B) + 本计划 **§15 P-S8-4**

> 🔴 **P-S8-4 是本批最容易漏的一条**：`ui/pages/result_report.py:59` **直接 import 了 `_determine_conclusion`**。架构 §12 的 `ui/` 条目**没提这个 import** ⇒ 只改 `reporting.py` 会让**结果页模块导入即崩**（`ImportError`），而且**不是运行到某个分支才崩，是打开页面就崩**。

**需要实现的内容**：

1. **改名 `_determine_conclusion` → `_assemble_conclusion`**（架构 §5.2），新职责**三件事，一件不多**：
   ```
   1. level / goal_checks ← state["execution_result"]["conclusion"]（.get() 防御读）
   2. annotations ← 既有四条标注逻辑（credential_degraded / incomplete_execution /
      scale_reduced / simulation）—— 除审计那半句外，一字不动
   3. 组装 {"level", "annotations", "goal_checks"} 返回 —— 返回结构与今天逐字相同
   ```
   ⇒ `_render_report` / `_render_goal_checks` / `_render_annotation_notices` 的**入参契约零改动**。
2. 🔴 **判定段整体删除**（`:313-322` 的 `if not success: level = "none" / elif ... level = "science" / else level = "engineering"`）。
   - **AC-S8-06③ 的负向断言落点**（架构 §5.2 末行）：`_assemble_conclusion` 函数体内**不得出现任何 `level = ...` 的条件赋值**。**静态审查即可断言。**
3. **审计析取项删除**（`:281-282`）：`if has_notice or bool(audit_hits)` → `if has_notice` ⇒ `simulation` 标注**恢复为"只由 `simulation_notice` 触发"**（那本来就是它的原意）。
4. 🔴 **`ui/pages/result_report.py` 跟改（P-S8-4，两处）**：
   - `:59` 的 import 改名；
   - `_conclusion_card_key`（`:121-135`）按 `level == "science"` 分卡的逻辑**必须跟改** —— 四档制下 `"science"` 恒不成立 ⇒ **所有 full_success 都会印成 `full_success_engineering` 卡**（静默降级，不报错，最危险的形态）。
   - ⚠ **卡片文案本身的换发在 T-S8-3-9**，本任务只做**不崩 + 不静默降级**这一层。
5. 🔴 **`_determine_report_form`（`:92-106`）函数逻辑零改动**（PRD §4.5.4 第 5 条）。**已核实它返回的是内部键（`"code_only"` / `"full_success"` / `"degraded"`），不是文案** ⇒ 与 Maria 拍板 2（改形态文案）**不冲突**：函数不动，动的是映射表与卡片文案（T-S8-3-8 / T-S8-3-9）。详见 §15 **P-S8-3**。
6. **`reporting()` 的 `audit_code_dir` 调用点、次数、返回契约一字不动**（架构 §5.6(B) 首行 / CP-C2-5 红线）。

**自测检查点**：
- [ ] **CP-3.3-1** 改名完成；**全仓 `_determine_conclusion` 零残留**（含 `ui/` 与 `tests/`；`ui/term_map.py` 的注释提及需一并订正）
- [ ] **CP-3.3-2** ★★**P-S8-4 上半**：`ui/pages/result_report.py` **可正常 import**（`importlib.import_module("ui.pages.result_report")` 不抛）
- [ ] **CP-3.3-3** ★★**P-S8-4 下半**：`_conclusion_card_key` 在四档下**不静默降级**——「复现成功」与「部分复现」**分别命中不同的卡片 key**，不会双双落到同一张卡
- [ ] **CP-3.3-4** ★**AC-S8-06③ 负向静态断言**：`_assemble_conclusion` 函数体内**无任何 `level = ...` 的条件赋值**（`ast` 扫赋值语句）
- [ ] **CP-3.3-5** `level` / `goal_checks` 从 `execution_result.conclusion` **`.get()` 防御读**；旧快照（`conclusion` 缺失）→ 走旧快照兼容分支，**不崩**
- [ ] **CP-3.3-6** ★审计析取项已删：`simulation` 标注**只由 `simulation_notice` 触发**（构造"审计命中 + 无 simulation_notice" → **无 simulation 标注**）
- [ ] **CP-3.3-7** 返回结构 `{level, annotations, goal_checks}` 与改前**逐键相同**；三个渲染函数**签名与入参契约零改动**（与 CP-3.1-4 落档对照）
- [ ] **CP-3.3-8** `_determine_report_form` **逐字节未改**；`audit_code_dir` 调用点 / 次数 / 三键返回契约**逐字节未改**
- [ ] **CP-3.3-9 逐条验红**：①在 `_assemble_conclusion` 里加一句 `level = ...` 条件赋值 → CP-3.3-4 必红；②把 `result_report.py` 的 import 改回旧名 → CP-3.3-2 必红；③保留 `level == "science"` 分卡 → CP-3.3-3 必红

### 任务 T-S8-3-4：`_render_audit_findings` 独立成节（S8-09，裁定 2 的落地形态）

- **产出文件**：`core/nodes/reporting.py`
- **依赖项**：T-S8-3-3
- **预计复杂度**：中
- **架构参考**：§5.6(B) + PRD §4.9.2 三做一不做

**需要实现的内容**：

1. **新纯函数 `_render_audit_findings(audit)`**：把 `:629-652` 的 hits 表**整体搬进**，在 `_render_report` 中与 `_render_annotation_notices` **并列调用**。
2. **零扰动早退**：`hits` 空 / `audit` 为 `None` → 返回 `[]`（与 `_render_annotation_notices:604` 同款）。
3. 🔴 **新节文案三要点**（架构 §5.6(B) 逐字）：
   - **中性标题**（**不用"⚠️ 重要声明"那一档**）；
   - 明说「以下写法**不影响本次结论档位**」；
   - 明说「**命中不等于造假**，常见正当写法（如把论文报告值写进代码做对照）也会命中」。
   - 🔴 **措辞不得暗示"这是作弊"**（PRD §4.9.2 第③条）——漏改会让用户看到一份**说自己作弊、却又判了复现成功**的自相矛盾报告。
4. **`_render_annotation_notices` 的 `:612-613` 导语改写**：现文「结论口径**已据此降档**（详见"复现结论"节）」—— annotations 不再降档（档位由执行环节判）⇒ **这句变成假话**（架构 §5.5 第 3 条）。
5. **新节文案提为模块级具名常量**并进术语守门候选（交 T-S8-3-10）。

**自测检查点**：
- [ ] **CP-3.4-1** `_render_audit_findings` 存在；hits 空 / audit None → 返回 `[]`（**字节零扰动早退**）
- [ ] **CP-3.4-2** hits 非空 → 独立节渲染，**与 annotations 声明块并列**（不在 simulation 小节内）
- [ ] **CP-3.4-3（AC-S8-17④）★** 文案**不含暗示作弊的措辞**（负向断言：不含"作弊""造假嫌疑""违规"这类词）**且**明说"命中不等于造假"（正向断言）
- [ ] **CP-3.4-4** 文案明说"不影响本次结论档位"（正向断言）
- [ ] **CP-3.4-5** 标题是中性档（负向：不含"⚠️ 重要声明"）
- [ ] **CP-3.4-6** `:612-613` 导语已改写：**不含"已据此降档"**（负向断言）
- [ ] **CP-3.4-7** 新节文案已提为具名常量并进守门候选；通俗中文、零内部字段名
- [ ] **CP-3.4-8 验红**：①把 hits 表挪回 simulation 小节 → CP-3.4-2 红；②去掉"命中不等于造假" → CP-3.4-3 红

### 任务 T-S8-3-5：`_render_success_criteria`（报告展示本篇成功标准，架构 §5.7）

- **产出文件**：`core/nodes/reporting.py`
- **依赖项**：T-S8-3-3
- **预计复杂度**：低
- **架构参考**：§5.7 全节 + PRD §4.7 第 5 条

**需要实现的内容**：

| 项 | 裁定（架构 §5.7，照办） |
|---|---|
| **数据源** | `state["reproduction_plan"].get("success_criteria")`，空则返回 `[]`（零扰动早退） |
| **位置** | **紧接「复现结论」档位之后、「计划目标回验」之前** —— 阅读顺序应当是"判了哪一档 → 这一档是按什么标准判的 → 逐条对照"。**放在回验之后就成了事后解释** |
| **加工** | 🔴 **原文照登：不摘要、不截断、不改写**。它是**用户批准过的原文**，任何二次加工都等于篡改判定依据。超长时用 Markdown 引用块原样展示，**不加省略号** |
| **措辞** | 须明说**这份标准来自你审核批准过的复现计划**。🔴 **不得写成"系统认为"或"系统判定标准"**——那是把用户批准过的东西说成系统的，**既不实也卸了责任链** |
| **旧快照** | 字段缺失 → **整节不渲染**（R-6 范式，旧报告可重放） |
| **界面结果页** | **本次不扩**（架构 §5.7 明裁；若 Maria 要求属追加，非本裁定的遗漏） |

🔴 **与 `_assemble_conclusion` 的关系**（架构 §5.7 末行）：`_render_success_criteria` **不进 `_assemble_conclusion`**，它是**独立渲染函数、直接读计划**。理由同 §0.5 红线 2 —— **报告侧也不得解析达标线文本，只负责原样呈现。**

**自测检查点**：
- [ ] **CP-3.5-1** 有 `success_criteria` → 渲染；**文本与计划里的逐字相同**（原文照登，AC-S8-24 末条的可测形态）
- [ ] **CP-3.5-2** 空 / 缺失 / 非 str → 返回 `[]`，**整节不渲染**，报告字节零扰动
- [ ] **CP-3.5-3** ★**位置正确**：在「复现结论」之后、「计划目标回验」之前（按渲染行序断言）
- [ ] **CP-3.5-4** ★**措辞正负两向**：**正向**含"来自你审核批准过的复现计划"；🔴 **负向**不含"系统认为""系统判定标准"
- [ ] **CP-3.5-5** ★**负向：不解析达标线**：`_render_success_criteria` 函数体内**无任何对 `success_criteria` 文本的条件判断 / 分支 / 提取**（`ast` 静态审查）；它**不出现在 `_assemble_conclusion` 里**
- [ ] **CP-3.5-6** 超长文本**不截断、不加省略号**（构造 5000 字符标准，逐字比对）
- [ ] **CP-3.5-7 验红**：①改成摘要展示 → CP-3.5-1 红；②挪到回验之后 → CP-3.5-3 红；③写成"系统判定标准" → CP-3.5-4 红

### 任务 T-S8-3-6：回验渲染文案 + `_render_goal_checks` 按台账回查（**v1.3 改判**）

> **📌 v1.3 改判留痕**：本任务在 v1.2 中含「`_render_metrics_comparison` 的 `:955` / `:995` 组名说明改写 + 复现侧无数据不渲染主表」—— **两项议题随主实验表整体删除而消失**（架构 §5.9 第 1/5 条）。**换入**：🔴 **`_render_goal_checks` 扩围**（架构 §5.9 第 9 条）—— 行渲染（`:739`）新增一列或一段，**按 `evidence_ids` 回查 `evidence_ledger` 展示物证路径**，**引用到 `ok=false` 记录的条目显著标注**。**签名 `(conclusion)` 单参不变。**

- **产出文件**：`core/nodes/reporting.py`
- **依赖项**：T-S8-3-3
- **预计复杂度**：中
- **架构参考**：§5.3 / §5.5 + PRD §4.7 第 1/3 条

**需要实现的内容**：

1. **`_render_goal_checks`（`:707`）**：
   - `:728` icons 表**三个 key 换发为新三态字面量**（值不变，仍是三个 emoji）；`.get(verdict, "⚠️")` **兜底一字不动** ⇒ **旧快照里的旧字面量渲染成 ⚠️ 不崩**；
   - `:741` 汇总口径改为按新三态字面量；
   - `:722-723` 导语改写：现文「回验为**确定性比较**，仅依据本次执行**解析出的指标**，绝不猜测」—— **判者已换成 agent** ⇒ 须如实改为「由执行环节逐条判断并交出物证，系统核验物证真伪」（架构 §5.5 第 4 条）；
   - `:744-747` 小结文案改写：现文「整体结论不作**科学复现（完全成功）**级别的宣告」—— **档名已作废，且报告侧不再宣告档位**（架构 §5.5 第 2 条）。
2. **`_render_metrics_comparison`（`:949`）**：
   - `:955` 与 `:995` 的「组名为产物目录相对路径」**订正**——**今天就已与实际不符**（印的是方法名），方案 A 后更不符（PRD §4.7 第 1 条，踩 MEMORY §4.2）；
   - 🔴 **复现侧无数据时不渲染主实验表**（PRD §4.7 第 3 条）：`:980-989` 的主实验表标签集是「复现 ∪ 论文原值」，复现侧恒空后仍被论文原值撑起 ⇒ **印出一张「本次复现值」整列空白的表**。⇒ 复现侧无数据 ⇒ **该表整体不渲染**。
3. **`_SUCCESS_CRITERIA_NOTE`（`:560-563`）换发**（架构 §5.5 第 1 条）：现文逐字描述的是旧三合取判据（"至少解析出 1 个指标"），**四档制下整条失真**。
   - ⚠ **它已在术语守门表里**（`:111`）⇒ 换发后守门自动覆盖，**不需要加条目，但要确认改后仍通过黑名单扫描**。
   - ⚠ 换发后它描述的是什么？—— 四档判定的**口径来源**（"档位由执行环节照计划写明的本篇标准判出，系统核验其物证"）。**不得再描述具体阈值**（那是第二层，因论文而异）。

**自测检查点**：
- [ ] **CP-3.6-1** icons 三 key 已换发；`.get(verdict, "⚠️")` 兜底**一字未动**；喂旧三态字面量 → 渲染成 ⚠️ **不崩**
- [ ] **CP-3.6-2** `:741` 汇总按新三态；小结文案**不含"科学复现"**（负向）
- [ ] **CP-3.6-3** ★`:722-723` 导语已改写：**不含"确定性比较""解析出的指标"**（负向）；含"执行环节逐条判断 + 系统核验物证"（正向）
- [ ] **CP-3.6-4（AC-S8-20④）** `:955` / `:995` 组名说明与实际一致；**"产物目录相对路径"零残留**（负向）
- [ ] **CP-3.6-5（AC-S8-20③）★** **复现侧无数据 → 不渲染主实验表**（构造"复现空 + baseline 非空" → 报告中**无**「本次复现值」整列空白的表）
- [ ] **CP-3.6-6（AC-S8-20①）** 分组表非空时正常渲染
- [ ] **CP-3.6-7** `_SUCCESS_CRITERIA_NOTE` 已换发：**不含"至少解析出 1 个指标"**（负向）；**不含任何具体阈值**（负向，防把第二层写回第一层）；通过守门黑名单扫描
- [ ] **CP-3.6-8 验红**：①icons 保留旧 key → CP-3.6-1 红；②复现侧空时仍渲染主表 → CP-3.6-5 红；③`_SUCCESS_CRITERIA_NOTE` 留旧文 → CP-3.6-7 红

### 任务 T-S8-3-6b：🔴 **新增 `_render_result_blocks` + 两处调用点**（v1.3 新增，S8-07 报告侧核心）

- **产出文件**：`core/nodes/reporting.py`
- **依赖项**：T-S8-3-2（旧对比表已删、`:880` 腾空）
- **预计复杂度**：中
- **架构参考**：**§16.5① 渲染侧 + §16.5③ 渲染纪律 + §5.8 全节**

**需要实现的内容**：

1. **新纯函数 `_render_result_blocks(conclusion) -> List[str]`**，🔴 **只做三件事**：Markdown 行内转义（`_md_escape_inline`，`:406-412`）、拼装、印 caveats。**零判断、零裁剪、零排序、零默认值**（收编已在 execution 侧 `_collect_result_blocks` 做完）。
2. 🔴 **四条渲染纪律（可静态断言，AC-S8-19② 的对象）**：
   - **按 `result_blocks` 数组顺序渲染**；函数体内**不得出现 `sorted()`**（今天 `reporting.py:997` 那个 `sorted()` 就是代码在替 agent 决定组的先后）；
   - 函数体内**不得出现任何写死的表头字符串 / 结果分节标题**（今天 `:938` / `:986` / `:993` / `:995` 四处）；
   - 🔴 **入参只有 `conclusion`**，**不得取 `state`、不得取 `exec_result`** —— 取了就会有人从 `paper_analysis.baseline_results` 再拼一列论文值，**预设表头当场复发**；
   - **空块早退返 `[]`**，**不印空标题**（degraded 路径上块为空是常态，与 `_render_annotation_notices:604` 零扰动早退同款）。
3. 🔴 **导语必须写明核验边界（AR-S8-09，不得省略、不得软化）**：「**下表由执行环节汇报，系统核验的是它标注的来源文件与逐条结论的物证，未逐格核对表内每一个数字。**」
   > **为什么这句是硬要求**：验钞验的是 `evidence_ledger` 里的记录，**不是 `result_blocks[].rows` 里的单元格** ⇒ agent 完全可以在块里印 `0.61`、同时在台账里放一条 `0.62` 的合法物证，**两者不一致，系统无任何机制发现**。⚠ **架构明令不做"cell 必须能在 sources 里找到"的校验**——那是"渲染层做结构推导"的近亲（PRD 非目标 11），对纯文字块无意义，且会**诱导 agent 只报能匹配的数**。⇒ **只能如实标注。**
4. 🔴 **调用点两处（`degraded` 那处是本次新增，架构 §5.8 挖出的真缺口）**：

   | 形态 | 调用？ | 落点 | 理由 |
   |---|---|---|---|
   | `full_success` | **是** | `:880` **原位替换** `_render_metrics_comparison` | 原位替换，报告结构零扰动 |
   | 🔴 `degraded` | **是（新增一处并列调用）** | 插在 `_render_step_reconciliation`（`:1061`）**之后**、"降级原因"（`:1063`）**之前** | 见下 |
   | `code_only` | **否** | — | `_is_code_only` 意味着**根本没走 execution**（`:95-96` 逐字）⇒ `conclusion` 里不可能有块。**不是遗漏，是前提不成立** |

   🔴 **degraded 那处为什么必须补**（架构 §5.8 推论）：四档派生的成功布尔里，**「仅代码跑通」→ `success=False` → `degraded`**；而「仅代码跑通」恰恰是"命令跑通了、产物可能确实写出来了、只是没达标或步骤没跑完"的那一档。⇒ **照原落点搬，执行环节辛辛苦苦汇报的结果块，会在最需要看它的那一档里整节消失。**
   ⚠ **这不是新引入的 bug** —— 今天 degraded 就不渲染指标对比表。**但今天它无伤大雅**（degraded 意味着基本没结果），**本版之后它变致命**：块是结果的**唯一**载体（`metrics` / `metrics_groups` 已删键）⇒ **块不渲染 = 用户看不到任何结果。**

**自测检查点**：
- [ ] **CP-3.6b-1（B2）★验红** **代码不预设形状**（静态审查）：`reporting.py` 内**不存在写死的结果表头字符串、写死的结果分节标题、对块或块内行列的 `sorted()``。**验红：加回任一 → 必红**
- [ ] **CP-3.6b-2（B3）★验红** **入参边界**：`_render_result_blocks` 入参**只有 `conclusion`**；函数体不出现 `state` / `paper_analysis` / `baseline_results`。**验红：改成取 state → 必红**
- [ ] **CP-3.6b-3（B14）★** **degraded 形态能看到块**：`level="仅代码跑通"`（`success=False`）且块非空 ⇒ **degraded 报告里能看到结果块**，且位置在对账之后、降级原因之前
- [ ] **CP-3.6b-4（B15）** **空块早退**：块为空 ⇒ 结果节**整节不渲染**，**不印空标题**（full_success 与 degraded 两条路径各验一次）
- [ ] **CP-3.6b-5** 🔴**导语核验边界原文存在**（正向子串断言："未逐格核对表内每一个数字"）；**未被软化**（负向：不出现"已核验所有数据"这类措辞）
- [ ] **CP-3.6b-6（B8/B9）** **caveats 三来源各印一条**：疑似截断 / `budget_truncated=True` / 块结构不合法 ⇒ **三句不同的中文**，印在结果节**之前**
- [ ] **CP-3.6b-7（B13）★验红** **确定性口径换发**：同一份 state 重放两次 ⇒ **报告字节一致**；🔴 **不得**断言"两次真跑结果可 diff"（AC-S8-19③）
- [ ] **CP-3.6b-8（B17）★验红** **块不参与判定**：同一份物证 + 两份**块数完全不同**的汇报 ⇒ `conclusion.level` **逐字相同**（AR-S8-04 同款）
- [ ] **CP-3.6b-9** `code_only` **不调**（负向）；`_render_full_success:880` 已原位替换、`_render_degraded` 已新增一处

---

### 任务 T-S8-3-7：execution 侧审计注入（S8-09 裁定 2 的 A 落点）

- **产出文件**：`core/nodes/execution.py`（`_run_execution_agent` + `_build_execution_agent_context`）
- **依赖项**：T-S8-3-1（**可与 T-S8-3-2~3-6 并行**，但须遵守 §0.7 单收口窗口令）
- **预计复杂度**：中
- **架构参考**：§5.6(A)

**需要实现的内容**：

1. **落点**：`_run_execution_agent` 在构造 context **之前**调 `audit_code_dir(work_dir)`（`core/honesty_audit.py:528` 现成，**纯静态 AST 扫描、零 LLM、零网络、同输入同输出、目录不存在自带容忍**），结果作为**第 4 个入参**传给 `_build_execution_agent_context`（现签名 3 参，§1.5 事实 27 已核）。
2. **注入范式**：沿"非空才注入" —— **只在 `hits` 非空时**注入 `payload["code_audit_findings"]`（含 rule / file / line / snippet，`snippet` 在审计内部已过 `mask_value`），clean 或未审计时**不注入** ⇒ **与基线字节零扰动**。
3. 🔴 **提示词措辞（R-S8-13 的直接对冲，必须写死）**：告知 agent 这是"代码里发现的若干写法，供你结合上下文判断，**命中不等于造假**"，并**明确点出**"**把论文报告值写进代码做对照是复现的正当写法，会命中本项**"。
   - ⚠ **不得**写成"怎么写才不被审计命中"（PRD 非目标 5）。
   - 🔴 **落点是 payload 里的 directive 常量，不是系统提示词主体**（沿 `_CREDENTIAL_DEGRADATIONS_DIRECTIVE` / `_SCALE_REDUCED_DIRECTIVE` 既有范式）⇒ **不触发第二次 prompt 哈希换发**（架构 §6.2④「只换发一次」，本 Sprint 的那一次已在 T-S8-2-9 用掉）。
4. **异常兜底**：`audit_code_dir` 抛异常 → **try/except 吞掉 + WARNING + 视同未审计**，🔴 **绝不阻断执行**（沿 `_persist_round_log` 的 R-S7-4 兜底范式）。
5. **为什么不把审计结果塞进 state 让两边共读**（架构 §5.6(B) 末段，须进交接文档）：那会**突破 Q-S8-02 的状态契约新增上限**（两处已被 `conclusion` 与 `success_criteria` 占满）。`audit_code_dir` 是**同一目录 → 同一结果的确定性纯函数**，且最后一次 execution 之后不再有 coding 改代码（路由：成功 → reporting）⇒ **两次独立调用结果必然一致**。代价是每回合多一次 AST 扫描（纯本地、无 LLM、无配额），可接受（AR-S8-06 **登记不治**）。

**自测检查点**：
- [ ] **CP-3.7-1** `hits` 非空 → payload 含 `code_audit_findings`（含 rule / file / line / snippet 四字段）
- [ ] **CP-3.7-2（AC-S8-17②）★** 审计结果**确实进了 agent 上下文**
- [ ] **CP-3.7-3** ★**字节零扰动**：clean / 未审计（目录不存在）/ hits 为空 **三形态**下 payload 与基线**逐字节相同**
- [ ] **CP-3.7-4** ★**directive 措辞正负两向**：**正向**含"命中不等于造假" + "把论文报告值写进代码做对照是正当写法，会命中本项"；🔴 **负向**不含任何"怎么写才不被命中"的规避教学
- [ ] **CP-3.7-5** ★**系统提示词主体逐字节未改**（哈希与 T-S8-2-9 换发后的新基线**一致**，证明本任务没有触发第二次换发）
- [ ] **CP-3.7-6** ★**异常不阻断**：`audit_code_dir` 抛异常 → WARNING 已打 + 视同未审计 + **节点继续跑完**
- [ ] **CP-3.7-7** `snippet` 已过 `mask_value`（构造含哨兵 token 的代码 → payload 里被打码）
- [ ] **CP-3.7-8 验红**：①去掉 try/except → CP-3.7-6 红；②改成无条件注入 → CP-3.7-3 红；③把 directive 塞进 system prompt 主体 → CP-3.7-5 红

### 任务 T-S8-3-8：🔴 `term_map` 四档换发 + **形态文案降级为纯结构描述（Maria 拍板 2，三落点）** + `error_category` +1

- **产出文件**：`ui/term_map.py` + `core/nodes/reporting.py`（`:522` / `:859` / `:869` / `:1055`）
- **依赖项**：T-S8-3-3 + T-S8-3-6
- **预计复杂度**：**高**（P-S8-3：**只改 term_map 等于什么都没改**）
- **架构参考**：§2.3 + Q-S8-08 §5.5 + **Maria 2026-08-04 拍板 2** + 本计划 **§15 P-S8-3**
- ⚠ **架构 §8 明令本项须单列任务 + 单列验收，不许挂在别的任务下顺手做。**

> 🔴 **Maria 拍板 2（本任务的产品口径，推翻架构 §9 复裁项 2 的默认取值）**：
> - 架构 §9 复裁项 2 的默认取值是「**两套文案并存不动、只调顺序**」；
> - **Maria 拍板：报告里两套口径统一到四档** —— 顶部那三条**形态文案**要降级成**纯粹描述结构、不带结论意味**的措辞，**结论一律由四档来说**。
> - **与 PRD §4.5.4 第 5 条「`_determine_report_form` 函数逻辑零改动」的边界**（本计划已上磁盘核实，**两句不冲突**）：`_determine_report_form`（`reporting.py:92-106`）返回的是**内部键**（`"code_only"` / `"full_success"` / `"degraded"`），**不是文案**。⇒ **函数逻辑仍然不动，动的只是映射表与卡片里的文案。** Maria 的理解成立。

**需要实现的内容**：

1. 🔴 **先认清落点在哪（P-S8-3，本任务最容易做错的一步）**：
   - `humanize("report_form", ...)` 与 `humanize("conclusion_level", ...)` **全仓零调用点**（§1.5 事实 14 实测）⇒ **`ui/term_map.py:80-86` 这两族的唯一作用是被术语守门扫进覆盖面**，改它们**用户看不到任何变化**；
   - **真正到达用户眼前的文案在三处**：
     | # | 落点 | 内容 | 本任务动作 |
     |---|---|---|---|
     | ① | `ui/pages/result_report.py::_FORM_CARD_SPEC`（`:85-118`，四张卡） | 顶部结论卡片 | **降级 `degraded` 卡的结论意味**；两张 `full_success_*` 卡随四档制换发（**卡片本身在 T-S8-3-9**） |
     | ② | `core/nodes/reporting.py:859` / `:869` / `:1055` | 报告正文的三条档位宣告 | **换发为四档** |
     | ③ | `core/nodes/reporting.py:522` | `` - 报告形态: `{form}` `` —— **直接印内部键** | 🔴 **顺带治**：MEMORY §4.2 的**既有违反**，架构未提（P-S8-3） |
2. **`term_map` 的 `conclusion_level` 三条 → 四条恒等映射**（架构 §2.3）：
   ```python
   "conclusion_level:复现成功": "复现成功",
   "conclusion_level:部分复现": "部分复现",
   "conclusion_level:仅代码跑通": "仅代码跑通",
   "conclusion_level:失败": "失败",
   ```
   - 🔴 **这不是"两套值"** —— 它是同一个值，`humanize` 调用点因此**一个都不用改**，术语守门扫描面与计数保持**相等断言**闭合。
   - 🔴 **恒等映射的存在理由是保住守门通道，不是做转换**，须在 `term_map` 里**写一行注释说明，防后人当冗余删掉**（架构 §2.3 末行）。
3. **`term_map` 的 `report_form` 三条降级为纯结构描述**（Maria 拍板 2）：
   - `report_form:full_success` 现文「**执行成功**」→ **带结论意味，须降级**（如"完整流程报告"这类**只说这份报告长什么样**的措辞）；
   - `report_form:code_only` 现文「仅生成代码」→ **已是纯结构描述**，可保留（复核确认）；
   - `report_form:degraded` 现文「**未成功复现（降级）**」→ **带结论意味，须降级**；
   - 🔴 **具体措辞由开发拟、须过 MEMORY §4.2 与术语守门**；**判据是"读者看了只知道报告结构，不知道判了哪一档"**。
4. **`error_category` +1**：新增 `error_category:no_verifiable_output` 文案（对应 T-S8-2-3 的新类别）；🔴 **`error_category:no_metrics` 保留不删**（架构 §7：旧报告仍要能渲染）。
5. **`reporting.py:522` 的裸键治理**：`` - 报告形态: `{form}` `` 改为经 `humanize` 或直接印中文描述。⚠ **`reporting.py` 目前不 import `ui.term_map`**——若引入 import 会造成 `core` → `ui` 的反向依赖。
   > **默认取值**：**不引入 import**，改为在 `reporting.py` 内用一个**模块级映射常量**印中文结构描述，并**把该常量登记进术语守门**（`_GUARDED_CONSTANTS`）。理由：`core` 不依赖 `ui` 是既有分层；且守门本来就支持"具名常量"这条扫描源。⚠ 这会产生"两处描述同一件事"的轻微重复 —— **如实登记在 §15 P-S8-3**，若 Maria/架构师认为应统一到 term_map 并接受反向依赖，属单点可推翻。

**自测检查点**（AC-S8-20⑤ **须验红**）：
- [ ] **CP-3.8-1** `conclusion_level` **四条恒等映射**（key 的值部分与 label **逐字相同**）；旧三条**已删**
- [ ] **CP-3.8-2** ★恒等映射旁**有注释说明"存在理由是守门通道"**（元检查，防后人当冗余删掉）
- [ ] **CP-3.8-3** ★**形态文案已降级为纯结构描述**（Maria 拍板 2）：三条 `report_form` 值中**不含结论意味词**（负向断言：不含"成功""未成功""复现""降级"这类判定词）
- [ ] **CP-3.8-4** `error_category:no_verifiable_output` 已加；🔴 `error_category:no_metrics` **仍在**（负向清零断言的边界，复跑 CP-2.3-4 同族）
- [ ] **CP-3.8-5** ★**报告正文三条档位宣告已换发为四档**（`:859` / `:869` / `:1055`）；**"科学复现""工程复现"两档名全仓零残留**（负向）
- [ ] **CP-3.8-6** ★**P-S8-3 顺带治理**：`reporting.py:522` **不再直接印内部键**；改后文案通俗中文、已进守门；`reporting.py` **未 import `ui.term_map`**（分层未被打破）
- [ ] **CP-3.8-7（AC-S8-20⑤）★★须验红** **术语守门计数精确闭合**：`EXPECTED_TERM_LABELS_N` / `EXPECTED_CONSTANTS_N` / `EXPECTED_N` 三个数与 CP-3.1-2 落档的目标值**逐一相等**；🔴 **三处仍是 `==`，未被放宽为 `>=`**（元检查：`inspect.getsource` 扫这三条断言）。**验红：从受控清单删一条 → 必红**
- [ ] **CP-3.8-8 逐条验红**：①`conclusion_level` 只换三条不加第四条 → CP-3.8-1 红；②形态文案留"执行成功" → CP-3.8-3 红；③删掉 `error_category:no_metrics` → CP-3.8-4 红；④把 `EXPECTED_N` 改成 `>=` → CP-3.8-7 红

### 任务 T-S8-3-9：🔴 **界面结果页按结果块渲染**（v1.3 **整条作废并换发**）

> **📌 v1.3 作废换发留痕（PRD v4.1 §4.7 第 2 条与 §14 两处点名，架构 §5.9 第 11 条 / §12 `ui/`）**：
> **v1.2 原任务「数据源改读 `metrics_groups` + 按组展开二维」整条作废** —— 照做等于**把旧的二维格子重新实现一遍**，且**接到一个本 Sprint 已删除的字段上**。原文保留在下方划删块。

- **产出文件**：`ui/pages/result_report.py`
- **依赖项**：T-S8-3-3 + T-S8-3-6b + T-S8-3-8
- **预计复杂度**：中
- **架构参考**：§5.9 第 11 条 + §12 `ui/` v2.2/v2.4 条目 + PRD §13 第 2 条（**Maria 2026-08-06 确认方案 A：界面自建块表**，备选 B 不启用）

**需要实现的内容**（落点已上磁盘核实，2026-08-06）：

1. 🔴 **`_metric_comparison_rows`（`:163-201`）整体替换**：它的 `:196-198` 与 `reporting._comparison_table:938` 是**同一套写死的三列**，是"代码预设表头"的**第二处**。替换为按 `conclusion["result_blocks"]` 逐块出表。
   - ⚠ `:178` 读的 `exec_result.get("metrics")` —— **该键已随 T-S8-2-8 删除** ⇒ 这不再只是"改数据源"，是"**读一个不存在的键**"。
2. **`_render_metrics_section`（`:315-330`，调用点 `:486`）** 改为**逐块**渲染：「标题 → 可选说明 → `st.table(rows)` → 可选来源与 caveats」，🔴 **按数组顺序、不排序**。
3. **`:320` 的空文案**「无可对比指标：论文 baseline / 复现 metrics 均为空。」→ 换发为「**本次执行未汇报可展示的结果块。**」
4. 🔴 **不得兜底回退到 `metrics` / `metrics_groups`**（AC-S8-20②）。⚠ **v1.3 起该断言的性质变了**：两个键已删除 ⇒ "不许回退"**由约定升级为事实**（想回退也无处可退）—— **但断言仍要写**，它守的是「**不许换个名字把旧格子重建回来**」。
5. **结论档位改读 `execution_result.conclusion.level`**。

**自测检查点**：
- [ ] **CP-3.9-1（AC-S8-20②）★** **结果页不再假空**：有 `result_blocks` 时**不显示**空文案；空文案已换发为「本次执行未汇报可展示的结果块。」
- [ ] **CP-3.9-2** **逐块渲染口径与报告侧一致**：同一份 `conclusion` 喂 `reporting._render_result_blocks` 与结果页 ⇒ **块数、块序、每块列名与行数相同**
- [ ] **CP-3.9-3** ★**不排序**（负向静态断言）：`_render_metrics_section` 与替换后的行函数**函数体内不出现 `sorted()`**
- [ ] **CP-3.9-4** ★**预设表头零残留**（负向）：`:196-198` 那套写死三列已不存在；全 `ui/` 无写死的结果表头字符串
- [ ] **CP-3.9-5（AC-S8-20②/AC-S8-26②）★★** **全 `ui/` 对 `metrics` / `metrics_groups` 零命中**（静态审查）；🔴 **无任何兜底回退分支**
- [ ] **CP-3.9-6** 结论档位读 `conclusion.level`；旧快照（`conclusion` 缺失）→ 兼容分支**不崩**
- [ ] **CP-3.9-7** 🔴 **负向：结果页未新增成功标准展示**（架构 §5.7 明裁本次不扩）
- [ ] **CP-3.9-8 验红**：①把数据源改回 `metrics` → CP-3.9-5 红；②加回任一预设表头 → CP-3.9-4 红

<details><summary>~~v1.2 原任务原文（「数据源改读 `metrics_groups` + 按组展开二维」）—— 已整条作废，保留供追溯~~</summary>

> ~~### 任务 T-S8-3-9：`result_report.py` 数据源改 `metrics_groups` + 档位读 `conclusion.level` + 卡片跟改~~
>
> ~~- **产出文件**：`ui/pages/result_report.py`~~
> ~~- **依赖项**：T-S8-3-3 + T-S8-3-8~~
> ~~- **预计复杂度**：中~~
> ~~- **架构参考**：§12 `ui/` 条目第二项 + PRD §4.7 第 2 条 + 本计划 §15 **P-S8-4**~~
>
> ~~**需要实现的内容**：~~
>
> ~~1. 🔴 **数据源从 `metrics` 改读 `metrics_groups`**（`:178`）：全 `ui/` 对 `metrics_groups` **今天零命中**（§1.5 事实 13）⇒ **不改则结果页永远显示「无可对比指标」**（`:320`）。~~
> ~~   - ⚠ **不是简单替换**：`metrics_groups` 是二维容器（`{组名: {指标名: 值}}`），而 `_metric_comparison_rows`（`:176-` 附近）现在按扁平 dict 组装行。**须按组展开**，与 `reporting._render_metrics_comparison` 的分组表**口径一致**。~~
> ~~   - **主实验指标（`metrics`）仍要展示**——它现在装的是 agent 报的 `group` 为空的那些。⇒ **两者并列**，不是二选一。~~
> ~~2. **结论档位改读 `execution_result.conclusion.level`**（架构 §12）。~~
> ~~3. **`_FORM_CARD_SPEC`（`:85-118`）四张卡换发**：~~

**作废理由**：方案 A（把维度写进组名）已由 Maria 2026-08-05 拍板作废；`metrics_groups` 已由 Maria 2026-08-06 拍板删键。**照原文实现 = 把旧格子重新实现一遍 + 接到一个不存在的字段上。**

</details>

### 任务 T-S8-3-10：术语守门账目对平 + 既有断言同步 + 批次 3 自测收口门

- **产出文件**：`tests/`（同步面）
- **依赖项**：T-S8-3-2 ~ T-S8-3-9
- **预计复杂度**：中

**需要做的事**：

1. **术语守门账目对平**（§3.4 C 表）：三个数改为 CP-3.1-2 落档的目标值；**逐条列出增量来源**；🔴 **保持 `==`，禁止放宽为 `>=`**。
   - 待补进 `_GUARDED_CONSTANTS` 的清单（CP-3.1-3 落档）：批次 1b 的审核页兜底句、批次 2 的新错误类别两常量 + 早停文案（P-S8-8）、批次 3 的审计新节文案 + 成功标准节措辞 + `reporting.py:522` 的结构描述映射常量。
2. **reporting / result_report 侧同步面**：删除的四个函数相关断言、三态字面量相关断言、`_determine_conclusion` 改名的 48 处引用（7 个测试文件）。
3. **禁弱化自查**。
4. 🔴 **本批要求全量回归零失败**。

**自测检查点**：
- [ ] **CP-3.10-1** ★术语守门三个数与目标值**逐一相等**；增量来源逐条可追溯；`==` 未被放宽
- [ ] **CP-3.10-2** 待补常量清单**逐条已进 `_GUARDED_CONSTANTS`**（含 P-S8-8 那条遗留）
- [ ] **CP-3.10-3** `_determine_conclusion` 改名的 48 处引用（7 文件）逐处同步；全仓旧名零残留
- [ ] **CP-3.10-4** ★**全量非 e2e 回归零失败**（含时间点）
- [ ] **CP-3.10-5** 禁弱化自查通过
- [ ] **CP-3.10-6** **文件边界自查**：生产侧只有 `reporting.py` / `execution.py` / `term_map.py` / `result_report.py` 四个文件；其余逐一零改动
- [ ] **CP-3.10-7** `mypy` 清缓存后零错误

> **批次 3 收口门**：CP-3.1-\* ~ CP-3.10-\* 全绿 + 全量非 e2e 回归零失败。🔴 **停手等 Maria 确认再开批次 4。**

---

## 8. 批次 4：全量回归 + 三类基线账目对平

> **前置条件**：批次 1a / 1b / 2 / 3 全部收口。
> **产出**：账目精确闭合 + AC 覆盖矩阵 + 五条留档文字核对 + handoff 交接文档。
> **文件边界**：`tests/` + 交接文档。**生产代码零改动**（若发现必须改，说明前面某批没收干净 ⇒ **回到那一批处理，不在本批夹带**）。

### 任务 T-S8-4-1：全量回归修断言 + 增减账清单逐条对平（AC-S8-23）

- **依赖项**：批次 3 收口
- **预计复杂度**：**高**

**需要做的事**：

1. **全量非 e2e 回归**，与**改前基线 2635**（2026-08-03，`docs/TODO.md:845`；CP-1a.1-1 已重测确认）逐条对得上增减账。
2. **增减账清单**（每一条增 / 减都要有归属）：
   - **减**：随 `_apply_no_metrics` / `_verify_trend` 等删除而移除的用例；
   - **增**：本 Sprint 新行为的用例（**正式用例由测试工程师补**，开发侧账目里如实标注"待测试工程师补齐"的缺口）；
   - **换**：只换不弱化的断言。
3. PRD §4.2 预估"本次触及 117 处断言 / 17 个文件"，本计划实测**同族 grep 为 268 处 / 36 文件**（§15 P-S8-7）⇒ **以实测清点为准，不拿 PRD 的估数当靶**；账目闭合的判据是"全量回归零失败 + 每条增减有归属"。

**自测检查点**：
- [ ] **CP-4.1-1** ★全量非 e2e 回归**零失败**（含时间点 + `-p no:randomly` 与随机序**各一次，通过数相同**）
- [ ] **CP-4.1-2** ★增减账清单逐条有归属；**零"说不清来历"的增减**
- [ ] **CP-4.1-3** `mypy` 清缓存后零错误
- [ ] **CP-4.1-4** 生产代码**本批零改动**（`git status` 自证）

### 任务 T-S8-4-2：三类基线账目显式对平（AC-S8-21）

- **依赖项**：T-S8-4-1
- **预计复杂度**：中
- 🔴 **红线：禁止整表删除或改成"不少于"来规避**（AC-S8-21 原文）

**需要做的事**：逐类对照 §3.4 三张表：

1. **A. 函数源码字节冻结表**：逐行核对（换发的写明原因、未改动的一字不动、移出的写明授权留档）。
2. **B. 三处 prompt 哈希基线**：coding（**新建**，T-S8-1a-3/4）/ planning（换发，T-S8-1b-2）/ execution（换发**一次**，两处同步，T-S8-2-9）—— 三处均**右侧为硬编码字面量**，均在 §15.1 有留档。
3. **C. 术语守门计数**：三个数 `==` 闭合（T-S8-3-10 已做，本批复核）。

**自测检查点**：
- [ ] **CP-4.2-1** ★A 表逐行对平（**v1.2 裁定 1 之后**）：**移出 4 行 + 换发 1 行 + 不变 5 行；冻结表 10 行 → 6 行**，**逐行原因可查**
- [ ] **CP-4.2-2** ★**红线自查**：冻结表**未被整表删除**；三类断言**均未被改成"不少于"/`>=`**（元检查）
- [ ] **CP-4.2-3** ★B 表三处哈希：**均存在、均绿、右侧均为字面量**（元检查扫三处）；§15.1 留档三行齐全（含 coding 那行的"旧哈希 + 建门红 + 新哈希"三件套）
- [ ] **CP-4.2-4** ★C 表三个数 `==` 闭合，与 T-S8-3-10 一致
- [ ] **CP-4.2-5** ★**预先列示自证**（架构 §6.2④「禁止事后补记」）：三类基线的预期改动**确实在本 dev-plan §3.4 里预先列过**，且实际改动**未超出预列范围**；超出的**如实登记为计划外**

### 任务 T-S8-4-3：AC 覆盖矩阵审计 + **五条留档文字核对** + handoff（AC-S8-22）

- **依赖项**：T-S8-4-2
- **预计复杂度**：中

**需要做的事**：

1. **AC 覆盖矩阵**：AC-S8-01 ~ AC-S8-25 逐条 → CP → 预期用例落点，**零遗漏**（AC-S8-24 / 25 标注"批次 5 真跑"）。**+ DA-S8-1（BUG-S8-01）单列一行。**
2. 🔴 **五条留档文字核对**（AC-S8-22，**在 PRD 与交接文档中均须存在**）：
   | # | 留档 | PRD 落点 | 交接文档须逐字照抄 |
   |---|---|---|---|
   | 1 | **R-S4-01 被显式推翻**（agent 判断可进入判定，但物证须逐条通过确定性核验） | §4.5.5 留档 1 | ✅ |
   | 2 | **S7-13 自律门控废止**（防假绿职责由五重验钞接管） | §4.5.5 留档 2 | ✅ |
   | 3 | **零改动红线解锁**（`_apply_no_metrics` 删除；红线禁的是"改函数体"不是"删函数"） | §4.5.4 第 4 条 | ✅ |
   | 4 | **主控原配套 2 撤销**（"这道最后的闸不挪进 agent 手里"→ 改为证据输入） | §4.9.1 三方留痕 | ✅ |
   | 5 | **A-S8-02 被推翻**（"不需要新增计划字段"→ 必须新增） | §7 / §12.5 | ✅ |
   - **另加本计划新增的两条**：**`plan_checks` 零改动红线再解锁**（范围严格限 W6 + 关键字形参）+ **`check_plan` 签名"一字不变"的精确化表述**（架构 §15.2）。
3. 🔴 **对外表述纪律**（R-S8-01，交接文档须逐字写）：
   - **不得**说「系统改为信任 agent 自述」；
   - **正确表述**：「**agent 的结论必须附可核验物证，且判定所依据的标准是用户审核过的；但物证挡不住蓄意伪造、标准也可能被写宽。**」
   - **不得说"已杜绝造假"**，理由四条（PRD §4.9.7）：①极短内联写码任何可行阈值都拦不住（R-S7-57，三个月前已定价接受）；②**造假线上已无自动机械闸**（裁定 2 的实质代价，R-S8-15）；③**审计扫不到结果文件**（裁定 4 登记不治，R-S8-16）；④**成功标准本身可被写低**（R-S8-02，护栏只是三道、不是保证）。
   - **不得**把护栏 3（W6）宣传成"防止标准画低"的保证 —— 它只挡空话，挡不住"具体但宽松"（R-S8-17）。
4. **口径分界线声明**（R-S8-11）：**档位定义与判据来源双变更 ⇒ 历史结论与成功率口径不可跨版本比较**（本版比前版更甚：连"按什么判"都因论文而异）。交接文档与未来评测体系 Sprint 须标注这条分界线。
5. **handoff 交接文档**：运行方式、依赖说明、测试入口、已知限制与遗留（含 R-S8-09 读文件 8000 截断 / R-S8-15 / R-S8-16 / R-S8-17 四条**登记不治**）、**§16 的最终裁决结果**（含已裁定的 §16.A / §16.B）。
6. **顺带登记的收益**：sprint7 **R-S7-73 / P-71 空洞已被本 Sprint 消解**（T-S8-2-8 第 2 条）—— 如实写进交接文档，它是本 Sprint 的顺带收益。

**自测检查点**：
- [ ] **CP-4.3-1** AC-S8-01 ~ 25 + DA-S8-1 **逐条有 CP 归属**，零遗漏
- [ ] **CP-4.3-2** ★五条留档文字（+ 本计划新增两条）在交接文档中**逐字存在**
- [ ] **CP-4.3-3** ★对外表述纪律四条理由**逐条写进交接文档**；正确表述**逐字照抄**
- [ ] **CP-4.3-4** 口径分界线声明已写
- [ ] **CP-4.3-5** handoff 含运行方式 / 测试入口 / 已知限制四条 / §16 最终裁决
- [ ] **CP-4.3-6** R-S7-73 消解已如实登记
- [ ] **CP-4.3-7** 🔴**`mypy.ini` 债务清单行号订正**（架构 §2.6.5 交回，v1.3 新增）：本 Sprint 大改后**行号大面积失真**——已核实 `:124` 记的 `core.nodes.execution` `call-overload ×1 **L520**` **正在 `_parse_metrics`（`:517-550`）函数体内、该函数本次整体删除**；`:146` 记的 `core.nodes.reporting` `var-annotated ×5 L354/495/814/930/**995**` 里 **L995 在 `_render_metrics_comparison` 内、同样本次删除**。
  - **功能上不会红**（`disable_error_code` 是文件级、多压制不报错；`warn_unused_ignores = False`）⇒ **不会被任何收口门抓到**。
  - 🔴 **但 `mypy.ini:23-27` 自己立了 ratchet 规矩**：「债务清单里每一行都是一条可以删掉的 TODO……**只准往严了走**」⇒ **不订正它就是一条新的"文档与代码不符"**（正是本 Sprint 反复在治的那族病）。
  - **本任务的硬要求 = 行号注释同批订正**；**收紧（把不再触发的 code 从清单里删掉）属可选**——交付后重跑 `.venv/bin/mypy` 即可确认哪些 code 已不触发。⚠ 架构**不裁"必须收紧"**（那是排期问题），本计划**默认只做订正、把收紧登记为机会**。

> **批次 4 收口门**：CP-4.1-\* ~ CP-4.3-\* 全绿。🔴 **停手等 Maria 确认再开批次 5。**

---

## 9. 批次 5：真跑取证（⚠ **须 Maria 单独授权具体动作，严禁预授权**）

> **前置条件**：批次 4 收口。
> 🔴 **本批全部动作耗 deepxiv 配额与真实 LLM**：**须 Maria 明确授权具体动作**（泛泛的"好"不构成授权）。**严禁预授权。**
> **省配额纪律**（记忆条目「真实 e2e 须明确授权·省配额范式」）：先 smoke fail-fast；靶论文优先用已缓存的；两条 AC 尽量合并在**同一次真跑**里取证（§1.4 裁剪线第 1 条）。

### 任务 T-S8-5-1：AC-S8-24 端到端真跑取证

- **依赖项**：批次 4 收口 + **Maria 单独授权**
- **预计复杂度**：中

**验收要求**（AC-S8-24 逐条）：

1. **一篇定性为主的论文跑完 → 判出四档中的具体一档且不是「失败」**；
2. **逐条结论可溯源到具体产出文件**；
3. **该文件确实在本次代码目录下**；
4. **报告里印出的成功标准与计划里的一致**。

🔴 **判定只认落盘原文**（AC-S8-24 原文）：**计划与产出全文落盘取证**，**不得引步骤对账作证**（sprint7 R-S7-49 的教训：步骤对账用 agent 自报的 `step_index` 打标，换了命令再自报下标照样判"完成"）。

**自测检查点**：
- [ ] **CP-5.1-1** ⚠ **Maria 授权具体动作已取得**（授权原文落档）
- [ ] **CP-5.1-2** 真跑前 smoke fail-fast 通过（省配额）
- [ ] **CP-5.1-3** `reproduction_plan` 全文 + `execution_result` 全文 + 报告全文**落盘成 bundle**（sprint7 P-26 教训：临时 checkpoint db 会被回收，只剩间接物证链就没法复核）
- [ ] **CP-5.1-4** ★判出的档位**不是「失败」**，且档位名是四档字面量之一
- [ ] **CP-5.1-5** ★逐条结论的物证路径**逐条可溯源到真实文件**，且**全部在本次 `code_output_dir` 之下**
- [ ] **CP-5.1-6** ★报告里印出的成功标准与计划里的 `success_criteria` **逐字相同**
- [ ] **CP-5.1-7** 🔴 **判定不引步骤对账作证**（元检查：验收记录里不出现"步骤对账 N/N 完成"作为主证据）
- [ ] **CP-5.1-8** 真跑报告归档到 `docs/sprint8/test-reports/`

### 任务 T-S8-5-2：AC-S8-25 护栏真值核对

- **依赖项**：T-S8-5-1（**建议合并在同一次真跑里取证，省一次配额**）
- **预计复杂度**：低

**验收要求**：真跑产出的计划里，成功标准**确实引用了该论文的具体指标或结论**（不是空话）⇒ 真跑计划全文落盘后**人工核对** + **W6 警示未命中**。

**自测检查点**：
- [ ] **CP-5.2-1** 真跑计划的 `success_criteria` 全文落盘；**人工核对确实引用了具体指标或结论**
- [ ] **CP-5.2-2** 对该计划跑 `check_plan(..., paper_analysis=...)` → **W6 未命中**
- [ ] **CP-5.2-3** ★**交付表述纪律**（R-S8-02 / R-S8-17）：**单次真跑不构成服从率证据**（sprint7 §8 已明载）。交付说明**不得**声称"规划环节一定会写出好标准"，只能说"本次这一篇写出来了"
- [ ] **CP-5.2-4** 护栏 1（人眼）在真跑中**确实被看到**：审核页截图或记录证明成功标准**独立可见**

> **批次 5 收口门**：CP-5.1-\* / CP-5.2-\* 全绿 + 真跑报告归档。**Sprint 8 交付完成。**

---

## 10. 交付物清单

| # | 交付物 | 落点 | 批次 |
|---|---|---|---|
| 1 | `ReproductionPlan.success_criteria` + `ExecutionResult.conclusion` 两个状态契约键 | `core/state.py` | 1a |
| 2 | coding 侧 system prompt **字节哈希门（新建）** | `tests/` | 1a |
| 3 | `<METRICS>` 通道退场（编码侧三处清除 + 执行侧三档解绑）+ 产出约定 | `core/nodes/coding.py` / `core/nodes/execution.py` | 1a |
| 4 | 执行环节只读文件能力（`read_code_file` / `list_dir`） | `core/nodes/execution.py` | 1a |
| 5 | 论文报告值 + 本篇成功标准送达执行 / 编码环节 | `core/nodes/execution.py` / `coding.py` | 1a |
| 6 | 计划写明本篇成功标准（提示词三条约束 + schema 进 required + 两处构造点） | `core/nodes/planning.py` | 1b |
| 7 | 护栏 3：`check_plan` W6 + 带默认值的第三形参 | `core/plan_checks.py` | 1b |
| 8 | 护栏 1：成功标准在审核页顶部独立只读展示 + W6 展示接线 | `ui/pages/plan_review.py` | 1b |
| 9 | `_resolve_agent_report`（判定跨中断保真的取数单点） | `core/nodes/execution.py` | 2 |
| 10 | `_verify_evidence` 五重验钞 | `core/nodes/execution.py` | 2 |
| 11 | `_decide_conclusion` 四档判定 + 三条封顶（按 `_LEVELS` 下标取更低档） | `core/nodes/execution.py` | 2 |
| 12 | `ErrorCategory.NO_VERIFIABLE_OUTPUT` + `_apply_no_verifiable_output` + `_no_progress_stalled` | `core/nodes/execution.py` | 2 |
| 13 | `success` 由 `level` 派生；`_apply_no_metrics` 删除 | `core/nodes/execution.py` | 2 |
| 14 | execution prompt 四档语义段 + 输出要求 + `EXECUTION_OUTPUT_SCHEMA` +3 字段 | `core/nodes/execution.py` | 2 |
| 15 | 撞名两条都丢弃 + 🔴 **BUG-S8-01 非 str `group` 修复** | `core/nodes/execution.py` | 2 |
| 16 | reporting 判定职责退场（四函数删除 + `_assemble_conclusion` + 三态换发） | `core/nodes/reporting.py` | 3 |
| 17 | `_render_audit_findings` 独立成节（审计改为证据输入的报告侧） | `core/nodes/reporting.py` | 3 |
| 18 | `_render_success_criteria`（报告展示本篇成功标准，原文照登） | `core/nodes/reporting.py` | 3 |
| 19 | execution 侧审计注入（`code_audit_findings` + directive 常量） | `core/nodes/execution.py` | 3 |
| 20 | 四档文案换发 + **形态文案降级为纯结构描述（三落点）** | `ui/term_map.py` / `core/nodes/reporting.py` | 3 |
| 21 | 结果页数据源重定 + 四档卡片映射 | `ui/pages/result_report.py` | 3 |
| 22 | 三类基线账目对平记录（冻结表 / 三处 prompt 哈希 / 术语守门计数） | `tests/` + 本文 §15.1 | 4 |
| 23 | AC 覆盖矩阵 + 五条留档核对 + handoff 交接文档 | 交接文档 | 4 |
| 24 | 真跑取证报告 | `docs/sprint8/test-reports/` | 5 |

**运行方式与测试入口**（交接给测试工程师）：
- 全量非 e2e 回归：`.venv/bin/pytest -q -m "not e2e"`（基线 2635，2026-08-03）
- 类型检查：`rm -rf .mypy_cache && .venv/bin/mypy`
- 取模块**一律** `importlib.import_module`（已知坑 #6）
- 真跑：**须 Maria 单独授权**，且 conftest 会从 `.env` 读凭证使 e2e **默认真跑**

---

## 11. 风险登记（PRD R-S8-01~17 与架构 AR-S8-01~08 不重复；本计划新登记 **R-S8-18 ~ R-S8-24**）

| 编号 | 风险 | 缓解 |
|---|---|---|
| **R-S8-18** 🔴 | **AR-S8-01 不可用窗口被误判为回归**：窗口期内（T-S8-2-1 合入 → T-S8-2-11 完成）系统"一律判失败"，看到全红/全失败的人（含并行会话的其他代理、测试工程师、Maria 本人）极可能当成 bug 去"修"，一修就把 T-S8-2-1 回滚 | **拍板 2 已把窗口压进批次 2 内部**（不再横跨批次边界，暴露面显著变小、且不再夹一次"停手等确认"）+ §0.0 单列告示放全文最显著位置 + §3.3 预期变红面逐族列表 + 解绑处代码注释指路（CP-2.1-6）+ T-S8-2-1 与 T-S8-2-8 的配对约束写进批次头 + 批次 2 中途带红须逐条归因（归不到 = 真回归） |
| **R-S8-19** 🔴 | **只改 `term_map` 就以为文案改完了**（P-S8-3）：`report_form` / `conclusion_level` 两族**零 humanize 调用点**，改它们**用户看不到任何变化**。Maria 拍板 2 若只落在 `term_map:80-82`，等于什么都没做 | T-S8-3-8 单列任务 + 三落点表逐处点名 + CP-3.8-5 / CP-3.8-6 分别断言报告正文与裸键两处 |
| **R-S8-20** 🔴 | **`_determine_conclusion` 改名导致结果页模块导入即崩**（P-S8-4）：`ui/pages/result_report.py:59` 直接 import 它，架构 §12 的 `ui/` 条目**未提**。且 `_conclusion_card_key` 的 `level == "science"` 分支在四档下**静默降级**（不报错，最危险的形态） | T-S8-3-3 把 `result_report.py` 跟改列为**同一任务**（不拆到 T-S8-3-9）+ CP-3.3-2 / CP-3.3-3 两条命门 + CP-3.3-9② 验红 |
| **R-S8-21** 🔴 | **coding 侧字节门"改完再建"**：AC-S8-21② 写"换发"，而**编码侧根本没有基线**（P-S8-5）。若先改 `coding.py` 再建门，建出来的基线照着改后字节写死、**永远绿**，与自锁定形态等价 —— **这道门等于没建，而账目上却记着"已建"** | **T-S8-1a-3 单列为独立任务且强制早于 T-S8-2-1b**（先建后改，沿 sprint7 T-S7-6-2 范式）+ CP-2.1b-7「改前必红」的活体证明 + CP-1a.5-3b 三件套闭合自证（**缺任一件不得声称"已建"**）+ §1.4 裁剪线明列"不可裁不可顺延" |
| ~~**R-S8-22**~~ ✅ **已注销** | ~~三档函数解绑后成为死代码~~ —— **v1.2 裁定 1 之后四个函数整体删除 ⇒ 死代码风险不成立**。⚠ 原文里「删了则冻结表必红行数由 3 推到 6」**数字有误**，已订正为 **5 行动 / 表 10 → 6 行**（§3.4 A 表 + §15 P-S8-9） | **换发为**：删除的真实风险是**引用清零面广**（`tests/` 实测 48 处 / 12 文件）**+ e2e 哑弹**（函数级 import 逃过 `-m "not e2e"` 收口门）⇒ 由 **CP-2.12-2b（显式 grep）** 与 **CP-2.12-2c** 守 |
| **R-S8-23** | **`EXPECTED_CONSTANTS_N` 目标值在计划阶段无法精确定**：本 Sprint 要新提的具名常量分散在三个批次，此刻写死一个数等于给自己造一条注定要改的断言 | §3.4 C 表**刻意不写死**，改为 T-S8-3-1 / CP-3.1-2 按实际提取清单精确定档；T-S8-3-10 对平 |
| **R-S8-24** 🔴 | **`reporting.py:522` 裸键治理引入 `core → ui` 反向依赖**：治它最直观的做法是 import `ui.term_map`，但那会打破既有分层 | 默认取值 = **不引入 import**，改用 `reporting.py` 内的模块级映射常量并登记进术语守门（T-S8-3-8 第 5 条）。**代价是"两处描述同一件事"的轻微重复，已如实登记**（§15 P-S8-3）；若 Maria / 架构师认为应统一到 term_map 并接受反向依赖，**单点可推翻** |

**沿用并需持续盯的既有风险**（不注销）：
- **R-S8-02（规划把及格线画低）+ R-S8-10（提示词服从率约 75%）+ R-S8-08（组名服从度）**：**唯一真实验证手段是批次 5 真跑**，且**单次真跑不构成服从率证据**（sprint7 §8 已明载）。⇒ 交付表述纪律见 §12 第 1 条。
- **R-S8-15（造假线上已无自动机械闸）/ R-S8-16（审计扫不到结果文件）/ R-S8-17（W6 挡不住"具体但宽松"）/ R-S8-09（读文件 8000 截断）**：四条**登记不治**，进交接文档的"已知限制"。

---

## 12. 关键纪律汇总（开工前逐条对照）

1. 🔴 **AR-S8-01 不可用窗口（已被拍板 2 压进批次 2 内部）**：**T-S8-2-1 合入后到 T-S8-2-11 完成前**，不得真跑、不得演示、不得据此判断"哪里坏了"、不得为让回归变绿而回滚（§0.0）。✅ **批次 1a 落盘后系统仍可用。**
2. 🔴 **批次边界逐批确认**：五道停手线；对某批的授权 ≠ 对后续批次的授权；耗配额动作**严禁预授权**（§0.1）。
3. 🔴 **两层分离不得混成一层**（§0.5）：第一层进系统提示词 + 模块常量；第二层走 HumanMessage；**`_decide_conclusion` 不得读 `success_criteria`**；**不按证据形态分支**。
4. 🔴 **封顶只压不抬**：按 `_LEVELS` 元组下标取更低档，**不写 if 链**。
5. 🔴 **`core/state.py` 批次 1a 一次收口，此后全 Sprint 零改动**；状态契约新增**上限两处两键**。
6. 🔴 **先建后改**：coding 字节门（T-S8-1a-3）必须早于 coding prompt 改动（T-S8-2-1b）；改前那一次红是"门有牙"的活体证明。execution 侧同理（CP-2.9-1）。
7. 🔴 **prompt 哈希基线只换发一次/侧**：coding 新建 1 次、planning 换发 1 次、execution 换发 1 次（两处同步）。发现还要改 ⇒ **回到那一任务合并，不得二次换发**。
8. 🔴 **`ErrorCategory.NO_METRICS` 成员保留**（Q-S8-07）：清零断言的对象是**函数与其调用点**，**不是枚举成员**。
9. 🔴 **验红纪律**：`cp` + `sha256sum -c` 还原，**全程禁 `git checkout` / `git restore` / `git stash`**；**"写了断言" ≠ "断言有牙"**，首轮没红的当场加固 fixture 复验并**如实登记**。
10. 🔴 **禁弱化自查**：每批 `git diff tests/` 中 `>=` / `issubset` / `pytest.skip` / `xfail` **零新增**；术语守门三个数**保持 `==`**；冻结表**禁整表删除、禁改"不少于"**。
11. 🔴 **一律 `importlib.import_module`**（已知坑 #6）；一律 `.venv/bin/pytest`。
12. 🔴 **文件边界**：`app.py` / `docs/TODO.md` / 本 dev-plan **谁都不许碰**（主控收口）；`execution.py` 与 `reporting.py` 走单收口窗口；跨会话同文件冲突 ⇒ **停手请示**。
13. 🔴 **对外表述纪律**（R-S8-01）：不得说"信任 agent 自述"、不得说"已杜绝造假"、不得把 W6 宣传成"防止标准画低"的保证。正确表述见 T-S8-4-3 第 3 条。
14. 🔴 **用户可见文本禁内部术语**（MEMORY §4.2）：新增文案一律通俗中文、零字段名、零英文缩写；写 LLM prompt 时也别拿英文枚举当叙述示范。
15. 🔴 **已知 bug 模式 #3（禁静默吞错）本 Sprint 三处命中**：`_resolve_agent_report` 两通道皆空要 WARNING（**与 `reported_metrics` 相反**）、`_verify_evidence` IO 异常要 WARNING、**BUG-S8-01 的非 str `group` 要 WARNING**。
16. 🔴 **反过度工程**：零新模块、零新 Python Enum 类、四档档名就是四个中文串、`success_criteria` 就是一个字符串（**不做"档位→达标线"字典**）。

---

## 13. 检查点总览（CP 索引，共 **328** 个）

| 批次 | 任务 | CP 段 | 数量 | 命门 |
|---|---|---|---|---|
| **1a** | T-S8-1a-1 | CP-1a.1-1 ~ 5 | 5 | 基线重测标时间点 |
| | T-S8-1a-2 | CP-1a.2-1 ~ 6 | 6 | 既有键顺序零扰动 |
| | T-S8-1a-3 | CP-1a.3-1 ~ 8 | 8 | ★门验红 + 证否自锁定（**门留 1a；改词已迁 2-1b**） |
| | T-S8-1a-4 | CP-1a.4-1 ~ 6 | 6 | ★两个闸同时为真（**只加工具，不改行为**） |
| | T-S8-1a-5 | CP-1a.5-1 ~ 6（含 3b） | 7 | ★**全绿收口** + 可用性自证 / 三件套闭合自证 |
| | **批次 1a 小计** | | **32** | |
| **1b** | T-S8-1b-1 | CP-1b.1-1 ~ 5 | 5 | G5 对照基准落档 |
| | T-S8-1b-2 | CP-1b.2-1 ~ 11 | 11 | ★★四档档名零出现 / 两处构造点 |
| | T-S8-1b-3 | CP-1b.3-1 ~ 9 | 9 | ★G5 契约回归逐字节 |
| | T-S8-1b-4 | CP-1b.4-1 ~ 9（含 2b） | 10 | ★独立可见（拍板 1 只读口径）/ 不阻断审批 |
| | T-S8-1b-5 | CP-1b.5-1 ~ 6 | 6 | 收口门 |
| | **批次 1b 小计** | | **41** | |
| **2** | T-S8-2-1 | CP-2.1-0 ~ 8（含 4b） | 10 | ★★**四函数整体删除**（v1.2 裁定 1）+ **不可用窗口起点**；期望值待 T-S8-2-11 翻转 |
| | **T-S8-2-1b** | CP-2.1b-1 ~ 9 | 9 | ★★改前必红（先建后改活体证明；**门在 1a 已建**）/ 字节零扰动（**v1.2 迁入**） |
| | T-S8-2-2 | CP-2.2-1 ~ 6 | 6 | ★旧基线先记（不做则三道门白建） |
| | T-S8-2-3 | CP-2.3-1 ~ 6 | 6 | ★`NO_METRICS` 成员保留 |
| | T-S8-2-4 | CP-2.4-1 ~ 10 | 10 | ★★V3 收集器截断不改判定 |
| | T-S8-2-5 | CP-2.5-1 ~ 9 | 15 | ★★五重逐一放宽各自必红 |
| | T-S8-2-6 | CP-2.6-1 ~ 13 | 13 | ★★三条封顶 / 负向静态审查 / 三类论文可达 |
| | T-S8-2-7 | CP-2.7-1 ~ 9 | 9 | ★顺序即优先级 / 早停链顺序不动 |
| | T-S8-2-8 | CP-2.8-1 ~ 9 | 16 | ★四向路由 / 幂等纪律③ |
| | **T-S8-2-8b** | CP-2.8b-1 ~ 6 | 6 | ★字节零扰动 / **AC-S8-15③ 落点，须早于 2-9**（**v1.2 迁入**） |
| | T-S8-2-9 | CP-2.9-1 ~ 11 | 11 | ★★两层分离物理落点 / 不进 required |
| | T-S8-2-10 | CP-2.10-1 ~ 10 | 7 | ★★BUG-S8-01（DA-S8-1） |
| | **T-S8-2-10b** | CP-2.10b-1 ~ 8 | 8 | ★★**DA-S8-1 换发**（P-S8-12 非 str 环境相关坑）/ 六道处置 |
| | T-S8-2-11 | CP-2.11-1 ~ 8 | 8 | ★可用性恢复自证 |
| | T-S8-2-12 | CP-2.12-1 ~ 8（含 2b / 2c） | 10 | ★冻结表 **10→6 行**对账 / 🔴 **e2e 哑弹 grep** / 验红总账 |
| | **批次 2 小计** | | **144** | |
| **3** | T-S8-3-1 | CP-3.1-1 ~ 4 | 4 | ★守门目标值精确定档 |
| | T-S8-3-2 | CP-3.2-1 ~ 7 | 7 | ★两处同源交叉断言 |
| | T-S8-3-3 | CP-3.3-1 ~ 9 | 9 | ★★结果页不崩 + 不静默降级 |
| | T-S8-3-4 | CP-3.4-1 ~ 8 | 8 | ★文案不暗示作弊 |
| | T-S8-3-5 | CP-3.5-1 ~ 7 | 7 | ★原文照登 / 不解析达标线 |
| | T-S8-3-6 | CP-3.6-1 ~ 8 | 8 | ★复现侧无数据不渲染主表 |
| | **T-S8-3-6b** | CP-3.6b-1 ~ 9 | 9 | ★★degraded 缺口 / 导语核验边界 / 块不参与判定 |
| | T-S8-3-7 | CP-3.7-1 ~ 8 | 8 | ★字节零扰动 / 异常不阻断 |
| | T-S8-3-8 | CP-3.8-1 ~ 8 | 8 | ★★三落点 / 守门计数 `==` 闭合 |
| | T-S8-3-9 | CP-3.9-1 ~ 8 | 8 | ★结果页不再假空 / 四档映射 |
| | T-S8-3-10 | CP-3.10-1 ~ 7 | 7 | 收口门 |
| | **批次 3 小计** | | **83** | |
| **4** | T-S8-4-1 | CP-4.1-1 ~ 4 | 4 | ★增减账零"说不清来历" |
| | T-S8-4-2 | CP-4.2-1 ~ 5 | 5 | ★★红线自查 / 预先列示自证 |
| | T-S8-4-3 | CP-4.3-1 ~ 6 | 7 | ★五条留档 + 对外表述纪律 |
| | **批次 4 小计** | | **16** | |
| **5** | T-S8-5-1 | CP-5.1-1 ~ 8 | 8 | ⚠授权 / 落盘取证 / 不引步骤对账 |
| | T-S8-5-2 | CP-5.2-1 ~ 4 | 4 | ⚠单次真跑不构成服从率证据 |
| | **批次 5 小计** | | **12** | |
| | **合计** | | **328** | |

---

## 14. 编号速查

| 类别 | 本 Sprint 用到的范围 | 说明 |
|---|---|---|
| 需求 | `S8-01` ~ `S8-11` | PRD §1，全 P0、同批交付 |
| 验收 | `AC-S8-01` ~ `AC-S8-25` | PRD §5 |
| **dev-plan 自定验收点** | **`DA-S8-1`** | **仅 BUG-S8-01 一条**，不占用 `AC-S8-*` |
| 架构问题 | `Q-S8-01` ~ `Q-S8-09` | **一律以架构文档编号为准**；PRD §8 那行 `Q-S8-07`（护栏 3）读作 `Q-S8-09` |
| PRD 风险 | `R-S8-01` ~ `R-S8-17` | PRD §6 |
| **dev-plan 新登记风险** | **`R-S8-18` ~ `R-S8-24`** | 本文 §11，接续 PRD 号段 |
| 架构风险 | `AR-S8-01` ~ `AR-S8-08` | 架构 §10，**本计划不扩此段** |
| PM 假设 | `A-S8-01` ~ `A-S8-10` | PRD §7（A-S8-02 已推翻） |
| 任务 | `T-S8-1a-1` ~ `T-S8-5-2` | 共 **39** 个（1a=5 / 1b=5 / 2=**14**（含 `2-1b` / `2-8b`）/ 3=10 / 4=3 / 5=2） |
| 检查点 | `CP-1a.1-1` ~ `CP-5.2-4` | 共 **300** 个（逐批：1a=32 / 1b=41 / 2=126 / 3=74 / 4=15 / 5=12） |
| **落点勘误** | **`P-S8-1` ~ `P-S8-11`** | 本文 §15，**与 sprint7 的裸 `P-N`（已用到 P-73）显式分段** |
| **本计划新登记缺陷** | **`BUG-S8-01`** | 本文 §1.6 |

### 14.1 🔴 编号迁移表（**Maria 2026-08-04 拍板 2 之后，v1.0 → v1.1 的唯一一次重编**）

> **纪律：全文只有一套口径。** 下表只用于**读 v1.0 旧对话记录时对照**，正文一律用新号。

| v1.0 旧号 | v1.1 新号 | 说明 |
|---|---|---|
| `T-S8-1a-5`（三档解绑） | **`T-S8-2-1`** | 拍板 2 迁到批次 2 首位 |
| `T-S8-1a-8`（1a 收口） | **`T-S8-1a-5`** | 顺位前移一格 |
| `T-S8-2-1` ~ `T-S8-2-11` | **`T-S8-2-2` ~ `T-S8-2-12`** | 整体后移一格，给首位腾位 |
| `CP-1a.5-*`（解绑的 CP） | **`CP-2.1-*`**（另新增 `CP-2.1-0`） | 随任务迁移 |
| `CP-1a.8-*` | **`CP-1a.5-*`** | 随任务前移 |
| `CP-2.1-*` ~ `CP-2.11-*` | **`CP-2.2-*`** ~ **`CP-2.12-*`** | 整体后移一格 |

**未变**：批次 1b / 3 / 4 / 5 的全部任务号与 CP 号；`R-S8-*` / `P-S8-*` / `DA-S8-1` / `AC-S8-*` / `Q-S8-*` 全部号段。

### 14.2 🔴 编号迁移表（v1.1 → **v1.2**，裁定 2 + Maria 加拍的两次搬迁）

> 🔴 **本次刻意采用「后缀编号」而不再整体重编。** 理由：v1.1 那次整体重编留下了**两处残留**（批次 1a 收口任务的**悬空前向依赖**、`CP-2.11-2` 的**配对对象引错**，均已在 v1.2 修复并留痕于 §15）。后缀式**零改动既有 `2-2` ~ `2-12` 的全部交叉引用**，风险最低。

| v1.1 旧号 | v1.2 新号 | 说明 |
|---|---|---|
| `T-S8-1a-4`（coding 侧改词） | **`T-S8-2-1b`** | Maria 加拍迁入批次 2，置于 `T-S8-2-1` 之后 |
| `T-S8-1a-5`（execution 只读工具） | **`T-S8-1a-4`** | 顺位前移一格 |
| `T-S8-1a-6`（execution 上下文注入） | **`T-S8-2-8b`** | 裁定 2 迁入批次 2，置于 `T-S8-2-8` 与 `T-S8-2-9` 之间 |
| `T-S8-1a-7`（1a 收口） | **`T-S8-1a-5`** | 顺位前移两格 |
| `CP-1a.4-*` | **`CP-2.1b-*`** | 随任务迁移 |
| `CP-1a.5-*` | **`CP-1a.4-*`** | 随任务前移 |
| `CP-1a.6-*` | **`CP-2.8b-*`** | 随任务迁移 |
| `CP-1a.7-*` | **`CP-1a.5-*`** | 随任务前移 |

**未变**：批次 2 既有的 `T-S8-2-1` ~ `T-S8-2-12` 与 `CP-2.1-*` ~ `CP-2.12-*` **全部号段一字未动**（这正是选后缀式的收益）；批次 1b / 3 / 4 / 5 全部未变。
| **裁定留痕** | **§16.A / 16.B / 16.C / 16.D**（🟢 零开放项）+ §16.E 架构跟改清单 | 本文 §16 |

---

## 15. 落点勘误留档（本 dev-plan 落盘时逐处 Read / grep 源码发现的出入，2026-08-04）

> **纪律**（沿 sprint7 §14 / §31 / §40 / §48 / §56 / §59 / §63 六处先例）：架构 §12 与 PRD 给的函数名与行号，**逐处上磁盘核实**；对不上的落进本节并写清实际值，**不默默按文档写的行号抄进任务**。
> **核实时点**：2026-08-04，`git status --porcelain` 为空，`HEAD = 191bd93`。
> **总体结论**：架构 §12 的落点清单**绝大多数逐处对得上**（execution 侧 20 余处行号全中，reporting 侧除一处外全中，coding / planning / plan_checks / code_fs_tools / honesty_audit / ui 各处全中）。下表 11 条为出入，**其中 P-S8-3 / P-S8-4 / P-S8-5 / P-S8-9 四条为实质性**。

| # | 文档原文 | 磁盘实测 | 影响 | 处置 |
|---|---|---|---|---|
| **P-S8-1** | 架构 §2.1 写 `ExecutionResult`「…既有 **10** 键一字不动…」 | **实为 11 键**：success / metrics / logs / errors / artifacts / runtime_seconds / environment_info / step_reconciliation / budget_truncated / metrics_groups / degraded_credentials | 低（不改设计），但**"10 键"若被写进断言会当场红** | T-S8-1a-2 按 **11 键**写（加键后 12）；CP-1a.2-2 明写"⚠ 是 11 不是 10" |
| **P-S8-2** | 架构 §12 写 `_render_report`（`:1176`） | **def 实为 `:1172`**（`:1176` 是 `audit` 形参那一行） | 低 | T-S8-3-1 / CP-3.1-1 复核；任务里按 `:1172` 定位 |
| **P-S8-3 🔴 实质** | 架构 §9 复裁项 2 与 §12 把"报告形态三条文案"定位在 `ui/term_map.py:80-82`；**Maria 拍板 2** 也照此引用 | ❌ **`humanize("report_form", ...)` 与 `humanize("conclusion_level", ...)` 全仓零调用点**（`grep -rn` 排除 `.venv` 后为空）⇒ 这两族在 `term_map` 里的**唯一作用是被 `tests/test_s708_user_text_guard.py` 扫进守门覆盖面**。真正到达用户眼前的文案在**另外三处**：①`ui/pages/result_report.py::_FORM_CARD_SPEC`（`:85-118`，四张卡，其中两张是**结论级**文案不是形态文案）；②`core/nodes/reporting.py:859` / `:869` / `:1055`（报告正文三条档位宣告）；③🔴 `core/nodes/reporting.py:522` `` - 报告形态: `{form}` `` —— **直接把内部键印给用户**，MEMORY §4.2 的**既有违反**，两份文档均未提 | **高**：**只改 `term_map:80-82`，用户看到的一个字都不会变** ⇒ Maria 拍板 2 会落空 | T-S8-3-8 单列任务 + 三落点表逐处点名（CP-3.8-3 / 3.8-5 / 3.8-6）；**`:522` 裸键顺带治**，默认**不引入 `core → ui` 反向依赖**（用 reporting 内模块级映射常量 + 进守门），代价"两处描述同一件事"如实登记 ⇒ **R-S8-24**，单点可推翻 |
| **P-S8-4 🔴 实质** | 架构 §5.2 把 `_determine_conclusion` **改名** `_assemble_conclusion`；§12 的 `ui/` 条目只写了 `result_report.py:178` 数据源与档位读法 | ❌ **`ui/pages/result_report.py:59` 直接 `from core.nodes.reporting import _determine_conclusion, _determine_report_form`** ⇒ 改名后**结果页模块导入即崩**（`ImportError`，打开页面就崩，不是跑到某分支才崩）。另 `_conclusion_card_key`（`:121-135`）按 `level == "science"` 分卡，四档制下该分支**恒不成立** ⇒ **所有 full_success 静默落到 `full_success_engineering` 卡**（不报错，最危险的形态）。另全仓 `_determine_conclusion` 引用 **48 处 / 7 个测试文件** + `ui/term_map.py` 注释一处 | **高** | T-S8-3-3 把 `result_report.py` 跟改列入**同一任务**（不拆到 T-S8-3-9）；CP-3.3-2 / CP-3.3-3 两条命门 + CP-3.3-9② 验红 ⇒ **R-S8-20** |
| **P-S8-5 🔴 实质** | **AC-S8-21②**（PRD `:411`）写「执行侧、**编码侧**、规划侧提示词哈希基线**换发**，在开发计划里预先列为预期改动」 | ❌ **编码侧根本没有基线可换发**。全仓 `grep -rn "hexdigest" tests/` 命中的**真 prompt 门只有 3 处**：planning 1（`test_sprint6_b1_prompt_guards.py:79`，`EXPECTED_HASH = "ef6d267030fd2a0c"`）+ execution 2（`test_sprint5_t14_execution_prompt.py` / `test_sprint7_s710_exec_locality.py`）；其余 `hexdigest` 命中（`test_react_base.py` / `test_sprint6_s6_01_controller.py` / `test_sprint6_s6_07_task_status.py` / `test_sprint5_t52_regression_targets.py` / `test_sprint7_s713_reported_metrics.py`）**均非 prompt 门**。**coding 零。** 唯一沾边的 `tests/test_sprint5_t13_coding_prompt.py:180-183` 是 `expected_prefix = _CODING_SYSTEM_PROMPT_BODY + _CODING_HONESTY_SECTION` 的**自锁定形态**（等号两边同源，常量改成什么都恒绿），与 R-S7-41 那道 `x == x`、sprint7 P-27 记的 execution 侧旧门**完全同族**。旁证：sp7 S7-13 已用探针实证"改 coding 提示词零阻力零回归保护"（§63 P-64）；`docs/TODO.md:633` 是一条**开放的 sp7 遗留**，逐字写着「`coding.py` / `resource_scout.py` 两侧 system prompt 主体仍无字节基线守门……**日后改那两处 prompt 时须一并补齐"写死哈希 + 留档 + 验红"三件套**」 | **高**：照"换发"去做，开发会去找一个不存在的基线，任务当场落空，事后又多一条"写了没人做"。且 **S8-02 正是本 Sprint 对编码侧提示词最大的一次改动**（三处 `<METRICS>` 清除 + 补产出约定 + 上下文补两键），偏偏落在唯一没有守门的那个节点上 | ⇒ **口径订正为「新建」**，并**单列独立任务 T-S8-1a-3**（**先建后改**，强制早于 T-S8-2-1b），沿 sprint7 T-S7-6-2 范式：用**改前**哈希建门 → 改 prompt 时门**当场红** ⇒ **那次红本身就是"门是真的"的活体证明**。**这不是扩围，是履行 `docs/TODO.md:633` 登记的前置条件。** ⚠ **`resource_scout.py` 那一侧本 Sprint 不动其 prompt ⇒ 不扩围**，其门仍缺，**属 sp7 遗留、留待日后改那处 prompt 时补齐**（`docs/TODO.md:633` 条目**不因本 Sprint 关闭**） ⇒ **R-S8-21** |
| **P-S8-6** | 架构 §12 只写「`_extract_metrics_block` / `_regex_scan_metrics` / `_llm_extract_metrics` / `_parse_metrics`（含死参数 `plan`）：判定链路解绑」 | ⚠ **未提 `llm_calls_used` 支路**：`_parse_metrics` 的**第二返回值**流向 `_map_execution_result(..., llm_calls_used=...)`（`:3016`）→ 预算扣减 `total_calls = react_rounds_used + llm_calls_used`（`:2533`）。解绑后该支路必须**显式归零**，否则要么 `NameError`、要么被顺手改成别的取值 | 中：漏处置会连带碰预算扣减（本 Sprint 非目标） | T-S8-2-1 第 2 条写死处置方式（`_map_execution_result` 签名不动、调用点不再传或显式传 0，二选一并在实现里注释）+ CP-2.1-3 断言 `total_calls == react_rounds_used` |
| **P-S8-7** | PRD §4.2「⚠ 回归面价签」写「三档函数与零指标相关断言在 `tests/` 下共 **117 处、跨 17 个文件**」 | ⚠ 本计划用同族关键词（`_extract_metrics_block` / `_regex_scan_metrics` / `_llm_extract_metrics` / `_apply_no_metrics` / `no_metrics` / `NO_METRICS` / `<METRICS>`）实测为 **268 处 / 36 文件**。差异应来自关键词集合口径不同（PRD 未给出所用的 grep 表达式，无法逐条比对） | 中：**照 117 对账会有余数，被误判为异常** | 一律**以开工时精确清点为准**（T-S8-1a-1 / CP-1a.1-4），账目闭合的判据是"全量回归零失败 + 每条增减有归属"，**不拿 PRD 的估数当靶**（T-S8-4-1 第 3 条） |
| **P-S8-8** | —— | ⚠ **`_NO_METRICS_EARLY_STOP_SUMMARY`（`execution.py:2715-2718`）不在 `tests/test_s708_user_text_guard.py` 的 `_GUARDED_CONSTANTS` 里**（该表 `:95-115` 共 15 条，含 `_INCOMPLETE_EXECUTION_SUMMARY_LEAD` / `_INCOMPLETE_EXECUTION_FIX_HINT` 却无它）⇒ 它是**直达用户的终态面板文案**却从未被守门扫到（与 sprint7 P-13、S7-11 的 `_SUCCESS_CRITERIA_NOTE` **同款失效模式**） | 中：本批要改它（架构 §4.1 明令换发），改完仍不受守门保护 | T-S8-2-7 第 5 条**顺手补进守门候选**；T-S8-3-10 / CP-3.10-2 对平时逐条落表 |
| **P-S8-9 🔴 实质（已由 v1.2 裁定 1 结案）** | PRD §4.2「⚠ 回归面价签」写「`tests/test_sprint7_s713_reported_metrics.py:994-1005` 的**函数源码字节冻结表至少 6 行必红**」 | ⚠ **口径差**：AC-S8-03 的**判定方式**明写「执行侧三档解析**不再被判定链路调用**（**调用链断言**）」，架构 §12 也写「**判定链路解绑**」——**两者均非"删除函数"**。按"只解绑不删除"，冻结表实际只有 **3 行动**：`_apply_no_metrics` **移出**（函数删除）+ `_build_execution_result` **换发**（新形参 + `success` 改派生）+ `_parse_metrics` **换发**（清死参数）。「至少 6 行」是按**三档函数一并删除**估的 | 中：**照"至少 6 行"当靶会以为漏改了三行**，反过来可能促使开发去删本不该删的函数 | 🔴 **v1.2 结案**：架构师裁定 + Maria 已认 ⇒ **四个函数整体删除**（见 §16.C）⇒ **PRD 的「至少 6 行」与本条口径差一并消解**。冻结表实际账目 = **4 行移出 + 1 行换发 = 5 行动，10 行 → 6 行**（§3.4 A 表已切到删除版）。⚠ **本计划 v1.1 曾把删除代价算成「从 3 推到 6、账目翻倍」，该数字有误，已订正并留痕**：`_parse_metrics` 删了就**不存在「换发」**（是移出）、`_llm_extract_metrics` **本就不在冻结表 10 行内**。⚠ **批次归属**：删除落在 **T-S8-2-1**（批次 2 首位），逐行对账落在 **T-S8-2-12** —— **五行动全部发生在批次 2 内部，批次 1a 不碰冻结表** |
| **P-S8-10** | 架构 §2.5.4 红线 3 写「`success_criteria` 在 `core/` 下的出现点**只允许三处**——`state.py`（声明）、`planning.py`（生产）、`_build_execution_agent_context` + `coding.py` 上下文（注入）」 | ⚠ **数法差异**：它把"`_build_execution_agent_context` + `coding.py` 上下文"并成了一处，逐文件数实为 **4 处**；再加批次 3 新增的 `reporting._render_success_criteria` 一处读点，**共 5 处** | 低（**非设计差异**），但**照"三处"写静态断言会当场红** | §0.5 红线 3 已按逐文件实际处数改写并注明；断言按实际处数写 |
| **P-S8-11** | 架构头部「架构级结论」写「…+ **1 个** reporting 侧纯函数…」 | ⚠ **实为 2 个**：§5.6(B) 裁了 `_render_audit_findings`、§5.7 裁了 `_render_success_criteria` | 低（表述差） | §1.2 已按 2 个写并注明 |

**另有两处 PRD 引用的行号需微调（影响低，一并留档）**：
- PRD §4.9.4 引 `plan_checks.py:283-287` 说"贴写形态是已登记接受的漏判"——实测**贴写形态在 `:282-284`**，`:285-287` 是"引号内的裸元字符"那条（**另一条**误伤登记）。**两条结论均不变**（都是已登记接受的残留），仅行号需订正。
- PRD §4.1.2 引 `core/state.py:75-85` 说论文分析已产出 `metrics` / `datasets` / `baseline_results` / `method_summary`——实测 `method_summary` 在 **`:73`**（在所引范围之外），其余三者在 `:75` / `:76` / `:80` 内。**结论不变。**

### 15.0 🔴 v1.3 新增勘误（跟改 PRD v4.1 / 架构 v2.4 时上磁盘发现，2026-08-06）

| # | 文档原文 / 预期 | 磁盘实测 | 影响 | 处置 |
|---|---|---|---|---|
| **P-S8-12 🔴🔴 实质·本次最重要** | 架构 §16.5① 只写「每个 `title` / `note` / `cell` 过 `mask_value`」，**未说非 str 怎么办**；而 §16.2③ 只在 **schema** 层写「单元格一律字符串」，架构 §16.2 技术依据 2 又自认**主通道是自由 JSON、实测非 strict** ⇒ **schema 拦不住非 str** | ❌ **实测 `core/secrets_store.py:261` `mask_value` 对非 str 的行为取决于运行时是否注册了凭证**：**无凭证**（`known` 为空，`:280-281` 提前 `return text`）⇒ **静默原样返回，脱敏被完全跳过、零日志**；**有凭证**（走到 `:283-284` 的 `masked.replace(...)`）⇒ 🔴 **抛 `AttributeError: 'int' object has no attribute 'replace'`**。已用 `register_sensitive_value` 实测复现五种类型（int / float / bool / list / dict）**全部抛异常** | 🔴 **高**：**测试环境通常无凭证 ⇒ 单测与集成回归全绿；真跑时凭证已注册 ⇒ 当场炸节点。这是一条环境相关、收口门抓不到的路径。** 且它正是 **BUG-S8-01 缺陷类别的转移落点**（原址随 `_split_reported_metrics` 删除而消失） | **DA-S8-1 换发**（不注销）：`_collect_result_blocks` 必须**先确定性转字符串再脱敏**；容器形态**置占位符、不做 `str()` 强转**（防 Python repr 进报告，同 BUG-S8-01 当初写死的理由）；畸形一律 WARNING。落 **T-S8-2-10b / CP-2.10b-7**，含**"已注册凭证"前置下的验红**（不构造该前置这条用例会**假绿**）。⚠ **架构未覆盖此点，已如实登记，交主控转架构师知会** |
| **P-S8-13** | 架构 §12 `core/state.py` v2.4 条目未指明**删键落哪一批** | ⚠ **本计划实测判定必须落批次 2 且与 `_build_execution_result` 原子同批**：`/tmp` 最小复现实测 —— `R(success=True, logs="x", metrics={})` → `error: Extra key "metrics" for TypedDict "R" [typeddict-unknown-key]`。⇒ 只要形参还在传，声明就不能先删。而 `mypy.ini:43` `files = core` 覆盖 `state.py` 与 `execution.py`，各批收口门都要求 mypy 零错误 ⇒ **拆到两批之间必然红一整批**。⚠ **读侧不受此限**（同次实测：`r.get("metrics")` 读未声明键 mypy 不报错）⇒ reporting / ui 消费点可留到批次 3 | 中：批次归属若判错会让批次 1a 或 2 的收口门无法通过 | **并入 T-S8-2-8**（与形参删除原子同批），理由与实测证据已写进该任务体 + CP-2.8-16 自证 |
| **P-S8-14** | 本计划 v1.2 §3.4 A 表账目「冻结表 10 行 → 6 行」 | ⚠ **失真**：架构 v2.4 §16.6 新增 `_collect_grouped_metrics` **整体删除**（**推翻架构 v2.1 §13 的"不删、不改"**），而它**本来就在冻结表 10 行内** ⇒ 实际是 **5 行移出 + 1 行换发 = 6 行动，表 10 行 → 4 行** | 中：照 v1.2 的账对不平 | §3.4 A 表已切到 v1.3 版并逐行订正 |
| **P-S8-15** | 架构 §12 `reporting.py` v2.2 条目已注明「`_flatten_entries`（`:440`）与 `_fmt_metric_value`（`:415`）**不删**」 | ✅ **复核属实且必须照办**：两者另有消费者——`_fmt_metric_value` 在 `:898`（执行总耗时）、`_fmt_metric_value` 与 `_md_escape_inline` 在 `:924-925`（环境信息节）。⚠ **`_flatten_mapping`（`:474-486`）的三个消费者 `:963` / `:968` / `:998` 全在被删的 `_render_metrics_comparison` 内** ⇒ 它才是该随之删的那个 | 中：**误删 `_flatten_entries` / `_fmt_metric_value` 会打掉环境信息节渲染** | T-S8-3-2 已逐条点名"不删"，并写明各自的消费者行号 |

### 15.1 prompt 主体字节基线留档（三件套第③件，**开工时逐行补齐**）

> **纪律**：不做这一节等于门白建（sprint7 §48.1 / §40.1 范式）。**每一行都必须在对应任务收口时补齐，禁止事后补记**（架构 §6.2④）。

| 侧 | 门的落点 | 旧哈希 / 长度 | 新哈希 / 长度 | 变更原因 | 建/换发任务 | 状态 |
|---|---|---|---|---|---|---|
| **coding** | 🔴 **本 Sprint 新建**（P-S8-5） | 待 **CP-1a.1-5** 现算（sprint7 §60.2 事实 15 记为 `37ec6ee2b1606715` / 3052 字符，**须复核**） | 待 **CP-2.1b-8** 补 | S8-02：三处 `<METRICS>` 教学文本清除 + 补产出约定 | 建门 T-S8-1a-3 / 换发 T-S8-2-1b | ⬜ 待开工 |
| **coding（自锁定证否留档）** | —— | —— | —— | **CP-1a.3-6 实测记录**：把门临时改成 `EXPECTED = actual` 形态后再插空格，**它不会红** —— 把"假门长什么样"实测一遍存证 | T-S8-1a-3 | ⬜ 待开工 |
| **planning** | `tests/test_sprint6_b1_prompt_guards.py:79` | `ef6d267030fd2a0c`（**须 CP-1b.1-5 复核，并确认右侧是字面量非自锁定**） | 待 **CP-1b.2-9** 补 | S8-01 扩围：交付清单语义扩围 + `expected_output` 路径要求 + `success_criteria` 三条约束 | T-S8-1b-2 | ⬜ 待开工 |
| **execution** | `tests/test_sprint5_t14_execution_prompt.py:219` **+** `tests/test_sprint7_s710_exec_locality.py:653`（**两处同步**） | 待 **CP-2.2-2** 现算 | 待 **CP-2.9-9** 补（**两处一致**） | S8-04/05：判定纪律段改写 + 四档语义段 + 输出要求段 + 只读工具说明 + `baseline_results` 配套约束（**同一次改写，只换发一次**，架构 §6.2④） | T-S8-2-9 | ⬜ 待开工 |
| **resource_scout** | 🔴 **仍无门** | —— | —— | **本 Sprint 不动其 prompt ⇒ 不扩围**。属 sp7 遗留，`docs/TODO.md:633` 条目**不因本 Sprint 关闭**，留待日后改该处 prompt 时按同一"三件套"补齐 | —— | ⬜ 遗留 |

### 15.2 CP 期望值翻转留档（**不可用窗口的账**）

> ⚠ 拍板 2 之后，**翻转的起止两端都落在批次 2 内部**（起于 T-S8-2-1、终于 T-S8-2-11）。

| CP | 原期望（批次 1a） | 翻转后期望（批次 2） | 翻转时点 | 说明 |
|---|---|---|---|---|
| **CP-2.1-5** | 同一构造输入下 `metrics == {}`（门控不采信）+ `success is False` | 同一构造输入下 `metrics` **非空** + 档位判出 + `success` 按 `level` 派生 | **T-S8-2-11**（对应新 CP = **CP-2.11-2**） | 沿 sprint7 §49.0「删原内容 → 换发新 CP」先例。**翻转时点须在此表落档**，否则后人看到两条互斥的 CP 会以为其中一条写错了。对应新 CP = **CP-2.11-2** |

---

## 16. 裁定留痕（🟢 **零开放项**）

> **纪律**：本节每一项都是**本计划落盘时在磁盘上发现、但两份定稿文档未覆盖或存在张力**的点。按开发代理定义的触发条件，**给默认取值但不自行拍板改设计**。**截至 v1.2，四项全部已裁定，无开放项。**

### 16.0 状态总览

| 项 | 议题 | 状态 | 裁定 |
|---|---|---|---|
| **16.A** | AC-S8-14「可编辑」的可执行口径 | ✅ **已裁定（Maria 2026-08-04 拍板 1）** | **按只读展示验收**；「可改」走既有多轮对话修订通道，**不新增交互控件种类** |
| **16.B** | 通道退场任务要不要挪到批次 2 首位 | ✅ **已裁定（Maria 2026-08-04 拍板 2）** | **挪**。现 `T-S8-2-1`，不可用窗口整体压进批次 2 内部 |
| **16.C** | 三档 + `_parse_metrics` 删不删 | ✅ **已裁定（架构师裁定 + Maria 已认，2026-08-04）** | 🔴 **四个函数整体删除**——**推翻本计划 v1.0/v1.1 的默认取值 A** |
| **16.D** | execution 上下文注入落哪一批 / 配套 prompt 约束落哪一批 | ✅ **已裁定（架构师裁定 2 + Maria 加拍，2026-08-04）** | **注入整体迁进批次 2**（`T-S8-2-8b`）；**注入与其配套提示词约束必须同批落地**，哈希基线仍只换发一次 |

### 16.A ✅ 已裁定：AC-S8-14「可编辑」按只读展示验收（**Maria 2026-08-04 拍板 1**）

- **落点**：T-S8-1b-4（完整裁定留痕表已就地写在该任务体内，**含两条理由与 AC 的可执行读法**）。
- **一句话**：成功标准在审核页**顶部只读、显眼**展示；「可改」走**现成的多轮对话修订通道**，**不新增任何交互控件种类**。
- **理由**：①新增就地编辑控件**直接违反 PRD 非目标 8 与 §4.11.2**；②**本项目一贯反对为单个字段新增交互形态**（MEMORY §4.1；判例：sprint4 交互工具 5 种 `input_type` 枚举被否、收敛为单个 `request_user_input`）。
- **验收落点**：CP-1b.4-1 + CP-1b.4-2 + **CP-1b.4-2b**（改后取值 + **负向：交互控件计数与改前逐一相同**）。

### 16.B ✅ 已裁定：通道退场任务挪到批次 2 首位（**Maria 2026-08-04 拍板 2**）

- **落点**：完整裁定留痕表在 **§0.0**。
- **一句话**：原 `T-S8-1a-7` **挪到批次 2 首位（现 `T-S8-2-1`）** ⇒ 不可用窗口**整体落进批次 2 内部**。
- **配对约束**：`T-S8-2-1`（撤旧判据分子）与 `T-S8-2-8`（装新判据）**是天然配对、中间不得留缝**；批次 2 **本就受 R-S8-06「内部不得拆分」约束**。

### 16.C ✅ 已裁定：三档 + `_parse_metrics` **四个函数整体删除**（**架构师裁定 + Maria 已认，2026-08-04**）

> 🔴 **本项推翻本计划 v1.0 / v1.1 的默认取值 A（"解绑但保留"）。** 三条保留理由逐条结案如下，**不静默改掉**：

| # | 本计划原保留理由 | 结案 |
|---|---|---|
| ① | 「AC-S8-03 的判定方式只要求"不再被**判定链路调用**"」 | ✅ **不冲突**：**删除是该断言的加强版**——符号都不存在了，自然不可能被调用。⇒ CP-2.1-1 由「调用链断言」**升级换发**为「符号不存在 + 全仓引用清零」 |
| ② | 「PRD §4.2 第 5 条要清 `_parse_metrics` 的死参数 `plan`，这个动作预设了它还在」 | 🔴 **必须留痕的一条**：**删函数严格强于清死参** ⇒ PRD §4.2 **第 5 条被「超越」而非「违反」**。**PRD 一律不改（铁律）**，本行即其留痕 |
| ③ | 「删除会把冻结表必红行数从 3 推到 6，账目翻倍」 | ❌ **这个数本计划算错了，如实登记**：`_parse_metrics` 删了就**不存在「换发」**（是移出）、`_llm_extract_metrics` **本就不在冻结表 10 行内**。实际是 **4 移出 + 1 换发 = 5 行动，表由 10 行缩到 6 行**，不是"翻倍" |

- **删除面**：`_extract_metrics_block`（`:402-423`）/ `_regex_scan_metrics`（`:426-449`）/ `_llm_extract_metrics`（`:452-514`）/ `_parse_metrics` + 调用点（`:517-550` / `:2935`）+ `<METRICS>` 标签常量与 pattern（`:393-399`）+ 模块 docstring `:9-10` / `:26-27` 订正。**落在 T-S8-2-1**；引用清零与冻结表对账**落在 T-S8-2-12**。
- 🔴 **不在删除面内**（一并清零会当场打掉旧快照兼容与唯一的扫盘兜底）：`_collect_grouped_metrics` / `ErrorCategory.NO_METRICS` 枚举成员 / `ui/term_map.py` 的 `error_category:no_metrics`。
- ✨ **附带红利**：`_llm_extract_metrics` 是 execution 主体在 ReAct 子图**之外唯一的 LLM 调用入口** ⇒ 删除后「执行主体不调 LLM」由"目前恒成立"升级为"**结构上不可能不成立**"（CP-2.1-4b 自证）。
- **架构文档同批跟改**：架构 v2.1 §12 / §7 末段 / §13 三处（见 §16.E 的跟改清单）。

### 16.D ✅ 已裁定：execution 上下文注入整体迁进批次 2（**架构师裁定 2 + Maria 加拍，2026-08-04**）

- **两个任务迁入批次 2**：`T-S8-1a-6`（execution 上下文注入）→ **`T-S8-2-8b`**（置于 T-S8-2-8 与 T-S8-2-9 之间）；`T-S8-1a-4`（coding 侧改词）→ **`T-S8-2-1b`**（置于 T-S8-2-1 之后）。完整留痕在两个任务体内。
- **本计划原理由为什么失效**：v1.1 把注入留在 1a 的理由**逐字**是「1a 之后本来就不得真跑」——**拍板 2 之后 §0.0 结论恰好相反**，论据整条失效。
- **架构师另指出一条本计划未察觉的**：🔴 **`success_criteria` 的诱导性比 `baseline_results` 更强**（直接告诉 agent"达到什么算过"），而 1a 期间系统提示词还在说「你不判定复现是否成功」——**给了及格线又说你不判**，自相矛盾。
- **Maria 加拍连带**：coding 侧改词若留在 1a，教学文本一清 ⇒ **档 1 从 1a 起实际失效** ⇒ 「1a 可演示」名不副实。迁走后该隐患**自动消解**（§0.0 已留痕）。
- 🔴 **`T-S8-1a-3`（新建 coding 字节门）留在批次 1a 不动**：零生产改动、零行为变化 ⇒ **「先建后改」次序改为「1a 建门 → 批次 2 改词」，跨批次仍然成立且更稳**。
- **v1.1 的「§16.2 待确认」旧议题一并消解**：注入与其配套提示词约束现已同批（架构 v2.1 §6.2 新增第⑥条），**哈希基线仍只换发一次**。

### 16.E 📌 架构文档同批跟改（`docs/sprint8/architecture.md` v2.0 → **v2.1**）

| # | 落点 | 跟改内容 | 来源 |
|---|---|---|---|
| 1 | §12 `execution.py` 第 2 条 | 「判定链路解绑」→ **「整体删除（含 `<METRICS>` 标签常量与 pattern）」** + 注明**超越** PRD §4.2 第 5 条 | 裁定 1 |
| 2 | §7 末段边界澄清 | 补一句「本次删除面 = 四个三档函数；`NO_METRICS` 枚举成员与 `_collect_grouped_metrics` **均不在内**」 | 裁定 1 |
| 3 | §13 | 补一句「`_collect_grouped_metrics` 与三档**不同类**：前者有生产调用者、数据源本 Sprint 被强化；后者**输入源同批拆除**」 | 裁定 1 |
| 4 | §11 批次表 | `S8-10`（execution 侧注入）由 1a 行**移到批次 2 行**；`S8-02` 的**编码侧**部分同步移到批次 2 行（**字节门 `T-S8-1a-3` 仍留 1a**）；前置约束①改为「1a 仍依赖 `success_criteria` **字段名**」 | 裁定 2 + Maria 加拍 |
| 5 | §6.2 | 新增第⑥条：「**注入与其配套提示词约束必须同批落地**；本项因此与 T-S8-2-9 同批，哈希基线仍只换发一次」。**§6.1 配套约束原文不动**——它现在才真正被满足 | 裁定 2 |

🔴 **PRD 一律不改（铁律）**。两处与 PRD 的关系在此留痕：
1. **本裁定与 PRD §4.2 第 4 条字面一致**（"三档退场"），**第 5 条被超越**（删函数强于清死参）；
2. **PRD §10 把 S8-10 放批次 1**，本次移到批次 2 属**批次内实现顺序调整**，与拍板 2 同性质，**已经 Maria 拍板**。

---


*（Sprint 8 开发计划 **v1.2** 完。**6 个批次 / 39 个任务 / 300 个检查点 / 11 条落点勘误 / 7 条新登记风险（R-S8-18~24）/ 1 条新登记缺陷（BUG-S8-01，验收点 DA-S8-1）/ **4 项裁定全部结案（§16.A~D，🟢 零开放项）+ 架构文档同批跟改至 v2.1（§16.E）**。** 本计划只写规格与检查点，**未写任何生产代码、未跑任何测试、未改 `docs/TODO.md`**。）*

*⚠ **三条必须被读到的东西**：①**§0.0 的可用性中间态告示**（批次 1a 到批次 2 之间不得真跑、不得演示）；②**§0.5 的两层分离总纲**（第一层进系统提示词 + 模块常量、第二层走 HumanMessage，`_decide_conclusion` 不得读 `success_criteria`）；③**§0.1 的批次边界逐批确认制**（五道停手线，对某批的授权 ≠ 对后续批次的授权）。*
