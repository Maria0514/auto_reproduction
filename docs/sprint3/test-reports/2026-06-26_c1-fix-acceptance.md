# 测试执行报告 - c1-fix-acceptance（坑1 端到端硬伤 + 坑2 read_section 不可用）

- **日期**：2026-06-26 06:38 PDT
- **执行人**：@测试工程师代理
- **Sprint**：sprint3
- **触发原因**：代码审查发现 C1 coding 节点严重缺陷（坑1：写代码位置与 state.code_output_dir 脱钩；坑2：context 无 arxiv_id 致 read_section 不可用）→ 架构师定方案（A 工具层 base_dir 绑定 + B 首轮无条件注入 + C 落点校验 + 坑2 arxiv_id 注入）→ 开发落地 → 本次独立验收（不轻信开发自测）
- **commit**：3fd791a（修复在 working tree，未提交；HEAD=dbf4e44 为修复前态，diff 可精确审查）

## 执行范围
- 命令：
  - `.venv/bin/pytest tests/test_sprint3_b2.py tests/test_sprint3_b2_strengthen.py tests/test_sprint3_c1.py tests/test_sprint3_c1_fix.py -q`
  - `.venv/bin/pytest tests/test_sprint3_c1_fix_reinforce.py -q`（新增补强）
  - `.venv/bin/pytest -q -m "not e2e" --ignore=tests/test_paper_intake.py`（全量回归）
  - 连跑 3 次：`tests/test_sprint3_c1_fix_reinforce.py tests/test_sprint3_c1_fix.py tests/test_sprint3_c1.py`
- 覆盖用例：B2（test_sprint3_b2.py 21 + b2_strengthen 20）+ C1（test_sprint3_c1.py 16）+ C1-fix（test_sprint3_c1_fix.py 18）+ 新增补强（test_sprint3_c1_fix_reinforce.py 21）
- 是否包含 e2e：否（红线要求，e2e 留 F 阶段）

## 结果摘要
- 通过：757（全量非 e2e）；其中 c1 修复相关套件 b2+c1+c1_fix+reinforce = 96
- 失败：0
- 跳过：25（全为 D3/D4 UI shadcn 迁移既有 skip，与本修复无关）
- 警告：1（langgraph 库级 LangChainPendingDeprecationWarning，预存，与本修复无关）
- 总耗时：全量 120.65s；c1 套件 < 1s
- 基线对照：基线 736 + 本次新增 21 补强 = 757，完全吻合，**零退化**

## 8 条断言逐条独立复核结论（运行时实证）

| # | 断言 | 独立结论 | 实证来源 |
|---|------|---------|---------|
| 1 | 首轮 context（fix_count=0/exec_result=None）含绝对 code_output_dir == _resolve_code_output_dir(state) | PASS | c1_fix::test_first_round_context_has_abs_code_output_dir；补强覆盖 exec非空+fix=0 仍走首轮 |
| 2 | payload arxiv_id == paper_meta.arxiv_id（含 paper_meta 缺失/非 dict 时 None） | PASS | c1_fix::test_context_has_arxiv_id（{}→None）+ 补强参数化 str/int/list/None 均 None 不抛 |
| 3 | 三处幂等同值（build_context 注入 / write 工具 base_dir / map_result code_dir） | PASS | c1_fix::test_three_way_idempotent_code_dir + 补强 test_three_way_idempotent_in_fix_round（修复回合 state 带 code_output_dir 时仍同值） |
| 4 | write 工具越界被拒（A 核心）：外路径 success=false 含「code_output_dir」+ 未落盘；内相对路径 success=true 落 code_dir 内 | PASS | c1_fix::test_write_tool_base_dir_rejects_out_of_base + 补强 4 形态（绝对系统路径/../../etc/workspace内code外/sub锚定）均实证「未落盘」 |
| 5 | B2 向后兼容（base_dir=None）：workspace 内成功、外越界含「WORKSPACE_DIR」；B2 既有 41 用例零改动通过 | PASS | c1_fix::test_b2_backward_compat_no_base_dir + 补强 .. 逃逸/深层建父目录；**git status 确认 b2 两文件零改动**；41 用例全绿 |
| 6 | _has_written_any_file 落点校验（C 核心）：path 外→False / 内→True / 无 path 不计 / success=false 不计 | PASS | c1_fix::test_has_written_landing_check + 补强 path==code_dir/子目录/..内/..外/空串/前缀陷阱(code vs code_other)/无path |
| 7 | 真实 FS 断言：真实 write 工具写文件，state.code_output_dir 目录实际存在该文件 | PASS | c1_fix::test_real_filesystem_write_lands_in_state_dir + 补强 test_real_fs_write_then_read_roundtrip（write→read 回读逐字节一致） |
| 8 | read/list 仍能跨访问 selected_repo.local_path（code_dir 外、workspace 内）未被 A 误伤 | PASS | c1_fix::test_read_list_cross_access_selected_repo |

## 开发改 3 处既有用例的独立判定（重点：是否弱化断言）

逐处审查 `git diff HEAD -- tests/test_sprint3_c1.py`（HEAD 为修复前态）：

1. **`test_reinforce_branch_exec_result_but_fix_count_zero_is_first_round` L453**：
   `assert "code_output_dir" not in payload`（旧）→ `assert "code_output_dir" in payload` + `assert Path(...).is_absolute()`（新）。
   **判定：新契约的必然同步，且强度不降反升**。旧断言「首轮不注入 code_output_dir」正是坑1-B 要修的 bug 行为；坑1-B 修复就是首轮无条件注入。改后多了 `is_absolute()` 校验。该用例核心目的（首轮不注入 `fix_round`/`last_error_summary`）的断言原封保留。**未弱化。**

2. **`test_reinforce_has_written_mixed_fail_then_success_is_true` L533**：
   `{"success": True}`（旧）→ `{"success": True, "path": "/tmp/x/model.py"}`（新）。
   **判定：合理同步**。坑1-C 引入落点校验后（生产 L359-361 `if not written_path: continue`），无 path 的成功 write 不再计为产出；要保留该用例「混合 fail→success → True」本意必须补上 code_dir 内 path。**未弱化本意，但暴露覆盖空缺**（见下）。

3. **`test_reinforce_has_written_multimodal_list_content_success` L546**：同 2，multimodal 成功 JSON 补 path。**判定：合理同步。**

**结论：3 处改动均为「坑1-B/坑1-C 新契约的必然同步」，无一处为过测试而弱化断言强度。** 但改动 2/3 把 path 补进既有用例后，**「无 path 的 success=true 应判 False」这个坑1-C 关键边界反而无人覆盖**——我已在补强 `test_has_written_success_without_path_is_false` 显式补回，实证 False 正确。

## 补强用例（test_sprint3_c1_fix_reinforce.py，21 条，未改动开发既有文件）

- 坑1-C 落点校验边界（7）：无 path→False / path==code_dir→True / 子目录→True / .. resolve 内→True / .. resolve 外→False / 空串→False / 前缀陷阱 code vs code_other→False
- 坑1-A 相对/绝对锚定（6）：相对 sub/model.py 锚定+落盘 / 绝对 code_dir 内放行 / 绝对系统路径拒+未落盘 / ../../etc 拒 / workspace内code外拒+未落盘 / sub/../a.py 合法放行
- 坑2 arxiv_id 鲁棒（1 参数化×4）：paper_meta 为 str/int/list/None → arxiv_id None 不抛
- B2 向后兼容补强（2）：无参 .. 逃逸拒含 WORKSPACE_DIR / 深层子目录建父目录落盘
- 三处幂等修复回合（1）：state 带 code_output_dir+exec_result+fix>0 时三处仍同值
- 真实 FS 闭环（1）：write→read 回读逐字节一致

## 失败排查

无。全程 0 失败。

## 稳定性

补强+c1_fix+c1 套件（55 用例）连跑 3 次全绿（0.87/0.78/0.79s），0 flaky。真实 FS / 路径校验用例（tmp_path + monkeypatch WORKSPACE_DIR）均独立、无运行顺序依赖、不污染真实 workspace。

## 最终裁决

**PASS**。坑1（A 工具层 base_dir 绑定 + B 首轮无条件注入 + C 落点校验）与坑2（arxiv_id 注入）修复全部命中架构 §2.2.2/§2.2.3/§2.2.4 设计；8 条断言独立重证全 PASS；开发改 3 处既有用例均为新契约必然同步、无弱化；B2 向后兼容确认（41 用例零改动 + git 未动 b2 文件）；补强 21 用例填补「无 path success=true」等边界空缺；全量回归 757 passed / 0 failed 零退化；3 次连跑 0 flaky。**零生产 BUG。**

## 后续动作
- e2e（真实 LLM 写文件落 code_output_dir）留 F 阶段，凭证就绪后补跑。
- 修复在 working tree 未提交，由 Maria 统一 commit。
- 遗留非阻断项 L-A3-01 / L-A3-02 仍在（与本修复无关）。
