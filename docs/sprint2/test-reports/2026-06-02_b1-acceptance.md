# 测试执行报告 - b1-acceptance

- **日期**：2026-06-02 00:30（本地时区）
- **执行人**：@测试工程师代理
- **Sprint**：sprint2
- **触发原因**：B1（paper_intake / paper_analysis 追加式扩展：*_zh/*_en schema + 输出语言策略段落 + backfill 兜底）由全栈开发代理标记完成，做独立验收
- **commit**：20888f7（B1 改动尚未提交，全部在工作区：`core/nodes/paper_intake.py`、`core/nodes/paper_analysis.py`、`tests/test_paper_analysis.py` 已修改 + `tests/test_sprint2_b1.py` 未追踪 + `docs/sprint2/dev-plan.md` checkpoint 勾选）

## 执行范围

- 命令：
  - `git diff` / AST 解析 / md5 比对实证 R-PC4 硬约束
  - `pytest --collect-only -q [-m e2e]`（收集结果核对）
  - `pytest -q -m "not e2e"`（核心回归，连跑 5 次：补强前 3 次 + 补强后 2 次）
  - `pytest tests/test_sprint2_b1.py -v`（B1 单文件独立性）
- 覆盖用例：`tests/test_sprint2_b1.py`（CP-B1-1~10，12 用例）+ 测试工程师补强 4 用例 = 16；联动 `tests/test_paper_analysis.py`（sp1 CP1~CP11）/ `tests/test_paper_intake.py`（CP1~CP8）经 CP-B1-10 包装跑通
- 是否包含 e2e：否（B1 不涉及真实 LLM；Prompt Cache 真实命中率回归属 E2 阶段，本次范围外）

## 结果摘要

- 通过：189（核心非 e2e 全量；其中 B1 文件 16）
- 失败：0
- 跳过：0（17 个 e2e 为 deselected，非 skip）
- 警告：1（`langgraph` 库级预存 `LangChainPendingDeprecationWarning`，sp1 即有，与 B1 无关）
- 总耗时：核心回归约 2.3~2.4s/次；B1 单文件 0.85s

## CP-B1-1~10 逐条独立复核结果（自行 Read 代码 + 实证，不依赖开发自报）

| CP | 结论 | 独立验证方式 |
|---|---|---|
| CP-B1-1 | PASS | Read paper_intake.py L44-57：`title_zh/abstract_zh/tldr_zh` 三字段在 properties，`required` 仅 5 项不含 *_zh |
| CP-B1-2 | PASS | Read paper_analysis.py L48-66：`method_summary_en/hardware_requirements_en` 在 properties 不在 required；`method_summary`(L34)/`hardware_requirements`(L42) description 含"中文主字段" |
| CP-B1-3 | PASS | AST 实证：`_LANGUAGE_POLICY_SECTION` / `_LANGUAGE_POLICY_SECTION_INTAKE` 均 module-level 静态 str 常量，无 JoinedStr/Call/BinOp/FormattedValue（无 f-string/动态生成） |
| CP-B1-4 | PASS（R-PC4 核心）| md5 实证：`_ANALYSIS_SYSTEM_PROMPT_BODY` / `_INTAKE_SYSTEM_PROMPT` 主体相对 sp1 定稿（C2 commit ea6a9b2）与 HEAD 均**字节级一致**（md5 完全相同，len 一致） |
| CP-B1-5 | PASS | 真实有效：用例断言两篇论文截去 `--- 当前论文上下文 ---` 尾部后 `body_a == body_b`，且前缀 == `BODY + "\n" + _LANGUAGE_POLICY_SECTION`；主体仍出现在等式中，强校验未被破坏 |
| CP-B1-6 | PASS | 漏写 title_zh → 回退 title + degraded_nodes 含 paper_intake + 1 条 degraded NodeError + caplog 断言 WARNING 非静默 |
| CP-B1-7 | PASS | 漏写 method_summary_en → 回退 + degraded + WARNING；独立 python 实证旧 mock（无 *_en）确会被标 degraded（证明新行为真实生效） |
| CP-B1-8 | PASS | 多 *_zh 漏写全部回退 + degraded_nodes 去重 1 次 + NodeError 1 条；含反向用例（*_zh 已存在不回退） |
| CP-B1-9 | PASS | 全文件 grep 两节点无 `create_llm`/`llm.invoke`/`ChatOpenAI`/`translate`；用例 inspect.getsource 扫 4 函数；测试工程师补强扩展到 2 个工具回填函数 |
| CP-B1-10 | PASS | sp1 paper_analysis CP1~CP11 + paper_intake CP1~CP8 经 main() 全绿；核心回归 5 次连跑零退化 |

## R-PC4 硬约束实证（最高优先级）

- `_ANALYSIS_SYSTEM_PROMPT_BODY` / `_INTAKE_SYSTEM_PROMPT` 主体相对 sp1：**字节级未改**（md5 在 sp1 定稿 C2、HEAD、工作区三点全一致；长度 1850 / 1908 不变）。
- `_LANGUAGE_POLICY_SECTION` 系列：**确为 module-level 静态字符串常量**，AST 验证无 f-string / Call / BinOp / FormattedValue 动态表达式。
- CP-B1-5 字节级一致测试：**真实有效**——`body_a == body_b`（两篇论文去尾部上下文后相同）+ 前缀 == `主体 + 静态常量`，主体常量参与等式，能捕捉任何主体改动。
- 拼接顺序与架构 §4.5 方案 A 一致：`BODY + "\n" + _LANGUAGE_POLICY_SECTION + "\n--- 当前论文上下文 ---\n" + tail`（paper_analysis）；intake 无动态尾部，`_INTAKE_SYSTEM_PROMPT + "\n" + _LANGUAGE_POLICY_SECTION_INTAKE`。
- 结论：**R-PC4 完全满足**。

## 对 sp1 既有测试 `tests/test_paper_analysis.py` 改动的裁定

git diff 显示 3 处改动，逐一裁定：

1. **CP10 `case_prompt_cache_prefix_stable` 字节断言升级**（`body_a == BODY` → `body_a == BODY + "\n" + _LANGUAGE_POLICY_SECTION`）：**语义无损的必要调整**。B1 在主体冻结前提下追加了静态语言策略段落，前缀自然延长到该段落末尾。断言仍保留 `body_a == body_b`（两篇论文主体一致）这一核心校验，且 `_ANALYSIS_SYSTEM_PROMPT_BODY` 仍出现在等式中——主体冻结约束依旧被强校验，**未掩盖退化**。

2. **CP4 `case_normal_path` / CP11 `case_backfill_sections_read_from_tools` mock 补 *_en**：**符合 B1 spec 的最小必要、语义保真调整，非掩盖退化**。裁定链：
   - CP4 含断言 `assert NODE_NAME not in degraded_nodes`，验证"clean path 不降级"。
   - sp2 把"clean path"重定义为 LLM 完整输出**必须含 *_en**（CP-B1-7 / 架构 §2.6.3：漏写 *_en 即降级）。
   - 独立 python 实证：旧 mock（不含 *_en）在 B1 下确会被 `_backfill_en_fields` 标 degraded → 旧 mock 已不再代表 clean path。补全 *_en 后重新代表真正 clean path，断言语义不变。
   - "漏写 *_en → degraded + WARNING"的**新行为有 CP-B1-7 独立专项覆盖**，无行为盲区。
   - CP4 其余断言（method_summary/datasets/metrics/sections_read/framework 等）全部保留未动；CP11 的本意（BUG-S1-03 sections_read 工具历史回填回归）依然被验证。

**总裁定：sp1 测试 3 处改动均为"测试随契约演进"的最小必要调整，语义无损，未掩盖任何真实退化。通过。**

## 补强用例（测试工程师在验收中追加，落 tests/test_sprint2_b1.py 共 12→16）

1. `test_aux_b1_analysis_no_backfill_when_en_present`：CP-B1-7 反向（analysis 侧此前缺反向，对齐 intake 的 no_backfill_when_zh_present）——*_en 已存在不回退、不 degraded。
2. `test_aux_b1_analysis_partial_en_omission`：部分漏写（只漏 method_summary_en）——只回退缺失项、保留已给项、仍标 degraded。
3. `test_aux_b1_en_omission_and_missing_core_dedup`：*_en 漏写 + 核心字段缺失叠加——degraded_nodes 去重 1 次、2 条语义不同的 degraded NodeError（*_en 回退 + missing core）分别记录。
4. `test_aux_b1_tool_backfill_funcs_also_no_secondary_llm`：CP-B1-9 扩展——扫 `_backfill_paper_meta_from_tools` / `_backfill_analysis_from_tools` 两个工具回填函数也无二次 LLM 调用（堵未来在工具回填里偷塞翻译的口子）。

## 稳定性

- 核心非 e2e 回归连跑 5 次（补强前 3 次 185/185 + 补强后 2 次 189/189），0 失败 0 跳过 0 抖动。
- B1 单文件 16 用例独立运行全绿（无运行顺序依赖）。
- `--collect-only`：默认收集 206（189+17 e2e），`-m e2e` 收集 17（185 deselected）。e2e 默认不跑（项目设计为靠凭证 skipif 决定，本次 -m "not e2e" 显式排除）。
- 1 warning 为 langgraph 库级预存 `LangChainPendingDeprecationWarning`（sp1 既有，与 B1 无关，记录在案）。

## 失败排查

无失败。

## 后续动作

- **E2 阶段**：Prompt Cache 真实链路命中率回归（对照 S-3 基线 R=0.7669 / AC-S2-08 守门 ≥ 0.7286），在凭证就绪、真实 LLM e2e 验收时跑。本次已用 CP-B1-5 字节级一致测试 + md5 主体实证为该回归提供前置安全垫（前缀字节稳定已确认）。
- **遗留观察（非阻塞）**：B1 改动尚未 git commit（在工作区）；建议 Maria 触发提交后，新提交点上再跑一次核心回归确认与本次一致。
- 无需 Maria 决策的阻断点；无 BUG。

## 验收结论

**PASS**。CP-B1-1~10 全部独立复核命中；R-PC4 硬约束 md5 + AST 双重实证满足；sp1 测试改动裁定为语义无损必要调整；补强 4 边界用例全绿；核心回归 5 次连跑零退化零抖动；治理范式（非静默 WARNING + 无二次 LLM）合规。
