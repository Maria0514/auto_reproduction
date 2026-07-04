"""Sprint 4 任务 E4（AC-S4-05/14）：execution 侧 Q-C 复验 —— 回归守门新增断言。

覆盖 dev-plan §4 任务 E4 自测检查点 CP-E4-2（AC-S4-14 execution 场景落地证）：

    **真实 execution() 节点全链路**（区别于 CP-E2-5 只测 _run_execution_agent 层）：
    真实主图节点（含 E3 编排收尾七步）+ 真实 create_react_subgraph + 脚本 LLM +
    mock sandbox（run_in_venv 副作用计数）跑
        run_in_sandbox（真实副作用）→ request_user_input（interrupt#3 暂停）
        → Command(resume) → 再 run_in_sandbox → 收尾
    断言：
      1) resume 后 **pre-interrupt 的 sandbox 副作用恰为 1**（B2 门禁「工具历史
         不重放」在真实 execution 节点上成立，AC-S4-14 命门）；
      2) **收集器 / messages 合并通道在 resume 后结果完整**（R-S4-10：resume 重建
         收集器只有尾段，前段由子图 messages 回读补全——execution_result.logs 含
         全部两条 step、且 R-S4-10 WARNING 留痕非静默）；
      3) 稳定性连跑 3 次结论一致（interrupt/重跑幂等类判据，CP-E4-3 纪律）。

    另含 L-E4-01（credential 闭环判定口径，2026-07-04 已裁决）相关用例：
    B 档判定改为 **effective runs**（同命令 argv 精确匹配取最后一次，
    ``_effective_runs``）——
      - 边界锚定：resume 后失败命令未重试（不同命令一成一败）→ 仍 failure →
        interrupt#2（原 characterization 用例，语义更新后保留）；
      - 主叙事翻转：resume 后同 argv 重试 fetch 成功 → success 单回合闭环、
        无第二次 interrupt、logs 仍含 exit=128 失败证据；
      - 无 interrupt 的子图内同命令重试成功 → success（口径不依赖 interrupt 边界）。
    单元级边界（末次 stderr / argv 变体 / _effective_runs 本体）见
    tests/test_sprint4_le401_fix.py。

全离线（InMemorySaver + 脚本 LLM + mock run_in_venv），零 API 配额。
脚本 LLM 沿用 B2 范式：路由完全基于输入 messages 的 ToolMessage 计数（replay 安全）。
"""

from __future__ import annotations

import importlib
import json
import logging
import uuid
from pathlib import Path
from typing import Any, Dict, List

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

import config
from core import secrets_store
from core.state import ExecutionMode, GlobalState
from core.tools.interaction_tools import INTERRUPT_KIND_USER_INPUT

execution_module = importlib.import_module("core.nodes.execution")

from sandbox.local_venv import SandboxRunResult  # noqa: E402

INTERRUPT_KIND_DEV_LOOP = execution_module.INTERRUPT_KIND  # "dev_loop_failure"

_TOKEN = "ghp_SECRET_e4_token_0987654321"


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
    """`.secrets` / mask 落点隔离到 tmp_path（防命中真实 .secrets 缓存跳过 interrupt）。"""
    ws = tmp_path / "workspace"
    ws.mkdir()
    monkeypatch.setattr(config, "WORKSPACE_DIR", ws)
    return ws


@pytest.fixture(autouse=True)
def _no_artifacts(monkeypatch):
    monkeypatch.setattr(execution_module, "collect_artifacts", lambda *a, **k: [])


def _make_workdir_with_venv(tmp_path: Path, name: str = "wd") -> str:
    """带已建好 .venv 痕迹的 work_dir（run_in_sandbox 的 python_exe 确定性推导路径，
    使剧本无需 prepare_environment 且 resume 重建收集器后仍可解析解释器，R-S4-10）。"""
    wd = tmp_path / name
    (wd / ".venv" / "bin").mkdir(parents=True)
    (wd / ".venv" / "pyvenv.cfg").write_text("home = /usr\n")
    return str(wd)


def _base_state(work_dir: str, **overrides: Any) -> Dict[str, Any]:
    state: Dict[str, Any] = {
        "llm_config_set": {"default": {"model": "test"}},
        "code_output_dir": work_dir,
        "reproduction_plan": {
            "execution_steps": [{"command": "python prep_data.py"},
                                {"command": "python train.py"}],
            "environment": {},
        },
        "paper_analysis": {"metrics": ["acc"]},
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


class CountingRunner:
    """run_in_venv mock：按命令名分桶计数（AC-S4-14「副作用恰为 1」的观测点）。

    跨 interrupt/resume 持久（模块级 patch 的同一实例），故计数覆盖节点整个生命周期。
    spec 值支持两种形态：dict（每次尝试同一结果）或 list[dict]（按第 N 次尝试取第 N 个，
    越界取末个——L-E4-01 剧本「首败次成」用）。
    """

    def __init__(self, specs: Dict[str, Any]) -> None:
        self.specs = specs  # 关键 token → {exit_code, stdout, stderr} 或其 list
        self.counts: Dict[str, int] = {}
        self.calls: List[List[str]] = []

    def __call__(self, python_exe, command, work_dir, *a, **k):
        self.calls.append(list(command))
        spec: Dict[str, Any] = {}
        for token, s in self.specs.items():
            if any(token in str(c) for c in command):
                self.counts[token] = self.counts.get(token, 0) + 1
                if isinstance(s, list):
                    spec = s[min(self.counts[token] - 1, len(s) - 1)]
                else:
                    spec = s
                break
        return SandboxRunResult(
            exit_code=spec.get("exit_code", 0),
            stdout=spec.get("stdout", ""),
            stderr=spec.get("stderr", ""),
            duration_seconds=0.1,
            timed_out=False,
            output_truncated=False,
            command=list(command),
        )


class GateScriptLLM(BaseChatModel):
    """CP-E4-2 剧本（B2 范式：路由基于 ToolMessage 名称计数，replay 安全）。

    mode="param":            run(prep_data) → request_user_input(非敏感参数) → run(train) → 收尾
    mode="credential":       run(fetch, 认证失败) → request_user_input(敏感凭证) → run(train) → 收尾
                             （fetch 未重试——L-E4-01 新口径下的「不同命令一成一败」边界剧本）
    mode="credential_retry": run(fetch, 认证失败) → request_user_input(敏感凭证)
                             → run(fetch 同 argv 重试成功) → run(train) → 收尾
                             （L-E4-01 裁决主叙事：凭证闭环单回合 success）
    mode="inline_retry":     run(fetch 失败) → run(fetch 同 argv 重试成功) → run(train) → 收尾
                             （无任何 interrupt——证明 effective 口径不依赖 interrupt 边界）
    """

    mode: str

    @property
    def _llm_type(self) -> str:
        return "e4-gate-scripted"

    def bind_tools(self, tools: Any, **kwargs: Any) -> "GateScriptLLM":
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        n_run = sum(
            1 for m in messages
            if isinstance(m, ToolMessage) and getattr(m, "name", None) == "run_in_sandbox"
        )
        n_req = sum(
            1 for m in messages
            if isinstance(m, ToolMessage) and getattr(m, "name", None) == "request_user_input"
        )

        def _call(name: str, args: Dict[str, Any], cid: str) -> AIMessage:
            return AIMessage(content="", tool_calls=[
                {"name": name, "args": args, "id": cid, "type": "tool_call"},
            ])

        def _final(steps: int, all_zero: bool) -> AIMessage:
            body = json.dumps({
                "steps_attempted": steps, "all_exit_zero": all_zero,
                "summary": "用户补充信息后重试完成", "notes": None,
            }, ensure_ascii=False, sort_keys=True)
            return AIMessage(content=(
                f"{config.REACT_RESULT_TAG_OPEN}{body}{config.REACT_RESULT_TAG_CLOSE}"
            ))

        if self.mode == "inline_retry":
            if n_run == 0:
                ai = _call("run_in_sandbox", {"command": "python fetch.py"}, "c_run1")
            elif n_run == 1:
                ai = _call("run_in_sandbox", {"command": "python fetch.py"}, "c_run2")
            elif n_run == 2:
                ai = _call("run_in_sandbox", {"command": "python train.py"}, "c_run3")
            else:
                ai = _final(3, True)
            return ChatResult(generations=[ChatGeneration(message=ai)])

        if self.mode == "credential_retry":
            if n_run == 0:
                ai = _call("run_in_sandbox", {"command": "python fetch.py"}, "c_run1")
            elif n_req == 0:
                ai = _call("request_user_input", {
                    "question": "克隆私有仓库需要 git token，请提供",
                    "is_sensitive": True, "purpose_key": "",
                }, "c_rui")
            elif n_run == 1:
                ai = _call("run_in_sandbox", {"command": "python fetch.py"}, "c_run2")
            elif n_run == 2:
                ai = _call("run_in_sandbox", {"command": "python train.py"}, "c_run3")
            else:
                ai = _final(3, True)
            return ChatResult(generations=[ChatGeneration(message=ai)])

        first_cmd = "python prep_data.py" if self.mode == "param" else "python fetch.py"
        if n_run == 0:
            ai = _call("run_in_sandbox", {"command": first_cmd}, "c_run1")
        elif n_run == 1 and n_req == 0:
            if self.mode == "param":
                ai = _call("request_user_input", {
                    "question": "训练需要 batch_size 参数，请提供",
                    "is_sensitive": False, "purpose_key": "",
                }, "c_rui")
            else:
                ai = _call("request_user_input", {
                    "question": "克隆私有仓库需要 git token，请提供",
                    "is_sensitive": True, "purpose_key": "",
                }, "c_rui")
        elif n_run == 1 and n_req == 1:
            ai = _call("run_in_sandbox", {"command": "python train.py"}, "c_run2")
        else:
            body = json.dumps({
                "steps_attempted": 2, "all_exit_zero": self.mode == "param",
                "summary": "用户补充信息后重试完成", "notes": None,
            }, ensure_ascii=False, sort_keys=True)
            ai = AIMessage(content=(
                f"{config.REACT_RESULT_TAG_OPEN}{body}{config.REACT_RESULT_TAG_CLOSE}"
            ))
        return ChatResult(generations=[ChatGeneration(message=ai)])


def _build_self_loop_graph(checkpointer):
    """sp3 CP-C3-7 同款最小 self-loop 图（真实 execution 节点 + D1 关键分支）。"""
    g = StateGraph(GlobalState)
    g.add_node("execution", execution_module.execution)
    g.add_edge(START, "execution")

    def route(state: Dict[str, Any]) -> str:
        if state.get("_dev_loop_route") == execution_module._ROUTE_AWAIT_INTERRUPT:
            return "execution"
        return "end"

    g.add_conditional_edges("execution", route, {"execution": "execution", "end": END})
    return g.compile(checkpointer=checkpointer)


def _wire(monkeypatch, runner: CountingRunner, llm: GateScriptLLM) -> None:
    monkeypatch.setattr(execution_module, "run_in_venv", runner)
    monkeypatch.setattr(execution_module, "resolve_llm_config", lambda cfg, node: cfg)
    monkeypatch.setattr(execution_module, "create_llm", lambda cfg: llm)


def _interrupt_values(graph, cfg) -> List[Dict[str, Any]]:
    snap = graph.get_state(cfg)
    return [
        iv.value for task in (snap.tasks or [])
        for iv in (getattr(task, "interrupts", None) or [])
    ]


# ===========================================================================
# CP-E4-2 主证：真实 execution 节点上 interrupt#3 resume 副作用恰为 1 + 合并完整
# ===========================================================================


def _run_gate_scenario(monkeypatch, tmp_path, caplog, name: str):
    """跑一次完整 param 剧本，返回 (runner, final_state_values, paused_out)。"""
    wd = _make_workdir_with_venv(tmp_path, name)
    runner = CountingRunner({
        "prep_data.py": {"exit_code": 0, "stdout": "prep data done (full fidelity)"},
        "train.py": {"exit_code": 0, "stdout": '<METRICS>{"acc": 0.9}</METRICS>'},
    })
    _wire(monkeypatch, runner, GateScriptLLM(mode="param"))

    graph = _build_self_loop_graph(InMemorySaver())
    cfg = {"configurable": {"thread_id": f"e4-gate-{name}-{uuid.uuid4().hex[:8]}"}}

    # 第一段：run(prep_data) → request_user_input → interrupt#3 暂停主图。
    paused = graph.invoke(_base_state(wd), cfg)
    assert "__interrupt__" in paused, "request_user_input 必须以 interrupt#3 暂停主图"
    ivs = _interrupt_values(graph, cfg)
    assert ivs and ivs[0]["interrupt_kind"] == INTERRUPT_KIND_USER_INPUT
    assert ivs[0]["is_sensitive"] is False
    # 暂停时：pre-interrupt 副作用恰 1、第二条命令未执行。
    assert runner.counts.get("prep_data.py") == 1, \
        f"暂停前 prep_data 应恰执行 1 次，实际 {runner.counts}"
    assert runner.counts.get("train.py") is None

    # 第二段：resume 注入参数 → 再 run(train) → 收尾（节点函数体重跑，子图从 checkpoint 恢复）。
    with caplog.at_level(logging.WARNING, logger="core.nodes.execution"):
        final = graph.invoke(Command(resume={"value": "128", "remember": False}), cfg)
    assert "__interrupt__" not in final, "param 剧本收尾成功后不应再暂停"

    # —— AC-S4-14 命门：resume 后 pre-interrupt 副作用仍恰为 1（工具历史不重放）。
    assert runner.counts.get("prep_data.py") == 1, \
        f"resume 后 pre-interrupt sandbox 副作用必须恰为 1，实际 {runner.counts}"
    assert runner.counts.get("train.py") == 1
    assert len(runner.calls) == 2, f"run_in_venv 总调用应恰 2 次，实际 {runner.calls}"

    values = graph.get_state(cfg).values
    return runner, values, caplog


def test_cp_e4_2_interrupt3_resume_sandbox_side_effect_exactly_once(
    monkeypatch, tmp_path, caplog,
):
    """AC-S4-14 execution 场景落地证（B2 结论在真实 execution() 节点上成立）。"""
    runner, values, caplog = _run_gate_scenario(monkeypatch, tmp_path, caplog, "main")

    # —— 收集器 / messages 合并通道完整性（R-S4-10）：
    # resume 重建收集器只含尾段（train），前段（prep_data）由子图 messages 回读补全；
    # execution_result.logs 必须含完整两条 step（缺前段 = 合并通道回归）。
    er = values["execution_result"]
    logs = er["logs"]
    assert "prep_data.py" in logs, \
        f"R-S4-10 合并通道回归：pre-interrupt run 未出现在 execution_result.logs：{logs[:400]}"
    assert "train.py" in logs
    assert "[step#0" in logs and "[step#1" in logs, "两条 run 应完整聚合为 step#0/#1"
    # 合并路径 WARNING 留痕（非静默，_merge_with_collector R-S4-10 分支）。
    assert any("收集器缺失前段" in r.message for r in caplog.records), \
        "R-S4-10 合并必须 WARNING 留痕"

    # —— E3 编排收尾：merged 全 exit 0 + 档 1 指标 → B 档成功（收尾链完整走通）。
    assert er["success"] is True
    assert er["metrics"] == {"acc": 0.9}
    assert values.get("_dev_loop_route") in (None, ""), "成功路径不进修复循环"
    # 预算扣减发生（子图真实 rounds > 0，落点 B 单点扣减生效——精确额由 CP-E3-1 覆盖）。
    assert values["retry_budget_remaining"] < 40


def test_cp_e4_2_flow_stable_across_3_runs(monkeypatch, tmp_path, caplog):
    """interrupt/重跑幂等类判据：同剧本连跑 3 次结论一致（CP-E4-3 纪律内嵌）。"""
    for i in range(3):
        caplog.clear()
        runner, values, _ = _run_gate_scenario(monkeypatch, tmp_path, caplog, f"r{i}")
        assert runner.counts == {"prep_data.py": 1, "train.py": 1}, \
            f"第 {i + 1} 次连跑副作用计数漂移：{runner.counts}"
        assert values["execution_result"]["success"] is True


# ===========================================================================
# L-E4-01（已裁决 2026-07-04）：credential 闭环判定口径 —— effective runs
# （同命令 argv 精确匹配取最后一次）。边界锚定 + 主叙事翻转两条用例。
# ===========================================================================


def test_le401_characterization_credential_inline_retry_still_judged_failure(
    monkeypatch, tmp_path,
):
    """【边界锚定，L-E4-01 裁决后语义】本剧本 fetch(exit 128) → interrupt#3 → resume 后
    agent **未重试 fetch** 直接跑 train(exit 0)——effective runs 口径（同命令 argv 精确
    匹配取最后一次）下，fetch 的最后一次尝试仍是 exit 128，属「不同命令一成一败」，
    B 档判定**仍为 failure** → credential_required → interrupt#2 再次问询。

    这不是裁决前的旧张力：新口径只在「同命令重试成功」时闭环 success（见下方
    test_le401_fix_* 主叙事翻转用例）；agent 拿到凭证却不重试失败命令时，再次问询
    是正确语义（凭证问题未被证明已解决）。AC-S4-14（副作用恰 1）在本场景同时断言。
    """
    wd = _make_workdir_with_venv(tmp_path, "cred")
    runner = CountingRunner({
        "fetch.py": {"exit_code": 128,
                     "stderr": f"fatal: Authentication failed for 'https://u:{_TOKEN}@github.com/x'"},
        "train.py": {"exit_code": 0, "stdout": '<METRICS>{"acc": 0.9}</METRICS>'},
    })
    _wire(monkeypatch, runner, GateScriptLLM(mode="credential"))

    graph = _build_self_loop_graph(InMemorySaver())
    cfg = {"configurable": {"thread_id": f"e4-cred-{uuid.uuid4().hex[:8]}"}}

    paused = graph.invoke(_base_state(wd), cfg)
    assert "__interrupt__" in paused
    ivs = _interrupt_values(graph, cfg)
    assert ivs[0]["interrupt_kind"] == INTERRUPT_KIND_USER_INPUT
    assert ivs[0]["is_sensitive"] is True
    assert runner.counts.get("fetch.py") == 1

    out2 = graph.invoke(Command(resume={"value": _TOKEN, "remember": False}), cfg)

    # AC-S4-14 部分在 credential 场景同样成立：认证失败 run 不重放（恰 1 次）。
    assert runner.counts.get("fetch.py") == 1, \
        f"resume 后认证失败 run 必须恰为 1（不重放），实际 {runner.counts}"
    assert runner.counts.get("train.py") == 1

    # —— 边界锚定（L-E4-01 裁决后）：fetch 未重试 → 其末次尝试仍 exit 128 →
    #    effective runs 非全 0 → failure → interrupt#2 再次问询（正确语义）。
    assert "__interrupt__" in out2, \
        "L-E4-01 边界：凭证到手但失败命令未重试，应仍触发 interrupt#2"
    ivs2 = _interrupt_values(graph, cfg)
    assert ivs2 and ivs2[0]["interrupt_kind"] == INTERRUPT_KIND_DEV_LOOP
    assert ivs2[0]["error_category"] == "credential_required"

    values = graph.get_state(cfg).values
    er = values["execution_result"]
    assert er["success"] is False, \
        "L-E4-01 边界：fetch 末次尝试 exit 128（未重试）→ effective 非全 0 → failure"
    # credential 不耗 fix_loop_count（CP-E3-2 契约在端到端场景保持）。
    assert values.get("fix_loop_count", 0) == 0
    # AC-S4-11：凭证明文不进 logs / payload（resume 值已 register_sensitive_value）。
    assert _TOKEN not in er["logs"]
    assert _TOKEN not in json.dumps(ivs2[0], ensure_ascii=False, default=str)

    # 收尾：terminate 三态 resume 仍走通（sp3 契约零回归）。
    graph.invoke(Command(resume={"decision": "terminate"}), cfg)
    final = graph.get_state(cfg).values
    assert final["user_fix_decision"] == "terminate"
    assert runner.counts.get("fetch.py") == 1, "interrupt#2 resume 重跑同样不重放 sandbox"


def test_le401_fix_credential_inline_retry_success_single_round(
    monkeypatch, tmp_path,
):
    """L-E4-01 裁决主叙事翻转：agent 经 interrupt#3 拿到凭证后**就地重试 fetch
    （同 argv，exit 0）** 再跑 train（exit 0）→ effective runs 全 0 + 指标 →
    success=True 单回合闭环，**无第二次 interrupt**；logs 聚合仍用全量序列，
    exit=128 失败证据不丢（架构 §9.1 凭证闭环叙事由此成立）。
    """
    wd = _make_workdir_with_venv(tmp_path, "cred_fix")
    runner = CountingRunner({
        "fetch.py": [
            {"exit_code": 128,
             "stderr": f"fatal: Authentication failed for 'https://u:{_TOKEN}@github.com/x'"},
            {"exit_code": 0, "stdout": "fetch ok with token"},
        ],
        "train.py": {"exit_code": 0, "stdout": '<METRICS>{"acc": 0.9}</METRICS>'},
    })
    _wire(monkeypatch, runner, GateScriptLLM(mode="credential_retry"))

    graph = _build_self_loop_graph(InMemorySaver())
    cfg = {"configurable": {"thread_id": f"e4-credfix-{uuid.uuid4().hex[:8]}"}}

    paused = graph.invoke(_base_state(wd), cfg)
    assert "__interrupt__" in paused
    ivs = _interrupt_values(graph, cfg)
    assert ivs[0]["interrupt_kind"] == INTERRUPT_KIND_USER_INPUT
    assert ivs[0]["is_sensitive"] is True
    assert runner.counts.get("fetch.py") == 1

    final = graph.invoke(Command(resume={"value": _TOKEN, "remember": False}), cfg)

    # —— 主断言：单回合 success 闭环，无第二次 interrupt（不再重复问询凭证）。
    assert "__interrupt__" not in final, \
        "L-E4-01 修复后：同命令重试成功应单回合闭环，不得再触发 interrupt#2"

    # AC-S4-14 不回归：pre-interrupt 失败 run 不重放，重试是新调用（fetch 恰 2 次）。
    assert runner.counts == {"fetch.py": 2, "train.py": 1}, \
        f"副作用计数应为 fetch×2 + train×1，实际 {runner.counts}"

    values = graph.get_state(cfg).values
    er = values["execution_result"]
    assert er["success"] is True, "effective runs（fetch 末次 + train）全 0 + 指标 → success"
    assert er["metrics"] == {"acc": 0.9}
    assert values.get("_dev_loop_route") in (None, ""), "成功路径不进修复循环"
    assert values.get("fix_loop_count", 0) == 0

    # logs 仍用全量序列：exit=128 失败证据保留（三条 step 完整聚合）。
    logs = er["logs"]
    assert "exit=128" in logs, f"失败证据（exit=128）不得被 effective 过滤掉出 logs：{logs[:400]}"
    assert "[step#0" in logs and "[step#1" in logs and "[step#2" in logs
    # AC-S4-11：凭证明文不进 logs。
    assert _TOKEN not in logs


def test_le401_fix_inline_retry_without_interrupt_success(monkeypatch, tmp_path):
    """L-E4-01 边界③：**无任何 interrupt** 的子图内同命令重试成功 → success。

    证明 effective runs 口径是判定层通用语义，不依赖 interrupt/resume 边界
    （agent 自主重试瞬态失败同样单回合闭环）。
    """
    wd = _make_workdir_with_venv(tmp_path, "inline")
    runner = CountingRunner({
        "fetch.py": [
            {"exit_code": 1, "stderr": "ConnectionResetError: [Errno 104] peer reset"},
            {"exit_code": 0, "stdout": "fetch ok on retry"},
        ],
        "train.py": {"exit_code": 0, "stdout": '<METRICS>{"acc": 0.9}</METRICS>'},
    })
    _wire(monkeypatch, runner, GateScriptLLM(mode="inline_retry"))

    graph = _build_self_loop_graph(InMemorySaver())
    cfg = {"configurable": {"thread_id": f"e4-inline-{uuid.uuid4().hex[:8]}"}}

    final = graph.invoke(_base_state(wd), cfg)
    assert "__interrupt__" not in final, "无 interrupt 剧本应一次跑完"

    assert runner.counts == {"fetch.py": 2, "train.py": 1}
    er = graph.get_state(cfg).values["execution_result"]
    assert er["success"] is True, \
        "子图内同 argv 重试成功（无 interrupt）→ effective 全 0 → success"
    assert er["metrics"] == {"acc": 0.9}
    assert "exit=1" in er["logs"], "首败证据仍留在 logs（全量聚合）"


if __name__ == "__main__":
    import sys

    sys.exit(pytest.main([__file__, "-v"]))
