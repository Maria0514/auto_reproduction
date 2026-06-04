"""Sprint 2 Streamlit UI 层包。

按 sp1 既有惯例（`core/__init__.py` / `core/tools/__init__.py`），本包不做显式
re-export，避免 callable 遮蔽子模块（吸取 BUG-S1-02 / C2 教训）。子模块按需
`from ui.components.llm_config_form import render_llm_config_form` 直接导入。
"""
