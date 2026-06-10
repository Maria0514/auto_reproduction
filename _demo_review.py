"""临时演示入口：用假 controller 渲染计划审核页 (D5)，仅供截图预览。

    .venv/bin/streamlit run _demo_review.py --server.headless true --server.port 8520

不依赖真实 LLM / 凭证。通过 monkeypatch app._get_controller 注入一个
吐出逼真假 payload 的 FakeController，再把 current_page 设为 "review"。
"""
import streamlit as st

import app as _app


# ---- 一份逼真的假 interrupt payload ----
_FAKE_PAYLOAD = {
    "reproduction_plan": {
        "plan_summary": "复现论文《MoE-Lite: 稀疏专家模型的高效训练》的核心训练与评测流程，"
        "在单卡 A100 上跑通 small 配置并复现表3的困惑度指标。",
        "code_strategy": "use_repo",
        "estimated_time": "约 6~8 小时（含环境搭建与一次完整小规模训练）",
        "environment": {
            "python": "3.11",
            "cuda": "12.1",
            "key_deps": ["torch==2.3.0", "transformers==4.41", "datasets", "deepspeed"],
        },
        "data_preparation": [
            "下载 WikiText-103 数据集并解压到 data/wikitext-103/",
            "运行 scripts/preprocess.py 生成分词缓存",
            "校验 token 总数与论文附录A一致（约 1.03 亿）",
        ],
        "execution_steps": [
            {
                "step_name": "搭建环境",
                "command": "pip install -r requirements.txt",
                "expected_output": "所有依赖安装成功，无版本冲突",
            },
            {
                "step_name": "预处理数据",
                "command": "python scripts/preprocess.py --config configs/small.yaml",
                "expected_output": "生成 data/cache/*.bin，日志打印 token 统计",
            },
            {
                "step_name": "训练 small 模型",
                "command": "python train.py --config configs/small.yaml --max_steps 20000",
                "expected_output": "训练 loss 收敛至 ~3.1，checkpoint 落盘",
            },
            {
                "step_name": "评测困惑度",
                "command": "python eval.py --ckpt out/small/best.pt",
                "expected_output": "test PPL ≈ 22.4（对应论文表3 small 行）",
            },
        ],
        "expected_results": {
            "test_perplexity": "≈ 22.4",
            "对照": "论文表3 small 配置 PPL=22.1（允许 ±1.0 误差）",
        },
        "estimated_time_detail": "训练约5h + 评测0.5h",
        "deliverables": [
            "可复现的训练脚本与配置",
            "训练好的 small checkpoint",
            "复现实验报告（含 PPL 对照表）",
        ],
        "user_feedback": None,
        "approved": False,
    },
    "resource_info": {
        "repos": [
            {
                "url": "https://github.com/example/moe-lite",
                "source": "github",
                "is_official": True,
                "stars": 1843,
                "forks": 211,
                "last_commit_date": "2026-04-18",
                "commit_count_recent": 37,
                "has_readme": True,
                "has_requirements": True,
                "quality_score": 0.92,
                "local_path": "/tmp/repos/moe-lite",
            },
            {
                "url": "https://github.com/community/moe-lite-reproduce",
                "source": "github",
                "is_official": False,
                "stars": 156,
                "forks": 23,
                "last_commit_date": "2026-02-09",
                "commit_count_recent": 8,
                "has_readme": True,
                "has_requirements": False,
                "quality_score": 0.61,
                "local_path": "/tmp/repos/moe-lite-reproduce",
            },
        ],
        "selected_repo": {
            "url": "https://github.com/example/moe-lite",
            "source": "github",
            "is_official": True,
            "stars": 1843,
            "forks": 211,
            "quality_score": 0.92,
        },
        "external_resources": [
            {"name": "WikiText-103", "url": "https://example.com/wikitext-103"},
        ],
        "resource_strategy": "优先使用官方仓库 + 官方数据集，社区复现仓作为交叉参考。",
    },
    "paper_analysis_summary": {
        "method_summary": "提出稀疏门控的轻量 MoE 层，仅激活 top-2 专家以降低算力。",
        "datasets": ["WikiText-103"],
        "metrics": ["Perplexity"],
        "framework": "PyTorch + DeepSpeed",
    },
    "degraded_nodes": ["resource_scout（GitHub API 限流，已用缓存结果降级）"],
    "node_errors": [
        "planning: 第1次 LLM 调用超时，已重试成功",
    ],
    "revise_count": 1,
    "soft_hint_threshold": 5,
    "max_total_llm_calls": 50,
}


class _FakeController:
    def get_interrupt_payload(self, thread_id):
        return _FAKE_PAYLOAD

    def is_interrupted(self, thread_id):
        return True

    def resume_with(self, thread_id, resume_payload):
        st.session_state["_demo_last_action"] = ("resume_with", resume_payload)

    def cancel_task(self, thread_id):
        st.session_state["_demo_last_action"] = ("cancel_task", None)


def _fake_get_controller():
    return _FakeController()


# 注入假 controller（plan_review.py 通过 from app import _get_controller 取）
_app._get_controller = _fake_get_controller

# 设好审核页所需的 session_state
st.set_page_config(page_title="计划审核页预览", layout="wide")
st.session_state.setdefault("thread_id", "demo-thread-001")
st.session_state.setdefault("current_page", "review")

# ---- 主题 CSS 注入（不改 plan_review.py 源码，仅演示层套样式）----
import os as _os  # noqa: E402

_THEMES = {
    "linear": """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600&display=swap');
:root{--bg:#08090a;--panel:#0f1011;--surf:#191a1b;--surf2:#28282c;
 --txt:#f7f8f8;--txt2:#d0d6e0;--mut:#8a8f98;--accent:#7170ff;--brand:#5e6ad2;
 --bd:rgba(255,255,255,.08);--bds:rgba(255,255,255,.05);}
html,body,[data-testid="stAppViewContainer"],.stApp{background:var(--bg)!important;}
[data-testid="stAppViewContainer"] *{font-family:'Inter',system-ui,sans-serif!important;}
[data-testid="stHeader"]{background:transparent!important;}
.block-container{padding-top:2.2rem;max-width:1100px;}
h1,h2,h3{color:var(--txt)!important;letter-spacing:-.03em;font-weight:600!important;}
h1{font-size:2rem!important;}
p,span,li,label,div{color:var(--txt2);}
[data-testid="stMarkdownContainer"] p{color:var(--txt2)!important;}
/* 卡片：expander / container */
[data-testid="stExpander"],div[data-testid="stVerticalBlockBorderWrapper"]{
 background:rgba(255,255,255,.02)!important;border:1px solid var(--bd)!important;
 border-radius:12px!important;}
[data-testid="stExpander"] summary{color:var(--txt)!important;font-weight:510!important;}
/* metric */
[data-testid="stMetric"]{background:var(--surf)!important;border:1px solid var(--bd);
 border-radius:10px;padding:14px 16px;}
[data-testid="stMetricValue"]{color:var(--txt)!important;}
[data-testid="stMetricLabel"]{color:var(--mut)!important;}
/* 按钮 */
.stButton>button{background:rgba(255,255,255,.04)!important;color:var(--txt)!important;
 border:1px solid var(--bd)!important;border-radius:6px!important;font-weight:510!important;}
.stButton>button:hover{background:rgba(255,255,255,.08)!important;border-color:var(--accent)!important;}
.stButton>button[kind="primary"]{background:var(--brand)!important;color:#fff!important;border:none!important;}
.stButton>button[kind="primary"]:hover{background:var(--accent)!important;}
code,pre{background:#0c0d0e!important;border:1px solid var(--bds)!important;
 border-radius:6px!important;color:#c8cdd6!important;}
[data-testid="stAlert"]{border-radius:8px!important;}
hr{border-color:var(--bds)!important;}
</style>
""",
    "stripe": """
<style>
@import url('https://fonts.googleapis.com/css2?family=Source+Sans+3:wght@300;400;500;600&display=swap');
:root{--bg:#ffffff;--ink:#061b31;--body:#64748d;--label:#273951;
 --brand:#533afd;--brandh:#4434d4;--bd:#e5edf5;
 --shadow:rgba(50,50,93,.15) 0px 18px 36px -18px, rgba(0,0,0,.07) 0px 10px 24px -12px;}
html,body,[data-testid="stAppViewContainer"],.stApp{background:var(--bg)!important;}
[data-testid="stAppViewContainer"] *{font-family:'Source Sans 3',system-ui,sans-serif!important;}
[data-testid="stHeader"]{background:transparent!important;}
.block-container{padding-top:2.2rem;max-width:1080px;}
h1,h2,h3{color:var(--ink)!important;font-weight:300!important;letter-spacing:-.02em;}
h1{font-size:2.1rem!important;}
p,span,li,div{color:var(--body);}
[data-testid="stMarkdownContainer"] p{color:var(--body)!important;}
label,[data-testid="stMetricLabel"]{color:var(--label)!important;}
[data-testid="stExpander"],div[data-testid="stVerticalBlockBorderWrapper"]{
 background:#fff!important;border:1px solid var(--bd)!important;border-radius:8px!important;
 box-shadow:var(--shadow)!important;}
[data-testid="stExpander"] summary{color:var(--ink)!important;font-weight:500!important;}
[data-testid="stMetric"]{background:#fff!important;border:1px solid var(--bd);border-radius:8px;
 padding:14px 16px;box-shadow:var(--shadow);}
[data-testid="stMetricValue"]{color:var(--ink)!important;font-weight:300!important;}
.stButton>button{background:#fff!important;color:var(--brand)!important;
 border:1px solid #b9b9f9!important;border-radius:4px!important;font-weight:400!important;}
.stButton>button:hover{background:rgba(83,58,253,.05)!important;}
.stButton>button[kind="primary"]{background:var(--brand)!important;color:#fff!important;border:none!important;}
.stButton>button[kind="primary"]:hover{background:var(--brandh)!important;}
code,pre{background:#f6f9fc!important;border:1px solid var(--bd)!important;border-radius:4px!important;color:#0d253d!important;}
[data-testid="stAlert"]{border-radius:6px!important;}
hr{border-color:var(--bd)!important;}
</style>
""",
    "notion": """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
:root{--bg:#ffffff;--warm:#f6f5f4;--ink:rgba(0,0,0,.95);--body:#615d59;--mut:#a39e98;
 --blue:#0075de;--blueh:#005bab;--bd:rgba(0,0,0,.1);
 --shadow:rgba(0,0,0,.04) 0px 4px 18px, rgba(0,0,0,.02) 0px 0.8px 2.9px;}
html,body,[data-testid="stAppViewContainer"],.stApp{background:var(--bg)!important;}
[data-testid="stAppViewContainer"] *{font-family:'Inter',system-ui,sans-serif!important;}
[data-testid="stHeader"]{background:transparent!important;}
.block-container{padding-top:2.2rem;max-width:1100px;}
h1,h2,h3{color:var(--ink)!important;font-weight:700!important;letter-spacing:-.03em;}
h1{font-size:2.1rem!important;}
p,span,li,div{color:var(--ink);}
[data-testid="stMarkdownContainer"] p{color:var(--body)!important;}
label,[data-testid="stMetricLabel"]{color:var(--body)!important;}
[data-testid="stExpander"],div[data-testid="stVerticalBlockBorderWrapper"]{
 background:var(--warm)!important;border:1px solid var(--bd)!important;border-radius:12px!important;
 box-shadow:var(--shadow)!important;}
[data-testid="stExpander"] summary{color:var(--ink)!important;font-weight:600!important;}
[data-testid="stMetric"]{background:var(--warm)!important;border:1px solid var(--bd);border-radius:12px;
 padding:14px 16px;}
[data-testid="stMetricValue"]{color:var(--ink)!important;font-weight:700!important;}
.stButton>button{background:rgba(0,0,0,.05)!important;color:var(--ink)!important;
 border:1px solid var(--bd)!important;border-radius:6px!important;font-weight:600!important;}
.stButton>button:hover{background:rgba(0,0,0,.08)!important;}
.stButton>button[kind="primary"]{background:var(--blue)!important;color:#fff!important;border:none!important;}
.stButton>button[kind="primary"]:hover{background:var(--blueh)!important;}
code,pre{background:var(--warm)!important;border:1px solid var(--bd)!important;border-radius:6px!important;color:#31302e!important;}
[data-testid="stAlert"]{border-radius:8px!important;}
hr{border-color:var(--bd)!important;}
</style>
""",
    "coinbase": """
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&display=swap');
:root{--bg:#ffffff;--ink:#0a0b0d;--body:#5b616e;--surf:#eef0f3;
 --blue:#0052ff;--blueh:#578bfa;--bd:rgba(91,97,110,.2);
 --shadow:rgba(10,11,13,.06) 0px 8px 24px -8px;}
html,body,[data-testid="stAppViewContainer"],.stApp{background:var(--bg)!important;}
[data-testid="stAppViewContainer"] *{font-family:'DM Sans',system-ui,sans-serif!important;}
[data-testid="stHeader"]{background:transparent!important;}
.block-container{padding-top:2.2rem;max-width:1100px;}
h1,h2,h3{color:var(--blue)!important;font-weight:700!important;letter-spacing:-.02em;}
h1{font-size:2.1rem!important;color:var(--ink)!important;}
p,span,li,div{color:var(--body);}
[data-testid="stMarkdownContainer"] p{color:var(--body)!important;}
label,[data-testid="stMetricLabel"]{color:var(--ink)!important;font-weight:600!important;}
[data-testid="stExpander"],div[data-testid="stVerticalBlockBorderWrapper"]{
 background:#fff!important;border:1px solid var(--bd)!important;border-radius:16px!important;
 box-shadow:var(--shadow)!important;}
[data-testid="stExpander"] summary{color:var(--ink)!important;font-weight:600!important;}
[data-testid="stMetric"]{background:var(--surf)!important;border:1px solid var(--bd);border-radius:16px;
 padding:14px 16px;}
[data-testid="stMetricValue"]{color:var(--blue)!important;font-weight:700!important;}
.stButton>button{background:var(--surf)!important;color:var(--ink)!important;
 border:1px solid var(--bd)!important;border-radius:56px!important;font-weight:600!important;}
.stButton>button:hover{background:#e2e6ec!important;}
.stButton>button[kind="primary"]{background:var(--blue)!important;color:#fff!important;border:none!important;border-radius:56px!important;}
.stButton>button[kind="primary"]:hover{background:var(--blueh)!important;}
code,pre{background:var(--surf)!important;border:1px solid var(--bd)!important;border-radius:8px!important;color:#0a0b0d!important;}
[data-testid="stAlert"]{border-radius:12px!important;}
hr{border-color:var(--bd)!important;}
</style>
""",
    "ibm": """
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@300;400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap');
:root{--bg:#ffffff;--ink:#161616;--body:#525252;--surf:#f4f4f4;--surf2:#e8e8e8;
 --blue:#0f62fe;--blueh:#0353e9;--bd:#c6c6c6;}
html,body,[data-testid="stAppViewContainer"],.stApp{background:var(--bg)!important;}
[data-testid="stAppViewContainer"] *{font-family:'IBM Plex Sans',system-ui,sans-serif!important;}
[data-testid="stHeader"]{background:transparent!important;}
.block-container{padding-top:2.2rem;max-width:1100px;}
h1,h2,h3{color:var(--ink)!important;font-weight:300!important;letter-spacing:0;}
h1{font-size:2.2rem!important;}
h3{font-weight:600!important;}
p,span,li,div{color:var(--body);}
[data-testid="stMarkdownContainer"] p{color:var(--body)!important;}
label,[data-testid="stMetricLabel"]{color:var(--body)!important;}
[data-testid="stExpander"],div[data-testid="stVerticalBlockBorderWrapper"]{
 background:var(--surf)!important;border:none!important;border-radius:0!important;}
[data-testid="stExpander"] summary{color:var(--ink)!important;font-weight:600!important;}
[data-testid="stMetric"]{background:var(--surf)!important;border:none;border-radius:0;
 padding:14px 16px;border-left:3px solid var(--blue);}
[data-testid="stMetricValue"]{color:var(--ink)!important;font-weight:400!important;}
.stButton>button{background:var(--surf2)!important;color:var(--ink)!important;
 border:none!important;border-radius:0!important;font-weight:400!important;}
.stButton>button:hover{background:#d1d1d1!important;}
.stButton>button[kind="primary"]{background:var(--blue)!important;color:#fff!important;border:none!important;border-radius:0!important;}
.stButton>button[kind="primary"]:hover{background:var(--blueh)!important;}
code,pre{background:var(--surf)!important;border:none!important;border-radius:0!important;color:#161616!important;font-family:'IBM Plex Mono',monospace!important;}
[data-testid="stAlert"]{border-radius:0!important;}
hr{border-color:var(--bd)!important;}
</style>
""",
}

_theme = _os.environ.get("DEMO_THEME", "").strip().lower()
if _theme in _THEMES:
    st.markdown(_THEMES[_theme], unsafe_allow_html=True)

# 演示提示条
last = st.session_state.get("_demo_last_action")
if last:
    st.success(f"（演示）已捕获决策动作：{last[0]} -> {last[1]}")

from ui.pages.plan_review import render  # noqa: E402

render()
