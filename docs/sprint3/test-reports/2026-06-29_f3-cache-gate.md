# Sprint 3 任务 F3 / CP-F3-2 Prompt Cache 命中率守门报告

- **日期**：2026-06-29
- **执行**：@主控（Maria 授权后真跑；脚本 `scripts/spike_coding_prompt_cache.py`，`SPIKE_F3_AUTHORIZED=1` 硬闸门）
- **靶论文**：arXiv:2405.14831（HippoRAG，deepxiv 缓存命中省配额）
- **Provider / model**：NVIDIA inference-api gateway / `azure/openai/gpt-5.4`

---

## 裁决：CP-F3-2 PASS

`R_after = mean(R_2, R_3) = (0.8709 + 0.9306) / 2 = **0.9008** ≥ 守门 0.7286`（= sp2 S-3 基线 0.7669 × 0.95）。

### 跑数据（coding × 3，首轮 cold + 2 轮 warm）
| run | cached / prompt | hit | calls | elapsed | degraded |
|---|---|---|---|---|---|
| #1 (cold) | 8960 / 11527 | 77.7% | 4 | 36.1s | False |
| #2 (warm) | 16256 / 18665 | 87.1% | 6 | 37.3s | False |
| #3 (warm) | 14976 / 16093 | 93.1% | 6 | 46.6s | False |

3 次总耗时 120.06s（cache TTL 通常 ~5 分钟）。`R_after` 取 warm 两轮（#2/#3）均值。

### 结论
coding 节点新注入的 prompt 段（B2 工具说明 + `<METRICS>` 约定 + sp2 语言策略等）经 sp1/sp2「方案 A」前缀治理（静态主体在前、论文级动态上下文走 HumanMessage / 尾部常量段），命中率 **0.9008 远超守门 0.7286**，且高于 sp2 基线 0.7669。结合 CP-F3-1（coding system prompt 主体跨两篇不同论文**完全字节一致**），Prompt Cache 字节级幂等在 coding 节点成立。

### 旁证一致性
本次正式跑（77.7% / 87.1% / 93.1%）与 F3 开发阶段误跑观察（63%~98%）一致，互为印证。

### LLM 服从度观察（复现 F2）
spike 日志再现 LLM 猜错 deepxiv 章节名（`method`/`approach`/`retrieval`/`abstract` not found），agent 兜底继续、不影响 cache 命中率结果。已记入 handoff 已知限制（read_section 章节名命中率优化项）。

### 原始指标
`workspace/runs/spike-f3-coding-prompt-cache_20260629-071427.json`（非 git 跟踪）。
