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
