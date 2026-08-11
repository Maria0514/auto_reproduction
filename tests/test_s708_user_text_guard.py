"""S7-08 用户可见**静态**文案术语守门（AC-S7-40；防 R-S7-39「扫 0 条却 passed」假绿）。

为什么新写一个文件、而不是往 ``tests/test_e2e2_message_guard.py`` 的
``_GUARDED_MODULES`` 里加模块名
==========================================================================
决定性论据不是取舍，是**扫描面错配**（架构 sp7 §18.2 / dev-plan §2 T-S7-5-11）：

既有守门扫的是 ``make_node_error(...)`` **第 3 实参**的字面量。而 S7-08 新增的用户
可见文案**一条都不在那个面上**——reporting 声明块是 ``lines.append(...)``、
plan_review 是 ``st.markdown(...)`` 字面量、term_map 是表值。扩围会两头不讨好：

1. **扫不到本次新增文案**（产品红线 AC-S7-40 直接落空）；
2. **连带打红既有文案**（``reporting.py`` 的 ``code_only`` / ``code_output_dir`` 等，
   属 TODO 登记的其余 16 处同族余项），与"那 16 处不得同期开工"正面冲突。

故本文件**只读地复用** ``_BLACKLIST`` / ``_hits``，**不改** 既有守门一个字节；
本批新增词单独放 ``_S708_EXTRA``。日后清理那 16 处时，往 ``_GUARDED_MODULES``
加模块名的路径完全不变。

"扫不到必报红"三重机制（架构 §18.7(3)：这三条本身开发时须**逐条验红**）
==========================================================================
本项目刚吃过 S7-06 的亏：守门写了、扫描面指错、实际扫 0 条却 **passed**，缺陷在
全绿状态下进了代码。三重机制各堵一种失效形态：

① **按名 import**（``getattr(module, name)`` 不给 default）
   ⇒ 常量被删除 / 改名 → ``AttributeError`` **报红**，不是 skip、不是 0 条 passed。
② **``assert scanned == EXPECTED_N``**（硬编码期望条数）
   ⇒ 少扫一条即红。见下方 EXPECTED_N 的维护语义。
③ **每条 ``assert literal.strip()``**
   ⇒ 常量被清空成 ``""`` 不能蒙混过关（空串永远不命中黑名单 = 假绿）。

外加**扫描器活性金丝雀**（沿 S7-06 AC-S7-19 同款）：断言 ``_hits`` 对
``from_scratch`` / ``resource_scout`` / ``ReAct`` 确实命中、对通俗中文不误报
—— 防"扫描器自己坏了导致零命中"的另一种假绿。

⚠ EXPECTED_N 的维护语义（禁止放宽，dev-plan CP-5.11-6）
==========================================================================
``EXPECTED_N`` 用 ``==`` 是**刻意的**，不是写死忘了改：

- 新增一条 ``TERM_LABELS`` 条目、或新增一条用户可见静态文案常量时，**必须同步 +1**；
- 这道"必须过一次守门评审"正是产品红线（新增文案必须被真正扫到）的**机制载体**；
- **任何把它放宽为 ``>=`` 的改动都等于废掉机制 ②**，须先在 dev-plan 留档才允许。

覆盖边界（明确不入本守门的东西）
==========================================================================
- ``coding._SCALE_REDUCED_DIRECTIVE`` / ``execution._SCALE_REDUCED_DIRECTIVE``：
  给**模型**看的指令，不是给用户看的文案 ⇒ 不入本守门，只入 T-5-8 的"两侧字节相等"。
- 模型生成的 ``local_fit_note`` 正文：**运行时产物**，任何静态守门都扫不到
  （架构 §18.8 ③ / PRD §10.6 已接受该残留）。其唯一防线是 prompt 契约
  （实测服从率 75%）+ AC-S7-43 真跑人眼，**不在本文件覆盖范围内**。

⚠ 已知 bug 模式 #6：``core/nodes/__init__.py`` 的显式 export 会遮蔽同名子模块属性，
访问模块级私有常量必须走 ``importlib.import_module``，不得 ``from core.nodes import x``。

离线维（零 LLM、零网络、零 deepxiv 配额）。
"""
from __future__ import annotations

import importlib
import re
import sys
from typing import List, Sequence, Tuple

import pytest

# 只读复用既有守门的黑名单与匹配器，**不修改**它们（`tests/__init__.py` 存在，
# 跨模块 import 可行；`_hits(literal: str) -> List[str]` 签名已核实）。
from tests.test_e2e2_message_guard import _BLACKLIST, _hits

# --------------------------------------------------------------------------- #
# 本批新增词：单独一张表，**绝不并进共享 `_BLACKLIST`**
# （并进去会连带扩大 resource_scout 既有扫描面 → 打红 TODO 那 16 处余项）。
# --------------------------------------------------------------------------- #
_S708_EXTRA: Tuple[str, ...] = (
    "scale_reduced",      # plan 新键 / reporting 第 4 条标注
    "local_fit_note",     # plan 新键
    "local_env_facts",    # plan interrupt payload 第 11 键
    "probe_environment",  # 环境探测工具名
    "code_only",          # 报告形态枚举 / 决策枚举
)

# --------------------------------------------------------------------------- #
# 扫描源 2：本批新增的用户可见静态文案**具名常量**（机制 ① 的作用面）。
#
# 收录判据 = "会原样出现在用户眼前的静态中文"，三条来源：
#   - T-S7-5-6  resource_scout：探测摘要截尾说明（经 payload 直达审核页，
#                dev-plan §40 P-13：S7-08 之后它是真·用户可见文案）
#   - T-S7-5-9  reporting：缩规模声明块正文 + 其引出语
#   - T-S7-5-10 plan_review：只读展示块标题/小标题/两条兜底句、
#                "仅复现代码"按钮上下文说明、讨论助手边界语
#
# ⚠ 新增任何用户可见静态文案时：提为模块级具名常量 → 加进本表 → EXPECTED_CONSTANTS_N +1。
#   写成内联 `st.markdown("...")` 字面量则本守门扫不到（§40 P-13 同款失效模式）。
# --------------------------------------------------------------------------- #
_GUARDED_CONSTANTS: Tuple[Tuple[str, str], ...] = (
    ("core.nodes.resource_scout", "_PROBE_DIGEST_TRUNCATED_NOTE"),
    ("core.nodes.reporting", "_SCALE_REDUCED_DECLARATION"),
    ("core.nodes.reporting", "_SCALE_REDUCED_NOTE_LEAD"),
    ("ui.pages.plan_review", "_LOCAL_ENV_BLOCK_TITLE"),
    ("ui.pages.plan_review", "_LOCAL_ENV_FACTS_HEADING"),
    ("ui.pages.plan_review", "_LOCAL_FIT_HEADING"),
    ("ui.pages.plan_review", "_LOCAL_ENV_FACTS_FALLBACK"),
    ("ui.pages.plan_review", "_LOCAL_FIT_NOTE_FALLBACK"),
    ("ui.pages.plan_review", "_CODE_ONLY_SCALE_REDUCED_NOTE"),
    ("ui.pages.plan_review", "_CHAT_NO_FIELD_NAME_RULE"),
    # S7-10 / T-S7-6-5：计划期两条确定性告警的文案，经 plan_review 的 st.warning 直达用户。
    ("core.plan_checks", "_W4_MESSAGE"),
    ("core.plan_checks", "_W5_MESSAGE"),
    # S7-11 / T-S7-7-7：报告里的"代码跑通"判定口径说明（此前是内联 f-string，因而
    # 从未被本守门扫到——"（B 档）"这个内部分档术语就是这么裸露到用户面前的）。
    ("core.nodes.reporting", "_SUCCESS_CRITERIA_NOTE"),
    # S7-11 / T-S7-7-6：步骤没跑完时的改判文案，经 fix_loop_history 进 UI 修复历程条。
    ("core.nodes.execution", "_INCOMPLETE_EXECUTION_SUMMARY_LEAD"),
    ("core.nodes.execution", "_INCOMPLETE_EXECUTION_FIX_HINT"),
    # === Sprint 8 批次 1b 新增（S8-01 扩围 / S8-11 三道护栏）===
    # S8-11 / T-S8-1b-3：护栏 3 第六条警示的文案，与 W4/W5 同一条通道直达用户。
    ("core.plan_checks", "_W6_MESSAGE"),
    # S8-01 / T-S8-1b-4：护栏 1 —— 计划审核页顶部只读展示的小节标题与兜底句。
    ("ui.pages.plan_review", "_SUCCESS_CRITERIA_HEADING"),
    ("ui.pages.plan_review", "_SUCCESS_CRITERIA_FALLBACK"),
)

# --------------------------------------------------------------------------- #
# EXPECTED_N 对账（2026-08-01 上磁盘实测，S7-11 / T-S7-7-7 后）：
#   - ui/term_map.py::TERM_LABELS 全量值 …………………………… ~~43~~ 44 条
#     （dev-plan §32.4 事实 9 记 41 条为 T-5-9 加 `annotation:scale_reduced` 之前的值；
#      S7-10 后为 42 条；S7-11 加 `error_category:incomplete_execution` 一条 ⇒ 43；
#      **S8-05 加 `error_category:no_verifiable_output` 一条（T-S8-2-3，2026-08-11）⇒ 44**）
#     🔴 这一条不是"顺手加的文案"，是**被防线逼出来的**：
#     tests/test_sprint5_t35_term_map.py 断言 term_map 的 error_category 域必须与
#     ErrorCategory 全部取值**相等** ⇒ 新增枚举成员而不补文案，UI 会把内部值
#     `no_verifiable_output` 原样印给用户（撞 MEMORY §4.2）。登记见 dev-plan P-S8-49。
#   - 上表具名常量 ………………………………………………………… 18 条
#     （S7-08 收口时 10 条；S7-10 加 core.plan_checks 的 W4 / W5 两条 message ⇒ 12；
#      S7-11 加 3 条：reporting._SUCCESS_CRITERIA_NOTE +
#      execution._INCOMPLETE_EXECUTION_SUMMARY_LEAD / _INCOMPLETE_EXECUTION_FIX_HINT
#      ⇒ 15；**sp8 批次 1b 加 3 条**：plan_checks._W6_MESSAGE（护栏 3 第六条警示文案，
#      T-S8-1b-3）+ plan_review._SUCCESS_CRITERIA_HEADING / _SUCCESS_CRITERIA_FALLBACK
#      （护栏 1 顶部只读展示的标题与兜底句，T-S8-1b-4）⇒ **18**）
#                                                     ------
#   合计 EXPECTED_N …………………………………………………… ~~61~~ 62 条
#
# ⚠ 三个数字必须 `==`，**禁止改成 `>=`**（理由见模块 docstring）。
#   sp8 批次 1b 的这次 +3 走的正是 docstring 里那条维护语义（新增用户可见静态文案
#   ⇒ 提为具名常量 ⇒ 加进本表 ⇒ 计数同步 +1），**不是放宽，是按机制走一遍守门评审**。
#   ⚠ `EXPECTED_TERM_LABELS_N` 本批**零改动**（批次 1b 不碰 `ui/term_map.py`）；
#   它的目标值由批次 3 的 T-S8-3-1 / T-S8-3-10 精确定档，**届时的现值是 18 不是 15**。
# --------------------------------------------------------------------------- #
EXPECTED_TERM_LABELS_N: int = 44
EXPECTED_CONSTANTS_N: int = 18
EXPECTED_N: int = EXPECTED_TERM_LABELS_N + EXPECTED_CONSTANTS_N  # == 62

# 既有守门黑名单的核心词（只做"没被掏空"的下界校验，
# 不写成相等——日后清理那 16 处时 `_BLACKLIST` 可能合法扩充）。
_BLACKLIST_CORE: Tuple[str, ...] = (
    "from_scratch",
    "use_repo",
    "hybrid",
    "resource_scout",
    "ReAct",
)


def _word_hits(literal: str, words: Sequence[str]) -> List[str]:
    """在 `literal` 中按词边界、大小写不敏感地找 `words` 命中项。

    与 `tests.test_e2e2_message_guard._hits` 同款正则语义（该函数只认死
    `_BLACKLIST`，无法传入自定义词表，故此处重述一次匹配规则）；
    两者语义是否仍等价由 `test_scanner_liveness_canary` 交叉验证。
    """
    return [
        w for w in words
        if re.search(rf"(?<![0-9A-Za-z_]){re.escape(w)}(?![0-9A-Za-z_])",
                     literal, flags=re.IGNORECASE)
    ]


def _all_hits(literal: str) -> List[str]:
    """共享黑名单命中 + 本批新增词命中。"""
    return _hits(literal) + _word_hits(literal, _S708_EXTRA)


def _collect_literals() -> List[Tuple[str, str]]:
    """收集三个扫描源的**全量**文案，返回 [(来源标签, 字面量), ...]。

    机制 ①：具名常量走 `getattr(module, name)` **不给 default** ——
    常量被删除 / 改名时抛 `AttributeError`，用例**报红**（不是 skip、不是 0 条 passed）。
    """
    collected: List[Tuple[str, str]] = []

    # 扫描源 1：ui/term_map.py::TERM_LABELS 全量**值**
    # （key 天然是内部枚举 `domain:value`，本就不给用户看，只能扫值）。
    term_map = importlib.import_module("ui.term_map")
    for key, value in term_map.TERM_LABELS.items():
        collected.append((f"ui/term_map.py::TERM_LABELS[{key!r}]", value))

    # 扫描源 2：本批新增的用户可见静态文案具名常量（按名 import）。
    for module_path, const_name in _GUARDED_CONSTANTS:
        module = importlib.import_module(module_path)
        value = getattr(module, const_name)  # ← 机制 ①：缺失即 AttributeError
        collected.append((f"{module_path}.{const_name}", value))

    return collected


def test_guarded_constants_are_importable_by_name() -> None:
    """机制 ①：每条被守文案都必须能按名 import 到（删除 / 改名 → AttributeError → 红）。

    刻意不 try/except、不给 getattr 默认值、不 skip —— S7-06 的教训正是
    "守门目标不在了，用例却安静地绿着过去"。
    """
    for module_path, const_name in _GUARDED_CONSTANTS:
        module = importlib.import_module(module_path)
        value = getattr(module, const_name)
        assert isinstance(value, str), (
            f"{module_path}.{const_name} 必须是 str 静态文案，实际 {type(value).__name__}"
        )


def test_expected_n_accounting_is_closed() -> None:
    """EXPECTED_N 账目闭合：常量表条数 == EXPECTED_CONSTANTS_N，且总数 == 两项之和。

    与主用例的 `scanned == EXPECTED_N` 互为双保险：从 `_GUARDED_CONSTANTS`
    偷偷移走一条时，这里先红一次。
    """
    assert len(_GUARDED_CONSTANTS) == EXPECTED_CONSTANTS_N, (
        f"_GUARDED_CONSTANTS 实际 {len(_GUARDED_CONSTANTS)} 条，"
        f"EXPECTED_CONSTANTS_N={EXPECTED_CONSTANTS_N}；"
        "新增/删除用户可见文案常量时必须同步这个数字（禁止放宽为 >=）。"
    )
    assert len(set(_GUARDED_CONSTANTS)) == len(_GUARDED_CONSTANTS), (
        "_GUARDED_CONSTANTS 存在重复项——重复会虚增 scanned，掩盖真实漏扫。"
    )
    assert EXPECTED_N == EXPECTED_TERM_LABELS_N + EXPECTED_CONSTANTS_N


def test_user_visible_static_text_has_no_internal_jargon() -> None:
    """AC-S7-40 主守门：三个扫描源全量文案零内部术语，且三重机制同时在岗。

    - 机制 ①：`_collect_literals` 内按名 import（常量没了 → AttributeError）；
    - 机制 ②：`scanned == EXPECTED_N`（少扫一条即红，**禁止改 `>=`**）；
    - 机制 ③：逐条 `literal.strip()`（常量被清空成 `""` 不能蒙混）。
    """
    literals = _collect_literals()
    scanned = len(literals)

    # 机制 ②：硬编码期望条数。改这个数字前请先读模块 docstring 的维护语义。
    assert scanned == EXPECTED_N, (
        f"本次实际扫描 {scanned} 条，期望 {EXPECTED_N} 条"
        f"（term_map {EXPECTED_TERM_LABELS_N} + 具名常量 {EXPECTED_CONSTANTS_N}）。\n"
        "少扫 = 有文案脱离守门覆盖（S7-06 同款假绿）；多扫 = 新增文案未登记评审。\n"
        "两种情况都必须先确认原因，再同步更新 EXPECTED_* 常量——"
        "**不要把断言放宽为 >= 来消红**。"
    )

    violations: List[str] = []
    for source, literal in literals:
        # 机制 ③：空串永远不命中黑名单，等于假绿，必须先拦下来。
        assert isinstance(literal, str), f"{source} 不是 str：{type(literal).__name__}"
        assert literal.strip(), (
            f"{source} 是空串 / 纯空白——被清空的文案在黑名单扫描下永远'零命中'，"
            "属假绿，禁止以此方式消红。"
        )

        hit = _all_hits(literal)
        if hit:
            violations.append(f"  {source} 命中 {hit} -> {literal!r}")

    assert not violations, (
        "用户可见文案禁用内部标识符（内部枚举 / 字段名 / 节点名 / 工具名 / 技术术语），"
        "请改为通俗中文：\n"
        + "\n".join(violations)
        + f"\n（本次共扫描 {scanned} 条：ui/term_map.py::TERM_LABELS "
        f"{EXPECTED_TERM_LABELS_N} 条 + 具名常量 {EXPECTED_CONSTANTS_N} 条）"
    )


def test_scanner_liveness_canary() -> None:
    """扫描器活性金丝雀：防"扫描器自己坏了 → 零命中 → 假绿"（沿 S7-06 AC-S7-19）。

    正向：共享黑名单的三个代表词必须真能命中。
    负向：通俗中文、以及"词的一部分"不得误报（词边界生效）。
    交叉：`_word_hits` 与既有 `_hits` 在共享黑名单上语义等价。
    """
    assert _hits("准备好之后我们 from_scratch 重写") == ["from_scratch"]
    assert _hits("这一步由 resource_scout 负责") == ["resource_scout"]
    assert _hits("走的是 ReAct 循环") == ["ReAct"]
    # 大小写不敏感
    assert _hits("FROM_SCRATCH") == ["from_scratch"]

    # 负向：通俗中文零误报
    assert _hits("这台机器的实测情况与计划适配说明") == []
    assert _all_hits("本次复现是按这台机器实际跑得动的规模缩小后做的") == []
    # 负向：词边界生效（不做子串匹配）
    assert _hits("from_scratched") == []
    assert _hits("ReActor") == []

    # 交叉验证：本文件的 `_word_hits` 与既有 `_hits` 正则语义仍等价
    for sample in (
        "准备好之后我们 from_scratch 重写",
        "这一步由 resource_scout 负责",
        "走的是 ReAct 循环",
        "这台机器的实测情况与计划适配说明",
        "from_scratched",
    ):
        assert sorted(_word_hits(sample, _BLACKLIST)) == sorted(_hits(sample)), (
            f"_word_hits 与 _hits 在 {sample!r} 上语义分叉——匹配规则已腐坏"
        )


def test_s708_extra_words_each_hit_a_synthetic_sample() -> None:
    """`_S708_EXTRA` 五个新增词各自能命中一条人造样本（词表没写错、没写漏）。"""
    samples = {
        "scale_reduced": "标注里带了 scale_reduced 这个标记",
        "local_fit_note": "计划的 local_fit_note 字段为空",
        "local_env_facts": "上下文里缺少 local_env_facts",
        "probe_environment": "调用 probe_environment 工具探测",
        "code_only": "本次报告形态是 code_only",
    }
    assert set(samples) == set(_S708_EXTRA), (
        "人造样本与 _S708_EXTRA 词表不同步：新增词必须同时补一条样本，"
        f"样本缺={sorted(set(_S708_EXTRA) - set(samples))}，"
        f"样本多={sorted(set(samples) - set(_S708_EXTRA))}"
    )
    for word, sample in samples.items():
        assert _word_hits(sample, _S708_EXTRA) == [word], (
            f"新增词 {word!r} 在样本 {sample!r} 上未按预期命中"
        )


def test_guard_itself_goes_red_on_all_three_tamper_modes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """三重自证的**常驻化**：三种篡改形态下主守门必须抛异常，绝不能安静地绿。

    架构 §18.7(3) 只要求"开发时逐条验红"（一次性人工仪式）。本用例把那三次验红
    钉进代码——日后有人把 `getattr` 改成带默认值、把 `==` 放宽成 `>=`、或去掉
    `strip()` 判空，本用例当场红，不必等下一次人工验红。

    三种篡改与真实退化形态的等价性：
    - `delattr` ≡ 常量在源码里被删除 / 改名（`importlib` 拿到的是同一个模块对象，
      `getattr` 一样抛 `AttributeError`）；
    - 截短 `_GUARDED_CONSTANTS` ≡ 有文案脱离守门覆盖（scanned 少一条）；
    - 置 `""` ≡ 常量被清空（空串永远零命中 = 假绿）。
    """
    reporting = importlib.import_module("core.nodes.reporting")
    self_module = sys.modules[__name__]

    # 机制 ①：常量不在了 → AttributeError（不是 skip、不是 0 条 passed）
    with monkeypatch.context() as m:
        m.delattr(reporting, "_SCALE_REDUCED_NOTE_LEAD")
        with pytest.raises(AttributeError):
            test_user_visible_static_text_has_no_internal_jargon()

    # 机制 ②：少扫一条 → AssertionError（scanned != EXPECTED_N）
    with monkeypatch.context() as m:
        m.setattr(self_module, "_GUARDED_CONSTANTS", _GUARDED_CONSTANTS[:-1])
        with pytest.raises(AssertionError, match=r"实际扫描"):
            test_user_visible_static_text_has_no_internal_jargon()

    # 机制 ③：常量被清空成 "" → AssertionError（不能靠"零命中"蒙混）
    with monkeypatch.context() as m:
        m.setattr(reporting, "_SCALE_REDUCED_NOTE_LEAD", "")
        with pytest.raises(AssertionError, match=r"空串"):
            test_user_visible_static_text_has_no_internal_jargon()


def test_shared_blacklist_is_reused_not_mutated() -> None:
    """复用不复制、且**不改**共享黑名单（dev-plan CP-5.11-6 的机制化部分）。

    - 下界：既有五个核心词一个都不能被拿掉（拿掉 = 既有守门被悄悄放水）；
    - 上界：本批五个新增词**不得**出现在共享 `_BLACKLIST` 里——它们一旦并进去，
      就会连带扩大 resource_scout 既有扫描面，打红 TODO 登记的其余 16 处余项。

    注：刻意**不**断言 `_BLACKLIST` / `_GUARDED_MODULES` 完全相等——日后清理那 16 处时
    往 `_GUARDED_MODULES` 加模块名是既定路径，不该被本文件挡住。
    """
    missing = [w for w in _BLACKLIST_CORE if w not in _BLACKLIST]
    assert not missing, f"共享 _BLACKLIST 被掏空了核心词：{missing}"

    lowered = {w.lower() for w in _BLACKLIST}
    leaked = [w for w in _S708_EXTRA if w.lower() in lowered]
    assert not leaked, (
        f"本批新增词 {leaked} 被并进了共享 _BLACKLIST；"
        "它们只能待在 _S708_EXTRA 里（理由见本用例 docstring）。"
    )
