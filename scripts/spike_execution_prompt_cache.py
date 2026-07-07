"""Spike G3-E：execution 节点 Prompt Cache 命中率首测（CP-G3-1，待主控授权补跑）。

⚠️ **本脚本依赖 LLM 凭证，绝不在常规回归中自动跑。**
由主控在 Maria 明确授权后手动执行（沿用 sp2/sp3 e2e「凭证就绪后补跑」省配额范式）。
闸门与 spike_coding_prompt_cache.py 同款：``SPIKE_F3_AUTHORIZED=1`` 双保险。

用途（建基线，无历史对照故无守门线）：
- sp4 E2 为 execution 节点新建了整条字节级常量 SystemMessage
  （``_EXECUTION_SYSTEM_PROMPT_BODY``，CP-E2-1 已锁字节一致）；本脚本首测其
  真实命中率 R_after，作为后续 sprint 改动 execution prompt 时的守门基线。

设计（零 deepxiv 配额）：
- 不跑上游链路，预置 execution 所需 GlobalState（code_output_dir /
  reproduction_plan.execution_steps / paper_analysis.metrics）；
- sandbox 全 mock：prepare_venv 确定性成功 + run_in_venv 返回 exit 0 与
  ``<METRICS>`` JSON 标签（档 1 确定性解析，不触发档 3 LLM 抽取）+
  collect_artifacts 空——agent 走「prepare → run → finalize」最短路径，
  不真跑任何子进程；
- **连跑 execution × 3**（首轮 cold + 2 轮 warm，尽量 5 分钟内完成）；
- R_i = sum(cached_tokens) / sum(prompt_tokens)；R_after = mean(R_2, R_3)
  （与 sp2 S-3 / sp3 F3 同口径）。

运行（主控获 Maria 授权后）：
    SPIKE_F3_AUTHORIZED=1 .venv/bin/python scripts/spike_execution_prompt_cache.py
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(PROJECT_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

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
    get_llm_api_key,
    get_llm_base_url,
    get_llm_model,
)
from core.state import LLMConfig, create_initial_state  # noqa: E402
from sandbox.local_venv import SandboxPrepareResult, SandboxRunResult  # noqa: E402

# 复用 coding spike 的采集桶 / monkey-patch / 汇总（同一 list 对象，跨模块共享）。
from spike_coding_prompt_cache import (  # noqa: E402
    _METRICS_BUCKET,
    _patch_chat_openai_invoke,
    _summarize_run,
)

import importlib  # noqa: E402

# callable 遮蔽陷阱（tests/test_sprint4_e3.py 同款处置）：core/nodes/__init__ 把
# 同名函数 execution 绑到包属性上，`import core.nodes.execution as m` 拿到的是
# 函数而非模块，必须走 importlib。
execution_module = importlib.import_module("core.nodes.execution")  # noqa: E402

ARXIV_ID = "2405.14831"  # HippoRAG（与 sp1~sp4 各 spike 同源）


def _fake_prepare(work_dir, *a, **k) -> SandboxPrepareResult:
    venv = Path(work_dir) / ".venv"
    return SandboxPrepareResult(
        success=True, venv_dir=str(venv),
        python_exe=str(venv / "bin" / "python"),
        pip_exe=str(venv / "bin" / "pip"),
        env_info={"python_version": "3.11 (mock)"},
    )


def _fake_run(python_exe, command, work_dir, *a, **k) -> SandboxRunResult:
    return SandboxRunResult(
        exit_code=0,
        stdout='ok\n<METRICS>{"accuracy": 0.9}</METRICS>',
        stderr="", duration_seconds=0.1, timed_out=False,
        output_truncated=False, command=list(command),
    )


def _build_execution_ready_state(llm_config: LLMConfig, code_dir: str) -> dict:
    """预置 execution 所需上游字段（与 tests/test_sprint4_e2e.py real_s4_3 同构）。"""
    state = create_initial_state(user_input=ARXIV_ID, llm_config=llm_config)
    state["paper_meta"] = {"arxiv_id": ARXIV_ID, "title": "HippoRAG"}
    state["paper_analysis"] = {"metrics": ["accuracy"]}
    state["reproduction_plan"] = {
        "execution_steps": [
            {"command": "python fetch_data.py"},
            {"command": "python train.py"},
        ],
        "environment": {"python": "3.10"},
    }
    state["code_output_dir"] = code_dir
    state["current_step"] = "coding"
    return state


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if os.environ.get("SPIKE_F3_AUTHORIZED") != "1":
        print(
            "SKIP: 未授权真跑（需 Maria 明确授权，由主控补跑）。\n"
            "      授权命令：SPIKE_F3_AUTHORIZED=1 .venv/bin/python "
            "scripts/spike_execution_prompt_cache.py",
            file=sys.stderr,
        )
        return 2
    if not get_llm_api_key():
        print("SKIP: 缺少 LLM_API_KEY 环境变量", file=sys.stderr)
        return 2

    _patch_chat_openai_invoke()

    # sandbox 全 mock（脚本级直接替换模块属性，一次性进程无需恢复）。
    execution_module.prepare_venv = _fake_prepare
    execution_module.run_in_venv = _fake_run
    execution_module.collect_artifacts = lambda *a, **k: []

    base_url = get_llm_base_url()
    model = get_llm_model()
    llm_config = LLMConfig(
        base_url=base_url, model=model, api_key=get_llm_api_key() or "",
        temperature=DEFAULT_LLM_TEMPERATURE, max_tokens=DEFAULT_LLM_MAX_TOKENS,
    )

    print("=== Spike G3-E：execution 节点 Prompt Cache 命中率首测（建基线）===")
    print(f"arxiv_id : {ARXIV_ID}")
    print(f"base_url : {base_url}")
    print(f"model    : {model}")
    print("守门     : 无（首测建基线；后续改 execution prompt 时以本次 R_after×0.95 守门）")
    print()

    runs_root = (
        Path(WORKSPACE_DIR) / "runs"
        / f"spike-g3-execution-{datetime.now():%Y%m%d-%H%M%S}"
    )

    print(">>> 连跑 execution × 3 次（首轮 cold + 2 轮 warm，尽量 5 分钟内完成）")
    run_results: List[Dict[str, Any]] = []
    phase_t0 = time.perf_counter()

    for i in range(1, 4):
        print(f"--- execution run #{i} 启动 ---")
        code_dir = runs_root / f"run{i}" / "code"
        (code_dir / ".venv" / "bin").mkdir(parents=True, exist_ok=True)
        (code_dir / ".venv" / "pyvenv.cfg").write_text("home = /usr\n")
        run_state = _build_execution_ready_state(llm_config, str(code_dir.resolve()))

        _METRICS_BUCKET.clear()
        t0 = time.perf_counter()
        try:
            update = execution_module.execution(run_state)
            degraded = "execution" in (update.get("degraded_nodes") or [])
        except Exception as exc:  # noqa: BLE001 - interrupt/agent 异常记录后继续统计
            print(f"    [WARN] run #{i} execution 抛出 {type(exc).__name__}: {exc}")
            degraded = True
        elapsed = time.perf_counter() - t0

        summary = _summarize_run(i, list(_METRICS_BUCKET), elapsed)
        summary["degraded"] = degraded
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
        print("[WARN] 超出 ~5 分钟，cache TTL 可能受影响")

    r2 = run_results[1]["hit_ratio"]
    r3 = run_results[2]["hit_ratio"]
    r_after = (r2 + r3) / 2.0

    print()
    print(f"=== R_after = mean(R_2, R_3) = ({r2:.4f} + {r3:.4f}) / 2 = {r_after:.4f} ===")
    print("=== 首测基线建立：后续 execution prompt 改动以 "
          f"R >= {r_after:.4f} × 0.95 = {r_after * 0.95:.4f} 守门 ===")

    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = Path(WORKSPACE_DIR) / "runs"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"spike-g3-execution-prompt-cache_{ts}.json"
    payload = {
        "spike": "G3-E",
        "task": "execution 节点 Prompt Cache 命中率首测（CP-G3-1 建基线）",
        "timestamp": ts,
        "arxiv_id": ARXIV_ID,
        "base_url": base_url,
        "model": model,
        "phase_elapsed_seconds": round(phase_elapsed, 3),
        "ttl_risk": phase_elapsed > 300,
        "runs": run_results,
        "r1_cold_hit_ratio": round(run_results[0]["hit_ratio"], 4),
        "r2_hit_ratio": round(r2, 4),
        "r3_hit_ratio": round(r3, 4),
        "r_after_baseline": round(r_after, 4),
        "suggested_gate": round(r_after * 0.95, 4),
    }
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print()
    print(f"原始指标落盘：{out_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
