# 测试执行报告 - Sprint 2 最终验收（阶段 E：E1 + E3）

- **日期**：2026-06-13 11:30（本地时区）
- **执行人**：@测试工程师代理
- **Sprint**：sprint2
- **触发原因**：Sprint 2 最终收尾验收（dev-plan 阶段 E：E1 单元/集成自测 + E3 交付物完整性 + AC 覆盖矩阵）。S2-13 等全部 sp2 实现已 commit（084fd46）。
- **commit**：084fd46
- **结论**：**有条件 PASS**（不依赖凭证的全部验收项 PASS；真实 LLM / deepxiv 的 E2E 因凭证缺失一律 skip 不伪造，列入「待凭证补跑」清单）

---

## 0. 前置：凭证现状

| 凭证 | 状态 |
|---|---|
| `config.get_llm_api_key()` | **EMPTY** |
| `config.get_deepxiv_token()` | **EMPTY** |

本轮聚焦不依赖凭证的验收：全套非 e2e 回归 + mock 集成自测 + Playwright browser e2e（mock 后端）+ 交付物完整性 + AC 覆盖矩阵。真实凭证驱动的 e2e marker 用例（28 条）全部 skip，明确列入 §6 待凭证补跑清单。

---

## 1. 执行范围

| 项 | 命令 |
|---|---|
| 全套非 e2e 回归（3 次连跑） | `.venv/bin/pytest -q -m "not e2e" --ignore=tests/test_paper_intake.py` |
| Playwright browser e2e | `.venv/bin/pytest -m browser -q` |
| e2e marker 收集 | `.venv/bin/pytest -m e2e --collect-only -q` |
| 交付物 import / 导出 / 签名核查 | python 探针（importlib + inspect） |

- 是否包含真实 e2e（LLM/deepxiv）：**否**（凭证 EMPTY，全部 skip）。
- browser e2e 凭证依赖：**无**（mock GraphController + 注入 payload，真起 streamlit 子进程 + chromium）。

---

## 2. 结果摘要

| 指标 | 值 |
|---|---|
| 非 e2e 回归（run1/2/3） | **559 passed / 0 failed / 25 skipped / 28 deselected**（三次完全一致，82.98s / 83.20s / 83.29s） |
| Playwright browser e2e | **12 passed / 0 failed / 600 deselected**（78.46s，chromium 真起动非 skip） |
| e2e marker（真实凭证） | **28 collected** → 凭证 EMPTY 全部 skip（不伪造） |
| 警告 | 1（langgraph `LangChainPendingDeprecationWarning`，库级预存，与 sp2 无关，见 §5 遗留） |
| 补强测试数 | **0**（既有覆盖已完整满足 dev-plan §5.2 mock 场景 + E1 五个 CP；净增 0，仍 0 failed） |

**25 skipped 性质核查**：全部为 shadcn 迁移后「AppTest 看不到 iframe」的 UI 文本/按钮断言（`ui.alert` / `ui.button` / `ui.accordion` 渲染在 iframe），已迁移到 Playwright browser e2e 或被 `*_logic.py` 直调测试等价覆盖。属设计性 skip，**非退化**，每条 skip 都带明确 reason 与替代覆盖指向。

**补强决策说明**：本轮通读 dev-plan §5.2（E2E-1~6）+ E1 五个 CP 后核查既有测试，确认 mock 可覆盖的场景已全部落地且严格（见 §3）。无新增缺口，故净增 0 测试，回归基线维持 559 passed / 0 failed 零退化。

---

## 3. E1：单元测试与集成自测结论

### 3.1 全套回归零退化（CP-E1-2 / CP-E1-3）

3 次连跑均 **559 passed / 0 failed / 25 skipped**，无 flaky。sp1 基线零退化（A1-9 / A3-4 / A3-5 / B1-10 / C1 守门历次验收均生效，本轮全量复跑无破裂）。

### 3.2 集成视角自测（mock 链路，不需真实 LLM）

| 场景 | 对应 E2E / AC | 覆盖测试 | 结论 |
|---|---|---|---|
| graph 编译 + 7 业务节点串接 | C1 | `test_sprint2_c1.py::test_cp_c1_1/2`（编译 + 7 节点） | PASS |
| interrupt 自然暂停（planning） | AC-S2-02 | `test_sprint2_c1.py::test_cp_c1_9_natural_pause_at_planning` | PASS |
| resume approve → next | AC-S2-01 | `test_cp_c1_9b_resume_approve_routes_to_next` | PASS |
| resume revise self-loop | AC-S2-06 | `test_cp_c1_9c_resume_revise_self_loop` + `test_revise_count_increments_across_self_loops` | PASS |
| **E2E-3 revise 无上限 + 不强制 approve** | AC-S2-06 | `test_sprint2_b3.py::test_cp_b3_5_six_revises_no_forced_approve`（连续 6 次 revise，计数单调递增至 6，从不返回 reproduction_plan，无 revise_limit 痕迹） | PASS |
| 软提示阈值 | AC-S2-06 | `test_cp_b3_3_approve`（`soft_hint_threshold == PLANNING_SOFT_HINT_THRESHOLD`）+ plan_review N≥5 软提示卡片 | PASS |
| **E2E-2 resource_scout 全失败降级 from_scratch** | AC-S2-05 | `test_sprint2_b2.py`（degraded + from_scratch）+ `test_sprint2_c1.py` 全图链路 | PASS |
| **E2E-1-bis 多模型路由 P1/P2/P3** | AC-S2-11 | `test_llm_routing.py`（24 用例：override 命中 / fallback default / None node / PermanentError 6 形态）+ `test_react_base_sp2.py`（P1/P2/P3 装配链） | PASS |
| **E2E-4 switch_repo 重路由** | AC-S2-07 | `test_sprint2_b3.py::test_cp_b3_6_*` + `test_sprint2_c1.py::test_switch_repo_resource_info_persists_then_approve` | PASS |
| **E2E-6 cancel 终止任务** | AC-S2-13 | `test_cp_c1_10_cancel_routes_to_end`（cancel→END）+ `test_cp_b3_8_cancel`（current_step=cancelled_by_user）+ `test_app_controller.py`（cancel_task） | PASS |
| SQLite 跨实例 reload 状态存活 | AC-S2-02 | `test_analysis_notes_survives_sqlite_reload` | PASS |
| 路由优先级（cancelled > approved） | AC-S2-13 | `test_route_priority_cancelled_overrides_approved_via_graph` | PASS |

### 3.3 项目级守门脚本（CP-E1-4 / CP-E1-5）

- **CP-E1-4 序列化合规**：`git_tools._serialize_tool_result` / `pwc_tools._serialize_tool_result` / `deepxiv_tools._serialize` 三处均为 `json.dumps(ensure_ascii=False, sort_keys=True, default=str)`，全仓工具层 **0 处 `str(dict)` 漏网**（出现的 `str(dict)` 字样全是 docstring 警示注释）。**PASS**。
- **CP-E1-5 `_map_*_result` 3 参签名**：`_map_intake_result` / `_map_analysis_result` / `_map_resource_scout_result` / `_map_planning_result` 四者签名经 `inspect.signature` 实证均为 `(result, state, react_messages)` 3 参（BUG-S1-03 治理范式一致）。**PASS**。

### 3.4 各任务历史验收引用

A1~D5 全部任务已逐任务独立验收 PASS（见 test-reports/ 下 2026-05-27 ~ 2026-06-13 各报告）。本轮为整体收尾复跑，未发现任何历史结论被推翻。

---

## 4. E2：Prompt Cache 命中率回归（引用既有结论）

凭证缺失，**不复跑**，引用 2026-06-02 提前跑过的结论（`docs/sprint2/test-reports/2026-06-02_prompt-cache-regression.md`）：

| 项 | 值 |
|---|---|
| S-3 基线 R_baseline | 0.7669 |
| 守门线（×0.95） | 0.7286 |
| 实测 R_after | **0.7601** |
| 判定 | **PASS**（0.7601 ≥ 0.7286，余量 +4.3pp；vs 基线 -0.68pp 基本持平） |

前缀字节稳定性双证（B1 字节级一致单测 + 实测跨 run call1/call2 cached_tokens 字节级一致），AC-S2-08 PASS。

**注意（待办 R-PC4）**：S2-13 在 planning prompt 追加了静态引导段（`REPO_QUALITY_SCORING_SECTION` 等）。2026-06-02 回归仅覆盖 paper_analysis 维度，**planning 维度 cache 回归待凭证补跑**（S2-13 验收报告 §5.3 已记 L-S2-13-02）。

---

## 5. E3：交付物完整性

### 5.1 交付物文件就位（CP-E3-1）

16 个核心交付物文件 + requirements.txt 全部就位，import 全通过，关键导出齐全：

| 文件 | 任务 | import | 关键导出 |
|---|---|---|---|
| `core/state.py` | A1 | OK | `LLMConfigSet`/`NodeName`/`PaperMeta`/`PaperAnalysis`/`RepoInfo`/`GlobalState`/`create_initial_state` |
| `core/llm_client.py` | A2 | OK | `create_llm`/`resolve_llm_config` |
| `core/react_base.py` | A3 | OK | `_make_react_wrapper` |
| `config.py` | A4 | OK | `PLANNING_SOFT_HINT_THRESHOLD`/`REACT_MAX_ROUNDS_*`/`GIT_CLONE_TIMEOUT`/`WORKSPACE_REPOS_DIR`/`PWC_BASE_URL`/`STREAMLIT_POLL_INTERVAL` |
| `core/tools/git_tools.py` | A5 | OK | `git_clone`/`analyze_local_repo`/`check_url_reachable` + 3 工具工厂 |
| `core/tools/pwc_tools.py` | A6 | OK | `search_pwc_by_arxiv`/`search_pwc_by_title`/`make_search_pwc_tool` |
| `core/nodes/paper_intake.py` | B1 | OK | `_backfill_zh_fields` / `_map_intake_result`(3参) |
| `core/nodes/paper_analysis.py` | B1 | OK | `_backfill_en_fields` / `_map_analysis_result`(3参) |
| `core/nodes/resource_scout.py` | B2 | OK | `resource_scout`(节点入口) / `_map_resource_scout_result`(3参) |
| `core/nodes/planning.py` | B3 | OK | `planning`(节点入口) / `_map_planning_result`(3参) |
| `core/nodes/_repo_scoring.py` | S2-13 | OK | `REPO_QUALITY_SCORING_SECTION` 同口径评分通道 |
| `core/graph.py` | C1 | OK | `build_graph` + 7 节点 + `_route_after_planning`(3 路) |
| `ui/components/llm_config_form.py` | D1 | OK | `render_llm_config_form` |
| `app.py` | D2 | OK | `GraphController` |
| `ui/pages/paper_input.py` | D3 | OK | import OK |
| `ui/pages/analysis_progress.py` | D4 | OK | import OK |
| `ui/pages/plan_review.py` | D5 | OK | import OK |
| `requirements.txt` | D2 | OK | streamlit + streamlit-autorefresh |

### 5.2 治理范式复用（CP-E3-4）

- **BUG-S1-02 序列化合规**：git_tools / pwc_tools / deepxiv_tools 三处一致（§3.3）。
- **BUG-S1-03 backfill 兜底 + 3 参签名**：4 节点 `_map_*_result` 3 参（§3.3）；`_backfill_zh_fields`(paper_intake) / `_backfill_en_fields`(paper_analysis) backfill 兜底 + degraded_nodes 追加就位。
- **WARNING 非静默**：`.warning(` 调用计数 — git_tools 8 / pwc_tools 7 / resource_scout 5 / planning 7（非静默吞错）。

### 5.3 AC 覆盖矩阵（AC-S2-01 ~ AC-S2-26）

| AC | 描述 | 覆盖方式 | 测试文件 / 引用 |
|---|---|---|---|
| AC-S2-01 | 完整 happy path | **待凭证 e2e** | `test_sprint2_c1_e2e.py`（skip）；mock 链路 `test_sprint2_c1.py::cp_c1_9b` |
| AC-S2-02 | interrupt 中断/恢复语义 | 自动化（mock） | `test_sprint2_c1.py`（c1_9/9b/9c + sqlite reload）；spike S-1 PASS |
| AC-S2-03 | interrupt 在轮询架构下恢复 | 自动化（mock）+ 待凭证 e2e | `test_app_controller.py`（poll_state/resume_with）；spike S-1/S-2 |
| AC-S2-04 | resource_scout 自动选仓与候选展示 | **待凭证 e2e** + mock | `test_sprint2_b2_e2e.py`（skip）；`test_sprint2_b2.py` mock |
| AC-S2-05 | 全失败降级 from_scratch | 自动化（mock） | `test_sprint2_b2.py` + `test_sprint2_c1.py` |
| AC-S2-06 | revise 无上限 + 软提示 | 自动化（mock） | `test_sprint2_b3.py::cp_b3_5`（6 次）+ plan_review 软提示 |
| AC-S2-07 | switch_repo 路由 | 自动化（mock） | `test_sprint2_b3.py::cp_b3_6` + `test_sprint2_c1.py` |
| AC-S2-08 | Prompt Cache 不回退 | 自动化（已跑，引用） | `2026-06-02_prompt-cache-regression.md`（R_after=0.7601 PASS）；planning 维度待凭证补跑 |
| AC-S2-09 | 新字段降级兜底 | 自动化（mock） | `test_sprint2_b1.py`（backfill + degraded + WARNING） |
| AC-S2-10 | git_tools 基础能力 | 自动化（mock） | `test_sprint2_a5.py`（CP-A5-1~10，19 用例） |
| AC-S2-11 | LLM 配置表单（多模型） | 自动化 | `test_llm_config_form.py` + `test_llm_routing.py`（P1/P2/P3） |
| AC-S2-12 | Streamlit 三页面贯通 | **manual-only**（PRD 标注） | 无自动化 UI；browser e2e 部分覆盖三页流转 |
| AC-S2-13 | cancel 终止任务 | 自动化（mock） | `test_cp_c1_10` + `test_cp_b3_8` + `test_app_controller.py` + browser e2e cancel 二次确认 |
| AC-S2-14 | 对话面板替换 textarea | 自动化 | `test_plan_review_logic.py`（S2-12 验收 2026-06-11） |
| AC-S2-15 | 多轮对话实时可见 | 自动化（mock LLM） | `test_plan_review_logic.py` |
| AC-S2-16 | 敲定方向触发一次重规划 | 自动化（mock） | `test_plan_review_logic.py`（resume_with revise 恰一次） |
| AC-S2-17 | 对话不直接落计划 | 自动化（mock） | `test_plan_review_logic.py` |
| AC-S2-18 | 模型不可用降级不崩页 | 自动化（mock） | `test_plan_review_logic.py` |
| AC-S2-19 | UI 手动贯通 | **manual-only**（PRD 标注） | 无自动化；browser e2e 部分覆盖 |
| AC-S2-20 | 贴链接经模型判断纳入候选 | 自动化（mock） | `test_sprint2_s2_13.py` / `_boundary.py`（工具调用 + repos 合并） |
| AC-S2-21 | 切换仓库后质量分非 0 | 自动化（mock） | `test_sprint2_s2_13_boundary.py` + browser e2e（真实 quality_score 非 0） |
| AC-S2-22 | 评分口径一致可比 | 自动化（mock） | `test_sprint2_s2_13.py`（_repo_scoring 同口径字节等价） |
| AC-S2-23 | stars/forks 留空不报错 | 自动化（browser e2e） | `test_sprint2_s2_13_e2e.py`（「⭐ —」「🍴 —」不崩页） |
| AC-S2-24 | 不值得加入不抓取 | 自动化（mock） | `test_sprint2_s2_13_boundary.py`（不调工具 / repos 长度不变） |
| AC-S2-25 | clone 失败降级不崩 | 自动化（mock）+ browser e2e | `test_sprint2_s2_13.py`（switch_repo_failed payload 强制断言 + 三类失败参数化）+ e2e 重填提示 |
| AC-S2-26 | 与 S2-12 对话 revise 衔接 | **manual-only**（自动化部分由 AC-20/25 覆盖） | `test_sprint2_s2_13.py` 部分 |

**覆盖统计**：
- **自动化测试覆盖（mock，现已全绿）**：AC-S2-02 / 05 / 06 / 07 / 09 / 10 / 11 / 13 / 14 / 15 / 16 / 17 / 18 / 20 / 21 / 22 / 23 / 24 / 25 — **19 条**
- **自动化（已跑 / 引用结论）**：AC-S2-08 — **1 条**（planning 维度待凭证补跑）
- **manual-only（PRD 显式标注，无自动化 UI）**：AC-S2-12 / 19 / 26 — **3 条**
- **待凭证真实 e2e（mock 旁证已覆盖，真实链路 skip）**：AC-S2-01 / 03 / 04 — **3 条**（其中 03 已有 mock 自动化旁证）

---

## 6. 待凭证补跑的真实 e2e 清单

凭证（`LLM_API_KEY` + `DEEPXIV_TOKEN`）就绪后需补跑的 28 条 e2e marker 用例（命令：`.venv/bin/pytest -m e2e -v -s`，固定 arxiv `2405.14831`）：

| e2e 文件 | 覆盖 | 凭证就绪后验收 |
|---|---|---|
| `test_sprint2_c1_e2e.py`（3 用例） | E2E-1 happy path / revise self-loop→approve→END / cancel→END | AC-S2-01 / 02 / 03 / 06 / 13 真实链路 |
| `test_sprint2_b2_e2e.py` | resource_scout 真实选仓 + 候选展示 | AC-S2-04 真实链路 |
| `test_paper_intake_e2e.py` / `test_paper_analysis_e2e.py` | sp1 节点真实 LLM 链路 | sp1 回归 + B1 新字段真实产出 |
| `test_sprint2_a2_e2e.py`（4 用例） | resolve_llm_config 真实 ChatOpenAI 装配选路 | AC-S2-11 真实装配 |
| **Prompt Cache planning 维度回归** | S2-13 静态引导段 cache 影响（R-PC4） | 补 planning 维度 R_after ≥ 0.95×baseline（S2-13 §5.3 / L-S2-13-02） |
| **AC-S2-12 / AC-S2-19 manual UI 贯通** | `streamlit run app.py` 三页面手动走查 | 凭证就绪后人工 manual 验收 |

---

## 7. 失败排查

**无失败用例。** 三次回归 559 passed / 0 failed；browser e2e 12 passed / 0 failed。**零生产 BUG。**

---

## 8. 遗留非阻断项

| ID | 描述 | 性质 |
|---|---|---|
| L-A3-01 | `tests/test_paper_intake.py` 为 sp1 遗留 main() 风格自测脚本（`pytest -q` 收集 0），游离于回归网外，本轮回归用 `--ignore` 排除。建议标准化为 pytest 用例。 | 非阻断（TODO 已记） |
| 库级 warning | langgraph `LangChainPendingDeprecationWarning`（`allowed_objects` 默认值），库版本预存，与 sp2 代码无关。 | 非阻断观察项 |
| L-S2-13-02 | planning prompt 维度 Prompt Cache 回归待凭证补跑（S2-13 静态引导段）。 | 非阻断（§6 待凭证清单） |
| AppTest iframe 盲区 | 25 个 shadcn 迁移 UI 断言降级为 skip，已迁移 Playwright browser e2e / logic 测试等价覆盖。 | 设计性 skip 非退化 |

---

## 9. 后续动作

- 凭证（`LLM_API_KEY` + `DEEPXIV_TOKEN`）就绪后按 §6 清单补跑 28 条真实 e2e + AC-S2-12/19 manual UI 贯通 + planning 维度 Prompt Cache 回归。
- L-A3-01 test_paper_intake.py 标准化（建议 Sprint 3 启动前或独立小任务处理）。

---

## 10. 总体结论

**Sprint 2 阶段 E（E1 + E3）：有条件 PASS。**

- E1：全套非 e2e 回归 **559 passed / 0 failed**（3 次连跑零 flaky，sp1 基线零退化，净增 0 测试仍 0 failed）；mock 集成自测覆盖 dev-plan §5.2 全部可 mock 的 E2E 场景（E2E-1-bis / 2 / 3 / 4 / 6）+ E1 五个 CP（含序列化合规、3 参签名守门）全 PASS；Playwright browser e2e **12 passed**（三页面 UI 流转 mock 后端，chromium 真起动）。
- E2：引用 2026-06-02 既跑结论，R_after=0.7601 ≥ 0.7286，AC-S2-08 PASS。
- E3：16 交付物文件 + requirements 全就位、import 通过、关键导出齐全、治理范式三处复用核实；AC-S2-01~26 覆盖矩阵 = 自动化 19 + 已跑引用 1 + manual-only 3 + 待凭证真实 e2e 3。

**唯一「有条件」来源**：凭证 EMPTY 导致 28 条真实 LLM/deepxiv e2e + manual UI 贯通 + planning 维度 cache 回归无法本轮跑，已全部 mock 旁证覆盖契约层并列入 §6 待凭证补跑清单（一律 skip 不伪造）。**零生产 BUG。**
