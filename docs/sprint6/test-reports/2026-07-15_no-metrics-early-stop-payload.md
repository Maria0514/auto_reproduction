# 测试执行报告 - no-metrics-early-stop-payload

- **日期**：2026-07-15
- **执行人**：@测试工程师代理
- **Sprint**：sprint6（批次 2，NO_METRICS 早停）
- **触发原因**：CP-2.5-2 假绿回填——早停终态 payload 文案此前无任何断言守住（error_summary/fix_hint 未被验证），为已落地的 bug 修复补上缺失断言，把假绿补实。纯测试工作，不改产品代码。
- **commit**：9f41545（产品代码含开发代理未 commit 的早停修复，见下"产品代码状态"）

## 执行范围
- 命令：
  - `.venv/bin/pytest tests/test_sprint6_b2.py -q`
  - 三个目标用例单独运行 `-v`
  - 加分项验红：临时注释 `execution.py:2072-2076` 的 `replace(...)` 覆盖块后重跑（验完已还原）
- 覆盖用例（本次补断言涉及）：
  - `tests/test_sprint6_b2.py::TestNoMetricsEarlyStop::test_cp_2_5_2_early_stop_skips_retry_coding`（既有用例内**加法**扩充 payload 文案断言）
  - `tests/test_sprint6_b2.py::TestNoMetricsEarlyStop::test_cp_2_5_2_normal_no_metrics_round_no_early_stop_text`（**新增**对照用例）
  - `tests/test_sprint6_b2.py::TestNoMetricsEarlyStop::test_cp_2_5_2_early_stop_payload_keys_unchanged`（**新增**键结构用例）
- 是否包含 e2e：否（全部为纯函数级单元测试，mock `interrupt`，无外部调用、无凭证需求）

## 结果摘要
- 通过：40（基线 38 + 新增 2 个用例函数；早停用例本体的文案断言为既有用例内加法扩充，不新增函数）
- 失败：0
- 跳过：0（fixture 相关 skip 在本次运行环境下 fixture 均存在，未触发）
- 警告：3（均为既有项目级 warning，非本次引入）
  - `LangChainPendingDeprecationWarning`（langgraph checkpoint serde，`allowed_objects` 默认值将变更）
  - `PydanticDeprecatedSince20` × 2（`test_cp_2_1_5_schema_byte_equal` 用 `.schema()`，Pydantic V2 建议 `model_json_schema()`）
  - 说明：这两类 warning 长期存在于本套件，建议后续清理（第 2 项可就地改测试，第 1 项属 langgraph 依赖侧），本次补测未触碰不扩大化。
- 总耗时：约 0.8s

## 假绿根因与补实内容

**假绿根因**：CP-2.5-2 被勾 `[x]`，但既有 `test_cp_2_5_2_early_stop_skips_retry_coding`（约 :561）**只断言了**：
- `result["_dev_loop_route"] != _ROUTE_RETRY_CODING`（不走回 coding）
- `len(interrupted_payloads) == 1`（触发了 interrupt#2）

从未断言 interrupt#2 的 **payload 文案内容**。因此「面板显示早停轮次上下文」这条验收（AC-S6-10、架构 §3.4、dev-plan CP-2.5-2）实际没被任何测试守住。修复前的产品代码里 payload builder 喂的是原始 `feedback`（通用 NO_METRICS 文案 "代码跑通但未产出指标"），且 reason 文案里轮次是 `已连续 {N} 轮` = "已连续 2 轮"（off-by-one，正确应为 N+1=3）——面板从未拿到早停文案，测试却全绿。**CP-2.5-2 此前断言不足、本次补实。**

**本次补的三层断言**：
1. **早停终态 payload 文案（核心，守 AC-S6-10）**——在既有早停用例内加法扩充：
   - `payload["error_summary"]` 含 `"已连续 3 轮"`（**显式写死 3**，锁死 off-by-one，不用 `N+1` 表达式/import 常量算，常量若被改错本断言应跟着变红）
   - `payload["error_summary"]` 含 `"自动修复无进展"`
   - `payload["fix_hint"]` 含 `"已连续 3 轮"`（两个面板渲染字段 execution_monitor.py:579/:582 都守）
2. **对照断言（守"两态可区分"缺陷本质）**——新增 `test_cp_2_5_2_normal_no_metrics_round_no_early_stop_text`：
   - 构造普通 NO_METRICS 轮次（`fix_loop_history` 尾部仅 1 条 no_metrics < N=2，预算/回合数远低于上限）
   - 断言 `_no_metrics_stalled` 为 False、`route == _ROUTE_RETRY_CODING`、**不产生 interrupt**
   - 对该路通用 feedback 构造 payload，断言 `error_summary`/`fix_hint` **不含** `"已连续"` → 证明早停终态与普通轮次文案可区分
3. **payload 键结构未变（守 AC-S4-05）**——新增 `test_cp_2_5_2_early_stop_payload_keys_unchanged`：
   - 早停 payload 键集合 == 普通 payload 键集合 == 冻结的 10 键
     （interrupt_kind / fix_loop_count / error_category / error_summary / fix_hint / auto_fixable / fix_loop_history / execution_errors / representative_stderr / options）
   - `payload["error_category"] == "no_metrics"`（`replace` 只覆盖 summary/fix_hint，保留 category）、`interrupt_kind == "dev_loop_failure"`

## 失败排查
无失败。

**加分项——验红证据（确认断言真正守住修复）**：临时注释 `execution.py` 早停覆盖块的 `replace(...)`（保留 `panel_feedback = feedback`）后重跑：
- `test_cp_2_5_2_early_stop_skips_retry_coding` **变红**，报错精确：
  `AssertionError: error_summary 应含早停轮次文案 '已连续 3 轮'，实际: '代码跑通但未产出指标'`
  → 证明该文案断言在修复缺失时确实失败，是有效守门。
- `test_cp_2_5_2_early_stop_payload_keys_unchanged` **仍 PASS**（符合预期）：该用例在测试内自行复刻 `replace(...)` 构造早停 feedback，不依赖产品代码路径，专门守键结构/category 不变；键结构本不该被此 bug 影响，故不该跟着变红。
- 验证后**立即还原**产品代码。`git diff -- core/nodes/execution.py` 与开发代理修复后一致（新增 `replace` import + `_NO_METRICS_EARLY_STOP_SUMMARY` 常量 + `panel_feedback` 覆盖 + payload builder 喂 `panel_feedback`），**无测试工程师的任何额外改动**。

## 产品代码状态
- `core/nodes/execution.py` 存在未 commit 的 diff——这是**开发代理的早停修复本体**（背景说明「未 commit」），非本次测试工作引入。本次补测对该文件零改动，仅改 `tests/test_sprint6_b2.py`。
- 本次未 commit（按指令）。

## 后续动作
- CP-2.5-2 假绿回填（dev-plan `[x]` 说明补注）由主控统一处理，测试侧本报告已点明"此前断言不足、本次补实"。
- 遗留 warning（LangChainPendingDeprecationWarning / PydanticDeprecatedSince20）建议后续独立清理，非本任务范围，不扩大化。
- 下一次跑测试触发条件：early-stop 相关产品代码或 payload 键结构再变动时回归本套件。
