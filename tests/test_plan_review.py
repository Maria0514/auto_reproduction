"""plan_review 页测试——真实 LangGraph 契约（e2e）。

迁移说明（shadcn 化后测试范式拆分）
====================================
ui/pages/plan_review.py 已从原生 widget 迁到 streamlit-shadcn-ui，决策按钮渲染在
iframe 里。``streamlit.testing.v1.AppTest`` 看不到 iframe 组件、点击不回写 session_state，
故原先 14 个 AppTest 用例按性质拆成两套新范式：

- 逻辑类（可导入、无 thread_id 兜底、payload=None/残缺不 KeyError、软提示阈值）
  → tests/test_plan_review_logic.py（AppTest，断言文本/调用，不依赖点击 iframe）。
- 点击类（approve / code_only / revise / switch_repo / cancel 二次确认）
  → tests/test_plan_review_e2e.py（@pytest.mark.browser，真起 streamlit 子进程 +
    chromium，进 iframe 点 shadcn 按钮，mock controller 落盘断言 payload）。

本文件仅保留真实 GraphController + 真实 graph 的 interrupt-payload 契约 e2e
（@pytest.mark.e2e，凭证缺失自动 skip）——它不依赖点击 UI，跑真实读路径。
"""

from __future__ import annotations

import pytest


# =========================================================================== #
# T-D5-E1（e2e）：真实 GraphController + 真实 graph 跑到 planning interrupt，
#                 验 get_interrupt_payload 真读路径返回页面消费契约。无凭证自动 skip。
# =========================================================================== #
def _has_credentials() -> bool:
    from config import get_deepxiv_token, get_llm_api_key

    return bool(get_llm_api_key()) and bool(get_deepxiv_token())


@pytest.mark.e2e
@pytest.mark.skipif(not _has_credentials(),
                    reason="缺少 LLM_API_KEY 或 DEEPXIV_TOKEN 环境变量")
def test_d5_e1_interrupt_payload_contract_e2e(tmp_path):
    """真实跑到 planning interrupt → get_interrupt_payload 返回页面消费的全部契约键。"""
    import sqlite3
    import uuid

    from langgraph.checkpoint.sqlite import SqliteSaver
    from langgraph.types import Command  # noqa: F401

    import app as app_module
    from config import (DEFAULT_LLM_MAX_TOKENS, DEFAULT_LLM_TEMPERATURE,
                        get_llm_api_key, get_llm_base_url, get_llm_model)
    from core.graph import build_graph
    from core.state import LLMConfig, create_initial_state

    db_path = str(tmp_path / "ckpt_d5_e1.db")
    thread_id = f"task-d5-e1-{uuid.uuid4().hex[:8]}"
    llm_config = LLMConfig(
        base_url=get_llm_base_url(), model=get_llm_model(),
        api_key=get_llm_api_key() or "", temperature=DEFAULT_LLM_TEMPERATURE,
        max_tokens=DEFAULT_LLM_MAX_TOKENS,
    )

    # 真实工作图跑到 planning interrupt（不烧 token 之外的额外开销，单跑一次）。
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    saver = SqliteSaver(conn)
    graph = build_graph(checkpointer=saver)
    cfg = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
    initial = create_initial_state(user_input="2405.14831", llm_config=llm_config)
    graph.invoke(initial, cfg)
    conn.close()

    # 真实 GraphController（主图指向同一 tmp 库）读 interrupt payload。
    import threading

    from core.checkpointer import get_checkpointer

    controller = app_module.GraphController.__new__(app_module.GraphController)
    controller._lock = threading.Lock()
    controller._workers = {}
    controller._worker_errors = {}
    controller._main_checkpointer = get_checkpointer(db_path)
    controller._main_graph = build_graph(checkpointer=controller._main_checkpointer)

    payload = controller.get_interrupt_payload(thread_id)
    assert payload is not None, "真实跑到 planning interrupt 后 payload 不应为 None"
    # 页面消费的契约键齐全
    assert "reproduction_plan" in payload
    assert "resource_info" in payload
