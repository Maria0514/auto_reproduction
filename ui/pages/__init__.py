"""Streamlit 业务页面包（Sprint 2 任务 D3/D4/D5）。

刻意**不做**显式 re-export（沿用 sp1/D1 惯例，吸取 BUG-S1-02 / C2 教训）：
``__init__.py`` 里 ``from .paper_input import render`` 之类的显式导出会让
``ui.pages.paper_input`` 在测试中可能被 callable 遮蔽，导致
``from ui.pages import paper_input`` 拿到的不是子模块。

各页面由 app.py 路由通过 ``importlib.import_module("ui.pages.<name>")`` 动态加载，
无需在此聚合导出。
"""
