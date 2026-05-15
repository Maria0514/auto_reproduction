"""C1 - paper_intake 真实端到端 pytest（依赖外部 LLM + deepxiv API）。

测试论文：arXiv:2405.14831v3 (https://arxiv.org/abs/2405.14831)

运行方式：
    LLM_API_KEY=... DEEPXIV_TOKEN=... pytest tests/test_paper_intake_e2e.py -m e2e -v -s

任一凭证缺失则全部跳过。pytest -m e2e 选择性运行。
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import (  # noqa: E402
    DEFAULT_LLM_MAX_TOKENS,
    DEFAULT_LLM_TEMPERATURE,
    get_deepxiv_token,
    get_llm_api_key,
    get_llm_base_url,
    get_llm_model,
)
from core.nodes.paper_intake import NODE_NAME, paper_intake  # noqa: E402
from core.state import GlobalState, LLMConfig, create_initial_state  # noqa: E402


PAPER_ARXIV_ID = "2405.14831"
INPUT_VERSIONED = "2405.14831v3"
INPUT_URL = "https://arxiv.org/abs/2405.14831"
INPUT_PLAIN = "2405.14831"


pytestmark = pytest.mark.e2e


def _has_credentials() -> bool:
    return bool(get_llm_api_key()) and bool(get_deepxiv_token())


skip_if_no_creds = pytest.mark.skipif(
    not _has_credentials(),
    reason="缺少 LLM_API_KEY 或 DEEPXIV_TOKEN 环境变量",
)


@pytest.fixture(scope="module")
def llm_config() -> LLMConfig:
    return LLMConfig(
        base_url=get_llm_base_url(),
        model=get_llm_model(),
        api_key=get_llm_api_key() or "",
        temperature=DEFAULT_LLM_TEMPERATURE,
        max_tokens=DEFAULT_LLM_MAX_TOKENS,
    )


def _make_state(user_input: str, cfg: LLMConfig) -> GlobalState:
    return create_initial_state(user_input=user_input, llm_config=cfg)


def _assert_paper_meta_valid(update: Dict[str, Any]) -> None:
    """通用断言：update 含完整 paper_meta、无 error、arxiv_id 已清洗。"""
    assert update.get("error") is None, (
        f"返回带 error: {update.get('error')}; "
        f"node_errors={update.get('node_errors')}"
    )
    assert update.get("current_step") == NODE_NAME, (
        f"current_step 不符: {update.get('current_step')}"
    )

    pm = update.get("paper_meta")
    assert pm, f"paper_meta 未填充: update={update}"
    assert pm["arxiv_id"] == PAPER_ARXIV_ID, (
        f"arxiv_id 未清洗为 {PAPER_ARXIV_ID}: 实际 {pm['arxiv_id']!r}"
    )
    assert pm["title"], "title 为空"
    assert isinstance(pm["authors"], list) and len(pm["authors"]) > 0, (
        f"authors 为空: {pm['authors']}"
    )
    assert pm["abstract"], "abstract 为空"
    assert isinstance(pm["categories"], list) and len(pm["categories"]) > 0, (
        f"categories 为空: {pm['categories']}"
    )


@skip_if_no_creds
def test_e2e_versioned_id_cleanup(llm_config):
    """输入带版本号的 ID `2405.14831v3`，应清洗为 `2405.14831` 并完整返回元数据。"""
    state = _make_state(INPUT_VERSIONED, llm_config)
    update = paper_intake(state)

    _assert_paper_meta_valid(update)

    # 预算应已扣减（至少 1 轮）
    assert update["retry_budget_remaining"] < state["retry_budget_remaining"], (
        f"retry_budget_remaining 未扣减: before={state['retry_budget_remaining']}, "
        f"after={update['retry_budget_remaining']}"
    )


@skip_if_no_creds
def test_e2e_full_url_cleanup(llm_config):
    """输入完整 arXiv URL，应抽取 ID 并完整返回元数据。"""
    state = _make_state(INPUT_URL, llm_config)
    update = paper_intake(state)

    _assert_paper_meta_valid(update)


@skip_if_no_creds
def test_e2e_plain_id_cs_category(llm_config):
    """输入纯 ID，元数据完整且 categories 含 cs.* 前缀（论文属 CS 领域）。"""
    state = _make_state(INPUT_PLAIN, llm_config)
    update = paper_intake(state)

    _assert_paper_meta_valid(update)

    pm = update["paper_meta"]
    assert any(c.lower().startswith("cs.") for c in pm["categories"]), (
        f"论文应属 CS 领域，实际 categories={pm['categories']}"
    )


@skip_if_no_creds
def test_e2e_node_errors_empty_on_success(llm_config):
    """正常路径下，node_errors 应保持为空，degraded_nodes 不应包含 paper_intake。"""
    state = _make_state(INPUT_PLAIN, llm_config)
    update = paper_intake(state)

    _assert_paper_meta_valid(update)

    node_errors = update.get("node_errors") or []
    paper_intake_errors = [e for e in node_errors if e.get("node_name") == NODE_NAME]
    assert not paper_intake_errors, (
        f"成功路径不应在 node_errors 中记录 paper_intake 错误: {paper_intake_errors}"
    )
