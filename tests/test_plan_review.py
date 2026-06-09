"""S2-07 计划审核页（D5 ui/pages/plan_review.py）单元 + e2e 测试。

测试策略（对齐 D3/D4 AppTest 范式 test_paper_input.py / test_analysis_progress.py）
============================================================================
- UI 行为用 ``streamlit.testing.v1.AppTest`` 驱动真实 ``render`` + ``patch(
  "app._get_controller")`` 注入 Mock controller（页面 ``from app import _get_controller``，
  故 patch app 模块源符号）。
- 纯 mock，不烧 token、不连网：controller 的 get_interrupt_payload / resume_with /
  cancel_task 全是 MagicMock 桩，断言其被以正确 payload 调用。
- thread_id 经 AppTest 脚本顶层 ``st.session_state.setdefault`` 预置（模拟 D4 跳转后）。
- e2e（@pytest.mark.e2e）：凭证缺失自动 skip（沿用 tests/conftest.py + skip_if_no_creds 范式）。

运行::

    .venv/bin/python -m pytest tests/test_plan_review.py -q
"""

from __future__ import annotations

import importlib
from typing import Dict, List, Optional
from unittest.mock import MagicMock, patch

import pytest
from streamlit.testing.v1 import AppTest


# --------------------------------------------------------------------------- #
# AppTest 脚本：模拟 app.py page 路由（顶层预置 thread_id，模拟 D4 跳转后进入 review）。
# render() 内决策按钮点击会 st.rerun() 重跑脚本；切到别页时渲染 stub 跳出循环。
# --------------------------------------------------------------------------- #
_APP_SCRIPT = """
import streamlit as st
st.session_state.setdefault("thread_id", "task-review-001")
st.session_state.setdefault("current_page", "review")
page = st.session_state.get("current_page", "review")
if page == "review":
    from ui.pages.plan_review import render
    render()
elif page == "progress":
    st.write("PROGRESS_STUB")
else:
    st.write("INPUT_STUB")
"""

# 无 thread_id 脚本（不预置 thread_id，直接进 render 的 no-thread 兜底守卫）。
_APP_SCRIPT_NO_THREAD = """
from ui.pages.plan_review import render
render()
"""


def _make_payload(
    revise_count: int = 0,
    soft_hint_threshold: int = 5,
    degraded_nodes: Optional[List[str]] = None,
    node_errors: Optional[List[Dict]] = None,
) -> Dict:
    """构造一份完整可用的 interrupt payload（plan_review 页消费的全部字段）。"""
    return {
        "reproduction_plan": {
            "plan_summary": "复现 HippoRAG 检索增强方法",
            "environment": {"python": "3.11", "cuda": "12.1"},
            "data_preparation": ["下载 MuSiQue 数据集", "构建知识图谱"],
            "code_strategy": "use_repo",
            "execution_steps": [
                {"step_name": "建图", "command": "python build.py", "expected_output": "graph.pkl"},
                {"step_name": "检索", "command": "python retrieve.py", "expected_output": "metrics.json"},
            ],
            "expected_results": {"recall@5": 0.89},
            "estimated_time": "约 2 小时",
            "deliverables": ["复现报告", "指标对比表"],
            "user_feedback": None,
            "approved": False,
        },
        "resource_info": {
            "repos": [
                {"url": "https://github.com/OSU-NLP-Group/HippoRAG", "source": "github",
                 "is_official": True, "stars": 1200, "forks": 90, "quality_score": 0.95,
                 "last_commit_date": "2025-01-01", "has_readme": True, "has_requirements": True},
                {"url": "https://github.com/other/fork", "source": "github",
                 "is_official": False, "stars": 10, "quality_score": 0.3},
            ],
            "selected_repo": {"url": "https://github.com/OSU-NLP-Group/HippoRAG"},
            "external_resources": [],
            "resource_strategy": "use_official",
        },
        "paper_analysis_summary": {
            "method_summary": "基于个性化 PageRank 的检索",
            "datasets": ["MuSiQue"],
            "metrics": ["recall@5"],
            "framework": "PyTorch",
        },
        "degraded_nodes": degraded_nodes if degraded_nodes is not None else [],
        "node_errors": node_errors if node_errors is not None else [],
        "revise_count": revise_count,
        "soft_hint_threshold": soft_hint_threshold,
        "max_total_llm_calls": 50,
    }


def _make_controller_mock(payload: Optional[Dict]) -> MagicMock:
    """构造 GraphController mock：脚本化 get_interrupt_payload，resume_with/cancel_task 为桩。"""
    controller = MagicMock()
    controller.get_interrupt_payload.return_value = payload
    return controller


def _run(controller: MagicMock, script: str = _APP_SCRIPT) -> AppTest:
    """patch app._get_controller，跑一次 AppTest，返回 at。"""
    with patch("app._get_controller", return_value=controller):
        at = AppTest.from_string(script)
        at.run()
    return at


def _collect_text(at: AppTest) -> str:
    """聚合 AppTest 元素树所有可读文本，便于断言渲染内容。"""
    parts: List[str] = []
    for collection in (at.title, at.subheader, at.caption, at.markdown,
                       at.text, at.warning, at.info, at.error):
        for el in collection:
            parts.append(str(getattr(el, "value", "")))
    for el in getattr(at, "code", []):
        parts.append(str(getattr(el, "value", "")))
    return "\n".join(parts)


# =========================================================================== #
# T-D5-01：入口可导入（importlib，避免 __init__ 遮蔽子模块）
# =========================================================================== #
def test_d5_01_importable():
    """render_plan_review_page 可导入 + 与主名 render 同对象 + current_page 约定。"""
    mod = importlib.import_module("ui.pages.plan_review")
    assert callable(mod.render)
    assert mod.render_plan_review_page is mod.render
    assert mod.__all__ == ["render", "render_plan_review_page"]


# =========================================================================== #
# T-D5-02：无 thread_id → 走兜底分支不崩，显示「尚未启动任务」，不调 controller
# =========================================================================== #
def test_d5_02_no_thread_id_fallback():
    """无 thread_id → 兜底提示「尚未启动任务」并 return，不崩、不调 get_interrupt_payload。"""
    controller = _make_controller_mock(payload=None)
    at = _run(controller, script=_APP_SCRIPT_NO_THREAD)
    assert not at.exception
    text = _collect_text(at)
    assert "尚未启动任务" in text
    # 兜底分支在取 controller 之前 return，不应触达 get_interrupt_payload
    controller.get_interrupt_payload.assert_not_called()


# =========================================================================== #
# T-D5-03：get_interrupt_payload 返回 None → 「计划尚未就绪」并 return，不渲染后续
# =========================================================================== #
def test_d5_03_payload_none_not_ready():
    """payload=None → 显示「计划尚未就绪」并 return，不渲染计划/仓库/决策按钮。"""
    controller = _make_controller_mock(payload=None)
    at = _run(controller)
    assert not at.exception
    text = _collect_text(at)
    assert "计划尚未就绪" in text
    # return 在渲染之前：无任何决策按钮、无计划区块（## 复现计划 markdown header）
    assert all("btn_approve" != getattr(b, "key", None) for b in at.button)
    assert "## 复现计划" not in text


# =========================================================================== #
# T-D5-04：完整 payload → 各 _render_* 子函数渲染、不抛异常，关键字段可见
# =========================================================================== #
def test_d5_04_full_payload_renders_all_sections():
    """完整 payload → 计划/仓库/透明化/决策全渲染，selected_repo 高亮 ✅，不抛异常。"""
    controller = _make_controller_mock(payload=_make_payload())
    at = _run(controller)
    assert not at.exception
    text = _collect_text(at)
    assert "## 复现计划" in text
    assert "复现 HippoRAG 检索增强方法" in text  # plan_summary
    assert "候选代码仓库" in text
    # selected_repo 高亮：✅ 出现在 expander label（非 markdown 文本流）
    labels = " ".join(getattr(e, "label", "") for e in getattr(at, "expander", []))
    assert "✅" in labels
    assert "透明化信息" in text
    assert "决策" in text
    # 五个决策按钮齐全
    for key in ("btn_approve", "btn_code_only", "btn_revise", "btn_switch_repo", "btn_cancel"):
        assert at.button(key=key)


# =========================================================================== #
# T-D5-05：残缺 payload（字段缺失）→ 防御式 .get 不抛 KeyError
# =========================================================================== #
def test_d5_05_partial_payload_no_keyerror():
    """残缺 payload（仅给最小键、子结构缺失）→ 防御式 .get 兜底，不抛 KeyError/异常。"""
    # 极简：reproduction_plan / resource_info 为空，无 paper_analysis_summary 等
    partial = {"reproduction_plan": {}, "resource_info": {}}
    controller = _make_controller_mock(payload=partial)
    at = _run(controller)
    assert not at.exception, at.exception
    text = _collect_text(at)
    # 仍渲染骨架标题，仓库为空时给「未发现候选仓库」
    assert "## 复现计划" in text
    assert "未发现候选仓库" in text


# =========================================================================== #
# T-D5-06：approve 决策 → controller.resume_with(tid, {"decision":"approve"})
# =========================================================================== #
def test_d5_06_approve_decision_payload():
    """点「批准计划」→ resume_with(thread_id, {"decision":"approve"})。"""
    controller = _make_controller_mock(payload=_make_payload())
    with patch("app._get_controller", return_value=controller):
        at = AppTest.from_string(_APP_SCRIPT)
        at.run()
        at.button(key="btn_approve").click().run()
    assert not at.exception
    controller.resume_with.assert_called_once_with(
        "task-review-001", {"decision": "approve"}
    )
    controller.cancel_task.assert_not_called()


# =========================================================================== #
# T-D5-07：code_only 决策 → resume_with(tid, {"decision":"code_only"})
# =========================================================================== #
def test_d5_07_code_only_decision_payload():
    """点「仅复现代码」→ resume_with(thread_id, {"decision":"code_only"})。"""
    controller = _make_controller_mock(payload=_make_payload())
    with patch("app._get_controller", return_value=controller):
        at = AppTest.from_string(_APP_SCRIPT)
        at.run()
        at.button(key="btn_code_only").click().run()
    assert not at.exception
    controller.resume_with.assert_called_once_with(
        "task-review-001", {"decision": "code_only"}
    )


# =========================================================================== #
# T-D5-08：revise 决策 → resume_with(tid, {"decision":"revise","user_feedback":<text>})
# =========================================================================== #
def test_d5_08_revise_decision_carries_user_feedback():
    """填修改意见后点「提交修改」→ resume_with 带 decision=revise + user_feedback。"""
    controller = _make_controller_mock(payload=_make_payload())
    fb = "请把数据集换成 2WikiMultiHopQA"
    with patch("app._get_controller", return_value=controller):
        at = AppTest.from_string(_APP_SCRIPT)
        at.run()
        at.text_area(key="_review_revise_feedback").set_value(fb).run()
        at.button(key="btn_revise").click().run()
    assert not at.exception
    controller.resume_with.assert_called_once_with(
        "task-review-001", {"decision": "revise", "user_feedback": fb}
    )


# =========================================================================== #
# T-D5-09：switch_repo 决策 → resume_with 带 user_feedback + new_repo_url
# =========================================================================== #
def test_d5_09_switch_repo_decision_carries_feedback_and_url():
    """填更换原因 + 新仓库 URL 后点「提交更换」→ resume_with 带三字段。"""
    controller = _make_controller_mock(payload=_make_payload())
    reason = "官方仓库缺训练脚本"
    new_url = "https://github.com/alt/HippoRAG-repro"
    with patch("app._get_controller", return_value=controller):
        at = AppTest.from_string(_APP_SCRIPT)
        at.run()
        at.text_area(key="_review_switch_feedback").set_value(reason).run()
        at.text_input(key="_review_switch_repo_url").set_value(new_url).run()
        at.button(key="btn_switch_repo").click().run()
    assert not at.exception
    controller.resume_with.assert_called_once_with(
        "task-review-001",
        {"decision": "switch_repo", "user_feedback": reason, "new_repo_url": new_url},
    )


# =========================================================================== #
# T-D5-10：cancel 二次确认——首次点击只置确认标记，不调 cancel_task
# =========================================================================== #
def test_d5_10_cancel_first_click_sets_flag_only():
    """首次点「终止任务」→ 仅置 _review_confirm_cancel=True，不调 cancel_task，出现确认文案。"""
    controller = _make_controller_mock(payload=_make_payload())
    with patch("app._get_controller", return_value=controller):
        at = AppTest.from_string(_APP_SCRIPT)
        at.run()
        at.button(key="btn_cancel").click().run()
    assert not at.exception
    controller.cancel_task.assert_not_called()
    assert at.session_state["_review_confirm_cancel"] is True
    assert "确认终止" in _collect_text(at)


# =========================================================================== #
# T-D5-11：cancel 二次确认——确认后才调 cancel_task(thread_id)
# =========================================================================== #
def test_d5_11_cancel_confirm_calls_cancel_task():
    """二次点击「确认终止」→ controller.cancel_task(thread_id)，清标记并切 progress 页。"""
    controller = _make_controller_mock(payload=_make_payload())
    with patch("app._get_controller", return_value=controller):
        at = AppTest.from_string(_APP_SCRIPT)
        at.run()
        at.button(key="btn_cancel").click().run()        # 首次：置标记
        controller.cancel_task.assert_not_called()
        at.button(key="btn_cancel_confirm").click().run()  # 二次：确认
    assert not at.exception
    controller.cancel_task.assert_called_once_with("task-review-001")
    assert at.session_state["_review_confirm_cancel"] is False
    assert at.session_state["current_page"] == "progress"


# =========================================================================== #
# T-D5-12：revise_count >= soft_hint_threshold → 显示软提示
# =========================================================================== #
def test_d5_12_soft_hint_shown_at_threshold():
    """revise_count==soft_hint_threshold(5) → 透明化区出现软提示 warning。"""
    controller = _make_controller_mock(
        payload=_make_payload(revise_count=5, soft_hint_threshold=5)
    )
    at = _run(controller)
    assert not at.exception
    text = _collect_text(at)
    assert "建议考虑直接批准或取消" in text  # 软提示文案


def test_d5_12b_soft_hint_absent_below_threshold():
    """revise_count < threshold → 不显示软提示（边界对照）。"""
    controller = _make_controller_mock(
        payload=_make_payload(revise_count=2, soft_hint_threshold=5)
    )
    at = _run(controller)
    assert not at.exception
    assert "建议考虑直接批准或取消" not in _collect_text(at)


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
