"""Sprint 3 任务 B2 独立验收补强：core/tools/code_fs_tools.py 边界与端到端。

由 @测试工程师代理 在开发交付 tests/test_sprint3_b2.py（21 用例）基础上补强，
不修改开发既有用例。聚焦验收任务点名的重点项：

    1. BUG-S1-02 端到端闭环：三工具输出真正喂给 react_base.extract_last_tool_result
       能被正确 json.loads 回 dict（str(dict) 反例对照证明这正是当年永久失败根因）。
    2. 路径越界多形态：符号链接逃逸、WORKSPACE_DIR 边界自身、借建父目录逃逸。
    3. read 截断边界：恰好 == TOOL_RESULT_MAX_LENGTH 不截 vs 超 1 字节截。
    4. list truncated 边界：恰好 cap 不 truncated vs cap+1 truncated。
    5. write 多级父目录创建仍受 workspace 限定。
    6. 异常路径补强（read 目标是目录、空目录、空文件）。

测试基建沿用开发用例的 workspace fixture 范式（monkeypatch WORKSPACE_DIR 到 tmp_path）。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from langchain_core.messages import ToolMessage

from core.react_base import extract_last_tool_result
from core.tools import code_fs_tools
from core.tools.code_fs_tools import (
    make_list_dir_tool,
    make_read_code_file_tool,
    make_write_code_file_tool,
)


@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    """把 WORKSPACE_DIR patch 到 tmp_path 下受控目录，返回 (ws, code_dir)。"""
    ws = tmp_path / "workspace"
    code_dir = ws / "thread-001" / "code"
    code_dir.mkdir(parents=True)
    monkeypatch.setattr(code_fs_tools, "WORKSPACE_DIR", ws)
    return ws, code_dir


# ===========================================================================
# BUG-S1-02 端到端闭环：工具输出 → ToolMessage → extract_last_tool_result
# ===========================================================================


def test_bug_s1_02_write_output_parseable_by_extract(workspace):
    """write 工具输出经 ToolMessage 喂 extract_last_tool_result 能解析回 dict。

    这正是 BUG-S1-02 当年 str(dict) 导致下游 json.loads 永久失败的真实下游。
    """
    ws, code_dir = workspace
    tool = make_write_code_file_tool()
    target = code_dir / "模型_训练.py"
    out = tool.invoke({"path": str(target), "content": "# 中文内容\n"})

    msg = ToolMessage(content=out, name="write_code_file", tool_call_id="c1")
    parsed = extract_last_tool_result([msg], "write_code_file")

    assert parsed is not None  # str(dict) 反例会返回 None
    assert isinstance(parsed, dict)
    assert parsed["success"] is True
    assert parsed["path"] == str(target.resolve())  # 中文路径字段无损往返
    assert parsed["bytes_written"] == len("# 中文内容\n".encode("utf-8"))


def test_bug_s1_02_list_output_parseable_by_extract(workspace):
    """list 工具输出（含中文条目）经 extract_last_tool_result 解析回 dict。"""
    ws, code_dir = workspace
    (code_dir / "数据集.csv").write_text("", encoding="utf-8")
    (code_dir / "src").mkdir()
    tool = make_list_dir_tool()
    out = tool.invoke({"path": str(code_dir)})

    msg = ToolMessage(content=out, name="list_dir", tool_call_id="c2")
    parsed = extract_last_tool_result([msg], "list_dir")

    assert parsed is not None
    assert parsed["success"] is True
    assert "数据集.csv" in parsed["entries"]
    assert "src/" in parsed["entries"]


def test_bug_s1_02_error_json_parseable_by_extract(workspace):
    """工具的错误 JSON（success=False）同样合规可解析（错误也是结构化往返）。"""
    tool = make_write_code_file_tool()
    out = tool.invoke({"path": "/tmp/evil.py", "content": "x"})

    msg = ToolMessage(content=out, name="write_code_file", tool_call_id="c3")
    parsed = extract_last_tool_result([msg], "write_code_file")

    assert parsed is not None
    assert parsed["success"] is False
    assert "越界" in parsed["error"]


def test_bug_s1_02_str_dict_counterexample_fails_to_parse():
    """反例对照：str(dict)（Python repr 单引号）喂 extract_last_tool_result 解析失败。

    锚定 BUG-S1-02 根因——证明若 B2 用 str(dict) 替代 json.dumps，下游会永久失败。
    本用例不依赖被测代码，是对验收基线的负向锚点。
    """
    bad = str({"success": True, "path": "x", "bytes_written": 1})
    assert "'" in bad and '"' not in bad  # 确认是 repr 单引号形态
    msg = ToolMessage(content=bad, name="t", tool_call_id="bad")
    # extract 无法把单引号 repr 解析成 dict（无平衡双引号 JSON）
    assert extract_last_tool_result([msg], "t") is None


def test_bug_s1_02_truncated_read_not_misparsed():
    """read 返回纯文本（非 JSON）时 extract 不会误解析成 dict（语义边界正确）。"""
    plain = "import torch\nprint(1)\n"
    msg = ToolMessage(content=plain, name="read_code_file", tool_call_id="r1")
    # 纯文本无花括号，extract 返回 None（read 本就不走 JSON 回填路径）
    assert extract_last_tool_result([msg], "read_code_file") is None


# ===========================================================================
# 路径越界多形态：符号链接逃逸 / 边界自身 / 借建父目录逃逸
# ===========================================================================


def test_symlink_escape_write_rejected(workspace, tmp_path):
    """workspace 内符号链接指向外部目录，写经该链接被 resolve() 拆穿拒绝。"""
    ws, code_dir = workspace
    outside = tmp_path / "outside"
    outside.mkdir()
    link = code_dir / "evil_link"
    link.symlink_to(outside)  # workspace 内的链接，真实落点在外
    tool = make_write_code_file_tool()
    out = tool.invoke({"path": str(link / "leak.py"), "content": "x"})
    payload = json.loads(out)
    assert payload["success"] is False
    assert "越界" in payload["error"]
    assert not (outside / "leak.py").exists()


def test_symlink_escape_read_rejected(workspace, tmp_path):
    """workspace 内符号链接指向外部文件，读经该链接被 resolve() 拆穿拒绝。"""
    ws, code_dir = workspace
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP SECRET", encoding="utf-8")
    link = code_dir / "secret_link.txt"
    link.symlink_to(secret)
    tool = make_read_code_file_tool()
    out = tool.invoke({"path": str(link)})
    assert isinstance(out, str)
    assert out.startswith("Error")
    assert "越界" in out
    assert "TOP SECRET" not in out


def test_symlink_escape_list_rejected(workspace, tmp_path):
    """workspace 内符号链接指向外部目录，list 经该链接被 resolve() 拆穿拒绝。"""
    ws, code_dir = workspace
    outside = tmp_path / "outside_dir"
    outside.mkdir()
    (outside / "leaked.py").write_text("", encoding="utf-8")
    link = code_dir / "dir_link"
    link.symlink_to(outside)
    tool = make_list_dir_tool()
    out = tool.invoke({"path": str(link)})
    payload = json.loads(out)
    assert payload["success"] is False
    assert "越界" in payload["error"]


def test_workspace_dir_boundary_itself_allowed(workspace):
    """WORKSPACE_DIR 边界自身（target == workspace）被接受（与 git_tools 不变量一致）。"""
    ws, code_dir = workspace
    tool = make_list_dir_tool()
    out = tool.invoke({"path": str(ws)})
    payload = json.loads(out)
    assert payload["success"] is True
    assert payload["path"] == str(ws.resolve())


def test_write_create_parent_dirs_still_workspace_bound(workspace):
    """多级不存在父目录自动创建，但仍受 workspace 限定——不能借建目录逃逸。"""
    ws, code_dir = workspace
    tool = make_write_code_file_tool()
    # 深层新建路径在 workspace 内 → 成功并建出多级父目录
    deep = code_dir / "a" / "b" / "c" / "deep.py"
    out = tool.invoke({"path": str(deep), "content": "ok\n"})
    payload = json.loads(out)
    assert payload["success"] is True
    assert deep.exists()
    assert deep.parent.is_dir()


def test_write_dotdot_create_dir_escape_rejected(workspace, tmp_path):
    """借 ../ 在 workspace 外建多级目录 + 写文件被拒，外部目录不被创建。"""
    ws, code_dir = workspace
    escape = code_dir / ".." / ".." / ".." / "newdir" / "x" / "leak.py"
    out = tool_write_invoke(escape, "x")
    payload = json.loads(out)
    assert payload["success"] is False
    assert "越界" in payload["error"]
    # 校验在 mkdir 之前：外部目录不应被创建
    assert not (tmp_path / "newdir").exists()


def tool_write_invoke(target: Path, content: str) -> str:
    return make_write_code_file_tool().invoke({"path": str(target), "content": content})


# ===========================================================================
# read 截断边界：== MAX 不截 vs MAX+1 截
# ===========================================================================


def test_read_exact_max_length_not_truncated(workspace, monkeypatch):
    """内容长度恰好等于 TOOL_RESULT_MAX_LENGTH：不截断，原样返回。"""
    ws, code_dir = workspace
    monkeypatch.setattr(code_fs_tools, "TOOL_RESULT_MAX_LENGTH", 100)
    target = code_dir / "exact.py"
    target.write_text("a" * 100, encoding="utf-8")
    tool = make_read_code_file_tool()
    out = tool.invoke({"path": str(target)})
    assert out == "a" * 100
    assert "truncated" not in out


def test_read_one_over_max_length_truncated(workspace, monkeypatch):
    """内容超 TOOL_RESULT_MAX_LENGTH 一个字符：截断到 MAX 并附固定标记。"""
    ws, code_dir = workspace
    monkeypatch.setattr(code_fs_tools, "TOOL_RESULT_MAX_LENGTH", 100)
    target = code_dir / "over.py"
    target.write_text("a" * 101, encoding="utf-8")
    tool = make_read_code_file_tool()
    out = tool.invoke({"path": str(target)})
    assert out.startswith("a" * 100)
    assert "[truncated at 100 chars]" in out
    # 截断标记为固定文本（不含动态片段，Prompt Cache 友好）
    assert out == "a" * 100 + "\n... [truncated at 100 chars]"


def test_read_empty_file_returns_empty_string(workspace):
    """空文件读取返回空字符串（不报错、不当作不存在）。"""
    ws, code_dir = workspace
    target = code_dir / "empty.py"
    target.write_text("", encoding="utf-8")
    tool = make_read_code_file_tool()
    out = tool.invoke({"path": str(target)})
    assert out == ""


# ===========================================================================
# list truncated 边界：== cap 不 truncated vs cap+1 truncated
# ===========================================================================


def test_list_exact_cap_not_truncated(workspace, monkeypatch):
    """目录恰好 cap 项：truncated=False，entries 全数返回。"""
    ws, code_dir = workspace
    monkeypatch.setattr(code_fs_tools, "_LIST_DIR_MAX_ITEMS", 5)
    for i in range(5):
        (code_dir / f"f{i}.py").write_text("", encoding="utf-8")
    tool = make_list_dir_tool()
    out = tool.invoke({"path": str(code_dir)})
    payload = json.loads(out)
    assert payload["truncated"] is False
    assert len(payload["entries"]) == 5


def test_list_one_over_cap_truncated(workspace, monkeypatch):
    """目录 cap+1 项：truncated=True，entries 截到 cap 项且仍字典序。"""
    ws, code_dir = workspace
    monkeypatch.setattr(code_fs_tools, "_LIST_DIR_MAX_ITEMS", 5)
    for i in range(6):
        (code_dir / f"f{i:02d}.py").write_text("", encoding="utf-8")
    tool = make_list_dir_tool()
    out = tool.invoke({"path": str(code_dir)})
    payload = json.loads(out)
    assert payload["truncated"] is True
    assert len(payload["entries"]) == 5
    # 截断取的是排序后的前 cap 项（字典序裁剪稳定）
    assert payload["entries"] == ["f00.py", "f01.py", "f02.py", "f03.py", "f04.py"]


def test_list_empty_dir_returns_empty_entries(workspace):
    """空目录列出返回空 entries、truncated=False。"""
    ws, code_dir = workspace
    empty = code_dir / "emptydir"
    empty.mkdir()
    tool = make_list_dir_tool()
    out = tool.invoke({"path": str(empty)})
    payload = json.loads(out)
    assert payload["success"] is True
    assert payload["entries"] == []
    assert payload["truncated"] is False


# ===========================================================================
# 异常不逃逸补强：read 目标是目录、write 目标是已存在目录
# ===========================================================================


def test_read_target_is_dir_no_raise(workspace):
    """read 目标是目录：返回 Error 字符串，不抛异常（区别于不存在）。"""
    ws, code_dir = workspace
    tool = make_read_code_file_tool()
    out = tool.invoke({"path": str(code_dir)})
    assert isinstance(out, str)
    assert out.startswith("Error")
    assert "目录" in out


def test_write_target_is_existing_dir_no_raise(workspace):
    """write 目标路径是已存在目录：IsADirectoryError 被兜底转错误 JSON，不抛。"""
    ws, code_dir = workspace
    existing_dir = code_dir / "iam_a_dir"
    existing_dir.mkdir()
    tool = make_write_code_file_tool()
    out = tool.invoke({"path": str(existing_dir), "content": "x"})
    payload = json.loads(out)
    assert payload["success"] is False
    assert "error" in payload


def test_serialize_default_str_handles_path(workspace):
    """_serialize_tool_result default=str 能兜底序列化 Path 等非原生 JSON 类型。"""
    out = code_fs_tools._serialize_tool_result({"p": Path("/x/y"), "n": 1})
    payload = json.loads(out)
    assert payload["n"] == 1
    assert payload["p"] == "/x/y" or payload["p"].endswith("x/y")
