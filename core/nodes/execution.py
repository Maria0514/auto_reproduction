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
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from langgraph.types import interrupt

from config import (
    DEV_LOOP_MIN_CALLS_PER_ROUND,
    MAX_DEV_LOOP_LLM_CALLS,
    MAX_FIX_LOOP_COUNT,
)
from core.errors import SandboxCreationError, make_node_error
from core.state import ExecutionResult, FixLoopRecord, GlobalState
from sandbox.local_venv import (
    SandboxPrepareResult,
    SandboxRunResult,
    _is_within_workspace,
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
) -> Tuple[List[SandboxRunResult], str]:
    """执行一个 step 的子命令序列（顶层 && / ; 拆分后），返回 (run_results, 更新后的 current_dir)。

    语义（解析期，非 shell）：
      - connector "&&"：前一条非 0/超时则短路，停止该 step 剩余子命令；
      - connector ";"：无条件顺序执行；
      - `cd <dir>`：更新 current_dir（经 workspace 边界校验），不作为子进程执行；越界拒绝该 step；
      - `source`/`.`：丢弃（venv 已由 prepare_venv 建好，python_exe 已指向 venv）；
      - 裸 python/pip：改写为 venv 解释器；通配符：glob 展开（空则保留原样）。
    每条子命令以 current_dir 作 run_in_venv 的 work_dir（跨子命令、跨 step 持续）。
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
            rr = run_in_venv(python_exe, argv, current_dir)
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
