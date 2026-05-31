"""Sprint 2 任务 A2 自测：core/llm_client.py::resolve_llm_config() 节点级 LLM 路由。

覆盖 dev-plan §A2 CP-A2-1 ~ CP-A2-7（7 个程序化可验证 checkpoint）。
resolve_llm_config 为纯函数，不创建 ChatOpenAI、不发起网络请求，
故全部用合法的 LLMConfig dict 构造即可，无需真实 LLM。

风格对齐 sp1 / A1 同款轻量结构性断言（tests/test_sprint2_a1.py）。
"""

from __future__ import annotations

import inspect

import pytest

from core.errors import PermanentError
from core.llm_client import create_llm, resolve_llm_config
from core.state import LLMConfig


# ========== 测试夹具：两份合法 LLMConfig ==========

cfg_A: LLMConfig = {
    "base_url": "https://a.example/v1",
    "model": "model-a",
    "api_key": "sk-a",
    "temperature": 0.3,
    "max_tokens": 8192,
}

cfg_B: LLMConfig = {
    "base_url": "https://b.example/v1",
    "model": "model-b",
    "api_key": "sk-b",
    "temperature": 0.1,
    "max_tokens": 16384,
}


# ========== CP-A2-1：overrides 为空时回退 default ==========


def test_cp_a2_1_empty_overrides_returns_default() -> None:
    """CP-A2-1: overrides 为空 dict，任意节点都回退到 default。"""
    result = resolve_llm_config({"default": cfg_A, "overrides": {}}, "paper_intake")
    assert result == cfg_A, "overrides 为空时应回退 default(cfg_A)"


# ========== CP-A2-2：命中 overrides 返回节点级配置 ==========


def test_cp_a2_2_hit_override_returns_override() -> None:
    """CP-A2-2: node_name 在 overrides 中，返回对应的节点级配置。"""
    result = resolve_llm_config(
        {"default": cfg_A, "overrides": {"paper_analysis": cfg_B}},
        "paper_analysis",
    )
    assert result == cfg_B, "命中 overrides[paper_analysis] 时应返回 cfg_B"


# ========== CP-A2-3：未命中 overrides 的其它节点回退 default ==========


def test_cp_a2_3_miss_override_falls_back_to_default() -> None:
    """CP-A2-3: node_name 不在 overrides 中（其它节点），回退 default。"""
    result = resolve_llm_config(
        {"default": cfg_A, "overrides": {"paper_analysis": cfg_B}},
        "paper_intake",
    )
    assert result == cfg_A, "paper_intake 未覆写时应回退 default(cfg_A)，不应误取 cfg_B"


# ========== CP-A2-4：node_name 为 None 返回 default ==========


def test_cp_a2_4_none_node_name_returns_default() -> None:
    """CP-A2-4: node_name 为 None（force_finish 共用路径），返回 default。"""
    result = resolve_llm_config({"default": cfg_A, "overrides": {}}, None)
    assert result == cfg_A, "node_name 为 None 时应返回 default(cfg_A)"


# ========== CP-A2-5：缺 default 键抛 PermanentError ==========


def test_cp_a2_5_missing_default_raises_permanent_error() -> None:
    """CP-A2-5: 空 dict（缺 default 键）抛 PermanentError。"""
    with pytest.raises(PermanentError, match="llm_config_set.default 缺失或形态错误"):
        resolve_llm_config({}, "paper_intake")  # type: ignore[arg-type]


# ========== CP-A2-6：None 入参抛 PermanentError ==========


def test_cp_a2_6_none_config_set_raises_permanent_error() -> None:
    """CP-A2-6: llm_config_set 为 None 抛 PermanentError。"""
    with pytest.raises(PermanentError, match="llm_config_set.default 缺失或形态错误"):
        resolve_llm_config(None, "paper_intake")  # type: ignore[arg-type]


# ========== CP-A2-7：create_llm 函数签名未变（向后兼容） ==========


def test_cp_a2_7_create_llm_signature_unchanged() -> None:
    """CP-A2-7: create_llm() 签名未变，仍是单参 ``config: LLMConfig``。

    A2 只允许在 llm_client.py 追加 resolve_llm_config，**绝不动 create_llm 签名**。
    用 inspect.signature 断言参数个数与名字，防止误改破坏 sp1 既有调用方。
    """
    sig = inspect.signature(create_llm)
    params = list(sig.parameters.values())
    assert len(params) == 1, f"create_llm 应仍是单参函数，实测参数：{[p.name for p in params]}"
    assert params[0].name == "config", (
        f"create_llm 唯一参数应仍名为 config，实测：{params[0].name}"
    )
    # 注解应仍是 LLMConfig（字符串注解或类型对象两种形态都接受）
    annotation = params[0].annotation
    assert annotation in (LLMConfig, "LLMConfig"), (
        f"create_llm 参数注解应仍为 LLMConfig，实测：{annotation}"
    )


# ========== A2 补：node_name 为 None 但命中 overrides 同名也走 default 路径 ==========


def test_cp_a2_aux_none_short_circuits_before_overrides_lookup() -> None:
    """补充：node_name 为 None 时短路返回 default，不进入 overrides 查找。

    防止后续重构误用 ``overrides.get(None, default)``——None 作为 key 查找虽不报错，
    但语义上 force_finish 共用路径就应取 default。本用例固化 None 短路行为。
    """
    result = resolve_llm_config(
        {"default": cfg_A, "overrides": {"paper_analysis": cfg_B}}, None
    )
    assert result == cfg_A
