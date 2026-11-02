"""Sp8 执行侧**输出契约**防线（`T-S8-2-12` 从 `test_sprint7_s713_reported_metrics.py` 迁出）。

为什么单独立一个文件：原文件（S7-13 自报指标防线，1302 行）的**被测物已被整批删除** ——
`_split_reported_metrics` / `_coerce_reported_value` / `_collect_grouped_metrics` /
`analysis` 折叠通道，以及 `EXECUTION_OUTPUT_SCHEMA.metrics`。按 `T-S8-2-12` 的处置（Maria
2026-10-21 拍板"删掉整个文件"）**整文件删除**，但其中三簇**必须留下**：

1. **冻结表 + `test_f1`**（5 行）—— ⚠ 计划红线写死「**禁止整表删除**」（`AC-S8-21①`），
   且这张表刚在 `T-S8-2-12` 完成 10 行 → 5 行的换发，是全绿的；
2. **`test_d3`**（`source` 字段名不得回潮）—— 已按新口径换发成"判字段名、不判子串"；
3. **`test_d4`**（schema 契约）—— 已按新契约换发成"`metrics` 不存在 + 四字段不进 required"。

⚠ **这不是"弱化断言"**：删掉的是**测不到的东西**（函数不存在了，断言只会变成
`AttributeError`，那是"测不到"不是"没变"）；留下的是**今天仍然成立**的契约。
"""

from __future__ import annotations

import copy
import hashlib
import importlib
import inspect
import json
import re
from pathlib import Path
from typing import Any, Dict, List

import pytest

from core.state import ExecutionMode  # noqa: E402

execution_module = importlib.import_module("core.nodes.execution")
react_base_module = importlib.import_module("core.react_base")

_ONE_STEP = [{"step_name": "训练", "command": "python train.py"}]
_ONE_LEDGER = [(0, ["python", "train.py"], 0)]


_FIXTURES = Path(__file__).parent / "fixtures" / "s713_realrun_20260802"
_ABSENT = object()  # "这个构造点根本不传该字段"，区别于"传了一个空值"


def _load_fixture(name: str) -> Any:
    return json.loads((_FIXTURES / name).read_text(encoding="utf-8"))


def _state(steps: List[Any], work_dir: str = "/tmp/s713-workdir", **overrides: Any) -> Dict[str, Any]:
    st: Dict[str, Any] = {
        "llm_config_set": {"default": {"model": "test"}},
        "code_output_dir": work_dir,
        "reproduction_plan": {
            "execution_steps": steps, "environment": {"dependencies": []},
        },
        "paper_analysis": {"metrics": []},
        "execution_mode": ExecutionMode.FULL,
        "node_errors": [],
        "degraded_nodes": [],
        "fix_loop_history": [],
        "fix_loop_count": 0,
        "retry_budget_remaining": 40,
        "_dev_loop_llm_calls": 0,
        "_dev_loop_route": None,
        "execution_result": None,
        "current_step": "coding",
    }
    st.update(overrides)
    return st


# ===========================================================================
# 冻结表：函数源码字节钉死（5 行）
# ===========================================================================
#
# 与 `_EXECUTION_SYSTEM_PROMPT_BODY` 字节门的同款纪律，只是对象换成了函数体。
#
# 基线沿革：S7-13（commit 1e2577d）建表 10 行 → `T-S8-2-12` 换发为 5 行。

_FROZEN_FUNCTION_HASHES: Dict[str, str] = {
    # ⟦Sp8 / T-S8-2-12 换发：10 行 → **5 行**（AC-S8-21①，§3.4 A 表）⟧
    #
    # **移出 5 行（被测物整体删除，不是放宽）**：
    #   · `_apply_no_metrics` —— S8-02 整体删除；v1.2 裁定 1，架构师裁定 + Maria 2026-08-04 已认
    #   · `_extract_metrics_block` / `_parse_metrics` / `_regex_scan_metrics` —— 同上（<METRICS> 通道退场）
    #   · `_collect_grouped_metrics` —— Sp8 T-S8-2-10 整体删除（推翻架构 v2.1 §13 的"不删、不改"）
    #   ⇒ 这 5 个函数在 `core/nodes/execution.py` 里**已不存在**，留在表里只会变成
    #      `AttributeError`（"测不到"而非"没变"），所以是**删行**，不是改成"不少于"。
    #
    # 🔴 **换发 1 行**：`_build_execution_result` —— 函数还在，只换哈希值。
    #   变更内容：`+conclusion` 形参、`success` 改由四档 `level` 派生、两个指标形参删除
    #   （T-S8-2-8）。`67296a1efe8aa703` → `c19be60025adcd8a`。
    #
    # **其余 4 行哈希不变**（逐行现场复算确认）：`_completion_insufficient` /
    #   `_apply_incomplete_execution` / `_reconcile_steps` / `_audit_declared_steps`。
    #
    # 🔴 红线（AC-S8-21 原文）：**禁止整表删除、禁止改成「不少于」来规避。**
    "_completion_insufficient": "a8b5d4c33c7fcfba",
    "_apply_incomplete_execution": "8f2563c160c41cf9",
    "_build_execution_result": "c19be60025adcd8a",
    "_reconcile_steps": "a837711c28f2bbbf",
    "_audit_declared_steps": "e45f8adf2406ab43",
}


@pytest.mark.parametrize("func_name", sorted(_FROZEN_FUNCTION_HASHES))
def test_f1_frozen_function_source_bytes_are_unchanged(func_name):
    """零改动红线：这五个函数的源码字节被钉死。

    改动流程（与 §48.1 prompt 字节门四件套同款）：①先跑一次看它当场红；
    ②重算写死新值；③在 dev-plan 留一行变更原因；④再验红一次。

    **禁止写成 `EXPECTED = actual` 的自锁定形态**（R-S7-41 的坑）。
    """
    src = inspect.getsource(getattr(execution_module, func_name))
    actual = hashlib.sha256(src.encode("utf-8")).hexdigest()[:16]
    assert actual == _FROZEN_FUNCTION_HASHES[func_name], (
        f"{func_name} 源码已变更（当前：{actual}，基线：{_FROZEN_FUNCTION_HASHES[func_name]}）"
        f"——该函数声明零改动，如确需改动请走四件套并更新本表"
    )


def _walk_property_names(node: Any) -> List[str]:
    names: List[str] = []
    if isinstance(node, dict):
        props = node.get("properties")
        if isinstance(props, dict):
            names.extend(props.keys())
        for value in node.values():
            names.extend(_walk_property_names(value))
    elif isinstance(node, list):
        for value in node:
            names.extend(_walk_property_names(value))
    return names


# ===========================================================================
# schema 与 prompt 的契约口径（已按 Sp8 新契约换发）
# ===========================================================================


def test_d3_schema_and_prompt_carry_no_source_field():
    """`metrics[].source` 已被砍除（2026-08-02 Maria 拍板：没有消费点的字段即过度工程）。

    两侧同时断：schema 里递归扫不到 `source`，冻结区 prompt 主体里也不出现——
    只断一侧的话，另一侧留个孤儿描述，模型照样会去想那一层。

    ⟦🔴 Sp8 / T-S8-2-12 **换发口径**（原口径已过粗）：本条的 schema 侧判的是**属性名
    恰为 `source`**（原样保留，`_walk_property_names` 本来就做精确匹配）；但 prompt 侧
    原先写的是**子串** `"source" not in 主体` —— 而 v2.3 起物证里**合法地**有
    `source_note`（架构 §16.2 明文），主体又必须教 agent 填这个键 ⇒ 子串口径必然命中。
    ⇒ 改成**判"字段名恰为 source"**：词边界 `(?<![\w-])source(?![\w-])`（`_` 属 `\w`，
    所以 `source_note` 不命中）。⚠ **不得为了让这条变绿而从主体删掉 `source_note`** ——
    那会让 agent 不知道该记出处（同 `AC-S8-18` 那次"判 token 不判子串"的同族）。⟧
    """
    assert "source" not in _walk_property_names(execution_module.EXECUTION_OUTPUT_SCHEMA)
    assert re.search(r"(?<![\w-])source(?![\w-])", execution_module._EXECUTION_SYSTEM_PROMPT_BODY) is None, (
        "主体里不得出现字段名 `source`（`source_note` 合法，不在此列）"
    )


def test_d4_metrics_item_shape_is_exactly_three_fields():
    """schema 契约：`metrics` 已整体移除，新四字段都不进顶层 `required`。

    ⟦🔴 Sp8 / T-S8-2-12 **换发**（原断言的前提已被废除）：`T-S8-2-9` 把 `metrics`
    属性**整体从 schema 移除**（方案 A 已废除，架构 §12）⇒ 本条不再可能断言"恰三字段"。
    ⇒ 改判为**负向断言** + 钉住新契约四字段**都不在顶层 `required`**（AR-S8-07：
    `_missing_required_fields` 把空数组也算缺失，列进去会让"跑挂了、零结果"的回合
    每次白烧一次 schema 重生成调用）。⟧
    """
    props = execution_module.EXECUTION_OUTPUT_SCHEMA["properties"]
    assert "metrics" not in props, "`metrics` 属性已随 T-S8-2-9 整体移除"
    assert execution_module.EXECUTION_OUTPUT_SCHEMA["required"] == [
        "steps_attempted", "all_exit_zero", "summary",
    ], "顶层 required 必须保持原样三键"
    for field in ("conclusion_level", "goal_checks", "evidence", "result_blocks"):
        assert field in props, f"新契约字段 {field} 缺席"
        assert field not in execution_module.EXECUTION_OUTPUT_SCHEMA["required"], (
            f"{field} 不得进顶层 required（AR-S8-07）"
        )


# ===========================================================================
# 从 s713 迁出的**活簇**（T-S8-2-12，Maria 2026-10-21 拍板 A：死防线整文件删）
# ===========================================================================
# 为什么这簇要留：它们与"自报指标折叠"无关，测的是**今天仍然成立**的契约 ——
#   · `d1/d2`：`EXECUTION_OUTPUT_SCHEMA` 真的传进了子图工厂 + `required` 不含 metrics；
#   · `e1/e2/e3`：`expected_results` 原样注入 + 无该键时 payload 字节零扰动；
#   · `f4`：`_completion_insufficient` 的真值表（封顶②的判据）。
# ⚠ 18 条绿的里另有 `b4`（flaky 自比基线）与 `b5`（两来源合并——对象已随 2-10 删除），
#   属"绿的但被测物已死"，**不迁**。


def _install_agent_harness(monkeypatch, subgraph_result: Any) -> Dict[str, Any]:
    """把 `_run_execution_agent` 的外部依赖全部替身化，捕获传给工厂的实参。

    体例沿 `tests/test_sprint7_targeted.py::_install_agent_harness`：
    `result_schema` **显式列在签名里并记录**，而不是被 `**kwargs` 吞掉——否则
    "到底传没传 schema"就断不了了。
    """
    captured: Dict[str, Any] = {}

    class _FakeSubgraph:
        def invoke(self, initial: Dict[str, Any]) -> Dict[str, Any]:
            captured["initial"] = initial
            return {"messages": [], "round": 1, "result": subgraph_result}

    def fake_create_react_subgraph(
        *, node_name, system_prompt, tools, max_rounds, result_schema=None
    ):
        captured["result_schema"] = result_schema
        captured["system_prompt"] = system_prompt
        return _FakeSubgraph()

    monkeypatch.setattr(execution_module, "create_react_subgraph", fake_create_react_subgraph)
    monkeypatch.setattr(execution_module, "create_llm", lambda cfg: object())
    monkeypatch.setattr(execution_module, "resolve_llm_config", lambda cfg, node: cfg)
    monkeypatch.setattr(execution_module, "load_all_secrets", lambda *a, **k: {})
    monkeypatch.setattr(execution_module, "build_credential_env", lambda secrets: {})
    monkeypatch.setattr(execution_module, "make_prepare_environment_tool", lambda *a, **k: None)
    monkeypatch.setattr(execution_module, "make_run_in_sandbox_tool", lambda *a, **k: None)
    monkeypatch.setattr(execution_module, "make_request_user_input_tool", lambda *a, **k: None)
    return captured


def test_d1_result_schema_object_actually_reaches_the_subgraph_factory(monkeypatch):
    """`create_react_subgraph` 收到的第 5 个参数**就是** `EXECUTION_OUTPUT_SCHEMA` 本体。

    用 `is` 而不是 `==`：换成一份"长得像"的字典（比如把 `metrics` 摘掉再传）
    也能过 `==` 那一关，`is` 才能钉死"传的是那个常量"。
    """
    captured = _install_agent_harness(monkeypatch, None)
    execution_module._run_execution_agent(
        _state(_ONE_STEP), "/tmp/s713-workdir", {"execution_steps": _ONE_STEP},
    )
    assert captured["result_schema"] is execution_module.EXECUTION_OUTPUT_SCHEMA


def test_d2_required_omits_metrics_so_zero_metric_rounds_do_not_burn_a_call():
    """`required` 不含 `metrics` —— 用**生产的** `_missing_required_fields` 断行为。

    `react_base._missing_required_fields` 把"必填的空 list/dict"判为缺失，缺失会
    触发一次 schema 重生成调用 ⇒ 若 `metrics` 进了 required，**每个零指标回合都白烧
    一次账外 LLM 调用**。

    第二段是这条断言的"牙"：把 `metrics` 塞进 required 的**副本**，同一份 parsed
    立刻被判缺失 ⇒ 证明上面那个 `== []` 不是因为函数本身不干活。
    """
    schema = execution_module.EXECUTION_OUTPUT_SCHEMA
    assert "metrics" not in (schema.get("required") or [])

    parsed = {"steps_attempted": 0, "all_exit_zero": True, "summary": "空跑", "metrics": []}
    assert react_base_module._missing_required_fields(parsed, schema) == []

    mutated = copy.deepcopy(schema)
    mutated["required"] = list(mutated["required"]) + ["metrics"]
    assert react_base_module._missing_required_fields(parsed, mutated) == ["metrics"]


def test_e1_expected_results_is_injected_verbatim():
    """计划里的 `expected_results` 原样进 payload——agent 靠它知道"计划怎么称呼这一组"。

    不注入的话，"`group` / `name` 用计划写法"这条约束**整条落空**：agent 只能按
    产物目录名和代码字段名填，回到 2026-08-01 那次"组名失配"的老路。
    """
    expected = _load_fixture("expected_results.json")
    payload = execution_module._build_execution_agent_context(
        _state(_ONE_STEP), "/tmp/s713-workdir",
        {"execution_steps": _ONE_STEP, "expected_results": expected},
    )
    assert payload["expected_results"] == expected
    # 计划写法必须**逐字**可见（t-SNE 的连字符大写是关键那一格）。
    blob = json.dumps(payload, ensure_ascii=False)
    assert "t-SNE" in blob and "k-NN classifier accuracy" in blob


@pytest.mark.parametrize(
    "plan_extra",
    [
        pytest.param({}, id="key_absent"),
        pytest.param({"expected_results": None}, id="none"),
        pytest.param({"expected_results": []}, id="empty_list"),
        pytest.param({"expected_results": {}}, id="empty_dict"),
        pytest.param({"expected_results": ""}, id="empty_str"),
    ],
)
def test_e2_absent_expected_results_leaves_payload_byte_identical(plan_extra):
    """★ "非空才注入"：无该键 / 空值的计划下，payload 与基线**逐字节相同**。

    这条守的是 Prompt Cache 与跨批次字节幂等——注入若无条件发生，所有老计划的
    HumanMessage 都会多出一个 `"expected_results": null`，白白作废一次缓存前缀。
    """
    base_plan = {"execution_steps": _ONE_STEP, "environment": {}}
    baseline = json.dumps(
        execution_module._build_execution_agent_context(
            _state(_ONE_STEP), "/tmp/s713-workdir", dict(base_plan),
        ),
        sort_keys=True, ensure_ascii=False, default=str,
    )
    actual = json.dumps(
        execution_module._build_execution_agent_context(
            _state(_ONE_STEP), "/tmp/s713-workdir", {**base_plan, **plan_extra},
        ),
        sort_keys=True, ensure_ascii=False, default=str,
    )
    assert actual == baseline
    assert "expected_results" not in actual


def test_e3_injection_reaches_the_human_message_the_agent_actually_reads(monkeypatch):
    """端到端一格：注入的内容真的出现在送进子图的 HumanMessage 里。

    只断 `_build_execution_agent_context` 的返回值证明不了 agent 看得见它——
    payload 到 HumanMessage 之间还有一次 `json.dumps` 装配。
    """
    captured = _install_agent_harness(monkeypatch, None)
    expected = _load_fixture("expected_results.json")
    execution_module._run_execution_agent(
        _state(_ONE_STEP), "/tmp/s713-workdir",
        {"execution_steps": _ONE_STEP, "expected_results": expected},
    )
    human = captured["initial"]["messages"][-1]
    assert "expected_results" in human.content
    assert "t-SNE" in human.content


@pytest.mark.parametrize(
    "recon, expected",
    [
        pytest.param({"planned_actionable": 2, "completed": 1}, True, id="not_done"),
        pytest.param({"planned_actionable": 2, "completed": 2}, False, id="done"),
        pytest.param({"planned_actionable": 0, "completed": 0}, False, id="nothing_actionable"),
        pytest.param({"planned": 2, "completed": 1}, True, id="legacy_falls_back_to_planned"),
        pytest.param({}, False, id="empty"),
        pytest.param(None, False, id="none"),
        pytest.param("垃圾", False, id="not_a_dict"),
        pytest.param({"planned_actionable": 2, "completed": True}, False, id="completed_bool"),
    ],
)
def test_f4_completion_predicate_truth_table_unchanged(recon, expected):
    """S7-11 交付的完成度谓词真值表——本批零改动，逐格钉死。"""
    assert execution_module._completion_insufficient(recon) is expected
