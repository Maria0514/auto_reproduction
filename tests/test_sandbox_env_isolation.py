"""沙箱环境变量隔离测试（凭证泄漏止血修复）。

背景：sandbox/local_venv.py::_run_subprocess 原先 `{**os.environ, **extra_env}`
全量继承父进程环境，导致 .env 装载的 LLM_API_KEY / DEEPXIV_TOKEN 等凭证暴露给
沙箱内 LLM 生成的不可信代码。修复为 `_build_sandbox_env` 白名单继承。

覆盖：
    1. _build_sandbox_env 纯函数：凭证剔除 / 白名单保留 / extra_env 显式覆盖；
    2. 真实子进程实测：monkeypatch 哨兵变量后沙箱内 `print(sorted(os.environ))`
       看不到哨兵（含真实凭证名 LLM_API_KEY / DEEPXIV_TOKEN），白名单关键变量仍在，
       extra_env 显式注入仍可见（sp4 凭证注入口不被白名单挡住）；
    3. prepare_venv（venv 创建 + pip）路径：spy Popen 断言所有子进程 env 均收口；
    4. 既有护栏不回归：超时杀树 / 输出截断 / 路径越界在白名单环境下仍生效
       （全量护栏回归由 tests/test_sprint3_b1.py 承担，此处仅轻量兜底）。

注意：真实子进程测试在 tmp_path 受控 WORKSPACE_DIR 下跑，不污染真实 workspace；
不依赖真实网络 / 不装任何 pip 包。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest import mock

import pytest

from core.errors import SandboxCreationError
from sandbox import local_venv
from sandbox.local_venv import (
    _build_sandbox_env,
    prepare_venv,
    run_in_venv,
)

# 哨兵变量名（模拟凭证）：确保不撞真实环境已有变量。
_SENTINEL_KEY = "FAKE_SECRET_FOR_TEST"
_SENTINEL_VALUE = "top-secret-sentinel-do-not-leak"

# 真实凭证名（.env 装载进 os.environ 的键）：沙箱内绝不能出现。
_REAL_CREDENTIAL_KEYS = ("LLM_API_KEY", "DEEPXIV_TOKEN")


# ---------------------------------------------------------------------------
# fixtures（沿用 test_sprint3_b1.py 的受控 workspace 范式）
# ---------------------------------------------------------------------------

@pytest.fixture()
def sandbox_workspace(tmp_path, monkeypatch):
    """把 WORKSPACE_DIR patch 到 tmp_path 下的受控目录，避免污染真实 workspace。"""
    ws = tmp_path / "workspace"
    work = ws / "thread-env" / "code"
    work.mkdir(parents=True)
    monkeypatch.setattr(local_venv, "WORKSPACE_DIR", ws)
    return ws, work


@pytest.fixture()
def venv_python(sandbox_workspace):
    """在受控 workspace 下创建真实 venv（不装包），返回 (ws, work, python_exe)。"""
    ws, work = sandbox_workspace
    result = prepare_venv(str(work))
    assert result.success is True, f"venv 创建应成功: {result.error}"
    assert Path(result.python_exe).exists()
    return ws, work, result.python_exe


# ===========================================================================
# 1. _build_sandbox_env 纯函数测试
# ===========================================================================

def test_build_sandbox_env_drops_credentials(monkeypatch):
    """凭证类变量（哨兵 + 真实凭证名 + 常见云凭证）一律剔除，不进沙箱环境。"""
    monkeypatch.setenv(_SENTINEL_KEY, _SENTINEL_VALUE)
    monkeypatch.setenv("LLM_API_KEY", "fake-llm-key")
    monkeypatch.setenv("DEEPXIV_TOKEN", "fake-deepxiv-token")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "fake-aws-secret")

    env = _build_sandbox_env()

    assert _SENTINEL_KEY not in env
    assert "LLM_API_KEY" not in env
    assert "DEEPXIV_TOKEN" not in env
    assert "AWS_SECRET_ACCESS_KEY" not in env
    # 值也绝不能以任何形式出现。
    assert _SENTINEL_VALUE not in json.dumps(env)


def test_build_sandbox_env_keeps_allowlist(monkeypatch):
    """白名单变量保留：PATH/HOME/LANG/LC_*/TMPDIR/PYTHONUNBUFFERED/代理/PIP_*。"""
    monkeypatch.setenv("LANG", "en_US.UTF-8")
    monkeypatch.setenv("LC_ALL", "en_US.UTF-8")
    monkeypatch.setenv("TMPDIR", "/tmp")
    monkeypatch.setenv("PYTHONUNBUFFERED", "1")
    monkeypatch.setenv("https_proxy", "http://proxy.example:3128")
    monkeypatch.setenv("no_proxy", "localhost")
    monkeypatch.setenv("PIP_INDEX_URL", "https://pypi.example/simple")
    monkeypatch.setenv("PIP_CACHE_DIR", "/tmp/pipcache")

    env = _build_sandbox_env()

    # PATH/HOME 父进程必有，直接断言透传且值一致。
    assert env.get("PATH") == os.environ["PATH"]
    assert env.get("HOME") == os.environ.get("HOME")
    assert env.get("LANG") == "en_US.UTF-8"
    assert env.get("LC_ALL") == "en_US.UTF-8"  # LC_ 前缀
    assert env.get("TMPDIR") == "/tmp"
    assert env.get("PYTHONUNBUFFERED") == "1"
    # 网络代理类保留（非凭证，pip 装依赖需要）。
    assert env.get("https_proxy") == "http://proxy.example:3128"
    assert env.get("no_proxy") == "localhost"
    # pip 源类保留（PIP_ 前缀）。
    assert env.get("PIP_INDEX_URL") == "https://pypi.example/simple"
    assert env.get("PIP_CACHE_DIR") == "/tmp/pipcache"


def test_build_sandbox_env_extra_env_overrides(monkeypatch):
    """extra_env 显式注入且覆盖白名单同名项（sp4 凭证注入唯一入口不被挡）。"""
    monkeypatch.setenv("LANG", "C")
    env = _build_sandbox_env({"INJECTED_VAR": "yes", "LANG": "zh_CN.UTF-8"})
    assert env.get("INJECTED_VAR") == "yes"  # 白名单外显式注入生效
    assert env.get("LANG") == "zh_CN.UTF-8"  # 覆盖白名单同名项


def test_build_sandbox_env_no_pythonpath_inheritance(monkeypatch):
    """PYTHONPATH 不继承（父进程模块路径漏进沙箱会破坏隔离）。"""
    monkeypatch.setenv("PYTHONPATH", "/data/myproj/should_not_leak")
    env = _build_sandbox_env()
    assert "PYTHONPATH" not in env


# ---------------------------------------------------------------------------
# 1b. 白名单继承值级凭证形态否决（sp4 D2 / HOTFIX-2 备忘闭环，架构师定案 (a')-修正版）
#     既有用例零修改（语义不弱化）；本节为增量护栏。
# ---------------------------------------------------------------------------

def test_build_sandbox_env_drops_pip_credential_userinfo(monkeypatch, caplog):
    """PIP_INDEX_URL 值含 user:token@（URL userinfo 凭证形态）→ 整变量剔除 +
    WARNING（只打变量名，绝不打 token 值）。"""
    import logging as _logging
    token = "d2-pip-embedded-token-do-not-leak"
    monkeypatch.setenv("PIP_INDEX_URL", f"https://user:{token}@pypi.private.example/simple")
    with caplog.at_level(_logging.WARNING, logger="sandbox.local_venv"):
        env = _build_sandbox_env()
    assert "PIP_INDEX_URL" not in env
    assert token not in json.dumps(env)
    warnings = [r for r in caplog.records if r.levelno == _logging.WARNING]
    assert any("PIP_INDEX_URL" in r.getMessage() for r in warnings), "剔除必须 WARNING 非静默"
    for record in caplog.records:
        assert token not in record.getMessage(), "WARNING 不得泄漏 token 值"


def test_build_sandbox_env_drops_pip_extra_index_url_multi_url(monkeypatch):
    """PIP_EXTRA_INDEX_URL 多 URL 空格串、其中一段含 userinfo → 整变量剔除。"""
    monkeypatch.setenv(
        "PIP_EXTRA_INDEX_URL",
        "https://pypi.org/simple https://user:tok@private.example/simple",
    )
    env = _build_sandbox_env()
    assert "PIP_EXTRA_INDEX_URL" not in env


def test_build_sandbox_env_drops_token_only_userinfo(monkeypatch):
    """token-only userinfo（https://<PAT>@host/，无 user:pass 冒号）同样剔除
    （护住正则放宽：`://[^/\\s@]+@` 同时覆盖 user:pass 与 token-only 两形态）。"""
    monkeypatch.setenv("PIP_INDEX_URL", "https://sometoken@pypi.private.example/simple")
    env = _build_sandbox_env()
    assert "PIP_INDEX_URL" not in env


def test_build_sandbox_env_credential_filter_applies_allowlist_wide(monkeypatch):
    """值级否决对全部白名单继承统一生效（不特判 PIP_ 前缀）：认证代理
    https_proxy=http://u:p@proxy:3128 剔除；同时既有无 userinfo proxy / 非凭证
    PIP_* 语义不弱化（继续透传）。"""
    monkeypatch.setenv("https_proxy", "http://user:pass@proxy.example:3128")
    monkeypatch.setenv("http_proxy", "http://proxy.example:3128")  # 无 userinfo，保留
    monkeypatch.setenv("PIP_INDEX_URL", "https://pypi.example/simple")  # 非凭证，保留
    monkeypatch.setenv("PIP_CACHE_DIR", "/tmp/pipcache")  # 非 URL，保留
    env = _build_sandbox_env()
    assert "https_proxy" not in env, "认证代理（userinfo 形态）必须剔除"
    assert env.get("http_proxy") == "http://proxy.example:3128"
    assert env.get("PIP_INDEX_URL") == "https://pypi.example/simple"
    assert env.get("PIP_CACHE_DIR") == "/tmp/pipcache"


# ===========================================================================
# 2. 真实子进程实测：沙箱内看不到哨兵，白名单关键变量仍在
# ===========================================================================

def test_sandbox_subprocess_cannot_see_sentinel(venv_python, monkeypatch):
    """核心验收：沙箱内 print(sorted(os.environ)) 看不到哨兵/凭证，白名单变量在。"""
    ws, work, py = venv_python
    monkeypatch.setenv(_SENTINEL_KEY, _SENTINEL_VALUE)

    dump_code = "import os; print(sorted(os.environ))"
    result = run_in_venv(py, [py, "-c", dump_code], str(work), timeout=60)

    assert result.exit_code == 0, f"env dump 应成功: {result.stderr}"
    # 哨兵键名与值都不可见。
    assert _SENTINEL_KEY not in result.stdout
    assert _SENTINEL_VALUE not in result.stdout
    # 真实凭证名（conftest load_dotenv 装载进父进程）也不可见。
    for cred in _REAL_CREDENTIAL_KEYS:
        assert cred not in result.stdout, f"凭证 {cred} 泄漏进沙箱环境"
    # 白名单关键变量仍在（PATH 必有；HOME 父进程有则透传）。
    assert "'PATH'" in result.stdout
    if os.environ.get("HOME"):
        assert "'HOME'" in result.stdout


def test_sandbox_subprocess_extra_env_still_injected(venv_python):
    """extra_env 显式注入在真实子进程中可见（白名单不挡显式注入口）。"""
    ws, work, py = venv_python
    dump_code = "import os; print(os.environ.get('SANDBOX_EXTRA_FOR_TEST', 'MISSING'))"
    result = run_in_venv(
        py, [py, "-c", dump_code], str(work), timeout=60,
        extra_env={"SANDBOX_EXTRA_FOR_TEST": "injected-ok"},
    )
    assert result.exit_code == 0
    assert "injected-ok" in result.stdout


# ===========================================================================
# 3. prepare_venv（venv 创建 + pip）路径同样收口
# ===========================================================================

def test_prepare_venv_all_subprocess_env_isolated(sandbox_workspace, monkeypatch):
    """spy Popen：prepare_venv 全路径（venv 创建/pip freeze 等）子进程 env 均收口。"""
    ws, work = sandbox_workspace
    monkeypatch.setenv(_SENTINEL_KEY, _SENTINEL_VALUE)

    recorded_envs = []
    real_popen = local_venv.subprocess.Popen

    def _spy_popen(cmd, *args, **kwargs):
        recorded_envs.append(kwargs.get("env"))
        return real_popen(cmd, *args, **kwargs)

    with mock.patch.object(local_venv.subprocess, "Popen", side_effect=_spy_popen):
        result = prepare_venv(str(work))

    assert result.success is True, f"venv 创建应成功: {result.error}"
    assert recorded_envs, "prepare_venv 路径应有 Popen 调用"
    for env in recorded_envs:
        assert env is not None, "必须显式传 env=白名单环境（env=None 即全量继承）"
        assert _SENTINEL_KEY not in env
        for cred in _REAL_CREDENTIAL_KEYS:
            assert cred not in env
        assert "PATH" in env  # 白名单核心变量仍在，pip 可正常工作


# ===========================================================================
# 4. 既有护栏不回归（轻量兜底；全量回归由 test_sprint3_b1.py 承担）
# ===========================================================================

def test_guardrails_not_broken_by_allowlist_env(venv_python):
    """白名单环境下护栏 1（超时杀树）与护栏 2（输出截断）仍生效（同一 venv 两次 run）。"""
    ws, work, py = venv_python

    # 护栏 1：超时杀树。
    result_timeout = run_in_venv(
        py, [py, "-c", "import time; time.sleep(30)"], str(work), timeout=2,
    )
    assert result_timeout.timed_out is True
    assert result_timeout.exit_code != 0

    # 护栏 2：输出截断保留尾部。
    trunc_code = (
        "import sys; sys.stdout.write('A' * 5000); "
        "sys.stdout.write('TAIL_MARKER_END'); sys.stdout.flush()"
    )
    result_trunc = run_in_venv(
        py, [py, "-c", trunc_code], str(work), timeout=60, output_max_bytes=1000,
    )
    assert result_trunc.exit_code == 0
    assert result_trunc.output_truncated is True
    assert "TAIL_MARKER_END" in result_trunc.stdout


def test_guardrail_out_of_workspace_still_rejected(sandbox_workspace):
    """护栏 3（越界拒绝）不受环境白名单改动影响：越界仍抛且 0 次 subprocess。"""
    ws, work = sandbox_workspace
    with mock.patch.object(local_venv.subprocess, "Popen") as m_popen:
        with pytest.raises(SandboxCreationError):
            run_in_venv("/usr/bin/python", ["echo", "x"], "/etc")
    m_popen.assert_not_called()
