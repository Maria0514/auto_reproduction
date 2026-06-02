"""Sprint 2 任务 B1 自测：paper_intake / paper_analysis 节点追加式扩展。

覆盖 dev-plan §B1 CP-B1-1 ~ CP-B1-10（程序化可验证的 10 个 checkpoint）：
    - Schema 扩展（*_zh / *_en Optional 字段 + 主字段 description 含"中文"）；
    - _LANGUAGE_POLICY_SECTION(_INTAKE) module-level 常量 + 字节级幂等；
    - _map_*_result backfill 兜底（漏写时回退 + degraded + 非静默 WARNING）；
    - 严禁二次 LLM 翻译调用；
    - sp1 既有 paper_intake / paper_analysis 单测 CP1~CP11 仍通过（CP-B1-10）。

风格：pytest 标准函数（参考 tests/test_sprint2_a1.py / a4），无真实 LLM、无 main()。
访问节点模块属性统一用 importlib.import_module，避免 core/nodes/__init__.py 的
callable export 遮蔽子模块（known bug #6）。
"""

from __future__ import annotations

import importlib
import logging
import re

import pytest

pi = importlib.import_module("core.nodes.paper_intake")
pa = importlib.import_module("core.nodes.paper_analysis")


# ========== CP-B1-1：PAPER_META_SCHEMA 含 3 个 *_zh 字段且不在 required ==========


def test_cp_b1_1_paper_meta_schema_zh_fields() -> None:
    """CP-B1-1: PAPER_META_SCHEMA.properties 含 title_zh / abstract_zh / tldr_zh，
    且均不在 required 数组内。"""
    schema = pi.PAPER_META_SCHEMA
    props = schema["properties"]
    required = schema["required"]
    for field in ("title_zh", "abstract_zh", "tldr_zh"):
        assert field in props, f"PAPER_META_SCHEMA.properties 缺失 {field}"
        assert field not in required, f"{field} 不应进入 required 数组（Optional 备份字段）"


# ========== CP-B1-2：PAPER_ANALYSIS_SCHEMA 含 2 个 *_en 字段且不在 required ==========
#            + method_summary / hardware_requirements description 含"中文"


def test_cp_b1_2_paper_analysis_schema_en_fields_and_zh_description() -> None:
    """CP-B1-2: PAPER_ANALYSIS_SCHEMA.properties 含 method_summary_en /
    hardware_requirements_en 且不在 required；主字段 description 含"中文"。"""
    schema = pa.PAPER_ANALYSIS_SCHEMA
    props = schema["properties"]
    required = schema["required"]
    for field in ("method_summary_en", "hardware_requirements_en"):
        assert field in props, f"PAPER_ANALYSIS_SCHEMA.properties 缺失 {field}"
        assert field not in required, f"{field} 不应进入 required 数组（Optional 备份字段）"
    # 主字段语义反转：description 必须含"中文"，明确告知 LLM 中文为主字段
    for field in ("method_summary", "hardware_requirements"):
        desc = props[field].get("description", "")
        assert "中文" in desc, (
            f"{field} description 应含\"中文\"标注语义反转，实测：{desc!r}"
        )


# ========== CP-B1-3：_LANGUAGE_POLICY_SECTION 是 module-level 常量且字节级一致 ==========


def test_cp_b1_3_language_policy_section_is_module_constant() -> None:
    """CP-B1-3: _LANGUAGE_POLICY_SECTION 是 module-level 常量，多次以不同 arxiv 调用
    _build_analysis_system_prompt 时该段落字节级一致（即不随论文变化）。"""
    # module-level 常量存在且为 str
    assert isinstance(pa._LANGUAGE_POLICY_SECTION, str)
    assert isinstance(pi._LANGUAGE_POLICY_SECTION_INTAKE, str)
    assert pa._LANGUAGE_POLICY_SECTION.startswith("--- 输出语言策略 ---")
    assert pi._LANGUAGE_POLICY_SECTION_INTAKE.startswith("--- 输出语言策略 ---")

    ctx_a = {"arxiv_id": "2409.05591", "paper_meta": {"arxiv_id": "2409.05591",
             "title": "A", "abstract": "a", "categories": ["cs.CL"]}}
    ctx_b = {"arxiv_id": "2305.99999", "paper_meta": {"arxiv_id": "2305.99999",
             "title": "B", "abstract": "b", "categories": ["cs.AI"]}}
    p_a = pa._build_analysis_system_prompt(ctx_a)
    p_b = pa._build_analysis_system_prompt(ctx_b)
    # 段落本身在两次组装结果中均原样出现（字节级一致，不被 arxiv 污染）
    assert pa._LANGUAGE_POLICY_SECTION in p_a
    assert pa._LANGUAGE_POLICY_SECTION in p_b

    # 段落内不含任何论文级动态变量（防止误用 f-string）
    for needle in ("2409.05591", "2305.99999", "Attention", "{", "}"):
        assert needle not in pa._LANGUAGE_POLICY_SECTION, (
            f"_LANGUAGE_POLICY_SECTION 含动态/f-string 占位 {needle!r}，破坏字节幂等"
        )
        assert needle not in pi._LANGUAGE_POLICY_SECTION_INTAKE, (
            f"_LANGUAGE_POLICY_SECTION_INTAKE 含动态/f-string 占位 {needle!r}"
        )


# ========== CP-B1-4：_ANALYSIS_SYSTEM_PROMPT_BODY / _INTAKE_SYSTEM_PROMPT 主体冻结 ==========


def test_cp_b1_4_system_prompt_body_frozen() -> None:
    """CP-B1-4: 主体常量未被 sp2 改造修改——验证组装结果以主体原样为前缀，
    且主体内不含语言策略段落（语言策略是追加段落，不是主体的一部分）。"""
    # analysis：组装结果必须以主体原样开头
    ctx = {"arxiv_id": "x", "paper_meta": {"arxiv_id": "x", "title": "t"}}
    assert pa._build_analysis_system_prompt(ctx).startswith(
        pa._ANALYSIS_SYSTEM_PROMPT_BODY
    ), "analysis 组装结果未以 _ANALYSIS_SYSTEM_PROMPT_BODY 原样开头（主体被破坏）"
    # 语言策略段落不得混入主体常量本身
    assert pa._LANGUAGE_POLICY_SECTION not in pa._ANALYSIS_SYSTEM_PROMPT_BODY, (
        "语言策略段落混入主体常量，破坏主体冻结约束"
    )

    # intake：组装结果必须以主体原样开头
    assert pi._build_intake_system_prompt({}).startswith(pi._INTAKE_SYSTEM_PROMPT), (
        "intake 组装结果未以 _INTAKE_SYSTEM_PROMPT 原样开头（主体被破坏）"
    )
    assert pi._LANGUAGE_POLICY_SECTION_INTAKE not in pi._INTAKE_SYSTEM_PROMPT


# ========== CP-B1-5：主体字节级一致（两篇论文截去尾部上下文后前缀相同） ==========


def test_cp_b1_5_prompt_prefix_byte_identical() -> None:
    """CP-B1-5: 两篇不同论文截取 system prompt，去尾部"--- 当前论文上下文 ---"段落后
    字节级一致；改造后前缀截止到 _LANGUAGE_POLICY_SECTION 末尾也字节级一致。"""
    ctx_a = {"arxiv_id": "2409.05591", "paper_meta": {"arxiv_id": "2409.05591",
             "title": "Attention Is All You Need", "authors": ["Vaswani"],
             "abstract": "We propose the Transformer.", "categories": ["cs.CL"]}}
    ctx_b = {"arxiv_id": "2305.99999", "paper_meta": {"arxiv_id": "2305.99999",
             "title": "Completely Different Paper", "authors": ["Doe"],
             "abstract": "Another abstract.", "categories": ["cs.AI", "cs.LG"]}}
    p_a = pa._build_analysis_system_prompt(ctx_a)
    p_b = pa._build_analysis_system_prompt(ctx_b)

    # 1) 整段不同（尾部上下文不同）
    assert p_a != p_b, "两次 prompt 完全相同？尾部上下文未生效"

    # 2) 截去尾部独立段落后主体字节级一致
    separator = "\n--- 当前论文上下文 ---\n"
    assert separator in p_a and separator in p_b, "未发现尾部分隔符"
    body_a = p_a.split(separator, 1)[0]
    body_b = p_b.split(separator, 1)[0]
    assert body_a == body_b, "主体字节级不一致，破坏 Prompt Cache 前缀稳定"

    # 3) 前缀 = 主体 + 语言策略段落（字节级一致）
    expected_prefix = (
        pa._ANALYSIS_SYSTEM_PROMPT_BODY + "\n" + pa._LANGUAGE_POLICY_SECTION
    )
    assert body_a.rstrip("\n") == expected_prefix.rstrip("\n"), (
        "前缀与 BODY + _LANGUAGE_POLICY_SECTION 不一致"
    )

    # 4) intake 同理：无动态尾部，主体 + 语言策略段落，两次调用字节级一致
    i_a = pi._build_intake_system_prompt({"user_input": "abc"})
    i_b = pi._build_intake_system_prompt({"user_input": "xyz-different"})
    assert i_a == i_b, "intake system prompt 不应随 context 变化（动态走 HumanMessage）"
    assert i_a == pi._INTAKE_SYSTEM_PROMPT + "\n" + pi._LANGUAGE_POLICY_SECTION_INTAKE


# ========== CP-B1-6：LLM 漏写 title_zh → 回退 title + degraded + WARNING ==========


def _intake_state() -> dict:
    return {"user_input": "2409.05591", "node_errors": [], "degraded_nodes": []}


def test_cp_b1_6_intake_backfill_zh_with_warning(caplog) -> None:
    """CP-B1-6: LLM 漏写 title_zh，_map_intake_result 回退 title_zh=title，
    degraded_nodes 含 paper_intake，node_errors 含 degraded，WARNING 非静默。"""
    result = {
        "arxiv_id": "2409.05591",
        "title": "Attention Is All You Need",
        "authors": ["Vaswani"],
        "abstract": "We propose the Transformer.",
        "categories": ["cs.CL"],
        "tldr": "Transformer",
        # 漏写 title_zh / abstract_zh / tldr_zh
    }
    with caplog.at_level(logging.WARNING, logger="core.nodes.paper_intake"):
        update = pi._map_intake_result(result, _intake_state())

    meta = update["paper_meta"]
    assert meta["title_zh"] == "Attention Is All You Need", "title_zh 未回退为 title"
    assert "paper_intake" in update["degraded_nodes"]
    degraded_errs = [e for e in update["node_errors"] if e["error_type"] == "degraded"]
    assert len(degraded_errs) == 1, f"应有 1 条 degraded NodeError，实测 {len(degraded_errs)}"
    # WARNING 非静默
    warns = [r for r in caplog.records
             if r.levelno == logging.WARNING and "_zh" in r.getMessage()]
    assert len(warns) >= 1, "未打 *_zh 回退 WARNING（静默吞错，违反 BUG-S1-02 治理范式）"


# ========== CP-B1-7：LLM 漏写 method_summary_en → 回退 + degraded + WARNING ==========


def _analysis_state() -> dict:
    return {"node_errors": [], "degraded_nodes": []}


def test_cp_b1_7_analysis_backfill_en_with_warning(caplog) -> None:
    """CP-B1-7: LLM 漏写 method_summary_en，_map_analysis_result 回退
    method_summary_en=method_summary，degraded + WARNING 非静默。"""
    result = {
        "method_summary": "基于多头自注意力的 Transformer 架构。",
        "datasets": ["WMT 2014 EN-DE"],
        "metrics": ["BLEU"],
        "hyperparams": {"d_model": 512},
        "hardware_requirements": "8x P100 GPU",
        "sections_read": ["Method", "Experiments"],
        "analysis_notes": "正常路径",
        # 漏写 method_summary_en / hardware_requirements_en
    }
    with caplog.at_level(logging.WARNING, logger="core.nodes.paper_analysis"):
        update = pa._map_analysis_result(result, _analysis_state())

    analysis = update["paper_analysis"]
    assert analysis["method_summary_en"] == "基于多头自注意力的 Transformer 架构。", (
        "method_summary_en 未回退为 method_summary"
    )
    assert analysis["hardware_requirements_en"] == "8x P100 GPU"
    assert "paper_analysis" in update["degraded_nodes"]
    degraded_errs = [e for e in update["node_errors"]
                     if e["error_type"] == "degraded"
                     and "_en" in e["error_message"]]
    assert len(degraded_errs) == 1, "应有 1 条 *_en degraded NodeError"
    warns = [r for r in caplog.records
             if r.levelno == logging.WARNING and "_en" in r.getMessage()]
    assert len(warns) >= 1, "未打 *_en 回退 WARNING（静默吞错）"


# ========== CP-B1-8：同时漏写多个 *_zh → 全部回退、degraded 去重一次、NodeError 一条 ==========


def test_cp_b1_8_intake_backfill_multiple_zh_dedup() -> None:
    """CP-B1-8: 同时漏写 title_zh / abstract_zh / tldr_zh，全部回退；
    degraded_nodes 仅追加一次（去重）；degraded NodeError 仅一条。"""
    result = {
        "arxiv_id": "2409.05591",
        "title": "T",
        "authors": ["A"],
        "abstract": "ABS",
        "categories": ["cs.CL"],
        "tldr": "TLDR",
        # 三个 *_zh 全漏写
    }
    update = pi._map_intake_result(result, _intake_state())
    meta = update["paper_meta"]
    assert meta["title_zh"] == "T"
    assert meta["abstract_zh"] == "ABS"
    assert meta["tldr_zh"] == "TLDR"
    # 去重：degraded_nodes 中 paper_intake 仅出现一次
    assert update["degraded_nodes"].count("paper_intake") == 1, (
        f"degraded_nodes 未去重：{update['degraded_nodes']}"
    )
    degraded_errs = [e for e in update["node_errors"] if e["error_type"] == "degraded"]
    assert len(degraded_errs) == 1, (
        f"多字段漏写应只写一条 degraded NodeError，实测 {len(degraded_errs)}"
    )


def test_cp_b1_8_no_backfill_when_zh_present() -> None:
    """CP-B1-8 反向：LLM 已给出全部 *_zh 时不回退、不进 degraded、不写 NodeError。"""
    result = {
        "arxiv_id": "2409.05591",
        "title": "T", "authors": ["A"], "abstract": "ABS", "categories": ["cs.CL"],
        "tldr": "TLDR",
        "title_zh": "标题", "abstract_zh": "摘要", "tldr_zh": "速览",
    }
    update = pi._map_intake_result(result, _intake_state())
    meta = update["paper_meta"]
    assert meta["title_zh"] == "标题"
    assert "paper_intake" not in update["degraded_nodes"]
    assert all(e["error_type"] != "degraded" for e in update["node_errors"])


# ========== CP-B1-9：严禁二次 LLM 翻译调用 ==========


def test_cp_b1_9_no_secondary_llm_translation_call() -> None:
    """CP-B1-9: backfill 路径不引入 create_llm / llm.invoke 二次调用。

    grep 两个 backfill 函数 + _map_*_result 源码，确认无 create_llm / llm.invoke /
    translate 等二次 LLM 调用关键字（ReAct 子图内主流程不在这些函数内）。
    """
    import inspect

    sources = [
        inspect.getsource(pi._backfill_zh_fields),
        inspect.getsource(pi._map_intake_result),
        inspect.getsource(pa._backfill_en_fields),
        inspect.getsource(pa._map_analysis_result),
    ]
    forbidden = ("create_llm", "llm.invoke", ".invoke(", "ChatOpenAI", "translate(")
    for src in sources:
        # 去掉注释行后再检查，避免文档字符串里的"翻译"误伤
        code_lines = [ln for ln in src.splitlines() if not ln.strip().startswith("#")]
        code = "\n".join(code_lines)
        for kw in forbidden:
            assert kw not in code, (
                f"backfill/map 路径含二次 LLM 调用关键字 {kw!r}，违反 PRD §4.7.4 硬约束"
            )


# ========== 测试工程师补强（B1 独立验收）：边界用例 ==========
# 由 @测试工程师代理 在 2026-06-02 B1 独立验收时追加，补 dev-plan CP-B1-7/8/9
# 在 paper_analysis 侧的覆盖缺口（反向用例 / 部分漏写 / degraded 叠加去重 /
# 工具回填函数也禁二次 LLM）。原 12 个 CP 用例不动。


def test_aux_b1_analysis_no_backfill_when_en_present() -> None:
    """CP-B1-7 反向：LLM 已给出全部 *_en 时不回退、不进 degraded、不写 degraded NodeError。

    对齐 intake 侧已有的 test_cp_b1_8_no_backfill_when_zh_present，补 analysis 侧空白。
    """
    result = {
        "method_summary": "基于多头自注意力的 Transformer 架构。",
        "method_summary_en": "Transformer based on multi-head self-attention.",
        "datasets": ["WMT 2014 EN-DE"],
        "metrics": ["BLEU"],
        "hyperparams": {"d_model": 512},
        "hardware_requirements": "8x P100 GPU",
        "hardware_requirements_en": "8x P100 GPU",
        "sections_read": ["Method", "Experiments"],
        "analysis_notes": "clean path",
    }
    update = pa._map_analysis_result(result, _analysis_state())
    analysis = update["paper_analysis"]
    # 原值保留，未被中文主字段覆盖
    assert analysis["method_summary_en"] == "Transformer based on multi-head self-attention."
    assert analysis["hardware_requirements_en"] == "8x P100 GPU"
    assert "paper_analysis" not in update["degraded_nodes"]
    assert all(e["error_type"] != "degraded" for e in update["node_errors"]), (
        "clean path（含 *_en）不应产生任何 degraded NodeError"
    )


def test_aux_b1_analysis_partial_en_omission() -> None:
    """部分漏写：只漏 method_summary_en（hardware_requirements_en 已给）。

    断言只回退缺失项、保留已给项、仍标 degraded（漏写即降级符合 CP-B1-7 语义）。
    """
    result = {
        "method_summary": "中文方法概述。",
        # 漏 method_summary_en
        "hardware_requirements": "GPU",
        "hardware_requirements_en": "GPU (en, 用户给定)",
        "datasets": ["D"],
        "metrics": ["M"],
        "hyperparams": {},
        "sections_read": ["Method"],
        "analysis_notes": "partial",
    }
    update = pa._map_analysis_result(result, _analysis_state())
    analysis = update["paper_analysis"]
    assert analysis["method_summary_en"] == "中文方法概述。", "缺失项未回退中文主字段"
    assert analysis["hardware_requirements_en"] == "GPU (en, 用户给定)", "已给项被错误覆盖"
    assert "paper_analysis" in update["degraded_nodes"], "部分漏写仍应标 degraded"


def test_aux_b1_en_omission_and_missing_core_dedup() -> None:
    """*_en 漏写 + 核心字段缺失（sections_read 空）同时触发：

    degraded_nodes 去重为单次出现；两条 degraded NodeError 语义不同应分别记录
    （一条 *_en 回退、一条 missing core fields），不被错误合并。
    """
    result = {
        "method_summary": "中文方法概述。",
        # 漏 *_en
        "datasets": ["D"],
        "metrics": ["M"],
        "hyperparams": {},
        "hardware_requirements": "GPU",
        "sections_read": [],  # 核心字段缺失
        "analysis_notes": "",
    }
    update = pa._map_analysis_result(result, _analysis_state())
    # degraded_nodes 去重：paper_analysis 只出现一次
    assert update["degraded_nodes"].count("paper_analysis") == 1, (
        f"degraded_nodes 未去重：{update['degraded_nodes']}"
    )
    degraded_errs = [e for e in update["node_errors"] if e["error_type"] == "degraded"]
    assert len(degraded_errs) == 2, (
        f"应有 2 条语义不同的 degraded NodeError（*_en 回退 + missing core），"
        f"实测 {len(degraded_errs)}：{[e['error_message'] for e in degraded_errs]}"
    )
    msgs = " ".join(e["error_message"] for e in degraded_errs)
    assert "_en" in msgs and "缺失" in msgs, "两条 degraded 应分别覆盖 *_en 回退与 missing"


def test_aux_b1_tool_backfill_funcs_also_no_secondary_llm() -> None:
    """CP-B1-9 扩展：sp1 既有工具回填函数（_backfill_paper_meta_from_tools /
    _backfill_analysis_from_tools）同样在 backfill 路径上，确认其也无二次 LLM 调用。

    防止未来在工具回填里偷偷塞翻译调用绕过 CP-B1-9 的 4 函数扫描。
    """
    import inspect

    sources = [
        inspect.getsource(pi._backfill_paper_meta_from_tools),
        inspect.getsource(pa._backfill_analysis_from_tools),
    ]
    forbidden = ("create_llm", "llm.invoke", ".invoke(", "ChatOpenAI", "translate(")
    for src in sources:
        code_lines = [ln for ln in src.splitlines() if not ln.strip().startswith("#")]
        code = "\n".join(code_lines)
        for kw in forbidden:
            assert kw not in code, (
                f"工具回填路径含二次 LLM 调用关键字 {kw!r}，违反 PRD §4.7.4 硬约束"
            )


# ========== 测试工程师深化（第二轮 B1 复验收，2026-06-02）：边界 / 三态 / 顺序交互 ==========
# 由 @测试工程师代理 在 B1 深化测试完善轮追加（在已有 16 用例之上）。聚焦上一轮
# 验收报告标注的缺口：*_zh/*_en 三态（缺键 / None / 空串）、主字段本身缺失时不回退
# 出空串、工具回填与 *_en backfill 的执行顺序交互、degraded 叠加去重的 NodeError 计数、
# prompt cache 极端 context（None / 空 dict / 特殊字符）前缀字节稳定。原 16 用例不动。


# ---- 三态语义：缺键 / 显式 None / 空串 都应触发 *_zh 回退（backfill 条件用 not get()）----


@pytest.mark.parametrize(
    "label, zh_overlay",
    [
        ("missing_keys", {}),  # 完全不写 *_zh 键
        ("explicit_none", {"title_zh": None, "abstract_zh": None, "tldr_zh": None}),
        ("empty_string", {"title_zh": "", "abstract_zh": "", "tldr_zh": ""}),
    ],
)
def test_deep_b1_intake_zh_tristate_all_trigger_backfill(label, zh_overlay) -> None:
    """三态语义：LLM 漏写 *_zh 的三种形态（缺键 / 显式 null / 空串）都应触发回退。

    上一轮只覆盖了"缺键"形态。backfill 条件是 `not meta.get(field)`——空串与 None
    都为 falsy，应一视同仁回退。本用例锁死这一语义，防止未来改成 `field not in meta`
    （只认缺键）而漏掉 LLM 输出空串 / null 的真实形态。
    """
    result = {
        "arxiv_id": "2409.05591", "title": "T", "authors": ["A"],
        "abstract": "ABS", "categories": ["cs.CL"], "tldr": "TLDR",
        **zh_overlay,
    }
    update = pi._map_intake_result(result, _intake_state())
    meta = update["paper_meta"]
    assert meta["title_zh"] == "T", f"[{label}] title_zh 未回退主字段"
    assert meta["abstract_zh"] == "ABS", f"[{label}] abstract_zh 未回退主字段"
    assert meta["tldr_zh"] == "TLDR", f"[{label}] tldr_zh 未回退主字段"
    assert "paper_intake" in update["degraded_nodes"], f"[{label}] 未标 degraded"
    degraded = [e for e in update["node_errors"] if e["error_type"] == "degraded"]
    assert len(degraded) == 1, f"[{label}] 应恰好 1 条 degraded NodeError"


@pytest.mark.parametrize(
    "label, en_overlay",
    [
        ("missing_keys", {}),
        ("explicit_none", {"method_summary_en": None, "hardware_requirements_en": None}),
        ("empty_string", {"method_summary_en": "", "hardware_requirements_en": ""}),
    ],
)
def test_deep_b1_analysis_en_tristate_all_trigger_backfill(label, en_overlay) -> None:
    """三态语义（analysis 侧）：*_en 缺键 / 显式 null / 空串都应触发回退到中文主字段。"""
    result = {
        "method_summary": "中文方法概述。",
        "datasets": ["D"], "metrics": ["M"], "hyperparams": {},
        "hardware_requirements": "GPU 描述",
        "sections_read": ["Method"], "analysis_notes": "n",
        **en_overlay,
    }
    update = pa._map_analysis_result(result, _analysis_state())
    analysis = update["paper_analysis"]
    assert analysis["method_summary_en"] == "中文方法概述。", f"[{label}] method_summary_en 未回退"
    assert analysis["hardware_requirements_en"] == "GPU 描述", f"[{label}] hardware_requirements_en 未回退"
    assert "paper_analysis" in update["degraded_nodes"], f"[{label}] 未标 degraded"


# ---- 主字段本身缺失时：*_zh / *_en 不应被回退成空串（避免制造空字段假象）----


def test_deep_b1_intake_no_backfill_when_main_field_absent() -> None:
    """主字段缺失边角：tldr 主字段本身缺失（None）时，tldr_zh 不应被回退成空串。

    dev-plan 深化重点明确点名此场景。backfill 条件 `not meta.get(field_zh) and
    meta.get(main_field)`——主字段为 falsy 时回退分支短路，tldr_zh 保持 None，
    不制造"看似有中文翻译实则空串"的假象。title / abstract 给了值仍正常回退，
    证明是逐字段判定而非整体开关。
    """
    result = {
        "arxiv_id": "2409.05591", "title": "T", "authors": ["A"],
        "abstract": "ABS", "categories": ["cs.CL"],
        # 无 tldr 主字段，也无 tldr_zh
    }
    update = pi._map_intake_result(result, _intake_state())
    meta = update["paper_meta"]
    # title/abstract 主字段存在 → 正常回退
    assert meta["title_zh"] == "T"
    assert meta["abstract_zh"] == "ABS"
    # tldr 主字段缺失 → tldr_zh 保持 None，绝不回退成 "" 或假值
    assert meta["tldr"] is None, "tldr 主字段应为 None"
    assert meta["tldr_zh"] is None, "tldr 主字段缺失时 tldr_zh 不应被回退成空串/假值"
    # 仍因 title_zh/abstract_zh 回退而 degraded（部分字段触发即降级）
    assert "paper_intake" in update["degraded_nodes"]


def test_deep_b1_analysis_no_backfill_when_main_field_absent() -> None:
    """主字段缺失边角（analysis 侧）：hardware_requirements 主字段为空时，
    hardware_requirements_en 不应被回退成空串。

    构造 method_summary 有值（→ method_summary_en 回退触发 degraded）但
    hardware_requirements 为空：hardware_requirements_en 应保持 None，不被填空。
    """
    result = {
        "method_summary": "中文方法概述。",
        "datasets": ["D"], "metrics": ["M"], "hyperparams": {},
        "hardware_requirements": "",  # 主字段空
        "sections_read": ["Method"], "analysis_notes": "n",
        # 两个 *_en 都漏写
    }
    update = pa._map_analysis_result(result, _analysis_state())
    analysis = update["paper_analysis"]
    assert analysis["method_summary_en"] == "中文方法概述。", "method_summary_en 应回退"
    assert not analysis["hardware_requirements_en"], (
        "hardware_requirements 主字段为空时 hardware_requirements_en 不应被回退成假值"
    )
    assert "paper_analysis" in update["degraded_nodes"]


def test_deep_b1_intake_no_zh_backfill_and_no_degraded_when_all_main_absent() -> None:
    """极端边角：所有 *_zh 主字段都缺失（无 tldr，title/abstract 也为空串构造不出 meta）。

    构造一个 title/abstract 主字段为空串、无 tldr 的结果（仍带 arxiv_id 与有效
    title 以过 permanent 校验，但 abstract/tldr 主字段空）：验证 *_zh 回退是逐字段
    "主字段非空才回退"的精确语义，且不会因为某些字段无可回退源而误标 degraded。
    """
    # title 必须非空（否则 permanent error 短路），abstract/tldr 主字段空
    result = {
        "arxiv_id": "2409.05591", "title": "OnlyTitle", "authors": ["A"],
        "abstract": "", "categories": ["cs.CL"],
        # 无 tldr；无任何 *_zh
    }
    update = pi._map_intake_result(result, _intake_state())
    meta = update["paper_meta"]
    # title 主字段非空 → title_zh 回退；abstract/tldr 主字段空 → 对应 *_zh 不回退
    assert meta["title_zh"] == "OnlyTitle"
    assert not meta["abstract_zh"], "abstract 主字段空时 abstract_zh 不应回退"
    assert not meta["tldr_zh"], "tldr 主字段缺失时 tldr_zh 不应回退"
    # 因 title_zh 回退仍 degraded（至少一项回退即降级）
    assert "paper_intake" in update["degraded_nodes"]


# ---- 执行顺序交互：工具回填(method_summary) → *_en backfill 应跟随工具回填后的值 ----


def _ai_tool_call(section_name: str, call_id: str):
    from langchain_core.messages import AIMessage
    return AIMessage(
        content="",
        tool_calls=[{"name": "read_section", "args": {"section_name": section_name},
                     "id": call_id}],
    )


def _tool_msg(content: str, call_id: str):
    from langchain_core.messages import ToolMessage
    return ToolMessage(content=content, name="read_section", tool_call_id=call_id)


def test_deep_b1_analysis_tool_backfill_then_en_backfill_order() -> None:
    """执行顺序交互（dev-plan 深化重点）：LLM 漏写 method_summary 与 method_summary_en，
    但 ReAct 工具历史里有成功的 read_section。

    预期执行链：_backfill_analysis_from_tools 先用工具内容补全 method_summary
    （+ sections_read）→ 随后 _backfill_en_fields 把 method_summary_en 回退到
    **工具回填后的 method_summary 值**，而非空。

    这是把"工具回填在 *_en 回退之前"这一顺序约束钉死的关键用例：若未来有人把
    _backfill_en_fields 提到工具回填之前，method_summary_en 会回退成空（此时
    method_summary 还是空），本用例会立刻失败。
    """
    tool_content = "This section proposes a Transformer with multi-head attention."
    react_messages = [
        _ai_tool_call("Method", "call-1"),
        _tool_msg(tool_content, "call-1"),
    ]
    result = {
        "method_summary": "",          # LLM 漏写主字段
        "datasets": ["D"], "metrics": ["M"], "hyperparams": {},
        "hardware_requirements": "GPU",
        "sections_read": [],           # LLM 漏写（工具历史有）
        "analysis_notes": "",
        # method_summary_en / hardware_requirements_en 漏写
    }
    update = pa._map_analysis_result(result, _analysis_state(), react_messages)
    analysis = update["paper_analysis"]

    # 1) 工具回填补全 method_summary（取工具内容前缀）
    assert analysis["method_summary"].startswith("This section proposes a Transformer"), (
        "method_summary 未被工具历史回填"
    )
    assert "Method" in analysis["sections_read"], "sections_read 未被工具历史回填"

    # 2) 关键：method_summary_en 回退到工具回填后的 method_summary（非空、相等）
    assert analysis["method_summary_en"] == analysis["method_summary"], (
        "method_summary_en 未回退到工具回填后的 method_summary——执行顺序被破坏"
        "（*_en backfill 跑在工具回填之前会回退成空）"
    )
    assert analysis["method_summary_en"], "method_summary_en 不应为空"

    # 3) hardware_requirements_en 回退到 hardware_requirements
    assert analysis["hardware_requirements_en"] == "GPU"

    # 4) 工具回填补全后 method_summary/sections_read 都非空 → 不应再有 missing core degraded；
    #    但仍有 *_en 回退的 degraded。验证 degraded 语义精确。
    degraded = [e for e in update["node_errors"] if e["error_type"] == "degraded"]
    en_degraded = [e for e in degraded if "_en" in e["error_message"]]
    missing_degraded = [e for e in degraded if "缺失" in e["error_message"]]
    assert len(en_degraded) == 1, "应有 1 条 *_en 回退 degraded"
    assert len(missing_degraded) == 0, (
        "工具回填已补全核心字段，不应再标 missing core degraded（证明回填在判降级之前）"
    )
    assert update["degraded_nodes"].count("paper_analysis") == 1, "degraded_nodes 未去重"


def test_deep_b1_analysis_triple_degraded_dedup_and_count() -> None:
    """三重降级叠加去重：*_en 漏写 + 工具回填无可用历史 + 核心字段缺失同时发生。

    无 react_messages（无工具历史可回填）+ method_summary 空 + sections_read 空 +
    *_en 漏写：
    - method_summary 空 → *_en 无主字段可回退（不产生 *_en degraded，因为
      hardware_requirements 有值会单独触发 *_en 回退）；
    - missing core 触发 1 条 degraded；
    - degraded_nodes 去重为单次。

    这是补 intake 侧之外、analysis 侧"工具历史缺位 + 多源降级"的 NodeError 计数语义。
    """
    result = {
        "method_summary": "",       # 空，且无工具历史可回填
        "datasets": [], "metrics": [],   # 触发 datasets+metrics missing
        "hyperparams": {},
        "hardware_requirements": "GPU",  # 有值 → hardware_requirements_en 回退触发 *_en degraded
        "sections_read": [],        # 触发 sections_read missing
        "analysis_notes": "",
    }
    update = pa._map_analysis_result(result, _analysis_state())  # 无 react_messages
    degraded = [e for e in update["node_errors"] if e["error_type"] == "degraded"]
    # 一条 *_en（hardware_requirements_en 回退）+ 一条 missing core
    en_degraded = [e for e in degraded if "_en" in e["error_message"]]
    missing_degraded = [e for e in degraded if "缺失" in e["error_message"]]
    assert len(en_degraded) == 1, f"应有 1 条 *_en degraded，实测 {len(en_degraded)}"
    assert len(missing_degraded) == 1, f"应有 1 条 missing degraded，实测 {len(missing_degraded)}"
    # degraded_nodes 去重：尽管两处都 append，最终只出现一次
    assert update["degraded_nodes"].count("paper_analysis") == 1, (
        f"degraded_nodes 未去重：{update['degraded_nodes']}"
    )


# ---- Prompt Cache 字节级幂等：极端 context（None / 空 dict / 特殊字符）前缀稳定 ----


def test_deep_b1_prompt_prefix_stable_under_extreme_contexts() -> None:
    """Prompt Cache 前缀稳定性加固：在 paper_meta=None / 空 dict / 含特殊字符（{}、引号、
    换行、中文）等极端 context 下，system prompt 截去尾部上下文后的前缀仍字节级一致。

    上一轮只在两篇"正常"论文上验证前缀稳定。本用例把 context 推到边角，确认
    _format_paper_context 的渲染（json.dumps sort_keys / ensure_ascii=False）与前缀拼接
    不会因 None / 空 dict / 特殊字符导致主体前缀漂移——这是 R-PC4 字节级幂等的更强证据。
    """
    separator = "\n--- 当前论文上下文 ---\n"
    expected_prefix = (
        pa._ANALYSIS_SYSTEM_PROMPT_BODY + "\n" + pa._LANGUAGE_POLICY_SECTION
    ).rstrip("\n")

    contexts = [
        {"arxiv_id": "2409.05591", "paper_meta": None},
        {"arxiv_id": "2409.05591", "paper_meta": {}},
        {"arxiv_id": "", "paper_meta": None},
        {"arxiv_id": "x", "paper_meta": {
            "arxiv_id": "x", "title": 'Title with "quotes" and {braces}',
            "abstract": "line1\nline2\ttab", "categories": ["cs.CL"],
            "tldr": "中文 TL;DR 带特殊符号 — — \\n 字面量"}},
        # context 完全不是 dict 的防御路径（_build 内 isinstance 兜底）
        {"arxiv_id": "y", "paper_meta": "not-a-dict"},
    ]
    prefixes = []
    for ctx in contexts:
        prompt = pa._build_analysis_system_prompt(ctx)
        assert separator in prompt, f"context={ctx!r} 缺尾部分隔符"
        prefix = prompt.split(separator, 1)[0].rstrip("\n")
        prefixes.append(prefix)
        assert prefix == expected_prefix, (
            f"极端 context={ctx!r} 下前缀漂移，破坏 R-PC4 字节级幂等"
        )
    # 所有极端 context 的前缀互相字节级一致
    assert len(set(prefixes)) == 1, "极端 context 间前缀不一致"


def test_deep_b1_intake_prompt_fully_static_under_any_context() -> None:
    """intake system prompt 在任意 context 下完全字节级一致（无尾部动态段落）。

    加固 CP-B1-5：用极端 context（空 dict / 含特殊字符 user_input / 缺键）验证
    _build_intake_system_prompt 始终返回同一字节串（动态 user_input 走 HumanMessage）。
    """
    contexts = [
        {},
        {"user_input": ""},
        {"user_input": 'https://arxiv.org/abs/2409.05591v3 "quoted" {x}'},
        {"user_input": "中文标题 with newline\nand tab\t", "input_type": "title"},
        {"unrelated_key": object()},  # 完全无关的 key 也不应影响
    ]
    outputs = {pi._build_intake_system_prompt(c) for c in contexts}
    assert len(outputs) == 1, "intake system prompt 随 context 变化（破坏字节幂等）"
    only = outputs.pop()
    assert only == pi._INTAKE_SYSTEM_PROMPT + "\n" + pi._LANGUAGE_POLICY_SECTION_INTAKE


# ---- WARNING 非静默：三态各形态都打 WARNING（治理范式不被空串/None 绕过）----


def test_deep_b1_intake_empty_string_zh_still_warns(caplog) -> None:
    """空串形态的 *_zh 回退同样打 WARNING（非静默）。

    防止"LLM 输出空串"被当成'有值'而静默吞掉降级信号——空串触发回退就必须告警。
    """
    result = {
        "arxiv_id": "2409.05591", "title": "T", "authors": ["A"],
        "abstract": "ABS", "categories": ["cs.CL"], "tldr": "TLDR",
        "title_zh": "", "abstract_zh": "", "tldr_zh": "",
    }
    with caplog.at_level(logging.WARNING, logger="core.nodes.paper_intake"):
        pi._map_intake_result(result, _intake_state())
    warns = [r for r in caplog.records
             if r.levelno == logging.WARNING and "_zh" in r.getMessage()]
    assert len(warns) >= 1, "空串 *_zh 回退未打 WARNING（静默吞错）"


# ========== CP-B1-10：sp1 既有 paper_intake / paper_analysis 单测仍通过 ==========


def test_cp_b1_10_sp1_paper_analysis_checkpoints_pass() -> None:
    """CP-B1-10(部分): sp1 paper_analysis CP1~CP11 仍全绿（复用其 main() 入口）。"""
    test_pa = importlib.import_module("tests.test_paper_analysis")
    rc = test_pa.main()
    assert rc == 0, "sp1 paper_analysis CP1~CP11 未全绿（B1 改造引入退化）"


def test_cp_b1_10_sp1_paper_intake_checkpoints_pass() -> None:
    """CP-B1-10(部分): sp1 paper_intake CP1~CP8 仍全绿（复用其 main() 入口）。"""
    test_pi = importlib.import_module("tests.test_paper_intake")
    rc = test_pi.main()
    assert rc == 0, "sp1 paper_intake CP1~CP8 未全绿（B1 改造引入退化）"
