# e2e harness apps

供 Playwright e2e 测试（`tests/test_*_e2e.py`）启动的 streamlit 宿主 app。

这些 **不是** 测试用例本身，也不是项目入口。e2e 测试的工作方式是：起一个真实
streamlit 子进程跑这里的某个 harness app（注入 mock controller / 假 state），再用
chromium 去渲染、点击、读 iframe 文本断言。harness 用假数据，不依赖真实 LLM / 凭证。

测试通过 `PROJECT_ROOT / "tests" / "e2e_harnesses" / "<harness>.py"` 引用本目录文件；
streamlit 子进程的 `PYTHONPATH` / `cwd` 设为项目根，故 harness 里 `import app` /
`import ui.pages.xxx` 能正常解析到项目根模块。

| harness | 被谁用 | 模拟 |
|---|---|---|
| `_e2e_app.py` | `tests/test_plan_review_e2e.py` | 计划审核页（mock controller 落盘记录决策点击） |
| `_e2e_progress_app.py` | `tests/test_analysis_progress_e2e.py` | 分析进度页（按 `E2E_SCENE` 注入不同终态 state） |

运行 e2e（marker `browser`，chromium 已装）::

    .venv/bin/python -m pytest tests/test_plan_review_e2e.py -v -m browser
