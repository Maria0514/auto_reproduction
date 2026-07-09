"""Sprint 5 任务 T-S5-1-4（S5-06/10 前置）：execution prompt/工具 schema 静态批次（P3~P6）单测。

覆盖 dev-plan 批次 1 任务 T-S5-1-4 自测检查点（architecture §9.1 P3~P6）：
    - CP-1.4-1 主体字节级一致断言（CP-E2-1 同款，本文件内新写）+ 主体无
      "max_rounds=10" 数字残留 + step_index 用法说明存在（P3 / P4）；
    - CP-1.4-2 `run_in_sandbox` docstring（= 工具 schema，进 Prompt Cache 前缀）
      字节稳定、零工厂入参动态变量 + `step_index` 缺省 -1 向后兼容
      （不带参调用不炸、台账记 -1）（P5）；
    - CP-1.4-3 prepare payload 含 `env_info`（含 key_packages）且 JSON 合法
      （sort_keys / ensure_ascii=False / 禁 str(dict)，BUG-S1-02 自查）（P6）；
    - CP-1.4-4 HumanMessage 动态上下文含预算数字键（max_rounds）且 sort_keys
      字节幂等（P3 动态通道，R-PC4）；`interaction_tools.request_user_input`
      docstring 零字节改动（CP-B1-5 守门沿用，只读断言，不改 interaction 文件）。

全离线（mock sandbox + 捕获式假子图 + 假 LLM 对象），零 API 配额。
装配捕获 / 工具构造范式沿用 tests/test_sprint4_e2.py / test_sprint4_e1.py。
"""

from __future__ import annotations

import importlib
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytest

import config
from core import secrets_store
from core.tools.interaction_tools import request_user_input
from langchain_core.messages import HumanMessage, SystemMessage
from sandbox.local_venv import SandboxPrepareResult, SandboxRunResult

execution_module = importlib.import_module("core.nodes.execution")


# ---------------------------------------------------------------------------
# fixtures / helpers（沿用 test_sprint4_e2 / e1 范式）
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


def _base_state(**overrides: Any) -> Dict[str, Any]:
    state: Dict[str, Any] = {
        "llm_config_set": {"default": {"model": "test"}},
        "fix_loop_count": 0,
    }
    state.update(overrides)
    return state


def _plan(steps: Optional[List[Any]] = None) -> Dict[str, Any]:
    return {
        "execution_steps": steps if steps is not None else [
            {"command": "python train.py"}, {"command": "python eval.py"},
        ],
        "environment": {"dependencies": ["numpy"]},
    }


class _CapturingSubgraph:
    """假子图：捕获 initial state 后返回固定收尾（LLM 不会被真正调用）。"""

    def __init__(self, capture: Dict[str, Any]):
        self._capture = capture

    def invoke(self, initial: Dict[str, Any]) -> Dict[str, Any]:
        self._capture["initial"] = initial
        return {"round": 2, "messages": list(initial["messages"]),
                "result": {}, "status": "done"}


def _capture_assembly(monkeypatch, state, work_dir, plan) -> Dict[str, Any]:
    """跑一次 _run_execution_agent，捕获装配产物（system prompt / messages）。"""
    capture: Dict[str, Any] = {}
    monkeypatch.setattr(execution_module, "resolve_llm_config", lambda cfg, node: cfg)
    monkeypatch.setattr(execution_module, "create_llm", lambda cfg: object())

    def fake_factory(node_name, system_prompt, tools, max_rounds, result_schema=None):
        capture.update(node_name=node_name, system_prompt=system_prompt,
                       tools=list(tools), max_rounds=max_rounds)
        return _CapturingSubgraph(capture)

    monkeypatch.setattr(execution_module, "create_react_subgraph", fake_factory)
    execution_module._run_execution_agent(state, work_dir, plan)
    return capture


class RecordingRunner:
    """按序返回预设 SandboxRunResult 的 run_in_venv mock（e1 同款精简版）。"""

    def __init__(self, exit_codes: Optional[List[int]] = None) -> None:
        self.calls: List[List[str]] = []
        self._codes = list(exit_codes or [])

    def __call__(self, python_exe: str, command: List[str], work_dir: str, *a: Any, **k: Any):
        self.calls.append(list(command))
        i = len(self.calls) - 1
        return SandboxRunResult(
            exit_code=self._codes[i] if i < len(self._codes) else 0,
            stdout="ok", stderr="", duration_seconds=0.1,
            timed_out=False, output_truncated=False, command=list(command),
        )


def _run_tool(work_dir: str, monkeypatch, exit_codes: Optional[List[int]] = None):
    """构造 run_in_sandbox 工具（python_exe 经 ref 显式提供，绕开 prepare 依赖）。"""
    runner = RecordingRunner(exit_codes=exit_codes)
    monkeypatch.setattr(execution_module, "run_in_venv", runner)
    collector = execution_module._SandboxRunCollector()
    ref: Dict[str, Optional[str]] = {"python_exe": str(Path(work_dir) / ".venv" / "bin" / "python")}
    t = execution_module.make_run_in_sandbox_tool(work_dir, collector, None, ref)
    return t, collector, runner


def _assert_sorted_json(raw: str) -> Dict[str, Any]:
    """断言 raw 是合法 JSON 且 sort_keys 序列化字节幂等（BUG-S1-02 治理，禁 str(dict)）。"""
    parsed = json.loads(raw)  # 单引号 repr 在此必炸
    assert isinstance(parsed, dict)
    assert raw == json.dumps(parsed, ensure_ascii=False, sort_keys=True, default=str), \
        "工具返回必须 json.dumps(ensure_ascii=False, sort_keys=True) 字节级幂等"
    return parsed


# ===========================================================================
# CP-1.4-1 主体字节级一致 + 无数字残留 + step_index 说明（P3 / P4）
# ===========================================================================


def test_cp_1_4_1_system_message_byte_identical_across_tasks(monkeypatch, tmp_path):
    """CP-E2-1 同款（本文件内新写）：不同任务 state 的整条 SystemMessage 字节级一致，
    去尾部段落后 == 主体常量；主体内零任务级动态变量。"""
    cap_a = _capture_assembly(
        monkeypatch, _base_state(), str(tmp_path / "wd_a"),
        _plan([{"command": "python a.py"}]),
    )
    cap_b = _capture_assembly(
        monkeypatch,
        _base_state(fix_loop_count=2, execution_result={
            "success": False, "errors": ["[error_category=runtime] 运行时异常"],
            "logs": "Traceback ...",
        }),
        str(tmp_path / "wd_b"),
        _plan([{"command": "python b.py --seed 42"}]),
    )
    sys_a = cap_a["initial"]["messages"][0]
    sys_b = cap_b["initial"]["messages"][0]
    assert isinstance(sys_a, SystemMessage) and isinstance(sys_b, SystemMessage)
    assert sys_a.content == sys_b.content, "不同任务的 SystemMessage 必须字节级一致"
    head = sys_a.content.split("\n--- 当前任务上下文 ---\n")[0]
    assert head == execution_module._EXECUTION_SYSTEM_PROMPT_BODY
    # 主体常量内零动态变量物证：不含 work_dir / 预算数字注入痕迹。
    assert str(tmp_path) not in sys_a.content


def test_cp_1_4_1_no_numeric_max_rounds_in_body():
    """P3：主体无 "max_rounds=10" 数字残留——预算数字一律走 HumanMessage 动态通道。"""
    body = execution_module._EXECUTION_SYSTEM_PROMPT_BODY
    assert "max_rounds=10" not in body
    assert re.search(r"max_rounds\s*=\s*\d", body) is None, \
        "主体不得写死任何 max_rounds 数字（R-PC4：动态值走动态通道）"
    # 非数字表述仍保留预算意识指引，且明确指向 HumanMessage 动态上下文。
    assert "max_rounds" in body
    assert "HumanMessage" in body


def test_cp_1_4_1_step_index_usage_line_exists():
    """P4：主体含 step_index 用法一行说明（"执行计划第 i 步时以 step_index=i 声明归属"）。"""
    body = execution_module._EXECUTION_SYSTEM_PROMPT_BODY
    assert "step_index" in body
    assert "step_index=i" in body
    assert "声明归属" in body or "声明该命令归属" in body


# ===========================================================================
# CP-1.4-2 run_in_sandbox docstring 字节稳定 + step_index 缺省 -1 向后兼容（P5）
# ===========================================================================


def test_cp_1_4_2_run_docstring_byte_stable_and_static(monkeypatch, tmp_path):
    """docstring = 工具 schema description，进 Prompt Cache 前缀：两次构造
    （不同 work_dir / collector）字节级一致，且不含任何工厂入参动态变量。"""
    wd1, wd2 = str(tmp_path / "wd1"), str(tmp_path / "wd2")
    t1, _, _ = _run_tool(wd1, monkeypatch)
    t2, _, _ = _run_tool(wd2, monkeypatch)
    assert t1.description == t2.description, "docstring 不得含工厂入参等动态变量"
    assert wd1 not in t1.description and wd2 not in t1.description
    assert re.search(r"\d{4,}", t1.description) is None, "docstring 内不得混入长数字动态值"
    # step_index 用法说明进 schema（P5 静态说明，零动态变量）。
    assert "step_index" in t1.description
    # 参数确实进了 args schema（LLM 可见）。
    assert "step_index" in t1.args


def test_cp_1_4_2_default_step_index_backward_compat(monkeypatch, tmp_path):
    """不带 step_index 调用不炸（既有调用面向后兼容），台账记 -1。"""
    t, collector, runner = _run_tool(str(tmp_path / "wd"), monkeypatch)
    raw = t.invoke({"command": "python a.py"})
    parsed = _assert_sorted_json(raw)
    assert parsed["exit_code"] == 0
    assert len(runner.calls) == 1
    # 台账雏形：(step_index, command, exit_code)，未声明 → -1。
    assert len(collector.step_ledger) == 1
    step_index, command, exit_code = collector.step_ledger[0]
    assert step_index == -1
    assert exit_code == 0
    assert command == runner.calls[0]
    # run_results 既有通道不受影响。
    assert len(collector.run_results) == 1


def test_cp_1_4_2_declared_step_index_recorded_per_subcommand(monkeypatch, tmp_path):
    """声明 step_index=1 → 复合命令逐子命令台账均记 1，exit_code 为真实值。"""
    t, collector, _ = _run_tool(str(tmp_path / "wd"), monkeypatch, exit_codes=[0, 3])
    raw = t.invoke({"command": "python a.py ; python b.py", "step_index": 1})
    parsed = _assert_sorted_json(raw)
    assert parsed["exit_code"] == 3
    assert [e[0] for e in collector.step_ledger] == [1, 1]
    assert [e[2] for e in collector.step_ledger] == [0, 3]


def test_cp_1_4_2_ledger_not_written_on_unprepared_env(monkeypatch, tmp_path):
    """环境未准备（无 python_exe）→ 结构化错误，台账零条目（只记真实执行）。"""
    runner = RecordingRunner()
    monkeypatch.setattr(execution_module, "run_in_venv", runner)
    collector = execution_module._SandboxRunCollector()
    t = execution_module.make_run_in_sandbox_tool(
        str(tmp_path / "wd_noprep"), collector, None, None,
    )
    parsed = _assert_sorted_json(t.invoke({"command": "python a.py", "step_index": 0}))
    assert parsed.get("tool_error") is True
    assert collector.step_ledger == []
    assert runner.calls == []


# ===========================================================================
# CP-1.4-3 prepare payload 含 env_info 且 JSON 合法（P6）
# ===========================================================================


def test_cp_1_4_3_prepare_payload_env_info_json_legal(monkeypatch, tmp_path):
    """payload 增带 env_info（含 key_packages）；整条返回 sort_keys/ensure_ascii=False
    字节幂等（禁 str(dict)，BUG-S1-02 自查）。"""
    env_info = {
        "python_version": "Python 3.11.9",
        "key_packages": "numpy==1.26.0, torch==2.1.0",
    }
    prep = SandboxPrepareResult(
        success=True, venv_dir=str(tmp_path / "wd" / ".venv"),
        python_exe=str(tmp_path / "wd" / ".venv" / "bin" / "python"),
        pip_exe="", env_info=dict(env_info), install_log="ok",
        install_failed_packages=[], error=None,
    )
    monkeypatch.setattr(execution_module, "prepare_venv", lambda **kw: prep)
    collector = execution_module._SandboxRunCollector()
    t = execution_module.make_prepare_environment_tool(
        str(tmp_path / "wd"), _plan(), collector,
    )
    raw = t.invoke({})
    parsed = _assert_sorted_json(raw)
    assert parsed["success"] is True
    assert parsed["env_info"] == env_info
    assert "key_packages" in parsed["env_info"]
    # 收集器仍收到真实 dataclass（R-S4-01 通道不受扰）。
    assert collector.prep_results == [prep]


def test_cp_1_4_3_prepare_payload_env_info_empty_safe(monkeypatch, tmp_path):
    """env_info 为空 dict 时 payload 键仍存在（消费侧 T-S5-2-6 可无条件 .get）。"""
    prep = SandboxPrepareResult(
        success=True, venv_dir="", python_exe="/x/python", pip_exe="",
        env_info={}, install_log="", install_failed_packages=[], error=None,
    )
    monkeypatch.setattr(execution_module, "prepare_venv", lambda **kw: prep)
    t = execution_module.make_prepare_environment_tool(
        str(tmp_path / "wd"), {}, execution_module._SandboxRunCollector(),
    )
    parsed = _assert_sorted_json(t.invoke({}))
    assert parsed["env_info"] == {}


# ===========================================================================
# CP-1.4-4 HumanMessage 预算数字键 + sort_keys 幂等；request_user_input docstring 守门
# ===========================================================================


def test_cp_1_4_4_human_message_budget_key_and_idempotent(monkeypatch, tmp_path):
    """动态上下文含 max_rounds 数字键（暂取 REACT_MAX_ROUNDS_EXECUTION，
    T-S5-2-5 切 _effective_max_rounds），同一 state 两次装配字节幂等。"""
    state = _base_state()
    wd = str(tmp_path / "wd")
    plan = _plan()
    cap1 = _capture_assembly(monkeypatch, state, wd, plan)
    cap2 = _capture_assembly(monkeypatch, state, wd, plan)
    h1 = cap1["initial"]["messages"][1]
    h2 = cap2["initial"]["messages"][1]
    assert isinstance(h1, HumanMessage)
    assert h1.content == h2.content, "同一 state 两次装配的 HumanMessage 必须字节级幂等"
    # 内容 = 动态上下文的 sort_keys 稳定序列化。
    expected = json.dumps(
        execution_module._build_execution_agent_context(state, wd, plan),
        ensure_ascii=False, sort_keys=True, default=str,
    )
    assert h1.content == expected
    payload = json.loads(h1.content)
    assert payload["max_rounds"] == config.REACT_MAX_ROUNDS_EXECUTION
    assert isinstance(payload["max_rounds"], int), "预算须是数字（agent 直接消费）"


def test_cp_1_4_4_budget_number_only_in_dynamic_channel(monkeypatch, tmp_path):
    """预算数字只出现在 HumanMessage（动态通道），SystemMessage 不含注入痕迹（R-PC4）。"""
    cap = _capture_assembly(monkeypatch, _base_state(), str(tmp_path / "wd"), _plan())
    sys_msg = cap["initial"]["messages"][0]
    human = cap["initial"]["messages"][1]
    assert f'"max_rounds": {config.REACT_MAX_ROUNDS_EXECUTION}' in human.content
    assert re.search(r"max_rounds\s*[=:]\s*\d", sys_msg.content) is None


# CP-B1-5 守门沿用：request_user_input 的 docstring（= 工具 schema description，
# 参与三节点 Prompt Cache 稳定前缀）逐字节锚定，T-S5-1-4 批次零改动。
# 锚定文本与 tests/test_sprint4_b1.py::_EXPECTED_TOOL_DESCRIPTION 一致（只读断言）。
_EXPECTED_RUI_DESCRIPTION = (
    "当缺少继续任务所需的信息（凭证 / 参数 / 决策 / 路径）时，向用户索要一条信息。\n"
    "\n"
    "    仅在确实无法从已有上下文推断、且信息缺失会阻塞任务时调用。一次只问一个信息项。\n"
    "    本工具会暂停任务等待用户回答：请单独一轮调用，不要与写文件 / 运行命令等\n"
    "    其他工具放在同一轮 tool_calls 中。\n"
    "\n"
    "    Args:\n"
    "        question: 给用户看的问题文本（中文叙述，URL/包名等事实层保留英文）。\n"
    "        is_sensitive: 凭证/密钥类置 True（UI 用 password 输入、全程脱敏、可「记住」）。\n"
    "        purpose_key: 信息项稳定标识（如 \"git_credential:github.com\" / \"hf_token\"），\n"
    "            用作 .secrets 的 key + 去重（同 key 命中已存则直接返回，不再打断用户）。\n"
    "\n"
    "    Returns:\n"
    "        用户输入的字符串值（敏感值不进 GlobalState 业务字段，日志/报告/UI 投影面统一脱敏）。"
)


def test_cp_1_4_4_request_user_input_docstring_untouched():
    """CP-B1-5 守门：本批次（P3~P6）不得触碰 request_user_input docstring 任何字节。"""
    assert request_user_input.description == _EXPECTED_RUI_DESCRIPTION
