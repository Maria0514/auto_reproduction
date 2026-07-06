"""SqliteSaver checkpoint 管理。

提供 get_checkpointer 工厂函数，创建配置了 WAL 模式的 SqliteSaver 实例，
用于 LangGraph 主图的状态持久化。
"""

from __future__ import annotations

import logging
import os
import sqlite3
from pathlib import Path
from typing import Optional

from langgraph.checkpoint.sqlite import SqliteSaver

import config
from core.errors import PermanentError

logger = logging.getLogger(__name__)


def get_checkpointer(db_path: Optional[str] = None) -> SqliteSaver:
    """创建并返回配置好 WAL 模式的 SqliteSaver 实例。

    Args:
        db_path: SQLite 数据库文件路径。为 None 时使用 config.CHECKPOINT_DB_PATH。

    Returns:
        配置好的 SqliteSaver 实例。

    Raises:
        PermanentError: db_path 指向一个已存在的目录而非文件。
    """
    if db_path is None:
        db_path = str(config.CHECKPOINT_DB_PATH)

    db_file = Path(db_path)

    db_file.parent.mkdir(parents=True, exist_ok=True)

    if db_file.exists() and not db_file.is_file():
        raise PermanentError(
            f"Checkpoint 路径不是常规文件: {db_path}",
            detail=f"路径 '{db_path}' 已存在但不是常规文件（可能是目录）",
        )

    conn = sqlite3.connect(db_path, check_same_thread=False)
    # ADJ-S4-G2-02 裁决 (a+)：checkpoint 帧含敏感 resume 值明文（已知接受限制），
    # DB 权限收敛 0600 与 .secrets 对齐（威胁模型唯一真实暴露面增量 = 同机其他
    # OS 用户可读默认 0644 建库）。置于 WAL PRAGMA 前：-wal/-shm 创建时继承主库
    # 权限，无需单独处理。POSIX 强制；非 POSIX 打 WARNING 不强制（沿
    # secrets_store._write_entries 范式）。
    os.chmod(db_path, 0o600)
    if os.name != "posix":
        logger.warning(
            "非 POSIX 平台无法强制 checkpoint DB 0600 权限（MVP 不强制）: path=%s",
            db_path,
        )
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")

    return SqliteSaver(conn)
