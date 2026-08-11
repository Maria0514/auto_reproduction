"""Sprint 8 批次 1a — T-S8-1a-3：**新建** coding 侧 system prompt 字节哈希门。

## 为什么本文件是"新建"而不是"换发"（P-S8-5）

`docs/sprint8/prd.md` AC-S8-21② 的字面写法是「执行侧、**编码侧**、规划侧提示词哈希
基线**换发**」——但**编码侧根本没有基线可换发**：

- 全仓 ``grep -rn "hexdigest" tests/`` 命中的**真 prompt 字节门只有三处**：
  planning 一处（``test_sprint6_b1_prompt_guards.py:79``）+ execution 两处
  （``test_sprint5_t14_execution_prompt.py`` / ``test_sprint7_s710_exec_locality.py``）。
  **coding 侧零。**
- 唯一沾边的 ``test_sprint5_t13_coding_prompt.py:180-183`` 是**自锁定形态**：

  .. code-block:: python

     expected_prefix = _CODING_SYSTEM_PROMPT_BODY + _CODING_HONESTY_SECTION
     assert prefix_a == expected_prefix

  等号两边都从**同一组常量**算出 ⇒ **常量改成什么它都恒绿**，与 R-S7-41 那道
  ``x == x``、以及 sprint7 P-27 记的 execution 侧旧门**完全同族，零守门效力**。
  ⚠ 该断言**本 Sprint 不动**：它证明的另一件事（"稳定前缀 == 两常量拼接"这个
  **结构**关系）仍然成立且有价值。**本文件是补充，不是替换。**
- 旁证：sp7 S7-13 已用非侵入探针实证——改 coding 主体后全量 2506 passed / 0 failed，
  **零红**（sprint7 dev-plan §63 P-64）。

## 为什么必须"先建后改"

sprint7 T-S7-6-2 / §48 P-27 已确立的范式：用**改前**哈希建门 → 下一个任务
（`T-S8-2-1b`，批次 2）一改 prompt，门**当场红** ⇒ **那次红本身就是"这道门是真的"
的活体证明**。反过来"改完再建门"，建出来的基线是照着改后字节写死的、**永远绿**，
与自锁定形态等价（R-S7-41 / sprint7 P-27 两次实证）。

⇒ **本文件落盘时 ``core/nodes/coding.py`` 逐字节未改**（CP-1a.3-7 自证）。

## 覆盖的检查点

- CP-1a.3-1 门存在且**当前绿**（基线取自 CP-1a.1-5 现算的改前哈希）
- CP-1a.3-2 断言右侧**是硬编码字面量**（元断言，AST 扫；专防 R-S7-41 自锁定坑）
- CP-1a.3-3 失败信息含**新旧两个哈希**
- CP-1a.3-4 "主体无论文级动态变量"断言（正则 ``\\d{4}\\.\\d{4,5}`` 零命中）

## 不扩围声明

``docs/TODO.md:633`` 登记的是 ``coding.py`` / ``resource_scout.py` **两侧**都缺门，
但 **Sprint 8 不动 `resource_scout` 的 prompt** ⇒ 按同一条纪律（"日后改那处 prompt
时再补"）**本批不建它的门**，其条目**不因本 Sprint 关闭**（§15 P-S8-5 / R-S8-21）。

全部离线维（零 LLM、零网络、零 deepxiv 配额）。
"""

from __future__ import annotations

import ast
import hashlib
import importlib
import inspect
import re
from typing import List

# 已知坑 #6：core/nodes/__init__.py 的显式 export 会让 callable 遮蔽子模块，
# 一律用 importlib.import_module 取模块属性（dev-plan §0.3 第 7 条）。
coding_module = importlib.import_module("core.nodes.coding")


# ──────────────────────────────────────────────────────────────────────────────
# 基线字面量（🔴 必须是硬编码字符串常量，禁止赋成运行时算出的值）
#
# 取值来源：T-S8-1a-1 / CP-1a.1-5 于 2026-08-06 在 HEAD=0e250fb 上现算，
# 与 sprint7 dev-plan §60.2 事实 15 记录的 37ec6ee2b1606715 / 3052 字符复核一致。
#
# 🔴 改动 _CODING_SYSTEM_PROMPT_BODY 或 _CODING_HONESTY_SECTION 必须三件套齐做：
#   ①重算并同步更新下方字面量；
#   ②在 docs/sprint8/dev-plan.md §15.1 对应行补新哈希 + 变更原因；
#   ③跑一次验红（临时改 body → 本门变红）。
# 🔴 **严禁改回 ``EXPECTED_* = <运行时算出的值>`` 的自锁定形态**
#    （R-S7-41 / sprint7 P-27 已两次实证：那种写法恒绿、零守门效力）。
# ──────────────────────────────────────────────────────────────────────────────

# _CODING_SYSTEM_PROMPT_BODY 单体（本 Sprint S8-02 要改的就是它，红得精准）
# 🔴 T-S8-2-1b 换发（2026-08-11）：<METRICS> 通道整体退场，主体四处改动 ——
#   ①第 5 条"并打印关键指标"→"按下面的产出约定把结果落盘"；②"入口脚本指标输出约定"
#   整段 6 行 → "实验结果产出约定"（summary.json / 顶层对象 / 例子 / 落点 / 无结果不写）；
#   ③修复回合"保持入口脚本的 <METRICS> 输出约定不变"→"保持…结果文件产出约定不变"。
#   （第四处 :113 在 CODING_OUTPUT_SCHEMA 里，不进主体，故不影响本哈希。）
# 换发前基线：37ec6ee2b1606715 / 3052 字符（T-S8-1a-1 于 HEAD=0e250fb 现算）。
# 换发时的验红实证（CP-2.1b-7，"门有牙"活体证明）：改完主体后本门当场变红，
#   报 “当前：ff741c03002db5f8，基线：37ec6ee2b1606715；当前长度 3112，基线长度 3052”。
# ⚠ 上面那个 ff741c03002db5f8 是**初版文案**的哈希，**不是最终基线**：初版产出约定的
#   JSON 例子写成 {"dataset": "CIFAR-10", ...}，被既有防线 test_sprint3_f3.py::
#   test_cp_f3_1_body_constant_carries_no_paper_level_variable 判红（该防线禁止主体
#   常量出现任何论文级字面量，"CIFAR-10" 正是它的禁词之一）⇒ 例子改为不含数据集名的
#   {"accuracy": 0.873, "f1": 0.81, "epochs": 20} 后重算，得下方最终值。
#   🔴 留痕理由：这一红说明「例子要具体才有效」与「主体禁含论文级字面量」两条约束会
#   互相拉扯，后续改本段文案的人容易再踩一次。
EXPECTED_BODY_HASH = "92362448116543e2"
EXPECTED_BODY_LEN = 3101

# 稳定前缀 = 主体 + 诚实红线段（R-PC4，口径沿 test_sprint5_t13_coding_prompt.py:167）。
# 单守主体会漏掉「诚实红线段被改」的情形，故并建一条。
# 🔴 T-S8-2-1b 同批换发：诚实红线段**一字未动**，本条变动全部来自主体（3534-3101=433
#    = 红线段长度，与换发前 3485-3052=433 逐字相等 ⇒ 反证红线段未被误改）。
# 换发前基线：2973ea0f0ad17502 / 3485 字符；验红实测 3c65d42c2a38a873 / 3545（初版文案，
#   同上被 CP-F3-1 判红而作废）⇒ 最终值如下。
EXPECTED_PREFIX_HASH = "3d9391e7b6b337ed"
EXPECTED_PREFIX_LEN = 3534


def _sha16(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _body() -> str:
    return coding_module._CODING_SYSTEM_PROMPT_BODY


def _stable_prefix() -> str:
    return coding_module._CODING_SYSTEM_PROMPT_BODY + coding_module._CODING_HONESTY_SECTION


# ──────────────────────────────────────────────────────────────────────────────
# CP-1a.3-1 / CP-1a.3-3：字节门本体（两条断言）
# ──────────────────────────────────────────────────────────────────────────────


class TestCodingPromptByteGate:
    """coding 侧 system prompt 字节哈希门（S8-02 改动前基线，先建后改）。"""

    def test_cp_1a_3_1_body_byte_hash_matches_baseline(self) -> None:
        """_CODING_SYSTEM_PROMPT_BODY 字节哈希 == 写死的改前基线。

        本 Sprint 的 T-S8-2-1b（批次 2）会改这段主体（三处 <METRICS> 教学文本清除
        + 补产出约定），届时**本断言必须当场变红**——那次红就是这道门为真的活体证明。
        """
        actual_hash = _sha16(_body())
        actual_len = len(_body())
        assert actual_hash == EXPECTED_BODY_HASH, (
            f"coding prompt 主体字节已变更（当前：{actual_hash}，基线：{EXPECTED_BODY_HASH}；"
            f"当前长度 {actual_len}，基线长度 {EXPECTED_BODY_LEN}）"
            "——若是合规变更，请重算并更新本文件字面量，并在 dev-plan §15.1 留档变更原因"
        )
        assert actual_len == EXPECTED_BODY_LEN, (
            f"coding prompt 主体长度已变更（当前：{actual_len}，基线：{EXPECTED_BODY_LEN}）"
        )

    def test_cp_1a_3_1_stable_prefix_byte_hash_matches_baseline(self) -> None:
        """稳定前缀（主体 + 诚实红线段）字节哈希 == 写死的改前基线。

        单守主体会漏掉 _CODING_HONESTY_SECTION 被改的情形，故并建这一条。
        """
        actual_hash = _sha16(_stable_prefix())
        actual_len = len(_stable_prefix())
        assert actual_hash == EXPECTED_PREFIX_HASH, (
            f"coding prompt 稳定前缀（主体+诚实红线段）字节已变更"
            f"（当前：{actual_hash}，基线：{EXPECTED_PREFIX_HASH}；"
            f"当前长度 {actual_len}，基线长度 {EXPECTED_PREFIX_LEN}）"
            "——若是合规变更，请重算并更新本文件字面量，并在 dev-plan §15.1 留档变更原因"
        )
        assert actual_len == EXPECTED_PREFIX_LEN, (
            f"coding prompt 稳定前缀长度已变更（当前：{actual_len}，基线：{EXPECTED_PREFIX_LEN}）"
        )


# ──────────────────────────────────────────────────────────────────────────────
# CP-1a.3-2：元断言 —— 证否自锁定形态（专防 R-S7-41）
# ──────────────────────────────────────────────────────────────────────────────


class TestGateIsNotSelfLocking:
    """元检查：本文件的基线**必须**是硬编码字面量，不得从运行时算出。

    立此条的理由（两次实证，不是假想风险）：
      - R-S7-41：``test_sprint6_b1_prompt_guards.py`` 的 planning 门在 sp6~sp7 期间
        一直写成 ``EXPECTED_HASH = actual_hash``，即 ``x == x`` 恒真，docstring
        自称的"字节级回归门"从来就不存在，零守门能力地活了两个 sprint。
      - sprint7 P-27：execution 侧旧门同族死法。
    """

    _BASELINE_NAMES = (
        "EXPECTED_BODY_HASH",
        "EXPECTED_BODY_LEN",
        "EXPECTED_PREFIX_HASH",
        "EXPECTED_PREFIX_LEN",
    )

    def _module_ast(self) -> ast.Module:
        source = inspect.getsource(importlib.import_module(__name__))
        return ast.parse(source)

    def test_cp_1a_3_2_baselines_are_hardcoded_literals(self) -> None:
        """四个 EXPECTED_* 基线的赋值右侧必须是字面量常量（str / int）。

        任何 ``EXPECTED_X = <函数调用 / 名字引用 / 表达式>`` 形态一律判红——
        那正是自锁定假门的长相。
        """
        tree = self._module_ast()
        seen: List[str] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            for target in node.targets:
                if not isinstance(target, ast.Name) or target.id not in self._BASELINE_NAMES:
                    continue
                seen.append(target.id)
                assert isinstance(node.value, ast.Constant), (
                    f"{target.id} 的赋值右侧不是字面量常量，而是 "
                    f"{type(node.value).__name__} —— 这正是 R-S7-41 自锁定假门的形态"
                    "（右侧一旦从运行时算出，断言恒真、零守门效力）"
                )
                assert isinstance(node.value.value, (str, int)), (
                    f"{target.id} 的字面量类型应为 str / int，实为 {type(node.value.value)}"
                )
        assert sorted(seen) == sorted(self._BASELINE_NAMES), (
            f"未在模块顶层找到全部基线常量的字面量赋值：实际找到 {sorted(seen)}，"
            f"期望 {sorted(self._BASELINE_NAMES)}（基线被改名或被挪走都会让本元断言失效）"
        )

    def test_cp_1a_3_2_gate_body_does_not_recompute_baseline(self) -> None:
        """门函数体内不得出现对 EXPECTED_* 的赋值（防"函数里就地覆盖成 actual"）。"""
        for func in (
            TestCodingPromptByteGate.test_cp_1a_3_1_body_byte_hash_matches_baseline,
            TestCodingPromptByteGate.test_cp_1a_3_1_stable_prefix_byte_hash_matches_baseline,
        ):
            src = inspect.getsource(func)
            for name in self._BASELINE_NAMES:
                assert f"{name} =" not in src and f"{name}=" not in src, (
                    f"{func.__name__} 函数体内对 {name} 做了赋值 —— 自锁定形态，禁止"
                )

    def test_cp_1a_3_3_failure_message_carries_both_hashes(self) -> None:
        """CP-1a.3-3：失败信息必须同时打出**当前**与**基线**两个哈希。

        门红的时候，后人要能一眼看出"该重算基线了"而不是"哪里坏了"
        （沿 test_sprint6_b1_prompt_guards.py:82 范式）。
        """
        src = inspect.getsource(
            TestCodingPromptByteGate.test_cp_1a_3_1_body_byte_hash_matches_baseline
        )
        assert "{actual_hash}" in src, "失败信息未打出当前哈希"
        assert "{EXPECTED_BODY_HASH}" in src, "失败信息未打出基线哈希"


# ──────────────────────────────────────────────────────────────────────────────
# CP-1a.3-4：主体无论文级动态变量（已知 bug 模式 #4，Prompt Cache 字节级幂等）
# ──────────────────────────────────────────────────────────────────────────────


class TestCodingPromptNoDynamicVariables:
    """与 planning / resource_scout 的既有守门对齐：主体内零论文级动态变量。"""

    _ARXIV_ID_PATTERN = r"\d{4}\.\d{4,5}"

    def test_cp_1a_3_4_body_has_no_paper_level_dynamic_variables(self) -> None:
        matches = re.findall(self._ARXIV_ID_PATTERN, _body())
        assert not matches, (
            f"coding prompt 主体含具体 arxiv_id 形态字串：{matches}"
            "（违反 Prompt Cache 前缀稳定约束，已知 bug 模式 #4）"
        )

    def test_cp_1a_3_4_stable_prefix_has_no_paper_level_dynamic_variables(self) -> None:
        matches = re.findall(self._ARXIV_ID_PATTERN, _stable_prefix())
        assert not matches, (
            f"coding prompt 稳定前缀含具体 arxiv_id 形态字串：{matches}"
            "（违反 Prompt Cache 前缀稳定约束，已知 bug 模式 #4）"
        )
