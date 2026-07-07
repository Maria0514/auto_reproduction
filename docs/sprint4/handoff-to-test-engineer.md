# Sprint 4 阶段 G 交付：移交测试工程师

> 主控收口产出（G3，2026-07-06）。对齐 `docs/sprint4/dev-plan.md` v1.0 §4 任务 G3 +
> §5 AC 覆盖矩阵 + §9 交付物清单。G1/G2 报告：
> `docs/sprint4/test-reports/2026-07-05_g1-ac-matrix-audit.md` /
> `docs/sprint4/test-reports/2026-07-06_g2-e2e-mock-layer.md`。

---

## 1. 待授权真跑清单（真实链路 e2e + Prompt Cache 命中率，逐项须 Maria 明确授权）

> 纪律：真实 e2e 消耗 LLM/deepxiv 配额，**任何真跑须 Maria 明确授权具体动作**（泛"好"不够）。
> `pytest.ini` 已设 `addopts = -m "not e2e"`（2026-07-05 E4 事故机制性防线，commit 5401a90），
> 裸 pytest 永不触发 e2e；真跑须显式 `-m e2e` 覆盖。

> **[转正记录 2026-07-06] Maria 授权"全跑"，除 #2 外全部由主控执行完毕**（详见
> `docs/sprint4/test-reports/2026-07-06_g2-real-run-conversion.md`）。

| # | 项 | 验证目标 | 状态（2026-07-06） |
|---|---|---|---|
| 1 | `real_s4_1`（三 interrupt 串行真实链路） | AC-S4-13 转正 | ✅ **有效 3/3 PASS**（4:38/5:28/5:39，LLM 服从度 3/3）；前置 3 次作废跑 = 2 处 harness 缺陷（workspace 护栏 / 固定剧本 vs 真实 LLM 非确定），已直修留档 |
| 2 | `real_s4_2`（真凭证注入 sandbox） | AC-S4-08 转正 | ⏳ **待 Maria 提供** `SP4_E2E_PRIVATE_REPO_URL` + `SP4_E2E_GIT_TOKEN`（.env 两行；零 LLM/deepxiv 配额，<2 min，1 次） |
| 3 | `real_s4_3`（真实 agent 复述脱敏） | AC-S4-11 真实 LLM 侧转正 | ✅ **3/3 PASS**（12~15s/次，复述零哨兵泄漏） |
| 4 | coding Prompt Cache 命中率回归 | CP-G3-1 守门 | ✅ **R_after=0.8726 双门 PASS**（脚本 sp2 门 0.7286 ✓ / dev-plan 规格门 0.9008×0.95=0.8558 ✓） |
| 5 | execution Prompt Cache 命中率首测 | 新 prompt 建基线 | ✅ **基线 R_after=0.8906**（warm 两轮一致 89.1%），后续守门 ≥0.8461；脚本 `scripts/spike_execution_prompt_cache.py` |
| 6 | sp3 `real-1` smoke | fail-fast 验凭证 | ✅ PASS（3:53） |

入口命令（真跑时逐条显式指定，勿批量）：

```bash
# e2e 骨架（tests/test_sprint4_e2e.py 末部 3 条，@pytest.mark.e2e + 凭证 skipif）
.venv/bin/pytest "tests/test_sprint4_e2e.py::<node_id>" -m e2e -v -s

# Prompt Cache 命中率（双保险闸门：不设 SPIKE_F3_AUTHORIZED=1 直接拒跑）
SPIKE_F3_AUTHORIZED=1 .venv/bin/python scripts/spike_coding_prompt_cache.py
```

注意：本次 G 阶段修正了 `request_user_input` docstring（ADJ-S4-G2-02 措辞 A，工具
schema 参与 coding prompt 前缀）→ **coding prompt cache 前缀失效一次**（裁决权衡内
接受）；命中率回归应在该版本上重建预热后采样，与 0.9008 基线比较口径不变。

## 2. mock 用例运行方式（默认回归主路径，零配额）

```bash
# 一键全量非 e2e 回归（addopts 默认排除 e2e，推荐验收主路径）
.venv/bin/pytest -q

# 仅 sp4 全套
.venv/bin/pytest tests/test_sprint4_a1.py tests/test_sprint4_a2.py tests/test_sprint4_a3.py \
  tests/test_sprint4_b1.py tests/test_sprint4_b1_fix.py \
  tests/test_sprint4_b2_interrupt3_idempotency.py \
  tests/test_sprint4_c1.py tests/test_sprint4_c2.py \
  tests/test_sprint4_d1.py tests/test_sprint4_d2.py \
  tests/test_sprint4_e1.py tests/test_sprint4_e2.py tests/test_sprint4_e3.py \
  tests/test_sprint4_e4_regression_gate.py tests/test_sprint4_le401_fix.py \
  tests/test_sprint4_f1.py tests/test_sprint4_g1.py tests/test_sprint4_e2e.py -q

# G1 AC 覆盖矩阵审计单独入口
.venv/bin/pytest tests/test_sprint4_g1.py -q

# e2e 骨架收集核验（不真跑）
.venv/bin/pytest tests/test_sprint4_e2e.py --collect-only -q -m e2e
```

环境：`.venv/bin/pytest`（裸 python 是 py2、裸 pytest 不在 PATH）。

## 3. AC-S4-01~14 覆盖矩阵（终态）

| AC | 内容 | 自动化覆盖 | 状态 |
|---|---|---|---|
| AC-S4-01 | run_command 存在 + 正常返回 + 越界拒 | CP-C1-1/2 / CP-C2-1（G1 矩阵映射） | ✅ mock 全覆盖 |
| AC-S4-02 | 护栏复用 + RUN_COMMAND_TIMEOUT < SANDBOX_EXEC_TIMEOUT | CP-A1-2 / CP-C1-3 | ✅ mock 全覆盖 |
| AC-S4-03 | 7 节点骨架不变 | CP-G1-2 结构测试 5 条 + git 双证（graph.py 最后触碰 8b62230，sp3 阶段 D） | ✅ 结构测试 |
| AC-S4-04 | 预算扣减 = rounds + metrics 不双扣 | CP-E3-1（40→35 精确对账；guard/降级零扣减） | ✅ mock 全覆盖 |
| AC-S4-05 | interrupt#2 / 修复循环边界零回归 | CP-E4-1（sp3 六文件 35 条 mock 落点适配零弱化） | ✅ 回归守门 |
| AC-S4-06 | request_user_input 两 agent 均 interrupt#3 + resume | CP-B1-1/2 / CP-B2-1 / CP-E2-5 + CP-G2-1（主图链路） | ✅ mock；真实 LLM 侧→清单#1 |
| AC-S4-07 | credential_required 分类 + 不耗 fix_loop_count | CP-E3-2（8 关键字逐字 §9.2，先于 data_missing/hardware） | ✅ mock 全覆盖 |
| AC-S4-08 | 凭证经 extra_env 注入 sandbox | CP-D1-2 / CP-E1-4 / CP-G2-3（Popen 层 spy 全链路 + 白名单收口首证） | ✅ mock 注入断言；真凭证→清单#2 |
| AC-S4-09 | 记住 → .secrets（0600+gitignore）+ 同 key 不再问 | CP-A3-1/2 / CP-B1-3 | ✅ mock + 文件断言 |
| AC-S4-10 | UI 面板 + resume_with | CP-F1-1/2 | ✅ mock；CP-F1-3 手动 streamlit 走查留真实链路 |
| AC-S4-11 | 全链路无凭证明文 | CP-A3-3 / CP-C1-4 / CP-E1-3 / CP-E3-4 / CP-D2-2 / CP-G2-2（哨兵 grep 四落点；GlobalState 投影 BUG-S4-G2-01 修复后转正；checkpoint DB = ADJ-S4-G2-02 已知限制 characterization 锚定） | ✅ mock grep + **真实 agent 复述 3/3 转正（2026-07-06）** |
| AC-S4-12 | 日志脱敏 | CP-A3-3/5（caplog）+ G2-01 修复补 warning 日志 mask | ✅ caplog |
| AC-S4-13 | 三 interrupt 互不干扰 | CP-G2-1（mock LLM + 真实 build_graph + SqliteSaver，#1→#3→#2 串行，3 连跑） | ✅ mock + **真实链路 3/3 转正（2026-07-06，LLM 服从度 3/3；顺带实证 interrupt#2 commit 边界重入幂等与非法 resume 降级两条真实路径）** |
| AC-S4-14 | 重跑幂等：前序副作用恰为 1 | CP-B2-2 → CP-C2-3 → CP-E4-2 三级递进 + CP-G2-1 主图侧副作用幂等 | ✅ mock + resume 断言 |

## 4. 已知限制与遗留问题

1. **[已知限制 / ADJ-S4-G2-02（架构裁决 (a+)，2026-07-06）]** interrupt#3 敏感 resume
   值经两条机制固有通道明文进入 `checkpoints.db`：子图 `execution:<task>` messages
   （工具返回 ToolMessage）与 `__resume__`（resume Command pending write）。裁决 (a)
   接受：能读该 DB 的主体（同 OS 用户/root）同样能读明文 `.secrets` 与 GIT_ASKPASS
   脚本，DB 级脱敏无净安全收益；缓解 = gitignore（`checkpoints.db*`、`workspace/`）+
   DB 权限收敛 0600（`core/checkpointer.py`，与 `.secrets` 对齐）。**mask serde 因破坏
   checkpoint 往返一致性（B2 实证 resume 依赖 messages 回读真值）永久排除**；升级路径
   为引用键传值（方案 c），**触发条件（任一命中即重开设计）**：① 多用户/托管部署；
   ② checkpoint 需外发（远程持久化/云备份/observability 平台上传帧内容——**后续做
   observability/tracing 时先查这条**）；③ `.secrets` 升级为加密存储；④ 合规要求
   会话级敏感值不落盘。升级时预埋结论：先把进程内 sensitive set 升级为 key→value
   会话存储（解 `remember=False` 引用悬空），消费侧统一走解引用。
2. **Q-C 重跑幂等实证结论摘要**（B2→C2→E4 三级递进）：独立轮次的前序副作用工具
   resume 后**不重放**（副作用恰 1，机理 = 子图纳入父 checkpointer 子命名空间精确恢复）；
   同批混调（副作用工具 + request_user_input 同轮 tool_calls）**整体重放**（副作用 =2）
   → "单独一轮"纪律已直译进 coding/execution prompt；闭包收集器 resume 后丢
   pre-interrupt 值 → 跨 interrupt 序列以子图 messages 回读为权威（R-S4-10 落法）。
   机理防回归锚点已固化（langgraph 升级改 checkpoint 粒度时用例先翻红）。
3. **R-S4-03**：训练中途缺信息 → interrupt#3 → resume 重跑训练（非幂等），MVP 接受
   重跑代价；设计上让缺信息在 prepare 阶段暴露，列 v1.x（子图回合级 checkpoint_ns）。
4. **credential 关键字表覆盖边界**：8 关键字逐字架构 §9.2，漏判兜底 = agent 主动
   request_user_input（首选路径）+ MAX_FIX_LOOP_COUNT 拦截。
5. **D2 备忘闭环结论**（HOTFIX-2 复核定案 (a') 修正版，详见
   `tests/test_sprint4_d2.py` 模块头全文固化）：`PIP_`/`LC_` 前缀保留白名单透传
   （非凭证 PIP_INDEX_URL/PIP_CACHE_DIR/PIP_TIMEOUT 等继续生效），凭证走 extra_env
   显式注入，两机制并存不冲突；既有 isolation 9 条字节级零修改。
6. **L-B1-01 空值防线**：remember=True + 空值提交会落盘空串条目致同 key 永久
   cache-hit；防线 = F1 UI 提交前非空校验（已锚定）；落盘处 `and value` 一行纵深
   防御为架构师非阻断建议（挂账）。
7. **langgraph 升级检查单**：测试内 FakeLLM msgpack 反序列化 "will be blocked in
   future" 提示（b1_fix/B2 同款）；`JsonPlusSerializer` allowed_objects
   PendingDeprecation warning。升级 langgraph 前先跑 B2/G2 幂等与结构锚点用例。
8. **信息项**：修复回合会覆盖 `execution_result`，前回合证据只在 checkpoint 历史帧
   （G2 实证留档）；`test_sprint4_e1.py` `_ws_dir` 留持久测试目录（沿 sp3 先例，
   凭证类残留经 G2 复核为零）。

## 5. 交付物清单与 dev-plan §9 一致性核对

| §9 条目 | 状态 |
|---|---|
| 新增 `core/secrets_store.py`（A3） | ✅ |
| 新增 `core/tools/interaction_tools.py`（B1） | ✅（2026-07-06 docstring 措辞随 ADJ-S4-G2-02 修正，CP-B1-5 锚定常量同步） |
| 新增 `core/tools/run_command_tool.py`（C1） | ✅ |
| 改动 `config.py` 纯追加 3 常量（A1） | ✅ |
| 改动 `core/state.py` 追加 2 通道无 reducer（A2） | ✅ |
| 改动 `core/nodes/coding.py` 7 工具 + prompt 稳定前缀（C2） | ✅ |
| 改动 `sandbox/local_venv.py` extra_env 透传（D1/D2） | ✅ |
| 改动 `core/nodes/execution.py`（E1/E2/E3） | ✅（+2026-07-06 BUG-S4-G2-01 修复：5 处 tainted summary/stderr 落 state/日志点统一 mask_value） |
| 改动 `ui/pages/execution_monitor.py` + `app.py`（F1） | ✅ |
| 新增 `tests/test_sprint4_*`（B2/各任务/G1/G2） | ✅（18 个文件） |
| 新增 `docs/sprint4/test-reports/*` | ✅（a1-a2-d1 / a3-d2 / b1 / b2 / cef / e3-e4 / g1 / g2 / real-run-conversion） |
| 新增 `scripts/spike_execution_prompt_cache.py`（§9 表外合理增量，G3 清单 #5 载体） | ✅（同款 `SPIKE_F3_AUTHORIZED` 闸门） |
| 新增本 handoff（G3，含 D2 结论） | ✅ |
| 零改动承诺 | ✅ `core/graph.py`（G1 git 双证）等；`core/react_base.py` 按 §9 勘误豁免（BUG-S4-B1-01 GraphBubbleUp 直通，8 行纯追加）；**§9 表外合理增量：`core/checkpointer.py` +chmod 0600（ADJ-S4-G2-02 裁决 (a+) 唯一代码加固，3 行级）** |

### 5.1 pytest 状态快照（G3 收口基线，2026-07-06）

```
.venv/bin/pytest -q
1387 passed / 37 skipped / 46 deselected / 0 failed / 0 xfailed（3 连跑恒定，~62s）
```

- deselected 46 = e2e 全集 43 + sp4 e2e 骨架 3（`-m e2e` 收集 46/1470 精确互补）。
- skipped 37 恒定（sp2 以来 UI iframe 类设计性 skip，非退化）。
- Sprint 4 对账链（全量非 e2e passed）：1119（sp4 基线）→ 1140（A1/A2/D1）→
  1197（A3/D2）→ 1224（B1）→ 1229（B1 fix）→ 1238（B2）→ 1311（C/E/F 三线）→
  1341（E3/E4）→ 1349（L-E4-01）→ 1377（G1）→ 1385+2xfail（G2 首验）→
  1387（G2 发现处置收口：BUG-S4-G2-01 修复转正 + ADJ-S4-G2-02 裁决降格），每跳有档。

## 6. Prompt Cache 守门（CP-G3-1 就绪态）

- **coding prompt 字节级一致**：`test_cp_c2_2_prompt_body_byte_identical_guard`
  （`tests/test_sprint4_c2.py`，主体 == `_CODING_SYSTEM_PROMPT_BODY` 常量 + 哨兵
  断言防动态混入）✅ 已落并全绿。
- **execution prompt 字节级基线**：`test_cp_e2_1_system_message_byte_identical_across_tasks`
  （`tests/test_sprint4_e2.py`，SystemMessage 整条字节级常量跨任务一致）✅ 已落并全绿。
- **工具 schema 稳定前缀**：`request_user_input` docstring 逐字节锚定
  （CP-B1-5，`_EXPECTED_TOOL_DESCRIPTION`）；本次措辞修正为**有意识同步**（锚定
  常量同批更新），机制未破。
- **命中率回归（2026-07-06 真跑转正）**：coding `R_after = 0.8726`（R1=93.6% cold /
  R2=82.7% / R3=91.8%）双门 PASS——脚本 sp2 门 0.7286 ✓、dev-plan 规格门
  0.9008 × 0.95 = 0.8558 ✓；较 sp3 0.9008 的降幅与 C2 挂 7 工具 + ADJ 措辞 A
  docstring 修正的预期影响一致。execution 首测基线 `R_after = 0.8906`（warm 两轮
  一致 89.1%），后续 execution prompt 改动守门 ≥ 0.8461；脚本
  `scripts/spike_execution_prompt_cache.py`（复用 coding spike 采集与
  `SPIKE_F3_AUTHORIZED` 闸门）。原始指标 JSON 均落 `workspace/runs/`。

## 附录：协作建议

- G2 若做 e2e 转正，**另立 e2e 覆盖矩阵**，勿并入 G1 mock 矩阵
  （`tests/test_sprint4_g1.py` 元断言已锁 10 键防混入）。
- 全量回归以裸 `.venv/bin/pytest` 为准（addopts 防线保证零 e2e 触发）；连跑 3 次
  为闭环声明标准。
- checkpoint DB 相关断言基于 langgraph 当前落帧行为（characterization 锚定），
  langgraph 升级后若翻红，按用例 docstring 指引同步清理勘误文档。
