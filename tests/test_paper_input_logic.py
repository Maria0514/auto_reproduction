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


# =========================================================================== #
# BUG-S2-D5-01 回归：btn_start / btn_fetch 双路径不可共享 key
# =========================================================================== #
# 病因：can_start False 时走 st.button(disabled=True, key="btn_start") →
#       session_state["btn_start"] 写 bool；
#       can_start 翻 True → 走 ui.button(key="btn_start")，shadcn 前端读
#       session_state["btn_start"] 期待 dict（{"event_id":...}），
#       拿到 bool 触发 'bool' object is not subscriptable。
# 修复：active 路径改新 key（btn_start_go / btn_fetch_go），
#       disabled 路径保留旧 key 给 AppTest 断言。
# 本测试用源码扫描固化该约束（防回归），不依赖 AppTest / Playwright。
def test_btn_start_active_and_disabled_use_distinct_keys():
    """ui.button(can_start=True 路径) 不得与 st.button(disabled 路径) 共享 key。"""
    import re
    from pathlib import Path

    src = Path("ui/pages/paper_input.py").read_text(encoding="utf-8")

    # 抓 "🚀 开始复现" 周围两个 button 调用的 key=
    # 简单做法：找所有 key="btn_start*" 出现位置，断言至少 2 个不同 key。
    keys_start = set(re.findall(r'key="(btn_start\w*)"', src))
    assert keys_start == {"btn_start_go", "btn_start"}, (
        f"btn_start 双路径必须用不同 key（active=btn_start_go, "
        f"disabled=btn_start），实际：{keys_start}"
    )


def test_btn_fetch_active_and_disabled_use_distinct_keys():
    """ui.button(非 submitted 路径) 不得与 st.button(submitted disabled 路径) 共享 key。"""
    import re
    from pathlib import Path

    src = Path("ui/pages/paper_input.py").read_text(encoding="utf-8")
    keys_fetch = set(re.findall(r'key="(btn_fetch\w*)"', src))
    assert keys_fetch == {"btn_fetch_go", "btn_fetch"}, (
        f"btn_fetch 双路径必须用不同 key（active=btn_fetch_go, "
        f"disabled=btn_fetch），实际：{keys_fetch}"
    )


def test_disabled_path_session_state_does_not_break_active_render():
    """直跑 render：先 disabled 让 st.button 写 bool 进 session_state['btn_start']，
    再翻 active 让 ui.button 走 'btn_start_go' key——前者 bool 不会被后者读到。

    AppTest 看不到 ui.button，所以这里只断言 render 不抛
    'bool' object is not subscriptable（即 bug 中的崩溃路径不复现）。
    """
    from streamlit.testing.v1 import AppTest

    # 第一轮：cfg 留空（侧栏不填）→ can_start False → 走 st.button(disabled, key=btn_start)
    at = AppTest.from_file("ui/pages/paper_input.py", default_timeout=10)
    at.run()
    # disabled 路径渲染过 → session_state['btn_start'] 应为 bool（False）
    # AppTest session_state 是 SafeSessionState，无 .get；用 in / [] 访问。
    if "btn_start" in at.session_state:
        btn_start_val = at.session_state["btn_start"]
        assert isinstance(btn_start_val, bool), (
            f"disabled 路径应写 bool，实际类型：{type(btn_start_val).__name__}"
        )
    assert not at.exception, f"首轮 render 不应抛异常，实际：{at.exception}"

    # 第二轮：模拟 cfg 配齐 + arxiv 有值 → can_start True → 走 ui.button(key=btn_start_go)
    # 关键：session_state['btn_start'] 此时仍为上一轮 bool；
    # 修复前 ui.button 读 session_state['btn_start'] 期待 dict → 'bool' object is not subscriptable；
    # 修复后 active 路径换 key 'btn_start_go' → 上一轮 bool 不会再被读取。
    at.session_state["arxiv_id_input"] = "2405.14831"
    # 直接模拟 cfg 已合法（render_llm_config_form 写 session_state['llm_config_set']）
    from core.state import LLMConfigSet
    fake_cfg: LLMConfigSet = {
        "default": {
            "base_url": "https://api.example.com/v1",
            "model": "gpt-4",
            "api_key": "sk-test",
            "temperature": 0.3,
            "max_tokens": 8192,
        },
        "overrides": {},
    }
    at.session_state["llm_config_set"] = fake_cfg
    at.run()
    # 关键断言：第二轮（active 分支）渲染不抛 'bool' object is not subscriptable
    if at.exception:
        msgs = [str(e.value) for e in at.exception]
        assert not any("'bool' object is not subscriptable" in m for m in msgs), (
            f"BUG-S2-D5-01 复现：active 分支误读了 disabled 路径写入的 bool —— {msgs}"
        )



