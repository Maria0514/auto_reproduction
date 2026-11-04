"""`core.shell_tokens` 的跨平台分词契约（A 类修复的回归门，2026-10-21）。

背景见 `docs/solutions/2026-10-21_windows-command-tokenization.md`：
产品全程「禁 shell、argv 直跑」，而"把命令串切成 argv"这一层原来用 `shlex.split`
（**POSIX 语义**，反斜杠是转义符）⇒ Windows 路径被吃掉，子进程 `WinError 2`，
报错与病因看不出关系。

本文件钉三件事：
1. **路径原样保留**（Windows / UNC / 中文 / `=连路径` / 带空格引号）；
2. **既有安全语义一格不少**（引号内元字符不拒、裸元字符仍拒、`&&`/`;` 连接符、
   未闭合引号降级）—— 修 bug 不许顺手削掉防线；
3. **实现里没有按平台分支**（静态断言，用 AST 判 NAME，不用子串 —— 见 `MEMORY` §六：
   留痕注释会让子串断言自己打红）。
"""

from __future__ import annotations

import ast
import inspect
import sys

import pytest

import core.shell_tokens as shell_tokens

execution_module = None  # 见 _execution_module()：用 importlib 取，避免 __init__ 遮蔽


def _execution_module():
    """取真实子模块（`core/nodes/__init__.py` 的重导出会遮蔽子模块，坑 6）。"""
    import importlib

    return importlib.import_module("core.nodes.execution")


def _plan_checks():
    import importlib

    return importlib.import_module("core.plan_checks")


# ===========================================================================
# ① 路径原样保留（本 bug 的正面）
# ===========================================================================


@pytest.mark.parametrize(
    "command, expected",
    [
        pytest.param(
            r"D:\study\venv\Scripts\python.exe -c" + ' "print(1)"',
            [r"D:\study\venv\Scripts\python.exe", "-c", "print(1)"],
            id="win_abs_path",
        ),
        pytest.param(
            r"D:\数据\code\train.py --epochs 3",
            [r"D:\数据\code\train.py", "--epochs", "3"],
            id="win_path_with_cjk",
        ),
        pytest.param(
            r"python prep.py --data D:\corpus\v1 --out out\r.json",
            ["python", "prep.py", "--data", r"D:\corpus\v1", "--out", r"out\r.json"],
            id="paths_in_args",
        ),
        pytest.param(
            r"python x.py --cfg=C:\cfg\a.yaml",
            ["python", "x.py", r"--cfg=C:\cfg\a.yaml"],
            id="equals_joined_path",
        ),
        pytest.param(
            "python " + r'"\\server\share\run.py"',
            ["python", r"\\server\share\run.py"],
            id="unc_path_quoted",
        ),
        pytest.param(
            'python "C:\\Program Files\\x.py"',
            ["python", r"C:\Program Files\x.py"],
            id="quoted_path_with_space",
        ),
    ],
)
def test_windows_paths_survive_tokenization(command, expected):
    """反斜杠是**普通字符**：路径逐字保留（旧实现会吃成 `D:studypython.exe`）。"""
    assert shell_tokens.split_argv(command) == expected


def test_sys_executable_survives_tokenization():
    """回归那个真实现场：`f'{sys.executable} -c "print(1)"'` 的第一个 token。

    Windows 上 `sys.executable` 是 `D:\\...\\.venv\\Scripts\\python.exe`；
    旧实现切成 `D:...venvScriptspython.exe` ⇒ `Popen` `WinError 2`。
    """
    argv = shell_tokens.split_argv(f'{sys.executable} -c "print(1)"')
    assert argv[0] == sys.executable
    assert argv[1:] == ["-c", "print(1)"]


def test_backslash_is_not_an_escape_character():
    """契约明写：`\\ ` **不是**转义空格；要含空格请用引号。

    这条不是"缺陷"而是**为了跨平台一致**的取舍（见方案文档 §4）：
    若让反斜杠兼作转义符，同一条命令在 Windows 与 POSIX 上会切出不同 argv，
    "同一条计划换台机器还能跑出同一件事"当场不成立。
    """
    assert shell_tokens.split_argv(r"python my\ file.py") == ["python", "my\\", "file.py"]
    # 正确写法（引号）——两种平台一致
    assert shell_tokens.split_argv('python "my file.py"') == ["python", "my file.py"]


# ===========================================================================
# ② 既有安全语义一格不少
# ===========================================================================


@pytest.mark.parametrize(
    "command",
    [
        "python train.py > out.log",
        "python train.py >> out.log",
        "python a.py 2>&1",
        "python a.py | grep x",
        "python a.py &",
        "python a.py < in.txt",
    ],
)
def test_bare_metachars_still_rejected(command):
    """裸元字符（独立 token）仍被 `has_unsupported_shell_syntax` 拒 —— 防线不动。"""
    assert _plan_checks().has_unsupported_shell_syntax(command) is True


@pytest.mark.parametrize(
    "command",
    [
        'python -c "print(1>2)"',
        "python -c 'import sys; print(sys.version)'",
        "python run.py --tag '#'",
        'python run.py --json "{\\"a\\": 1}"',
    ],
)
def test_quoted_metachars_not_rejected(command):
    """引号内的一切元字符都是字面量：**不许误拒**（误伤 = 挡住合法复现）。"""
    assert _plan_checks().has_unsupported_shell_syntax(command) is False


def test_quoted_semicolon_is_not_a_connector():
    """引号内的 `;` 不拆子命令。"""
    subs = shell_tokens.split_top_level('python -c "a; b"')
    assert subs == [(["python", "-c", "a; b"], "")]


@pytest.mark.parametrize(
    "command, expected",
    [
        pytest.param(
            "python a.py && python b.py",
            [(["python", "a.py"], ""), (["python", "b.py"], "&&")],
            id="and",
        ),
        pytest.param(
            "python a.py ; python b.py",
            [(["python", "a.py"], ""), (["python", "b.py"], ";")],
            id="semicolon",
        ),
        pytest.param(
            "python a.py && python b.py ; python c.py",
            [(["python", "a.py"], ""), (["python", "b.py"], "&&"),
             (["python", "c.py"], ";")],
            id="mixed",
        ),
        pytest.param("python a.py", [(["python", "a.py"], "")], id="single"),
        pytest.param("", [], id="empty"),
        pytest.param("   ", [], id="whitespace"),
    ],
)
def test_top_level_connectors(command, expected):
    """`&&` / `;` 的连接符归属逐格钉死（`_split_top_level` 的返回契约）。"""
    assert shell_tokens.split_top_level(command) == expected


def test_unclosed_quote_raises_then_degrades():
    """未闭合引号：`split_argv` 抛 `ValueError`；`split_top_level` 降级为单条 whitespace split。

    🔴 降级**不是**为了容错好看：把它改成抛异常会让"引号写错"从"命令报错"升级成
    "节点炸掉"，那是既有两个实现的历史契约，不许改。
    """
    with pytest.raises(ValueError):
        shell_tokens.split_argv('python "abc')
    assert shell_tokens.split_top_level('python "abc') == [(["python", '"abc'], "")]


def test_execution_and_plan_checks_delegate_to_same_semantics():
    """两个历史实现（execution / plan_checks）都走同一套语义，不再各自一份。"""
    cmd = r"python D:\x\train.py && python D:\x\eval.py"
    exec_subs = _execution_module()._split_top_level(cmd)
    plan_subs = _plan_checks()._split_top_level_argv(cmd)
    assert [argv for argv, _ in exec_subs] == plan_subs
    assert plan_subs == [[r"python", r"D:\x\train.py"], [r"python", r"D:\x\eval.py"]]


# ===========================================================================
# ③ 实现里不许有平台分支（静态断言，判 NAME 不判子串）
# ===========================================================================


def test_implementation_has_no_platform_branch():
    """🔴 把"跨平台一致"从一句承诺变成一条会红的断言。

    判法是 **AST 上的 NAME/Attribute**，不是子串 —— 模块 docstring 里就写着
    "禁止引入按平台分支（`os.name` / `platform`）"，子串断言会被这句话自己打红
    （`MEMORY` §六 那条"留痕会把自己打红"）。
    """
    tree = ast.parse(inspect.getsource(shell_tokens))
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            assert not (node.value.id == "os" and node.attr == "name"), (
                "core.shell_tokens 不得按 os.name 分支：那会让同一条命令在 Windows / "
                "POSIX 上切出不同 argv（跨平台可重放的前提）")
            assert not (node.value.id == "sys" and node.attr == "platform"), (
                "core.shell_tokens 不得按 sys.platform 分支（同上）")
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            mods = {a.name for a in node.names}
            mods.add(getattr(node, "module", None) or "")
            assert "platform" not in mods, (
                "core.shell_tokens 不得 import platform（同上）")
