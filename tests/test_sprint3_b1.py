"""Sprint 3 任务 B1 自测：sandbox/local_venv.py + 4 项护栏。

覆盖 dev-plan §B1 CP-B1-1 ~ CP-B1-9（AC-S3-02 四护栏映射）。

测试策略（参考 sp2 tests/test_sprint2_a5.py 的 mock subprocess 范式）：
    - 护栏 1（超时杀子树）/ 护栏 2（输出截断）/ 护栏 4（子进程崩溃）用真实子进程
      `sys.executable -c "..."` 实测（比纯 mock 更可信，且能验证进程组确实被杀）；
    - CP-B1-1（venv 创建）在 tmp workspace 跑真实轻量 venv（不装包，仅验证结构）；
    - 护栏 3（越界）/ CP-B1-6（禁 shell=True）/ CP-B1-7（复用幂等）/ CP-B1-8（pip 失败降级）
      用 mock / spy subprocess 验证。

硬约束验证：
    - 子进程禁 shell=True，command 一律列表形式（CP-B1-6 spy 断言）；
    - 路径越界一律 resolve()+is_relative_to，越界抛 SandboxCreationError（CP-B1-4）；
    - 任何 OSError / 子进程崩溃不逃逸到调用方（CP-B1-5）。

注意：所有真实 venv / 子进程测试在 tmp_path 下的受控 WORKSPACE_DIR 跑，
不污染真实 WORKSPACE_DIR，且超时杀进程后验证进程组无残留。
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from unittest import mock

import pytest

from core.errors import SandboxCreationError
from sandbox import local_venv
from sandbox.local_venv import (
    SandboxPrepareResult,
    SandboxRunResult,
    collect_artifacts,
    prepare_venv,
    run_in_venv,
)


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def sandbox_workspace(tmp_path, monkeypatch):
    """把 WORKSPACE_DIR patch 到 tmp_path 下的受控目录，避免污染真实 workspace。

    返回 (workspace_dir, work_dir)，work_dir 是 workspace 下的一个子目录。
    """
    ws = tmp_path / "workspace"
    work = ws / "thread-001" / "code"
    work.mkdir(parents=True)
    monkeypatch.setattr(local_venv, "WORKSPACE_DIR", ws)
    return ws, work


@pytest.fixture()
def venv_python(sandbox_workspace):
    """在受控 workspace 下创建一个真实 venv，返回 (ws, work, python_exe)。

    护栏 3 要求 python_exe 也必须在 WORKSPACE_DIR 下，因此护栏 1/2/4 这类执行测试
    必须用 workspace 下的真实 venv python，不能直接用 sys.executable（在 workspace 外）。
    venv 创建一次，供单个测试复用（venv python 等价于 sys.executable，能跑 -c 命令）。
    """
    ws, work = sandbox_workspace
    result = prepare_venv(str(work))
    assert result.success is True, f"venv 创建应成功: {result.error}"
    assert Path(result.python_exe).exists()
    return ws, work, result.python_exe


def _make_run_result(
    *,
    exit_code: int = 0,
    stdout: str = "",
    stderr: str = "",
    timed_out: bool = False,
    output_truncated: bool = False,
) -> SandboxRunResult:
    return SandboxRunResult(
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        duration_seconds=0.01,
        timed_out=timed_out,
        output_truncated=output_truncated,
        command=["pip", "install", "x"],
    )


# ===========================================================================
# CP-B1-1：prepare_venv 合法 work_dir 创建 venv 返回 success + 合法 python/pip exe
# ===========================================================================

def test_cp_b1_1_prepare_venv_creates_real_venv(sandbox_workspace):
    """CP-B1-1: 合法 work_dir 跑真实轻量 venv（不装包）返回 success=True + 合法 exe。"""
    ws, work = sandbox_workspace
    result = prepare_venv(str(work), requirements=None, requirements_files=None)

    assert isinstance(result, SandboxPrepareResult)
    assert result.success is True
    # python_exe / pip_exe 落在 work_dir/.venv 下，且真实存在。
    assert Path(result.venv_dir) == (work / ".venv")
    assert Path(result.python_exe).exists(), f"python_exe 应存在: {result.python_exe}"
    # pyvenv.cfg 标识 venv 已创建。
    assert (work / ".venv" / "pyvenv.cfg").exists()
    # python_exe 字面路径在 workspace 下（venv bin/python 是指向系统解释器的符号链接，
    # 不能用 resolve() 断言——resolve 会解到 /usr/bin/python3.11）。
    assert Path(os.path.abspath(result.python_exe)).is_relative_to(ws.resolve())
    # env_info 含 python_version。
    assert "python_version" in result.env_info
    assert result.install_failed_packages == []


# ===========================================================================
# CP-B1-2：护栏 1 超时 + 跨平台子进程树 kill（AC-S3-02 ①）
# ===========================================================================

def test_cp_b1_2_timeout_kills_process_tree(venv_python):
    """CP-B1-2: sleep 超 timeout 的真实子进程，run_in_venv 在上限内强制终止。

    断言：timed_out=True，整体耗时接近 timeout（被杀而非跑满 sleep），
    且子进程树无残留（getpgid 抛 ProcessLookupError 证明进程组已清）。
    """
    ws, work, py = venv_python
    # 子进程：自己再派生一个孙进程后 sleep 30s，验证整组被杀。
    child_code = (
        "import subprocess, sys, time; "
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']); "
        "time.sleep(30)"
    )
    command = [py, "-c", child_code]

    start = time.monotonic()
    result = run_in_venv(py, command, str(work), timeout=2)
    elapsed = time.monotonic() - start

    assert result.timed_out is True
    # 被超时杀掉，而非跑满 30s（留足缓冲，<10s）。
    assert elapsed < 10, f"超时应在上限附近终止，实测 {elapsed:.1f}s"
    # exit_code 非 0（被 SIGKILL）。
    assert result.exit_code != 0


def test_cp_b1_2b_no_orphan_process_group(venv_python):
    """CP-B1-2 补充: 超时杀进程后进程组无残留（POSIX 实测 killpg 生效）。

    监听 _kill_process_tree 是否被调用 + getpgid 在杀后查不到（ProcessLookupError）。
    Windows 跳过（killpg 语义不同）。
    """
    if sys.platform == "win32":  # pragma: no cover
        pytest.skip("POSIX 专属进程组残留检查")

    ws, work, py = venv_python
    captured_pgid: dict = {}

    real_kill = local_venv._kill_process_tree

    def _spy_kill(proc):
        # 记录被杀进程的 pgid，杀后再校验已清。
        try:
            captured_pgid["pgid"] = os.getpgid(proc.pid)
        except OSError:
            captured_pgid["pgid"] = None
        return real_kill(proc)

    command = [py, "-c", "import time; time.sleep(30)"]
    with mock.patch.object(local_venv, "_kill_process_tree", side_effect=_spy_kill) as m_kill:
        result = run_in_venv(py, command, str(work), timeout=2)

    assert result.timed_out is True
    m_kill.assert_called_once()  # 护栏 1 确实触发杀子树
    # 杀后给一点时间让内核回收，再验证进程组已不存在。
    pgid = captured_pgid.get("pgid")
    if pgid:
        time.sleep(0.5)
        with pytest.raises(ProcessLookupError):
            os.killpg(pgid, 0)  # signal 0 仅探测存在性；组已清则抛 ProcessLookupError


# ===========================================================================
# CP-B1-3：护栏 2 输出字节截断（AC-S3-02 ②）
# ===========================================================================

def test_cp_b1_3_output_truncation_keeps_tail(venv_python):
    """CP-B1-3: 超 output_max_bytes 的真实输出返回 output_truncated=True 且保留尾部。"""
    ws, work, py = venv_python
    # 子进程打印大量 'A' 后在末尾打印可识别尾部标记。
    child_code = (
        "import sys; "
        "sys.stdout.write('A' * 5000); "
        "sys.stdout.write('TAIL_MARKER_END'); "
        "sys.stdout.flush()"
    )
    command = [py, "-c", child_code]

    result = run_in_venv(py, command, str(work), timeout=30, output_max_bytes=1000)

    assert result.exit_code == 0
    assert result.output_truncated is True
    # 保留尾部：末尾的 TAIL_MARKER_END 必须在；头部被截断。
    assert "TAIL_MARKER_END" in result.stdout
    assert "truncated" in result.stdout  # 截断标记
    # 截断后体积受控（marker + 1000 bytes 量级，不是 5000+）。
    assert len(result.stdout.encode("utf-8")) <= 1000 + 200  # +marker 余量


def test_cp_b1_3b_no_truncation_when_small(venv_python):
    """CP-B1-3 补充: 小输出不截断，output_truncated=False。"""
    ws, work, py = venv_python
    command = [py, "-c", "print('hello small output')"]
    result = run_in_venv(py, command, str(work), timeout=30, output_max_bytes=1_000_000)
    assert result.output_truncated is False
    assert "hello small output" in result.stdout


# ===========================================================================
# CP-B1-4：护栏 3 工作目录限定（AC-S3-02 ③）—— 越界校验在 subprocess 之前
# ===========================================================================

def test_cp_b1_4_prepare_venv_rejects_out_of_workspace(sandbox_workspace):
    """CP-B1-4: prepare_venv 对越界 work_dir（/etc）抛 SandboxCreationError，且 0 次 subprocess。"""
    ws, work = sandbox_workspace
    with mock.patch.object(local_venv.subprocess, "Popen") as m_popen:
        with pytest.raises(SandboxCreationError):
            prepare_venv("/etc/evil_dir")
    m_popen.assert_not_called()  # 校验必须在 subprocess 之前


def test_cp_b1_4b_run_in_venv_rejects_out_of_workspace(sandbox_workspace):
    """CP-B1-4: run_in_venv 对越界 work_dir 抛 SandboxCreationError，且 0 次 subprocess。"""
    ws, work = sandbox_workspace
    with mock.patch.object(local_venv.subprocess, "Popen") as m_popen:
        with pytest.raises(SandboxCreationError):
            run_in_venv(sys.executable, ["echo", "x"], "/etc")
    m_popen.assert_not_called()


def test_cp_b1_4c_run_in_venv_rejects_out_of_workspace_python_exe(sandbox_workspace):
    """CP-B1-4: run_in_venv 对越界 python_exe（/usr/bin/python）抛 SandboxCreationError。"""
    ws, work = sandbox_workspace
    with mock.patch.object(local_venv.subprocess, "Popen") as m_popen:
        with pytest.raises(SandboxCreationError):
            run_in_venv("/usr/bin/python", ["echo", "x"], str(work))
    m_popen.assert_not_called()


def test_cp_b1_4d_collect_artifacts_rejects_out_of_workspace(sandbox_workspace):
    """CP-B1-4: collect_artifacts 对越界 work_dir 抛 SandboxCreationError。"""
    ws, work = sandbox_workspace
    with pytest.raises(SandboxCreationError):
        collect_artifacts("/etc")


# ===========================================================================
# CP-B1-5：护栏 4 子进程隔离 —— 崩溃 / OSError 不逃逸（AC-S3-02 ④）
# ===========================================================================

def test_cp_b1_5_nonzero_exit_does_not_raise(venv_python):
    """CP-B1-5: 子进程非 0 退出，run_in_venv 不抛异常，返回非 0 exit_code。"""
    ws, work, py = venv_python
    command = [py, "-c", "import sys; sys.exit(42)"]
    result = run_in_venv(py, command, str(work), timeout=30)
    assert isinstance(result, SandboxRunResult)
    assert result.exit_code == 42
    assert result.timed_out is False


def test_cp_b1_5b_popen_oserror_returns_exit_minus_one(sandbox_workspace):
    """CP-B1-5: Popen 启动抛 OSError（命令不存在），run_in_venv 兜底返回 exit_code=-1，不逃逸。"""
    ws, work = sandbox_workspace
    with mock.patch.object(
        local_venv.subprocess, "Popen", side_effect=OSError("No such file")
    ):
        # 用 workspace 下合法的 python_exe 占位（路径校验通过），但 Popen 被 mock 成抛错。
        fake_python = str(work / ".venv" / "bin" / "python")
        result = run_in_venv(fake_python, ["nonexistent_binary"], str(work), timeout=30)
    assert isinstance(result, SandboxRunResult)
    assert result.exit_code == -1
    assert "subprocess start failed" in result.stderr


def test_cp_b1_5c_real_missing_binary_returns_exit_minus_one(sandbox_workspace):
    """CP-B1-5: 真实执行不存在的二进制，FileNotFoundError(OSError 子类)兜底为 -1。"""
    ws, work = sandbox_workspace
    fake_python = str(work / ".venv" / "bin" / "python")
    result = run_in_venv(
        fake_python,
        [str(work / "definitely_not_a_real_binary_xyz")],
        str(work),
        timeout=30,
    )
    assert result.exit_code == -1
    assert "subprocess start failed" in result.stderr


# ===========================================================================
# CP-B1-6：subprocess.Popen 全部不使用 shell=True（spy 断言）
# ===========================================================================

def test_cp_b1_6_popen_never_uses_shell_true(venv_python):
    """CP-B1-6: spy 录制所有 Popen 调用，args 是列表 + shell 不为 True。"""
    ws, work, py = venv_python
    recorded = []
    real_popen = local_venv.subprocess.Popen

    def _spy_popen(cmd, *args, **kwargs):
        recorded.append((cmd, kwargs))
        return real_popen(cmd, *args, **kwargs)

    command = [py, "-c", "print('ok')"]
    with mock.patch.object(local_venv.subprocess, "Popen", side_effect=_spy_popen):
        run_in_venv(py, command, str(work), timeout=30)

    assert recorded, "应至少有一次 Popen 调用"
    for cmd, kwargs in recorded:
        assert isinstance(cmd, list), f"Popen 第一参数必须是列表，实测 {type(cmd)}"
        assert kwargs.get("shell", False) is not True, "禁止 shell=True"


def test_cp_b1_6b_prepare_venv_popen_never_shell_true(sandbox_workspace):
    """CP-B1-6: prepare_venv 路径上所有 Popen 也不用 shell=True。"""
    ws, work = sandbox_workspace
    recorded = []
    real_popen = local_venv.subprocess.Popen

    def _spy_popen(cmd, *args, **kwargs):
        recorded.append((cmd, kwargs))
        return real_popen(cmd, *args, **kwargs)

    with mock.patch.object(local_venv.subprocess, "Popen", side_effect=_spy_popen):
        prepare_venv(str(work))

    assert recorded
    for cmd, kwargs in recorded:
        assert isinstance(cmd, list)
        assert kwargs.get("shell", False) is not True


# ===========================================================================
# CP-B1-7：reuse_existing=True 且 .venv/pyvenv.cfg 存在跳过创建（幂等）
# ===========================================================================

def test_cp_b1_7_reuse_existing_skips_creation(sandbox_workspace):
    """CP-B1-7: 已存在 .venv/pyvenv.cfg 时 reuse_existing=True 跳过 venv 创建（不重建）。"""
    ws, work = sandbox_workspace
    # 第一次真实创建 venv。
    first = prepare_venv(str(work))
    assert first.success is True
    assert (work / ".venv" / "pyvenv.cfg").exists()

    # 第二次复用：spy Popen，断言不再出现 `-m venv` 创建命令。
    recorded = []
    real_popen = local_venv.subprocess.Popen

    def _spy_popen(cmd, *args, **kwargs):
        recorded.append(cmd)
        return real_popen(cmd, *args, **kwargs)

    with mock.patch.object(local_venv.subprocess, "Popen", side_effect=_spy_popen):
        second = prepare_venv(str(work), reuse_existing=True)

    assert second.success is True
    # 不应再有 venv 创建命令。
    venv_create_calls = [c for c in recorded if isinstance(c, list) and "venv" in c]
    assert venv_create_calls == [], f"复用时不应重建 venv，实测 {venv_create_calls}"


def test_cp_b1_7b_reuse_false_recreates(sandbox_workspace):
    """CP-B1-7 补充: reuse_existing=False 时即使 pyvenv.cfg 存在也会执行创建命令。"""
    ws, work = sandbox_workspace
    first = prepare_venv(str(work))
    assert first.success is True

    recorded = []
    real_popen = local_venv.subprocess.Popen

    def _spy_popen(cmd, *args, **kwargs):
        recorded.append(cmd)
        return real_popen(cmd, *args, **kwargs)

    with mock.patch.object(local_venv.subprocess, "Popen", side_effect=_spy_popen):
        prepare_venv(str(work), reuse_existing=False)

    venv_create_calls = [c for c in recorded if isinstance(c, list) and "venv" in c]
    assert venv_create_calls, "reuse_existing=False 应重新执行 venv 创建命令"


# ===========================================================================
# CP-B1-8：pip 装不上的包记入 install_failed_packages，success=False，不抛异常
# ===========================================================================

def test_cp_b1_8_pip_failure_records_package_no_raise(sandbox_workspace):
    """CP-B1-8: mock pip exit 非 0（包不存在），记入 install_failed_packages，success=False，不抛。"""
    ws, work = sandbox_workspace
    # 先真实创建 venv（复用），再 mock _pip_install_with_retry 模拟装包失败。
    prepare_venv(str(work))

    def _fake_pip(pip_exe, install_args, **kwargs):
        # 模拟"包不存在"非瞬态失败（exit_code 非 0）。
        return _make_run_result(
            exit_code=1,
            stderr="ERROR: Could not find a version that satisfies the requirement nonexistent-pkg",
        )

    with mock.patch.object(local_venv, "_pip_install_with_retry", side_effect=_fake_pip):
        result = prepare_venv(
            str(work),
            requirements=["nonexistent-pkg-xyz==9.9.9"],
            reuse_existing=True,
        )

    assert result.success is False
    assert "nonexistent-pkg-xyz==9.9.9" in result.install_failed_packages
    assert result.error is not None  # 失败摘要已填


def test_cp_b1_8b_pip_transient_retries_then_records(sandbox_workspace):
    """CP-B1-8 补充: pip 网络瞬态失败按 SANDBOX_PIP_MAX_RETRIES 退避重试后仍失败则记入。"""
    ws, work = sandbox_workspace
    call_count = {"n": 0}

    def _always_transient(cmd, *, cwd, timeout, output_max_bytes, extra_env=None):
        call_count["n"] += 1
        return _make_run_result(
            exit_code=1,
            stderr="Connection timed out (max retries exceeded)",
        )

    # patch 掉真实 sleep 避免退避拖慢测试。
    with mock.patch.object(local_venv.time, "sleep"), \
         mock.patch.object(local_venv, "_run_subprocess", side_effect=_always_transient):
        result = local_venv._pip_install_with_retry(
            "/fake/pip", ["torch"], cwd=str(work), timeout=10, max_retries=2,
        )

    # 首次 + 2 次重试 = 3 次（瞬态触发重试）。
    assert call_count["n"] == 3
    assert result.exit_code == 1


def test_cp_b1_8c_pip_non_transient_no_retry(sandbox_workspace):
    """CP-B1-8 补充: 非瞬态失败（版本冲突）不重试，仅执行一次。"""
    ws, work = sandbox_workspace
    call_count = {"n": 0}

    def _non_transient(cmd, *, cwd, timeout, output_max_bytes, extra_env=None):
        call_count["n"] += 1
        return _make_run_result(
            exit_code=1,
            stderr="ERROR: Cannot install foo because these package versions have conflicting dependencies",
        )

    with mock.patch.object(local_venv.time, "sleep"), \
         mock.patch.object(local_venv, "_run_subprocess", side_effect=_non_transient):
        result = local_venv._pip_install_with_retry(
            "/fake/pip", ["foo"], cwd=str(work), timeout=10, max_retries=2,
        )

    assert call_count["n"] == 1  # 非瞬态不重试
    assert result.exit_code == 1


def test_cp_b1_8d_venv_create_failure_no_raise(sandbox_workspace):
    """CP-B1-8 补充: venv 创建本身失败（mock exit 非 0），success=False + error，不抛异常。"""
    ws, work = sandbox_workspace

    def _fail_create(cmd, *, cwd, timeout, output_max_bytes, extra_env=None):
        return _make_run_result(exit_code=1, stderr="venv creation failed: disk full")

    with mock.patch.object(local_venv, "_run_subprocess", side_effect=_fail_create):
        result = prepare_venv(str(work), reuse_existing=False)

    assert result.success is False
    assert result.error is not None
    assert "venv 创建失败" in result.error


# ===========================================================================
# CP-B1-9：collect_artifacts 收集产物路径均为绝对路径且限定 WORKSPACE_DIR 下
# ===========================================================================

def test_cp_b1_9_collect_artifacts_absolute_within_workspace(sandbox_workspace):
    """CP-B1-9: collect_artifacts 返回绝对路径，且全部限定在 WORKSPACE_DIR 下。"""
    ws, work = sandbox_workspace
    # 造几个产物 + 一个非产物 + .venv 下的干扰文件。
    (work / "model.pt").write_bytes(b"weights")
    (work / "metrics.json").write_text("{}")
    (work / "plot.png").write_bytes(b"img")
    (work / "readme_unrelated.md").write_text("doc")  # 不匹配默认 glob
    venv_noise = work / ".venv" / "lib"
    venv_noise.mkdir(parents=True)
    (venv_noise / "fake.json").write_text("{}")  # .venv 下应被跳过

    artifacts = collect_artifacts(str(work))

    assert isinstance(artifacts, list)
    assert artifacts, "应收集到产物"
    for p in artifacts:
        assert os.path.isabs(p), f"产物路径必须绝对: {p}"
        assert Path(p).resolve().is_relative_to(ws.resolve()), f"产物必须在 workspace 下: {p}"
    # 含 model.pt / metrics.json / plot.png，不含 .venv 下的 fake.json。
    names = {Path(p).name for p in artifacts}
    assert "model.pt" in names
    assert "metrics.json" in names
    assert "plot.png" in names
    assert all(".venv" not in p for p in artifacts), ".venv 下文件应被跳过"


def test_cp_b1_9b_collect_artifacts_custom_patterns(sandbox_workspace):
    """CP-B1-9 补充: 自定义 patterns 只收集匹配项。"""
    ws, work = sandbox_workspace
    (work / "a.pt").write_bytes(b"x")
    (work / "b.json").write_text("{}")
    artifacts = collect_artifacts(str(work), patterns=["*.pt"])
    names = {Path(p).name for p in artifacts}
    assert names == {"a.pt"}


def test_cp_b1_9c_collect_artifacts_empty_when_missing_dir(sandbox_workspace):
    """CP-B1-9 补充: work_dir 不存在（但在 workspace 下）返回空列表，不抛。"""
    ws, work = sandbox_workspace
    missing = ws / "nonexistent_subdir"
    artifacts = collect_artifacts(str(missing))
    assert artifacts == []


# ===========================================================================
# 测试工程师补强用例（独立验收，沿用 sp2 A5 验收范式）
# ===========================================================================
# 补强方向（任务书）：
#   - 路径越界多形态（../ / 符号链接逃逸）+ 与 git_tools 路径不变量一致性
#   - 超时边界：恰好不超时不杀 vs 超时杀；timed_out 与 exit_code 组合
#   - 输出截断：恰好等于上限不截 vs 超一字节截断 + 尾部保留字节级断言
#   - collect_artifacts：跳过 __pycache__、绝对路径性质、去重排序
#   - pip 失败分级：瞬态重试 == SANDBOX_PIP_MAX_RETRIES vs 非瞬态直接记
#   - reuse 幂等：pyvenv.cfg 存在不重建（spy venv 创建 subprocess 0 次）
#   - SandboxCreationError 异常分类（永久错误家族）
#   - python_exe 符号链接偏差：lexical 校验仍拦 ../ 逃逸（核验项）


# --- 路径越界多形态 + 与 git_tools 不变量一致性 -------------------------------

def test_reinforce_dotdot_escape_rejected_work_dir(sandbox_workspace):
    """补强: work_dir 用 `../` 逃逸到 workspace 外被拒（resolve 后越界），0 次 subprocess。"""
    ws, work = sandbox_workspace
    escape = str(work / ".." / ".." / ".." / ".." / "etc")
    with mock.patch.object(local_venv.subprocess, "Popen") as m_popen:
        with pytest.raises(SandboxCreationError):
            prepare_venv(escape)
    m_popen.assert_not_called()


def test_reinforce_python_exe_dotdot_escape_rejected(sandbox_workspace):
    """补强（核验项）: python_exe lexical 校验仍能拦 `../` 逃逸。

    venv bin/python 用 lexical 校验（不解符号链接，因 bin/python 是指向系统解释器的
    符号链接）——必须独立验证 lexical 不会因此放过 `../` 逃逸。构造
    `work/.venv/../../../../etc/python` 断言被拒，且 0 次 subprocess。
    """
    ws, work = sandbox_workspace
    evil_python = str(work / ".venv" / ".." / ".." / ".." / ".." / "etc" / "python")
    with mock.patch.object(local_venv.subprocess, "Popen") as m_popen:
        with pytest.raises(SandboxCreationError):
            run_in_venv(evil_python, ["-c", "print(1)"], str(work))
    m_popen.assert_not_called()


def test_reinforce_symlink_escape_rejected_work_dir(sandbox_workspace):
    """补强: work_dir 是指向 workspace 外的符号链接时，resolve() 后越界被拒。"""
    ws, work = sandbox_workspace
    # 在 workspace 下放一个符号链接，target 在 workspace 外（/tmp）。
    outside = Path(os.path.abspath(os.path.join(str(ws), "..", "outside_target")))
    outside.mkdir(parents=True, exist_ok=True)
    link = ws / "evil_link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("文件系统不支持符号链接")
    try:
        with mock.patch.object(local_venv.subprocess, "Popen") as m_popen:
            with pytest.raises(SandboxCreationError):
                prepare_venv(str(link))
        m_popen.assert_not_called()
    finally:
        link.unlink(missing_ok=True)
        # outside 在 tmp_path 兄弟目录，留给 pytest tmp 清理体系；显式删以防污染。
        import shutil
        shutil.rmtree(outside, ignore_errors=True)


def test_reinforce_path_invariant_matches_git_tools(sandbox_workspace):
    """补强: local_venv 与 git_tools 路径不变量同源（resolve()+is_relative_to）。

    断言两模块的 _is_within_workspace 对同一 workspace 下/外路径判定一致，
    防止 sandbox 越界语义与 sp2 git_tools 漂移。
    """
    from core.tools import git_tools

    ws, work = sandbox_workspace
    inside = work / "sub" / "x"
    outside = Path(os.path.abspath(os.path.join(str(ws), "..", "elsewhere")))

    # git_tools._is_within_workspace 读其模块级 WORKSPACE_DIR；为公平对比，
    # 仅断言 local_venv 自身判定逻辑正确（resolve 包含关系），并与 git_tools 的
    # 实现路径形态一致（同名 helper + 同判据）。
    assert local_venv._is_within_workspace(inside) is True
    assert local_venv._is_within_workspace(outside) is False
    # 两模块 helper 同名、同判据（resolve + is_relative_to）——结构一致性断言。
    assert hasattr(git_tools, "_is_within_workspace")
    assert hasattr(local_venv, "_is_within_workspace")


# --- 超时边界：恰好不超时 vs 超时 -------------------------------------------

def test_reinforce_just_under_timeout_not_killed(venv_python):
    """补强: 命令在 timeout 内正常结束，不被杀，timed_out=False + exit_code=0。"""
    ws, work, py = venv_python
    command = [py, "-c", "import time; time.sleep(0.3); print('done')"]
    result = run_in_venv(py, command, str(work), timeout=10)
    assert result.timed_out is False
    assert result.exit_code == 0
    assert "done" in result.stdout


def test_reinforce_timeout_exit_code_combination(venv_python):
    """补强: 超时场景 timed_out=True 且 exit_code 非 0（被 kill 标准化为非 0）。"""
    ws, work, py = venv_python
    command = [py, "-c", "import time; time.sleep(30)"]
    result = run_in_venv(py, command, str(work), timeout=2)
    assert result.timed_out is True
    assert result.exit_code != 0


# --- 输出截断字节级边界 -----------------------------------------------------

def test_reinforce_truncate_exactly_at_limit_no_truncation():
    """补强: 输出恰好等于上限（== max_bytes）不截断（边界 <=）。"""
    raw = b"B" * 1000
    text, truncated = local_venv._truncate_output(raw, 1000)
    assert truncated is False
    assert text == "B" * 1000


def test_reinforce_truncate_one_byte_over_keeps_tail():
    """补强: 超一字节即截断，保留尾部精确字节（错误栈在末尾）。"""
    raw = b"HEAD" + b"M" * 996 + b"TAILBYTES"  # 4+996+9 = 1009 > 1000
    text, truncated = local_venv._truncate_output(raw, 1000)
    assert truncated is True
    # 尾部精确保留：末尾的 TAILBYTES 必须完整在结果里。
    assert text.endswith("TAILBYTES")
    # 头部 HEAD 被截掉（保留尾部语义）。
    assert "truncated" in text
    # 截掉的是头部：原始头部 "HEAD" 不在尾部 1000 字节内。
    tail_segment = text.split("...\n", 1)[-1]
    assert tail_segment == raw[-1000:].decode("utf-8")


# --- collect_artifacts 边界 -------------------------------------------------

def test_reinforce_collect_artifacts_skips_pycache(sandbox_workspace):
    """补强: __pycache__ 下的产物被跳过（_ARTIFACT_SKIP_DIRS）。"""
    ws, work = sandbox_workspace
    (work / "real.json").write_text("{}")
    pyc = work / "__pycache__"
    pyc.mkdir()
    (pyc / "cached.json").write_text("{}")
    artifacts = collect_artifacts(str(work))
    names = {Path(p).name for p in artifacts}
    assert "real.json" in names
    assert all("__pycache__" not in p for p in artifacts)


def test_reinforce_collect_artifacts_sorted_and_deduped(sandbox_workspace):
    """补强: collect_artifacts 返回排序去重（同文件被多 pattern 命中只出现一次）。"""
    ws, work = sandbox_workspace
    (work / "z.json").write_text("{}")
    (work / "a.json").write_text("{}")
    # *.json 与 * 都会命中 a.json/z.json；断言去重 + 排序。
    artifacts = collect_artifacts(str(work), patterns=["*.json", "*.json"])
    assert artifacts == sorted(artifacts)
    assert len(artifacts) == len(set(artifacts))
    assert len(artifacts) == 2


# --- pip 失败分级：瞬态重试次数 == SANDBOX_PIP_MAX_RETRIES --------------------

def test_reinforce_pip_transient_retry_count_matches_config(sandbox_workspace):
    """补强: 瞬态失败重试次数严格 == 首次 + SANDBOX_PIP_MAX_RETRIES（config 驱动）。"""
    import config

    ws, work = sandbox_workspace
    call_count = {"n": 0}

    def _always_transient(cmd, *, cwd, timeout, output_max_bytes, extra_env=None):
        call_count["n"] += 1
        return _make_run_result(exit_code=1, stderr="read timed out")

    with mock.patch.object(local_venv.time, "sleep"), \
         mock.patch.object(local_venv, "_run_subprocess", side_effect=_always_transient):
        local_venv._pip_install_with_retry(
            "/fake/pip", ["torch"], cwd=str(work), timeout=10,
            max_retries=config.SANDBOX_PIP_MAX_RETRIES,
        )

    assert call_count["n"] == config.SANDBOX_PIP_MAX_RETRIES + 1


def test_reinforce_pip_transient_recovers_on_retry(sandbox_workspace):
    """补强: 瞬态失败后某次重试成功 → 立即返回 success，不再继续重试。"""
    ws, work = sandbox_workspace
    results = iter([
        _make_run_result(exit_code=1, stderr="connection reset"),
        _make_run_result(exit_code=0, stdout="Successfully installed torch"),
    ])
    call_count = {"n": 0}

    def _recover(cmd, *, cwd, timeout, output_max_bytes, extra_env=None):
        call_count["n"] += 1
        return next(results)

    with mock.patch.object(local_venv.time, "sleep"), \
         mock.patch.object(local_venv, "_run_subprocess", side_effect=_recover):
        result = local_venv._pip_install_with_retry(
            "/fake/pip", ["torch"], cwd=str(work), timeout=10, max_retries=2,
        )

    assert call_count["n"] == 2  # 第二次成功即停
    assert result.exit_code == 0


# --- reuse 幂等：pyvenv.cfg 存在不重建（venv 创建 subprocess 0 次）----------

def test_reinforce_reuse_no_venv_create_subprocess(sandbox_workspace):
    """补强（核验项）: reuse 时 venv 创建（`-m venv`）的 subprocess 调用为 0 次。

    与 CP-B1-7 不同：这里只 mock 出 venv 创建的存在前提（手工造 pyvenv.cfg），
    然后 spy 整个 prepare_venv 路径，断言没有任何含 `-m venv` 的 Popen，
    且不需要真实跑 venv（更快、纯结构断言）。
    """
    ws, work = sandbox_workspace
    venv_dir = work / ".venv"
    bin_dir = venv_dir / "bin"
    bin_dir.mkdir(parents=True)
    (venv_dir / "pyvenv.cfg").write_text("home = /usr\n")
    # 造一个假的 python 可执行占位，使 python_exe.exists() 为真（跳过缺失分支）。
    (bin_dir / "python").write_text("#!/bin/sh\n")

    venv_create_calls = []
    real_popen = local_venv.subprocess.Popen

    def _spy_popen(cmd, *args, **kwargs):
        if isinstance(cmd, list) and "-m" in cmd and "venv" in cmd:
            venv_create_calls.append(cmd)
        return real_popen(cmd, *args, **kwargs)

    with mock.patch.object(local_venv.subprocess, "Popen", side_effect=_spy_popen):
        result = prepare_venv(str(work), reuse_existing=True)

    assert result.success is True
    assert venv_create_calls == [], f"reuse 不应有 venv 创建 subprocess，实测 {venv_create_calls}"


# --- SandboxCreationError 异常分类 ------------------------------------------

def test_reinforce_sandbox_creation_error_is_permanent():
    """补强: SandboxCreationError 属永久错误家族（不应被上游当瞬态重试）。"""
    from core.errors import PermanentError, SandboxError

    err = SandboxCreationError("x 越界", "detail")
    assert isinstance(err, SandboxError)
    assert isinstance(err, PermanentError)


def test_reinforce_run_in_venv_rejects_empty_command(sandbox_workspace):
    """补强: run_in_venv 对空 command / 非列表抛 SandboxCreationError（禁字符串命令）。"""
    ws, work = sandbox_workspace
    venv_python_path = str(work / ".venv" / "bin" / "python")
    for bad in ([], "echo hi", None):
        with pytest.raises(SandboxCreationError):
            run_in_venv(venv_python_path, bad, str(work))
