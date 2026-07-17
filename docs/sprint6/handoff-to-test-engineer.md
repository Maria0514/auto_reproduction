# Sprint 6 Handoff：AC-S6-01~23 覆盖矩阵 + 已知限制 + 运行入口

- **日期**：2026-07-16
- **交付**：主控（批次 5 收口）→ 测试工程师
- **前置**：批次 0~4 已合入（commit 4994fd4 + 批次 5 收口增量）

## 1. AC-S6-01~23 覆盖矩阵

| AC | 验收点 | 覆盖测试 | 状态 |
|---|---|---|---|
| AC-S6-01 | 过渡态 + 自刷新 + resume 消费后自动转态 | test_sprint6_s6_01_execution_monitor.py::test_cp_3_3_1_* | ✅（真跑面待 T-S6-5-3） |
| AC-S6-02 | 至多一次 resume + 换代不误提交 | test_sprint6_s6_01_execution_monitor.py::test_cp_3_3_2_* + test_sprint6_s6_01_controller.py::test_cp_3_1_3_* + 面板 assert_called_once_with | ✅ |
| AC-S6-03 | 面板同契约 + 无"停轮询等后台"分支 | test_sprint6_s6_01_execution_monitor.py::test_cp_3_3_3_polling_discipline_matrix / test_cp_3_3_5_* | ✅ |
| AC-S6-04 | 在途标签不滞后 | test_sprint6_s6_01_controller.py::test_cp_3_2_1_* + test_sprint6_s6_01_execution_monitor.py::test_cp_3_3_4_* | ✅ |
| AC-S6-05 | coding/execution 在途切页 + planning 不误切 | test_sprint6_s6_02_progress.py::test_cp_3_4_1_* | ✅ |
| AC-S6-06 | gate 降级记账非空 + 现场靶 | test_sprint6_b2.py::test_cp_2_3_3_fixture_cdcd_* | ✅（真跑面待 T-S6-5-3） |
| AC-S6-07 | 降级 coding/execution 上下文含指令 + 修复循环各轮 | test_sprint6_b2.py::test_cp_2_2_1/2_3_1 + **test_sprint6_b5_deferred_cps.py::CP-2.2-3（修复回合各轮）** | ✅ |
| AC-S6-08 | 已拒凭证二次索要确定性短路 | test_sprint6_b2.py::test_cp_2_1_2_* | ✅ |
| AC-S6-09 | no_metrics 专属类别文案消除矛盾 | test_sprint6_b2.py::test_cp_2_4_* + **test_sprint6_b5_deferred_cps.py::CP-2.4-5（characterization 翻转）** | ✅ |
| AC-S6-10 | no_metrics 定向 hint + 早停 | test_sprint6_b2.py::test_cp_2_5_* + **test_sprint6_b5_deferred_cps.py::CP-2.5-3（hint 三下游）** | ✅ |
| AC-S6-11 | 计划自洽两规则 + 干净计划零警示 | test_sprint6_b1_plan_checks.py::TestCP131*/TestCP132* | ✅ |
| AC-S6-12 | 数据缺失警示 + interrupt 种类不变 | test_sprint6_b1_plan_checks.py::TestCP133* + test_sprint6_b1_prompt_guards.py::TestCP153* | ✅ |
| AC-S6-13 | planning prompt 含"运行实验主入口"约束 + 前缀冻结 | test_sprint6_b1_prompt_guards.py::test_planning_prompt_body_byte_snapshot（**仅前缀冻结面**） | ⚠️ 约束内容面**已裁剪**（见 §2） |
| AC-S6-14 | URL 含标识 + F5 重连路由 + 无参数字节等价 | test_sprint6_s6_06_query_params.py + test_sprint6_s6_06_reconnect.py（R1~R7 路由矩阵） | ✅（真跑面待 T-S6-5-3） |
| AC-S6-15 | 列表页只读枚举 + 挂回 + 无删/搜/分页 | test_sprint6_s6_07_task_status.py::test_cp_4_3_* + test_sprint6_s6_07_task_list_page.py::test_cp_4_4_1_* | ✅ |
| AC-S6-16 | 挂回 resume 有效 + 执行须显式触发 | test_sprint6_s6_07_task_status.py::test_cp_4_3_4_* + test_sprint6_s6_07_task_list_page.py::test_cp_4_4_2_*（挂回不调 resume_task） | ✅（真库 resume 有效性待 T-S6-5-3） |
| AC-S6-17 | pip install 不写 home 缓存 | test_sandbox_env_isolation.py + test_sprint4_d2.py::test_cp_d2_1_* | ✅（真跑面待 T-S6-5-3） |
| AC-S6-18 | 作者字段友好展示 + 异形不裸渲染 dict | **test_sprint6_mf2_authors.py（批次 5 新补，含缺 name dict 占位 + 泛化守门）** | ✅（本批补测 + MF-2 兜底修） |
| AC-S6-19 | logs 类型三方一致 + stdout 入账可消费 | test_sprint5_t26_grouped_metrics.py（11 键精确集）+ core/state.py:154 `logs: str` + 消费方分散覆盖 | ✅（无聚合"消费方清单"用例，见 §2） |
| AC-S6-20 | 用户可见文本无 [error_category=...] 裸标签 | test_sprint6_s6_01_execution_monitor.py::test_cp_3_3_6_* | ✅ |
| AC-S6-21 | resource_scout 无 pwc + 降级链 deepxiv→web | test_sprint6_b1_prompt_guards.py::TestCP152*/TestCP154* | ✅ |
| AC-S6-22 | 冷启动首屏有加载提示 | —（浏览器手动项） | 🖐 手动项（T-S6-5-3 浏览器复走确认） |
| AC-S6-23 | dev_loop 面板展示运行输出尾部 + 空占位 | test_sprint6_s6_01_execution_monitor.py::test_cp_3_3_5_* | ✅ |

**统计**：✅ 21 条（含批次 5 补齐 AC-18）| 🖐 手动项 1 条（AC-22）| ⚠️ 已裁剪/已知限制 1 条（AC-13 约束内容面）。

## 2. 已知限制（如实留档）

- **AC-S6-13 约束内容面已裁剪**：planning prompt 中"执行步骤必须包含运行实验主入口并产出指标"的**约束文本未落地**——源于批次 1 **T-S6-1-1 被删除**（Maria 决策：code_only 模式无跑实验步骤、硬约束冲突，见 TODO 2026-07-14 批次 1 收官条目）。前缀冻结面（Prompt Cache 基线）已覆盖，约束内容面属**有意范围裁剪**，非缺陷。no_metrics 早停（S6-04）+ 计划自洽交叉检查（S6-05）从执行侧/审核侧共同覆盖"计划光搭骨架不跑实验"这一病灶，功能目标以另两条路径达成。
- **AC-S6-19 无聚合守门**：logs 类型三方一致有 11 键精确集守门 + `logs: str` 定案 + 各消费方分散覆盖，但无单一"全部消费方清单核查"聚合用例（PRD 原文点名要一个）。功能正确、覆盖分散，属守门形态差异非缺陷。若要闭合可补一条枚举 4 消费方（execution_monitor:163/429、coding.py:236、execution.py:1103）均按 str 消费的聚合断言。

## 3. 真跑待收口清单（T-S6-5-3，须 Maria 授权）

mock/现场 fixture 已覆盖，真跑面待授权窗口：
- AC-S6-01（真渲染周期无手动刷新端到端时序）
- AC-S6-06（gate 降级记账真库现场）
- AC-S6-14（URL 重连真浏览器 F5 直达）
- AC-S6-16（真库 resume 有效性 = 原 session 等价推进）
- AC-S6-17（sandbox pip 真装不写 home 缓存）
- AC-S6-22（冷启动 spinner 手动浏览器确认）

## 4. 运行入口

- 全量非 e2e：`.venv/bin/pytest -q -m "not e2e"`
- sp6 分批：`.venv/bin/pytest -q tests/test_sprint6_*.py`
- 三现场 fixture：`tests/fixtures/checkpoints_s6_cdcd432cda49.db`（换代/降级/挂回 R5）、`checkpoints_s6_19e21e015017.db`（no_metrics/两面计划）、`checkpoints_s6_full20.db`（列表页枚举 R2~R7 全类型）
- execution_monitor 页面级 ×3 防 flaky：见 test_sprint6_s6_01_execution_monitor.py::test_cp_3_3_8_*（parametrize run_idx）
