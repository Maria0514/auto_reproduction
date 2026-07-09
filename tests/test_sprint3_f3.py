"""Sprint 3 阶段 F / 任务 F3：Prompt Cache 守门（CP-F3-1）的字节级一致断言。

F3 的 CP-F3-1 把 C1 的 CP-C1-6（mock 级两份简易 context 比较）**升级**为
「**两篇完全不同的论文**经过完整 _build_coding_context → _build_coding_system_prompt
链路生成 SystemMessage，去尾部 ``--- 当前任务上下文 ---`` 动态段后主体字节级一致」。

这是纯静态 / mock 验证，**不依赖任何凭证**（不打 LLM、不读 deepxiv），直接进默认回归。

范式照搬：
    - paper_analysis CP10（``tests/test_paper_analysis.py::case_prompt_cache_prefix_stable``）
      的「split 尾部独立段落后主体字节级 ==」手法；
    - coding CP-C1-6（``tests/test_sprint3_c1.py::test_cp_c1_6_system_prompt_body_no_dynamic``）
      的「主体常量内无论文级动态变量字面量 + 去尾部段落字节一致」手法。

—— CP-F3-2（Prompt Cache 命中率真跑）依赖 LLM 凭证 + deepxiv 配额，**不在本文件**，
   由 ``scripts/spike_coding_prompt_cache.py`` 准备好，待主控授权后补跑（不真跑）。
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Any, Dict

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# importlib 拿真实子模块（避免 core/nodes/__init__ 显式 export callable 遮蔽，坑 6）。
coding_module = importlib.import_module("core.nodes.coding")

_CODING_SYSTEM_PROMPT_BODY = coding_module._CODING_SYSTEM_PROMPT_BODY
_CODING_HONESTY_SECTION = coding_module._CODING_HONESTY_SECTION
_build_coding_system_prompt = coding_module._build_coding_system_prompt
_build_coding_context = coding_module._build_coding_context

# 尾部动态段分隔符（与 coding._build_coding_system_prompt 中的拼接串字节一致）。
_TAIL_SEP = "\n--- 当前任务上下文 ---\n"


# ===========================================================================
# 两篇「完全不同」的论文 mock GlobalState 构造
# ===========================================================================
# 关键：两篇论文的 **每一个论文级 / 任务级动态字段都不同**——arxiv_id / title /
# paper_meta / reproduction_plan（code_strategy / execution_steps / deliverables /
# environment）/ resource_info（selected_repo.local_path）/ paper_analysis（method /
# datasets / framework / hardware）。若主体常量被任何动态变量污染，去尾部段落后两份
# 主体必不相等，断言会立刻失败。


def _paper_a_state(workspace: str) -> Dict[str, Any]:
    """论文 A：HippoRAG 风格（RAG / 检索增强）。"""
    return {
        "user_input": "2405.14831",
        "workspace_dir": workspace,
        "code_output_dir": str(Path(workspace) / "2405.14831" / "code"),
        "paper_meta": {
            "arxiv_id": "2405.14831",
            "title": "HippoRAG: Neurobiologically Inspired Long-Term Memory for LLMs",
        },
        "paper_analysis": {
            "method_summary_en": "HippoRAG indexes a knowledge graph and uses "
            "personalized PageRank for single-step multi-hop retrieval.",
            "datasets": ["MuSiQue", "2WikiMultiHopQA", "HotpotQA"],
            "framework": "PyTorch",
            "hardware_requirements_en": "1x A100 80GB GPU",
        },
        "resource_info": {
            "selected_repo": {
                "local_path": str(Path(workspace) / "2405.14831" / "repo" / "HippoRAG"),
            }
        },
        "reproduction_plan": {
            "code_strategy": "复用官方仓库的 KG 构建与 PPR 检索模块，重跑 MuSiQue 评估。",
            "execution_steps": [
                "准备 MuSiQue 子集",
                "构建知识图谱索引",
                "运行 PPR 检索",
                "评估 recall@k",
            ],
            "deliverables": ["run_hipporag.py", "eval_recall.py"],
            "environment": {"python": "3.10", "cuda": "12.1"},
        },
        "fix_loop_count": 0,
        "execution_result": None,
    }


def _paper_b_state(workspace: str) -> Dict[str, Any]:
    """论文 B：完全不同领域（图像分类 / CNN），所有动态字段与 A 互不相同。"""
    return {
        "user_input": "1512.03385",
        "workspace_dir": workspace,
        "code_output_dir": str(Path(workspace) / "1512.03385" / "code"),
        "paper_meta": {
            "arxiv_id": "1512.03385",
            "title": "Deep Residual Learning for Image Recognition",
        },
        "paper_analysis": {
            "method_summary_en": "ResNet introduces identity shortcut connections "
            "enabling training of very deep convolutional networks on ImageNet.",
            "datasets": ["ImageNet", "CIFAR-10"],
            "framework": "TensorFlow",
            "hardware_requirements_en": "8x V100 GPUs",
        },
        "resource_info": {
            "selected_repo": {
                "local_path": str(Path(workspace) / "1512.03385" / "repo" / "resnet"),
            }
        },
        "reproduction_plan": {
            "code_strategy": "从零实现 ResNet-50，在 CIFAR-10 上训练并报告 top-1 准确率。",
            "execution_steps": [
                "下载 CIFAR-10",
                "搭建 ResNet-50",
                "训练 90 epoch",
                "评估 top-1 accuracy",
            ],
            "deliverables": ["train_resnet.py", "eval_top1.py", "model.py"],
            "environment": {"python": "3.11", "cuda": "11.8"},
        },
        # 论文 B 故意处于修复回合，注入 execution_result + fix_round，进一步拉开两篇
        # context 的差异（验证修复回合反馈也不污染主体）。
        "fix_loop_count": 2,
        "execution_result": {
            "errors": ["[error_category=import] ModuleNotFoundError: No module named 'foo'"],
            "logs": "Traceback...\nModuleNotFoundError: No module named 'foo'\n",
        },
    }


# ===========================================================================
# CP-F3-1：两篇不同论文 SystemMessage 去尾部段落后主体字节级一致
# ===========================================================================


def test_cp_f3_1_two_papers_system_prompt_body_byte_identical(tmp_path: Path) -> None:
    """CP-F3-1 主断言：两篇完全不同论文经完整 build_context→build_system_prompt
    链路生成的 system prompt，去掉尾部 ``--- 当前任务上下文 ---`` 动态段后主体字节级一致。

    这是 Prompt Cache 方案 A 前缀稳定性的 Sprint 级守门——主体常量内任何论文级动态
    变量泄漏都会让两份主体不等而 fail。
    """
    ws_a = str(tmp_path / "wsA")
    ws_b = str(tmp_path / "wsB")
    Path(ws_a).mkdir(parents=True, exist_ok=True)
    Path(ws_b).mkdir(parents=True, exist_ok=True)

    state_a = _paper_a_state(ws_a)
    state_b = _paper_b_state(ws_b)

    # 完整链路：curated context（HumanMessage 通道）→ system prompt（方案 A）。
    ctx_a = _build_coding_context(state_a)
    ctx_b = _build_coding_context(state_b)

    sp_a = _build_coding_system_prompt(ctx_a)
    sp_b = _build_coding_system_prompt(ctx_b)

    # 1) 两份 system prompt 都含且仅含一个尾部分隔符（结构对齐 paper_analysis CP10）。
    assert sp_a.count(_TAIL_SEP) == 1, "论文 A system prompt 尾部分隔符数量异常"
    assert sp_b.count(_TAIL_SEP) == 1, "论文 B system prompt 尾部分隔符数量异常"

    # 2) 截去尾部独立段落后，主体必须字节级完全相同。
    body_a = sp_a.split(_TAIL_SEP, 1)[0]
    body_b = sp_b.split(_TAIL_SEP, 1)[0]
    assert body_a == body_b, (
        "两篇不同论文的 coding system prompt 主体字节级不一致，破坏 Prompt Cache 前缀稳定"
    )

    # 3) 主体应等于 _CODING_SYSTEM_PROMPT_BODY + _CODING_HONESTY_SECTION 常量拼接
    #    （前缀就是冻结的静态主体；sp5 T-S5-1-3 在主体与尾部间插入静态诚实红线段，
    #    同属跨任务字节恒定的稳定前缀——T-S5-1-6 断言目标同步，语义不降）。
    assert body_a == _CODING_SYSTEM_PROMPT_BODY + _CODING_HONESTY_SECTION, (
        "主体应等于 _CODING_SYSTEM_PROMPT_BODY + _CODING_HONESTY_SECTION"
        "（不得被任何动态变量改写）"
    )


def test_cp_f3_1_body_constant_carries_no_paper_level_variable(tmp_path: Path) -> None:
    """CP-F3-1 旁证：主体常量内不含任意一篇论文的论文级动态变量字面量。

    比 CP-C1-6 更强——用本测试两篇论文的真实 arxiv_id / title / 数据集 / framework /
    仓库名逐条扫描主体常量，任何一项出现即 fail（防有人把某篇论文的特征硬编进主体）。
    """
    body = _CODING_SYSTEM_PROMPT_BODY
    forbidden = [
        # 论文 A 特征
        "2405.14831", "HippoRAG", "MuSiQue", "2WikiMultiHopQA",
        # 论文 B 特征
        "1512.03385", "Deep Residual Learning", "ResNet", "ImageNet", "CIFAR-10",
        # 通用动态字段名（不应作为字面量出现在主体）
        "arxiv_id=", "selected_repo_local_path=", "code_output_dir=",
    ]
    for token in forbidden:
        assert token not in body, f"主体常量不应含论文级 / 任务级动态变量字面量 {token!r}"


def test_cp_f3_1_dynamic_context_lives_in_humanmessage_not_system_prompt(
    tmp_path: Path,
) -> None:
    """CP-F3-1 旁证：论文级动态上下文（计划 / 仓库路径 / 修复反馈）全部进 curated
    context（HumanMessage 通道），system prompt 主体一字节都不带。

    断言 _build_coding_context 确实把两篇论文的差异化字段都装进了 context dict
    （否则「动态在 HumanMessage」的契约只是空话），且这些差异值都不出现在主体常量里。
    """
    ws_a = str(tmp_path / "wsA")
    ws_b = str(tmp_path / "wsB")
    Path(ws_a).mkdir(parents=True, exist_ok=True)
    Path(ws_b).mkdir(parents=True, exist_ok=True)

    ctx_a = _build_coding_context(_paper_a_state(ws_a))
    ctx_b = _build_coding_context(_paper_b_state(ws_b))

    # context 确实携带了论文级动态字段（差异化、非空）。
    assert ctx_a["arxiv_id"] == "2405.14831"
    assert ctx_b["arxiv_id"] == "1512.03385"
    assert ctx_a["code_strategy"] != ctx_b["code_strategy"]
    assert ctx_a["datasets"] != ctx_b["datasets"]
    assert ctx_a["framework"] != ctx_b["framework"]
    assert ctx_a["selected_repo_local_path"] != ctx_b["selected_repo_local_path"]
    # 论文 B 处于修复回合，应注入 fix_round + last_error_summary；A 首轮不注入。
    assert "fix_round" not in ctx_a
    assert ctx_b["fix_round"] == 2
    assert ctx_b["last_error_summary"]["error_category"] == "import"

    # 这些动态值一个都不能出现在 system prompt 主体常量里。
    body = _CODING_SYSTEM_PROMPT_BODY
    for ctx in (ctx_a, ctx_b):
        for key in ("arxiv_id", "code_strategy", "selected_repo_local_path"):
            val = ctx.get(key)
            if isinstance(val, str) and val:
                assert val not in body, (
                    f"动态字段 {key}={val!r} 泄漏进 system prompt 主体常量"
                )


def test_cp_f3_1_tail_segment_is_constant_across_papers(tmp_path: Path) -> None:
    """CP-F3-1 旁证：连尾部段落本身在两篇论文间也字节一致（coding 节点动态全走
    HumanMessage，尾部仅渲染常量 ``{"node": "coding"}``，故整份 system prompt 在
    两篇论文间应完全相同——这是 coding 节点比 paper_analysis 更强的稳定性）。
    """
    ws_a = str(tmp_path / "wsA")
    ws_b = str(tmp_path / "wsB")
    Path(ws_a).mkdir(parents=True, exist_ok=True)
    Path(ws_b).mkdir(parents=True, exist_ok=True)

    sp_a = _build_coding_system_prompt(_build_coding_context(_paper_a_state(ws_a)))
    sp_b = _build_coding_system_prompt(_build_coding_context(_paper_b_state(ws_b)))

    # coding 尾部段落是常量 {"node":"coding"}，故整份 system prompt 都应字节一致。
    assert sp_a == sp_b, (
        "coding 节点 system prompt（含尾部常量段）在两篇论文间应完全字节一致"
    )
    tail_a = sp_a.split(_TAIL_SEP, 1)[1]
    tail_b = sp_b.split(_TAIL_SEP, 1)[1]
    assert tail_a == tail_b == '{"node": "coding"}'
