# 测试执行报告 - d2-acceptance

- **日期**：2026-06-04 （本地时区）
- **执行人**：@测试工程师代理
- **Sprint**：sprint2
- **触发原因**：任务 D2（app.py + GraphController）独立验收 + 边界用例补全（高风险：threading + LangGraph interrupt + SqliteSaver 三件套）
- **commit**：0aa71e6（D2 产出留工作区未提交：`app.py` / `tests/test_app_controller.py` 为 untracked，`requirements.txt` 为 modified）

## 验收结论：**PASS**

CP-D2-1 ~ CP-D2-10 全部独立复核命中；高风险三件套 + OBS-D1-01 七项独立裁定全部通过；无生产 BUG；与架构 §2.7 无实质偏差（仅 1 处属架构参考代码滞后，详见下）。

---

## 执行范围

- 命令：
  - `pytest tests/test_app_controller.py -q`（D2 单测，含补强）
  - `pytest tests/test_app_controller.py -q -p no:randomly`（连跑 5 次，threading 不 flaky 守门）
  - 单条独立运行 3 个高风险用例（无序依赖守门）
  - `pytest -q -m "not e2e" --ignore=tests/test_paper_intake.py`（非 e2e 核心回归，连跑 3 次）
- 覆盖用例：`tests/test_app_controller.py` 全部 31 用例（开发 15 + 测试工程师补强 16）
- 是否包含 e2e：**否**。D2 验收用 FakeGraph + 真实 SqliteSaver(tempfile) 两套 fixture 即可完整验证并发/持久化/线程模型，不烧真实 token；真实 LLM + interrupt resume 端到端由 E 阶段统筹（C1 resume e2e 已于 2026-06-03 跑通，见 `2026-06-03_c1-resume-e2e.md`）。

---

## 结果摘要

- 通过：D2 单测 31/31；非 e2e 核心回归 390/390（27 deselected = e2e）
- 失败：0
- 跳过：0
- 警告：1（`LangChainPendingDeprecationWarning`，langgraph 库内部 `checkpoint/serde/encrypted.py:5` 触发，**非项目代码**，全 sp1/sp2 套件长期存在；见"后续动作"）
- 总耗时：D2 单测 0.76~0.88s/次；非 e2e 核心回归 3.36~3.41s/次

### 连跑稳定性
- D2 单测连跑 5 次：31 passed（0.77/0.76/0.77/0.77/0.80s），**0 flaky**，无线程竞态/无序依赖/无抖动
- 非 e2e 核心回归连跑 3 次：均 390 passed（3.36/3.41/3.37s），**零退化**（开发自报 D2 后基线 374 + 本次补强 16 = 390，数对一致）
- 单条独立运行（`::test_bnd_four_threads...` / `::test_cp_d2_9...` / `::test_bnd_get_controller_singleton...`）：3 passed，每个用例可脱离套件独立运行

---

## CP-D2-1 ~ CP-D2-10 逐条独立复核

| CP | 复核结论 | 独立实证方式 |
|----|---------|------------|
| CP-D2-1 导入+实例化 | PASS | `test_cp_d2_1`：断言 `_main_checkpointer`/`_main_graph`/`_workers={}`/`_worker_errors={}` 初值 |
| CP-D2-2 start_task 起 worker | PASS | `test_cp_d2_2`：Event 阻塞观察 `is_alive()==True` → release join → `False`；config 经 `_make_config` |
| CP-D2-3 poll_state 走 main_graph | PASS | `test_cp_d2_3`：返回 `snapshot.values`；无 snapshot 返回 None |
| CP-D2-4 is_interrupted 三态 | PASS | True（interrupt）/ False（END）/ False（普通节点）3 子用例 + 补强 4 边界（空 tasks / 空 interrupts / 无 snapshot / 多 task 任一命中） |
| CP-D2-5 resume_with 起新 worker + 独立 saver | PASS | `test_cp_d2_5`：daemon worker、created 计数 +1、invoke 收 `Command(resume=...)`、config 经 `_make_config` |
| CP-D2-6 worker 异常感知 | PASS | start 路径 + resume 路径两用例均验证 `_worker_errors[tid]` 含同一对象、`get_worker_error` 读得到（加锁） |
| CP-D2-7 cancel_task 两路径 | PASS | interrupt 走 cancel payload + poll 反映 cancelled_by_user + 转 False；非 interrupt 恰 1 WARNING 不抛不起线程；补强 END / 普通节点两个 cancel 忽略边界 |
| CP-D2-8 api_key 逐条刷新 | PASS | default+4 override 全刷新断言；空 override 清理；补强三态（全空/部分空/全填）+ 非法节点名忽略 + overrides 缺失降级 |
| CP-D2-9 并发 2 thread_id 真实 SqliteSaver | PASS（**真实落盘已独立验证**） | tempfile DB + 真实最小 graph，2 worker 独立回读 done:paperA/done:paperB 不串扰；created id 全唯一；补强扩展到 N=4 |
| CP-D2-10 sp1/sp2 零退化 | PASS | 非 e2e 核心回归 390/390 连跑 3 次稳定；占位用例断言 `_make_config` 注入 checkpoint_ns="" |

---

## 高风险三件套 + OBS-D1-01 独立裁定

### 1. 每线程独立 SqliteSaver（真实性核验）— PASS（确为真实落盘）
独立审 `test_cp_d2_9` + 我新增的 `test_bnd_four_threads_independent_savers_real_sqlite`：两者均 **monkeypatch `get_checkpointer` → 真实 `core.checkpointer.get_checkpointer(tempfile_db)`、`build_graph` → 真实 `StateGraph(...).compile(checkpointer)`**，非 mock。worker 节点把 `user_input` 写入 `current_step`，主线程经 `poll_state`（独立 main_graph / main_checkpointer）跨实例回读，N=2 与 N=4 均各自取回自己的值、互不串扰。`created` 列表 `id()` 全唯一 → main + N worker 确为 N+1 个独立 Python 实例，仅共享 SQLite 文件（WAL）。**开发自报"真实 SqliteSaver 落盘 + 跨实例回读"属实，不是 mock。**

### 2. `_make_config` 注入 checkpoint_ns=""（完整性核验）— PASS
`app.py` 所有触达 saver 的路径——`_worker_run` invoke / `_resume_run` invoke / `poll_state` get_state / `is_interrupted` get_state——**全部经 `_make_config(thread_id)`**，无任何字面量 dict 漏注入。补强 `test_bnd_make_config_injected_on_get_state_path`（spy get_state，2 次调用均含 `checkpoint_ns=""`）+ `test_bnd_make_config_injected_on_invoke_paths`（start + resume 两条 invoke config 均含 `checkpoint_ns=""`）双路径实证。符合 LangGraph 1.1.10 强制约束（S-2 spike L50）。

### 3. 不在主线程同步 invoke — PASS
`start_task` / `resume_with`（及 cancel_task 经 resume_with）三入口均 `threading.Thread(..., daemon=True)` 起线程后立即返回，invoke 只在 `_worker_run` / `_resume_run` 内执行。`test_cp_d2_2` 用 Event 阻塞 invoke 时主线程已拿到 thread_id 且 worker `is_alive()==True`，证明主线程不阻塞。`cancel_task` 在主线程仅做 `is_interrupted` 只读判定后委托 resume_with。

### 4. is_interrupted 判定形态（边界全覆盖）— PASS
`= snapshot.next 非空 且 _has_interrupt(snapshot)`。边界全覆盖：
- 真 interrupt（next 非空 + task.interrupts 非空）→ True（CP-D2-4 + 补强多 task 任一命中）
- END（next=()）→ False（CP-D2-4）
- 普通节点暂停（next 非空、task.interrupts 空）→ False（CP-D2-4 补充 + 补强）
- tasks 为空元组 → False（补强，与"interrupts 空"是不同代码路径）
- 无 snapshot（get_state None）→ False 短路不抛（补强）
作为 cancel_task 前置守门，判错风险已被这 6 个用例封死。

### 5. worker 异常 100% 感知 — PASS
start 路径（`test_cp_d2_6`）+ resume 路径（`test_cp_d2_6_resume...`）均验证 mock invoke 抛 `LLMError` 后 `_worker_errors[tid]` 写入同一对象、`get_worker_error` 加锁读出。`_worker_run` / `_resume_run` 用 `except Exception`（noqa BLE001）统一兜底，100% 崩溃感知。

### 6. api_key 逐条刷新 + 空 override 清理 — PASS
`_refresh_llm_config_set` 逐条 `dict(node_cfg)` 复制 default + 每条 override；空 LLMConfig（`if not node_cfg`）被清理出 overrides。补强三态用例实证：全空→{}、部分空（仅 paper_intake 保留，planning/analysis/scout 空被清理）、全填 4 个全保留且各为新 dict（非引用复用）。额外验证非法节点名（如 execution）被忽略 + WARNING，overrides 键缺失安全降级。CP-D2-8 进一步截获 worker initial_state 断言 5 条 api_key 与表单提交值字节一致。

### 7. OBS-D1-01（配置来源走返回值）— PASS
独立审 `ui/components/llm_config_form.py::render_llm_config_form`：成功时写 `st.session_state[SESSION_KEY]` 并返回 config_set（L247-249），**校验失败时 `return None`（L214/245）但不清除该键** → stale 键背景成立。`app.py::_render_sidebar` 拿 `cfg = render_llm_config_form(default=prefill)` 并 `return cfg`（返回值），prefill 仅作回显输入、不作权威配置源。main() 的注释也明确"返回值在 D3 传给 start_task"。未把 stale `session_state["llm_config_set"]` 当权威。**符合。**（注：真正的"传给 start_task"消费点在 D3 paper_input 页，D2 仅搭路由骨架，本条在 D2 范围内已正确建立返回值通道。）

### 8. controller 单例 — PASS
`_get_controller()` 检查 `"graph_controller" not in st.session_state` 才构造，存入 session_state 复用。补强 `test_bnd_get_controller_singleton_not_rebuilt`：patch `st.session_state` 为 FakeSessionState，两次调用 `c1 is c2` 且 `session_state["graph_controller"] is c1`，rerun 不重建。

### 9. requirements.txt — PASS
`git diff HEAD -- requirements.txt` 确认 **sp1 部分零改动**，仅在文件尾部追加 sp2 块（`streamlit>=1.57` + `streamlit-autorefresh>=1.0.0`，含说明注释）。下界 >=1.57 高于 dev-plan 初稿 >=1.28.0，与 venv 实测 1.58.0 / developing-with-streamlit skill 需求对齐，合理。

---

## 与架构 §2.7 的偏差核查

- **唯一差异（非 BUG，属架构参考代码滞后）**：架构 §2.7.1 参考实现的 config 为 `{"configurable": {"thread_id": thread_id}}`（**无 checkpoint_ns**），而 `app.py` 实际统一经 `_make_config` 注入 `checkpoint_ns=""`。**实现正确**：S-2 spike（2026-05-24）已实锤 LangGraph 1.1.10 的 `SqliteSaver.put` 强制要求 checkpoint_ns，dev-plan TODO L50 与 S-2 报告均要求 GraphController 封装 `_make_config` 注入。§2.7.1 的代码块是 spike 之前的旧形态，dev-plan §D2「3. 关键约束」与 S-2 报告才是最终权威。**建议架构师在 §2.7.1 代码块补注 checkpoint_ns 注入**（文档对齐项，不阻塞 D2 验收）。
- 其余方法签名（8 方法）、cancel_task 逻辑、api_key 注入策略、线程模型均与 §2.7.1 / §2.7.2 / §4.3 一致。

---

## 失败排查

无失败。

---

## 补强用例清单（16 个，测试工程师新增）

| 用例 | 覆盖维度 |
|------|---------|
| test_bnd_cancel_at_end_state_ignored_warns | cancel 在 END 状态忽略 + WARNING + 不起线程 |
| test_bnd_cancel_at_running_node_ignored_warns | cancel 在普通节点暂停状态忽略（最易判错边界） |
| test_bnd_is_interrupted_empty_tasks_returns_false | is_interrupted tasks 空元组路径 |
| test_bnd_is_interrupted_task_with_empty_interrupts_returns_false | tasks 非空但 interrupts 空（与上不同路径） |
| test_bnd_is_interrupted_no_snapshot_returns_false | 无 snapshot 短路不抛 |
| test_bnd_is_interrupted_multiple_tasks_one_with_interrupt | 多 task 任一命中 |
| test_bnd_make_config_injected_on_get_state_path | get_state 路径注入 checkpoint_ns |
| test_bnd_make_config_injected_on_invoke_paths | start + resume 两 invoke 路径注入 checkpoint_ns |
| test_bnd_refresh_overrides_all_empty | _refresh 全空态 |
| test_bnd_refresh_overrides_partial | _refresh 部分空态（清理） |
| test_bnd_refresh_overrides_all_filled | _refresh 全填态（非引用复用） |
| test_bnd_refresh_drops_illegal_node_name | 非法节点名忽略 + WARNING |
| test_bnd_refresh_handles_missing_overrides_key | overrides 键缺失降级 |
| test_bnd_poll_state_unknown_thread_returns_none | poll_state 不存在 thread_id |
| test_bnd_four_threads_independent_savers_real_sqlite | 并发 N=4 真实 SqliteSaver 隔离 |
| test_bnd_get_controller_singleton_not_rebuilt | 单例不被 rerun 重建 |

D2 单测最终总数：**31 用例**（开发 15 + 补强 16）。

---

## 后续动作

- [遗留-警告] `LangChainPendingDeprecationWarning`（langgraph `checkpoint/serde/encrypted.py:5`，`JsonPlusSerializer` 的 `allowed_objects` 默认值将变更）是**库级**警告，非项目代码可控；全 sp1/sp2 套件长期存在 1 条。当前不阻塞，建议在 sp2 收尾或升级 langgraph 时统一评估是否显式传 `allowed_objects=` 消除。已记入此报告作为长期跟踪项。
- [文档对齐] 建议架构师在 architecture.md §2.7.1 参考代码块补注 `checkpoint_ns=""` 注入（与 S-2 spike / dev-plan §D2 最终约束对齐）；非阻塞。
- [E 阶段] 真实 LLM + interrupt resume 的 GraphController 端到端（start → planning interrupt → resume_with(approve/revise/cancel) → poll 验证）建议在 D3/D4/D5 三页面就位后随 E1/E3 一并跑；D2 本身已用 FakeGraph + 真实 SqliteSaver 充分验证线程/持久化骨架。

## 需 Maria 决策的阻断点

无。D2 验收 PASS，可放行 D3/D4/D5。
