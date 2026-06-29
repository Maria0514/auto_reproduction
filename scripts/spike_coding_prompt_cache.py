"""Spike F3：coding 节点 Prompt Cache 命中率回归（CP-F3-2，待主控授权补跑）。

⚠️ **本脚本依赖 LLM 凭证 + 可能消耗 deepxiv 配额，绝不在常规回归中自动跑。**
由主控在 Maria 明确授权后手动执行（沿用 sp2/sp3 e2e「凭证就绪后补跑」省配额范式）。
无凭证时 ``main()`` 直接返回 2 并打印 skip 原因，不发起任何真实调用。

用途（守门）：
- 验证 sp3 C1 在 coding 节点新注入的 system prompt 段（_CODING_SYSTEM_PROMPT_BODY +
  尾部常量段，方案 A 前缀治理）没有破坏 Prompt Cache 命中率；
- 守门判据：``R_after >= R_baseline_sp2 * 0.95``，其中 sp2 S-3 基线
  ``R_baseline_sp2 = 0.7669``（见 scripts/spike_prompt_cache_baseline.py + sp2 S-3 报告）。

设计（最省 deepxiv 配额）：
- 固定 arxiv_id=2405.14831 (HippoRAG)；
- **不跑上游链路**（intake/analysis/scout/planning 会真打 deepxiv + 多次 LLM）。改用一份
  预置的 **mock plan/resource/analysis state**（论文级事实简化但结构合法），直接构造可进入
  coding 的 GlobalState；
- **连跑 coding × 3**（首轮 cold + 2 轮 warm），记录每次所有 LLM 调用的 cached / prompt tokens；
- coding 内部若 agent 自主调 read_section 才会消耗 deepxiv 配额（HippoRAG 大概率已缓存，
  read 已缓存章节不额外计配额）；脚本不强制 agent 调，配额消耗下界为 0；
- R_i = sum(cached_tokens) / sum(prompt_tokens)；R_after = mean(R_2, R_3)（与 sp2 基线同口径）。

关键约束（照搬 sp2 S-3 spike）：
- 3 次连跑尽量在 cache TTL（通常 5 分钟）内完成；
- ReAct 子图通过 llm.invoke() 直调，绕开 _call_llm_with_retry，故 monkey-patch
  ChatOpenAI.invoke 在每次响应后采集 (cached, prompt) 数对，不改任何生产代码；
- 每轮用独立 code_output_dir（tmp 子目录）避免修复回合误判 / 文件互相覆盖。

输出：
- 终端打印 3 次的 cached / prompt / 命中率 + R_after + 是否过守门；
- 原始指标 JSON 落盘 workspace/runs/spike-f3-coding-prompt-cache_<ts>.json。

依赖环境变量：LLM_API_KEY / DEEPXIV_TOKEN / [可选] LLM_BASE_URL / LLM_MODEL。

⚠️⚠️ 真跑硬闸门（双保险，防误执行）：
- 本脚本会 ``load_dotenv`` 自动加载仓库 ``.env`` 的凭证，故**清除环境变量也拦不住真跑**；
- 因此额外要求显式设置 ``SPIKE_F3_AUTHORIZED=1`` 才会真正发起 LLM / deepxiv 调用，
  否则 ``main()`` 立即返回 2（skip），不消耗任何配额；
- 主控获 Maria 授权补跑时的命令：
    ``SPIKE_F3_AUTHORIZED=1 .venv/bin/python scripts/spike_coding_prompt_cache.py``
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# 自动加载 .env（与 tests/conftest.py / run_paper.py / spike_prompt_cache_baseline.py 同模式）。
try:
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env", override=False)
    load_dotenv(Path.home() / ".env", override=False)
except ImportError:
    pass

from config import (  # noqa: E402
    DEFAULT_LLM_MAX_TOKENS,
    DEFAULT_LLM_TEMPERATURE,
    WORKSPACE_DIR,
    get_deepxiv_token,
    get_llm_api_key,
    get_llm_base_url,
    get_llm_model,
)
from core.llm_client import _log_cache_metrics  # noqa: E402
from core.nodes.coding import coding  # noqa: E402
from core.state import LLMConfig, create_initial_state  # noqa: E402

ARXIV_ID = "2405.14831"  # HippoRAG（与 sp1/sp2 S-3 同源，便于横向对比）

# sp2 S-3 Prompt Cache fresh 基线（paper_analysis HumanMessage 输出语言策略改造的守门基线）。
# 见 scripts/spike_prompt_cache_baseline.py 注释 + sp2 S-3 报告 R_baseline=0.7669。
R_BASELINE_SP2 = 0.7669
GATE_FACTOR = 0.95  # 守门：R_after >= R_baseline_sp2 * 0.95


# ========== Monkey-patch：把每次 LLM 响应的 (cached, prompt) 数对累计到 _METRICS_BUCKET ==========
# 抽取规则与 scripts/spike_prompt_cache_baseline.py::_extract_cache_metrics 完全一致
# （OpenAI / Anthropic / LangChain usage_metadata 三种 schema 都覆盖）。

_METRICS_BUCKET: List[Dict[str, int]] = []


def _extract_cache_metrics(response: Any) -> Dict[str, int]:
    cached: Optional[int] = None
    prompt: Optional[int] = None
    cache_creation: Optional[int] = None
    try:
        usage_meta = getattr(response, "usage_metadata", None)
        if isinstance(usage_meta, dict):
            prompt = usage_meta.get("input_tokens") or prompt
            details = usage_meta.get("input_token_details") or {}
            if isinstance(details, dict):
                cached = (
                    details.get("cache_read")
                    or details.get("cache_read_input_tokens")
                    or cached
                )
                cache_creation = (
                    details.get("cache_creation")
                    or details.get("cache_creation_input_tokens")
                    or cache_creation
                )
        resp_meta = getattr(response, "response_metadata", None)
        if isinstance(resp_meta, dict):
            usage = resp_meta.get("usage") or resp_meta.get("token_usage") or {}
            if isinstance(usage, dict):
                if prompt is None:
                    prompt = usage.get("prompt_tokens") or usage.get("input_tokens")
                pt_details = usage.get("prompt_tokens_details") or {}
                if isinstance(pt_details, dict) and cached is None:
                    cached = pt_details.get("cached_tokens")
                if cached is None:
                    cached = usage.get("cache_read_input_tokens")
                if cache_creation is None:
                    cache_creation = usage.get("cache_creation_input_tokens")
    except Exception:  # noqa: BLE001
        pass
    return {
        "cached_tokens": int(cached) if cached else 0,
        "prompt_tokens": int(prompt) if prompt else 0,
        "cache_creation_tokens": int(cache_creation) if cache_creation else 0,
    }


def _patch_chat_openai_invoke() -> None:
    from langchain_openai import ChatOpenAI

    if getattr(ChatOpenAI, "_spike_f3_patched", False):
        return
    original_invoke = ChatOpenAI.invoke

    def patched_invoke(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        response = original_invoke(self, *args, **kwargs)
        try:
            _METRICS_BUCKET.append(_extract_cache_metrics(response))
            _log_cache_metrics(response)
        except Exception:  # noqa: BLE001
            pass
        return response

    ChatOpenAI.invoke = patched_invoke  # type: ignore[assignment]
    ChatOpenAI._spike_f3_patched = True  # type: ignore[attr-defined]


# ========== 预置 mock 上游 state（不跑 intake/analysis/scout/planning，省 deepxiv）==========


def _build_coding_ready_state(llm_config: LLMConfig, code_dir: str) -> dict:
    """构造一个已具备 coding 所需上游字段的 GlobalState（结构合法，论文级事实简化）。

    注入 coding 节点 _build_coding_context 真正读取的字段：
        reproduction_plan(code_strategy/execution_steps/deliverables/environment) +
        resource_info.selected_repo.local_path + paper_analysis(method/datasets/framework/
        hardware) + paper_meta.arxiv_id + code_output_dir。
    """
    state = create_initial_state(user_input=ARXIV_ID, llm_config=llm_config)
    state["paper_meta"] = {
        "arxiv_id": ARXIV_ID,
        "title": "HippoRAG: Neurobiologically Inspired Long-Term Memory for LLMs",
    }
    state["paper_analysis"] = {
        "method_summary_en": (
            "HippoRAG indexes a knowledge graph from a corpus and uses personalized "
            "PageRank for single-step multi-hop retrieval to augment LLM QA."
        ),
        "datasets": ["MuSiQue", "2WikiMultiHopQA", "HotpotQA"],
        "framework": "PyTorch",
        "hardware_requirements_en": "1x A100 80GB GPU",
    }
    state["resource_info"] = {"selected_repo": {"local_path": None}}
    state["reproduction_plan"] = {
        "code_strategy": (
            "实现一个 HippoRAG 风格的最小可运行检索增强问答 pipeline：构建小型知识图谱 → "
            "personalized PageRank 检索 → 在一个 toy QA 子集上评估 recall。"
        ),
        "execution_steps": [
            "准备一个 toy 多跳 QA 子集（10 条）",
            "从语料抽取三元组构建知识图谱",
            "实现 personalized PageRank 检索",
            "评估 recall@5 并打印 <METRICS>",
        ],
        "deliverables": ["run_repro.py", "graph_build.py", "ppr_retrieve.py"],
        "environment": {"python": "3.10"},
    }
    state["code_output_dir"] = code_dir
    state["current_step"] = "planning"
    return state


def _summarize_run(idx: int, metrics: List[Dict[str, int]], elapsed: float) -> Dict[str, Any]:
    total_cached = sum(m["cached_tokens"] for m in metrics)
    total_prompt = sum(m["prompt_tokens"] for m in metrics)
    total_cache_creation = sum(m["cache_creation_tokens"] for m in metrics)
    ratio = (total_cached / total_prompt) if total_prompt > 0 else 0.0
    return {
        "run_index": idx,
        "llm_calls": len(metrics),
        "total_cached_tokens": total_cached,
        "total_prompt_tokens": total_prompt,
        "total_cache_creation_tokens": total_cache_creation,
        "hit_ratio": ratio,
        "elapsed_seconds": round(elapsed, 3),
        "per_call_metrics": metrics,
    }


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # === 真跑硬闸门：必须显式授权才发起任何真实调用（防 .env 自动加载凭证导致误跑）===
    if os.environ.get("SPIKE_F3_AUTHORIZED") != "1":
        print(
            "SKIP: 未授权真跑（需 Maria 明确授权，由主控补跑）。\n"
            "      授权命令：SPIKE_F3_AUTHORIZED=1 .venv/bin/python "
            "scripts/spike_coding_prompt_cache.py",
            file=sys.stderr,
        )
        return 2

    if not get_llm_api_key():
        print("SKIP: 缺少 LLM_API_KEY 环境变量（CP-F3-2 待主控授权补跑）", file=sys.stderr)
        return 2
    if not get_deepxiv_token():
        print("SKIP: 缺少 DEEPXIV_TOKEN 环境变量（CP-F3-2 待主控授权补跑）", file=sys.stderr)
        return 2

    _patch_chat_openai_invoke()

    base_url = get_llm_base_url()
    model = get_llm_model()
    llm_config = LLMConfig(
        base_url=base_url,
        model=model,
        api_key=get_llm_api_key() or "",
        temperature=DEFAULT_LLM_TEMPERATURE,
        max_tokens=DEFAULT_LLM_MAX_TOKENS,
    )

    print("=== Spike F3：coding 节点 Prompt Cache 命中率回归（CP-F3-2）===")
    print(f"arxiv_id : {ARXIV_ID}")
    print(f"base_url : {base_url}")
    print(f"model    : {model}")
    print(f"守门     : R_after >= R_baseline_sp2({R_BASELINE_SP2}) * {GATE_FACTOR} = "
          f"{R_BASELINE_SP2 * GATE_FACTOR:.4f}")
    print()

    runs_root = Path(WORKSPACE_DIR) / "runs" / f"spike-f3-coding-{datetime.now():%Y%m%d-%H%M%S}"
    runs_root.mkdir(parents=True, exist_ok=True)

    print(">>> 连跑 coding × 3 次（首轮 cold + 2 轮 warm，尽量 5 分钟内完成）")
    run_results: List[Dict[str, Any]] = []
    phase_t0 = time.perf_counter()

    for i in range(1, 4):
        print(f"--- coding run #{i} 启动 ---")
        code_dir = runs_root / f"run{i}" / "code"
        code_dir.mkdir(parents=True, exist_ok=True)
        run_state = _build_coding_ready_state(llm_config, str(code_dir.resolve()))

        _METRICS_BUCKET.clear()
        t0 = time.perf_counter()
        update = coding(run_state)  # 真实 ReAct（真实 LLM；deepxiv 仅当 agent 自主调 read_section）
        elapsed = time.perf_counter() - t0

        run_metrics = list(_METRICS_BUCKET)
        summary = _summarize_run(i, run_metrics, elapsed)
        summary["degraded"] = "coding" in (update.get("degraded_nodes") or [])
        run_results.append(summary)

        print(
            f"    run #{i}: cached={summary['total_cached_tokens']} / "
            f"prompt={summary['total_prompt_tokens']} "
            f"({summary['hit_ratio']*100:.1f}% hit), "
            f"calls={summary['llm_calls']}, elapsed={summary['elapsed_seconds']}s, "
            f"degraded={summary['degraded']}"
        )
        if summary["total_cached_tokens"] == 0:
            print(f"    [WARN] run #{i} cached_tokens=0，可能 provider 不透传或缓存未命中")

    phase_elapsed = time.perf_counter() - phase_t0
    print()
    print(f">>> 3 次总耗时：{phase_elapsed:.2f}s（cache TTL 通常 ~5 分钟）")
    if phase_elapsed > 300:
        print(f"[WARN] 超出 ~5 分钟，cache TTL 可能受影响")

    # R_after = mean(R_2, R_3)（与 sp2 基线同口径，去掉 cold 首轮）
    r2 = run_results[1]["hit_ratio"]
    r3 = run_results[2]["hit_ratio"]
    r_after = (r2 + r3) / 2.0
    gate = R_BASELINE_SP2 * GATE_FACTOR
    passed = r_after >= gate

    print()
    print(f"=== R_after = mean(R_2, R_3) = ({r2:.4f} + {r3:.4f}) / 2 = {r_after:.4f} ===")
    print(f"=== 守门 R_after({r_after:.4f}) >= {gate:.4f} ? {'PASS' if passed else 'FAIL'} ===")

    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = Path(WORKSPACE_DIR) / "runs"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"spike-f3-coding-prompt-cache_{ts}.json"
    payload = {
        "spike": "F3",
        "task": "coding 节点 Prompt Cache 命中率回归（CP-F3-2）",
        "timestamp": ts,
        "arxiv_id": ARXIV_ID,
        "base_url": base_url,
        "model": model,
        "r_baseline_sp2": R_BASELINE_SP2,
        "gate_factor": GATE_FACTOR,
        "gate_threshold": round(gate, 4),
        "phase_elapsed_seconds": round(phase_elapsed, 3),
        "ttl_risk": phase_elapsed > 300,
        "runs": run_results,
        "r1_cold_hit_ratio": round(run_results[0]["hit_ratio"], 4),
        "r2_hit_ratio": round(r2, 4),
        "r3_hit_ratio": round(r3, 4),
        "r_after": round(r_after, 4),
        "gate_passed": passed,
    }
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print()
    print(f"原始指标落盘：{out_path}")

    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
