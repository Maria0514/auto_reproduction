"""Spike S-3：Prompt Cache fresh 基线复采（Sprint 2 启动前）。

用途：
- 固定 arxiv_id=2405.14831 (HippoRAG)；
- 先跑 1 次 paper_intake 拿 paper_meta（不计入基线）；
- **连跑 3 次 paper_analysis**，记录每次的 prompt_tokens / cached_tokens；
- 计算 R_i = sum(cached_tokens) / sum(prompt_tokens)；
- 计算 R_baseline = mean(R_2, R_3)，作为 sp2 阶段 B HumanMessage 输出语言策略
  改造的命中率守门基线（PRD R-PC4 ≥ baseline × 0.95）。

关键约束：
- 不修改任何 sp1 代码（spike 测的就是 sp1 现状基线）；
- 3 次连跑必须在 30 秒内完成，避免 cache TTL 过期（通常 5 分钟）；
- ReAct 子图通过 llm.invoke() 直调，**绕开 _call_llm_with_retry，因此
  _log_cache_metrics 不会被自动触发**。本脚本通过 monkey-patch
  ChatOpenAI.invoke 在每次响应后调用 _log_cache_metrics + 把 (cached,
  prompt) 数对累计到全局列表，避免修改 sp1 代码。

输出：
- 终端打印 3 次的 cached / prompt / 命中率 + R_baseline；
- 完整指标 JSON 落盘 workspace/runs/spike-s3-prompt-cache-baseline_<ts>.json
  （供报告引用原始日志）。

依赖环境变量：LLM_API_KEY / DEEPXIV_TOKEN / [可选] LLM_BASE_URL / LLM_MODEL。
"""
from __future__ import annotations

import json
import logging
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# 自动加载 .env（与 tests/conftest.py / run_paper.py 同模式）。
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
from core.nodes.paper_analysis import paper_analysis  # noqa: E402
from core.nodes.paper_intake import paper_intake  # noqa: E402
from core.state import LLMConfig, create_initial_state  # noqa: E402

ARXIV_ID = "2405.14831"  # HippoRAG（与 sp1 F 阶段同源，便于横向对比）


# ========== Monkey-patch: 把每次 LLM 响应的 (cached, prompt) 数对累计到 _METRICS_BUCKET ==========

_METRICS_BUCKET: List[Dict[str, int]] = []  # 当前 run 的累计 metric


def _extract_cache_metrics(response: Any) -> Dict[str, int]:
    """从 LLM 响应中抽 cached_tokens / prompt_tokens / cache_creation_tokens。

    抽取规则与 core.llm_client._log_cache_metrics 完全一致（OpenAI /
    Anthropic / LangChain usage_metadata 三种 schema 都覆盖），但**返回结构化
    字典而不只是打 INFO 日志**，便于落盘报告。
    """
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
    """在 ChatOpenAI.invoke 出口包一层，每次响应后 push 指标到 _METRICS_BUCKET。"""
    from langchain_openai import ChatOpenAI

    if getattr(ChatOpenAI, "_spike_s3_patched", False):
        return

    original_invoke = ChatOpenAI.invoke

    def patched_invoke(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        response = original_invoke(self, *args, **kwargs)
        try:
            metric = _extract_cache_metrics(response)
            _METRICS_BUCKET.append(metric)
            # 同时调原 _log_cache_metrics 触发 INFO 日志，保留 sp1 既有日志链路
            _log_cache_metrics(response)
        except Exception:  # noqa: BLE001
            pass
        return response

    ChatOpenAI.invoke = patched_invoke  # type: ignore[assignment]
    ChatOpenAI._spike_s3_patched = True  # type: ignore[attr-defined]


# ========== Spike 主流程 ==========


def _build_initial_state(arxiv_id: str, llm_config: LLMConfig) -> dict:
    """构造完整 GlobalState（与 run_paper.py / 单测保持一致）。"""
    state = create_initial_state(user_input=arxiv_id, llm_config=llm_config)
    return state


def _merge_update(state: dict, update: dict) -> dict:
    """把节点 update dict 合并到 state（不依赖 LangGraph reducer，手工合并）。"""
    merged = dict(state)
    for k, v in update.items():
        if v is None:
            continue
        if k in ("node_errors", "degraded_nodes") and isinstance(v, list):
            merged[k] = v  # 节点返回的是完整列表（已经基于旧值扩充），直接覆盖
        else:
            merged[k] = v
    return merged


def _summarize_run(idx: int, metrics: List[Dict[str, int]], elapsed: float) -> Dict[str, Any]:
    """单次 run 的指标聚合：累加所有 LLM 调用的 cached / prompt，给出命中率。"""
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
    # 日志：INFO 级别，捕获 _log_cache_metrics 的 INFO 输出
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if not get_llm_api_key():
        print("ERROR: 缺少 LLM_API_KEY 环境变量", file=sys.stderr)
        return 2
    if not get_deepxiv_token():
        print("ERROR: 缺少 DEEPXIV_TOKEN 环境变量", file=sys.stderr)
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

    print(f"=== Spike S-3 Prompt Cache fresh 基线复采 ===")
    print(f"arxiv_id : {ARXIV_ID}")
    print(f"base_url : {base_url}")
    print(f"model    : {model}")
    print()

    # ---------- 阶段 1：跑 paper_intake 拿 paper_meta（不计入 3 次基线）----------
    print(">>> 阶段 1：paper_intake 拿 paper_meta（不计入基线）")
    _METRICS_BUCKET.clear()
    state = _build_initial_state(ARXIV_ID, llm_config)
    t0 = time.perf_counter()
    intake_update = paper_intake(state)
    intake_elapsed = time.perf_counter() - t0
    state = _merge_update(state, intake_update)
    paper_meta = state.get("paper_meta")
    if not paper_meta or not paper_meta.get("title"):
        print("ERROR: paper_intake 未拿到 paper_meta，无法进入 paper_analysis", file=sys.stderr)
        return 3
    print(f"    paper_meta.title = {paper_meta.get('title')!r}")
    print(f"    paper_intake 耗时 {intake_elapsed:.2f}s，LLM 调用 {len(_METRICS_BUCKET)} 次")
    print()

    # ---------- 阶段 2：连跑 paper_analysis × 3，每次单独记录指标 ----------
    print(">>> 阶段 2：连跑 paper_analysis × 3 次（30 秒内完成）")
    run_results: List[Dict[str, Any]] = []
    phase2_t0 = time.perf_counter()

    for i in range(1, 4):
        print(f"--- run #{i} 启动 ---")
        # 每次 run 重置 state 中可能被上次污染的字段（仅 paper_analysis 相关写入 +
        # retry_budget_remaining + degraded / node_errors），但保留 paper_meta。
        run_state = dict(state)
        run_state["paper_analysis"] = None
        run_state["retry_budget_remaining"] = 50
        run_state["node_errors"] = []
        run_state["degraded_nodes"] = []
        run_state["current_step"] = "paper_intake"  # 复位到 paper_intake 完成态

        _METRICS_BUCKET.clear()
        t0 = time.perf_counter()
        update = paper_analysis(run_state)
        elapsed = time.perf_counter() - t0

        # 把本次 metric 拷出来（_METRICS_BUCKET 下一轮会 clear）
        run_metrics = list(_METRICS_BUCKET)
        summary = _summarize_run(i, run_metrics, elapsed)
        run_results.append(summary)

        # 更新 state（让 run_index 之间不冲突，但 paper_meta 始终保留）
        state = _merge_update(state, update)

        print(
            f"    run #{i}: cached={summary['total_cached_tokens']} / "
            f"prompt={summary['total_prompt_tokens']} "
            f"({summary['hit_ratio']*100:.1f}% hit), "
            f"calls={summary['llm_calls']}, elapsed={summary['elapsed_seconds']}s"
        )
        if summary["total_cached_tokens"] == 0:
            print(f"    [WARN] run #{i} cached_tokens=0，可能 provider 不透传或缓存未命中")

    phase2_elapsed = time.perf_counter() - phase2_t0
    print()
    print(f">>> 阶段 2 总耗时：{phase2_elapsed:.2f}s（约束 <= 30s）")
    if phase2_elapsed > 30:
        print(f"[WARN] 超出 30 秒约束，cache TTL 可能受影响")

    # ---------- R_baseline 计算 ----------
    r2 = run_results[1]["hit_ratio"]
    r3 = run_results[2]["hit_ratio"]
    r_baseline = (r2 + r3) / 2.0
    print()
    print(f"=== R_baseline = mean(R_2, R_3) = ({r2:.4f} + {r3:.4f}) / 2 = {r_baseline:.4f} ===")
    print(f"    (作为 sp2 阶段 B HumanMessage 输出语言策略改造的 R-PC4 守门基线)")

    # ---------- 落盘原始指标 JSON ----------
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = Path(WORKSPACE_DIR) / "runs"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"spike-s3-prompt-cache-baseline_{ts}.json"
    payload = {
        "spike": "S-3",
        "task": "Prompt Cache fresh 基线复采",
        "timestamp": ts,
        "arxiv_id": ARXIV_ID,
        "base_url": base_url,
        "model": model,
        "paper_intake": {
            "elapsed_seconds": round(intake_elapsed, 3),
            "title": paper_meta.get("title"),
        },
        "phase2_elapsed_seconds": round(phase2_elapsed, 3),
        "ttl_violated": phase2_elapsed > 30,
        "runs": run_results,
        "r_baseline": round(r_baseline, 4),
        "r2_hit_ratio": round(r2, 4),
        "r3_hit_ratio": round(r3, 4),
        "r1_cold_hit_ratio": round(run_results[0]["hit_ratio"], 4),
    }
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print()
    print(f"原始指标落盘：{out_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
