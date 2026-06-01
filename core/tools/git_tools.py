"""git_tools.py -- 仓库克隆与本地仓库分析。

通过系统 git 命令实现浅克隆与基础指标分析，MVP 阶段不依赖 GitHub API。
所有工具工厂的 ToolMessage 输出严格使用 json.dumps(ensure_ascii=False,
sort_keys=True, default=str) 序列化，沿袭 BUG-S1-02 治理后的合规约束
（与 ``core/tools/deepxiv_tools.py::_serialize`` 同源）。

安全约束：
    - 所有 subprocess 调用使用列表形式，禁止 shell=True；
    - 副作用边界严格限定在 ``WORKSPACE_DIR`` 之下（dest_dir 越界拒绝）。
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Dict, List, Optional

from langchain_core.tools import tool, BaseTool

from config import (
    GIT_CLONE_DEPTH,
    GIT_CLONE_TIMEOUT,
    URL_REACHABLE_TIMEOUT,
    WORKSPACE_DIR,
    WORKSPACE_REPOS_DIR,
)
from core.errors import PermanentError, TransientError
from core.state import RepoInfo

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

# 网络瞬态错误关键字（小写匹配 git clone stderr）→ 触发指数退避重试。
_TRANSIENT_STDERR_KEYWORDS = (
    "connection refused",
    "connection timed out",
    "timed out",
    "could not resolve host",
    "rpc failed",
    "early eof",
    "network is unreachable",
    "temporary failure in name resolution",
    "failed to connect",
    "the remote end hung up",
)

# 永久错误关键字（小写匹配 git clone stderr）→ 不重试，直接 PermanentError。
_PERMANENT_STDERR_KEYWORDS = (
    "repository not found",
    "authentication failed",
    "permission denied",
    "could not read username",
    "access denied",
    "no space left on device",
    "disk quota exceeded",
)

# 网络瞬态重试退避序列（秒），技术架构 §12.4：1s / 2s / 4s，共 3 次重试。
_RETRY_BACKOFF_SECONDS = (1.0, 2.0, 4.0)

# README / 依赖声明文件检测清单。
_README_NAMES = ("README.md", "README.rst", "README.txt", "README")
_REQUIREMENTS_NAMES = (
    "requirements.txt",
    "environment.yml",
    "environment.yaml",
    "pyproject.toml",
    "setup.py",
)

# analyze_local_repo 顶层目录扫描上限。
_DIR_STRUCTURE_MAX_ITEMS = 30


# ---------------------------------------------------------------------------
# JSON 序列化合规 helper（BUG-S1-02 治理范式硬约束）
# ---------------------------------------------------------------------------

def _serialize_tool_result(result: object) -> str:
    """ReAct ToolMessage 序列化合规 helper（与 deepxiv_tools._serialize 同源）。

    沿袭 BUG-S1-02 治理：
    - ensure_ascii=False（中文不转义）
    - sort_keys=True（Prompt Cache 字节级幂等）
    - default=str（兜底未知类型，如 Path / datetime）

    禁止用 ``str(dict)``（Python repr 单引号会让下游 json.loads 永久失败）。
    """
    return json.dumps(result, ensure_ascii=False, sort_keys=True, default=str)


# ---------------------------------------------------------------------------
# 内部 helper
# ---------------------------------------------------------------------------

def _repo_slug(url: str) -> str:
    """从仓库 URL 提取 ``{owner}__{repo}`` 形式的 slug，作为本地目录名。

    规则：
        - 去掉末尾 ``.git`` 与斜杠；
        - 取最后两段路径（owner / repo），用 ``__`` 连接，规避目录嵌套；
        - 非法字符替换为 ``_``，保证落地路径安全。
    若无法解析出两段，退化为最后一段或经清洗的整串。
    """
    cleaned = url.strip().rstrip("/")
    if cleaned.endswith(".git"):
        cleaned = cleaned[: -len(".git")]
    # 去除协议头与 host，仅保留路径部分。
    # 兼容 https://github.com/owner/repo 与 git@github.com:owner/repo
    path_part = re.sub(r"^[a-zA-Z]+://", "", cleaned)
    path_part = re.sub(r"^[^/:]+[:/]", "", path_part) if (":" in path_part or "/" in path_part) else path_part
    segments = [s for s in re.split(r"[/:]", path_part) if s]
    if len(segments) >= 2:
        slug = f"{segments[-2]}__{segments[-1]}"
    elif segments:
        slug = segments[-1]
    else:
        slug = cleaned
    return re.sub(r"[^A-Za-z0-9._-]", "_", slug)


def _is_within_workspace(dest: Path) -> bool:
    """校验 dest 解析后是否位于 WORKSPACE_DIR 之下（含等于自身的情况由调用方拒绝）。

    与 A4 验收断言的不变量
    ``WORKSPACE_REPOS_DIR.resolve().is_relative_to(WORKSPACE_DIR.resolve())``
    保持同一判定路径（resolve 后比较真实包含关系）。
    """
    workspace = WORKSPACE_DIR.resolve()
    resolved = dest.resolve()
    return resolved == workspace or resolved.is_relative_to(workspace)


def _classify_clone_failure(stderr: str, returncode: int) -> Exception:
    """根据 git clone 的 stderr / returncode 分类为 Transient / Permanent。

    永久关键字优先（认证 / 仓库不存在 / 磁盘空间不足），其次瞬态网络关键字，
    都不命中时默认归类为 PermanentError（避免对未知错误盲目重试浪费时间）。
    """
    lowered = (stderr or "").lower()
    for kw in _PERMANENT_STDERR_KEYWORDS:
        if kw in lowered:
            return PermanentError(
                f"git clone 永久失败: {kw}",
                f"exit={returncode} stderr={stderr.strip()}",
            )
    for kw in _TRANSIENT_STDERR_KEYWORDS:
        if kw in lowered:
            return TransientError(
                f"git clone 网络瞬态失败: {kw}",
                f"exit={returncode} stderr={stderr.strip()}",
            )
    return PermanentError(
        "git clone 失败（未识别错误，按永久处理不重试）",
        f"exit={returncode} stderr={stderr.strip()}",
    )


def _run_git(args: List[str], *, cwd: Optional[str] = None, timeout: int) -> subprocess.CompletedProcess:
    """统一 subprocess.run 封装：列表形式、捕获文本输出、禁止 shell。

    git 二进制缺失（FileNotFoundError）直接转 PermanentError 提示安装 git。
    """
    cmd = ["git"] + args
    try:
        return subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise PermanentError(
            "git 二进制缺失，请先安装 git",
            str(exc),
        ) from exc


# ---------------------------------------------------------------------------
# 原子函数（业务层调用）
# ---------------------------------------------------------------------------

def git_clone(
    url: str,
    dest_dir: str,
    depth: int = GIT_CLONE_DEPTH,
    timeout: int = GIT_CLONE_TIMEOUT,
) -> Dict[str, object]:
    """通过 subprocess 调用 ``git clone --depth N`` 实现浅克隆。

    Args:
        url: 仓库 URL（https 或 ssh）。
        dest_dir: 目标克隆目录，必须位于 WORKSPACE_DIR 之下。
        depth: 浅克隆 depth，默认取 config.GIT_CLONE_DEPTH。
        timeout: 单次 git clone 子进程超时（秒），默认取 config.GIT_CLONE_TIMEOUT。

    Returns:
        {"success": bool, "local_path": str, "duration_seconds": float,
         "error": Optional[str]}

    Raises:
        TransientError: 网络瞬态错误，3 次指数退避重试后仍失败。
        PermanentError: dest_dir 越界 / 仓库不存在 / 认证失败 / 磁盘空间不足 /
                        git 二进制缺失。
    """
    dest_path = Path(dest_dir)
    # --- 安全约束：dest_dir 越界校验（不重试） ---
    if not _is_within_workspace(dest_path):
        raise PermanentError(
            "dest_dir 越界",
            f"dest_dir={dest_dir} 不在 WORKSPACE_DIR({WORKSPACE_DIR}) 之下",
        )

    # --- 同 URL 重复克隆：识别已有 local_path 跳过 ---
    slug = _repo_slug(url)
    existing = WORKSPACE_REPOS_DIR / slug
    if existing.exists():
        logger.info("git_clone: 检测到已存在仓库 %s，跳过克隆", existing)
        return {
            "success": True,
            "local_path": str(existing),
            "duration_seconds": 0.0,
            "error": None,
        }
    # dest_dir 自身已存在且非空时也跳过（幂等）。
    if dest_path.exists() and dest_path.is_dir() and any(dest_path.iterdir()):
        logger.info("git_clone: dest_dir %s 已存在且非空，跳过克隆", dest_path)
        return {
            "success": True,
            "local_path": str(dest_path.resolve()),
            "duration_seconds": 0.0,
            "error": None,
        }

    args = ["clone", "--depth", str(depth), url, str(dest_path)]
    last_transient: Optional[TransientError] = None
    # 首次尝试 + 3 次退避重试（共最多 4 次执行；退避序列长度即重试次数）。
    total_attempts = len(_RETRY_BACKOFF_SECONDS) + 1
    start = time.monotonic()
    for attempt in range(total_attempts):
        try:
            proc = _run_git(args, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            # 超时按网络瞬态处理。
            last_transient = TransientError(
                "git clone 超时",
                f"timeout={timeout}s url={url} attempt={attempt + 1}",
            )
            logger.warning("git_clone: 超时（attempt=%d）url=%s", attempt + 1, url)
        else:
            if proc.returncode == 0:
                duration = time.monotonic() - start
                logger.info(
                    "git_clone: 成功 url=%s -> %s (%.2fs)",
                    url, dest_path, duration,
                )
                return {
                    "success": True,
                    "local_path": str(dest_path.resolve()),
                    "duration_seconds": round(duration, 3),
                    "error": None,
                }
            # 非 0 退出：分类。
            err = _classify_clone_failure(proc.stderr, proc.returncode)
            if isinstance(err, PermanentError):
                logger.warning(
                    "git_clone: 永久失败 url=%s exit=%d: %s",
                    url, proc.returncode, err.message,
                )
                raise err
            last_transient = err  # TransientError，进入退避重试
            logger.warning(
                "git_clone: 瞬态失败（attempt=%d）url=%s: %s",
                attempt + 1, url, err.message,
            )

        # 还有退避机会才 sleep；最后一次失败后不再 sleep，落到循环外抛出。
        if attempt < len(_RETRY_BACKOFF_SECONDS):
            time.sleep(_RETRY_BACKOFF_SECONDS[attempt])

    # 全部重试耗尽，抛出最后一次瞬态错误。
    assert last_transient is not None  # 循环至少跑一次
    logger.error("git_clone: 重试 %d 次仍失败 url=%s", total_attempts, url)
    raise last_transient


def analyze_local_repo(local_path: str) -> RepoInfo:
    """对本地仓库做基础指标分析，返回 RepoInfo。

    步骤：
        1. ``git log --since="6 months ago" --pretty=format:%H`` 数 commit_count_recent
        2. ``git log -1 --format=%cI`` 取 last_commit_date（ISO 8601）
        3. 扫顶层目录（仅一级，最多 30 项，字典序）写 dir_structure
        4. README* 任一存在 -> has_readme
        5. requirements.txt / environment.yml / pyproject.toml / setup.py 任一 -> has_requirements
        6. is_official=False（由 resource_scout 节点回填）；local_path 写入参数值。

    git log 命令失败（非 git 仓库 / 空仓库）时对应字段置 None / 0，不抛异常，
    保证本地分析的健壮性（resource_scout 仍可基于其它指标评分）。
    """
    repo = Path(local_path)

    # --- commit_count_recent ---
    commit_count_recent: Optional[int] = None
    try:
        proc = _run_git(
            ["log", "--since=6 months ago", "--pretty=format:%H"],
            cwd=local_path,
            timeout=30,
        )
        if proc.returncode == 0:
            lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
            commit_count_recent = len(lines)
        else:
            logger.warning(
                "analyze_local_repo: git log --since 失败 path=%s stderr=%s",
                local_path, proc.stderr.strip(),
            )
    except (subprocess.TimeoutExpired, PermanentError) as exc:
        logger.warning("analyze_local_repo: commit_count_recent 提取异常: %s", exc)

    # --- last_commit_date ---
    last_commit_date: Optional[str] = None
    try:
        proc = _run_git(["log", "-1", "--format=%cI"], cwd=local_path, timeout=30)
        if proc.returncode == 0:
            value = proc.stdout.strip()
            last_commit_date = value or None
        else:
            logger.warning(
                "analyze_local_repo: git log -1 失败 path=%s stderr=%s",
                local_path, proc.stderr.strip(),
            )
    except (subprocess.TimeoutExpired, PermanentError) as exc:
        logger.warning("analyze_local_repo: last_commit_date 提取异常: %s", exc)

    # --- dir_structure（顶层一级，字典序，最多 30 项） ---
    dir_structure: Optional[List[str]] = None
    try:
        entries = sorted(os.listdir(local_path))
        dir_structure = entries[:_DIR_STRUCTURE_MAX_ITEMS]
    except OSError as exc:
        logger.warning("analyze_local_repo: 列目录失败 path=%s: %s", local_path, exc)

    # --- has_readme / has_requirements ---
    has_readme = any((repo / name).exists() for name in _README_NAMES)
    has_requirements = any((repo / name).exists() for name in _REQUIREMENTS_NAMES)

    repo_info: RepoInfo = {
        "url": "",
        "source": "git_clone",
        "is_official": False,
        "stars": None,
        "forks": None,
        "last_commit_date": last_commit_date,
        "commit_count_recent": commit_count_recent,
        "has_readme": has_readme,
        "has_requirements": has_requirements,
        "dir_structure": dir_structure,
        "quality_score": 0.0,
        "local_path": local_path,
    }
    logger.info(
        "analyze_local_repo: path=%s readme=%s req=%s commits=%s",
        local_path, has_readme, has_requirements, commit_count_recent,
    )
    return repo_info


def check_url_reachable(url: str, timeout: int = URL_REACHABLE_TIMEOUT) -> bool:
    """快速 HEAD 探测 URL 可达性，用于在 git_clone 前过滤死链。

    HTTP 200/301/302 返回 True；其它状态码 / 任意异常返回 False（不抛）。
    """
    import requests  # 延迟导入，避免在不需要时引入网络依赖。

    try:
        resp = requests.head(url, timeout=timeout, allow_redirects=True)
    except Exception as exc:  # noqa: BLE001 — 任意网络异常均视为不可达
        logger.info("check_url_reachable: 探测异常 url=%s: %s", url, exc)
        return False
    reachable = resp.status_code in (200, 301, 302)
    logger.info("check_url_reachable: url=%s status=%d -> %s", url, resp.status_code, reachable)
    return reachable


# ---------------------------------------------------------------------------
# ReAct 工具工厂（供 ReAct agent 调用）
# ---------------------------------------------------------------------------

def make_git_clone_and_analyze_tool() -> BaseTool:
    """复合工具工厂：git_clone + analyze_local_repo 一次完成。

    单次工具调用完成"克隆 + 本地分析"两步，避免 agent 多轮拆分浪费 max_rounds。
    成功时 ToolMessage 输出序列化的 RepoInfo（含 local_path），失败时输出
    ``{"success": False, "error": "..."}``。
    """

    @tool
    def git_clone_and_analyze(url: str) -> str:
        """Clone a git repository (shallow) into the workspace and analyze it.

        Performs `git clone --depth 1` then extracts basic repo metrics
        (recent commit count, last commit date, top-level directory structure,
        whether README / requirements files exist). Returns a JSON RepoInfo
        object including its local_path, or {"success": false, "error": "..."}
        on failure.

        Args:
            url: Git repository URL, e.g. "https://github.com/owner/repo".
        """
        try:
            slug = _repo_slug(url)
            dest = str(WORKSPACE_REPOS_DIR / slug)
            clone_result = git_clone(url, dest)
            local_path = str(clone_result["local_path"])
            repo_info = analyze_local_repo(local_path)
            repo_info["url"] = url
            return _serialize_tool_result(repo_info)
        except (TransientError, PermanentError) as exc:
            return _serialize_tool_result({"success": False, "error": str(exc)})
        except Exception as exc:  # noqa: BLE001 — 兜底，不打断 ReAct 子图
            return _serialize_tool_result({"success": False, "error": str(exc)})

    return git_clone_and_analyze  # type: ignore[return-value]


def make_check_url_reachable_tool() -> BaseTool:
    """工具工厂：check_url_reachable，用于死链过滤。"""

    @tool
    def check_url_reachable_tool(url: str) -> str:
        """Check whether a repository URL is reachable via an HTTP HEAD request.

        Returns a JSON object {"url": ..., "reachable": true/false}. Used to
        filter dead links before attempting an expensive git clone.

        Args:
            url: URL to probe, e.g. "https://github.com/owner/repo".
        """
        try:
            reachable = check_url_reachable(url)
            return _serialize_tool_result({"url": url, "reachable": reachable})
        except Exception as exc:  # noqa: BLE001 — 兜底，不打断 ReAct 子图
            return _serialize_tool_result({"url": url, "reachable": False, "error": str(exc)})

    return check_url_reachable_tool  # type: ignore[return-value]


def make_git_clone_tool() -> BaseTool:
    """工具工厂：单独的 git_clone（高级 agent 路径，不做本地分析）。"""

    @tool
    def git_clone_tool(url: str) -> str:
        """Clone a git repository (shallow, depth 1) into the workspace.

        Returns a JSON object {"success": bool, "local_path": str,
        "duration_seconds": float, "error": Optional[str]}. Does NOT analyze
        the repository (use git_clone_and_analyze for clone + analysis).

        Args:
            url: Git repository URL, e.g. "https://github.com/owner/repo".
        """
        try:
            slug = _repo_slug(url)
            dest = str(WORKSPACE_REPOS_DIR / slug)
            result = git_clone(url, dest)
            return _serialize_tool_result(result)
        except (TransientError, PermanentError) as exc:
            return _serialize_tool_result(
                {"success": False, "local_path": "", "duration_seconds": 0.0, "error": str(exc)}
            )
        except Exception as exc:  # noqa: BLE001 — 兜底，不打断 ReAct 子图
            return _serialize_tool_result(
                {"success": False, "local_path": "", "duration_seconds": 0.0, "error": str(exc)}
            )

    return git_clone_tool  # type: ignore[return-value]
