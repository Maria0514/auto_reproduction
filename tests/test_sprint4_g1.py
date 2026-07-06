"""Sprint 4 任务 G1：mock 单测全套 + AC-S4 覆盖矩阵审计（Sprint 级聚合断言）。

dev-plan §4 G1 / §5 AC 覆盖矩阵的可执行版，沿用并强化 sp3 CP-F1-4 范式：

    CP-G1-1  AC-S4-01/02/03/04/05/06/07/09/12/14 十条 AC 逐条有 mock 层 CP 用例映射
             （§5 矩阵 → AC_COVERAGE_MAP 可执行断言）。三重防假绿：
             ① 存在性——映射的测试函数存在于对应模块且 callable、名字以 test_ 开头
                （保证被 pytest 默认收集）；
             ② mark 审计——映射用例不得带 skip / e2e mark（防"映射到永不运行的用例"，
                e2e 已被 pytest.ini addopts 默认排除，映射进矩阵即假绿）；
             ③ AST 断言实质性审计——每个映射用例函数体须含 ≥1 个真实断言构造
                （ast.Assert / pytest.raises / mock 的 assert_* 调用 / _assert helper），
                防"名字对但断言空泛"（G1 规格明文，比 sp3 CP-F1-4 仅查 callable 更强）。
    CP-G1-2  AC-S4-03 结构测试（graph.py 零改动的行为证）：build_graph() 编译成功、
             业务节点集合逐字 == 7 节点、无禁止/泄漏节点、await_dev_loop_interrupt
             self-loop 边在（路由函数 + 编译图边双证）、边结构与 sp3 逐字一致。
             （git 层证据——core/graph.py 最后改动 commit 8b62230 2026-06-28 属 sp3，
             Sprint 4 期间零 commit 触碰——记录于 G1 审计报告，不在测试内断 git。）
    CP-G1-3  全量非 e2e 回归 + 3 次连跑 + 对账由 G1 审计流程承载（见
             docs/sprint4/test-reports/2026-07-05_g1-ac-matrix-audit.md）；本文件提供
             sp4 全套测试模块可收集性守门（import 期回归即刻红）。

G1 授权边界（dev-plan §5 矩阵）：AC-S4-08（真凭证注入 e2e）/ 10（UI 手动走查）/
11（脱敏 grep 全链路 e2e）/ 13（三 interrupt 串行 e2e）属 G2/F1 手动项，不纳入本矩阵
强制键（与 sp3 把 AC-S3-01 留 F2 同一逻辑）；其 mock 层地基已由 CP-D1-2/CP-E1-4（08）、
CP-F1-1/2（10）、CP-A3-3/CP-C1-4/CP-E1-3/CP-E3-4/CP-D2-2（11）覆盖并全绿，
在审计报告中列旁证、不在此重复映射。

全部离线：真实 build_graph + MemorySaver + 静态 AST/import 审计，
零 LLM / 零 deepxiv / 零网络 / 零 sandbox 真跑。
"""

from __future__ import annotations

import ast
import importlib
import inspect
import sys
import textwrap
from pathlib import Path
from typing import Dict, List, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest  # noqa: E402
from langgraph.checkpoint.memory import MemorySaver  # noqa: E402
from langgraph.graph.state import CompiledStateGraph  # noqa: E402

from core.graph import _route_after_execution, build_graph  # noqa: E402

# ===========================================================================
# CP-G1-1：AC → mock 用例覆盖矩阵（dev-plan §5 的可执行版）
# ---------------------------------------------------------------------------
# 每条 AC 映射到「已验收 PASS 的 CP 用例」（来源：§5 矩阵 关键检查点列 + 各任务
# 验收报告 2026-07-02_a1-a2-d1 / 2026-07-02_a3-d2 / 2026-07-03_b1 /
# 2026-07-04_b2 / 2026-07-04_cef / 2026-07-04_e3-e4）。
# 映射选取原则：每条 AC 至少含该 AC 的**核心判定用例**（非旁证），跨任务 AC
# （如 AC-S4-14 三级递进）逐级各取主证一条。
# ===========================================================================

AC_COVERAGE_MAP: Dict[str, List[Tuple[str, List[str]]]] = {
    "AC-S4-01": [  # run_command 存在 + 正常返回 + 越界拒（CP-C1-1/2 / CP-C2-1）
        ("tests.test_sprint4_c1", [
            "test_cp_c1_1_normal_smoke_returns_valid_json",
            "test_cp_c1_2_out_of_workspace_base_dir_rejected",
        ]),
        ("tests.test_sprint4_c2", [
            "test_cp_c2_1_seven_tools_with_exact_names",
            "test_cp_c2_1_wrapper_form_unchanged",
        ]),
    ],
    "AC-S4-02": [  # 护栏复用 + RUN_COMMAND_TIMEOUT < SANDBOX_EXEC_TIMEOUT（CP-A1-2 / CP-C1-3）
        ("tests.test_sprint4_a1", [
            "test_cp_a1_2_run_command_timeout_below_sandbox_exec_timeout",
        ]),
        ("tests.test_sprint4_c1", [
            "test_cp_c1_3_timeout_kills_subprocess_tree",
        ]),
    ],
    "AC-S4-03": [  # 7 节点骨架不变（CP-G1-2 本文件结构测试 + sp3 AC-S3-10 基线沿用）
        ("tests.test_sprint4_g1", [
            "test_cp_g1_2_build_graph_compiles",
            "test_cp_g1_2_exactly_seven_nodes_verbatim",
            "test_cp_g1_2_await_dev_loop_interrupt_self_loop_edge",
        ]),
        ("tests.test_sprint3_d1", [
            "test_cp_d1_1_build_graph_compiles",
            "test_cp_d1_1_exactly_seven_nodes",
            "test_cp_d1_4_await_dev_loop_interrupt_self_loop",
        ]),
    ],
    "AC-S4-04": [  # 预算扣减 = rounds + metrics，不双扣；guard 重入零扣；触顶 interrupt（CP-E3-1）
        ("tests.test_sprint4_e3", [
            "test_cp_e3_1_deduction_rounds_plus_metric_calls",
            "test_cp_e3_1_guard_reentry_zero_deduction_and_agent_not_called",
            "test_cp_e3_1_dev_loop_ceiling_to_interrupt",
        ]),
    ],
    "AC-S4-05": [  # interrupt#2 / 修复循环边界零回归（CP-E4-1：sp3 C3/D1/E2 用例回归）
        ("tests.test_sprint3_c3", [
            "test_cp_c3_7_interrupt_three_state_resume",
            "test_cp_c3_13_interrupt_rerun_idempotent",
            "test_cp_c3_4_retry_coding_increments",
            "test_cp_c3_5_upper_limit_to_interrupt",
        ]),
        ("tests.test_sprint3_d1_reinforce", [
            "test_h1_real_graph_await_self_loop_triggers_interrupt_sandbox_once",
            "test_h2_resume_terminate_reaches_end_sandbox_still_once",
        ]),
        ("tests.test_sprint3_e2_reinforce", [
            "test_g1_all_three_payloads_distinct_terminal_states",
        ]),
        ("tests.test_sprint4_e4_regression_gate", [
            "test_cp_e4_2_interrupt3_resume_sandbox_side_effect_exactly_once",
        ]),
    ],
    "AC-S4-06": [  # request_user_input 两 agent 均触发 interrupt#3 + resume 继续
        #（CP-B1-1/2 契约 / CP-B2-1 harness / CP-C2-3 coding 侧 / CP-E2-5 execution 侧）
        ("tests.test_sprint4_b1", [
            "test_cp_b1_1_payload_contract_four_keys",
            "test_cp_b1_2_remember_with_purpose_key_persists_then_returns",
        ]),
        ("tests.test_sprint4_b2_interrupt3_idempotency", [
            "test_cp_b2_1_harness_pause_resume_value_reaches_finalize",
        ]),
        ("tests.test_sprint4_c2", [
            "test_cp_c2_3_resume_value_reaches_agent_and_map_contract",
        ]),
        ("tests.test_sprint4_e2", [
            "test_cp_e2_5_credential_flow_interrupt_resume_and_r_s4_10_merge",
        ]),
    ],
    "AC-S4-07": [  # credential_required 分类 + 不耗 fix_loop_count（CP-E3-2）
        ("tests.test_sprint4_e3", [
            "test_cp_e3_2_eight_keywords_hit_credential_required",
            "test_cp_e3_2_not_auto_fixable_maps_permanent",
            "test_cp_e3_2_credential_priority_over_data_missing_and_hardware",
            "test_cp_e3_2_no_fix_loop_consumption_interrupt_fallback",
        ]),
    ],
    "AC-S4-09": [  # 记住 → .secrets（0600 + gitignore）+ 同 key 不再问（CP-A3-1/2 / CP-B1-3）
        ("tests.test_sprint4_a3", [
            "test_cp_a3_1_remember_then_lookup_roundtrip",
            "test_cp_a3_1_file_permission_is_0600",
            "test_cp_a3_2_secrets_covered_by_gitignore",
        ]),
        ("tests.test_sprint4_b1", [
            "test_cp_b1_3_cache_hit_returns_without_interrupt",
        ]),
    ],
    "AC-S4-12": [  # 日志脱敏（CP-A3-3/5，caplog）
        ("tests.test_sprint4_a3", [
            "test_cp_a3_3_masks_remembered_sensitive_value",
            "test_cp_a3_3_masks_process_registered_unremembered_value",
            "test_cp_a3_5_all_module_logs_never_contain_secret_values",
        ]),
    ],
    "AC-S4-14": [  # 重跑幂等：前序副作用恰为 1（CP-B2-2 → CP-C2-3 → CP-E4-2 三级递进）
        ("tests.test_sprint4_b2_interrupt3_idempotency", [
            "test_cp_b2_2_gate_prior_round_side_effect_exactly_once",
        ]),
        ("tests.test_sprint4_c2", [
            "test_cp_c2_3_write_side_effect_exactly_once_across_resume",
        ]),
        ("tests.test_sprint4_e4_regression_gate", [
            "test_cp_e4_2_interrupt3_resume_sandbox_side_effect_exactly_once",
        ]),
    ],
}

# G1 强制矩阵键（dev-plan CP-G1-1 原文枚举，逐字）。
G1_MANDATED_ACS = {
    "AC-S4-01", "AC-S4-02", "AC-S4-03", "AC-S4-04", "AC-S4-05",
    "AC-S4-06", "AC-S4-07", "AC-S4-09", "AC-S4-12", "AC-S4-14",
}


def iter_mapped_node_ids() -> List[str]:
    """导出矩阵映射的 pytest node id 列表（供 targeted 全绿运行：CP-G1-1 后半）。"""
    ids: List[str] = []
    for entries in AC_COVERAGE_MAP.values():
        for module_name, func_names in entries:
            rel = module_name.replace(".", "/") + ".py"
            for fn in func_names:
                node = f"{rel}::{fn}"
                if node not in ids:
                    ids.append(node)
    return ids


def _resolve_mapped_functions(ac_id: str):
    """解析某 AC 的全部映射函数对象，缺失即断言失败。"""
    resolved = []
    for module_name, func_names in AC_COVERAGE_MAP[ac_id]:
        mod = importlib.import_module(module_name)
        for fn_name in func_names:
            obj = getattr(mod, fn_name, None)
            assert callable(obj), (
                f"{ac_id} 的覆盖用例 {module_name}::{fn_name} 缺失或不可调用"
                f"（AC 覆盖矩阵与真实测试脱节）"
            )
            resolved.append((mod, module_name, fn_name, obj))
    return resolved


def _count_assertion_constructs(func) -> int:
    """AST 级统计测试函数内的真实断言构造数。

    计入：ast.Assert；名字含 assert 的调用（mock 的 assert_called* /
    assert_not_called、helper 的 _assert_*）；pytest.raises / pytest.fail。
    不计：字符串内容、parametrize 等装饰器调用。
    """
    src = textwrap.dedent(inspect.getsource(func))
    fn_node = ast.parse(src).body[0]
    count = 0
    for node in ast.walk(fn_node):
        if isinstance(node, ast.Assert):
            count += 1
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                name = node.func.attr
            else:
                name = ""
            if "assert" in name.lower() or name in ("raises", "fail"):
                count += 1
    return count


@pytest.mark.parametrize("ac_id", sorted(AC_COVERAGE_MAP.keys()))
def test_cp_g1_1_ac_has_mock_test_coverage(ac_id: str) -> None:
    """CP-G1-1 ①②：每条 AC 的映射用例存在、可被默认收集、且不被 skip/e2e 屏蔽。

    - 存在性：模块可 import、函数 callable（误删覆盖用例本断言即红）；
    - 可收集：函数名以 test_ 开头（pytest 默认收集规则 + testpaths=tests）；
    - mark 审计：函数级与模块级 pytestmark 均无 skip / e2e——e2e 被 pytest.ini
      addopts 默认排除，若矩阵映射到 e2e 用例，"映射存在"就成了假绿。
    """
    coverage = AC_COVERAGE_MAP[ac_id]
    assert coverage, f"{ac_id} 无任何覆盖映射"
    for mod, module_name, fn_name, obj in _resolve_mapped_functions(ac_id):
        assert fn_name.startswith("test_"), (
            f"{module_name}::{fn_name} 不以 test_ 开头，pytest 不会收集它"
        )
        mark_names = {m.name for m in getattr(obj, "pytestmark", [])} | {
            m.name for m in getattr(mod, "pytestmark", [])
        }
        forbidden = mark_names & {"skip", "e2e", "sandbox_real"}
        assert not forbidden, (
            f"{module_name}::{fn_name} 带 {forbidden} mark，默认回归不会运行它，"
            f"不得作为 {ac_id} 的 mock 层覆盖证据"
        )


@pytest.mark.parametrize("ac_id", sorted(AC_COVERAGE_MAP.keys()))
def test_cp_g1_1_mapped_tests_not_hollow(ac_id: str) -> None:
    """CP-G1-1 ③（防"名字对但断言空泛"，G1 规格明文）：

    每个映射用例函数体经 AST 审计须含 ≥1 个真实断言构造。用例被改成
    空转（删光 assert / 只剩 print）时本断言即红——这是 sp3 CP-F1-4
    仅查 callable 之上的强化层。
    """
    for _mod, module_name, fn_name, obj in _resolve_mapped_functions(ac_id):
        n = _count_assertion_constructs(obj)
        assert n >= 1, (
            f"{module_name}::{fn_name} 无任何断言构造（空泛用例），"
            f"不得作为 {ac_id} 的覆盖证据"
        )


def test_cp_g1_1_coverage_map_spans_exactly_mandated_acs() -> None:
    """CP-G1-1 元断言：矩阵键恰为 G1 授权的 10 条 AC，不多不少。

    AC-S4-08/10/11/13 属 G2 e2e / F1 手动走查范围（dev-plan §5 矩阵 测试类型列），
    不纳入 G1 强制键——纳入会把"待授权补跑项"伪装成已覆盖。
    """
    assert set(AC_COVERAGE_MAP.keys()) == G1_MANDATED_ACS, (
        f"覆盖矩阵键应恰为 G1 授权 10 条 AC，实际 {sorted(AC_COVERAGE_MAP.keys())}"
    )


def test_cp_g1_1_mapped_node_ids_unique_and_wellformed() -> None:
    """CP-G1-1 辅助：导出的 targeted node id 列表格式合法、无重复模块名笔误。"""
    ids = iter_mapped_node_ids()
    assert len(ids) == len(set(ids))
    for node in ids:
        rel, _, fn = node.partition("::")
        assert (PROJECT_ROOT / rel).is_file(), f"映射文件不存在：{rel}"
        assert fn.startswith("test_")


# ===========================================================================
# CP-G1-2：AC-S4-03 结构测试（core/graph.py 零改动的行为证）
# ---------------------------------------------------------------------------
# 与 sp3 test_sprint3_d1 的 AC-S3-10 用例同源但独立成 Sprint 4 锚：sp4 若有人
# 动 graph.py（哪怕 sp3 用例被同步"适配"），本组用例仍以 dev-plan §1.1
# "core/graph.py 零改动" 的字面契约守门。
# ===========================================================================

EXPECTED_NODES = {
    "paper_intake",
    "paper_analysis",
    "resource_scout",
    "planning",
    "coding",
    "execution",
    "reporting",
}
# sp3 禁止节点 ∪ sp4 新增符号（工具名 / 子图内部节点 / 路由值）不得泄漏为主图节点。
FORBIDDEN_NODES = {
    "coding_only", "dev_loop", "exit_dev_loop",          # sp3 AC-S3-10 原禁
    "await_dev_loop_interrupt",                            # 路由值，非节点
    "request_user_input", "run_command",                  # sp4 新工具
    "prepare_environment", "run_in_sandbox",              # sp4 execution 子图工具
    "reasoning", "tool_executor", "budget_check",         # ReAct 子图内部节点
}


def _business_nodes(g) -> set:
    return {n for n in g.get_graph().nodes.keys() if not n.startswith("__")}


def _successors(g) -> Dict[str, set]:
    succ: Dict[str, set] = {}
    for e in g.get_graph().edges:
        succ.setdefault(e.source, set()).add(e.target)
    return succ


def test_cp_g1_2_build_graph_compiles() -> None:
    """AC-S4-03 ①：build_graph() 编译成功，返回 CompiledStateGraph。"""
    g = build_graph(checkpointer=MemorySaver())
    assert isinstance(g, CompiledStateGraph)
    assert callable(getattr(g, "invoke", None))


def test_cp_g1_2_exactly_seven_nodes_verbatim() -> None:
    """AC-S4-03 ②：业务节点集合逐字 == sp3 的 7 节点，不多不少。"""
    nodes = _business_nodes(build_graph(checkpointer=MemorySaver()))
    assert nodes == EXPECTED_NODES, (
        f"节点集合漂移：缺 {EXPECTED_NODES - nodes}，多 {nodes - EXPECTED_NODES}"
    )
    assert len(nodes) == 7


def test_cp_g1_2_no_forbidden_or_leaked_sp4_nodes() -> None:
    """AC-S4-03 ③：禁止节点与 sp4 新符号（工具/子图节点/路由值）零泄漏进主图。"""
    nodes = _business_nodes(build_graph(checkpointer=MemorySaver()))
    leaked = nodes & FORBIDDEN_NODES
    assert not leaked, f"主图出现禁止/泄漏节点：{leaked}"


def test_cp_g1_2_await_dev_loop_interrupt_self_loop_edge() -> None:
    """AC-S4-03 ④（interrupt#2 命门）：await_dev_loop_interrupt self-loop 边在。

    双证：路由函数语义（_dev_loop_route=await → execution）+ 编译图中
    execution → execution 自环边真实存在。
    """
    assert (
        _route_after_execution({"_dev_loop_route": "await_dev_loop_interrupt"})
        == "execution"
    )
    succ = _successors(build_graph(checkpointer=MemorySaver()))
    assert "execution" in succ.get("execution", set()), (
        "编译图缺 execution self-loop 边（interrupt#2 第二回合将永不触发）"
    )


def test_cp_g1_2_edges_structure_unchanged_from_sp3() -> None:
    """AC-S4-03 ⑤：边结构与 sp3 逐字一致（coding 2 路 / execution 5 路 / reporting→END）。"""
    succ = _successors(build_graph(checkpointer=MemorySaver()))
    assert succ.get("coding") == {"execution", "reporting"}, (
        f"coding 后继漂移：{succ.get('coding')}"
    )
    exec_succ = succ.get("execution", set())
    assert {"execution", "coding", "planning", "reporting"} <= exec_succ, (
        f"execution 后继缺分支：{exec_succ}"
    )
    assert any(t.startswith("__end") for t in exec_succ), (
        f"execution 缺 terminate→END 分支：{exec_succ}"
    )
    rep_succ = succ.get("reporting", set())
    assert rep_succ and all(t.startswith("__end") for t in rep_succ), (
        f"reporting 后继应仅为 END：{rep_succ}"
    )
    plan_succ = succ.get("planning", set())
    assert "planning" in plan_succ and "coding" in plan_succ, (
        f"planning 3 路条件边漂移：{plan_succ}"
    )
    assert any(t.startswith("__end") for t in plan_succ)


# ===========================================================================
# CP-G1-3 旁证：sp4 全套测试模块可收集性守门（import 期回归即刻红）
# ---------------------------------------------------------------------------
# 全量回归 3 连跑 + 数字对账由 G1 审计流程执行并归档报告；本断言守门
# "模块可 import" 这一收集前提（沿用 sp3 test_cp_f1_1 范式）。
# ===========================================================================

SP4_TEST_MODULES = [
    "test_sprint4_a1", "test_sprint4_a2", "test_sprint4_a3",
    "test_sprint4_b1", "test_sprint4_b1_fix",
    "test_sprint4_b2_interrupt3_idempotency",
    "test_sprint4_c1", "test_sprint4_c2",
    "test_sprint4_d1", "test_sprint4_d2",
    "test_sprint4_e1", "test_sprint4_e2", "test_sprint4_e3",
    "test_sprint4_e4_regression_gate",
    "test_sprint4_f1", "test_sprint4_le401_fix",
    "test_sprint4_g1",
]


def test_cp_g1_3_sprint4_test_modules_all_importable() -> None:
    """CP-G1-3 旁证：sp4 全部 17 个测试模块可 import（无收集期错误）。"""
    for m in SP4_TEST_MODULES:
        mod = importlib.import_module(f"tests.{m}")
        assert mod is not None, f"tests.{m} 无法 import"
