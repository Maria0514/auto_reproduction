"""Sprint 4 任务 E3（S4-03/06/08）：execution() 主函数改造 + 预算对账 + credential 分类。

覆盖 dev-plan §4 任务 E3 自测检查点：
    - CP-E3-1 预算对账（AC-S4-04）：一次 execution 后 retry_budget_remaining 递减恰 =
      子图 rounds + metric_llm_calls；_dev_loop_llm_calls 累加同额；guard 命中重入路径
      零扣减（rounds=0）；触顶 60 → interrupt#2（复用 sp3 CP-C3-9/10 断言范式改造）；
    - CP-E3-2 credential 分类（AC-S4-07）：8 关键字参数化命中 CREDENTIAL_REQUIRED；
      auto_fixable=False、error_type=permanent、不消耗 fix_loop_count（走 interrupt#2
      兜底路径而非 retry_coding）；判定顺序先于 data_missing / hardware；
    - CP-E3-3 B 档判定只认编排层：收集器 exit 全 0 + ≥1 指标 → success；agent 自述
      成功但收集器有非 0 → failure（CP-E2-3 端到端闭环，真实子图 + liar 剧本）；
    - CP-E3-4 logs / interrupt payload 无凭证明文（注入已知 token 后断言，AC-S4-11
      节点内分支）；
    - CP-E3-5 通信契约不变（AC-S4-08 / Q-D）：失败时 execution_result +
      [error_category=...] 前缀 + fix_loop_history 逐字同 sp3 schema，coding
      _digest_execution_feedback 零改动可消费（只读 coding.py 不改它）。

全离线（InMemorySaver + 脚本 LLM + mock sandbox / mock agent），零 API 配额。
"""

from __future__ import annotations

import importlib
import json
import logging
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

import config
from core import secrets_store
from core.state import ExecutionMode

execution_module = importlib.import_module("core.nodes.execution")
coding_module = importlib.import_module("core.nodes.coding")  # CP-E3-5 只读消费，不改

from config import (  # noqa: E402
    DEV_LOOP_MIN_CALLS_PER_ROUND,
    MAX_DEV_LOOP_LLM_CALLS,
    MAX_FIX_LOOP_COUNT,
)
from core.nodes.execution import (  # noqa: E402  # callable 遮蔽陷阱：常量走 execution_module
    execution,
)
from sandbox.local_venv import SandboxPrepareResult, SandboxRunResult  # noqa: E402

AUTO_FIXABLE = execution_module.AUTO_FIXABLE
ErrorCategory = execution_module.ErrorCategory
ExecutionFeedback = execution_module.ExecutionFeedback
ExecAgentOutput = execution_module.ExecAgentOutput
NODE_NAME = execution_module.NODE_NAME
INTERRUPT_KIND = execution_module.INTERRUPT_KIND

_TOKEN = "ghp_SECRET_e3_token_1234567890"


# ---------------------------------------------------------------------------
# fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_process_sensitive_set():
    secrets_store._SENSITIVE_VALUES.clear()
    yield
    secrets_store._SENSITIVE_VALUES.clear()


@pytest.fixture(autouse=True)
def secrets_workspace(tmp_path, monkeypatch):
    """`.secrets` / mask 落点隔离到 tmp_path（secrets_store 动态读 config.WORKSPACE_DIR）。"""
    ws = tmp_path / "workspace"
    ws.mkdir()
    monkeypatch.setattr(config, "WORKSPACE_DIR", ws)
    return ws


@pytest.fixture(autouse=True)
def _no_artifacts(monkeypatch):
    monkeypatch.setattr(execution_module, "collect_artifacts", lambda *a, **k: [])


def _prep(
    success: bool = True,
    install_log: str = "ok",
    python_exe: str = "/w/.venv/bin/python",
) -> SandboxPrepareResult:
    return SandboxPrepareResult(
        success=success, venv_dir="/w/.venv", python_exe=python_exe, pip_exe="",
        env_info={"python_version": "Python 3.11"}, install_log=install_log,
        install_failed_packages=[], error=None,
    )


def _run(
    exit_code: int = 0,
    stdout: str = "",
    stderr: str = "",
    timed_out: bool = False,
) -> SandboxRunResult:
    return SandboxRunResult(
        exit_code=exit_code, stdout=stdout, stderr=stderr,
        duration_seconds=0.1, timed_out=timed_out,
        output_truncated=False, command=["python", "x.py"],
    )


def _agent_out(
    prep: Optional[SandboxPrepareResult],
    runs: List[SandboxRunResult],
    rounds: int,
) -> ExecAgentOutput:
    return ExecAgentOutput(
        prep=prep, run_results=runs, rounds_used=rounds, llm_calls=rounds,
    )


def _base_state(**overrides: Any) -> Dict[str, Any]:
    state: Dict[str, Any] = {
        "llm_config_set": {"default": {"model": "test"}},
        "code_output_dir": "/tmp/e3-workdir",
        "reproduction_plan": {
            "execution_steps": [{"command": "python train.py"}],
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
    """patch _run_execution_agent 返回预设输出，返回调用计数（guard 零调用断言用）。"""
    cnt = {"agent": 0}

    def fake_agent(state, work_dir, plan):
        cnt["agent"] += 1
        return out

    monkeypatch.setattr(execution_module, "_run_execution_agent", fake_agent)
    return cnt


def _build_self_loop_graph(checkpointer):
    """sp3 CP-C3-7 同款最小 self-loop 图（模拟 D1 _route_after_execution 关键分支）。"""
    from langgraph.graph import END, START, StateGraph

    from core.state import GlobalState

    g = StateGraph(GlobalState)
    g.add_node("execution", execution)
    g.add_edge(START, "execution")

    def route(state: Dict[str, Any]) -> str:
        if state.get("_dev_loop_route") == "await_dev_loop_interrupt":
            return "execution"
        return "end"

    g.add_conditional_edges("execution", route, {"execution": "execution", "end": END})
    return g.compile(checkpointer=checkpointer)


def _get_interrupt_payload(graph, cfg) -> Dict[str, Any]:
    snap = graph.get_state(cfg)
    interrupts = [
        iv for task in snap.tasks for iv in (getattr(task, "interrupts", None) or [])
    ]
    assert interrupts, "snapshot 无 interrupt 元数据"
    return interrupts[0].value


# ===========================================================================
# CP-E3-1 预算对账（AC-S4-04）
# ===========================================================================


def test_cp_e3_1_deduction_rounds_plus_metric_calls(monkeypatch, caplog):
    """递减恰 = 子图 rounds + metric_llm_calls；_dev_loop_llm_calls 累加同额 + INFO 日志。"""
    _patch_agent(monkeypatch, _agent_out(
        _prep(), [_run(exit_code=0, stdout="final result printed but unstructured")],
        rounds=4,
    ))
    # 档 1/2 均不命中 → 档 3 LLM 抽取触发（mock 返回 1 指标 + 1 次消耗）。
    monkeypatch.setattr(
        execution_module, "_llm_extract_metrics", lambda *a, **k: ({"accuracy": 0.8}, 1)
    )
    state = _base_state(
        retry_budget_remaining=40, _dev_loop_llm_calls=0,
        paper_analysis={"metrics": ["nonexistent"]},
    )
    with caplog.at_level(logging.INFO):
        out = execution(state)

    assert out["retry_budget_remaining"] == 40 - (4 + 1)  # rounds=4 + metric=1
    assert out["_dev_loop_llm_calls"] == 0 + (4 + 1)
    assert out["execution_result"]["success"] is True  # exit 0 + ≥1 指标
    assert any("LLM 预算单点扣减" in r.message for r in caplog.records), \
        "扣减必须有 INFO 日志留痕"


def test_cp_e3_1_deduction_rounds_only_no_metric_call(monkeypatch):
    """档 1 结构化命中（metric_llm_calls=0）→ 递减恰 = 子图 rounds。"""
    _patch_agent(monkeypatch, _agent_out(
        _prep(), [_run(exit_code=0, stdout='<METRICS>{"acc": 0.9}</METRICS>')], rounds=4,
    ))
    state = _base_state(retry_budget_remaining=30, _dev_loop_llm_calls=2)
    out = execution(state)
    assert out["retry_budget_remaining"] == 26  # 30 - 4
    assert out["_dev_loop_llm_calls"] == 6  # 2 + 4
    assert out["execution_result"]["success"] is True


def test_cp_e3_1_degrade_rounds_zero_no_deduction(monkeypatch):
    """子图降级（空结果 + rounds=0）→ 零扣减（不写预算键），走既有降级分类不炸节点。"""
    _patch_agent(monkeypatch, _agent_out(None, [], rounds=0))
    state = _base_state(retry_budget_remaining=40, _dev_loop_llm_calls=3)
    out = execution(state)
    assert "retry_budget_remaining" not in out, "rounds=0 绝不扣预算"
    assert "_dev_loop_llm_calls" not in out
    er = out["execution_result"]
    assert er["success"] is False
    # prep=None + 空 run_results → 环境未准备（既有降级分类：DEPENDENCY 可修复）。
    assert "[error_category=dependency]" in er["errors"][0]


def test_cp_e3_1_guard_reentry_zero_deduction_and_agent_not_called(monkeypatch):
    """guard 命中重入路径：不跑子图（agent 零调用）、零扣减，直接 interrupt#2。

    兼验 CP-E3-4 节点内分支：interrupt payload 的 execution_errors 无凭证明文。
    """
    secrets_store.register_sensitive_value(_TOKEN)

    def forbidden_agent(*a, **k):  # guard 命中绝不允许重跑子图
        raise AssertionError("guard 命中路径不得调用 _run_execution_agent")

    monkeypatch.setattr(execution_module, "_run_execution_agent", forbidden_agent)

    committed = {
        "success": False, "metrics": {},
        "logs": "masked-earlier",
        "errors": [f"[error_category=data_missing] 数据集缺失 url=https://u:{_TOKEN}@host/d"],
        "artifacts": [], "runtime_seconds": 0.1, "environment_info": {},
    }
    graph = _build_self_loop_graph(InMemorySaver())
    cfg = {"configurable": {"thread_id": f"e3-guard-{uuid.uuid4().hex[:8]}"}}
    init = _base_state(
        execution_result=committed,
        _dev_loop_route="await_dev_loop_interrupt",
        retry_budget_remaining=25,
        _dev_loop_llm_calls=7,
    )
    out1 = graph.invoke(init, cfg)
    assert "__interrupt__" in out1, "guard 命中 + 不可修复 → 必须 interrupt#2"

    payload = _get_interrupt_payload(graph, cfg)
    assert payload["interrupt_kind"] == INTERRUPT_KIND
    # CP-E3-4：payload 全文无 token 明文。
    assert _TOKEN not in json.dumps(payload, ensure_ascii=False, default=str)
    assert any("****" in e for e in payload["execution_errors"])

    # 零扣减：checkpoint 中预算与子预算保持入口值。
    values = graph.get_state(cfg).values
    assert values["retry_budget_remaining"] == 25
    assert values["_dev_loop_llm_calls"] == 7

    graph.invoke(Command(resume={"decision": "terminate"}), cfg)
    final = graph.get_state(cfg).values
    assert final["user_fix_decision"] == "terminate"
    assert final["retry_budget_remaining"] == 25, "resume 重跑同样零扣减"


def test_cp_e3_1_dev_loop_ceiling_to_interrupt(monkeypatch):
    """子预算触顶（CP-C3-10 范式改造）：入口已触顶 + 可修复失败 → 不回 coding，
    self-loop 重入后 interrupt#2；agent 恰跑 1 次（重入 guard 命中零重扣）。"""
    agent_cnt = _patch_agent(monkeypatch, _agent_out(
        _prep(), [_run(exit_code=1, stderr="RuntimeError: boom")], rounds=4,
    ))
    graph = _build_self_loop_graph(InMemorySaver())
    cfg = {"configurable": {"thread_id": f"e3-ceil-{uuid.uuid4().hex[:8]}"}}
    init = _base_state(
        fix_loop_count=1,
        retry_budget_remaining=30,
        _dev_loop_llm_calls=MAX_DEV_LOOP_LLM_CALLS,  # 入口已触顶
    )
    out1 = graph.invoke(init, cfg)
    assert "__interrupt__" in out1, "触顶 → 视同修复耗尽 → interrupt#2"
    payload = _get_interrupt_payload(graph, cfg)
    assert payload["interrupt_kind"] == INTERRUPT_KIND

    values = graph.get_state(cfg).values
    assert values["fix_loop_count"] == 1, "触顶不回 coding、不自增"
    # 首次进入仍如实扣减本次子图消耗；重入 guard 命中零重扣（恰扣一次）。
    assert values["retry_budget_remaining"] == 30 - 4
    assert values["_dev_loop_llm_calls"] == MAX_DEV_LOOP_LLM_CALLS + 4
    assert agent_cnt["agent"] == 1, "self-loop 重入必须 guard 命中、子图不重跑"


def test_cp_e3_1_entry_budget_gate_still_degrades(monkeypatch):
    """入口预算门逐字保留（CP-C3-9 改造）：预算 < 2 → 降级不 interrupt。"""
    _patch_agent(monkeypatch, _agent_out(
        _prep(), [_run(exit_code=1, stderr="ModuleNotFoundError: x")], rounds=2,
    ))
    state = _base_state(retry_budget_remaining=DEV_LOOP_MIN_CALLS_PER_ROUND - 1)
    out = execution(state)
    assert NODE_NAME in out["degraded_nodes"]
    assert out.get("_dev_loop_route") is None
    assert "user_fix_decision" not in out


# ===========================================================================
# CP-E3-2 credential 分类（AC-S4-07）
# ===========================================================================


_CRED_KEYWORDS = (
    "could not read username", "authentication failed",
    "terminal prompts disabled", "permission denied (publickey)",
    "fatal: could not read", "invalid username or password",
    "401 unauthorized", "403 forbidden",
)


def test_cp_e3_2_keyword_table_matches_architecture():
    """关键字表与架构 §9.2 逐字一致（8 项）。"""
    assert execution_module._CREDENTIAL_KEYWORDS == _CRED_KEYWORDS


@pytest.mark.parametrize("kw", _CRED_KEYWORDS)
def test_cp_e3_2_eight_keywords_hit_credential_required(kw):
    fb = execution_module._classify_execution(
        _prep(), [_run(exit_code=128, stderr=f"fatal: blah {kw.upper()} blah")],
    )
    assert fb.category == ErrorCategory.CREDENTIAL_REQUIRED
    assert fb.auto_fixable is False


def test_cp_e3_2_not_auto_fixable_maps_permanent():
    assert ErrorCategory.CREDENTIAL_REQUIRED not in AUTO_FIXABLE
    assert execution_module._map_category_to_error_type(
        ErrorCategory.CREDENTIAL_REQUIRED
    ) == "permanent"


@pytest.mark.parametrize("other", [
    "Dataset not found, please download the dataset first",   # data_missing 关键字
    "RuntimeError: CUDA out of memory",                        # hardware 关键字
])
def test_cp_e3_2_credential_priority_over_data_missing_and_hardware(other):
    """双关键字 stderr：credential 判定先于 data_missing / hardware。"""
    fb = execution_module._classify_execution(
        _prep(),
        [_run(exit_code=1, stderr=f"remote: Authentication failed.\n{other}")],
    )
    assert fb.category == ErrorCategory.CREDENTIAL_REQUIRED


def test_cp_e3_2_no_fix_loop_consumption_interrupt_fallback(monkeypatch):
    """credential 失败不消耗 fix_loop_count：走 interrupt#2 兜底路径（await），
    不走 retry_coding；error_type=permanent + [error_category=credential_required] 前缀。"""
    _patch_agent(monkeypatch, _agent_out(
        _prep(),
        [_run(exit_code=128, stderr="fatal: could not read Username for 'https://github.com'")],
        rounds=3,
    ))
    state = _base_state(fix_loop_count=0)
    out = execution(state)

    assert out["execution_result"]["success"] is False
    assert "fix_loop_count" not in out, "credential 不耗 fix_loop_count"
    assert out.get("_dev_loop_route") == "await_dev_loop_interrupt"
    assert out.get("_dev_loop_route") != "retry_coding"
    last_err = out["node_errors"][-1]
    assert last_err["error_type"] == "permanent"
    assert last_err["error_message"].startswith("[error_category=credential_required]")
    # 预算照常扣减（rounds=3 真实消耗）。
    assert out["retry_budget_remaining"] == 40 - 3


# ===========================================================================
# CP-E3-3 B 档判定只认编排层（CP-E2-3 端到端闭环）
# ===========================================================================


def test_cp_e3_3_success_from_real_exit_codes_and_metrics(monkeypatch):
    _patch_agent(monkeypatch, _agent_out(
        _prep(),
        [_run(exit_code=0, stdout="train ok"),
         _run(exit_code=0, stdout='<METRICS>{"acc": 0.91}</METRICS>')],
        rounds=4,
    ))
    out = execution(_base_state())
    er = out["execution_result"]
    assert er["success"] is True
    assert er["metrics"] == {"acc": 0.91}
    assert out.get("_dev_loop_route") is None


def test_cp_e3_3_exit_zero_but_no_metrics_not_success(monkeypatch):
    _patch_agent(monkeypatch, _agent_out(
        _prep(), [_run(exit_code=0, stdout="done, nothing structured")], rounds=2,
    ))
    monkeypatch.setattr(execution_module, "_llm_extract_metrics", lambda *a, **k: ({}, 1))
    out = execution(_base_state())
    assert out["execution_result"]["success"] is False


class _LiarLLM(BaseChatModel):
    """CP-E2-3 liar 剧本（B2 范式：路由基于 ToolMessage 计数，replay 安全）。"""

    @property
    def _llm_type(self) -> str:
        return "e3-liar"

    def bind_tools(self, tools: Any, **kwargs: Any) -> "_LiarLLM":
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        n_tool = sum(1 for m in messages if isinstance(m, ToolMessage))
        if n_tool == 0:
            ai = AIMessage(content="", tool_calls=[{
                "name": "run_in_sandbox", "args": {"command": "python broken.py"},
                "id": "c_run1", "type": "tool_call",
            }])
        else:
            body = json.dumps({
                "steps_attempted": 1, "all_exit_zero": True,
                "summary": "复现成功（谎报）", "notes": None,
            }, ensure_ascii=False, sort_keys=True)
            ai = AIMessage(content=(
                f"{config.REACT_RESULT_TAG_OPEN}{body}{config.REACT_RESULT_TAG_CLOSE}"
            ))
        return ChatResult(generations=[ChatGeneration(message=ai)])


def test_cp_e3_3_agent_lie_end_to_end_judged_failure(monkeypatch, tmp_path):
    """端到端（真实子图 + liar 剧本）：agent <result> 自称成功，但收集器真实
    exit_code=2 → _build_execution_result 判 failure（R-S4-01 命门）。"""
    wd = tmp_path / "wd"
    (wd / ".venv" / "bin").mkdir(parents=True)
    (wd / ".venv" / "pyvenv.cfg").write_text("home = /usr\n")

    def fake_run(python_exe, command, work_dir, *a, **k):
        return SandboxRunResult(
            exit_code=2, stdout="", stderr="RuntimeError: boom",
            duration_seconds=0.1, timed_out=False,
            output_truncated=False, command=list(command),
        )

    monkeypatch.setattr(execution_module, "run_in_venv", fake_run)
    monkeypatch.setattr(execution_module, "resolve_llm_config", lambda cfg, node: cfg)
    monkeypatch.setattr(execution_module, "create_llm", lambda cfg: _LiarLLM())

    state = _base_state(code_output_dir=str(wd), retry_budget_remaining=40)
    out = execution(state)

    er = out["execution_result"]
    assert er["success"] is False, "agent 谎报不得改变编排层判定"
    assert er["errors"][0].startswith("[error_category=runtime]")
    # 端到端预算对账：liar 剧本恰 2 轮（run + finish）→ 递减恰 2。
    assert out["retry_budget_remaining"] == 38
    assert out["_dev_loop_llm_calls"] == 2
    # runtime 可修复 → 回 coding（fix_loop_count 单点自增，sp3 契约零回归）。
    assert out["fix_loop_count"] == 1
    assert out["_dev_loop_route"] == "retry_coding"


# ===========================================================================
# CP-E3-4 logs / interrupt payload 无凭证明文（AC-S4-11 节点内分支）
# ===========================================================================


def test_cp_e3_4_logs_masked_before_state(monkeypatch):
    """install_log + stdout/stderr（收集器全保真原文）聚合回 state 前统一 mask
    （L-D1-01 消费侧兜底落点）。"""
    secrets_store.register_sensitive_value(_TOKEN)
    _patch_agent(monkeypatch, _agent_out(
        _prep(install_log=f"Looking in indexes: https://u:{_TOKEN}@pypi.example/simple"),
        [_run(exit_code=1, stdout=f"cloning https://{_TOKEN}@github.com/x/y",
              stderr=f"fatal: Authentication failed for 'https://{_TOKEN}@github.com'")],
        rounds=2,
    ))
    out = execution(_base_state())
    logs = out["execution_result"]["logs"]
    assert _TOKEN not in logs, "execution_result.logs 不得含凭证明文"
    assert "****" in logs
    # 语义保留：install_log 与 step 结构头仍在（mask 只替换敏感值）。
    assert "[install_log]" in logs and "[step#0" in logs


def test_cp_e3_4_interrupt_payload_masked():
    """_build_dev_loop_interrupt_payload：日志派生字段过 mask，键结构逐字同 sp3。"""
    secrets_store.register_sensitive_value(_TOKEN)
    feedback = ExecutionFeedback(
        category=ErrorCategory.CREDENTIAL_REQUIRED,
        auto_fixable=False,
        summary=f"认证失败 url=https://u:{_TOKEN}@github.com/x",
        fix_hint="提供凭证后重试",
        representative_stderr=f"fatal: Authentication failed ({_TOKEN})",
    )
    exec_result = {
        "success": False, "metrics": {}, "logs": "",
        "errors": [f"[error_category=credential_required] 认证失败 token={_TOKEN}"],
        "artifacts": [], "runtime_seconds": 0.0, "environment_info": {},
    }
    payload = execution_module._build_dev_loop_interrupt_payload(
        exec_result, feedback, _base_state(fix_loop_count=2),
    )
    # 键结构逐字保持 sp3 schema（AC-S4-05 命门）。
    assert set(payload.keys()) == {
        "interrupt_kind", "fix_loop_count", "error_category", "error_summary",
        "fix_hint", "auto_fixable", "fix_loop_history", "execution_errors",
        "representative_stderr", "options",
    }
    assert payload["interrupt_kind"] == INTERRUPT_KIND
    assert payload["error_category"] == "credential_required"
    # 全文无明文（AC-S4-11）。
    assert _TOKEN not in json.dumps(payload, ensure_ascii=False, default=str)
    assert "****" in payload["representative_stderr"]
    assert "****" in payload["error_summary"]
    assert all(_TOKEN not in e for e in payload["execution_errors"])


# ===========================================================================
# CP-E3-5 通信契约不变（AC-S4-08 / Q-D）：coding._digest_execution_feedback 零改动消费
# ===========================================================================


def test_cp_e3_5_coding_digest_consumes_failure_contract(monkeypatch):
    _patch_agent(monkeypatch, _agent_out(
        _prep(),
        [_run(exit_code=1, stderr="ModuleNotFoundError: No module named 'torch'")],
        rounds=3,
    ))
    out = execution(_base_state())
    er = out["execution_result"]

    # ExecutionResult schema：sp3 7 键 + sp5 4 新键恰为 11 键精确集合。
    # [sp5 T-S5-2-6 适配] 主控授权的唯一既有测试适配：T-S5-2-4/2-5/2-6 起两处构造
    # 点均补齐 step_reconciliation / budget_truncated / metrics_groups /
    # degraded_credentials，键集合"恰为"精确语义不弱化（防意外增删键）。
    assert set(er.keys()) == {
        "success", "metrics", "logs", "errors", "artifacts",
        "runtime_seconds", "environment_info", "step_reconciliation",
        "budget_truncated", "metrics_groups", "degraded_credentials",
    }
    assert er["errors"][0].startswith("[error_category=import]")

    # fix_loop_history 逐字同 sp3 FixLoopRecord 5 字段。
    rec = out["fix_loop_history"][0]
    assert set(rec.keys()) == {
        "round_number", "error_summary", "error_category", "fix_strategy", "timestamp",
    }
    assert rec["error_category"] == "import"

    # coding 侧解析断言（只读 coding.py，零改动消费）。
    digest = coding_module._digest_execution_feedback(er)
    assert digest["error_category"] == "import"
    assert digest["errors"] == er["errors"]
    assert digest["stderr_tail"] and digest["stderr_tail"] in er["logs"]


def test_cp_e3_5_coding_digest_parses_credential_category(monkeypatch):
    """新增细分类 credential_required 经同一前缀通道被 coding 零改动解析。"""
    _patch_agent(monkeypatch, _agent_out(
        _prep(), [_run(exit_code=128, stderr="remote: Invalid username or password.")],
        rounds=2,
    ))
    out = execution(_base_state())
    digest = coding_module._digest_execution_feedback(out["execution_result"])
    assert digest["error_category"] == "credential_required"
