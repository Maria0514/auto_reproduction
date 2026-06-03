"""Sprint 2 任务 B3 自测：core/nodes/planning.py。

覆盖 dev-plan §B3 CP-B3-1 ~ CP-B3-12（pytest 标准函数风格，参考 tests/test_sprint2_b2.py）。

测试策略（mock 覆盖核心路径，不依赖真实网络 / LLM；真实 e2e 留 E 阶段）：
    - 走 ReAct 子图的 CP 通过 monkeypatch core.react_base.create_react_subgraph +
      create_llm 注入脚本化子图结果；
    - interrupt 通过 monkeypatch core.nodes.planning.interrupt 注入用户决策 payload；
    - 路由 / 降级 / 类型补齐等确定性逻辑直接驱动对应 helper。

硬约束验证：
    - planning 是手写复合节点（非 _make_react_wrapper 直接生成）；
    - revise / switch_repo 无次数硬上限（CP-B3-5 连续 6 次不强制 approve）；
    - cancel 写 current_step="cancelled_by_user"，不强制 approve；
    - Prompt Cache：_PLANNING_SYSTEM_PROMPT_BODY 主体字节级一致、无论文级动态值；
    - _map_planning_result 用 3 参签名（治理范式）。
"""

from __future__ import annotations

import importlib
import inspect
from typing import Any, Dict, List, Optional

import pytest

import core.react_base as react_base
from core.errors import LLMError
from core.state import ExecutionMode

planning_module = importlib.import_module("core.nodes.planning")
planning = planning_module.planning
_planning_react = planning_module._planning_react
_map_planning_result = planning_module._map_planning_result
_build_planning_system_prompt = planning_module._build_planning_system_prompt
_switch_selected_repo = planning_module._switch_selected_repo
_finalize_approve = planning_module._finalize_approve
_minimal_plan = planning_module._minimal_plan
REPRODUCTION_PLAN_SCHEMA = planning_module.REPRODUCTION_PLAN_SCHEMA
NODE_NAME = planning_module.NODE_NAME
_BODY = planning_module._PLANNING_SYSTEM_PROMPT_BODY


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _base_state(**overrides: Any) -> Dict[str, Any]:
    state: Dict[str, Any] = {
        "llm_config_set": {
            "default": {
                "base_url": "http://x",
                "model": "m",
                "api_key": "k",
                "temperature": 0.0,
                "max_tokens": 1024,
            },
            "overrides": {},
        },
        "paper_meta": {"arxiv_id": "2405.14831", "title": "HippoRAG"},
        "paper_analysis": {
            "method_summary": "中文方法摘要",
            "datasets": ["MuSiQue"],
            "metrics": ["EM"],
            "hardware_requirements": "1x A100",
            "framework": "PyTorch",
        },
        "resource_info": {
            "repos": [{"url": "https://github.com/a/repo", "quality_score": 0.8}],
            "selected_repo": {"url": "https://github.com/a/repo", "quality_score": 0.8},
            "external_resources": [],
            "resource_strategy": "use_repo",
        },
        "node_errors": [],
        "degraded_nodes": [],
        "analysis_notes": "",
        "retry_budget_remaining": 50,
        "_planning_revise_count": 0,
        "_planning_user_feedback": None,
        "execution_mode": ExecutionMode.FULL,
        "current_step": "resource_scout",
    }
    state.update(overrides)
    return state


def _full_plan_result() -> Dict[str, Any]:
    """构造一份完整的 ReproductionPlan <result>（LLM 输出形态）。"""
    return {
        "plan_summary": "复现 HippoRAG 的整体思路……",
        "environment": {"gpu": "1x A100", "python": "3.11"},
        "data_preparation": ["下载 MuSiQue", "预处理"],
        "code_strategy": "use_repo",
        "execution_steps": [
            {"step_name": "安装依赖", "command": "pip install -r requirements.txt",
             "expected_output": "成功"},
        ],
        "expected_results": {"EM": "0.5"},
        "estimated_time": "2 天",
        "deliverables": ["README.md", "requirements.txt", "run.py", "core 实现", "py_compile 通过"],
    }


class _FakeSubgraph:
    """脚本化 ReAct 子图：invoke 直接返回预设 result + messages + round。"""

    def __init__(self, result: Optional[Dict[str, Any]], messages: List[Any], rounds: int):
        self._result = result
        self._messages = messages
        self._rounds = rounds
        self.captured_initial: Optional[Dict[str, Any]] = None

    def invoke(self, initial):
        self.captured_initial = initial
        return {
            "result": self._result,
            "messages": self._messages,
            "round": self._rounds,
            "status": "done",
        }


class _RaisingSubgraph:
    """invoke 抛异常的脚本化子图（模拟 ReAct 子图本身失败）。"""

    def __init__(self, exc: Exception):
        self._exc = exc

    def invoke(self, initial):
        raise self._exc


def _patch_react(monkeypatch, result, messages=None, rounds=3):
    """让 _planning_react 走脚本化子图 + 假 LLM。"""
    fake = _FakeSubgraph(result, messages or [], rounds)
    monkeypatch.setattr(react_base, "create_react_subgraph", lambda **kw: fake)
    monkeypatch.setattr(react_base, "create_llm", lambda cfg: object())
    return fake


def _patch_react_raises(monkeypatch, exc):
    fake = _RaisingSubgraph(exc)
    monkeypatch.setattr(react_base, "create_react_subgraph", lambda **kw: fake)
    monkeypatch.setattr(react_base, "create_llm", lambda cfg: object())
    return fake


def _patch_interrupt(monkeypatch, decision):
    """注入用户决策 payload（interrupt 返回值）。"""
    captured = {}

    def fake_interrupt(payload):
        captured["payload"] = payload
        return decision

    monkeypatch.setattr(planning_module, "interrupt", fake_interrupt)
    return captured


# ---------------------------------------------------------------------------
# CP-B3-1：planning 是手写函数（非 _make_react_wrapper 直接生成）
# ---------------------------------------------------------------------------

def test_cp_b3_1_planning_is_handwritten():
    assert callable(planning)
    # 手写函数 __name__ 是 "planning"，而非 wrapper 的 "react_wrapper_planning"
    assert planning.__name__ == "planning"
    params = list(inspect.signature(planning).parameters.keys())
    assert params == ["state"], params


# ---------------------------------------------------------------------------
# CP-B3-2：_planning_react 是 _make_react_wrapper 生成（节点级 LLM 路由）
# ---------------------------------------------------------------------------

def test_cp_b3_2_planning_react_is_wrapper():
    assert callable(_planning_react)
    assert _planning_react.__name__ == "react_wrapper_planning"


# ---------------------------------------------------------------------------
# CP-B3-3：approve -> reproduction_plan.approved == True、current_step == "planning"
# ---------------------------------------------------------------------------

def test_cp_b3_3_approve(monkeypatch):
    _patch_react(monkeypatch, _full_plan_result())
    cap = _patch_interrupt(monkeypatch, {"decision": "approve"})
    out = planning(_base_state())
    assert out["reproduction_plan"]["approved"] is True
    assert out["current_step"] == "planning"
    assert out["reproduction_plan"]["code_strategy"] == "use_repo"
    # payload 字段齐全（UI 审核用）
    p = cap["payload"]
    assert p["soft_hint_threshold"] == planning_module.PLANNING_SOFT_HINT_THRESHOLD
    assert p["max_total_llm_calls"] == planning_module.MAX_TOTAL_LLM_CALLS
    assert "reproduction_plan" in p and "resource_info" in p


# ---------------------------------------------------------------------------
# CP-B3-4：revise -> _planning_user_feedback / _planning_revise_count+1、不强制 approve
# ---------------------------------------------------------------------------

def test_cp_b3_4_revise(monkeypatch):
    _patch_react(monkeypatch, _full_plan_result())
    _patch_interrupt(monkeypatch, {"decision": "revise", "user_feedback": "缩减实验"})
    out = planning(_base_state(_planning_revise_count=0))
    assert out["_planning_user_feedback"] == "缩减实验"
    assert out["_planning_revise_count"] == 1
    # revise 不返回 reproduction_plan（让 graph 走 self-loop 重入 planning）
    assert "reproduction_plan" not in out


# ---------------------------------------------------------------------------
# CP-B3-5：连续 6 次 revise 不强制 approve，计数单调递增
# ---------------------------------------------------------------------------

def test_cp_b3_5_six_revises_no_forced_approve(monkeypatch):
    _patch_react(monkeypatch, _full_plan_result())
    _patch_interrupt(monkeypatch, {"decision": "revise", "user_feedback": "再改"})
    count = 0
    for expected in range(1, 7):
        out = planning(_base_state(_planning_revise_count=count))
        assert out["_planning_revise_count"] == expected
        assert "reproduction_plan" not in out  # 从不强制 approve
        notes = out.get("analysis_notes", "") or ""
        assert "revise_limit_reached" not in notes
        assert "revise_limit" not in notes
        count = out["_planning_revise_count"]
    assert count == 6


# ---------------------------------------------------------------------------
# CP-B3-6：switch_repo -> selected_repo.url 切换、与 revise 共享计数
# ---------------------------------------------------------------------------

def test_cp_b3_6_switch_repo(monkeypatch):
    _patch_react(monkeypatch, _full_plan_result())
    _patch_interrupt(
        monkeypatch,
        {"decision": "switch_repo", "new_repo_url": "https://github.com/new/repo"},
    )
    out = planning(_base_state(_planning_revise_count=2))
    assert out["resource_info"]["selected_repo"]["url"] == "https://github.com/new/repo"
    assert out["_planning_revise_count"] == 3
    assert "reproduction_plan" not in out


# ---------------------------------------------------------------------------
# CP-B3-7：code_only -> execution_mode == CODE_ONLY + approved True
# ---------------------------------------------------------------------------

def test_cp_b3_7_code_only(monkeypatch):
    _patch_react(monkeypatch, _full_plan_result())
    _patch_interrupt(monkeypatch, {"decision": "code_only"})
    out = planning(_base_state())
    assert out["execution_mode"] == ExecutionMode.CODE_ONLY
    assert out["reproduction_plan"]["approved"] is True


# ---------------------------------------------------------------------------
# CP-B3-8：cancel -> current_step="cancelled_by_user" + [CANCELLED]，不强制 approve
# ---------------------------------------------------------------------------

def test_cp_b3_8_cancel(monkeypatch):
    _patch_react(monkeypatch, _full_plan_result())
    _patch_interrupt(monkeypatch, {"decision": "cancel"})
    out = planning(_base_state())
    assert out["current_step"] == "cancelled_by_user"
    assert "[CANCELLED]" in out["analysis_notes"]
    # cancel 不强制 approve（不返回 reproduction_plan）
    assert "reproduction_plan" not in out


# ---------------------------------------------------------------------------
# CP-B3-9：非法 resume payload -> _finalize_approve(invalid_resume_payload) 兜底不抛错
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad", [{"foo": "bar"}, None, "approve", 123])
def test_cp_b3_9_invalid_payload_fallback(monkeypatch, bad):
    _patch_react(monkeypatch, _full_plan_result())
    _patch_interrupt(monkeypatch, bad)
    out = planning(_base_state())
    assert out["reproduction_plan"]["approved"] is True
    assert "[PLANNING_FALLBACK]" in out.get("analysis_notes", "")
    assert "invalid_resume_payload" in out.get("analysis_notes", "")


def test_cp_b3_9b_unknown_decision_fallback(monkeypatch):
    _patch_react(monkeypatch, _full_plan_result())
    _patch_interrupt(monkeypatch, {"decision": "nonsense"})
    out = planning(_base_state())
    assert out["reproduction_plan"]["approved"] is True
    assert "unknown_decision:nonsense" in out.get("analysis_notes", "")


# ---------------------------------------------------------------------------
# CP-B3-10：_PLANNING_SYSTEM_PROMPT_BODY 主体无论文级动态变量 + 字节级一致
# ---------------------------------------------------------------------------

def test_cp_b3_10_prompt_body_frozen():
    # 不同论文上下文下，build_system_prompt 返回值字节级一致
    a = _build_planning_system_prompt({"paper_meta": {"arxiv_id": "1111.1"}})
    b = _build_planning_system_prompt({"paper_meta": {"arxiv_id": "2222.2"}, "user_feedback": "x"})
    assert a == b == _BODY
    # 主体不含任何论文级动态值（arxiv_id / 具体 URL）
    assert "1111.1" not in _BODY and "2222.2" not in _BODY
    assert "arxiv_id" not in _BODY or "{" not in _BODY.split("arxiv_id")[0][-3:]


# ---------------------------------------------------------------------------
# CP-B3-11：ReAct 子图失败 -> 最简版 plan（plan_summary + code_strategy）+ 仍 interrupt
# ---------------------------------------------------------------------------

def test_cp_b3_11_react_failure_minimal_plan(monkeypatch):
    _patch_react_raises(monkeypatch, LLMError("provider down"))
    cap = _patch_interrupt(monkeypatch, {"decision": "approve"})
    out = planning(_base_state())
    # 仍触发了 interrupt（payload 被捕获）
    assert "payload" in cap
    plan = out["reproduction_plan"]
    assert plan["plan_summary"]  # 非空
    assert plan["code_strategy"] == "from_scratch"
    assert plan["approved"] is True  # approve 决策
    # degraded 标记
    assert NODE_NAME in out.get("degraded_nodes", [])


def test_cp_b3_11b_react_failure_then_cancel(monkeypatch):
    """子图失败 + 用户选择 cancel：仍正常路由到 cancel（不抛错）。"""
    _patch_react_raises(monkeypatch, LLMError("provider down"))
    _patch_interrupt(monkeypatch, {"decision": "cancel"})
    out = planning(_base_state())
    assert out["current_step"] == "cancelled_by_user"


# ---------------------------------------------------------------------------
# CP-B3-12：_map_planning_result 用 3 参签名
# ---------------------------------------------------------------------------

def test_cp_b3_12_three_arg_signature():
    params = list(inspect.signature(_map_planning_result).parameters.keys())
    assert params == ["result", "state", "react_messages"], params


# ---------------------------------------------------------------------------
# 补充：_map_planning_result 直接驱动（确定性）
# ---------------------------------------------------------------------------

def test_map_empty_result_degrades_minimal():
    out = _map_planning_result(None, _base_state())
    assert out["reproduction_plan"]["code_strategy"] == "from_scratch"
    assert NODE_NAME in out["degraded_nodes"]
    assert out["current_step"] == NODE_NAME


def test_map_missing_core_fields_degraded():
    # 缺 plan_summary（核心字段）-> degraded
    out = _map_planning_result({"code_strategy": "use_repo", "deliverables": ["x"]}, _base_state())
    assert NODE_NAME in out["degraded_nodes"]
    assert out["reproduction_plan"]["plan_summary"]  # 给了兜底文案


def test_map_no_resource_info_forces_from_scratch():
    state = _base_state(resource_info=None)
    out = _map_planning_result(_full_plan_result(), state)
    # resource_info 为空 -> code_strategy 强制 from_scratch
    assert out["reproduction_plan"]["code_strategy"] == "from_scratch"


def test_switch_repo_no_resource_info():
    """resource_info 为空时 switch_repo 也能构造仅含该仓库的 ResourceInfo。"""
    ri = _switch_selected_repo(None, "https://github.com/x/y")
    assert ri["selected_repo"]["url"] == "https://github.com/x/y"
    assert ri["selected_repo"]["source"] == "user_switch"
    assert len(ri["repos"]) == 1
    assert ri["resource_strategy"] == "use_repo"


def test_minimal_plan_has_deliverables():
    plan = _minimal_plan(_base_state(), "test reason")
    assert plan["code_strategy"] == "from_scratch"
    assert plan["deliverables"]  # 最低基准线非空
    assert plan["approved"] is False


# ===========================================================================
# 深化补强（@测试工程师代理 2026-06-03）：开发自测未覆盖的边角
# ===========================================================================

# --- D1 _switch_selected_repo 各形态 ---

def test_switch_repo_hits_existing_candidate_reuses_source():
    """新 URL 命中 repos 已有候选时：复用原候选（保留原 source），不新建 user_switch、不重复入列。"""
    ri = {
        "repos": [{"url": "https://github.com/a/b", "quality_score": 0.9, "source": "pwc"}],
        "selected_repo": None,
        "external_resources": [],
        "resource_strategy": "hybrid",
    }
    out = _switch_selected_repo(ri, "https://github.com/a/b")
    assert out["selected_repo"]["url"] == "https://github.com/a/b"
    assert out["selected_repo"]["source"] == "pwc"  # 复用原候选，非 user_switch
    assert len(out["repos"]) == 1  # 不重复入列
    # 命中已有候选时 strategy 保留原值（hybrid 合法）
    assert out["resource_strategy"] == "hybrid"


def test_switch_repo_empty_url_keeps_none_and_strategy():
    """switch_repo 给空 URL：selected 保持 None，不新建候选，strategy 保留原合法值。"""
    ri = {
        "repos": [{"url": "https://github.com/a/b", "quality_score": 0.9}],
        "selected_repo": None,
        "external_resources": [],
        "resource_strategy": "hybrid",
    }
    out = _switch_selected_repo(ri, "")
    assert out["selected_repo"] is None
    assert len(out["repos"]) == 1  # 没有空 URL 候选被加入
    assert out["resource_strategy"] == "hybrid"


def test_switch_repo_new_url_creates_user_switch_repo():
    """新 URL 未命中候选：构造 source='user_switch' 的 RepoInfo 并入列选中。"""
    ri = {
        "repos": [{"url": "https://github.com/a/b", "quality_score": 0.9}],
        "selected_repo": {"url": "https://github.com/a/b"},
        "external_resources": [],
        "resource_strategy": "use_repo",
    }
    out = _switch_selected_repo(ri, "https://github.com/c/d")
    assert out["selected_repo"]["url"] == "https://github.com/c/d"
    assert out["selected_repo"]["source"] == "user_switch"
    assert out["selected_repo"]["quality_score"] == 0.0  # 未评估
    urls = {r["url"] for r in out["repos"]}
    assert urls == {"https://github.com/a/b", "https://github.com/c/d"}


def test_switch_repo_illegal_strategy_normalized_to_use_repo():
    """选中仓库但 resource_strategy 是**非法字符串**时：归一化为 use_repo。

    契约观察：归一化只兜底"非 _VALID_STRATEGIES 的非法值"，对已选中仓库时仍保留的
    合法 from_scratch 不做语义翻转（见 test_switch_repo_legal_from_scratch_kept_when_selected）。
    """
    ri = {
        "repos": [],
        "selected_repo": None,
        "external_resources": [],
        "resource_strategy": "garbage_value",  # 非法
    }
    out = _switch_selected_repo(ri, "https://github.com/c/d")
    assert out["resource_strategy"] == "use_repo"


def test_switch_repo_legal_from_scratch_kept_when_selected():
    """契约观察：选中具体仓库后，已有的合法 from_scratch **不**被翻转为 use_repo。

    归一化逻辑仅针对非法字符串值；from_scratch 本身合法，故保留。此为 _switch_selected_repo
    的实际语义（非 BUG）：strategy 翻转由下游 planning ReAct（_build_reproduction_plan）按
    selected_repo 存在性重判，switch 工具本身不强翻合法值。
    """
    ri = {
        "repos": [],
        "selected_repo": None,
        "external_resources": [],
        "resource_strategy": "from_scratch",  # 合法值
    }
    out = _switch_selected_repo(ri, "https://github.com/c/d")
    assert out["selected_repo"]["url"] == "https://github.com/c/d"
    assert out["resource_strategy"] == "from_scratch"  # 合法值保留，不翻转


# --- D2 _map 类型补齐脏数据 ---

def test_map_coerces_dirty_types():
    """LLM 返回脏类型：execution_steps 含字符串元素 / data_preparation 是 str /
    environment 是非 dict / deliverables 是 str —— 全部安全补齐不抛错。"""
    dirty = {
        "plan_summary": "ok",
        "code_strategy": "use_repo",
        "environment": "1x A100",            # 应被吞为 {}
        "data_preparation": "单条字符串",     # 应被包装为 [str]
        "execution_steps": ["纯字符串步骤", {"step_name": "正常步骤", "command": "x"}, None],
        "expected_results": ["不是dict"],     # 应被吞为 {}
        "deliverables": "README.md",          # 应被包装为 [str]
    }
    out = _map_planning_result(dirty, _base_state())
    plan = out["reproduction_plan"]
    assert plan["environment"] == {}
    assert plan["data_preparation"] == ["单条字符串"]
    # 字符串步骤降级包装为单字段 dict；None 被过滤
    assert plan["execution_steps"][0] == {"step_name": "纯字符串步骤"}
    assert plan["execution_steps"][1]["step_name"] == "正常步骤"
    assert len(plan["execution_steps"]) == 2
    assert plan["expected_results"] == {}
    assert plan["deliverables"] == ["README.md"]
    # 脏数据但核心字段齐全 -> 不 degraded
    assert NODE_NAME not in out.get("degraded_nodes", [])


def test_map_invalid_strategy_with_repo_falls_back_use_repo():
    """有仓库但 code_strategy 非法值 -> 归一化为 use_repo。"""
    res = _full_plan_result()
    res["code_strategy"] = "garbage_strategy"
    out = _map_planning_result(res, _base_state())
    assert out["reproduction_plan"]["code_strategy"] == "use_repo"


# --- D3 _finalize_approve 兜底 analysis_notes 累加 ---

def test_finalize_approve_appends_to_existing_notes():
    """兜底路径写 analysis_notes 时，若 updates 已含 notes 则换行追加而非覆盖。"""
    updates = {
        "reproduction_plan": {"plan_summary": "p", "code_strategy": "use_repo"},
        "analysis_notes": "[SEARCH_LOG] prior",
    }
    out = _finalize_approve(dict(updates), reason="invalid_resume_payload")
    notes = out["analysis_notes"]
    assert "[SEARCH_LOG] prior" in notes  # 原内容保留
    assert "[PLANNING_FALLBACK]" in notes  # 新标记追加
    assert notes.index("prior") < notes.index("[PLANNING_FALLBACK]")  # 顺序：旧在前
    assert out["reproduction_plan"]["approved"] is True


def test_finalize_approve_clean_path_no_notes():
    """正常 approve（无 reason）不写 analysis_notes，避免污染干净路径。"""
    updates = {"reproduction_plan": {"plan_summary": "p", "code_strategy": "use_repo"}}
    out = _finalize_approve(dict(updates))
    assert "analysis_notes" not in out
    assert out["reproduction_plan"]["approved"] is True


def test_finalize_approve_does_not_mutate_input_plan():
    """_finalize_approve 不应原地修改入参 plan（防御性拷贝）。"""
    plan = {"plan_summary": "p", "code_strategy": "use_repo", "approved": False}
    updates = {"reproduction_plan": plan}
    out = _finalize_approve(updates)
    assert out["reproduction_plan"]["approved"] is True
    assert plan["approved"] is False  # 原 plan 未被改


# --- D4 cancel 的 analysis_notes 累加 ---

def test_cancel_appends_to_existing_notes(monkeypatch):
    """cancel 时若 state 已有 analysis_notes，[CANCELLED] 应换行追加而非覆盖。"""
    _patch_react(monkeypatch, _full_plan_result())
    _patch_interrupt(monkeypatch, {"decision": "cancel"})
    out = planning(_base_state(analysis_notes="[QUALITY_WARN] low stars"))
    notes = out["analysis_notes"]
    assert "[QUALITY_WARN] low stars" in notes
    assert "[CANCELLED]" in notes
    assert notes.index("QUALITY_WARN") < notes.index("[CANCELLED]")


# --- D5 switch_repo 经节点完整路径（子图正常 + 子图失败） ---

def test_switch_repo_after_react_failure(monkeypatch):
    """ReAct 子图失败 + 用户 switch_repo：仍正常切仓并 +1 计数（不强制 approve）。"""
    _patch_react_raises(monkeypatch, LLMError("provider down"))
    _patch_interrupt(
        monkeypatch,
        {"decision": "switch_repo", "new_repo_url": "https://github.com/new/x"},
    )
    out = planning(_base_state(_planning_revise_count=1))
    assert out["resource_info"]["selected_repo"]["url"] == "https://github.com/new/x"
    assert out["_planning_revise_count"] == 2
    assert "reproduction_plan" not in out  # 不强制 approve


def test_revise_carries_empty_feedback_when_absent(monkeypatch):
    """revise 决策未带 user_feedback 时：_planning_user_feedback 落空串不抛 KeyError。"""
    _patch_react(monkeypatch, _full_plan_result())
    _patch_interrupt(monkeypatch, {"decision": "revise"})
    out = planning(_base_state())
    assert out["_planning_user_feedback"] == ""
    assert out["_planning_revise_count"] == 1
