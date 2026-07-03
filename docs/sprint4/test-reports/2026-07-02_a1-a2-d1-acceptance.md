# 测试执行报告 - a1-a2-d1-acceptance

- **日期**：2026-07-02 22:46（本地时区）
- **执行人**：@测试工程师代理
- **Sprint**：sprint4
- **触发原因**：Sprint 4 首批任务 A1（config 3 常量）/ A2（state 2 交互通道）/ D1（prepare_venv extra_env 透传）独立验收 —— 逐条运行时实证 CP-A1-1~3 / CP-A2-1~3 / CP-D1-1~3 + 治理范式核查 + 边界补强 + 全量回归 3 次连跑
- **commit**：504e2ea（被验收改动为工作区未提交状态：`config.py` +8 / `core/state.py` +17 / `sandbox/local_venv.py` +10，`git diff` 实证 35 insertions / 0 deletions）

## 验收结论

**PASS**（A1 / A2 / D1 三任务全部通过验收，无 must-fix，无生产代码 BUG）。

- 9 个 CP 检查点逐条独立运行时实证全部命中（python 探针 + AST + grep + spy，不依赖开发自报，不只跑开发用例）。
- 三生产文件 `git diff` 实证**纯追加零删改**；既有常量 / 字段 / 函数签名零漂移。
- 治理范式两项核查通过：A2 新字段无 reducer（must-fix-1）；D1 无第二套 env 合并逻辑（AST 实证 `os.environ` 仅 `_build_sandbox_env` 一处触达）。
- CP-D1-3 mask 落点结论与架构 §9.4 一致，判定**可接受为非阻断**（详见下文专节）。
- 全量非 e2e 回归 3 次连跑 1140/1140 passed，= 基线 1119 + 新增 21，0 退化 0 flaky。

## 执行范围

- 命令：
  - 独立 python 探针（`.venv/bin/python` heredoc）：CP-A1-1~3 / CP-A2-1~2 值与类型断言、`create_initial_state` 老/新形态默认值、dict 实例独立性、三 List 字段 origin=list
  - `grep -nE "Annotated|operator\.add" core/state.py`（must-fix-1 双证之一）
  - AST 探针：`sandbox/local_venv.py` 全函数扫描 `os.environ` 触达点（治理核查）
  - `.venv/bin/pytest -q tests/test_sprint4_a1.py tests/test_sprint4_a2.py tests/test_sprint4_d1.py`（开发 19 条 + 补强 2 条）
  - `.venv/bin/pytest -q tests/test_sprint3_b1.py tests/test_sandbox_env_isolation.py`（CP-D1-1 引用的 sp3 护栏 38+9 条）
  - `.venv/bin/pytest -q -m "not e2e and not sandbox_real" --ignore=tests/test_paper_intake.py` × 3（全量回归）
  - 单用例独立运行抽查（2 条补强用例逐条 `::` 指定运行）
- 是否包含 e2e：否（硬性边界：禁跑 e2e / sandbox_real，本批任务均为结构性改动，mock 层足以验收）。

## 逐 CP 独立实证

| CP | dev-plan 要求 | 独立复核方式 | 结论 |
|---|---|---|---|
| CP-A1-1 | 三常量可导入且值/类型逐项断言 | python 探针：`REACT_MAX_ROUNDS_EXECUTION==10`（`type is int` 排 bool）/ `RUN_COMMAND_TIMEOUT==120` / `SECRETS_FILE_NAME==".secrets"`，与架构 §12.2 表逐项对照 | PASS |
| CP-A1-2 | 120 < 1800 且 10 ≤ 60 | 探针实证 `RUN_COMMAND_TIMEOUT < SANDBOX_EXEC_TIMEOUT==1800`、`REACT_MAX_ROUNDS_EXECUTION <= MAX_DEV_LOOP_LLM_CALLS==60` | PASS |
| CP-A1-3 | 既有常量基线不动 | 探针逐项断言 5 个基线常量（120/10/60/2/12）+ `git diff -- config.py` 实证 +8 行纯插入（sp4 独立段落，插在"环境变量读取"节前），0 删改 | PASS |
| CP-A2-1 | 两字段声明 + 类型 + 默认值（老/新形态） | 探针：`__annotations__` 中 `pending_user_input == Optional[Dict]`（origin=Union 非 Annotated）、`collected_inputs == Dict[str,str]`（origin=dict）；`create_initial_state` 老形态 LLMConfig 与新形态 LLMConfigSet 均得 None/{}，且两次调用 `collected_inputs` 非同一 dict 实例 | PASS |
| CP-A2-2 | must-fix-1 grep 双证沿用 | grep 全文件唯一命中为 L216 注释行（"绝不给任何字段加 Annotated / operator.add"自述），不落任何字段声明行；三 List 字段 `get_origin is list` 探针实证 | PASS |
| CP-A2-3 | 全量非 e2e 回归零退化（基线 1119） | 3 次连跑 1140 passed / 0 failed / 37 skipped（1119 + 21 新增，见回归统计节） | PASS |
| CP-D1-1 | 签名向后兼容，不传时与 sp3 逐字一致 | `inspect.signature` 实证 `extra_env` 末位追加、默认 None、既有 6 形参顺序不变；sp3 护栏 `test_sprint3_b1.py` 38 条 + `test_sandbox_env_isolation.py` 9 条全绿（49s）；开发用例实证不传时全调用点收到 None | PASS |
| CP-D1-2 | spy 全路径透传 + 合并语义不被绕过 | 开发用例 spy `_run_subprocess` 实证 venv 创建 + `-r` 文件 + 逐包 + 瞬态重试第 2 次 attempt 均收 extra_env；Popen 层用例实证白名单剔除哨兵、PATH 保留、extra_env 覆盖白名单同名项、与 `_build_sandbox_env(extra_env)` 纯函数结果逐字相等；本代理 AST 补证：`os.environ` 全文件仅 `_build_sandbox_env` 一处触达，**无第二套合并逻辑** | PASS |
| CP-D1-3 | 注入值不落 install_log 明文，链路至少一处兜住，落点写进报告 | 双向实证：(a) pip 不回显时 install_log/error/env_info 均不含 token（prepare 自身零泄漏）；(b) characterization：pip 回显 token 时 install_log **现状明文含** token → prepare 层无 mask 兜底，落点判定见下节 | PASS（结论成立，非阻断，附挂账） |

## CP-D1-3 mask 落点结论的验收判定

**开发结论**："prepare_venv 层无 mask 兜底，mask 统一落点 = A3 `secrets_store.mask_value` + E3 execution 收尾消费"。

**判定：与架构一致，可接受为非阻断。** 依据：

1. 架构 §9.4 脱敏统一落点表明确列 "`execution_result.logs` 聚合前：`_aggregate_logs` 结果 `mask_value(logs, load_all_secrets())` 后再写 state"——mask 落点在**消费侧（E3）**，不在 prepare 层；§13 改动清单对 `sandbox/local_venv.py` 的定义也仅为"补 extra_env 形参 + 透传"（最小 diff），不含 mask 职责。
2. 泄漏面受控：`SandboxPrepareResult.install_log` 是进程内返回值，D1 阶段无消费方写 state/checkpoint/日志落盘；真正的落盘路径（execution 写 `execution_result.logs`）在 E3 才接线，届时 mask 强制生效（AC-S4-11/12 验证点）。
3. characterization 用例（`test_cp_d1_3_pip_echo_token_lands_in_install_log_characterization`）把现状钉死为回归锚：若 E3 落地后有人把 mask 下沉到 prepare 层，该断言翻转会强制复核 §9.4 落点表——设计合理。

**挂账（非阻断，验收 E1/E3 时必查）**：E3 验收时必须实证 "`install_log` 进入 `execution_result.logs` / state 前经 `mask_value`"，否则本 characterization 锚定的明文将成为真实泄漏。建议主控在 E3 验收清单中显式加入此项。

## 治理范式核查

| 项 | 方式 | 结论 |
|---|---|---|
| A2 两新字段无 reducer（must-fix-1） | grep 零字段行命中 + `get_origin` 非 Annotated（Union/dict）+ 补强用例经真实 StateGraph 实证 last-write-wins 无重复累加 | PASS |
| A2 显式声明 + 默认值（B2/B3 治理） | `__annotations__` 存在性 + `create_initial_state` 默认值 + 补强用例实证真实图写入**不被静默丢弃** | PASS |
| D1 无第二套 env 合并（单点 `_build_sandbox_env`） | AST 扫描：全文件触达 `os.environ` 的函数集合 == `{_build_sandbox_env}`；Popen 层用例实证子进程 env 与纯函数结果逐字相等 | PASS |
| A2 顺带注释（`_dev_loop_llm_calls` 语义收窄 3 行） | git diff 实证纯注释追加，dev-plan §7.3 明文授权（"state 字段注释同步微调（A2 顺带）"），零代码行为变化 | PASS |

## 审阅开发用例 + 补强

开发用例 19 条（A1×5 / A2×6 / D1×8）覆盖扎实：A1 严格类型排 bool、A2 老/新形态双覆盖 + dict 实例独立性、D1 的 Popen 层合并语义端到端与 `_collect_env_info` 边界锚定尤佳。两点小事实修正：主控简报称 A1 为 6 用例，实际收集 5 条（CP 覆盖完整，无缺口）；三文件合计 19 条非 20 条。

测试工程师补强 2 条边界用例（反过度工程，只补真实盲区）：

| 用例 | 落点 | 价值 |
|---|---|---|
| `test_boundary_new_channels_writable_via_minimal_graph_not_silently_dropped`（test_sprint4_a2.py） | A2 | 开发用例只验了声明与默认值（静态层）；本用例用真实 StateGraph 双节点实证两新通道**写入不被 LangGraph 静默丢弃**（这正是 B2/B3 治理要求显式声明的存在理由）+ read-modify-write last-write-wins + resume 清 None 语义，为 E 阶段编排层写入方提供行为基线 |
| `test_boundary_pip_permanent_failure_single_attempt_extra_env_intact`（test_sprint4_d1.py） | D1 | 开发用例只在成功/瞬态重试路径断言透传；本用例补**非瞬态失败路径**：恰 1 次 attempt（不重试）仍收 extra_env、`success=False` + error 汇总、失败通道（error/install_log）不含注入值明文、入参 extra_env dict 不被 prepare_venv 变异 |

两条均通过单用例独立运行抽查（`pytest tests/...::case` 逐条跑通）。

## 结果摘要

- sp4 三文件：**21 passed**（开发 19 + 补强 2），0.16s
- sp3 护栏（CP-D1-1 引用）：**47 passed**（B1 38 + 隔离 9），49s
- 全量非 e2e 回归：**1140 passed / 0 failed / 37 skipped / 43 deselected**（e2e+sandbox_real）
- 警告：1 —— `LangChainPendingDeprecationWarning`（langgraph `checkpoint/serde/jsonplus` 的 `allowed_objects` 预弃用），库级别、sp1 起既有、与本批改动无关
- 独立探针：A1/A2 全项 PASS、D1 结构探针（签名/AST/合并语义）全项 PASS

## 连跑稳定性

- 全量非 e2e 回归 3 次连跑：1140 / 1140 / 1140 passed（56.07s / 56.93s / 56.09s），skipped 恒 37、deselected 恒 43，**0 退化 0 flaky**
- 数字对账：基线 1119（2026-07-02 收口实测）+ 新增 21（开发 19 + 补强 2）= 1140，逐项吻合

## 失败排查

无失败。

## 后续动作 / 遗留项（均非阻断）

- **L-D1-01（挂账，E3 验收必查）**：CP-D1-3 characterization 锚定 "prepare 层 install_log 现状明文含回显 token"；E3 落地后必须实证消费侧 `mask_value` 兜底生效（架构 §9.4 / AC-S4-11/12），否则明文成为真实泄漏。
- **L-D1-02（信息项）**：`_collect_env_info`（python --version / pip freeze）刻意保持 extra_env=None（最小 diff，本地查询无凭证需求），已有用例锚定该边界；若未来私有 venv 内 pip freeze 需要凭证（罕见），需同步放开并改锚定用例。
- **事实修正**：主控简报"A1 6 用例"实际为 5 条（覆盖无缺口，仅计数差异）。
- 沿用既有遗留（非本次引入）：`tests/test_paper_intake.py` main 风格脚本按惯例 `--ignore`；langgraph 库级 PendingDeprecationWarning。
- 下一次触发条件：A3（secrets_store）交付后验收 mask_value 契约；E3 交付后回归本报告 L-D1-01 挂账项。
