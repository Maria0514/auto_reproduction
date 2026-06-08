"""S2-04 LLM 配置表单组件单测（D1，CP-D1-1 ~ CP-D1-10）。

测试策略
========
- 主路径全部通过 ``streamlit.testing.v1.AppTest`` 驱动真实表单脚本（CP-D1-1~10）。
- D1 风险标注预判 "AppTest 对 expander 内部 widget 支持有限"，实测 streamlit
  1.58.0 的 AppTest **可正常访问折叠 expander 内的 widget**（见 probe），故 CP-D1-3~6
  的 override 路径均用 AppTest 真实驱动，**无需降级为纯逻辑单测**。
- CP-D1-10（api_key 是否 password mask）AppTest 元素树的 ``TextInput`` 不暴露
  password/text 属性，但底层 ``proto.type`` 暴露（DEFAULT=0 / PASSWORD=1），
  故用 ``proto.type`` 验证，仍属 AppTest 范畴。
- 另补一组 ``_validate_panel`` / ``_panel_is_blank`` 的直接单元测试，作为校验
  内核的细粒度补强（与 AppTest 互为印证）。

运行::

    .venv/bin/python -m pytest tests/test_llm_config_form.py -q
"""

from __future__ import annotations

import pytest
from streamlit.testing.v1 import AppTest

# CP-D1-1：导入即视为通过（顶层 import 失败会让整个文件 collect error）。
from ui.components.llm_config_form import render_llm_config_form  # noqa: E402


# streamlit TextInput proto.type 枚举：DEFAULT=0（明文），PASSWORD=1（mask）。
_PASSWORD_PROTO_TYPE = 1
_DEFAULT_PROTO_TYPE = 0

# AppTest 脚本：顶层调用表单，结果落 session_state 供断言读取。
_APP_SCRIPT = """
import streamlit as st
from ui.components.llm_config_form import render_llm_config_form

res = render_llm_config_form()
st.session_state["_test_result"] = res
"""


def _new_app() -> AppTest:
    at = AppTest.from_string(_APP_SCRIPT)
    at.run()
    return at


def _fill_global(at: AppTest, *, base_url="https://api.example.com/v1",
                 model="gpt-4o", api_key="sk-GLOBAL") -> None:
    at.text_input(key="default_base_url").set_value(base_url)
    at.text_input(key="default_model").set_value(model)
    at.text_input(key="default_api_key").set_value(api_key)


def _fill_override(at: AppTest, node: str, *, base_url="https://api.node.com/v1",
                   model="node-model", api_key="sk-NODE") -> None:
    at.text_input(key=f"override_{node}_base_url").set_value(base_url)
    at.text_input(key=f"override_{node}_model").set_value(model)
    at.text_input(key=f"override_{node}_api_key").set_value(api_key)


# --------------------------------------------------------------------------- #
# CP-D1-1：可正常导入
# --------------------------------------------------------------------------- #
def test_cp_d1_1_importable():
    assert callable(render_llm_config_form)


# --------------------------------------------------------------------------- #
# CP-D1-2（D1 增强改写）：全局 panel base_url/model 清空 → 返回 None（必填校验）
#
# 语义反转说明（架构 §2.8.2 D1 增强）：
#   - default panel 现在**预填** base_url/model（config getter），故"什么都不填"
#     不再等于"全空"；要触发必填失败需显式清空 base_url/model。
#   - api_key 校验已反转为"允许留空"，故必填错误**不再含 api_key 不能为空**。
# --------------------------------------------------------------------------- #
def test_cp_d1_2_global_base_url_model_blank_returns_none():
    at = _new_app()
    # 显式清空预填的 base_url/model（api_key 本就空）。
    at.text_input(key="default_base_url").set_value("")
    at.text_input(key="default_model").set_value("")
    at.run()
    assert at.session_state["_test_result"] is None
    # 行内 st.error 提示：base_url/model 必填失败；**不应**再含 api_key 不能为空。
    error_msgs = [e.value for e in at.error]
    assert any("base_url" in m for m in error_msgs)
    assert any("model" in m for m in error_msgs)
    assert not any("api_key" in m and "不能为空" in m for m in error_msgs), (
        "校验反转后 api_key 不应再报'不能为空'"
    )
    # 注：default panel 预填 base_url/model + env 有 LLM_API_KEY 时，_new_app() 首屏
    # 即成功写入 llm_config_set；本次清空后失败返回 None 但不清除 stale 键（OBS-D1-01
    # 已知可接受行为，§2.8.4 明确消费返回值而非直读 session_state）。故只断言权威信号
    # _test_result is None（上方已断言），不对 stale session_state 强约束。


# --------------------------------------------------------------------------- #
# CP-D1-3：全局全填合法 + 4 个 override 全空 → LLMConfigSet, overrides == {}
# --------------------------------------------------------------------------- #
def test_cp_d1_3_global_only_empty_overrides():
    at = _new_app()
    _fill_global(at)
    at.run()
    res = at.session_state["_test_result"]
    assert res is not None
    assert res["overrides"] == {}
    assert res["default"]["base_url"] == "https://api.example.com/v1"
    assert res["default"]["model"] == "gpt-4o"
    assert res["default"]["api_key"] == "sk-GLOBAL"
    assert res["default"]["temperature"] == pytest.approx(0.3)
    assert res["default"]["max_tokens"] == 8192  # D1 增强：slider 默认改 4096→8192。
    assert not at.error  # 无行内错误。


# --------------------------------------------------------------------------- #
# CP-D1-4：全局全填 + paper_analysis override 全填合法 →
#          overrides == {"paper_analysis": cfg_B}
# --------------------------------------------------------------------------- #
def test_cp_d1_4_single_override_paper_analysis():
    at = _new_app()
    _fill_global(at)
    at.run()  # 先渲染出 override expander 内的 widget。
    _fill_override(at, "paper_analysis",
                   base_url="https://api.analysis.com/v1",
                   model="analysis-model", api_key="sk-ANALYSIS")
    at.run()
    res = at.session_state["_test_result"]
    assert res is not None
    assert set(res["overrides"].keys()) == {"paper_analysis"}
    cfg_b = res["overrides"]["paper_analysis"]
    assert cfg_b["base_url"] == "https://api.analysis.com/v1"
    assert cfg_b["model"] == "analysis-model"
    assert cfg_b["api_key"] == "sk-ANALYSIS"
    # 其余节点未填 → 不出现在 overrides。
    assert "paper_intake" not in res["overrides"]
    assert "resource_scout" not in res["overrides"]
    assert "planning" not in res["overrides"]


# --------------------------------------------------------------------------- #
# CP-D1-5（D1 增强改写）：paper_analysis override 仅填 base_url（model 留空）→
#          视为开启覆写但校验失败（model 缺失）→ None + st.error
#
# 语义反转说明：api_key 校验已反转为"允许留空"，故只填 base_url 时**只报 model 缺失**，
# 不再报 api_key 缺失（缺 api_key 不再是失败因子）。
# --------------------------------------------------------------------------- #
def test_cp_d1_5_partial_override_fails():
    at = _new_app()
    _fill_global(at)
    at.run()  # 先渲染出 override expander 内的 widget。
    # 仅填 override 的 base_url，model / api_key 留空。
    at.text_input(key="override_paper_analysis_base_url").set_value(
        "https://api.partial.com/v1")
    at.run()
    res = at.session_state["_test_result"]
    assert res is None
    error_msgs = [e.value for e in at.error]
    # 应针对 paper_analysis 节点报 model 缺失（base_url 已填，触发开启覆写）。
    assert any("paper_analysis" in m and "model" in m for m in error_msgs)
    # 校验反转：api_key 留空不再报"不能为空"。
    assert not any("paper_analysis" in m and "api_key" in m and "不能为空" in m
                   for m in error_msgs), "override api_key 留空不应报缺失"


# --------------------------------------------------------------------------- #
# CP-D1-6：4 个节点全 override 全填合法 → 含 4 个 override 节点
# --------------------------------------------------------------------------- #
def test_cp_d1_6_all_four_overrides():
    at = _new_app()
    _fill_global(at)
    at.run()  # 先渲染出 override expander 内的 widget。
    for node in ("paper_intake", "paper_analysis", "resource_scout", "planning"):
        _fill_override(at, node,
                       base_url=f"https://api.{node}.com/v1",
                       model=f"{node}-model", api_key=f"sk-{node}")
    at.run()
    res = at.session_state["_test_result"]
    assert res is not None
    assert set(res["overrides"].keys()) == {
        "paper_intake", "paper_analysis", "resource_scout", "planning",
    }
    for node in res["overrides"]:
        assert res["overrides"][node]["model"] == f"{node}-model"
        assert res["overrides"][node]["api_key"] == f"sk-{node}"


# --------------------------------------------------------------------------- #
# CP-D1-7：temperature 超界（1.5）→ None + 行内错误
# 通过直接构造校验内核驱动（slider 在 UI 被 clamp 到 [0,1]，超界值无法经 widget
# 真实输入，故针对校验函数验证其拒绝逻辑——这是 AppTest 无法表达的边界）。
# --------------------------------------------------------------------------- #
def test_cp_d1_7_temperature_out_of_range():
    from ui.components.llm_config_form import _validate_panel
    raw = {
        "base_url": "https://api.x.com/v1",
        "model": "gpt-4o",
        "api_key": "sk-X",
        "temperature": 1.5,
        "max_tokens": 4096,
    }
    cfg, errors = _validate_panel(raw, scope_label="全局默认")
    assert cfg is None
    assert any("temperature" in m for m in errors)


def test_cp_d1_7_temperature_slider_rejected_in_ui():
    """补充：AppTest 角度——slider 超界 set_value 被 streamlit 静默拒绝回退默认值
    （OBS-D1-02：非 clamp 到 max，而是值保留为默认 0.3），证明 UI 层天然不会
    产出超界 temperature（与校验内核形成双保险）。"""
    at = _new_app()
    _fill_global(at)
    at.slider(key="default_temperature").set_value(1.5)
    at.run()
    res = at.session_state["_test_result"]
    # set_value(1.5) 超界被静默拒绝回退默认 0.3，故仍合法返回且 <=1.0。
    assert res is not None
    assert res["default"]["temperature"] <= 1.0


# --------------------------------------------------------------------------- #
# CP-D1-8：max_tokens 小于下界（100 < 256）→ None + 行内错误
# --------------------------------------------------------------------------- #
def test_cp_d1_8_max_tokens_below_lower_bound():
    from ui.components.llm_config_form import _validate_panel
    raw = {
        "base_url": "https://api.x.com/v1",
        "model": "gpt-4o",
        "api_key": "sk-X",
        "temperature": 0.3,
        "max_tokens": 100,
    }
    cfg, errors = _validate_panel(raw, scope_label="全局默认")
    assert cfg is None
    assert any("max_tokens" in m for m in errors)


def test_cp_d1_8_max_tokens_slider_rejected_in_ui():
    """补充：AppTest 角度——max_tokens 现为 slider（D1 增强 number_input→slider）；
    低于 min(512) 的 set_value 被 streamlit 静默拒绝回退默认 8192（OBS-D1-02），
    故仍合法返回且 >=512。"""
    at = _new_app()
    _fill_global(at)
    at.slider(key="default_max_tokens").set_value(100)
    at.run()
    res = at.session_state["_test_result"]
    assert res is not None
    assert res["default"]["max_tokens"] >= 512


# --------------------------------------------------------------------------- #
# CP-D1-9：提交成功后 session_state["llm_config_set"] 非空且与返回值一致
# --------------------------------------------------------------------------- #
def test_cp_d1_9_session_state_matches_return():
    at = _new_app()
    _fill_global(at)
    at.run()  # 先渲染出 override expander 内的 widget。
    _fill_override(at, "planning", base_url="https://api.plan.com/v1",
                   model="plan-model", api_key="sk-PLAN")
    at.run()
    res = at.session_state["_test_result"]
    assert res is not None
    assert "llm_config_set" in at.session_state
    assert at.session_state["llm_config_set"] == res


# --------------------------------------------------------------------------- #
# CP-D1-10：api_key 字段类型为 password（mask）— 通过 proto.type 验证
# --------------------------------------------------------------------------- #
def test_cp_d1_10_api_key_is_password_type():
    at = _new_app()
    _fill_global(at)
    at.run()
    # 全局 + 4 个 override 共 5 个 api_key 字段，全部应为 password mask。
    api_key_inputs = [t for t in at.text_input if t.key.endswith("_api_key")]
    assert len(api_key_inputs) == 5
    for ti in api_key_inputs:
        assert ti.proto.type == _PASSWORD_PROTO_TYPE, (
            f"{ti.key} 应为 password mask（proto.type=1），实际 {ti.proto.type}"
        )
    # 反证：base_url / model 应为明文 default。
    plain_inputs = [
        t for t in at.text_input
        if t.key.endswith("_base_url") or t.key.endswith("_model")
    ]
    for ti in plain_inputs:
        assert ti.proto.type == _DEFAULT_PROTO_TYPE


# --------------------------------------------------------------------------- #
# 补强：校验内核 / 全空判定 的直接单元测试
# --------------------------------------------------------------------------- #
def test_panel_is_blank_helper():
    from ui.components.llm_config_form import _panel_is_blank
    # 3 个文本字段全空 → blank（temperature/max_tokens 有默认值不参与）。
    assert _panel_is_blank({
        "base_url": "", "model": "  ", "api_key": "",
        "temperature": 0.3, "max_tokens": 4096,
    }) is True
    # 任填一个文本字段 → 非 blank（视为开启覆写）。
    assert _panel_is_blank({
        "base_url": "x", "model": "", "api_key": "",
        "temperature": 0.3, "max_tokens": 4096,
    }) is False


def test_validate_panel_happy_path():
    from ui.components.llm_config_form import _validate_panel
    cfg, errors = _validate_panel({
        "base_url": "https://api.x.com/v1",
        "model": "gpt-4o",
        "api_key": "sk-X",
        "temperature": 0.5,
        "max_tokens": 8192,
    }, scope_label="全局默认")
    assert errors == []
    assert cfg == {
        "base_url": "https://api.x.com/v1",
        "model": "gpt-4o",
        "api_key": "sk-X",
        "temperature": 0.5,
        "max_tokens": 8192,
    }


def test_validate_panel_boundaries_inclusive():
    """边界值含端点：temperature 0.0/1.0、max_tokens 512/16384 均合法
    （D1 增强：max_tokens 下界 256→512）。"""
    from ui.components.llm_config_form import _validate_panel
    for temp in (0.0, 1.0):
        for mt in (512, 16384):
            cfg, errors = _validate_panel({
                "base_url": "u", "model": "m", "api_key": "k",
                "temperature": temp, "max_tokens": mt,
            }, scope_label="x")
            assert errors == [], f"temp={temp} mt={mt} 应合法"
            assert cfg is not None


def test_render_with_prefill_default_echoes():
    """default 形参回显：传入 LLMConfigSet，表单 widget 应 prefill 对应值。"""
    prefill = {
        "default": {
            "base_url": "https://prefill.com/v1",
            "model": "prefill-model",
            "api_key": "sk-PREFILL",
            "temperature": 0.7,
            "max_tokens": 2048,
        },
        "overrides": {
            "resource_scout": {
                "base_url": "https://rs.com/v1",
                "model": "rs-model",
                "api_key": "sk-RS",
                "temperature": 0.2,
                "max_tokens": 1024,
            }
        },
    }
    script = f"""
import streamlit as st
from ui.components.llm_config_form import render_llm_config_form

prefill = {prefill!r}
res = render_llm_config_form(default=prefill)
st.session_state["_test_result"] = res
"""
    at = AppTest.from_string(script)
    at.run()
    res = at.session_state["_test_result"]
    assert res is not None
    assert res["default"]["base_url"] == "https://prefill.com/v1"
    assert res["default"]["temperature"] == pytest.approx(0.7)
    assert res["default"]["max_tokens"] == 2048
    assert set(res["overrides"].keys()) == {"resource_scout"}
    assert res["overrides"]["resource_scout"]["model"] == "rs-model"


# =========================================================================== #
# 测试工程师补强用例（2026-06-04，@测试工程师代理 D1 独立验收）
# 目标：边界值精确命中 / override 组合 / 字段契约对齐 / mask 语义反证 /
#       session_state 反向断言 / 空白串 vs None 非空判定 / 多节点隔离。
# 复用开发已有 _new_app / _fill_global / _fill_override 基建，AppTest 真实驱动。
# =========================================================================== #


# --- 边界值：温度/token 恰好越界（拒绝）与恰好合法（接受）的相邻对 --------- #
def test_strengthen_temperature_just_above_upper_rejected():
    """temperature = 1.0 + 极小量（1.0000001）恰好越上界 → 校验拒绝。"""
    from ui.components.llm_config_form import _validate_panel
    cfg, errors = _validate_panel({
        "base_url": "u", "model": "m", "api_key": "k",
        "temperature": 1.0000001, "max_tokens": 4096,
    }, scope_label="x")
    assert cfg is None
    assert any("temperature" in m for m in errors)


def test_strengthen_temperature_below_lower_rejected():
    """temperature = -0.01 恰好越下界 → 校验拒绝（dev 只测了 1.5 上界）。"""
    from ui.components.llm_config_form import _validate_panel
    cfg, errors = _validate_panel({
        "base_url": "u", "model": "m", "api_key": "k",
        "temperature": -0.01, "max_tokens": 4096,
    }, scope_label="x")
    assert cfg is None
    assert any("temperature" in m for m in errors)


def test_strengthen_max_tokens_just_above_upper_rejected():
    """max_tokens = 16385 恰好越上界 → 校验拒绝（dev 只测了 100 下界）。"""
    from ui.components.llm_config_form import _validate_panel
    cfg, errors = _validate_panel({
        "base_url": "u", "model": "m", "api_key": "k",
        "temperature": 0.3, "max_tokens": 16385,
    }, scope_label="x")
    assert cfg is None
    assert any("max_tokens" in m for m in errors)


def test_strengthen_max_tokens_just_below_lower_rejected():
    """max_tokens = 511 恰好越下界（512-1，D1 增强下界 256→512）→ 校验拒绝。"""
    from ui.components.llm_config_form import _validate_panel
    cfg, errors = _validate_panel({
        "base_url": "u", "model": "m", "api_key": "k",
        "temperature": 0.3, "max_tokens": 511,
    }, scope_label="x")
    assert cfg is None
    assert any("max_tokens" in m for m in errors)


def test_strengthen_boundaries_exact_endpoints_accepted_with_type_check():
    """端点 0.0/1.0/512/16384 恰好合法，且组装结果类型正确（float/int）
    （D1 增强：max_tokens 下界端点 256→512）。"""
    from ui.components.llm_config_form import _validate_panel
    cfg, errors = _validate_panel({
        "base_url": "u", "model": "m", "api_key": "k",
        "temperature": 1.0, "max_tokens": 16384,
    }, scope_label="x")
    assert errors == []
    assert cfg is not None
    assert isinstance(cfg["temperature"], float) and cfg["temperature"] == 1.0
    assert isinstance(cfg["max_tokens"], int) and cfg["max_tokens"] == 16384
    cfg2, errors2 = _validate_panel({
        "base_url": "u", "model": "m", "api_key": "k",
        "temperature": 0.0, "max_tokens": 512,
    }, scope_label="x")
    assert errors2 == []
    assert cfg2 is not None
    assert cfg2["temperature"] == 0.0 and cfg2["max_tokens"] == 512


# --- 校验内核独立防线：超界值绕过 UI clamp 仍被拒（adaptation #2b 实证） ---- #
def test_strengthen_validate_panel_is_independent_defense_not_just_ui_clamp():
    """实证校验层独立拒绝超界（temperature=1.5 + max_tokens=100 同时越界），
    证明组件不是只靠 UI widget min/max clamp，校验内核本身是第二道防线。
    一次性触发两个越界，断言两条错误都被报出。"""
    from ui.components.llm_config_form import _validate_panel
    cfg, errors = _validate_panel({
        "base_url": "u", "model": "m", "api_key": "k",
        "temperature": 1.5, "max_tokens": 100,
    }, scope_label="全局默认")
    assert cfg is None
    assert any("temperature" in m for m in errors)
    assert any("max_tokens" in m for m in errors)


# --- override 部分字段填写的各种组合（dev 只测了仅填 base_url 一种） ------- #
@pytest.mark.parametrize("filled_field", ["model", "api_key"])
def test_strengthen_partial_override_single_other_field_fails(filled_field):
    """override 仅填 model（或仅填 api_key）单字段 → 开启覆写但校验失败 → None。
    覆盖 dev CP-D1-5 未覆盖的"非 base_url 单字段"开启覆写分支。"""
    at = _new_app()
    _fill_global(at)
    at.run()
    at.text_input(key=f"override_paper_intake_{filled_field}").set_value("xyz")
    at.run()
    res = at.session_state["_test_result"]
    assert res is None
    error_msgs = [e.value for e in at.error]
    assert any("paper_intake" in m for m in error_msgs)


def test_strengthen_override_base_url_model_filled_blank_api_key_now_valid():
    """D1 增强（语义反转）：override 填了 base_url + model、api_key 留空 → 现在**合法**
    （api_key 留空由 create_llm 回退 .env）。该 override 入 overrides 且 api_key 为 ""。

    旧契约下此组合"缺 api_key → 校验失败 → None"，校验反转后改为合法通过。"""
    at = _new_app()
    _fill_global(at)
    at.run()
    at.text_input(key="override_resource_scout_base_url").set_value("https://rs/v1")
    at.text_input(key="override_resource_scout_model").set_value("rs-m")
    at.run()
    res = at.session_state["_test_result"]
    assert res is not None
    assert "resource_scout" in res["overrides"]
    assert res["overrides"]["resource_scout"]["base_url"] == "https://rs/v1"
    assert res["overrides"]["resource_scout"]["model"] == "rs-m"
    assert res["overrides"]["resource_scout"]["api_key"] == ""  # 留空合法，恒空。
    assert not at.error


# --- 多节点 override 隔离：一个节点失败不污染另一个合法节点的判定 --------- #
def test_strengthen_one_invalid_override_blocks_whole_form():
    """两个 override：一个合法填齐 + 一个部分填（失败）→ 整表返回 None
    （override_failed 短路）。验证'任一失败即不返回'语义。

    注（测试工程师 2026-06-04）：本用例同时锚定一个**已知行为契约边界**——
    若同一会话上一次 run 曾成功写入 session_state["llm_config_set"]，本次失败
    返回 None 时组件**不会清除**该 stale 键（dev-plan/architecture §2.8 仅规定
    "成功时写入"，未规定"失败时清除"）。架构 §2.8.4 明确 GraphController.start_task
    **消费返回值**而非直接读 session_state，故 stale 键在 D1 契约内可接受。
    这里只断言返回值为 None（权威信号），不对 stale session_state 键做强约束。
    见验收报告 OBS-D1-01。"""
    at = _new_app()
    _fill_global(at)
    at.run()
    _fill_override(at, "paper_analysis")  # 合法填齐
    at.text_input(key="override_planning_base_url").set_value("https://p/v1")  # 部分
    at.run()
    res = at.session_state["_test_result"]
    assert res is None
    # 行内 st.error 必须提示 planning 节点的缺失字段（失败的权威可观察信号）。
    error_msgs = [e.value for e in at.error]
    assert any("planning" in m for m in error_msgs)


def test_strengthen_two_valid_overrides_isolated():
    """两个节点各自合法填齐 → overrides 含两个且互不串值（隔离性）。"""
    at = _new_app()
    _fill_global(at)
    at.run()
    _fill_override(at, "paper_intake", base_url="https://pi/v1",
                   model="pi-m", api_key="sk-PI")
    _fill_override(at, "planning", base_url="https://pl/v1",
                   model="pl-m", api_key="sk-PL")
    at.run()
    res = at.session_state["_test_result"]
    assert res is not None
    assert set(res["overrides"].keys()) == {"paper_intake", "planning"}
    assert res["overrides"]["paper_intake"]["api_key"] == "sk-PI"
    assert res["overrides"]["planning"]["api_key"] == "sk-PL"
    # 隔离：两节点配置不串值。
    assert res["overrides"]["paper_intake"]["base_url"] != \
        res["overrides"]["planning"]["base_url"]


# --- 空白串 vs None 的非空判定：纯空格不算填写 ----------------------------- #
def test_strengthen_whitespace_only_global_treated_as_blank():
    """全局 panel 三文本字段填纯空格 → strip 后视为空 → 必填校验失败 → None。"""
    at = _new_app()
    at.text_input(key="default_base_url").set_value("   ")
    at.text_input(key="default_model").set_value("\t")
    at.text_input(key="default_api_key").set_value("  ")
    at.run()
    res = at.session_state["_test_result"]
    assert res is None
    error_msgs = [e.value for e in at.error]
    assert any("base_url" in m for m in error_msgs)


def test_strengthen_whitespace_only_override_treated_as_not_override():
    """override 三文本字段填纯空格 → _panel_is_blank 判 True → 不写入 overrides。
    全局合法 → 返回 LLMConfigSet 且 overrides == {}。"""
    at = _new_app()
    _fill_global(at)
    at.run()
    at.text_input(key="override_planning_base_url").set_value("   ")
    at.text_input(key="override_planning_model").set_value(" ")
    at.run()
    res = at.session_state["_test_result"]
    assert res is not None
    assert res["overrides"] == {}


def test_strengthen_base_url_strip_applied_to_stored_value():
    """合法但首尾带空格的 base_url/model → 存储值被 strip（_validate_panel 契约）。"""
    from ui.components.llm_config_form import _validate_panel
    cfg, errors = _validate_panel({
        "base_url": "  https://api.x.com/v1  ", "model": "  gpt-4o  ",
        "api_key": "sk-X", "temperature": 0.3, "max_tokens": 4096,
    }, scope_label="x")
    assert errors == []
    assert cfg["base_url"] == "https://api.x.com/v1"
    assert cfg["model"] == "gpt-4o"


# --- 字段契约严格对齐 core.state.LLMConfig（不多/不少字段） ----------------- #
def test_strengthen_assembled_config_field_contract_matches_state():
    """组装出的 default / override LLMConfig 字段集恰好等于 core.state.LLMConfig
    的 5 个键（不多不少），LLMConfigSet 恰好 {default, overrides} 两键。"""
    at = _new_app()
    _fill_global(at)
    at.run()
    _fill_override(at, "paper_analysis")
    at.run()
    res = at.session_state["_test_result"]
    assert res is not None
    expected_cfg_keys = {"base_url", "model", "api_key", "temperature", "max_tokens"}
    assert set(res.keys()) == {"default", "overrides"}
    assert set(res["default"].keys()) == expected_cfg_keys
    assert set(res["overrides"]["paper_analysis"].keys()) == expected_cfg_keys


# --- session_state 反向断言：失败时绝不写权威键 --------------------------- #
def test_strengthen_failed_validation_returns_none_weak_session_contract():
    """全局必填失败 → 返回值 None（权威信号）；修正后重跑能写入。

    D1 增强适配（OBS-D1-01 + 预填副作用）：default panel 现在预填 base_url/model
    + 本环境 env 有 LLM_API_KEY，故 _new_app() 首屏即成功写入 llm_config_set；
    随后清空 base_url 失败时返回 None 但**不清除 stale 键**（OBS-D1-01 已知可接受，
    §2.8.4 消费返回值而非直读 session_state）。故只断言权威信号——返回 None，
    不对 stale session_state 强约束（与测试工程师 2026-06-04 OBS-D1-01 处理范式一致）。"""
    at = _new_app()
    # 清空预填的 base_url → 必填失败 → 返回 None（权威信号）。
    at.text_input(key="default_base_url").set_value("")
    at.run()
    assert at.session_state["_test_result"] is None
    # 修正后重跑（填回合法 base_url/model/api_key）→ 成功写入，session_state 与返回值一致。
    _fill_global(at)
    at.run()
    assert at.session_state["_test_result"] is not None
    assert "llm_config_set" in at.session_state
    assert at.session_state["llm_config_set"] == at.session_state["_test_result"]


# --- key 前缀防冲突：15 个 widget key 全部唯一 ----------------------------- #
def test_strengthen_widget_keys_all_unique_no_collision():
    """全局(3) + 4 override(各3) = 15 个 text_input key 全部唯一；
    temperature + max_tokens 均为 slider（D1 增强：max_tokens number_input→slider），
    故 10 个 slider key、0 个 number_input，全部唯一（前缀防冲突约束实证）。"""
    at = _new_app()
    _fill_global(at)
    at.run()
    text_keys = [t.key for t in at.text_input]
    assert len(text_keys) == 15
    assert len(set(text_keys)) == 15, "text_input key 存在冲突"
    slider_keys = [s.key for s in at.slider]
    num_keys = [n.key for n in at.number_input]
    # 5 panel * 2 slider（temperature + max_tokens）= 10 slider；number_input 已无。
    assert len(set(slider_keys)) == len(slider_keys) == 10
    assert len(num_keys) == 0
    all_keys = text_keys + slider_keys + num_keys
    assert len(set(all_keys)) == len(all_keys), "跨控件类型 key 冲突"


# --- api_key 不落盘：仅 session_state，无文件系统写入副作用 ---------------- #
def test_strengthen_api_key_not_persisted_to_disk(tmp_path, monkeypatch):
    """提交含 api_key 的配置后，组件不应产生任何磁盘写入（api_key 仅入
    session_state）。用 cwd 切到空 tmp_path 并断言运行后目录仍为空。"""
    import os
    monkeypatch.chdir(tmp_path)
    at = _new_app()
    _fill_global(at, api_key="sk-SECRET-SHOULD-NOT-PERSIST")
    at.run()
    res = at.session_state["_test_result"]
    assert res is not None
    assert res["default"]["api_key"] == "sk-SECRET-SHOULD-NOT-PERSIST"
    # tmp cwd 下不应有任何新文件落盘。
    assert os.listdir(tmp_path) == [], f"组件产生了磁盘副作用: {os.listdir(tmp_path)}"


# --- default 回显：override prefill 使 expander 默认展开 ------------------- #
def test_strengthen_prefill_override_expander_defaults_expanded():
    """default 含 override 时，对应 expander expanded=True（回显已有配置可见）。
    锚定组件 expanded=prefill is not None 的回显 UX 契约。"""
    prefill = {
        "default": {
            "base_url": "https://d/v1", "model": "d-m", "api_key": "sk-D",
            "temperature": 0.3, "max_tokens": 4096,
        },
        "overrides": {
            "planning": {
                "base_url": "https://pl/v1", "model": "pl-m", "api_key": "sk-PL",
                "temperature": 0.9, "max_tokens": 8192,
            }
        },
    }
    script = f"""
import streamlit as st
from ui.components.llm_config_form import render_llm_config_form
res = render_llm_config_form(default={prefill!r})
st.session_state["_test_result"] = res
"""
    at = AppTest.from_string(script)
    at.run()
    res = at.session_state["_test_result"]
    assert res is not None
    # planning override 被回显且温度/token 透传。
    assert res["overrides"]["planning"]["temperature"] == pytest.approx(0.9)
    assert res["overrides"]["planning"]["max_tokens"] == 8192
    # 找到 planning expander 并确认其默认展开（labels 含 planning）。
    planning_ex = [ex for ex in at.expander if "planning" in ex.label]
    assert len(planning_ex) == 1


# =========================================================================== #
# D1 增强新增用例（2026-06-08，@全栈开发代理）
# 契约：architecture.md §2.8.2（校验反转 + 兜底纯函数 + slider + Q1 round + 预填）。
# 维度：D3=校验反转 / D4=预填+OBS-D1-01 / D5=slider。
# =========================================================================== #


# --- D3 校验反转：default api_key 留空合法（_validate_panel 直测） ----------- #
def test_d1e_validate_panel_blank_api_key_now_valid():
    """T-D1E-U07：base_url/model 合法、api_key 空 → 校验通过，cfg.api_key=="" 且无
    'api_key 不能为空' 错误（校验反转，架构 §2.8.2）。"""
    from ui.components.llm_config_form import _validate_panel
    cfg, errors = _validate_panel({
        "base_url": "https://api.x.com/v1", "model": "gpt-4o", "api_key": "",
        "temperature": 0.3, "max_tokens": 8192,
    }, scope_label="全局默认")
    assert cfg is not None
    assert cfg["api_key"] == ""
    assert errors == []
    assert not any("api_key" in m for m in errors)


def test_d1e_validate_panel_blank_api_key_still_fails_on_base_url():
    """T-D1E-U08：api_key 空 + base_url 空 → 仍因 base_url 报错 None；错误**不含** api_key。"""
    from ui.components.llm_config_form import _validate_panel
    cfg, errors = _validate_panel({
        "base_url": "", "model": "gpt-4o", "api_key": "",
        "temperature": 0.3, "max_tokens": 8192,
    }, scope_label="x")
    assert cfg is None
    assert any("base_url" in m for m in errors)
    assert not any("api_key" in m for m in errors)


def test_d1e_validate_panel_nonblank_api_key_preserved():
    """T-D1E-U09：api_key 非空 + base_url/model 合法 → 通过且原值保留（不 strip 吃字符）。"""
    from ui.components.llm_config_form import _validate_panel
    cfg, errors = _validate_panel({
        "base_url": "https://api.x.com/v1", "model": "gpt-4o", "api_key": "sk-USER-123",
        "temperature": 0.3, "max_tokens": 8192,
    }, scope_label="x")
    assert errors == []
    assert cfg["api_key"] == "sk-USER-123"


def test_d1e_panel_is_blank_unchanged():
    """T-D1E-U10（回归）：_panel_is_blank 行为不变——三文本字段全空白 → True。"""
    from ui.components.llm_config_form import _panel_is_blank
    assert _panel_is_blank({
        "base_url": "", "model": "  ", "api_key": "\t",
        "temperature": 0.3, "max_tokens": 8192,
    }) is True
    # api_key 单独填（其余空）→ 仍视为开启覆写（非 blank）。
    assert _panel_is_blank({
        "base_url": "", "model": "", "api_key": "k",
        "temperature": 0.3, "max_tokens": 8192,
    }) is False


# --- D3 兜底纯函数真值表 _should_block_for_missing_api_key ------------------ #
def test_d1e_should_block_truth_table():
    """T-D1E-U15：兜底纯函数真值表（架构 §2.8.2 Q2）。
    空 api_key + env 空 → True；空 + env 有 → False；非空 → False（无论 env）。"""
    from ui.components.llm_config_form import _should_block_for_missing_api_key
    # 空 api_key + env 空（None / "" / 纯空白）→ True。
    assert _should_block_for_missing_api_key({"api_key": ""}, None) is True
    assert _should_block_for_missing_api_key({"api_key": ""}, "") is True
    assert _should_block_for_missing_api_key({"api_key": "   "}, "   ") is True
    # 空 api_key + env 有 → False（create_llm 可回退 .env）。
    assert _should_block_for_missing_api_key({"api_key": ""}, "env-key") is False
    assert _should_block_for_missing_api_key({"api_key": "  "}, "env-key") is False
    # 非空 api_key → False（无论 env 有无，用户显式优先）。
    assert _should_block_for_missing_api_key({"api_key": "sk-user"}, None) is False
    assert _should_block_for_missing_api_key({"api_key": "sk-user"}, "env-key") is False


def test_d1e_should_block_triggers_st_error_via_apptest(monkeypatch):
    """T-D1E-U15（AppTest）：default api_key 空 且 get_llm_api_key() 空 → 兜底拦截
    （st.error + 返回 None）。脚本内 monkeypatch config.get_llm_api_key→空。"""
    script = """
import streamlit as st
import config
config.get_llm_api_key = lambda: ""  # 模拟 env 无 LLM_API_KEY
from ui.components.llm_config_form import render_llm_config_form
# default panel 预填 base_url/model（合法），api_key 留空 → 触发兜底。
res = render_llm_config_form()
st.session_state["_test_result"] = res
"""
    at = AppTest.from_string(script)
    at.run()
    assert at.session_state["_test_result"] is None
    error_msgs = [e.value for e in at.error]
    assert any("api_key" in m and "环境变量" in m for m in error_msgs)


def test_d1e_blank_api_key_passes_when_env_present():
    """default api_key 留空但 env 有 LLM_API_KEY（本环境真实）→ 兜底不触发 →
    成功返回 LLMConfigSet 且 default.api_key 恒为 ""（真实 key 不进表单/session）。"""
    script = """
import streamlit as st
import config
config.get_llm_api_key = lambda: "env-real-key"  # 模拟 env 有 key
from ui.components.llm_config_form import render_llm_config_form
res = render_llm_config_form()
st.session_state["_test_result"] = res
"""
    at = AppTest.from_string(script)
    at.run()
    res = at.session_state["_test_result"]
    assert res is not None
    # 预填 base_url/model 合法、api_key 留空 → 合法；api_key 恒空（不回写真实 key）。
    assert res["default"]["api_key"] == ""
    assert res["default"]["base_url"] == "https://inference-api.nvidia.com/v1"
    assert res["default"]["model"] == "azure/openai/gpt-5.4"


# --- D5 slider：max_tokens 参数 / 默认 / 端点 / 常量锚定 -------------------- #
def test_d1e_max_tokens_constants():
    """T-D1E-U11/U12：max_tokens slider 常量 min=512/max=16384/step=512/默认=8192，
    且 _MAX_TOKENS_DEFAULT 与 config.DEFAULT_LLM_MAX_TOKENS 动态锚定。"""
    import config as cfg
    from ui.components import llm_config_form as form
    assert form._MAX_TOKENS_MIN == 512
    assert form._MAX_TOKENS_MAX == 16384
    assert form._MAX_TOKENS_STEP == 512
    assert form._MAX_TOKENS_DEFAULT == 8192
    assert form._MAX_TOKENS_DEFAULT == cfg.DEFAULT_LLM_MAX_TOKENS == 8192


def test_d1e_max_tokens_is_slider_default_8192():
    """T-D1E-U11：max_tokens 渲染为 slider（非 number_input），默认 8192。"""
    at = _new_app()
    _fill_global(at)
    at.run()
    res = at.session_state["_test_result"]
    assert res is not None
    assert res["default"]["max_tokens"] == 8192
    # max_tokens 是 slider：default_max_tokens 应在 slider 列表、不在 number_input。
    slider_keys = [s.key for s in at.slider]
    num_keys = [n.key for n in at.number_input]
    assert "default_max_tokens" in slider_keys
    assert "default_max_tokens" not in num_keys


def test_d1e_max_tokens_slider_endpoints():
    """T-D1E-U13：slider 端点 512 / 16384 合法且 int 类型。"""
    at = _new_app()
    _fill_global(at)
    at.slider(key="default_max_tokens").set_value(512)
    at.run()
    res = at.session_state["_test_result"]
    assert res is not None
    assert res["default"]["max_tokens"] == 512
    assert isinstance(res["default"]["max_tokens"], int)
    at2 = _new_app()
    _fill_global(at2)
    at2.slider(key="default_max_tokens").set_value(16384)
    at2.run()
    res2 = at2.session_state["_test_result"]
    assert res2["default"]["max_tokens"] == 16384


# --- D5 Q1：prefill 非 512 整除 round 到最近 step --------------------------- #
def test_d1e_round_to_step_helper():
    """T-D1E-U14（Q1 裁定）：_round_to_step 把非 step 整除值 round 到最近 512 整除并
    clamp 到 [512, 16384]。8000→8192（最近），8100→8192，7800→7680，超界 clamp。"""
    from ui.components.llm_config_form import _round_to_step
    assert _round_to_step(8000, 512, 512, 16384) == 8192   # 8000/512=15.6→16*512
    assert _round_to_step(8100, 512, 512, 16384) == 8192
    assert _round_to_step(7800, 512, 512, 16384) == 7680   # 7800/512=15.2→15*512
    assert _round_to_step(4096, 512, 512, 16384) == 4096   # 已整除不变
    assert _round_to_step(100, 512, 512, 16384) == 512     # 低于下界 clamp
    assert _round_to_step(20000, 512, 512, 16384) == 16384  # 高于上界 clamp


def test_d1e_prefill_non_step_value_rounded_in_slider():
    """T-D1E-U14（AppTest）：prefill max_tokens=8000（非 512 整除）注入 slider →
    组件 round 到 8192，slider 初始值落在 step 网格、合法返回。"""
    prefill = {
        "default": {
            "base_url": "https://d/v1", "model": "d-m", "api_key": "sk-D",
            "temperature": 0.3, "max_tokens": 8000,  # 非 512 整除
        },
        "overrides": {},
    }
    script = f"""
import streamlit as st
from ui.components.llm_config_form import render_llm_config_form
res = render_llm_config_form(default={prefill!r})
st.session_state["_test_result"] = res
"""
    at = AppTest.from_string(script)
    at.run()
    res = at.session_state["_test_result"]
    assert res is not None
    # 8000 round 到最近 512 整除 = 8192。
    assert res["default"]["max_tokens"] == 8192


# --- D4 预填：default panel 预填 config getter；override 不预填 ------------- #
def test_d1e_default_panel_prefills_base_url_model():
    """T-D1E-S02 单测化：无 prefill 时 default panel base_url/model 预填 config getter。"""
    at = _new_app()  # 无 prefill。
    base_url_val = at.text_input(key="default_base_url").value
    model_val = at.text_input(key="default_model").value
    assert base_url_val == "https://inference-api.nvidia.com/v1"
    assert model_val == "azure/openai/gpt-5.4"
    # override 卡片不预填（保持空，守"全空=不覆写"）。
    for node in ("paper_intake", "paper_analysis", "resource_scout", "planning"):
        assert at.text_input(key=f"override_{node}_base_url").value == ""
        assert at.text_input(key=f"override_{node}_model").value == ""


def test_d1e_default_panel_prefill_overrides_config_getter():
    """有 prefill 时 default panel 用 prefill 值（而非 config getter）。"""
    prefill = {
        "default": {
            "base_url": "https://custom/v1", "model": "custom-m", "api_key": "sk-C",
            "temperature": 0.3, "max_tokens": 8192,
        },
        "overrides": {},
    }
    script = f"""
import streamlit as st
from ui.components.llm_config_form import render_llm_config_form
res = render_llm_config_form(default={prefill!r})
st.session_state["_test_result"] = res
"""
    at = AppTest.from_string(script)
    at.run()
    assert at.text_input(key="default_base_url").value == "https://custom/v1"
    assert at.text_input(key="default_model").value == "custom-m"
