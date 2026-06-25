"""C1 - coding ReAct agent 节点自测（Sprint 3，S3-02）。

覆盖 dev-plan.md 任务 C1 的 7 个自测检查点（CP-C1-1 ~ CP-C1-7），全部 mock LLM 单测，
参考 tests/test_paper_analysis.py 范式。

- CP-C1-1 可导入；coding 为 callable，inspect.signature 形参为 (state)（wrapper 产物）
- CP-C1-2（mock LLM）首轮 coding 产出代码文件到 code_output_dir，返回 dict 含
  code_output_dir / current_step="coding"
- CP-C1-3（mock）修复回合：_build_coding_context 注入上轮 stderr 尾部 + error_category，
  断言 context payload 含 last_error_summary / fix_round，prompt 切"现有代码上修改"模式
- CP-C1-4 _map_coding_result 是 3 参签名（含 react_messages）；ReAct 失败时
  read-modify-write 写 node_errors/degraded_nodes（读出整列表→append→return），打 WARNING
- CP-C1-5 _map_coding_result 不写 fix_loop_count；retry_budget_remaining 不被 map_result 覆盖
- CP-C1-6 system prompt 主体常量内无 arxiv_id/paper_meta 等论文级动态变量 + 两份不同
  context 渲染出的 SystemMessage 去尾部段落后主体字节一致
- CP-C1-7 ToolMessage 序列化合规（间接经 B2 工具，断言不出现 str(dict) repr）
"""
from __future__ import annotations

import importlib
import inspect
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from langchain_core.messages import AIMessage, BaseMessage, SystemMessage  # noqa: E402
from langchain_core.tools import tool  # noqa: E402

# 用 importlib 拿真实子模块对象——core/nodes/__init__.py 把同名 callable 注册为包属性，
# 会遮蔽子模块引用（sp1 教训，CLAUDE 指令第 6 条）。
coding_module = importlib.import_module("core.nodes.coding")

from core.nodes.coding import (  # noqa: E402
    NODE_NAME,
    CODING_OUTPUT_SCHEMA,
    _CODING_SYSTEM_PROMPT_BODY,
    _build_coding_context,
    _build_coding_system_prompt,
    _digest_execution_feedback,
    _has_written_any_file,
    _map_coding_result,
    coding,
)


# ----------------------------- fake LLM -----------------------------


class FakeLLM:
    """脚本化 LLM：按 invoke 调用顺序返回预设 AIMessage。"""

    def __init__(
        self,
        responses: List[AIMessage],
        forced_dict: Optional[Dict[str, Any]] = None,
    ):
        self._responses = list(responses)
        self.calls: List[List[BaseMessage]] = []
        self.bind_tools_calls = 0
        self._forced_dict = forced_dict

    def bind_tools(self, tools):
        self.bind_tools_calls += 1
        return self

    def invoke(self, messages):
        self.calls.append(list(messages))
        if not self._responses:
            return AIMessage(content="<result>{}</result>")
        return self._responses.pop(0)

    def with_structured_output(self, schema, method=None):
        if self._forced_dict is None:
            raise RuntimeError("FakeLLM has no forced_dict scripted")
        parent = self

        class _Runnable:
            def invoke(self, messages):
                parent.calls.append(list(messages))
                return dict(parent._forced_dict)

        return _Runnable()


def _tool_call(name: str, args: Dict[str, Any], call_id: str) -> Dict[str, Any]:
    return {"name": name, "args": args, "id": call_id}


# --------------------------- fake state -----------------------------


def _make_state(
    tmp_path: Path,
    *,
    paper_meta: Optional[Dict[str, Any]] = None,
    code_output_dir: Optional[str] = None,
    execution_result: Optional[Dict[str, Any]] = None,
    fix_loop_count: int = 0,
    node_errors: Optional[List[Any]] = None,
    degraded_nodes: Optional[List[str]] = None,
) -> Dict[str, Any]:
    return {
        "user_input": "2409.05591",
        "input_type": "arxiv_id",
        "paper_meta": paper_meta if paper_meta is not None else {"arxiv_id": "2409.05591"},
        "paper_analysis": {
            "method_summary": "中文方法概述",
            "method_summary_en": "English method summary.",
            "datasets": ["WMT2014"],
            "framework": "PyTorch",
            "hardware_requirements": "8x P100",
            "hardware_requirements_en": "8x P100 GPUs",
        },
        "resource_info": {
            "selected_repo": {"local_path": str(tmp_path / "repo")},
        },
        "reproduction_plan": {
            "code_strategy": "adapt official repo",
            "execution_steps": [{"name": "train", "cmd": "python run.py"}],
            "deliverables": ["run.py"],
            "environment": {"python": "3.11"},
        },
        "code_output_dir": code_output_dir,
        "execution_result": execution_result,
        "fix_loop_count": fix_loop_count,
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
        "node_errors": list(node_errors) if node_errors else [],
        "degraded_nodes": list(degraded_nodes) if degraded_nodes else [],
        "messages": [],
        "workspace_dir": str(tmp_path / "ws"),
    }


def _install_inert_tool_mocks(monkeypatch) -> None:
    """把 read_section / web_search 工具换成惰性 mock（coding 测试不依赖它们）。

    write/read/list 用真实 B2 工具（CP-C1-2 需真实写文件，CP-C1-7 需真实 JSON ToolMessage）。
    """

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


def _patch_workspace(monkeypatch, tmp_path: Path) -> None:
    """把 code_fs_tools 与 coding 模块的 WORKSPACE_DIR 指向 tmp_path（越界校验通过）。"""
    from core.tools import code_fs_tools

    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(code_fs_tools, "WORKSPACE_DIR", ws)
    monkeypatch.setattr(coding_module, "WORKSPACE_DIR", ws)


def _set_llm(monkeypatch, factory) -> None:
    from core import react_base

    monkeypatch.setattr(react_base, "create_llm", factory)


# ============================== CP-C1-1 ==============================


def test_cp_c1_1_importable_and_state_signature() -> None:
    assert callable(coding)
    sig = inspect.signature(coding)
    params = list(sig.parameters)
    assert params == ["state"], f"coding wrapper 形参应为 (state)，实为 {params}"
    # schema 形态正确
    assert CODING_OUTPUT_SCHEMA["title"] == "CodingResult"
    assert "files_written" in CODING_OUTPUT_SCHEMA["properties"]


# ============================== CP-C1-2 ==============================


def test_cp_c1_2_first_round_writes_files(monkeypatch, tmp_path: Path) -> None:
    _patch_workspace(monkeypatch, tmp_path)
    _install_inert_tool_mocks(monkeypatch)

    state = _make_state(tmp_path)
    code_dir = str((tmp_path / "ws" / "2409.05591" / "code").resolve())
    target_file = str(Path(code_dir) / "run.py")

    responses = [
        AIMessage(
            content="",
            tool_calls=[_tool_call(
                "write_code_file",
                {"path": target_file, "content": "print('hi')\nprint('<METRICS>{}</METRICS>')\n"},
                "w1",
            )],
        ),
        AIMessage(content=(
            "<result>"
            + json.dumps({
                "files_written": [target_file],
                "entry_script": target_file,
                "summary": "生成入口脚本 run.py",
                "notes": None,
            })
            + "</result>"
        )),
    ]
    fake = FakeLLM(responses)
    _set_llm(monkeypatch, lambda config: fake)

    update = coding(state)

    assert update.get("current_step") == NODE_NAME
    assert update.get("code_output_dir") == code_dir
    # 文件真实写入
    assert Path(target_file).exists(), "write_code_file 应真实写入 run.py"
    # 未标 degraded（有成功写文件）
    assert NODE_NAME not in (update.get("degraded_nodes") or [])


# ============================== CP-C1-3 ==============================


def test_cp_c1_3_fix_round_injects_feedback(monkeypatch, tmp_path: Path) -> None:
    _patch_workspace(monkeypatch, tmp_path)

    code_dir = str((tmp_path / "ws" / "fixdir" / "code").resolve())
    exec_result = {
        "success": False,
        "metrics": {},
        "logs": "Traceback...\n" + ("X" * 5000) + "\nModuleNotFoundError: No module named 'torch'",
        "errors": ["[error_category=import] ModuleNotFoundError: torch 缺失"],
        "artifacts": [],
        "runtime_seconds": 1.0,
        "environment_info": {},
    }
    state = _make_state(
        tmp_path,
        code_output_dir=code_dir,
        execution_result=exec_result,
        fix_loop_count=2,
    )

    payload = _build_coding_context(state)

    assert payload.get("fix_round") == 2
    assert "last_error_summary" in payload
    les = payload["last_error_summary"]
    assert les["error_category"] == "import"
    assert les["errors"], "errors 应注入"
    # stderr 尾部裁剪到 ~2000 字符（含尾部 ModuleNotFoundError）
    assert len(les["stderr_tail"]) <= 2000
    assert "ModuleNotFoundError" in les["stderr_tail"]
    assert payload.get("code_output_dir") == code_dir

    # 首轮（无 execution_result）不应注入修复字段
    first_state = _make_state(tmp_path)
    first_payload = _build_coding_context(first_state)
    assert "fix_round" not in first_payload
    assert "last_error_summary" not in first_payload

    # 修复回合 prompt 已含"修复回合模式"指引（常量主体已含，HumanMessage 触发）
    assert "修复回合模式" in _CODING_SYSTEM_PROMPT_BODY


# ============================== CP-C1-4 ==============================


def test_cp_c1_4_three_arg_signature_and_degraded(monkeypatch, tmp_path: Path, caplog) -> None:
    # 3 参签名断言
    sig = inspect.signature(_map_coding_result)
    params = list(sig.parameters)
    assert params == ["result", "state", "react_messages"], f"实为 {params}"

    _patch_workspace(monkeypatch, tmp_path)

    # 预置已有的 node_errors / degraded_nodes，验证 read-modify-write 读出整列表→append→return
    preexisting_err = {"node_name": "upstream", "error_type": "degraded"}
    state = _make_state(
        tmp_path,
        node_errors=[preexisting_err],
        degraded_nodes=["upstream_node"],
    )

    # ReAct 失败：result 为 None，无任何 write ToolMessage
    with caplog.at_level(logging.WARNING):
        update = _map_coding_result(None, state, react_messages=[])

    ne = update.get("node_errors")
    dn = update.get("degraded_nodes")
    # 读出整列表：原有条目仍在 + 新 append
    assert preexisting_err in ne, "应保留上游 node_errors（read-modify-write）"
    assert "upstream_node" in dn, "应保留上游 degraded_nodes"
    assert NODE_NAME in dn, "coding 失败应 append 到 degraded_nodes"
    assert any(e.get("node_name") == NODE_NAME and e.get("error_type") == "degraded" for e in ne)
    # 新列表是新对象（不原地改 state）
    assert ne is not state["node_errors"]
    assert dn is not state["degraded_nodes"]
    # WARNING 日志
    assert any("未产出代码文件" in r.message for r in caplog.records)


# ============================== CP-C1-5 ==============================


def test_cp_c1_5_no_fix_loop_count_no_budget_override(monkeypatch, tmp_path: Path) -> None:
    _patch_workspace(monkeypatch, tmp_path)
    state = _make_state(tmp_path, fix_loop_count=3)

    update = _map_coding_result({"files_written": ["x"]}, state, react_messages=[])

    # must-fix-2：不写 fix_loop_count
    assert "fix_loop_count" not in update, "_map_coding_result 不得写 fix_loop_count"
    # must-fix-2：map_result 不写 retry_budget_remaining（由 wrapper setdefault）
    assert "retry_budget_remaining" not in update, (
        "_map_coding_result 不得覆盖 retry_budget_remaining"
    )


def test_cp_c1_5_wrapper_sets_budget_not_map(monkeypatch, tmp_path: Path) -> None:
    """端到端验证：wrapper 自动回写 retry_budget_remaining（扣减后），map_result 不覆盖。"""
    _patch_workspace(monkeypatch, tmp_path)
    _install_inert_tool_mocks(monkeypatch)
    state = _make_state(tmp_path)
    code_dir = str((tmp_path / "ws" / "2409.05591" / "code").resolve())
    target_file = str(Path(code_dir) / "run.py")
    responses = [
        AIMessage(content="", tool_calls=[_tool_call(
            "write_code_file", {"path": target_file, "content": "print(1)\n"}, "w1")]),
        AIMessage(content="<result>" + json.dumps(
            {"files_written": [target_file], "summary": "ok"}) + "</result>"),
    ]
    _set_llm(monkeypatch, lambda config: FakeLLM(responses))
    update = coding(state)
    assert "retry_budget_remaining" in update
    assert update["retry_budget_remaining"] < 50, "wrapper 应按 round 数扣减预算"


# ============================== CP-C1-6 ==============================


def test_cp_c1_6_system_prompt_body_no_dynamic(monkeypatch, tmp_path: Path) -> None:
    # 主体常量内无论文级动态变量字面量
    body = _CODING_SYSTEM_PROMPT_BODY
    for token in ("2409.05591", "arxiv_id=", "paper_meta", "Attention Is All You Need"):
        assert token not in body, f"主体常量不应含动态变量 {token!r}"

    # 两份不同 context 渲染出的 SystemMessage 去尾部段落后主体字节一致
    sep = "\n--- 当前任务上下文 ---\n"
    ctx_a = {"code_strategy": "A", "selected_repo_local_path": "/x/a"}
    ctx_b = {"code_strategy": "B", "fix_round": 3, "last_error_summary": {"error_category": "import"}}
    sp_a = _build_coding_system_prompt(ctx_a)
    sp_b = _build_coding_system_prompt(ctx_b)
    body_a = sp_a.split(sep)[0]
    body_b = sp_b.split(sep)[0]
    assert body_a == body_b, "去尾部段落后主体应字节级一致"
    assert body_a == body, "主体应等于 _CODING_SYSTEM_PROMPT_BODY 常量"


# ============================== CP-C1-7 ==============================


def test_cp_c1_7_toolmessage_json_compliant(monkeypatch, tmp_path: Path) -> None:
    """间接经 B2 write_code_file 工具：ToolMessage 内容是合法 JSON（非 str(dict) repr）。"""
    _patch_workspace(monkeypatch, tmp_path)
    from core.tools.code_fs_tools import make_write_code_file_tool, make_list_dir_tool

    code_dir = tmp_path / "ws" / "t" / "code"
    code_dir.mkdir(parents=True, exist_ok=True)
    write_tool = make_write_code_file_tool()
    out = write_tool.invoke({"path": str(code_dir / "a.py"), "content": "x=1\n"})

    # 合法 JSON（json.loads 成功；str(dict) 单引号会失败）
    parsed = json.loads(out)
    assert parsed["success"] is True
    assert "'success'" not in out, "不得出现 str(dict) repr 单引号"

    list_tool = make_list_dir_tool()
    lout = list_tool.invoke({"path": str(code_dir)})
    lparsed = json.loads(lout)
    assert "a.py" in lparsed["entries"]

    # _has_written_any_file 能识别真实 write ToolMessage
    from langchain_core.messages import AIMessage as _AI, ToolMessage
    react_msgs = [
        _AI(content="", tool_calls=[{"name": "write_code_file", "args": {}, "id": "w1"}]),
        ToolMessage(content=out, name="write_code_file", tool_call_id="w1"),
    ]
    assert _has_written_any_file(react_msgs, str(code_dir)) is True


def test_cp_c1_7_digest_feedback_no_logs() -> None:
    """_digest_execution_feedback 不注入完整 logs，仅 stderr 尾部。"""
    exec_result = {
        "logs": "A" * 10000,
        "errors": ["[error_category=runtime] boom"],
    }
    digest = _digest_execution_feedback(exec_result)
    assert len(digest["stderr_tail"]) <= 2000
    assert digest["error_category"] == "runtime"
    assert "logs" not in digest, "不得注入完整 logs 字段"


# ====================================================================
# 测试工程师补强用例（独立验收，2026-06-25）
# ====================================================================
from core.nodes.coding import _resolve_code_output_dir  # noqa: E402


# ---- 补强1：首轮 vs 修复回合分流的两个边界 ----


def test_reinforce_branch_exec_result_but_fix_count_zero_is_first_round(
    monkeypatch, tmp_path: Path
) -> None:
    """边界：execution_result 非空但 fix_loop_count==0 → 走首轮（不注入修复反馈）。

    架构 §2.2.2：判定修复回合要求 ``exec_result and fix_count > 0`` 两条件同时满足。
    """
    _patch_workspace(monkeypatch, tmp_path)
    state = _make_state(
        tmp_path,
        execution_result={"errors": ["[error_category=import] boom"], "logs": "x"},
        fix_loop_count=0,
    )
    payload = _build_coding_context(state)
    assert "fix_round" not in payload, "fix_loop_count==0 应判为首轮"
    assert "last_error_summary" not in payload
    assert "code_output_dir" not in payload, "首轮 context 不注入 code_output_dir"


def test_reinforce_branch_fix_count_but_no_exec_result_is_first_round(
    monkeypatch, tmp_path: Path
) -> None:
    """边界：fix_loop_count>0 但 execution_result=None → 走首轮（防 None 解析炸）。"""
    _patch_workspace(monkeypatch, tmp_path)
    state = _make_state(tmp_path, execution_result=None, fix_loop_count=3)
    payload = _build_coding_context(state)
    assert "fix_round" not in payload
    assert "last_error_summary" not in payload


# ---- 补强2：code_output_dir 端到端幂等（首轮 resolve 落库 → 修复回合复用） ----


def test_reinforce_code_output_dir_idempotent_across_rounds(
    monkeypatch, tmp_path: Path
) -> None:
    """首轮 map_result 写 resolve 后绝对路径；修复回合从 state 读回 → 两轮字节一致。

    C3 集成约定：execution 应直接读 state["code_output_dir"] 作 work_dir，不自拼目录。
    """
    _patch_workspace(monkeypatch, tmp_path)
    state1 = _make_state(tmp_path)  # 无 code_output_dir（None）→ 首轮新建
    u1 = _map_coding_result({"files_written": ["x"]}, state1, react_messages=[])
    first_dir = u1["code_output_dir"]
    # 首轮返回的是 resolve 后绝对路径
    assert first_dir == str(Path(first_dir).resolve())
    assert Path(first_dir).is_absolute()

    # 修复回合：state 带回首轮目录
    state2 = _make_state(tmp_path, code_output_dir=first_dir)
    u2 = _map_coding_result({"files_written": ["y"]}, state2, react_messages=[])
    assert u2["code_output_dir"] == first_dir, "修复回合应复用首轮目录（幂等）"


def test_reinforce_resolve_dir_fallback_workspace_and_thread(
    monkeypatch, tmp_path: Path
) -> None:
    """workspace_dir 缺失回退 config.WORKSPACE_DIR；arxiv_id 缺失 thread 回退 'task'。"""
    _patch_workspace(monkeypatch, tmp_path)
    # arxiv_id 缺失 → thread="task"
    state = _make_state(tmp_path, paper_meta={})
    out = _resolve_code_output_dir(state)
    assert out.endswith("/task/code"), f"thread 应回退 task，实为 {out}"


# ---- 补强3：_has_written_any_file 判定矩阵 ----


def _tm(content, name, cid="c1"):
    from langchain_core.messages import ToolMessage
    return ToolMessage(content=content, name=name, tool_call_id=cid)


def test_reinforce_has_written_only_read_list_is_false() -> None:
    msgs = [
        _tm("file content text", "read_code_file", "r1"),
        _tm(json.dumps({"success": True, "entries": []}), "list_dir", "l1"),
    ]
    assert _has_written_any_file(msgs, "/tmp/x") is False


def test_reinforce_has_written_failed_write_is_false() -> None:
    msgs = [_tm(json.dumps({"success": False, "error": "越界"}), "write_code_file", "w1")]
    assert _has_written_any_file(msgs, "/tmp/x") is False


def test_reinforce_has_written_framework_error_text_is_false() -> None:
    """write ToolMessage 是框架异常文案（'Error in ...'）→ 不计成功写入。"""
    msgs = [_tm("Error in write_code_file: boom", "write_code_file", "w1")]
    assert _has_written_any_file(msgs, "/tmp/x") is False


def test_reinforce_has_written_mixed_fail_then_success_is_true() -> None:
    msgs = [
        _tm(json.dumps({"success": False}), "write_code_file", "w1"),
        _tm(json.dumps({"success": True}), "write_code_file", "w2"),
    ]
    assert _has_written_any_file(msgs, "/tmp/x") is True


def test_reinforce_has_written_none_and_empty_is_false() -> None:
    assert _has_written_any_file(None, "/tmp/x") is False
    assert _has_written_any_file([], "/tmp/x") is False


def test_reinforce_has_written_multimodal_list_content_success() -> None:
    """content 为 multimodal list（[{type,text}]）含成功 JSON → True。"""
    msgs = [_tm([{"type": "text", "text": json.dumps({"success": True})}],
                "write_code_file", "w1")]
    assert _has_written_any_file(msgs, "/tmp/x") is True


# ---- 补强4：must-fix-1 多回合 degraded 不重复累加 ----


def test_reinforce_must_fix_1_multi_round_no_duplicate_degraded(
    monkeypatch, tmp_path: Path
) -> None:
    """coding 连续多轮失败：degraded_nodes 去重（coding 只出现一次），
    node_errors 每轮追加一条（设计预期，非去重），上游条目全程保留。"""
    _patch_workspace(monkeypatch, tmp_path)
    upstream_err = {"node_name": "upstream", "error_type": "permanent"}
    state = _make_state(
        tmp_path, node_errors=[upstream_err], degraded_nodes=["upstream"]
    )
    # 回合1 失败
    u1 = _map_coding_result(None, state, react_messages=[])
    assert u1["degraded_nodes"] == ["upstream", NODE_NAME]
    assert len(u1["node_errors"]) == 2
    # 回合2：state 带回回合1结果，再失败
    state2 = _make_state(
        tmp_path, node_errors=u1["node_errors"], degraded_nodes=u1["degraded_nodes"]
    )
    u2 = _map_coding_result(None, state2, react_messages=[])
    # degraded 不重复累加 coding
    assert u2["degraded_nodes"].count(NODE_NAME) == 1
    assert u2["degraded_nodes"] == ["upstream", NODE_NAME]
    # node_errors 每轮追加一条
    assert len(u2["node_errors"]) == 3
    # 上游条目全程保留
    assert upstream_err in u2["node_errors"]


# ---- 补强5：<METRICS> 约定 + 修复回合模式 prompt 入口 ----


def test_reinforce_metrics_convention_in_system_prompt() -> None:
    """system prompt 主体含 <METRICS>{...}</METRICS> 入口脚本指标约定（R-S3-05 缓解，
    C3 解析依赖此约定）。"""
    body = _CODING_SYSTEM_PROMPT_BODY
    assert "<METRICS>" in body and "</METRICS>" in body
    assert "<METRICS>{}</METRICS>" in body, "应含空指标兜底约定"
    # 修复回合模式入口（HumanMessage 出现 fix_round/last_error_summary 时生效）
    assert "修复回合模式" in body
    assert "fix_round" in body and "last_error_summary" in body
    assert "不要从头重新生成全部代码" in body


# ---- 补强6：digest 裁剪 + 解析鲁棒性边界 ----


def test_reinforce_digest_stderr_tail_keeps_end_not_full() -> None:
    """构造超长 logs：注入的是尾部 ~2000 字符（含末尾错误栈），非完整 logs。"""
    tail_marker = "FATAL_AT_END_OF_LOG"
    logs = "HEAD_NOISE " + ("Z" * 6000) + tail_marker
    digest = _digest_execution_feedback(
        {"logs": logs, "errors": ["[error_category=runtime] x"]}
    )
    assert len(digest["stderr_tail"]) <= 2000
    assert tail_marker in digest["stderr_tail"], "应保留尾部错误栈"
    assert "HEAD_NOISE" not in digest["stderr_tail"], "头部噪声应被裁掉"


def test_reinforce_digest_no_category_prefix_and_empty_errors() -> None:
    """errors[0] 无 [error_category=] 前缀 → error_category=None；空 errors 安全。"""
    d1 = _digest_execution_feedback({"errors": ["plain error"], "logs": ""})
    assert d1["error_category"] is None
    d2 = _digest_execution_feedback({"errors": [], "logs": "short"})
    assert d2["error_category"] is None
    assert d2["stderr_tail"] == "short"


def test_reinforce_digest_non_str_logs_coerced() -> None:
    """logs 非 str（如 list）→ str() 兜底不抛。"""
    d = _digest_execution_feedback({"errors": [], "logs": ["a", "b"]})
    assert isinstance(d["stderr_tail"], str)


# ---- 补强7：map_result result 为非 dict / 空 dict 也判 degraded ----


def test_reinforce_map_result_non_dict_result_degraded(
    monkeypatch, tmp_path: Path
) -> None:
    """result 不是 dict（异常返回）→ 判 degraded（短路保护）。"""
    _patch_workspace(monkeypatch, tmp_path)
    state = _make_state(tmp_path)
    # result 为字符串（异常形态），即便给了成功 write 也应因 result 非 dict 判 degraded
    write_msg = _tm(json.dumps({"success": True}), "write_code_file", "w1")
    update = _map_coding_result("not a dict", state, react_messages=[write_msg])
    assert NODE_NAME in update["degraded_nodes"]
