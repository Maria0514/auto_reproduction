"""C1 坑1（写/读边界硬伤）+ 坑2（read_section 不可用）修复自测（Sprint 3, S3-02）。

覆盖架构师列的 8 条断言方向：
  1. 首轮 context（fix_count=0/execution_result=None）含 code_output_dir 且为绝对路径、
     == _resolve_code_output_dir(state)
  2. payload 含 arxiv_id == state.paper_meta.arxiv_id（坑2）
  3. 三处幂等同值（build_context 注入值 / get_tools 内 base_dir / map_result 内 code_dir）
  4. write 工具越界被拒（base_dir 绑定）：code_dir 外路径 success=false；内相对路径 success=true
  5. B2 向后兼容：make_write_code_file_tool() 无参行为与改前一致（含 "WORKSPACE_DIR" 错误信息）
  6. _has_written_any_file 落点校验：path 在 code_dir 外 → False；内 → True
  7. 真实文件系统断言：真实 write 工具写文件到 code_dir，state.code_output_dir 目录里实际存在
  8. read/list 仍能跨访问 selected_repo.local_path（code_dir 外、workspace 内）
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

from langchain_core.messages import ToolMessage  # noqa: E402

coding_module = importlib.import_module("core.nodes.coding")
code_fs_tools = importlib.import_module("core.tools.code_fs_tools")

from core.nodes.coding import (  # noqa: E402
    _build_coding_context,
    _get_coding_tools,
    _has_written_any_file,
    _map_coding_result,
    _resolve_code_output_dir,
)
from core.tools.code_fs_tools import (  # noqa: E402
    make_list_dir_tool,
    make_read_code_file_tool,
    make_write_code_file_tool,
)


# --------------------------- helpers --------------------------------


def _make_state(
    tmp_path: Path,
    *,
    paper_meta: Optional[Dict[str, Any]] = None,
    code_output_dir: Optional[str] = None,
    execution_result: Optional[Dict[str, Any]] = None,
    fix_loop_count: int = 0,
) -> Dict[str, Any]:
    return {
        "user_input": "2409.05591",
        "paper_meta": paper_meta if paper_meta is not None else {"arxiv_id": "2409.05591"},
        "paper_analysis": {"method_summary_en": "m", "datasets": [], "framework": "PyTorch"},
        "resource_info": {"selected_repo": {"local_path": str(tmp_path / "ws" / "repo")}},
        "reproduction_plan": {
            "code_strategy": "adapt",
            "execution_steps": [],
            "deliverables": ["run.py"],
            "environment": {},
        },
        "code_output_dir": code_output_dir,
        "execution_result": execution_result,
        "fix_loop_count": fix_loop_count,
        "node_errors": [],
        "degraded_nodes": [],
        "workspace_dir": str(tmp_path / "ws"),
    }


def _patch_workspace(monkeypatch, tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(code_fs_tools, "WORKSPACE_DIR", ws)
    monkeypatch.setattr(coding_module, "WORKSPACE_DIR", ws)
    return ws


def _invoke(tool_obj, **kwargs):
    return tool_obj.invoke(kwargs)


def _write_tool_msg(path: str, success: bool = True) -> ToolMessage:
    payload = {"success": success, "path": path, "bytes_written": 10}
    if not success:
        payload = {"success": False, "error": "boom"}
    return ToolMessage(
        content=json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str),
        name="write_code_file",
        tool_call_id="t1",
    )


# ------------------------- 断言 1 / 2 -------------------------------


def test_first_round_context_has_abs_code_output_dir(monkeypatch, tmp_path):
    """断言1：首轮（fix_count=0/execution_result=None）context 含绝对 code_output_dir。"""
    _patch_workspace(monkeypatch, tmp_path)
    state = _make_state(tmp_path, execution_result=None, fix_loop_count=0)
    payload = _build_coding_context(state)
    assert "code_output_dir" in payload
    cod = payload["code_output_dir"]
    assert Path(cod).is_absolute()
    assert cod == _resolve_code_output_dir(state)
    # 首轮不应注入修复反馈
    assert "fix_round" not in payload
    assert "last_error_summary" not in payload


def test_context_has_arxiv_id(monkeypatch, tmp_path):
    """断言2：payload 含 arxiv_id == state.paper_meta.arxiv_id（坑2）。"""
    _patch_workspace(monkeypatch, tmp_path)
    state = _make_state(tmp_path, paper_meta={"arxiv_id": "2409.05591"})
    payload = _build_coding_context(state)
    assert payload["arxiv_id"] == "2409.05591"

    # paper_meta 为 {}（无 arxiv_id） → arxiv_id None，不报错
    state2 = _make_state(tmp_path, paper_meta={})
    payload2 = _build_coding_context(state2)
    assert payload2["arxiv_id"] is None


# ------------------------- 断言 3 ----------------------------------


def test_three_way_idempotent_code_dir(monkeypatch, tmp_path):
    """断言3：build_context 注入值 / get_tools 内 base_dir / map_result 内 code_dir 相等。"""
    ws = _patch_workspace(monkeypatch, tmp_path)
    state = _make_state(tmp_path)

    ctx_dir = _build_coding_context(state)["code_output_dir"]

    tools = _get_coding_tools(state)
    write_tool = tools[0]
    # base_dir 闭包在 write_code_file 工具内；用工具实际行为反推：写相对文件落点应在 ctx_dir
    res = json.loads(_invoke(write_tool, path="probe.py", content="x"))
    assert res["success"] is True
    assert Path(res["path"]).parent == Path(ctx_dir).resolve()

    # map_result 内部 code_dir == _resolve_code_output_dir(state)（幂等同值）
    updates = _map_coding_result({"files_written": ["probe.py"], "summary": "s"}, state)
    assert updates["code_output_dir"] == ctx_dir == _resolve_code_output_dir(state)


# ------------------------- 断言 4 ----------------------------------


def test_write_tool_base_dir_rejects_out_of_base(monkeypatch, tmp_path):
    """断言4：base_dir 绑定时 code_dir 外路径被拒、内相对路径成功且落 code_dir 内。"""
    ws = _patch_workspace(monkeypatch, tmp_path)
    code_dir = ws / "2409.05591" / "code"
    code_dir.mkdir(parents=True, exist_ok=True)
    wt = make_write_code_file_tool(base_dir=str(code_dir))

    # 绝对路径在 workspace 内但 code_dir 外 → 拒
    evil = ws / "evil.py"
    res = json.loads(_invoke(wt, path=str(evil), content="x"))
    assert res["success"] is False
    assert "code_output_dir" in res["error"]

    # 相对 ../.. 逃逸 → 拒
    res2 = json.loads(_invoke(wt, path="../../etc/x", content="x"))
    assert res2["success"] is False
    assert "code_output_dir" in res2["error"]

    # code_dir 内相对路径 → 成功，落点在 code_dir 内
    res3 = json.loads(_invoke(wt, path="model.py", content="print('hi')"))
    assert res3["success"] is True
    rp = Path(res3["path"]).resolve()
    assert rp == (code_dir / "model.py").resolve()
    assert rp.is_relative_to(code_dir.resolve())


# ------------------------- 断言 5（B2 向后兼容）--------------------


def test_b2_backward_compat_no_base_dir(monkeypatch, tmp_path):
    """断言5：make_write_code_file_tool() 无参行为与改前一致。"""
    ws = _patch_workspace(monkeypatch, tmp_path)
    wt = make_write_code_file_tool()

    # workspace 内成功
    ok = ws / "a.py"
    res = json.loads(_invoke(wt, path=str(ok), content="x"))
    assert res["success"] is True
    assert Path(res["path"]).resolve() == ok.resolve()

    # workspace 外越界被拒，错误信息含 WORKSPACE_DIR
    outside = tmp_path / "outside.py"
    res2 = json.loads(_invoke(wt, path=str(outside), content="x"))
    assert res2["success"] is False
    assert "WORKSPACE_DIR" in res2["error"]


# ------------------------- 断言 6 ----------------------------------


def test_has_written_landing_check(monkeypatch, tmp_path):
    """断言6：success=true 但 path 在 code_dir 外 → False；内 → True。"""
    ws = _patch_workspace(monkeypatch, tmp_path)
    code_dir = ws / "2409.05591" / "code"
    code_dir.mkdir(parents=True, exist_ok=True)

    inside = str((code_dir / "model.py").resolve())
    outside = str((ws / "evil.py").resolve())

    assert _has_written_any_file([_write_tool_msg(inside)], str(code_dir)) is True
    assert _has_written_any_file([_write_tool_msg(outside)], str(code_dir)) is False
    # success=false 不计
    assert _has_written_any_file([_write_tool_msg(inside, success=False)], str(code_dir)) is False
    # 混合：外 + 内 → True
    assert _has_written_any_file(
        [_write_tool_msg(outside), _write_tool_msg(inside)], str(code_dir)
    ) is True


# ------------------------- 断言 7（真实文件系统）------------------


def test_real_filesystem_write_lands_in_state_dir(monkeypatch, tmp_path):
    """断言7：真实 write 工具写文件，state.code_output_dir 目录里实际存在该文件。"""
    _patch_workspace(monkeypatch, tmp_path)
    state = _make_state(tmp_path)

    tools = _get_coding_tools(state)
    write_tool = tools[0]
    res = json.loads(_invoke(write_tool, path="reproduce.py", content="print(1)"))
    assert res["success"] is True

    code_dir = _resolve_code_output_dir(state)
    state["code_output_dir"] = code_dir
    real_file = Path(code_dir) / "reproduce.py"
    assert real_file.exists()
    assert real_file.read_text(encoding="utf-8") == "print(1)"
    # _has_written_any_file 也认可
    msgs = [_write_tool_msg(str(real_file.resolve()))]
    assert _has_written_any_file(msgs, code_dir) is True


# ------------------------- 断言 8（read/list 跨访问）--------------


def test_read_list_cross_access_selected_repo(monkeypatch, tmp_path):
    """断言8：read/list 仍能访问 code_dir 外、workspace 内的 selected_repo.local_path。"""
    ws = _patch_workspace(monkeypatch, tmp_path)
    repo = ws / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    (repo / "orig.py").write_text("# original repo code", encoding="utf-8")

    rt = make_read_code_file_tool()
    content = _invoke(rt, path=str(repo / "orig.py"))
    assert "# original repo code" in content
    assert not content.startswith("Error")

    lt = make_list_dir_tool()
    listed = json.loads(_invoke(lt, path=str(repo)))
    assert listed["success"] is True
    assert "orig.py" in listed["entries"]


# ------------------------- map_result 签名 -------------------------


def test_map_result_three_arg_signature():
    sig = inspect.signature(_map_coding_result)
    assert list(sig.parameters) == ["result", "state", "react_messages"]
