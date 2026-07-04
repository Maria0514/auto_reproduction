"""Sprint 4 任务 C1：core/tools/run_command_tool.py 自测（S4-01）。

覆盖 dev-plan §4 任务 C1 CP-C1-1 ~ CP-C1-5（architecture §2.2 骨架 + §5 Q-B1
边界 + §9.4 脱敏落点）。全部离线单测（真实子进程用 sys.executable，零 LLM）。

- CP-C1-1 正常 smoke 返回合法 JSON、exit_code=0；json.loads 可解析 + sort_keys
  + ensure_ascii=False（禁 str(dict) 断言）
- CP-C1-2 越界 base_dir → 结构化错误 JSON + WARNING，_run_subprocess 0 次调用；
  解析失败（不闭合引号）/ 空命令 → 结构化错误不炸子图
- CP-C1-3 超时护栏：真实 sleep 子进程 + monkeypatch 短超时 → timed_out=True、
  子进程树被杀（AC-S4-02）
- CP-C1-4 脱敏：register_sensitive_value 注入已知 token 后，stdout/stderr 含
  token 的输出返回前被 mask 为 ****
- CP-C1-5 不写 execution_result：返回结构无 metrics / success 语义键（Q-B1 红线 3）
"""

from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path
from typing import Any, Dict

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import config  # noqa: E402
from core import secrets_store  # noqa: E402
from core.tools import run_command_tool as rct_module  # noqa: E402
from core.tools.run_command_tool import make_run_command_tool  # noqa: E402


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_process_sensitive_set():
    secrets_store._SENSITIVE_VALUES.clear()
    yield
    secrets_store._SENSITIVE_VALUES.clear()


@pytest.fixture()
def workspace(tmp_path, monkeypatch) -> Path:
    """WORKSPACE_DIR 隔离到 tmp_path：越界校验基准 + mask_value 的 .secrets 落点。"""
    import sandbox.local_venv as lv

    ws = tmp_path / "workspace"
    ws.mkdir()
    monkeypatch.setattr(config, "WORKSPACE_DIR", ws)
    monkeypatch.setattr(lv, "WORKSPACE_DIR", ws)
    return ws


@pytest.fixture()
def code_dir(workspace: Path) -> Path:
    d = workspace / "task" / "code"
    d.mkdir(parents=True)
    return d


def _invoke(tool, command: str) -> str:
    return tool.invoke({"command": command})


# ===========================================================================
# CP-C1-1 正常 smoke：合法 JSON、exit_code=0、sort_keys、ensure_ascii=False
# ===========================================================================


def test_cp_c1_1_normal_smoke_returns_valid_json(code_dir: Path):
    tool = make_run_command_tool(base_dir=str(code_dir))
    out = _invoke(tool, f'{sys.executable} -c "print(1)"')

    # 合法 JSON（str(dict) 单引号 repr 会在此失败，BUG-S1-02 禁令）
    assert isinstance(out, str) and out.startswith("{")
    parsed = json.loads(out)
    assert parsed["exit_code"] == 0
    assert "1" in parsed["stdout_tail"]
    assert parsed["timed_out"] is False
    assert parsed["truncated"] is False

    # sort_keys：序列化串中键按字典序出现
    keys = ["exit_code", "stderr_tail", "stdout_tail", "timed_out", "truncated"]
    positions = [out.index(f'"{k}"') for k in keys]
    assert positions == sorted(positions), "返回 JSON 必须 sort_keys=True"


def test_cp_c1_1_ensure_ascii_false_non_ascii_verbatim(code_dir: Path):
    """ensure_ascii=False：非 ASCII 输出原样进 JSON（不出现 \\uXXXX 转义）。"""
    tool = make_run_command_tool(base_dir=str(code_dir))
    out = _invoke(tool, f'{sys.executable} -c "print(\'中文输出\')"')
    assert "中文输出" in out, "ensure_ascii=False 应保留非 ASCII 原文"
    assert "\\u4e2d" not in out
    assert json.loads(out)["exit_code"] == 0


def test_cp_c1_1_cwd_anchored_to_base_dir(code_dir: Path):
    """cwd 锚定 base_dir（写文件落在 code_dir 内证明）。"""
    tool = make_run_command_tool(base_dir=str(code_dir))
    out = _invoke(
        tool,
        f'{sys.executable} -c "import pathlib; pathlib.Path(\'probe.txt\').write_text(\'ok\')"',
    )
    assert json.loads(out)["exit_code"] == 0
    assert (code_dir / "probe.txt").read_text() == "ok"


# ===========================================================================
# CP-C1-2 越界 / 解析失败 / 空命令 → 结构化错误，不炸子图
# ===========================================================================


def test_cp_c1_2_out_of_workspace_base_dir_rejected(workspace: Path, caplog, monkeypatch):
    calls = {"n": 0}
    real = rct_module._run_subprocess

    def _spy(*args: Any, **kwargs: Any):
        calls["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(rct_module, "_run_subprocess", _spy)

    tool = make_run_command_tool(base_dir="/etc")
    with caplog.at_level(logging.WARNING):
        out = _invoke(tool, f'{sys.executable} -c "print(1)"')

    parsed = json.loads(out)
    assert "越界" in parsed["error"]
    assert parsed["exit_code"] == -1
    assert calls["n"] == 0, "越界校验必须在 _run_subprocess 之前（0 次调用）"
    assert any("越界" in r.message for r in caplog.records), "越界必须打 WARNING"


def test_cp_c1_2_unparseable_command_structured_error(code_dir: Path):
    tool = make_run_command_tool(base_dir=str(code_dir))
    out = _invoke(tool, 'echo "unclosed quote')  # 不闭合引号 → shlex ValueError
    parsed = json.loads(out)
    assert "命令解析失败" in parsed["error"]
    assert parsed["exit_code"] == -1


def test_cp_c1_2_empty_command_structured_error(code_dir: Path):
    tool = make_run_command_tool(base_dir=str(code_dir))
    for cmd in ("", "   "):
        parsed = json.loads(_invoke(tool, cmd))
        assert parsed["error"] == "空命令"
        assert parsed["exit_code"] == -1


def test_cp_c1_2_nonexistent_binary_no_exception(code_dir: Path):
    """启动失败（命令不存在）由 _run_subprocess OSError 兜底转 exit_code=-1，不逃逸。"""
    tool = make_run_command_tool(base_dir=str(code_dir))
    parsed = json.loads(_invoke(tool, "definitely_no_such_binary_xyz --help"))
    assert parsed["exit_code"] == -1
    assert "subprocess start failed" in parsed["stderr_tail"]


# ===========================================================================
# CP-C1-3 超时护栏：真实 sleep + monkeypatch 短超时 → timed_out=True、子树被杀
# ===========================================================================


def test_cp_c1_3_timeout_kills_subprocess_tree(code_dir: Path, monkeypatch):
    monkeypatch.setattr(config, "RUN_COMMAND_TIMEOUT", 1)

    tool = make_run_command_tool(base_dir=str(code_dir))
    start = time.monotonic()
    out = _invoke(tool, f'{sys.executable} -c "import time; time.sleep(30)"')
    elapsed = time.monotonic() - start

    parsed = json.loads(out)
    assert parsed["timed_out"] is True
    assert parsed["exit_code"] != 0, "超时被杀 exit_code 必须非 0"
    assert elapsed < 10, f"子进程树应被立即杀掉而非等 sleep 结束（实测 {elapsed:.1f}s）"


def test_cp_c1_3_timeout_read_dynamically_from_config(code_dir: Path, monkeypatch):
    """工具体运行期动态读 config.RUN_COMMAND_TIMEOUT（monkeypatch 生效即证）。"""
    seen: Dict[str, Any] = {}

    def _fake_run(argv, *, cwd, timeout, output_max_bytes, extra_env=None):
        seen.update(timeout=timeout, output_max_bytes=output_max_bytes)
        from sandbox.local_venv import SandboxRunResult
        return SandboxRunResult(
            exit_code=0, stdout="", stderr="", duration_seconds=0.0,
            timed_out=False, output_truncated=False, command=list(argv))

    monkeypatch.setattr(rct_module, "_run_subprocess", _fake_run)
    monkeypatch.setattr(config, "RUN_COMMAND_TIMEOUT", 7)

    tool = make_run_command_tool(base_dir=str(code_dir))
    _invoke(tool, "true")
    assert seen["timeout"] == 7
    assert seen["output_max_bytes"] == config.SANDBOX_OUTPUT_MAX_BYTES


# ===========================================================================
# CP-C1-4 脱敏：已登记敏感值在 stdout/stderr 中被 mask 为 ****
# ===========================================================================


def test_cp_c1_4_stdout_stderr_masked(code_dir: Path):
    token = "ghp_SECRET_TOKEN_c1_4_abcdef"
    secrets_store.register_sensitive_value(token)

    tool = make_run_command_tool(base_dir=str(code_dir))
    out = _invoke(
        tool,
        f'{sys.executable} -c "import sys; print(\'out {token} tail\');'
        f' print(\'err {token} tail\', file=sys.stderr)"',
    )
    parsed = json.loads(out)
    assert token not in out, "token 不得出现在工具返回值任何位置"
    assert "****" in parsed["stdout_tail"]
    assert "****" in parsed["stderr_tail"]
    assert "out" in parsed["stdout_tail"] and "tail" in parsed["stdout_tail"]


# ===========================================================================
# CP-C1-5 不写 execution_result：返回结构无 metrics / success 语义键
# ===========================================================================


def test_cp_c1_5_no_metrics_no_success_keys(code_dir: Path):
    tool = make_run_command_tool(base_dir=str(code_dir))
    parsed = json.loads(_invoke(tool, f'{sys.executable} -c "print(1)"'))

    assert set(parsed.keys()) == {
        "exit_code", "stdout_tail", "stderr_tail", "timed_out", "truncated",
    }, "返回结构必须恰为 5 键（Q-B1 红线 3：B 档判定无从消费）"
    for forbidden in ("metrics", "success", "logs", "artifacts"):
        assert forbidden not in parsed

    # 错误分支同样无 success/metrics 语义键
    err = json.loads(_invoke(tool, ""))
    assert set(err.keys()) == {"error", "exit_code"}


def test_cp_c1_5_docstring_discipline_no_dynamic_vars(code_dir: Path):
    """docstring 纪律：写明轻量验证边界 + 禁训练；零动态变量（两工厂实例字节一致）。"""
    t1 = make_run_command_tool(base_dir=str(code_dir))
    t2 = make_run_command_tool(base_dir="/other/dir", extra_env={"X": "1"})
    assert t1.description == t2.description, "docstring 不得含工厂入参等动态变量"
    for kw in ("轻量验证", "禁止", "训练"):
        assert kw in t1.description
    assert str(code_dir) not in t1.description
