"""S7-08 / T-S7-5-12 收口闸门（一）：**计划契约面** AC-S7-32 / 33 / 34 / 35 / 36。

对应 dev-plan §35 任务 T-S7-5-12 的 CP-5.12-1 / 2 / 3 / 5 / 9，架构 sp7 §18.5 修正
口径 + §18.7 验证方式 (a)(b)，PRD §10.7。

本文件承载的四件事
==================
1. **CP-5.12-1（§18.7(a)）**：三方键集合相等的**收口复核**——`tests/test_s708_plan_keys.py`
   已把"三方相等"钉成常驻断言（T-5-1 先写后改），本文件只补它刻意不做的那半：
   **数量收口到 13** 且新两键确实是那两个（前者防"三方一起漏改仍相等"的共同模式失效，
   后者防"改名了但仍相等"）。
2. **CP-5.12-2（§18.7(b)，R-S7-38 唯一防线）**：`local_env_facts` 是**任务级动态值**，
   写进 system prompt 那一刻规划侧 Prompt Cache 前缀"破成每次"——**功能全对、账单
   持续渗漏、零告警**。故断"带该键 / 不带该键两次运行的 SystemMessage 字节完全一致"
   + "该值的特征串一个都不出现在 SystemMessage 里"。
3. **CP-5.12-3（AC-S7-33 禁编造，三道命门之一）**：见下方该节 docstring 的测试形态说明。
4. **CP-5.12-5（AC-S7-36，口径已由架构 §18.5(1) 修正）** + **CP-5.12-9（AC-S7-35
   旧存档兼容）**。

⚠ 已知 bug 模式 #6（S7-06 撞过两次）：`core/nodes/__init__.py` 显式 export 的 callable
会遮蔽同名子模块属性，访问模块级私有属性一律走 `importlib.import_module`。
"""

from __future__ import annotations

import importlib
import json
import re
from typing import Any, Dict, List, Optional

import pytest

import core.react_base as react_base
from core.state import ReproductionPlan

planning_module = importlib.import_module("core.nodes.planning")
reporting_module = importlib.import_module("core.nodes.reporting")
coding_module = importlib.import_module("core.nodes.coding")
execution_module = importlib.import_module("core.nodes.execution")

_PLANNING_BODY: str = planning_module._PLANNING_SYSTEM_PROMPT_BODY
_format_planning_context = planning_module._format_planning_context
_planning_react = planning_module._planning_react


# --------------------------------------------------------------------------- #
# 公共夹具
# --------------------------------------------------------------------------- #
class _CapturingSubgraph:
    """脚本化 ReAct 子图：只捕获 initial（含 SystemMessage / HumanMessage），不跑 LLM。"""

    def __init__(self, captured: Dict[str, Any]) -> None:
        self._captured = captured

    def invoke(self, initial: Dict[str, Any]) -> Dict[str, Any]:
        self._captured["initial"] = initial
        return {"result": {}, "messages": [], "round": 1, "status": "done"}


def _patch_subgraph(monkeypatch: pytest.MonkeyPatch) -> Dict[str, Any]:
    """替换 ReAct 子图工厂与 LLM 工厂，使 `_planning_react` 可离线跑到"装配完消息"为止。"""
    captured: Dict[str, Any] = {}

    def _factory(**kw: Any) -> _CapturingSubgraph:
        captured.update(kw)
        return _CapturingSubgraph(captured)

    monkeypatch.setattr(react_base, "create_react_subgraph", _factory)
    monkeypatch.setattr(react_base, "create_llm", lambda cfg: object())
    return captured


#: 只含"显卡 / CUDA / 磁盘"三维度、**完全不含内存**的本机实测事实原文。
#: 这正是本次 bug 的真实输入形态（真跑实证：只探到 GPU / CUDA / 磁盘三项）。
_FACTS_NO_MEMORY = (
    "本机环境实测（资源探索阶段真机探测所得，非论文推断）：\n"
    "$ nvidia-smi -L\n"
    "GPU 0: NVIDIA A100-SXM4-80GB (UUID: GPU-aaaa)\n"
    "$ nvcc --version\n"
    "Cuda compilation tools, release 12.1, V12.1.105\n"
    "$ df -h .\n"
    "Filesystem      Size  Used Avail Use%\n"
    "/dev/sda1       278G   32G  241G  12%"
)

#: 对照组（CP-5.12-5）：同一维度集合、**结论相反**的另一台机器。
_FACTS_TINY_MACHINE = (
    "本机环境实测（资源探索阶段真机探测所得，非论文推断）：\n"
    "$ nvidia-smi -L\n"
    "该命令在本机不可用\n"
    "$ nvcc --version\n"
    "该命令在本机不可用\n"
    "$ df -h .\n"
    "Filesystem      Size  Used Avail Use%\n"
    "/dev/sda1        40G   36G  3.2G  92%"
)

_FACTS_BIG_MACHINE = (
    "本机环境实测（资源探索阶段真机探测所得，非论文推断）：\n"
    "$ nvidia-smi -L\n"
    "GPU 0: NVIDIA H100 80GB HBM3 (UUID: GPU-1111)\n"
    "GPU 1: NVIDIA H100 80GB HBM3 (UUID: GPU-2222)\n"
    "GPU 2: NVIDIA H100 80GB HBM3 (UUID: GPU-3333)\n"
    "GPU 3: NVIDIA H100 80GB HBM3 (UUID: GPU-4444)\n"
    "$ nvcc --version\n"
    "Cuda compilation tools, release 12.4, V12.4.99\n"
    "$ df -h .\n"
    "Filesystem      Size  Used Avail Use%\n"
    "/dev/nvme0n1    7.0T  1.1T  5.6T  17%"
)


def _planning_state(**overrides: Any) -> Dict[str, Any]:
    """planning 节点最小可跑 state（离线；paper_analysis **默认不含硬件要求字段**）。"""
    state: Dict[str, Any] = {
        "llm_config_set": {
            "default": {
                "base_url": "http://x", "model": "m", "api_key": "k",
                "temperature": 0.0, "max_tokens": 1024,
            },
            "overrides": {},
        },
        "paper_meta": {"arxiv_id": "2403.06402", "title": "A Heavy-Compute Paper"},
        # ⚠ 刻意**没有** hardware_requirements：双缺失的第二半。
        "paper_analysis": {
            "method_summary": "在大规模语料上做持续预训练后再微调",
            "datasets": ["C4"],
            "metrics": ["EM"],
            "framework": "PyTorch",
        },
        "resource_info": {
            "repos": [{"url": "https://github.com/a/repo", "quality_score": 0.8}],
            "selected_repo": {"url": "https://github.com/a/repo", "quality_score": 0.8},
            "external_resources": [],
            "resource_strategy": "use_repo",
        },
        "node_errors": [],
        "degraded_nodes": [],
        "retry_budget_remaining": 50,
        "_planning_user_feedback": None,
        "_planning_pending_repo_url": None,
    }
    state.update(overrides)
    return state


def _run_planning_react(monkeypatch: pytest.MonkeyPatch, **state_overrides: Any):
    """跑一次 planning ReAct 装配，返回 (system_text, human_text)。"""
    captured = _patch_subgraph(monkeypatch)
    _planning_react(_planning_state(**state_overrides))
    messages = captured["initial"]["messages"]
    return messages[0].content, messages[1].content


# =========================================================================== #
# CP-5.12-1（§18.7(a)）：三方键集合相等 —— 收口复核（14 == 14 == 14）
#
# ⚠ **sp8 T-S8-1b-2 换发（2026-08-08）**：键数由 13 → **14**（新增
# `ReproductionPlan.success_criteria`，S8-01 扩围 / 架构 sp8 §2.5.1 / `AR-S8-15`）。
# **只换数不弱化**：仍是 `==` 精确相等 + 新键名逐个钉死，本条守的两种退化
# （"新键被后续批次顺手删掉" / "被改名成别的"）**射程未减**，且射程由 S7-08 两键
# 扩到 S7-08 两键 + S8-01 一键。
# =========================================================================== #
_S708_NEW_PLAN_KEYS = {"scale_reduced", "local_fit_note"}
#: sp8 S8-01 扩围新增的那一键（本篇论文的达标线）。
_S801_NEW_PLAN_KEYS = {"success_criteria"}
_EXPECTED_PLAN_KEY_COUNT = 14


def test_cp_5_12_1_three_way_key_sets_closed_at_fourteen() -> None:
    """收口复核：声明 / 正常构造 / 降级构造三方**都恰 14 键**，且新增的恰是那三个。

    `tests/test_s708_plan_keys.py` 已断"三方相等"；相等断言有一个共同模式盲区——
    三方**一起**漏改（或一起改错名）时它仍然全绿。本条把数量与键名一并钉死，
    使"S7-08 / S8-01 新键被后续批次顺手删掉""被改名成别的"这两种退化必红。

    ⚠ `success_criteria` 是 `R-S8-42` 的直接作用面：它的 TypedDict 声明与
    `planning.py` 两处构造点**必须原子同批**，本条正是"只补了一处构造点"的红灯。
    """
    declared = set(ReproductionPlan.__annotations__)
    built = set(planning_module._build_reproduction_plan({}, {}).keys())
    minimal = set(planning_module._minimal_plan({}, "x").keys())

    assert declared == built == minimal, (
        "三方键集合必须逐字相等（详见 tests/test_s708_plan_keys.py）；"
        f"declared-built={sorted(declared - built)}, declared-minimal={sorted(declared - minimal)}"
    )
    for name, keys in (("声明", declared), ("正常构造", built), ("降级构造", minimal)):
        assert len(keys) == _EXPECTED_PLAN_KEY_COUNT, (
            f"{name}侧键数应为 {_EXPECTED_PLAN_KEY_COUNT}"
            "（sp5 的 11 键 + S7-08 两键 + S8-01 一键），"
            f"实得 {len(keys)}：{sorted(keys)}"
        )
        assert _S708_NEW_PLAN_KEYS <= keys, f"{name}侧缺 S7-08 新键：{sorted(_S708_NEW_PLAN_KEYS - keys)}"
        assert _S801_NEW_PLAN_KEYS <= keys, f"{name}侧缺 S8-01 新键：{sorted(_S801_NEW_PLAN_KEYS - keys)}"


def test_cp_5_12_1_new_keys_carry_safe_defaults_on_both_paths() -> None:
    """AC-S7-35 前半：两条构造路径在 LLM 一字未给时都回落**安全缺省**（False / ""）。

    "没做过本机适配"是安全默认（架构 §18.1 裁决 4）——缺省若取 True，
    报告会平白多出一条"本次是缩小规模复现"的假声明。
    """
    built = planning_module._build_reproduction_plan({}, {})
    minimal = planning_module._minimal_plan({}, "降级原因")

    for name, plan in (("_build_reproduction_plan", built), ("_minimal_plan", minimal)):
        assert plan["scale_reduced"] is False, f"{name} 的 scale_reduced 缺省必须是 False"
        assert plan["local_fit_note"] == "", f"{name} 的 local_fit_note 缺省必须是空串"
        assert isinstance(plan["scale_reduced"], bool), name
        assert isinstance(plan["local_fit_note"], str), name

    # 降级路径**恒**为假：最简版计划根本没读过本机实测事实，不得冒充"已按本机缩过"。
    assert planning_module._minimal_plan({"scale_reduced": True}, "x")["scale_reduced"] is False


# =========================================================================== #
# CP-5.12-2（§18.7(b)）：local_env_facts 绝不进 system prompt —— R-S7-38 唯一防线
# =========================================================================== #
def test_cp_5_12_2_system_message_byte_identical_with_and_without_local_env_facts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """收口复核（命门级）：带 / 不带 `local_env_facts` 两次装配，SystemMessage **字节完全一致**。

    R-S7-38 的失效形态极其隐蔽：把任务级动态值写进 system prompt 后，功能全对、
    零报错、零告警，只有账单在持续渗漏（每任务首调必 miss Prompt Cache）。
    **本断言是唯一防线**，故这里断的是整段字节相等，不是"某个子串不在"。
    """
    system_with, human_with = _run_planning_react(
        monkeypatch, local_env_facts=_FACTS_NO_MEMORY
    )
    system_without, human_without = _run_planning_react(monkeypatch)

    assert system_with == system_without, (
        "SystemMessage 字节必须与不带 local_env_facts 时完全一致——"
        "任务级动态值一旦进冻结前缀，规划侧 Prompt Cache 即破成每次（R-S7-38）"
    )
    # 反面对照：HumanMessage 必须**确实不同**，否则上面那条会退化成"两边都没送到"的假绿。
    assert human_with != human_without, "本机事实必须真的经 HumanMessage 通道送达"
    assert "local_env_facts" in json.loads(human_with)
    assert "local_env_facts" not in json.loads(human_without)


def test_cp_5_12_2_no_local_env_fact_value_leaks_into_frozen_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """负向：本机事实的**特征值**一个都不得出现在 SystemMessage / 冻结主体里。

    与上一条互补——上一条守"整体字节没变"，本条守"就算字节碰巧没变，值也没混进去"
    （例如有人把事实写进主体又同时删掉等长的别的字符）。同时复核主体零插值痕迹。
    """
    system_text, _ = _run_planning_react(monkeypatch, local_env_facts=_FACTS_BIG_MACHINE)

    for token in ("A100", "H100", "GPU-1111", "12.4", "nvme0n1", "5.6T", "241G"):
        assert token not in system_text, f"本机实测特征值泄漏进 SystemMessage：{token}"
        assert token not in _PLANNING_BODY, f"本机实测特征值泄漏进冻结主体：{token}"

    # 冻结主体零插值：主体里确有【输出格式】JSON 示例的大括号，故不能粗暴断"无 {"，
    # 改断更精确的"无 {placeholder} 形态插值位"（f-string / str.format 的痕迹）。
    assert not re.search(r"\{[a-z_]+\}", _PLANNING_BODY), (
        "冻结主体不得含 {placeholder} 形态的插值位"
    )
    # 主体里的 `arxiv_id` 是**工具形参名**（静态、与具体论文无关），合法；
    # 不合法的是具体论文号这类论文级值，故按 arXiv ID 形态断而非按 "arxiv" 字样断。
    assert not re.search(r"\b\d{4}\.\d{4,5}\b", _PLANNING_BODY), (
        "冻结主体不得含具体 arXiv 论文号这类论文级动态值"
    )


# =========================================================================== #
# AC-S7-32：三级优先级进契约（正向子串 + 负向：旧句消失）
# =========================================================================== #
def test_ac_s7_32_three_level_priority_replaced_the_unconditional_old_rule() -> None:
    """AC-S7-32：environment 一节改为三级优先级；那条无条件引用论文字段的旧句**已消失**。

    旧句（`planning.py:151-152`，S7-08 之前）：
        「2. environment（硬件 / 软件 / 预估时间）：引用论文分析的 hardware_requirements
          中文主字段，列出 GPU / 内存 / Python 与关键依赖版本。」
    它要求模型去引用一个**可能根本不存在**的字段 —— 无据可依 → 凭常识补 →
    编出"建议 32GB 内存"，正是本次 bug 的契约层根因（dev-plan §32.1）。
    """
    # 负向：旧句必须彻底消失（只断"引用论文分析的 hardware_requirements"这一确定性片段）
    assert "引用论文分析的 hardware_requirements" not in _PLANNING_BODY, (
        "那条无条件'引用论文分析的 hardware_requirements'旧指令必须被替换掉（AC-S7-32 负向）"
    )

    # 正向：三级优先级逐级都在，且高低关系写明
    assert "本机实测事实" in _PLANNING_BODY
    assert "第一级" in _PLANNING_BODY and "第二级" in _PLANNING_BODY and "第三级" in _PLANNING_BODY
    assert "高一级压过低一级" in _PLANNING_BODY, "三级之间的优先关系必须显式写明"
    # 第二级仍允许用论文推断，但必须限定为"本机实测未覆盖时"且需注明出处
    assert "只用于本机实测未覆盖" in _PLANNING_BODY
    assert "这是论文侧的说法" in _PLANNING_BODY


# =========================================================================== #
# CP-5.12-3：AC-S7-33 禁编造（**三道命门之一，须验红**）
# =========================================================================== #
#: 禁编造条款的三条硬约束——**验红对象**：把 `_PLANNING_SYSTEM_PROMPT_BODY` 里
#: 【禁止编造】那一段撤掉，下面每一条都会打红。
_NO_FABRICATION_CLAUSES = (
    "【禁止编造】",
    "不得给出任何具体数值",           # ①本机未覆盖的维度禁给数值
    "不得降级回条件句",               # ②已实测确定的事实禁退回"若无显卡则…"
    "拿论文数字或常识数字顶替",        # ③本机事实整体缺席时禁拿论文/常识数字顶
)


def test_cp_5_12_3_ac_s7_33_no_fabrication_contract_is_delivered_under_double_absence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**AC-S7-33 命门**：双缺失（本机只探到显卡/CUDA/磁盘 + 论文分析无硬件要求字段）下，
    禁编造契约必须**在 prompt 里且被真正送达**，且内存维度在整个任务级输入里**无任何来源**。

    ⚠⚠ 测试形态说明（dev-plan §35 T-5-12 第 2 条明文要求写进 docstring）
    ================================================================
    mock LLM 层只能验这一半：**规则在 prompt 里、且随本次调用真被送到模型面前**。
    "模型真的照做"那一半 **mock 层物理上不可证** —— 用 mock LLM 就得自己预设一份
    假计划，再去断言这份假计划里没有内存数字，那断的是 **mock 自己**（纯自证），
    与架构 §18.5(1) 判定 AC-S7-36 原口径不成立的理由完全同源。
    "模型真的照做"由 **AC-S7-43 真跑（T-S7-5-13）** 承担 —— 本项目实测 prompt
    服从率 **75%**，故那一环不可省、也不可用本条替代。

    因此本条的断言对象恰是两件**确定性可测**的事：
      (1) **契约文本**：禁编造三条硬约束逐条在 system prompt 里；
      (2) **契约缺失时的可观测差异**：撤掉该段 → (1) 立刻变红（验红已执行，见测试报告）；
          且双缺失 state 下内存维度在 system + human 两条通道里**都没有任何数据来源**，
          ⇒ 届时模型若写出内存数字，必然是编的（这是"编造"在本场景下的定义本身）。
    """
    system_text, human_text = _run_planning_react(
        monkeypatch, local_env_facts=_FACTS_NO_MEMORY
    )

    # ---- (1) 契约文本：禁编造三条硬约束逐条在 system prompt 里（验红对象）----
    for clause in _NO_FABRICATION_CLAUSES:
        assert clause in system_text, (
            f"禁编造条款缺失，AC-S7-33 失守：{clause!r}。"
            "（这正是本条的验红对象——撤掉该条款后必须看到本断言变红）"
        )
    # 兜底级"未探测 / 未知"必须是被点名的唯一合法写法
    assert '一律写"未探测 / 未知"' in system_text, "未覆盖维度的兜底写法必须被显式指定"
    assert "整个硬件部分" in system_text and "未探测 / 未知" in system_text, (
        "本机事实整体缺席时（探测失败）必须有一条兜底规则，否则模型会退回论文/常识数字"
    )

    # ---- (2) 双缺失确属真实：内存维度在任务级输入里零来源 ----
    human_payload = json.loads(human_text)
    assert "local_env_facts" in human_payload, "本机事实必须真的送达（否则本条退化为空跑）"
    facts = human_payload["local_env_facts"]
    assert "A100" in facts and "Cuda compilation tools" in facts and "Avail" in facts, (
        "双缺失场景的前提：显卡 / CUDA / 磁盘三维度确实探到了"
    )
    assert "hardware_requirements" not in human_payload, (
        "双缺失场景的第二半：论文分析侧确实没有硬件要求字段"
    )

    # 内存维度：整份 HumanMessage（= 唯一的任务级数据来源）里没有任何内存信息
    for memory_token in ("内存", "MemTotal", "Mem:", "free -h", "RAM", "GB 内存"):
        assert memory_token not in human_text, (
            f"双缺失前提被破坏：任务级输入里出现了内存来源 {memory_token!r}，"
            "本条据以成立的'无据可依'前提不再存在"
        )
    # 任何 “<数字>GB / <数字>G” 形态的容量数字都不该在任务级输入的内存位置出现；
    # 这里直接断整份 human 文本没有独立的内存容量表述（磁盘容量带 Avail 上下文，不冲突）。
    assert not re.search(r"\d+\s*(?:GB|G)\s*(?:内存|memory|RAM)", human_text, re.I), (
        "任务级输入里出现了内存容量数值，双缺失前提被破坏"
    )


def test_cp_5_12_3_no_fabrication_clause_is_not_diluted_elsewhere() -> None:
    """AC-S7-33 补强：禁编造是**硬约束**措辞，不得被弱化为"尽量 / 建议"这类软措辞。

    验红目标同上一条——把【禁止编造】段撤掉本条同样变红；另外它单独挡住
    "条款还在、但被改写成软措辞"这一种更隐蔽的退化。
    """
    idx = _PLANNING_BODY.find("【禁止编造】")
    assert idx > 0, "【禁止编造】段落必须存在"
    # 取该段到下一个编号章节（"3. data_preparation"）为止
    end = _PLANNING_BODY.find("\n3. data_preparation", idx)
    assert end > idx, "禁编造段必须落在 environment 一节内（第 2 节与第 3 节之间）"
    clause_block = _PLANNING_BODY[idx:end]

    assert "硬约束" in clause_block and "违反即为错误输出" in clause_block, (
        "禁编造必须以硬约束措辞落地，不得写成'尽量/建议'"
    )
    for soft in ("尽量不要", "建议不要", "最好不要", "可以不写"):
        assert soft not in clause_block, f"禁编造条款被弱化为软措辞：{soft}"


# =========================================================================== #
# CP-5.12-5：AC-S7-36 对照断言（口径已由架构 §18.5(1) 修正）
# =========================================================================== #
def test_cp_5_12_5_ac_s7_36_two_machines_produce_different_human_messages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**AC-S7-36（修正口径）**：同一篇重算力论文 + 两组本机事实 → **不同的 HumanMessage**。

    ⚠ 口径说明（架构 §18.5(1)，dev-plan §35 T-5-12 第 5 条）
    ======================================================
    原口径"断言两版计划的执行步骤规模参数出现差异"在 mock 层**不可证伪**——
    用 mock LLM 就得预设两份不同的假输出，断的是 mock 自己。故本条只断**输入侧
    差异**（确定性可测）：两台机器的实测事实产生两份不同的 HumanMessage，
    "计划规模真的跟着变了"整体交 AC-S7-43 真跑。

    **必须是对照断言，不能单跑一组**（PRD §10.7 测试盲区警示）——单跑一组
    无法区分"按本机缩了"与"本来就这么写"。
    """
    system_tiny, human_tiny = _run_planning_react(
        monkeypatch, local_env_facts=_FACTS_TINY_MACHINE
    )
    system_big, human_big = _run_planning_react(
        monkeypatch, local_env_facts=_FACTS_BIG_MACHINE
    )

    assert human_tiny != human_big, (
        "两组本机事实必须产生不同的 HumanMessage——相同即说明本机事实根本没参与"
        "规划输入的构造（AC-S7-36 对照断言）"
    )

    tiny_payload = json.loads(human_tiny)
    big_payload = json.loads(human_big)
    # 差异必须落在 local_env_facts 这一键上，而不是别处的偶然抖动
    assert tiny_payload["local_env_facts"] != big_payload["local_env_facts"]
    assert {k: v for k, v in tiny_payload.items() if k != "local_env_facts"} == {
        k: v for k, v in big_payload.items() if k != "local_env_facts"
    }, "除本机事实外的上下文必须逐键一致，否则'不同'可能来自别的抖动源"

    # 两台机器的关键结论确实相反（无显卡小盘 vs 多卡大盘），对照才有意义
    assert "该命令在本机不可用" in tiny_payload["local_env_facts"]
    assert big_payload["local_env_facts"].count("GPU ") >= 4

    # 冻结前缀不受对照影响（同 CP-5.12-2）
    assert system_tiny == system_big, "两次对照的 SystemMessage 必须字节一致"


def test_cp_5_12_5_context_builder_keeps_facts_out_when_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """对照的第三极：探测彻底失败（空串）→ 该键**不写**，不造 unknown / N/A 哨兵值。

    与上一条合起来构成三态对照（大机 / 小机 / 没探到），使"本机事实真的是变量"
    这件事无处可藏。
    """
    _, human_empty = _run_planning_react(monkeypatch, local_env_facts="")
    payload = json.loads(human_empty)
    assert "local_env_facts" not in payload
    for sentinel in ("unknown", "N/A", "未知"):
        assert sentinel not in human_empty, f"探测缺席不得造哨兵值：{sentinel}"


# =========================================================================== #
# CP-5.12-9：AC-S7-35 旧存档兼容 —— 11 键旧 plan 走下游全链路零 KeyError
# =========================================================================== #
#: sp5 时代（S7-08 之前）的 `ReproductionPlan` 形态：恰 11 键，**没有** S7-08 两键。
#: 这就是旧 checkpoint 里躺着的那份 plan——所有下游必须 `.get()` 防御读。
_LEGACY_11_KEY_PLAN: Dict[str, Any] = {
    "plan_summary": "复现 HippoRAG 主实验",
    "environment": {"python": "3.11", "cuda": "12.1"},
    "data_preparation": ["下载 MuSiQue"],
    "code_strategy": "use_repo",
    "execution_steps": [
        {"step_name": "建图", "command": "python build.py", "expected_output": "graph.pkl"},
    ],
    "expected_results": [{"description": "loss 应收敛", "trend": None}],
    "estimated_time": "约 2 小时",
    "deliverables": ["README.md"],
    "user_feedback": None,
    "approved": True,
    "required_credentials": [],
}


def test_cp_5_12_9_legacy_plan_really_has_eleven_keys_and_neither_new_key() -> None:
    """前提自证：夹具确实是"旧 11 键"形态，否则本组用例会退化成用新形态测兼容性。

    ⚠ **sp8 T-S8-1b-2 换发（2026-08-08）**：末条"恰是当前声明减掉新键"的自证里，
    被减掉的集合由 `S7-08 两键` 扩为 `S7-08 两键 ∪ S8-01 一键`。**只换不弱化**：
    仍是 `==` 精确相等，"sp5 键被后续批次改名后本夹具悄悄失真"这条射程未减；
    夹具本体（`_LEGACY_11_KEY_PLAN`，11 键）**一字未动**，它代表的旧 checkpoint
    形态本来就不含任何新键，正是防御读要兼容的那一份。
    """
    assert len(_LEGACY_11_KEY_PLAN) == 11, sorted(_LEGACY_11_KEY_PLAN)
    assert not (_S708_NEW_PLAN_KEYS & set(_LEGACY_11_KEY_PLAN)), "旧形态不得含 S7-08 两键"
    assert not (_S801_NEW_PLAN_KEYS & set(_LEGACY_11_KEY_PLAN)), "旧形态不得含 S8-01 新键"
    # 且它恰是当前声明键集合减掉全部新键（防 sp5 键被后续批次改名后本夹具悄悄失真）
    assert set(_LEGACY_11_KEY_PLAN) == (
        set(ReproductionPlan.__annotations__) - _S708_NEW_PLAN_KEYS - _S801_NEW_PLAN_KEYS
    )


def _legacy_state() -> Dict[str, Any]:
    return {
        "execution_mode": "full",
        "paper_meta": {"arxiv_id": "2405.14831", "title": "HippoRAG"},
        "reproduction_plan": dict(_LEGACY_11_KEY_PLAN),
        "resource_info": {"selected_repo": {"local_path": "/tmp/s708/repo"}},
        "paper_analysis": {"method_summary_en": "A method.", "framework": "pytorch"},
        "execution_result": {
            "success": True, "metrics": {}, "logs": "", "errors": [], "artifacts": [],
            "runtime_seconds": 1.0, "environment_info": {}, "degraded_credentials": [],
            "budget_truncated": False,
        },
        "simulation_notice": None,
        "code_output_dir": "/tmp/s708/code",
        "credential_degradations": {},
        "fix_loop_count": 0,
    }


@pytest.mark.parametrize("form", ["full_success", "code_only", "degraded"])
def test_cp_5_12_9_legacy_plan_through_reporting_no_keyerror(form: str) -> None:
    """链路 1/4 报告：三形态渲染 + 结论判定，旧 11 键 plan 一律零 KeyError、零缩规模声明。"""
    state = _legacy_state()
    conclusion = reporting_module._determine_conclusion(state, state["execution_result"], None)
    assert "scale_reduced" not in conclusion["annotations"], "缺键 ≡ False，不得平白多出标注"

    markdown = reporting_module._render_report(state, form, conclusion, None)
    assert isinstance(markdown, str) and markdown
    assert "缩小规模复现" not in markdown, "缺键时报告必须零扰动（AC-S7-38 负向）"


def test_cp_5_12_9_legacy_plan_through_coding_and_execution_no_keyerror() -> None:
    """链路 2、3/4 编码 + 执行：旧 11 键 plan 走两侧上下文构造，零 KeyError、零指令注入。"""
    state = _legacy_state()

    coding_payload = coding_module._build_coding_context(state)
    assert "scale_reduced_directive" not in coding_payload, "缺键 ≡ False，不得注入缩规模指令"

    execution_payload = execution_module._build_execution_agent_context(
        state, "/tmp/s708/work", state["reproduction_plan"]
    )
    assert "scale_reduced_directive" not in execution_payload


def test_cp_5_12_9_legacy_plan_through_plan_review_ui_no_keyerror() -> None:
    """链路 4/4 审核页：旧 payload（既无 `local_env_facts` 键、plan 也无新两键）零 KeyError。

    覆盖 UI 侧全部四个新读取点 + 讨论助手上下文；缺键一律走静态兜底常量，
    绝不渲染空白块、绝不抛。
    """
    mod = importlib.import_module("ui.pages.plan_review")
    legacy_payload = {
        "interrupt_kind": "plan_review",
        "reproduction_plan": dict(_LEGACY_11_KEY_PLAN),
        "resource_info": {"repos": []},
        "paper_analysis_summary": {},
        "degraded_nodes": [], "node_errors": [], "revise_count": 0,
        "soft_hint_threshold": 5, "max_total_llm_calls": 240, "switch_repo_failed": False,
    }

    assert mod._plan_of(legacy_payload) == _LEGACY_11_KEY_PLAN
    assert mod._local_env_facts_text(legacy_payload) == mod._LOCAL_ENV_FACTS_FALLBACK
    assert mod._local_fit_note_text(legacy_payload) == mod._LOCAL_FIT_NOTE_FALLBACK
    assert mod._is_scale_reduced(legacy_payload) is False

    context = json.loads(mod._format_plan_context(legacy_payload))
    assert context["local_env_facts"] == "", "缺键走空串，不造哨兵值"
    assert mod._build_chat_system_prompt(legacy_payload)  # 不抛即可


def test_cp_5_12_9_legacy_plan_survives_full_page_render() -> None:
    """链路 4/4 端到端：旧 payload 跑完整 `render()`，页面不抛异常且披露块恒常展示。

    纯函数直测挡不住"函数改对了但页面某处仍直接下标取键"这一档，故补一次整页渲染。
    """
    from unittest.mock import MagicMock, patch

    from streamlit.testing.v1 import AppTest

    controller = MagicMock()
    controller.get_interrupt_payload.return_value = {
        "interrupt_kind": "plan_review",
        "reproduction_plan": dict(_LEGACY_11_KEY_PLAN),
        "resource_info": {"repos": [], "selected_repo": None},
        "paper_analysis_summary": {},
        "degraded_nodes": [], "node_errors": [], "revise_count": 0,
        "soft_hint_threshold": 5, "max_total_llm_calls": 240, "switch_repo_failed": False,
    }
    controller.poll_state.return_value = {
        "llm_config_set": {
            "default": {"base_url": "https://example.test/v1", "model": "gpt-test",
                        "api_key": "", "temperature": 0.3, "max_tokens": 4096},
            "overrides": {},
        }
    }

    script = (
        "import streamlit as st\n"
        "st.session_state.setdefault('thread_id', 'task-legacy-001')\n"
        "st.session_state.setdefault('current_page', 'review')\n"
        "from ui.pages.plan_review import render\n"
        "render()\n"
    )
    with patch("app._get_controller", return_value=controller):
        at = AppTest.from_string(script)
        at.run()

    assert not at.exception, f"旧存档 payload 让审核页崩了：{at.exception}"

    mod = importlib.import_module("ui.pages.plan_review")
    texts: List[str] = []
    for collection in (at.markdown, at.caption, at.info, at.warning, at.error,
                       getattr(at, "code", [])):
        for el in collection:
            texts.append(str(getattr(el, "value", "")))
    page_text = "\n".join(texts)

    assert mod._LOCAL_ENV_BLOCK_TITLE in page_text, "披露块必须恒常展示（含旧存档）"
    assert mod._LOCAL_ENV_FACTS_FALLBACK in page_text, "旧存档取不到实测事实时须展示兜底句"
    assert mod._LOCAL_FIT_NOTE_FALLBACK in page_text
