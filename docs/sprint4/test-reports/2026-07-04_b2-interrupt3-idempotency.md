# 测试执行报告 - B2 interrupt#3 重跑幂等 harness 首验（Q-C1，软前置门）

- **日期**：2026-07-04
- **执行人**：@测试工程师代理
- **Sprint**：sprint4
- **触发原因**：dev-plan §4 任务 B2 首验——R-S4-02 本 Sprint 头号验证项，结论决定 C2/E2 是否可挂载 `request_user_input`
- **commit**：ae09733（工作区新增本测试文件与本报告，未 commit）

## 执行范围

- 命令：
  - `pytest tests/test_sprint4_b2_interrupt3_idempotency.py -v`（新增 9 用例，连跑 3 次）
  - `pytest tests/ -m "not e2e" -q`（全量非 e2e 回归）
- 覆盖用例：`tests/test_sprint4_b2_interrupt3_idempotency.py` 全部 9 项（CP-B2-1~4 + R-S4-10）
- 是否包含 e2e：否。全离线（InMemorySaver + 脚本 LLM），零 API 配额消耗。

## 结果摘要

- B2 新增用例：**9 passed / 0 failed**，单文件连跑 3 次结论一致（0.7s/次）
- 全量非 e2e 回归：**1238 passed / 37 skipped / 0 failed**（基线 1229 + 新增 9，零退化），56.1s
- 警告：1（langgraph 库级预存 `LangChainPendingDeprecationWarning`，与本任务无关；另有 langgraph 对测试模块内 FakeLLM 类型反序列化的 stderr 提示 "Deserializing unregistered type ... will be blocked in a future version"，见"遗留项"）

## 检查点判定

| 检查点 | 判定 | 依据 |
|---|---|---|
| CP-B2-1 harness 跑通（interrupt#3 → resume → 值回 agent → 收尾） | **PASS** | `test_cp_b2_1_*`：`__interrupt__` 四键 payload 可见，resume 后 result == {"user_value": "USERVAL-b2"} |
| CP-B2-2 **门禁**：前序独立轮次副作用恰为 1 | **PASS（门禁通过）** | `test_cp_b2_2_gate_*`：resume 后进程内计数 == 1、磁盘 append 行数 == 1 |
| CP-B2-3 断言点 2/3 落档 + 连跑 3 次一致 | **PASS** | 混调重放/messages 完整性各自锚定；核心与混调场景各在用例内连跑 3 次 + 整文件复跑 3 次结论一致 |
| CP-B2-4 报告归档（含 collector 影响评估供 E1/E2） | **PASS** | 本文件 |

## 第一手实证结论（架构 §8.2 留白机理事实）

harness 形态：父图（InMemorySaver）单节点 imperative 调真实 `create_react_subgraph`
（与生产 `_make_react_wrapper` 同拓扑），脚本 LLM 为可 msgpack 序列化的
BaseChatModel（沿用 B1-fix `ScriptedToolCallLLM` 范式，路由纯基于输入 messages，
replay 安全）；副作用工具双通道观测（进程内计数 + 磁盘 append）；LLM 调用数经
模块级 dict 观测（跨 checkpoint round-trip 存活）。环境：langgraph 1.1.10。

### 断言点 1：独立轮次重放范围（门禁核心）

| 观测量 | pause 时 | resume 后 | 结论 |
|---|---|---|---|
| 副作用计数（进程内） | 1 | **1** | 前序独立轮次工具**不重放** |
| 磁盘写入行数 | 1 | **1** | 同上（双通道一致） |
| LLM `_generate` 调用数 | 2 | **3** | 已完成的 reasoning 节点**不重放**，只补收尾一跳 |
| 父节点函数体执行次数 | 1 | 2 | 父节点函数体**重跑**，但子图 invoke 内部从 checkpoint 恢复 |

**机理判定**：`subgraph.invoke(initial)` 在父图节点体内调用时，经 langgraph 隐式
config 传播纳入父 checkpointer 的子命名空间；resume 时父节点函数体从头重跑，
但子图从**自身 checkpoint** 精确恢复到 interrupt 所在的 tool_executor 节点——
已完成的 reasoning / tool_executor 子图节点均不重放，`interrupt()` 在原位返回
resume 值。**架构 §8.3 缓解 1（LangGraph 节点级 resume 定位）成立，门禁通过，
C2/E2 可以挂载 request_user_input。**

### 断言点 2：同轮混调（同批 tool_calls = [side_probe, request_user_input]）

- pause 时副作用 = 1（side_probe 先执行，随后 interrupt 中止整个 tool_executor 节点，**该批次零 ToolMessage 落 checkpoint**）；
- resume 后副作用 = **2**——**同一批 tool_calls 整体重放**，被采纳的是第二次执行结果（ToolMessage 中 call_no=2）；
- 末态安全性：最终 messages 中副作用 ToolMessage 恰 1 条 + resume 值 1 条，无重复消息、无脏半批；对覆盖写幂等工具末态与单次执行等价。

**判定**：架构 §8.3 缓解 2（docstring "单独一轮调用" 纪律）+ 缓解 3（工具自身
幂等）的必要性**实证成立**——同轮混调下非幂等工具（clone/下载/训练）会真实
重复执行。

### 断言点 3：messages 完整性

resume 后子图 messages 历史完整：前序独立轮次的 ToolMessage 原样保留
（call_no=1，非重算值）、顺序不变；resume 值以裸串 ToolMessage 回到 agent，
agent 带值继续到 finalize（result 携带用户值）。**PASS**。

### 断言点 4：连跑稳定

核心场景（独立轮次）与混调场景各连跑 3 次 + 整文件复跑 3 次，全部结论
逐次一致（机理类，零抖动），无需升级 5 次。**PASS**。

### R-S4-10：闭包收集器（collector）在 resume 后的可见性（供 E1/E2 消费）

专项实证（`test_b2_r_s4_10_*`）：节点体内新建、经工具闭包填充的 collector list——

- 首次执行 pause 时：collector == ["pre-interrupt"]（正常收集）；
- resume 后：**collector == ["post-resume"]，pre-interrupt 值丢失**。

机理：resume 重跑父节点函数体 → collector 被重建为空；而前序轮次工具不重放
→ 无法重新填充。**同一场景下 messages 通道是完整的**（checkpoint 恢复，
pre-interrupt ToolMessage 仍在）。

## 给 C2/E2 的挂载建议

1. **可以挂载**：门禁 CP-B2-2 通过，独立轮次的前序副作用在 resume 后恰为 1，不需要架构师咨询、不需要 sp3 式 commit 边界。
2. **request_user_input 必须坚持"单独一轮调用"纪律**（docstring 已含，B1 已锁）：同轮混调会导致同批工具整体重放。挂载后建议 C2/E2 的 e2e 中保留"LLM 服从单独一轮"的观察项（LLM 服从度属 F 阶段真实链路验证范围）；即使 LLM 违纪，write（覆盖写）/ run_command（smoke）/ clone（已存在跳过）等幂等工具末态仍安全，真正风险仅剩非幂等重活（训练），与架构 §8.3.3 的 MVP 接受口径一致。
3. **collector 类实现（R-S4-10）**：E1/E2 若需要收集工具产出（如 execution 收集 run 结果），**不要依赖节点体内新建的闭包收集器承载跨 interrupt 的完整序列**——resume 后会丢失 pre-interrupt 收集值。正确姿势二选一：
   - 从子图最终 messages 回读（`extract_last_tool_result` / 遍历 ToolMessage，checkpoint 恢复保证完整）——推荐，与 dev-plan R-S4-10 预案"ToolMessage 解析兜底"一致；
   - 或把 collector 放在节点体外的持久作用域（进程级），并接受"仅本进程有效"的语义。
4. **机理防回归锚点**：`test_cp_b2_2_mechanism_reasoning_nodes_not_replayed` 固化了"LLM 调用数 pause=2 → final=3"——若未来 langgraph 升级改变子图 checkpoint 粒度（如整子图重放），该用例先翻红预警，重新过门后再放行。

## 失败排查

无失败。探索阶段（先跑一次性探针脚本拿机理事实，再固化断言）与正式用例结论一致。

## 后续动作

- C2/E2 按上述建议挂载 request_user_input（门已过）；E4 真实 wrapper 上复验 Q-C（dev-plan 既定）。
- 遗留观察（非阻断）：langgraph 对测试模块内 FakeLLM 的 msgpack 反序列化提示 "will be blocked in a future version"（`LANGGRAPH_STRICT_MSGPACK`）——影响面为所有带 checkpointer 父图 + 子图 context 注入 FakeLLM 的测试（本文件与 test_sprint4_b1_fix.py 同款），未来升级 langgraph 时需统一处理（如 allowed_msgpack_modules 注册），建议记入 G 阶段升级检查单。
- 本文件与报告未 commit，待 Maria/主控统一收口。
