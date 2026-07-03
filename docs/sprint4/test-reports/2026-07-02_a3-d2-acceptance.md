# 测试执行报告 - a3-d2-acceptance

- **日期**：2026-07-02 23:59（本地时区）
- **执行人**：@测试工程师代理
- **Sprint**：sprint4
- **触发原因**：Sprint 4 任务 A3（`core/secrets_store.py`，安全关键）/ D2（PIP_* 白名单复核落地）独立验收——逐条运行时实证 CP-A3-1~5 / CP-D2-1~2 + 攻击者视角安全补强 + 凭证纪律核查 + 全量回归 3 次连跑
- **commit**：449fa21（被验收改动为工作区未提交状态：新增 `core/secrets_store.py` / `tests/test_sprint4_a3.py` / `tests/test_sprint4_d2.py`，修改 `sandbox/local_venv.py`（23+/5-）/ `tests/test_sandbox_env_isolation.py`（54+/0-））

## 验收结论

**PASS**（A3 / D2 双任务通过验收，无 must-fix，无生产代码 BUG）。

- 7 个 CP 检查点逐条独立运行时实证全部命中（独立 python 探针 A3 25 项 + D2 28 项 + grep 静态日志审计 + git check-ignore + diff 实证，不依赖开发自报、不只复跑开发用例）。
- 安全补强 13 条用例（A3 7 + D2 6）全绿，未发现可利用缺陷；shell 注入 token（`$(rm -rf x)`、反引号）实跑不生效、ASKPASS host 路径穿越不逃逸、大小写具名变量无绕过。
- `git diff` 实证改动最小：`local_venv.py` 5 行删除仅为白名单 dict 推导式改写为等价 for 循环（白名单集合与 extra_env 合并语义逐字保留）；既有 isolation 9 条用例**函数体字节级零修改**且对新代码全绿——语义不弱化的最强证明。
- 全量非 e2e 回归 3 次连跑 1197/1197/1197 passed，= 基线 1140 + 开发新增 44 + 补强 13，0 退化 0 flaky。
- 凭证纪律：全套测试跑完后真实 workspace 无 `.secrets` / `.git_askpass_*` 残留；模块全部 11 条 logger 语句静态审计 + DEBUG 级全捕动态审计均无 value 明文。

## 执行范围

- 命令：
  - 独立探针 `/tmp/probe_a3.py`（25 项：remember→lookup 闭环 / 0600 / JSON schema / 只读不建文件 / 缺失+损坏+结构异常三形态非静默 / mask 长值优先+会话旁路+非敏感保留 / build_credential_env 全分支 + ASKPASS 脚本实跑含 shell 元字符 token / DEBUG 级日志全捕审计）
  - 独立探针 `/tmp/probe_d2.py`（28 项：白名单剔除 4 变量（含大写 HTTPS_PROXY）/ 保留 6 变量 / WARNING 纪律 / extra_env 不过滤 / 正则边界 10 例含 IPv6、路径@、空 userinfo、git+https）
  - `git check-ignore -v workspace/.secrets`（命中 `.gitignore:8 workspace/`）
  - `grep -n "logger\." core/secrets_store.py`（11 条日志语句格式串审计：只打 path / purpose_key / keys / error 类型）
  - `grep -rn "os.environ" sandbox/local_venv.py core/secrets_store.py`（单点触达确认：仅 `_build_sandbox_env` L147；secrets_store 零触达）
  - `diff <(git show HEAD:tests/test_sandbox_env_isolation.py ...) ...`（既有 9 条用例签名零修改实证）
  - `.venv/bin/pytest -q tests/test_sprint4_a3.py tests/test_sprint4_d2.py tests/test_sandbox_env_isolation.py`（开发 53 条）
  - 补强用例单条独立运行抽查（3 条逐 `::` 指定）
  - `.venv/bin/pytest -q -m "not e2e and not sandbox_real" --ignore=tests/test_paper_intake.py` × 3（全量回归）
- 是否包含 e2e：否（硬性边界：禁 e2e / sandbox_real；本批为纯本地文件/env 构造逻辑，mock + 本地 /bin/sh 实跑足以验收，无网络调用）。

## 逐 CP 独立实证

| CP | dev-plan 要求 | 独立复核方式 | 结论 |
|---|---|---|---|
| CP-A3-1 | remember→lookup 闭环；权限恰 0600；不记住不落盘 | 探针：roundtrip + `stat.S_IMODE==0o600`（含二次 O_TRUNC 覆盖写后不漂移，开发用例）+ 落盘 JSON schema 逐字对账 + 全新目录跑 lookup/load/mask 后断言文件不存在 | PASS |
| CP-A3-2 | gitignore 覆盖；损坏/缺失 → None + WARNING 非静默 | `git check-ignore -v` 命中 `.gitignore:8 workspace/`；探针：缺失→None+WARNING、非法 JSON→lookup None + load {} + WARNING≥2、顶层非 dict / 条目非 dict 同样 None；损坏内容（含 token 碎片）不进日志 | PASS |
| CP-A3-3 | 已记住+进程内敏感值全 mask；非敏感不 mask；子串/多值/长短混合无残留 | 探针：短值为长值前缀场景输出 `a=**** b=**** c=**** d=plain-dataset` 无 `123456` 尾巴残留；会话旁路（未记住）命中；非敏感保留；None/空串透传 | PASS |
| CP-A3-4 | 无凭证仍 GIT_TERMINAL_PROMPT=0；ASKPASS 0700 落 workspace、token 不出 env 值/命令行；hf_token 双变量 | 探针：无凭证 env 恰 `{"GIT_TERMINAL_PROMPT":"0"}` 且零脚本残留；ASKPASS 0700 + 路径在 workspace 下 + token 不在任何 env 值；**脚本 /bin/sh 实跑**：含 `'` `"` `` ` `` `$()` `;&|` 的 token 原样回显（命令注入不生效），带 prompt 实参（git 真实调用形态）同样返回 token；hf 双变量 + GIT_TERMINAL_PROMPT 并存 | PASS |
| CP-A3-5 | 模块 logger 全输出无 value 明文 | 双证：grep 静态审计 11 条日志格式串仅含 path/purpose_key/keys/error 类型；探针 DEBUG 级全捕（含损坏路径 WARNING 分支）4 组敏感值零命中；开发用例 caplog 同口径 | PASS |
| CP-D2-1 | 架构师确认记录在案；不改白名单则既有 9 条零弱化 + mask 覆盖面结论落档 | 定案 (a')-修正版全文固化于 `test_sprint4_d2.py` 模块头（方案+实证+否决理由+YAGNI 挂账）；`PIP_`/`LC_` 前缀仍在白名单（探针+用例双证）；既有 isolation 9 条 diff 实证字节级零修改且对新代码全绿；非凭证 PIP_INDEX_URL/PIP_CACHE_DIR/PIP_TIMEOUT 继续透传（探针 6 变量保留实证） | PASS |
| CP-D2-2 | 构造 `user:token@` 环境，token 不入沙箱子进程可见 env / 显式注入接管 | 开发用例 Popen spy 实证 prepare_venv 全路径子进程 env 无 PIP_INDEX_URL、无 token 明文、剔除 WARNING 只打变量名；探针补证：user:pass / token-only / 多 URL / 大写 HTTPS_PROXY 四形态全剔除，token 不在 env JSON 任何位置；extra_env 显式注入同名变量可见（正规通道接管，与 CP-D1-2 合并语义一致） | PASS |

## 规格口径记录（下游消费必读）

**`mask_value` 实际签名为 `mask_value(text)`（单参），与架构 §9.4 旧签名 `mask_value(text: str, secrets: Dict[str, str]) -> str` 不一致；以 dev-plan §4 A3 接口表（`mask_value(text) -> str`）为准。** 敏感值全集改为函数内部自取（`.secrets` 中 `is_sensitive=True` 项 ∪ 进程内 sensitive set），调用方**不需要也不能**传 secrets 参数。下游 B1/C1/E1 若按架构 §9.4 落点表写 `mask_value(logs, load_all_secrets())` 会直接 TypeError——消费时按单参调用，验收 C1/E1 时需核对调用形态。建议架构师在 §9.4 补一行勘误（非阻断）。

## 安全补强清单（13 条，攻击者视角；反过度工程——只补真实攻击面）

| 用例 | 落点 | 攻击面 / 价值 |
|---|---|---|
| `test_hardening_mask_value_secret_with_regex_special_chars` | A3 | 真实 token 常含 `+/=$` 等字符；锚定 str.replace 字面量语义，防未来重构为未 escape 的 re.sub（正则注入/误匹配）；含形近文本不误杀反向断言 |
| `test_hardening_mask_value_cross_overlapping_secrets_invariant` | A3 | 等长交叉重叠敏感值排序不确定；锚定不变量「任何已知敏感值不得完整明文出现」 |
| `test_hardening_mask_value_empty_sensitive_entry_no_explosion` | A3 | 用户提交空输入被「记住」→ 空串 str.replace 会在每字符间插 `****` 摧毁输出；实证 `entry.get("value")` 真值守卫生效 |
| `test_hardening_askpass_host_sanitized_no_path_escape`（×3 参数：`../../evil` / 空 host / `host;rm -rf /`） | A3 | purpose_key 源自 LLM 工具调用属不可信输入；实证脚本 resolve 后仍在 workspace 正下方、文件名无 `/`、空 host 落 `default`、脚本仍正确回显 |
| `test_hardening_remember_relocks_preexisting_loose_permissions` | A3 | `.secrets` 预存 0666（手工/历史残留）：os.open mode 仅创建时生效，实证显式 chmod 重锁 0600 |
| `test_hardening_uppercase_named_allowlist_var_also_filtered` | D2 | 大小写绕过：大写 HTTPS_PROXY 带 token-only userinfo 同样剔除（值级过滤与变量名无关）+ WARNING 无 token |
| `test_hardening_env_level_userinfo_boundary`（×5 参数） | D2 | env 级端到端锚定（非仅正则单元）：IPv6 无 userinfo / 路径含 `@` / 空 userinfo 三类合法运维配置不误杀断源；IPv6 userinfo / git+https userinfo 两类真凭证不放行 |

补强未覆盖且判定不补的点（威胁模型评估）：`.secrets` 符号链接 / 并发写——local venv 沙箱不可信代码与主进程同 uid，可直接读写任意同权限文件，symlink 攻击不新增攻击面（固有边界，架构已知）；token 含换行——git PAT 字符集不含换行，纯理论场景。

## 结果摘要

- sp4 A3+D2 两文件：**53 passed**（开发 40 + 补强 13），0.12s
- isolation 全套：**13 passed**（既有 9 字节级零修改 + D2 增量 4）
- 独立探针：A3 25 项 / D2 28 项全 PASS
- 全量非 e2e 回归：**1197 passed / 0 failed / 37 skipped / 43 deselected**（e2e+sandbox_real）
- 警告：1 —— langgraph `checkpoint/serde/encrypted` 的 `LangChainPendingDeprecationWarning`（库级、既有、与本批无关）

## 连跑稳定性

- 3 次连跑：1197 / 1197 / 1197 passed（56.39s / 56.05s / 55.93s），skipped 恒 37、deselected 恒 43，**0 退化 0 flaky**
- 数字对账：基线 1140（2026-07-02 A1/A2/D1 验收实测，commit 449fa21）+ 开发新增 44（A3 27 + D2 13 + isolation 4）+ 补强 13 = 1197，精确吻合

## 凭证纪律核查

- 全套测试（探针 + 单套 + 3 次全量）跑完后，真实 `workspace/` 递归查找（maxdepth 3）零命中 `.secrets` / `.git_askpass_*`；探针一律 tempfile、测试一律 tmp_path + monkeypatch config.WORKSPACE_DIR
- 日志明文审计：静态（11 条 logger 格式串）+ 动态（DEBUG 级全捕含损坏/多凭证 WARNING 分支）双证零泄漏；D2 剔除 WARNING 实测消息只含变量名
- `git status` 与验收前一致（仅预期 5 文件），无意外产物

## 失败排查

无失败。

## 后续动作 / 遗留项（均非阻断）

- **L-A3-01（B1/C1/E1 验收必查）**：`mask_value` / `build_credential_env` 均无 workspace_dir 入参，`.secrets` 读取与 ASKPASS 脚本落点固定基于 `config.WORKSPACE_DIR`（运行期动态读取）。若运行期 `state["workspace_dir"]` 为自定义路径（`create_initial_state` 支持 override），remember 到自定义路径的敏感值不会被 `mask_value` 看到（进程内 set 可兜住本会话，但重启恢复后 `.secrets` 读取会 miss）。dev-plan 签名口径如此，非 BUG；下游消费方需保证 remember/lookup 与 mask 的 workspace 基准一致。
- **L-A3-02（口径勘误建议）**：架构 §9.4 `mask_value(text, secrets)` 旧签名与实现不一致（详见上文专节），建议架构师补勘误行，防 E1/E3 开发照抄文档调用形态。
- **L-D2-01（覆盖面结论）**：值级否决仅覆盖 URL userinfo 形态（架构师定案边界）；当前白名单集合内无裸凭证类变量，覆盖面成立。`ssh://git@host` 形态会被剔除（git@ 为用户名非凭证）属可接受假阳性——当前白名单无携带此形态的变量；若未来白名单加入 GIT_SSH 类变量需复核。
- **L-D2-02（G3 核对项）**：CP-D2-1 "架构师确认记录在案" 当前落点 = `test_sprint4_d2.py` 模块头；G3 handoff 交付时需将 D2 定案全文同步进 handoff（dev-plan §8 已列），主控届时闭环 TODO HOTFIX-2 备忘挂账项。
- 沿用既有遗留：`tests/test_paper_intake.py` main 风格脚本按惯例 `--ignore`；langgraph 库级 PendingDeprecationWarning。
- 下一次触发条件：B1（interaction_tools）交付后验收其对 A3 三接口的消费形态（含 L-A3-01/02 核对）；E3 交付后回归 L-D1-01（install_log mask 兜底）挂账项。
