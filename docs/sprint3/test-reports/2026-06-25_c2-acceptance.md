# 测试执行报告 - c2-acceptance（reporting 节点独立验收 + 补强）

- **日期**：2026-06-25 16:00（本地时区）
- **执行人**：@测试工程师代理
- **Sprint**：sprint3
- **触发原因**：全栈开发代理交付 C2（S3-05 `core/nodes/reporting.py`）独立验收 + 边界补强；C2 刚开发完成、未 git commit。
- **commit**：dbf4e44（HEAD，C2 改动未提交）

## 执行范围
- 命令：
  - `.venv/bin/pytest tests/test_sprint3_c2.py -v`（C2 全套，开发 15 + 补强 22 = 37）
  - `.venv/bin/pytest -q -m "not e2e" --ignore=tests/test_paper_intake.py`（全量非 e2e 回归）
  - 稳定性连跑 `.venv/bin/pytest tests/test_sprint3_c2.py -q` × 3
  - 单用例独立性抽样运行 2 条
- 覆盖用例：`tests/test_sprint3_c2.py`（CP-C2-1~6 + 22 条补强）
- 是否包含 e2e：否（reporting 纯函数、无 LLM/无 deepxiv 依赖，本就无 e2e；真实链路 e2e + UI 消费留 E/F 阶段）

## 结果摘要
- 通过：C2 文件 37 / 37；全量回归 727 passed
- 失败：0
- 跳过：回归 25 skipped（全为既有 D3/D4 UI 迁移 skip，与 C2 无关）
- 警告：1（langgraph 库级 `LangChainPendingDeprecationWarning`，sp1 即存在的预存项，非本任务引入）
- 总耗时：C2 单文件 0.7s；全量回归 121.46s；3 次连跑 0.67/0.69/0.68s

## CP-C2-1~6 逐条独立复核结论（运行时实证）

- **CP-C2-1 PASS**：`importlib.import_module("core.nodes.reporting")` 拿子模块（避开 `__init__.py` 同名 callable 遮蔽）；`inspect.signature(reporting).parameters == ["state"]`，返回 dict。
- **CP-C2-2 PASS**：full_success（`execution_result.success=True`）报告含「指标对比」章节（baseline 0.91 / 复现 0.893 并列）、「产物清单」含 model.pt、「执行概况」含 runtime 123.4、成功结论「复现成功」；`report_path` 非空且文件真写出（read_text 回读断言章节存在）。补强加验三列（baseline/expected/复现）齐全、空指标不崩。
- **CP-C2-3 PASS**：code_only 含「代码位置」+ code_output_dir + deliverables（train.py/README.md）+「仅生成代码」标注；**断言不出现「指标对比」「本次复现值」章节**；`execution_result is None` 仍产有效非空报告。
- **CP-C2-4 PASS**：degraded（success=False / export_code）标「未成功复现」+「降级原因」(含 degraded_nodes execution) + node_errors 摘要解析出 `[error_category=runtime]` 的 category + fix_loop_history 两轮逐轮（import/补依赖、runtime/修正张量维度）+ user_fix_decision(export_code) + 保留代码目录。补强加验三轮顺序渲染 + 行数精确（表头+3 数据行）+ data_missing 场景。
- **CP-C2-5 PASS（纯读红线）**：三形态各跑一遍，返回 dict 键集合**精确 = {report_path, current_step}**，绝无 node_errors/degraded_nodes/fix_loop_history；另实证调用后 state 三 list 字段原对象不被 mutate（== before）。
- **CP-C2-6 PASS**：report_path 经 `resolve()+is_relative_to(WORKSPACE_DIR.resolve())` 校验落 workspace 下；与 code_output_dir 同父目录（`Path(code_output_dir).parent/"report.md"`）；越界 code_output_dir 被限定回 workspace；缺 code_output_dir 回退 `workspace/<arxiv_id>/report.md`。补强加验越界时 outside 目录下绝无 report.md 写出 + `../` 路径逃逸被钳制。

## 重点核验项结论

- **三形态判定优先级矩阵**：① Enum/str CODE_ONLY + success=True → 仍 code_only（不被 success 抢，2 条）；② execution_result=None 非 code_only（含 export_code）→ degraded；③ success 显式 False / 缺 success 键（`success is True` 严格判定）→ degraded。Enum 与 str 两种 execution_mode 取值均覆盖。全部符合架构 §2.4 优先级。
- **指标对比表「仅对比不判定」（Q-S3-01 B 档）**：复现值远高(0.99 vs 0.9)/远低(0.5 vs 0.9)于 baseline 时，断言报告均不出现「达标/未达标/不达标/成功复现达到/超过 baseline/通过验证/PASS/FAIL」等硬结论措辞，仅并列数值。守住。
- **node_errors error_category 解析**：`[error_category=import]`/`[error_category=data_missing]` 前缀被 `_parse_error_category` 解析进独立 category 列；无前缀时渲染占位符不报错；node_name + error_type(transient/permanent) + 原始 message 同时保留。
- **fix_loop_history 修复历程**：三轮 FixLoopRecord（round_number/error_summary/error_category/fix_strategy/timestamp）逐轮渲染，表格数据行数精确；空 history + count>0 时仅显示计数 + 无逐轮提示。
- **report_path 与 code_output_dir 对齐**：与 C1 落点 `workspace/<arxiv_id>/code` 同父级（`workspace/<arxiv_id>/report.md`），code_output_dir 缺失回退 arxiv_id；越界退回安全落点逻辑确认。
- **缺字段健壮性**：paper_analysis/reproduction_plan/fix_loop_history/node_errors/degraded_nodes/paper_meta/code_output_dir 任一为 None 或缺失时，三形态均不抛异常、仍产有效报告（极简 state 仅 workspace_dir+execution_mode 也走 degraded 正常产出）。reporting 作为终点消费者的容错性达标。

## 失败排查
无失败用例。

## 偏差记录（非阻断，回报全栈开发代理知悉）

- **DEV-C2-01（设计不一致，非 BUG，低优先）**：`_resolve_report_path` 的越界校验基准用模块级 `WORKSPACE_DIR.resolve()`，而越界后退回的"安全落点"用 `_workspace_root(state)`（即 `state["workspace_dir"]`）。当 `state["workspace_dir"]` 与 `config.WORKSPACE_DIR` **不一致**时（如用户自定义 workspace_dir），判定为越界后退回的安全落点仍落在 `state["workspace_dir"]` 下、**不在校验基准 WORKSPACE_DIR 下**，且文件确实写出。
  - **实证**：构造 state.workspace_dir=state_ws、WORKSPACE_DIR=config_ws、code_output_dir 落 state_ws 内 → 判定越界 → 退回落点 `state_ws/2401/report.md`（在 state_ws 内、不在 config_ws 内）、文件写出。
  - **影响评估**：**常规路径无害**——`create_initial_state` 默认把 `workspace_dir` 设为 `WORKSPACE_DIR`，coding 的 code_output_dir 也源自同一 `state["workspace_dir"]`，二者一致时越界校验与回退基准统一。仅在用户显式传入与 config 不同的 workspace_dir 时暴露，且退回落点仍在受控 workspace（state.workspace_dir）内、未落到任意外部路径。
  - **建议方向**（交开发判断）：越界校验基准统一改用 `_workspace_root(state)`（与 code_output_dir 来源、与回退落点一致），而非模块级 `WORKSPACE_DIR`；或在文档显式约定二者必须一致。本次测试**未对此越界基准不一致做硬断言**（因常规路径等价、非功能正确性问题），仅记录。

## 后续动作
- DEV-C2-01 由开发评估是否在 D（graph 接线）或后续小修中统一越界基准；不阻断 C2 验收。
- reporting 真实链路三形态端到端渲染 e2e + UI 结果报告页消费 report_path 留 E/F 阶段。
- C3 execution 写 `code_output_dir` / D graph 接线时确认 reporting 拿到的 code_output_dir 与 execution work_dir 一致（与 C1 交接项一致）。

## 裁决
**C2 独立验收 PASS。** CP-C2-1~6 全部运行时实证命中，纯读红线 + 三形态优先级 + 仅对比不判定 + 缺字段健壮性全部守住。补强 22 用例（C2 文件共 37），未破坏开发既有 15 条。全量非 e2e 回归 727 passed / 0 failed，3 次连跑 0 flaky。**零生产 BUG**，1 项非阻断设计偏差 DEV-C2-01 已记录回报。
