"""Sprint 3 任务 E3 单测：`ui/pages/result_report.py`（S3-10 结果报告页）。

覆盖 dev-plan §E3 检查点 CP-E3-1 ~ CP-E3-4（dev-plan.md L617-621）。

测试策略（沿用 sp2 UI 测试范式）::

    - 纯函数内核（_load_report_markdown / _metric_comparison_rows / _fix_loop_rows /
      _artifact_list / _deliverables_list / _degradation_reasons / _reset_to_input_page）
      模块级直测——逻辑层断言优先，不依赖 shadcn iframe（本页未用 shadcn，AppTest 可见
      原生组件，但逻辑层断言仍是首选，最稳）；
    - AppTest + mock GraphController（patch app._get_controller）跑 render()，断言渲染
      文本 / 结论卡片关键文案 / 出口按钮行为；
    - 三形态判定**复用 reporting._determine_report_form**（不臆造），并断言本页 import
      的就是 reporting 同一函数对象（契约对齐守门）。

不跑 e2e（省 deepxiv 配额）。运行::

    .venv/bin/pytest tests/test_sprint3_e3.py -q
"""

from __future__ import annotations

import importlib
from typing import Dict, List, Optional
from unittest.mock import MagicMock, patch

import pytest
from streamlit.testing.v1 import AppTest

import config
from core.state import ExecutionMode


# --------------------------------------------------------------------------- #
# 公共 fixture / 工具
# --------------------------------------------------------------------------- #
def _report_mod():
    """用 importlib 取模块（避免 __init__ 显式 export 遮蔽子模块的已知坑，坑6）。"""
    return importlib.import_module("ui.pages.result_report")


def _make_full_success_state(report_path: str) -> Dict:
    """构造一份 full_success 形态的 state（execution_result.success=True + 指标 + 产物）。"""
    return {
        "report_path": report_path,
        "execution_mode": ExecutionMode.FULL,
        "execution_result": {
            "success": True,
            "metrics": {"recall@5": 0.88, "f1": 0.71},
            "logs": "ok",
            "errors": [],
            "artifacts": ["workspace/2405.14831/out/result.json", "workspace/2405.14831/out/fig.png"],
            "runtime_seconds": 42.5,
            "environment_info": {"python": "3.11"},
        },
        "paper_analysis": {"baseline_results": {"recall@5": 0.89}},
        "reproduction_plan": {
            "expected_results": {"recall@5": 0.87, "f1": 0.70},
            "deliverables": ["复现报告", "评测脚本"],
        },
        "code_output_dir": "workspace/2405.14831/code",
        "fix_loop_count": 1,
        "fix_loop_history": [
            {
                "round_number": 1,
                "error_category": "dependency_error",
                "error_summary": "缺少 torch",
                "fix_strategy": "在 requirements 中补齐 torch",
                "timestamp": "2026-06-28T10:00:00",
            }
        ],
    }


def _make_code_only_state(report_path: str) -> Dict:
    """构造一份 code_only 形态的 state（execution_mode=CODE_ONLY，无 execution_result）。"""
    return {
        "report_path": report_path,
        "execution_mode": ExecutionMode.CODE_ONLY,
        "execution_result": None,
        "reproduction_plan": {"deliverables": ["复现代码", "README"]},
        "code_output_dir": "workspace/2405.14831/code",
        "fix_loop_count": 0,
        "fix_loop_history": [],
    }


def _make_degraded_state(report_path: str) -> Dict:
    """构造一份 degraded 形态的 state（success=False + 降级节点 + 修复历程 + 用户决策）。"""
    return {
        "report_path": report_path,
        "execution_mode": ExecutionMode.FULL,
        "execution_result": {
            "success": False,
            "metrics": {},
            "logs": "boom",
            "errors": ["ImportError: no module named foo", "exit_code=1"],
            "artifacts": ["workspace/2405.14831/out/partial.log"],
            "runtime_seconds": 12.0,
            "environment_info": {},
        },
        "paper_analysis": {"baseline_results": {"recall@5": 0.89}},
        "reproduction_plan": {"expected_results": {"recall@5": 0.87}, "deliverables": ["复现报告"]},
        "code_output_dir": "workspace/2405.14831/code",
        "degraded_nodes": ["execution"],
        "user_fix_decision": "export_code",
        "fix_loop_count": 3,
        "fix_loop_history": [
            {
                "round_number": 1,
                "error_category": "dependency_error",
                "error_summary": "缺少 foo",
                "fix_strategy": "补依赖",
                "timestamp": "t1",
            },
            {
                "round_number": 2,
                "error_category": "runtime_error",
                "error_summary": "CUDA OOM",
                "fix_strategy": "降 batch size",
                "timestamp": "t2",
            },
        ],
    }


def _make_controller_mock(state: Optional[Dict]) -> MagicMock:
    """构造 GraphController mock：poll_state 返回指定 state，其余为桩。"""
    controller = MagicMock()
    controller.poll_state.return_value = state
    return controller


_APP_SCRIPT = """
import streamlit as st
st.session_state.setdefault("thread_id", "task-report-001")
st.session_state.setdefault("current_page", "report")
from ui.pages.result_report import render
render()
"""

_APP_SCRIPT_NO_THREAD = """
from ui.pages.result_report import render
render()
"""


def _run(controller: MagicMock, script: str = _APP_SCRIPT) -> AppTest:
    """patch app._get_controller（页面 from app import _get_controller），跑一次 AppTest。"""
    with patch("app._get_controller", return_value=controller):
        at = AppTest.from_string(script)
        at.run()
    return at


def _collect_text(at: AppTest) -> str:
    """聚合 AppTest 元素树所有可读文本，便于断言渲染内容。"""
    parts: List[str] = []
    for collection in (at.title, at.subheader, at.caption, at.markdown,
                       at.text, at.warning, at.info, at.error):
        for el in collection:
            parts.append(str(getattr(el, "value", "")))
    for el in getattr(at, "code", []):
        parts.append(str(getattr(el, "value", "")))
    # st.table 内容也聚合（指标对比表 / 修复历程表）。
    for el in getattr(at, "table", []):
        parts.append(str(getattr(el, "value", "")))
    return "\n".join(parts)


# =========================================================================== #
# CP-E3-1：页面可导入 + report_path 非空时 st.markdown 渲染完整报告
# =========================================================================== #
def test_cp_e3_1_importable():
    """CP-E3-1：render 可导入 + callable + 别名/__all__ 约定（入口名与 E1 _PAGE_MAP 一致）。"""
    mod = _report_mod()
    assert callable(mod.render)
    assert mod.render_result_report_page is mod.render
    assert mod.__all__ == ["render", "render_result_report_page"]
    # E1 _PAGE_MAP 按 render_result_report_page 预留 dispatch，函数名必须一致。
    import app
    module_name, func_name = app._PAGE_MAP[config.STREAMLIT_PAGE_REPORT]
    assert module_name == "ui.pages.result_report"
    assert func_name == "render_result_report_page"
    assert hasattr(mod, func_name)


def test_cp_e3_1_load_report_markdown_reads_file(tmp_path):
    """CP-E3-1：report_path 指向真实文件 → _load_report_markdown 读出全文，无错误。"""
    mod = _report_mod()
    report = tmp_path / "report.md"
    content = "# 论文复现报告\n\n- arXiv ID: `2405.14831`\n\n## 复现结论\n\n复现成功。"
    report.write_text(content, encoding="utf-8")

    markdown, err = mod._load_report_markdown(str(report))
    assert err is None
    assert markdown == content


def test_cp_e3_1_load_report_markdown_defensive():
    """CP-E3-1：report_path 为空 / 文件不存在 → 返回 (None, 错误文案)，不抛。"""
    mod = _report_mod()
    md_none, err_none = mod._load_report_markdown(None)
    assert md_none is None and err_none

    md_missing, err_missing = mod._load_report_markdown("/no/such/report-xyz.md")
    assert md_missing is None and "不存在" in err_missing


def test_cp_e3_1_full_report_markdown_rendered_in_render(tmp_path):
    """CP-E3-1：report_path 非空（真实文件）→ render() 走 st.markdown 渲染报告全文。

    AppTest 跑 render()，断言报告正文的独特片段出现在元素树文本中（st.markdown 渲染）。
    """
    mod = _report_mod()
    report = tmp_path / "report.md"
    marker = "这是报告正文的独特标记字符串_REPORT_BODY_MARKER"
    report.write_text(f"# 复现报告\n\n{marker}\n", encoding="utf-8")

    state = _make_full_success_state(str(report))
    at = _run(_make_controller_mock(state))
    assert not at.exception, at.exception
    text = _collect_text(at)
    assert "复现报告全文" in text, "应出现报告全文区块标题"
    assert marker in text, "report_path 文件正文应被 st.markdown 完整渲染"


def test_cp_e3_1_missing_report_path_warns_not_crash():
    """CP-E3-1 边界：report_path 为 None（reporting 未跑完）→ 报告全文区块给 warning，不崩。"""
    state = {"report_path": None, "execution_mode": ExecutionMode.FULL,
             "execution_result": {"success": True, "metrics": {"a": 1}}}
    at = _run(_make_controller_mock(state))
    assert not at.exception, at.exception
    text = _collect_text(at)
    assert "report_path 为空" in text or "报告" in text


# =========================================================================== #
# CP-E3-2：三形态结论卡片正确渲染（关键文案 + 与 reporting 判定对齐）
# =========================================================================== #
def test_cp_e3_2_form_determination_reuses_reporting():
    """CP-E3-2：本页三形态判定**复用** reporting._determine_report_form（同一函数对象）。

    契约对齐守门：避免本页自行实现一套可能与报告正文不一致的判定。

    注意：core.nodes.__init__ 显式 export `reporting` callable 会遮蔽子模块（项目已知坑6），
    故必须用 importlib.import_module 取模块对象，不能 `import core.nodes.reporting as reporting`。
    """
    reporting = importlib.import_module("core.nodes.reporting")
    mod = _report_mod()
    assert mod._determine_report_form is reporting._determine_report_form


def test_cp_e3_2_full_success_card(tmp_path):
    """CP-E3-2：full_success 形态 → 结论卡片按 conclusion 两级措辞 +「不做硬性结论」口径。

    sp5 T-S5-3-5 适配（AC-S5-07 口径差同步）：_make_full_success_state 的
    expected_results 为旧 dict 形态 → goal_checks 全"未验证" → level=engineering →
    卡片标题「代码跑通（工程复现），论文实验结论未验证」（禁"复现成功"字样）。
    断言目标从旧卡片「复现成功」换为 engineering 新措辞，语义不弱化（仍断言
    full_success 形态渲染出结论卡片 + B 档口径）。
    """
    report = tmp_path / "report.md"
    report.write_text("# 报告\n执行成功", encoding="utf-8")
    at = _run(_make_controller_mock(_make_full_success_state(str(report))))
    assert not at.exception, at.exception
    text = _collect_text(at)
    assert "代码跑通（工程复现）" in text
    assert "论文实验结论未验证" in text
    # AC-S5-07 红线：engineering 卡片禁"复现成功"字样（报告 md fixture 已避开该词）。
    assert "复现成功" not in text
    # B 档口径：不做硬性结论判定（Q-S3-01）。
    assert "不做硬性结论" in text or "不做任何硬性" in text


def test_cp_e3_2_code_only_card(tmp_path):
    """CP-E3-2：code_only 形态 → 结论卡片显示「仅生成代码」+「未执行」，且无指标章节。"""
    report = tmp_path / "report.md"
    report.write_text("# 报告\n仅生成代码", encoding="utf-8")
    at = _run(_make_controller_mock(_make_code_only_state(str(report))))
    assert not at.exception, at.exception
    text = _collect_text(at)
    assert "仅生成代码" in text
    assert "未在沙箱中实际执行" in text or "未执行" in text
    # code_only 无指标章节（AC-S3-09：code_only 无指标章节）。
    assert "📊 指标对比" not in text


def test_cp_e3_2_degraded_card(tmp_path):
    """CP-E3-2：degraded 形态 → 结论卡片显示「未成功复现（降级）」+ 降级原因区块。"""
    report = tmp_path / "report.md"
    report.write_text("# 报告\n未成功复现", encoding="utf-8")
    at = _run(_make_controller_mock(_make_degraded_state(str(report))))
    assert not at.exception, at.exception
    text = _collect_text(at)
    assert "未成功复现" in text
    assert "降级原因" in text


def test_cp_e3_2_card_spec_covers_all_three_forms():
    """CP-E3-2：_FORM_CARD_SPEC 覆盖三形态（full_success 两级分卡），标题文案正确。

    sp5 T-S5-3-5 适配：full_success 拆 science / engineering 两级卡片（与报告正文
    _render_full_success 同措辞，AC-S5-07）；断言目标随之更新，语义不弱化（仍防漏形态）。
    """
    mod = _report_mod()
    assert set(mod._FORM_CARD_SPEC.keys()) == {
        "full_success_science", "full_success_engineering", "code_only", "degraded",
    }
    assert "复现成功（科学复现）" in mod._FORM_CARD_SPEC["full_success_science"][0]
    # AC-S5-07 红线：engineering 卡片标题/描述均禁"复现成功"字样。
    eng_title, eng_desc = mod._FORM_CARD_SPEC["full_success_engineering"][:2]
    assert "代码跑通（工程复现）" in eng_title
    assert "复现成功" not in eng_title and "复现成功" not in eng_desc
    assert "仅生成代码" in mod._FORM_CARD_SPEC["code_only"][0]
    assert "未成功复现" in mod._FORM_CARD_SPEC["degraded"][0]


# =========================================================================== #
# CP-E3-3：指标对比表 + artifact 清单 + 修复历程 + deliverables 区块渲染
# =========================================================================== #
def test_cp_e3_3_metric_comparison_rows_union_and_no_judgement():
    """CP-E3-3：指标对比表行取 baseline/expected/复现 三方并集，缺值用 None；不出达标判定。"""
    mod = _report_mod()
    state = _make_full_success_state("/tmp/x.md")
    # baseline 仅 recall@5；expected 有 recall@5 + f1；复现有 recall@5 + f1。
    rows = mod._metric_comparison_rows(state)
    metric_names = {r["指标 (Metric)"] for r in rows}
    assert metric_names == {"recall@5", "f1"}
    # f1 在 baseline 缺失 → None（render 层渲染为「—」）。
    f1_row = next(r for r in rows if r["指标 (Metric)"] == "f1")
    assert f1_row["论文 baseline"] is None
    assert f1_row["本次复现值"] == 0.71
    # B 档：行里绝不出现「达标」「不达标」结论键。
    for r in rows:
        assert "达标" not in "".join(str(k) for k in r.keys())


def test_cp_e3_3_metric_rows_empty_when_no_metrics():
    """CP-E3-3 边界：三方指标全空 → 返回空列表（render 给「无可对比指标」caption）。"""
    mod = _report_mod()
    rows = mod._metric_comparison_rows({"execution_result": {"metrics": {}}})
    assert rows == []


def test_cp_e3_3_artifact_and_deliverable_lists():
    """CP-E3-3：artifact 清单 / deliverables 取数正确，缺失给空列表（防御）。"""
    mod = _report_mod()
    full = _make_full_success_state("/tmp/x.md")
    assert mod._artifact_list(full) == [
        "workspace/2405.14831/out/result.json",
        "workspace/2405.14831/out/fig.png",
    ]
    assert mod._deliverables_list(full) == ["复现报告", "评测脚本"]
    # 缺失结构 → 空列表，不抛。
    assert mod._artifact_list({}) == []
    assert mod._deliverables_list({}) == []
    assert mod._artifact_list({"execution_result": None}) == []


def test_cp_e3_3_fix_loop_rows():
    """CP-E3-3：修复历程逐轮取数（round/category/summary/strategy），非 dict 项跳过。

    sp5 T-S5-3-5 适配：error_category 经 humanize 渲染——fixture 的 "runtime_error"
    不是真实 ErrorCategory 枚举值（真实值为 "runtime"），命中未知值兜底
    `f"{value}（内部标识）"`（AC-S5-18，原值保留不静默）。断言目标随之更新。
    """
    mod = _report_mod()
    degraded = _make_degraded_state("/tmp/x.md")
    rows = mod._fix_loop_rows(degraded)
    assert len(rows) == 2
    assert rows[0]["轮次"] == 1
    assert rows[1]["错误分类 (error_category)"] == "runtime_error（内部标识）"
    # 鲁棒性：history 含非 dict 项 / 为 None。
    assert mod._fix_loop_rows({"fix_loop_history": [None, "x", {"round_number": 9}]})[0]["轮次"] == 9
    assert mod._fix_loop_rows({}) == []


def test_cp_e3_3_full_success_sections_rendered(tmp_path):
    """CP-E3-3：full_success 形态 render → 指标对比表 + artifact + 修复历程 + 代码/deliverables 全渲染。"""
    report = tmp_path / "report.md"
    report.write_text("# 报告\nok", encoding="utf-8")
    at = _run(_make_controller_mock(_make_full_success_state(str(report))))
    assert not at.exception, at.exception
    text = _collect_text(at)
    assert "指标对比" in text
    assert "recall@5" in text
    assert "产物清单" in text
    assert "result.json" in text
    assert "修复历程" in text
    assert "代码位置与交付物" in text
    assert "workspace/2405.14831/code" in text
    assert "复现报告" in text  # deliverable


def test_cp_e3_3_degraded_sections_rendered(tmp_path):
    """CP-E3-3：degraded 形态 render → 降级原因 + 节点 + 执行错误 + 用户决策 + 修复历程逐轮全渲染。"""
    report = tmp_path / "report.md"
    report.write_text("# 报告\nfail", encoding="utf-8")
    at = _run(_make_controller_mock(_make_degraded_state(str(report))))
    assert not at.exception, at.exception
    text = _collect_text(at)
    assert "降级原因" in text
    # sp5 T-S5-3-5 适配：降级节点渲染为"执行验证（execution）"（中文 + 括注内部名），
    # 内部名锚点保留，原断言不变。
    assert "execution" in text  # degraded_nodes
    assert "ImportError" in text  # execution_result.errors
    # sp5 T-S5-3-5 适配：user_fix_decision 经 humanize 渲染为"导出代码（降级交付）"，
    # 断言目标从内部值 "export_code" 换为中文文案，语义不弱化（仍断言决策已渲染）。
    assert "导出代码" in text  # user_fix_decision（export_code 的 humanize 文案）
    assert "CUDA OOM" in text  # fix_loop_history 第二轮摘要
    assert "保留" in text or "产物清单" in text  # 保留产物 / artifact


def test_cp_e3_3_degradation_reasons_defensive():
    """CP-E3-3：_degradation_reasons 防御式 .get，空 state / 缺字段不抛。"""
    mod = _report_mod()
    r = mod._degradation_reasons({})
    assert r["degraded_nodes"] == []
    assert r["execution_errors"] == []
    assert r["user_fix_decision"] is None


# =========================================================================== #
# CP-E3-4：「返回输入页开启新任务」出口可用（切回 input + 状态重置）
# =========================================================================== #
def test_cp_e3_4_reset_to_input_page_logic():
    """CP-E3-4：_reset_to_input_page 切回 input 页 + 解锁提交锁 + 清 thread_id（纯逻辑直测）。"""
    import streamlit as st
    mod = _report_mod()
    st.session_state["current_page"] = "report"
    st.session_state["_input_submitted"] = True
    st.session_state["thread_id"] = "task-old"

    mod._reset_to_input_page()

    assert st.session_state["current_page"] == config.STREAMLIT_PAGE_INPUT
    assert st.session_state["_input_submitted"] is False
    assert st.session_state["thread_id"] is None


def test_cp_e3_4_new_task_button_click_switches_to_input(tmp_path):
    """CP-E3-4：点「返回输入页开启新任务」按钮 → current_page 切回 input + 状态重置（AppTest 点击）。"""
    report = tmp_path / "report.md"
    report.write_text("# 报告\nok", encoding="utf-8")
    controller = _make_controller_mock(_make_full_success_state(str(report)))
    script = """
import streamlit as st
st.session_state.setdefault("thread_id", "task-report-001")
st.session_state.setdefault("current_page", "report")
st.session_state.setdefault("_input_submitted", True)
from ui.pages.result_report import render
render()
"""
    with patch("app._get_controller", return_value=controller):
        at = AppTest.from_string(script)
        at.run()
        assert not at.exception, at.exception
        btns = [b for b in at.button if b.key == "btn_report_new_task"]
        assert len(btns) == 1, "应渲染「返回输入页开启新任务」按钮"
        btns[0].click().run()

    assert at.session_state["current_page"] == config.STREAMLIT_PAGE_INPUT
    assert at.session_state["_input_submitted"] is False
    assert at.session_state["thread_id"] is None


def test_cp_e3_4_no_thread_id_shows_back_button():
    """CP-E3-4：F5 后无 thread_id → 占位提示 + 「返回输入页」出口可用，不死页（沿用 sp2 限制）。"""
    controller = _make_controller_mock(None)
    at = _run(controller, script=_APP_SCRIPT_NO_THREAD)
    assert not at.exception, at.exception
    text = _collect_text(at)
    assert "尚未有可展示" in text or "返回输入页" in text
    # 无 thread_id 分支在取 controller 前 return → 不应调 poll_state。
    controller.poll_state.assert_not_called()
    # 出口按钮存在。
    back_btns = [b for b in at.button if b.key == "btn_report_no_task_back"]
    assert len(back_btns) == 1


def test_cp_e3_4_no_thread_back_button_resets_to_input():
    """CP-E3-4：无 thread 占位页点「返回输入页」→ 切回 input（出口闭环可用）。"""
    controller = _make_controller_mock(None)
    with patch("app._get_controller", return_value=controller):
        at = AppTest.from_string(_APP_SCRIPT_NO_THREAD)
        at.run()
        back_btns = [b for b in at.button if b.key == "btn_report_no_task_back"]
        assert len(back_btns) == 1
        back_btns[0].click().run()
    assert at.session_state["current_page"] == config.STREAMLIT_PAGE_INPUT


# =========================================================================== #
# 健壮性补强：poll_state 返回 None（snapshot 未落盘）/ 空 state 不崩
# =========================================================================== #
def test_render_with_none_state_does_not_crash():
    """poll_state 返回 None（snapshot 未落盘）→ render 不崩（state 兜底为空 dict → degraded）。"""
    controller = _make_controller_mock(None)
    at = _run(controller)  # 有 thread_id 但 poll_state 返回 None
    assert not at.exception, at.exception
    # 空 state → _determine_report_form 返回 degraded（探针实证）。
    text = _collect_text(at)
    assert "未成功复现" in text


def test_render_empty_dict_state_degraded():
    """poll_state 返回 {} → degraded 形态，全区块防御式渲染不崩。"""
    controller = _make_controller_mock({})
    at = _run(controller)
    assert not at.exception, at.exception
    text = _collect_text(at)
    assert "未成功复现" in text
