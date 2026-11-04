"""跨平台命令串分词单点（A 类修复，2026-10-21）。

## 为什么要这个模块

全部命令通道都是「**禁 shell、argv 直跑**」，但"把命令串切成 argv"这一层曾被复制成
**5 份** ``shlex.split(...)``（``run_command_tool`` / ``execution._split_top_level`` /
``plan_checks._split_top_level_argv`` / ``env_probe_tool`` ×2）。而 ``shlex.split`` 是
**POSIX 语义**：它把反斜杠当转义符 ⇒ Windows 路径 ``D:\\x\\train.py`` 被切成
``D:xtrain.py``，子进程报 ``WinError 2``，**报错与病因（路径被吃掉）看不出关系**。

本模块是那条语义的唯一实现，且**跨平台解析结果一致**（"复现可对账"的前提）。
做法：保留 POSIX 的**引号**规则，但**反斜杠是普通字符**（``lex.escape = ""``）。

## 契约（三条，写死；改动前先读）

1. **反斜杠不做转义**：Windows 路径、UNC、``--cfg=C:\\x\\a.yaml`` 一律原样保留。
   需要含空格的参数请用**引号**（``python "C:\\Program Files\\x.py"``），不要用 ``\\ ``。
2. **引号内的一切元字符都是字面量**：``python -c "print(1>2)"`` 切成 3 个 token，
   且不会被 ``has_unsupported_shell_syntax`` 误拒（那是 ``>`` 作为**独立 token** 才拒）。
3. **顶层 ``&&`` / ``;`` 是连接符**（由 :func:`split_top_level` 消费掉，不进 argv）；
   其余 shell 语法（管道 / 重定向 / 后台 / 变量展开）本产品不支持，也不在此实现。

🔴 **禁止在此引入按平台分支**（``os.name`` / ``platform``）。一旦分支，同一条命令会在
Windows 与 POSIX 上切出不同 argv，"同一条计划换台机器还能跑出同一件事"当场不成立
（``tests/test_sprint8_shell_tokens.py`` 有一条静态断言钉住这点）。
"""

from __future__ import annotations

import shlex
from typing import List, Tuple

__all__ = ["split_argv", "split_top_level"]


def _new_lexer(command: str) -> "shlex.shlex":
    """构造唯一的词法器：POSIX 引号规则 + **反斜杠不转义**。

    逐项都有理由，删任意一行都会坏掉一件事：

    - ``whitespace_split = True``：只按空白切词（``shlex.split`` 的主体行为），
      否则 ``shlex`` 会把 ``(`` ``)`` 等字符也当分隔；
    - ``commenters = ""``：**不把 ``#`` 当注释** —— 命令里的 ``#`` 可能是参数
      （``--tag '#1'``），当注释会把后半条命令整段吞掉；
    - ``escape = ""``：🔴 本模块存在的全部理由。反斜杠是普通字符 ⇒ 路径不被吃。
    """
    lex = shlex.shlex(command, posix=True)
    lex.whitespace_split = True
    lex.commenters = ""
    lex.escape = ""
    return lex


def split_argv(command: str) -> List[str]:
    """把一条命令串切成 argv（**不经 shell**，只做分词与去引号）。

    Args:
        command: 单条命令字符串。

    Returns:
        token 列表；空串 / 纯空白返回 ``[]``。

    Raises:
        ValueError: 引号未闭合等词法错误（调用方按各自契约转结构化错误，
            见 ``run_command_tool`` / ``env_probe_tool``）。
    """
    if not isinstance(command, str) or not command.strip():
        return []
    return list(_new_lexer(command))


def split_top_level(command: str) -> List[Tuple[List[str], str]]:
    """按**顶层** ``&&`` / ``;`` 把命令串拆成多条子命令。

    Returns:
        ``[(argv, connector), ...]``，``connector`` 是该子命令**之前**的连接符：
        第一条恒为 ``""``，其后为 ``"&&"``（前置非 0 短路）或 ``";"``（无条件顺序）。

    解析失败（未闭合引号等）**退化为整条 whitespace split 单子命令**，交下游自然报错
    —— 与既有两个实现（``execution._split_top_level`` / ``plan_checks._split_top_level_argv``）
    的历史行为逐字相同，**不要把这里改成抛异常**：那会让"引号写错"从"命令报错"升级成
    "节点炸掉"。
    """
    if not isinstance(command, str):
        return []
    try:
        tokens = split_argv(command)
    except ValueError:
        toks = command.split()
        return [(toks, "")] if toks else []

    subcommands: List[Tuple[List[str], str]] = []
    current: List[str] = []
    connector = ""  # 当前累积子命令前的连接符
    for tok in tokens:
        if tok == "&&" or tok == ";":
            if current:
                subcommands.append((current, connector))
                current = []
            connector = tok
            continue
        current.append(tok)
    if current:
        subcommands.append((current, connector))
    return subcommands
