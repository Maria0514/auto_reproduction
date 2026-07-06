# Sprint 4 开发计划

**产品名称**：Auto-Reproduction —— 论文自动复现系统
**Sprint**：Sprint 4 —— 路线丙：coding / execution 双 agent 松耦合升级 + 通用用户交互能力
**版本**：v1.0
**日期**：2026-07-02
**作者**：全栈开发工程师代理
**状态**：正式版
**对应 PRD**：`docs/sprint4/prd.md` v2（路线丙，S4-01~10 + AC-S4-01~14 + §11 开放问题）
**对应架构**：`docs/sprint4/architecture.md` v1.0（2026-07-02；§13 改动点清单 + §14 治理硬约束 + §16 测试策略 + §17 任务拆分建议）
**体例参照**：`docs/sprint3/dev-plan.md`

> **代码现状校准注记（2026-07-02，两热修复后）**：本计划所有常量 / 签名 / 行号均以当日代码实证为准——
> `MAX_TOTAL_LLM_CALLS=120`（config.py L31）/ `MAX_FIX_LOOP_COUNT=10`（L32）/ `MAX_DEV_LOOP_LLM_CALLS=60`（L116）/ `DEV_LOOP_MIN_CALLS_PER_ROUND=2`（L117）/ `REACT_MAX_ROUNDS_CODING=12`（L118）。
> **HOTFIX-1**（react_base 三处 `invoke_with_retry` 接入）与 **HOTFIX-2**（sandbox `_build_sandbox_env` 白名单）已落地，本计划涉及 `core/react_base.py` / `sandbox/local_venv.py` 的任务全部基于修复后现状；架构文档引用的部分行号是热修复前快照，漂移清单见 §7.1，**以本计划行号为准**。
> 全量非 e2e 回归基线：**1119 passed / 0 failed / 37 skipped**（`.venv/bin/pytest -q -m "not e2e" --ignore=tests/test_paper_intake.py`，2026-07-02 主控收口实测）。每阶段回归守门以此为零退化基准。

---

## 1. 概述

### 1.1 Sprint 目标

Sprint 4 在 sp3 已打通的端到端流程（含 coding↔execution 修复循环 + interrupt#2）之上，把 coding / execution 升级为**两个松耦合真 agent**，并补齐**通用用户交互能力**：

- **交互层（新增）**：单一 LangChain `@tool` `request_user_input`（question / is_sensitive / purpose_key 三字段，**无 input_type 枚举**，Maria 硬约束）→ 工具内 `interrupt()` 触发第三类 interrupt（`interrupt_kind="user_input_request"`）→ UI 单输入框收集 → `Command(resume={"value", "remember"})` 恢复；
- **凭证层（新增）**：`core/secrets_store.py`（`.secrets` 0600 明文 + gitignore + `mask_value` 全程脱敏）+ `prepare_venv` 补 `extra_env` 形参 + `CREDENTIAL_REQUIRED` 分类（不进 AUTO_FIXABLE），止损 sp3 真跑暴露的"缺凭证打转到 `MAX_FIX_LOOP_COUNT=10` 耗尽"硬伤；
- **coding agent（改动局部）**：wrapper 形态零改动，`_get_coding_tools`（coding.py L255，现 5 工具）增挂 `run_command`（系统解释器轻量 smoke，120s 短超时）+ `request_user_input`，闭合"写→跑→看→改"；
- **execution agent（最大工作量）**：手写七步节点 → **手写编排 + 内嵌 ReAct 子图**（架构 §3 Q-A1 结论）——编排层逐字保留 sp3 的 guard / 分类 / B 档判定 / `_maybe_interrupt_or_return` / interrupt#2 / `await_dev_loop_interrupt` self-loop，仅把"步骤 1+2（prepare_venv + `_run_step_subcommands` 确定性循环）"替换为 `_run_execution_agent`（裸 `create_react_subgraph`，挂 `prepare_environment` / `run_in_sandbox` / `request_user_input` 三工具）；预算按架构 §4 落点 B 由编排层单点扣减；
- **主图骨架**：**`core/graph.py` 零改动**（7 节点集合 / 节点名 / 边结构 / self-loop 全部不变，AC-S3-10 沿用为 AC-S4-03）。

### 1.2 范围对齐

- **PRD 权威**：`docs/sprint4/prd.md` v2（MVP 10 项 S4-01~10 + 非目标清单 + AC-S4-01~14；Q-F1 一直暂停无硬超时 / Q-F2 无前端按信息缺失降级 / Q-F3 逐条问，均 Maria 已定）。
- **架构权威**：`docs/sprint4/architecture.md` v1.0——Q-A1（薄手写编排 + 内嵌 ReAct 子图，§3）、Q-A2（预算对账落点 B，§4）、Q-B1（run_command 系统解释器 + 120s，§5）、Q-E1（`workspace_dir/.secrets`，§6）四个开放问题已全部落地为可执行设计，本计划不再重新决策。
- **本计划相对架构 §17 拆分建议的调整**（理由随注）：
  1. §17 的 P0~P6 六层保留为骨架，但把 **Q-C1 重跑幂等验证**从"P1 附带"提升为**独立任务 B2 + 软前置门**（PRD §9 Q-C 明示"开发中头号重点验证项"，且其结论直接决定 execution 改造方案是否成立，必须在进入 E 阶段前拿到首个实证结论——与 sp3 把 S-1 spike 设为 C3 前置门同一工程逻辑，只是按 Maria 决策不做独立 spike、用开发中 harness 验证替代）；
  2. 新增任务 **D2**：闭环 TODO「HOTFIX-2 备忘」——`PIP_*` 前缀白名单与 `.secrets`/extra_env 显式注入的复核（TODO.md L320，架构文档未覆盖此备忘，属本 Sprint 凭证注入落地时必须收口的挂账项）；
  3. §17 P5 的 UI 任务提前为 F1 并与 E 阶段并行（文件边界不重叠，见 §3.3），缩短关键路径。

### 1.3 关键决策沿用（不可推翻）

1. **交互工具单一极简**（PRD §5.1，Maria 硬约束）：一个 `request_user_input`，三字段，无 input_type / options / choice / confirm 类型区分；UI 一个输入框（敏感 password + 「记住」勾选）。**任何任务不得擅自加抽象层或预设扩展点。**
2. **execution 编排安置 = 手写编排 + 内嵌 ReAct 子图**（架构 §3）：interrupt#2 与 self-loop 留在编排层，**不下沉子图**；确定性收尾（分类 / metrics / B 档判定）**不交给 agent 自由裁量**。
3. **预算对账落点 B**（架构 §4.5）：`REACT_MAX_ROUNDS_EXECUTION` 管子图内轮次；`retry_budget_remaining` 由 execution 编排层在 `_map_execution_result` 单点扣减（子图 rounds + metrics 抽取，唯一扣减点）；`_dev_loop_llm_calls` 语义收窄为 execution 侧累计。coding 仍由 wrapper（react_base.py L894-898）扣减，互不重叠。
4. **敏感值全程旁路 state**（架构 §6.5）：只在 `.secrets`（0600）+ 子进程 `extra_env` + 子图私有 messages 中流转，**完全不进 GlobalState / checkpoint**；`pending_user_input` 只存问题快照、`collected_inputs` 只存非敏感项。
5. **must-fix-1 红线沿用**：新增两个 state 通道均单值 / 单点写、**绝不加 reducer**；`node_errors` / `degraded_nodes` / `fix_loop_history` 三 List 字段零改动。

### 1.4 关键风险一句话

**interrupt#3（工具内 interrupt）在 ReAct 子图内的 resume 重跑幂等是 sp4 单点最大技术风险**（架构 §8 / R-S4-02，机理与 sp3 节点级 interrupt#2 的 commit 边界**不同**）——Maria 已定不单独 spike、边开发边验（AC-S4-14 强制断言）。本计划以 B2（harness 首验）→ C2（coding 真实 wrapper 复验）→ E4（execution 内嵌子图复验）三级递进验证；**B2 实测若发现前序副作用工具在 resume 后重放且三层幂等缓解（架构 §8.3）无效，立即触发架构师咨询、E 阶段方案重评估，不得带病推进**。次要风险：内嵌子图真实结果取回（R-S4-01）、脱敏泄漏（R-S4-05）、预算错扣漏扣（R-S4-04）。

---

## 2. 任务清单总表

| 任务编号 | PRD 编号 | 任务名 | 产出文件 | 依赖前置 | 估时 | 风险 |
|---|---|---|---|---|---|---|
| **A1** | S4-10 | `config.py` 新增 3 常量 | `config.py` | -- | 0.5h | 低 |
| **A2** | S4-10 | `core/state.py` 新增 2 个交互通道 | `core/state.py` | -- | 1h | 低 |
| **A3** | S4-07 | `core/secrets_store.py`（.secrets 读写 + 脱敏 + 凭证 env 组装） | `core/secrets_store.py` | A1 | 4h | 中（安全关键） |
| **B1** | S4-05 | `core/tools/interaction_tools.py`（request_user_input + interrupt#3） | `core/tools/interaction_tools.py` | A3 | 3h | 中 |
| **B2** | Q-C1 | interrupt#3 重跑幂等 harness 首验（软前置门） | `tests/test_sprint4_b2_interrupt3_idempotency.py` | B1 | 3h | 高 |
| **C1** | S4-01 | `core/tools/run_command_tool.py`（coding 轻量 smoke 工具） | `core/tools/run_command_tool.py` | A1 + A3 | 3h | 低 |
| **C2** | S4-01/02 | `core/nodes/coding.py` 挂 2 工具 + prompt 稳定前缀 + coding 侧 Q-C 复验 | `core/nodes/coding.py` | B1 + B2 + C1 | 3h | 中 |
| **D1** | S4-06 | `sandbox/local_venv.py` `prepare_venv` 补 `extra_env` 透传 | `sandbox/local_venv.py` | -- | 2h | 低 |
| **D2** | S4-06 | HOTFIX-2 备忘闭环：`PIP_*` 白名单 vs 显式注入复核 | 结论记录（TODO 挂账闭环） | A3 + D1 | 1h | 低 |
| **E1** | S4-04 | sandbox 工具化（prepare_environment / run_in_sandbox + 结果收集器） | `core/nodes/execution.py` | A3 + D1 | 5h | 高 |
| **E2** | S4-03 | `_run_execution_agent`（裸 create_react_subgraph 装配 + execution system prompt） | `core/nodes/execution.py` | E1 + B1 + B2 | 6h | 高 |
| **E3** | S4-03/06/08 | execution() 步骤 1+2 替换 + 预算单点扣减 + CREDENTIAL_REQUIRED 分类 + logs 脱敏 | `core/nodes/execution.py` | E2 | 5h | 高 |
| **E4** | AC-S4-05/14 | sp3 回归守门（C3/D1/E2 用例）+ execution 侧 Q-C 复验 | `tests/test_sprint4_e4_*` | E3 | 3h | 中 |
| **F1** | S4-09 | UI：execution_monitor `user_input_request` 面板 + app.py 路由分支 | `ui/pages/execution_monitor.py` + `app.py` | B1 | 3h | 低 |
| **G1** | AC 全表 | mock 单测全套 + AC-S4-01~14 覆盖矩阵审计 | `tests/test_sprint4_*` | A~F 全部 | 5h | 中 |
| **G2** | AC-S4-08/11/13 | e2e（三 interrupt 串行 / 脱敏 grep 全链路 / 真凭证注入，凭证就绪后补跑） | `tests/test_sprint4_e2e.py` + 报告 | G1 | 4h | 中 |
| **G3** | -- | Prompt Cache 守门（coding prompt body 变更回归）+ handoff | `docs/sprint4/test-reports/` + handoff | C2 + G1 | 2h | 中 |

**任务总数**：17 个（3 基础设施 + 2 交互地基 + 2 coding + 2 sandbox 凭证 + 4 execution 改造 + 1 UI + 3 集成验收）。

**总估时**：**~53.5h**（基础设施 5.5h + 交互地基 6h + coding 6h + sandbox 凭证 3h + execution 改造 19h + UI 3h + 验收 11h）。

---

## 3. 阶段划分与依赖图

### 3.1 阶段总览

| 阶段 | 名称 | 任务 | 前置条件 | 产出 | AC 映射 |
|---|---|---|---|---|---|
| **A** | 基础设施（P0，先行被复用） | A1 / A2 / A3 | 无（A1∥A2 并行；A3 依赖 A1） | 3 常量 + 2 state 通道 + secrets_store | AC-S4-09（部分）/12 |
| **B** | 交互工具地基（P1，含头号验证项） | B1 / B2 | A3 | request_user_input + interrupt#3 契约 + Q-C 首个实证结论 | AC-S4-06 / 14（首验） |
| **C** | coding 补工具（P1，改动局部） | C1 / C2 | B1 + B2 + A1 + A3 | coding 7 工具 + prompt 守门 + Q-C 真实 wrapper 复验 | AC-S4-01 / 02 / 14 |
| **D** | 凭证注入地基（P1，可提前并行） | D1 / D2 | 无（D2 依赖 A3+D1） | prepare_venv extra_env + PIP_* 备忘闭环 | AC-S4-08（部分） |
| **E** | execution 改造（P1，最大工作量最高风险） | E1 / E2 / E3 / E4 | A 全 + B 全 + D1 | 手写编排 + 内嵌 ReAct 子图 + 预算对账 + credential 分类 | AC-S4-03/04/05/07/14 |
| **F** | UI（P1，与 E 并行） | F1 | B1 | user_input_request 交互面板 | AC-S4-10 |
| **G** | 集成与验收（P2 收尾交付） | G1 / G2 / G3 | A~F 全部 | mock 全套 + e2e + Prompt Cache 守门 + handoff | AC-S4-01~14 全覆盖 |

### 3.2 依赖关系图（Mermaid）

```mermaid
graph TD
    A1[A1 config 3 常量]
    A2[A2 state 2 通道]
    A3[A3 secrets_store]
    B1[B1 interaction_tools]
    B2[B2 Q-C harness 首验·软前置门]
    C1[C1 run_command_tool]
    C2[C2 coding 挂载 + Q-C 复验]
    D1[D1 prepare_venv extra_env]
    D2[D2 PIP_* 备忘闭环]
    E1[E1 sandbox 工具化]
    E2[E2 _run_execution_agent]
    E3[E3 execution 主函数改造]
    E4[E4 sp3 回归守门 + Q-C 复验]
    F1[F1 UI 面板 + app 路由]
    G1[G1 mock 全套 + AC 矩阵]
    G2[G2 e2e]
    G3[G3 Prompt Cache 守门 + handoff]

    A1 --> A3
    A1 --> C1
    A3 --> B1
    A3 --> C1
    A3 --> D2
    A3 --> E1
    B1 --> B2
    B1 --> C2
    B1 --> E2
    B1 --> F1
    B2 --> C2
    B2 --> E2
    C1 --> C2
    D1 --> D2
    D1 --> E1
    E1 --> E2
    E2 --> E3
    E3 --> E4
    C2 --> G1
    D2 --> G1
    E4 --> G1
    F1 --> G1
    G1 --> G2
    C2 --> G3
    G1 --> G3
    G2 --> G3
```

**关键路径**：A1 → A3 → B1 → B2 → E1（∥E2 前段）→ E2 → E3 → E4 → G1 → G2 → G3。

### 3.3 并行机会与文件边界（供多代理并行，主控收口）

多代理并行沿用「主工作区文件边界隔离」范式（各任务只碰各自文件、不碰共享 docs，主控统一收口全量回归）：

| 并行组 | 任务 | 文件边界（互不重叠） | 说明 |
|---|---|---|---|
| P-1（A 阶段内） | A1 ∥ A2 | `config.py` ∥ `core/state.py` | 纯追加，零冲突 |
| P-2（地基并行） | B1+B2 ∥ C1 ∥ D1 | `core/tools/interaction_tools.py` + `tests/test_sprint4_b2_*` ∥ `core/tools/run_command_tool.py` ∥ `sandbox/local_venv.py` | 三条线文件完全独立；C1 依赖 A1（`RUN_COMMAND_TIMEOUT`）+ A3（`mask_value`），D1 无依赖可最早启动 |
| P-3（主线并行） | E1~E4 ∥ C2 ∥ F1 | `core/nodes/execution.py` ∥ `core/nodes/coding.py` ∥ `ui/pages/execution_monitor.py` + `app.py` | E 与 C2 分别独占 execution.py / coding.py；F1 只碰 UI 两文件。**注意**：C2 与 E2 都会写 `tests/` 下各自新文件（`test_sprint4_c2_*` / `test_sprint4_e*`），命名错开即可 |
| 串行点 | B2 → C2 / E2 | -- | B2 是软前置门：Q-C 首验结论未出，C2/E2 不得挂 `request_user_input` 进真实节点 |
| 串行点 | G1 → G2 → G3 | -- | 验收线性收口，主控执行 |

---

## 4. 任务详细规格

### 阶段 A：基础设施（P0）

> **前置条件**：无。
> **产出**：3 个 config 常量、2 个 state 通道、secrets_store 模块，供 B/C/D/E 全线消费。

#### 任务 A1：`config.py` 新增 3 常量（S4-10）

- **产出文件**：`config.py`（纯追加，Sprint 4 独立段落，不动既有常量）
- **依赖项**：无
- **预计复杂度**：低（0.5h）
- **架构参考**：architecture §12.2

**需要实现的内容**（值取架构 §12.2 给定，不自创）：

| 常量名 | 值 | 用途 |
|---|---|---|
| `REACT_MAX_ROUNDS_EXECUTION` | `10` | execution 内嵌子图单次 invoke 轮次上限（budget_check_node 消费） |
| `RUN_COMMAND_TIMEOUT` | `120` | coding `run_command` 短超时（秒，机制上防跑重活；远小于 `SANDBOX_EXEC_TIMEOUT=1800`） |
| `SECRETS_FILE_NAME` | `".secrets"` | secrets 文件名；实际路径 = `Path(workspace_dir) / SECRETS_FILE_NAME`（运行期 state 优先，回退 `config.WORKSPACE_DIR`） |

**关键约束**：不新增交互超时常量（Q-F1 Maria 已定一直暂停）；沿用 sp1~sp3 字面量风格（无 env 覆盖）。

**自测检查点**：
- [x] CP-A1-1 三常量可导入且值 / 类型逐项断言（`REACT_MAX_ROUNDS_EXECUTION==10` int / `RUN_COMMAND_TIMEOUT==120` int / `SECRETS_FILE_NAME==".secrets"` str）
- [x] CP-A1-2 边界断言：`RUN_COMMAND_TIMEOUT < SANDBOX_EXEC_TIMEOUT`（120 < 1800，AC-S4-02 直接验收点）+ `REACT_MAX_ROUNDS_EXECUTION <= MAX_DEV_LOOP_LLM_CALLS`（10 ≤ 60，单次子图 invoke 不可能一次击穿子预算）
- [x] CP-A1-3 既有常量基线不动（`MAX_TOTAL_LLM_CALLS==120` / `MAX_FIX_LOOP_COUNT==10` / `MAX_DEV_LOOP_LLM_CALLS==60` / `DEV_LOOP_MIN_CALLS_PER_ROUND==2` / `REACT_MAX_ROUNDS_CODING==12`；git diff 实证纯追加）

（2026-07-05 按 test-reports/2026-07-02_a1-a2-d1-acceptance.md 验收 PASS 补勾）

---

#### 任务 A2：`core/state.py` 新增 2 个交互通道（S4-10）

- **产出文件**：`core/state.py`（追加 2 字段 + `create_initial_state` 默认值；**不碰 List 字段、不加 reducer**）
- **依赖项**：无
- **预计复杂度**：低（1h）
- **架构参考**：architecture §7.2 / §12.1

**需要实现的内容**：

| 改动 | 内容 |
|---|---|
| 追加字段 | `pending_user_input: Optional[Dict]`（当前待回答请求快照 question/is_sensitive/purpose_key；**绝不存答案**；MVP 为"通道声明占位"，UI 实际渲染走 interrupt payload，架构 §7.2 推荐） |
| 追加字段 | `collected_inputs: Dict[str, str]`（本任务内已收集**非敏感**项 purpose_key→value；敏感项绝不进入；MVP 写入落点从简——非敏感去重 P2 优化，架构 §6.3） |
| 默认值 | `create_initial_state`：`pending_user_input=None` / `collected_inputs={}` |
| 零改动 | `execution_result` / `fix_loop_count` / `fix_loop_history` / `node_errors` / `degraded_nodes` / `retry_budget_remaining` / `_dev_loop_route` / `_dev_loop_llm_calls`（L177-210 现状全部保留） |

**关键约束（must-fix-1 + B2/B3 治理）**：两字段均单值 / Dict 单点写，**无 reducer**；必须显式声明 + 默认值，否则节点写入被 LangGraph 静默丢弃。

**自测检查点**：
- [x] CP-A2-1 `GlobalState.__annotations__` 含两字段，类型正确；`create_initial_state` 默认值正确（老形态 + 新形态入参均覆盖）
- [x] CP-A2-2 **must-fix-1 grep 断言沿用**：`node_errors` / `degraded_nodes` / `fix_loop_history` 三字段仍无 `Annotated` / `operator.add`（沿用 sp3 CP-A2-3 双证：grep 零命中 + `get_origin is list`）
- [x] CP-A2-3 全量非 e2e 回归零退化（基线 1119 passed，确认追加字段不破坏既有初始化 / 反序列化）

（2026-07-05 按 test-reports/2026-07-02_a1-a2-d1-acceptance.md 验收 PASS 补勾）

---

#### 任务 A3：`core/secrets_store.py`（S4-07，安全关键）

- **产出文件**：`core/secrets_store.py`（新增）
- **依赖项**：A1（`SECRETS_FILE_NAME`）+ `config.WORKSPACE_DIR`
- **预计复杂度**：中（4h）
- **风险等级**：中（安全关键，AC-S4-09/11/12 直接依赖）
- **架构参考**：architecture §2.3 / §6（Q-E1 完整设计）/ §9.3-9.4

**需要实现的接口**（极简，不多不少）：

| 接口 | 语义 | 关键约束 |
|---|---|---|
| `lookup_secret(purpose_key, workspace_dir=None) -> Optional[str]` | 只读 `.secrets` 命中查询（去重 + 跨任务复用） | 文件缺失 / JSON 损坏返回 None 并打 WARNING（非静默） |
| `remember_secret(purpose_key, value, is_sensitive, workspace_dir=None)` | 「记住」落盘 | `os.open(O_CREAT\|O_WRONLY\|O_TRUNC, 0o600)` + `os.chmod(0o600)`；JSON 结构 `{purpose_key: {"value", "is_sensitive"}}`；POSIX 强制，Windows 打 WARNING 不强制 |
| `load_all_secrets(workspace_dir=None) -> Dict[str, str]` | 编排层启动时读入内存（供 extra_env 注入 + mask） | 只返回 value 映射 |
| `register_sensitive_value(value)` / `iter_sensitive_values()` | **进程内 sensitive-values set**（本次会话未「记住」的敏感值也必须可被 mask，架构 §9.4） | 模块级 set，值级去重；空串 / None 不注册 |
| `mask_value(text) -> str` | 把 text 中出现的一切已知敏感值（`.secrets` 中 `is_sensitive=True` 项 ∪ 进程内 set）替换为 `****` | 长值优先替换（防子串截断残留）；text 为 None/空返回原值 |
| `build_credential_env(secrets=None) -> Dict[str, str]` | 凭证 → 子进程 env 组装（coding `run_command` 与 execution sandbox 工具共用，避免两处各写一份映射） | **无条件含 `GIT_TERMINAL_PROMPT=0`**（R-S4-08：即便无凭证也带，让 git 认证失败立即返回）；`git_credential:<host>` → GIT_ASKPASS 脚本（0700，落 workspace 下，内容仅 echo token，架构 §9.3 推荐，token 不进命令行/URL）；`hf_token` → `HF_TOKEN` + `HUGGING_FACE_HUB_TOKEN` |

**路径基准**：运行期 `state["workspace_dir"]` 优先、回退 `config.WORKSPACE_DIR`（架构 §6.2；与 C1/C2 修复后 `_workspace_root(state)` 同基准哲学）。

**硬约束**：
- `.gitignore` 核对：默认 workspace 路径下 `.secrets` 已被 `workspace/` 规则覆盖（.gitignore L8 实证）——用 `git check-ignore workspace/.secrets` 断言，**不需要**新增 gitignore 行（若后续 Maria 改用项目根路径才需要，本 Sprint 按架构 §6.2 用 workspace_dir 不改）；
- 敏感值**绝不落日志**：本模块所有 logger 输出只打 purpose_key，不打 value；
- MVP 不加密（明文 0600），不做过期 / 轮换（PRD 非目标）。

**自测检查点**：
- [x] CP-A3-1 remember → lookup 闭环：落盘后命中返回原值；文件权限恰为 0600（`stat.S_IMODE == 0o600`）；不勾记住则不落盘
- [x] CP-A3-2 `.secrets` 在 gitignore 内（`git check-ignore` 断言，AC-S4-09 部分）；JSON 损坏 / 文件缺失 lookup 返回 None + caplog WARNING（失败非静默）
- [x] CP-A3-3 `mask_value`：已记住敏感值 + 进程内未记住敏感值均被替换为 `****`；非敏感项不 mask；子串 / 多值 / 长短值混合场景无明文残留（AC-S4-12 地基）
- [x] CP-A3-4 `build_credential_env`：无凭证时仍含 `GIT_TERMINAL_PROMPT=0`；`git_credential:github.com` 生成 GIT_ASKPASS 脚本（0700、workspace 下、token 不出现在 env 值以外任何路径 / 命令行）；`hf_token` 双变量映射正确
- [x] CP-A3-5 模块内 logger 全部输出经审计无 value 明文（caplog 断言）

（2026-07-05 按 test-reports/2026-07-02_a3-d2-acceptance.md 验收 PASS 补勾）

---

### 阶段 B：交互工具地基（P1，含头号验证项）

> **前置条件**：A3。
> **产出**：`request_user_input` 工具 + interrupt#3 payload/resume 契约 + Q-C1 重跑幂等首个实证结论。
> **门禁语义**：B2 是 C2 / E2 的**软前置门**——B2 结论异常（前序副作用工具 resume 重放且架构 §8.3 三层缓解无效）时触发架构师咨询、暂停 C2/E2 挂载。

#### 任务 B1：`core/tools/interaction_tools.py`（S4-05）

- **产出文件**：`core/tools/interaction_tools.py`（新增）
- **依赖项**：A3（`lookup_secret` / `remember_secret` / `register_sensitive_value`）+ `langgraph.types.interrupt`
- **预计复杂度**：中（3h）
- **架构参考**：architecture §2.1（含参考实现骨架）/ §7.1（payload 契约）

**需要实现的内容**（严格按架构 §2.1 骨架，四步语义）：

1. **模块常量** `INTERRUPT_KIND_USER_INPUT = "user_input_request"`；
2. `@tool request_user_input(question: str, is_sensitive: bool = False, purpose_key: str = "") -> str`：
   - **去重前查**：`purpose_key` 命中 `.secrets` → 直接返回缓存值、不 interrupt（跨任务复用，Maria 已定 purpose_key 粒度）；
   - **interrupt#3**：payload = `{"interrupt_kind": "user_input_request", "question", "is_sensitive", "purpose_key"}`（与 `"planning"` / `"dev_loop_failure"` 三类并存，`app.py::interrupt_kind` L255 已按 kind 分发零改动）；
   - **resume 契约**：`Command(resume={"value": str, "remember": bool})`；非法 resume（非 dict / 缺 value）→ 返回空串 + WARNING（Q-F2 无前端降级语义，失败非静默）；
   - **记住**：`remember and purpose_key` → `remember_secret(...)`；
   - **敏感值登记**：`is_sensitive=True` 时无论是否记住均 `register_sensitive_value(value)`（保证本次会话内 mask 覆盖）；
   - 返回**纯字符串**（用户值），不 json.dumps（架构 §2.1 序列化治理注记）。
3. **docstring 纪律**（三重约束，进工具 schema 参与 Prompt Cache）：仅在信息缺失阻塞任务时调用 / 一次只问一项 / **单独一轮调用、不与写文件/运行命令同轮**（架构 §8.3 缓解 2）；docstring 内零动态变量。

**自测检查点**：
- [x] CP-B1-1 mock interrupt：调用触发 payload 契约完整（interrupt_kind / question / is_sensitive / purpose_key 四键，purpose_key 空串转 None）
- [x] CP-B1-2 resume `{"value": "x", "remember": False}` → 返回 "x" 不落盘；`{"value": "x", "remember": True, purpose_key 非空}` → 落 `.secrets` 后返回；非法 resume（None / 缺 value / 非 dict）→ 返回空串 + caplog WARNING
- [x] CP-B1-3 purpose_key 命中 `.secrets` → **不 interrupt**（mock interrupt 0 次调用断言）直接返回缓存值（AC-S4-09 后半）
- [x] CP-B1-4 敏感值旁路断言：is_sensitive=True 的值经 `register_sensitive_value` 进程内可查、`mask_value` 可脱敏；工具返回值是纯 str（非 JSON）
- [x] CP-B1-5 docstring 字节级稳定（模块级常量断言，无动态变量）

（2026-07-05 按 test-reports/2026-07-03_b1-acceptance.md 验收 PASS 补勾）

#### 任务 B2：interrupt#3 重跑幂等 harness 首验（Q-C1，软前置门）

- **产出文件**：`tests/test_sprint4_b2_interrupt3_idempotency.py`（**不标 e2e**，mock LLM + InMemorySaver/SqliteSaver，不耗任何配额）
- **依赖项**：B1
- **预计复杂度**：中（3h）
- **风险等级**：高（R-S4-02，本 Sprint 头号验证项）
- **架构参考**：architecture §8（机理差异表 + 三层幂等保证 + AC-S4-14 断言设计）

**需要实现的内容**：

- 构造最小验证 harness：真实 `create_react_subgraph`（或经 `_make_react_wrapper` 的最小节点）+ 脚本化 mock LLM（第 1 轮 tool_calls=[副作用工具]，第 2 轮 tool_calls=[request_user_input]，第 3 轮收尾）+ 带计数副作用的 fake 工具（模拟 write_code_file：落盘计数 +1）+ checkpointer 绑定主图；
- **断言点 1（重跑范围实证）**：interrupt#3 暂停 → `Command(resume={"value": ...})` 恢复 → 观测**前序副作用工具计数是否 >1**（LangGraph 对 ReAct 子图内 tools 节点的 resume 重放范围实证——这是架构 §8.2 留白的机理事实，必须拿到第一手结论并写入报告）；
- **断言点 2（同轮混调风险）**：构造"同一轮 tool_calls 同时含副作用工具 + request_user_input"场景，实证架构 §8.3 缓解 2 所述"同批 tool_calls 重放"是否发生、幂等工具（覆盖写）是否安全；
- **断言点 3（messages 完整性）**：resume 后子图 messages 历史完整（前序 ToolMessage 不丢），agent 能带值继续到收尾；
- **断言点 4（连跑稳定）**：核心场景连跑 3 次结论一致（重跑幂等属机理类而非 LLM 服从度类，3 次足够；若出现不一致升级为 5 次并排查）；
- 结论落 `docs/sprint4/test-reports/2026-MM-DD_b2-interrupt3-idempotency.md`（含给 C2/E2 的挂载建议：request_user_input 是否必须独立一轮、collector 类实现在 resume 后的可见性）。

**自测检查点**：
- [x] CP-B2-1 harness 跑通：interrupt#3 → resume → 值回到 agent → 收尾（AC-S4-06 mock 层首证）
- [x] CP-B2-2 **断言点 1 结论落档**：前序独立轮次的副作用工具在 resume 后**副作用恰为 1**（AC-S4-14 核心断言）——若 >1，判定架构 §8.3 缓解 1（LangGraph 节点级 resume 定位）失效，**触发架构师咨询，C2/E2 暂停挂载**
- [x] CP-B2-3 断言点 2/3 结论落档（同轮混调行为 + messages 完整性），连跑 3 次一致
- [x] CP-B2-4 报告归档 `docs/sprint4/test-reports/`（含 resume 重放语义实证 + 对 collector 方案的影响评估，供 E1/E2 消费）

（2026-07-05 按 test-reports/2026-07-04_b2-interrupt3-idempotency.md 验收 PASS 补勾——门禁通过，C2/E2 已获准挂载）

---

### 阶段 C：coding 补工具（P1）

> **前置条件**：B1 + B2（软前置门）+ A1 + A3。
> **产出**：coding agent 7 工具形态 + prompt 稳定前缀守门 + coding 真实 wrapper 上的 Q-C 复验。

#### 任务 C1：`core/tools/run_command_tool.py`（S4-01）

- **产出文件**：`core/tools/run_command_tool.py`（新增）
- **依赖项**：A1（`RUN_COMMAND_TIMEOUT`）+ A3（`mask_value`）+ `sandbox/local_venv.py`（`_run_subprocess` L330 / `_require_within_workspace` L211，均现状零改动直接复用）
- **预计复杂度**：低（3h）
- **架构参考**：architecture §2.2（参考实现骨架）/ §5（Q-B1 全部结论）

**需要实现的内容**（严格按架构 §2.2 骨架 + §5 边界）：

- 工厂 `make_run_command_tool(base_dir: str, extra_env: Optional[Dict[str, str]] = None)`：闭包绑定 `code_output_dir` 作 cwd + 已收集凭证 extra_env；
- 工具体：`shlex.split` 解析（解析失败 → 结构化错误 JSON，非抛异常）→ `_require_within_workspace(base_dir, label=...)` 越界校验（越界 → 结构化错误 + WARNING）→ `_run_subprocess(argv, cwd=base_dir, timeout=RUN_COMMAND_TIMEOUT, output_max_bytes=SANDBOX_OUTPUT_MAX_BYTES, extra_env=extra_env)`；
- **返回 JSON**（BUG-S1-02 范式）：`json.dumps({"exit_code", "stdout_tail", "stderr_tail", "timed_out", "truncated"}, ensure_ascii=False, sort_keys=True, default=str)`；**stdout/stderr 返回前经 `mask_value` 脱敏**（架构 §9.4 落点表；smoke 中 clone 失败回显可能含 token URL）；
- **系统解释器直跑 argv，不共用 venv**（Q-B1 结论：依赖判定交 execution，MVP 不做 venv 复用可选项）；
- docstring 纪律：明确"仅轻量验证（import / py_compile / 启动探测），禁止完整训练 / 评估 / 下载大数据集"（+120s 超时机制双保险）；零动态变量。

**自测检查点**：
- [x] CP-C1-1 正常 smoke（`python -c "print(1)"`）返回合法 JSON、exit_code=0；`json.loads` 可解析 + sort_keys + ensure_ascii=False（禁 str(dict) 断言）
- [x] CP-C1-2 越界 base_dir（/etc 等）→ 结构化错误 JSON + caplog WARNING，`_run_subprocess` 0 次调用（校验在前）；解析失败（不闭合引号）/ 空命令 → 结构化错误不炸子图
- [x] CP-C1-3 超时护栏：真实 sleep 子进程 + monkeypatch 短超时 → `timed_out=True`、子进程树被杀（AC-S4-02；`RUN_COMMAND_TIMEOUT=120 < SANDBOX_EXEC_TIMEOUT=1800` 常量断言在 CP-A1-2）
- [x] CP-C1-4 脱敏：`register_sensitive_value` 注入已知 token 后，stdout/stderr 含 token 的输出返回前被 mask 为 `****`
- [x] CP-C1-5 **不写 execution_result**：工具返回值结构中无 metrics / success 语义键，B 档判定无从消费（Q-B1 红线 3 的机制面断言）

#### 任务 C2：`core/nodes/coding.py` 挂载 + prompt 守门 + Q-C 复验（S4-01/02）

- **产出文件**：`core/nodes/coding.py`（改动局部：`_get_coding_tools` + `_CODING_SYSTEM_PROMPT_BODY`；wrapper 形态 L436 零改动）
- **依赖项**：B1 + B2 + C1
- **预计复杂度**：中（3h）
- **架构参考**：architecture §2.4

**需要实现的内容**：

- `_get_coding_tools`（L255）：既有 5 工具 + `make_run_command_tool(base_dir=code_dir, extra_env=build_credential_env(load_all_secrets()))` + `request_user_input`（共 7 工具，AC-S4-01）；
- `_CODING_SYSTEM_PROMPT_BODY`（L87）追加一段**稳定前缀**工具说明：`run_command` 仅轻量验证边界（Q-B1 红线：smoke 成功≠复现成功、完整执行交 execution）+ `request_user_input` 使用纪律（缺信息才用 / 逐条问 / 单独一轮）——**段内零论文级动态变量**，不破坏方案 A；
- coding 侧 Q-C 复验（B2 结论在真实 coding wrapper 上复证）：mock LLM 剧本 write_code_file → request_user_input → resume，断言 write 副作用恰为 1 + `code_output_dir` 等 map_result 契约不回归。

**自测检查点**：
- [x] CP-C2-1 工具集恰为 7 个、名称集合断言（AC-S4-01 存在性）；wrapper 形态不变（`coding` 仍为 `_make_react_wrapper` 产物，`_map_coding_result` 3 参签名不动）
- [x] CP-C2-2 prompt 主体字节级一致守门更新：沿用 sp3 CP-F3-1 断言（两篇不同论文去尾部动态段后主体 `==` 新 `_CODING_SYSTEM_PROMPT_BODY` 常量），确认新增段落进稳定前缀
- [x] CP-C2-3 **coding 真实 wrapper Q-C 复验**（AC-S4-14 coding 侧）：write → request_user_input → resume 后文件写副作用恰为 1、文件内容正确、agent 拿到值继续收尾
- [x] CP-C2-4 凭证注入链路：mock `.secrets` 含 git 凭证时 `run_command` 收到的 extra_env 含 GIT_ASKPASS + GIT_TERMINAL_PROMPT=0（经工厂闭包断言）
- [x] CP-C2-5 coding 既有测试全绿（sp3 c1 系列 + c1_fix 系列零退化）

---

### 阶段 D：凭证注入地基（P1，可最早并行启动）

> **前置条件**：D1 无依赖；D2 依赖 A3 + D1。
> **产出**：`prepare_venv` extra_env 透传 + HOTFIX-2 备忘闭环。

#### 任务 D1：`sandbox/local_venv.py` `prepare_venv` 补 `extra_env`（S4-06）

- **产出文件**：`sandbox/local_venv.py`（最小 diff：仅 `prepare_venv` 签名 + 内部透传）
- **依赖项**：无（`_run_subprocess` L330 已带 extra_env 形参 + `_build_sandbox_env` L126 白名单收口，HOTFIX-2 后现状）
- **预计复杂度**：低（2h）
- **架构参考**：architecture §9.3

**需要实现的内容**：

- `prepare_venv`（L488）签名追加 `extra_env: Optional[Dict[str, str]] = None`（向后兼容，默认 None）；
- 内部**所有** `_run_subprocess` 调用点（venv 创建 + 每次 pip install，含重试路径）透传 `extra_env=extra_env`（pip 装私有源依赖需凭证；`GIT_TERMINAL_PROMPT=0` 也经此让 pip 内 git 依赖认证失败立即返回）；
- `run_in_venv`（L696）已有 extra_env，零改动。

**自测检查点**：
- [x] CP-D1-1 签名向后兼容：不传 extra_env 时行为与 sp3 逐字一致（既有 B1 护栏 38 条 + HOTFIX-2 隔离 9 条全绿零退化）
- [x] CP-D1-2 透传断言：spy `_run_subprocess`，venv 创建 + pip install（含瞬态重试第 2 次）全路径均收到 extra_env；`_build_sandbox_env` 合并语义（白名单之上显式覆盖）不被绕过
- [x] CP-D1-3 注入值不落 `install_log` 明文（构造含 token 的 extra_env + mock pip 输出回显，断言 prepare 结果中已 mask 或不含——若 prepare_venv 层不做 mask 则由 E3 收尾统一 mask，此 CP 验证链路上至少一处兜住，落点写进报告）

（2026-07-05 按 test-reports/2026-07-02_a1-a2-d1-acceptance.md 验收 PASS 补勾；CP-D1-3 判定"PASS（非阻断，附挂账 L-D1-01）"）

#### 任务 D2：HOTFIX-2 备忘闭环——`PIP_*` 白名单 vs 显式注入复核（S4-06 配套）

- **产出**：结论记录（更新到 G3 handoff + 通知主控闭环 TODO.md L320 挂账项；本任务**不直接改 TODO.md**，主控收口）
- **依赖项**：A3 + D1
- **预计复杂度**：低（1h）
- **背景**：TODO「HOTFIX-2 备忘」——`_SANDBOX_ENV_ALLOWLIST_PREFIXES` 含 `PIP_*` 前缀透传（local_venv.py L123），理论上 `PIP_INDEX_URL=https://user:token@host/` 形态会把私有源凭证带进沙箱；备忘要求 `.secrets`/extra_env 方案落地时改显式注入并复核。

**需要执行的内容**：

1. 实证当前环境 `PIP_*` 变量现状（是否存在内嵌凭证形态）；
2. 评估两案并**与架构师确认**（触发条件：涉及白名单变更 = 架构文档未覆盖的安全决策）：(a) `PIP_*` 从前缀白名单剔除、私有源凭证一律走 `.secrets` purpose_key（如 `pip_index_url`）显式 extra_env 注入；(b) 保留前缀透传 + `mask_value` 把 `PIP_*` 值中凭证形态纳入脱敏集。倾向 (a)（与 HOTFIX-2 "凭证注入只走 extra_env 显式口" 的设计意图一致），但需评估对现有 pip 源配置（非凭证类 PIP_INDEX_URL / PIP_CACHE_DIR）的破坏面——非凭证 PIP_* 仍应透传；
3. 结论 + 改动（若有）落地，同步 G3 handoff。

**自测检查点**：
- [x] CP-D2-1 架构师确认记录在案（方案 + 理由）；若改白名单：既有 `test_sandbox_env_isolation.py` 9 条相应更新且语义不弱化；若不改：`mask_value` 覆盖面结论落档
- [x] CP-D2-2 无论何案：构造 `PIP_INDEX_URL` 含 `user:token@` 的环境，断言 token 不出现在沙箱子进程可见 env 或已被显式注入路径接管（AC-S4-11 的 pip 分支地基）

（2026-07-05 按 test-reports/2026-07-02_a3-d2-acceptance.md 验收 PASS 补勾）

---

### 阶段 E：execution 改造（P1，最大工作量 + 最高风险）

> **前置条件**：A 全 + B 全（B2 软前置门）+ D1。
> **产出**：execution = 手写编排 + 内嵌 ReAct 子图；预算对账落点 B；credential 分类闭环；sp3 命门零回归。
> **总原则（架构 §3.3 分层落点表钉死）**：编排层 ①②④⑤⑥（guard / 降级 / 分类判定 map / 边界 interrupt#2 / self-loop）**逐字保留 sp3**，仅 ③（步骤 1+2）替换。

#### 任务 E1：sandbox 工具化（S4-04）

- **产出文件**：`core/nodes/execution.py`（新增工具工厂区段；确定性辅助函数 `_step_to_command` L520 / `_rewrite_interpreter` L540 / `_expand_globs` L552 / `_resolve_cd` L574 保留为工具内部实现）
- **依赖项**：A3 + D1
- **预计复杂度**：高（5h）
- **风险等级**：高（R-S4-01 真实结果取回）
- **架构参考**：architecture §3.3 工具层 / §3.4 关键注记 / §9.3

**需要实现的内容**：

- **结果收集器**（R-S4-01 核心方案）：`_SandboxRunCollector`（或等价的闭包列表）——由 `_run_execution_agent` 每次调用时新建、经工厂闭包绑进两工具；工具体内**真跑 sandbox 后把真实 `SandboxPrepareResult` / `SandboxRunResult` append 进收集器**；编排层收尾读收集器而非 agent 自述（**agent 无法伪造 exit_code**）。**兜底通道**（B2 结论若显示 resume 重跑后收集器重建丢前序结果时启用）：从子图 final messages 的 ToolMessage 解析回填（BUG-S1-03 范式：过滤失败 ToolMessage、只认成功记录、失败打 WARNING）——MVP 先单通道收集器，兜底按 B2/E4 实证决定是否启用，避免过度工程；
- `make_prepare_environment_tool(work_dir, plan, collector, extra_env)`：包 `prepare_venv(work_dir, requirements=_extract_requirements(plan), ..., extra_env=extra_env)`；返回 JSON（success / python_exe / install_failed_packages / error 摘要，`json.dumps(ensure_ascii=False, sort_keys=True, default=str)`）；结果 append 收集器；
- `make_run_in_sandbox_tool(work_dir, collector, extra_env, python_exe_ref)`：入参单条命令字符串 → `_step_to_command` 拆分 / `_rewrite_interpreter` 改写 / `_resolve_cd` 边界校验 / `_expand_globs`（确定性能力留工具内，agent 只管"跑哪条"）→ `run_in_venv(..., extra_env=extra_env)`；返回 JSON（exit_code / stdout_tail / stderr_tail / timed_out / truncated）；**stdout/stderr 返回前 `mask_value`**（与 C1 同范式：ToolMessage 虽在子图私有 messages，但 agent 可能把内容复述进 `<result>` 进而入 state，必须在源头 mask）；结果 append 收集器；
- 工具异常一律 try/except 转结构化错误 JSON + WARNING，不炸子图（sp1 以来工具工厂治理）。

**自测检查点**：
- [x] CP-E1-1 两工具返回合法 JSON（sort_keys / ensure_ascii=False / 禁 str(dict)）；mock sandbox 下收集器收到的是**真实 dataclass 结果**而非 agent 文本
- [x] CP-E1-2 确定性解析改写在工具内生效（复合命令 `&&`/`;` 拆分、裸 python/pip 改写 venv 解释器、cd 越界拒绝、glob 展开——复用 sp3 既有用例语料断言行为等价）
- [x] CP-E1-3 脱敏：注入已知 token 后 run_in_sandbox 返回的 stdout/stderr 无明文（`****`）
- [x] CP-E1-4 extra_env 注入链路：两工具透传到 `prepare_venv` / `run_in_venv`（spy 断言）；无条件含 `GIT_TERMINAL_PROMPT=0`
- [x] CP-E1-5 工具异常（sandbox 抛 SandboxCreationError / OSError 兜底）转结构化错误 + WARNING，子图不被打断

#### 任务 E2：`_run_execution_agent`（S4-03 内嵌子图装配）

- **产出文件**：`core/nodes/execution.py`（新增 `_run_execution_agent` + `_EXECUTION_SYSTEM_PROMPT_BODY` + `_build_execution_agent_context`）
- **依赖项**：E1 + B1 + B2
- **预计复杂度**：高（6h）
- **风险等级**：高（**首个裸 `create_react_subgraph` 消费者**，见下方注意）
- **架构参考**：architecture §3.3 子图层 / §3.4 / §4.3

**⚠️ 实现注意（本计划对架构 §3.4 "与 planning 同构" 的机制层校准，见 §7.2 差异 3）**：planning 实际是经 `_make_react_wrapper` 生成 `_planning_react` 后在节点函数内调用（planning.py L614/L736），wrapper 会自动 setdefault 扣减 `retry_budget_remaining`（react_base.py L894-898）。而架构 §4.2 给 execution 明确规定**不经 wrapper、裸 `create_react_subgraph(...).invoke(...)`、扣减由编排层显式做**——语义上这是为了让"子图 rounds + metrics 抽取"合并成唯一扣减点（落点 B），设计成立且本计划遵循。代价是：wrapper 内建的装配纪律必须在 `_run_execution_agent` 手工复刻，**逐项对照 react_base.py L815-866 清单**：

| wrapper 内建项 | `_run_execution_agent` 复刻要求 |
|---|---|
| LLM 路由注入 | `llm = create_llm(resolve_llm_config(state["llm_config_set"], "execution"))` → `context["_llm"]`（子图 `_bind_llm` L520 硬依赖，缺失抛 TransientError） |
| 消息装配（Prompt Cache 方案 A） | SystemMessage = `_EXECUTION_SYSTEM_PROMPT_BODY` 稳定常量（零论文级动态变量）；HumanMessage = 动态上下文 `json.dumps(sort_keys=True, ensure_ascii=False, default=str)`（execution_steps / work_dir / 修复回合反馈摘要等） |
| ReActState 初始化 | `{"messages", "round": 0, "max_rounds": REACT_MAX_ROUNDS_EXECUTION, "status": "reasoning", "result": None, "context"}` |
| rounds 提取 | `rounds_used = max(1, int(final_state.get("round", 0)))`（与 wrapper 同口径，喂 E3 扣减） |
| 重试层 | `create_react_subgraph` 内部已接 `invoke_with_retry`（HOTFIX-1），自动获得，无需额外处理 |

**需要实现的内容**：

- `_run_execution_agent(state, work_dir, plan) -> ExecAgentOutput`（轻量返回结构：`prep` / `run_results` / `rounds_used` / `llm_calls`＝rounds_used）：新建收集器 → 组装 extra_env（`build_credential_env(load_all_secrets())`）→ 装配三工具（prepare_environment / run_in_sandbox / `request_user_input`）→ 裸子图 invoke → 从收集器取真实结果 + 从 final_state 取 rounds；
- **子图任何异常降级**（与 planning L736-757 同范式）：异常捕获 → WARNING → 返回空结果集（编排层 `_classify_execution` 对空 run_results 走既有降级分类路径，不炸节点）；
- `_EXECUTION_SYSTEM_PROMPT_BODY`：职责边界（先 prepare 后 run / 逐条跑 execution_steps / 看 exit_code 决策 / 缺凭证或缺信息就地 `request_user_input`、**问在跑重活之前**（R-S4-03 缓解）/ 不得自行宣称成功——成功由编排层判定）；零动态变量。

**自测检查点**：
- [x] CP-E2-1 装配清单逐项断言：`_llm` 注入（缺 llm_config_set 时报错路径）、SystemMessage 主体字节级一致（两个不同任务 state 去尾部/HumanMessage 后 `==` 常量——沿用 CP-F3-1 断言范式）、HumanMessage sort_keys 幂等
- [x] CP-E2-2 mock LLM 剧本（prepare → run×2 → 收尾）：收集器取回真实结果、rounds_used 与剧本轮数一致；`REACT_MAX_ROUNDS_EXECUTION=10` 到顶 force_finish 不越界（budget_check_node L613 消费断言）
- [x] CP-E2-3 agent 谎报拦截：剧本让 LLM `<result>` 自称 success 但收集器 exit_code 非 0 → 上层（E3 装配后）判定失败（R-S4-01 端到端断言，可在 E3 后补跑）
- [x] CP-E2-4 子图异常降级：mock 子图抛异常 → WARNING + 空结果返回，节点不炸
- [x] CP-E2-5 execution 侧 request_user_input 挂载：剧本 run（认证失败 stderr）→ request_user_input → resume → 再 run（成功）走通（AC-S4-06 execution 侧 mock 证）

#### 任务 E3：execution() 主函数改造 + 预算对账 + credential 分类（S4-03/06/08）

- **产出文件**：`core/nodes/execution.py`（主函数 L1020 步骤 1+2 替换；`_map_execution_result` L737 签名扩展；`ErrorCategory` L87 + 关键字表；`_classify_execution` L167 顺序插入）
- **依赖项**：E2
- **预计复杂度**：高（5h）
- **架构参考**：architecture §3.4（主函数骨架伪代码）/ §4.3-4.5（计数职责划分表 + 扣减时序）/ §9.2（检测分类）

**需要实现的内容**：

1. **主函数**（架构 §3.4 骨架逐行对齐）：guard 分支（L1029-1045）/ work_dir 缺失降级（L1046-1060）**逐字保留**；原"prepare_venv + `_run_step_subcommands`（L595）确定性循环"替换为 `_run_execution_agent` 调用；步骤 3-7（`_classify_execution` → `_parse_metrics` L412 → `_build_execution_result` L687 → `_map_execution_result` → `_maybe_interrupt_or_return` L925）**调用序逐字保留**；`_run_step_subcommands` 若不再有调用方可删（其子能力已下沉 E1 工具内——删除前 grep 确认测试引用，必要时保留为工具内部私有实现的别名）；
2. **`_map_execution_result` 扩展**（落点 B 唯一扣减点）：签名追加 `react_rounds_used: int = 0`、既有 `llm_calls_used`（metrics 抽取）保留——`retry_budget_remaining -= (react_rounds_used + llm_calls_used)`、`_dev_loop_llm_calls += (react_rounds_used + llm_calls_used)`，单点 read-modify-write + INFO 日志（沿用 L780-795 现范式扩展）；**guard 命中路径 rounds=0 不重扣**（架构 §4.4 时序）；
3. **credential 分类**：`ErrorCategory` 增 `CREDENTIAL_REQUIRED = "credential_required"`（execution 本地 Enum，**core/errors.py 零改动**——架构 §13 表该行以 §9.2 为准，见 §7.2 差异 4）；**不进** `AUTO_FIXABLE`（L105）；`_map_category_to_error_type`（L727）映射 `permanent`；`_CREDENTIAL_KEYWORDS` 8 关键字表（架构 §9.2 逐字），在 `_classify_execution` 内**先于** DATA_MISSING / HARDWARE 判定；
4. **logs 脱敏**：`_aggregate_logs`（L667）结果回 state 前 `mask_value`（架构 §9.4 落点表）；`_build_dev_loop_interrupt_payload`（L844）中的日志片段同样过 mask；
5. **修复循环边界零改动**：`_maybe_interrupt_or_return` / `_has_committed_result_for_round`（L1006）/ `_ROUTE_AWAIT_INTERRUPT` self-loop / interrupt#2 payload **逐字不动**（AC-S4-05 命门）。

**自测检查点**：
- [x] CP-E3-1 **预算对账（AC-S4-04）**：一次 execution 后 `retry_budget_remaining` 递减恰 = 子图 rounds + metric_llm_calls；`_dev_loop_llm_calls` 累加同额；guard 命中重入路径零扣减（rounds=0）；触顶 60 → interrupt#2（复用 sp3 CP-C3-9/10 断言范式改造）
- [x] CP-E3-2 **credential 分类（AC-S4-07）**：8 关键字参数化命中 `CREDENTIAL_REQUIRED`；`auto_fixable=False`、error_type=permanent、**不消耗 fix_loop_count**（进 `_maybe_interrupt_or_return` 走 interrupt#2 兜底路径而非 retry_coding）；判定顺序先于 data_missing/hardware（构造双关键字 stderr 断言）
- [x] CP-E3-3 B 档判定只认编排层：收集器 exit 全 0 + ≥1 指标 → success；agent 自述成功但收集器有非 0 → failure（CP-E2-3 端到端闭环）（L-E4-01 裁决后口径 = `_effective_runs` 有效执行视图——同命令 argv 精确匹配取最后一次，agent 就地重试成功不判 failure；见架构 §3.2 注记与 commit 86c5325）
- [x] CP-E3-4 logs / interrupt payload 无凭证明文（注入已知 token 后断言，AC-S4-11 节点内分支）
- [x] CP-E3-5 通信契约不变（AC-S4-08 / Q-D）：失败时 `execution_result` + `[error_category=...]` 前缀 + `fix_loop_history` 逐字同 sp3 schema，coding `_digest_execution_feedback`（coding.py L162）无需任何改动即可消费（跑一次 coding 侧解析断言）

#### 任务 E4：sp3 回归守门 + execution 侧 Q-C 复验（AC-S4-05/14）

- **产出文件**：`tests/test_sprint4_e4_regression_gate.py`（新增补充断言；主体为既有用例回归）
- **依赖项**：E3
- **预计复杂度**：中（3h）

**需要执行的内容**：

- **AC-S4-05 回归**：sp3 C3（`test_sprint3_c3*`）/ D1（graph 路由）/ E2（execution_monitor）全部既有用例全绿——execution 内部改造对外契约（interrupt#2 三态 resume / commit 边界 self-loop / fix_loop_count 自增 / 降级路径）零回归；预期部分 c3 用例因"步骤 1+2 换子图"需适配 mock 点（patch `_run_execution_agent` 而非 `prepare_venv`）——**适配只许换 mock 落点、不许弱化断言语义**（沿用 C1 修复验收"独立判定改测理由"纪律，逐条记录）；
- **execution 侧 Q-C 复验（AC-S4-14）**：真实 execution 节点 + mock LLM 剧本"run_in_sandbox（真跑 mock sandbox 副作用计数）→ request_user_input → resume"，断言 resume 后前序 sandbox 副作用恰为 1（收集器完整性同时验证——B2 结论的 execution 场景落地证）；
- 全量非 e2e 回归零退化（基线 1119 + 本 Sprint 新增）；interrupt/重跑幂等类用例**连跑 3 次**（复现率高类判据），异常升 5 次。

**自测检查点**：
- [x] CP-E4-1 sp3 C3/D1/E2 既有用例全绿（AC-S4-05）；mock 落点适配清单逐条记录（改哪条 / 为何 / 断言语义不降）
- [x] CP-E4-2 execution 侧 AC-S4-14 断言 PASS（sandbox 副作用恰为 1 + 收集器在 resume 后结果完整；若收集器丢前序 → 启用 E1 兜底通道并复测）
- [x] CP-E4-3 全量非 e2e 回归零退化，interrupt 类用例连跑 3 次 0 flaky

---

### 阶段 F：UI（P1，与 E 并行）

#### 任务 F1：execution_monitor `user_input_request` 面板 + app.py 路由（S4-09）

- **产出文件**：`ui/pages/execution_monitor.py` + `app.py`（路由分发处新增分支；`interrupt_kind` helper L255 / `resume_with` L175 本体零改动）
- **依赖项**：B1（payload 契约）
- **预计复杂度**：低（3h）
- **架构参考**：architecture §7.3-7.4

**需要实现的内容**：

- `is_interrupted and interrupt_kind == "user_input_request"` 分支：读 `get_interrupt_payload`（L234）渲染 `question` + 一句上下文（`current_step`）+ 单输入框（`is_sensitive=True` → `type="password"`）+ 敏感时「记住此凭证供后续复现复用」勾选（默认不勾）→ 提交 `resume_with(thread_id, {"value": ..., "remember": ...})`；
- **就一个输入框**（Maria 硬约束，无按类型分渲染）；沿用 sp3 dev_loop 决策面板的组件范式与轮询机制；
- app.py 路由分发：`user_input_request` → execution_monitor 页（与 `dev_loop_failure` 同页不同面板）。

**自测检查点**：
- [x] CP-F1-1 面板逻辑单测（沿用 sp3 E2 测试范式 mock controller）：三类 interrupt_kind 分发正确（planning / dev_loop_failure / user_input_request 互不误触）；敏感 → password + 勾选出现、非敏感 → 普通输入 + 无勾选
- [x] CP-F1-2 提交 payload 契约：`{"value", "remember"}` 两键、类型正确、经 `resume_with` 透传
- [ ] CP-F1-3 手动 happy path（`streamlit run`）走查记录（AC-S4-10，与测试工程师协作，coding / execution 两侧各一次）

---

### 阶段 G：集成与验收（P2）

#### 任务 G1：mock 单测全套 + AC 覆盖矩阵审计

- **产出文件**：`tests/test_sprint4_g1.py`（Sprint 级聚合断言）+ 各任务已落 CP 用例
- **依赖项**：A~F 全部
- **预计复杂度**：中（5h）
- **覆盖**（对照 §5 AC 矩阵）：AC-S4-01/02/03/04/05/06/07/09/12/14 mock 层全覆盖 + AC-S4-03 结构测试（`build_graph` 仍 7 节点、节点名集合逐字、编译成功、`await_dev_loop_interrupt` self-loop 边在——graph.py 零改动的行为证）+ 元断言矩阵审计（沿用 sp3 CP-F1-4 范式，防"名字对但断言空泛"）。

**自测检查点**：
- [ ] CP-G1-1 AC-S4-01~07 / 09 / 12 / 14 mock 层逐条有 CP 用例映射且运行全绿（参数化审计）
- [ ] CP-G1-2 AC-S4-03 结构测试 PASS（7 节点 / 边结构 / self-loop / graph.py git diff 零改动实证）
- [ ] CP-G1-3 全量非 e2e 回归零退化 + 连跑 3 次 0 flaky（新增用例总量与各任务 CP 对账）

#### 任务 G2：e2e（凭证就绪后补跑，省配额范式）

- **产出文件**：`tests/test_sprint4_e2e.py` + `docs/sprint4/test-reports/`
- **依赖项**：G1
- **预计复杂度**：中（4h）
- **测试策略**（架构 §16.2 省配额范式，**任何真跑须 Maria 明确授权具体动作**）：
  1. **AC-S4-13 三 interrupt 串行**：同 thread_id 下 planning → （coding 内 user_input_request）→ dev_loop_failure 依次触发、各自正确路由——mock LLM + 真实 build_graph + checkpointer 先行（不耗配额），真实 LLM 链路按授权补跑；
  2. **AC-S4-11 脱敏 grep 全链路**：注入已知假 token（`FAKE-TOKEN-sp4-e2e` 类哨兵值，**不用真凭证**即可覆盖大部分链路）→ 跑完整修复循环 → grep 工作目录生成代码 / checkpoint DB / 报告 Markdown / caplog 无明文；
  3. **AC-S4-08 真凭证注入**：需真私有仓库 + 真 token，**默认 mock 注入断言**（extra_env 透传链路 spy），真跑列授权补跑项；
  4. mock sandbox 不真跑训练；先 smoke fail-fast。
- **复跑要求**：interrupt 链路类连跑 3 次；LLM 服从度依赖类（agent 是否主动调 request_user_input）按实测复现率定 3~5 次。

**自测检查点**：
- [ ] CP-G2-1 三 interrupt 串行 e2e（mock 层）PASS + 连跑 3 次（AC-S4-13）
- [ ] CP-G2-2 脱敏 grep 全链路（哨兵 token）PASS：代码 / checkpoint / 报告 / 日志四落点零明文（AC-S4-11）
- [ ] CP-G2-3 AC-S4-08 mock 注入断言 PASS；真实链路骨架（`@pytest.mark.e2e` + 凭证 skipif）就绪，真跑转正**待 Maria 授权**后补跑并归档跑数/耗时
- [ ] CP-G2-4 e2e 报告归档 `docs/sprint4/test-reports/`

#### 任务 G3：Prompt Cache 守门 + handoff

- **产出文件**：`docs/sprint4/test-reports/` + `docs/sprint4/handoff-to-test-engineer.md`
- **依赖项**：C2 + G1（+ G2 结果引用）
- **预计复杂度**：中（2h）
- **内容**：
  - **Prompt Cache 守门**：coding prompt body 本 Sprint 有变更（C2 新增工具说明段）→ 字节级一致断言（CP-C2-2 已落）+ 命中率回归对照 sp3 F3 基线 `R_after=0.9008` ≥ ×0.95（沿用 `scripts/spike_coding_prompt_cache.py` + `SPIKE_F3_AUTHORIZED` 双保险闸门，**真跑须授权**）；execution 新 prompt（`_EXECUTION_SYSTEM_PROMPT_BODY`）建字节级一致基线（CP-E2-1 已落，命中率首测列授权补跑）；
  - **handoff**：运行方式（mock 全量 / e2e 入口 / 授权补跑清单）+ AC-S4-01~14 覆盖矩阵 + 已知限制（Q-C 实证结论摘要 / R-S4-03 训练中途 interrupt 重跑代价 MVP 接受 / credential 关键字表覆盖边界 / D2 结论）+ 交付物清单与本计划 §9 一致性核对 + **D2 备忘闭环结论**（供主控更新 TODO）。

**自测检查点**：
- [ ] CP-G3-1 coding prompt 字节级一致（新段落进稳定前缀）+ 命中率回归脚本就绪（真跑待授权）；execution prompt 字节级基线建立
- [ ] CP-G3-2 handoff 文档完成，交付物与 §9 逐条一致
- [ ] CP-G3-3 全量非 e2e 回归最终收口数字落档（对照基线 1119 零退化）

---

## 5. AC 覆盖矩阵

| 验收标准 | 对应任务 | 关键检查点 | 测试类型 |
|---|---|---|---|
| AC-S4-01 run_command 存在 + 正常返回 + 越界拒 | C1 + C2 | CP-C1-1/2 / CP-C2-1 | mock |
| AC-S4-02 护栏复用 + 超时 < SANDBOX_EXEC_TIMEOUT | A1 + C1 | CP-A1-2 / CP-C1-3 | mock |
| AC-S4-03 7 节点骨架不变 | E3 + G1 | CP-G1-2 | 结构测试 |
| AC-S4-04 预算扣减 = rounds + metrics，不双扣 | E3 | CP-E3-1 | mock |
| AC-S4-05 interrupt#2 / 修复循环边界零回归 | E4 | CP-E4-1 | sp3 用例回归 |
| AC-S4-06 request_user_input 两 agent 均触发 interrupt#3 + resume 继续 | B1 + E2 + G2 | CP-B1-1/2 / CP-B2-1 / CP-E2-5 | mock + e2e |
| AC-S4-07 credential_required 分类 + 不耗 fix_loop_count | E3 | CP-E3-2 | mock |
| AC-S4-08 凭证经 extra_env 注入 sandbox | D1 + E1 + G2 | CP-D1-2 / CP-E1-4 / CP-G2-3 | mock 注入断言 + e2e（授权） |
| AC-S4-09 记住 → .secrets（0600 + gitignore）+ 同 key 不再问 | A3 + B1 | CP-A3-1/2 / CP-B1-3 | mock + 文件断言 |
| AC-S4-10 UI 面板 + resume_with | F1 | CP-F1-1/2/3 | mock + 手动 |
| AC-S4-11 全链路无凭证明文 | A3 + C1 + E1 + E3 + D2 + G2 | CP-A3-3 / CP-C1-4 / CP-E1-3 / CP-E3-4 / CP-D2-2 / CP-G2-2 | mock + e2e grep |
| AC-S4-12 日志脱敏 | A3 + G1 | CP-A3-3/5 | caplog |
| AC-S4-13 三 interrupt 互不干扰 | G2 | CP-G2-1 | e2e |
| AC-S4-14 重跑幂等：前序副作用恰为 1 | B2 + C2 + E4 | CP-B2-2 / CP-C2-3 / CP-E4-2 | mock + resume 断言（三级递进） |

---

## 6. 风险登记与缓解

R-S4-01~08 沿用架构 §15（等级 / 缓解不复述，仅标注 dev-plan 落点）；R-S4-09~11 为本计划核对代码现状新增。

| 编号 | 风险 | 可能性 | 影响 | 缓解（dev-plan 落点） |
|---|---|---|---|---|
| R-S4-01 | 内嵌子图无法可靠取回真实执行结果（agent 谎报） | 中 | 高 | E1 收集器（工具真跑结果 append）+ CP-E2-3/CP-E3-3 谎报拦截断言；兜底 ToolMessage 解析按 B2/E4 实证启用 |
| R-S4-02 | interrupt#3 resume 重放前序副作用工具 | 中 | 高 | **B2→C2→E4 三级递进验证 + 软前置门**；docstring 单独一轮纪律；write 覆盖幂等 / clone 已存在跳过 / prepare_venv reuse 幂等 |
| R-S4-03 | 训练中途 interrupt#3 → resume 重跑训练 | 中 | 中 | E2 prompt 纪律"问在跑重活之前"；MVP 接受边界代价，G3 handoff 列已知限制 |
| R-S4-04 | 预算错扣 / 漏扣 | 中 | 中 | 落点 B 唯一扣减点 + CP-E3-1 四断言（含 guard 重入零扣） |
| R-S4-05 | 敏感值泄漏 state/logs/report/checkpoint | 中 | 高 | A3 mask 地基 + C1/E1 工具源头 mask + E3 收尾 mask + CP-G2-2 哨兵 grep 全链路 |
| R-S4-06 | .secrets 误提交 | 低 | 中 | workspace/ 已在 gitignore（实证）+ CP-A3-2 check-ignore 断言 |
| R-S4-07 | credential 关键字漏判 | 中 | 中 | 8 关键字先序判定 + 兜底 MAX_FIX_LOOP_COUNT 拦截 + agent 主动问是首选路径（E2 prompt） |
| R-S4-08 | GIT_TERMINAL_PROMPT=0 未注入 → clone 挂起 | 中 | 中 | A3 `build_credential_env` 无条件带 + CP-A3-4/CP-E1-4 断言 |
| **R-S4-09**（新） | execution 是**首个裸 `create_react_subgraph` 消费者**（planning 实经 wrapper），wrapper 内建装配（LLM 注入 / 消息纪律 / rounds 口径）手工复刻漏项 | 中 | 高 | E2 装配清单表逐项对照 react_base.py L815-866 + CP-E2-1 逐项断言；缺 `_llm` 抛错路径显式测试 |
| **R-S4-10**（新） | 收集器（闭包列表）在 interrupt#3 resume 重跑时随节点函数重建，若子图从 checkpoint 恢复不重放工具则收集器丢前序结果 | 中 | 高 | B2 断言点 1/3 先拿机理实证；E4 CP-E4-2 execution 场景复验；异常则启用 E1 兜底通道（ToolMessage 解析回填，BUG-S1-03 范式） |
| **R-S4-11**（新，低） | 架构文档行号漂移（热修复后）导致按旧行号改错位置 | 低 | 低 | §7.1 漂移清单钉死；各任务规格已用当日实证行号 |
| R-PC | coding/execution prompt 变更破坏 cache 字节幂等 | 低 | 中 | 稳定前缀纪律（CP-C2-2 / CP-E2-1）+ G3 命中率回归 ≥ sp3 基线 ×0.95 |

---

## 7. 与既有代码的冲突 / 迁移点（含文档-代码不一致校准）

### 7.1 架构文档行号漂移清单（热修复后，以下为 2026-07-02 实证行号）

| 符号 | 架构引用 | 实际（本计划一律以此为准） |
|---|---|---|
| `sandbox/local_venv.py::_run_subprocess` | L304 | **L330**（HOTFIX-2 加入 `_build_sandbox_env` 区段后移） |
| `sandbox/local_venv.py::prepare_venv` | L444 | **L488**（确认无 extra_env 形参，缺口属实） |
| `sandbox/local_venv.py::run_in_venv` | L658 | **L696**（extra_env 形参在，属实） |
| `core/react_base.py` wrapper 预算扣减 | L889-894 | **L894-898**（HOTFIX-1 后；语义不变） |

### 7.2 架构 / PRD 与代码现状的实质差异（4 处，处置已定）

1. **`_run_subprocess` env 构造已非全量继承**：架构 §9.3 引 `env = {**os.environ, **(extra_env or {})}`——HOTFIX-2 后实际为 `_build_sandbox_env(extra_env)` 白名单收口（L126-141）。**影响正向**：extra_env 已是凭证注入唯一显式口，S4-06 直接对接；但连带产生 D2（PIP_* 前缀白名单备忘闭环）。
2. **PRD §6.2 "自动走 wrapper 扣减"表述失效**：仅裸 wrapper 方案成立；架构 §4.2 已修正为编排层显式扣减（落点 B），本计划按架构执行，PRD 该句不作为实现依据。
3. **架构 §3.4 "与 planning 的内嵌子图调用同构"机制层不精确**：planning 实为 `_make_react_wrapper` 产物在节点内调用（planning.py L614/L736，自动扣减 budget）；execution 按架构 §4.2 用**裸** `create_react_subgraph`（不扣减，编排层显式扣）——"同构"仅在"手写节点 + 内嵌子图 + 节点内 interrupt"形态层成立。设计意图（唯一扣减点）自洽且本计划遵循，代价（装配手工复刻）由 E2 装配清单 + R-S4-09 承接。**不需架构师返工**，属表述精度问题，已在 E2 规格钉死。
4. **`core/errors.py` 实际零改动**：架构 §1.1 改动表把它列为"改动"，但其说明列与 §9.2 均确认 `CREDENTIAL_REQUIRED` 是 execution 本地 Enum（execution.py L87 现有 `ErrorCategory` 就地扩展）、关键字表也是 execution 本地。本计划按零改动处理（§9 交付物清单归入零改动行）。

### 7.3 迁移点（sp3 → sp4 内部结构调整）

| 迁移点 | 处置 |
|---|---|
| `_run_step_subcommands`（execution.py L595）确定性循环 | 调用方消失（步骤 2 换子图）；其子能力（拆分/改写/cd/glob）由 E1 工具内部继续消费——函数本体删除或降级为私有实现，E3 落地时 grep 测试引用后定，记录进 E4 适配清单 |
| sp3 c3 系列测试 mock 落点 | patch 点从 `prepare_venv`/`run_in_venv` 迁到 `_run_execution_agent`（部分用例）；**只换落点不弱化断言**，E4 逐条记录 |
| `_map_execution_result` 签名 | 追加 `react_rounds_used=0` 默认参（向后兼容，既有 4 处调用零改动可过） |
| `_dev_loop_llm_calls` 语义 | 收窄为"execution 侧累计"（落点 B）；state 字段注释同步微调（A2 顺带），数值行为对 sp3 用例向后兼容（coding 本就不写它） |
| coding `_get_coding_tools` | 5 → 7 工具；既有 5 工具工厂调用零改动 |
| `.gitignore` | 零改动（`workspace/` 已覆盖 `.secrets` 默认落点，CP-A3-2 断言即可） |
| `core/graph.py` / `core/react_base.py` / `core/checkpointer.py` / `core/nodes/planning.py` / `core/nodes/reporting.py` | **零改动**（架构 §13 钉死；G1 以 git diff + 结构测试双证）**[勘误 2026-07-05] `core/react_base.py` 已由 BUG-S4-B1-01 修复（commit ae09733，GraphBubbleUp 直通、8 行纯追加）豁免零改动承诺；G1 的 git diff 基线以 ae09733 后状态为准，严禁为对齐本表述还原该修复（还原即复活 interrupt#3 永不暂停 bug）** |

---

## 8. 已知 bug 模式规避自查（沿用 sp1~sp3 治理，架构 §14 逐条映射）

1. **ToolMessage JSON 序列化（BUG-S1-02）**：C1 / E1 三个新工具返回 dict 一律 `json.dumps(ensure_ascii=False, sort_keys=True, default=str)`，禁 `str(dict)`（CP-C1-1 / CP-E1-1）；`request_user_input` 返回纯 str 豁免（架构 §2.1 注记）。
2. **工具历史回填 + 失败 ToolMessage 过滤（BUG-S1-03）**：E1 兜底通道（若启用）扫 ToolMessage 必须过滤失败记录、只认成功结果（CP-E4-2）；coding `_map_coding_result` 3 参签名不动（CP-C2-1）。
3. **失败非静默 WARNING**：secrets 读损坏（CP-A3-2）/ 非法 resume（CP-B1-2）/ run_command 越界与解析失败（CP-C1-2）/ 子图异常降级（CP-E2-4）/ credential 命中（E3 沿用 `_map_execution_result` WARNING 范式）全部有 caplog 断言。
4. **新增 state 通道显式声明 + 默认值**：`pending_user_input` / `collected_inputs`（CP-A2-1），防 LangGraph 静默丢弃。
5. **Prompt Cache 字节级幂等（方案 A）**：三个新工具 docstring 零动态变量（CP-B1-5）；coding 新段进稳定前缀（CP-C2-2）；execution 新 prompt 建字节级基线（CP-E2-1）；命中率守门 G3。
6. **回归连跑足够次数**：interrupt / 重跑幂等类连跑 3 次全绿（CP-B2-3 / CP-E4-3 / CP-G2-1），实测复现率 10%~50% 类升 5 次且含全量回归。
7. **`__init__.py` export 遮蔽子模块**：新增 `core/tools/interaction_tools.py` / `run_command_tool.py` 若在 `core/tools/__init__.py` 显式 export，测试访问模块属性用 `importlib.import_module`（G1 注意）。
8. **must-fix-1 红线**：三 List 字段零 reducer（CP-A2-2 grep 双证）；所有写入单点 read-modify-write。

---

## 9. 交付物清单

| 类型 | 文件 | 任务 |
|---|---|---|
| 新增 | `core/secrets_store.py` | A3 |
| 新增 | `core/tools/interaction_tools.py` | B1 |
| 新增 | `core/tools/run_command_tool.py` | C1 |
| 改动 | `config.py`（纯追加 3 常量） | A1 |
| 改动 | `core/state.py`（追加 2 通道，绝不碰 List/reducer） | A2 |
| 改动 | `core/nodes/coding.py`（_get_coding_tools 7 工具 + prompt 稳定前缀段） | C2 |
| 改动 | `sandbox/local_venv.py`（仅 prepare_venv 补 extra_env 透传；白名单是否调整以 D2 结论为准） | D1 / D2 |
| 改动 | `core/nodes/execution.py`（工具化 + `_run_execution_agent` + 主函数步骤 1+2 替换 + 扣减扩展 + credential 分类 + logs 脱敏） | E1 / E2 / E3 |
| 改动 | `ui/pages/execution_monitor.py` + `app.py`（user_input_request 面板 + 路由分支） | F1 |
| 新增 | `tests/test_sprint4_b2_interrupt3_idempotency.py` / `tests/test_sprint4_*`（各任务 CP）/ `tests/test_sprint4_e2e.py` | B2 / 各任务 / G2 |
| 新增 | `docs/sprint4/test-reports/*`（B2 幂等实证 / e2e / Prompt Cache） | B2 / G2 / G3 |
| 新增 | `docs/sprint4/handoff-to-test-engineer.md`（含 D2 备忘闭环结论） | G3 |
| **零改动** | `core/graph.py` / `core/react_base.py` / `core/checkpointer.py` / `core/errors.py` / `core/nodes/planning.py` / `core/nodes/reporting.py` / `core/llm_client.py` / sp1/sp2 既有节点与工具 **[勘误 2026-07-05] `core/react_base.py` 已由 BUG-S4-B1-01 修复（commit ae09733，GraphBubbleUp 直通、8 行纯追加）豁免零改动承诺；G1 的 git diff 基线以 ae09733 后状态为准，严禁为对齐本表述还原该修复（还原即复活 interrupt#3 永不暂停 bug）** | — |

---

**文档结束**

*本开发计划严格对齐 `docs/sprint4/prd.md` v2 与 `docs/sprint4/architecture.md` v1.0（Q-A1/Q-A2/Q-B1/Q-E1 四结论 + §13 改动清单 + §14 治理硬约束）。所有常量 / 签名 / 行号以 2026-07-02 代码实证为准（两热修复后现状，漂移清单见 §7.1，与架构文档的 4 处实质差异及处置见 §7.2）。预算扣减以 execution 编排层单点回写为准（execution 不经 `_make_react_wrapper`，react_base.py L894-898 仅对 coding 等 wrapper 节点触发）；interrupt#2 重跑幂等命门以 `_has_committed_result_for_round` guard + `await_dev_loop_interrupt` self-loop 为准（sp4 逐字保留）；interrupt#3 重跑幂等以 B2→C2→E4 三级递进实证为准（Maria 已定不单独 spike）；敏感值旁路 state 以 `core/secrets_store.py` + 架构 §6.5 filter 落点表为准；主图骨架以 `core/graph.py::build_graph` 零改动为准。*
