"""Sprint 7 S7-06（T-S7-4-8）CP 测试 —— 只读环境探测**工具层**。

覆盖 AC-S7-16 / 21 / 22 / 23 / 24 / 26 + AC-S7-15 的 cwd 锚定与越界面。
姊妹文件 ``tests/test_sprint7_s706_env_facts.py`` 覆盖 AC-S7-15/17/18/19/20（节点与送达面）。

设计权威：
    - docs/sprint7/architecture.md §14.5（必拒 / 必过集与副作用探针口径）、
      §16.5（AC-S7-23/24）、§17.4（AC-S7-26 唯一守门口径）；
    - docs/sprint7/prd.md §3 AC 表 AC-S7-15~26；
    - docs/sprint7/dev-plan.md §26 T-S7-4-8（CP-4.8-1~9，四道命门逐环验红）。

**本文件承载四道命门中的三道**（验红操作与结果见 test-reports/）：
    - 命门 1 = AC-S7-16 只读保证（注掉强制拒绝 → 必红）；
    - 命门 3 = AC-S7-21 清单形态守门（往清单加自由参数 / 解释器条目 → 必红）；
    - 命门 4 = AC-S7-26 返回恒不触发 8000 截断（把 ``_PROBE_OUTPUT_MAX_BYTES``
      调到 8000 以上 → 必红）。

离线维：零 LLM、零 deepxiv 配额、零网络。少量用例真跑本机只读命令
（``python3 --version`` / ``df -h .`` / ``uname -srm``），cwd 一律锚在 tmp 工作目录内。

⚠ **P-8 硬约束（AC-S7-26 的构造口径）**：docs/sprint7/dev-plan.md §31 P-8 实证——
"恒不触发 8000 截断"只对**真实命令输出形态**成立。JSON 转义把 ``\\n`` 一字节变两字符，
若用纯换行填满两路（每 1 字节一个换行）会以"设计缺陷"之名恒红。故本文件的最坏两路满载
**用 freeze 形态填充料**（换行密度 1/14，比本机实测 ``pip list --format=freeze`` 的
1/18.1 更密，属保守取值；P-8 测得撑破 8000 需要 1/2.7，清单 15 条无一可达）。
"""

from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import config  # noqa: E402
import core.tools.env_probe_tool as ept  # noqa: E402
import sandbox.local_venv as lv  # noqa: E402
from core import secrets_store  # noqa: E402
from core.nodes.resource_scout import _parse_tool_content  # noqa: E402
from core.react_base import _truncate_tool_result  # noqa: E402
from core.tools.env_probe_tool import (  # noqa: E402
    PROBE_TOOL_NAME,
    make_probe_environment_tool,
)
from core.tools.run_command_tool import make_run_command_tool  # noqa: E402
from sandbox.local_venv import SandboxRunResult, _truncate_output  # noqa: E402

# 在任何 fixture 改写 config.WORKSPACE_DIR **之前**抓一次真实值：
# AC-S7-24 的"描述里不得出现工作目录路径串"要拿真实 WORKSPACE_DIR 去比对。
_REAL_WORKSPACE_DIR = str(config.WORKSPACE_DIR)


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_process_sensitive_set():
    """mask_value 的进程内敏感集清干净，避免跨用例污染返回串。"""
    secrets_store._SENSITIVE_VALUES.clear()
    yield
    secrets_store._SENSITIVE_VALUES.clear()


@pytest.fixture()
def workspace(tmp_path, monkeypatch) -> Path:
    """WORKSPACE_DIR 隔离到 tmp_path（越界校验基准 + mask_value 的 .secrets 落点）。

    沿 tests/test_sprint4_c1.py::workspace 同款范式。
    """
    ws = tmp_path / "workspace"
    ws.mkdir()
    monkeypatch.setattr(config, "WORKSPACE_DIR", ws)
    monkeypatch.setattr(lv, "WORKSPACE_DIR", ws)
    return ws


@pytest.fixture()
def probe_dir(workspace: Path) -> Path:
    d = workspace / "task"
    d.mkdir(parents=True)
    return d


def _invoke(tool, command: str) -> str:
    return tool.invoke({"command": command})


def _file_snapshot(path: Path) -> Tuple[str, Any, Any]:
    """副作用探针的可比较快照：(存在性, 内容或不可读原因, 权限位)。

    比逐条 assert 更抗干扰——删除 / 改名 / 清空 / 覆盖写 / 改权限任一发生都会让
    快照不等，且失败信息一次性给全。
    """
    if not path.is_file():
        return ("MISSING", None, None)
    mode = path.stat().st_mode & 0o777
    try:
        text: Any = path.read_text(encoding="utf-8")
    except OSError as exc:  # chmod 000 之后读不动，同样算被改动
        text = f"UNREADABLE: {type(exc).__name__}"
    return ("FILE", text, mode)


class _SubprocessSpy:
    """``_run_subprocess`` 替身：只记录调用，绝不启动任何进程。

    AC-S7-16 / AC-S7-22 负向的判定基准——"判定发生在 Popen 之前"只能靠
    "底层执行通道一次都没被调用"来证，光断返回码挡不住"跑完了才判定"。
    """

    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []

    def __call__(self, cmd, *, cwd, timeout, output_max_bytes, extra_env=None):
        self.calls.append({
            "cmd": list(cmd),
            "cwd": cwd,
            "timeout": timeout,
            "output_max_bytes": output_max_bytes,
            "extra_env": extra_env,
        })
        return SandboxRunResult(
            exit_code=0,
            stdout="SPY-STDOUT",
            stderr="",
            duration_seconds=0.0,
            timed_out=False,
            output_truncated=False,
            command=list(cmd),
        )


# ===========================================================================
# AC-S7-16（命门 1）只读保证：必拒集 + 未启动进程 + 副作用探针 + 必过集
# ===========================================================================

# architecture §14.5 建议的必拒集（12 条），逐条覆盖五类禁止项 + 解释器 + 路径形态。
_MUST_REJECT: Tuple[str, ...] = (
    'python -c "print(1)"',            # 通用解释器执行任意代码
    'sh -c "echo hi"',                 # shell 包一层
    "env",                             # 环境变量枚举 / 变量注入前置
    "xargs",                           # 命令拼装器
    "pip install requests",            # 安装（改宿主机状态）
    "pip list --outdated",             # 联网
    "git clone https://github.com/a/b",  # 联网下载
    "nvidia-smi -r",                   # 清单内命令 + 危险参数（命令名粒度会漏）
    "/bin/sh",                         # 绝对路径形态
    "./nvidia-smi",                    # 相对路径形态
    "cat ~/.ssh/id_rsa",               # 读凭证
    "df -h /home",                     # 清单内命令 + 自由参数（越出 cwd）
)


def test_ac_s7_16_must_reject_structured_and_no_process(probe_dir: Path, monkeypatch):
    """必拒集 12 条：结构化拒绝（不抛异常）+ **底层执行通道一次都没被调用**。

    ⚠ 命门 1 验红目标：注掉 ``env_probe_tool`` 里
    ``if tuple(argv) not in _ALLOWED_ARGV: return _reject_with_list()``
    → 本用例必须变红（spy.calls 非空 + 返回不含 error）。
    本用例全程 mock 底层执行，验红时**零真实进程、零宿主副作用**。
    """
    spy = _SubprocessSpy()
    monkeypatch.setattr(ept, "_run_subprocess", spy)
    tool = make_probe_environment_tool(base_dir=str(probe_dir))

    for command in _MUST_REJECT:
        out = _invoke(tool, command)
        parsed = json.loads(out)  # 结构化：合法 JSON（BUG-S1-02 禁 str(dict)）
        assert "error" in parsed, f"必拒命令未被拒绝：{command!r} -> {out!r}"
        assert parsed["exit_code"] == -1, command
        # 不在清单内这一拒因必须附可选清单，供 agent 当轮自纠（R-S7-14）
        assert parsed.get("allowed_commands") == list(ept._PROBE_COMMANDS), command

    assert spy.calls == [], (
        "AC-S7-16 命门：被拒命令不得启动任何进程（判定必须先于 Popen），"
        f"实际被调用 {len(spy.calls)} 次：{spy.calls!r}"
    )


def test_ac_s7_16_side_effect_probe_file_intact(probe_dir: Path):
    """副作用探针：走**真实**执行路径跑破坏性命令，探针文件必须原样存在。

    只断返回码不合格——"拒绝了但其实已经跑过"只有探针能抓到。
    破坏性命令全部相对 cwd（= tmp 探测目录），验红时也不会碰宿主机任何东西。
    """
    probe_file = probe_dir / "probe.txt"
    probe_file.write_text("ORIGINAL", encoding="utf-8")

    tool = make_probe_environment_tool(base_dir=str(probe_dir))
    destructive = (
        "rm -f probe.txt",
        "rm probe.txt",
        "mv probe.txt probe.bak",
        "truncate -s 0 probe.txt",
        'sh -c "echo overwritten > probe.txt"',
        "cp /dev/null probe.txt",
        "chmod 000 probe.txt",
    )
    before = _file_snapshot(probe_file)
    # 先全部跑完再断探针：让"探针被改动"成为首个失败信号（诊断价值最高）。
    returns = [(command, _invoke(tool, command)) for command in destructive]

    assert _file_snapshot(probe_file) == before, (
        "副作用探针被改动（删除 / 改名 / 清空 / 覆盖写 / 改权限）—— "
        "被拒命令其实执行了，只断返回码抓不到这一档"
    )
    assert not (probe_dir / "probe.bak").exists()

    for command, out in returns:
        parsed = json.loads(out)
        assert parsed["exit_code"] == -1 and "error" in parsed, command


def test_ac_s7_16_must_pass_readonly_commands(probe_dir: Path):
    """必过集：清单中不依赖本机可选组件的三条真跑成功且有输出。

    只有必拒集会让"把工具做成永远拒绝"也变绿——必过集是它的对照组。
    """
    tool = make_probe_environment_tool(base_dir=str(probe_dir))
    for command in ("python3 --version", "df -h .", "uname -srm"):
        parsed = json.loads(_invoke(tool, command))
        assert parsed["exit_code"] == 0, f"{command} 应可正常执行: {parsed}"
        assert parsed["timed_out"] is False, command
        body = (parsed["stdout_tail"] or "") + (parsed["stderr_tail"] or "")
        assert body.strip(), f"{command} 应有输出: {parsed}"
        assert parsed["command"] == command


def test_ac_s7_16_reject_does_not_raise_on_malformed_command(probe_dir: Path, monkeypatch):
    """解析失败 / 空命令：结构化拒绝，不抛异常炸子图，且不启动进程。"""
    spy = _SubprocessSpy()
    monkeypatch.setattr(ept, "_run_subprocess", spy)
    tool = make_probe_environment_tool(base_dir=str(probe_dir))

    parsed = json.loads(_invoke(tool, 'nvidia-smi "unbalanced'))
    assert parsed["exit_code"] == -1 and "命令解析失败" in parsed["error"]

    parsed_empty = json.loads(_invoke(tool, "   "))
    assert parsed_empty["exit_code"] == -1 and "error" in parsed_empty

    assert spy.calls == []


# ===========================================================================
# AC-S7-15（工具层面）cwd 锚定 + 越界被拒
# ===========================================================================


def test_ac_s7_15_cwd_anchored_to_base_dir(probe_dir: Path, monkeypatch):
    """cwd 闭包绑定 base_dir（非工具入参，模型不可指定）。"""
    spy = _SubprocessSpy()
    monkeypatch.setattr(ept, "_run_subprocess", spy)
    tool = make_probe_environment_tool(base_dir=str(probe_dir))
    _invoke(tool, "uname -srm")
    assert len(spy.calls) == 1
    assert spy.calls[0]["cwd"] == str(probe_dir)
    # 凭证零注入：探测通道不传 extra_env
    assert spy.calls[0]["extra_env"] is None


def test_ac_s7_15_cwd_outside_workspace_rejected(tmp_path, workspace: Path, monkeypatch):
    """越界 cwd → 结构化拒绝 + 底层执行通道未被调用。"""
    spy = _SubprocessSpy()
    monkeypatch.setattr(ept, "_run_subprocess", spy)
    outside = tmp_path / "outside"
    outside.mkdir()
    tool = make_probe_environment_tool(base_dir=str(outside))

    parsed = json.loads(_invoke(tool, "uname -srm"))
    assert parsed["exit_code"] == -1
    assert "工作目录越界" in parsed["error"]
    assert spy.calls == [], "越界必须在 Popen 之前判定"


# ===========================================================================
# AC-S7-21（命门 3）允许清单形态守门 —— 清单是整条只读边界的信任根
# ===========================================================================

# 通用解释器 / 命令包装器形态的 argv[0] 黑名单（PRD AC-S7-21 口径）。
# 注意：清单内 ``python3 --version`` / ``python --version`` 合法，故 python 不入此表——
# "借解释器跑任意代码"由下面的 ``-c`` 形态断言封住。
_WRAPPER_EXECUTABLES = frozenset({
    "sh", "bash", "zsh", "ksh", "dash", "csh", "tcsh", "fish",
    "env", "xargs", "nohup", "timeout", "nice", "stdbuf", "setsid",
    "sudo", "su", "chroot", "script", "eval", "exec", "watch", "nc",
})

# 占位符 / shell 元字符形态：出现任一即意味着"由模型自由填参数"重新打开缺口。
_PLACEHOLDER_CHARS = ("{", "}", "<", ">", "$", "*", "?", "|", ";", "&", "`", "~", "!", "\n")


def test_ac_s7_21_command_list_shape_is_frozen():
    """清单每条经 shlex 解析后 argv 完全确定：无占位符、无解释器 / 包装器形态。

    ⚠ 命门 3 验红目标（R-S7-16 清单漂移是绕过分析里唯一未被机制封住的残余）：
      - 往 ``_PROBE_COMMANDS`` 加 ``"df -h {path}"``（自由参数）→ 本用例必红；
      - 加 ``"python -c print(1)"``（解释器形态）→ 本用例必红。
    """
    commands = ept._PROBE_COMMANDS
    assert commands, "允许清单不得为空（扫不到即报红保险）"
    assert len(set(commands)) == len(commands), "允许清单不得有重复条目"

    for command in commands:
        argv = shlex.split(command)  # 解析不得抛异常
        assert argv, f"清单条目解析为空：{command!r}"

        for token in argv:
            for ch in _PLACEHOLDER_CHARS:
                assert ch not in token, (
                    f"清单条目 {command!r} 的 token {token!r} 含占位符 / 元字符 {ch!r}"
                    "——带自由参数的条目会同时重新打开五类禁止项（R-S7-16）"
                )

        # 解释器执行形态：任何 -c / --command / -e / --eval 形态一律不许进清单
        for token in argv[1:]:
            assert token not in ("-c", "--command", "-e", "--eval", "-exec"), \
                f"清单条目 {command!r} 含解释器执行形态 token {token!r}"

        exe = argv[0]
        assert "/" not in exe, f"清单条目 {command!r} 的 argv[0] 不得为路径形态"
        assert exe not in _WRAPPER_EXECUTABLES, \
            f"清单条目 {command!r} 的 argv[0]={exe!r} 是通用解释器 / 命令包装器"

        # 规范化回显与清单文本逐字符相等（digest 字节幂等的前提）
        assert " ".join(argv) == command, \
            f"清单条目 {command!r} 的规范化回显与原文不等（会让 digest 字节抖动）"

    # 判定表由清单唯一派生，条数必须对得上（防"加了条目但判定表没重建"）
    assert len(ept._ALLOWED_ARGV) == len(commands)
    for command in commands:
        assert tuple(shlex.split(command)) in ept._ALLOWED_ARGV


def test_ac_s7_21_description_matches_command_list():
    """送进模型的描述里的清单文本 == 清单常量（单一真相源，不得各写一份）。"""
    description = ept._PROBE_TOOL_DESCRIPTION
    assert description.strip()
    for command in ept._PROBE_COMMANDS:
        assert f"  - {command}" in description, \
            f"清单条目 {command!r} 未出现在工具描述里（描述与常量成了两份真相）"
    # 描述里的清单行条数与常量条数一致（防描述里多写了清单外条目）
    listed = [
        line.strip()[2:].strip()
        for line in description.splitlines()
        if line.startswith("  - ")
    ]
    assert listed == list(ept._PROBE_COMMANDS)


# ===========================================================================
# AC-S7-22 双用途边界互不削弱（一正一负，同文件相邻两条）
# ===========================================================================


def test_ac_s7_22_positive_coding_run_command_still_executes_interpreter(probe_dir: Path):
    """正向：coding 侧 ``run_command`` 跑解释器形态两条**仍成功**（smoke 能力零回归）。

    只留负向会让 coding 侧哪天悄悄失守而无人察觉——`run_command_tool.py` 本批零改动
    只保证"这一次没改"，保证不了后续。
    用 ``sys.executable`` 而非裸 ``python``：本机裸 ``python`` 是 py2，用当前解释器
    才能稳定表达"解释器执行形态在 coding 侧仍然可用"这一语义。
    """
    script = probe_dir / "smoke_mod.py"
    script.write_text("VALUE = 1\n", encoding="utf-8")

    tool = make_run_command_tool(base_dir=str(probe_dir))

    out1 = json.loads(tool.invoke({"command": f'{sys.executable} -c "print(1)"'}))
    assert out1["exit_code"] == 0, out1
    assert "1" in out1["stdout_tail"]

    out2 = json.loads(tool.invoke({"command": f"{sys.executable} -m py_compile {script}"}))
    assert out2["exit_code"] == 0, out2


def test_ac_s7_22_negative_probe_rejects_same_two_commands(probe_dir: Path, monkeypatch):
    """负向：探测侧同样两条被结构化拒绝，**且底层执行通道未被调用**。

    与上一条形成"边界相反且互不削弱"的对照断言（比"断言 run_command_tool.py 未被 diff"
    更可执行、且不依赖版本控制状态）。
    """
    script = probe_dir / "smoke_mod.py"
    script.write_text("VALUE = 1\n", encoding="utf-8")

    spy = _SubprocessSpy()
    monkeypatch.setattr(ept, "_run_subprocess", spy)
    tool = make_probe_environment_tool(base_dir=str(probe_dir))

    same_two = (
        'python -c "print(1)"',
        f"python -m py_compile {script}",
        f'{sys.executable} -c "print(1)"',
        f"{sys.executable} -m py_compile {script}",
    )
    for command in same_two:
        parsed = json.loads(_invoke(tool, command))
        assert parsed["exit_code"] == -1 and "error" in parsed, command

    assert spy.calls == [], "探测侧必须在 Popen 之前拒绝解释器形态"


# ===========================================================================
# AC-S7-23 探测超时独立且真的传下去
# ===========================================================================


def test_ac_s7_23_timeout_constant_value_and_magnitude():
    assert ept._PROBE_TIMEOUT_SECONDS == 30
    assert isinstance(ept._PROBE_TIMEOUT_SECONDS, int)
    assert not isinstance(ept._PROBE_TIMEOUT_SECONDS, bool)
    assert (
        ept._PROBE_TIMEOUT_SECONDS
        < config.RUN_COMMAND_TIMEOUT
        < config.SANDBOX_EXEC_TIMEOUT
    ), "量级关系 30 < 120 < 1800 必须成立"


def test_ac_s7_23_timeout_actually_passed_down(probe_dir: Path, monkeypatch):
    """"只定义不用"是本条最现实的失效形态：常量在、注释在、跑起来还是 120s 且无报错。"""
    spy = _SubprocessSpy()
    monkeypatch.setattr(ept, "_run_subprocess", spy)
    tool = make_probe_environment_tool(base_dir=str(probe_dir))
    _invoke(tool, "uname -srm")

    assert len(spy.calls) == 1
    assert spy.calls[0]["timeout"] == ept._PROBE_TIMEOUT_SECONDS
    assert spy.calls[0]["timeout"] != config.RUN_COMMAND_TIMEOUT
    # 返回端字节上限同样必须真的传下去（AC-S7-26 的前置条件）
    assert spy.calls[0]["output_max_bytes"] == ept._PROBE_OUTPUT_MAX_BYTES
    assert spy.calls[0]["output_max_bytes"] != config.SANDBOX_OUTPUT_MAX_BYTES


def test_ac_s7_23_config_has_no_probe_constants():
    """负向：config.py 未新增任何探测相关常量（常量落工具模块，回归面为零）。"""
    leaked = [name for name in dir(config) if "PROBE" in name.upper()]
    assert leaked == [], f"config 不应出现探测相关常量：{leaked}"


# ===========================================================================
# AC-S7-24 工具 schema 零任务级动态值（"破成每次"的唯一防线）
# ===========================================================================


def test_ac_s7_24_two_factories_byte_identical_schema(workspace: Path):
    """两个不同 base_dir 造出的工具，name / description / args_schema 字节级一致。

    失效形态是**静默**的：开发在描述里写"工作目录为 {base_dir}"，功能全对、无报错，
    但缓存前缀变成每任务一版、每任务首调必 miss，账单持续渗漏（R-S7-26）。
    """
    dir_a = workspace / "task-a"
    dir_b = workspace / "task-b"
    dir_a.mkdir()
    dir_b.mkdir()

    tool_a = make_probe_environment_tool(base_dir=str(dir_a))
    tool_b = make_probe_environment_tool(base_dir=str(dir_b))

    assert tool_a.name == tool_b.name == PROBE_TOOL_NAME
    assert tool_a.description == tool_b.description
    schema_a = json.dumps(tool_a.args_schema.model_json_schema(), sort_keys=True)
    schema_b = json.dumps(tool_b.args_schema.model_json_schema(), sort_keys=True)
    assert schema_a == schema_b


def test_ac_s7_24_description_has_no_task_level_dynamic_values(workspace: Path):
    """描述内零路径串、零未渲染占位符；清单每条原文均在描述中（同一真相源）。"""
    dir_a = workspace / "task-a"
    dir_a.mkdir()
    description = make_probe_environment_tool(base_dir=str(dir_a)).description

    assert "{" not in description and "}" not in description, \
        "描述内不得出现未渲染的花括号（未渲染占位符 / 插值痕迹）"
    for forbidden in (str(dir_a), str(workspace), _REAL_WORKSPACE_DIR):
        assert forbidden not in description, f"描述泄漏工作目录路径串：{forbidden}"
    assert "arxiv" not in description.lower()
    for command in ept._PROBE_COMMANDS:
        assert command in description


# ===========================================================================
# AC-S7-26（命门 4）返回恒不触发 8000 截断 —— §17 主控实测收口的唯一守门
# ===========================================================================


def _freeze_shaped_bytes(min_bytes: int) -> bytes:
    """构造 ``pip list --format=freeze`` 形态的真实输出填充料（P-8 硬约束）。

    每行形如 ``pkg000==1.2.0\\n``（14 字节），换行密度 1/14 —— 比本机实测的
    ``pip list --format=freeze``（1/18.1）更密，属**保守**取值；P-8 实证撑破 8000
    需要 1/2.7 的病态密度，清单 15 条无一可达。**不得**改用纯换行等病态填充：
    那会让本守门以"设计缺陷"之名恒红，把唯一的静默失效守门废掉。
    """
    chunks: List[bytes] = []
    total = 0
    index = 0
    while total < min_bytes:
        line = f"pkg{index % 1000:03d}==1.2.{index % 10}\n".encode("utf-8")
        chunks.append(line)
        total += len(line)
        index += 1
    return b"".join(chunks)


def test_ac_s7_26_worst_case_two_way_saturation_never_truncated(probe_dir: Path, monkeypatch):
    """最坏两路满载：stdout / stderr 各撑满 ``_PROBE_OUTPUT_MAX_BYTES``。

    断言 ① 返回串 < TOOL_RESULT_MAX_LENGTH(8000)；② 过 ``_truncate_tool_result``
    原样不变；③ 再过 ``_parse_tool_content`` 解析成功且 **6 键齐全**
    （= architecture §17.2 对照组的固化）。

    ⚠ 命门 4 验红目标：把 ``_PROBE_OUTPUT_MAX_BYTES`` 调到 8000 以上
    （或改传 ``config.SANDBOX_OUTPUT_MAX_BYTES``）→ 本用例必红。
    没有它，`_PROBE_OUTPUT_MAX_BYTES` 退化为一句注释，而失效形态**静默无红**：
    超长输出被 8000 截断 → JSON 残缺 → `_parse_tool_content` 返 None →
    整条探测结果消失且无异常、无日志。
    """
    # 填充料按**当前常量**放大，故调大常量必然放大返回串（验红能力的来源）。
    raw = _freeze_shaped_bytes(ept._PROBE_OUTPUT_MAX_BYTES * 3)

    def _saturating_run(cmd, *, cwd, timeout, output_max_bytes, extra_env=None):
        # 走**真实**截断函数，marker 与保尾语义与生产路径完全一致。
        stdout_text, stdout_trunc = _truncate_output(raw, output_max_bytes)
        stderr_text, stderr_trunc = _truncate_output(raw, output_max_bytes)
        return SandboxRunResult(
            exit_code=0,
            stdout=stdout_text,
            stderr=stderr_text,
            duration_seconds=0.0,
            timed_out=False,
            output_truncated=stdout_trunc or stderr_trunc,
            command=list(cmd),
        )

    monkeypatch.setattr(ept, "_run_subprocess", _saturating_run)
    tool = make_probe_environment_tool(base_dir=str(probe_dir))
    out = _invoke(tool, "pip list --format=freeze")

    assert len(out) < config.TOOL_RESULT_MAX_LENGTH, (
        f"最坏两路满载下返回串 {len(out)} 字符 >= TOOL_RESULT_MAX_LENGTH"
        f"({config.TOOL_RESULT_MAX_LENGTH})——会被 react_base 截断成残缺 JSON，"
        "整条探测结果静默丢失"
    )

    # 构造真的满载了（防"填充料没撑满 → 断言空转"的假绿）
    parsed_direct = json.loads(out)
    assert len(parsed_direct["stdout_tail"].encode("utf-8")) >= ept._PROBE_OUTPUT_MAX_BYTES
    assert len(parsed_direct["stderr_tail"].encode("utf-8")) >= ept._PROBE_OUTPUT_MAX_BYTES
    assert parsed_direct["truncated"] is True, \
        "两路输出必须真的经过返回端字节上限截断（否则本用例是空转的假绿）"

    stage1 = _truncate_tool_result(out)
    assert stage1 == out, "未触发 8000 截断时 _truncate_tool_result 应原样返回"

    stage2 = _parse_tool_content(stage1)
    assert stage2 is not None, "双阶段解析必须成功（否则 digest 静默丢整条）"
    assert set(stage2.keys()) == {
        "command", "exit_code", "stdout_tail", "stderr_tail", "timed_out", "truncated",
    }


# ===========================================================================
# CP-4.2-8 序列化与拒绝形态（BUG-S1-02 规避自查）
# ===========================================================================


def test_cp_4_2_8_serialization_form(probe_dir: Path):
    """返回走 json.dumps(ensure_ascii=False, sort_keys=True)；command 为规范化回显。"""
    tool = make_probe_environment_tool(base_dir=str(probe_dir))

    out = _invoke(tool, "uname -srm")
    # BUG-S1-02 规避自查：str(dict) 的 Python repr 以 "{'" 开头且 json.loads 必失败
    assert out.startswith('{"'), f"返回必须是合法 JSON 而非 str(dict) repr: {out[:40]!r}"
    assert json.loads(out)["exit_code"] == 0
    keys = ["command", "exit_code", "stderr_tail", "stdout_tail", "timed_out", "truncated"]
    positions = [out.index(f'"{k}"') for k in keys]
    assert positions == sorted(positions), "返回 JSON 必须 sort_keys=True"

    # 多空白书写变体 → 同一 argv → 同一 command 回显（digest 对书写变体免疫）
    a = json.loads(_invoke(tool, "df -h ."))
    b = json.loads(_invoke(tool, "df  -h   ."))
    assert a["command"] == b["command"] == "df -h ."


def test_cp_4_2_8_reject_json_is_ensure_ascii_false():
    """拒绝文案为中文原文（ensure_ascii=False），且 allowed_commands 取自同一常量。"""
    payload = ept._reject_with_list()
    assert "\\u" not in payload, "ensure_ascii=False 应保留中文原文"
    parsed = json.loads(payload)
    assert parsed["allowed_commands"] == list(ept._PROBE_COMMANDS)
    assert parsed["exit_code"] == -1
