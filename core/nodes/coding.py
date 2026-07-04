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
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from config import REACT_MAX_ROUNDS_CODING, WORKSPACE_DIR
from core.errors import make_node_error
from core.react_base import _make_react_wrapper
from core.state import GlobalState
from core.secrets_store import build_credential_env, load_all_secrets
from core.tools.code_fs_tools import (
    make_list_dir_tool,
    make_read_code_file_tool,
    make_write_code_file_tool,
)
from core.tools.deepxiv_tools import read_section_tool, web_search_tool
from core.tools.interaction_tools import request_user_input
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

    - 主体 _CODING_SYSTEM_PROMPT_BODY 在不同论文 / 任务间字节级一致。
    - 尾部独立段落仅含常量任务标识，无任何论文级动态变量（动态上下文走 HumanMessage）。
    - 自测断言：截去尾部段落后两次返回值完全相同（CP-C1-6）。
    """
    tail = _format_task_context()
    return (
        _CODING_SYSTEM_PROMPT_BODY
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
        "node_errors": node_errors,
        "degraded_nodes": degraded_nodes,
        # 不写 fix_loop_count（must-fix-2）；
        # retry_budget_remaining 由 wrapper 自动 setdefault 回写（不在此覆盖，must-fix-2）。
    }


# ReAct wrapper：把 GlobalState ↔ ReActState 双向映射 + 子图编译 + 预算扣减都封装好，
# 主图直接 import 该 callable 注册节点即可。
coding = _make_react_wrapper(
    node_name=NODE_NAME,
    build_context=_build_coding_context,
    build_system_prompt=_build_coding_system_prompt,
    get_tools=_get_coding_tools,
    map_result=_map_coding_result,
    max_rounds=REACT_MAX_ROUNDS_CODING,
    result_schema=CODING_OUTPUT_SCHEMA,
)
