"""local_venv.py -- 本地 venv 执行环境 + 4 项护栏（S3-01）。

职责（纯基础设施，无 LLM / 无 GlobalState 依赖）：
    - ``prepare_venv``：在 work_dir 下创建（或复用）隔离 venv 并安装依赖；
    - ``run_in_venv``：在 venv 中以独立子进程执行单条命令，4 护栏兜底；
    - ``collect_artifacts``：扫描 work_dir 收集执行产物路径（限定 workspace 下）。

4 项护栏（architecture §2.1.3 逐条落地，硬要求）：
    1. 执行超时 + 跨平台子进程树 kill：Popen + communicate(timeout=)；POSIX
       start_new_session=True + os.killpg(SIGKILL)；Windows CREATE_NEW_PROCESS_GROUP
       + CTRL_BREAK_EVENT/kill；超时后再 communicate() 回收残余输出避免管道死锁。
    2. 输出字节截断：communicate 返回 bytes 后对 stdout/stderr 各自按
       output_max_bytes 截断，保留尾部（错误栈在末尾），置 output_truncated=True。
    3. 工作目录限定：所有 work_dir/venv_dir/python_exe 入参经
       resolve()+is_relative_to(WORKSPACE_DIR.resolve()) 校验，越界抛
       SandboxCreationError；校验必须在 subprocess 之前；子进程 cwd 强制为校验后 work_dir。
    4. 子进程隔离：每条命令独立 Popen（新进程组/会话）；_run_subprocess 内
       try/except 兜底任何 OSError 转 SandboxRunResult(exit_code=-1, ...) 返回，
       绝不让异常逃逸到 execution 节点之外。

安全约束（BUG-S1-02 / sp2 安全范式）：
    - 子进程禁 shell=True，command 一律列表形式；
    - 路径越界一律 resolve()+is_relative_to（复用 git_tools `_is_within_workspace` 范式）；
    - venv 创建必须用 sys.executable -m venv（当前解释器即 python3），不用裸 `python`；
    - 子进程环境白名单继承（_build_sandbox_env）：禁止全量继承 os.environ，
      LLM_API_KEY / DEEPXIV_TOKEN 等凭证一律不透传给沙箱内不可信代码。

跨平台说明：MVP 主测 Linux/macOS（与 sp2 git_tools 一致），Windows 分支代码保留但不强测。
"""

from __future__ import annotations

import logging
import os
import re
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from config import (
    SANDBOX_EXEC_TIMEOUT,
    SANDBOX_OUTPUT_MAX_BYTES,
    SANDBOX_PIP_CACHE_DIR,
    SANDBOX_PIP_INSTALL_TIMEOUT,
    SANDBOX_PIP_MAX_RETRIES,
    SANDBOX_VENV_CREATE_TIMEOUT,
    WORKSPACE_DIR,
)
from core.errors import SandboxCreationError

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

# pip 网络瞬态错误关键字（小写匹配 pip stderr/stdout）→ 触发指数退避重试。
# 复用 git_tools._TRANSIENT_STDERR_KEYWORDS 思路。
_PIP_TRANSIENT_KEYWORDS = (
    "connection refused",
    "connection timed out",
    "read timed out",
    "timed out",
    "could not resolve host",
    "temporary failure in name resolution",
    "network is unreachable",
    "failed to establish a new connection",
    "connection reset",
    "connection broken",
    "retrying",
    "newconnectionerror",
    "max retries exceeded",
)

# pip 网络瞬态重试退避序列（秒）：指数退避，长度对齐 SANDBOX_PIP_MAX_RETRIES。
# SANDBOX_PIP_MAX_RETRIES=2 → 退避 [1.0, 2.0]。
_PIP_RETRY_BACKOFF_SECONDS = (1.0, 2.0, 4.0, 8.0)

# collect_artifacts 默认产物 glob（模型权重 / 图像 / 结构化结果）。
_DEFAULT_ARTIFACT_PATTERNS = (
    "*.pt",
    "*.pth",
    "*.ckpt",
    "*.png",
    "*.jpg",
    "*.jpeg",
    "*.json",
    "*.csv",
    "*.txt",
    "*.log",
    "*.npy",
    "*.npz",
)

# collect_artifacts 扫描时跳过的目录名（避免把 venv / 缓存当产物）。
_ARTIFACT_SKIP_DIRS = (".venv", "venv", "__pycache__", ".git", ".pytest_cache")

# 沙箱子进程环境白名单（凭证泄漏止血）：沙箱里跑的是 LLM 生成的不可信代码，
# 全量继承 os.environ 会把 .env 装载的 LLM_API_KEY / DEEPXIV_TOKEN 等凭证暴露给
# 被执行代码。按「运行必需」原则仅透传以下变量；凭证注入只能走 extra_env 显式口
# （sp4 .secrets 方案沿用该口，本次不实现）。
_SANDBOX_ENV_ALLOWLIST = frozenset({
    # 基础运行必需：可执行查找 / 用户目录（pip 缓存 ~/.cache/pip）/ locale 编码
    "PATH", "HOME", "LANG",
    # 临时目录（tempfile / pip build；TEMP/TMP 兼顾 Windows）
    "TMPDIR", "TEMP", "TMP",
    # 输出无缓冲（超时被杀时尾部日志尽量完整，配合护栏 2 尾部截断）
    "PYTHONUNBUFFERED",
    # 网络代理（非凭证；pip 装依赖 / 沙箱内下载数据需要）
    "http_proxy", "https_proxy", "no_proxy",
    "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY",
    # CA 证书路径（内网/代理 MITM 场景 pip https 需要；路径类非凭证）
    "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE",
    # Windows 分支保留但不强测（缺 SystemRoot 时 subprocess 启动会失败）
    "SYSTEMROOT", "SYSTEMDRIVE", "COMSPEC", "PATHEXT",
})

# 前缀白名单：LC_*（locale 全家）+ PIP_*（pip 源/缓存配置，prepare_venv 装依赖需要）。
# 注意不含 PYTHON*（PYTHONPATH 继承会把父进程模块路径漏进沙箱，破坏隔离）。
_SANDBOX_ENV_ALLOWLIST_PREFIXES = ("LC_", "PIP_")

# PIP_CACHE_DIR 注入点说明（Sprint 6 MF-1）：
# 白名单前缀 PIP_* 可能从宿主继承指向 home 的 PIP_CACHE_DIR（~/.cache/pip），
# 必须在 extra_env 合并之后无条件覆盖为 SANDBOX_PIP_CACHE_DIR（/data 卷），
# 防止打爆 home 配额。覆盖使用赋值而非 setdefault，确保宿主传入值被压制。

# 凭证形态否决（sp4 D2 / HOTFIX-2 备忘闭环，架构师定案 (a')-修正版）：
# URL userinfo（`://user:pass@host` 或 token-only `://token@host`）按 RFC 语义
# 本身即凭证载体——`PIP_INDEX_URL=https://user:token@host/` / 认证代理等形态若随
# 白名单继承进沙箱，会把私有源凭证暴露给沙箱内不可信代码。白名单继承环节对
# **所有**继承值统一做值级否决（不特判 PIP_ 前缀，单一规则）；凭证注入的正规
# 路径 = .secrets → extra_env 显式口（不过滤）。
_CREDENTIAL_URL_USERINFO_RE = re.compile(r"://[^/\s@]+@")


def _build_sandbox_env(extra_env: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """构造沙箱子进程环境：白名单继承 + extra_env 显式覆盖，禁止全量继承 os.environ。

    prepare_venv（venv 创建 / pip install）与 run_in_venv（执行）两条 subprocess
    路径都经 _run_subprocess，在此单点收口——pip 路径同样会执行不完全可信代码
    （sdist 安装会跑包内 setup.py），且其运行必需的代理 / PIP_* 变量已在白名单内，
    收口无功能损失。

    白名单继承值经凭证形态否决（URL userinfo 命中即整变量剔除 + WARNING，日志
    只打变量名绝不打值）；extra_env 显式注入口不过滤（凭证注入唯一入口全通）。
    """
    env: Dict[str, str] = {}
    for key, value in os.environ.items():
        if key not in _SANDBOX_ENV_ALLOWLIST and not key.startswith(_SANDBOX_ENV_ALLOWLIST_PREFIXES):
            continue
        if _CREDENTIAL_URL_USERINFO_RE.search(value):
            logger.warning(
                "沙箱环境白名单变量 %s 值含 URL 内嵌凭证形态，已剔除不透传"
                "（凭证请走 .secrets/extra_env 显式注入）", key,
            )
            continue
        env[key] = value
    if extra_env:
        env.update(extra_env)
    # Sprint 6 MF-1：无条件覆盖 PIP_CACHE_DIR 为项目 /data 卷路径（压制宿主或 extra_env
    # 中指向 home 的旧值，防止沙箱 pip install 打爆 home 配额）。
    # 使用赋值而非 setdefault，保证在 extra_env 合并之后强制生效。
    env["PIP_CACHE_DIR"] = str(SANDBOX_PIP_CACHE_DIR)
    return env


# ---------------------------------------------------------------------------
# dataclass（结构化结果）
# ---------------------------------------------------------------------------

@dataclass
class SandboxRunResult:
    """单次 sandbox 子进程执行的结构化结果。

    供 execution 节点映射为 ExecutionResult 的原料。
    """

    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float
    timed_out: bool
    output_truncated: bool
    command: List[str]  # 实际执行的命令（列表形式，便于审计）


@dataclass
class SandboxPrepareResult:
    """venv 创建 + 依赖安装的结构化结果。"""

    success: bool
    venv_dir: str  # venv 绝对路径（workspace_dir 下）
    python_exe: str  # venv 内 python 解释器绝对路径
    pip_exe: str  # venv 内 pip 绝对路径
    env_info: Dict[str, str] = field(default_factory=dict)  # {"python_version": ..., "key_packages": ...}
    install_log: str = ""  # pip 安装日志（受 OUTPUT_MAX_BYTES 截断）
    install_failed_packages: List[str] = field(default_factory=list)  # 装不上的包
    error: Optional[str] = None  # 创建/安装失败摘要（success=False 时填）


# ---------------------------------------------------------------------------
# 护栏 3：工作目录限定 —— 路径越界校验（复用 git_tools 范式）
# ---------------------------------------------------------------------------

def _is_within_workspace(target: Path) -> bool:
    """校验 target 解析后是否位于 WORKSPACE_DIR 之下（含等于自身）。

    与 git_tools._is_within_workspace 同一判定路径：resolve 后比较真实包含关系。
    符号链接逃逸也被 resolve() 解开后拦截。

    适用于「写入副作用目录」（work_dir / venv_dir / requirements_files）：这类路径
    必须保证真实落点在 workspace 下，符号链接逃逸需拦截，故用 resolve()。
    """
    workspace = WORKSPACE_DIR.resolve()
    resolved = target.resolve()
    return resolved == workspace or resolved.is_relative_to(workspace)


def _is_within_workspace_lexical(target: Path) -> bool:
    """字面（不解符号链接）校验 target 是否位于 WORKSPACE_DIR 下，仅规范化 `..`/`.`。

    专用于 ``python_exe`` 校验：venv 内 ``bin/python`` 本身就是指向系统解释器
    （如 /usr/bin/python3.11）的符号链接，用 resolve() 会解开到 workspace 外导致误判越界。
    护栏意图是「确认调用方传的是 prepare_venv 在 workspace 下创建的 venv python，
    而非任意系统二进制」——因此对其字面路径做包含校验即可，不解符号链接。
    WORKSPACE_DIR 侧仍用 resolve()（其本身是确定的真实目录）。
    """
    workspace = WORKSPACE_DIR.resolve()
    # os.path.abspath 规范化 `..`/`.` 但不解符号链接，杜绝 ../ 逃逸。
    normalized = Path(os.path.abspath(str(target)))
    return normalized == workspace or normalized.is_relative_to(workspace)


def _require_within_workspace(target: str, *, label: str) -> Path:
    """校验 target（写入副作用路径）在 WORKSPACE_DIR 之下，越界抛 SandboxCreationError。

    护栏 3 的统一入口（work_dir / venv_dir / requirements_files）：必须在任何
    subprocess 之前调用。返回 resolve 后的 Path（真实落点）。
    """
    path = Path(target)
    if not _is_within_workspace(path):
        raise SandboxCreationError(
            f"{label} 越界",
            f"{label}={target} 不在 WORKSPACE_DIR({WORKSPACE_DIR}) 之下",
        )
    return path.resolve()


def _require_python_exe_within_workspace(python_exe: str) -> Path:
    """校验 python_exe 字面路径在 WORKSPACE_DIR 下（不解符号链接），越界抛 SandboxCreationError。

    venv bin/python 是指向系统解释器的符号链接，故用 lexical 校验而非 resolve()。
    """
    path = Path(python_exe)
    if not _is_within_workspace_lexical(path):
        raise SandboxCreationError(
            "python_exe 越界",
            f"python_exe={python_exe} 不在 WORKSPACE_DIR({WORKSPACE_DIR}) 之下",
        )
    return Path(os.path.abspath(python_exe))


# ---------------------------------------------------------------------------
# venv 内可执行文件路径（跨平台）
# ---------------------------------------------------------------------------

def _venv_bin_dir(venv_dir: Path) -> Path:
    """venv 内可执行目录：Windows 为 Scripts，POSIX 为 bin。"""
    return venv_dir / ("Scripts" if sys.platform == "win32" else "bin")


def _venv_python_exe(venv_dir: Path) -> Path:
    bin_dir = _venv_bin_dir(venv_dir)
    return bin_dir / ("python.exe" if sys.platform == "win32" else "python")


def _venv_pip_exe(venv_dir: Path) -> Path:
    bin_dir = _venv_bin_dir(venv_dir)
    return bin_dir / ("pip.exe" if sys.platform == "win32" else "pip")


# ---------------------------------------------------------------------------
# 护栏 1 / 2 / 4：跨平台子进程封装
# ---------------------------------------------------------------------------

def _kill_process_tree(proc: "subprocess.Popen") -> None:
    """跨平台杀子进程树（含孙进程）。

    POSIX：proc 以 start_new_session=True 启动（新会话/进程组首领），
        os.killpg(os.getpgid(pid), SIGKILL) 杀整组。
    Windows：proc 以 CREATE_NEW_PROCESS_GROUP 启动，
        先 send_signal(CTRL_BREAK_EVENT)，再 proc.kill()，最后 taskkill /T /F 兜底。

    任何 kill 阶段的 OSError（进程已退出等）均吞掉——目的是确保不残留，
    单个 kill 失败不应让护栏崩溃。
    """
    if sys.platform == "win32":  # pragma: no cover - Windows 分支不强测
        try:
            proc.send_signal(signal.CTRL_BREAK_EVENT)  # type: ignore[attr-defined]
        except (OSError, ValueError) as exc:
            logger.debug("_kill_process_tree: CTRL_BREAK_EVENT 失败: %s", exc)
        try:
            proc.kill()
        except OSError as exc:
            logger.debug("_kill_process_tree: proc.kill 失败: %s", exc)
        # taskkill /T /F 兜底杀子树（孙进程）。
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            logger.debug("_kill_process_tree: taskkill 兜底失败: %s", exc)
        return

    # POSIX：杀整个进程组（killpg），覆盖 torch.distributed 等派生的孙进程。
    try:
        pgid = os.getpgid(proc.pid)
    except OSError as exc:
        # 进程已退出，直接 kill 兜底。
        logger.debug("_kill_process_tree: getpgid 失败（进程可能已退出）: %s", exc)
        try:
            proc.kill()
        except OSError:
            pass
        return
    try:
        os.killpg(pgid, signal.SIGKILL)
    except OSError as exc:
        logger.debug("_kill_process_tree: killpg 失败: %s", exc)
        try:
            proc.kill()
        except OSError:
            pass


def _truncate_output(raw: bytes, max_bytes: int) -> Tuple[str, bool]:
    """护栏 2：对单路输出按字节上限截断，保留尾部（错误栈在末尾）。

    Returns:
        (decoded_text, truncated)
    """
    if raw is None:
        return "", False
    if len(raw) <= max_bytes:
        return raw.decode("utf-8", errors="replace"), False
    tail = raw[-max_bytes:]  # 保留尾部（错误信息通常在末尾）
    marker = f"... [truncated, kept last {max_bytes} bytes] ...\n"
    return marker + tail.decode("utf-8", errors="replace"), True


def _run_subprocess(
    cmd: List[str],
    *,
    cwd: str,
    timeout: int,
    output_max_bytes: int,
    extra_env: Optional[Dict[str, str]] = None,
) -> SandboxRunResult:
    """统一子进程封装：列表形式 + 禁 shell=True + 进程组隔离 + 超时杀子树 + 输出截断。

    4 护栏在此集中落地（护栏 3 的路径校验由调用方在本函数之前完成）：
        - 护栏 1：超时杀子进程树（killpg / CTRL_BREAK+kill）+ 超时后 communicate 回收残余；
        - 护栏 2：stdout/stderr 各自按 output_max_bytes 尾部截断；
        - 护栏 4：每条命令独立新进程组/会话；OSError 兜底转 exit_code=-1 不逃逸。

    绝不抛异常逃逸——任何启动/执行失败均返回 SandboxRunResult。
    """
    # 子进程环境：白名单继承 + extra_env 显式覆盖（凭证泄漏止血，禁全量继承）。
    env = _build_sandbox_env(extra_env)

    popen_kwargs: Dict[str, object] = dict(
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        # 关键：不传 shell=True；cmd 是列表形式。
    )
    if sys.platform == "win32":  # pragma: no cover - Windows 分支不强测
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
    else:
        popen_kwargs["start_new_session"] = True  # setsid，新会话/进程组首领

    start = time.monotonic()
    try:
        proc = subprocess.Popen(cmd, **popen_kwargs)  # type: ignore[arg-type]
    except OSError as exc:
        # 护栏 4：启动失败（命令不存在 / 权限 / 文件缺失等）兜底，不逃逸。
        duration = time.monotonic() - start
        logger.warning("_run_subprocess: Popen 启动失败 cmd=%s: %s", cmd, exc)
        return SandboxRunResult(
            exit_code=-1,
            stdout="",
            stderr=f"subprocess start failed: {exc}",
            duration_seconds=round(duration, 3),
            timed_out=False,
            output_truncated=False,
            command=list(cmd),
        )

    timed_out = False
    try:
        stdout_b, stderr_b = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        # 护栏 1：超时杀子进程树。
        timed_out = True
        logger.warning("_run_subprocess: 超时（timeout=%ds）杀子进程树 cmd=%s", timeout, cmd)
        _kill_process_tree(proc)
        # 超时后必须再 communicate() 回收残余输出，避免管道死锁。
        try:
            stdout_b, stderr_b = proc.communicate()
        except (OSError, ValueError) as exc:
            logger.debug("_run_subprocess: 超时后回收残余输出失败: %s", exc)
            stdout_b, stderr_b = b"", b""
    except OSError as exc:
        # 护栏 4：communicate 阶段 OSError 兜底，杀进程后返回 exit_code=-1。
        duration = time.monotonic() - start
        logger.warning("_run_subprocess: communicate OSError cmd=%s: %s", cmd, exc)
        _kill_process_tree(proc)
        return SandboxRunResult(
            exit_code=-1,
            stdout="",
            stderr=f"subprocess communicate failed: {exc}",
            duration_seconds=round(duration, 3),
            timed_out=False,
            output_truncated=False,
            command=list(cmd),
        )

    duration = time.monotonic() - start

    # 护栏 2：stdout/stderr 各自截断，保留尾部。
    stdout_text, stdout_trunc = _truncate_output(stdout_b, output_max_bytes)
    stderr_text, stderr_trunc = _truncate_output(stderr_b, output_max_bytes)

    # 超时场景 exit_code 可能为 None（被 kill），统一标准化为非 0。
    exit_code = proc.returncode
    if exit_code is None:
        exit_code = -1

    return SandboxRunResult(
        exit_code=exit_code,
        stdout=stdout_text,
        stderr=stderr_text,
        duration_seconds=round(duration, 3),
        timed_out=timed_out,
        output_truncated=stdout_trunc or stderr_trunc,
        command=list(cmd),
    )


# ---------------------------------------------------------------------------
# pip 安装（瞬态退避重试 + 失败降级）
# ---------------------------------------------------------------------------

def _is_pip_transient(run_result: SandboxRunResult) -> bool:
    """判断 pip 失败是否为网络瞬态（连接超时 / 解析失败）→ 可退避重试。"""
    if run_result.timed_out:
        return True  # 超时按瞬态处理（可能是网络慢）
    blob = f"{run_result.stdout}\n{run_result.stderr}".lower()
    return any(kw in blob for kw in _PIP_TRANSIENT_KEYWORDS)


def _pip_install_with_retry(
    pip_exe: str,
    install_args: List[str],
    *,
    cwd: str,
    timeout: int,
    max_retries: int,
    extra_env: Optional[Dict[str, str]] = None,
) -> SandboxRunResult:
    """单条 pip install 命令：网络瞬态按指数退避重试，其它失败直接返回。

    install_args 形如 ["torch", "numpy"] 或 ["-r", "/abs/requirements.txt"]。
    extra_env 透传给每次 _run_subprocess（含重试路径），在白名单环境之上显式覆盖。
    """
    cmd = [pip_exe, "install"] + install_args
    last_result: Optional[SandboxRunResult] = None
    total_attempts = max(0, max_retries) + 1  # 首次 + max_retries 次重试
    for attempt in range(total_attempts):
        result = _run_subprocess(
            cmd,
            cwd=cwd,
            timeout=timeout,
            output_max_bytes=SANDBOX_OUTPUT_MAX_BYTES,
            extra_env=extra_env,
        )
        last_result = result
        if result.exit_code == 0:
            return result
        if not _is_pip_transient(result):
            # 非瞬态（包不存在 / 版本冲突 / 编译失败）→ 不重试。
            return result
        # 瞬态：还有退避机会才 sleep。
        if attempt < total_attempts - 1:
            backoff = _PIP_RETRY_BACKOFF_SECONDS[
                min(attempt, len(_PIP_RETRY_BACKOFF_SECONDS) - 1)
            ]
            logger.warning(
                "pip install 网络瞬态失败（attempt=%d/%d）退避 %.1fs: %s",
                attempt + 1, total_attempts, backoff, install_args,
            )
            time.sleep(backoff)
    assert last_result is not None  # 循环至少跑一次
    return last_result


# ---------------------------------------------------------------------------
# prepare_venv
# ---------------------------------------------------------------------------

def prepare_venv(
    work_dir: str,
    requirements: Optional[List[str]] = None,
    requirements_files: Optional[List[str]] = None,
    reuse_existing: bool = True,
    venv_timeout: int = SANDBOX_VENV_CREATE_TIMEOUT,
    pip_timeout: int = SANDBOX_PIP_INSTALL_TIMEOUT,
    extra_env: Optional[Dict[str, str]] = None,
) -> SandboxPrepareResult:
    """在 work_dir 下创建（或复用）隔离 venv 并安装依赖。

    护栏：
        - work_dir 经 resolve()+is_relative_to(WORKSPACE_DIR) 校验，越界抛 SandboxCreationError；
        - venv 落在 work_dir/.venv（位于 workspace_dir 下）；
        - python -m venv / pip install 均经 _run_subprocess（列表形式，禁 shell=True）；
        - venv 创建必须用 sys.executable -m venv（当前解释器即 python3）；
        - pip 网络瞬态失败按 SANDBOX_PIP_MAX_RETRIES 指数退避重试；
        - reuse_existing=True 且 .venv/pyvenv.cfg 存在时跳过创建（复用），仍执行增量 pip install。

    pip 失败分级（§2.1.4）：
        - 网络瞬态 → 退避重试；
        - 包不存在 / 版本冲突 / 编译失败 → 记入 install_failed_packages，success=False，不抛异常；
        - venv 创建本身失败 → success=False + error 摘要。

    Args:
        work_dir: 工作目录绝对路径（必须位于 WORKSPACE_DIR 下）。
        requirements: 显式依赖列表（来自 ReproductionPlan.environment）。
        requirements_files: requirements.txt / environment.yml / pyproject.toml 绝对路径列表。
        reuse_existing: 同目录已存在 venv 时复用（默认 True）。
        venv_timeout: python -m venv 创建超时（秒）。
        pip_timeout: 单次 pip install 超时（秒）。
        extra_env: 额外环境变量（在白名单环境之上显式注入/覆盖；透传给 venv 创建
            与每次 pip install 子进程，含瞬态重试路径——pip 装私有源依赖需凭证，
            GIT_TERMINAL_PROMPT=0 也经此让 pip 内 git 依赖认证失败立即返回）。

    Returns:
        SandboxPrepareResult。

    Raises:
        SandboxCreationError: work_dir 越界（护栏 3）。
    """
    # 护栏 3：work_dir 越界校验（必须在任何 subprocess 之前）。
    work_path = _require_within_workspace(work_dir, label="work_dir")
    work_path.mkdir(parents=True, exist_ok=True)
    work_dir_str = str(work_path)

    venv_dir = work_path / ".venv"
    python_exe = _venv_python_exe(venv_dir)
    pip_exe = _venv_pip_exe(venv_dir)

    install_log_parts: List[str] = []
    install_failed_packages: List[str] = []

    # --- venv 创建 / 复用 ---
    pyvenv_cfg = venv_dir / "pyvenv.cfg"
    reused = reuse_existing and pyvenv_cfg.exists()
    if reused:
        logger.info("prepare_venv: 复用已存在 venv %s（跳过创建）", venv_dir)
    else:
        # 关键：用 sys.executable（当前解释器即 python3），不用裸 `python`（本机可能是 py2）。
        create_cmd = [sys.executable, "-m", "venv", str(venv_dir)]
        create_result = _run_subprocess(
            create_cmd,
            cwd=work_dir_str,
            timeout=venv_timeout,
            output_max_bytes=SANDBOX_OUTPUT_MAX_BYTES,
            extra_env=extra_env,
        )
        if create_result.exit_code != 0 or not pyvenv_cfg.exists():
            err = (
                f"venv 创建失败 exit={create_result.exit_code} "
                f"timed_out={create_result.timed_out} "
                f"stderr={create_result.stderr.strip()[:500]}"
            )
            logger.error("prepare_venv: %s", err)
            return SandboxPrepareResult(
                success=False,
                venv_dir=str(venv_dir),
                python_exe=str(python_exe),
                pip_exe=str(pip_exe),
                env_info={},
                install_log=create_result.stdout + create_result.stderr,
                install_failed_packages=[],
                error=err,
            )
        install_log_parts.append(f"[venv create] exit={create_result.exit_code}")

    # venv 可执行文件应已就位（复用或刚创建）。
    if not python_exe.exists():
        err = f"venv python 解释器缺失: {python_exe}"
        logger.error("prepare_venv: %s", err)
        return SandboxPrepareResult(
            success=False,
            venv_dir=str(venv_dir),
            python_exe=str(python_exe),
            pip_exe=str(pip_exe),
            env_info={},
            install_log="\n".join(install_log_parts),
            install_failed_packages=[],
            error=err,
        )

    # --- pip 依赖安装（增量） ---
    overall_success = True

    # 1) requirements_files（-r 安装）：每个文件单独一条命令，越界文件跳过。
    for req_file in requirements_files or []:
        req_path = Path(req_file)
        if not _is_within_workspace(req_path):
            logger.warning("prepare_venv: requirements_file 越界，跳过: %s", req_file)
            install_log_parts.append(f"[skip out-of-workspace req file] {req_file}")
            install_failed_packages.append(req_file)
            overall_success = False
            continue
        if not req_path.exists():
            logger.warning("prepare_venv: requirements_file 不存在，跳过: %s", req_file)
            install_log_parts.append(f"[skip missing req file] {req_file}")
            install_failed_packages.append(req_file)
            overall_success = False
            continue
        result = _pip_install_with_retry(
            str(pip_exe),
            ["-r", str(req_path.resolve())],
            cwd=work_dir_str,
            timeout=pip_timeout,
            max_retries=SANDBOX_PIP_MAX_RETRIES,
            extra_env=extra_env,
        )
        install_log_parts.append(
            f"[pip install -r {req_file}] exit={result.exit_code}\n"
            f"{result.stdout}\n{result.stderr}"
        )
        if result.exit_code != 0:
            logger.warning("prepare_venv: pip install -r 失败: %s", req_file)
            install_failed_packages.append(req_file)
            overall_success = False

    # 2) requirements（显式包列表）：逐包安装，便于精确定位失败包。
    for pkg in requirements or []:
        result = _pip_install_with_retry(
            str(pip_exe),
            [pkg],
            cwd=work_dir_str,
            timeout=pip_timeout,
            max_retries=SANDBOX_PIP_MAX_RETRIES,
            extra_env=extra_env,
        )
        install_log_parts.append(
            f"[pip install {pkg}] exit={result.exit_code}\n"
            f"{result.stdout}\n{result.stderr}"
        )
        if result.exit_code != 0:
            logger.warning("prepare_venv: pip install 失败: %s", pkg)
            install_failed_packages.append(pkg)
            overall_success = False

    # --- env_info：venv python 版本 + key packages ---
    env_info = _collect_env_info(str(python_exe), work_dir_str)

    # install_log 受 OUTPUT_MAX_BYTES 截断（保留尾部，复用护栏 2）。
    install_log_raw = "\n".join(install_log_parts).encode("utf-8")
    install_log, _ = _truncate_output(install_log_raw, SANDBOX_OUTPUT_MAX_BYTES)

    error: Optional[str] = None
    if not overall_success:
        error = f"部分依赖安装失败: {install_failed_packages}"

    logger.info(
        "prepare_venv: work_dir=%s reused=%s success=%s failed=%s",
        work_dir_str, reused, overall_success, install_failed_packages,
    )
    return SandboxPrepareResult(
        success=overall_success,
        venv_dir=str(venv_dir),
        python_exe=str(python_exe),
        pip_exe=str(pip_exe),
        env_info=env_info,
        install_log=install_log,
        install_failed_packages=install_failed_packages,
        error=error,
    )


def _collect_env_info(python_exe: str, work_dir: str) -> Dict[str, str]:
    """收集 venv 的 python 版本与已装包摘要（best-effort，失败不抛）。"""
    env_info: Dict[str, str] = {}
    # python 版本
    ver_result = _run_subprocess(
        [python_exe, "--version"],
        cwd=work_dir,
        timeout=30,
        output_max_bytes=SANDBOX_OUTPUT_MAX_BYTES,
    )
    if ver_result.exit_code == 0:
        # python --version 早期版本写 stderr，新版本写 stdout，合并取非空。
        version = (ver_result.stdout.strip() or ver_result.stderr.strip())
        if version:
            env_info["python_version"] = version
    # 关键包列表（pip freeze 头部，best-effort）
    freeze_result = _run_subprocess(
        [python_exe, "-m", "pip", "freeze"],
        cwd=work_dir,
        timeout=60,
        output_max_bytes=SANDBOX_OUTPUT_MAX_BYTES,
    )
    if freeze_result.exit_code == 0:
        pkgs = [ln for ln in freeze_result.stdout.splitlines() if ln.strip()]
        env_info["key_packages"] = ", ".join(pkgs[:20])
    return env_info


# ---------------------------------------------------------------------------
# run_in_venv
# ---------------------------------------------------------------------------

def run_in_venv(
    python_exe: str,
    command: List[str],
    work_dir: str,
    timeout: int = SANDBOX_EXEC_TIMEOUT,
    output_max_bytes: int = SANDBOX_OUTPUT_MAX_BYTES,
    extra_env: Optional[Dict[str, str]] = None,
) -> SandboxRunResult:
    """在隔离 venv 中以独立子进程执行单条命令，捕获 stdout/stderr/exit_code/时长。

    4 项护栏：执行超时强杀进程树 / 输出字节截断 / cwd 限定 workspace_dir / 独立子进程隔离。

    Args:
        python_exe: prepare_venv 返回的 venv python 绝对路径（也必须在 workspace 下）。
        command: 执行命令（列表形式，禁 shell=True），如 [python_exe, "train.py", "--epochs", "1"]。
        work_dir: 子进程 cwd（必须位于 WORKSPACE_DIR 下）。
        timeout: 执行超时（秒）。
        output_max_bytes: stdout/stderr 各自字节上限。
        extra_env: 额外环境变量（在白名单环境之上显式注入/覆盖；凭证注入唯一入口）。

    Returns:
        SandboxRunResult（绝不抛异常逃逸；越界除外）。

    Raises:
        SandboxCreationError: work_dir 或 python_exe 越界（护栏 3，在 subprocess 之前）。
    """
    # 护栏 3：work_dir + python_exe 越界校验（必须在任何 subprocess 之前）。
    # work_dir 用 resolve()（写入副作用路径，防符号链接逃逸）；
    # python_exe 用 lexical 校验（venv bin/python 本身是指向系统解释器的符号链接）。
    work_path = _require_within_workspace(work_dir, label="work_dir")
    _require_python_exe_within_workspace(python_exe)

    if not isinstance(command, (list, tuple)) or not command:
        raise SandboxCreationError(
            "command 必须为非空列表（禁 shell=True / 字符串命令）",
            f"command={command!r}",
        )

    # 护栏 1/2/4：交给 _run_subprocess（cwd 强制为校验后 work_dir）。
    return _run_subprocess(
        [str(c) for c in command],
        cwd=str(work_path),
        timeout=timeout,
        output_max_bytes=output_max_bytes,
        extra_env=extra_env,
    )


# ---------------------------------------------------------------------------
# collect_artifacts
# ---------------------------------------------------------------------------

def collect_artifacts(
    work_dir: str,
    patterns: Optional[List[str]] = None,
) -> List[str]:
    """扫描 work_dir 收集执行产物路径（绝对路径，限定 workspace_dir 下）。

    护栏 3：work_dir 越界抛 SandboxCreationError。
    跳过 .venv / __pycache__ / .git 等非产物目录。

    Args:
        work_dir: 工作目录绝对路径（必须位于 WORKSPACE_DIR 下）。
        patterns: 产物 glob 列表（默认 *.pt/*.pth/*.ckpt/*.png/*.json/*.csv 等）。

    Returns:
        排序去重后的产物绝对路径列表（均限定在 WORKSPACE_DIR 下）。
    """
    work_path = _require_within_workspace(work_dir, label="work_dir")
    if not work_path.exists():
        return []

    glob_patterns = list(patterns) if patterns else list(_DEFAULT_ARTIFACT_PATTERNS)

    found: set[str] = set()
    workspace = WORKSPACE_DIR.resolve()
    for pat in glob_patterns:
        # 递归匹配（**），覆盖子目录产物。
        for path in work_path.rglob(pat):
            if not path.is_file():
                continue
            # 跳过 .venv / __pycache__ / .git 等目录下的文件。
            if any(part in _ARTIFACT_SKIP_DIRS for part in path.parts):
                continue
            resolved = path.resolve()
            # 双保险：产物必须仍限定在 workspace 下（防符号链接逃逸）。
            if resolved == workspace or resolved.is_relative_to(workspace):
                found.add(str(resolved))

    return sorted(found)
