"""Sprint 7 需求 S7-11（批次 7 / 任务 T-S7-7-3 ~ T-S7-7-7）：执行完整度进判定。

一句话：把"成功"的定义从「跑过的命令有没有跑错」改成「**该跑的跑完没有** 且 跑过的
没跑错 且 有指标」。立项依据是 2026-08-01 第三次 UMAP 真跑——计划 9 步只完成 2 步、
执行总耗时 0.243 秒，却判 `success=true`，报告形态出 `full_success`，而同一份报告顶部
如实印着「计划步骤未全部执行完成（已完成 2/9 步）」：**判定逻辑压根没看这个数**。
更硬的物证（dev-plan §56.1 取证表）：round_0 跑了 17 条命令 5 条失败 ⇒ 判失败；
round_1 只跑 2 条全 0 ⇒ 判**成功**，而那个"成功指标"还是汇总 round_0 残留产物得来的。
**做了 17 件事失败，做了 2 件事成功** —— 这是标准的反向激励。

★ 完成度的数据源（Maria 2026-08-01 复审拍板，dev-plan §49.0 变更 1）：
    **直接采信 execution agent 自报的 `step_index`**，即复用既有 `_reconcile_steps`
    产出的 `step_reconciliation`，**不新写确定性完成度算法、不新增任何 schema 键**。
    依据是存档实测——agent 首轮诚实声明了 8/9 步，**根本没有虚报**；问题从来不在自报
    可信度。代价（理论上自报可被刷满，R-S7-65）由 `_audit_declared_steps` 的 WARNING
    留痕对冲：**信任但留痕，不阻断**。

覆盖 dev-plan §52 批次 7 的检查点：
    - CP-7.3-1~6（DA-S7-11-1）修法 A：修复轮上下文接线 + 零扰动 + 字节幂等 + 注释订正；
    - CP-7.4-3~6（DA-S7-11-2/3）修法 B：提示词纪律正负两向 + `step_index` 强制声明；
    - CP-7.5-1~5（DA-S7-11-4）单点谓词真值表 / 防御 + 防伪留痕正负四向 + **纯观测守门**；
    - CP-7.6-1~8（DA-S7-11-5/6/7）四格真值表 + **单点谓词打桩** + 路由回 coding +
      guard 重入 round-trip + 早停不误触 + 三处映射点零改动；
    - CP-7.7-1/2/5（DA-S7-11-8）对外口径："B 档"消失、三条件在位、UMAP 同型场景端到端；
    - DA-S7-11-9 四条修法同批落地的机制化守门（**四条臂全部是行为断言，禁源码子串**）。

全离线（mock agent + tmp_path），零 API 配额。
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

from core.nodes.execution import execution  # noqa: E402  # 常量走 execution_module（callable 遮蔽陷阱）
from sandbox.local_venv import SandboxPrepareResult, SandboxRunResult  # noqa: E402

ExecAgentOutput = execution_module.ExecAgentOutput
ErrorCategory = execution_module.ErrorCategory
ExecutionFeedback = execution_module.ExecutionFeedback

_METRICS_LINE = '<METRICS>{"acc": 0.9}</METRICS>'


# ---------------------------------------------------------------------------
# fixtures / helpers（沿用 test_sprint5_t26 约定）
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_process_sensitive_set():
    secrets_store._SENSITIVE_VALUES.clear()
    yield
    secrets_store._SENSITIVE_VALUES.clear()


@pytest.fixture(autouse=True)
def secrets_workspace(tmp_path, monkeypatch):
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


def _agent_out(
    runs: List[SandboxRunResult],
    ledger: Optional[List[Any]] = None,
    rounds: int = 2,
) -> ExecAgentOutput:
    return ExecAgentOutput(
        prep=_prep(), run_results=runs, rounds_used=rounds, llm_calls=rounds,
        step_ledger=list(ledger or []),
    )


def _steps(*commands: str) -> List[Dict[str, str]]:
    return [
        {"step_name": f"第 {i + 1} 步", "command": c} for i, c in enumerate(commands)
    ]


def _base_state(steps: List[Dict[str, str]], **overrides: Any) -> Dict[str, Any]:
    state: Dict[str, Any] = {
        "llm_config_set": {"default": {"model": "test"}},
        "code_output_dir": "/tmp/s711-workdir",
        "reproduction_plan": {
            "execution_steps": steps,
            "environment": {"dependencies": ["numpy"]},
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
    state.update(overrides)
    return state


def _patch_agent(monkeypatch, out: ExecAgentOutput) -> Dict[str, int]:
    cnt = {"agent": 0}

    def fake_agent(state, work_dir, plan):
        cnt["agent"] += 1
        return out

    monkeypatch.setattr(execution_module, "_run_execution_agent", fake_agent)
    return cnt


def _recon(planned: int, completed: int, **extra: Any) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "planned": planned, "executed": completed, "completed": completed,
        "unexecuted_steps": [], "extra_commands": [], "attribution_unavailable": False,
    }
    out.update(extra)
    return out


# ===========================================================================
# DA-S7-11-1 / CP-7.3-*：修法 A —— 修复轮上下文接线（上一轮改了哪些文件 + 怎么改的）
# ===========================================================================


def test_da_1_cp_7_3_1_fix_round_context_carries_last_fix():
    """CP-7.3-1：修复轮 payload 含 `last_fix.note` 与 `last_fix.files`。

    这是修法 B 的地基：提示词要求 agent 重跑验证修复，而 agent 只有拿到"代码已被改、
    改了这些文件、这么改的"才**有依据**相信重跑会有不同结果；否则它是在执行一条没有
    理由的指令（dev-plan §51.3 第 2 条）。
    """
    state = _base_state(
        _steps("python train.py"),
        fix_loop_count=1,
        execution_result={"errors": ["boom"], "logs": "traceback"},
        last_fix_note="已在各入口脚本开头统一加入 PROJECT_ROOT 到 sys.path",
        last_files_written=["/w/code/scripts/prep.py", "/w/code/train.py"],
    )
    payload = execution_module._build_execution_agent_context(
        state, "/w/code", state["reproduction_plan"],
    )
    assert payload["last_fix"]["note"] == (
        "已在各入口脚本开头统一加入 PROJECT_ROOT 到 sys.path"
    )
    assert payload["last_fix"]["files"] == ["prep.py", "train.py"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"fix_loop_count": 0},
        {"fix_loop_count": 1, "last_fix_note": "", "last_files_written": []},
        {"fix_loop_count": 1},  # 旧 checkpoint：两键都缺失
    ],
    ids=["first_round", "empty_values", "old_checkpoint_missing_keys"],
)
def test_da_1_cp_7_3_2_zero_perturbation_when_no_fix_info(overrides):
    """CP-7.3-2 零扰动：首轮 / 空值 / 旧 checkpoint 三形态下 payload 不含 `last_fix`。

    沿 `credential_degradations` 与 `scale_reduced_directive` 的"非空才注入"既有范式
    —— 无修复信息的路径 HumanMessage 字节与 sp7 基线一致，Prompt 前缀不被扰动。
    """
    state = _base_state(_steps("python train.py"), **overrides)
    if overrides.get("fix_loop_count"):
        state["execution_result"] = {"errors": ["boom"], "logs": ""}
    payload = execution_module._build_execution_agent_context(
        state, "/w/code", state["reproduction_plan"],
    )
    assert "last_fix" not in payload


def test_da_1_cp_7_3_3_byte_idempotent_and_basename_only():
    """CP-7.3-3 字节幂等 + 无绝对路径：连续两次调用序列化逐字节相同，files 全为 basename。"""
    state = _base_state(
        _steps("python train.py"),
        fix_loop_count=2,
        execution_result={"errors": [], "logs": ""},
        last_fix_note="修了导入路径",
        last_files_written=["/abs/path/a.py", "sub/dir/b.py"],
    )
    dumps = [
        json.dumps(
            execution_module._build_execution_agent_context(
                state, "/w/code", state["reproduction_plan"],
            ),
            ensure_ascii=False, sort_keys=True, default=str,
        )
        for _ in range(2)
    ]
    assert dumps[0] == dumps[1], "同一 state 两次调用必须字节一致（Prompt Cache 前提）"
    files = json.loads(dumps[0])["last_fix"]["files"]
    assert files == ["a.py", "b.py"]
    assert all("/" not in f for f in files), "files 必须是 basename，不得含绝对路径"


def test_da_1_cp_7_3_4_truncation_of_files_and_note():
    """CP-7.3-4 截断：files 超上限只留前 N + 计数尾巴；note 按 coding 侧上限截断。"""
    n = execution_module._LAST_FIX_FILES_MAX
    long_note = "详细说明" * 200
    out = execution_module._build_last_fix_context({
        "last_fix_note": long_note,
        "last_files_written": [f"f{i}.py" for i in range(n + 3)],
    })
    assert len(out["files"]) == n + 1
    assert out["files"][-1] == f"...共 {n + 3} 个"
    assert len(out["note"]) == execution_module._FIX_NOTE_MAX_CHARS


def test_da_1_cp_7_3_5_context_docstring_states_reverify_not_avoid():
    """CP-7.3-5 注释意图订正："避开"不再出现，改为"重跑验证"口径。

    ⚠ 本条断言守的是**注释**，是本项目少见的做法，理由（dev-plan §52.3 实施要点 5）：
    §49.2 第二层根因里"设计意图就是绕开上一轮错误"这句判断，**唯一的书面证据就是这
    行注释**。不改它，下一个读代码的人会照着"避开"继续实现——而 agent 当时确实照做了
    （修复轮只补跑两条命令就收尾）。
    """
    doc = execution_module._build_execution_agent_context.__doc__ or ""
    assert "避开上一轮" not in doc, "'避开上一轮已知错误'的口径必须已被订正"
    assert "重跑验证" in doc


# ===========================================================================
# DA-S7-11-2 / CP-7.4-*：修法 B —— execution 冻结区纪律
# ===========================================================================


def _body() -> str:
    return execution_module._EXECUTION_SYSTEM_PROMPT_BODY


def test_da_2_cp_7_4_3_full_rerun_discipline_present():
    """CP-7.4-3 正向：全量重跑纪律三层语义在位。

    ★ 这条是**判定层正确性的地基**（R-S7-59）：`run_results` / `step_ledger` 逐轮重置
    是正确设计（跨轮取并集等于"把上轮代码下的通过当成本轮代码下的通过"，是与本次修复
    初衷同型的假绿，已被 Maria 与架构双重否决）。因此判定口径是**单轮全量**——若 agent
    只补跑缺失的那几步，本轮就会恒判未完成。**这条纪律不在位 = 假红 + 白烧预算。**
    """
    body = _body()
    assert "从 execution_steps 的第一步开始按顺序全量重跑" in body
    assert "不再自动成立" in body
    assert "少跑步骤不会被判成功" in body


def test_da_2_cp_7_4_4_stale_wordings_narrowed_and_ac_s7_46_intact():
    """CP-7.4-4 负向/收窄 + S7-10 的 AC-S7-46 不破。"""
    body = _body()
    # 收窄：旧的无条件"禁止重复执行同一条命令"原文不再存在（会误伤修复回合的重跑验证）。
    assert "不要重复执行同一条命令空转" not in body
    assert "同一回合内不要用完全相同的命令反复空转" in body
    # AC-S7-46（S7-10 PRD §12）点名保留的三句，一字不动。
    assert "不得写入或修改任何代码文件" in body
    assert "交回代码生成环节修复" in body
    assert "cd（限工作区内）" in body
    # AC-S7-46 点名必须缺席的一句，仍然缺席。
    assert "修正相对路径" not in body


def test_da_2_cp_7_4_5_step_index_declaration_is_mandatory():
    """CP-7.4-5：`step_index` 声明升为**必须**，且没有引入冗余的自报字段。

    S7-11 起完成度**直接采信** `step_index`（dev-plan §49.0 变更 1）⇒ 漏报等于自称
    没跑。原方案里那个 `plan_steps_finished` 输出字段随确定性算法一并删除——判定既然
    用自报归属，再要一个自报数字就是无机械绑定的双源真相。
    """
    body = _body()
    assert "必须**以 step_index=i 声明归属" in body
    assert "漏报会让编排层认为该步没跑" in body
    assert "plan_steps_finished" not in body


def test_da_2_cp_7_4_6_no_interpolation_and_byte_identical_across_calls():
    """CP-7.4-6 零插值 + 跨调用字节一致（冻结区契约）。"""
    body = _body()
    # 冻结区禁的是**论文级 / 任务级动态值**，不是所有花括号——输出契约里那段 JSON
    # 骨架本就带花括号且是静态的（S7-10 建门时即如此）。故这里查的是插值形态。
    assert "{arxiv" not in body and "{work_dir" not in body and "{plan" not in body
    assert "arxiv" not in body.lower()
    assert "/data/" not in body and "/tmp/" not in body
    first = execution_module._build_execution_system_prompt()
    second = execution_module._build_execution_system_prompt()
    assert first == second


# ===========================================================================
# DA-S7-11-4 / CP-7.5-*：单点谓词 + 防伪留痕（纯函数层）
# ===========================================================================


@pytest.mark.parametrize(
    "recon, expected",
    [
        ({"planned": 9, "completed": 2}, True),
        ({"planned": 9, "completed": 9}, False),
        ({"planned": 0, "completed": 0}, False),      # 空计划不得被判永久红
        (None, False),                                 # 旧 checkpoint
        ({}, False),
        ({"planned": "x", "completed": 1}, False),     # 畸形快照
        ({"planned": 9}, False),                       # 缺键
        ({"planned": True, "completed": False}, False),  # bool 不是合法计数
    ],
)
def test_da_4_cp_7_5_1_predicate_truth_table_and_defense(recon, expected):
    """CP-7.5-1：单点谓词真值表 + 防御。

    畸形/缺失一律返回 False 是刻意的：宁可漏判也不误判红——判红会把用户推进修复循环，
    代价方向比漏判更差（R-6 旧 checkpoint 兼容红线）。
    """
    assert execution_module._completion_insufficient(recon) is expected


def test_da_4_cp_7_5_2_umap_shaped_rounds_are_both_insufficient():
    """CP-7.5-2：用 dev-plan §56.1 存档的真跑现场数字回放两轮对账。

    round_0：executed=8 / completed=3；round_1：executed=2 / completed=2。**两轮都
    没跑完 9 步** —— 而旧口径下 round_1 被判成功（全 0 + 有指标）。这就是"做 2 件事比
    做 17 件事更容易成功"的完整链条。
    """
    assert execution_module._completion_insufficient(_recon(9, 3)) is True
    assert execution_module._completion_insufficient(_recon(9, 2)) is True


def _audit_warnings(caplog) -> List[str]:
    return [
        r.getMessage() for r in caplog.records
        if r.levelno >= logging.WARNING and "自报与实际执行不符" in r.getMessage()
    ]


def test_da_4_cp_7_5_3_audit_fires_on_declared_command_mismatch(caplog):
    """CP-7.5-3 ①正向：自报第 0 步、实跑的却是完全不相干的命令 → 有 WARNING。

    这正是"换了命令还自报同一下标"这种虚报形态（R-S7-65 的对冲观测点）。
    """
    steps = _steps("python train.py")
    runs = [_run(["python", "totally_other.py"])]
    ledger = [(0, ["python", "totally_other.py"], 0)]
    with caplog.at_level(logging.WARNING):
        assert execution_module._audit_declared_steps(steps, runs, ledger) is None
    msgs = _audit_warnings(caplog)
    assert msgs and "1 条" in msgs[0]
    assert "自报第 1 步" in msgs[0]


def test_da_4_cp_7_5_3_audit_silent_when_declaration_matches(caplog):
    """CP-7.5-3 ②负向：自报下标与实跑命令一致 → 零 WARNING（不打噪声）。"""
    steps = _steps("python train.py")
    runs = [_run(["python", "train.py"])]
    with caplog.at_level(logging.WARNING):
        execution_module._audit_declared_steps(steps, runs, [(0, ["python", "train.py"], 0)])
    assert _audit_warnings(caplog) == []


def test_da_4_cp_7_5_3_audit_fires_on_writing_style_variant(caplog):
    """CP-7.5-3 ③：`-m` 形态重跑同一步 → **有 WARNING，且这是设计内命中不是缺陷**。

    dev-plan §56.1 的真跑物证：5 次失败全是 `python scripts/<name>.py`，紧随其后成功的
    全是 `python -m scripts.<name>`（agent 遇 sys.path 问题后统一改用 `-m` 绕过）。
    **首版方案曾打算用命令字符串比对来算完成度，那会把这些步骤判成"未完成"——即便活全
    干完了**（已作废的 R-S7-61）。现在改为采信自报下标：判定不受影响，写法差异只留痕。
    """
    steps = _steps("python scripts/summarize.py --out outputs")
    runs = [_run(["python", "-m", "scripts.summarize", "--out", "outputs"])]
    ledger = [(0, ["python", "-m", "scripts.summarize", "--out", "outputs"], 0)]
    with caplog.at_level(logging.WARNING):
        execution_module._audit_declared_steps(steps, runs, ledger)
    assert _audit_warnings(caplog), "写法变通必须被观测到（只留痕、不判红）"
    # 而完成度照常算作"跑完了"——这才是本批要的行为。
    recon = execution_module._reconcile_steps(steps, runs, ledger)
    assert recon["completed"] == 1
    assert execution_module._completion_insufficient(recon) is False


@pytest.mark.parametrize(
    "steps, ledger",
    [
        (_steps("cd subdir"), [(0, ["python", "whatever.py"], 0)]),   # 该步无可比对命令
        (_steps("python train.py"), [(-1, ["python", "other.py"], 0)]),  # 未声明
        (_steps("python train.py"), [(7, ["python", "other.py"], 0)]),   # 越界（另有告警）
        (_steps("python train.py"), []),                                  # 无台账
    ],
    ids=["no_command_step", "undeclared", "out_of_range", "empty_ledger"],
)
def test_da_4_cp_7_5_3_audit_silent_on_unjudgeable_cases(caplog, steps, ledger):
    """CP-7.5-3 ④：无从比对的四类情形一律不打 WARNING（避免噪声淹没真信号）。"""
    runs = [_run(list(entry[1])) for entry in ledger] or [_run(["python", "other.py"])]
    with caplog.at_level(logging.WARNING):
        execution_module._audit_declared_steps(steps, runs, ledger)
    assert _audit_warnings(caplog) == []


def test_da_4_cp_7_5_4_audit_is_pure_observation(monkeypatch, caplog):
    """CP-7.5-4 ★ 纯观测守门：打桩使留痕函数疯狂报不符 → 判定与 feedback 一字不变。

    这条守的是本批的一条红线：防伪留痕**只留痕**。一旦有人顺手把它的结论接进判定，
    "写法变通"就会重新变成判红的理由——那正是被砍掉的那条路（§49.0 变更 1）。
    """
    calls = {"n": 0}

    def loud_audit(plan_steps, run_results, step_ledger=None):
        calls["n"] += 1
        logging.getLogger("core.nodes.execution").warning(
            "[execution] 步骤自报与实际执行不符 999 条（打桩）",
        )
        return ["mismatch"] * 999  # 即使返回了东西，也不得被任何人消费

    monkeypatch.setattr(execution_module, "_audit_declared_steps", loud_audit)
    steps = _steps("python train.py")
    _patch_agent(monkeypatch, _agent_out(
        [_run(["python", "train.py"], stdout=_METRICS_LINE)],
        [(0, ["python", "train.py"], 0)],
    ))
    with caplog.at_level(logging.WARNING):
        out = execution(_base_state(steps))

    assert calls["n"] == 1, "留痕函数应在主流程被调用一次"
    er = out["execution_result"]
    assert er["success"] is True, "留痕结论绝不得影响 success"
    assert er["errors"] == []
    assert set(er.keys()) == {
        "success", "metrics", "logs", "errors", "artifacts", "runtime_seconds",
        "environment_info", "step_reconciliation", "budget_truncated",
        "metrics_groups", "degraded_credentials",
    }, "本批零新增 schema 键（§49.0 变更 1）"


def test_da_4_cp_7_5_5_audit_masks_secrets(caplog, monkeypatch):
    """CP-7.5-5 脱敏：留痕日志里的命令过 mask_value（命令可能内嵌 token）。"""
    token = "ghp_s711_secret_token_abcdefghij"
    secrets_store.register_sensitive_value(token)
    steps = _steps("python train.py")
    cmd = ["python", "fetch.py", f"--token={token}"]
    with caplog.at_level(logging.WARNING):
        execution_module._audit_declared_steps(steps, [_run(cmd)], [(0, cmd, 0)])
    msgs = _audit_warnings(caplog)
    assert msgs, "命令不符必须留痕"
    assert token not in msgs[0], "留痕日志不得出现凭证明文"


# ===========================================================================
# DA-S7-11-5/6/7 / CP-7.6-*：判定接线 + 改判 + 路由
# ===========================================================================


@pytest.mark.parametrize(
    "has_metrics, completion_ok, expected_category, expected_success",
    [
        (True, True, ErrorCategory.NONE, True),
        (True, False, ErrorCategory.INCOMPLETE_EXECUTION, False),
        (False, False, ErrorCategory.INCOMPLETE_EXECUTION, False),
        (False, True, ErrorCategory.NO_METRICS, False),
    ],
    ids=["metrics_complete", "metrics_incomplete", "nometrics_incomplete", "nometrics_complete"],
)
def test_da_7_cp_7_6_1_four_cell_truth_table(
    monkeypatch, has_metrics, completion_ok, expected_category, expected_success,
):
    """CP-7.6-1 ★ 四格真值表（exit 全 0 前提）。

    第三格是关键：**没跑完且没指标 → 报"步骤没跑完"（真因）而不是"未产出指标"（果）**。
    优先级不是写死的 if-else，而是**靠调用顺序拿**：`_apply_incomplete_execution` 排在
    `_apply_no_metrics` 上游，后者的 `category == NONE` 前置守卫使它自动让位 ⇒
    `_apply_no_metrics` 函数体一行不改（Q-S7-30）。
    """
    steps = _steps("python a.py", "python b.py")
    ran = ["python a.py", "python b.py"] if completion_ok else ["python a.py"]
    runs = [
        _run(c.split(), stdout=_METRICS_LINE if (has_metrics and i == 0) else "")
        for i, c in enumerate(ran)
    ]
    ledger = [(i, c.split(), 0) for i, c in enumerate(ran)]
    _patch_agent(monkeypatch, _agent_out(runs, ledger))
    out = execution(_base_state(steps))

    er = out["execution_result"]
    assert er["success"] is expected_success
    if expected_category is ErrorCategory.NONE:
        assert er["errors"] == []
    else:
        assert f"[error_category={expected_category.value}]" in er["errors"][0]


def _stub_predicate(monkeypatch, value: bool) -> None:
    monkeypatch.setattr(
        execution_module, "_completion_insufficient", lambda recon: value,
    )


@pytest.mark.parametrize("stubbed", [True, False])
def test_da_5_cp_7_6_2_single_predicate_drives_both_success_and_feedback(
    monkeypatch, stubbed,
):
    """CP-7.6-2 ★ 单点谓词守门（**本批最重要的一条**）。

    打桩这一个谓词，`success` 与 feedback 改判必须**同时**翻转。它拦的是"改判了但
    success 还是 True"这种最隐蔽的假绿——若哪天有人在 `_build_execution_result` 里另
    写一遍 `completed < planned` 比较，两处口径就会各自漂移，而本用例会当场变红。
    """
    steps = _steps("python a.py")
    runs = [_run(["python", "a.py"], stdout=_METRICS_LINE)]
    _patch_agent(monkeypatch, _agent_out(runs, [(0, ["python", "a.py"], 0)]))
    _stub_predicate(monkeypatch, stubbed)
    er = execution(_base_state(steps))["execution_result"]

    assert er["success"] is (not stubbed)
    if stubbed:
        assert "[error_category=incomplete_execution]" in er["errors"][0]
    else:
        assert er["errors"] == []


def test_da_6_cp_7_6_3_incomplete_routes_back_to_coding(monkeypatch):
    """CP-7.6-3 ★ 修法 D 的命门：完成度不足 → **回修复循环**，不是打断用户。

    第四层根因（dev-plan §56 P-42）：`success=False` 并不等于"回修复循环"。路由要求
    `feedback.auto_fixable` 为真才回 coding，而"全部 exit 0 + 有指标"这条路径上
    feedback 恒为 `ErrorCategory.NONE`（auto_fixable=False）⇒ **只收严 success 而不配套
    改判，会把 Maria 拍板的"交修复循环继续补跑"落成"直接打断用户"，设计意图落反。**
    """
    steps = _steps("python a.py", "python b.py")
    runs = [_run(["python", "a.py"], stdout=_METRICS_LINE)]
    _patch_agent(monkeypatch, _agent_out(runs, [(0, ["python", "a.py"], 0)]))
    out = execution(_base_state(steps))

    assert out["execution_result"]["success"] is False
    assert out["_dev_loop_route"] == execution_module._ROUTE_RETRY_CODING
    assert out["fix_loop_count"] == 1
    record = out["fix_loop_history"][-1]
    assert record["error_category"] == "incomplete_execution"
    assert record["error_summary"]
    assert "__interrupt__" not in out


def test_da_6_cp_7_6_3_user_facing_summary_is_plain_chinese(monkeypatch):
    """CP-7.6-3 配套：改判文案直达用户（UI 修复历程折叠条）⇒ 零内部标识符。"""
    steps = _steps("python a.py", "python b.py", "python c.py")
    runs = [_run(["python", "a.py"], stdout=_METRICS_LINE)]
    _patch_agent(monkeypatch, _agent_out(runs, [(0, ["python", "a.py"], 0)]))
    summary = execution(_base_state(steps))["fix_loop_history"][-1]["error_summary"]

    assert "已跑完 1/3 步" in summary
    assert "第 2 步" in summary and "第 3 步" in summary
    for banned in ("incomplete_execution", "step_index", "completed", "planned",
                   "run_results", "execution_steps", "检查指标输出"):
        assert banned not in summary


def test_da_6_cp_7_6_5_guard_reentry_round_trip(monkeypatch):
    """CP-7.6-5：落盘 errors 前缀经 `_feedback_from_committed_result` round-trip 还原。

    证明该函数**零改动即正确**（它按 `ErrorCategory(raw)` + `in AUTO_FIXABLE` 重建）。
    """
    fb = execution_module._feedback_from_committed_result({
        "errors": ["[error_category=incomplete_execution] 命令都正常结束了，但计划里的步骤没跑完（已跑完 1/3 步）"],
        "success": False,
    })
    assert fb.category is ErrorCategory.INCOMPLETE_EXECUTION
    assert fb.auto_fixable is True


def test_da_6_cp_7_6_6_no_metrics_early_stop_not_triggered():
    """CP-7.6-6：连续多轮 INCOMPLETE 不触发 NO_METRICS 早停。

    这正是**不复用 NO_METRICS 分类**的第一条理由（Q-S7-29）：复用会让"正在补跑"被
    `_no_metrics_stalled` 当成"无进展"提前打断用户。
    """
    state = {
        "fix_loop_history": [
            {"error_category": "incomplete_execution"} for _ in range(3)
        ],
    }
    fb = ExecutionFeedback(
        ErrorCategory.INCOMPLETE_EXECUTION, True, "没跑完", "补跑", "",
    )
    assert execution_module._no_metrics_stalled(state, fb) is False


def test_da_6_cp_7_6_7_three_mapping_points_need_no_change():
    """CP-7.6-7：三处映射点对新分类零改动即正确。"""
    assert ErrorCategory.INCOMPLETE_EXECUTION in execution_module.AUTO_FIXABLE
    assert execution_module._map_category_to_error_type(
        ErrorCategory.INCOMPLETE_EXECUTION,
    ) == "transient"


def test_da_7_cp_7_6_8_apply_no_metrics_untouched_and_returns_as_is():
    """CP-7.6-8：`_apply_no_metrics` 在 category 已被改判后原样返回（前置守卫生效）。

    这是"优先级靠调用顺序拿、函数体一行不改"的直接证明。
    """
    incomplete = ExecutionFeedback(
        ErrorCategory.INCOMPLETE_EXECUTION, True, "没跑完", "补跑", "",
    )
    out = execution_module._apply_no_metrics(incomplete, {}, {}, True)
    assert out is incomplete


def test_da_7_cp_7_6_1_apply_incomplete_returns_as_is_outside_conditions():
    """CP-7.6-1 边界：非 exit_ok / 已有别的分类 / 完成度充足 → 原样返回。"""
    none_fb = ExecutionFeedback(ErrorCategory.NONE, False, "执行成功", "", "")
    runtime_fb = ExecutionFeedback(ErrorCategory.RUNTIME, True, "崩了", "查", "")
    apply = execution_module._apply_incomplete_execution
    assert apply(none_fb, _recon(9, 2), False) is none_fb      # exit 非全 0
    assert apply(runtime_fb, _recon(9, 2), True) is runtime_fb  # 已有真错误，别抢
    assert apply(none_fb, _recon(9, 9), True) is none_fb        # 跑完了
    assert apply(none_fb, {}, True) is none_fb                  # 旧 checkpoint


def test_da_7_incomplete_summary_has_no_step_list_when_attribution_unavailable():
    """`attribution_unavailable` 时 `unexecuted_steps` 恒空 ⇒ 文案不得凭空编造步骤名。"""
    fb = execution_module._apply_incomplete_execution(
        ExecutionFeedback(ErrorCategory.NONE, False, "执行成功", "", ""),
        _recon(9, 0, attribution_unavailable=True),
        True,
    )
    assert fb.category is ErrorCategory.INCOMPLETE_EXECUTION
    assert "已跑完 0/9 步" in fb.summary
    assert "还没跑的有" not in fb.summary


# ===========================================================================
# DA-S7-11-8 / CP-7.7-*：对外口径订正
# ===========================================================================


def test_da_8_cp_7_7_1_grade_label_gone_from_user_text():
    """CP-7.7-1："B 档"这个内部分档术语从用户可见文案里消失。

    它一直没被术语守门扫到，只是因为此前是内联 f-string、不在受控常量清单内
    （S7-06 同款失效模式）⇒ 本批顺手提为具名常量并登记进守门。
    """
    note = reporting_module._SUCCESS_CRITERIA_NOTE
    assert "B 档" not in note
    assert "档" not in note, "任何内部分档表述都不该出现在用户可见文案里"


def test_da_8_cp_7_7_2_three_conditions_stated():
    """CP-7.7-2：口径句写全三个条件，且"仅供参考对比"半句逐字保留。"""
    note = reporting_module._SUCCESS_CRITERIA_NOTE
    assert "退出码" in note
    assert "指标" in note
    assert "计划里的步骤全部跑完" in note, "第三个条件必须写出来，否则文档与实现不符"
    assert "仅供参考对比" in note and "**不做硬性结论判定**" in note


def test_da_8_cp_7_7_5_umap_shaped_report_is_not_full_success(tmp_path):
    """CP-7.7-5 ★ "UMAP 那份自相矛盾的报告不会再出现"的直接证明。

    真跑现场：planned=9 / completed=2 / 指标非空 / exit 全 0 ⇒ 旧口径判 `success=true`、
    报告形态 `full_success`，同一份报告顶部却印着"已完成 2/9 步"。新口径下 success 为
    假，报告不再落进 full_success 形态，横幅照常印数字。
    """
    exec_result = {
        "success": False,
        "metrics": {"best_knn_accuracy": 0.8303},
        "logs": "", "errors": [
            "[error_category=incomplete_execution] 命令都正常结束了，但计划里的步骤没跑完（已跑完 2/9 步）",
        ],
        "artifacts": [], "runtime_seconds": 0.243, "environment_info": {},
        "step_reconciliation": {
            "planned": 9, "executed": 2, "completed": 2,
            "unexecuted_steps": [
                {"index": i, "step_name": f"第 {i + 1} 步"} for i in range(7)
            ],
            "extra_commands": [], "attribution_unavailable": False,
        },
        "budget_truncated": False, "metrics_groups": {}, "degraded_credentials": [],
    }
    conclusion = reporting_module._determine_conclusion(
        {"reproduction_plan": {"execution_steps": []}}, exec_result, None,
    )
    assert conclusion["level"] == "none", "少跑步骤不得再评为'代码跑通'"
    assert "incomplete_execution" in conclusion["annotations"]


def test_da_8_success_and_conclusion_stay_aligned(monkeypatch):
    """判定与结论同向：completed<planned 时 success 假、结论不为 engineering。"""
    steps = _steps("python a.py", "python b.py")
    runs = [_run(["python", "a.py"], stdout=_METRICS_LINE)]
    _patch_agent(monkeypatch, _agent_out(runs, [(0, ["python", "a.py"], 0)]))
    er = execution(_base_state(steps))["execution_result"]
    conclusion = reporting_module._determine_conclusion(
        {"reproduction_plan": {"execution_steps": steps}}, er, None,
    )
    assert er["success"] is False
    assert conclusion["level"] != "engineering"


# ===========================================================================
# DA-S7-11-9：四条修法同批落地的机制化守门
# ===========================================================================


def test_da_9_all_four_fixes_landed_together(monkeypatch):
    """DA-S7-11-9：A/B/C/D 四条**必须同批生效**（R-S7-60），缺任一条当场红。

    为什么必须连坐（dev-plan §49.3 首条红线）：
      - 只上 C 不上 D ⇒ 完成度不足直接 interrupt#2 打断用户，**设计意图落反**；
      - 只上 C 不上 B ⇒ agent 只补跑缺失步骤，判定层**必然死锁**、烧满 20 轮修复；
      - 只上 A/B 不上 C ⇒ 反向激励原封不动。
    ⚠ 四条臂一律写成**真调生产路径的行为断言**，禁源码子串检查（S7-10 的 F2 教训）。
    """
    # 臂 A：修复轮上下文真的带上了"上一轮改了哪些文件"。
    fix_state = _base_state(
        _steps("python a.py"),
        fix_loop_count=1,
        execution_result={"errors": ["boom"], "logs": ""},
        last_fix_note="补了 sys.path",
        last_files_written=["/w/code/a.py"],
    )
    payload = execution_module._build_execution_agent_context(
        fix_state, "/w/code", fix_state["reproduction_plan"],
    )
    assert payload["last_fix"]["files"] == ["a.py"], "修法 A 未落地"

    # 臂 B：提示词要求每回合全量重跑（判定层的正确性硬挂在这条上）。
    assert "从 execution_steps 的第一步开始按顺序全量重跑" in _body(), "修法 B 未落地"

    # 臂 C + D：少跑一步 → 判不成功（C），且**回 coding 而不是打断用户**（D）。
    steps = _steps("python a.py", "python b.py")
    runs = [_run(["python", "a.py"], stdout=_METRICS_LINE)]
    _patch_agent(monkeypatch, _agent_out(runs, [(0, ["python", "a.py"], 0)]))
    out = execution(_base_state(steps))
    assert out["execution_result"]["success"] is False, "修法 C 未落地"
    assert out["_dev_loop_route"] == execution_module._ROUTE_RETRY_CODING, "修法 D 未落地"


def test_da_9_zero_schema_growth():
    """§49.0 变更 1 的机制化守门：本批**零新增 state / schema 键**。

    首版方案要给 `ExecutionResult` 加一个 `completion` 字段承载新算法的产出；随确定性
    算法一并删除后，完成度唯一取自既有的 `step_reconciliation` ⇒ 键集合一字不变。
    """
    from core.state import ExecutionResult

    assert set(ExecutionResult.__annotations__) == {
        "success", "metrics", "logs", "errors", "artifacts", "runtime_seconds",
        "environment_info", "step_reconciliation", "budget_truncated",
        "metrics_groups", "degraded_credentials",
    }
    assert not hasattr(execution_module, "_deterministic_completion"), (
        "确定性完成度算法已被 Maria 复审砍掉（§49.0 变更 1），不得悄悄回来"
    )
