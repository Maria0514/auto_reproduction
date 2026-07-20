# AC-S7-01~08 覆盖矩阵审计（CP-2.1-2 / CP-2.2-4）

- **日期**：2026-07-19 08:55（PDT）
- **执行人**：@测试工程师代理
- **Sprint**：sprint7
- **触发原因**：批次 2 T-S7-2-1 逐 AC 覆盖矩阵审计——每条 AC 至少一个可测断言映射，验红点如实标注，缺口不凑绿。
- **commit**：`4dc0a75`
- **基准**：`docs/sprint7/prd.md` AC-S7-01~08（§204-211）+ `architecture.md` §9.2/§12 AC→组件映射

---

## 图例

- ✅ **覆盖**：有确定性 pytest 断言映射，验证通过。
- 🖐️ **手动走查**：无自动断言，靠源码 review / 现场证据坐实（如全量回归零失败）。
- ⚠️ **缺口**：无测试覆盖，如实标出。
- 🔴 **验红铁证**：注掉对应改动 → 断言变红（防假绿），批次 1 已坐实。

---

## 覆盖矩阵（逐条 AC）

### AC-S7-01（S7-01：预算耗尽不静默降级、抵达 interrupt#2）

| 维度 | 测试用例（文件::函数） | 状态 |
|------|----------------------|------|
| 同构 mock：budget=0/success=False → 不再 `_mark_degraded_for_report`、置 await | `test_sprint7_s7_01_budget_gate_sink.py::test_cp_1_4_1_no_silent_degrade_first_entry` | ✅ |
| **源码锁定验红**：`reason="budget_exhausted"` return 已删、`budget >= DEV_LOOP_MIN_CALLS_PER_ROUND` 下沉为准入条件（`inspect.getsource`） | `..._budget_gate_sink.py::test_cp_1_4_1_degrade_return_deleted_from_function` | ✅🔴（活验红：源码互斥锁定） |
| **现场靶**：真 fixture budget=0/dev_calls=92 驱动 → 不降级、置 await、不回 coding | `test_sprint7_targeted.py::test_cp_2_2_2_field_budget_exhausted_no_silent_degrade` | ✅（真现场数据） |

**结论**：✅ 覆盖 + 现场靶双验。缺陷现场铁证：fixture `current_step='reporting'` / `user_fix_decision=None`（治理前静默降级），治理后置 await 进 interrupt#2。

### AC-S7-02（S7-01：经两段式抵达 interrupt、S-1 幂等不破）

| 维度 | 测试用例 | 状态 |
|------|---------|------|
| 同构 mock：首次 return await、self-loop 重入 guard 命中 interrupt 恰一次、sandbox 不重跑 | `..._budget_gate_sink.py::test_cp_1_4_2_two_phase_idempotent_budget_exhausted` | ✅ |
| **现场靶**：现场同构 graph → 两段式抵达 interrupt#2、agent 恰跑 1 次、options 三态 | `test_sprint7_targeted.py::test_cp_2_2_2_field_two_phase_reaches_interrupt` | ✅（真现场数据） |
| 既有 S-1 幂等套件零退化 | `test_graph.py` + `test_react_base.py`（连跑 3 次 46 passed） | ✅ |

**结论**：✅ 覆盖。

### AC-S7-03（面板文案 + 三态守门 + payload 键结构不变）

| 维度 | 测试用例 | 状态 |
|------|---------|------|
| 预算耗尽 → error_summary 含"预算已耗尽"、fix_hint 走 replace 注入 | `..._budget_gate_sink.py::test_cp_1_4_3_budget_exhausted_panel_text_and_three_state` | ✅ |
| **对照用例**（防文案泛化）：预算充足+子上限触顶 → 不含预算耗尽文案 | `..._budget_gate_sink.py::test_cp_1_4_3_control_non_budget_no_budget_text` | ✅ |
| payload 键集合与 sp6 逐字一致（10 键）；options==["terminate","revise_plan","export_code"] 无第四态 | `..._budget_gate_sink.py::test_cp_1_4_3_budget_exhausted_panel_text_and_three_state`（`set(payload.keys())==...`） | ✅ |
| execution_monitor.py 面板渲染逻辑零改（文案走数据通道） | 🖐️ 架构 §4.5 + §8 坐实、grep 亲验零改（批次 1 主控收口令） | 🖐️（源码 review） |

**结论**：✅ 覆盖。面板文件零改由架构约束 + 源码 review 守护（数据通道注入，非渲染逻辑改）。

### AC-S7-04（硬上限翻倍后守门、revise 重置不破硬顶）

| 维度 | 测试用例 | 状态 |
|------|---------|------|
| revise 后子上限仍拦：dev_calls=120 → 不回 coding（不突破 120） | `..._budget_gate_sink.py::test_cp_1_4_4_dev_loop_ceiling_still_blocks_after_revise` | ✅ |
| revise 全额重置为 MAX_TOTAL_LLM_CALLS(240)，不突破 240 硬顶 | `..._budget_gate_sink.py::test_cp_1_4_4_budget_reset_does_not_exceed_total_cap` | ✅ |
| revise → retry_budget=240 + fix_loop_count=0，`_dev_loop_llm_calls` 累计**未重置** | `..._budget_gate_sink.py::test_cp_1_4_5_revise_resets_budget_not_dev_calls` | ✅ |
| **对照**：terminate/export_code 不做预算重置 | `..._budget_gate_sink.py::test_cp_1_4_5_terminate_export_no_budget_reset` | ✅ |

**结论**：✅ 覆盖。R-S7-2（revise 全额重置被误读为绕过硬顶）由"dev_calls 不重置 + 子上限继续拦"守门。

### AC-S7-05（coder 见真错：日志文件路径存在且含真报错）

| 维度 | 测试用例 | 状态 |
|------|---------|------|
| import 失败现场 → round_{n}.log 落盘含真报错行 | `test_sprint7_s7_02_persist_log.py::test_cp_1_2_1_persist_import_failure` | ✅ |
| 错误优先编排：真报错落文件头 8000 内 | `..._persist_log.py::test_cp_1_2_2_error_first_within_8000` | ✅ |
| **主流程接线验红**：execution() 首跑经步骤 5-6 间接线落盘（注掉接线→红） | `..._persist_log.py::test_cp_1_2_1b_mainflow_wiring_persists` | ✅🔴 |
| 反馈含 log_file_path 子键 + error_category 保留 | `test_sprint7_s7_02_coding_feedback.py::test_cp_1_3_1_log_file_path_subkey_and_error_category` | ✅ |
| 端到端可读：read_code_file 读到 No module named 'src' | `..._coding_feedback.py::test_cp_1_3_2_end_to_end_readable` | ✅ |
| **现场靶**：现场失败步 stderr → 落盘 + 反馈路径 + read_code_file 读真报错（头 8000 内） | `test_sprint7_targeted.py::test_cp_2_2_3_field_logs_persist_and_readable` | ✅（真现场数据） |

**结论**：✅ 覆盖 + 现场靶双验 + 主流程接线验红铁证。

### AC-S7-06（13 常量翻倍 + 联动等式 + 旧断言同步）

| 维度 | 测试用例 | 状态 |
|------|---------|------|
| 13 常量翻倍值（MAX_TOTAL=240 / MAX_DEV_LOOP=120 / ...） | `test_sprint5_t11_config.py::TestBudgetConstants::test_budget_constants_baseline`（:58-60）+ config 值断言散布 sprint2/3/4/5 | ✅ |
| 联动等式 `CAP*2 == MAX_DEV_LOOP_LLM_CALLS`（60*2==120） | `test_sprint5_t25_budget_link.py`（:242 `CAP*2==MAX_DEV_LOOP`）+ `test_sprint5_t11_config.py::test_cap_equals_half_dev_loop_budget`（:39） | ✅ |
| 强约束 `MAX_DEV_LOOP_LLM_CALLS < MAX_TOTAL_LLM_CALLS`（120<240） | `test_sprint5_t25_budget_link.py`（联动账本对账 :240-244） | ✅ |
| 十几处旧硬编码断言同步（sprint2/3/4/5）+ 全量回归零失败 | 🖐️ 批次 0 已落（6 文件断言同步），本批全量回归 1985 passed 零失败坐实账目闭合 | ✅（全量回归佐证） |

**结论**：✅ 覆盖。翻倍值 + 联动等式 + 强约束三重断言，全量回归零失败佐证旧断言同步账目闭合。

### AC-S7-07（设计取舍守门：反馈以路径为准、非系统截断产物）**须验红**

| 维度 | 测试用例 | 状态 |
|------|---------|------|
| stderr_tail 不再是 logs[-2000:] 截断产物，而是固定指引串；反馈以 log_file_path 为准 | `test_sprint7_s7_02_coding_feedback.py::test_cp_1_3_3_stderr_tail_is_guidance_not_truncation` | ✅🔴（互斥断言：与旧实现互斥；注掉路径注入→红） |
| representative_stderr 未被 S7-02 触碰（恒空）；execution 侧 stderr_tail 维持尾部（AA-S7-3 正交） | `..._coding_feedback.py::test_cp_1_3_5_representative_stderr_untouched` + `test_cp_1_3_5_execution_side_stderr_tail_stays_tail` | ✅ |
| **现场靶验红**：stderr_tail 与现场 logs[-2000:] 互斥；现场 logs[-2000:] **根本读不到真报错**（尾部是成功步 stdout） | `test_sprint7_targeted.py::test_cp_2_2_3_field_stderr_tail_is_guidance_not_field_logs` | ✅🔴（真数据铁证） |

**结论**：✅ 覆盖 + 现场真数据验红。**最硬铁证**：现场 `logs[-2000:]`（旧实现）在真现场数据下根本不含 `No module named 'src'`（真报错在位置 13772、尾部 2000 是 step#11 成功步 stdout），逐字坐实 Maria 现场质疑的 S7-02 缺陷本质——同构 mock 无法提供此级别真数据验红。

### AC-S7-08（S7-03 单轮刹车：逼近子上限及时刹车、越界确定性小范围）**须验红**

| 维度 | 测试用例 | 状态 |
|------|---------|------|
| 收窄逻辑：dev_calls 逼近子上限 → effective_max_rounds = max(1, min(联动值, 剩余子预算)) | `test_sprint7_s7_03_max_rounds_clamp.py::test_cp_1_1_1_clamp_narrows_when_dev_calls_approach_ceiling` | ✅🔴（注掉 clamp → 5 红，批次 1 坐实） |
| dev_calls=0 → 不收窄退回联动值 | `..._max_rounds_clamp.py::test_cp_1_1_1_no_narrow_when_dev_calls_zero` | ✅ |
| 保底 1 轮（触顶/越顶 → 剩余 clamp 到 0 → 保底 1，防 0 轮死锁） | `..._max_rounds_clamp.py::test_cp_1_1_2_floor_one_round_when_budget_exhausted` / `test_cp_1_1_2_floor_one_round_when_over_ceiling` | ✅ |
| 越界上界确定性小值（远小于 CAP 级） | `..._max_rounds_clamp.py::test_cp_1_1_3_over_run_bound_is_deterministic_small` | ✅🔴 |
| R-PC4 无扰：context max_rounds 恒为联动值不随 dev_calls 抖 | `..._max_rounds_clamp.py::test_cp_1_1_4_context_max_rounds_invariant_across_dev_calls` | ✅ |
| **现场靶**：现场 dev_calls=92 → 收窄到 28（剩余子预算）、≤ 子上限、越界远小于现场缺陷 32 | `test_sprint7_targeted.py::test_cp_2_2_4_field_dev_calls_clamps_within_sub_budget` + `test_cp_2_2_4_field_clamp_bounds_over_run_vs_field_bug` | ✅（真现场数据） |

**结论**：✅ 覆盖 + 现场靶双验 + clamp 注掉验红铁证（批次 1 已坐实 5 红/恢复 6 绿）。

---

## 汇总

| AC | 状态 | 验红 | 现场靶（真 fixture） | 缺口 |
|----|------|------|--------------------|------|
| AC-S7-01 | ✅ | 🔴 源码锁定 | ✅ | 无 |
| AC-S7-02 | ✅ | — | ✅ | 无 |
| AC-S7-03 | ✅ | — | 部分（间接） | 无（面板零改靠 §4.5 + review） |
| AC-S7-04 | ✅ | — | 间接（现场 dev_calls 驱动） | 无 |
| AC-S7-05 | ✅ | 🔴 接线 | ✅ | 无 |
| AC-S7-06 | ✅ | — | — | 无（config 常量 + 全量回归佐证） |
| AC-S7-07 | ✅ | 🔴 互斥+真数据 | ✅ | 无 |
| AC-S7-08 | ✅ | 🔴 clamp 注掉 | ✅ | 无 |

**审计结论**：AC-S7-01~08 **全部 ✅ 覆盖，零 ⚠️ 缺口**。三项须验红（AC-S7-05/07/08）验红铁证齐备（批次 1 坐实 + 本批现场真数据加固）。四条以现场同构 thread 为强制回归靶的 AC（AC-S7-01/02/05 + S7-03 类 AC-S7-08）均有真 fixture 靶测覆盖，非仅同构 mock 自证——满足 PRD §214 强制回归靶要求，规避 sp5 AC-S5-03 mock 假绿教训。

## 已知限制（handoff 携带）

- AC-S7-03 面板文案的**真实 UI 渲染**（Streamlit 面板显示"预算已耗尽"）未做浏览器 e2e——靠 payload 数据通道断言 + execution_monitor.py 零改约束守护。真实渲染在 T-S7-2-3 真跑 e2e 顺带人工观察（非阻塞）。
- AC-S7-06 的"注释同步"（planning.py 等 =120→=240 注释）为非逻辑项，无自动断言，靠批次 0 源码 review 落实。
- R-S7-4（落盘失败降级到 sp6 现状）+ R-S7-3（极端超长日志 list_dir 逐读兜底）：批次 1 已覆盖落盘 IO 失败兜底（`test_cp_1_2_5_*`）+ 文件不存在退回 errors（`test_cp_1_3_4_*`）；极端超长日志的 list_dir 逐读为设计兜底，未专测（低频，接受）。
