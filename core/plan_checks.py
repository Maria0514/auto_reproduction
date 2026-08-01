"""plan_checks.py — 计划自洽确定性交叉检查（S6-05，架构 §7.5；S7-10 扩两条 + 共用谓词）。

零 LLM、零 state 写入的纯函数模块。
调用 check_plan(plan, resource_info) 返回警示列表，由 UI 渲染消费（不阻断审批）。

五条规则（rule 用字符串字面量，不建 Enum）：
  W1 数据步骤脱节 ── data_preparation 非空 ∧ 执行步骤无数据关键词
  W2 指标产出脱节 ── expected_results 非空 ∧ 执行步骤无实验/指标关键词
  W3 数据不可得   ── resource_info 无 dataset ∧ selected_repo=None ∧ data_preparation 非空
  W4 步骤进参考仓库目录 ── 任一步骤的顶层子命令 `cd` 到参考仓库（S7-10 约束 A 软防线）
  W5 步骤内联写代码     ── 任一步骤命中 is_inline_code_write（S7-10 约束 B/C 的计划期观测）

S7-10（约束 A/B/C 落点对齐）新增职责：本模块额外承载 **`is_inline_code_write` 这条
共用纯谓词**——同一条不变量在**计划期**（W5，本模块）与**执行期**（`run_in_sandbox`
工具层硬拦截）**各查一次，一处定义两处调用**，不是造两套机制。谓词住这里的理由是
本模块位于 `core/` 顶层、零项目内依赖，被 `core.nodes.execution` import 不成环。

误报防线（R-S6-A5）：关键词宁窄勿宽；警示不阻断审批；纯函数调用方决定展示方式。
S7-10 补一条：W4/W5 同样**只产警示、不阻断审批**——人在回路的计划审核本身就是硬门；
约束 C 的**硬**保证在工具层（命中即拒、不进台账），本模块只负责把它前移成可见告警。
"""
from __future__ import annotations

import shlex
from pathlib import PurePosixPath
from typing import Any, Dict, List, Optional, Tuple

# ──────────────────────────────────────────────────────────────────────────────
# 关键词静态小表（宁窄勿宽——宁漏报不误报）
# ──────────────────────────────────────────────────────────────────────────────

# W1：数据准备相关关键词（英文全小写匹配，中文直接 in 判断）
_DATA_KEYWORDS: List[str] = [
    "data",
    "dataset",
    "download",
    "prepare",
    "预处理",
    "数据",
]

# W2：实验/指标相关关键词
_METRIC_KEYWORDS: List[str] = [
    "run",
    "train",
    "eval",
    "experiment",
    "metric",
    "summary.json",
    "实验",
    "评测",
    "指标",
]


# ──────────────────────────────────────────────────────────────────────────────
# S7-10 约束 C：内联写码判定（共用纯谓词，计划期 W5 + 执行期工具层共用）
# ──────────────────────────────────────────────────────────────────────────────

#: 行内 ``python -c`` 载荷的字符数上限；超过即判「在命令行里写代码」。
#:
#: **定稿值 120（Q-S7-21，2026-07-31 用两轮真实日志语料标定，不是拍脑袋）**。
#: 语料 = ``workspace/1802.03426/code/exec_logs/round_0.log`` + ``round_1.log`` 的
#: 全部 ``python -c`` 子命令（两轮各 7 条，**去重后 9 条**——见架构 §19.5 那张 9 行
#: 标定表；dev-plan 与本处早先写的"8 条"是笔误，已由 §48 P-34 订正，阈值结论不变）：
#:
#:   必须放行（真·短探针）      36 / 46 / 98
#:   必须命中（在命令行里写码）  127 / 144 / 183 / 510 / 1304
#:
#: ⇒ 可行窗口 **[98, 126]**，120 落在窗口内。其中 183 那条（加载真实数据集 + 按论文
#: 超参跑完整降维 + 打印结果）被架构重标为「把整条实验流水线塞进命令行」= PRD §12.5.3
#: 定义的**形态 2**，必须命中；181 那条（三连 mkdir）在窗口内任何取值下都会被拒，
#: 属**预期命中且可恢复**（拒绝文案会指路"拆短或先落成脚本"），不算误伤。
#:
#: ⚠ **单一规则，不做动词枚举、不做后缀白名单**（PRD §12.3 非目标 5 + dev-plan §41.3
#: 红线末条 + Q-S7-24）。dev-plan R-S7-48 原回退列写的"上调 200 + 补 OR 分支"**已作废**：
#: T=200 会在 [120,200] 区间给形态 2 开门，而 183 正是这扇门里真实存在的样本。
#: 需要调值时**只能在可行窗口 [98,126] 内单点调整**；窗口被新语料证伪时回头重议手段，
#: **不得自行新增第二条规则**。
#:
#: 已知且被接受的残留（R-S7-57）：极短写码（如 ``open('x.py','w').write('pass')`` 约 30
#: 字符）任何可行阈值都拦不住 —— 这是"单一规则"的代价，由约束 B（计划不写占位步骤）
#: + W5 + 人在回路审核兜。**不得以此为由回头加动词枚举。**
_INLINE_PY_MAX_CHARS: int = 120

#: 被认作 Python 解释器的可执行文件名（含 venv 绝对路径形态，取 basename 判断）。
_PYTHON_BASENAMES: Tuple[str, ...] = ("py",)

#: ``env`` 包装器的可执行名（``env python -c ...`` 是实测出现过的形态）。
_ENV_WRAPPER_BASENAME: str = "env"

#: CPython **命令行文法**里"额外吃一个参数"的短选项（``-X utf8`` / ``-W ignore`` / ``-Q new``）。
#:
#: ⚠ 这**不是第二条判定规则**：它只用于在 argv 里**定位 ``-c`` 载荷的真实位置**，
#: 不参与"是不是在写代码"的判断——判断永远只有"载荷长度 > 阈值"这一条
#: （PRD §12.3 非目标 5 / Q-S7-24）。把解释器的选项文法和内容判据混为一谈，
#: 就会走回被明令作废的"动词 / 后缀枚举"老路。
_PY_OPTS_TAKING_ARG: str = "XWQ"

#: ``-m <module>``：其后的一切（含 ``-c``）都属于该模块，不再是解释器自己的选项。
#: 这条正是 ``python -m pip install -c constraints.txt`` 不被误判的依据。
_PY_OPT_MODULE: str = "m"


def _basename(token: str) -> str:
    """取可执行路径的文件名（兼容 Windows 反斜杠形态）。"""
    return PurePosixPath(token.replace("\\", "/")).name


def _is_python_exe(token: str) -> bool:
    """该 token 是否为 Python 解释器（裸 ``python`` / ``python3.11`` / venv 绝对路径均算）。"""
    if not token:
        return False
    name = _basename(token)
    return name.startswith("python") or name in _PYTHON_BASENAMES


def _python_exe_index(argv: List[str]) -> Optional[int]:
    """定位 argv 里 Python 解释器所在的下标（这条 argv 不是 Python 命令则 None）。

    通常就是 ``argv[0]``；额外覆盖 ``env [VAR=VALUE ...] python ...`` 这一层包装
    （BUG-S7-10-01 实测的绕过形态之一）。``bash`` / ``pip`` / ``node`` 等其它程序
    一律返回 None ⇒ 它们的 ``-c`` 参数与本谓词无关。
    """
    if not argv:
        return None
    if _is_python_exe(argv[0]):
        return 0
    if _basename(argv[0]) != _ENV_WRAPPER_BASENAME:
        return None
    idx = 1
    while idx < len(argv) and "=" in argv[idx] and not argv[idx].startswith("-"):
        idx += 1  # env 的 VAR=VALUE 环境赋值段
    return idx if idx < len(argv) and _is_python_exe(argv[idx]) else None


def _inline_python_payload(argv: List[str]) -> Optional[str]:
    """取出这条 argv 交给 Python 解释器 ``-c`` 的**载荷**（无则 None）。

    **BUG-S7-10-01 的修复要害**：载荷位靠**扫描**定位，不再硬编码成 ``argv[1]``。
    旧写法要求 ``argv[1] == "-c"``，于是 ``python -u -c`` / ``python -X utf8 -c`` /
    ``python3 -uc`` / ``env python -c`` 只要多一个前置 flag 就整条短路，
    约束 C 的唯一硬防线被一个 token 绕过（实测：文件真落盘、还进 step_ledger）。

    扫描按 CPython 自己的选项文法走，逐 token 判：

      * 位置参数（不以 ``-`` 开头，或就是 ``-``）⇒ 已到脚本路径，解释器选项到此为止；
      * ``--`` 开头的长选项 ⇒ 与 ``-c`` 无关，跳过；
      * 短选项簇逐字符看：``c`` 命中（载荷 = 簇内剩余部分，为空则取下一 token，
        故 ``-uc "<载荷>"`` 与 ``-c"<载荷>"`` 都认）；``m`` 直接终止（模块模式）；
        ``X`` / ``W`` / ``Q`` 会吃掉一个参数（贴在簇内或落在下一 token）。
    """
    idx = _python_exe_index(argv)
    if idx is None:
        return None
    pos = idx + 1
    while pos < len(argv):
        token = argv[pos]
        if len(token) < 2 or not token.startswith("-"):
            return None  # 位置参数（脚本路径 / `-`）——其后的 -c 不属于解释器
        if token.startswith("--"):
            pos += 1
            continue
        cursor = 1
        takes_next_token = False
        while cursor < len(token):
            char = token[cursor]
            if char == "c":
                # `-c` 之后的一切都是载荷：贴在本簇里，或落在下一个 token。
                attached = token[cursor + 1:]
                if attached:
                    return attached
                return argv[pos + 1] if pos + 1 < len(argv) else None
            if char == _PY_OPT_MODULE:
                return None
            if char in _PY_OPTS_TAKING_ARG:
                takes_next_token = cursor + 1 == len(token)
                break
            cursor += 1
        pos += 2 if takes_next_token else 1
    return None


def _split_top_level_argv(command: str) -> List[List[str]]:
    """把命令串按**顶层** ``&&`` / ``;`` 拆成多条 argv（禁 shell，引号内不误拆）。

    与 ``core.nodes.execution._split_top_level`` 同款 ``shlex`` 语义，但**刻意不 import
    它**——那会造成 ``execution → plan_checks → execution`` 循环依赖，且本模块的定位
    就是 ``core/`` 顶层零依赖纯函数。解析失败（未闭合引号等）退化为整条 whitespace
    split 单条 argv，交由调用方自然处理（宁可漏判也不抛异常）。
    """
    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = command.split()

    subcommands: List[List[str]] = []
    current: List[str] = []
    for tok in tokens:
        if tok in ("&&", ";"):
            if current:
                subcommands.append(current)
                current = []
            continue
        current.append(tok)
    if current:
        subcommands.append(current)
    return subcommands


def is_inline_code_write(command: str) -> bool:
    """命令串里是否**以字面量形式**携带了成段代码载荷（S7-10 约束 C 的唯一判据）。

    界线走「**内容从哪来**」而非「文件类型」：

      违规 —— 文件内容 / 实现逻辑以**字面量**出现在命令串里；
      合规 —— 内容由**被执行的既有脚本在运行时算出来**，命令串里只有脚本路径与参数。

    判定对象是**命令字符串本身**、不是文件系统副作用 ⇒ 纯函数、零 IO、零时序、可单测。
    ``python run_repro_basics.py`` 写出多少结果文件与图**永远合规**（零误伤正常复现）。

    单一规则（Q-S7-21）：**先按顶层 ``&&`` / ``;`` 拆分再逐条判**，任一子命令是
    "Python 解释器 + ``-c`` 载荷" 且 ``len(payload) > _INLINE_PY_MAX_CHARS`` 即为 True。
    先拆分是硬要求——否则 ``pip install x && python -c "<超长载荷>"`` 会整条漏判。

    ⚠ **载荷位靠扫描定位、不是硬编码 ``argv[1]``**（BUG-S7-10-01）：``python -u -c`` /
    ``python -B -c`` / ``python -X utf8 -c`` / ``python -W ignore -c`` / ``python3 -uc``
    （组合短选项）/ ``env python -c`` 与裸 ``python -c`` **判定完全一致**。
    定位逻辑见 :func:`_inline_python_payload`；**规则本身仍只有长度这一条**，
    没有也不得有动词 / 后缀枚举（PRD §12.3 非目标 5）。

    误伤边界：``-c`` 属于**别的程序**时一律不触发——``bash -c`` / ``pip install -c
    constraints.txt``（argv[0] 不是 Python 解释器）、``python -m pip install -c ...``
    （``-m`` 之后归模块）、``python train.py -c ...``（``-c`` 是脚本自己的参数）。

    Args:
        command: 单条命令字符串（可含顶层 ``&&`` / ``;`` 复合）。

    Returns:
        True 表示命中「在命令行里写代码」。非字符串 / 空串 / 非 Python ``-c`` 形态 /
        argv 缺载荷一律 False，**任何输入都不抛异常**。
    """
    if not isinstance(command, str) or not command.strip():
        return False
    for argv in _split_top_level_argv(command):
        payload = _inline_python_payload(argv)
        if payload is not None and len(payload) > _INLINE_PY_MAX_CHARS:
            return True
    return False


# ──────────────────────────────────────────────────────────────────────────────
# S7-10：W4 / W5 两条用户可见警示文案（具名常量 → 进 tests/test_s708_user_text_guard.py）
#
# ⚠ 它们经 ui/pages/plan_review.py 的 st.warning 直达用户 ⇒ 必须通俗中文、
#   零内部枚举值 / 字段名 / 节点名 / 工具名 / 英文缩写。
# ──────────────────────────────────────────────────────────────────────────────

_W4_MESSAGE: str = (
    "计划里有步骤先切换到参考仓库目录再执行命令。"
    "参考仓库是多篇论文共用的参考资料，复现代码与实验产出都应该留在本次论文自己的代码目录里；"
    "需要用到仓库源码时，把仓库装成可导入的包即可，不必进到仓库目录里去跑。"
)

_W5_MESSAGE: str = (
    "计划里有步骤把成段代码直接写在命令行里。"
    "复现代码应当由写代码的环节产出成脚本文件，计划只负责规定运行哪个脚本、带什么参数；"
    "这类步骤在实际执行时会被拒绝。"
)

#: W4 判定用的路径信号：克隆下来的参考仓库统一放在工作区的这个目录下。
_REPO_DIR_MARKER: str = "/repos/"


def _cd_targets(command: str) -> List[str]:
    """取出命令串里所有顶层 ``cd <target>`` 的目标（无则空列表）。"""
    targets: List[str] = []
    for argv in _split_top_level_argv(command):
        if len(argv) >= 2 and argv[0] == "cd":
            targets.append(argv[1])
    return targets


def _selected_repo_path(resource_info: Dict[str, Any]) -> Optional[str]:
    """从 resource_info 取选中参考仓库的本地路径（缺失 / 结构异常一律 None，不抛）。"""
    if not isinstance(resource_info, dict):
        return None
    selected = resource_info.get("selected_repo")
    if not isinstance(selected, dict):
        return None
    local_path = selected.get("local_path")
    if not isinstance(local_path, str) or not local_path.strip():
        return None
    return local_path.strip().rstrip("/")


def _step_text(step: Any) -> str:
    """将单个执行步骤提取为可搜索文本（name + command 拼接，忽略 None）。"""
    if not isinstance(step, dict):
        return ""
    parts = [
        str(step.get("name") or ""),
        str(step.get("step_name") or ""),
        str(step.get("command") or ""),
    ]
    return " ".join(parts).strip()


def _any_step_matches(steps: List[Any], keywords: List[str]) -> bool:
    """任意步骤的文本命中关键词列表中的任意一个则返回 True。

    匹配策略：文本转小写后 in 判断（避免大小写误差）。
    中文关键词本身不区分大小写，也走相同路径不特殊处理。
    """
    for step in steps:
        text = _step_text(step).lower()
        for kw in keywords:
            if kw.lower() in text:
                return True
    return False


def _expected_results_nonempty(expected_results: Any) -> bool:
    """判断 expected_results 字段是否「非空」（有实质内容）。

    兼容两种形态：
      - dict 形态：{"trend": ..., "description": ...} —— 至少一键有非空值
      - list 形态：[{"description": ..., "trend": ...}, ...] —— 至少一项非空 dict

    空 dict / 空 list / None / "" 均视作空。
    """
    if not expected_results:
        return False
    if isinstance(expected_results, list):
        for item in expected_results:
            if isinstance(item, dict) and any(v for v in item.values()):
                return True
        return False
    if isinstance(expected_results, dict):
        return any(v for v in expected_results.values())
    # 其他类型（str 等）：转 bool
    return bool(expected_results)


def _has_dataset_resource(resource_info: Dict[str, Any]) -> bool:
    """resource_info 的 external_resources 中是否含有 dataset 类条目。

    判断逻辑（宁窄勿宽）：
      - external_resources 为 list
      - 至少一条 entry 的 type/category/kind 字段（大小写不敏感）含 "dataset"，
        或 url/name 字段含 "dataset"/"huggingface" 等数据集强信号关键词。
    """
    if not resource_info:
        return False
    ext = resource_info.get("external_resources")
    if not isinstance(ext, list) or not ext:
        return False
    _dataset_signals = {"dataset", "huggingface", "kaggle", "zenodo"}
    for entry in ext:
        if not isinstance(entry, dict):
            continue
        # 检查 type / category / kind 字段
        for field in ("type", "category", "kind"):
            val = str(entry.get(field) or "").lower()
            if "dataset" in val:
                return True
        # 检查 url / name 字段含数据集强信号
        for field in ("url", "name", "description"):
            val = str(entry.get(field) or "").lower()
            if any(sig in val for sig in _dataset_signals):
                return True
    return False


def check_plan(plan: Dict[str, Any], resource_info: Dict[str, Any]) -> List[Dict[str, str]]:
    """对复现计划做确定性交叉检查，返回警示列表。

    Args:
        plan: ReproductionPlan dict（来自 interrupt payload）。
        resource_info: ResourceInfo dict（来自 interrupt payload 或 state）。

    Returns:
        警示列表，每项为 {"rule": str, "message": str}。
        空列表表示无警示（干净计划）。
        零 LLM 调用、零 state 写入。
    """
    plan = plan or {}
    resource_info = resource_info or {}

    warnings: List[Dict[str, str]] = []

    data_preparation: Any = plan.get("data_preparation")
    execution_steps: List[Any] = plan.get("execution_steps") or []
    expected_results: Any = plan.get("expected_results")

    # ── W1：数据步骤脱节 ──────────────────────────────────────────────────────
    # data_preparation 非空 AND 全部执行步骤均无数据关键词
    data_prep_nonempty = bool(data_preparation)  # None/[]/""/"" → False
    if data_prep_nonempty:
        if not _any_step_matches(execution_steps, _DATA_KEYWORDS):
            warnings.append({
                "rule": "W1",
                "message": "计划声明了数据准备工作，但执行步骤中没有任何数据相关步骤",
            })

    # ── W2：指标产出脱节 ──────────────────────────────────────────────────────
    # expected_results 非空 AND 全部执行步骤均无实验/指标关键词
    if _expected_results_nonempty(expected_results):
        if not _any_step_matches(execution_steps, _METRIC_KEYWORDS):
            warnings.append({
                "rule": "W2",
                "message": "计划有指标性预期，但执行步骤中没有产出指标的步骤",
            })

    # ── W3：数据不可得 ────────────────────────────────────────────────────────
    # resource_info 无 dataset 类条目 AND selected_repo=None AND data_preparation 非空
    if data_prep_nonempty:
        selected_repo = resource_info.get("selected_repo")
        has_dataset = _has_dataset_resource(resource_info)
        if not has_dataset and selected_repo is None:
            warnings.append({
                "rule": "W3",
                "message": "所需数据集未在资源侦察中找到，请决策",
            })

    # ── W4：步骤切进参考仓库目录（S7-10 约束 A 的计划期软防线）─────────────────
    # 只产警示不阻断：硬拦 `cd` 进仓库已被架构明确否决（部分仓库依赖以仓库根为工作
    # 目录的相对资源路径，硬拦会打死这类复现）⇒ A 只走软防线，硬防线只给约束 C。
    repo_path = _selected_repo_path(resource_info)
    for step in execution_steps:
        command = str(step.get("command") or "") if isinstance(step, dict) else (
            step if isinstance(step, str) else ""
        )
        if not command.strip():
            continue
        hit = False
        for target in _cd_targets(command):
            normalized = target.rstrip("/")
            if repo_path and (normalized == repo_path or normalized.startswith(repo_path + "/")):
                hit = True
            elif _REPO_DIR_MARKER in target:
                hit = True
            if hit:
                break
        if hit:
            warnings.append({"rule": "W4", "message": _W4_MESSAGE})
            break

    # ── W5：步骤在命令行里内联写代码（与工具层硬拦截共用同一条谓词）───────────
    for step in execution_steps:
        command = str(step.get("command") or "") if isinstance(step, dict) else (
            step if isinstance(step, str) else ""
        )
        if is_inline_code_write(command):
            warnings.append({"rule": "W5", "message": _W5_MESSAGE})
            break

    return warnings
