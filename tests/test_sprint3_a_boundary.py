"""Sprint 3 阶段 A 边界补强测试（测试工程师独立验收新增）。

定位：在开发自测 tests/test_sprint3_a1.py（CP-A1-1~4）+ tests/test_sprint3_a2.py
（CP-A2-1~5）之外补强边界覆盖，沿用 sp2 验收范式（A4 补 6 / A5 补 19 边界）。

覆盖维度：
    1. timeout 类常量量纲与正数语义（按实际值判断，不强造关系）；
    2. dev_loop 预算量纲关系（DEV_LOOP_MIN_CALLS_PER_ROUND <= MAX_DEV_LOOP_LLM_CALLS）；
    3. UI 路由常量与 sp1/sp2 既有 STREAMLIT_PAGE_* 全集互异（无路由键冲突）；
    4. env 无覆盖设计声明（state 侧 + config 全量 sp3 常量 reload 不变）；
    5. create_initial_state 新字段与既有字段共存、既有默认值零破坏（抽查 sp1/sp2 关键默认值）；
    6. must-fix-1 反向行为证：最小 LangGraph 图对 node_errors 做 read-modify-write
       返回整列表，断言 last-write-wins（无 reducer 不重复累加）——这是「严禁加 reducer」
       约束的运行时正向证据，而非仅静态 grep。

约束：纯结构性 / 行为性断言，不发起真实 LLM / deepxiv / 网络请求；非 e2e，默认运行。
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from core.state import (
    GlobalState,
    LLMConfig,
    NodeError,
    create_initial_state,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent

_CFG: LLMConfig = {
    "base_url": "https://a.example/v1",
    "model": "model-a",
    "api_key": "sk-a",
    "temperature": 0.3,
    "max_tokens": 8192,
}


# ========== 1. timeout 类常量量纲与正数语义 ==========


def test_sandbox_timeouts_all_positive() -> None:
    """所有 sandbox timeout / 字节上限 / 重试次数为严格正数（防 0/负数语义错误）。"""
    import config

    for name in (
        "SANDBOX_EXEC_TIMEOUT",
        "SANDBOX_VENV_CREATE_TIMEOUT",
        "SANDBOX_PIP_INSTALL_TIMEOUT",
        "SANDBOX_OUTPUT_MAX_BYTES",
        "SANDBOX_PIP_MAX_RETRIES",
    ):
        v = getattr(config, name)
        assert isinstance(v, int) and not isinstance(v, bool), f"{name} 应为严格 int"
        assert v > 0, f"{name} 应为正数，实测 {v}"


def test_sandbox_timeout_magnitude_ordering() -> None:
    """timeout 量纲合理性（按 dev-plan L227-238 实际值判断）：
        单步执行总超时(1800) >= 单次 pip install 超时(1200) >= venv 创建超时(300)。

    依据：execution 一个步骤可能包含多次 pip install + 运行；pip install 比建 venv 更重。
    此关系由当前默认值天然成立，作为「值不被误改成颠倒量纲」的回归护栏。
    """
    import config

    assert (
        config.SANDBOX_EXEC_TIMEOUT
        >= config.SANDBOX_PIP_INSTALL_TIMEOUT
        >= config.SANDBOX_VENV_CREATE_TIMEOUT
    ), (
        "量纲应满足 EXEC(1800) >= PIP_INSTALL(1200) >= VENV_CREATE(300)，"
        f"实测 EXEC={config.SANDBOX_EXEC_TIMEOUT} "
        f"PIP={config.SANDBOX_PIP_INSTALL_TIMEOUT} "
        f"VENV={config.SANDBOX_VENV_CREATE_TIMEOUT}"
    )


def test_sandbox_output_max_bytes_is_one_mib() -> None:
    """SANDBOX_OUTPUT_MAX_BYTES 精确等于 1 MiB（1_048_576 下划线字面量 == 1048576）。"""
    import config

    assert config.SANDBOX_OUTPUT_MAX_BYTES == 1_048_576
    assert config.SANDBOX_OUTPUT_MAX_BYTES == 1048576
    assert config.SANDBOX_OUTPUT_MAX_BYTES == 1024 * 1024


# ========== 2. dev_loop 预算量纲关系 ==========


def test_dev_loop_budget_magnitude_ordering() -> None:
    """dev_loop 预算量纲：单回合最小调用数(2) <= 子预算天花板(20) <= 总预算(50)。

    覆盖 CP-A1-3 强约束的延伸：入口预算门 DEV_LOOP_MIN_CALLS_PER_ROUND 必须能在
    子预算内至少跑满 1 回合（2 <= 20），子预算又必须严格小于总预算（20 < 50）。
    """
    import config

    assert config.DEV_LOOP_MIN_CALLS_PER_ROUND <= config.MAX_DEV_LOOP_LLM_CALLS, (
        "单回合最小调用数应 <= 子预算天花板"
    )
    assert config.MAX_DEV_LOOP_LLM_CALLS < config.MAX_TOTAL_LLM_CALLS, (
        "子预算必须严格 < 总预算（CP-A1-3 / AC-S3-04②）"
    )
    # 子预算下至少可承载多少回合（仅作语义合理性观察，>=1 即可启动修复循环）
    assert config.MAX_DEV_LOOP_LLM_CALLS // config.DEV_LOOP_MIN_CALLS_PER_ROUND >= 1


def test_react_max_rounds_coding_in_reasonable_range() -> None:
    """coding 节点 ReAct 轮数(12) 为正数且与 sp1/sp2 同类常量量级一致（不畸大畸小）。"""
    import config

    assert isinstance(config.REACT_MAX_ROUNDS_CODING, int)
    assert config.REACT_MAX_ROUNDS_CODING > 0
    # 与既有 ReAct 轮数常量同量级（paper_analysis=12 / planning=8 / resource_scout=10）
    assert 1 <= config.REACT_MAX_ROUNDS_CODING <= 50


# ========== 3. UI 路由常量与 sp1/sp2 全集互异 ==========


def test_streamlit_page_routes_no_key_collision() -> None:
    """sp3 两个 UI 路由常量与 sp1/sp2 既有 STREAMLIT_PAGE_* 全集互不相同（无路由键冲突）。

    动态收集 config 中所有 STREAMLIT_PAGE_* 常量，断言去重后数量 == 总数（全互异）。
    比固定列举更稳健：未来新增页面常量若与现有撞键会被本用例捕获。
    """
    import config

    page_consts = {
        name: getattr(config, name)
        for name in dir(config)
        if name.startswith("STREAMLIT_PAGE_")
    }
    values = list(page_consts.values())
    assert len(values) >= 5, f"至少应有 sp1/sp2/sp3 共 5 个路由常量，实测 {page_consts}"
    assert all(isinstance(v, str) for v in values), "所有路由常量应为 str"
    assert len(set(values)) == len(values), (
        f"STREAMLIT_PAGE_* 路由值存在重复（路由键冲突）：{page_consts}"
    )
    # 显式确认 sp3 两页在全集中且互异
    assert config.STREAMLIT_PAGE_EXECUTION in values
    assert config.STREAMLIT_PAGE_REPORT in values
    assert config.STREAMLIT_PAGE_EXECUTION != config.STREAMLIT_PAGE_REPORT


# ========== 4. env 无覆盖设计声明（sp3 全量常量 reload 不变） ==========


def test_no_env_override_for_all_sp3_constants(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A4 既有先例「env 无覆盖设计声明」全量版：设同名 env 后 reload config，
    sp3 全部 10 个常量值不变（沿用 sp1/sp2 字面量风格，无 os.getenv 覆盖）。

    test_sprint3_a1.py 仅抽查 4 个，此处覆盖全部 10 个常量，且包含数值/字符串两类。
    """
    import config as config_module

    env_overrides = {
        "SANDBOX_EXEC_TIMEOUT": "9",
        "SANDBOX_VENV_CREATE_TIMEOUT": "9",
        "SANDBOX_PIP_INSTALL_TIMEOUT": "9",
        "SANDBOX_OUTPUT_MAX_BYTES": "9",
        "SANDBOX_PIP_MAX_RETRIES": "9",
        "MAX_DEV_LOOP_LLM_CALLS": "9",
        "DEV_LOOP_MIN_CALLS_PER_ROUND": "9",
        "REACT_MAX_ROUNDS_CODING": "9",
        "STREAMLIT_PAGE_EXECUTION": "evil",
        "STREAMLIT_PAGE_REPORT": "evil",
    }
    for k, v in env_overrides.items():
        monkeypatch.setenv(k, v)

    expected = {
        "SANDBOX_EXEC_TIMEOUT": 1800,
        "SANDBOX_VENV_CREATE_TIMEOUT": 300,
        "SANDBOX_PIP_INSTALL_TIMEOUT": 1200,
        "SANDBOX_OUTPUT_MAX_BYTES": 1_048_576,
        "SANDBOX_PIP_MAX_RETRIES": 2,
        "MAX_DEV_LOOP_LLM_CALLS": 20,
        "DEV_LOOP_MIN_CALLS_PER_ROUND": 2,
        "REACT_MAX_ROUNDS_CODING": 12,
        "STREAMLIT_PAGE_EXECUTION": "execution",
        "STREAMLIT_PAGE_REPORT": "report",
    }
    reloaded = importlib.reload(config_module)
    try:
        for name, exp in expected.items():
            assert getattr(reloaded, name) == exp, (
                f"{name} 不应被 env 覆盖，实测 {getattr(reloaded, name)!r} 期望 {exp!r}"
            )
    finally:
        for k in env_overrides:
            monkeypatch.delenv(k, raising=False)
        importlib.reload(config_module)


# ========== 5. create_initial_state 既有默认值零破坏（抽查 sp1/sp2） ==========


def test_create_initial_state_legacy_defaults_intact() -> None:
    """新增 2 字段后，create_initial_state 既有 sp1/sp2 关键默认值零破坏（抽查）。

    覆盖补强方向：「新字段与既有字段共存、不破坏既有默认值」。
    """
    state = create_initial_state("2103.00020", _CFG)

    # sp1 预算 / 错误追踪默认值
    assert state["retry_budget_remaining"] == 50  # == MAX_TOTAL_LLM_CALLS
    assert state["fix_loop_count"] == 0
    assert state["node_errors"] == []
    assert state["degraded_nodes"] == []
    assert state["fix_loop_history"] == []
    assert state["user_fix_decision"] is None
    assert state["error"] is None
    assert state["current_step"] == "start"
    assert state["sandbox_type"] == "venv"
    assert state["execution_mode"].value == "full"
    # sp2 planning 内部字段默认值
    assert state["_planning_revise_count"] == 0
    assert state["_planning_user_feedback"] is None
    assert state["_planning_pending_repo_url"] is None
    assert state["_planning_switch_failed"] is False
    # sp3 新增字段默认值（与既有字段共存）
    assert state["_dev_loop_route"] is None
    assert state["_dev_loop_llm_calls"] == 0


def test_create_initial_state_custom_workspace_does_not_touch_new_fields() -> None:
    """传入自定义 workspace_dir 时，sp3 新字段仍取默认值（参数互不干扰）。"""
    state = create_initial_state("2103.00020", _CFG, workspace_dir="/tmp/ws-xyz")
    assert state["workspace_dir"] == "/tmp/ws-xyz"
    assert state["_dev_loop_route"] is None
    assert state["_dev_loop_llm_calls"] == 0


# ========== 6. must-fix-1 反向行为证：node_errors 无 reducer last-write-wins ==========


def _mk_node_error(node_name: str) -> NodeError:
    return {
        "node_name": node_name,
        "error_type": "TestError",
        "error_message": "boundary-probe",
        "error_detail": None,
        "timestamp": "2026-06-23T00:00:00",
        "retry_count": 0,
        "resolved": False,
    }


def test_node_errors_no_reducer_last_write_wins_via_minimal_graph() -> None:
    """must-fix-1 反向行为证（运行时正向证据，非仅静态 grep）：

    构造最小 LangGraph 图：node_a 读 node_errors（初始 []）追加 1 条返回整列表，
    node_b 再读（应看到 node_a 的 1 条）追加 1 条返回整列表。
    若 node_errors 误加了 Annotated[List, operator.add] reducer，则两次「返回整列表」
    会被 reducer 累加 → 最终长度为 1 + (1+2) = 异常膨胀（重复累加）。
    无 reducer（last-write-wins）下，最终长度恰为 2，每条 node_name 唯一。

    这正是 CP-A2-3 grep 红线要保护的运行时契约：sp1/sp2 全部既有节点都用
    「read-modify-write 返回整列表」写法，加 reducer 会破坏它们。
    """
    from langgraph.graph import StateGraph, START, END

    def node_a(state: GlobalState) -> dict:
        current = list(state["node_errors"])
        current.append(_mk_node_error("node_a"))
        return {"node_errors": current}

    def node_b(state: GlobalState) -> dict:
        # 关键：node_b 读到的应是 node_a 写入后的列表（长度 1），而非空
        current = list(state["node_errors"])
        assert len(current) == 1, (
            f"node_b 应读到 node_a 写入的 1 条，实测 {len(current)} 条 "
            f"(若读到 0 条说明状态未传递；若 >1 说明被 reducer 重复累加)"
        )
        current.append(_mk_node_error("node_b"))
        return {"node_errors": current}

    builder = StateGraph(GlobalState)
    builder.add_node("node_a", node_a)
    builder.add_node("node_b", node_b)
    builder.add_edge(START, "node_a")
    builder.add_edge("node_a", "node_b")
    builder.add_edge("node_b", END)
    graph = builder.compile()

    init = create_initial_state("2103.00020", _CFG)
    final = graph.invoke(init)

    names = [e["node_name"] for e in final["node_errors"]]
    assert len(final["node_errors"]) == 2, (
        f"无 reducer last-write-wins 下最终应恰为 2 条，实测 {len(final['node_errors'])} 条："
        f"{names}（>2 表明误加了 operator.add reducer，违反 must-fix-1）"
    )
    assert names == ["node_a", "node_b"], (
        f"两条记录顺序应为 [node_a, node_b]，实测 {names}"
    )


def test_degraded_nodes_no_reducer_last_write_wins_via_minimal_graph() -> None:
    """must-fix-1 反向行为证（degraded_nodes 同款）：read-modify-write 返回整列表，
    无 reducer 下 last-write-wins，最终恰 2 条且无重复累加。"""
    from langgraph.graph import StateGraph, START, END

    def node_a(state: GlobalState) -> dict:
        return {"degraded_nodes": list(state["degraded_nodes"]) + ["paper_intake"]}

    def node_b(state: GlobalState) -> dict:
        assert state["degraded_nodes"] == ["paper_intake"], (
            f"node_b 应读到 ['paper_intake']，实测 {state['degraded_nodes']}"
        )
        return {"degraded_nodes": list(state["degraded_nodes"]) + ["resource_scout"]}

    builder = StateGraph(GlobalState)
    builder.add_node("node_a", node_a)
    builder.add_node("node_b", node_b)
    builder.add_edge(START, "node_a")
    builder.add_edge("node_a", "node_b")
    builder.add_edge("node_b", END)
    graph = builder.compile()

    final = graph.invoke(create_initial_state("2103.00020", _CFG))
    assert final["degraded_nodes"] == ["paper_intake", "resource_scout"], (
        f"无 reducer 下应恰为 ['paper_intake','resource_scout']，"
        f"实测 {final['degraded_nodes']}（重复或膨胀表明误加 reducer）"
    )
