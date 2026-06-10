"""analysis_progress 卡片化视觉验收 demo：mock controller 吐一个正常运行态 state。"""
import streamlit as st
import app as _app


class DemoCtrl:
    def get_worker_error(self, tid):
        return None

    def is_interrupted(self, tid):
        return False

    def poll_state(self, tid):
        return {
            "current_step": "resource_scout",  # 进行到第3段
            "degraded_nodes": [],
            "paper_meta": {
                "title": "HippoRAG: Neurobiologically Inspired Long-Term Memory for LLMs",
                "title_zh": "HippoRAG：神经生物学启发的大模型长期记忆",
                "arxiv_id": "2405.14831",
                "authors": ["Bernal Jiménez", "Yiheng Shu", "Yu Su"],
                "tldr": "A retrieval framework inspired by the hippocampal indexing theory.",
                "tldr_zh": "受海马体索引理论启发的检索框架，单步多跳检索。",
                "abstract": "We propose HippoRAG, a novel retrieval framework inspired by "
                            "the hippocampal indexing theory of human long-term memory ...",
            },
            "node_errors": [
                {"node_name": "resource_scout", "error_type": "RateLimit",
                 "error_message": "GitHub API 限流，已退避重试", "error_detail": "HTTP 403 rate limited"},
                {"node_name": "paper_analysis", "error_type": "",
                 "error_message": "摘要解析完成，提取 4 个关键实验", "error_detail": None},
            ],
        }


_app._get_controller = lambda: DemoCtrl()
st.session_state.setdefault("thread_id", "demo-progress")
st.session_state.setdefault("current_page", "progress")

from ui.pages.analysis_progress import render
render()
