# 干净复现代码 fixture（AC-S5-06 审计误报防线靶）

> **本 fixture 内文件不得修改**——它是真实性审计"不误报"断言的回归基线（AC-S5-06：
> 审计对无 smell 的干净代码不误报，结论不降档）。改动即破坏断言语义。
> 姊妹靶（审计应命中的造假现场）见 `tests/fixtures/regression_2604_01687/`。

## 来源

- 新造样本（非 workspace 复制），固化日期 2026-07-09（Sprint 5 批次 3 前置门，dev-plan §3.4 / architecture §9.4）
- 固化人：@测试工程师代理

## 内容：一个最小情感分类复现 eval（4 文件，真读输入、真算分）

| 文件 | 职责 |
|---|---|
| `data/ground_truth.jsonl` | 12 条标注数据（id + text + label，三分类 positive/negative/neutral，标签分布 5/4/3 非对称） |
| `data/predictions.jsonl` | 12 条模型预测（id + label，含 3 处真实错判） |
| `metrics.py` | 纯指标函数：accuracy / per-label F1 / macro F1，全部由标签对算术推导；空输入抛 `ValueError` 而非返回常量 |
| `evaluate.py` | eval 入口：真读两个 JSONL、按 id 对齐（缺失/多余 id 抛错）、调 metrics 计算、写 `summary.json` |

验证方式（已于固化日真跑通过，stdlib-only 无三方依赖）：

```bash
cd tests/fixtures/clean_code_sample
python evaluate.py --output /tmp/summary.json
# → accuracy 0.75，macro_f1 ≈ 0.7389，per_label_f1 {positive: 0.8, negative: 0.75, neutral: 0.6667}
```

注意：勿用默认 `--output`（会在 fixture 内落 `outputs/`）；测试中运行请显式指到 tmp_path，并留意 `__pycache__` 残留。

## 为何审计不应命中（与回归靶逐条对照）

| smell 类别（回归靶现场） | 本样本的干净对照 |
|---|---|
| **答案泄漏**：`skill_generator.py` 把 verifier 的 `expected_skill_keywords` 抄进被评估产物 | ground truth 只在 `evaluate.py` 中作**单向比对**使用，从不回流进被评估对象（predictions 是固定输入产物，eval 只读不生成） |
| **硬编码分数**：`task_executor.py` 按 baseline 类型写死 0.0/0.3/0.1 | 所有分数均为 `correct/total`、`2*tp/(2*tp+fp+fn)` 类算术结果；代码中无任何字面量分数赋值 |
| **常量结局 / 编造规则**：难度 ±0.2/-0.1 调分，结论预写 | 无按实验名/难度/baseline 分支调分的逻辑；换数据文件即换结果 |
| **mock 分支**：绕开真实计算的模拟路径 | 无 mock/simulation 分支；退化输入（空集、长度不齐、id 不对齐）一律抛异常，不静默给分 |
