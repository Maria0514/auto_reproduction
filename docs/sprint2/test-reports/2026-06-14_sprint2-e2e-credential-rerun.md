# 测试执行报告 - Sprint 2 e2e 凭证补跑转正（credential-rerun）

- **日期**：2026-06-14 20:30（本地时区）
- **执行人**：@测试工程师代理
- **Sprint**：sprint2
- **触发原因**：将 2026-06-13 最终验收的「有条件 PASS」凭证依赖项转正。06-13 因 `LLM_API_KEY` / `DEEPXIV_TOKEN` 均 EMPTY，28 条 e2e marker 用例 + planning 维度 cache 回归一律 skip 不伪造（见 `2026-06-13_sprint2-final-acceptance.md` §6 待凭证补跑清单）。2026-06-14 凭证已就绪（项目根 `.env` 中 `LLM_API_KEY` / `DEEPXIV_TOKEN` 非空且真实有效），由主控完成 e2e 补跑，本报告基于已跑出的权威结果落盘。
- **commit**：c7a9c4b
- **结论**：**PASS（凭证依赖项已转正）**。真实链路 e2e 25/25 全绿，0 failed；3 条为永久设计性 skip（非凭证、有等价替代）。残留项仅 manual UI 贯通（自动化无法补）+ planning 维度 cache 字节级回归（仍未覆盖），**均不阻断 Sprint 2 转正**（裁决见 §6）。

---

## 0. 重要约束声明

本报告**不重跑任何 e2e 用例**。deepxiv 有日配额（Maria 之前踩过 `DeepxivDailyLimitError` 日配额耗尽，见 commit 8cbe351），重复全量跑会浪费配额。下述 e2e 逐条结果均来自主控本次会话已跑出的真实数据：
- smoke 命令输出（4/4 PASSED）；
- 全量命令 summary（21 passed, 3 skipped, 584 deselected）+ 完整逐条日志 `workspace/e2e_full_run.log`（已 Read 取明细核对）。

本报告唯一自行执行的 pytest 是**非 e2e mock 回归**（不烧 LLM/deepxiv 配额），用于确认本次会话两个 commit 无退化（见 §5）。

---

## 1. 执行范围

| 项 | 命令 | 执行者 |
|---|---|---|
| Smoke（fail-fast 验凭证） | `.venv/bin/python -m pytest tests/test_paper_intake_e2e.py -m e2e -x -v` | 主控（本报告引用） |
| 全量 e2e（排除已 smoke 的 paper_intake，省 deepxiv 配额） | `.venv/bin/python -m pytest -m e2e --ignore=tests/test_paper_intake_e2e.py -v` | 主控（本报告引用） |
| 非 e2e mock 回归（commit 退化核查） | `.venv/bin/python -m pytest -q -m "not e2e" --ignore=tests/test_paper_intake.py` | 本报告自跑 |

- **是否包含真实 e2e（LLM/deepxiv）**：是（由主控跑，本报告不重跑）。
- **凭证来源**：项目根 `.env`，env 变量 `LLM_API_KEY` + `DEEPXIV_TOKEN`（均非空真实有效；不在本报告写入凭证值）。
- **固定靶论文**：arXiv `2405.14831`（小论文，避免 token 爆）。

---

## 2. 结果摘要

| 指标 | 值 |
|---|---|
| Smoke（paper_intake e2e） | **4 passed / 0 failed / 0 skipped**（无 401/429，证明凭证真实有效） |
| 全量 e2e（除 paper_intake） | **21 passed / 3 skipped / 584 deselected / 1 warning**，exit code 0，0 failed，651.20s（0:10:51） |
| **真实链路 e2e 合计** | **25/25 全绿**（smoke 4 + 全量 21），0 failed |
| 永久设计性 skip | **3**（`test_graph_e2e.py`，非凭证原因，有等价替代，见 §3.3） |
| 非 e2e mock 回归（commit 退化核查） | **559 passed / 0 failed / 25 skipped / 28 deselected**，85.54s（与 06-13 基线逐项一致，零退化） |
| 警告 | **1**（langgraph `LangChainPendingDeprecationWarning`，库级预存，与 sp2 无关，见 §7） |

---

## 3. 真实链路 e2e 逐条结果（权威数据）

### 3.1 Smoke：paper_intake e2e（4/4 PASSED）

命令：`.venv/bin/python -m pytest tests/test_paper_intake_e2e.py -m e2e -x -v`

| 用例 | 结果 |
|---|---|
| `test_e2e_versioned_id_cleanup` | PASSED |
| `test_e2e_full_url_cleanup` | PASSED |
| `test_e2e_plain_id_cs_category` | PASSED |
| `test_e2e_node_errors_empty_on_success` | PASSED |

无 skip、无 401/429。作为 fail-fast 哨兵，证明凭证真实有效，授权后续全量跑。

### 3.2 全量 e2e（21/21 PASSED）

命令：`.venv/bin/python -m pytest -m e2e --ignore=tests/test_paper_intake_e2e.py -v`
完整逐条日志：`workspace/e2e_full_run.log`（已核对）。

| 文件 | 用例数 | 结果 | 覆盖维度 |
|---|---|---|---|
| `test_paper_analysis_e2e.py` | 6/6 | PASSED | sp1 节点真实 LLM 链路；含 `test_e2e_prompt_cache_system_prompt_byte_identical`（**analysis 维度** cache 字节级回归，验证 commit e97712e B1 语言策略段拼接修正在真实链路通过） |
| `test_sprint2_b2_e2e.py` | 7/7 | PASSED | `test_e2e_prompt_cache_system_body_byte_identical`（**resource_scout 维度** cache 回归）/ `git_clone_and_analyze` 真实小仓库 / `check_url_reachable` 真实死活链接 / `search_pwc` 匿名 JSON 契约 / resource_scout 真实契约 + 无致命异常 + degraded 一致性 |
| `test_sprint2_a2_e2e.py` | 4/4 | PASSED | LLM 路由装配链（resolve_llm_config 真实 ChatOpenAI 选路；只读 model_name 几乎不发请求） |
| `test_sprint2_c1_e2e.py` | 3/3 | PASSED | graph interrupt/resume 真实链路：`natural_pause_at_planning` / `revise_self_loop_then_approve_to_end` / `cancel_routes_to_end` |
| `test_plan_review.py::test_d5_e1_interrupt_payload_contract_e2e` | 1/1 | PASSED | D5 interrupt payload 契约真实链路 |
| `test_graph_e2e.py` | 3 | **SKIPPED** | 永久设计性 skip（见 §3.3，非凭证原因） |

逐条明细与 06-13 §6 待补清单的 e2e 文件一一对应，覆盖无遗漏。

### 3.3 3 条永久 skip 的性质澄清（非凭证、有等价替代）

`test_graph_e2e.py` 的 3 条（`test_e2e_d1_01_full_pipeline_invoke_acceptance` / `test_e2e_d1_02_placeholder_nodes_do_not_pollute_state` / `test_e2e_d1_03_sqlite_checkpoint_persist_and_resume`）被 skip，**不是凭证原因**，而是文件头部显式声明的永久 skip：

```python
pytestmark = [pytest.mark.e2e, pytest.mark.skip(reason="sp1 D1 e2e 与 sp2 C1（planning interrupt 暂停 + 真节点）语义冲突...本旧套件保留为历史证据，永久 skip")]
```

语义冲突点：sp1 D1 假设全图一路 invoke 到 END（占位节点），而 sp2 C1 在 planning 节点引入了 interrupt 人在回路暂停 + 真实节点，全图不再一路跑到底。该套件保留为历史证据。其语义已由 `test_sprint2_c1_e2e.py` 的 3 条真实链路用例**等价取代**（natural_pause / revise→approve→END / cancel→END，本次又全绿，且 2026-06-03 已验收过）。**属设计性永久 skip，非退化、非凭证缺失。**

---

## 4. 与 06-13「待凭证补跑清单」逐项对账

源清单：`2026-06-13_sprint2-final-acceptance.md` §6。

| 06-13 待补项 | 覆盖 e2e | 本次状态 | 说明 |
|---|---|---|---|
| `test_sprint2_c1_e2e.py`（3 用例）happy/revise/cancel 真实链路（AC-S2-01/02/03/06/13） | 3/3 PASSED | **已转正** | interrupt 自然暂停 + revise 自循环→approve→END + cancel→END 真实链路全绿 |
| `test_sprint2_b2_e2e.py` resource_scout 真实选仓 + 候选展示（AC-S2-04） | 7/7 PASSED | **已转正** | 含真实 git clone / 死活链接 / pwc JSON 契约 / degraded 一致性 |
| `test_paper_intake_e2e.py` / `test_paper_analysis_e2e.py` sp1 节点真实 LLM 链路 + B1 新字段真实产出 | intake 4/4 + analysis 6/6 PASSED | **已转正** | analysis 含 B1 字节级 cache 断言（commit e97712e 修正真实验证通过） |
| `test_sprint2_a2_e2e.py`（4 用例）resolve_llm_config 真实装配选路（AC-S2-11） | 4/4 PASSED | **已转正** | override/default/None node/create_initial_state 四装配链 |
| **Prompt Cache planning 维度回归（S2-13 静态引导段 R-PC4 / L-S2-13-02 / §5.3）** | 无专门用例 | **仍未覆盖** | 见 §6.2，本次 25 条无 planning 维度 cache 字节级回归用例 |
| AC-S2-12 / AC-S2-19 manual UI 贯通（`streamlit run app.py` 三页面手动走查） | 无自动化 | **仍未覆盖（自动化无法补）** | 见 §6.1，需 Maria 手动走查或维持 manual-only 标注 |

**对账结论**：28 条 e2e marker 用例已实跑 25 条全绿 + 3 条永久设计性 skip（有等价替代），e2e 维度已全部转正。仅剩 2 类残留项（manual UI + planning cache），见 §6。

---

## 5. 本次会话两个 commit 退化核查（自跑非 e2e 回归）

为确认本次会话提交的 e97712e（测试断言修正）+ c7a9c4b（gitignore）未引入退化，自跑非 e2e mock 回归（不烧凭证配额）：

命令：`.venv/bin/python -m pytest -q -m "not e2e" --ignore=tests/test_paper_intake.py`
结果：**559 passed / 0 failed / 25 skipped / 28 deselected / 1 warning，85.54s**。

- 与 06-13 基线（559 passed / 0 failed / 25 skipped / 28 deselected）**逐项一致，零退化**。
- commit e97712e 改动核查：仅 `tests/test_paper_intake_e2e.py`... 实为 `tests/test_paper_analysis_e2e.py`（+8/-3 行），将 cache 字节级断言从只比对 `_ANALYSIS_SYSTEM_PROMPT_BODY` 改为比对 `BODY + '\n' + _LANGUAGE_POLICY_SECTION` 拼接常量，与生产代码 `core/nodes/paper_analysis.py:174` 拼接方式对齐。**纯测试代码，不碰生产代码。** 该断言已在本次 analysis e2e 6/6 中真实链路通过。
- commit c7a9c4b 改动核查：仅 `.gitignore`（+2/-2），`checkpoints.db` → `checkpoints.db*` 通配以一并忽略 WAL 伴生文件（-shm/-wal）。**纯配置，无代码影响。**

25 skipped 为已知设计性 skip（shadcn 迁移 AppTest iframe 盲区，已由 Playwright browser e2e / `*_logic.py` 直调等价覆盖）；28 deselected 为 e2e marker 用例（非 e2e 跑被正确排除）。

---

## 6. 残留项裁决（是否阻断 Sprint 2 转正）

### 6.1 残留项 A：AC-S2-12 / AC-S2-19 manual UI 贯通

- **性质**：手动 UI 操作（`streamlit run app.py` 三页面人工走查），PRD 显式标注 manual-only。自动化无法补（即便 Playwright browser e2e 已覆盖三页流转 mock 后端，但真实凭证下的人工端到端体验需人眼确认）。
- **处置**：需 Maria 手动走查，或维持 manual-only 标注。
- **是否阻断转正**：**不阻断**。属 PRD 既定 manual-only 范围，且 browser e2e（06-13 报告 §1，12 passed）已提供三页面 UI 流转的自动化旁证。

### 6.2 残留项 B：planning 维度 Prompt Cache cache 字节级回归（R-PC4 / L-S2-13-02）

- **核实结论**：本次 25 条 e2e 中，cache 字节级断言用例仅两个维度——
  - `test_paper_analysis_e2e.py::test_e2e_prompt_cache_system_prompt_byte_identical`（**analysis 维度**）；
  - `test_sprint2_b2_e2e.py::test_e2e_prompt_cache_system_body_byte_identical`（**resource_scout 维度**）。
  - `test_sprint2_c1_e2e.py` 的 3 条覆盖 planning **interrupt/resume/cancel 路由语义**，**不含** planning prompt 的 cache 字节级断言。
  - 全仓亦无 `tests/test_planning*.py` / `test_sprint2_b3*.py` 的 planning 维度 cache 单测或 e2e。
- **如实标注**：**仍未覆盖**。S2-13 在 planning prompt 追加了静态引导段（`REPO_QUALITY_SCORING_SECTION` 等），2026-06-02 cache 回归仅覆盖 paper_analysis 维度（R_after=0.7601 ≥ 0.7286 PASS），planning 维度的静态引导段是否破坏前缀字节稳定性 / cache 命中率，**至今无专门 e2e 用例验证**。建议补一条 planning 维度的 `system body byte_identical` e2e（与 b2/analysis 同范式），或并入既有 c1_e2e 链路追加一次 planning prompt 前缀字节断言。
- **是否阻断转正**：**不阻断**（降级为非阻断遗留）。理由：(1) cache 命中率守门基线（AC-S2-08，R_after=0.7601）已在 paper_analysis 维度 PASS，前缀稳定性范式经 analysis + resource_scout 两维度真实链路双证有效；(2) planning 静态引导段为常量拼接（与 analysis 的 `_LANGUAGE_POLICY_SECTION` 同模式），逻辑上对所有任务字节级一致，破坏 cache 前缀的概率低；(3) 即便回归命中率微降，影响的是成本而非功能正确性，不影响复现流水线可用性。**建议作为 Sprint 3 启动前的独立小补缺项跟踪**（已记 TODO）。

### 6.3 总裁决

**Sprint 2 阶段 E 凭证依赖项：转正 PASS。** e2e 维度真实链路 25/25 全绿，0 failed，0 生产 BUG。两类残留项（manual UI 自动化无法补 + planning cache 维度仍未覆盖）**均为非阻断遗留**，不影响 Sprint 2 转正结论。06-13 的「有条件 PASS」中「有条件」唯一来源（凭证缺失）已消除。

---

## 7. 失败排查

**无失败用例。** smoke 4/4 + 全量 21/21 真实链路全绿；非 e2e 回归 559 passed / 0 failed。**零生产 BUG。**

警告 1 条：langgraph `LangChainPendingDeprecationWarning`（`allowed_objects` 默认值将变更），库版本预存，与 sp2 代码无关，e2e 与非 e2e 两轮均稳定复现该条，属非阻断观察项（06-13 报告 §8 已记）。

---

## 8. 后续动作

| 项 | 处置 | 触发条件 |
|---|---|---|
| planning 维度 Prompt Cache cache 字节级回归（R-PC4 / L-S2-13-02） | 建议补一条 planning 维度 `system body byte_identical` e2e，或并入 c1_e2e 链路 | Sprint 3 启动前的独立小补缺项（凭证就绪日可顺带跑，注意 deepxiv 日配额） |
| AC-S2-12 / AC-S2-19 manual UI 贯通 | Maria 手动走查或维持 manual-only 标注 | 凭证就绪后人工 manual 验收 |
| L-A3-01 `tests/test_paper_intake.py` main() 风格自测脚本标准化 | 改写为 pytest 用例纳入回归网 | Sprint 3 启动前或独立小任务 |
| 库级 `LangChainPendingDeprecationWarning` | 观察，待 langgraph 升级或显式传 `allowed_objects` | 非阻断 |

---

## 9. 总体结论

**Sprint 2：凭证依赖项转正 PASS，可结项。**

- e2e 维度：真实链路 **25/25 全绿**（smoke 4 + 全量 21），0 failed，0 生产 BUG；3 条永久设计性 skip（非凭证、`test_sprint2_c1_e2e.py` 等价取代）。06-13「有条件 PASS」的凭证条件已消除。
- 非 e2e 回归：**559 passed / 0 failed**，本次会话两个 commit（e97712e 测试断言修正 + c7a9c4b gitignore）零退化，均为纯测试/配置改动，不碰生产代码。
- 残留两项均非阻断：AC-S2-12/19 manual UI（PRD 既定 manual-only，自动化无法补，browser e2e 已旁证）+ planning 维度 cache 字节级回归（仍未覆盖，建议 Sprint 3 前补缺，逻辑上低风险）。
