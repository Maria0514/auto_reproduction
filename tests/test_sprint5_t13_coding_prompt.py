"""Sprint 5 T-S5-1-3：coding prompt/schema 静态批次（P1+P2）+ simulation_notice 透传（S5-02）。

自测检查点（dev-plan T-S5-1-3）：
- CP-1.3-1 prompt 断言：三红线关键语 + simulation_notice 义务语句存在（AC-S5-04 prompt 部分）
- CP-1.3-2 主体字节级一致守门：两篇不同论文去尾部动态段后 == 新稳定前缀
  （_CODING_SYSTEM_PROMPT_BODY + _CODING_HONESTY_SECTION）；段内禁动态变量审查
- CP-1.3-3 mock map 断言：<result> 带/不带 simulation_notice → state 落值/回填 None
  （AC-S5-04 state 部分）；_map_coding_result 3 参签名零回归

注意：core/nodes/__init__.py 显式 export 会让 callable 遮蔽子模块，
统一用 importlib.import_module 访问模块属性（已知坑 #6）。
"""

from __future__ import annotations

import importlib
import inspect
import json
from pathlib import Path
from typing import Any, Dict, Optional

from langchain_core.messages import ToolMessage

coding_module = importlib.import_module("core.nodes.coding")

CODING_OUTPUT_SCHEMA = coding_module.CODING_OUTPUT_SCHEMA
NODE_NAME = coding_module.NODE_NAME
_CODING_HONESTY_SECTION = coding_module._CODING_HONESTY_SECTION
_CODING_SYSTEM_PROMPT_BODY = coding_module._CODING_SYSTEM_PROMPT_BODY
_build_coding_context = coding_module._build_coding_context
_build_coding_system_prompt = coding_module._build_coding_system_prompt
_map_coding_result = coding_module._map_coding_result

# 尾部动态段分隔符（与 coding._build_coding_system_prompt 中的拼接串字节一致）。
_TAIL_SEP = "\n--- 当前任务上下文 ---\n"


# --------------------------- fixtures（最小自包含） ---------------------------


def _make_state(
    tmp_path: Path,
    *,
    arxiv_id: str = "2409.05591",
    **overrides: Any,
) -> Dict[str, Any]:
    state: Dict[str, Any] = {
        "user_input": arxiv_id,
        "input_type": "arxiv_id",
        "paper_meta": {"arxiv_id": arxiv_id},
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
        "code_output_dir": None,
        "execution_result": None,
        "fix_loop_count": 0,
        "retry_budget_remaining": 50,
        "node_errors": [],
        "degraded_nodes": [],
        "messages": [],
        "workspace_dir": str(tmp_path / "ws"),
    }
    state.update(overrides)
    return state


def _paper_b_state(tmp_path: Path) -> Dict[str, Any]:
    """第二篇完全不同的论文（含修复回合），用于字节级一致守门。"""
    return _make_state(
        tmp_path,
        arxiv_id="1706.03762",
        paper_analysis={
            "method_summary": "另一篇论文的中文概述",
            "method_summary_en": "A totally different transformer paper.",
            "datasets": ["SQuAD"],
            "framework": "JAX",
            "hardware_requirements": "TPU v3",
            "hardware_requirements_en": "TPU v3 pods",
        },
        reproduction_plan={
            "code_strategy": "from scratch",
            "execution_steps": [{"name": "eval", "cmd": "python eval.py"}],
            "deliverables": ["eval.py"],
            "environment": {"python": "3.10"},
        },
        execution_result={
            "errors": ["[error_category=import] ModuleNotFoundError: jax"],
            "logs": "Traceback ... ModuleNotFoundError: No module named 'jax'",
        },
        fix_loop_count=2,
    )


def _tm(content: str, name: str, cid: str = "c1") -> ToolMessage:
    return ToolMessage(content=content, name=name, tool_call_id=cid)


def _success_write_msg(state: Dict[str, Any]) -> ToolMessage:
    """构造落在 code_output_dir 内的成功 write ToolMessage（非 degraded 路径用）。"""
    code_dir = coding_module._resolve_code_output_dir(state)
    return _tm(
        json.dumps({"success": True, "path": str(Path(code_dir) / "run.py")}),
        "write_code_file",
        "w1",
    )


# ===========================================================================
# CP-1.3-1 prompt 断言：三红线关键语 + simulation_notice 义务语句
# ===========================================================================


def test_cp_1_3_1_three_redlines_in_honesty_section() -> None:
    """三条红线关键语逐条存在于 _CODING_HONESTY_SECTION（AC-S5-04 prompt 部分）。"""
    section = _CODING_HONESTY_SECTION
    # 红线①：禁 verifier 答案泄漏给被评估对象
    assert "verifier" in section
    assert "泄漏" in section
    assert "被评估对象" in section
    # 红线②：禁硬编码分数/结果/常量结局
    assert "禁止硬编码分数" in section
    assert "常量结局" in section
    # 红线③：不得以改变实验本质的方式规避资源缺失
    assert "改变实验本质" in section
    assert "规避资源缺失" in section


def test_cp_1_3_1_simulation_notice_obligation_in_honesty_section() -> None:
    """simulation_notice 义务语句存在：无法真实验时必须在 <result> 中给出声明。"""
    section = _CODING_HONESTY_SECTION
    assert "simulation_notice" in section
    assert "<result>" in section, "义务语句应明确声明落点为 <result> JSON"
    assert "无法进行真实实验" in section
    assert "必须" in section


def test_cp_1_3_1_honesty_section_rendered_into_system_prompt(tmp_path: Path) -> None:
    """组装后的 system prompt 全文包含红线段（插入位置：主体与尾部动态段之间）。"""
    sp = _build_coding_system_prompt(_build_coding_context(_make_state(tmp_path)))
    assert _CODING_HONESTY_SECTION in sp
    # 插入位置守门：主体之后、尾部动态段之前
    assert sp.index(_CODING_HONESTY_SECTION) >= len(_CODING_SYSTEM_PROMPT_BODY) - 1
    assert sp.index(_CODING_HONESTY_SECTION) < sp.index(_TAIL_SEP)


# ===========================================================================
# CP-1.3-2 主体字节级一致守门 + 段内禁动态变量审查
# ===========================================================================


def test_cp_1_3_2_stable_prefix_byte_identical_across_papers(tmp_path: Path) -> None:
    """两篇不同论文（一篇首轮 / 一篇修复回合）全链路渲染 system prompt，
    去尾部动态段后 == _CODING_SYSTEM_PROMPT_BODY + _CODING_HONESTY_SECTION
    （新稳定前缀，含红线段，R-PC4）。"""
    ws_a = tmp_path / "a"
    ws_b = tmp_path / "b"
    ws_a.mkdir()
    ws_b.mkdir()
    sp_a = _build_coding_system_prompt(_build_coding_context(_make_state(ws_a)))
    sp_b = _build_coding_system_prompt(_build_coding_context(_paper_b_state(ws_b)))

    prefix_a = sp_a.split(_TAIL_SEP)[0]
    prefix_b = sp_b.split(_TAIL_SEP)[0]
    assert prefix_a == prefix_b, "去尾部动态段后稳定前缀应字节级一致"

    expected_prefix = _CODING_SYSTEM_PROMPT_BODY + _CODING_HONESTY_SECTION
    assert prefix_a == expected_prefix, (
        "稳定前缀应 == 主体常量 + 诚实红线段常量（红线段进稳定前缀）"
    )


def test_cp_1_3_2_honesty_section_repeated_render_byte_identical(tmp_path: Path) -> None:
    """同一论文重复渲染两次，system prompt 全文字节级一致（含尾部常量段）。"""
    state = _make_state(tmp_path)
    sp_1 = _build_coding_system_prompt(_build_coding_context(state))
    sp_2 = _build_coding_system_prompt(_build_coding_context(state))
    assert sp_1 == sp_2


def test_cp_1_3_2_no_dynamic_vars_in_honesty_section() -> None:
    """段内禁动态变量审查：无 f-string 插值 / format 占位 / 论文级动态字面量。"""
    section = _CODING_HONESTY_SECTION
    # format / f-string 占位符号（段内本就不该出现花括号与 % 占位）
    for token in ("{", "}", "%s", "%(", "%d"):
        assert token not in section, f"红线段不得含格式占位符号 {token!r}"
    # 论文级 / 任务级动态字面量（沿用 CP-C1-6 / CP-C2-2 审查 token）
    for token in ("2409.05591", "arxiv_id=", "paper_meta", str(Path.home())):
        assert token not in section, f"红线段不得含动态变量 {token!r}"
    # 常量恒等性：模块内常量即最终渲染内容（无运行时拼接改写）
    assert isinstance(section, str) and section == coding_module._CODING_HONESTY_SECTION


# ===========================================================================
# CP-1.3-3 mock map 断言：simulation_notice 透传 + 3 参签名零回归
# ===========================================================================


def test_cp_1_3_3_map_signature_zero_regression() -> None:
    """_map_coding_result 保持 3 参签名（result, state, react_messages）。"""
    params = list(inspect.signature(_map_coding_result).parameters)
    assert params == ["result", "state", "react_messages"], f"实为 {params}"


def test_cp_1_3_3_notice_present_lands_in_state(tmp_path: Path) -> None:
    """<result> 带 simulation_notice → 原样落 state（AC-S5-04 state 部分）。"""
    state = _make_state(tmp_path)
    notice = "训练部分为模拟：无 GPU 算力，用随机初始化权重跑通评估流程"
    update = _map_coding_result(
        {"files_written": ["run.py"], "summary": "ok", "simulation_notice": notice},
        state,
        react_messages=[_success_write_msg(state)],
    )
    assert update["simulation_notice"] == notice
    # 非 degraded 路径（有成功 write）
    assert NODE_NAME not in update["degraded_nodes"]


def test_cp_1_3_3_notice_absent_backfills_none(tmp_path: Path) -> None:
    """<result> 不带 simulation_notice → 回填 None（缺失即诚实语义，单点写）。"""
    state = _make_state(tmp_path)
    update = _map_coding_result(
        {"files_written": ["run.py"], "summary": "ok"},
        state,
        react_messages=[_success_write_msg(state)],
    )
    assert "simulation_notice" in update, "缺失也必须显式写 None（单点写契约）"
    assert update["simulation_notice"] is None


def test_cp_1_3_3_notice_none_on_degraded_result(tmp_path: Path) -> None:
    """result=None（ReAct 失败 degraded 路径）→ simulation_notice 回填 None 不炸。"""
    state = _make_state(tmp_path)
    update = _map_coding_result(None, state, react_messages=[])
    assert update["simulation_notice"] is None
    assert NODE_NAME in update["degraded_nodes"]


def test_cp_1_3_3_notice_blank_or_non_str_coerced_to_none(tmp_path: Path) -> None:
    """空串 / 纯空白 / 非 str 形态 → None（防御：不落无意义声明）。"""
    state = _make_state(tmp_path)
    for junk in ("", "   ", 123, {"note": "x"}, ["y"]):
        update = _map_coding_result(
            {"files_written": ["run.py"], "summary": "ok", "simulation_notice": junk},
            state,
            react_messages=[_success_write_msg(state)],
        )
        assert update["simulation_notice"] is None, f"{junk!r} 应回填 None"


def test_cp_1_3_3_schema_has_optional_simulation_notice() -> None:
    """CODING_OUTPUT_SCHEMA 含可选 simulation_notice: string|null，不进 required。"""
    props = CODING_OUTPUT_SCHEMA["properties"]
    assert "simulation_notice" in props
    assert props["simulation_notice"]["type"] == ["string", "null"]
    assert "simulation_notice" not in CODING_OUTPUT_SCHEMA["required"]
    # 既有 required 契约零回归
    assert CODING_OUTPUT_SCHEMA["required"] == ["files_written", "summary"]
