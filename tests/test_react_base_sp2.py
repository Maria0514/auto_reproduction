"""Sprint 2 任务 A3 自测：_make_react_wrapper 接入节点级 LLM 路由。

覆盖 dev-plan §A3 检查点：
- CP-A3-1：修改后 ``_make_react_wrapper`` 函数签名未变（inspect.signature 形参列表完全一致）。
- CP-A3-2：P1 全局回退——state 无 override 时，wrapper 内 create_llm 收到
  ``llm_config_set["default"]``。
- CP-A3-3：P2 单节点 override——overrides[node_name] 命中时，wrapper 内 create_llm
  收到节点级覆写配置。
- P3 补充：多节点全 override 形态，每个节点各取自己的 override。

架构参考：sprint2/architecture.md §4.9（落地约束 3，B 方案——路由发生在 wrapper 工厂层）。

设计：mock ``react_base.create_llm`` 捕获实参，断言选路结果，不发起真实网络请求。
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from langchain_core.tools import BaseTool


# ---------- 公共 fixtures / helpers ----------


CFG_DEFAULT: Dict[str, Any] = {
    "base_url": "https://default.example/v1",
    "model": "default-model",
    "api_key": "sk-default",
    "temperature": 0.3,
    "max_tokens": 1024,
}
CFG_INTAKE: Dict[str, Any] = {
    "base_url": "https://intake.example/v1",
    "model": "intake-model",
    "api_key": "sk-intake",
    "temperature": 0.1,
    "max_tokens": 2048,
}
CFG_ANALYSIS: Dict[str, Any] = {
    "base_url": "https://analysis.example/v1",
    "model": "analysis-model",
    "api_key": "sk-analysis",
    "temperature": 0.5,
    "max_tokens": 4096,
}


class _FakeSubgraph:
    """脚本化子图：直接回显 initial，并标记 done。"""

    def invoke(self, initial: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "messages": initial["messages"],
            "round": 1,
            "max_rounds": initial["max_rounds"],
            "status": "done",
            "result": {"ok": True},
            "context": initial["context"],
        }


def _install_wrapper_stubs(monkeypatch) -> List[Dict[str, Any]]:
    """把 create_react_subgraph / create_llm 替换为桩，返回 create_llm 实参收集列表。

    关键：**不 mock resolve_llm_config**，确保 wrapper 内真实走选路逻辑。
    """
    from core import react_base

    captured_cfgs: List[Dict[str, Any]] = []

    def fake_create_react_subgraph(**kwargs):
        return _FakeSubgraph()

    def fake_create_llm(config):
        captured_cfgs.append(config)
        return object()

    monkeypatch.setattr(react_base, "create_react_subgraph", fake_create_react_subgraph)
    monkeypatch.setattr(react_base, "create_llm", fake_create_llm)
    return captured_cfgs


def _make_wrapper(node_name: str):
    from core import react_base

    def build_context(state):
        return {"user_input": state.get("user_input", "")}

    def build_system_prompt(context):
        return f"You are {node_name} agent."

    def get_tools(state) -> List[BaseTool]:
        return []

    def map_result(result, state):
        return {}

    return react_base._make_react_wrapper(
        node_name=node_name,
        build_context=build_context,
        build_system_prompt=build_system_prompt,
        get_tools=get_tools,
        map_result=map_result,
        max_rounds=5,
    )


def _make_state(llm_config_set: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "user_input": "2405.14831",
        "input_type": "arxiv_id",
        "llm_config_set": llm_config_set,
        "retry_budget_remaining": 50,
        "node_errors": [],
        "degraded_nodes": [],
        "messages": [],
    }


# ---------- CP-A3-1：签名零变化 ----------


def test_cp_a3_1_make_react_wrapper_signature_unchanged() -> None:
    """CP-A3-1: _make_react_wrapper 形参列表与 sp1 设计完全一致（node_name 首参不变）。"""
    import inspect

    from core import react_base

    sig = inspect.signature(react_base._make_react_wrapper)
    param_names = list(sig.parameters.keys())
    assert param_names == [
        "node_name",
        "build_context",
        "build_system_prompt",
        "get_tools",
        "map_result",
        "max_rounds",
        "result_schema",
    ], f"_make_react_wrapper 签名被改动：{param_names}"

    # node_name 仍是第一个位置参数且无默认值（sp1 契约）
    node_name_param = sig.parameters["node_name"]
    assert node_name_param.default is inspect.Parameter.empty
    assert node_name_param.kind in (
        inspect.Parameter.POSITIONAL_ONLY,
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
    )
    # result_schema 仍是唯一带默认值（None）的尾参
    assert sig.parameters["result_schema"].default is None


# ---------- CP-A3-2：P1 全局回退（无 override） ----------


def test_cp_a3_2_p1_global_fallback_no_override(monkeypatch) -> None:
    """CP-A3-2: overrides 为空时，wrapper 内 create_llm 收到 default 配置。"""
    captured = _install_wrapper_stubs(monkeypatch)

    wrapper = _make_wrapper("paper_intake")
    state = _make_state({"default": CFG_DEFAULT, "overrides": {}})
    wrapper(state)

    assert captured, "create_llm 未被调用"
    assert captured[0] == CFG_DEFAULT, (
        f"P1 全局回退失败：期望 default 配置，实际 {captured[0]}"
    )


def test_cp_a3_2_p1_fallback_node_not_in_overrides(monkeypatch) -> None:
    """CP-A3-2 补：node_name 不在 overrides 中时回退 default（其它节点有 override 不影响）。"""
    captured = _install_wrapper_stubs(monkeypatch)

    wrapper = _make_wrapper("paper_intake")
    # 只为 paper_analysis 配置 override，paper_intake 应回退 default
    state = _make_state(
        {"default": CFG_DEFAULT, "overrides": {"paper_analysis": CFG_ANALYSIS}}
    )
    wrapper(state)

    assert captured[0] == CFG_DEFAULT, (
        f"node 未命中 override 应回退 default，实际 {captured[0]}"
    )


# ---------- CP-A3-3：P2 单节点 override ----------


def test_cp_a3_3_p2_single_node_override(monkeypatch) -> None:
    """CP-A3-3: overrides[node_name] 命中时，wrapper 内 create_llm 收到节点级覆写配置。"""
    captured = _install_wrapper_stubs(monkeypatch)

    wrapper = _make_wrapper("paper_intake")
    state = _make_state(
        {"default": CFG_DEFAULT, "overrides": {"paper_intake": CFG_INTAKE}}
    )
    wrapper(state)

    assert captured[0] == CFG_INTAKE, (
        f"P2 单节点 override 失败：期望 paper_intake 覆写配置，实际 {captured[0]}"
    )
    assert captured[0] != CFG_DEFAULT


# ---------- P3：多节点全 override，各取其所 ----------


def test_cp_a3_p3_multi_node_override_each_picks_own(monkeypatch) -> None:
    """P3: 多节点 override 形态下，每个节点 wrapper 各取自己的 override 配置。"""
    captured = _install_wrapper_stubs(monkeypatch)

    config_set = {
        "default": CFG_DEFAULT,
        "overrides": {
            "paper_intake": CFG_INTAKE,
            "paper_analysis": CFG_ANALYSIS,
        },
    }

    intake_wrapper = _make_wrapper("paper_intake")
    analysis_wrapper = _make_wrapper("paper_analysis")

    intake_wrapper(_make_state(config_set))
    analysis_wrapper(_make_state(config_set))

    assert captured[0] == CFG_INTAKE, "paper_intake 应取 intake override"
    assert captured[1] == CFG_ANALYSIS, "paper_analysis 应取 analysis override"


# ---------- 回归：state 已无镜像字段 llm_config，wrapper 也不再读它 ----------


def test_a3_wrapper_does_not_read_legacy_llm_config(monkeypatch) -> None:
    """state 仅含 llm_config_set（无 llm_config 镜像）时，wrapper 仍能正常选路。"""
    captured = _install_wrapper_stubs(monkeypatch)

    wrapper = _make_wrapper("paper_intake")
    state = _make_state({"default": CFG_DEFAULT, "overrides": {}})
    assert "llm_config" not in state  # 明确：无镜像字段

    update = wrapper(state)
    assert captured[0] == CFG_DEFAULT
    assert isinstance(update, dict)


# ---------- 测试工程师补强：A3 集成视角边界（验收补全） ----------


def test_aux_a3_malformed_config_set_propagates_permanent_error(monkeypatch) -> None:
    """补强 1：llm_config_set 缺 default 时，PermanentError 经 wrapper 调用路径真实冒泡。

    A2 已在单元层验证 resolve_llm_config 抛 PermanentError；本用例补 A3 集成视角——
    确认该错误在 _make_react_wrapper 的 wrapper 体内不被吞掉（wrapper 没有 try/except
    把它降级成 None / 静默继续），而是原样向上冒泡，避免后续 create_llm(None) 隐性崩溃。
    """
    from core.errors import PermanentError

    captured = _install_wrapper_stubs(monkeypatch)

    wrapper = _make_wrapper("paper_intake")
    # 形态错误：缺 default 键
    state = _make_state({"overrides": {"paper_intake": CFG_INTAKE}})

    with pytest.raises(PermanentError, match="default"):
        wrapper(state)
    # 选路在 create_llm 之前失败，create_llm 不应被调用
    assert captured == [], "形态错误应在 resolve 阶段抛错，create_llm 不应被触达"


def test_aux_a3_create_llm_receives_override_object_identity(monkeypatch) -> None:
    """补强 2：命中 override 时 create_llm 收到的是 override 配置对象本身（非拷贝/非污染）。

    保护 resolve_llm_config 的"返回引用而非深拷贝"契约（A2 已在单元层断言 is 同一对象）。
    本用例从 wrapper 调用视角确认：经过 wrapper → resolve → create_llm 链路后，create_llm
    收到的仍是 state 中那个 override 对象本身，default 配置不会泄漏进来。
    """
    captured = _install_wrapper_stubs(monkeypatch)

    override_obj = dict(CFG_INTAKE)  # 独立对象，便于 is 断言
    config_set = {"default": CFG_DEFAULT, "overrides": {"paper_intake": override_obj}}

    wrapper = _make_wrapper("paper_intake")
    wrapper(_make_state(config_set))

    assert captured[0] is override_obj, "命中 override 时 create_llm 应收到 override 对象本身"


def test_aux_a3_same_wrapper_repeated_calls_independent(monkeypatch) -> None:
    """补强 3：同一 wrapper 多次调用各自按当次 state 独立选路（无跨调用状态累积）。

    满足"测试用例必须能独立运行、不依赖时序"的约束在被测代码侧的镜像要求：wrapper 本身
    是无状态闭包，连续两次调用——第一次 default、第二次该节点的 override——应各自取对的配置，
    不因第一次调用的 default 选路而污染第二次的 override 选路。
    """
    captured = _install_wrapper_stubs(monkeypatch)

    wrapper = _make_wrapper("paper_intake")
    # 第一次：无 override → default
    wrapper(_make_state({"default": CFG_DEFAULT, "overrides": {}}))
    # 第二次：同一 wrapper、不同 state 带 paper_intake override
    wrapper(_make_state({"default": CFG_DEFAULT, "overrides": {"paper_intake": CFG_INTAKE}}))

    assert captured[0] == CFG_DEFAULT, "首次调用应取 default"
    assert captured[1] == CFG_INTAKE, "二次调用应取 override，不被首次 default 污染"


def test_aux_a3_overrides_none_falls_back_to_default(monkeypatch) -> None:
    """补强 4：overrides 键缺失 / 为 None 时，wrapper 经 resolve 回退 default（健壮性）。

    UI/GraphController 装配 llm_config_set 时可能只给 default（overrides 缺键或显式 None）。
    resolve_llm_config 用 `get("overrides") or {}` 兜底；本用例从 wrapper 视角验证两形态
    都安全回退 default，不抛 KeyError / TypeError。
    """
    captured = _install_wrapper_stubs(monkeypatch)

    wrapper = _make_wrapper("paper_intake")

    # 形态 a：overrides 键缺失
    wrapper(_make_state({"default": CFG_DEFAULT}))
    # 形态 b：overrides 显式为 None
    wrapper(_make_state({"default": CFG_DEFAULT, "overrides": None}))

    assert captured[0] == CFG_DEFAULT, "overrides 缺失应回退 default"
    assert captured[1] == CFG_DEFAULT, "overrides=None 应回退 default"
