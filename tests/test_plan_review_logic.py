"""plan_review 页「逻辑单测」（新范式：不起浏览器、不点 iframe 按钮）。

背景
====
ui/pages/plan_review.py 已全量迁到 streamlit-shadcn-ui（组件渲染在 iframe 里），
``streamlit.testing.v1.AppTest`` 看不到 iframe 组件、点击不回写 session_state，
故「点击 shadcn 按钮」类用例已迁到 tests/test_plan_review_e2e.py（Playwright）。

本文件只保留**不需点击**即可断言的逻辑——它们断言的是 markdown/info/warning 文本
和 controller mock 的调用次数，AppTest 仍可可靠测：

- 可导入：render 存在且 callable，别名/__all__ 约定
- 无 thread_id → 兜底「尚未启动任务」并 return，不触达 controller
- payload=None → 「计划尚未就绪」并 return
- 残缺 / partial payload → 防御式 .get 不抛 KeyError
- 软提示阈值行为：revise_count>=threshold 出软提示、低于不出

运行::

    .venv/bin/python -m pytest tests/test_plan_review_logic.py -v
"""

from __future__ import annotations

import importlib
from typing import Dict, List, Optional
from unittest.mock import MagicMock, patch

from streamlit.testing.v1 import AppTest


# --------------------------------------------------------------------------- #
# AppTest 脚本：顶层预置 thread_id（模拟 D4 跳转后进入 review）。
# 本文件不点击任何按钮，故无需路由 stub。
# --------------------------------------------------------------------------- #
_APP_SCRIPT = """
import streamlit as st
st.session_state.setdefault("thread_id", "task-review-001")
st.session_state.setdefault("current_page", "review")
from ui.pages.plan_review import render
render()
"""

# 无 thread_id 脚本（不预置 thread_id → 走 render 的 no-thread 兜底守卫）。
_APP_SCRIPT_NO_THREAD = """
from ui.pages.plan_review import render
render()
"""


def _make_payload(
    revise_count: int = 0,
    soft_hint_threshold: int = 5,
) -> Dict:
    """构造一份完整可用的 interrupt payload（plan_review 页消费的全部字段）。"""
    return {
        "reproduction_plan": {
            "plan_summary": "复现 HippoRAG 检索增强方法",
            "environment": {"python": "3.11", "cuda": "12.1"},
            "data_preparation": ["下载 MuSiQue 数据集", "构建知识图谱"],
            "code_strategy": "use_repo",
            "execution_steps": [
                {"step_name": "建图", "command": "python build.py",
                 "expected_output": "graph.pkl"},
            ],
            "expected_results": {"recall@5": 0.89},
            "estimated_time": "约 2 小时",
            "deliverables": ["复现报告"],
        },
        "resource_info": {
            "repos": [
                {"url": "https://github.com/OSU-NLP-Group/HippoRAG",
                 "source": "github", "is_official": True, "stars": 1200,
                 "forks": 90, "quality_score": 0.95},
            ],
            "selected_repo": {"url": "https://github.com/OSU-NLP-Group/HippoRAG"},
            "resource_strategy": "use_official",
        },
        "paper_analysis_summary": {"method_summary": "基于个性化 PageRank 的检索"},
        "degraded_nodes": [],
        "node_errors": [],
        "revise_count": revise_count,
        "soft_hint_threshold": soft_hint_threshold,
        "max_total_llm_calls": 50,
    }


def _make_controller_mock(payload: Optional[Dict]) -> MagicMock:
    """构造 GraphController mock：脚本化 get_interrupt_payload，其余为桩。"""
    controller = MagicMock()
    controller.get_interrupt_payload.return_value = payload
    return controller


def _run(controller: MagicMock, script: str = _APP_SCRIPT) -> AppTest:
    """patch app._get_controller（页面 from app import _get_controller），跑一次 AppTest。"""
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
# T-01：入口可导入（importlib，避免 __init__ 遮蔽子模块）
# =========================================================================== #
def test_importable():
    """render 可导入且 callable + 与别名 render_plan_review_page 同对象 + __all__ 约定。"""
    mod = importlib.import_module("ui.pages.plan_review")
    assert callable(mod.render)
    assert mod.render_plan_review_page is mod.render
    assert mod.__all__ == ["render", "render_plan_review_page"]


# =========================================================================== #
# T-02：无 thread_id → 兜底「尚未启动任务」并 return，不崩、不调 controller
# =========================================================================== #
def test_no_thread_id_fallback():
    """无 thread_id → 兜底提示并 return，不崩、不触达 get_interrupt_payload。"""
    controller = _make_controller_mock(payload=None)
    at = _run(controller, script=_APP_SCRIPT_NO_THREAD)
    assert not at.exception
    assert "尚未启动任务" in _collect_text(at)
    # 兜底分支在取 controller 之前 return → 不应调任何 controller 方法
    controller.get_interrupt_payload.assert_not_called()


# =========================================================================== #
# T-03：payload=None → 「计划尚未就绪」并 return，不渲染后续区块
# =========================================================================== #
def test_payload_none_not_ready():
    """payload=None → 显示「计划尚未就绪」并 return，不渲染计划/仓库区块。"""
    controller = _make_controller_mock(payload=None)
    at = _run(controller)
    assert not at.exception
    text = _collect_text(at)
    assert "计划尚未就绪" in text
    # return 在渲染之前：不应出现计划标题
    assert "📋 复现计划" not in text


# =========================================================================== #
# T-04：残缺 payload（字段缺失）→ 防御式 .get 不抛 KeyError/异常
# =========================================================================== #
def test_partial_payload_no_keyerror():
    """partial payload（子结构为空）→ 防御式 .get 兜底，不抛 KeyError/异常。"""
    partial = {"reproduction_plan": {}, "resource_info": {}}
    controller = _make_controller_mock(payload=partial)
    at = _run(controller)
    assert not at.exception, at.exception
    text = _collect_text(at)
    # 仍渲染骨架标题（h3 ### 📋 复现计划），仓库为空时给「未发现候选仓库」
    assert "📋 复现计划" in text
    assert "未发现候选仓库" in text


def test_empty_payload_dict_no_keyerror():
    """彻底空 dict（连 reproduction_plan/resource_info 键都没有）→ 不抛 KeyError。"""
    controller = _make_controller_mock(payload={})
    at = _run(controller)
    assert not at.exception, at.exception
    assert "未发现候选仓库" in _collect_text(at)


# =========================================================================== #
# T-05：软提示阈值行为（revise_count>=threshold 出提示、低于不出）
# =========================================================================== #
def test_soft_hint_shown_at_threshold():
    """revise_count == soft_hint_threshold(5) → 透明化区出现软提示 warning。"""
    controller = _make_controller_mock(
        payload=_make_payload(revise_count=5, soft_hint_threshold=5)
    )
    at = _run(controller)
    assert not at.exception
    assert "建议考虑直接批准或取消" in _collect_text(at)


def test_soft_hint_shown_above_threshold():
    """revise_count > threshold → 同样出软提示（>= 边界上侧）。"""
    controller = _make_controller_mock(
        payload=_make_payload(revise_count=7, soft_hint_threshold=5)
    )
    at = _run(controller)
    assert not at.exception
    assert "建议考虑直接批准或取消" in _collect_text(at)


def test_soft_hint_absent_below_threshold():
    """revise_count < threshold → 不显示软提示（边界对照）。"""
    controller = _make_controller_mock(
        payload=_make_payload(revise_count=2, soft_hint_threshold=5)
    )
    at = _run(controller)
    assert not at.exception
    assert "建议考虑直接批准或取消" not in _collect_text(at)


# =========================================================================== #
# T-06：抖动修复回归保护——3 个反馈/URL 输入框已从 shadcn ui.*（iframe，AppTest
#       不可见）迁回原生 st.text_area/st.text_input（主文档，AppTest 可见可驱动）。
#       D5 迁移注释曾断言「AppTest 看不到 iframe 组件」；原生化后本用例真实驱动
#       这些 widget，既是抖动修复（单源治理：仅 key、无 default_value 双源）的结构性
#       旁证，也是防止后续回退到 shadcn 双源反模式的回归护栏。键名一个都不能改，
#       否则 session_state 流转与下游 resume_with 取值会断。
# =========================================================================== #
def test_feedback_widgets_are_native_and_appvisible():
    """3 个输入框原生化后对 AppTest 可见 + 键名严格不变（抖动修复结构性旁证）。

    D5 迁 shadcn 时这些框渲染在 iframe，AppTest 不可见（迁移注释 L5-7 明确断言）；
    本次单源治理迁回原生 st.text_area/st.text_input 后，它们出现在主文档元素树，
    AppTest 即可查询到。本用例断言「可见 + 键名快照」，是防止后续回退到 shadcn
    双源反模式（抖动源）的回归护栏；至于「填值→点提交→断言 resume_with payload」
    的完整链路，因 AppTest 不维护 expander 展开状态、二次 run 后 expander 内 widget
    会从查询树消失（AppTest 框架对 expander 的已知限制），仍归 Playwright e2e
    （tests/test_plan_review_e2e.py::test_e2e_revise_carries_feedback 等）。
    """
    controller = _make_controller_mock(payload=_make_payload())
    at = _run(controller)
    assert not at.exception, at.exception

    # 键名快照：迁原生后 widget 必须仍按这三个 key 暴露（AppTest 与下游取值依赖）。
    # 任何键名变动都会断掉 session_state 流转与 resume_with 取值——此处守住。
    ta_keys = {ta.key for ta in at.text_area}
    ti_keys = {ti.key for ti in at.text_input}
    assert "_review_revise_feedback" in ta_keys, (
        "revise 反馈框应为原生 st.text_area 且键名不变（shadcn iframe 时 AppTest 不可见）"
    )
    assert "_review_switch_feedback" in ta_keys, (
        "switch 反馈框应为原生 st.text_area 且键名不变"
    )
    assert "_review_switch_repo_url" in ti_keys, (
        "switch 仓库 URL 框应为原生 st.text_input 且键名不变"
    )
