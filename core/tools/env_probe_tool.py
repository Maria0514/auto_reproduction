"""env_probe_tool.py -- resource_scout 只读环境探测工具（S7-06）。

设计权威：docs/sprint7/architecture.md v1.3 §14.1/§14.2/§14.3（Q-S7-7 / Q-S7-8）
+ §15.3(c)（返回增补 ``command`` 键 + 导出 ``PROBE_TOOL_NAME``）
+ §16.1 裁决 2（超时收窄）与 §16.2(a)（描述单一真相源）
+ **§17.3（主控跨节合并裁定：返回端 ``_PROBE_OUTPUT_MAX_BYTES=2500``，推翻 §16.1 裁决 1）**。
对应 dev-plan：docs/sprint7/dev-plan.md §26 T-S7-4-2。

职责：给资源探索 agent 一个**只读**问机器的能力——有没有 GPU、显存 / 驱动 / CUDA、
CPU / 内存、磁盘余量、Python 与工具链版本、已装包，用真事实替代"靠猜"。

只读边界靠机制、不靠 prompt（PRD §2.6 核心红线；实证：S7-05 真跑 coder 遵守率仅 75%）：
    - 判定对象 = ``shlex.split(command)`` 得到的 **argv 元组整体**，命中
      ``_ALLOWED_ARGV`` 才放行；未命中返回结构化拒绝，**且不启动任何进程**
      （判定必须先于任何 ``Popen``，AC-S7-16 副作用探针守门）；
    - 无分级、无分类、无多档权限（architecture §14.1 方案 C：唯一无需黑名单
      兜底、且代码量最小的方案。命令名粒度会漏 ``nvidia-smi -r`` /
      ``pip list --outdated`` / ``git clone <url>`` 三条实证）；
    - 允许清单 ``_PROBE_COMMANDS`` 是整条只读边界的**信任根**，形态由
      AC-S7-21 守门（不得加带自由参数 / 解释器形态的条目）。

与 ``run_command_tool.py`` 的关系（architecture §14.3 形态 3）：
    **另起薄封装，``run_command_tool.py`` 一字不动**——coding 侧正需要
    ``python -c`` / ``py_compile``，探测侧必须禁它，一个 ``@tool`` 只有一份
    schema 描述，两个相反边界无法共存故拆开。真正值得复用的是**执行护栏**，
    本模块 100% 复用 ``_run_subprocess`` 四护栏 + ``_require_within_workspace``
    + ``mask_value``，**不重造执行通道**；且签名本身无 ``extra_env`` 之外的
    凭证口子，本模块显式传 ``extra_env=None``（不注凭证）。

常量落本模块而非 ``config.py``（``config.py`` 零改动，回归面为零）：
清单 / 超时 / 输出上限三者同属该工具的语义边界，与描述同源防漂移。

序列化治理（BUG-S1-02 范式）：返回值一律
``json.dumps(..., ensure_ascii=False, sort_keys=True, default=str)``——
``str(dict)`` 是 Python repr，下游 ``json.loads`` 永远失败且表面看 LLM 还能
"读懂"，bug 极其隐蔽；``sort_keys=True`` + ``ensure_ascii=False`` 同时是
Prompt Cache 字节级幂等的前提。解析失败 / 越界 / 启动失败均转结构化 JSON，
绝不抛异常炸子图。

Prompt Cache 纪律（R-S7-26，本模块最易出事处）：``_PROBE_TOOL_DESCRIPTION``
是工具 schema 的一部分，作为稳定前缀参与 Prompt Cache——**零论文级 / 任务级
动态变量**。特别地，**禁止**把工厂入参 ``base_dir``（= 任务级的
``state["workspace_dir"]``）或任何 workspace 路径写进描述：那一刻前缀"破成
每次"，每任务首调必 miss，**功能全对、无报错、账单持续渗漏且无人察觉**。
措辞刻意只说"工作目录不可指定"而不给出路径（同 ``run_command_tool.py:76``
既有写法）。守门 = AC-S7-24 双工厂字节比对。
"""

from __future__ import annotations

import json
import logging
import shlex
from typing import FrozenSet, Tuple

from core.secrets_store import mask_value
from sandbox.local_venv import _require_within_workspace, _run_subprocess

logger = logging.getLogger(__name__)


# ---------- 单一真相源常量 ----------

#: ``@tool`` 函数名（= ToolMessage.name）。**导出**供 resource_scout 的
#: ``_digest_env_probe`` 扫描配对使用，杜绝"工具改名 → digest 悄悄失效 →
#: 白探回潮"这一类静默漂移（沿 ``_GIT_CLONE_TOOL_NAME`` 同款范式）。
PROBE_TOOL_NAME: str = "probe_environment"

#: 允许清单（15 条，产品可调常量；增删走单点、机制不动）。
#: 刻意排除 ``uname -a``（带主机名等无关信息）、``conda list``、
#: 一切解释器执行形态。``pip list --format=freeze`` 而非裸 ``pip list``：
#: 同为字母序、同样不联网，但每行由约 40 字符降到 15~20 字符，同样预算下
#: 容纳条目约翻倍（R-S7-25：两级截断方向相反，字母序靠后的 torch /
#: transformers 恰是最需要知道的那几个）。
_PROBE_COMMANDS: Tuple[str, ...] = (
    "nvidia-smi", "nvidia-smi -L", "nvcc --version",              # GPU / 驱动 / CUDA
    "lscpu", "free -h", "uname -srm",                             # CPU / 内存 / 架构
    "df -h .",                                                    # 磁盘（cwd 即产物落地盘）
    "python3 --version", "python --version",
    "pip --version", "pip list --format=freeze",                  # Python 环境
    "git --version", "gcc --version", "make --version", "cmake --version",
)

#: 模块级预解析一次；判定即 ``tuple(argv) in _ALLOWED_ARGV``（唯一判定规则）。
_ALLOWED_ARGV: FrozenSet[Tuple[str, ...]] = frozenset(
    tuple(shlex.split(_cmd)) for _cmd in _PROBE_COMMANDS
)

#: 探测专用超时（秒）。**不沿用** ``config.RUN_COMMAND_TIMEOUT``（120，为
#: "跑一段脚本"标定）——清单 15 条全是秒级查询；量级关系
#: 30 < 120 < 1800 成立。收窄后单次挂起最坏 30s，病态路径下节点上界从
#: 20×120s≈40min 降到 20×30s≈10min（R-S7-29：``nvidia-smi`` 在驱动/GPU 坏
#: 状态时可长时间不返回）。30 而非更小：清单里最慢的 ``pip list`` 在冷 FS
#: 上可到秒级十位数，假超时比慢成功更坏，留 3~6 倍余量。
_PROBE_TIMEOUT_SECONDS: int = 30

#: 探测专用**返回端**输出字节上限（stdout / stderr 各自生效）。
#: **绝不传 ``config.SANDBOX_OUTPUT_MAX_BYTES``（1MiB）**——architecture §17.3
#: 主控实测裁定：2500 < ``TOOL_RESULT_MAX_LENGTH``(8000)，必然**先于**
#: ``react_base._truncate_tool_result`` 生效，令包装后 JSON 恒不触发 8000 截断
#: ⇒ ``resource_scout._parse_tool_content`` 永不失败 ⇒ digest 永不静默丢失。
#: 实测对照：无上限时 16111 字符 → 截断 → 解析返回 ``None``（**整条探测结果
#: 消失且无异常、无日志、无红**）；压到 2500 字节后 2737 字符 → 解析成功。
#: 守门 = AC-S7-26（唯一守门，缺失则本常量退化为一句注释）。
#: 注：与 resource_scout 侧 ``_PROBE_OUTPUT_MAX_CHARS``（digest 渲染端字符上限）
#: **两者并存、职责不同，不合并**。
_PROBE_OUTPUT_MAX_BYTES: int = 2500


# ---------- 工具描述（由清单渲染，单一真相源） ----------

# 全静态；正文内零动态值（R-PC4 / AC-S7-24）。清单段由 _PROBE_COMMANDS 渲染，
# 杜绝"清单改了、描述没改"的两份真相（AC-S7-21 描述↔常量一致性口径）。
# 正文刻意不含 `{` / `}` 字面量：Returns 段用中文列举键名而非 JSON 花括号，
# 使"描述内不出现未渲染占位符"成为可直接断言的形态。
_PROBE_TOOL_DESCRIPTION_TEMPLATE = """在本机运行一条【只读环境探测】命令，用来问清这台机器的真实情况：有没有 GPU、显存与驱动 / CUDA 版本、CPU 与内存、磁盘可用空间、Python 与常用工具链版本、已安装的包。

只接受下列固定命令中的一条，且必须逐字一致（多一个参数、少一个参数、换一种写法都会被拒绝）：
{commands}

拒绝的都是同一类原因：本工具只能"查"，不能"改"，也不能"下载"，更不能借解释器执行任意代码。安装 / 卸载 / 删除 / 改配置、任何联网拉取、python -c 这类任意代码执行，一律不会被执行。命令在固定的工作目录下运行，工作目录不可指定。

被拒绝时不要反复猜写法，直接照返回里给出的可选命令清单换一条。

Args:
    command: 上面清单中的一条命令原文。

Returns:
    JSON 字符串，含 command、exit_code、stdout_tail、stderr_tail、timed_out、truncated 六个字段；
    被拒绝时返回 error 与 exit_code（值为 -1），其中"不在清单内"这一拒因还会带上
    allowed_commands 可选命令清单，可据此改写后重试。"""

_PROBE_TOOL_DESCRIPTION: str = _PROBE_TOOL_DESCRIPTION_TEMPLATE.format(
    commands="\n".join(f"  - {_cmd}" for _cmd in _PROBE_COMMANDS)
)


# ---------- 拒绝返回（结构化，不抛异常炸子图） ----------


def _reject(message: str) -> str:
    """结构化拒绝 JSON（沿 ``run_command_tool._error_json`` 范式）。

    不抛异常：拒绝是让 agent 换招继续，不是让节点整体失败（PRD §2.6 边界 7）。
    """
    return json.dumps(
        {"error": message, "exit_code": -1},
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )


def _reject_with_list() -> str:
    """仅「不在清单内」这一拒因专用：额外附允许清单供 agent 当轮自纠（R-S7-14）。

    清单取自 ``_PROBE_COMMANDS`` 同一常量（单一真相源，不另写一份）。
    """
    return json.dumps(
        {
            "error": (
                "这条命令不在只读环境探测的允许清单内，已被拒绝，"
                "没有真正执行。请照下面的可选命令清单逐字挑一条重试，不要自行改写参数。"
            ),
            "exit_code": -1,
            "allowed_commands": list(_PROBE_COMMANDS),
        },
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )


# ---------- 工厂 ----------


def make_probe_environment_tool(base_dir: str):
    """工厂：闭包绑定 ``base_dir`` 作 cwd。

    ``base_dir`` 取 ``state["workspace_dir"]``（资源探索时已就绪；
    ``code_output_dir`` 此刻仍为 ``None`` 不可用）。**闭包绑定、非工具入参**
    —— cwd 不可被模型指定；再叠 ``_require_within_workspace`` 兜底。

    **``base_dir`` 绝不出现在工具描述里**（R-PC4 / AC-S7-24）。
    """
    from langchain_core.tools import tool

    @tool(description=_PROBE_TOOL_DESCRIPTION)
    def probe_environment(command: str) -> str:
        """在本机运行一条只读环境探测命令（只接受固定清单内的整条命令）。

        送进模型的 schema 描述取 ``_PROBE_TOOL_DESCRIPTION``（``description=``
        优先于本 docstring），二者同源于 ``_PROBE_COMMANDS``。
        """
        # 1) shlex 解析：失败 → 结构化拒绝（不抛异常炸子图）。
        try:
            argv = shlex.split(command)
        except ValueError as exc:
            return _reject(f"命令解析失败: {exc}")

        # 2) **唯一判定，先于一切进程启动**（AC-S7-16：不是"跑完了才判定"）。
        #    整条 argv 元组精确匹配；大小写 / 多空白 / 绝对路径 / 解释器形态
        #    一律 fail-closed。
        if tuple(argv) not in _ALLOWED_ARGV:
            logger.info(
                "probe_environment: 命令不在允许清单内，已拒绝且未启动任何进程: %s",
                mask_value(command),
            )
            return _reject_with_list()

        # 3) 护栏：cwd 锚定 base_dir 并校验在 WORKSPACE_DIR 之下
        #    （越界抛 SandboxCreationError → 捕获转结构化拒绝 + WARNING）。
        try:
            _require_within_workspace(base_dir, label="环境探测工作目录")
        except Exception as exc:  # noqa: BLE001 — SandboxCreationError 等一律转结构化拒绝
            logger.warning(
                "probe_environment: 工作目录越界，拒绝执行: base_dir=%s, error=%s",
                base_dir, exc,
            )
            return _reject(f"工作目录越界: {exc}")

        # 4) 执行：100% 复用 _run_subprocess 四护栏；探测专用超时 / 输出上限；
        #    extra_env=None —— 不注凭证。
        rr = _run_subprocess(
            argv,
            cwd=base_dir,
            timeout=_PROBE_TIMEOUT_SECONDS,
            output_max_bytes=_PROBE_OUTPUT_MAX_BYTES,
            extra_env=None,
        )

        # 5) 返回 6 键 JSON（BUG-S1-02 范式）；stdout/stderr 经 mask_value 脱敏。
        #    command 取 " ".join(argv) 的**规范化回显**而非原始入参串：模型写
        #    `df  -h  .` 与 `df -h .` 得到同一 argv、同样命中清单，原样回显会让
        #    下游 digest 字节抖动；规范化后 digest 对模型书写变体免疫（字节幂等），
        #    且与清单文本逐字符相等（清单条目本身无引号）。
        return json.dumps(
            {
                "command": " ".join(argv),
                "exit_code": rr.exit_code,
                "stdout_tail": mask_value(rr.stdout),
                "stderr_tail": mask_value(rr.stderr),
                "timed_out": rr.timed_out,
                "truncated": rr.output_truncated,
            },
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )

    return probe_environment
