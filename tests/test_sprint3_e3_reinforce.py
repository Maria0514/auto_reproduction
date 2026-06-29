"""Sprint 3 任务 E3 独立验收补强：`ui/pages/result_report.py`（S3-10 结果报告页）。

由测试工程师代理在 E3 独立验收中新建，补强开发自测（`tests/test_sprint3_e3.py` 23 条）
未覆盖的边界 / 集成契约重锤维度。与开发套件互补，不重复：

    1. **三形态判定优先级穷举**（code_only > full_success > degraded；success 严格 is True
       的 truthy 边界；str execution_mode；空 state）——逐一断言 E3 与 reporting 同口径。
    2. **页面卡片形态 vs reporting 报告正文形态严格一致**（集成契约重锤：同一 state 双路
       对比，杜绝「页面说成功、正文说降级」两份矛盾结论，CP-E3-2 命门）。
    3. **B 档无硬判定红线（精确版，Q-S3-01）**：指标对比表 DataFrame 列名 / 单元格绝无
       「达标 / 不达标 / 未达标」结论字样；渲染文本中「达标」仅允许出现在「不做……达标
       结论」否定声明里（不得作为某指标的逐项硬性结论）。
    4. **指标对比表三方并集 / 无 baseline / 缺值「—」/ 脏数据防御 / degraded 也展示**。
    5. **report_path 读失败降级**：非 UTF-8 文件（BUG-S3-E3-01）/ 目录路径 / 空路径。
    6. **artifact 空清单 / fix_loop_history 空与多轮与脏项 / deliverables 缺失**。
    7. **返回输入页出口状态重置完整性 + widget key 唯一性**（D3 教训：no_thread 出口键与
       正常出口键不同，避免 StreamlitDuplicateElementKey）。

测试策略沿用 sp2 / E3 范式：逻辑层断言优先（纯函数直测），AppTest + mock GraphController
（patch app._get_controller）跑 render() 验渲染/出口。不跑 e2e（省 deepxiv 配额）。

运行::

    .venv/bin/pytest tests/test_sprint3_e3_reinforce.py -q
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
# 公共工具（坑6：用 importlib 取模块，避免 core.nodes.__init__ callable 遮蔽子模块）
# --------------------------------------------------------------------------- #
def _report_mod():
    return importlib.import_module("ui.pages.result_report")


def _reporting_mod():
    return importlib.import_module("core.nodes.reporting")


def _make_controller_mock(state: Optional[Dict]) -> MagicMock:
    controller = MagicMock()
    controller.poll_state.return_value = state
    return controller


_APP_SCRIPT = """
import streamlit as st
st.session_state.setdefault("thread_id", "task-e3r-001")
st.session_state.setdefault("current_page", "report")
from ui.pages.result_report import render
render()
"""

_APP_SCRIPT_NO_THREAD = """
from ui.pages.result_report import render
render()
"""


def _run(controller: MagicMock, script: str = _APP_SCRIPT) -> AppTest:
    with patch("app._get_controller", return_value=controller):
        at = AppTest.from_string(script)
        at.run()
    return at


def _collect_text(at: AppTest) -> str:
    parts: List[str] = []
    for collection in (at.title, at.subheader, at.caption, at.markdown,
                       at.text, at.warning, at.info, at.error):
        for el in collection:
            parts.append(str(getattr(el, "value", "")))
    for el in getattr(at, "code", []):
        parts.append(str(getattr(el, "value", "")))
    for el in getattr(at, "table", []):
        parts.append(str(getattr(el, "value", "")))
    return "\n".join(parts)


# =========================================================================== #
# 1. 三形态判定优先级穷举（与 reporting 同口径；同一函数对象本应一致，
#    此处验证 E3 实际调用路径无额外包装/覆盖，且优先级符合 dev-plan/architecture §2.4）
# =========================================================================== #
def test_form_priority_code_only_beats_success():
    """code_only 优先于 full_success：execution_mode==CODE_ONLY 即便 success=True 也走 code_only。"""
    mod = _report_mod()
    rep = _reporting_mod()
    state = {"execution_mode": ExecutionMode.CODE_ONLY,
             "execution_result": {"success": True, "metrics": {"a": 1}}}
    assert mod._determine_report_form(state) == "code_only"
    assert mod._determine_report_form(state) == rep._determine_report_form(state)


def test_form_str_execution_mode_code_only():
    """execution_mode 为裸字符串 'code_only'（非 Enum）也判 code_only（兼容 Enum/str）。"""
    mod = _report_mod()
    rep = _reporting_mod()
    state = {"execution_mode": "code_only", "execution_result": {"success": True}}
    assert mod._determine_report_form(state) == "code_only"
    assert mod._determine_report_form(state) == rep._determine_report_form(state)


@pytest.mark.parametrize(
    "success_val,expected",
    [
        (True, "full_success"),   # 严格 is True 才 full_success
        (1, "degraded"),          # truthy 但非 True → degraded
        ("true", "degraded"),
        ("True", "degraded"),
        ({"x": 1}, "degraded"),
        (False, "degraded"),
        (None, "degraded"),
    ],
)
def test_form_success_strict_is_true(success_val, expected):
    """full_success 判定严格 `success is True`：任何 truthy 非 True 值都降级（防误判成功）。"""
    mod = _report_mod()
    rep = _reporting_mod()
    state = {"execution_mode": ExecutionMode.FULL,
             "execution_result": {"success": success_val}}
    assert mod._determine_report_form(state) == expected
    assert mod._determine_report_form(state) == rep._determine_report_form(state)


def test_form_exec_result_none_non_code_only_degraded():
    """execution_result is None 且非 code_only → degraded（不臆造成功/code_only）。"""
    mod = _report_mod()
    state = {"execution_mode": ExecutionMode.FULL, "execution_result": None}
    assert mod._determine_report_form(state) == "degraded"


def test_form_empty_and_none_state_degraded():
    """空 dict / 缺字段 state → degraded（F5 后 poll_state 返回空时的兜底形态）。"""
    mod = _report_mod()
    assert mod._determine_report_form({}) == "degraded"


# =========================================================================== #
# 2. 集成契约重锤：页面卡片形态 == reporting 报告正文形态（杜绝两份矛盾结论）
# =========================================================================== #
def _state_for_form(form: str, report_path: str = "/tmp/x.md") -> Dict:
    base = {
        "report_path": report_path,
        "paper_meta": {"arxiv_id": "2405.14831", "title": "T"},
        "code_output_dir": "/tmp/ws/2405.14831/code",
    }
    if form == "full_success":
        base.update({"execution_mode": ExecutionMode.FULL,
                     "execution_result": {"success": True, "metrics": {"acc": 0.9},
                                          "artifacts": ["a.json"], "errors": []}})
    elif form == "code_only":
        base.update({"execution_mode": ExecutionMode.CODE_ONLY,
                     "execution_result": None,
                     "reproduction_plan": {"deliverables": ["x"]}})
    else:  # degraded
        base.update({"execution_mode": ExecutionMode.FULL,
                     "execution_result": {"success": False, "errors": ["boom"]},
                     "degraded_nodes": ["execution"], "user_fix_decision": "export_code"})
    return base


@pytest.mark.parametrize("form", ["full_success", "code_only", "degraded"])
def test_card_form_matches_report_body_form(form):
    """CP-E3-2 命门：同一 state 下，E3 卡片判定形态 == reporting 报告正文头部声明的形态。

    若两者不一致即出现「页面卡片说成功、报告正文说降级」——这正是 E3 复用
    reporting._determine_report_form 要杜绝的。此处用 reporting 真实生成的 Markdown
    正文（_render_report 内部也走同一判定）做交叉验证，不依赖「同一函数对象」这一前提。
    """
    import re
    mod = _report_mod()
    rep = _reporting_mod()
    state = _state_for_form(form)

    card_form = mod._determine_report_form(state)
    body = rep._render_report(state, rep._determine_report_form(state))
    m = re.search(r"报告形态: `(\w+)`", body)
    assert m, "reporting 报告正文应在头部声明报告形态"
    body_form = m.group(1)

    assert card_form == body_form == form, (
        f"页面卡片形态({card_form}) 与报告正文形态({body_form}) 不一致 → 两份矛盾结论"
    )


# =========================================================================== #
# 3. B 档无硬判定红线（精确版，Q-S3-01）
# =========================================================================== #
_HARD_JUDGEMENT_WORDS = ["达标", "不达标", "未达标"]


def test_metric_table_has_no_hard_judgement_column_or_cell():
    """指标对比表 DataFrame 的列名与单元格绝无「达标/不达标/未达标」结论字样。"""
    mod = _report_mod()
    state = _state_for_form("full_success", report_path="/tmp/x.md")
    state["paper_analysis"] = {"baseline_results": {"acc": 0.91}}
    state["reproduction_plan"] = {"expected_results": {"acc": 0.92}, "deliverables": ["d"]}
    rows = mod._metric_comparison_rows(state)
    assert rows, "应有可对比指标"
    serialized = "".join(str(k) for r in rows for k in r.keys())
    serialized += "".join(str(v) for r in rows for v in r.values())
    for w in _HARD_JUDGEMENT_WORDS:
        assert w not in serialized, f"指标对比表行不得出现硬判定字样「{w}」"


@pytest.mark.parametrize("form", ["full_success", "degraded"])
def test_render_no_hard_judgement_except_negation(form, tmp_path):
    """渲染文本中「达标」只允许出现在「不做……达标结论」否定声明里，不得作逐项硬判定。

    full_success / degraded 两形态都展示指标对比表，均不得对具体指标下「X 达标/不达标」。
    """
    report = tmp_path / "report.md"
    report.write_text("# 报告\nbody", encoding="utf-8")
    state = _state_for_form(form, report_path=str(report))
    state["paper_analysis"] = {"baseline_results": {"acc": 0.91}}
    state["reproduction_plan"] = {"expected_results": {"acc": 0.92}}
    if form == "full_success":
        state["execution_result"]["metrics"] = {"acc": 0.9}
    else:
        state["execution_result"]["metrics"] = {"acc": 0.5}

    at = _run(_make_controller_mock(state))
    assert not at.exception, at.exception
    # 「不达标」「未达标」绝不允许出现（这些必是逐项硬结论）。
    text = _collect_text(at)
    assert "不达标" not in text
    assert "未达标" not in text
    # 「达标」若出现，必须只在否定声明上下文（如「不做任何硬性达标结论」），
    # 即「达标」前应紧邻否定词「不做」/「不」。
    idx = 0
    while True:
        idx = text.find("达标", idx)
        if idx == -1:
            break
        ctx = text[max(0, idx - 8):idx + 2]
        assert ("不做" in ctx) or ("不" in ctx), (
            f"「达标」出现在非否定上下文，疑似逐项硬判定：...{ctx}..."
        )
        idx += 2


# =========================================================================== #
# 4. 指标对比表：三方并集 / 无 baseline / 缺值 / 脏数据 / degraded 也展示
# =========================================================================== #
def test_metric_rows_union_three_sources_with_gaps():
    """三方指标名并集：repro 有 a+b、baseline 仅 a、expected 仅 b → 行为 {a,b}，缺值 None。"""
    mod = _report_mod()
    state = {
        "execution_result": {"metrics": {"a": 0.1, "b": 0.2}},
        "paper_analysis": {"baseline_results": {"a": 0.11}},
        "reproduction_plan": {"expected_results": {"b": 0.22}},
    }
    rows = mod._metric_comparison_rows(state)
    names = {r["指标 (Metric)"] for r in rows}
    assert names == {"a", "b"}
    row_a = next(r for r in rows if r["指标 (Metric)"] == "a")
    row_b = next(r for r in rows if r["指标 (Metric)"] == "b")
    assert row_a["论文 baseline"] == 0.11 and row_a["计划 expected"] is None
    assert row_b["计划 expected"] == 0.22 and row_b["论文 baseline"] is None


def test_metric_rows_no_baseline_only_expected_and_repro():
    """无 baseline，仅 expected + 复现 → 仍构造行，baseline 列 None。"""
    mod = _report_mod()
    state = {"execution_result": {"metrics": {"acc": 0.9}},
             "reproduction_plan": {"expected_results": {"acc": 0.91}}}
    rows = mod._metric_comparison_rows(state)
    assert len(rows) == 1
    assert rows[0]["论文 baseline"] is None
    assert rows[0]["计划 expected"] == 0.91
    assert rows[0]["本次复现值"] == 0.9


def test_metric_rows_dirty_sources_defensive():
    """脏数据：baseline / expected 非 dict（字符串/None）→ 不抛，对应列 None。"""
    mod = _report_mod()
    state = {
        "execution_result": {"metrics": {"acc": 0.9}},
        "paper_analysis": {"baseline_results": "garbage"},
        "reproduction_plan": {"expected_results": None},
    }
    rows = mod._metric_comparison_rows(state)
    assert len(rows) == 1
    assert rows[0]["论文 baseline"] is None
    assert rows[0]["计划 expected"] is None
    assert rows[0]["本次复现值"] == 0.9


def test_metric_rows_none_state_returns_empty():
    """state 为 None / execution_result 非 dict → 空列表，不抛。"""
    mod = _report_mod()
    assert mod._metric_comparison_rows(None) == []
    assert mod._metric_comparison_rows({"execution_result": "x"}) == []


def test_degraded_form_also_renders_metric_table_with_dash_for_gaps(tmp_path):
    """degraded 形态也展示指标对比表（开发只验了 full_success），缺值渲染「—」。"""
    report = tmp_path / "report.md"
    report.write_text("# 报告\nfail", encoding="utf-8")
    state = _state_for_form("degraded", report_path=str(report))
    # baseline 有 acc，复现缺 acc 但有 f1（制造缺值），expected 空。
    state["execution_result"]["metrics"] = {"f1": 0.5}
    state["paper_analysis"] = {"baseline_results": {"acc": 0.9}}
    at = _run(_make_controller_mock(state))
    assert not at.exception, at.exception
    text = _collect_text(at)
    assert "指标对比" in text, "degraded 形态也应展示指标对比表"
    assert "acc" in text and "f1" in text
    # 缺值占位「—」（acc 缺复现值；f1 缺 baseline）。
    assert "—" in text


# =========================================================================== #
# 5. report_path 读失败降级（含 BUG-S3-E3-01）
# =========================================================================== #
def test_load_report_directory_path_degrades_not_crash(tmp_path):
    """report_path 指向目录（IsADirectoryError ∈ OSError）→ 降级提示，不抛。"""
    mod = _report_mod()
    md, err = mod._load_report_markdown(str(tmp_path))
    assert md is None
    assert err and "读取失败" in err


def test_load_report_empty_path_degrades():
    """report_path 为空字符串 / None → 降级提示，不抛。"""
    mod = _report_mod()
    md1, err1 = mod._load_report_markdown("")
    md2, err2 = mod._load_report_markdown(None)
    assert md1 is None and err1
    assert md2 is None and err2


def test_load_report_non_utf8_file_degrades_not_crash(tmp_path):
    """BUG-S3-E3-01：report_path 指向非 UTF-8 文件 → 应降级提示而非抛 UnicodeDecodeError。"""
    mod = _report_mod()
    bad = tmp_path / "bad.md"
    bad.write_bytes(b"\xff\xfe\x00not utf-8 bytes")
    md, err = mod._load_report_markdown(str(bad))  # 当前抛 UnicodeDecodeError
    assert md is None
    assert err and ("读取失败" in err or "编码" in err)


def test_render_non_utf8_report_does_not_crash_page(tmp_path):
    """BUG-S3-E3-01：非 UTF-8 报告文件在真实 render 路径下不应让页面崩（应降级 warning）。"""
    bad = tmp_path / "bad.md"
    bad.write_bytes(b"\xff\xfe\x00not utf-8 bytes")
    state = _state_for_form("full_success", report_path=str(bad))
    state["execution_result"]["metrics"] = {"acc": 0.9}
    at = _run(_make_controller_mock(state))
    assert not at.exception, at.exception


# =========================================================================== #
# 6. artifact 空清单 / fix_loop_history 空与多轮与脏项 / deliverables 缺失
# =========================================================================== #
def test_artifact_list_filters_empty_and_none():
    """artifact 清单过滤空串 / None，仅保留有效路径。"""
    mod = _report_mod()
    state = {"execution_result": {"artifacts": ["a.json", "", None, "b.png"]}}
    assert mod._artifact_list(state) == ["a.json", "b.png"]


def test_artifact_empty_renders_placeholder_caption(tmp_path):
    """artifact 空清单 → render 给「未收集到产物」caption，不崩。"""
    report = tmp_path / "report.md"
    report.write_text("# 报告\nok", encoding="utf-8")
    state = _state_for_form("full_success", report_path=str(report))
    state["execution_result"]["metrics"] = {"acc": 0.9}
    state["execution_result"]["artifacts"] = []
    at = _run(_make_controller_mock(state))
    assert not at.exception, at.exception
    text = _collect_text(at)
    assert "产物清单" in text
    assert "未" in text and "产物" in text


def test_fix_loop_rows_multi_round_and_dirty_items():
    """fix_loop_history 多轮 + 混入非 dict 脏项 → 只取 dict 项，顺序与轮次保留。"""
    mod = _report_mod()
    hist = [
        {"round_number": 1, "error_category": "dependency_error",
         "error_summary": "缺 torch", "fix_strategy": "补依赖"},
        "junk", None, 123,
        {"round_number": 2, "error_category": "runtime_error",
         "error_summary": "OOM", "fix_strategy": "降 batch"},
    ]
    rows = mod._fix_loop_rows({"fix_loop_history": hist})
    assert len(rows) == 2
    assert [r["轮次"] for r in rows] == [1, 2]
    assert rows[1]["错误分类 (error_category)"] == "runtime_error"


def test_fix_loop_rows_empty_history():
    """fix_loop_history 空 / 缺失 → 空列表，render 给「无逐轮修复记录」caption。"""
    mod = _report_mod()
    assert mod._fix_loop_rows({}) == []
    assert mod._fix_loop_rows({"fix_loop_history": None}) == []


def test_fix_loop_count_zero_caption(tmp_path):
    """fix_loop_count=0 且无 history → render 修复历程区块显示 0 回合 caption，不崩。"""
    report = tmp_path / "report.md"
    report.write_text("# 报告\nok", encoding="utf-8")
    state = _state_for_form("full_success", report_path=str(report))
    state["execution_result"]["metrics"] = {"acc": 0.9}
    state["fix_loop_count"] = 0
    state["fix_loop_history"] = []
    at = _run(_make_controller_mock(state))
    assert not at.exception, at.exception
    text = _collect_text(at)
    assert "修复历程" in text


def test_deliverables_missing_renders_caption(tmp_path):
    """reproduction_plan 无 deliverables → render 代码/交付物区块给 caption，不崩。"""
    report = tmp_path / "report.md"
    report.write_text("# 报告\nok", encoding="utf-8")
    state = _state_for_form("full_success", report_path=str(report))
    state["execution_result"]["metrics"] = {"acc": 0.9}
    state["reproduction_plan"] = {}  # 无 deliverables
    at = _run(_make_controller_mock(state))
    assert not at.exception, at.exception
    text = _collect_text(at)
    assert "代码位置与交付物" in text


def test_deliverables_list_defensive():
    """_deliverables_list 防御：plan 非 dict / 缺失 → 空列表，过滤空项。"""
    mod = _report_mod()
    assert mod._deliverables_list({}) == []
    assert mod._deliverables_list({"reproduction_plan": "x"}) == []
    assert mod._deliverables_list({"reproduction_plan": {"deliverables": ["a", "", None, "b"]}}) == ["a", "b"]


# =========================================================================== #
# 7. 返回输入页出口状态重置完整性 + widget key 唯一性（D3 教训）
# =========================================================================== #
def test_reset_to_input_resets_all_three_keys():
    """_reset_to_input_page 三件套：切 input 页 + 解锁 _input_submitted + 清 thread_id。"""
    import streamlit as st
    mod = _report_mod()
    st.session_state["current_page"] = "report"
    st.session_state["_input_submitted"] = True
    st.session_state["thread_id"] = "task-old"
    mod._reset_to_input_page()
    assert st.session_state["current_page"] == config.STREAMLIT_PAGE_INPUT
    assert st.session_state["_input_submitted"] is False
    assert st.session_state["thread_id"] is None


def test_back_button_keys_unique_across_two_exit_paths():
    """两个返回出口（正常页 / no_thread 占位页）的按钮 key 不同，避免 DuplicateElementKey。

    D3 教训：同页/多渲染路径 widget key 冲突 → StreamlitDuplicateElementKey。E3 正常页
    用 btn_report_new_task、no_thread 占位页用 btn_report_no_task_back，必须互异。
    """
    # 正常页（有 thread + state）的出口按钮 key。
    import tempfile, os
    fd, rp = tempfile.mkstemp(suffix=".md")
    os.write(fd, b"# r\nok"); os.close(fd)
    state = _state_for_form("full_success", report_path=rp)
    state["execution_result"]["metrics"] = {"acc": 0.9}
    at_normal = _run(_make_controller_mock(state))
    assert not at_normal.exception, at_normal.exception
    normal_keys = {b.key for b in at_normal.button}

    # no_thread 占位页的出口按钮 key。
    at_no_thread = _run(_make_controller_mock(None), script=_APP_SCRIPT_NO_THREAD)
    assert not at_no_thread.exception, at_no_thread.exception
    no_thread_keys = {b.key for b in at_no_thread.button}

    assert "btn_report_new_task" in normal_keys
    assert "btn_report_no_task_back" in no_thread_keys
    # 两套出口按钮 key 不相交（不同渲染路径，杜绝跨路径冲突隐患）。
    assert normal_keys.isdisjoint(no_thread_keys)
    os.unlink(rp)


def test_no_thread_path_does_not_call_poll_state():
    """无 thread_id 分支在取 controller 前 return → 不触 poll_state（F5 兜底不误读旧 thread）。"""
    controller = _make_controller_mock(None)
    at = _run(controller, script=_APP_SCRIPT_NO_THREAD)
    assert not at.exception, at.exception
    controller.poll_state.assert_not_called()


def test_back_button_click_full_reset_via_apptest():
    """点正常页「返回输入页开启新任务」→ session_state 三件套全部重置（AppTest 真实点击）。"""
    import tempfile, os
    fd, rp = tempfile.mkstemp(suffix=".md")
    os.write(fd, b"# r\nok"); os.close(fd)
    state = _state_for_form("full_success", report_path=rp)
    state["execution_result"]["metrics"] = {"acc": 0.9}
    controller = _make_controller_mock(state)
    script = """
import streamlit as st
st.session_state.setdefault("thread_id", "task-e3r-001")
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
        assert len(btns) == 1
        btns[0].click().run()
    assert at.session_state["current_page"] == config.STREAMLIT_PAGE_INPUT
    assert at.session_state["_input_submitted"] is False
    assert at.session_state["thread_id"] is None
    os.unlink(rp)


# =========================================================================== #
# 8. E3 只新建、不碰 app.py / 其它生产代码（隔离边界守门）
# =========================================================================== #
def test_e3_reuses_reporting_determine_form_same_object():
    """守门：E3 import 的 _determine_report_form 与 reporting 子模块同一函数对象（不臆造判定）。"""
    mod = _report_mod()
    rep = _reporting_mod()
    assert mod._determine_report_form is rep._determine_report_form


def test_e3_page_map_entry_consistent_with_app():
    """守门：app._PAGE_MAP[STREAMLIT_PAGE_REPORT] 指向 result_report.render_result_report_page。"""
    import app
    mod = _report_mod()
    module_name, func_name = app._PAGE_MAP[config.STREAMLIT_PAGE_REPORT]
    assert module_name == "ui.pages.result_report"
    assert func_name == "render_result_report_page"
    assert getattr(mod, func_name) is mod.render
