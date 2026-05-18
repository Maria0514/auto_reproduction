# Sprint 1 D1 — `core/graph.py` 单元测试报告

- **日期**：2026-05-17
- **执行人**：@全栈开发代理
- **范围**：D1 任务（LangGraph 主图骨架）单元测试 + 全量回归
- **测试入口**：`tests/test_graph.py`（13 个用例，纯单测，无真实 LLM/SDK 调用）
- **依据**：`docs/sprint1/dev-plan.md` 第 751-757 行 D1 自测检查点

---

## 1. 测试目标

验证 `core/graph.py`：
- 7 个业务节点（paper_intake / paper_analysis ReAct wrapper + 5 个占位）正确注册到 StateGraph
- 顺序边 `START → paper_intake → paper_analysis → resource_scout → planning → coding → execution → reporting → END` 连通
- 默认 checkpointer 通过懒导入 `core.checkpointer.get_checkpointer` 注入（避免循环依赖）
- 占位节点纯 pass-through（返回 `{}`，不污染上游写入）
- 全链路 `graph.invoke(state, config)` 可执行（mock ReAct wrapper 以避开真实 LLM 调用）

---

## 2. 单测覆盖与结果

| # | 用例 | dev-plan 检查点 | 结果 |
|---|------|-----------------|------|
| 1 | `test_build_graph_returns_compiled_graph` | CP1：返回 CompiledGraph 实例 | PASS |
| 2 | `test_graph_contains_seven_business_nodes` | CP2：图含 7 个节点（节点名集合一致） | PASS |
| 3 | `test_paper_intake_is_react_wrapper` | CP3：paper_intake 是 ReAct wrapper（`__name__ == react_wrapper_paper_intake`） | PASS |
| 4 | `test_paper_analysis_is_react_wrapper` | CP3：paper_analysis 是 ReAct wrapper | PASS |
| 5-9 | `test_placeholder_nodes_return_empty_dict[resource_scout/planning/coding/execution/reporting]` | CP4：5 个占位节点返回 `{}` | PASS×5 |
| 10 | `test_passthrough_helper_returns_empty_dict` | CP4 补充：内部 `_passthrough` helper | PASS |
| 11 | `test_build_graph_with_mock_checkpointer` | CP5：MemorySaver 编译成功 | PASS |
| 12 | `test_build_graph_default_checkpointer_lazy_import` | CP5 补充：默认 checkpointer 懒导入路径正确 | PASS |
| 13 | `test_full_graph_invoke_with_patched_react_wrappers` | CP6：全链路 `graph.invoke` 跑通（patch ReAct wrapper） | PASS |

D1 全部 6 个自测检查点 → 全绿。

执行命令与结果：
```
python -m pytest tests/test_graph.py -v
======================== 13 passed, 1 warning in 1.60s =========================
```

---

## 3. 实现取舍说明

**CP6 全链路 invoke 的 patch 策略**
- 在 `core.graph` 命名空间用 `unittest.mock.patch.object(graph_module, "paper_intake", fake_fn)` 替换，而非直接 patch 子模块属性。原因：`build_graph()` 在 `add_node` 时存的是 `core.graph` 模块导入时绑定的引用，子模块层面的 patch 无法穿透到已注册的 StateGraph 内部。
- ReAct wrapper 被替换为返回固定 dict 的轻量函数，避免触发真实 LLM/SDK 调用；同时保留对 `state` 字段流动的断言（fake_paper_analysis 内验证 paper_intake 写入的 `paper_meta` 已合入 state），从而同步验证顺序边数据流。

**导入子模块用 `importlib.import_module`**
- 沿用 `tests/test_paper_intake.py` / `tests/test_paper_analysis.py` 已有模式：`core/nodes/__init__.py` 把 `paper_intake` / `paper_analysis` re-export 成包属性后，普通 `from core.nodes.paper_intake import ...` 在 Python 模块缓存中拿到的仍是子模块（首次加载已完成），但用 `core.nodes.paper_intake` 这个属性名取值会被包属性遮蔽。CP3 用 `importlib.import_module("core.nodes.paper_intake")` 显式拿子模块对象，再断言 `core.graph.paper_intake is paper_intake_module.paper_intake`，保证拿到的是 ReAct wrapper 本体而非被遮蔽的引用。

---

## 4. 全量回归

执行命令：
```
python -m pytest -v
```

结果：**39 passed, 1 warning in 179.41s (0:02:59)**

涵盖：
- `tests/test_graph.py`：13 项（本次新增）
- `tests/test_llm_client.py`：11 项
- `tests/test_paper_analysis.py`：1 项（含 11 个内部检查点）
- `tests/test_paper_analysis_e2e.py`：6 项（真实 LLM + 真实 deepxiv SDK，含 Prompt Cache 字节级幂等断言）
- `tests/test_paper_intake_e2e.py`：4 项（真实链路）
- `tests/test_react_base.py`：4 项

E2E 真实链路（paper_intake + paper_analysis 共 10 个用例）全绿，**无回归**。

---

## 5. 已知限制 / 后续 Sprint TODO

D1 仅实现骨架，按 dev-plan 设计将以下能力推迟到后续 Sprint，已在 `core/graph.py` 末尾留 TODO 注释：
- Sprint 2：`planning` 节点末尾接入 `langgraph.types.interrupt()` 实现人在回路审核
- Sprint 3：在 `planning` 之后添加条件路由（`execution_mode == CODE_ONLY` 时 coding → reporting 跳过 execution；FULL 模式时进入 coding → execution）
- Sprint 3：`execution` 之后加修复循环条件边（按测试结果回到 coding，最多 3 轮）
- Sprint 3：`coding` / `execution` 替换为 dev_loop 双 agent 协作子图

阶段 1 验收（端到端真实 arXiv ID 走 paper_intake + paper_analysis 链路并校验 SQLite 持久化）属于测试工程师代理职责，本次 D1 单测**不覆盖**，等阶段 1 验收专项执行。
