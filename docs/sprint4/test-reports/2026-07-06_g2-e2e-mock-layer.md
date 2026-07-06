# 测试执行报告 - G2 e2e 零配额部分（mock 层 + 真跑骨架就绪）

- **日期**：2026-07-06（本地时区）
- **执行人**：@测试工程师代理
- **Sprint**：sprint4
- **触发原因**：dev-plan §4 任务 G2 的零配额子集——CP-G2-1（AC-S4-13 三 interrupt 串行 mock 层）/ CP-G2-2（AC-S4-11 哨兵脱敏 grep 四落点）/ CP-G2-3（AC-S4-08 mock 注入断言 + 真实链路转正骨架）/ CP-G2-4（本报告 + 待授权真跑清单）。真跑转正部分**不在本次范围**（待 Maria 授权后由主控执行）。
- **commit**：47f0a9c（工作区仅新增 `tests/test_sprint4_e2e.py` 与本报告，未 commit，主控收口）

## 验收结论

| 检查点 | 结论 |
|---|---|
| CP-G2-1 三 interrupt 串行（mock 层）+ 连跑 3 次 | **PASS**（3 条用例；interrupt 序列 / payload 契约 / resume 路由 / 副作用幂等全绿，3 连跑零抖动） |
| CP-G2-2 哨兵 grep 四落点 | **部分 PASS + 2 个实证缺口**：落点 ①生成代码 / ③报告 / ④caplog 零明文 PASS；GlobalState 投影与落点 ②checkpoint DB 发现明文泄漏（F1/F2，已编码为 `xfail(strict=True)` 并挂 BUG/裁决号，见"发现与开方"） |
| CP-G2-3 mock 注入断言 + 真跑骨架就绪 | **PASS + 骨架就绪**（subprocess spy 全链路透传断言 2 条全绿；3 条 e2e 骨架 `--collect-only -m e2e` 收集验证通过、默认回归 deselected、mark 审计用例守门） |
| CP-G2-4 报告归档 + 待授权真跑清单 | **本报告**（清单见下） |

## 执行范围

- 命令（全程零真实外呼、零 LLM/deepxiv 配额；**未**显式传 `-m e2e` / `-m sandbox_real` 跑任何测试）：
  - `.venv/bin/pytest tests/test_sprint4_e2e.py -v`（新文件 mock 层）
  - `.venv/bin/pytest tests/test_sprint4_e2e.py --collect-only -q -m e2e`（骨架收集验证，仅 collect）
  - `.venv/bin/pytest --collect-only -q -m e2e` / `--collect-only -q`（全库收集对账）
  - `.venv/bin/pytest tests/ -q` × 3（全量非 e2e 回归 3 连跑）
  - 一次性探针 `/tmp/g2_probe.py`（定位哨兵在 state 字段与 DB 表/命名空间的精确通道，已删除）
- 是否包含 e2e：**否**（真跑待授权）。凭证 env 仅提及未消费：LLM_API_KEY / DEEPXIV_TOKEN（.env 在仓，故 e2e 骨架 skipif 在本机不生效——**唯一防线是不跑**，本次全程遵守）。

## 结果摘要

- 新增文件：`tests/test_sprint4_e2e.py`，收集 **13** 条 = 非 e2e **10** 条（8 passed + 2 xfailed）+ e2e 骨架 **3** 条（deselected）。
- 全量非 e2e 回归 3 连跑：恒 **1385 passed / 0 failed / 37 skipped / 46 deselected / 2 xfailed**（60.9s / 61.2s / 61.1s），**0 flaky**。
- 对账：passed 1385 = 基线 1377（G1 收口，commit 47f0a9c）+ 本次 8；xfailed 2 = 本次新增（既有 0）；deselected 46 = 基线 43（e2e+sandbox_real）+ 本次 e2e 骨架 3；`-m e2e` 收集 46/1470。精确闭合。
- 警告恒 1：langgraph 库级 `LangChainPendingDeprecationWarning`（sp1 起既有挂账，非项目代码，G 阶段升级检查单已转记）。

## CP-G2-1：三 interrupt 串行（AC-S4-13 mock 层）

harness：mock LLM（`DispatchScriptLLM`，按 SystemMessage 身份短语分发 planning/coding/execution 三节点脚本；B2 范式纯数据字段 + messages 路由，replay 安全）+ **真实 `build_graph()`**（planning/coding/execution/reporting 全真实节点，仅上游 3 节点 fake）+ **真实 SqliteSaver(WAL)**。`MAX_FIX_LOOP_COUNT` patch 为 1（保留"触顶"语义、压缩链长：恰 1 个真实修复回合后触顶）。

同一 thread_id 链路：`interrupt#1(planning) → approve → interrupt#3(coding 内 request_user_input, 非敏感) → resume 值 → execution fail → fix#1 → coding 修复回合 → execution fail → 触顶 → self-loop commit 边界 → interrupt#2(dev_loop_failure) → export_code → reporting(降级报告落盘) → END`。

关键断言（全绿）：
- interrupt 种类序列恰 `["planning", "user_input_request", "dev_loop_failure"]`，每次暂停恰 1 个 pending interrupt（互不串扰）；
- payload 契约：#1 十键集逐字（revise_count=0 / soft_hint_threshold / max_total_llm_calls…）；#3 **恰四键**（§7.1，purpose_key 空串规整 None）；#2 十键集逐字（error_category=import / auto_fixable=True / fix_loop_count=1 / options 三态）；
- 路由：暂停位置依次 planning/coding/execution，export_code 后 END + 降级报告真落盘；
- 触顶前 `_dev_loop_route="await_dev_loop_interrupt"`（L-C3-01 self-loop 命门旁证）；
- 副作用幂等（AC-S4-14 链内复证）：write 双通道 pause#2 时恰 1、终态恰 2（interrupt#3 resume 不重放 + 修复回合恰 1 次）；sandbox 恰 2 跑（guard 重入与 interrupt#2 resume 零新增）；fix_loop_history 恰 1 条 import；
- **连跑 3 次零抖动**（interrupt 序列 / 副作用计数 / 终态逐项一致）。

顺带首证：真实 coding wrapper 在同一 thread 内二次进入（修复回合）时子图 checkpoint 命名空间按 task 隔离、不与首轮串扰——此前该形态仅存在于未跑的 sp3 real-2 真跑用例中。

## CP-G2-2：哨兵脱敏 grep（AC-S4-11）

哨兵 `FAKE-TOKEN-sp4-e2e-sentinel-*`（非真凭证）经 execution 内 `request_user_input(is_sensitive=True, purpose_key="git_credential:github.com")` interrupt#3 + `resume={value, remember: True}` 进入系统；完整 mock 修复循环（fetch 认证失败→重试成功（stdout 带原文哨兵）→train import 失败（stderr 带原文哨兵）→修复回合→成功收尾→reporting）。coding 修复回合脚本为**忠实复读器**：把观察到的修复上下文原样写进代码注释（上游任一 mask 环节失效即被落点 ① 捕获）。

| 落点 | 结果 |
|---|---|
| ① 工作目录生成代码 | **零明文 PASS**（含阳性对照：复读进代码的 stderr_tail 携带 `ModuleNotFoundError` + `****` 占位符，证明反馈确实流经且已脱敏） |
| ② checkpoint DB 字节 | **FAIL → xfail(strict=True)**（F1+F2，见下） |
| ③ 报告 Markdown | **零明文 PASS** |
| ④ caplog（DEBUG 全捕获） | **零明文 PASS**（非空捕获，防假绿） |
| GlobalState 终态序列化（加测投影） | **FAIL → xfail(strict=True)**（F1 单因） |
| mask 阳性对照 | PASS（历史帧 exec#1 logs：`Bearer` 证据行 + `****` + `exit=128` 失败证据保留、零哨兵；注意修复回合会**覆盖** execution_result，含哨兵行只在 state history 帧） |
| `.secrets`（设计内明文，不在落点清单） | 权限 **0600** 断言 PASS，条目 `{value, is_sensitive:true}` 正确 |
| GIT_ASKPASS 脚本（设计内明文） | 权限 **0700** 断言 PASS，且不落在代码目录 |

## CP-G2-3：AC-S4-08 mock 注入断言 + 真跑骨架

**mock 注入主证（PASS）**：种子 `.secrets`（git + hf 双凭证哨兵）→ 真实 execution 节点 → 真实 `_run_execution_agent` → `build_credential_env` → 真实 `run_in_venv` → 真实 `_run_subprocess` → **patch `subprocess.Popen` 处捕获最终子进程 env**（最深零配额边界）。断言：
- `GIT_TERMINAL_PROMPT=0` 无条件注入（R-S4-08）；`HF_TOKEN` + `HUGGING_FACE_HUB_TOKEN` 双变量；
- `GIT_ASKPASS` 指向 workspace 下 0700 脚本（内容含 token）且 **git token 不出现在任何 env 值中**（间接注入命门）；
- 命令改写到 `.venv/bin/python`（确定性推导链路旁证）。

**白名单收口旁证（PASS）**：宿主环境的 `LLM_API_KEY` / `DEEPXIV_TOKEN` / `OPENAI_API_KEY` / `PYTHONPATH` / `VIRTUAL_ENV` 均未透传进沙箱 env（HOTFIX-2/D2 语义在真实链路上首次全链验证）；`PATH` 等白名单基础变量与显式注入凭证共存。

**真跑骨架就绪**：`TestSprint4RealChainE2E` 类级 `pytestmark = [e2e, skipif(凭证)]`，3 条骨架用例代码完整可跑（docstring 均注明"真跑待 Maria 授权"）；`--collect-only -m e2e` 收集 3/13 验证通过；默认回归 deselected；`test_cp_g2_3_real_chain_skeleton_marks`（mock 层）对 mark/用例集做元断言守门，防止骨架被误改后漏进默认回归。

## 失败排查

初跑 3 failed，逐一处置：

1. `test_cp_g2_2_mask_engaged_and_bydesign_stores` —— **测试代码观测点错误**（自行修复）：修复回合成功后 `execution_result` 被 exec#2 结果**覆盖**，终态 logs 不再含 exec#1 的哨兵证据行，`****` 阳性对照落空。修复：从 `graph.get_state_history()` 历史帧提取 exec#1 的 logs 做阳性对照。修复后 PASS。
2. `test_cp_g2_2_sentinel_not_in_global_state_dump` —— **生产代码缺口 F1（BUG-S4-G2-01）**，见下。处置：`xfail(strict=True)` 挂 BUG 号，修复后翻红提醒摘标转正。
3. `test_cp_g2_2_sentinel_checkpoint_db_zero_plaintext` —— **F1 + 机制固有缺口 F2（ADJ-S4-G2-02）**，见下。处置同上（F1 修复后仍受 F2 阻塞，直至裁决落地）。

## 发现与开方（留主控裁决）

### BUG-S4-G2-01（F1）：representative_stderr 原文进 NodeError.error_detail，绕过全部 mask 落点

- **复现路径**：`.venv/bin/pytest tests/test_sprint4_e2e.py::test_cp_g2_2_sentinel_not_in_global_state_dump`（去掉 xfail 标记即红）
- **期望行为**：AC-S4-11"全链路无凭证明文"；架构 §9.4 敏感值经 `mask_value` 后才可进 GlobalState。
- **实际行为**：`core/nodes/execution.py::_map_execution_result`（L1437-1444）把 `feedback.representative_stderr`（`_classify_execution` 从**收集器原文** stderr 截取，未过 mask）直接作为 `make_node_error(...)` 第 4 参写入 `NodeError.error_detail` → 入 GlobalState → 随 checkpoint 入库。探针实证：state 全字段扫描**仅** `node_errors` 一个通道命中；DB 命中 root ns 的 `checkpoints` 表 + `writes` 表（channel=node_errors）。
- **既有覆盖为何没抓到**：CP-E3-4 只断言了 logs（`_build_execution_result` 有 mask）与 interrupt#2 payload（`_build_dev_loop_interrupt_payload` 有 mask）两个投影点；E4 的 credential 用例断言了 `er["logs"]` 与 payload，未查 node_errors。三个投影点两个有 mask、恰第三个漏了。
- **影响范围**：任何"敏感值已注册后 sandbox 再次失败"的回合（典型：凭证到手重试仍失败、或失败输出回显了凭证），error_detail 即携明文进入 state/checkpoint/UI 可见面。不阻塞其它用例。
- **建议修复方向**（供开发判断）：`_map_execution_result` 写 node_errors 前对 `feedback.representative_stderr` 过 `mask_value`（一行）；或在 `_classify_execution` 构造 `rep` 时统一 mask（覆盖 `_feedback_from_committed_result` 重建路径则需同步检查）。修复后**摘除**本用例 xfail 标记即回归转正（strict xfail 会翻红提醒）。

### ADJ-S4-G2-02（F2）：敏感 resume 值明文进 checkpoint DB（机制固有，需架构裁决）

- **复现路径**：`.venv/bin/pytest tests/test_sprint4_e2e.py::test_cp_g2_2_sentinel_checkpoint_db_zero_plaintext`
- **事实**（探针定位，F1 通道之外还有两条）：
  1. 子图 ns `execution:<task_id>` 的 **messages channel**——`request_user_input` 返回值即裸哨兵字符串，作为 ToolMessage 内容随子图 checkpoint 落库（这正是 B2 实证的"messages 经 checkpoint 恢复完整"的另一面）；
  2. root 与子图 ns 的 **`__resume__` channel**——`Command(resume={"value": <敏感值>})` 作为 pending write 落库。
- **矛盾点**：`core/tools/interaction_tools.py` docstring 声称"敏感值只作为 ToolMessage 内容回 agent，**绝不进 GlobalState / checkpoint**"——前半句为真（GlobalState 确实只有 F1 一个泄漏点），后半句与 LangGraph 机制事实不符：ToolMessage 本身就在被 checkpoint 的子图 messages 里。dev-plan CP-G2-2 的落点 ② 因此在当前设计下不可能零明文。
- **裁决选项**（供架构师）：(a) 降格为已知限制——checkpoint DB 属本地 0600 可控面（可顺带断言 db 文件权限），文档改口"不进 GlobalState；checkpoint 依赖文件权限保护"，xfail 转为 characterization；(b) 自定义 serde 层在入库前对已注册敏感值做 mask（代价：resume 精确恢复语义与工具返回值一致性需评估，风险高）；(c) 敏感值不走 ToolMessage 原文回传，改传 `.secrets` 引用键（改动大，涉及 agent 用值方式）。测试侧倾向 (a)+文档修正，但不替代架构判断。
- **影响范围**：仅 checkpoint DB 字节层；四个用户可见投影点（代码/报告/日志/payload）在 F1 修复后即全零明文。

### 信息项（非阻断）

- 修复回合会**覆盖** `execution_result`，前一回合的执行证据只存在于 checkpoint 历史帧——写断言/做 UI 展示时不要假设终态含全链证据（本次测试代码先踩后改，留档提醒）。
- e2e 骨架的 skipif 在本仓（.env 有真凭证）不生效，真跑防线只有"不传 `-m e2e`"——与 G1 报告口径一致，再次确认 pytest.ini addopts 防线有效。

## 待授权真跑清单（CP-G2-4，供主控汇总给 Maria 拍板）

| # | 动作 | 前置 | 预估时长 | 预估配额消耗 | 复跑要求 |
|---|---|---|---|---|---|
| 1 | `TestSprint4RealChainE2E::test_real_s4_1_three_interrupt_serial_llm_compliance`（AC-S4-13 转正） | LLM_API_KEY + DEEPXIV_TOKEN（.env 已具备） | ~3-8 分钟/次 | 1 条真实链路 LLM token（intake→planning 全真 + coding/execution 真 LLM）；deepxiv：HippoRAG 已缓存则近零 | 3~5 次（interrupt#3 依赖 LLM 服从度，按实测复现率定性） |
| 2 | `test_real_s4_2_credential_injection_private_repo_clone`（AC-S4-08 转正） | 额外 env：`SP4_E2E_PRIVATE_REPO_URL` + `SP4_E2E_GIT_TOKEN`（需 Maria 提供真私有仓库 + PAT） | <2 分钟 | **零 LLM/deepxiv 配额**（纯 git 网络） | 1 次即可（确定性） |
| 3 | `test_real_s4_3_sentinel_masking_with_real_llm`（AC-S4-11 真实 agent 复述行为转正） | LLM_API_KEY（不需 deepxiv） | ~2-5 分钟/次 | execution 子图 ~5-10 轮 LLM 调用 | 3~5 次（同服从度口径） |
| 4 | （既有池，非 G2 新增）sp3 `TestRealChainE2E` real-1~5 转正 | 同 #1 | 见 sp3 报告 | real-1 为 smoke 首选（fail-fast 验凭证+deepxiv 可达） | 见 sp3 报告 |

建议执行顺序：先 sp3 real-1 或 #1 做 smoke fail-fast（验凭证有效 + deepxiv 可达 + 装配正确），全绿后再补跑其余；#2 待 Maria 提供私有仓库资源后单独跑。

## 后续动作

- **主控**：BUG-S4-G2-01 转 @全栈开发代理修复（修复后摘 `test_cp_g2_2_sentinel_not_in_global_state_dump` 的 xfail 并回归本文件）；ADJ-S4-G2-02 转架构师裁决（裁决落地后同步处理 DB 用例与 interaction_tools docstring）；TODO.md / dev-plan CP-G2-1/2/3 勾选由主控统一收口（CP-G2-2 建议标注"四落点中 ①③④ PASS，② 挂 F1/F2"）。
- **真跑转正**：按上表待 Maria 授权，由主控执行并另立 `2026-07-XX_g2-e2e-real.md` 归档跑数/耗时。
- G3 handoff 引用本报告的 AC-S4-08/11/13 结论与 xfail 挂账清单。

---

## 发现处置结果（2026-07-06 追加，当日闭环；上方正文为处置前历史记录，保持原样）

- **执行人**：@测试工程师代理（P1 处置，主控指令 + 架构师授权断言粒度）
- **commit**：47f0a9c 工作区（主控并行落地生产侧修复/加固，未 commit）

### BUG-S4-G2-01（F1）：已修复转正

- **修复**（主控直修，沿 L-B1-02 先例，比原报告建议的单点更宽）：`core/nodes/execution.py` 全部 **5 处** tainted summary/stderr 落 state/日志点统一 `mask_value`——L1380 errors 列表、L1441/1443 NodeError message+detail、L1449 warning 日志、L1494 FixLoopRecord.error_summary（L1534 注释自证 summary 可能内嵌 stderr 原文，故 message 与 summary 也一并 mask）。
- **测试转正**（主控执行）：`test_cp_g2_2_sentinel_not_in_global_state_dump` 先实证 XPASS(strict) 翻红、后摘 xfail 转绿，现为 F1 的 GlobalState 投影回归锚点。
- **修复后探针复核**（测试代理，2026-07-06 二次探针，已删）：终态 state 全字段零哨兵；checkpoints 表 **root ns 命中清零**（修复前 root+子图两 ns 命中 → 修复后仅子图 ns 10 行）；writes 表命中收敛为恰 3 处裁决内通道（root `__resume__` ×1、子图 `__resume__` ×1、子图 `messages` ×1），**node_errors 通道零命中**。F1 修复在 DB 字节层彻底。

### ADJ-S4-G2-02（F2）：架构裁决 (a+)，降格为已知限制

- **裁决**（2026-07-06 架构师）：checkpoint DB 字节级零明文降格为已知限制 + 文档修正 + `checkpoints.db` chmod 0600（唯一代码加固，主控落地）。论证：能读 DB 的主体同样能读明文 `.secrets`，DB 级脱敏净收益≈零；mask serde 破坏 checkpoint 往返一致性（B2 实证 messages 回读真值是 resume 精确恢复的机制前提），**永久排除**；引用键传值留作多用户部署升级路径。
- **测试处置**（测试代理）：原 `test_cp_g2_2_sentinel_checkpoint_db_zero_plaintext`（strict xfail）摘标改写为 characterization 用例 **`test_cp_g2_2_sentinel_checkpoint_db_known_limitation`**，两条断言：
  1. **锚定现状**：DB 字节含哨兵——若未来 langgraph 序列化/checkpoint 行为变化使其消失，翻红提醒同步清理 architecture 勘误 / interaction_tools docstring / 本用例（届时落点 ② 可转正零明文断言）；
  2. **收窄后的真规格**（SQL 级探针，粒度超出主控最低要求）：(2a) checkpoints 表 root ns 零哨兵、命中仅限内嵌子图 ns（裁决内通道 ①）；(2b) writes 表逐行审计，命中必须全部落在白名单（`__resume__` 任意 ns / 内嵌子图 ns `messages`），并显式断言 **node_errors 业务通道零哨兵**（F1 的 writes 侧回归锚点）——白名单外任何命中即"未知第三通道"，立即翻红升级排查。
- 模块 docstring F2 条目同步改为裁决记录；段标题由"xfail 挂账"改为"处置记录"（文件内已无 xfail）。

### 处置后数字

- targeted `tests/test_sprint4_e2e.py` 连跑 3 次：恒 **10 passed / 0 failed / 0 xfailed / 3 deselected**（2.06s / 2.21s / 2.41s），0 flaky（原 8 passed + 2 xfailed → 两条均转为可过用例）。
- 全量收口数字以主控为准（主控并行改动 interaction_tools / test_sprint4_b1 / checkpointer / architecture，主控报告其修复轮全量 3 连跑恒 1386 passed / 37 skipped / 46 deselected / 1 xfailed / 0 failed；本次 DB 用例改写后 xfailed 应归零、passed +1，由主控全量复核确认）。

### 遗留

- 落点 ② 的"零明文"语义正式由已知限制承接：防护面 = `.secrets` 0600 + askpass 0700 + checkpoints.db 0600（主控落地）+ workspace gitignore；四个用户可见投影点（代码/报告/日志/payload）+ GlobalState 全部零明文，均有回归锚点在案。
- 真跑转正清单不受本次处置影响，仍按上表待 Maria 授权。
