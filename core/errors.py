"""统一异常层次定义。

实现三层防御式错误处理架构（技术架构文档 section 12）的基础类型。
所有系统内部异常均继承自 AutoReproError，
通过 TransientError / PermanentError 混入区分可重试性。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from core.state import NodeError


class AutoReproError(Exception):
    """系统根异常。

    所有 Auto-Reproduction 系统内部异常的基类。
    外部异常（如 deepxiv_sdk 的 APIError）在工具层被捕获并转换为本体系中的对应异常。

    Attributes:
        message: 人类可读的错误描述。
        detail: 技术细节（堆栈、响应体等），可选。
        timestamp: 异常创建时间，ISO 8601 格式。
    """
    def __init__(self, message: str, detail: Optional[str] = None):
        super().__init__(message)
        self.message = message
        self.detail = detail
        self.timestamp = datetime.now(timezone.utc).isoformat()


class TransientError(AutoReproError):
    """瞬态错误，可重试。

    触发场景：网络超时、API 限流、服务端 5xx 等。
    处理方式：工具层自动指数退避重试。
    """
    pass


class PermanentError(AutoReproError):
    """永久错误，不可重试。

    触发场景：认证失败、资源不存在、上下文溢出等。
    处理方式：直接传播到节点层，记录 NodeError。
    """
    pass


# --- LLM 相关异常 ---

class LLMError(AutoReproError):
    """LLM 相关错误基类。"""
    pass


class LLMAuthError(LLMError, PermanentError):
    """LLM API 认证失败（HTTP 401）。

    触发场景：api_key 无效或过期。
    处理方式：不重试，立即提示用户检查 API 配置。
    """
    pass


class LLMRateLimitError(LLMError, TransientError):
    """LLM API 限流（HTTP 429）。

    触发场景：请求频率超出限制。
    处理方式：指数退避重试，优先解析 Retry-After 头。

    Attributes:
        retry_after: Retry-After 头中的等待秒数，可选。
    """
    def __init__(self, message: str, detail: Optional[str] = None,
                 retry_after: Optional[float] = None):
        super().__init__(message, detail)
        self.retry_after = retry_after


class LLMContextOverflowError(LLMError, PermanentError):
    """LLM 上下文窗口溢出。

    触发场景：输入 token 数超出模型的上下文窗口限制。
    处理方式：不重试，节点层切换为精简分析模式。
    """
    pass


class LLMOutputError(LLMError, TransientError):
    """LLM 输出格式不合规。

    触发场景：LLM 返回的内容无法解析为指定 JSON Schema。
    处理方式：附加错误信息后重试（最多 3 次）。
    """
    pass


# --- 沙箱相关异常（Sprint 1 仅定义，Sprint 3 使用）---

class SandboxError(AutoReproError):
    """沙箱相关错误基类。"""
    pass


class SandboxCreationError(SandboxError, PermanentError):
    """沙箱创建失败。

    触发场景：Python 版本不满足、磁盘空间不足。
    """
    pass


class CodeExecutionError(SandboxError):
    """代码执行失败。

    注意：CodeExecutionError 本身不混入 TransientError 或 PermanentError，
    因为代码执行失败可能是瞬态的（如网络下载数据失败）也可能是永久的（如 OOM），
    具体由子类决定。
    """
    pass


class OOMError(CodeExecutionError, PermanentError):
    """内存/显存溢出。"""
    pass


class ExecutionTimeoutError(CodeExecutionError, PermanentError):
    """执行超时。"""
    pass


class DegradedResultError(AutoReproError):
    """降级运行完成，非致命。

    触发场景：节点部分功能降级完成（如 paper_analysis 某些章节读取失败
    但仍产出了部分分析结果）。
    处理方式：不中断流程，记录到 degraded_nodes，在报告中标注。

    注意：DegradedResultError 既不是 TransientError 也不是 PermanentError。
    它表示"完成了，但质量有损"。
    """
    pass


# --- 辅助函数 ---

def make_node_error(
    node_name: str,
    error_type: str,
    error_message: str,
    error_detail: Optional[str] = None,
    retry_count: int = 0,
    resolved: bool = False,
) -> NodeError:
    """创建 NodeError TypedDict 实例的工厂函数。

    使用延迟导入避免与 core.state 之间的循环依赖。

    Args:
        node_name: 发生错误的节点名。
        error_type: "transient" | "permanent" | "degraded"。
        error_message: 人类可读的错误描述。
        error_detail: 技术细节，可选。
        retry_count: 已重试次数。
        resolved: 是否已通过重试/降级解决。

    Returns:
        NodeError TypedDict 实例。
    """
    from core.state import NodeError
    return NodeError(
        node_name=node_name,
        error_type=error_type,
        error_message=error_message,
        error_detail=error_detail,
        timestamp=datetime.now(timezone.utc).isoformat(),
        retry_count=retry_count,
        resolved=resolved,
    )
