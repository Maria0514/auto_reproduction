"""Sprint 7 需求 S7-11（批次 7）——**独立验收补缺**（测试工程师代理，2026-08-01）。

本文件不是开发侧交付物，而是独立测试设计对 `tests/test_sprint7_s711_completion.py`
（49 条）逐条复核后**补的缺口**。补缺分五类，每类的动机都写在对应区段抬头：

    ① BUG-S7-11-01（★ 本次最重要产出）：完成度谓词用的是 `planned = len(plan_steps)`
       的**原始步数**，不是"可执行步数"。⇒ 计划里只要有一条 agent 无从执行的步骤
       （无 command 键 / command 为空串 / 纯 `cd` / 自然语言描述），`completed` 永远
       追不上 `planned`，**success 变成不可达**：即便 agent 完全照做、命令全 exit 0、
       指标齐全，也恒判 INCOMPLETE、烧满 `MAX_FIX_LOOP_COUNT` 轮修复、最后推到
       interrupt#2。这与 R-S7-59 的后果同型（假红 + 白烧预算），但**成因完全不同**：
       R-S7-59 赖的是"agent 不听话"，本条**与 agent 是否听话无关**，是判定层自身的
       退化输入缺陷 —— 因此它**在 mock 层就能证伪**（dev-plan §53 称该后果"mock 层
       证不到"，对这条成因不成立）。dev-plan §49.2 第 6 条与 R-S7-59 正文两处写的是
       `completed < planned_actionable`（**actionable**），实现落成了 `planned`。

    ② 纯观测红线（CP-7.5-4）的守门有盲区：既有守门只钉死"留痕结论不得影响
       `success`"，而在**成功场景**下 `feedback` 根本不进 `ExecutionResult`（`errors`
       仅在 `not success` 时填充）⇒ 把留痕结论接进 **feedback** 的写法可以完整躲过
       既有守门（本文件实测：改坏后 2445 全绿）。本区段用"打桩前后节点输出投影逐字节
       相同"的一般化守门补上。

    ③ `auto_fixable` 双真相源：`_apply_incomplete_execution` 与 `_apply_no_metrics`
       都是**硬编码** `auto_fixable=True`，而 `_feedback_from_committed_result` 是
       `category in AUTO_FIXABLE` 推导 ⇒ 两者可以静默漂移。实测：把新枚举从
       `AUTO_FIXABLE` 摘掉后，**首跑路径照样回 coding、guard 重入路径却变成不可修复**
       —— 同一份落盘结果两条路径判两样（这也正是 dev-plan CP-7.6-3 原验红手法失效的
       根因，§56.3 P-51 只记了现象、未记这条一致性缺口）。

    ④ CP-7.6-4 未实做（dev-plan §56.3 P-52 如实登记）：撞 `MAX_FIX_LOOP_COUNT`
       / 预算不足时新分类是否走既有两段式 interrupt#2，本区段补齐。

    ⑤ R-S7-59 的**可测边界**：agent 是否听话只能真跑证伪，但"不听话会怎样"完全可以
       在 mock 层机械复现。本区段把"只补跑失败那一步"的不听话 agent 连跑到轮次上限，
       钉死"每一轮都回 coding、最后落 interrupt#2"这条后果链。

全离线（mock agent + tmp_path），零 API 配额。**不修改任何生产代码**。
"""

from __future__ import annotations

import importlib
import json
import logging
from typing import Any, Dict, List, Optional

import pytest

import config
from core import secrets_store
from core.state import ExecutionMode

execution_module = importlib.import_module("core.nodes.execution")
reporting_module = importlib.import_module("core.nodes.reporting")

from core.nodes.execution import execution  # noqa: E402
from sandbox.local_venv import SandboxPrepareResult, SandboxRunResult  # noqa: E402

ErrorCategory = execution_module.ErrorCategory
ExecutionFeedback = execution_module.ExecutionFeedback
ExecAgentOutput = execution_module.ExecAgentOutput

_METRICS_LINE = '<METRICS>{"acc": 0.9}</METRICS>'


# ---------------------------------------------------------------------------
# fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_process_sensitive_set():
    secrets_store._SENSITIVE_VALUES.clear()
    yield
    secrets_store._SENSITIVE_VALUES.clear()


@pytest.fixture(autouse=True)
def _workspace(tmp_path, monkeypatch):
    ws = tmp_path / "workspace"
    ws.mkdir()
    monkeypatch.setattr(config, "WORKSPACE_DIR", ws)
    return ws


@pytest.fixture(autouse=True)
def _no_artifacts(monkeypatch):
    monkeypatch.setattr(execution_module, "collect_artifacts", lambda *a, **k: [])


def _prep() -> SandboxPrepareResult:
    return SandboxPrepareResult(
        success=True, venv_dir="/w/.venv", python_exe="/w/.venv/bin/python", pip_exe="",
        env_info={"python_version": "Python 3.11"},
        install_log="ok", install_failed_packages=[], error=None,
    )


def _run(command: List[str], exit_code: int = 0, stdout: str = "") -> SandboxRunResult:
    return SandboxRunResult(
        exit_code=exit_code, stdout=stdout, stderr="",
        duration_seconds=0.1, timed_out=False,
        output_truncated=False, command=list(command),
    )


def _agent_out(runs, ledger=None, rounds: int = 2) -> ExecAgentOutput:
    return ExecAgentOutput(
        prep=_prep(), run_results=list(runs), rounds_used=rounds, llm_calls=rounds,
        step_ledger=list(ledger or []),
    )


def _patch_agent(monkeypatch, out: ExecAgentOutput) -> None:
    monkeypatch.setattr(
        execution_module, "_run_execution_agent", lambda state, wd, plan: out,
    )


def _state(steps: List[Any], **overrides: Any) -> Dict[str, Any]:
    st: Dict[str, Any] = {
        "llm_config_set": {"default": {"model": "test"}},
        "code_output_dir": "/tmp/s711-gap-workdir",
        "reproduction_plan": {
            "execution_steps": steps, "environment": {"dependencies": []},
        },
        "paper_analysis": {"metrics": []},
        "execution_mode": ExecutionMode.FULL,
        "node_errors": [],
        "degraded_nodes": [],
        "fix_loop_history": [],
        "fix_loop_count": 0,
        "retry_budget_remaining": 40,
        "_dev_loop_llm_calls": 0,
        "_dev_loop_route": None,
        "execution_result": None,
        "current_step": "coding",
    }
    st.update(overrides)
    return st


def _recon(planned: int, completed: int, **extra: Any) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "planned": planned, "executed": completed, "completed": completed,
        "unexecuted_steps": [], "extra_commands": [], "attribution_unavailable": False,
    }
    out.update(extra)
    return out


# ===========================================================================
# ① BUG-S7-11-01：计划含"无从执行"的步骤 ⇒ success 不可达（★ 本次最重要产出）
# ===========================================================================
#
# 四种形态都来自真实计划里会出现的东西：LLM 写计划时把"人工检查产物"" 查看图表"
# 之类的收尾动作也编成一步是常见现象（planning prompt 只是"要求"每步含命令，
# `check_plan` 对空命令是 `continue` 放行、`_coerce_step_list` 还容忍纯字符串元素，
# 全链路**没有任何一处强制 command 非空可执行**）。
#
# 判定链路：`_reconcile_steps` 的 `planned = len(steps)` 计入这些步骤，而它们
#   - 进不了归属规则②的 `plan_index`（`_extract_command_str` 返回 None，或 `cd` 头被跳过）；
#   - 也拿不到归属规则①的自报（agent 没东西可跑，自然不会声明 step_index）；
# ⇒ 恒 `completed < planned` ⇒ `_completion_insufficient` 恒 True ⇒ success 恒 False。

_UNRUNNABLE_STEP_SHAPES = [
    pytest.param({"step_name": "查看 outputs/ 下的图表确认可视化正常"}, id="no_command_key"),
    pytest.param({"step_name": "人工核对指标", "command": ""}, id="empty_command"),
    pytest.param({"step_name": "进入代码目录", "command": "cd ."}, id="pure_cd_step"),
]


def _obedient_run_of_one_real_step(monkeypatch, extra_step: Dict[str, Any]):
    """一个**完全照做**的 agent：把计划里所有可执行步骤跑完、全 exit 0、产出指标。"""
    steps: List[Any] = [{"step_name": "训练", "command": "python train.py"}, extra_step]
    runs = [_run(["python", "train.py"], stdout=_METRICS_LINE)]
    _patch_agent(monkeypatch, _agent_out(runs, [(0, ["python", "train.py"], 0)]))
    return execution(_state(steps))


@pytest.mark.parametrize("extra_step", _UNRUNNABLE_STEP_SHAPES)
def test_bug_s711_01_unrunnable_plan_step_forces_endless_fix_loop(monkeypatch, extra_step):
    """BUG-S7-11-01 **修复后**的完整行为断言（2026-08-01 @全栈开发代理 转正）。

    ⚠ 本条原为"现状钉死（characterization）"——逐字钉住缺陷行为（success False +
    回 coding），使修复必然经过这里。缺陷已修（分母改为 `planned_actionable`），
    故按同一套前置条件把**正确行为**逐条钉死，强度只增不减：
    原来断言 3 条（success/errors/route），现在断言 6 条（多钉 `planned_actionable`、
    `unexecuted_steps` 为空、`node_errors` 为空）。**一条前置断言都没删。**
    """
    out = _obedient_run_of_one_real_step(monkeypatch, extra_step)
    er = out["execution_result"]

    # 前置条件成立：命令全 0、指标有、agent 把能跑的都跑了、还诚实声明了归属。
    assert er["metrics"], "夹具前提：指标必须解析出来（否则这条测的就不是完成度了）"
    assert er["step_reconciliation"]["completed"] == 1
    # `planned` 仍是**原始步数**（展示"计划共 N 步" + 自报 step_index 的合法区间）。
    assert er["step_reconciliation"]["planned"] == 2
    # 判定分母是**可执行步数**——那条无从执行的步骤不进分母。
    assert er["step_reconciliation"]["planned_actionable"] == 1
    # 它同样不进"未执行清单"：否则 reporting 的 incomplete_execution 标注会在
    # success=True 时照样点火，制造 CP-7.9-3 明令禁止的自相矛盾报告。
    assert er["step_reconciliation"]["unexecuted_steps"] == []

    # 修复后：可执行步骤全跑完 ⇒ 判成功、不回 coding、不落 node_errors。
    assert er["success"] is True
    assert er["errors"] == []
    assert out["_dev_loop_route"] is None
    assert out.get("node_errors") in (None, [])


@pytest.mark.parametrize("extra_step", _UNRUNNABLE_STEP_SHAPES)
def test_bug_s711_01_expected_success_when_every_runnable_step_is_done(
    monkeypatch, extra_step,
):
    """BUG-S7-11-01 期望行为：可执行步骤全部跑完 + 全 exit 0 + 有指标 ⇒ 应判成功。

    2026-08-01 @全栈开发代理：缺陷已修，本条**已由 `xfail(strict=True)` 转正为常规
    断言**（S7-10 的 BUG-S7-10-01 同款流程）。断言原文一字未动，只摘掉 xfail 标记。
    """
    out = _obedient_run_of_one_real_step(monkeypatch, extra_step)
    assert out["execution_result"]["success"] is True


def test_bug_s711_01_control_group_all_steps_runnable_still_succeeds(monkeypatch):
    """阴性对照：把那条步骤换成可执行命令并跑掉 ⇒ 照常判成功。

    有这条对照，上面两条的红/xfail 才能归因到"步骤不可执行"，而不是夹具本身写坏了。
    """
    steps = [
        {"step_name": "训练", "command": "python train.py"},
        {"step_name": "汇总", "command": "python summarize.py"},
    ]
    runs = [
        _run(["python", "train.py"], stdout=_METRICS_LINE),
        _run(["python", "summarize.py"]),
    ]
    ledger = [(0, ["python", "train.py"], 0), (1, ["python", "summarize.py"], 0)]
    _patch_agent(monkeypatch, _agent_out(runs, ledger))
    out = execution(_state(steps))
    assert out["execution_result"]["success"] is True
    assert out["_dev_loop_route"] is None


def test_bug_s711_01_natural_language_command_is_the_same_trap(monkeypatch):
    """同型第四种形态：`command` 写成一句中文描述。

    它**能**进归属规则②的索引（shlex 拆得出 token），但 agent 永远不会真去执行它
    ⇒ 后果与前三种一模一样。单列一条是因为它最像真实 LLM 计划的产物。
    """
    steps = [
        {"step_name": "训练", "command": "python train.py"},
        {"step_name": "检查", "command": "人工查看 outputs/figures 下的图是否正常"},
    ]
    runs = [_run(["python", "train.py"], stdout=_METRICS_LINE)]
    _patch_agent(monkeypatch, _agent_out(runs, [(0, ["python", "train.py"], 0)]))
    out = execution(_state(steps))
    assert out["execution_result"]["success"] is False
    assert out["_dev_loop_route"] == execution_module._ROUTE_RETRY_CODING


def test_bug_s711_01_predicate_level_reproduction():
    """谓词层最小复现：缺陷在 `_reconcile_steps` → `_completion_insufficient` 这一段。

    把缺陷定位到谓词层（而非节点层）是为了让修复方向明确：要改的是"分母怎么算"，
    不是"要不要收严 success"。2026-08-01 修复后本条转为**修复后行为**的谓词层断言，
    并额外钉死"两套编号不混用"（`planned` 仍是原始步数）与回落语义。
    """
    steps = [
        {"step_name": "训练", "command": "python train.py"},
        {"step_name": "看图"},  # 无 command
    ]
    runs = [_run(["python", "train.py"])]
    recon = execution_module._reconcile_steps(steps, runs, [(0, ["python", "train.py"], 0)])
    assert recon["planned"] == 2, "planned 仍是原始步数（展示口径 + 自报下标合法区间）"
    assert recon["planned_actionable"] == 1, "判定分母只数有可执行命令的步骤"
    assert recon["completed"] == 1, "分子不可能包含它（它没有命令可跑）"
    assert execution_module._completion_insufficient(recon) is False

    # 分母口径本身的最小真值：把 actionable 键摘掉 → 回落 planned → 退回旧口径判 True。
    # 这一条同时钉死"回落是保守行为、不是新语义"（旧 checkpoint 兼容 R-6）。
    legacy = {k: v for k, v in recon.items() if k != "planned_actionable"}
    assert execution_module._completion_insufficient(legacy) is True


def test_bug_s711_01_actionable_predicate_truth_table():
    """`_is_actionable_step` 的判据真值表（分母判据必须确定性、边界清晰、可单测）。

    ⚠ 最后一格（自然语言 command）是**刻意判 True** 的取舍：它与"真命令写错/拼错"
    在字符串层没有确定性判据可分，任何启发式都会把真步骤误剔出分母 ⇒ 往假绿方向退。
    ⇒ 宁可算进分母。残留后果与本 bug 同型，根治出口在 planning 侧（dev-plan §56.3）。
    """
    f = execution_module._is_actionable_step
    # 不可执行：无 command 键 / 空串 / 纯空白 / 纯 cd / cd 复合但无实际命令 / 非 dict 空串
    assert f({"step_name": "看图"}) is False
    assert f({"step_name": "看图", "command": ""}) is False
    assert f({"step_name": "看图", "command": "   "}) is False
    assert f({"command": "cd ."}) is False
    assert f({"command": "cd repo && source venv/bin/activate"}) is False
    assert f("") is False
    assert f(None) is False
    # 可执行：普通命令 / cd 复合里带真命令 / 纯字符串步骤 / 自然语言（刻意收进分母）
    assert f({"command": "python train.py"}) is True
    assert f({"command": "cd repo && python run.py"}) is True
    assert f("python train.py") is True
    assert f({"command": "人工查看 outputs/figures 下的图是否正常"}) is True


# ===========================================================================
# ② 纯观测红线（CP-7.5-4）的盲区补强
# ===========================================================================


def _projection(out: Dict[str, Any]) -> str:
    """节点输出里**一切与判定/路由/落盘有关**的部分（剔除易变字段）。

    比"只看 success"强得多：留痕结论一旦被消费，无论它影响的是 success、feedback
    分类、错误文案、路由还是 fix_loop_history，这份投影都会变。

    2026-08-01 @全栈开发代理 复核缺陷二时**再度补强**：原投影只挑了 5 个字段
    （`execution_result` 全键 + route + fix_loop_count + fix 分类 + node_error **类型**），
    仍有三条泄漏路径能躲过它——
        ① `degraded_nodes`（不在投影里）；
        ② `node_errors` 的 **message / error_category 文案**（只取了 error_type）；
        ③ 任何**新增的 state 键**（投影是白名单，白名单外的键写什么都看不见）。
    ⇒ 改为**整份节点输出的黑名单式快照**：除已知易变字段外一律纳入比对，
    新增键自动被覆盖（白名单→黑名单是强度提升，不是放宽）。
    """
    scrubbed: Dict[str, Any] = {}
    for key, value in (out or {}).items():
        if key == "execution_result" and isinstance(value, dict):
            scrubbed[key] = {
                k: value[k] for k in sorted(value) if k not in ("runtime_seconds",)
            }
        elif key == "fix_loop_history" and isinstance(value, list):
            scrubbed[key] = [
                {k: r[k] for k in sorted(r) if k != "timestamp"}
                if isinstance(r, dict) else r
                for r in value
            ]
        elif key == "node_errors" and isinstance(value, list):
            scrubbed[key] = [
                {k: e[k] for k in sorted(e) if k != "timestamp"}
                if isinstance(e, dict) else e
                for e in value
            ]
        else:
            scrubbed[key] = value
    return json.dumps(scrubbed, sort_keys=True, ensure_ascii=False, default=str)


def _screaming_audit(plan_steps, run_results, step_ledger=None):
    """打桩：疯狂报不符，并**返回一个真值**（真函数返回 None）。"""
    logging.getLogger("core.nodes.execution").warning(
        "[execution] 步骤自报与实际执行不符 999 条（打桩）",
    )
    return ["mismatch"] * 999


@pytest.mark.parametrize(
    "steps, runs, ledger, scenario",
    [
        (
            [{"step_name": "训练", "command": "python train.py"}],
            [_run(["python", "train.py"], stdout=_METRICS_LINE)],
            [(0, ["python", "train.py"], 0)],
            "success",
        ),
        (
            [{"step_name": "a", "command": "python a.py"},
             {"step_name": "b", "command": "python b.py"}],
            [_run(["python", "a.py"], stdout=_METRICS_LINE)],
            [(0, ["python", "a.py"], 0)],
            "incomplete",
        ),
        (
            [{"step_name": "a", "command": "python a.py"}],
            [_run(["python", "a.py"], exit_code=1)],
            [(0, ["python", "a.py"], 1)],
            "failed",
        ),
    ],
    ids=["success", "incomplete", "failed"],
)
def test_gap_pure_observation_output_is_identical_when_audit_screams(
    monkeypatch, steps, runs, ledger, scenario,
):
    """★ 补强 CP-7.5-4：留痕函数疯狂报不符 ⇒ 节点输出投影**逐字节不变**。

    既有守门（`test_da_4_cp_7_5_4_audit_is_pure_observation`）只断言 `success is True`
    与 `errors == []`，而在**成功场景**下 `feedback` 压根不进 `ExecutionResult`
    （`_build_execution_result` 只在 `not success` 时才把 feedback 写进 `errors`）
    ⇒ 把留痕结论接进 **feedback** 的实现能完整躲过它。本条实测过：在主流程写成
    `if _audit_res: feedback = ExecutionFeedback(RUNTIME, ...)` 之后，
    `-m "not e2e and not browser"` 仍是 2445 全绿。

    ⇒ 改为"打桩前后整份判定投影相同"，并覆盖 success / incomplete / failed 三种
    场景，把红线（返回值不得被判定 / 渲染 / state 消费）真正机制化。
    """
    def build_out():
        return execution(_state([dict(s) for s in steps]))

    _patch_agent(monkeypatch, _agent_out(runs, ledger))
    baseline = _projection(build_out())

    monkeypatch.setattr(execution_module, "_audit_declared_steps", _screaming_audit)
    _patch_agent(monkeypatch, _agent_out(runs, ledger))
    stubbed = _projection(build_out())

    assert stubbed == baseline, (
        f"留痕函数（{scenario} 场景）的结论泄漏进了判定 / 路由 / 落盘 —— "
        "它是纯观测，返回值不得被任何人消费"
    )


@pytest.mark.parametrize(
    "steps, runs, ledger",
    [
        ([], [], []),
        (None, [], None),
        ([{"command": "python a.py"}], [_run(["python", "b.py"])], [(0, ["python", "b.py"], 0)]),
        ([{"command": "python a.py"}], [_run(["python", "b.py"])], [("x", ["python", "b.py"], 0)]),
        ([{"command": "python a.py"}], [_run(["python", "b.py"])], ["畸形条目"]),
        ([{"command": "python a.py"}], [_run([])], [(0, [], 0)]),
    ],
    ids=["empty", "none", "mismatch", "bad_index", "bad_entry", "empty_cmd"],
)
def test_gap_audit_always_returns_none_and_never_raises(steps, runs, ledger):
    """留痕函数对各种退化输入都必须 **返回 None 且不抛异常**。

    签名返回 None 是这条红线的硬保障（dev-plan §52.5 实施要点 2），但既有用例只在
    一种正向输入上验过；观测函数一旦在畸形输入上抛异常，就会把整个 execution 节点
    炸掉——那比它想防的问题严重得多。
    """
    assert execution_module._audit_declared_steps(steps, runs, ledger) is None


# ===========================================================================
# ③ `auto_fixable` 双真相源一致性
# ===========================================================================


def test_gap_apply_chain_auto_fixable_agrees_with_the_auto_fixable_set():
    """改判链路产出的 feedback，其 `auto_fixable` 必须与 `AUTO_FIXABLE` 集合一致。

    为什么要有这条（dev-plan §56.3 **P-51** 只记了现象、没记根因）：
    `_apply_incomplete_execution` / `_apply_no_metrics` 都是**硬编码**
    `auto_fixable=True`，而 guard 重入路径的 `_feedback_from_committed_result` 是
    `category in AUTO_FIXABLE` **推导**。两者是两个真值源：把新枚举从 `AUTO_FIXABLE`
    里摘掉之后，**首跑路径照样回 coding、guard 重入路径却变成不可修复** —— 同一份
    落盘结果，两条路径判两样。这也正是 CP-7.6-3 原验红手法（摘集合）失效的根因。
    """
    none_fb = ExecutionFeedback(ErrorCategory.NONE, False, "执行成功", "", "")
    produced = [
        execution_module._apply_incomplete_execution(none_fb, _recon(9, 2), True),
        execution_module._apply_no_metrics(none_fb, {}, {}, True),
    ]
    for fb in produced:
        assert fb.auto_fixable is (fb.category in execution_module.AUTO_FIXABLE), (
            f"{fb.category.value}: 构造出来的 auto_fixable={fb.auto_fixable}，"
            f"但集合口径是 {fb.category in execution_module.AUTO_FIXABLE} —— 双真相源已漂移"
        )


def test_gap_first_pass_and_guard_reentry_agree_on_auto_fixable():
    """同一份落盘结果，首跑路径与 guard 重入路径对"能否自动修复"的判断必须一致。"""
    fb_first = execution_module._apply_incomplete_execution(
        ExecutionFeedback(ErrorCategory.NONE, False, "执行成功", "", ""),
        _recon(9, 2), True,
    )
    fb_reentry = execution_module._feedback_from_committed_result({
        "success": False,
        "errors": [f"[error_category={fb_first.category.value}] {fb_first.summary}"],
    })
    assert fb_reentry.category is fb_first.category
    assert fb_reentry.auto_fixable is fb_first.auto_fixable


# ===========================================================================
# ④ CP-7.6-4 补齐（dev-plan §56.3 P-52 登记为未实做）
# ===========================================================================


def _incomplete_run(monkeypatch, **state_overrides):
    steps = [
        {"step_name": "a", "command": "python a.py"},
        {"step_name": "b", "command": "python b.py"},
    ]
    _patch_agent(monkeypatch, _agent_out(
        [_run(["python", "a.py"], stdout=_METRICS_LINE)], [(0, ["python", "a.py"], 0)],
    ))
    return execution(_state(steps, **state_overrides))


def test_gap_cp_7_6_4_incomplete_at_fix_loop_cap_falls_to_two_stage_interrupt(monkeypatch):
    """CP-7.6-4（本批未实做）：撞 `MAX_FIX_LOOP_COUNT` 时新分类走既有两段式 interrupt#2。

    两段式语义（S-1 重跑幂等契约）：本回合 sandbox 刚跑过、尚未过 checkpoint 边界 ⇒
    **不在函数体内 interrupt**，而是先落盘 + 置 await 标记 return，由 self-loop 重入
    后再 interrupt。R-S7-62 明确接受"最坏烧满 20 轮"，那这一格就必须有用例守着。
    """
    out = _incomplete_run(monkeypatch, fix_loop_count=config.MAX_FIX_LOOP_COUNT)
    assert out["execution_result"]["success"] is False
    assert out["_dev_loop_route"] == execution_module._ROUTE_AWAIT_INTERRUPT
    assert "__interrupt__" not in out, "首跑回合不得在函数体内 interrupt（重跑幂等契约）"
    assert out.get("fix_loop_count", config.MAX_FIX_LOOP_COUNT) == config.MAX_FIX_LOOP_COUNT


def test_gap_cp_7_6_4_incomplete_with_insufficient_budget_falls_to_interrupt(monkeypatch):
    """预算不足一回合时，新分类同样落两段式 interrupt#2（S7-01 预算门下沉后的口径）。"""
    out = _incomplete_run(
        monkeypatch,
        retry_budget_remaining=config.DEV_LOOP_MIN_CALLS_PER_ROUND - 1,
    )
    assert out["_dev_loop_route"] == execution_module._ROUTE_AWAIT_INTERRUPT


def test_gap_cp_7_6_4_incomplete_at_dev_loop_llm_cap_falls_to_interrupt(monkeypatch):
    """子预算（`MAX_DEV_LOOP_LLM_CALLS`）触顶时同理。"""
    out = _incomplete_run(
        monkeypatch, _dev_loop_llm_calls=config.MAX_DEV_LOOP_LLM_CALLS,
    )
    assert out["_dev_loop_route"] == execution_module._ROUTE_AWAIT_INTERRUPT


# ===========================================================================
# ⑤ R-S7-59 的可测边界：agent 不听话的**后果**在 mock 层完全可复现
# ===========================================================================


def test_gap_r_s7_59_disobedient_agent_burns_every_fix_round(monkeypatch):
    """R-S7-59 后果链机械复现：只补跑失败那一步的 agent ⇒ 每一轮都被打回 coding。

    ⚠ **本条能测到什么、测不到什么**（这正是 R-S7-59 的可测边界）：
      - **能测**：一旦 agent 不做全量重跑，判定层会怎么反应 —— 每一轮 INCOMPLETE、
        每一轮 `fix_loop_count += 1`，直到撞顶转 interrupt#2。这条后果链**不需要真跑**。
      - **测不到**：agent 到底听不听话（本项目实测提示词服从率约 75%）。那是 LLM 行为，
        只能靠真跑观测"逐轮 completed 是否覆盖全部计划步骤"证伪。
    ⇒ dev-plan R-S7-59 写的"唯一证伪手段是真跑、mock 层证不到"**只对后半句成立**；
      前半句（后果链）本文件已在 mock 层钉死。
    """
    steps = [
        {"step_name": f"第 {i} 步", "command": f"python s{i}.py"} for i in range(3)
    ]
    # 不听话的 agent：每个修复回合只重跑第 0 步（上一轮失败的那步），其余不碰。
    _patch_agent(monkeypatch, _agent_out(
        [_run(["python", "s0.py"], stdout=_METRICS_LINE)], [(0, ["python", "s0.py"], 0)],
    ))

    for round_no in range(config.MAX_FIX_LOOP_COUNT):
        out = execution(_state(steps, fix_loop_count=round_no))
        assert out["execution_result"]["success"] is False, f"第 {round_no} 轮竟判成功"
        assert out["_dev_loop_route"] == execution_module._ROUTE_RETRY_CODING
        assert out["fix_loop_count"] == round_no + 1

    # 第 MAX 轮：修复轮次耗尽 → 两段式 interrupt#2（用户被打断）。
    final = execution(_state(steps, fix_loop_count=config.MAX_FIX_LOOP_COUNT))
    assert final["_dev_loop_route"] == execution_module._ROUTE_AWAIT_INTERRUPT


def test_gap_r_s7_59_obedient_full_rerun_converges_in_one_round(monkeypatch):
    """阳性对照：听话的 agent（修复轮从第一步全量重跑）**一轮即收敛**。

    有这条对照，上一条才能归因到"没有全量重跑"，而不是"判定层根本判不出成功"。
    """
    steps = [
        {"step_name": f"第 {i} 步", "command": f"python s{i}.py"} for i in range(3)
    ]
    runs = [
        _run(["python", f"s{i}.py"], stdout=_METRICS_LINE if i == 0 else "")
        for i in range(3)
    ]
    ledger = [(i, ["python", f"s{i}.py"], 0) for i in range(3)]
    _patch_agent(monkeypatch, _agent_out(runs, ledger))
    out = execution(_state(steps, fix_loop_count=3))
    assert out["execution_result"]["success"] is True
    assert out["_dev_loop_route"] is None


# ===========================================================================
# ⑥ 谓词退化输入补角 + reporting 侧 P-47 撤回后的残留矛盾（如实钉死）
# ===========================================================================


@pytest.mark.parametrize(
    "recon, expected",
    [
        ({"planned": 3, "completed": 5}, False),        # 完成数超过计划（计划外命令误归属）
        ({"planned": -1, "completed": -5}, False),      # 负数：planned > 0 为假
        ({"planned": 3.0, "completed": 1}, False),      # float 不是 int
        ({"planned": 3, "completed": 1.0}, False),
        ({"planned": None, "completed": None}, False),
        ([], False),                                     # 非 dict
        ("planned=3", False),
    ],
)
def test_gap_predicate_degenerate_inputs(recon, expected):
    """谓词退化输入补角：既有真值表没覆盖的几种形态，一律 False 且零异常。

    `completed > planned` 这一格值得单列：它在"计划外命令被规则②误归属"时真的会出现，
    而 `<` 的写法天然吃得下（这是实现对的地方，用例把它钉住防日后改成 `!=`）。
    """
    assert execution_module._completion_insufficient(recon) is expected


def test_gap_attribution_unavailable_judgement_and_report_diverge():
    """P-47 撤回后的**残留矛盾**如实钉死（不是缺陷判定，是既有契约的已知代价）。

    `attribution_unavailable` 时：判定层照常判不成功（`completed=0 < planned`），
    但报告的 `incomplete_execution` 标注**按既有 R-2 保守契约不点火**（dev-plan
    §56.3 P-47 撤回了那条析取项）。⇒ 用户会看到"没成功"却看不到"哪一步没跑完"。
    本条把这个已知落差钉住：日后若有人认为它是 bug 要改，会先撞到这条用例、必须
    连同 R-2 契约一起重议，而不是单点补一个析取项又把 t33/t34 撞红。
    """
    exec_result = {
        "success": False, "metrics": {"acc": 0.9}, "logs": "",
        "errors": ["[error_category=incomplete_execution] 命令都正常结束了，但计划里的步骤没跑完（已跑完 0/9 步）"],
        "artifacts": [], "runtime_seconds": 1.0, "environment_info": {},
        "step_reconciliation": {
            "planned": 9, "executed": 0, "completed": 0,
            "unexecuted_steps": [], "extra_commands": ["python whatever.py"],
            "attribution_unavailable": True,
        },
        "budget_truncated": False, "metrics_groups": {}, "degraded_credentials": [],
    }
    conclusion = reporting_module._determine_conclusion(
        {"reproduction_plan": {"execution_steps": []}}, exec_result, None,
    )
    assert conclusion["level"] != "engineering", "判定层：没跑完就不是'代码跑通'"
    assert "incomplete_execution" not in conclusion["annotations"], (
        "R-2 保守契约：归属不可用时不打未执行标注（P-47 撤回了那条析取项）"
    )
