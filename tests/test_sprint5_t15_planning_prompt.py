"""Sprint 5 任务 T-S5-1-5 自测：planning prompt/schema 静态批次（P7~P9）+ map 回填。

覆盖 dev-plan sp5 T-S5-1-5 CP-1.5-1 ~ CP-1.5-4（S5-01 / S5-05 / S5-09；
AC-S5-01 / AC-S5-09 / AC-S5-19 的 prompt/schema 部分）。

范式来源：
    - CP-1.5-3 沿用 CP-B3-10 同款"主体字节级一致"断言（test_sprint2_b3.py），
      本文件内新写，不改既有文件；
    - 模块访问用 importlib.import_module（规避 core/nodes/__init__.py callable 遮蔽，
      已知坑 #6）。

纯结构性断言：无真实 LLM、无网络。
"""

from __future__ import annotations

import importlib
from typing import Any, Dict

planning_module = importlib.import_module("core.nodes.planning")

_BODY = planning_module._PLANNING_SYSTEM_PROMPT_BODY
_TERM_SECTION = planning_module._PLANNING_TERMINOLOGY_SECTION
_SCHEMA = planning_module.REPRODUCTION_PLAN_SCHEMA
_build_prompt = planning_module._build_planning_system_prompt
_map = planning_module._map_planning_result
NODE_NAME = planning_module.NODE_NAME


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _base_state(**overrides: Any) -> Dict[str, Any]:
    state: Dict[str, Any] = {
        "llm_config_set": {
            "default": {
                "base_url": "http://x",
                "model": "m",
                "api_key": "k",
                "temperature": 0.0,
                "max_tokens": 1024,
            },
            "overrides": {},
        },
        "paper_meta": {"arxiv_id": "2405.14831", "title": "HippoRAG"},
        "paper_analysis": {"method_summary": "中文摘要", "metrics": ["EM"]},
        "resource_info": {
            "repos": [{"url": "https://github.com/a/repo", "quality_score": 0.8}],
            "selected_repo": {"url": "https://github.com/a/repo", "quality_score": 0.8},
            "external_resources": [],
            "resource_strategy": "use_repo",
        },
        "node_errors": [],
        "degraded_nodes": [],
        "_planning_user_feedback": None,
    }
    state.update(overrides)
    return state


def _full_result(**overrides: Any) -> Dict[str, Any]:
    """完整 <result>（sp5 新形态）。"""
    result: Dict[str, Any] = {
        "plan_summary": "复现思路概述……",
        "environment": {"gpu": "1x A100"},
        "data_preparation": ["下载数据集"],
        "code_strategy": "use_repo",
        "execution_steps": [
            {"step_name": "安装依赖", "command": "pip install -r requirements.txt",
             "expected_output": "成功"},
        ],
        "expected_results": [
            {"description": "loss 应收敛", "trend": None},
            {"description": "有技能组应优于无技能组",
             "trend": {"metric": "pass_rate", "greater": "evoskills", "lesser": "no_skill"}},
        ],
        "required_credentials": [
            {"purpose_key": "env:OPENAI_API_KEY", "purpose": "论文方法依赖真实 LLM 调用"},
        ],
        "estimated_time": "2 天",
        "deliverables": ["README.md", "requirements.txt", "run.py"],
    }
    result.update(overrides)
    return result


# ===========================================================================
# CP-1.5-1：prompt 断言（AC-S5-01 / AC-S5-09 / AC-S5-19 的 prompt 部分）
# ===========================================================================


def test_cp_1_5_1_prompt_forbids_fabricated_numbers():
    """P7：主体第 6 节含禁编造数值文案 + 论文 baseline 数字不复述指令（AC-S5-09）。"""
    assert "严禁编造具体数值" in _BODY
    assert "禁止凭空捏造任何具体数字" in _BODY
    # 论文真实 baseline 数字不在计划复述。
    assert "**不要**在计划中复述" in _BODY
    # 旧文案（引用论文 baseline_results 数值预期）已移除。
    assert "引用论文 baseline_results" not in _BODY


def test_cp_1_5_1_prompt_expected_results_qualitative_form():
    """P7：expected_results 指令为定性描述 + 可选 trend 结构（metric/greater/lesser）。"""
    assert "定性" in _BODY
    assert '"trend"' in _BODY or "trend" in _BODY
    for key in ("metric", "greater", "lesser"):
        assert key in _BODY, f"trend 结构键 {key} 应出现在主体指令中"
    assert '"description"' in _BODY or "description" in _BODY


def test_cp_1_5_1_prompt_required_credentials_instruction():
    """P7：required_credentials 声明指令齐备——purpose_key 三类约定 + purpose 中文用途说明
    （AC-S5-01）。"""
    assert "required_credentials" in _BODY
    assert "git_credential:<host>" in _BODY
    assert "hf_token" in _BODY
    assert "env:<ENV_VAR>" in _BODY
    assert "env:OPENAI_API_KEY" in _BODY  # 通用约定示例
    assert "purpose_key" in _BODY and "purpose" in _BODY
    assert "中文用途说明" in _BODY
    # 安全红线：只声明用途，不写凭证值。
    assert "凭证值本身" in _BODY
    # 可为空属合法：不依赖凭证时输出 []，不杜撰。
    assert "空列表" in _BODY and "杜撰" in _BODY


def test_cp_1_5_1_prompt_tail_terminology_section_present():
    """P8：尾部术语约束段存在（AC-S5-19）——机器可读保留原值 / 用户可读通俗中文 /
    禁内部枚举、字段名、自创缩写。"""
    assert "【术语与措辞约束】" in _BODY
    assert "保留枚举" in _TERM_SECTION
    assert "通俗中文" in _TERM_SECTION
    assert "禁止直接引用内部枚举值" in _TERM_SECTION
    assert "自创缩写" in _TERM_SECTION
    # 机器可读字段（枚举原值）与用户可读文本两个面向均被覆盖。
    assert "机器可读字段" in _TERM_SECTION
    assert "可读文本" in _TERM_SECTION


# ===========================================================================
# CP-1.5-2：schema 断言（AC-S5-09 / AC-S5-01 的 schema 部分）
# ===========================================================================


def test_cp_1_5_2_schema_expected_results_is_array_form():
    """P9：expected_results 为 array 形态——items 中 description 必填、trend 可选，
    无 {metric: number} 数值映射（AC-S5-09）。"""
    prop = _SCHEMA["properties"]["expected_results"]
    assert prop["type"] == "array", "expected_results 应为 array 形态（不再是 object 数值映射）"
    items = prop["items"]
    assert items["type"] == "object"
    assert items["required"] == ["description"], "description 必填、trend 不在 required 中"
    assert "description" in items["properties"]
    assert "trend" in items["properties"]
    trend = items["properties"]["trend"]
    # trend 可空（object 或 null），三键为 metric/greater/lesser。
    assert "null" in trend["type"]
    assert set(trend["properties"].keys()) == {"metric", "greater", "lesser"}
    # 无数值映射残留：items 属性中不存在 number 类型的开放映射。
    assert items["properties"]["description"]["type"] == "string"


def test_cp_1_5_2_schema_required_credentials_two_keys():
    """P9：required_credentials 可选属性——每项恰两键 purpose_key/purpose 且均必填
    （AC-S5-01）。"""
    prop = _SCHEMA["properties"]["required_credentials"]
    assert prop["type"] == "array"
    items = prop["items"]
    assert set(items["properties"].keys()) == {"purpose_key", "purpose"}
    assert sorted(items["required"]) == ["purpose", "purpose_key"]
    assert items["properties"]["purpose_key"]["type"] == "string"
    assert items["properties"]["purpose"]["type"] == "string"


def test_cp_1_5_2_schema_top_level_required_unchanged():
    """两新字段均不入 schema top-level required（required_credentials 可为空属合法；
    expected_results 缺失走 map 回填）。"""
    assert "required_credentials" not in _SCHEMA["required"]
    assert "expected_results" not in _SCHEMA["required"]
    # 既有必填三键不动。
    assert _SCHEMA["required"] == ["plan_summary", "code_strategy", "deliverables"]


# ===========================================================================
# CP-1.5-3：主体字节级一致（CP-B3-10 同款范式，本文件内新写）+ 尾部段落常量 + 禁动态变量
# ===========================================================================


def test_cp_1_5_3_prompt_body_byte_identical_across_papers():
    """两篇不同论文上下文构建 system prompt，去尾部术语段后 == 常量（字节级）。"""
    a = _build_prompt({"paper_meta": {"arxiv_id": "1111.11111", "title": "Paper A"}})
    b = _build_prompt({
        "paper_meta": {"arxiv_id": "2222.22222", "title": "Paper B"},
        "user_feedback": "改一下",
    })
    assert a == b == _BODY  # 整体字节级一致（planning 动态上下文全走 HumanMessage）
    # 尾部段落常量断言：主体以术语约束段结尾，去尾部后仍为跨论文一致的静态前缀。
    assert _BODY.endswith(_TERM_SECTION)
    stripped_a = a[: -len(_TERM_SECTION)]
    stripped_b = b[: -len(_TERM_SECTION)]
    assert stripped_a == stripped_b == _BODY[: -len(_TERM_SECTION)]


def test_cp_1_5_3_no_dynamic_variables_in_body():
    """禁动态变量审查：哨兵论文级动态值不得渗入 system prompt 主体。"""
    sentinel_ctx = {
        "paper_meta": {"arxiv_id": "9999.99999", "title": "SENTINEL_TITLE_XYZ"},
        "user_feedback": "SENTINEL_FEEDBACK_XYZ",
        "pending_repo_url": "https://example.com/SENTINEL_REPO",
    }
    out = _build_prompt(sentinel_ctx)
    for needle in ("9999.99999", "SENTINEL_TITLE_XYZ", "SENTINEL_FEEDBACK_XYZ", "SENTINEL_REPO"):
        assert needle not in out
    # 主体/尾部段落均为纯 str 常量，无 f-string 占位符残留。
    assert "{arxiv_id}" not in _BODY and "{title}" not in _BODY
    assert "{" not in _TERM_SECTION  # 术语段纯文本，无任何占位符/花括号


def test_cp_1_5_3_terminology_section_is_static_tail_constant():
    """尾部术语段为独立模块级 str 常量（跨论文字节恒定，R-PC4 合规落点）。"""
    assert isinstance(_TERM_SECTION, str) and _TERM_SECTION.strip()
    # 位置断言：术语段在 REPO_QUALITY_SCORING_SECTION 之后（真·尾部）。
    from core.nodes._repo_scoring import REPO_QUALITY_SCORING_SECTION
    assert _BODY.index(REPO_QUALITY_SCORING_SECTION) < _BODY.index("【术语与措辞约束】")


# ===========================================================================
# CP-1.5-4：map 回填断言（缺失 → [] 不炸；带值 → 透传）
# ===========================================================================


def test_cp_1_5_4_missing_both_fields_backfill_empty_list():
    """<result> 缺 expected_results + required_credentials → 两字段回填 [] 不炸、
    不 degraded（两字段均不在 _missing_core_fields）。"""
    result = _full_result()
    result.pop("expected_results")
    result.pop("required_credentials")
    out = _map(result, _base_state())
    plan = out["reproduction_plan"]
    assert plan["expected_results"] == []
    assert plan["required_credentials"] == []
    # required_credentials 为空属合法：不触发 degraded。
    assert NODE_NAME not in out.get("degraded_nodes", [])


def test_cp_1_5_4_with_values_passthrough():
    """带值 → 透传：新形态 list 原样进 plan。"""
    result = _full_result()
    out = _map(result, _base_state())
    plan = out["reproduction_plan"]
    assert plan["expected_results"] == result["expected_results"]
    assert plan["required_credentials"] == result["required_credentials"]
    assert plan["required_credentials"][0]["purpose_key"] == "env:OPENAI_API_KEY"


def test_cp_1_5_4_legacy_dict_form_coerced_to_empty():
    """旧 {metric: number} dict 形态（编造数值链路）→ 回填 []，不带进新链路。"""
    result = _full_result(expected_results={"EM": 0.5}, required_credentials={"x": "y"})
    out = _map(result, _base_state())
    plan = out["reproduction_plan"]
    assert plan["expected_results"] == []
    assert plan["required_credentials"] == []


def test_cp_1_5_4_dirty_items_tolerated():
    """脏项容忍：expected_results 字符串项包装 {"description": str}；
    required_credentials 非 dict 项过滤。"""
    result = _full_result(
        expected_results=["loss 应收敛", {"description": "趋势符合"}, None, 42],
        required_credentials=[{"purpose_key": "hf_token", "purpose": "下载模型"}, "garbage", None],
    )
    out = _map(result, _base_state())
    plan = out["reproduction_plan"]
    assert plan["expected_results"] == [
        {"description": "loss 应收敛"},
        {"description": "趋势符合"},
    ]
    assert plan["required_credentials"] == [{"purpose_key": "hf_token", "purpose": "下载模型"}]


def test_cp_1_5_4_minimal_plan_degraded_path_has_empty_lists():
    """降级最简版 plan（result=None 路径）两字段同样为 []（新形态一致性）。"""
    out = _map(None, _base_state())
    plan = out["reproduction_plan"]
    assert plan["expected_results"] == []
    assert plan["required_credentials"] == []
    assert NODE_NAME in out["degraded_nodes"]
