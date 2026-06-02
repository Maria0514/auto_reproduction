# 测试执行报告 - b2-acceptance（resource_scout 节点独立验收）

- **日期**：2026-06-02 09:50（本地时区）
- **执行人**：@测试工程师代理
- **Sprint**：sprint2
- **触发原因**：阶段 B 任务 B2（`core/nodes/resource_scout.py`）由全栈开发代理标记完成后的独立验收（逐条复核 CP-B2-1~10，不轻信开发自报）
- **commit**：baef535（B2 产物在工作区未提交：`core/nodes/resource_scout.py` 新建、`core/nodes/__init__.py` 改动、`tests/test_sprint2_b2.py` 新建）

## 执行范围

- 命令：
  - `pytest tests/test_sprint2_b2.py -v`（B2 单测，补强前 18 / 补强后 34）
  - `pytest -q -m "not e2e" --ignore=tests/test_paper_intake.py`（非 e2e 核心回归，连跑 3 次）
  - 多个 python 探针脚本（独立复核 SCHEMA 对齐 / backfill 边界 / build_context 过滤 / 工具集组成 / AST 静态常量）
- 覆盖用例：`tests/test_sprint2_b2.py`（CP-B2-1~10 + aux 3 + 测试工程师补强 14 项，其中 1 个 parametrize×2）
- 是否包含 e2e：否（B2 真实 LLM e2e 留 E 阶段，本次按任务说明不跑真链路）

## 结果摘要

- 通过：B2 单测 34/34；非 e2e 核心回归 237/237（连跑 3 次稳定）
- 失败：0
- 跳过：0（17 个 e2e deselected，非跳过）
- 警告：1（langgraph 库级预存 `LangChainPendingDeprecationWarning`，sp1 即有，与 B2 无关）
- 总耗时：B2 单测 ~0.72s；核心回归每次 ~2.36s

## CP-B2-1~10 逐条独立复核结果

| CP | 结论 | 独立验证方式与证据 |
|----|------|--------------------|
| CP-B2-1 | PASS | `resource_scout` 是 `_make_react_wrapper` 产物，`__name__ == "react_wrapper_resource_scout"`（Read 源码 L565 + 探针断言） |
| CP-B2-2 | PASS | 探针实证：schema props ∖ {search_log} == ResourceInfo 4 字段（repos/selected_repo/external_resources/resource_strategy）；required == {repos, selected_repo, resource_strategy} |
| CP-B2-3 | PASS | github_url 路径 mock 子图 → use_repo + selected_repo==repos[0] + source=github_url + 不 degraded |
| CP-B2-4 | PASS | 无 github_url + PwC 路径 → source=pwc 透传正确（mock 驱动，真实链路区分留 E） |
| CP-B2-5 | PASS | 全失败 → from_scratch + repos=[] + selected_repo=None + degraded_nodes 含 resource_scout + degraded NodeError + **不抛致命异常** |
| CP-B2-6 | PASS | LLM 漏写 repos + 1 个成功 clone ToolMessage → 回填 1 RepoInfo（quality 默认 0.5）；只失败时不回填 + WARNING；无 ToolMessage 不打噪声；LLM 已给 repos 时不回填 |
| CP-B2-7 | PASS | `inspect.signature(_map_resource_scout_result).parameters == [result, state, react_messages]`；react_base L880 经 inspect 检测 ≥3 位置参自动透传 final_messages |
| CP-B2-8 | PASS | quality 全 <0.3 → selected_repo==best 候选 + analysis_notes 含 [QUALITY_WARN] + WARNING 日志 |
| CP-B2-9 | PASS | force_finish（result=None, rounds=10）→ from_scratch 不抛错 + max_rounds=10 透传子图；有工具成功候选时仍能回填 best |
| CP-B2-10 | PASS | `_RESOURCE_SCOUT_SYSTEM_PROMPT_BODY` AST 实证为 module-level `ast.Constant` str（len 2402，无 f-string/Call/BinOp）；不同论文 context 下 `_build_resource_scout_system_prompt` 返回值字节级一致；主体不含任何论文 id/title |

## SCHEMA 对齐裁定

**裁定：对齐正确且映射合理（PASS）。**

- `ResourceInfo` TypedDict 实为 **4 字段**：`repos` / `selected_repo` / `external_resources` / `resource_strategy`（Read `core/state.py` L107-112 实证）。
- `RESOURCE_SCOUT_SCHEMA.properties` 为 5 项 = 上述 4 字段 + `search_log`。
- 开发自报"schema 去掉 search_log 后与 ResourceInfo 4 字段相等"——**探针实证成立**：`props - {"search_log"} == set(ResourceInfo.__annotations__)`。
- `search_log` 作为 agent 自报告字段（检索过程/判定理由）由 `_append_search_log_note` 透明落到 `analysis_notes`（人类审核），**不进 ResourceInfo Schema**，`additionalProperties: True` 容纳之。映射合理，符合 architecture §2.3.1 注释与 dev-plan L802 设计意图。
- `required == [repos, selected_repo, resource_strategy]`，与 §2.3.1 / dev-plan L804 一致。

## 各验收重点裁定

1. **搜索优先级链降级（github_url→PwC→web_search→from_scratch）**：prompt 主体完整描述四级链 + 工具名（补强 test_acc_prompt_body_describes_priority_chain 锚定）；全失败降级路径 from_scratch/repos=[]/selected_repo=None/degraded 标记/不抛异常实证正确（CP-B2-5 + aux）。真实链路逐级真实降级留 E 阶段。
2. **_backfill_repos_from_tools 回填正确性**：探针逐项实证——`tool ... raised` / `Error in` 前缀跳过、`success==False` 跳过、缺 local_path 跳过、截断 JSON 后缀恢复、重复 URL 去重、混合成功/失败只回填成功者、quality 默认 0.5、无成功记录打 WARNING、无 ToolMessage 不打噪声、LLM 已给 repos 不回填。全部正确。
3. **BUG-S1-02/03 治理范式合规**：节点不自行 `str()` 化工具结果（复用 A5/A6 `_serialize_tool_result` 合法 JSON）；`_map` 3 参签名 + backfill WARNING 非静默——grep + 探针实证合规。
4. **Prompt Cache 前缀治理**：`_RESOURCE_SCOUT_SYSTEM_PROMPT_BODY` AST 实证 module-level 静态 str 常量、主体无论文级动态值、跨论文字节级一致——满足 R-PC4 范式。
5. **工具集组成**：探针捕获 get_tools 实际返回 **6 工具**（web_search/search_papers/get_paper_brief/search_pwc/git_clone_and_analyze/check_url_reachable_tool），max_rounds=10，schema title=ResourceInfo——按 dev-plan B2 + 架构 §4.6/§2.3.5 落地（含 search_pwc）。

## 补强用例（测试工程师新增 14 项 → B2 单测 18→34）

- `test_acc_tool_set_composition_six_tools`：工具集 6 工具含 search_pwc + wrapper 参数透传
- `test_acc_prompt_body_describes_priority_chain`：prompt 主体覆盖四级降级链 + 工具名 + quality_score
- `test_acc_context_filters_empty_and_keeps_english_facts` / `test_acc_context_handles_none_inputs`：`_format_resource_scout_context` 英文事实层过滤（None/空值/中文备份字段不泄漏）+ None 输入不抛错
- `test_acc_wrapper_passes_messages_to_backfill`：BUG-S1-03 范式**真实经 wrapper**透传 final_messages 到 backfill（非直接调 _map）
- `test_acc_selected_repo_picks_best_when_missing` / `test_acc_invalid_strategy_falls_back`：selected_repo 漏写补 best、无效策略 fallback
- `test_acc_degraded_nodes_dedup_preserves_upstream`：degraded_nodes 去重 + 保留上游 NodeError（不污染）
- `test_acc_backfill_recovers_truncated_json` / `test_acc_backfill_skips_failure_prefixes`(×2) / `test_acc_backfill_mixed_success_failure`：截断恢复 / 失败前缀跳过 + WARNING / 混合记录
- `test_acc_external_resources_malformed_filtered`：external_resources 畸形条目过滤不抛错
- `test_acc_search_log_appended_to_notes`：search_log 透明落 analysis_notes
- `test_acc_quality_boundary_exactly_threshold_no_warn`：quality 恰好 0.3 不触发 QUALITY_WARN（严格 `<`）
- `test_acc_quality_score_clamped`：quality_score 钳制 [0,1]

## 失败排查

本轮唯一失败为**测试代码自身 bug**（非生产代码 bug）：

- 用例：`test_acc_degraded_nodes_dedup_preserves_upstream`（tests/test_sprint2_b2.py）
- 失败类型：测试代码 bug
- 关键报错：`KeyError: 'node'`（构造上游 NodeError 时用了臆造键 `node`/`message`/`detail`）
- 排查与结论：探针 `make_node_error` 实际 NodeError 键为 `node_name`/`error_type`/`error_message`/`error_detail`/`timestamp`/`retry_count`/`resolved`。我的预置 dict 用错键，与生产代码无关。
- 处置：自行修复——改用 `make_node_error("paper_analysis", "degraded", "x", None)` 构造上游条目 + 断言键改 `node_name`。修复后 34/34 通过。

## 验收结论

**PASS（通过）。** CP-B2-1~10 逐条独立复核全部命中；SCHEMA 与 ResourceInfo 4 字段严格对齐且 search_log 映射合理；搜索优先级链降级 / backfill / 治理范式 / Prompt Cache 前缀治理 / 工具集组成全部实证正确；**零生产代码 BUG**；非 e2e 核心回归连跑 3 次 237/237 稳定、0 失败 0 跳过 0 抖动、sp1+sp2 基线零退化。

## 后续动作 / 遗留（均非阻断）

- B2 真实 LLM e2e（搜索优先级链真实逐级降级 + 真实 git clone + PwC 真实接入）留 E 阶段；工具层真实网络 e2e 同 A5/A6 留 E1。本轮凭证未就绪不伪造。
- 架构 §2.3.2 prompt 示例工具集只列 5 个（漏 search_pwc），而 dev-plan B2 + §4.6/§2.3.5 + L1388 权威要求含 search_pwc——实现按权威要求落地 6 工具，**文档轻微不一致已由 §4.6 决策化解**，建议后续顺手把 §2.3.2 示例补 search_pwc（不阻塞，无需 Maria 决策）。
- `_format_resource_scout_context` 偏离架构 §2.3.2 字面 `{"paper_meta":..., "paper_analysis":...}` 嵌套 dict，改为英文事实层扁平化挑字段——**判定为合理改进**（对齐 PRD §4.7.5 + Prompt Cache 稳定 + 与 sp1 paper_intake/paper_analysis 同款），非偏差。
- B2 产物（resource_scout.py / __init__.py / test_sprint2_b2.py）仍在工作区未 git commit（沿用 B1 遗留状态）。
- 复现 L-A3-02：pytest.ini markers 注释 `--run-e2e` 与凭证驱动实现不符（sp1 既有，与 B2 无关）。
