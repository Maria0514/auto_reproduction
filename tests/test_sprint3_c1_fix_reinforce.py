"""C1 坑1+坑2 修复 —— 测试工程师独立验收补强用例（Sprint 3, S3-02, 2026-06-25）。

不改动开发既有用例（test_sprint3_c1.py / test_sprint3_c1_fix.py），在独立文件中补齐
开发自测与 c1_fix 未覆盖或覆盖不足的边界：

- 坑1-C 落点校验边界：无 path 的 success=true → False（开发改动2/3 把 path 补进既有
  用例后，此场景反而失覆盖）；path 恰好 == code_dir；path 为 code_dir 子目录文件；
  path 用 `..` resolve 后落 code_dir 内（合法）vs 外（非法）。
- 坑1-A 相对/绝对路径锚定：相对 sub/model.py → code_dir/sub/model.py；绝对在 code_dir
  内放行 / 外拒；越界多形态（绝对系统路径、../../etc、workspace 内 code_dir 外）。
- 坑2 arxiv_id：paper_meta 非 dict（str/None/list）→ arxiv_id None 不抛。
- 真实 FS：write 子目录文件实际落盘 + read 回读一致；越界 write 目标文件未落盘。
- 三处幂等在修复回合（state 带 code_output_dir）仍同值。
"""
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional

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
    make_read_code_file_tool,
    make_write_code_file_tool,
)


# --------------------------- helpers --------------------------------


def _patch_workspace(monkeypatch, tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(code_fs_tools, "WORKSPACE_DIR", ws)
    monkeypatch.setattr(coding_module, "WORKSPACE_DIR", ws)
    return ws


def _make_state(
    tmp_path: Path,
    *,
    paper_meta: Any = None,
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


def _wt_msg(path: Optional[str], success: bool = True) -> ToolMessage:
    if success:
        payload: Dict[str, Any] = {"success": True, "bytes_written": 10}
        if path is not None:
            payload["path"] = path
    else:
        payload = {"success": False, "error": "boom"}
    return ToolMessage(
        content=json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str),
        name="write_code_file",
        tool_call_id="t1",
    )


# =================== 坑1-C 落点校验边界（补缺口）====================


def test_has_written_success_without_path_is_false():
    """关键缺口：success=true 但无 path 字段 → 不计（生产 L359-361 `if not written_path: continue`）。

    开发改动 2/3 把 path 补进既有 multimodal/mixed 用例后，这个「无 path」场景反而无人覆盖。
    """
    code_dir = "/tmp/cd"
    assert _has_written_any_file([_wt_msg(None, success=True)], code_dir) is False


def test_has_written_path_equals_code_dir_itself_is_true(tmp_path):
    """边界：path 恰好 == code_dir（rp == cd 分支）→ True。"""
    code_dir = tmp_path / "code"
    code_dir.mkdir(parents=True, exist_ok=True)
    cd = str(code_dir.resolve())
    assert _has_written_any_file([_wt_msg(cd)], cd) is True


def test_has_written_path_in_subdir_is_true(tmp_path):
    """边界：path 是 code_dir 的子目录文件 → True（is_relative_to 分支）。"""
    code_dir = tmp_path / "code"
    (code_dir / "sub").mkdir(parents=True, exist_ok=True)
    f = str((code_dir / "sub" / "model.py").resolve())
    assert _has_written_any_file([_wt_msg(f)], str(code_dir)) is True


def test_has_written_path_with_dotdot_resolving_inside_is_true(tmp_path):
    """边界：path 含 `..` 但 resolve 后仍落 code_dir 内 → True。"""
    code_dir = tmp_path / "code"
    (code_dir / "sub").mkdir(parents=True, exist_ok=True)
    sneaky = str(code_dir / "sub" / ".." / "model.py")  # resolve → code_dir/model.py（内）
    assert _has_written_any_file([_wt_msg(sneaky)], str(code_dir)) is True


def test_has_written_path_with_dotdot_resolving_outside_is_false(tmp_path):
    """边界：path 含 `..` resolve 后逃出 code_dir → False。"""
    code_dir = tmp_path / "code"
    code_dir.mkdir(parents=True, exist_ok=True)
    escape = str(code_dir / ".." / "evil.py")  # resolve → tmp_path/evil.py（外）
    assert _has_written_any_file([_wt_msg(escape)], str(code_dir)) is False


def test_has_written_empty_string_path_is_false(tmp_path):
    """边界：path 为空字符串（falsy）→ 不计。"""
    code_dir = str(tmp_path / "code")
    assert _has_written_any_file([_wt_msg("")], code_dir) is False


def test_has_written_sibling_prefix_dir_not_counted(tmp_path):
    """前缀陷阱：code_dir=/x/code，path 落在 /x/code_other 不应被误判为内
    （is_relative_to 不是字符串前缀匹配，应正确判 False）。"""
    base = tmp_path
    code_dir = base / "code"
    code_dir.mkdir(parents=True, exist_ok=True)
    other = base / "code_other"
    other.mkdir(parents=True, exist_ok=True)
    f = str((other / "x.py").resolve())
    assert _has_written_any_file([_wt_msg(f)], str(code_dir)) is False


# =================== 坑1-A 相对/绝对路径锚定 =======================


def test_write_tool_relative_subdir_anchors_to_code_dir(monkeypatch, tmp_path):
    """相对 sub/model.py 锚定到 code_dir/sub/model.py 并真实落盘。"""
    ws = _patch_workspace(monkeypatch, tmp_path)
    code_dir = ws / "2409.05591" / "code"
    code_dir.mkdir(parents=True, exist_ok=True)
    wt = make_write_code_file_tool(base_dir=str(code_dir))

    res = json.loads(wt.invoke({"path": "sub/model.py", "content": "X=1\n"}))
    assert res["success"] is True
    rp = Path(res["path"]).resolve()
    assert rp == (code_dir / "sub" / "model.py").resolve()
    assert rp.exists()
    assert rp.read_text(encoding="utf-8") == "X=1\n"


def test_write_tool_abs_inside_code_dir_allowed(monkeypatch, tmp_path):
    """绝对路径落在 code_dir 内 → 放行。"""
    ws = _patch_workspace(monkeypatch, tmp_path)
    code_dir = ws / "2409.05591" / "code"
    code_dir.mkdir(parents=True, exist_ok=True)
    wt = make_write_code_file_tool(base_dir=str(code_dir))

    abs_in = str(code_dir / "nested" / "a.py")
    res = json.loads(wt.invoke({"path": abs_in, "content": "y\n"}))
    assert res["success"] is True
    assert Path(res["path"]).resolve() == Path(abs_in).resolve()
    assert Path(abs_in).exists()


def test_write_tool_abs_system_path_rejected_not_written(monkeypatch, tmp_path):
    """越界多形态1：绝对系统路径 /tmp/... → 拒，且目标文件未落盘。"""
    ws = _patch_workspace(monkeypatch, tmp_path)
    code_dir = ws / "2409.05591" / "code"
    code_dir.mkdir(parents=True, exist_ok=True)
    wt = make_write_code_file_tool(base_dir=str(code_dir))

    victim = tmp_path / "system_evil.py"
    res = json.loads(wt.invoke({"path": str(victim), "content": "evil\n"}))
    assert res["success"] is False
    assert "code_output_dir" in res["error"]
    assert not victim.exists(), "越界目标绝不应落盘"


def test_write_tool_dotdot_escape_rejected_not_written(monkeypatch, tmp_path):
    """越界多形态2：相对 ../../etc/x → 拒，无文件落盘。"""
    ws = _patch_workspace(monkeypatch, tmp_path)
    code_dir = ws / "2409.05591" / "code"
    code_dir.mkdir(parents=True, exist_ok=True)
    wt = make_write_code_file_tool(base_dir=str(code_dir))

    res = json.loads(wt.invoke({"path": "../../etc/x", "content": "evil\n"}))
    assert res["success"] is False
    assert "code_output_dir" in res["error"]


def test_write_tool_workspace_inside_but_code_dir_outside_rejected(monkeypatch, tmp_path):
    """越界多形态3：绝对路径在 workspace 内但 code_dir 外（如 selected_repo）→ 拒，未落盘。

    这正是坑1 核心：write 只锚 code_dir，不准写到 workspace 其它位置（区别于 read/list）。
    """
    ws = _patch_workspace(monkeypatch, tmp_path)
    code_dir = ws / "2409.05591" / "code"
    code_dir.mkdir(parents=True, exist_ok=True)
    repo = ws / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    wt = make_write_code_file_tool(base_dir=str(code_dir))

    victim = repo / "hijack.py"
    res = json.loads(wt.invoke({"path": str(victim), "content": "x\n"}))
    assert res["success"] is False
    assert "code_output_dir" in res["error"]
    assert not victim.exists()


def test_write_tool_dotdot_resolving_inside_code_dir_allowed(monkeypatch, tmp_path):
    """相对 `sub/../a.py` resolve 后落 code_dir 内 → 放行（合法 .. 不应误杀）。"""
    ws = _patch_workspace(monkeypatch, tmp_path)
    code_dir = ws / "2409.05591" / "code"
    code_dir.mkdir(parents=True, exist_ok=True)
    wt = make_write_code_file_tool(base_dir=str(code_dir))

    res = json.loads(wt.invoke({"path": "sub/../a.py", "content": "ok\n"}))
    assert res["success"] is True
    assert Path(res["path"]).resolve() == (code_dir / "a.py").resolve()


# =================== 坑2 arxiv_id 非 dict 鲁棒性 ===================


@pytest.mark.parametrize("bad_meta", ["a string", 12345, ["list"], None])
def test_context_arxiv_id_none_when_paper_meta_not_dict(monkeypatch, tmp_path, bad_meta):
    """坑2 边界：paper_meta 非 dict（str/int/list）或缺失 → arxiv_id None，不抛。"""
    _patch_workspace(monkeypatch, tmp_path)
    # None 走 _make_state 的默认值替换，需显式塞进 state 后再覆盖
    state = _make_state(tmp_path)
    state["paper_meta"] = bad_meta
    payload = _build_coding_context(state)
    assert payload["arxiv_id"] is None


# =================== B2 向后兼容补强（无 base_dir 越界形态）=======


def test_b2_no_base_dir_dotdot_escape_rejected(monkeypatch, tmp_path):
    """B2 无参：相对/拼接逃逸到 workspace 外 → 拒，错误含 WORKSPACE_DIR。"""
    ws = _patch_workspace(monkeypatch, tmp_path)
    wt = make_write_code_file_tool()
    escape = str(ws / ".." / "escaped.py")  # resolve → tmp_path/escaped.py（workspace 外）
    res = json.loads(wt.invoke({"path": escape, "content": "x\n"}))
    assert res["success"] is False
    assert "WORKSPACE_DIR" in res["error"]
    assert not (tmp_path / "escaped.py").exists()


def test_b2_no_base_dir_subdir_write_creates_parents(monkeypatch, tmp_path):
    """B2 无参：workspace 内深层子目录写入自动建父目录并落盘。"""
    ws = _patch_workspace(monkeypatch, tmp_path)
    wt = make_write_code_file_tool()
    target = ws / "a" / "b" / "c.py"
    res = json.loads(wt.invoke({"path": str(target), "content": "deep\n"}))
    assert res["success"] is True
    assert target.exists()


# =================== 三处幂等：修复回合 state 带 code_output_dir ====


def test_three_way_idempotent_in_fix_round(monkeypatch, tmp_path):
    """修复回合（state 已带 code_output_dir + execution_result + fix_count>0）下，
    build_context 注入值 / write 工具落点 / map_result code_dir 仍三处同值。"""
    ws = _patch_workspace(monkeypatch, tmp_path)
    fixed_dir = str((ws / "fixdir" / "code").resolve())
    Path(fixed_dir).mkdir(parents=True, exist_ok=True)
    state = _make_state(
        tmp_path,
        code_output_dir=fixed_dir,
        execution_result={"success": False, "errors": ["[error_category=import] x"], "logs": "l"},
        fix_loop_count=2,
    )

    ctx_dir = _build_coding_context(state)["code_output_dir"]
    assert ctx_dir == fixed_dir

    tools = _get_coding_tools(state)
    res = json.loads(tools[0].invoke({"path": "fix.py", "content": "f\n"}))
    assert res["success"] is True
    assert Path(res["path"]).parent == Path(fixed_dir).resolve()

    updates = _map_coding_result({"files_written": ["fix.py"], "summary": "s"}, state)
    assert updates["code_output_dir"] == fixed_dir == ctx_dir


# =================== 真实 FS：write→read 闭环一致 =================


def test_real_fs_write_then_read_roundtrip(monkeypatch, tmp_path):
    """真实 FS：write 工具写入 code_dir 后，read 工具回读内容逐字节一致。

    防再退化的真实文件系统断言（mock ToolMessage 照不到原 bug 的根因）。
    """
    ws = _patch_workspace(monkeypatch, tmp_path)
    state = _make_state(tmp_path)
    code_dir = _resolve_code_output_dir(state)

    wt = _get_coding_tools(state)[0]
    content = "import torch\nprint('<METRICS>{\"acc\": 0.9}</METRICS>')\n"
    res = json.loads(wt.invoke({"path": "reproduce.py", "content": content}))
    assert res["success"] is True

    real_file = Path(code_dir) / "reproduce.py"
    assert real_file.exists()

    rt = make_read_code_file_tool()
    read_back = rt.invoke({"path": str(real_file)})
    assert read_back == content
    assert not read_back.startswith("Error")
