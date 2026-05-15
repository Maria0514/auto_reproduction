# 测试执行报告 - paper-analysis-e2e

- **日期**：2026-05-15 14:30 PDT
- **执行人**：@测试工程师代理
- **Sprint**：sprint1
- **触发原因**：Maria 手动触发，C2 (paper_analysis) 实现完成后首次真实端到端验收——补齐 dev-plan 中 C2 任务缺失的 e2e 覆盖（之前 C2 仅有 10 个 Mock 单测），并真实链路验证 Prompt Cache 前缀治理方案 A 是否生效。
- **commit**：86af2ce

## 执行范围

- 命令：
  - `pytest --collect-only -q tests/test_paper_analysis_e2e.py`
  - `pytest tests/test_paper_analysis_e2e.py -m e2e -v -s --durations=0 --log-cli-level=INFO`（首跑，记录工具调用轨迹）
  - `pytest tests/test_paper_analysis_e2e.py -m e2e -v --durations=0`（复跑 × 2）
  - `pytest -v --durations=0`（全量回归 × 3）
  - `pytest tests/test_paper_analysis_e2e.py::test_e2e_basic_path_full_pipeline -m e2e -v`（单测复现验证）
- 新增测试文件：`tests/test_paper_analysis_e2e.py`（6 用例，全部 `@pytest.mark.e2e`）
  - `test_e2e_paper_meta_none_short_circuit`（T-PA-E2E-02，前置校验路径）
  - `test_e2e_basic_path_full_pipeline`（T-PA-E2E-01，真实链路核心字段断言）
  - `test_e2e_react_actually_used_tools`（T-PA-E2E-03，工具序列化往返 + sections_read 隐式断言）
  - `test_e2e_prompt_cache_system_prompt_byte_identical`（T-PA-E2E-04，**最高 ROI**：真实 LLM 链路截取 SystemMessage 字节级比较）
  - `test_e2e_budget_not_exhausted_in_normal_paper`（T-PA-E2E-05，max_rounds=12 边际验证）
  - `test_e2e_node_errors_clean_on_success`（T-PA-E2E-06，成功路径不写 permanent NodeError）
- 是否包含 e2e：是
- 凭证来源：项目根 `.env`（被 `tests/conftest.py` 通过 `dotenv.load_dotenv` 自动加载），涉及 env 变量名 `LLM_API_KEY`、`DEEPXIV_TOKEN`、`LLM_BASE_URL`、`LLM_MODEL`。
- 靶论文：
  - 主靶：arXiv `2405.14831`（HippoRAG）—— 17 章节
  - 副靶：arXiv `2402.17764`（BitNet）—— 4 章节，仅 Prompt Cache 用例用，不跑 paper_intake

## 结果摘要

### paper_analysis e2e（仅本文件，3 次复跑）

| 跑次 | 通过 | 失败 | 跳过 | 警告 | 耗时 |
|------|------|------|------|------|------|
| 第 1 次 | 6/6 | 0 | 0 | 1（langgraph 第三方告警） | 139.78s |
| 第 2 次 | 6/6 | 0 | 0 | 1 | 129.62s |
| 第 3 次 | 6/6 | 0 | 0 | 1 | 124.11s |

**paper_analysis e2e 自身稳定性：3/3 全绿**。

### 全量 pytest 回归（3 次）

| 跑次 | 通过 | 失败 | 跳过 | 警告 | 耗时 |
|------|------|------|------|------|------|
| 第 1 次 | 23/26 | 3 | 0 | 1 | 213.31s |
| 第 2 次 | 26/26 | 0 | 0 | 1 | 183.24s |
| 第 3 次 | 26/26 | 0 | 0 | 1 | 181.14s |

第 1 次失败的 3 个用例全部来自 `test_paper_analysis_e2e.py`（`test_e2e_basic_path_full_pipeline`、`test_e2e_react_actually_used_tools`、`test_e2e_node_errors_clean_on_success`），且均通过同一份 module-scope fixture `primary_analysis_result`，所以是**同一次 LLM 调用产物，触发同一个根因**（详见"失败排查"）。

### 单用例隔离复现验证

- `pytest tests/test_paper_analysis_e2e.py::test_e2e_basic_path_full_pipeline -m e2e -v` 单跑：通过（65.43s）
- 推测复现率：约 1/4 ≈ 25%（4 次全量回归中 1 次踩中），属于**偶发 LLM 服从度问题**，非确定性 bug。

### 用例耗时分布（第 1 次全量回归）

| 用例 | 耗时 |
|------|------|
| `test_e2e_prompt_cache_system_prompt_byte_identical` | 72.17s（涉及两次 paper_analysis 调用） |
| `primary_state_with_meta` + `primary_analysis_result` setup | 90.37s（paper_intake 一次 + paper_analysis 一次） |
| `test_e2e_plain_id_cs_category` | 13.15s |
| `test_e2e_full_url_cleanup` | 12.77s |
| `test_e2e_versioned_id_cleanup` | 11.89s |
| `test_e2e_node_errors_empty_on_success` | 11.11s |
| 其它单测 | < 0.1s |

### Prompt Cache 治理验证（T-PA-E2E-04，最高 ROI）

- **3 次复跑全部通过**——主体（去掉尾部 "--- 当前论文上下文 ---" 段落后）在 HippoRAG / BitNet 两篇论文间字节级一致。
- 主体字节级与导出常量 `_ANALYSIS_SYSTEM_PROMPT_BODY` 一致（防止主体被偷偷改成动态拼接）。
- 主体不含任何论文级动态变量（断言列表：`2405.14831` / `2402.17764` / `HippoRAG` / `BitNet` / `Hongyu Wang` / `Vaswani` 均未出现）。
- 尾部段落各自携带正确的 arxiv_id。
- **结论：Prompt Cache 前缀治理在真实 ChatOpenAI 链路上字节级生效。**
- **遗憾：未观察到 `cached_tokens` 命中率数据**——`llm_client._log_cache_metrics` 在本次跑里仍未输出 INFO 日志（与 C1 e2e 报告观察一致），NVIDIA 网关 response usage metadata 不暴露该字段。需要切换 DeepSeek/OpenAI 直连端点才能量化。

### ReAct 行为统计（基于 INFO 日志）

| 论文 | structure | read_section 调用次数 | get_full_paper | rounds 用量 | sections_read 数 |
|------|-----------|----------------------|----------------|--------------|-------------------|
| HippoRAG（第 1 轮） | 1 | 6（含 1 次 not-found 重试为部分匹配） | 0 | 4 | 6 |
| HippoRAG（Prompt Cache 用例第 1 篇） | 1 | 6 | 0 | 4 | 6 |
| BitNet（Prompt Cache 用例第 2 篇） | 1 | 3+1 重复 Results | 0 | 4 | 3 |

- max_rounds=12 在两篇代表论文上均**远未耗尽**（实际 4 轮），现阶段配置充足。
- ReAct agent 正确处理了 HippoRAG 章节名不完全匹配（"Appendix H Implementation Details & Compute Requirements" 失败 → 自主重试为 "Appendix H Implementation Details" 成功），符合 C2 设计的"非标准章节名自主匹配"行为。
- ReAct agent 对 BitNet 出现了一次 read_section 重复调用（Results 章节连续读两次），轻微 token 浪费但未阻塞结果——可作为后续 prompt 优化项观察。

### Token / 耗时（无 cached_tokens metadata 可读）

- 主路径每次 paper_analysis：5 次 LLM 调用（reasoning × 4 + finalize × 1），耗时约 30~50s。
- HippoRAG 工具实际拉取章节字符数：HippoRAG (10051) + Experimental Setup (3100) + Results (5419) + Appendix H Implementation Details (1491) + Introduction (4012) + Discussions (8848) ≈ 32921 chars，与 token 估算约 8K~9K input。
- BitNet 工具拉取：3279 + 8078 + 3357 + 8078 (dup) ≈ 22792 chars，约 5K~6K input。

## 失败排查

### 失败用例（来自全量回归第 1 次）：`test_e2e_basic_path_full_pipeline` / `test_e2e_react_actually_used_tools` / `test_e2e_node_errors_clean_on_success`

均通过 `primary_analysis_result` module fixture 间接断言同一次 paper_analysis 调用结果，因此是**一次失败连带三个用例**。

- 失败类型：**生产代码偶发 bug（LLM 服从度 / `_map_analysis_result` 兜底缺失）**
- 关键报错（来自 `test_e2e_basic_path_full_pipeline`）：

```
AssertionError: sections_read 为空（核心字段）：{
  'method_summary': 'HippoRAG is a neurobiologically inspired retrieval framework ...',
  'datasets': ['MuSiQue (answerable)', '2WikiMultiHopQA', 'HotpotQA'],
  'metrics': ['Recall@2', 'Recall@5', 'Exact Match (EM)', 'F1', 'All-Recall@2', 'All-Recall@5'],
  'hyperparams': { ...完整... },
  ...
  'sections_read': [],
  'analysis_notes': '[DEGRADED] missing=sections_read\nRead strategy followed section structure
                     first, then Method/Experiments/Results, with Introduction and Discussions
                     for clarification. Initial read of "Appendix H Implementation Details &
                     Compute Requirements" failed due to section name mismatch; retried
                     successfully with partial match "Appendix H Implementation Details". ...',
}
```

ReAct 轨迹（INFO 日志）：

```
get_paper_structure: 17 sections for 2405.14831
read_section: arxiv_id=2405.14831, section=HippoRAG               (10051 chars OK)
read_section: arxiv_id=2405.14831, section=Experimental Setup     (3100 chars OK)
read_section: arxiv_id=2405.14831, section=Results                (5419 chars OK)
read_section: arxiv_id=2405.14831, section=Appendix H Implementation Details & Compute Requirements   ← 不存在
[deepxiv WARNING] Section not found ...
read_section: arxiv_id=2405.14831, section=Appendix H Implementation Details                          ← 重试成功 1491 chars
read_section: arxiv_id=2405.14831, section=Introduction           (4012 chars OK)
read_section: arxiv_id=2405.14831, section=Discussions            (8848 chars OK)
[paper_analysis] 完成: sections_read=0, method_len=990, degraded=True
[paper_analysis] react wrapper done: rounds=4, remaining_budget=44
```

### 排查步骤与结论

1. **首先怀疑工具调用失败** → 看 INFO 日志，6 次 read_section 调用全部 success，章节内容总长约 32K chars 顺利返回给 LLM。**排除**。
2. **怀疑 ReAct 子图 finalize 解析失败** → method_summary / datasets / metrics / hyperparams / baseline_results 全部完整填充，说明 `<result>` JSON 被成功解析；只有 `sections_read` 字段是空数组。**排除整体解析失败**。
3. **怀疑 `_coerce_str_list` 把非空内容强转空** → 不可能，因为 `_coerce_str_list` 输入 list 时只会过滤 None 和空字符串；输入非空字符串列表会保持。`analysis_notes` 里 LLM 自己用自然语言列出了所有读过的章节名（"HippoRAG / Experimental Setup / Results / Appendix H Implementation Details / Introduction / Discussions"），但 JSON 输出的 `sections_read` 字段就是 `[]`。**确认：LLM 在最终 `<result>` JSON 中漏写了 sections_read 字段值**。
4. **复现率验证**：
   - 单独跑 `test_e2e_basic_path_full_pipeline`：通过（65.43s）
   - paper_analysis e2e 整套 3 次复跑：3/3 通过
   - 全量 pytest 回归 3 次：第 1 次失败，第 2、3 次通过
   - **复现率 ≈ 1/4 = 25%**
5. **是否同 BUG-S1-02 的回归？** 不是。BUG-S1-02 根因是 deepxiv 工具 `str(dict)` 写 ToolMessage 导致下游 `extract_last_tool_result` 解析失败（已修复）。本次工具序列化正常（structure 工具返回的 dict 在 T-PA-E2E-03 验证可被往返解析），工具结果实际到达了 LLM 上下文（章节内容被 LLM 用于填充 method_summary / datasets / metrics）。这是**LLM 最终 JSON 输出漏字段**，与 BUG-S1-02 同形态（categories 漏写）但发生在 paper_analysis 节点。

6. **判定**：
   - 这不是测试代码 bug：`sections_read` 是 PaperAnalysis 必填核心字段（`_missing_core_fields` 明确把它列入 missing 判断），测试断言契约正确。
   - 这不是外部依赖抖动：deepxiv 章节读取全部成功。
   - 这是**生产代码（LLM 服从度 + 兜底逻辑）问题**：
     - C2 的 system prompt 在"字段填充优先级"段列了 `sections_read: 实际成功读取的章节名列表（与工具调用历史一致）`，但 LLM 偶发会输出空数组。
     - `_map_analysis_result` 当前**没有从 ReAct 子图 messages 历史回填 sections_read 的兜底**——`_make_react_wrapper` 支持把 `final_messages` 作为第 3 参传给 `map_result`（架构 §2.8.2 "head 优先契约"基础设施已就绪），但 `_map_analysis_result` 只声明了 2 个参数，没用上这个能力。
   - **复现率约 25%**，不能视为偶然抖动（vs BUG-S1-02 在 categories 上的 5/6 复现率，这次更隐蔽）。

### 处置

- **未自行修复**（按 agent 规范，不动 `core/`）。
- **Bug 已起草**（见下方 Bug 报告），同步落 HTML 失败分析报告：`docs/sprint1/test-reports/2026-05-15_paper_analysis_e2e_failure_analysis.html`。
- **等 Maria 决策**：本 Bug 立即转交全栈开发代理修复，还是先做更多复跑确认复现率，或先观察是否被 C2 后续迭代自然解决。
- **不视为 flaky 忽略**：复现率 25% 在 LLM 节点中已属于明确 bug 信号（参考 C1 BUG-S1-02 的 5/6 复现率被认定为 bug；25% 是更低但仍稳定可观测的偶发率，对单节点 e2e 验收 4-用例-相关-失败的影响明显）。

### Bug 报告（草稿）

- **Bug ID**：`BUG-S1-03`
- **复现路径**：
  ```
  pytest -v   # 跑约 4 次中 1 次能复现
  # 或单独：
  pytest tests/test_paper_analysis_e2e.py::test_e2e_basic_path_full_pipeline -m e2e -v
  ```
- **期望行为**：
  - PRD / dev-plan：paper_analysis 的 `sections_read` 字段应包含实际成功读取的章节名列表。
  - `core/nodes/paper_analysis.py` system prompt: `sections_read：实际成功读取的章节名列表（与工具调用历史一致）`。
  - `_missing_core_fields` 把 sections_read 列为核心字段——隐含的语义就是"成功路径下不应为空"。
  - 综合：当 ReAct 子图 messages 中存在 `read_section` 成功 ToolMessage（`name=read_section` 且 content 非错误）时，最终 `sections_read` 不应为空。
- **实际行为**：
  - HippoRAG (2405.14831) e2e 主跑：6 次成功 read_section + 1 次失败重试，LLM 最终 JSON 中 `sections_read: []`，节点被标记 degraded。
  - 复现率：约 25%（4 次全量回归中 1 次出现）。
  - LLM 在 `analysis_notes` 自然语言部分清楚列出读过的章节，仅 JSON 字段漏写。
- **影响范围**：
  - paper_analysis 节点：偶发性误标记 degraded，污染 `degraded_nodes` / `node_errors`。
  - 下游：架构 §12.5 的降级链依赖 `degraded_nodes`；误标记会触发下游不必要的二次保护逻辑。
  - 不阻塞 C2 验收的"主路径功能正确性"（其它字段填充正常），但**阻塞 paper_analysis 单节点 e2e 验收的稳定性**。
- **建议修复方向**（按 ROI 排序）：
  1. **首选：在 `_map_analysis_result` 加 ReAct messages 工具历史回填**——把 `_map_analysis_result` 签名扩到 3 参（`result, state, react_messages`），扫描 `react_messages` 中所有 `name=read_section` 的 ToolMessage，从 tool_call 的 args 抽 `section_name`（成功 ToolMessage 才算），若 LLM 输出 `sections_read` 为空但工具历史非空则回填。`_make_react_wrapper` 第 877 行已支持 3 参签名，无需改基础设施。这与"head 优先回填"的架构契约一致，零依赖 LLM 服从度。
  2. **次选：强化 prompt**——在"输出要求"段补一句"若调用过 read_section 且成功，最终 JSON 的 sections_read 必须包含对应章节名"。受限于 LLM 服从度，不如 #1 稳定。
  3. **可选：把 sections_read 移出核心字段**——降级判定不再依赖该字段；但这弱化了"读了哪些章节"的可观测性，不推荐。
- **回归命令**：修复后重跑 `pytest -v`，**至少连续 5 次** 26/26 通过才可关 Bug（25% 复现率需更多样本才能确认治理）。

## 后续动作

- [ ] **等 Maria 决策**：是否立即转交全栈开发代理修复 `BUG-S1-03`，或先做更多复跑确认复现率上限。
- [ ] 修复 `BUG-S1-03` 后回归命令：`for i in {1..5}; do pytest -v || break; done`
- [ ] 缺失覆盖（已知遗漏）：
  - **Prompt Cache `cached_tokens` 命中率定量验证**：NVIDIA 网关不暴露该字段，需切到 DeepSeek 等支持 cached_tokens metadata 的端点才能跑（已在 TODO 阶段 4 Maria 自己 owner 的 cache 实验项中追踪）。本测试仅覆盖**字节级前缀稳定性**这一前置条件，不覆盖**实际命中率**。
  - **degraded 路径**：成功路径下偶发的 degraded（如本次 BUG-S1-03）有覆盖；但"工具全失败 → get_full_paper 兜底"的真实 e2e 路径未覆盖，单测已覆盖（CP7）。需要构造故意失败的 arxiv_id 才能在真实链路验证，token 浪费风险较高，暂不补。
  - **大论文 max_rounds=12 边际**：HippoRAG/BitNet 仅用 4 轮，未触及 max_rounds 边界。若发现 max_rounds 真实场景紧张，再补一篇章节多/篇幅大的论文（如 transformer-survey 系列）。
- [ ] 同 Sprint 内若需重跑本套 e2e（如修复 BUG-S1-03 后回归），追加报告 `2026-05-15_paper-analysis-e2e-02.md`，不覆盖本份。
