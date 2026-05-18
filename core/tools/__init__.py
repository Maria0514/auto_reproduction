"""工具子包：deepxiv Reader 薄封装与 ReAct 工具工厂。

刻意不做显式 re-export，避免 callable 遮蔽 `core.tools.deepxiv_tools` 子模块。
下游一律使用完整路径导入：
``from core.tools.deepxiv_tools import DeepxivTools, get_paper_brief_tool, ...``。
"""
