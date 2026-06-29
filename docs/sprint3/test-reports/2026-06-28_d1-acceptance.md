# 测试执行报告 - d1-acceptance

- **日期**：2026-06-28 20:10（本地时区）
- **执行人**：@测试工程师代理
- **Sprint**：sprint3
- **触发原因**：任务 D1（`core/graph.py` 路由编排 + `core/nodes/planning.py` interrupt_kind）独立验收——不轻信开发自测，逐条运行时实证复核 CP-D1-1~7 + interrupt#2 self-loop 命门重锤 + 红线核查 + 补强边界 + 回归零退化。
- **commit**：`119abbd`（D1 改动在工作树未提交：`core/graph.py` M / `core/nodes/planning.py` M / `tests/test_graph.py` M / `tests/test_sprint2_c1.py` M / `tests/test_sprint3_d1.py` 新增；待 Maria 统一 commit）

## 执行范围
- 命令：
  - `.venv/bin/pytest tests/test_sprint3_d1.py -q`（开发自测复现）
  - `.venv/bin/pytest tests/test_sprint3_d1_reinforce.py -q`（本次补强，连跑 3 次）
  - `.venv/bin/pytest tests/test_graph.py tests/test_sprint2_c1.py tests/test_sprint3_d1.py tests/test_sprint3_d1_reinforce.py -q`（D1 影响面）
  - `.venv/bin/pytest -q -m "not e2e" --ignore=tests/test_paper_intake.py`（全量非 e2e 回归）
  - 独立运行时探针脚本（CP-D1-1~6 逐条 + 编译图 branch mapping dump + `_route_after_planning` 相对 sp2 字节等价 diff）
- 覆盖用例：`tests/test_sprint3_d1.py`（CP-D1-1~7，36 用例，开发）+ 新建 `tests/test_sprint3_d1_reinforce.py`（H-1~3 重锤 + B-1~7 边界，33 用例）+ 受影响 sp1/sp2（`test_graph.py` / `test_sprint2_c1.py`）
- 是否包含 e2e：否（省 deepxiv 日配额，留 F 阶段）

## 结果摘要
- 通过：870（全量非 e2e；基线 837 + 补强 33，逐项吻合）
- 失败：0
- 跳过：25（与 C3 验收基线一致，e2e/凭证缺失等既有 skip，非新引入）
- 警告：1（`LangChainPendingDeprecationWarning`，langgraph 1.x 库级预存，sp1 即有，非 D1 引入；项目级遗留，待观察）
- deselected：28（e2e 默认排除）
- 总耗时：120.74s（全量回归）
- 连跑稳定性：D1 自测 + 补强连跑 3 次（36+33 / 69 / 69）**0 flaky**

## 裁决：**PASS**

### CP-D1-1~7 逐条独立实证结果

| CP | 内容 | 实证方法 | 结果 |
|----|------|---------|------|
| CP-D1-1 | 7 节点骨架不变性 | `build_graph()` 编译成功；`get_graph().nodes` 去 `__` 前缀后精确 == `{paper_intake, paper_analysis, resource_scout, planning, coding, execution, reporting}`，`len==7`，与 `{coding_only, dev_loop, exit_dev_loop}` 交集空集 | PASS |
| CP-D1-2 | 三节点真实现非占位 | `_passthrough` 已删（`hasattr` False）；`gm.coding is core.nodes.coding.coding` 且 `__module__==core.react_base`；`execution`/`reporting` is 真实现 + `__module__` 指向真实现模块；`execution({})` 走 code_output_dir 缺失降级返回非空 dict（打 WARNING）反证非占位 | PASS |
| CP-D1-3 | `_route_after_coding` 三形态 | Enum CODE_ONLY / str `"code_only"` / `.value` 三形态均 → `skip_execution`；FULL/None/缺省 → `to_execution` | PASS |
| CP-D1-4 | `_route_after_execution` 全路 | await→execution（命门）/ retry_coding→coding / revise_plan→planning / terminate+cancelled_by_user→end / cancelled_by_user 单独→end / export_code→reporting / 成功→reporting / 空兜底→reporting，8 路全覆盖 | PASS |
| CP-D1-5 | 修复回边为新增条件边 + 既有顺序边语义保留 | 真实编译图 branch mapping dump：planning 3 路 `{self:planning, next:coding, end:__end__}`、coding `{to_execution:execution, skip_execution:reporting}`、execution `{execution:execution, coding:coding, planning:planning, reporting:reporting, end:__end__}`、reporting→END | PASS |
| CP-D1-6 | planning interrupt_kind=="planning"（真实 interrupt 捕获 payload） | 最小 StateGraph 跑真实 interrupt + 捕获 `snapshot.tasks[].interrupts[].value`，payload 含 `interrupt_kind=="planning"`；`planning.py` git diff 实证仅 +3 行（1 键 + 2 注释），不动其他逻辑 | PASS |
| CP-D1-7 | mock 全链路 happy path | FULL（intake→…→execution→reporting→END）+ CODE_ONLY（coding→reporting 跳 execution，execution 未触达）+ retry_coding 修复回边（coding/execution 各 2 次） | PASS |

### 高风险重锤：interrupt#2 self-loop 命门（L-C3-01）——结论 PASS

任务书要求：**不能只验路由函数返回字符串 `"execution"`，要确认编译后的 graph 里 `add_conditional_edges` 映射真把 `"execution"` 指回 execution 节点形成自环**，否则路由对、图里没成边、interrupt#2 仍永不触发。

开发自测 `test_cp_d1_4_await_dev_loop_interrupt_self_loop` 只验路由函数返回 `"execution"`；`test_cp_d1_5_edges_structure` 验 `get_graph()` edges 含 self-loop。本次**补强用真实 `build_graph()` 编译图（不是简化 stub 图）端到端实证**（C3 补强用的是模拟 route，本次用真实 `_route_after_execution` + 与 `build_graph()` 逐字节相同的映射键接线）：

1. **真实编译图 branch mapping**（`compiled.builder.branches` 反射，非空校验已过）：`mappings["execution"]["execution"] == "execution"` —— self-loop 在生产 `build_graph()` 层真正成边（B-1 断言）。
2. **端到端命门**（H-1）：构造 hardware 不可修复失败，execution 首次进入 → 跑 sandbox → `return` 落盘 `execution_result` + 置 `_dev_loop_route="await_dev_loop_interrupt"`（不 interrupt，commit 边界）→ 真实 `_route_after_execution` 经映射 `{"execution":"execution"}` self-loop 重入 execution → guard `_has_committed_result_for_round` 命中跳 sandbox → 函数体内 `interrupt()` 真触发暂停（`"__interrupt__" in out`）。**`prepare_venv` 副作用恰 == 1**（首次 1，self-loop 重入跳过）。
3. **interrupt#2 payload**（H-1b）：self-loop 重入触发的 payload 携 `interrupt_kind=="dev_loop_failure"`，与 planning 的 `"planning"` 区分。
4. **resume 三态在真实图走对目的地 + resume 期间 sandbox 不重跑**（H-2/H-2b/H-2c）：terminate→END（`current_step=cancelled_by_user`）/ export_code→reporting（产 report_path）/ revise_plan→planning（`current_step=planning` + 出口清 `approved=False`），三态全程 `prepare_venv` 恒 1。
5. **retry_coding 修复回边在真实图成边**（H-3）：可修复失败（import）→ retry_coding → 真实映射 `{"coding":"coding"}` → 回 execution，第二次成功 → reporting，`prepare_venv==2`。

**结论**：D1 对 dev-plan L532 正文 / 架构 §2.5.3 字面遗漏的 `await_dev_loop_interrupt → execution` 一路，以 C3 交接（TODO L214 / dev-plan L492）为权威补全，落地正确——命门在**生产编译图层级**端到端实证成立，副作用幂等（恒 1）。

### 其它红线核查（全 PASS）

- **路由函数只读 state 不写**（must-fix-1 相关）：`grep -nE "state\[|state\.update|state\.setdefault|state\.pop|\.append\(|\.extend\(" core/graph.py` 零命中；补强 B-7 运行时反证（含 list 字段 state 调用三路由后 `id()` 不变、长度不变、内容不变、标量不变）。
- **零 reducer 红线**：`git status` 确认 D1 未碰 `core/state.py`（不在改动列表）；`grep -nE "Annotated|operator.add" core/state.py` 零命中。
- **code_only 区分点严格在 coding 出边，planning 3 路边零改动**：提取 `_route_after_planning` 可执行语句相对 sp2 graph commit（52a6f11）逐行 `diff` **字节等价**（`if current_step==cancelled_by_user→end` / `plan=...` / `return next if approved else self`）；补强 B-4/B-4b 反证 planning 出边对 `execution_mode` 完全不敏感（FULL 与 CODE_ONLY 同 approved 走同一路 `next`）。
- **路由完备性**：补强 B-2b 穷举 `_route_after_execution` 在 9 种 state 下的返回值，全部 ⊆ execution 映射键集合（防 KeyError 悬空路由）。

## 补强用例（33 条，新建 `tests/test_sprint3_d1_reinforce.py`）

- 重锤组（7 条）：H-1 真实编译图 self-loop 端到端 + sandbox==1；H-1b interrupt_kind==dev_loop_failure；H-2/H-2b/H-2c resume 三态真实图走对目的地 + sandbox 不重跑；H-3 retry_coding 修复回边真实图成边。
- 边界组（26 条）：B-1/B-2 真实编译图 branch mapping 键 vs 路由返回值一致性（命门键 `execution→execution` 直证）；B-2b 穷举返回值无悬空；B-3 await/retry 优先级防御（4 形态）；B-4/B-4b planning 出边 sp2 行为等价 + 对 execution_mode 不敏感（7+1 形态）；B-5/B-5b coding 出边 code_only 多形态（含非法值兜底 FULL，8+1 形态）；B-6/B-6b reporting 唯一通 END + 两 mode 汇聚 reporting；B-7 路由纯读不 mutate state list。

## 失败排查
无。全程 0 failed。

## 后续动作
- **无生产 BUG，无文档硬偏差**。
- **遗留（非阻断，已知会）**：dev-plan L532 正文 + 架构 §2.5.3 代码块字面**仍遗漏** `await_dev_loop_interrupt → execution` self-loop 一路（D1 已按 C3 交接 L-C3-01 正确补全并落单测/命门实证）。建议架构师后续顺手把 §2.5.3 代码块补上此路 + 把 docstring 标的「4 路」更正为实际 6 路（含 self-loop），以消歧；非阻断，不影响 D1 验收 PASS。
- **项目级遗留 warning**：`LangChainPendingDeprecationWarning`（langgraph 库级，sp1 起即存，非 D1 引入），持续观察。
- **下次跑测试触发条件**：E 阶段 UI（execution_monitor / result_report 两页）落地后做 D1+E 集成验收；F 阶段凭证就绪后补跑 e2e（code_only/full/retry 修复循环/interrupt#2 三态端到端）。
- **未 git commit**（待 Maria 统一）。
