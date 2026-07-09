"""T-S5-3-4 自测：报告渲染改造——措辞/回验节/对账节/声明块/删列/降维（S5-04/05/06/10）。

覆盖 dev-plan §批次3 CP-3.4-1 ~ CP-3.4-5：
    - CP-3.4-1 两级措辞（AC-S5-07 红线）：engineering 场景全文无"复现成功"
      （含"复现成功（科学复现）"整串）、含目标措辞；science 场景正常宣告；
    - CP-3.4-2 "计划目标回验"节三态渲染；存在不符/未验证 → 整体结论不宣告完全成功；
    - CP-3.4-3 "步骤对账"节（N/M + 未执行清单 + 截断声明 + attribution_unavailable
      如实原始命令，AC-S5-10 红线）+ 声明块三来源渲染（AC-S5-03③/11/12 报告面）；
    - CP-3.4-4 对比表无"计划 expected"列、多组按组展开且"本次复现值"非空；
      嵌套指标降维无巨型 dict 字符串；environment 节 key_packages 逐包渲染（AC-S5-09/20）；
    - CP-3.4-5 旧形态兼容：旧 dict expected_results + 旧 7 键 exec_result（旧
      checkpoint 快照）→ 报告生成不崩、回验全"未验证"（R-5/R-6）。

全部 mock state 单测，零 LLM、零配额；组名/指标与 tests/fixtures/regression_2604_01687
固化口径一致（与 t33 同源）。
"""

from __future__ import annotations

import copy
import importlib
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from core.state import ExecutionMode

# core/nodes/__init__.py 显式 export 同名 callable 会遮蔽子模块（sp1 已知坑 6），
# 必须 importlib 取模块对象。
reporting_module = importlib.import_module("core.nodes.reporting")
reporting = reporting_module.reporting
_determine_report_form = reporting_module._determine_report_form


# ----------------------------- fixtures / helpers -----------------------------

# 与 tests/fixtures/regression_2604_01687/code/outputs/**/summary.json 固化组名一致。
FIXTURE_GROUPS: Dict[str, Dict[str, Any]] = {
    "evoskills_smoke": {
        "experiment_name": "evoskills_smoke",
        "num_tasks": 3,
        "pass_rate": 0.6666666666666666,
        "mean_oracle_score": 0.9666666666666667,
    },
    "baselines/no_skill": {
        "experiment_name": "baseline_no_skill",
        "num_tasks": 3,
        "pass_rate": 0.0,
        "mean_score": 0.06666666666666667,
    },
    "baselines/self_generated": {
        "experiment_name": "baseline_self_generated",
        "num_tasks": 3,
        "pass_rate": 0.6666666666666666,
        "mean_score": 0.9666666666666667,
    },
}

# 对 FIXTURE_GROUPS 恒判"符合"的定性预期（与 t33 GOOD_EXPECTED 同源）。
GOOD_EXPECTED: List[Dict[str, Any]] = [
    {
        "description": "EvoSkills 组 pass_rate 应高于 no_skill 基线",
        "trend": {
            "metric": "pass_rate",
            "greater": "evoskills_smoke",
            "lesser": "baselines/no_skill",
        },
    },
    {
        "description": "self_generated 基线 mean_score 应高于 no_skill 基线",
        "trend": {"metric": "mean_score", "greater": "self_generated", "lesser": "no_skill"},
    },
]

# 恒判"不符"的反向趋势条目。
MISMATCH_ENTRY: Dict[str, Any] = {
    "description": "no_skill 基线应高于 EvoSkills（反向断言）",
    "trend": {
        "metric": "pass_rate",
        "greater": "baselines/no_skill",
        "lesser": "evoskills_smoke",
    },
}

AUDIT_HIT = {
    "clean": False,
    "hits": [
        {
            "rule": "hardcoded_score",
            "file": "src/task_executor.py",
            "line": 42,
            "snippet": "score = 0.96",
        }
    ],
}


@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    """把 reporting 模块内 WORKSPACE_DIR 指向临时目录（与 c2/t32 同范式）。"""
    ws = tmp_path / "workspace"
    ws.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(reporting_module, "WORKSPACE_DIR", ws)
    return ws


def _exec_result(**overrides: Any) -> Dict[str, Any]:
    """11 键 ExecutionResult（sp5 全量形态），默认全干净、success=True。"""
    result: Dict[str, Any] = {
        "success": True,
        "metrics": {"pass_rate": 0.6666666666666666},
        "logs": "",
        "errors": [],
        "artifacts": [],
        "runtime_seconds": 11.5,
        "environment_info": {},
        "step_reconciliation": {
            "planned": 2,
            "executed": 2,
            "completed": 2,
            "unexecuted_steps": [],
            "extra_commands": [],
            "attribution_unavailable": False,
        },
        "budget_truncated": False,
        "metrics_groups": copy.deepcopy(FIXTURE_GROUPS),
        "degraded_credentials": [],
    }
    result.update(overrides)
    return result


def _state(
    workspace: Path,
    exec_result: Optional[Dict[str, Any]],
    *,
    expected_results: Any = "default",
    simulation_notice: Optional[str] = None,
    **overrides: Any,
) -> Dict[str, Any]:
    """可落盘完整 state（code_output_dir 落 workspace/<arxiv_id>/code，与 C1 对齐）。"""
    arxiv_id = "2604.01687"
    code_dir = workspace / arxiv_id / "code"
    code_dir.mkdir(parents=True, exist_ok=True)
    if expected_results == "default":
        expected_results = copy.deepcopy(GOOD_EXPECTED)
    state: Dict[str, Any] = {
        "workspace_dir": str(workspace),
        "code_output_dir": str(code_dir.resolve()),
        "execution_mode": ExecutionMode.FULL,
        "paper_meta": {"arxiv_id": arxiv_id, "title": "EvoSkills"},
        "paper_analysis": {"baseline_results": {}},
        "reproduction_plan": {"expected_results": expected_results, "deliverables": []},
        "execution_result": exec_result,
        "simulation_notice": simulation_notice,
        "node_errors": [],
        "degraded_nodes": [],
        "fix_loop_history": [],
        "fix_loop_count": 0,
        "user_fix_decision": None,
    }
    state.update(overrides)
    return state


def _render(state: Dict[str, Any]) -> str:
    """跑真实 reporting()（含真实审计，空 code_dir → clean）并读回 Markdown。"""
    out = reporting(state)
    assert set(out.keys()) == {"report_path", "current_step", "honesty_audit"}, out.keys()
    return Path(out["report_path"]).read_text(encoding="utf-8")


# ===========================================================================
# CP-3.4-1 两级措辞（AC-S5-07 红线）
# ===========================================================================


def test_cp_3_4_1_science_wording(workspace):
    """science（全"符合"+ 无标注）→ 正常宣告"复现成功（科学复现）"。"""
    state = _state(workspace, _exec_result())
    md = _render(state)
    assert _determine_report_form(state) == "full_success"
    assert "复现成功（科学复现）" in md
    # 无标注 → 无顶部声明块
    assert "重要声明" not in md
    # 回验小结全符合
    assert "全部条目符合计划预期" in md


def test_cp_3_4_1_engineering_wording_no_success_claim(workspace):
    """engineering（标注触发降档）→ 目标措辞 + 全文禁"复现成功"字样
    （"复现成功（科学复现）"整串也不许出现）。"""
    state = _state(workspace, _exec_result(), simulation_notice="评测部分为模拟实现")
    md = _render(state)
    assert "代码跑通（工程复现），论文实验结论未验证" in md
    assert "复现成功" not in md  # 红线：整串"复现成功（科学复现）"同被排除


def test_cp_3_4_1_engineering_via_unverified_goal(workspace):
    """engineering（goal_checks 含"未验证"）→ 同样禁"复现成功"、含目标措辞。"""
    state = _state(
        workspace,
        _exec_result(),
        expected_results=[{"description": "loss 应收敛", "trend": None}],
    )
    md = _render(state)
    assert "代码跑通（工程复现），论文实验结论未验证" in md
    assert "复现成功" not in md


# ===========================================================================
# CP-3.4-2 回验节三态渲染 + 不宣告完全成功（AC-S5-08）
# ===========================================================================


def test_cp_3_4_2_goal_checks_three_states_rendered(workspace):
    """三态各一条 → 回验表逐条渲染"符合/不符/未验证"，且整体不宣告完全成功。"""
    expected = [
        copy.deepcopy(GOOD_EXPECTED[0]),          # 符合
        copy.deepcopy(MISMATCH_ENTRY),            # 不符
        {"description": "生成样例应可读", "trend": None},  # 未验证
    ]
    state = _state(workspace, _exec_result(), expected_results=expected)
    md = _render(state)
    assert "计划目标回验" in md
    assert "EvoSkills 组 pass_rate 应高于 no_skill 基线" in md
    assert "no_skill 基线应高于 EvoSkills（反向断言）" in md
    assert "生成样例应可读" in md
    assert "✅ 符合" in md and "❌ 不符" in md and "⚠️ 未验证" in md
    # 存在不符/未验证 → 明示不作完全成功宣告 + 结论卡片按 engineering 措辞
    assert "整体结论不作科学复现（完全成功）级别的宣告" in md
    assert "代码跑通（工程复现），论文实验结论未验证" in md
    assert "复现成功" not in md


def test_cp_3_4_2_all_match_science_summary(workspace):
    """全"符合"→ 回验小结正面陈述 + science 正常宣告（与降档路径互为正控制）。"""
    state = _state(workspace, _exec_result())
    md = _render(state)
    assert "计划目标回验" in md
    assert "✅ 符合" in md
    assert "全部条目符合计划预期" in md
    assert "复现成功（科学复现）" in md
    assert "不符" not in md.split("计划目标回验")[1].split("##")[0].replace(
        "三态：符合 / 不符 / 未验证", ""
    )  # 表体无"不符"判定（口径说明行除外）


def test_cp_3_4_2_empty_goal_checks_note(workspace):
    """goal_checks 为空（计划无预期）→ 回验节仍在场并给出提示，不宣告完全成功。"""
    state = _state(workspace, _exec_result(), expected_results=[])
    md = _render(state)
    assert "计划目标回验" in md
    assert "未提供可回验的预期结果" in md
    assert "代码跑通（工程复现），论文实验结论未验证" in md
    assert "复现成功" not in md


# ===========================================================================
# CP-3.4-3 对账节 + 声明块三来源（AC-S5-10/11/12 + AC-S5-03 第③落点渲染面）
# ===========================================================================


def test_cp_3_4_3_reconciliation_n_of_m_unexecuted_and_truncation(workspace):
    """对账节：已完成 8/13 步 + 未执行清单 + 截断声明 + 顶部"执行不完整"声明块。"""
    unexecuted = [
        {"index": i, "step_name": f"步骤_{i}_评测"} for i in range(8, 13)
    ]
    recon = {
        "planned": 13,
        "executed": 8,
        "completed": 8,
        "unexecuted_steps": unexecuted,
        "extra_commands": ["python extra_probe.py"],
        "attribution_unavailable": False,
    }
    state = _state(
        workspace,
        _exec_result(step_reconciliation=recon, budget_truncated=True),
    )
    md = _render(state)
    # 对账节主体（AC-S5-10）
    assert "步骤对账" in md
    assert "已完成 8/13 步" in md
    for i in range(8, 13):
        assert f"第 {i + 1} 步：步骤_{i}_评测" in md
    assert "python extra_probe.py" in md  # 计划外命令如实展示
    # 截断显式声明（AC-S5-12）
    assert "执行被截断" in md and "预算" in md
    # 顶部声明块：incomplete_execution 标注（AC-S5-11）
    assert "重要声明" in md
    assert "执行不完整" in md
    # 未全部执行 → 禁静默 success 措辞（强制降档有痕）
    assert "复现成功" not in md
    assert "代码跑通（工程复现），论文实验结论未验证" in md


def test_cp_3_4_3_attribution_unavailable_honest_raw_commands(workspace):
    """AC-S5-10 红线：attribution_unavailable=True → unexecuted 恒空场景下**不做**
    "已完成 N/M / 未执行步骤"统计、不出现"0 步未执行"式误导，如实列原始命令。"""
    recon = {
        "planned": 2,
        "executed": 0,
        "completed": 0,
        "unexecuted_steps": [],
        "extra_commands": ["python run_all.py --full", "python eval.py"],
        "attribution_unavailable": True,
    }
    state = _state(
        workspace,
        _exec_result(step_reconciliation=recon),
        expected_results=[],
    )
    md = _render(state)
    assert "步骤对账" in md
    assert "命令归属不可用" in md
    # 原始命令清单如实展示
    assert "python run_all.py --full" in md
    assert "python eval.py" in md
    # 红线：不许出现误导性"无未执行步骤/已完成 0 步"统计
    assert "未执行的计划步骤：无" not in md
    assert "0 步未执行" not in md
    assert "已完成 0/2 步" not in md
    # R-2 保守语义：attribution_unavailable 不触发 incomplete_execution 标注
    assert "执行不完整" not in md


def test_cp_3_4_3_notice_block_simulation_sources(workspace, monkeypatch):
    """声明块来源①：simulation ← notice 原文 + 审计 hits 证据表（含规则中文说明）。"""
    monkeypatch.setattr(reporting_module, "audit_code_dir", lambda _d: copy.deepcopy(AUDIT_HIT))
    notice = "数据加载部分为模拟实现\n因无 API 凭证，评测调用被替换为固定样例"
    state = _state(workspace, _exec_result(), simulation_notice=notice)
    md = _render(state)
    assert "重要声明" in md
    assert "模拟/未验证" in md
    # notice 原文逐行在场（blockquote 保原文）
    assert "数据加载部分为模拟实现" in md
    assert "因无 API 凭证，评测调用被替换为固定样例" in md
    # 审计证据（rule 中文说明 + file/line/snippet 三元组）
    assert "硬编码分数" in md and "hardcoded_score" in md
    assert "src/task_executor.py" in md
    assert "42" in md
    assert "score = 0.96" in md
    # 声明块在结论节之前（顶部显著）
    assert md.index("重要声明") < md.index("复现结论")


def test_cp_3_4_3_notice_block_credential_degraded_purpose_lookup(workspace):
    """声明块来源②：credential_degraded ← purpose 中文说明查 plan；查不到展示原 key。"""
    state = _state(
        workspace,
        _exec_result(degraded_credentials=["llm_api_key", "orphan_key"]),
    )
    state["reproduction_plan"]["required_credentials"] = [
        {"purpose_key": "llm_api_key", "purpose": "论文方法依赖真实 LLM 调用"},
    ]
    md = _render(state)
    assert "重要声明" in md
    assert "凭证降级" in md
    assert "论文方法依赖真实 LLM 调用" in md  # purpose 中文说明命中
    assert "llm_api_key" in md
    assert "orphan_key" in md  # 查不到 purpose → 降级展示 purpose_key 原值
    assert "复现成功" not in md  # 标注强制降档（AC-S5-11）


def test_cp_3_4_3_notice_block_incomplete_with_budget(workspace):
    """声明块来源③：incomplete_execution ← 缺步 + budget_truncated 双源声明。"""
    recon = {
        "planned": 3,
        "executed": 2,
        "completed": 2,
        "unexecuted_steps": [{"index": 2, "step_name": "运行评测"}],
        "extra_commands": [],
        "attribution_unavailable": False,
    }
    state = _state(
        workspace,
        _exec_result(step_reconciliation=recon, budget_truncated=True),
    )
    md = _render(state)
    assert "重要声明" in md and "执行不完整" in md
    assert "已完成 2/3 步" in md
    assert "预算耗尽" in md
    assert "运行评测" in md


# ===========================================================================
# CP-3.4-4 对比表删列 + 多组展开 + 嵌套降维（AC-S5-09/20 渲染部分）
# ===========================================================================


def test_cp_3_4_4_no_expected_column_and_groups_expanded(workspace):
    """对比表无"计划 expected"列；metrics_groups 三组逐组展开且"本次复现值"非空。"""
    state = _state(workspace, _exec_result())
    md = _render(state)
    assert "指标对比" in md
    assert "论文 baseline" in md and "本次复现值" in md
    assert "计划 expected" not in md  # AC-S5-09：删列
    # 三组逐组展开（组名 = 产物目录相对路径）
    for group in ("evoskills_smoke", "baselines/no_skill", "baselines/self_generated"):
        assert f"组 `{group}`" in md
    # 组内"本次复现值"非空（4g 精度格式化）
    assert "0.6667" in md      # pass_rate
    assert "0.9667" in md      # mean_oracle_score / mean_score
    assert "0.06667" in md     # no_skill mean_score


def test_cp_3_4_4_nested_baseline_flattened_no_giant_dict(workspace):
    """嵌套 baseline（回归样本形态）逐键行降维，全文无巨型 dict 字符串。"""
    state = _state(workspace, _exec_result())
    state["paper_analysis"] = {
        "baseline_results": {
            "main_comparison": {"EvoSkills": 71.1, "No-Skill_Baseline": 30.6},
            "cross_model_transfer": {
                "Claude_Opus": {"with_skills": 71.1, "no_skill": 30.6},
            },
            "notes": ["trend only", "absolute values may differ"],
        }
    }
    md = _render(state)
    # 逐键行标签（点级联降维）
    assert "main_comparison.EvoSkills" in md
    assert "main_comparison.No-Skill_Baseline" in md
    assert "cross_model_transfer.Claude_Opus.with_skills" in md
    # 标量列表内联（不整塞 repr）
    assert "trend only, absolute values may differ" in md
    # 无巨型 dict 字符串（旧渲染反例：fixtures/regression_2604_01687/report.md L24-26）
    assert "{'" not in md and '{"' not in md


def test_cp_3_4_4_nested_repro_metrics_flattened(workspace):
    """复现侧嵌套 metrics 同样降维（防御：<METRICS> 恰好带嵌套结构）。"""
    state = _state(
        workspace,
        _exec_result(metrics={"ablation": {"full": 0.71, "wo_evolution": 0.49}}),
    )
    md = _render(state)
    assert "ablation.full" in md
    assert "ablation.wo_evolution" in md
    assert "{'" not in md and '{"' not in md


def test_cp_3_4_4_env_key_packages_rendered(workspace):
    """执行概况：key_packages 逐包渲染（数据修复 T-S5-2-6，渲染归本任务）。"""
    state = _state(
        workspace,
        _exec_result(
            environment_info={
                "python_version": "Python 3.11.5",
                "key_packages": "numpy==1.26.0, torch==2.3.0",
            }
        ),
    )
    md = _render(state)
    assert "执行概况" in md
    assert "关键依赖包（key_packages）" in md
    assert "`numpy==1.26.0`" in md
    assert "`torch==2.3.0`" in md
    assert "Python 3.11.5" in md


# ===========================================================================
# CP-3.4-5 旧形态兼容（旧 checkpoint 快照，R-5/R-6）
# ===========================================================================


def test_cp_3_4_5_legacy_dict_expected_and_7key_exec_result(workspace):
    """旧 dict expected_results + 旧 7 键 exec_result → 不崩、回验全"未验证"、
    对比表无 expected 列、对账节整节省略（无数据不硬凑）。"""
    legacy_exec = {
        "success": True,
        "metrics": {"accuracy": 0.893},
        "logs": "",
        "errors": [],
        "artifacts": [],
        "runtime_seconds": 1.0,
        "environment_info": {},
    }
    state = _state(
        workspace,
        legacy_exec,
        expected_results={"accuracy": 0.90, "f1": 0.88},
    )
    state["paper_analysis"] = {"baseline_results": {"accuracy": 0.91}}
    md = _render(state)
    # 不崩且有效
    assert md.strip()
    # 回验节：旧 dict 逐键"未验证"（锚定节标题切分，避免命中结论卡片中的提及）
    assert "## 计划目标回验" in md
    section = md.split("## 计划目标回验")[1].split("\n## ")[0]
    assert section.count("⚠️ 未验证") == 2
    assert "accuracy = 0.9" in md and "f1 = 0.88" in md
    # 对比表：无 expected 列，baseline vs 复现值仍在
    assert "计划 expected" not in md
    assert "0.91" in md and "0.893" in md
    # 对账节：旧快照无对账数据 → 整节省略（防御，R-6）
    assert "步骤对账" not in md
    # 措辞：metrics_groups 缺失 → 回验非全符合 → engineering
    assert "代码跑通（工程复现），论文实验结论未验证" in md
    assert "复现成功" not in md


def test_cp_3_4_5_degraded_renders_goal_and_reconciliation(workspace):
    """degraded 路径同样渲染回验/对账节，既有降级章节保持在场。"""
    recon = {
        "planned": 3,
        "executed": 1,
        "completed": 1,
        "unexecuted_steps": [
            {"index": 1, "step_name": "训练模型"},
            {"index": 2, "step_name": "运行评测"},
        ],
        "extra_commands": [],
        "attribution_unavailable": False,
    }
    state = _state(
        workspace,
        _exec_result(success=False, step_reconciliation=recon, metrics={}),
    )
    md = _render(state)
    assert _determine_report_form(state) == "degraded"
    assert "未成功复现" in md
    assert "计划目标回验" in md
    assert "步骤对账" in md and "已完成 1/3 步" in md
    assert "训练模型" in md and "运行评测" in md
    # 既有降级章节零退化
    assert "降级原因" in md and "节点错误摘要" in md and "修复历程" in md
    assert "复现成功" not in md


def test_cp_3_4_5_code_only_notice_block_no_metric_sections(workspace):
    """code_only + simulation_notice → 顶部声明块在场；指标/回验/对账章节不进
    code_only（无执行事实可对账），既有"仅生成代码"口径零退化。"""
    state = _state(
        workspace,
        None,
        execution_mode=ExecutionMode.CODE_ONLY,
        simulation_notice="全部评测为模拟实现",
    )
    md = _render(state)
    assert _determine_report_form(state) == "code_only"
    assert "仅生成代码" in md
    assert "重要声明" in md and "模拟/未验证" in md
    assert "全部评测为模拟实现" in md
    assert "指标对比" not in md and "本次复现值" not in md
    assert "步骤对账" not in md
    assert "复现成功" not in md
