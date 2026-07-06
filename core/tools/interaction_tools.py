"""interaction_tools.py -- `request_user_input` 通用交互工具（S4-05，interrupt#3）。

设计权威：docs/sprint4/dev-plan.md §4 任务 B1 + architecture §2.1（参考实现骨架，
四步语义）/ §7.1（payload 契约）/ §8.3（缓解 2：docstring 单独一轮纪律）。

职责：提供**唯一**一个 LangChain ``@tool``，coding / execution 两 agent 共用。
agent 缺任何信息（凭证 / 参数 / 决策 / 路径）时调它，工具体内 ``interrupt(payload)``
暂停主图，UI 收集后 ``Command(resume={"value": str, "remember": bool})`` 恢复，
工具返回值作为 ToolMessage 喂回 agent。**极简三字段，不做 input_type 枚举**
（Maria 反过度工程硬约束）。

四步语义（architecture §2.1）：
    1. **去重前查**：purpose_key 命中 `.secrets`（``lookup_secret``）→ 直接返回
       缓存值、不 interrupt（跨任务复用）；
    2. **interrupt#3**：payload 四键 ``{interrupt_kind, question, is_sensitive,
       purpose_key}``（purpose_key 空串规整为 None），与 ``"planning"`` /
       ``"dev_loop_failure"`` 两类并存，UI 按 interrupt_kind 分发；
    3. **resume 契约**：非法 resume（非 dict / 缺 value）→ 返回空串 + WARNING
       （Q-F2 无前端降级语义，失败非静默）；
    4. **敏感值登记 + 记住**：is_sensitive=True 时无论是否记住均
       ``register_sensitive_value(value)``（保证本次会话内 mask 覆盖）；
       ``remember and purpose_key`` → ``remember_secret(...)`` 0600 落盘。

序列化治理注记（BUG-S1-02 刻意例外）：本工具返回**纯字符串**（用户输入值），
不是 dict / list，返回语义就是裸值，直接作为 ToolMessage 内容、无需
``json.dumps``。若未来返回结构化则必须走
``json.dumps(ensure_ascii=False, sort_keys=True, default=str)``。

Prompt Cache 纪律：``request_user_input`` 的 docstring 是工具 schema 的一部分，
作为稳定前缀参与 Prompt Cache——docstring 内**绝不含**任何论文级 / 任务级动态
变量，多任务间字节级一致（CP-B1-5 锁定）。

安全纪律（与 secrets_store 一致）：本模块 logger 只打 purpose_key / resume 类型，
**绝不打 value / question 全文**（question 可能内嵌上下文片段）。
`.secrets` 落点一律走 secrets_store 默认 ``config.WORKSPACE_DIR``（挂账 L-A3-01
口径：与 ``mask_value`` 基准一致，不额外做 workspace_dir 透传）。

checkpoint 边界（ADJ-S4-G2-02 裁决 2026-07-06）：敏感值经子图 messages
（ToolMessage）与 ``__resume__`` 通道随 LangGraph checkpoint 帧明文落
checkpoints.db，属已知接受限制——威胁面与 .secrets / GIT_ASKPASS 明文落盘
等价（同 gitignore 覆盖、0600 权限对齐）。本模块「不进」承诺的准确范围 =
GlobalState 业务字段 + logs / 报告 / UI 投影面。
"""

from __future__ import annotations

import logging

from langchain_core.tools import tool
from langgraph.types import interrupt

from core.secrets_store import (
    lookup_secret,
    register_sensitive_value,
    remember_secret,
)

logger = logging.getLogger(__name__)

# interrupt#3 类型标识（architecture §7.1：与 "planning" / "dev_loop_failure" 并存）。
INTERRUPT_KIND_USER_INPUT: str = "user_input_request"


@tool
def request_user_input(
    question: str,
    is_sensitive: bool = False,
    purpose_key: str = "",
) -> str:
    """当缺少继续任务所需的信息（凭证 / 参数 / 决策 / 路径）时，向用户索要一条信息。

    仅在确实无法从已有上下文推断、且信息缺失会阻塞任务时调用。一次只问一个信息项。
    本工具会暂停任务等待用户回答：请单独一轮调用，不要与写文件 / 运行命令等
    其他工具放在同一轮 tool_calls 中。

    Args:
        question: 给用户看的问题文本（中文叙述，URL/包名等事实层保留英文）。
        is_sensitive: 凭证/密钥类置 True（UI 用 password 输入、全程脱敏、可「记住」）。
        purpose_key: 信息项稳定标识（如 "git_credential:github.com" / "hf_token"），
            用作 .secrets 的 key + 去重（同 key 命中已存则直接返回，不再打断用户）。

    Returns:
        用户输入的字符串值（敏感值不进 GlobalState 业务字段，日志/报告/UI 投影面统一脱敏）。
    """
    # 1) 去重 / 跨任务复用：purpose_key 命中 .secrets → 直接返回缓存值，不 interrupt。
    if purpose_key:
        cached = lookup_secret(purpose_key)
        if cached is not None:
            # L-B1-02 修复：cache-hit 也按调用方敏感语义补登记，防止 .secrets 条目
            # is_sensitive=False 而本次调用视为敏感时，缓存值游离于 mask 集之外。
            if is_sensitive:
                register_sensitive_value(cached)
            logger.info(
                "request_user_input: purpose_key 命中 .secrets 缓存，跳过 interrupt: "
                "purpose_key=%s",
                purpose_key,
            )
            return cached

    # 2) interrupt#3：payload 带 interrupt_kind 供 UI/GraphController 分发
    #    （§7.1 四键契约；purpose_key 空串规整为 None）。
    resume = interrupt({
        "interrupt_kind": INTERRUPT_KIND_USER_INPUT,
        "question": question,
        "is_sensitive": bool(is_sensitive),
        "purpose_key": purpose_key or None,
    })

    # 3) resume 契约：Command(resume={"value": str, "remember": bool})。
    #    非法 resume → 返回空串 + WARNING（失败非静默，agent 自行决定降级）。
    if not isinstance(resume, dict) or "value" not in resume:
        logger.warning(
            "request_user_input: 非法 resume（期望 dict 且含 'value' 键），"
            "返回空串降级: resume_type=%s, purpose_key=%s",
            type(resume).__name__,
            purpose_key or None,
        )
        return ""
    value = str(resume.get("value") or "")
    remember = bool(resume.get("remember"))

    # 4) 敏感值登记（先登记再落盘：即便落盘失败，本会话 mask 覆盖也已生效）；
    #    「记住」→ 落 .secrets（0600）。敏感值只作为 ToolMessage 内容回 agent，
    #    不进 GlobalState 业务字段（§9.3 脱敏落点）；ToolMessage 随子图 checkpoint
    #    帧明文落库为已知接受限制（ADJ-S4-G2-02 裁决 (a)），mask_value 覆盖
    #    logs / 报告 / UI 投影面。
    if is_sensitive:
        register_sensitive_value(value)
    if remember and purpose_key:
        remember_secret(purpose_key, value, is_sensitive=bool(is_sensitive))
    return value
