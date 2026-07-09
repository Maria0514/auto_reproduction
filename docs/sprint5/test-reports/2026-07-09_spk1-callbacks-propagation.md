# Spike 结论报告 - CP-SPK-1 callbacks 传播实证（T-S5-0-1）

- **日期**：2026-07-09（本地时区）
- **执行人**：@全栈开发代理
- **Sprint**：sprint5 批次 0
- **触发原因**：dev-plan §4 任务 T-S5-0-1 / 架构 §4（Q-S5-8 可行性论证）/ §10.1 R-1——验证顶层 `build_graph().invoke(state, {**config, "callbacks": [handler]})` 注入的 callbacks 能否经 langchain-core `var_child_runnable_config` contextvar 穿透两类"节点内手动 `subgraph.invoke`"边界，决定批次 4（S5-07 活动流）走主路径还是回退 R-B1。
- **commit**：b3eb01a（工作区仅新增 `tests/test_sprint5_spk1_callbacks_spike.py` 与本报告，未 commit，主控收口）
- **环境**：Python 3.11.5 / langchain-core 1.3.3 / langgraph 1.1.10（⚠️ 结论依赖库版本的 contextvar 传播实现，后续升级 langchain-core / langgraph 时须复跑本 spike 套件复验）

## 门禁结论（批次 4 路径判定）

> **两条路径均传播 → 批次 4 走主路径：react_base 零字节改动，不触发回退 R-B1（第一档、第二档均不需要）。**
> execution.py 亦无需编排层透传改动；dev-plan §7 无需勘误注记。

| 检查点 | 结论 |
|---|---|
| CP-0.1-1 coding 路径传播（`_make_react_wrapper` → `subgraph.invoke`，react_base.py:873） | **是，传播**（证据见下） |
| CP-0.1-1 execution 路径传播（`_run_execution_agent` → 裸 `subgraph.invoke`，execution.py:1272） | **是，传播**（证据见下） |
| CP-0.1-2 事件元数据三项实证 | **全部可用**（含 1 个关键落地修正，见"元数据实证"） |
| CP-0.1-3 结论报告归档 | 本报告 |

## 执行范围

- 命令（全程纯 mock，零 LLM/deepxiv 配额、零网络外呼；测试**不标 e2e marker**，默认收集运行）：
  - `.venv/bin/pytest tests/test_sprint5_spk1_callbacks_spike.py -s -v` × 1 + `-q` × 2（**3 连跑**）
  - `.venv/bin/pytest --collect-only -q`（全库收集对账：1428/1474 collected、46 deselected，与基线一致）
- 结果：**4 passed / 0 failed，3 连跑零抖动**（0.82s / 0.85s / 0.81s / 0.81s）。

## 实验装置（tests/test_sprint5_spk1_callbacks_spike.py）

裁剪自 sp4 G2 mock e2e harness（`tests/test_sprint4_e2e.py`）：

- **真实 `build_graph(checkpointer=InMemorySaver())`**，coding / execution 节点走**真实节点代码路径**（wrapper → `subgraph.invoke` / 手写复合 → 裸 `subgraph.invoke`），仅注入点替换：
  - `react_base.create_llm` + `execution_module.create_llm` → 剧本 LLM `SpikeScriptLLM`（真 `BaseChatModel` 子类，按 SystemMessage 身份短语分发 coding/execution 脚本，各 2 轮：1 次 tool_call + 1 次 `<result>` 收尾）；
  - `execution_module.run_in_venv` → CountingRunner（train.py exit 0，B 档成功，无修复循环、无 interrupt#2）；
  - coding 的 deepxiv 工具 → 惰性 mock；`write_code_file` 用**真实工具**（真写盘到 tmp 隔离 workspace）。
- 上游 4 节点（intake/analysis/scout/planning）patch 为 fake 纯函数（planning 直接 approve 绕过 interrupt#1）。fake 不产生任何 LLM/工具事件 → **handler 收到的全部 LLM/工具事件必然来自 coding/execution 内层子图**，归属断言天然干净（双保险：另按 system prompt 身份短语 + 工具名归属）。
- **核心被测语句**：`graph.invoke(initial_state, {**cfg, "callbacks": [handler]})`（app.py 批次 4 的同款注入点）。
- handler：最小计数型 `SpikeCallbackHandler(BaseCallbackHandler)`，记录 `on_chat_model_start` / `on_llm_start`（防御兜底）/ `on_llm_end` / `on_tool_start` 事件及 metadata/tags。

链路：`START → intake(fake) → analysis(fake) → scout(fake) → planning(fake, approved) → coding(真) → execution(真) → reporting(真) → END`，单次 invoke 全程无 interrupt（`test_spk1_graph_reaches_reporting` 自检 `current_step == "reporting"` + `report_path` 非空）。

## 路径 1：coding（经 `_make_react_wrapper` → `subgraph.invoke`）——传播：**是**

用例：`test_spk1_coding_path_callbacks_propagate`（PASS × 3 连跑）。

| 事件 | 断言 | 观测 |
|---|---|---|
| `on_chat_model_start`（system prompt 含"资深机器学习复现工程师"） | ≥2 | 2（tool_call 轮 + final 轮） |
| `on_llm_end`（run_id 回查归属 coding） | ≥2 | 2 |
| `on_tool_start`（tool_name == `write_code_file`） | ≥1 | 1 |

## 路径 2：execution（裸 `subgraph.invoke`）——传播：**是**

用例：`test_spk1_execution_path_callbacks_propagate`（PASS × 3 连跑）。

| 事件 | 断言 | 观测 |
|---|---|---|
| `on_chat_model_start`（system prompt 含"复现执行工程师"） | ≥2 | 2 |
| `on_llm_end`（run_id 回查归属 execution） | ≥2 | 2 |
| `on_tool_start`（tool_name == `run_in_sandbox`） | ≥1 | 1 |

两条路径的传播机制一致：LangGraph 1.x 节点执行时以 `set_config_context` 将 child config 写入 `var_child_runnable_config` contextvar；节点函数体内的裸 `subgraph.invoke(initial)`（及内层 `llm.invoke` / `tool.invoke`）经 `ensure_config()` 合并该 contextvar，callbacks 自动继承——与架构 §4 Q-S5-8 论证完全吻合。

## 元数据实证（CP-0.1-2，喂 T-S5-4-1 ActivityEvent schema 落地）

用例：`test_spk1_event_metadata_availability`（PASS × 3 连跑）。

### 实证 1：`metadata["langgraph_node"]` 可取，但值是**内层子图节点名**（关键落地修正）

- `on_chat_model_start` 观测：`langgraph_node == "reasoning"`（全部）；
- `on_tool_start` 观测：`langgraph_node == "tool_executor"`（全部）；
- **外层主图节点名（coding / execution）不在 `langgraph_node` 里**，但可从以下两键任一恢复（断言已固化）：
  - `metadata["checkpoint_ns"]`：`"coding:<uuid>"` / `"execution:<uuid>"` → `split(":")[0]`；
  - `metadata["langgraph_checkpoint_ns"]`：层级全路径 `"coding:<uuid>|reasoning:<uuid>"` → 首段 `split(":")[0]`。
- metadata 完整键集（观测样本）：`checkpoint_ns / langgraph_checkpoint_ns / langgraph_node / langgraph_path / langgraph_step / langgraph_triggers / ls_integration / ls_model_type / ls_provider / thread_id`（`thread_id` 也在，per-thread handler 归属还有第二通道）。

> **T-S5-4-1 落地建议**：`ActivityEvent.node` 取 `metadata.get("checkpoint_ns", "").split(":")[0]`，空则回退 `metadata.get("langgraph_node", "")`——架构 §4 schema 注释"node: metadata['langgraph_node']，取不到时 ''"建议按此实况微调（属实现细节，不动 schema 五字段本身）。

### 实证 2：`on_tool_start` 入参可做 ≤120 字符摘要

- `on_tool_start` 同时拿到 `input_str`（str）与 `inputs`（dict，结构化入参），二选一皆可；
- 摘要样例（已断言 ≤120 且含工具名）：`write_code_file({'path': 'train.py', 'content': "print('spk1')\n"})`。

### 实证 3：`on_llm_end` 文本可截断预览

- `response.generations[0][0].message.content` 可取、可 `[:160]` 截断；
- 样例：`<result>{"entry_script": "train.py", "files_written": ["train.py"], "notes": nul…`；
- tool_call 轮 content 为空串（剧本 AIMessage(content="")），空文本轮次 T-S5-4-1 采集时按 kind="llm" 空 text 处理或跳过即可。

### 附带实证（T-S5-4-1 实现约束）

- **`on_llm_end` 事件本身不携带 metadata/messages**（langchain callbacks 契约如此）：node 归属须在 `on_chat_model_start` 时缓存 `run_id → metadata` 映射、`on_llm_end` 按 `run_id` 回查——spike handler 已示范该模式，3 连跑归属恒 `{coding, execution}` 无泄漏。

## 遗留与提示

1. **版本敏感性**：传播依赖 langgraph `set_config_context` + langchain-core `ensure_config` 的 contextvar 合并；库升级时复跑本套件（~1s）即可复验，已在环境栏标注。
2. **app.py worker 线程注意点（批次 4 实现时验证）**：本 spike 在 pytest 主线程 invoke；app.py 的 worker 线程注入点（app.py:169/197）为 config 显式传参（`{**config, "callbacks": [handler]}`），callbacks 走 config 而非跨线程 contextvar，机制上不受线程边界影响——T-S5-4-3 收口时以真实 UI 链路复证。
3. 本报告结论为主路径，**dev-plan §7 无需勘误注记**；dev-plan §4 T-S5-0-1 三个检查点（CP-0.1-1/2/3）达成，勾选由主控收口时回写（本任务文件边界禁改 dev-plan）。

## 交付物

- `tests/test_sprint5_spk1_callbacks_spike.py`（4 用例：1 链路自检 + 2 路径传播 + 1 元数据实证；不标 e2e，纯 mock 默认回归常驻）
- 本报告
