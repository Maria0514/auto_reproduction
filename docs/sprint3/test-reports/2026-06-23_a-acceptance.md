# 测试执行报告 - a-acceptance（Sprint 3 阶段 A 独立验收）

- **日期**：2026-06-23 01:10（本地时区）
- **执行人**：@测试工程师代理
- **Sprint**：sprint3
- **触发原因**：全栈开发代理交付 Sprint 3 阶段 A（配置与状态层 / A1+A2）后的独立验收 + 边界补强（不轻信开发自测断言，逐条独立复核 CP-A1-1~4 / CP-A2-1~5）
- **commit**：d2f923f（工作区未提交：`config.py` / `core/state.py` / `docs/TODO.md` / `docs/sprint3/dev-plan.md` M，`tests/test_sprint3_a1.py` / `tests/test_sprint3_a2.py` / 本报告 untracked；按 Maria 要求不 git commit）

## 执行范围
- 命令：
  - 独立复核（不依赖测试断言）：`git diff HEAD --stat`、`grep -nE "Annotated|operator\.add" core/state.py`、`.venv/bin/python -c "..."` 运行时读取常量值/类型/注解/默认值
  - 阶段 A 三文件：`pytest tests/test_sprint3_a1.py tests/test_sprint3_a2.py tests/test_sprint3_a_boundary.py -v`
  - 全量回归：`pytest -q -m "not e2e" --ignore=tests/test_paper_intake.py`（×2）
  - 稳定性连跑：三文件 ×3、单用例独立性 ×1
- 覆盖用例：
  - 开发自测 16 条（`test_sprint3_a1.py` 9 条含 aux / `test_sprint3_a2.py` 7 条含 aux —— 注：开发报告称 16/16 CP，实际函数数为 16，CP 主体 9 个由这些函数覆盖）
  - 测试工程师补强 11 条（新建 `tests/test_sprint3_a_boundary.py`）
- 是否包含 e2e：否（阶段 A 为纯配置/状态层结构性断言，零 LLM / 零 deepxiv / 零网络；按红线不跑 e2e 省 deepxiv 日配额）

## 结果摘要
- 通过：586（全量回归）/ 27（阶段 A 三文件）
- 失败：0
- 跳过：25（与 sp2 基线一致，全为 shadcn 迁移后 AppTest 看不到 iframe 的 UI 断言，已由 Playwright browser e2e / logic 测试等价覆盖，设计性非退化）
- 取消选择（deselect）：28（e2e marker 用例，按红线不跑）
- 警告：1（`LangChainPendingDeprecationWarning`，详见下方"警告说明"）
- 总耗时：阶段 A 三文件 ~0.16s；全量回归 ~83s（两次 83.49s / 82.65s）

### 回归基线对账
| 项 | 数量 | 来源 |
|---|---|---|
| sp2 基线 passed | 559 | 2026-06-14 sp2 e2e 凭证补跑报告 |
| + 开发阶段 A 新增 | 16 | `test_sprint3_a1.py`(9) + `test_sprint3_a2.py`(7) |
| 开发报告回归 passed | 575 | 全栈代理自报，本人复核吻合 |
| + 测试工程师补强 | 11 | `test_sprint3_a_boundary.py` |
| **本次回归 passed** | **586** | 575 + 11，逐项吻合，既有 559 零退化 |

## CP 逐条独立复核结论（不依赖开发断言）

| CP | 复核手段 | 结论 |
|---|---|---|
| CP-A1-1 10 常量可导入 | `python -c "import config; getattr(...)"` 逐个取值 | PASS |
| CP-A1-2 全表值 + 严格类型 | 运行时逐项 `v==exp and type(v) is typ`；`1_048_576==1048576==1024*1024` | PASS |
| CP-A1-3 强约束 20<50 | 运行时 `MAX_DEV_LOOP_LLM_CALLS(20) < MAX_TOTAL_LLM_CALLS(50)` = True（AC-S3-04② 直接验收点） | PASS |
| CP-A1-4 既有常量零修改 | `git diff HEAD --stat config.py` = 26 insertions / 0 deletion；`grep '^-[^-]'` 删除行 = 0；运行时 `MAX_TOTAL_LLM_CALLS==50`/`MAX_FIX_LOOP_COUNT==3` 不变 | PASS（纯追加实证） |
| CP-A2-1 两字段注解 | 运行时 `__annotations__['_dev_loop_route']==Optional[str]`、`['_dev_loop_llm_calls'] is int` | PASS |
| CP-A2-2 默认值 | `create_initial_state` 返回 `_dev_loop_route is None`、`_dev_loop_llm_calls==0`（int 非 bool）；老形态 + 新形态 LLMConfigSet 入参均覆盖 | PASS |
| CP-A2-3 must-fix-1 grep 红线 | `grep -nE "Annotated\|operator\.add" core/state.py` exit=1 全文件**零命中**；`typing.get_origin` 三字段均 `is list` 双重证（AC-S3-05① 强制验收点） | PASS |
| CP-A2-4 FixLoopRecord 5 字段 | 运行时 `set(FixLoopRecord.__annotations__) == {round_number,error_summary,error_category,fix_strategy,timestamp}`；无 reviewer_verdict/coder_confidence/agent_trace | PASS |
| CP-A2-5 全量回归不退化 | `pytest -q -m "not e2e"` 586 passed / 0 failed，既有 559 零退化（×2 稳定） | PASS |

## 补强用例清单（`tests/test_sprint3_a_boundary.py`，新增 11 条）
1. `test_sandbox_timeouts_all_positive` — 5 个 sandbox 常量严格正数（int 非 bool）
2. `test_sandbox_timeout_magnitude_ordering` — 量纲 EXEC(1800) ≥ PIP_INSTALL(1200) ≥ VENV_CREATE(300)（按实际值判断，作误改颠倒量纲护栏）
3. `test_sandbox_output_max_bytes_is_one_mib` — `1_048_576 == 1048576 == 1024*1024`
4. `test_dev_loop_budget_magnitude_ordering` — `2 ≤ 20 < 50`（CP-A1-3 量纲延伸）
5. `test_react_max_rounds_coding_in_reasonable_range` — coding 轮数与 sp1/sp2 同量级
6. `test_streamlit_page_routes_no_key_collision` — **动态收集**全部 STREAMLIT_PAGE_* 常量去重 == 总数（5 个互异，无路由键冲突；比固定列举稳健）
7. `test_no_env_override_for_all_sp3_constants` — A4 范式全量版：10 个常量设同名 env 后 reload 全不变
8. `test_create_initial_state_legacy_defaults_intact` — 抽查 sp1/sp2 共 15 个既有默认值（retry_budget_remaining=50 / fix_loop_count=0 / _planning_* 等）+ sp3 新字段共存零破坏
9. `test_create_initial_state_custom_workspace_does_not_touch_new_fields` — 自定义 workspace 不干扰新字段
10. `test_node_errors_no_reducer_last_write_wins_via_minimal_graph` — **must-fix-1 反向行为证**：最小 LangGraph 图两节点对 node_errors 做 read-modify-write 返回整列表，断言最终恰 2 条无重复累加（若误加 operator.add 会膨胀），且 node_b 读到 node_a 写入值 → 运行时正向证据
11. `test_degraded_nodes_no_reducer_last_write_wins_via_minimal_graph` — degraded_nodes 同款反向行为证

> 补强重点超出 sp2 纯静态范式：第 10/11 条以真实 LangGraph 图调用提供 must-fix-1「无 reducer」的运行时行为证据，而非仅静态 grep —— 直接复现「若加 reducer 会破坏 sp1/sp2 全部既有节点 return 整列表写法」的失败路径作为反向断言。

## 稳定性结论
- 阶段 A 三文件连跑 3 次：均 27 passed，0 flaky。
- 全量非 e2e 回归连跑 2 次：586 passed / 25 skipped / 0 failed（83.49s / 82.65s）。
- 用例独立性：单独跑最易受执行顺序影响的两条（reducer 反向证图调用 + env reload，后者有 `importlib.reload(config)` 副作用）均独立 PASS；env reload 用例在 `finally` 中复原 env 并 reload，回归两次 586 一致证明无污染。

## 失败排查
无失败用例。

## 警告说明（记入报告，非本阶段引入）
- **`LangChainPendingDeprecationWarning`**（1 条）：来自 `langgraph/checkpoint/serde/encrypted.py:5`（全量回归，import 期触发）/ `langgraph/cache/base/__init__.py:8`（补强文件第 10 条图调用触发）。内容为 `JsonPlusSerializer` 的 `allowed_objects` 默认值未来将变更。
  - 判定：**第三方库（langgraph 1.1.10）自身的 pending deprecation**，与本阶段 config/state 改动无关，sp2 基线即存在（非新引入）。
  - 处置：项目级遗留 warning，记录待观察；建议在 langgraph 升级或后续阶段统一处理（如显式传 `allowed_objects`），非阶段 A 阻断项。
- 无 `PytestUnknownMarkWarning`（pytest.ini markers 声明完整）；无 `PytestCollectionWarning`。

## 文档↔代码偏差核对
- dev-plan L245-281 把 CP-A1/A2 的自测勾选指向具体 `test_*` 函数名，与实际文件函数一致，无偏差。
- 开发报告称「16/16 CP 全绿」——实测 `test_sprint3_a1.py`(9 函数) + `test_sprint3_a2.py`(7 函数) = 16 个测试函数，其中含若干 `aux` 辅助函数；CP 主体为 A1 的 4 个 + A2 的 5 个共 9 个 checkpoint，由这 16 函数覆盖。表述「16 用例」准确，「16 CP」为口径宽松（实际 9 CP / 16 函数），不影响验收结论。已在本报告"CP 逐条复核"按 9 个 CP 主体逐条独立证。

## 后续动作
- 无遗留 BUG，无文档↔代码硬偏差，阶段 A 解除对阶段 B（B 仅依赖 config 常量）/ 阶段 C（C 依赖 A2 的 `_dev_loop_*` 字段）的阻塞。
- 下一次跑测试触发条件：阶段 B（sandbox local_venv + 4 护栏 + code_fs_tools）交付后验收；阶段 C3 修复循环落地后需重点回归 `_dev_loop_llm_calls` read-modify-write 累加 + must-fix-2 预算回写。
- 项目级 warning（LangChainPendingDeprecationWarning）持续观察，建议 langgraph 相关任务统一消除。
- e2e 待凭证补跑清单延续 sp2 残留（不属阶段 A 范围）。

## 最终裁决
**Sprint 3 阶段 A（配置与状态层）独立验收 PASS。** CP-A1-1~4 / CP-A2-1~5 全部独立复核通过（不依赖开发断言），两个 must-fix-1 强制验收点（grep 零命中 + get_origin 双重证 + 运行时反向行为证）守住，强约束 20<50 成立，纯追加 0 删改实证。补强 11 条边界用例全绿，全量回归 586 passed / 0 failed 连跑 2 次稳定零 flaky。**零生产 BUG，零文档硬偏差。**
