"""Sprint 2 任务 A2 单元测试补强：core/llm_client.py::resolve_llm_config() 节点级 LLM 路由。

本文件在 tests/test_sprint2_a2.py（CP-A2-1~7，8 passed）基础上**补强测试维度**，
不重复已覆盖的基础 checkpoint，聚焦以下高价值维度：

- 4 个合法 NodeName 逐一命中 override 的参数化（NodeName 全枚举闭环）
- overrides 键为 NodeName 之外的字符串（如 "coding"）查询回退 default
- overrides 健壮性：缺 overrides 键 / overrides 为 None / overrides 非 dict → 回退 default
- 返回值是 config_set 内对象的引用 + 函数不篡改入参（deepcopy 前后比对）
- PermanentError 既断类型也断消息含「default」（多形态错误入参参数化）
- 集成维度：用 create_initial_state 造真实 state，喂 state["llm_config_set"] 验证选路
  （老形态单 LLMConfig 入参 → 任意节点回退 default；新形态带 override → 命中）

resolve_llm_config 为纯函数，不创建 ChatOpenAI、不发起网络请求，故全部用合法
LLMConfig dict 构造即可，无需真实 LLM。
"""

from __future__ import annotations

import copy

import pytest

from core.errors import PermanentError
from core.llm_client import resolve_llm_config
from core.state import create_initial_state


# ========== 测试夹具：两份合法 LLMConfig ==========

cfg_default: dict = {
    "base_url": "https://default.example/v1",
    "model": "model-default",
    "api_key": "sk-default",
    "temperature": 0.3,
    "max_tokens": 8192,
}

cfg_override: dict = {
    "base_url": "https://override.example/v1",
    "model": "model-override",
    "api_key": "sk-override",
    "temperature": 0.1,
    "max_tokens": 16384,
}

# 与 core/state.py::NodeName 强一致的 4 个合法节点名
LEGAL_NODE_NAMES = ["paper_intake", "paper_analysis", "resource_scout", "planning"]


# ========== 维度 1：4 个合法 NodeName 逐一命中 override（参数化全枚举） ==========


@pytest.mark.parametrize("node_name", LEGAL_NODE_NAMES)
def test_each_legal_node_name_hits_its_override(node_name: str) -> None:
    """4 个合法 NodeName 在 overrides 中各自登记时，逐一命中返回各自节点级配置。"""
    config_set = {
        "default": cfg_default,
        "overrides": {node_name: cfg_override},
    }
    result = resolve_llm_config(config_set, node_name)
    assert result == cfg_override, f"{node_name} 已登记 override 时应命中 cfg_override"


@pytest.mark.parametrize("queried", LEGAL_NODE_NAMES)
def test_only_queried_node_hits_others_fall_back(queried: str) -> None:
    """overrides 仅登记某一节点时，仅被查询的节点命中，其余 NodeName 回退 default。"""
    # overrides 只登记 "paper_analysis"
    config_set = {
        "default": cfg_default,
        "overrides": {"paper_analysis": cfg_override},
    }
    result = resolve_llm_config(config_set, queried)
    if queried == "paper_analysis":
        assert result == cfg_override, "已登记节点应命中 override"
    else:
        assert result == cfg_default, f"{queried} 未登记 override 时应回退 default"


# ========== 维度 2：overrides 键为 NodeName 之外的字符串查询回退 default ==========


def test_unknown_node_name_string_falls_back_to_default() -> None:
    """查询 overrides 之外的字符串节点名（如 "coding"）回退 default。

    A2 仅 4 个 NodeName 支持覆写；其余节点名（如执行类 "coding"）即使语法上是
    合法字符串，未登记时也应回退 default，不应报错。
    """
    config_set = {
        "default": cfg_default,
        "overrides": {"paper_intake": cfg_override},
    }
    result = resolve_llm_config(config_set, "coding")
    assert result == cfg_default, "未登记的 'coding' 节点应回退 default"


# ========== 维度 3：overrides 健壮性（缺键 / None / 非 dict 均回退 default） ==========


@pytest.mark.parametrize(
    "overrides_value, desc",
    [
        ("__MISSING__", "缺 overrides 键"),
        (None, "overrides 为 None"),
        ([], "overrides 为 list（非 dict）"),
        ("not-a-dict", "overrides 为 str（非 dict）"),
        (42, "overrides 为 int（非 dict）"),
    ],
)
def test_malformed_overrides_falls_back_to_default(overrides_value, desc: str) -> None:
    """overrides 缺失 / None / 非 dict 时按空覆写表处理，回退 default（向后兼容 sp1）。"""
    config_set = {"default": cfg_default}
    if overrides_value != "__MISSING__":
        config_set["overrides"] = overrides_value
    result = resolve_llm_config(config_set, "paper_intake")
    assert result == cfg_default, f"{desc} 时应回退 default"


# ========== 维度 4：返回引用 + 不篡改入参 ==========


def test_returns_reference_not_copy() -> None:
    """命中 override / 回退 default 时返回的都是 config_set 内对象的同一引用（非拷贝）。"""
    config_set = {
        "default": cfg_default,
        "overrides": {"paper_analysis": cfg_override},
    }
    hit = resolve_llm_config(config_set, "paper_analysis")
    fallback = resolve_llm_config(config_set, "paper_intake")
    assert hit is config_set["overrides"]["paper_analysis"], "命中时应返回 overrides 内同一引用"
    assert fallback is config_set["default"], "回退时应返回 default 内同一引用"


def test_does_not_mutate_input_config_set() -> None:
    """函数为纯查询，调用前后入参 config_set 应深度相等（deepcopy 比对），不被篡改。"""
    config_set = {
        "default": cfg_default,
        "overrides": {"planning": cfg_override},
    }
    snapshot = copy.deepcopy(config_set)
    # 覆盖命中 / 回退 / None 三条路径各调一次
    resolve_llm_config(config_set, "planning")
    resolve_llm_config(config_set, "paper_intake")
    resolve_llm_config(config_set, None)
    assert config_set == snapshot, "resolve_llm_config 不应修改入参 config_set"


# ========== 维度 5：PermanentError 既断类型也断消息含「default」（多形态参数化） ==========


@pytest.mark.parametrize(
    "bad_config_set, desc",
    [
        (None, "None 入参"),
        ({}, "空 dict（缺 default 键）"),
        ({"overrides": {}}, "有 overrides 但缺 default 键"),
        ("not-a-dict", "str 非 dict 形态"),
        (["default"], "list 非 dict 形态"),
        (123, "int 非 dict 形态"),
    ],
)
def test_malformed_config_set_raises_permanent_with_default_msg(bad_config_set, desc: str) -> None:
    """非法 config_set（None/非 dict/缺 default）抛 PermanentError，消息须含「default」。"""
    with pytest.raises(PermanentError) as exc_info:
        resolve_llm_config(bad_config_set, "paper_intake")  # type: ignore[arg-type]
    assert "default" in str(exc_info.value), (
        f"{desc}：PermanentError 消息应含 'default'，实测：{exc_info.value}"
    )


# ========== 维度 6：集成 —— create_initial_state 真实 state 喂入选路 ==========


def test_integration_legacy_single_config_all_nodes_fallback() -> None:
    """老形态单 LLMConfig 入 create_initial_state → state.llm_config_set 任意节点回退 default。

    向后兼容 sp1：单一全局配置被包装为 {"default":cfg,"overrides":{}}，
    4 个 NodeName + None 共用路径都应解析到同一个 default。
    """
    state = create_initial_state("2405.14831", cfg_default)
    config_set = state["llm_config_set"]
    for node_name in LEGAL_NODE_NAMES + [None]:
        result = resolve_llm_config(config_set, node_name)
        assert result == cfg_default, f"老形态下 {node_name} 应回退 default"


def test_integration_new_config_set_with_override_routes_correctly() -> None:
    """新形态 LLMConfigSet 入 create_initial_state → 命中节点取 override，其余回退 default。"""
    new_config_set = {
        "default": cfg_default,
        "overrides": {"paper_analysis": cfg_override},
    }
    state = create_initial_state("2405.14831", new_config_set)
    config_set = state["llm_config_set"]

    assert resolve_llm_config(config_set, "paper_analysis") == cfg_override, (
        "新形态下 paper_analysis 应命中 override"
    )
    assert resolve_llm_config(config_set, "paper_intake") == cfg_default, (
        "新形态下未覆写的 paper_intake 应回退 default"
    )
    assert resolve_llm_config(config_set, None) == cfg_default, (
        "None 共用路径应回退 default"
    )
