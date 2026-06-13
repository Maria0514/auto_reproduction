"""S2-13 e2e harness app：注入 switch_repo_failed / stars-forks=None 的 interrupt payload。

通过 env 控制 payload 变体（不依赖真实 LLM / 真实 git clone，只验证 UI 可观测行为）：
  - E2E_S2_13_VARIANT=switch_failed : payload.switch_repo_failed=True（强制重填提示）
  - E2E_S2_13_VARIANT=ok            : switch_repo_failed=False，候选含 stars/forks=None、真实 quality_score
controller 调用同样落盘到 E2E_REC 供读盘断言（沿用 _e2e_app.py 范式）。
"""
import json
import os
from unittest.mock import MagicMock  # noqa: F401  (沿用范式，保持一致)

import streamlit as st
import app as _app

REC = os.environ.get("E2E_REC", "/tmp/e2e_s2_13_calls.jsonl")
VARIANT = os.environ.get("E2E_S2_13_VARIANT", "switch_failed")

# 候选仓库：stars/forks 恒 None（AC-S2-23），quality_score 为真实非 0 值（AC-S2-21）。
_REPO_USER_PROVIDED = {
    "url": "https://github.com/user/realrepo",
    "source": "user_provided",
    "is_official": True,
    "stars": None,
    "forks": None,
    "last_commit_date": None,
    "commit_count_recent": None,
    "has_readme": True,
    "has_requirements": True,
    "dir_structure": ["src"],
    "quality_score": 0.78,
    "local_path": "/ws/repos/realrepo",
}

_PLAN = {
    "plan_summary": "demo plan",
    "code_strategy": "use_repo",
    "environment": {},
    "data_preparation": [],
    "execution_steps": [{"step_name": "a", "command": "b", "expected_output": "c"}],
    "expected_results": {},
    "estimated_time": "",
    "deliverables": ["README.md"],
}

if VARIANT == "switch_failed":
    _RESOURCE_INFO = {
        "resource_strategy": "from_scratch",
        "selected_repo": None,
        "repos": [],
        "external_resources": [],
    }
    _SWITCH_FAILED = True
else:  # "ok"
    _RESOURCE_INFO = {
        "resource_strategy": "use_repo",
        "selected_repo": _REPO_USER_PROVIDED,
        "repos": [_REPO_USER_PROVIDED],
        "external_resources": [],
    }
    _SWITCH_FAILED = False

_PAYLOAD = {
    "reproduction_plan": _PLAN,
    "resource_info": _RESOURCE_INFO,
    "paper_analysis_summary": {"method_summary": "m"},
    "degraded_nodes": [],
    "node_errors": [],
    "revise_count": 1,
    "soft_hint_threshold": 5,
    "max_total_llm_calls": 50,
    "switch_repo_failed": _SWITCH_FAILED,
}

_LLM_CONFIG = {
    "base_url": "https://example.test/v1", "model": "gpt-test",
    "api_key": "", "temperature": 0.3, "max_tokens": 4096,
}
_LLM_CONFIG_SET = {"default": _LLM_CONFIG, "overrides": {}}


class RecCtrl:
    def get_interrupt_payload(self, tid):
        return _PAYLOAD

    def poll_state(self, tid):
        return {"llm_config_set": _LLM_CONFIG_SET}

    def is_interrupted(self, tid):
        return True

    def get_worker_error(self, tid):
        return None

    def resume_with(self, tid, decision):
        with open(REC, "a") as f:
            f.write(json.dumps({"m": "resume_with", "tid": tid, "decision": decision}) + "\n")

    def cancel_task(self, tid):
        with open(REC, "a") as f:
            f.write(json.dumps({"m": "cancel_task", "tid": tid}) + "\n")


_app._get_controller = lambda: RecCtrl()
st.session_state.setdefault("thread_id", "tid-e2e-s2-13")
st.session_state.setdefault("current_page", "review")

from ui.pages.plan_review import render  # noqa: E402
render()
