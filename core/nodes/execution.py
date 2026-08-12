"""execution 节点（S3-03 + S3-04 + S3-07）：sandbox 执行 + 错误分类 + B 档判定 + 修复循环边界 + interrupt#2。

节点形态：**手写复合节点**（与 ``planning.py`` 同构，非 ReAct wrapper）。七步骨架（架构 §2.3.1，
sp4 §3.4 步骤 1+2 换内嵌子图）：
    1+2.（sp4 S4-03）_run_execution_agent 内嵌 ReAct 子图自主编排 prepare_environment /
         run_in_sandbox / request_user_input（interrupt#3）；收尾只认工具执行的真实
         sandbox 结果（收集器 + messages 回读），不认 agent 自述；
    3. _classify_execution 错误分类（节点本地 ExecutionFeedback，不污染 NodeError 三态）；
    4.〔S8-02 / T-S8-2-1，2026-08-10：**已退场**〕原「_parse_metrics 三档解析（结构化标签
       → 正则 → LLM 抽取兜底）」**四个函数整体删除**，<METRICS> 通道废止（Maria 决策 3：
       是废掉不是收窄）。metrics 现在的**唯一来源** = agent 经 <result>.metrics 自报，
       由步骤 4.4 _split_reported_metrics 拆分；
    5. _build_execution_result success 判定 —— ⚠ **判据正在换装**：T-S8-2-8 会把 success
       改为由四档 level 派生，**在那之前 success 恒假**（`len(metrics) >= 1` 这个合取项的
       分子已被步骤 4 撤走）。这是计划内的中间态，详见步骤 4 处的窗口告示；
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
    - execution 主体不调 LLM（零扣减）—— 🔴 **S8-02 之后这是"结构上不可能"，不再只是"目前
      恒成立"**：被删掉的 _llm_extract_metrics 是主体在 ReAct 子图**之外唯一的 LLM 调用入口**，
      它随 <METRICS> 通道一并消失后，主体已无处产生子图之外的 LLM 调用 ⇒ 预算扣减恒等于
      react_rounds_used（llm_calls_used 支路归零，见 _map_execution_result 调用点）。
      ~~仅 metrics 档 3 LLM 抽取兜底触发时按实际次数单点回写 retry_budget_remaining +
      累加 _dev_loop_llm_calls~~（该通道已不存在）；
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
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional, Tuple

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.tools import tool
from langgraph.errors import GraphBubbleUp
from langgraph.types import interrupt

from config import (
    DEV_LOOP_MIN_CALLS_PER_ROUND,
    MAX_DEV_LOOP_LLM_CALLS,
    MAX_FIX_LOOP_COUNT,
    MAX_TOTAL_LLM_CALLS,
    NO_METRICS_EARLY_STOP_ROUNDS,
    REACT_EXECUTION_ROUNDS_MARGIN,
    REACT_MAX_ROUNDS_EXECUTION,
    REACT_MAX_ROUNDS_EXECUTION_CAP,
    REACT_RESULT_TAG_CLOSE,
    REACT_RESULT_TAG_OPEN,
)
from core.errors import SandboxCreationError, make_node_error
from core.llm_client import create_llm, resolve_llm_config
from core.plan_checks import (
    UNSUPPORTED_SHELL_SYNTAX_MESSAGE,
    has_unsupported_shell_syntax,
    is_inline_code_write,
)
from core.nodes.coding import _FIX_NOTE_MAX_CHARS
from core.react_base import _repair_truncated_json_prefix, create_react_subgraph
from core.secrets_store import build_credential_env, load_all_secrets, mask_value
from core.state import (
    ExecutionResult,
    FixLoopRecord,
    GlobalState,
    completion_denominator as _completion_denominator,
)
from core.tools.code_fs_tools import make_list_dir_tool, make_read_code_file_tool
from core.tools.interaction_tools import make_request_user_input_tool
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

# S6-B2（T-S6-2-3）：降级凭证通用指令常量（coding/execution 两侧值一致）。
# 降级非空时注入 HumanMessage payload，告知 agent 全程走模拟路径。
_CREDENTIAL_DEGRADATIONS_DIRECTIVE: str = (
    "重要：部分凭证已被用户明确拒绝，下游模拟已激活。"
    "在所有步骤中，全程不得再向用户索要被拒绝的凭证；"
    "所有涉及该凭证的功能必须走模拟/mock 路径，并在报告中如实声明模拟范围。"
)

# S7-08（T-S7-5-8，架构 §18.1.2 落点 8 + §18.7(5)）：缩规模复现通用指令常量
# （coding/execution 两侧值必须逐字节相同，由测试断言锁死防单边漂移）。
# 计划 scale_reduced 为真时注入 HumanMessage payload，告知 agent 按缩小后的规模执行。
# 给模型看的指令文案（非用户可见 UI 文案），故不入 §18.2 用户文案守门。
_SCALE_REDUCED_DIRECTIVE: str = (
    "重要：本次复现计划已按本机实际可跑的规模缩小。"
    "计划中的规模参数（模型大小 / 数据子集 / 实验组数 / 训练步数等）是硬约束，"
    "不得按论文原始规模放大，也不得自行恢复被裁掉的实验组；"
    "并在产出中如实体现这是缩小规模的复现。"
)


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
    # sp4 §9.2：缺凭证/认证失败（不进 AUTO_FIXABLE、映射 permanent、不耗 fix_loop_count；
    # 首选路径是 agent 子图内就地 request_user_input，本分类是收尾兜底）。
    CREDENTIAL_REQUIRED = "credential_required"
    DATA_MISSING = "data_missing"
    HARDWARE = "hardware"
    TIMEOUT = "timeout"
    UNRESOLVED_RESOURCE = "unresolved_resource"
    NONE = "none"  # 执行成功，无错误
    # S6-B2（T-S6-2-4）：代码跑通但未产出指标——可自动修复（送回 coding 检查入口脚本）。
    # 🔴 S8-05（T-S8-2-3，架构 Q-S8-07）：**Sprint 8 起本成员无生产者，但必须保留。**
    # 理由不是"留着以防万一"，是硬约束：_feedback_from_committed_result 会从已落盘的
    # ExecutionResult.errors[0] 里 "[error_category=xxx]" 前缀**反序列化**重建本枚举，
    # 而旧 checkpoint 里存着 error_category=no_metrics 的字符串 ⇒ **删成员会让旧任务
    # resume 当场炸**。AUTO_FIXABLE 中的归属同样不动；ui/term_map.py 的对应文案亦保留
    # （旧报告仍要能渲染）。
    # ⚠ 与 AC-S8-18「_apply_no_metrics 已删除且无残留引用」的边界：那条清零的对象是
    # **函数与其调用点**，**不是本枚举成员**——把成员一并清零会当场打掉旧快照兼容。
    NO_METRICS = "no_metrics"
    # 🔴 S8-05（T-S8-2-3，Q-S8-04）：跑通了、但计划里说好要产出的东西没落地——可自动
    # 修复（送回 coding 补产出）。**不复用 NO_METRICS**，理由与下方 INCOMPLETE_EXECUTION
    # 那三条同源：①会被无进展早停误伤；②fix_hint 指错方向；③fix_loop_history 里的
    # error_category 是面向用户的修复历程标题，复用会让界面印"未产出指标"而真相是
    # "产出没落地"——**对用户撒谎比技术债更贵**。
    NO_VERIFIABLE_OUTPUT = "no_verifiable_output"
    # S7-11（T-S7-7-6）：命令都跑通了、但计划步骤没跑完——可自动修复（送回 coding
    # 继续补跑）。不复用 NO_METRICS 的三条理由（架构 Q-S7-29）：①会被
    # _no_metrics_stalled 的"无进展早停"误伤成提前打断；②fix_hint 指错方向；
    # ③fix_loop_history.error_category 是面向用户的修复历程标题，复用会让界面连续
    # 印"未产出指标"而真相是"步骤没跑完"——对用户撒谎比技术债更贵。
    INCOMPLETE_EXECUTION = "incomplete_execution"


# 可自动修复类集合（驱动 §2.5.2 路由：是否回 coding）。
AUTO_FIXABLE = {
    ErrorCategory.SYNTAX,
    ErrorCategory.IMPORT,
    ErrorCategory.DEPENDENCY,
    ErrorCategory.PATH,
    ErrorCategory.RUNTIME,
    ErrorCategory.NO_METRICS,  # S6-B2（T-S6-2-4）：零指标可修复（S8 起无生产者，归属不动）
    ErrorCategory.INCOMPLETE_EXECUTION,  # S7-11（T-S7-7-6）：步骤没跑完可修复
    ErrorCategory.NO_VERIFIABLE_OUTPUT,  # S8-05（T-S8-2-3）：产出没落地可修复
}


# ---------------------------------------------------------------------------
# S8-05（T-S8-2-3，架构 §2.3）：四档结论的档名 —— **一套值，没有第二套**
# ---------------------------------------------------------------------------
# 🔴 落盘的字面量就是下面四个中文串本身：**不引入 ConclusionLevel Enum，也不引入
# "success" / "partial" / "code_only" 之类的英文内部值**（A-S8-05 / 反过度工程）。
# 理由：档名同时就是**用户可见文案**，多一套英文内部值就多一处要 humanize 的地方、
# 也多一处两套值走散的可能（sprint7 术语泄漏的成因正是此类"内部值 + 展示值"双轨）。
_LEVEL_SUCCESS: str = "复现成功"
_LEVEL_PARTIAL: str = "部分复现"
_LEVEL_CODE_ONLY: str = "仅代码跑通"
_LEVEL_FAILED: str = "失败"

# 档位顺序元组，**从高到低**（下标越大、档位越低）。
# 🔴 封顶一律写成"按本元组下标取更低档"，**不要写 if 链**（架构 §2.3）：
#       capped = _LEVELS[max(_LEVELS.index(a), _LEVELS.index(b))]
# 取 max(下标) 天然满足 AC-S8-09④「只压低、不抬高」——**这是结构性保证，不是靠测试
# 逐条覆盖出来的**；而 if 链每加一条封顶规则都要重新证明一次"没有哪条路径会抬高"。
# ⚠ 这是全 Sprint 最容易被"顺手优化"成 if 链的地方，动它之前先读 AC-S8-09④。
_LEVELS: Tuple[str, ...] = (
    _LEVEL_SUCCESS,
    _LEVEL_PARTIAL,
    _LEVEL_CODE_ONLY,
    _LEVEL_FAILED,
)


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
# sp4 §9.2（逐字）：凭证缺失/认证失败关键字，判定顺序先于 DATA_MISSING / HARDWARE。
_CREDENTIAL_KEYWORDS = (
    "could not read username", "authentication failed",
    "terminal prompts disabled", "permission denied (publickey)",
    "fatal: could not read", "invalid username or password",
    "401 unauthorized", "403 forbidden",
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


def _effective_runs(run_results: List[SandboxRunResult]) -> List[SandboxRunResult]:
    """同命令(argv 精确匹配)多次尝试只保留最后一次(L-E4-01 裁决:重试以最后一次为准)。

    保序;仅用于判定/分类/metrics 视图,logs 聚合仍用全量序列(失败证据不丢)。
    """
    last: Dict[Tuple[str, ...], int] = {}
    for i, r in enumerate(run_results):
        last[tuple(str(c) for c in (r.command or []))] = i
    keep = set(last.values())
    return [r for i, r in enumerate(run_results) if i in keep]


# ---------------------------------------------------------------------------
# 步骤 3：错误分类（架构 §2.3.2）
# ---------------------------------------------------------------------------


def _classify_execution(
    prep: Optional[SandboxPrepareResult],
    run_results: List[SandboxRunResult],
) -> ExecutionFeedback:
    """基于 prep / exit_code / stderr 关键字 / timed_out 的执行错误分类。

    判定优先级（顺序敏感）：
        0) prep=None（sp4 E3：agent 未调 prepare / 子图降级）——无任何真实运行结果时
           视作"环境未准备"走既有降级分类（DEPENDENCY 可修复，与 sp3 venv 失败同口径）；
           有运行结果时视 prep 为中性（agent 复用既有 venv 跑通了命令），按 exit/stderr 判定；
        0') 全部 exit 0 且 venv 成功 → NONE（成功）；
        1) 超时优先（疑似死循环，不可修复）；
        2) 依赖装不上（可修复，送回 coding 调整版本/换包）；
        3) 凭证缺失/认证失败（sp4 §9.2，**先于** DATA_MISSING / HARDWARE，不可自动修复）；
        3') stderr 关键字（硬件/数据缺失/未公开资源先于通用 runtime）；
        4) import / syntax / path（可修复）；
        5) 兜底 RUNTIME（可修复，给一次机会；MAX_FIX_LOOP_COUNT 上限拦截，缓解 R-S3-04）。

    L-E4-01：判定视图先经 ``_effective_runs`` 过滤（同命令取最后一次），
    representative_stderr 因此取同命令最后一次尝试的 stderr（语义更对）。
    """
    run_results = _effective_runs(run_results)
    if prep is None and not run_results:
        return ExecutionFeedback(
            ErrorCategory.DEPENDENCY,
            True,
            "沙箱环境未准备（agent 未调用 prepare_environment 或执行子图降级）",
            "检查依赖声明是否可解析 / LLM 配置是否完整后重试",
            "",
        )
    prep_ok = bool(prep.success) if prep is not None else True  # 有真实运行结果 → prep 中性
    exit_ok = prep_ok and all(r.exit_code == 0 for r in run_results)
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

    # 2) 依赖装不上（可修复）。prep=None 时跳过（exit_ok=False 必然来自 run_results）。
    if prep is not None and not prep.success and prep.install_failed_packages:
        return ExecutionFeedback(
            ErrorCategory.DEPENDENCY,
            True,
            f"依赖安装失败: {prep.install_failed_packages}",
            "调整依赖版本 / 更换等价包 / 移除不必要依赖后重试",
            _tail(prep.install_log or prep.error),
        )
    # venv 创建本身失败（无 failed_packages 但 prep 失败）→ 当依赖问题处理（可修复）。
    if prep is not None and not prep.success:
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

    # 3) 凭证缺失/认证失败（sp4 §9.2：先于 DATA_MISSING / HARDWARE，不可自动修复，
    #    不耗 fix_loop_count——auto_fixable=False 走 interrupt#2 兜底路径）。
    if any(k in stderr for k in _CREDENTIAL_KEYWORDS):
        return ExecutionFeedback(
            ErrorCategory.CREDENTIAL_REQUIRED,
            False,
            "缺少凭证 / 认证失败（需用户提供凭证后重试）",
            "通过 UI 提供对应凭证（git token / HF token / 私有源账号）后重试，超出自动修复范围",
            rep,
        )

    # 3') 硬件/数据缺失/未公开资源（不可修复）先于通用 runtime。
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
# 步骤 4：〔S8-02 / T-S8-2-1，2026-08-10〕**<METRICS> 通道整体退场** —— 此处原有代码已删除
# ---------------------------------------------------------------------------
#
# 删掉的是（架构 v2.5 §7 / dev-plan §16.C，Maria 2026-08-04 已认）：
#   - 三个标签常量 _METRICS_TAG_OPEN / _METRICS_TAG_CLOSE / _METRICS_TAG_PATTERN；
#   - 档 1 _extract_metrics_block（结构化标签）
#   - 档 2 _regex_scan_metrics（正则兜底）
#   - 档 3 _llm_extract_metrics（LLM 抽取兜底）
#   - 三档调度 _parse_metrics
#
# 🔴 **是废掉，不是收窄**（Maria 决策 3）。不要"为了兼容"把任何一档加回来 —— 加回来就等于
# 把"分子由 agent 自己写"这条路重新打通，而本 Sprint 整件事就是为了堵它。
#
# **metrics 现在的唯一来源** = 执行 agent 经 <result>.metrics 自报，由步骤 4.4 的
# _split_reported_metrics 拆成主实验 / 分组两份。**没有第二个来源。**
#
# ★★★ 给后人看的窗口告示（dev-plan §0.0，AR-S8-01）★★★
# 从本次删除合入起，到 T-S8-2-8（success 由四档 level 派生）落地为止，
# **本系统一律判失败** —— 因为 _build_execution_result 的成功判据里还留着
# `len(metrics) >= 1` 这个合取项，而它的分子刚刚被撤走（步骤 4.4 的自律门控
# 在主通道零指标时不采信 agent 自报，见那里的 elif 分支）。
#
# 🔴 **这不是 bug，不要来"修"它**：
#   - 不要回滚本次删除来让回归变绿（dev-plan §0.0 第 4 条明令禁止）；
#   - 不要顺手改 `len(metrics) >= 1` 那个合取项 —— 那是 T-S8-2-8 的射程；
#   - 窗口期间**不得端到端真跑、不得对外演示**，也不得据此判断"哪里坏了"。
# 恢复点 = T-S8-2-11（节点主体接线 + S7-13 自律门控废止）。窗口期间若真有跑通的需求，
# **唯一正确的做法是把批次 2 做完**。
#
# ✨ 附带红利（dev-plan T-S8-2-1 第 3 条，须随交接文档一起抄走）：
# _llm_extract_metrics 是 execution 主体在 ReAct 子图之外**唯一的 LLM 调用入口**。
# 删掉它之后，「执行主体不调 LLM」这句话从"目前恒成立"升级为"**结构上不可能不成立**"。


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

# S7-10 约束 C：内联写码被工具层拒绝时返回给 agent 的结构化说明。
# 这是**给模型看的**文本（不是给用户看的 UI 文案）⇒ 不入 tests/test_s708_user_text_guard.py
# 守门面，判定口径与 _SCALE_REDUCED_DIRECTIVE 一致。
# ⚠ 必须**明确指路**（PRD §12.5.3）：误伤可恢复、agent 下一轮能自行合规，否则会空转（R-S7-58）。
_INLINE_CODE_WRITE_REJECTION: str = (
    "本工具不用于写代码：这条命令把成段代码字面量直接放在了命令行里，已被拒绝执行。"
    "需要写或修改代码文件时请如实收尾，编排层会交回代码生成环节修复；"
    "只是想探一下环境的话，请把行内命令拆得更短（只做导入检查、打印版本或数据形状），"
    "或者先把逻辑落成脚本文件再运行该脚本。"
)

# 拒绝日志里回显的命令前缀长度（脱敏后截断，避免把超长载荷整段刷进日志）。
_REJECT_LOG_COMMAND_CHARS: int = 120

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
    # P5（sp5 S5-06 台账雏形）：逐条真实执行的 (step_index, command, exit_code)。
    # step_index 是 agent 经 run_in_sandbox 声明的计划步骤归属标签（0 起；-1 =
    # 未声明/计划外）；归属合法性校验与对账（_reconcile_steps）属 T-S5-2-4。
    step_ledger: List[Tuple[int, List[str], int]] = field(default_factory=list)


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
            # P6（sp5 S5-10 key_packages 修复前置）：env_info（python_version /
            # key_packages）随工具返回 JSON 带出，使 messages 回读可重建（R-S4-10
            # 回读为权威时不再被空占位覆盖；回读解析属 T-S5-2-6）。仅 ToolMessage
            # 内容，不进 Prompt Cache 前缀；经 _tool_json 统一序列化（禁 str(dict)）。
            "env_info": {str(k): str(v) for k, v in (prep.env_info or {}).items()},
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
    def run_in_sandbox(command: str, step_index: int = -1) -> str:
        """在已准备好的沙箱 venv 中执行一条命令，返回真实执行结果。

        入参为单条命令字符串（如 "python train.py --epochs 1"）。支持顶层
        `&&` / `;` 复合命令、`cd`（限工作区内，越界拒绝）、裸 python/pip 自动
        改写为 venv 解释器、通配符展开；不经过 shell。执行复现计划第 i 步
        （execution_steps 下标，从 0 起）时传 step_index=i 声明该命令的步骤
        归属；计划外命令（调试/兜底）省略该参数即可。返回 JSON：exit_code
        （首个非 0 子命令的退出码，全 0 则 0）/ timed_out / results（逐子命令
        command、exit_code、stdout_tail、stderr_tail）。请根据 exit_code 与
        stderr_tail 决定下一步，一次只执行一条命令。
        """
        # 归属标签防御性归一（agent 可能传非法值；台账只收 int，非法回落 -1）。
        try:
            declared_step = int(step_index)
        except (TypeError, ValueError):
            declared_step = -1
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
            # S7-10 约束 C 的**唯一硬防线**（Q-S7-21，dev-plan T-S7-6-6）：
            # 命令串里以字面量携带成段代码载荷 ⇒ 命中即拒。
            # ⚠ 早退位置是硬要求——必须在 `_resolve_python_exe()` 之后、
            # `_run_step_subcommands` 之前：这样被拒命令**不进 collector.run_results、
            # 不进 collector.step_ledger**（两者都在下方 `:978` 之后才写），
            # 因而不污染 exit_ok、不被步骤对账当成"完成"（否则这条硬防线会自己
            # 制造 R-S7-49 那类假绿）。判定对象是命令字符串本身而非文件系统副作用 ⇒
            # 跑一个既有脚本写出多少结果文件与图**永远合规**（零误伤正常复现）。
            if is_inline_code_write(command):
                logger.warning(
                    "[%s] %s 工具拒绝内联写码命令（约束 C 硬拦截）：%s",
                    NODE_NAME, _RUN_TOOL_NAME,
                    mask_value(command[:_REJECT_LOG_COMMAND_CHARS]) or "",
                )
                return _tool_error_json(
                    _INLINE_CODE_WRITE_REJECTION, exit_code=-1, results=[], timed_out=False,
                )
            # S7-12：沙箱不认的 shell 元字符（管道 / 重定向 / 后台）⇒ 命中即拒。
            # ⚠ 早退位置同上一条硬要求：必须在下方 collector.run_results /
            # collector.step_ledger 之前——否则一条**实际什么都没干成**的命令会带着
            # 假 exit_code=0 进台账，污染 exit_ok、被步骤对账当成"完成"。
            # 拒绝而不是支持：实现管道 / 重定向语义等于自己写一个完整 shell，
            # 正是当初禁 shell=True 要逃离的东西（这些语法现在也本就不生效）。
            if has_unsupported_shell_syntax(command):
                logger.warning(
                    "[%s] %s 工具拒绝含管道/重定向的命令（沙箱不经 shell，写了不生效）：%s",
                    NODE_NAME, _RUN_TOOL_NAME,
                    mask_value(command[:_REJECT_LOG_COMMAND_CHARS]) or "",
                )
                return _tool_error_json(
                    UNSUPPORTED_SHELL_SYNTAX_MESSAGE,
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
        for rr in results:  # P5 台账雏形：逐条 (step_index, command, exit_code)
            collector.step_ledger.append(
                (declared_step, [str(c) for c in (rr.command or [])], rr.exit_code)
            )
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


# S7-13（T-S7-9-1）：execution 侧 ReAct 输出契约。体例照 coding.CODING_OUTPUT_SCHEMA。
#
# 立项事实：`_run_execution_agent` 此前调 `create_react_subgraph` **不传第 5 个参数
# result_schema**（react_base.py:509 一直支持，coding.py:894 传了）⇒ execution 的 agent
# 全程在场、跑完全部步骤，但**系统从设计上没给它留汇报的出口**，收尾只从工具收集器
# 取原始数据，指标归属靠代码猜（"取最后一块" / "只收顶层标量" / "组名子串匹配"三处
# 猜测在 2026-08-01 真跑里全部猜错）。本 schema 就是那个出口：让它汇报，代码只管判定。
#
# 字段集刻意保持最小（MEMORY §4.1 反对过度工程）：
#   - metrics[].group **必须填计划预期（expected_results）里出现的写法**，不是产物目录名
#     ——名字对不上的问题由此**被绕过而非修补**（实测：计划写 "t-SNE"、目录叫
#     baselines/tsne，`reporting._match_metrics_group("t-SNE", …)` 返回 None）；
#   - metrics[].group 为空/缺省 ⇒ 主实验指标（进 ExecutionResult.metrics）；
#   - ⟦2026-08-02 Maria 拍板砍掉 source 字段⟧ 原设计有 metrics[].source（产物文件相对
#     路径），定位是"给模型的锚"、**无代码消费点**——磁盘核对已先行否决（它只能拦"报了
#     不存在的数"、拦不住"数取错了"，且真跑实测零编造 ⇒ 没有证据前是过度工程，沿
#     Q-S7-27 同款裁决）。**一个没有消费点的字段就是过度工程本身**（MEMORY §4.1）：
#     它每回合都占 schema 与 prompt 的字节、要模型多想一层，却不产生任何可验证的约束。
#     ⇒ 整条砍除。日后若真跑发现报数与磁盘对不上，**要加就连同磁盘核对一起加**——
#     那时字段才有消费点，而不是先摆一个"看着像防线"的空壳。
# required 刻意**不含 metrics**：零指标回合它就是空数组，若列为必填会被
# `react_base._missing_required_fields` 判成"缺失"→ 每次都白烧一次 schema 重生成调用。
EXECUTION_OUTPUT_SCHEMA: Dict[str, Any] = {
    # title 字段是 langchain_openai.with_structured_output 的强制要求（函数名）。
    "title": "ExecutionAgentReport",
    "description": "execution 节点输出契约：本回合执行情况 + 真实跑出来的指标汇报。",
    "type": "object",
    "properties": {
        "steps_attempted": {
            "type": "integer",
            "description": "本回合实际执行的命令条数。",
        },
        "all_exit_zero": {
            "type": "boolean",
            "description": "已执行命令是否全部 exit_code=0（如实填写）。",
        },
        "summary": {
            "type": "string",
            "description": "执行过程与结果的中文如实描述。",
        },
        "notes": {
            "type": ["string", "null"],
            "description": "降级 / 遗留问题等元信息（可选）。",
        },
        "metrics": {
            "type": "array",
            "description": "本回合真实跑出来的指标逐条列出；没有则为空数组。",
            "items": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "指标名，优先用计划预期里出现的写法。",
                    },
                    "value": {
                        "type": ["number", "string", "boolean"],
                        "description": "指标值，直接取自产物文件，不得口算或估计。",
                    },
                    "group": {
                        "type": ["string", "null"],
                        "description": "该指标属于哪一组实验，用计划预期里的写法；主实验指标留空。",
                    },
                },
                "required": ["name", "value"],
            },
        },
    },
    "required": ["steps_attempted", "all_exit_zero", "summary"],
    "additionalProperties": True,
}


# Prompt Cache 方案 A：主体常量，零论文级 / 任务级动态变量（CP-E2-1 字节级一致断言）。
# 动态上下文（work_dir / execution_steps / 修复回合反馈）一律走 HumanMessage。
_EXECUTION_SYSTEM_PROMPT_BODY = """你是复现执行工程师，负责在隔离沙箱中执行论文复现代码并收集真实运行结果。HumanMessage 提供 work_dir、execution_steps 与环境依赖信息；修复回合时额外提供上一轮错误摘要。

可用工具：
- prepare_environment(): 在工作目录下创建隔离 venv 并安装复现计划声明的依赖。任何 run_in_sandbox 之前必须先调用一次。
- run_in_sandbox(command, step_index): 在沙箱 venv 中执行一条命令，返回真实 exit_code 与 stdout/stderr 尾部。支持顶层 && / ; 复合与 cd（限工作区内），裸 python/pip 自动指向 venv 解释器。执行 execution_steps 里的第 i 步（下标从 0 起）时**必须**以 step_index=i 声明归属——漏报会让编排层认为该步没跑；计划外命令（调试/兜底）不带该参数即可。本工具不用于写代码；行内 -c 只用于简短探针（导入检查、打印版本或数据形状）。凡是需要写文件、或把成段实现塞进命令行的，一律先落成脚本再运行——超长载荷会被直接拒绝。
- request_user_input(question, is_sensitive, purpose_key): 缺少继续执行所需的信息（凭证/参数/路径）时向用户索要一条信息。必须单独一轮调用（不与其他工具放在同一轮 tool_calls），且尽量在执行训练等重活之前问。

工作纪律：
1. 先调 prepare_environment 准备环境；依赖装不全（install_failed_packages 非空）时可用 run_in_sandbox 执行 pip install 兜底或调整版本。
2. 按 execution_steps 逐条执行：每条命令跑完先检查返回 JSON 的 exit_code / stderr_tail 再决定下一步；一次只跑一条命令。
3. 识别到认证失败 / 缺凭证迹象（authentication failed、401 unauthorized、403 forbidden、could not read username、terminal prompts disabled 等）时，立即调 request_user_input（is_sensitive=true，给出合适的 purpose_key，如 "git_credential:github.com" / "hf_token"）索取凭证后重试，不要反复盲试。
4. 命令失败时可做少量有把握的就地修正（如补装缺失包、调整依赖版本）后重试；但**不得写入或修改任何代码文件**——代码本身有问题时，把该步跑到失败为止即可，由编排层交回代码生成环节修复。确实无法继续时才如实收尾，交由编排层分类处理；收尾前必须先把计划里还没跑过的步骤跑完或跑到失败为止，不得因为"上一轮这条失败过"就跳过它。
5. 预算意识：推理轮数有限，本次实际可用轮数以 HumanMessage 上下文中的 max_rounds 数字为准；同一回合内不要用完全相同的命令反复空转（同一条命令在不同回合之间的重跑是必要的验证，不算空转）。
6. 修复回合请从 execution_steps 的第一步开始按顺序全量重跑，不要只挑上一轮失败的那几步——代码已被改动，上一轮通过的步骤不再自动成立。HumanMessage 会告知上一轮改了哪些文件与修复思路，据此重跑验证，而不是绕开。

成功判定纪律（强约束）：
- 你不判定复现是否成功——成功与否由编排层基于工具执行的真实 exit_code 与指标做确定性判定。
- 编排层还会检查计划步骤是否全部跑完——少跑步骤不会被判成功。
- 不得在结果中宣称"复现成功"；只如实汇报执行了哪些命令、各自 exit_code 与观察到的现象。

输出要求：
- 执行收尾时必须在 <result>...</result> 标签内输出严格 JSON，字段如下：
  {
    "steps_attempted": int,        // 本回合实际执行的命令条数
    "all_exit_zero": bool,         // 已执行命令是否全部 exit_code=0（如实填写）
    "summary": str,                // 执行过程与结果的中文如实描述
    "notes": str | null,           // 降级/遗留问题等（可选）
    "metrics": [                   // 本回合真实跑出来的指标，逐条列出；没有就写 []
      {
        "name": str,                    // 指标名
        "value": number | str | bool,   // 指标值，直接抄自产物文件，不得口算或估计
        "group": str | null             // 该指标属于哪一组实验；主实验指标留 null
      }
    ]
  }
- metrics 的 group 与 name 必须使用 HumanMessage 里 expected_results 的原文写法：计划怎么称呼那一组方法、怎么称呼那个指标，你就怎么填，不要改成产物目录名或代码里的字段名。expected_results 没提到的指标，按产物文件里的原名填。
- 一条命令跑完写出的产物文件，请用同一组名把该组的各项指标都列出来；同一组同一指标只报一条。
- 只汇报你本回合真实读到的数值；产物文件里没有的一律不填，宁可少报也不得编造。
- 不得捏造未执行的命令；不要在 <result> 之外再夹杂其它 JSON 块。
"""


@dataclass
class ExecAgentOutput:
    """``_run_execution_agent`` 的轻量返回结构（喂 E3 编排层收尾 + 预算扣减）。

    - prep / run_results：工具执行的**真实** sandbox 结果（收集器 + messages 回读
      合并，非 agent 自述）；prep 取最后一次 prepare（agent 可能重试）；
    - rounds_used：子图实际 round（与 wrapper 同口径 max(1, round)；降级路径 0）；
    - llm_calls：子图内 LLM 调用数（= rounds_used，喂 _dev_loop_llm_calls 累加）；
    - step_ledger（sp5 S5-06，T-S5-2-4 消费）：收集器台账 (step_index, command,
      exit_code) 原样透传，供 _reconcile_steps 做确定性步骤对账。保真注记
      （R-S4-10 同机理）：interrupt#3 resume 后收集器重建，pre-interrupt 台账段
      丢失——丢失段的 runs 无声明标签，走归属规则②（归一匹配）兜底；全零归属
      由 R-2 保守语义（attribution_unavailable）兜底，不误标未执行。
    - budget_truncated（sp5 S5-06，T-S5-2-5）：轮次预算截断显式标记（Q-S5-7 确定性
      代理判据 rounds_used >= effective_max_rounds ⇔ force_finish 截断路径）；
      _run_execution_agent 单点产出（架构 §8 总表），随 exec_result 一次 commit。
      带默认值 False：降级路径（rounds_used=0）与既有构造点天然为 False。
    - reported_metrics（S7-13，T-S7-9-1）：agent <result>.metrics 的**原样透传**
      （list of dict，未清洗）。这是"让 agent 汇报、代码只管判定"的唯一入口；清洗
      与拆分在 `_split_reported_metrics`（步骤 4.4）单点做，本 dataclass 不加工。
      ⚠ 它是**自报**通道：绝不进 `_reconcile_steps` / `_completion_insufficient` /
      `exit_ok` 任何一处（R-S4-01 红线），且主实验指标合并受"三档主通道非空"门控
      （见 execution() 步骤 4.4 注释）——`len(metrics) >= 1` 这个成功合取项的
      **分子来源不因本批变化**。带默认值 []：降级路径与既有构造点天然为空。
    - report（sp8 Q-S8-01，T-S8-2-4）：agent 收尾汇报的**整份原样透传**，由
      `_resolve_agent_report` 单点产出（result 通道优先 + messages 末条 <result> 回读
      兜底）。档位 / 逐条结论 / 物证清单**全部从这里取**，不再各自去读 final_state。
      ⚠ 它同样是**自报**通道，R-S4-01 红线照旧：绝不进 _reconcile_steps /
      _completion_insufficient / exit_ok。`reported_metrics` 已改为它的派生量
      （report.get("metrics")）⇒ 两个取数口径合并为一个。带默认值 {}：降级路径
      （子图抛非 GraphBubbleUp 异常）与既有构造点天然为空。
    """

    prep: Optional[SandboxPrepareResult]
    run_results: List[SandboxRunResult]
    rounds_used: int
    llm_calls: int
    step_ledger: List[Tuple[int, List[str], int]] = field(default_factory=list)
    budget_truncated: bool = False
    reported_metrics: List[Any] = field(default_factory=list)
    report: Dict[str, Any] = field(default_factory=dict)


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


def _effective_max_rounds(plan: Optional[Dict[str, Any]]) -> int:
    """预算联动公式（sp5 S5-06，Q-S5-7 / AC-S5-12，确定性 helper 零 LLM）。

    effective_max_rounds = clamp(len(execution_steps) + K, FLOOR, CAP)：
        - K = REACT_EXECUTION_ROUNDS_MARGIN（prepare 1 + 收尾 <result> 1 + 兜底 3）；
        - FLOOR = REACT_MAX_ROUNDS_EXECUTION（值 10 不变，sp5 语义收窄为下限）；
        - CAP = REACT_MAX_ROUNDS_EXECUTION_CAP（= MAX_DEV_LOOP_LLM_CALLS/2，
          保证初跑耗尽 CAP 后修复循环子预算仍容一个完整回合，账本对账见架构 §3）。

    防御：plan 非 dict / execution_steps 非 list → 按 0 步计（回落 FLOOR），不炸。
    """
    steps = plan.get("execution_steps") if isinstance(plan, dict) else None
    n_steps = len(steps) if isinstance(steps, list) else 0
    return max(
        int(REACT_MAX_ROUNDS_EXECUTION),
        min(n_steps + int(REACT_EXECUTION_ROUNDS_MARGIN), int(REACT_MAX_ROUNDS_EXECUTION_CAP)),
    )


# S7-11（T-S7-7-3）：修复轮上下文里"上一轮改了哪些文件"的展示条数上限。
# 只为防长列表撑爆 context，不是产品语义 ⇒ 不进 config.py。
_LAST_FIX_FILES_MAX: int = 10


def _build_last_fix_context(state: GlobalState) -> Dict[str, Any]:
    """上一轮编码环节的修复自述 + 改动文件清单（修法 A，S7-11 / T-S7-7-3）。

    - 数据源是 coding 侧单点写入的 ``last_fix_note`` / ``last_files_written``，
      本函数只读不写、不新开 state 通道（``fix_loop_history`` 是历史累积，本轮那
      份就在 ``last_*`` 里，读历史既重复又更贵）；
    - ``files`` 取 basename（对齐 coding 侧 ``_digest_fix_loop_history`` 的脱敏/瘦身
      口径），并截断到 ``_LAST_FIX_FILES_MAX`` + 计数尾巴 —— 顺带保证 payload 里不
      出现绝对路径，字节幂等不被机器路径污染；
    - ``note`` 沿用 coding 侧的 ``_FIX_NOTE_MAX_CHARS`` 上限（不新增常量）；
    - **两项皆空 → 返回空 dict**，调用方据此不注入该键（零扰动范式）。
    """
    note = state.get("last_fix_note", "")
    note = str(note).strip()[:_FIX_NOTE_MAX_CHARS] if isinstance(note, str) else ""
    raw_files = state.get("last_files_written") or []
    files: List[str] = []
    if isinstance(raw_files, list):
        for item in raw_files:
            name = PurePosixPath(str(item).replace("\\", "/")).name.strip()
            if name:
                files.append(name)
    total = len(files)
    shown: List[str] = files[:_LAST_FIX_FILES_MAX]
    if total > _LAST_FIX_FILES_MAX:
        shown = shown + [f"...共 {total} 个"]
    if not note and not shown:
        return {}
    out: Dict[str, Any] = {}
    if note:
        out["note"] = note
    if shown:
        out["files"] = shown
    return out


def _build_execution_agent_context(
    state: GlobalState,
    work_dir: str,
    plan: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """curated 动态上下文（HumanMessage 通道，json.dumps sort_keys 字节幂等）。

    修复回合（fix_loop_count > 0 且已有上一轮 execution_result）注入摘要级反馈：
    上一轮的错误摘要 + **本轮代码已发生的改动**（改了哪些文件、编码环节怎么改的），
    使 agent 有依据重跑验证修复，而不是绕开上一轮失败过的步骤（stderr 尾部裁剪防
    撑爆 context）。⚠ 措辞是刻意的：S7-11 之前这句写的是"帮助 agent 绕过上一轮已知
    错误"——那把设计意图写反了，且 agent 当时确实照做（修复轮只补跑两条命令就收尾）。
    修复轮上下文的目的是**重跑验证**，不是躲开。

    P3（sp5 R-PC4）：轮次预算数字从 system prompt 主体迁出、经本动态通道注入
    （主体只留非数字表述）。T-S5-2-5 起为 _effective_max_rounds(plan) 联动值
    ——同一 plan 确定性产出，动态值走动态通道，字节幂等不破坏稳定前缀。
    """
    plan = plan if isinstance(plan, dict) else {}
    payload: Dict[str, Any] = {
        "work_dir": work_dir,
        "execution_steps": plan.get("execution_steps"),
        "environment": plan.get("environment"),
        "max_rounds": int(_effective_max_rounds(plan)),
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
        # S7-11（T-S7-7-3，修法 A）：上一轮编码环节做了什么——修复说明 + 改动文件清单。
        # 数据现成（coding.py 单点写 last_fix_note / last_files_written），此前只进
        # fix_loop_history 供下一回合 coder 参考，从未送到 execution agent 眼前 ⇒
        # agent 在它的认知里"那些命令还是坏的"，自然不会重跑验证。
        # 沿 credential_degradations / scale_reduced_directive 的"非空才注入"范式：
        # 无 fix_note 且无 files 时 payload 与 sp7 基线字节零扰动。
        last_fix = _build_last_fix_context(state)
        if last_fix:
            payload["last_fix"] = last_fix

    # S6-B2（T-S6-2-3）：gate 放行后的降级事实注入——用户已显式降级的凭证
    # {purpose_key: purpose} 摘要告知 agent，触发模拟路径。
    # 非空才注入（零降级路径的 HumanMessage 字节零扰动）。
    degradations = state.get("credential_degradations") or {}
    if isinstance(degradations, dict) and degradations:
        payload["credential_degradations"] = {
            str(k): str(v) for k, v in degradations.items()
        }
        payload["credential_degradations_directive"] = _CREDENTIAL_DEGRADATIONS_DIRECTIVE

    # S7-08（T-S7-5-8，架构 §18.1.2 落点 8 + §18.7(5)(6)）：缩规模指令下游贯穿——
    # 规划已按本机可跑规模缩过时，把"规模参数是硬约束"这层约束显式送给执行 agent，
    # 防其把训练步数 / 数据量放大回论文原始规模。沿 credential_degradations 同款
    # "非空才注入"范式：
    #   - 用 `is True` 而非真值判断——旧 checkpoint 里该键若为 "false" 字符串，
    #     bool("false") is True 会误注入（架构 §18.7(6) 点名的下游对称面）；
    #   - 假 / 缺键 / "false" 三形态下 payload 与 sp5 基线字节一致（零扰动）；
    #   - 走 HumanMessage 动态通道（json.dumps sort_keys 字节幂等），system prompt 零改动。
    # 读的是本函数入参 plan（:1105 已归一为 dict）——它就是本次要执行的那份计划，
    # 与 execution_steps / environment / max_rounds 同源，不另开 state 读取通道。
    # 生产路径上 plan 恒等于 state["reproduction_plan"]（node 主体 → _run_execution_agent 透传）。
    if isinstance(plan, dict) and plan.get("scale_reduced") is True:
        payload["scale_reduced_directive"] = _SCALE_REDUCED_DIRECTIVE

    # S7-13（T-S7-9-1）：计划预期送到执行 agent 眼前。
    # 在此之前本函数只传 execution_steps + environment，**从未传 expected_results**
    # ⇒ agent 无从知道计划管那一组方法叫 "t-SNE"、管那个指标叫 "k-NN classifier
    # accuracy"，`<result>.metrics` 里的 group / name 就只能按产物目录名与代码字段名
    # 填，回到"名字对不上"的老路（实测：`_match_metrics_group("t-SNE", …)` → None）。
    # 不补这一处，EXECUTION_OUTPUT_SCHEMA 里"用计划写法"那条约束直接落空。
    # 沿 credential_degradations / scale_reduced_directive 同款"非空才注入"范式：
    # 无 expected_results 的计划下 payload 与 sp7 基线字节零扰动。
    expected_results = plan.get("expected_results")
    if expected_results:
        payload["expected_results"] = expected_results

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
    """从子图 messages 回读 prepare_environment 结果序列。

    sp5 T-S5-2-6（S5-10 key_packages 修复）：P6 起工具返回 payload 带 env_info
    （python_version / key_packages），回读随之重建——R-S4-10"回读为权威"合并
    不再用空占位覆盖收集器真值（恒空根因，架构 §7.10）。install_log / pip_exe
    仍不在工具返回 JSON 内，回读为空占位（保真弱于收集器）。失败 ToolMessage
    过滤 + 零成功记录 WARNING 纪律（BUG-S1-02/03）不变。"""
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
        raw_env = payload.get("env_info")
        out.append(SandboxPrepareResult(
            success=bool(payload.get("success")),
            venv_dir=str(payload.get("venv_dir") or ""),
            python_exe=str(payload.get("python_exe") or ""),
            pip_exe="",
            env_info=(
                {str(k): str(v) for k, v in raw_env.items()}
                if isinstance(raw_env, dict) else {}
            ),
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


# sp8 T-S8-2-4（Q-S8-01，架构 §1.3）：agent 收尾汇报的回读通道。
# 🔴 **零新依赖、不 import 私有符号**：正则按 config 的同一对常量在本模块自建，
# **不 import react_base 的 `_RESULT_TAG_PATTERN`**（它是私有的；与
# reporting._resolve_report_path 自写边界判定同一取向）—— 跨模块借私有符号会把两个
# 模块焊死在一起，而这里要的只是"同一对标签常量"，config 已经是那个单一真源。
# ⚠ 故意取名 _RESULT_TAG_RE 而非同名：两个模块里出现同名私有量，正是本条要防的
# 那种混淆。CP-2.4-9 的"零命中"判在 **import 层 + 属性访问层**（R-S8-44：零命中型
# 断言必须写明在哪一层零）——本行为把理由留痕，字符串层必然点名它一次。
_RESULT_TAG_RE = re.compile(
    re.escape(REACT_RESULT_TAG_OPEN) + r"(.*?)" + re.escape(REACT_RESULT_TAG_CLOSE),
    re.DOTALL,
)


def _ai_message_text(msg: BaseMessage) -> str:
    """提取 AIMessage 文本内容（兼容 content 为 list[parts] 的情况）。

    与同文件 _tool_message_text 同款、只是消息类型不同（本文件内既有范式）。
    """
    content = getattr(msg, "content", "")
    if isinstance(content, list):
        content = "".join(
            c if isinstance(c, str) else (c.get("text") or "") if isinstance(c, dict) else ""
            for c in content
        )
    return content if isinstance(content, str) else str(content)


def _resolve_agent_report(
    final_state: Any,
    final_messages: Optional[List[BaseMessage]],
) -> Dict[str, Any]:
    """agent 收尾汇报的取数单点（Q-S8-01）。

    与 _merge_with_collector 同一范式家族、方向镜像：
      - _merge_with_collector 治的是"保真度差"（收集器全文 > 回读尾部）⇒ 收集器优先；
      - 本函数治的是"存在性差"（两边字节同源、无截断差）⇒ 子图 result 优先，
        缺失 / 为空时用 messages 末条 <result> 回读补位。
    两条都拿不到 → 返回 {}，由调用方走封顶（**绝不因此判失败**，架构 §1.4）。

    🔴 为什么档位不进 _SandboxRunCollector（架构 §1.1 裁定 1，落地时最容易做反的一件事）：
    收集器治的是累积型数据在 resume 后被重建导致的前半段丢失；判定是 finalize 终态
    **一次性**写出的，压根没有"前半段"。塞进收集器不但拿不到额外保真度，反而把一个
    "一次写"的数据主动降级成累积型 —— 那正是 Q-S8-01 要避免的结果。

    🔴 回读只治"在不在"、不治"全不全"：两条通道读的是同一份 JSON 的同一份字节。
    故 result 非空时**一律不回读**——架构 §1.3 docstring 原文里的"必填不全时回读补位"
    在 react_base 现状下是**反向操作**：finalize_node 对必填不全的情形已经先 schema
    重生成、再与标签解析结果 merge（react_base.py:745-751），落进 result 的必然是
    末条 <result> 的**超集**；此时改用回读只会把超集换成子集。登记见 dev-plan §15.0e。
    """
    result = final_state.get("result") if isinstance(final_state, dict) else None
    if isinstance(result, dict) and result:
        return dict(result)

    # 🔴 三条写死的防"假绿通道"纪律（AR-S8-02），一条都不许放宽：
    #   ① 只认 <result> 标签包裹 —— 不采信"任意 AIMessage 里的 JSON 块"（agent 的
    #      中间推理里经常带 JSON，采信它等于让没收尾的回合伪装成有汇报）；
    #   ② 只取最后一条 —— 逆序扫到的首条命中即全局末条，同一条消息内也取末块；
    #   ③ 解析失败即空 —— 不回头去试更早的标签，也不返回部分结果。
    tag_text: Optional[str] = None
    for msg in reversed(list(final_messages or [])):
        if not isinstance(msg, AIMessage):
            continue
        blocks = _RESULT_TAG_RE.findall(_ai_message_text(msg))
        if blocks:
            tag_text = blocks[-1]
            break

    # 🔴 禁静默吞错（陷阱 3）。⚠ 这里与 reported_metrics 的"零指标不打 WARNING"
    # **故意相反**：零指标是合法常态（跑失败、只跑了 prepare），而**档位缺失不是**
    # —— 两条通道都没交出汇报意味着子图没能正常收尾，必须留痕。后人若照
    # reported_metrics 的先例把这里"统一"成不打日志，就把唯一的现场线索抹掉了。
    if tag_text is None:
        logger.warning(
            "[%s] agent 收尾汇报两条通道皆空（final_state.result 缺失/为空，且 messages 末尾"
            "无 %s 标签），本回合按「无可核验产出」走封顶，不因此判失败（架构 §1.4）",
            NODE_NAME, REACT_RESULT_TAG_OPEN,
        )
        return {}

    try:
        parsed = json.loads(tag_text)
    except (TypeError, ValueError) as exc:
        logger.warning(
            "[%s] messages 末条 %s 标签存在但内容非法 JSON，汇报按缺失处理: %s",
            NODE_NAME, REACT_RESULT_TAG_OPEN, exc,
        )
        return {}
    if not isinstance(parsed, dict) or not parsed:
        logger.warning(
            "[%s] messages 末条 %s 标签解析出的不是非空对象（%s），汇报按缺失处理",
            NODE_NAME, REACT_RESULT_TAG_OPEN, type(parsed).__name__,
        )
        return {}

    logger.info(
        "[%s] agent 收尾汇报由 messages 末条 %s 回读补位（子图 result 通道为空，"
        "架构 §1.2 路径 (b)/(c)）",
        NODE_NAME, REACT_RESULT_TAG_OPEN,
    )
    return parsed


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
            make_request_user_input_tool(state.get("credential_degradations") or {}),  # interrupt#3（B2 门禁已过，2026-07-04）
            # sp8 T-S8-1a-4（S8-03，架构 Q-S8-03 方案 A）：执行环节接入两个**只读**
            # 文件工具 —— 没有它，agent 既看不到自己刚跑出来的 outputs/、也没法读
            # 参考仓库里的结果表来诊断问题，"让执行环节自己判断复现结果"整件事无从谈起。
            # 🔴 **不新造工具**（PRD §4.3 明令）：直接复用 coding 侧既有的两个工厂。
            #
            # 🔴 **两个闸物理分处两文件，永远不许合并**（架构 §3.3）：
            #   ┌ 工具边界 = "agent 能读什么" → code_fs_tools._is_within_workspace
            #   │   作用域 = **整个工作区**（含参考仓库 selected_repo.local_path）。
            #   │   本次 code_fs_tools.py **一字不改**；明确否决给 make_read_code_file_tool
            #   │   加 base_dir 在工具层收窄（架构 §3.2 方案 C）——那会砍掉"读参考仓库
            #   │   诊断问题"的能力，**直接违反 PRD §4.3**。
            #   └ 证据边界 = "什么能当判定物证" → execution._verify_evidence 第④重
            #       （批次 2 T-S8-2-5），作用域 = **仅 code_output_dir 之下**。
            # ⇒ agent 读参考仓库里的结果表**不被拒绝**，但**拿它当物证一律不成立**。
            #
            # ⚠ 只读：两个工具都不写盘。"执行环节不得写代码"的硬防线
            # （is_inline_code_write / 管道重定向拒绝）本次一字不动。
            make_read_code_file_tool(),
            make_list_dir_tool(),
        ]

        # 装配项 2：消息装配（Prompt Cache 方案 A）。
        system_prompt = _build_execution_system_prompt()
        context = _build_execution_agent_context(state, work_dir, plan)
        initial_messages: List[BaseMessage] = [SystemMessage(content=system_prompt)]
        human_text = json.dumps(context, ensure_ascii=False, sort_keys=True, default=str)
        initial_messages.append(HumanMessage(content=human_text))

        # sp5 T-S5-2-5（Q-S5-7）：轮次预算与计划步数联动（确定性 helper，两处消费点
        # 同源同值；HumanMessage 内 max_rounds 数字亦出自同一 helper，见装配项 2）。
        #
        # sp7 T-S7-1-1（S7-03，架构 §6.2）：入口收窄——把"本轮子图轮次上限"收窄为
        # "联动值"与"剩余子预算"的较小值，让现成 budget_check_node（react_base.py:621-629）
        # 在本轮内即刹住跨回合累计的 dev_calls（单轮不再一口气烧满 CAP 冲过子上限）。
        # 只改"本轮子图轮次上限"（入口收窄，非新埋点）；零 react_base 改动、零计量口径
        # 改动（_dev_loop_llm_calls 累加口径一字不动）。保底 1 轮防 0 轮死锁/退化（R-S7-5）。
        # R-PC4 无扰：context（:1341 已构造，早于此处）里的 max_rounds 仍是联动值不收窄
        # ——收窄是 agent 无需感知的系统级护栏，不回灌 context，避免动态通道字节因
        # dev_calls 变化而抖动（架构 §6.2 / AA-S7-6）。
        base_rounds = _effective_max_rounds(plan)  # 联动公式，不变
        dev_calls_so_far = state.get("_dev_loop_llm_calls", 0) or 0
        remaining_sub_budget = max(0, MAX_DEV_LOOP_LLM_CALLS - dev_calls_so_far)
        effective_max_rounds = max(1, min(base_rounds, remaining_sub_budget))
        # S7-13（T-S7-9-1）：补传 result_schema —— 在此之前本处**不传第 5 个参数**，
        # 而 react_base.py:509 一直支持、coding.py:894 一直在传 ⇒ execution 的 agent
        # 全程在场却没有汇报出口，指标只能靠代码从原始 stdout / 产物文件里猜。
        subgraph = create_react_subgraph(
            node_name=NODE_NAME,
            system_prompt=system_prompt,
            tools=tools,
            max_rounds=effective_max_rounds,
            result_schema=EXECUTION_OUTPUT_SCHEMA,
        )
        # 装配项 3：ReActState 初始化。
        initial: Dict[str, Any] = {
            "messages": initial_messages,
            "round": 0,
            "max_rounds": effective_max_rounds,
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

    # sp5 T-S5-2-5（Q-S5-7 / AC-S5-12）：截断显式化，零 react_base 改动的确定性
    # 代理判据——budget_check 在 round >= max_rounds-1 触发、force_finish 再 +1 轮，
    # 故 rounds_used >= effective_max_rounds ⇔ 走了 force_finish 截断路径
    # （正常收尾 round 恒 <= max_rounds-1）。"任何预算截断必须显式 log + state 记录"
    # 项目通则的 sp5 首个落点：INFO 日志在此，state 记录经 exec_result 一次 commit。
    budget_truncated = rounds_used >= effective_max_rounds
    if budget_truncated:
        logger.info(
            "[%s] 轮次预算截断（budget_truncated）: rounds_used=%d >= effective_max_rounds=%d"
            "（force_finish 收尾），标记随 execution_result 落盘（AC-S5-12）",
            NODE_NAME, rounds_used, effective_max_rounds,
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

    # sp8 T-S8-2-4（Q-S8-01）：agent 收尾汇报的**单一取数口**。此前本处直接读
    # `final_state["result"]`，那是架构 §1.2 三条缺失路径里 (b)(c) 的正面暴露——
    # result 为空时整份汇报就没了，而同一份字节其实还躺在 messages 末条 <result> 里。
    report = _resolve_agent_report(final_state, final_messages)

    # S7-13（T-S7-9-1）：取 agent 自报的指标数组（原样透传不清洗）。改为 report 的
    # 派生量，**不再单独读 final_state["result"]** ⇒ 消除两个取数口径（架构 §1.3）。
    # 非 dict / 非 list 一律降级空数组——**不打 WARNING**：零指标回合是合法常态
    # （跑失败、只跑了 prepare），打了就是噪声。⚠ 这与 _resolve_agent_report 里
    # "两通道皆空必打 WARNING" 的反差是**有意的**，理由见该函数注释，勿"统一"。
    raw_reported = report.get("metrics")
    reported_metrics: List[Any] = list(raw_reported) if isinstance(raw_reported, list) else []

    logger.info(
        "[%s] execution agent 完成: rounds=%d, prep_success=%s, run_results=%d, "
        "reported_metrics=%d",
        NODE_NAME, rounds_used,
        (prep.success if prep is not None else None), len(run_results),
        len(reported_metrics),
    )
    return ExecAgentOutput(
        prep=prep,
        run_results=run_results,
        rounds_used=rounds_used,
        llm_calls=rounds_used,
        step_ledger=list(collector.step_ledger),
        budget_truncated=budget_truncated,
        reported_metrics=reported_metrics,
        report=report,
    )


# ---------------------------------------------------------------------------
# 步骤 4.5（sp5 S5-10，T-S5-2-6）：多组指标确定性收编（架构 §7.10）
# ---------------------------------------------------------------------------

# 顶层 str 字段收编长度上限：超长视为日志/长文本混入，跳过不收（防污染对比表）。
_GROUP_METRIC_STR_MAX_LEN: int = 120


def _collect_grouped_metrics(work_dir: str) -> Dict[str, Dict[str, Any]]:
    """步骤 4.5：扫描 ``<work_dir>/outputs/**/summary.json`` 收编多组实验指标。

    确定性纯函数（零 LLM），架构 §7.10 裁决（文件扫描约定，弃"扩展 <METRICS>
    多块"方案）：
        - 组名 = summary.json 相对 outputs/ 的父目录 POSIX 路径（回归样本即
          evoskills_smoke / baselines/no_skill / baselines/self_generated）；
        - 每文件只收**顶层**数值/布尔/短字符串字段——深层嵌套 dict/list/None/
          超长 str 跳过（与档 1 _extract_metrics_block 同口径，防嵌套大对象
          污染对比表）；
        - 既有 <METRICS> 三档主通道语义零改动（metrics 仍是主实验指标，本函数
          产出独立落 ExecutionResult.metrics_groups）。

    容错（CP-2.6-2）：无 outputs 目录 → ``{}``；损坏 JSON / 顶层非 dict / 读取
    失败 → 容忍跳过 + WARNING（非静默吞错）。str 值过 ``mask_value`` 后落 state
    （生成代码的输出理论上可能内嵌敏感值，脱敏出口纪律同 §9.3）；文件按路径
    排序遍历，产出确定性。
    """
    groups: Dict[str, Dict[str, Any]] = {}
    if not work_dir:
        return groups
    outputs_dir = Path(work_dir) / "outputs"
    if not outputs_dir.is_dir():
        return groups
    for path in sorted(outputs_dir.rglob("summary.json")):
        try:
            parsed = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.warning(
                "[%s] 多组指标收编：summary.json 读取/解析失败，容忍跳过 %s: %s",
                NODE_NAME, path, exc,
            )
            continue
        if not isinstance(parsed, dict):
            logger.warning(
                "[%s] 多组指标收编：summary.json 顶层非 dict（%s），容忍跳过 %s",
                NODE_NAME, type(parsed).__name__, path,
            )
            continue
        fields: Dict[str, Any] = {}
        for k, v in parsed.items():
            if isinstance(v, (bool, int, float)):
                fields[str(k)] = v
            elif isinstance(v, str) and len(v) <= _GROUP_METRIC_STR_MAX_LEN:
                fields[str(k)] = mask_value(v) or ""
            # 其余（dict/list/None/超长 str）：只收顶层标量，跳过。
        groups[path.parent.relative_to(outputs_dir).as_posix()] = fields
    return groups


# ---------------------------------------------------------------------------
# 步骤 4.4（sp7 S7-13，T-S7-9-1）：agent 自报指标拆分（主实验 / 分组）
# ---------------------------------------------------------------------------


def _coerce_reported_value(value: Any) -> Tuple[bool, Any]:
    """标量收编，口径与 `_collect_grouped_metrics` 完全一致。

    返回 ``(是否可收, 收编后的值)``——用 tuple 而不是 ``Optional`` 是因为 ``None``
    与 ``""`` / ``0`` / ``False`` 都是合法取值，单靠返回值无法区分"不可收"。
    """
    if isinstance(value, bool) or isinstance(value, (int, float)):
        return True, value
    if isinstance(value, str):
        text = value.strip()
        if not text or len(text) > _GROUP_METRIC_STR_MAX_LEN:
            return False, None
        # 生成代码的输出理论上可能内嵌敏感值，脱敏出口纪律同 §9.3。
        return True, (mask_value(text) or "")
    return False, None


def _split_reported_metrics(
    reported: Any,
) -> Tuple[Dict[str, Any], Dict[str, Dict[str, Any]]]:
    """把 agent `<result>.metrics` 数组拆成 ``(主实验指标, 分组指标)``。

    确定性纯函数（零 LLM、零磁盘 IO），是 S7-13「让 agent 汇报、代码只管判定」的
    唯一清洗点：

        - ``group`` 缺省 / null / 去空白后为空 ⇒ **主实验指标**（第一个返回值）；
        - ``group`` 非空 ⇒ 落 ``{组名: {指标名: 值}}``（第二个返回值），组名**保持
          agent 填的原文**（它填的是计划预期里的写法，`reporting._match_metrics_group`
          归一后正好与 ``trend.greater`` / ``trend.lesser`` 对得上——名字对不上的问题
          在这里是被**绕过**的，不是被修补的）；
        - 值只收标量（口径同 `_collect_grouped_metrics`），str 过 ``mask_value`` +
          120 字符上限；
        - 同一 (组, 指标名) 重复：**先到先得**，绝不后覆盖前（"后覆盖前"正是本批要治
          的病：把某一步的值冒充成另一步的）；取值不同的重复打 WARNING 留痕；
        - 畸形条目（非 dict / 无 name / 值非标量）跳过并**打 WARNING**（已知 bug 模式
          #3：禁止静默吞错）；
        - 产出按组名、指标名 ``sorted``，同一份输入连跑逐字节相同。

    ⚠ 本函数的输出属**agent 自报**，绝不得进入 `_reconcile_steps` /
    `_completion_insufficient` / `exit_ok` 任何一处（R-S4-01 红线）。
    """
    main: Dict[str, Any] = {}
    groups: Dict[str, Dict[str, Any]] = {}
    if not isinstance(reported, list) or not reported:
        return main, groups

    collected: Dict[str, Dict[str, Any]] = {}
    skipped: List[str] = []
    conflicts: List[str] = []
    for item in reported:
        if not isinstance(item, dict):
            skipped.append(f"非对象条目({type(item).__name__})")
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            skipped.append("name 缺失或为空")
            continue
        ok, value = _coerce_reported_value(item.get("value"))
        if not ok:
            skipped.append(f"{name}: value 非标量或超长")
            continue
        raw_group = item.get("group")
        group = str(raw_group).strip() if isinstance(raw_group, str) else ""
        bucket = collected.setdefault(group, {})
        if name in bucket:
            if bucket[name] != value:
                conflicts.append(f"{group or '(主实验)'}::{name}")
            continue  # 先到先得
        bucket[name] = value

    if skipped:
        logger.warning(
            "[%s] agent 自报指标：%d 条畸形条目已跳过（%s）",
            NODE_NAME, len(skipped), "; ".join(skipped[:5]),
        )
    if conflicts:
        logger.warning(
            "[%s] agent 自报指标：%d 处同名异值重复，保留首次出现值（%s）",
            NODE_NAME, len(conflicts), "; ".join(sorted(set(conflicts))[:5]),
        )

    main = dict(sorted(collected.get("", {}).items()))
    for group_name in sorted(k for k in collected if k):
        groups[group_name] = dict(sorted(collected[group_name].items()))
    return main, groups


# ---------------------------------------------------------------------------
# 步骤 4.6（sp5 S5-06，T-S5-2-4）：确定性步骤对账（架构 §2 Q-S5-6 / §7.6 / §10.1 R-2）
# ---------------------------------------------------------------------------
# 产品红线：执行事实不得来自 agent 单方声明——agent <result> 自报的"已执行步数"
# 字段仅供参考，**绝不进入本区段任何函数**（该字段名在本模块源码中只出现在 prompt
# 常量内，无任何代码消费点——CP-2.4-4 结构守门）。对账事实源 = 编排层工具台账
# （step_ledger）+ 真实 run_results。


def _normalize_argv_for_match(argv: Any) -> Tuple[str, ...]:
    """归属规则②的命令归一（计划侧与执行侧对称使用，确定性纯函数）。

    复用 ``_rewrite_interpreter`` 同套改写（裸 python/python3/py → 统一 "python"、
    pip/pip3 → python -m pip），并把**解释器绝对路径**（执行侧 argv[0] 已被工具改写
    为 venv python 路径，即"改路径"；pip → python -m pip 即"补参"）折叠为 basename
    后归一——使 "python train.py"（计划）与 ["/w/.venv/bin/python3.11", "train.py"]
    （真实执行）归一后精确相等。非解释器 head（bash / ./run.sh 等）不做路径折叠，
    避免跨目录同名脚本误归属（误报防线优先）。
    """
    toks = [str(t) for t in (argv or []) if str(t)]
    if not toks:
        return ()
    base = Path(toks[0]).name
    if base.startswith("python") or base in ("py", "pip", "pip3"):
        # python3.11 等变体统一折叠为 "python"；py/pip/pip3 交给 _rewrite_interpreter。
        head = "python" if base.startswith("python") else base
        toks = [head] + toks[1:]
    return tuple(_rewrite_interpreter(toks, "python"))


def _step_display_name(step: Any, index: int) -> str:
    """未执行清单条目的展示名：step_name 优先，缺失回落 command 串，再缺退位序号。"""
    if isinstance(step, dict) and step.get("step_name"):
        return str(step["step_name"])
    return _extract_command_str(step) or f"step_{index}"


def _reconcile_steps(
    plan_steps: Optional[List[Any]],
    run_results: List[SandboxRunResult],
    step_ledger: Optional[List[Tuple[int, List[str], int]]] = None,
) -> Dict[str, Any]:
    """计划步骤 vs 真实执行的确定性对账（Q-S5-6，100% 确定性代码计算）。

    归属三级（优先级从上到下，作用于 effective runs——同命令最后一次，L-E4-01 口径）：
        ① 台账条目带合法 step_index（0 <= idx < planned）→ 声明归属；越界丢弃 +
           WARNING（idx == -1 是"未声明"哨兵，不告警）；同命令多次声明以最后一次为准
           （与 effective runs 同向的确定性 tie-break）；
        ② 无标签条目与计划步骤 command 同套归一（_split_top_level 拆顶层 && / ; +
           _normalize_argv_for_match）后精确匹配 → 归属（cd/source 子命令不参与——
           执行侧本就不产生 run）；
        ③ 仍不匹配 → extra_commands 计划外命令（不折算步骤）。
    "已完成" = 该步归属的全部 effective run exit_code==0（复合 && 步骤产生多条台账，
    须全 0 才算完成）。

    R-2 保守语义（误报防线优先于命中）：全零归属 ∧ run_results 非空 ∧ planned > 0
    → attribution_unavailable=True 且 unexecuted_steps 置空——"无法归属 ≠ 未执行"，
    下游 incomplete_execution 标注规则（架构 §7.4：存在未执行步骤 ∨ budget_truncated）
    自然不点火；原始命令如实保留在 extra_commands 供报告展示。

    脱敏出口②（架构 §9.3）：extra_commands 与 unexecuted_steps 内命令串/步骤名
    一律过 mask_value 后落 state（命令可能内嵌 token）。

    产品红线：agent <result> 自报的"已执行步数"不是本函数入参，不参与任何判定。
    """
    steps = list(plan_steps or [])
    planned = len(steps)
    # BUG-S7-11-01（2026-08-01）：完成度分母必须是**可执行步数**，不是原始步数。
    # planned 仍是原始步数——它是 ① 自报下标的合法区间（下标恒对原始步序有效，
    # 两套编号绝不可混用）与 ② 报告"计划共 N 步"的口径；新增的 planned_actionable
    # 才是判定分母（详见 _is_actionable_step / _completion_denominator）。
    actionable_idx = {i for i, s in enumerate(steps) if _is_actionable_step(s)}
    planned_actionable = len(actionable_idx)
    effective = _effective_runs(list(run_results or []))

    # 归属规则①：台账合法声明 map（命令 tuple → step_index，最后一次声明为准）。
    declared: Dict[Tuple[str, ...], int] = {}
    for entry in step_ledger or []:
        try:
            idx_raw, cmd, _exit = entry
            idx = int(idx_raw)
        except (TypeError, ValueError):
            logger.warning("[%s] 对账台账条目畸形，丢弃: %r", NODE_NAME, entry)
            continue
        if idx == -1:
            continue  # 未声明（计划外/无标签），交给规则②/③
        # ⚠ 越界判据用**原始步数** planned（不是 planned_actionable）：agent 自报的
        # step_index 指向的是它看到的那份计划的原始步序，剔除不可执行步骤只影响分母、
        # 不重排步序。用 actionable 数做上界会把靠后的合法声明误丢（BUG-S7-11-01 修复
        # 时的头号混淆点）。
        if not (0 <= idx < planned):
            logger.warning(
                "[%s] 对账台账 step_index 越界丢弃: index=%d planned=%d cmd=%s",
                NODE_NAME, idx, planned,
                mask_value(" ".join(str(c) for c in (cmd or []))) or "",
            )
            continue
        declared[tuple(str(c) for c in (cmd or []))] = idx

    # 归属规则②：计划步骤命令归一索引（冲突时归属靠前步骤，确定性）。
    plan_index: Dict[Tuple[str, ...], int] = {}
    for i, step in enumerate(steps):
        cmd_str = _extract_command_str(step)
        if not cmd_str:
            continue
        for argv, _conn in _split_top_level(cmd_str):
            if not argv or argv[0] in ("cd", "source", "."):
                continue
            key = _normalize_argv_for_match(argv)
            if key and key not in plan_index:
                plan_index[key] = i

    step_runs: Dict[int, List[SandboxRunResult]] = {}
    extra_commands: List[str] = []
    for r in effective:
        cmd_t = tuple(str(c) for c in (r.command or []))
        if cmd_t in declared:  # ① 声明归属
            step_runs.setdefault(declared[cmd_t], []).append(r)
            continue
        key = _normalize_argv_for_match(list(cmd_t))
        if key in plan_index:  # ② 归一精确匹配兜底
            step_runs.setdefault(plan_index[key], []).append(r)
            continue
        extra_commands.append(" ".join(cmd_t))  # ③ 计划外命令

    completed = sum(
        1 for runs in step_runs.values() if all(rr.exit_code == 0 for rr in runs)
    )
    attribution_unavailable = bool(effective) and not step_runs and planned > 0
    if attribution_unavailable:
        logger.warning(
            "[%s] 步骤对账全零归属（planned=%d, effective_runs=%d）→ "
            "attribution_unavailable 保守语义：不标注未执行步骤，原始命令清单如实保留（R-2）",
            NODE_NAME, planned, len(effective),
        )
        unexecuted: List[Dict[str, Any]] = []
    else:
        # BUG-S7-11-01：不可执行的步骤不进"未执行清单"——它不是"agent 该跑却没跑"，
        # 而是"压根没有可跑的命令"。留在清单里会让 reporting 的 incomplete_execution
        # 标注在 success=True 时照样点火，制造 CP-7.9-3 明令禁止的自相矛盾报告
        # （"判定成功"却横幅说"没跑完"），同时给 coder 一条它变不出命令的伪修复目标。
        unexecuted = [
            {"index": i, "step_name": mask_value(_step_display_name(steps[i], i)) or ""}
            for i in range(planned)
            if i not in step_runs and i in actionable_idx
        ]

    return {
        "planned": planned,
        "planned_actionable": planned_actionable,
        "executed": len(step_runs),
        "completed": completed,
        "unexecuted_steps": unexecuted,
        "extra_commands": [mask_value(c) or "" for c in extra_commands],
        "attribution_unavailable": attribution_unavailable,
    }


# ---------------------------------------------------------------------------
# S7-11（T-S7-7-5）：完成度判定谓词 + 自报防伪留痕
# ---------------------------------------------------------------------------
# 设计权威：dev-plan §49.0 变更 1 / §49.2 第 5 条（Maria 2026-08-01 复审拍板）。
# ⚠ 本批**不**新写确定性完成度算法：完成度唯一取自 _reconcile_steps 的产出
# （agent 自报 step_index 为主、命令归一匹配兜底）。理由是立项那次真跑的实测——
# agent 首轮诚实声明了 8/9 步，**根本没有虚报**；问题从来不在自报可信度，而在
# "exit_code 全 0 就算成功"这个口径（做得多反而判失败、做得少反而判成功）。
# 采信自报的代价（理论上可被刷满）由下方 _audit_declared_steps 的 WARNING 留痕对冲。

# 自报不符的 WARNING 里最多并排展示几条（防长清单刷屏，非产品语义 ⇒ 不进 config.py）。
_AUDIT_MISMATCH_LOG_MAX: int = 5


def _completion_insufficient(recon: Optional[Dict[str, Any]]) -> bool:
    """计划步骤是否"没跑完"（★ 单点谓词，S7-11 / T-S7-7-5）。

    ``success`` 判定（``_build_execution_result``）与 ``_apply_incomplete_execution``
    **两处必须都调它**，禁止各写一遍比较——否则日后必漂移出"改判了但 success 还是
    True"这种最隐蔽的假绿（dev-plan §49.3 单点谓词红线，CP-7.6-2 打桩守门）。

    - 语义：``planned_actionable > 0 and completed < planned_actionable``（**单轮全量**
      口径——``run_results``
      / ``step_ledger`` 逐轮重置是正确设计，跨轮取并集等于"把上轮代码下的通过当成本轮
      代码下的通过"，是与本次修复初衷同型的假绿，Q-S7-25(0) + Maria 双重否决）；
    - ``planned_actionable == 0``（无计划步骤，**或计划里一条可执行命令都没有**）→
      ``False``，既有语义零变化（BUG-S7-11-01 修复：架构原裁决要求这一格恒真让位）；
    - 入参非 dict / 缺键 / 键值非 int（旧 checkpoint、畸形快照，R-6）→ ``False``：
      宁可漏判也不误判红——判红会把用户推进修复循环，代价方向更差；
    - ★ 分母走 ``_completion_denominator``（``planned_actionable`` 优先、回落
      ``planned``）：**不可执行的步骤**（无 ``command`` 键 / 空串 / 纯 ``cd``）永远进不了
      分子，把它们算进分母会让 ``success`` **不可达**——agent 完全照做也恒判未完成、
      烧满 ``MAX_FIX_LOOP_COUNT`` 轮、每次真跑都被推到 interrupt#2，且下一轮 coding
      变不出"查看图表"的命令 ⇒ **循环无解**（BUG-S7-11-01，2026-08-01 独立验收发现）；
    - ``attribution_unavailable``（R-2 保守语义）**不特殊对待**：它只置空
      ``unexecuted_steps``（展示层保守），``completed`` 照常为 0 ⇒ 判不成功。
      "跑了一堆计划外命令、一步计划都没归属上"本就不该判成功。
    """
    if not isinstance(recon, dict):
        return False
    denominator = _completion_denominator(recon)
    completed = recon.get("completed")
    if denominator is None:
        return False
    if not isinstance(completed, int) or isinstance(completed, bool):
        return False
    return denominator > 0 and completed < denominator


def _plan_step_keys(step: Any) -> set:
    """单个计划步骤的归一命令 key 集合（``cd`` / ``source`` / ``.`` 子命令不计）。"""
    cmd_str = _extract_command_str(step)
    if not cmd_str:
        return set()
    keys = set()
    for argv, _conn in _split_top_level(cmd_str):
        if not argv or argv[0] in ("cd", "source", "."):
            continue
        key = _normalize_argv_for_match(argv)
        if key:
            keys.add(key)
    return keys


def _is_actionable_step(step: Any) -> bool:
    """该计划步骤是否**有可执行命令**（完成度分母判据，BUG-S7-11-01）。

    判据完全确定性、与 ``_plan_step_keys`` **同一取数点**（它正是归属规则②建索引用的
    那套解析），因此"进得了分母"与"进得了分子"用的是同一把尺子——这是修复的关键：
    只要一条步骤能被归属，它就在分母里；归属不上的（下列三形态）才被剔除。

    判 ``False`` 的三种形态（逐条可单测，边界清晰）：
        - 无 ``command`` / ``cmd`` / ``run`` 键（``_extract_command_str`` 返回 ``None``）；
        - ``command`` 为空串或纯空白（同上）；
        - 拆顶层 ``&&`` / ``;`` 后**只剩** ``cd`` / ``source`` / ``.`` 子命令（执行侧本就
          不产生 run，归属规则②也显式跳过）。

    ⚠ **判 ``True`` 但 agent 其实跑不了的一类：``command`` 写成自然语言描述**
    （"人工查看 outputs/figures 下的图是否正常"）。**本函数刻意不去识别它**——它与
    "真命令写错/拼错"（``pyhton train.py``）在字符串层面**没有确定性判据**可分，任何
    启发式（英文词表、可执行文件存在性、非 ASCII 头 token）都会把真步骤误剔出分母，
    那是往**假绿**方向退（正是 S7-11 本身要修的东西）。⇒ 取舍：**宁可算进分母**。
    该形态的残留后果与本 bug 同型（恒判未完成），根治出口在 planning 侧强制每步
    ``command`` 可执行（跨节点契约，本批不扩围），已登记 dev-plan §56.3。
    """
    return bool(_plan_step_keys(step))


def _audit_declared_steps(
    plan_steps: Optional[List[Any]],
    run_results: List[SandboxRunResult],
    step_ledger: Optional[List[Tuple[int, List[str], int]]] = None,
) -> None:
    """自报归属防伪留痕（★ 纯观测，S7-11 / T-S7-7-5；只打 WARNING、不返回任何值）。

    完成度采信 agent 自报的 ``step_index``（§49.0 变更 1），代价是理论上 agent 给
    任意命令打任意下标就能把完成数刷满（R-S7-65）。本函数是那条产品级假设的**唯一
    对冲手段**：信任但留痕——**自报与实际执行明显不符时记 WARNING，不阻断流程**。

    判据：对每条 effective run，若它带**合法自报下标 i**（``0 <= i < planned``），
    比对它的归一 key 与**计划第 i 步自身**的归一 key 集合；集合非空且 key 不在其中
    ⇒ 记一条不符。
      - ``idx == -1``（未声明）与越界一律跳过——越界已由 ``_reconcile_steps`` 打过
        WARNING，此处不重复告警；
      - 计划第 i 步无命令（缺 command / 空串 / 纯 ``cd``）⇒ **不判**（无从比对，避噪声）。

    ⚠ **写法变通也会命中**（计划写 ``python scripts/x.py``、agent 改用
    ``python -m scripts.x`` 重跑）——**那正是要的观测量**，所以是 WARNING 不是错误：
    不比对字符串才是判定层的正确设计（比对会把正当的写法变通误判成"未完成"，
    dev-plan §56 P-45 / 已作废的 R-S7-61）。

    ★ 纯观测红线：**返回 None**，从签名上杜绝被判定 / 渲染 / state 消费（CP-7.5-4）。
    脱敏：命令与步骤名一律过 ``mask_value``（命令可能内嵌 token，架构 §9.3）。
    """
    steps = list(plan_steps or [])
    planned = len(steps)
    if planned <= 0:
        return
    declared: Dict[Tuple[str, ...], int] = {}
    for entry in step_ledger or []:
        try:
            idx_raw, cmd, _exit = entry
            idx = int(idx_raw)
        except (TypeError, ValueError):
            continue  # 畸形条目已由 _reconcile_steps 告警，此处不重复
        if not (0 <= idx < planned):
            continue  # -1（未声明）与越界都不在本函数职责内
        declared[tuple(str(c) for c in (cmd or []))] = idx

    if not declared:
        return

    mismatches: List[Tuple[int, str, str]] = []
    for r in _effective_runs(list(run_results or [])):
        cmd_t = tuple(str(c) for c in (r.command or []))
        declared_idx = declared.get(cmd_t)
        if declared_idx is None:
            continue
        plan_keys = _plan_step_keys(steps[declared_idx])
        if not plan_keys:
            continue  # 计划该步没写命令 → 无从比对
        if _normalize_argv_for_match(list(cmd_t)) in plan_keys:
            continue
        mismatches.append((
            declared_idx,
            mask_value(_step_display_name(steps[declared_idx], declared_idx)) or "",
            mask_value(" ".join(cmd_t)) or "",
        ))

    if not mismatches:
        return
    shown = mismatches[:_AUDIT_MISMATCH_LOG_MAX]
    detail = "；".join(
        f"自报第 {idx + 1} 步「{name}」← 实际命令 {cmd}" for idx, name, cmd in shown
    )
    logger.warning(
        "[%s] 步骤自报与实际执行不符 %d 条（完成度采信自报，仅留痕不阻断）：%s%s",
        NODE_NAME, len(mismatches), detail,
        f"（另 {len(mismatches) - len(shown)} 条略）" if len(mismatches) > len(shown) else "",
    )



# ---------------------------------------------------------------------------
# 步骤 4.75（S6-B2，T-S6-2-4）：NO_METRICS 合流——exit 0 但无指标时改判
# ---------------------------------------------------------------------------


# S7-11（T-S7-7-6）：改判文案（用户可见——经 _append_fix_record 进 fix_loop_history，
# 由 UI 的"修复历程"折叠条直接展示）⇒ 通俗中文、零内部标识符、零字段名（Q-S7-29b +
# docs/product-design-specification.md:479）。提为模块级具名常量以进术语守门扫描面。
_INCOMPLETE_EXECUTION_SUMMARY_LEAD: str = "命令都正常结束了，但计划里的步骤没跑完"
_INCOMPLETE_EXECUTION_FIX_HINT: str = (
    "请把计划里剩下的步骤按顺序跑完；若某些步骤跑不起来，"
    "排查它们为什么没跑（前置步骤失败？入口脚本不存在？）。"
)
# 未跑完步骤清单在文案里最多列几条（防长清单撑爆 UI 折叠条）。
_INCOMPLETE_STEPS_TEXT_MAX: int = 5

# 🔴 S8-05（T-S8-2-3，Q-S8-04 / 架构 §4.1）：NO_VERIFIABLE_OUTPUT 的改判文案。
# 与上方两条同款——**用户可见**（经 _append_fix_record 进 fix_loop_history，由 UI 的
# "修复历程"折叠条直接展示）⇒ 通俗中文、零内部标识符、零字段名，并提为模块级具名
# 常量以进术语守门扫描面（账目交 T-S8-3-10）。
# ⚠ 消费方 _apply_no_verifiable_output 落在 T-S8-2-7，本任务只立常量、不接线。
_NO_VERIFIABLE_OUTPUT_SUMMARY_LEAD: str = "跑通了，但计划里说好要产出的东西没落地"
_NO_VERIFIABLE_OUTPUT_FIX_HINT: str = (
    "请检查入口脚本有没有真的把结果写成文件、写到了计划声明的位置；"
    "若确实没有可写的结果，先排查实验本身为什么没产出结果，不要补一个空文件了事。"
)


def _apply_incomplete_execution(
    feedback: "ExecutionFeedback",
    recon: Optional[Dict[str, Any]],
    exit_ok: bool,
) -> "ExecutionFeedback":
    """纯函数：命令全跑通但计划步骤没跑完时，改判为 INCOMPLETE_EXECUTION。

    S7-11 修法 D（架构 Q-S7-28~30 + dev-plan §49.2 第 8 条）。**没有这一步，修法 C
    的设计意图会落反**：``success=False`` 并不等于"回修复循环"——路由要求
    ``feedback.auto_fixable`` 为真才回 coding，而"全部 exit 0 + 有指标"这条路径上
    feedback 恒为 ``ErrorCategory.NONE``（``auto_fixable=False``）⇒ 只收严 success
    会把 Maria 拍板的"交修复循环继续补跑"落成"直接打断用户"。

    - 条件：``exit_ok ∧ feedback.category == NONE ∧ _completion_insufficient(recon)``，
      其余情形**原样返回**（与 ``_apply_no_metrics`` 同款结构）；
    - **优先级**：本函数排在 ``_apply_no_metrics`` **上游** ⇒ 命中后 category 不再是
      NONE，后者的前置守卫自动让位（Q-S7-30：优先级靠调用顺序拿，``_apply_no_metrics``
      函数体一行不改）；
    - ``attribution_unavailable`` 时 ``unexecuted_steps`` 恒空 ⇒ 文案走**无清单**分支，
      **不得凭空编造步骤名**。
    """
    if not (
        exit_ok
        and feedback.category == ErrorCategory.NONE
        and _completion_insufficient(recon)
    ):
        return feedback
    recon = recon if isinstance(recon, dict) else {}
    # ⚠ 分母走 _completion_denominator（与 _completion_insufficient 同一口径）：
    # 判定说"没跑完"、文案却按原始步数报另一个分母，会让 coder 与用户看到两个数
    # （BUG-S7-11-01 修复，dev-plan §49.2 第 7 条单一完成度数据源）。
    planned = _completion_denominator(recon)
    completed = recon.get("completed")
    summary = f"{_INCOMPLETE_EXECUTION_SUMMARY_LEAD}（已跑完 {completed}/{planned} 步）"
    names = [
        str(item.get("step_name") or "").strip()
        for item in (recon.get("unexecuted_steps") or [])
        if isinstance(item, dict)
    ]
    names = [n for n in names if n]
    if names:
        shown = names[:_INCOMPLETE_STEPS_TEXT_MAX]
        tail = f" 等共 {len(names)} 个" if len(names) > len(shown) else ""
        summary = f"{summary}：还没跑的有 {'、'.join(shown)}{tail}"
    return ExecutionFeedback(
        category=ErrorCategory.INCOMPLETE_EXECUTION,
        auto_fixable=True,
        summary=summary,
        fix_hint=_INCOMPLETE_EXECUTION_FIX_HINT,
        representative_stderr="",
    )


def _apply_no_metrics(
    feedback: "ExecutionFeedback",
    metrics: dict,
    metrics_groups: dict,
    exit_ok: bool,
) -> "ExecutionFeedback":
    """纯函数：代码跑通但未产出指标时，将 feedback 改判为 NO_METRICS。

    条件：exit_ok ∧ feedback.category == NONE ∧ metrics 为空 ∧ metrics_groups 为空。
    其余情形原样返回。
    """
    if (
        exit_ok
        and feedback.category == ErrorCategory.NONE
        and not metrics
        and not metrics_groups
    ):
        msg = (
            "代码跑通但未产出指标：全部命令 exit 0，但未发现 <METRICS> 输出或"
            " outputs/*/summary.json。请检查执行步骤是否调用了实验主入口，"
            "并按输出约定写出指标。"
        )
        return ExecutionFeedback(
            category=ErrorCategory.NO_METRICS,
            auto_fixable=True,
            summary=msg,
            fix_hint=msg,
            representative_stderr="",
        )
    return feedback


# ---------------------------------------------------------------------------
# 步骤 4.75（sp8 S8-04，T-S8-2-5）：证据台账建账 + 七重验钞
# ---------------------------------------------------------------------------
# ⚠ 上一节的横幅写的**也是**"步骤 4.75"（S6-B2 的 NO_METRICS 合流）。不是笔误，
#   也不在本任务射程内：sp8 的步骤骨架（架构 §1.5）把 4.75 给了本节，NO_METRICS
#   合流那一节随 _apply_no_metrics 由 T-S8-2-7 整体删除，届时重号自然消失。
#   批次 2 内部串行改同一文件，**本任务只加不改**（T-S8-2-2 已声明该纪律）。
#
# 🔴 本节只立函数，**不接线**：调用点在 T-S8-2-11 的步骤 4.75。在那之前它零消费者。

# 单份物证文件为查数值而读入的字节上限；超出部分不参与第③重匹配 ⇒ 查不到即
# **不成立**（保守方向，与 AR-S8-03 对 IO 异常的处置同向，不是"放行"）。
# 🔴 设这个上限不是洁癖：path 完全由 agent 汇报，指向一个几 GB 的 checkpoint
# 是**合法**的（它确实在本次代码目录下、确实可读），全量读进内存会把工作站打爆。
# 2 MB 对 summary.json / metrics.csv / 训练日志这类真物证绰绰有余。
_EVIDENCE_READ_MAX_BYTES: int = 2_000_000

# 🔴 验钞不通过时给出的原因（**用户可见**：随证据台账落盘，报告的逐条回验小节会把
# 引用到未过验证据的条目显著标注出来，T-S8-3-6）⇒ 通俗中文、零内部标识符、零字段名
# （MEMORY §4.2），并提为模块级具名常量以进术语守门扫描面（账目交 T-S8-3-10，沿
# _NO_VERIFIABLE_OUTPUT_SUMMARY_LEAD 同款范式）。
# 🔴 措辞一律**中性**：不成立说的是"无从核对"，**不是"你在造假"**（架构 §16.3.2 第 3 条
# 明令，取向与 §5.6 审计文案的中性要求同源）。核验保证的是"agent 没有二次编造"，
# **不保证"论文值本身是对的"** —— 对外不得说成"论文值已核实"（R-S8-01）。
_EV_REASON_SOURCE_MISSING: str = "这条证据既没写产出文件的位置，也没写论文里的指标名，无从核对"
_EV_REASON_SOURCE_AMBIGUOUS: str = "这条证据同时写了产出文件的位置和论文里的指标名，看不出它到底出自哪一边"
_EV_REASON_OUT_OF_SCOPE: str = "这个位置不在本次复现产出的目录里，不能当作本次跑出来的证据"
_EV_REASON_IN_EXTRA_COMMAND: str = "这个位置出现在计划之外临时敲的命令里，不能当作本次复现的证据"
_EV_REASON_NOT_FOUND: str = "按这个位置找不到文件"
_EV_REASON_UNREADABLE: str = "这个位置不是一个能读的文件（可能是一个目录，或者没有读取权限）"
_EV_REASON_READ_ERROR: str = "读这个文件的时候出错了，无从核对"
_EV_REASON_VALUE_NOT_IN_FILE: str = "在这个文件里没找到它写的那个数"
_EV_REASON_NO_BASELINE: str = "这次没有从论文里读到任何报告值，无从核对"
_EV_REASON_METRIC_MISSING: str = "论文分析里没有这个指标的报告值"
_EV_REASON_METRIC_AMBIGUOUS: str = "论文分析里有几个只差大小写或空格的指标名，认不准说的是哪一个"
_EV_REASON_NO_VALUE: str = "这条证据没写它对应的数值，无从核对"
_EV_REASON_VALUE_MISMATCH: str = "它写的数值和论文分析里记下的这个指标对不上"


def _evidence_text(value: Any) -> Optional[str]:
    """物证里的标量 → 用于匹配的字符串；缺失 / 空白一律归 ``None``。

    🔴 **空白必须归 None，不能当成"写了一个空值"**：论文值侧第⑦重是双向前缀匹配，
    而任何串都以空串开头 ⇒ 空 value 会**无条件通过**第⑦重，是一条不打自招的假绿
    通道。归 None 之后它走"没写数值"那条出口（不成立），语义正确。
    （产物侧第③重同理：``"" in text`` 恒真；只是那一侧"通过"本就是无数值物证的
    正路，所以那边归 None 只是把台账键写干净，不改判定。）
    """
    if value is None:
        return None
    text = value if isinstance(value, str) else str(value)
    text = text.strip()
    return text or None


def _verify_evidence(
    evidence_item: Any,
    code_output_dir: str,
    extra_commands: Optional[List[str]],
    baseline_results: Optional[Dict[str, Any]],
) -> Tuple[bool, str]:
    """七重验钞：产物侧五重 + 论文值侧两重，**该出处对应的各重全过才采信**。

    🔴 **能力边界不许对外含糊**（PRD §4.4 第 5 条 / R-S8-01）：本函数能验的是
    **物证真伪** —— 位置是不是真的、文件读不读得出来、那个数是不是真在里面、
    这份东西算不算本次跑出来的。它**验不了"这些结果够不够格叫复现成功"**，
    那一步由 agent 照计划里写的那条本篇达标线自己判。两者不得混为一谈。

    🔴 **按「出处」二选一，各走各的核验**（``path`` 与 ``metric`` 互斥且必居其一）：
      - **本次跑出来的**（带 path）→ ④落在本次代码目录之下 ⑤未在计划外命令参数里
        字面出现 ①位置真实存在 ②文件可读 ③数值能在该文件里前缀匹配查到；
      - **论文报告的**（带 metric）→ ⑥指标名能在送进来的那份论文报告值里查到
        ⑦数值与该指标的记录双向前缀匹配。
    两者都有 / 都无 ⇒ 不成立 + WARNING（**畸形不静默吞**，已知 bug 模式 #3）。

    ⚠ **这不是"按证据形态分支"，不违反 AC-S8-08②**（PRD v4.1 边界澄清）：禁的是按
    证据的**内容形态**（数值 / 趋势 / 定性）把判定岔开——那是病③的根因；这里分的是
    **出处**，出处决定的是"拿什么去核对它"，不是"这篇论文属于哪一类"，两种出处对
    **所有**论文同时存在。且它**只落在本函数里**：_decide_conclusion 的红线一字不动。

    🔴 **对不上时只是这一条不成立，本函数不降档、不封顶**（架构 §16.3.2 第 2 条）：
    ok=false 自动落进两个既有出口（引用它的逐条结论落「无法核实」/ 档位的支撑物证
    全不成立则走既有封顶 3）。**不得另写一条"论文值对不上则降档"的分支**——既有
    两个出口已完全覆盖，写第二处必然与第一处打架。

    🔴 **七重写在同一个函数体里是刻意的**：CP-2.5-7（不读本篇达标线）与 CP-2.5-12③
    （不得出现归一化模糊匹配）都是**对本函数体的静态审查**；拆成小函数会让静态断言
    的射程漏掉被拆出去的那部分，等于给自己留一条审查看不见的后门。

    ⚠ **局限如实登记，不得包装成"杜绝编造"**（架构 §16.3.2）：论文值侧挡的是
    "把 0.95 编成 0.61"这一档量级的改动，**挡不住"把 0.62 报成 0.6"**——后者仍能
    通过前缀匹配。且**论文分析自己抽错了值**的情形本函数一概发现不了。
    """
    item = evidence_item if isinstance(evidence_item, dict) else {}
    path = _evidence_text(item.get("path"))
    metric = _evidence_text(item.get("metric"))
    value = _evidence_text(item.get("value"))

    # ── 畸形：path 与 metric 互斥且必居其一（架构 §16.3.2）。**不静默吞**。
    if (path is None) == (metric is None):
        both = path is not None
        logger.warning(
            "[%s] 物证记录畸形（%s产出位置与论文指标名），该条判不成立: %s",
            NODE_NAME, "同时写了" if both else "既没写",
            mask_value(json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)),
        )
        return False, _EV_REASON_SOURCE_AMBIGUOUS if both else _EV_REASON_SOURCE_MISSING

    # ── 论文值物证：第⑥⑦重（架构 §16.3.2，AR-S8-14 的唯一挡板）
    if metric is not None:
        # 🔴 baseline_results 为空 ⇒ 一律不成立，且**这条零误伤，理由是结构性的**：
        # _build_execution_agent_context 只注入这一份论文报告值，**不注入整个论文分析、
        # 更没有论文原文**（A-S8-07）⇒ agent 手上唯一的合法论文值来源就是那份注入；
        # 注入为空时它**没有任何合法途径**知道论文报了什么 ⇒ 报出来的只可能是编的。
        # ✨ 附带收获：A-S8-07 那条"只送这一份"从"反过度工程"升级成了**一条防线** ——
        # 🔴 **日后若有人为了"让 agent 看得更全"把整份论文分析塞进上下文，会在毫无
        # 察觉的情况下把本重核验掏空。** 改注入面之前先回来读这段。
        if not isinstance(baseline_results, dict) or not baseline_results:
            return False, _EV_REASON_NO_BASELINE
        # 🔴 第⑥重 = **精确匹配，仅大小写与首尾空白不敏感，此外一字不差**。
        # **绝不做归一化模糊匹配**：reporting 的 _normalize_group_key（把非字母数字
        # 一律 re.sub 成下划线）+ _match_metrics_group 那套正是 S7-13 真跑挖出的歧义源，
        # 本 Sprint 正在删它们 —— **不能在隔壁重建一个同型物**。不做模糊匹配零成本：
        # 原键名已经整份摆在 agent 眼前（提示词写死"用原键名"）。
        wanted = metric.lower()
        hits = [k for k in baseline_results if str(k).strip().lower() == wanted]
        if len(hits) > 1:
            # 归一后多个候选同时命中 ⇒ 判歧义、不成立 + WARNING、**不做任何 tie-break**
            # （沿 _match_metrics_group 当年"命中 2 条判歧义返 None"的保守取向——
            # 那条取向本身是对的，被删的是它的模糊匹配前提，不是它的保守出口）。
            logger.warning(
                "[%s] 论文报告值里有 %d 个键归一后同名（%s），判歧义、该条不成立，不做取舍",
                NODE_NAME, len(hits), mask_value(", ".join(str(k) for k in hits)),
            )
            return False, _EV_REASON_METRIC_AMBIGUOUS
        if not hits:
            return False, _EV_REASON_METRIC_MISSING
        if value is None:
            return False, _EV_REASON_NO_VALUE
        recorded = _evidence_text(baseline_results[hits[0]])
        if recorded is None:
            return False, _EV_REASON_METRIC_MISSING
        # 🔴 第⑦重 = **双向**前缀匹配（"0.62" 与 0.6201 互相成立）。严格相等不可取：
        # 浮点字符串化（0.62 vs 0.6200000000000001）会造成大面积误伤。
        if not (value.startswith(recorded) or recorded.startswith(value)):
            return False, _EV_REASON_VALUE_MISMATCH
        return True, ""

    # ── 产物物证：第①~⑤重。判序 = 先判"这算不算本次的东西"（④⑤，纯字符串、零 IO、
    #    且是安全面），再判"东西在不在、读不读得出、数对不对"（①②③）。
    # 走到这里 metric 必为 None ⇒ 由上面的互斥判据，path 必非 None；显式取出一份
    # 非可选的局部量，让类型检查看得见这个不变量（而不是靠 assert 在运行期兜）。
    artifact_path: str = path or ""
    base_raw = (code_output_dir or "").strip()
    if not base_raw:
        logger.warning(
            "[%s] 本次代码目录为空，任何产出位置都无从判定归属，该条不成立（保守方向）",
            NODE_NAME,
        )
        return False, _EV_REASON_OUT_OF_SCOPE

    # 🔴 第④重 = **自写内联判断**，与 reporting._resolve_report_path、
    # code_fs_tools._is_within_base **同一判定路径**（resolve() 后 == base 或
    # is_relative_to(base)）。**明确否决 `from core.tools.code_fs_tools import
    # _is_within_base`**：跨模块 import 私有符号，且会造成"改工具层边界会连带改判定"
    # 的隐性耦合——恰恰是本项最要提防的事。
    # 🔴 **两个闸物理分处两文件、不可能被合成一个**（架构 §3.3，须逐字进交接文档）：
    #   工具边界管"agent 能读什么" = 整个工作区（含参考仓库），本次一字不改；
    #   证据边界管"什么能当判定物证" = 仅本次代码目录之下。
    #   ⇒ agent 读参考仓库里的结果表**不被拒绝**，但拿它当物证**一律不成立**
    #     （R-S8-03：堵的是"从官方仓库抄一个对得上的数"）。
    # ⚠ 相对路径**先锚到本次代码目录再 resolve**：agent 汇报的就是相对位置，
    #   直接 Path(相对串).resolve() 会锚到进程 cwd 上 —— 那样所有正当物证都会
    #   同时判越界 + 判不存在，验钞会退化成"什么都不认"。
    try:
        base = Path(base_raw).resolve()
        raw_path = Path(artifact_path)
        target = (raw_path if raw_path.is_absolute() else base / raw_path).resolve()
    except (OSError, ValueError) as exc:
        logger.warning("[%s] 物证位置无法解析，该条不成立: %s (%s)", NODE_NAME, artifact_path, exc)
        return False, _EV_REASON_READ_ERROR
    if not (target == base or target.is_relative_to(base)):
        return False, _EV_REASON_OUT_OF_SCOPE

    # 🔴 第⑤重 = **只查计划外命令**（PRD §4.9.5 措施 3）：计划步骤写出的文件完全不受
    # 影响 ⇒ 正常复现零误伤。口径 = **字面子串包含**（原样串出现在任一条计划外命令的
    # 任一参数里即判不成立）。数据源是对账产出的计划外命令清单。
    for cmd in extra_commands or []:
        if isinstance(cmd, str) and artifact_path in cmd:
            return False, _EV_REASON_IN_EXTRA_COMMAND

    if not target.exists():                      # 第①重
        return False, _EV_REASON_NOT_FOUND
    if not target.is_file():                     # 第②重（目录 / 非常规文件）
        return False, _EV_REASON_UNREADABLE
    try:                                         # 第②重（权限 / 其它 IO 异常）
        # 🔴 按**字节**读，不按 utf-8 解码判可读：图产物（png/pdf）解不出 utf-8，
        # 按解码判会把"图产出了、文件在且能读"这条**定性物证的正路**误判成不可读。
        with target.open("rb") as fh:
            raw_bytes = fh.read(_EVIDENCE_READ_MAX_BYTES)
    except OSError as exc:
        # 🔴 IO 异常 ⇒ 该条判**不成立**（保守方向，**不是"放行"**）+ WARNING（AR-S8-03）。
        logger.warning("[%s] 物证文件读取失败，该条不成立: %s (%s)", NODE_NAME, artifact_path, exc)
        return False, _EV_REASON_READ_ERROR

    # 🔴 第③重：value 为 None ⇒ **本重不适用，其余四重照跑**（架构 §16.3 第 3 条）。
    # 这是**定性物证的正路** —— "图产出了、文件存在且可读"本来就没有数值可查。
    # **不是漏洞**：无数值的物证支撑不了数值主张，而它能支撑的定性主张正是本 Sprint
    # 要让它支撑的。⚠ 这条必须留在注释里，否则后人要么让它崩、要么让它无条件通过。
    if value is None:
        return True, ""
    text = raw_bytes.decode("utf-8", errors="replace")
    # 🔴 **前缀匹配是单向的**（复裁 8）：0.6201 能匹配文件里的 0.62014732，反过来不行。
    # 实现 = 子串搜索 + "前面不能紧挨数字或小数点"的左边界 ⇒ 命中的必须是某个数的
    # **开头**。⚠ 光写 `value in text` 会连"数中间那一截"也认（1473 命中 0.62014732），
    # 那是**放宽**方向；这个左边界就是为堵它加的，别当成可有可无的修饰。
    if not re.search(r"(?<![0-9.])" + re.escape(value), text):
        return False, _EV_REASON_VALUE_NOT_IN_FILE
    return True, ""


def _evidence_key(evidence_item: Any) -> Tuple[str, str, str]:
    """物证去重键：``(("P", path) 或 ("B", metric), value)`` 的三元展开（架构 §16.3.2）。

    **两个命名空间分开**（``P`` = 本次跑出来的产物，``B`` = 论文报告的），防止某个
    位置串恰好等于某个指标名而被并成一条。``source_note`` **不进键** —— 同一条物证
    换个措辞不该拆成两条记录（同键**首见优先**，与 _flatten_mapping 的"重复标签首见
    优先"同款确定性取向）。畸形记录（两者都有 / 都无）落第三个命名空间 ``X`` +
    原始内容的确定性序列化：它们也各自成一条，且相同的畸形只占一条。

    🔴 收编方（goal_checks / 结果块）拿各处就地写的 ``{path, value}`` /
    ``{metric, value}`` 查同一份索引 ⇒ **三处引用必然指向同一个 id**，
    "引用漂移"在结构上不可能发生。
    """
    item = evidence_item if isinstance(evidence_item, dict) else {}
    path = _evidence_text(item.get("path"))
    metric = _evidence_text(item.get("metric"))
    value = _evidence_text(item.get("value")) or ""
    if (path is None) == (metric is None):
        return (
            "X",
            json.dumps(evidence_item, ensure_ascii=False, sort_keys=True, default=str),
            value,
        )
    return ("P", path, value) if path is not None else ("B", metric or "", value)


def _iter_reported_evidence(report: Optional[Dict[str, Any]]) -> List[Any]:
    """按**固定遍历序**取出 agent 汇报里的全部物证条目，**不排序**（架构 §16.3.1 第 2 条）。

    序 = **先逐条结论、后结果块**（架构原文），**最后**才是支撑档位本身的那一组。
    ⚠ 架构原文只点了前两处（第三处是 v2.2 给 schema 加的顶层物证字段，遍历序那句
    没跟改，已登记勘误）。**把它追加在末尾而不是插在前面**是刻意的：追加不改动
    前两处已被明文规定的相对次序，插在前面会让所有既定 id 整体位移。
    """
    if not isinstance(report, dict):
        return []
    items: List[Any] = []
    for holder_key in ("goal_checks", "result_blocks"):
        for holder in report.get(holder_key) or []:
            if isinstance(holder, dict):
                items.extend(holder.get("evidence") or [])
    items.extend(report.get("evidence") or [])
    return items


def _build_evidence_ledger(
    report: Optional[Dict[str, Any]],
    code_output_dir: str,
    extra_commands: Optional[List[str]],
    baseline_results: Optional[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], Dict[Tuple[str, str, str], str]]:
    """建证据台账：去重 → **逐条只验钞一次** → 按首次出现顺序分配 ``E1``/``E2``…

    返回 ``(台账, 去重键 → id 的索引)``；索引供收编方回填 ``evidence_ids``。

    🔴 **id 由系统生成，agent 一个 id 都不写**（架构 §16.3.1 方案 A）⇒ 悬空 id 与
    id 撞车**在结构上不可能发生** —— 不是"被缓解"，是"不存在"（R-S8-23 不适用）。
    方案 B（agent 自造 id）被否决的理由值得留在这里：那等于引入一个 agent 自造的
    命名空间，而"自造命名空间 + 撞名怎么办"正是本 Sprint 要拆掉的那套东西的同构物。

    脱敏出口（架构 §9.3）：落台账的位置串 / 指标名 / 数值 / 来源自述一律过 mask_value
    —— 它们全是 agent 自由书写、且会随报告展示出去的文本。⚠ **验钞用的是脱敏前的
    原样串**（脱敏后的串拿去读盘必然读不到），台账里存的是脱敏后的串。
    """
    ledger: List[Dict[str, Any]] = []
    index: Dict[Tuple[str, str, str], str] = {}
    for item in _iter_reported_evidence(report):
        key = _evidence_key(item)
        if key in index:
            continue  # 同键首见优先：source_note 不进键，重复引用不重复验钞
        ok, reason = _verify_evidence(item, code_output_dir, extra_commands, baseline_results)
        eid = f"E{len(ledger) + 1}"
        index[key] = eid
        record: Dict[str, Any] = {"id": eid}
        if key[0] == "P":
            record["path"] = mask_value(key[1]) or ""
        elif key[0] == "B":
            record["metric"] = mask_value(key[1]) or ""
        record["value"] = (mask_value(key[2]) or "") if key[2] else None
        note = _evidence_text(item.get("source_note")) if isinstance(item, dict) else None
        record["source_note"] = (mask_value(note) or "") if note else ""
        record["ok"] = ok
        record["reason"] = reason
        ledger.append(record)
    return ledger, index


# ---------------------------------------------------------------------------
# 步骤 5：ExecutionResult 构造 + B 档 success 判定（架构 §2.3.5，Q-S3-01）
# ---------------------------------------------------------------------------


def _aggregate_logs(
    prep: Optional[SandboxPrepareResult],
    run_results: List[SandboxRunResult],
) -> str:
    """聚合 install_log + 各步骤 stdout/stderr（受 sandbox output_truncated 护栏约束）。

    脱敏注记（sp4 §9.4 / L-D1-01）：本函数返回**原文**；回 state 前由
    ``_build_execution_result`` 统一 ``mask_value``（消费侧兜底——prepare 层
    install_log 与收集器 stdout/stderr 均为未脱敏原文）。
    """
    parts: List[str] = []
    if prep is not None and prep.install_log:
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


# ---------------------------------------------------------------------------
# 步骤 5.5：完整日志落盘（S7-02，架构 §5.3）——错误优先编排 + try/except 兜底
# ---------------------------------------------------------------------------

# 落盘子目录名（<code_output_dir>/exec_logs/，进 workspace 天然被 read_code_file 读到）。
_EXEC_LOGS_SUBDIR: str = "exec_logs"


def _build_error_first_log(
    prep: Optional[SandboxPrepareResult],
    run_results: List[SandboxRunResult],
) -> str:
    """错误优先编排（架构 §5.3 内容编排，应对 read_code_file 8000 截断 R-S7-3）。

    文件头部先写"错误摘要区"：非零 exit（或 timed_out）步骤的
    ``[step#i exit=N cmd=...]`` 头 + 其 stderr 段前置；随后完整时序日志
    （_aggregate_logs 未截断原文）。保证真报错行（stderr / ``No module named`` 类）
    落在文件头 8000 字符内，coder 用 read_code_file 整读一次即命中。
    """
    error_parts: List[str] = []
    for i, r in enumerate(run_results):
        if r.exit_code == 0 and not getattr(r, "timed_out", False):
            continue
        cmd = " ".join(r.command) if isinstance(r.command, (list, tuple)) else str(r.command)
        head = f"[step#{i} exit={r.exit_code} timed_out={r.timed_out} cmd={cmd}]"
        seg = [head]
        if r.stderr:
            seg.append(f"[stderr]\n{r.stderr}")
        error_parts.append("\n".join(seg))
    # prep 安装失败也是首要错误证据，前置。
    if prep is not None and not prep.success:
        failed = getattr(prep, "install_failed_packages", None) or []
        err = getattr(prep, "error", None)
        seg = ["[prepare_environment FAILED]"]
        if failed:
            seg.append(f"install_failed_packages={list(failed)}")
        if err:
            seg.append(f"error={err}")
        error_parts.insert(0, "\n".join(seg))

    full = _aggregate_logs(prep, run_results)
    if not error_parts:
        return full
    return (
        "===== 错误摘要区（error-first，真报错前置）=====\n"
        + "\n\n".join(error_parts)
        + "\n\n===== 完整时序日志 =====\n"
        + full
    )


def _persist_round_log(
    work_dir: str,
    fix_count: int,
    prep: Optional[SandboxPrepareResult],
    run_results: List[SandboxRunResult],
) -> Optional[str]:
    """把本回合完整日志落盘到 ``<work_dir>/exec_logs/round_{fix_count}.log``（S7-02，架构 §5.3）。

    - 位置：``<code_output_dir>/exec_logs/``（code_output_dir 在 WORKSPACE_DIR 之下，
      ``read_code_file`` 天然可读，无需工具微调）；
    - 命名：``round_{fix_loop_count}.log``（确定性编号，首跑=0，第 N 次修复回合=N；
      不用时间戳/uuid，Prompt Cache 无扰、coder 可从 fix_round 反推）；
    - 内容：错误优先编排后的**完整日志原文**（未截断），用 **mask 后**口径
      （与 execution_result.logs 同脱敏级别，coder 读到的日志不泄凭证）；
    - 落盘异常兜底（R-S7-4）：写文件失败（IO/越界）**不阻断节点**，try/except 吞异常
      返回 None（沿 coding gate 工具兜底范式），coder read 到"文件不存在"退回 errors 摘要。

    返回落盘文件绝对路径；失败返回 None。
    """
    import os as _os

    try:
        log_dir = _os.path.join(str(work_dir), _EXEC_LOGS_SUBDIR)
        _os.makedirs(log_dir, exist_ok=True)
        log_path = _os.path.join(log_dir, f"round_{int(fix_count)}.log")
        content = mask_value(_build_error_first_log(prep, run_results)) or ""
        with open(log_path, "w", encoding="utf-8") as fh:
            fh.write(content)
        logger.info(
            "[%s] 本回合完整日志已落盘: %s (%d chars)", NODE_NAME, log_path, len(content)
        )
        return _os.path.abspath(log_path)
    except Exception as exc:  # noqa: BLE001 — 落盘失败不阻断节点（R-S7-4 兜底）
        logger.warning(
            "[%s] 日志落盘失败（不阻断，coder 反馈退回 errors 摘要）: %s: %s",
            NODE_NAME, type(exc).__name__, exc,
        )
        return None


def _build_execution_result(
    prep: Optional[SandboxPrepareResult],
    run_results: List[SandboxRunResult],
    feedback: ExecutionFeedback,
    metrics: Dict[str, Any],
    work_dir: str,
    step_reconciliation: Optional[Dict[str, Any]] = None,
    degraded_credentials: Optional[List[str]] = None,
    budget_truncated: bool = False,
    metrics_groups: Optional[Dict[str, Dict[str, Any]]] = None,
) -> ExecutionResult:
    """构造 ExecutionResult，B 档 success = (exit 全 0) and len(metrics) >= 1。

    - B 档判定只认真实 exit_code（收集器/回读），agent 自述无从进入（R-S4-01）；
    - prep=None（sp4 E3）：有运行结果视为中性（复用既有 venv），无运行结果视为失败；
    - logs 回 state 前统一 ``mask_value``（sp4 §9.4 落点：install_log + stdout/stderr
      原文在此收口脱敏，AC-S4-11）；
    - L-E4-01：exit_ok 对 effective runs（同命令最后一次）求 all-0；
      logs 聚合与 runtime_seconds 仍用全量 run_results（失败证据/真实耗时不丢）；
    - sp5 T-S5-2-4/2-5/2-6（幂等纪律③）：step_reconciliation / degraded_credentials /
      budget_truncated / metrics_groups 由调用方在本函数**之前**算好传入，随
      exec_result 一次 commit——guard 命中路径复用已落盘结果即含新字段，零重算；
      guard / _map_execution_result 均不二次写入。budget_truncated 判据单点在
      _run_execution_agent（Q-S5-7）、metrics_groups 产出单点在
      _collect_grouped_metrics（步骤 4.5），本函数只透传落盘 + 默认值兜底。
    """
    prep_ok = bool(prep.success) if prep is not None else bool(run_results)
    exit_ok = prep_ok and all(r.exit_code == 0 for r in _effective_runs(run_results))
    # S7-11（T-S7-7-6，修法 C）：第三个合取项——计划里的步骤必须跑完。
    # 在此之前只问"做过的有没有做错"，不问"该做的做完没有"，构成反向激励：
    # 立项那次真跑 round_0 跑了 17 条命令 5 条失败 ⇒ 判失败；round_1 只跑 2 条全 0
    # ⇒ 判**成功**（指标还是汇总上一轮的残留产物）。做 2 件事比做 17 件事更容易成功。
    # ⚠ 必须调 _completion_insufficient 这个单点谓词，禁止在此另写比较（红线）。
    success = bool(
        exit_ok
        and len(metrics) >= 1
        and not _completion_insufficient(step_reconciliation)
    )

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
        # summary 可能内嵌 stderr 原文（同 §9.4 payload 落点），入 state 前 mask。
        errors = [
            mask_value(f"[error_category={feedback.category.value}] {feedback.summary}")
            or ""
        ]

    return ExecutionResult(
        success=success,
        metrics=metrics,
        logs=mask_value(_aggregate_logs(prep, run_results)) or "",
        errors=errors,
        artifacts=artifacts,
        runtime_seconds=float(sum(r.duration_seconds for r in run_results)),
        environment_info=dict(prep.env_info or {}) if prep is not None else {},
        step_reconciliation=dict(step_reconciliation or {}),
        degraded_credentials=list(degraded_credentials or []),
        budget_truncated=bool(budget_truncated),
        metrics_groups=dict(metrics_groups or {}),
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
    react_rounds_used: int = 0,
) -> dict:
    """把 ExecutionResult 映射为 GlobalState 局部更新（must-fix-1 单点 read-modify-write）。

    - execution_result / current_step；
    - 失败时把细分类写进 NodeError.error_message 的 [error_category=...] 前缀（error_type 严格三态）；
    - node_errors / degraded_nodes 走 read-modify-write；
    - **落点 B 唯一扣减点**（sp4 §4.3/§4.4，AC-S4-04）：
      ``retry_budget_remaining -= (react_rounds_used + llm_calls_used)``、
      ``_dev_loop_llm_calls += 同额``，单点 read-modify-write + INFO 日志。
      react_rounds_used = 子图实际 rounds（E2 契约 llm_calls==rounds_used）；
      llm_calls_used = metrics 档 3 LLM 抽取次数（must-fix-2 保留）。
      guard 命中路径不经过本函数的扣减（rounds=0 / 直接构造 updates）→ 不重扣。
    """
    node_errors = list(state.get("node_errors", []))  # read-modify-write（must-fix-1）
    degraded_nodes = list(state.get("degraded_nodes", []))

    updates: Dict[str, Any] = {
        "execution_result": exec_result,
        "current_step": NODE_NAME,
    }

    if not exec_result["success"]:
        three_state = _map_category_to_error_type(feedback.category)
        # BUG-S4-G2-01：summary/stderr 可能内嵌敏感值原文，入 node_errors 前 mask
        # （消费侧兜底，同 §9.4 payload/logs 落点范式）。
        node_errors.append(
            make_node_error(
                NODE_NAME,
                three_state,
                mask_value(f"[error_category={feedback.category.value}] {feedback.summary}")
                or "",
                mask_value(feedback.representative_stderr or None),
            )
        )
        logger.warning(
            "[%s] 执行失败 category=%s three_state=%s summary=%s",
            NODE_NAME,
            feedback.category.value,
            three_state,
            mask_value(feedback.summary),
        )

    updates["node_errors"] = node_errors
    updates["degraded_nodes"] = degraded_nodes

    # 落点 B 唯一扣减点（sp4 §4.4）：子图 rounds + metrics 档 3 LLM 抽取合并单点扣减。
    total_calls = max(0, int(react_rounds_used or 0)) + max(0, int(llm_calls_used or 0))
    if total_calls > 0:
        prev_budget = state.get("retry_budget_remaining", 0) or 0
        updates["retry_budget_remaining"] = max(0, prev_budget - total_calls)
        prev_calls = state.get("_dev_loop_llm_calls", 0) or 0
        updates["_dev_loop_llm_calls"] = prev_calls + total_calls
        logger.info(
            "[%s] LLM 预算单点扣减: react_rounds=%d + metric_llm_calls=%d = %d，"
            "retry_budget %d->%d, _dev_loop_llm_calls %d->%d",
            NODE_NAME,
            react_rounds_used,
            llm_calls_used,
            total_calls,
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
    """单点 read-modify-write 追加 FixLoopRecord（must-fix-1，严禁 reducer）。

    S7-05（修复循环记忆增强，档 B，架构 v1.1 §13.7）：追加 2 字段取值——
        - fix_note = state["last_fix_note"]：coder 本轮自述"定位+修复逻辑"。时序自洽
          （§13.7 / R-S7-10）：coding 先跑（_map_coding_result 写 last_fix_note）→
          execution 后跑（本函数取），此时 state["last_fix_note"] 恰是本轮对应 coder 输出
          （第 N 轮 record 记录 coder 第 N 轮改了什么 + execution 第 N 轮跑出什么真错）。
        - files_touched = state["last_files_written"]：coder 本轮改的文件列表（同链路）。
    ``.get`` 兜底旧 checkpoint（task-99eef17bccf2 现场无这 2 键 → ""/[]，R-S7-8，不 KeyError）。
    既有 round_number/error_summary/error_category/fix_strategy/timestamp 全不变；单点
    read-modify-write（严禁 reducer）不变。
    """
    history = list(state.get("fix_loop_history", []))  # 读出整列表
    history.append(
        FixLoopRecord(
            round_number=round_no,
            error_summary=mask_value(feedback.summary) or "",
            error_category=feedback.category.value,
            fix_strategy=feedback.fix_hint,
            timestamp=datetime.now(timezone.utc).isoformat(),
            # S7-05：coding→execution 传递字段取值（时序天然对齐，见上）。
            fix_note=state.get("last_fix_note", "") or "",
            files_touched=list(state.get("last_files_written", []) or []),
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
    """interrupt#2 payload（含 interrupt_kind="dev_loop_failure"，与 interrupt#1 区分，§2.5.4）。

    sp4 §9.4（AC-S4-11）：payload 键结构逐字保持 sp3 不动（AC-S4-05 命门），
    仅对日志派生的**值**（error_summary / execution_errors / representative_stderr）
    过 ``mask_value``——这些字段可能内嵌 stderr 原文（如 token URL）。
    """
    return {
        "interrupt_kind": INTERRUPT_KIND,
        "fix_loop_count": state.get("fix_loop_count", 0) or 0,
        "error_category": feedback.category.value,
        "error_summary": mask_value(feedback.summary) or "",
        "fix_hint": feedback.fix_hint,
        "auto_fixable": feedback.auto_fixable,
        "fix_loop_history": list(state.get("fix_loop_history", [])),
        "execution_errors": [
            mask_value(e if isinstance(e, str) else str(e)) or ""
            for e in (exec_result.get("errors") or [])
        ],
        "representative_stderr": mask_value(feedback.representative_stderr) or "",
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
        # sp7 S7-01（Q-S7-1 方案 A，架构 §1.2）：revise_plan = "换计划 = 重新开始"——补齐
        # 预算语义自洽，防预算耗尽下 revise_plan 空转（新一轮 execution 入口又立刻耗尽）。
        # 全额重置为 MAX_TOTAL_LLM_CALLS（240，与 state.py:340 初始化同口径）。硬顶不破：
        # _dev_loop_llm_calls 累计**不重置**（子上限 MAX_DEV_LOOP_LLM_CALLS 硬顶继续生效于
        # :2036/:2077，叠加 S7-03 收窄），故 revise 后即便预算重满仍不突破 240/120（R-S7-2）。
        out["retry_budget_remaining"] = MAX_TOTAL_LLM_CALLS
        logger.info(
            "[%s] interrupt#2 resume: revise_plan（fix_loop_count 清零，history 保留，"
            "retry_budget_remaining 全额重置=%d，_dev_loop_llm_calls 不重置）",
            NODE_NAME, MAX_TOTAL_LLM_CALLS,
        )
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


# 早停终态文案：N+1 轮（尾部 N 条历史 + 当前 1 次），面板 error_summary 与 :2070 logger 共用。
_NO_METRICS_EARLY_STOP_SUMMARY = (
    f"已连续 {NO_METRICS_EARLY_STOP_ROUNDS + 1} 轮零指标，"
    "自动修复无进展，请检查执行步骤或更换论文"
)

# sp7 S7-01（架构 §4.3）：预算耗尽终态面板文案，走既有 summary/fix_hint 通道经 replace 注入
# （复用 sp6 AC-S6-10 范式，零新 payload 键）。与 _NO_METRICS_EARLY_STOP_SUMMARY 同款。
_BUDGET_EXHAUSTED_SUMMARY = (
    "修复循环已反复失败，重试预算已耗尽（LLM 调用额度用尽）。"
    "系统不再自动继续，请在下方三种处置中选择：接受当前结果导出报告 / "
    "重订计划再试 / 终止任务。"
)


def _no_metrics_stalled(state: GlobalState, feedback: "ExecutionFeedback") -> bool:
    """早停判定：本轮 NO_METRICS 且历史尾部已连续 NO_METRICS_EARLY_STOP_ROUNDS 轮。

    "无进展"口径 = 类别连续复现：fix_loop_history 尾部已有 >= N 条
    error_category == "no_metrics" 的历史记录。
    """
    if feedback.category != ErrorCategory.NO_METRICS:
        return False
    history = state.get("fix_loop_history") or []
    if not isinstance(history, list) or len(history) < NO_METRICS_EARLY_STOP_ROUNDS:
        return False
    tail = history[-NO_METRICS_EARLY_STOP_ROUNDS:]
    return all(
        isinstance(r, dict) and r.get("error_category") == ErrorCategory.NO_METRICS.value
        for r in tail
    )


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

    # sp7 S7-01（架构 §2.3 实现 1）：**删除**入口预算门的独立降级 return——预算门不再是
    # "提前降级的旁路"（旧 :2029-2030 `if budget < MIN: return _mark_degraded_for_report`
    # 造成静默降级、_dev_loop_route 被清 None、graph.py 兜底路由 reporting，用户无知情选择）。
    # 改为**下沉为修复分支的一个准入否决条件**（下方 and budget >= DEV_LOOP_MIN_CALLS_PER_ROUND）：
    # 预算不足一回合时不回 coding 修复，自动落既有两段式 interrupt#2（:2055 await / :2091 interrupt），
    # 复用 commit-边界-return + self-loop-重入，`already_committed` guard 一字不改、零新路径、
    # 零新 guard（架构 §2.2 坐实：预算门命中时 exec_result 已在 updates、sandbox 不重跑）。
    #
    # 可修复 + 未超限 + 预算够一回合 + 子预算未触顶 + 无 NO_METRICS 早停 → 回 coding 修复。
    if (
        feedback.auto_fixable
        and fix_count < MAX_FIX_LOOP_COUNT
        and dev_calls < MAX_DEV_LOOP_LLM_CALLS
        and budget >= DEV_LOOP_MIN_CALLS_PER_ROUND  # sp7 S7-01：预算门下沉为修复准入否决条件
        and not _no_metrics_stalled(state, feedback)
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
    # 早停终态：覆盖 feedback.summary/fix_hint（面板 error_summary 渲染此值），使决策面板承载
    # 早停轮次上下文文案而非普通 NO_METRICS 通用文案（架构 §3.4，走既有 summary 通道，payload
    # 键结构不变）。reason 与 payload 文案共用同一常量，避免日志/面板轮次口径不一致。
    panel_feedback = feedback
    if _no_metrics_stalled(state, feedback):
        reason = _NO_METRICS_EARLY_STOP_SUMMARY
        panel_feedback = replace(
            feedback,
            summary=_NO_METRICS_EARLY_STOP_SUMMARY,
            fix_hint=_NO_METRICS_EARLY_STOP_SUMMARY,
        )
    elif budget < DEV_LOOP_MIN_CALLS_PER_ROUND:
        # sp7 S7-01（架构 §2.4）：预算耗尽终态——优先级高于子上限/不可修复/修复耗尽
        # （预算耗尽是更强的资源终态），低于早停（早停是更具体的"无进展"语境）。
        # 面板文案走既有 summary/fix_hint 通道经 replace 注入（复用 sp6 AC-S6-10 范式，
        # 零新 payload 键；_build_dev_loop_interrupt_payload 从 feedback.summary/fix_hint 取）。
        reason = _BUDGET_EXHAUSTED_SUMMARY
        panel_feedback = replace(
            feedback,
            summary=_BUDGET_EXHAUSTED_SUMMARY,
            fix_hint=_BUDGET_EXHAUSTED_SUMMARY,
        )
    elif dev_calls >= MAX_DEV_LOOP_LLM_CALLS:
        reason = "子预算触顶"
    elif not feedback.auto_fixable:
        reason = "不可修复"
    else:
        reason = "修复耗尽"
    logger.warning(
        "[%s] 触发 interrupt#2（%s）: fix_loop_count=%d dev_calls=%d category=%s",
        NODE_NAME,
        reason,
        fix_count,
        dev_calls,
        feedback.category.value,
    )
    decision = interrupt(
        _build_dev_loop_interrupt_payload(exec_result, panel_feedback, state)
    )
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

    七步骨架（架构 §2.3.1 + sp4 §3.4）：_run_execution_agent（内嵌 ReAct 子图，步骤 1+2）→
    _classify_execution → ~~_parse_metrics~~（**T-S8-2-1 已删除，<METRICS> 通道退场**）→
    _split_reported_metrics（步骤 4.4，metrics 的唯一来源）→ _build_execution_result →
    _map_execution_result → _maybe_interrupt_or_return。
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
            # sp5 T-S5-2-6：降级路径构造点同步补齐 4 新键默认值（架构 §8 R-6，
            # 未进 sandbox——对账/截断/分组/降级快照均为空默认）。
            step_reconciliation={}, budget_truncated=False,
            metrics_groups={}, degraded_credentials=[],
        )
        updates = _map_execution_result(exec_result, feedback, state)
        return _mark_degraded_for_report(updates, state, reason="missing_code_output_dir")

    plan = state.get("reproduction_plan") or {}

    # 步骤 1+2（sp4 S4-03，架构 §3.4）：内嵌 ReAct 子图自主编排 prepare_environment /
    # run_in_sandbox / request_user_input。interrupt#3（GraphBubbleUp）直通上浮暂停主图，
    # resume 时本函数体整体重跑、子图从 checkpoint 恢复（工具历史不重放，B2 门禁）。
    # 收尾只认工具执行的真实 sandbox 结果（收集器 + messages 回读），不认 agent 自述（R-S4-01）。
    agent_out = _run_execution_agent(state, work_dir, plan)
    prep = agent_out.prep  # 可能为 None（agent 未调 prepare / 子图降级）→ 下游 Optional 分支
    run_results = agent_out.run_results

    # 步骤 3：错误分类。
    feedback = _classify_execution(prep, run_results)

    # 步骤 4：〔T-S8-2-1，2026-08-10〕**<METRICS> 三档解析已整体删除**（模块顶部有完整告示）。
    # metrics 从这里起是空字典，唯一来源是下面步骤 4.4 的 agent 自报；而步骤 4.4 的自律门控
    # 在主通道零指标时**不采信**自报 ⇒ **在 T-S8-2-8 换上新判据之前 metrics 恒空、success 恒假**。
    # 🔴 这是计划内的不可用中间态（AR-S8-01），不是回归。
    metrics: Dict[str, Any] = {}

    # 步骤 4.4（sp7 S7-13，T-S7-9-1）：agent 自报指标拆分。
    reported_main, reported_groups = _split_reported_metrics(agent_out.reported_metrics)
    if metrics and reported_main:
        # 主实验指标合并，**真实 stdout 解析值优先**（同名键 agent 自报不得覆盖），
        # agent 自报只填补主通道没解析到的键——真跑现场档 1 只取到收尾脚本的
        # mean_timing_seconds，科学指标 best_knn_accuracy 就是这样被补回来的。
        metrics = {**reported_main, **metrics}
    elif reported_main:
        # ★ 门控（本批最要紧的一条自律）：三档主通道零指标时**不采信**自报。
        # 否则 `len(metrics) >= 1` 这个成功合取项的分子就变成了 agent 自报——
        # 代码一个字没改，语义却被悄悄换掉，正是 S7-11 立项时那类反向激励。
        logger.warning(
            "[%s] 主通道零指标，agent 自报的 %d 个主实验指标不采信"
            "（成功判定的指标分子只认真实 stdout 解析）",
            NODE_NAME, len(reported_main),
        )

    # 步骤 4.5（sp5 S5-10，T-S5-2-6）：多组指标收编。
    # S7-13：**agent 汇报优先，磁盘扫描降为兜底**（agent 一组都没报时才扫盘）。
    # 刻意**不合并**两个来源——实测合并会把回验打坏：磁盘组名 "umap" 与 agent 组名
    # "UMAP" 归一后同为 "umap"，`reporting._match_metrics_group` 精确匹配命中 2 条
    # 判歧义返 None，本来能匹配上的组反而匹配不上（一条"符合"退回"未验证"）。
    # 幂等纪律③同 4.6：在 _build_execution_result 之前算好、随 exec_result 一次
    # commit，guard 命中路径复用已落盘结果零重算。
    metrics_groups = reported_groups or _collect_grouped_metrics(work_dir)

    # 步骤 4.6（sp5 S5-06，T-S5-2-4/2-5）：确定性步骤对账 + 降级凭证快照 + 截断标记。
    # 幂等纪律③（架构 §9.2）：必须在 _build_execution_result 之前完成、随 exec_result
    # 一次 commit；guard 命中路径（复用已落盘 execution_result）即含新字段，零重算。
    # ⚠ S7-11（T-S7-7-6）：**调用位置前移**（原在 4.75 之后）——步骤 4.7 的完整度改判
    # 需要它的产出。_reconcile_steps 函数体一行未改，两条既有契约（在
    # _build_execution_result 之前完成、只调一次）均保持。
    step_reconciliation = _reconcile_steps(
        plan.get("execution_steps") or [], run_results, agent_out.step_ledger,
    )

    # 步骤 4.65（S7-11，T-S7-7-5）：自报归属防伪留痕——完成度采信 agent 自报的
    # step_index，此处只把"自报与实际执行明显不符"打成 WARNING 留痕，**不阻断、
    # 不返回任何值**（R-S7-65 的唯一对冲手段）。
    _audit_declared_steps(
        plan.get("execution_steps") or [], run_results, agent_out.step_ledger,
    )

    # 步骤 4.7（S7-11，T-S7-7-6）+ 4.75（S6-B2，T-S6-2-4）：两级改判合流。
    # 顺序即优先级（Q-S7-30）：INCOMPLETE 排在 NO_METRICS 之前 ⇒ "没跑完且没指标"
    # 报的是"步骤没跑完"（真因）而不是"没产出指标"（果）；_apply_no_metrics 的
    # category==NONE 前置守卫使它在改判后自动原样返回，**函数体一行不改**。
    # 两者共用同一处 exit_ok 单点计算，与分类器同套 _effective_runs 口径。
    _prep_ok = bool(agent_out.prep.success) if agent_out.prep is not None else bool(run_results)
    _exit_ok = _prep_ok and all(r.exit_code == 0 for r in _effective_runs(run_results))
    feedback = _apply_incomplete_execution(feedback, step_reconciliation, _exit_ok)
    feedback = _apply_no_metrics(feedback, metrics, metrics_groups, _exit_ok)
    # AC-S5-03 第②落点：同点快照 coding gate 的降级凭证 purpose_key（.get() 防御读，
    # 兼容旧 checkpoint 无该键；只存 purpose_key，天然无敏感值，架构 §9.3）。
    degraded_credentials = sorted((state.get("credential_degradations") or {}).keys())

    # 步骤 5：构造 ExecutionResult + B 档 success（budget_truncated 判据与 INFO 日志
    # 单点在 _run_execution_agent，此处只随 exec_result 透传落盘，AC-S5-12）。
    exec_result = _build_execution_result(
        prep, run_results, feedback, metrics, work_dir,
        step_reconciliation=step_reconciliation,
        degraded_credentials=degraded_credentials,
        budget_truncated=agent_out.budget_truncated,
        metrics_groups=metrics_groups,
    )

    # 步骤 5.5（sp7 S7-02，架构 §5.3）：本回合完整日志落盘到
    # <code_output_dir>/exec_logs/round_{fix_loop_count}.log（错误优先编排，coder 可
    # 用 read_code_file 自读定位真报错行）。只在真跑回合落盘——guard 命中路径本就不
    # 重跑 sandbox、不经过此处（日志已在上一次真跑回合落盘），无需二次落盘。落盘失败
    # try/except 兜底不阻断节点（R-S7-4）；路径由 coding 侧确定性推导（不存 state/exec_result）。
    _persist_round_log(
        work_dir, state.get("fix_loop_count", 0) or 0, prep, run_results
    )

    # 步骤 6：单点 read-modify-write 写 state（落点 B 唯一扣减点：**现在只剩子图 rounds**，§4.4；
    # 降级路径 rounds_used=0 → 零扣减，与 guard 命中同口径）。
    # 〔T-S8-2-1，2026-08-10〕**不再传 llm_calls_used** —— 该支路的唯一来源 _llm_extract_metrics
    # 已随 <METRICS> 通道删除。两种写法（不传 / 显式传 0）等价，**选"不传"**：形参 `llm_calls_used:
    # int = 0` 的默认值本身就是这条支路归零后的正确取值，显式传 0 反而会让人以为"这里还有个会
    # 变的量"。🔴 _map_execution_result 的**签名一字未动**（形参保留、默认值保留），预算扣减公式
    # `total_calls = react_rounds_used + llm_calls_used` 也**一字未动**（那是另一件事，本 Sprint 非目标）
    # ⇒ 效果 = total_calls 恒等于 react_rounds_used。
    updates = _map_execution_result(
        exec_result, feedback, state,
        react_rounds_used=agent_out.rounds_used,
    )

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
