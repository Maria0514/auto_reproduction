"""Sprint 3 任务 B2 自测：core/tools/code_fs_tools.py 三工具工厂。

覆盖 dev-plan §B2 CP-B2-1 ~ CP-B2-5。

测试策略（参考 sp2 tests/test_sprint2_a5.py / tests/test_pwc_tools.py 工具工厂范式）：
    - 用 tmp_path 把 WORKSPACE_DIR patch 到受控目录（monkeypatch.setattr(code_fs_tools,
      "WORKSPACE_DIR", ws)），在其下造测试路径，不污染真实 WORKSPACE_DIR；
    - 工具工厂返回 BaseTool，通过 .invoke({...}) 调用，断言 ToolMessage 输出。

硬约束验证：
    - 序列化禁 str(dict)：构造含中文 / 特殊字符的结果，断言输出可 json.loads 解析且
      中文未被转义成 \\uXXXX，键字典序（CP-B2-4）；
    - 路径越界一律 resolve()+is_relative_to 拒绝（CP-B2-1/2/3）；
    - 工具内部异常被 try/except 捕获转字符串返回，不打断 ReAct 子图（CP-B2-5）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from langchain_core.tools import BaseTool

from core.tools import code_fs_tools
from core.tools.code_fs_tools import (
    make_list_dir_tool,
    make_read_code_file_tool,
    make_write_code_file_tool,
)


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    """把 WORKSPACE_DIR patch 到 tmp_path 下的受控目录，返回 (ws, code_dir)。

    code_dir 模拟 code_output_dir（workspace/<thread>/code），是合法写入落点。
    """
    ws = tmp_path / "workspace"
    code_dir = ws / "thread-001" / "code"
    code_dir.mkdir(parents=True)
    monkeypatch.setattr(code_fs_tools, "WORKSPACE_DIR", ws)
    return ws, code_dir


# ========== CP-B2-1：write_code_file 成功 + 越界拒绝 ==========


def test_cp_b2_1_write_success_returns_valid_json(workspace):
    ws, code_dir = workspace
    tool = make_write_code_file_tool()
    target = code_dir / "train.py"

    out = tool.invoke({"path": str(target), "content": "print('hi')\n"})

    payload = json.loads(out)  # 合法 JSON，不报错
    assert payload["success"] is True
    assert payload["path"] == str(target.resolve())
    assert payload["bytes_written"] == len("print('hi')\n".encode("utf-8"))
    assert target.read_text(encoding="utf-8") == "print('hi')\n"


def test_cp_b2_1_write_creates_parent_dirs(workspace):
    ws, code_dir = workspace
    tool = make_write_code_file_tool()
    target = code_dir / "sub" / "deep" / "model.py"

    out = tool.invoke({"path": str(target), "content": "x = 1\n"})

    payload = json.loads(out)
    assert payload["success"] is True
    assert target.exists()


def test_cp_b2_1_write_absolute_outside_rejected(workspace):
    tool = make_write_code_file_tool()
    out = tool.invoke({"path": "/tmp/evil.py", "content": "x"})
    payload = json.loads(out)
    assert payload["success"] is False
    assert "越界" in payload["error"]
    assert not Path("/tmp/evil.py").exists()


def test_cp_b2_1_write_dotdot_escape_rejected(workspace):
    ws, code_dir = workspace
    tool = make_write_code_file_tool()
    # code_dir/../../../etc/escape.py 解析后逃出 workspace
    escape = code_dir / ".." / ".." / ".." / "escape_outside.py"
    out = tool.invoke({"path": str(escape), "content": "x"})
    payload = json.loads(out)
    assert payload["success"] is False
    assert "越界" in payload["error"]


# ========== CP-B2-2：read_code_file 读取 / 越界 / 不存在 ==========


def test_cp_b2_2_read_existing_returns_content(workspace):
    ws, code_dir = workspace
    target = code_dir / "train.py"
    target.write_text("import torch\nprint(42)\n", encoding="utf-8")
    tool = make_read_code_file_tool()

    out = tool.invoke({"path": str(target)})
    assert out == "import torch\nprint(42)\n"


def test_cp_b2_2_read_outside_rejected(workspace):
    tool = make_read_code_file_tool()
    out = tool.invoke({"path": "/etc/passwd"})
    assert isinstance(out, str)
    assert out.startswith("Error")
    assert "越界" in out


def test_cp_b2_2_read_missing_returns_error_string_no_raise(workspace):
    ws, code_dir = workspace
    tool = make_read_code_file_tool()
    out = tool.invoke({"path": str(code_dir / "nonexistent.py")})
    assert isinstance(out, str)
    assert out.startswith("Error")
    assert "不存在" in out


def test_cp_b2_2_read_dir_returns_error_string(workspace):
    ws, code_dir = workspace
    tool = make_read_code_file_tool()
    out = tool.invoke({"path": str(code_dir)})
    assert isinstance(out, str)
    assert out.startswith("Error")


def test_cp_b2_2_read_truncates_long_content(workspace, monkeypatch):
    ws, code_dir = workspace
    monkeypatch.setattr(code_fs_tools, "TOOL_RESULT_MAX_LENGTH", 100)
    target = code_dir / "big.py"
    target.write_text("a" * 5000, encoding="utf-8")
    tool = make_read_code_file_tool()
    out = tool.invoke({"path": str(target)})
    assert "[truncated at 100 chars]" in out


# ========== CP-B2-3：list_dir 列目录 + 越界 ==========


def test_cp_b2_3_list_dir_returns_valid_json(workspace):
    ws, code_dir = workspace
    (code_dir / "a.py").write_text("", encoding="utf-8")
    (code_dir / "b.py").write_text("", encoding="utf-8")
    (code_dir / "subdir").mkdir()
    tool = make_list_dir_tool()

    out = tool.invoke({"path": str(code_dir)})
    payload = json.loads(out)
    assert payload["success"] is True
    assert payload["path"] == str(code_dir.resolve())
    # entries 字典序，子目录带 "/" 后缀
    assert payload["entries"] == ["a.py", "b.py", "subdir/"]
    assert payload["truncated"] is False


def test_cp_b2_3_list_dir_outside_rejected(workspace):
    tool = make_list_dir_tool()
    out = tool.invoke({"path": "/tmp"})
    payload = json.loads(out)
    assert payload["success"] is False
    assert "越界" in payload["error"]


def test_cp_b2_3_list_dir_missing_returns_error_json(workspace):
    ws, code_dir = workspace
    tool = make_list_dir_tool()
    out = tool.invoke({"path": str(code_dir / "nope")})
    payload = json.loads(out)
    assert payload["success"] is False
    assert "不存在" in payload["error"]


def test_cp_b2_3_list_dir_on_file_returns_error_json(workspace):
    ws, code_dir = workspace
    f = code_dir / "f.py"
    f.write_text("", encoding="utf-8")
    tool = make_list_dir_tool()
    out = tool.invoke({"path": str(f)})
    payload = json.loads(out)
    assert payload["success"] is False
    assert "不是目录" in payload["error"]


def test_cp_b2_3_list_dir_truncates_over_cap(workspace, monkeypatch):
    ws, code_dir = workspace
    monkeypatch.setattr(code_fs_tools, "_LIST_DIR_MAX_ITEMS", 3)
    for i in range(10):
        (code_dir / f"f{i}.py").write_text("", encoding="utf-8")
    tool = make_list_dir_tool()
    out = tool.invoke({"path": str(code_dir)})
    payload = json.loads(out)
    assert payload["success"] is True
    assert len(payload["entries"]) == 3
    assert payload["truncated"] is True


# ========== CP-B2-4：BaseTool 实例 + 序列化合规（禁 str(dict)） ==========


def test_cp_b2_4_factories_return_basetool():
    assert isinstance(make_write_code_file_tool(), BaseTool)
    assert isinstance(make_read_code_file_tool(), BaseTool)
    assert isinstance(make_list_dir_tool(), BaseTool)


def test_cp_b2_4_write_chinese_not_escaped_and_sorted_keys(workspace):
    """中文不转义（ensure_ascii=False）+ json.loads 可解析；禁 str(dict)。"""
    ws, code_dir = workspace
    tool = make_write_code_file_tool()
    # 用含中文 / 特殊字符的文件名（落在路径里，回写到 path 字段）
    target = code_dir / "模型_训练.py"
    out = tool.invoke({"path": str(target), "content": "# 中文注释\n"})

    # 1) 合法 JSON，json.loads 不报错（str(dict) 会因单引号失败）
    payload = json.loads(out)
    # 2) 中文未被转义成 \uXXXX
    assert "\\u" not in out
    assert "模型_训练.py" in out
    # 3) 双引号（JSON）而非单引号（Python repr）
    assert "'" not in out or '"' in out
    assert "'success'" not in out  # repr 形式的关键标志
    assert payload["success"] is True


def test_cp_b2_4_list_dir_chinese_not_escaped(workspace):
    ws, code_dir = workspace
    (code_dir / "数据集.csv").write_text("", encoding="utf-8")
    tool = make_list_dir_tool()
    out = tool.invoke({"path": str(code_dir)})
    payload = json.loads(out)
    assert "\\u" not in out
    assert "数据集.csv" in payload["entries"]


def test_cp_b2_4_serialize_helper_sorted_keys_ensure_ascii():
    """直接验证 _serialize_tool_result 合规：sort_keys + ensure_ascii=False。"""
    out = code_fs_tools._serialize_tool_result({"b": "值", "a": 1})
    assert out == '{"a": 1, "b": "值"}'  # 键字典序 a<b + 中文不转义
    assert json.loads(out) == {"a": 1, "b": "值"}


# ========== CP-B2-5：内部异常被捕获转字符串，不打断 ReAct 子图 ==========


def test_cp_b2_5_write_io_error_caught(workspace, monkeypatch):
    """write 遇 OSError（如写权限/路径冲突）被 try/except 捕获，不抛异常。"""
    ws, code_dir = workspace

    def _boom(*args, **kwargs):
        raise OSError("disk full")

    # patch Path.write_text 触发 IO 错误
    monkeypatch.setattr(Path, "write_text", _boom)
    tool = make_write_code_file_tool()
    target = code_dir / "x.py"
    # 不应抛异常
    out = tool.invoke({"path": str(target), "content": "x"})
    payload = json.loads(out)
    assert payload["success"] is False
    assert "disk full" in payload["error"]


def test_cp_b2_5_read_io_error_caught(workspace, monkeypatch):
    ws, code_dir = workspace
    target = code_dir / "x.py"
    target.write_text("data", encoding="utf-8")

    def _boom(*args, **kwargs):
        raise OSError("read error")

    monkeypatch.setattr(Path, "read_text", _boom)
    tool = make_read_code_file_tool()
    out = tool.invoke({"path": str(target)})
    assert isinstance(out, str)
    assert out.startswith("Error")
    assert "read error" in out


def test_cp_b2_5_list_dir_io_error_caught(workspace, monkeypatch):
    ws, code_dir = workspace

    def _boom(*args, **kwargs):
        raise OSError("listdir error")

    monkeypatch.setattr(code_fs_tools.os, "listdir", _boom)
    tool = make_list_dir_tool()
    out = tool.invoke({"path": str(code_dir)})
    payload = json.loads(out)
    assert payload["success"] is False
    assert "listdir error" in payload["error"]
