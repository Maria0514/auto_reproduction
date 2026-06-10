"""e2e harness app（paper_input 页）：mock controller + DeepxivTools，落盘 jsonl 供 Playwright 断言。

设计同根 _e2e_app.py：
- DeepxivTools 经替换 ui.pages.paper_input.DeepxivTools 名称为 _RecDeepxivTools 类（落盘 + 按 KIND 切场景）；
- controller 经 monkeypatch app._get_controller=lambda: _RecCtrl()（落盘 start_task 调用）；
- 任意 mock 调用 append 一行 JSON 到 E2E_REC（路径由测试通过 env 注入）。

env 控制：
- E2E_REC：落盘 jsonl 路径（必填）。
- E2E_PAPER_KIND（默认 "cs"）：
  - "cs"        → CS 论文 brief/head（含 cs.* 分类）；
  - "noncs"     → 非 CS 论文（categories=["math.AP"]）；
  - "head_fail" → brief 正常，head 抛 TransientError；
  - "brief_fail"→ brief 抛 PermanentError；
  - "search"    → search_papers 返回 2 条（其余走 cs 路径）。
- E2E_PREFILL_ARXIV：arxiv_id_input widget 初值（widget 实例化前写 key 合法）。
- E2E_CFG_NONE=1：不依赖 env 默认 cfg；测试需要「cfg=None」分支时设此环境清空配置。

D1 cfg 默认值依赖：default panel 在无 prefill 时用 config.get_llm_base_url/get_llm_model
作 value= 预填；api_key 留空时由 create_llm 回退 env LLM_API_KEY。在 conftest 自动加载
.env 后通常 env 已经齐备，故 harness 默认状态下 cfg 即合法（btn_start 走 ui.button 路径）。
"""
from __future__ import annotations

import json
import os

import streamlit as st

REC = os.environ.get("E2E_REC", "/tmp/e2e_paper_input_calls.jsonl")
KIND = os.environ.get("E2E_PAPER_KIND", "cs")


def _rec(payload: dict) -> None:
    with open(REC, "a") as f:
        f.write(json.dumps(payload) + "\n")


# --------------------------------------------------------------------------- #
# DeepxivTools 替身：按 KIND 提供不同场景，所有调用落盘
# --------------------------------------------------------------------------- #
_BRIEF_CS = {
    "arxiv_id": "2405.14831",
    "title": "HippoRAG: Neurobiologically Inspired Long-Term Memory for LLMs",
    "tldr": "A retrieval framework inspired by the hippocampal indexing theory.",
    "github_url": "https://github.com/OSU-NLP-Group/HippoRAG",
    "keywords": ["RAG", "memory"],
}
_HEAD_CS = {
    "title": "HippoRAG: Neurobiologically Inspired Long-Term Memory for LLMs",
    "abstract": "We introduce HippoRAG, a novel retrieval framework demonstrating ...",
    "authors": ["Bernal Jimenez Gutierrez", "Yiheng Shu", "Yu Su"],
    "categories": ["cs.CL", "cs.AI"],
}
_BRIEF_NONCS = {
    "arxiv_id": "1234.56789",
    "title": "A Theorem in Partial Differential Equations",
    "tldr": None,
    "github_url": None,
    "keywords": [],
}
_HEAD_NONCS = {
    "title": "A Theorem in Partial Differential Equations",
    "abstract": "We prove a new estimate for nonlinear PDE.",
    "authors": ["Jane Mathematician"],
    "categories": ["math.AP"],
}


class _RecDeepxivTools:
    def __init__(self, *args, **kwargs):
        _rec({"m": "DeepxivTools.__init__"})

    def get_paper_brief(self, arxiv_id):
        _rec({"m": "get_paper_brief", "arxiv_id": arxiv_id, "kind": KIND})
        if KIND == "brief_fail":
            from core.errors import PermanentError
            raise PermanentError("paper not found")
        if KIND == "noncs":
            return dict(_BRIEF_NONCS)
        return dict(_BRIEF_CS)

    def get_paper_head(self, arxiv_id):
        _rec({"m": "get_paper_head", "arxiv_id": arxiv_id, "kind": KIND})
        if KIND == "head_fail":
            from core.errors import TransientError
            raise TransientError("head boom")
        if KIND == "noncs":
            return dict(_HEAD_NONCS)
        return dict(_HEAD_CS)

    def search_papers(self, query, size=10):
        _rec({"m": "search_papers", "query": query, "size": size})
        if KIND == "search":
            return [
                {"arxiv_id": "2401.00001", "title": "Paper A"},
                {"id": "2402.00002", "title": "Paper B"},
            ]
        return []


# --------------------------------------------------------------------------- #
# Controller 替身：start_task 落盘 + 返回固定 thread_id
# --------------------------------------------------------------------------- #
class _RecCtrl:
    def start_task(self, arxiv_id, cfg):
        try:
            cfg_default = (cfg or {}).get("default", {}) if hasattr(cfg, "get") else {}
        except Exception:
            cfg_default = {}
        _rec({
            "m": "start_task",
            "arxiv_id": arxiv_id,
            "cfg_default_model": cfg_default.get("model"),
            "cfg_default_api_key": cfg_default.get("api_key"),
        })
        return "task-e2e-001"


import app as _app  # noqa: E402

_app._get_controller = lambda: _RecCtrl()

# 替换页面命名空间里的 DeepxivTools 引用（页面顶层 from ... import DeepxivTools 已固化）。
import ui.pages.paper_input as _paper_input  # noqa: E402

_paper_input.DeepxivTools = _RecDeepxivTools

# --------------------------------------------------------------------------- #
# 预置 session_state（widget 实例化前写 key 合法）
# --------------------------------------------------------------------------- #
_pre_arxiv = os.environ.get("E2E_PREFILL_ARXIV")
if _pre_arxiv:
    st.session_state.setdefault("arxiv_id_input", _pre_arxiv)

# 测试需要 cfg=None 分支时：把 default_base_url/default_model 强制清空（widget 实例化前写 key 作 value 预填覆盖）。
# 注意 D1 widget 用 value= 注入，session_state[key] 预写不会被读取——故只能依赖 env 不清。
# 该分支已被原 test_cp_d3_2 覆盖（logic 层），e2e 不重复处理。

from ui.pages.paper_input import render  # noqa: E402

render()
