# 测试执行报告 - G2 真跑转正（AC-S4-13/11 转正 + Prompt Cache 双测）

- **日期**：2026-07-06（本地时区）
- **执行人**：@主控（真跑执行，Maria 已授权"全跑"）；@测试工程师代理（据实落档 + harness 直修复核，沿 sp2 凭证补跑报告先例——主控执行、测试代理落档，**本次未重跑任何 e2e**）
- **Sprint**：sprint4
- **触发原因**：dev-plan §4 任务 G2 真跑转正（`2026-07-06_g2-e2e-mock-layer.md` 待授权清单 #1/#3/#4 + CP-G3-1 Prompt Cache 双测）；#2（real_s4_2）资源依赖仍挂
- **commit**：47f0a9c 工作区（真跑相关改动未 commit，主控收口）
- **原始日志**：主控留档 `$CLAUDE_JOB_DIR/tmp/`；spike 原始指标 JSON 见 `workspace/runs/`

## 结论速览

| 项 | 结论 |
|---|---|
| smoke（sp3 real-1） | **PASS**（fail-fast 验证凭证有效 + deepxiv 可达 + 全链装配） |
| AC-S4-13 三 interrupt 串行（real_s4_1） | **转正 PASS**：有效 3/3，LLM 服从度 3/3；前置 3 次作废跑（2 例 harness 缺陷，root cause 与修复见"失败排查"） |
| AC-S4-11 真实 agent 复述脱敏（real_s4_3） | **转正 PASS**：3/3，agent 立即服从问询、复述零哨兵泄漏 |
| AC-S4-08 真凭证注入（real_s4_2） | **未跑**，待 Maria 提供 `SP4_E2E_PRIVATE_REPO_URL` + `SP4_E2E_GIT_TOKEN`（mock 注入断言层已于 mock-layer 报告 PASS） |
| CP-G3-1 coding Prompt Cache 回归 | **双门 PASS**：R_after=0.8726（脚本 sp2 门 0.7286 ✓；dev-plan G3 规格门 0.9008×0.95=0.8558 ✓） |
| CP-G3-1 execution Prompt Cache 首测 | **基线建立**：R_after=0.8906，建议守门 0.8906×0.95=**0.8461** |

## 执行范围与配额入账

- 真跑命令（主控执行）：`-m e2e` 定向跑 `TestSprint4RealChainE2E`（real_s4_1 / real_s4_3）+ sp3 real-1 smoke；`SPIKE_F3_AUTHORIZED=1` 跑两个 prompt-cache spike。
- 凭证：LLM_API_KEY + DEEPXIV_TOKEN（env 名，不写值）。
- 配额纪律执行情况：先 smoke fail-fast ✓；靶 HippoRAG（deepxiv 缓存命中，read_section 近零增量配额）✓；sandbox 全 mock 不真跑训练 ✓；spike 零 deepxiv 配额（预置 state 不跑上游）✓。
- 真实 LLM 耗时入账（含作废跑，如实）：

| 跑次 | 结果 | 耗时 |
|---|---|---|
| smoke sp3 real-1 | PASS | 233.47s |
| real_s4_1 作废 run1（harness 缺陷 1） | FAIL（作废） | 891.28s |
| real_s4_1 作废 run2（harness 缺陷 1） | FAIL（作废） | 760.52s |
| real_s4_1 作废 run3（harness 缺陷 2，但实证 3 项机制，见下） | FAIL（作废） | 405.78s |
| real_s4_1 有效 #1/#2/#3 | **PASS ×3** | 278.55s / 328.99s / 339.29s |
| real_s4_3 有效 #1/#2/#3 | **PASS ×3** | 12.24s / 15.43s / 13.20s |
| coding PC spike（3 轮采集段） | 双门 PASS | 228.11s（<300s，TTL 安全） |
| execution PC spike 作废首跑（AttributeError 瞬败） | 作废 | ≈0（3 轮全瞬败，**零配额**，作废 JSON 已删） |
| execution PC spike 有效跑（3 轮采集段） | 基线建立 | 21.80s（<300s，TTL 安全） |

作废跑合计 2057.58s 真实 LLM 时长——绝大部分烧在 harness 缺陷 1 的修复循环空转上（见失败排查），教训已记入"经验沉淀"。

## AC-S4-13 转正详情（real_s4_1，有效 3/3）

链路（真实 LLM + 真实 deepxiv 上游 + mock sandbox 状态机）：`interrupt#1(planning 真实暂停) → approve → 真实 coding → 真实 execution agent 遇 mock 认证失败 → 主动 request_user_input（interrupt#3）→ resume(哨兵) → CUDA OOM（不可修复）→ interrupt#2 → terminate → END`。

- **LLM 服从度 3/3**：三次有效跑中真实 agent 面对认证失败均主动调 `request_user_input`（dev-plan §G2 复现率口径：≥50% 连跑 3 次全绿即达标，实测 100%）。
- 断言口径（harness 修复 2 后收敛到 AC 本质）：三种 interrupt 同 thread 各 ≥1 次 + approve 后 planning 不再现（路由无串扰）+ terminate 干净到 END。
- 与 mock 层 CP-G2-1（固定剧本、payload 键集逐字断言、3 连跑）互补：mock 层锁契约细节，真实层验 LLM 服从度与真实链路装配——**AC-S4-13 mock+真实双层转正**。

## AC-S4-11 转正详情（real_s4_3，3/3）

真实 LLM execution agent + mock sandbox（哨兵经 interrupt#3 resume 注入后出现在 mock stdout）：agent 立即服从问询（3/3），复述行为零哨兵泄漏（GlobalState 序列化 / interrupt payload 零明文）。与 mock 层四落点 grep（①③④ PASS + ② 裁决降格 ADJ-S4-G2-02）合并——**AC-S4-11 转正 PASS（含已知限制在案）**。

## CP-G3-1 Prompt Cache 双测（数字已与 JSON 落盘核对一致）

**coding 回归**（`scripts/spike_coding_prompt_cache.py`，`SPIKE_F3_AUTHORIZED` 双保险）：
- R1=93.56%（cold）/ R2=82.70% / R3=91.82% → **R_after = 0.8726**
- 双门判定：脚本 sp2 门 0.7286 ✓ PASS；dev-plan G3 规格门 = sp3 基线 0.9008×0.95 = 0.8558 ✓ PASS
- 较 sp3 基线 0.9008 略降（-3.1%），与 C2 挂 7 工具（工具 schema 进前缀）+ ADJ-S4-G2-02 措辞 A docstring 修正的预期影响一致，在守门带内。
- JSON：`workspace/runs/spike-f3-coding-prompt-cache_20260706-042422.json`

**execution 首测（建基线）**（`scripts/spike_execution_prompt_cache.py`，主控新写，测试代理已复核见下）：
- R1=47.37%（cold）/ R2=R3=89.06% → **R_after 基线 = 0.8906**，建议守门 **0.8461**（×0.95）
- CP-E2-1 字节级一致断言（mock 层已锁）+ 本真实命中率基线 → execution prompt 的 Prompt Cache 守门闭环建立。
- JSON：`workspace/runs/spike-g3-execution-prompt-cache_20260706-190856.json`

## 失败排查（3 次作废跑 + 1 次脚本瞬败，root cause 如实入账）

### harness 缺陷 1（作废 run1/run2，合计 1651.80s）：workspace 隔离缺失

- **现象**：real_s4_1 首版 FAIL——agent 始终收不到 mock 认证失败，修复循环空转直至 retry_budget 20→0 烧穿。
- **root cause**：测试代理首版骨架漏调 `_isolate_workspace`（mock 场景 harness 有、e2e 骨架没抄全）：workspace 落 pytest tmp_path，违反 sandbox 护栏 3（work_dir 必须在 `config.WORKSPACE_DIR` 下）→ `prepare_environment` 永远"越界"失败 → 走 DEPENDENCY 可修复分类反复回 coding。**失败类型：测试代码（harness）bug，非产品缺陷、非 LLM 服从度问题。**
- **修复**（主控直修）：补 `_isolate_workspace` + `_prepare_code_dir` 预置 `.venv` 痕迹 + `prepare_venv` 确定性 fake（mock sandbox 本意，不真跑 pip）；`SandboxPrepareResult` import 同步补齐。
- **复核**（测试代理）：修复正确，fake venv 路径与预置痕迹一致（interrupt resume 后 python_exe 确定性推导链亦通）。

### harness 缺陷 2（作废 run3，405.78s）：固定剧本扛不住真实 LLM 非确定性

- **现象**：修复 1 后 run3 仍 FAIL——coding 真实 `run_command` 对真 github 用假哨兵 token clone 失败，agent **合理地**对同 purpose_key 发起第二次 interrupt#3 问询；固定 4 步剧本把 `terminate` 误打进 `request_user_input`，被非法 resume 契约降级为空串；真正的 interrupt#2 在 commit 边界重入后才升起，剧本已错位。
- **root cause**：测试代理首版骨架按确定性剧本写死 resume 序列，未给真实 LLM 的合理分支留余地。**失败类型：测试代码（harness）设计缺陷。**
- **修复**（主控直修）：改容忍循环——遇 #3 一律喂哨兵值、见 #2 才 terminate、上限 8 防死循环；断言收敛到 AC-S4-13 本质（三种 interrupt 各 ≥1 + 无串扰 + terminate 到 END）。
- **run3 的副产品价值**（虽作废但实证三点）：① LLM 服从度 ✓（主动二次问询正是服从纪律）；② interrupt#2 commit 边界重入幂等机制在**真实链路**上成立 ✓（L-C3-01 命门首次真实验证）；③ `request_user_input` 非法 resume 降级路径（返回空串 + WARNING）真实触发 ✓。

### execution PC spike 首跑瞬败（零配额）

- **现象**：3 轮全 `AttributeError` 瞬败（作废 JSON 已删）。
- **root cause**：`core/nodes/__init__` 把同名函数 `execution` 绑到包属性，`import core.nodes.execution` 形态拿到函数而非模块（callable 遮蔽陷阱，e3/sp3 已知坑）。**失败类型：脚本代码 bug；因瞬败发生在任何 LLM 调用前，零配额损失。**
- **修复**（主控）：改 `importlib.import_module`（e3 同款处置），并在脚本内留注释锚定该陷阱。

## harness 直修与新脚本复核（测试代理，本次 P1 复核任务）

**结论：两处直修与新脚本均正确可用、无阻断问题**；mock 层 targeted 复跑 10 passed / 0 failed（直修零回归），`-m e2e --collect-only` 仍 3/13。非阻断意见 5 条（不改动，留主控/后续维护定夺）：

1. **重言式断言**（real_s4_1）：`assert kinds[-1] == INTERRUPT_KIND_DEV_LOOP` 仅 break 路径可达、恒真；上限 8 耗尽路径会被 END 断言拦截但报错文案（"terminate 后应到 END"）与真实原因（8 轮未见 #2）不匹配。建议下轮维护用 for-else 给上限耗尽独立失败语义。该红一定红，判定正确性不受影响。
2. **coding run_command 未 mock**（real_s4_1）：真实 agent 可对真 github 发起 clone（run3 实证）——零配额但引入不受控网络外呼与时延方差（有效跑 278~339s 的方差部分来源）。发出的是 FAKE 哨兵值且 run_command 输出通道有 CP-C1-4 mask 覆盖，安全无虞；后续可选 patch `make_run_command_tool` 收掉方差（代价：损失 coding 侧真实工具行为覆盖，权衡留主控）。
3. **骨架 docstring 漂移**：`TestSprint4RealChainE2E` 类与 real_s4_1 的 docstring 仍写"真跑待 Maria 授权/本次仅 collect-only"——转正后属历史表述，建议主控收口时随 handoff 一并校准。
4. **spike 授权 env 命名**：新脚本沿用 `SPIKE_F3_AUTHORIZED`（历史名），两脚本共用一个闸门——授权 coding 回归即顺带解锁 execution 首测，粒度略损。本次一次性全授权无实际影响；未来如需分粒度可拆分 env 名。
5. **spike degraded 轮不排除统计**：异常/降级轮的 metrics 仍计入 R 值（`degraded` 字段有标记但不剔除）——本次 3 轮全成功无影响；未来复跑若见 degraded=true 轮需人工判读 JSON 后再采信 R_after。

复核确认项：`_METRICS_BUCKET` 跨模块共享为同一 list 对象（append 闭包引用 + clear 不重绑，复用正确）；`_patch_chat_openai_invoke` 幂等闸有效；R 口径与 sp2 S-3 / sp3 F3 一致；JSON 数字与主控口径逐项核对一致（含顺带补齐的 spike 采集段耗时 228.11s / 21.80s）。

## 经验沉淀（供后续 e2e 骨架编写）

1. **e2e 骨架必须与 mock 场景共用同一套 harness 基建**（workspace 隔离 / code_dir 预置 / sandbox fake 三件套）——缺陷 1 的 1651.80s 学费本质是"骨架抄 harness 没抄全"，且此类缺陷在 mock 层（不真跑）不可见，只有真跑才暴露。后续骨架交付时应把"与 mock harness 的差异清单"写进 docstring 供真跑前走查。
2. **真实 LLM 剧本要写成"容忍循环 + 本质断言"而非固定步数**——真实 agent 的合理分支（如同 key 二次问询）不是失败；断言应锚定 AC 语义不是执行轨迹。
3. 作废跑也要入账：run3 的三项机制实证（服从度 / commit 边界重入幂等 / 非法 resume 降级）具引用价值，已在上文留档。

## 后续动作

- **real_s4_2（AC-S4-08 真跑）**：仍挂，待 Maria 提供 `SP4_E2E_PRIVATE_REPO_URL` + `SP4_E2E_GIT_TOKEN` 后由主控补跑（<2 分钟，零 LLM/deepxiv 配额），另行追加归档。→ **已补跑转正，见下方补记**
- dev-plan CP-G2-1/2/3（真跑维度）与 CP-G3-1 勾选、TODO / handoff 更新由主控统一收口；本报告可作勾选依据（AC-S4-13/11 双层转正、AC-S4-08 mock 层 PASS + 真跑挂 #2）。
- execution PC 守门线 0.8461 建议由 G3 handoff 固化为规格数字。

---

## 补记（2026-07-07，主控）：real_s4_2 转正，6/6 全部完成

- Maria 提供私有仓库 `https://github.com/Maria0514/sky-take-out`（无凭证 ls-remote
  探测确认 private）+ fine-grained PAT（仅该仓库 Contents Read-only），经 `.env`
  注入后由主控执行。
- **real_s4_2 PASS（1.85s，1 次，零 LLM/deepxiv 配额）**：真 token 经
  `.secrets` → `build_credential_env` → `extra_env` → `GIT_ASKPASS` 注入，
  真实 `git clone --depth 1` 私有仓库 exit 0 + `.git` 落地；token 零明文断言
  全过（命令行 / stdout / stderr）。**AC-S4-08 真跑转正，待授权清单 6/6 全部完成。**
- 凭证卫生（主控执行）：测试经 `remember_secret` 写入 `workspace/.secrets` 的真
  token 条目与 `workspace/.git_askpass_github.com.sh`（0700，含真 token）均已删除，
  复查零残留；已提醒 Maria 吊销该 PAT 并清理 `.env` 两行。
