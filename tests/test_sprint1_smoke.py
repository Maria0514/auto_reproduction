"""Sprint 1 阶段 E 冒烟自测。

覆盖 dev-plan.md L786~806 全部 5 类自测点：

1. 10 条 import 语句逐条验证；
2. 异常继承关系；
3. ``create_initial_state`` 默认值；
4. ``estimate_tokens`` 基本功能；
5. ``config.py`` 环境变量覆盖（reload 验证）。

刻意只做轻量结构性断言，不重复 e2e 节点测试已覆盖的逻辑，
且不打任何真实 LLM / deepxiv 调用。
"""

from __future__ import annotations

import importlib
import os

import pytest


# ========== 1. 导入测试 ==========


def test_import_core_state() -> None:
    from core.state import (
        ExecutionMode,
        GlobalState,
        PaperAnalysis,
        PaperMeta,
        create_initial_state,
    )

    assert GlobalState is not None
    assert PaperMeta is not None
    assert PaperAnalysis is not None
    assert ExecutionMode is not None
    assert callable(create_initial_state)


def test_import_core_errors() -> None:
    from core.errors import (
        AutoReproError,
        LLMAuthError,
        LLMContextOverflowError,
        LLMOutputError,
        LLMRateLimitError,
        PermanentError,
        TransientError,
        make_node_error,
    )

    assert AutoReproError is not None
    assert TransientError is not None
    assert PermanentError is not None
    assert LLMAuthError is not None
    assert LLMRateLimitError is not None
    assert LLMContextOverflowError is not None
    assert LLMOutputError is not None
    assert callable(make_node_error)


def test_import_core_llm_client() -> None:
    from core.llm_client import (
        call_with_structured_output,
        check_context_limit,
        create_llm,
        estimate_tokens,
    )

    assert callable(create_llm)
    assert callable(call_with_structured_output)
    assert callable(estimate_tokens)
    assert callable(check_context_limit)


def test_import_core_react_base() -> None:
    from core.react_base import (
        ReActState,
        _make_react_wrapper,
        create_react_subgraph,
    )

    assert ReActState is not None
    assert callable(create_react_subgraph)
    assert callable(_make_react_wrapper)


def test_import_core_tools_deepxiv() -> None:
    from core.tools.deepxiv_tools import (
        DeepxivTools,
        get_full_paper_tool,
        get_paper_brief_tool,
        get_paper_head_tool,
        get_paper_structure_tool,
        read_section_tool,
        search_papers_tool,
        web_search_tool,
    )

    assert DeepxivTools is not None
    for factory in (
        get_paper_brief_tool,
        get_paper_head_tool,
        get_paper_structure_tool,
        read_section_tool,
        get_full_paper_tool,
        search_papers_tool,
        web_search_tool,
    ):
        assert callable(factory)


def test_import_core_nodes_paper_intake() -> None:
    from core.nodes.paper_intake import paper_intake

    assert callable(paper_intake)


def test_import_core_nodes_paper_analysis() -> None:
    from core.nodes.paper_analysis import paper_analysis

    assert callable(paper_analysis)


def test_import_core_checkpointer() -> None:
    from core.checkpointer import get_checkpointer

    assert callable(get_checkpointer)


def test_import_core_graph() -> None:
    from core.graph import build_graph

    assert callable(build_graph)


def test_import_config_symbols() -> None:
    from config import (
        CHECKPOINT_DB_PATH,
        PROJECT_ROOT,
        REACT_MAX_ROUNDS_PAPER_ANALYSIS,
        REACT_MAX_ROUNDS_PAPER_INTAKE,
        REACT_RESULT_TAG_CLOSE,
        REACT_RESULT_TAG_OPEN,
        TOOL_RESULT_MAX_LENGTH,
        get_deepxiv_token,
        get_llm_api_key,
    )

    assert PROJECT_ROOT is not None
    assert CHECKPOINT_DB_PATH is not None
    assert isinstance(REACT_MAX_ROUNDS_PAPER_INTAKE, int)
    assert isinstance(REACT_MAX_ROUNDS_PAPER_ANALYSIS, int)
    assert REACT_RESULT_TAG_OPEN == "<result>"
    assert REACT_RESULT_TAG_CLOSE == "</result>"
    assert isinstance(TOOL_RESULT_MAX_LENGTH, int) and TOOL_RESULT_MAX_LENGTH > 0
    assert callable(get_deepxiv_token)
    assert callable(get_llm_api_key)


# ========== 2. 异常继承关系验证 ==========


def test_exception_hierarchy() -> None:
    from core.errors import (
        AutoReproError,
        LLMAuthError,
        LLMContextOverflowError,
        LLMError,
        LLMOutputError,
        LLMRateLimitError,
        PermanentError,
        TransientError,
    )

    # 一级分支：可重试 vs 不可重试
    assert issubclass(TransientError, AutoReproError)
    assert issubclass(PermanentError, AutoReproError)

    # LLM 系列基类
    assert issubclass(LLMError, AutoReproError)

    # 永久错误分支（认证、上下文溢出）
    assert issubclass(LLMAuthError, LLMError)
    assert issubclass(LLMAuthError, PermanentError)
    assert issubclass(LLMContextOverflowError, LLMError)
    assert issubclass(LLMContextOverflowError, PermanentError)

    # 瞬态错误分支（限流、输出格式）
    assert issubclass(LLMRateLimitError, LLMError)
    assert issubclass(LLMRateLimitError, TransientError)
    assert issubclass(LLMOutputError, LLMError)
    assert issubclass(LLMOutputError, TransientError)

    # 互斥性：永久 != 瞬态
    assert not issubclass(LLMAuthError, TransientError)
    assert not issubclass(LLMRateLimitError, PermanentError)


# ========== 3. create_initial_state 默认值验证 ==========


def test_create_initial_state_defaults() -> None:
    from core.state import ExecutionMode, LLMConfig, create_initial_state

    llm_config: LLMConfig = {
        "base_url": "https://example.com/v1",
        "model": "test-model",
        "api_key": "sk-test",
        "temperature": 0.3,
        "max_tokens": 8192,
    }

    state = create_initial_state(user_input="2405.14831", llm_config=llm_config)

    # 关键默认值断言（dev-plan L802）；初值引用常量，默认预算调整后不再破。
    from config import MAX_TOTAL_LLM_CALLS

    assert state["retry_budget_remaining"] == MAX_TOTAL_LLM_CALLS
    assert state["fix_loop_count"] == 0
    assert state["execution_mode"] == ExecutionMode.FULL
    assert state["node_errors"] == []
    assert state["degraded_nodes"] == []
    assert state["paper_meta"] is None
    assert state["paper_analysis"] is None

    # 顺带核查不容易遗漏的几个字段
    assert state["user_input"] == "2405.14831"
    # A3 起镜像字段 llm_config 已移除；老形态入参被包装进 llm_config_set.default
    assert "llm_config" not in state
    assert state["llm_config_set"]["default"] == llm_config
    assert state["current_step"] == "start"
    assert state["error"] is None
    assert state["messages"] == []
    assert state["fix_loop_history"] == []


# ========== 4. estimate_tokens 基本功能验证 ==========


def test_estimate_tokens_returns_positive_int() -> None:
    from core.llm_client import estimate_tokens

    # 空串 → 0 或正整数皆可，但必须是 int
    zero = estimate_tokens("")
    assert isinstance(zero, int)
    assert zero >= 0

    # 非空字符串 → 必为正整数
    short = estimate_tokens("hello world")
    assert isinstance(short, int)
    assert short > 0

    # 长文本应比短文本估算更多 token
    long_text = "hello world " * 200
    long_val = estimate_tokens(long_text)
    assert isinstance(long_val, int)
    assert long_val > short


# ========== 5. config.py 环境变量覆盖验证 ==========


def test_config_env_override_reload(monkeypatch: pytest.MonkeyPatch) -> None:
    """改 env 后 reload `config` 模块，验证 `get_*` 取到新值。"""
    import config as config_module

    # 用 monkeypatch 改两个常用 env，作用域只在本 test 内
    monkeypatch.setenv("DEEPXIV_TOKEN", "test-deepxiv-token-XYZ")
    monkeypatch.setenv("LLM_API_KEY", "test-llm-key-XYZ")

    # reload 确保 config 内部的 os.environ 读取在最新 env 下运行
    # （当前 get_* 是函数式读取，本身已能感知 env 改变；reload 仅做防御）
    reloaded = importlib.reload(config_module)

    assert reloaded.get_deepxiv_token() == "test-deepxiv-token-XYZ"
    assert reloaded.get_llm_api_key() == "test-llm-key-XYZ"

    # monkeypatch 会在 fixture teardown 自动恢复 env
    # reload 一次回到原 env 状态，保证后续 test 看到的 config 行为不变
    monkeypatch.delenv("DEEPXIV_TOKEN", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    # 同步在 .env 之外再清一遍 os.environ（防御 dotenv 之前已设值）
    os.environ.pop("DEEPXIV_TOKEN_OVERRIDE_SENTINEL", None)
    importlib.reload(config_module)
