# 测试执行报告 - E3 独立验收 + E4 回归守门（sp3 适配 + Q-C 复验）+ L-B1-02 修复验收

- **日期**：2026-07-04
- **执行人**：@测试工程师代理
- **Sprint**：sprint4
- **触发原因**：任务 E4（CP-E4-1/2/3，AC-S4-05/14）+ E3 独立验收 + L-B1-02 修复独立验收
- **commit**：7c04a04（E3/E4/B1 修复均为工作区未 commit 改动，验收针对工作区现状）

## 执行范围
- 命令（最终验收轮）：
  - `.venv/bin/pytest tests/ -m "not e2e" -q`（全量回归）
  - `.venv/bin/pytest <sp3 六文件 + b2/e3/e4> -m "not e2e" -q` × 3（interrupt/幂等类连跑）
  - 临时探针 `/tmp/e3_probe.py`（E3 独立复核，已删除）
- 是否包含 e2e：**否**（未授权，不耗配额）。凭证来源 env：LLM_API_KEY / DEEPXIV_TOKEN（仅提及，未使用）

## 结果摘要
- **全量非 e2e 回归：1341 passed / 0 failed / 37 skipped / 43 deselected，57.7s**（基线 1119+，含本 Sprint 全部新增，零退化）
- sp3 六文件适配后：148 passed（适配前 35 failed / 113 passed）
- 新增 `test_sprint4_e4_regression_gate.py`：3 passed
- interrupt/重跑幂等类 167 条 × 3 轮：全绿，**0 flaky**
- 警告：1（langgraph 库级 `LangChainPendingDeprecationWarning`，非项目代码，挂账观察）

## 结论速览
| 项 | 判定 |
|---|---|
| CP-E4-1 sp3 35 条 mock 落点适配 | **PASS**（全部转绿，断言语义不降，无 E3 真回归） |
| CP-E4-2 execution 侧 Q-C 复验（AC-S4-14） | **PASS**（副作用恰 1 + 合并通道完整 + 3 次稳定） |
| CP-E4-3 全量回归 + 3 次连跑 | **PASS**（1341 全绿，0 flaky） |
| E3 独立验收 | **PASS**（附 1 项中危设计缺口挂账 L-E4-01 + 2 条低危注记） |
| L-B1-02 修复独立验收 | **PASS**（修复正确、翻转理由充分、缺口真被堵上） |

---

## 一、CP-E4-1：35 条 sp3 失败适配清单

**根因（全部一致）**：E3 把 execution() 步骤 1+2 换成 `_run_execution_agent` 内嵌子图后，
sp3 测试 patch 的 `execution_module.prepare_venv / run_in_venv` 不再被主函数直接调用；
且 sp3 各 `_base_state` 无 `llm_config_set` → 真实 `_run_execution_agent` 内 `KeyError`
→ 降级空结果集（`prep=None, [], rounds=0`）→ 分类恒为 DEPENDENCY，mock 剧本失效。
**基线确认**：适配前复跑六文件 = 35 failed / 113 passed，与 E3 开发代理移交清单逐条一致，
失败形态均为「降级路径污染」，**无 E3 真回归**。

**统一适配方案（helper 级，6 个文件各 1 处）**：`_patch_sandbox`（及 e2_reinforce 的
`_patch_sandbox_fail`）改为 patch `_run_execution_agent`，返回
`ExecAgentOutput(prep, [下一条 run], rounds_used=0, llm_calls=0)`。等价映射论证：

1. **副作用计数语义等价**：sp3 中每次 execution 进入 = 1 次 prepare + 1 次 run（六文件
   plan 恒为 1 step）；适配后每次 agent 调用同样计 `cnt["prepare"]+=1, cnt["run"]+=1`。
   所有「sandbox 副作用恰为 1/2」「resume 不重跑」「guard 命中零调用」断言**逐字保留**。
2. **rounds_used=0**：保持 sp3「仅 metrics 档 3 抽取扣减」的预算断言字节级不变
   （cp_c3_8/8b、cp_f1_3 系列）。E3 新增的子图 rounds 扣减**不在 sp3 文件职责内**，
   由 `test_sprint4_e3.py` CP-E3-1（5 条）+ 本次独立探针 A 专项覆盖，无覆盖真空。
3. **r9 特例（prep_raises）**：sp4 真实链路中 `prepare_environment` 工具捕获
   `SandboxCreationError` 转结构化错误 → 收集器无 prep → `prep=None + 空 run_results`，
   `_classify_execution` 步骤 0 同样归 DEPENDENCY 可修复——适配为 agent 返回空结果集，
   `cnt["run"]==0` 断言保留，分类/回边断言逐字不动。

**逐文件清单**（改动均仅为 mock helper，测试函数体零改动）：

| 文件 | 适配用例 | 断言语义 |
|---|---|---|
| test_sprint3_c3.py | cp_c3_2 / cp_c3_4 / cp_c3_6 / cp_c3_7×3 / cp_c3_8 / cp_c3_12 / cp_c3_13（9 条） | interrupt#2 三态 resume、commit 边界、fix_loop 自增、预算回写断言全保留 |
| test_sprint3_c3_reinforce.py | r1b / r2 / r4 / r9 / r10 / r10b / r11 / r12 / r14 / r15（10 条） | guard 边界、优先级、payload 形状、幂等断言全保留；r9 见特例注记 |
| test_sprint3_d1_reinforce.py | h1 / h2 / h2b / h3（4 条） | 真实路由 self-loop 成边 + 副作用==1/2 断言全保留 |
| test_sprint3_e2_reinforce.py | g1×5（5 条） | UI payload → 节点三态路由端到端断言全保留 |
| test_sprint3_e2e.py | e2e_1 / e2e_3×3 / e2e_5a / e2e_5b（6 条） | 真实 build_graph 主图断言全保留；**TestRealChainE2E._patch_sandbox_real 不适配**（sp4 工具体内仍调模块级 prepare_venv/run_in_venv，patch 依旧生效——已代码级核实） |
| test_sprint3_f1.py | cp_f1_3（1 条） | must-fix-2 预算回写断言逐字保留 |

**无一条弱化断言**；无一条失败被判定为 E3 真回归。

## 二、CP-E4-2：execution 侧 Q-C 复验（AC-S4-14）

新增 `tests/test_sprint4_e4_regression_gate.py`（3 条，全离线）：

1. `test_cp_e4_2_interrupt3_resume_sandbox_side_effect_exactly_once`（主证）：
   **真实 execution() 节点**（含 E3 七步收尾，区别于 CP-E2-5 只测 agent 层）+ 真实
   `create_react_subgraph` + 脚本 LLM（B2 replay 安全范式）+ `CountingRunner` mock
   run_in_venv。剧本：run(prep_data) → request_user_input → interrupt#3 暂停 →
   resume → run(train) → 收尾。断言：
   - 暂停时 prep_data 副作用 ==1、train 未执行；**resume 后 prep_data 仍 ==1**（工具历史不重放，B2 结论在真实节点成立）；
   - **合并通道完整**：`execution_result.logs` 含 step#0(prep_data，messages 回读补全) + step#1(train，收集器尾段)，且 `_merge_with_collector` 的 R-S4-10 WARNING 留痕；
   - E3 收尾闭环：merged 全 exit 0 + 档 1 指标 → B 档 success=True，预算扣减生效。
   → **未触发 E1 兜底通道的需要**（合并通道一次通过）。
2. `test_cp_e4_2_flow_stable_across_3_runs`：同剧本 3 次副作用计数零漂移。
3. `test_le401_characterization_credential_inline_retry_still_judged_failure`：见下方 L-E4-01。

## 三、CP-E4-3：全量回归 + 稳定性

- `pytest tests/ -m "not e2e"`：**1341 passed / 0 failed**（57.7s）。
- interrupt/重跑幂等类（sp3 六文件 + b2_interrupt3 + e3 + e4，167 条）连跑 3 次全绿，0 flaky，未触发升 5 次条件。

## 四、E3 独立验收（不信任开发自测）

### 4.1 diff 审读（git diff core/nodes/execution.py，16 hunks 逐一核对）
- **修复循环边界五件套零 hunk**：`_maybe_interrupt_or_return` / `_has_committed_result_for_round`
  函数体 / `_ROUTE_RETRY_CODING`/`_ROUTE_AWAIT_INTERRUPT` / guard 命中分支 / interrupt#2
  payload **键结构**均无改动（payload 仅 3 个值过 mask_value，键集合逐字 sp3）✅
- **扣减单点**：`_map_execution_result` 是唯一扣减点，`total_calls = rounds + metric_calls`
  单点 read-modify-write + INFO 日志；guard 命中路径直接构造 updates 不经扣减 ✅
- **credential 判定顺序**：`_CREDENTIAL_KEYWORDS` 与架构 §9.2 逐字一致（8 项），判定先于
  HARDWARE / DATA_MISSING / UNRESOLVED_RESOURCE；不进 AUTO_FIXABLE、映射 permanent ✅
- **mask 落点**：`_build_execution_result` logs 收口 mask + payload 3 个日志派生值 mask ✅
- **prep=None 分支**：无结果→DEPENDENCY（与 sp3 prepare 抛错同口径）、有结果→prep 中性，
  合理；sp3「prepare_venv 抛错 try/except」分支删除等价下沉到 E1 工具层 ✅
- 低危注记 ①：`_map_execution_result` 只消费 `agent_out.rounds_used`、忽略
  `agent_out.llm_calls`（当前契约二者恒等，若未来分叉会漏计——建议 E3 加一行契约断言或注释，非阻断）。
- 低危注记 ②：架构 §3.4 伪代码的 `dev_calls_delta` 形参被合并进 `react_rounds_used`
  （因 llm_calls==rounds），实现与架构伪代码有形参差异但语义等价，已在 docstring 说明。

### 4.2 独立探针（临时脚本，已删）
- 探针 A：rounds=5 单点扣减恰 5（独立于 e3 helper）PASS
- 探针 B：credential + hardware + data_missing + unresolved 四关键字叠加 → credential 胜出 PASS
- 探针 C：interrupt#2 payload 键集合逐字 sp3 PASS
- 探针 D：guard 命中零扣减 + agent 零调用 + 到达 interrupt PASS
- 探针 E：CREDENTIAL_REQUIRED 不进 core/state.py / core/errors.py / AUTO_FIXABLE PASS

### 4.3 test_sprint4_e3.py 假绿审计（26 条）
- `_patch_agent` mock 边界恰当（编排层 CP 用 mock agent，B 档谎报 CP-E3-3 用真实子图 + LiarLLM 端到端）；
- guard 用例以 `forbidden_agent` 抛 AssertionError 反证零调用（非仅计数）——强断言 ✅；
- 预算断言均为**精确等值**（非 `<=`），checkpoint values 级复核 ✅；
- secrets 隔离 fixture 齐全，无真实 .secrets 污染 ✅；
- **未发现假绿模式**。CP-E3-1~5 判定：全部 PASS。

### 4.4 E3 验收发现：L-E4-01（中危设计缺口，挂账待架构师/PM 裁决，非 E3 实现 bug）
**现象**（`test_le401_characterization_*` 真实子图 + 真实节点复现锚定）：credential 闭环
（架构 §9.1）中 agent 就地 `request_user_input` 拿到凭证 → 重试成功（exit 0 + 指标），
但 merged run_results 含 pre-interrupt 认证失败 run（exit 128）→ B 档「exit 全 0」判
failure → 分类 credential_required → interrupt#2 **再次以「缺少凭证」问询用户**。
**张力**：用户刚提供完凭证且重试已成功，凭证闭环无法单回合以 success 收尾，与 §9.1
「重试 → 成功」叙事及 system prompt 纪律 4「就地修正后重试」的价值相抵。同理适用于
所有「agent 就地重试成功」场景（非仅 credential）。
**定性**：E3 实现严格符合架构 §3.4/B 档判定的字面规格（判定只认全部真实 exit_code），
故不判 E3 FAIL；但这是「规格与 §9.1 叙事的冲突」，需架构师裁决判定口径（如按每条
命令的最终尝试判定）。AC-S4-14 断言本身（副作用恰 1 + 合并完整）在该场景同样成立。
**处置**：characterization 用例锚定现状 + docstring 注明翻转条件；不自行改生产代码。

## 五、L-B1-02 修复独立验收：PASS
- **修复正确性**：`interaction_tools.py` cache-hit 分支 `if is_sensitive:
  register_sensitive_value(cached)`——按调用方敏感语义补登记，位置在 return cached 前，
  正确；`register_sensitive_value` 对空串/None 有防护（不注册），无 mask_value 空串
  替换风险（已代码级核实 secrets_store L217-224）。
- **翻转理由充分性**：原锚定「cache-hit 不重复 register」依赖「mask 全集 = .secrets
  is_sensitive 项 ∪ 进程内 set」——当 .secrets 条目 `is_sensitive=False` 而本次调用
  `is_sensitive=True` 时两通道均不覆盖，缺口成立；翻转 docstring 完整记录了日期/原因/
  双通道并存结论 ✅。
- **缺口真被堵上**：新增 `test_lb102_fix_cache_hit_sensitivity_mismatch_masked` 先断言
  前置缺口成立（非敏感条目不进 mask 集）再断言修复后可脱敏——直证缺口场景 ✅。
  反向错配（条目敏感 + 调用非敏感）经 .secrets is_sensitive=True 通道覆盖，由既有
  翻转用例继续锁定 ✅。b1/b1_fix 相关 59 条全绿。

## 失败排查
无（适配后零失败；35 条基线失败已逐条归因为 mock 落点失效，非生产回归）。

## 执行事故记录（自省，非测试失败）
首轮基线确认误用 `pytest <六文件> -q`（未加 `-m "not e2e"`，且 pytest.ini 无 addopts
默认排除、conftest 从 .env 加载凭证）→ `test_sprint3_e2e.py::TestRealChainE2E` 的真实
链路 e2e 可能实际启动（后台运行约 10 分钟后被本人发现并 kill，无输出留存，实际消耗量
不可考，估计消耗了部分 LLM 调用与 deepxiv 配额）。**违反「真实 e2e 须 Maria 明确授权」
纪律，已即时终止并在此如实记录**；后续所有回归命令一律显式 `-m "not e2e"`。
建议（挂账）：给 pytest.ini 增加 `addopts = -m "not e2e"` 默认排除，从机制上杜绝误触。

## 后续动作
- L-E4-01：credential/就地重试成功的 B 档判定口径 → 待架构师/PM 裁决（characterization
  用例 `test_le401_*` 锚定现状，裁决后翻转）。
- 低危注记 ①②（llm_calls 契约断言、架构伪代码形参差异）→ 移交 E3 开发代理酌情补注释。
- pytest.ini addopts 默认排除 e2e → 建议主控裁决后落地。
- dev-plan CP-E3-1~5 / CP-E4-1~3 勾选 → 由主控统一收口（本任务边界不碰共享 docs 的
  dev-plan/TODO，仅落本报告）。
