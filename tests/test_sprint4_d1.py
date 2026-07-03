"""Sprint 4 任务 D1 自测：prepare_venv 补 extra_env 透传（S4-06）。

覆盖 dev-plan §D1 CP-D1-1 ~ CP-D1-3（architecture §9.3，HOTFIX-2 白名单现状）：
    - CP-D1-1 签名向后兼容：extra_env 默认 None，不传时行为与 sp3 逐字一致
      （全量回归由 tests/test_sprint3_b1.py 38 条 + tests/test_sandbox_env_isolation.py
      9 条承担，本文件补"默认 None 透传"的最小锚定断言）；
    - CP-D1-2 透传断言：spy `_run_subprocess`，venv 创建 + 每次 pip install
      （含瞬态重试第 2 次）全路径均收到 extra_env；`_build_sandbox_env` 合并语义
      （白名单之上显式覆盖）不被绕过；
    - CP-D1-3 注入值不落 install_log 明文：
        a) prepare_venv 自身绝不把 extra_env 的值写进 install_log（注入本身不泄漏）；
        b) characterization：mock pip 输出回显 token 时，install_log 当前**明文含**
           token —— 实测结论：prepare_venv 层无 mask 兜底（D1 最小 diff 不加 mask），
           统一 mask 落点为 A3 secrets_store.mask_value + E3 execution 收尾消费
           （architecture §9.4；本测试落笔时 core/secrets_store.py 尚未落地）。

测试策略（沿用 test_sprint3_b1.py 范式）：
    - WORKSPACE_DIR patch 到 tmp_path 受控目录，不污染真实 workspace；
    - `_run_subprocess` 用 fake 替身记录 (cmd, extra_env)，venv create 命令时落
      pyvenv.cfg + bin/python 假文件（满足 prepare_venv 的存在性检查），不跑真实
      子进程 / 不装任何 pip 包 / 不依赖网络；
    - 合并语义测试 mock 到 subprocess.Popen 层（fake proc），验证 prepare_venv →
      _run_subprocess → _build_sandbox_env 全链路不被绕过。

设计边界说明：dev-plan D1 枚举的透传调用点为「venv 创建 + 每次 pip install（含
重试路径）」；`_collect_env_info` 内 `python --version` / `pip freeze` 是 best-effort
本地查询，无需凭证，按最小 diff 保持 extra_env=None（本文件显式锚定该边界）。
"""

from __future__ import annotations

import inspect
import os
from pathlib import Path
from typing import Dict, List, Optional
from unittest import mock

import pytest

from sandbox import local_venv
from sandbox.local_venv import (
    SandboxRunResult,
    _build_sandbox_env,
    prepare_venv,
)

# 模拟私有源凭证（token）：断言明文泄漏路径用。
_FAKE_TOKEN = "d1-fake-private-index-token-do-not-leak"
_EXTRA_ENV = {
    "PIP_INDEX_URL": f"https://user:{_FAKE_TOKEN}@pypi.private.example/simple",
    "GIT_TERMINAL_PROMPT": "0",
}

# 哨兵凭证（父进程环境中模拟 .env 装载的凭证，绝不能进沙箱 env）。
_SENTINEL_KEY = "D1_FAKE_CREDENTIAL_FOR_TEST"
_SENTINEL_VALUE = "d1-sentinel-credential-value"


# ---------------------------------------------------------------------------
# fixtures（沿用 test_sprint3_b1.py 的受控 workspace 范式）
# ---------------------------------------------------------------------------

@pytest.fixture()
def sandbox_workspace(tmp_path, monkeypatch):
    """把 WORKSPACE_DIR patch 到 tmp_path 下的受控目录，避免污染真实 workspace。"""
    ws = tmp_path / "workspace"
    work = ws / "thread-d1" / "code"
    work.mkdir(parents=True)
    monkeypatch.setattr(local_venv, "WORKSPACE_DIR", ws)
    return ws, work


def _ok(cmd: List[str], stdout: str = "", stderr: str = "") -> SandboxRunResult:
    return SandboxRunResult(
        exit_code=0, stdout=stdout, stderr=stderr,
        duration_seconds=0.01, timed_out=False, output_truncated=False,
        command=list(cmd),
    )


def _fail(cmd: List[str], stderr: str) -> SandboxRunResult:
    return SandboxRunResult(
        exit_code=1, stdout="", stderr=stderr,
        duration_seconds=0.01, timed_out=False, output_truncated=False,
        command=list(cmd),
    )


def _make_fake_run_subprocess(
    recorder: List,
    *,
    pip_stdout: str = "",
    transient_first_for: Optional[str] = None,
):
    """构造 `_run_subprocess` 替身：记录 (cmd, extra_env)，不跑真实子进程。

    - venv create 命令（... -m venv <dir>）：落 pyvenv.cfg + bin/python 假文件后返回成功；
    - pip install 命令：默认成功（stdout=pip_stdout）；若 install 目标 ==
      transient_first_for，则该包第 1 次 attempt 返回网络瞬态失败（触发重试），
      第 2 次起成功；
    - 其余命令（_collect_env_info 的 python --version / pip freeze）：返回成功空输出。
    """
    attempt_count: Dict[str, int] = {}

    def fake(cmd, *, cwd, timeout, output_max_bytes, extra_env=None):
        recorder.append((list(cmd), extra_env))
        if "-m" in cmd and "venv" in cmd:
            venv_dir = Path(cmd[-1])
            (venv_dir / "bin").mkdir(parents=True, exist_ok=True)
            (venv_dir / "pyvenv.cfg").write_text("home = /usr\n", encoding="utf-8")
            (venv_dir / "bin" / "python").write_text("#!/bin/sh\n", encoding="utf-8")
            return _ok(cmd)
        if "install" in cmd:
            target = cmd[-1]
            if transient_first_for is not None and target == transient_first_for:
                attempt_count[target] = attempt_count.get(target, 0) + 1
                if attempt_count[target] == 1:
                    return _fail(cmd, stderr="connection timed out")  # 瞬态关键字
            return _ok(cmd, stdout=pip_stdout)
        return _ok(cmd)

    return fake


def _calls_of(recorder: List, kind: str) -> List:
    """按命令内容过滤记录：kind in {'venv_create', 'pip_install', 'other'}。"""
    picked = []
    for cmd, extra_env in recorder:
        if "-m" in cmd and "venv" in cmd:
            k = "venv_create"
        elif "install" in cmd:
            k = "pip_install"
        else:
            k = "other"
        if k == kind:
            picked.append((cmd, extra_env))
    return picked


# ===========================================================================
# CP-D1-1 签名向后兼容
# ===========================================================================

def test_cp_d1_1_signature_has_optional_extra_env_default_none():
    """prepare_venv 签名末尾追加 extra_env，默认 None（向后兼容，旧调用零改动）。"""
    sig = inspect.signature(prepare_venv)
    params = list(sig.parameters)
    assert "extra_env" in params
    assert sig.parameters["extra_env"].default is None
    # 追加在签名末尾，不改变既有位置参数的顺序（sp3 位置调用不受影响）。
    assert params[-1] == "extra_env"
    assert params[:-1] == [
        "work_dir", "requirements", "requirements_files",
        "reuse_existing", "venv_timeout", "pip_timeout",
    ]


def test_cp_d1_1_not_passing_extra_env_propagates_none(sandbox_workspace):
    """不传 extra_env 时，所有 _run_subprocess 调用收到 extra_env=None——
    _build_sandbox_env(None) 分支与 sp3 逐字一致（零行为变化）。"""
    ws, work = sandbox_workspace
    recorder: List = []
    fake = _make_fake_run_subprocess(recorder)

    with mock.patch.object(local_venv, "_run_subprocess", side_effect=fake):
        result = prepare_venv(str(work), requirements=["numpy"])

    assert result.success is True
    assert recorder, "应有 _run_subprocess 调用"
    for cmd, extra_env in recorder:
        assert extra_env is None, f"不传 extra_env 时必须透传 None: cmd={cmd}"


# ===========================================================================
# CP-D1-2 透传断言：全调用点 + 合并语义不被绕过
# ===========================================================================

def test_cp_d1_2_venv_create_and_pip_install_receive_extra_env(sandbox_workspace):
    """venv 创建 + requirements 逐包 + requirements_files（-r）全路径收到 extra_env。"""
    ws, work = sandbox_workspace
    req_file = work / "requirements.txt"
    req_file.write_text("numpy\n", encoding="utf-8")
    recorder: List = []
    fake = _make_fake_run_subprocess(recorder)

    with mock.patch.object(local_venv, "_run_subprocess", side_effect=fake):
        result = prepare_venv(
            str(work),
            requirements=["torch", "pandas"],
            requirements_files=[str(req_file)],
            extra_env=_EXTRA_ENV,
        )

    assert result.success is True
    venv_calls = _calls_of(recorder, "venv_create")
    pip_calls = _calls_of(recorder, "pip_install")
    assert len(venv_calls) == 1, "应有且仅有一次 venv 创建"
    # 1 个 -r 文件 + 2 个逐包安装 = 3 条 pip install。
    assert len(pip_calls) == 3, f"pip install 调用数不符: {[c for c, _ in pip_calls]}"
    for cmd, extra_env in venv_calls + pip_calls:
        assert extra_env == _EXTRA_ENV, f"调用点未透传 extra_env: cmd={cmd}"


def test_cp_d1_2_pip_transient_retry_second_attempt_receives_extra_env(sandbox_workspace):
    """瞬态重试路径：同一包第 1 次瞬态失败、第 2 次成功，两次 attempt 均收到 extra_env。"""
    ws, work = sandbox_workspace
    recorder: List = []
    fake = _make_fake_run_subprocess(recorder, transient_first_for="flaky-pkg")

    with mock.patch.object(local_venv.time, "sleep"), \
         mock.patch.object(local_venv, "_run_subprocess", side_effect=fake):
        result = prepare_venv(str(work), requirements=["flaky-pkg"], extra_env=_EXTRA_ENV)

    assert result.success is True, f"重试后应成功: {result.error}"
    flaky_calls = [
        (cmd, ee) for cmd, ee in _calls_of(recorder, "pip_install")
        if cmd[-1] == "flaky-pkg"
    ]
    assert len(flaky_calls) == 2, "瞬态失败应重试：同一包应有 2 次 pip install attempt"
    for cmd, extra_env in flaky_calls:
        assert extra_env == _EXTRA_ENV, "重试路径每次 attempt 都必须收到 extra_env"


def test_cp_d1_2_collect_env_info_boundary_stays_none(sandbox_workspace):
    """设计边界锚定：_collect_env_info（python --version / pip freeze 本地查询）
    不在 D1 透传枚举内，保持 extra_env=None（最小 diff，无凭证需求）。"""
    ws, work = sandbox_workspace
    recorder: List = []
    fake = _make_fake_run_subprocess(recorder)

    with mock.patch.object(local_venv, "_run_subprocess", side_effect=fake):
        prepare_venv(str(work), requirements=["numpy"], extra_env=_EXTRA_ENV)

    other_calls = _calls_of(recorder, "other")
    assert other_calls, "_collect_env_info 应产生 python --version / pip freeze 调用"
    for cmd, extra_env in other_calls:
        assert extra_env is None, f"_collect_env_info 调用不应透传 extra_env: cmd={cmd}"


def test_cp_d1_2_build_sandbox_env_merge_not_bypassed(sandbox_workspace, monkeypatch):
    """合并语义端到端：mock 到 Popen 层，prepare_venv 的 pip 子进程 env 必须是
    _build_sandbox_env 白名单继承 + extra_env 显式覆盖的结果——凭证哨兵剔除、
    PATH 保留、注入变量可见、白名单同名项被 extra_env 覆盖，不被任何调用点绕过。"""
    ws, work = sandbox_workspace
    monkeypatch.setenv(_SENTINEL_KEY, _SENTINEL_VALUE)
    monkeypatch.setenv("LANG", "C")  # 白名单同名项，将被 extra_env 覆盖

    # 复用路径：预置 venv 结构，跳过真实 venv 创建（Popen 全程 mock，不跑子进程）。
    venv_dir = work / ".venv"
    (venv_dir / "bin").mkdir(parents=True)
    (venv_dir / "pyvenv.cfg").write_text("home = /usr\n", encoding="utf-8")
    (venv_dir / "bin" / "python").write_text("#!/bin/sh\n", encoding="utf-8")

    extra_env = {**_EXTRA_ENV, "LANG": "zh_CN.UTF-8"}
    recorded: List = []

    class _FakeProc:
        pid = 99999
        returncode = 0

        def communicate(self, timeout=None):
            return b"", b""

    def _spy_popen(cmd, *args, **kwargs):
        recorded.append((list(cmd), kwargs.get("env")))
        return _FakeProc()

    with mock.patch.object(local_venv.subprocess, "Popen", side_effect=_spy_popen):
        result = prepare_venv(
            str(work), requirements=["numpy"], reuse_existing=True, extra_env=extra_env,
        )

    assert result.success is True
    pip_install_envs = [env for cmd, env in recorded if "install" in cmd]
    assert pip_install_envs, "应有 pip install 子进程"
    for env in pip_install_envs:
        assert env is not None, "必须显式传 env（env=None 即全量继承，绕过白名单）"
        # 白名单收口：父进程凭证哨兵剔除。
        assert _SENTINEL_KEY not in env
        # 白名单保留：PATH 必在（pip 可执行查找）。
        assert env.get("PATH") == os.environ["PATH"]
        # extra_env 显式注入可见。
        assert env.get("PIP_INDEX_URL") == extra_env["PIP_INDEX_URL"]
        assert env.get("GIT_TERMINAL_PROMPT") == "0"
        # extra_env 覆盖白名单同名项（显式覆盖在白名单之上）。
        assert env.get("LANG") == "zh_CN.UTF-8"
    # 与纯函数结果一致（同一合并语义，无第二套逻辑）。
    assert pip_install_envs[0] == _build_sandbox_env(extra_env)
    # 非透传调用点（_collect_env_info）也必须收口白名单（哨兵同样不可见）。
    for cmd, env in recorded:
        assert env is not None and _SENTINEL_KEY not in env


# ===========================================================================
# CP-D1-3 注入值不落 install_log 明文（落点结论见 docstring / 模块头）
# ===========================================================================

def test_cp_d1_3_prepare_itself_never_writes_extra_env_into_install_log(sandbox_workspace):
    """prepare_venv 自身（install_log 拼装逻辑）绝不把 extra_env 的值写进
    install_log：pip 输出不含 token 时，install_log 必须不含 token。"""
    ws, work = sandbox_workspace
    recorder: List = []
    fake = _make_fake_run_subprocess(recorder, pip_stdout="Successfully installed numpy")

    with mock.patch.object(local_venv, "_run_subprocess", side_effect=fake):
        result = prepare_venv(str(work), requirements=["numpy"], extra_env=_EXTRA_ENV)

    assert result.success is True
    assert _FAKE_TOKEN not in result.install_log
    assert _FAKE_TOKEN not in (result.error or "")
    assert _FAKE_TOKEN not in str(result.env_info)


def test_cp_d1_3_pip_echo_token_lands_in_install_log_characterization(sandbox_workspace):
    """Characterization（现状锚定）：pip 输出回显 token 时，install_log 当前**明文含**
    token —— 实测结论：prepare_venv 层无 mask 兜底（D1 最小 diff 不加 mask 逻辑）。

    链路兜底落点：A3 `core/secrets_store.py::mask_value`（本测试落笔时尚未落地）+
    E3 execution 收尾统一 mask（architecture §9.4：logs 聚合前 mask_value 后再写
    state）。E3 落地后若本断言翻转（token 被 mask/移除），说明 mask 下沉到了
    prepare 层，需同步复核本 characterization 与 §9.4 落点表。
    """
    ws, work = sandbox_workspace
    echo = f"Looking in indexes: https://user:{_FAKE_TOKEN}@pypi.private.example/simple"
    recorder: List = []
    fake = _make_fake_run_subprocess(recorder, pip_stdout=echo)

    with mock.patch.object(local_venv, "_run_subprocess", side_effect=fake):
        result = prepare_venv(str(work), requirements=["numpy"], extra_env=_EXTRA_ENV)

    assert result.success is True
    # 现状：明文在 install_log 中（prepare 层无兜底）→ mask 责任在 E3 消费侧。
    assert _FAKE_TOKEN in result.install_log, (
        "characterization 翻转：prepare 层出现了 mask 兜底？"
        "请复核 D1 最小 diff 约束与 §9.4 mask 落点表"
    )


# ===========================================================================
# 验收补强（test-engineer）：非瞬态失败路径的透传 + 不泄漏 + 入参不变异
# ===========================================================================

def test_boundary_pip_permanent_failure_single_attempt_extra_env_intact(sandbox_workspace):
    """边界补强：开发自测只在成功/瞬态重试路径断言了透传，此处补非瞬态失败路径：
        - 非瞬态 pip 失败（包不存在类 stderr）→ 恰 1 次 attempt（不触发重试风暴），
          且该次 attempt 仍收到 extra_env；
        - prepare_venv 返回 success=False、error 汇总失败包；
        - 失败通道（error / install_log）不含 extra_env 注入值本身
          （pip 未回显时，prepare 自身失败拼装逻辑不引入泄漏）；
        - 调用方传入的 extra_env dict 不被 prepare_venv 变异（防御性契约）。
    """
    ws, work = sandbox_workspace
    recorder: List = []

    def fake(cmd, *, cwd, timeout, output_max_bytes, extra_env=None):
        recorder.append((list(cmd), extra_env))
        if "-m" in cmd and "venv" in cmd:
            venv_dir = Path(cmd[-1])
            (venv_dir / "bin").mkdir(parents=True, exist_ok=True)
            (venv_dir / "pyvenv.cfg").write_text("home = /usr\n", encoding="utf-8")
            (venv_dir / "bin" / "python").write_text("#!/bin/sh\n", encoding="utf-8")
            return _ok(cmd)
        if "install" in cmd:
            # 非瞬态失败：不含网络瞬态关键字 → _is_pip_transient=False → 不重试
            return _fail(cmd, stderr="ERROR: No matching distribution found for no-such-pkg")
        return _ok(cmd)

    extra_env_input = dict(_EXTRA_ENV)
    snapshot = dict(extra_env_input)

    with mock.patch.object(local_venv, "_run_subprocess", side_effect=fake):
        result = prepare_venv(str(work), requirements=["no-such-pkg"], extra_env=extra_env_input)

    # 非瞬态 → 恰 1 次 attempt，且收到 extra_env
    pip_calls = _calls_of(recorder, "pip_install")
    assert len(pip_calls) == 1, f"非瞬态失败不应重试，实测 {len(pip_calls)} 次 attempt"
    assert pip_calls[0][1] == snapshot, "失败路径的 attempt 也必须透传 extra_env"

    # 失败结果契约
    assert result.success is False
    assert "no-such-pkg" in (result.error or "")

    # 失败通道不含注入值明文（pip 未回显场景，prepare 自身零泄漏）
    assert _FAKE_TOKEN not in (result.error or "")
    assert _FAKE_TOKEN not in result.install_log

    # 入参 dict 不被变异
    assert extra_env_input == snapshot, "prepare_venv 不得变异调用方的 extra_env dict"
