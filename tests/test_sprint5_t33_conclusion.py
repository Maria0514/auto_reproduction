"""T-S5-3-3 自测：S5-04 结论判定 `_determine_conclusion` + 回验 `_verify_expected_results`。

覆盖 dev-plan §批次3 CP-3.3-1 ~ CP-3.3-4：
    - CP-3.3-1 level 三值参数化：success=False→none / success=True+标注→engineering
      禁 science / 全"符合"无标注→science（AC-S5-07 判定部分）；
    - CP-3.3-2 正交标注五路径各一 mock（simulation_notice / audit hits / 降级凭证
      快照 / 未执行步骤 / budget_truncated）；attribution_unavailable **不触发**
      （R-2 保守语义）；audit=None（未审计）不触发 simulation（AC-S5-03/11）；
    - CP-3.3-3 trend 回验三态：与 metrics_groups 相符→"符合"、相反→"不符"、组名
      失配→"未验证"；纯文本→"未验证"；旧 dict 形态→全"未验证"不崩（R-5）；
    - CP-3.3-4 判定纯函数无 LLM（确定性红线，与 CP-3.1-5 同范式）+ 返回契约三键
      不被扩展（conclusion 为报告内消费，不进返回契约）。

判定/回验均为纯确定性函数（零 LLM、零猜测），测试全部离线可跑。
"""

from __future__ import annotations

import ast
import copy
import importlib
import inspect
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from core.state import ExecutionMode

# core/nodes/__init__.py 显式 export 同名 callable 会遮蔽子模块（sp1 已知坑 6），
# 必须 importlib 取模块对象。
reporting_module = importlib.import_module("core.nodes.reporting")
reporting = reporting_module.reporting
_determine_conclusion = reporting_module._determine_conclusion
_verify_expected_results = reporting_module._verify_expected_results


# ----------------------------- fixtures / helpers -----------------------------

# 与 tests/fixtures/regression_2604_01687/code/outputs/**/summary.json 固化组名
# 一致（组名 = summary.json 相对 outputs/ 的父目录 POSIX 路径，execution
# _collect_grouped_metrics 口径）。
FIXTURE_GROUPS: Dict[str, Dict[str, Any]] = {
    "evoskills_smoke": {
        "experiment_name": "evoskills_smoke",
        "num_tasks": 3,
        "pass_rate": 0.6666666666666666,
        "mean_oracle_score": 0.9666666666666667,
    },
    "baselines/no_skill": {
        "experiment_name": "baseline_no_skill",
        "baseline_type": "no_skill",
        "num_tasks": 3,
        "pass_rate": 0.0,
        "mean_score": 0.06666666666666667,
    },
    "baselines/self_generated": {
        "experiment_name": "baseline_self_generated",
        "baseline_type": "self_generated",
        "num_tasks": 3,
        "pass_rate": 0.6666666666666666,
        "mean_score": 0.9666666666666667,
    },
}

# 两条对 FIXTURE_GROUPS 恒判"符合"的定性预期（第二条走归一化子串匹配：
# "self_generated" → "baselines/self_generated"、"no_skill" → "baselines/no_skill"）。
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

AUDIT_HIT = {
    "clean": False,
    "hits": [
        {"rule": "hardcoded_score", "file": "x.py", "line": 1, "snippet": "score = 0.96"}
    ],
}
AUDIT_CLEAN = {"clean": True, "hits": []}


def _exec_result(**overrides: Any) -> Dict[str, Any]:
    """11 键 ExecutionResult（sp5 全量形态），默认全干净、success=True。"""
    result: Dict[str, Any] = {
        "success": True,
        "metrics": {"pass_rate": 0.6666666666666666},
        "logs": "",
        "errors": [],
        "artifacts": [],
        "runtime_seconds": 1.0,
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
    exec_result: Optional[Dict[str, Any]],
    *,
    expected_results: Any = None,
    simulation_notice: Optional[str] = None,
) -> Dict[str, Any]:
    """最小可用 state（dict 形态，与 t32 同范式）。"""
    if expected_results is None:
        expected_results = copy.deepcopy(GOOD_EXPECTED)
    return {
        "execution_mode": ExecutionMode.FULL,
        "paper_meta": {"arxiv_id": "2604.01687", "title": "EvoSkills"},
        "reproduction_plan": {"expected_results": expected_results, "deliverables": []},
        "execution_result": exec_result,
        "simulation_notice": simulation_notice,
    }


# ===========================================================================
# CP-3.3-1 level 三值参数化（AC-S5-07 判定部分）
# ===========================================================================


def test_cp_3_3_1_success_false_is_none_even_if_goals_match():
    """success=False → none，哪怕 goal_checks 全"符合"且无标注。"""
    state = _state(_exec_result(success=False))
    out = _determine_conclusion(state, state["execution_result"], None)
    assert out["level"] == "none"
    assert [c["verdict"] for c in out["goal_checks"]] == ["符合", "符合"]


def test_cp_3_3_1_exec_result_none_is_none():
    """exec_result=None（未执行）→ none，不崩。"""
    state = _state(None, expected_results=[])
    out = _determine_conclusion(state, None, None)
    assert out["level"] == "none"
    assert out["annotations"] == []
    assert out["goal_checks"] == []


def test_cp_3_3_1_success_with_annotation_is_engineering_never_science():
    """success=True + 标注 → engineering 禁 science（goal_checks 全"符合"也不行）。"""
    state = _state(_exec_result(), simulation_notice="数据加载部分为模拟实现")
    out = _determine_conclusion(state, state["execution_result"], None)
    assert out["level"] == "engineering"
    assert out["annotations"] == ["simulation"]
    assert all(c["verdict"] == "符合" for c in out["goal_checks"])  # 被标注单独否决


def test_cp_3_3_1_all_match_no_annotation_is_science():
    """success=True ∧ goal_checks 全"符合"且非空 ∧ 无标注 → science。"""
    state = _state(_exec_result())
    out = _determine_conclusion(state, state["execution_result"], AUDIT_CLEAN)
    assert out["level"] == "science"
    assert out["annotations"] == []
    assert len(out["goal_checks"]) == 2
    assert all(c["verdict"] == "符合" for c in out["goal_checks"])


@pytest.mark.parametrize(
    "expected_results",
    [
        [],  # goal_checks 空 → science 条件"非空"不满足
        [{"description": "loss 应收敛", "trend": None}],  # 纯文本 → 未验证
        [  # 趋势相反 → 不符
            {
                "description": "no_skill 应高于 EvoSkills（反向）",
                "trend": {
                    "metric": "pass_rate",
                    "greater": "baselines/no_skill",
                    "lesser": "evoskills_smoke",
                },
            }
        ],
    ],
    ids=["empty_goals", "unverified_goal", "mismatch_goal"],
)
def test_cp_3_3_1_success_without_full_match_is_engineering(expected_results):
    """success=True 但 goal_checks 空 / 含未验证 / 含不符 → engineering（禁 science）。"""
    state = _state(_exec_result(), expected_results=expected_results)
    out = _determine_conclusion(state, state["execution_result"], None)
    assert out["level"] == "engineering"
    assert out["annotations"] == []


# ===========================================================================
# CP-3.3-2 正交标注五路径 + attribution_unavailable / audit=None 不触发
# ===========================================================================


def test_cp_3_3_2_simulation_from_notice():
    """路径①：simulation_notice 非空 → simulation 标注（audit=None 未审计）。"""
    state = _state(_exec_result(), simulation_notice="训练数据以随机模拟生成")
    out = _determine_conclusion(state, state["execution_result"], None)
    assert out["annotations"] == ["simulation"]
    assert out["level"] == "engineering"


def test_cp_3_3_2_simulation_from_audit_hits():
    """路径②：审计 hits 非空 → simulation 标注（notice 为 None 也触发）。"""
    state = _state(_exec_result())
    out = _determine_conclusion(state, state["execution_result"], AUDIT_HIT)
    assert out["annotations"] == ["simulation"]
    assert out["level"] == "engineering"


def test_cp_3_3_2_credential_degraded_from_snapshot():
    """路径③：exec_result.degraded_credentials 快照非空 → credential_degraded。"""
    state = _state(_exec_result(degraded_credentials=["wandb_api_key"]))
    out = _determine_conclusion(state, state["execution_result"], None)
    assert out["annotations"] == ["credential_degraded"]
    assert out["level"] == "engineering"


def test_cp_3_3_2_incomplete_from_unexecuted_steps():
    """路径④：step_reconciliation 存在未执行步骤 → incomplete_execution。"""
    recon = {
        "planned": 3,
        "executed": 2,
        "completed": 2,
        "unexecuted_steps": [{"index": 2, "step_name": "运行评测"}],
        "extra_commands": [],
        "attribution_unavailable": False,
    }
    state = _state(_exec_result(step_reconciliation=recon))
    out = _determine_conclusion(state, state["execution_result"], None)
    assert out["annotations"] == ["incomplete_execution"]
    assert out["level"] == "engineering"


def test_cp_3_3_2_incomplete_from_budget_truncated():
    """路径⑤：budget_truncated=True → incomplete_execution（预算截断有痕）。"""
    state = _state(_exec_result(budget_truncated=True))
    out = _determine_conclusion(state, state["execution_result"], None)
    assert out["annotations"] == ["incomplete_execution"]
    assert out["level"] == "engineering"


def test_cp_3_3_2_attribution_unavailable_does_not_trigger():
    """attribution_unavailable=True（unexecuted_steps 已置空）**不触发**
    incomplete_execution——R-2 保守语义："无法归属 ≠ 未执行"。"""
    recon = {
        "planned": 2,
        "executed": 0,
        "completed": 0,
        "unexecuted_steps": [],
        "extra_commands": ["python run_all.py"],
        "attribution_unavailable": True,
    }
    state = _state(_exec_result(step_reconciliation=recon))
    out = _determine_conclusion(state, state["execution_result"], AUDIT_CLEAN)
    assert out["annotations"] == []
    assert out["level"] == "science"  # 无标注 + 全符合 → 不因归属失效误降档


def test_cp_3_3_2_audit_none_means_unaudited_not_hit():
    """audit=None = 未审计（区别于 {"clean": True, "hits": []}）：不触发 simulation。"""
    state = _state(_exec_result())
    out_none = _determine_conclusion(state, state["execution_result"], None)
    out_clean = _determine_conclusion(state, state["execution_result"], AUDIT_CLEAN)
    assert out_none["annotations"] == []
    assert out_clean["annotations"] == []
    assert out_none["level"] == "science" == out_clean["level"]


def test_cp_3_3_2_annotations_orthogonal_and_deduped():
    """三标注正交可叠加；simulation 双来源（notice + hits）只记一次。"""
    state = _state(
        _exec_result(budget_truncated=True, degraded_credentials=["hf_token"]),
        simulation_notice="评测部分为模拟",
    )
    out = _determine_conclusion(state, state["execution_result"], AUDIT_HIT)
    assert out["annotations"] == ["simulation", "credential_degraded", "incomplete_execution"]
    assert out["level"] == "engineering"


def test_cp_3_3_2_old_checkpoint_7key_exec_result_no_crash():
    """旧 checkpoint 7 键快照（无 sp5 新键）→ .get() 防御读不崩、无误标注。"""
    old = {
        "success": True,
        "metrics": {"acc": 0.9},
        "logs": "",
        "errors": [],
        "artifacts": [],
        "runtime_seconds": 1.0,
        "environment_info": {},
    }
    state = _state(old)
    out = _determine_conclusion(state, old, None)
    assert out["annotations"] == []
    # metrics_groups 缺失 → trend 全"未验证" → 只能 engineering
    assert out["level"] == "engineering"
    assert all(c["verdict"] == "未验证" for c in out["goal_checks"])


# ===========================================================================
# CP-3.3-3 trend 回验三态 + 纯文本 + 旧 dict 形态（R-5）
# ===========================================================================


def test_cp_3_3_3_trend_match():
    """greater/lesser 与 metrics_groups 相符 → "符合"。"""
    checks = _verify_expected_results(copy.deepcopy(GOOD_EXPECTED), _exec_result())
    assert [c["verdict"] for c in checks] == ["符合", "符合"]
    assert checks[0]["description"] == "EvoSkills 组 pass_rate 应高于 no_skill 基线"


def test_cp_3_3_3_trend_mismatch():
    """趋势与实测相反 → "不符"（两组均取到值、比较失败）。"""
    expected = [
        {
            "description": "no_skill 基线应高于 EvoSkills（反向断言）",
            "trend": {
                "metric": "pass_rate",
                "greater": "baselines/no_skill",
                "lesser": "evoskills_smoke",
            },
        }
    ]
    checks = _verify_expected_results(expected, _exec_result())
    assert [c["verdict"] for c in checks] == ["不符"]


def test_cp_3_3_3_trend_equal_values_is_mismatch():
    """相等（非严格大于）→ "不符"：比较成功但趋势不成立，不含糊判符合。"""
    expected = [
        {
            "description": "num_tasks 应更大（实际相等）",
            "trend": {
                "metric": "num_tasks",
                "greater": "evoskills_smoke",
                "lesser": "baselines/no_skill",
            },
        }
    ]
    checks = _verify_expected_results(expected, _exec_result())
    assert [c["verdict"] for c in checks] == ["不符"]


@pytest.mark.parametrize(
    "trend",
    [
        # 组名失配（不存在的组）
        {"metric": "pass_rate", "greater": "nonexistent_group", "lesser": "no_skill"},
        # 组名歧义（"baselines" 同时子串命中两组）→ 保守失配
        {"metric": "mean_score", "greater": "baselines", "lesser": "evoskills_smoke"},
        # 指标在命中组内缺失
        {"metric": "f1", "greater": "evoskills_smoke", "lesser": "no_skill"},
        # 指标值非数值（字符串字段）
        {"metric": "experiment_name", "greater": "evoskills_smoke", "lesser": "no_skill"},
        # trend 结构缺键
        {"metric": "pass_rate", "greater": "evoskills_smoke"},
    ],
    ids=["group_missing", "group_ambiguous", "metric_missing", "metric_non_numeric", "trend_incomplete"],
)
def test_cp_3_3_3_trend_unverified_paths(trend):
    """组名失配 / 歧义 / 指标缺失 / 非数值 / 结构缺键 → 一律保守"未验证"。"""
    checks = _verify_expected_results([{"description": "d", "trend": trend}], _exec_result())
    assert [c["verdict"] for c in checks] == ["未验证"]


def test_cp_3_3_3_text_only_entries_unverified():
    """纯文本条目（trend=None / 缺失 / 畸形）一律"未验证"——绝不猜测判定。"""
    expected = [
        {"description": "loss 应收敛", "trend": None},
        {"description": "生成样例应可读"},
        {"description": "trend 畸形", "trend": "pass_rate 应更高"},
        "裸字符串条目",
    ]
    checks = _verify_expected_results(expected, _exec_result())
    assert [c["verdict"] for c in checks] == ["未验证"] * 4
    assert checks[0]["description"] == "loss 应收敛"
    assert checks[3]["description"] == "裸字符串条目"


def test_cp_3_3_3_legacy_dict_form_all_unverified_no_crash():
    """旧 dict 形态（R-5，旧 checkpoint）→ 逐键全"未验证"，不比较不崩。"""
    legacy = {"accuracy": 0.95, "pass_rate": "0.67"}
    checks = _verify_expected_results(legacy, _exec_result())
    assert len(checks) == 2
    assert all(c["verdict"] == "未验证" for c in checks)
    assert "accuracy" in checks[0]["description"]


def test_cp_3_3_3_degenerate_inputs():
    """None / 空 dict / 空 list / metrics_groups 缺失均容忍。"""
    assert _verify_expected_results(None, _exec_result()) == []
    assert _verify_expected_results({}, _exec_result()) == []
    assert _verify_expected_results([], _exec_result()) == []
    # exec_result=None → trend 条目保守"未验证"
    checks = _verify_expected_results(copy.deepcopy(GOOD_EXPECTED), None)
    assert [c["verdict"] for c in checks] == ["未验证", "未验证"]
    # metrics_groups 为空 dict → 同样"未验证"
    checks = _verify_expected_results(
        copy.deepcopy(GOOD_EXPECTED), _exec_result(metrics_groups={})
    )
    assert [c["verdict"] for c in checks] == ["未验证", "未验证"]


def test_cp_3_3_3_check_item_shape():
    """goal_checks 条目形状 = {description, verdict}（架构 §7.4 契约，供 T-S5-3-4 渲染）。"""
    checks = _verify_expected_results(copy.deepcopy(GOOD_EXPECTED), _exec_result())
    for c in checks:
        assert set(c.keys()) == {"description", "verdict"}
        assert c["verdict"] in {"符合", "不符", "未验证"}


# ===========================================================================
# CP-3.3-4 纯函数确定性红线（与 CP-3.1-5 同范式）+ 返回契约三键不扩
# ===========================================================================


def test_cp_3_3_4_purity_no_llm_in_reporting_module():
    """源码结构断言：reporting 模块 import 全集限于 stdlib + config +
    core.honesty_audit + core.state；无 LLM 客户端、无 langchain/langgraph。"""
    src = inspect.getsource(reporting_module)
    tree = ast.parse(src)
    imported: set = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")

    allowed = {
        "__future__",
        "logging",
        "re",
        "datetime",
        "pathlib",
        "typing",
        "config",
        "core.honesty_audit",
        "core.state",
    }
    assert imported <= allowed, f"越界 import: {imported - allowed}"

    forbidden_prefixes = ("core.llm_client", "langchain", "langgraph", "openai")
    assert not any(m.startswith(forbidden_prefixes) for m in imported)
    assert "create_llm" not in src


def test_cp_3_3_4_deterministic_same_input_same_output():
    """确定性：同一输入两次判定输出逐字节一致，且不改动入参（纯函数）。"""
    state = _state(
        _exec_result(budget_truncated=True, degraded_credentials=["hf_token"]),
        simulation_notice="部分模拟",
    )
    state_snapshot = copy.deepcopy(state)
    audit = copy.deepcopy(AUDIT_HIT)

    first = _determine_conclusion(state, state["execution_result"], audit)
    second = _determine_conclusion(state, state["execution_result"], audit)

    assert first == second
    assert json.dumps(first, ensure_ascii=False, sort_keys=True) == json.dumps(
        second, ensure_ascii=False, sort_keys=True
    )
    assert state == state_snapshot  # 入参零改动（含 exec_result 嵌套）
    assert audit == AUDIT_HIT


def test_cp_3_3_4_signature_three_params():
    """签名契约（T-S5-3-2 交接）：_determine_conclusion(state, exec_result, audit)。"""
    params = list(inspect.signature(_determine_conclusion).parameters.keys())
    assert params == ["state", "exec_result", "audit"]
    params_v = list(inspect.signature(_verify_expected_results).parameters.keys())
    assert params_v == ["expected_results", "exec_result"]


def test_cp_3_3_4_return_contract_still_three_keys(tmp_path):
    """conclusion 为报告内消费，**不进返回契约**：reporting() 返回键集合精确
    = {report_path, current_step, honesty_audit}（CP-C2-5 显式扩展语义零改动）。"""
    ws = tmp_path / "workspace"
    code_dir = ws / "2604.01687" / "code"
    code_dir.mkdir(parents=True, exist_ok=True)
    state = _state(
        _exec_result(budget_truncated=True),
        simulation_notice="评测部分为模拟",
    )
    state.update({
        "workspace_dir": str(ws),
        "code_output_dir": str(code_dir.resolve()),
        "node_errors": [],
        "degraded_nodes": [],
        "fix_loop_history": [],
        "fix_loop_count": 0,
        "user_fix_decision": None,
    })

    out = reporting(state)

    assert set(out.keys()) == {"report_path", "current_step", "honesty_audit"}, out.keys()
    assert out["current_step"] == reporting_module.NODE_NAME
    assert Path(out["report_path"]).read_text(encoding="utf-8").strip()
