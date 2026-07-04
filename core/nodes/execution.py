"""execution 节点（S3-03 + S3-04 + S3-07）：sandbox 执行 + 错误分类 + B 档判定 + 修复循环边界 + interrupt#2。

节点形态：**手写复合节点**（与 ``planning.py`` 同构，非 ReAct wrapper）。七步骨架（架构 §2.3.1）：
    1. prepare_venv（sandbox）准备 venv + 装依赖；
    2. 逐条 run_in_venv 执行 reproduction_plan.execution_steps 并聚合；
    3. _classify_execution 错误分类（节点本地 ExecutionFeedback，不污染 NodeError 三态）；
    4. _parse_metrics 三档解析（结构化标签 → 正则 → LLM 抽取兜底）；
    5. _build_execution_result B 档 success 判定（exit 0 且 ≥1 指标）；
    6. _map_execution_result 单点 read-modify-write（细分类进 error_message 前缀）；
    7. _maybe_interrupt_or_return 修复循环边界 + 可能的 interrupt#2。

interrupt#2 重跑幂等（S-1 spike CP-S-3 契约，架构 §4.3）：
    LangGraph 节点函数体内 interrupt() 在 resume 时整节点从头重跑；interrupt 前于函数体内
    对 state 的写入（尚未 return 的局部 dict）不会被 checkpoint。因此「sandbox + interrupt 同一
    节点内靠读 state 去重」在 resume 重跑时无效（S-1 实测副作用=2）。
    **可行契约（C3 落地）= 持久化边界分离**：execution 首次跑 sandbox 后若判定需要 interrupt，
    **先 return 落盘 execution_result + 置 _dev_loop_route="await_dev_loop_interrupt" 标记，不 interrupt**；
    由出边 self-loop 路由（D1 _route_after_execution）再次进入 execution，重入时入口 state 已含本
    回合结果（已过 checkpoint 边界），guard 命中跳过 sandbox 后才函数体内 interrupt()。resume 重跑
    仅重跑 interrupt 所在的这次进入，sandbox 不重跑 → 副作用恰为 1（CP-C3-13）。

治理范式（must-fix-1 / must-fix-2 / BUG-S1-02/03）：
    - node_errors / degraded_nodes / fix_loop_history 全部单点 read-modify-write，**严禁 reducer**；
    - execution 主体不调 LLM（零扣减）；仅 metrics 档 3 LLM 抽取兜底触发时按实际次数单点回写
      retry_budget_remaining + 累加 _dev_loop_llm_calls；
    - ErrorCategory / ExecutionFeedback / AUTO_FIXABLE 是节点本地对象，**不进 core/state.py**；
      细分类写进 NodeError.error_message 的 [error_category=...] 前缀，error_type 严格保持三态；
    - fix_loop_count 单点自增（仅「回 coding」分支），interrupt/降级/成功分支绝不自增；
    - 失败分类/降级/异常兜底均打 WARNING 日志（非静默吞错）；
    - 任何写进结构化字段的 dict 一律 json.dumps(ensure_ascii=False, sort_keys=True, default=str)，禁 str(dict)。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.errors import GraphBubbleUp
from langgraph.types import interrupt

from config import (
    DEV_LOOP_MIN_CALLS_PER_ROUND,
    MAX_DEV_LOOP_LLM_CALLS,
    MAX_FIX_LOOP_COUNT,
    REACT_MAX_ROUNDS_EXECUTION,
)
from core.errors import SandboxCreationError, make_node_error
from core.llm_client import create_llm, resolve_llm_config
from core.react_base import _repair_truncated_json_prefix, create_react_subgraph
from core.secrets_store import build_credential_env, load_all_secrets, mask_value
from core.state import ExecutionResult, FixLoopRecord, GlobalState
from core.tools.interaction_tools import request_user_input
from sandbox.local_venv import (
    SandboxPrepareResult,
    SandboxRunResult,
    _is_within_workspace,
    _venv_python_exe,
    collect_artifacts,
    prepare_venv,
    run_in_venv,
)

logger = logging.getLogger(__name__)


NODE_NAME: str = "execution"

# interrupt#2 payload 约定（与 S-1 spike / app.py interrupt_kind helper 对齐，§2.5.4）。
INTERRUPT_KIND: str = "dev_loop_failure"

# _dev_loop_route 取值约定（execution 写、_route_after_execution(D1) 读，§2.5.3）：
#   "retry_coding"            —— 可修复且未触顶 → 出边回 coding 修复（fix_loop_count 本回合已 +1）。
#   "await_dev_loop_interrupt" —— sandbox 已跑完并落盘、判定需 interrupt#2，等待 self-loop
#                                 重入 execution 后函数体内 interrupt()（重跑幂等 commit 边界）。
# 其余（成功 / 降级）一律置 None，由路由按 user_fix_decision / execution_result.success 兜底到 reporting。
_ROUTE_RETRY_CODING: str = "retry_coding"
_ROUTE_AWAIT_INTERRUPT: str = "await_dev_loop_interrupt"

# 单条 stderr/代表性片段裁剪上限（防 payload / NodeError 撑爆）。
_STDERR_TAIL_CHARS: int = 2000


# ---------------------------------------------------------------------------
# 错误分类载体（节点本地 dataclass / Enum，不进 core/state.py，架构 §2.3.2）
# ---------------------------------------------------------------------------


class ErrorCategory(str, Enum):
    """执行期错误细分类（节点本地，绝不写入 NodeError.error_type）。"""

    # —— 可自动修复类（送回 coding，计入 fix_loop_count，AC-S3-08）——
    SYNTAX = "syntax"
    IMPORT = "import"
    DEPENDENCY = "dependency"
    PATH = "path"
    RUNTIME = "runtime"
    # —— 不可自动修复类（不进重试，走 interrupt#2 / 降级）——
    DATA_MISSING = "data_missing"
    HARDWARE = "hardware"
    TIMEOUT = "timeout"
    UNRESOLVED_RESOURCE = "unresolved_resource"
    NONE = "none"  # 执行成功，无错误


# 可自动修复类集合（驱动 §2.5.2 路由：是否回 coding）。
AUTO_FIXABLE = {
    ErrorCategory.SYNTAX,
    ErrorCategory.IMPORT,
    ErrorCategory.DEPENDENCY,
    ErrorCategory.PATH,
    ErrorCategory.RUNTIME,
}


@dataclass
class ExecutionFeedback:
    """执行反馈层载体（节点本地）。category 冒泡到 GlobalState 时再映射为三态之一。"""

    category: ErrorCategory
    auto_fixable: bool  # = category in AUTO_FIXABLE
    summary: str  # 一句话错误摘要（供 fix_loop_history.error_summary + coding 反馈）
    fix_hint: str  # 给 coding 的修复建议
    representative_stderr: str  # 代表性 stderr 片段（裁剪）


# 关键字表（小写匹配 stderr，复用 git_tools 静态常量范式）。顺序敏感：硬件/数据缺失先于通用 runtime。
_HARDWARE_KEYWORDS = (
    "cuda out of memory",
    "out of memory",
    "no cuda gpus are available",
    "no cuda-capable device",
    "cuda error",
    "device-side assert",
    "cudnn",
    "insufficient memory",
)
_DATA_MISSING_KEYWORDS = (
    "dataset not found",
    "no such file or directory: 'data",
    "no such file or directory: \"data",
    "download the dataset",
    "please download",
    "missing dataset",
    "data directory",
)
_UNRESOLVED_RESOURCE_KEYWORDS = (
    "pretrained weights not found",
    "checkpoint not found",
    "model weights are not publicly available",
    "request access",
    "license required",
)


def _tail(text: Optional[str], limit: int = _STDERR_TAIL_CHARS) -> str:
    """取字符串尾部（错误栈通常在末尾）。"""
    if not text:
        return ""
    s = text if isinstance(text, str) else str(text)
    return s[-limit:] if len(s) > limit else s


# ---------------------------------------------------------------------------
# 步骤 3：错误分类（架构 §2.3.2）
# ---------------------------------------------------------------------------


def _classify_execution(
    prep: SandboxPrepareResult,
    run_results: List[SandboxRunResult],
) -> ExecutionFeedback:
    """基于 prep / exit_code / stderr 关键字 / timed_out 的执行错误分类。

    判定优先级（顺序敏感）：
        0) 全部 exit 0 且 venv 成功 → NONE（成功）；
        1) 超时优先（疑似死循环，不可修复）；
        2) 依赖装不上（可修复，送回 coding 调整版本/换包）；
        3) stderr 关键字（硬件/数据缺失/未公开资源先于通用 runtime）；
        4) import / syntax / path（可修复）；
        5) 兜底 RUNTIME（可修复，给一次机会；MAX_FIX_LOOP_COUNT 上限拦截，缓解 R-S3-04）。
    """
    exit_ok = bool(prep.success) and all(r.exit_code == 0 for r in run_results)
    if exit_ok:
        return ExecutionFeedback(ErrorCategory.NONE, False, "执行成功", "", "")

    # 1) 超时优先（不可修复）。
    timed_out = next((r for r in run_results if r.timed_out), None)
    if timed_out is not None:
        return ExecutionFeedback(
            ErrorCategory.TIMEOUT,
            False,
            "执行超时（疑似死循环或资源不足）",
            "需人工核查脚本是否陷入死循环 / 缩小数据规模 / 增大超时阈值",
            _tail(timed_out.stderr or timed_out.stdout),
        )

    # 2) 依赖装不上（可修复）。
    if not prep.success and prep.install_failed_packages:
        return ExecutionFeedback(
            ErrorCategory.DEPENDENCY,
            True,
            f"依赖安装失败: {prep.install_failed_packages}",
            "调整依赖版本 / 更换等价包 / 移除不必要依赖后重试",
            _tail(prep.install_log or prep.error),
        )
    # venv 创建本身失败（无 failed_packages 但 prep 失败）→ 当依赖问题处理（可修复）。
    if not prep.success:
        return ExecutionFeedback(
            ErrorCategory.DEPENDENCY,
            True,
            f"环境准备失败: {prep.error or 'venv 创建/依赖安装失败'}",
            "检查 requirements 是否可解析 / 依赖版本是否冲突",
            _tail(prep.error or prep.install_log),
        )

    # 取第一条失败步骤的 stderr 做关键字匹配。
    failed = next((r for r in run_results if r.exit_code != 0), None)
    raw_stderr = (failed.stderr if failed else "") or ""
    rep = _tail(raw_stderr or (failed.stdout if failed else ""))
    stderr = raw_stderr.lower()

    # 3) 硬件/数据缺失/未公开资源（不可修复）先于通用 runtime。
    if any(k in stderr for k in _HARDWARE_KEYWORDS):
        return ExecutionFeedback(
            ErrorCategory.HARDWARE,
            False,
            "硬件/显存约束（CUDA OOM / 无可用 GPU）",
            "需更大显存 / 减小 batch size / 切换 CPU，超出自动修复范围",
            rep,
        )
    if any(k in stderr for k in _UNRESOLVED_RESOURCE_KEYWORDS):
        return ExecutionFeedback(
            ErrorCategory.UNRESOLVED_RESOURCE,
            False,
            "依赖论文未公开的资源（预训练权重 / 受限访问）",
            "需作者公开资源或申请访问，超出自动修复范围",
            rep,
        )
    if any(k in stderr for k in _DATA_MISSING_KEYWORDS):
        return ExecutionFeedback(
            ErrorCategory.DATA_MISSING,
            False,
            "数据集缺失，需人工下载",
            "按论文/README 指引下载数据集到指定目录后重试",
            rep,
        )

    # 4) import / syntax / path（可修复）。
    if "modulenotfounderror" in stderr or "importerror" in stderr:
        return ExecutionFeedback(
            ErrorCategory.IMPORT,
            True,
            "import 错误（缺包 / 模块路径错误）",
            "补充缺失依赖 / 修正 import 路径 / 检查包名拼写",
            rep,
        )
    if "syntaxerror" in stderr or "indentationerror" in stderr:
        return ExecutionFeedback(
            ErrorCategory.SYNTAX,
            True,
            "语法错误",
            "修正报错位置的语法 / 缩进",
            rep,
        )
    if "filenotfounderror" in stderr or "no such file" in stderr:
        # 数据缺失已在上面 _DATA_MISSING_KEYWORDS 拦截，这里是非数据集的路径错。
        return ExecutionFeedback(
            ErrorCategory.PATH,
            True,
            "文件路径错误（非数据集）",
            "修正脚本中的相对/绝对路径，确保引用文件存在",
            rep,
        )

    # 5) 兜底：通用运行时错误（可修复，给一次机会，靠上限拦截）。
    return ExecutionFeedback(
        ErrorCategory.RUNTIME,
        True,
        "运行时异常",
        "根据 stderr 尾部定位异常并做针对性修复",
        rep,
    )


# ---------------------------------------------------------------------------
# 步骤 4：metrics 三档解析（架构 §2.3.3，缓解 R-S3-05）
# ---------------------------------------------------------------------------

# 档 1 结构化标签 <METRICS>...</METRICS>（类比 react_base 的 <result> 标签范式）。
_METRICS_TAG_OPEN: str = "<METRICS>"
_METRICS_TAG_CLOSE: str = "</METRICS>"
_METRICS_TAG_PATTERN = re.compile(
    re.escape(_METRICS_TAG_OPEN) + r"(.*?)" + re.escape(_METRICS_TAG_CLOSE),
    re.DOTALL,
)


def _extract_metrics_block(stdout: str) -> Dict[str, Any]:
    """档 1：解析 stdout 中最后一个 <METRICS>{...}</METRICS> 块（取最后一个，容忍中途打印）。"""
    if not stdout:
        return {}
    matches = _METRICS_TAG_PATTERN.findall(stdout)
    for raw in reversed(matches):
        candidate = (raw or "").strip()
        if not candidate:
            continue
        try:
            parsed = json.loads(candidate)
        except (ValueError, TypeError):
            continue
        if isinstance(parsed, dict) and parsed:
            # 仅保留 value 为数值/字符串的扁平指标（防嵌套大对象污染对比表）。
            out: Dict[str, Any] = {}
            for k, v in parsed.items():
                if isinstance(v, (int, float, str, bool)):
                    out[str(k)] = v
            if out:
                return out
    return {}


def _regex_scan_metrics(stdout: str, metric_names: List[Any]) -> Dict[str, Any]:
    """档 2：按 paper_analysis.metrics 英文事实字段做锚点，正则扫 "name: 0.91" / "Acc = 91.2%" 等。"""
    if not stdout or not metric_names:
        return {}
    out: Dict[str, Any] = {}
    for name in metric_names:
        if not name or not isinstance(name, str):
            continue
        # 锚定指标名（大小写不敏感），允许 ": " / " = " / " " 分隔，值为数字（可带 %）。
        pat = re.compile(
            re.escape(name) + r"\s*[:=]?\s*([0-9]+(?:\.[0-9]+)?)\s*(%?)",
            re.IGNORECASE,
        )
        m = pat.search(stdout)
        if not m:
            continue
        try:
            value = float(m.group(1))
        except (ValueError, TypeError):
            continue
        if m.group(2) == "%":
            value = value / 100.0
        out[name] = value
    return out


def _llm_extract_metrics(
    stdout: str,
    metric_names: List[Any],
    state: GlobalState,
) -> Tuple[Dict[str, Any], int]:
    """档 3：LLM 抽取兜底（仅 exit 0 且 stdout 非空时由调用方触发）。

    返回 (metrics, calls_used)。calls_used 由 _parse_metrics 透传给 map_result 单点回写预算
    （must-fix-2）。LLM 调用任何失败都降级为空 metrics（不抛异常打断节点）。
    """
    # 局部 import 避免模块加载期与 llm_client 的潜在循环依赖，且测试可 patch 此函数。
    from core.llm_client import create_llm, resolve_llm_config

    metric_hint = ", ".join([n for n in metric_names if isinstance(n, str)]) or "(论文指标名未知)"
    snippet = stdout[-4000:] if len(stdout) > 4000 else stdout
    system = (
        "你是指标抽取器。从给定的程序标准输出中抽取数值型复现指标，"
        f"严格只输出一个 JSON 对象（形如 {{\"accuracy\": 0.91}}），不要解释。"
        "找不到任何指标时输出 {}。"
    )
    human = (
        f"论文关注的指标名（锚点，可作为键参考）：{metric_hint}\n"
        f"--- 程序标准输出（尾部）---\n{snippet}\n"
        f"--- 结束 ---\n请输出抽取到的指标 JSON。"
    )
    config = None
    try:
        # execution 不在节点级覆写白名单内，resolve_llm_config 自然回退 default。
        config = resolve_llm_config(state.get("llm_config_set"), "planning")
    except Exception:  # noqa: BLE001 - 配置解析失败则不抽取
        config = None
    if config is None:
        return {}, 0

    try:
        from langchain_core.messages import HumanMessage, SystemMessage

        llm = create_llm(config)
        resp = llm.invoke([SystemMessage(content=system), HumanMessage(content=human)])
        content = getattr(resp, "content", "") or ""
        if isinstance(content, list):
            content = "".join(
                c if isinstance(c, str) else (c.get("text") or "") if isinstance(c, dict) else ""
                for c in content
            )
        content = content.strip()
        # 容忍模型包 ```json fence```。
        if content.startswith("```"):
            content = content.strip("`")
            if content.lower().startswith("json"):
                content = content[4:]
            content = content.strip()
        parsed = json.loads(content) if content else {}
    except Exception as exc:  # noqa: BLE001 - 抽取失败降级空，仍计 1 次调用消耗
        logger.warning("[%s] metrics LLM 抽取兜底失败，降级空指标: %s", NODE_NAME, exc)
        return {}, 1

    out: Dict[str, Any] = {}
    if isinstance(parsed, dict):
        for k, v in parsed.items():
            if isinstance(v, (int, float, str, bool)):
                out[str(k)] = v
    return out, 1


def _parse_metrics(
    run_results: List[SandboxRunResult],
    plan: Optional[Dict[str, Any]],
    state: GlobalState,
) -> Tuple[Dict[str, Any], int]:
    """三档降级解析（结构化约定优先 → 正则兜底 → LLM 抽取兜底）。

    返回 (metrics, llm_calls_used)。llm_calls_used > 0 仅当档 3 LLM 抽取触发（must-fix-2）。
    """
    stdout = "\n".join((r.stdout or "") for r in run_results)

    # 档 1（首选）：结构化标签。
    block = _extract_metrics_block(stdout)
    if block:
        return block, 0

    # 档 2（兜底）：正则按 paper_analysis.metrics 英文事实字段扫描。
    metric_names = (state.get("paper_analysis") or {}).get("metrics") or []
    if not isinstance(metric_names, list):
        metric_names = []
    scanned = _regex_scan_metrics(stdout, metric_names)
    if scanned:
        return scanned, 0

    # 档 3（最后兜底）：LLM 抽取 —— 仅当全部 exit 0（值得抽）且 stdout 非空时触发。
    if run_results and all(r.exit_code == 0 for r in run_results) and stdout.strip():
        metrics, calls = _llm_extract_metrics(stdout, metric_names, state)
        return metrics, calls

    return {}, 0


# ---------------------------------------------------------------------------
# 步骤 1+2：sandbox 准备 + 执行步骤聚合
# ---------------------------------------------------------------------------


def _extract_requirements(plan: Optional[Dict[str, Any]]) -> List[str]:
    """从 reproduction_plan.environment 抽取显式依赖列表（容错多种形态）。"""
    if not isinstance(plan, dict):
        return []
    env = plan.get("environment")
    if not isinstance(env, dict):
        return []
    reqs: List[str] = []
    for key in ("dependencies", "requirements", "packages", "pip"):
        val = env.get(key)
        if isinstance(val, list):
            reqs.extend(str(x) for x in val if x)
        elif isinstance(val, str) and val.strip():
            reqs.append(val.strip())
    # 去重保序。
    seen: set = set()
    out: List[str] = []
    for r in reqs:
        if r not in seen:
            seen.add(r)
            out.append(r)
    return out


def _extract_command_str(step: Any) -> Optional[str]:
    """从 execution_step（dict 含 command 字段，或纯字符串）取出命令字符串。"""
    cmd_str: Optional[str] = None
    if isinstance(step, dict):
        cmd_str = step.get("command") or step.get("cmd") or step.get("run")
        if cmd_str is not None and not isinstance(cmd_str, str):
            cmd_str = str(cmd_str)
    elif isinstance(step, str):
        cmd_str = step
    if not cmd_str or not cmd_str.strip():
        return None
    return cmd_str.strip()


def _split_top_level(cmd_str: str) -> List[Tuple[List[str], str]]:
    """把一个 command 字符串按**顶层** `&&` / `;` 拆成多条子命令（禁 shell，shlex 保证引号内不误拆）。

    返回 List[(argv, connector)]，connector 为**该子命令之前**的连接符：
    第一条恒为 "" ；其后每条为 "&&"（前置非 0 短路）或 ";"（无条件顺序）。
    shlex.split 已剥离引号，故引号内的 `&&` / `;` 不会被当作连接符（它们成为单个 token）。

    解析失败（未闭合引号等）退化为整条 whitespace split 单子命令，交由下游自然报错。
    """
    import shlex

    try:
        tokens = shlex.split(cmd_str)
    except ValueError:
        toks = cmd_str.split()
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


def _step_to_command(step: Any, python_exe: str) -> Optional[List[Tuple[List[str], str]]]:
    """把一个 execution_step 转为子命令序列 List[(argv, connector)]，供执行循环逐条跑。

    禁 shell=True：每条子命令一律 argv 列表形式。在**解析期**（非 shell）安全处理一小撮
    shell 语义：顶层 `&&` / `;` 拆分（见 _split_top_level）。裸 python/pip 改写与 cd/source/
    glob 等 token 级语义在执行循环里按 current_dir 处理（_apply_subcommand_semantics）。

    connector：第一条 "" ；其后 "&&"（短路）或 ";"（顺序）。
    """
    cmd_str = _extract_command_str(step)
    if not cmd_str:
        return None
    subs = _split_top_level(cmd_str)
    return subs or None


# cd 后续步骤都假设在新目录里——current_dir 跨子命令/跨 step 持续（模拟连续 shell 会话）。
_GLOB_CHARS = ("*", "?", "[")


def _rewrite_interpreter(argv: List[str], python_exe: str) -> List[str]:
    """裸 python/python3/py -> venv python_exe；裸 pip -> python_exe -m pip（避免落到系统 pip）。"""
    if not argv:
        return argv
    head = argv[0]
    if head in ("python", "python3", "py"):
        return [python_exe] + argv[1:]
    if head in ("pip", "pip3"):
        return [python_exe, "-m", "pip"] + argv[1:]
    return argv


def _expand_globs(argv: List[str], cwd: str) -> List[str]:
    """对含通配符的 token 用 Python glob 在 cwd 下展开（非 shell）。展开为空保留原 token（让命令自然报错）。"""
    import glob as _glob
    import os as _os

    out: List[str] = []
    for tok in argv:
        if any(c in tok for c in _GLOB_CHARS):
            if _os.path.isabs(tok):
                matches = sorted(_glob.glob(tok))
            else:
                # root_dir 保证相对模式相对 current_dir 展开，返回的也是相对路径（与原命令语义一致）。
                matches = sorted(_glob.glob(tok, root_dir=cwd))
            if matches:
                out.extend(matches)
            else:
                out.append(tok)  # 展开为空：保留原样，不静默吞
        else:
            out.append(tok)
    return out


def _resolve_cd(target: Optional[str], current_dir: str) -> str:
    """把 `cd <target>` 相对 current_dir 解析为绝对路径，并经 workspace 边界校验。

    Raises:
        SandboxCreationError: 解析后越出 WORKSPACE_DIR（绝不允许 cd 逃逸）。
    """
    import os as _os

    if not target:
        # 裸 `cd`：退回 work_dir 语义不明确，这里保持当前目录（不做 HOME 跳转，避免逃逸）。
        return current_dir
    candidate = target if _os.path.isabs(target) else _os.path.join(current_dir, target)
    new_path = Path(candidate)
    if not _is_within_workspace(new_path):
        raise SandboxCreationError(
            "cd 目标越界",
            f"cd {target} 解析为 {new_path} 不在 WORKSPACE_DIR 之下",
        )
    return str(new_path.resolve())


def _run_step_subcommands(
    step: Any,
    python_exe: str,
    current_dir: str,
    extra_env: Optional[Dict[str, str]] = None,
) -> Tuple[List[SandboxRunResult], str]:
    """执行一个 step 的子命令序列（顶层 && / ; 拆分后），返回 (run_results, 更新后的 current_dir)。

    语义（解析期，非 shell）：
      - connector "&&"：前一条非 0/超时则短路，停止该 step 剩余子命令；
      - connector ";"：无条件顺序执行；
      - `cd <dir>`：更新 current_dir（经 workspace 边界校验），不作为子进程执行；越界拒绝该 step；
      - `source`/`.`：丢弃（venv 已由 prepare_venv 建好，python_exe 已指向 venv）；
      - 裸 python/pip：改写为 venv 解释器；通配符：glob 展开（空则保留原样）。
    每条子命令以 current_dir 作 run_in_venv 的 work_dir（跨子命令、跨 step 持续）。

    extra_env（sp4 E1 新增，保持向后兼容默认 None）：透传给每条 run_in_venv 子进程，
    在沙箱白名单环境之上显式注入（凭证注入唯一入口，architecture §9.3）。
    """
    subs = _step_to_command(step, python_exe)
    results: List[SandboxRunResult] = []
    if not subs:
        return results, current_dir

    prev_failed = False
    for argv, connector in subs:
        # && 短路：前一条失败则停止该 step 剩余子命令。
        if connector == "&&" and prev_failed:
            break
        if not argv:
            continue

        head = argv[0]
        # source / . 激活 venv：丢弃（无需执行）。
        if head in ("source", "."):
            continue
        # cd：更新 current_dir，不执行子进程。
        if head == "cd":
            target = argv[1] if len(argv) > 1 else None
            try:
                current_dir = _resolve_cd(target, current_dir)
            except SandboxCreationError as exc:
                logger.warning("[%s] cd 越界拒绝: %s", NODE_NAME, exc)
                results.append(SandboxRunResult(
                    exit_code=-1, stdout="", stderr=str(exc),
                    duration_seconds=0.0, timed_out=False,
                    output_truncated=False, command=argv,
                ))
                prev_failed = True
                if connector != ";":  # 默认 cd 失败短路（& 风险），仅显式 ; 才续跑
                    break
            continue

        argv = _rewrite_interpreter(argv, python_exe)
        argv = _expand_globs(argv, current_dir)

        try:
            rr = run_in_venv(python_exe, argv, current_dir, extra_env=extra_env)
        except SandboxCreationError as exc:
            logger.warning("[%s] run_in_venv 越界: %s", NODE_NAME, exc)
            rr = SandboxRunResult(
                exit_code=-1, stdout="", stderr=str(exc),
                duration_seconds=0.0, timed_out=False,
                output_truncated=False, command=argv,
            )
        results.append(rr)
        prev_failed = (rr.exit_code != 0 or rr.timed_out)

    return results, current_dir


# ---------------------------------------------------------------------------
# E1（S4-04）：sandbox 工具化 —— prepare_environment / run_in_sandbox + 结果收集器
# ---------------------------------------------------------------------------
# 设计权威：dev-plan §4 任务 E1 + architecture §3.3 工具层 / §3.4 关键注记 / §9.3。
# 确定性辅助函数（_step_to_command / _rewrite_interpreter / _expand_globs /
# _resolve_cd / _run_step_subcommands）保留为工具内部实现——agent 只管"跑哪条"。


_PREPARE_TOOL_NAME: str = "prepare_environment"
_RUN_TOOL_NAME: str = "run_in_sandbox"

# 工具执行失败 ToolMessage 的典型前缀（react_base tool_executor 兜底写入），
# messages 回读时过滤（BUG-S1-03 范式：仅回填成功结果）。
_FAILED_TOOL_MESSAGE_PREFIXES: Tuple[str, ...] = ("Error in ", "tool ", "unknown tool")


@dataclass
class _SandboxRunCollector:
    """R-S4-01 结果收集器：工具体内真跑 sandbox 后 append **真实 dataclass 结果**。

    编排层收尾读收集器（真实 exit_code/stderr）而非 agent 自述——agent 无法伪造
    成功。

    R-S4-10 实证边界（B2 报告 2026-07-04）：本收集器由 ``_run_execution_agent``
    每次进入时新建；``request_user_input`` interrupt#3 → resume 会重跑节点函数体、
    重建收集器，**pre-interrupt 的收集值会丢失**（而子图 messages 经 checkpoint
    恢复是完整的）。因此跨 interrupt 的完整执行序列以子图 messages 回读为权威
    （``_rebuild_*_from_messages``），收集器仅对其覆盖的尾段提供全保真（未截断
    stdout/stderr）结果——见 ``_merge_with_collector``。
    """

    prep_results: List[SandboxPrepareResult] = field(default_factory=list)
    run_results: List[SandboxRunResult] = field(default_factory=list)


def _tool_json(payload: Dict[str, Any]) -> str:
    """工具返回 JSON 统一序列化（BUG-S1-02 治理：禁 str(dict)；sort_keys 保证
    Prompt Cache 字节级幂等）。"""
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)


def _tool_error_json(message: str, **extra: Any) -> str:
    """工具异常 → 结构化错误 JSON（tool_error=True 标记，messages 回读时据此跳过，
    与"prepare_venv 返回的业务失败"区分——后者是合法结果、进收集器）。"""
    payload: Dict[str, Any] = {"tool_error": True, "error": mask_value(message) or ""}
    payload.update(extra)
    return _tool_json(payload)


def _merge_extra_env(extra_env: Optional[Dict[str, str]]) -> Dict[str, str]:
    """工具层兜底保证 extra_env 无条件含 GIT_TERMINAL_PROMPT=0（R-S4-08：git 认证
    失败立即返回而非挂起等 stdin；CP-E1-4）。调用方（build_credential_env）通常
    已含，这里是防御性收口。"""
    return {"GIT_TERMINAL_PROMPT": "0", **(extra_env or {})}


def _run_result_to_payload(rr: SandboxRunResult) -> Dict[str, Any]:
    """单条 SandboxRunResult → 工具返回 JSON 条目。

    stdout/stderr 返回前 ``mask_value``（C1 同范式：ToolMessage 虽在子图私有
    messages，但 agent 可能把内容复述进 <result> 进而入 state，必须源头 mask）；
    取尾部 ~2000 字符（错误栈 / <METRICS> 行均在末尾）。
    """
    return {
        "command": [str(c) for c in (rr.command or [])],
        "exit_code": rr.exit_code,
        "stdout_tail": mask_value(_tail(rr.stdout)) or "",
        "stderr_tail": mask_value(_tail(rr.stderr)) or "",
        "timed_out": bool(rr.timed_out),
        "truncated": bool(rr.output_truncated),
        "duration_seconds": rr.duration_seconds,
    }


def make_prepare_environment_tool(
    work_dir: str,
    plan: Optional[Dict[str, Any]],
    collector: _SandboxRunCollector,
    extra_env: Optional[Dict[str, str]] = None,
):
    """工厂：包 ``prepare_venv`` 为 LangChain tool（真实结果 append 收集器）。

    工具异常一律 try/except 转结构化错误 JSON + WARNING，不炸子图（CP-E1-5）。
    """
    merged_env = _merge_extra_env(extra_env)

    @tool
    def prepare_environment() -> str:
        """在工作目录下创建（或复用）隔离 venv 并安装复现计划声明的依赖。

        在执行任何 run_in_sandbox 命令之前必须先调用本工具一次。返回 JSON：
        success / python_exe / venv_dir / install_failed_packages / error。
        依赖装不全时 success=false 且 install_failed_packages 列出失败项，
        可据此用 run_in_sandbox 执行 pip install 兜底或调整依赖后继续。
        """
        try:
            prep = prepare_venv(
                work_dir=work_dir,
                requirements=_extract_requirements(plan),
                requirements_files=None,
                extra_env=merged_env,
            )
        except SandboxCreationError as exc:
            logger.warning(
                "[%s] %s 工具 prepare_venv 失败（转结构化错误，不炸子图）: %s",
                NODE_NAME, _PREPARE_TOOL_NAME, exc,
            )
            return _tool_error_json(f"SandboxCreationError: {exc}", success=False)
        except Exception as exc:  # noqa: BLE001 - OSError 等兜底，绝不让工具异常杀掉子图
            logger.warning(
                "[%s] %s 工具异常（转结构化错误，不炸子图）: %s: %s",
                NODE_NAME, _PREPARE_TOOL_NAME, type(exc).__name__, exc,
            )
            return _tool_error_json(f"{type(exc).__name__}: {exc}", success=False)

        collector.prep_results.append(prep)  # R-S4-01：真实 dataclass 进收集器
        return _tool_json({
            "success": bool(prep.success),
            "python_exe": prep.python_exe,
            "venv_dir": prep.venv_dir,
            "install_failed_packages": [str(p) for p in (prep.install_failed_packages or [])],
            "error": (mask_value(_tail(prep.error)) or None) if prep.error else None,
        })

    return prepare_environment


def make_run_in_sandbox_tool(
    work_dir: str,
    collector: _SandboxRunCollector,
    extra_env: Optional[Dict[str, str]] = None,
    python_exe_ref: Optional[Dict[str, Optional[str]]] = None,
):
    """工厂：包 ``run_in_venv`` 为 LangChain tool（含确定性解析改写 + 收集器）。

    python_exe 解析优先级（工具内确定性，agent 无需感知）：
        1. 收集器内最近一次成功 prepare 的 python_exe（本次进入内正常路径）；
        2. ``python_exe_ref["python_exe"]``（调用方显式提供）；
        3. ``work_dir/.venv`` 已存在（pyvenv.cfg 探测）→ 确定性推导（R-S4-10：
           interrupt resume 后收集器重建为空、但 venv 已在 pre-interrupt 建好）；
        4. 均无 → 结构化错误 JSON 提示 agent 先调 prepare_environment。

    ``cd`` 引起的 current_dir 变化在工具闭包内跨调用持续（模拟连续 shell 会话）；
    resume 重建后回落 work_dir（可接受：agent 通常在命令内显式 cd）。
    """
    merged_env = _merge_extra_env(extra_env)
    session: Dict[str, str] = {"current_dir": work_dir}
    ref: Dict[str, Optional[str]] = python_exe_ref if python_exe_ref is not None else {}

    def _resolve_python_exe() -> Optional[str]:
        for prep in reversed(collector.prep_results):
            if prep.python_exe:
                return str(prep.python_exe)
        if ref.get("python_exe"):
            return str(ref["python_exe"])
        venv_dir = Path(work_dir) / ".venv"
        if (venv_dir / "pyvenv.cfg").exists():
            return str(_venv_python_exe(venv_dir))
        return None

    @tool
    def run_in_sandbox(command: str) -> str:
        """在已准备好的沙箱 venv 中执行一条命令，返回真实执行结果。

        入参为单条命令字符串（如 "python train.py --epochs 1"）。支持顶层
        `&&` / `;` 复合命令、`cd`（限工作区内，越界拒绝）、裸 python/pip 自动
        改写为 venv 解释器、通配符展开；不经过 shell。返回 JSON：exit_code
        （首个非 0 子命令的退出码，全 0 则 0）/ timed_out / results（逐子命令
        command、exit_code、stdout_tail、stderr_tail）。请根据 exit_code 与
        stderr_tail 决定下一步，一次只执行一条命令。
        """
        try:
            python_exe = _resolve_python_exe()
            if not python_exe:
                logger.warning(
                    "[%s] %s 工具：沙箱环境尚未准备（无可用 venv python），提示先 prepare",
                    NODE_NAME, _RUN_TOOL_NAME,
                )
                return _tool_error_json(
                    "沙箱环境尚未准备，请先调用 prepare_environment 创建 venv",
                    exit_code=-1, results=[], timed_out=False,
                )
            results, session["current_dir"] = _run_step_subcommands(
                {"command": command},
                python_exe,
                session["current_dir"],
                extra_env=merged_env,
            )
        except SandboxCreationError as exc:
            logger.warning(
                "[%s] %s 工具越界/沙箱失败（转结构化错误，不炸子图）: %s",
                NODE_NAME, _RUN_TOOL_NAME, exc,
            )
            return _tool_error_json(
                f"SandboxCreationError: {exc}", exit_code=-1, results=[], timed_out=False,
            )
        except Exception as exc:  # noqa: BLE001 - OSError 等兜底，绝不让工具异常杀掉子图
            logger.warning(
                "[%s] %s 工具异常（转结构化错误，不炸子图）: %s: %s",
                NODE_NAME, _RUN_TOOL_NAME, type(exc).__name__, exc,
            )
            return _tool_error_json(
                f"{type(exc).__name__}: {exc}", exit_code=-1, results=[], timed_out=False,
            )

        collector.run_results.extend(results)  # R-S4-01：真实 dataclass 进收集器
        if not results:
            return _tool_error_json(
                "命令为空或无可执行子命令", exit_code=-1, results=[], timed_out=False,
            )
        overall = next((r.exit_code for r in results if r.exit_code != 0), 0)
        return _tool_json({
            "exit_code": overall,
            "timed_out": any(r.timed_out for r in results),
            "results": [_run_result_to_payload(r) for r in results],
        })

    return run_in_sandbox


# ---------------------------------------------------------------------------
# E2（S4-03）：_run_execution_agent —— 内嵌 ReAct 子图装配（首个裸 create_react_subgraph 消费者）
# ---------------------------------------------------------------------------
# 设计权威：dev-plan §4 任务 E2（含 wrapper 内建项复刻清单）+ architecture §3.3
# 子图层 / §3.4 / §4.3。不经 _make_react_wrapper：预算扣减由编排层（E3
# _map_execution_result）按本函数返回的 rounds_used 单点显式做（落点 B）。


# Prompt Cache 方案 A：主体常量，零论文级 / 任务级动态变量（CP-E2-1 字节级一致断言）。
# 动态上下文（work_dir / execution_steps / 修复回合反馈）一律走 HumanMessage。
_EXECUTION_SYSTEM_PROMPT_BODY = """你是复现执行工程师，负责在隔离沙箱中执行论文复现代码并收集真实运行结果。HumanMessage 提供 work_dir、execution_steps 与环境依赖信息；修复回合时额外提供上一轮错误摘要。

可用工具：
- prepare_environment(): 在工作目录下创建隔离 venv 并安装复现计划声明的依赖。任何 run_in_sandbox 之前必须先调用一次。
- run_in_sandbox(command): 在沙箱 venv 中执行一条命令，返回真实 exit_code 与 stdout/stderr 尾部。支持顶层 && / ; 复合与 cd（限工作区内），裸 python/pip 自动指向 venv 解释器。
- request_user_input(question, is_sensitive, purpose_key): 缺少继续执行所需的信息（凭证/参数/路径）时向用户索要一条信息。必须单独一轮调用（不与其他工具放在同一轮 tool_calls），且尽量在执行训练等重活之前问。

工作纪律：
1. 先调 prepare_environment 准备环境；依赖装不全（install_failed_packages 非空）时可用 run_in_sandbox 执行 pip install 兜底或调整版本。
2. 按 execution_steps 逐条执行：每条命令跑完先检查返回 JSON 的 exit_code / stderr_tail 再决定下一步；一次只跑一条命令。
3. 识别到认证失败 / 缺凭证迹象（authentication failed、401 unauthorized、403 forbidden、could not read username、terminal prompts disabled 等）时，立即调 request_user_input（is_sensitive=true，给出合适的 purpose_key，如 "git_credential:github.com" / "hf_token"）索取凭证后重试，不要反复盲试。
4. 命令失败时可做少量有把握的就地修正（如补装缺失包、修正相对路径）后重试；无法解决时如实收尾，交由编排层分类处理。
5. 预算意识：max_rounds=10；不要重复执行同一条命令空转。

成功判定纪律（强约束）：
- 你不判定复现是否成功——成功与否由编排层基于工具执行的真实 exit_code 与指标做确定性判定。
- 不得在结果中宣称"复现成功"；只如实汇报执行了哪些命令、各自 exit_code 与观察到的现象。

输出要求：
- 执行收尾时必须在 <result>...</result> 标签内输出严格 JSON，字段如下：
  {
    "steps_attempted": int,        // 实际执行的命令条数
    "all_exit_zero": bool,         // 已执行命令是否全部 exit_code=0（如实填写）
    "summary": str,                // 执行过程与结果的中文如实描述
    "notes": str | null            // 降级/遗留问题等（可选）
  }
- 不得捏造未执行的命令；不要在 <result> 之外再夹杂其它 JSON 块。
"""


@dataclass
class ExecAgentOutput:
    """``_run_execution_agent`` 的轻量返回结构（喂 E3 编排层收尾 + 预算扣减）。

    - prep / run_results：工具执行的**真实** sandbox 结果（收集器 + messages 回读
      合并，非 agent 自述）；prep 取最后一次 prepare（agent 可能重试）；
    - rounds_used：子图实际 round（与 wrapper 同口径 max(1, round)；降级路径 0）；
    - llm_calls：子图内 LLM 调用数（= rounds_used，喂 _dev_loop_llm_calls 累加）。
    """

    prep: Optional[SandboxPrepareResult]
    run_results: List[SandboxRunResult]
    rounds_used: int
    llm_calls: int


def _format_execution_task_context() -> str:
    """system prompt 尾部稳定段落（常量，无任何动态变量；与 coding 范式结构对齐）。"""
    payload: Dict[str, Any] = {"node": NODE_NAME}
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)


def _build_execution_system_prompt() -> str:
    """组装 execution 的 system prompt（Prompt Cache 方案 A，CP-E2-1）。

    主体 + 尾部段落均为常量：不同任务 / 不同论文间**整条 SystemMessage 字节级一致**
    （比 CP-F3-1 更强——execution 连尾部都无动态变量，动态上下文全走 HumanMessage）。
    """
    return (
        _EXECUTION_SYSTEM_PROMPT_BODY
        + "\n--- 当前任务上下文 ---\n"
        + _format_execution_task_context()
    )


def _build_execution_agent_context(
    state: GlobalState,
    work_dir: str,
    plan: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """curated 动态上下文（HumanMessage 通道，json.dumps sort_keys 字节幂等）。

    修复回合（fix_loop_count > 0 且已有上一轮 execution_result）注入摘要级反馈，
    帮助 agent 避开上一轮已知错误（stderr 尾部裁剪防撑爆 context）。
    """
    plan = plan if isinstance(plan, dict) else {}
    payload: Dict[str, Any] = {
        "work_dir": work_dir,
        "execution_steps": plan.get("execution_steps"),
        "environment": plan.get("environment"),
    }
    fix_count = state.get("fix_loop_count", 0) or 0
    exec_result = state.get("execution_result")
    if exec_result and fix_count > 0:
        errors = list(exec_result.get("errors") or [])
        logs = exec_result.get("logs") or ""
        if not isinstance(logs, str):
            logs = str(logs)
        payload["fix_round"] = fix_count
        payload["last_error_summary"] = {
            "errors": [e if isinstance(e, str) else str(e) for e in errors],
            "stderr_tail": _tail(logs),
        }
    return payload


def _tool_message_text(msg: ToolMessage) -> str:
    """提取 ToolMessage 文本内容（兼容 content parts 形式）。"""
    content = getattr(msg, "content", "")
    if isinstance(content, list):
        content = "".join(
            c if isinstance(c, str) else (c.get("text") or "") if isinstance(c, dict) else ""
            for c in content
        )
    return content if isinstance(content, str) else str(content)


def _parse_tool_message_payload(text: str) -> Optional[Dict[str, Any]]:
    """解析工具 ToolMessage 的 JSON 内容（容忍 _truncate_tool_result 截断）。

    失败 ToolMessage（react_base 兜底前缀）与空内容返回 None（BUG-S1-03 范式）。
    """
    stripped = (text or "").strip()
    if not stripped:
        return None
    if any(stripped.startswith(p) for p in _FAILED_TOOL_MESSAGE_PREFIXES):
        return None
    try:
        parsed = json.loads(stripped)
        if isinstance(parsed, dict):
            return parsed
    except (TypeError, ValueError):
        pass
    # 剥离截断后缀再修复（BUG-S1-02 截断 JSON 修复范式）。
    trunc_idx = stripped.rfind("... [truncated at")
    candidate = stripped[:trunc_idx].rstrip() if trunc_idx > 0 else stripped
    repaired = _repair_truncated_json_prefix(candidate)
    if repaired is not None:
        try:
            parsed = json.loads(repaired)
            if isinstance(parsed, dict):
                return parsed
        except (TypeError, ValueError):
            return None
    return None


def _rebuild_run_results_from_messages(
    react_messages: Optional[List[BaseMessage]],
) -> List[SandboxRunResult]:
    """从子图 messages 回读 run_in_sandbox 的执行序列（R-S4-10 权威通道）。

    仅回填成功 ToolMessage（过滤 react_base 失败前缀与 tool_error 结构化错误）；
    存在目标 ToolMessage 但一条都解析不出时打 WARNING（陷阱 3：禁静默吞错）。
    保真度注记：回读条目的 stdout/stderr 为 mask + 尾部截断后的文本（工具返回
    JSON 的 tail），弱于收集器的全量原文——故 _merge_with_collector 对收集器
    覆盖的尾段优先用收集器。
    """
    out: List[SandboxRunResult] = []
    saw_tool_message = False
    for msg in react_messages or []:
        if not isinstance(msg, ToolMessage) or getattr(msg, "name", None) != _RUN_TOOL_NAME:
            continue
        saw_tool_message = True
        payload = _parse_tool_message_payload(_tool_message_text(msg))
        if not isinstance(payload, dict) or payload.get("tool_error"):
            continue
        for entry in payload.get("results") or []:
            if not isinstance(entry, dict):
                continue
            try:
                out.append(SandboxRunResult(
                    exit_code=int(entry.get("exit_code", -1)),
                    stdout=str(entry.get("stdout_tail") or ""),
                    stderr=str(entry.get("stderr_tail") or ""),
                    duration_seconds=float(entry.get("duration_seconds") or 0.0),
                    timed_out=bool(entry.get("timed_out")),
                    output_truncated=bool(entry.get("truncated")),
                    command=[str(c) for c in (entry.get("command") or [])],
                ))
            except (TypeError, ValueError) as exc:
                logger.warning(
                    "[%s] %s ToolMessage 回读条目字段异常，跳过: %s",
                    NODE_NAME, _RUN_TOOL_NAME, exc,
                )
    if saw_tool_message and not out:
        logger.warning(
            "[%s] 存在 %s ToolMessage 但未回读出任何成功执行记录"
            "（全部为失败/tool_error/无法解析）", NODE_NAME, _RUN_TOOL_NAME,
        )
    return out


def _rebuild_prep_results_from_messages(
    react_messages: Optional[List[BaseMessage]],
) -> List[SandboxPrepareResult]:
    """从子图 messages 回读 prepare_environment 结果序列（字段保真弱于收集器：
    install_log / pip_exe / env_info 不在工具返回 JSON 内，回读为空占位）。"""
    out: List[SandboxPrepareResult] = []
    saw_tool_message = False
    for msg in react_messages or []:
        if not isinstance(msg, ToolMessage) or getattr(msg, "name", None) != _PREPARE_TOOL_NAME:
            continue
        saw_tool_message = True
        payload = _parse_tool_message_payload(_tool_message_text(msg))
        if not isinstance(payload, dict) or payload.get("tool_error"):
            continue
        if "success" not in payload:
            continue
        out.append(SandboxPrepareResult(
            success=bool(payload.get("success")),
            venv_dir=str(payload.get("venv_dir") or ""),
            python_exe=str(payload.get("python_exe") or ""),
            pip_exe="",
            env_info={},
            install_log="",
            install_failed_packages=[
                str(p) for p in (payload.get("install_failed_packages") or [])
            ],
            error=(str(payload["error"]) if payload.get("error") else None),
        ))
    if saw_tool_message and not out:
        logger.warning(
            "[%s] 存在 %s ToolMessage 但未回读出任何成功记录"
            "（全部为失败/tool_error/无法解析）", NODE_NAME, _PREPARE_TOOL_NAME,
        )
    return out


def _merge_with_collector(
    rebuilt: List[Any],
    collected: List[Any],
    label: str,
) -> List[Any]:
    """合并 messages 回读序列（权威、跨 interrupt 完整）与收集器（尾段全保真）。

    机理（B2 实证）：resume 后收集器被重建，只含 post-interrupt 的尾段结果，且
    与 messages 回读序列的尾段按序一一对应；无 interrupt 时收集器覆盖全序列。
        - len(collected) >= len(rebuilt)：收集器覆盖全序列（常规路径）→ 全用收集器；
        - len(collected) <  len(rebuilt)：疑似 interrupt resume（R-S4-10）→ 前段用
          messages 回读补全 + 尾段用收集器，打 WARNING 留痕。
    """
    if not rebuilt:
        return list(collected)
    k = len(collected)
    if k >= len(rebuilt):
        if k > len(rebuilt):
            logger.warning(
                "[%s] %s 收集器条数(%d) > messages 回读条数(%d)"
                "（部分 ToolMessage 截断不可解析），以收集器为准",
                NODE_NAME, label, k, len(rebuilt),
            )
        return list(collected)
    if k == 0:
        return list(rebuilt)
    logger.warning(
        "[%s] %s 收集器缺失前段（%d/%d，疑似 interrupt resume 重建收集器，R-S4-10），"
        "前 %d 条以 messages 回读补全（尾部截断保真度）",
        NODE_NAME, label, k, len(rebuilt), len(rebuilt) - k,
    )
    return list(rebuilt[: len(rebuilt) - k]) + list(collected)


def _run_execution_agent(
    state: GlobalState,
    work_dir: str,
    plan: Optional[Dict[str, Any]],
) -> ExecAgentOutput:
    """内嵌 ReAct 子图跑"步骤 1+2 的自适应执行"，返回真实 sandbox 结果原料。

    装配纪律（wrapper 内建项手工复刻，dev-plan E2 清单逐项）：
        - LLM 路由注入：resolve_llm_config(state["llm_config_set"], "execution")
          → context["_llm"]（子图 _bind_llm 硬依赖）；
        - 消息装配（Prompt Cache 方案 A）：SystemMessage = 稳定常量；HumanMessage =
          动态上下文 json.dumps(sort_keys=True, ensure_ascii=False, default=str)；
        - ReActState 初始化 + rounds 提取（max(1, round)，与 wrapper 同口径）；
        - 重试层：create_react_subgraph 内部已接 invoke_with_retry，自动获得。

    异常语义：
        - GraphBubbleUp（interrupt#3 / ParentCommand）**直通上浮**——LangGraph 靠它
          暂停主图，绝不捕获（BUG-S4-B1-01 同一条红线）；
        - 其余任何异常 → WARNING + 空结果集降级（编排层对空 run_results 走既有
          降级分类路径，不炸节点；rounds_used=0 不扣预算）。
    """
    collector = _SandboxRunCollector()
    try:
        # 装配项 1：LLM 路由注入（缺 llm_config_set → KeyError → 降级路径 + WARNING）。
        llm = create_llm(resolve_llm_config(state["llm_config_set"], NODE_NAME))

        # 凭证 extra_env（architecture §9.3：.secrets → build_credential_env，
        # 无条件含 GIT_TERMINAL_PROMPT=0；工具工厂内再防御性收口一次）。
        extra_env = build_credential_env(load_all_secrets())
        python_exe_ref: Dict[str, Optional[str]] = {"python_exe": None}
        tools = [
            make_prepare_environment_tool(work_dir, plan, collector, extra_env),
            make_run_in_sandbox_tool(work_dir, collector, extra_env, python_exe_ref),
            request_user_input,  # interrupt#3（B2 门禁已过，2026-07-04）
        ]

        # 装配项 2：消息装配（Prompt Cache 方案 A）。
        system_prompt = _build_execution_system_prompt()
        context = _build_execution_agent_context(state, work_dir, plan)
        initial_messages: List[BaseMessage] = [SystemMessage(content=system_prompt)]
        human_text = json.dumps(context, ensure_ascii=False, sort_keys=True, default=str)
        initial_messages.append(HumanMessage(content=human_text))

        subgraph = create_react_subgraph(
            node_name=NODE_NAME,
            system_prompt=system_prompt,
            tools=tools,
            max_rounds=REACT_MAX_ROUNDS_EXECUTION,
        )
        # 装配项 3：ReActState 初始化。
        initial: Dict[str, Any] = {
            "messages": initial_messages,
            "round": 0,
            "max_rounds": REACT_MAX_ROUNDS_EXECUTION,
            "status": "reasoning",
            "result": None,
            "context": {"_llm": llm},
        }
        final_state = subgraph.invoke(initial)
    except GraphBubbleUp:
        # interrupt#3（request_user_input）等 LangGraph 控制流必须直通上浮，
        # 交由 LangGraph 暂停主图；resume 时本函数体重跑、子图从 checkpoint 恢复。
        raise
    except Exception as exc:  # noqa: BLE001 - 子图任何异常降级（planning 同范式）
        logger.warning(
            "[%s] execution ReAct 子图执行失败，降级空结果集: %s: %s",
            NODE_NAME, type(exc).__name__, exc,
        )
        return ExecAgentOutput(prep=None, run_results=[], rounds_used=0, llm_calls=0)

    final_messages = (
        final_state.get("messages") if isinstance(final_state, dict) else None
    )
    # 装配项 4：rounds 提取（与 wrapper 同口径，喂 E3 单点扣减）。
    rounds_used = (
        max(1, int(final_state.get("round", 0) or 0))
        if isinstance(final_state, dict) else 1
    )

    # R-S4-10：messages 回读为权威序列（跨 interrupt 完整），收集器提供尾段全保真。
    run_results = _merge_with_collector(
        _rebuild_run_results_from_messages(final_messages),
        collector.run_results,
        "run_results",
    )
    prep_results = _merge_with_collector(
        _rebuild_prep_results_from_messages(final_messages),
        collector.prep_results,
        "prep_results",
    )
    prep = prep_results[-1] if prep_results else None

    logger.info(
        "[%s] execution agent 完成: rounds=%d, prep_success=%s, run_results=%d",
        NODE_NAME, rounds_used,
        (prep.success if prep is not None else None), len(run_results),
    )
    return ExecAgentOutput(
        prep=prep,
        run_results=run_results,
        rounds_used=rounds_used,
        llm_calls=rounds_used,
    )


# ---------------------------------------------------------------------------
# 步骤 5：ExecutionResult 构造 + B 档 success 判定（架构 §2.3.5，Q-S3-01）
# ---------------------------------------------------------------------------


def _aggregate_logs(
    prep: SandboxPrepareResult,
    run_results: List[SandboxRunResult],
) -> str:
    """聚合 install_log + 各步骤 stdout/stderr（受 sandbox output_truncated 护栏约束）。"""
    parts: List[str] = []
    if prep.install_log:
        parts.append(f"[install_log]\n{prep.install_log}")
    for i, r in enumerate(run_results):
        cmd = " ".join(r.command) if isinstance(r.command, (list, tuple)) else str(r.command)
        head = f"[step#{i} exit={r.exit_code} timed_out={r.timed_out} cmd={cmd}]"
        body_parts = [head]
        if r.stdout:
            body_parts.append(f"[stdout]\n{r.stdout}")
        if r.stderr:
            body_parts.append(f"[stderr]\n{r.stderr}")
        parts.append("\n".join(body_parts))
    return "\n\n".join(parts)


def _build_execution_result(
    prep: SandboxPrepareResult,
    run_results: List[SandboxRunResult],
    feedback: ExecutionFeedback,
    metrics: Dict[str, Any],
    work_dir: str,
) -> ExecutionResult:
    """构造 ExecutionResult，B 档 success = (exit 全 0) and len(metrics) >= 1。"""
    exit_ok = bool(prep.success) and all(r.exit_code == 0 for r in run_results)
    success = bool(exit_ok and len(metrics) >= 1)

    # artifacts 收集（越界等异常不应炸节点）。
    artifacts: List[str] = []
    try:
        artifacts = collect_artifacts(work_dir)
    except SandboxCreationError as exc:
        logger.warning("[%s] collect_artifacts 越界跳过: %s", NODE_NAME, exc)
    except Exception as exc:  # noqa: BLE001 - 产物收集失败不阻断
        logger.warning("[%s] collect_artifacts 失败: %s", NODE_NAME, exc)

    errors: List[str] = []
    if not success:
        errors = [f"[error_category={feedback.category.value}] {feedback.summary}"]

    return ExecutionResult(
        success=success,
        metrics=metrics,
        logs=_aggregate_logs(prep, run_results),
        errors=errors,
        artifacts=artifacts,
        runtime_seconds=float(sum(r.duration_seconds for r in run_results)),
        environment_info=dict(prep.env_info or {}),
    )


# ---------------------------------------------------------------------------
# 步骤 6：map_result（细分类进 message 前缀，单点 read-modify-write，must-fix-1）
# ---------------------------------------------------------------------------


def _map_category_to_error_type(category: ErrorCategory) -> str:
    """冒泡映射：执行细分类 → NodeError 三态（兼容性矩阵 §A.1）。

    error_type 严格保持 transient/permanent/degraded（不含 syntax/import 等细分类）。
    """
    if category in AUTO_FIXABLE:
        return "transient"  # 还能重试语义
    return "permanent"  # 放弃语义（不可修复类）


def _map_execution_result(
    exec_result: ExecutionResult,
    feedback: ExecutionFeedback,
    state: GlobalState,
    llm_calls_used: int = 0,
) -> dict:
    """把 ExecutionResult 映射为 GlobalState 局部更新（must-fix-1 单点 read-modify-write）。

    - execution_result / current_step；
    - 失败时把细分类写进 NodeError.error_message 的 [error_category=...] 前缀（error_type 严格三态）；
    - node_errors / degraded_nodes 走 read-modify-write；
    - llm_calls_used > 0（档 3 LLM 抽取触发）时单点回写 retry_budget_remaining + 累加 _dev_loop_llm_calls（must-fix-2）。
    """
    node_errors = list(state.get("node_errors", []))  # read-modify-write（must-fix-1）
    degraded_nodes = list(state.get("degraded_nodes", []))

    updates: Dict[str, Any] = {
        "execution_result": exec_result,
        "current_step": NODE_NAME,
    }

    if not exec_result["success"]:
        three_state = _map_category_to_error_type(feedback.category)
        node_errors.append(
            make_node_error(
                NODE_NAME,
                three_state,
                f"[error_category={feedback.category.value}] {feedback.summary}",
                feedback.representative_stderr or None,
            )
        )
        logger.warning(
            "[%s] 执行失败 category=%s three_state=%s summary=%s",
            NODE_NAME,
            feedback.category.value,
            three_state,
            feedback.summary,
        )

    updates["node_errors"] = node_errors
    updates["degraded_nodes"] = degraded_nodes

    # must-fix-2：仅 metrics 档 3 LLM 抽取触发时单点回写预算 + 累加子预算计数。
    if llm_calls_used and llm_calls_used > 0:
        prev_budget = state.get("retry_budget_remaining", 0) or 0
        updates["retry_budget_remaining"] = max(0, prev_budget - llm_calls_used)
        prev_calls = state.get("_dev_loop_llm_calls", 0) or 0
        updates["_dev_loop_llm_calls"] = prev_calls + llm_calls_used
        logger.info(
            "[%s] metrics LLM 抽取兜底消耗 %d 次：retry_budget %d->%d, _dev_loop_llm_calls %d->%d",
            NODE_NAME,
            llm_calls_used,
            prev_budget,
            updates["retry_budget_remaining"],
            prev_calls,
            updates["_dev_loop_llm_calls"],
        )

    return updates


# ---------------------------------------------------------------------------
# 步骤 7：修复循环边界 + interrupt#2（架构 §2.5.1 / §2.5.2 / §2.5.4）
# ---------------------------------------------------------------------------


def _append_fix_record(
    state: GlobalState,
    round_no: int,
    feedback: ExecutionFeedback,
) -> List[FixLoopRecord]:
    """单点 read-modify-write 追加 FixLoopRecord（must-fix-1，严禁 reducer）。"""
    history = list(state.get("fix_loop_history", []))  # 读出整列表
    history.append(
        FixLoopRecord(
            round_number=round_no,
            error_summary=feedback.summary,
            error_category=feedback.category.value,
            fix_strategy=feedback.fix_hint,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
    )
    return history  # return 整列表（last-write-wins，安全）


def _mark_degraded_for_report(updates: dict, state: GlobalState, *, reason: str) -> dict:
    """标记 degraded → 出边路由到 reporting（不 interrupt）。read-modify-write，非静默。"""
    out = dict(updates)
    degraded_nodes = list(out.get("degraded_nodes", state.get("degraded_nodes", [])))
    node_errors = list(out.get("node_errors", state.get("node_errors", [])))
    if NODE_NAME not in degraded_nodes:
        degraded_nodes.append(NODE_NAME)
    node_errors.append(
        make_node_error(
            NODE_NAME,
            "degraded",
            f"[error_category=degraded] execution 降级: {reason}",
            None,
        )
    )
    out["degraded_nodes"] = degraded_nodes
    out["node_errors"] = node_errors
    out["_dev_loop_route"] = None  # 降级 → reporting，清路由意图
    logger.warning("[%s] 降级到 reporting: reason=%s", NODE_NAME, reason)
    return out


def _build_dev_loop_interrupt_payload(
    exec_result: ExecutionResult,
    feedback: ExecutionFeedback,
    state: GlobalState,
) -> Dict[str, Any]:
    """interrupt#2 payload（含 interrupt_kind="dev_loop_failure"，与 interrupt#1 区分，§2.5.4）。"""
    return {
        "interrupt_kind": INTERRUPT_KIND,
        "fix_loop_count": state.get("fix_loop_count", 0) or 0,
        "error_category": feedback.category.value,
        "error_summary": feedback.summary,
        "fix_hint": feedback.fix_hint,
        "auto_fixable": feedback.auto_fixable,
        "fix_loop_history": list(state.get("fix_loop_history", [])),
        "execution_errors": list(exec_result.get("errors") or []),
        "representative_stderr": feedback.representative_stderr,
        "options": ["terminate", "revise_plan", "export_code"],
    }


def _build_revise_context(state: GlobalState, feedback_summary: str = "") -> str:
    """revise_plan 回 planning 时带的修复失败上下文（写 _planning_user_feedback）。"""
    fix_count = state.get("fix_loop_count", 0) or 0
    history = state.get("fix_loop_history", []) or []
    cats = [r.get("error_category") for r in history if isinstance(r, dict)]
    lines = [
        "（来自 execution 修复循环失败回流）复现执行多轮未通过，请据此修订复现计划。",
        f"已尝试修复回合数: {fix_count}",
    ]
    if cats:
        lines.append(f"历轮错误分类: {cats}")
    if feedback_summary:
        lines.append(f"最近一轮错误: {feedback_summary}")
    return "\n".join(lines)


def _route_user_fix_decision(decision: Any, updates: dict, state: GlobalState) -> dict:
    """interrupt#2 resume 三态路由（dict + "decision" 键，与 sp2 planning 一致，§2.5.4）。"""
    if not isinstance(decision, dict) or "decision" not in decision:
        # 防御兜底：非法 payload 视为终止（不空转）。
        logger.warning("[%s] interrupt#2 收到非法 resume payload，兜底视为 terminate", NODE_NAME)
        decision = {"decision": "terminate"}

    kind = decision["decision"]
    out = dict(updates)
    out["_dev_loop_route"] = None  # interrupt 后离开修复循环，清路由意图

    if kind == "terminate":
        out["user_fix_decision"] = "terminate"
        out["current_step"] = "cancelled_by_user"  # → END，checkpoint 保留
        logger.info("[%s] interrupt#2 resume: terminate", NODE_NAME)
        return out

    if kind == "revise_plan":
        out["user_fix_decision"] = "revise_plan"
        out["_planning_user_feedback"] = _build_revise_context(
            state, decision.get("user_feedback") or ""
        )
        # 清 approved，否则 planning 重入后 _route_after_planning 直接 next。
        out["reproduction_plan"] = {
            **(state.get("reproduction_plan") or {}),
            "approved": False,
        }
        # 回问点 2：fix_loop_count 清零、fix_loop_history 保留（供报告审计，§7）。
        out["fix_loop_count"] = 0
        logger.info("[%s] interrupt#2 resume: revise_plan（fix_loop_count 清零，history 保留）", NODE_NAME)
        return out

    if kind == "export_code":
        out["user_fix_decision"] = "export_code"
        out = _mark_degraded_for_report(out, state, reason="export_code")
        logger.info("[%s] interrupt#2 resume: export_code（降级导出）", NODE_NAME)
        return out

    # 未知 decision 兜底视为 terminate（不空转）。
    logger.warning("[%s] interrupt#2 resume 未知 decision=%r，兜底 terminate", NODE_NAME, kind)
    out["user_fix_decision"] = "terminate"
    out["current_step"] = "cancelled_by_user"
    return out


def _maybe_interrupt_or_return(
    updates: dict,
    exec_result: ExecutionResult,
    feedback: ExecutionFeedback,
    state: GlobalState,
    *,
    already_committed: bool,
) -> dict:
    """修复循环边界判定 + 可能的 interrupt#2（架构 §2.5.1）。

    already_committed：本次进入 execution 时本回合 sandbox 结果是否已通过上一个 checkpoint
    边界落盘（即 guard 命中、跳过了 sandbox）。仅当 already_committed=True 时才允许在函数体内
    interrupt()（满足 S-1 重跑幂等契约）；否则把 execution_result 落盘 + 置
    _dev_loop_route=await，先 return（不 interrupt），由 self-loop 路由重入后再 interrupt。
    """
    if exec_result["success"]:
        updates["_dev_loop_route"] = None  # → reporting（B 档成功）
        return updates

    fix_count = state.get("fix_loop_count", 0) or 0
    budget = state.get("retry_budget_remaining", 0) or 0
    dev_calls = state.get("_dev_loop_llm_calls", 0) or 0

    # 入口预算门：预算不足以启动一回合 → 直接降级（不 interrupt，§2.5.4 / PRD §5）。
    if budget < DEV_LOOP_MIN_CALLS_PER_ROUND:
        return _mark_degraded_for_report(updates, state, reason="budget_exhausted")

    # 可修复 + 未超限 + 预算够一回合 + 子预算未触顶 → 回 coding 修复（fix_loop_count 单点 +1）。
    if (
        feedback.auto_fixable
        and fix_count < MAX_FIX_LOOP_COUNT
        and dev_calls < MAX_DEV_LOOP_LLM_CALLS
    ):
        updates["fix_loop_count"] = fix_count + 1  # 单点自增（§2.5.2）
        updates["fix_loop_history"] = _append_fix_record(state, fix_count + 1, feedback)
        updates["_dev_loop_route"] = _ROUTE_RETRY_CODING  # → 出边回 coding
        logger.info(
            "[%s] 可修复失败 → 回 coding 修复: fix_loop_count %d->%d category=%s",
            NODE_NAME,
            fix_count,
            fix_count + 1,
            feedback.category.value,
        )
        return updates

    # 修复耗尽 / 不可修复 / 子预算触顶 → interrupt#2（三选一）。
    # interrupt 重跑幂等保护（S-1 CP-S-3）：仅当本回合 sandbox 结果已落盘（guard 命中、未重跑
    # sandbox）时才函数体内 interrupt()；否则先把 execution_result 落盘 + 置 await 标记 return，
    # 由 self-loop 路由重入后再 interrupt（重入时 sandbox 不重跑）。
    if not already_committed:
        updates["_dev_loop_route"] = _ROUTE_AWAIT_INTERRUPT  # → self-loop 重入 execution
        logger.info(
            "[%s] 需 interrupt#2 但本回合 sandbox 结果尚未过 checkpoint 边界，"
            "先落盘 execution_result 等待重入（重跑幂等 commit 边界）: category=%s",
            NODE_NAME,
            feedback.category.value,
        )
        return updates

    # 本回合结果已落盘 → 安全地在函数体内 interrupt()。
    reason = (
        "子预算触顶" if dev_calls >= MAX_DEV_LOOP_LLM_CALLS
        else ("不可修复" if not feedback.auto_fixable else "修复耗尽")
    )
    logger.warning(
        "[%s] 触发 interrupt#2（%s）: fix_loop_count=%d dev_calls=%d category=%s",
        NODE_NAME,
        reason,
        fix_count,
        dev_calls,
        feedback.category.value,
    )
    decision = interrupt(_build_dev_loop_interrupt_payload(exec_result, feedback, state))
    return _route_user_fix_decision(decision, updates, state)


# ---------------------------------------------------------------------------
# 主节点函数（手写复合，七步骨架）
# ---------------------------------------------------------------------------


def _has_committed_result_for_round(state: GlobalState) -> bool:
    """interrupt#2 重跑幂等 guard：判定本回合 sandbox 结果是否已通过 checkpoint 边界落盘。

    判定标准（S-1 CP-S-3 契约）：上一次 execution 进入跑完 sandbox 后置了 await 标记并 return
    落盘 → 本次（self-loop 重入 / resume 重跑）入口 state 满足
    `_dev_loop_route == "await_dev_loop_interrupt"` 且 execution_result 非空。
    跨回合（coding 修复回合）入口标记是 "retry_coding"（或被 D1 清空），不会误命中。
    """
    return (
        state.get("_dev_loop_route") == _ROUTE_AWAIT_INTERRUPT
        and state.get("execution_result") is not None
    )


def execution(state: GlobalState) -> dict:
    """步骤 6：sandbox 执行 + 错误分类 + B 档判定 + 修复耗尽/不可修复时 interrupt#2。

    七步骨架（架构 §2.3.1）：prepare_venv → run_in_venv 聚合 → _classify_execution →
    _parse_metrics → _build_execution_result → _map_execution_result → _maybe_interrupt_or_return。
    """
    work_dir = state.get("code_output_dir")  # C1 集成约定：直接读，不自拼目录。

    # interrupt#2 重跑幂等 guard（S-1 CP-S-3）：本回合 sandbox 结果已落盘 → 跳过 sandbox，复用结果。
    if _has_committed_result_for_round(state):
        logger.info(
            "[%s] guard 命中：本回合 execution_result 已落盘，跳过 sandbox 直接进入 interrupt 判定",
            NODE_NAME,
        )
        prev = state.get("execution_result") or {}
        exec_result: ExecutionResult = prev  # type: ignore[assignment]
        feedback = _feedback_from_committed_result(prev)
        # 已落盘的失败结果不重新写 node_errors（上一次进入已写），仅做边界判定 + interrupt。
        updates: Dict[str, Any] = {
            "execution_result": exec_result,
            "current_step": NODE_NAME,
        }
        return _maybe_interrupt_or_return(
            updates, exec_result, feedback, state, already_committed=True
        )

    # work_dir 缺失（coding 未产出代码目录）→ 降级，不进 sandbox（防御 C1 上游缺失）。
    if not work_dir:
        logger.warning("[%s] code_output_dir 缺失，无法执行，降级", NODE_NAME)
        feedback = ExecutionFeedback(
            ErrorCategory.PATH, False, "code_output_dir 缺失（上游未产出代码目录）",
            "检查 coding 节点是否产出代码", "",
        )
        exec_result = ExecutionResult(
            success=False, metrics={}, logs="", errors=[
                f"[error_category={feedback.category.value}] {feedback.summary}"
            ],
            artifacts=[], runtime_seconds=0.0, environment_info={},
        )
        updates = _map_execution_result(exec_result, feedback, state)
        return _mark_degraded_for_report(updates, state, reason="missing_code_output_dir")

    plan = state.get("reproduction_plan") or {}

    # 步骤 1：准备 venv + 装依赖。
    try:
        prep = prepare_venv(
            work_dir=work_dir,
            requirements=_extract_requirements(plan),
            requirements_files=None,
        )
    except SandboxCreationError as exc:
        logger.warning("[%s] prepare_venv 越界/创建失败: %s", NODE_NAME, exc)
        feedback = ExecutionFeedback(
            ErrorCategory.DEPENDENCY, True, f"sandbox 准备失败: {exc}",
            "检查 work_dir 是否在 workspace 下 / 依赖是否可解析", "",
        )
        exec_result = ExecutionResult(
            success=False, metrics={}, logs=str(exc), errors=[
                f"[error_category={feedback.category.value}] {feedback.summary}"
            ],
            artifacts=[], runtime_seconds=0.0, environment_info={},
        )
        updates = _map_execution_result(exec_result, feedback, state)
        return _maybe_interrupt_or_return(
            updates, exec_result, feedback, state, already_committed=False
        )

    # 步骤 2：逐条执行 execution_steps 并聚合。
    # current_dir 模拟连续 shell 会话的 cwd：cd 更新它、跨子命令/跨 step 持续（LLM 规划时
    # 假设 `cd 进仓库后后续步骤都在仓库里`）。始终经 workspace 边界校验，越界拒绝。
    steps = plan.get("execution_steps") or []
    run_results: List[SandboxRunResult] = []
    current_dir = work_dir
    if prep.success:
        for step in steps:
            sub_results, current_dir = _run_step_subcommands(
                step, prep.python_exe, current_dir
            )
            if not sub_results:
                continue
            run_results.extend(sub_results)
            # 某 step（任一子命令）失败即停（后续步骤通常依赖前序成功，避免噪声）。
            if any(r.exit_code != 0 or r.timed_out for r in sub_results):
                break

    # 步骤 3：错误分类。
    feedback = _classify_execution(prep, run_results)

    # 步骤 4：metrics 三档解析（档 3 LLM 抽取按实际次数回写预算）。
    metrics, llm_calls_used = _parse_metrics(run_results, plan, state)

    # 步骤 5：构造 ExecutionResult + B 档 success。
    exec_result = _build_execution_result(prep, run_results, feedback, metrics, work_dir)

    # 步骤 6：单点 read-modify-write 写 state（含 must-fix-2 预算回写）。
    updates = _map_execution_result(exec_result, feedback, state, llm_calls_used=llm_calls_used)

    # 步骤 7：修复循环边界判定（首次进入：sandbox 刚跑、未过 checkpoint 边界 → already_committed=False）。
    return _maybe_interrupt_or_return(
        updates, exec_result, feedback, state, already_committed=False
    )


def _feedback_from_committed_result(exec_result: Dict[str, Any]) -> ExecutionFeedback:
    """从已落盘的 ExecutionResult.errors[0] 的 [error_category=...] 前缀重建 ExecutionFeedback。

    guard 命中（跳过 sandbox）时用，避免重跑分类。解析失败兜底为 RUNTIME（可修复）。
    """
    errors = exec_result.get("errors") or []
    category = ErrorCategory.RUNTIME
    summary = "（复用已落盘失败结果）"
    if errors and isinstance(errors[0], str):
        head = errors[0]
        marker = "[error_category="
        idx = head.find(marker)
        if idx != -1:
            start = idx + len(marker)
            end = head.find("]", start)
            if end != -1:
                raw = head[start:end].strip()
                try:
                    category = ErrorCategory(raw)
                except ValueError:
                    category = ErrorCategory.RUNTIME
                summary = head[end + 1:].strip() or summary
    return ExecutionFeedback(
        category=category,
        auto_fixable=category in AUTO_FIXABLE,
        summary=summary,
        fix_hint="",
        representative_stderr="",
    )
