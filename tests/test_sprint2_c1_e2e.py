"""Sprint 2 C1 - 真实 LLM + interrupt resume 的 graph 端到端 pytest。

重写自 sp1 `tests/test_graph_e2e.py`（[SP1-E2E-OBSOLETE]）：sp1 D1 e2e 的前提
"7 节点线性跑到底 + resource_scout/planning 为 pass-through" 与 sp2 C1 语义冲突
（planning interrupt 自然暂停 + 真节点填充）。本套件覆盖 **C1 升级后** 的 graph
集成视角：

    跑到 planning 自然暂停（interrupt 在节点函数体内触发，无 interrupt_before/after）
    + Command(resume=...) 恢复
    + 3 路决策（approve→coding→END / cancel→END / revise→self-loop）
    + 真实 SqliteSaver 跨实例回读持久化。

被测链路全程真实：paper_intake → paper_analysis → resource_scout → planning 四个
真节点（真实 ChatOpenAI + 真实 deepxiv SDK + 真实 git/网络），仅 coding/execution/
reporting 仍为占位节点（C1 现状）。

测试论文：arXiv:2405.14831 (HippoRAG)
- 已被 paper_intake_e2e / paper_analysis_e2e / b2_e2e 验证可访问 + CS 领域 + 章节齐全。

凭证驱动（无 flag，conftest 自动加载 .env）：
- LLM_API_KEY + DEEPXIV_TOKEN 就绪 -> 真跑；
- 任一缺失 -> 全部 skip，reason 可见。

运行方式：
    pytest tests/test_sprint2_c1_e2e.py -m e2e -v -s

省 token / 防抖设计（真实 LLM 跑一次"到 planning 暂停"≈ paper_analysis 全链路开销）：
- 两条 thread 各跑一次"到 planning 暂停"：
    - approve thread：跑到暂停后 revise(self-loop) -> 再暂停 -> approve -> coding -> END
      （单 thread 同时验证 self-loop + approve + SqliteSaver 跨实例回读）；
    - cancel thread：跑到暂停后 cancel -> END。
- "到 planning 暂停"的真实链路只在 module fixture 里各跑一次，resume 步骤本身（approve/
  cancel/revise）不再额外触发 paper_intake/paper_analysis/resource_scout（已 checkpoint）。
"""
from __future__ import annotations

import sqlite3
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, Tuple

import pytest
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

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
from core.state import ExecutionMode, LLMConfig, create_initial_state  # noqa: E402


pytestmark = pytest.mark.e2e


PAPER_ARXIV_ID = "2405.14831"  # HippoRAG，主路径靶论文

# planning 自然暂停后，state_history 至少包含 START + 4 真节点的若干帧；保守下限。
MIN_HISTORY_FRAMES_AT_PAUSE = 4


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


def _make_wal_saver(db_path: Path) -> Tuple[sqlite3.Connection, SqliteSaver]:
    """新建一个 WAL 模式 SqliteSaver（与 core/checkpointer.py 一致），返回 (conn, saver)。"""
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    return conn, SqliteSaver(conn)


def _run_to_pause(
    db_path: Path,
    thread_id: str,
    llm_config: LLMConfig,
) -> Dict[str, Any]:
    """真实跑 paper_intake -> paper_analysis -> resource_scout -> planning(interrupt) 暂停。

    用真实 SQLite 文件 + WAL；invoke 返回值里应含 ``__interrupt__``。完成后显式关闭连接，
    强制 WAL 刷盘，便于后续用新 SqliteSaver 实例回读。返回 graph.invoke 的返回值。
    """
    conn, saver = _make_wal_saver(db_path)
    try:
        graph = build_graph(checkpointer=saver)
        initial_state = create_initial_state(
            user_input=PAPER_ARXIV_ID,
            llm_config=llm_config,
        )
        config = {"configurable": {"thread_id": thread_id}}
        out = graph.invoke(initial_state, config)
        try:
            conn.commit()
        except Exception:
            pass
        return out
    finally:
        try:
            conn.close()
        except Exception:
            pass


@pytest.fixture(scope="module")
def approve_thread(tmp_path_factory, llm_config) -> Tuple[Path, str]:
    """approve thread：真实跑到 planning 暂停（成本最高的真实链路只跑一次）。

    返回 (db_path, thread_id)。后续 revise / approve resume 用例从这个已暂停的
    checkpoint 出发，不再重跑上游真节点。
    """
    if not _has_credentials():
        pytest.skip("缺少 LLM_API_KEY 或 DEEPXIV_TOKEN")
    d = tmp_path_factory.mktemp("c1_e2e_approve")
    db_path = d / "approve.db"
    thread_id = f"c1-e2e-approve-{uuid.uuid4().hex[:8]}"
    out = _run_to_pause(db_path, thread_id, llm_config)
    # 前置健全：必须真的暂停在 planning（否则后续 resume 用例无意义）
    assert "__interrupt__" in out, f"approve thread 未在 planning 暂停：keys={list(out.keys())}"
    return db_path, thread_id


@pytest.fixture(scope="module")
def cancel_thread(tmp_path_factory, llm_config) -> Tuple[Path, str]:
    """cancel thread：真实跑到 planning 暂停（独立 thread，与 approve 隔离）。"""
    if not _has_credentials():
        pytest.skip("缺少 LLM_API_KEY 或 DEEPXIV_TOKEN")
    d = tmp_path_factory.mktemp("c1_e2e_cancel")
    db_path = d / "cancel.db"
    thread_id = f"c1-e2e-cancel-{uuid.uuid4().hex[:8]}"
    out = _run_to_pause(db_path, thread_id, llm_config)
    assert "__interrupt__" in out, f"cancel thread 未在 planning 暂停：keys={list(out.keys())}"
    return db_path, thread_id


# ============== TC-E2E-C1-01：真实链路跑到 planning 自然暂停（含 interrupt payload 契约） ==============


@skip_if_no_creds
def test_e2e_c1_01_natural_pause_at_planning(approve_thread, llm_config):
    """TC-E2E-C1-01：真实 4 节点链路跑到 planning **自然暂停**（无 interrupt_before/after）。

    断言：
    1. 新 SqliteSaver 实例回读 -> snapshot.next == ("planning",)（暂停在 planning）；
    2. interrupt 元数据存在（snapshot.tasks 含 interrupts）；
    3. interrupt payload 契约：含 reproduction_plan（plan_summary/code_strategy 非空）、
       revise_count=0、soft_hint_threshold、max_total_llm_calls；
    4. 上游真节点已真实填充：paper_meta / paper_analysis / resource_info 非 None。
    """
    db_path, thread_id = approve_thread
    conn, saver = _make_wal_saver(db_path)
    try:
        graph = build_graph(checkpointer=saver)
        config = {"configurable": {"thread_id": thread_id}}
        snap = graph.get_state(config)

        # 1) 暂停在 planning
        assert snap is not None
        assert snap.next == ("planning",), f"未暂停在 planning：next={snap.next}"

        # 2) interrupt 元数据存在（snapshot.tasks 至少一个 task 带 interrupts）
        interrupts = [
            iv for task in (snap.tasks or [])
            for iv in (getattr(task, "interrupts", None) or [])
        ]
        assert interrupts, f"snapshot.tasks 无 interrupt 元数据：tasks={snap.tasks}"

        # 3) interrupt payload 契约
        payload = interrupts[0].value
        assert isinstance(payload, dict), f"interrupt payload 非 dict：{type(payload)}"
        plan = payload.get("reproduction_plan")
        assert isinstance(plan, dict) and plan.get("plan_summary"), (
            f"interrupt payload.reproduction_plan 缺 plan_summary：{plan}"
        )
        assert plan.get("code_strategy"), f"reproduction_plan 缺 code_strategy：{plan}"
        assert payload.get("revise_count") == 0, (
            f"首次暂停 revise_count 应为 0：{payload.get('revise_count')}"
        )
        from config import MAX_TOTAL_LLM_CALLS

        assert payload.get("soft_hint_threshold") == 5
        assert payload.get("max_total_llm_calls") == MAX_TOTAL_LLM_CALLS

        # 4) 上游真节点已填充
        vals = snap.values
        pm = vals.get("paper_meta")
        assert pm and pm.get("arxiv_id") == PAPER_ARXIV_ID, f"paper_meta 未填充：{pm}"
        pa = vals.get("paper_analysis")
        assert pa and pa.get("method_summary"), f"paper_analysis 未填充：{pa}"
        # resource_scout 真节点已写 resource_info（即便 from_scratch 也非 None）
        assert vals.get("resource_info") is not None, "resource_info 未被 resource_scout 填充"
        assert vals.get("current_step") == "resource_scout", (
            f"暂停点 current_step 应为最后一个写入的真节点 resource_scout，"
            f"实际 {vals.get('current_step')!r}（planning 尚未写完）"
        )

        # 顺路：上游真节点不应留下 permanent NodeError（成功主路径）
        permanent = [
            e for e in (vals.get("node_errors") or [])
            if e.get("error_type") == "permanent"
        ]
        assert not permanent, f"成功主路径不应有 permanent NodeError：{permanent}"

        # state_history 至少 MIN_HISTORY_FRAMES_AT_PAUSE 帧（防退化为仅末帧）
        history = list(graph.get_state_history(config))
        assert len(history) >= MIN_HISTORY_FRAMES_AT_PAUSE, (
            f"暂停态 state_history 帧数 {len(history)} 过少"
        )
    finally:
        try:
            conn.close()
        except Exception:
            pass


# ============== TC-E2E-C1-02：revise→self-loop + approve→coding→END（含 SqliteSaver 跨实例回读） ==============


@skip_if_no_creds
def test_e2e_c1_02_revise_self_loop_then_approve_to_end(approve_thread, llm_config):
    """TC-E2E-C1-02：从暂停态 revise（self-loop 重入 planning 再暂停）-> approve -> END。

    覆盖 3 路决策中的 self（revise）与 next（approve）两条，并验证：
    1. revise resume -> 又暂停在 planning（self-loop 生效），_planning_revise_count 递增到 1，
       _planning_user_feedback 落地；
    2. approve resume -> 路由到 coding（next）-> ... -> END（snap.next == ()），
       reproduction_plan.approved == True；
    3. 全程经真实 SqliteSaver 落盘，每步用新 SqliteSaver 实例回读（跨实例持久化）。

    注意：本用例与 approve_thread fixture 共享同一 thread/db；按 module 内顺序，必须先于
    任何把该 thread 推进到 END 的用例运行——故 cancel 用例使用独立 thread，互不干扰。
    """
    db_path, thread_id = approve_thread
    config = {"configurable": {"thread_id": thread_id}}

    # --- 步骤 1：revise（self-loop）。每步新 SqliteSaver 实例，验证跨实例恢复 ---
    feedback = "请补充数据集下载与环境依赖版本细节"
    conn1, saver1 = _make_wal_saver(db_path)
    try:
        g1 = build_graph(checkpointer=saver1)
        out1 = g1.invoke(
            Command(resume={"decision": "revise", "user_feedback": feedback}),
            config,
        )
        try:
            conn1.commit()
        except Exception:
            pass
        # revise -> self-loop -> 再次暂停在 planning
        assert "__interrupt__" in out1, f"revise 后未再次暂停：keys={list(out1.keys())}"
        snap1 = g1.get_state(config)
        assert snap1.next == ("planning",), f"revise 后未重入 planning：next={snap1.next}"
        assert snap1.values.get("_planning_revise_count") == 1, (
            f"revise_count 未递增到 1：{snap1.values.get('_planning_revise_count')}"
        )
        assert snap1.values.get("_planning_user_feedback") == feedback, (
            f"user_feedback 未落地：{snap1.values.get('_planning_user_feedback')!r}"
        )
    finally:
        try:
            conn1.close()
        except Exception:
            pass

    # --- 步骤 2：approve（next）-> coding -> ... -> END。全新 SqliteSaver 实例 ---
    conn2, saver2 = _make_wal_saver(db_path)
    try:
        g2 = build_graph(checkpointer=saver2)
        out2 = g2.invoke(Command(resume={"decision": "approve"}), config)
        try:
            conn2.commit()
        except Exception:
            pass
        snap2 = g2.get_state(config)
        # approve -> next -> coding -> execution -> reporting -> END
        assert snap2.next == (), f"approve 后未跑到 END：next={snap2.next}"
        plan = snap2.values.get("reproduction_plan") or {}
        assert plan.get("approved") is True, f"approve 后 plan.approved 非 True：{plan.get('approved')}"
        # invoke 返回值同样应反映 approved
        assert (out2.get("reproduction_plan") or {}).get("approved") is True
        # revise 阶段写入的 user_feedback 仍持久化在 state
        assert snap2.values.get("_planning_revise_count") == 1
    finally:
        try:
            conn2.close()
        except Exception:
            pass

    # --- 步骤 3：全新 SqliteSaver 实例仅回读，确认 END 态从 SQLite 完整持久化 ---
    conn3, saver3 = _make_wal_saver(db_path)
    try:
        g3 = build_graph(checkpointer=saver3)
        reloaded = g3.get_state(config)
        assert reloaded.next == (), "新实例回读：thread 未处于 END 态"
        rplan = reloaded.values.get("reproduction_plan") or {}
        assert rplan.get("approved") is True, "新实例回读：approved 持久化丢失"
        # 上游真节点产物在 END 态仍完整（占位节点透明）
        assert reloaded.values.get("paper_meta", {}).get("arxiv_id") == PAPER_ARXIV_ID
        assert reloaded.values.get("paper_analysis", {}).get("method_summary")
    finally:
        try:
            conn3.close()
        except Exception:
            pass


# ============== TC-E2E-C1-03：cancel→END（不进 coding，保留 checkpoint） ==============


@skip_if_no_creds
def test_e2e_c1_03_cancel_routes_to_end(cancel_thread, llm_config):
    """TC-E2E-C1-03：从暂停态 cancel -> 路由到 END（不进 coding），独立 thread。

    覆盖 3 路决策中的 end（cancel）路径，并验证：
    1. cancel resume -> snap.next == ()（到 END）；
    2. current_step == "cancelled_by_user"（路由依据，AC-S2-13）；
    3. analysis_notes 含 [CANCELLED] 标记，且经真实 SqliteSaver 落盘后新实例可回读
       （analysis_notes 通道修复 + 持久化的真实链路实证，对应 c1 单测同名 mock 用例）；
    4. reproduction_plan.approved 不为 True（cancel 不批准）。
    """
    db_path, thread_id = cancel_thread
    config = {"configurable": {"thread_id": thread_id}}

    conn, saver = _make_wal_saver(db_path)
    try:
        graph = build_graph(checkpointer=saver)
        out = graph.invoke(Command(resume={"decision": "cancel"}), config)
        try:
            conn.commit()
        except Exception:
            pass
        snap = graph.get_state(config)
        assert snap.next == (), f"cancel 后未到 END：next={snap.next}"
        assert snap.values.get("current_step") == "cancelled_by_user", (
            f"cancel 后 current_step 应为 cancelled_by_user：{snap.values.get('current_step')!r}"
        )
        assert "[CANCELLED]" in (snap.values.get("analysis_notes") or ""), (
            f"cancel 后 analysis_notes 缺 [CANCELLED] 标记：{snap.values.get('analysis_notes')!r}"
        )
        assert "[CANCELLED]" in (out.get("analysis_notes") or ""), "invoke 返回值缺 [CANCELLED]"
        # cancel 不批准
        assert (snap.values.get("reproduction_plan") or {}).get("approved") is not True
    finally:
        try:
            conn.close()
        except Exception:
            pass

    # 新 SqliteSaver 实例回读：[CANCELLED] + END 态持久化
    conn2, saver2 = _make_wal_saver(db_path)
    try:
        g2 = build_graph(checkpointer=saver2)
        reloaded = g2.get_state(config)
        assert reloaded.next == (), "新实例回读：cancel thread 未处于 END 态"
        assert "[CANCELLED]" in (reloaded.values.get("analysis_notes") or ""), (
            "新实例回读：analysis_notes [CANCELLED] 持久化丢失（通道/持久化失效）"
        )
        assert reloaded.values.get("current_step") == "cancelled_by_user"
    finally:
        try:
            conn2.close()
        except Exception:
            pass
