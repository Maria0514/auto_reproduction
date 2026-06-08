"""B2 - llm_client Prompt Cache 子任务单测。

覆盖 Prompt Cache 方案 A（参见架构文档 §2.6.6 / 技术架构文档 §10.5）：
- _log_cache_metrics 在响应含缓存字段时触发 INFO 日志
- 无缓存字段时静默且不抛错
- 异常 response 对象被静默吞掉
- LLM_ENABLE_PROMPT_CACHE=False 时不输出日志
- create_llm 签名未变更（向后兼容）
"""
from __future__ import annotations

import inspect
import logging
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

# 保证测试能 import 项目根模块（pytest 默认 cwd 已在项目根）
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _make_response(usage_metadata: Any = None, response_metadata: Any = None) -> SimpleNamespace:
    """构造一个最小化的 response 对象，模拟 LangChain AIMessage 的关键属性。"""
    return SimpleNamespace(
        content="ok",
        usage_metadata=usage_metadata,
        response_metadata=response_metadata,
    )


# ---------- 用例 1：含缓存字段的响应应触发 INFO 日志 ----------

def test_log_cache_metrics_hits_langchain_usage_metadata(caplog):
    from core import llm_client

    response = _make_response(
        usage_metadata={
            "input_tokens": 1000,
            "input_token_details": {"cache_read": 800},
        }
    )

    with caplog.at_level(logging.INFO, logger="core.llm_client"):
        llm_client._log_cache_metrics(response)

    hit_records = [r for r in caplog.records if "Prompt cache hit" in r.getMessage()]
    assert len(hit_records) == 1, f"应触发一次 INFO 日志, 实际 records: {caplog.records}"
    msg = hit_records[0].getMessage()
    assert "cached_tokens=800" in msg
    assert "prompt_tokens=1000" in msg


def test_log_cache_metrics_hits_openai_style_prompt_tokens_details(caplog):
    from core import llm_client

    response = _make_response(
        response_metadata={
            "token_usage": {
                "prompt_tokens": 500,
                "prompt_tokens_details": {"cached_tokens": 320},
            }
        }
    )

    with caplog.at_level(logging.INFO, logger="core.llm_client"):
        llm_client._log_cache_metrics(response)

    hit_records = [r for r in caplog.records if "Prompt cache hit" in r.getMessage()]
    assert len(hit_records) == 1
    msg = hit_records[0].getMessage()
    assert "cached_tokens=320" in msg
    assert "prompt_tokens=500" in msg


def test_log_cache_metrics_hits_anthropic_style_cache_read_input_tokens(caplog):
    from core import llm_client

    response = _make_response(
        response_metadata={
            "usage": {
                "input_tokens": 1200,
                "cache_read_input_tokens": 1100,
                "cache_creation_input_tokens": 50,
            }
        }
    )

    with caplog.at_level(logging.INFO, logger="core.llm_client"):
        llm_client._log_cache_metrics(response)

    hit_records = [r for r in caplog.records if "Prompt cache hit" in r.getMessage()]
    assert len(hit_records) == 1
    msg = hit_records[0].getMessage()
    assert "cached_tokens=1100" in msg
    assert "prompt_tokens=1200" in msg


# ---------- 用例 2：无缓存字段的响应应静默 ----------

def test_log_cache_metrics_no_cache_field_silent(caplog):
    from core import llm_client

    # 含 usage 但没有任何 cached_tokens
    response = _make_response(
        usage_metadata={"input_tokens": 800, "output_tokens": 200},
        response_metadata={"usage": {"prompt_tokens": 800, "completion_tokens": 200}},
    )

    with caplog.at_level(logging.INFO, logger="core.llm_client"):
        result = llm_client._log_cache_metrics(response)

    assert result is None
    hit_records = [r for r in caplog.records if "Prompt cache hit" in r.getMessage()]
    assert hit_records == [], "无 cached_tokens 时不应输出命中日志"


def test_log_cache_metrics_empty_response_silent(caplog):
    from core import llm_client

    response = _make_response(usage_metadata=None, response_metadata=None)

    with caplog.at_level(logging.INFO, logger="core.llm_client"):
        result = llm_client._log_cache_metrics(response)

    assert result is None
    hit_records = [r for r in caplog.records if "Prompt cache hit" in r.getMessage()]
    assert hit_records == []


# ---------- 用例 3：异常 response 对象应被静默吞掉 ----------

def test_log_cache_metrics_swallows_attribute_error():
    from core import llm_client

    class BadResponse:
        @property
        def usage_metadata(self):
            raise AttributeError("intentionally broken")

        @property
        def response_metadata(self):
            raise AttributeError("intentionally broken")

    # 必须返回 None 且不抛错
    result = llm_client._log_cache_metrics(BadResponse())
    assert result is None


def test_log_cache_metrics_swallows_generic_exception():
    from core import llm_client

    class ExplodingResponse:
        @property
        def usage_metadata(self):
            raise RuntimeError("boom")

        @property
        def response_metadata(self):
            raise RuntimeError("boom")

    result = llm_client._log_cache_metrics(ExplodingResponse())
    assert result is None


# ---------- 用例 4：LLM_ENABLE_PROMPT_CACHE=False 时不输出日志 ----------

def test_log_cache_metrics_disabled_skips_logging(caplog, monkeypatch):
    from core import llm_client

    monkeypatch.setattr(llm_client, "LLM_ENABLE_PROMPT_CACHE", False)

    response = _make_response(
        usage_metadata={
            "input_tokens": 1000,
            "input_token_details": {"cache_read": 800},
        }
    )

    with caplog.at_level(logging.INFO, logger="core.llm_client"):
        llm_client._log_cache_metrics(response)

    hit_records = [r for r in caplog.records if "Prompt cache hit" in r.getMessage()]
    assert hit_records == [], "开关关闭时不应输出日志"


# ---------- 用例 5：create_llm 签名未变 ----------

def test_create_llm_signature_unchanged():
    from core import llm_client

    sig = inspect.signature(llm_client.create_llm)
    params = list(sig.parameters.values())
    assert len(params) == 1, "create_llm 应仅接受 config 一个参数"
    assert params[0].name == "config"


# ---------- D1 增强：create_llm api_key 回退（方案 A，架构 §2.7.2） ----------
#
# 回退点裁定：config["api_key"] strip 后为空 → 回退 get_llm_api_key()（读 .env）；
# 非空时不回退（用户显式优先）；回退到 None 不在 create_llm 抛错；不回写入参 dict。


def _capture_chatopenai(monkeypatch):
    """patch core.llm_client.ChatOpenAI 为捕获 kwargs 的假类，返回捕获 dict。"""
    captured: dict = {}

    class _FakeChatOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    from core import llm_client
    monkeypatch.setattr(llm_client, "ChatOpenAI", _FakeChatOpenAI)
    return captured


def _base_config(api_key: str) -> dict:
    return {
        "base_url": "https://api.x.com/v1",
        "model": "gpt-4o",
        "api_key": api_key,
        "temperature": 0.3,
        "max_tokens": 8192,
    }


def test_d1e_create_llm_blank_api_key_falls_back_to_env(monkeypatch):
    """T-D1E-U01：空 api_key（""）→ 回退 get_llm_api_key()。"""
    from core import llm_client
    captured = _capture_chatopenai(monkeypatch)
    monkeypatch.setattr(llm_client, "get_llm_api_key", lambda: "env-key")
    llm_client.create_llm(_base_config(""))
    assert captured["api_key"] == "env-key"


def test_d1e_create_llm_nonblank_api_key_no_fallback(monkeypatch):
    """T-D1E-U02：非空 api_key（'sk-user'）→ 不回退，env 值不被采用。"""
    from core import llm_client
    captured = _capture_chatopenai(monkeypatch)
    called = {"n": 0}

    def _spy_get_key():
        called["n"] += 1
        return "env-key"

    monkeypatch.setattr(llm_client, "get_llm_api_key", _spy_get_key)
    llm_client.create_llm(_base_config("sk-user"))
    assert captured["api_key"] == "sk-user"
    # 非空时不应采用 env 值（短路：get_llm_api_key 不被调用）。
    assert called["n"] == 0


def test_d1e_create_llm_whitespace_api_key_falls_back(monkeypatch):
    """T-D1E-U03：纯空白 api_key（'  '）→ strip 后视为空 → 回退 env。"""
    from core import llm_client
    captured = _capture_chatopenai(monkeypatch)
    monkeypatch.setattr(llm_client, "get_llm_api_key", lambda: "env-key")
    llm_client.create_llm(_base_config("   "))
    assert captured["api_key"] == "env-key"


def test_d1e_create_llm_fallback_to_none_does_not_raise(monkeypatch):
    """T-D1E-U04：空 api_key 且 get_llm_api_key()→None → 不在 create_llm 抛错，
    原样传给 ChatOpenAI（错误交后续 invoke 的 LLMError 路径）。"""
    from core import llm_client
    captured = _capture_chatopenai(monkeypatch)
    monkeypatch.setattr(llm_client, "get_llm_api_key", lambda: None)
    # 不应抛任何异常。
    llm_client.create_llm(_base_config(""))
    assert captured["api_key"] is None


def test_d1e_create_llm_does_not_mutate_input_config(monkeypatch):
    """T-D1E-U05：回退值不回写入参 config dict（仅进程内存）。"""
    import copy

    from core import llm_client
    _capture_chatopenai(monkeypatch)
    monkeypatch.setattr(llm_client, "get_llm_api_key", lambda: "env-key")
    cfg = _base_config("")
    cfg_before = copy.deepcopy(cfg)
    llm_client.create_llm(cfg)
    # 入参 api_key 调用后仍为 ""（回退值不写回 state）。
    assert cfg["api_key"] == ""
    assert cfg == cfg_before


# ---------- 用例 6：LLM_ENABLE_PROMPT_CACHE 默认与 env 解析 ----------

def test_llm_enable_prompt_cache_default_true(monkeypatch):
    """默认值应为 True；'false'/'0'/'no'/'off' 视为 False。"""
    import importlib
    monkeypatch.delenv("LLM_ENABLE_PROMPT_CACHE", raising=False)
    import config as cfg
    importlib.reload(cfg)
    assert cfg.LLM_ENABLE_PROMPT_CACHE is True


def test_llm_enable_prompt_cache_env_false_variants(monkeypatch):
    import importlib
    import config as cfg

    for raw in ["false", "False", "FALSE", "0", "no", "NO", "off", "Off"]:
        monkeypatch.setenv("LLM_ENABLE_PROMPT_CACHE", raw)
        importlib.reload(cfg)
        assert cfg.LLM_ENABLE_PROMPT_CACHE is False, f"{raw!r} 应解析为 False"

    for raw in ["true", "TRUE", "1", "yes", "anything-else"]:
        monkeypatch.setenv("LLM_ENABLE_PROMPT_CACHE", raw)
        importlib.reload(cfg)
        assert cfg.LLM_ENABLE_PROMPT_CACHE is True, f"{raw!r} 应解析为 True"

    # 还原默认
    monkeypatch.delenv("LLM_ENABLE_PROMPT_CACHE", raising=False)
    importlib.reload(cfg)
