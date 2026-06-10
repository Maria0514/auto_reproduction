"""演示驱动：mock controller 吐假 payload，渲染新版 plan_review（shadcn 版）。"""
import sys
from unittest.mock import MagicMock
import streamlit as st

# mock app._get_controller
import app as _app

_PAYLOAD = {
    "reproduction_plan": {
        "plan_summary": "基于官方仓库 use_repo 策略复现 MoE-Lite 核心训练流程，搭建稀疏门控 MoE 层与轻量化负载均衡损失，在 WikiText-103 上对比稠密基线收敛速度，验证约 30% 加速。",
        "code_strategy": "use_repo",
        "estimated_time": "6~8 小时",
        "environment": {"python": "3.11", "cuda": "12.1", "torch": "2.3.0"},
        "data_preparation": ["下载 WikiText-103 数据集", "分词与预处理", "划分 train/valid/test"],
        "execution_steps": [
            {"step_name": "克隆仓库搭环境", "command": "git clone ... && pip install -r requirements.txt", "expected_output": "环境就绪"},
            {"step_name": "训练 MoE-Lite", "command": "python train.py --config moe_lite.yaml", "expected_output": "收敛曲线"},
            {"step_name": "对比稠密基线", "command": "python train.py --config dense.yaml", "expected_output": "对比报告"},
        ],
        "expected_results": {"speedup": "~30%", "perplexity": "对齐论文 Table 2"},
        "deliverables": ["训练脚本与配置", "收敛曲线对比图", "复现报告 Markdown"],
    },
    "resource_info": {
        "resource_strategy": "use_repo",
        "selected_repo": {"url": "github.com/example/moe-lite"},
        "repos": [
            {"url": "github.com/example/moe-lite", "source": "official", "is_official": True,
             "stars": 1843, "forks": 211, "quality_score": 0.92, "has_readme": True, "has_requirements": True,
             "last_commit_date": "2026-05-20"},
            {"url": "github.com/community/moe-reproduce", "source": "community", "is_official": False,
             "stars": 132, "forks": 18, "quality_score": 0.71, "has_readme": True, "has_requirements": False},
        ],
    },
    "paper_analysis_summary": {"title": "MoE-Lite", "task_type": "训练加速"},
    "revise_count": 1, "soft_hint_threshold": 5, "max_total_llm_calls": 50,
    "degraded_nodes": ["resource_scout"], "node_errors": [],
}

_ctrl = MagicMock()
_ctrl.get_interrupt_payload.return_value = _PAYLOAD
_app._get_controller = lambda: _ctrl

st.session_state.setdefault("thread_id", "demo-thread-001")
st.session_state.setdefault("current_page", "review")

from ui.pages.plan_review import render
render()
