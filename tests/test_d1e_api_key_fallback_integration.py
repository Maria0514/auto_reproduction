"""D1 增强：api_key 回退 + 安全不变量 L2 集成测试（test plan 2026-06-08 I 系列）。

补缺背景
========
D1 增强改动的 test plan（docs/sprint2/test-reports/2026-06-08_test-plan-d1-enhance.md）
§2 L2 + §3 安全不变量专项把 I01/I02（真实 SqliteSaver 落盘读回 api_key 恒空）
列为**硬门槛 #1**，但全栈开发提交的代码只补了 L1 单元（test_llm_client.py /
test_llm_config_form.py），**未落地任何 I 系列集成用例**。本文件由测试工程师在
独立验收阶段补齐，覆盖：

- T-D1E-I01/I02：真实 ``get_checkpointer(tmp db)`` + 真实 ``build_graph`` +
  ``GraphController.start_task``（api_key 留空）→ worker 跑到 planning interrupt →
  主线程 ``get_state().values`` 读回 → 断言 default + 4 override 的 api_key **全恒空**。
  **禁用 FakeGraph**（避免 D3 同款 mock 盲区），用真实 SqliteSaver 落盘读回。
  为避免真网络 / 真 LLM，把 graph.py 的 4 个节点 monkeypatch 成写最小 state 的 stub，
  planning stub 调真实 ``interrupt()`` 暂停（与真实拓扑一致）。
- T-D1E-I03：``_refresh_llm_config_set`` 静态审计——即便进程 env 有真实 LLM_API_KEY，
  返回 set 的 api_key 仍恒空（_refresh 层不回退，§2.7.2 否决）。
- T-D1E-I04/I05/I06：经真实 ``resolve_llm_config`` 选路后 ``create_llm`` 回退
  （default 路径 + override 路径），且用户显式 key 不被 env 覆盖。
- T-D1E-I07：react_base L828 消费点调用形态不变（回退对消费点透明）。

均为默认运行（不打 e2e mark），零 token、零真网络。
"""
from __future__ import annotations

import inspect
from typing import Dict
from unittest import mock

import pytest

import app as app_module
from core.checkpointer import get_checkpointer as real_get_checkpointer

# planning stub 需调真实 interrupt() 暂停 graph。
from langgraph.types import interrupt


_OVERRIDE_NODES = ("paper_intake", "paper_analysis", "resource_scout", "planning")


def _blank_config(base_url: str = "https://api.x.com/v1", model: str = "m") -> Dict:
    """构造一条 api_key 留空的合法 LLMConfig（base_url/model 填齐）。"""
    return {
        "base_url": base_url,
        "model": model,
        "api_key": "",  # 留空 → 由 create_llm 在消费层回退 .env，永不进 checkpoint。
        "temperature": 0.3,
        "max_tokens": 8192,
    }


# --------------------------------------------------------------------------- #
# graph 节点 stub：写最小 state，planning 调真实 interrupt() 暂停。
# 不触碰 react_base / create_llm / 真网络，仅验证 start_task → checkpoint 落盘链路。
# --------------------------------------------------------------------------- #
def _stub_paper_intake(state):
    return {"current_step": "paper_intake_done"}


def _stub_paper_analysis(state):
    return {"current_step": "paper_analysis_done"}


def _stub_resource_scout(state):
    return {"current_step": "resource_scout_done"}


def _stub_planning(state):
    # 调真实 interrupt() → graph 在此自然暂停（与生产 planning 同形态）。
    interrupt({"reason": "stub_planning_pause"})
    return {"current_step": "planning_done"}  # interrupt 后本行在 resume 前不会执行


def _patched_real_graph_controller(monkeypatch, tmp_path):
    """真实 SqliteSaver(tmp) + 真实 build_graph 拓扑 + 4 节点 stub，返回 GraphController。

    关键：**不** patch build_graph 本身（保留真实 add_node/add_edge/interrupt 拓扑），
    只 patch graph.py 顶层导入的 4 个节点函数为 stub，且把 get_checkpointer 指向
    tmp db（真实 SqliteSaver 落盘）。这样 start_task → _refresh → create_initial_state
    → SqliteSaver put 的整条链路都真实执行。
    """
    import core.graph as graph_module

    db_path = str(tmp_path / "d1e_invariant.sqlite")

    created = []

    def _tmp_cp(db=None):
        cp = real_get_checkpointer(db_path)
        created.append(cp)
        return cp

    monkeypatch.setattr(app_module, "get_checkpointer", _tmp_cp)
    # 在 graph.py 命名空间替换 4 节点（build_graph add_node 时引用这些名字）。
    monkeypatch.setattr(graph_module, "paper_intake", _stub_paper_intake)
    monkeypatch.setattr(graph_module, "paper_analysis", _stub_paper_analysis)
    monkeypatch.setattr(graph_module, "resource_scout", _stub_resource_scout)
    monkeypatch.setattr(graph_module, "planning", _stub_planning)

    controller = app_module.GraphController()
    return controller, created


def _read_back_checkpoint(controller, thread_id):
    """主线程经独立 main_graph 读回 checkpoint values（真实 SqliteSaver 回读）。"""
    snapshot = controller._main_graph.get_state(app_module._make_config(thread_id))
    assert snapshot is not None, "snapshot 为空，checkpoint 未落盘"
    return snapshot


# ========================================================================== #
# T-D1E-I01：default api_key 留空 → checkpoint 恒空（真实落盘读回）
# ========================================================================== #
def test_d1e_i01_blank_default_api_key_stays_empty_in_checkpoint(monkeypatch, tmp_path):
    controller, _ = _patched_real_graph_controller(monkeypatch, tmp_path)

    cfg_set = {"default": _blank_config(), "overrides": {}}
    thread_id = controller.start_task("2405.14831", cfg_set)
    controller._workers[thread_id].join(timeout=30.0)

    # 跑到 planning interrupt 暂停（不应有 worker 异常）。
    assert controller.get_worker_error(thread_id) is None, (
        f"worker 异常: {controller.get_worker_error(thread_id)}"
    )
    assert controller.is_interrupted(thread_id), "应停在 planning interrupt"

    snapshot = _read_back_checkpoint(controller, thread_id)
    lcs = snapshot.values["llm_config_set"]
    # 安全不变量：default.api_key 真实落盘读回恒空。
    assert lcs["default"]["api_key"] == "", (
        f"default.api_key 不为空，违反安全不变量：{lcs['default']['api_key']!r}"
    )
    assert lcs.get("overrides", {}) == {}


# ========================================================================== #
# T-D1E-I02：default + 4 override 全留空 api_key → 5 条 checkpoint 恒空
# ========================================================================== #
def test_d1e_i02_all_five_api_keys_stay_empty_in_checkpoint(monkeypatch, tmp_path):
    controller, _ = _patched_real_graph_controller(monkeypatch, tmp_path)

    cfg_set = {
        "default": _blank_config(),
        "overrides": {
            node: _blank_config(base_url=f"https://{node}/v1", model=f"{node}-m")
            for node in _OVERRIDE_NODES
        },
    }
    thread_id = controller.start_task("2405.14831", cfg_set)
    controller._workers[thread_id].join(timeout=30.0)

    assert controller.get_worker_error(thread_id) is None, (
        f"worker 异常: {controller.get_worker_error(thread_id)}"
    )

    snapshot = _read_back_checkpoint(controller, thread_id)
    lcs = snapshot.values["llm_config_set"]

    # 5 条 api_key（1 default + 4 override）真实落盘读回全恒空。
    assert lcs["default"]["api_key"] == "", "default.api_key 非空"
    overrides = lcs.get("overrides", {})
    assert set(overrides.keys()) == set(_OVERRIDE_NODES), (
        f"override 节点集合不符: {set(overrides.keys())}"
    )
    for node in _OVERRIDE_NODES:
        assert overrides[node]["api_key"] == "", (
            f"override[{node}].api_key 非空，违反安全不变量：{overrides[node]['api_key']!r}"
        )
        # base_url/model 应被真实落盘（证明 override 确实写入，只有 api_key 恒空）。
        assert overrides[node]["base_url"] == f"https://{node}/v1"


# ========================================================================== #
# T-D1E-I02b：即便进程 env 有真实 LLM_API_KEY，checkpoint api_key 仍恒空
# （回退只在 create_llm 进程内存发生，绝不回写 state/checkpoint）
# ========================================================================== #
def test_d1e_i02b_env_key_present_checkpoint_still_empty(monkeypatch, tmp_path):
    controller, _ = _patched_real_graph_controller(monkeypatch, tmp_path)
    # 强制 env 有真实 key——验证它绝不渗入 checkpoint。
    monkeypatch.setattr("config.get_llm_api_key", lambda: "REAL-ENV-KEY-SHOULD-NOT-LEAK")

    cfg_set = {"default": _blank_config(), "overrides": {}}
    thread_id = controller.start_task("2405.14831", cfg_set)
    controller._workers[thread_id].join(timeout=30.0)
    assert controller.get_worker_error(thread_id) is None

    snapshot = _read_back_checkpoint(controller, thread_id)
    lcs = snapshot.values["llm_config_set"]
    assert lcs["default"]["api_key"] == "", "env 真实 key 渗入了 checkpoint！"
    assert "REAL-ENV-KEY" not in repr(lcs), "env 真实 key 出现在 state，安全不变量被破坏"


# ========================================================================== #
# T-D1E-I03：_refresh_llm_config_set 静态审计——不引入真实 key
# ========================================================================== #
def test_d1e_i03_refresh_does_not_inject_env_key(monkeypatch):
    """即便 env 有真实 LLM_API_KEY，_refresh 返回的 set api_key 仍恒空。"""
    # 哪怕把 get_llm_api_key 改成返回真实 key，_refresh 也不应调用它。
    monkeypatch.setattr("config.get_llm_api_key", lambda: "REAL-ENV-KEY")

    cfg_set = {
        "default": _blank_config(),
        "overrides": {
            "paper_intake": _blank_config(base_url="https://pi/v1"),
        },
    }
    refreshed = app_module._refresh_llm_config_set(cfg_set)
    assert refreshed["default"]["api_key"] == ""
    assert refreshed["overrides"]["paper_intake"]["api_key"] == ""
    assert "REAL-ENV-KEY" not in repr(refreshed), "_refresh 把 env 真实 key 写进了 set"


# ========================================================================== #
# T-D1E-I04：真实 resolve_llm_config 选路（default 路径）→ create_llm 回退
# ========================================================================== #
def test_d1e_i04_default_route_fallback(monkeypatch):
    from core import llm_client

    captured: Dict = {}

    class _FakeCO:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(llm_client, "ChatOpenAI", _FakeCO)
    monkeypatch.setattr(llm_client, "get_llm_api_key", lambda: "ENV-KEY")

    cfg_set = {"default": _blank_config(base_url="https://default/v1"), "overrides": {}}
    # node 未在 overrides → 真实选路命中 default。
    resolved = llm_client.resolve_llm_config(cfg_set, "paper_intake")
    assert resolved["base_url"] == "https://default/v1"  # 确认选路命中 default
    llm_client.create_llm(resolved)
    # default 路径空 api_key 在 create_llm 回退 env。
    assert captured["api_key"] == "ENV-KEY"
    # 选路返回的 dict 未被回写（回退仅进程内存）。
    assert resolved["api_key"] == ""


# ========================================================================== #
# T-D1E-I05：真实 resolve_llm_config 选路（override 路径）→ create_llm 回退
# ========================================================================== #
def test_d1e_i05_override_route_fallback(monkeypatch):
    from core import llm_client

    captured: Dict = {}

    class _FakeCO:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(llm_client, "ChatOpenAI", _FakeCO)
    monkeypatch.setattr(llm_client, "get_llm_api_key", lambda: "ENV-KEY")

    cfg_set = {
        "default": _blank_config(base_url="https://default/v1"),
        "overrides": {
            "paper_analysis": _blank_config(base_url="https://override/v1", model="ov-m"),
        },
    }
    resolved = llm_client.resolve_llm_config(cfg_set, "paper_analysis")
    assert resolved["base_url"] == "https://override/v1"  # 确认命中 override
    llm_client.create_llm(resolved)
    # override 路径空 api_key 在 create_llm 回退同一 env 源（§2.7.2 override 一致规则）。
    assert captured["api_key"] == "ENV-KEY"
    assert resolved["api_key"] == ""


# ========================================================================== #
# T-D1E-I06：override 路径用户显式填了 api_key → 不回退（显式优先）
# ========================================================================== #
def test_d1e_i06_override_explicit_key_no_fallback(monkeypatch):
    from core import llm_client

    captured: Dict = {}

    class _FakeCO:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    called = {"n": 0}

    def _spy_get_key():
        called["n"] += 1
        return "ENV-KEY"

    monkeypatch.setattr(llm_client, "ChatOpenAI", _FakeCO)
    monkeypatch.setattr(llm_client, "get_llm_api_key", _spy_get_key)

    ov = _blank_config(base_url="https://override/v1", model="ov-m")
    ov["api_key"] = "sk-OVERRIDE"  # 用户显式填写
    cfg_set = {"default": _blank_config(), "overrides": {"planning": ov}}
    resolved = llm_client.resolve_llm_config(cfg_set, "planning")
    llm_client.create_llm(resolved)
    assert captured["api_key"] == "sk-OVERRIDE"  # 用户显式优先，不被 env 覆盖
    assert called["n"] == 0  # 非空短路，get_llm_api_key 未被调用


# ========================================================================== #
# T-D1E-I07：react_base L828 消费点调用形态不变（回退对消费点透明）
# ========================================================================== #
def test_d1e_i07_react_base_consumer_point_unchanged():
    """L828 仍调 create_llm(resolve_llm_config(state["llm_config_set"], node_name))。"""
    import core.react_base as rb

    src = inspect.getsource(rb)
    assert "create_llm(resolve_llm_config(state[\"llm_config_set\"], node_name))" in src, (
        "react_base 消费点调用形态变了——回退应对消费点透明（签名不变）"
    )


# ========================================================================== #
# T-D1E-I08：兜底校验失败返回 None 时不引入新 stale 写入（OBS-D1-01）
# ========================================================================== #
def test_d1e_i08_block_branch_does_not_write_session_state(monkeypatch):
    """兜底分支触发（env 空 + default api_key 空）→ 返回 None 且不写 session_state。

    用 AppTest 驱动：脚本内把 config.get_llm_api_key 改成返回空 → 触发兜底；
    断言返回 None 且 session_state 未被本次成功写入（SESSION_KEY 不被本分支落值）。
    """
    from streamlit.testing.v1 import AppTest

    script = """
import streamlit as st
import config
config.get_llm_api_key = lambda: ""  # env 无 key → 触发兜底
from ui.components.llm_config_form import render_llm_config_form, SESSION_KEY
res = render_llm_config_form()
st.session_state["_test_res"] = res
st.session_state["_test_has_key"] = SESSION_KEY in st.session_state
"""
    at = AppTest.from_string(script)
    at.run()
    assert at.session_state["_test_res"] is None
    # 兜底分支在写 session_state[SESSION_KEY] 之前 return None（不引入新 stale 写入）。
    assert at.session_state["_test_has_key"] is False
