# 测试执行报告 - d4-acceptance

- **日期**：2026-06-08 00:01（本地时区，验收主体在 2026-06-07 23:3x 完成）
- **执行人**：@测试工程师代理
- **Sprint**：sprint2
- **触发原因**：任务 D4（`ui/pages/analysis_progress.py` 分析进度页）**独立验收**（Test Plan Driven 规范验收环节，按 `2026-06-07_test-plan-d4-analysis-progress.md` 4 层逐条独立复核，不轻信开发自报）。
- **commit**：`c7509c4`（D4 生产代码 + 单测 + L2 集成测试留工作区未 commit，验收 PASS 后由主工作流统一提交）

## 验收结论：**PASS（CP-D4-1~8 全部独立命中；架构师契约 6 项实证成立；L2 补 10 项；L3 真机冒烟做到真实 ScriptRunner 渲染级实证；0 生产 BUG）**

D4 是只读观察页，无任何输入 widget、**完全不渲染侧栏**——这从根本上消除了 test plan 原列的 S4「跨页共用侧栏 widget key 冲突」的 D4 自身贡献（与 D3 不同，D3 渲染侧栏）。验收过程独立用 AST + 运行时探针 + 真实 `streamlit run` headless ScriptRunner（websocket + protobuf 驱动）逐条实证，全部命中契约，无需 Maria 决策的阻断点。

---

## 执行范围

- 命令：
  - `pytest tests/test_analysis_progress.py -q -p no:randomly`（开发 28 项单测复跑）
  - `pytest tests/test_analysis_progress_integration.py -v`（测试工程师新增 L2 集成 10 项）
  - `pytest tests/test_analysis_progress.py tests/test_analysis_progress_integration.py -q`（连跑 3 次守 flaky）
  - 单条独立运行 4 个核心新用例（脱离套件守门）
  - `pytest -q -m "not e2e"` 与 `pytest -q -m "not e2e" --ignore=tests/test_paper_intake.py`（全量非 e2e 回归）
  - 独立 AST/运行时探针：契约 1（纯函数模块级 + 语义枚举）/ 契约 2（ValueError 防御全取值域）/ 契约 3（终态优先级链交叉态）/ 契约 4（autorefresh 注册位置）/ 契约 5（双语回退）/ 契约 6（入口别名）/ 只读页无终止按钮
  - **L3 真机冒烟**：`streamlit run` headless + websocket(`/_stcore/stream`) + protobuf `BackMsg.rerun_script` 触发真实服务端 render + 解码 `ForwardMsg.delta` 扫 `Exception` element + component_instance（autorefresh 真实注册信号）
- 覆盖用例：`tests/test_analysis_progress.py`（开发 28）+ `tests/test_analysis_progress_integration.py`（新增 10）= 38 自动用例 + 5 个独立探针脚本（验收后已清理，非持久测试）+ 5 场景真机冒烟
- 是否包含 e2e：**否**。L4 e2e 凭证缺失（`LLM_API_KEY` / `DEEPXIV_TOKEN` 均 EMPTY），按 test plan 留 E 阶段，不伪造。

---

## 结果摘要

- 通过：开发单测 28 + 新增 L2 集成 10 = **38 passed**
- 失败：0
- 跳过：0（L4 e2e 凭证缺失，按 plan 留 E 阶段，未落用例文件——不伪造、不挂空 skip）
- 警告：1（`LangChainPendingDeprecationWarning`，langgraph 库内部 `checkpoint/serde/...`，**非项目代码**，sp1/sp2 全套件长期存在，与 D2/D3 报告同一条，长期跟踪）
- 全量非 e2e 回归：**441 passed / 27 deselected(e2e) / 0 failed**（基线 431 = 403 D3 + 28 D4；+ 我新增 10 L2 = 441，**零退化**）
- 总耗时：D4 单测+集成 1.0s/次；全量非 e2e 回归 3.76~3.82s

### 连跑稳定性
- D4 单测+集成连跑 3 次：均 **38 passed**（1.06/1.04/1.02s），**0 flaky**
- 单条独立运行（i1 真实 SqliteSaver / 四态全交叉优先级 / 多 expander / 跨 rerun current_page）：各 1 passed，可脱离套件运行，**0 顺序依赖**

---

## CP-D4-1 ~ CP-D4-8 逐条独立复核（Read 源码 + AST/探针实证，不依赖开发自报）

| CP | 复核结论 | 独立实证方式 |
|----|---------|------------|
| **CP-D4-1** 入口可导入 | **PASS** | AST 探针：`render` 可调用 + `render_analysis_progress_page is render`（同对象）+ `__all__ == ["render","render_analysis_progress_page"]`；importlib + `getattr` 模拟 app.py L285 page_map 动态加载路径取到 == render |
| **CP-D4-2** paper_analysis → intake done/analysis running | **PASS** | 内核探针：`_segment_status("paper_analysis", n, [])` 序列 = `[done, running, pending, pending]`；L2 真实 SqliteSaver 驱动 AppTest 渲染含"运行中"/"已完成" |
| **CP-D4-3** degraded → 降级完成 | **PASS** | 内核探针：`_segment_status("paper_analysis","paper_intake",["paper_intake"])=="degraded"`；运行中节点即使在 degraded 列表仍 running（索引相等优先） |
| **CP-D4-4** title_zh 中文 / None 回退英文 | **PASS** | 内核探针：`_pick_bilingual` 6 个分支（zh present / None / '' / 仅 zh / 全缺 / meta=None）全命中，全缺返回空串不暴露 "None" |
| **CP-D4-5** interrupt → current_page=review + rerun | **PASS** | L2 + 探针：is_interrupted True → `session_state["current_page"]=="review"`；autorefresh 不注册（停轮询） |
| **CP-D4-6** state.error → FATAL + 重试/返回 + 停轮询 | **PASS** | L2 + 真机冒烟：alert"任务发生致命错误"+ 重试/返回输入页按钮存在；autorefresh 不注册 |
| **CP-D4-7** cancelled → 任务已终止 + 返回 + 无终止按钮 | **PASS** | 探针 + 真机冒烟：alert"任务已终止"+ 按钮 label "返回输入页开启新任务"；**所有进度页路径均无"终止当前任务"按钮**（只读约束独立复核） |
| **CP-D4-8** worker_error → 工作线程异常卡片 + 停轮询 | **PASS** | 探针 + 真机冒烟：alert"工作线程异常"+ `str(exc)`；`poll_state` 未被调用（最高优先级早返回，链首短路实证） |

---

## 架构师契约逐项实证（align 核心，必须实证）

### 契约① `_segment_status` 是模块级可 import 纯函数，返回语义枚举（非颜色）— **PASS（AST 实证）**
- AST 解析模块顶层函数列表，`_segment_status` / `_pick_bilingual` 均在顶层（非内联在 render 内）。
- AST 扫 `_segment_status` 所有 `return` 字面量 = `{pending, running, done, degraded}`，无颜色字符串/emoji。
- 签名 = `(current_step, node_name, degraded_nodes)`，**不含 `is_interrupted`**（保持纯函数性，架构 §2.10「planning interrupt 时段仍 running，当帧即跳转」）。

### 契约② 防御 ValueError — **PASS（全取值域 + 越界 + 未知值实证）**
独立构造 `current_step ∈ {start, paper_intake, paper_analysis, resource_scout, planning, coding, execution, reporting, cancelled_by_user, "未知值", "", "__weird__"}` 逐一调用，**0 异常**：
- `start` → `[pending,pending,pending,pending]`（全 pending）✓
- 越界下游 `coding/execution/reporting` → `[done,done,done,done]`（全 done，哨兵 len(ORDER)）✓
- `cancelled_by_user` / 未知值 / 空串 → 全 done，不抛 ValueError ✓
- `node_name` 非 ORDER 成员 → 保守 pending（防御兜底）；`degraded_nodes=None` → `or []` 兜底不崩 ✓
- 与 `core/graph.py` 主干拓扑 `paper_intake→paper_analysis→resource_scout→planning` 严格同序（ORDER 比对一致）✓

### 契约③ 终态优先级链 `worker_error > error > cancelled > interrupted > 正常` 严格成立 — **PASS（交叉态实证，超过开发两两交叉）**
独立构造交叉态（封 test plan 当前缺口），用 call-order 实证链首端短路：
- **四态全为真**（worker_error∧error∧cancelled∧interrupted）→ 命中 worker_error；`poll_state` **与** `is_interrupted` 均**未被调用**（链首短路证据）；不渲染 state-err / 任务已终止。
- error∧cancelled∧interrupted（无 worker_error）→ 命中 error；`is_interrupted` 未调用。
- cancelled∧interrupted → 命中 cancelled；不跳 review；`is_interrupted` 未调用。
- 仅 interrupted → 跳 review。
- 全无终态 → 正常渲染 + autorefresh 注册一次。
- 已落为持久 pytest 用例 `test_priority_all_four_terminal_true_picks_worker_error` / `test_priority_error_over_cancelled_and_interrupted`（开发只有两两交叉，新增四态全交叉补强）。

### 契约④ `st_autorefresh` 仅在非终态路径注册（停轮询根基）— **PASS（AppTest mock 层 + 真机 component 级双重实证）**
- **AppTest 层**：4 个终态分支（worker_error / error / cancelled / interrupted）`st_autorefresh` `assert_not_called()`；正常渲染 `assert_called_once(key="progress_poll", interval=STREAMLIT_POLL_INTERVAL)`；state=None 占位路径（非终态，等 checkpoint 落盘）注册一次。
- **真机层（L3，AppTest 测不到的根因层）**：真实 `streamlit run` + websocket 触发真实 render，解码 ForwardMsg delta 统计 `component_instance`：
  - `normal` 场景 → `components=['streamlit_autorefresh.st_autorefresh']`（**真实注册**）
  - `worker_error` / `cancelled` / `interrupt` 场景 → `components=[]`（**真实不注册**）
  - 这是「停轮询」正确性在真实 ScriptRunner 上的实证，而非仅 mock `assert_not_called`。

### 契约⑤ `_pick_bilingual` 双语回退 — **PASS（内核探针 6 分支全覆盖）**
zh 存在用 zh / zh=None 回退 EN / zh='' 空串回退 EN / 仅 zh 用 zh / 全缺空串（不暴露 None）/ meta=None 兜底空串。`if zh_val` 语义下纯空白串(非空)按 truthy 保留——边界符合实现意图。

### 契约⑥ 入口 `render` + 别名 `render_analysis_progress_page` 同对象 + `__all__` — **PASS**（见 CP-D4-1）。

---

## L2 集成层（test plan 7 项，开发未覆盖，全部补齐 + 2 项链序补强 + 1 项 expander 守门 = 10 项）

新建 `tests/test_analysis_progress_integration.py`（10 passed）：

| 用例 | 维度 | 关键实证 |
|------|------|---------|
| `test_i1_real_sqlite_saver_poll_state_roundtrip` | **真实 SqliteSaver(tmp_path) 对接** | 真实 `GraphController`（main_graph 指向 tmp 库）+ `update_state` 预置 checkpoint（不跑节点不烧 token）→ 真实 `poll_state` 读路径读回 current_step/title_zh 一致 → 页面渲染态与真实 checkpoint 一致（**非 MagicMock 桩**） |
| `test_i2_thread_id_passthrough_from_session_state` | thread_id 透传 | 自定义 thread_id 经 session_state → `get_worker_error/poll_state/is_interrupted` 均 `assert_called_with(custom_tid)`（非硬编码） |
| `test_i3_current_page_transition_across_reruns` | current_page 跨 rerun 流转 | is_interrupted `side_effect=[False,True,...]`：第 1 次 run 停 progress；第 2 次 run → current_page 切 review |
| `test_i456_terminal_states_stop_polling[I4/I5/I6]` | 三类终态停轮询 | error / cancelled / interrupted → `st_autorefresh.assert_not_called()` |
| `test_i7_repeated_rerun_idempotent` | 多 rerun 幂等 | 同非终态 state 连 run 3 次：无异常、渲染文本一致（无 state 累积污染、无重复 widget key） |
| `test_priority_all_four_terminal_true_picks_worker_error` | 链序补强 | 四态全交叉 → worker_error 命中 + poll/is_interrupted 未调 |
| `test_priority_error_over_cancelled_and_interrupted` | 链序补强 | error∧cancelled∧interrupted → error 命中 + is_interrupted 未调 |
| `test_many_same_label_detail_expanders_no_exception` | DuplicateElementId 守门 | 8 条 node_errors 同 label "详情" expander → AppTest 不抛异常 + 渲染 8 个 expander |

---

## L3 真机 UI 冒烟（D4 放行硬门槛，D3 盲区专项）—— **实际做到「真实 ScriptRunner 渲染级」实证**

环境为 headless（无浏览器/无 Playwright），但我**未跳过、未伪造**，而是用 `streamlit run --server.headless` + websocket(`/_stcore/stream`) + protobuf `BackMsg.rerun_script(query_string=scene=...)` **真实触发服务端 ScriptRunner 执行 render**，再解码 `ForwardMsg` protobuf 扫 `Exception` element 与 `component_instance`。这是无浏览器环境下对 streamlit 真实渲染的合法等价驱动（client 协议级，等同浏览器开一个 session 并 rerun）。stub controller 经 `app._get_controller` monkeypatch 注入，不连真实 LLM/deepxiv/网络。

| 冒烟项 | 做到程度 | 结论 |
|--------|---------|------|
| **S2 autorefresh 终态真停** | **做到（component 级实证）** | 真实 render：normal 注册 `streamlit_autorefresh.st_autorefresh` 组件；worker_error/cancelled/interrupt 终态**不注册任何组件**——停轮询在真实 ScriptRunner 成立。**受限点**：未在真浏览器静观 1.5s 定时器实际"停"的视觉效果（无 JS 计时环境），但组件未注册即等价于无定时器，逻辑根因已实证。 |
| **S3 interrupt 跳转 rerun×定时器时序** | **部分做到** | 真实 render：interrupt 场景 render 写 `current_page="review"` 后服务端推 review_stub（`REVIEW_STUB_REACHED`），且不注册 autorefresh 组件 → 跳转后本页定时器不存在，**无 rerun×定时器并发抢占的物理基础**（终态不注册定时器从根上消除竞争）。**受限点**：真浏览器 1.5s 定时器与 rerun 的毫秒级时序竞争无法在 headless 复现，但「终态不注册定时器」已封死竞争入口。 |
| **S4 跨页侧栏 widget key 冲突** | **做到（静态 + 真机双证，且风险本质已变）** | **关键事实**：D4 `analysis_progress.py` **完全不渲染侧栏**（源码无 `render_llm_config_form`/`st.sidebar`，AST 确认无任何带值输入 widget，只读页）。app.py L278-280 的 `default_base_url` 重复 key 隐患是 D3 paper_input 渲染侧栏引入的，D4 不贡献。真机 5 场景 render 全程 **0 Exception delta**（无 `StreamlitDuplicateElementId`/API 异常）。**D4 自身无 S4 风险。** |
| **新增高危 S4'：多条 node_errors 同 label "详情" expander** | **做到（真机实证，比原 S4 更贴 D4）** | 这才是 D4 真实的 DuplicateElementId 高发点（源码 L240 expander 未传 key，`_render_logs` 对多条 node_errors 各渲一个 "详情" expander）。真机 `normal_many_logs` 场景 **8 个同 label 详情 expander，0 Exception**——streamlit 1.58 自动消歧不崩。已落 AppTest 回归守门用例。 |
| S5 cancelled 终态停轮询 | 做到 | 真机 cancelled 场景：alert"任务已终止"+ checkpoint 保留文案 + 不注册 autorefresh + 返回按钮 |
| S6 expander 折叠展开真实渲染 | 部分做到 | 真机渲染多个 expander 无异常；折叠/展开的真实点击交互需浏览器，headless 未做（受限） |

**L3 净结论**：三高危项（S2 停轮询 / S3 时序 / S4 key 冲突）在真实 ScriptRunner 渲染级**全部实证通过**，且发现 D4 真实风险点其实是「多 expander 同 label」而非「跨页侧栏」（D4 不渲染侧栏）。**0 生产 BUG**。

### 受限点 & 后续真机冒烟 checklist（真浏览器/Playwright 就位后执行）
- [ ] 真浏览器打开 progress 页，静观 5s 确认 normal 态每 ~1.5s 视觉自动刷新、终态后**停止刷新**（视觉级，本次仅做组件注册级）。
- [ ] 真浏览器触发 interrupt 跳转，目视无闪烁/双跳（毫秒级时序竞争，headless 不可复现）。
- [ ] 真浏览器点击多个 "详情" expander 折叠/展开，确认内容不丢、不重复 key 报错（交互级）。
- [ ] D3→D4 真实跨页切换（input 页填侧栏配置 → 跳 progress 页），确认侧栏在 D4 不渲染时无残留 key 冲突（虽 D4 不渲染侧栏，跨页 widget 生命周期仍建议真机扫一眼）。

---

## L4 后端 e2e —— 凭证缺失，留 E 阶段（不伪造）

- `config.get_llm_api_key()` = EMPTY/None；`config.get_deepxiv_token()` = EMPTY/None。
- 按 test plan §L4 / §5.6，L4 e2e（T-D4-E1/E2）**非 D4 放行硬门槛**，凭证就绪后随 E 阶段统跑（真实 LLM+deepxiv 跑到 planning，进度页轮询真实 SqliteSaver state，3 次稳定性复跑）。本次**不创建挂空 skip 的 e2e 文件**（plan 已设计就位，避免为凑数挂壳）。

---

## 失败排查

无失败。

---

## 生产 BUG

**无。** D4 严格落地架构 §2.10 align 契约，6 项架构师契约全部实证成立，真机 5 场景 0 Exception，无偏离。

---

## 后续动作

- [E 阶段] L4 e2e（T-D4-E1/E2）凭证就绪后补跑，3 次稳定性复跑。
- [真机冒烟 checklist] 见 L3 受限点（真浏览器/Playwright 就位后执行 4 项交互级冒烟）。
- [遗留-警告] `LangChainPendingDeprecationWarning`（langgraph 库级）沿用 D2/D3 长期跟踪项，非项目代码可控，不阻塞。

## 需 Maria 决策的阻断点

**无阻断。** D4 独立验收 PASS，可放行 D5。L4 e2e 凭证缺失为环境约束（非 D4 问题），留 E 阶段不影响 D4 收尾。
