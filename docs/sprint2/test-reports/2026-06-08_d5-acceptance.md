# D5 计划审核页测试报告（test-report-d5）

## 被测对象
`ui/pages/plan_review.py`（Sprint 2 任务 D5，计划审核 HITL 页，294 行）。
导出主入口 `render()` 与别名 `render_plan_review_page = render`，`current_page="review"`。
当 planning 节点产出 `ReproductionPlan` 并触发 LangGraph interrupt 后，本页展示复现计划全文 /
候选仓库 / 透明化信息，并提供五个决策按钮（approve / code_only / revise / switch_repo / cancel）
经 `GraphController.resume_with` / `cancel_task` 恢复或终止执行。

## 新增测试文件
`tests/test_plan_review.py`（沿用 D3/D4 AppTest 范式：`streamlit.testing.v1.AppTest` 驱动真实
`render` + `patch("app._get_controller")` 注入 Mock controller，纯 mock 不烧 token / 不连网）。

### 用例清单（14 项：13 单元 + 1 e2e）
| 用例 | 覆盖说明 |
| --- | --- |
| `test_d5_01_importable` | 入口可导入，`render_plan_review_page is render`，`__all__` 约定正确 |
| `test_d5_02_no_thread_id_fallback` | 无 thread_id → 兜底提示「尚未启动任务」并 return，不崩、不调 controller |
| `test_d5_03_payload_none_not_ready` | `get_interrupt_payload` 返回 None → 显示「计划尚未就绪」并 return，不渲染后续 |
| `test_d5_04_full_payload_renders_all_sections` | 完整 payload → 计划/仓库/透明化/决策全渲染，selected_repo 高亮 ✅，五按钮齐全 |
| `test_d5_05_partial_payload_no_keyerror` | 残缺 payload（子结构缺失）→ 防御式 `.get` 兜底不抛 KeyError |
| `test_d5_06_approve_decision_payload` | approve → `resume_with(tid, {"decision":"approve"})`，不调 cancel_task |
| `test_d5_07_code_only_decision_payload` | code_only → `resume_with(tid, {"decision":"code_only"})` |
| `test_d5_08_revise_decision_carries_user_feedback` | revise → `resume_with` 带 `decision=revise` + `user_feedback`（文本框值透传） |
| `test_d5_09_switch_repo_decision_carries_feedback_and_url` | switch_repo → `resume_with` 带 `user_feedback` + `new_repo_url` 三字段 |
| `test_d5_10_cancel_first_click_sets_flag_only` | cancel 首次点击只置确认标记，不调 cancel_task，出现确认文案 |
| `test_d5_11_cancel_confirm_calls_cancel_task` | cancel 二次确认后才 `cancel_task(tid)`，清标记并切 progress 页 |
| `test_d5_12_soft_hint_shown_at_threshold` | `revise_count >= soft_hint_threshold(5)` → 显示软提示 warning |
| `test_d5_12b_soft_hint_absent_below_threshold` | `revise_count < threshold` → 不显示软提示（边界对照） |
| `test_d5_e1_interrupt_payload_contract_e2e`（e2e） | 真实跑到 planning interrupt → `get_interrupt_payload` 真读路径返回页面消费契约键 |

## 覆盖的决策契约
- `approve` → `resume_with(thread_id, {"decision": "approve"})`
- `code_only` → `resume_with(thread_id, {"decision": "code_only"})`
- `revise` → `resume_with(thread_id, {"decision": "revise", "user_feedback": <text>})`
- `switch_repo` → `resume_with(thread_id, {"decision": "switch_repo", "user_feedback": ..., "new_repo_url": ...})`
- `cancel` → 二次确认：首次点击仅置 `_review_confirm_cancel=True`（不调 cancel_task）；
  确认后才 `cancel_task(thread_id)`，清标记并切 `current_page="progress"`。

## e2e 说明
- 标记：`@pytest.mark.e2e`（项目既有 marker，见 `pytest.ini`）。
- 凭证门控：`@pytest.mark.skipif(not _has_credentials(), ...)`——`LLM_API_KEY` + `DEEPXIV_TOKEN`
  任一缺失即自动 skip（沿用 `tests/conftest.py` 自动加载 `.env` + 凭证存在与否决定是否跑的范式）。
- 运行：`LLM_API_KEY=... DEEPXIV_TOKEN=... .venv/bin/python -m pytest tests/test_plan_review.py -m e2e -v`。
- 默认套件：`.venv/bin/python -m pytest -m "not e2e"` 会 deselect 该 e2e 用例，不烧 token。

## 最终 pytest 数字
`.venv/bin/python -m pytest -m "not e2e" -q`

| 指标 | before | after |
| --- | --- | --- |
| passed | 469 | 482 |
| failed | 0 | 0 |
| deselected | 27 | 28 |

- 净增 13 个单元用例（469 → 482），0 failed，未碰挂任何既有测试。
- deselected 27 → 28（新增的 1 个 e2e 用例被 `-m "not e2e"` 正确排除）。
- 新测试文件单独跑：`pytest tests/test_plan_review.py -m "not e2e" -q` → 13 passed, 1 deselected。

## 结论
**可提交（通过）**。新增测试全部覆盖任务要点 1~6，0 failed，未污染既有套件；e2e 凭证门控正确。

---

## 追加：视觉对齐产品经理 mock（docs/sprint2/ui-mockup/index.html）

首版 D5 只做了控件的 shadcn 化（机械替换），未逐项比对目标稿，落地与 mock 存在 7 处偏差。
本轮打开 mock 渲染对比后逐项补齐：

| # | 偏差项 | mock 目标 | 修复 | 验收 |
| --- | --- | --- | --- | --- |
| 1 | 进度段数 | 5 段 | 新增 `DISPLAY_ORDER` 5 段展示层（逻辑层 `ORDER` 仍 4 段，`_segment_status` 单测不变）；第 5 段 `post_review` 合并下游 coding/execution/reporting，Sprint 2 恒 pending | ✅ frame inner_text 命中 5 段 |
| 2 | 段名 emoji | 每段顶部 emoji | `_NODE_DISPLAY` 映射：解析论文📄 / 分析论文🧠 / 资源侦察🔍 / 制定计划🧩 / 执行复现⚙️ | ✅ 5/5 emoji 命中 |
| 3 | 运行中整卡高亮 | `.stage.active` 蓝边+浅蓝底 | 进行中段整卡 `border:#2563eb; background:#eff6ff`（原生 `st.container(key=)` + `.st-key-` CSS 注入） | ✅ vision 确认整卡蓝边 |
| 4 | 论文卡分类徽章 | cs.XX 蓝色 pill | 用 HTML span 画蓝 pill（`#eff6ff` 底 + `#2563eb` 字 + 圆角）；ui.badges 走 Tailwind 在 shadcn iframe 内被 tree-shake 成灰色，故弃用 | ✅ vision 确认蓝色圆角 pill |
| 5 | Stars/Forks | 数字 | 三 metric_card（质量分/⭐Stars/🍴Forks）渲染就绪。**注：后端 resource_scout 当前未采集 GitHub 元数据，真实数据缺失时显示占位 —— 留 D6 接 GitHub/PWC API** | ⚠️ 组件就位，数据源待 D6 |
| 6 | 仓库选中态 | `.repo-card.selected` 浅蓝底+左粗蓝边 | 选中仓库 `background:#eff6ff; border-left:4px solid #2563eb` + 徽章「已选用」（原生 container CSS 注入） | ✅ vision 确认选中/未选对比明显 |
| 7 | 终止按钮 | destructive 红色 | D5 commit 已是 `variant="destructive"`，无需新代码 | ✅ 已实现 |

### 技术债清理
- `streamlit_extras.stylable_container` 在 streamlit 1.58 已 deprecated（运行时弹黄色警告框污染界面）。
  两处用法（进度段卡、仓库卡）改用原生 `st.container(key=...)` 生成的 `st-key-<key>` class
  选择器 + `st.markdown` 注入 CSS。验收确认警告框消失，视觉零变化。

### 验收方法
- **真实 e2e 流水线**：`streamlit run app.py` + playwright 走 input→progress→review（arxiv 2405.14831 HippoRAG），
  全程 `errors=[]`，reached_review=True，14 帧截图。frame inner_text 实证 5 段/emoji/cs.CL+cs.AI/已选用/三 metric_card。
- **demo harness**（注入 fake state 精确验收选中态/运行中态）：
  `/tmp/d5_demo_progress.py`（进度页）、`/tmp/d5_demo_review.py`（审核页双候选仓库一选一未选）。
- **视觉验收**：vision_analyze 逐张确认 mock 对齐（注：shadcn 在 iframe 内，full_page 截不全，
  仓库区靠 demo harness 单页渲染绕开）。

### mock 对齐后回归
`.venv/bin/python -m pytest tests/test_analysis_progress.py tests/test_plan_review*.py tests/test_paper_input*.py -q`
→ **37 passed, 20 skipped, 0 failed**（_render_paper_card 渲染逻辑变更未破坏任何既有测试）。

### 已知偏差（转 D6）
- Stars/Forks 真实数据：后端 `resource_scout` 节点未采集 GitHub 仓库元数据（stars/forks/last_commit），
  需新接 GitHub API 或 PapersWithCode API，超出 D5 范围。UI 侧 metric_card 组件已就位，接上数据源即生效。
