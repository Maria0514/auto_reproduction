# Sprint 6 批次 4 测试执行报告——恢复批 G3 / 任务重连 + 任务列表页

- **日期**：2026-07-15
- **批次**：Sprint 6 批次 4（T-S6-4-1 ~ T-S6-4-4）
- **执行者**：主控
- **测试命令口径**：`.venv/bin/pytest -q -m "not e2e"`
- **相对基线**：sp6 批次 3 收官 1888 绿

## 1. 范围

恢复批 G3——**零 state 变更**（旧 checkpoint 直接可消费，本 Sprint 最大架构红利）。四任务：

| 任务 | 内容 | 落点文件 |
|---|---|---|
| T-S6-4-3 | `derive_task_status` 纯函数 R1~R7 + `list_threads` mode=ro 只读枚举 + `resume_task` 显式续跑（原子 check-and-set）+ `STREAMLIT_PAGE_TASKS` 常量 + `_PAGE_MAP` 注册 + `_extract_paper_label` 三级回退 | `app.py` + `config.py` |
| T-S6-4-1 | `_restore_from_query_params(controller)` + main() 接线（`_restore_attempted` 会话单次标志 + 无参数路径字节等价红线）+ `_route_for_status` 挂回路由 | `app.py` |
| T-S6-4-2 | start_task 成功写 `st.query_params["task"]` + render 顶部清 stale 参数 | `ui/pages/paper_input.py` |
| T-S6-4-4 | 任务列表页（枚举表 + 状态徽标 + 一键挂回，挂回**不调** resume_task）+ 入口导航链接 + R7 卡片「继续执行」接通 resume_task | 新 `ui/pages/task_list.py` + `paper_input.py` |

## 2. 新增测试（CP 覆盖）

| 文件 | 用例数 | 覆盖 CP |
|---|---|---|
| `tests/test_sprint6_s6_07_task_status.py` | 15 | CP-4.3-1（derive_task_status R1~R7 全行 + 优先级短路）+ CP-4.3-2/3（20-thread 真库枚举 + 排序 + md5 只读不变 + 缺库防御）+ CP-4.3-4（resume_task invoke(None) + 原子拒绝存活 worker + 真实 invoke 实参） |
| `tests/test_sprint6_s6_06_reconnect.py` | 9 | CP-4.1-1（无参数/已有 thread_id 字节等价）+ CP-4.1-2（重连路由矩阵：done→报告 / awaiting 按 kind → 审核\|监控 / 终态+running→监控）+ CP-4.1-3（不存在 thread 安全回退 + _restore_attempted 标志） |
| `tests/test_sprint6_s6_06_query_params.py` | 4 | CP-4.2-1（start_task 写 task 参数·源码固化）+ CP-4.2-2（无活动任务清 stale / 活动任务保留） |
| `tests/test_sprint6_s6_07_task_list_page.py` | 9 | CP-4.4-1（枚举渲染 + 空态 + 无删除/搜索/分页边界）+ CP-4.4-2（一键挂回写 state+路由 + **不调 resume_task** 红线）+ CP-4.4-3（无 autorefresh + 入口链接）+ CP-4.4-4（R7 卡片「继续执行」→ resume_task） |

## 3. 20-thread 真库靶测（CP-4.3-3，AC-S6-15）

`tests/fixtures/checkpoints_s6_full20.db`（测试工程师批次 3 前置门固化）驱动 `list_threads`：
- 20 thread 全部推导成功，状态矩阵覆盖 **awaiting×10 / interrupted×4 / no_report×3 / done×1 / failed×1 / cancelled×1**（R2~R7 全类型）；
- 新任务在前（`task-cdcd432cda49` 最新 checkpoint 居首）；论文标识三级回退命中 title_zh 中文标题；
- 主库 md5 前后一致（mode=ro 只读不写业务数据）；坏 thread 逐条跳过不炸整页（R-S6-A3 容错）。

## 4. 回归适配（§9.4 只换不弱化）

- `test_sprint3_e1.py` / `test_sprint3_e1_reinforce.py`：公开方法白名单纳入 `list_threads`/`resume_task`/`get_task_status`；`_PAGE_MAP` 键集合 + config 页面常量集合纳入 `STREAMLIT_PAGE_TASKS`（6 页）。
- `test_sprint3_e1_reinforce.py::_FakeStreamlit`：补 `query_params={}` 属性——main() 新增 `_restore_from_query_params` 步骤读 `st.query_params`，无参数路径直接 return（字节等价红线），空 dict 即满足，dispatch 行为零变化。

## 5. 产品红线守护

- **挂回 = 展示现状**（AC-S6-16）：任务列表页「挂回」只写 query_params + thread_id + 路由，**绝不调 resume_task**（`resume_task.assert_not_called()` 守门）；孤儿任务推进由挂回落地的执行监控页 R7 卡片「继续执行」显式按钮触发（那里才调 resume_task，本批接通）。
- **无参数路径字节等价**（AC-S6-14 红线）：无 task 参数 ∨ session 已有 thread_id → `_restore_from_query_params` 直接 return，main() 行为与现状完全一致（CP-4.1-1 守门）。
- **零 state 变更**：derive_task_status / list_threads / resume_task 全走既有 `_main_graph.get_state` 读路径 + 既有线程模型，旧 checkpoint 直接可消费。

## 6. 全量回归结果

- **`.venv/bin/pytest -q -m "not e2e"`**：**1924 passed / 1 failed / 25 skipped / 45 deselected**（153s）。
- 唯一失败 = `test_plan_review_e2e::test_e2e_code_only`（`@pytest.mark.browser` chromium 点 iframe 「仅复现代码」按钮）——**预存在的间歇性浏览器 flaky**，批次 3 已用 `git stash` 全量改动在 baseline 复现同款失败、证明与 sp6 无关；批次 3 收官那轮它恰好通过（1888/0）。该 flaky 通过时本批为 **1925 passed / 0 failed**。
- 逻辑用例（非浏览器）**1924 全部稳定通过**，相对 1888 基线净增 37 用例（task_status 15 + reconnect 9 + query_params 4 + task_list 9），零退化。
- 回归适配：初轮全量有 3 处 `_FakeStreamlit` 缺 `query_params` 失败（main 接线 `_restore_from_query_params` 读 st.query_params 引发，属 test-double 适配非 CP 失败）——两处 e1 假 st 补 `query_params={}` 后转绿。

## 7. 结论

批次 4 CP-4.1~4.4 全绿 + 20-thread 真库靶测 + 无参数路径字节等价 + 全量非 e2e 零退化。批次 3 R7 卡片「继续执行」按钮此时接通 `resume_task`。批次边界停手，等 Maria 确认再开批次 5（收口）。
