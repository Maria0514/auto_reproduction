"""T-S5-5-2（其二）：AC-S5-01~21 覆盖矩阵参数化审计（Sprint 级聚合断言）。

dev-plan §5 AC 覆盖矩阵的可执行版，沿用 sp4 G1 范式（tests/test_sprint4_g1.py）
并全量继承其三重防假绿：

    CP-5.2-3 ① 存在性——映射用例存在于对应模块且 callable、名字以 test_ 开头
             （支持 t11 的类方法形态 "TestCls.test_x"）；
             ② mark 审计——映射用例不得带 skip / e2e mark（e2e 被 pytest.ini
                addopts 默认排除，映射进矩阵即假绿）；
             ③ AST 断言实质性审计——每个映射用例函数体须含 ≥1 个真实断言构造
                （防"名字对但断言空泛"，G1 规格明文）。

    元断言：矩阵键恰为 AC-S5-01 ~ AC-S5-21 全 21 条（sp5 与 sp4 不同：每条 AC
    都有 mock 层核心证据，手动/授权项以 GAP_MANIFEST 显式落档而非从矩阵剔除）。

    缺口清单（GAP_MANIFEST）：AC 的"手动/授权"部分不可由默认回归覆盖的事实
    显式建档并断言恰为预期集合——缺口静默增删即红（交 T-S5-5-3 的授权/手动
    清单以此为准）。

映射选取原则（沿用 G1）：每条 AC 至少含核心判定用例（非旁证）；跨任务 AC
逐落点各取主证；t52_regression_targets 的五条靶测 + 三落点串联按 dev-plan §5
"T-S5-5-2 / CP-5.2-1/2" 列入对应 AC。

全部离线静态审计：importlib + inspect + ast，零 LLM、零网络、零 fixture 写入。
"""

from __future__ import annotations

import ast
import importlib
import inspect
import textwrap
from pathlib import Path
from typing import Dict, List, Tuple

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# ===========================================================================
# CP-5.2-3：AC → 用例覆盖矩阵（dev-plan §5 的可执行版）
# ---------------------------------------------------------------------------
# 条目形态：{AC: [(测试模块, [用例名, ...]), ...]}；类方法用 "TestCls.test_x"。
# 关键检查点来源：dev-plan §5 矩阵"关键检查点"列 + 各批次验收（批次 1 收口
# 1467 绿 / 批次 2+4 收口 1588 绿 / T-S5-5-1 收口 1702 绿，主控归档）。
# ===========================================================================

AC_COVERAGE_MAP: Dict[str, List[Tuple[str, List[str]]]] = {
    "AC-S5-01": [  # 计划含凭证声明；回归样本场景非空（CP-1.5-1/2/4 + CP-5.2-1）
        ("tests.test_sprint5_t15_planning_prompt", [
            "test_cp_1_5_1_prompt_required_credentials_instruction",
            "test_cp_1_5_2_schema_required_credentials_two_keys",
            "test_cp_1_5_4_with_values_passthrough",
        ]),
        ("tests.test_sprint5_t52_regression_targets", [
            "test_cp_5_2_1_ac01_regression_scenario_plan_credentials_nonempty",
        ]),
    ],
    "AC-S5-02": [  # 缺凭证 interrupt#3 或降级标记，无静默绕过（CP-2.2-1/2）
        ("tests.test_sprint5_t22_coding_gate", [
            "test_cp_2_2_1_missing_credential_payload_five_keys",
            "test_cp_2_2_1_secrets_hit_zero_interrupt",
            "test_cp_2_2_2_degrade_lands_in_state_and_stops_blocking",
        ]),
    ],
    "AC-S5-03": [  # 降级标记三落点全链路（CP-2.2-2 / CP-2.4-1 / CP-3.3-2 / CP-3.4-3 / CP-5.2-2）
        ("tests.test_sprint5_t22_coding_gate", [
            "test_cp_2_2_2_degrade_lands_in_state_and_stops_blocking",
        ]),
        ("tests.test_sprint5_t24_reconcile", [
            "test_cp_2_4_1_degraded_credentials_defensive_default",
        ]),
        ("tests.test_sprint5_t33_conclusion", [
            "test_cp_3_3_2_credential_degraded_from_snapshot",
        ]),
        ("tests.test_sprint5_t34_report_render", [
            "test_cp_3_4_3_notice_block_credential_degraded_purpose_lookup",
        ]),
        ("tests.test_sprint5_t52_regression_targets", [
            "test_cp_5_2_2_ac03_degrade_marker_three_landing_chain",
        ]),
    ],
    "AC-S5-04": [  # 三条诚实红线 + simulation_notice 入 state（CP-1.3-1/3）
        ("tests.test_sprint5_t13_coding_prompt", [
            "test_cp_1_3_1_three_redlines_in_honesty_section",
            "test_cp_1_3_1_simulation_notice_obligation_in_honesty_section",
            "test_cp_1_3_3_notice_present_lands_in_state",
        ]),
    ],
    "AC-S5-05": [  # 回归样本审计命中 ≥2 类 + 降档 + 显著标注（CP-3.1-1 + CP-5.2-1）
        ("tests.test_sprint5_t31_honesty_audit", [
            "test_cp_3_1_1_regression_fixture_hits_two_categories",
        ]),
        ("tests.test_sprint5_t52_regression_targets", [
            "test_cp_5_2_1_ac05_fixture_audit_to_report_annotation_chain",
        ]),
    ],
    "AC-S5-06": [  # 干净代码零误报不降档（CP-3.1-2/3）
        ("tests.test_sprint5_t31_honesty_audit", [
            "test_cp_3_1_2_clean_fixture_zero_hits",
            "test_cp_3_1_3_exemption_evaluator_reads_answers",
            "test_cp_3_1_3_exemption_score_init_then_update",
        ]),
    ],
    "AC-S5-07": [  # 两级结论措辞红线（CP-3.3-1 / CP-3.4-1 + CP-5.2-1 靶测）
        ("tests.test_sprint5_t33_conclusion", [
            "test_cp_3_3_1_success_with_annotation_is_engineering_never_science",
            "test_cp_3_3_1_all_match_no_annotation_is_science",
        ]),
        ("tests.test_sprint5_t34_report_render", [
            "test_cp_3_4_1_engineering_wording_no_success_claim",
            "test_cp_3_4_1_science_wording",
        ]),
        ("tests.test_sprint5_t52_regression_targets", [
            "test_cp_5_2_1_ac07_snapshot_regen_wording_vs_frozen_old_report",
        ]),
    ],
    "AC-S5-08": [  # 回验节三态 + 不宣告完全成功（CP-3.3-3 / CP-3.4-2）
        ("tests.test_sprint5_t33_conclusion", [
            "test_cp_3_3_3_trend_match",
            "test_cp_3_3_3_trend_mismatch",
            "test_cp_3_3_3_trend_unverified_paths",
        ]),
        ("tests.test_sprint5_t34_report_render", [
            "test_cp_3_4_2_goal_checks_three_states_rendered",
        ]),
    ],
    "AC-S5-09": [  # expected_results 定性化 + 对比表删列（CP-1.5-1/2 / CP-3.4-4）
        ("tests.test_sprint5_t15_planning_prompt", [
            "test_cp_1_5_1_prompt_forbids_fabricated_numbers",
            "test_cp_1_5_2_schema_expected_results_is_array_form",
        ]),
        ("tests.test_sprint5_t34_report_render", [
            "test_cp_3_4_4_no_expected_column_and_groups_expanded",
        ]),
    ],
    "AC-S5-10": [  # 对账入 execution_result + 报告 N/M 展示（CP-2.4-1/2 / CP-3.4-3）
        ("tests.test_sprint5_t24_reconcile", [
            "test_cp_2_4_1_thirteen_planned_eight_executed",
            "test_cp_2_4_2_level1_step_index_priority_over_normalized_match",
        ]),
        ("tests.test_sprint5_t34_report_render", [
            "test_cp_3_4_3_reconciliation_n_of_m_unexecuted_and_truncation",
        ]),
    ],
    "AC-S5-11": [  # 未全执行禁静默 success：强制降档有痕（CP-2.4-1 / CP-3.3-2）
        ("tests.test_sprint5_t33_conclusion", [
            "test_cp_3_3_2_incomplete_from_unexecuted_steps",
            "test_cp_3_3_2_incomplete_from_budget_truncated",
            "test_cp_3_3_2_attribution_unavailable_does_not_trigger",
        ]),
        ("tests.test_sprint5_t34_report_render", [
            "test_cp_3_4_3_notice_block_incomplete_with_budget",
        ]),
    ],
    "AC-S5-12": [  # 预算联动 + 截断显式 log+state（CP-1.1-2 / CP-2.5-1/2）
        ("tests.test_sprint5_t11_config", [
            "TestCP112BudgetLedgerBoundary.test_cap_equals_half_dev_loop_budget",
            "TestCP112BudgetLedgerBoundary.test_cap_gt_floor_gt_zero",
        ]),
        ("tests.test_sprint5_t25_budget_link", [
            "test_cp_2_5_1_linkage_parametrized",
            "test_cp_2_5_2_truncated_scenario_flag_and_info_log",
        ]),
    ],
    "AC-S5-13": [  # 活动流事件生成 + 监控页尾部渲染（CP-4.1-1 / CP-4.3-1；手动部分见 GAP）
        ("tests.test_sprint5_t41_activity_stream", [
            "test_cp411_tool_event_schema_and_truncation",
            "test_cp411_llm_event_schema_and_truncation",
        ]),
        ("tests.test_sprint5_t43_activity_render", [
            "test_cp_4_3_1_tail_renders_exactly_last_30_of_45",
        ]),
    ],
    "AC-S5-14": [  # 三个"不"+ 封顶 + react_base 零改动（CP-4.1-2 / CP-4.2-4；全量回归见 GAP）
        ("tests.test_sprint5_t41_activity_stream", [
            "test_cp412_deque_cap_keeps_latest_max",
        ]),
        ("tests.test_sprint5_t42_app_callbacks", [
            "test_cp424_events_absent_from_checkpoint_and_state",
            "test_cp424_react_base_zero_coupling_with_activity_stream",
        ]),
    ],
    "AC-S5-15": [  # 顺利执行全程路由可达双通道（CP-0.2-2 + CP-5.2-1；真库手动见 GAP）
        ("tests.test_sprint5_s5_08_routing", [
            "test_cp_0_2_2_case4bis_coding_execution_route_to_monitor",
            "test_cp_0_2_2_case4ter_reporting_with_report_routes_to_report",
            "test_cp_0_2_2_monitor_case6_reporting_with_report_routes_to_report",
            "test_cp_0_2_2_state_sequence_progress_to_monitor_to_report",
        ]),
        ("tests.test_sprint5_t52_regression_targets", [
            "test_cp_5_2_1_ac15_regression_thread_state_sequence_full_route",
        ]),
    ],
    "AC-S5-16": [  # dev_loop_failure 路由到失败决策面板（CP-0.2-1）
        ("tests.test_sprint5_s5_08_routing", [
            "test_cp_0_2_1_interrupt_route_target_pure",
            "test_cp_0_2_1_dev_loop_and_user_input_route_to_execution",
        ]),
    ],
    "AC-S5-17": [  # 空 report_path 失败卡片 + 停假轮询（CP-0.2-3）
        ("tests.test_sprint5_s5_08_routing", [
            "test_cp_0_2_3_case6bis_finished_no_report_renders_failure_card_stops_polling",
            "test_cp_0_2_3_case6bis_empty_string_report_path_also_triggers",
        ]),
    ],
    "AC-S5-18": [  # UI 无裸枚举 + 未知值兜底（CP-3.5-1/2）
        ("tests.test_sprint5_t35_term_map", [
            "test_cp351_humanize_hits_every_table_entry",
            "test_cp351_unknown_value_fallback",
            "test_cp352_plan_review_no_bare_enums",
        ]),
    ],
    "AC-S5-19": [  # prompt 术语约束 + 主体冻结（CP-1.5-1/3；Prompt Cache 在线维见 GAP）
        ("tests.test_sprint5_t15_planning_prompt", [
            "test_cp_1_5_1_prompt_tail_terminology_section_present",
            "test_cp_1_5_3_prompt_body_byte_identical_across_papers",
            "test_cp_1_5_3_terminology_section_is_static_tail_constant",
        ]),
    ],
    "AC-S5-20": [  # 多组解析对齐 + 降维渲染 + key_packages（CP-2.6-1/3 / CP-3.4-4 + CP-5.2-1）
        ("tests.test_sprint5_t26_grouped_metrics", [
            "test_cp_2_6_1_fixture_three_groups_aligned",
            "test_cp_2_6_3_env_info_rebuilt_key_packages_nonempty",
        ]),
        ("tests.test_sprint5_t34_report_render", [
            "test_cp_3_4_4_no_expected_column_and_groups_expanded",
            "test_cp_3_4_4_nested_baseline_flattened_no_giant_dict",
        ]),
        ("tests.test_sprint5_t52_regression_targets", [
            "test_cp_5_2_1_ac20_fixture_parse_to_report_columns_chain",
        ]),
    ],
    "AC-S5-21": [  # 两页产物路径展示可复制（CP-3.6-1；UI 手动 happy path 见 GAP）
        ("tests.test_sprint5_t36_artifact_paths", [
            "test_cp_3_6_1_report_page_shows_both_paths_from_state",
            "test_cp_3_6_1_monitor_page_shows_both_paths_from_state",
        ]),
    ],
}

#: 强制矩阵键：AC-S5-01 ~ 21 全部（每条 AC 均有 mock 层核心证据）。
ALL_SPRINT5_ACS = {f"AC-S5-{i:02d}" for i in range(1, 22)}

# ===========================================================================
# 缺口清单（CP-5.2-3 后半：手动/授权项显式落档，交 T-S5-5-3）
# ---------------------------------------------------------------------------
# 键 = 存在"默认回归覆盖不到的部分"的 AC；值 = 缺口性质与承接安排。
# 这不是矩阵映射的替代——上述 AC 的 mock 层证据仍在矩阵内强制审计；
# 本清单守门的是"缺口必须显式、不许静默增删"。
# ===========================================================================

GAP_MANIFEST: Dict[str, str] = {
    "AC-S5-13": "监控页活动流 UI 手动 happy path（真实 streamlit run 观察渲染节奏）——T-S5-5-3 真实链路抽验项",
    "AC-S5-14": "sp3/sp4 全量非 e2e 回归由主控收口执行（T-S5-5-1 已 1702 passed / 0 failed，报告归档主控）——本矩阵仅存旁证不重跑",
    "AC-S5-15": "回归样本 thread task-9208a1a4b4f5 真库路由手动验证（checkpoints.db 只读）——T-S5-5-3 手动项",
    "AC-S5-19": "Prompt Cache 在线维复采 + 新 R_baseline×0.95 守门（CP-5.3-2）——T-S5-5-3 Maria 授权项",
    "AC-S5-21": "报告页/监控页路径展示 UI 手动 happy path（可复制交互无法 AppTest 断言）——T-S5-5-3 手动项",
}


# ---------------------------------------------------------------------------
# 解析与审计工具（G1 同源，扩展类方法支持）
# ---------------------------------------------------------------------------


def iter_mapped_node_ids() -> List[str]:
    """导出矩阵映射的 pytest node id 列表（供 targeted 全绿运行）。"""
    ids: List[str] = []
    for entries in AC_COVERAGE_MAP.values():
        for module_name, func_names in entries:
            rel = module_name.replace(".", "/") + ".py"
            for fn in func_names:
                node = f"{rel}::{fn.replace('.', '::')}"
                if node not in ids:
                    ids.append(node)
    return ids


def _resolve_mapped_functions(ac_id: str):
    """解析某 AC 的全部映射函数对象（支持 'TestCls.test_x'），缺失即断言失败。"""
    resolved = []
    for module_name, func_names in AC_COVERAGE_MAP[ac_id]:
        mod = importlib.import_module(module_name)
        for fn_name in func_names:
            holder = mod
            for part in fn_name.split("."):
                holder = getattr(holder, part, None)
                assert holder is not None, (
                    f"{ac_id} 的覆盖用例 {module_name}::{fn_name} 缺失"
                    f"（AC 覆盖矩阵与真实测试脱节，断在 {part!r}）"
                )
            assert callable(holder), f"{module_name}::{fn_name} 不可调用"
            resolved.append((mod, module_name, fn_name, holder))
    return resolved


def _count_assertion_constructs(func) -> int:
    """AST 级统计测试函数内的真实断言构造数（G1 同源）。

    计入：ast.Assert；名字含 assert 的调用（mock 的 assert_called* /
    _assert helper）；pytest.raises / pytest.fail。
    """
    src = textwrap.dedent(inspect.getsource(func))
    fn_node = ast.parse(src).body[0]
    count = 0
    for node in ast.walk(fn_node):
        if isinstance(node, ast.Assert):
            count += 1
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                name = node.func.attr
            else:
                name = ""
            if "assert" in name.lower() or name in ("raises", "fail"):
                count += 1
    return count


# ===========================================================================
# CP-5.2-3 ①②：存在性 + 可收集 + mark 审计
# ===========================================================================


@pytest.mark.parametrize("ac_id", sorted(AC_COVERAGE_MAP.keys()))
def test_cp_5_2_3_ac_has_default_run_coverage(ac_id: str) -> None:
    """每条 AC 的映射用例存在、可被 pytest 默认收集、不被 skip/e2e 屏蔽。

    - 存在性：模块可 import、函数（或类方法）callable——误删覆盖用例即红；
    - 可收集：函数名以 test_ 开头，类形态须 TestXxx.test_yyy（pytest 默认规则）；
    - mark 审计：函数级 / 类级 / 模块级 pytestmark 均无 skip / e2e——e2e 被
      pytest.ini addopts 默认排除，矩阵映射到 e2e 用例即假绿。
    """
    coverage = AC_COVERAGE_MAP[ac_id]
    assert coverage, f"{ac_id} 无任何覆盖映射"
    for mod, module_name, fn_name, obj in _resolve_mapped_functions(ac_id):
        parts = fn_name.split(".")
        assert parts[-1].startswith("test_"), (
            f"{module_name}::{fn_name} 用例段不以 test_ 开头，pytest 不会收集它"
        )
        mark_names = {m.name for m in getattr(obj, "pytestmark", [])} | {
            m.name for m in getattr(mod, "pytestmark", [])
        }
        if len(parts) == 2:  # 类方法：类名须 Test 开头（收集规则）+ 类级 mark 并审
            assert parts[0].startswith("Test"), (
                f"{module_name}::{fn_name} 宿主类不以 Test 开头，pytest 不会收集它"
            )
            cls = getattr(mod, parts[0])
            mark_names |= {m.name for m in getattr(cls, "pytestmark", [])}
        forbidden = mark_names & {"skip", "skipif", "e2e", "sandbox_real"}
        assert not forbidden, (
            f"{module_name}::{fn_name} 带 {forbidden} mark，默认回归不会运行它，"
            f"不得作为 {ac_id} 的覆盖证据"
        )


# ===========================================================================
# CP-5.2-3 ③：AST 防空泛断言（"名字对但断言空泛"红线）
# ===========================================================================


@pytest.mark.parametrize("ac_id", sorted(AC_COVERAGE_MAP.keys()))
def test_cp_5_2_3_mapped_tests_not_hollow(ac_id: str) -> None:
    """每个映射用例函数体经 AST 审计须含 ≥1 个真实断言构造。

    用例被改成空转（删光 assert / 只剩 print）时本断言即红。
    """
    for _mod, module_name, fn_name, obj in _resolve_mapped_functions(ac_id):
        n = _count_assertion_constructs(obj)
        assert n >= 1, (
            f"{module_name}::{fn_name} 无任何断言构造（空泛用例），"
            f"不得作为 {ac_id} 的覆盖证据"
        )


# ===========================================================================
# 元断言：矩阵完备性 + 缺口清单显式性
# ===========================================================================


def test_cp_5_2_3_coverage_map_spans_all_21_acs() -> None:
    """矩阵键恰为 AC-S5-01 ~ 21 全 21 条，不多不少。

    与 sp4 G1 不同（sp4 剔除 4 条纯 e2e/手动 AC）：sp5 每条 AC 都有 mock 层
    核心判定用例，手动/授权部分以 GAP_MANIFEST 单独建档，两边互不替代。
    """
    assert set(AC_COVERAGE_MAP.keys()) == ALL_SPRINT5_ACS, (
        f"覆盖矩阵键应恰为全部 21 条 AC，"
        f"缺 {ALL_SPRINT5_ACS - set(AC_COVERAGE_MAP.keys())}，"
        f"多 {set(AC_COVERAGE_MAP.keys()) - ALL_SPRINT5_ACS}"
    )


def test_cp_5_2_3_gap_manifest_explicit_and_bounded() -> None:
    """缺口清单守门：恰为已核准的 5 条手动/授权项，静默增删即红。

    - 增（新 AC 出现缺口却没进清单）→ 假覆盖风险；
    - 删（清单项消失却无人补跑）→ 缺口被静默吞掉；
    - 每条缺口必须仍有矩阵内 mock 证据（缺口 ≠ 零覆盖）。
    """
    assert set(GAP_MANIFEST.keys()) == {
        "AC-S5-13", "AC-S5-14", "AC-S5-15", "AC-S5-19", "AC-S5-21",
    }
    for ac_id, reason in GAP_MANIFEST.items():
        assert ac_id in AC_COVERAGE_MAP and AC_COVERAGE_MAP[ac_id], (
            f"{ac_id} 在缺口清单中但矩阵内无 mock 层证据——缺口不等于零覆盖"
        )
        assert "T-S5-5-3" in reason or "主控" in reason, (
            f"{ac_id} 缺口未标注承接方（T-S5-5-3 / 主控）：{reason}"
        )


def test_cp_5_2_3_mapped_node_ids_unique_and_wellformed() -> None:
    """导出的 targeted node id 列表格式合法、映射文件真实存在、无重复。"""
    ids = iter_mapped_node_ids()
    assert len(ids) == len(set(ids))
    assert len(ids) >= 40, f"矩阵映射用例数异常偏少：{len(ids)}"
    for node in ids:
        rel, _, tail = node.partition("::")
        assert (PROJECT_ROOT / rel).is_file(), f"映射文件不存在：{rel}"
        assert tail.split("::")[-1].startswith("test_")


# ===========================================================================
# 旁证：sp5 全套测试模块可收集性守门（import 期回归即刻红，G1 CP-G1-3 同范式）
# ===========================================================================

SP5_TEST_MODULES = [
    "test_sprint5_s5_08_routing",
    "test_sprint5_spk1_callbacks_spike",
    "test_sprint5_t11_config", "test_sprint5_t12_state",
    "test_sprint5_t13_coding_prompt", "test_sprint5_t14_execution_prompt",
    "test_sprint5_t15_planning_prompt",
    "test_sprint5_t21_secrets", "test_sprint5_t22_coding_gate",
    "test_sprint5_t23_degrade_button", "test_sprint5_t24_reconcile",
    "test_sprint5_t25_budget_link", "test_sprint5_t26_grouped_metrics",
    "test_sprint5_t31_honesty_audit", "test_sprint5_t32_reporting_audit",
    "test_sprint5_t33_conclusion", "test_sprint5_t34_report_render",
    "test_sprint5_t35_term_map", "test_sprint5_t36_artifact_paths",
    "test_sprint5_t41_activity_stream", "test_sprint5_t42_app_callbacks",
    "test_sprint5_t43_activity_render",
    "test_sprint5_t52_regression_targets", "test_sprint5_t52_ac_matrix",
]


def test_cp_5_2_3_sprint5_test_modules_all_importable() -> None:
    """sp5 全部 24 个测试模块可 import（无收集期错误）。"""
    for m in SP5_TEST_MODULES:
        mod = importlib.import_module(f"tests.{m}")
        assert mod is not None, f"tests.{m} 无法 import"
