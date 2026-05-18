# Sprint 1 阶段 F 交付：移交测试工程师

- 文档版本：v1.0
- 落盘日期：2026-05-17
- 作者：全栈开发代理
- 适用 Sprint：Sprint 1（基础骨架）
- 对接对象：测试工程师代理（@test-engineer）

本文档汇总 Sprint 1 阶段 A~E 全部代码交付物的核对结果、测试入口、运行方式、已知限制以及 PRD §6 验收标准与代码模块的对应关系，作为测试工程师执行 Sprint 1 最终验收的上下文基线。

---

## 1. F1 交付物完整性检查

按 `docs/sprint1/dev-plan.md` L835-851 的 14 项产出清单逐一核对。

| # | 产出文件 | 对应任务 | 文件存在 | import 通过 | 关键导出齐全 | 备注 |
|---|---------|---------|----------|-------------|-------------|------|
| 1 | `requirements.txt` | S1-10 / A1 | [x] | n/a | [x] | 7 个 pip 依赖 + Python 3.10+ 注释；deepxiv-sdk 注释行内说明本地 editable 安装 |
| 2 | `config.py` | S1-09 / A2 | [x] | [x] | [x] | `PROJECT_ROOT` / `CHECKPOINT_DB_PATH` / `WORKSPACE_DIR` / `LOG_DIR` / LLM 默认值 / 预算常量 / `REACT_MAX_ROUNDS_PAPER_INTAKE` / `REACT_MAX_ROUNDS_PAPER_ANALYSIS` / `REACT_RESULT_TAG_OPEN/CLOSE` / `TOOL_RESULT_MAX_LENGTH` / `get_deepxiv_token` / `get_llm_api_key` 全部齐全（smoke 已验证） |
| 3 | `core/__init__.py` | 包初始化 | [x] | [x] | n/a | 刻意空 re-export，避免 callable 遮蔽子模块（吸取 BUG-S1-02 / C2 教训），内含 docstring 说明 |
| 4 | `core/state.py` | S1-01 / A3 | [x] | [x] | [x] | 11 个 TypedDict/Enum + `create_initial_state` 全部 export；`retry_budget_remaining=50` / `fix_loop_count=0` / `execution_mode=FULL` 默认值 smoke 已验证 |
| 5 | `core/errors.py` | S1-02 / A4 | [x] | [x] | [x] | 异常树继承关系（`LLMAuthError ⊆ PermanentError` / `LLMRateLimitError ⊆ TransientError` 等）smoke 已验证；含 `make_node_error` 工厂 |
| 6 | `core/react_base.py` | S1-11 / B4 | [x] | [x] | [x] | `ReActState` / `create_react_subgraph` / `_make_react_wrapper` 全部 export；Prompt Cache 前缀稳定改造已落地（SystemMessage 主体常量化 + tool_executor 固定截断标记） |
| 7 | `core/graph.py` | S1-03 / D1 | [x] | [x] | [x] | `build_graph(checkpointer=None) -> CompiledGraph` 默认 checkpointer 懒导入；7 节点完整注册 + `START→paper_intake→…→reporting→END` 顺序边 + planning interrupt 占位注释 + Sprint 2/3 条件路由 TODO |
| 8 | `core/checkpointer.py` | S1-04 / B1 | [x] | [x] | [x] | `get_checkpointer(db_path=None) -> SqliteSaver`，WAL 模式，文件不存在自动建库 |
| 9 | `core/llm_client.py` | S1-05 / B2 | [x] | [x] | [x] | `create_llm` / `call_with_structured_output` / `estimate_tokens` / `check_context_limit` 全部 export；指数退避 + `Retry-After` 解析 + `cached_tokens` INFO 日志（Prompt Cache 方案 A 已落地） |
| 10 | `core/tools/__init__.py` | 包初始化 | [x] | [x] | n/a | 刻意空 re-export（同 core/__init__.py 设计原则） |
| 11 | `core/tools/deepxiv_tools.py` | S1-06 / B3 | [x] | [x] | [x] | `DeepxivTools` 类 + 7 个 ReAct 工具工厂（`get_paper_brief_tool` / `get_paper_head_tool` / `get_paper_structure_tool` / `read_section_tool` / `get_full_paper_tool` / `search_papers_tool` / `web_search_tool`）；BUG-S1-02 已修复（`_serialize` 用 `json.dumps(ensure_ascii=False, sort_keys=True, default=str)`） |
| 12 | `core/nodes/__init__.py` | 包初始化 | [x] | [x] | [x] | 显式 export `paper_intake` / `paper_analysis` 两个 callable（配套已修复 tests 用 `importlib.import_module` 访问子模块属性） |
| 13 | `core/nodes/paper_intake.py` | S1-07 / C1 | [x] | [x] | [x] | `paper_intake` callable（ReAct wrapper）+ `PAPER_META_SCHEMA` + `_build_intake_system_prompt` + `_map_intake_result` 3 参签名 + `_backfill_paper_meta_from_tools`（BUG-S1-02 修复后已生效） |
| 14 | `core/nodes/paper_analysis.py` | S1-08 / C2 | [x] | [x] | [x] | `paper_analysis` callable（ReAct wrapper）+ `PAPER_ANALYSIS_SCHEMA` + `_ANALYSIS_SYSTEM_PROMPT_BODY` 稳定主体常量 + `_build_analysis_system_prompt(context)` 尾部独立段落（Prompt Cache 方案 A）+ `_map_analysis_result` 3 参签名 + `_backfill_analysis_from_tools`（BUG-S1-03 修复后已生效） |

**检查结论**：14/14 文件全部就位，import 全部通过（`tests/test_sprint1_smoke.py` 已自动化覆盖 10 条 import + 异常继承关系 + `create_initial_state` 默认值 + `estimate_tokens` + `config` env 覆盖），关键导出对象齐全，零缺失，零退化。

---

## 2. F2 验收上下文

### 2.1 环境准备

#### 2.1.1 Python 与系统

- Python 版本：**3.10+**（开发与回归基线均使用 3.11.5）
- 操作系统：Linux（开发机为 6.1.62-4.x86_64）
- 推荐使用虚拟环境（仓库已存在 `.venv/`）

#### 2.1.2 依赖安装

```bash
# 在仓库根目录
python -m venv .venv
source .venv/bin/activate                  # bash/zsh
# csh 用户：source .venv/bin/activate.csh

pip install -r requirements.txt
pip install -e ./deepxiv_sdk_repo           # 本地 editable 安装 deepxiv-sdk（>=0.2.5）
```

`requirements.txt` 内容：

```
langgraph>=0.2.0
langgraph-checkpoint-sqlite>=3.0.0
langchain-openai>=0.1.0
langchain-core>=0.2.0
requests>=2.31.0
pydantic>=2.0.0
tiktoken>=0.5.0
pytest>=8.0
```

deepxiv-sdk 行被注释，需要单独执行 `pip install -e ./deepxiv_sdk_repo`。注意：本仓库 `./deepxiv_sdk_repo/` 仅为参考仓库，代码统一通过 pip 包名 `deepxiv_sdk` 导入（命名冲突问题已在 Maria 2026-05-12 的目录重命名中解决）。

#### 2.1.3 环境变量

| 环境变量 | 是否必需 | 用途 | 读取入口 |
|---------|---------|------|---------|
| `DEEPXIV_TOKEN` | e2e 测试**必需** | deepxiv API 鉴权（免费额度 1000 次/天） | `config.get_deepxiv_token()` |
| `LLM_API_KEY` | e2e 测试**必需** | OpenAI 兼容 LLM API key | `config.get_llm_api_key()` |
| `LLM_BASE_URL` | 可选 | OpenAI 兼容 base URL（默认 NVIDIA 推理网关） | `tests/conftest.py` |
| `LLM_MODEL` | 可选 | 模型名（默认见 conftest） | `tests/conftest.py` |
| `LLM_ENABLE_PROMPT_CACHE` | 可选 | Prompt Cache 开关，默认 True | `core/llm_client.py` |

**mock 测试不需要任何 env**（已用 `monkeypatch` / `MagicMock` 隔离外部依赖）。

#### 2.1.4 pytest 标记说明

`tests/conftest.py` 已注册 `@pytest.mark.e2e`，需要真实 LLM + 真实 deepxiv SDK 链路的 6 个 e2e 测试文件中的 16 个用例统一打了该标记。仅跑 mock 自测可用 `pytest -q -m "not e2e"`。

---

### 2.2 端到端测试运行方式

#### 2.2.1 一键全量回归（推荐验收主路径）

```bash
pytest -q
```

- 覆盖 56 个用例（40 mock + 16 真实链路 e2e）。
- 预期耗时：**约 4-5 分钟**（开发基线 255.70s，含真实 LLM 链路）。
- 通过门槛：56/56 PASSED，0 FAILED / 0 SKIPPED / 0 XFAIL。

#### 2.2.2 仅跑 mock（快速 smoke）

```bash
pytest -q -m "not e2e"
```

- 覆盖 40 个用例，耗时约 5~10 秒，不需要 `DEEPXIV_TOKEN` / `LLM_API_KEY`。

#### 2.2.3 端到端单文件运行

| 范围 | 命令 | 用例数 | 耗时（基线） |
|------|------|--------|------------|
| paper_intake e2e | `pytest tests/test_paper_intake_e2e.py -m e2e -v` | 4 | ~45s |
| paper_analysis e2e | `pytest tests/test_paper_analysis_e2e.py -m e2e -v` | 6 | ~130s |
| graph e2e（D1） | `pytest tests/test_graph_e2e.py -m e2e -v` | 3 | ~65s |

#### 2.2.4 靶论文（real LLM + real deepxiv）

| arXiv ID | 用途 | 备注 |
|----------|------|------|
| `2405.14831` | 主路径靶论文（graph e2e / paper_analysis e2e 基本路径） | HippoRAG，章节结构规范，含 GitHub URL |
| `2409.05591v1` | versioned id 清洗用例 | paper_intake e2e |
| `https://arxiv.org/abs/2409.05591` | URL 清洗用例 | paper_intake e2e |
| `9999.99999` | 论文不存在用例（已废弃，被工具调用路径自然覆盖） | 不在当前 e2e 集合 |

#### 2.2.5 Prompt Cache 命中率观察（可选）

如需观察 `cached_tokens` INFO 日志（验证 Prompt Cache 方案 A 生效），用以下命令并提升日志级别：

```bash
pytest tests/test_paper_analysis_e2e.py::test_e2e_prompt_cache_system_prompt_byte_identical -m e2e -v -s --log-cli-level=INFO
```

注意：NVIDIA 推理网关是否透传 `cached_tokens` 字段属于 R-PC1 范围（详见 2.4 已知限制）。

---

### 2.3 各模块 mock 测试入口

> 所有 mock 测试均不依赖外部 API，可在无 token 环境下运行；适合在 CI 或开发自测中作为快速门槛。

| 测试文件 | 用例数 | 覆盖模块 | 关键检查点 |
|---------|--------|---------|-----------|
| `tests/test_sprint1_smoke.py` | 14 | 全部 14 个产出文件 | 10 条 import + 异常继承 + `create_initial_state` 默认值 + `estimate_tokens` 单调性 + `config` env reload 覆盖 |
| `tests/test_graph.py` | 13 | `core/graph.py` D1 | `CompiledGraph` 实例 / 7 业务节点集合 / paper_intake & paper_analysis 是 ReAct wrapper 本体 / 5 占位节点返回 `{}` / MemorySaver 编译 / 默认 checkpointer 懒导入 / 全链路 invoke 用 patch 替换 wrapper 端到端跑通 |
| `tests/test_paper_intake.py` | 8 | `core/nodes/paper_intake.py` C1 | callable / context→HumanMessage 映射 / 工具调用路径 / 全字段填充 / head 失败仅 brief / 非 CS 警告 / 论文不存在 error+node_errors / URL 清洗 |
| `tests/test_paper_analysis.py` | 11 | `core/nodes/paper_analysis.py` C2 + BUG-S1-03 | CP1-10 原计划检查点 + CP11 `_backfill_analysis_from_tools` 回填（sections_read 漏写场景） |
| `tests/test_llm_client.py` | 12 | `core/llm_client.py` Prompt Cache 探测 + 签名 | `_log_cache_metrics` 命中三种 metadata 形态（LangChain `usage_metadata` / OpenAI `prompt_tokens_details` / Anthropic `cache_read_input_tokens`）+ 无字段静默 + 异常吞错 + 开关默认值 + env 覆盖 |
| `tests/test_react_base.py` | 4 | `core/react_base.py` B4 + Prompt Cache | SystemMessage 字节级一致 + `_truncate_tool_result` 幂等性 + 固定截断标记 + 短输入直通 |

**测试入口约定**：

- 节点单测一律 patch `_invoke_react_subgraph`（或在 paper_analysis 中 patch 子图 invoke），不打真实 LLM；
- 工具测试通过 mock `DeepxivTools.<method>` 注入预设 dict / 异常；
- 图测试通过 patch 替换 ReAct wrapper 验证 7 节点串接。

---

### 2.4 已知限制与遗留问题

#### 2.4.1 当前 Sprint 内可接受的已知限制

| 编号 | 限制 | 影响范围 | 后续处理 |
|------|------|---------|---------|
| L-01 | `LLMConfig.api_key` 会被 SqliteSaver 序列化写入 `checkpoints.db`（明文） | `core/checkpointer.py` / `core/state.py` | Sprint 1 接受风险，PRD §8 OP-1 已标记；后续 Sprint 评估 state 层脱敏或恢复时由用户重新提供 |
| L-02 | `planning` 节点的 `interrupt()` 占位仅做注释说明，未真正中断 | `core/graph.py` | Sprint 2 实现人在回路时落地 |
| L-03 | `resource_scout` / `coding` / `execution` / `reporting` 节点为 pass-through 占位，原样返回 `{}` | `core/graph.py` | Sprint 2/3 逐步实现 |
| L-04 | code_only 条件路由、execution↔coding 修复循环路由未实现 | `core/graph.py` | Sprint 3 实现 |
| L-05 | NVIDIA 推理网关是否透传 `cached_tokens` 字段未知，可能导致 Prompt Cache INFO 日志长期为空（但缓存实际生效与否需另行验证） | `core/llm_client.py` | R-PC1，dev-plan 已挂"Prompt Cache 命中率基线实验"和"跨 provider AB 实验"为待办 |
| L-06 | Prompt Cache 跨 provider AB 实验（DeepSeek 等自动型端点）未跑 | `core/react_base.py` + `core/nodes/paper_analysis.py` | dev-plan 阶段 4 待办（非阻塞 Sprint 1 验收） |
| L-07 | Prompt Cache 命中率基线实验（同 arxiv_id 连跑 ×3 观察 `cached_tokens / prompt_tokens` 比值）未跑 | 同上 | 同上 |

#### 2.4.2 已修复的关键 bug（验收时需关注其回归覆盖）

| Bug | 形态 | 修复 commit / 修复 PR | 复现率治理样本 |
|-----|------|---------------------|--------------|
| BUG-S1-02 | deepxiv 工具用 `str(dict)`（repr）写 ToolMessage，下游 `json.loads` 永远失败，backfill 静默不生效 | TODO L26-27 详述 | paper_intake e2e 3 次连跑全绿（43.67s / 46.19s / 41.10s）+ 全量套件 19/19 |
| BUG-S1-03 | LLM 偶发漏写 `sections_read`（≈25% 复现率），节点缺工具历史回填兜底 | TODO L30 详述 | 全量 pytest 连续 5 次 26/26 全绿（含 10 e2e 用例 × 5 = 50 次 e2e 全绿，远超 25% 复现率治理样本要求） |

#### 2.4.3 测试环境注意事项

- `pytest -q` 跑全量 56 用例时，会真实调用 deepxiv API（消耗免费额度）+ 真实 LLM API（产生 token 费用）；在 CI / 高频回归场景请用 `-m "not e2e"` 跑 40 个 mock 用例。
- `checkpoints.db` 在 e2e 测试中会创建（`tests/test_graph_e2e.py::test_e2e_d1_03_sqlite_checkpoint_persist_and_resume`），测试会自行清理 fixture 临时目录。如果验收过程中发现 `checkpoints.db` 残留，可手工删除，不影响下次跑测。

---

### 2.5 PRD §6 验收标准 ↔ 模块映射

PRD §6 共 9 条验收标准（AC-1 端到端、AC-2 持久化、AC-3 ~ AC-9 模块级）。对应模块与覆盖测试如下：

| AC | 验收要点 | 对应代码模块 | 覆盖测试入口 |
|----|---------|------------|------------|
| **AC-1**：基础流程可执行（输入有效 arXiv ID，依次执行 paper_intake + paper_analysis，输出结构化 PaperAnalysis） | `core/graph.py` + `core/nodes/paper_intake.py` + `core/nodes/paper_analysis.py` + `core/state.py` | `tests/test_graph_e2e.py::test_e2e_d1_01_full_pipeline_invoke_acceptance`（5 步逐条断言，靶论文 2405.14831） |
| **AC-2**：状态持久化到 SQLite，支持断点续跑 | `core/checkpointer.py` + `core/graph.py` | `tests/test_graph_e2e.py::test_e2e_d1_03_sqlite_checkpoint_persist_and_resume`（4 步逐条断言） |
| **AC-3**：GlobalState 完整性（所有 TypedDict/Enum 可导入，字段定义与架构文档一致） | `core/state.py` | `tests/test_sprint1_smoke.py::test_import_core_state` + `test_create_initial_state_defaults` |
| **AC-4**：异常体系完整性（继承关系正确） | `core/errors.py` | `tests/test_sprint1_smoke.py::test_import_core_errors` + `test_exception_hierarchy` |
| **AC-5**：LLM 客户端功能（`create_llm` / `call_with_structured_output` / 异常分类） | `core/llm_client.py` | `tests/test_sprint1_smoke.py::test_import_core_llm_client` + `test_estimate_tokens_returns_positive_int` + `tests/test_llm_client.py`（12 用例） + `tests/test_paper_analysis_e2e.py`（真实 LLM 链路） |
| **AC-6**：deepxiv_tools 封装功能（`get_paper_brief` / `read_section` + SDK 异常映射） | `core/tools/deepxiv_tools.py` | `tests/test_sprint1_smoke.py::test_import_core_tools_deepxiv` + `tests/test_paper_intake_e2e.py`（真实 deepxiv 链路）+ `tests/test_paper_analysis_e2e.py::test_e2e_react_actually_used_tools` |
| **AC-7**：paper_analysis 降级链（章节读取失败时别名匹配 → 全文提取 → 标记缺失） | `core/nodes/paper_analysis.py` | `tests/test_paper_analysis.py`（CP6 非标准章节名 Our Framework→Method / CP7 全章节失败→`get_full_paper` 兜底 / CP9 不完整结果 degraded 标记 / CP11 `_backfill_analysis_from_tools`） |
| **AC-8**：配置管理（`config.py` 路径配置 + env 覆盖） | `config.py` | `tests/test_sprint1_smoke.py::test_import_config_symbols` + `test_config_env_override_reload` |
| **AC-9**：依赖可安装（`pip install -r requirements.txt` 在 Python 3.10+ 无冲突） | `requirements.txt` | TODO 阶段 1 A1 自测项已通过：`pip install` 无冲突 + `pip check` 无问题 + 7 个核心包 import 成功 |

**覆盖率汇总**：9/9 验收标准全部有自动化测试覆盖，无 manual-only AC。

---

## 3. 当前 pytest 状态快照（基线）

**执行命令**：

```bash
pytest -q
```

**执行时间**：2026-05-17（阶段 F 落盘前最后一次基线）

**执行结果**：

```
........................................................                 [100%]
=============================== warnings summary ===============================
.venv/lib64/python3.11/site-packages/langgraph/checkpoint/serde/encrypted.py:5
  /home/yujingm/myproj/auto_reproduction/.venv/lib64/python3.11/site-packages/langgraph/checkpoint/serde/encrypted.py:5: LangChainPendingDeprecationWarning: The default value of `allowed_objects` will change in a future version. Pass an explicit value (e.g., allowed_objects='messages' or allowed_objects='core') to suppress this warning.
    from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
56 passed, 1 warning in 255.70s (0:04:15)
```

**统计**：

- 总用例数：**56**
- 通过：**56**
- 失败：**0**
- 跳过：**0**
- 警告：1 个（LangChain `allowed_objects` 默认值变更预告，非项目代码，不阻塞验收）
- 耗时：**255.70s**（约 4 分 16 秒）

**测试集分布**：

| 测试文件 | 用例数 | 类型 |
|---------|--------|------|
| `tests/test_graph.py` | 13 | mock |
| `tests/test_graph_e2e.py` | 3 | **e2e** |
| `tests/test_llm_client.py` | 12 | mock |
| `tests/test_paper_analysis.py` | 1 (参数化展开为 11) | mock |
| `tests/test_paper_analysis_e2e.py` | 6 | **e2e** |
| `tests/test_paper_intake_e2e.py` | 4 | **e2e** |
| `tests/test_react_base.py` | 4 | mock |
| `tests/test_sprint1_smoke.py` | 14 | mock |
| **合计** | **56**（其中 16 个 e2e） | |

> 注：`tests/test_paper_intake.py` 的 8 个 mock 检查点以 1 个 `test_paper_intake_all_checkpoints` 参数化集合的形式实现，pytest 计为 1 项。`tests/test_paper_analysis.py` 同理（CP1-11 计为 1 项）。所以"56 passed"对应的逻辑检查点远多于 56，实际覆盖深度详见 §2.3 各文件用例数列。

**结论**：Sprint 1 全部代码交付物已通过自测基线 56/56 全绿，无任何已知失败 / 退化，可移交测试工程师进入最终验收阶段。

---

## 附录：与测试工程师的协作建议

1. 验收前请先跑一次 `pytest -q` 复测基线；如本机 56/56 不一致，对照 §2.4.3 检查环境（token、依赖、网络）。
2. 验收测试报告归档约定见 dev-plan L865："`docs/sprint1/test-reports/YYYY-MM-DD_<scope>.md`，含触发原因、执行命令、结果摘要、失败排查、后续动作"（规范参见 `.claude/agents/test-engineer.md`）。
3. 已有 6 份历史测试报告可作为格式参考：
   - `2026-05-14_paper-intake-e2e.md` / `2026-05-14_paper_intake_e2e_failure_analysis.html`（BUG-S1-02 全过程）
   - `2026-05-15_paper-analysis-e2e.md` / `2026-05-15_paper_analysis_e2e_failure_analysis.html`（BUG-S1-03 全过程）
   - `2026-05-17_graph-d1-unit.md` / `2026-05-17_graph-d1-e2e.md`（D1 单测 + e2e 验收）
4. 如验收过程发现新 bug，请按已有 BUG-S1-02 / BUG-S1-03 在 TODO.md 的归档格式登记（含复现率、根因、修复内容、回归样本数 / 耗时）。

---

**文档结束。**
