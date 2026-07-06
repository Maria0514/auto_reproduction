# Sprint 3 产品需求文档 (PRD)

**产品名称**：Auto-Reproduction —— 论文自动复现系统
**Sprint**：Sprint 3 —— 端到端复现打通（单 agent 修复循环）
**版本**：v1.0
**日期**：2026-06-15
**作者**：产品经理代理
**状态**：正式版

> **默认参数变更注记（2026-06-30，Maria 拍板）**：修复循环相关三常量默认值已放大以支撑真实生产环境收敛（3 轮在真实环境收敛不了问题）：`MAX_FIX_LOOP_COUNT` 3 → **10**、`MAX_DEV_LOOP_LLM_CALLS` 20 → **60**、`MAX_TOTAL_LLM_CALLS` 50 → **120**（强约束 `MAX_DEV_LOOP_LLM_CALLS < MAX_TOTAL_LLM_CALLS` 不变，60 < 120）。本文档正文涉及这些数值处已就地更新为新值；§8 全部决策记录（含 Q-S3-03/04/06 等）为立项时点叙述（如拍板上限=3 / 子预算=20），数值以正文与 config.py 为准，仅作为立项考据，**当前生效值以本注记与配置表为准**。

---

## 目录

1. [Sprint 3 概述](#1-sprint-3-概述)
2. [功能需求](#2-功能需求)
3. [非功能需求](#3-非功能需求)
4. [数据结构定义](#4-数据结构定义)
5. [预算与配置](#5-预算与配置)
6. [验收标准](#6-验收标准)
7. [依赖与风险](#7-依赖与风险)
8. [开放问题与决策记录](#8-开放问题与决策记录)

---

## 1. Sprint 3 概述

### 1.1 Sprint 目标

Sprint 3 的目标是在 Sprint 1/2 已打通的"论文输入 → 分析 → 资源评估 → 规划（人在回路审核）"链路之上，**首次打通从论文到复现报告的完整端到端流程**，包括：

- 把 `coding` / `execution` / `reporting` 三个 Sprint 1/2 的 pass-through 占位节点替换为真实业务实现；
- 实现本地 venv sandbox 执行环境（`python -m venv` + pip + 子进程 + 基础资源护栏），**不引入 Docker/容器**；
- 实现 **单 agent 的 coding↔execution 修复循环**（首次激活 `fix_loop_count` / `fix_loop_history` 等长期处于"死字段"状态的修复循环字段，上限 10 回合）；
- 实现 dev_loop 修复耗尽后的"第二个 interrupt 人在回路"（沿用 sp2 interrupt/resume/cancel 范式，用户三选一：终止 / 改计划 / 导出代码）；
- 实现 `code_only` 模式的执行路径（只生成代码、跳过 execution、不进修复循环，直接出报告）；
- 实现 Streamlit Web UI 的后 2 个页面（执行监控页 + 结果报告页），**两页都完整实现，不简化**；
- 落地预算/配置缺口（新增 `MAX_DEV_LOOP_LLM_CALLS=60` 子预算、统一 `MAX_FIX_LOOP_COUNT=10`、补上修复循环对全局预算的扣减回写）。

Sprint 3 完成后，**真实用户**应能够通过浏览器输入 arXiv ID，一路走完"分析 → 规划 → 自动编码 → sandbox 执行 → 失败自动修复 N 次 → 仍失败人工决策 → 复现报告"的完整闭环，并在结果报告页查看指标对比、降级原因与产物清单。

> **重心说明（2026-06-14 Maria 决策，引自 `docs/sprint3/dev-loop-compatibility-matrix.md` §0 方向调整）**：Sprint 3 **不做** dev_loop 真 multi-agent（Coder/Executor/Reviewer + supervisor + 共享 scratchpad），该项顺延 Sprint 4+。Sprint 3 的修复循环是 **单 agent 的 coding↔execution 回边**（复用现有 ReAct wrapper 范式 + 主图加回边），先把端到端复现跑通。

### 1.2 范围边界

**Sprint 3 包含**：

| 编号 | 模块 | 产出文件 | 优先级 |
|------|------|---------|------|
| S3-01 | sandbox 本地 venv 执行环境 | `sandbox/local_venv.py` | MVP |
| S3-02 | coding 节点真实现 | `core/nodes/coding.py` | MVP |
| S3-03 | execution 节点真实现 | `core/nodes/execution.py` | MVP |
| S3-04 | 单 agent 修复循环（coding↔execution） | `core/graph.py` + S3-02/S3-03 节点 | MVP |
| S3-05 | reporting 节点真实现 | `core/nodes/reporting.py` | MVP |
| S3-06 | code_only 路径（路由 + 模式分支） | `core/graph.py` | MVP |
| S3-07 | dev_loop 失败人在回路（第二个 interrupt + 三选一） | `core/graph.py` + UI | MVP |
| S3-08 | 预算 / config 落地 | `config.py` | MVP |
| S3-09 | state 字段微调（FixLoopRecord 视需要追加） | `core/state.py` | MVP |
| S3-10 | UI 执行监控页 + 结果报告页（两页都完整做） | `ui/pages/execution_monitor.py` + `ui/pages/result_report.py` | MVP |

**Sprint 3 不包含（明确不做）**：

- **dev_loop 真 multi-agent**：supervisor 模式 + Coder/Executor/Reviewer 三 agent 子图 + 共享 scratchpad + `DevLoopState` —— **全部顺延 Sprint 4+**（兼容性矩阵 §0.3 / §A.4 / §D.6）；
- **Docker / 容器化 sandbox、跨机远程执行** —— Sprint 3 只做本地 venv（Q-S3-02）；
- **dev_loop 回合级断点续跑**（`checkpoint_ns` 命名空间隔离 + 中途崩溃不重跑前几回合）—— MVP 不做，修复循环对主图是"整节点重跑"语义（兼容性矩阵 §C.2）；
- **指标与论文 baseline 的强容差校验**：复现"成功"判据采 B 档（见 §1.5 / Q-S3-01），指标是否达标由 reporting 做**对比展示**，**不作硬验收**；
- **GPU 多卡调度 / 分布式训练**；
- **给 `node_errors` / `degraded_nodes` / `fix_loop_history` 等无 reducer 的 List 字段加 `operator.add` reducer** —— **严禁**（must-fix-1，见 §4.3 / §1.6），会破坏 sp1/sp2 所有节点的"return 整列表"写法。

### 1.3 与整体产品的关系

```
七步流程与 Sprint 对应关系：

步骤 1: 论文输入与解析 (paper_intake)        <-- Sprint 1 已实现
步骤 2: 深度论文分析 (paper_analysis)         <-- Sprint 1 已实现
步骤 3: 资源搜集与评估 (resource_scout)       <-- Sprint 2 已实现
步骤 4: 复现规划与用户审核 (planning)         <-- Sprint 2 已实现（含 interrupt 人在回路）
步骤 5: 编码与环境搭建 (coding)               <-- Sprint 3 实现（单 agent）
步骤 6: 执行与测试验证 (execution)            <-- Sprint 3 实现（本地 venv sandbox）
        coding↔execution 单 agent 修复循环   <-- Sprint 3 实现（上限 10 + 第二个 interrupt）
步骤 7: 报告生成 (reporting)                  <-- Sprint 3 实现
```

Sprint 3 对应技术架构文档（`docs/technical-architecture.md`）§13 的"阶段 3：端到端复现"，并据 `docs/sprint3/dev-loop-compatibility-matrix.md` 的立项结论（条件性可立项 + 2 项 must-fix + 5 处偏差消歧）收敛。

### 1.4 目标用户

Sprint 3 延续 Sprint 2 的目标用户（**CS 在校学生与科研工作者**，详见产品设计说明书 §2）。Sprint 3 是**首个能让用户跑完整条复现流程**的 Sprint：用户首次能从"输入 arXiv ID"一路得到"复现报告"，对自动编码质量、执行成功率、修复循环的修复力、降级交付的可读性、失败人工决策的流畅度有真实感知。

### 1.5 复现"成功"判据（Q-S3-01，采 B 档）

Sprint 3 复现"成功"的判据固定为 **B 档**（Maria 拍板）：

- **成功条件**：代码无报错跑完（子进程 `exit code == 0`）**且** 能从执行输出中解析出至少一个关键指标数值，写入 `ExecutionResult.metrics`；
- **不强制**指标与论文 baseline 对齐：指标是否达标由 reporting 节点做**对比展示**（并列论文 baseline 与本次复现值），**不作硬验收**；
- 这是产品级"成功"定义，对应 `ExecutionResult.success = True` 的写入条件（详见 §2.3 S3-03）。

### 1.6 must-fix 钉死（兼容性矩阵 §D.3）

Sprint 3 PRD 必须显式钉死以下 2 项，并列入验收：

- **must-fix-1（list 合并策略）**：`node_errors` / `degraded_nodes` / `fix_loop_history` 在 `core/state.py` 是**无 `operator.add` reducer 的普通 `List`**（[代码实证] state.py L189/L190/L193）。修复循环写入这三个字段时**必须走单点 read-modify-write 合并**（复用 sp1/sp2 既有"读出整列表 → append → return 整列表"范式，参 `paper_analysis.py` L436 起）。**严禁**为图省事给这三个字段加 reducer —— 那会让 sp1/sp2 所有"return 整列表"的节点变成重复累加，是 breaking change。详见 §4.3 / AC-S3-05。
- **must-fix-2（预算扣减缺口）**：当前修复循环路径不走 `_make_react_wrapper` 的预算扣减逻辑（占位节点对 `retry_budget_remaining` 零扣减，[代码实证] 兼容性矩阵 §B.1）。Sprint 3 必须**按实际 LLM 调用次数主动单点回写 `retry_budget_remaining`** + 引入 `MAX_DEV_LOOP_LLM_CALLS=20` 子预算 + 入口预算门。详见 §5 / AC-S3-04。

---

## 2. 功能需求

### 2.1 S3-01: sandbox 本地 venv 执行环境 (`sandbox/local_venv.py`)

**目的**：提供受控的本地代码执行能力，作为 execution 节点跑生成代码的基础设施。**Sprint 3 采本地 venv + 基础资源护栏（Q-S3-02），不引入 Docker/容器、不做跨机远程执行**。

**详细要求**：

- 提供环境准备能力：基于 `python -m venv` 在工作目录下创建隔离虚拟环境，并用该 venv 的 pip 安装 `ReproductionPlan` / 仓库 `requirements`（`requirements.txt` / `environment.yml` / `pyproject.toml` 任一）声明的依赖；
- 提供子进程执行能力：在隔离 venv 中以子进程方式执行 `ReproductionPlan.execution_steps` 中的命令/脚本，捕获 stdout / stderr / exit code / 运行时长；
- **基础资源护栏（4 项，硬要求）**：
  1. **执行超时**：子进程超过超时上限（`config` 可配，建议默认值见 §5）即强制终止，标记为超时类错误（疑似死循环，归不可自动修复类，见 §2.7 Q-S3-07）；
  2. **输出大小上限**：stdout/stderr 捕获超过上限即截断并标记，避免日志撑爆内存/checkpoint；
  3. **工作目录限定**：所有读写副作用严格限定在 `workspace_dir`（venv、代码、产物、日志均落在 `workspace_dir` 下），不允许越界写其它路径（沿用 sp2 S2-01 路径越界约束）；
  4. **子进程隔离**：执行使用独立子进程，异常/崩溃不污染主工作线程。
- 返回结构化执行结果（供 execution 节点映射为 `ExecutionResult`），至少含：`exit_code` / `stdout` / `stderr` / `duration_seconds` / `timed_out`（bool） / `output_truncated`（bool） / `env_info`（python 版本、关键依赖版本等）。

> **建议转架构师**：护栏的具体落点（超时如何实现跨平台子进程 kill、输出截断的字节阈值、venv 复用/隔离策略、pip 安装失败的重试与降级、资源护栏与 `config` 常量的映射）属技术实现细节，由架构师代理在 sp3 架构文档中细化。本 PRD 只定产品级护栏要求（超时 / 输出上限 / 目录限定 / 子进程隔离 4 项必须生效）。

**输入**：代码目录（`code_output_dir`）、依赖声明、执行命令列表、超时/输出上限等护栏参数。

**输出**：结构化执行结果字典。

**边界条件**：

- 同一任务内重复执行同一目录时，应支持复用已建好的 venv（避免重复 pip 安装），由调度方决定；
- venv 创建失败 / pip 安装失败属可自动修复类的一部分（缺依赖、import 错），按 §2.7 分类送回 coding 重试；
- 执行产物（artifacts，如模型权重、图表、结果文件）路径必须可被 execution 节点收集进 `ExecutionResult.artifacts`。

**依赖关系**：依赖 `config.py`（workspace_dir + 护栏常量）+ `core/errors.py`。被 `core/nodes/execution.py` 依赖。

---

### 2.2 S3-02: coding 节点真实现 (`core/nodes/coding.py`)

**目的**：把 Sprint 1/2 的 `coding` pass-through 占位（`core/graph.py` L57-60）替换为真实编码节点：基于复现计划与选定仓库，生成/适配可执行代码并写入 `code_output_dir`。

**详细要求**：

- **节点函数签名**：`def coding(state: GlobalState) -> dict`，沿用现有 ReAct wrapper 范式（建议由 `_make_react_wrapper()` 工厂生成，与 sp1/sp2 节点同构；wrapper 边界与预算扣减由 ReAct 路径承载，见 §5）；
- **输入依据**：
  - `reproduction_plan`（`code_strategy` / `execution_steps` / `data_preparation` / `environment` 等）；
  - `resource_info.selected_repo`，尤其是 `selected_repo.local_path`（sp2 已落地的本地仓库绝对路径，coding 直接复用，无需重新 clone）；
  - `paper_analysis` 的英文备份字段（`method_summary_en` / `hardware_requirements_en` / `datasets` / `framework` 等英文事实层字段，避免中文 prompt 喂代码生成造成中英混杂，沿用 sp2 §4.7.5 协作约束）；
- **两种代码策略**：
  - `use_repo + 适配`：在 `selected_repo.local_path` 基础上做最小适配（补缺、改路径、对齐数据集/超参），不重写整仓；
  - `from_scratch`：当 `resource_strategy == "from_scratch"` 或无可用仓库时，从零生成可执行代码骨架；
- **执行模式区分（与 S3-06 联动）**：节点照常生成代码，**不自建 `coding_only` 节点**（Q-S3-05）；执行模式的跳过逻辑由路由 + 模式判断实现（见 §2.6 S3-06）。无论 `execution_mode` 是 `FULL` 还是 `CODE_ONLY`，coding 都正常产出代码到 `code_output_dir`；
- **修复回合内的行为（与 S3-04 联动）**：当 coding 是被 execution 失败回边触发的"修复回合"时，节点必须读取上一轮 execution 反馈（错误摘要 + 错误分类），有针对性地修改代码（注入 HumanMessage：上轮 stderr / 错误类别 / 修复建议），而非从头重生成；
- **产出**：把生成/适配后的代码写到 `code_output_dir`（位于 `workspace_dir` 下），并在状态中写 `code_output_dir`。

> **建议转架构师**：coding 节点在修复回合中如何拿到上一轮 execution 的结构化反馈（是直接读 `execution_result` 还是经一条精简 feedback 字段）、错误反馈注入 HumanMessage 的具体上下文裁剪策略，属实现细节，交架构师细化。

**输入**：`GlobalState`，含 `reproduction_plan` / `resource_info` / `paper_analysis`，修复回合时含上一轮 `execution_result`。

**输出**：状态更新字典，至少含 `code_output_dir` / `current_step`，必要时 `node_errors` / `degraded_nodes`（走单点 read-modify-write，遵守 must-fix-1）。

**边界条件**：

- `selected_repo` 为 None（resource_scout 完全失败、from_scratch）时，coding 必须仍能产出最简可执行骨架；
- coding 自身 ReAct 失败（LLM 不可用等）：写 `node_errors` + 加 `degraded_nodes`，按降级处理（不死循环重试）。

**依赖关系**：依赖 `core/state.py` / `core/react_base.py` / `core/llm_client.py` / `core/tools/*`。被 `core/graph.py` 注册调用，与 `execution` 节点构成修复回边。

---

### 2.3 S3-03: execution 节点真实现 (`core/nodes/execution.py`)

**目的**：把 `execution` pass-through 占位（`core/graph.py` L63-66）替换为真实执行节点：在 sandbox 中跑 coding 产出的代码，产出结构化 `ExecutionResult`，并对失败做错误分类。

**详细要求**：

- **节点函数签名**：`def execution(state: GlobalState) -> dict`；
- **执行流程**：调用 S3-01 的 sandbox 能力，准备 venv → 装依赖 → 按 `ReproductionPlan.execution_steps` 顺序执行 → 捕获结果；
- **产出 `ExecutionResult`**（复用 state.py 既有 7 字段 TypedDict，§4.1）：
  - `success`：按 **B 档判据**（§1.5）写入——`exit_code == 0` 且 `metrics` 至少解析出一个关键指标数值 → `True`；否则 `False`；
  - `metrics`：从执行输出解析出的关键指标数值（指标名沿用 `paper_analysis.metrics` 英文事实字段做对齐）；
  - `logs`：执行日志（受输出大小上限护栏约束，超限截断）；
  - `errors`：错误信息列表（失败时填）；
  - `artifacts`：执行产物路径清单（模型权重 / 图表 / 结果文件）；
  - `runtime_seconds`：运行时长；
  - `environment_info`：python 版本、关键依赖版本等；
- **错误分类（Q-S3-07，关键）**：执行失败时，节点必须对错误做分类，区分**可自动修复类**与**不可自动修复类**（分类原则见 §2.7），分类结果驱动 S3-04 的路由决策（送回 coding 重试 vs 直接走失败 interrupt/降级）；
- **执行模式联动**：`execution_mode == CODE_ONLY` 时，execution 节点**不应被进入**（由 S3-06 路由跳过），无需在节点内做模式判断；
- **错误处理**：遵循节点统一错误处理模板；写 `node_errors` / `degraded_nodes` 走单点 read-modify-write（遵守 must-fix-1）；沿用 BUG-S1-02/03 非静默吞错治理（WARNING 日志，不静默）。

> **建议转架构师**：错误分类到具体载体的映射（兼容性矩阵 §A.1 指出运行期错误细分如 syntax/import/runtime/oom/timeout 不应污染 `NodeError.error_type` 三态 transient/permanent/degraded，应落在执行反馈层，冒泡时再映射为三态之一）、metrics 从异构输出中解析的策略（正则 / 结构化输出约定 / LLM 抽取），属实现细节，交架构师细化。

**输入**：`GlobalState`，含 `code_output_dir` / `reproduction_plan`。

**输出**：状态更新字典，含 `execution_result` / `current_step`，失败时含分类信息驱动路由 + `node_errors` / `degraded_nodes`（单点合并）。

**边界条件**：

- 执行超时（疑似死循环）属不可自动修复类，不送回 coding 重试，直接走失败路径（§2.7）；
- 输出截断不视为失败本身，但需在 `logs` / `ExecutionResult` 标记 `output_truncated`，供 reporting 透明展示。

**依赖关系**：依赖 `sandbox/local_venv.py`（S3-01）/ `core/state.py` / `core/errors.py`。被 `core/graph.py` 注册调用，与 `coding` 节点构成修复回边。

---

### 2.4 S3-04: 单 agent 修复循环 (`core/graph.py` + S3-02/S3-03 节点)

**目的**：在主图建立 `coding → execution`（失败时 →`coding`）的**单 agent 修复回边**，首次激活 `fix_loop_count` / `fix_loop_history` 等修复循环字段，并以上限 10 回合 + 预算扣减回写收敛。

**详细要求**：

- **回边结构（兼容性矩阵 §C.1/§C.3）**：在现有 `coding → execution → reporting` 顺序边基础上，把 `execution → reporting` 改为 **execution 后条件路由**：
  - execution 成功（B 档 success=True）→ `reporting`；
  - execution 失败 **且** 错误属可自动修复类 **且** `fix_loop_count < MAX_FIX_LOOP_COUNT` **且** 预算门通过 → 回 `coding`（修复回合，`fix_loop_count += 1`）；
  - execution 失败 **且** 错误属不可自动修复类 → 直接走 dev_loop 失败处理（§2.7 第二个 interrupt 或降级）；
  - `fix_loop_count >= MAX_FIX_LOOP_COUNT`（修复耗尽）→ 走 dev_loop 失败处理（§2.7）；
  - 预算不足以启动一回合（入口预算门未通过）→ 降级（§5）；
- **计数粒度（Q-S3-03）**：**一个 coding → execution 完整修复回合 = `fix_loop_count += 1`**（不按单 agent 单次 LLM 调用计数）；
- **上限（Q-S3-03）**：当前默认 `MAX_FIX_LOOP_COUNT = 10`（2026-06-30 Maria 拍板由立项时的 3 放大，理由：3 轮真实环境收敛不了；立项消歧史见 §8 偏差消歧）；这是 Sprint 3 首次接线引用该常量（此前为零生产引用的死常量）；
- **`fix_loop_history` 记录**：每完成一个修复回合，append 一条 `FixLoopRecord`（`round_number` / `error_summary` / `error_category` / `fix_strategy` / `timestamp`），**走单点 read-modify-write 合并**（遵守 must-fix-1，**严禁加 reducer**）；
- **预算扣减回写（must-fix-2）**：修复循环按实际 LLM 调用次数主动单点回写 `retry_budget_remaining`，受 `MAX_DEV_LOOP_LLM_CALLS=60` 子预算 + 入口预算门约束（详见 §5）；
- **断点续跑粒度（MVP 不做）**：修复循环对主图是"整节点重跑"语义，不做回合级 `checkpoint_ns` 断点续跑（兼容性矩阵 §C.2，归 Sprint 4+）。

> **建议转架构师**：回边在主图的具体加边方式（execution 后的条件路由函数实现、`fix_loop_count` 自增的写入点、错误分类如何作为路由判据传递）、修复回合的"整节点重跑"语义与 checkpointer 的协同，属实现细节，交架构师细化。

**输入/输出**：见 S3-02 / S3-03；本项主要是 `core/graph.py` 的路由编排 + 两节点对修复循环字段的写入约定。

**依赖关系**：依赖 S3-02 / S3-03 / S3-08（config 常量）/ S3-09（state 字段）。

---

### 2.5 S3-05: reporting 节点真实现 (`core/nodes/reporting.py`)

**目的**：把 `reporting` pass-through 占位（`core/graph.py` L69-72）替换为真实报告节点：生成 Markdown 复现报告，写 `report_path`，支持 **full / code_only / 降级三形态**。

**详细要求**：

- **节点函数签名**：`def reporting(state: GlobalState) -> dict`；
- **报告内容（Markdown）**：
  - **指标对比展示**：并列论文 baseline（`paper_analysis.baseline_results` / `reproduction_plan.expected_results`）与本次复现值（`execution_result.metrics`），做对比表（**不做硬达标判定**，仅展示对比，对应 Q-S3-01 B 档）；
  - **降级原因**：若任一节点降级（`degraded_nodes` 非空）或复现未成功，醒目展示降级原因、`node_errors` 摘要、`fix_loop_history` 修复历程（修复了几轮、每轮什么错、什么策略）；
  - **artifact 清单**：列出 `execution_result.artifacts` 中的产物路径；
  - **复现计划与执行概况**：计划摘要、执行步骤、运行时长、环境信息；
- **三形态（Q-S3-01 / Q-S3-05 / §1.2 降级交付场景）**：
  1. **full 成功形态**：execution 成功（B 档）→ 完整报告（含指标对比 + artifact + 成功结论）；
  2. **code_only 形态**：`execution_mode == CODE_ONLY` → 报告聚焦"已生成代码"（代码位置 `code_output_dir` + 交付物清单 `deliverables`），无执行指标章节，明确标注"仅生成代码、未执行"；
  3. **降级形态**：复现未成功（修复耗尽 / 不可修复 / 预算耗尽 / 用户选 export_code）→ 标注 `degraded`，出"未成功复现"报告（降级原因 + 已尽力到哪一步 + 保留的代码与产物 + 修复历程）；
- **产出**：写 Markdown 报告到 `report_path`（位于 `workspace_dir` 下），并在状态中写 `report_path` / `current_step`。

**输入**：`GlobalState`，含 `paper_meta` / `paper_analysis` / `reproduction_plan` / `execution_result` / `code_output_dir` / `degraded_nodes` / `node_errors` / `fix_loop_history` / `execution_mode`。

**输出**：状态更新字典，含 `report_path` / `current_step`。

**边界条件**：

- `execution_result` 为 None（code_only 或 execution 未执行）时，reporting 必须仍能产出有效报告（走 code_only / 降级形态）；
- 报告中所有展示遵循 sp2 输出语言策略：面向用户的叙述用中文，事实层（数据集名 / 指标名 / 仓库 URL）保留英文。

**依赖关系**：依赖 `core/state.py`。被 `core/graph.py` 注册调用，被 `ui/pages/result_report.py`（S3-10）消费 `report_path`。

---

### 2.6 S3-06: code_only 路径 (`core/graph.py`)

**目的**：实现 `execution_mode == CODE_ONLY` 的执行路径——只生成代码、跳过 execution、不进修复循环，直接到 reporting。

**详细要求（Q-S3-05，B 路由 + 模式判断复用）**：

- **不单建 `coding_only` 节点**（Q-S3-05 拍板，与架构文档 §3.4 的 `coding_only` 节点方案分歧——以本 PRD 为准）；
- coding 节点照常生成代码（§2.2），路由层判断 `execution_mode`：
  - `execution_mode == FULL` → coding → execution（进入修复循环，§2.4）；
  - `execution_mode == CODE_ONLY` → coding → **跳过 execution、跳过修复循环** → 直接到 reporting；
- `execution_mode` 的来源：sp2 planning interrupt 中用户选"只编码不运行（code_only）"决策写入（sp2 §2.7），Sprint 3 首次真正消费此模式；
- reporting 在 code_only 形态下输出"仅生成代码"报告（§2.5 形态 2）。

> **建议转架构师**：code_only 分支在主图的具体落点（是在 coding 出边做条件路由，还是 planning→coding 边上携带模式判断；与 sp2 现有 `_route_after_planning` 的 3 路条件边如何衔接、不破坏既有边），属实现细节，交架构师细化。兼容性矩阵 §C.3 指出当前 `_route_after_planning` 的 approve 和 code_only 都走 `next→coding`，sp3 需在 coding 后或路由处区分 mode。

**输入/输出**：主要是 `core/graph.py` 路由编排 + `execution_mode` 字段判断。

**依赖关系**：依赖 S3-02 / S3-05 / `core/state.py`（`execution_mode`）。

---

### 2.7 S3-07: dev_loop 失败人在回路（第二个 interrupt + 三选一）(`core/graph.py` + UI)

**目的**：自动修复 10 次仍失败（或遇不可自动修复类错误、预算耗尽）时，触发**第二个 interrupt 人在回路**（沿用 sp2 interrupt/resume/cancel 范式 + 共享 thread_id），让用户三选一决策。

**详细要求（Q-S3-06，A 做 + UI 简化）**：

- **触发时机**：在修复循环退出点（建议 execution 后路由的失败分支，命名约定 `exit_dev_loop`/失败处理点）触发 `interrupt()`，触发条件：
  - `fix_loop_count >= MAX_FIX_LOOP_COUNT`（修复耗尽）；或
  - execution 失败且错误属**不可自动修复类**；或
  - 预算耗尽 / 入口预算门未通过（此场景可直接降级，也可走 interrupt 让用户决策，见下"降级与 interrupt 的关系"）；
- **interrupt 范式**：沿用 sp2 planning 的 interrupt/resume/cancel 范式（在节点函数体内调用 `interrupt()`，不用 `interrupt_before/after`），**复用同一 thread_id**（兼容性矩阵 §C.2），不破坏 sp2 已落地的"每线程独立 SqliteSaver + WAL"持久化模型；
- **resume 三选一（写入 `user_fix_decision`，复用 state.py 既有字段 + 既有约定取值）**：

  | 用户决策 | `user_fix_decision` | resume payload | 节点后续行为 |
  |---|---|---|---|
  | **终止任务** | `"terminate"` | `{"decision": "terminate"}` | 写 `current_step = "cancelled_by_user"`（或等价终止态），路由到 END；checkpoint 保留供后续查看（沿用 sp2 cancel 语义） |
  | **改计划** | `"revise_plan"` | `{"decision": "revise_plan", ...}` | 回 `planning` 节点重规划（带上修复失败上下文），用户可调整计划后重新走编码执行 |
  | **导出代码** | `"export_code"` | `{"decision": "export_code"}` | 保留已生成代码（`code_output_dir`），标 `degraded`，走 reporting 降级形态出报告并结束（="复现未成功但交付代码"） |

- **UI 范围（Maria 拍板：UI 可简化）**：dev_loop 失败决策的 UI 交互可简化（三个按钮 + 失败上下文摘要展示即可），但三选一决策必须可用、可正确路由；
- **降级与 interrupt 的关系**：不可自动修复类 / 修复耗尽 → 走 interrupt 让用户三选一（A 方案）；纯预算耗尽 / 入口预算门未通过的场景，PRD 允许直接降级出"未成功复现"报告（不强制 interrupt），具体是否统一走 interrupt 由架构师在落地时与降级路径协调（见 §5）。

> **建议转架构师**：第二个 interrupt 在修复循环边界的具体触发位置（execution 后路由失败分支 vs 独立退出节点）、resume 三态的路由实现（terminate→END / revise_plan→planning / export_code→reporting）、与 sp2 planning interrupt 复用同一 thread_id 的 checkpoint 协同，属实现细节，交架构师细化。兼容性矩阵 §A.2/§C.2 已确认 `user_fix_decision` 字段可直接复用、interrupt 范式与 sp2 一致不破坏 checkpointer 模型。

**输入**：`GlobalState`，含 `fix_loop_count` / `fix_loop_history` / `execution_result`（失败上下文）。

**输出**：触发 interrupt；resume 后写 `user_fix_decision` + 对应路由。

**依赖关系**：依赖 S3-04 / `core/state.py`（`user_fix_decision`）/ UI（S3-10 执行监控页承载失败决策交互）。

---

### 2.8 S3-08: 预算 / config 落地 (`config.py`)

**目的**：补齐预算/配置缺口（兼容性矩阵 §B / §D.4），首次接线引用修复循环常量。

**详细要求**：

- **`MAX_DEV_LOOP_LLM_CALLS = 60`**（Q-S3-04，默认值 2026-06-30 由 20 放大）：修复循环（dev_loop）在全局共享预算池里的子预算天花板；强约束 `MAX_DEV_LOOP_LLM_CALLS < MAX_TOTAL_LLM_CALLS`（60 < 120）；
- **`MAX_FIX_LOOP_COUNT = 10`**（Q-S3-04，默认值 2026-06-30 由 3 放大；立项消歧史见 §8）：Sprint 3 首次在修复循环路由中接线引用此常量（此前为零生产引用的死常量）；
- **预算扣减回写（must-fix-2）**：修复循环按实际 LLM 调用次数主动单点回写 `retry_budget_remaining`（详见 §5）；
- **sandbox 护栏常量**：执行超时上限、输出大小上限等护栏参数建议落 config 常量（具体常量名与默认值交架构师定，见 §5）。

**依赖关系**：被 S3-01 / S3-04 / S3-07 引用。

---

### 2.9 S3-09: state 字段微调 (`core/state.py`)

**目的**：视需要追加 `FixLoopRecord` 字段；**严禁给无 reducer 的 List 字段加 `operator.add` reducer**（must-fix-1）。

**详细要求**：

- **复用字段（零改动，兼容性矩阵 §D.2）**：`fix_loop_count: int` / `fix_loop_history: List[FixLoopRecord]` / `user_fix_decision: Optional[str]` / `execution_result: Optional[ExecutionResult]` / `code_output_dir: Optional[str]` / `report_path: Optional[str]` / `execution_mode: ExecutionMode` —— 这些字段已存在，Sprint 3 首次真正写入（修复循环字段此前为死字段，重定义语义无回归风险，兼容性矩阵 §A.2/§D.1）；
- **可选追加（向后兼容，单 agent 下非必须）**：`FixLoopRecord` 当前 5 字段（`round_number` / `error_summary` / `error_category` / `fix_strategy` / `timestamp`）对单 agent 修复循环已足够。兼容性矩阵 §A.2 建议的 `reviewer_verdict` / `coder_confidence` / `agent_trace` 三个 Optional 字段是 **multi-agent 专属**，Sprint 3 单 agent **可不追加**（顺延 Sprint 4+）；若 Sprint 3 实现中确有需要可向后兼容追加 Optional 字段（不破坏现有默认值）；
- **严禁加 reducer（must-fix-1）**：`node_errors` / `degraded_nodes` / `fix_loop_history` 保持普通 `List`，**严禁加 `Annotated[List, operator.add]`**；所有写入走单点 read-modify-write（§4.3）。

**依赖关系**：被 S3-02 / S3-03 / S3-04 / S3-05 / S3-07 引用。

---

### 2.10 S3-10: UI 执行监控页 + 结果报告页 (`ui/pages/execution_monitor.py` + `ui/pages/result_report.py`)

**目的**：实现 Streamlit 后 2 个页面，让用户观察编码/执行/修复循环进度，并查看最终复现报告。**两页都完整实现，不简化**（UI 范围决策：Maria 选择比 PM 推荐的"报告页必做 + 执行页简化"更重的方案——两页都完整做）。

**详细要求**：

**页面 4 —— 执行监控页 (`ui/pages/execution_monitor.py`)**：

- 承接 sp2 计划审核页"确认计划并开始编码"之后的流程，展示 coding → execution → 修复循环 → reporting 的实时进度；
- 展示修复循环状态：当前 `fix_loop_count` / `MAX_FIX_LOOP_COUNT`（"修复第 N / 10 轮"）、每轮 `FixLoopRecord` 摘要（错了什么、修复策略）；
- 展示 sandbox 执行实时信息：当前执行步骤、日志流（受输出截断护栏约束，标注 `output_truncated`）、运行时长；
- 展示错误与降级：滚动展示 `node_errors` / `degraded_nodes` 最近条目（沿用 sp2 错误信息展示规范，一句话摘要 + 可展开详情）；
- **dev_loop 失败人在回路交互（承载 S3-07，UI 可简化）**：当 graph 进入第二个 interrupt 时，展示失败上下文摘要 + 三个决策按钮（终止任务 / 改计划 / 导出代码），用户点击后通过 `resume_with` 注入 `user_fix_decision`；
- 流程结束（reporting 完成）后自动跳转到结果报告页（页面 5）；
- 轮询机制沿用 sp2 `app.py` 的 `poll_state` / `is_interrupted` / `resume_with`（1~2 秒一次）；execution 阶段可能长耗时，轮询不阻塞主线程。

**页面 5 —— 结果报告页 (`ui/pages/result_report.py`)**：

- 读取 `report_path`（S3-05 产出）并完整渲染 Markdown 复现报告；
- 展示三形态对应内容（full / code_only / 降级，§2.5）：
  - 顶部结论卡片（复现成功 / 仅生成代码 / 未成功复现 + 降级原因）；
  - 指标对比表（论文 baseline vs 本次复现，B 档对比展示，不做硬达标判定）；
  - artifact 清单（产物路径，可下载/定位）；
  - 修复历程（`fix_loop_history`）与降级原因（降级形态）；
  - 代码位置（`code_output_dir`）与交付物清单（`deliverables`）；
- 提供"返回输入页开启新任务"出口（沿用 sp2 终止后出口范式）。

> **建议转架构师**：执行监控页轮询 execution 长耗时阶段的 UI 刷新策略、第二个 interrupt 在 Streamlit 轮询架构下的检测与 resume 注入（复用 sp2 `is_interrupted` / `resume_with` 范式即可，但需确认与第一个 planning interrupt 在同一 thread_id 下的状态区分），属实现细节，交架构师细化。

**输入**：当前 `thread_id`（从 session_state 读取）。

**输出**：UI 渲染 + dev_loop 失败决策 payload 注入。

**边界条件**：

- 用户在执行监控页**不能**修改状态（除 dev_loop 失败的三选一决策外，仅观察）；
- 页面刷新（F5）后 session_state 丢失但 SqliteSaver 保留状态，沿用 sp2 限制（不提供"从已有 thread_id 恢复"入口，归 v1.x）。

**依赖关系**：依赖 `app.py`（轮询/resume 接口）/ `core/state.py` / `streamlit` / S3-05（`report_path`）/ S3-07（dev_loop 失败决策）。

---

## 3. 非功能需求

### 3.1 性能

| 指标 | 要求 | 说明 |
|------|------|------|
| coding 节点执行时间（单回合） | <= 3 分钟 | 单次 ReAct 编码生成/适配 |
| execution 节点执行时间（不含训练耗时） | 受 sandbox 超时护栏约束 | 实际执行时长取决于复现任务本身；超时上限由 config 配（§5），疑似死循环按超时类错误处理 |
| 修复循环总回合 | <= 10 回合（`MAX_FIX_LOOP_COUNT`） | 上限拦截后走第二个 interrupt（§2.7） |
| Streamlit 执行监控页渲染延迟 | <= 1 秒 | UI 主线程不被工作线程阻塞（沿用 sp2 §3.1） |
| 状态轮询间隔 | 1~2 秒 | 沿用 sp2 默认 1.5 秒 |
| 第二个 interrupt 恢复响应时间 | <= 5 秒 | 从用户点击决策按钮到工作线程实际开始下一步（沿用 sp2 §3.1） |

### 3.2 可靠性

| 指标 | 要求 |
|------|------|
| sandbox 护栏生效率 | 100% —— 执行超时 / 输出上限 / 工作目录限定 / 子进程隔离四项护栏必须全部生效，不允许越界写、不允许日志撑爆、不允许死循环卡死 |
| 修复循环上限拦截率 | 100% —— `fix_loop_count` 达 `MAX_FIX_LOOP_COUNT`（10）必须被拦截，不允许无限重试 |
| 预算扣减回写完整率 | 100% —— 修复循环每轮按实际 LLM 调用次数回写 `retry_budget_remaining`，不允许零扣减（must-fix-2） |
| list 字段无丢失率 | 100% —— `node_errors` / `degraded_nodes` / `fix_loop_history` 走单点 read-modify-write，不丢记录、不重复累加（must-fix-1） |
| 降级交付完成率 | 100% —— 不可修复 / 修复耗尽 / 预算耗尽时必须出"未成功复现"报告，不抛致命异常、不崩流程 |
| 第二个 interrupt 恢复完整性 | 100% —— terminate / revise_plan / export_code 三类 resume 必须能被正确路由 |

### 3.3 可维护性

| 指标 | 要求 |
|------|------|
| 类型标注覆盖率 | 100%（公开接口） |
| Docstring 覆盖率 | 100%（公开接口） |
| 节点 list 写入合规率 | 100% —— 所有写 `node_errors`/`degraded_nodes`/`fix_loop_history` 的节点必须走单点 read-modify-write（防 must-fix-1 回归，禁止加 reducer） |
| 非静默吞错 | 100% —— 沿用 BUG-S1-02/03 治理，所有错误均有 WARNING 日志，禁止静默吞错 |

### 3.4 可测试性

| 指标 | 要求 |
|------|------|
| 单元测试 | coding / execution / reporting 节点函数与 sandbox 模块可独立 mock 测试 |
| 端到端测试 | 真实/mock 链路覆盖 happy path（B 档成功）+ 修复循环 + 第二个 interrupt 三选一 + code_only + 降级五条核心场景；可通过 `Command(resume=...)` 模拟用户决策 |
| Streamlit UI 测试 | 至少能通过 `streamlit run app.py` 启动并完成手动 happy path（含执行监控页 + 结果报告页两页） |

---

## 4. 数据结构定义

### 4.1 复用字段一览（Sprint 3 首次写入，零/微改动）

| 类型 / 字段 | 类型 | Sprint 3 处置 | 说明 |
|---|---|---|---|
| `GlobalState.code_output_dir` | `Optional[str]` | **复用，首次写入** | coding 节点写入代码目录绝对路径 |
| `GlobalState.execution_result` | `Optional[ExecutionResult]` | **复用，首次写入** | execution 节点写入；`success` 按 B 档判据 |
| `GlobalState.report_path` | `Optional[str]` | **复用，首次写入** | reporting 节点写入报告路径 |
| `GlobalState.execution_mode` | `ExecutionMode` | **复用，首次消费** | sp2 写入，sp3 在 S3-06 路由首次消费 |
| `GlobalState.fix_loop_count` | `int` | **复用，首次激活**（死字段→激活） | 修复回合计数，粒度 = 一个 coding→execution 回合 +1 |
| `GlobalState.fix_loop_history` | `List[FixLoopRecord]` | **复用，首次激活**（无 reducer，单点合并） | 每回合一条记录；must-fix-1 |
| `GlobalState.user_fix_decision` | `Optional[str]` | **复用，首次激活** | 第二个 interrupt resume 载体，取值 terminate/revise_plan/export_code |
| `GlobalState.retry_budget_remaining` | `int` | **复用，新增扣减回写** | must-fix-2：修复循环按实际 LLM 调用次数主动单点回写 |
| `ExecutionResult`（7 字段 TypedDict） | — | **复用，首次填充** | success/metrics/logs/errors/artifacts/runtime_seconds/environment_info |
| `FixLoopRecord`（5 字段 TypedDict） | — | **复用，首次填充** | round_number/error_summary/error_category/fix_strategy/timestamp |
| `RepoInfo.local_path` | `Optional[str]` | **复用，首次消费** | sp2 落地，sp3 coding 直接读本地仓库路径 |

### 4.2 可选追加字段（向后兼容，单 agent 下非必须）

| 类型 / 字段 | 类型 | 处置 | 说明 |
|---|---|---|---|
| `FixLoopRecord.reviewer_verdict` | `Optional[str]` | **Sprint 3 可不加（multi-agent 专属，顺延 sp4+）** | 兼容性矩阵 §A.2 建议字段；单 agent 无 Reviewer，不需要 |
| `FixLoopRecord.coder_confidence` | `Optional[float]` | **Sprint 3 可不加** | 同上 |
| `FixLoopRecord.agent_trace` | `Optional[str]` | **Sprint 3 可不加** | 同上 |

> Sprint 3 若实现中确需追加，按"向后兼容追加 Optional 字段、不破坏现有默认值"原则处理（当前无人写它，零回归）。

### 4.3 list 字段合并策略（must-fix-1，钉死）

`node_errors` / `degraded_nodes` / `fix_loop_history` 在 `core/state.py` 是**无 `operator.add` reducer 的普通 `List`**（[代码实证] state.py L189/L190/L193，附录抽查复核证实全 state.py 无 `Annotated`/`operator.add`）：

- **所有写入必须走单点 read-modify-write 合并**：`list(state.get("<field>", []))` → append → `return` 整列表（复用 sp1/sp2 既有范式，参 `paper_analysis.py` L436 起）；
- 修复循环涉及 coding / execution 多节点对这三个字段的写入，必须各自走 read-modify-write，**确保只在单点写回、不并发覆盖**；
- **严禁**给这三个字段加 `Annotated[List, operator.add]` reducer —— 会让 sp1/sp2 所有"return 整列表"的节点变成重复累加（breaking change，§1.2 明确不做、§7 风险 R-S3-03）。

### 4.4 子图私有结构（Sprint 3 不做，顺延 sp4+）

`DevLoopState` / `CodingOutput` / `ExecutionFeedback` + 共享 scratchpad（`Annotated[List, operator.add]`）是 **multi-agent 专属**（兼容性矩阵 §A.4/§D.6），**Sprint 3 不实现**。Sprint 3 单 agent 修复循环的过程数据走现有 ReAct wrapper 的私有 messages + GlobalState 既有字段即可。

---

## 5. 预算与配置

### 5.1 预算模型（Q-S3-04，共享池 + 子预算 60 + 入口预算门）

Sprint 3 预算模型由 Maria 拍板四要素（兼容性矩阵 §B.3 推荐方案）：

1. **共享池**：修复循环（dev_loop）从全局 `MAX_TOTAL_LLM_CALLS = 120` 共享池扣减（与"单任务总预算"语义一致，**不设独立配额**）；
2. **子预算天花板**：`MAX_DEV_LOOP_LLM_CALLS = 60` 作为修复循环在共享池里的天花板；修复循环每回合检查双重约束 `min(子图内累计 LLM 调用 < 60, retry_budget_remaining > 0)`，任一触顶即终止修复循环并走第二个 interrupt / 降级；
3. **按实际 LLM 调用次数单点主动回写（must-fix-2）**：修复循环不走 `_make_react_wrapper` 的预算扣减逻辑时，必须**主动按实际 LLM 调用次数读-改-写 `retry_budget_remaining`**（单点回写——`int` 字段 last-write-wins 是正确语义，但必须确保只有一个回写点，兼容性矩阵 §B.3）。**量纲注意**：修复循环按"实际 LLM 调用次数"扣减，不套用 ReAct 的 "round 口径"（兼容性矩阵 §B.2）；
4. **入口预算门**：进入修复循环前检查 `retry_budget_remaining`，若不足以启动一回合（如低于单回合最小调用数）→ **直接降级**（标 `degraded` + 走 dev_loop 失败处理），不进修复循环空转。

> **降级 vs interrupt（§2.7 联动）**：入口预算门未通过 / 预算耗尽时，PRD 允许直接降级出"未成功复现"报告；修复耗尽 / 不可修复类错误走第二个 interrupt 三选一。二者的统一与边界由架构师在落地时协调。

### 5.2 config 新增 / 统一项（S3-08）

| 常量 | Sprint 3 处置 | 值 | 说明 |
|---|---|---|---|
| `MAX_DEV_LOOP_LLM_CALLS` | **新增（默认 2026-06-30 由 20 放大）** | `60` | 修复循环子预算天花板；强约束 `< MAX_TOTAL_LLM_CALLS`（60 < 120） |
| `MAX_FIX_LOOP_COUNT` | **首次接线 + 默认 2026-06-30 由 3 放大** | `10` | sp3 首次在修复循环路由引用（此前零生产引用）；立项消歧史（采纳 3、消歧架构文档"5"）见 §8.1 |
| `MAX_TOTAL_LLM_CALLS` | **默认 2026-06-30 由 50 放大** | `120` | 全局共享预算池 |
| sandbox 执行超时上限 | **新增（常量名/默认值交架构师定）** | TBD | S3-01 护栏：超时即终止，标超时类错误 |
| sandbox 输出大小上限 | **新增（常量名/默认值交架构师定）** | TBD | S3-01 护栏：超限截断 + 标 `output_truncated` |

> **建议转架构师**：sandbox 护栏的常量名、默认值（超时秒数、输出字节阈值）、单回合最小调用数（入口预算门阈值）的具体取值，属技术实现细节，交架构师在 sp3 架构文档定。

---

## 6. 验收标准

> Sprint 3 共 10 条验收标准（AC-S3-01 ~ AC-S3-10）。除显式标注 manual-only 的项外均要求自动化测试覆盖。

**AC-S3-01：端到端 happy path 按 B 档成功**（对应 S3-01~S3-05、S3-10）

> 输入有效 arXiv ID（FULL 模式），系统依次执行 paper_intake → paper_analysis → resource_scout → planning（审核确认）→ coding → execution → reporting，全链路跑通。execution 子进程 `exit_code == 0` 且 `ExecutionResult.metrics` 至少解析出一个关键指标数值，`ExecutionResult.success == True`（B 档判据，§1.5），`report_path` 非空、结果报告页完整渲染含指标对比表。验证可用真实链路或 mock sandbox（返回 exit 0 + 可解析指标）覆盖。

**AC-S3-02：sandbox 护栏生效**（对应 S3-01）

> ① 执行超时：mock 一个超时（疑似死循环）任务，sandbox 在超时上限内强制终止子进程，返回 `timed_out == True`，且该错误被归为不可自动修复类（不送回 coding，§2.7）；② 输出大小上限：mock 超大输出，sandbox 截断并置 `output_truncated == True`，不撑爆内存/checkpoint；③ 工作目录限定：mock 代码尝试写 `workspace_dir` 之外路径，被拒绝/限定在 `workspace_dir` 内；④ 子进程隔离：子进程崩溃不污染主工作线程。四项护栏均自动化断言。

**AC-S3-03：修复循环计数与上限拦截**（对应 S3-04、S3-08）

> mock execution 连续失败（可自动修复类错误）：① 每个 coding→execution 完整回合 `fix_loop_count` 自增 1（粒度 Q-S3-03）；② `fix_loop_history` 每回合 append 一条 `FixLoopRecord`；③ 当 `fix_loop_count` 达 `MAX_FIX_LOOP_COUNT`（默认 10）时被拦截，不再回 coding，转入第二个 interrupt（AC-S3-07）。自动化断言计数粒度与上限拦截（**断言引用常量而非硬编码上限值**）。

**AC-S3-04：预算扣减回写 + 子预算 60 + 入口预算门**（对应 S3-08、must-fix-2）

> ① 修复循环每回合按实际 LLM 调用次数主动单点回写 `retry_budget_remaining`（不再零扣减）；② `MAX_DEV_LOOP_LLM_CALLS == 60` 存在于 config 且 `< MAX_TOTAL_LLM_CALLS`，修复循环累计 LLM 调用达 60 时终止；③ 入口预算门：mock `retry_budget_remaining` 不足以启动一回合时，直接降级（标 `degraded` + 走 dev_loop 失败处理），不进修复循环空转。三项自动化断言。

**AC-S3-05：list 字段单点合并、无 reducer、无丢失**（对应 S3-09、must-fix-1）

> ① grep 断言 `core/state.py` 中 `node_errors` / `degraded_nodes` / `fix_loop_history` 三字段**仍为普通 `List`、无 `Annotated`/`operator.add`**（防 reducer 回归，强制验收点）；② mock 多回合修复，断言三字段经单点 read-modify-write 后**记录完整无丢失、无重复累加**；③ 修复循环节点（coding/execution）对这三字段的写入均走 read-modify-write 范式。

**AC-S3-06：code_only 跳过 execution 与修复循环**（对应 S3-06、S3-02、S3-05）

> 用户在 planning 审核选 code_only（`execution_mode == CODE_ONLY`）：① coding 正常产出代码到 `code_output_dir`；② 流程**跳过 execution、跳过修复循环**，直接到 reporting；③ `execution_result` 为 None；④ reporting 输出 code_only 形态报告（含代码位置 + deliverables，无执行指标章节，标注"仅生成代码、未执行"）。**不存在独立 `coding_only` 节点**（Q-S3-05，断言主图节点集合不新增该节点）。自动化断言路由跳过 + 报告形态。

**AC-S3-07：dev_loop 失败 interrupt 三选一**（对应 S3-07）

> 修复耗尽（`fix_loop_count == MAX_FIX_LOOP_COUNT`，默认 10）或遇不可自动修复类错误时，graph 触发第二个 interrupt（沿用 sp2 范式 + 同一 thread_id）。通过 `Command(resume=...)` 注入三类决策并断言路由：① `{"decision": "terminate"}` → `user_fix_decision == "terminate"`，路由到 END，checkpoint 保留；② `{"decision": "revise_plan"}` → 回 planning 重规划；③ `{"decision": "export_code"}` → 保留 `code_output_dir`、标 `degraded`、走 reporting 降级形态出报告并结束。自动化用 mock interrupt + 三种 resume 路径覆盖（不依赖 UI 渲染）。

**AC-S3-08：不可自动修复类错误不进重试**（对应 S3-03、S3-04、Q-S3-07）

> mock execution 失败且错误属**不可自动修复类**（数据集缺失需人工下载 / 显存等硬件约束 / 需论文未公开资源 / 超时疑似死循环之一）：① 错误被正确分类为不可自动修复；② **不送回 coding 重试**、`fix_loop_count` **不自增**；③ 直接走第二个 interrupt（或降级）。同时断言：可自动修复类（缺依赖 / import 错 / 语法错 / 简单路径错 / 简单运行时异常）**送回 coding 重试且计入 `fix_loop_count`**。自动化覆盖两类错误的分流。

**AC-S3-09：reporting 三形态**（对应 S3-05、S3-10）

> reporting 节点对三种输入产出正确形态报告：① **full 成功形态**（execution B 档成功）→ 含指标对比表 + artifact 清单 + 成功结论；② **code_only 形态**（`execution_mode == CODE_ONLY`）→ 含代码位置 + deliverables，标注"仅生成代码"，无指标章节；③ **降级形态**（修复耗尽/不可修复/预算耗尽/export_code）→ 标 `degraded`，含降级原因 + `node_errors` 摘要 + `fix_loop_history` 修复历程 + 保留的代码与产物。三形态 `report_path` 均非空、结果报告页均能渲染。自动化断言三形态报告内容关键段落。

**AC-S3-10：主图 7 节点骨架不变性**（对应 S3-04、S3-06、S3-07，manual + 自动混合）

> sp3 对主图的改动仅为**加边 / 改条件路由 / 节点位换真实现**，不破坏 7 节点骨架基线：① 主图仍为 `paper_intake / paper_analysis / resource_scout / planning / coding / execution / reporting` 7 节点（**不新增 `coding_only` / dev_loop 子图节点**）；② coding↔execution 修复回边为新增条件边，不删除既有顺序边；③ code_only 与 dev_loop 失败路由为新增条件边；④ `build_graph` 编译成功、节点数仍为 7。自动化断言节点集合与编译成功；端到端路由 manual 复核。

---

## 7. 依赖与风险

### 7.1 外部依赖

| 依赖项 | 类型 | 风险等级 | 说明 |
|---|---|---|---|
| `python` / `venv` / `pip` | 系统/Python 工具 | 低 | sandbox 依赖；需用户机器可创建 venv 并联网装依赖 |
| 论文复现所需第三方依赖 | PyPI 包 | **中** | 复现代码的依赖可能装不上 / 版本冲突 / 需特定 CUDA；属可/不可自动修复类分流处理（§2.7） |
| GPU / 硬件资源 | 硬件 | **中** | 部分论文需 GPU/显存；硬件约束属不可自动修复类，走第二个 interrupt（§2.7） |
| LangGraph `interrupt()` | LangGraph API | 中 | 第二个 interrupt 复用 sp2 已验证范式 + 同一 thread_id，风险较 sp2 低 |

### 7.2 内部依赖

| 依赖项 | 说明 |
|---|---|
| Sprint 1/2 已交付模块 | coding 复用 `react_base.py` + `llm_client.py` + `resource_info.selected_repo.local_path`（sp2 落地）；reporting 复用 sp2 输出语言策略；UI 复用 sp2 `app.py` 轮询/resume 框架 |
| 架构师代理 | sandbox 护栏落点 / 错误分类映射 / 第二个 interrupt 触发位置 / code_only 路由落点 / 预算扣减回写点 / config 护栏常量取值（本 PRD 多处标"建议转架构师"）需架构师在 sp3 架构文档细化 |
| 测试工程师代理 | 五条核心场景 e2e（happy path / 修复循环 / 第二个 interrupt / code_only / 降级）+ sandbox 护栏 mock 用例 + must-fix-1/2 回归断言需测试工程师协作，报告归档 `docs/sprint3/test-reports/` |

### 7.3 风险矩阵

| 编号 | 风险 | 可能性 | 影响 | 缓解策略 |
|---|---|---|---|---|
| R-S3-01 | **sandbox 执行护栏失效**：超时未生效导致死循环卡死工作线程、输出未截断撑爆内存/checkpoint、子进程越界写文件 | 中 | 高 —— 全 UI 链路瘫痪 / 数据损坏 | §2.1 四项护栏硬要求 + AC-S3-02 强制验收；架构师细化跨平台子进程 kill 与截断阈值 |
| R-S3-02 | **修复循环不收敛 / 预算失控**：错误分类不准导致不可修复类被反复送回 coding，或预算零扣减导致成本失控 | 中 | 高 —— token 成本失控 / 流程卡死 | `MAX_FIX_LOOP_COUNT=10` 上限拦截（AC-S3-03）+ `MAX_DEV_LOOP_LLM_CALLS=60` 子预算 + 入口预算门 + 实际调用次数回写（AC-S3-04，must-fix-2）+ 错误分类分流（AC-S3-08） |
| R-S3-03 | **误给 list 字段加 reducer（违反 must-fix-1）**：图省事加 `operator.add` 导致 sp1/sp2 所有"return 整列表"节点重复累加 | 中 | 高 —— breaking change，全链路状态污染 | §4.3 钉死 + AC-S3-05 grep 断言强制验收（断言三字段无 `Annotated`/`operator.add`）；所有写入走单点 read-modify-write |
| R-S3-04 | **错误分类准确率不足**：可/不可自动修复类边界模糊，分类错误导致无效重试或过早放弃 | 中 | 中 —— 复现成功率下降 / 浪费回合 | §2.7 分类原则明确化 + AC-S3-08 两类错误分流验收；分类映射交架构师细化（落执行反馈层、不污染 `error_type` 三态） |
| R-S3-05 | **B 档指标解析失败**：execution 跑通（exit 0）但无法从异构输出解析出指标，导致 `success` 误判为 False | 中 | 中 —— happy path 误判失败 | metrics 解析策略交架构师细化（结构化输出约定 / LLM 抽取兜底）；reporting 降级形态仍能交付 |
| R-S3-06 | **第二个 interrupt 与第一个 planning interrupt 在同一 thread_id 下状态混淆**：UI 无法区分当前处于哪个 interrupt | 低 | 中 —— 人在回路决策错乱 | 复用 sp2 已验证 interrupt 范式 + `current_step` / `user_fix_decision` 区分；AC-S3-07 覆盖三类 resume 路由 |
| R-S3-07 | **依赖安装 / 环境搭建失败率高**：复现代码依赖装不上、CUDA 版本不匹配 | 中 | 中 —— happy path 完成率下降 | 缺依赖/import 错属可自动修复类送回 coding（§2.7）；装不上的硬约束走降级交付（§2.5 降级形态），不崩流程 |

---

## 8. 开放问题与决策记录

> Sprint 3 启动前 Maria 已对全部关键决策点拍板，下表作为**已决策记录归档**（无遗留待定项）。

| 编号 | 问题 | 影响范围 | 决策 |
|---|---|---|---|
| **Q-S3-01** | 复现"成功"判据如何定？ | `core/nodes/execution.py` + reporting | **[RESOLVED Maria 拍板] = B 档**：代码无报错跑完（`exit_code == 0`）+ 能解析出关键指标数值写入 `ExecutionResult.metrics`，**不强制**与论文 baseline 对齐；指标是否达标由 reporting 做对比展示，**非硬验收**。详见 §1.5 / §2.3 / AC-S3-01。 |
| **Q-S3-02** | sandbox 用什么形态？ | `sandbox/local_venv.py` | **[RESOLVED Maria 拍板] = 本地 venv + 基础资源护栏**：`python -m venv` + pip 装依赖 + 子进程执行 + 执行超时 + 输出大小上限 + 工作目录限定。**不上 Docker/容器**、不做跨机远程执行。详见 §2.1。 |
| **Q-S3-03** | 修复循环上限取值 + 计数粒度？ | `config.py` + `core/graph.py` | **[RESOLVED Maria 拍板] 上限 = 3**：统一 `MAX_FIX_LOOP_COUNT=3`（采纳 config 现值，**消歧架构文档写的 5**）。**计数粒度**：一个 coding→execution 完整修复回合 = `fix_loop_count += 1`。详见 §2.4 / §5.2 / AC-S3-03。 |
| **Q-S3-04** | 预算模型？ | `config.py` + 修复循环 | **[RESOLVED Maria 拍板] = 共享池 + 子预算 20 + 入口预算门**：dev_loop 从全局 50 共享池扣（与"单任务总 50"一致）；新增 `MAX_DEV_LOOP_LLM_CALLS=20` 子预算天花板；按实际 LLM 调用次数单点主动回写 `retry_budget_remaining`（must-fix-2）；剩余预算不足以启动一回合则入口预算门直接降级。详见 §5 / AC-S3-04。 |
| **Q-S3-05** | code_only 是否单建 `coding_only` 节点？ | `core/graph.py` + `core/nodes/coding.py` | **[RESOLVED Maria 拍板] = B 路由 + 模式判断复用**：**不单建 `coding_only` 节点**（与架构文档 §3.4 分歧，以本 PRD 为准）。coding 照常生成代码，遇 `execution_mode == code_only` 即跳过 execution、不进修复循环，直接到 reporting，靠路由 + 模式判断实现。详见 §2.6 / AC-S3-06。 |
| **Q-S3-06** | dev_loop 失败人在回路怎么做？ | `core/graph.py` + UI | **[RESOLVED Maria 拍板] = A 做 + UI 简化**：自动修复 3 次仍失败 → 在修复循环退出点触发**第二个 interrupt**（沿用 sp2 interrupt/resume/cancel 范式 + 共享 thread_id），用户三选一写 `user_fix_decision`：terminate / revise_plan / export_code（export_code = 保留已生成代码并结束）。UI 可简化。详见 §2.7 / AC-S3-07。 |
| **UI 范围** | 执行监控页 + 结果报告页是否都完整做？ | `ui/pages/*` | **[RESOLVED Maria 拍板] = 两页都完整做**（Maria 选择比 PM 推荐的"报告页必做 + 执行页简化"更重的方案）：执行监控页 + 结果报告页**都完整实现，不简化**。详见 §2.10 / AC-S3-09 / AC-S3-10。 |
| **Q-S3-07** | 错误可修复性边界如何划？ | `core/nodes/execution.py` 错误分类 | **[RESOLVED Maria 拍板] = 采纳分类原则**：**可自动修复类**（送回 coding 重试、计入 `fix_loop_count`）= 缺依赖 / import 错 / 语法错 / 简单路径错 / 简单运行时异常；**不可自动修复类**（不进重试、直接走失败 interrupt 或降级）= 数据集缺失需人工下载 / 显存等硬件约束 / 需论文未公开资源 / 超时（疑似死循环）。详见 §2.7 / AC-S3-08。 |

### 8.1 文档↔代码偏差消歧记录（兼容性矩阵 §D.4）

| 项 | 架构文档 | 代码现状 | Sprint 3 裁定（本 PRD） |
|---|---|---|---|
| 修复轮次上限 | §12.6/§3.2.2 写 **5** | `config.MAX_FIX_LOOP_COUNT = 3` | **统一为 3**（Q-S3-03），sp3 首次接线引用 |
| 子图 LLM 预算常量 | §3.2.2/§12.6 写 `MAX_DEV_LOOP_LLM_CALLS=20` | config.py 中**不存在** | **新增 `MAX_DEV_LOOP_LLM_CALLS=20`**（Q-S3-04 / S3-08） |
| code_only 实现 | §3.4 主张独立 `coding_only` 节点 | 仅占位 coding | **不单建 `coding_only` 节点**，靠路由 + 模式判断（Q-S3-05），以本 PRD 为准 |
| dev_loop 形态 | §3.2.2 主张 multi-agent 子图（双/三 agent） | 纯文档意图、未落地 | **Sprint 3 单 agent 修复回边**，multi-agent 顺延 sp4+（§1.1 重心调整） |

### 8.2 顺延 Sprint 4+ 事项（明确不做，归档）

- dev_loop 真 multi-agent（supervisor + Coder/Executor/Reviewer + 共享 scratchpad + `DevLoopState`）；
- `FixLoopRecord` 的 `reviewer_verdict` / `coder_confidence` / `agent_trace` 三个 multi-agent 专属 Optional 字段；
- dev_loop 回合级断点续跑（`checkpoint_ns` 命名空间隔离）；
- 指标与论文 baseline 的强容差校验；
- Docker/容器化 sandbox、跨机远程执行、GPU 多卡调度/分布式训练。

---

**文档结束**

*本文档为 Sprint 3 产品需求文档正式版。数据结构与异常体系的权威定义以 `core/state.py` 与 `docs/technical-architecture.md` 第 4 章 / 第 12 章为准；修复循环 / 预算 / 占位节点 / code_only 路由的兼容性结论与 must-fix 依据以 `docs/sprint3/dev-loop-compatibility-matrix.md`（§A.2 修复循环死字段、§B 预算总账、§C.3 code_only 路由、§D.3 两个 must-fix、§D.4 偏差消歧、§D.6 设计约束）为准；标注"建议转架构师"处的技术实现细节由架构师代理在 sp3 架构文档落地。Q-S3-01~Q-S3-07 + UI 范围决策均由 Maria 于 Sprint 3 启动前拍板，无遗留待定项。*
