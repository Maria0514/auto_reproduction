"""T-S5-3-2 自测：reporting 集成诚实性审计 + 返回契约扩展（S5-03）。

覆盖 dev-plan §批次3 CP-3.2-1 ~ CP-3.2-3：
    - CP-3.2-1 reporting 返回含 honesty_audit 且与 audit_code_dir 产出一致；
      调用恰好一次（mock call count），且在 _determine_report_form 之前（架构 §1）；
    - CP-3.2-2 code_output_dir 缺失/None/空串 → honesty_audit=None（未审计语义，
      判空契约在 reporting 侧）；目录不存在/空目录 → 模块容忍产出
      {"clean": True, "hits": []}；各路径报告仍生成（降级容忍，命中绝不阻断）；
    - CP-3.2-3 CP-C2-5 契约显式扩展落地：返回键集合精确 =
      {report_path, current_step, honesty_audit}；node_errors / degraded_nodes /
      fix_loop_history 等 list 字段零触碰（红线原意原样保留，§10.1 R-7）。

不改渲染逻辑/措辞（T-S5-3-3/3-4 范围），只测集成 + 契约。
"""

from __future__ import annotations

import importlib
import json
import textwrap
from pathlib import Path
from typing import Any, Dict

import pytest

from core.honesty_audit import audit_code_dir
from core.state import ExecutionMode

# core/nodes/__init__.py 显式 export 同名 callable 会遮蔽子模块（sp1 已知坑 6），
# 必须 importlib 取模块对象。
reporting_module = importlib.import_module("core.nodes.reporting")
reporting = reporting_module.reporting
NODE_NAME = reporting_module.NODE_NAME


# ----------------------------- fixtures / helpers -----------------------------


@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    """把 reporting 模块内 WORKSPACE_DIR 指向临时目录（与 test_sprint3_c2 同范式）。"""
    ws = tmp_path / "workspace"
    ws.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(reporting_module, "WORKSPACE_DIR", ws)
    return ws


def _base_state(workspace: Path, arxiv_id: str = "2405.00042") -> Dict[str, Any]:
    """最小可用 state；code_output_dir 落 workspace/<arxiv_id>/code（与 C1 对齐）。"""
    code_dir = workspace / arxiv_id / "code"
    code_dir.mkdir(parents=True, exist_ok=True)
    return {
        "workspace_dir": str(workspace),
        "code_output_dir": str(code_dir.resolve()),
        "execution_mode": ExecutionMode.FULL,
        "paper_meta": {"arxiv_id": arxiv_id, "title": "A Great Paper"},
        "paper_analysis": {"baseline_results": {}, "metrics": []},
        "reproduction_plan": {"expected_results": {}, "deliverables": []},
        "execution_result": None,
        "node_errors": [],
        "degraded_nodes": [],
        "fix_loop_history": [],
        "fix_loop_count": 0,
        "user_fix_decision": None,
    }


def _write_cheat_code(code_dir: Path) -> None:
    """写一个确定性命中审计的作弊样本（R1 答案泄漏 + R2 硬编码分数）。"""
    (code_dir / "task_runner.py").write_text(
        textwrap.dedent(
            """
            baseline_scores = {"main": 0.5, "ablation": 0.3}


            def run_task(data):
                return data["expected_answer"]
            """
        ).lstrip("\n"),
        encoding="utf-8",
    )


# ===========================================================================
# CP-3.2-1 集成一致性 + 调用恰一次
# ===========================================================================


def test_cp_3_2_1_audit_called_exactly_once_before_form(workspace, monkeypatch):
    """audit_code_dir 恰好调用一次、入参为 code_output_dir，且先于三形态判定（架构 §1）。"""
    calls: list = []
    order: list = []
    sentinel = {"clean": False, "hits": [{"rule": "hardcoded_score",
                                          "file": "x.py", "line": 1, "snippet": "s"}]}

    def fake_audit(code_dir):
        calls.append(code_dir)
        order.append("audit")
        return sentinel

    real_form = reporting_module._determine_report_form

    def spy_form(state):
        order.append("form")
        return real_form(state)

    monkeypatch.setattr(reporting_module, "audit_code_dir", fake_audit)
    monkeypatch.setattr(reporting_module, "_determine_report_form", spy_form)

    state = _base_state(workspace)
    out = reporting(state)

    assert calls == [state["code_output_dir"]], calls  # 恰一次 + 入参正确
    assert order[:2] == ["audit", "form"], order  # 审计先于三形态判定
    assert out["honesty_audit"] is sentinel  # 产出原样落返回契约（不加工）


def test_cp_3_2_1_honesty_audit_matches_real_audit_output(workspace):
    """真实审计路径：返回的 honesty_audit == audit_code_dir 独立产出（确定性一致）。"""
    state = _base_state(workspace)
    code_dir = Path(state["code_output_dir"])
    _write_cheat_code(code_dir)

    out = reporting(state)

    expected = audit_code_dir(code_dir)  # t31 已证同输入同输出，可独立复算对照
    assert out["honesty_audit"] == expected
    assert out["honesty_audit"]["clean"] is False
    assert len(out["honesty_audit"]["hits"]) >= 2  # R1 + R2 至少各一
    # 命中不阻断：报告仍正常生成
    assert Path(out["report_path"]).read_text(encoding="utf-8").strip()
    # 落 state 的值可 JSON 序列化（checkpoint 前提）
    json.dumps(out["honesty_audit"], ensure_ascii=False)


# ===========================================================================
# CP-3.2-2 判空契约 + 降级容忍
# ===========================================================================


@pytest.mark.parametrize("mutate", [
    pytest.param(lambda s: s.pop("code_output_dir"), id="key-missing"),
    pytest.param(lambda s: s.update(code_output_dir=None), id="none"),
    pytest.param(lambda s: s.update(code_output_dir=""), id="empty-str"),
])
def test_cp_3_2_2_missing_code_dir_yields_none_and_report_still_written(
    workspace, monkeypatch, mutate
):
    """缺失/None/空串 → honesty_audit=None（未审计语义）且**不调用**审计；报告仍生成。"""
    calls: list = []

    def spy_audit(code_dir):
        calls.append(code_dir)
        return audit_code_dir(code_dir)

    monkeypatch.setattr(reporting_module, "audit_code_dir", spy_audit)

    state = _base_state(workspace)
    mutate(state)
    out = reporting(state)

    assert out["honesty_audit"] is None
    assert calls == []  # 判空在 reporting 侧，压根不调用
    md = Path(out["report_path"]).read_text(encoding="utf-8")
    assert md.strip()  # 报告仍生成（降级容忍）


def test_cp_3_2_2_nonexistent_dir_tolerated(workspace):
    """code_output_dir 非空但目录不存在 → 模块容忍产出空结果，报告仍生成。"""
    state = _base_state(workspace)
    missing = Path(state["code_output_dir"]).parent / "not_exist" / "code"
    state["code_output_dir"] = str(missing)  # 不 mkdir

    out = reporting(state)

    assert out["honesty_audit"] == {"clean": True, "hits": []}
    assert Path(out["report_path"]).read_text(encoding="utf-8").strip()


def test_cp_3_2_2_existing_empty_dir_tolerated(workspace):
    """存在但空目录 → 容忍产出 {"clean": True, "hits": []}，报告仍生成。"""
    state = _base_state(workspace)  # code_dir 已创建且为空
    out = reporting(state)

    assert out["honesty_audit"] == {"clean": True, "hits": []}
    assert Path(out["report_path"]).read_text(encoding="utf-8").strip()


# ===========================================================================
# CP-3.2-3 CP-C2-5 契约显式扩展 + list 通道零触碰
# ===========================================================================


def test_cp_3_2_3_return_key_set_three_forms(workspace):
    """三形态下返回键集合精确 = {report_path, current_step, honesty_audit}。"""
    for mode_setup in (
        # full_success
        lambda s: s.update(execution_result={
            "success": True, "metrics": {"a": 1}, "logs": "", "errors": [],
            "artifacts": [], "runtime_seconds": 1.0, "environment_info": {},
        }),
        # code_only
        lambda s: s.update(execution_mode=ExecutionMode.CODE_ONLY),
        # degraded
        lambda s: s.update(execution_result={
            "success": False, "metrics": {}, "logs": "", "errors": ["x"],
            "artifacts": [], "runtime_seconds": 0.0, "environment_info": {},
        }),
    ):
        state = _base_state(workspace)
        mode_setup(state)
        out = reporting(state)
        assert set(out.keys()) == {"report_path", "current_step", "honesty_audit"}, (
            out.keys()
        )
        assert out["current_step"] == NODE_NAME


def test_cp_3_2_3_list_channels_untouched(workspace):
    """node_errors / degraded_nodes / fix_loop_history 零触碰：不返回、不改原对象。"""
    state = _base_state(workspace)
    _write_cheat_code(Path(state["code_output_dir"]))  # 即便审计命中也不碰 list 通道
    state["node_errors"] = [{"node_name": "execution", "error_type": "transient",
                             "error_message": "[error_category=runtime] x",
                             "error_detail": None, "timestamp": "t",
                             "retry_count": 0, "resolved": False}]
    state["degraded_nodes"] = ["execution"]
    state["fix_loop_history"] = [{"round_number": 1, "error_summary": "s",
                                  "error_category": "runtime", "fix_strategy": "f",
                                  "timestamp": "t"}]
    ne_before = list(state["node_errors"])
    dn_before = list(state["degraded_nodes"])
    fh_before = list(state["fix_loop_history"])

    out = reporting(state)

    for forbidden in ("node_errors", "degraded_nodes", "fix_loop_history"):
        assert forbidden not in out
    assert state["node_errors"] == ne_before
    assert state["degraded_nodes"] == dn_before
    assert state["fix_loop_history"] == fh_before
    assert out["honesty_audit"]["clean"] is False  # 命中确实发生（正控制）
