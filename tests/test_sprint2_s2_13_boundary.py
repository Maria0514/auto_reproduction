"""S2-13 测试工程师补强边界用例（独立验收 Task B）。

覆盖开发自测（test_sprint2_s2_13.py 27 用例）可能遗漏的边角，所有断言前均已用 python
探针验真行为：
  - _normalize_repo_url 更多等价形态（多重尾斜杠 / 协议大小写 / .git+尾斜杠组合）；
  - _merge_user_repos_from_tools 多仓库混合成功+失败（仅成功入候选）；
  - 漏写 LLM 打分时兜底 _BACKFILL_DEFAULT_QUALITY + 非静默 WARNING；
  - switch_failed 标记清除时机：成功后清、入口 a 失败不置、re-submit 清旧标记；
  - code_strategy 纠正 from_scratch → use_repo；
  - switch_repo resume 非命中 URL 写 pending + 清旧 failed 标记；
  - cancel 路由 current_step=cancelled_by_user。

与开发用例不重复：聚焦多仓库混合、标记生命周期、resume 路由分支与兜底量纲。
"""

from __future__ import annotations

import importlib
import json
import logging
from typing import Any, Dict, List

import pytest
from langchain_core.messages import AIMessage, ToolMessage

import core.react_base as react_base
from core.state import ExecutionMode

P = importlib.import_module("core.nodes.planning")
RS = importlib.import_module("core.nodes.resource_scout")

_normalize = P._normalize_repo_url
_merge = P._merge_user_repos_from_tools
_map = P._map_planning_result


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _succ(url: str, q: float = 0.7) -> Dict[str, Any]:
    return {
        "url": url, "source": "git_clone", "local_path": f"/ws/repos/{url[-3:]}",
        "is_official": True, "stars": None, "forks": None,
        "last_commit_date": None, "commit_count_recent": None,
        "has_readme": True, "has_requirements": True, "dir_structure": ["src"],
        "quality_score": q, "success": True,
    }


def _tm(payload: Dict[str, Any]) -> List[Any]:
    return [
        AIMessage(content="", tool_calls=[
            {"name": "git_clone_and_analyze", "args": {"url": payload.get("url")}, "id": "c"}]),
        ToolMessage(
            content=json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str),
            name="git_clone_and_analyze", tool_call_id="c"),
    ]


def _state(**ov: Any) -> Dict[str, Any]:
    s: Dict[str, Any] = {
        "llm_config_set": {"default": {"base_url": "x", "model": "m", "api_key": "k",
                                       "temperature": 0.0, "max_tokens": 512}, "overrides": {}},
        "paper_meta": {"arxiv_id": "1", "title": "T", "authors": ["X"]},
        "paper_analysis": {"method_summary": "m"},
        "resource_info": {"repos": [], "selected_repo": None,
                          "external_resources": [], "resource_strategy": "from_scratch"},
        "node_errors": [], "degraded_nodes": [], "analysis_notes": "",
        "retry_budget_remaining": 50, "_planning_revise_count": 1,
        "_planning_user_feedback": "", "_planning_pending_repo_url": None,
        "_planning_switch_failed": False, "execution_mode": ExecutionMode.FULL,
        "current_step": "planning",
    }
    s.update(ov)
    return s


def _plan(repos=None, code_strategy="from_scratch") -> Dict[str, Any]:
    return {
        "plan_summary": "摘要", "code_strategy": code_strategy,
        "deliverables": ["README.md"], "execution_steps": [],
        "repos": repos or [],
    }


def _patch_react(monkeypatch, result, messages):
    class FG:
        def invoke(self, init):
            return {"result": result, "messages": messages, "round": 2, "status": "done"}
    monkeypatch.setattr(react_base, "create_react_subgraph", lambda **kw: FG())
    monkeypatch.setattr(react_base, "create_llm", lambda cfg: object())


# =========================================================================== #
# B1：_normalize_repo_url 更多等价形态
# =========================================================================== #
@pytest.mark.parametrize("url", [
    "https://github.com/a/b///",
    "https://github.com/a/b.git///",
    "HTTPS://GitHub.com/A/B",
    "https://GITHUB.com/a/B.git/",
    "  HTTPS://github.com/A/b.git  ",
])
def test_b1_normalize_extra_variants_equal(url):
    """多重尾斜杠 / 协议大小写 / .git+尾斜杠组合 全部归一为同一 key。"""
    assert _normalize(url) == "https://github.com/a/b"


def test_b1_normalize_distinguishes_protocol():
    """http vs https 保守不归一（只做大小写/尾斜杠/.git，不做协议等价语义）。"""
    assert _normalize("http://github.com/a/b") != _normalize("https://github.com/a/b")


def test_b1_normalize_distinguishes_different_repos():
    assert _normalize("https://github.com/a/b") != _normalize("https://github.com/a/c")


# =========================================================================== #
# B2：多仓库混合成功+失败 —— 仅成功入候选
# =========================================================================== #
def test_b2_mixed_success_fail_only_success_merged():
    ok = "https://github.com/u/ok"
    bad = "https://github.com/u/bad"
    msgs = _tm(_succ(ok, 0.6)) + _tm({"url": bad, "success": False, "error": "boom"})
    state = _state()
    r = _merge(_plan(repos=[{"url": ok, "quality_score": 0.6}]), msgs, state)
    urls = {_normalize(x["url"]) for x in r["resource_info"]["repos"]}
    assert r["merged"] is True
    assert _normalize(ok) in urls
    assert _normalize(bad) not in urls  # 失败仓库不入候选
    # 选中为成功仓库
    assert _normalize(r["resource_info"]["selected_repo"]["url"]) == _normalize(ok)


def test_b2_two_success_both_merged_last_selected():
    """两个成功仓库都入候选；默认选中最后合并的那条（Q-S2-07 最新优先）。"""
    a = "https://github.com/u/aaa"
    b = "https://github.com/u/bbb"
    msgs = _tm(_succ(a, 0.5)) + _tm(_succ(b, 0.8))
    r = _merge(_plan(repos=[{"url": a, "quality_score": 0.5}, {"url": b, "quality_score": 0.8}]),
               msgs, _state())
    urls = {_normalize(x["url"]) for x in r["resource_info"]["repos"]}
    assert _normalize(a) in urls and _normalize(b) in urls
    assert _normalize(r["resource_info"]["selected_repo"]["url"]) == _normalize(b)


# =========================================================================== #
# B3：漏写 LLM 打分 → 兜底 _BACKFILL_DEFAULT_QUALITY + 非静默 WARNING
# =========================================================================== #
def test_b3_missing_llm_score_backfill_default_with_warning(caplog):
    """模型把仓库放进工具历史但漏写进 result.repos（无 LLM 评分）→ 兜底默认值 + WARNING。"""
    url = "https://github.com/u/noscore"
    msgs = _tm(_succ(url, 0.0))  # tool 产出 quality_score=0.0（视为缺失）
    state = _state()
    with caplog.at_level(logging.WARNING):
        # plan.result.repos 不含该 URL → 无模型分
        r = _merge(_plan(repos=[]), msgs, state)
    sel = r["resource_info"]["selected_repo"]
    assert sel["quality_score"] == RS._BACKFILL_DEFAULT_QUALITY
    assert sel["quality_score"] != 0.0  # 同口径量纲，非 0（AC-S2-22）
    # 非静默：WARNING 日志出现（BUG-S1-02 治理范式）
    assert any("quality_score" in rec.message or "fallback" in rec.message.lower()
               for rec in caplog.records)


# =========================================================================== #
# B4：switch_failed 标记生命周期
# =========================================================================== #
def test_b4_success_clears_prior_switch_failed():
    """入口 b 成功合并后清除上一轮失败标记 + pending_url。"""
    url = "https://github.com/o/r"
    msgs = _tm(_succ(url, 0.9))
    state = _state(_planning_pending_repo_url=url, _planning_switch_failed=True)
    out = _map(_plan(repos=[{"url": url, "quality_score": 0.9}]), state, react_messages=msgs)
    assert out["_planning_switch_failed"] is False
    assert out["_planning_pending_repo_url"] is None


def test_b4_entry_a_failure_does_not_set_switch_failed():
    """入口 a（revise，无 pending_url）clone 失败：不置 switch_failed（AC-S2-25②）。"""
    msgs = _tm({"url": "https://github.com/x/y", "success": False, "error": "boom"})
    out = _map(_plan(), _state(_planning_pending_repo_url=None), react_messages=msgs)
    assert "_planning_switch_failed" not in out  # 入口 a 全程不碰此标记


def test_b4_entry_b_failure_sets_switch_failed_no_zero_placeholder():
    """入口 b clone 失败：置 switch_failed=True，且不写 0.0 占位 RepoInfo。"""
    msgs = _tm({"url": "https://github.com/u/v", "success": False, "error": "boom"})
    out = _map(_plan(), _state(_planning_pending_repo_url="https://github.com/u/v"),
               react_messages=msgs)
    assert out["_planning_switch_failed"] is True
    assert out["_planning_pending_repo_url"] is None
    # 不写 resource_info / 不留 0.0 占位候选
    assert "resource_info" not in out or not out["resource_info"].get("repos")


def test_b4_resubmit_switch_repo_clears_stale_failed_flag(monkeypatch):
    """re-submit 新 switch_repo（非命中 URL）→ 写 pending_url + 清旧 failed 标记。"""
    state = _state(_planning_switch_failed=True, _planning_revise_count=2)
    _patch_react(monkeypatch, _plan(), [])
    monkeypatch.setattr(P, "interrupt", lambda payload: {
        "decision": "switch_repo", "new_repo_url": "https://github.com/new/repo",
        "user_feedback": "use this"})
    out = P.planning(state)
    assert out["_planning_pending_repo_url"] == "https://github.com/new/repo"
    assert out["_planning_switch_failed"] is False  # re-submit 清旧标记
    assert out["_planning_revise_count"] == 3


# =========================================================================== #
# B5：code_strategy 纠正 from_scratch → use_repo
# =========================================================================== #
def test_b5_code_strategy_corrected_to_use_repo():
    url = "https://github.com/u/v"
    msgs = _tm(_succ(url, 0.7))
    out = _map(_plan(repos=[{"url": url, "quality_score": 0.7}], code_strategy="from_scratch"),
               _state(_planning_user_feedback=f"用 {url}"), react_messages=msgs)
    assert out["reproduction_plan"]["code_strategy"] == "use_repo"
    assert out["resource_info"]["resource_strategy"] == "use_repo"


def test_b5_no_merge_keeps_from_scratch():
    """无任何成功合并时 code_strategy 不被强行改写（保持模型产出）。"""
    msgs = [AIMessage(content="无相关仓库")]  # 无工具调用
    out = _map(_plan(code_strategy="from_scratch"), _state(), react_messages=msgs)
    assert out["reproduction_plan"]["code_strategy"] == "from_scratch"
    assert "resource_info" not in out  # 未合并不回写


# =========================================================================== #
# B6：switch_repo resume 命中既有候选 → 确定性选中（不重抓、不写 pending）
# =========================================================================== #
def test_b6_switch_repo_hit_existing_no_pending(monkeypatch):
    existing = "https://github.com/a/b"
    state = _state(resource_info={
        "repos": [{"url": existing, "quality_score": 0.85, "source": "pwc"}],
        "selected_repo": None, "external_resources": [], "resource_strategy": "use_repo"})
    _patch_react(monkeypatch, _plan(), [])
    monkeypatch.setattr(P, "interrupt", lambda payload: {
        "decision": "switch_repo", "new_repo_url": "https://github.com/A/B.git/"})
    out = P.planning(state)
    # 命中既有候选：直接选中，清失败/ pending（无需重抓）
    assert out["resource_info"]["selected_repo"]["url"] == existing
    assert out["resource_info"]["selected_repo"]["source"] == "pwc"
    assert out["_planning_pending_repo_url"] is None
    assert out["_planning_switch_failed"] is False


# =========================================================================== #
# B7：cancel / approve 路由不被 S2-13 改动破坏
# =========================================================================== #
def test_b7_cancel_routes_to_cancelled(monkeypatch):
    _patch_react(monkeypatch, _plan(), [])
    monkeypatch.setattr(P, "interrupt", lambda payload: {"decision": "cancel"})
    out = P.planning(_state())
    assert out["current_step"] == "cancelled_by_user"


def test_b7_approve_sets_approved(monkeypatch):
    _patch_react(monkeypatch, _plan(), [])
    monkeypatch.setattr(P, "interrupt", lambda payload: {"decision": "approve"})
    out = P.planning(_state())
    assert out["reproduction_plan"]["approved"] is True
