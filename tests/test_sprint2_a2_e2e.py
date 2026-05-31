"""Sprint 2 任务 A2 端到端/集成验证：create_llm + resolve_llm_config 真实装配链选路。

A2 的 resolve_llm_config 是纯函数（不发网络），故 e2e 的意义在于验证「真实装配链」：
    create_llm(resolve_llm_config(config_set, node_name))
能基于路由结果构造出**真实的 ChatOpenAI 实例**，且其 model_name 等于：
    - 命中 override 的节点 → override 指定的 model；
    - 未命中 / None 共用路径 → default 的 model。

设计要点：
- create_llm 只构造对象、读 .model_name 属性即可验证选路，**不发起任何 LLM 请求**
  （省 token、结果稳定，不受网络抖动影响）。
- 凭证缺失时整文件 skip（参考 tests/test_paper_intake_e2e.py 的 _has_credentials 模式）。

运行方式：
    pytest tests/test_sprint2_a2_e2e.py -v
任一凭证缺失则全部跳过（属正常）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import (  # noqa: E402
    DEFAULT_LLM_MAX_TOKENS,
    DEFAULT_LLM_TEMPERATURE,
    get_llm_api_key,
    get_llm_base_url,
    get_llm_model,
)
from core.llm_client import create_llm, resolve_llm_config  # noqa: E402
from core.state import LLMConfig, LLMConfigSet, create_initial_state  # noqa: E402


pytestmark = pytest.mark.e2e


def _has_credentials() -> bool:
    """A2 e2e 仅需 LLM_API_KEY（构造 ChatOpenAI 即可，不发请求、不依赖 deepxiv）。"""
    return bool(get_llm_api_key())


skip_if_no_creds = pytest.mark.skipif(
    not _has_credentials(),
    reason="缺 LLM_API_KEY，跳过 A2 e2e",
)


# 覆写模型名特意与 default 不同，便于断言选路命中。
OVERRIDE_MODEL = "azure/openai/gpt-5.4-a2-override"


def _build_config_set() -> LLMConfigSet:
    """用真实凭证 + 真实 base_url 造一份带 override 的 LLMConfigSet。"""
    api_key = get_llm_api_key() or "sk-placeholder"
    base_url = get_llm_base_url()
    default_model = get_llm_model()

    default_cfg: LLMConfig = {
        "base_url": base_url,
        "model": default_model,
        "api_key": api_key,
        "temperature": DEFAULT_LLM_TEMPERATURE,
        "max_tokens": DEFAULT_LLM_MAX_TOKENS,
    }
    override_cfg: LLMConfig = {
        "base_url": base_url,
        "model": OVERRIDE_MODEL,
        "api_key": api_key,
        "temperature": 0.1,
        "max_tokens": DEFAULT_LLM_MAX_TOKENS,
    }
    return {
        "default": default_cfg,
        "overrides": {"paper_analysis": override_cfg},
    }


@skip_if_no_creds
def test_e2e_assembly_chain_hit_override_builds_override_model() -> None:
    """命中 override：create_llm(resolve_llm_config(..., 'paper_analysis')) 的 model_name == override.model。"""
    config_set = _build_config_set()
    resolved = resolve_llm_config(config_set, "paper_analysis")
    llm = create_llm(resolved)
    assert llm.model_name == OVERRIDE_MODEL, (
        f"命中 override 时 ChatOpenAI.model_name 应为 {OVERRIDE_MODEL}，实测 {llm.model_name}"
    )


@skip_if_no_creds
def test_e2e_assembly_chain_miss_override_builds_default_model() -> None:
    """未命中 override：未覆写节点 'paper_intake' 的 ChatOpenAI.model_name == default.model。"""
    config_set = _build_config_set()
    default_model = config_set["default"]["model"]
    resolved = resolve_llm_config(config_set, "paper_intake")
    llm = create_llm(resolved)
    assert llm.model_name == default_model, (
        f"未命中 override 时 model_name 应为 default {default_model}，实测 {llm.model_name}"
    )


@skip_if_no_creds
def test_e2e_assembly_chain_none_node_builds_default_model() -> None:
    """None 共用路径：create_llm(resolve_llm_config(..., None)) 的 model_name == default.model。"""
    config_set = _build_config_set()
    default_model = config_set["default"]["model"]
    resolved = resolve_llm_config(config_set, None)
    llm = create_llm(resolved)
    assert llm.model_name == default_model, (
        f"None 共用路径 model_name 应为 default {default_model}，实测 {llm.model_name}"
    )


@skip_if_no_creds
def test_e2e_assembly_chain_via_create_initial_state() -> None:
    """全链路：create_initial_state 规整出的 state.llm_config_set 经装配链选路命中 override。"""
    config_set = _build_config_set()
    state = create_initial_state("2405.14831", config_set)
    routed_set = state["llm_config_set"]

    hit_llm = create_llm(resolve_llm_config(routed_set, "paper_analysis"))
    miss_llm = create_llm(resolve_llm_config(routed_set, "planning"))

    assert hit_llm.model_name == OVERRIDE_MODEL, "经 create_initial_state 后命中节点仍应取 override"
    assert miss_llm.model_name == config_set["default"]["model"], (
        "经 create_initial_state 后未覆写节点应回退 default"
    )
