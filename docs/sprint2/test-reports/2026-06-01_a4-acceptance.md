# 测试执行报告 - a4-acceptance

- **日期**：2026-06-01 （本地时区）
- **执行人**：@测试工程师代理
- **Sprint**：sprint2
- **触发原因**：A4（config.py 追加 15 个 sp2 常量）独立测试补全与验收 —— 复核 CP-A4-1~4 + 补边界 + 回归基线零退化
- **commit**：216b26a

## 验收结论

**PASS**。

- CP-A4-1~4 四个 dev-plan 检查点逐条独立复核全部命中（不依赖开发代理自报，自行 Read config.py + git diff 核实）。
- 15 个常量值 / 类型 / 路径关系全部符合 dev-plan §A4 表格规格。
- `WORKSPACE_REPOS_DIR` 确已加入 `ensure_directories()`（L124）。
- sp1 既有常量经 `git diff HEAD~1 -- config.py` 核实为**纯追加 0 删改**，`MAX_TOTAL_LLM_CALLS == 50` 等关键常量零修改。
- 无 BUG，无路径不变量破坏。

## 执行范围

- 命令：
  - `pytest tests/test_sprint2_a4.py -v`（A4 单测）
  - `pytest -q -m "not e2e" --ignore=tests/test_paper_intake.py`（非 e2e 核心回归，3 次连跑）
  - `pytest tests/test_sprint2_a4.py -q`（A4 文件 3 次连跑）
  - `pytest --collect-only -q -m "not e2e"`（收集确认）
- 覆盖用例：`tests/test_sprint2_a4.py` 全部 12 用例（6 原始 CP + 6 测试工程师补强 Aux）+ sp1/sp2 全部非 e2e 单测集。
- 是否包含 e2e：否（A4 为纯配置任务，无外部依赖，无 e2e 必要；按要求避免触发真实 LLM 消耗 token，全程 `-m "not e2e"`）。

## CP-A4-1~4 逐条复核

| CP | dev-plan 要求 | 独立复核方式 | 结论 |
|---|---|---|---|
| CP-A4-1 | 15 个新增常量全部可导入 | Read config.py L13/L65-98 确认定义 + `test_cp_a4_1` 全表 15 常量 import | PASS |
| CP-A4-2 | 关键常量值正确 | 逐项值断言 + 严格类型（int 非 bool / Path / str） + `WORKSPACE_REPOS_DIR == WORKSPACE_DIR/'repos'` | PASS |
| CP-A4-3 | `ensure_directories()` 后 `WORKSPACE_REPOS_DIR.is_dir()` True | Read config.py L124 确认 mkdir 已追加 + 运行时断言 | PASS |
| CP-A4-4 | sp1 既有常量零修改（尤其 `MAX_TOTAL_LLM_CALLS=50`） | `git diff HEAD~1 -- config.py` 核实纯追加无删改 + 14 个 sp1 关键常量逐项值断言 | PASS |

git diff 关键证据：config.py 相对 HEAD~1 的全部改动为 3 段插入（L13 `WORKSPACE_REPOS_DIR` / L65-98 sp2 常量块 / L124 `ensure_directories` 末行追加），无任何既有行被修改或删除。

## 审阅开发代理用例 + 补强

开发代理 6 用例（CP-A4-1~4）覆盖充分扎实：已含全表 15 常量导入、逐项值断言、严格 int/Path/str 类型断言、`WORKSPACE_REPOS_DIR == WORKSPACE_DIR/'repos'`、`ensure_directories` 创建断言、14 个 sp1 关键常量零修改强断言。

测试工程师补强 6 条边界用例（覆盖开发代理未触及的契约维度）：

| 用例 | 关注点 | 价值 |
|---|---|---|
| `test_aux_1_repos_dir_is_resolved_subdir_of_workspace` | A4→A5 关键路径不变量 | 断 `WORKSPACE_REPOS_DIR.resolve().is_relative_to(WORKSPACE_DIR.resolve())` 且为真子目录。开发代理只断了未 resolve 的字符串 `==`；A5 git_clone 越界校验（dev-plan L468）用 `resolve()+is_relative_to`，符号链接 / `..` 风险只有 resolve 后才暴露。这是 A4 配置交给 A5 的核心契约。 |
| `test_aux_2_pwc_rate_limit_to_interval_ms` | 语义换算 | 固化 `PWC_RATE_LIMIT_RPS=5 → 1000/5 == 200ms`（dev-plan L435 声明），防默认值漂移使 A6 节流间隔静默变化。 |
| `test_aux_3_timeout_and_threshold_semantics` | 数值语义合理性 | 全部 timeout/阈值 > 0（防 0/负数让超时立即触发或永不触发）+ `PWC_TIMEOUT_CONNECT <= PWC_TIMEOUT_READ` 常识不变量。 |
| `test_aux_4_streamlit_page_constants_distinct` | UI 路由 | 三个 `STREAMLIT_PAGE_*` 互不相同（防路由撞键）。 |
| `test_aux_5_no_env_override_for_sp2_literals` | env 覆盖设计声明 | 设同名 env 后 reload，断言 5 个 sp2 常量不被 env 撬动，锁定 A4 dev 决策"纯字面量无 env 覆盖"（区别于 sp1 base_url/model getter）。 |
| `test_aux_6_ensure_directories_idempotent` | 幂等性 | `ensure_directories()` 二次调用不抛异常（exist_ok）。 |

补强用例均无副作用、可独立运行；`aux_5` 用 monkeypatch + try/finally reload 还原，不污染后续 test。

## 结果摘要

- A4 单测：12 passed（6 CP + 6 Aux），0.03s
- 非 e2e 核心回归：**113 passed / 0 failed / 17 deselected(e2e) / 0.93s**（较 A3 验收 + A4 dev 自报基线 107 增加 6，即本次补强 6 条）
- 跳过：0（非 e2e 范围内无 skip）
- 警告：1 —— `LangChainPendingDeprecationWarning`（langgraph `checkpoint/serde/encrypted.py:5` 的 `allowed_objects` 默认值预存弃用警告），库级别、sp1 既有、与 A4 无关
- 总耗时：单测 ~0.03s / 回归 ~0.93s

## 连跑稳定性

- 非 e2e 核心回归 3 次连跑：113 / 113 / 113 passed（0.94s / 0.91s / 0.91s），0 抖动
- A4 单文件 3 次连跑：12 / 12 / 12 passed（均 0.01s）
- 累计 3×113 + 3×12 = 375/375 PASS，0 失败 0 跳过
- 收集确认：`--collect-only -m "not e2e"` 收到 `test_sprint2_a4` 12 项，e2e 默认排除正常

## 失败排查

无失败。

## 后续动作 / 遗留项

- 无新增 BUG，无阻断项。
- 沿用既有遗留项（非本次引入，非阻断）：
  - L-A3-01：`tests/test_paper_intake.py` 是 sp1 main 风格脚本，`pytest` 收集为 0，本次回归按既有惯例 `--ignore` 排除（TODO L149 已挂条目）。
  - L-A3-02：`pytest.ini` markers 注释 `--run-e2e` 与凭证驱动实现不符（TODO L150 已挂条目）。
- 下一次跑测试的触发条件：A5 `core/tools/git_tools.py` 交付后 —— 届时 `test_aux_1` 断言的 `WORKSPACE_REPOS_DIR` 路径不变量将由 A5 git_clone 的 `is_relative_to` 越界校验实际消费，需在 A5 验收时联动核对。
