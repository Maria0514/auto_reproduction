"""D1 - core/graph.py 真实端到端 pytest（依赖外部 LLM + deepxiv API）。

聚焦 graph 集成视角，不重复 paper_intake / paper_analysis 节点本体的 e2e（已有 4+6 用例
分别覆盖）。本套件验证 PRD §6.1 阶段 1 端到端验收 AC-1（基础流程可执行）与 AC-2（状态
持久化到 SQLite）。

测试论文：arXiv:2405.14831 (HippoRAG)
- 已被 paper_intake_e2e / paper_analysis_e2e 验证可访问 + CS 领域 + 章节齐全
- 跑过的 fixture 缓存沉淀，省 token 与时间

运行方式：
    LLM_API_KEY=... DEEPXIV_TOKEN=... pytest tests/test_graph_e2e.py -m e2e -v -s

任一凭证缺失则全部跳过。pytest -m e2e 选择性运行。

设计要点：
1. 用 ``tmp_path_factory`` 提供隔离的 SQLite checkpoint 文件，每个 module 一份；不污染项目根。
2. ``build_graph(checkpointer=SqliteSaver(...))`` 用真实 SQLite 文件（非 in-memory），确保
   AC-2 的"文件存在且大小 > 0"可断言。
3. ``graph.invoke`` 跑一次完整 7 节点流水线缓存到 module scope fixture，下游用例从这里
   读，不重复跑 LLM；三个用例真实总耗时 ≈ 一次完整流水线（~120-180 秒）。
4. AC-2 的"新建 graph 实例恢复"用同一 SQLite 文件 + 同一 thread_id + 新 SqliteSaver 实例
   验证 ``get_state(config)`` 回读，并断言 ``get_state_history`` ≥ 8（START + 7 nodes 至少各一帧）。
"""
from __future__ import annotations

import os
import sqlite3
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, Tuple

import pytest
from langgraph.checkpoint.sqlite import SqliteSaver

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import (  # noqa: E402
    DEFAULT_LLM_MAX_TOKENS,
    DEFAULT_LLM_TEMPERATURE,
    get_deepxiv_token,
    get_llm_api_key,
    get_llm_base_url,
    get_llm_model,
)
from core.graph import build_graph  # noqa: E402
from core.nodes.paper_analysis import NODE_NAME as ANALYSIS_NODE_NAME  # noqa: E402
from core.nodes.paper_intake import NODE_NAME as INTAKE_NODE_NAME  # noqa: E402
from core.state import LLMConfig, create_initial_state  # noqa: E402


# ============== 常量 ==============

PAPER_ARXIV_ID = "2405.14831"  # HippoRAG，主路径靶论文

# AC-2 验收：图编排后至少包含 START + 7 个业务节点的 checkpoint，每节点至少 1 帧。
# LangGraph 实际会写入更多（每节点 enter/exit 各 1 帧通常），所以下限取 8（保守阈值）。
MIN_EXPECTED_CHECKPOINTS = 8


# NOTE (Sprint 2 C1)：本套件是 sp1 D1 的 e2e，前提假设是"7 节点线性跑到底、
# resource_scout / planning 为 pass-through 占位"。C1 升级后 resource_scout / planning
# 已接入真节点，且 planning 内部调用 interrupt() —— graph 会在 planning **自然暂停**
# 而非跑到 reporting/END，且 resource_info 会被真实填充。因此本套件的 d1_01（current_step
# 期望 paper_analysis）/ d1_02（resource_info、reproduction_plan 期望 None）断言已与新语义
# 冲突，必然失败。这些用例应由测试工程师在 sp2 E 阶段重写为"跑到 planning 暂停 +
# Command(resume=...) 恢复 + 3 路决策"的新 graph e2e（见 docs/TODO.md E 阶段条目）。
# 在重写前先整体跳过，避免给出虚假的红灯/绿灯。
pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skip(
        reason="sp1 D1 e2e 与 sp2 C1（planning interrupt 暂停 + 真节点）语义冲突，"
        "已由 sp2 E 阶段重写为 tests/test_sprint2_c1_e2e.py（resume 流程 graph e2e，"
        "2026-06-03 真实 LLM 链路 3 用例验收通过）；本旧套件保留为历史证据，永久 skip。",
    ),
]


def _has_credentials() -> bool:
    return bool(get_llm_api_key()) and bool(get_deepxiv_token())


skip_if_no_creds = pytest.mark.skipif(
    not _has_credentials(),
    reason="缺少 LLM_API_KEY 或 DEEPXIV_TOKEN 环境变量",
)


# ============== Fixtures ==============


@pytest.fixture(scope="module")
def llm_config() -> LLMConfig:
    return LLMConfig(
        base_url=get_llm_base_url(),
        model=get_llm_model(),
        api_key=get_llm_api_key() or "",
        temperature=DEFAULT_LLM_TEMPERATURE,
        max_tokens=DEFAULT_LLM_MAX_TOKENS,
    )


@pytest.fixture(scope="module")
def sqlite_db_path(tmp_path_factory) -> Path:
    """每个 module 一份独立的 SQLite checkpoint 文件，避免跨用例 / 跨模块污染。"""
    d = tmp_path_factory.mktemp("graph_e2e_checkpoints")
    return d / "checkpoints.db"


@pytest.fixture(scope="module")
def main_thread_id() -> str:
    """主流水线运行用的 thread_id，跨用例共享以便 AC-2 回读验证。"""
    return f"graph-e2e-main-{uuid.uuid4().hex[:8]}"


@pytest.fixture(scope="module")
def main_pipeline_result(
    llm_config: LLMConfig,
    sqlite_db_path: Path,
    main_thread_id: str,
) -> Tuple[Dict[str, Any], Path, str]:
    """跑一次完整的 7 节点流水线，结果缓存供 TC-E2E-D1-01 / 02 / 03 复用。

    返回 (final_state, sqlite_db_path, main_thread_id)。

    设计：用 sqlite3.connect 显式开 WAL（与 core/checkpointer.py::get_checkpointer 一致），
    使下游 reopen 时数据可见。
    """
    if not _has_credentials():
        pytest.skip("缺少凭证")

    # 用真实 SQLite 文件 + WAL 模式
    conn = sqlite3.connect(str(sqlite_db_path), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    saver = SqliteSaver(conn)

    graph = build_graph(checkpointer=saver)
    initial_state = create_initial_state(
        user_input=PAPER_ARXIV_ID,
        llm_config=llm_config,
    )

    config_dict = {"configurable": {"thread_id": main_thread_id}}
    final_state = graph.invoke(initial_state, config_dict)

    # 显式关闭连接，强制 WAL 刷盘到 -wal/-shm（后续用新 SqliteSaver 时确保数据可见）
    try:
        conn.commit()
    except Exception:
        pass
    try:
        conn.close()
    except Exception:
        pass

    return final_state, sqlite_db_path, main_thread_id


# ============== TC-E2E-D1-01：阶段 1 验收主路径（AC-1） ==============


@skip_if_no_creds
def test_e2e_d1_01_full_pipeline_invoke_acceptance(main_pipeline_result):
    """TC-E2E-D1-01：真实 7 节点 invoke 跑通，PRD §6.1 AC-1 五步验证逐条断言。

    AC-1 验证步骤（PRD 700-714 行）：
    1. 创建包含 LLM 配置与 arXiv ID 的初始 GlobalState（fixture 已构造）
    2. build_graph() 构建图，graph.invoke(state, config) 执行（fixture 已执行）
    3. paper_meta 不为 None 且关键字段（arxiv_id, title, abstract）已填充
    4. paper_analysis 不为 None 且关键字段（method_summary, datasets, metrics）已填充
    5. current_step 已更新（非 'start'）
    """
    final_state, _db, _tid = main_pipeline_result

    # 3) paper_meta 与关键字段
    pm = final_state.get("paper_meta")
    assert pm is not None, f"paper_meta 未填充：final_state={final_state}"
    assert pm.get("arxiv_id") == PAPER_ARXIV_ID, (
        f"paper_meta.arxiv_id 未清洗为输入 ID：{pm.get('arxiv_id')!r}"
    )
    assert pm.get("title"), f"paper_meta.title 为空：{pm}"
    assert pm.get("abstract"), f"paper_meta.abstract 为空：{pm}"
    # 兼顾 PRD AC-1 文字（仅列 arxiv_id/title/abstract），并加 authors / categories
    # 是因为 paper_intake_e2e 已在节点级覆盖；这里再压一道防止 graph 层吞掉字段。
    assert isinstance(pm.get("authors"), list) and len(pm["authors"]) > 0, (
        f"paper_meta.authors 为空：{pm.get('authors')}"
    )

    # 4) paper_analysis 与关键字段
    pa = final_state.get("paper_analysis")
    assert pa is not None, f"paper_analysis 未填充：final_state.keys={list(final_state.keys())}"
    assert pa.get("method_summary"), f"paper_analysis.method_summary 为空：{pa}"
    # PRD 列了 datasets 与 metrics，但允许其一为空（数据论文可能没有显式 metrics 等）
    assert pa.get("datasets") or pa.get("metrics"), (
        f"datasets 与 metrics 均为空（核心字段全缺）：{pa}"
    )
    assert pa.get("sections_read"), (
        f"sections_read 为空，意味着 paper_analysis 工具调用失败：{pa}"
    )

    # 5) current_step 已更新——5 个占位节点不写 current_step，所以最后写入者是
    # paper_analysis（按顺序边在 paper_analysis 之后 5 个占位都返回 {}，不覆盖该字段）
    assert final_state.get("current_step") == ANALYSIS_NODE_NAME, (
        f"current_step 期望 {ANALYSIS_NODE_NAME!r}（最后一个写入此字段的节点），"
        f"实际 {final_state.get('current_step')!r}"
    )

    # 顺路防御：成功路径不应在 node_errors 留下 paper_intake/paper_analysis 的 permanent 记录
    ne = final_state.get("node_errors") or []
    permanent = [
        e for e in ne
        if e.get("node_name") in {INTAKE_NODE_NAME, ANALYSIS_NODE_NAME}
        and e.get("error_type") == "permanent"
    ]
    assert not permanent, (
        f"成功主路径不应有 paper_intake/paper_analysis 的 permanent NodeError：{permanent}"
    )


# ============== TC-E2E-D1-02：占位节点透明性 ==============


@skip_if_no_creds
def test_e2e_d1_02_placeholder_nodes_do_not_pollute_state(main_pipeline_result):
    """TC-E2E-D1-02：5 个占位节点（resource_scout/planning/coding/execution/reporting）
    应对 paper_intake / paper_analysis 写入的 state 完全透明。

    断言：
    1. ``resource_info`` / ``reproduction_plan`` / ``code_output_dir`` / ``execution_result``
       / ``report_path`` 这些占位节点对应的字段应仍为 None（占位节点没有真实业务）。
    2. ``paper_meta`` / ``paper_analysis`` 字段未被 5 个下游占位节点覆盖（值与上游写入相同）。
    3. ``user_input`` / ``input_type`` / ``llm_config_set`` 等初始字段保持不变。
    """
    final_state, _db, _tid = main_pipeline_result

    # 1) 占位节点对应字段都应仍为 None（5 个占位都返回 {}，不会写这些字段）
    placeholder_fields = [
        "resource_info",
        "reproduction_plan",
        "code_output_dir",
        "execution_result",
        "report_path",
    ]
    for f in placeholder_fields:
        assert final_state.get(f) is None, (
            f"占位节点对应字段 {f!r} 应保持 None，实际为 {final_state.get(f)!r}；"
            f"说明某个占位节点污染了状态"
        )

    # 2) paper_meta / paper_analysis 字段保持非空（占位节点没把它们覆盖回 None）
    assert final_state.get("paper_meta") is not None
    assert final_state.get("paper_analysis") is not None
    assert final_state["paper_meta"].get("arxiv_id") == PAPER_ARXIV_ID, (
        "paper_meta.arxiv_id 被下游覆盖，占位节点透明性破坏"
    )

    # 3) 初始字段保持不变
    assert final_state.get("user_input") == PAPER_ARXIV_ID, (
        f"user_input 被覆盖：{final_state.get('user_input')!r}"
    )
    assert final_state.get("input_type") == "arxiv_id", (
        f"input_type 被覆盖：{final_state.get('input_type')!r}"
    )
    # A3 起镜像字段 llm_config 已移除；llm_config_set 应仍存在（LangGraph 默认合并语义不删除字段）
    assert final_state.get("llm_config_set"), "llm_config_set 在流水线后被清空"
    assert final_state["llm_config_set"].get("default"), "llm_config_set.default 在流水线后被清空"


# ============== TC-E2E-D1-03：SqliteSaver 持久化与回读（AC-2） ==============


@skip_if_no_creds
def test_e2e_d1_03_sqlite_checkpoint_persist_and_resume(main_pipeline_result, llm_config):
    """TC-E2E-D1-03：PRD §6.1 AC-2 四步验收逐条覆盖。

    AC-2 验证步骤（PRD 718-723 行）：
    1. 执行 AC-1 流程（main_pipeline_result fixture 已跑）
    2. checkpoints.db 文件已创建且大小 > 0
    3. graph.get_state(config) 可读取完整状态
    4. 新建 graph 实例，使用相同 thread_id，可恢复到上次执行后的状态

    额外断言：get_state_history 应至少返回 ``MIN_EXPECTED_CHECKPOINTS`` 帧（防止仅最末
    一帧被写入的退化场景）。
    """
    _final, db_path, thread_id = main_pipeline_result

    # 2) SQLite 文件大小 > 0
    assert db_path.exists(), f"SQLite checkpoint 文件不存在：{db_path}"
    db_size = db_path.stat().st_size
    assert db_size > 0, (
        f"SQLite checkpoint 文件大小为 0，未发生任何持久化：{db_path}"
    )

    # 3) + 4) 用新 SqliteSaver 实例化新 graph，从同一 thread_id 读回状态
    conn2 = sqlite3.connect(str(db_path), check_same_thread=False)
    conn2.execute("PRAGMA journal_mode=WAL;")
    try:
        saver2 = SqliteSaver(conn2)
        graph2 = build_graph(checkpointer=saver2)
        config = {"configurable": {"thread_id": thread_id}}

        snapshot = graph2.get_state(config)
        assert snapshot is not None, (
            f"用相同 thread_id={thread_id!r} get_state 返回 None；checkpoint 未被持久化"
        )
        restored = snapshot.values
        assert restored, f"get_state.values 为空 dict：{snapshot}"

        # 关键字段：paper_meta / paper_analysis 都应从 SQLite 完整回读
        rpm = restored.get("paper_meta")
        rpa = restored.get("paper_analysis")
        assert rpm and rpm.get("arxiv_id") == PAPER_ARXIV_ID, (
            f"resume 后 paper_meta 缺失或 arxiv_id 不一致：{rpm}"
        )
        assert rpa and rpa.get("method_summary"), (
            f"resume 后 paper_analysis 缺失 method_summary：{rpa}"
        )
        assert rpa.get("sections_read"), (
            f"resume 后 paper_analysis.sections_read 为空：{rpa}"
        )

        # get_state_history：至少 MIN_EXPECTED_CHECKPOINTS 帧
        history = list(graph2.get_state_history(config))
        assert len(history) >= MIN_EXPECTED_CHECKPOINTS, (
            f"get_state_history 帧数 {len(history)} < {MIN_EXPECTED_CHECKPOINTS}；"
            f"SqliteSaver 可能仅持久化了末帧，违反 LangGraph 编排预期"
        )

        # 历史帧倒序：最近一帧的 values 应与 snapshot.values 等价（同一 checkpoint）
        latest = history[0]
        assert latest.values.get("paper_meta", {}).get("arxiv_id") == PAPER_ARXIV_ID, (
            "最近一帧 paper_meta 与 snapshot 不一致"
        )
    finally:
        try:
            conn2.close()
        except Exception:
            pass


# ============== TC-E2E-D1-04（保留位，本轮不启用） ==============
# 异常 arXiv ID（如不存在论文）下 graph 不抛异常退出 + degraded 标记正确传播。
# 当前轮次为控制 e2e 总时长（目标 < 10 分钟）暂不开启；paper_intake_e2e 已在节点层覆盖
# 非法输入路径。若后续需要 graph 层异常透传断言，可启用此用例（reason 见测试报告 §5）。
