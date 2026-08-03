"""Sprint 7 需求 S7-13（批次 9 / T-S7-9-1）——**正式测试**（测试工程师代理，2026-08-02）。

开发侧只做了 `/tmp` 自测脚本（仓库零触碰），`tests/` 下**一条用例都没有**——
改前改后全量回归逐格相同（2494 passed）恰恰证明**本批防线覆盖率为零**（S7-12 同款情形）。
本文件是该批的正式防线，按"这条断言如果没了会漏掉什么"分七区：

    A. ★★ 主指标门控 —— 本批最要紧的一条自律。没有它，`success` 的"至少 1 个指标"
       合取项**分子会变成 agent 自报**：生产代码一个字没改，判定语义却被悄悄换掉。
    B. `metrics_groups` 三方关系 —— agent 汇报优先 / 磁盘扫描兜底 / **禁止合并**，
       且零汇报时行为必须与本批之前**逐字节相同**。
    C. `_split_reported_metrics` / `_coerce_reported_value` —— 它在 execution 节点
       主流程上，**抛异常会炸掉整个节点**，所以畸形输入的"恒不抛"是硬约束。
    D. schema 与装配 —— `result_schema` 真的传进去了（断行为不断源码子串，S7-10 F2
       教训）；`required` 不含 `metrics`（否则每个零指标回合白烧一次账外 LLM 调用）；
       schema 不含 `source`（2026-08-02 Maria 拍板砍除）。
    E. `expected_results` 注入 —— 不注入则"用计划写法"整条约束落空；且**无该键的
       计划下 payload 必须与基线字节零扰动**。
    F. 零改动红线 —— 十个函数在本批中一字未改，用可执行断言钉住。
    G. ★ 2026-08-02 第二次真跑重放（离线夹具，`tests/fixtures/s713_realrun_20260802/`）
       —— 现场证据抄自 `/data/myproj/.umap_evidence/20260802-233011/`（会被下次真跑
       覆盖）。本区除了复现"链路通了"，还钉住**两条与交付表述相左的事实**，见 G 区抬头。

全离线（mock agent + tmp_path + 已抄走的真跑夹具），零 API 配额、零网络、零真跑。
**不修改任何生产代码。**

⚠ 取模块一律 `importlib.import_module`：`core/nodes/__init__.py` 显式 export 会遮蔽
子模块（dev-plan §63 P-67 实证：`import core.nodes.execution as ex` 拿到的是 callable）。
"""

from __future__ import annotations

import copy
import hashlib
import importlib
import inspect
import json
import logging
from pathlib import Path
from typing import Any, Dict, List

import pytest

import config
from core import secrets_store
from core.state import ExecutionMode

execution_module = importlib.import_module("core.nodes.execution")
reporting_module = importlib.import_module("core.nodes.reporting")
react_base_module = importlib.import_module("core.react_base")

from core.nodes.execution import execution  # noqa: E402
from sandbox.local_venv import SandboxPrepareResult, SandboxRunResult  # noqa: E402

ExecAgentOutput = execution_module.ExecAgentOutput

_FIXTURES = Path(__file__).parent / "fixtures" / "s713_realrun_20260802"

# 主通道（档 1）能解析出一个指标的 stdout；用它做"主通道非空"的前提。
_METRICS_LINE = '<METRICS>{"acc": 0.9}</METRICS>'


# ===========================================================================
# fixtures / helpers（体例沿 tests/test_sprint7_s711_gap_audit.py）
# ===========================================================================


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


_ABSENT = object()  # "这个构造点根本不传该字段"，区别于"传了一个空值"


def _agent_out(
    runs,
    ledger=None,
    rounds: int = 2,
    reported: Any = _ABSENT,
    **kwargs: Any,
) -> ExecAgentOutput:
    """构造 ExecAgentOutput。

    ``reported=_ABSENT`` 时**刻意不传** ``reported_metrics``——走 dataclass 默认值，
    这正是"本批之前的构造形态"（B 区的字节零扰动断言依赖这一点）。
    其余情况**原样传入**（不做 ``list()`` 包装），才能覆盖"字段被塞了畸形值"的形态。
    """
    if reported is not _ABSENT:
        kwargs["reported_metrics"] = reported
    return ExecAgentOutput(
        prep=_prep(), run_results=list(runs), rounds_used=rounds, llm_calls=rounds,
        step_ledger=list(ledger or []), **kwargs,
    )


def _patch_agent(monkeypatch, out: ExecAgentOutput) -> None:
    monkeypatch.setattr(
        execution_module, "_run_execution_agent", lambda state, wd, plan: out,
    )


def _state(steps: List[Any], work_dir: str = "/tmp/s713-workdir", **overrides: Any) -> Dict[str, Any]:
    st: Dict[str, Any] = {
        "llm_config_set": {"default": {"model": "test"}},
        "code_output_dir": work_dir,
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


_ONE_STEP = [{"step_name": "训练", "command": "python train.py"}]
_ONE_LEDGER = [(0, ["python", "train.py"], 0)]


def _drive(
    monkeypatch,
    *,
    stdout: str = "",
    reported: Any = _ABSENT,
    work_dir: str = "/tmp/s713-workdir",
) -> Dict[str, Any]:
    """一次"完全照做"的执行：唯一计划步骤跑完、exit 0、诚实声明归属。

    只有 ``stdout``（决定主通道有没有指标）与 ``reported``（agent 自报）两个变量，
    其余前置条件全部固定 ⇒ 结果差异**只能**归因于这两个变量。
    """
    runs = [_run(["python", "train.py"], stdout=stdout)]
    _patch_agent(monkeypatch, _agent_out(runs, _ONE_LEDGER, reported=reported))
    return execution(_state(_ONE_STEP, work_dir=work_dir))


def _warnings(caplog) -> List[str]:
    return [
        r.getMessage() for r in caplog.records
        if r.levelno >= logging.WARNING and r.name == execution_module.__name__
    ]


def _load_fixture(name: str) -> Any:
    return json.loads((_FIXTURES / name).read_text(encoding="utf-8"))


# ===========================================================================
# A 区 ★★ 主指标门控（execution.py 步骤 4.4，`elif reported_main:` 那一支）
# ===========================================================================
#
# 为什么这是本批最要紧的一条：`success` 的三合取项之一是 `len(metrics) >= 1`
# （`_build_execution_result:2430`）。若主通道零指标时采信 agent 自报，那么这条
# 合取项的**分子来源**就从"真实 stdout 解析"变成了"agent 说它跑出来了"——
# `_build_execution_result` 一个字没改，判定语义却被换掉。这与 S7-11 立项时那类
# 反向激励同型，且**在 mock 层就能证伪**，不需要真跑。


def test_a1_zero_parsed_metrics_never_adopts_self_report(monkeypatch, caplog):
    """★★ 主通道零指标 + agent 自报 1 个主实验指标 ⇒ 不采信、success 仍为 False。

    前置条件刻意做满："exit 全 0 + 计划步骤全部跑完 + 归属诚实"——三合取项里
    另外两项都成立，**唯一缺的就是指标**。所以 success 的取值完全由"采不采信自报"
    决定，这一格就是门控的活体证明。
    """
    with caplog.at_level(logging.WARNING):
        out = _drive(
            monkeypatch,
            stdout="没有任何 METRICS 标签",
            reported=[{"name": "best_knn_accuracy", "value": 0.98, "group": None}],
        )
    er = out["execution_result"]

    # 前置条件成立（否则这条测的就不是门控了）。
    assert er["step_reconciliation"]["completed"] == 1
    assert er["step_reconciliation"]["planned_actionable"] == 1

    assert er["metrics"] == {}, "主通道零指标时 agent 自报的主实验指标一个都不得进 metrics"
    assert er["success"] is False, (
        "success 的指标分子只认真实 stdout 解析；采信自报 = 判定语义被悄悄换掉"
    )
    assert any("不采信" in m for m in _warnings(caplog)), "不采信必须留痕，不得静默丢弃"


def test_a1b_control_group_one_parsed_metric_flips_the_same_run_to_success(monkeypatch):
    """阴性对照：同一夹具只把主通道从"零指标"换成"1 个指标" ⇒ success 变 True。

    有这条对照，A1 的 `success is False` 才能归因到**门控**，而不是夹具里别的东西
    （比如步骤没跑完、exit 非 0）把它压红了。
    """
    out = _drive(
        monkeypatch,
        stdout=_METRICS_LINE,
        reported=[{"name": "best_knn_accuracy", "value": 0.98, "group": None}],
    )
    assert out["execution_result"]["success"] is True


@pytest.mark.parametrize("reported_count", [1, 5, 20])
def test_a2_success_numerator_never_comes_from_self_report(monkeypatch, reported_count):
    """自报多少个主实验指标都不改变结论：主通道零 ⇒ metrics 恒空、success 恒 False。

    参数化到 20 条是为了排除"少量时不采信、多了就采信"这类阈值型退化。
    """
    reported = [
        {"name": f"metric_{i}", "value": float(i), "group": None}
        for i in range(reported_count)
    ]
    out = _drive(monkeypatch, stdout="", reported=reported)
    assert out["execution_result"]["metrics"] == {}
    assert out["execution_result"]["success"] is False


def test_a3_parsed_value_wins_on_duplicate_key(monkeypatch):
    """合并方向：`{**自报, **解析}` ⇒ **真实解析值优先**，同名键自报不得覆盖。

    ⚠ 这条断言必须用**异值**（44.81 vs 999.0）：开发自查登记的 P-72 就是栽在
    "fixture 里两边取值恰好相同"上——那样即便把合并方向反转过来也不会变红。
    """
    out = _drive(
        monkeypatch,
        stdout='<METRICS>{"mean_timing_seconds": 44.81}</METRICS>',
        reported=[{"name": "mean_timing_seconds", "value": 999.0, "group": None}],
    )
    assert out["execution_result"]["metrics"]["mean_timing_seconds"] == 44.81, (
        "同名键必须是真实 stdout 解析值胜出；反转合并方向这条当场红"
    )


def test_a4_self_report_fills_only_the_keys_the_main_channel_missed(monkeypatch):
    """主通道非空时，自报补进它没解析到的键——这正是"主指标被吞"的补救路径。"""
    out = _drive(
        monkeypatch,
        stdout='<METRICS>{"mean_timing_seconds": 44.81}</METRICS>',
        reported=[
            {"name": "best_knn_accuracy", "value": 0.987, "group": None},
            {"name": "mean_timing_seconds", "value": 999.0, "group": None},
        ],
    )
    metrics = out["execution_result"]["metrics"]
    assert metrics == {"best_knn_accuracy": 0.987, "mean_timing_seconds": 44.81}


def test_a5_gate_warning_reports_the_true_count(monkeypatch, caplog):
    """不采信的 WARNING 必须写出真实条数（3 条就是 3 条），便于事后从日志复算。"""
    reported = [
        {"name": "a", "value": 1, "group": None},
        {"name": "b", "value": 2, "group": ""},
        {"name": "c", "value": 3},
    ]
    with caplog.at_level(logging.WARNING):
        _drive(monkeypatch, stdout="", reported=reported)
    gate = [m for m in _warnings(caplog) if "不采信" in m]
    assert len(gate) == 1, "门控只打一条 WARNING"
    assert "3" in gate[0], f"条数须如实：{gate[0]}"


def test_a6_no_gate_warning_when_agent_reports_nothing(monkeypatch, caplog):
    """零汇报是合法常态（跑失败 / 只跑了 prepare）——不得制造噪声 WARNING。"""
    with caplog.at_level(logging.WARNING):
        _drive(monkeypatch, stdout="", reported=[])
    assert [m for m in _warnings(caplog) if "不采信" in m] == []


def test_a7_grouped_only_report_does_not_touch_the_main_channel(monkeypatch):
    """只报分组指标（`group` 全非空）时，主通道的门控分支不该被惊动。

    这一格来自 2026-08-02 真跑现场的真实形态：agent 报的 24 条**全部带 group**，
    `reported_main` 为空 dict ⇒ 既不合并也不打"不采信"。
    """
    out = _drive(
        monkeypatch,
        stdout="",
        reported=[{"name": "k-NN classifier accuracy", "value": 0.62, "group": "UMAP"}],
    )
    er = out["execution_result"]
    assert er["metrics"] == {}
    assert er["metrics_groups"] == {"UMAP": {"k-NN classifier accuracy": 0.62}}
    assert er["success"] is False, "分组指标不是 success 的指标分子（合取项数的是 metrics）"


# ===========================================================================
# B 区：metrics_groups 三方关系（agent 优先 / 磁盘兜底 / 禁止合并）
# ===========================================================================


def _make_disk_groups(tmp_path, groups: Dict[str, Dict[str, Any]]) -> str:
    """在真实磁盘上摆出 `<work_dir>/outputs/<组名>/summary.json`（不 mock 扫描函数）。"""
    work = tmp_path / "code"
    for name, fields in groups.items():
        d = work / "outputs" / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "summary.json").write_text(json.dumps(fields), encoding="utf-8")
    work.mkdir(parents=True, exist_ok=True)
    return str(work)


def test_b1_agent_report_wins_and_disk_group_names_do_not_leak(monkeypatch, tmp_path):
    """agent 有汇报 ⇒ metrics_groups 取 agent，磁盘上的组名**一个都不掺入**。"""
    work_dir = _make_disk_groups(tmp_path, {"legacy_group": {"disk_metric": 1.5}})
    out = _drive(
        monkeypatch,
        stdout=_METRICS_LINE,
        reported=[
            {"name": "k-NN classifier accuracy", "value": 0.62, "group": "UMAP"},
            {"name": "runtime", "value": 0.65, "group": "UMAP"},
            {"name": "k-NN classifier accuracy", "value": 0.27, "group": "PCA"},
        ],
        work_dir=work_dir,
    )
    groups = out["execution_result"]["metrics_groups"]
    assert set(groups) == {"UMAP", "PCA"}
    assert "legacy_group" not in groups, "禁止合并：磁盘组名不得掺入 agent 汇报"
    assert groups["UMAP"] == {"k-NN classifier accuracy": 0.62, "runtime": 0.65}


def test_b2_disk_scan_is_not_even_called_when_agent_reports(monkeypatch, tmp_path):
    """`reported_groups or _collect_grouped_metrics(...)` 的**短路**必须成立。

    断的是"磁盘扫描根本没跑"这个行为，而不是"结果里没有磁盘组名"——后者在
    "扫了但结果被覆盖"的实现下同样能过，等于放行了一次无谓的全盘 rglob。
    """
    work_dir = _make_disk_groups(tmp_path, {"legacy_group": {"disk_metric": 1.5}})
    calls: List[str] = []

    def _boom(wd):
        calls.append(wd)
        raise AssertionError("agent 已汇报时不得再扫磁盘")

    monkeypatch.setattr(execution_module, "_collect_grouped_metrics", _boom)
    out = _drive(
        monkeypatch,
        stdout=_METRICS_LINE,
        reported=[{"name": "acc", "value": 0.62, "group": "UMAP"}],
        work_dir=work_dir,
    )
    assert calls == []
    assert out["execution_result"]["metrics_groups"] == {"UMAP": {"acc": 0.62}}


def test_b3_zero_report_falls_back_to_disk_scan(monkeypatch, tmp_path):
    """agent 一组都没报 ⇒ 回落磁盘扫描，逐字节等于 `_collect_grouped_metrics` 的产出。"""
    work_dir = _make_disk_groups(tmp_path, {"legacy_group": {"disk_metric": 1.5}})
    out = _drive(monkeypatch, stdout=_METRICS_LINE, reported=[], work_dir=work_dir)
    assert out["execution_result"]["metrics_groups"] == execution_module._collect_grouped_metrics(
        work_dir
    ) == {"legacy_group": {"disk_metric": 1.5}}


def _projection(out: Dict[str, Any]) -> str:
    """节点输出中与判定 / 落盘 / 路由有关的部分（剔除易变字段）。

    比"只看 metrics_groups"强：只要零汇报路径在任何一处与本批之前有出入
    （success / errors / 对账 / 路由 / 分类前缀），这份投影就会变。
    """
    er = dict(out.get("execution_result") or {})
    er.pop("runtime_seconds", None)
    er.pop("logs", None)
    return json.dumps(
        {
            "execution_result": er,
            "_dev_loop_route": out.get("_dev_loop_route"),
            "node_errors": out.get("node_errors"),
            "current_step": out.get("current_step"),
        },
        sort_keys=True, ensure_ascii=False, default=str,
    )


@pytest.mark.parametrize(
    "reported", [_ABSENT, [], None, "不是数组", {"metrics": 1}, 0],
    ids=["field_absent", "empty_list", "none", "str", "dict", "zero"],
)
def test_b4_zero_report_output_is_byte_identical_to_pre_batch(monkeypatch, tmp_path, reported):
    """★ 零汇报（含各种"等价于没报"的畸形值）⇒ 节点输出与本批之前**逐字节相同**。

    基线取 ``reported=_ABSENT``——那一支**刻意不给 `ExecAgentOutput` 传
    `reported_metrics`**，走 dataclass 默认值，就是本批之前的构造形态。
    ⇒ 本条同时守住"旧 checkpoint / 子图降级时零退化"这条承诺。
    """
    work_dir = _make_disk_groups(tmp_path, {"legacy_group": {"disk_metric": 1.5}})
    baseline = _projection(
        _drive(monkeypatch, stdout=_METRICS_LINE, reported=_ABSENT, work_dir=work_dir)
    )
    actual = _projection(
        _drive(monkeypatch, stdout=_METRICS_LINE, reported=reported, work_dir=work_dir)
    )
    assert actual == baseline


def test_b5_merging_two_sources_would_break_verification():
    """"禁止合并"的机制反证：合并后 `_match_metrics_group` 判歧义返 None。

    用 2026-08-01 那次真跑的磁盘组名形态（dev-plan §60.2 事实 12 实测：
    `{"baselines/laplacian_eigenmaps","baselines/pca","baselines/tsne","umap"}`）——
    磁盘 `umap` 与 agent `UMAP` 归一后同为 `umap`，精确匹配命中 2 条 ⇒ 歧义 ⇒ None
    ⇒ 本来能判"符合"的那条退回"未验证"。**合并比不合并更差，不是取舍。**
    """
    agent = {
        "UMAP": {"k-NN classifier accuracy": 0.98},
        "PCA": {"k-NN classifier accuracy": 0.54},
    }
    disk = {"umap": {}, "baselines/pca": {}, "baselines/tsne": {}}
    merged = {**disk, **agent}

    assert reporting_module._match_metrics_group("UMAP", agent) == "UMAP"
    assert reporting_module._match_metrics_group("UMAP", merged) is None, (
        "合并后 umap / UMAP 归一撞名 ⇒ 判歧义 ⇒ 匹配不上"
    )

    trend = {"metric": "k-NN classifier accuracy", "greater": "UMAP", "lesser": "PCA"}
    assert reporting_module._verify_trend(trend, agent) == "符合"
    assert reporting_module._verify_trend(trend, merged) == "未验证"


def test_b6_merge_harm_is_target_specific_not_universal():
    """★ 独立发现（如实登记）：合并之害是**靶相关**的，不是普遍成立的。

    dev-plan §60.6-订正 裁决 1 把"禁止合并"称为"硬证据"，证据取自 2026-08-01 那次
    真跑的磁盘组名（`umap`）。但 **2026-08-02 第二次真跑的磁盘组名换成了
    `data/COIL-20` / `data/MNIST` / `data/PenDigits` / `report`**（本文件夹具
    `disk_scan_groups.json` 实测），与 agent 组名一个都不撞 ⇒ **那一轮即便合并，
    5 条回验产出也完全不变**。

    ⇒ 结论不变（不合并仍是对的：它零成本地消除了一整类撞名风险），但**理由要说准**：
    "合并会打坏回验"在某些靶上成立、在另一些靶上不成立。把靶相关的观测写成普遍
    结论，正是 dev-plan §63 P-61 / P-62 两次栽过的同一个跟头。
    """
    disk = _load_fixture("disk_scan_groups.json")
    agent_main, agent_groups = execution_module._split_reported_metrics(
        _load_fixture("agent_reported_metrics.json")
    )
    expected = _load_fixture("expected_results.json")

    merged = {**disk, **agent_groups}
    not_merged = agent_groups
    verdicts_merged = [
        c["verdict"]
        for c in reporting_module._verify_expected_results(
            expected, {"metrics_groups": merged}
        )
    ]
    verdicts_plain = [
        c["verdict"]
        for c in reporting_module._verify_expected_results(
            expected, {"metrics_groups": not_merged}
        )
    ]
    assert verdicts_merged == verdicts_plain, (
        "2026-08-02 这一靶上合并与否结论相同 ⇒ '合并必然打坏回验'不是普遍事实"
    )
    assert agent_main == {}, "该轮 agent 一条主实验指标都没报（G 区详述）"


# ===========================================================================
# C 区：`_split_reported_metrics` / `_coerce_reported_value`
# ===========================================================================
#
# 它在 execution 节点主流程上（步骤 4.4，`execution()` 内无 try/except 包裹）
# ⇒ **抛异常 = 炸掉整个 execution 节点**。所以"畸形输入恒不抛"是硬约束，不是雅致。

@pytest.mark.parametrize(
    "item",
    [
        pytest.param({"name": "acc", "value": 1.0}, id="group_key_absent"),
        pytest.param({"name": "acc", "value": 1.0, "group": None}, id="group_null"),
        pytest.param({"name": "acc", "value": 1.0, "group": ""}, id="group_empty_str"),
        pytest.param({"name": "acc", "value": 1.0, "group": "   "}, id="group_blank"),
        pytest.param({"name": "acc", "value": 1.0, "group": "\t\n "}, id="group_whitespace"),
    ],
)
def test_c1_blank_group_goes_to_main_metrics(item):
    """`group` 空 / 缺省 / null / 纯空白 ⇒ 主实验指标（第一个返回值）。"""
    main, groups = execution_module._split_reported_metrics([item])
    assert main == {"acc": 1.0}
    assert groups == {}


def test_c2_non_empty_group_keeps_the_agent_wording_verbatim():
    """组名保持 agent 原文——大小写、连字符一个都不许改。

    `t-SNE` 这个写法是整个方案成立的关键：计划里写 `t-SNE`，agent 照抄 `t-SNE`，
    `reporting._match_metrics_group` 归一后才对得上。任何"顺手归一化一下"的改动
    都会把它打回 2026-08-01 那次"组名失配"的老路。
    """
    main, groups = execution_module._split_reported_metrics([
        {"name": "runtime", "value": 0.75, "group": "t-SNE"},
        {"name": "runtime", "value": 0.12, "group": "  PCA  "},
    ])
    assert main == {}
    assert sorted(groups) == ["PCA", "t-SNE"], "只去首尾空白，不做大小写/分隔符归一"


def test_c3_duplicate_same_key_keeps_the_first_value():
    """同一 (组, 名) 重复 ⇒ **保留首次出现值**，绝不后覆盖前。

    ⚠ 夹具刻意做成"三条值互不相同、且末条与首条不同"——开发自查 P-72 登记的
    两条无牙断言之一就是栽在"末条取值恰与首条相同"上，改成"后覆盖前"照样绿。
    这里额外断言末值**不**等于结果，把那条路堵死。
    """
    reported = [
        {"name": "acc", "value": 0.1, "group": "UMAP"},
        {"name": "acc", "value": 0.5, "group": "UMAP"},
        {"name": "acc", "value": 0.9, "group": "UMAP"},
    ]
    _main, groups = execution_module._split_reported_metrics(reported)
    assert groups["UMAP"]["acc"] == 0.1, "先到先得"
    assert groups["UMAP"]["acc"] != 0.9, "改成'后覆盖前'这条必须当场红"


def test_c3b_duplicate_in_main_bucket_also_keeps_first(caplog):
    """主实验桶（group 为空）同样先到先得，且冲突有 WARNING 留痕。"""
    with caplog.at_level(logging.WARNING):
        main, _groups = execution_module._split_reported_metrics([
            {"name": "acc", "value": 0.1},
            {"name": "acc", "value": 0.9},
        ])
    assert main == {"acc": 0.1}
    conflict = [m for m in _warnings(caplog) if "同名异值" in m]
    assert len(conflict) == 1 and "(主实验)" in conflict[0]


def test_c4_duplicate_with_identical_value_is_not_reported_as_conflict(caplog):
    """同名**同值**重复不算冲突——否则真跑日志会被无意义的 WARNING 淹掉。"""
    with caplog.at_level(logging.WARNING):
        _main, groups = execution_module._split_reported_metrics([
            {"name": "acc", "value": 0.5, "group": "UMAP"},
            {"name": "acc", "value": 0.5, "group": "UMAP"},
        ])
    assert groups == {"UMAP": {"acc": 0.5}}
    assert [m for m in _warnings(caplog) if "同名异值" in m] == []


_MALFORMED_ITEMS = [
    pytest.param("字符串条目", id="item_is_str"),
    pytest.param(123, id="item_is_int"),
    pytest.param(None, id="item_is_none"),
    pytest.param([], id="item_is_list"),
    pytest.param({}, id="empty_dict"),
    pytest.param({"value": 1.0}, id="name_missing"),
    pytest.param({"name": "", "value": 1.0}, id="name_empty"),
    pytest.param({"name": "   ", "value": 1.0}, id="name_blank"),
    pytest.param({"name": None, "value": 1.0}, id="name_none"),
    pytest.param({"name": "acc"}, id="value_missing"),
    pytest.param({"name": "acc", "value": None}, id="value_none"),
    pytest.param({"name": "acc", "value": {"a": 1}}, id="value_dict"),
    pytest.param({"name": "acc", "value": [1, 2]}, id="value_list"),
    pytest.param({"name": "acc", "value": "x" * 121}, id="value_str_too_long"),
    pytest.param({"name": "acc", "value": ""}, id="value_empty_str"),
    pytest.param({"name": "acc", "value": "   "}, id="value_blank_str"),
]


@pytest.mark.parametrize("item", _MALFORMED_ITEMS)
def test_c5_malformed_item_is_skipped_and_never_raises(item):
    """畸形条目一律跳过、**恒不抛异常**（它在节点主流程上，抛了就炸整个 execution）。"""
    main, groups = execution_module._split_reported_metrics([item])
    assert main == {} and groups == {}


@pytest.mark.parametrize("item", _MALFORMED_ITEMS)
def test_c5b_one_malformed_item_never_swallows_its_healthy_neighbours(item):
    """一条畸形不得连累同一数组里的健康条目（"整份丢弃"是本批要治的病之一）。"""
    main, groups = execution_module._split_reported_metrics([
        item, {"name": "acc", "value": 0.9, "group": "UMAP"},
    ])
    assert groups == {"UMAP": {"acc": 0.9}}
    assert main == {}


@pytest.mark.parametrize(
    "reported",
    [None, "", "不是数组", 0, 1, {"metrics": []}, {"name": "acc", "value": 1}, set(), 3.14, True],
    ids=[
        "none", "empty_str", "str", "int_zero", "int_one",
        "dict", "dict_shaped_like_item", "set", "float", "bool",
    ],
)
def test_c6_non_list_input_returns_empty_pair(reported):
    """`metrics` 整个不是数组（旧 checkpoint / 模型跑偏）⇒ 返回 ({}, {})，不抛。"""
    assert execution_module._split_reported_metrics(reported) == ({}, {})


def test_c7_skipped_items_warn_with_the_true_count(caplog):
    """畸形跳过必须打 WARNING 且条数如实（已知 bug 模式 #3：禁止静默吞错）。"""
    with caplog.at_level(logging.WARNING):
        execution_module._split_reported_metrics([
            "非对象", {"value": 1}, {"name": "acc", "value": {"a": 1}},
        ])
    skipped = [m for m in _warnings(caplog) if "畸形条目" in m]
    assert len(skipped) == 1
    assert "3" in skipped[0], f"条数须如实：{skipped[0]}"


def test_c8_output_is_sorted_and_byte_deterministic():
    """输出按组名 / 指标名 sorted，同一输入连跑三次 `json.dumps` 逐字节相同。"""
    reported = [
        {"name": "z_metric", "value": 1, "group": "B"},
        {"name": "m_metric", "value": 3, "group": "B"},
        {"name": "a_metric", "value": 2, "group": "A"},
        {"name": "b_main", "value": 4},
        {"name": "a_main", "value": 5},
    ]
    dumps = [
        json.dumps(execution_module._split_reported_metrics(copy.deepcopy(reported)),
                   ensure_ascii=False, sort_keys=False)
        for _ in range(3)
    ]
    assert dumps[0] == dumps[1] == dumps[2]
    main, groups = execution_module._split_reported_metrics(reported)
    assert list(main) == ["a_main", "b_main"]
    assert list(groups) == ["A", "B"]
    assert list(groups["B"]) == ["m_metric", "z_metric"]


@pytest.mark.parametrize(
    "raw, ok, coerced",
    [
        pytest.param(1, True, 1, id="int"),
        pytest.param(0, True, 0, id="int_zero"),
        pytest.param(-3, True, -3, id="int_negative"),
        pytest.param(0.5, True, 0.5, id="float"),
        pytest.param(True, True, True, id="bool_true"),
        pytest.param(False, True, False, id="bool_false"),
        pytest.param("ok", True, "ok", id="str"),
        pytest.param("  ok  ", True, "ok", id="str_stripped"),
        pytest.param("x" * 120, True, "x" * 120, id="str_at_limit"),
        pytest.param("x" * 121, False, None, id="str_over_limit"),
        pytest.param("", False, None, id="str_empty"),
        pytest.param("   ", False, None, id="str_blank"),
        pytest.param(None, False, None, id="none"),
        pytest.param({"a": 1}, False, None, id="dict"),
        pytest.param([1], False, None, id="list"),
        pytest.param((1,), False, None, id="tuple"),
    ],
)
def test_c9_coerce_reported_value_truth_table(raw, ok, coerced):
    """标量收编真值表——口径必须与 `_collect_grouped_metrics` 完全一致（120 字符上限）。

    120 / 121 两格是边界：阈值若被改动，这两格中必有一格变红。
    """
    assert execution_module._coerce_reported_value(raw) == (ok, coerced)
    assert execution_module._GROUP_METRIC_STR_MAX_LEN == 120


def test_c10_str_value_goes_through_mask_value():
    """str 值必须过脱敏出口（生成代码的输出理论上可能内嵌凭证，§9.3 纪律）。"""
    sentinel = "sk-s713-secret-token"
    secrets_store._SENSITIVE_VALUES.add(sentinel)
    main, _groups = execution_module._split_reported_metrics(
        [{"name": "note", "value": f"token={sentinel}"}]
    )
    assert sentinel not in main["note"], "敏感值必须被打码"
    assert main["note"] != f"token={sentinel}"


def test_c11_non_str_group_falls_back_to_main_bucket():
    """现状钉死：`group` 是 int / list / dict（非 str）⇒ 归主实验桶。

    这不是"正确"或"错误"，而是一个**取舍**：实现只认 `isinstance(raw_group, str)`。
    钉死它是为了让日后任何改动（比如改成 `str(raw_group)` 收编）成为**显式决策**，
    而不是悄悄改变"哪些指标会进 success 分子的候选集"。
    """
    main, groups = execution_module._split_reported_metrics([
        {"name": "a", "value": 1, "group": 123},
        {"name": "b", "value": 2, "group": ["UMAP"]},
        {"name": "c", "value": 3, "group": {"g": "UMAP"}},
    ])
    assert main == {"a": 1, "b": 2, "c": 3}
    assert groups == {}


# ===========================================================================
# D 区：schema 与装配（断行为，不断源码子串——S7-10 F2 教训）
# ===========================================================================


def _install_agent_harness(monkeypatch, subgraph_result: Any) -> Dict[str, Any]:
    """把 `_run_execution_agent` 的外部依赖全部替身化，捕获传给工厂的实参。

    体例沿 `tests/test_sprint7_targeted.py::_install_agent_harness`：
    `result_schema` **显式列在签名里并记录**，而不是被 `**kwargs` 吞掉——否则
    "到底传没传 schema"就断不了了。
    """
    captured: Dict[str, Any] = {}

    class _FakeSubgraph:
        def invoke(self, initial: Dict[str, Any]) -> Dict[str, Any]:
            captured["initial"] = initial
            return {"messages": [], "round": 1, "result": subgraph_result}

    def fake_create_react_subgraph(
        *, node_name, system_prompt, tools, max_rounds, result_schema=None
    ):
        captured["result_schema"] = result_schema
        captured["system_prompt"] = system_prompt
        return _FakeSubgraph()

    monkeypatch.setattr(execution_module, "create_react_subgraph", fake_create_react_subgraph)
    monkeypatch.setattr(execution_module, "create_llm", lambda cfg: object())
    monkeypatch.setattr(execution_module, "resolve_llm_config", lambda cfg, node: cfg)
    monkeypatch.setattr(execution_module, "load_all_secrets", lambda *a, **k: {})
    monkeypatch.setattr(execution_module, "build_credential_env", lambda secrets: {})
    monkeypatch.setattr(execution_module, "make_prepare_environment_tool", lambda *a, **k: None)
    monkeypatch.setattr(execution_module, "make_run_in_sandbox_tool", lambda *a, **k: None)
    monkeypatch.setattr(execution_module, "make_request_user_input_tool", lambda *a, **k: None)
    return captured


def test_d1_result_schema_object_actually_reaches_the_subgraph_factory(monkeypatch):
    """`create_react_subgraph` 收到的第 5 个参数**就是** `EXECUTION_OUTPUT_SCHEMA` 本体。

    用 `is` 而不是 `==`：换成一份"长得像"的字典（比如把 `metrics` 摘掉再传）
    也能过 `==` 那一关，`is` 才能钉死"传的是那个常量"。
    """
    captured = _install_agent_harness(monkeypatch, None)
    execution_module._run_execution_agent(
        _state(_ONE_STEP), "/tmp/s713-workdir", {"execution_steps": _ONE_STEP},
    )
    assert captured["result_schema"] is execution_module.EXECUTION_OUTPUT_SCHEMA


def test_d2_required_omits_metrics_so_zero_metric_rounds_do_not_burn_a_call():
    """`required` 不含 `metrics` —— 用**生产的** `_missing_required_fields` 断行为。

    `react_base._missing_required_fields` 把"必填的空 list/dict"判为缺失，缺失会
    触发一次 schema 重生成调用 ⇒ 若 `metrics` 进了 required，**每个零指标回合都白烧
    一次账外 LLM 调用**。

    第二段是这条断言的"牙"：把 `metrics` 塞进 required 的**副本**，同一份 parsed
    立刻被判缺失 ⇒ 证明上面那个 `== []` 不是因为函数本身不干活。
    """
    schema = execution_module.EXECUTION_OUTPUT_SCHEMA
    assert "metrics" not in (schema.get("required") or [])

    parsed = {"steps_attempted": 0, "all_exit_zero": True, "summary": "空跑", "metrics": []}
    assert react_base_module._missing_required_fields(parsed, schema) == []

    mutated = copy.deepcopy(schema)
    mutated["required"] = list(mutated["required"]) + ["metrics"]
    assert react_base_module._missing_required_fields(parsed, mutated) == ["metrics"]


def _walk_property_names(node: Any) -> List[str]:
    names: List[str] = []
    if isinstance(node, dict):
        props = node.get("properties")
        if isinstance(props, dict):
            names.extend(props.keys())
        for value in node.values():
            names.extend(_walk_property_names(value))
    elif isinstance(node, list):
        for value in node:
            names.extend(_walk_property_names(value))
    return names


def test_d3_schema_and_prompt_carry_no_source_field():
    """`metrics[].source` 已被砍除（2026-08-02 Maria 拍板：没有消费点的字段即过度工程）。

    两侧同时断：schema 里递归扫不到 `source`，冻结区 prompt 主体里也不出现——
    只断一侧的话，另一侧留个孤儿描述，模型照样会去想那一层。
    """
    assert "source" not in _walk_property_names(execution_module.EXECUTION_OUTPUT_SCHEMA)
    assert "source" not in execution_module._EXECUTION_SYSTEM_PROMPT_BODY


def test_d4_metrics_item_shape_is_exactly_three_fields():
    """`metrics[]` 项恰三字段（name / value / group），且 name+value 必填。

    "恰"是有意义的：多一个字段就是多一层要模型去想的东西（MEMORY §4.1），
    少一个 `group` 则整个"分组 / 主实验"的拆分失去输入。
    """
    items = execution_module.EXECUTION_OUTPUT_SCHEMA["properties"]["metrics"]["items"]
    assert sorted(items["properties"]) == ["group", "name", "value"]
    assert sorted(items["required"]) == ["name", "value"]
    assert execution_module.EXECUTION_OUTPUT_SCHEMA["properties"]["metrics"]["type"] == "array"


@pytest.mark.parametrize(
    "subgraph_result, expected",
    [
        pytest.param(None, [], id="result_none"),
        pytest.param({}, [], id="result_empty_dict"),
        pytest.param({"summary": "x"}, [], id="metrics_key_absent"),
        pytest.param({"metrics": None}, [], id="metrics_none"),
        pytest.param({"metrics": "不是数组"}, [], id="metrics_str"),
        pytest.param({"metrics": {"a": 1}}, [], id="metrics_dict"),
        pytest.param("不是 dict", [], id="result_not_dict"),
        pytest.param(
            {"metrics": [{"name": "acc", "value": 0.9, "group": "UMAP"}]},
            [{"name": "acc", "value": 0.9, "group": "UMAP"}],
            id="healthy_passthrough",
        ),
        pytest.param(
            {"metrics": ["垃圾", {"name": "acc", "value": 0.9}]},
            ["垃圾", {"name": "acc", "value": 0.9}],
            id="raw_passthrough_no_cleaning_here",
        ),
    ],
)
def test_d5_reported_metrics_passthrough_tolerates_every_result_shape(
    monkeypatch, subgraph_result, expected, caplog,
):
    """`<result>.metrics` 原样透传到 `ExecAgentOutput.reported_metrics`，四类畸形一律降级 []。

    最后一格钉住"这里不清洗"：清洗单点在 `_split_reported_metrics`，若有人顺手在
    透传处也过滤一遍，就会出现两个清洗点、日后必漂移。
    """
    _install_agent_harness(monkeypatch, subgraph_result)
    with caplog.at_level(logging.WARNING):
        out = execution_module._run_execution_agent(
            _state(_ONE_STEP), "/tmp/s713-workdir", {"execution_steps": _ONE_STEP},
        )
    assert out.reported_metrics == expected
    # 零指标是合法常态（跑失败 / 只跑了 prepare），透传处不得打 WARNING 制造噪声。
    assert [m for m in _warnings(caplog) if "metrics" in m] == []


def test_d6_degraded_path_leaves_reported_metrics_empty(monkeypatch):
    """子图抛异常 ⇒ 降级 ExecAgentOutput，`reported_metrics` 为空（默认值兜底）。"""
    def _boom(**kwargs):
        raise RuntimeError("子图炸了")

    monkeypatch.setattr(execution_module, "create_react_subgraph", _boom)
    monkeypatch.setattr(execution_module, "create_llm", lambda cfg: object())
    monkeypatch.setattr(execution_module, "resolve_llm_config", lambda cfg, node: cfg)
    monkeypatch.setattr(execution_module, "load_all_secrets", lambda *a, **k: {})
    monkeypatch.setattr(execution_module, "build_credential_env", lambda secrets: {})
    monkeypatch.setattr(execution_module, "make_prepare_environment_tool", lambda *a, **k: None)
    monkeypatch.setattr(execution_module, "make_run_in_sandbox_tool", lambda *a, **k: None)
    monkeypatch.setattr(execution_module, "make_request_user_input_tool", lambda *a, **k: None)

    out = execution_module._run_execution_agent(
        _state(_ONE_STEP), "/tmp/s713-workdir", {"execution_steps": _ONE_STEP},
    )
    assert out.reported_metrics == []
    assert out.rounds_used == 0


def test_d7_reported_metrics_never_reaches_the_execution_fact_channels(monkeypatch, tmp_path):
    """R-S4-01 红线：自报指标绝不得影响对账 / 完整度 / exit_ok。

    做法是"投影对比"：其余前置全固定，只让 agent 多报一堆指标，断言除
    `metrics` / `metrics_groups` 之外的一切（success / 对账 / 路由 / 错误）逐字节相同。
    比逐字段断言强——任何新接的消费点都会让这份投影变。
    """
    work_dir = _make_disk_groups(tmp_path, {"legacy_group": {"disk_metric": 1.5}})

    def _facts(out: Dict[str, Any]) -> str:
        er = dict(out["execution_result"])
        for volatile in ("runtime_seconds", "logs", "metrics", "metrics_groups"):
            er.pop(volatile, None)
        return json.dumps(
            {"er": er, "route": out.get("_dev_loop_route")},
            sort_keys=True, ensure_ascii=False, default=str,
        )

    silent = _drive(monkeypatch, stdout=_METRICS_LINE, reported=[], work_dir=work_dir)
    chatty = _drive(
        monkeypatch, stdout=_METRICS_LINE, work_dir=work_dir,
        reported=[
            {"name": f"m{i}", "value": i, "group": g}
            for i in range(5) for g in (None, "UMAP", "t-SNE")
        ],
    )
    assert _facts(chatty) == _facts(silent)


# ===========================================================================
# E 区：`expected_results` 进 agent 上下文（方案根基）
# ===========================================================================


def test_e1_expected_results_is_injected_verbatim():
    """计划里的 `expected_results` 原样进 payload——agent 靠它知道"计划怎么称呼这一组"。

    不注入的话，"`group` / `name` 用计划写法"这条约束**整条落空**：agent 只能按
    产物目录名和代码字段名填，回到 2026-08-01 那次"组名失配"的老路。
    """
    expected = _load_fixture("expected_results.json")
    payload = execution_module._build_execution_agent_context(
        _state(_ONE_STEP), "/tmp/s713-workdir",
        {"execution_steps": _ONE_STEP, "expected_results": expected},
    )
    assert payload["expected_results"] == expected
    # 计划写法必须**逐字**可见（t-SNE 的连字符大写是关键那一格）。
    blob = json.dumps(payload, ensure_ascii=False)
    assert "t-SNE" in blob and "k-NN classifier accuracy" in blob


@pytest.mark.parametrize(
    "plan_extra",
    [
        pytest.param({}, id="key_absent"),
        pytest.param({"expected_results": None}, id="none"),
        pytest.param({"expected_results": []}, id="empty_list"),
        pytest.param({"expected_results": {}}, id="empty_dict"),
        pytest.param({"expected_results": ""}, id="empty_str"),
    ],
)
def test_e2_absent_expected_results_leaves_payload_byte_identical(plan_extra):
    """★ "非空才注入"：无该键 / 空值的计划下，payload 与基线**逐字节相同**。

    这条守的是 Prompt Cache 与跨批次字节幂等——注入若无条件发生，所有老计划的
    HumanMessage 都会多出一个 `"expected_results": null`，白白作废一次缓存前缀。
    """
    base_plan = {"execution_steps": _ONE_STEP, "environment": {}}
    baseline = json.dumps(
        execution_module._build_execution_agent_context(
            _state(_ONE_STEP), "/tmp/s713-workdir", dict(base_plan),
        ),
        sort_keys=True, ensure_ascii=False, default=str,
    )
    actual = json.dumps(
        execution_module._build_execution_agent_context(
            _state(_ONE_STEP), "/tmp/s713-workdir", {**base_plan, **plan_extra},
        ),
        sort_keys=True, ensure_ascii=False, default=str,
    )
    assert actual == baseline
    assert "expected_results" not in actual


def test_e3_injection_reaches_the_human_message_the_agent_actually_reads(monkeypatch):
    """端到端一格：注入的内容真的出现在送进子图的 HumanMessage 里。

    只断 `_build_execution_agent_context` 的返回值证明不了 agent 看得见它——
    payload 到 HumanMessage 之间还有一次 `json.dumps` 装配。
    """
    captured = _install_agent_harness(monkeypatch, None)
    expected = _load_fixture("expected_results.json")
    execution_module._run_execution_agent(
        _state(_ONE_STEP), "/tmp/s713-workdir",
        {"execution_steps": _ONE_STEP, "expected_results": expected},
    )
    human = captured["initial"]["messages"][-1]
    assert "expected_results" in human.content
    assert "t-SNE" in human.content


# ===========================================================================
# F 区：零改动红线（十个函数本批一字未改）
# ===========================================================================
#
# 主控声称已逐字节复核。本区把该声称变成**可执行断言**：任何一处被改动（哪怕只是
# 挪一个空格），下面这张表当场红，改动者必须显式更新基线并写明原因——这正是
# `_EXECUTION_SYSTEM_PROMPT_BODY` 字节门的同款纪律，只是对象换成了函数体。
#
# 基线取自 commit 1e2577d（S7-13 交付点）。独立核实方式（本文件落盘时已实做）：
# 用 `ast.get_source_segment` 分别解析 e61f82a（本批开工前）与 HEAD 的
# `execution.py`，十个函数的源码片段 sha256 **逐一相同**。

_FROZEN_FUNCTION_HASHES: Dict[str, str] = {
    "_completion_insufficient": "a8b5d4c33c7fcfba",
    "_apply_no_metrics": "01ca42bfed8e930e",
    "_apply_incomplete_execution": "8f2563c160c41cf9",
    "_build_execution_result": "67296a1efe8aa703",
    "_reconcile_steps": "a837711c28f2bbbf",
    "_audit_declared_steps": "e45f8adf2406ab43",
    "_extract_metrics_block": "438257b7f5ef4283",
    "_parse_metrics": "4fa65c12efae7018",
    "_collect_grouped_metrics": "bd837c45105b9646",
    "_regex_scan_metrics": "207e27a731701d54",
}


@pytest.mark.parametrize("func_name", sorted(_FROZEN_FUNCTION_HASHES))
def test_f1_frozen_function_source_bytes_are_unchanged(func_name):
    """S7-13 零改动红线：这十个函数的源码字节被钉死。

    改动流程（与 §48.1 prompt 字节门四件套同款）：①先跑一次看它当场红；
    ②重算写死新值；③在 dev-plan 留一行变更原因；④再验红一次。
    **禁止写成 `EXPECTED = actual` 的自锁定形态**（R-S7-41 的坑）。
    """
    src = inspect.getsource(getattr(execution_module, func_name))
    actual = hashlib.sha256(src.encode("utf-8")).hexdigest()[:16]
    assert actual == _FROZEN_FUNCTION_HASHES[func_name], (
        f"{func_name} 源码已变更（当前：{actual}，基线：{_FROZEN_FUNCTION_HASHES[func_name]}）"
        f"——S7-13 声明该函数零改动，如确需改动请走四件套并更新本表"
    )


def test_f2_extract_metrics_block_still_takes_the_last_block():
    """P-70 残留**行为**钉死：档 1 仍然"只取最后一块"，本批一字未改。

    字节门证明"源码没变"，这条证明"语义就是那个语义"——两者缺一不可：日后若有人
    重写该函数并同步更新哈希，字节门会放行，这条会拦住。

    喂的是 2026-08-02 真跑 `round_0.log` 里 7 个 `<METRICS>` 块的原文（夹具
    `metrics_blocks.txt`）：返回的是**最后一块**。这一轮它恰好是对的（收尾脚本
    打的正是汇总指标），但那是运气 —— 2026-08-01 那轮同一规则取到的是
    `mean_timing_seconds`（收尾脚本的运行时元数据）。
    """
    blocks = (_FIXTURES / "metrics_blocks.txt").read_text(encoding="utf-8")
    lines = [ln for ln in blocks.splitlines() if ln.strip()]
    assert len(lines) == 7, "夹具前提：真跑日志里有 7 个 METRICS 块"
    got = execution_module._extract_metrics_block(blocks)
    assert got == json.loads(lines[-1][len("<METRICS>"):-len("</METRICS>")])
    assert "best_knn_accuracy" in got
    # 反证"取的是最后一块"而不是"取的是并集"：第一块的键不在结果里。
    assert "n_samples" not in got


def test_f3_extract_metrics_block_still_skips_all_non_scalar_blocks():
    """P-60 登记的容错行为也没变：值全是非标量的块被跳过、继续往前找。

    照"命中即返回"的描述去实现会丢掉这一格 —— 钉住它，防止后人照描述改写。
    """
    stdout = (
        '<METRICS>{"acc": 0.5}</METRICS>\n'
        '<METRICS>{"nested": {"a": 1}, "arr": [1, 2]}</METRICS>\n'
    )
    assert execution_module._extract_metrics_block(stdout) == {"acc": 0.5}


@pytest.mark.parametrize(
    "recon, expected",
    [
        pytest.param({"planned_actionable": 2, "completed": 1}, True, id="not_done"),
        pytest.param({"planned_actionable": 2, "completed": 2}, False, id="done"),
        pytest.param({"planned_actionable": 0, "completed": 0}, False, id="nothing_actionable"),
        pytest.param({"planned": 2, "completed": 1}, True, id="legacy_falls_back_to_planned"),
        pytest.param({}, False, id="empty"),
        pytest.param(None, False, id="none"),
        pytest.param("垃圾", False, id="not_a_dict"),
        pytest.param({"planned_actionable": 2, "completed": True}, False, id="completed_bool"),
    ],
)
def test_f4_completion_predicate_truth_table_unchanged(recon, expected):
    """S7-11 交付的完成度谓词真值表——本批零改动，逐格钉死。"""
    assert execution_module._completion_insufficient(recon) is expected


def test_f5_success_conjunction_is_still_exactly_three_terms(tmp_path):
    """`success` = exit 全 0 **且** 至少 1 个指标 **且** 步骤跑完 —— 三合取项一格不变。

    直接驱动 `_build_execution_result`，逐格翻转三个自变量。第三格
    （"多了 metrics_groups 但 metrics 仍空"）是本批特别要守的：分组指标**不是**
    success 的指标分子，否则 agent 报一组就能把成功判定顶起来。
    """
    build = execution_module._build_execution_result
    feedback = execution_module.ExecutionFeedback(
        execution_module.ErrorCategory.NONE, True, "", "", "",
    )
    ok_recon = {"planned": 1, "planned_actionable": 1, "completed": 1}
    runs_ok = [_run(["python", "train.py"])]
    runs_bad = [_run(["python", "train.py"], exit_code=1)]

    def _success(runs, metrics, recon, groups=None) -> bool:
        return build(
            _prep(), runs, feedback, metrics, str(tmp_path),
            step_reconciliation=recon, metrics_groups=groups,
        )["success"]

    assert _success(runs_ok, {"acc": 1}, ok_recon) is True
    assert _success(runs_bad, {"acc": 1}, ok_recon) is False, "exit 非 0"
    assert _success(runs_ok, {}, ok_recon) is False, "零指标"
    assert _success(runs_ok, {}, ok_recon, {"UMAP": {"acc": 1}}) is False, (
        "分组指标不得顶替 metrics 成为 success 的指标分子"
    )
    assert _success(
        runs_ok, {"acc": 1}, {"planned": 2, "planned_actionable": 2, "completed": 1}
    ) is False, "步骤没跑完"


# ===========================================================================
# G 区 ★ 2026-08-02 第二次真跑重放（离线夹具，零配额）
# ===========================================================================
#
# 夹具抄自 `/data/myproj/.umap_evidence/20260802-233011/`（`realrun.db` 里 agent 的
# `<result>` 原文 + `reproduction_plan.json` 的 5 条 expected_results + 现场 work_dir
# 的磁盘扫描产出 + `outputs/eval/knn_results.csv` + `exec_logs/round_0.log` 的 7 个
# METRICS 块）。现场会被下一次真跑覆盖，抄走后本区**永久可离线复跑**。
#
# 本区除了复现"链路通了"，还钉住两条**与交付表述相左**的事实：
#   - G4：这一轮的主指标 `best_knn_accuracy` 来自**档 1 主通道**（最后一块恰好是
#     汇总块），**不是** agent 自报补回来的 —— agent 一条主实验指标都没报。
#   - G6：3 条机器判定里有 **2 条**会随"保留首次 / 保留末次"这个 tie-break 方向翻转。


def test_g1_realrun_24_reported_items_collapse_into_four_groups(caplog):
    """真跑重放：24 条自报 → 4 组 × 2 指标，去重 16 处并如实留痕。"""
    reported = _load_fixture("agent_reported_metrics.json")
    assert len(reported) == 24
    with caplog.at_level(logging.WARNING):
        main, groups = execution_module._split_reported_metrics(reported)
    assert sorted(groups) == ["PCA", "UMAP", "laplacian_eigenmaps", "t-SNE"]
    assert all(sorted(v) == ["k-NN classifier accuracy", "runtime"] for v in groups.values())
    conflicts = [m for m in _warnings(caplog) if "同名异值" in m]
    assert len(conflicts) == 1 and "16 处" in conflicts[0]
    assert main == {}


def test_g2_realrun_verdicts_reproduce_exactly():
    """真跑重放：5 条计划预期的回验产出逐条复现（2 符合 / 1 不符 / 2 未验证）。

    这是"链路下半段打通了"的机制证明 —— `t-SNE` 这个带连字符大写的写法过了
    `_match_metrics_group`，`k-NN classifier accuracy` 过了 `_lookup_metric_value`。
    **它不证明 agent 一定会照做**（那只能靠下一次真跑）。
    """
    _main, groups = execution_module._split_reported_metrics(
        _load_fixture("agent_reported_metrics.json")
    )
    checks = reporting_module._verify_expected_results(
        _load_fixture("expected_results.json"), {"metrics_groups": groups},
    )
    assert [c["verdict"] for c in checks] == ["未验证", "符合", "未验证", "符合", "不符"]


def test_g3_trend_less_entries_are_unconditionally_unverified():
    """诚实守门 A 半：`trend` 为 null 的条目**恒**"未验证"，与本批无关。

    2026-08-02 这份计划有 2 条（上一份是 3 条）—— 数字会随计划变，**性质不会变**：
    它们死在 planning 侧没产出 trend 结构，属规划环节，本批不治也治不了。
    """
    expected = _load_fixture("expected_results.json")
    trendless = [i for i, e in enumerate(expected) if not isinstance(e.get("trend"), dict)]
    assert trendless, "夹具前提：这份计划确有 trend 缺失的条目"

    _main, groups = execution_module._split_reported_metrics(
        _load_fixture("agent_reported_metrics.json")
    )
    for metrics_groups in ({}, groups, {"UMAP": {"任何指标": 1.0}}):
        checks = reporting_module._verify_expected_results(
            expected, {"metrics_groups": metrics_groups},
        )
        for i in trendless:
            assert checks[i]["verdict"] == "未验证"


def test_g4_the_headline_metric_came_from_the_main_channel_not_from_the_agent():
    """★ 独立发现：这一轮的 `best_knn_accuracy` 是**档 1 主通道**解析出来的。

    交付口径把"主指标回来了（0.9766，上一轮是 mean_timing_seconds=44.81）"记在本批
    名下。但两条硬事实相反：
      ① agent 自报的 24 条**全部带 group**，`reported_main` 为空 ⇒ 步骤 4.4 的
         主指标合并分支这一轮**根本没执行**；
      ② 真跑日志最后一个 `<METRICS>` 块本身就是
         `{"best_dataset","best_method","best_knn_accuracy","num_results"}`，
         `_extract_metrics_block`（本批一字未改）直接取到它。
    ⇒ 主指标变好，**归因于 coding 侧最后打印的那一块变对了**，不是本批的功劳。
    P-70 登记的"档 1 选块缺陷仍在"依然完整成立。
    """
    main, groups = execution_module._split_reported_metrics(
        _load_fixture("agent_reported_metrics.json")
    )
    assert main == {}, "agent 一条主实验指标都没报"
    assert groups, "报的全是分组指标"

    blocks = (_FIXTURES / "metrics_blocks.txt").read_text(encoding="utf-8")
    parsed = execution_module._extract_metrics_block(blocks)
    assert parsed["best_knn_accuracy"] == pytest.approx(0.976627712854758)


def test_g5_retained_values_are_one_arbitrary_dataset_out_of_three():
    """★ 去重保留的是 `COIL-20` 那一组 —— 三个数据集里**按数组顺序**排第一的那个。

    对照 `knn_results.csv`（现场 12 行 = 3 数据集 × 4 方法）：保留下来的 8 个值
    与 COIL-20 的 8 行逐一相等，MNIST / PenDigits 的 16 个值被静默丢弃（只在日志里
    留了一行 WARNING，用户可见报告里**没有任何数据集标注**）。
    """
    _main, groups = execution_module._split_reported_metrics(
        _load_fixture("agent_reported_metrics.json")
    )
    rows = [
        line.split(",")
        for line in (_FIXTURES / "knn_results.csv").read_text(encoding="utf-8").splitlines()[1:]
        if line.strip()
    ]
    coil = {r[1]: (float(r[2]), float(r[6])) for r in rows if r[0] == "COIL-20"}
    assert len(coil) == 4, "夹具前提：CSV 里 COIL-20 有 4 个方法"

    method_of = {
        "UMAP": "umap", "t-SNE": "tsne", "PCA": "pca",
        "laplacian_eigenmaps": "laplacian_eigenmaps",
    }
    for group, method in method_of.items():
        acc, runtime = coil[method]
        assert groups[group]["k-NN classifier accuracy"] == pytest.approx(acc)
        assert groups[group]["runtime"] == pytest.approx(runtime)


def test_g6_two_of_three_verdicts_flip_when_the_tie_break_direction_flips():
    """★★ 本次最重要的发现：3 条机器判定里 **2 条**由 tie-break 方向决定。

    "保留首次"与"保留末次"都是**没有科学含义的实现细节**（数组顺序 = agent 逐数据集
    汇报的顺序）。同一份自报数据：
        保留首次（现行，取到 COIL-20）→ ['未验证','符合','未验证','符合','不符']
        保留末次（取到 PenDigits）    → ['未验证','符合','未验证','不符','符合']
    第 4 条（runtime）与第 5 条（k-NN accuracy）**双双翻转**。

    ⇒ 真跑报告把第 5 条那个 ❌不符 称为"本轮最有价值的产出 / 判定正确"，实测**站不住**：
    换个同样任意的 tie-break，它就是 ✅符合；而且被选中的 COIL-20 恰是 agent 自己在
    `notes` 里标注过"实际下载的是 Olivetti faces"的那份数据。

    本条是 characterization（钉死现状 + 钉死这个敏感性），**不是**在主张改成"保留末次"
    ——两个方向一样任意。真正的出口是"同名多值不该被坍缩成一个数"，属设计决策，
    已作为独立发现上报，不在本批范围内自行修改。
    """
    reported = _load_fixture("agent_reported_metrics.json")
    expected = _load_fixture("expected_results.json")

    _main, first_wins = execution_module._split_reported_metrics(reported)

    last_wins: Dict[str, Dict[str, Any]] = {}
    for item in reported:
        last_wins.setdefault(item["group"], {})[item["name"]] = item["value"]

    def _verdicts(groups):
        return [
            c["verdict"]
            for c in reporting_module._verify_expected_results(
                expected, {"metrics_groups": groups}
            )
        ]

    v_first = _verdicts(first_wins)
    v_last = _verdicts(last_wins)
    assert v_first == ["未验证", "符合", "未验证", "符合", "不符"]
    assert v_last == ["未验证", "符合", "未验证", "不符", "符合"]
    flipped = [i for i, (a, b) in enumerate(zip(v_first, v_last)) if a != b]
    assert flipped == [3, 4], (
        "第 4、5 条判定随 tie-break 方向翻转 —— 判定结论不得被当作复现结论对外表述"
    )
