# Sprint 5 Handoff（主控 → 测试工程师 / 后续 Sprint）

日期：2026-07-09 ｜ 状态：Sprint 5 全六批次收官 ｜ 最终收口：全量非 e2e **1754 passed / 0 failed / 37 skipped / 46 deselected**

## 1. 运行方式

- **mock 全量（默认，零配额）**：`.venv/bin/pytest -q`（pytest.ini addopts 默认排除 e2e）
- **e2e 入口（耗配额，须 Maria 授权具体动作）**：`.venv/bin/pytest -m e2e <file::test>`；凭证由 conftest load_dotenv 从 `.env` 读取
- **Prompt Cache 复采（须授权）**：`SPIKE_F3_AUTHORIZED=1 .venv/bin/python scripts/spike_{coding,execution}_prompt_cache.py`、`spike_prompt_cache_baseline.py`；现行守门线 coding 0.8852 / execution 0.8521 / analysis 0.7761（基线详见 `test-reports/2026-07-09_t53-real-run-window.md`）
- **回归样本**：一律用 `tests/fixtures/regression_2604_01687/`（注意实际路径 `code/src/` 与 `code/outputs/`）与 `tests/fixtures/clean_code_sample/`；`workspace/2604.01687/` 与 `checkpoints.db`（thread `task-9208a1a4b4f5`）只读。**陷阱**：把 `code_output_dir` 直接指向 fixture 会让 `reporting()` 就地覆写 report.md——用 tmp 副本驱动（t52 完整性守门已封此坑）

## 2. AC-S5-01~21 覆盖矩阵

权威矩阵：`tests/test_sprint5_t52_ac_matrix.py`（21 条 AC 元断言封闭 + 三重防假绿）+ 审计报告 `test-reports/2026-07-09_t52-ac-matrix-audit.md`。缺口清单（GAP_MANIFEST 断言封闭）现状：

- AC-S5-19 在线复采：**已闭环**（2026-07-09 真跑窗口，新基线三值落档）
- AC-S5-15 真库手动验证：**已闭环**（批次 0 CP-0.2-5 字节副本只读验证）
- AC-S5-13 / AC-S5-21 浏览器走查：**待 Maria**（headless 证据 t42/t43 已覆盖；操作指引见 t53 报告 §4）
- AC-S5-14 全量回归旁证：**已闭环**（1754 绿）

## 3. 已知限制与语义边界

1. **spike 结论依赖库版本**：callbacks 传播（批次 4 主路径、react_base 零字节）依赖 langchain-core 1.3.3 / langgraph 1.1.10 的 contextvar 实现；升级库后复跑 `tests/test_sprint5_spk1_callbacks_spike.py`（~1s）复验
2. **审计规则覆盖边界**：honesty_audit 只认单文件字面量证据（AST/文本），不做跨文件数据流推断；R3 常量结局对非评估角色命名的函数不触发（回归靶实测 R1×5+R2×2、R3=0 属如实）
3. **对账保守语义（R-2）**：全零归属∧非空台账 → `attribution_unavailable=true`，不触发 incomplete_execution，如实展示原始命令
4. **活动流尽力而为语义（R-9）**：极端竞态可丢尾部若干行；纯内存、进程重启即失属预期
5. **expected_results breaking 兼容**：dict→list 为 sp5 唯一 breaking；旧 checkpoint 消费侧一律 `.get()` 防御 + 回验全"未验证"不崩（t33/t34 有兜底断言）
6. **degrade 路径产码方差**：凭证降级后 coding agent 可合法不产码走降级形态（2026-07-09 真跑 4 现 2）；诚实性正确、复现率质量点，建议 Sprint 6 评测纳入统计
7. **execution cd 越界高频**：真实 LLM 常用 cd 风格命令被护栏拒绝→修复循环自愈（预算治理内合法但耗预算）；优化只能走动态通道（前缀冻结令）

## 4. 勘误注记汇总

| # | 勘误 | 落点 |
|---|---|---|
| 1 | CP-A2-5（state.py git-diff 纯追加守门）退役：钦定 breaking 使"零删除行"前提失效 | tests/test_sprint3_a2.py 原位注释 |
| 2 | dev-plan §3.4 fixture 路径笔误：实际为 `code/src/` 与 `code/outputs/` | dev-plan §3.4 注记 + fixture README |
| 3 | architecture §9.2 补记：`lookup_secret` 同步感知会话层（防 gate 重跑死循环，架构师裁决） | architecture.md §9.2 |
| 4 | 审计靶作弊形态勘误：if/elif 分派而非 dict 映射，R2(b) 同语义覆盖两种字面形态 | dev-plan CP-3.1-1 注记 |
| 5 | real_1 真实 e2e 三处适配：gate 循环 / prepare>=1+预算上界 / 诚实链快照旁证（sp3 旧假设 vs sp5 gate + sp4 预算治理） | tests/test_sprint3_e2e.py + t53 报告 §2 |
| 6 | 批次 4 spike 走主路径，R-B1 两档回退均未触发，无 react_base 勘误 | dev-plan T-S5-0-1 门禁注记 |

## 5. 交付物一致性核对（对照 dev-plan §7）

- 新模块 3：`core/honesty_audit.py`、`core/activity_stream.py`、`ui/term_map.py` ✅
- 改造：config/state/coding/execution/planning/reporting/secrets_store/app/ui 四页 ✅
- 新测试 19 文件 344 用例（spike 4 + s5_08 21 + t11~t43 十五文件 267 + t52 两文件 52），既有适配全部只换不弱化留痕 ✅
- 文档：PRD/architecture/dev-plan 检查点全勾（除两项浏览器走查挂 Maria）、test-reports 4 份归档、TODO 全程留痕 ✅
- 遗留清单：见 §2 缺口（2 项浏览器走查）+ §3.6/§3.7 两条 Sprint 6 观察项 + TODO"当前挂账"节既有 2 项（L-S2-13-02 planning cache 断言 / L-A3-01 paper_intake 老测试标准化，均非 sp5 范围）
