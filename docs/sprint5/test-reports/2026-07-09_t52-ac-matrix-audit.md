# 测试执行报告 - t52-ac-matrix-audit（T-S5-5-2 回归样本靶测收口 + AC 覆盖矩阵审计）

- **日期**：2026-07-09 20:18（本地时区）
- **执行人**：@测试工程师代理
- **Sprint**：sprint5
- **触发原因**：主控正式派发 T-S5-5-2（批次 5 验收线；前置 T-S5-5-1 已由主控收口 1702 passed / 0 failed）
- **commit**：703589c（新增两测试文件未 commit，按任务边界留主控收口）

## 执行范围

- 命令：
  - `pytest tests/test_sprint5_t52_regression_targets.py -v`（CP-5.2-1 / CP-5.2-2）
  - `pytest tests/test_sprint5_t52_ac_matrix.py -v`（CP-5.2-3）
  - `pytest <矩阵导出的 69 个 targeted node ids>`（映射用例实跑全绿验证）
  - 两文件合并 3 连跑（稳定性）
- 新增文件：
  - `tests/test_sprint5_t52_regression_targets.py`（6 用例：五条靶测 AC + AC-S5-03 三落点串联）
  - `tests/test_sprint5_t52_ac_matrix.py`（46 用例：21 AC×2 参数化审计 + 4 元断言 + 模块可收集性守门）
- 是否包含 e2e：否（全离线 mock，零 LLM、零 deepxiv、零配额）

## 结果摘要

- 通过：52（6 + 46）；矩阵 targeted 实跑另计 78 绿（69 node id 含参数化展开）
- 失败：0（终态；开发过程中 2 次红详见"失败排查"）
- 跳过：0
- 警告：1（`LangChainPendingDeprecationWarning`，langgraph serde 上游告警，项目级已知、非本 Sprint 引入）
- 总耗时：单轮 ~1.4s；3 连跑 52/52/52 全绿零 flaky
- 只读终验：`tests/fixtures/**`、`workspace/2604.01687/`、`checkpoints.db`（md5 `7dfba802...`）测试前后零变动

## CP 检查点结论

| 检查点 | 结论 |
|---|---|
| CP-5.2-1 五条靶测 AC 全绿（fixture 副本驱动 + 原样本零写入实证） | PASS——AC-S5-01/05/07/15/20 各一条聚合用例；模块级 `_fixture_integrity_guard` 对固化副本 7 文件 md5 + 原样本/真库 stat 前后对账 |
| CP-5.2-2 AC-S5-03 三落点 mock e2e 串联 | PASS——同一份降级事实 gate resume → state.credential_degradations → exec_result.degraded_credentials → 报告"重要声明/凭证降级"块，单测内不断链流转 |
| CP-5.2-3 覆盖矩阵审计 21 条逐条映射全绿 + 缺口显式落档 | PASS——G1 三重防假绿（存在性/mark 审计/AST 防空泛）继承并扩展类方法支持；GAP_MANIFEST 5 条缺口建档且断言封闭 |

## AC-S5-01~21 覆盖矩阵（AC → 主证用例 → 状态）

| AC | 主证用例（模块::用例，节选核心） | 状态 |
|---|---|---|
| AC-S5-01 | t15::required_credentials 指令/schema/passthrough + **t52::ac01 靶测**（2604.01687 场景 mock planning 输出→map 落 plan 非空） | 绿 |
| AC-S5-02 | t22::payload 五键 / secrets 命中零 interrupt / degrade 落 state | 绿 |
| AC-S5-03 | t22+t24+t33+t34 四落点各证 + **t52::三落点串联** | 绿 |
| AC-S5-04 | t13::三红线 in prompt + simulation_notice 落 state | 绿 |
| AC-S5-05 | t31::CP-3.1-1 fixture 命中 7 三元组 + **t52::ac05 审计→报告全链**（降档措辞+重要声明+证据进报告） | 绿 |
| AC-S5-06 | t31::CP-3.1-2 干净 fixture 零命中 + 豁免正控制 ×2 | 绿 |
| AC-S5-07 | t33::结论三值 + t34::两级措辞 + **t52::ac07 快照重生成 vs 旧 report.md 措辞对照**（旧含"✅ **复现成功**"/巨型 dict，新全禁） | 绿 |
| AC-S5-08 | t33::trend 三态 + t34::回验节渲染 | 绿 |
| AC-S5-09 | t15::禁编造数值/array schema + t34::删"计划 expected"列 | 绿 |
| AC-S5-10 | t24::13 步计划/8 步执行对账 + t34::N/M 渲染 | 绿 |
| AC-S5-11 | t33::incomplete 双源标注 + attribution_unavailable 不触发（R-2 保守）+ t34::声明块 | 绿 |
| AC-S5-12 | t11::TestCP112 预算边界（类方法映射） + t25::联动参数化/截断显式 | 绿 |
| AC-S5-13 | t41::事件 schema/截断 + t43::尾部渲染（手动部分见缺口） | 绿(mock) |
| AC-S5-14 | t41::deque 封顶 + t42::不进 checkpoint/react_base 零耦合（全量回归旁证主控 1702 绿） | 绿(mock) |
| AC-S5-15 | s5_08::双通道 4 用例 + **t52::ac15 回归 thread state 序列全程路由**（真库手动见缺口） | 绿(mock) |
| AC-S5-16 | s5_08::kind 分发真值表 + AppTest 两 kind → 监控页 | 绿 |
| AC-S5-17 | s5_08::case⑥bis 失败卡片 + 停假轮询（含空串边界） | 绿 |
| AC-S5-18 | t35::映射全表命中/未知值兜底/页面无裸枚举 | 绿 |
| AC-S5-19 | t15::术语尾段 + 主体字节冻结（Prompt Cache 在线维见缺口） | 绿(离线维) |
| AC-S5-20 | t26::三组解析对齐/key_packages + t34::删列/降维 + **t52::ac20 真实解析→渲染合龙**（"本次复现值"数字进表非空） | 绿 |
| AC-S5-21 | t36::两页路径展示 + 缺字段防御（UI 手动见缺口） | 绿(mock) |

矩阵映射共 69 个唯一 node id，targeted 实跑 78 用例（参数化展开）全绿。

## 失败排查（开发过程中 2 次红，均为测试代码问题，已修复）

### ① fixture 覆写事故（严重度高，被自建守门当场抓获）

- 用例：`test_cp_5_2_1_ac05/ac07`（首版实现）
- 失败类型：**测试代码 bug**（非生产代码 bug）
- 现象：首跑后 `_fixture_integrity_guard` 报"固化 fixture 在测试执行期间被修改"；ac07 读到的"旧报告"竟是新生成内容
- 排查：`reporting()` 落盘规则为 `Path(code_output_dir).parent / "report.md"`（reporting.py `_resolve_report_path` 规则 1"报告与代码同目录"）。首版把 `code_output_dir` 直接指向 `tests/fixtures/regression_2604_01687/code`，导致真实 reporting 把新报告写到 fixture 根，覆盖固化 report.md
- 处置：`git checkout` 恢复 fixture（md5 复核与源一致 `a44949af...`）；新增 `_fixture_code_scratch_copy` helper——固化 code/ 复制到 tmp 作为被测输入，fixture 本体只读；helper docstring 记录事故防复发
- 结论：完整性守门 fixture 的设计价值得到实证（"原样本零写入"不是口号而是断言）；**此规则对批次 3 消费方同样适用——任何测试把 code_output_dir 指向 fixture 都会覆写 report.md**（t31/t26 直调纯函数无此风险，t34 用 tmp 无此风险，已核对）

### ② ac07 巨型 dict 断言过宽（测试断言口径问题）

- 失败类型：测试断言与 AC 口径错位（非生产 bug）
- 现象：全文 `"{'" not in md` 断言被"计划目标回验"表命中——旧 dict 形态 expected_results 条目按 repr 原样渲染 + "旧形态数值预期，不做机验"注记
- 排查：AC-S5-20 红线口径为"嵌套**指标**降维渲染无巨型 dict"（指标对比表，已验证降维正确）；回验表对旧 dict 的 repr 展示属 R-5 兼容路径既定容忍设计（CP-3.4-5 仅断"不崩 + 全未验证"），仅旧 checkpoint 重生成时出现
- 处置：断言收窄至"## 指标对比"节（与 AC 口径精确对齐），并断言兼容注记在场（容忍边界有痕）
- **遗留观察（供 handoff，非 bug 挂账）**：若旧 thread 的 expected_results 含真实快照那样的巨型 cross_model_transfer dict，重生成报告的回验表会出现一条超长行（可读性 wart，诚实性无损）。是否值得收敛渲染请主控/PM 在 T-S5-5-3 handoff 时定夺

## 缺口清单（GAP_MANIFEST，交 T-S5-5-3，测试内断言封闭）

| AC | 缺口内容 | 承接 |
|---|---|---|
| AC-S5-13 | 监控页活动流 UI 手动 happy path（真实 streamlit run 观察） | T-S5-5-3 真实链路抽验 |
| AC-S5-14 | sp3/sp4 全量非 e2e 回归 | 主控已收口（T-S5-5-1，1702 绿），矩阵存旁证 |
| AC-S5-15 | 回归 thread task-9208a1a4b4f5 真库路由手动验证（checkpoints.db 只读） | T-S5-5-3 手动项 |
| AC-S5-19 | Prompt Cache 在线维复采 + 新 R_baseline×0.95 守门 | T-S5-5-3 **Maria 授权项**（CP-5.3-2） |
| AC-S5-21 | 两页产物路径"可复制"UI 手动 happy path | T-S5-5-3 手动项 |

## 后续动作

- T-S5-5-3：按 GAP_MANIFEST 提交授权动作清单给 Maria（一次授权合并执行）；handoff 文档引用本报告矩阵表
- 主控收口：两个 t52 新文件并入全量回归（预期 1702 + 52 = 1754 收口数字）；TODO/dev-plan CP-5.2-1/2/3 勾选由主控执行
- 遗留观察（回验表旧形态 repr 长行）在 handoff"已知限制"节记一笔
