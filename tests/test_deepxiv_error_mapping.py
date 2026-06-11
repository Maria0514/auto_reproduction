"""DeepxivTools._handle_sdk_error 异常映射测试。

聚焦 SDK 异常 → 系统异常体系（PermanentError / TransientError）的映射正确性，
重点守门「deepxiv 429 = 日配额耗尽 → 永久错误（不可当天重试）」这一语义。

纯离线：DeepxivTools(token="x") 仅构造 Reader（不发网络请求），_handle_sdk_error
是纯映射逻辑，零 token 消耗、不连网。
"""
from __future__ import annotations

import pytest

from deepxiv_sdk import (
    NotFoundError,
    AuthenticationError,
    BadRequestError,
    RateLimitError,
    ServerError,
    APIError,
)
from core.errors import (
    PermanentError,
    TransientError,
    DeepxivDailyLimitError,
)
from core.tools.deepxiv_tools import DeepxivTools


@pytest.fixture()
def tools() -> DeepxivTools:
    # 传显式 token 走离线构造，不触发 get_deepxiv_token()，Reader.__init__ 不发请求。
    return DeepxivTools(token="offline-test-token")


# --------------------------------------------------------------------------- #
# 核心：429 日配额 → DeepxivDailyLimitError（永久，不可重试）
# --------------------------------------------------------------------------- #
def test_rate_limit_maps_to_daily_limit_permanent(tools: DeepxivTools):
    with pytest.raises(DeepxivDailyLimitError) as exc_info:
        tools._handle_sdk_error(RateLimitError("Daily limit reached"), "get_paper_brief")
    err = exc_info.value
    # 必须是永久错误，绝不能是瞬态（否则上层会无谓重试，当天必再 429）。
    assert isinstance(err, PermanentError)
    assert not isinstance(err, TransientError)
    assert "日配额" in err.message
    # detail 透传原始 SDK 异常信息。
    assert "RateLimitError" in (err.detail or "")


def test_daily_limit_class_hierarchy():
    assert issubclass(DeepxivDailyLimitError, PermanentError)
    assert not issubclass(DeepxivDailyLimitError, TransientError)


# --------------------------------------------------------------------------- #
# 回归：其余映射不变（守整个映射块的稳定性）
# --------------------------------------------------------------------------- #
def test_server_error_stays_transient(tools: DeepxivTools):
    # 5xx 仍归瞬态可重试（稍后重试可能成功），但不再声称 "SDK already retried"。
    with pytest.raises(TransientError) as exc_info:
        tools._handle_sdk_error(ServerError("boom"), "search_papers")
    assert not isinstance(exc_info.value, PermanentError)


def test_generic_api_error_stays_transient(tools: DeepxivTools):
    with pytest.raises(TransientError):
        tools._handle_sdk_error(APIError("network exhausted"), "read_section")


@pytest.mark.parametrize("sdk_exc", [
    NotFoundError("not found"),
    AuthenticationError("bad token"),
    BadRequestError("bad id"),
])
def test_client_errors_stay_permanent(tools: DeepxivTools, sdk_exc: Exception):
    with pytest.raises(PermanentError) as exc_info:
        tools._handle_sdk_error(sdk_exc, "get_paper_head")
    assert not isinstance(exc_info.value, TransientError)


def test_unknown_error_defaults_transient(tools: DeepxivTools):
    with pytest.raises(TransientError):
        tools._handle_sdk_error(RuntimeError("???"), "web_search")
