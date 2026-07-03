# 测试执行报告 - b1-acceptance

- **日期**：2026-07-03（本地时区）
- **执行人**：@测试工程师代理
- **Sprint**：sprint4
- **触发原因**：Sprint 4 任务 B1（`core/tools/interaction_tools.py`，request_user_input + interrupt#3）独立验收——逐条运行时实证 CP-B1-1~5 + 5 处骨架偏差核对 + 空值卡死去重实证 + 治理范式核查 + 全量回归 3 次连跑
- **commit**：96592c3（被验收改动为工作区未提交状态：新增 `core/tools/interaction_tools.py` + `tests/test_sprint4_b1.py`，主控已核对边界）

## 验收结论

**B1 任务 PASS**（交付文件无缺陷、5 CP 全部独立实证命中、5 处偏差均有据、无 must-fix in B1）。

**但发现 1 个阻断下游的既有代码集成缺口：BUG-S4-B1-01（`core/react_base.py` L596-601 裸 `except Exception` 吞 `GraphInterrupt`，interrupt#3 在 react 子图内绝不暂停）——C2/E2 挂载前必修，B2 harness 首条用例即会撞上。** 详见"失败排查/BUG"节。该缺口在 sp3 既有代码中（sp3 无工具调 interrupt 故从未暴露），不属 B1 交付文件，不影响 B1 PASS 定性。

- 独立探针 2 份共 75 项断言（契约探针 66 + 真实图探针 9），不依赖开发自报、不只复跑开发用例；含 fault-injection（落盘炸后 mask 已生效）与真实 langgraph interrupt（不 mock）闭环。
- 补强 6 条 pytest 用例全绿（GraphInterrupt 穿透锚定 / 真实 interrupt 闭环 / 空值卡死 characterization / 偏差 e 核证 / 日志动态审计 / 纯 str 下游语义）。
- 全量非 e2e 回归 3 次连跑 1224/1224/1224 passed = 基线 1197 + 开发 21 + 补强 6，0 退化 0 flaky。
- 凭证纪律：全程 tmp_path/tempfile，跑完后真实 `workspace/` 零 `.secrets` / `.git_askpass_*` 残留；日志静态+动态双审计无 value / question 明文。

## 执行范围

- 命令：
  - 独立探针 `/tmp/probe_b1_contract.py`（66 项：payload 四键不多不少 / 空串转 None / resume 三形态×6 非法形态 / cache-hit 0 interrupt 硬断言 / 敏感旁路+mask 闭环 / 先登记后落盘 fault-injection / 偏差 e 核证 / 空值卡死 5 项 / DEBUG 全捕日志审计 / docstring AST 静态常量证明 + schema 三参）
  - 独立探针 `/tmp/probe_b1_realgraph.py`（真实 langgraph interrupt，不 mock：普通节点闭环 7 项 PASS + react_base 误吞实证 2 项预期 FAIL=发现 BUG）
  - 跨进程 docstring sha256 双跑（字节级一致：`d5c990efaf0e5c08`，477 chars）
  - `.venv/bin/pytest -q tests/test_sprint4_b1.py`（27 = 开发 21 + 补强 6）
  - 补强用例单条独立运行抽查（2 条逐 `::` 指定）
  - `.venv/bin/pytest -q -m "not e2e and not sandbox_real" --ignore=tests/test_paper_intake.py` × 3（全量回归）
- 是否包含 e2e：否（硬性边界禁 e2e / sandbox_real；interrupt 语义用 InMemorySaver 全离线可真验，无需网络/凭证）。

## 逐 CP 独立实证

| CP | dev-plan 要求 | 独立复核方式 | 结论 |
|---|---|---|---|
| CP-B1-1 | payload 四键契约完整，purpose_key 空串转 None | 探针：`set(payload.keys())` 恰四键不多不少（无额外泄漏键）、kind 字面量三方相等、空串→None、is_sensitive 严格 bool（含 `1`→`True` 规整）；**真实图复证**：payload 经真实 `__interrupt__` 通道穿透主图仍恰四键（探针 2 A.2） | PASS |
| CP-B1-2 | 不记住不落盘 / 记住落盘 / 非法 resume 空串+WARNING | 探针：remember=False 文件不存在；remember=True 落盘 JSON schema 精确 + 0600 + lookup 闭环；remember=True 无 purpose_key 不落盘；非敏感 remember is_sensitive=False 透传；非法 resume 六形态（None/str/int/list/缺 value dict/空 dict）全部空串+WARNING+零落盘副作用；value=None 属合法不误报 WARNING | PASS |
| CP-B1-3 | 命中 `.secrets` → 0 次 interrupt 直接返回缓存值 | 探针：`fake.calls == []` 硬断言；异 key 不误命中仍 interrupt 1 次；空 purpose_key 跳过缓存查；**真实图复证**：新线程同 key 无 `__interrupt__` 直接返回（跨任务复用图内形态，探针 2 A.7） | PASS |
| CP-B1-4 | 敏感值 register 进程内可查、mask 可脱敏；返回纯 str | 探针：不记住也登记 + mask `****` 精确；记住时登记+落盘双生效；非敏感不登记不误 mask；返回非 json.dumps 包裹；**fault-injection**：monkeypatch remember_secret 抛 OSError → 进程内 set 已含值（"先登记再落盘"顺序实证）且异常向上抛非静默 | PASS |
| CP-B1-5 | docstring 字节级稳定，零动态变量 | AST 证明 docstring 为静态字符串常量（f-string/format 会成 JoinedStr/Call 节点）；跨进程 sha256 双跑一致；纪律三条齐备；schema 恰三参 + 缺省值稳定；开发侧另有 477 字符逐字节锚定常量 | PASS |

## 5 处骨架偏差核对（vs architecture §2.1 参考骨架）

| # | 偏差 | 规格依据 | 运行时核证 | 判定 |
|---|---|---|---|---|
| 1 | 非法 resume 降级补 WARNING（骨架无） | dev-plan B1 第 2 点原文"返回空串 + WARNING（Q-F2 无前端降级语义，失败非静默）"+ 全局治理条款点名 CP-B1-2 | 六形态全 WARNING（探针 2.11.*） | **规格要求** |
| 2 | 补 register_sensitive_value（骨架无） | dev-plan B1 第 2 点原文"无论是否记住均 register_sensitive_value(value)"（CP-B1-4） | 探针 4.1/4.4 + fault-injection 4.6 证"先登记再落盘"顺序声称属实 | **规格要求** |
| 3 | 去未用 import mask_value（骨架 import 但从未使用） | 骨架自身 L113 import 后体内零引用，属文档笔误 | 实现 import 恰为实际使用的三符号；无行为差异 | **合理收敛** |
| 4 | docstring 补第三条"单独一轮调用"（骨架只两条） | dev-plan B1 第 3 点"三重约束"+ 架构 §8.3 缓解 2 原文逐字要求此句 | 探针 5.4 三条齐备 + CP-B1-5 字节锚定 | **规格要求** |
| 5 | cache-hit 加 INFO 且不重复 register（骨架裸 return） | INFO：可观测性最小增量，只打 purpose_key（日志审计过）；不重复 register：开发声称"由 .secrets 条目直接提供 mask 覆盖" | **声称核证成立**：`.secrets` 置 is_sensitive=True 条目 + 进程内 set 清零（模拟重启）→ cache-hit 返回后 set 仍空、`mask_value` 经 `.secrets` 条目直接脱敏该值（探针 D-e.1~3 + 常驻用例） | **合理收敛（声称属实）** |

偏差 5 附带边界（记录，不阻断）：`.secrets` 条目 is_sensitive=False 而本次调用 is_sensitive=True 的**敏感度错配 cache-hit**——返回值不被 mask 也不 register（探针 D-e.4 OBS）。触发前提是 LLM 对同一 purpose_key 的敏感度分类前后不一致，purpose_key 语义（如 `hf_token`）天然稳定，B1 层接受；E2/E3 验收 mask 覆盖面时留意。

## 空值卡死去重实证 + 判定

**实证**（探针 EV.1~5 + characterization 常驻用例锚定）：
- `resume={"value": "", "remember": True}` + purpose_key → 落盘 `{"gh_tok": {"value": "", "is_sensitive": true}}`；
- 后续同 key 调用 → cache-hit 返回空串、**0 次 interrupt**——用户永久失去补值机会（须手删 `.secrets` 条目恢复）；
- `value=None + remember=True` 经 `or ""` 规整同样落空串，同一卡死面；
- 联动守卫无恶化：空串不进 sensitive set（register 空值守卫）、`.secrets` 空值条目不摧毁 mask（A3 真值守卫，探针 EV.3/4）。

**判定：B1 层可接受，不必 B1 修，不阻断验收。** 理由：实现与架构 §2.1 骨架逐字一致（`cached is not None` + `remember and purpose_key`），B1 单方加真值守卫属越权改契约。防线约定 = **F1 UI 提交前非空校验（升为 F1 验收必查项，挂账 L-B1-01）**。同时建议（非阻断）架构师评估在落盘处加 `and value` 真值守卫作纵深防御（一行改动、符合极简）：UI 校验护不住未来 headless / 程序化 resume 路径。characterization 用例已锚定现状，未来任何修复会使其翻红提醒同步。

## 治理范式核查

- **失败非静默**：非法 resume 六形态 WARNING（caplog + 探针双证）；落盘异常向上抛不被工具吞（fault-injection 4.7）。
- **日志无明文**：静态审计——模块仅 2 条 logger 语句（L83 INFO 只打 purpose_key、L102 WARNING 只打 resume 类型名+purpose_key）；动态审计——interrupt / cache-hit / 非法 resume 三路径 DEBUG 全捕（含 secrets_store 联动日志），value 与 question 唯一标记零命中（探针 LOG.1/2 + 常驻用例）。
- **纯 str 例外的下游影响**：`extract_last_tool_result` 喂纯 str ToolMessage → 返回 None 不抛（含 json 可解析但非 dict 的纯数字输入），符合 sp3 B2 先例的"纯文本 None 语义"；下游"从工具历史回填 dict"兜底对本工具天然不适用，常驻用例锚定。
- **interrupt 不误吞**：B1 工具自身不吞（GraphInterrupt 穿透 @tool 包装，常驻用例锚定）；**react_base 子图层会吞——BUG-S4-B1-01**，见下节。

## 失败排查 / BUG

### BUG-S4-B1-01：react_base 工具执行节点吞掉 GraphInterrupt，interrupt#3 在 ReAct 子图内失效

- **组件**：`core/react_base.py::tool_executor_node` L596-601（sp3 既有代码，**非 B1 交付文件**）
- **失败类型**：生产代码集成缺口（sp4 新暴露：sp3 无任何工具调 interrupt）
- **复现路径**：`/tmp/probe_b1_realgraph.py` Part B——`create_react_subgraph` 挂 `request_user_input` + 脚本化 LLM 触发工具调用
- **期望行为**：架构 §2.1"工具体内 interrupt(payload) 暂停主图"、§8.3 层 1 依赖 LangGraph interrupt 上浮语义
- **实际行为**：`GraphInterrupt` MRO = `GraphInterrupt → GraphBubbleUp → Exception`，被 L599 裸 `except Exception` 捕获，转为 error ToolMessage `"tool request_user_input raised GraphInterrupt: (Interrupt(value={...}),)"`，子图继续跑完 `status='done'`，**主图绝不暂停**。带 InMemorySaver 父图与 standalone 两种形态均实证吞没。
- **次生问题**：吞没转写的 f-string 把 Interrupt payload 全文（含 **question 文本**）写进 ToolMessage 与 L601 WARNING 日志——question 可能内嵌上下文片段，违反日志纪律；根修同时消除。
- **影响范围**：不影响 B1 交付本身（工具在普通节点/编排层直调形态闭环全通过，探针 2 Part A 7/7）；**阻断 B2/C2/E2/F1 的 react 子图路径**——B2 harness 首条用例即撞。
- **建议修复方向**：L596 try 块内在裸 `except Exception` 之前加 `except GraphBubbleUp: raise`（`from langgraph.errors import GraphBubbleUp`），让 interrupt 上浮交 LangGraph 处理；修复后 B2 文件应有常驻断言。
- **处置**：已转交全栈开发代理（挂 B2 软前置门前必修）；本文件不放常红用例，B1 侧以 `test_hardening_graphinterrupt_propagates_through_tool_wrapper` 锚定工具层不吞。

### 探针附带发现（非 BUG，B2 施工注意）

带 checkpointer 的父图中，节点内 `subgraph.invoke(initial)` 经隐式 config 传播会将**子图状态（含 `context._llm`）纳入 checkpoint 序列化**——不可 msgpack 序列化的 FakeLLM 直接 `TypeError: Type is not msgpack serializable: FakeLLM`（生产 ChatOpenAI 为 LC Serializable 不受影响）。B2 harness 的 FakeLLM 需可序列化或规避。

## 给 B2 的消费清单（开发自报 5 条偏差已全部核实 + 验收新增）

1. **（最高优先）** react_base L596-601 `GraphBubbleUp` 放行必须先修（BUG-S4-B1-01），否则 interrupt#3 harness 全线不通。
2. FakeLLM 注入 `context._llm` 后若父图带 checkpointer，需可 msgpack 序列化（上节实证）。
3. 真实 resume 语义已证：resume 后节点从头重跑、`interrupt()` 处直接返回 resume 值（探针 A.4）；同 key cache-hit 对重放再问天然免疫（A.7）——B2 断言"副作用恰 1"时注意 cache-hit 贡献。
4. payload 四键 + purpose_key None 契约已在真实 `__interrupt__` 通道核证（A.2），B2 可直接引用 `test_integration_real_interrupt_closed_loop_plain_node` 锚定。
5. 两个已锚定边界勿误踩：空值卡死去重（用非空值构造场景）、敏感度错配 cache-hit 不 mask（同 purpose_key 保持稳定 is_sensitive 分类）。

## 结果摘要

- `tests/test_sprint4_b1.py`：**27 passed**（开发 21 + 补强 6），0.48s；单条独立运行抽查 2 条通过
- 独立探针：契约 66 项全 PASS；真实图 9 项 = 7 PASS + 2 预期 FAIL（=BUG-S4-B1-01 实锤）
- 全量非 e2e 回归：**1224 passed / 0 failed / 37 skipped / 43 deselected**（e2e+sandbox_real）
- 警告：1 —— langgraph `LangChainPendingDeprecationWarning`（库级、既有、与本批无关）

## 连跑稳定性

- 3 次连跑：1224 / 1224 / 1224 passed（56.14s / 56.31s / 56.13s），skipped 恒 37、deselected 恒 43，**0 退化 0 flaky**
- 数字对账：基线 1197（2026-07-02 A3/D2 验收，commit 96592c3）+ 开发 21 + 补强 6 = 1224，精确吻合

## 凭证纪律核查

- 全套（探针 + 单套 + 3 次全量）跑完后真实 `workspace/` 零 `.secrets` / `.git_askpass_*` 残留；探针 tempfile、用例 tmp_path + monkeypatch config.WORKSPACE_DIR
- `git status` 与验收前一致（仅预期 2 新文件），无意外产物

## 后续动作 / 遗留项

- **BUG-S4-B1-01（阻断 B2/C2/E2）**：react_base GraphBubbleUp 放行，@全栈开发代理，B2 挂载前必修；修复后回归本文件 + B2 常驻断言。
- **L-B1-01（F1 验收必查）**：UI 提交前非空校验（空值卡死去重防线约定）；建议架构师评估落盘真值守卫纵深防御（非阻断）。
- **L-B1-02（E2/E3 留意）**：敏感度错配 cache-hit 不 mask 的覆盖缺口（探针 D-e.4，触发面窄）。
- 沿用既有：L-A3-01（workspace 基准一致性，本次 B1 消费形态已核——lookup/remember/mask 均回退 config.WORKSPACE_DIR，基准一致）；`tests/test_paper_intake.py` 按惯例 --ignore；langgraph 库级 PendingDeprecationWarning。
- 下一次触发条件：BUG-S4-B1-01 修复后回归 `tests/test_sprint4_b1.py` + 全量；B2 harness 交付后验收 interrupt#3 重跑幂等结论。
