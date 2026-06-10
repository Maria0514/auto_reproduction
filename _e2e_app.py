"""e2e harness app：用文件落盘记录 controller 调用，供 Playwright 测试读盘断言。"""
import json
import os
from unittest.mock import MagicMock
import streamlit as st
import app as _app

REC = os.environ.get("E2E_REC", "/tmp/e2e_calls.jsonl")

_PAYLOAD = {
    "reproduction_plan": {"plan_summary": "demo", "code_strategy": "use_repo",
        "execution_steps": [{"step_name": "a", "command": "b", "expected_output": "c"}]},
    "resource_info": {"resource_strategy": "use_repo",
        "selected_repo": {"url": "github.com/x/y"},
        "repos": [{"url": "github.com/x/y", "is_official": True, "stars": 10, "forks": 2, "quality_score": 0.9}]},
    "revise_count": 0, "soft_hint_threshold": 5,
}

class RecCtrl:
    def get_interrupt_payload(self, tid):
        return _PAYLOAD
    def resume_with(self, tid, decision):
        with open(REC, "a") as f:
            f.write(json.dumps({"m": "resume_with", "tid": tid, "decision": decision}) + "\n")
    def cancel_task(self, tid):
        with open(REC, "a") as f:
            f.write(json.dumps({"m": "cancel_task", "tid": tid}) + "\n")

_app._get_controller = lambda: RecCtrl()
st.session_state.setdefault("thread_id", "tid-e2e")
st.session_state.setdefault("current_page", "review")

from ui.pages.plan_review import render
render()
