"""secrets_store.py -- `.secrets` 读写 + 脱敏 + 凭证 env 组装（S4-07，安全关键）。

设计权威：docs/sprint4/dev-plan.md §4 任务 A3 + architecture §2.3 / §6（Q-E1）/ §9.3-9.4。

职责（极简六接口，不多不少，Maria 反过度工程硬约束）：
    - ``lookup_secret``：只读 `.secrets` 命中查询（去重 + 跨任务复用）；
    - ``remember_secret``：「记住」落盘（0600 明文 JSON，POSIX 强制权限）；
    - ``load_all_secrets``：编排层启动时读入内存（供 extra_env 注入 + mask）；
    - ``register_sensitive_value`` / ``iter_sensitive_values``：进程内
      sensitive-values set（本次会话未「记住」的敏感值也必须可被 mask，§9.4）；
    - ``mask_value``：把 text 中一切已知敏感值替换为 ``****``（长值优先防子串残留）；
    - ``build_credential_env``：凭证 → 子进程 env 组装（coding run_command 与
      execution sandbox 工具共用，避免两处各写一份映射）。

存储形态（architecture §6.2）：
    - 路径 = ``Path(workspace_dir) / config.SECRETS_FILE_NAME``；workspace_dir
      入参优先，回退 ``config.WORKSPACE_DIR``（运行期动态读取，便于测试受控替换）；
    - JSON 明文 ``{purpose_key: {"value": str, "is_sensitive": bool}}``；
    - 权限 0600：``os.open(O_CREAT|O_WRONLY|O_TRUNC, 0o600)`` + ``os.chmod(0o600)``；
      POSIX 强制，非 POSIX（Windows）打 WARNING 不强制；
    - MVP 不加密 / 不过期 / 不轮换（PRD 非目标）。

安全纪律（AC-S4-09/11/12 地基）：
    - 本模块所有 logger 输出只打 purpose_key / 路径，**绝不打 value**；
    - `.secrets` 依赖 `.gitignore` 的 ``workspace/`` 规则覆盖（不新增 gitignore 行，
      CP-A3-2 用 ``git check-ignore`` 断言）；
    - git token 经 GIT_ASKPASS 脚本（0700）注入，不进命令行 / URL / env 值
      （architecture §9.3 推荐方案）。
"""

from __future__ import annotations

import json
import logging
import os
import shlex
from pathlib import Path
from typing import Dict, Iterator, Optional, Set, Union

import config

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

# 脱敏占位符（architecture §9.4）。
_MASK_PLACEHOLDER = "****"

# git 凭证 purpose_key 前缀（architecture §9.3：`git_credential:<host>`）。
_GIT_CREDENTIAL_PREFIX = "git_credential:"

# HuggingFace token purpose_key → 双 env var（architecture §9.3 映射表）。
_HF_TOKEN_PURPOSE_KEY = "hf_token"

# GIT_ASKPASS 脚本文件名模板（落 workspace 下，0700）。
_GIT_ASKPASS_SCRIPT_TEMPLATE = ".git_askpass_{host}.sh"

# 进程内 sensitive-values set（模块级，值级去重；本会话未「记住」的敏感值
# 也必须可被 mask_value 覆盖，architecture §9.4 内存旁路）。
_SENSITIVE_VALUES: Set[str] = set()


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------

def _secrets_path(workspace_dir: Optional[Union[str, Path]] = None) -> Path:
    """secrets 文件路径：workspace_dir 入参优先，回退 config.WORKSPACE_DIR。

    动态读取 ``config.WORKSPACE_DIR``（不在 import 期快照），保证测试可用
    monkeypatch 受控替换、不污染真实 workspace。
    """
    base = Path(workspace_dir) if workspace_dir is not None else Path(config.WORKSPACE_DIR)
    return base / config.SECRETS_FILE_NAME


def _read_entries(
    workspace_dir: Optional[Union[str, Path]] = None,
    *,
    warn_missing: bool = False,
) -> Optional[Dict[str, Dict]]:
    """读 `.secrets` 全量条目。

    返回语义：
        - 正常 → ``{purpose_key: {"value": ..., "is_sensitive": ...}}``；
        - 文件缺失 → None（warn_missing=True 时打 WARNING，lookup 路径要求非静默；
          mask/load 高频路径 warn_missing=False 避免噪声）；
        - JSON 损坏 / 结构非 dict / 读取异常 → None + WARNING（一律非静默）。

    日志纪律：只打路径，绝不打文件内容（可能含敏感值）。
    """
    path = _secrets_path(workspace_dir)
    if not path.exists():
        if warn_missing:
            logger.warning("secrets 文件不存在（尚未记住任何凭证）: path=%s", path)
        return None
    try:
        raw = path.read_text(encoding="utf-8")
        entries = json.loads(raw)
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
        logger.warning(
            "secrets 文件读取/解析失败（按无凭证处理，非静默）: path=%s, error=%s",
            path, type(exc).__name__,
        )
        return None
    if not isinstance(entries, dict):
        logger.warning(
            "secrets 文件结构异常（顶层非 dict，按无凭证处理）: path=%s", path,
        )
        return None
    return entries


def _write_entries(entries: Dict[str, Dict], workspace_dir: Optional[Union[str, Path]]) -> None:
    """0600 权限落盘：os.open(O_CREAT|O_WRONLY|O_TRUNC, 0o600) + os.chmod(0o600)。

    POSIX 强制；非 POSIX 平台（Windows）0600 语义不可保证，打 WARNING 不强制。
    """
    path = _secrets_path(workspace_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(entries, ensure_ascii=False, sort_keys=True)
    fd = os.open(str(path), os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
    except Exception:
        # os.fdopen 成功后由 with 负责关闭；失败时兜底关 fd 防泄漏。
        try:
            os.close(fd)
        except OSError:
            pass
        raise
    os.chmod(path, 0o600)
    if os.name != "posix":
        logger.warning(
            "非 POSIX 平台无法强制 secrets 文件 0600 权限（MVP 不强制）: path=%s", path,
        )


# ---------------------------------------------------------------------------
# 接口 1/2/3：.secrets 读写
# ---------------------------------------------------------------------------

def lookup_secret(
    purpose_key: str,
    workspace_dir: Optional[Union[str, Path]] = None,
) -> Optional[str]:
    """只读 `.secrets` 命中查询（去重 + 跨任务复用）。

    文件缺失 / JSON 损坏 → 返回 None 并打 WARNING（非静默）；绝不创建文件。
    """
    entries = _read_entries(workspace_dir, warn_missing=True)
    if entries is None:
        return None
    entry = entries.get(purpose_key)
    if entry is None:
        return None
    if not isinstance(entry, dict) or "value" not in entry:
        logger.warning(
            "secrets 条目结构异常（缺 value，按未命中处理）: purpose_key=%s", purpose_key,
        )
        return None
    return entry["value"]


def remember_secret(
    purpose_key: str,
    value: str,
    is_sensitive: bool,
    workspace_dir: Optional[Union[str, Path]] = None,
) -> None:
    """「记住」落盘：JSON 结构 ``{purpose_key: {"value", "is_sensitive"}}``，0600。

    既有条目合并保留（同 purpose_key 覆盖）；既有文件损坏时以空表重建 + WARNING。
    日志只打 purpose_key，绝不打 value。
    """
    entries = _read_entries(workspace_dir, warn_missing=False)
    if entries is None:
        entries = {}
    entries[purpose_key] = {"value": value, "is_sensitive": bool(is_sensitive)}
    _write_entries(entries, workspace_dir)
    logger.info(
        "remember_secret: 已落盘 purpose_key=%s (is_sensitive=%s)",
        purpose_key, bool(is_sensitive),
    )


def load_all_secrets(
    workspace_dir: Optional[Union[str, Path]] = None,
) -> Dict[str, str]:
    """编排层启动时读入内存（供 extra_env 注入 + mask）；只返回 value 映射。

    文件缺失（尚未收集任何凭证，正常状态）→ 空 dict；损坏 → 空 dict + WARNING
    （由 ``_read_entries`` 打，非静默）。
    """
    entries = _read_entries(workspace_dir, warn_missing=False)
    if entries is None:
        return {}
    result: Dict[str, str] = {}
    for purpose_key, entry in entries.items():
        if not isinstance(entry, dict) or "value" not in entry:
            logger.warning(
                "secrets 条目结构异常（缺 value，已跳过）: purpose_key=%s", purpose_key,
            )
            continue
        result[purpose_key] = entry["value"]
    return result


# ---------------------------------------------------------------------------
# 接口 4：进程内 sensitive-values set
# ---------------------------------------------------------------------------

def register_sensitive_value(value: Optional[str]) -> None:
    """登记进程内敏感值（值级去重；空串 / None 不注册）。

    用途：本次会话未「记住」的敏感值也必须可被 ``mask_value`` 覆盖（§9.4 内存旁路）。
    """
    if not value:
        return
    _SENSITIVE_VALUES.add(value)


def iter_sensitive_values() -> Iterator[str]:
    """迭代进程内已登记的敏感值（快照迭代器，迭代期间注册新值不受影响）。"""
    return iter(tuple(_SENSITIVE_VALUES))


# ---------------------------------------------------------------------------
# 接口 5：脱敏 filter（AC-S4-11/12 统一落点）
# ---------------------------------------------------------------------------

def mask_value(text: Optional[str]) -> Optional[str]:
    """把 text 中出现的一切已知敏感值替换为 ``****``。

    敏感值全集 = `.secrets` 中 ``is_sensitive=True`` 项 ∪ 进程内 sensitive set。
    长值优先替换（防子串截断残留：短值先替换会把长值切成 ``****`` + 明文尾巴）。
    text 为 None / 空串返回原值。非敏感项（is_sensitive=False）不 mask。
    """
    if not text:
        return text
    known: Set[str] = set(iter_sensitive_values())
    entries = _read_entries(warn_missing=False)
    if entries:
        for entry in entries.values():
            if (
                isinstance(entry, dict)
                and entry.get("is_sensitive")
                and entry.get("value")
            ):
                known.add(entry["value"])
    if not known:
        return text
    masked = text
    for secret in sorted(known, key=len, reverse=True):
        masked = masked.replace(secret, _MASK_PLACEHOLDER)
    return masked


# ---------------------------------------------------------------------------
# 接口 6：凭证 → 子进程 env 组装（architecture §9.3）
# ---------------------------------------------------------------------------

def _write_git_askpass_script(host: str, token: str) -> Path:
    """生成 GIT_ASKPASS 脚本（0700，落 workspace 下）。

    内容仅 ``echo <token>``（shlex.quote 防注入）；git 对 username / password
    两类 prompt 均以 token 应答（GitHub PAT 场景两者一致即可认证）。
    token 不进命令行 / URL / env 值，只存在于 0700 脚本文件内（§9.3 推荐）。
    """
    safe_host = "".join(ch if (ch.isalnum() or ch in "._-") else "_" for ch in host)
    base = Path(config.WORKSPACE_DIR)
    base.mkdir(parents=True, exist_ok=True)
    path = base / _GIT_ASKPASS_SCRIPT_TEMPLATE.format(host=safe_host)
    content = "#!/bin/sh\necho {token}\n".format(token=shlex.quote(token))
    fd = os.open(str(path), os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o700)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        raise
    os.chmod(path, 0o700)
    if os.name != "posix":
        logger.warning(
            "非 POSIX 平台无法强制 GIT_ASKPASS 脚本 0700 权限: path=%s", path,
        )
    return path


def build_credential_env(secrets: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """凭证 → 子进程 env 组装（coding run_command 与 execution sandbox 工具共用）。

    - **无条件含 ``GIT_TERMINAL_PROMPT=0``**（R-S4-08：即便无凭证也带，让 git
      认证失败立即返回而非挂起等 stdin）；
    - ``git_credential:<host>`` → 生成 GIT_ASKPASS 脚本（0700、workspace 下、
      内容仅 echo token）→ env["GIT_ASKPASS"]=脚本路径（token 不进 env 值）；
      多 host 时 GIT_ASKPASS 单值限制，取排序首个 + WARNING（MVP 单 host 场景）；
    - ``hf_token`` → ``HF_TOKEN`` + ``HUGGING_FACE_HUB_TOKEN`` 双变量；
    - 未知 purpose_key 忽略（agent 可经 lookup_secret 自取，无对应 env 映射）；
    - secrets=None → ``load_all_secrets()``。
    """
    if secrets is None:
        secrets = load_all_secrets()
    env: Dict[str, str] = {"GIT_TERMINAL_PROMPT": "0"}

    git_keys = sorted(
        key for key in secrets
        if key.startswith(_GIT_CREDENTIAL_PREFIX) and secrets[key]
    )
    if len(git_keys) > 1:
        logger.warning(
            "存在多个 git 凭证但 GIT_ASKPASS 仅支持单脚本，取排序首个: keys=%s",
            git_keys,
        )
    if git_keys:
        chosen = git_keys[0]
        host = chosen[len(_GIT_CREDENTIAL_PREFIX):] or "default"
        script_path = _write_git_askpass_script(host, secrets[chosen])
        env["GIT_ASKPASS"] = str(script_path)
        logger.info(
            "build_credential_env: 已生成 GIT_ASKPASS 脚本 purpose_key=%s", chosen,
        )

    hf_token = secrets.get(_HF_TOKEN_PURPOSE_KEY)
    if hf_token:
        env["HF_TOKEN"] = hf_token
        env["HUGGING_FACE_HUB_TOKEN"] = hf_token
        logger.info("build_credential_env: 已注入 hf_token 双变量")

    return env
