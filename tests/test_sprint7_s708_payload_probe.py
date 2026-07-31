"""S7-08 / T-S7-5-12 收口闸门（三）：**审核契约面 + 探测摘要面 + AC 覆盖矩阵审计**。

对应 dev-plan §35 任务 T-S7-5-12 的 CP-5.12-7 / CP-5.12-8 / CP-5.12-10，
架构 sp7 §18.5(3)、PRD §10.7 AC-S7-37 / AC-S7-42、§39 AC 映射表。

三节内容
========
1. **CP-5.12-7（AC-S7-37 两道守门收口复核）**
   ⚠ 口径订正（本任务实测推翻 dev-plan §32.4 事实 5 与 §40 P-16）：
   两道守门**不是"新造"，而是"既有 + 已同步"**——
     · payload 键集合：`tests/test_sprint4_e2e.py` 里**一直存在**一条 planning
       interrupt payload 的精确 10 键集合断言（`set(p1.keys()) == {...}`），
       S7-08 加 `local_env_facts` 后它按设计打红，已被同步为 **11 键且仍精确相等**；
     · 决策集合：T-5-10 已在 `tests/test_plan_review_logic.py` 落了精确断言。
   故本节做的是**收口复核**而非重造：断这两处守门**仍在、仍精确、且仍跑在默认回归里**。
   这不是"测试的测试"式过度设计——R-S7-46 的失效形态恰恰是"以为有守门、实际零覆盖"，
   而守门被**悄悄弱化**（`==` 改 `issubset` / `>=`）与守门根本不存在，后果完全一样。

2. **CP-5.12-8（AC-S7-42 两条用例都写，架构 §18.5(3)）**
   条 1 绕过工具 → 验**渲染端**上限；条 2 走真实工具 → 验**两级截断方向合成**后
   `torch` 仍在；两条都加总长确定性上界断言。

3. **CP-5.12-10（AC 覆盖矩阵审计）**：AC-S7-32~42 逐条映射到具体用例，
   并断言被映射的用例**确实存在**（防"矩阵写得很满、用例早被改名/删了"）。

⚠ 已知 bug 模式 #6：访问模块级私有属性一律 `importlib.import_module`。
"""

from __future__ import annotations

import ast
import importlib
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Set

import pytest
from langchain_core.messages import ToolMessage

import config
import core.tools.env_probe_tool as ept
import sandbox.local_venv as lv
from core.tools.env_probe_tool import PROBE_TOOL_NAME, make_probe_environment_tool

resource_scout_mod = importlib.import_module("core.nodes.resource_scout")
planning_mod = importlib.import_module("core.nodes.planning")

_digest_env_probe = resource_scout_mod._digest_env_probe
_PROBE_OUTPUT_MAX_CHARS = resource_scout_mod._PROBE_OUTPUT_MAX_CHARS
_PROBE_DIGEST_MAX_CHARS = resource_scout_mod._PROBE_DIGEST_MAX_CHARS

_TESTS_DIR = Path(__file__).resolve().parent


# =========================================================================== #
# CP-5.12-7：AC-S7-37 两道守门收口复核（payload 恰 11 键 + 决策恰 5 类）
# =========================================================================== #
#: sp4 以来一字未动的既有 10 键（S7-08 红线：只 +1 键，既有 10 键一字不动）。
_LEGACY_PAYLOAD_KEYS = {
    "interrupt_kind", "reproduction_plan", "resource_info", "paper_analysis_summary",
    "degraded_nodes", "node_errors", "revise_count", "soft_hint_threshold",
    "max_total_llm_calls", "switch_repo_failed",
}
_S708_PAYLOAD_KEY = "local_env_facts"
_EXPECTED_PAYLOAD_KEYS = _LEGACY_PAYLOAD_KEYS | {_S708_PAYLOAD_KEY}


def _payload_guard_source() -> str:
    return (_TESTS_DIR / "test_sprint4_e2e.py").read_text(encoding="utf-8")


def test_cp_5_12_7_payload_key_set_guard_is_present_precise_and_complete() -> None:
    """收口复核：planning interrupt payload 的键集合守门**仍在、仍是精确相等、恰 11 键**。

    该守门在 `tests/test_sprint4_e2e.py::test_cp_g2_1_three_interrupts_serial_same_thread`
    里（`set(p1.keys()) == {...}`），断的是**图跑到 planning 第一次中断时的真实 payload**。

    本条复核三件事，任一不成立 AC-S7-37 的"契约不变"就失去承载：
      ① 断言形态仍是 `set(...) == {...}` —— 弱化成 `issubset` / `>=` / 单键 `in`
         会让"payload 悄悄多长出键 / 少掉键"重新变得不可见；
      ② 键集合恰为既有 10 键 + `local_env_facts`；
      ③ 既有 10 键一字不动（S7-08 红线）。
    """
    src = _payload_guard_source()

    # ① 形态：精确相等，且没有被弱化成包含关系
    assert "assert set(p1.keys()) == {" in src, (
        "planning interrupt payload 的键集合守门不见了或被改写 —— "
        "AC-S7-37『既有 payload 键结构不变』将失去唯一承载（R-S7-46）"
    )
    for weakened in (
        "set(p1.keys()) >= {", "set(p1.keys()) <= {",
        "set(p1.keys()).issubset(", "set(p1.keys()).issuperset(",
    ):
        assert weakened not in src, f"payload 键守门被弱化为包含关系：{weakened}"

    # ② + ③ 键集合内容：从源码里把那段集合字面量抠出来逐键核对
    start = src.index("assert set(p1.keys()) == {")
    end = src.index("}", start)
    literal = src[start:end]
    found = set(re.findall(r'"([a-z_]+)"', literal))
    assert found == _EXPECTED_PAYLOAD_KEYS, (
        f"payload 键集合守门覆盖的键不符：多={sorted(found - _EXPECTED_PAYLOAD_KEYS)}, "
        f"少={sorted(_EXPECTED_PAYLOAD_KEYS - found)}"
    )
    assert _LEGACY_PAYLOAD_KEYS <= found, "既有 10 键必须一字不动（S7-08 红线）"
    assert len(found) == 11, f"payload 应恰 11 键，实得 {len(found)}"


def test_cp_5_12_7_payload_guard_runs_in_default_regression() -> None:
    """收口复核补强：承载该守门的用例**必须跑在默认回归里**（不得被 e2e 标签排除）。

    文件名带 `_e2e` 却只有类级 `pytestmark`——若哪天有人给模块加上 `pytest.mark.e2e`，
    这道守门会被 `addopts = -m "not e2e"` 默默 deselect，表现为"守门还在、但从来不跑"。
    """
    src = _payload_guard_source()
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "pytestmark" for t in node.targets
        ):
            pytest.fail(
                "tests/test_sprint4_e2e.py 出现了模块级 pytestmark —— "
                "payload 键守门有被整体 deselect 出默认回归的风险"
            )
    # 承载守门的那个用例是模块级函数（不在 e2e 类里）
    fn_names = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
    assert "test_cp_g2_1_three_interrupts_serial_same_thread" in fn_names


def test_cp_5_12_7_planning_payload_actually_carries_eleven_keys_including_new_one() -> None:
    """源头侧复核：`planning.py` 构造 interrupt payload 的那处字面量恰 11 键。

    上面两条守的是"测试侧的守门还在"，本条守的是**生产侧真相源**——两者分别
    在被改坏时打红，合起来才不会出现"改了生产、顺手把测试也改了"的双向漂移。
    用 AST 取字典键，不做字符串猜测。
    """
    src = Path(planning_mod.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)

    candidates: List[Set[str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        keys = {k.value for k in node.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)}
        if "interrupt_kind" in keys and "reproduction_plan" in keys:
            candidates.append(keys)

    assert len(candidates) == 1, (
        f"planning.py 里 interrupt payload 字典应恰一处，实得 {len(candidates)} 处"
    )
    assert candidates[0] == _EXPECTED_PAYLOAD_KEYS, (
        f"生产侧 payload 键不符：多={sorted(candidates[0] - _EXPECTED_PAYLOAD_KEYS)}, "
        f"少={sorted(_EXPECTED_PAYLOAD_KEYS - candidates[0])}"
    )


def test_cp_5_12_7_decision_set_guard_is_present_and_precise() -> None:
    """收口复核：决策集合守门**仍在、仍精确恰 5 类**（4 类走恢复通道 + 1 类走取消通道）。

    ⚠ 口径订正（本任务实测坐实）：源码里走 `resume_with({"decision": ...})` 的字面量
    **只有 4 个**（approve / code_only / revise / switch_repo）；第 5 类"取消"走
    `controller.cancel_task(thread_id)`，**不经决策通道**、没有 decision 字面量。
    故守门写成"4 类精确集合 + 取消路径存在 + `len(found) + 1 == 5`"是**正确写法**，
    **不得**为了凑"源码里恰 5 个 decision 字面量"把断言写松或去生造第 5 个字面量。
    """
    src = (_TESTS_DIR / "test_plan_review_logic.py").read_text(encoding="utf-8")

    assert '_EXPECTED_RESUME_DECISIONS = {"approve", "code_only", "revise", "switch_repo"}' in src, (
        "决策集合守门（4 类恢复决策）不见了或被改写"
    )
    assert "_EXPECTED_DECISION_COUNT = 5" in src, "决策总数常量（5 类）不见了"
    assert "assert found == _EXPECTED_RESUME_DECISIONS" in src, (
        "决策集合断言必须是精确相等 —— 弱化成包含关系则'悄悄多出一种决策'不可见"
    )
    assert 'assert "controller.cancel_task(thread_id)" in src' in src, (
        "第 5 类『取消』的存在性断言不见了 —— 5 类里就只剩 4 类有守门"
    )
    assert "assert len(found) + 1 == _EXPECTED_DECISION_COUNT" in src, (
        "4 + 1 = 5 的对账断言不见了"
    )
    for weakened in ("found >= _EXPECTED_RESUME_DECISIONS", "found.issuperset", "found.issubset"):
        assert weakened not in src, f"决策集合守门被弱化：{weakened}"


def test_cp_5_12_7_ui_introduces_no_new_interrupt_kind_or_button() -> None:
    """AC-S7-37 红线复核：不新增中断种类、不新增决策类型、不新增按钮。

    S7-08 只在审核页加**只读展示块**与一句按钮上下文说明；只读块里出现任何
    输入控件 / 按钮，就等于事实上新增了一种决策入口。
    """
    ui_src = (Path(config.__file__).resolve().parent / "ui" / "pages" / "plan_review.py").read_text(
        encoding="utf-8"
    )
    mod = importlib.import_module("ui.pages.plan_review")

    # 审核页不产出中断、不定义 interrupt_kind
    assert "interrupt(" not in ui_src
    assert '"interrupt_kind":' not in ui_src

    # 只读展示块内零控件（按名取函数源码，防"改了别处但没改这里"）
    import inspect

    block_src = inspect.getsource(mod._render_local_env_block)
    for widget in ("st.button", "ui.button", "st.text_input", "st.text_area",
                   "st.selectbox", "st.form", "st.checkbox", "st.radio"):
        assert widget not in block_src, f"只读披露块里出现了输入控件 / 按钮：{widget}"


# =========================================================================== #
# CP-5.12-8：AC-S7-42 探测摘要不切掉关键包（**两条用例都写**，架构 §18.5(3)）
# =========================================================================== #
def _pip_freeze_120_lines() -> str:
    """构造 120 行 `pip list --format=freeze` 输出，`torch` / `transformers` 落在字母序末尾。

    行长贴近真实 freeze 输出（`pkg==x.y.z`），故 120 行整体约 2.3KB ——
    **正好落在旧上限 400 与新上限 2600 之间**，这就是 R-S7-25 在渲染端复发的原形：
    典型 venv 50~150 个包，t 开头的几乎必被 400 切掉。
    """
    fillers = [f"pkg-{i:03d}=={i % 9}.{i % 7}.{i % 5}" for i in range(118)]
    return "\n".join(sorted(fillers) + ["torch==2.3.0", "transformers==4.41.0"])


def _probe_tool_message(command: str, stdout: str, idx: int = 0) -> ToolMessage:
    """构造与工厂层逐字同形的 6 键返回 JSON ToolMessage。"""
    content = json.dumps(
        {
            "command": command, "exit_code": 0, "stdout_tail": stdout,
            "stderr_tail": "", "timed_out": False, "truncated": False,
        },
        ensure_ascii=False, sort_keys=True, default=str,
    )
    return ToolMessage(content=content, name=PROBE_TOOL_NAME, tool_call_id=f"call-{idx}")


def test_cp_5_12_8_ac_s7_42_case1_bypass_tool_render_cap_keeps_key_packages() -> None:
    """**AC-S7-42 条 1（绕过工具）**：120 行 freeze 输出直造 ToolMessage → 验**渲染端上限**。

    绕过工具就绕过了返回端 2500 字节尾部截断，故本条测的是渲染端
    `_PROBE_OUTPUT_MAX_CHARS` **单独**的行为（架构 §18.5(3) 明确要求两条分开测）。
    """
    raw = _pip_freeze_120_lines()
    assert raw.count("\n") + 1 == 120, "夹具自证：确实是 120 行"

    digest = _digest_env_probe([_probe_tool_message("pip list --format=freeze", raw)])

    assert "torch==2.3.0" in digest, (
        "字母序靠后的关键包被渲染端上限切掉了 —— R-S7-25 在 digest 渲染端原样复发"
    )
    assert "transformers==4.41.0" in digest
    # 总长确定性上界（AC-S7-42 后半句）
    assert len(digest) <= _PROBE_DIGEST_MAX_CHARS, f"digest 总长越界：{len(digest)}"


def test_cp_5_12_8_ac_s7_42_case1_is_falsifiable_at_the_old_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """条 1 的**可证伪性自证**：把渲染端上限调回 S7-08 之前的 400，同一份输入必须切掉 `torch`。

    没有这条，上面那条断言可能只是"输入本来就很短"的空跑绿。有了它才能证明
    "上限 400 → 2600"这次调值**确实是那条断言成立的原因**。
    """
    raw = _pip_freeze_120_lines()
    monkeypatch.setattr(resource_scout_mod, "_PROBE_OUTPUT_MAX_CHARS", 400)
    digest_at_old_cap = _digest_env_probe([_probe_tool_message("pip list --format=freeze", raw)])

    assert "torch==2.3.0" not in digest_at_old_cap, (
        "旧上限 400 下 torch 竟然还在 —— 说明本组用例的输入根本没触到上限，"
        "上面那条'关键包仍在'是空跑绿"
    )


class _FakePopen:
    """受控 Popen 替身：不起任何真实进程，回放指定 stdout 字节。

    刻意**只替换到 Popen 这一层**——`_run_subprocess` 的四道护栏与
    `_truncate_output` 的返回端字节截断都走**真实生产代码**，
    这正是 AC-S7-42 条 2 要验的"两级截断方向合成"。
    """

    payload: bytes = b""

    def __init__(self, cmd: Any, **kwargs: Any) -> None:
        self.returncode = 0
        self.pid = 987654

    def communicate(self, timeout: Any = None):
        return (type(self).payload, b"")

    def kill(self) -> None:  # pragma: no cover - 护栏兜底接口
        pass


def test_cp_5_12_8_ac_s7_42_case2_real_tool_two_level_truncation_keeps_key_packages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**AC-S7-42 条 2（走真实工具）**：返回端 2500 字节**保尾** + 渲染端 2600 字符**保头**，
    两级截断**方向相反**，合成之后 `torch` / `transformers` 仍在。

    这条是架构 §18.3.1 那条结构性原则的实证：
        外层上限（渲染端 2600）必须 >= 内层上限（返回端 2500 + 42 字符 marker = 2542），
        否则返回端刻意保尾留下的关键包会被渲染端取头**原样作废**。
    走的是真实 `probe_environment` 工具（清单校验 → 越界校验 → `_run_subprocess`
    四护栏 → 真实 `_truncate_output`），只把 `subprocess.Popen` 换成受控替身。
    """
    ws = tmp_path / "workspace"
    ws.mkdir()
    probe_dir = ws / "task"
    probe_dir.mkdir()
    monkeypatch.setattr(config, "WORKSPACE_DIR", ws)
    monkeypatch.setattr(lv, "WORKSPACE_DIR", ws)

    # 大 venv：输出显著超过返回端 2500 字节上限，关键包在字母序末尾。
    big_freeze = "\n".join(
        [f"some-longish-package-name-{i:04d}==1.{i}.0" for i in range(200)]
        + ["torch==2.3.0", "transformers==4.41.0"]
    )
    raw_bytes = big_freeze.encode("utf-8")
    assert len(raw_bytes) > ept._PROBE_OUTPUT_MAX_BYTES, (
        "夹具自证：原始输出必须真的超过返回端上限，否则返回端截断根本不会发生"
    )
    _FakePopen.payload = raw_bytes
    monkeypatch.setattr(lv.subprocess, "Popen", _FakePopen)

    tool = make_probe_environment_tool(base_dir=str(probe_dir))
    returned = json.loads(tool.invoke({"command": "pip list --format=freeze"}))

    # ---- 返回端：保尾截断真的发生了，且关键包被留在尾部 ----
    assert returned["truncated"] is True, "返回端应发生截断"
    stdout_tail = returned["stdout_tail"]
    assert "[truncated, kept last" in stdout_tail, "返回端截断 marker 缺失（不静默）"
    assert "torch==2.3.0" in stdout_tail and "transformers==4.41.0" in stdout_tail, (
        "返回端保尾截断应把字母序靠后的关键包留下"
    )
    marker_len = len(f"... [truncated, kept last {ept._PROBE_OUTPUT_MAX_BYTES} bytes] ...\n")
    assert len(stdout_tail) <= ept._PROBE_OUTPUT_MAX_BYTES + marker_len

    # ---- 渲染端：保头截断，因 2600 >= 2542 而没把上面那份尾部作废 ----
    digest = _digest_env_probe(
        [ToolMessage(content=json.dumps(returned, ensure_ascii=False, sort_keys=True),
                     name=PROBE_TOOL_NAME, tool_call_id="call-real-0")]
    )
    assert "torch==2.3.0" in digest, (
        "两级截断方向合成后关键包丢失 —— 渲染端上限没能覆盖返回端硬上界"
    )
    assert "transformers==4.41.0" in digest
    assert len(digest) <= _PROBE_DIGEST_MAX_CHARS, f"digest 总长越界：{len(digest)}"

    # 结构性原则本身（外层 >= 内层），断关系不断字面量
    assert _PROBE_OUTPUT_MAX_CHARS >= ept._PROBE_OUTPUT_MAX_BYTES + marker_len
    assert _PROBE_DIGEST_MAX_CHARS >= _PROBE_OUTPUT_MAX_CHARS


def test_cp_5_12_8_ac_s7_42_total_length_bound_holds_under_six_dimension_load() -> None:
    """AC-S7-42 总长上界：6 项必探维度**全部触到单条上限**时，整份 digest 仍有确定性上界。

    这是 R-S7-37（探测摘要变长的静默成本）与 R-S7-42（多卡机 + 大 venv 咬到最后一条）
    的可证伪出口——上界必须是**常量决定的**，不能依赖"这台机器碰巧输出不长"。
    """
    six = ("nvidia-smi", "nvcc --version", "free -h", "df -h .",
           "python3 --version", "pip list --format=freeze")
    messages = [
        _probe_tool_message(cmd, "X" * (_PROBE_OUTPUT_MAX_CHARS * 2), idx)
        for idx, cmd in enumerate(six)
    ]
    digest = _digest_env_probe(messages)

    assert len(digest) <= _PROBE_DIGEST_MAX_CHARS, f"总长上界失守：{len(digest)}"
    # 6 × 2600 = 15600 > 8000 ⇒ 必然触顶，故截尾说明行必须出现（不静默）
    assert digest.endswith(resource_scout_mod._PROBE_DIGEST_TRUNCATED_NOTE), (
        "触顶截尾必须追加显式中文说明行（架构 §18.3.2 / R-S7-42：不静默）"
    )
    assert digest.startswith("本机环境实测"), "截尾保头：抬头行必须留下"


# =========================================================================== #
# CP-5.12-10：AC-S7-32~42 覆盖矩阵审计
# =========================================================================== #
#: AC → 承载该 AC 的具体用例（文件名 → 用例函数名）。
#: 每条 AC 至少一个**可测断言**映射；被映射的用例必须真实存在（下方审计）。
_AC_COVERAGE_MATRIX: Dict[str, List[tuple]] = {
    "AC-S7-32": [
        ("test_sprint7_s708_plan_contract.py",
         "test_ac_s7_32_three_level_priority_replaced_the_unconditional_old_rule"),
    ],
    "AC-S7-33": [  # ⚠ 命门（已验红）
        ("test_sprint7_s708_plan_contract.py",
         "test_cp_5_12_3_ac_s7_33_no_fabrication_contract_is_delivered_under_double_absence"),
        ("test_sprint7_s708_plan_contract.py",
         "test_cp_5_12_3_no_fabrication_clause_is_not_diluted_elsewhere"),
    ],
    "AC-S7-34": [
        ("test_sprint7_s708_plan_contract.py",
         "test_cp_5_12_2_system_message_byte_identical_with_and_without_local_env_facts"),
        ("test_sprint7_s708_plan_contract.py",
         "test_cp_5_12_2_no_local_env_fact_value_leaks_into_frozen_prefix"),
        ("test_sprint6_b1_prompt_guards.py", "test_planning_prompt_body_byte_snapshot"),
        ("test_sprint7_s706_env_facts.py",
         "test_ac_s7_20_scout_prompt_body_byte_identical_across_papers"),
    ],
    "AC-S7-35": [
        ("test_s708_plan_keys.py", "test_reproduction_plan_three_way_key_sets_are_equal"),
        ("test_sprint7_s708_plan_contract.py",
         "test_cp_5_12_1_three_way_key_sets_closed_at_thirteen"),
        ("test_sprint7_s708_plan_contract.py",
         "test_cp_5_12_1_new_keys_carry_safe_defaults_on_both_paths"),
        ("test_sprint7_s708_plan_contract.py",
         "test_cp_5_12_9_legacy_plan_through_coding_and_execution_no_keyerror"),
        ("test_sprint7_s708_plan_contract.py",
         "test_cp_5_12_9_legacy_plan_through_plan_review_ui_no_keyerror"),
    ],
    "AC-S7-36": [
        ("test_sprint7_s708_plan_contract.py",
         "test_cp_5_12_5_ac_s7_36_two_machines_produce_different_human_messages"),
        ("test_sprint7_s708_plan_contract.py",
         "test_cp_5_12_5_context_builder_keeps_facts_out_when_absent"),
    ],
    "AC-S7-37": [
        ("test_sprint4_e2e.py", "test_cp_g2_1_three_interrupts_serial_same_thread"),
        ("test_sprint7_s708_payload_probe.py",
         "test_cp_5_12_7_payload_key_set_guard_is_present_precise_and_complete"),
        ("test_sprint7_s708_payload_probe.py",
         "test_cp_5_12_7_planning_payload_actually_carries_eleven_keys_including_new_one"),
        ("test_plan_review_logic.py", "test_cp_5_10_6_decision_set_still_exactly_five"),
        ("test_sprint7_s708_payload_probe.py",
         "test_cp_5_12_7_decision_set_guard_is_present_and_precise"),
        ("test_plan_review_logic.py", "test_cp_5_10_1_local_env_block_always_rendered"),
    ],
    "AC-S7-38": [  # ⚠ 命门（已验红）
        ("test_sprint7_s708_downstream.py",
         "test_cp_5_12_4_ac_s7_38_scale_reduced_forbids_science_conclusion"),
        ("test_sprint7_s708_downstream.py",
         "test_cp_5_12_4_ac_s7_38_report_carries_visible_chinese_declaration"),
        ("test_sprint7_s708_reporting_scale.py",
         "test_cp_5_9_1_scale_reduced_forbids_science_level"),
        ("test_sprint7_s708_downstream.py",
         "test_cp_5_12_6_zero_perturbation_three_chains_at_once"),
    ],
    "AC-S7-39": [
        ("test_s708_scale_reduced_directive.py", "test_cp_5_8_1_two_sides_byte_identical"),
        ("test_sprint7_s708_downstream.py",
         "test_cp_5_12_6_ac_s7_39_directive_byte_equal_across_two_sides"),
        ("test_sprint7_s708_downstream.py",
         "test_cp_5_12_6_positive_direction_three_chains_at_once"),
    ],
    "AC-S7-40": [
        ("test_s708_user_text_guard.py", "test_user_visible_static_text_has_no_internal_jargon"),
        ("test_s708_user_text_guard.py", "test_guard_itself_goes_red_on_all_three_tamper_modes"),
        ("test_sprint7_s706_env_facts.py",
         "test_s708_digest_truncated_note_is_named_user_facing_constant"),
    ],
    "AC-S7-41": [
        ("test_sprint7_s706_env_facts.py", "test_s708_probe_section_lists_six_required_dimensions"),
        ("test_sprint7_s706_env_facts.py",
         "test_s708_ac_s7_41_digest_records_command_even_when_unavailable"),
    ],
    "AC-S7-42": [
        ("test_sprint7_s708_payload_probe.py",
         "test_cp_5_12_8_ac_s7_42_case1_bypass_tool_render_cap_keeps_key_packages"),
        ("test_sprint7_s708_payload_probe.py",
         "test_cp_5_12_8_ac_s7_42_case2_real_tool_two_level_truncation_keeps_key_packages"),
        ("test_sprint7_s708_payload_probe.py",
         "test_cp_5_12_8_ac_s7_42_total_length_bound_holds_under_six_dimension_load"),
        ("test_sprint7_s706_env_facts.py", "test_s708_probe_output_cap_covers_return_side_hard_bound"),
    ],
}

#: AC-S7-43 是**真跑验收**（T-S7-5-13，须 Maria 单独授权 deepxiv 配额），
#: 代码侧无 mock 断言可承载 —— 刻意不进上表，由 handoff 与 TODO 承载。
_AC_NOT_COVERED_BY_CODE = {"AC-S7-43"}


def _collect_test_function_names(filename: str) -> Set[str]:
    """AST 解析测试文件，收集全部 `test_*` 函数名（含类内方法）。"""
    path = _TESTS_DIR / filename
    assert path.is_file(), f"覆盖矩阵指向的测试文件不存在：{filename}"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_"):
            names.add(node.name)
    return names


def test_cp_5_12_10_ac_coverage_matrix_maps_every_ac_to_an_existing_test() -> None:
    """**CP-5.12-10**：AC-S7-32~42 逐条至少一个可测断言映射，且被映射的用例**确实存在**。

    这条审计防的是"矩阵写得很满、用例早已被改名或删除"——那种情况下 AC 覆盖率
    表面 100%、实际为零（R-S7-46 / R-S7-30 同族失效模式）。
    """
    expected_acs = {f"AC-S7-{n}" for n in range(32, 43)}
    assert set(_AC_COVERAGE_MATRIX) == expected_acs, (
        f"覆盖矩阵应恰覆盖 AC-S7-32~42 共 11 条；"
        f"缺={sorted(expected_acs - set(_AC_COVERAGE_MATRIX))}, "
        f"多={sorted(set(_AC_COVERAGE_MATRIX) - expected_acs)}"
    )
    assert "AC-S7-43" not in _AC_COVERAGE_MATRIX, (
        "AC-S7-43 是真跑验收，代码侧无断言可承载；写进矩阵会造成'已覆盖'的假象"
    )
    assert _AC_NOT_COVERED_BY_CODE == {"AC-S7-43"}

    cache: Dict[str, Set[str]] = {}
    missing: List[str] = []
    for ac, entries in _AC_COVERAGE_MATRIX.items():
        assert entries, f"{ac} 没有任何映射用例"
        for filename, func_name in entries:
            if filename not in cache:
                cache[filename] = _collect_test_function_names(filename)
            if func_name not in cache[filename]:
                missing.append(f"{ac} -> {filename}::{func_name}")

    assert not missing, "覆盖矩阵指向了不存在的用例（改名 / 删除后未同步）：\n" + "\n".join(missing)


# --------------------------------------------------------------------------- #
# 关于"零改动红线四条"（`_repo_scoring.py` / `graph.py` / `env_probe_tool.py` /
# `plan_checks.py`）的收口复核：**刻意不写成测试用例**。
#
# 它是"本批相对开工点一字未动"这一**时点性质**，唯一可执行的写法是
# `git diff HEAD -- <四个路径>` 为空；而本批一旦 commit，HEAD 就包含了本批，
# 该断言从此**恒为真** —— 与 R-S7-41 那条 `EXPECTED_HASH = actual_hash`
# 完全同族的假绿（看起来是道守门、实际零守门能力）。
# 故红线复核由收口时人工执行 `git diff --stat` 并落测试报告，不进常驻测试套件。
# --------------------------------------------------------------------------- #
