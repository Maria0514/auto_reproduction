# Sprint 3 阶段 F 交付：移交测试工程师

- 文档版本：v1.0
- 落盘日期：2026-06-29
- 作者：全栈开发代理
- 适用 Sprint：Sprint 3（coding / execution（↔coding 修复循环）/ reporting + sandbox + 人在回路 interrupt#2）
- 对接对象：测试工程师代理（@test-engineer）
- 任务来源：`docs/sprint3/dev-plan.md` 任务 F3 / CP-F3-3

本文档汇总 Sprint 3 全部代码交付物的核对结果、五条核心 e2e 入口、mock 用例运行方式、AC-S3-01~10 覆盖矩阵、已知限制，作为测试工程师执行 Sprint 3 最终验收的上下文基线。范式照搬 `docs/sprint1/handoff-to-test-engineer.md`（sp1 F2 产出）。

---

## 1. 五条核心 e2e 入口（真实链路，凭证就绪后由主控补跑）

真实链路 e2e 全部在 `tests/test_sprint3_e2e.py::TestRealChainE2E`（类级 `@pytest.mark.e2e` + `skip_if_no_creds` 凭证 skipif）。五条对应 dev-plan §667-672 五场景，**真实 LLM + 真实 deepxiv + mock sandbox**（§667 权威约定：不真跑 30min venv 训练，只模拟 sandbox 三入口 exit code + stdout `<METRICS>`）。

| # | 精确节点 id | 场景 | AC | 备注 |
|---|------------|------|----|------|
| real-1 | `tests/test_sprint3_e2e.py::TestRealChainE2E::test_real_1_happy_path_b_grade_success` | happy path B 档成功（FULL 模式跑通 → success=True + 报告渲染） | AC-S3-01 | **smoke 首选**（最省 deepxiv 配额、一条真实链路 fail-fast 验凭证 + deepxiv 可达 + 全装配） |
| real-2 | `tests/test_sprint3_e2e.py::TestRealChainE2E::test_real_2_fix_loop_upper_limit_three` | 修复循环上限 3 拦截 → interrupt#2 | AC-S3-03 | mock 连续可修复失败 |
| real-3 | `tests/test_sprint3_e2e.py::TestRealChainE2E::test_real_3_interrupt2_three_state_resume[terminate-end]` / `[revise_plan-planning]` / `[export_code-reporting]` | interrupt#2 三选一（`Command(resume=...)` 三态路由） | AC-S3-07 | **参数化展开 3 个 item** |
| real-4 | `tests/test_sprint3_e2e.py::TestRealChainE2E::test_real_4_code_only_skips_execution` | code_only 跳过 execution → reporting code_only 形态 | AC-S3-06 | planning 选 code_only |
| real-5 | `tests/test_sprint3_e2e.py::TestRealChainE2E::test_real_5_degraded_budget_exhausted` | 预算耗尽 → degraded 报告 | AC-S3-09 ③ | 降级仍交付 |

**收集校验（5 条参数化共 7 item）**：

```bash
.venv/bin/pytest tests/test_sprint3_e2e.py -m e2e --co -q
```

**真实补跑（凭证就绪、Maria 授权后由主控统一执行）**：

```bash
# 全部真实 e2e（约 29 分钟，见 F2 真跑报告）
.venv/bin/pytest tests/test_sprint3_e2e.py -m e2e -v -s
# smoke 首选（先单跑 real-1 fail-fast 验凭证 + deepxiv 可达，最省配额）
.venv/bin/pytest "tests/test_sprint3_e2e.py::TestRealChainE2E::test_real_1_happy_path_b_grade_success" -m e2e -v -s
```

**F2 真跑现状（2026-06-29，主控已补跑转正）**：7/7 真实 e2e 全绿（首轮全套 6/7，real-1 仅 `run==1` 断言过严已修[真实 LLM 规划 11 步 execution_steps]→重跑 PASSED）；详见 `test-reports/2026-06-29_f2-real-e2e-run.md` + `test-reports/2026-06-29_f2-real-e2e-wiring.md`。dev-plan §674 稳定性复跑（real-2/3）经 Maria 决策省配额记为可选待补。

---

## 2. mock 用例运行方式（不依赖凭证，默认回归主路径）

> 所有 mock 测试均不依赖外部 API / 凭证，可在无 token 环境下运行；适合 CI / 开发自测快速门槛。

### 2.1 一键全量非 e2e 回归（推荐验收主路径）

```bash
.venv/bin/pytest -q -m "not e2e" --ignore=tests/test_paper_intake.py
```

- **基线：1083 passed / 25 skipped / 35 deselected**（F3 阶段，约 121s）。F3 新增 `tests/test_sprint3_f3.py` 4 条均无 `@pytest.mark.e2e`，进默认回归后基线升至 **1087 passed**（见 §5）。
- `--ignore=tests/test_paper_intake.py`：沿用 sp1/sp2 既有约定（该文件 8 个 mock 检查点以参数化集合实现，与全量回归口径分离，详见 sp1 handoff §3 注）。
- `25 skipped`：e2e 类在无凭证时整体 skip；`35 deselected`：`-m "not e2e"` 排除的真实 e2e item（含 sp3 7 个 + sp1/sp2 既有）。

### 2.2 仅跑 sp3 全套 mock

```bash
.venv/bin/pytest tests/test_sprint3_*.py -q -m "not e2e"
```

- 覆盖 sp3 阶段 A~F 全部 mock 单测（含 test-engineer 补强的 `*_reinforce` / `*_strengthen` / `*_boundary` / `c1_fix*`）。
- `tests/test_sprint3_e2e.py` 的 8 条 **mock e2e**（`test_f2_e2e_1~5`，含 5a/5b）不标 `@pytest.mark.e2e`，会被这条命令跑到（`-m "not e2e"` 收 8 deselect 7）。

### 2.3 单任务 mock 入口

| 任务 | 测试文件 | 覆盖模块 |
|------|---------|---------|
| A1 配置常量 | `test_sprint3_a1.py` | `config.py`（预算常量 + REACT_MAX_ROUNDS_CODING 等） |
| A2 state 微增字段 | `test_sprint3_a2.py` / `test_sprint3_a_boundary.py` | `core/state.py`（三 List 字段无 reducer grep 断言 = must-fix-1） |
| B1 sandbox 四护栏 | `test_sprint3_b1.py` | `sandbox/local_venv.py` |
| B2 代码文件读写工具 | `test_sprint3_b2.py` / `test_sprint3_b2_strengthen.py` | `core/tools/code_fs_tools.py` |
| C1 coding 节点 | `test_sprint3_c1.py` / `c1_fix.py` / `c1_fix_reinforce.py` | `core/nodes/coding.py` |
| C2 reporting 节点 | `test_sprint3_c2.py` | `core/nodes/reporting.py` |
| C3 execution 节点 | `test_sprint3_c3.py` / `c3_reinforce.py` | `core/nodes/execution.py` |
| D1 图编排 + 条件路由 | `test_sprint3_d1.py` / `d1_reinforce.py` | `core/graph.py` / `core/nodes/planning.py` |
| E1~E3 UI | `test_sprint3_e1*.py` / `e2*.py` / `e3*.py` | `app.py` / `ui/pages/execution_monitor.py` / `ui/pages/result_report.py` |
| F1 AC 级聚合验收 | `test_sprint3_f1.py` | AC 覆盖审计 + must-fix-1/2 Sprint 级聚合断言 |
| **F2 e2e 装配 + mock e2e** | `test_sprint3_e2e.py` | 8 mock e2e + 5 真实链路 e2e（凭证 skip） |
| **F3 Prompt Cache 守门** | `test_sprint3_f3.py` | coding system prompt 主体字节级一致（CP-F3-1） |

---

## 3. AC-S3-01~10 覆盖矩阵（复用 F1 / dev-plan §5）

| 验收标准 | 对应任务 | 关键检查点 | 测试类型 | 覆盖状态 |
|---|---|---|---|---|
| **AC-S3-01** 端到端 happy path B 档成功 | C1+C2+C3+D1+E3 / F2 | CP-C3-2 / CP-C2-2 / CP-D1-7 / CP-F2-2 | mock 单测 + 真实链路 e2e | mock 旁证全覆盖（c3::test_cp_c3_2 / c2::test_cp_c2_2 / d1::test_cp_d1_7）；**B 档真实成功 e2e 转正 PASS（real-1 PASSED 2026-06-29，7/7 真实 e2e 全绿）** |
| **AC-S3-02** sandbox 4 护栏生效 | B1 / F1 | CP-B1-2~5 | mock 单测 | 全覆盖（b1::test_cp_b1_2/3/4/5；f1::test_cp_f1_4[AC-S3-02] 审计） |
| **AC-S3-03** 修复循环计数 + 上限 3 拦截 | C3+D1 / F2 | CP-C3-4/5 / CP-F2-1 | mock 单测 + e2e | mock 全覆盖（c3::test_cp_c3_4/5；d1_reinforce::test_h3 真实图回边）；real-2 真实 e2e PASS |
| **AC-S3-04** 预算回写 + 子预算 20 + 入口预算门 | A1+C3 / F1 | CP-A1-3 / CP-C3-8/9/10 | mock 单测 | 全覆盖（a1::test_cp_a1_3 / c3::test_cp_c3_8/9/10；f1::test_cp_f1_3_* 4 条 Sprint 级专项再断言） |
| **AC-S3-05** list 无 reducer 单点合并无丢失（must-fix-1） | A2+C1+C3 / F1 | CP-A2-3 / CP-C1-4 / CP-C3-11 | grep 断言 + mock 单测 | 全覆盖（a2::test_cp_a2_3 grep / c1::test_cp_c1_4 / c3::test_cp_c3_11；f1::test_cp_f1_2_* 3 条 Sprint 级聚合） |
| **AC-S3-06** code_only 跳过 execution + 修复循环 | D1+C1+C2 / F2 | CP-D1-3 / CP-C2-3 | mock 单测 + e2e | mock 全覆盖（d1::test_cp_d1_3 / c2::test_cp_c2_3 / d1::test_cp_d1_7_code_only）；real-4 真实 e2e PASS |
| **AC-S3-07** dev_loop 失败 interrupt 三选一 | C3+E2 / S-1 / F2 | CP-S-3/4 / CP-C3-7 / CP-E2-3 | spike + mock(Command resume) + e2e | mock(Command resume) 全覆盖（c3::test_cp_c3_7 参数化三态 / d1::test_cp_d1_4 / d1_reinforce::test_h2_* 真实图 resume）；real-3 真实 e2e 三态 PASS |
| **AC-S3-08** 不可修复类不进重试 | C3 / F1 | CP-C3-3/6 | mock 单测 | 全覆盖（c3::test_cp_c3_3 两类分流 / test_cp_c3_6 不可修复无重试；f1::test_f1_classify_two_class_split_direct） |
| **AC-S3-09** reporting 三形态 | C2+E3 / F1 | CP-C2-2/3/4 / CP-E3-2 | mock 单测 + 手动 UI | mock 全覆盖（c2::test_cp_c2_2/3/4 三形态）；降级 e2e real-5 PASS；UI 手动走查留测试工程师 |
| **AC-S3-10** 主图 7 节点骨架不变性 | D1 / F1 | CP-D1-1/2/4 | mock 单测 + manual 路由复核 | 全覆盖（d1::test_cp_d1_1_compiles / _exactly_seven_nodes / _no_forbidden_subgraph_nodes）；manual 路由复核留测试工程师 |

**汇总**：10/10 AC 全部有 mock 自动化覆盖；AC-S3-01/03/06/07/09 的真实链路 e2e 由 F2 主控补跑转正（7/7 全绿）。

---

## 4. 已知限制与遗留问题

| 编号 | 限制 | 影响范围 | 后续处理 / 验收关注点 |
|------|------|---------|---------|
| L-S3-01 | **interrupt#2 重跑幂等依赖 S-1 spike 结论**：execution 函数体内 resume 重跑非幂等风险（R-S3-06 最高风险）由 S-1 spike 前置验证幂等保护方案，C3 复用本回合 `execution_result`（CP-C3-13）。验收 real-2/3 时需关注重跑幂等行为。 | `core/nodes/execution.py` + `core/graph.py` | S-1 spike 报告 `test-reports/2026-06-16_spike-s1-execution-interrupt-idempotency.md`；§674 稳定性复跑（real-2/3）经 Maria 决策省配额记为可选待补，验收时若复跑可补样本 |
| L-S3-02 | **错误分类准确率边界**：execution 错误分类（可修复 RUNTIME vs 不可修复 PERMANENT）基于规则 + 兜底归 RUNTIME 给一次机会（R-S3-04）；LLM / 异构 stderr 下分类准确率非 100%。 | `core/nodes/execution.py` 分类逻辑 | 上限 3 拦截 + CP-C3-3 两类分流验收兜底；验收时关注边界 stderr 的分类落点 |
| L-S3-03 | **B 档解析依赖 `<METRICS>` 约定**：B 档 metrics 解析依赖入口脚本在 stdout 末尾打印 `<METRICS>{...}</METRICS>`（C1 prompt 强约束前移）；三档解析为正则 → LLM 抽取兜底 → 降级。若 LLM 生成的入口脚本漏打该行，走 LLM 抽取兜底或降级。 | `core/nodes/coding.py`（prompt 约定）+ `core/nodes/execution.py`（三档解析） | R-S3-05 缓解；验收 real-1 happy path 时关注 `<METRICS>` 命中走正则解析路径 |
| L-S3-04 | **LLM read_section 章节名命中率**（F2 真跑观察）：coding ReAct 内 agent 自主调 `read_section(arxiv_id, section_name)` 时，LLM 常猜错章节名（如猜 `method` / `experiments` / `abstract`，而 HippoRAG 真实章节是 `HippoRAG` / `Experimental Setup` / `Introduction`），触发 deepxiv「Section not found」降级（返回可用章节列表）。不影响最终产出（agent 会重试或换章节），但**真跑日志会有大量 `Section not found` WARNING**，属预期，非 bug。 | `core/tools/deepxiv_tools.py::read_section` + coding ReAct | 验收真实 e2e 时见此 WARNING 不必惊慌；后续 Sprint 可考虑把 `get_paper_structure` 章节清单前置喂给 agent 提升命中率 |
| L-S3-05 | **§674 稳定性复跑待补**：interrupt#2 / 修复循环属 LLM 服从度 + 重跑幂等类风险，dev-plan §674 要求按复现率连跑 3~5 次全绿。F2 真跑各场景本次各 1 次通过，real-2/3 多次复跑经 Maria 决策省配额记为可选待补。 | 真实 e2e real-2/3 | 验收若有配额预算，可补连跑样本固化复现率结论 |
| L-S3-06 | **真实 e2e 用 mock sandbox 不真跑训练**（§667 权威约定）：真实链路 e2e 真实驱动 LLM 全流程，但 sandbox 三入口（prepare_venv / run_in_venv / collect_artifacts）被 mock，注入受控 exit code + stdout `<METRICS>`，**不真跑 30min venv 训练 / 不依赖 GPU**。sandbox 护栏本身由 B1 mock 单测（CP-B1-2~5）独立验收。 | `tests/test_sprint3_e2e.py::TestRealChainE2E` | 验收时理解真实 e2e 验的是「LLM 全流程装配 + 路由 + 三形态报告」，不是「真实训练复现成功」 |
| L-S3-07 | **CP-F3-2 Prompt Cache 命中率守门待主控授权补跑**：coding 节点新注入 prompt 段的 cache 命中率回归（守门 = R_after ≥ sp2 S-3 基线 0.7669 × 0.95 = 0.7286）依赖 LLM 凭证 + deepxiv 配额，**未真跑**。验证脚本已就绪（见 §6），待 Maria 授权由主控补跑。 | `scripts/spike_coding_prompt_cache.py` | 见 §6；CP-F3-1 字节级一致（不依赖凭证）已 PASS，是 cache 命中率的前置必要条件 |

---

## 5. 交付物清单与 dev-plan §8 一致性核对

逐条核对 `docs/sprint3/dev-plan.md` §8 交付物清单（2026-06-29 文件存在性 + 关键导出）：

| 类型 | 文件 | 任务 | 存在 | 备注 |
|---|---|---|---|---|
| 新增 | `sandbox/__init__.py` + `sandbox/local_venv.py` | B1 | [x] | prepare_venv / run_in_venv / collect_artifacts + 四护栏 |
| 新增 | `core/tools/code_fs_tools.py` | B2 | [x] | write/read/list 工具；ToolMessage 合法 JSON（BUG-S1-02 治理） |
| 新增 | `core/nodes/coding.py` | C1 | [x] | coding ReAct wrapper + `_CODING_SYSTEM_PROMPT_BODY` 主体常量 + 尾部常量段（方案 A）+ `_map_coding_result` 3 参签名 + `_has_written_any_file` 工具历史回填 |
| 新增 | `core/nodes/execution.py` | C3 | [x] | execution 复合节点 + 错误分类 + B 档三档解析 + 修复循环边界 + interrupt#2 |
| 新增 | `core/nodes/reporting.py` | C2 | [x] | 三形态（full_success / code_only / degraded） |
| 新增 | `ui/pages/execution_monitor.py` | E2 | [x] | interrupt#2 三选一 UI |
| 新增 | `ui/pages/result_report.py` | E3 | [x] | 报告渲染页 |
| 改动 | `config.py`（纯追加常量） | A1 | [x] | MAX_FIX_LOOP_COUNT / 子预算 / REACT_MAX_ROUNDS_CODING 等 |
| 改动 | `core/state.py`（微增下划线字段，不碰 List/reducer） | A2 | [x] | must-fix-1：三 List 字段无 reducer |
| 改动 | `core/graph.py`（三节点换真实现 + 2 条件路由） | D1 | [x] | 7 节点骨架不变 + code_only / 修复循环路由 |
| 改动 | `core/nodes/planning.py`（interrupt payload 加 `interrupt_kind`） | D1 | [x] | interrupt#1 `interrupt_kind=planning` |
| 改动 | `app.py`（`interrupt_kind` helper + 路由分两页） | E1 | [x] | — |
| 新增 | `scripts/spike_execution_interrupt_idempotency.py` | S-1 | [x] | 幂等保护 spike |
| 新增 | `scripts/spike_coding_prompt_cache.py` | **F3** | [x] | **CP-F3-2 cache 命中率验证脚本（待主控授权补跑，见 §6）** |
| 新增 | `tests/test_sprint3_*.py` + `tests/test_sprint3_e2e.py` | F1/F2 | [x] | 23 个 sp3 测试文件（含 `test_sprint3_f3.py`） |
| 新增 | `tests/test_sprint3_f3.py` | **F3** | [x] | **CP-F3-1 coding system prompt 主体字节级一致（4 条 mock 断言）** |
| 新增 | `docs/sprint3/test-reports/*`（spike + e2e + acceptance） | S-1/F2/F3 | [x] | 15 份报告已归档 |
| 新增 | `docs/sprint3/handoff-to-test-engineer.md` | **F3** | [x] | 本文档 |
| 零改动 | `core/react_base.py` / `core/checkpointer.py` / `core/errors.py` / `core/tools/git_tools.py` / sp1/sp2 既有节点 | — | [x] | 未触碰 |

**核对结论**：dev-plan §8 全部交付物文件就位，F3 新增 `tests/test_sprint3_f3.py` + `scripts/spike_coding_prompt_cache.py` + 本 handoff 文档，零缺失。

### 5.1 当前 pytest 状态快照（F3 落盘前基线）

```bash
.venv/bin/pytest -q -m "not e2e" --ignore=tests/test_paper_intake.py
```

- **F3 前基线**：1083 passed / 25 skipped / 35 deselected / 121.02s（2026-06-29）
- **F3 后**：+4（`test_sprint3_f3.py` CP-F3-1）→ **1087 passed**，零退化（见本 Sprint dev-plan F3 自测检查点实测数）

---

## 6. CP-F3-2 Prompt Cache 命中率守门（待主控授权补跑）

⚠️ **CP-F3-2 未真跑**——依赖 LLM 凭证 + 消耗 deepxiv 配额，需 Maria 明确授权、由主控补跑（沿用 sp2/sp3 e2e 省配额范式）。本任务只准备好验证脚本，不勾选 CP-F3-2。

### 6.1 验证脚本

- 脚本：`scripts/spike_coding_prompt_cache.py`（沿用 `scripts/spike_prompt_cache_baseline.py` sp2 S-3 范式）。
- 目标节点：**coding**（sp3 C1 新注入 system prompt 段）。
- 守门判据：`R_after ≥ R_baseline_sp2 × 0.95`，其中 **sp2 S-3 基线 `R_baseline_sp2 = 0.7669`**，门限 = **0.7286**。
- 口径：固定 arxiv_id=2405.14831（HippoRAG），用预置 mock 上游 state（不跑 intake/analysis/scout/planning 省 deepxiv），连跑 coding × 3，monkey-patch `ChatOpenAI.invoke` 采集 cached/prompt tokens，`R_after = mean(R_2, R_3)`（去 cold 首轮，与 sp2 同口径）。
- **双保险硬闸门**：脚本会 `load_dotenv` 自动加载 `.env` 凭证，故清除环境变量也拦不住真跑；额外要求显式设 `SPIKE_F3_AUTHORIZED=1` 才发起真实调用，否则立即 skip（exit 2），不耗配额。

### 6.2 授权补跑命令

```bash
SPIKE_F3_AUTHORIZED=1 .venv/bin/python scripts/spike_coding_prompt_cache.py
```

- 输出：终端打印 3 次 cached/prompt/命中率 + R_after + 是否过守门；原始指标 JSON 落盘 `workspace/runs/spike-f3-coding-prompt-cache_<ts>.json`。
- exit 0 = 过守门；exit 1 = 未过守门；exit 2 = 未授权 / 缺凭证（skip）。

### 6.3 旁证（非正式守门数据）

CP-F3-1 字节级一致（不依赖凭证）已 PASS——这是 cache 命中的**前置必要条件**（system prompt 字节稳定 → cache 前缀可命中）。coding 节点 system prompt 在两篇论文间甚至**完全字节一致**（动态全走 HumanMessage，尾部段也是常量 `{"node": "coding"}`），稳定性强于 paper_analysis（后者尾部有论文级 `sort_keys` 渲染段）。正式守门 R_after 数据仍以 §6.2 授权补跑结果为准。

---

## 附录：与测试工程师的协作建议

1. 验收前先跑一次 `.venv/bin/pytest -q -m "not e2e" --ignore=tests/test_paper_intake.py` 复测基线；如本机与 §5.1 数字不一致，对照环境（venv、依赖、`.env` 凭证未注入也不影响 mock）。
2. 真实链路 e2e 验收 / 复跑需 Maria 授权（消耗 deepxiv 日配额）；smoke 首选 real-1。
3. 验收报告归档约定见 `.claude/agents/test-engineer.md` 测试报告归档规范，落盘 `docs/sprint3/test-reports/YYYY-MM-DD_<scope>.md`。
4. 如验收发现新 bug，按 BUG-S1-02 / BUG-S1-03 在 `docs/TODO.md` 的归档格式登记（含复现率、根因、修复内容、回归样本数 / 耗时）。
5. CP-F3-2 cache 命中率守门若 Maria 授权补跑，请把 R_after 实测 + 是否过守门归档 `test-reports/`，并回填 dev-plan CP-F3-2 检查点。

---

**文档结束。**
