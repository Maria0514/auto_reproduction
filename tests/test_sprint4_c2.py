"""Sprint 4 任务 C2：coding 节点挂载 run_command + request_user_input（S4-01/02）。

覆盖 dev-plan §4 任务 C2 CP-C2-1 ~ CP-C2-4（CP-C2-5 = 跑 sp3 c1 系列既有测试，
不在本文件内）。architecture §2.4（7 工具挂载）+ §5（Q-B1 边界进 prompt）+
§8（Q-C 复验：B2 结论在真实 coding wrapper 上复证）。

- CP-C2-1 工具集恰 7 个、名称集合断言；wrapper 形态不变（_make_react_wrapper
  产物 + _map_coding_result 3 参签名）
- CP-C2-2 prompt 主体字节级一致守门（沿用 sp3 CP-F3-1 断言）+ 新增段落确认
  进稳定前缀（run_command 边界 / request_user_input 单独一轮纪律）
- CP-C2-3 coding 真实 wrapper Q-C 复验（AC-S4-14 coding 侧）：write →
  request_user_input → resume 后文件写副作用恰为 1、文件内容正确、agent 拿到
  值继续收尾、map_result 契约不回归
- CP-C2-4 凭证注入链路：mock `.secrets` 含 git 凭证时 run_command 工厂收到的
  extra_env 含 GIT_ASKPASS + GIT_TERMINAL_PROMPT=0（经工厂闭包断言）

测试策略：全离线（InMemorySaver + 脚本 BaseChatModel），零 API 配额。
FakeLLM / 观测通道范式沿用 tests/test_sprint4_b2_interrupt3_idempotency.py：
脚本 LLM 纯数据字段（msgpack round-trip 安全）、路由完全基于输入 messages 的
ToolMessage 计数（replay 安全）；副作用观测走模块级 dict + 磁盘双通道
（resume 重跑节点体会重建工具闭包，闭包内计数器不可靠——R-S4-10 实证）。
"""

from __future__ import annotations

import importlib
import inspect
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from langchain_core.language_models.chat_models import BaseChatModel  # noqa: E402
from langchain_core.messages import AIMessage, ToolMessage  # noqa: E402
from langchain_core.outputs import ChatGeneration, ChatResult  # noqa: E402
from langchain_core.tools import tool  # noqa: E402
from langgraph.checkpoint.memory import InMemorySaver  # noqa: E402
from langgraph.graph import END, START, StateGraph  # noqa: E402
from langgraph.types import Command  # noqa: E402

import config  # noqa: E402
from core import secrets_store  # noqa: E402
from core.secrets_store import remember_secret  # noqa: E402
from core.state import GlobalState  # noqa: E402
from core.tools.interaction_tools import INTERRUPT_KIND_USER_INPUT  # noqa: E402

# core/nodes/__init__.py 把同名 callable 注册为包属性会遮蔽子模块引用
# （sp1 教训，CLAUDE 指令第 6 条）——用 importlib 拿真实模块对象。
coding_module = importlib.import_module("core.nodes.coding")

from core.nodes.coding import (  # noqa: E402
    NODE_NAME,
    _CODING_HONESTY_SECTION,
    _CODING_SYSTEM_PROMPT_BODY,
    _build_coding_system_prompt,
    _get_coding_tools,
    _map_coding_result,
    coding,
)

_RESUME_VALUE = "USERVAL-c2"

# 进程级副作用观测通道（跨 resume 重跑节点体存活；工具闭包会被重建，
# 闭包内计数器在 resume 后不可靠——B2 R-S4-10 实证）。
_WRITE_CALLS: Dict[str, int] = {"n": 0}


# ---------------------------------------------------------------------------
# fixtures（范式沿用 tests/test_sprint3_c1.py + test_sprint4_b2）
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_process_state():
    secrets_store._SENSITIVE_VALUES.clear()
    _WRITE_CALLS["n"] = 0
    yield
    secrets_store._SENSITIVE_VALUES.clear()


@pytest.fixture()
def workspace(tmp_path, monkeypatch) -> Path:
    """WORKSPACE_DIR 全落点隔离：code_fs_tools 越界基准 + coding 目录解析 +
    config（.secrets / GIT_ASKPASS 脚本 / run_command 越界基准）。"""
    import sandbox.local_venv as lv
    from core.tools import code_fs_tools

    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(config, "WORKSPACE_DIR", ws)
    monkeypatch.setattr(lv, "WORKSPACE_DIR", ws)
    monkeypatch.setattr(code_fs_tools, "WORKSPACE_DIR", ws)
    monkeypatch.setattr(coding_module, "WORKSPACE_DIR", ws)
    return ws


def _install_inert_deepxiv_mocks(monkeypatch) -> None:
    """read_section / web_search 换成惰性 mock（不依赖网络 / token；名称保持一致）。"""

    @tool
    def read_section(arxiv_id: str, section_name: str) -> str:
        """Mock read_section."""
        return f"section {section_name} content"

    @tool
    def web_search(query: str) -> str:
        """Mock web_search."""
        return "search result"

    monkeypatch.setattr(coding_module, "read_section_tool", lambda token=None: read_section)
    monkeypatch.setattr(coding_module, "web_search_tool", lambda token=None: web_search)


def _make_state(workspace: Path) -> Dict[str, Any]:
    return {
        "user_input": "2409.05591",
        "input_type": "arxiv_id",
        "paper_meta": {"arxiv_id": "2409.05591"},
        "paper_analysis": {
            "method_summary_en": "English method summary.",
            "datasets": ["WMT2014"],
            "framework": "PyTorch",
            "hardware_requirements_en": "8x P100 GPUs",
        },
        "resource_info": {"selected_repo": {"local_path": str(workspace / "repo")}},
        "reproduction_plan": {
            "code_strategy": "adapt official repo",
            "execution_steps": [{"name": "train", "cmd": "python run.py"}],
            "deliverables": ["run.py"],
            "environment": {"python": "3.11"},
        },
        "code_output_dir": None,
        "execution_result": None,
        "fix_loop_count": 0,
        "llm_config_set": {
            "default": {
                "base_url": "https://example.test/v1",
                "model": "test-model",
                "api_key": "sk-test",
                "temperature": 0.3,
                "max_tokens": 1024,
            },
            "overrides": {},
        },
        "retry_budget_remaining": 50,
        "node_errors": [],
        "degraded_nodes": [],
        "messages": [],
        "workspace_dir": str(workspace),
    }


_EXPECTED_TOOL_NAMES = {
    "write_code_file", "read_code_file", "list_dir",
    "read_section", "web_search",
    "run_command", "request_user_input",
}


# ===========================================================================
# CP-C2-1 工具集恰 7 个 + 名称集合；wrapper 形态零改动
# ===========================================================================


def test_cp_c2_1_seven_tools_with_exact_names(workspace: Path, monkeypatch):
    _install_inert_deepxiv_mocks(monkeypatch)
    tools = _get_coding_tools(_make_state(workspace))

    assert len(tools) == 7, f"AC-S4-01：coding 工具集恰 7 个，实为 {len(tools)}"
    names = {t.name for t in tools}
    assert names == _EXPECTED_TOOL_NAMES, f"名称集合不符: {names}"


def test_cp_c2_1_wrapper_form_unchanged():
    # coding 仍为 _make_react_wrapper 产物（命名约定 + 单形参 state）
    assert callable(coding)
    assert coding.__name__ == f"react_wrapper_{NODE_NAME}"
    assert list(inspect.signature(coding).parameters) == ["state"]
    # _map_coding_result 3 参签名不动
    params = list(inspect.signature(_map_coding_result).parameters)
    assert params == ["result", "state", "react_messages"], f"实为 {params}"


# ===========================================================================
# CP-C2-2 prompt 主体字节级一致守门 + 新增段落进稳定前缀
# ===========================================================================


def test_cp_c2_2_prompt_body_byte_identical_guard():
    """沿用 sp3 CP-F3-1 / CP-C1-6 断言：两份不同 context 渲染出的 SystemMessage
    去尾部动态段后主体 == 新 _CODING_SYSTEM_PROMPT_BODY 常量。"""
    sep = "\n--- 当前任务上下文 ---\n"
    ctx_a = {"code_strategy": "A", "selected_repo_local_path": "/x/a"}
    ctx_b = {"code_strategy": "B", "fix_round": 3,
             "last_error_summary": {"error_category": "import"}}
    body_a = _build_coding_system_prompt(ctx_a).split(sep)[0]
    body_b = _build_coding_system_prompt(ctx_b).split(sep)[0]
    assert body_a == body_b, "去尾部段落后主体应字节级一致"
    # sp5 T-S5-1-6：诚实红线段 _CODING_HONESTY_SECTION（T-S5-1-3）为静态段、
    # 同属稳定前缀，断言目标改为两常量拼接，语义不降。
    assert body_a == _CODING_SYSTEM_PROMPT_BODY + _CODING_HONESTY_SECTION, (
        "主体应等于 _CODING_SYSTEM_PROMPT_BODY + _CODING_HONESTY_SECTION"
        "（新增段进稳定前缀）"
    )


def test_cp_c2_2_new_tool_sections_in_stable_prefix():
    body = _CODING_SYSTEM_PROMPT_BODY
    # run_command：Q-B1 轻量验证边界（smoke 成功≠复现成功、重活交 execution）
    assert "run_command(command)" in body
    assert "run_command 使用边界" in body
    assert "禁止用它跑完整训练" in body
    assert "smoke 通过不等于复现成功" in body
    # request_user_input：缺信息才用 / 逐条问 / 单独一轮（B2 断言点 2 实证要求）
    assert "request_user_input(question, is_sensitive, purpose_key)" in body
    assert "request_user_input 使用纪律" in body
    assert "一次只问一个信息项" in body
    assert "【单独一轮】" in body


def test_cp_c2_2_no_dynamic_vars_in_body():
    for token in ("2409.05591", "arxiv_id=", "paper_meta",
                  "Attention Is All You Need", str(Path.home())):
        assert token not in _CODING_SYSTEM_PROMPT_BODY, \
            f"主体常量不应含动态变量 {token!r}"


# ===========================================================================
# CP-C2-3 coding 真实 wrapper Q-C 复验（AC-S4-14 coding 侧）
# ===========================================================================


class ScriptedCodingLLM(BaseChatModel):
    """三段式脚本（纯数据字段，msgpack round-trip 安全；路由基于输入 messages
    的 ToolMessage 计数，replay 安全——沿用 B2 范式）：
        0 条 ToolMessage → tool_calls=[write_code_file(run.py)]（副作用轮）
        1 条 ToolMessage → tool_calls=[request_user_input]（interrupt#3，单独一轮）
        ≥2 条 ToolMessage → <result> 收尾（引用 resume 值）
    """

    target_file: str

    @property
    def _llm_type(self) -> str:
        return "c2-scripted"

    def bind_tools(self, tools: Any, **kwargs: Any) -> "ScriptedCodingLLM":
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        n_tool = sum(1 for m in messages if isinstance(m, ToolMessage))
        if n_tool == 0:
            ai = AIMessage(content="", tool_calls=[{
                "name": "write_code_file",
                "args": {"path": self.target_file,
                         "content": "print('<METRICS>{}</METRICS>')\n"},
                "id": "call_write_1", "type": "tool_call"}])
        elif n_tool == 1:
            ai = AIMessage(content="", tool_calls=[{
                "name": "request_user_input",
                "args": {"question": "缺一个训练参数，请提供",
                         "is_sensitive": False, "purpose_key": ""},
                "id": "call_rui_1", "type": "tool_call"}])
        else:
            last = [m for m in messages if isinstance(m, ToolMessage)][-1]
            payload = json.dumps({
                "files_written": [self.target_file],
                "entry_script": self.target_file,
                "summary": f"生成 run.py（用户补充: {last.content}）",
                "notes": None,
            }, ensure_ascii=False, sort_keys=True)
            ai = AIMessage(content=(
                f"{config.REACT_RESULT_TAG_OPEN}{payload}"
                f"{config.REACT_RESULT_TAG_CLOSE}"))
        return ChatResult(generations=[ChatGeneration(message=ai)])


def _install_counting_write_tool(monkeypatch, disk_log: Path) -> None:
    """把 coding 的 write 工具工厂换成计数包装（委托真实工具，副作用双通道观测）。

    resume 重跑节点体会重新调用本工厂重建工具闭包——计数走模块级
    _WRITE_CALLS + 磁盘 append，跨重建存活。
    """
    from core.tools.code_fs_tools import make_write_code_file_tool as real_factory

    def counting_factory(base_dir: Optional[str] = None):
        real_tool = real_factory(base_dir=base_dir)

        @tool
        def write_code_file(path: str, content: str) -> str:
            """写入代码文件（真实工具的计数探针包装）。"""
            _WRITE_CALLS["n"] += 1
            with open(disk_log, "a", encoding="utf-8") as f:
                f.write(path + "\n")
            return real_tool.invoke({"path": path, "content": content})

        return write_code_file

    monkeypatch.setattr(coding_module, "make_write_code_file_tool", counting_factory)


def _build_parent_app():
    """父图：真实 coding wrapper 直接注册为节点（生产同构拓扑）+ InMemorySaver。"""
    builder = StateGraph(GlobalState)
    builder.add_node(NODE_NAME, coding)
    builder.add_edge(START, NODE_NAME)
    builder.add_edge(NODE_NAME, END)
    return builder.compile(checkpointer=InMemorySaver())


def _run_qc_scenario(workspace: Path, monkeypatch, tmp_path: Path,
                     thread_id: str) -> Dict[str, Any]:
    """跑一次完整 write → request_user_input → interrupt → resume 闭环。"""
    from core import react_base

    _install_inert_deepxiv_mocks(monkeypatch)
    disk_log = tmp_path / f"write_calls_{thread_id}.log"
    _install_counting_write_tool(monkeypatch, disk_log)

    code_dir = str((workspace / "2409.05591" / "code").resolve())
    target_file = str(Path(code_dir) / "run.py")
    monkeypatch.setattr(
        react_base, "create_llm",
        lambda cfg: ScriptedCodingLLM(target_file=target_file))

    app = _build_parent_app()
    cfg = {"configurable": {"thread_id": thread_id}}
    paused = app.invoke(_make_state(workspace), cfg)
    obs: Dict[str, Any] = {
        "interrupt": paused.get("__interrupt__"),
        "write_calls_at_pause": _WRITE_CALLS["n"],
        "code_dir": code_dir,
        "target_file": target_file,
        "disk_log": disk_log,
    }
    final = app.invoke(
        Command(resume={"value": _RESUME_VALUE, "remember": False}), cfg)
    obs.update({
        "final": final,
        "write_calls_final": _WRITE_CALLS["n"],
        "disk_lines": (disk_log.read_text(encoding="utf-8").splitlines()
                       if disk_log.exists() else []),
    })
    return obs


def test_cp_c2_3_write_side_effect_exactly_once_across_resume(
        workspace: Path, monkeypatch, tmp_path: Path):
    obs = _run_qc_scenario(workspace, monkeypatch, tmp_path, "c2-qc-main")

    # interrupt#3 暂停主图，payload 契约正确
    intr = obs["interrupt"]
    assert intr, "request_user_input 必须以 interrupt#3 暂停主图"
    assert intr[0].value["interrupt_kind"] == INTERRUPT_KIND_USER_INPUT
    assert intr[0].value["question"] == "缺一个训练参数，请提供"

    # 暂停时：前序独立轮次的 write 已执行恰一次
    assert obs["write_calls_at_pause"] == 1

    # —— AC-S4-14 coding 侧核心断言（进程内计数 + 磁盘双通道）——
    assert obs["write_calls_final"] == 1, (
        "Q-C 复验 FAIL：resume 后 write_code_file 副作用 > 1，前序独立轮次被"
        "重放——B2 门禁结论在真实 coding wrapper 上不成立，须触发架构师咨询"
    )
    assert obs["disk_lines"] == [obs["target_file"]], "磁盘通道：恰 1 次写入"

    # 文件内容正确（真实工具委托写入）
    assert Path(obs["target_file"]).read_text(encoding="utf-8") == \
        "print('<METRICS>{}</METRICS>')\n"


def test_cp_c2_3_resume_value_reaches_agent_and_map_contract(
        workspace: Path, monkeypatch, tmp_path: Path):
    obs = _run_qc_scenario(workspace, monkeypatch, tmp_path, "c2-qc-map")
    final = obs["final"]

    # resume 后不得再暂停；agent 拿到值继续收尾（summary 引用 resume 值）
    assert "__interrupt__" not in final

    # map_result 契约不回归（CP-C2-3 后半）
    assert final.get("current_step") == NODE_NAME
    assert final.get("code_output_dir") == obs["code_dir"]
    assert NODE_NAME not in (final.get("degraded_nodes") or []), \
        "成功写文件 + 正常收尾不得标 degraded"
    assert not any(
        e.get("node_name") == NODE_NAME for e in (final.get("node_errors") or []))
    # wrapper 预算扣减仍生效（形态零改动旁证）
    assert final.get("retry_budget_remaining", 50) < 50


def test_cp_c2_3_stable_across_3_runs(workspace: Path, monkeypatch, tmp_path: Path):
    """机理类结论连跑 3 次一致（dev-plan B2 断言点 4 口径）。"""
    for i in range(3):
        _WRITE_CALLS["n"] = 0
        obs = _run_qc_scenario(workspace, monkeypatch, tmp_path, f"c2-qc-stab{i}")
        assert obs["interrupt"], f"run#{i}: interrupt 必须出现"
        assert obs["write_calls_final"] == 1, f"run#{i}: 副作用恰为 1"
        assert "__interrupt__" not in obs["final"], f"run#{i}: resume 后收尾"


# ===========================================================================
# CP-C2-4 凭证注入链路：.secrets 含 git 凭证 → run_command 工厂收到的
# extra_env 含 GIT_ASKPASS + GIT_TERMINAL_PROMPT=0（经工厂闭包断言）
# ===========================================================================


def test_cp_c2_4_credential_env_injected_into_run_command_factory(
        workspace: Path, monkeypatch):
    _install_inert_deepxiv_mocks(monkeypatch)
    remember_secret("git_credential:github.com", "ghp_c2_token_xyz", is_sensitive=True)

    captured: Dict[str, Any] = {}
    real_factory = coding_module.make_run_command_tool

    def spy_factory(base_dir: str, extra_env: Optional[Dict[str, str]] = None):
        captured["base_dir"] = base_dir
        captured["extra_env"] = extra_env
        return real_factory(base_dir=base_dir, extra_env=extra_env)

    monkeypatch.setattr(coding_module, "make_run_command_tool", spy_factory)

    state = _make_state(workspace)
    tools = _get_coding_tools(state)
    assert len(tools) == 7

    env = captured["extra_env"]
    assert env is not None
    assert env["GIT_TERMINAL_PROMPT"] == "0", "R-S4-08：无条件带 GIT_TERMINAL_PROMPT=0"
    askpass = env.get("GIT_ASKPASS")
    assert askpass, "git 凭证在 .secrets 时必须注入 GIT_ASKPASS 脚本路径"
    script = Path(askpass)
    assert script.exists()
    assert "ghp_c2_token_xyz" in script.read_text(encoding="utf-8")
    assert "ghp_c2_token_xyz" not in json.dumps(
        {k: v for k, v in env.items()}, default=str), \
        "token 不得进 env 值（只进 0700 脚本文件）"
    # base_dir 与 write 工具同基准（code_output_dir）
    assert captured["base_dir"] == state["code_output_dir"] or \
        captured["base_dir"].endswith("/2409.05591/code")


def test_cp_c2_4_no_secrets_minimal_env(workspace: Path, monkeypatch):
    """无 .secrets 时 extra_env 仅 GIT_TERMINAL_PROMPT=0（不挂起地基仍在）。"""
    _install_inert_deepxiv_mocks(monkeypatch)
    captured: Dict[str, Any] = {}
    real_factory = coding_module.make_run_command_tool

    def spy_factory(base_dir: str, extra_env: Optional[Dict[str, str]] = None):
        captured["extra_env"] = extra_env
        return real_factory(base_dir=base_dir, extra_env=extra_env)

    monkeypatch.setattr(coding_module, "make_run_command_tool", spy_factory)
    _get_coding_tools(_make_state(workspace))
    assert captured["extra_env"] == {"GIT_TERMINAL_PROMPT": "0"}
