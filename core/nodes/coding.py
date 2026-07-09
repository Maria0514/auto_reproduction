"""coding 节点（S3-02）：以 ReAct agent 形式生成 / 修复复现代码。

把 ``graph.py`` 中的 ``coding`` 占位替换为真实 ReAct 编码节点。**复用
``_make_react_wrapper``**（与 paper_intake/paper_analysis/resource_scout/planning
内部 ReAct 完全同构），自动获得节点级 LLM 路由 + 预算扣减。

设计要点（架构 §2.2）：
    - ``_build_coding_context``：curated 上下文（HumanMessage 通道，sort_keys 字节
      幂等）。首轮与修复回合共用，靠 ``fix_loop_count`` + ``execution_result`` 区分。
    - ``_build_coding_system_prompt``：Prompt Cache 方案 A——主体常量
      ``_CODING_SYSTEM_PROMPT_BODY`` 内绝不出现任何论文级动态变量，动态上下文放尾部
      独立段落用 json.dumps(sort_keys=True, ensure_ascii=False) 渲染。
    - ``_get_coding_tools``：B2 的 write/read/list 工具 + deepxiv read_section（回读
      论文核对实现）+ web_search（查依赖/API）+ sp4 新增 run_command（轻量验证
      smoke，S4-01）与 request_user_input（interrupt#3 就地问用户，S4-02），
      共 7 工具（AC-S4-01）。
    - ``_map_coding_result``：3 参签名（含 react_messages），写 code_output_dir +
      current_step；ReAct 失败时走单点 read-modify-write 标记 degraded（must-fix-1）；
      **不写 fix_loop_count**（自增点在 execution 出口，must-fix-2），retry_budget_remaining
      由 wrapper 自动 setdefault 回写（不在此覆盖）。

Sprint 5（S5-01，架构 sp5 §5/§6/§7.1）：节点从裸 wrapper 升级为"手写凭证前置门 +
既有 ReAct wrapper"的复合函数（planning 手写复合同范式，graph.py 节点名/节点数/
边结构零改动）：
    - ``_credential_gate``：确定性代码逐项比对 ``plan.required_credentials``——
      `.secrets` ∪ 会话覆盖层命中即静默通过；缺失项经 interrupt#3 增量五键 payload
      （四键 + ``allow_degrade=True``，该键只由 gate 设置，agent 工具路径永不含）
      向用户索要或由用户显式降级（``credential_degradations`` 整 dict 单点回写）；
    - 幂等命门（架构 sp5 §9.2 纪律①）：missing 按执行开始时快照单项串行 interrupt，
      **副作用（remember/stash）整体后置到快照收齐之后**，防 LangGraph 按调用序
      重放 resume 值串位；gate 的 ``interrupt()`` 严禁 try/except 兜底（纪律②，
      BUG-S4-B1-01 同款 GraphBubbleUp 红线）。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from langgraph.types import interrupt

from config import REACT_MAX_ROUNDS_CODING, WORKSPACE_DIR
from core.errors import make_node_error
from core.react_base import _make_react_wrapper
from core.state import GlobalState
from core.secrets_store import (
    build_credential_env,
    load_all_secrets,
    lookup_secret,
    register_sensitive_value,
    remember_secret,
    stash_session_secret,
)
from core.tools.code_fs_tools import (
    make_list_dir_tool,
    make_read_code_file_tool,
    make_write_code_file_tool,
)
from core.tools.deepxiv_tools import read_section_tool, web_search_tool
from core.tools.interaction_tools import INTERRUPT_KIND_USER_INPUT, request_user_input
from core.tools.run_command_tool import make_run_command_tool

logger = logging.getLogger(__name__)


NODE_NAME: str = "coding"


# 上一轮 execution stderr 注入修复回合的尾部裁剪上限（架构 §2.2.2，防撑爆 context）。
_STDERR_TAIL_CHARS: int = 2000


CODING_OUTPUT_SCHEMA: Dict[str, Any] = {
    # title 字段是 langchain_openai.with_structured_output 的强制要求（函数名）。
    "title": "CodingResult",
    "description": "coding 节点输出契约：复现代码生成 / 修复结果摘要。",
    "type": "object",
    "properties": {
        "files_written": {
            "type": "array",
            "items": {"type": "string"},
            "description": "本轮写入 / 修改的代码文件绝对路径列表。",
        },
        "entry_script": {
            "type": ["string", "null"],
            "description": "复现入口脚本相对/绝对路径（运行后末尾打印 <METRICS>{...}</METRICS>）。",
        },
        "summary": {
            "type": "string",
            "description": "本轮代码生成 / 修复工作的中文摘要。",
        },
        "notes": {
            "type": ["string", "null"],
            "description": "降级 / 不确定性 / 遗留问题等元信息（可选）。",
        },
        # S5-02（P2）：simulation 声明——仅 finalize 消费，不进 Prompt Cache 前缀。
        "simulation_notice": {
            "type": ["string", "null"],
            "description": "无法真实实验时的模拟声明：中文说明哪部分是模拟、为什么（能真实实验时置 null）。",
        },
    },
    "required": ["files_written", "summary"],
    "additionalProperties": True,
}


# ---------------------------------------------------------------------------
# Prompt Cache 前缀治理（方案 A，照搬 paper_analysis 范式）
# ---------------------------------------------------------------------------
# 下面的 _CODING_SYSTEM_PROMPT_BODY 是 SystemMessage 的稳定前缀部分。
# 严禁在此字符串中插入 arxiv_id / title / paper_meta / 代码目录路径等任何论文级或
# 任务级动态变量，否则会破坏多任务间的字节级前缀一致性，导致 Prompt Cache 失效。
# 动态上下文（复现计划 / 仓库路径 / 修复反馈）一律由 _build_coding_context 放进
# HumanMessage（curated context 通道），不进 system prompt 主体；system prompt 仅
# 在尾部独立段落 "--- 当前任务上下文 ---" 渲染极少量稳定的任务级标识（无论文级变量）。
_CODING_SYSTEM_PROMPT_BODY = """你是资深机器学习复现工程师，负责把一篇论文的复现计划落地为可运行的 Python 代码。请基于 HumanMessage 中提供的复现计划、选定参考仓库与论文事实层信息，产出符合复现目标的代码文件。

可用工具：
- write_code_file(path, content): 把完整文件内容写入 code_output_dir 下（会自动创建父目录，越界路径会被拒绝）。
- read_code_file(path): 读取 code_output_dir 或选定参考仓库（selected_repo_local_path）下的已有文件，用于复用/核对/修复。
- list_dir(path): 列出 workspace 下某目录的条目，用于探查参考仓库结构或确认已写文件。
- read_section(arxiv_id, section_name): 回读论文章节，核对方法 / 超参 / 数据集等实现细节。
- web_search(query): 查依赖包、API 用法、报错排查等外部信息。
- run_command(command): 在代码目录下跑一条【轻量验证】命令（语法检查 / import 探测 / 启动探测）。
- request_user_input(question, is_sensitive, purpose_key): 缺关键信息（凭证 / 参数 / 决策 / 路径）时向用户索要一条信息。

run_command 使用边界（强约束）：
- 仅用于 smoke 级自查：python -m py_compile 验证语法、python -c "import x" 探测依赖、脚本能否启动。
- 禁止用它跑完整训练 / 评估 / 下载大数据集——那是下游执行节点的职责，且本工具超时很短（约 2 分钟）会强制终止。
- 命令用系统解释器执行（无项目 venv），项目依赖缺失导致的 ImportError 属预期，交给下游执行节点判定，不要试图在此 pip install。
- smoke 通过不等于复现成功：复现成败仅由下游执行节点的完整执行判定，不要因 run_command 退出码 0 就宣称复现完成。

request_user_input 使用纪律（强约束）：
- 仅在确实无法从已有上下文推断、且信息缺失会阻塞任务时调用；能推断 / 能用合理默认值的不要问。
- 一次只问一个信息项；多个信息项分多轮逐条问。
- 本工具会暂停任务等待用户回答：必须【单独一轮】调用——同一轮 tool_calls 中不得混入 write_code_file / run_command 等任何其他工具调用，否则这些调用会被重复执行。
- 凭证 / 密钥类信息置 is_sensitive=true，并给出稳定 purpose_key（如 "git_credential:github.com" / "hf_token"）以便复用、避免重复打扰用户。

工作策略（推荐顺序，agent 可自主调整）：
1. 先理解 HumanMessage 中的 code_strategy / execution_steps / deliverables / environment，明确要交付什么、怎么跑。
2. 若提供了 selected_repo_local_path，先用 list_dir / read_code_file 探查参考仓库，优先复用其已有实现，避免从零造轮子。
3. 需要核对论文方法细节 / 超参 / 数据集时调用 read_section；需要查依赖版本或 API 用法时调用 web_search。
4. 用 write_code_file 把代码逐文件写入 code_output_dir（绝对路径以 HumanMessage 给定的目录为准）。每个文件写完整内容，不要写片段。
5. 至少产出一个复现入口脚本（如 run.py / main.py / reproduce.py），它应能按 execution_steps 串起数据准备 → 训练/推理 → 评估，并打印关键指标。
6. 预算意识：max_rounds=12；不要重复调用同一 (tool, args)；优先把工作做扎实而非反复试探。

入口脚本指标输出约定（强约束，下游执行节点依赖）：
- 复现入口脚本运行结束时，必须在标准输出的**最后**以单独一行打印关键指标 JSON，格式严格为：
  <METRICS>{"metric_name": value, ...}</METRICS>
- 例如：<METRICS>{"accuracy": 0.873, "f1": 0.81}</METRICS>
- 指标键名尽量对齐论文 metrics 字段；无可计算指标时打印 <METRICS>{}</METRICS>，不要省略该行。
- 这是把异构输出解析难题前移到代码生成阶段的关键约定，请务必在入口脚本中实现。

修复回合模式（HumanMessage 中出现 fix_round / last_error_summary 时生效）：
- 此时表示上一轮执行失败，进入修复回合。**不要从头重新生成全部代码**。
- 先 read_code_file / list_dir 读出 code_output_dir 下的现有代码，定位 last_error_summary 指向的错误（含 error_category / stderr 尾部）。
- 仅对出错处做有针对性的最小修改，用 write_code_file 覆盖被修改的文件；保持入口脚本的 <METRICS> 输出约定不变。

输出要求：
- 完成代码写入后，必须在 <result>...</result> 标签内输出严格 JSON，字段如下：
  {
    "files_written": [str, ...],   // 本轮写入/修改的文件路径
    "entry_script": str | null,    // 复现入口脚本路径
    "summary": str,                // 本轮工作的中文摘要
    "notes": str | null            // 降级/遗留问题等（可选）
  }
- files_written 必须如实反映 write_code_file 成功写入的文件，不要捏造；至少应包含入口脚本。
- 不要在 <result> 之外再夹杂任何其它 JSON 块。
- 若彻底无法产出任何代码（所有工具均失败），输出 {"error": "<原因>"} 并仍包在 <result>...</result> 中。
"""


# S5-02（P1）诚实红线独立静态段落（架构 §7.2 / §9.1）。
# 与 _CODING_SYSTEM_PROMPT_BODY 同为 Prompt Cache 稳定前缀的一部分：跨论文 / 跨任务
# 字节级恒定，段内严禁任何动态变量（f-string 插值 / format 占位 / 论文级字面量）。
# 由 _build_coding_system_prompt 插入主体与尾部 "--- 当前任务上下文 ---" 之间。
_CODING_HONESTY_SECTION = """
诚实红线（最高优先级约束，违反任意一条即视为造假，比复现失败更严重）：
1. 禁止把 verifier / 评估器的答案、标签或期望输出以任何形式泄漏给被评估对象——出题人与答题人必须隔离，生成侧代码不得读取答案字段。
2. 禁止硬编码分数 / 实验结果 / 常量结局——指标必须由真实计算产生，评估结果必须随输入变化，不得预写"复现成功"的剧本。
3. 不得以改变实验本质的方式规避资源缺失——缺数据 / 缺凭证 / 缺算力时，不得偷换任务、伪造等价数据或缩水评估来制造"已复现"的假象。

simulation 声明义务（与红线同级）：
- 若确实无法进行真实实验（数据 / 凭证 / 算力等资源缺失），必须如实降级为模拟实现，并且必须在 <result> 中给出 simulation_notice 字段，用中文说明哪部分是模拟、为什么模拟。
- 能真实实验时 simulation_notice 置 null；严禁把模拟 / 占位结果伪装成真实实验结果。
"""


def _format_task_context() -> str:
    """渲染 system prompt 尾部稳定的任务级上下文段落（无论文级动态变量）。

    coding 节点的论文级 / 任务级动态上下文（复现计划、仓库路径、修复反馈）全部走
    HumanMessage 的 curated context 通道，因此 system prompt 尾部段落不携带任何
    动态变量，保持字节级稳定。这里仍保留一个尾部段落骨架以与 paper_analysis 范式
    结构对齐，但内容是常量。
    """
    payload: Dict[str, Any] = {"node": NODE_NAME}
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)


def _build_coding_system_prompt(context: Dict[str, Any]) -> str:
    """组装 coding 的 system prompt（Prompt Cache 方案 A）。

    - 主体 _CODING_SYSTEM_PROMPT_BODY + 诚实红线段 _CODING_HONESTY_SECTION（S5-02）
      在不同论文 / 任务间字节级一致（稳定前缀 = 主体 + 红线段）。
    - 尾部独立段落仅含常量任务标识，无任何论文级动态变量（动态上下文走 HumanMessage）。
    - 自测断言：截去尾部段落后两次返回值完全相同（CP-C1-6 / CP-1.3-2）。
    """
    tail = _format_task_context()
    return (
        _CODING_SYSTEM_PROMPT_BODY
        + _CODING_HONESTY_SECTION
        + "\n--- 当前任务上下文 ---\n"
        + tail
    )


# ---------------------------------------------------------------------------
# build_context（curated 上下文 + 修复回合反馈注入，架构 §2.2.2）
# ---------------------------------------------------------------------------


def _digest_execution_feedback(exec_result: Dict[str, Any]) -> Dict[str, Any]:
    """把上一轮 ExecutionResult 裁剪为修复用的精简反馈（架构 §2.2.2）。

    裁剪策略（防 stderr 撑爆 context）：
        - errors: 取 ExecutionResult.errors 全部（已是摘要级，每条一句话；首条带
          ``[error_category=...]`` 前缀，§2.3.2）；
        - error_category: 从 errors[0] 解析出的细分类（驱动有针对性修复）；
        - stderr_tail: logs 取尾部 ~2000 字符（错误栈通常在末尾）；
        - 不注入完整 logs / stdout（已被 sandbox 截断，仍可能很大）。
    """
    errors = list(exec_result.get("errors") or [])
    logs = exec_result.get("logs") or ""
    if not isinstance(logs, str):
        logs = str(logs)
    stderr_tail = logs[-_STDERR_TAIL_CHARS:] if len(logs) > _STDERR_TAIL_CHARS else logs

    # 从 errors[0] 解析 [error_category=xxx] 前缀（execution 节点写入约定，§2.3.2）。
    error_category: Optional[str] = None
    if errors and isinstance(errors[0], str):
        head = errors[0]
        marker = "[error_category="
        idx = head.find(marker)
        if idx != -1:
            start = idx + len(marker)
            end = head.find("]", start)
            if end != -1:
                error_category = head[start:end].strip() or None

    return {
        "errors": [e if isinstance(e, str) else str(e) for e in errors],
        "error_category": error_category,
        "stderr_tail": stderr_tail,
    }


def _build_coding_context(state: GlobalState) -> Dict[str, Any]:
    """curated 上下文（HumanMessage 通道，sort_keys 字节幂等，对齐 planning 范式）。

    首轮与修复回合共用：靠 ``execution_result`` 非空 + ``fix_loop_count > 0`` 区分。
    修复回合时注入裁剪后的上一轮执行反馈，并把 code_output_dir 传入提示"在现有代码上
    有针对性修改"模式。
    """
    payload: Dict[str, Any] = {}

    # 复现计划 + 选定参考仓库本地路径（sp2 已落地，直接复用，无需重 clone）。
    plan = state.get("reproduction_plan") or {}
    payload["code_strategy"] = plan.get("code_strategy")
    payload["execution_steps"] = plan.get("execution_steps")
    payload["deliverables"] = plan.get("deliverables")
    payload["environment"] = plan.get("environment")

    resource = state.get("resource_info") or {}
    selected = resource.get("selected_repo") or {}
    payload["selected_repo_local_path"] = selected.get("local_path")

    # 论文英文事实层字段（避免中英混杂喂代码生成，sp2 §4.7.5；用 *_en，回退非 _en）。
    analysis = state.get("paper_analysis") or {}
    payload["method_summary_en"] = (
        analysis.get("method_summary_en") or analysis.get("method_summary")
    )
    payload["datasets"] = analysis.get("datasets")
    payload["framework"] = analysis.get("framework")
    payload["hardware_requirements_en"] = (
        analysis.get("hardware_requirements_en")
        or analysis.get("hardware_requirements")
    )

    # 无条件注入（首轮 + 修复回合共用，坑1-B / 坑2 修复）：
    #   - code_output_dir：首轮也必须告知 agent 写入绝对路径（与 get_tools / map_result
    #     幂等同值），避免 agent 写到臆想路径导致 state.code_output_dir 指向空目录；
    #   - arxiv_id：read_section(arxiv_id, section_name) 工具的必需入参（坑2 修复，
    #     任务标识非论文内容，放 HumanMessage 不破坏 Prompt Cache 方案 A）。
    payload["code_output_dir"] = _resolve_code_output_dir(state)
    paper_meta = state.get("paper_meta") or {}
    payload["arxiv_id"] = (
        paper_meta.get("arxiv_id") if isinstance(paper_meta, dict) else None
    )

    # S5-01（架构 sp5 §7.1）：gate 放行后的降级事实注入——用户已显式降级的凭证
    # {purpose_key: purpose} 摘要告知 agent，触发 S5-02 simulation_notice 声明义务。
    # 非空才注入（零降级路径的 HumanMessage 字节零扰动）；走动态上下文通道并由
    # wrapper 统一 json.dumps(sort_keys=True) 渲染，同一 state 下字节幂等（R-PC4 无扰）。
    degradations = state.get("credential_degradations") or {}
    if isinstance(degradations, dict) and degradations:
        payload["credential_degradations"] = {
            str(k): str(v) for k, v in degradations.items()
        }

    # === 修复回合：只保留反馈裁剪（code_output_dir 已上移无条件注入）===
    exec_result = state.get("execution_result")
    fix_count = state.get("fix_loop_count", 0) or 0
    if exec_result and fix_count > 0:
        payload["fix_round"] = fix_count
        payload["last_error_summary"] = _digest_execution_feedback(exec_result)

    return payload


# ---------------------------------------------------------------------------
# 工具集
# ---------------------------------------------------------------------------


def _get_coding_tools(state: GlobalState) -> List[Any]:
    """coding 节点工具集（sp3 架构 §2.2.3 + sp4 架构 §2.4，共 7 工具，AC-S4-01）。

    write 工具锚定到 code_output_dir（坑1-A）：相对路径以 code_dir 为基准，越界写被
    工具直接拒。read/list 仍限定 workspace 根（需跨访问 selected_repo.local_path）。

    sp4 新增（S4-01/02）：
        - run_command：轻量验证 smoke（cwd 同锚定 code_dir；extra_env 注入已收集
          凭证——GIT_ASKPASS 脚本路径 + GIT_TERMINAL_PROMPT=0 + HF token，见
          secrets_store.build_credential_env，clone 私有仓库等 smoke 场景可用）；
        - request_user_input：缺信息就地问用户（interrupt#3，B2 门禁已过：独立
          轮次副作用恰为 1，单独一轮纪律已写入 system prompt 稳定前缀）。
    """
    code_dir = _resolve_code_output_dir(state)   # 与 build_context / map_result 幂等同值
    return [
        make_write_code_file_tool(base_dir=code_dir),
        make_read_code_file_tool(),
        make_list_dir_tool(),
        read_section_tool(),
        web_search_tool(),
        make_run_command_tool(
            base_dir=code_dir,
            extra_env=build_credential_env(load_all_secrets()),
        ),
        request_user_input,
    ]


# ---------------------------------------------------------------------------
# code_output_dir 解析（首轮新建 / 修复回合复用，幂等）
# ---------------------------------------------------------------------------


def _resolve_code_output_dir(state: GlobalState) -> str:
    """解析代码输出目录绝对路径：``workspace_dir/<thread>/code``，幂等。

    优先级：
        1. state 已有 ``code_output_dir`` → 直接复用（修复回合复跑同目录）；
        2. 否则按 ``workspace_dir/<thread>/code`` 新建。``<thread>`` 取一个对同一任务
           稳定的代理标识（paper_meta.arxiv_id；缺失时回退 "task"），保证同一任务多轮
           推进落在同一目录（map_result 无 RunnableConfig 拿不到 thread_id，用 arxiv_id
           作稳定代理，与 sandbox work_dir 的 workspace/<thread>/code 结构对齐）。

    目录在此处创建（exist_ok=True，幂等）。
    """
    existing = state.get("code_output_dir")
    if existing:
        return str(existing)

    workspace = state.get("workspace_dir") or str(WORKSPACE_DIR)
    paper_meta = state.get("paper_meta") or {}
    thread = ""
    if isinstance(paper_meta, dict):
        thread = str(paper_meta.get("arxiv_id") or "").strip()
    thread = thread or "task"

    code_dir = Path(workspace) / thread / "code"
    try:
        code_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:  # noqa: BLE001 — 目录创建失败不应炸节点，降级由 map_result 处理
        logger.warning("[%s] code_output_dir 创建失败 %s: %s", NODE_NAME, code_dir, exc)
    return str(code_dir.resolve())


# ---------------------------------------------------------------------------
# react_messages 扫描：判断本轮是否成功写过任何文件
# ---------------------------------------------------------------------------


def _has_written_any_file(react_messages: Optional[Any], code_dir: str) -> bool:
    """扫描 ReAct 子图最终 messages，判断本轮是否有成功的 write_code_file 调用。

    判定标准：存在 ``name=write_code_file`` 的 ToolMessage，其内容解析为
    ``{"success": true, "path": <在 code_dir 内>}``（code_fs_tools 写工具的成功序列化
    契约）。失败 / 越界 / 异常的 write 返回 ``{"success": false, ...}``，不计入；
    success=true 但 ``path`` 落在 code_dir 之外的也不计（坑1-C：防 agent 写到臆想路径
    后仍被误判为有产出）。

    BUG-S1-02 治理：write_code_file 用 json.dumps（合法 JSON），这里用 json.loads
    解析，不走 str(dict) repr。
    """
    if not react_messages:
        return False
    try:
        from langchain_core.messages import ToolMessage
    except Exception:  # pragma: no cover - defensive
        return False

    found_write_tool = False
    for m in react_messages:
        if not isinstance(m, ToolMessage):
            continue
        if getattr(m, "name", None) != "write_code_file":
            continue
        found_write_tool = True
        content = getattr(m, "content", "")
        if isinstance(content, list):
            content = "".join(
                c if isinstance(c, str)
                else (c.get("text") or "") if isinstance(c, dict) else ""
                for c in content
            )
        if not isinstance(content, str):
            content = str(content)
        content_strip = content.strip()
        if (not content_strip
                or content_strip.startswith("Error in ")
                or content_strip.startswith("tool ")):
            continue
        try:
            parsed = json.loads(content_strip)
        except (ValueError, TypeError):
            continue
        if isinstance(parsed, dict) and parsed.get("success") is True:
            # 落点校验（坑1-C）：只认落在 code_dir 内的成功 write。
            # 工具 success=true 返回的 path 是 str(target.resolve())，已是绝对真实路径。
            written_path = parsed.get("path")
            if not written_path:
                continue   # 无 path 字段不计（防御）
            try:
                rp = Path(str(written_path)).resolve()
                cd = Path(code_dir).resolve()
                if rp == cd or rp.is_relative_to(cd):
                    return True
            except (OSError, ValueError):
                continue
            # 写成功但落在 code_dir 外 → 不计，继续扫描。

    if found_write_tool:
        logger.warning(
            "[%s] write_code_file ToolMessage 存在但无任何成功写入记录（无法解析出 "
            "success=true）", NODE_NAME,
        )
    return False


# ---------------------------------------------------------------------------
# map_result（3 参签名，must-fix-1 / must-fix-2）
# ---------------------------------------------------------------------------


def _map_coding_result(
    result: Optional[Dict[str, Any]],
    state: GlobalState,
    react_messages: Optional[Any] = None,
) -> dict:
    """把 coding ReAct 结果映射为 GlobalState 局部更新（架构 §2.2.4）。

    写入字段：
        - code_output_dir: 代码目录绝对路径（首轮新建，修复回合复用同目录，幂等）；
        - current_step: "coding"；
        - simulation_notice: S5-02 模拟声明透传（coding 单点写，缺失回填 None）。
          该字段是 LLM 自述声明，无工具事实源，**不做工具历史回填**（BUG-S1-02/03
          规避自查：backfill 仅适用于有 ToolMessage 事实源的核心字段；simulation_notice
          缺失即 None 属诚实语义，不是丢字段）；
        - node_errors / degraded_nodes: 仅在 coding 自身 ReAct 失败时写，走单点
          read-modify-write（must-fix-1）。

    **不写 fix_loop_count**（自增点在 execution 出口路由判定，§2.5.2，避免双点写，
    must-fix-2）；``retry_budget_remaining`` 由 _make_react_wrapper 自动 setdefault
    回写，不在此覆盖（must-fix-2）。

    ``react_messages`` 由 _make_react_wrapper 通过 inspect 检测 3 参签名自动注入。
    """
    node_errors = list(state.get("node_errors", []))      # read-modify-write（must-fix-1）
    degraded_nodes = list(state.get("degraded_nodes", []))

    code_dir = _resolve_code_output_dir(state)            # workspace_dir/<thread>/code，幂等

    # S5-02：simulation_notice 透传（单点写）。非空字符串才落值，其余一律 None。
    simulation_notice: Optional[str] = None
    if isinstance(result, dict):
        raw_notice = result.get("simulation_notice")
        if isinstance(raw_notice, str) and raw_notice.strip():
            simulation_notice = raw_notice

    # ReAct 失败判定：result 空 / 无 success 写文件记录 → degraded，不死循环。
    if not result or not isinstance(result, dict) or not _has_written_any_file(
        react_messages, code_dir
    ):
        if NODE_NAME not in degraded_nodes:
            degraded_nodes.append(NODE_NAME)
        node_errors.append(
            make_node_error(
                NODE_NAME,
                "degraded",
                "coding 未产出代码文件，降级",
                None,
            )
        )
        logger.warning("[%s] 未产出代码文件，标记 degraded", NODE_NAME)

    return {
        "code_output_dir": code_dir,
        "current_step": NODE_NAME,
        "simulation_notice": simulation_notice,
        "node_errors": node_errors,
        "degraded_nodes": degraded_nodes,
        # 不写 fix_loop_count（must-fix-2）；
        # retry_budget_remaining 由 wrapper 自动 setdefault 回写（不在此覆盖，must-fix-2）。
    }


# ---------------------------------------------------------------------------
# 凭证前置门（S5-01，架构 sp5 §5 Q-S5-9 / §6 Q-S5-10 / §9.2 幂等纪律）
# ---------------------------------------------------------------------------


def _compute_missing_credentials(
    state: GlobalState,
    degradations: Dict[str, str],
) -> List[Dict[str, str]]:
    """重算缺失凭证列表（确定性纯查询，声明序稳定，同 key 去重）。

    missing = ``plan.required_credentials`` 逐项：
        - purpose_key 空 / 项非 dict → 跳过（防御，计划字段经 planning coerce 但
          旧 checkpoint / 手改计划可能带脏数据）；
        - 已在 ``degradations``（用户显式降级）→ 排除，不再拦；
        - ``lookup_secret`` 命中（`.secrets` ∪ 会话覆盖层，两级查找）→ 静默通过；
        - 其余 → 缺失，保持声明顺序返回 ``{"purpose_key", "purpose"}``。

    ``required_credentials`` 缺失（旧 checkpoint 无该键）或为 ``[]`` → 返回 ``[]``，
    gate 零开销直通（本函数一次 ``lookup_secret`` 都不会调）。
    """
    plan = state.get("reproduction_plan") or {}
    required = plan.get("required_credentials") if isinstance(plan, dict) else None
    if not isinstance(required, list) or not required:
        return []
    missing: List[Dict[str, str]] = []
    seen: set = set()
    for item in required:
        if not isinstance(item, dict):
            continue
        purpose_key = str(item.get("purpose_key") or "").strip()
        if not purpose_key or purpose_key in seen:
            continue
        seen.add(purpose_key)
        if purpose_key in degradations:
            continue
        if lookup_secret(purpose_key) is not None:
            continue
        missing.append(
            {"purpose_key": purpose_key, "purpose": str(item.get("purpose") or "")}
        )
    return missing


def _credential_gate(state: GlobalState) -> Dict[str, str]:
    """coding 开工前确定性凭证门（S5-01 主体）。返回最终降级标记整 dict。

    对每个缺失凭证发起 interrupt#3 增量五键 payload（四键契约 +
    ``allow_degrade=True``——该键**只由本 gate 设置**，agent 经 request_user_input
    产生的 payload 永远不含，agent 无降级入口红线）；resume 契约
    ``{"value", "remember", "degrade"}``（degrade 缺省 False）四分支：
        - 提交且 remember → ``remember_secret`` 落盘（0600）；
        - 提交不 remember → ``stash_session_secret``（会话覆盖层，不落盘）；
        - degrade=True → ``credential_degradations[purpose_key] = purpose``，放行；
        - 非法 resume（缺 value 且非 degrade）→ WARNING 非静默 + 重新 interrupt 同一项。

    幂等命门（架构 sp5 §9.2 纪律①，LangGraph 按 interrupt 调用序重放 resume 值）：
        - missing 在**本次执行开始时快照**，快照内逐项串行 interrupt（一次一项）；
        - **副作用整体后置**：remember/stash 在快照全部项收齐（gate 不再暂停）后才
          统一应用。若逐项立即落副作用，中途暂停后节点重跑时 lookup_secret 会命中
          已收项使 missing 缩小，重放的 resume 值将按调用序错配到后一项（串位陷阱）；
          后置副作用保证每次重跑的 interrupt 调用序列与已录 resume 序列严格对位，
          且「记住」项在 gate 完成后经 `.secrets` 命中、后续节点重跑零 interrupt；
        - degrade 只累积在本地 dict，由复合节点在返回时整 dict 单点回写 state。

    红线（架构 sp5 §9.2 纪律②，BUG-S4-B1-01 同款）：``interrupt()`` 及其所在
    函数体**严禁 try/except 兜底**——GraphInterrupt（基类 GraphBubbleUp）必须直通
    冒泡交 LangGraph 暂停主图（CP-2.2-4 以 AST 断言守门）。

    日志纪律（架构 sp5 §9.3 脱敏出口④）：只打 purpose_key，绝不打 value 明文。
    """
    existing = state.get("credential_degradations") or {}
    degradations: Dict[str, str] = dict(existing) if isinstance(existing, dict) else {}

    missing = _compute_missing_credentials(state, degradations)
    if not missing:
        return degradations

    # (purpose_key, value, remember) 收集暂存——副作用后置（见 docstring 幂等命门）。
    collected: List[Tuple[str, str, bool]] = []
    for item in missing:
        purpose_key = item["purpose_key"]
        purpose = item["purpose"]
        while True:
            logger.info(
                "[%s] credential gate: 缺失凭证，发起 interrupt: purpose_key=%s",
                NODE_NAME, purpose_key,
            )
            # 纪律②：interrupt() 周围严禁 try/except——GraphInterrupt 直通冒泡。
            resume = interrupt({
                "interrupt_kind": INTERRUPT_KIND_USER_INPUT,
                "question": (
                    f"复现计划声明需要凭证「{purpose_key}」"
                    f"（用途：{purpose or '计划未说明'}）。"
                    "请提供该凭证；若确实无法提供，可选择降级为模拟实验。"
                ),
                "is_sensitive": True,
                "purpose_key": purpose_key,
                # 增量第 5 键：只由 gate 设置（agent 工具路径永不含，红线）。
                "allow_degrade": True,
            })
            if isinstance(resume, dict) and bool(resume.get("degrade")):
                degradations[purpose_key] = purpose
                logger.info(
                    "[%s] credential gate: 用户显式降级为模拟实验: purpose_key=%s",
                    NODE_NAME, purpose_key,
                )
                break
            if isinstance(resume, dict) and resume.get("value") is not None:
                value = str(resume.get("value"))
                if value.strip():
                    collected.append((purpose_key, value, bool(resume.get("remember"))))
                    break
            # 非法 resume（非 dict / 缺 value / 空 value，且非 degrade）→ 非静默，
            # 重新 interrupt 同一项（while 下一轮；无可重放值时暂停等待用户重填）。
            logger.warning(
                "[%s] credential gate: 非法 resume（缺 value 且非 degrade），"
                "重新 interrupt 同一项: purpose_key=%s, resume_type=%s",
                NODE_NAME, purpose_key, type(resume).__name__,
            )

    # 快照内全部项已有着落（gate 本次执行不会再暂停）→ 统一应用副作用。
    for purpose_key, value, remember in collected:
        # gate 索要的一律按敏感语义登记（payload is_sensitive=True 契约），
        # 先登记再落盘：即便落盘失败，本会话 mask 覆盖也已生效（同 B1 范式）。
        register_sensitive_value(value)
        if remember:
            remember_secret(purpose_key, value, is_sensitive=True)
        else:
            stash_session_secret(purpose_key, value)
        logger.info(
            "[%s] credential gate: 凭证已收集 purpose_key=%s (remember=%s)",
            NODE_NAME, purpose_key, remember,
        )
    return degradations


# ---------------------------------------------------------------------------
# 节点注册面：手写前置门 + 既有 ReAct wrapper 复合（planning 手写复合同范式）
# ---------------------------------------------------------------------------

# 既有 ReAct wrapper（sp3/sp4 语义原样保留）：GlobalState ↔ ReActState 双向映射 +
# 子图编译 + 预算扣减。S5-01 起不再直接注册主图，由下方复合函数 coding 委托调用。
_coding_react = _make_react_wrapper(
    node_name=NODE_NAME,
    build_context=_build_coding_context,
    build_system_prompt=_build_coding_system_prompt,
    get_tools=_get_coding_tools,
    map_result=_map_coding_result,
    max_rounds=REACT_MAX_ROUNDS_CODING,
    result_schema=CODING_OUTPUT_SCHEMA,
)


def coding(state: GlobalState) -> dict:
    """coding 复合节点 = 凭证前置门（确定性代码）+ 既有 ReAct wrapper（S5-01）。

    graph.py 仍 ``from core.nodes.coding import coding`` 注册，节点名/节点数/
    边结构零改动。流程：
        1. ``_credential_gate``：缺凭证 → interrupt#3（GraphInterrupt 直通冒泡，
           本函数无任何 try/except）；命中 / 已降级 / 无声明 → 零开销直通；
        2. gate 放行后把（可能新增的）降级标记并入传给 ReAct 的 state 视图——
           ``_build_coding_context`` 据此注入 HumanMessage 降级摘要（S5-02 义务）；
        3. ReAct wrapper 原样执行（内部 request_user_input 的 interrupt 同样直通）；
        4. ``credential_degradations`` 整 dict 单点回写（写入方唯一 = gate；
           无降级事实且 state 本无该键时不写，保持既有 update 键集零扰动）。
    """
    degradations = _credential_gate(state)

    react_state: GlobalState = state
    if degradations:
        # 浅拷贝出 ReAct 视图（不就地改 state 入参）：本轮新降级项也要进上下文。
        react_state = dict(state)  # type: ignore[assignment]
        react_state["credential_degradations"] = degradations

    update = _coding_react(react_state)
    if not isinstance(update, dict):
        update = {}
    if degradations or state.get("credential_degradations"):
        update["credential_degradations"] = degradations
    return update


# 既有验收面契约（test_sprint3_d1 CP-D1-2 / test_sprint4_c2 CP-C2-1）把节点 callable
# 的 __name__/__module__ 钉死为 _make_react_wrapper 产物命名约定。复合 = gate +
# 同一 wrapper 产物、对外仍是同一注册面，显式继承元数据保持该命名契约（CP-2.2-5）。
coding.__name__ = _coding_react.__name__          # "react_wrapper_coding"
coding.__qualname__ = _coding_react.__qualname__
coding.__module__ = _coding_react.__module__      # "core.react_base"
