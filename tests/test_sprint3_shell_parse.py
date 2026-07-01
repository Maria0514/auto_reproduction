"""execution 节点 shell 复合命令安全解析单测（BUG: LLM 规划的 shell 复合命令无法执行）。

覆盖（方向 2，禁 shell=True，仅安全解析少量 shell 语义）：
    - 顶层 && 拆分 + 短路；; 顺序无条件；
    - cd 改 current_dir 且跨子命令/跨 step 持久；cd 越界被拒；
    - source / . 丢弃；裸 pip -> python -m pip；裸 python -> venv python；
    - glob 展开（含展开为空保留原样）；
    - 注入字符串安全回归（; / $(...) 不触发 shell 解释执行）；
    - 向后兼容（单条原子命令仍正常）。

全部用 **mock run_in_venv**（记录 argv + cwd，不真跑子进程），不碰 LLM/deepxiv/真 subprocess。
glob 用 workspace 下真实临时文件验证展开（不执行命令）。
"""
from __future__ import annotations

import importlib
import os
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pytest

execution_module = importlib.import_module("core.nodes.execution")

from config import WORKSPACE_DIR  # noqa: E402

PY = str(WORKSPACE_DIR / ".venv" / "bin" / "python")


class RecordingRunner:
    """记录每次 run_in_venv 的 (argv, cwd)，按预设 exit_code 序列返回 FakeRunResult。"""

    def __init__(self, exit_codes: List[int] | None = None) -> None:
        self.calls: List[Tuple[List[str], str]] = []
        self._codes = list(exit_codes or [])
        self._i = 0

    def __call__(self, python_exe: str, command: List[str], work_dir: str, *a: Any, **k: Any):
        self.calls.append((list(command), work_dir))
        code = 0
        if self._i < len(self._codes):
            code = self._codes[self._i]
        self._i += 1
        from sandbox.local_venv import SandboxRunResult
        return SandboxRunResult(
            exit_code=code, stdout="", stderr="", duration_seconds=0.1,
            timed_out=False, output_truncated=False, command=command,
        )


def _ws_dir(name: str) -> str:
    d = WORKSPACE_DIR / "shell-parse-test" / name
    d.mkdir(parents=True, exist_ok=True)
    return str(d)


# ---------------------------------------------------------------------------
# _split_top_level：顶层拆分 + 引号保护
# ---------------------------------------------------------------------------


def test_split_top_level_double_amp():
    subs = execution_module._split_top_level("git clone X && cd repo")
    assert subs == [(["git", "clone", "X"], ""), (["cd", "repo"], "&&")]


def test_split_top_level_semicolon():
    subs = execution_module._split_top_level("a b ; c d")
    assert subs == [(["a", "b"], ""), (["c", "d"], ";")]


def test_split_top_level_quoted_not_split():
    # 引号内的 ; 与 && 不应被当作连接符（shlex 剥引号后成单 token）。
    subs = execution_module._split_top_level('python -c "print(1); print(2)"')
    assert subs == [(["python", "-c", "print(1); print(2)"], "")]


def test_step_to_command_single_atomic():
    subs = execution_module._step_to_command({"command": "python run.py --x 1"}, PY)
    assert subs == [(["python", "run.py", "--x", "1"], "")]


def test_step_to_command_empty():
    assert execution_module._step_to_command({"command": ""}, PY) is None
    assert execution_module._step_to_command("   ", PY) is None


# ---------------------------------------------------------------------------
# _rewrite_interpreter：裸 python / pip 改写
# ---------------------------------------------------------------------------


def test_rewrite_python():
    assert execution_module._rewrite_interpreter(["python", "x.py"], PY) == [PY, "x.py"]
    assert execution_module._rewrite_interpreter(["python3", "x.py"], PY) == [PY, "x.py"]


def test_rewrite_pip_to_module():
    assert execution_module._rewrite_interpreter(["pip", "install", "torch"], PY) == [
        PY, "-m", "pip", "install", "torch"
    ]


def test_rewrite_noop_for_other():
    assert execution_module._rewrite_interpreter(["git", "clone", "X"], PY) == ["git", "clone", "X"]


# ---------------------------------------------------------------------------
# _resolve_cd：相对解析 + 边界校验
# ---------------------------------------------------------------------------


def test_resolve_cd_relative():
    base = _ws_dir("cdbase")
    sub = Path(base) / "repo"
    sub.mkdir(exist_ok=True)
    assert execution_module._resolve_cd("repo", base) == str(sub.resolve())


def test_resolve_cd_escape_rejected():
    base = _ws_dir("cdesc")
    from core.errors import SandboxCreationError
    with pytest.raises(SandboxCreationError):
        execution_module._resolve_cd("../../../../etc", base)


def test_resolve_cd_abs_escape_rejected():
    base = _ws_dir("cdabs")
    from core.errors import SandboxCreationError
    with pytest.raises(SandboxCreationError):
        execution_module._resolve_cd("/etc", base)


# ---------------------------------------------------------------------------
# _expand_globs：glob 展开 + 空保留
# ---------------------------------------------------------------------------


def test_expand_globs_matches():
    d = _ws_dir("glob1")
    for fn in ("a.py", "b.py", "c.txt"):
        Path(d, fn).write_text("x")
    out = execution_module._expand_globs(["py_compile", "*.py"], d)
    assert out[0] == "py_compile"
    assert sorted(out[1:]) == ["a.py", "b.py"]


def test_expand_globs_empty_kept():
    d = _ws_dir("glob2")
    out = execution_module._expand_globs(["py_compile", "*.nonexist"], d)
    assert out == ["py_compile", "*.nonexist"]  # 展开为空：保留原样


# ---------------------------------------------------------------------------
# _run_step_subcommands：&& 短路 / ; 顺序 / cd 持久 / source 丢弃
# ---------------------------------------------------------------------------


def test_amp_shortcircuit(monkeypatch):
    base = _ws_dir("amp2")
    runner = RecordingRunner(exit_codes=[1, 0])
    monkeypatch.setattr(execution_module, "run_in_venv", runner)
    res, cwd = execution_module._run_step_subcommands(
        {"command": "git clone X && python build.py"}, PY, base
    )
    # 第一条失败 -> && 短路，第二条不执行。
    assert len(runner.calls) == 1
    assert runner.calls[0][0] == ["git", "clone", "X"]
    assert len(res) == 1 and res[0].exit_code == 1


def test_semicolon_runs_both(monkeypatch):
    base = _ws_dir("semi")
    runner = RecordingRunner(exit_codes=[1, 0])
    monkeypatch.setattr(execution_module, "run_in_venv", runner)
    res, cwd = execution_module._run_step_subcommands(
        {"command": "git clone X ; python build.py"}, PY, base
    )
    # ; 无条件顺序：两条都跑。
    assert len(runner.calls) == 2
    assert runner.calls[1][0] == [PY, "build.py"]


def test_cd_updates_cwd_and_persists(monkeypatch):
    base = _ws_dir("cdpersist")
    repo = Path(base) / "repo"
    repo.mkdir(exist_ok=True)
    runner = RecordingRunner(exit_codes=[0, 0])
    monkeypatch.setattr(execution_module, "run_in_venv", runner)
    # step0: cd repo && python a.py
    res0, cwd0 = execution_module._run_step_subcommands(
        {"command": "cd repo && python a.py"}, PY, base
    )
    assert cwd0 == str(repo.resolve())
    assert runner.calls[0][1] == str(repo.resolve())  # a.py 在 repo 下执行
    # step1: 复用上一步的 cwd（跨 step 持久）
    res1, cwd1 = execution_module._run_step_subcommands(
        {"command": "python b.py"}, PY, cwd0
    )
    assert cwd1 == str(repo.resolve())
    assert runner.calls[1][1] == str(repo.resolve())


def test_cd_escape_rejected_in_step(monkeypatch):
    base = _ws_dir("cdrej")
    runner = RecordingRunner()
    monkeypatch.setattr(execution_module, "run_in_venv", runner)
    res, cwd = execution_module._run_step_subcommands(
        {"command": "cd ../../../../etc && python x.py"}, PY, base
    )
    # cd 越界 -> 记错误 + 短路，python x.py 不执行。
    assert cwd == base  # current_dir 未被改动
    assert len(runner.calls) == 0
    assert any(r.exit_code == -1 and "越界" in r.stderr for r in res)


def test_source_discarded(monkeypatch):
    base = _ws_dir("src")
    runner = RecordingRunner(exit_codes=[0])
    monkeypatch.setattr(execution_module, "run_in_venv", runner)
    res, cwd = execution_module._run_step_subcommands(
        {"command": "source .venv/bin/activate && python run.py"}, PY, base
    )
    # source 被丢弃（不执行子进程），只跑 python run.py。
    assert len(runner.calls) == 1
    assert runner.calls[0][0] == [PY, "run.py"]


def test_pip_rewritten_in_step(monkeypatch):
    base = _ws_dir("pip")
    runner = RecordingRunner(exit_codes=[0])
    monkeypatch.setattr(execution_module, "run_in_venv", runner)
    res, cwd = execution_module._run_step_subcommands(
        {"command": "pip install -r requirements.txt"}, PY, base
    )
    assert runner.calls[0][0] == [PY, "-m", "pip", "install", "-r", "requirements.txt"]


def test_backward_compat_single_atomic(monkeypatch):
    base = _ws_dir("compat")
    runner = RecordingRunner(exit_codes=[0])
    monkeypatch.setattr(execution_module, "run_in_venv", runner)
    res, cwd = execution_module._run_step_subcommands(
        {"command": "python run.py --epochs 1"}, PY, base
    )
    assert len(runner.calls) == 1
    assert runner.calls[0][0] == [PY, "run.py", "--epochs", "1"]
    assert cwd == base


# ---------------------------------------------------------------------------
# 安全回归：注入字符串不触发 shell 解释执行
# ---------------------------------------------------------------------------


def test_injection_semicolon_rm_not_shell_interpreted(monkeypatch):
    """`git clone X ; rm -rf <probe>`：rm 作为独立顺序子命令的字面 argv，
    绝不经 shell 解释（无 glob/通配符 shell 展开）；探针文件不被本解析层删除。"""
    base = _ws_dir("inject1")
    probe = Path(base) / "probe.txt"
    probe.write_text("keep")

    runner = RecordingRunner(exit_codes=[0, 0])
    monkeypatch.setattr(execution_module, "run_in_venv", runner)

    res, cwd = execution_module._run_step_subcommands(
        {"command": f"git clone X ; rm -rf {probe}"}, PY, base
    )
    # 拆为两条独立顺序子命令；rm 作为字面 argv 交给 run_in_venv（被 mock，不真跑）。
    assert runner.calls[0][0] == ["git", "clone", "X"]
    assert runner.calls[1][0] == ["rm", "-rf", str(probe)]
    # 探针未被本解析层删除（mock 不执行 rm；关键是 ; 没被 shell 一次性解释跑掉）。
    assert probe.exists()


def test_injection_command_substitution_literal(monkeypatch):
    """`echo $(rm -rf /)`：$(...) 保持字面 token，不做命令替换。"""
    base = _ws_dir("inject2")
    runner = RecordingRunner(exit_codes=[0])
    monkeypatch.setattr(execution_module, "run_in_venv", runner)
    res, cwd = execution_module._run_step_subcommands(
        {"command": "echo $(rm -rf /)"}, PY, base
    )
    # shlex 把 $(rm -rf /) 拆成多个 token 但都是字面字符串，绝无命令替换语义。
    argv = runner.calls[0][0]
    assert argv[0] == "echo"
    # $( 作为字面字符出现在 token 中，而非被求值。
    assert any("$(" in t or "rm" in t for t in argv)
    assert len(runner.calls) == 1  # 仅一条 echo，无额外 rm 子进程


def test_injection_backtick_literal(monkeypatch):
    base = _ws_dir("inject3")
    runner = RecordingRunner(exit_codes=[0])
    monkeypatch.setattr(execution_module, "run_in_venv", runner)
    res, cwd = execution_module._run_step_subcommands(
        {"command": "echo `whoami`"}, PY, base
    )
    argv = runner.calls[0][0]
    assert argv[0] == "echo"
    assert any("whoami" in t for t in argv)  # 反引号内容为字面


# ---------------------------------------------------------------------------
# planning prompt 收敛：Prompt Cache 字节级幂等（body 仍为常量、含原子命令约束）
# ---------------------------------------------------------------------------


def test_planning_prompt_body_stable_and_constrained():
    planning_module = importlib.import_module("core.nodes.planning")
    body = planning_module._PLANNING_SYSTEM_PROMPT_BODY
    # 静态约束已注入（原子命令 / 不要 source / venv 已准备好）。
    assert "原子命令" in body
    assert "source" in body
    # body 仍为 str 常量（无动态变量），两次取值字节一致。
    assert body == planning_module._build_planning_system_prompt({"arxiv_id": "x"})
    assert body == planning_module._build_planning_system_prompt({"arxiv_id": "y"})
