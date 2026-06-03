# 测试执行报告 - prompt-cache-regression（E2 / AC-S2-08）

- **日期**：2026-06-02 19:49 PDT
- **执行人**：@测试工程师代理
- **Sprint**：sprint2
- **触发原因**：dev-plan 阶段 E 任务 E2「Prompt Cache 命中率回归」提前执行（凭证就绪后消除 sp2 最大悬置风险）。验证 B1 对 paper_intake / paper_analysis 追加 `_LANGUAGE_POLICY_SECTION` / `_LANGUAGE_POLICY_SECTION_INTAKE`（方案 A：system prompt 主体之后、动态上下文之前）后，Prompt Cache 命中率相对 S-3 基线**不退化**。
- **commit**：`000fbdb`（sp2 b2）

| 项目 | 值 |
| --- | --- |
| 关联约束 | PRD R-PC4 / AC-S2-08 / dev-plan §5.3 + E2 |
| S-3 基线 R_baseline | **0.7669（76.69%）** |
| 守门线 R_after ≥ R_baseline × 0.95 | **0.7286** |
| 本次实测 **R_after** | **0.7601（76.01%）** |
| **守门判定** | **PASS**（0.7601 ≥ 0.7286，余量 +0.0315 / +4.3pp） |
| vs S-3 基线 | -0.0068（-0.68pp，基本持平，落差由 ReAct agent 章节读取非确定性主导，非前缀失稳） |

---

## 1. 执行范围

- **命令**：`.venv/bin/python scripts/spike_prompt_cache_baseline.py`
- **方法学**：完全复用 Spike S-3 基线脚本 `scripts/spike_prompt_cache_baseline.py`，**未做任何改动**（详见 §6）。脚本在 B1 改造后可直接导入运行（`create_initial_state` 签名 `(user_input, llm_config, workspace_dir=None)` 兼容；A1 移除 `state["llm_config"]` 镜像字段后脚本不读它，无影响）。
- **流程**：阶段 1 跑 1 次 `paper_intake` 拿 paper_meta（不计入基线）→ 阶段 2 连跑 `paper_analysis` × 3 次，monkey-patch `ChatOpenAI.invoke` 出口收集每次调用的 `usage.prompt_tokens_details.cached_tokens` / `prompt_tokens`，与 S-3 同口径。
- **覆盖用例**：CP-E2-1 / CP-E2-2 / CP-E2-3 / CP-E2-4；交叉验证 `tests/test_sprint2_b1.py::test_cp_b1_3 / test_cp_b1_4 / test_cp_b1_5`（前缀字节级一致单测）。
- **是否包含 e2e**：**是**。凭证来源：项目根 `.env` 注入的 `LLM_API_KEY` + `DEEPXIV_TOKEN`（仅记录 env 变量名，不写值）。

### 1.1 实验环境（与 S-3 同 provider，保证横向可比）

| 参数 | 值 | 与 S-3 是否一致 |
| --- | --- | --- |
| `arxiv_id` | `2405.14831`（HippoRAG） | 一致 |
| Provider / Gateway | NVIDIA inference-api gateway | 一致 |
| `LLM_BASE_URL` | `https://inference-api.nvidia.com/v1` | 一致 |
| `LLM_MODEL` | `azure/openai/gpt-5.4` | 一致 |
| sp 代码状态 | B1 已落地（`_LANGUAGE_POLICY_SECTION` 方案 A）+ A1~A6 + B2 已合入 | 改造后（S-3 为改造前现状） |

---

## 2. 结果摘要

- **通过**：4 个 E2 自测检查点（CP-E2-1/2/3/4）+ 3 个 B1 字节级一致单测交叉验证全绿。
- **失败**：0
- **跳过**：0（凭证就绪，未触发 skipif）
- **警告**：1 条 `LangChainPendingDeprecationWarning`（langgraph 库级 `allowed_objects` 预存警告，与本任务无关，sp2 各报告均已记录为遗留项）；脚本输出 1 条业务 WARNING（`Appendix H ... not found` deepxiv fuzzy-not-found，sp1 既有行为，不影响 paper_analysis 输出 degraded=False）。
- **总耗时**：paper_intake 15.12s + 阶段 2 三次连跑 154.35s ≈ 170s（单次真实 LLM 链路调用 + deepxiv 工具）。

### 2.1 三次连跑命中率表

| run | LLM 调用数 | `total_cached_tokens` | `total_prompt_tokens` | **命中率 R** | sections_read | 耗时 |
| --- | --- | --- | --- | --- | --- | --- |
| **#1 cold** | 4 | 17,408 | 30,547 | **56.99%** | 6 | 53.17s |
| **#2 hit** | 4 | 26,880 | 31,030 | **86.63%** | 7 | 49.03s |
| **#3 stable** | 4 | 22,912 | 35,033 | **65.40%** | 7 | 52.15s |

```
R_after = mean(R_2, R_3) = (0.8663 + 0.6540) / 2 = 0.7601
```

### 2.2 per-call 明细（关键：前缀字节稳定性证据）

| run | call | cached | prompt | 命中率 | 说明 |
| --- | --- | --- | --- | --- | --- |
| #1 | 1 | 0 | 3,110 | 0.0% | 冷启动（无前缀缓存） |
| #1 | 2 | 3,072 | 4,635 | 66.3% | |
| #1 | 3 | 4,608 | 9,757 | 47.2% | |
| #1 | 4 | 9,728 | 13,045 | 74.6% | |
| **#2** | **1** | **3,072** | **3,110** | **98.8%** | system prompt 前缀全命中 |
| **#2** | **2** | **4,480** | **4,635** | **96.7%** | |
| #2 | 3 | 9,600 | 9,757 | 98.4% | |
| #2 | 4 | 9,728 | 13,528 | 71.9% | 末尾 schema 强制 + 新增章节正文 |
| **#3** | **1** | **3,072** | **3,110** | **98.8%** | **与 #2 call1 字节级一致** |
| **#3** | **2** | **4,480** | **4,635** | **96.7%** | **与 #2 call2 字节级一致** |
| #3 | 3 | 4,736 | 10,630 | 44.6% | agent 选了不同章节序列 → 新内容多 |
| #3 | 4 | 10,624 | 16,658 | 63.8% | 同上，prompt 总量随 agent 行为浮动 |

> **核心结论（前缀字节稳定性 PASS）**：run #2 与 run #3 的 **call 1（cached=3072/prompt=3110=98.8%）与 call 2（cached=4480/prompt=4635=96.7%）跨 run 字节级一致**。这两次调用对应「SystemMessage（含 `_LANGUAGE_POLICY_SECTION`）+ 早期 HumanMessage 前缀」的纯命中，证明 B1 追加的语言策略段落**没有破坏前缀字节稳定性**——若该段落含动态变量或换行符问题，call 1 命中率会显著塌陷而非稳定在 98.8%。

---

## 3. 守门判定与 S-3 横向对比

| 维度 | S-3 基线（2026-05-25，改造前） | E2 回归（2026-06-02，B1 改造后） | 差异 |
| --- | --- | --- | --- |
| R1 (cold) | 52.87% | 56.99% | +4.12pp |
| R2 (hit) | 76.69% | 86.63% | +9.94pp |
| R3 (stable) | 76.69% | 65.40% | -11.29pp |
| **R_after = mean(R2,R3)** | **76.69%** | **76.01%** | **-0.68pp** |
| 守门线 (×0.95) | -- | 72.86% | 余量 +4.3pp |
| call1/call2 前缀命中（hit/stable run） | 95.1% / 97.3% | 98.8% / 96.7% | 持平（同量级，字节级稳定） |

**判定**：`R_after = 0.7601 ≥ 0.7286` → **AC-S2-08 PASS**。B1 改造对 Prompt Cache 命中率的影响为 **-0.68pp（基本持平，在抖动容差内）**，理想结果。

**关于 R3 单点波动（65.40% vs S-3 的 76.69%）的归因**：
- S-3 当次 run #2/#3 的 agent 恰好读取了**相同章节序列**，故 R2=R3 字节级完全一致（76.69%）；
- 本次 run #3 的 ReAct agent 选择了**不同章节读取路径**（call3 读到的新章节正文多 → cache miss 多，prompt 总量 35,033 > run#2 的 31,030），导致 R3 偏低；
- 这是 **ReAct agent 行为非确定性**（temperature=0.3 + 工具选择自由度），**不是 prompt 前缀字节失稳**——前缀稳定性由 §2.2 call1/call2 跨 run 字节级一致直接证伪了"前缀被破坏"的假设。
- R_after 取 R2/R3 均值正是为吸收这类 agent 行为抖动，0.7601 仍稳过守门线。

---

## 4. 与 B1 字节级一致单测的交叉验证

执行 `.venv/bin/python -m pytest tests/test_sprint2_b1.py -q -k "cp_b1_3 or cp_b1_4 or cp_b1_5"` → **3 passed**：

- **CP-B1-3**：`_LANGUAGE_POLICY_SECTION` 是 module-level 常量，多次 `_build_analysis_system_prompt({...不同 arxiv...})` 该段落字节级一致（含无 f-string 占位断言）；
- **CP-B1-4**：`_ANALYSIS_SYSTEM_PROMPT_BODY` 改造后与 sp1 字节级一致；
- **CP-B1-5**：两篇不同论文截取 SystemMessage，去尾部 `--- 当前论文上下文 ---` 段落后字节级一致。

> 单测（静态字节级断言）+ 实测（运行时跨 run cached_tokens 字节级命中）**双重互证**前缀治理完好，闭合了 CP-E2-2/CP-E2-3 的字节级因果链。

---

## 5. 失败排查

**无失败**。守门 PASS，无需停工排查字节级差异，无需转交全栈开发代理。

唯一需要解释的现象（R3 单点偏低）已在 §3 归因为 ReAct agent 章节读取非确定性，非生产代码 bug、非前缀失稳，处置为「正常波动、纳入 R_after 均值吸收」。

---

## 6. 是否复用 S-3 脚本 / 改动说明

- **完全复用** `scripts/spike_prompt_cache_baseline.py`，**0 行改动**。
- 验证脚本在 B1 改造后兼容：dry-import 通过；`create_initial_state` 签名 `(user_input, llm_config, workspace_dir=None)` 未变，老形态 LLMConfig 兜底仍在，脚本内 `create_initial_state(user_input=arxiv_id, llm_config=llm_config)` 正常工作；A1 移除 `state["llm_config"]` 镜像字段不影响（脚本不读该字段，且 `_merge_update` 重置的字段均存在）。
- 靶论文与 S-3 一致：`arxiv_id=2405.14831`（HippoRAG）；base_url / model 同从 `config` 默认取（NVIDIA gateway + gpt-5.4），保证横向可比。

---

## 7. 可选对照实验（CP-E2-5）说明 —— 经评估不跑

dev-plan CP-E2-5 标注为「**可选**」对照实验（在 `_LANGUAGE_POLICY_SECTION` 追加 8 字节随机后缀，预期 `R_disturbed << R_after`）。**本次不执行**，理由：

1. 前缀字节稳定性已由**双重硬证据**闭合：(a) §4 B1 字节级一致单测 PASS；(b) §2.2 实测 run#2/#3 的 call1/call2 cached_tokens **跨 run 字节级一致（98.8% / 96.7%）**——后者本身就是"前缀稳定 → 高命中"的运行时正向证据；
2. 扰动实验仅能再证一遍"破坏前缀 → 命中塌陷"的反向命题，对已 PASS 的守门判定无新增信息，却需额外消耗 ~100k LLM token（任务明确要求控制 token 在必要 3 次跑 + 必要校验内）；
3. 若后续守门出现可疑边界值（如 R_after 贴近 0.7286），再补扰动对照定位。当前余量 +4.3pp 充裕，无此需要。

---

## 8. 自测检查点验收（CP-E2-1 ~ CP-E2-5）

| CP | 验收点 | 结论 | 证据 |
| --- | --- | --- | --- |
| **CP-E2-1** | sp2 改造后 paper_analysis ×3 实测完成，无 LLM 报错 | **PASS** | 3 次 run 均完成 degraded=False / sections_read=6~7 / method_len 529~644；0 ERROR |
| **CP-E2-2** | `R_sp2 >= R_baseline × 0.95` | **PASS** | R_after=0.7601 ≥ 0.7286（余量 +4.3pp） |
| **CP-E2-3** | 若不达标定位字节级差异并修正 | **N/A（已达标）** | 守门 PASS，无需修正；前缀字节稳定性经单测+实测双证 |
| **CP-E2-4** | 报告含原始日志 + 命中率表 + R_baseline/R_sp2 对比 | **PASS** | §2 命中率表 + per-call 明细 + §9 INFO 日志片段 + §3 横向对比 |
| **CP-E2-5** | （可选）扰动对照 R_disturbed << R_sp2 | **跳过（可选）** | §7 说明：双重硬证据已闭合，不再额外耗 token |

---

## 9. `_log_cache_metrics` 原始 INFO 日志片段（运行时实证）

```
# run #2 (hit) —— 前缀全命中
core.llm_client: Prompt cache hit: cached_tokens=3072 / prompt_tokens=3110 (98.8%)
core.llm_client: Prompt cache hit: cached_tokens=4480 / prompt_tokens=4635 (96.7%)
core.llm_client: Prompt cache hit: cached_tokens=9600 / prompt_tokens=9757 (98.4%)
core.llm_client: Prompt cache hit: cached_tokens=9728 / prompt_tokens=13528 (71.9%)
# run #3 (stable) —— call1/call2 与 run#2 字节级一致
core.llm_client: Prompt cache hit: cached_tokens=3072 / prompt_tokens=3110 (98.8%)
core.llm_client: Prompt cache hit: cached_tokens=4480 / prompt_tokens=4635 (96.7%)
core.llm_client: Prompt cache hit: cached_tokens=4736 / prompt_tokens=10630 (44.6%)
core.llm_client: Prompt cache hit: cached_tokens=10624 / prompt_tokens=16658 (63.8%)
```

完整 per-call metric 见原始 JSON：`workspace/runs/spike-s3-prompt-cache-baseline_20260602-194921.json`。

---

## 10. 后续动作

- E2 / AC-S2-08 守门 **PASS**，无遗留阻断项。
- 遗留观察项（非阻断）：ReAct agent 章节读取非确定性会让单次 R 在 ~65%~87% 间波动；若 sp3 引入 provider 切换或更大论文，建议重采基线。
- langgraph 库级 `LangChainPendingDeprecationWarning` 为跨报告长期遗留项，不归本任务处置。

---

## 附录：交付物清单

- 本报告：`docs/sprint2/test-reports/2026-06-02_prompt-cache-regression.md`
- 复用脚本（0 改动）：`scripts/spike_prompt_cache_baseline.py`
- 原始指标 JSON：`workspace/runs/spike-s3-prompt-cache-baseline_20260602-194921.json`
- 对比基线：`docs/sprint2/test-reports/2026-05-25_prompt-cache-baseline.md`（R_baseline=0.7669）
- 交叉验证单测：`tests/test_sprint2_b1.py::test_cp_b1_3 / test_cp_b1_4 / test_cp_b1_5`
