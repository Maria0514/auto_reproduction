"""code_fs_tools.py -- coding 节点 ReAct agent 的代码文件读写工具集（S3-02）。

提供三个工具工厂（写 / 读 / 列目录），供 ``core/nodes/coding.py`` 的 ReAct
agent 在生成与修复代码时操作 ``code_output_dir`` / ``selected_repo.local_path``
下的文件。

严格复用 ``core/tools/git_tools.py`` 的两条安全范式：
    - ``_is_within_workspace``：``resolve() + is_relative_to(WORKSPACE_DIR.resolve())``
      路径越界校验，对所有写 / 读 / 列目标统一 ``resolve()`` 解开符号链接看真实落点，
      越界一律拒绝（防"符号链接 / .. 逃逸出 workspace"）。
    - ``_serialize_tool_result``：``json.dumps(ensure_ascii=False, sort_keys=True,
      default=str)``，沿袭 BUG-S1-02 治理（禁 ``str(dict)`` 否则下游
      ``react_base.extract_last_tool_result`` 的 ``json.loads`` 永久失败）。

工具治理（沿用 sp1/sp2 工具工厂范式）：
    - ``@tool`` 工厂 + ``try/except`` 兜底：工具内部任何异常（写权限错误、文件
      不存在、IO 错误、路径越界）一律转成错误描述字符串返回，绝不抛异常打断 ReAct 子图；
    - 纯文件操作基础设施，不依赖 LLM、不直接改 GlobalState。
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from langchain_core.tools import tool, BaseTool

from config import TOOL_RESULT_MAX_LENGTH, WORKSPACE_DIR

logger = logging.getLogger(__name__)


# 列目录条目上限（与 git_tools.analyze_local_repo 的 30 项扫描上限同量级，
# 防止超大目录把 ToolMessage 撑爆）。
_LIST_DIR_MAX_ITEMS = 200


# ---------------------------------------------------------------------------
# JSON 序列化合规 helper（BUG-S1-02 治理范式硬约束，与 git_tools 同源）
# ---------------------------------------------------------------------------

def _serialize_tool_result(result: object) -> str:
    """ReAct ToolMessage 序列化合规 helper（与 git_tools._serialize_tool_result 同源）。

    - ensure_ascii=False（中文不转义）
    - sort_keys=True（Prompt Cache 字节级幂等）
    - default=str（兜底未知类型，如 Path）

    禁止用 ``str(dict)``（Python repr 单引号会让下游 json.loads 永久失败）。
    """
    return json.dumps(result, ensure_ascii=False, sort_keys=True, default=str)


def _truncate(text: str) -> str:
    """将文本截断到 TOOL_RESULT_MAX_LENGTH 字符（与 deepxiv_tools._truncate 同源）。

    读文件内容可能很大，超长时截断并附固定标记（不含动态片段，Prompt Cache 友好）。
    """
    if len(text) <= TOOL_RESULT_MAX_LENGTH:
        return text
    return text[:TOOL_RESULT_MAX_LENGTH] + f"\n... [truncated at {TOOL_RESULT_MAX_LENGTH} chars]"


# ---------------------------------------------------------------------------
# 路径越界校验（复用 git_tools._is_within_workspace 范式）
# ---------------------------------------------------------------------------

def _is_within_workspace(target: Path) -> bool:
    """校验 target 解析后是否位于 WORKSPACE_DIR 之下（含等于自身）。

    与 git_tools._is_within_workspace 同一判定路径：resolve() 后比较真实包含关系。
    所有写 / 读 / 列目标路径均为文件系统副作用点，统一 resolve() 解符号链接看真实落点。
    """
    workspace = WORKSPACE_DIR.resolve()
    resolved = target.resolve()
    return resolved == workspace or resolved.is_relative_to(workspace)


# ---------------------------------------------------------------------------
# ReAct 工具工厂（供 coding 节点 ReAct agent 调用）
# ---------------------------------------------------------------------------

def make_write_code_file_tool() -> BaseTool:
    """工具工厂：写代码文件到 workspace（通常是 code_output_dir）下。

    成功时 ToolMessage 输出 ``{"success": true, "path": ..., "bytes_written": ...}``，
    路径越界 / 写失败时输出 ``{"success": false, "error": "..."}``。
    """

    @tool
    def write_code_file(path: str, content: str) -> str:
        """Write text content to a file under the workspace (e.g. code_output_dir).

        Creates parent directories as needed. The target path must resolve to a
        location inside the workspace directory; out-of-workspace paths are
        rejected. Returns a JSON object {"success": true, "path": ...,
        "bytes_written": ...} on success, or {"success": false, "error": "..."}
        on failure (never raises).

        Args:
            path: Absolute or workspace-relative file path to write.
            content: Full text content to write (overwrites existing file).
        """
        try:
            target = Path(path)
            if not _is_within_workspace(target):
                return _serialize_tool_result({
                    "success": False,
                    "error": f"路径越界：{path} 不在 WORKSPACE_DIR({WORKSPACE_DIR}) 之下",
                })
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            resolved = target.resolve()
            bytes_written = len(content.encode("utf-8"))
            logger.info("write_code_file: 写入 %s (%d bytes)", resolved, bytes_written)
            return _serialize_tool_result({
                "success": True,
                "path": str(resolved),
                "bytes_written": bytes_written,
            })
        except Exception as exc:  # noqa: BLE001 — 兜底，不打断 ReAct 子图
            logger.warning("write_code_file: 写入失败 path=%s: %s", path, exc)
            return _serialize_tool_result({"success": False, "error": str(exc)})

    return write_code_file  # type: ignore[return-value]


def make_read_code_file_tool() -> BaseTool:
    """工具工厂：读 workspace 下文件（code_output_dir / selected_repo.local_path）。

    成功返回文件内容字符串（超长截断）；路径越界 / 文件不存在 / IO 错误时返回错误
    描述字符串（不抛异常）。读文件语义返回纯文本内容，不强制 JSON 包裹。
    """

    @tool
    def read_code_file(path: str) -> str:
        """Read a text file under the workspace and return its content.

        Used to read existing code under code_output_dir or files under a cloned
        repository (selected_repo.local_path). The path must resolve inside the
        workspace; out-of-workspace paths are rejected. On success returns the
        file content as text (truncated if very long). On failure (out of
        workspace / file not found / IO error) returns an error description
        string instead of raising.

        Args:
            path: Absolute or workspace-relative file path to read.
        """
        try:
            target = Path(path)
            if not _is_within_workspace(target):
                return f"Error: 路径越界：{path} 不在 WORKSPACE_DIR({WORKSPACE_DIR}) 之下"
            if not target.exists():
                return f"Error: 文件不存在：{path}"
            if target.is_dir():
                return f"Error: 目标是目录而非文件：{path}（请用 list_dir 列目录）"
            content = target.read_text(encoding="utf-8", errors="replace")
            logger.info("read_code_file: 读取 %s (%d chars)", target.resolve(), len(content))
            return _truncate(content)
        except Exception as exc:  # noqa: BLE001 — 兜底，不打断 ReAct 子图
            logger.warning("read_code_file: 读取失败 path=%s: %s", path, exc)
            return f"Error: 读取文件失败 {path}: {exc}"

    return read_code_file  # type: ignore[return-value]


def make_list_dir_tool() -> BaseTool:
    """工具工厂：列目录（限定 workspace）。

    成功时 ToolMessage 输出
    ``{"success": true, "path": ..., "entries": [...], "truncated": bool}``，
    路径越界 / 不存在 / IO 错误时输出 ``{"success": false, "error": "..."}``。
    """

    @tool
    def list_dir(path: str) -> str:
        """List the entries of a directory under the workspace.

        The directory path must resolve inside the workspace; out-of-workspace
        paths are rejected. Returns a JSON object {"success": true, "path": ...,
        "entries": [...], "truncated": bool} on success (entries sorted, capped
        at a max count), or {"success": false, "error": "..."} on failure (never
        raises). Each entry is suffixed with "/" if it is a subdirectory.

        Args:
            path: Absolute or workspace-relative directory path to list.
        """
        try:
            target = Path(path)
            if not _is_within_workspace(target):
                return _serialize_tool_result({
                    "success": False,
                    "error": f"路径越界：{path} 不在 WORKSPACE_DIR({WORKSPACE_DIR}) 之下",
                })
            if not target.exists():
                return _serialize_tool_result({
                    "success": False,
                    "error": f"目录不存在：{path}",
                })
            if not target.is_dir():
                return _serialize_tool_result({
                    "success": False,
                    "error": f"目标不是目录：{path}",
                })
            names = sorted(os.listdir(target))
            truncated = len(names) > _LIST_DIR_MAX_ITEMS
            names = names[:_LIST_DIR_MAX_ITEMS]
            entries = [
                name + "/" if (target / name).is_dir() else name
                for name in names
            ]
            logger.info("list_dir: 列出 %s (%d 项)", target.resolve(), len(entries))
            return _serialize_tool_result({
                "success": True,
                "path": str(target.resolve()),
                "entries": entries,
                "truncated": truncated,
            })
        except Exception as exc:  # noqa: BLE001 — 兜底，不打断 ReAct 子图
            logger.warning("list_dir: 列目录失败 path=%s: %s", path, exc)
            return _serialize_tool_result({"success": False, "error": str(exc)})

    return list_dir  # type: ignore[return-value]
