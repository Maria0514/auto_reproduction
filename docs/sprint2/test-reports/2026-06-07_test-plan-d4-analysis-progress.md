# Test Plan: D4 `ui/pages/analysis_progress.py`（S2-06 分析进度页）

- **日期**：2026-06-07
- **作者**：@测试工程师代理
- **Sprint**：sprint2
- **类型**：**开发前测试设计文档（Test Plan Driven 首次落地，TODO L78-79）**
- **被测代码状态**：**`ui/pages/analysis_progress.py` 尚未实现**——本文是 test matrix / 测试策略，不含执行结果（无代码可跑）。开发完成后按本 plan 逐条补缺 + 独立验收，另出执行报告。
- **commit（plan 撰写基线）**：10e5392
- **环境核对**：`streamlit_autorefresh` 已装、`streamlit==1.58.0`、`config.STREAMLIT_POLL_INTERVAL=1500` 已就位。

---

## 1. 概述

### 1.1 D4 职责（dev-plan L1384-1431 + architecture §2.10）
进度页是**纯只读观察页**：autorefresh 每 1.5s rerun 一次 → `controller.poll_state(thread_id)` 拉最新 state → 渲染（论文卡片 + 4 段进度条 + 实时日志）→ 在 interrupt/error/cancelled/worker_error 四类终态做停轮询/跳转/卡片展示。**用户在本页不能改状态、无"终止任务"按钮**（仅 plan_review 页提供）。

### 1.2 被测契约（已对代码核实，不轻信文档）
| 契约点 | 来源（代码行号） | plan 采信值 |
|---|---|---|
| 入口函数名 | app.py L285 page_map `("ui.pages.analysis_progress", "render_analysis_progress_page")` | **以 app.py 为准 = `render_analysis_progress_page`**；CP-D4-1 写的 `render` 是 D3 同款"主名 `render` + 别名"约定 → 待 align #1 |
| poll_state 返回 | app.py L172-176 | `Optional[GlobalState]`，**可能为 None**（snapshot 不存在）——dev-plan 未覆盖 None 边界 |
| is_interrupted | app.py L178-187 | `bool`；snapshot.next 非空 + tasks 含 interrupt 才 True；图走到 END 时 False |
| get_worker_error | app.py L199-202 | `Optional[Exception]`（**异常对象，非 str**）——卡片渲染需 `str(exc)` |
| current_step 取值域 | core/nodes/*.py NODE_NAME + planning L563 + state.py L268 | `start`(初值) / `paper_intake` / `paper_analysis` / `resource_scout` / `planning` / `cancelled_by_user`；后续 sp3 还有 coding/execution/reporting |
| 节点拓扑顺序 | core/graph.py L131-134 | `paper_intake → paper_analysis → resource_scout → planning`（严格线性，进度条 4 段同序） |
| degraded_nodes | resource_scout.py L453-467 等 | `List[str]`，append 节点名（如 `["paper_intake"]`），判降级 |
| node_errors | state.py L189 / NodeError L140-148 | `List[NodeError]`，TypedDict 含 node_name/error_type/error_message/error_detail/timestamp 等 |
| paper_meta 双语 | state.py L57-59 | `title_zh / abstract_zh / tldr_zh`，均 `Optional`，缺失回退英文主字段 |
| 上游写入的 session_state | paper_input.py L305-307 | `thread_id`(非空) + `current_page="progress"`——进度页要消费 thread_id |

### 1.3 进度条状态机——**dev-plan 未明说的核心推断逻辑（必测）**
dev-plan L1407-1411 给了 4 态判定，但 `current_step` **只记录"最后写入的节点"，没有独立的"已完成"标志位**。判定"节点 N 已完成"必须靠**节点序列索引比较**：

```
ORDER = ["paper_intake", "paper_analysis", "resource_scout", "planning"]
idx_cur = ORDER.index(current_step)  # current_step 不在 ORDER 时需特判
对节点 N（索引 idx_N）：
  idx_N > idx_cur            → 待执行（灰）
  idx_N == idx_cur           → 运行中（蓝）
  idx_N < idx_cur 且 N∈degraded → 降级完成（黄）
  idx_N < idx_cur 且 N∉degraded → 已完成（绿）
当 current_step=="planning" 且 is_interrupted → planning 段应显示"等待审核/完成"（待 align #4）
当 current_step 不在 ORDER（start / cancelled_by_user）→ 特判（全灰 / 终止态）
```

**这是 D3 同型陷阱的高发处**：状态机本身是纯逻辑，应抽成可单测内核函数（见 L1 设计），否则只能靠 AppTest 间接断言渲染文本，脆弱且漏分支。

### 1.4 用例规模与分层分布
| 层 | 用例数 | 默认运行 | 烧 token | 说明 |
|---|---|---|---|---|
| L1 mock 单测 | **22** | 是 | 否 | CP-D4-1~8 全覆盖 + 状态机内核直测 + 双语回退 + 边界 |
| L2 集成 | **7** | 是 | 否 | FakeGraph + 真实 SqliteSaver；session_state 跨 rerun；停轮询逻辑 |
| L3 真实 UI 冒烟（D3 盲区专项） | **6**（手动 checklist） | 否（手动） | 否 | `streamlit run` 真实启动，专杀 widget key / autorefresh / rerun 时序 |
| L4 后端 e2e | **2** | 否（`-m e2e`） | **是** | 真实 LLM+deepxiv 跑到 planning，进度页轮询真实 state |
| **合计** | **37** | | | 31 自动 + 6 手动冒烟 |

---

## 2. 四层 Test Matrix

### L1 — mock 单测（`tests/test_analysis_progress.py`，默认运行，纯 mock）

测试手段说明：状态机/双语回退判定**抽成内核纯函数直测**（不经 AppTest，稳定且覆盖全分支）；UI 整体行为（停轮询、跳转、卡片渲染、按钮存在性）用 `AppTest.from_string` 驱动真实 `render` + `patch("app._get_controller")` 注入 Mock controller（沿用 D3 范式 L114-116）。

| ID | 层 | 对应 CP / 维度 | 手段 | mock 边界 | 预期断言 | 烧 token |
|---|---|---|---|---|---|---|
| T-D4-01 | L1 | CP-D4-1 | importlib | 无 | `render_analysis_progress_page` 可导入 + 与主名 `render` 同对象（对齐 app.py L285 page_map）| 否 |
| T-D4-02 | L1 | CP-D4-2 | 内核直测 | 无 | `state_machine(current_step="paper_analysis", degraded=[])` → paper_intake=已完成(绿)、paper_analysis=运行中(蓝)、resource_scout/planning=待执行(灰) | 否 |
| T-D4-03 | L1 | CP-D4-2 | AppTest | controller.poll_state→该 state | 渲染含"运行中"标识于 paper_analysis 段、paper_intake 显"已完成" | 否 |
| T-D4-04 | L1 | CP-D4-3 | 内核直测 | 无 | `state_machine(current_step="paper_analysis", degraded=["paper_intake"])` → paper_intake=降级完成(黄) | 否 |
| T-D4-05 | L1 | CP-D4-3 | AppTest | poll_state | UI 出现 paper_intake "降级完成/降级"黄色标识文案 | 否 |
| T-D4-06 | L1 | CP-D4-4 | 内核直测 | 无 | `pick_title({title_zh:"中文标题", title:"EN"})=="中文标题"`；`title_zh=None` → 回退 "EN"；同理 tldr_zh/abstract_zh | 否 |
| T-D4-07 | L1 | CP-D4-4 | AppTest | poll_state 含 paper_meta.title_zh | 卡片显示中文标题 | 否 |
| T-D4-08 | L1 | CP-D4-4 | AppTest | poll_state title_zh=None | 卡片回退显示英文 title | 否 |
| T-D4-09 | L1 | CP-D4-5 | AppTest | is_interrupted→True | `at.session_state["current_page"]=="review"`（已切换）；停轮询 | 否 |
| T-D4-10 | L1 | CP-D4-6 | AppTest | poll_state error="LLM 不可用" | FATAL 卡片文案 + "重试"按钮 + "返回输入页"按钮存在；autorefresh 停（见 L2 验证停轮询副作用） | 否 |
| T-D4-11 | L1 | CP-D4-7 | AppTest | poll_state current_step="cancelled_by_user" | "任务已终止"卡片 + "返回输入页开启新任务"按钮（AC-S2-13）；无"终止任务"按钮 | 否 |
| T-D4-12 | L1 | CP-D4-8 | AppTest | get_worker_error→RuntimeError("boom") | "工作线程异常"FATAL 卡片，含 `str(exc)`="boom"；停轮询 | 否 |
| T-D4-13 | L1 | 新维度：状态机起点 | 内核直测 | 无 | `current_step="start"`（初值，未进任何节点）→ 4 段全"待执行"（不报错、不误判 paper_intake 运行中） | 否 |
| T-D4-14 | L1 | 新维度：全部完成 | 内核直测 | 无 | `current_step="planning"` + degraded=[] → 前 3 段绿、planning 段运行中/等待审核 | 否 |
| T-D4-15 | L1 | 新维度：current_step 越界 | 内核直测 | 无 | `current_step="coding"`（sp3 推进出 4 段外）→ 4 段全"已完成"，不抛 ValueError（ORDER.index 越界防御） | 否 |
| T-D4-16 | L1 | 新维度：state=None 边界 | AppTest | poll_state→None | 不崩溃；显示"加载中/等待任务启动"占位，不渲染空卡片、不抛 KeyError | 否 |
| T-D4-17 | L1 | 新维度：node_errors 为空 | AppTest | poll_state node_errors=[] | 日志区不报错、显示"暂无日志"或留空，不抛 IndexError | 否 |
| T-D4-18 | L1 | 新维度：node_errors 截断 [-10:] | 内核直测/AppTest | 构造 15 条 node_errors | 仅渲染最后 10 条（验证 `[-10:]` dev-plan L1412），第 1-5 条不出现、第 6-15 条出现 | 否 |
| T-D4-19 | L1 | 新维度：node_errors 摘要+详情 | AppTest | node_errors 含 error_detail | 一句话摘要(error_message)可见 + 详情可展开(expander 内含 error_detail) | 否 |
| T-D4-20 | L1 | 新维度：error 优先级 | AppTest | error 非空 **且** current_step="paper_analysis" | error 非空时优先走 FATAL 分支（停轮询），不把它当"运行中"渲染（终态分支优先级裁定） | 否 |
| T-D4-21 | L1 | 新维度：双语全缺失 | AppTest | paper_meta 三个 *_zh 全 None | 三处全回退英文，不出现 "None" 字面量、不崩 | 否 |
| T-D4-22 | L1 | 新维度：paper_meta=None | AppTest | poll_state paper_meta=None（intake 未完成） | 卡片区降级（"论文信息加载中"），不抛 NoneType subscript | 否 |

**L1 设计要点（D3 教训驱动）**：
- CP-D4-2/3/4 既有**内核直测**（T-02/04/06，覆盖全状态机分支，稳定）又有 **AppTest**（T-03/05/07/08，验证渲染真把内核结果落到元素树）。理由：D3 暴露 AppTest 漏集成 bug，但纯内核又漏渲染绑定 bug，**两者互补**。
- 状态机/双语回退**必须可纯函数直测** → 这是对开发的可测试性要求（待 align #5：请开发把判定逻辑抽成模块级纯函数如 `_segment_status(state, node)` / `_pick_bilingual(meta, field)`，而非埋在 render 内联）。

### L2 — 集成（`tests/test_analysis_progress_integration.py`，默认运行，FakeGraph + 真实 SqliteSaver）

测试手段：构造真实 `GraphController`（app.py 真实类），但用 **FakeGraph 替换 `_main_graph`** 或用**真实 SqliteSaver（`tmp_path` 库）预置 checkpoint**，让 poll_state/is_interrupted 走真实读路径，验证页面与 controller 真实对象对接（非 MagicMock 桩）。session_state 跨 rerun 用 AppTest 多次 `.run()`。

| ID | 层 | 维度 | 手段 | mock 边界 | 预期断言 | 烧 token |
|---|---|---|---|---|---|---|
| T-D4-I1 | L2 | controller 真实对接 | 真实 SqliteSaver(tmp_path) 预置 state + 真实 GraphController.poll_state | 仅 mock graph 节点不跑（只读 checkpoint） | 页面渲染的进度态与真实 checkpoint 写入的 current_step 一致 | 否 |
| T-D4-I2 | L2 | thread_id 流转 | AppTest，session_state 预置 thread_id（模拟 D3 跳转后） | mock controller | 页面用 session_state["thread_id"] 调 poll_state（thread_id 透传正确，非硬编码） | 否 |
| T-D4-I3 | L2 | current_page 跨 rerun | AppTest 连续 run | is_interrupted 第 2 次 run 才 True | 第 1 次 run 停在 progress；第 2 次 run interrupt → current_page 切 review（跨 rerun 状态正确流转） | 否 |
| T-D4-I4 | L2 | 停轮询：error | AppTest | poll_state error 非空 | autorefresh 不再注册/被短路（验证 st_autorefresh 在 error 分支前 return 或条件跳过——见 L3 真实验证补充） | 否 |
| T-D4-I5 | L2 | 停轮询：cancelled | AppTest | current_step="cancelled_by_user" | 同上，cancelled 分支停轮询 | 否 |
| T-D4-I6 | L2 | 停轮询：interrupted | AppTest | is_interrupted True | interrupt 跳转前停本页轮询（不与 review 页轮询打架，architecture §2.7.3） | 否 |
| T-D4-I7 | L2 | 多次 rerun 幂等 | AppTest 连 run 3 次（同一非终态 state） | mock poll_state 固定返回 | 3 次渲染结果一致、session_state 无累积污染、无重复 widget key 异常（`not at.exception`） | 否 |

**L2 关键风险标注**：`st_autorefresh(key="progress_poll")` 在 AppTest 下的可观测性有限（autorefresh 是前端 JS 组件，AppTest 不真跑浏览器定时器）。L2 只能断言"代码路径是否执行到 st_autorefresh 调用"或"终态分支提前 return"，**autorefresh 真实停/转行为必须靠 L3 冒烟**——这是 plan 显式承认的 L2 盲区。

### L3 — 真实 UI 冒烟（D3 盲区专项，手动 checklist，`streamlit run`）

**为什么必须有 L3**：D3 的 BUG-S2-D3-01（直写已实例化 widget key）AppTest **完全测不到**，只有真实点击才崩。进度页有**两个同型/更高危的真实运行专属风险**：
1. `st_autorefresh` 是真实前端定时器组件，AppTest 不执行其 JS，**停轮询逻辑只有真机能验**；
2. interrupt 跳转触发 `st.rerun()` + autorefresh 定时器并存时的**时序竞争**（rerun 与 1.5s 定时器谁先触发），AppTest 单线程顺序执行掩盖了真实并发。
3. 跨页 widget key 冲突：进度页与 paper_input 页**共用同一侧栏**（app.py 注释 L278-280 警告过 `default_base_url` 重复 key 会 `StreamlitDuplicateElementKey`）——D4 若也渲染侧栏 LLM 表单，跨页切换可能复现 D3 同型 key 冲突。

**冒烟操作步骤（启动）**：
```
.venv/bin/streamlit run app.py --server.headless true --server.port 8502
# 浏览器开 http://localhost:8502
# 先在输入页填 LLM 配置 + 一个 arxiv_id（或用 stub controller 直接 session_state["current_page"]="progress"）
```

| ID | 维度 | 操作步骤 | 看什么（PASS 判据） | D3 同型? |
|---|---|---|---|---|
| T-D4-S1 | autorefresh 真实轮询 | 进入 progress 页，工作线程跑中（非终态），静观 5s | 页面每 ~1.5s 自动刷新（进度条/日志有更新或重渲染痕迹），无报错红框 | — |
| T-D4-S2 | **autorefresh 真实停（error）** | 让 poll_state 返回 error 非空（可注入 stub 或真跑到 LLM 失败） | FATAL 卡片出现后**页面不再自动刷新**（autorefresh 真停，不再 1.5s 轮询）；"重试"/"返回"按钮可点 | **高危**：AppTest 测不到定时器停 |
| T-D4-S3 | **interrupt 跳转时序** | 工作线程跑到 planning interrupt，等进度页轮询命中 | 自动跳转到 plan_review 页（不卡在 progress、不出现 rerun 与 autorefresh 抢占导致的闪烁/双跳） | **高危**：rerun×定时器并发，AppTest 顺序执行掩盖 |
| T-D4-S4 | **跨页侧栏 widget key 冲突** | input 页填配置 → 跳 progress 页 → （若有"返回输入页"）点返回 | 全程无 `StreamlitDuplicateElementKey`（尤其 `default_base_url` 等 D1 表单 key）；app.py L278-280 已就此告警 | **D3 同型**：直接对应 BUG-S2-D3-01 家族 |
| T-D4-S5 | cancelled 终态停轮询 | 在 review 页点终止 → 自动回流 progress（或 poll 到 cancelled_by_user） | "任务已终止"卡片 + 停轮询 + "返回输入页"按钮真实可点并跳回 input | — |
| T-D4-S6 | 进度条/卡片折叠展开真实渲染 | 展开/折叠摘要 expander、node_errors 详情 expander | 折叠态默认正确、展开内容完整、多次折叠展开不丢内容/不重复 key 报错 | 中危：expander 真实 DOM 行为 |

**L3 产出**：开发完成后由测试工程师执行，结果记入 D4 验收报告（不在本 plan）。任一高危项（S2/S3/S4）失败即视为生产 BUG，转全栈开发代理。

### L4 — 后端 e2e（`tests/test_analysis_progress_e2e.py`，`@pytest.mark.e2e`，真实凭证）

测试手段：真实 LLM + 真实 deepxiv + 真实 graph 工作线程跑到 paper_analysis/planning，主线程进度页轮询真实 SqliteSaver state。**必须 `pytestmark = pytest.mark.e2e` + `skipif` 凭证缺失**（读 `config.get_llm_api_key()` / `config.get_deepxiv_token()`），默认 `addopts` 排除。

| ID | 层 | 维度 | 手段 | 凭证 | 预期断言（聚焦契约非内容） | 烧 token |
|---|---|---|---|---|---|---|
| T-D4-E1 | L4 | 真实轮询渐进 | 真实 GraphController.start_task(小论文 2405.14831) + 轮询 | LLM_API_KEY + DEEPXIV_TOKEN | poll_state 的 current_step 随时间从 start→paper_intake→paper_analysis 单调推进；title_zh 非空（双语回填真实生效）；不 hardcode 标题文本 | **是** |
| T-D4-E2 | L4 | 真实 interrupt 跳转 | 跑到 planning interrupt | 同上 | is_interrupted 最终 True；poll_state 含非空 reproduction_plan（approved=False）；进度页此时应跳 review | **是** |

**L4 约束**：用小论文 `arXiv:2405.14831`（HippoRAG，与 D3 同靶）避免 token 爆；e2e 层稳定性复跑 3 次（轮询时序对 LLM 速度敏感，需确认无 flaky）；断言只验**字段存在性/类型/单调性**，不验具体中文译文（LLM 输出会微变）。**留 E 阶段凭证就绪跑，本 plan 仅设计好。**

---

## 3. D3 盲区专项对照表

| D3 教训 | 进度页对应同型风险 | 本 plan 覆盖项 | 覆盖层 |
|---|---|---|---|
| 直写已实例化 widget key 崩溃（AppTest 测不到） | 跨页共用侧栏 `default_base_url` 等 key 重复 → `StreamlitDuplicateElementKey`（app.py L278-280 已告警同款隐患） | T-D4-S4 | **L3 必跑** |
| 单元全绿但真实集成崩 | autorefresh 真实定时器停/转、rerun 时序竞争 | T-D4-S2 / T-D4-S3 | **L3 必跑** |
| 纯 mock 漏渲染绑定 | 内核直测 + AppTest 双轨（状态机/双语） | T-D4-02~08（内核+AppTest 配对） | L1 |
| `value=` + `key=` 双源反模式 | 进度页只读无输入框，风险低；但 session_state["current_page"] 写入需确认非"写已实例化 widget"路径 | T-D4-09（跳转写 current_page）+ T-D4-S4 真机验 | L1 + L3 |
| 防止 None/空边界崩 | state=None / paper_meta=None / node_errors=[] | T-D4-16/17/22 | L1 |

**核心结论**：进度页**比 D3 风险更高**——D3 只有 widget key 一类真机专属陷阱，进度页叠加了 **autorefresh 定时器 + rerun 时序 + 跨页侧栏** 三类 AppTest 盲区。**L3 冒烟从"可选"升级为"放行 D4 的强制门槛"**。

---

## 4. 待 align 问题清单（请架构师 / PM / Maria 裁定）

| # | 问题 | 影响 | 我的倾向（不替代决策） | 建议裁定方 |
|---|---|---|---|---|
| **1** | **入口函数名契约冲突**：CP-D4-1 写 `from ...import render`，但 app.py L285 page_map 期望 `render_analysis_progress_page`。 | 决定测试断言哪个名；不一致会导致路由 ImportError | 沿用 D3 方案：主名 `render` + `render_analysis_progress_page = render` 别名导出，两者都测（T-D4-01）。请确认 dev-plan CP-D4-1 措辞与 app.py 谁为准。 | 架构师 |
| **2** | **进度条"已完成"判定无显式标志**：current_step 只记最后节点，须靠节点序列索引推断（§1.3）。dev-plan L1407-1411 未明说推断方式。 | 状态机实现方式 + 测试断言基准 | 用 `ORDER.index` 比较；越界(coding/start/cancelled)需特判。请确认此推断契约，避免开发各写各的。 | 架构师 |
| **3** | **planning + interrupt 时 planning 段显示什么**：current_step=="planning" 且 is_interrupted 时，planning 段是"运行中(蓝)"还是"等待审核"特殊态？dev-plan/架构未定义。 | T-D4-14 断言基准 | 建议显示"等待审核"独立态（蓝→紫/特殊），但此时通常已跳 review 页，可能不渲染。请确认是否需要该态。 | 架构师 + PM |
| **4** | **error 与 cancelled 与 worker_error 三终态优先级**：若同时出现（如 worker 崩溃且 state.error 也写了），渲染哪个卡片？ | T-D4-20 终态分支裁定 | 建议优先级 worker_error > error > cancelled（worker 崩溃最致命），dev-plan 未定义。 | 架构师 |
| **5** | **可测试性要求（对开发）**：请把状态机判定与双语回退抽成模块级纯函数（如 `_segment_status(current_step, degraded, node)` / `_pick_bilingual(meta, base, zh)`），而非埋在 render 内联。 | 决定 L1 能否做内核直测（覆盖全分支、稳定）；否则只能脆弱地断言渲染文本 | 强烈建议抽函数（D3 教训：内核可直测才能补 AppTest 盲区）。 | 全栈开发代理 |
| **6** | **non-CS 警告是否在进度页重复展示**：D3 输入页已弹 non-CS WARNING，进度页论文卡片是否重复弹？ | 是否需补 non-CS 用例 | 倾向不重复（避免噪音），但请 PM 确认进度页卡片是否含 categories/警告。 | PM |

**阻断性判断**：#1 / #2 / #5 影响测试能否落地（断言基准 + 可测试性），**建议开发启动前快速 align**；#3 / #4 / #6 是边界完善项，可在补缺过程中并行确认，不阻断主体开发。

---

## 5. 验收门槛建议（D4 放行标准）

D4 独立验收 PASS 需同时满足：

1. **L1 全绿**：CP-D4-1~8 八项检查点（内核直测 + AppTest 双轨）+ 9 个新维度边界用例（T-13~22），共 22 项 0 fail；连跑 3 次 0 flaky。
2. **L2 全绿**：7 项集成用例 0 fail，含真实 SqliteSaver 对接 + 跨 rerun current_page 流转 + 三类停轮询分支 + 多 rerun 幂等。
3. **L3 强制冒烟通过**：6 项真机冒烟全过，**尤其 S2(autorefresh 真停) / S3(interrupt 跳转时序) / S4(跨页侧栏 key 冲突) 三个高危项必须人工确认**（D3 盲区直接对应，任一失败即生产 BUG，不放行）。
4. **回归零退化**：`pytest -q -m "not e2e"` 在 D3 基线（403 passed）基础上仅增不减，无既有用例退化。
5. **不烧 token / 不连网**（默认运行）：L1/L2 全 mock；L4 e2e 仅 `-m e2e` 显式触发。
6. **L4 e2e 留 E 阶段**：凭证就绪后跑 T-E1/E2，3 次稳定性复跑，作为 E 阶段统跑的一部分（非 D4 放行硬门槛，但 plan 已设计就位）。

**门槛特别说明**：鉴于 D3 教训 + 进度页三类 AppTest 盲区，**L3 冒烟是 D4 放行的硬门槛**（D3 时 L3 缺位才漏掉 BUG-S2-D3-01）。仅 L1+L2 全绿**不足以**放行 D4。

---

## 6. 已知遗漏 & 接受理由（诚实清单）

| 遗漏点 | 原因 | 缓解 |
|---|---|---|
| autorefresh 真实定时器的精确间隔（1.5s±）压测 | 需真浏览器 JS 计时，AppTest 不跑 | L3 人工静观 5s 粗验；精确性非验收项 |
| 多浏览器标签/多用户并发轮询同 thread_id | sp2 单用户单 thread_id（PRD Q-S2-05 不引入恢复入口），超范围 | 记录为 sp3+ 风险 |
| Playwright 端到端 DOM 断言（自动化点击/截图） | 超出当前测试栈范围（无 Playwright） | L3 手动 checklist 替代 |
| rerun×autorefresh 并发竞争的确定性复现 | 时序竞争难稳定自动复现 | L3 人工反复触发观察；若现 flaky 转 BUG |

---

## 7. 执行计划摘要

- **补缺用例预估数**：自动 31（L1:22 + L2:7 + L4:2）+ 手动冒烟 6（L3）= **37 项**。
- **预估耗时**：L1/L2 编写约 3-4h；L3 冒烟人工约 0.5h；L4 e2e 编写约 1h（执行待 E 阶段凭证）。
- **稳定性复跑**：L1/L2 连跑 3 次守 flaky；L4 e2e 连跑 3 次（轮询对 LLM 速度敏感）。
- **依赖前置**：本 plan 待 align #1/#2/#5 确认后，开发与测试并行按 plan 补缺；D4 代码就位后出 D4 验收执行报告（含 L3 冒烟结果）。

---

*本文为 D4 开发前 test plan（Test Plan Driven 首次落地）。开发完成后逐条补缺并独立验收，验收结果另出执行报告归档于本目录。*
