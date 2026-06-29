# 测试执行报告 - f1-acceptance（含 F2 CP-F2-1 mock e2e）

- **日期**：2026-06-29 00:45 PDT
- **执行人**：@测试工程师代理
- **Sprint**：sprint3
- **触发原因**：① 任务 F1（mock 单测全套 + AC 覆盖矩阵）独立验收（不轻信开发自测）；② 补全 F2 CP-F2-1（5 条 mock e2e 骨架，不依赖凭证）。
- **commit**：8b62230（基线 1056；F1 19 + F2 8 用例为工作树未提交新增）

## 执行范围
- 命令：
  - `pytest tests/test_sprint3_f1.py -v`（F1 自测复现）
  - 独立探针 `python -` 直跑 `core.nodes.execution.execution`（CP-F1-2 / CP-F1-3 行为级复核，不依赖开发断言）
  - `pytest <AC 映射的 26 个 CP 测试函数> -v`（AC 矩阵逐条有效性审计）
  - `pytest tests/test_sprint3_e2e.py -v`（F2 5 条 mock e2e）
  - `pytest tests/test_sprint3_e2e.py -m "not e2e" -q`（确认进默认回归）×3（flaky 检查）
  - `pytest -q -m "not e2e" --ignore=tests/test_paper_intake.py`（全量回归）
- 覆盖用例：
  - F1：`tests/test_sprint3_f1.py`（19 用例 CP-F1-1~4）
  - F2：新建 `tests/test_sprint3_e2e.py`（8 mock e2e + 1 真实链路骨架 skip）
  - AC 审计：b1 / c3 / a1 / a2 / c1 / c2 / d1 中的 26 个 AC 映射 CP 测试函数
- 是否包含 e2e：**否**（未跑真实 e2e，省 deepxiv 日配额）。CP-F2-2 真实链路骨架 `@pytest.mark.e2e` 凭证 skip + 默认回归 deselect，留 Maria 凭证/配额就绪后补跑。

## 结果摘要
- 通过：1083（全量非 e2e 回归）
- 失败：0
- 跳过：25（D3/D4 UI shadcn iframe 设计性 skip，与本次无关，历史一致）
- 反选：29（历史 e2e marker 28 + 本次新增真实链路骨架 1）
- 警告：1（langgraph 库级 LangChainPendingDeprecationWarning，sp1 即存，非新引入）
- 总耗时：121.26s（全量回归）；F2 mock e2e 单跑 0.72s
- 基线对账：F1 后基线 **1075** + F2 mock e2e **8** = **1083**，逐项吻合零退化。

---

## 第一部分：F1 独立验收

### 裁决：**PASS**

### CP-F1-1（全套 mock 单测 + 全量回归不退化）— PASS
- `tests/test_sprint3_f1.py` 19 passed（0.91s）复现。
- 全量回归 1083 passed（含本次 F2 新增 8），= F1 基线 1075 + 8，零退化；25 skip 与基线一致。
- F1 自报 1075 数字独立复核成立（本次在其上叠加 F2 8 条 = 1083）。

### CP-F1-2（must-fix-1，AC-S3-05）— PASS（不止信开发断言，行为级独立复核）
- grep `core/state.py` 三字段（node_errors / degraded_nodes / fix_loop_history）无 `Annotated`/`operator.add`；`GlobalState.__annotations__` 三字段 `typing.get_origin is list`。
- **独立探针端到端聚合**：预置 state（1 条旧 node_error[OLD] + 1 条旧 fix_record + degraded[upstream_x]），mock 一次可修复失败（ModuleNotFoundError），跑真实 `execution()`：
  - node_errors 旧1+新1=2，OLD 保留恰 **1 次**（无丢失无重复累加）；
  - degraded_nodes 保留 upstream_x（read-modify-write 非覆盖）；
  - fix_loop_history 旧1+新1=2，旧 round=1 保留；
  - **原 state 三 list 字段未被原地 mutate**（仍 1/1/1）。
- 观察（非缺陷）：新 FixLoopRecord 字段名为 `round_number`（TypedDict 定义），非旧探针用的 `round`；F1 用例对新记录不检查 round，只检查旧记录，断言正确，无影响。

### CP-F1-3（must-fix-2，AC-S3-04）— PASS（行为级独立复核）
- ② 子预算 `MAX_DEV_LOOP_LLM_CALLS=20 < MAX_TOTAL_LLM_CALLS=50`。
- ① 预算回写：exit0 + 无 `<METRICS>` 逼 LLM 抽取（mock 返回 1 指标耗 2 次）→ `retry_budget_remaining` 40-2=**38** + `_dev_loop_llm_calls`=**2**，且抽到指标 → B 档成功。
- ① 补：`<METRICS>` 命中（无 LLM 抽取）→ updates 不含预算键（零扣减）。
- ③ 入口预算门：可修复失败 + budget=1（< DEV_LOOP_MIN_CALLS_PER_ROUND=2）→ degraded_nodes=['execution']、`_dev_loop_route`=None（≠ retry_coding）、fix_loop_count 不自增。
- ③ 补：子预算触顶（`_dev_loop_llm_calls`=20）+ 可修复失败 → `_dev_loop_route`=await_dev_loop_interrupt（走 interrupt 而非 retry_coding），fix_loop_count 不自增。

### CP-F1-4 + AC 矩阵审计（重点）— PASS，**无空泛、无缺口**
- 逐条独立运行 AC-S3-02/03/04/05/06/07(mock)/08/09/10 映射的 **26 个 CP 测试函数全 PASS**。
- **断言有效性审计**（防"名字对但断言空泛"）：抽查源码确认断言的是对的东西，举证：
  - AC-07 `c3::test_cp_c3_7_interrupt_three_state_resume`（参数化三态）：真用最小 self-loop StateGraph + InMemorySaver + `Command(resume=...)` 跑真实 interrupt，断言 `interrupt_kind=="dev_loop_failure"` + 三态各自 `user_fix_decision`/`current_step=cancelled_by_user`/`approved=False`/`fix_loop_count==0` 清零/`degraded_nodes` + **sandbox prepare 恒==1**（幂等）——强断言。
  - AC-03 `c3::test_cp_c3_5_upper_limit_to_interrupt`：fix_loop_count==3 + 可修复失败 → `_dev_loop_route==await` + `"fix_loop_count" not in out`（绝不自增）——强断言。
  - AC-08 `c3::test_cp_c3_6_unfixable_no_retry`：CUDA OOM → permanent + `[error_category=hardware]` 前缀 + 不自增——强断言。
- 元断言 `test_cp_f1_4_coverage_map_spans_all_f1_owned_acs` 恰覆盖 9 条 AC（02~10），AC-S3-01 真实 happy path / AC-07 真实三态 e2e 明确留 F2，划界正确。
- **覆盖薄弱点识别（已由 F2 填补）**：F1 的 AC 映射全部是单节点级 mock / 最小子图 / build_graph branch mapping 静态检查，**无一条用真实 `build_graph()` 全主图从 START 跑到 END/interrupt 的集成测试**。这正是 F2 CP-F2-1 的增量价值，本次已补（见第二部分）。

### F1 补强数：**0**（F1 既有 AC 映射真实有效、断言对的东西，无缺口需补强；薄弱的集成层由 F2 CP-F2-1 填补，归入第二部分）

---

## 第二部分：F2 CP-F2-1（5 条 mock e2e，不依赖凭证）

### 产出：新建 `tests/test_sprint3_e2e.py`（8 mock e2e + 1 真实链路骨架）

设计：**真实 `build_graph()` 主图 + InMemorySaver**，上游 4 节点（intake/analysis/scout/planning）+ coding 用 `patch.object(graph_module, ...)` 替为 fake（避免真实 LLM/SDK/interrupt#1），**execution 真实 + patch 其模块内 sandbox 三入口（prepare_venv/run_in_venv/collect_artifacts）+ _llm_extract_metrics**，**reporting 真实**（`state.workspace_dir` 指向 tmp_path 真落盘不污染真实 workspace）。集成视角，比 c3/d1 单节点测试高一层。唯一 uuid4 thread_id 防串。

| 用例 | 场景 | AC | 结果 |
|---|---|---|---|
| `test_f2_e2e_1_happy_path_b_grade_success_full_mode` | FULL 跑通 START→…→reporting→END，exit0+`<METRICS>` → success=True + full_success 报告（含复现值 0.893 + baseline 0.91 对比 + 无硬性达标结论） | AC-S3-01 | PASS |
| `test_f2_e2e_2_fix_loop_upper_limit_three_then_interrupt` | 连续可修复失败 → fix_loop_count 0→3 拦截 → coding 被进入 ≥4 次（回边真走通）+ fix_loop_history 满 3 条 → interrupt#2 暂停（kind=dev_loop_failure + options 三态） | AC-S3-03 | PASS |
| `test_f2_e2e_3_interrupt2_three_state_resume[terminate/revise_plan/export_code]` | interrupt#2 暂停后 `Command(resume=...)` 三态 → 真实图路由：terminate→END(cancelled_by_user) / revise_plan→planning(fix_loop_count 清零 + 再走一轮 prepare=2) / export_code→reporting(degraded)；幂等：非 revise 分支 sandbox prepare 恒==1 | AC-S3-07 | PASS×3 |
| `test_f2_e2e_4_code_only_skips_execution` | planning code_only → coding 出边 skip_execution → reporting code_only；**execution 未执行（sandbox prepare==0）** + execution_result is None + 报告标注"仅生成代码" | AC-S3-06 | PASS |
| `test_f2_e2e_5a_degraded_budget_exhausted` | 预算耗尽（入口预算门 budget=1<2）→ 直接降级不 interrupt → reporting degraded（含未成功/降级结论）+ sandbox 只跑 1 次 | AC-S3-09③ | PASS |
| `test_f2_e2e_5b_degraded_unfixable_then_export` | 不可修复（CUDA OOM）→ interrupt#2 → export_code → degraded 报告（含 hardware 分类）+ node_errors permanent + `[error_category=hardware]` 前缀 | AC-S3-09③ | PASS |
| `TestRealChainSkeleton::test_real_chain_placeholder` | CP-F2-2 真实链路骨架（`@pytest.mark.e2e` 凭证 skip + 默认回归 deselect） | — | SKIPPED（留补跑） |

### 约束符合性
- **CP-F2-1 = mock 版本，不标 `@pytest.mark.e2e`** ✓：`pytest tests/test_sprint3_e2e.py -m "not e2e" -q` → **8 passed / 1 deselected**（真实链路骨架被 deselect），进常规回归、不依赖凭证、不耗 deepxiv 配额。
- **CP-F2-2 真实链路骨架已预备** ✓：`TestRealChainSkeleton` 标 `@pytest.mark.e2e` + 凭证 skipif，含 5 条真实场景待补跑清单（real-1~5）+ dev-plan §674 复跑要求注记，**本次不跑真实 e2e**。
- 唯一 uuid4 thread_id 防串 ✓；连跑 3 次 8×3 全绿 **0 flaky**。

### 过程修正（测试代码自身，非生产 BUG）
- 初版 revise_plan 参数化 case 误用"resume 后 sandbox prepare 恒==1"幂等断言而失败：真实主图里 revise_plan → planning（fake_planning 未真改计划仍 approve）→ 再走 coding→execution（仍 OOM）→ 再 interrupt#2，sandbox 自然再跑一次（prepare=2）。这是**真实图正确行为**，非生产 bug。已修正断言：revise_plan 分支核心断言改为「user_fix_decision=revise_plan 落地 + fix_loop_count 清零（回问点2）+ 再走一轮 prepare=2 + 再次 interrupt」，幂等恒==1 断言仅对 terminate/export_code 生效。修正后三态全 PASS。

## 失败排查
无。F1 自测、AC 映射 26 函数、F2 8 mock e2e、全量回归 1083 全部 PASS，0 failed。（唯一一次 revise_plan 失败为测试自身断言不当，已就地修正并复核，属测试代码 bug 非生产 bug，详见上「过程修正」。）

## 后续动作
- **CP-F2-2**：凭证 + deepxiv 配额就绪后补跑 5 条真实链路 e2e（`tests/test_sprint3_e2e.py::TestRealChainSkeleton` 占位 + 待补跑清单 real-1~5），转正。复跑要求见 dev-plan §674（复现率高连跑 3 次 / 低连跑 5 次含全量回归）。触发条件：Maria 凭证/配额就绪。
- **CP-F2-3**：e2e 报告归档（凭证补跑时落盘跑数/耗时）。
- **F3**：Prompt Cache 守门 + handoff（AC 矩阵已为 F3 handoff 备好）。
- 项目级遗留 warning（langgraph 库级 LangChainPendingDeprecationWarning，sp1 即存非新引入，待观察）；L-A3-02（pytest.ini markers 注释与凭证驱动实现不符，非阻断文档偏差）仍在。
- **零生产 BUG，零文档硬偏差。未 git commit（待 Maria 统一）。**
