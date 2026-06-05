# 测试执行报告 - d3-acceptance

- **日期**：2026-06-04 （本地时区）
- **执行人**：@测试工程师代理
- **Sprint**：sprint2
- **触发原因**：任务 D3（`ui/pages/paper_input.py` 论文输入页）独立验收 + 边界/分支用例补全（重点：OBS-D1-01 stale 配置落地、brief+head 合并降级、防重复提交、non-CS 不阻塞）；**第二轮（2026-06-04）追加：BUG-S2-D3-01 修复复验（去 xfail 转真实 PASS）**
- **commit**：4535781（D3 已 commit；`tests/test_paper_input.py` 补强 + 去 xfail 留工作区，未改动任何生产代码。生产侧 BUG-S2-D3-01 修复由全栈开发代理落入 `ui/pages/paper_input.py`）

## 验收结论：**PASS（CP-D3-1~6 全部命中；BUG-S2-D3-01 已修复并回归通过，已关闭）**

CP-D3-1 ~ CP-D3-6 六项验收检查点 + OBS-D1-01 落地（含 stale 序列）+ 函数名别名适配 + brief/head 合并降级 + non-CS 不阻塞 + 防重复提交 + 纯 mock 不烧 token 不连网，全部独立复核命中。曾发现 1 个生产 BUG（BUG-S2-D3-01：关键词搜索"选用"回填崩溃，P1 可选功能、不在 CP-D3-1~6 范围、未阻断核心链路），第一轮以 `xfail(strict=True)` 钉死并转交开发；**全栈开发代理已修复，本报告第二轮复验确认 xfail → PASS、回归零退化，BUG-S2-D3-01 已关闭**（详见文末「修复复验」节）。

---

## 执行范围

- 命令：
  - `pytest tests/test_paper_input.py -q`（D3 单测，含补强）
  - `pytest tests/test_paper_input.py -q -p no:randomly`（连跑 3 次，无序/抖动守门）
  - 单条独立运行 2 个核心补强用例（无序依赖守门）
  - `pytest -q -m "not e2e" --ignore=tests/test_paper_intake.py`（非 e2e 核心回归，连跑 3 次）
- 覆盖用例：`tests/test_paper_input.py` 全部 13 用例（开发 5 + 测试工程师补强 8，其中 1 为 BUG xfail）
- 是否包含 e2e：**否**。D3 验收为纯 mock 层（`streamlit.testing.v1.AppTest` 驱动真实页面脚本 + `patch("ui.pages.paper_input.DeepxivTools")` + `patch("app._get_controller")`），不触发真实 deepxiv / 真实 LLM / 网络；真实端到端留 E 阶段。

---

## 结果摘要

- 通过（第一轮）：D3 单测 12 passed + 1 xfailed；非 e2e 核心回归 402 passed + 1 xfailed（27 deselected = e2e）
- 通过（第二轮，BUG 修复后去 xfail）：D3 单测 **13 passed**；非 e2e 核心回归 **403 passed**（27 deselected = e2e），无 xfailed
- 失败：0
- 跳过：0
- xfailed：第一轮 1（BUG-S2-D3-01 钉死）；**第二轮 0**（BUG 已修，xfail 已去除转常规 PASS）
- 警告：1（`LangChainPendingDeprecationWarning`，langgraph 库内部 `checkpoint/serde/...`，**非项目代码**，全 sp1/sp2 套件长期存在；与 D2 报告同一条，沿用长期跟踪）
- 总耗时：D3 单测 1.17~1.23s/次；非 e2e 核心回归 3.76~4.02s/次

### 连跑稳定性
- D3 单测连跑 3 次：均 12 passed + 1 xfailed（1.23/1.22/1.17s），**0 flaky**，无无序依赖、无抖动
- 非 e2e 核心回归连跑 3 次：均 402 passed + 1 xfailed（4.02/3.76/3.90s），**零退化**（D3 验收前基线 395 + 本次补强 7 个 passed = 402；BUG xfail 不计入 pass，数对一致）
- 单条独立运行（`test_bnd_stale_cfg_legal_then_illegal_disables_button` / `test_bnd_all_widgets_disabled_after_submit`）：2 passed，可脱离套件独立运行

---

## CP-D3-1 ~ CP-D3-6 逐条独立裁定

| CP | 复核结论 | 独立实证方式 |
|----|---------|------------|
| CP-D3-1 `from ui.pages.paper_input import render` 可导入 | **PASS** | `test_cp_d3_1_importable`（importlib + 直接 import）；补 `test_bnd_alias_render_paper_input_page_importable` 验证别名 `render_paper_input_page is render`，与 app.py L283 page_map `("ui.pages.paper_input","render_paper_input_page")` 动态加载契约对齐（`getattr(mod,"render_paper_input_page")` 取到） |
| CP-D3-2 未填 cfg 点"开始复现"不调用 start_task | **PASS** | `test_cp_d3_2_no_cfg_does_not_start`（cfg=None → 按钮 disabled + click 后 `start_task.assert_not_called()` + current_page 仍 input）。补 `test_bnd_stale_cfg_legal_then_illegal_disables_button` 覆盖 OBS-D1-01 最关键的 stale 序列（见下专节） |
| CP-D3-3 填好 cfg+arxiv → start_task 调用 1 次、传参一致 | **PASS** | `test_cp_d3_3_and_6...`（`assert_called_once` + args[0]==arxiv_id + args[1]["default"]["model"]/["api_key"] 与 UI 输入字节一致）。补 `test_bnd_start_without_fetching_card`：未点"获取论文信息"也可开始（卡片是可选辅助非前置） |
| CP-D3-4 mock brief/head 有效数据 → 卡片展示 title/abstract/authors | **PASS** | `test_cp_d3_4_card_renders_brief_head`（HippoRAG title + 作者 Yu Su + abstract 文本均出现、无 non-CS WARNING）。补 `test_bnd_head_failure_degrades_to_brief`（head 抛 TransientError 时降级展示 brief、fetch_error 不置位、abstract/authors 空不崩）+ `test_bnd_brief_failure_shows_error_no_card`（brief 失败 → 卡片为 None + "获取论文摘要失败"文案 + head 不被调用） |
| CP-D3-5 non-CS → WARNING 但按钮可点 | **PASS** | `test_cp_d3_5_non_cs_warns_but_not_blocked`（math.AP → "不属于 CS" WARNING + btn_start disabled False）。补 `test_bnd_is_non_cs_classification_edges`（空 categories 不误报 / 纯非 CS True / 含任一 cs.* False / 大小写不敏感） |
| CP-D3-6 提交后 current_page=="progress"、thread_id 非空、控件禁用 | **PASS** | `test_cp_d3_3_and_6...`（current_page progress + thread_id 非空 + btn_start/arxiv_id_input/btn_fetch disabled）。补 `test_bnd_all_widgets_disabled_after_submit` 扩展到 search section（search_query / btn_search 也 disabled），防重复提交全控件覆盖 |

---

## 高风险项独立裁定（开发声明项逐条复核）

### 1. OBS-D1-01 落地（配置来源走 render_llm_config_form 返回值，禁直读 stale 键）— **PASS（核心 stale 序列实锤）**
独立审 `ui/pages/paper_input.py` L224-227：侧栏 `cfg = render_llm_config_form(default=prefill)`，`prefill` 仅作回显输入，**权威配置源是返回值 cfg**。L262 `can_start = (cfg is not None) and bool(arxiv_id.strip()) and (not submitted)`；L276-283 click 回调内 `if cfg is None: st.error(...); return` 双保险。
关键裁定：`test_bnd_stale_cfg_legal_then_illegal_disables_button` 实证"先填合法（D1 写入 `session_state["llm_config_set"]`）→ 清空 model 变非法"序列：
- 合法时 btn_start disabled=False、stale 键已写入；
- 改非法后 btn_start **重新 disabled=True**（依据返回值，非 stale 键），且 stale 键仍在（确认 D1 不清键、背景成立）；
- 强行 click 后 `start_task.assert_not_called()` + current_page 仍 input。
**若 D3 直读 `session_state["llm_config_set"]` 必会拿到上次合法配置而误放行 —— 实现正确规避。**

### 2. 函数名适配（render + 别名 render_paper_input_page 皆可导）— **PASS**
`__all__ = ["render","render_paper_input_page"]`，L296 `render_paper_input_page = render`。`test_bnd_alias_render_paper_input_page_importable` 断言两者同对象且 app.py page_map 二元组可 getattr 取到。

### 3. brief + head 合并、head 失败降级 — **PASS**
`_fetch_paper_card`：brief 提供 title/tldr/github_url/keywords；head 补 abstract/authors/categories。head 用宽 `except Exception` 兜底降级（L127-129），brief 失败则 return (None, err)。两路分支均有补强用例钉死（见 CP-D3-4 行）。**brief 失败致命、head 失败降级**的非对称契约符合 paper_intake "brief 为主 + head 补充" 同源设计。

### 4. non-CS WARNING 但不阻塞 — **PASS**
`_is_non_cs` 在 categories 为空时保守返回 False（不误报阻塞体验），有任一 `cs.*`（大小写不敏感）视为 CS。WARNING 仅 `st.warning` 展示，不影响 `can_start`。

### 5. 防重复提交（提交后全控件 disabled + 写 thread_id/current_page + rerun）— **PASS**
提交回调写 `_KEY_THREAD_ID` / `_KEY_SUBMITTED=True` / `_KEY_CURRENT_PAGE="progress"` 后 `st.rerun()`；`submitted` 标志驱动 arxiv_id_input/btn_fetch/search_query/btn_search/btn_start 全 disabled。`test_bnd_all_widgets_disabled_after_submit` 覆盖含 search section 全控件。

### 6. 纯 mock 不烧 token 不连网 — **PASS**
全部 13 用例经 `patch("ui.pages.paper_input.DeepxivTools")`（页面把类 import 进自身命名空间，patch 其引用即生效）+ `patch("app._get_controller")`，无任何真实 deepxiv / LLM / 网络调用。

---

## 失败排查

无失败。1 个 xfailed 为下方生产 BUG 钉死（预期失败），非测试缺陷。

---

## 生产 BUG（已转交全栈开发代理）

### BUG-S2-D3-01：关键词搜索"选用"按钮回填 arXiv ID 时崩溃

- **复现路径**：`pytest tests/test_paper_input.py::test_bug_s2_d3_01_search_pick_backfills_arxiv_id`（当前 xfail 钉死）
- **失败类型**：生产代码 BUG（非测试 / 非环境 / 非外部依赖抖动）
- **期望行为**：dev-plan §D3「主区下半（P1 可选）：关键词搜索框 → 展示前 10 条候选」+ 页面 docstring L9「点击某条候选可一键填入上方 arXiv ID 框」。点"选用"应把候选 arxiv_id 回填到主区 arXiv ID 输入框。
- **实际行为**：`ui/pages/paper_input.py` L210 `_render_search_section` 内执行 `st.session_state["arxiv_id_input"] = aid`，而 `arxiv_id_input` 已在 L232 被 `st.text_input(key="arxiv_id_input")` 实例化。Streamlit 禁止在 widget 实例化后修改其 session_state key，抛：
  ```
  streamlit.errors.StreamlitAPIException: `st.session_state.arxiv_id_input`
  cannot be modified after the widget with key `arxiv_id_input` is instantiated.
  ```
  → 用户点"选用"页面直接崩溃（真实运行触发，非 AppTest 特有）。
- **影响范围**：仅影响 P1 可选的"搜索→选用回填"路径，**不影响 D3 核心链路**（侧栏配置 + 直接输入 arXiv ID + 开始复现，CP-D3-1~6 全部正常）。用户若手动输入 arXiv ID（主路径）不受影响。
- **建议修复方向（不替代开发判断）**：去掉 L210 对 widget key `arxiv_id_input` 的直写，仅写非 widget 键 `selected_arxiv_id` 后 rerun；主区 text_input 已用 `value=st.session_state.get(_KEY_SELECTED_ARXIV,"")` 回显，理论上 rerun 后即可生效。但需注意 text_input 同时带 `key="arxiv_id_input"` + `value=` 的双源反模式，rerun 后 widget 已有 state 时 `value=` 可能不覆盖 —— 建议改为单源（仅 `key`，初值经 `_init_page_state` setdefault 注入），由开发统一治理回填一致性。

---

### 修复复验（2026-06-04 第二轮，commit 4535781，BUG-S2-D3-01 → **CLOSED**）

- **修复方案（全栈开发代理）**：采用 pending 中间键单源模式，规避「写已实例化 widget key」反模式：
  - 新增非 widget 中间键 `_KEY_PENDING_ARXIV = "_input_pending_arxiv"` + widget key 常量 `_KEY_ARXIV_WIDGET = "arxiv_id_input"`。
  - `_render_search_section`「选用」按钮（L218-222）不再直写 `st.session_state["arxiv_id_input"]`，改写 `_KEY_PENDING_ARXIV` + `st.rerun()`。
  - `render()`（L245-247）在 `st.text_input(key=_KEY_ARXIV_WIDGET)` 实例化**之前** `pop(_KEY_PENDING_ARXIV)` 灌入 widget key 作初值（实例化前写 widget key 合法），且仅 `not submitted` 时灌入（不破坏防重复提交）。
  - `_init_page_state`（L68）对 widget key 做 `setdefault("")`；text_input 改单源（仅 `key`，去 `value=` 双源反模式）；L256 `selected_arxiv_id` 镜像跟随 widget 当前值。
- **测试侧动作**：去掉 `test_bug_s2_d3_01_search_pick_backfills_arxiv_id` 的 `@pytest.mark.xfail(strict=True)` 标记，转为常规回归用例，并加强断言（5 点）：① `assert not at.exception`（无 StreamlitAPIException）；② widget 当前值 == 选中 arxiv_id `2401.00001`；③ 镜像 `selected_arxiv_id` 跟随；④ pending 键消费后已 pop 清空、不残留；⑤ 回填后填 cfg 可继续主路径（btn_start 可点），证明回填值真正可用。
- **复验结果**：`xfail → PASS`。
  - D3 单测 `pytest tests/test_paper_input.py -q` 连跑 3 次：均 **13 passed**（无 FAIL、无 xfailed、无 XPASS），耗时 1.19 / 1.13 / 1.19s，**0 flaky**。
  - 单条独立运行 `::test_bug_s2_d3_01_search_pick_backfills_arxiv_id`：1 passed（0.95s），可脱离套件运行。
  - 非 e2e 核心回归 `pytest -q -m "not e2e" --ignore=tests/test_paper_intake.py` 连跑 2 次：均 **403 passed + 27 deselected**（无 xfailed），耗时 3.85 / 3.83s。基线 402 passed + 1 xfailed → 去 xfail 后 403 passed + 0 xfailed，**零退化**，数对一致。
- **回归侧独立确认修复未破坏既有契约**：审 `paper_input.py` 现状——OBS-D1-01（L237 cfg 取 `render_llm_config_form` 返回值）、防重复提交（L246 pending 灌入限 `not submitted`；submitted 驱动全控件 disabled）、non-CS 不阻塞（`_is_non_cs` 未动）、CP-D3-1~6 全链路均未受影响（403 回归全绿佐证）。
- **结论**：BUG-S2-D3-01 **已关闭**。D3 验收最终用例数 **13（全部常规 PASS，无 xfail）**。

---

## 补强用例清单（8 个，测试工程师新增）

| 用例 | 覆盖维度 |
|------|---------|
| test_bnd_stale_cfg_legal_then_illegal_disables_button | OBS-D1-01 核心：cfg 合法→非法 stale 序列，按钮重新禁用 + start_task 不调用 |
| test_bnd_head_failure_degrades_to_brief | head 抛异常降级展示 brief，不报死、fetch_error 不置位 |
| test_bnd_brief_failure_shows_error_no_card | brief 失败致命文案 + 卡片 None + head 不被调用 |
| test_bnd_start_without_fetching_card | 未点"获取论文信息"也可直接开始复现（卡片非前置） |
| test_bnd_alias_render_paper_input_page_importable | 别名 render_paper_input_page is render + page_map 适配 |
| test_bnd_is_non_cs_classification_edges | _is_non_cs 边界：空/纯非CS/含cs.*/大小写 |
| test_bnd_all_widgets_disabled_after_submit | 防重复提交全控件 disabled（含 search section） |
| test_bug_s2_d3_01_search_pick_backfills_arxiv_id | BUG-S2-D3-01 xfail(strict) 钉死，修复后 XPASS→FAIL 提醒摘标记 |

D3 单测最终总数：**13 用例**（开发 5 + 补强 8，其中 1 为 BUG xfail）。

---

## 后续动作

- [BUG-CLOSED] BUG-S2-D3-01 已由 @全栈开发代理修复（pending 键中转 + rerun + 实例化前灌入 widget key），测试工程师已去除 `test_bug_s2_d3_01_*` 的 xfail 标记并加强断言，复验 xfail → PASS、回归 403 零退化，**BUG 关闭**（详见「修复复验」节）。
- [遗留-警告] `LangChainPendingDeprecationWarning`（langgraph 库级）沿用 D2 报告长期跟踪项，非项目代码可控，不阻塞。
- [E 阶段] 真实 deepxiv brief/head + 真实 LLM 的 paper_input → start_task → progress 端到端建议随 E1/E3 在三页面就位后统跑。

## 需 Maria 决策的阻断点

无阻断。D3 核心验收（CP-D3-1~6 + OBS-D1-01）PASS，可放行 D4/D5。仅 1 个非阻断生产 BUG（P1 可选搜索回填路径）待开发修复，是否在本 Sprint 内修复或顺延由开发/产品排期，**不阻塞 D3 收尾**。
