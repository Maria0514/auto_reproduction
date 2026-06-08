"""S2-05 论文输入页单测（D3，CP-D3-1 ~ CP-D3-6）。

测试策略
========
- 主路径通过 ``streamlit.testing.v1.AppTest`` 驱动真实页面脚本（与 D1 test_llm_config_form
  同款），模拟 "侧栏填 LLM 配置 → 输入 arXiv ID → 点获取论文信息 → 点开始复现" 完整路径。
- **纯 mock，不烧 token、不连真实网络**：
  - deepxiv 经 ``unittest.mock.patch("ui.pages.paper_input.DeepxivTools")`` 替换（页面把
    DeepxivTools 直接 import 进自身命名空间，故 patch 其引用即可）；
  - GraphController 经 ``patch("app._get_controller")`` 注入 Mock 单例（页面通过
    ``from app import _get_controller`` 取，故 patch app 模块的源符号）；
  - AppTest 与测试在同进程同线程运行，patch 在 ``at.run()`` 期间生效。
- CP-D3-1 用 importlib 校验 ``render`` 可导入（避免 __init__ 显式导出遮蔽子模块，沿用
  sp1/C2 教训：测试访问子模块属性用 importlib.import_module）。

运行::

    .venv/bin/python -m pytest tests/test_paper_input.py -q
"""

from __future__ import annotations

import importlib
from unittest.mock import MagicMock, patch

import pytest
from streamlit.testing.v1 import AppTest

from core.errors import PermanentError, TransientError


# --------------------------------------------------------------------------- #
# AppTest 脚本：顶层调用 render()，并把侧栏表单结果落 session_state 供旁证。
# --------------------------------------------------------------------------- #
_APP_SCRIPT = """
from ui.pages.paper_input import render
render()
"""


# 一份合法的 brief / head mock 返回（CS 论文，靶 arXiv:2405.14831 HippoRAG）。
_BRIEF_CS = {
    "arxiv_id": "2405.14831",
    "title": "HippoRAG: Neurobiologically Inspired Long-Term Memory for LLMs",
    "tldr": "A retrieval framework inspired by the hippocampal indexing theory.",
    "github_url": "https://github.com/OSU-NLP-Group/HippoRAG",
    "keywords": ["RAG", "memory"],
}
_HEAD_CS = {
    "title": "HippoRAG: Neurobiologically Inspired Long-Term Memory for LLMs",
    "abstract": "We introduce HippoRAG, a novel retrieval framework ...",
    "authors": ["Bernal Jiménez Gutiérrez", "Yiheng Shu", "Yu Su"],
    "categories": ["cs.CL", "cs.AI"],
}

# non-CS 论文（CP-D3-5）：categories 全部非 cs.*。
_BRIEF_NONCS = {
    "arxiv_id": "1234.56789",
    "title": "A Theorem in Partial Differential Equations",
    "tldr": None,
    "github_url": None,
}
_HEAD_NONCS = {
    "title": "A Theorem in Partial Differential Equations",
    "abstract": "We prove a new estimate ...",
    "authors": ["Jane Mathematician"],
    "categories": ["math.AP"],
}


def _make_deepxiv_mock(brief: dict, head: dict) -> MagicMock:
    """构造 DeepxivTools 类 mock：实例方法 get_paper_brief / get_paper_head 返回固定数据。"""
    tools_cls = MagicMock()
    instance = tools_cls.return_value
    instance.get_paper_brief.return_value = brief
    instance.get_paper_head.return_value = head
    instance.search_papers.return_value = []
    return tools_cls


def _make_controller_mock(thread_id: str = "task-abc123def456") -> MagicMock:
    """构造 GraphController mock：start_task 返回固定 thread_id。"""
    controller = MagicMock()
    controller.start_task.return_value = thread_id
    return controller


def _fill_sidebar_llm(at: AppTest) -> None:
    """在侧栏填好全局默认 LLM 配置（令 render_llm_config_form 返回非 None cfg）。"""
    at.text_input(key="default_base_url").set_value("https://api.example.com/v1")
    at.text_input(key="default_model").set_value("gpt-4o")
    at.text_input(key="default_api_key").set_value("sk-TEST")


# --------------------------------------------------------------------------- #
# CP-D3-1：可正常导入 render（importlib 访问子模块，避免 __init__ 遮蔽）
# --------------------------------------------------------------------------- #
def test_cp_d3_1_importable():
    mod = importlib.import_module("ui.pages.paper_input")
    assert callable(mod.render)
    # dev-plan 明示的入口名
    from ui.pages.paper_input import render  # noqa: F401

    assert callable(render)
    # 兼容 D2 app.py page_map 的别名也在
    assert mod.render_paper_input_page is mod.render


# --------------------------------------------------------------------------- #
# CP-D3-4：mock brief/head 返回有效数据 → 卡片正确展示 title/abstract/authors
# --------------------------------------------------------------------------- #
def test_cp_d3_4_card_renders_brief_head():
    deepxiv_mock = _make_deepxiv_mock(_BRIEF_CS, _HEAD_CS)
    with patch("ui.pages.paper_input.DeepxivTools", deepxiv_mock), patch(
        "app._get_controller", return_value=_make_controller_mock()
    ):
        at = AppTest.from_string(_APP_SCRIPT)
        at.run()
        at.text_input(key="arxiv_id_input").set_value("2405.14831")
        at.button(key="btn_fetch").click().run()

    # 卡片标题（subheader）+ 作者（caption）+ abstract（expander 内 write）应出现在元素树。
    all_text = _collect_text(at)
    assert "HippoRAG" in all_text
    assert "Yu Su" in all_text  # 作者
    assert "novel retrieval framework" in all_text  # abstract
    # CS 论文不应出现 non-CS WARNING
    assert all("不属于 CS" not in w.value for w in at.warning)


# --------------------------------------------------------------------------- #
# CP-D3-5：non-CS 论文 → WARNING 卡片，但"开始复现"按钮可点（填好 cfg + arxiv_id）
# --------------------------------------------------------------------------- #
def test_cp_d3_5_non_cs_warns_but_not_blocked():
    deepxiv_mock = _make_deepxiv_mock(_BRIEF_NONCS, _HEAD_NONCS)
    with patch("ui.pages.paper_input.DeepxivTools", deepxiv_mock), patch(
        "app._get_controller", return_value=_make_controller_mock()
    ):
        at = AppTest.from_string(_APP_SCRIPT)
        at.run()
        _fill_sidebar_llm(at)
        at.text_input(key="arxiv_id_input").set_value("1234.56789")
        at.button(key="btn_fetch").click().run()

        # non-CS WARNING 卡片存在
        assert any("不属于 CS" in w.value for w in at.warning)
        # "开始复现"按钮可点（disabled=False）
        start_btn = at.button(key="btn_start")
        assert start_btn.disabled is False


# --------------------------------------------------------------------------- #
# CP-D3-2：未填 llm_config_set → 点"开始复现"不调用 start_task（按钮禁用 + 不调用）
# --------------------------------------------------------------------------- #
def test_cp_d3_2_no_cfg_does_not_start():
    deepxiv_mock = _make_deepxiv_mock(_BRIEF_CS, _HEAD_CS)
    controller = _make_controller_mock()
    with patch("ui.pages.paper_input.DeepxivTools", deepxiv_mock), patch(
        "app._get_controller", return_value=controller
    ):
        at = AppTest.from_string(_APP_SCRIPT)
        at.run()
        # D1 增强适配：default panel 现在预填 base_url/model + env 有 LLM_API_KEY，
        # 故"什么都不填"会产出合法 cfg。要制造 cfg=None（必填失败），需显式清空
        # 预填的 base_url/model（与补-1 stale 用例清空 model 同源）。
        at.text_input(key="default_base_url").set_value("")
        at.text_input(key="default_model").set_value("")
        # 只填 arxiv_id
        at.text_input(key="arxiv_id_input").set_value("2405.14831")
        at.run()

        # 按钮禁用（cfg is None）
        assert at.button(key="btn_start").disabled is True
        # 即便强行 click 也不应触发 start_task（disabled 控件 click 不产生状态变更，
        # 且回调内有 cfg is None 的二次校验兜底）
        at.button(key="btn_start").click().run()
        controller.start_task.assert_not_called()
        # 未跳转
        assert at.session_state["current_page"] == "input"


# --------------------------------------------------------------------------- #
# CP-D3-3：填好 cfg + arxiv_id → start_task 被调用 1 次，传参与 UI 输入一致
# CP-D3-6：提交后 current_page == "progress"、thread_id 非空、控件禁用
# --------------------------------------------------------------------------- #
def test_cp_d3_3_and_6_start_task_called_and_navigates():
    deepxiv_mock = _make_deepxiv_mock(_BRIEF_CS, _HEAD_CS)
    controller = _make_controller_mock(thread_id="task-deadbeef0001")
    with patch("ui.pages.paper_input.DeepxivTools", deepxiv_mock), patch(
        "app._get_controller", return_value=controller
    ):
        at = AppTest.from_string(_APP_SCRIPT)
        at.run()
        _fill_sidebar_llm(at)
        at.text_input(key="arxiv_id_input").set_value("2405.14831")
        at.run()

        # 此时按钮应可点
        assert at.button(key="btn_start").disabled is False
        at.button(key="btn_start").click().run()

    # CP-D3-3：start_task 被调用 1 次，传参一致
    controller.start_task.assert_called_once()
    call_args = controller.start_task.call_args
    assert call_args.args[0] == "2405.14831"
    # 第二参为 cfg（LLMConfigSet），其 default.model 与 UI 输入一致
    cfg_arg = call_args.args[1]
    assert cfg_arg["default"]["model"] == "gpt-4o"
    assert cfg_arg["default"]["api_key"] == "sk-TEST"

    # CP-D3-6：跳转 progress + thread_id 非空
    assert at.session_state["current_page"] == "progress"
    assert at.session_state["thread_id"] == "task-deadbeef0001"
    assert at.session_state["thread_id"]

    # CP-D3-6：提交后控件禁用（rerun 后 submitted=True）
    assert at.session_state["_input_submitted"] is True
    assert at.button(key="btn_start").disabled is True
    assert at.text_input(key="arxiv_id_input").disabled is True
    assert at.button(key="btn_fetch").disabled is True


# --------------------------------------------------------------------------- #
# 辅助：把 AppTest 元素树里所有可读文本聚合成一个字符串，便于断言卡片内容。
# --------------------------------------------------------------------------- #
def _collect_text(at: AppTest) -> str:
    parts = []
    for collection in (
        at.subheader,
        at.caption,
        at.markdown,
        at.text,
        at.title,
        at.warning,
        at.info,
        at.error,
    ):
        for el in collection:
            parts.append(str(getattr(el, "value", "")))
    # st.write(abstract) 渲染为 markdown 元素，已被上面 at.markdown 覆盖；
    # expander 内元素同样在主元素树可见（D1 实测 AppTest 可访问 expander 内 widget）。
    return "\n".join(parts)


# =========================================================================== #
# 测试工程师补强用例（2026-06-04 D3 独立验收）
#
# 补齐开发 5 用例遗漏的边界/分支：
#   - OBS-D1-01 stale 序列（cfg 由合法→非法，按钮必须重新禁用、不读 stale 键）
#   - head 失败降级（展示 brief 不报死）
#   - brief 失败（致命错误文案 + 不渲染卡片）
#   - 关键词搜索 selectresults 回填（BUG-S2-D3-01，xfail strict 钉死）
#   - 别名 render_paper_input_page 可直接 import 且与 render 同对象
#   - _is_non_cs 边界（空 categories 不误报 / 大小写 / 混合分类含 cs.* 视为 CS）
#   - 防重复提交：提交后全控件 disabled
#   - 未获取论文卡片也可直接开始复现（卡片是可选展示，不是开始前置）
# =========================================================================== #


# --------------------------------------------------------------------------- #
# 补-1（CP-D3-2 强化 / OBS-D1-01 核心）：cfg 由合法→非法的 stale 序列。
# 这是 OBS-D1-01 最关键的独立裁定点：D1 校验失败返回 None 时不清 session_state
# 的 stale 键；若 D3 直读该键会拿到上一次合法配置。正确实现必须用返回值 cfg，
# 故"先填合法（写入 stale 键）→ 改成非法"后按钮必须重新禁用、start_task 不被调用。
# --------------------------------------------------------------------------- #
def test_bnd_stale_cfg_legal_then_illegal_disables_button():
    deepxiv_mock = _make_deepxiv_mock(_BRIEF_CS, _HEAD_CS)
    controller = _make_controller_mock()
    with patch("ui.pages.paper_input.DeepxivTools", deepxiv_mock), patch(
        "app._get_controller", return_value=controller
    ):
        at = AppTest.from_string(_APP_SCRIPT)
        at.run()
        # 1) 先填合法 cfg + arxiv_id → 按钮可点（D1 此时写入 session_state["llm_config_set"]）
        _fill_sidebar_llm(at)
        at.text_input(key="arxiv_id_input").set_value("2405.14831")
        at.run()
        assert at.button(key="btn_start").disabled is False
        # 合法配置已落入 stale 键
        assert "llm_config_set" in at.session_state

        # 2) 把 model 清空 → cfg 变 None（D1 返回 None 但不清 stale 键）
        at.text_input(key="default_model").set_value("")
        at.run()
        # 关键裁定：按钮必须重新禁用（依据 render_llm_config_form 返回值，不依赖 stale 键）
        assert at.button(key="btn_start").disabled is True
        # stale 键确实仍在（证明 D1 不清键，背景成立）
        assert "llm_config_set" in at.session_state

        # 3) 即便强行点击也不触发 start_task（双保险回调内 cfg is None 校验）
        at.button(key="btn_start").click().run()
        controller.start_task.assert_not_called()
        assert at.session_state["current_page"] == "input"


# --------------------------------------------------------------------------- #
# 补-2（CP-D3-4 分支）：head 失败降级 —— 仅展示 brief 字段，不报死、不写 fetch_error。
# --------------------------------------------------------------------------- #
def test_bnd_head_failure_degrades_to_brief():
    cls = MagicMock()
    inst = cls.return_value
    inst.get_paper_brief.return_value = {
        "arxiv_id": "2405.14831",
        "title": "HippoRAG",
        "tldr": "tldr-degraded",
        "github_url": "https://github.com/x",
        "keywords": [],
    }
    inst.get_paper_head.side_effect = TransientError("head boom")
    inst.search_papers.return_value = []
    with patch("ui.pages.paper_input.DeepxivTools", cls), patch(
        "app._get_controller", return_value=_make_controller_mock()
    ):
        at = AppTest.from_string(_APP_SCRIPT)
        at.run()
        at.text_input(key="arxiv_id_input").set_value("2405.14831")
        at.button(key="btn_fetch").click().run()

    # 卡片仍展示（降级展示 brief），fetch_error 不被置位（head 仅补充信息）
    assert at.session_state["_input_paper_card"] is not None
    assert at.session_state["_input_fetch_error"] is None
    all_text = _collect_text(at)
    assert "HippoRAG" in all_text          # brief.title
    assert "tldr-degraded" in all_text     # brief.tldr
    # head 缺失 → abstract/authors 为空，不应崩溃
    assert at.session_state["_input_paper_card"]["abstract"] == ""
    assert at.session_state["_input_paper_card"]["authors"] == []


# --------------------------------------------------------------------------- #
# 补-3（CP-D3-4 分支）：brief 失败 —— 致命错误文案 + 不渲染卡片。
# brief 是卡片主字段来源，失败时 _fetch_paper_card 返回 (None, err)。
# --------------------------------------------------------------------------- #
def test_bnd_brief_failure_shows_error_no_card():
    cls = MagicMock()
    inst = cls.return_value
    inst.get_paper_brief.side_effect = PermanentError("paper not found")
    inst.get_paper_head.return_value = {}
    inst.search_papers.return_value = []
    with patch("ui.pages.paper_input.DeepxivTools", cls), patch(
        "app._get_controller", return_value=_make_controller_mock()
    ):
        at = AppTest.from_string(_APP_SCRIPT)
        at.run()
        at.text_input(key="arxiv_id_input").set_value("9999.99999")
        at.button(key="btn_fetch").click().run()

    # 卡片为 None，fetch_error 被置位
    assert at.session_state["_input_paper_card"] is None
    assert at.session_state["_input_fetch_error"]
    assert "获取论文摘要失败" in at.session_state["_input_fetch_error"]
    # head 不应被调用（brief 先失败已 return）
    inst.get_paper_head.assert_not_called()


# --------------------------------------------------------------------------- #
# 补-4（CP-D3-3 分支）：未点"获取论文信息"也可直接开始复现。
# 卡片展示是可选辅助，不是 start 前置；只要 cfg + arxiv_id 齐备按钮即可点。
# --------------------------------------------------------------------------- #
def test_bnd_start_without_fetching_card():
    deepxiv_mock = _make_deepxiv_mock(_BRIEF_CS, _HEAD_CS)
    controller = _make_controller_mock(thread_id="task-nofetch")
    with patch("ui.pages.paper_input.DeepxivTools", deepxiv_mock), patch(
        "app._get_controller", return_value=controller
    ):
        at = AppTest.from_string(_APP_SCRIPT)
        at.run()
        _fill_sidebar_llm(at)
        at.text_input(key="arxiv_id_input").set_value("2405.14831")
        at.run()
        # 没点 btn_fetch，卡片为空
        assert at.session_state["_input_paper_card"] is None
        assert at.button(key="btn_start").disabled is False
        at.button(key="btn_start").click().run()

    controller.start_task.assert_called_once()
    assert controller.start_task.call_args.args[0] == "2405.14831"
    assert at.session_state["current_page"] == "progress"
    assert at.session_state["thread_id"] == "task-nofetch"


# --------------------------------------------------------------------------- #
# 补-5（CP-D3-1 强化）：别名 render_paper_input_page 可直接 import 且与 render 同对象。
# D2 app.py page_map 用 render_paper_input_page 动态加载，此为唯一适配点。
# --------------------------------------------------------------------------- #
def test_bnd_alias_render_paper_input_page_importable():
    from ui.pages.paper_input import render, render_paper_input_page

    assert callable(render_paper_input_page)
    assert render_paper_input_page is render
    # app.py page_map 声明的 (module, func) 二元组可用 getattr 取到
    mod = importlib.import_module("ui.pages.paper_input")
    assert getattr(mod, "render_paper_input_page") is mod.render


# --------------------------------------------------------------------------- #
# 补-6：_is_non_cs 纯函数边界（不经 AppTest，直接单测分类判定逻辑）。
# --------------------------------------------------------------------------- #
def test_bnd_is_non_cs_classification_edges():
    mod = importlib.import_module("ui.pages.paper_input")
    _is_non_cs = mod._is_non_cs

    # 空 categories → 保守不误报（返回 False，不弹 WARNING）
    assert _is_non_cs([]) is False
    # 纯非 CS → True
    assert _is_non_cs(["math.AP"]) is True
    assert _is_non_cs(["stat.ML", "math.PR"]) is True
    # 含任一 cs.* → 视为 CS（False）
    assert _is_non_cs(["cs.CL"]) is False
    assert _is_non_cs(["math.AP", "cs.LG"]) is False
    # 大小写不敏感（startswith 前已 lower）
    assert _is_non_cs(["CS.CL"]) is False


# --------------------------------------------------------------------------- #
# 补-7（CP-D3-6 强化）：提交后 search section 控件也禁用（防重复提交全控件覆盖）。
# 开发 CP-D3-6 只断言 btn_start / arxiv_id_input / btn_fetch 三件；这里补 search 区。
# --------------------------------------------------------------------------- #
def test_bnd_all_widgets_disabled_after_submit():
    deepxiv_mock = _make_deepxiv_mock(_BRIEF_CS, _HEAD_CS)
    controller = _make_controller_mock(thread_id="task-disabled")
    with patch("ui.pages.paper_input.DeepxivTools", deepxiv_mock), patch(
        "app._get_controller", return_value=controller
    ):
        at = AppTest.from_string(_APP_SCRIPT)
        at.run()
        _fill_sidebar_llm(at)
        at.text_input(key="arxiv_id_input").set_value("2405.14831")
        at.run()
        at.button(key="btn_start").click().run()

    assert at.session_state["_input_submitted"] is True
    # 主区控件
    assert at.button(key="btn_start").disabled is True
    assert at.text_input(key="arxiv_id_input").disabled is True
    assert at.button(key="btn_fetch").disabled is True
    # search section 控件（防重复提交应一并禁用）
    assert at.text_input(key="search_query").disabled is True
    assert at.button(key="btn_search").disabled is True


# --------------------------------------------------------------------------- #
# 补-8（BUG-S2-D3-01 回归，已修复）：关键词搜索 → 点"选用"回填 arXiv ID。
#
# 期望：点候选的"选用"按钮应把该 arxiv_id 回填到上方 arXiv ID 输入框（页面
# docstring + dev-plan §D3「主区下半」明示的 P1 功能）。
#
# 历史：原实现 `_render_search_section` 直写已实例化 widget key arxiv_id_input →
# Streamlit 抛 StreamlitAPIException，真实点"选用"即崩溃（BUG-S2-D3-01）。测试工程师
# 曾以 xfail(strict=True) 钉死。全栈开发代理已修复（pending 键 _input_pending_arxiv
# 中转 + st.rerun()，render() 在 text_input 实例化前 pop 灌入 widget key 作初值，绝不
# 直写已实例化 widget key）。本用例去 xfail 转常规回归：验证"选用→rerun→回填生效且无异常"。
# --------------------------------------------------------------------------- #
def test_bug_s2_d3_01_search_pick_backfills_arxiv_id():
    cls = MagicMock()
    inst = cls.return_value
    inst.get_paper_brief.return_value = _BRIEF_CS
    inst.get_paper_head.return_value = _HEAD_CS
    inst.search_papers.return_value = [
        {"arxiv_id": "2401.00001", "title": "Paper A"},
        {"id": "2402.00002", "title": "Paper B"},
    ]
    with patch("ui.pages.paper_input.DeepxivTools", cls), patch(
        "app._get_controller", return_value=_make_controller_mock()
    ):
        at = AppTest.from_string(_APP_SCRIPT)
        at.run()
        at.text_input(key="search_query").set_value("rag")
        at.button(key="btn_search").click().run()
        # 搜索结果已暂存
        assert len(at.session_state["_input_search_results"]) == 2
        # 点"选用"第 0 条 —— 触发 pending 键中转 + st.rerun()，修复后不应崩溃
        at.button(key="pick_0").click().run()

        # 1) 真实路径无未捕获异常（修复前此处会记到 StreamlitAPIException）。
        assert not at.exception
        # 2) 回填生效：widget 当前值 == 选中的 arxiv_id（权威输入源 widget 自身 state）。
        assert at.text_input(key="arxiv_id_input").value == "2401.00001"
        # 3) 对外镜像 selected_arxiv_id 跟随 widget 当前值。
        assert at.session_state["selected_arxiv_id"] == "2401.00001"
        # 4) pending 中转键消费后已被 pop 清空，不残留污染下一轮 run。
        assert "_input_pending_arxiv" not in at.session_state

        # 5) 回填后即可基于该 arxiv_id 继续主路径（填 cfg → 开始复现），证明回填值真正可用。
        _fill_sidebar_llm(at)
        at.run()
        assert not at.exception
        assert at.text_input(key="arxiv_id_input").value == "2401.00001"
        assert at.button(key="btn_start").disabled is False
