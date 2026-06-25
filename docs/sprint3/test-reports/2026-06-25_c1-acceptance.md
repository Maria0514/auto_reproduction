# 测试执行报告 - c1-acceptance

- **日期**：2026-06-25 02:00（本地时区）
- **执行人**：@测试工程师代理
- **Sprint**：sprint3
- **触发原因**：C1（coding 节点真实现 `core/nodes/coding.py`，S3-02）独立验收 + 边界补强
- **commit**：41f1cc4（C1 产物未提交，working tree 内验收）

## 最终裁决：PASS

## 执行范围
- 命令：
  - `.venv/bin/pytest tests/test_sprint3_c1.py -q`（开发 9 用例 + 补强 16 用例）
  - `.venv/bin/pytest -q -m "not e2e" --ignore=tests/test_paper_intake.py`（全量非 e2e 回归，连跑 3 次）
- 覆盖用例：`tests/test_sprint3_c1.py`（CP-C1-1~7 + 16 补强）
- 是否包含 e2e：否（按任务红线，coding 真实 LLM e2e 留 F 阶段）

## 结果摘要
- 通过：690（全量非 e2e；含 C1 的 25 用例）
- 失败：0
- 跳过：25（**全部为 D3/D4 UI 层 shadcn 迁移用例**——AppTest 看不到 iframe，文本断言已迁 e2e；与 C1 完全无关，为 UI 阶段既有 skip 基线。C1 自身 0 skip）
- 警告：1（langgraph 库级预存 `LangChainPendingDeprecationWarning`，sp1 即有，与 C1 无关）
- 总耗时：3 次连跑 122.95s / 122.24s / 121.02s

## CP-C1-1~7 逐条独立复核结论

| CP | 结论 | 复核要点 |
|----|------|---------|
| CP-C1-1 | PASS | `from core.nodes.coding import coding` 可导入；`inspect.signature(coding).parameters == ["state"]`（wrapper 产物）；CODING_OUTPUT_SCHEMA.title=="CodingResult" |
| CP-C1-2 | PASS | mock LLM 首轮经真实 B2 write 工具写出 run.py；返回 dict 含 `code_output_dir`/`current_step="coding"`，文件真实落盘，未标 degraded |
| CP-C1-3 | PASS | 修复回合（exec_result 非空 + fix_loop_count=2）注入 `fix_round`/`last_error_summary`（含 error_category=import + stderr 尾部含 ModuleNotFoundError），首轮 payload 无修复字段；主体含"修复回合模式" |
| CP-C1-4 | PASS | `_map_coding_result` 3 参签名 `(result, state, react_messages)`（inspect 实证）；ReAct 失败走 read-modify-write，保留上游 + append + **返回新列表对象**（`ne is not state["node_errors"]`），打 WARNING |
| CP-C1-5 | PASS | map_result 返回 dict 无 `fix_loop_count`（must-fix-2）；无 `retry_budget_remaining`；端到端经 wrapper 扣减后 <50（wrapper 写，非 map_result 写，must-fix-2） |
| CP-C1-6 | PASS | 主体常量无 `2409.05591`/`arxiv_id=`/`paper_meta`/论文标题；两份不同 context 渲染 SystemMessage 去尾部段落后主体字节一致，且 == 常量 |
| CP-C1-7 | PASS | B2 write/list 工具 ToolMessage 为合法 JSON（`json.loads` 成功，无 `'success'` 单引号 repr）；`_has_written_any_file` 正确识别真实 write ToolMessage |

## must-fix 守住情况
- **must-fix-1（read-modify-write）**：守住。`node_errors`/`degraded_nodes` 均 `list(state.get(...))` 拷贝→append→return 新对象，不原地改 state。**多回合实证**：coding 连续两轮失败，`degraded_nodes` 用 `if NODE_NAME not in` 防重复（始终 `['upstream','coding']`，coding 仅一次）；`node_errors` 每轮追加一条（2→3，设计预期为每次失败记一条 NodeError，非去重）；上游条目全程保留。
- **must-fix-2（不写 fix_loop_count / 不覆盖 retry_budget_remaining）**：守住。map_result 返回 dict 无 `fix_loop_count`；`retry_budget_remaining` 由 `_make_react_wrapper` 在 react_base.py L894 `setdefault` 自动回写，map_result 不返回该键，端到端实测扣减后 <50。

## 重点核验项实证结果
- **修复回合反馈裁剪**：`_digest_execution_feedback` 取 stderr **尾部** ≤2000 字符（构造 6000+ 字符 logs，注入的含末尾 `FATAL_AT_END_OF_LOG` 错误栈、不含头部 `HEAD_NOISE`）；errors 全部注入；从 `errors[0]` 解析 `[error_category=...]` 前缀；无前缀→None；空 errors / 非 str logs（list）均 str() 兜底不抛；不注入完整 `logs` 字段。防 context 撑爆。
- **首轮 vs 修复回合分流**：判定 `exec_result and fix_count > 0`。边界 A（exec_result 非空 + fix_loop_count==0）→ **首轮**（无 fix_round）；边界 B（fix_loop_count>0 + exec_result=None）→ **首轮**。与架构 §2.2.2 一致。
- **`<METRICS>` 约定**：主体 prompt 含 `<METRICS>{...}</METRICS>` 入口脚本指标打印要求 + `<METRICS>{}</METRICS>` 空指标兜底约定（R-S3-05 缓解，C3 解析依赖此约定）。
- **`_has_written_any_file` 判定矩阵（7 路径全验）**：只 read/list→False；write 失败 success=false→False；write 成功→True；框架异常文案 `Error in ...`→False；混合失败+成功→True；None/空→False；multimodal list content 含成功 JSON→True。判定精确，无误判 degraded 或漏判。

## code_output_dir 集成约定确认（C3/D 依赖，务必对齐）
- 解析逻辑（`_resolve_code_output_dir`）：state 已有 `code_output_dir` → 直接复用；否则按 `workspace_dir/<thread>/code` 新建（`<thread>` 取 `paper_meta.arxiv_id`，缺失回退 `"task"`）。`map_result` 无 RunnableConfig 拿不到真实 thread_id，用 arxiv_id 作稳定代理（架构示例写 `<thread>`）。
- **幂等性实证（正常流程）**：首轮 `map_result` 写入的是 **resolve 后绝对路径**（落库）；修复回合从 state 读回同值，两轮字节一致；`build_context` 修复回合也复用同目录。正常流程下幂等成立（map_result 是唯一写入点，首轮已 resolve）。
- **⚠️ C3 集成约定**：**execution（C3）必须直接读 `state["code_output_dir"]` 作 work_dir，不要自己拼 `workspace/<thread>/code` 目录**。否则若 C3 自拼目录而 coding 用的是 arxiv_id 代理 thread，二者可能不一致导致 execution 找不到 coding 写的代码。coding 已把权威目录写进 state，C3 直接取即可。
- 轻微瑕疵（非 BUG，记录供 C3 知悉）：`_resolve_code_output_dir` 对 state 已有值是**原样返回不再 resolve**，首轮新建才 resolve。正常流程无影响（首轮已 resolve 落库）；仅当外部直接塞入未 resolve 的 code_output_dir 才会原样透传——真实流程不会发生。

## 失败排查
无失败。

## 补强用例（16 条，追加至 `tests/test_sprint3_c1.py`，未破坏开发既有 9 条）
1. 分流边界：exec_result 非空 + fix=0 → 首轮
2. 分流边界：fix>0 + exec_result=None → 首轮
3. code_output_dir 端到端幂等（首轮 resolve → 修复回合复用）
4. resolve 回退：workspace 缺失回退 WORKSPACE_DIR + arxiv 缺失 thread 回退 task
5-10. `_has_written_any_file` 判定矩阵 6 条（read/list、write 失败、框架 Error in、混合、None/空、multimodal list）
11. must-fix-1 多回合 degraded 不重复累加 + node_errors 每轮追加 + 上游保留
12. `<METRICS>` 约定 + 修复回合模式 prompt 入口
13-15. digest 裁剪尾部 / 无前缀+空 errors / 非 str logs 兜底
16. map_result result 非 dict 短路判 degraded

## 稳定性
全量非 e2e 回归 3 次连跑 **690 passed / 0 failed / 0 flaky**（122.95s / 122.24s / 121.02s），25 skipped 稳定恒为 D3/D4 UI 既有 skip。

## 后续动作
- C3（execution）开发时务必落实「直接读 `state["code_output_dir"]`」集成约定（见上）。
- coding 真实 LLM e2e（含 <METRICS> 服从度、修复回合真实裁剪注入、Prompt Cache 真实链路字节级）留 F 阶段凭证就绪后补跑。
- 遗留 L-A3-01（test_paper_intake.py main 风格）/ L-A3-02（pytest.ini markers 注释偏差）仍未处理，与 C1 无关。

## 结论
C1 PASS。CP-C1-1~7 全部独立复核命中，must-fix-1/2 守住，修复回合裁剪与首轮/修复分流符合架构 §2.2.2，code_output_dir 幂等正常流程成立。**零生产 BUG**。补强 16 用例（C1 文件共 25），全量回归 690 passed 0 failed，3 次连跑 0 flaky。
