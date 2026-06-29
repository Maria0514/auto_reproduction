# 测试执行报告 - e1-acceptance

- **日期**：2026-06-28 21:05（本地时区）
- **执行人**：@测试工程师代理
- **Sprint**：sprint3
- **触发原因**：Sprint 3 任务 E1 独立验收（`app.py` 新增 `interrupt_kind` 只读 helper + 把 `page_map` 抽成模块级 `_PAGE_MAP` 并接入 execution/report 两页路由分发）
- **commit**：119abbd（HEAD；E1 改动在工作树未 commit，待 Maria 统一提交）

## 裁决：PASS

E1 三个自测检查点 CP-E1-1~3 经逐条运行时实证全部 PASS，红线（既有 6 方法签名零变化 + 只读 + 无新增页面常量 + sp2 路由不破坏）全部守住，零生产 BUG，零文档硬偏差。补强 32 用例，全量非 e2e 回归 924 passed / 0 failed，零退化。

## 执行范围
- 命令：
  - `.venv/bin/pytest tests/test_sprint3_e1.py -q`（开发自测 22 passed 确认）
  - `.venv/bin/pytest tests/test_sprint3_e1_reinforce.py -q`（补强 32 passed）
  - E1 全套连跑 3 次（54×3 全绿 0 flaky）
  - `.venv/bin/pytest -q -m "not e2e" --ignore=tests/test_paper_intake.py`（全量回归）
  - 应用层 UI 冒烟：`.venv/bin/streamlit run app.py --server.headless true --server.port 8520` + curl 健康探测
- 覆盖用例：`tests/test_sprint3_e1.py`（开发，22）+ `tests/test_sprint3_e1_reinforce.py`（本次补强，32）
- 是否包含 e2e：否（E1 纯 UI 路由 + 只读 helper，无 LLM/deepxiv 依赖；省 deepxiv 日配额）

## 结果摘要
- 通过：924（全量非 e2e 回归）；其中 E1 套件 54（开发 22 + 补强 32）
- 失败：0
- 跳过：25（与基线一致，sp2 shadcn 迁移后 AppTest 看不到 iframe 的 UI 断言 skip，非 E1 引入）
- 警告：1（LangChainPendingDeprecationWarning，langgraph 1.x 库级 pending deprecation，sp1 即存非新引入，待观察）
- 总耗时：回归 121.49s；E1 套件单次约 0.66s

## 逐条 CP 实证结论

### CP-E1-1（红线：既有 6 方法签名零变化）— PASS（最强实证）
不止用 `inspect.signature` 验签名字符串，更用 **AST 段对照 sp2 commit bec628e**：
- 对 sp2 (bec628e) 与当前工作树两版 `app.py` 的 `GraphController` 各方法用 `ast.get_source_segment` 提取**整个方法体源码**做逐字节比较。
- 结果：sp2 全部 **11 个既有方法**（公开 7：start_task/resume_with/poll_state/is_interrupted/get_interrupt_payload/get_worker_error/cancel_task；私有 4：`__init__`/`_worker_run`/`_resume_run`/`_has_interrupt`）**方法体逐字节相同**，唯一新增 `interrupt_kind`，零删除。
- 任务书点名 6 个核心方法（5 + cancel_task）签名零变化 ✓；补强把 `get_worker_error`（sp2 第 7 个公开方法）也纳入黄金签名固化，确认其同样零变化（开发文件把它作为豁免项而未固化签名，补强已补齐）。
- 公开方法集合：sp2 公开 7 个 + E1 唯一新增 `interrupt_kind` = 8 个，无其它方法被改/删/增。

### CP-E1-2（interrupt_kind 四态 + 兜底 + 只读）— PASS（兜底/只读重锤）
mock `get_interrupt_payload` 返回，端到端实证 helper 取值逻辑 `payload.get("interrupt_kind", "planning")`：
- ① planning payload 显式含 `interrupt_kind="planning"` → `"planning"` ✓
- ② planning payload **无该键**（sp2 老 payload，如仅含 reproduction_plan/fix_loop_history）→ **兜底 `"planning"`** ✓（重锤：两种无键非空 payload 形态均兜底）
- ③ execution payload 含 `interrupt_kind="dev_loop_failure"` → `"dev_loop_failure"` ✓
- ④ 无 interrupt（payload 为 `None` / 空 dict `{}`）→ `None` ✓（`not payload` 短路）
- **畸形 payload 透传契约**（补强）：含 kind 键时**原样透传不做白名单/类型清洗**——未知值 `"some_future_kind"` 透传、显式 `interrupt_kind=None` 透传 None（键存在不走 default 兜底）、非字符串值 `123` 透传 123。固化「纯读取」契约，避免 helper 吞掉异常上游数据。
- **只读重锤**（补强 `test_interrupt_kind_read_only_does_not_touch_read_or_write_paths`）：spy 全部读写路径，断言 `interrupt_kind` **恰调 get_interrupt_payload 一次**，`poll_state`（另一读路径）/`resume_with`（写路径）/`start_task`/`cancel_task` **零调用**，`_workers`/`_worker_errors` 不被改动（未起任何工作线程），且**不修改读到的 payload dict**（不 pop/写回）。证 helper 纯只读、不改 state、不调 LLM、不起线程。

### CP-E1-3（两页接入 _PAGE_MAP + sp2 三页不破坏）— PASS
- `_PAGE_MAP` 接入 `STREAMLIT_PAGE_EXECUTION`→`(ui.pages.execution_monitor, render_execution_monitor_page)`、`STREAMLIT_PAGE_REPORT`→`(ui.pages.result_report, render_result_report_page)` ✓
- **键集合 == config 五常量值**，五常量值互不相同无撞键；E1 **未在 config 偷偷新增页面常量**（`dir(config)` 中 `STREAMLIT_PAGE_*` 恰好 5 个，全复用 A1 已落地）✓
- `_PAGE_MAP` 每项值均为 `(module_name, func_name)` 二元组，模块名/函数名非空 str 且模块名在 `ui.pages.` 下；`_PAGE_MAP` 为模块级单例（非 main() 内每次 rerun 重建字面量）✓
- **sp2 三页（input/progress/review）dispatch 未被破坏**：参数化对全部五页 current_page 跑 `main()` dispatch 实证（轻量 fake streamlit + fake importlib），每页都 import 正确模块并调用正确 render 函数，已实现页不走降级 ✓
- **降级路径**：非法 current_page / 缺失 current_page → 回退 input 页；页面模块缺失（ImportError）或缺 render 函数（AttributeError）→ 走 `st.info` 优雅降级，不崩溃、不调任何 render 函数 ✓

### 应用层 UI 冒烟（test plan UI 维度，D3 教训）— PASS
实际 `streamlit run app.py --server.headless true` 启动：
- `/healthz` 返回 HTTP 200，根路径 HTTP 200（服务就绪）；
- 启动日志无 traceback/ImportError/exception（排除 ScriptRunContext/PendingDeprecation 库级噪音）；
- 进程启动后 12s 仍存活未崩溃；
- 独立确认 sp3 两页模块（execution_monitor/result_report）当前**确实尚未实现**（ImportError）—— E1 接入两页常量后仍能启动，证优雅降级生效（这是纯 mock 层捕不到、D3 暴露的集成盲区）。

## 红线核查结论
- `interrupt_kind` 纯只读（不改 state / 不调 LLM / 不起工作线程）— **PASS**（spy 全路径实证）
- E1 未破坏 sp2 既有页面路由与 GraphController 行为（向后兼容）— **PASS**（AST 段字节级 + 五页 dispatch + 回归零退化）
- E1 没有偷偷新增页面常量（复用 config 已有 5 个 `STREAMLIT_PAGE_*`）— **PASS**

## 失败排查
无。E1 套件 54 用例 + 全量回归 924 用例全部通过。

## 补强用例清单（32 条，`tests/test_sprint3_e1_reinforce.py`）
- CP-E1-1 深证（9）：sp2 公开方法**全集 7 个**黄金签名（含 get_worker_error）+ 私有 helper 仍在 + interrupt_kind 是唯一新增公开方法。
- CP-E1-2（13）：含 kind 透传（含未知值）/无键兜底（两形态）/None/空 dict → None（参数化）+ 显式 None 值透传 + 非字符串值透传 + 只读重锤（全路径 spy）+ 不 mutate payload。
- CP-E1-3（10）：键 == config 常量值一致性 + 五常量无撞键 + 无新增页面常量 + 值结构完整（2-tuple/非空 str/ui.pages 下）+ 模块级单例 + 五页全 dispatch（参数化含 sp2 三页）+ 非法页降级回 input + 缺 current_page 默认 input + AttributeError 也优雅降级。

## 回归数字
- 全量非 e2e：**924 passed / 0 failed / 25 skipped / 28 deselected / 1 warning / 121.49s**
- 核对：D1 验收基线 870 + E1 开发 22 + 补强 32 = 924（逐项吻合），skipped 仍 25，sp1/sp2/sp3 零退化。
- 稳定性：E1 套件（54）连跑 3 次全绿 0 flaky；回归无 flaky。

## 偏差与遗留（非阻断）
- **OBS-E1-01（一致性小瑕疵，非 BUG）**：`_init_session_state()`（app.py L322）仍用字面量 `"input"` 而非 `STREAMLIT_PAGE_INPUT` 常量；因 `STREAMLIT_PAGE_INPUT == "input"` 行为完全等价（dispatch 默认回退也走 `STREAMLIT_PAGE_INPUT`，二者同值）。E1 已把 `main()` 内 current_page 默认值与 `_PAGE_MAP.get` 默认键都改用常量，唯独 `_init_session_state` 的 setdefault 字面量未一并改。**不影响功能、不影响测试**，建议 E2/E3 顺手统一（非阻断，无需 Maria 拍板）。
- **项目级遗留 warning**：LangChainPendingDeprecationWarning（langgraph 库级 pending deprecation，sp1 即存非 E1 引入），全 sprint 一致，待 langgraph 升级后观察。

## 对 E2/E3 的交接确认
- `_PAGE_MAP` 两页入口已预留：E2 实现 `ui/pages/execution_monitor.py::render_execution_monitor_page`、E3 实现 `ui/pages/result_report.py::render_result_report_page`，落地即自动被 `main()` dispatch（无需再改 app.py 路由）。已实测两页模块当前为 ImportError，优雅降级生效。
- E2 dev_loop 失败决策面板判定：`is_interrupted(thread_id) and controller.interrupt_kind(thread_id) == "dev_loop_failure"`。已实证 planning interrupt（含无键兜底）返回 `"planning"`，不会误进 execution 面板；execution interrupt（payload 含 `interrupt_kind="dev_loop_failure"`，C3 既有）返回 `"dev_loop_failure"`。
- `interrupt_kind` 纯主线程只读，可与 `poll_state`/`is_interrupted` 同轮询周期安全调用（不触碰工作线程、不阻塞）。

## 后续动作
- E2/E3 落地后，对 `is_interrupted + interrupt_kind == "dev_loop_failure"` 判定面板做集成测试（含真实图 interrupt#2 payload 经 get_interrupt_payload→interrupt_kind 端到端）。
- F 阶段凭证补齐后，把 E1 路由 + interrupt_kind 纳入端到端 UI 流（planning interrupt#1 与 dev_loop interrupt#2 真实区分）。
- 建议 E2/E3 顺手修 OBS-E1-01（`_init_session_state` setdefault 改用 `STREAMLIT_PAGE_INPUT` 常量），统一一致性。
