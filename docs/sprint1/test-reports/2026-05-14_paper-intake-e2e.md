# 测试执行报告 - paper-intake-e2e

- **日期**：2026-05-14 03:30 PDT
- **执行人**：@测试工程师代理
- **Sprint**：sprint1
- **触发原因**：Maria 手动触发，验证 paper_intake e2e 当前是否能在凭证就绪环境跑通（C1 提交后首跑真实端到端）。
- **commit**：3d5a650

## 执行范围

- 命令：
  - `pytest --collect-only -q tests/test_paper_intake_e2e.py`
  - `pytest tests/test_paper_intake_e2e.py -m e2e -v -s --durations=0`
  - 复跑（用于评估 flakiness）：
    - `pytest tests/test_paper_intake_e2e.py::test_e2e_versioned_id_cleanup tests/test_paper_intake_e2e.py::test_e2e_full_url_cleanup -m e2e -v -s --log-cli-level=INFO`（跑了 2 次）
- 覆盖用例（4 个，全部 `@pytest.mark.e2e`）：
  - `test_e2e_versioned_id_cleanup`（输入 `2405.14831v3`，验证版本号清洗）
  - `test_e2e_full_url_cleanup`（输入 `https://arxiv.org/abs/2405.14831`，验证 URL 抽取）
  - `test_e2e_plain_id_cs_category`（输入 `2405.14831`，验证 CS 分类）
  - `test_e2e_node_errors_empty_on_success`（输入 `2405.14831`，验证成功路径不写 node_errors）
- 是否包含 e2e：是
- 凭证来源：项目根 `.env`（被 `tests/conftest.py` 通过 `dotenv.load_dotenv` 自动加载），涉及 env 变量名 `LLM_API_KEY`、`DEEPXIV_TOKEN`、`LLM_BASE_URL`、`LLM_MODEL`（不在本报告中记录值）。
- 靶论文：arXiv `2405.14831`（HippoRAG: Neurobiologically Inspired Long-Term Memory for Large Language Models）。

## 结果摘要

### 主跑（第一次）
- 通过：2（`test_e2e_plain_id_cs_category`、`test_e2e_node_errors_empty_on_success`）
- 失败：2（`test_e2e_versioned_id_cleanup`、`test_e2e_full_url_cleanup`）
- 跳过：0
- 警告：1（`LangChainPendingDeprecationWarning: allowed_objects` 来自 `langgraph/cache/base/__init__.py`，第三方包内部告警，本仓库无法直接消除）
- 总耗时：35.17s
- 各用例耗时：versioned 9.16s / url 8.69s / node_errors_empty 8.30s / plain_id_cs 8.06s

### 复跑（第二、三次，仅两个失败用例）
- 第二次：`versioned` 通过、`url` 失败（耗时 19.40s）
- 第三次：`versioned` 失败、`url` 失败（耗时 33.98s）

### 稳定性统计（跨 3 次主/复跑）
- `test_e2e_versioned_id_cleanup`：1/3 通过（2 失败）
- `test_e2e_full_url_cleanup`：0/3 通过（3 失败）
- `test_e2e_plain_id_cs_category`：1/1 通过（未复跑）
- `test_e2e_node_errors_empty_on_success`：1/1 通过（未复跑）

### Prompt Cache / Token 观测
- `core.llm_client._log_cache_metrics` 的 INFO 日志在本次跑里**未出现在 pytest 捕获中**（即 NVIDIA 网关 ChatCompletion response 的 `usage` metadata 中 `cached_tokens` 字段当前未被读到，或为 0）。
- 每个用例 ReAct rounds=2、remaining_budget=48（初始 50，扣 2），符合预期。
- 工具调用轨迹一致：`get_paper_brief` → `get_paper_head`，两次 HTTP 请求到 `inference-api.nvidia.com`。

## 失败排查

### 失败用例 1：`test_e2e_full_url_cleanup`（0/3 通过）
- 文件：`tests/test_paper_intake_e2e.py::test_e2e_full_url_cleanup`
- 失败类型：**生产代码 bug（LLM 服从度 / prompt 稳健性）**
- 关键报错：

```
update = {'paper_meta': {'arxiv_id': '2405.14831', 'title': 'HippoRAG: ...',
          'authors': ['Bernal Jiménez Gutiérrez', 'Yiheng Shu', 'Yu Gu',
          'Michihiro Yasunaga', 'Yu Su'], 'categories': [], 'abstract': 'In order to thrive...'},
          'current_step': 'paper_intake', 'node_errors': [], 'retry_budget_remaining': 48}

AssertionError: categories 为空: []
```

ReAct 轨迹（INFO 日志）：

```
get_paper_brief: success for 2405.14831
get_paper_head: success for 2405.14831
[paper_intake] 完成: arxiv_id=2405.14831, title=HippoRAG: ...
[paper_intake] react wrapper done: rounds=2, remaining_budget=48
```

### 失败用例 2：`test_e2e_versioned_id_cleanup`（1/3 通过 = 2 失败）
- 文件：`tests/test_paper_intake_e2e.py::test_e2e_versioned_id_cleanup`
- 失败类型：**同上，生产代码 bug，呈 flaky 形态**
- 失败 update 与日志路径与用例 1 完全相同，区别仅在输入是 `2405.14831v3`。

### 排查步骤与结论

1. **首先怀疑 LLM 跳过 head 工具调用** → 看 INFO 日志，`get_paper_head: success for 2405.14831` 出现在所有失败跑次中。排除。
2. **怀疑 deepxiv head API 不返回 categories** → 直接构造一段脚本调用 `DeepxivTools().get_paper_head("2405.14831")`，结果：
   ```
   HEAD keys: ['abstract', 'arxiv_id', 'authors', 'categories', 'citations',
              'github_url', 'journal_name', 'keywords', 'publish_at', 'sections',
              'src_url', 'title', 'tldr', 'token_count', 'venue']
   head.categories = ['cs.CL', 'cs.AI']
   ```
   原始数据**确实**含 `categories=['cs.CL', 'cs.AI']`。排除。
3. **怀疑 `_truncate` 截断破坏了 head 输出** → `TOOL_RESULT_MAX_LENGTH=8000`，head 返回 `str(result)` 后大概率长度不超过 8K（categories 字段位于字典开头附近），且日志未触发 `[truncated]` 标记。排除。
4. **结论**：`get_paper_head` 工具结果**确实**到达了 LLM，但 LLM 在最终 `<result>...</result>` JSON 中**丢失** `categories` 字段（输出空数组 `[]`）。
5. **场景相关性**：
   - 当输入是纯 ID `2405.14831`（无需清洗）→ categories **稳定**填充（2/2 通过）。
   - 当输入是 `2405.14831v3`（需去版本号）→ categories **2/3 丢失**。
   - 当输入是 `https://arxiv.org/abs/2405.14831`（需 URL 抽取）→ categories **3/3 丢失**。
   - 强相关：**输入需要清洗时**，LLM 在 system prompt 步骤 1（清洗）+ 步骤 4（字段合并）之间，似乎容易遗漏 categories 这一字段。这与 prompt 模板中"4. 字段合并规则"未把 categories 单独列为合并条目（只列了 authors/abstract/categories 一行混在一起）可能相关。
6. **判定**：
   - 这不是测试代码 bug：`PaperMeta.categories: List[str]` 是必填契约，architecture.md L1887/L1902 明确"head 优先"，PRD L324 也把 categories 列为必须输出。测试断言符合契约。
   - 这不是外部依赖抖动：deepxiv 已稳定返回 categories；问题在 LLM 节点产物。
   - 这是**生产代码（prompt 设计 + 兜底逻辑）问题**：
     - prompt 在"清洗"分支下未足够强调字段合并的完整性。
     - `_map_intake_result` 当前没有"head 工具结果回填"的兜底——如果 LLM 在 `<result>` 中漏写 categories，节点不会从 head 工具调用历史里捞回来。
   - 复现率：清洗类输入约 5/6 失败，纯 ID 输入 0/2 失败；**不能视为偶发抖动**。

### 处置

- **未自行修复**（按 agent 规范，不改 `core/`）。
- **未立即转交全栈开发代理子代理**——这次按 Maria 在任务里允许的"自行判断"模式，**列入 Bug 报告等 Maria 决策**。理由：
  1. 修复方向有 2 种思路（A: 强化 prompt 字段合并指令；B: 在 `_map_intake_result` 用 head 工具历史回填），Maria 可能想自己拍板优先级。
  2. 也可能 Maria 想先 NVIDIA 网关 / DeepSeek AB 对比，再决定是否调 prompt——目前样本量（3 次）已经够定性但还不足以排除模型版本差异。
  3. C2（paper_analysis）还没开工，调 prompt 可能要与 C2 一并设计 prompt 风格。
- **标记为 known-failing**（不视为 flaky 待观察——复现率过高）。

### Bug 报告（草稿，待 Maria 拍板后再发给全栈开发代理）

- **Bug ID**：`BUG-S1-02`
- **复现路径**：
  ```
  pytest tests/test_paper_intake_e2e.py::test_e2e_full_url_cleanup -m e2e -v -s
  pytest tests/test_paper_intake_e2e.py::test_e2e_versioned_id_cleanup -m e2e -v -s
  ```
  连跑 2-3 次即可复现 categories 字段被 LLM 漏写。
- **期望行为**：
  - PRD §4.2.x（L324）："`categories`（来自 `head()`）"是必返字段。
  - architecture.md L1902：`categories=h.get("categories", brief.get("categories", []))`——以 head 优先。
  - dev-plan.md L544 / L559：head 优先填 categories。
  - 综合：paper_intake 在 head 工具调用成功且 head 返回含 `categories` 时，最终 `paper_meta["categories"]` 不应为空。
- **实际行为**：
  - 输入 URL（`https://arxiv.org/abs/2405.14831`）：3/3 categories=[]。
  - 输入带版本号 ID（`2405.14831v3`）：2/3 categories=[]。
  - 输入纯 ID（`2405.14831`）：0/2 失败。
  - LLM 实际调用了 `get_paper_head`，工具返回成功，但 LLM 在 `<result>` JSON 输出中漏写 categories 字段（输出 `[]`）。
- **影响范围**：
  - paper_intake 节点本身：categories 字段在清洗类输入下不可靠。
  - 下游：架构 §12.5 / paper_intake "学科范围校验" 依赖 categories；本 bug 会导致下游误判为"非 CS 论文"，触发不必要的 warning（虽 architecture.md L1922 对空 categories 做了 early return，不会崩，但 warning 噪声 + 后续 resource_scout 可能漏掉 CS 上下文）。
  - 不阻塞 C2（paper_analysis 不消费 categories），但**阻塞 paper_intake 单节点 e2e 验收**。
- **建议修复方向**（按 ROI 排序）：
  1. **首选：在 `_map_intake_result` 加 head 工具结果回填**——记录 ReAct 子图中 `get_paper_head` 的最后一次成功返回，若 LLM 输出的 `categories` 为空但 head 返回非空，则用 head 值兜底。这与"head 优先"的架构契约一致，且不依赖 LLM 服从度。
  2. **次选：强化 prompt**——把"字段合并规则"中 categories 单独列一行（与 title/abstract 平级），并在末尾"输出要求"补一句"若 head 返回了 categories 且非空，最终 JSON 的 categories 不得为空数组"。但仍受 LLM 服从度限制，不如 #1 稳定。
  3. **可选：在 `_INTAKE_SYSTEM_PROMPT` 步骤 4 拆开输入清洗与字段合并的 chain-of-thought**——目前两者混在同一份 prompt，LLM 在"清洗"分支注意力更集中，"合并"环节会被压缩。可以让 prompt 显式输出 "step1_cleaned_id"、"step4_field_merge_check" 中间字段，再总结到 `<result>`。

## 后续动作

- [ ] **等 Maria 决策**：本 Bug 立即转交全栈开发代理修复，还是先做更多复跑（如换 DeepSeek 端点验证模型相关性），或留到 C2 一并设计 prompt。
- [ ] 修复后回归命令：`pytest tests/test_paper_intake_e2e.py -m e2e -v -s --durations=0`，4/4 通过且**至少连续 3 次**全绿才可关 Bug（避免被 LLM 服从度的偶发性误判为已修复）。
- [ ] 顺带观察项（不阻塞）：
  - **测试代码副作用**：pytest 在 fixture 渲染中把 `LLMConfig` 字典 dump 到 stdout，会**泄露 api_key 值**（已观察到）。`tests/test_paper_intake_e2e.py::llm_config` 返回的是普通 dict，pytest 失败回溯时会 repr 出来。建议在测试基建层把 api_key 字段 mask（如 `LLMConfig` 在 conftest 中包一层带 `__repr__` 屏蔽的轻量 wrapper，或在 e2e fixture 中 `mask=api_key[:4] + "..."`）。**这是 Maria 可决定要不要做的卫生改进**，不阻塞 paper_intake 修复。
  - **`pytest.ini` 缺 `addopts = -m "not e2e"`**：当前 e2e mark 用例靠 `skipif` 控制是否跑，凭证就绪时会"默默真跑"，这与 agent 规范 `addopts` 默认排除 e2e 的约定不一致。**视使用习惯而定**，若希望"默认 pytest 不触发任何外网调用"，应补 `addopts = -m "not e2e"`；若希望"凭证在就尽量跑"，保持现状即可。本次执行无影响。
  - **第三方告警**：`langgraph.cache.base` 的 `LangChainPendingDeprecationWarning: allowed_objects` 持续输出，源于 `langgraph` 依赖 `langchain-core` 的接口将来变化。等 langgraph 升级修复即可，本仓库无需动作。
- [ ] 缺失覆盖（已知遗漏）：
  - 真实 URL `.pdf` 后缀（如 `arxiv.org/pdf/2405.14831v3.pdf`）的清洗，未覆盖。
  - 旧格式 ID（`cs/0507019` 之类）的兜底，未覆盖。
  - 论文真的不存在（如 `9999.99999`）的 error 路径，e2e 未覆盖（单元测试已覆盖）。
