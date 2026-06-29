# Sprint 3 任务 E3 独立验收报告 — 结果报告页 `ui/pages/result_report.py`

- **日期**：2026-06-28
- **验收人**：@测试工程师代理（独立验收 + 补强）；BUG 修复转正由 @主控 收口
- **被测产出**：`ui/pages/result_report.py`（结果报告页 S3-10）+ 开发自测 `tests/test_sprint3_e3.py`
- **补强产出**：`tests/test_sprint3_e3_reinforce.py`
- **状态**：未 git commit（待 Maria 统一）

---

## 裁决：PASS

核心链路（三形态报告渲染 + 出口 + 复用 reporting 判定）CP-E3-1~4 逐条运行时实证全部命中。验收发现 1 个非阻断防御性生产 BUG（BUG-S3-E3-01），经 Maria 拍板「本 Sprint 修」后已于本次收口修复并转常规回归（详见下）。

## CP-E3-1~4 逐条结果（自造场景运行时实证，不轻信开发自测）

- **CP-E3-1 PASS** — 模块可导入；`render` / `render_result_report_page` 别名一致，`__all__` 与 E1 `_PAGE_MAP[STREAMLIT_PAGE_REPORT]=("ui.pages.result_report","render_result_report_page")` 完全对齐；`_load_report_markdown` 三态（真实文件读全文 / 空路径 / 文件不存在）防御正确。
- **CP-E3-2 PASS（命门，重锤通过）** — 见「三形态判定契约重锤」。
- **CP-E3-3 PASS** — 指标对比表三方并集、缺值「—」、脏数据防御、artifact / fix_loop_history / deliverables 取数与渲染全对；degraded 形态亦展示指标对比表（开发仅验 full_success，验收补验）。B 档红线见下。
- **CP-E3-4 PASS** — `_reset_to_input_page` 三件套（切 `STREAMLIT_PAGE_INPUT` + 解锁 `_input_submitted` + 清 `thread_id`）完整；AppTest 真实点击「返回输入页开启新任务」三键全部重置；no_thread 占位页出口独立可用。

## 三形态判定复用 reporting 的契约重锤（通过）

- **同一函数对象**：运行时 `is` 实证 `ui.pages.result_report._determine_report_form IS core.nodes.reporting._determine_report_form` == True，E3 未自行重写判定逻辑。
- **优先级一致**：穷举 code_only > full_success > degraded；`success` 严格 `is True`（`1`/`"true"`/`"True"`/`{}` 全判 degraded）；str 形态 `execution_mode`、`execution_result=None`、空 state，E3 与 reporting 逐一同口径。
- **页面卡片 vs 报告正文不矛盾（关键集成契约）**：同一 state 下 E3 卡片判定形态 == reporting 生成的 Markdown 正文头部声明形态，三形态全一致。杜绝「页面说成功、正文说降级」。

## B 档无硬判定红线核实（Q-S3-01，守住）

指标对比表 `st.table` 列名为「指标 / 论文 baseline / 计划 expected / 本次复现值」，列名与所有单元格**绝无**「达标/不达标/未达标」字样，缺值渲染「—」。「达标」二字仅出现在否定声明 caption「不做任何硬性达标结论」中（与 reporting 报告正文同口径），非逐项硬判定。补强用例用精确判据（否定上下文 + 「不达标」「未达标」零容忍）封死，避免粗关键词误报。

## 坑6 规避核实（规避成功）

`core/nodes/__init__.py` 顶层 `from core.nodes.reporting import reporting` 把 callable 绑到 `core.nodes.reporting` 属性（子模块被遮蔽）。但生产代码 `from core.nodes.reporting import _determine_report_form` 走 from-import 子模块加载路径，拿到真函数（运行时 `is` 实证）；测试侧统一用 `importlib.import_module` 取模块，正确。

## 生产 BUG — BUG-S3-E3-01（非阻断 P2，**已修复转正**）

- **位置**：`ui/pages/result_report.py::_load_report_markdown`
- **根因**：`read_text(encoding="utf-8")` 对非 UTF-8 报告文件抛 `UnicodeDecodeError`（`ValueError` 子类，**不属于** `OSError`），原 `except OSError` 漏接 → `render()` 整页崩溃（AppTest `at.exception` 非空），违反 E3 自身 docstring「读失败仅降级提示、绝不崩页」防御契约。
- **影响**：正常链路 reporting 写报告恒为 UTF-8，**不触发**；仅外部/手工放置非 UTF-8 报告文件时触发，属防御路径失效，不阻塞 happy path。
- **处置**：验收时以 2 条 `xfail(strict=True)` 钉死（不掩盖不阻塞）；Maria 拍板「本 Sprint 修」后，主控收口修复：`except OSError` → `except (OSError, UnicodeDecodeError)`（1 行），并去除 2 个 xfail 标记。
- **修复验证**：2 个原 xfail 用例转常规回归后 PASS；全量回归（主控统一）无 XPASS / 无残留 xfailed。

## 补强用例数与自测数字

- **补强新增**：`tests/test_sprint3_e3_reinforce.py` 共 39 条（验收时 37 passed + 2 xfailed 钉死 BUG；**修复转正后 39 全 passed**）。
- **E3 套件合计**：开发 23 + 补强 39 = 62 条；验收阶段连跑 3 次 60 passed + 2 xfailed 0 flaky；BUG 修复后 62 全 passed。
- warning：仅 1 个 langgraph 库级预存 `LangChainPendingDeprecationWarning`（非项目代码，长期跟踪基线）。

## streamlit 冒烟

`streamlit run app.py --server.port 8512 --server.headless true`：2s 内就绪，root 200 + `/_stcore/health` 200，启动日志无 traceback / import error；用完立即 kill（无端口残留，不与并行 E2 验收冲突）。report 页 render 路径三形态/出口/边界由 60 条 AppTest 在 streamlit runtime 下覆盖。未补 Playwright browser e2e（HTTP + AppTest 已足够，非强制）。

## 隔离边界（核实通过）

`git status` 确认 E3 验收仅新建 `tests/test_sprint3_e3_reinforce.py` + 本报告；未碰 `docs/TODO.md` / `dev-plan.md`（由主控统一收口）、对方 E2 文件、生产代码（BUG 修复由主控收口执行，非验收代理擅改）。

## 全量回归（主控统一收口）

`.venv/bin/pytest -q -m "not e2e" --ignore=tests/test_paper_intake.py` → **1056 passed / 0 failed / 25 skipped / 28 deselected / 1 warning / 122.33s**（E1 验收基线 924 + E2 开发 26 + E2 补强 44 + E3 开发 23 + E3 补强 39 = 1056 逐项吻合，零退化）。未跑 e2e（省 deepxiv 配额，留 F 阶段）。
