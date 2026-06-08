# Test Plan: D1 增强改动（api_key 回退 + 表单字段/校验调整 + load_dotenv）

- **日期**：2026-06-08
- **作者**：@测试工程师代理
- **Sprint**：sprint2
- **类型**：**开发前测试设计（Test Plan Driven）**——代码尚未改动，本文档不跑测试，仅清单化覆盖维度供架构师/Maria align 后再开发。
- **契约权威源**：`docs/sprint2/architecture.md` §2.7.2（api_key 回退点裁定，2026-06-07 Maria 拍板方案 A）+ §2.8.2（字段/校验，刚更新）+ §2.7.1（start_task / `_refresh_llm_config_set` 链路）+ §10 索引
- **历史约束**：`docs/sprint2/test-reports/2026-06-04_d1-acceptance.md`（OBS-D1-01 stale 风险 + api_key 不落盘原则；OBS-D1-02 streamlit set_value 超界静默拒绝回退默认；L-A3-02 pytest.ini markers 注释不符）

---

## 1. 概述

### 1.1 改动范围（4 文件，3 改 1 引）

| # | 文件 | 改动 | 契约 |
|---|------|------|------|
| 1 | `core/llm_client.py::create_llm` | **api_key 回退点**：`config["api_key"]` strip 后为 "" 时回退 `config.get_llm_api_key()`（读 .env）。签名不变（仍 1 参）。`resolve_llm_config` 不改 | §2.7.2 |
| 2 | `ui/components/llm_config_form.py` | (a) base_url/model **仅 default panel** 预填 config getter（override 不预填）；(b) max_tokens 由 `number_input` 改 **slider**（min=512/max=16384/step=512/默认=8192，修历史 4096）；(c) `_validate_panel` **取消 api_key 非空硬校验**；(d) 提交成功路径末端加**兜底校验**（default api_key 空 且 get_llm_api_key() 也空 → st.error + 返回 None） | §2.8.2 |
| 3 | `app.py` | 补 `load_dotenv`（与 conftest 一致），import 区当前 L25-36 无 dotenv | §2.7.2 末条 |
| 4 | `config.py` | **不改**，仅被引用（`get_llm_api_key`/`get_llm_base_url`/`get_llm_model`/`DEFAULT_LLM_MAX_TOKENS=8192`） | — |

### 1.2 链路上 api_key 状态（§2.7.2，命门）

```
表单("")
  → LLMConfigSet.default.api_key=""          （表单层不回退）
  → _refresh_llm_config_set("")               （_refresh 层不回退）
  → create_initial_state("")
  → SqliteSaver checkpoint("" 恒空可断言)      ← 安全不变量实证点
  → resolve_llm_config("")                     （纯选路，不回退）
  → create_llm 回退 .env 真实 key（仅进程内存，不回写 state）  ← 回退唯一落点
```

### 1.3 现有基建复用清单（不另造轮子）

| 复用对象 | 文件:位置 | 用途 |
|---|---|---|
| `_make_llm_config_set(api_key_default=...)` | `tests/test_app_controller.py` L92 | 构造 1 default + 4 override 的 LLMConfigSet（每条独立 api_key），改造支持空 api_key |
| 真实 SqliteSaver + checkpoint 真实回读范式 | `tests/test_app_controller.py::test_cp_d2_9` L450+ | **安全不变量集成层骨架**：真实 `get_checkpointer(tmp db)` + 真实最小 graph + `snapshot.values` 读回 |
| 真实 build_graph + patch 4 节点范式 | `tests/test_graph.py::test_full_graph_invoke_with_patched_react_wrappers` L186+ | 回退集成层骨架（注意安全不变量须用真实 SqliteSaver(tmp)，非 MemorySaver） |
| AppTest 驱动表单 + proto.type mask 探测 | `tests/test_llm_config_form.py`（D1 既有 35 项） | L1/L2 表单层校验/预填/slider 用例直接扩展 |
| e2e skip 范式 | `tests/test_sprint2_c1_e2e.py` L63/76：`pytestmark=pytest.mark.e2e` + `skip_if_no_creds=skipif(not (get_llm_api_key() and get_deepxiv_token()))` | L4 后端 e2e |
| `test_create_llm_signature_unchanged` | `tests/test_llm_client.py` L191（断言 `len(params)==1`） | **回归校验**：回退是函数体逻辑，签名不变，此用例须仍通过 |
| `test_llm_routing.py` resolve_llm_config 纯选路 | 19 项 | **回归校验**：resolve_llm_config 不改，全部须仍通过 |

---

## 2. 分层 Test Matrix

> 列：用例 ID / 层级 / 对应契约维度 / 测试手段 / mock 边界 / 预期断言 / 烧 token。
> 维度编号对应需求点名：D1=安全不变量 / D2=回退正确性 / D3=校验反转 / D4=预填+OBS-D1-01 / D5=slider / D6=load_dotenv。

### L1 单元（全 mock，默认运行，零 token）

| 用例 ID | 维度 | 测试手段 | mock 边界 | 预期断言 | token |
|---|---|---|---|---|---|
| T-D1E-U01 | D2 | `create_llm` 收空 api_key（""）→ 回退 | monkeypatch `llm_client.get_llm_api_key`→"env-key"；patch `ChatOpenAI` 捕获入参 | ChatOpenAI 收到 `api_key=="env-key"`（非 ""） | 否 |
| T-D1E-U02 | D2 | `create_llm` 收**非空** api_key（"sk-user"）→ **不回退** | monkeypatch get_llm_api_key→"env-key"；patch ChatOpenAI | ChatOpenAI 收到 `api_key=="sk-user"`；get_llm_api_key **未被调用**（或调用了但值未采用） | 否 |
| T-D1E-U03 | D2 | `create_llm` 收纯空白 api_key（"  "）→ strip 后视为空 → 回退 | 同 U01 | ChatOpenAI 收到 env-key（strip 后空 == 空语义对齐 _validate_panel） | 否 |
| T-D1E-U04 | D2 | `create_llm` 空 api_key 且 get_llm_api_key() 返回 None → 不在 create_llm 抛 | monkeypatch get_llm_api_key→None；patch ChatOpenAI | ChatOpenAI 收到 None（或 ""）；`create_llm` **本身不抛 LLMError/不拦截**（交 ChatOpenAI/后续 invoke），契约 §2.7.2「回退取到 None 仍 404，由兜底校验在表单层拦」 | 否 |
| T-D1E-U05 | D2 | 回退**不回写 config 入参 dict** | 调用前后 deepcopy 对比 | 入参 LLMConfig 的 api_key 字段调用后仍为 ""（回退值不写回 state，仅进程内存） | 否 |
| T-D1E-U06 | D2(回归) | `create_llm` 签名仍 1 参 | `inspect.signature` | `len(params)==1`（复用 test_llm_client L191 语义；回退是函数体逻辑） | 否 |
| T-D1E-U07 | D3 | `_validate_panel`：base_url/model 合法、api_key 空 → **校验通过**（返回非 None LLMConfig，api_key 字段为 ""） | 无（纯函数直测，复用 D1 既有直测范式） | 返回 cfg 非 None；`cfg["api_key"]==""`；errors 空；**无 "api_key 不能为空" 错误** | 否 |
| T-D1E-U08 | D3 | `_validate_panel`：api_key 空 + base_url 空 → 仍因 base_url 报错返回 None | 无 | 返回 None；errors 含 base_url 不能为空；**不含** api_key 不能为空 | 否 |
| T-D1E-U09 | D3 | `_validate_panel`：api_key 非空 + base_url/model 合法 → 通过且原值保留 | 无 | `cfg["api_key"]` == 用户原值（不 strip 吃字符，沿用 L129-130 存原值语义） | 否 |
| T-D1E-U10 | D3(回归) | `_panel_is_blank` 行为**不变**：base_url/model/api_key 全空白 → True | 无 | 与 D1 既有断言一致（契约明确不动 _panel_is_blank） | 否 |
| T-D1E-U11 | D5 | slider 参数：min=512/max=16384/step=512/默认=8192 | AppTest 取 slider proto / 或读组件常量 `_MAX_TOKENS_*` | min==512 且 max==16384 且 step==512 且 default==8192（修历史 4096） | 否 |
| T-D1E-U12 | D5 | `_MAX_TOKENS_DEFAULT` 与 `config.DEFAULT_LLM_MAX_TOKENS` 对齐 | 直接 import 两常量比较 | `_MAX_TOKENS_DEFAULT == config.DEFAULT_LLM_MAX_TOKENS == 8192`（动态锚定，未来改 config 立刻失败） | 否 |
| T-D1E-U13 | D5 | slider 边界：set_value(512)/set_value(16384) 端点合法 | AppTest | 端点值落入 overrides/default 且 int 类型 | 否 |
| T-D1E-U14 | D5(待 align Q1) | **prefill 旧值非 512 整除**（如 4096 旧 checkpoint / 8000）注入 slider | AppTest set prefill value=4096 / 8000 | 见 §5 Q1：要么 round 到最近 step（8000→8192），要么 streamlit 静默拒绝回退默认 8192（参 OBS-D1-02）——**断言行为需架构师确认后定** | 否 |
| T-D1E-U15 | D3 | 兜底校验**逻辑单元**：组装 default.api_key="" 且 get_llm_api_key()=="" → 该判定返回触发兜底 | monkeypatch get_llm_api_key→""（或 None）；AppTest 或抽出的判定函数直测 | 触发 st.error("未提供 api_key 且环境变量 LLM_API_KEY 为空...") + 返回 None | 否 |

### L2 集成（含安全不变量实证，默认运行，零 token）

| 用例 ID | 维度 | 测试手段 | mock 边界 | 预期断言 | token |
|---|---|---|---|---|---|
| T-D1E-I01 | **D1 命门** | 真实 `get_checkpointer(tmp_path db)` + 真实 build_graph（patch 4 节点为快速 stub，patch create_llm 内 ChatOpenAI 避免真网络）→ `start_task`（default.api_key="") → worker invoke 跑到 interrupt → 主线程 `get_state(config).values` 读回 checkpoint | patch ChatOpenAI（防真网络）；4 react 节点 patch 为写最小 state 的 stub | `snapshot.values["llm_config_set"]["default"]["api_key"] == ""`；**真实 SqliteSaver 落盘读回恒空** | 否 |
| T-D1E-I02 | **D1 命门** | 同 I01，但 LLMConfigSet 含 4 个 override 各填 base_url/model、api_key 留空 | 同 I01 | checkpoint 中 `default.api_key==""` 且**每条** `overrides[node].api_key==""`（5 条全恒空） | 否 |
| T-D1E-I03 | **D1 命门** | `_refresh_llm_config_set`（app.py L56）单元直测：传入空 api_key 的 set → 返回 set | 无 | 返回的 default/overrides api_key 均为 ""；**不把 get_llm_api_key() 真实值写入**（_refresh 层不回退，§2.7.2 明确否决） | 否 |
| T-D1E-I04 | D2 | `create_llm(resolve_llm_config(set, node_name))` **真实选路 + 回退**（default 路径）：set={default:api_key="", overrides:{}} | monkeypatch get_llm_api_key→"env-key"；patch ChatOpenAI 捕获 | node_name="paper_intake" 选中 default → create_llm 回退 → ChatOpenAI 收 env-key | 否 |
| T-D1E-I05 | D2 | 同 I04，**override 路径**：set={default:..., overrides:{paper_analysis:{api_key="", base_url=X, model=Y}}} | 同 I04 | node_name="paper_analysis" 选中 override（base_url==X）→ 该条空 api_key 在 create_llm 回退同一 env 源（§2.7.2 override 一致规则） | 否 |
| T-D1E-I06 | D2 | override 路径用户**显式填了** api_key（"sk-ov"）→ 不回退 | 同 I04 | node_name 选中 override → ChatOpenAI 收 "sk-ov"（用户显式优先，不被 env 覆盖） | 否 |
| T-D1E-I07 | D2(回归) | react_base L828 消费点不被改签名 | grep/import 断言 wrapper 仍调 `create_llm(resolve_llm_config(...))` | 消费点调用形态不变（回退对消费点透明） | 否 |
| T-D1E-I08 | D4/OBS-D1-01 | 表单校验失败返回 None 时，stale `session_state["llm_config_set"]` 行为**不被本改动加剧** | AppTest：先成功写入 → 再触发兜底校验失败 | 返回 None；session_state 旧值仍在（D1 既有可接受行为，§OBS-D1-01）；本改动新增的兜底分支**不引入新 stale 写入** | 否 |

### L3 真机冒烟（streamlit 真实启动；环境受限明确标注，默认不跑）

| 用例 ID | 维度 | 测试手段 | mock 边界 | 预期断言 | token |
|---|---|---|---|---|---|
| T-D1E-S01 | **D1+D6 命门** | `streamlit run app.py --server.headless true` 子进程启动 → 轮询就绪（HTTP 200）→ (api_key 留空) 发起任务 → poll checkpoint | 真实进程；可 patch create_llm 内 ChatOpenAI 为 stub 避免真烧 token（或归 L4） | 进程启动无 `StreamlitDuplicateElementKey`（D3 教训）；任务能跑进 worker **不再 404**；checkpoint 中 api_key 恒空 | 否（stub）/ 是（真 LLM 见 L4） |
| T-D1E-S02 | D4 | 真机首屏侧栏渲染 | 真实进程 | default panel base_url 预填显示 `https://inference-api.nvidia.com/v1`、model 预填 `azure/openai/gpt-5.4`；4 个 override expander 折叠且**不预填** | 否 |
| T-D1E-S03 | D5 | 真机 slider 可用 | 真实进程 | max_tokens 渲染为 slider（非 number_input），默认 8192，可拖动 | 否 |
| T-D1E-S04 | D6 | `load_dotenv` 生效 | 真实进程 + .env 有 LLM_API_KEY | app 进程 `os.environ["LLM_API_KEY"]` 非空（导入后注入，与 conftest 行为对齐） | 否 |

> **L3 环境受限标注**：本环境为无头服务器，浏览器交互（拖动 slider、点击按钮）无法真人执行。S01-S04 以「子进程启动 + HTTP 探活 + 进程内 os.environ/日志断言」实现可自动化部分；纯交互（slider 拖动手感、点击发起）标注为**人工冒烟项**，不伪造通过。若引入 AppTest 可覆盖 S02/S03 的 widget 存在性（非真 streamlit run），但 D3 教训表明 AppTest 捕不到 DuplicateElementKey，故 S01 必须真 `streamlit run`。

### L4 后端 e2e（真实 LLM，凭证驱动，默认 skip）

| 用例 ID | 维度 | 测试手段 | mock 边界 | 预期断言 | token |
|---|---|---|---|---|---|
| T-D1E-E01 | **D2+D6 命门** | `pytestmark=e2e` + skip_if_no_creds；表单 api_key **留空** → create_llm 回退 .env → 真实 LLM 单轮调用跑通 | 无（真凭证、真网络） | LLM 调用成功返回（不 401/404）；证明留空回退路径端到端可用 | **是** |
| T-D1E-E02 | D2 | 表单填**错误** api_key（"sk-bad"）→ **不回退**（用户显式优先）→ 真实 LLM 返回 401 | 无 | 抛 LLMAuthError/401（证明非空时确实用用户值而非 env，回退不抢） | 是（少量） |

---

## 3. 安全不变量专项（最高优先级，命门）

**不变量声明**：跑 `start_task`（任意 panel 的 api_key 留空）后，SqliteSaver checkpoint 里 `default.api_key` 与各 `overrides[node].api_key` **恒为 ""**；真实 key 只在 `create_llm` 进程内存出现，**永不回写 state / session_state / checkpoint**。

**怎么测（三道实证，覆盖架构师 V1/V2/V5）**：

1. **静态链路审计**（T-D1E-I03）：`_refresh_llm_config_set` 单元直测——即使进程 env 有真实 LLM_API_KEY，`_refresh` 返回的 set 中 api_key 仍恒空（证明 _refresh 层不回退，§2.7.2 明确否决在此层回退）。
2. **真实 checkpoint 读回实证**（T-D1E-I01/I02，**硬门槛**）：真实 `get_checkpointer(tmp_path)` + 真实 build_graph → start_task(api_key 留空) → worker 跑到 interrupt → 主线程 `get_state().values` 读回 → 断言 5 条 api_key 全 ""。复用 `test_app_controller::test_cp_d2_9` 的真实 SqliteSaver 回读骨架（**不能用 FakeGraph，必须真实落盘读回**，否则退回 D3 同款 mock 盲区）。
3. **回退仅进程内存实证**（T-D1E-U05 + I04/I05）：monkeypatch get_llm_api_key→"env-key" 后调 create_llm，断言 ChatOpenAI 收到 env-key 但**入参 LLMConfig dict 的 api_key 仍为 ""**（回退值不写回 state）。
4. **真机闭环实证**（T-D1E-S01，**硬门槛**）：真 streamlit run + api_key 留空 → 任务跑进 worker 不再 404 → poll checkpoint 实证 api_key 空。这是把单元/集成的不变量在真实运行时再钉一遍（弥补 D3 暴露的「单元全绿但集成崩」盲区）。

---

## 4. 覆盖维度确认表

| 维度 | 预期用例数 | 风险识别 | 补缺计划 |
|---|---|---|---|
| L1 单元 | 15 | streamlit set_value 超界静默拒绝（OBS-D1-02）误导 slider 断言；prefill 非 step 整除处理未定 | slider 端点用 AppTest + 常量直读双保险；U14 待 align Q1 |
| L2 集成（含安全不变量） | 8 | 用 FakeGraph 会复现 D3 mock 盲区；MemorySaver 不满足「真实 checkpoint 读取」要求 | I01/I02 强制真实 SqliteSaver(tmp_path) 落盘读回 |
| L3 真机冒烟 | 4 | 无头环境无法真人交互；D3 DuplicateElementKey 类集成 BUG 单元/AppTest 捕不到 | S01 必须真 `streamlit run` 子进程；纯交互项标人工不伪造 |
| L4 后端 e2e | 2 | 凭证缺失；真烧 token；LLM 401/404 区分 | pytestmark=e2e + skip_if_no_creds；E01 留空回退路径 + E02 错误 key 非空不回退 |
| **合计** | **29** | — | — |

**回归校验（不计入 29，但放行前必须全绿）**：`test_llm_client.py` 全量（含 U06 签名）、`test_llm_routing.py` 19 项 resolve_llm_config 纯选路、`test_llm_config_form.py` D1 既有 35 项中**不与本改动冲突**的部分、`test_app_controller.py` D2 全量。**注意**：D1 既有 35 项中存在「全局 5 字段全空→None+st.error」（CP-D1-2）与「api_key 不能为空」相关断言，本次取消 api_key 硬校验后**这些用例会失效，需开发同步改写**——已列入 §5 Q3 提请。

---

## 5. 待 align 问题清单（需 Maria/架构师决策）

- **Q1（slider prefill 非 step 整除，架构师 R4 点名，影响 T-D1E-U14）**：旧 LLMConfigSet prefill 的 max_tokens 若非 512 整除（如 sp1 老值 4096 实际是 512 整除无碍；但用户自定义 8000、或老 checkpoint 残值），slider step=512 时 streamlit 行为是？候选：(a) 组件侧 round 到最近 step（8000→8192）再注入 value=；(b) 不处理，依赖 streamlit 静默拒绝回退默认 8192（参 OBS-D1-02 行为）。**断言写法取决于此**——请架构师确认采 (a) 还是 (b)。
- **Q2（兜底校验是否抽成可单测纯函数）**：兜底校验目前在 `render_llm_config_form` 成功路径末端内联（依赖 st.error）。T-D1E-U15 若要纯单元（不起 AppTest）测「default.api_key 空 且 get_llm_api_key() 空 → 触发」判定，建议开发把判定抽成 `_should_block_for_missing_api_key(cfg_default, env_key) -> bool` 纯函数。**是否接受这一小幅可测性重构**？（不抽则该用例只能走 AppTest，成本略高但可行。）
- **Q3（D1 既有用例失效改写归属）**：取消 api_key 硬校验后，`test_llm_config_form.py` 中依赖「api_key 空→None/报错」的既有用例（如 CP-D1-2「全局 5 字段全空→None+st.error」、CP-D1-5 等）语义反转。这些是**生产契约变更导致的既有用例失效**（非测试 bug），按职责边界由**全栈开发代理**在改组件时同步改写，还是由测试工程师在验收阶段一并改？请明确归属，避免双改冲突。

---

## 6. 验收门槛建议

### 6.1 硬门槛（不满足 = 不放行）

1. **checkpoint key 恒空**：T-D1E-I01 + I02 通过——真实 SqliteSaver 落盘读回，5 条 api_key（1 default + 4 override）全 ""。这是本改动安全命门，**必须真实 checkpoint 读取实证，禁止用 FakeGraph 替代**。
2. **真机留空跑通**：T-D1E-S01 通过——真 `streamlit run` + api_key 留空，任务跑进 worker **不再 404**，且 checkpoint 实证 api_key 空。弥补 D3「单元全绿集成崩」盲区。
3. **回退正确性双路**：T-D1E-I04（default 回退）+ I05（override 回退）+ T-D1E-U02（非空不回退）全绿。
4. **既有基线零退化**：`test_llm_routing.py` 19 项、`test_llm_client.py` 全量（含签名 U06）、`test_app_controller.py` D2 全量仍全绿。

### 6.2 软门槛（凭证就绪时补，缺失则标注留 E 阶段）

5. **L4 e2e**：T-D1E-E01（留空回退真 LLM 跑通）+ E02（错误 key 401）——凭证就绪（.env 有 LLM_API_KEY，本环境已确认存在 LLM_API_KEY/DEEPXIV_TOKEN 两 key 名）时跑；缺失则 skip 并在报告标注「留 E 阶段补」。e2e 层稳定性复跑 ≥3 次。

### 6.3 报告与回归

- 开发完成后，测试工程师按本 plan 补缺用例，跑全量（默认 + e2e）并落 `2026-06-08_d1-enhance-acceptance.md`（或当日实际跑测日期）。
- L-A3-02 历史遗留（pytest.ini markers 注释 `--run-e2e` 与 conftest 凭证驱动实现不符）在本次验收报告中再次记入观察项，建议顺手修注释。

---

## 7. 已知遗漏 & 接受理由

- **真人浏览器交互**（拖 slider 手感、点击发起按钮的真实 DOM 事件）：无头环境 + 无 Playwright，超出范围。以 streamlit run 子进程 HTTP 探活 + AppTest widget 存在性覆盖可自动化部分，纯交互标人工冒烟，不伪造。
- **prompt cache 命中率回归**：本改动不碰 system prompt 前缀（create_llm 回退、表单字段、load_dotenv 均不影响 cache key），无需 §5.3 cache 回归。
- **多 thread_id 并发下的 api_key 隔离**：D2 已覆盖（test_app_controller test_cp_d2_9 并发回读），本改动不改并发模型，不重复。
