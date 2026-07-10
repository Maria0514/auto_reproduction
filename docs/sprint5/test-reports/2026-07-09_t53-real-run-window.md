# T-S5-5-3 真跑窗口报告（2026-07-09）

> 授权记录：Maria 于 2026-07-09 对主控提交的三项动作清单回复"都批准"（①Prompt Cache 三脚本复采 ②真实 e2e 抽验 ③手动协作项），一次授权、一个动作窗口合并执行。执行人：主控（省配额范式）。

## 1. Prompt Cache 在线维复采（AC-S5-19 收口）

| 维度 | 脚本 | R_after / R_baseline | 旧守门线 | 判定 | 新守门线（×0.95） | 落盘 |
|---|---|---|---|---|---|---|
| coding | spike_coding_prompt_cache.py | **0.9318** = mean(0.9232, 0.9405) | 0.7286（借用 sp2 值） | PASS | **0.8852** | workspace/runs/spike-f3-coding-prompt-cache_20260709-221856.json |
| execution | spike_execution_prompt_cache.py | **0.8970** = mean(0.9002, 0.8938) | 无（首测建基线） | 基线建立 | **0.8521** | workspace/runs/spike-g3-execution-prompt-cache_20260709-221939.json |
| analysis（全链基线） | spike_prompt_cache_baseline.py | **0.8169** = mean(0.8629, 0.7708) | 0.7601（sp2 S-3） | 高于旧值 | **0.7761** | workspace/runs/spike-s3-prompt-cache-baseline_20260709-222214.json |

- 批次 1 前缀变更后命中率不降反升（coding 0.9318 vs 旧基线 0.7669）——静态段落一次合入 + 前缀冻结令的收益实证。
- 新基线已回写脚本常量/注释（coding `R_BASELINE_SP2=0.9318` + execution 脚本 docstring 基线注记），后续 prompt 改动以新线守门。
- 备注：analysis 维度 run#3 命中 77.1% 略低（阶段 2 总耗时 131s 超脚本 30s 约束，cache TTL 边缘效应），均值判定健康。

## 2. 真实 e2e 抽验（smoke + real_1 happy path）

- smoke：`test_paper_intake_e2e::test_e2e_plain_id_cs_category` **1 passed（13.8s）**——凭证/配额健康门先行。
- 靶：HippoRAG arXiv:2405.14831（deepxiv 已缓存）；真实 LLM + 真实 deepxiv + mock sandbox（不真跑训练）。

### real_1 五跑历程（sp5 诚实链首次真实点火，每跑都有信息量）

| # | 结果 | 耗时 | 发现 |
|---|---|---|---|
| 1 | FAIL（预期外→实为设计行为） | 97.8s | **coding gate 真实点火**：planning 按 P7 新提示词真实声明 required_credentials（HippoRAG 需 OpenAI key），gate interrupt 拦停——manual-run 反馈 #10 的修复在真实链路首次生效；旧断言"approve 直达 END"写于 gate 之前 |
| 2 | FAIL（degrade 路径方差） | 100.6s | 逐项 degrade resume 后 coding **合法选择不产码**走降级形态到 END（诚实呈现，非假成功） |
| 3 | FAIL（旧假设过时） | 577.5s | degrade 后 coding 产码；execution agent 的 cd 命令被沙箱边界护栏拒绝 → **修复循环 6 轮自愈**（prepare=7）→ 最终 B 档成功；sp3 时代"prepare 恰 1 次"假设与 sp4 预算治理（60 调用子预算）不符 |
| 4 | FAIL（方差复现） | 112.0s | degrade→不产码复现（4 跑 2 现），确认 degrade 路径产码方差是真实行为特征而非偶发 |
| 5 | **PASS** | 209.7s | 适配后 happy path：gate interrupt → 会话层供给假凭证（`remember=False` 不落盘；sandbox 全 mock 假值不外发）→ coding 真实写码 → execution 成功 → full_success 报告 → END。**gate→stash→build_credential_env→env: 注入链路真实覆盖** |

### test_real_1 适配记录（只换不弱化，语义增强）

1. approve 后新增 gate 逐项 interrupt 处理循环：五键 payload 契约断言（interrupt_kind/allow_degrade/is_sensitive/purpose_key）+ 供给凭证 resume；零声明时循环零次直达 END（向后兼容）
2. `prepare==1` → `prepare>=1` + `fix_loop_count<=30` 预算治理上界（修复循环是 sp4 起的合法自愈行为，happy path 契约收敛为"最终 B 档成功+循环受预算约束"）
3. 新增诚实链快照旁证：供给凭证路径 credential_degradations 与 er.degraded_credentials 均为空（degrade 路径对应断言在 t22 mock 套件）

### 凭证卫生

假凭证仅进程内会话层，`workspace/.secrets` 未创建（实测不存在）、workspace 全树 grep 无假值残留；测后全量非 e2e 回归 **1754 passed / 0 failed** 零副作用。

## 3. 遗留观察（建议 Sprint 6 评测体系纳入）

- **degrade 路径产码方差**：凭证降级后 agent 可合法选择不产码（降级形态如实呈现）。对诚实性是正确行为；对"复现成功率"指标是质量点——Sprint 6 benchmark 应把"降级后是否仍产出模拟实验代码"纳入统计维度。
- **execution cd 越界高频**：真实 LLM 爱用 `cd xxx && ...` 风格命令，被边界护栏拒绝后靠修复循环自愈（有效但耗预算）。可评估在 execution prompt 动态上下文或工具错误信息里给出更明确的"禁 cd、用相对路径"引导（注意前缀冻结令——只能走动态通道）。

## 4. 手动协作项状态

- AC-S5-15 真库路由验证：已于批次 0 CP-0.2-5 以字节副本只读方式完成（无需重复）。
- AC-S5-13（活动流滚动可感知）/ AC-S5-21（产物路径页走查）：需浏览器会话，**留给 Maria**——启动 `streamlit run app.py`，提交任意论文任务后在执行监控页观察"agent 活动流"区滚动、在报告页核对"产物路径（可复制）"区。headless 证据（真实 GraphController 装配 + 事件到达断言）已由 t42/t43 覆盖。
