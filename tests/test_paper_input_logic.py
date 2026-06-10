"""paper_input 页「逻辑单测」（新范式：不起浏览器、不点 iframe 按钮）。

背景
====
ui/pages/paper_input.py 把决策按钮（btn_fetch / btn_search / btn_start / pick_*）
渲染为 streamlit-shadcn-ui 的 ``ui.button``，组件运行在 iframe 里。
``streamlit.testing.v1.AppTest`` 看不到 iframe 组件、点击不回写 session_state，
故所有「点击 ui.button → 断言 controller / session_state 状态」类用例都已迁到
``tests/test_paper_input_e2e.py``（Playwright）。

本文件只保留**不需点击 iframe**即可断言的逻辑：

- `_fetch_paper_card` 纯函数：head 失败降级 / brief 失败致命错误（直接调，不经 render）
- `_is_non_cs` 纯函数边界（已在原 test_paper_input.py 中保留）
- 「cfg=None → btn_start 退化为 st.button(disabled=True)」AppTest 断言
  （cfg=None 路径不渲染 ui.button，故 AppTest 可见。stale 序列里
  「合法 cfg → 不合法 cfg」的合法阶段会触发 ui.button 崩溃，
  完整 stale 序列已迁 e2e；本处仅留 cfg=None 直接禁用一刀。）

运行::

    .venv/bin/python -m pytest tests/test_paper_input_logic.py -v
"""

from __future__ import annotations

import importlib

from core.errors import PermanentError, TransientError


# =========================================================================== #
# _fetch_paper_card 纯函数测（直接调，绕过 render，不触发 ui.button 崩溃）
# =========================================================================== #
class _FakeDeepxivTools:
    """轻量替身：DeepxivTools() 实例，行为由属性脚本化。"""

    def __init__(self, brief, head, *, brief_exc=None, head_exc=None):
        self._brief = brief
        self._head = head
        self._brief_exc = brief_exc
        self._head_exc = head_exc

    def get_paper_brief(self, arxiv_id):
        if self._brief_exc is not None:
            raise self._brief_exc
        return self._brief

    def get_paper_head(self, arxiv_id):
        if self._head_exc is not None:
            raise self._head_exc
        return self._head


def _patch_deepxiv(monkeypatch, fake):
    """把 ui.pages.paper_input.DeepxivTools 替换为返回 fake 的 callable。"""
    mod = importlib.import_module("ui.pages.paper_input")
    monkeypatch.setattr(mod, "DeepxivTools", lambda *a, **kw: fake)
    return mod


def test_fetch_card_head_failure_degrades_to_brief(monkeypatch):
    """补-2 迁 logic：head 失败 → 仅展示 brief 字段，card 不为 None、err 为 None。

    原 test_bnd_head_failure_degrades_to_brief 通过 AppTest 点 btn_fetch 触发。
    btn_fetch 在非 submitted 时是 ui.button → AppTest 崩。改为直调底层纯函数，
    断言 _fetch_paper_card 返回 (card, None) 且 abstract/authors 为空兜底。
    """
    fake = _FakeDeepxivTools(
        brief={
            "arxiv_id": "2405.14831",
            "title": "HippoRAG",
            "tldr": "tldr-degraded",
            "github_url": "https://github.com/x",
            "keywords": [],
        },
        head=None,
        head_exc=TransientError("head boom"),
    )
    mod = _patch_deepxiv(monkeypatch, fake)

    card, err = mod._fetch_paper_card("2405.14831")

    assert err is None, f"head 失败仅降级，err 应为 None，实际：{err}"
    assert card is not None
    assert card["title"] == "HippoRAG"
    assert card["tldr"] == "tldr-degraded"
    assert card["github_url"] == "https://github.com/x"
    # head 缺失 → abstract/authors/categories 兜底为空
    assert card["abstract"] == ""
    assert card["authors"] == []
    assert card["categories"] == []


def test_fetch_card_brief_failure_returns_error_and_no_card(monkeypatch):
    """补-3 迁 logic：brief 失败 → (None, err)，且 head 不应被调用（先失败 return）。"""
    head_called = {"n": 0}

    class _Tools:
        def get_paper_brief(self, arxiv_id):
            raise PermanentError("paper not found")

        def get_paper_head(self, arxiv_id):
            head_called["n"] += 1
            return {}

    mod = importlib.import_module("ui.pages.paper_input")
    monkeypatch.setattr(mod, "DeepxivTools", lambda *a, **kw: _Tools())

    card, err = mod._fetch_paper_card("9999.99999")

    assert card is None
    assert err is not None
    assert "获取论文摘要失败" in err
    assert head_called["n"] == 0, "brief 先失败已 return，head 不应被调用"


def test_fetch_card_empty_arxiv_returns_hint(monkeypatch):
    """边界：arxiv_id 空串/纯空白 → 返回 (None, '请先输入 arXiv ID')，不触发 deepxiv。"""
    called = {"brief": 0}

    class _Tools:
        def get_paper_brief(self, arxiv_id):
            called["brief"] += 1
            return {}

        def get_paper_head(self, arxiv_id):
            return {}

    mod = importlib.import_module("ui.pages.paper_input")
    monkeypatch.setattr(mod, "DeepxivTools", lambda *a, **kw: _Tools())

    card, err = mod._fetch_paper_card("   ")
    assert card is None
    assert err == "请先输入 arXiv ID"
    assert called["brief"] == 0, "空输入不应触发 deepxiv 调用"


def test_fetch_card_full_cs_paper_merges_brief_and_head(monkeypatch):
    """主路径：brief + head 合并成完整 card（title/abstract/authors/categories/tldr/github_url）。"""
    fake = _FakeDeepxivTools(
        brief={
            "arxiv_id": "2405.14831",
            "title": "HippoRAG",
            "tldr": "tldr-cs",
            "github_url": "https://github.com/OSU-NLP-Group/HippoRAG",
            "keywords": ["RAG"],
        },
        head={
            "title": "HippoRAG",
            "abstract": "We introduce HippoRAG ...",
            "authors": ["Yu Su"],
            "categories": ["cs.CL"],
        },
    )
    mod = _patch_deepxiv(monkeypatch, fake)

    card, err = mod._fetch_paper_card("2405.14831")
    assert err is None
    assert card["title"] == "HippoRAG"
    assert card["abstract"].startswith("We introduce")
    assert card["authors"] == ["Yu Su"]
    assert card["categories"] == ["cs.CL"]
    assert card["tldr"] == "tldr-cs"
    assert card["github_url"].endswith("/HippoRAG")



