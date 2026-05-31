"""Sprint 2 任务 A1 自测：core/state.py 扩展。

覆盖 dev-plan §A1 CP-A1-1 ~ CP-A1-8（程序化可验证的 8 个 checkpoint）。
CP-A1-9（全量 pytest 168/168 回归）由仓库根 ``pytest -q`` 走，不在本文件覆盖。

参考实现：
    - sp1 同款风格 tests/test_sprint1_smoke.py（轻量结构性断言，无真实 LLM）。
    - 关键设计偏差备注（CP-A1-5）：
        dev-plan L308 原意是把 ``GlobalState.llm_config`` 替换为 ``llm_config_set``，
        但 core/state.py 实际选择"新增 llm_config_set + 保留 llm_config 镜像"的双字段
        过渡形态——理由是 react_base.py:825 与 test_sprint1_smoke.py:229 / test_graph_e2e.py:254
        仍在直取 ``state["llm_config"]``，待 sp2 A3 单行 diff 完成后才能彻底移除。
        本测试遵循当前实现，断言"llm_config_set 必须存在"+"llm_config 作为镜像
        允许同时存在并等于 llm_config_set['default']"，与代码注释（state.py L160-170）一致。
"""

from __future__ import annotations

from typing import get_type_hints

import pytest


# ========== CP-A1-1：LLMConfigSet / NodeName 可导入 ==========


def test_cp_a1_1_import_llm_config_set_and_node_name() -> None:
    """CP-A1-1: ``from core.state import LLMConfigSet, NodeName`` 可正常导入。"""
    from core.state import LLMConfigSet, NodeName  # noqa: F401

    # LLMConfigSet 应是 TypedDict
    assert hasattr(LLMConfigSet, "__annotations__")
    assert "default" in LLMConfigSet.__annotations__
    assert "overrides" in LLMConfigSet.__annotations__

    # NodeName 应是 Literal，含 4 个支持覆写的节点名（PRD §2.4 / AC-S2-11）
    import typing

    args = typing.get_args(NodeName)
    assert set(args) == {"paper_intake", "paper_analysis", "resource_scout", "planning"}, (
        f"NodeName Literal 必须严格等于 4 个节点名，实测：{args}"
    )


# ========== CP-A1-2：PaperMeta 含 3 个中文 Optional 字段 ==========


def test_cp_a1_2_paper_meta_has_zh_fields() -> None:
    """CP-A1-2: ``PaperMeta`` 含 ``title_zh`` / ``abstract_zh`` / ``tldr_zh`` 三个 Optional 字段。"""
    from typing import Optional

    from core.state import PaperMeta

    hints = get_type_hints(PaperMeta)
    for field in ("title_zh", "abstract_zh", "tldr_zh"):
        assert field in hints, f"PaperMeta 缺失字段 {field}"
        # Optional[str] 即 Union[str, None]
        assert hints[field] == Optional[str], (
            f"PaperMeta.{field} 类型应为 Optional[str]，实测：{hints[field]}"
        )


# ========== CP-A1-3：PaperAnalysis 含 2 个英文备份 Optional 字段 ==========


def test_cp_a1_3_paper_analysis_has_en_fields() -> None:
    """CP-A1-3: ``PaperAnalysis`` 含 ``method_summary_en`` / ``hardware_requirements_en`` 两个 Optional 字段。"""
    from typing import Optional

    from core.state import PaperAnalysis

    hints = get_type_hints(PaperAnalysis)
    for field in ("method_summary_en", "hardware_requirements_en"):
        assert field in hints, f"PaperAnalysis 缺失字段 {field}"
        assert hints[field] == Optional[str], (
            f"PaperAnalysis.{field} 类型应为 Optional[str]，实测：{hints[field]}"
        )


# ========== CP-A1-4：RepoInfo 含 local_path 字段 ==========


def test_cp_a1_4_repo_info_has_local_path() -> None:
    """CP-A1-4: ``RepoInfo`` 含 ``local_path: Optional[str]`` 字段。"""
    from typing import Optional

    from core.state import RepoInfo

    hints = get_type_hints(RepoInfo)
    assert "local_path" in hints, "RepoInfo 缺失字段 local_path"
    assert hints["local_path"] == Optional[str], (
        f"RepoInfo.local_path 类型应为 Optional[str]，实测：{hints['local_path']}"
    )


# ========== CP-A1-5：GlobalState 含 llm_config_set + 2 个 planning 内部字段 ==========


def test_cp_a1_5_global_state_has_new_fields() -> None:
    """CP-A1-5: ``GlobalState`` 含 ``llm_config_set`` 字段，并含 ``_planning_revise_count: int`` /
    ``_planning_user_feedback: Optional[str]``。

    设计偏差（与 dev-plan L308 不同）：
        当前实现保留 ``llm_config: LLMConfig`` 作为过渡期向后兼容镜像字段
        （见 state.py L160-170 + react_base.py:825 老路径依赖）。本断言仅强制
        ``llm_config_set`` 存在 + 两个 planning 内部字段类型正确，不强求删除 llm_config。
        待 sp2 A3 单行 diff 完成后，可在另一条单测中加 "llm_config 已移除" 的反向断言。
    """
    from typing import Optional

    from core.state import GlobalState, LLMConfigSet

    hints = get_type_hints(GlobalState)

    # llm_config_set 必须存在（sp2 权威配置源）
    assert "llm_config_set" in hints, "GlobalState 缺失 llm_config_set 字段"
    assert hints["llm_config_set"] == LLMConfigSet, (
        f"GlobalState.llm_config_set 应为 LLMConfigSet，实测：{hints['llm_config_set']}"
    )

    # _planning_revise_count: int
    assert "_planning_revise_count" in hints, "GlobalState 缺失 _planning_revise_count 字段"
    assert hints["_planning_revise_count"] is int, (
        f"GlobalState._planning_revise_count 应为 int，实测：{hints['_planning_revise_count']}"
    )

    # _planning_user_feedback: Optional[str]
    assert "_planning_user_feedback" in hints, "GlobalState 缺失 _planning_user_feedback 字段"
    assert hints["_planning_user_feedback"] == Optional[str], (
        f"GlobalState._planning_user_feedback 应为 Optional[str]，实测：{hints['_planning_user_feedback']}"
    )


# ========== CP-A1-6：create_initial_state 老形态兜底 ==========


def test_cp_a1_6_create_initial_state_legacy_llm_config_wrapping() -> None:
    """CP-A1-6: 老形态 ``LLMConfig`` 入参能正确包装为 ``{"default": cfg, "overrides": {}}``。

    这是 sp1 168/168 测试基线的关键兜底——sp1 单测全部用 LLMConfig dict 调用
    ``create_initial_state``，必须保持透明兼容。
    """
    from core.state import LLMConfig, create_initial_state

    legacy_cfg: LLMConfig = {
        "base_url": "https://example.com/v1",
        "model": "test-model",
        "api_key": "sk-test-legacy",
        "temperature": 0.3,
        "max_tokens": 8192,
    }

    state = create_initial_state(user_input="2405.14831", llm_config=legacy_cfg)

    # 兜底层把老形态包装为 LLMConfigSet
    assert state["llm_config_set"] == {
        "default": legacy_cfg,
        "overrides": {},
    }, "create_initial_state 老形态兜底失败：未正确包装为 {'default': cfg, 'overrides': {}}"

    # 过渡期镜像字段：llm_config 应等于 llm_config_set["default"]
    # （state.py L160-170 显式记录的设计约定，保 sp1 测试基线零退化）
    assert state["llm_config"] == legacy_cfg, (
        "过渡期镜像约定被破坏：state['llm_config'] 应等于 llm_config_set['default']"
    )


# ========== CP-A1-7：create_initial_state 新形态透传 ==========


def test_cp_a1_7_create_initial_state_new_llm_config_set_passthrough() -> None:
    """CP-A1-7: 新形态 ``LLMConfigSet`` 入参直接透传写入 state。"""
    from core.state import LLMConfig, LLMConfigSet, create_initial_state

    cfg_default: LLMConfig = {
        "base_url": "https://default.example/v1",
        "model": "default-model",
        "api_key": "sk-default",
        "temperature": 0.3,
        "max_tokens": 8192,
    }
    cfg_analysis: LLMConfig = {
        "base_url": "https://analysis.example/v1",
        "model": "analysis-model",
        "api_key": "sk-analysis",
        "temperature": 0.1,
        "max_tokens": 16384,
    }
    config_set: LLMConfigSet = {
        "default": cfg_default,
        "overrides": {"paper_analysis": cfg_analysis},
    }

    state = create_initial_state(user_input="2405.14831", llm_config=config_set)

    # 新形态直接透传：default + overrides 完整保留
    assert state["llm_config_set"]["default"] == cfg_default
    assert state["llm_config_set"]["overrides"] == {"paper_analysis": cfg_analysis}

    # 过渡期镜像：llm_config 应取 default（不是 override）
    assert state["llm_config"] == cfg_default, (
        "过渡期镜像应取 llm_config_set['default']，不是某个 override"
    )


def test_cp_a1_7_create_initial_state_new_form_missing_overrides_normalized() -> None:
    """CP-A1-7 补：新形态入参缺 ``overrides`` 字段时应规整为空 dict（state.py L245）。"""
    from core.state import LLMConfig, create_initial_state

    cfg: LLMConfig = {
        "base_url": "https://example.com/v1",
        "model": "test",
        "api_key": "sk-x",
        "temperature": 0.3,
        "max_tokens": 8192,
    }

    # 故意省略 overrides 键
    state = create_initial_state(user_input="2405.14831", llm_config={"default": cfg})

    assert state["llm_config_set"]["overrides"] == {}, (
        "新形态缺 overrides 时应规整为空 dict，便于下游 resolve_llm_config 安全 lookup"
    )


def test_cp_a1_7_create_initial_state_rejects_invalid_input() -> None:
    """CP-A1-7 补：既非老 LLMConfig 也非 LLMConfigSet 的入参应抛 ValueError。"""
    from core.state import create_initial_state

    # 既无 base_url 也无 default 子配置
    with pytest.raises(ValueError, match="llm_config 必须是"):
        create_initial_state(user_input="2405.14831", llm_config={"foo": "bar"})  # type: ignore[arg-type]


# ========== CP-A1-8：planning 内部字段默认值正确 ==========


def test_cp_a1_8_planning_internal_fields_defaults() -> None:
    """CP-A1-8: ``state["_planning_revise_count"] == 0`` 且 ``state["_planning_user_feedback"] is None``。"""
    from core.state import LLMConfig, create_initial_state

    cfg: LLMConfig = {
        "base_url": "https://example.com/v1",
        "model": "test",
        "api_key": "sk-x",
        "temperature": 0.3,
        "max_tokens": 8192,
    }
    state = create_initial_state(user_input="2405.14831", llm_config=cfg)

    assert state["_planning_revise_count"] == 0, (
        "_planning_revise_count 初始值应为 0（PLANNING_SOFT_HINT_THRESHOLD 计数起点）"
    )
    assert state["_planning_user_feedback"] is None, (
        "_planning_user_feedback 初始值应为 None（首次进入 planning 节点前无用户反馈）"
    )


# ========== 测试工程师补全：CP-A1-Aux 边界场景 ==========
#
# 由测试工程师代理在 A1 独立验收阶段（2026-05-27）追加，补足开发代理
# 自测矩阵未覆盖的边界场景：
#   Aux-1：_planning_revise_count 严格 int（不容忍 bool 子类型混入）
#   Aux-2：LLMConfigSet 注解结构性（default: LLMConfig + overrides: Dict[str, LLMConfig]）
#   Aux-3：PaperAnalysis 主字段 method_summary / hardware_requirements 仍存在且为 str（语义反转但字段保留）
#   Aux-4：overrides 含多节点 override（非单点）能完整透传，且 state 与入参 dict 隔离（防外部 mutate）
#   Aux-5：overrides 含非 NodeName Literal 中的 key（如 "coding"）当前实现行为—— TypedDict 运行时不强校验，记录现状
#   Aux-6：镜像不变量在新/老两种入参路径下均成立（state["llm_config"] is/== state["llm_config_set"]["default"]）


def test_cp_a1_aux_1_planning_revise_count_is_strict_int_not_bool() -> None:
    """Aux-1: ``_planning_revise_count`` 必须是严格 int（``bool`` 是 int 子类，但语义上禁止当 int 用）。

    虽然 TypedDict 在运行时不强校验类型，但默认值的实际 type 必须是 int 而非 bool，
    避免后续 ``state["_planning_revise_count"] += 1`` 时把 True/False 当 0/1 累加的隐患。
    """
    from core.state import LLMConfig, create_initial_state

    cfg: LLMConfig = {
        "base_url": "https://example.com/v1",
        "model": "test",
        "api_key": "sk-x",
        "temperature": 0.3,
        "max_tokens": 8192,
    }
    state = create_initial_state(user_input="2405.14831", llm_config=cfg)

    assert type(state["_planning_revise_count"]) is int, (
        "_planning_revise_count 默认值的运行时类型必须是 int 而非 bool 子类，"
        f"实测：{type(state['_planning_revise_count']).__name__}"
    )


def test_cp_a1_aux_2_llm_config_set_annotation_structure() -> None:
    """Aux-2: ``LLMConfigSet`` 注解结构必须含 ``default: LLMConfig`` + ``overrides: Dict[str, LLMConfig]``。

    CP-A1-1 仅断言两个键存在；本用例补强对类型注解本体的结构性检查，
    防止后续重构时把 ``overrides`` 误改成 ``Dict[NodeName, LLMConfig]``（运行时 Literal 校验代价大）
    或把 ``default`` 改成 ``Optional[LLMConfig]``（破坏"必填"语义）。
    """
    from typing import Dict, get_type_hints

    from core.state import LLMConfig, LLMConfigSet

    hints = get_type_hints(LLMConfigSet)
    assert hints["default"] is LLMConfig, (
        f"LLMConfigSet.default 必须是 LLMConfig（必填），实测：{hints['default']}"
    )
    # overrides 应为 Dict[str, LLMConfig]（架构 §2.1.1.bis 显式选择 str 而非 NodeName Literal，
    # 避免 Literal 校验导致 sp1 兜底层 dict 操作复杂化；NodeName Literal 仅用于代码注释 / 静态类型提示）
    assert hints["overrides"] == Dict[str, LLMConfig], (
        f"LLMConfigSet.overrides 必须是 Dict[str, LLMConfig]，实测：{hints['overrides']}"
    )


def test_cp_a1_aux_3_paper_analysis_main_fields_still_exist_as_str() -> None:
    """Aux-3: ``PaperAnalysis`` 主字段 ``method_summary`` / ``hardware_requirements`` 仍保留为 ``str``。

    PRD §4.7.3 / R-S2-05 的语义反转**仅改字段含义**（中文主字段 + *_en 英文备份），
    **不删除原字段**。本用例确保字段未被误删，类型仍为非 Optional 的 str（必填语义）。
    """
    from typing import get_type_hints

    from core.state import PaperAnalysis

    hints = get_type_hints(PaperAnalysis)
    for field in ("method_summary", "hardware_requirements"):
        assert field in hints, f"PaperAnalysis 主字段 {field} 被误删"
        assert hints[field] is str, (
            f"PaperAnalysis.{field} 语义反转后仍应为非 Optional 的 str，实测：{hints[field]}"
        )


def test_cp_a1_aux_4_overrides_multi_node_and_isolation_from_caller() -> None:
    """Aux-4: ``overrides`` 含多节点 override 能完整透传，且 ``state`` 与入参 dict 隔离。

    覆盖两个独立性质：
      (a) 不止单节点 override（开发代理用例仅测了 paper_analysis 单点），多节点同时覆写应一致透传。
      (b) **入参隔离**：调用方在 ``create_initial_state`` 之后 mutate 原 overrides dict
          （如追加新节点 / 修改既有节点 cfg），不应反向污染 state——
          状态字典的不变性是 LangGraph reducer 的隐含契约。

    当前实现 ``state.py`` L248 ``"overrides": dict(overrides)`` 已浅拷贝顶层 dict，
    本用例验证浅拷贝足以隔离顶层 mutate；深层 LLMConfig 对象引用不在本用例约束范围。
    """
    from core.state import LLMConfig, LLMConfigSet, create_initial_state

    cfg_default: LLMConfig = {
        "base_url": "https://default.example/v1",
        "model": "default-model",
        "api_key": "sk-default",
        "temperature": 0.3,
        "max_tokens": 8192,
    }
    cfg_analysis: LLMConfig = {
        "base_url": "https://analysis.example/v1",
        "model": "analysis-model",
        "api_key": "sk-analysis",
        "temperature": 0.1,
        "max_tokens": 16384,
    }
    cfg_planning: LLMConfig = {
        "base_url": "https://planning.example/v1",
        "model": "planning-model",
        "api_key": "sk-planning",
        "temperature": 0.5,
        "max_tokens": 4096,
    }
    mutable_overrides: dict = {
        "paper_analysis": cfg_analysis,
        "planning": cfg_planning,
    }
    config_set: LLMConfigSet = {
        "default": cfg_default,
        "overrides": mutable_overrides,
    }

    state = create_initial_state(user_input="2405.14831", llm_config=config_set)

    # (a) 多节点 override 完整透传
    assert set(state["llm_config_set"]["overrides"].keys()) == {"paper_analysis", "planning"}, (
        "多节点 overrides 透传丢失字段"
    )
    assert state["llm_config_set"]["overrides"]["paper_analysis"] == cfg_analysis
    assert state["llm_config_set"]["overrides"]["planning"] == cfg_planning

    # (b) 调用方 mutate 原 dict 不应污染 state（顶层浅拷贝隔离）
    mutable_overrides["resource_scout"] = cfg_planning  # 故意新增节点
    mutable_overrides.pop("paper_analysis", None)  # 故意删除既有节点
    assert "resource_scout" not in state["llm_config_set"]["overrides"], (
        "调用方 mutate 入参 overrides dict 反向污染了 state（顶层浅拷贝隔离失效）"
    )
    assert "paper_analysis" in state["llm_config_set"]["overrides"], (
        "调用方 mutate 入参 overrides dict 反向影响了 state（顶层浅拷贝隔离失效）"
    )


def test_cp_a1_aux_5_overrides_with_non_node_name_key_runtime_lenient() -> None:
    """Aux-5: ``overrides`` 含非 NodeName Literal 中的 key（如 ``coding``）当前实现不报错。

    NodeName Literal 仅用于静态类型提示 + ``resolve_llm_config`` 路由白名单（A2 落地），
    ``create_initial_state`` 层不做运行时 Literal 校验（架构 §2.1.1.bis 显式选择），
    透传任何字符串 key。本用例**记录现状**，若未来 A2 / A3 引入运行时 key 白名单校验，
    本用例应改为 ``pytest.raises(...)``。

    设计意图：保护 sp1 168/168 测试基线的兜底路径不被过早严格校验破坏。
    """
    from core.state import LLMConfig, LLMConfigSet, create_initial_state

    cfg_default: LLMConfig = {
        "base_url": "https://default.example/v1",
        "model": "default-model",
        "api_key": "sk-default",
        "temperature": 0.3,
        "max_tokens": 8192,
    }
    cfg_extra: LLMConfig = {
        "base_url": "https://extra.example/v1",
        "model": "extra-model",
        "api_key": "sk-extra",
        "temperature": 0.3,
        "max_tokens": 8192,
    }
    # "coding" 不在 NodeName Literal 中（仅 paper_intake / paper_analysis / resource_scout / planning）
    config_set: LLMConfigSet = {
        "default": cfg_default,
        "overrides": {"coding": cfg_extra},  # type: ignore[typeddict-item]
    }

    state = create_initial_state(user_input="2405.14831", llm_config=config_set)

    # 当前实现：宽松透传，不抛错
    assert state["llm_config_set"]["overrides"] == {"coding": cfg_extra}, (
        "create_initial_state 当前不做 NodeName Literal 运行时校验（架构 §2.1.1.bis 设计）"
    )


def test_cp_a1_aux_6_llm_config_mirror_invariant_both_paths() -> None:
    """Aux-6: 镜像不变量在新/老两种入参路径下均成立。

    state.py L160-170 显式声明：``state["llm_config"]`` 是 ``state["llm_config_set"]["default"]``
    的过渡期镜像。本用例集中断言这条不变量在四种入参形态下都成立：
      (i)   sp1 老形态 LLMConfig（含 base_url）
      (ii)  sp2 新形态 LLMConfigSet 含 overrides
      (iii) sp2 新形态 LLMConfigSet 不含 overrides 键
      (iv)  sp2 新形态 LLMConfigSet overrides 为空 dict

    A3 单行 diff 完成后，react_base.py:825 改读 ``llm_config_set`` 路径，
    届时本用例应升级为 ``"llm_config" not in state``（彻底移除字段断言）。
    """
    from core.state import LLMConfig, LLMConfigSet, create_initial_state

    cfg_a: LLMConfig = {
        "base_url": "https://a.example/v1",
        "model": "model-a",
        "api_key": "sk-a",
        "temperature": 0.3,
        "max_tokens": 8192,
    }
    cfg_b: LLMConfig = {
        "base_url": "https://b.example/v1",
        "model": "model-b",
        "api_key": "sk-b",
        "temperature": 0.5,
        "max_tokens": 4096,
    }

    # (i) 老形态
    s1 = create_initial_state(user_input="x", llm_config=cfg_a)
    assert s1["llm_config"] == s1["llm_config_set"]["default"] == cfg_a

    # (ii) 新形态含 overrides
    s2 = create_initial_state(
        user_input="x",
        llm_config={"default": cfg_a, "overrides": {"paper_analysis": cfg_b}},
    )
    assert s2["llm_config"] == s2["llm_config_set"]["default"] == cfg_a
    # 镜像取 default 不是 override
    assert s2["llm_config"] != cfg_b

    # (iii) 新形态缺 overrides 键（规整为 {}）
    s3 = create_initial_state(user_input="x", llm_config={"default": cfg_a})
    assert s3["llm_config"] == s3["llm_config_set"]["default"] == cfg_a
    assert s3["llm_config_set"]["overrides"] == {}

    # (iv) 新形态 overrides 为空 dict（显式）
    s4 = create_initial_state(
        user_input="x", llm_config={"default": cfg_a, "overrides": {}}
    )
    assert s4["llm_config"] == s4["llm_config_set"]["default"] == cfg_a
