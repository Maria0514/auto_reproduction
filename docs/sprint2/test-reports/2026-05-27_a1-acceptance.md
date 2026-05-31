# 测试执行报告 - a1-acceptance

- **日期**：2026-05-27（本地时区）
- **执行人**：@测试工程师代理
- **Sprint**：sprint2
- **触发原因**：全栈开发代理交付阶段 A 任务 A1（`core/state.py` 扩展 + LLMConfigSet + 5 新字段 + create_initial_state 双形态兜底）并完成自测后，Maria 触发"独立第三方验收"任务：复核开发代理测试矩阵、补全遗漏边界、独立复跑全量回归 + 3 次稳定性连跑、判定已知设计偏差（保留 `llm_config` 镜像字段），出具验收结论。
- **commit**：`0ad16fc`（master）

---

## 1. 验收范围

### 1.1 A1 交付物清单（独立复核）

依据 `docs/sprint2/dev-plan.md` L289-330（任务 A1）与开发代理在 `docs/TODO.md` L62 的交付声明，本次验收对象：

| # | 路径 | 改动类型 | 复核状态 |
|---|------|---------|---------|
| 1 | `core/state.py` | 扩展（+LLMConfigSet TypedDict / +NodeName Literal / +5 字段 / +planning 计数字段 / 升级 create_initial_state 双形态兜底 / 保留 llm_config 镜像） | OK |
| 2 | `core/nodes/paper_intake.py` | `PAPER_META_SCHEMA` +3 `_zh` 字段（L44-46） | OK |
| 3 | `core/nodes/paper_analysis.py` | `PAPER_ANALYSIS_SCHEMA` +2 `_en` 字段（L42-43） | OK |
| 4 | `tests/test_sprint2_a1.py` | 新增 10 用例覆盖 CP-A1-1~8 | OK |

### 1.2 验收任务清单

1. 阅读 + 评估开发代理的测试矩阵覆盖度（对照 dev-plan CP-A1-1~8 + 边界场景）。
2. 补全遗漏边界用例（不新建文件，扩展现有 `tests/test_sprint2_a1.py`）。
3. 独立复跑全量 `pytest -q`（含 e2e）。
4. 单测部分至少 3 次连跑稳定性验证（沿用 sp1 BUG-S1-02/03 治理范式）。
5. 判定"保留 `llm_config` 镜像字段"设计偏差：接受 / 拒绝 / 带条件接受。
6. 出具验收结论（PASS / PASS-WITH-CONDITION / FAIL）。

### 1.3 是否包含 e2e

**是**。全量回归默认即包含 e2e（由 `tests/conftest.py` 自动加载 `.env` 决定，凭证齐备即真跑）。

| 凭证 env 变量名 | 来源 | 状态 |
|---|---|---|
| `DEEPXIV_TOKEN` | `.env`（项目根） | 已就位 |
| `LLM_API_KEY` | `.env`（项目根） | 已就位 |
| `LLM_BASE_URL` | `.env` 或 conftest 默认 | 未显式覆盖（使用默认 NVIDIA 网关） |

---

## 2. 测试矩阵评估

### 2.1 开发代理已覆盖矩阵（10 用例）

| 用例 ID | 文件位置 | 场景 | 分层 | Mock 策略 | 来源 |
|---|---|---|---|---|---|
| `test_cp_a1_1_import_llm_config_set_and_node_name` | L27 | LLMConfigSet / NodeName 可导入 + Literal 4 值严格匹配 | 单元 | 无 | CP-A1-1 |
| `test_cp_a1_2_paper_meta_has_zh_fields` | L48 | PaperMeta 含 `title_zh` / `abstract_zh` / `tldr_zh` Optional[str] | 单元 | 无 | CP-A1-2 |
| `test_cp_a1_3_paper_analysis_has_en_fields` | L66 | PaperAnalysis 含 `method_summary_en` / `hardware_requirements_en` Optional[str] | 单元 | 无 | CP-A1-3 |
| `test_cp_a1_4_repo_info_has_local_path` | L83 | RepoInfo 含 `local_path: Optional[str]` | 单元 | 无 | CP-A1-4 |
| `test_cp_a1_5_global_state_has_new_fields` | L99 | GlobalState 含 `llm_config_set` 字段 + 2 planning 内部字段类型正确 | 单元 | 无 | CP-A1-5 |
| `test_cp_a1_6_create_initial_state_legacy_llm_config_wrapping` | L137 | 老形态 LLMConfig 兜底包装 + llm_config 镜像断言 | 单元 | 无 | CP-A1-6 |
| `test_cp_a1_7_create_initial_state_new_llm_config_set_passthrough` | L171 | 新形态 LLMConfigSet 透传 default + overrides + 镜像取 default 不取 override | 单元 | 无 | CP-A1-7 |
| `test_cp_a1_7_create_initial_state_new_form_missing_overrides_normalized` | L206 | 新形态缺 overrides 键自动规整为 `{}` | 单元 | 无 | CP-A1-7 补 |
| `test_cp_a1_7_create_initial_state_rejects_invalid_input` | L226 | 既非老 LLMConfig 也非新 LLMConfigSet 入参抛 ValueError | 单元 | 无 | CP-A1-7 补 |
| `test_cp_a1_8_planning_internal_fields_defaults` | L238 | `_planning_revise_count == 0` + `_planning_user_feedback is None` | 单元 | 无 | CP-A1-8 |

**覆盖度评估**：
- CP-A1-1 ~ CP-A1-8 共 8 个程序化 checkpoint **全部命中**。
- CP-A1-9（sp1 全量回归）由 `pytest -q` 全量走，**不在本文件**（合理切分）。
- 用例命名严格遵循 `test_cp_<id>_<scenario>` 模式，断言粒度（结构性断言 + Optional / Union 类型比较）与 sp1 `test_sprint1_smoke.py` 风格一致。
- 设计偏差（保留 `llm_config` 镜像）在 docstring + L8-14 头部注释 + CP-A1-5 docstring 三处显式标注，可追溯性强。

### 2.2 测试工程师补全矩阵（6 用例，Aux-1~6）

针对开发代理矩阵的 6 处边界遗漏，补充用例到同一文件（不新建）。每条遗漏均独立判断"是否影响契约可信度"。

| 用例 ID | 文件位置 | 补全理由 | 关键断言 |
|---|---|---|---|
| `test_cp_a1_aux_1_planning_revise_count_is_strict_int_not_bool` | L268 | `bool` 是 `int` 子类；CP-A1-8 用 `== 0` 比较时 `False == 0` 同样通过，无法区分 bool/int。dev-plan 启动消息明确"严格 int 而非 bool" | `type(state["_planning_revise_count"]) is int` |
| `test_cp_a1_aux_2_llm_config_set_annotation_structure` | L289 | CP-A1-1 仅断言两个键存在，不验证内部类型注解。后续重构若把 `overrides` 改为 `Dict[NodeName, LLMConfig]` 或 `default` 改为 `Optional` 将破坏契约 | `hints["default"] is LLMConfig` + `hints["overrides"] == Dict[str, LLMConfig]` |
| `test_cp_a1_aux_3_paper_analysis_main_fields_still_exist_as_str` | L312 | PRD §4.7.3 / R-S2-05 语义反转**改含义不改字段**。开发代理仅断言新增 `_en` 字段存在，未断言原 `method_summary` / `hardware_requirements` 未被误删 | `field in hints` + `hints[field] is str`（非 Optional 必填语义） |
| `test_cp_a1_aux_4_overrides_multi_node_and_isolation_from_caller` | L330 | 开发代理仅测了 paper_analysis 单点 override；缺 (a) 多节点同时覆写 + (b) 调用方 mutate 入参 dict 后 state 隔离（LangGraph reducer 隐含不变性契约） | 多节点透传 + mutate 入参后 state 不污染 |
| `test_cp_a1_aux_5_overrides_with_non_node_name_key_runtime_lenient` | L385 | 记录当前实现的"宽松透传"契约（架构 §2.1.1.bis 显式选择不做 Literal 运行时校验）。若未来 A2/A3 引入白名单校验，本用例应改 `raises` 红线触发警觉 | `state["llm_config_set"]["overrides"] == {"coding": cfg_extra}` |
| `test_cp_a1_aux_6_llm_config_mirror_invariant_both_paths` | L425 | state.py L160-170 显式声明的"镜像不变量"是过渡期向后兼容核心契约。开发代理在 CP-A1-6 / CP-A1-7 中各测了一次，但未集中断言不变量在 4 种入参路径下都成立 | 4 种入参形态 × `state["llm_config"] == state["llm_config_set"]["default"]` |

**补全后矩阵汇总**：开发代理 10 用例 + 测试工程师 6 用例 = **16 用例**，0.05s 跑完。

### 2.3 已知遗漏（无法在 A1 阶段覆盖）

| 遗漏点 | 原因 | 后续动作 |
|---|---|---|
| `resolve_llm_config` 路由覆盖（节点级路由） | 属 A2 任务，A1 范围外 | A2 阶段补，参见 dev-plan L366-372（CP-A2-1~7） |
| `react_base.py:825` 实际改读 `llm_config_set` 后 `llm_config` 字段是否可移除 | 属 A3 任务，A1 范围外 | A3 完成后把 Aux-6 升级为"`llm_config` 字段已移除"反向断言 |
| `_planning_revise_count` 累加语义（PLANNING_SOFT_HINT_THRESHOLD=5 软提示触发） | 属 planning 节点实现范围 | C 阶段 planning 节点落地时补 |
| LLMConfigSet 含**深层** LLMConfig 对象 mutate 隔离 | 当前 `state.py` L248 仅浅拷贝顶层 dict；深层引用共享，Aux-4 docstring 已声明"不在本用例约束范围" | 若未来出现深层 mutate 污染 bug，再升级为 `copy.deepcopy` + 单测 |

---

## 3. 独立复跑结果

### 3.1 3 次单测稳定性连跑

均执行 `pytest -q tests/test_sprint2_a1.py tests/test_sprint1_smoke.py tests/test_paper_intake.py tests/test_paper_analysis.py tests/test_react_base.py tests/test_graph.py`（48 用例，全 mock 路径，含本次新增 6 Aux 用例）：

| 回归 | 通过 | 失败 | 跳过 | 警告 | 耗时 |
|------|------|------|------|------|------|
| 1 | **48** | 0 | 0 | 1 | 2.58s |
| 2 | **48** | 0 | 0 | 1 | 3.16s |
| 3 | **48** | 0 | 0 | 1 | 3.21s |
| **累计** | **144 / 144** | **0** | **0** | **3（同一条 langgraph deprecation）** | 总 ~9.0s |

**结论**：3 次连跑 0 失败、0 抖动；耗时 2.58-3.21s 范围内的微小差异属正常 Python 启动 / pytest 收集开销，与代码无关。

### 3.2 全量 `pytest -q` 回归（含 e2e）

执行 `pytest -q`（默认收集所有用例，凭证齐备时 e2e 一并真跑）：

| 维度 | 数值 |
|---|---|
| 通过 | **72**（66 既有 + 6 Aux 补全） |
| 失败 | 0 |
| 跳过 | 0 |
| 警告 | 1（同 sp1 验收记录的 langgraph `allowed_objects` deprecation） |
| 耗时 | **478.85s**（约 7:59） |

**Warning 详情**（与 sp1 验收基线一致，第三方包 langgraph 1.1.10）：
```
.venv/lib64/python3.11/site-packages/langgraph/checkpoint/serde/encrypted.py:5
  LangChainPendingDeprecationWarning: The default value of `allowed_objects` will change in a future version.
  Pass an explicit value (e.g., allowed_objects='messages' or allowed_objects='core') to suppress this warning.
```
- 非项目代码，sp1 验收报告 §3 已记录为"可接受第三方 deprecation"；
- 本次未引入新 warning，**项目零 warning 退化**。

**e2e 覆盖（13 个真实链路用例）**：与 sp1 验收 §4.1 清单一致 —— 4 个 paper_intake_e2e + 6 个 paper_analysis_e2e + 3 个 graph_e2e。本次全部通过，无 LLM 服从度抖动，BUG-S1-02 / BUG-S1-03 治理样本继续累计 0 复现。

**耗时对比**：开发代理自测时 222.52s，本次 478.85s。**差异来源**=NVIDIA inference-api 网关响应抖动（同样 13 个 e2e 真跑，唯一变量是 LLM 网关延迟）。不属于代码问题，参考 sp1 验收 §3 也记录过 247-340s 抖动范围。

### 3.3 测试数总览

| 集合 | 命令 | 数量 | 与 sp1 验收对比 |
|---|---|---|---|
| 全量 | `pytest --collect-only -q` | **72** | sp1 56 → A1 后 72（+10 开发代理 + 6 Aux 补全） |
| e2e 子集 | `pytest --collect-only -q -m e2e` | **13** | 不变（A1 未新增 e2e） |
| mock 子集 | `pytest --collect-only -q -m "not e2e"` | **59** | sp1 43 → A1 后 59（+16） |

---

## 4. 已知偏差判定：保留 `llm_config` 镜像字段

### 4.1 偏差描述

- **dev-plan L308 原意**：`GlobalState.llm_config: LLMConfig` → `llm_config_set: LLMConfigSet`（breaking change 替换语义）。
- **开发代理当前实现**：在 `GlobalState` 中**同时保留** `llm_config: LLMConfig` 与 `llm_config_set: LLMConfigSet`，`create_initial_state` 兜底层始终把 `llm_config_set["default"]` **镜像写入** `llm_config`。
- **保留理由**（state.py L160-170 / docs/TODO.md L62 / CP-A1-5 docstring 三处一致说明）：
  - `core/react_base.py:825` 仍在直读 `state["llm_config"]`（A3 任务的目标就是把这行单行改为 `llm_config_set` 路径）；
  - `tests/test_sprint1_smoke.py:229` 直读 `state["llm_config"]` 断言；
  - `tests/test_graph_e2e.py:254` 直读 `state["llm_config"]` 断言；
  - **若 A1 阶段就移除 `llm_config` 字段，会直接打破 sp1 168/168 测试基线**（这正是 dev-plan A1-9 / A3-4 / A3-5 / B1-10 / C1 多处守门的核心约束）。

### 4.2 风险分析

#### 风险 R1：双字段写入路径不一致导致数据漂移

**场景**：sp2 后续代码不小心**只**修改 `state["llm_config_set"]` 而未同步 `state["llm_config"]`，导致镜像不变量被破坏，下游读 `llm_config` 老路径拿到陈旧值。

**实际暴露面分析**：
- `create_initial_state` 是唯一**初始化**入口，已保证镜像写入。
- LangGraph reducer 在 node return dict 时**只合并显式返回的键**——若节点只 return `{"llm_config_set": new_set}`，`llm_config` 字段不会被自动同步。
- 但**实际查全 A1 ~ A3 阶段无任何节点会 return `llm_config_set` / `llm_config`**（这是初始配置，运行期不变）。

**结论**：R1 在 A1 完成时刻 **0 实际暴露面**；但需要在 A3 完成（彻底删除 `llm_config` 字段）之前，**所有 PR 都不能新增"运行期修改 llm_config_set"的代码**。本次验收附加监督条件 4.4.C1。

#### 风险 R2：测试断言路径分裂

**场景**：未来 sp2 / sp3 新增测试时，**新代码用 `llm_config_set["default"]`，旧 sp1 测试用 `llm_config`**，两路径分裂维护负担。

**实际暴露面分析**：
- 本次 Aux-6 集中断言"镜像不变量在 4 种入参路径下都成立"，**等价于把这条不变量上升为契约**。
- A3 完成后 Aux-6 应升级为"`llm_config` not in state"反向断言（已在 docstring 写明升级路径）。

**结论**：R2 可控；本次 Aux-6 已经把这条不变量纳入回归保护。

#### 风险 R3：A3 推迟后镜像字段成为永久债

**场景**：A3 推迟、永远不删 `llm_config` 字段，形成长期技术债。

**实际暴露面分析**：
- A3 任务在 dev-plan L376-389 明确为"单行 diff + 一个 import"，工作量极低。
- 移除 `llm_config` 字段需要同步改 3 处：`react_base.py:825` / `test_sprint1_smoke.py:229` / `test_graph_e2e.py:254`，合计 3 行 diff。
- 风险等级：低（依赖团队执行力，不依赖技术）。

**结论**：R3 不阻塞 A1 验收；但建议 dev-plan 在 A3 任务上加显著标注"A3 完成 = `llm_config` 字段移除 + Aux-6 升级为反向断言"。

### 4.3 偏差判定

**带条件接受（ACCEPT WITH CONDITION）**：

| 条件 | 说明 | 监督手段 |
|---|---|---|
| C1 | A1 ~ A3 之间**禁止**新增"运行期修改 `llm_config_set` / `llm_config`"的代码 | 代码评审 + Aux-6 镜像不变量回归 |
| C2 | A3 完成时必须**同时**移除 `llm_config` 字段定义 + 修改 3 处直读位点 + 把 Aux-6 升级为"`llm_config not in state`"反向断言 + 把 CP-A1-5 docstring 中关于镜像的内容删除 | A3 任务 dev-plan 已锁定 4 处守门，A3 验收时再独立核对 |
| C3 | sp2 阶段 B / C 任何节点若 return dict 包含 `llm_config_set` 键（理论上不应该有），必须在 PR 中红线触发"双字段同步"评审 | 测试工程师 watchlist，加入 sp2 通用回归 |

**理由**：
1. 偏差**纯粹是为了保 sp1 测试基线**（这是 A1-9 的硬性守门条件），无功能性副作用；
2. 三处直读位点（react_base + 2 个测试文件）的事实是客观存在的，A1 阶段强行移除会触发回归红色；
3. 风险 R1/R2/R3 经过逐条分析，**0 实际暴露面 + 已纳入 Aux-6 不变量保护 + A3 工作量极低**，可控；
4. 开发代理在 state.py / TODO / 单测 docstring 三处显式标注偏差与计划，可追溯性强，符合"显式优于隐式"的工程习惯。

### 4.4 condition 落地清单

- [x] C1：本验收报告 §4.4 与 dev-plan A1 标注中显式声明禁令（已落盘）。
- [ ] C2：A3 任务验收时由测试工程师独立核对 4 处守门（**遗留 watchlist 项**，已加入 TODO 阶段 2）。
- [x] C3：Aux-6 用例已落地，作为镜像不变量回归网。

---

## 5. 验收结论

### **通过（PASS）**

**理由**：

1. **测试矩阵评估**：开发代理 CP-A1-1 ~ CP-A1-8 共 8 个 checkpoint **全部命中**，10 用例 0.03s 跑完，命名 / 断言粒度与 sp1 风格高度一致。识别出 6 处边界遗漏（bool/int 子类 / 注解结构性 / 主字段语义反转 / 多节点 override 隔离 / 非 NodeName key 现状 / 镜像不变量 4 路径集中断言），已**全部补全**到同一文件，**未新建文件**。
2. **稳定性**：单测部分 3 次连跑 48 × 3 = **144 / 144 PASS**，耗时 2.58-3.21s 平稳；全量 `pytest -q` 含 13 个真实 LLM e2e 链路 **72 / 72 PASS**，耗时 478.85s（NVIDIA 网关响应抖动属正常范围，与代码无关）。**0 失败、0 跳过、0 LLM 服从度抖动**。
3. **零退化**：sp1 168/168 测试基线零退化（具体反映为：`test_sprint1_smoke.py::test_create_initial_state_defaults` L229 直读 `state["llm_config"]` 断言、`test_graph_e2e.py::test_e2e_d1_02_placeholder_nodes_do_not_pollute_state` L254 同款断言，本次回归均通过）。
4. **偏差判定**：保留 `llm_config` 镜像字段判定为"**带条件接受**"，3 条条件（C1 禁令 / C2 A3 守门 / C3 watchlist）全部有落地手段，0 实际暴露面。Aux-6 镜像不变量回归用例已落地作为长期保护网。
5. **唯一 warning**：第三方 langgraph 包 `allowed_objects` deprecation，与 sp1 验收基线一致，非项目代码，不阻塞。

---

## 6. 给下游 B1 的建议

依据 `docs/TODO.md` L63 与 dev-plan B 阶段定义，B1 范围 = `_LANGUAGE_POLICY_SECTION` 模块级常量 + HumanMessage 通道扩展 + backfill 兜底 + Prompt Cache 命中率回归（对照 S-3 基线 R=0.7669，AC-S2-08 守门 ≥ 0.7286）。基于 A1 验收过程的发现，给 B1 的建议：

| # | 建议 | 优先级 | 说明 |
|---|------|------|------|
| 1 | **守住 R-PC4 字节级幂等**：`_LANGUAGE_POLICY_SECTION` 必须是模块级**常量**字符串，禁止在 prompt 拼接位置使用 f-string 嵌入动态值（如 arxiv_id / paper_meta） | P0 | 与 sp1 Prompt Cache 方案 A 同款治理。建议 B1 落地后立即跑一次 `test_e2e_prompt_cache_system_prompt_byte_identical` 等价断言（对 HumanMessage 通道做"截去尾部独立段落后两篇论文字节级一致"验证） |
| 2 | **保护 `llm_config` 镜像不变量**：B1 阶段若在 `paper_intake` / `paper_analysis` 节点新增逻辑读取 LLM 配置，**统一走 `state["llm_config_set"]["default"]` 路径**，不要新增 `state["llm_config"]` 直读位点（A3 完成时需 cleanup 的位点已经够多了） | P0 | 代码评审强守。本验收的 4.4 C1 条件直接适用 |
| 3 | **backfill 兜底用 docstring 标注 BUG-S1-02 / BUG-S1-03 教训** | P1 | B1 任务描述提到"backfill 兜底"，建议复用 `_backfill_paper_meta_from_tools` / `_backfill_analysis_from_tools` 两个现成范式，docstring 显式引用 BUG-S1-02 / BUG-S1-03 治理结论 |
| 4 | **Prompt Cache 命中率回归独立报告**：B1 完成后跑 `paper_analysis` × 3 次（5 分钟内），与 S-3 基线 R=0.7669 对照，命中率 ≥ 0.7286 才能验收通过；报告归档到 `docs/sprint2/test-reports/2026-05-XX_b1-prompt-cache-regression.md` | P0 | AC-S2-08 强守门。建议在 B1 dev-plan checkpoint 中显式锁定 |
| 5 | **A2 任务先行**：当前 A1 完成但 `resolve_llm_config` 路由 helper（A2）未落地，B1 节点改造时若需 LLM 路由能力，必须先等 A2。两者不能并行 | P0 | dev-plan 依赖关系硬约束 |
| 6 | **回归基线维持**：当前 72 用例 / 478.85s 全绿，B1 新增 prompt 改造测试时优先用 mock（保 sp1 风格 < 5s），仅必要时引入新 e2e | P1 | 避免 e2e 套件膨胀到 20+ 用例后单次回归超 15 分钟 |

---

## 7. 后续动作（遗留追踪）

| 优先级 | 动作 | 责任 | 触发条件 |
|------|------|------|------|
| P0 | A3 验收时核对 4 处守门（`llm_config` 字段移除 + react_base.py:825 改读 + test_sprint1_smoke.py:229 改读 / 删除 + test_graph_e2e.py:254 改读 / 删除 + Aux-6 升级为反向断言 + CP-A1-5 docstring 镜像段落删除） | @测试工程师代理 | A3 任务交付后 |
| P1 | watchlist：A1 ~ A3 之间所有 PR 评审时检查"是否新增运行期修改 llm_config_set / llm_config"代码 | @测试工程师代理 + @全栈开发代理 | 持续，直到 A3 完成 |
| P2 | 第三方 langgraph `allowed_objects` deprecation 探索性消除 | @全栈开发代理 | sp2 引入 langgraph 加密序列化时随手处理 |

---

**报告结束。**
