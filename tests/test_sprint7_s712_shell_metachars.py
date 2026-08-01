"""S7-12 补测：沙箱不认的 shell 元字符 —— 把「悄悄不生效」改成「明说不支持」。

权威规格：`docs/sprint7/dev-plan.md` §57~§59（批次 8 / 任务 T-S7-8-1 / CP-8.x-y）。
本文件专门兑现 **CP-8.1-11**（开发批次未做、Maria 指定交测试工程师补测）。

被测缺陷（dev-plan §57.4 事实 1/2 已实跑坐实）：项目全局禁 shell=True，
`execution._split_top_level` 这层迷你 shell 解析**只认 `&&` 和 `;`**，其余元字符
（`>` `>>` `|` `2>&1` …）被静默当普通 argv token 传给程序 ⇒
`subprocess.run(['echo','hi','>','f.txt'])` 拿到 **returncode=0**、stdout 原样打印
`hi > f.txt`、而 `f.txt` **根本没被创建**。危害是可靠性三连：
①假 exit 0 污染 `exit_ok`（execution.py:1882-1883）；②错误信号错位毒害修复循环；
③返回 0 是正反馈，agent 下次还这么写。

三层覆盖（**行为断言，不做源码子串检查** —— F2 教训）：
  A. 谓词层 `core.plan_checks.has_unsupported_shell_syntax`（CP-8.1-1 / CP-8.1-2）
  B. 消费点 A `core.nodes.execution::run_in_sandbox`（CP-8.1-3 / 5 / 6）**最关键**
  C. 消费点 B `core.tools.run_command_tool::run_command`（CP-8.1-4 / 5 / 6）

⚠ B 组的核心不是"返回了错误"，而是 **被拒命令不进 `collector.run_results` /
  `collector.step_ledger`、`_run_step_subcommands` 零调用** —— 早退位置放错，这条
  防线会自己制造假绿（污染 `exit_ok`、被步骤对账当成"完成"，即 R-S7-49 那类）。
  故台账断言直接断"条数不变"，而不是只断返回值。

⚠ 已知缺口一律以 `xfail(strict=True)` 在测试层显形（见文件末尾 F 组），
  **不假装已覆盖**；strict 意味着日后谁把缺口补上了，xpass 会当场变红，
  逼他回来同步 dev-plan §58 第 1 条与 §59 的登记。

⚠ 访问模块级私有常量一律走 `importlib.import_module`（已知 bug 模式 #6：
  `core/nodes/__init__.py` 的显式 export 会遮蔽子模块属性）。
"""

from __future__ import annotations

import importlib
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import config  # noqa: E402
from core import secrets_store  # noqa: E402
from core.tools import run_command_tool as rct_module  # noqa: E402
from core.tools.run_command_tool import make_run_command_tool  # noqa: E402
from sandbox import local_venv  # noqa: E402

execution_module = importlib.import_module("core.nodes.execution")
plan_checks = importlib.import_module("core.plan_checks")

REJECTION_MESSAGE: str = plan_checks.UNSUPPORTED_SHELL_SYNTAX_MESSAGE


# =========================================================================== #
# 语料：元字符集合 / 必命中 / 必放行
# =========================================================================== #

#: 集合内容锁（dev-plan §58 第 1 条的表格逐格誊抄）。
#:
#: ⚠ 这是**内容锁不是数量锁**：多一条 / 少一条 / 改一条都会红，逼改动者回来对规格。
#: 尤其防的是"后人顺手把 `&&` / `;` 补进去"——那会当场打死 `run_in_sandbox` 真支持
#: 的复合命令（§57.4 事实 4）。
EXPECTED_UNSUPPORTED_TOKENS = frozenset({
    # 管道 / 或（3）
    "|", "||", "|&",
    # 输出重定向（8）
    ">", ">>", "1>", "1>>", "2>", "2>>", "&>", "&>>",
    # 输入重定向 / heredoc（3）
    "<", "<<", "<<<",
    # 描述符合并（6）——`>&` / `>&1` / `>&2` / `<>` 由测试工程师发现漏判，
    # Maria 2026-08-01 拍板补入（17 → 21）：它们是 shlex 之后的独立 token，
    # 补入零成本、零模糊匹配、零误伤，与"贴写形态"那条已接受漏判性质不同。
    "2>&1", "1>&2", ">&", ">&1", ">&2", "<>",
    # 后台（1）
    "&",
})

#: 必命中：CP-8.1-1 点名的 19 条真实形态（含复合命令后半段命中）。
MUST_REJECT_COMMANDS: List[str] = [
    "python train.py > train.log",
    "python train.py >> out.log",
    "python train.py 2> err.log",
    "python train.py 2>> e.log",
    "python train.py 1> o.log",
    "python train.py 1>> o.log",
    "python train.py 2>&1",
    "python train.py 1>&2",
    "python train.py &> all.log",
    "python train.py &>> all.log",
    "python train.py 2>&1 | tee log.txt",
    "cat f.txt | head -3",
    "python a.py || python b.py",
    "python a.py |& tee log.txt",
    "python train.py < in.txt",
    "cat << EOF",
    'cat <<< "hi"',
    "python train.py &",
    "pip install numpy && python train.py > train.log",  # 复合命令后半段命中
]

#: 必放行：CP-8.1-1 点名的 12 条合法命令。
#:
#: **这是本文件最重要的一组** —— §57.5「宁可漏判，不可误伤」：漏判 = 回到现状
#: （无损），误伤 = 挡住合法复现命令（有损且当场可见）。
MUST_ALLOW_COMMANDS: List[str] = [
    "python train.py --epochs 1",
    'python -c "print(1>2)"',            # ★要害：print(1>2) 是一整个 token
    'python -c "a and b"',
    "pip install -e /a/b",
    "python a.py && python b.py",        # ★要害：&& 是真支持的，打死它=打死复合命令
    "cd sub ; python x.py",              # ★要害：; 同上
    'python -m pip install "numpy>=1.20"',
    'git commit -m "a > b"',             # 引号内的 > 与其它字符同 token，不误伤
    "source .venv/bin/activate",
    "python -m py_compile x.py",
    "python run_repro_basics.py --dataset digits",
    'python -c "import sys; print(sys.version)"',
]

#: 退化输入：CP-8.1-2 —— 一律 False 且零异常。
DEGENERATE_INPUTS: List[Any] = [
    "", "   ", "\t\n", None, 123, 0, True, ["python"], {"a": 1}, b"python > x",
    'python -c "unclosed',  # 未闭合引号（shlex 抛 ValueError，helper 退化 split）
]


# =========================================================================== #
# A. 谓词层：has_unsupported_shell_syntax（CP-8.1-1 / CP-8.1-2）
# =========================================================================== #


def test_cp_8_1_1_token_set_is_exactly_the_documented_twenty_one() -> None:
    """CP-8.1-1：集合内容与 dev-plan §58 第 1 条表格**逐格相等**。

    集合为 **21 条**（3 管道 + 8 输出重定向 + 3 输入重定向 + 6 描述符合并 + 1 后台）。
    其中 `>&` / `>&1` / `>&2` / `<>` 是测试工程师发现的漏判，Maria 2026-08-01 拍板
    补入（17 → 21）；补入前 dev-plan §58 的散文写「16 条」、表格与代码为 17 条，
    散文系笔误，已随本次一并订正。本用例锁的是**内容**，数量断言随内容自然成立。
    """
    actual = plan_checks._UNSUPPORTED_SHELL_TOKENS
    assert isinstance(actual, frozenset), "必须是 frozenset（模块级常量不可变）"
    assert actual == EXPECTED_UNSUPPORTED_TOKENS, (
        f"元字符集合变了：多了 {sorted(actual - EXPECTED_UNSUPPORTED_TOKENS)}、"
        f"少了 {sorted(EXPECTED_UNSUPPORTED_TOKENS - actual)} —— "
        "改集合必须同步 dev-plan §58 第 1 条并说明理由"
    )
    assert len(actual) == 21


def test_cp_8_1_1_supported_connectors_stay_out_of_the_set() -> None:
    """CP-8.1-1 ★反向命门：`&&` / `;` **绝不能**进集合（§57.3 红线 / §59 P-49）。

    单列一条是刻意的：上面那条内容锁虽已覆盖，但这两个 token 是**最可能被后人
    "顺手补全"**的（看着像 shell 元字符，实则 `run_in_sandbox` 真支持）。收进来
    会当场打死正常复合命令，属"把漏放修成误伤"——§57.5 说的唯一能把事情做坏的方式。
    """
    for connector in ("&&", ";"):
        assert connector not in plan_checks._UNSUPPORTED_SHELL_TOKENS, (
            f"{connector!r} 被收进了不支持集合 —— run_in_sandbox 真支持它"
            "（_split_top_level 拆成子命令），这会当场打死正常复合命令"
        )


@pytest.mark.parametrize("token", sorted(EXPECTED_UNSUPPORTED_TOKENS))
def test_cp_8_1_1_every_token_in_the_set_is_detected(token: str) -> None:
    """CP-8.1-1：集合内 **21 个 token 逐条**在真实命令位置上被识别。

    刻意不直接查集合（那是同义反复），而是**过一遍谓词**——若哪天谓词改成别的
    匹配方式（前缀 / 正则）导致某个 token 反而匹配不上，这里会红。
    """
    assert plan_checks.has_unsupported_shell_syntax(f"python train.py {token} x") is True


@pytest.mark.parametrize("command", MUST_REJECT_COMMANDS)
def test_cp_8_1_1_realistic_metachar_commands_are_detected(command: str) -> None:
    """CP-8.1-1：19 条真实形态逐条命中（含复合命令后半段）。"""
    assert plan_checks.has_unsupported_shell_syntax(command) is True, command


@pytest.mark.parametrize("command", MUST_ALLOW_COMMANDS)
def test_cp_8_1_1_legal_commands_are_never_falsely_rejected(command: str) -> None:
    """CP-8.1-1 ★最重要一组：12 条合法命令**逐条放行**（不可误伤）。

    §57.5：误伤会挡住合法复现命令（有损），比漏判（无损）严重得多。
    """
    assert plan_checks.has_unsupported_shell_syntax(command) is False, (
        f"合法命令被误判为含不支持语法：{command!r}"
    )


@pytest.mark.parametrize("command", [
    "python train.py > train.log && echo done",   # 前半段命中
    "pip install numpy && python train.py | tee log",  # 后半段命中
    "python x.py ; cat y | head -3",              # `;` 后半段命中
])
def test_cp_8_1_1_compound_command_hits_from_any_segment(command: str) -> None:
    """CP-8.1-1：先按顶层 `&&` / `;` 拆分再逐条判 ⇒ 任一段命中即整条命中。

    先拆分是硬要求（与 `is_inline_code_write` 同款纪律）：否则
    `pip install x && python train.py > log` 会整条漏判。
    """
    assert plan_checks.has_unsupported_shell_syntax(command) is True, command


@pytest.mark.parametrize("value", DEGENERATE_INPUTS, ids=lambda v: repr(v)[:24])
def test_cp_8_1_2_degenerate_input_returns_false_without_raising(value: Any) -> None:
    """CP-8.1-2：空串 / 空白 / 非字符串 / 未闭合引号 **一律 False 且零异常**。

    "任何输入都不抛异常"是硬契约：谓词跑在工具入口，抛异常会炸子图。
    """
    assert plan_checks.has_unsupported_shell_syntax(value) is False, repr(value)


def test_cp_8_1_2_predicate_is_pure_and_repeatable() -> None:
    """谓词是纯函数：同一输入连调三次结果恒定（零 IO / 零时序 / 无隐藏状态）。"""
    command = "python train.py > train.log"
    assert [plan_checks.has_unsupported_shell_syntax(command) for _ in range(3)] == [
        True, True, True,
    ]


# =========================================================================== #
# B. 消费点 A：run_in_sandbox（CP-8.1-3 / CP-8.1-5 / CP-8.1-6）—— 最关键
# =========================================================================== #


@pytest.fixture()
def mock_runner_sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Dict[str, Any]:
    """工具层夹具：runner 换成替身，观测"拒 / 不拒"+"是否真进了执行通道"。

    与 `tests/test_sprint7_s710_exec_locality.py::mock_runner_tool` 同款范式
    （刻意复用，不另造轮子）。此处额外 spy 住 **`_run_step_subcommands`** ——
    dev-plan CP-8.1-3 点名要"实证底层 runner 一次都没被调用"，spy 在这一层
    比 spy `run_in_venv` 更贴近早退点（拦截若下沉进 `_run_step_subcommands`
    内部，spy `run_in_venv` 仍可能是 0 次，从而给出假绿）。
    """
    ws = tmp_path / "workspace"
    work = ws / "code"
    work.mkdir(parents=True)
    monkeypatch.setattr(local_venv, "WORKSPACE_DIR", ws)

    venv_calls: List[List[str]] = []
    step_calls: List[Dict[str, Any]] = []

    def _fake_run(python_exe: str, command: List[str], work_dir: str, *a: Any, **k: Any):
        venv_calls.append(list(command))
        return local_venv.SandboxRunResult(
            exit_code=0, stdout="ok", stderr="", duration_seconds=0.0,
            timed_out=False, output_truncated=False, command=list(command),
        )

    real_step_runner = execution_module._run_step_subcommands

    def _spy_step_runner(step: Dict[str, Any], *a: Any, **k: Any):
        step_calls.append(dict(step))
        return real_step_runner(step, *a, **k)

    monkeypatch.setattr(execution_module, "run_in_venv", _fake_run)
    monkeypatch.setattr(execution_module, "_run_step_subcommands", _spy_step_runner)

    collector = execution_module._SandboxRunCollector()
    tool = execution_module.make_run_in_sandbox_tool(
        str(work), collector, None, {"python_exe": str(work / ".venv" / "bin" / "python")},
    )
    return {
        "tool": tool,
        "collector": collector,
        "venv_calls": venv_calls,
        "step_calls": step_calls,
        "work": work,
    }


@pytest.fixture()
def real_subprocess_sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Dict[str, Any]:
    """**能真正起子进程**的最小沙箱（workspace 边界 + venv python 符号链接）。

    真起子进程是刻意的：要断的是"磁盘上那个文件**没被创建**"，拿 mock runner
    断这一条等于什么都没证明（mock 本来就不会写盘）—— S7-06「扫 0 条却 passed」
    同款假绿的解药。范式抄自 `test_sprint7_s710_exec_locality.py::sandbox_workspace`。
    """
    ws = tmp_path / "workspace"
    work = ws / "code"
    venv_bin = work / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    python_link = venv_bin / "python"
    python_link.symlink_to(sys.executable)
    monkeypatch.setattr(local_venv, "WORKSPACE_DIR", ws)

    collector = execution_module._SandboxRunCollector()
    tool = execution_module.make_run_in_sandbox_tool(
        str(work), collector, None, {"python_exe": str(python_link)},
    )
    return {"work": work, "tool": tool, "collector": collector}


def test_cp_8_1_3_sandbox_rejects_redirect_with_structured_error(
    mock_runner_sandbox: Dict[str, Any],
) -> None:
    """CP-8.1-3：命中即返回结构化错误 —— `exit_code=-1`、`tool_error=True`、**不抛异常**。

    返回值必须是合法 JSON（BUG-S1-02 纪律：`str(dict)` 的单引号 repr 会在
    `json.loads` 当场炸），且沿 `_tool_error_json` 既有形态**不新增字段**。
    """
    raw = mock_runner_sandbox["tool"].invoke({"command": "python train.py > train.log"})
    parsed = json.loads(raw)

    assert parsed["tool_error"] is True, parsed
    assert parsed["exit_code"] == -1, parsed
    assert parsed["error"] == REJECTION_MESSAGE, "拒绝文案必须是那唯一一份共用常量"
    assert parsed["results"] == []
    assert parsed["timed_out"] is False
    assert set(parsed) == {"tool_error", "error", "exit_code", "results", "timed_out"}, (
        f"返回结构新增了字段（BUG-S1-02 纪律：沿既有形态）：{sorted(parsed)}"
    )


def test_cp_8_1_3_rejected_command_never_reaches_the_step_runner(
    mock_runner_sandbox: Dict[str, Any],
) -> None:
    """CP-8.1-3 ★命门：被拒命令 **`_run_step_subcommands` 零调用**。

    早退点必须早于执行通道 —— 否则命令会先跑完再报错，那正是这条防线自己
    制造假绿的方式（跑了半条、留下脏状态）。
    """
    mock_runner_sandbox["tool"].invoke({"command": "python train.py 2>&1 | tee log.txt"})

    assert mock_runner_sandbox["step_calls"] == [], (
        "被拒命令进了 _run_step_subcommands —— 早退点下沉到了执行通道之后"
    )
    assert mock_runner_sandbox["venv_calls"] == [], "被拒命令起了子进程"


def test_cp_8_1_3_rejected_command_pollutes_neither_ledger(
    mock_runner_sandbox: Dict[str, Any],
) -> None:
    """CP-8.1-3 ★★硬约束：被拒命令**不进 `run_results`、不进 `step_ledger`**。

    这是拦截点位置的核心验收（`execution.py:970-986` 注释写明了理由，S7-10 已踩
    过一次）：早退若放在写台账之后，一条**实际什么都没干成**的命令会带着假
    `exit_code` 进台账 ⇒ 污染 `exit_ok`（`execution.py:1882-1883`）、被步骤对账
    当成"完成"（R-S7-49 那类假绿）。

    刻意带上 `step_index=7`：声明了归属的命令才是最容易被对账当成"这步做完了"的。
    """
    collector = mock_runner_sandbox["collector"]
    assert collector.run_results == [] and collector.step_ledger == []

    mock_runner_sandbox["tool"].invoke(
        {"command": "python train.py > train.log", "step_index": 7}
    )

    assert collector.run_results == [], "被拒命令进了 run_results ⇒ 会污染 exit_ok"
    assert collector.step_ledger == [], "被拒命令进了 step_ledger ⇒ 会被对账当成'完成'"


def test_cp_8_1_3_ledger_count_is_unchanged_across_a_rejection(
    mock_runner_sandbox: Dict[str, Any],
) -> None:
    """CP-8.1-3 ★★"台账条数不变"—— 空台账断言的加强版。

    上一条从**空**台账起断，若早退恰好被放在"写台账"之前但"清台账"之后，空断言
    仍可能空转。这里先跑两条正常命令让台账真攒到 2 条，再喂被拒命令，断言
    **两个容器的条数一格没动**、且没有任何一条记录的命令串里出现被拒的命令。
    """
    tool = mock_runner_sandbox["tool"]
    collector = mock_runner_sandbox["collector"]

    tool.invoke({"command": "python a.py", "step_index": 0})
    tool.invoke({"command": "python b.py", "step_index": 1})
    before_runs = len(collector.run_results)
    before_ledger = len(collector.step_ledger)
    assert (before_runs, before_ledger) == (2, 2), "阳性对照：正常命令本该进台账"

    tool.invoke({"command": "python train.py > train.log", "step_index": 2})

    assert len(collector.run_results) == before_runs, "被拒命令让 run_results 变长了"
    assert len(collector.step_ledger) == before_ledger, "被拒命令让 step_ledger 变长了"
    recorded = [" ".join(cmd) for _, cmd, _ in collector.step_ledger]
    assert not any("train.log" in c for c in recorded), f"被拒命令留在了台账里：{recorded}"


def test_cp_8_1_3_rejected_redirect_creates_no_file(
    real_subprocess_sandbox: Dict[str, Any],
) -> None:
    """CP-8.1-3（真子进程）：被拒的重定向命令**不产生任何磁盘副作用**。

    配套阳性对照在下一条 —— 没有对照，"文件没被创建"可能只是夹具根本跑不动
    子进程（S7-06 同款假绿）。
    """
    work: Path = real_subprocess_sandbox["work"]
    parsed = json.loads(
        real_subprocess_sandbox["tool"].invoke({"command": 'python -c "print(1)" > out.txt'})
    )

    assert parsed["tool_error"] is True, parsed
    assert parsed["exit_code"] == -1, parsed
    assert not (work / "out.txt").exists(), "拒绝了却仍留下副作用 —— 早退点位置错了"
    assert real_subprocess_sandbox["collector"].step_ledger == []


def test_cp_8_1_3_harness_can_really_create_files(
    real_subprocess_sandbox: Dict[str, Any],
) -> None:
    """★阳性对照（防上一条的"文件未创建"空转）：不带重定向时确实能写盘。

    用短的 `python -c` 写文件（长度远在内联写码阈值之下，不会被 S7-10 那条防线
    连带拦掉）—— 先证明这套夹具真能写盘，再去证明拦截让它写不成。
    """
    work: Path = real_subprocess_sandbox["work"]
    payload = "open('probe_marker.txt','w').write('x')"
    assert len(payload) <= plan_checks._INLINE_PY_MAX_CHARS, "对照命令须在内联阈值之下"

    parsed = json.loads(
        real_subprocess_sandbox["tool"].invoke({"command": f'python -c "{payload}"'})
    )

    assert parsed.get("tool_error") is not True, f"阳性对照被误拒：{parsed}"
    assert parsed["exit_code"] == 0, parsed
    assert (work / "probe_marker.txt").exists(), "夹具跑不动子进程 —— 上一条会空转"


def test_cp_8_1_5_sandbox_does_not_block_the_inline_comparison_probe(
    real_subprocess_sandbox: Dict[str, Any],
) -> None:
    """CP-8.1-5 ★不误伤（真子进程）：`python -c "print(1>2)"` 正常跑通。

    这是"整 token 精确相等"这条取舍的**活体验证**：`print(1>2)` 经 shlex 之后是
    一整个 token、不含裸 `>`。换成子串匹配会当场打死这条完全合法的探针。
    """
    parsed = json.loads(
        real_subprocess_sandbox["tool"].invoke({"command": 'python -c "print(1>2)"'})
    )

    assert parsed.get("tool_error") is not True, f"合法探针被误拒：{parsed}"
    assert parsed["exit_code"] == 0, parsed
    assert "False" in parsed["results"][0]["stdout_tail"], parsed


def test_cp_8_1_5_sandbox_still_supports_compound_commands(
    real_subprocess_sandbox: Dict[str, Any],
) -> None:
    """CP-8.1-5 ★不误伤（真子进程）：`&&` 复合命令**未被本批打死**。

    §57.3 红线「不覆盖 `&&` 与 `;`」的活体验证：两条子命令都真跑、都 exit 0、
    两份 stdout 各自正确 —— 若哪天有人把 `&&` 补进集合，这里会当场红。
    """
    command = 'python -c "print(6*7)" && python -c "print(1+1)"'
    parsed = json.loads(real_subprocess_sandbox["tool"].invoke({"command": command}))

    assert parsed.get("tool_error") is not True, f"复合命令被误拒：{parsed}"
    assert parsed["exit_code"] == 0, parsed
    assert len(parsed["results"]) == 2, parsed
    assert "42" in parsed["results"][0]["stdout_tail"], parsed
    assert "2" in parsed["results"][1]["stdout_tail"], parsed
    assert len(real_subprocess_sandbox["collector"].step_ledger) == 2


def test_cp_8_1_6_sandbox_rejection_logs_masked_and_truncated_warning(
    mock_runner_sandbox: Dict[str, Any], caplog: pytest.LogCaptureFixture,
) -> None:
    """CP-8.1-6：拒绝路径必须打 WARNING，且命令串**已脱敏 + 已截断**。

    已知 bug 模式 #3：禁止静默吞错 —— 关键分支没日志会让下一次诊断多花两轮
    （BUG-S1-02 的教训）。
    """
    secret = "ghp_s712_supersecrettoken"
    secrets_store._SENSITIVE_VALUES.add(secret)
    try:
        command = f"python train.py --token {secret} " + "--pad x " * 60 + "> train.log"
        with caplog.at_level(logging.WARNING, logger=execution_module.logger.name):
            mock_runner_sandbox["tool"].invoke({"command": command})
    finally:
        secrets_store._SENSITIVE_VALUES.discard(secret)

    hits = [r for r in caplog.records if "拒绝含管道/重定向的命令" in r.getMessage()]
    assert hits, "拒绝路径没打 WARNING 日志（禁止静默吞错）"
    message = hits[0].getMessage()
    assert secret not in message, "日志里泄漏了敏感值 —— 必须过 mask_value"
    assert len(message) < 400, "日志回显了超长命令全文 —— 必须截断"


# =========================================================================== #
# C. 消费点 B：run_command（CP-8.1-4 / CP-8.1-5 / CP-8.1-6）
# =========================================================================== #


@pytest.fixture(autouse=True)
def _clean_sensitive_values():
    """进程级敏感值集合逐用例清空（与 tests/test_sprint4_c1.py 同款隔离）。"""
    secrets_store._SENSITIVE_VALUES.clear()
    yield
    secrets_store._SENSITIVE_VALUES.clear()


@pytest.fixture()
def code_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """WORKSPACE_DIR 隔离到 tmp_path（run_command 的越界校验基准）。"""
    # exist_ok：本 fixture 可与 mock_runner_sandbox 同用例共存（两者共用同一 tmp_path），
    # 那条"两处消费点文案同源"的用例正是这么用的。
    ws = tmp_path / "workspace"
    ws.mkdir(exist_ok=True)
    monkeypatch.setattr(config, "WORKSPACE_DIR", ws)
    monkeypatch.setattr(local_venv, "WORKSPACE_DIR", ws)
    d = ws / "task" / "code"
    d.mkdir(parents=True)
    return d


@pytest.fixture()
def run_command_spy(code_dir: Path, monkeypatch: pytest.MonkeyPatch) -> Dict[str, Any]:
    """run_command 工具 + `_run_subprocess` spy（CP-8.1-4 点名的观测点）。"""
    calls: List[List[str]] = []
    real = rct_module._run_subprocess

    def _spy(argv: List[str], *a: Any, **k: Any):
        calls.append(list(argv))
        return real(argv, *a, **k)

    monkeypatch.setattr(rct_module, "_run_subprocess", _spy)
    return {"tool": make_run_command_tool(base_dir=str(code_dir)), "calls": calls}


def test_cp_8_1_4_run_command_rejects_before_starting_any_subprocess(
    run_command_spy: Dict[str, Any],
) -> None:
    """CP-8.1-4 ★命门：命中时 `_run_subprocess` **零调用**、返回结构化错误、不抛异常。

    docstring（`run_command_tool.py:87`）早写了"管道 / 重定向不可用"，但
    **光靠 docstring 约束不住 LLM** —— `RUN_COMMAND_TIMEOUT` 那次的结论就是
    "用机制封顶，不靠 docstring"。这条断言守的就是那个机制。
    """
    raw = run_command_spy["tool"].invoke({"command": 'python -c "import sys" > out.txt'})
    parsed = json.loads(raw)

    assert parsed["exit_code"] == -1, parsed
    assert parsed["error"] == REJECTION_MESSAGE
    assert run_command_spy["calls"] == [], (
        "拒绝路径起了子进程 —— 判定必须早于 _run_subprocess（不留任何执行痕迹）"
    )


def test_cp_8_1_4_run_command_rejection_adds_no_semantic_keys(
    run_command_spy: Dict[str, Any],
) -> None:
    """CP-8.1-4：拒绝返回体键集**恰为 `{error, exit_code}`**（Q-B1 红线 3 不破）。

    结构中不得出现 metrics / success 语义键 —— coding smoke 成功 ≠ 复现成功，
    B 档判定无从消费。
    """
    parsed = json.loads(run_command_spy["tool"].invoke({"command": "cat a.txt | head -3"}))
    assert set(parsed) == {"error", "exit_code"}, f"拒绝结构新增了键：{sorted(parsed)}"


@pytest.mark.parametrize("command", MUST_REJECT_COMMANDS)
def test_cp_8_1_4_run_command_rejects_every_metachar_form(
    run_command_spy: Dict[str, Any], command: str,
) -> None:
    """CP-8.1-4：19 条真实形态在 run_command 侧**逐条被拒且零子进程**。

    两个消费点分别参数化跑一遍是刻意的：谓词共用不代表**接线**共用 —— 只测谓词
    等于假设两处都接对了，那正是 CP-8.1-11 要堵的证据强度问题。
    """
    parsed = json.loads(run_command_spy["tool"].invoke({"command": command}))
    assert parsed["exit_code"] == -1, f"{command} 未被拒：{parsed}"
    assert parsed["error"] == REJECTION_MESSAGE
    assert run_command_spy["calls"] == [], f"{command} 起了子进程"


def test_cp_8_1_5_run_command_does_not_block_legal_command(
    run_command_spy: Dict[str, Any],
) -> None:
    """CP-8.1-5 ★不误伤（真子进程）：合法 smoke 命令正常跑、`_run_subprocess` 调 1 次。"""
    parsed = json.loads(
        run_command_spy["tool"].invoke({"command": f'{sys.executable} -c "print(1>2)"'})
    )

    assert parsed["exit_code"] == 0, parsed
    assert "False" in parsed["stdout_tail"], parsed
    assert len(run_command_spy["calls"]) == 1, "合法命令没被真正执行"


def test_cp_8_1_6_run_command_rejection_logs_masked_and_truncated_warning(
    run_command_spy: Dict[str, Any], caplog: pytest.LogCaptureFixture,
) -> None:
    """CP-8.1-6：run_command 侧拒绝同样打 WARNING + 脱敏 + 截断。"""
    secret = "ghp_s712_runcommand_secret"
    secrets_store._SENSITIVE_VALUES.add(secret)
    try:
        command = f"python x.py --token {secret} " + "--pad y " * 60 + "| tee log.txt"
        with caplog.at_level(logging.WARNING, logger=rct_module.logger.name):
            run_command_spy["tool"].invoke({"command": command})
    finally:
        secrets_store._SENSITIVE_VALUES.discard(secret)

    hits = [r for r in caplog.records if "拒绝执行" in r.getMessage()]
    assert hits, "拒绝路径没打 WARNING 日志"
    message = hits[0].getMessage()
    assert secret not in message, "日志里泄漏了敏感值"
    assert len(message) < 400, "日志未截断超长命令"


# =========================================================================== #
# D. 一处定义两处调用 + 文案可行动（CP-8.1-6）
# =========================================================================== #


def test_cp_8_1_6_both_consumers_return_the_very_same_message(
    mock_runner_sandbox: Dict[str, Any], run_command_spy: Dict[str, Any],
) -> None:
    """CP-8.1-6：两处消费点返回的拒绝文案**逐字相同且同源于那一个常量**。

    "一处定义两处调用"是硬要求：在两个消费点各写一份文案必然漂移（改了 A 忘了 B，
    agent 拿到两套说法）。本用例从**行为侧**证明同源，而不是数源码里有几处字面量。
    """
    from_sandbox = json.loads(
        mock_runner_sandbox["tool"].invoke({"command": "python x.py > a.log"})
    )["error"]
    from_run_command = json.loads(
        run_command_spy["tool"].invoke({"command": "python x.py > a.log"})
    )["error"]

    assert from_sandbox == from_run_command == REJECTION_MESSAGE


def test_rejection_message_is_actionable_plain_chinese() -> None:
    """拒绝文案必须**可行动**（§57.2 第 10 条：这是本次改动净收益还是净损失的决定因素）。

    提示语烂会让 agent 反复撞墙，比静默走偏更糟。三件事一件都不能少：
    ①为什么不生效；②输出**本来就自动完整记录并返回**（agent 用 `>` 是在重造一个
    已存在且被它造坏了的轮子）；③该怎么做。另：这是给模型看的文本，仍须通俗中文、
    零内部枚举 / 字段名 / 节点名（Maria 的用户可见文案纪律同款要求）。
    """
    msg = REJECTION_MESSAGE
    assert "不经过命令行解释器" in msg, "没讲清'为什么不生效'"
    assert "自动完整记录" in msg and "返回给你" in msg, "没讲清输出本来就被捕获"
    assert "直接运行命令" in msg and "让脚本自己去写" in msg, "没给出可执行的替代做法"

    for jargon in (
        "exit_code", "run_in_sandbox", "run_command", "shell=True", "argv", "shlex",
        "stdout_tail", "stderr_tail", "execution", "coding", "token",
    ):
        assert jargon not in msg, f"拒绝文案里出现了内部术语 {jargon!r}"


# =========================================================================== #
# E. 第三个 shlex.split 站点：env_probe_tool 无同族缺口（审计闭合）
# =========================================================================== #


def test_env_probe_tool_is_fail_closed_against_redirects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """审计闭合：全仓第三个 `shlex.split` 后直跑的站点**不存在同族缺口**。

    全仓 `shlex.split` 共 4 处：`plan_checks`（纯解析）、`execution._split_top_level`、
    `run_command_tool`、`env_probe_tool`。前三处本批已治，第四处靠**整条 argv 白名单
    精确匹配**天然 fail-closed（带元字符的命令永远匹配不上清单）⇒ 无需接本批谓词。
    本用例把这个"无需处置"的结论固化成断言，防止日后白名单改成前缀匹配时悄悄开洞。
    """
    env_probe = importlib.import_module("core.tools.env_probe_tool")

    ws = tmp_path / "workspace"
    ws.mkdir()
    monkeypatch.setattr(local_venv, "WORKSPACE_DIR", ws)

    calls: List[Any] = []
    monkeypatch.setattr(
        env_probe, "_run_subprocess", lambda *a, **k: calls.append(a) or (_ for _ in ()).throw(
            AssertionError("白名单被绕过了")
        ),
    )

    tool = env_probe.make_probe_environment_tool(str(ws))
    parsed = json.loads(tool.invoke({"command": "nvidia-smi > gpu.log"}))

    assert calls == [], "带重定向的命令进了子进程 —— 白名单不再 fail-closed"
    assert parsed.get("exit_code") == -1 or "error" in parsed, parsed


# =========================================================================== #
# F. 已知缺口：一律以 xfail(strict=True) 在测试层显形，不假装已覆盖
#
# strict=True 是刻意的 —— 日后谁把缺口补上了，xpass 会当场变红，逼他回来同步
# dev-plan §58 第 1 条与 §59 的登记，而不是让文档静静过期。
# =========================================================================== #


@pytest.mark.xfail(
    strict=True,
    reason=(
        "已知漏判（dev-plan §58 第 1 条第 2 类 / §57.5，登记接受）：贴写形态无空格，"
        "shlex 之后是单 token '>train.log'，精确相等匹配不到。要覆盖必须做前缀匹配，"
        "违反『不做模糊匹配』纪律。漏判 = 回到现状（无损），故接受。"
        "**这是本次覆盖的最大缺口**：LLM 写 `>train.log` 与写 `> train.log` 概率相当。"
    ),
)
@pytest.mark.parametrize("command", [
    "python train.py >train.log",
    "python train.py 2>err.txt",
    "python train.py >>out.log",
])
def test_known_gap_attached_redirect_form_is_missed(command: str) -> None:
    """已知缺口①：贴写重定向（无空格）漏判 —— 现状下必然 xfail。"""
    assert plan_checks.has_unsupported_shell_syntax(command) is True


@pytest.mark.xfail(
    strict=True,
    reason=(
        "已知误伤（dev-plan §59 P-50 / §58 第 1 条第 3 类，登记接受）：shlex 剥引号后"
        "无法区分『引号内字面量』与『裸元字符』，故 grep '|' f.txt 被误拒。"
        "判定为可接受：复现命令里把裸 | 当参数传极罕见，且拒绝文案可行动。"
        "**这是唯一已知的误伤形态** —— 与『宁可漏判不可误伤』原则相悖，故必须在测试层显形。"
    ),
)
@pytest.mark.parametrize("command", [
    "grep '|' f.txt",
    'python x.py --sep ">"',
])
def test_known_gap_quoted_metachar_is_falsely_rejected(command: str) -> None:
    """已知缺口②：引号内的裸元字符被误拒 —— 现状下必然 xfail。"""
    assert plan_checks.has_unsupported_shell_syntax(command) is False


# ⚠ 原「已知缺口③」已转正为正向断言（Maria 2026-08-01 拍板补入集合，17 → 21 条）。
# 保留在 F 组原位以便对照本组其余仍未治的缺口；它证明的是"补入生效"而非"缺口显形"。
@pytest.mark.parametrize("command", [
    "echo err >&2",
    "python x.py >&1",
    "python x.py >& out.log",
    "python x.py <> f.txt",
])
def test_fd_dup_shorthand_is_rejected(command: str) -> None:
    """``>&N`` / ``>&`` / ``<>`` 形态被拒（测试工程师发现的漏判，已补入集合）。

    与本组「贴写形态」那条**已接受**的漏判性质不同：这四个是 shlex 之后的
    **独立 token**，补进集合零成本、不需要任何模糊匹配、零误伤 ⇒ 不属于
    "覆盖它必须放宽匹配规则"那一类，故直接治掉而非登记接受。
    """
    assert plan_checks.has_unsupported_shell_syntax(command) is True


@pytest.mark.xfail(
    strict=True,
    reason=(
        "已知漏判（dev-plan §58 第 1 条第 4 类，登记接受）：$VAR / $(...) / 反引号 "
        "不会被 shlex 拆成独立 token（`$(date)` 是一个 token），token 相等法则上识别不了。"
        "且失败通常可见（程序收到字面量）。本批不扩围。"
    ),
)
def test_known_gap_shell_expansion_is_missed() -> None:
    """已知缺口④：`$(...)` / `$VAR` 展开漏判 —— 现状下必然 xfail。"""
    assert plan_checks.has_unsupported_shell_syntax("echo $(date)") is True


@pytest.mark.xfail(
    strict=True,
    reason=(
        "已知不治（dev-plan §59 P-49，登记不扩围）：run_command 侧不像 run_in_sandbox "
        "那样支持 && / ;（它 shlex 后直跑 argv），故 `cd src && python x.py` 在这里"
        "同样不生效。但那属另一件事且**失败可见**（`cd` 在磁盘上无此程序 ⇒ "
        "_run_subprocess 的 OSError 兜底转 exit_code=-1 + 明确 stderr），"
        "不构成本批要治的『假 exit 0』⇒ 本批刻意不扩围。"
    ),
)
def test_known_gap_run_command_does_not_support_connectors(
    run_command_spy: Dict[str, Any],
) -> None:
    """已知缺口⑤：run_command 侧 `&&` 不被拒也不生效 —— 现状下必然 xfail。

    这条 xfail 的存在价值是**把不对称写在明处**：同一个谓词接在两处工具上，
    但两处对 `&&` 的真实支持度不同（沙箱支持、coding 侧不支持）。
    """
    parsed = json.loads(
        run_command_spy["tool"].invoke({"command": "cd sub && python x.py"})
    )
    assert parsed["error"] == REJECTION_MESSAGE


def test_known_gap_run_command_connector_fails_visibly_instead(
    run_command_spy: Dict[str, Any],
) -> None:
    """缺口⑤的补充（这条是**真绿**）：run_command 侧 `&&` 虽不被拒，但**失败可见**。

    这正是 §59 P-49「登记不治」的依据 —— 它不会伪装成 exit 0，因此不属本批
    要治的病。有了这条断言，上面那个 xfail 才不至于被误读成"这里有个假成功"。
    """
    parsed = json.loads(
        run_command_spy["tool"].invoke({"command": "cd sub && python x.py"})
    )
    assert "error" not in parsed, parsed
    assert parsed["exit_code"] != 0, f"run_command 的 && 竟然返回了成功：{parsed}"
