# 测试执行报告 - a5-acceptance

- **日期**：2026-06-01 （本地时区）
- **执行人**：@测试工程师代理
- **Sprint**：sprint2
- **触发原因**：A5（`core/tools/git_tools.py`）独立测试补全与验收 —— 复核 CP-A5-1~10 + 审阅开发代理 19 用例 + 补全 subprocess/网络/重试边界 + 回归基线零退化
- **commit**：96e403c（补强前实现 commit 216b26a）

## 验收结论

**PASS**。

- CP-A5-1~10 十个 dev-plan 检查点逐条独立复核全部命中（自行 Read `git_tools.py` 全文 491 行确认实现，不依赖开发自报）。
- 安全约束全部满足：subprocess 全程列表形式无 `shell=True`；dest_dir 越界校验用 `resolve().is_relative_to(WORKSPACE_DIR.resolve())`，与 A4 验收路径不变量一致。
- 重试分类正确：永久关键字优先于瞬态、大小写无关、未识别默认归 Permanent；退避序列硬上限 3 次（1s/2s/4s）。
- BUG-S1-02 治理到位：所有 ToolMessage 输出经 `_serialize_tool_result`（json.dumps ensure_ascii=False/sort_keys=True/default=str），全程无 `str(dict)`；工具内部异常兜底为合法 JSON 失败 dict，不打断 ReAct 子图。
- RepoInfo 12 字段与 `core/state.py` 的 RepoInfo TypedDict 注解键逐字段一致。
- 无 BUG。1 处契约澄清（非缺陷，见下）。

## 执行范围

- 命令：
  - `pytest tests/test_sprint2_a5.py -v`（A5 单测，dev 19 + 补强 19 = 38）
  - `pytest -q -m "not e2e" --ignore=tests/test_paper_intake.py`（非 e2e 核心回归，3 次连跑）
  - `pytest tests/test_sprint2_a5.py -q`（A5 单文件 2 次连跑）
  - `pytest tests/test_sprint2_a5.py --collect-only -q -m "not e2e"`（收集确认）
- 覆盖用例：`tests/test_sprint2_a5.py` 全部 38 用例（19 dev CP/aux + 19 测试工程师补强）+ sp1/sp2 全部非 e2e 单测集。
- 是否包含 e2e：否。A5 是 subprocess/网络密集型工具，全部以 mock subprocess/requests/time.sleep + tempdir 真实 git init 覆盖；真实 clone e2e 按 dev-plan 留给 E1。全程未触发真实 LLM / 网络 / 真实 clone，零 token 消耗。

## CP-A5-1~10 逐条复核

| CP | dev-plan 要求 | 独立复核方式 | 结论 |
|---|---|---|---|
| CP-A5-1 | git_clone 网络可达返回 success=True + 合法 local_path | Read git_clone L195-296 + `test_cp_a5_1`（断命令列表 `["git","clone","--depth",...]` + url 在内） | PASS |
| CP-A5-2 | 死链（Repository not found + exit 128）抛 PermanentError 不重试 | `_classify_clone_failure` L144-166 永久关键字命中即 raise；`test_cp_a5_2` 断 run 1 次 sleep 0 次 | PASS |
| CP-A5-3 | 网络瞬态（TimeoutExpired）3 次指数退避后抛 TransientError | 循环 L252-296 首次+3 重试；`test_cp_a5_3` 断 run 4 次 + sleep 入参 `[1.0,2.0,4.0]`；3b 覆盖 stderr 网络关键字路径 | PASS |
| CP-A5-4 | dest_dir 越界抛 PermanentError("dest_dir 越界") | `_is_within_workspace` L132-141 resolve 后 is_relative_to；越界校验 L219-224 在 subprocess 之前；`test_cp_a5_4` 断 run 0 次 | PASS |
| CP-A5-5 | 同 URL 二次调用 success=True + duration=0.0 跳过 | slug 目录存在检测 L227-236；`test_cp_a5_5` 断 run 0 次 | PASS |
| CP-A5-6 | analyze_local_repo 返回完整 RepoInfo（local_path/has_readme/dir_structure 字典序/is_official=False） | 真实 git init+commit；断 12 字段全集 + commit_count_recent==1 + last_commit_date 非空 | PASS |
| CP-A5-7 | 无 README 仓库 has_readme=False/has_requirements=False | L358-359 any() 检测；非 git 目录指标降级不抛 | PASS |
| CP-A5-8 | check_url_reachable 200→True 死链→False | L382-396 状态码 in (200,301,302)；断 200/301/302/404/异常 + allow_redirects=True | PASS |
| CP-A5-9 | 3 工厂返回 BaseTool + 序列化合法 JSON | L403-490 三 `@tool` 工厂；`_serialize_tool_result` L89-99；断 json.loads 可解析 + 中文不转义 + 字典序 + 三工厂成功/失败两路径 | PASS |
| CP-A5-10 | subprocess.run 全部不用 shell=True | `_run_git` L169-188 列表形式无 shell；spy 录制 git_clone + analyze_local_repo 全部 run 调用逐条断 list + shell≠True | PASS |

## 安全 / 重试 / 治理重点核实（任务专项）

- **subprocess 安全**：`_run_git` 统一封装 `cmd = ["git"] + args`，`subprocess.run(..., timeout=..., check=False)` 无 `shell=` 参数。git_clone 命令 `["clone","--depth",str(depth),url,dest]`，analyze 命令 `["log","--since=6 months ago",...]` / `["log","-1","--format=%cI"]` 均列表形式（`--since=6 months ago` 作为单个列表元素正确，不会被 shell 切词）。CP-A5-10 + 补强 spy 双重证实。
- **越界不变量**：`_is_within_workspace` 用 `WORKSPACE_DIR.resolve()` 与 `dest.resolve().is_relative_to(...)`，与 A4 验收 `test_aux_1` 的 `WORKSPACE_REPOS_DIR.resolve().is_relative_to(WORKSPACE_DIR.resolve())` 同一判定路径。补强 `test_te_within_workspace_invariant_matches_a4` 联动断言；`test_te_within_workspace_rejects_parent_escape` 验证 `..` 逃逸被 resolve 后拒绝。
- **重试分类**：`_classify_clone_failure` 永久关键字（repository not found / authentication failed / permission denied / no space left on device / disk quota exceeded 等）先于瞬态（connection refused / timed out / could not resolve host / rpc failed / early eof 等）判定，未识别默认 Permanent。补强 4 条专项（优先级 / 大小写 / 未识别 / 磁盘满端到端）固化。退避序列 `_RETRY_BACKOFF_SECONDS == (1.0,2.0,4.0)` 上限 3 次，由 CP-A5-3 + 补强 `test_te_backoff_sequence_capped_at_three` 双重锁定。
- **BUG-S1-02 治理**：三工厂全部 `_serialize_tool_result(...)`，无任何 `str(dict)`；三类异常路径（TransientError/PermanentError/兜底 Exception）均返回合法 JSON 失败 dict。补强 `test_te_compound_tool_skips_analyze_on_clone_failure` / `_transient_failure_is_json` 验证失败 ToolMessage 仍 json.loads 可解析。
- **RepoInfo 对齐**：补强 `test_te_analyze_repoinfo_fields_match_state_typeddict` 用 `set(info.keys()) == set(StateRepoInfo.__annotations__.keys())` 动态对齐（比硬编码字面量集合更抗未来字段漂移）。

## 审阅开发代理 19 用例 + 补强 19 条

开发代理 19 用例覆盖扎实：CP-A5-1~10 全覆盖，含命令列表断言、不重试 run/sleep 计数、退避入参序列、真实 git init 完整 RepoInfo、三工厂成功/失败两路径 JSON、subprocess spy 无 shell、git 缺失、slug 基础解析。

测试工程师补强 19 条（覆盖开发未触及的边界）：

| 用例 | 关注点 |
|---|---|
| `test_te_classify_permanent_priority_over_transient` | 永久+瞬态关键字同存时永久优先（避免对死链浪费退避） |
| `test_te_classify_case_insensitive` | 关键字大小写无关 |
| `test_te_classify_unrecognized_defaults_permanent` | 未识别错误默认 Permanent 契约固化 |
| `test_te_classify_disk_full_permanent` + `test_te_disk_full_no_retry_via_git_clone` | 磁盘满永久不重试（分类器 + 端到端 run/sleep 计数） |
| `test_te_within_workspace_rejects_parent_escape` | `..` 逃逸 resolve 后拒绝 |
| `test_te_within_workspace_invariant_matches_a4` | A4→A5 路径不变量联动断言 |
| `test_te_analyze_dir_structure_truncated_to_30` | 50 项乱序 → 截断到字典序前 30 项 |
| `test_te_analyze_empty_dir` | 空目录降级（dir_structure=[]，commit 指标 None） |
| `test_te_analyze_repoinfo_fields_match_state_typeddict` | RepoInfo 字段动态对齐 TypedDict |
| `test_te_repo_slug_preserves_case` | slug 保留大小写（GitHub 名敏感） |
| `test_te_url_reachable_requests_timeout` / `_connection_error` / `_500_false` / `_403_false` | requests 具体异常类型 + 非 2xx/3xx 状态码均 False 不抛 |
| `test_te_compound_tool_skips_analyze_on_clone_failure` | clone 失败时复合工具跳过 analyze，输出合法 JSON |
| `test_te_compound_tool_transient_failure_is_json` | 瞬态最终失败兜底为合法 JSON |
| `test_te_compound_tool_success_sets_url_field` | 成功路径回填 url 字段（resource_scout 依赖） |
| `test_te_backoff_sequence_capped_at_three` | 退避硬上限 3 次语义固化 |

所有补强用例无副作用、可独立运行、全 mock subprocess/requests/time.sleep（仅 analyze 真实 tempdir 目录扫描，无网络）。

## 结果摘要

- A5 单测：38 passed（19 dev + 19 补强），~0.20s
- 非 e2e 核心回归：**151 passed / 0 failed / 17 deselected(e2e) / 1 warning / ~1.04s**（dev 自报基线 132 + 补强 19 = 151，sp1+sp2 零退化）
- 跳过：0
- 警告：1 —— `LangChainPendingDeprecationWarning`（langgraph `checkpoint/serde/encrypted.py:5` `allowed_objects` 默认值预存弃用），库级、sp1 既有、与 A5 无关
- 总耗时：单测 ~0.20s / 回归 ~1.04s

## 连跑稳定性

- 非 e2e 核心回归 3 次连跑：151 / 151 / 151 passed（1.04s / 1.04s / 1.07s），0 抖动
- A5 单文件 2 次连跑：38 / 38 passed（均 0.18s）
- 收集确认：`--collect-only -m "not e2e"` 收到 `test_sprint2_a5` 38 项；A5 无 e2e 用例，默认排除行为正常

## 失败排查

补强过程出现 1 次断言失败（已自行修复，非生产 bug）：

- 用例：`test_te_analyze_empty_dir`
- 失败类型：**测试代码断言写错**（非生产代码 bug）
- 关键报错：`assert None == 0`（commit_count_recent 实际为 None）
- 排查与结论：Read `git_tools.py` L316 `commit_count_recent: Optional[int] = None`，仅当 `git log` returncode==0 才设为行数。非 git 目录 `git log` 返回非 0（"not a git repository"），变量保持初始 None。实现行为合理（降级而非伪造 0）。开发自报 CP-A5-7 写"commit 指标降级为 None / 0"含糊，**实测降级值为 None**。已把断言修正为 `is None` 并补 `last_commit_date is None`，更精确刻画降级契约。**判定为契约澄清，非缺陷。**

## 后续动作 / 遗留项

- 无新增 BUG，无阻断项。
- **契约澄清（记录，非 bug）**：`analyze_local_repo` 对非 git / 空仓库的 commit 指标降级值为 `None`（不是 0）；dev-plan CP-A5-7 描述"None / 0"含糊，建议后续文档统一为 None。下游 resource_scout 评分需按 `commit_count_recent is None` 判定缺失（而非 `== 0`）。
- 沿用既有遗留项（非本次引入，非阻断）：
  - L-A3-01：`tests/test_paper_intake.py` 为 sp1 main 风格脚本，pytest 收集为 0，本次回归按惯例 `--ignore` 排除。
  - L-A3-02：`pytest.ini` markers 注释 `--run-e2e` 与凭证驱动实现不符。
- 下一次跑测试的触发条件：
  - A6 `core/tools/pwc_tools.py` 交付后验收（同 `_serialize_tool_result` 治理 + 429/5xx/timeout 退避）。
  - E1 阶段对 git_clone 做真实 clone e2e（小仓库靶，mark e2e + 网络可达 skipif）。
</content>
</invoke>
