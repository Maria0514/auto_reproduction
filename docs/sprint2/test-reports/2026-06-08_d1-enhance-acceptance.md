# 测试执行报告 - D1 增强独立验收（test plan driven）

- **日期**：2026-06-08 04:04（本地时区）
- **执行人**：@测试工程师代理
- **Sprint**：sprint2
- **触发原因**：D1 增强改动（api_key 回退 + 表单字段/校验调整 + load_dotenv）独立验收——按 `2026-06-08_test-plan-d1-enhance.md` 29 用例逐条检验，不轻信开发自报 460 passed
- **commit**：c7509c4（工作区有 D1 增强未提交改动 + 本验收新增的集成测试文件）

---

## 0. 验收结论速览

**验收结果：PASS（硬门槛全过）**，无生产 BUG，无需 Maria 决策的阻断点。

- 安全不变量（checkpoint 5 条 api_key 恒空）：**真实 SqliteSaver 落盘读回实证 PASS**。
- L3 原 404 崩溃：**已复现成因 + 已消除**（blank api_key 在消费点拿到真实 env key）。
- 回退正确性 default + override 双路：**PASS**。
- Q3 开发改写的 10 个失效既有用例：**正确反映新契约，未掩盖回归**。
- 补强：测试工程师独立补齐 **9 条 L2 集成用例**（开发完全漏做的硬门槛 #1）。
- 唯一受限：L4 真实 LLM 端到端跑通——**沙箱代理 infra 阻塞，留 E 阶段**（非回退 bug）。

---

## 1. 执行范围

- 命令（核心）：
  - `pytest -q -m "not e2e"`（默认非 e2e 全量回归，3 次连跑）
  - `pytest tests/test_d1e_api_key_fallback_integration.py -v`（新增 L2 集成）
  - `pytest tests/test_llm_client.py tests/test_llm_config_form.py -q`（L1）
  - `pytest tests/test_llm_routing.py tests/test_app_controller.py -q`（回归基线）
  - 独立探针（AST + 直接调用，不经 dev 测试）：create_llm 回退 6 项、两个 helper 真值表、validate 反转、安全不变量真实落盘读回、L3-equivalent main() AppTest 渲染、L3 命门消费点 key 注入、L4 网络探针
  - `streamlit run app.py --server.headless true`（真机启动尝试，见 L3）
- 是否包含 e2e：默认排除（`-m "not e2e"`）；e2e 层凭证就绪（`.env` 有 `LLM_API_KEY` + `DEEPXIV_TOKEN`），但网络被沙箱代理阻塞，详见 L4。

---

## 2. 结果摘要

| 项 | 数值 |
|---|---|
| 非 e2e 全量通过 | **469 passed**（基线 460 + 本次新增 9 集成）3/3 连跑稳定 |
| e2e 收集 | 27（`-m e2e` 时 469 deselected，gating 正确） |
| 跳过 | 0（非 e2e 层）；e2e 在默认 `-m "not e2e"` 下被 deselect |
| 警告 | 1（`LangChainPendingDeprecationWarning`，第三方 langgraph 库 import 期，非 D1 引入，非项目级） |
| flaky | 0（非 e2e 全量 3 连跑 + D1E 靶向 3 连跑 + 单用例独立运行全稳定） |
| 总耗时 | 非 e2e 全量约 4.1s/次 |

---

## 3. 分层验收结论（按 test plan 4 层）

### L1 单元（复核 + 独立探针）—— PASS

开发已落 L1 全量（test_llm_client.py 5 条 + test_llm_config_form.py D1E 段），我**不轻信，AST + 直接调用独立复跑**：

- **create_llm 回退 6 项**（独立探针 + dev 用例双印证）：
  - 空 `""` → 回退 env-key ✓
  - 纯空白 `"   "` → strip 视空 → 回退 ✓
  - 非空 `"sk-USER"` → 不回退，且 `get_llm_api_key` 短路未被调用 ✓
  - 回退 None → 不在 create_llm 抛错，原样传 ChatOpenAI ✓
  - 不回写入参 dict（调用后入参 api_key 仍 `""`，deepcopy 比对相等）✓
  - 签名仍 1 参（`inspect.signature` len==1）✓
- **校验反转 5 项**：base_url/model 合法 + api_key 空 → 通过且 `cfg["api_key"]==""`、errors 不含 api_key；base_url 空 + api_key 空 → 仅报 base_url；非空 api_key 原值保留；`_panel_is_blank` 行为不变 ✓
- **`_should_block_for_missing_api_key` 真值表**：独立探针验证 空+env空→True / 空+env有→False / 非空→False（无论 env），**额外补验** `block({}, None)==True`（缺 api_key 字段当空，dev 未测此边界，行为正确）✓
- **slider / `_round_to_step` 边界**：min512/max16384/step512/默认8192；`_MAX_TOKENS_DEFAULT == config.DEFAULT_LLM_MAX_TOKENS == 8192` 动态锚定；端点 512/16384 合法 int；round：8000→8192、7800→7680、4096→4096、clamp 100→512、20000→16384，**额外补验** 256→512（round-half 落 0 后 clamp，dev 未测）✓
- **AST 实证**：`_should_block_for_missing_api_key` 与 `_round_to_step` 均为模块级 `FunctionDef`（可直测纯函数，无需起 AppTest），与 Q2 裁定一致 ✓

### L2 集成（安全不变量，最高优先级硬门槛）—— PASS（**测试工程师补齐**）

**关键发现**：test plan 把 I01/I02（真实 SqliteSaver 落盘读回 api_key 恒空）列为硬门槛 #1，但**全栈开发只补了 L1，未落地任何 I 系列集成用例**。开发的 `test_cp_d2_9` 只读回 `current_step`、且用非空 api_key，不满足"blank api_key → 5 条恒空"实证。测试工程师独立补齐 `tests/test_d1e_api_key_fallback_integration.py`（9 条），**禁用 FakeGraph，真实 SqliteSaver(tmp_path) + 真实 build_graph 拓扑 + 真实 start_task/_refresh 链路**（4 节点 patch 成写最小 state 的 stub，planning 调真实 `interrupt()` 暂停，避免真网络/真 LLM）：

- **I01**：default api_key 留空 → worker 跑到 planning interrupt → 主线程 get_state().values 读回 → `default.api_key == ""` ✓
- **I02**：default + 4 override 全留空 → checkpoint 5 条 api_key 全 `""`，且 override 的 base_url 被真实落盘（证明确实写入，只 api_key 恒空）✓
- **I02b**（命门加固）：monkeypatch env 有 `REAL-ENV-KEY` → checkpoint 仍 `""`，且 `"REAL-ENV-KEY" not in repr(state)`（真实 key 绝不渗入 state/checkpoint）✓
- **I03**：`_refresh_llm_config_set` 静态审计——即便 env 有真实 key，返回 set api_key 恒空（_refresh 层不回退）✓
- **I04**：真实 `resolve_llm_config` 选 default 路径 → create_llm 回退 env，且 resolved dict 未被回写 ✓
- **I05**：真实 resolve_llm_config 选 override 路径（base_url 命中 override）→ create_llm 回退同一 env 源 ✓
- **I06**：override 用户显式填 `sk-OVERRIDE` → 不回退，get_llm_api_key 未被调用 ✓
- **I07**：`react_base` 消费点源码断言仍为 `create_llm(resolve_llm_config(state["llm_config_set"], node_name))`（回退对消费点透明，签名不变）✓
- **I08**：兜底分支触发（env 空 + default 空）→ 返回 None 且 `SESSION_KEY` 未写入 session_state（不引入新 stale 写入，守 OBS-D1-01）✓

**安全不变量实证结论**：跑 start_task（任意 panel api_key 留空）后，真实 SqliteSaver checkpoint 里 default + 各 override 的 api_key **恒为 `""`**；真实 key 只在 create_llm 进程内存出现，永不回写 state/checkpoint。三道实证（静态审计 I03 / 真实落盘读回 I01-I02b / 仅进程内存 I04-I06）全过。

### L3 真机冒烟（D3 盲区专项硬门槛）—— PASS（环境受限下做到等价实证，明确标注）

**做到什么程度**：
1. **真实 `streamlit run app.py --server.headless true` 启动成功**：uvicorn 绑定 127.0.0.1:8765，stdout 打印 "You can now view your Streamlit app"，stderr "Uvicorn server started"，**无 import 期崩溃、无 StreamlitDuplicateElementKey**。
2. **HTTP 探活受限**：本沙箱 localhost TCP 命名空间隔离 + 后台进程在工具调用结束后被 harness 回收，导致 `curl --noproxy` 探活拿不到端口（HTTP 000 / 进程已被回收）。**这是沙箱 infra 限制，非 app 问题**（进程确实成功启动并打印就绪 URL）。
3. **L3-equivalent（弥补无法 HTTP 探活）**：用 `AppTest.from_file("app.py")` 驱动**真实 `main()` → 真实 importlib 页面加载 → 真实 `paper_input.render()` → 真实侧栏 `render_llm_config_form()`**，实证：`at.exception` 为空（无崩溃）、`st.error` 为空（**无 DuplicateElementKey**——D3 崩溃 BUG 不复现）、`default_max_tokens` slider 存在、`default_base_url` 预填 `https://inference-api.nvidia.com/v1`、`load_dotenv` 把 `LLM_API_KEY` 注入 app 进程。
4. **L3 命门（原 404 复现并消除）**：Maria 手动测时崩溃成因 = blank api_key 无 env 回退 → ChatOpenAI 拿空 key → 首次 LLM 调用 404/auth。独立探针在真实消费点（react_base L828 路径）捕获 ChatOpenAI 收到的 api_key：blank api_key → **回退注入真实 env key（25 字符）** → 不再传空 key → **NO 404**。原崩溃路径已消除。
5. **纯交互（拖 slider 手感、点击发起的真实 DOM 事件）**：无头环境 + 无 Playwright，**标人工冒烟项，不伪造通过**。

### L4 后端 e2e（真实 LLM）—— 留 E 阶段（环境阻塞，非回退 bug）

- **凭证就绪**：`.env` 有 `LLM_API_KEY`（25 字符）+ `DEEPXIV_TOKEN`，conftest 凭证驱动会触发 e2e。
- **网络阻塞（沙箱 infra）**：
  - 代理 ON（默认 env 有 `ALL_PROXY=socks5h://localhost:1080`）：`ImportError: Using SOCKS proxy, but the 'socksio' package is not installed`（openai httpx 客户端构造期）。
  - 代理 OFF（清 proxy env）：`APIConnectionError: Connection error`（`inference-api.nvidia.com` 无代理不可达）。
- **结论**：两种失败模式**均为环境网络/依赖问题，非 api_key 回退 bug**——独立探针已证回退把真实 key 正确喂给 ChatOpenAI（L3 命门），调用仅卡在网络层。L4「留空回退真 LLM 跑通」+「错误 key 401 不回退」**留 E 阶段**，待沙箱代理 infra 就绪（装 `httpx[socks]` 或放通出网）后补跑，e2e 层稳定性复跑 ≥3 次。
- **附带影响说明**：默认 bare `pytest`（不带 `-m "not e2e"`）会收集并跑 27 个 e2e（凭证存在），全数因上述代理问题 fail/error（9 failed + 11 errored）——**与 D1 改动无关，是沙箱 infra + L-A3-02 gating 注释遗留共同导致**，见观察项 OBS。

---

## 4. Q3 职责复核：开发改写的 10 个失效既有用例

逐条复核 `test_llm_config_form.py` 中因校验反转而改写/补强的用例，**确认正确反映新契约、未掩盖回归、无遗漏红测**：

| 用例 | 旧契约 | 新契约复核 | 结论 |
|---|---|---|---|
| CP-D1-2 | 5 字段全空 → None + "api_key 不能为空" | 改为显式清空 base_url/model，断言 None 且 `not any("api_key" in m and "不能为空")` | 正确反转，仍断言失败路径（base_url/model 必填）✓ |
| CP-D1-3 | max_tokens 默认 4096 | 改断言 8192（slider 默认）+ overrides=={} | 正确 ✓ |
| CP-D1-5 | 部分 override 报 api_key 缺失 | 改为仅填 base_url → 只报 model 缺失、不报 api_key | 正确反转，仍断失败 ✓ |
| CP-D1-8 (slider) | number_input 下界 256 | 改 slider + 下界 512，且 set_value(100) 被静默拒绝回退默认（OBS-D1-02） | 正确 ✓ |
| test_paper_input CP-D3-2 | 不填即 cfg=None | 适配预填：显式清空 base_url/model 才得 None | 正确（不掩盖，仍验 cfg=None 不启动）✓ |
| 新增 U07-U15 / I 探针 / slider / round / should_block / prefill | — | 覆盖反转后的合法路径 + 兜底 + 边界 | 充分 ✓ |

**关键判断**：改写**没有把红测改绿来掩盖回归**——所有改写仍保留对应的失败断言（base_url/model 必填失败、override 部分填校验失败、兜底拦截失败），只是把"api_key 空→失败"这条**已不再成立的旧契约断言**移除/反转，与 §2.8.2 校验反转一致。无遗漏未改的红测（routing 24、signature 1、app_controller D2 31 全绿）。

---

## 5. 补强统计

测试工程师独立补强 **9 条 L2 集成用例**（新文件 `tests/test_d1e_api_key_fallback_integration.py`），全部覆盖 test plan 漏做的硬门槛 #1（安全不变量真实落盘读回 + 回退双路 + 消费点透明 + 兜底不写 stale）。非 e2e 基线从 460 → **469**。

---

## 6. 失败排查

**无生产 BUG。** 默认 `-m "not e2e"` 全量 469 passed 0 failed。

唯一非通过项是 bare `pytest`（含 e2e）下的 27 个 e2e fail/error：
- **失败类型**：环境问题（沙箱网络代理 + socksio 缺失），非测试 bug、非生产 bug。
- **关键报错**：`ImportError: Using SOCKS proxy, but the 'socksio' package is not installed`（proxy ON）/ `APIConnectionError: Connection error`（proxy OFF）。
- **排查步骤**：栈回溯定位到 `langchain_openai/_client_utils.py` 构造 httpx 客户端时读 `ALL_PROXY=socks5h://localhost:1080` → httpx 需 socksio → 未安装 → ImportError。`env | grep -i proxy` 确认沙箱注入了 socks5/http 代理。
- **处置**：标记为环境限制，L4 留 E 阶段；不阻塞 D1 验收（回退逻辑本身已在 L3 命门探针证明正确喂 key）。

---

## 7. 后续动作 / 遗留项

- [E 阶段] L4 真实 LLM e2e（E01 留空回退跑通 + E02 错误 key 401 不回退）：待沙箱装 `httpx[socks]` 或放通出网后补跑，≥3 次稳定性复跑。
- [观察项 OBS-D1E-01] `pytest.ini` markers 注释写"默认 skip，加 `--run-e2e` 启用"，与 conftest 凭证驱动实现不符（L-A3-02 历史遗留再次确认）。当前凭证存在时 bare `pytest` 会跑 e2e 撞代理问题。建议：要么修注释为"凭证驱动"，要么加 `addopts = -m "not e2e"` 默认排除 e2e（符合"默认 pytest 不触发真实外部调用"约束）。**归属待定**（pytest.ini 属测试基建，可由测试工程师修；但涉及团队 e2e 运行约定，建议 align 后改）。
- [遗留] 第三方 `LangChainPendingDeprecationWarning`（langgraph encrypted.py import 期）长期存在，非 D1 引入，记入观察。
