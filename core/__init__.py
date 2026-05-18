"""核心包：状态、错误、LLM 客户端、ReAct 基础设施、节点与工具子包。

刻意不做显式 re-export，避免被 callable 遮蔽子模块（例如 `core.nodes.paper_intake`
既是子模块也是 callable，曾导致测试 import 路径异常）。下游一律使用完整路径导入：
``from core.state import GlobalState`` / ``from core.errors import AutoReproError``。
"""
