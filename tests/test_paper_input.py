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

from streamlit.testing.v1 import AppTest


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
        # 故意不填侧栏 LLM 配置（cfg 为 None）；只填 arxiv_id
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
