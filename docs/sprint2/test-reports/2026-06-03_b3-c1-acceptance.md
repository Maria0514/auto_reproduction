# 测试执行报告 - b3-c1-acceptance

- **日期**：2026-06-03 （本地时区）
- **执行人**：@测试工程师代理
- **Sprint**：sprint2
- **触发原因**：B3（planning 节点）+ C1（graph 升级）+ analysis_notes 通道修复 独立验收 + 深化测试
- **commit**：1902d66（B3/C1/state.py 改动尚在工作区未 commit）

## 执行范围
- 命令：
  - `pytest tests/test_sprint2_b3.py tests/test_sprint2_c1.py -q`（深化后）
  - `pytest -q -m "not e2e" --ignore=tests/test_paper_intake.py`（核心回归，连跑 3 次）
  - 单用例 / 乱序独立性抽查
- 覆盖用例：
  - CP-B3-1~12（dev 22 用例）+ 测试工程师补强 13 用例 = `tests/test_sprint2_b3.py` 共 **35 项**
  - CP-C1-1~10（dev 19 用例）+ 测试工程师补强 6 用例 = `tests/test_sprint2_c1.py` 共 **25 项**
- 是否包含 e2e：**否**。`config.get_llm_api_key()` 与 `config.get_deepxiv_token()` 均返回 False（conftest 已从 .env 加载，凭证未就绪），真实 LLM + interrupt resume graph e2e **明确跳过不伪造**，未新建 `test_sprint2_c1_e2e.py`（建之亦全 skip，无验收价值）。E 阶段凭证就绪后补跑。

## 结果摘要
- 通过：B3+C1 深化合计 **60 passed**；核心非 e2e 回归 **324 passed**（基线 305 + 本次新增 19 深化用例）
- 失败：0
- 跳过：0（非 e2e 集），e2e 集默认 deselect 24 项（凭证缺失，符合预期）
- 警告：1（langgraph 库级 `LangChainPendingDeprecationWarning`，sp1 即存，与本次无关）
- 总耗时：核心回归每次 ~2.83s（3 次：2.83 / 2.84 / 2.82s）；B3+C1 ~1.1s

## 逐条独立复核结论（不轻信开发自报）

### B3 CP-B3-1~12 — 全部命中
- CP-B3-1 `planning.__name__=="planning"`、签名 `(state)`，手写非 wrapper ✔（Read 源码 L492 + inspect）
- CP-B3-2 `_planning_react.__name__=="react_wrapper_planning"`，经 `_make_react_wrapper(node_name="planning",...)` 生成（L370）✔
- CP-B3-3 approve → `approved=True` + `current_step=="planning"` ✔
- CP-B3-4 revise → `_planning_user_feedback` / count+1 / 不返回 reproduction_plan ✔
- CP-B3-5 连续 6 次 revise 计数 1→6 单调、不强制 approve、notes 无 revise_limit ✔
- CP-B3-6 switch_repo → selected_repo.url 切换 + 与 revise 共享计数 ✔
- CP-B3-7 code_only → `ExecutionMode.CODE_ONLY` + approved=True ✔
- CP-B3-8 cancel → `current_step=="cancelled_by_user"` + `[CANCELLED]`、approved 不为 True ✔
- CP-B3-9 非法 payload（`{foo}`/None/str/int）→ `_finalize_approve(invalid_resume_payload)` 兜底；未知 decision → `unknown_decision:` 兜底 ✔
- CP-B3-10 **AST 实证**：`_PLANNING_SYSTEM_PROMPT_BODY` 是 module-level 纯 `ast.Constant` 字符串（无 JoinedStr/FormattedValue/BinOp/Call），跨论文 `_build_planning_system_prompt` 字节级一致（len=1558, md5=bd2abd79...），主体无 arxiv_id/format 痕迹 ✔（R-PC4 满足）
- CP-B3-11 ReAct 子图抛 LLMError → 最简版 plan（code_strategy=from_scratch）+ degraded 标记 + 仍触发 interrupt ✔
- CP-B3-12 `_map_planning_result` 签名 `["result","state","react_messages"]` 3 参（治理范式）✔

### C1 CP-C1-1~10 — 全部命中
- CP-C1-1 `build_graph()` 返回 `CompiledStateGraph` ✔
- CP-C1-2 7 业务节点集合精确匹配 ✔
- CP-C1-3/4/5 paper_intake/paper_analysis/resource_scout 是 ReAct wrapper、planning 手写 ✔
- CP-C1-6 coding/execution/reporting 返回 `{}`（等价 pass-through；graph.py 用命名函数而非 `_passthrough` 直接注册，但行为等价并由用例实证）✔
- CP-C1-7 `_route_after_planning` 3 路：cancelled→end / approved→next / 其它(含 None plan/approved=False/空)→self；**cancel 优先级高于 approved** 已参数化覆盖 ✔
- CP-C1-8 spy `StateGraph.compile` 实证未传 `interrupt_before`/`interrupt_after` ✔
- CP-C1-9 编译图 invoke 跑到 planning **自然暂停**（`__interrupt__` in out + `snap.next==("planning",)`）；resume approve→next→END / revise→self-loop 再暂停 ✔
- CP-C1-10 cancel → 路由 END、coding spy 未被调用、`[CANCELLED]` 持久化 ✔

### analysis_notes 通道修复 — 编译图层面实证生效（核心关注点）
- `core/state.py` L187 GlobalState 新增 `analysis_notes: str` 通道 + L272 `create_initial_state` 默认 `""`（Read 实证 + `git diff` 实证为纯追加）
- **新增编译图实证用例**（C1 深化）：
  - `test_analysis_notes_cancel_survives_compiled_graph`：cancel 顶层写入经 MemorySaver 合并后 `[CANCELLED]` 存活于 `snap.values` + invoke 返回值
  - `test_analysis_notes_fallback_survives_compiled_graph`：`_finalize_approve` 兜底 `[PLANNING_FALLBACK]` 编译图层面存活
  - `test_analysis_notes_survives_sqlite_reload`：**真实 SqliteSaver（tmp_path 文件）落盘 + 全新 saver 实例回读** 后 `[CANCELLED]` 仍在——双重保证（通道声明 + 持久化）端到端实证
  - B2 当初未暴露此 BUG 正因单测只驱动 `_map_*` 返回 dict、不走编译图；本次补强直击编译图合并语义，证明修复生效。

## 深化补强用例清单（19 个，0 重复造轮子，复用 dev FakeSubgraph/patch helper）

**B3（13 个）**：_switch_repo 命中已有候选复用 source / 空 URL 保持 None / 新 URL 建 user_switch / 非法 strategy 归一化 use_repo / **合法 from_scratch 选中后不翻转（契约观察）** / _map 脏类型补齐（steps 含字符串+None、data_prep 是 str、env 非 dict、deliverables 是 str）/ 非法 strategy 有 repo 时 use_repo / _finalize_approve 兜底 notes 累加+顺序 / clean path 不写 notes / 不原地修改入参 plan / cancel notes 累加+顺序 / switch_repo after react failure / revise 缺 user_feedback 落空串。

**C1（6 个）**：analysis_notes cancel 编译图存活 / fallback 编译图存活 / **SqliteSaver 回读存活** / revise 计数跨 self-loop 持久递增 1→2→3（self-loop 边真实生效）/ switch_repo resource_info 持久化后再 approve 收尾 / 3 路优先级编译图实证（cancelled 覆盖 approved）。

## 失败排查
1 个失败已在补强过程内自查解决，**判定为测试代码用例预期错误，非生产 BUG**：
- 用例：`test_switch_repo_invalid_existing_strategy_normalized_to_use_repo`（原命名）
- 失败类型：测试代码 bug（我对契约的初始假设过强）
- 报错：`assert 'from_scratch' == 'use_repo'`
- 排查与结论：Read `_switch_selected_repo` L446-450，归一化条件是 `strategy not in _VALID_STRATEGIES`。`from_scratch` 本身是合法值（在 `_VALID_STRATEGIES` 中），故选中仓库后**不**被翻转为 use_repo——归一化仅兜底"非法字符串值"，不做"语义上不再合理"的翻转。dev-plan/架构未要求 switch 工具强翻合法值（strategy 重判由下游 `_build_reproduction_plan` 按 selected_repo 存在性完成）。故此为实际契约语义，非 BUG。
- 处置：自行修复——拆为两个用例：`test_switch_repo_illegal_strategy_normalized_to_use_repo`（非法值 garbage_value → use_repo，正向覆盖归一化）+ `test_switch_repo_legal_from_scratch_kept_when_selected`（合法 from_scratch 保留，固化契约观察）。重跑全绿。

## 验收结论
- **B3 + C1 + analysis_notes 通道修复：PASS**
- **零生产 BUG**
- 稳定性：核心非 e2e 回归 3 次连跑 324/324、0 失败 0 跳过 0 抖动；关键有状态用例单跑 + 乱序独立性抽查通过（每用例独立 thread_id + 独立 saver，无序依赖）
- sp1 + sp2 零退化（基线 305 → 324，仅本次 +19 深化用例）
- sp1 `tests/test_graph_e2e.py` 整体 skip **理由成立**：sp1 D1 e2e 假设线性跑到底 + planning 占位，与 C1 后 planning interrupt 自然暂停语义冲突，linear invoke 必在 planning 停住，原断言必失败；该套件另有 `skip_if_no_creds`，凭证缺失本就会跳，**skip 不掩盖任何回归**（C1 自测 + 本次深化已用编译图 resume 流程覆盖等价的真实暂停/恢复链路，仅以 mock 节点替代真实 LLM）。

## 后续动作
- 真实 LLM + interrupt resume graph e2e：凭证（LLM_API_KEY + DEEPXIV_TOKEN）就绪后补跑（E 阶段），届时新建 `tests/test_sprint2_c1_e2e.py`，沿用 B2 e2e 风格（真实 interrupt 暂停 + Command(resume) + 3 路决策）。
- sp1 `test_graph_e2e.py` 重写为 resume 流程 graph e2e（TODO `[SP1-E2E-OBSOLETE]`，本次不重写）。
- B3/C1/state.py 改动尚在工作区，待 commit。
