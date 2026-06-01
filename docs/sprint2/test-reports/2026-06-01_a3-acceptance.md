# 测试执行报告 - a3-acceptance

- **日期**：2026-06-01（本地时区）
- **执行人**：@测试工程师代理
- **Sprint**：sprint2
- **触发原因**：全栈开发代理交付阶段 A 任务 A3（`core/react_base.py` 单行 diff 接入节点级 LLM 路由 + **彻底删除 `core/state.py` 过渡期镜像字段 `llm_config`**）并完成自测后，Maria 触发"独立第三方验收"任务：逐条复核 CP-A3-1~5、核对 A3-watchlist 4 处守门、盘清镜像字段删除影响面、补齐边界测试、稳定性复跑 + 全量回归（含 e2e）确认 sp1 基线零退化、出具 PASS/FAIL 结论。本验收同时落地 A1 验收报告 §7 遗留的 P0 watchlist（C2 守门核对）。
- **commit**：`4f2aec7`（master，A3 交付 commit）

---

## 1. 验收范围

### 1.1 A3 交付物清单（独立复核）

| # | 路径 | 改动类型 | 复核状态 |
|---|------|---------|---------|
| 1 | `core/react_base.py` | 顶部 import 增 `resolve_llm_config`（L39）；L828 `create_llm(state["llm_config"])` → `create_llm(resolve_llm_config(state["llm_config_set"], node_name))`；`_make_react_wrapper` 签名零变化 | OK |
| 2 | `core/state.py` | **彻底删除** GlobalState.llm_config 字段定义 + create_initial_state 不再写 llm_config，仅留 llm_config_set 权威源；形参名 `create_initial_state(llm_config=...)` 保留（兼容老/新两形态） | OK |
| 3 | `tests/test_react_base_sp2.py` | 新增 6 用例（CP-A3-1 签名 / CP-A3-2 P1×2 / CP-A3-3 P2 / P3 多节点 / 无镜像回归） | OK（补强见 §4） |
| 4 | watchlist 4 处 + 连带清理 7 处 | 详见 §3 | OK |

### 1.2 验收任务清单

1. 逐条复核 CP-A3-1~5 是否真实命中（独立判定，不信开发代理自报）。
2. 复核 A3-watchlist 4 处守门是否到位。
3. 重点审查"删除 llm_config 镜像字段"的影响面是否盘干净（全仓 grep）。
4. 评估开发代理新增 sp2 测试充分性，补齐边界用例。
5. 稳定性复跑：核心单测集多次连跑 + 全量 `pytest -q`（含 e2e）确认 sp1 基线零退化。

### 1.3 是否包含 e2e

**是**。全量回归默认即包含 e2e（`tests/conftest.py` 启动时 `load_dotenv` 加载 `.env`，凭证齐备即真跑；与 sp1 / A1 / A2 验收基线一致）。

| 凭证 env 变量名 | 来源 | 状态 |
|---|---|---|
| `DEEPXIV_TOKEN` | `.env`（项目根，644B，5/14 创建） | 已就位（全量回归 0 skipped 证明真跑） |
| `LLM_API_KEY` | `.env`（项目根） | 已就位 |

---

## 2. CP-A3-1~5 逐条独立判定

| CP | 内容 | 独立判定 | 证据 |
|---|---|---|---|
| **CP-A3-1** | `_make_react_wrapper` 签名零变化（inspect 断言形参列表完全一致） | **PASS** | `tests/test_react_base_sp2.py::test_cp_a3_1_*` 断言形参列表 == `[node_name, build_context, build_system_prompt, get_tools, map_result, max_rounds, result_schema]`，且 node_name 无默认值、result_schema 默认 None。独立跑通 |
| **CP-A3-2** | P1 全局回退：无 override 时 `create_llm` 收到 `llm_config_set["default"]` | **PASS** | `test_cp_a3_2_p1_global_fallback_no_override`（overrides 空）+ `test_cp_a3_2_p1_fallback_node_not_in_overrides`（其它节点有 override，本节点回退 default）。mock `create_llm` 捕获实参 == CFG_DEFAULT；关键：**不 mock resolve_llm_config**，真实走选路 |
| **CP-A3-3** | P2 单节点 override：`create_llm(cfg_B)` 被调用 | **PASS** | `test_cp_a3_3_p2_single_node_override` 断言 captured[0] == CFG_INTAKE 且 != CFG_DEFAULT |
| **CP-A3-4** | `pytest tests/test_react_base.py -q` 零退化（sp1 B4 8 项自测） | **PASS** | 独立跑 `pytest tests/test_react_base.py -q` → **4 passed**（该文件聚合为 4 个 pytest 函数，覆盖 sp1 B4 8 检查点；mock state 已升级为 llm_config_set 结构，wrapper 改读后无 KeyError） |
| **CP-A3-5** | paper_intake / paper_analysis 全量单测通过（CP1~CP11） | **PASS（带结构债说明）** | paper_analysis：`pytest tests/test_paper_analysis.py` → 1 个聚合 pytest 用例 `test_paper_analysis_all_checkpoints` 内含 CP1~CP11，PASS。paper_intake：**该文件是 main() 风格自测脚本（`case_*` + `if __name__`），pytest 收集为 0**，用 `python tests/test_paper_intake.py` main 入口跑 → **8/8 passed**（CP1~CP8）。见 §6 遗留项 L-A3-01 |

**CP-A3-1~5 全部命中（5/5 PASS）。** 唯一附带说明：CP-A3-5 中 `test_paper_intake.py` 是 sp1 遗留的 main 风格脚本，不被 `pytest -q` 默认收集，需 main 入口单独跑（已验证 8/8）；此为 sp1 测试基建债，非 A3 引入，登记为非阻断遗留项。

---

## 3. A3-watchlist 4 处守门 + 连带清理核对

### 3.1 watchlist 4 处（A1 验收报告 §7 P0 遗留 C2 守门）

| # | 位点 | 期望 | 复核结果 |
|---|------|------|---------|
| 1 | `core/react_base.py` 原 L825 | 改读 `resolve_llm_config(state["llm_config_set"], node_name)` | **OK**（实际 L828，含 L825-827 注释引用架构 §4.9） |
| 2 | `tests/test_sprint1_smoke.py` L229 | 升级为 `"llm_config" not in state` + `llm_config_set["default"] == llm_config` 断言 | **OK** |
| 3 | `tests/test_graph_e2e.py` L254 | 升级为断言 `llm_config_set` / `.default` 仍存在（流水线后未被清空） | **OK** |
| 4 | `tests/test_sprint2_a1.py::test_cp_a1_aux_6_*` | 四形态全升级为 `"llm_config" not in state` 反向断言；CP-A1-6/7 镜像断言反转；CP-A1-5 docstring 镜像段落重写为"A3 后已移除" | **OK**（CP-A1-6 L161 / CP-A1-7 L199 / Aux-6 L466-490 四形态 + 文件头 docstring L9-13 全部反向断言到位） |

### 3.2 连带清理（镜像字段删除的完整影响面，超出原 watchlist 4 处）

| # | 位点 | 改动 | 复核结果 |
|---|------|------|---------|
| 5 | `tests/test_react_base.py` L37 | mock state `llm_config` → `llm_config_set` 结构 | **OK** |
| 6 | `tests/test_paper_intake.py` L51 | mock state 升级 | **OK** |
| 7 | `tests/test_paper_analysis.py` L57 + L979 | mock state ×2 升级 | **OK** |
| 8 | `scripts/run_paper.py` L162 | dump 剔除目标改为 `llm_config_set`（含 api_key）；保留 `llm_config` pop 兼容老 checkpoint | **OK** |

**A1 验收报告 §4.3 三条 condition 收口**：
- **C1**（A1~A3 间禁新增"运行期修改 llm_config_set/llm_config"代码）：grep 确认全仓无任何节点 return dict 含 `llm_config` / `llm_config_set` 键，C1 履约。
- **C2**（A3 完成同时移除字段 + 改 3 处直读 + Aux-6 反向断言 + CP-A1-5 docstring 删镜像）：本次 §3.1 逐条核对全部到位，**C2 收口**。
- **C3**（watchlist）：本次验收即 C3 收口动作，无新增运行期修改配置代码。

---

## 4. 镜像字段删除影响面盘点（全仓 grep）

### 4.1 `state["llm_config"]` 直读残留

```
grep -rn 'state\["llm_config"\]' --include="*.py" .  | grep -v llm_config_set
→ core/state.py:167  （docstring 文字："不再存在任何 state["llm_config"] 直读路径"）
```

**全仓真实代码 0 处直读 `state["llm_config"]`**。唯一命中是 state.py docstring 的说明文字（陈述事实，非代码引用）。**干净。**

### 4.2 GlobalState 字段定义

- `core/state.py` L160-196 GlobalState 定义中**无** `llm_config` 字段，仅 `llm_config_set: LLMConfigSet`（L169）。
- `create_initial_state`（L208-）只 `GlobalState(llm_config_set=config_set, ...)`，**不写** `llm_config`。

### 4.3 其余 `llm_config` 出现点分类（确认均非字段残留）

| 类别 | 文件 | 性质 | KeyError 风险 |
|------|------|------|--------------|
| 形参名 `create_initial_state(llm_config=...)` | state.py / 全部 e2e / spike / run_paper | 入参名（兼容老 LLMConfig + 新 LLMConfigSet 两形态），保留不变 | 无（不读 state 字段） |
| 局部变量 `llm_config = LLMConfig(...)` | run_paper.py:131 / spike:205 | 函数内局部变量 | 无 |
| `LLMConfig` 类型注解 / import | 多处 | 类型，非字段 | 无 |
| `llm_config_set` 字段 | core / tests | 新权威字段 | 无 |
| docstring / 注释 | state.py / 测试文件头 | 说明文字 | 无 |

**关键结论：未在自报清单中的 3 个文件（`test_paper_intake_e2e.py` / `test_paper_analysis_e2e.py` / `spike_prompt_cache_baseline.py`）均通过 `create_initial_state(llm_config=...)` 形参入口构造 state，由 create_initial_state 内部包装成 `llm_config_set` 写入——天然不受镜像字段删除影响（用形参名而非读 state 字段），故开发代理未将它们列入改动清单是正确的，无遗漏。** 全量回归 0 skipped 中这些 e2e 全部真跑通过，实证无运行期 KeyError。

**影响面盘点结论：干净。** 镜像字段删除无任何遗漏的运行期/e2e KeyError 触发点。

---

## 5. 测试矩阵评估与补强

### 5.1 开发代理 sp2 用例评估（`tests/test_react_base_sp2.py` 6 用例）

| 用例 | 场景 | 评估 |
|------|------|------|
| `test_cp_a3_1_*` | 签名 inspect 断言 | 充分（形参列表 + node_name 无默认值 + result_schema 默认 None 三重断言） |
| `test_cp_a3_2_p1_global_fallback_no_override` | overrides 空 → default | 充分 |
| `test_cp_a3_2_p1_fallback_node_not_in_overrides` | 本节点未命中、其它节点有 override → default | 充分（隔离视角） |
| `test_cp_a3_3_p2_single_node_override` | 命中 override | 充分 |
| `test_cp_a3_p3_multi_node_override_each_picks_own` | 多节点各取其所 | 充分（P3 三路径覆盖要求 dev-plan L1529） |
| `test_a3_wrapper_does_not_read_legacy_llm_config` | 无镜像字段回归 | 充分 |

**评估**：mock 边界正确（**不 mock resolve_llm_config**，真实走选路逻辑，避免假阳性）；P1/P2/P3 三路径覆盖到位；命名规范；fixture 无副作用、可独立运行。

### 5.2 测试工程师补强（4 用例，落到同一文件 `tests/test_react_base_sp2.py`，不新建）

| 用例 ID | 补强理由 | 关键断言 |
|---|---|---|
| `test_aux_a3_malformed_config_set_propagates_permanent_error` | **A3 集成视角原本缺失的关键契约**：A2 仅在单元层测了 `resolve_llm_config` 抛 PermanentError，未验证经 wrapper 调用时该错误真实冒泡（而非被 try/except 吞掉降级成 `create_llm(None)`）。缺 default 的形态错误必须在 resolve 阶段冒泡 | `pytest.raises(PermanentError)` + create_llm 未被触达（captured == []） |
| `test_aux_a3_create_llm_receives_override_object_identity` | 保护"返回引用而非拷贝"契约（A2 单元层有 is 断言，补 wrapper 链路视角）：经 wrapper→resolve→create_llm 后收到的是 override 对象本身，default 不泄漏 | `captured[0] is override_obj` |
| `test_aux_a3_same_wrapper_repeated_calls_independent` | 多节点 override 隔离 + 无跨调用状态累积：同一 wrapper 闭包连续两次调用（先 default 后 override）各取对的配置，不互相污染 | captured[0]==DEFAULT / captured[1]==INTAKE |
| `test_aux_a3_overrides_none_falls_back_to_default` | 空 overrides 健壮性：overrides 键缺失 / 显式 None 两形态经 wrapper 安全回退 default，不抛 KeyError/TypeError（UI/GraphController 装配时可能只给 default） | 两形态 captured 均 == DEFAULT |

**补强后矩阵**：开发代理 6 + 测试工程师 4 = **10 用例**，0.43s 跑完。

### 5.3 已知遗漏（无法在 A3 阶段覆盖）

| 遗漏点 | 原因 | 后续动作 |
|--------|------|---------|
| 真实多模型链路 e2e（override 真发不同 model 的请求） | A2 已有 `test_sprint2_a2_e2e.py` 在 create_llm 装配层验证 model_name 选路正确（不发请求省 token）；wrapper 集成层无需重复真发 LLM | 维持 A2 e2e 覆盖即可 |
| planning 节点 node_name="planning" 路由 | planning 节点尚未落地（C 阶段） | C 阶段 planning 落地时随节点单测覆盖 |
| `test_paper_intake.py` 标准化为 pytest 用例 | sp1 遗留 main 风格脚本，A3 范围外 | 见 §6 L-A3-01 |

---

## 6. 稳定性复跑结果

### 6.1 核心非 e2e 单测集 3 次连跑

执行 `pytest tests/test_react_base_sp2.py tests/test_react_base.py tests/test_paper_analysis.py tests/test_sprint1_smoke.py tests/test_sprint2_a1.py tests/test_sprint2_a2.py tests/test_llm_routing.py tests/test_graph.py tests/test_llm_client.py -q`（101 用例，含本次补强 4 用例；不含 paper_intake main 脚本，该脚本另行 main 入口验证）：

| 连跑 | 通过 | 失败 | 跳过 | 警告 | 耗时 |
|------|------|------|------|------|------|
| 1 | **101** | 0 | 0 | 1 | 0.90s |
| 2 | **101** | 0 | 0 | 1 | 0.90s |
| 3 | **101** | 0 | 0 | 1 | 0.91s |
| **累计** | **303 / 303** | **0** | **0** | 3（同一条 langgraph deprecation） | ~2.7s |

**结论**：3 次连跑 0 失败、0 抖动。

### 6.2 全量 `pytest -q`（含 e2e）2 次复跑

| 全量回归 | 通过 | 失败 | 跳过 | 警告 | 耗时 |
|------|------|------|------|------|------|
| 1 | **118** | 0 | 0 | 1 | 255.51s（4:15） |
| 2 | **118** | 0 | 0 | 1 | 341.24s（5:41） |
| **累计** | **236 / 236** | **0** | **0** | 2 | — |

- **0 skipped** 证明 17 个 e2e 全部真跑（凭证就位）。2 次累计 **34 次 e2e 真实 LLM + deepxiv 链路全绿，0 LLM 服从度抖动**。
- 耗时差异（255s vs 341s）来源 = NVIDIA inference-api 网关响应抖动，与代码无关（sp1 / A1 验收均记录过同款抖动）。
- 收集数：全量 118（sp1 56 + A1 +16 + A2 +12 + A3 净 +4 补强 ... 实际 `--collect-only` = 118，e2e 17 / mock 101）。

### 6.3 paper_intake 自测脚本（main 入口，CP-A3-5 paper_intake 部分）

`python tests/test_paper_intake.py` → **8/8 passed**（CP1~CP8，wrapper 改读 llm_config_set 后 mock 路径全通）。

---

## 7. Warning 现状

唯一 warning（与 sp1 / A1 / A2 验收基线**完全一致**，第三方 langgraph 1.1.10）：

```
.venv/.../langgraph/checkpoint/serde/encrypted.py:5
  LangChainPendingDeprecationWarning: The default value of `allowed_objects` will change in a future version.
```

- 非项目代码，A3 **未引入任何新 warning**，项目零 warning 退化。
- 已在 A1 验收 §7 登记为遗留 P2（sp2 引入 langgraph 加密序列化时随手处理），本次不重复登记。

---

## 8. 失败排查

**无失败。** 2 次全量回归 236/236 + 3 次核心连跑 303/303 + paper_intake 8/8 + sp2 补强 10/10 全部通过，0 失败、0 跳过、0 LLM 抖动。

---

## 9. BUG / 遗留项

### 9.1 BUG

**无。** A3 实现与架构 §4.9 落地约束 3 完全一致；镜像字段删除影响面盘清，无运行期 KeyError 触发点。

### 9.2 遗留项（非阻断）

| ID | 描述 | 性质 | 责任 / 触发条件 |
|----|------|------|----------------|
| **L-A3-01** | `tests/test_paper_intake.py` 是 sp1 遗留的 `main()` 风格自测脚本（`case_*` + `if __name__`），`pytest -q` 收集为 0，不在 118 全量回归内，需 main 入口单独跑。等同覆盖未以 pytest 标准用例落到 tests/ | 测试基建债（**非 A3 引入**，sp1 遗留） | @测试工程师代理；建议在 sp2 C 阶段前或独立小任务中标准化为 pytest 函数（避免 paper_intake mock 覆盖永久游离于回归网外）。**不阻断 A3 验收** |
| L-A3-02 | `pytest.ini` markers 注释写"加 --run-e2e 启用"，但实际 e2e 是凭证驱动（conftest load_dotenv + 各文件 skipif），无 `--run-e2e` 选项、无 addopts 默认排除 e2e。注释与实现不符 | 文档/注释偏差（沿用 sp1 既定惯例） | @全栈开发代理；建议把 pytest.ini 注释改为"凭证缺失则 skip"。**不阻断**（A1 验收 §1.3 已接受该惯例） |
| L-A3-03 | 第三方 langgraph `allowed_objects` deprecation warning | 第三方 deprecation | 沿用 A1 §7 P2 登记，不重复 |

---

## 10. 验收结论

### **通过（PASS）**

**理由**：

1. **CP-A3-1~5 全部命中（5/5 PASS）**：签名 inspect 零变化 / P1 全局回退 / P2 单节点 override / test_react_base.py 零退化（4 passed）/ paper_analysis CP1~CP11 通过 + paper_intake main 入口 8/8。均独立复核，不依赖开发代理自报。
2. **watchlist 4 处 + 连带清理 8 处全部到位**：react_base:828 改读 / 3 个测试文件断言反转 / 4 处 mock state 升级 / run_paper dump 剔除，逐条核对 OK。A1 验收 §4.3 三条 condition（C1/C2/C3）全部收口。
3. **镜像字段删除影响面干净**：全仓 grep 确认 0 处真实 `state["llm_config"]` 直读，GlobalState 无 llm_config 字段，create_initial_state 不写 llm_config，形参名保留。未在自报清单的 3 个 e2e/spike 文件经形参入口构造 state，天然不受影响（全量回归 0 skipped 实证无 KeyError）。
4. **测试补强**：评估开发代理 6 用例 mock 边界正确（不 mock resolve_llm_config）、P1/P2/P3 覆盖到位；补 4 条 A3 集成视角边界（PermanentError 经 wrapper 冒泡 / 引用语义 / 多次调用隔离 / 空 overrides 健壮性），落到同一文件，共 10 用例。
5. **稳定性零退化**：核心单测 3 次连跑 303/303；全量 `pytest -q`（含 e2e）2 次回归 236/236，34 次真实 LLM 链路全绿，0 失败 / 0 跳过 / 0 LLM 服从度抖动；sp1 基线零退化。
6. **零 BUG，零阻断**：2 个非阻断遗留项（L-A3-01 paper_intake main 风格脚本测试债 / L-A3-02 pytest.ini 注释偏差）均为 sp1 遗留，非 A3 引入，已登记追踪。

---

**报告结束。**
