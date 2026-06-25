# 测试执行报告 - b2-acceptance

- **日期**：2026-06-24 23:09（本地时区）
- **执行人**：@测试工程师代理
- **Sprint**：sprint3
- **触发原因**：任务 B2（`core/tools/code_fs_tools.py`，S3-02 工具部分）独立验收 + 边界补强
- **commit**：1a58937（B2 产物未提交；本报告在工作区状态下验收）

## 执行范围
- 命令：
  - `.venv/bin/pytest tests/test_sprint3_b2.py -q`（开发交付 21 用例基线）
  - `.venv/bin/pytest tests/test_sprint3_b2_strengthen.py -v`（补强 20 用例）
  - `.venv/bin/pytest -q -m "not e2e" --ignore=tests/test_paper_intake.py`（全量非 e2e 回归）
  - 稳定性连跑 3 次：`.venv/bin/pytest tests/test_sprint3_b2.py tests/test_sprint3_b2_strengthen.py -q`
- 覆盖用例：`tests/test_sprint3_b2.py`（CP-B2-1~5，21 项，开发交付）+ 新增 `tests/test_sprint3_b2_strengthen.py`（20 项补强）
- 是否包含 e2e：否（B2 为纯文件操作工具，无 LLM/deepxiv 依赖，本就无 e2e 层；e2e 28 项 deselected）

## 结果摘要
- 通过：665（全量非 e2e；含 B2 21 + 补强 20 = 41）
- 失败：0
- 跳过：25（既有套件凭证/环境相关跳过，非 B2 相关）
- 警告：1（`LangChainPendingDeprecationWarning`，langgraph 库 `checkpoint/serde` 内部，非 B2 引入，与历史报告一致的库级预存项）
- 总耗时：全量回归 122.66s；B2 双文件单跑 0.51s
- 稳定性：B2 双文件连跑 3 次均 41 passed，0 flaky

## CP-B2-1~5 逐条独立复核结论（运行时实证）

| CP | 结论 | 实证 |
|----|------|------|
| CP-B2-1 write 成功 + 越界拒 | PASS | 写 code_dir 返回合法 JSON（json.loads OK，含 path/bytes_written）；`/tmp/evil.py` 绝对路径 + `../` 三级逃逸均拒，且目标文件未落盘 |
| CP-B2-2 read 读取/越界/不存在 | PASS | 读已存在返回原文；`/etc/passwd` 越界拒；不存在文件返回 `Error: ...不存在` 字符串不抛 |
| CP-B2-3 list 合法 JSON + 越界 | PASS | 返回 `{success,path,entries,truncated}` 合法 JSON，字典序、子目录带 `/`；`/tmp` 越界拒 |
| CP-B2-4 三工厂 BaseTool + 序列化合规 | PASS | 三工厂均 `isinstance BaseTool`；中文不转义（输出无 `\u`）；`sort_keys` 字典序（直证 `_serialize_tool_result({"b":..,"a":..})=='{"a":..,"b":..}'`）；禁 str(dict)（输出无 `'success'` repr 标志，json.loads OK） |
| CP-B2-5 异常捕获转字符串不打断 | PASS | write OSError/IsADirectoryError、read OSError/目录目标、list OSError 均被 try/except 兜底转错误字符串或错误 JSON，无异常逃逸 |

## 重点核验项结论

1. **BUG-S1-02 端到端闭环（核心验收点）—— PASS**：
   三工具输出经 `ToolMessage` 喂给 `core/react_base.py::extract_last_tool_result` 真正解析：
   - write 输出（含中文路径字段）→ 解析回 dict，`path`/`bytes_written`/`success` 字段无损往返；
   - list 输出（含中文条目「数据集.csv」「src/」）→ 解析回 dict；
   - 错误 JSON（success=False）→ 同样可解析；
   - **负向锚点**：`str(dict)`（Python repr 单引号）喂同一 helper 返回 `None`，直证当年 BUG-S1-02「str(dict) 致下游 json.loads 永久失败」的根因，确认 B2 序列化合规彻底闭环；
   - read 返回纯文本（非 JSON）时 extract 返回 None（语义正确，read 本不走 JSON 回填路径）。

2. **路径越界多形态 —— PASS**：
   - 符号链接逃逸（workspace 内链接指向外部目录/文件）：write/read/list 三工具均被 `resolve()` 拆穿拒绝，外部 secret 内容不泄露、外部文件不被写；
   - WORKSPACE_DIR 边界自身（target == workspace）：接受（与 git_tools `resolved == workspace or is_relative_to` 不变量一致）；
   - 借建父目录逃逸（`../` 深层路径）：在 mkdir 之前拒绝，外部目录不被创建。

3. **read 超长截断边界 —— PASS**：恰好 == TOOL_RESULT_MAX_LENGTH 不截（原样返回）；超 1 字节截到 MAX 并附固定标记 `\n... [truncated at N chars]`（无动态片段，Prompt Cache 友好）；空文件返回空串不误判不存在。

4. **list truncated 边界 —— PASS**：恰好 == cap 项 truncated=False；cap+1 项 truncated=True 且 entries 截到 cap 项仍字典序；空目录返回空 entries。

5. **write 建父目录 —— PASS**：多级不存在父目录自动创建且仍受 workspace 限定；不能借建目录逃逸。

6. **范式一致性 —— PASS**：`_is_within_workspace`（resolve+is_relative_to）、`_serialize_tool_result`（json.dumps ensure_ascii=False/sort_keys=True/default=str）与 `core/tools/git_tools.py` 同源同不变量。

## 失败排查
无。开发交付 21 用例 + 补强 20 用例全绿，全量回归零退化。

## 补强用例清单（tests/test_sprint3_b2_strengthen.py，20 项）
- BUG-S1-02 端到端解析：write/list/错误 JSON 可解析 + str(dict) 反例失败 + read 纯文本不误解析（5）
- 路径越界多形态：符号链接 write/read/list 逃逸拒 + 边界自身允许 + 建父目录受限 + ../建目录逃逸拒（6）
- read 截断边界：== MAX 不截 / MAX+1 截 / 空文件（3）
- list truncated 边界：== cap / cap+1 / 空目录（3）
- 异常不逃逸：read 目录目标 / write 已存在目录目标 / serialize default=str 兜底 Path（3）

## 后续动作
- 无阻断遗留。B2 验收 PASS，可继续推进依赖 B2 的 C1（coding 节点）开发。
- B2 产物仍未 git commit（按要求测试工程师不 commit），交由 Maria 统一提交。
- 下一次触发：C1 落地后对 coding 节点做集成验收（届时 B2 三工具作为真实工具被 ReAct 调用）。
