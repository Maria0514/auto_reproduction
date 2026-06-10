"""paper_input 卡片化视觉验收 demo：mock 上游让页面渲染出论文卡片+搜索结果。"""
import streamlit as st
import ui.pages.paper_input as page
from core.state import LLMConfigSet


def _fake_render_llm_form(default=None):
    cfg = {
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o",
        "api_key": "sk-demo",
        "temperature": 0.0,
        "max_tokens": 4096,
    }
    return {"default": cfg, "overrides": {}}


page.render_llm_config_form = _fake_render_llm_form

# 预置一张论文卡片 + 搜索结果（让搜索 expander 展开后非空）
st.session_state.setdefault("_input_paper_card", {
    "title": "HippoRAG: Neurobiologically Inspired Long-Term Memory for LLMs",
    "authors": ["Bernal Jiménez", "Yiheng Shu", "Yu Su"],
    "categories": ["cs.CL", "cs.AI"],
    "tldr": "受海马体索引理论启发的检索框架，单步多跳检索。",
    "abstract": "We propose HippoRAG, a novel retrieval framework inspired by "
                "the hippocampal indexing theory of human long-term memory ...",
    "github_url": "https://github.com/OSU-NLP-Group/HippoRAG",
})
st.session_state.setdefault("_input_search_results", [
    {"arxiv_id": "2405.14831", "title": "HippoRAG: Neurobiologically Inspired Long-Term Memory for LLMs"},
    {"arxiv_id": "2310.11511", "title": "Self-RAG: Learning to Retrieve, Generate, and Critique through Self-Reflection"},
    {"arxiv_id": "2401.18059", "title": "RAPTOR: Recursive Abstractive Processing for Tree-Organized Retrieval"},
])
st.session_state.setdefault("arxiv_id_input", "2405.14831")

page.render()
