# 测试执行报告 - C/E/F 三线并行开发独立验收（C1+C2 / E1+E2 / F1）

- **日期**：2026-07-04 05:06（本地时区）
- **执行人**：@测试工程师代理
- **Sprint**：sprint4
- **触发原因**：三线并行开发完成（工作区未 commit），主控委托独立验收——不信任开发自测，逐文件 diff 审读 + 一次性独立探针复核 + 假绿审计 + 全量回归
- **commit**：c8fb634（三线改动均在工作区未 commit）

## 执行范围

- 命令：
  - `.venv/bin/pytest tests/ -m "not e2e" -q`（全量非 e2e 回归）
  - `.venv/bin/pytest tests/test_sprint4_{c1,c2,e1,e2,f1}.py -q` × 3（新增用例稳定性连跑）
  - 独立探针脚本（16 项，一次性，已删除；内容见"独立探针复核"）
- 覆盖用例：三线新增 5 个测试文件共 73 条 + 全库既有用例
- 是否包含 e2e：否。全离线（真实子进程仅限 run_command smoke/超时探针，零 LLM / 零 deepxiv 配额）

## 结果摘要

- 全量非 e2e 回归：**1311 passed / 37 skipped / 0 failed**（57.9s）——与预期基线完全一致（B2 后基线 1238 + 三线新增 73 = 1311，零退化）
- 三线新增用例连跑 3 次：**73 passed × 3，0 flaky**
- 独立探针：**16/16 PASS**
- 警告：1（langgraph 库级预存 `LangChainPendingDeprecationWarning`，与本次改动无关，B2 报告已记录）

## 验收判定

| 线 | 判定 | 说明 |
|---|---|---|
| **C 线（C1+C2）** | **PASS** | 全部 CP 独立复核通过，Q-B1 红线机制面成立 |
| **E 线（E1+E2）** | **PASS** | 全部 CP 独立复核通过；execution() 主函数经 diff 证实零 hunk |
| **F 线（F1）** | **PASS-with-notes** | CP-F1-1/2 通过；CP-F1-3（`streamlit run` 手动 happy path 走查）未执行，遗留至 F/G 阶段真实链路验证 |

## 逐检查点独立复核结论

### C1（`core/tools/run_command_tool.py`）

| CP | 判定 | 独立复核依据 |
|---|---|---|
| CP-C1-1 合法 JSON / sort_keys / ensure_ascii=False | PASS | 代码审读 L116-127 + 用例 3 条 + 探针 P3 |
| CP-C1-2 越界/解析失败/空命令结构化错误 | PASS | 探针 P2 独立证实：越界 → error JSON + WARNING + `_run_subprocess` 0 次调用（spy） |
| CP-C1-3 超时护栏杀子树 | PASS | 探针 P11：真实 sleep 20s 子进程 + 1s 超时，实测 1.0s 返回 `timed_out=true`（AC-S4-02） |
| CP-C1-4 stdout/stderr 脱敏 | PASS | 探针 P1：注册 token 后真实子进程回显 token，返回 JSON 全文无明文、双通道 `****` |
| CP-C1-5 **Q-B1 红线 3** 无 success/metrics 语义键 | PASS | 探针 P3：返回结构恰 `{exit_code, stdout_tail, stderr_tail, timed_out, truncated}` 5 键；错误分支恰 `{error, exit_code}` 2 键。B 档判定无从消费——机制面成立 |

补充审读结论：系统解释器直跑 argv（不共用 venv，§5 结论）√；`_require_within_workspace` / `_run_subprocess` 零改动复用 √；docstring 零动态变量（两工厂实例 description 字节一致有用例锚定）√；config 常量运行期动态读取（可测性正确）√。

### C2（`core/nodes/coding.py` 挂载 + prompt 守门 + Q-C 复验）

| CP | 判定 | 独立复核依据 |
|---|---|---|
| CP-C2-1 工具集恰 7 个 + wrapper 形态不变 | PASS | 探针 P4 独立跑真实 `_get_coding_tools`（仅 mock deepxiv 网络工厂）：7 工具名称集合精确匹配；diff 证实 wrapper（L436 一带）零 hunk、`_map_coding_result` 签名不动 |
| CP-C2-2 prompt 主体字节级一致 + 新增段进稳定前缀 | PASS | 代码审读：新增两段（run_command 边界 + request_user_input 纪律）为纯字面常量、零动态变量；用例沿用 CP-F3-1 断言（两 context 去尾部后 == 常量） |
| CP-C2-3 coding 真实 wrapper Q-C 复验（AC-S4-14 coding 侧） | PASS | 用例审计：真实 coding wrapper + InMemorySaver 父图 + 脚本 LLM（B2 replay 安全范式），write → interrupt#3 → resume 后**双通道**（模块级计数 + 磁盘 append）断言副作用恰 1、文件内容正确、map_result 契约（code_output_dir / 不标 degraded / 预算扣减）逐项断言；连跑 3 次锚定 |
| CP-C2-4 凭证注入链路 | PASS | 用例：`.secrets` 含 git 凭证 → 工厂 spy 断言 extra_env 含 GIT_ASKPASS（token 只进 0700 脚本、不进 env 值）+ GIT_TERMINAL_PROMPT=0；无凭证时恰 `{"GIT_TERMINAL_PROMPT": "0"}` |
| CP-C2-5 coding 既有测试零退化 | PASS | 全量回归 1311 全绿（含 sp3 c1 / c1_fix 系列） |

B2 纪律遵守核验：prompt 新增段明确"必须【单独一轮】调用——同一轮 tool_calls 中不得混入其他工具调用"（B2 断言点 2 实证要求的落点）√。

### E1（sandbox 工具化）

| CP | 判定 | 独立复核依据 |
|---|---|---|
| CP-E1-1 合法 JSON + 收集器收真实 dataclass | PASS | 用例断言 `raw == json.dumps(parsed, sort_keys=True, ...)` 字节级幂等（比 C1 更严）+ `collector.prep_results == [prep]` 同实例；prepare 业务失败（success=False）与 tool_error 正确区分 |
| CP-E1-2 确定性解析改写行为等价 | PASS | 复用 sp3 shell_parse 语料：&& 短路 / ; 双跑 / 裸 pip 改写 / cd 持续+越界拒绝 / glob 展开 / source 丢弃，RecordingRunner 逐 argv 断言 |
| CP-E1-3 脱敏 | PASS | 探针 P7a 独立证实 run_in_sandbox 返回无明文；用例额外锚定"收集器保留全量原文"（logs 脱敏是 E3 `_aggregate_logs` 落点，注记诚实） |
| CP-E1-4 extra_env 透传 + 无条件 GIT_TERMINAL_PROMPT=0 | PASS | 探针 P8 + 用例 spy：prepare_venv / run_in_venv（含复合拆分每条子命令）全路径收到 extra_env |
| CP-E1-5 工具异常转结构化错误 + WARNING | PASS | 探针 P7b/P7c：SandboxCreationError / OSError 兜底、**异常消息本身也过 mask**（token 内嵌 URL 场景无明文）——超出 dev-plan 最低要求的正确加固 |

补充：python_exe 四级解析优先级（收集器 → ref → .venv/pyvenv.cfg 探测 → 结构化错误提示先 prepare）有独立用例覆盖，其中优先级 3 正是 R-S4-10 resume 后收集器重建为空的兜底，设计与 B2 实证闭环。

### E2（`_run_execution_agent` 内嵌子图装配）

| CP | 判定 | 独立复核依据 |
|---|---|---|
| CP-E2-1 装配清单逐项 | PASS | 探针 P5/P5b：SystemMessage 整条常量（比 CP-F3-1 更强——连尾部段落都无动态变量）、HumanMessage sort_keys 幂等 + fix_round/stderr_tail 裁剪注入；用例经 capture 子图断言 `_llm` 注入 / ReActState 五要素 / max_rounds=REACT_MAX_ROUNDS_EXECUTION / rounds 提取 max(1, round) 同 wrapper 口径；缺 llm_config_set → WARNING + 空结果降级（非静默） |
| CP-E2-2 剧本收集器 + rounds + force_finish 不越界 | PASS | 真实 create_react_subgraph + 脚本 LLM：`out.prep is prep` 同 dataclass 实例、rounds=4=llm_calls；loop 模式到顶 rounds 恰 = max_rounds、工具执行恰 max_rounds-1 次 |
| CP-E2-3 agent 谎报拦截（E2 层） | PASS | liar 剧本：`<result>` 自称 all_exit_zero=true，ExecAgentOutput 携带真实 exit_code=2 + 不捏造 prep（端到端判定按计划留 E3 装配后闭环） |
| CP-E2-4 子图异常降级 + GraphBubbleUp 直通 | PASS | 探针 P6a/P6b 独立证实：GraphInterrupt ⊂ GraphBubbleUp（except 顺序有效）、`_run_execution_agent` 不吞 GraphInterrupt（BUG-S4-B1-01 红线）；一般异常 → WARNING + 空结果 + rounds=0（不扣预算） |
| CP-E2-5 execution 侧 request_user_input 挂载 | PASS | 用例：真实子图 + InMemorySaver 父图，run(128 认证失败) → interrupt#3（is_sensitive=True）→ resume → run(0) 成功收尾；`run_in_venv` 总调用恰 2 次（**前序不重放**，B2 门禁在 execution 场景复证）；R-S4-10 合并路径（收集器丢前段 → messages 回读补全 + WARNING 留痕 + 尾段全保真 stdout）逐项断言；resume 敏感值登记进程内 sensitive set；连跑 3 次锚定 |

**关于 messages 回读通道的启用判定**：dev-plan E1 写"MVP 先单通道收集器，兜底按 B2/E4 实证决定是否启用"。B2 报告（同日）已实证收集器 resume 后丢 pre-interrupt 值且 messages 通道完整——在 request_user_input 已挂载的前提下，启用回读+合并是**正确性必需**而非过度工程，且严格按 B2 建议 3 推荐方案（BUG-S1-03 范式：过滤失败 ToolMessage / tool_error / 解析失败打 WARNING 非静默）。**判定：有据启用，接受。**

### E 线边界完整性（E3 前置守门）

- **execution() 主函数一字未动**：diff 逐 hunk 核对，主函数（guard / work_dir 降级 / 步骤 1-7 调用序 / `_maybe_interrupt_or_return` / interrupt#2 payload / self-loop）**零 hunk**。当前主函数仍走 sp3 确定性循环，`_run_execution_agent` 已就绪未接线（E3 任务）。✔
- 既有代码唯一改动：`_run_step_subcommands` 追加 `extra_env: Optional[...] = None` 形参 + `run_in_venv(..., extra_env=extra_env)` 透传——向后兼容（主函数 3 参调用行为逐字一致），属 E1 规格"确定性能力留工具内部实现"的最小侵入。全量回归证实 sp3 c3/d1 系列零退化。✔

### F1（execution_monitor 面板 + app.py 路由）

| CP | 判定 | 独立复核依据 |
|---|---|---|
| CP-F1-1 三类 interrupt_kind 分发互不误触 + 敏感/非敏感渲染 | PASS | AppTest 5 条：user_input_request → 输入面板（单输入框 + password proto 断言 + 「记住」默认不勾）；dev_loop_failure → 既有决策面板零退化；planning → 跳 review；非敏感 → DEFAULT 输入 + 无勾选 |
| CP-F1-2 resume payload 两键契约 | PASS | 探针 P9a + AppTest 点击流：`resume_with(thread_id, {"value", "remember"})` 精确断言（assert_called_once_with）；value 不 strip 原样透传、remember 强转 bool |
| CP-F1-3 手动 happy path（streamlit run 走查） | **未执行** | dev-plan 标注 AC-S4-10 需真实两侧（coding/execution）各走查一次——涉及真实链路，留 F/G 阶段与 Maria 协作执行（遗留项） |

- **L-B1-01 空值防线真实生效**：探针 P9b（纯函数）+ AppTest 两条（留空 / 纯空白提交 → `resume_with.assert_not_called()` + 报错文案）。✔
- **app.py 改动最小**：diff 仅 3 处新增（本地常量 / 纯函数 `_should_route_to_user_input_panel` / main() 一处路由判定）；`interrupt_kind` helper 与 `resume_with` 本体零 hunk；惰性求值（未 interrupt 不读 kind）有用例锚定（mock `assert_not_called`）。✔
- **常量防漂移**：探针 P10 三处（interaction_tools / execution_monitor / app）字节一致，且有专门用例固化。✔

### 脱敏落点全覆盖核验（架构 §9.4 交叉表）

| 落点 | 实现 | 独立证据 |
|---|---|---|
| run_command stdout/stderr | `mask_value` L119-120 | 探针 P1（真实子进程） |
| run_in_sandbox stdout/stderr | `_run_result_to_payload` | 探针 P7a |
| prepare error 摘要 | `mask_value(_tail(prep.error))` | 探针 P7b |
| 工具异常消息（`_tool_error_json`） | `mask_value(message)` | 探针 P7c |
| `_aggregate_logs` / interrupt payload | E3 落点，本批不涉及 | —（E3 验收项） |

### 测试质量审计（假绿模式排查）

- 逐文件审计 5 个新增测试文件：**未发现** assert True 空转、mock 被测对象本身、剧本与断言脱节等假绿模式。
- 亮点：C2/E2 副作用观测走"模块级 dict + 磁盘 append"双通道（正确规避 R-S4-10 闭包重建陷阱）；E1 `_assert_sorted_json` 用序列化字节级幂等断言（比 json.loads 可解析更强）；F1 用 proto 层断言 password 类型（非字符串包含）。
- mock 边界均在被测对象外侧（prepare_venv / run_in_venv / create_llm / deepxiv 工厂 / GraphController），被测函数本体全部真实执行。

## 独立探针复核清单（16/16 PASS）

P1 run_command 脱敏（真实子进程回显 token）｜P2 越界拒绝 + `_run_subprocess` 零调用｜P3 Q-B1 红线 3 恰 5 键｜P4 coding 7 工具集合｜P5/P5b E2 SystemPrompt 常量 + HumanMessage 幂等｜P6a/P6b GraphInterrupt⊂GraphBubbleUp + 不被吞｜P7a/b/c E1 三处脱敏｜P8 GIT_TERMINAL_PROMPT=0 无条件｜P9a/b F1 两键契约 + 空值防线｜P10 interrupt_kind 常量三处一致｜P11 超时杀子树（1s 实测 1.0s）

## 失败排查

无失败。

## 发现的问题分级

**阻断**：无。

**非阻断**：
1. `tests/test_sprint4_e1.py::_ws_dir` 在真实 `config.WORKSPACE_DIR` 下创建持久目录（`workspace/e1-tool-test/…`），测试残留不自动清理。沿用 sp3 shell_parse 既有范式（`_resolve_cd`/`_is_within_workspace` 绑定 import 期 WORKSPACE_DIR 无法 monkeypatch，有先例），接受但记录——G 阶段可统一考虑 session 级清理 fixture。

**建议**：
2. `_run_execution_agent` 传给 `create_react_subgraph` 的 `system_prompt` 参数在子图内实际未被消费（消息由 initial_messages 提供，与 wrapper L850 同构）——无害冗余，E3/G 阶段可顺手在 react_base 注释澄清该参数语义，防未来误解为"子图会自动注入"。
3. `_merge_extra_env` 的覆盖顺序 `{"GIT_TERMINAL_PROMPT": "0", **(extra_env or {})}` 允许调用方显式覆盖 GIT_TERMINAL_PROMPT——语义合理（显式优先），但未有用例锚定"调用方传 1 时不被改回 0"；非契约点，仅记录。

## 后续动作

- **CP-F1-3 遗留**：`streamlit run` 手动 happy path 走查（coding / execution 两侧各一次，AC-S4-10）——需真实链路 + Maria 明确授权（e2e 配额纪律），建议随 F/G 阶段真实链路验证一并执行。
- E3 挂账验收项：`_aggregate_logs` / interrupt payload 脱敏（§9.4 后两落点）、CP-E2-3 端到端闭环（编排层判 failure）、预算对账（AC-S4-04）——待 E3 交付后验收。
- dev-plan CP 勾选与 TODO.md 收口由主控统一执行（本报告可作勾选依据：CP-C1-1~5 / CP-C2-1~5 / CP-E1-1~5 / CP-E2-1~5 / CP-F1-1~2 全部可勾，CP-F1-3 保留未勾）。
