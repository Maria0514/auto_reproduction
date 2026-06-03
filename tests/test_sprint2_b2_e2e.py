"""Sprint 2 B2 - resource_scout 真实端到端 pytest（依赖真实网络 + LLM）。

本轮深化补全：在 34+17 个 mock 单测之上，补真实链路 e2e，验证搜索优先级链
端到端可用（真实 search_pwc 匿名 + 真实 git clone 公开小仓库 + 真实 web_search 降级）。

凭证驱动（无 flag）：
- LLM_API_KEY + DEEPXIV_TOKEN 就绪 -> 节点级 LLM e2e 真跑（conftest 已自动加载 .env）；
- 任一缺失 -> 对应用例自动 skip，reason 可见。
- PWC_API_TOKEN 缺失不阻塞：pwc_tools 设计为匿名访问，仅工具层网络 e2e 受网络可达性影响。

省 token / 防抖设计：
- 真实 clone 靶选 octocat/Hello-World（极小仓库，秒级 clone，不爆 token）；
- resource_scout 节点级真实 LLM 用例只跑一次（module fixture 缓存），下游断言复用；
- 节点级用例靶论文 arXiv:2405.14831 (HippoRAG)，CS 领域、已被 sp1 e2e 验证可访问。

运行：
    pytest tests/test_sprint2_b2_e2e.py -m e2e -v -s
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytest
from langchain_core.messages import BaseMessage, SystemMessage

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
from core.nodes.paper_intake import paper_intake  # noqa: E402
from core.nodes.resource_scout import (  # noqa: E402
    _RESOURCE_SCOUT_SYSTEM_PROMPT_BODY,
    resource_scout,
)
from core.state import GlobalState, LLMConfig, create_initial_state  # noqa: E402
from core.tools.git_tools import (  # noqa: E402
    check_url_reachable,
    make_git_clone_and_analyze_tool,
)
from core.tools.pwc_tools import make_search_pwc_tool  # noqa: E402


pytestmark = pytest.mark.e2e

PRIMARY_ARXIV_ID = "2405.14831"  # HippoRAG，已被 sp1 e2e 验证可访问
SMALL_PUBLIC_REPO = "https://github.com/octocat/Hello-World"  # 极小公开仓库，秒级 clone
DEAD_REPO_URL = "https://github.com/this-org-does-not-exist-xyz/nope"


def _has_llm_creds() -> bool:
    return bool(get_llm_api_key()) and bool(get_deepxiv_token())


skip_if_no_llm = pytest.mark.skipif(
    not _has_llm_creds(),
    reason="缺少 LLM_API_KEY 或 DEEPXIV_TOKEN，跳过节点级 resource_scout 真实链路 e2e",
)


# ============== Fixtures ==============


@pytest.fixture(scope="module")
def llm_config() -> LLMConfig:
    return LLMConfig(
        base_url=get_llm_base_url(),
        model=get_llm_model(),
        api_key=get_llm_api_key() or "",
        temperature=DEFAULT_LLM_TEMPERATURE,
        max_tokens=DEFAULT_LLM_MAX_TOKENS,
    )


@pytest.fixture(scope="module")
def scout_state_and_update(llm_config) -> Tuple[GlobalState, Dict[str, Any]]:
    """跑一次 paper_intake -> resource_scout（主靶论文）并缓存，下游用例复用省 token。"""
    if not _has_llm_creds():
        pytest.skip("缺少 LLM_API_KEY 或 DEEPXIV_TOKEN")

    state = create_initial_state(user_input=PRIMARY_ARXIV_ID, llm_config=llm_config)
    intake_update = paper_intake(state)
    assert intake_update.get("error") is None, f"paper_intake 失败：{intake_update}"
    merged: Dict[str, Any] = dict(state)
    merged.update(intake_update)
    # 给 paper_analysis 占位（resource_scout build_context 会读，但缺失也容忍）。
    merged.setdefault("paper_analysis", {"framework": None, "datasets": [], "categories": []})

    update = resource_scout(merged)  # type: ignore[arg-type]
    return merged, update  # type: ignore[return-value]


# ============== 工具层真实网络 e2e（不需 LLM，仅需网络） ==============


def test_e2e_check_url_reachable_real_dead_vs_alive():
    """check_url_reachable 真实 HEAD：活仓库 True、不存在的仓库 False（不抛）。"""
    assert check_url_reachable(SMALL_PUBLIC_REPO) is True
    assert check_url_reachable(DEAD_REPO_URL) is False


def test_e2e_git_clone_and_analyze_small_public_repo():
    """真实 git clone 公开小仓库 + 本地分析，ToolMessage 返回合法 JSON RepoInfo。

    验证优先级链步骤 1 的克隆产物契约：success 路径含 local_path / has_readme 等，
    且序列化为合法 JSON（防 BUG-S1-02 类回归）。
    """
    tool = make_git_clone_and_analyze_tool()
    raw = tool.invoke({"url": SMALL_PUBLIC_REPO})
    parsed = json.loads(raw)  # 必须是合法 JSON（非 str(dict)）
    assert isinstance(parsed, dict)
    # 失败也可接受（如沙箱无外网时），但失败必须是 {"success": False, ...} 结构。
    if parsed.get("success") is False:
        pytest.skip(f"clone 失败（环境/网络）：{parsed.get('error')}")
    assert parsed.get("local_path"), "成功 clone 必须含 local_path"
    assert parsed.get("url") == SMALL_PUBLIC_REPO
    assert isinstance(parsed.get("has_readme"), bool)
    assert Path(parsed["local_path"]).exists(), "local_path 应真实落地"


def test_e2e_search_pwc_anonymous_returns_json_contract():
    """真实 search_pwc 匿名查询（PWC_API_TOKEN 缺失走匿名）：

    返回合法 JSON {"results": [...]}；命中/未命中/限流降级均不抛、不破坏契约。
    """
    tool = make_search_pwc_tool()
    raw = tool.invoke({"arxiv_id": PRIMARY_ARXIV_ID, "title": ""})
    parsed = json.loads(raw)
    assert isinstance(parsed, dict)
    assert "results" in parsed
    assert isinstance(parsed["results"], list)
    # 若网络/限流降级，工具返回 {"results": [], "error": ...}——契约仍成立，不视为失败。
    for item in parsed["results"]:
        assert isinstance(item, dict)
        assert "repos" in item


# ============== Prompt Cache 真实链路（需 LLM 截获 SystemMessage） ==============


def _patch_capture_system(monkeypatch, captured: List[List[BaseMessage]]):
    """劫持 ChatOpenAI.invoke 记录每次调用的 messages 副本，不改写真实链路。"""
    from langchain_openai import ChatOpenAI

    orig = ChatOpenAI.invoke

    def _wrapped(self, input, *args, **kwargs):  # noqa: A002
        try:
            if isinstance(input, list):
                captured.append(list(input))
        except Exception:
            pass
        return orig(self, input, *args, **kwargs)

    monkeypatch.setattr(ChatOpenAI, "invoke", _wrapped, raising=True)


@skip_if_no_llm
def test_e2e_prompt_cache_system_body_byte_identical(monkeypatch, llm_config):
    """真实跑两篇不同论文 context，截获真实 LLM 调用的 SystemMessage，
    断言 system prompt 主体（_RESOURCE_SCOUT_SYSTEM_PROMPT_BODY）字节级一致。"""
    captured: List[List[BaseMessage]] = []
    _patch_capture_system(monkeypatch, captured)

    body = _RESOURCE_SCOUT_SYSTEM_PROMPT_BODY

    def _run(arxiv_id: str, title: str):
        state = create_initial_state(user_input=arxiv_id, llm_config=llm_config)
        state["paper_meta"] = {  # type: ignore[index]
            "arxiv_id": arxiv_id, "title": title, "authors": ["X"],
            "github_url": None, "keywords": [],
        }
        state["paper_analysis"] = {"framework": "PyTorch", "datasets": [], "categories": []}  # type: ignore[index]
        try:
            resource_scout(state)  # type: ignore[arg-type]
        except Exception:
            pass  # 即便子图中途出错，只要有过 LLM 调用就已捕获 SystemMessage

    _run("2405.14831", "HippoRAG")
    _run("1706.03762", "Attention Is All You Need")

    system_msgs = [
        m.content for batch in captured for m in batch
        if isinstance(m, SystemMessage)
    ]
    assert system_msgs, "未捕获到任何真实 LLM 调用的 SystemMessage"
    # 每条 SystemMessage 都应以冻结主体为前缀（本节点主体即全部 system prompt）。
    for content in system_msgs:
        assert content == body, "真实链路 SystemMessage 与冻结主体不一致（Prompt Cache 前缀破裂）"


# ============== 节点级真实链路（完整 resource_scout 真跑） ==============


@skip_if_no_llm
def test_e2e_resource_scout_real_contract(scout_state_and_update):
    """真实 resource_scout 跑一篇论文，断言输出契约（不 hard-code 具体仓库内容）。"""
    _, update = scout_state_and_update
    ri = update["resource_info"]
    assert ri["resource_strategy"] in ("use_repo", "hybrid", "from_scratch")
    assert isinstance(ri["repos"], list)
    assert isinstance(ri["external_resources"], list)
    # selected_repo 与 repos 一致性：有仓库时必有 selected，无仓库时 selected 为 None。
    if ri["repos"]:
        assert ri["selected_repo"] is not None
        for r in ri["repos"]:
            assert set(r.keys()), "RepoInfo 非空"
            assert "url" in r and "quality_score" in r
    else:
        assert ri["selected_repo"] is None
        assert ri["resource_strategy"] == "from_scratch"
    assert update["current_step"] == "resource_scout"


@skip_if_no_llm
def test_e2e_resource_scout_no_fatal_exception(scout_state_and_update):
    """真实链路全程不抛致命异常：降级路径也只写 degraded NodeError，不 raise。"""
    _, update = scout_state_and_update
    # node_errors 中本节点条目（若有）必须是 degraded 类，而非 permanent。
    for e in update.get("node_errors", []):
        if e.get("node_name") == "resource_scout":
            assert e.get("error_type") == "degraded"


@skip_if_no_llm
def test_e2e_resource_scout_degraded_consistency(scout_state_and_update):
    """from_scratch 与 degraded_nodes 标记一致：repos 空 <=> from_scratch <=> 进 degraded。"""
    _, update = scout_state_and_update
    ri = update["resource_info"]
    if not ri["repos"]:
        assert ri["resource_strategy"] == "from_scratch"
        assert "resource_scout" in update.get("degraded_nodes", [])
