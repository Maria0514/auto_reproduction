"""Sprint 8 / S8-11 护栏 3 —— `check_plan` 第六条警示 W6 的**行为覆盖 + 常驻验红**。

本文件补的是一个**零覆盖缺口**，不是"再加几条用例"
==========================================================================
交付时（批次 1b，`T-S8-1b-3`）W6 的行为验证走的是一次性脚本 `selftest_1b3.py`，
脚本随手删掉了 ⇒ **committed 测试里 W6 判定块一次都不会被执行**。实测：`tests/` 下
`check_plan(` 共 **36 处调用，带第三个实参的 0 处**（⚠ 口径 = **本文件加入之前**的
`tests/` 目录 AST 静态清点，取值时刻 2026-08-09；本文件自身随后引入了两参 / 三参 /
`*args` 解包三种形态，**该数字不再描述当下**，别拿它去核现在的仓库）。
两参调用 ≡ `paper_analysis=None` ⇒ 候选集为空 ⇒ **在读 `plan` 之前就 return** ⇒
W6 判定块**从不执行**。此前仅有的两处"提到 W6"的用例守的是别的东西：
``test_s708_user_text_guard.py`` 守 ``_W6_MESSAGE`` 的**文案**、
``test_sprint7_s710_exec_locality.py::test_cp_6_5_5_*`` 守 ``check_plan`` 的**签名**。
⇒ 把整块 W6 判定删掉，改前的回归**照样全绿**。这正是本项目反复吃亏的
「**写了断言 ≠ 断言有牙**」。

🔴 防"验红随脚本一起蒸发"的机制：验红是**常驻用例**，不是一次性动作
==========================================================================
下方 ``_MUTANTS`` 把 `core/plan_checks.py` 的源码 **在内存里** AST 改写成三种残废版本，
再拿**同一批 case 表**去跑，断言结论**必须翻面**：

  ``drop``      整块 W6 判定删掉        ⇒ `_W6_EXPECTED` 全部**不再报** W6（正向断言有牙）
  ``always``    W6 改成无条件上报        ⇒ `_W6_ABSENT` 全部**开始报** W6（负向断言有牙）
  ``ordering``  `if fact_terms:`→`if True:` ⇒ `_EMPTY_CANDIDATE_ABSENT` 全部报 W6
                （"候选集空则早退"这条**顺序**有牙；dev-plan `CP-1b.3-9` 验红②的常驻版）

要害在**共用 case 表**：真实断言与突变断言 parametrize 的是同一个元组表 ⇒
**往表里加一条 case，它自动同时获得"正向绿"与"红态会红"两重检查**，
想加一条没牙的用例在结构上就做不到。突变**全在内存里做**（`ast` + `compile` +
`exec` 到独立命名空间），**零磁盘写入、不碰 `core/` 一个字节**——沿
``docs/MEMORY.md`` §6「断言副作用不该发生的用例，落点必须隔离；判据是红态不是绿态」
的同一条纪律：验红本身也不该在红的那一次污染工作区。

✅ 口径已塌回单口径（2026-08-09，`AR-S8-16` 裁定落地后）
==========================================================================
W6 的候选集有三处来源（`metrics` 元素 / `datasets` 元素 / `baseline_results` **键**）。
本文件初版写于 `AR-S8-16` 裁定**并行进行中**，涉及第三候选源的两条用例刻意写成
**双口径分支**（源在 ⇒ 断言不报；源被裁掉 ⇒ 断言照报），以免押注裁定何时落地。

🔴 **该过渡态已结束，双口径分支已全部塌成硬断言。** 架构 v2.7 `AR-S8-16` 裁定
「`planning.py::_digest_paper_analysis` 加第 5 键 `baseline_results`；`core/plan_checks.py`
与 W6 判据**一字不动**」，代码已落地 ⇒ 两处都只剩**唯一正确口径**，没有第二种。

⚠ **塌回来这件事本身的教训（比这条缺陷值钱）**：双口径分支在**绿的时候**和硬断言
长得一模一样，差别只在**它该红的那一次**。主控 2026-08-09 手工把 `planning.py` 那一行
摘掉实测：本文件 **133 条一条都没红** —— 双口径版只是静默切回 A 支继续绿，
那道守门于是退化成「**记录当前行为**」而不是「**守**」，缺陷可以用和上一批
一模一样的方式重新藏进来。⇒ **凡写"能适应两种口径"的用例，必须当场约定谁在什么
时点负责把它塌回单口径**；没有这个约定，"适应性"会在裁定落地的那一刻变成盲区，
而且**不会有任何一条红灯来提醒你**。

⇒ 现在 `planning.py` 那一行一摘就红的断言共 **4 处**（全部单口径、互相独立）：

  - ``test_g8_paper_reported_baseline_reaches_check_plan``   架构 §15.5 **G8** 的唯一承载
  - ``test_g9_vague_criteria_warns_when_only_baseline_present`` §15.5 **G9**（反向）
  - ``test_production_digest_key_set_is_exact``            补 `AR-S8-16` 点名的"内层键无人守"
  - ``test_red_state_digest_without_baseline_results``      **常驻验红**：摘键必翻面

⚠ 塌口径时实测挖出的第二件事（已写进 `test_red_state_digest_without_baseline_results`
的 docstring，**架构 §15.5 G8 那一行需要订正**）：G8 自带的验红条件「去掉该键 → 本条
必红」，若 G8 只按它写的期望「不报 W6」实现，**是不成立的** —— 键没了 ⇒ 候选集空 ⇒
早退（G3 宁窄勿宽）⇒ **照样不报**。⇒ G8 必须带白盒断言（键在 / 候选集里有它），
**行为面的红灯只有 G9 一个**。

  另：候选集为空的 fixture 仍一律用"**不含**任何候选键"的形态（`{}` / `None` / 三键
  显式置空）⇒ 上游将来再新增候选源也不会把它们悄悄变成非空。这条与口径无关，保留。

覆盖矩阵（用例 ID → 场景）
==========================================================================
  T-W6-01  两参调用（既有调用点的形态）→ 无 W6，且五条既有 W 与三参调用逐条相同
  T-W6-02  显式 `paper_analysis=None` / 非 dict → 无 W6、不抛
  T-W6-03  候选集为空的六种形态 → 不报（含"空标准 + 空候选"的优先级自证）
  T-W6-04  候选集非空 + 空话标准 → 报
  T-W6-05  候选集非空 + 空标准四形态（空串 / 纯空白 / 缺键 / 非 str）→ 报
  T-W6-06  标准命中指标名 → 不报
  T-W6-07  三处候选源各自生效 + 大小写不敏感
  T-W6-08  空白候选不得成为"命中一切"的万能候选
  T-W6-09  文案：纯中文、零内部字段名、与 `_W6_MESSAGE` 同一份
  T-W6-10  W6 与既有五条 W 互不干扰（G5 契约回归）+ 至多出现一次
  T-W6-11  已登记局限：具体但宽松的标准照样过（不得包装成"防止画低"的保证）
  T-W6-12  生产接线：审核页调用点确实传了第三个实参（否则 W6 在生产上永久静默）
  T-W6-13  生产数据形状：经 `_digest_paper_analysis` 压缩后的摘要仍能驱动 W6
  T-W6-14  链路级 **G8**：达标线引用论文自报基线 → **不报**（`AR-S8-16` 的接缝）
  T-W6-15  链路级 **G9**：同一摘要 + 空话达标线 → **报**（指标/数据集皆空时不再静音）
  T-W6-16  **G10 内层面**：生产摘要的键集合精确等于 5 键
  T-W6-M*  三种突变下 case 表整体翻面（常驻验红，`plan_checks` 侧）
  T-W6-MD  摘要突变：拿掉第 5 键 → G8 那条必然翻面（常驻验红，`planning` 侧）

运行::

    .venv/bin/python -m pytest tests/test_sprint8_s811_w6_criteria_guard.py -v
"""
from __future__ import annotations

import ast
import inspect
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, NamedTuple, Optional, Tuple

import pytest

from core import plan_checks
from core.plan_checks import _W6_MESSAGE, check_plan


# --------------------------------------------------------------------------- #
# 小工具
# --------------------------------------------------------------------------- #

def _rules(warnings: List[Dict[str, str]]) -> List[str]:
    """把 check_plan 的返回压成 rule 字符串列表（保持顺序，便于比对重复项）。"""
    return [w["rule"] for w in warnings]


def _plan(criteria: Any = None, *, omit: bool = False) -> Dict[str, Any]:
    """最小计划：只带 success_criteria，**不触发 W1~W5**（让 W6 的断言干净可读）。

    omit=True 时**整个键都不给**，用于覆盖"缺键"这一形态。
    """
    if omit:
        return {"plan_summary": "最小计划"}
    return {"plan_summary": "最小计划", "success_criteria": criteria}


# 候选集 fixture：三处来源各给一份，外加一份三源齐全的。
# ⚠ 这些 dict **只放候选键**，不放无关键——上游若新增候选源，无关键会让 fixture 的
#   语义漂移（"我以为它是空的，它突然有候选了"）。
_PA_METRICS = {"metrics": ["knn_accuracy", "Recall@5"]}
_PA_DATASETS = {"datasets": ["MuSiQue"]}
_PA_BASELINE = {"baseline_results": {"HippoRAG-MuSiQue-F1": 0.61}}
_PA_FULL = {
    "metrics": ["knn_accuracy", "Recall@5"],
    "datasets": ["MuSiQue"],
    "baseline_results": {"HippoRAG-MuSiQue-F1": 0.61},
}

# 空话标准：对任何一篇论文都成立，**不含任何候选词**（含英文词也不许命中候选）。
_VAGUE_CRITERIA = "只要代码能跑起来、不报错就算复现成功"

# 富计划：同时触发 W1 / W2 / W3，用于"W6 与既有五条互不干扰"的对照。
_RICH_PLAN: Dict[str, Any] = {
    "plan_summary": "复现论文基线实验",
    "data_preparation": ["下载原始语料", "预处理为标准格式"],
    "execution_steps": [
        {"step_name": "安装依赖", "command": "pip install -r requirements.txt"},
        {"step_name": "创建输出目录", "command": "mkdir -p outputs"},
    ],
    "expected_results": {"description": "F1 达到 0.45 以上", "trend": "higher_is_better"},
    "success_criteria": _VAGUE_CRITERIA,
}
_RICH_RESOURCE_INFO: Dict[str, Any] = {
    "repos": [],
    "selected_repo": None,
    "external_resources": [],
}


# --------------------------------------------------------------------------- #
# 共用 case 表 —— 真实断言与突变断言**共用**，是本文件防蒸发的结构性要害。
#
# args 是**完整实参元组**：长度 2 表示"两参调用"（既有调用点的形态），长度 3 表示
# 显式传 paper_analysis。⇒ 两种调用形态都进得了同一张表。
# --------------------------------------------------------------------------- #

class _Case(NamedTuple):
    case_id: str
    args: Tuple[Any, ...]


# ── 期望**报** W6 的 case（drop 突变下必须整体翻面为"不报"）────────────────────
_W6_EXPECTED: Tuple[_Case, ...] = (
    # T-W6-04：候选集非空 + 空话标准
    _Case("vague-criteria/metrics", (_plan(_VAGUE_CRITERIA), {}, _PA_METRICS)),
    _Case("vague-criteria/datasets", (_plan(_VAGUE_CRITERIA), {}, _PA_DATASETS)),
    _Case("vague-criteria/full-sources", (_plan(_VAGUE_CRITERIA), {}, _PA_FULL)),
    # T-W6-05：空标准四形态（空标准是最该被用户看到的一种，不能因"没内容"就沉默）
    _Case("empty-criteria/empty-str", (_plan(""), {}, _PA_METRICS)),
    _Case("empty-criteria/whitespace", (_plan("   \n\t "), {}, _PA_METRICS)),
    _Case("empty-criteria/key-missing", (_plan(omit=True), {}, _PA_METRICS)),
    _Case("empty-criteria/non-str-int", (_plan(42), {}, _PA_METRICS)),
    _Case("empty-criteria/non-str-none", (_plan(None), {}, _PA_METRICS)),
    _Case("empty-criteria/non-str-list", (_plan(["knn_accuracy"]), {}, _PA_METRICS)),
    # T-W6-08：空白候选不得变成"命中一切"的万能候选
    _Case(
        "blank-candidate-not-wildcard",
        (_plan(_VAGUE_CRITERIA), {}, {"metrics": ["knn_accuracy"], "baseline_results": {"": 0.1}}),
    ),
    # T-W6-10：W6 与既有五条 W 并存（富计划同时命中 W1/W2/W3）
    _Case("coexists-with-w1w2w3", (_RICH_PLAN, _RICH_RESOURCE_INFO, _PA_METRICS)),
)

# ── 期望**不报** W6、且候选集为空的 case（ordering 突变下必须整体翻面为"报"）──────
# ⚠ 这些 fixture 一律**不含任何候选键**（或三键显式置空）⇒ 上游新增候选源不会污染。
_EMPTY_CANDIDATE_ABSENT: Tuple[_Case, ...] = (
    # T-W6-01：两参调用 —— 既有调用点的形态，必须字节级零扰动
    _Case("two-arg/vague-criteria", (_plan(_VAGUE_CRITERIA), {})),
    _Case("two-arg/empty-criteria", (_plan(""), {})),
    _Case("two-arg/key-missing", (_plan(omit=True), {})),
    _Case("two-arg/rich-plan", (_RICH_PLAN, _RICH_RESOURCE_INFO)),
    # T-W6-02：显式 None / 非 dict
    _Case("explicit-none", (_plan(""), {}, None)),
    _Case("non-dict/str", (_plan(""), {}, "knn_accuracy")),
    _Case("non-dict/list", (_plan(""), {}, ["knn_accuracy"])),
    # T-W6-03：候选集为空的各种形态（🔴 含优先级自证：空标准 + 空候选 ⇒ 仍不报）
    _Case("empty-candidates/empty-dict", (_plan(""), {}, {})),
    _Case(
        "empty-candidates/three-keys-empty",
        (_plan(""), {}, {"metrics": [], "datasets": [], "baseline_results": {}}),
    ),
    _Case(
        "empty-candidates/wrong-types",
        (_plan(""), {}, {"metrics": "acc", "datasets": 3, "baseline_results": []}),
    ),
    _Case(
        "empty-candidates/non-str-elements",
        (_plan(""), {}, {"metrics": [1, 2.0, None], "datasets": [{"a": 1}]}),
    ),
    _Case(
        "empty-candidates/blank-strings-only",
        (_plan(""), {}, {"metrics": ["", "   "], "baseline_results": {"": 1, "  ": 2}}),
    ),
)

# ── 期望**不报** W6、但候选集非空（标准命中了候选）的 case ─────────────────────
_HIT_CANDIDATE_ABSENT: Tuple[_Case, ...] = (
    # T-W6-06：正向 —— 标准里写了论文分析中的指标名
    _Case(
        "hit/metric-name",
        (_plan("knn_accuracy 要与论文报告的数值对上"), {}, _PA_METRICS),
    ),
    # T-W6-07：三处候选源各自生效 + 大小写不敏感（原值小写 → 标准里写大写，反之亦然）
    _Case("hit/metrics-uppercased", (_plan("KNN_ACCURACY 必须复现到论文水平"), {}, _PA_METRICS)),
    _Case("hit/datasets-lowercased", (_plan("在 musique 上跑出论文同档的结果"), {}, _PA_DATASETS)),
    # T-W6-11：已登记局限 —— 具体但宽松的标准照样过（不得包装成"防止画低"的保证）
    _Case(
        "documented-limitation/specific-but-loose",
        (_plan("knn_accuracy 大于 0 即算成功"), {}, _PA_METRICS),
    ),
)

_W6_ABSENT: Tuple[_Case, ...] = _EMPTY_CANDIDATE_ABSENT + _HIT_CANDIDATE_ABSENT


def _ids(cases: Tuple[_Case, ...]) -> List[str]:
    return [c.case_id for c in cases]


# --------------------------------------------------------------------------- #
# 真实断言（对 core.plan_checks.check_plan 本尊）
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("case", _W6_EXPECTED, ids=_ids(_W6_EXPECTED))
def test_w6_reported(case: _Case) -> None:
    """T-W6-04/05/08/10：候选集非空且标准没点到任何论文事实 → **必须报 W6**。"""
    rules = _rules(check_plan(*case.args))
    assert "W6" in rules, f"{case.case_id}：应报 W6 却没报，实际 {rules}"


@pytest.mark.parametrize("case", _W6_ABSENT, ids=_ids(_W6_ABSENT))
def test_w6_not_reported(case: _Case) -> None:
    """T-W6-01/02/03/06/07/11：候选集空、或标准已点到论文事实 → **不得报 W6**。"""
    rules = _rules(check_plan(*case.args))
    assert "W6" not in rules, f"{case.case_id}：W6 误报，实际 {rules}"


@pytest.mark.parametrize("case", _W6_EXPECTED + _W6_ABSENT, ids=_ids(_W6_EXPECTED + _W6_ABSENT))
def test_w6_return_shape_and_no_duplicate(case: _Case) -> None:
    """返回项恒为 {rule, message} 两键；W6 **至多出现一次**（不重复刷屏）。"""
    warnings = check_plan(*case.args)
    assert isinstance(warnings, list)
    for item in warnings:
        assert set(item) == {"rule", "message"}, f"返回项结构变了：{item}"
        assert isinstance(item["rule"], str) and isinstance(item["message"], str)
        assert item["message"], f"message 为空：{item}"
    assert _rules(warnings).count("W6") <= 1, f"{case.case_id}：W6 重复出现"


@pytest.mark.parametrize("case", _W6_EXPECTED + _W6_ABSENT, ids=_ids(_W6_EXPECTED + _W6_ABSENT))
def test_w6_does_not_perturb_existing_five_rules(case: _Case) -> None:
    """T-W6-10（G5 契约回归）：传不传 `paper_analysis`，**既有五条 W 的输出逐条相同**。

    这是 S8-11「零改动红线解锁」的核心契约：W6 只许**加一条**，不许改动 W1~W5 的
    触发条件、顺序或文案。拿同一份 plan / resource_info 跑两参与三参两次，
    去掉 W6 后两侧必须**完全相等**（含顺序与 message）。
    """
    plan, resource_info = case.args[0], case.args[1]
    two_arg = [w for w in check_plan(plan, resource_info) if w["rule"] != "W6"]
    n_arg = [w for w in check_plan(*case.args) if w["rule"] != "W6"]
    assert two_arg == n_arg, f"{case.case_id}：既有五条 W 被 W6 扰动了"


def test_w6_third_candidate_source_is_baseline_results_keys() -> None:
    """T-W6-07：第三候选源 = `baseline_results` 的**键**（论文自报结果的名字）。

    🔴 **单口径硬断言**（2026-08-09 塌自双口径分支）：架构 v2.7 `AR-S8-16` 裁定
    「`core/plan_checks.py` 与 W6 判据**一字不动**」⇒ 第三候选源就是在的，没有第二种口径。
    ⚠ 这里必须**白盒断言候选集本身**：只断言"不报 W6"是没牙的 —— 第三源一旦被删，
    候选集变空 ⇒ 早退 ⇒ 照样不报 W6 ⇒ 断言静默保绿（正是本文件塌口径要治的病）。
    """
    baseline_name = "HippoRAG-MuSiQue-F1"
    terms = plan_checks._paper_fact_terms(_PA_BASELINE)
    assert baseline_name in terms, (
        f"`baseline_results` 的键不再进候选集（实际候选集 {terms}）—— 这是 W6 三处候选源的"
        f"第三处，删掉它 = 让「引用论文自报基线」的达标线拿不到候选。架构 sp8 §15.3 第 1 条"
    )
    # 标准里全小写写出该基线名 ⇒ 命中（顺带验大小写不敏感）⇒ 不报 W6。
    plan = _plan("hipporag-musique-f1 要与论文自己报的数对得上")
    rules = _rules(check_plan(plan, {}, _PA_BASELINE))
    assert "W6" not in rules, f"标准已点到论文自报基线的名字，不该报 W6，实际 {rules}"


def test_paper_fact_terms_drops_blank_candidates() -> None:
    """T-W6-08（白盒）：空串 / 纯空白**不得进候选集**。

    命门：`"" in 任意文本` 恒为 True ⇒ 一旦空串进了候选集，它就是一个**命中一切**的
    万能候选，W6 从此永久静默（一条永远不报的规则，和删掉它没有区别）。
    """
    terms = plan_checks._paper_fact_terms(
        {
            "metrics": ["knn_accuracy", "", "   "],
            "datasets": ["  ", "MuSiQue"],
            "baseline_results": {"": 0.1, "   ": 0.2, "HippoRAG-MuSiQue-F1": 0.61},
        }
    )
    assert "" not in terms and "   " not in terms and "  " not in terms, f"空白候选混进来了：{terms}"
    assert all(t.strip() == t and t for t in terms), f"候选未去空白：{terms}"
    assert "knn_accuracy" in terms and "MuSiQue" in terms


# --------------------------------------------------------------------------- #
# T-W6-09：文案（MEMORY §4.2 —— 用户可见文本禁用内部术语）
#
# ⚠ 与 tests/test_s708_user_text_guard.py 不重复：那边扫的是**共享黑名单词表**，
#   本条钉的是 W6 自己的两件事——①零内部字段名；②压根没有英文（连单词都没有）。
# --------------------------------------------------------------------------- #

_FORBIDDEN_LITERALS: Tuple[str, ...] = (
    "success_criteria", "paper_analysis", "baseline_results", "metrics", "datasets",
    "plan_checks", "check_plan", "W6", "ReproductionPlan", "PaperAnalysis",
    "fact_terms", "reproduction_plan",
)


def test_w6_message_has_no_internal_jargon() -> None:
    """W6 文案经 st.warning 直达用户 ⇒ 必须通俗中文、零字段名、零英文缩写。"""
    for literal in _FORBIDDEN_LITERALS:
        assert literal not in _W6_MESSAGE, f"W6 文案泄漏内部词「{literal}」：{_W6_MESSAGE}"
    leftover = re.findall(r"[A-Za-z_]{3,}", _W6_MESSAGE)
    assert not leftover, f"W6 文案出现英文串（应为纯中文）：{leftover}"
    assert len(_W6_MESSAGE) >= 30, "W6 文案过短，说不清该怎么改"


def test_w6_message_delivered_verbatim() -> None:
    """用户看到的 message 与被守门的常量 `_W6_MESSAGE` 是**同一份**。

    否则守门守的是一个没人用的常量（S7-06 踩过的"守门扫描面指错"同型）。
    """
    warnings = check_plan(_plan(_VAGUE_CRITERIA), {}, _PA_METRICS)
    w6 = [w for w in warnings if w["rule"] == "W6"]
    assert len(w6) == 1
    assert w6[0]["message"] == _W6_MESSAGE


# --------------------------------------------------------------------------- #
# T-W6-12：生产接线 —— 审核页调用点必须真把第三个实参传下去
#
# 不测这条的话，W6 判定再对也可能在生产上永久静默（候选集恒空），
# 而单元测试全绿 —— 正是本文件要补的那种"零覆盖"再上演一层。
# --------------------------------------------------------------------------- #

def test_plan_review_forwards_paper_analysis_to_check_plan() -> None:
    """`_render_plan_check_warnings` 必须把 paper_analysis 透传给 check_plan。"""
    from unittest.mock import patch

    import ui.pages.plan_review as plan_review

    sentinel = {"metrics": ["knn_accuracy"]}
    with patch.object(plan_review, "check_plan", return_value=[]) as spy:
        plan_review._render_plan_check_warnings({"a": 1}, {"b": 2}, paper_analysis=sentinel)
    assert spy.call_count == 1
    args, kwargs = spy.call_args
    passed = kwargs.get("paper_analysis", args[2] if len(args) > 2 else None)
    assert passed is sentinel, f"paper_analysis 未透传给 check_plan：args={args} kwargs={kwargs}"


def test_plan_review_call_site_passes_paper_analysis() -> None:
    """静态：审核页里对 `_render_plan_check_warnings` 的**每一处调用**都带第三个实参。

    ⚠ 只断言"传了"，**不断言传的是什么内容** —— 上游摘要带哪些键由架构师裁定，
    本条对两种口径都成立。
    """
    import ui.pages.plan_review as plan_review

    source = Path(inspect.getsourcefile(plan_review)).read_text(encoding="utf-8")
    calls = [
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_render_plan_check_warnings"
    ]
    assert calls, "审核页里找不到 _render_plan_check_warnings 的调用点（接线被删了？）"
    for call in calls:
        has_third = len(call.args) >= 3 or any(kw.arg == "paper_analysis" for kw in call.keywords)
        assert has_third, (
            f"plan_review.py:{call.lineno} 调用 _render_plan_check_warnings 未传 paper_analysis "
            f"⇒ W6 在生产上永久静默"
        )


# --------------------------------------------------------------------------- #
# T-W6-13 ~ T-W6-16：链路级 —— 走**真实** `_digest_paper_analysis`，禁止手搭 payload
#
# 🔴 手搭 payload 会把接缝整个测掉：候选集由 `plan_checks` 取、摘要由 `planning` 产，
#   两边各自都对、合起来断掉 —— 这正是 `AR-S8-16` 那个缺陷藏了一整批的原因
#   （`CP-1b.4-3` 的 UI 用例直接塞了 `baseline_results`，于是证明的是"这个能力存在"，
#   而不是"这条链路通"）。架构 §15.5 G8 对此写死：**输入必须由摘要函数真实产出**。
# --------------------------------------------------------------------------- #

_PAPER_ANALYSIS_RAW: Dict[str, Any] = {
    "method_summary": "用图结构做多跳检索。",
    "metrics": ["knn_accuracy", "Recall@5"],
    "datasets": ["MuSiQue"],
    "framework": "pytorch",
    "baseline_results": {"BM25_R2": 0.43},
}

# G8/G9 的输入逐字照架构 §15.5：**指标与数据集皆空**，候选只能从论文自报基线来
# ⇒ 第三候选源是这条链路上**唯一**的候选来源，摘要一旦漏键，断言必然翻面。
_G8_PAPER_ANALYSIS_RAW: Dict[str, Any] = {
    "metrics": [],
    "datasets": [],
    "baseline_results": {"BM25_R2": 0.43},
}
_G8_CRITERIA = "复现出论文报告的 BM25_R2 0.43"

# 生产摘要的键集合（架构 v2.7 `AR-S8-16` 落地后为 5 键）。
_DIGEST_KEYS = {"method_summary", "datasets", "metrics", "framework", "baseline_results"}


def _production_digest(raw: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """走**真实**的 `_digest_paper_analysis` 拿审核页那份摘要（不手搭 payload）。"""
    from core.nodes.planning import _digest_paper_analysis

    return _digest_paper_analysis(_PAPER_ANALYSIS_RAW if raw is None else raw)


def test_w6_works_on_production_digest_shape() -> None:
    """T-W6-13：`_digest_paper_analysis` 压缩后的摘要仍能让 W6 一报一不报。"""
    digest = _production_digest()
    assert plan_checks._paper_fact_terms(digest), (
        f"生产摘要里一个候选都取不到 ⇒ W6 在生产上恒不触发，摘要={digest}"
    )
    assert "W6" in _rules(check_plan(_plan(_VAGUE_CRITERIA), {}, digest))
    assert "W6" not in _rules(
        check_plan(_plan("knn_accuracy 要达到论文报告的水平"), {}, digest)
    )


def test_g8_paper_reported_baseline_reaches_check_plan() -> None:
    """T-W6-14 = 架构 §15.5 **G8**（`AR-S8-16` 的唯一承载，链路级、单口径硬断言）。

    达标线引用论文**自报基线的名字**（`BM25_R2`）—— 这是最扎实的一档达标线 ——
    经真实 `_digest_paper_analysis` 走一遭后，**不得被误报** W6。

    🔴 **本条 2026-08-09 由双口径分支塌成单口径。** 塌之前它写成
    「摘要带 `baseline_results` ⇒ 断言不报；不带 ⇒ 断言照报」，看着两边都有硬断言，
    实际上**把 `planning.py` 那一行摘掉时它只是静默换边继续绿** —— 架构 §15.5 G8
    白纸黑字写的验红条件（「去掉该键 → 本条必红」）**当时是不成立的**。
    ⇒ 现在三条断言逐条钉死，摘键即红。**不要再把它改回"能适应两种口径"。**
    """
    digest = _production_digest(_G8_PAPER_ANALYSIS_RAW)
    assert "baseline_results" in digest, (
        f"`_digest_paper_analysis` 的返回里没有 `baseline_results` 键 —— `AR-S8-16` 那一行"
        f"被摘掉了。后果：引用论文自报基线的达标线在生产上必被误报 W6。摘要={digest}"
    )
    terms = plan_checks._paper_fact_terms(digest)
    assert "BM25_R2" in terms, (
        f"论文自报基线的名字没能穿过摘要抵达候选集（候选集={terms}，摘要={digest}）"
    )
    rules = _rules(check_plan(_plan(_G8_CRITERIA), {}, digest))
    assert "W6" not in rules, (
        f"达标线已点名论文自报基线（{_G8_CRITERIA}），仍被 W6 误报 ⇒ AR-S8-16 复发，实际 {rules}"
    )


def test_g9_vague_criteria_warns_when_only_baseline_present() -> None:
    """T-W6-15 = 架构 §15.5 **G9**（反向，同一份摘要）：空话达标线仍**必须**报 W6。

    守的是 `AR-S8-16` 列的危害 2：论文只报了基线、没给指标/数据集清单时，护栏
    **不许静音**。⇒ 与 G8 共用同一份 digest，一正一反把这条链路夹死。
    """
    digest = _production_digest(_G8_PAPER_ANALYSIS_RAW)
    rules = _rules(check_plan(_plan(_VAGUE_CRITERIA), {}, digest))
    assert "W6" in rules, (
        f"指标与数据集皆空、只有论文自报基线时，空话达标线未被警示 ⇒ 护栏在这类论文上"
        f"整个静音（AR-S8-16 危害 2）。摘要={digest}，实际 {rules}"
    )


def test_production_digest_key_set_is_exact() -> None:
    """T-W6-16：生产摘要的**内层键集合**精确等于 5 键（`AR-S8-16` 点名的守门缺口）。

    `AR-S8-16` 复盘时认定该缺陷之所以躲过全部既有绿灯，正是因为
    `tests/test_sprint7_s708_payload_probe.py` 的精确 11 键守门断的是**外层** payload，
    而 `paper_analysis_summary` 的**内层键无人守**。本条把那个缺口补上。

    ⚠ 精确 `==`（禁止改成 `>=` / `issubset`）：少键 = 缺陷复发，多键 = payload 悄悄变胖，
    两个方向都该有人看一眼。将来若架构裁定增删键，**改这里的同时按 `P-S8-24` 全文
    grep 一遍其它精确集合断言**（外层 11 键守门在 `test_sprint7_s708_payload_probe.py`，
    架构 §15.5 G10 要求它**零改动**：本键是内层子键，不影响外层）。
    """
    digest = _production_digest()
    assert set(digest) == _DIGEST_KEYS, (
        f"生产摘要键集合变了：多出 {set(digest) - _DIGEST_KEYS}，缺少 {_DIGEST_KEYS - set(digest)}"
    )
    # 恒常给键：论文没报基线时也要有这个键（给 `{}`，不是缺席）—— 架构 §15.6 路 α 的形态。
    empty = _production_digest({"method_summary": "x"})
    assert set(empty) == _DIGEST_KEYS and empty["baseline_results"] == {}, (
        f"论文没报基线时 `baseline_results` 键应恒常存在且为空字典，实际 {empty}"
    )


# --------------------------------------------------------------------------- #
# 🔴 常驻验红：把 W6 判定改残废，同一批 case 必须整体翻面
#
# 突变**全在内存里**：读源码 → AST 改写 → compile → exec 到独立命名空间。
# 磁盘上的 `core/plan_checks.py` 一个字节都不动（`test_mutation_harness_is_side_effect_free`
# 每轮回归都核一遍）。
# --------------------------------------------------------------------------- #

_PLAN_CHECKS_PATH = Path(inspect.getsourcefile(plan_checks))
_PLAN_CHECKS_SOURCE = _PLAN_CHECKS_PATH.read_text(encoding="utf-8")

_ALWAYS_REPORT_SNIPPET = 'warnings.append({"rule": "W6", "message": _W6_MESSAGE})'


def _planning_module() -> Any:
    """拿 `core.nodes.planning` **模块本身**。

    ⚠ 必须走 `importlib`：`core/nodes/__init__.py` 把 7 个节点函数按**与子模块同名**的
    方式重导出（`__all__` 里就是 `planning` 等 7 个名字）⇒ `from core.nodes import planning`
    拿到的是那个**节点函数**，不是模块，`vars()` 里当然没有 `_coerce_str`（本轮实测踩到）。
    """
    import importlib

    return importlib.import_module("core.nodes.planning")


def _build_digest_mutant() -> Tuple[Callable[..., Dict[str, Any]], int]:
    """把 `planning._digest_paper_analysis` 返回的 dict 里 `baseline_results` 那一项删掉。

    🔴 **这条突变器是本轮补的**（2026-08-09）。原突变器只改写 `core/plan_checks.py`，
    **从不触碰 `planning.py`** ⇒ 架构 §15.5 G8 写死的验红条件（「把 `_digest_paper_analysis`
    的 `baseline_results` 键去掉 → 本条必红」）**没有任何常驻用例在守**，只能靠人手工
    摘一行去试。⇒ 现在把它钉成常驻的：摘键这个动作在内存里每轮都做一遍。

    只把这一个函数的 AST 抠出来单独 compile（**不 exec 整个 planning 模块**，否则会重跑
    模块级副作用），globals 借真身模块的一份**拷贝** ⇒ `_coerce_str` 等依赖照常可用，
    而 exec 产生的新函数落在拷贝里，`core.nodes.planning` 一个字节都不受影响。
    """
    planning_module = _planning_module()
    path = Path(inspect.getsourcefile(planning_module))
    tree = ast.parse(path.read_text(encoding="utf-8"))
    fn = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_digest_paper_analysis"
    )
    removed = 0
    for node in ast.walk(fn):
        if not isinstance(node, ast.Dict):
            continue
        keys: List[Any] = []
        values: List[Any] = []
        for key, value in zip(node.keys, node.values):
            if isinstance(key, ast.Constant) and key.value == "baseline_results":
                removed += 1
                continue
            keys.append(key)
            values.append(value)
        node.keys, node.values = keys, values
    module = ast.Module(body=[fn], type_ignores=[])
    ast.fix_missing_locations(module)
    namespace: Dict[str, Any] = dict(vars(planning_module))
    namespace["__name__"] = "mutant_planning_digest"
    exec(compile(module, str(path), "exec"), namespace)  # noqa: S102 - 测试内自造残废件
    return namespace["_digest_paper_analysis"], removed


def _build_mutant(mode: str) -> Tuple[Callable[..., List[Dict[str, str]]], int]:
    """把 `check_plan` 的 W6 判定改成残废版本，返回 (突变后的函数, 被改写的语句数)。

    识别方式：`check_plan` 函数体里**源码段含 "W6" 的顶层语句**（跳过 docstring）。
    ⇒ 与"整块 W6 判定"一一对应；W6 若被重构到别处、或被拆散，`removed` 会变化，
    由 `test_mutation_actually_bites` 当场报红（防"突变器自己失效导致假绿"，
    与 tests/test_s708_user_text_guard.py 的"扫描器活性金丝雀"同款思路）。
    """
    tree = ast.parse(_PLAN_CHECKS_SOURCE)
    fn = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "check_plan"
    )
    new_body: List[ast.stmt] = []
    mutated = 0
    for index, stmt in enumerate(fn.body):
        is_docstring = (
            index == 0
            and isinstance(stmt, ast.Expr)
            and isinstance(getattr(stmt, "value", None), ast.Constant)
        )
        segment = ast.get_source_segment(_PLAN_CHECKS_SOURCE, stmt) or ""
        if is_docstring or "W6" not in segment:
            new_body.append(stmt)
            continue
        mutated += 1
        if mode == "drop":
            continue  # 整块删掉
        if mode == "always":
            new_body.extend(ast.parse(_ALWAYS_REPORT_SNIPPET).body)  # 改成无条件上报
        elif mode == "ordering":
            stmt.test = ast.Constant(True)  # "候选集为空则早退" 失效
            new_body.append(stmt)
        else:  # pragma: no cover - 参数写错时立刻炸，不静默
            raise ValueError(f"未知突变模式：{mode}")
    fn.body = new_body
    ast.fix_missing_locations(tree)
    namespace: Dict[str, Any] = {
        "__name__": f"mutant_plan_checks_{mode}",
        "__file__": str(_PLAN_CHECKS_PATH),
    }
    exec(compile(tree, str(_PLAN_CHECKS_PATH), "exec"), namespace)  # noqa: S102 - 测试内自造残废件
    return namespace["check_plan"], mutated


@pytest.mark.parametrize("mode", ["drop", "always", "ordering"])
def test_mutation_actually_bites(mode: str) -> None:
    """突变器活性金丝雀：确实改到了 W6 判定，且改后**行为真的变了**。

    没有这一条，`_build_mutant` 一旦因重构而找不到 W6（改写 0 条语句），下面三条
    验红会拿着一个**和原件一模一样的"突变体"**跑出绿灯 —— 验红本身变成假绿。
    """
    mutant, mutated = _build_mutant(mode)
    assert mutated >= 1, (
        f"突变器没找到 W6 判定块（改写 {mutated} 条）⇒ W6 可能被重构到别处，"
        f"本文件的验红已失效，必须回来修突变器"
    )
    # 探针取**整张 case 表**，不手挑单条输入：三种突变各自只在部分输入上与原件分道
    # （`ordering` 只在候选集为空时才分道），手挑一条必然会挑错。
    diverged = [
        case.case_id
        for case in _W6_EXPECTED + _W6_ABSENT
        if _rules(mutant(*case.args)) != _rules(check_plan(*case.args))
    ]
    assert diverged, f"突变体（{mode}）在整张 case 表上与原件行为完全一致 ⇒ 突变没生效"


@pytest.mark.parametrize("case", _W6_EXPECTED, ids=_ids(_W6_EXPECTED))
def test_red_state_drop_w6_block(case: _Case) -> None:
    """🔴 验红①：**整块 W6 判定删掉** ⇒ 每一条"应报 W6"的 case 都不再报。

    等价于 `test_w6_reported` 在残废件上必红 —— 证明那批正向断言**有牙**。
    """
    mutant, _ = _build_mutant("drop")
    rules = _rules(mutant(*case.args))
    assert "W6" not in rules, (
        f"{case.case_id}：删掉 W6 判定后居然还报 W6 ⇒ 这条 case 报的不是 W6 判定块，"
        f"正向断言没牙，实际 {rules}"
    )


@pytest.mark.parametrize("case", _W6_ABSENT, ids=_ids(_W6_ABSENT))
def test_red_state_always_report_w6(case: _Case) -> None:
    """🔴 验红②：**W6 改成无条件上报** ⇒ 每一条"不该报 W6"的 case 都开始报。

    等价于 `test_w6_not_reported` 在残废件上必红 —— 证明那批负向断言**有牙**
    （负向断言最容易变成"反正它本来就不报"的摆设）。
    """
    mutant, _ = _build_mutant("always")
    rules = _rules(mutant(*case.args))
    assert "W6" in rules, (
        f"{case.case_id}：W6 改成无条件上报后仍未出现 ⇒ 这条 case 根本没走到 W6 判定，"
        f"负向断言没牙，实际 {rules}"
    )


@pytest.mark.parametrize(
    "case", _EMPTY_CANDIDATE_ABSENT, ids=_ids(_EMPTY_CANDIDATE_ABSENT)
)
def test_red_state_early_return_ordering(case: _Case) -> None:
    """🔴 验红③：**"候选集为空则不报"的早退失效** ⇒ 候选集空的 case 全部开始报 W6。

    这条守的是一个**顺序**不变量（dev-plan `T-S8-1b-3` 交接笔记）：早退必须排在
    "空标准则报"之前，否则既有那批两参调用（它们的计划普遍没写达标线）会被
    **集体打上 W6**，「既有五条 W 行为一字不变」的 G5 契约当场破。
    交付时这条只在一次性脚本里验过一次，现在把它钉成常驻用例。
    """
    mutant, _ = _build_mutant("ordering")
    rules = _rules(mutant(*case.args))
    assert "W6" in rules, (
        f"{case.case_id}：早退失效后仍未报 W6 ⇒ 这条 case 证不了早退的必要性，实际 {rules}"
    )


def test_red_state_digest_without_baseline_results() -> None:
    """🔴 验红④（T-W6-MD，**本轮新增**）：摘要里拿掉 `baseline_results` ⇒ G8/G9 必然翻面。

    架构 §15.5 G8 写死的验红条件是「把 `_digest_paper_analysis` 的 `baseline_results`
    键去掉 → 本条必红」。**在此之前没有任何常驻用例在守这句话** —— 原突变器只改
    `core/plan_checks.py`，`planning.py` 那一侧全靠人手工摘一行去试，而 2026-08-09
    真去试的时候发现：双口径写法下**一条都不红**。本条把那个手工动作变成每轮回归都做。

    🔴 **本条实测推翻了架构 §15.5 G8 对自己验红条件的表述**（2026-08-09）：
    G8 那一行写「期望 = **不报 W6**；验红 = 去掉该键 → **本条必红**」。**这两句不能同时成立** ——
    去掉该键后候选集变**空**，`check_plan` 在读达标线之前就早退（"宁窄勿宽"，`G3`），
    于是**照样不报 W6**。实测两态并列：

        真身(5 键)   候选集=['BM25_R2']  G8 达标线 → []      G9 空话 → ['W6']
        摘键后(4 键) 候选集=[]           G8 达标线 → []  ←同  G9 空话 → []   ←翻面

    ⇒ **G8 的行为断言"不报 W6"在验红下恒绿**，只按字面实现它 = 又造一条没牙的守门
    （和本文件开头骂的那个病一模一样，只是换了一层）。真正扛住这条验红的是两样：
      · **行为面 → `test_g9_...`**（护栏从"报"变成"静音"，这才是可观测的翻面）；
      · **结构面 → `test_g8_...` 的白盒断言①②** + `test_production_digest_key_set_is_exact`。
    这就是 `test_g8_...` 为什么不能只写一句 `assert "W6" not in rules`。

    下面三段断言逐条对应它们的翻面：
      ① 摘要不再有该键     ⇒ `test_production_digest_key_set_is_exact` 与 G8 断言① 必红
      ② 候选集塌成空       ⇒ G8 断言② 必红
      ③ G9 的空话达标线不再被警示 ⇒ `test_g9_...` 必红（**唯一的行为面红灯**）
    """
    mutant, removed = _build_digest_mutant()
    assert removed == 1, (
        f"突变器在 `_digest_paper_analysis` 里找到 {removed} 个 `baseline_results` 键（应为 1）"
        f"⇒ 要么 AR-S8-16 那一行已被摘掉（缺陷复发，看 test_g8_* 的红），"
        f"要么它被重构到别处 ⇒ 本文件的常驻验红已失效，必须回来修突变器"
    )
    digest = mutant(_G8_PAPER_ANALYSIS_RAW)
    assert "baseline_results" not in digest, f"突变没生效，摘要仍带该键：{digest}"  # ①
    assert not plan_checks._paper_fact_terms(digest), (  # ②
        f"摘掉该键后候选集竟然非空（{plan_checks._paper_fact_terms(digest)}）⇒ G8/G9 的输入选得不对，"
        f"这条链路不是只靠第三候选源撑着，验红证不了 AR-S8-16"
    )
    assert "W6" not in _rules(check_plan(_plan(_VAGUE_CRITERIA), {}, digest)), (  # ③
        "摘掉该键后，只有论文自报基线的那类论文上，空话达标线**居然仍被警示** ⇒ "
        "候选集之外还有别的东西在驱动 W6，`test_g9_...` 的红态证不了 AR-S8-16"
    )


def test_mutation_harness_is_side_effect_free() -> None:
    """突变全程只在内存里做：两个被突变的生产文件与真身函数**一个字节都没被改**。

    沿 `docs/MEMORY.md` §6 那条纪律的同一条道理：验红不该在红的那一次污染工作区
    （`/tmp/evil.py` 那次就是"红态真写了文件、还原代码后测试仍红"）。
    """
    planning_path = Path(inspect.getsourcefile(_planning_module()))
    planning_before = planning_path.read_text(encoding="utf-8")
    for mode in ("drop", "always", "ordering"):
        _build_mutant(mode)
    _build_digest_mutant()
    assert _PLAN_CHECKS_PATH.read_text(encoding="utf-8") == _PLAN_CHECKS_SOURCE, (
        "突变过程改动了磁盘上的 core/plan_checks.py —— 这是绝对禁止的"
    )
    assert planning_path.read_text(encoding="utf-8") == planning_before, (
        "突变过程改动了磁盘上的 core/nodes/planning.py —— 这是绝对禁止的"
    )
    # 真身仍然正常工作（没被 exec 出来的同名对象污染）。
    assert "W6" in _rules(check_plan(_plan(_VAGUE_CRITERIA), {}, _PA_METRICS))
    assert "W6" not in _rules(check_plan(_plan(_VAGUE_CRITERIA), {}))
    assert "baseline_results" in _production_digest(), (
        "真身 `_digest_paper_analysis` 被突变污染了（返回里少了第 5 键）"
    )


def test_case_tables_are_non_empty_and_disjoint() -> None:
    """case 表自身的完整性：非空、ID 唯一、两张表不重叠。

    ⇒ 谁把表清空 / 写重名，验红会"跑 0 条 case 然后 passed"，这条当场拦住。
    """
    assert len(_W6_EXPECTED) >= 8, "期望报 W6 的 case 少于 8 条，覆盖被削弱了"
    assert len(_EMPTY_CANDIDATE_ABSENT) >= 8, "候选集为空的 case 少于 8 条"
    assert len(_HIT_CANDIDATE_ABSENT) >= 3, "标准命中候选的 case 少于 3 条"
    all_ids = _ids(_W6_EXPECTED) + _ids(_W6_ABSENT)
    assert len(all_ids) == len(set(all_ids)), f"case ID 重名：{all_ids}"
