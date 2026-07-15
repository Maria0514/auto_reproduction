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
        "max_total_llm_calls": 120,
    }


# S2-12：新 render() 会调 controller.poll_state(tid) 取 planning 节点 llm_config_set
# 供对话面板构造模型。mock 必须给 poll_state 返回含合法 llm_config_set 的 dict。
_LLM_CONFIG = {
    "base_url": "https://example.test/v1", "model": "gpt-test",
    "api_key": "", "temperature": 0.3, "max_tokens": 4096,
}
_LLM_CONFIG_SET = {"default": _LLM_CONFIG, "overrides": {}}


def _make_controller_mock(payload: Optional[Dict]) -> MagicMock:
    """构造 GraphController mock：脚本化 get_interrupt_payload / poll_state，其余为桩。"""
    controller = MagicMock()
    controller.get_interrupt_payload.return_value = payload
    # poll_state 返回含合法 llm_config_set 的 state（新 render() 依赖）。
    controller.poll_state.return_value = {"llm_config_set": _LLM_CONFIG_SET}
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
# T-06：S2-12 后 revise 交互形态——一次性「修改计划」textarea 已替换为多轮对话面板
#       （st.chat_input + st.chat_message + 原生按钮，AppTest 可见）。switch_repo 两个
#       原生输入框（feedback / url）保留。本用例断言：
#       1. 对话输入框 at.chat_input 非空（对话面板已挂载）；
#       2. switch_repo 两个原生输入框键名不变（resume_with 取值依赖，不能改）；
#       3. 旧 revise textarea 键名 _review_revise_feedback 已彻底删除（防回退）。
# =========================================================================== #
def test_feedback_widgets_are_native_and_appvisible():
    """S2-12：对话面板 chat_input 可见 + switch_repo 键名快照 + 旧 revise 框已删。"""
    controller = _make_controller_mock(payload=_make_payload())
    at = _run(controller)
    assert not at.exception, at.exception

    # 1. 对话面板已挂载：st.chat_input 非空（AppTest 可见，避开 shadcn iframe 坑）。
    assert len(at.chat_input) >= 1, "S2-12 对话面板的 st.chat_input 应可见且非空"

    # 2. switch_repo 两个原生输入框键名不变（下游 resume_with 取值依赖）。
    ta_keys = {ta.key for ta in at.text_area}
    ti_keys = {ti.key for ti in at.text_input}
    assert "_review_switch_feedback" in ta_keys, (
        "switch 反馈框应为原生 st.text_area 且键名不变"
    )
    assert "_review_switch_repo_url" in ti_keys, (
        "switch 仓库 URL 框应为原生 st.text_input 且键名不变"
    )

    # 3. 旧 revise 一次性 textarea 已彻底删除（防回退到一次性提交反模式）。
    assert "_review_revise_feedback" not in ta_keys, (
        "S2-12 已删除一次性 revise textarea，不应再出现 _review_revise_feedback 键"
    )


# =========================================================================== #
# _await_phase / _safe_int 纯函数直测（决策提交后"等待图推进"状态机）
#
# 背景：resume_with 异步起后台线程，本页若不轮询会停在静态页"没动静"；且切页瞬间
# 旧 interrupt 常未消费，直接按 is_interrupted 路由会误弹。_await_phase 用 revise_count
# 基线（修改类）/ interrupt 是否消费（批准/取消类）判定何时、往哪儿路由。
# =========================================================================== #
def _phase(**kw):
    mod = importlib.import_module("ui.pages.plan_review")
    base = dict(kind="revise", payload=None, baseline=0,
                has_worker_error=False, is_interrupted=False)
    base.update(kw)
    return mod._await_phase(**base)


def test_safe_int_tolerant():
    mod = importlib.import_module("ui.pages.plan_review")
    assert mod._safe_int(3) == 3
    assert mod._safe_int("2") == 2
    assert mod._safe_int(None, default=0) == 0
    assert mod._safe_int("x", default=-1) == -1
    assert mod._safe_int({}, default=0) == 0


def test_await_phase_worker_error_overrides_all():
    # worker 崩 → error，无论哪种 kind / 是否 interrupted。
    assert _phase(kind="revise", has_worker_error=True) == "error"
    assert _phase(kind="approve", has_worker_error=True, is_interrupted=True) == "error"


def test_await_phase_revise_waits_until_revise_count_advances():
    # 提交瞬间：payload 仍是旧 interrupt（revise_count==baseline）→ 必须 waiting，不误判。
    assert _phase(kind="revise", payload={"revise_count": 0}, baseline=0) == "waiting"
    # 重规划中：payload 暂为 None → waiting。
    assert _phase(kind="revise", payload=None, baseline=0) == "waiting"
    # 新计划生成：revise_count 前进 → to_review。
    assert _phase(kind="revise", payload={"revise_count": 1}, baseline=0) == "to_review"
    # switch_repo 同理（baseline 非 0）。
    assert _phase(kind="switch_repo", payload={"revise_count": 2}, baseline=1) == "to_review"
    assert _phase(kind="switch_repo", payload={"revise_count": 1}, baseline=1) == "waiting"
    # revise_count 缺失/非数 → _safe_int 兜底 -1，不会误判 to_review。
    assert _phase(kind="revise", payload={}, baseline=0) == "waiting"
    assert _phase(kind="revise", payload={"revise_count": "x"}, baseline=0) == "waiting"


def test_await_phase_approve_cancel_wait_until_interrupt_consumed():
    # 批准/仅代码/取消：旧 interrupt 未消费（is_interrupted True，含切页瞬间的残留）→ waiting。
    for kind in ("approve", "code_only", "cancel"):
        assert _phase(kind=kind, is_interrupted=True) == "waiting"
        # interrupt 已消费（图离开 planning 暂停）→ 去 progress。
        assert _phase(kind=kind, is_interrupted=False) == "to_progress"
    # 这些 kind 不看 revise_count（即便 payload 还在也不返回 to_review）。
    assert _phase(kind="approve", payload={"revise_count": 9}, baseline=0,
                  is_interrupted=True) == "waiting"


# =========================================================================== #
# S2-12：与规划模型多轮对话敲定修改方向 —— 纯函数直测 + AppTest 行为断言
# =========================================================================== #
def _plan_review_mod():
    """用 importlib 取模块（避免 __init__ 显式 export 遮蔽子模块的已知坑）。"""
    return importlib.import_module("ui.pages.plan_review")


# --- 纯函数：_format_plan_context（满 / 空 / partial payload 不抛）------------- #
def test_format_plan_context_full_payload():
    """完整 payload → 返回字符串含三类 grounding 字段名，可被 json 解析。"""
    import json as _json

    mod = _plan_review_mod()
    text = mod._format_plan_context(_make_payload())
    assert isinstance(text, str)
    parsed = _json.loads(text)
    assert set(parsed.keys()) == {
        "reproduction_plan", "paper_analysis_summary", "resource_info"
    }
    # grounding 子串：计划摘要 / 候选仓库 URL 应出现在序列化文本里。
    assert "复现 HippoRAG" in text
    assert "HippoRAG" in text


def test_format_plan_context_none_and_partial_no_raise():
    """None / 空 dict / partial payload 均不抛，且键齐全（防御式 .get）。"""
    import json as _json

    mod = _plan_review_mod()
    for payload in (None, {}, {"reproduction_plan": {"plan_summary": "x"}}):
        text = mod._format_plan_context(payload)
        parsed = _json.loads(text)
        assert set(parsed.keys()) == {
            "reproduction_plan", "paper_analysis_summary", "resource_info"
        }


def test_format_plan_context_stable_for_same_payload():
    """同一 payload 两次渲染字节级一致（sort_keys 保证，便于直测与缓存友好）。"""
    mod = _plan_review_mod()
    p = _make_payload()
    assert mod._format_plan_context(p) == mod._format_plan_context(p)


# --- 纯函数：_build_chat_system_prompt（含边界语 + grounding 子串）------------- #
def test_build_chat_system_prompt_has_boundary_and_grounding():
    """system prompt 含明确边界语（不要现在就重写完整计划/输出大段 JSON）+ grounding 子串。"""
    mod = _plan_review_mod()
    sp = mod._build_chat_system_prompt(_make_payload())
    # 角色
    assert "讨论助手" in sp
    # 边界语（硬约束：对话不直接落计划）
    assert "不要" in sp and ("完整复现计划" in sp or "完整计划" in sp)
    assert "JSON" in sp or "代码" in sp
    # grounding 注入：计划上下文段落 + 实际计划内容子串
    assert "当前计划上下文" in sp
    assert "复现 HippoRAG" in sp


# --- 纯函数：_build_chat_messages（首条 SystemMessage + role↔类型）------------- #
def test_build_chat_messages_shape_and_roles():
    """首条 SystemMessage；历史 role==assistant→AIMessage、其余→HumanMessage。"""
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

    mod = _plan_review_mod()
    history = [
        {"role": "user", "content": "把数据集换掉"},
        {"role": "assistant", "content": "好的，换成哪个？"},
        {"role": "user", "content": "2WikiMultiHopQA"},
    ]
    msgs = mod._build_chat_messages(_make_payload(), history)
    assert isinstance(msgs[0], SystemMessage)
    assert isinstance(msgs[1], HumanMessage) and msgs[1].content == "把数据集换掉"
    assert isinstance(msgs[2], AIMessage) and msgs[2].content == "好的，换成哪个？"
    assert isinstance(msgs[3], HumanMessage)
    assert len(msgs) == 4


def test_build_chat_messages_empty_history():
    """空历史 → 仅一条 SystemMessage。"""
    from langchain_core.messages import SystemMessage

    mod = _plan_review_mod()
    msgs = mod._build_chat_messages(_make_payload(), [])
    assert len(msgs) == 1 and isinstance(msgs[0], SystemMessage)


# --- 纯函数：_build_summary_messages（形态：System + 历史 + 末条 Human 指令）---- #
def test_build_summary_messages_shape():
    """总结消息：首条 SystemMessage、末条 HumanMessage（含「修改方向纪要」总结指令）。"""
    from langchain_core.messages import HumanMessage, SystemMessage

    mod = _plan_review_mod()
    history = [
        {"role": "user", "content": "把数据集换成 2WikiMultiHopQA"},
        {"role": "assistant", "content": "明白"},
    ]
    msgs = mod._build_summary_messages(_make_payload(), history)
    assert isinstance(msgs[0], SystemMessage)
    assert isinstance(msgs[-1], HumanMessage)
    assert "修改方向纪要" in msgs[-1].content
    # 历史在中间被携带（System + 2 历史 + 1 指令 = 4）。
    assert len(msgs) == 4


# --- 纯函数：_sync_chat_thread（变 / 不变）------------------------------------- #
def test_sync_chat_thread_clears_on_change():
    """thread 变更 → 清空对话历史与计数；不变 → 保留。"""
    import streamlit as st

    mod = _plan_review_mod()
    # 预置一段历史 + 计数，绑定 thread A
    st.session_state["_review_chat_messages"] = [{"role": "user", "content": "x"}]
    st.session_state["_review_chat_calls"] = 3
    st.session_state["_review_chat_thread"] = "A"

    # 同 thread → 不清空
    mod._sync_chat_thread("A")
    assert st.session_state["_review_chat_messages"] == [{"role": "user", "content": "x"}]
    assert st.session_state["_review_chat_calls"] == 3

    # 切到 thread B → 清空
    mod._sync_chat_thread("B")
    assert st.session_state["_review_chat_messages"] == []
    assert st.session_state["_review_chat_calls"] == 0
    assert st.session_state["_review_chat_thread"] == "B"


# --- 副作用：_handle_chat_turn（patch llm，成功 append / 失败不污染）----------- #
def test_handle_chat_turn_success_appends():
    """成功路径：append user + assistant，计数 +1。"""
    import streamlit as st

    mod = _plan_review_mod()
    st.session_state["_review_chat_messages"] = []
    st.session_state["_review_chat_calls"] = 0

    fake_llm = MagicMock()
    fake_resp = MagicMock()
    fake_resp.content = "我建议把数据集换成 2WikiMultiHopQA"
    fake_llm.invoke.return_value = fake_resp

    with patch.object(mod, "_build_planning_chat_llm", return_value=fake_llm):
        mod._handle_chat_turn("换数据集", _make_payload(), _LLM_CONFIG_SET)

    hist = st.session_state["_review_chat_messages"]
    assert hist[0] == {"role": "user", "content": "换数据集"}
    assert hist[1]["role"] == "assistant"
    assert "2WikiMultiHopQA" in hist[1]["content"]
    assert st.session_state["_review_chat_calls"] == 1


def test_handle_chat_turn_failure_no_pollution():
    """失败路径（invoke 抛错）：保留 user 输入、不追加坏 assistant、计数不变、不崩。"""
    import streamlit as st

    mod = _plan_review_mod()
    st.session_state["_review_chat_messages"] = []
    st.session_state["_review_chat_calls"] = 0

    fake_llm = MagicMock()
    fake_llm.invoke.side_effect = RuntimeError("LLM down")

    with patch.object(mod, "_build_planning_chat_llm", return_value=fake_llm), \
            patch.object(mod.st, "error") as mock_error:
        mod._handle_chat_turn("换数据集", _make_payload(), _LLM_CONFIG_SET)

    hist = st.session_state["_review_chat_messages"]
    # user 输入仍在（可重试），但无 assistant 追加，计数不变。
    assert hist == [{"role": "user", "content": "换数据集"}]
    assert st.session_state["_review_chat_calls"] == 0
    # 降级文案已展示（含「下一步」指引）。
    assert mock_error.called


def test_handle_chat_turn_empty_input_noop():
    """空白输入 → 直接 return，不调 llm、不追加。"""
    import streamlit as st

    mod = _plan_review_mod()
    st.session_state["_review_chat_messages"] = []
    with patch.object(mod, "_build_planning_chat_llm") as mock_build:
        mod._handle_chat_turn("   ", _make_payload(), _LLM_CONFIG_SET)
    assert st.session_state["_review_chat_messages"] == []
    mock_build.assert_not_called()


# --- 副作用：_apply_chat_revision（patch llm，断言 resume_with + 清空 + awaiting）- #
def test_apply_chat_revision_resumes_with_summary():
    """敲定方向：模型产出纪要 → resume_with({"decision":"revise","user_feedback":纪要}) 一次
    + 历史清空 + _KEY_AWAITING 置 True。"""
    import streamlit as st

    mod = _plan_review_mod()
    st.session_state["_review_chat_messages"] = [
        {"role": "user", "content": "把数据集换成 2WikiMultiHopQA"},
        {"role": "assistant", "content": "好的"},
    ]
    st.session_state["_review_chat_calls"] = 1
    st.session_state["_review_awaiting"] = False

    summary_text = "修改方向：将数据集从 MuSiQue 更换为 2WikiMultiHopQA，并同步调整建图步骤。"
    fake_llm = MagicMock()
    fake_resp = MagicMock()
    fake_resp.content = summary_text
    fake_llm.invoke.return_value = fake_resp

    controller = MagicMock()
    with patch.object(mod, "_build_planning_chat_llm", return_value=fake_llm), \
            patch.object(mod.st, "rerun"):
        mod._apply_chat_revision(controller, "tid-1", _make_payload(), _LLM_CONFIG_SET)

    # resume_with 恰好一次，payload 为 revise + 模型纪要。
    controller.resume_with.assert_called_once_with(
        "tid-1", {"decision": "revise", "user_feedback": summary_text}
    )
    # 历史清空 + awaiting 置 True。
    assert st.session_state["_review_chat_messages"] == []
    assert st.session_state["_review_chat_calls"] == 0
    assert st.session_state["_review_awaiting"] is True
    assert st.session_state["_review_await_kind"] == "revise"


def test_apply_chat_revision_summary_failure_falls_back_to_concat():
    """总结失败 → 退化用拼接用户发言作 user_feedback，仍 resume_with 一次、不崩。"""
    import streamlit as st

    mod = _plan_review_mod()
    st.session_state["_review_chat_messages"] = [
        {"role": "user", "content": "第一条意见"},
        {"role": "assistant", "content": "好的"},
        {"role": "user", "content": "第二条意见"},
    ]
    st.session_state["_review_awaiting"] = False

    fake_llm = MagicMock()
    fake_llm.invoke.side_effect = RuntimeError("summary boom")

    controller = MagicMock()
    with patch.object(mod, "_build_planning_chat_llm", return_value=fake_llm), \
            patch.object(mod.st, "rerun"):
        mod._apply_chat_revision(controller, "tid-2", _make_payload(), _LLM_CONFIG_SET)

    controller.resume_with.assert_called_once()
    args, _ = controller.resume_with.call_args
    assert args[0] == "tid-2"
    decision = args[1]
    assert decision["decision"] == "revise"
    # 退化拼接了两条用户发言。
    assert "第一条意见" in decision["user_feedback"]
    assert "第二条意见" in decision["user_feedback"]
    assert st.session_state["_review_awaiting"] is True


# --- AppTest：对话面板「确定方案」按钮空对话 disabled、有历史 enabled ----------- #
_APP_SCRIPT_CHAT = """
import streamlit as st
st.session_state.setdefault("thread_id", "task-review-chat")
st.session_state.setdefault("current_page", "review")
from ui.pages.plan_review import render
render()
"""


def test_apply_button_disabled_on_empty_chat():
    """空对话 → 「确定方案并重新生成计划」按钮 disabled is True。"""
    controller = _make_controller_mock(payload=_make_payload())
    with patch("app._get_controller", return_value=controller):
        at = AppTest.from_string(_APP_SCRIPT_CHAT)
        at.run()
    assert not at.exception, at.exception
    apply_btns = [b for b in at.button if b.key == "btn_apply_chat_revision"]
    assert len(apply_btns) == 1, "应渲染「确定方案并重新生成计划」按钮"
    assert apply_btns[0].disabled is True, "空对话时该按钮必须 disabled"


def test_apply_button_enabled_with_history():
    """预置对话历史 → 按钮 enabled（disabled is False）。"""
    script = """
import streamlit as st
st.session_state.setdefault("thread_id", "task-review-chat")
st.session_state.setdefault("current_page", "review")
st.session_state["_review_chat_messages"] = [
    {"role": "user", "content": "换数据集"},
    {"role": "assistant", "content": "好的"},
]
st.session_state["_review_chat_thread"] = "task-review-chat"
from ui.pages.plan_review import render
render()
"""
    controller = _make_controller_mock(payload=_make_payload())
    with patch("app._get_controller", return_value=controller):
        at = AppTest.from_string(script)
        at.run()
    assert not at.exception, at.exception
    apply_btns = [b for b in at.button if b.key == "btn_apply_chat_revision"]
    assert len(apply_btns) == 1
    assert apply_btns[0].disabled is False, "预置对话历史后该按钮应 enabled"


def test_chat_calls_shown_in_info_bar():
    """info-bar 增列「本轮对话已消耗 X 次调用」（session 计数器）。"""
    script = """
import streamlit as st
st.session_state.setdefault("thread_id", "task-review-chat")
st.session_state.setdefault("current_page", "review")
st.session_state["_review_chat_thread"] = "task-review-chat"
st.session_state["_review_chat_calls"] = 2
from ui.pages.plan_review import render
render()
"""
    controller = _make_controller_mock(payload=_make_payload())
    with patch("app._get_controller", return_value=controller):
        at = AppTest.from_string(script)
        at.run()
    assert not at.exception, at.exception
    text = _collect_text(at)
    assert "本轮对话已消耗 2 次调用" in text


# =========================================================================== #
# S2-12 验收补强（测试工程师独立验收 2026-06-11）——针对开发可能漏的角，
# 全部「先 python 探针验证实现真行为，再写断言」（与本项目既有范式一致）。
#
# 覆盖缺口（现有用例未触达）：
# - _history_to_messages 异常输入鲁棒性（非 dict / 缺 role / content None）；
# - _handle_chat_turn 助手 content 非字符串时的 str() 兜底；
# - _apply_chat_revision 总结返回「空白串（非异常）」时的退化拼接；
# - _apply_chat_revision 完全空对话时不落定 + st.error（按钮 disabled 后的纵深兜底）；
# - AC-S2-17 强化：多轮对话期间 controller 零写（resume_with / cancel_task 均不调）；
# - AC-S2-18 强化：兜底输入框走 revise 的逻辑层断言（AppTest 原生 widget，不依赖浏览器）；
# - N≥5 chat_calls 软提示触发 / N=4 不触发对照；info-bar 同列 revise_count 与 chat_calls。
# =========================================================================== #


# --- _history_to_messages 异常输入鲁棒性（探针实证：非 dict 项被跳过）---------- #
def test_history_to_messages_robust_against_malformed_items():
    """非 dict 项跳过；缺 role / 未知 role → HumanMessage；content=None → 空串，不抛。"""
    from langchain_core.messages import AIMessage, HumanMessage

    mod = _plan_review_mod()
    history = [
        {"role": "assistant", "content": "答"},
        {"role": "unknown_role", "content": "未知角色"},  # 非 user/assistant
        {"content": "缺 role"},                           # 缺 role 键
        "not-a-dict",                                      # 非 dict 项（应被跳过）
        {"role": "user", "content": None},                 # content None → 空串
    ]
    msgs = mod._history_to_messages(history)
    # 非 dict 项被跳过 → 共 4 条
    assert len(msgs) == 4
    assert isinstance(msgs[0], AIMessage) and msgs[0].content == "答"
    # 未知 role / 缺 role 均归 HumanMessage
    assert isinstance(msgs[1], HumanMessage) and msgs[1].content == "未知角色"
    assert isinstance(msgs[2], HumanMessage) and msgs[2].content == "缺 role"
    # content None → 空串（str(... or "")）
    assert isinstance(msgs[3], HumanMessage) and msgs[3].content == ""


def test_history_to_messages_none_and_empty():
    """None / 空列表 → 空消息序列，不抛。"""
    mod = _plan_review_mod()
    assert mod._history_to_messages(None) == []
    assert mod._history_to_messages([]) == []


# --- _handle_chat_turn 助手 content 非字符串兜底（探针实证：int → str）-------- #
def test_handle_chat_turn_non_str_content_coerced():
    """模型返回 content 为非字符串（如 int）→ str() 兜底为字符串，append 不抛。"""
    import streamlit as st

    mod = _plan_review_mod()
    st.session_state["_review_chat_messages"] = []
    st.session_state["_review_chat_calls"] = 0

    fake_llm = MagicMock()
    fake_resp = MagicMock()
    fake_resp.content = 12345  # 非字符串
    fake_llm.invoke.return_value = fake_resp

    with patch.object(mod, "_build_planning_chat_llm", return_value=fake_llm):
        mod._handle_chat_turn("hi", _make_payload(), _LLM_CONFIG_SET)

    hist = st.session_state["_review_chat_messages"]
    assert hist[1]["role"] == "assistant"
    assert isinstance(hist[1]["content"], str)
    assert hist[1]["content"] == "12345"
    assert st.session_state["_review_chat_calls"] == 1


# --- AC-S2-17 强化：对话期间 controller 零写（不 resume / 不 cancel）----------- #
def test_chat_turns_never_touch_controller_until_apply():
    """连续多轮对话（AC-S2-15/17）：controller.resume_with / cancel_task 均零次调用，
    awaiting 不变（对话不直接落计划，graph 不发生 resume）。"""
    import streamlit as st

    mod = _plan_review_mod()
    st.session_state["_review_chat_messages"] = []
    st.session_state["_review_chat_calls"] = 0
    st.session_state["_review_awaiting"] = False

    controller = MagicMock()
    fake_llm = MagicMock()
    fake_resp = MagicMock()
    fake_resp.content = "模型回复"
    fake_llm.invoke.return_value = fake_resp

    with patch.object(mod, "_build_planning_chat_llm", return_value=fake_llm):
        for i in range(3):
            mod._handle_chat_turn(f"第 {i} 轮意见", _make_payload(), _LLM_CONFIG_SET)

    # 多轮消息正确追加（user/assistant 交替，共 6 条）。
    hist = st.session_state["_review_chat_messages"]
    assert len(hist) == 6
    assert [t["role"] for t in hist] == ["user", "assistant"] * 3
    assert st.session_state["_review_chat_calls"] == 3
    # 关键负向断言：对话阶段 graph 写路径零触达。
    controller.resume_with.assert_not_called()
    controller.cancel_task.assert_not_called()
    # awaiting 未被对话改动（仍 False = 未进入重规划轮询态）。
    assert st.session_state["_review_awaiting"] is False


# --- _apply_chat_revision 总结返回「空白串（非异常）」→ 退化拼接（探针实证）---- #
def test_apply_chat_revision_blank_summary_falls_back_to_concat():
    """模型总结返回纯空白（非抛错）→ feedback 为空 → 退化拼接用户发言，仍 resume_with 一次。"""
    import streamlit as st

    mod = _plan_review_mod()
    st.session_state["_review_chat_messages"] = [
        {"role": "user", "content": "把模型换成 BERT"},
        {"role": "assistant", "content": "好"},
    ]
    st.session_state["_review_chat_calls"] = 1
    st.session_state["_review_awaiting"] = False

    fake_llm = MagicMock()
    fake_resp = MagicMock()
    fake_resp.content = "   "  # 纯空白（strip 后空）
    fake_llm.invoke.return_value = fake_resp

    controller = MagicMock()
    with patch.object(mod, "_build_planning_chat_llm", return_value=fake_llm), \
            patch.object(mod.st, "rerun"):
        mod._apply_chat_revision(controller, "tid-blank", _make_payload(), _LLM_CONFIG_SET)

    controller.resume_with.assert_called_once()
    args, _ = controller.resume_with.call_args
    decision = args[1]
    assert decision["decision"] == "revise"
    assert decision["user_feedback"] == "把模型换成 BERT"  # 退化拼接用户发言
    assert st.session_state["_review_awaiting"] is True


def test_apply_chat_revision_no_content_does_not_resume():
    """完全空对话 + 总结失败 → 无可用内容 → 不 resume_with（不空跑）+ st.error 提示，不崩。

    纵深兜底：按钮在空对话时已 disabled（test_apply_button_disabled_on_empty_chat），
    本用例验证即便绕过 UI 直接调函数，也不会用空 feedback 触发无意义重规划。
    """
    import streamlit as st

    mod = _plan_review_mod()
    st.session_state["_review_chat_messages"] = []  # 完全空

    fake_llm = MagicMock()
    fake_llm.invoke.side_effect = RuntimeError("summary boom")

    controller = MagicMock()
    with patch.object(mod, "_build_planning_chat_llm", return_value=fake_llm), \
            patch.object(mod.st, "error") as mock_error, \
            patch.object(mod.st, "rerun"):
        mod._apply_chat_revision(controller, "tid-empty", _make_payload(), _LLM_CONFIG_SET)

    controller.resume_with.assert_not_called()
    assert mock_error.called


# --- AC-S2-18 强化：兜底输入框走 revise（AppTest 原生 widget，不依赖浏览器）---- #
def test_fallback_input_triggers_revise_via_apptest():
    """兜底输入框填一句方向 + 点「直接用这句话重新生成计划」→ resume_with(revise) 一次，
    user_feedback 为兜底原文（不经模型总结）；进入 awaiting。"""
    fb = "把数据集换成 2WikiMultiHopQA"
    script = f"""
import streamlit as st
st.session_state.setdefault("thread_id", "task-review-fb")
st.session_state["_review_chat_thread"] = "task-review-fb"
st.session_state["_review_fallback_feedback"] = {fb!r}
from ui.pages.plan_review import render
render()
"""
    controller = _make_controller_mock(payload=_make_payload())
    with patch("app._get_controller", return_value=controller):
        at = AppTest.from_string(script)
        at.run()
        assert not at.exception, at.exception
        # 兜底按钮为原生 st.button（AppTest 可见、可点）。
        fb_btns = [b for b in at.button if b.key == "btn_fallback_revise"]
        assert len(fb_btns) == 1, "应渲染兜底「直接用这句话重新生成计划」按钮"
        fb_btns[0].click().run()

    controller.resume_with.assert_called_once_with(
        "task-review-fb", {"decision": "revise", "user_feedback": fb}
    )


def test_fallback_input_empty_warns_no_revise():
    """兜底输入框为空时点重新生成 → 不 resume_with，仅 warning 提示先填写。"""
    script = """
import streamlit as st
st.session_state.setdefault("thread_id", "task-review-fb2")
st.session_state["_review_chat_thread"] = "task-review-fb2"
st.session_state["_review_fallback_feedback"] = "   "
from ui.pages.plan_review import render
render()
"""
    controller = _make_controller_mock(payload=_make_payload())
    with patch("app._get_controller", return_value=controller):
        at = AppTest.from_string(script)
        at.run()
        fb_btns = [b for b in at.button if b.key == "btn_fallback_revise"]
        assert len(fb_btns) == 1
        fb_btns[0].click().run()
    controller.resume_with.assert_not_called()
    assert any("请先填写" in w.value for w in at.warning)


# --- N≥5 chat_calls 软提示触发 / N=4 不触发对照（AC-S2-06 精神，对话口径）------ #
def _run_with_chat_calls(chat_calls: int, revise_count: int = 0) -> AppTest:
    """以指定 chat_calls / revise_count 渲染 review 页，返回 AppTest。"""
    script = f"""
import streamlit as st
st.session_state.setdefault("thread_id", "task-review-hint")
st.session_state["_review_chat_thread"] = "task-review-hint"
st.session_state["_review_chat_calls"] = {chat_calls}
from ui.pages.plan_review import render
render()
"""
    controller = _make_controller_mock(
        payload=_make_payload(revise_count=revise_count, soft_hint_threshold=5)
    )
    with patch("app._get_controller", return_value=controller):
        at = AppTest.from_string(script)
        at.run()
    return at


def test_chat_calls_soft_hint_at_threshold():
    """chat_calls == 5（== PLANNING_SOFT_HINT_THRESHOLD）→ 出对话软提示 warning。"""
    at = _run_with_chat_calls(5, revise_count=0)
    assert not at.exception, at.exception
    text = _collect_text(at)
    assert "本轮对话已消耗 5 次 LLM 调用" in text
    # revise_count=0（< 阈值）→ revise 软提示不应触发（与对话软提示独立）。
    assert "建议考虑直接批准或取消" not in text


def test_chat_calls_soft_hint_absent_below_threshold():
    """chat_calls == 4（< 5）→ 不出对话软提示（边界对照，防 off-by-one）。"""
    at = _run_with_chat_calls(4, revise_count=0)
    assert not at.exception, at.exception
    text = _collect_text(at)
    # info-bar 仍显示消耗次数，但不应出现软提示 warning 文案。
    assert "本轮对话已消耗 4 次调用" in text
    warns = "\n".join(str(w.value) for w in at.warning)
    assert "建议尽快敲定方向" not in warns


def test_info_bar_shows_both_revise_and_chat_counts():
    """info-bar 同列「已修改 N 轮」与「本轮对话已消耗 X 次调用」（透明化两口径并存）。"""
    at = _run_with_chat_calls(3, revise_count=2)
    assert not at.exception, at.exception
    text = _collect_text(at)
    assert "已修改 2 轮" in text
    assert "本轮对话已消耗 3 次调用" in text
    assert "LLM 调用上限 120 次" in text


# --- AC-S2-16 强化：_apply_chat_revision 计数 +1（总结调用计入对话计数）-------- #
def test_apply_chat_revision_increments_chat_calls_on_success():
    """敲定方向成功路径：总结调用使 chat_calls +1（计入对话口径），随后清零（落定后重置）。"""
    import streamlit as st

    mod = _plan_review_mod()
    st.session_state["_review_chat_messages"] = [
        {"role": "user", "content": "换数据集"},
        {"role": "assistant", "content": "好的"},
    ]
    st.session_state["_review_chat_calls"] = 2

    fake_llm = MagicMock()
    fake_resp = MagicMock()
    fake_resp.content = "修改方向纪要：更换数据集为 2WikiMultiHopQA。"
    fake_llm.invoke.return_value = fake_resp

    # 用旁路计数器验证 +1 发生在清零之前：拦截 resume_with 时读取当时计数。
    seen_calls = {}
    controller = MagicMock()
    controller.resume_with.side_effect = lambda *a, **k: seen_calls.setdefault(
        "at_resume", st.session_state["_review_chat_calls"]
    )

    with patch.object(mod, "_build_planning_chat_llm", return_value=fake_llm), \
            patch.object(mod.st, "rerun"):
        mod._apply_chat_revision(controller, "tid-cnt", _make_payload(), _LLM_CONFIG_SET)

    # 总结调用使计数从 2 → 3（在 resume_with 触发时刻已 +1）。
    assert seen_calls["at_resume"] == 3
    # 落定后清零（与历史一并重置）。
    assert st.session_state["_review_chat_calls"] == 0
    assert st.session_state["_review_chat_messages"] == []


# =========================================================================== #
# S6-05 T-S6-1-4：plan_review 警示位（CP-1.4-1 ~ CP-1.4-3）
#
# 验证 _render_plan_check_warnings 纯函数渲染与 render() 主流程集成：
#   CP-1.4-1：两面计划 payload → 警示行出现 + approve 按钮仍可用（不阻断）
#   CP-1.4-2：干净计划 payload → 零警示行
#   CP-1.4-3：数据不可得 payload → W3 警示行；interrupt 种类不变
# =========================================================================== #


def _make_two_faced_payload() -> Dict:
    """两面计划 payload（模拟 task-19e21e015017：data_prep 非空 + 无数据/实验步骤 + expected_results 非空）。"""
    return {
        "reproduction_plan": {
            "plan_summary": "复现 HippoRAG 基线实验",
            "data_preparation": ["下载 HippoRAG 原始数据集", "预处理为标准格式"],
            "execution_steps": [
                {"step_name": "安装依赖", "command": "pip install -r requirements.txt"},
                {"step_name": "配置环境变量", "command": "cp .env.example .env"},
                {"step_name": "初始化项目结构", "command": "mkdir -p outputs logs"},
                {"step_name": "验证 Python 环境", "command": "python --version"},
                {"step_name": "检查 CUDA 可用性", "command": "python -c 'import torch'"},
                {"step_name": "安装额外依赖", "command": "pip install faiss-cpu"},
                {"step_name": "创建输出目录", "command": "mkdir -p outputs/results"},
                {"step_name": "检查配置文件", "command": "cat config.yaml"},
                {"step_name": "验证模型路径", "command": "ls models/"},
                {"step_name": "初始化日志", "command": "touch logs/app.log"},
                {"step_name": "检查网络连接", "command": "ping -c 1 google.com"},
                {"step_name": "备份原始文件", "command": "cp -r . backup/"},
                {"step_name": "清理临时文件", "command": "rm -rf /tmp/cache"},
                {"step_name": "完成准备", "command": "echo 'setup done'"},
            ],
            "expected_results": {
                "description": "F1 ≥ 0.45（MuSiQue 基准）",
                "trend": "higher_is_better",
            },
            "code_strategy": "use_repo",
        },
        "resource_info": {
            "repos": [{"url": "https://github.com/OSU-NLP-Group/HippoRAG", "quality_score": 0.9}],
            "selected_repo": {"url": "https://github.com/OSU-NLP-Group/HippoRAG"},
            "external_resources": [],
            "resource_strategy": "use_repo",
        },
        "paper_analysis_summary": {"method_summary": "HippoRAG 检索增强"},
        "degraded_nodes": [],
        "node_errors": [],
        "revise_count": 0,
        "soft_hint_threshold": 5,
        "max_total_llm_calls": 120,
    }


def _make_clean_payload() -> Dict:
    """干净计划 payload（含数据步骤 + run experiment 步骤 + resource_info 有 dataset）。"""
    return {
        "reproduction_plan": {
            "plan_summary": "复现 HippoRAG 实验",
            "data_preparation": ["下载 MuSiQue 数据集"],
            "execution_steps": [
                {"step_name": "安装依赖", "command": "pip install -r requirements.txt"},
                {
                    "step_name": "下载数据集",
                    "command": "python scripts/download_dataset.py --name musique",
                },
                {
                    "step_name": "运行实验",
                    "command": "python run_experiment.py --config configs/base.yaml",
                },
                {
                    "step_name": "评测并输出指标",
                    "command": "python eval.py --output outputs/results/summary.json",
                },
            ],
            "expected_results": [
                {"description": "F1 ≥ 0.45", "trend": {"metric": "F1", "greater": "ours", "lesser": "baseline"}}
            ],
            "code_strategy": "use_repo",
        },
        "resource_info": {
            "repos": [{"url": "https://github.com/OSU-NLP-Group/HippoRAG", "quality_score": 0.9}],
            "selected_repo": {"url": "https://github.com/OSU-NLP-Group/HippoRAG"},
            "external_resources": [
                {"type": "dataset", "name": "MuSiQue", "url": "https://huggingface.co/datasets/musique"},
            ],
            "resource_strategy": "use_repo",
        },
        "paper_analysis_summary": {"method_summary": "HippoRAG 检索增强"},
        "degraded_nodes": [],
        "node_errors": [],
        "revise_count": 0,
        "soft_hint_threshold": 5,
        "max_total_llm_calls": 120,
    }


def _make_no_dataset_payload() -> Dict:
    """数据不可得 payload（data_prep 非空 + selected_repo=None + 无 dataset）。"""
    return {
        "reproduction_plan": {
            "plan_summary": "复现 X 实验",
            "data_preparation": ["下载 HippoRAG 原始数据集"],
            "execution_steps": [
                {"step_name": "安装依赖", "command": "pip install -r requirements.txt"},
            ],
            "expected_results": {},
            "code_strategy": "from_scratch",
        },
        "resource_info": {
            "repos": [],
            "selected_repo": None,
            "external_resources": [],
            "resource_strategy": "from_scratch",
        },
        "paper_analysis_summary": {},
        "degraded_nodes": [],
        "node_errors": [],
        "revise_count": 0,
        "soft_hint_threshold": 5,
        "max_total_llm_calls": 120,
    }


# CP-1.4-1：_render_plan_check_warnings 纯函数直测（两面计划 → W1/W2 出现）
def test_cp_1_4_1_render_plan_check_warnings_two_faced_plan():
    """两面计划 payload → _render_plan_check_warnings 渲染 W1/W2 警示行（直测纯函数）。"""
    mod = _plan_review_mod()
    plan = _make_two_faced_payload()["reproduction_plan"]
    resource_info = _make_two_faced_payload()["resource_info"]

    # 直接调用纯函数，验证 check_plan 返回正确警示
    from core.plan_checks import check_plan
    warnings = check_plan(plan, resource_info)
    rules = {w["rule"] for w in warnings}
    assert "W1" in rules, f"两面计划应触发 W1，实际：{warnings}"
    assert "W2" in rules, f"两面计划应触发 W2，实际：{warnings}"


# CP-1.4-1（AppTest 面）：两面计划 → 审核页出现 W1/W2 文案 + approve 按钮仍可用
def test_cp_1_4_1_apptest_two_faced_plan_warnings_visible():
    """AppTest：两面计划 payload → 审核页出现 W1/W2 警示文案。"""
    controller = _make_controller_mock(payload=_make_two_faced_payload())
    at = _run(controller)
    assert not at.exception, f"页面崩溃：{at.exception}"

    text = _collect_text(at)
    assert "W1" in text or "数据相关步骤" in text, (
        f"两面计划应出现 W1 警示，实际文本摘要：{text[:500]}"
    )
    assert "W2" in text or "指标" in text or "实验" in text, (
        f"两面计划应出现 W2 警示，实际文本摘要：{text[:500]}"
    )


# CP-1.4-2：干净计划 → 零警示行
def test_cp_1_4_2_clean_plan_no_warnings():
    """干净计划 payload → plan_checks 零警示（误报防线）。"""
    from core.plan_checks import check_plan
    payload = _make_clean_payload()
    plan = payload["reproduction_plan"]
    resource_info = payload["resource_info"]
    warnings = check_plan(plan, resource_info)
    assert warnings == [], f"干净计划误报警示：{warnings}"


def test_cp_1_4_2_apptest_clean_plan_no_warning_text():
    """AppTest：干净计划 payload → 审核页不出现 W1/W2/W3 警示标记。"""
    controller = _make_controller_mock(payload=_make_clean_payload())
    at = _run(controller)
    assert not at.exception, f"页面崩溃：{at.exception}"

    # 警示区域不含 [W1]/[W2]/[W3] 标记
    warning_texts = [str(getattr(w, "value", "")) for w in at.warning]
    for wt in warning_texts:
        assert "[W1]" not in wt and "[W2]" not in wt and "[W3]" not in wt, (
            f"干净计划误报警示：{wt}"
        )


# CP-1.4-3：数据不可得 → W3 警示行出现；interrupt 种类不变（无新 gate）
def test_cp_1_4_3_no_dataset_w3_warning():
    """数据不可得 payload → plan_checks 触发 W3 警示。"""
    from core.plan_checks import check_plan
    payload = _make_no_dataset_payload()
    warnings = check_plan(payload["reproduction_plan"], payload["resource_info"])
    rules = {w["rule"] for w in warnings}
    assert "W3" in rules, f"数据不可得应触发 W3，实际：{warnings}"


def test_cp_1_4_3_apptest_no_dataset_w3_visible():
    """AppTest：数据不可得 payload → 审核页出现 W3 警示文案。"""
    controller = _make_controller_mock(payload=_make_no_dataset_payload())
    at = _run(controller)
    assert not at.exception, f"页面崩溃：{at.exception}"

    text = _collect_text(at)
    assert "W3" in text or "数据集" in text, (
        f"数据不可得应出现 W3 警示，实际文本摘要：{text[:500]}"
    )


def test_cp_1_4_3_approve_button_still_available_despite_warnings():
    """警示不阻断审批：两面计划 payload 下 approve 按钮仍可用（不 disabled）。

    由于 approve 按钮是原生 st.button，AppTest 可见（不在 shadcn iframe 内）。
    """
    controller = _make_controller_mock(payload=_make_two_faced_payload())
    at = _run(controller)
    assert not at.exception, f"页面崩溃：{at.exception}"

    # 找 key=btn_approve 的按钮
    approve_buttons = [b for b in at.button if getattr(b, "key", None) == "btn_approve"]
    assert len(approve_buttons) == 1, (
        f"应存在 key='btn_approve' 的原生按钮，实际找到：{[b.key for b in at.button]}"
    )
    # 验证按钮未被 disabled
    btn = approve_buttons[0]
    assert not getattr(btn, "disabled", False), (
        "警示不阻断审批，approve 按钮不应被 disabled"
    )
