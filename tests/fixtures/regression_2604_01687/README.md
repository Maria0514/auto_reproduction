# 回归样本 fixture：2604.01687（EvoSkills）"假成功"现场

> **原样本只读警示**：本目录是 `workspace/2604.01687/` 的**复制固化**（复制不移动，architecture.md §9.4）。
> 原样本 `workspace/2604.01687/` 与 `checkpoints.db`（thread `task-9208a1a4b4f5`）为 Sprint 5 回归物证，
> **严格只读、勿清理、勿修改**（manual-run-feedback.md 抬头明令）。任何测试只允许读本 fixture，不得回写。
> 本 fixture 内文件同样**不得修改**——它是审计规则的回归基线，改动即破坏 AC 断言语义。

## 来源

- 原路径：`workspace/2604.01687/`（2026-07-07 Maria 手动真跑产物）
- 固化日期：2026-07-09（Sprint 5 批次 3 前置门，dev-plan §3.4）
- 固化人：@测试工程师代理
- 固化方式：`cp -p` 逐文件复制，md5 与源逐一比对一致（见固化当日 test-report）

## 用途（消费方 AC）

| 文件 | 对应 AC | 用途 |
|---|---|---|
| `code/src/skill_generator.py` | AC-S5-05 | 审计命中靶——**答案泄漏** smell：L23-25 把 verifier 的 `expected_skill_keywords` 原样抄进被评估的 SKILL.md（出题人=答题人自证闭环） |
| `code/src/task_executor.py` | AC-S5-05 | 审计命中靶——**硬编码分数** smell：L28-31 `no_skill` baseline score=0.0 写死、其他 baseline 0.3/0.1 写死；L32-35 难度 ±0.2/-0.1 编造规则（预写结局） |
| `code/data/skillsbench_manifest.json` | AC-S5-05 | 自造任务证据：3 个"SkillsBench"任务为 agent 自编，verifier 即关键词清单 |
| `code/outputs/evoskills_smoke/summary.json` | AC-S5-20 | 多组指标解析靶（组 1，含 `pass_rate` / `mean_oracle_score`） |
| `code/outputs/baselines/no_skill/summary.json` | AC-S5-20 | 多组指标解析靶（组 2，含 `pass_rate` / `mean_score`） |
| `code/outputs/baselines/self_generated/summary.json` | AC-S5-20 | 多组指标解析靶（组 3，含 `pass_rate` / `mean_score`）——三组须全部对齐，"本次复现值"列非空 |
| `report.md` | AC-S5-05/07/20 | 措辞对照靶：旧报告 `full_success`"✅ 复现成功"宽松口径 + L24-26 嵌套 dict 巨型行（渲染反例），新逻辑不应再产出同款措辞/渲染 |

审计降档预期（AC-S5-05）：命中 ≥2 类 smell（答案泄漏、硬编码分数），结论降档（非最高档），报告显著标注"模拟/未验证"。
误报防线对照靶见姊妹 fixture：`tests/fixtures/clean_code_sample/`（AC-S5-06）。

## 与 dev-plan §3.4 预期清单的路径出入（按实际结构固化）

- §3.4 写 `code/skill_generator.py` / `code/task_executor.py`，实际在 `code/src/` 子目录下 → 固化为 `code/src/…`；
- §3.4 写 "`outputs/` 三组 summary.json"，实际 outputs 位于 `code/outputs/` 下 → 固化为 `code/outputs/…`，三组组名与预期一致（`evoskills_smoke` / `baselines/no_skill` / `baselines/self_generated`），相对目录结构原样保留。

## 背景

完整"假成功"因果链见 `docs/sprint5/manual-run-feedback.md`（#9 造假实验现场解剖、#7 判定口径过松、#8 指标解析缺口）。
本 fixture 仅固化审计/解析/措辞三类断言所需的最小文件集；原样本中其余文件（configs、task_runs、.venv 等）未固化，需要时回原路径只读查阅。
