# 测试执行报告 - e2-acceptance（任务 E2 执行监控页独立验收）

- **日期**：2026-06-28 23:12（本地时区）
- **执行人**：@测试工程师代理
- **Sprint**：sprint3
- **触发原因**：Sprint 3 任务 E2（`ui/pages/execution_monitor.py` 执行监控页）独立验收 + 补强；不轻信开发自测，逐条运行时实证 CP-E2-1~5，重锤 dev_loop 决策面板三 payload 端到端契约。
- **commit**：119abbd（E2 产出 `ui/pages/execution_monitor.py` + `tests/test_sprint3_e2.py` 为未提交 untracked 文件）
- **并行隔离**：与 E3（结果报告页）验收并行；仅新建 `tests/test_sprint3_e2_reinforce.py` + 本报告；未碰 TODO/dev-plan/E3 文件/任何生产代码。

## 裁决：PASS

## 执行范围
- 命令：
  - `.venv/bin/pytest tests/test_sprint3_e2.py -q`（开发自测 26 用例基线复核）
  - `.venv/bin/pytest tests/test_sprint3_e2.py tests/test_sprint3_e2_reinforce.py -q`（E2 套件全量，连跑 3 次）
  - `.venv/bin/streamlit run app.py --server.port 8511 --server.headless true`（真实启动冒烟，用完即 kill）
  - 端到端契约重锤通过最小 self-loop StateGraph + InMemorySaver + `Command(resume=payload)` 驱动真实 `core/nodes/execution.py::execution`
- 覆盖用例：`tests/test_sprint3_e2.py`（26，开发）+ `tests/test_sprint3_e2_reinforce.py`（44，本次新建）= **70**
- 是否包含 e2e：否（省 deepxiv 配额；端到端 resume 重锤用 mock sandbox + InMemorySaver，零 LLM/deepxiv）

## 结果摘要
- 通过：70（开发 26 + 补强 44）
- 失败：0
- 跳过：0
- 警告：1（LangChainPendingDeprecationWarning，langgraph 库级 pending deprecation，sp1 即存非新引入，全项目级遗留待观察）
- 稳定性：E2 套件连跑 3 次 70×3 全绿 0 flaky（1.6s/次；interrupt/checkpointer 用例用唯一 uuid4 thread_id 防串）
- 总耗时：约 1.64s/次

## CP-E2-1~5 逐条运行时实证结果

| CP | 结论 | 实证方式 |
|----|------|---------|
| **CP-E2-1** | PASS | `render` callable + `render_execution_monitor_page is render` + `__all__` 约定；`app._PAGE_MAP[STREAMLIT_PAGE_EXECUTION] == ("ui.pages.execution_monitor","render_execution_monitor_page")` 运行时核对；**用真实 `app.main()` dispatch** 驱动进入执行监控页（带 dev_loop_failure interrupt 的 widget 最密路径）无 exception，决策面板真渲染。 |
| **CP-E2-2** | PASS | `_fix_loop_progress_text` 纯函数边界（N==1/2/3、越界封顶不出现「第 4/3 轮」、N==0/非数/None→「尚未进入修复循环」）+ AppTest 渲染断言「修复第 2 / 3 轮」+ 阶段名「执行验证」+ 每轮摘要；`_summarize_fix_history` 单轮/多轮/缺字段/非 dict 跳过/None。 |
| **CP-E2-3（命门）** | PASS | 见下「端到端契约重锤结论」。 |
| **CP-E2-4** | PASS | `_logs_truncated` 双探测路径穷举：路径1（顶层 `output_truncated` 真值，独立生效）+ 路径2（logs 文本含 `output_truncated`/`日志已截断`/`[truncated]` 标记，独立生效）+ 两路 OR（顶层 False 但 logs 有标记仍截断）+ 反证不误报（普通日志/falsy 0/空 dict）+ 防御非 dict/None/list/int；AppTest 两路径均渲染原生 `st.warning`「日志已截断」。**核实 `ExecutionResult` TypedDict 确无 `output_truncated` 字段**（`__annotations__` 断言），该字段是 `sandbox.local_venv.SandboxRunResult` 的（dataclass fields 断言）→ 双探测有正当性。 |
| **CP-E2-5** | PASS | `_should_jump_to_report` 真值表穷举：全满足→True；interrupt 优先不跳；current_step 非 reporting（coding/execution/cancelled/空/start/缺失）均不跳；report_path 空/None 不跳；防御 None/非 dict→False；AppTest 实证 reporting+report_path 非空+非 interrupt → `current_page == STREAMLIT_PAGE_REPORT`，反证 report_path 空/interrupt 时不跳。 |

## dev_loop 决策面板三 payload 端到端契约重锤结论（CP-E2-3 命门）

**重锤设计**：不止 mock 捕获 `resume_with` 实参，而是把本页 `_build_decision_payload` 真实产出的三种 payload 喂给真实 `core/nodes/execution.py::execution` 节点（最小 self-loop StateGraph + InMemorySaver + `Command(resume=payload)`，mock sandbox 让首回合失败触发 interrupt#2），断言三 payload **真能被正确路由到三态**：

- **terminate**（`{"decision":"terminate"}`）→ `user_fix_decision=="terminate"` + `current_step=="cancelled_by_user"`（→ END）；resume 重跑 `prepare_venv` 副作用恒==1（幂等）。
- **export_code**（`{"decision":"export_code"}`）→ `user_fix_decision=="export_code"` + `degraded_nodes` 含 `execution` + `_dev_loop_route` 已清空（→ reporting）；sandbox 恒==1。
- **revise_plan**（`{"decision":"revise_plan","user_feedback":<文本>}`）→ `user_fix_decision=="revise_plan"` + `fix_loop_count==0`（清零，回问点2）+ `fix_loop_history` **完整保留**（2 条历史无丢失）+ `reproduction_plan.approved is False` + 本页传入的 `user_feedback` 真织进节点 `_planning_user_feedback`（→ planning）；sandbox 恒==1。
- **revise_plan 空 feedback**（`{"decision":"revise_plan","user_feedback":""}`，本页对空文本框兜底空串）→ 端到端不崩，节点 `decision.get("user_feedback") or ""` 兜底，`_planning_user_feedback` 仍生成修复上下文框架。
- **三态互异终态**：同一驱动下三 payload 导向三个互斥终态（cancelled / approved=False / degraded），无串台。

**契约守门双向红线验证（本页常量 == 节点取值）**：
- `{_DECISION_TERMINATE, _DECISION_REVISE_PLAN, _DECISION_EXPORT_CODE}` == `_build_dev_loop_interrupt_payload(...)["options"]`（集合相等 + 数量==3），独立 mutation 探针确认若任一端改取值（如 export_code→download_code）集合即不等会变红。
- `_INTERRUPT_KIND_DEV_LOOP == execution.INTERRUPT_KIND`，且节点 payload **实际写入**的 `interrupt_kind` 值也等于本页常量（端到端值一致，非仅常量名一致）。
- `_route_user_fix_decision` 对本页三决策值均有**专门分支**（非 fallthrough 兜底 terminate）：export_code 走降级分支 vs 臆造值 `bogus_value` 走兜底 cancelled，行为不同 → 证明 export_code 不是被兜底成 terminate。

**结论**：决策面板三 payload 端到端契约**真闭环**，UI 与 execution 节点 interrupt#2 resume 解析强一致，无臆造决策值；契约守门测试能在任一端改取值时变红。

## `execution_errors` 键名核实

- **核实属实**：`execution.py::_build_dev_loop_interrupt_payload`（L699）interrupt#2 payload 的失败清单键名实为 `execution_errors`（值=`list(exec_result.get("errors") or [])`），payload 顶层**无** `execution_result` 嵌套。任务描述措辞「`execution_result.errors`」为口径偏差，开发已正确按实际键 `execution_errors` 优先读取。
- **E2 页面双路径核实**（`_render_dev_loop_decision_panel` L446-449）：优先 `payload.get("execution_errors")`；兜底 `payload.get("execution_result",{}).get("errors")`。AppTest 实证：① 仅有 `execution_errors` → 条目渲染；② 仅有 `execution_result.errors`（兜底措辞）→ 兜底渲染；③ 两键都无 → 三按钮仍渲染不崩；④ payload 为 None → `payload or {}` 兜底仍渲染三按钮不崩。两路都不崩，与节点实际写入键无偏差。

## shadcn iframe 处理核实

核心终态/降级/截断/决策文案全部改用原生 `st.error/warning/info`（AppTest 可见可断言，与 sp2 范式一致），shadcn `ui.*` 仅用于按钮/accordion 等非关键路径：
- worker_error / state.error / 决策面板顶部提示 → 原生 `st.error`（`at.error` 可见，分别断言「工作线程异常」「致命错误」「自动修复未通过」）；
- cancelled_by_user / degraded_nodes / 日志截断 → 原生 `st.warning`（`at.warning` 可见，断言「任务已终止」「降级节点 + execution」「日志已截断」）；
- 失败上下文摘要字段（error_category/error_summary/fix_hint/representative_stderr/已尝试修复回合数）→ `st.markdown`/`st.code`（`at.markdown`/`at.code` 可见）。

## E2 未碰生产代码核实（git diff）

- `ui/pages/execution_monitor.py` 为**新建 untracked 文件**（`git status --short` 显示 `??`）= E2 唯一生产产出。
- `app.py` 的 `git diff HEAD` 中 `execution_monitor` 仅出现在 `_PAGE_MAP` 路由条目 + docstring → 均归属 **E1**（任务 E1 已落地两页路由预留），非 E2。
- `core/nodes/planning.py` 的 `git diff` 仅 `interrupt_kind="planning"` 一行 + 2 行注释 → 归属 **D1**，非 E2。
- **结论**：E2 严格遵守任务边界，只新建 `ui/pages/execution_monitor.py`，未碰 app.py/graph.py/planning.py/execution.py 等任何生产代码。

## 补强用例（新建 tests/test_sprint3_e2_reinforce.py，44 条）

| 组 | 条数 | 覆盖 |
|----|------|------|
| G1 端到端契约重锤 | 5 | 三 payload 真实 execution 节点 resume → 三态 + revise_plan 空 feedback + 三态互异终态（sandbox 恒 1 幂等） |
| G2 契约守门双向红线 | 3 | 本页常量 == 节点 options（集合+数量）/ INTERRUPT_KIND 双侧 == payload 实际写入值 / 专门分支非兜底 |
| G3 不误展示决策面板 | 4 | planning interrupt 跳 review / interrupt_kind None / is_interrupted=False 残留 kind 不误判 / dev_loop_failure 正面 |
| G4 fix_loop_history 渲染边界 | 6 | 单轮/多轮顺序/缺字段+非 dict/空不渲染区块/AppTest 多轮渲染/面板内渲染历程 |
| G5 output_truncated 双探测 | 7 | 路径1独立/路径2独立/OR 语义/反证不误报/防御非 dict/AppTest 两路径/TypedDict 无该字段反证 |
| G6 跳转真值表补强 | 4 | report_path 空白串口径固化/current_step 多形态/interrupt 覆盖/AppTest 全真路径 |
| G7 execution_errors 键名 | 5 | 节点 payload 用 execution_errors / 面板优先读 / 兜底 execution_result.errors / 两键无不崩 / payload None 不崩 |
| G8 原生组件可测性 | 6 | worker_error/state.error/cancelled/degraded/截断/决策面板顶部 均原生 st.error/warning |
| G9 决策按钮端到端点击 | 2 | revise 空 feedback 点击注入空串 / terminate 点击不夹带 user_feedback 键 |
| G10 失败上下文摘要字段 | 2 | category/summary/fix_hint/stderr 渲染 / 面板内复用进度文案 |

## 失败排查

无。70/70 全绿，连跑 3 次 0 flaky。

## streamlit 冒烟结果

- `streamlit run app.py --server.port 8511 --server.headless true`：Uvicorn 在 8511 启动成功，日志无 traceback/exception。
- HTTP 探测：`/healthz` 200，`/` 200。
- 页面 wiring：`_PAGE_MAP[STREAMLIT_PAGE_EXECUTION]` 正确指向 `ui.pages.execution_monitor:render_execution_monitor_page`。
- **集成 BUG 高发区核查（D3 教训）**：用真实 `app.main()` dispatch 驱动进入 dev_loop_failure 决策面板（widget 最密路径：3 按钮 + text_area + 多 expander），无 `StreamlitDuplicateElementKey`、无任何 exception，3 决策按钮 key 齐全。
- 进程清理：冒烟完毕已 `pkill` 8511 进程，`ps` 确认 0 残留（不与并行 E3 验收冲突）。

## 任何生产 BUG 或偏差

- **零生产 BUG**。
- **零文档硬偏差**（任务描述「`execution_result.errors`」口径 vs 实际键 `execution_errors` 已由开发正确处理，属措辞偏差非 BUG）。
- 1 项可接受口径记录：`_should_jump_to_report` 用 `bool(report_path)`，纯空白串 `"   "` 为 truthy 会判定跳转（已用 `test_g6_jump_report_path_whitespace_is_truthy_string` 固化此行为）。reporting 节点不会写纯空白路径（C2 落 `report.md` 真路径），非实际风险；记此以便回归感知口径变化。

## 后续动作
- 全量回归数字本报告**留空**：并行期不跑全量（会扫到 E3 半成品补强文件），由主控在 E2/E3 两验收完成后统一跑零退化回归。本次仅标注 E2 套件数字：`tests/test_sprint3_e2.py`(26) + `tests/test_sprint3_e2_reinforce.py`(44) = **70 passed**，连跑 3 次 0 flaky。
- 项目级遗留 warning（LangChainPendingDeprecationWarning，langgraph 库级 sp1 即存）继续待观察，非本任务引入。
- e2e（真实 LLM + sandbox + 修复循环 + interrupt#2 三态端到端）留 F 阶段凭证补跑。
