"""Sprint 5 任务 T-S5-3-5 测试：`ui/term_map.py` + 全 UI 术语清扫（S5-09 / AC-S5-18）。

覆盖 dev-plan §T-S5-3-5 检查点：

- **CP-3.5-1** ``humanize`` 单测：全 domain 全条目采样 + 未知 domain / 未知 value
  兜底文案 ``f"{value}（内部标识）"``（不崩不静默，AC-S5-18）；TERM_LABELS 条目
  与代码枚举源（ErrorCategory / _VALID_STRATEGIES / 节点名 / 三形态 / 两级档位 /
  三标注 / 三审计规则 / 三决策值）防漂移对齐；T-S5-3-4 文案定稿逐字校验。
- **CP-3.5-2** 页面渲染扫描断言：AppTest 跑 plan_review / result_report /
  execution_monitor 三核心页，断言 §7.9 列举 domain 的取值不再裸露（中文文案
  出现、裸内部枚举缺席；节点名按项目既有"中文（内部名）"括注口径）。
- **CP-3.5-3**（可测子集）term_map 为纯静态字面量表（不反向 import core，
  term 表数据源只读）；state 字段零改动由"本任务不触碰 core/"结构性保证 +
  收口 git diff 核对（见任务报告），既有 UI 用例零退化由全量回归护住。

测试策略（沿用 sp2/sp3 UI 测试范式）::

    - 纯函数直测优先（humanize / TERM_LABELS）；
    - AppTest + mock GraphController（patch app._get_controller）跑 render()，
      聚合元素树文本断言（shadcn iframe 组件 AppTest 不可见——ui.accordion 等
      iframe 内容不纳入断言面，与既有测试口径一致）；
    - 枚举源模块经 importlib 取模块对象（core.nodes.__init__ 显式 export 遮蔽
      子模块的已知坑 6）。

运行::

    .venv/bin/pytest tests/test_sprint5_t35_term_map.py -q
"""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Dict, List, Optional
from unittest.mock import MagicMock, patch

import pytest
from streamlit.testing.v1 import AppTest

from ui.term_map import TERM_LABELS, humanize


# --------------------------------------------------------------------------- #
# 公共工具
# --------------------------------------------------------------------------- #
def _collect_text(at: AppTest) -> str:
    """聚合 AppTest 元素树所有可读文本（含 expander 标题），便于断言渲染内容。"""
    parts: List[str] = []
    for collection in (at.title, at.subheader, at.caption, at.markdown,
                       at.text, at.warning, at.info, at.error):
        for el in collection:
            parts.append(str(getattr(el, "value", "")))
    for el in getattr(at, "code", []):
        parts.append(str(getattr(el, "value", "")))
    for el in getattr(at, "table", []):
        parts.append(str(getattr(el, "value", "")))
    # st.expander 标题（修复历程折叠条 error_category 落在 label 上）。
    for el in getattr(at, "expander", []):
        parts.append(str(getattr(el, "label", "")))
    return "\n".join(parts)


def _run_page(controller: MagicMock, script: str) -> AppTest:
    """patch app._get_controller（页面 from app import _get_controller），跑一次 AppTest。"""
    with patch("app._get_controller", return_value=controller):
        at = AppTest.from_string(script)
        at.run()
    return at


# =========================================================================== #
# CP-3.5-1：humanize 单测 + TERM_LABELS 表完整性 / 防漂移
# =========================================================================== #
def test_cp351_term_labels_flat_key_format():
    """TERM_LABELS 为单一扁平表：key 恒为 "{domain}:{value}" 两段、value 为非空中文文案。"""
    assert TERM_LABELS, "TERM_LABELS 不应为空"
    for key, label in TERM_LABELS.items():
        assert isinstance(key, str) and ":" in key, f"key 非 domain:value 形态: {key!r}"
        domain, _, value = key.partition(":")
        assert domain and value, f"domain/value 不得为空: {key!r}"
        assert isinstance(label, str) and label.strip(), f"文案不得为空: {key!r}"


def test_cp351_humanize_hits_every_table_entry():
    """全 domain 全条目采样：表内每个 (domain, value) 经 humanize 均命中对应文案。"""
    for key, label in TERM_LABELS.items():
        domain, _, value = key.partition(":")
        assert humanize(domain, value) == label


@pytest.mark.parametrize(
    "key,expected",
    [
        # T-S5-3-4 文案定稿清单——逐字入表，不得改写（AC-S5-07 措辞红线）。
        ("conclusion_level:science", "复现成功（科学复现）"),
        ("conclusion_level:engineering", "代码跑通（工程复现），论文实验结论未验证"),
        ("conclusion_level:none", "未成功复现（降级）"),
        ("annotation:simulation", "模拟/未验证内容"),
        ("annotation:credential_degraded", "凭证降级"),
        ("annotation:incomplete_execution", "执行不完整"),
        ("audit_rule:answer_leakage", "答案泄漏（非评估代码直接读取答案字段）"),
        ("audit_rule:hardcoded_score", "硬编码分数（评分结果由字面量写死）"),
        ("audit_rule:constant_outcome", "常量结局（评估函数恒返回常量）"),
    ],
)
def test_cp351_handoff_wording_verbatim(key: str, expected: str):
    """T-S5-3-4 交接文案定稿逐字校验（byte-exact，防转述漂移）。"""
    assert TERM_LABELS[key] == expected


def test_cp351_engineering_label_never_says_fuxian_chenggong():
    """AC-S5-07 红线：engineering 档位文案禁"复现成功"字样（表级守门）。"""
    assert "复现成功" not in TERM_LABELS["conclusion_level:engineering"]


def test_cp351_unknown_value_fallback():
    """未知 value → f"{value}（内部标识）" 兜底：不崩、原值保留不静默（AC-S5-18）。"""
    assert humanize("error_category", "weird_new_cat") == "weird_new_cat（内部标识）"
    assert humanize("code_strategy", "use_official") == "use_official（内部标识）"


def test_cp351_unknown_domain_fallback():
    """未知 domain → 同一兜底（即便 value 在别的 domain 是合法枚举，也不跨域借文案）。"""
    assert humanize("no_such_domain", "use_repo") == "use_repo（内部标识）"


def test_cp351_fallback_never_raises_on_odd_types():
    """value 为 None / int / 空串等奇异类型 → 仍返回 str 兜底，绝不抛（不崩契约）。"""
    assert humanize("node", None) == "None（内部标识）"
    assert humanize("error_category", 42) == "42（内部标识）"
    assert humanize("", "") == "（内部标识）"


# --------------------------------------------------------------------------- #
# 防漂移：TERM_LABELS 覆盖代码中真实存在的枚举全集（如实收集，绝不臆造）
# --------------------------------------------------------------------------- #
def _domain_values(domain: str) -> set:
    prefix = f"{domain}:"
    return {k[len(prefix):] for k in TERM_LABELS if k.startswith(prefix)}


def test_cp351_covers_error_category_enum_plus_degraded_literal():
    """error_category 覆盖 execution.py::ErrorCategory 全部取值 + "degraded" 降级字面量。"""
    exec_mod = importlib.import_module("core.nodes.execution")
    enum_values = {c.value for c in exec_mod.ErrorCategory}
    assert enum_values | {"degraded"} == _domain_values("error_category")


def test_cp351_covers_code_and_resource_strategies():
    """code_strategy / resource_strategy 覆盖 planning / resource_scout 的 _VALID_STRATEGIES。"""
    planning_mod = importlib.import_module("core.nodes.planning")
    scout_mod = importlib.import_module("core.nodes.resource_scout")
    assert set(planning_mod._VALID_STRATEGIES) == _domain_values("code_strategy")
    assert set(scout_mod._VALID_STRATEGIES) == _domain_values("resource_strategy")


def test_cp351_covers_all_seven_graph_nodes():
    """node 覆盖 graph.py 七节点（含 core.state.NodeName 声明的前四节点）。"""
    from typing import get_args

    state_mod = importlib.import_module("core.state")
    declared = set(get_args(state_mod.NodeName))
    seven = {
        "paper_intake", "paper_analysis", "resource_scout", "planning",
        "coding", "execution", "reporting",
    }
    assert declared <= seven, "NodeName 声明节点应是七节点子集（Sprint 1/2 遗留声明）"
    assert _domain_values("node") == seven


def test_cp351_covers_report_forms_conclusions_annotations_audit_decisions():
    """报告三形态 / 两级档位 / 三标注 / 三审计规则 / 三决策值——各 domain 值域精确对齐。"""
    assert _domain_values("report_form") == {"full_success", "code_only", "degraded"}
    assert _domain_values("conclusion_level") == {"science", "engineering", "none"}
    assert _domain_values("annotation") == {
        "simulation", "credential_degraded", "incomplete_execution",
    }
    assert _domain_values("audit_rule") == {
        "answer_leakage", "hardcoded_score", "constant_outcome",
    }
    assert _domain_values("user_fix_decision") == {
        "terminate", "revise_plan", "export_code",
    }


def test_cp351_fix_strategy_has_no_entries_by_design():
    """fix_strategy 无表条目属如实收集（非遗漏）：其写入源是 fix_hint 自由中文文本
    （execution.py L1802），代码中不存在可枚举固定取值；页面对其原样渲染不经表。"""
    assert not _domain_values("fix_strategy")


# =========================================================================== #
# CP-3.5-2：页面渲染扫描断言（核心页面无裸内部枚举，覆盖 §7.9 列举 domain）
# =========================================================================== #
_PLAN_REVIEW_SCRIPT = """
import streamlit as st
st.session_state.setdefault("thread_id", "task-t35-review")
st.session_state.setdefault("current_page", "review")
from ui.pages.plan_review import render
render()
"""

_REPORT_SCRIPT = """
import streamlit as st
st.session_state.setdefault("thread_id", "task-t35-report")
st.session_state.setdefault("current_page", "report")
from ui.pages.result_report import render
render()
"""

_EXEC_SCRIPT = """
import streamlit as st
st.session_state.setdefault("thread_id", "task-t35-exec")
st.session_state.setdefault("current_page", "execution")
from ui.pages.execution_monitor import render
render()
"""


def _make_review_controller() -> MagicMock:
    """plan_review 页 mock：payload 携带 code_strategy / resource_strategy /
    degraded_nodes / node_errors 四类内部枚举，全部应经 humanize 渲染。"""
    payload = {
        "reproduction_plan": {
            "plan_summary": "复现目标方法",
            "environment": {},
            "data_preparation": ["准备数据"],
            "code_strategy": "use_repo",
            "execution_steps": [],
            "expected_results": [],
            "estimated_time": "约 1 小时",
            "deliverables": ["复现报告"],
        },
        "resource_info": {
            "repos": [{"url": "https://github.com/acme/demo", "is_official": True,
                       "stars": 10, "forks": 2, "quality_score": 0.8}],
            "selected_repo": {"url": "https://github.com/acme/demo"},
            "resource_strategy": "hybrid",
        },
        "paper_analysis_summary": {},
        "degraded_nodes": ["planning"],
        "node_errors": [{"node_name": "resource_scout",
                         "error_type": "transient",
                         "error_message": "检索一度失败已重试成功"}],
        "revise_count": 0,
        "soft_hint_threshold": 5,
        "max_total_llm_calls": 120,
    }
    controller = MagicMock()
    controller.poll_state.return_value = {"llm_config_set": None}
    controller.get_interrupt_payload.return_value = payload
    controller.is_interrupted.return_value = True
    controller.get_worker_error.return_value = None
    return controller


def test_cp352_plan_review_no_bare_enums():
    """plan_review：code_strategy / resource_strategy / 节点名裸露点全部经 humanize。

    原裸露点：:279 代码策略 `use_repo`、:329 资源策略 hybrid、透明化 info-bar
    降级节点、node_errors 节点名（T-S5-3-5 清扫对象）。
    """
    at = _run_page(_make_review_controller(), _PLAN_REVIEW_SCRIPT)
    assert not at.exception, at.exception
    text = _collect_text(at)
    # 中文文案出现（humanize 生效）。
    assert "使用现有仓库" in text                        # code_strategy:use_repo
    assert "混合（仓库为主 + 部分自研）" in text          # resource_strategy:hybrid
    assert "制定计划（planning）" in text                # 降级节点：中文 +（内部名）括注
    assert "资源侦察（resource_scout）" in text          # node_errors 节点名
    # 裸内部枚举缺席（策略值不再以原値渲染；fixture 其它字段不含这两个 token）。
    assert "use_repo" not in text
    assert "hybrid" not in text


def _make_report_controller(state: Dict) -> MagicMock:
    controller = MagicMock()
    controller.poll_state.return_value = state
    return controller


def _report_state_base(report_path: str) -> Dict:
    return {
        "report_path": report_path,
        "execution_mode": "full",
        "paper_analysis": {"baseline_results": {"acc": 0.9}},
        "reproduction_plan": {"expected_results": {"acc": 0.92},
                              "deliverables": ["复现报告"]},
        "code_output_dir": "workspace/t35/code",
        "fix_loop_count": 1,
        "fix_loop_history": [
            {"round_number": 1, "error_category": "dependency",
             "error_summary": "缺 torch", "fix_strategy": "补齐依赖后重试",
             "timestamp": "t1"},
        ],
    }


def test_cp352_result_report_engineering_card_and_no_expected_column(tmp_path):
    """result_report（full_success/engineering）：卡片按报告新措辞、表无 expected 列、
    error_category 中文化（AC-S5-07/09/18 口径差同步）。"""
    report = tmp_path / "report.md"
    report.write_text("# 报告\n执行成功。", encoding="utf-8")
    state = _report_state_base(str(report))
    state["execution_result"] = {
        "success": True, "metrics": {"acc": 0.88}, "logs": "ok", "errors": [],
        "artifacts": [], "runtime_seconds": 3.0, "environment_info": {},
    }
    at = _run_page(_make_report_controller(state), _REPORT_SCRIPT)
    assert not at.exception, at.exception
    text = _collect_text(at)
    # 两级措辞（旧 dict 形态 expected → goal_checks 全未验证 → engineering）。
    assert "代码跑通（工程复现），论文实验结论未验证" in text
    assert "复现成功" not in text          # AC-S5-07 红线（engineering 全文禁词）
    # 指标表删 expected 列（与报告正文 _comparison_table 同口径）。
    assert "论文 baseline" in text
    assert "计划 expected" not in text
    # 修复历程 error_category 中文化，裸值缺席。
    assert "依赖错误" in text
    assert "dependency" not in text


def test_cp352_result_report_science_card(tmp_path):
    """result_report（full_success/science）：回验全符合且无标注 → 「复现成功（科学复现）」。"""
    report = tmp_path / "report.md"
    report.write_text("# 报告\n回验全部符合。", encoding="utf-8")
    state = _report_state_base(str(report))
    state["fix_loop_history"] = []
    state["reproduction_plan"] = {
        "expected_results": [
            {"description": "主实验优于基线",
             "trend": {"metric": "acc", "greater": "main", "lesser": "baseline"}},
        ],
        "deliverables": ["复现报告"],
    }
    state["execution_result"] = {
        "success": True, "metrics": {"acc": 0.9}, "logs": "ok", "errors": [],
        "artifacts": [], "runtime_seconds": 3.0, "environment_info": {},
        "metrics_groups": {"main": {"acc": 0.9}, "baseline": {"acc": 0.5}},
    }
    at = _run_page(_make_report_controller(state), _REPORT_SCRIPT)
    assert not at.exception, at.exception
    text = _collect_text(at)
    assert "复现成功（科学复现）" in text


def test_cp352_result_report_degraded_humanized(tmp_path):
    """result_report（degraded）：降级节点括注中文、user_fix_decision 中文化。"""
    report = tmp_path / "report.md"
    report.write_text("# 报告\n本次未完成。", encoding="utf-8")
    state = _report_state_base(str(report))
    state["execution_result"] = {
        "success": False, "metrics": {}, "logs": "boom",
        "errors": ["ImportError: no module named foo"], "artifacts": [],
        "runtime_seconds": 1.0, "environment_info": {},
    }
    state["degraded_nodes"] = ["execution"]
    state["user_fix_decision"] = "terminate"
    at = _run_page(_make_report_controller(state), _REPORT_SCRIPT)
    assert not at.exception, at.exception
    text = _collect_text(at)
    assert "未成功复现（降级）" in text
    assert "执行验证（execution）" in text     # 降级节点：中文 +（内部名）括注
    assert "终止任务" in text                  # user_fix_decision:terminate
    assert "terminate" not in text             # 裸决策值缺席


def _make_exec_controller(
    state: Optional[Dict],
    *,
    is_interrupted: bool = False,
    kind: str = "",
    payload: Optional[Dict] = None,
) -> MagicMock:
    controller = MagicMock()
    controller.get_worker_error.return_value = None
    controller.poll_state.return_value = state
    controller.is_interrupted.return_value = is_interrupted
    controller.interrupt_kind.return_value = kind
    controller.get_interrupt_payload.return_value = payload
    controller.is_finished.return_value = False
    return controller


def test_cp352_execution_monitor_dev_loop_panel_humanized():
    """execution_monitor（dev_loop 决策面板）：最近错误分类 + 修复历程分类中文化。"""
    state = {"current_step": "execution", "error": None}
    payload = {
        "fix_loop_count": 2,
        "error_category": "dependency",
        "error_summary": "依赖反复装不上",
        "fix_loop_history": [
            {"round_number": 1, "error_summary": "缺 torch",
             "error_category": "import", "fix_strategy": "补齐依赖"},
        ],
    }
    controller = _make_exec_controller(
        state, is_interrupted=True, kind="dev_loop_failure", payload=payload
    )
    at = _run_page(controller, _EXEC_SCRIPT)
    assert not at.exception, at.exception
    text = _collect_text(at)
    assert "依赖错误" in text          # 最近错误分类（dependency）
    assert "导入错误" in text          # 修复历程折叠标题（import）
    assert "dependency" not in text    # 裸枚举缺席（fixture 其它字段不含该 token）


def test_cp352_execution_monitor_degraded_nodes_annotated():
    """execution_monitor（正常渲染路径）：降级节点中文 +（内部名）括注（G8 锚点保留）。"""
    state = {
        "current_step": "execution", "error": None,
        "fix_loop_count": 0, "fix_loop_history": [],
        "execution_result": None, "node_errors": [],
        "degraded_nodes": ["coding"],
    }
    controller = _make_exec_controller(state)
    at = _run_page(controller, _EXEC_SCRIPT)
    assert not at.exception, at.exception
    warn_texts = "\n".join(str(getattr(w, "value", "")) for w in at.warning)
    assert "降级节点" in warn_texts
    assert "代码生成（coding）" in warn_texts


# =========================================================================== #
# CP-3.5-3（可测子集）：term_map 纯静态、只读数据源；渲染层单向依赖
# =========================================================================== #
def test_cp353_term_map_is_pure_static_table_no_core_import():
    """term_map 不 import core（术语表为字面量常量，数据源只读、零 state 触碰）；
    模块公开面恰为一表一函数（极简契约：不建分层/命名空间类，不做 i18n 框架）。"""
    import ui.term_map as tm

    src = Path(tm.__file__).read_text(encoding="utf-8")
    code_lines = [
        line for line in src.splitlines()
        if line.strip().startswith(("import ", "from "))
    ]
    assert all("core" not in line for line in code_lines), code_lines
    assert tm.__all__ == ["TERM_LABELS", "humanize"]
