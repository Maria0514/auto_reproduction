# 测试执行报告 - G1 mock 单测全套 + AC-S4 覆盖矩阵审计

- **日期**：2026-07-05（本地时区）
- **执行人**：@测试工程师代理
- **Sprint**：sprint4
- **触发原因**：dev-plan §4 任务 G1（A~F 全部完成后的 Sprint 级聚合验收）——CP-G1-1（AC 矩阵参数化审计）/ CP-G1-2（AC-S4-03 结构测试 + graph.py 零改动 git 实证）/ CP-G1-3（全量回归 3 连跑 + 对账）
- **commit**：5401a90（工作区新增 `tests/test_sprint4_g1.py` 与本报告，未 commit，主控收口）

## 验收结论

**CP-G1-1 / CP-G1-2 / CP-G1-3 全部 PASS。**

- G1 授权的 10 条 AC（AC-S4-01~07/09/12/14）在 mock 层逐条有 CP 用例映射，矩阵已固化为可执行断言（`tests/test_sprint4_g1.py::AC_COVERAGE_MAP`，41 个映射 node id）；targeted 运行 51 条（含参数化展开）全绿。
- 元断言审计**强化**了 sp3 CP-F1-4 范式（三重防假绿：存在性/可收集 + skip/e2e mark 审计 + AST 断言实质性审计），39 个既有映射函数经 AST 审计全部含 ≥1 真实断言构造（最少 1、最多 13），**零空泛用例**。
- AC-S4-03 结构测试 5 条 PASS（编译成功 / 7 节点逐字 / 禁止与 sp4 符号零泄漏 / self-loop 边路由+编译图双证 / 边结构逐字 sp3）；git 双证 `core/graph.py` Sprint 4 期间零改动。
- 全量非 e2e 回归 3 连跑恒 **1377 passed / 0 failed**，= 基线 1349 + G1 新增 28，精确对账，0 flaky。
- **审计发现：G1 授权范围内零覆盖空洞、零假绿**；1 条历史报告计数口径的事实修正（非阻断，见"审计发现"）。

## 执行范围

- 命令：
  - `.venv/bin/pytest tests/test_sprint4_g1.py -q`（新增 28 条）
  - `.venv/bin/pytest <41 个矩阵映射 node id> -q`（targeted 全绿运行，CP-G1-1 后半）
  - `.venv/bin/pytest tests/ -q` × 3（全量非 e2e 回归；pytest.ini `addopts = -m "not e2e"` 默认排除 43 条 e2e，本次全程未显式传 `-m e2e` / `-m sandbox_real`，零配额消耗）
  - `.venv/bin/pytest --collect-only -q tests/test_sprint4_*.py` + 逐文件（对账）
  - `git log --follow -- core/graph.py` / `git log --since=2026-07-01 -- core/graph.py` / `git diff HEAD --stat -- core/graph.py`（CP-G1-2 git 实证）
  - 一次性映射探针 `/tmp/g1_probe.py`（写正式文件前对 39 个既有映射函数做存在性 + mark + AST 预验，已删除）
- 是否包含 e2e：**否**（未授权，不耗配额）。凭证 env 仅提及未使用：LLM_API_KEY / DEEPXIV_TOKEN。

## CP-G1-1：AC 覆盖矩阵（§5 矩阵 → 可执行断言）

矩阵全文固化于 `tests/test_sprint4_g1.py::AC_COVERAGE_MAP`，摘要：

| AC | 映射用例（模块::函数，核心判定项） | 来源验收报告 |
|---|---|---|
| AC-S4-01 | c1::cp_c1_1_normal_smoke / cp_c1_2_out_of_workspace；c2::cp_c2_1_seven_tools / cp_c2_1_wrapper_form | 07-04 cef |
| AC-S4-02 | a1::cp_a1_2_timeout_below_sandbox；c1::cp_c1_3_timeout_kills_subprocess_tree | 07-02 a1 / 07-04 cef |
| AC-S4-03 | g1::cp_g1_2 ×3（本任务新增）+ sp3 d1::cp_d1_1 ×2 / cp_d1_4_self_loop（AC-S3-10 基线沿用） | 本报告 |
| AC-S4-04 | e3::cp_e3_1_deduction_rounds_plus_metric / guard_reentry_zero / dev_loop_ceiling | 07-04 e3-e4 |
| AC-S4-05 | sp3 c3::cp_c3_7/13/4/5；d1_reinforce::h1/h2；e2_reinforce::g1_all_three_payloads；e4::cp_e4_2 主证 | 07-04 e3-e4 |
| AC-S4-06 | b1::cp_b1_1/cp_b1_2_remember；b2::cp_b2_1；c2::cp_c2_3_resume_value（coding 侧）；e2::cp_e2_5（execution 侧） | 07-03 b1 / 07-04 b2 / 07-04 cef |
| AC-S4-07 | e3::cp_e3_2 ×4（8 关键字 / not auto_fixable+permanent / 优先序 / 不耗 fix_loop_count） | 07-04 e3-e4 |
| AC-S4-09 | a3::cp_a3_1_roundtrip / cp_a3_1_0600 / cp_a3_2_gitignore；b1::cp_b1_3_cache_hit_no_interrupt | 07-02 a3 / 07-03 b1 |
| AC-S4-12 | a3::cp_a3_3_masks_remembered / masks_process_registered / cp_a3_5_logs_never_contain | 07-02 a3 |
| AC-S4-14 | b2::cp_b2_2_gate（首验）→ c2::cp_c2_3_write_exactly_once（coding）→ e4::cp_e4_2（execution），三级递进各取主证 | 07-04 b2 / cef / e3-e4 |

**排除键的理由（元断言锁定，矩阵键恰 10 条不多不少）**：AC-S4-08（真凭证注入）/ 11（脱敏 grep 全链路）/ 13（三 interrupt 串行）属 G2 e2e 授权补跑项，AC-S4-10 属 F1 手动走查（CP-F1-3 遗留）——纳入矩阵会把"待授权项"伪装成已覆盖。其 mock 层地基旁证均已全绿：08 → CP-D1-2/CP-E1-4/CP-C2-4；10 → CP-F1-1/2；11 → CP-A3-3/CP-C1-4/CP-E1-3/CP-E3-4/CP-D2-2；13 → 三类 kind 分发 CP-F1-1 + app 路由用例。

**防假绿三重审计（vs sp3 CP-F1-4 的强化）**：
1. 存在性 + 可收集（callable + `test_` 前缀 + 模块可 import）；
2. mark 审计——映射用例函数级/模块级 pytestmark 不得含 skip / e2e / sandbox_real（e2e 被 addopts 默认排除，映射到 e2e 用例即假绿）；
3. AST 断言实质性审计——`ast.Assert` / `pytest.raises` / mock `assert_*` / `_assert` helper 计数 ≥1（实测 39 函数分布 1~13，中位 4）。

**判定：PASS**（参数化审计 20 条 + 元断言 2 条全绿；targeted 51 条全绿）。

## CP-G1-2：AC-S4-03 结构测试 + graph.py 零改动 git 实证

结构测试 5 条（`test_cp_g1_2_*`）全 PASS：
- `build_graph()` 编译成功返回 CompiledStateGraph；
- 业务节点集合逐字 == {paper_intake, paper_analysis, resource_scout, planning, coding, execution, reporting}，恰 7；
- 禁止节点（sp3 三禁）∪ sp4 新符号（request_user_input / run_command / prepare_environment / run_in_sandbox / ReAct 子图内部节点 / await_dev_loop_interrupt 路由值）零泄漏进主图；
- `await_dev_loop_interrupt` self-loop 双证：路由函数返回 "execution" + 编译图 execution→execution 自环边真实存在；
- 边结构逐字 sp3：coding 后继恰 {execution, reporting}、execution 后继 ⊇ {execution, coding, planning, reporting} + END、reporting 仅到 END、planning 3 路。

**git 实证（graph.py 零改动）**：
- `git log --follow -- core/graph.py` 最近一次触碰 = **8b62230（2026-06-28，"feat(s3): 阶段 D 主图路由编排..."）**，属 Sprint 3；
- `git log --since=2026-07-01 -- core/graph.py` = **0 条 commit**（Sprint 4 起始 2026-07-02 之后无任何触碰）；
- `git diff HEAD --stat -- core/graph.py` = 空（工作区亦无未提交改动）。
- 注：dev-plan §7.3/§9 勘误已豁免 `core/react_base.py`（BUG-S4-B1-01 修复 ae09733），该文件不在本 CP 断言范围；graph.py 承诺不受影响。

**判定：PASS。**

## CP-G1-3：全量回归 + 连跑稳定性 + 对账

- 全量非 e2e 回归 3 连跑：**1377 / 1377 / 1377 passed，0 failed**（58.58s / 58.58s / 58.42s），skipped 恒 37、deselected 恒 43（e2e+sandbox_real），**0 退化 0 flaky**；
- 警告恒 1：langgraph 库级 `LangChainPendingDeprecationWarning`（sp1 起既有，挂账观察，非项目代码）。

**逐文件 collect 对账**（`pytest --collect-only -q`）：

| 文件 | 收集数 | 归属 |
|---|---|---|
| test_sprint4_a1 / a2 / d1 | 5 + 7 + 9 = 21 | A1/A2/D1（07-02 报告 +21 ✓） |
| test_sprint4_a3 / d2（+ isolation 增量 4） | 34 + 19 + 4 = 57 | A3/D2（07-02 报告 +57 ✓） |
| test_sprint4_b1 | 28 | B1 27（07-03 报告）+ L-B1-02 修复 1 ✓ |
| test_sprint4_b1_fix | 5 | BUG-S4-B1-01 修复 ✓ |
| test_sprint4_b2_interrupt3_idempotency | 9 | B2 ✓ |
| test_sprint4_c1 / c2 / e1 / e2 / f1 | 12 + 10 + 24 + 12 + 15 = 73 | 三线并行（07-04 cef 报告 +73 ✓） |
| test_sprint4_e3 / e4_regression_gate | 26 + 5 | E3 26 + E4 3 + L-E4-01 轮 e4 增 2 ✓ |
| test_sprint4_le401_fix | 6 | L-E4-01 闭环 ✓（与 e4 的 +2 合计该轮 +8） |
| **test_sprint4_g1（本任务新增）** | **28** | CP-G1-1 参数化 20 + 元断言 2 + 结构 5 + 可收集守门 1 |
| **合计** | **254**（sprint4 文件）+ 4（isolation 增量） = **258** | = 1377 − 1119（sp3 收口基线）**精确吻合** |

账链闭合：1119 → 1140 → 1197 → 1224 → 1229 → 1238 → 1311 → 1341 → 1349 → **1377**，每一跳均有报告可查。

**判定：PASS。**

## 审计发现与开方

**覆盖空洞 / 假绿：零发现**（G1 授权的 10 条 AC 范围内）。39 个既有映射函数全部真实存在、默认可运行、断言非空泛；targeted 与全量双通道全绿。

**事实修正（信息项，非阻断）**：`2026-07-05_le401-regression-backfill.md` 记 "新增测试 tests/test_sprint4_le401_fix.py（边界 8 条，含参数化）"——实际拆分为 le401_fix.py 收集 6 条 + test_sprint4_e4_regression_gate.py 同轮新增 2 条（`test_le401_fix_credential_inline_retry_success_single_round` / `test_le401_fix_inline_retry_without_interrupt_success`），合计 +8 与账链一致，仅归属文件表述有偏差。无需改动任何文件，此处留档即可。

**需主控裁决项：无**（本次审计未发现需要改动既有测试文件或生产代码的问题）。

**遗留项转记（均为既有挂账，非 G1 新增，供 G2/G3 消费）**：
- CP-F1-3（AC-S4-10）`streamlit run` 手动 happy path 走查——待与 Maria 协作（cef 报告遗留）；
- AC-S4-08/11/13 的 e2e 转正——G2 范围，真跑须 Maria 明确授权；
- langgraph 库级 PendingDeprecationWarning + FakeLLM msgpack 反序列化提示——G 阶段升级检查单（B2 报告遗留）；
- `tests/test_sprint4_e1.py::_ws_dir` 在真实 workspace 下留测试目录（cef 报告非阻断项 1）——本次复核 workspace 无 `.secrets` / `.git_askpass_*` 凭证残留（find 零命中），目录残留仍在，G 阶段可统一考虑清理 fixture。

## 失败排查

无失败（新增 28 条一次全绿；targeted 51 条一次全绿；全量 3 连跑零失败）。

## 后续动作

- dev-plan CP-G1-1/2/3 勾选与 TODO.md 收口由主控统一执行（本报告可作勾选依据）；
- G2（e2e 骨架 + 授权补跑清单）以本矩阵为基线，AC-S4-08/11/13 转正后可在 `AC_COVERAGE_MAP` 语义上补充 e2e 层映射（另立 e2e 矩阵，勿并入本 mock 矩阵——元断言锁 10 键）；
- G3 handoff 引用本报告的矩阵摘要与账链数字。
