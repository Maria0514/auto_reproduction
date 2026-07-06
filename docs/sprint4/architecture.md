# Sprint 4 核心架构设计文档

**文档版本**：v1.0
**日期**：2026-07-02
**作者**：架构师代理
**对应 PRD**：`docs/sprint4/prd.md` v2（路线丙）
**前置文档**：`docs/sprint3/architecture.md`（sp3 已踩通机制的权威来源）、`docs/sprint3/dev-loop-compatibility-matrix.md`（服务路线甲的兼容性评估，本文 §6 说明如何绕开）
**体例参照**：`docs/sprint3/architecture.md`

> 本文档把 PRD §11 的 4 个开放问题（Q-A1 execution ReAct 化编排安置 / Q-A2 预算对账 / Q-B1 run_command 边界 / Q-E1 .secrets 存储）全部落地为可执行的架构设计，并覆盖通用交互能力（interrupt#3 `user_input_request`）、工具内 interrupt 重跑幂等、凭证闭环、治理范式硬约束。所有设计**以代码实证为准**（`core/graph.py` / `core/nodes/execution.py` / `core/nodes/coding.py` / `core/react_base.py` / `core/nodes/planning.py` / `sandbox/local_venv.py` / `config.py` / `core/errors.py` / `core/state.py`），不臆造已有机制。全局技术架构文档已在本轮更新为路线丙口径，本文是它的细化落地，不与之冲突。

> **常量取值校准（2026-07-02 代码实证）**：`config.py` 现值为 `MAX_TOTAL_LLM_CALLS=120` / `MAX_FIX_LOOP_COUNT=10` / `MAX_DEV_LOOP_LLM_CALLS=60` / `DEV_LOOP_MIN_CALLS_PER_ROUND=2` / `REACT_MAX_ROUNDS_CODING=12`。PRD §Q-A2 引用的 `MAX_TOTAL_LLM_CALLS=50` 是 sp3 立项快照旧值，**当前实际是 120，60 < 120 强约束成立，PRD 提及的「50<60 张力」已在 sp3 落地时通过放大到 120 消解**。本文一律以 120 为准。

---

## 目录

1. [Sprint 4 架构总览](#1-sprint-4-架构总览)
2. [模块详细设计](#2-模块详细设计)
3. [Q-A1：execution ReAct 化的编排安置](#3-q-a1execution-react-化的编排安置)
4. [Q-A2：预算对账（计数职责划分表）](#4-q-a2预算对账计数职责划分表)
5. [Q-B1：run_command 边界](#5-q-b1run_command-边界)
6. [Q-E1：.secrets 存储](#6-q-e1secrets-存储)
7. [通用交互能力（interrupt#3 user_input_request）](#7-通用交互能力interrupt3-user_input_request)
8. [工具内 interrupt 的重跑幂等（Q-C1）](#8-工具内-interrupt-的重跑幂等q-c1)
9. [凭证闭环](#9-凭证闭环)
10. [数据流图](#10-数据流图)
11. [关键设计决策](#11-关键设计决策)
12. [state / config 字段处置表](#12-state--config-字段处置表)
13. [对既有代码的改动点清单](#13-对既有代码的改动点清单)
14. [治理范式硬约束](#14-治理范式硬约束)
15. [风险与缓解](#15-风险与缓解)
16. [测试策略建议](#16-测试策略建议)
17. [给全栈开发的任务拆分建议](#17-给全栈开发的任务拆分建议)

---

## 1. Sprint 4 架构总览

### 1.1 涉及的新模块与扩展模块

| 类型 | 路径 | 来源 | 说明 |
|---|---|---|---|
| **新增** | `core/tools/interaction_tools.py` | S4-05 | 单一通用交互工具 `request_user_input`（工具内 interrupt#3） |
| **新增** | `core/tools/run_command_tool.py` | S4-01 | coding 的 `run_command` 轻量验证工具（复用 sandbox 底座） |
| **新增** | `core/secrets_store.py` | S4-07 | `.secrets` 读写（0600 + gitignore，明文 MVP）+ 脱敏 filter |
| **改动** | `core/nodes/coding.py` | S4-01/02 | `_get_coding_tools` 增挂 `run_command` + `request_user_input`（wrapper 形态不变） |
| **改动** | `core/nodes/execution.py` | S4-03/04/06 | 手写编排保留 + 第 1/2 步替换为内嵌 ReAct execution 子图 + sandbox 工具化 + credential 分类 |
| **改动** | `sandbox/local_venv.py` | S4-06 | `prepare_venv` 补 `extra_env` 形参并透传 pip 子进程 |
| **零改动** | `core/errors.py` | S4-06 | `ErrorCategory` 无需改（细分类是 execution 本地 Enum）；新增凭证关键字表（execution 本地） |
| **改动** | `core/state.py` | S4-10 | 新增 `pending_user_input` / `collected_inputs` 两个通道（显式声明 + 默认值） |
| **改动** | `config.py` | S4-10 | 新增 `REACT_MAX_ROUNDS_EXECUTION` + `RUN_COMMAND_TIMEOUT` + `.secrets` 路径常量 |
| **改动** | `app.py` | S4-09 | `interrupt_kind()` 已按 kind 分发，新增 `"user_input_request"` 分支消费 |
| **改动** | `ui/pages/execution_monitor.py` | S4-09 | 新增 `user_input_request` 交互面板（单输入框 + 敏感 password + 记住勾选） |

> **硬约束（贯穿全文）**：主图保持 **严格 7 节点 DAG**（`paper_intake / paper_analysis / resource_scout / planning / coding / execution / reporting`），沿用 sp3 AC-S3-10。execution 从"手写七步"改为"手写编排 + 内嵌 ReAct 子图"，**节点名不变、节点数不变、`build_graph` 边结构不变**。这与 sp3 把 coding 从占位换成 ReAct wrapper 时"节点名不变"完全同范式。

### 1.2 模块依赖关系与初始化顺序

```
config.py（+REACT_MAX_ROUNDS_EXECUTION / +RUN_COMMAND_TIMEOUT / +SECRETS_FILE_NAME）
   │
core/state.py（+pending_user_input / +collected_inputs 两通道，绝不加 reducer）
   │
core/secrets_store.py（依赖 config.WORKSPACE_DIR；读写 .secrets + 脱敏 filter）
   │        │
   │        └─→ core/tools/interaction_tools.py（request_user_input：interrupt#3 + 记住时落 .secrets）
   │
sandbox/local_venv.py（prepare_venv 补 extra_env；run_in_venv/_run_subprocess 已支持）
   │        │
   │        ├─→ core/tools/run_command_tool.py（coding 轻量验证，复用 _run_subprocess 护栏）
   │        └─→ core/nodes/execution.py（sandbox 工具化 + 内嵌 ReAct 子图 + 保留手写编排）
   │
core/react_base.py（create_react_subgraph / _make_react_wrapper 唯一预算扣减点，零改动）
   │        │
   │        └─→ core/nodes/coding.py（wrapper 不变，仅 get_tools 增挂 2 工具）
   │
core/graph.py（零改动：节点名 execution 不变，边结构不变；self-loop await_dev_loop_interrupt 保留）
   │
app.py + ui/pages/execution_monitor.py（消费 interrupt_kind="user_input_request"）
```

> **[勘误 2026-07-05]** 上图 `core/react_base.py` "零改动 / 唯一预算扣减点"表述已被豁免：BUG-S4-B1-01 修复（commit ae09733，GraphBubbleUp 直通、8 行纯追加）落于该文件，预算扣减点本身未动；详见 dev-plan §7.3 勘误注记，严禁为对齐本图还原该修复。

**关键路径**：`config.py` + `state.py` → `secrets_store.py` → `interaction_tools.py`；`sandbox/local_venv.py`（补 extra_env）→ `run_command_tool.py` + `execution.py`。coding 与 graph.py 改动最小。

### 1.3 与 sp3 形态的映射（本次哪些变、哪些不变）

```
七步流程节点位          sp3 形态                          sp4 形态
────────────────────────────────────────────────────────────────────────────────
paper_intake     ReAct wrapper                       零改动
paper_analysis   ReAct wrapper                       零改动
resource_scout   ReAct wrapper                       零改动
planning         手写复合 + interrupt#1（编排范式基准）  零改动
coding           ReAct wrapper（5 工具）              ReAct wrapper（7 工具：+run_command +request_user_input），wrapper 形态不变
execution        手写复合七步 + interrupt#2 + self-loop  手写编排（保留 interrupt#2 + self-loop）+ 第1/2步内嵌 ReAct 子图（sandbox 工具化 + request_user_input）
reporting        纯函数三形态                          零改动
```

**核心洞见**：execution 从"手写确定性七步"升级为 agent，**不是**换成裸 `_make_react_wrapper`（那会把 sp3 已踩通的 commit 边界 self-loop 重跑幂等契约冲掉）。而是"手写编排层保留 sp3 的第 3/5/6/7 步（分类/判定/map_result/interrupt#2+self-loop），把第 1/2 步（准备环境 + 执行 steps）从确定性循环替换为一个内嵌 ReAct execution 子图"——与 planning "手写复合 + 内嵌 ReAct 子图 + interrupt#1" 完全同范式（见 §3）。

---

## 2. 模块详细设计

### 2.1 `core/tools/interaction_tools.py`（S4-05，通用交互工具）

**职责**：提供**唯一**一个 LangChain `@tool` `request_user_input`，coding / execution 两 agent 共用。agent 缺任何信息时调它，工具体内 `interrupt(payload)` 暂停主图，UI 收集后 `Command(resume=值)` 恢复，工具返回值作为 ToolMessage 喂回 agent。**极简三字段，不做 input_type 枚举**（Maria 硬约束）。

```python
# core/tools/interaction_tools.py
from langchain_core.tools import tool
from langgraph.types import interrupt
from core.secrets_store import lookup_secret, remember_secret

INTERRUPT_KIND_USER_INPUT: str = "user_input_request"

@tool
def request_user_input(question: str, is_sensitive: bool = False,
                       purpose_key: str = "") -> str:
    """当缺少继续任务所需的信息（凭证 / 参数 / 决策 / 路径）时，向用户索要一条信息。

    仅在确实无法从已有上下文推断、且信息缺失会阻塞任务时调用。一次只问一个信息项。
    - question: 给用户看的问题文本（中文叙述，URL/包名等事实层保留英文）。
    - is_sensitive: 凭证/密钥类置 True（UI 用 password 输入、全程脱敏、可「记住」）。
    - purpose_key: 信息项稳定标识（如 "git_credential:github.com" / "hf_token"），
      用作 .secrets 的 key + 去重（同 key 命中已存则直接返回不再打断用户）。
    返回：用户输入的字符串值（敏感值不进 state / checkpoint）。
    """
    # 1) 去重 / 跨任务复用：purpose_key 命中 .secrets 或本任务 collected_inputs → 直接返回，不 interrupt。
    #    （命中判定在工具内做一次只读查找；敏感值查 .secrets，非敏感查后由编排注入的上下文……
    #     实现见 §7.3 去重落点：优先 .secrets，其次本任务已收集。）
    if purpose_key:
        cached = lookup_secret(purpose_key)  # 只读 .secrets（敏感）；命中返回明文值
        if cached is not None:
            return cached  # 不 interrupt，不重复问

    # 2) interrupt#3：payload 带 interrupt_kind 供 UI/GraphController 分发。
    resume = interrupt({
        "interrupt_kind": INTERRUPT_KIND_USER_INPUT,
        "question": question,
        "is_sensitive": bool(is_sensitive),
        "purpose_key": purpose_key or None,
    })
    # 3) resume 契约：Command(resume={"value": "...", "remember": bool})。
    if not isinstance(resume, dict) or "value" not in resume:
        # 防御兜底：非法 resume → 返回空串，让 agent 自行决定降级（无交互前端 Q-F2 语义）。
        return ""
    value = str(resume.get("value") or "")
    remember = bool(resume.get("remember"))
    # 4) 「记住」→ 落 .secrets（0600）；敏感值绝不返回给 state，只作为 ToolMessage 内容回 agent。
    if remember and purpose_key:
        remember_secret(purpose_key, value, is_sensitive=bool(is_sensitive))
    return value
```

> **序列化治理（BUG-S1-02）**：`request_user_input` 返回的是**纯字符串**（用户输入值），不是 dict，直接作为 ToolMessage 内容，无需 `json.dumps`。若未来返回结构化则必须走 `json.dumps(ensure_ascii=False, sort_keys=True, default=str)`。**脱敏关键**：敏感值作为 ToolMessage 内容进入 ReAct 子图私有 messages，但**不进 GlobalState**（子图 messages 不回灌 GlobalState，见 §9.3 脱敏落点）。

> **Prompt Cache 字节级幂等**：`request_user_input` 的 docstring 是工具 schema 的一部分，作为稳定前缀参与 Prompt Cache。docstring 内**绝不含**任何论文级/任务级动态变量（当前设计已满足）。coding/execution 挂该工具时，工具 schema 在多任务间字节级一致。

### 2.2 `core/tools/run_command_tool.py`（S4-01，coding 轻量验证）

**职责**：coding agent 的"跑一下"能力，闭合"写→跑→看→改"。**复用 `sandbox/local_venv.py::_run_subprocess` 的全部护栏**（禁 shell=True、进程组隔离、超时杀子树、输出字节截断、cwd 校验），不新造执行通道。见 §5（Q-B1）的边界细化。

```python
# core/tools/run_command_tool.py
from langchain_core.tools import tool
from config import RUN_COMMAND_TIMEOUT, SANDBOX_OUTPUT_MAX_BYTES
from sandbox.local_venv import _run_subprocess, _require_within_workspace

def make_run_command_tool(base_dir: str, extra_env: dict | None = None):
    """工厂：绑定 code_output_dir 作 cwd + 已收集凭证 extra_env。coding 传 base_dir=code_dir。"""
    @tool
    def run_command(command: str) -> str:
        """在代码目录下运行一条【轻量验证】命令（如 python -c "import x" / python -m py_compile x.py）。

        仅用于 smoke 级自查：import 能否通过、脚本能否启动、语法是否正确。
        禁止用于完整训练 / 评估 / 下载大数据集（那是 execution 阶段的职责，且本工具超时很短）。
        返回：JSON {exit_code, stdout_tail, stderr_tail, timed_out, truncated}。
        """
        import shlex, json
        try:
            argv = shlex.split(command)
        except ValueError as exc:
            return json.dumps({"error": f"命令解析失败: {exc}", "exit_code": -1},
                              ensure_ascii=False, sort_keys=True, default=str)
        if not argv:
            return json.dumps({"error": "空命令", "exit_code": -1},
                              ensure_ascii=False, sort_keys=True, default=str)
        # 护栏：cwd 锚定 base_dir 并校验在 WORKSPACE_DIR 下（越界抛 → 捕获转结构化错误，不炸子图）。
        try:
            _require_within_workspace(base_dir, label="run_command cwd")
        except Exception as exc:  # SandboxCreationError 等
            return json.dumps({"error": f"工作目录越界: {exc}", "exit_code": -1},
                              ensure_ascii=False, sort_keys=True, default=str)
        rr = _run_subprocess(argv, cwd=base_dir, timeout=RUN_COMMAND_TIMEOUT,
                             output_max_bytes=SANDBOX_OUTPUT_MAX_BYTES, extra_env=extra_env)
        return json.dumps({
            "exit_code": rr.exit_code,
            "stdout_tail": rr.stdout, "stderr_tail": rr.stderr,
            "timed_out": rr.timed_out, "truncated": rr.output_truncated,
        }, ensure_ascii=False, sort_keys=True, default=str)
    return run_command
```

> **序列化 / 失败非静默**：返回值一律 `json.dumps(...)`（BUG-S1-02）；解析失败 / 越界 / 启动失败均转结构化错误返回（`_run_subprocess` 本身 OSError 兜底转 `exit_code=-1`，不逃逸），并在越界分支打 WARNING。

### 2.3 `core/secrets_store.py`（S4-07，.secrets 读写 + 脱敏）

见 §6（Q-E1）完整设计。此处仅列职责：`lookup_secret(purpose_key) -> Optional[str]`（只读 .secrets）、`remember_secret(purpose_key, value, is_sensitive)`（0600 落盘）、`load_all_secrets() -> Dict[str, str]`（execution/coding 编排层启动时读入内存注入 extra_env）、`mask_value(text, secrets) -> str`（日志/报告脱敏 filter）。（**勘误 2026-07-05：`mask_value` 实际为单参 `mask_value(text)`，敏感值内部自取，见 §9.4 勘误**）

### 2.4 `core/nodes/coding.py` 改动（S4-01/02）

**wrapper 形态零改动**（仍 `_make_react_wrapper(...)`），仅 `_get_coding_tools` 增挂两个工具：

```python
def _get_coding_tools(state: GlobalState) -> List[Any]:
    code_dir = _resolve_code_output_dir(state)
    secrets = load_all_secrets()  # 读 .secrets（敏感）→ extra_env（凭证注入 smoke）
    return [
        make_write_code_file_tool(base_dir=code_dir),
        make_read_code_file_tool(),
        make_list_dir_tool(),
        read_section_tool(),
        web_search_tool(),
        make_run_command_tool(base_dir=code_dir, extra_env=_git_env(secrets)),  # 新增（S4-01）
        request_user_input,                                                     # 新增（S4-02）
    ]
```

> `_git_env(secrets)` 把已收集凭证 + `GIT_TERMINAL_PROMPT=0` 组装成 extra_env（见 §9）。**coding system prompt 需补一段**：说明有 `run_command`（仅轻量验证）与 `request_user_input`（缺信息时问用户）两个新工具及其边界——这段说明**放入 `_CODING_SYSTEM_PROMPT_BODY` 稳定前缀**（不含动态变量，不破坏 Prompt Cache 方案 A）。

### 2.5 `core/nodes/execution.py` 改动（S4-03/04/06）

核心改造见 §3（Q-A1）。此处概述：保留全部手写编排（`_classify_execution` / `_parse_metrics` / `_build_execution_result` / `_map_execution_result` / `_maybe_interrupt_or_return` / `_has_committed_result_for_round` / `already_committed` guard / `await_dev_loop_interrupt` self-loop），**只替换 execution() 主函数的"步骤 1+2"**（`prepare_venv` + `_run_step_subcommands` 确定性循环）为"调用内嵌 ReAct execution 子图"。新增 execution 本地凭证关键字表 + `CREDENTIAL_REQUIRED` 分类（§9.2）。

---

## 3. Q-A1：execution ReAct 化的编排安置

### 3.1 结论

**采纳 PM 强烈倾向的"薄手写编排 + 内嵌 ReAct 子图"（与 planning 完全同范式）。** interrupt#2 与 self-loop **落在编排层（execution 主函数体内）**，**不下沉到 ReAct 子图**；修复循环边界字段（`_dev_loop_route` / `fix_loop_count` / `fix_loop_history` / `execution_result`）**全部保留在编排层**，安置位置与 sp3 逐字不变。ReAct 子图只承担"步骤 1+2 的自适应执行"，产出 sandbox 运行结果原料，交回编排层做确定性收尾（分类/判定/map）。

### 3.2 决策依据

1. **重跑幂等命门必须留在主图节点级**：sp3 已踩通的 interrupt#2 重跑幂等（副作用恰为 1）依赖 `_has_committed_result_for_round` guard + `_dev_loop_route="await_dev_loop_interrupt"` 落盘边界 + `_route_after_execution` 的 `"execution": "execution"` self-loop（`core/graph.py` L144-145 / `execution.py` L1006-1044 实证）。这套机制是**主图节点级**的（依赖主图 self-loop 边），若把 execution 换成裸 `_make_react_wrapper`，wrapper 的标准形态是"子图 invoke → map_result → return"，**无处安放 commit 边界 self-loop**，会冲掉这条命门 → interrupt#2 永不触发或副作用翻倍。故 execution **不能**用裸 wrapper。

2. **planning 已验证同范式**：`planning.py` 就是"手写复合节点：内部跑 ReAct 子图（S2-13 抓取用户仓库）→ 构造 payload → 函数体内 `interrupt()` → 5 类决策路由"（L764-829 实证）。execution 沿用：手写编排 → 内嵌 ReAct 子图跑执行 → 确定性收尾 → 修复边界 → interrupt#2/self-loop。**零新范式，纯复用**。

3. **确定性收尾不交给 agent**：`_classify_execution` / `_build_execution_result`（B 档 success = exit 全 0 且 ≥1 指标）保持确定性，**避免 agent 谎报成功**（PRD §4.2.2 明确要求）。agent 只决策"跑哪些命令、看结果、要不要问用户"。口径注记："exit 全 0"指 **effective runs**（同命令 argv 精确匹配以最后一次为准，`_effective_runs`，L-E4-01 裁决 2026-07-04；§9.1 重试→成功叙事由此闭环）；logs 聚合与 runtime 仍用全量序列，失败证据不丢。

### 3.3 分层落点表

| 层 | 归属 | sp4 内容 | 相对 sp3 |
|---|---|---|---|
| **主图编排层**（`execution()` 主函数体） | 手写 | ① `_has_committed_result_for_round` guard（复用结果、跳过重跑）；② work_dir 缺失降级；③ **调用内嵌 ReAct 子图跑执行（替换原确定性 prepare+run 循环）**；④ `_classify_execution` / `_parse_metrics` / `_build_execution_result` / `_map_execution_result`；⑤ `_maybe_interrupt_or_return`（含 `already_committed`）；⑥ `await_dev_loop_interrupt` self-loop + interrupt#2 | 仅 ③ 变；①②④⑤⑥ 逐字保留 |
| **内嵌 ReAct 子图**（execution agent） | `create_react_subgraph` | 自主决策"准备环境（调 prepare_environment 工具）→ 跑 execution_steps（调 run_in_sandbox 工具）→ 看结果 → 缺信息时调 request_user_input → 收尾产出运行结果原料"；`max_rounds=REACT_MAX_ROUNDS_EXECUTION` | 全新（替换确定性七步的 1+2 步） |
| **sandbox 工具层**（子图工具） | LangChain `@tool` | `prepare_environment`（包 `prepare_venv`）/ `run_in_sandbox`（包 `run_in_venv`）/ `request_user_input`；`_step_to_command` / `_rewrite_interpreter` / `_resolve_cd` / `_expand_globs` 等确定性解析改写保留为工具内部实现 | prepare_venv/run_in_venv 包成工具；解析改写下沉工具内 |

### 3.4 execution() 主函数改造骨架（保留 sp3 结构，只换步骤 1+2）

```python
def execution(state: GlobalState) -> dict:
    work_dir = state.get("code_output_dir")

    # 【逐字保留 sp3】interrupt#2 重跑幂等 guard：本回合结果已落盘 → 跳过执行，直接进 interrupt 判定。
    if _has_committed_result_for_round(state):
        prev = state.get("execution_result") or {}
        feedback = _feedback_from_committed_result(prev)
        updates = {"execution_result": prev, "current_step": NODE_NAME}
        return _maybe_interrupt_or_return(updates, prev, feedback, state, already_committed=True)

    # 【逐字保留 sp3】work_dir 缺失 → 降级。
    if not work_dir:
        ...  # 同 sp3

    plan = state.get("reproduction_plan") or {}

    # 【sp4 变更：步骤 1+2】原"prepare_venv + _run_step_subcommands 确定性循环"替换为内嵌 ReAct 子图。
    #   子图自主编排 prepare_environment / run_in_sandbox / request_user_input；
    #   产出结构化运行原料（prep 结果 + run_results 列表 + 子图 round 数 + 子图内 LLM 调用累计）。
    exec_agent_out = _run_execution_agent(state, work_dir, plan)
    prep = exec_agent_out.prep            # SandboxPrepareResult（工具执行的真实结果，非 agent 自述）
    run_results = exec_agent_out.run_results  # List[SandboxRunResult]
    rounds_used = exec_agent_out.rounds_used  # 子图实际 round（喂给预算扣减，§4）
    dev_calls_delta = exec_agent_out.llm_calls  # 子图内 LLM 调用数（=rounds，累加 _dev_loop_llm_calls）

    # 【逐字保留 sp3】步骤 3-6：确定性收尾。
    feedback = _classify_execution(prep, run_results)
    metrics, metric_llm_calls = _parse_metrics(run_results, plan, state)
    exec_result = _build_execution_result(prep, run_results, feedback, metrics, work_dir)
    updates = _map_execution_result(exec_result, feedback, state,
                                    llm_calls_used=metric_llm_calls,
                                    react_rounds_used=rounds_used,      # 新增（§4 预算对账）
                                    dev_calls_delta=dev_calls_delta)     # 新增（§4）

    # 【逐字保留 sp3】步骤 7：修复循环边界 + interrupt#2（首次进入 already_committed=False）。
    return _maybe_interrupt_or_return(updates, exec_result, feedback, state, already_committed=False)
```

> **（实现注记 2026-07-05）** 上方伪代码中的 `dev_calls_delta` 形参在实现中不存在——已并入 `react_rounds_used`，因 E2 契约 `llm_calls ≡ rounds` 恒等（`_map_execution_result` 实际仅收 `llm_calls_used` + `react_rounds_used` 两参）；见 e3-e4 验收报告低危注记②。

> **关键**：ReAct 子图产出的是**工具执行的真实 `SandboxRunResult`**（工具体内真跑 sandbox 落回的结构化结果），**不是** agent 自然语言自述。确定性收尾读的是真实 exit_code/stderr，agent 无法伪造成功。`_run_execution_agent` 内部用 `create_react_subgraph(...).invoke(...)`（与 planning 的内嵌子图调用同构），并从工具执行轨迹（ToolMessage）中收集 `SandboxRunResult`（工具返回结构化 → 编排层解析回 dataclass），或让工具把结果写入子图 context 由收尾取回（二选一，推荐后者：工具把每次 run 结果 append 到子图私有 context 列表，`_run_execution_agent` invoke 后从 final_state 取出）。

> **interrupt#3（request_user_input）与 interrupt#2（dev_loop_failure）在同一 execution 节点内共存**：request_user_input 的 interrupt 发生在**子图执行期**（步骤 1+2 内，agent 缺信息时）；interrupt#2 发生在**编排层收尾后**（步骤 7）。二者 `interrupt_kind` 不同（`user_input_request` vs `dev_loop_failure`），UI 按 kind 分发。二者重跑幂等语义不同，见 §8。

### 3.5 为什么不把 interrupt#2 放进子图 map_result

子图 map_result 的语义是"子图跑完一次 → 映射结果"，它**没有** self-loop 重入能力（self-loop 是主图边），也无法承载 sp3 的 commit 边界。若 interrupt#2 塞进子图内部，则子图 resume 重跑会把整个 agent loop（含 sandbox 副作用）重放，与 §8 的幂等契约冲突。故 interrupt#2 必须留在编排层，靠主图 self-loop 保证 sandbox 只跑一次。

---

## 4. Q-A2：预算对账（计数职责划分表）

### 4.1 结论

采纳 PM 倾向并给出精确划分：**新增 `REACT_MAX_ROUNDS_EXECUTION` 承担 execution 子图内单次 invoke 的轮次上限；`retry_budget_remaining` 全局账本由编排层按子图实际 round 数单点扣减（不双重扣减）；`_dev_loop_llm_calls` 继续累计跨回合修复循环子预算（子图 round + metrics LLM 抽取）**。三者语义正交，无重叠。

### 4.2 关键：execution 不走裸 wrapper，故 L889-894 不自动触发

sp3 execution 是手写节点，**不经过** `_make_react_wrapper`，故 `react_base.py` L889-894 的自动扣减对 execution **不生效**。sp4 execution 仍是**手写编排**（§3），内嵌子图是 execution 自己 `create_react_subgraph(...).invoke(...)` 调起来的（与 planning 同），**也不经过** `_make_react_wrapper` 的 `_wrapper`。因此：

- **不存在"自动走 L889-894 扣减"** —— PRD §6.2 "execution ReAct 化后自动获得同一扣减点"的表述**仅在裸 wrapper 方案下成立**；本文采用内嵌子图方案，扣减**由编排层显式做**（与 sp3 metrics LLM 抽取的单点回写同机理，扩展为"子图 round + metrics 抽取"两部分）。这消除了"双重扣减"风险的根源——因为根本没有第二个自动扣减点。

### 4.3 计数职责划分表（谁扣 / 在哪扣 / 边界值）

| 计数量 | 语义 | 谁扣 / 累计 | 在哪扣 | 边界值 / 上限 | 双重扣减防护 |
|---|---|---|---|---|---|
| `REACT_MAX_ROUNDS_EXECUTION`（新增 config，建议 =10） | execution 子图**单次 invoke** 内 ReAct 轮次上限 | `create_react_subgraph(max_rounds=...)` 内的 `budget_check_node` | 子图内部（`react_base` budget_check，**不碰全局账本**，实证 L780-787） | 单次执行 ≤10 轮，到顶 force_finish | 与全局账本正交（budget_check 只管子图内 round，不读写 `retry_budget_remaining`） |
| `retry_budget_remaining`（复用，全局账本） | 全任务 LLM 预算余额（初始 `MAX_TOTAL_LLM_CALLS=120`） | **编排层单点扣减**（`_map_execution_result` 新增 `react_rounds_used` + `metric_llm_calls`） | `execution.py::_map_execution_result`，`max(0, prev - react_rounds_used - metric_llm_calls)` | 0 触底 | 编排层是**唯一**扣减点；execution 不经 wrapper，L889-894 不触发；coding 仍由 wrapper 扣（各扣各的节点，不重叠） |
| `_dev_loop_llm_calls`（复用，跨回合子预算累计） | 修复循环**跨回合**累计 LLM 调用（coding rounds + execution 子图 rounds + metrics 抽取） | coding（wrapper 内需补累加）+ execution（编排层累加） | coding wrapper / execution `_map_execution_result` 各自 read-modify-write `+=` | `MAX_DEV_LOOP_LLM_CALLS=60` 触顶 → 视同修复耗尽走 interrupt#2（`_maybe_interrupt_or_return` L956 已实证判定 `dev_calls < MAX_DEV_LOOP_LLM_CALLS`） | 累计量，非扣减量；与 `retry_budget_remaining` 独立（一个是余额、一个是修复循环已用量） |
| `fix_loop_count`（复用） | 已完成的修复**回合**数（coding→execution 一轮 +1） | execution 编排层单点自增 | `_maybe_interrupt_or_return` "回 coding"分支（L958，逐字保留） | `MAX_FIX_LOOP_COUNT=10` | 单点自增，interrupt/降级/成功分支不增（sp3 已保证） |

### 4.4 扣减时序（单次 execution 进入）

```
execution() 首次进入（already_committed=False）:
  1. _run_execution_agent → 内嵌子图 invoke（子图内 budget_check 用 REACT_MAX_ROUNDS_EXECUTION 限轮）
     → 返回 rounds_used（子图实际 round）+ run_results
  2. _parse_metrics → 可能触发 metrics LLM 抽取 → metric_llm_calls（0 或 1）
  3. _map_execution_result 单点扣减（唯一扣减点）:
        retry_budget_remaining -= (rounds_used + metric_llm_calls)   # 全局账本
        _dev_loop_llm_calls    += (rounds_used + metric_llm_calls)   # 修复循环累计
  4. _maybe_interrupt_or_return:
        入口预算门: retry_budget_remaining < DEV_LOOP_MIN_CALLS_PER_ROUND(2) → 降级
        子预算触顶: _dev_loop_llm_calls >= MAX_DEV_LOOP_LLM_CALLS(60) → interrupt#2
        可修复+未触顶: fix_loop_count += 1 → 回 coding

self-loop 重入（already_committed=True，guard 命中跳过子图）:
  不再跑子图 → rounds_used=0、metric_llm_calls=0 → 不重复扣减（预算零消耗，仅做 interrupt 判定）
```

> **自洽性说明（120 vs 60）**：`MAX_DEV_LOOP_LLM_CALLS=60 < MAX_TOTAL_LLM_CALLS=120`（config 实证），修复循环子预算天花板严格小于全局预算。PRD 提及的"50<60 张力"是 sp3 立项旧值快照，sp3 落地已把 `MAX_TOTAL_LLM_CALLS` 放大到 120 消解，本文无此张力。前序 4 节点（intake/analysis/scout/planning + coding 首轮）已消耗部分全局预算，进入修复循环时若 `retry_budget_remaining < 2` 则入口预算门直接降级（不空转），与 sp3 逻辑逐字一致。

### 4.5 coding 侧 `_dev_loop_llm_calls` 累加缺口（须补）

sp3 中 coding 走 wrapper，wrapper 只扣 `retry_budget_remaining`（L892），**不累加 `_dev_loop_llm_calls`**（sp3 只有 execution metrics 抽取时累加）。sp4 要让子预算 `_dev_loop_llm_calls` 精确覆盖"coding rounds + execution rounds + metrics"，需在 coding 的 `_map_coding_result` 内补一句累加（read-modify-write，`+= rounds_used`）。但 `_map_coding_result` 现拿不到 rounds_used（那在 wrapper 内）。**两个落点选一**：

- **落点 A（推荐）**：`_make_react_wrapper._wrapper` 在扣 `retry_budget_remaining` 后，**若节点是修复循环内节点**，同时 `update.setdefault` 一个 `_dev_loop_llm_calls` 累加。但 wrapper 是通用的（intake/analysis 等也走它），不宜硬编码"哪些节点算 dev_loop"。
- **落点 B（更干净，推荐）**：`_dev_loop_llm_calls` 只在 **execution 编排层**累加（execution 子图 rounds + metrics）；coding 的 rounds 通过 `retry_budget_remaining` 的全局账本间接受约束，**不单独计入 `_dev_loop_llm_calls`**。即 `_dev_loop_llm_calls` 语义收窄为"execution 侧修复循环 LLM 消耗累计"。理由：修复循环的成本大头在 execution（跑 + 重试），coding 每回合 rounds 已被全局账本 `retry_budget_remaining` 约束；子预算 60 主要防"execution 反复重试烧预算"。**本文采落点 B**：`_dev_loop_llm_calls` 仅由 execution 编排层累加（子图 rounds + metrics），语义清晰、不动 wrapper、AC 可精确断言。

> **落点 B 的 config 校准**：`DEV_LOOP_MIN_CALLS_PER_ROUND=2` 入口门语义不变；`MAX_DEV_LOOP_LLM_CALLS=60` 现在专指 execution 侧累计上限。若测试发现 60 对纯 execution 偏大，可下调（config 单点改，不动逻辑）——这属参数调优，非架构变更。

---

## 5. Q-B1：run_command 边界

### 5.1 结论

**`run_command` 只做无需 venv 的极轻量 smoke，用系统解释器（`_run_subprocess` 直接跑 argv），不共用 code_output_dir 的 venv；超时取 `RUN_COMMAND_TIMEOUT=120s`（远小于 sandbox `SANDBOX_EXEC_TIMEOUT=1800s`）；cwd 强制锚定 `code_output_dir` 并经 `_require_within_workspace` 校验；护栏全量复用 `_run_subprocess`（禁 shell=True / 进程组隔离 / 超时杀子树 / 输出截断）。**

### 5.2 决策依据

1. **职责红线（防 coding 抢 execution 的活）**：coding 的 `run_command` 定位是"import 通不通、语法对不对、脚本能不能启动"这类秒级无副作用自查。**不共用 venv**——共用 venv 意味着 coding 可以 `pip install` + 跑训练，等于把 execution 的重活搬到 coding，违背路线丙"两 agent 职责分离"。用系统解释器做 `python -c "import ..."` / `python -m py_compile` 足够覆盖 smoke 场景（依赖是否装全的判定交 execution 的 `prepare_venv`）。
2. **短超时从机制上封顶**：120s 让 coding 无法用它跑训练（训练远超 120s 会被超时杀子树），配合 docstring 明确约束，双重防越界。
3. **B 档成功只认 execution**：`run_command` 的 exit 0 **不写 `execution_result`、不参与 B 档判定**（B 档只认 execution agent 产出，sp3 `_build_execution_result` 逐字保留）。coding smoke 成功不等于复现成功。
4. **越界拒绝复用既有护栏**：cwd 锚定 `code_output_dir`（与 write 工具同基准），`_require_within_workspace` 校验，越界转结构化错误。

### 5.3 与 execution sandbox 工具的对比

| | coding `run_command` | execution `run_in_sandbox` |
|---|---|---|
| 解释器 | 系统解释器（`_run_subprocess` 直跑 argv） | venv python（`run_in_venv`，prepare_venv 建的 .venv） |
| 超时 | `RUN_COMMAND_TIMEOUT=120s` | `SANDBOX_EXEC_TIMEOUT=1800s` |
| 用途 | 轻量 smoke（import/语法/启动） | 完整复现执行（训练/评估） |
| 写 execution_result | 否 | 是（经编排层收尾） |
| 凭证注入 | 是（extra_env，smoke 时 clone 用） | 是（extra_env，见 §9） |
| 护栏 | `_run_subprocess`（4 护栏） | `run_in_venv` → `_run_subprocess`（4 护栏） |

> **需 Maria 留意（非阻塞）**：若实践中发现 coding 的 smoke 确实需要项目依赖（如 `import torch` 才能验证），"系统解释器"会 ImportError。此时的正解仍是**让 coding 把这类判定交给 execution**（execution 装完 venv 再验），而非给 coding 开 venv。若 Maria 后续希望 coding 能在已建好的 venv 里 smoke，可在 v1.x 让 `run_command` 可选接受 execution 已建的 `python_exe`——但 MVP **不做**，保持职责纯粹。

---

## 6. Q-E1：.secrets 存储

### 6.1 结论（采纳 PM 倾向，补充读取优先级与一致性论证）

**采纳 `workspace_dir/.secrets`（0600 + gitignore，MVP 明文不加密），敏感值全程不进 state/checkpoint。** 与 config LLM api_key "不持久化"约定一致（见 §6.4）。**我认同 PM 倾向，直接采纳，不设"需 Maria 拍板"点。** 唯一补充：路径基准用**运行期 `state["workspace_dir"]`**（回退 `config.WORKSPACE_DIR`），而非项目级根目录——理由见 §6.2。

### 6.2 路径方案

- **文件路径**：`<workspace_dir>/.secrets`（JSON 明文，`{purpose_key: {"value": "...", "is_sensitive": true}}`）。
- **为什么用 workspace_dir 而非项目根**：workspace_dir 已是所有任务产物（venv/code/repos/logs）的根，`.gitignore` 只需保证 `workspace/` 或 `.secrets` 被忽略（当前 `.gitignore` 已改，开发时**必须核对** `.secrets` 在忽略清单内，AC-S4-09 断言）。放项目根会增加误提交风险。
- **权限**：创建时 `os.open(path, O_CREAT|O_WRONLY|O_TRUNC, 0o600)` + `os.chmod(0o600)`（POSIX；Windows MVP 不强制，打 WARNING）。
- **config 常量**：新增 `SECRETS_FILE_NAME: str = ".secrets"`；实际路径 = `Path(workspace_dir) / SECRETS_FILE_NAME`。

### 6.3 读取优先级（去重 + 跨任务复用）

`request_user_input(purpose_key=...)` 命中判定顺序：
1. **`.secrets` 命中**（`lookup_secret(purpose_key)` 返回非 None）→ 直接返回值，**不 interrupt、不重复问**（跨任务复用，Maria 已定 purpose_key 粒度）。
2. **未命中** → interrupt#3 问用户 → 用户勾「记住」→ `remember_secret` 落 `.secrets`。
3. **本任务内非敏感复用**：非敏感项（`is_sensitive=False`）经 `collected_inputs`（GlobalState，见 §7.2）在**同一任务内**去重；敏感项不进 `collected_inputs`，跨任务复用只靠 `.secrets`。

> **优先级细节**：敏感项去重完全靠 `.secrets`（因为敏感值不进 state）；非敏感项本任务内靠 `collected_inputs`、跨任务不复用（非敏感信息任务相关性强，不宜跨任务复用）。工具内 `lookup_secret` 只查 `.secrets`；非敏感项的 `collected_inputs` 命中判定由**编排层在挂工具前**做（把已收集非敏感项通过 system prompt 稳定段外的 HumanMessage 上下文提示 agent"已知 X=Y"，避免 agent 重复问）——MVP 可简化为只做 `.secrets` 去重，`collected_inputs` 去重列 P2 优化。

### 6.4 与 config "api_key 不持久化"约定的一致性

技术架构 §8.4 约定 LLM api_key 从 env 读、不持久化到 state/checkpoint（`config.get_llm_api_key()` 读 `LLM_API_KEY` env）。`.secrets` **扩展而非违背**此约定：
- **一致点**：敏感值**都不进 state/checkpoint**。LLM api_key 走 env，用户凭证走 `.secrets`——两者都在 GlobalState 之外。
- **差异点**：LLM api_key 由部署环境预置（env），用户凭证由运行期交互收集（`.secrets`）。`.secrets` 是"运行期收集的凭证的本地缓存"，语义上等价于"用户手动 export 的 env"，只是持久化到 0600 文件供跨任务复用。
- **注入路径统一**：`.secrets` 的值最终经 `extra_env` 注入 sandbox 子进程（§9），与 api_key 经 env 注入 LLM 客户端**同构**——都不经 GlobalState 中转。

### 6.5 敏感值全程不进 state 的 filter 落点

| 落点 | 保证 |
|---|---|
| `request_user_input` 返回 | 敏感值作为 ToolMessage 内容进入**子图私有 messages**，不进 GlobalState（子图 messages 不回灌，见 §9.3） |
| `pending_user_input`（state） | 只存问题快照（question/is_sensitive/purpose_key），**绝不存答案** |
| `collected_inputs`（state） | 只存**非敏感**项（`is_sensitive=False`）；敏感项跳过 |
| `execution_result.logs` / `run_command` stdout | 落盘/回 state 前经 `mask_value(text, load_all_secrets())` 脱敏（§9.3）（**勘误 2026-07-05：实际为单参 `mask_value(text)`，见 §9.4 勘误**） |
| checkpoint（SqliteSaver） | 因 state 不含敏感值，checkpoint 天然不含 |
| reporting Markdown | reporting 读 state，state 无敏感值；额外对 logs 片段 `mask_value` |

---

## 7. 通用交互能力（interrupt#3 user_input_request）

### 7.1 interrupt#3 payload 契约

```python
# request_user_input 工具体内 interrupt(payload)：
{
    "interrupt_kind": "user_input_request",   # 第三类，与 "planning" / "dev_loop_failure" 区分
    "question": "<给用户看的问题文本>",
    "is_sensitive": true|false,               # true → UI password 输入 + 「记住」勾选
    "purpose_key": "git_credential:github.com" | null,
}
# UI 收集后 resume：
Command(resume={"value": "<用户输入>", "remember": true|false})
```

与 sp2 planning（`{"decision": ...}`）、sp3 dev_loop（`{"decision": ...}`）的 resume 范式同构（都是 dict + 约定键）。

### 7.2 state 新增字段（S4-10，最小，绝不加 reducer）

| 字段 | 类型 | 语义 | 写入方 | reducer |
|---|---|---|---|---|
| `pending_user_input` | `Optional[Dict]` | 当前待回答请求的快照（question/is_sensitive/purpose_key），供 UI 渲染；**绝不存答案** | 编排层（interrupt 前写，resume 后清 None） | 无 |
| `collected_inputs` | `Dict[str, str]` | 本任务内已收集的**非敏感**信息（purpose_key → value）；敏感项不进 | 编排层 read-modify-write | 无（Dict 单点写） |

> **单点 read-modify-write 写法**（沿用 must-fix-1 范式）：
> ```python
> collected = dict(state.get("collected_inputs", {}))   # 读出整 dict
> if not is_sensitive and purpose_key:
>     collected[purpose_key] = value                     # 修改
> return {"collected_inputs": collected, ...}            # 写回整 dict（last-write-wins 安全）
> ```
> **`pending_user_input` 的写入时机**：注意工具内 `interrupt()` 无法直接写 GlobalState（工具在子图内运行，返回值才是子图更新）。`pending_user_input` 供 UI 渲染的信息**已在 interrupt payload 里**（UI 读 `get_interrupt_payload` 即得 question/is_sensitive/purpose_key），故 `pending_user_input` **主要用于编排层可观测性**（可选）；MVP 可让 UI 直接读 interrupt payload，`pending_user_input` 作为冗余镜像由编排层在 resume 后清空。**推荐 MVP：UI 直接读 interrupt payload，`pending_user_input` 仅作 state 通道声明占位**（保证声明了就不被 LangGraph 静默丢弃），实际渲染走 payload。

### 7.3 GraphController.interrupt_kind() 分发

`app.py::interrupt_kind(thread_id)` 已实现为读 `get_interrupt_payload().get("interrupt_kind")`（sp3 E1 落地），**天然支持第三类**。sp4 无需改 helper 本体，只需 UI 侧新增分支：

```
interrupt_kind ==
  "planning"           → plan_review 页（sp2）
  "dev_loop_failure"   → execution_monitor dev_loop 失败决策面板（sp3）
  "user_input_request" → execution_monitor 用户输入面板（sp4 新增）
```

### 7.4 UI 形态（S4-09，execution_monitor.py 新增分支）

- `is_interrupted(thread_id) and interrupt_kind == "user_input_request"` 时，读 payload 渲染：
  - `question` + 一句上下文（当前节点 `current_step`）；
  - 单输入框（`is_sensitive=True` → `type="password"`）；
  - `is_sensitive=True` 时显示「记住此凭证供后续复现复用」勾选（默认不勾）；
  - 提交 → `resume_with(thread_id, {"value": ..., "remember": ...})`。
- `GraphController` 的 `resume_with` / `is_interrupted` / `interrupt_kind` **零改动**承载（payload 通道通用）。

---

## 8. 工具内 interrupt 的重跑幂等（Q-C1）

### 8.1 结论（Maria 已定不单独 spike、边开发边验，AC-S4-14 强制断言）

工具内 `interrupt()`（interrupt#3）的重跑幂等**机理与 sp3 节点级 interrupt#2 不同**，须在开发中重点验证并用 AC-S4-14 硬断言。设计上给出"resume 后不重复副作用"的保证方案如下。

### 8.2 机理差异

| | interrupt#2（sp3 节点级） | interrupt#3（sp4 工具内） |
|---|---|---|
| 位置 | execution 主函数体内 | ReAct 子图内某工具体内 |
| 重跑范围（resume 时） | LangGraph 对**整节点**从头重跑到 interrupt 处（sp3 实证副作用=2 隐患，靠 commit 边界解决） | LangGraph 对**子图从上次 checkpoint 恢复**——ReAct 子图的 reasoning/tools 节点是否重放取决于子图 checkpoint 粒度 |
| sp3 已用方案 | commit 边界 self-loop（结果先落盘 → 重入跳过 sandbox → 再 interrupt） | 无（sp4 新增） |

### 8.3 幂等保证方案（三层）

1. **依赖 LangGraph interrupt 的"节点级 resume 定位"语义**：LangGraph 的 `interrupt()` 在 resume 时，同一线程同一 `checkpoint_ns` 下，**已完成的图节点不重跑**，只重跑 interrupt 所在节点并从 `interrupt()` 处返回 resume 值（这是 LangGraph human-in-the-loop 的核心保证）。ReAct 子图内，`request_user_input` 所在的 tools 执行节点重跑时，**同一批 tool_calls 会重放**——这是风险点。
2. **副作用工具与 request_user_input 分离到不同 ReAct 轮次**：ReAct 每轮是"reasoning（决定 tool_calls）→ tools（执行）"。若 agent 在**同一轮**同时调 `write_code_file` + `request_user_input`，则 resume 重跑该轮 tools 时 write 会重放。**缓解**：`request_user_input` 的 docstring 明确"本工具会暂停等待用户，请单独一轮调用、不要与写文件/运行命令同轮"；且 ReAct 子图的 tools 节点对**幂等工具**（write 覆盖写、run_command 无持久副作用的 smoke）天然安全——write 覆盖同内容幂等、run_command smoke 无副作用。真正有副作用的 clone/下载才是重点。
3. **副作用工具自身幂等**（根本保证）：
   - `write_code_file`：覆盖写，重放同内容幂等（sp3 已如此）。
   - `run_command`（smoke）：无持久副作用（import/语法检查），重放幂等。
   - `git clone`（若在 run_command / sandbox 内）：**复用 sp2 git_tools "同 URL 已存在则跳过"幂等范式**（clone 前检查目标目录已存在则跳过），重放不重复 clone。
   - `prepare_environment`：`prepare_venv(reuse_existing=True)`（L448 默认），venv 已存在则复用，重放幂等。
   - `run_in_sandbox` 跑训练：**非幂等**（重放会重跑训练）。**缓解**：`request_user_input` 应在"跑重活之前"问（缺凭证/缺数据在 prepare 阶段就暴露），使 interrupt#3 发生时尚未跑训练；若确实在训练中途缺信息，属边界 case，MVP 接受重跑一次训练的代价（AC-S4-14 覆盖"write/run_command → request_user_input → resume 前序不重放"，训练重跑列风险 R-S4-03）。

### 8.4 AC-S4-14 断言设计

在 coding / execution 两 agent 上各构造"调 write_code_file / run_command（副作用工具）→ 再调 request_user_input → Command(resume)"场景，断言 resume 后：
- write_code_file 不产生重复文件写（或覆盖写幂等，文件内容/数量不变）；
- run_command 若 smoke 无副作用，重放无影响；
- 有副作用的 clone 走幂等跳过。
断言"副作用恰为 1"（与 sp3 CP-C3-13 同款断言范式）。

---

## 9. 凭证闭环

### 9.1 完整链路

```
execution/coding agent 遇缺凭证（git clone 私有仓库 / pip 私有源 / HF token）
  → 检测：sandbox 子进程认证失败 stderr 命中凭证关键字
  → 分类：ErrorCategory.CREDENTIAL_REQUIRED（不可自动修复，不进 AUTO_FIXABLE，不耗 fix_loop_count）
  → agent 就地调 request_user_input(is_sensitive=True, purpose_key="git_credential:github.com")
  → interrupt#3 → UI password 输入 + 记住 → resume → 值经 .secrets 落盘（若记住）
  → agent 拿到凭证 → 经 extra_env 注入下次 sandbox 子进程 → clone/下载成功
```

### 9.2 检测与分类（execution 本地新增）

- **`ErrorCategory` 新增 `CREDENTIAL_REQUIRED = "credential_required"`**（`execution.py` 本地 Enum，**不进 core/state.py**，与 sp3 其它细分类同处置）；**不加入 `AUTO_FIXABLE`**（归不可修复类，`_map_category_to_error_type` 映射为 `permanent`，不耗 `fix_loop_count`）。
- **新增凭证关键字表**（execution 本地，小写匹配，**先于** DATA_MISSING/HARDWARE 判定顺序）：
  ```python
  _CREDENTIAL_KEYWORDS = (
      "could not read username", "authentication failed",
      "terminal prompts disabled", "permission denied (publickey)",
      "fatal: could not read", "invalid username or password",
      "401 unauthorized", "403 forbidden",
  )
  ```
  命中后 `_classify_execution` 返回 `ExecutionFeedback(CREDENTIAL_REQUIRED, auto_fixable=False, ...)`。
- **execution agent 就地问**：因 execution 现在是 agent（内嵌子图），子图内识别到凭证缺失可**直接调 `request_user_input`**（interrupt#3），拿到后重试 sandbox——不必绕回 coding 或走 interrupt#2 降级。这是路线丙相对 sp3"缺凭证打转到 `MAX_FIX_LOOP_COUNT` 耗尽"的核心改进。

> **注意 credential 的两个触发路径**：(a) agent 在子图内**主动**识别缺凭证 → 直接 `request_user_input`（首选，最顺）；(b) 若 agent 没主动问、sandbox 报认证失败落回编排层收尾 → `_classify_execution` 分类 `CREDENTIAL_REQUIRED` → 走 interrupt#2 让用户决策（兜底）。两条路径并存，(a) 优先。

### 9.3 注入 sandbox（S4-06）+ prepare_venv 补 extra_env

- **`run_in_venv`（L658）/ `_run_subprocess`（L304）已支持 extra_env**（`env = {**os.environ, **(extra_env or {})}` 实证），可直接注入。
- **缺口须补：`prepare_venv`（L444）无 extra_env 形参**。改动：
  ```python
  def prepare_venv(work_dir, requirements=None, requirements_files=None,
                   reuse_existing=True, venv_timeout=..., pip_timeout=...,
                   extra_env=None):   # 新增形参
      ...
      # 内部所有 pip install / venv 创建的 _run_subprocess 调用透传 extra_env=extra_env
      # （pip 装私有源依赖需凭证；GIT_TERMINAL_PROMPT=0 也经此注入让认证失败立即返回）
  ```
- **git 凭证注入方式**：MVP 用 **`extra_env` 注入 + `GIT_TERMINAL_PROMPT=0`**：
  - `GIT_TERMINAL_PROMPT=0`：让 git 认证失败**立即返回**而非挂起等 stdin（非交互 subprocess 读不到 stdin，否则会挂到超时）。
  - token 注入：MVP 用 **`GIT_ASKPASS` 脚本** 或 **`credential.helper` env**，或最简单——在 clone URL 里嵌 token（`https://<token>@github.com/...`），但 URL 嵌 token 会进 stderr/logs，**必须经 §9.4 脱敏**。**推荐 GIT_ASKPASS**（token 不进命令行/URL，更安全）。`purpose_key → env var 名` 映射：`git_credential:<host>` → 组装 GIT_ASKPASS 返回该 token；`hf_token` → `HF_TOKEN` / `HUGGING_FACE_HUB_TOKEN`。映射表放 `secrets_store.py` 或 execution 编排层的 `_git_env(secrets)`。
- **注入边界**：凭证 env var **只注入复现任务子进程**（sandbox extra_env），不污染主进程长期环境（`_run_subprocess` 用 `{**os.environ, **extra_env}` 局部合并，不改主进程 os.environ）。

### 9.4 脱敏 filter 统一落点

`core/secrets_store.py::mask_value(text: str, secrets: Dict[str, str]) -> str`：把 text 中出现的任何已知敏感值替换为 `****`。统一落点：

> **[勘误 2026-07-05] 实际签名为单参 `mask_value(text)`，敏感值全集由函数内部自取（`.secrets` 中 `is_sensitive=True` 项 ∪ 进程内注册 sensitive set）；双参写法会 TypeError。以 `core/secrets_store.py` 与 dev-plan §4 A3 接口表为准（对应挂账 L-A3-02）。本节表格与下方引注中的双参调用形态一并按此勘误理解。**

| 落点 | 调用 |
|---|---|
| `execution_result.logs` 聚合前 | `_aggregate_logs` 结果 `mask_value(logs, load_all_secrets())` 后再写 state |
| `run_command` 返回的 stdout/stderr | 工具返回前 `mask_value` |
| git stderr 含 token URL | `_run_subprocess` 返回后 / execution 收尾时 `mask_value` |
| 日志（logger） | 打日志前对含凭证的字符串 `mask_value`；或在 logging 层加 filter（MVP 在写入点手动 mask） |

> **脱敏 filter 是安全关键**，AC-S4-11/12 验证（grep 全链路无明文 + caplog 断言）。`load_all_secrets()` 只读已"记住"的敏感值 + 本次内存中的敏感值（内存旁路：本次任务未记住的敏感值也需 mask，故 `mask_value` 的 secrets 参数应含"本次会话内存中的敏感值集合"——由编排层维护一个进程内 sensitive-values set 传入）。

---

## 10. 数据流图

### 10.1 sp4 完整数据流（三类 interrupt + 两 agent）

```
START → paper_intake → paper_analysis → resource_scout → planning
                                                            │ interrupt#1 (planning, sp2)
                                                            ▼
                                              _route_after_planning (零改动)
                                                            │ next(approve/code_only)
                                                            ▼
                                                         coding  ← ReAct wrapper（7 工具）
                                              （write/read/list/read_section/web_search
                                                + run_command + request_user_input）
                                              coding 内可 interrupt#3 (user_input_request)
                                                            │
                                              _route_after_coding (零改动)
                                              ┌─────────────┴──────────────┐
                                        to_execution(FULL)          skip_execution(CODE_ONLY)
                                              │                              │
                                              ▼                              │
                                          execution  ← 手写编排 + 内嵌 ReAct 子图              │
                                    ┌──────────────────────────────────┐    │
                                    │ 编排层: guard → 内嵌子图跑执行 →     │    │
                                    │   分类/判定/map → 边界 → interrupt#2 │    │
                                    │ 子图内: prepare_environment /       │    │
                                    │   run_in_sandbox / request_user_input│   │
                                    │   → interrupt#3 (user_input_request) │    │
                                    └──────────────────────────────────┘    │
                                              │                              │
                                    _route_after_execution (零改动 sp3)       │
              ┌──────────┬───────────┬──────────────┬───────────┬──────────┤
        execution     coding      reporting      planning      end         ▼
        (await self-  (retry_     (成功/降级/     (revise_      (terminate) reporting
         loop命门)     coding)     export_code)   plan)                    (三形态)
              │  ▲                                                          │
              └──┘ await_dev_loop_interrupt self-loop（interrupt#2 命门，逐字保留）
                                                                            ▼
                                                                           END

三类 interrupt（同一 thread_id，interrupt_kind 分发）：
  "planning"           → plan_review 页（sp2）
  "dev_loop_failure"   → execution_monitor dev_loop 失败面板（sp3）
  "user_input_request" → execution_monitor 用户输入面板（sp4，coding/execution 均可触发）
```

### 10.2 凭证闭环数据流

```
execution 子图 run_in_sandbox（git clone 私有仓库）
  → GIT_TERMINAL_PROMPT=0 → 认证失败立即返回 stderr "could not read username"
  → agent 识别 → request_user_input(is_sensitive=True, purpose_key="git_credential:github.com")
  → interrupt#3 → checkpoint 落盘（state 无敏感值）
  → UI password 输入 + 记住 → Command(resume={"value": token, "remember": true})
  → 工具 remember_secret 落 .secrets(0600) → 返回 token 给 agent（子图私有 messages，不进 state）
  → agent 重试 run_in_sandbox（extra_env 注入 GIT_ASKPASS→token）→ clone 成功
  → logs mask_value 脱敏 → execution_result 回 state（无明文）→ checkpoint（无明文）
```

---

## 11. 关键设计决策

### 11.1 execution 用"手写编排 + 内嵌 ReAct 子图"而非裸 wrapper
见 §3。核心：保留 sp3 commit 边界 self-loop 重跑幂等命门（主图节点级机制），与 planning 同范式。裸 wrapper 无处安放 self-loop，会冲掉命门。

### 11.2 预算扣减在编排层单点做，不存在自动双重扣减
见 §4。execution 不经 `_make_react_wrapper`，L889-894 不触发；扣减由编排层显式做（子图 rounds + metrics），是唯一扣减点。`_dev_loop_llm_calls` 语义收窄为 execution 侧累计（落点 B）。

### 11.3 run_command 用系统解释器 + 120s 短超时，职责与 execution 严格分离
见 §5。coding smoke 不共用 venv、不写 execution_result、B 档只认 execution。

### 11.4 交互工具单一极简，敏感值全程旁路 state
见 §6/§7。一个 `request_user_input`（三字段），无 input_type 枚举。敏感值只在 .secrets + 子进程 extra_env + 子图私有 messages 中流转，不进 GlobalState/checkpoint。

### 11.5 credential 分类归不可修复 + agent 就地问（改进 sp3 打转缺陷）
见 §9.2。`CREDENTIAL_REQUIRED` 不进 AUTO_FIXABLE，execution agent 子图内直接 request_user_input，止损"缺凭证打转到 MAX_FIX_LOOP_COUNT 耗尽"。

### 11.6 7 节点骨架 + graph.py 零改动
execution 节点名不变、边结构不变、self-loop 保留 → `build_graph` 零改动。这是路线丙相对路线甲（要嵌 supervisor 子图、动主图边）的最大简化。

### 11.7 沿用 must-fix-1 红线：新增 state 通道绝不加 reducer
`pending_user_input`（Optional[Dict]）/ `collected_inputs`（Dict）均单值/单点写，last-write-wins 安全，绝不加 `Annotated`/`operator.add`。

---

## 12. state / config 字段处置表

### 12.1 state 新增/复用

| 字段 | 类型 | sp4 处置 | 写入方 | reducer | 备注 |
|---|---|---|---|---|---|
| `pending_user_input`（新增） | `Optional[Dict]` | **新增通道**（显式声明 + 默认 None） | 编排层（可选镜像） | 无 | 只存问题快照，绝不存答案；UI 主要读 interrupt payload |
| `collected_inputs`（新增） | `Dict[str, str]` | **新增通道**（显式声明 + 默认 {}） | 编排层 read-modify-write | 无 | 只存非敏感项；敏感项跳过 |
| `_dev_loop_route` | `Optional[str]` | 复用（sp3 已有） | execution 编排层 | 无 | await_dev_loop_interrupt / retry_coding |
| `_dev_loop_llm_calls` | `int` | 复用，语义收窄为 execution 侧累计（§4.5 落点 B） | execution 编排层 | 无 | 子预算累计，触顶 60 → interrupt#2 |
| `execution_result` / `code_output_dir` / `fix_loop_count` / `fix_loop_history` / `node_errors` / `degraded_nodes` / `retry_budget_remaining` / `user_fix_decision` | 同 sp3 | 复用，逐字不变 | 同 sp3 | 无（List 严禁加 reducer） | sp3 处置全部保留 |

### 12.2 config 新增

| 常量 | 值 | 说明 |
|---|---|---|
| `REACT_MAX_ROUNDS_EXECUTION` | 10（建议） | execution 内嵌子图单次 invoke 轮次上限（budget_check 用） |
| `RUN_COMMAND_TIMEOUT` | 120 | coding run_command 短超时（秒，远小于 SANDBOX_EXEC_TIMEOUT=1800） |
| `SECRETS_FILE_NAME` | `.secrets` | `.secrets` 文件名（路径 = workspace_dir / 此名） |

> **不新增交互超时常量**（Maria 已定一直暂停，Q-F1）。`MAX_DEV_LOOP_LLM_CALLS=60` / `MAX_FIX_LOOP_COUNT=10` / `DEV_LOOP_MIN_CALLS_PER_ROUND=2` / `MAX_TOTAL_LLM_CALLS=120` 全部复用现值。

---

## 13. 对既有代码的改动点清单（最小 diff 原则）

| 文件 | 改动类型 | 具体改动 | 风险 |
|---|---|---|---|
| `core/tools/interaction_tools.py` | **新增** | `request_user_input`（interrupt#3 + .secrets 去重 + 记住落盘） | 中（interrupt 重跑幂等，§8） |
| `core/tools/run_command_tool.py` | **新增** | `make_run_command_tool`（系统解释器 smoke，复用 _run_subprocess） | 低 |
| `core/secrets_store.py` | **新增** | lookup/remember/load_all/mask_value（0600 + gitignore + 脱敏） | 中（安全关键，AC-S4-11/12） |
| `sandbox/local_venv.py` | **改动** | `prepare_venv` 补 `extra_env` 形参 + 透传 pip 子进程 | 低（向后兼容，默认 None） |
| `core/nodes/coding.py` | **改动** | `_get_coding_tools` 增挂 2 工具；system prompt body 补工具说明（稳定前缀） | 低（wrapper 形态不变） |
| `core/nodes/execution.py` | **改动** | execution() 步骤1+2 换内嵌 ReAct 子图（`_run_execution_agent`）；`_map_execution_result` 加 react_rounds_used/dev_calls_delta 扣减；新增 CREDENTIAL_REQUIRED 分类 + 凭证关键字表；sandbox 工具化（prepare_environment/run_in_sandbox）；挂 request_user_input | **高**（最大工作量 + 重跑幂等 + 预算对账） |
| `core/state.py` | **改动** | 新增 `pending_user_input` / `collected_inputs` 两通道 + create_initial_state 默认值；**绝不碰 List 字段/reducer** | 低（向后兼容） |
| `config.py` | **改动** | 新增 REACT_MAX_ROUNDS_EXECUTION / RUN_COMMAND_TIMEOUT / SECRETS_FILE_NAME | 低 |
| `app.py` | **改动** | interrupt_kind 已支持第三类，无需改本体；UI 路由分发新增 user_input_request 分支消费 | 低 |
| `ui/pages/execution_monitor.py` | **改动** | 新增 user_input_request 交互面板（单输入框 + password + 记住） | 低（沿用 sp3 面板范式） |
| `.gitignore` | **核对** | 确认 `.secrets`（或 workspace/）在忽略清单（AC-S4-09） | 低 |
| **零改动** | — | `core/graph.py`（节点名/边/self-loop 不变）、`core/react_base.py`（预算扣减点不动）、`core/checkpointer.py`、`core/nodes/planning.py`、reporting、sp1/sp2 既有节点 **[勘误 2026-07-05] `core/react_base.py` 已由 BUG-S4-B1-01 修复（commit ae09733，GraphBubbleUp 直通、8 行纯追加）豁免零改动（预算扣减点本身未动）；详见 dev-plan §7.3 勘误注记，严禁为对齐本表述还原该修复** | 无 |

---

## 14. 治理范式硬约束（沿用 sp1/sp2/sp3）

1. **JSON 序列化写 ToolMessage（BUG-S1-02）**：新工具 `run_command` 返回 dict 一律 `json.dumps(ensure_ascii=False, sort_keys=True, default=str)`；`request_user_input` 返回纯字符串（用户值），无需序列化。禁 `str(dict)`。
2. **3 参 map_result + 失败 ToolMessage 过滤 backfill（BUG-S1-03）**：coding 沿用现 `_map_coding_result` 3 参签名；execution 编排层内嵌子图收尾时，解析工具轨迹须过滤失败 ToolMessage（沿用 coding `_has_written_any_file` 的"只认 success=true"范式）。
3. **失败非静默打 WARNING**：run_command 越界 / 解析失败、request_user_input 非法 resume、prepare_venv extra_env 注入失败、mask_value 覆盖失败、credential 分类命中——全部 `logger.warning`，不静默吞。
4. **新增 state 通道显式声明 + 默认值**：`pending_user_input`（None）/ `collected_inputs`（{}）必须在 GlobalState 声明 + create_initial_state 给默认值，否则节点写入被 LangGraph 静默丢弃（B2/B3 实证）。
5. **Prompt Cache 字节级幂等**：`request_user_input` / `run_command` 的 docstring（工具 schema）不含任何动态变量；coding system prompt 新增的工具说明放稳定前缀 body，不破坏方案 A。
6. **7 节点骨架与名称严格不变**（AC-S3-10 沿用）：`build_graph` 零改动。

---

## 15. 风险与缓解

| 编号 | 风险 | 等级 | 缓解 |
|---|---|---|---|
| R-S4-01 | execution 内嵌子图收尾时无法可靠从工具轨迹取回真实 SandboxRunResult（agent 谎报） | 高 | 工具把每次 run 结果 append 到子图私有 context 列表，`_run_execution_agent` invoke 后从 final_state 取真实结果；确定性收尾只认真实 exit_code/stderr，不信 agent 自述（§3.4） |
| R-S4-02 | interrupt#3 resume 重跑重放同轮有副作用工具（write/clone/训练） | 高 | write 覆盖幂等、clone 走"已存在跳过"幂等、prepare_venv reuse 幂等；docstring 约束 request_user_input 单独一轮调用；AC-S4-14 硬断言（§8） |
| R-S4-03 | 训练中途缺信息 → interrupt#3 → resume 重跑训练（非幂等） | 中 | 设计上让缺信息在 prepare 阶段暴露（跑重活前问）；训练中途缺信息属边界 case，MVP 接受重跑代价，列 v1.x（子图回合级 checkpoint_ns） |
| R-S4-04 | 预算对账错扣/漏扣（子图 rounds + metrics 两来源） | 中 | 编排层单点扣减 + AC-S4-04 断言"一次 execution 后 retry_budget_remaining 递减 = rounds+metrics"；self-loop 重入 rounds=0 不重扣 |
| R-S4-05 | 敏感值泄漏进 state/logs/report/checkpoint | 高 | 敏感值旁路 state（§6.5）；mask_value 统一脱敏落点（§9.4）；AC-S4-11/12 grep + caplog 断言 |
| R-S4-06 | .secrets 误提交 git | 中 | 0600 + .gitignore 核对（AC-S4-09）；开发时 grep .gitignore 确认 |
| R-S4-07 | credential 关键字表漏判（认证失败被误分类 runtime → 打转） | 中 | 凭证关键字表先于其它判定；兜底仍靠 MAX_FIX_LOOP_COUNT 拦截；agent 主动问是首选路径（§9.2） |
| R-S4-08 | GIT_TERMINAL_PROMPT=0 未注入 → clone 挂起到超时 | 中 | prepare_venv/run_in_venv 的 extra_env 无条件带 GIT_TERMINAL_PROMPT=0（即便无凭证也带，让失败立即返回） |

---

## 16. 测试策略建议（供测试工程师）

### 16.1 分层测试矩阵

| 层 | 用例 | 方式 |
|---|---|---|
| 交互工具 | request_user_input 触发 interrupt_kind="user_input_request"；resume {"value"} 后返回值；purpose_key 命中 .secrets 不 interrupt | mock + Command(resume) |
| run_command | 越界拒绝；120s 超时；系统解释器 smoke；返回 JSON 结构；不写 execution_result | mock（构造越界/超时） |
| execution ReAct | 内嵌子图跑通 → 确定性收尾读真实结果；agent 谎报成功被确定性判定拦（success 只认真实 exit+metrics） | mock sandbox（不真跑训练，省配额） |
| 预算对账（AC-S4-04） | 一次 execution 后 retry_budget_remaining 递减 = 子图 rounds + metric_llm_calls；self-loop 重入不重扣；_dev_loop_llm_calls 累加；触顶 60 → interrupt#2 | mock |
| interrupt#2 回归（AC-S4-05） | 复用 sp3 C3/D1/E2 用例全绿（commit 边界 self-loop 重跑幂等不回归） | 复用 sp3 用例 |
| 重跑幂等（AC-S4-14） | write/run_command → request_user_input → resume 后前序副作用恰为 1 | mock + resume 断言 |
| credential（AC-S4-07/08） | 认证失败 stderr → CREDENTIAL_REQUIRED（不进 AUTO_FIXABLE、不耗 fix_loop_count）→ request_user_input；extra_env 注入后 clone 成功 | mock 认证失败 stderr + 注入断言 |
| .secrets（AC-S4-09） | 记住 → 写 .secrets(0600 + 在 .gitignore)；同 purpose_key 复跑不问 | 文件权限 + gitignore 断言 |
| 脱敏（AC-S4-11/12） | 注入已知 token 后 grep 代码/logs/checkpoint/报告无明文；caplog 无明文 | e2e（授权真凭证）+ mock grep |
| 三 interrupt 互不干扰（AC-S4-13） | planning / dev_loop_failure / user_input_request 同 thread_id 串行各自正确路由 | e2e |
| 骨架不变（AC-S4-03） | build_graph 仍 7 节点、节点名集合不变、编译成功 | 结构测试 |

### 16.2 省配额范式（沿用）

- mock sandbox 不真跑训练；credential/脱敏的真凭证 e2e 须 Maria 明确授权具体动作；先 smoke fail-fast；靶 HippoRAG 缓存。
- 报告归档 `docs/sprint4/test-reports/`。

---

## 17. 给全栈开发的任务拆分建议（对齐 PRD §12.2 依赖顺序）

**P0 基础设施（无依赖，先行，风险低被复用）**
- [ ] `config.py`：新增 `REACT_MAX_ROUNDS_EXECUTION=10` / `RUN_COMMAND_TIMEOUT=120` / `SECRETS_FILE_NAME=".secrets"`。
- [ ] `core/state.py`：新增 `pending_user_input: Optional[Dict]`（默认 None）/ `collected_inputs: Dict[str, str]`（默认 {}）+ create_initial_state 默认值；**绝不碰 List 字段/reducer**（grep 断言保护）。
- [ ] `core/secrets_store.py`：`lookup_secret` / `remember_secret`（0600）/ `load_all_secrets` / `mask_value`；核对 `.gitignore` 含 `.secrets`。

**P1 交互工具地基（被两 agent 复用，开发中即验 Q-C1 / AC-S4-14）**
- [ ] `core/tools/interaction_tools.py`：`request_user_input`（interrupt#3 payload + .secrets 去重 + 记住落盘 + 非法 resume 兜底）。
- [ ] 即写 AC-S4-14 mock：write → request_user_input → resume 前序不重放。

**P2 coding 补工具（改动局部，coding 已是 ReAct）**
- [ ] `core/tools/run_command_tool.py`：`make_run_command_tool`（系统解释器 smoke + _run_subprocess 护栏 + JSON 返回 + 越界拒绝）。
- [ ] `core/nodes/coding.py`：`_get_coding_tools` 增挂 run_command + request_user_input；system prompt body 补工具说明（稳定前缀）。

**P3 凭证注入地基**
- [ ] `sandbox/local_venv.py`：`prepare_venv` 补 `extra_env` 形参 + 透传 pip 子进程（向后兼容默认 None）。

**P4 execution 改造（最大工作量 + 最高风险，须本文 §3/§4 方案）**
- [ ] `core/nodes/execution.py`：
  - sandbox 工具化：`prepare_environment`（包 prepare_venv）/ `run_in_sandbox`（包 run_in_venv），确定性解析改写下沉工具内；
  - `_run_execution_agent`：内嵌 `create_react_subgraph` invoke（挂 3 工具），返回 prep/run_results/rounds_used/llm_calls；工具把 run 结果 append 到子图 context，收尾从 final_state 取真实结果；
  - execution() 步骤1+2 替换为 `_run_execution_agent`；步骤3-7 逐字保留（含 guard/await self-loop/interrupt#2）；
  - `_map_execution_result` 加 `react_rounds_used` / `dev_calls_delta` 单点扣减 + 累加；
  - 新增 `CREDENTIAL_REQUIRED` 分类 + `_CREDENTIAL_KEYWORDS`（先于其它判定）；
  - 挂 request_user_input（子图内缺凭证就地问）；
  - logs 经 mask_value 脱敏后回 state。
- [ ] 回归守门：复用 sp3 C3/D1/E2 用例（AC-S4-05）。

**P5 UI**
- [ ] `ui/pages/execution_monitor.py`：新增 user_input_request 面板（单输入框 + is_sensitive password + 记住勾选）→ resume_with({"value","remember"})。
- [ ] `app.py`：UI 路由分发新增 user_input_request 分支（interrupt_kind helper 本体零改）。

**P6 验收对齐（测试工程师协作）**
- [ ] AC-S4-01~14 全覆盖；report 归档 `docs/sprint4/test-reports/`。

---

**文档结束**

*本文档为 Sprint 4 架构设计文档。数据结构权威定义以 `core/state.py` 为准；预算扣减以编排层单点回写为准（execution 不经 `_make_react_wrapper`，故 `react_base.py` L889-894 不对 execution 触发）；主图骨架基线以 `core/graph.py::build_graph` 为准（sp4 零改动）；interrupt#2 重跑幂等命门以 `core/nodes/execution.py` 的 `_has_committed_result_for_round` guard + `await_dev_loop_interrupt` self-loop 为准；编排范式以 `core/nodes/planning.py`（手写复合 + 内嵌 ReAct 子图 + interrupt#1）为准；sandbox 护栏/extra_env 注入以 `sandbox/local_venv.py` 为准；序列化/脱敏范式以 `core/tools/git_tools.py` + `core/secrets_store.py` 为准。PRD §11 的 Q-A1/Q-A2/Q-B1/Q-E1 据此落地，其余风险见 §15。*
