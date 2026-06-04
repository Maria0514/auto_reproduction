# 测试执行报告 - d1-acceptance

- **日期**：2026-06-04 00:45（本地时区）
- **执行人**：@测试工程师代理
- **Sprint**：sprint2
- **触发原因**：任务 D1（`ui/components/llm_config_form.py` LLM 配置表单组件）开发代理 2026-06-03 产出的独立验收 + 测试补强（CP-D1-1~10 逐条独立复核 + 边界补强 + 稳定性回归）
- **commit**：5214e1d（改动留工作区未 commit）

## 执行范围

- 命令：
  - `pytest tests/test_llm_config_form.py -q`（D1 单测，含补强）
  - `pytest tests/test_llm_config_form.py -q -p no:randomly`（×3 稳定性连跑）
  - `pytest -q -m "not e2e" --ignore=tests/test_paper_intake.py`（×3 非 e2e 核心回归）
  - 多段自写 streamlit probe 脚本（验证 clamp / expander / proto.type 行为）
- 覆盖用例：`tests/test_llm_config_form.py` CP-D1-1~10 共 16 项（开发）+ 测试工程师补强 19 项 = **35 项**
- 是否包含 e2e：**否**。D1 是纯 UI 组件，无真实 LLM / deepxiv 调用，无需凭证、零 token。

## 验收对象与权威规格

- 被测：`ui/components/llm_config_form.py`（250 行）/ `ui/__init__.py` / `ui/components/__init__.py`
- 规格来源：dev-plan L1172-1246（D1 完整规格）；architecture §2.8.1/§2.8.2/§2.8.3/§2.8.4；`core/state.py` L17-38（LLMConfig / LLMConfigSet / NodeName）

## 结果摘要

- 通过：35（D1 单测）；359（非 e2e 核心回归，含 D1 35）
- 失败：0
- 跳过：0
- 警告：1（langgraph 库级预存 `LangChainPendingDeprecationWarning`，sp1 即有，与 D1 无关）
- 总耗时：D1 单测 0.48s/run；核心回归 ~3.4s/run

**最终验收结论：PASS**

## CP-D1-1~10 逐条独立复核

| CP | 场景 | 复核手段 | 结论 |
|---|---|---|---|
| CP-D1-1 | 可导入 | 顶层 import + callable 断言 | PASS |
| CP-D1-2 | 全局 5 字段全空 → None + st.error | AppTest 真实驱动 | PASS |
| CP-D1-3 | 全局合法 + override 全空 → overrides=={} | AppTest 真实驱动 | PASS |
| CP-D1-4 | 全局合法 + paper_analysis override 合法 → 单 override | AppTest 真实驱动 expander | PASS |
| CP-D1-5 | override 仅填 base_url → 开启覆写但失败 → None | AppTest（补非 base_url 单字段盲区） | PASS |
| CP-D1-6 | 4 节点全 override 合法 → 4 override | AppTest 真实驱动 | PASS |
| CP-D1-7 | temperature=1.5 超界 → None | `_validate_panel` 直测（降级合理，见下） | PASS |
| CP-D1-8 | max_tokens=100 < 256 → None | `_validate_panel` 直测（降级合理） | PASS |
| CP-D1-9 | 成功后 session_state 与返回值一致 | AppTest | PASS |
| CP-D1-10 | api_key password mask | `TextInput.proto.type==1` | PASS |

## 字段契约对齐（core/state.py）

- `LLMConfig` = {base_url, model, api_key, temperature, max_tokens}（5 字段）→ 组件 `_validate_panel` 组装结果字段集**恰好相等，不多不少**，temperature 为 float、max_tokens 为 int 类型正确。
- `LLMConfigSet` = {default: LLMConfig, overrides: Dict[str, LLMConfig]} → 组件返回 `{"default":..., "overrides":...}` **恰好两键**。
- 新增 `test_strengthen_assembled_config_field_contract_matches_state` 动态锚定字段集，未来若 state.py 改字段会立刻失败。

## 开发三处测试手段适配 —— 独立裁定

### 1. expander AppTest 真实驱动（开发声称推翻 dev-plan 风险预判）—— 成立

probe 实证（streamlit 1.58.0）：
- `at.expander` 返回 4 个 Expander，每个 `ex.text_input` 按 key 返回其 3 个真实子控件（`override_<node>_base_url/model/api_key`），证明 override widget **确实是 expander 的子节点**，非平铺。
- 在 expander **collapsed（expanded=False，无 prefill）** 状态下对子 widget `set_value` 后 `at.run()`，结果正确写入 `overrides`（如 planning override 三字段填齐后 `overrides["planning"]` 出现）。
- **裁定：AppTest 确实驱动了 expander 内 widget，非绕过。** 开发推翻 dev-plan "AppTest 对 expander 支持有限" 的预判属实。

### 2. CP-D1-7/8 降级为 `_validate_panel` 直测 —— 降级合理，但开发"clamp"措辞不精确

probe 实证 streamlit 1.58.0 的 widget 行为：
- slider `set_value(1.5)`（max=1.0）→ **既不报错也不 clamp 到 1.0**，而是**静默拒绝、值保留为原默认 0.3**。
- number_input `set_value(100)`（min=256）→ 同样**静默拒绝、值保留为 4096**。

因此：
- (a) 降级理由**成立**——超界值确实无法经真实 UI widget 输入到达校验层（widget min/max 拦截），降级到对校验内核 `_validate_panel` 的直接单测合理。
- 但开发两条"clamp 印证"补充用例（`test_cp_d1_7_temperature_slider_clamped_in_ui` 断言 `<=1.0`、`test_cp_d1_8...` 断言 `>=256`）**能通过，但不是因为"clamp 到 max=1.0 / min=256"**，而是因为"超界 set_value 被丢弃、值回退默认 0.3/4096"，恰好满足 `<=1.0`/`>=256`。**措辞建议修正为"超界 set_value 被静默拒绝回退默认值"**（不影响结论与通过性）。
- (b) 校验内核**确有独立第二道防线**：新增 `test_strengthen_validate_panel_is_independent_defense_not_just_ui_clamp`，一次性传入 temperature=1.5 + max_tokens=100 双越界，断言两条错误均被 `_validate_panel` 报出、返回 None。证明组件不是只靠 UI clamp，校验层独立拒绝。

### 3. CP-D1-10 用 `TextInput.proto.type` —— 真实锚定 mask 语义

- `from streamlit.proto.TextInput_pb2 import TextInput` 确认 `TextInput.Type.DEFAULT==0` / `TextInput.Type.PASSWORD==1`。
- AppTest 高层 `TextInput` wrapper 不暴露 password/text 高层属性，下沉到 `proto.type` 是访问 mask 语义的正确路径。
- 开发断言 5 个 `*_api_key` 全 proto.type==1、base_url/model 全 ==0，**真实锚定 password mask**。裁定成立。

## 补强用例（19 项，16→35）

边界值：温度恰好越上界(1.0000001)/越下界(-0.01)、max_tokens 恰好越上界(16385)/越下界(255) 四向拒绝；端点 0.0/1.0/256/16384 恰好合法且 float/int 类型正确。
override 组合：非 base_url 单字段（model/api_key 各一，parametrize）开启覆写失败（补 CP-D1-5 盲区）；两字段填三缺一失败；一节点失败阻断整表（短路）；两节点 override 隔离不串值。
非空判定：全局纯空格→必填失败；override 纯空格→视为不覆写 overrides=={}；base_url/model 首尾空格→存储值被 strip。
契约：组装字段集对齐 state；session_state 反向断言（失败时权威键缺席，修正后写入）；15 个 text_input key + 5 slider + 5 number_input key 全唯一防冲突；api_key 不落盘（tmp cwd 运行后目录为空）；prefill override expander 默认展开回显（温度/token 透传）。

## 稳定性

- D1 单测 `-p no:randomly` 3 次连跑：35/35 / 35/35 / 35/35（0.48s 稳定）。
- 非 e2e 核心回归 3 次连跑：359 passed / 27 deselected / 0 failed / 0 skipped（~3.4s）。基线 = 开发自报 340 + 测试工程师补强 19 = 359，**sp1+sp2 基线零退化**。
- 无序依赖 / 抖动：未观察到。

## 失败排查

无失败。

唯一中途出现的失败为测试工程师自写用例 `test_strengthen_one_invalid_override_blocks_whole_form` 初版断言 `"llm_config_set" not in session_state`，运行暴露 **OBS-D1-01**（见下）后判定为"测试断言过严、非生产 BUG"，已将该用例改为只断言返回值 None + 行内 st.error 含 planning。属测试代码自身适配，非生产代码 bug。

## 观察项（非阻断）

- **OBS-D1-01（转 D2 注意）**：组件成功时写 `st.session_state["llm_config_set"]`，但后续 re-render 校验失败返回 None 时**不清除该 stale 键**。
  - probe 实证：先填全局合法 → run（写入 llm_config_set）→ 再部分填一个 override → run（返回 None），此时 `session_state["llm_config_set"]` **仍存在**且为上一次成功的旧值（overrides=={}）。
  - 规格判定：dev-plan L1221-1222 / 架构 §2.8.2 仅规定"成功时写入"，**未规定"失败时清除"**；架构 §2.8.4 L1030 明确 `GraphController.start_task(arxiv_id, llm_config_set)` **接收返回值**而非直接读 session_state。故在 D1 契约内**可接受、非生产 BUG**。
  - 措辞偏差：组件源码 L55 注释自称 session_state 是"GraphController 据此读取"的**唯一权威落点**，与架构 §2.8.4"消费返回值"轻微不一致。
  - 处置：提请 D2 实现时**以 `render_llm_config_form` 返回值为准，勿直接读可能 stale 的 `session_state["llm_config_set"]`**；或在 D2 渲染失败分支显式同步 session_state。
- **OBS-D1-02（文档措辞）**：开发 D1 自测 / TODO 中"slider/number_input set_value 被 clamp"措辞不精确，实为"静默拒绝回退默认"，建议复用 developing-with-streamlit skill 时知会 D2~D5 该 AppTest 行为细节。
- L-A3-02（pytest.ini markers 注释 `--run-e2e` 与凭证驱动实现不符）—— sp2 历史遗留，与 D1 无关，沿用既有 TODO 待办。

## 后续动作

- D2（GraphController）实现时落实 OBS-D1-01 的"以返回值为准"约束。
- streamlit 依赖声明（requirements.txt）按 Maria 决定留 D2，本次未改。
- 下一次跑测试触发条件：D2 产出后做 GraphController + 表单接入集成验收。
