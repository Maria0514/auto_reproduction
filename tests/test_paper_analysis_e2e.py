"""C2 - paper_analysis ReAct agent 真实端到端 pytest（依赖外部 LLM + deepxiv API）。

测试论文：
- 主靶：arXiv:2405.14831 (HippoRAG)——已被 C1 e2e 验证可访问，CS 领域、章节齐全
- 副靶（仅 Prompt Cache 对比用）：arXiv:2402.17764 (BitNet)——同领域、不同论文

运行方式：
    LLM_API_KEY=... DEEPXIV_TOKEN=... pytest tests/test_paper_analysis_e2e.py -m e2e -v -s

任一凭证缺失则全部跳过。pytest -m e2e 选择性运行。

设计要点（与 task brief 对齐）：
1. 单一 paper_intake 输出在 module scope fixture 内只跑一次，下游用例复用，省 token。
2. Prompt Cache 用例用 ChatOpenAI.invoke 真实链路 hook 截取两篇论文的 SystemMessage，
   断言主体字节级一致——本任务最高 ROI。
3. 工具序列化回归验证：检查 paper_analysis 至少调用过 get_paper_structure / read_section
   工具，且工具结果能被 extract_last_tool_result 正确解析（防 BUG-S1-02 类回归）。
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytest
from langchain_core.messages import BaseMessage, SystemMessage, ToolMessage

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import (  # noqa: E402
    DEFAULT_LLM_MAX_TOKENS,
    DEFAULT_LLM_TEMPERATURE,
    REACT_MAX_ROUNDS_PAPER_ANALYSIS,
    get_deepxiv_token,
    get_llm_api_key,
    get_llm_base_url,
    get_llm_model,
)
from core.nodes.paper_analysis import (  # noqa: E402
    NODE_NAME as ANALYSIS_NODE_NAME,
    _ANALYSIS_SYSTEM_PROMPT_BODY,
    _LANGUAGE_POLICY_SECTION,
    paper_analysis,
)
from core.nodes.paper_intake import paper_intake  # noqa: E402
from core.react_base import extract_last_tool_result  # noqa: E402
from core.state import GlobalState, LLMConfig, create_initial_state  # noqa: E402


# ============== 测试常量 ==============

PRIMARY_ARXIV_ID = "2405.14831"          # HippoRAG，作为主路径靶论文
SECONDARY_ARXIV_ID = "2402.17764"        # BitNet，仅供 Prompt Cache 对比

# Prompt Cache 主体分隔符（与 _build_analysis_system_prompt 完全一致）
SYSTEM_PROMPT_SEPARATOR = "\n--- 当前论文上下文 ---\n"

pytestmark = pytest.mark.e2e


def _has_credentials() -> bool:
    return bool(get_llm_api_key()) and bool(get_deepxiv_token())


skip_if_no_creds = pytest.mark.skipif(
    not _has_credentials(),
    reason="缺少 LLM_API_KEY 或 DEEPXIV_TOKEN 环境变量",
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
def primary_state_with_meta(llm_config) -> GlobalState:
    """跑一次 paper_intake，缓存 state（含 paper_meta）供下游用例复用。

    省 token 设计：所有需要真实 paper_meta 的下游用例都从这里取，不要重复跑 intake。
    """
    if not _has_credentials():
        pytest.skip("缺少凭证")

    state = create_initial_state(user_input=PRIMARY_ARXIV_ID, llm_config=llm_config)
    intake_update = paper_intake(state)

    assert intake_update.get("error") is None, (
        f"paper_intake 失败，无法继续 paper_analysis e2e：{intake_update}"
    )
    pm = intake_update.get("paper_meta")
    assert pm and pm.get("arxiv_id") == PRIMARY_ARXIV_ID, (
        f"paper_intake 未返回有效 paper_meta：{intake_update}"
    )

    # 把 intake 的 update 合并回 state，得到下游用 state（不含 paper_analysis）
    merged: Dict[str, Any] = dict(state)
    merged.update(intake_update)
    return merged  # type: ignore[return-value]


@pytest.fixture(scope="module")
def primary_analysis_result(primary_state_with_meta) -> Tuple[GlobalState, Dict[str, Any]]:
    """跑一次 paper_analysis（主靶论文）并缓存结果。

    返回 (state_in, analysis_update)；T-PA-E2E-01/03/05/06 都用这一份。
    """
    update = paper_analysis(primary_state_with_meta)
    return primary_state_with_meta, update


# ============== 工具：从真实 LLM 调用历史捕获 SystemMessage ==============


def _patch_chatopenai_capture_system(monkeypatch, captured: List[List[BaseMessage]]):
    """劫持 ChatOpenAI.invoke / .ainvoke 记录每次调用时的 messages 副本。

    设计：调用原始实现完成真实 LLM 请求；只把 messages 拷一份到 captured。
    不阻断、不改写，只观察——避免污染真实链路结果。

    bind_tools(...) 返回的也是 ChatOpenAI 包装，所以 patch 原类即可生效。
    """
    from langchain_openai import ChatOpenAI

    original_invoke = ChatOpenAI.invoke

    def wrapped_invoke(self, input, *args, **kwargs):  # noqa: A002
        # input 可能是 messages list 或 ChatPromptValue 等；只在 list 时记录
        if isinstance(input, list):
            captured.append(list(input))
        return original_invoke(self, input, *args, **kwargs)

    monkeypatch.setattr(ChatOpenAI, "invoke", wrapped_invoke)


def _extract_first_system_message(messages: List[BaseMessage]) -> Optional[SystemMessage]:
    for m in messages:
        if isinstance(m, SystemMessage):
            return m
    return None


def _split_system_prompt_body(content: str) -> Tuple[str, str]:
    """按 paper_analysis 的尾部分隔符切分；找不到时整段当作 body，tail 空串。"""
    if SYSTEM_PROMPT_SEPARATOR in content:
        body, tail = content.split(SYSTEM_PROMPT_SEPARATOR, 1)
        return body, tail
    return content, ""


# ============== 通用断言 helpers ==============


def _assert_analysis_basics(update: Dict[str, Any]) -> None:
    """T-PA-E2E-01 主断言：基本路径 paper_analysis 契约符合 PRD / architecture。"""
    assert update.get("error") is None, (
        f"paper_analysis 返回 error：{update.get('error')}; "
        f"node_errors={update.get('node_errors')}"
    )
    assert update.get("current_step") == ANALYSIS_NODE_NAME, (
        f"current_step 不符：{update.get('current_step')}"
    )

    pa = update.get("paper_analysis")
    assert pa, f"paper_analysis 未填充：{update}"

    # method_summary 非空
    assert pa.get("method_summary"), (
        f"method_summary 为空（核心字段）：{pa}"
    )
    # datasets 或 metrics 至少一个非空
    assert pa.get("datasets") or pa.get("metrics"), (
        f"datasets / metrics 全空（核心字段全缺失）：{pa}"
    )
    # sections_read 非空
    assert pa.get("sections_read"), (
        f"sections_read 为空（核心字段）：{pa}"
    )


# ============== T-PA-E2E-02 ==============


def test_e2e_paper_meta_none_short_circuit(llm_config):
    """T-PA-E2E-02：state 无 paper_meta 时前置校验路径短路返回 error。

    不依赖 LLM/deepxiv 真实请求（前置校验路径），但保留 e2e 标签以便统一管理；
    凭证缺失时同样跳过（与其他用例运行模式一致）。
    """
    if not _has_credentials():
        pytest.skip("缺少凭证")
    state = create_initial_state(user_input=PRIMARY_ARXIV_ID, llm_config=llm_config)
    # 明确不设 paper_meta（create_initial_state 默认就是 None / 不存在）
    state["paper_meta"] = None  # type: ignore[typeddict-item]
    update = paper_analysis(state)

    assert update.get("error"), f"应返回 error：{update}"
    assert update.get("current_step") == ANALYSIS_NODE_NAME
    ne = update.get("node_errors") or []
    assert any(
        e.get("node_name") == ANALYSIS_NODE_NAME and e.get("error_type") == "permanent"
        for e in ne
    ), f"node_errors 未含 paper_analysis/permanent 记录：{ne}"
    # 前置校验路径不应耗预算（map_result 直接返回，未进入 react wrapper）
    # paper_analysis 函数在 paper_meta 为 None 时直接 return，不会扣预算
    # 这里不强断言 retry_budget_remaining，因为前置返回值不含该字段


# ============== T-PA-E2E-01 ==============


@skip_if_no_creds
def test_e2e_basic_path_full_pipeline(primary_analysis_result):
    """T-PA-E2E-01：真实链路跑通 paper_analysis，核心字段齐全。"""
    _state, update = primary_analysis_result
    _assert_analysis_basics(update)


# ============== T-PA-E2E-03 ==============


@skip_if_no_creds
def test_e2e_react_actually_used_tools(primary_analysis_result):
    """T-PA-E2E-03：验证 ReAct agent 真的调用过 paper_analysis 工具，
    且 ToolMessage 序列化能被 extract_last_tool_result 正确解析（防 BUG-S1-02 类回归）。

    限制：paper_analysis 当前 wrapper 不会把子图 final messages 透传给调用方，
    因此这里通过两种方式间接验证：
    1) sections_read 字段非空 —— 隐含至少 read_section / structure 工具被成功调用。
    2) 直接调用 deepxiv_tools 的 get_paper_structure_tool() 工厂，模拟一次工具结果
       序列化 + extract_last_tool_result 解析往返；若 BUG-S1-02 回归（str(dict) 写
       ToolMessage），这里会失败。
    """
    _state, update = primary_analysis_result
    pa = update.get("paper_analysis") or {}

    # 1) sections_read 非空 → 隐含至少一次成功 read_section 或 get_full_paper 兜底
    assert pa.get("sections_read"), (
        f"sections_read 为空意味着 ReAct agent 在工具调用上失败：{pa}"
    )

    # 2) 工具序列化往返：构造一条 ToolMessage 模拟真实链路写入，验证 extract_last_tool_result
    #    在当前 deepxiv_tools._serialize 实现下能解析回 dict（这是 BUG-S1-02 修复的核心契约）
    from core.tools.deepxiv_tools import get_paper_structure_tool
    from core.react_base import _stringify_tool_result, _truncate_tool_result

    tool = get_paper_structure_tool()
    raw = tool.invoke({"arxiv_id": PRIMARY_ARXIV_ID})
    # 模拟 react_base.tool_executor_node 的 ToolMessage content 构造路径
    content = _truncate_tool_result(_stringify_tool_result(raw))
    msg = ToolMessage(content=content, tool_call_id="t1", name="get_paper_structure")
    parsed = extract_last_tool_result([msg], "get_paper_structure")
    assert isinstance(parsed, dict) and parsed, (
        f"extract_last_tool_result 无法解析工具结果，可能 BUG-S1-02 回归。content prefix="
        f"{content[:200]!r}"
    )
    # structure 工具的真实返回应含 sections 字段或 token_count
    assert "sections" in parsed or "token_count" in parsed, (
        f"解析后字典缺少结构契约字段：keys={list(parsed.keys())[:10]}"
    )


# ============== T-PA-E2E-04（最高 ROI：Prompt Cache 真实链路验证）==============


@skip_if_no_creds
def test_e2e_prompt_cache_system_prompt_byte_identical(
    llm_config, primary_state_with_meta, monkeypatch
):
    """T-PA-E2E-04：对两篇不同论文跑 paper_analysis，截取真实 LLM 调用的 SystemMessage，
    断言主体（去掉尾部 paper context 段落）字节级一致——验证 Prompt Cache 治理在真实链路生效。

    实现：monkeypatch ChatOpenAI.invoke 抓取每次真实 LLM 调用的 messages 副本（不阻断真实调用）。
    跑完两次 paper_analysis 后，分别取每次最早一次 LLM 调用的首条 SystemMessage 比较。

    省 token：副靶论文不复用 paper_intake，直接构造一个最小合法 paper_meta 注入 GlobalState。
    """
    captured: List[List[BaseMessage]] = []
    _patch_chatopenai_capture_system(monkeypatch, captured)

    # ---- 第一篇（主靶，已有 paper_meta，复用 primary_state_with_meta）----
    # 注意：primary_state_with_meta 是 module scope，但 _patch_chatopenai_capture_system
    # 是 function scope。如果 primary_state_with_meta 早于本测试时已经跑过 intake，那次
    # intake 的 LLM 调用不会被 captured；我们这里跑 paper_analysis 才是被 hook 的部分。
    captured.clear()
    update_a = paper_analysis(primary_state_with_meta)
    msgs_a = list(captured)
    captured.clear()

    # ---- 第二篇（副靶）：构造最小 paper_meta，跳过 paper_intake ----
    secondary_meta = {
        "arxiv_id": SECONDARY_ARXIV_ID,
        "title": "BitNet: Scaling 1-bit Transformers for Large Language Models",
        "authors": ["Hongyu Wang", "Shuming Ma"],
        "abstract": (
            "We introduce BitNet, a scalable and stable 1-bit Transformer architecture."
        ),
        "categories": ["cs.CL", "cs.LG"],
        "tldr": None,
        "keywords": None,
        "citation_count": None,
        "github_url": None,
        "publish_date": "2024-02-27",
        "pdf_url": None,
    }
    secondary_state = create_initial_state(
        user_input=SECONDARY_ARXIV_ID, llm_config=llm_config
    )
    secondary_state["paper_meta"] = secondary_meta  # type: ignore[typeddict-item]

    update_b = paper_analysis(secondary_state)
    msgs_b = list(captured)

    # 两次 paper_analysis 都应至少触发一次 LLM 调用
    assert msgs_a, "第一篇 paper_analysis 未触发任何 LLM invoke"
    assert msgs_b, "第二篇 paper_analysis 未触发任何 LLM invoke"

    # 取每次最早一次 LLM 调用的 SystemMessage（reasoning_node 首轮 input）
    sys_a = _extract_first_system_message(msgs_a[0])
    sys_b = _extract_first_system_message(msgs_b[0])
    assert sys_a is not None, "第一次 LLM invoke 无 SystemMessage"
    assert sys_b is not None, "第二次 LLM invoke 无 SystemMessage"

    body_a, tail_a = _split_system_prompt_body(sys_a.content)
    body_b, tail_b = _split_system_prompt_body(sys_b.content)

    # 1) 必须存在分隔符（即 _build_analysis_system_prompt 的尾部上下文段落机制生效）
    assert tail_a, (
        f"第一篇 system prompt 未发现尾部分隔符 {SYSTEM_PROMPT_SEPARATOR!r}，"
        f"Prompt Cache 前缀治理未生效"
    )
    assert tail_b, (
        f"第二篇 system prompt 未发现尾部分隔符 {SYSTEM_PROMPT_SEPARATOR!r}"
    )
    # 2) 主体字节级一致（核心断言）
    assert body_a == body_b, (
        f"两篇论文 system prompt 主体不一致，Prompt Cache 前缀治理破坏。\n"
        f"len_a={len(body_a)}, len_b={len(body_b)}\n"
        f"diff position: {next((i for i in range(min(len(body_a), len(body_b))) if body_a[i] != body_b[i]), 'len-mismatch')}"
    )
    # 3) 主体与导出常量字节级一致（防止主体被偷偷改成动态拼接）。
    #    sp2(B1) 起主体前缀 = _ANALYSIS_SYSTEM_PROMPT_BODY + "\n" + _LANGUAGE_POLICY_SECTION
    #    （静态语言策略段，对所有论文字节级一致，不破坏 Prompt Cache 前缀稳定性）。
    expected_body = _ANALYSIS_SYSTEM_PROMPT_BODY + "\n" + _LANGUAGE_POLICY_SECTION
    assert body_a.rstrip("\n") == expected_body.rstrip("\n"), (
        "主体与 (_ANALYSIS_SYSTEM_PROMPT_BODY + _LANGUAGE_POLICY_SECTION) 不一致；"
        "任何修改都应同步常量"
    )
    # 4) 主体不得包含任何论文级动态变量（防回归）
    for needle in [
        PRIMARY_ARXIV_ID, SECONDARY_ARXIV_ID,
        "HippoRAG", "BitNet",
        "Hongyu Wang", "Vaswani",
    ]:
        assert needle not in body_a, (
            f"主体含动态变量 {needle!r}，破坏前缀稳定（Prompt Cache 会失效）"
        )

    # 5) 尾部段落必须真包含 arxiv_id（否则等于尾部段落空转）
    assert PRIMARY_ARXIV_ID in tail_a, (
        f"第一篇尾部段落未包含 arxiv_id: {tail_a[:200]!r}"
    )
    assert SECONDARY_ARXIV_ID in tail_b, (
        f"第二篇尾部段落未包含 arxiv_id: {tail_b[:200]!r}"
    )

    # 顺便验证两次 update 都不是直接错误（容忍 degraded，但不应彻底 error）
    assert update_a.get("error") is None or update_a.get("paper_analysis"), (
        f"第一篇 paper_analysis error 且无任何结果：{update_a}"
    )
    assert update_b.get("error") is None or update_b.get("paper_analysis"), (
        f"第二篇 paper_analysis error 且无任何结果：{update_b}"
    )


# ============== T-PA-E2E-05 ==============


@skip_if_no_creds
def test_e2e_budget_not_exhausted_in_normal_paper(primary_analysis_result):
    """T-PA-E2E-05：max_rounds=12 在 HippoRAG 小论文上是否够用，验证未触发 force_finish。"""
    state, update = primary_analysis_result

    before_budget = state.get("retry_budget_remaining", 50)
    after_budget = update.get("retry_budget_remaining", before_budget)
    rounds_used = before_budget - after_budget

    # 正常路径应在 6~10 轮完成（dev-plan 预期），不应耗满 12 轮
    assert rounds_used > 0, (
        f"retry_budget_remaining 未扣减，未实际进入 ReAct：before={before_budget}, "
        f"after={after_budget}"
    )
    assert rounds_used < REACT_MAX_ROUNDS_PAPER_ANALYSIS, (
        f"ReAct 轮次耗尽到 max_rounds={REACT_MAX_ROUNDS_PAPER_ANALYSIS}，"
        f"实际 rounds_used={rounds_used}；说明 force_finish 被触发，max_rounds 可能偏紧"
    )


# ============== T-PA-E2E-06 ==============


@skip_if_no_creds
def test_e2e_node_errors_clean_on_success(primary_analysis_result):
    """T-PA-E2E-06：成功路径不应在 node_errors 中写 paper_analysis/permanent 记录。

    允许 degraded（如个别字段缺失），但不应 permanent。
    """
    _state, update = primary_analysis_result
    _assert_analysis_basics(update)

    ne = update.get("node_errors") or []
    permanent_records = [
        e for e in ne
        if e.get("node_name") == ANALYSIS_NODE_NAME
        and e.get("error_type") == "permanent"
    ]
    assert not permanent_records, (
        f"成功路径不应有 paper_analysis/permanent NodeError：{permanent_records}"
    )
