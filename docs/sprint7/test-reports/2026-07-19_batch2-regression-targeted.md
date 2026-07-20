# 测试执行报告 - batch2-regression-targeted

- **日期**：2026-07-19 08:50（本地时区 PDT）
- **执行人**：@测试工程师代理
- **Sprint**：sprint7
- **触发原因**：批次 2 收口批「非配额部分」T-S7-2-1（全量回归零退化复验 + AC 覆盖矩阵审计）+ T-S7-2-2（现场靶测 + fixture 固化）。批次 0+1 已 commit（`4dc0a75`）。
- **commit**：`4dc0a75`（工作区 clean 起手；本报告仅新增 `tests/fixtures/checkpoints_s7_99eef17bccf2.db` + `tests/test_sprint7_targeted.py` + 本报告，未 commit，主控统一收口）

---

## 执行范围

- **命令**：
  - `.venv/bin/pytest -q`（默认口径，含 browser，addopts `-m "not e2e"`）
  - `.venv/bin/pytest -q -m "not e2e and not browser"`（确定性口径，排除 pre-existing browser flaky）
  - `.venv/bin/pytest tests/test_sprint7_targeted.py -v`（新增现场靶测）
  - 幂等套件连跑 3 次：`tests/test_graph.py tests/test_react_base.py tests/test_sprint7_s7_01_budget_gate_sink.py tests/test_sprint7_s7_02_persist_log.py tests/test_sprint7_targeted.py`
  - Prompt 前缀稳定套件：`tests/test_sprint5_t13_coding_prompt.py tests/test_sprint5_t14_execution_prompt.py tests/test_sprint7_s7_03_max_rounds_clamp.py tests/test_sprint7_s7_02_coding_feedback.py`
- **覆盖用例**：全量 sp1~sp7 + 新增 `test_sprint7_targeted.py`（7 靶测）
- **是否包含 e2e**：**否**（严禁真跑，T-S7-2-3 主控职责）。凭证未读、无网络调用、无 deepxiv/LLM 配额消耗。

---

## 结果摘要

### CP-2.1-1 全量回归（独立复验）

| 口径 | passed | failed | skipped | deselected | 耗时 |
|------|--------|--------|---------|-----------|------|
| 默认 `-m "not e2e"`（含 browser，**未加新靶测前**） | 1989 | 1（browser flaky） | 25 | 45 | 152.04s |
| 确定性 `not e2e and not browser`（**未加新靶测前**） | 1978 | 0 | 25 | 57 | 61.13s |
| 确定性 `not e2e and not browser`（**含新增 7 靶测**） | 1985 | 0 | 25 | 57 | 60.59s |

**账目精确闭合**：全库总收集 2060；默认口径收集 2015（deselected 45=e2e）；browser marker 12 个。
- 默认全量：1989 passed + 1 failed(flaky) + 25 skipped = 2015 ✓
- 确定性口径：2015 − 12 browser = 2003 → 1978 passed + 25 skipped = 2003 ✓，deselected 57 = 45 e2e + 12 browser ✓
- 加新靶测：1978 + 7 = 1985 ✓

**结论**：sp7 批次 0+1 三改动（execution.py / coding.py / config.py 修复循环失控治理）**零回归退化**。唯一失败是 pre-existing browser flaky（下方排查坐实非 sp7 回归）。

### CP-2.1-3 幂等套件零退化

幂等 + 两段式 + sp7 修复循环套件连跑 3 次，稳定 **46 passed / 0 failed**，零抖动。S-1 重跑 guard + interrupt#2 两段式契约零退化。

### CP-2.1-4 Prompt Cache 前缀字节稳定

Prompt/context 前缀稳定套件 **40 passed**。R-PC4 无扰双重复核：
- 批次 1 CP-1.1-4：不同 dev_calls 下 HumanMessage context 的 max_rounds 恒为联动值。
- 现场靶测 `test_cp_2_2_4_field_dev_calls_clamps_within_sub_budget`：现场 dev_calls=92 收窄护栏生效但 context max_rounds 保联动值不回灌（S7-03 收窄不污染稳定前缀）。
- S7-02 指引串走 `last_error_summary` 动态通道（`_STDERR_TAIL_GUIDANCE`），不进稳定前缀。

### CP-2.2 现场靶测

`tests/test_sprint7_targeted.py` **7 passed / 0 failed**（每用例独立可跑已抽验）。

- **总通过**：1985（确定性口径含新靶测）
- **失败**：0（确定性口径）；默认口径 1 个 browser flaky（非 sp7 回归）
- **跳过**：25（e2e 凭证类 skip + 各模块环境 skip，非本批引入）
- **警告**：3（详见下方警告段）
- **总耗时**：确定性全量 60.59s；默认全量 152.04s（含 browser 起 chromium）

---

## 失败排查

### 失败用例：`tests/test_plan_review_e2e.py::test_e2e_code_only`

- **文件路径**：`tests/test_plan_review_e2e.py::test_e2e_code_only`
- **失败类型**：**外部依赖抖动 / pre-existing browser flaky**（非生产代码 bug、非 sp7 回归、非测试代码 bug）
- **关键报错**：
  ```
  AssertionError: 未找到/点不到「仅复现代码」按钮
  where False = _click_in_frame(<Page url='http://127.0.0.1:32529/'>, '仅复现代码')
  tests/test_plan_review_e2e.py:293
  ```
- **排查步骤与结论**：
  1. 该测试 marker 是 `@pytest.mark.browser`（`pytestmark = pytest.mark.browser`，:48），**不被 addopts `-m "not e2e"` 排除**，故默认全量会收集运行。它真起 streamlit 子进程 + chromium 点 iframe 按钮。
  2. **复跑 1（整文件）**：`pytest tests/test_plan_review_e2e.py -q -m browser` → **6 passed（含 test_e2e_code_only）**，41s。
  3. **复跑 2（单用例）**：`pytest tests/test_plan_review_e2e.py::test_e2e_code_only -q -m browser` → **failed**（同样 `_click_in_frame` 定位失败）。
  4. **矛盾即 flaky 铁证**：同源码，整文件跑通、单用例跑挂。根因是 `test_e2e_code_only` 依赖同文件前序用例（`test_e2e_approve` 等）先把 streamlit 页面/iframe 渲染热起来的时序状态；单独跑时页面冷启动、iframe 按钮尚未就绪 → 定位失败。属 **Playwright 交互时序 / 页面就绪竞争**。
  5. **与 sp7 正交**：sp7 三改动落点是 `core/nodes/execution.py` + `core/nodes/coding.py` + `config.py`（修复循环治理）；plan_review 页决策按钮与修复循环无关。TODO 批次 0/1 已记录此测试为 pre-existing chromium flaky（git stash 回改前也挂，坐实非翻倍回归）。
- **处置**：**标记 flaky 待观察，非 sp7 回归**。为拿干净 sp7 基线，正式口径用 `-m "not e2e and not browser"`（1985 passed 零失败）。browser 层 flaky 属独立稳定性问题（非本批范围），建议后续单独治理（如给 test_e2e_code_only 加页面就绪等待 / 独立页面 setup）。

---

## 警告（3 处，均为 pre-existing、非 sp7 引入）

1. `LangChainPendingDeprecationWarning`（`langgraph/checkpoint/serde/encrypted.py:5`）：`allowed_objects` 默认值将变，第三方库 langgraph 内部，非项目代码。
2. `Deserializing unregistered type core.state.ExecutionMode`（加载 fixture checkpoint 时）：langgraph msgpack 反序列化未注册类型提示，加载真现场 checkpoint 时出现，功能正常。
3. `PydanticDeprecatedSince20`（`tests/test_sprint6_b2.py:162-163`）：`.schema()` 方法弃用，建议改 `model_json_schema()`。这是**测试代码**里的 pre-existing warning（sp6 遗留，非 sp7 引入），建议后续测试维护窗口修正。

以上 3 处均为长期存在的项目级 warning，记入本报告备查，不阻塞本批交付。

---

## CP-2.2-1 fixture 固化证据

- **源库**：`checkpoints.db`（仓库根，99434496 bytes ≈ 99MB，含 22 个 thread、2514 checkpoints）
- **抽取方式**：`sqlite3` 只读 URI（`file:checkpoints.db?mode=ro`）打开源库 → 复用源库 schema DDL 在新库建 checkpoints/writes 表 → `INSERT` 目标 thread `task-99eef17bccf2` 的行（**复制不移动**，源库零写入）
- **抽取行数（逐一 MATCH）**：checkpoints 489 行、writes 1585 行、distinct threads 1
- **源库 md5 前后 MATCH**：固化前后均为 `986aeb2360c2f2b1d4d49523712aea07`，mtime 零变动（2026-07-18 02:58:46）。靶测运行后再次核验仍 `986aeb2360c2f2b1d4d49523712aea07`（源库零触碰）
  - 注：只读打开源库时 SQLite WAL 模式会建 `checkpoints.db-shm`（32KB）/ `checkpoints.db-wal`（0 字节）旁文件——属 WAL 正常副产物，**主库字节 md5 零变动**，符合铁律。
- **fixture db**：`tests/fixtures/checkpoints_s7_99eef17bccf2.db`，**21MB**（从 99MB 精简到单 thread），md5 固化基线 `3483890cd0197a27309543a48a2ece3f`
- **CP-2.2-3 只读契约验证**：靶测运行后 fixture md5 仍 `3483890cd0197a27309543a48a2ece3f`（零变动）、无 -wal/-shm 旁文件（只读连接 + `is_setup=True` 跳过 SqliteSaver.setup 的 WAL/建表写操作）
- **现场字段与架构 §9.1 声明逐一 MATCH**（`test_cp_2_2_0_field_fixture_contract` 锚定）：
  `retry_budget_remaining=0` / `_dev_loop_llm_calls=92` / `fix_loop_count=4` / fix_loop_history 4 条全 `import` / `execution_result.success=False` / logs 含 `No module named 'src'`（位置 13772/16435，尾部）/ `current_step='reporting'`（缺陷现场：静默降级）/ `user_fix_decision=None`

---

## CP-2.2-2~4 三靶测结果 + 锚定 AC

| 用例 | 缺陷 | 锚定 AC | 结论 |
|------|------|--------|------|
| `test_cp_2_2_0_field_fixture_contract` | fixture 契约 | 现场前提守门 | ✅ 现场字段与 §9.1 逐一 MATCH |
| `test_cp_2_2_2_field_budget_exhausted_no_silent_degrade` | ① S7-01 | AC-S7-01 | ✅ 现场 budget=0/dev_calls=92 驱动 → 不再 `_mark_degraded_for_report`、置 await 标记（非静默降级 reporting） |
| `test_cp_2_2_2_field_two_phase_reaches_interrupt` | ① S7-01 | AC-S7-02 | ✅ 现场同构 graph → 两段式抵达 interrupt#2、guard 命中 sandbox 不重跑、options 三态无第四态 |
| `test_cp_2_2_3_field_logs_persist_and_readable` | ② S7-02 | AC-S7-05 | ✅ 现场失败步 stderr → 落盘 round_0.log 含真报错、错误优先编排前置到头 8000 内、log_file_path 子键、read_code_file 可读到 `No module named 'src'` |
| `test_cp_2_2_3_field_stderr_tail_is_guidance_not_field_logs` | ② S7-02 | AC-S7-07 | ✅ stderr_tail 为固定指引串、**与现场 logs[-2000:] 互斥**（现场铁证：旧实现 logs[-2000:] 根本读不到真报错，尾部是成功步 stdout） |
| `test_cp_2_2_4_field_dev_calls_clamps_within_sub_budget` | ③ S7-03 | AC-S7-08 | ✅ 现场 dev_calls=92 → 收窄 effective_max_rounds=28（=剩余子预算 120-92）、≤ 子上限、context max_rounds 保联动值（R-PC4） |
| `test_cp_2_2_4_field_clamp_bounds_over_run_vs_field_bug` | ③ S7-03 | AC-S7-08 | ✅ 收窄后越界上界（force_finish 1 + metrics 3）远小于现场缺陷幅度 32 |

**CP-2.2-5 入 CI 确认**：7 靶测无 e2e/browser marker，默认 addopts 口径 + `not e2e and not browser` 口径均被收集（7 tests collected），随全量跑，永久防回归入 CI。

**现场靶测差异化价值（vs 批次 1 同构 mock）**：批次 1 四文件用同构 mock 自证；本文件用真现场 fixture 驱动，最硬铁证是 AC-S7-07——现场 `logs[-2000:]`（旧实现）**根本读不到 `No module named 'src'`**（尾部恰是 step#11 成功步 stdout），逐字坐实 Maria 现场质疑的 S7-02 缺陷本质。这是同构 mock 无法提供的真数据验红。

---

## 后续动作

- **凭证补齐 + Maria 授权后**：主控执行 T-S7-2-3 真跑 e2e（真实 LLM + deepxiv，耗日配额）。真跑入口情报见本批 handoff（另附主控回报第 7 段）。
- **browser flaky 待观察**：`test_e2e_code_only` 独立治理（非本批范围），建议加页面就绪等待。
- **pre-existing warning 清理**：`tests/test_sprint6_b2.py` 的 `.schema()` → `model_json_schema()`（后续测试维护窗口）。
- **commit 时机**：等 Maria 定，主控统一收口。
