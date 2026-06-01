"""手工跑通 paper_intake + paper_analysis 的入口脚本（Sprint 1 阶段）。

用法：
    python scripts/run_paper.py                          # 默认靶论文 HippoRAG (2405.14831)
    python scripts/run_paper.py --arxiv-id 2310.06825    # 指定 arxiv_id
    python scripts/run_paper.py --arxiv-id 2310.06825 --no-save   # 不落盘

依赖环境变量：LLM_API_KEY / DEEPXIV_TOKEN（其他用 config.py 默认值）。

输出：
- 终端打印 paper_meta + paper_analysis 关键字段的中文摘要
- 落盘完整 GlobalState JSON 到 workspace/runs/<arxiv_id>_<timestamp>.json
- 节点 5/6/7 (resource_scout/planning/coding/execution/reporting) 是 Sprint 1 占位节点，会原样透传 state
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# 自动加载 .env：与 tests/conftest.py 同模式，项目根优先 > ~/.env，已存在的环境变量不覆盖。
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
from core.graph import build_graph  # noqa: E402
from core.state import LLMConfig, create_initial_state  # noqa: E402

DEFAULT_ARXIV_ID = "2405.14831"  # HippoRAG


def _json_default(obj: Any) -> Any:
    """LangChain BaseMessage 等对象的兜底序列化。"""
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if hasattr(obj, "dict"):
        return obj.dict()
    return repr(obj)


def _print_section(title: str) -> None:
    print(f"\n{'=' * 8} {title} {'=' * 8}")


def _print_paper_meta(pm: dict | None) -> None:
    _print_section("paper_meta (节点 1)")
    if not pm:
        print("  <空>")
        return
    print(f"  arxiv_id     : {pm.get('arxiv_id')}")
    print(f"  title        : {pm.get('title')}")
    authors = pm.get("authors") or []
    print(f"  authors ({len(authors)}) : {', '.join(authors[:5])}{' ...' if len(authors) > 5 else ''}")
    print(f"  categories   : {pm.get('categories')}")
    print(f"  publish_date : {pm.get('publish_date')}")
    print(f"  github_url   : {pm.get('github_url')}")
    abstract = pm.get("abstract") or ""
    print(f"  abstract     : {abstract[:240]}{'...' if len(abstract) > 240 else ''}")
    if pm.get("tldr"):
        print(f"  tldr         : {pm['tldr']}")


def _print_paper_analysis(pa: dict | None) -> None:
    _print_section("paper_analysis (节点 2)")
    if not pa:
        print("  <空>")
        return
    method = pa.get("method_summary") or ""
    print(f"  method_summary  : {method[:320]}{'...' if len(method) > 320 else ''}")
    print(f"  key_formulas    : {pa.get('key_formulas')}")
    print(f"  datasets        : {pa.get('datasets')}")
    print(f"  metrics         : {pa.get('metrics')}")
    print(f"  framework       : {pa.get('framework')}")
    print(f"  hardware_reqs   : {pa.get('hardware_requirements')}")
    print(f"  sections_read   : {pa.get('sections_read')}")
    hp = pa.get("hyperparams") or {}
    print(f"  hyperparams ({len(hp)})  : {dict(list(hp.items())[:6])}{' ...' if len(hp) > 6 else ''}")
    notes = pa.get("analysis_notes") or ""
    if notes:
        print(f"  analysis_notes  : {notes[:280]}{'...' if len(notes) > 280 else ''}")


def _print_errors(state: dict) -> None:
    _print_section("错误与降级")
    node_errors = state.get("node_errors") or []
    degraded = state.get("degraded_nodes") or []
    print(f"  degraded_nodes  : {degraded if degraded else '<无>'}")
    print(f"  node_errors ({len(node_errors)}):")
    if not node_errors:
        print("    <无>")
    for ne in node_errors:
        print(f"    - {ne.get('node')} / {ne.get('severity')} / {ne.get('category')}: {ne.get('message')}")
    print(f"  retry_budget_remaining : {state.get('retry_budget_remaining')}")


def main() -> int:
    parser = argparse.ArgumentParser(description="跑通 paper_intake + paper_analysis 主路径")
    parser.add_argument("--arxiv-id", default=DEFAULT_ARXIV_ID, help=f"论文 arXiv ID（默认 {DEFAULT_ARXIV_ID} HippoRAG）")
    parser.add_argument("--no-save", action="store_true", help="不落盘完整 state JSON")
    args = parser.parse_args()

    if not get_llm_api_key():
        print("ERROR: 缺少 LLM_API_KEY 环境变量", file=sys.stderr)
        return 2
    if not get_deepxiv_token():
        print("ERROR: 缺少 DEEPXIV_TOKEN 环境变量", file=sys.stderr)
        return 2

    llm_config = LLMConfig(
        base_url=get_llm_base_url(),
        model=get_llm_model(),
        api_key=get_llm_api_key() or "",
        temperature=DEFAULT_LLM_TEMPERATURE,
        max_tokens=DEFAULT_LLM_MAX_TOKENS,
    )

    initial_state = create_initial_state(user_input=args.arxiv_id, llm_config=llm_config)
    thread_id = f"manual-{args.arxiv_id}-{uuid.uuid4().hex[:8]}"
    config = {"configurable": {"thread_id": thread_id}}

    print(f"开始执行：arxiv_id={args.arxiv_id} thread_id={thread_id}")
    print(f"LLM: {llm_config['model']} @ {llm_config['base_url']}")

    graph = build_graph()
    t0 = time.perf_counter()
    final_state = graph.invoke(initial_state, config)
    elapsed = time.perf_counter() - t0
    print(f"\n执行完成，耗时 {elapsed:.2f} 秒")

    _print_paper_meta(final_state.get("paper_meta"))
    _print_paper_analysis(final_state.get("paper_analysis"))
    _print_errors(final_state)

    if not args.no_save:
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        out_dir = Path(WORKSPACE_DIR) / "runs"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{args.arxiv_id.replace('/', '_')}_{ts}.json"
        dumpable = dict(final_state)
        dumpable.pop("llm_config", None)  # A3 已移除该镜像字段，保留 pop 兼容老 checkpoint
        dumpable.pop("llm_config_set", None)  # 含 api_key，剔除
        with out_path.open("w", encoding="utf-8") as f:
            json.dump(
                dumpable,
                f,
                ensure_ascii=False,
                indent=2,
                default=_json_default,
            )
        print(f"\n完整 state 已落盘：{out_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
