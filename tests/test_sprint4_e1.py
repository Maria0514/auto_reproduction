"""Sprint 4 任务 E1（S4-04）：sandbox 工具化单测。

覆盖 dev-plan §4 任务 E1 自测检查点：
    - CP-E1-1 两工具返回合法 JSON（sort_keys / ensure_ascii=False / 禁 str(dict)）；
      mock sandbox 下收集器收到的是真实 dataclass 结果而非 agent 文本；
    - CP-E1-2 确定性解析改写在工具内生效（&& / ; 拆分、裸 python/pip 改写、
      cd 越界拒绝、glob 展开——复用 sp3 test_sprint3_shell_parse 语料断言行为等价）；
    - CP-E1-3 脱敏：注入已知 token 后 run_in_sandbox 返回的 stdout/stderr 无明文；
    - CP-E1-4 extra_env 注入链路：两工具透传到 prepare_venv / run_in_venv（spy 断言）；
      无条件含 GIT_TERMINAL_PROMPT=0；
    - CP-E1-5 工具异常（SandboxCreationError / OSError 兜底）转结构化错误 + WARNING，
      子图不被打断。

全部 mock sandbox（不真跑子进程 / 不建真 venv），零 LLM / 零配额。
glob / cd 用 WORKSPACE_DIR 下真实临时目录（沿用 sp3 _ws_dir 范式，
_resolve_cd/_is_within_workspace 绑定 import 期 WORKSPACE_DIR，无法 monkeypatch）。
"""

from __future__ import annotations

import importlib
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytest

execution_module = importlib.import_module("core.nodes.execution")

from config import WORKSPACE_DIR  # noqa: E402
from core import secrets_store  # noqa: E402
from core.errors import SandboxCreationError  # noqa: E402
from sandbox.local_venv import SandboxPrepareResult, SandboxRunResult  # noqa: E402

PY = str(WORKSPACE_DIR / ".venv" / "bin" / "python")


# ---------------------------------------------------------------------------
# fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_process_sensitive_set():
    secrets_store._SENSITIVE_VALUES.clear()
    yield
    secrets_store._SENSITIVE_VALUES.clear()


def _ws_dir(name: str) -> str:
    d = WORKSPACE_DIR / "e1-tool-test" / name
    d.mkdir(parents=True, exist_ok=True)
    return str(d)


def _make_prep_result(success: bool = True, **overrides: Any) -> SandboxPrepareResult:
    base: Dict[str, Any] = dict(
        success=success,
        venv_dir=str(WORKSPACE_DIR / "wd" / ".venv"),
        python_exe=PY,
        pip_exe=str(WORKSPACE_DIR / "wd" / ".venv" / "bin" / "pip"),
        env_info={"python_version": "Python 3.11.0"},
        install_log="[venv create] exit=0",
        install_failed_packages=[],
        error=None,
    )
    base.update(overrides)
    return SandboxPrepareResult(**base)


class RecordingRunner:
    """记录每次 run_in_venv 的 (argv, cwd, kwargs)，按预设序列返回 SandboxRunResult。

    沿用 sp3 test_sprint3_shell_parse.RecordingRunner，扩展 kwargs 捕获（CP-E1-4）
    与可定制 stdout/stderr（CP-E1-3）。
    """

    def __init__(
        self,
        exit_codes: Optional[List[int]] = None,
        stdouts: Optional[List[str]] = None,
        stderrs: Optional[List[str]] = None,
    ) -> None:
        self.calls: List[Tuple[List[str], str, Dict[str, Any]]] = []
        self._codes = list(exit_codes or [])
        self._stdouts = list(stdouts or [])
        self._stderrs = list(stderrs or [])
        self._i = 0

    def __call__(self, python_exe: str, command: List[str], work_dir: str, *a: Any, **k: Any):
        self.calls.append((list(command), work_dir, dict(k)))
        i = self._i
        self._i += 1
        return SandboxRunResult(
            exit_code=self._codes[i] if i < len(self._codes) else 0,
            stdout=self._stdouts[i] if i < len(self._stdouts) else "",
            stderr=self._stderrs[i] if i < len(self._stderrs) else "",
            duration_seconds=0.1,
            timed_out=False,
            output_truncated=False,
            command=list(command),
        )


def _run_tool(work_dir: str, collector=None, extra_env=None, python_exe=PY):
    """构造 run_in_sandbox 工具（python_exe 经 ref 显式提供，绕开 prepare 依赖）。"""
    collector = collector if collector is not None else execution_module._SandboxRunCollector()
    ref: Dict[str, Optional[str]] = {"python_exe": python_exe}
    t = execution_module.make_run_in_sandbox_tool(work_dir, collector, extra_env, ref)
    return t, collector


def _assert_sorted_json(raw: str) -> Dict[str, Any]:
    """断言 raw 是合法 JSON 且 sort_keys 序列化（BUG-S1-02 治理，禁 str(dict)）。"""
    parsed = json.loads(raw)  # 单引号 repr 在此必炸
    assert isinstance(parsed, dict)
    assert raw == json.dumps(parsed, ensure_ascii=False, sort_keys=True, default=str), \
        "工具返回必须 json.dumps(ensure_ascii=False, sort_keys=True) 字节级幂等"
    return parsed


# ===========================================================================
# CP-E1-1：合法 JSON + 收集器收到真实 dataclass
# ===========================================================================


def test_cp_e1_1_prepare_tool_json_and_collector_dataclass(monkeypatch):
    prep = _make_prep_result()
    monkeypatch.setattr(execution_module, "prepare_venv", lambda **kw: prep)
    collector = execution_module._SandboxRunCollector()
    t = execution_module.make_prepare_environment_tool(
        _ws_dir("cp11p"), {"environment": {"dependencies": ["numpy"]}}, collector,
    )

    raw = t.invoke({})
    parsed = _assert_sorted_json(raw)
    assert parsed["success"] is True
    assert parsed["python_exe"] == PY
    assert parsed["install_failed_packages"] == []
    # 收集器收到真实 dataclass 实例（非 agent 文本 / 非 dict）。
    assert collector.prep_results == [prep]
    assert isinstance(collector.prep_results[0], SandboxPrepareResult)


def test_cp_e1_1_run_tool_json_and_collector_dataclass(monkeypatch):
    runner = RecordingRunner(exit_codes=[0], stdouts=["ok <METRICS>{}</METRICS>"])
    monkeypatch.setattr(execution_module, "run_in_venv", runner)
    t, collector = _run_tool(_ws_dir("cp11r"))

    raw = t.invoke({"command": "python train.py --epochs 1"})
    parsed = _assert_sorted_json(raw)
    assert parsed["exit_code"] == 0
    assert parsed["timed_out"] is False
    assert len(parsed["results"]) == 1
    entry = parsed["results"][0]
    assert entry["command"] == [PY, "train.py", "--epochs", "1"]
    assert "<METRICS>" in entry["stdout_tail"]
    # 收集器收到真实 dataclass（含全量 stdout，非截断文本）。
    assert len(collector.run_results) == 1
    assert isinstance(collector.run_results[0], SandboxRunResult)
    assert collector.run_results[0].exit_code == 0


def test_cp_e1_1_prepare_failure_result_still_appended(monkeypatch):
    """prepare_venv 返回业务失败（success=False）是合法结果：进收集器、非 tool_error。"""
    prep = _make_prep_result(
        success=False, install_failed_packages=["torch"], error="部分依赖安装失败: ['torch']",
    )
    monkeypatch.setattr(execution_module, "prepare_venv", lambda **kw: prep)
    collector = execution_module._SandboxRunCollector()
    t = execution_module.make_prepare_environment_tool(_ws_dir("cp11f"), {}, collector)

    parsed = _assert_sorted_json(t.invoke({}))
    assert parsed["success"] is False
    assert parsed["install_failed_packages"] == ["torch"]
    assert "tool_error" not in parsed
    assert collector.prep_results == [prep]


# ===========================================================================
# CP-E1-2：确定性解析改写在工具内生效（复用 sp3 语料，行为等价）
# ===========================================================================


def test_cp_e1_2_amp_shortcircuit_in_tool(monkeypatch):
    runner = RecordingRunner(exit_codes=[1, 0])
    monkeypatch.setattr(execution_module, "run_in_venv", runner)
    t, collector = _run_tool(_ws_dir("amp"))

    parsed = _assert_sorted_json(t.invoke({"command": "git clone X && python build.py"}))
    # 第一条失败 -> && 短路，第二条不执行（sp3 行为等价）。
    assert len(runner.calls) == 1
    assert runner.calls[0][0] == ["git", "clone", "X"]
    assert parsed["exit_code"] == 1
    assert len(collector.run_results) == 1


def test_cp_e1_2_semicolon_runs_both_in_tool(monkeypatch):
    runner = RecordingRunner(exit_codes=[1, 0])
    monkeypatch.setattr(execution_module, "run_in_venv", runner)
    t, collector = _run_tool(_ws_dir("semi"))

    parsed = _assert_sorted_json(t.invoke({"command": "git clone X ; python build.py"}))
    assert len(runner.calls) == 2
    assert runner.calls[1][0] == [PY, "build.py"]
    assert parsed["exit_code"] == 1  # 首个非 0
    assert len(parsed["results"]) == 2


def test_cp_e1_2_bare_pip_rewritten_in_tool(monkeypatch):
    runner = RecordingRunner(exit_codes=[0])
    monkeypatch.setattr(execution_module, "run_in_venv", runner)
    t, _ = _run_tool(_ws_dir("pip"))

    t.invoke({"command": "pip install -r requirements.txt"})
    assert runner.calls[0][0] == [PY, "-m", "pip", "install", "-r", "requirements.txt"]


def test_cp_e1_2_cd_updates_and_persists_across_tool_calls(monkeypatch):
    base = _ws_dir("cdpersist")
    repo = Path(base) / "repo"
    repo.mkdir(exist_ok=True)
    runner = RecordingRunner(exit_codes=[0, 0])
    monkeypatch.setattr(execution_module, "run_in_venv", runner)
    t, _ = _run_tool(base)

    t.invoke({"command": "cd repo && python a.py"})
    assert runner.calls[0][1] == str(repo.resolve())  # a.py 在 repo 下执行
    # 第二次工具调用复用上一次的 current_dir（跨调用持续，模拟连续 shell 会话）。
    t.invoke({"command": "python b.py"})
    assert runner.calls[1][1] == str(repo.resolve())


def test_cp_e1_2_cd_escape_rejected_in_tool(monkeypatch):
    base = _ws_dir("cdrej")
    runner = RecordingRunner()
    monkeypatch.setattr(execution_module, "run_in_venv", runner)
    t, collector = _run_tool(base)

    parsed = _assert_sorted_json(t.invoke({"command": "cd ../../../../etc && python x.py"}))
    # cd 越界 -> 记 exit=-1 错误 + 短路，python x.py 不执行（sp3 行为等价）。
    assert len(runner.calls) == 0
    assert parsed["exit_code"] == -1
    assert any("越界" in e["stderr_tail"] for e in parsed["results"])
    assert len(collector.run_results) == 1 and collector.run_results[0].exit_code == -1


def test_cp_e1_2_glob_expanded_in_tool(monkeypatch):
    d = _ws_dir("glob")
    for fn in ("a.py", "b.py", "c.txt"):
        Path(d, fn).write_text("x")
    runner = RecordingRunner(exit_codes=[0])
    monkeypatch.setattr(execution_module, "run_in_venv", runner)
    t, _ = _run_tool(d)

    t.invoke({"command": "py_compile *.py"})
    argv = runner.calls[0][0]
    assert argv[0] == "py_compile"
    assert sorted(argv[1:]) == ["a.py", "b.py"]


def test_cp_e1_2_source_discarded_in_tool(monkeypatch):
    runner = RecordingRunner(exit_codes=[0])
    monkeypatch.setattr(execution_module, "run_in_venv", runner)
    t, _ = _run_tool(_ws_dir("src"))

    t.invoke({"command": "source .venv/bin/activate && python run.py"})
    assert len(runner.calls) == 1
    assert runner.calls[0][0] == [PY, "run.py"]


def test_cp_e1_2_empty_command_structured_error(monkeypatch):
    runner = RecordingRunner()
    monkeypatch.setattr(execution_module, "run_in_venv", runner)
    t, collector = _run_tool(_ws_dir("empty"))

    parsed = _assert_sorted_json(t.invoke({"command": "   "}))
    assert parsed["tool_error"] is True
    assert parsed["exit_code"] == -1
    assert len(runner.calls) == 0
    assert collector.run_results == []


# ===========================================================================
# python_exe 解析优先级（工具内确定性；含 R-S4-10 resume 场景的 .venv 探测兜底）
# ===========================================================================


def test_python_exe_prefers_collector_prepare_result(monkeypatch):
    """优先级 1：收集器内最近一次成功 prepare 的 python_exe。"""
    prep_py = str(WORKSPACE_DIR / "e1-tool-test" / "prio" / ".venv" / "bin" / "python")
    runner = RecordingRunner(exit_codes=[0])
    monkeypatch.setattr(execution_module, "run_in_venv", runner)
    collector = execution_module._SandboxRunCollector()
    collector.prep_results.append(_make_prep_result(python_exe=prep_py))
    t, _ = _run_tool(_ws_dir("prio"), collector=collector, python_exe=PY)

    t.invoke({"command": "python x.py"})
    assert runner.calls[0][0] == [prep_py, "x.py"], "应优先用收集器 prepare 的 python_exe"


def test_python_exe_fallback_to_existing_venv_after_resume(monkeypatch):
    """优先级 3（R-S4-10）：resume 后收集器重建为空、ref 空，但 work_dir/.venv
    已在 pre-interrupt 建好 → pyvenv.cfg 探测确定性推导。"""
    base = _ws_dir("resume-venv")
    venv = Path(base) / ".venv"
    (venv / "bin").mkdir(parents=True, exist_ok=True)
    (venv / "pyvenv.cfg").write_text("home = /usr\n")
    runner = RecordingRunner(exit_codes=[0])
    monkeypatch.setattr(execution_module, "run_in_venv", runner)
    collector = execution_module._SandboxRunCollector()
    t = execution_module.make_run_in_sandbox_tool(base, collector, None, {"python_exe": None})

    parsed = _assert_sorted_json(t.invoke({"command": "python x.py"}))
    assert parsed["exit_code"] == 0
    assert runner.calls[0][0][0] == str(venv / "bin" / "python")


def test_python_exe_missing_returns_structured_error(monkeypatch, caplog):
    """优先级 4：无 prepare / 无 ref / 无 .venv → 结构化错误提示先 prepare，不炸。"""
    runner = RecordingRunner()
    monkeypatch.setattr(execution_module, "run_in_venv", runner)
    collector = execution_module._SandboxRunCollector()
    t = execution_module.make_run_in_sandbox_tool(_ws_dir("noprep"), collector, None, None)

    with caplog.at_level(logging.WARNING):
        parsed = _assert_sorted_json(t.invoke({"command": "python x.py"}))
    assert parsed["tool_error"] is True
    assert "prepare_environment" in parsed["error"]
    assert len(runner.calls) == 0
    assert any("尚未准备" in r.message for r in caplog.records)


# ===========================================================================
# CP-E1-3：脱敏 —— 注入已知 token 后返回的 stdout/stderr 无明文
# ===========================================================================


def test_cp_e1_3_run_tool_masks_sensitive_values(monkeypatch):
    token = "hf_SECRETTOKEN123456"
    secrets_store.register_sensitive_value(token)
    runner = RecordingRunner(
        exit_codes=[1],
        stdouts=[f"downloading with token={token} ..."],
        stderrs=[f"401 unauthorized: bad token {token}"],
    )
    monkeypatch.setattr(execution_module, "run_in_venv", runner)
    t, collector = _run_tool(_ws_dir("mask"))

    raw = t.invoke({"command": "python fetch.py"})
    assert token not in raw, "工具返回 JSON 内绝不能出现敏感值明文"
    parsed = json.loads(raw)
    assert "****" in parsed["results"][0]["stdout_tail"]
    assert "****" in parsed["results"][0]["stderr_tail"]
    # 收集器保留全量原文（供编排层收尾；logs 脱敏是 E3 _aggregate_logs 的落点）。
    assert token in collector.run_results[0].stdout


def test_cp_e1_3_prepare_tool_masks_error(monkeypatch):
    token = "ghp_PREPARESECRET789"
    secrets_store.register_sensitive_value(token)
    prep = _make_prep_result(success=False, error=f"pip 认证失败 token={token}")
    monkeypatch.setattr(execution_module, "prepare_venv", lambda **kw: prep)
    collector = execution_module._SandboxRunCollector()
    t = execution_module.make_prepare_environment_tool(_ws_dir("maskp"), {}, collector)

    raw = t.invoke({})
    assert token not in raw
    assert "****" in json.loads(raw)["error"]


# ===========================================================================
# CP-E1-4：extra_env 注入链路（spy 断言）+ 无条件 GIT_TERMINAL_PROMPT=0
# ===========================================================================


def test_cp_e1_4_prepare_tool_passes_extra_env(monkeypatch):
    seen: Dict[str, Any] = {}

    def spy_prepare(**kw):
        seen.update(kw)
        return _make_prep_result()

    monkeypatch.setattr(execution_module, "prepare_venv", spy_prepare)
    collector = execution_module._SandboxRunCollector()
    t = execution_module.make_prepare_environment_tool(
        _ws_dir("env1"), {"environment": {"dependencies": ["numpy"]}}, collector,
        extra_env={"HF_TOKEN": "x", "GIT_ASKPASS": "/w/.git_askpass_gh.sh"},
    )
    t.invoke({})

    assert seen["extra_env"]["HF_TOKEN"] == "x"
    assert seen["extra_env"]["GIT_ASKPASS"] == "/w/.git_askpass_gh.sh"
    assert seen["extra_env"]["GIT_TERMINAL_PROMPT"] == "0"
    assert seen["requirements"] == ["numpy"]  # _extract_requirements(plan) 链路


def test_cp_e1_4_prepare_tool_git_terminal_prompt_unconditional(monkeypatch):
    """extra_env=None 时也必须含 GIT_TERMINAL_PROMPT=0（无条件，R-S4-08）。"""
    seen: Dict[str, Any] = {}

    def spy_prepare(**kw):
        seen.update(kw)
        return _make_prep_result()

    monkeypatch.setattr(execution_module, "prepare_venv", spy_prepare)
    t = execution_module.make_prepare_environment_tool(
        _ws_dir("env2"), {}, execution_module._SandboxRunCollector(), extra_env=None,
    )
    t.invoke({})
    assert seen["extra_env"] == {"GIT_TERMINAL_PROMPT": "0"}


def test_cp_e1_4_run_tool_passes_extra_env(monkeypatch):
    runner = RecordingRunner(exit_codes=[0, 0])
    monkeypatch.setattr(execution_module, "run_in_venv", runner)
    t, _ = _run_tool(_ws_dir("env3"), extra_env={"HF_TOKEN": "y"})

    t.invoke({"command": "python a.py && python b.py"})
    for _, _, kwargs in runner.calls:  # 每条子命令都透传（含复合拆分后的多条）
        assert kwargs["extra_env"]["HF_TOKEN"] == "y"
        assert kwargs["extra_env"]["GIT_TERMINAL_PROMPT"] == "0"


def test_cp_e1_4_run_tool_git_terminal_prompt_unconditional(monkeypatch):
    runner = RecordingRunner(exit_codes=[0])
    monkeypatch.setattr(execution_module, "run_in_venv", runner)
    t, _ = _run_tool(_ws_dir("env4"), extra_env=None)

    t.invoke({"command": "git clone https://example.com/repo"})
    assert runner.calls[0][2]["extra_env"] == {"GIT_TERMINAL_PROMPT": "0"}


# ===========================================================================
# CP-E1-5：工具异常转结构化错误 + WARNING，子图不被打断
# ===========================================================================


def test_cp_e1_5_prepare_sandbox_creation_error(monkeypatch, caplog):
    def boom(**kw):
        raise SandboxCreationError("work_dir 越界", "detail")

    monkeypatch.setattr(execution_module, "prepare_venv", boom)
    collector = execution_module._SandboxRunCollector()
    t = execution_module.make_prepare_environment_tool(_ws_dir("ex1"), {}, collector)

    with caplog.at_level(logging.WARNING):
        raw = t.invoke({})  # 绝不抛异常
    parsed = _assert_sorted_json(raw)
    assert parsed["tool_error"] is True
    assert "SandboxCreationError" in parsed["error"]
    assert collector.prep_results == []  # 异常路径不进收集器
    assert any("prepare_venv 失败" in r.message for r in caplog.records)


def test_cp_e1_5_prepare_oserror_fallback(monkeypatch, caplog):
    def boom(**kw):
        raise OSError("disk full")

    monkeypatch.setattr(execution_module, "prepare_venv", boom)
    t = execution_module.make_prepare_environment_tool(
        _ws_dir("ex2"), {}, execution_module._SandboxRunCollector(),
    )
    with caplog.at_level(logging.WARNING):
        parsed = _assert_sorted_json(t.invoke({}))
    assert parsed["tool_error"] is True
    assert "OSError" in parsed["error"]
    assert any("工具异常" in r.message for r in caplog.records)


def test_cp_e1_5_run_oserror_fallback(monkeypatch, caplog):
    def boom(*a, **kw):
        raise OSError("no such device")

    monkeypatch.setattr(execution_module, "run_in_venv", boom)
    t, collector = _run_tool(_ws_dir("ex3"))

    with caplog.at_level(logging.WARNING):
        parsed = _assert_sorted_json(t.invoke({"command": "python x.py"}))
    assert parsed["tool_error"] is True
    assert parsed["exit_code"] == -1
    assert "OSError" in parsed["error"]
    assert any("工具异常" in r.message for r in caplog.records)


def test_cp_e1_5_error_json_masks_sensitive(monkeypatch):
    """异常消息可能内嵌凭证（如 URL），错误 JSON 也必须过 mask。"""
    token = "glpat-ERRSECRET42"
    secrets_store.register_sensitive_value(token)

    def boom(**kw):
        raise OSError(f"connect https://user:{token}@host failed")

    monkeypatch.setattr(execution_module, "prepare_venv", boom)
    t = execution_module.make_prepare_environment_tool(
        _ws_dir("ex4"), {}, execution_module._SandboxRunCollector(),
    )
    raw = t.invoke({})
    assert token not in raw
    assert "****" in json.loads(raw)["error"]
