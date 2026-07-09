# 论文复现报告：EvoSkills: Self-Evolving Agent Skills via Co-Evolutionary Verification

- arXiv ID: `2604.01687`
- 论文标题（Title）: EvoSkills: Self-Evolving Agent Skills via Co-Evolutionary Verification
- 生成时间: 2026-07-07 03:12:16
- 报告形态: `full_success`

## 复现结论

> ✅ **复现成功**：代码已在隔离环境中成功执行并解析出关键指标。
>
> 判定口径（B 档）：执行退出码正常且至少解析出 1 个指标。下方指标对比表仅做论文值与复现值的并列展示，仅供参考对比，**不做硬性结论判定**。

## 指标对比

> 下表并列论文报告值（baseline / expected）与本次复现值，仅供对比参考，**不做任何硬性结论**。

| 指标 (Metric) | 论文 baseline | 计划 expected | 本次复现值 |
|---|---|---|---|
| `experiment_name` | — | — | evoskills_smoke |
| `num_tasks` | — | — | 3 |
| `pass_rate` | — | — | 0.6667 |
| `mean_oracle_score` | — | — | 0.9667 |
| `main_comparison` | {'EvoSkills_Claude_Opus_4.6_Claude-Code': 71.1, 'No-Skill_Baseline': 30.6, 'Human_Curated_Skills': 53.5, 'Skill-Creator': 34.1, 'Skill-Creator_single_session_variant': 32.4, 'Self-Generated_Skills': 32.0, 'CoT-Guided_Self-Generation': 30.7} | {'target': '在 SkillsBench 上复现 EvoSkills 相对各基线的显著优势', 'paper_reference': {'EvoSkills_Claude_Opus_4.6_Claude-Code': 71.1, 'No-Skill_Baseline': 30.6, 'Human_Curated_Skills': 53.5, 'Self-Generated_Skills': 32.0, 'CoT-Guided_Self-Generation': 30.7, 'Skill-Creator': 34.1, 'Skill-Creator_single_session_variant': 32.4}} | — |
| `ablation` | {'EvoSkills_Full_framework': 71.1, 'W/O_surrogate_verifier': 41.1, 'W/O_evolution': 48.6, 'No-Skill_Baseline': 30.6} | {'paper_reference': {'EvoSkills_Full_framework': 71.1, 'W/O_surrogate_verifier': 41.1, 'W/O_evolution': 48.6, 'No-Skill_Baseline': 30.6}} | — |
| `cross_model_transfer` | {'Claude_Opus_4.6_self-evolved': {'with_skills': 71.1, 'no_skill': 30.6, 'delta': 40.5}, 'GPT-5.2_self-evolved': {'with_skills': 69.8, 'no_skill': 29.6, 'delta': 40.2}, 'GPT-5.2_transferred': {'with_skills': 65.0, 'no_skill': 29.6, 'delta': 35.4}, 'Claude_Sonnet_4.5': {'with_skills': 63.1, 'no_skill': 20.0, 'delta': 43.1}, 'Claude_Haiku_4.5': {'with_skills': 54.5, 'no_skill': 10.4, 'delta': 44.1}, 'Qwen3_Coder': {'with_skills': 50.8, 'no_skill': 8.4, 'delta': 42.4}, 'DeepSeek_V3': {'with_skills': 48.8, 'no_skill': 13.0, 'delta': 35.8}, 'Mistral_Large_3': {'with_skills': 43.1, 'no_skill': 4.9, 'delta': 38.2}} | {'paper_reference': {'Claude_Opus_4.6_self-evolved': {'with_skills': 71.1, 'no_skill': 30.6, 'delta': 40.5}, 'GPT-5.2_self-evolved': {'with_skills': 69.8, 'no_skill': 29.6, 'delta': 40.2}, 'GPT-5.2_transferred': {'with_skills': 65.0, 'no_skill': 29.6, 'delta': 35.4}, 'Claude_Sonnet_4.5': {'with_skills': 63.1, 'no_skill': 20.0, 'delta': 43.1}, 'Claude_Haiku_4.5': {'with_skills': 54.5, 'no_skill': 10.4, 'delta': 44.1}, 'Qwen3_Coder': {'with_skills': 50.8, 'no_skill': 8.4, 'delta': 42.4}, 'DeepSeek_V3': {'with_skills': 48.8, 'no_skill': 13.0, 'delta': 35.8}, 'Mistral_Large_3': {'with_skills': 43.1, 'no_skill': 4.9, 'delta': 38.2}}} | — |
| `primary_metric` | — | pass rate | — |
| `reproduction_goal` | — | 优先复现论文的相对趋势：Full > W/O evolution > W/O surrogate verifier > No-Skill，且跨模型迁移均带来明显正增益；若模型/API/harness 不同，则允许绝对数值存在偏差。 | — |

## 产物清单（Artifacts）

- `/data/myproj/auto_reproduction/workspace/2604.01687/code/data/skillsbench_manifest.json`
- `/data/myproj/auto_reproduction/workspace/2604.01687/code/outputs/baselines/no_skill/summary.json`
- `/data/myproj/auto_reproduction/workspace/2604.01687/code/outputs/baselines/no_skill/task_metrics.csv`
- `/data/myproj/auto_reproduction/workspace/2604.01687/code/outputs/baselines/no_skill/task_runs/sample_data_task_run/result.json`
- `/data/myproj/auto_reproduction/workspace/2604.01687/code/outputs/baselines/no_skill/task_runs/sample_debug_task_run/result.json`
- `/data/myproj/auto_reproduction/workspace/2604.01687/code/outputs/baselines/no_skill/task_runs/sample_refactor_task_run/result.json`
- `/data/myproj/auto_reproduction/workspace/2604.01687/code/outputs/baselines/self_generated/summary.json`
- `/data/myproj/auto_reproduction/workspace/2604.01687/code/outputs/baselines/self_generated/task_metrics.csv`
- `/data/myproj/auto_reproduction/workspace/2604.01687/code/outputs/baselines/self_generated/task_runs/sample_data_task_run/result.json`
- `/data/myproj/auto_reproduction/workspace/2604.01687/code/outputs/baselines/self_generated/task_runs/sample_debug_task_run/result.json`
- `/data/myproj/auto_reproduction/workspace/2604.01687/code/outputs/baselines/self_generated/task_runs/sample_refactor_task_run/result.json`
- `/data/myproj/auto_reproduction/workspace/2604.01687/code/outputs/evoskills_smoke/best_skills/sample_data_task.json`
- `/data/myproj/auto_reproduction/workspace/2604.01687/code/outputs/evoskills_smoke/best_skills/sample_debug_task.json`
- `/data/myproj/auto_reproduction/workspace/2604.01687/code/outputs/evoskills_smoke/best_skills/sample_refactor_task.json`
- `/data/myproj/auto_reproduction/workspace/2604.01687/code/outputs/evoskills_smoke/logs/sample_data_task.json`
- `/data/myproj/auto_reproduction/workspace/2604.01687/code/outputs/evoskills_smoke/logs/sample_debug_task.json`
- `/data/myproj/auto_reproduction/workspace/2604.01687/code/outputs/evoskills_smoke/logs/sample_refactor_task.json`
- `/data/myproj/auto_reproduction/workspace/2604.01687/code/outputs/evoskills_smoke/summary.json`
- `/data/myproj/auto_reproduction/workspace/2604.01687/code/outputs/evoskills_smoke/task_metrics.csv`
- `/data/myproj/auto_reproduction/workspace/2604.01687/code/outputs/evoskills_smoke/task_runs/sample_data_task_oracle/result.json`
- `/data/myproj/auto_reproduction/workspace/2604.01687/code/outputs/evoskills_smoke/task_runs/sample_debug_task_oracle/result.json`
- `/data/myproj/auto_reproduction/workspace/2604.01687/code/outputs/evoskills_smoke/task_runs/sample_refactor_task_oracle/result.json`
- `/data/myproj/auto_reproduction/workspace/2604.01687/code/requirements.txt`

## 执行概况

- 执行总耗时（runtime）: 11.5 秒
- 环境信息（environment）:
    - `key_packages`: —
    - `python_version`: Python 3.11.5
- 代码位置（code_output_dir）: `/data/myproj/auto_reproduction/workspace/2604.01687/code`
