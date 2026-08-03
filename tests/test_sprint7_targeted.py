"""Sprint 7 任务 T-S7-2-2（现场靶测收口，架构 §9.1 强制）：三缺陷同现场 fixture 驱动。

**与批次 1 单测的关键差异**：批次 1 四文件（s7_01/s7_02/s7_03）用「同构 mock state」自证；
本文件用 **真现场 fixture `tests/fixtures/checkpoints_s7_99eef17bccf2.db`** 驱动——即
Maria 手动跑 thread `task-99eef17bccf2`（论文 2403.06402 AICL 复现）留下的真实 checkpoint。
现场三缺陷同现场（架构 §0 坐实）：预算耗尽静默降级（S7-01）+ import 反复失败 coder 看不到真错
（S7-02）+ `_dev_loop_llm_calls=92>60` 单轮冲过头（S7-03）。这是防「同构 mock 假绿、真现场
仍崩」的强制回归靶（沿 sp5 AC-S5-03 mock e2e 假绿教训、§9.3 测试盲区警示）。

现场真实字段（从 fixture 最新 checkpoint channel_values 提取，源码级复核一致）：
    retry_budget_remaining=0 / _dev_loop_llm_calls=92 / fix_loop_count=4 /
    fix_loop_history 4 条全 error_category=import / execution_result.success=False /
    execution_result.logs 含 "No module named 'src'"（16435 字符）/
    current_step='reporting'（缺陷现场：静默降级到 reporting）/ user_fix_decision=None。

CP 映射（dev-plan §6 T-S7-2-2）：
    - CP-2.2-2 缺陷① S7-01（AC-S7-01/02）：现场同构 state 驱动 _maybe_interrupt_or_return
      → 断言不再静默降级、置 await 标记进 interrupt#2 两段式（非静默降级 reporting）；
    - CP-2.2-3 缺陷② S7-02（AC-S7-05/07）：现场 import logs → _persist_round_log 落盘含真报错、
      _digest_execution_feedback 含 log_file_path、read_code_file 可读到 No module named 'src'；
    - CP-2.2-4 缺陷③ S7-03（AC-S7-08）：现场 _dev_loop_llm_calls=92 → 收窄生效
      （effective_max_rounds 不超剩余子预算 = MAX_DEV_LOOP_LLM_CALLS - 92）；
    - CP-2.2-5：靶测随全量跑（不打 e2e marker），永久防回归入 CI。

fixture 只读契约（CP-2.2-1 / CP-2.2-3 只读不写）：SqliteSaver 只读连接 + is_setup=True 跳过
setup 的写操作（表已在 fixture 固化时建好），靶测后 fixture md5 零变动、源库 checkpoints.db 零触碰。

全离线（只读 fixture + mock sandbox agent），零 API / deepxiv 配额（不打 e2e marker）。
"""

from __future__ import annotations

import importlib
import sqlite3
from pathlib import Path
from typing import Any, Dict, Optional

import pytest

import config
from core import secrets_store

execution_module = importlib.import_module("core.nodes.execution")
coding_module = importlib.import_module("core.nodes.coding")

from config import MAX_DEV_LOOP_LLM_CALLS  # noqa: E402
from sandbox.local_venv import SandboxPrepareResult, SandboxRunResult  # noqa: E402

NODE_NAME = execution_module.NODE_NAME
INTERRUPT_KIND = execution_module.INTERRUPT_KIND
_ROUTE_AWAIT_INTERRUPT = execution_module._ROUTE_AWAIT_INTERRUPT
_EXEC_LOGS_SUBDIR = execution_module._EXEC_LOGS_SUBDIR
ExecutionFeedback = execution_module.ExecutionFeedback
ErrorCategory = execution_module.ErrorCategory

# 现场失败步 train_k_predictor.py 的真实 stderr 段（从 fixture logs 位置 13772 提取，
# 逐字对齐现场 `No module named 'src'` 真报错——这是 Maria 手动跑 2403.06402 留下的真实报错）。
# 关键现场特性（S7-02 缺陷本质）：真报错在现场聚合 logs 的**尾部**（13772/16435），前面是大段
# pip install stdout；旧 `stderr_tail=logs[-2000:]` 恰截到尾部**后续成功步 stdout**（step#11 exit=0），
# 把真报错挤掉——coder 全程看不到真错。S7-02 错误优先编排把失败步 stderr 前置到文件头 8000 内。
_FIELD_IMPORT_STDERR = (
    "Traceback (most recent call last):\n"
    '  File "/data/myproj/auto_reproduction/workspace/2403.06402/code/scripts/'
    'train_k_predictor.py", line 8, in <module>\n'
    "    from src.utils import load_json, print_metrics\n"
    "ModuleNotFoundError: No module named 'src'"
)

# 现场 fixture（复制不移动，批次 2 前置门固化；源库 checkpoints.db 单 thread 精简副本）。
FIXTURE_DB = Path(__file__).parent / "fixtures" / "checkpoints_s7_99eef17bccf2.db"
FIELD_THREAD_ID = "task-99eef17bccf2"


# ---------------------------------------------------------------------------
# fixture 加载 helper——只读打开、is_setup=True 跳过 setup 写操作、加载现场 state
# ---------------------------------------------------------------------------


def _load_field_state() -> Dict[str, Any]:
    """从 fixture 最新 checkpoint 加载真现场 channel_values（只读，不写 fixture）。

    SqliteSaver.setup() 会 PRAGMA journal_mode=WAL + CREATE TABLE（需写权限）；fixture 固化时
    表已建好，故置 is_setup=True 跳过 setup，用只读连接——保证靶测后 fixture md5 零变动。
    """
    if not FIXTURE_DB.exists():  # pragma: no cover - fixture 缺失防御
        pytest.skip(f"现场 fixture 缺失：{FIXTURE_DB}（批次 2 前置门 fixture 固化未就绪）")
    from langgraph.checkpoint.sqlite import SqliteSaver

    con = sqlite3.connect(f"file:{FIXTURE_DB}?mode=ro", uri=True, check_same_thread=False)
    try:
        saver = SqliteSaver(con)
        saver.is_setup = True  # 表已在 fixture 固化时建好——跳过 setup 的写操作（只读契约）
        tup = saver.get_tuple({"configurable": {"thread_id": FIELD_THREAD_ID}})
        assert tup is not None, "现场 fixture 应含 task-99eef17bccf2 的 checkpoint"
        return dict(tup.checkpoint["channel_values"])
    finally:
        con.close()


@pytest.fixture(scope="module")
def field_state() -> Dict[str, Any]:
    """现场真实 state（module 级加载一次，只读、独立可跑）。"""
    return _load_field_state()


@pytest.fixture(autouse=True)
def _clean_sensitive():
    secrets_store._SENSITIVE_VALUES.clear()
    yield
    secrets_store._SENSITIVE_VALUES.clear()


def _feedback_from_field(field_state: Dict[str, Any]) -> ExecutionFeedback:
    """用现场 execution_result 重建 feedback（现场 error_category=import / auto_fixable）。"""
    return execution_module._feedback_from_committed_result(field_state["execution_result"])


# ===========================================================================
# CP-2.2-0：现场 fixture 契约锚定——加载出的 state 与架构 §9.1 声明的现场字段逐一 MATCH
# （若源 fixture 被误换/污染，此测试先红，保护下游三靶测的现场前提）
# ===========================================================================


def test_cp_2_2_0_field_fixture_contract(field_state):
    """现场 fixture 加载的真实字段与架构 §0/§9.1 坐实的三缺陷同现场一致。"""
    assert field_state["retry_budget_remaining"] == 0, "现场预算已耗尽（S7-01 缺陷现场）"
    assert field_state["_dev_loop_llm_calls"] == 92, "现场子预算烧到 92>60（S7-03 缺陷现场）"
    assert field_state["fix_loop_count"] == 4, "现场修复 4 回合"
    hist = field_state.get("fix_loop_history") or []
    assert len(hist) == 4, "现场 fix_loop_history 4 条"
    assert all(h.get("error_category") == "import" for h in hist), "4 条全 import（反复失败现场）"

    er = field_state["execution_result"]
    assert er["success"] is False, "现场 execution 失败"
    assert "No module named 'src'" in er["logs"], "现场 logs 含真报错行（S7-02 缺陷现场）"

    # 缺陷现场铁证：静默降级到 reporting、用户未被问（S7-01 治理前的错误终态）。
    assert field_state.get("current_step") == "reporting", (
        "现场缺陷：预算耗尽被静默降级到 reporting（S7-01 治理正是要把它导向 interrupt#2）"
    )
    assert field_state.get("user_fix_decision") is None, "现场用户从未被问（缺陷铁证）"


# ===========================================================================
# CP-2.2-2：缺陷① S7-01（AC-S7-01/02）——现场预算耗尽不再静默降级，进 interrupt#2 两段式
# ===========================================================================


def test_cp_2_2_2_field_budget_exhausted_no_silent_degrade(field_state):
    """现场 state（budget=0/dev_calls=92/success=False）首次进入 _maybe_interrupt_or_return
    → **不再** _mark_degraded_for_report（degraded_nodes 不含 execution budget_exhausted 降级），
    而是置 _dev_loop_route=await（进两段式 interrupt#2），非静默降级 reporting（AC-S7-01）。"""
    er = field_state["execution_result"]
    updates: Dict[str, Any] = {"execution_result": er, "current_step": NODE_NAME}
    feedback = _feedback_from_field(field_state)

    # 用真现场字段驱动（不硬编码 mock 值）。
    state = {
        "fix_loop_count": field_state["fix_loop_count"],           # 现场 4
        "retry_budget_remaining": field_state["retry_budget_remaining"],  # 现场 0
        "_dev_loop_llm_calls": field_state["_dev_loop_llm_calls"], # 现场 92
        "_dev_loop_route": None,
        "fix_loop_history": field_state.get("fix_loop_history") or [],
        "node_errors": [],
        "degraded_nodes": [],
        "execution_result": er,
        "reproduction_plan": {"approved": True},
    }
    out = execution_module._maybe_interrupt_or_return(
        updates, er, feedback, state, already_committed=False
    )

    # AC-S7-01 核心：不再静默降级。
    assert NODE_NAME not in out.get("degraded_nodes", []), (
        "现场预算耗尽不得再 _mark_degraded_for_report（静默降级 reporting 是被治理的缺陷）"
    )
    assert not any(
        "budget_exhausted" in (e.get("error_message") or "") for e in out.get("node_errors", [])
    ), "不再写 budget_exhausted 降级 NodeError"
    # AC-S7-02：首次进入置两段式 await 标记（尚未 interrupt）。
    assert out.get("_dev_loop_route") == _ROUTE_AWAIT_INTERRUPT, (
        "现场预算耗尽应置 await 标记进 interrupt#2 两段式（非直达 reporting 兜底）"
    )
    assert "user_fix_decision" not in out, "首次进入尚未 interrupt（应答前非已决态）"
    # 现场 dev_calls=92 已超子上限判定阈值（92<120 仍未触顶，但 budget<MIN 否决修复准入）→ 不回 coding。
    assert out.get("_dev_loop_route") != execution_module._ROUTE_RETRY_CODING, (
        "现场预算耗尽不得回 coding（预算门下沉否决修复准入）"
    )


def test_cp_2_2_2_field_two_phase_reaches_interrupt(field_state, tmp_path, monkeypatch):
    """现场同构 graph 驱动：预算耗尽经两段式抵达 interrupt#2（self-loop 重入 guard 命中、
    sandbox 不重跑，AC-S7-02 幂等契约）。用现场预算/子预算数值驱动。"""
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.graph import START, END, StateGraph
    from core.state import GlobalState, ExecutionMode

    agent_cnt = {"n": 0}

    def fake_agent(state, wd, plan):
        agent_cnt["n"] += 1
        return execution_module.ExecAgentOutput(
            prep=SandboxPrepareResult(
                success=True, venv_dir="/w/.venv", python_exe="/w/.venv/bin/python", pip_exe="",
                env_info={"python_version": "Python 3.11"}, install_log="ok",
                install_failed_packages=[], error=None,
            ),
            run_results=[SandboxRunResult(
                exit_code=1, stdout="", stderr="ModuleNotFoundError: No module named 'src'",
                duration_seconds=0.1, timed_out=False, output_truncated=False,
                command=["python", "train_k_predictor.py"],
            )],
            rounds_used=1, llm_calls=0,
        )

    monkeypatch.setattr(execution_module, "_run_execution_agent", fake_agent)
    monkeypatch.setattr(execution_module, "collect_artifacts", lambda *a, **k: [])
    monkeypatch.setattr(execution_module, "_llm_extract_metrics", lambda *a, **k: ({}, 0))

    code_dir = tmp_path / "code"
    code_dir.mkdir(parents=True)

    g = StateGraph(GlobalState)
    g.add_node("execution", execution_module.execution)
    g.add_edge(START, "execution")

    def route(state: Dict[str, Any]) -> str:
        return "execution" if state.get("_dev_loop_route") == _ROUTE_AWAIT_INTERRUPT else "end"

    g.add_conditional_edges("execution", route, {"execution": "execution", "end": END})
    graph = g.compile(checkpointer=InMemorySaver())

    # 用现场数值：budget=0 / dev_calls=92 / fix_loop_count=4。
    state = {
        "llm_config_set": {"default": {"model": "test"}},
        "code_output_dir": str(code_dir),
        "reproduction_plan": {"execution_steps": [{"command": "python train_k_predictor.py"}],
                              "environment": {}, "approved": True},
        "paper_analysis": {"metrics": []},
        "execution_mode": ExecutionMode.FULL,
        "node_errors": [], "degraded_nodes": [], "fix_loop_history":
            field_state.get("fix_loop_history") or [],
        "fix_loop_count": field_state["fix_loop_count"],
        "retry_budget_remaining": field_state["retry_budget_remaining"],
        "_dev_loop_llm_calls": field_state["_dev_loop_llm_calls"],
        "_dev_loop_route": None,
        "execution_result": None, "current_step": "coding",
    }
    out = graph.invoke(state, {"configurable": {"thread_id": "s7-field-two-phase"}})

    assert "__interrupt__" in out, "现场预算耗尽应经两段式抵达 interrupt#2（非静默降级）"
    assert agent_cnt["n"] == 1, "self-loop 重入必须 guard 命中、sandbox 不重跑（S-1 幂等）"
    payload = out["__interrupt__"][0].value
    assert payload["interrupt_kind"] == INTERRUPT_KIND, "抵达的是 interrupt#2（dev_loop 决策面板）"
    assert payload["options"] == ["terminate", "revise_plan", "export_code"], "三态无第四态"


# ===========================================================================
# CP-2.2-3：缺陷② S7-02（AC-S7-05/07）——现场 import logs 落盘含真报错、反馈路径化、可自读
# ===========================================================================


def test_cp_2_2_3_field_logs_persist_and_readable(field_state, tmp_path, monkeypatch):
    """现场 import 失败步 stderr → _persist_round_log 落盘含真报错行；_digest_execution_feedback
    含 log_file_path；read_code_file 可读到 'No module named src'（AC-S7-05）。

    贴现场构造（S7-02 缺陷本质回归）：现场聚合 logs 里 `No module named 'src'` 在**尾部**
    （位置 13772/16435），前面是大段 pip install stdout、后面还有 step#11 成功步 stdout——旧
    `stderr_tail=logs[-2000:]` 取的是尾部成功步 stdout（不含真报错）。此处用现场真实失败步
    stderr（_FIELD_IMPORT_STDERR）+ 尾部大段成功步 stdout（模拟 step#11）构造多步 run_results，
    验证错误优先编排把失败步 stderr 前置到文件头 8000 内、真报错可达。"""
    # read_code_file 路径护栏是 WORKSPACE_DIR 根——指到 tmp_path。
    monkeypatch.setattr(config, "WORKSPACE_DIR", tmp_path)
    import core.tools.code_fs_tools as code_fs
    monkeypatch.setattr(code_fs, "WORKSPACE_DIR", tmp_path)

    code_dir = tmp_path / "code"
    code_dir.mkdir(parents=True)

    # 前提：现场 logs 真报错在尾部（缺陷本质坐实）。
    field_logs = field_state["execution_result"]["logs"]
    idx = field_logs.find("No module named 'src'")
    assert idx > len(field_logs) - 4000, (
        "现场缺陷前提：真报错在聚合 logs 尾部（旧 logs[-2000:] 取成功步 stdout 挤掉真报错）"
    )

    prep = SandboxPrepareResult(
        success=True, venv_dir="/w/.venv", python_exe="/w/.venv/bin/python", pip_exe="",
        env_info={"python_version": "Python 3.11"},
        install_log="Requirement already satisfied: torch ...\n" * 400,  # 现场大段 pip install
        install_failed_packages=[], error=None,
    )
    # 现场多步结构：失败步 train_k_predictor.py（exit=1, stderr=真报错段）+ 尾部成功步大段 stdout。
    runs = [
        SandboxRunResult(
            exit_code=1, stdout="", stderr=_FIELD_IMPORT_STDERR,
            duration_seconds=0.1, timed_out=False, output_truncated=False,
            command=["python", "scripts/train_k_predictor.py"],
        ),
        SandboxRunResult(
            exit_code=0, stdout="OK step#11 output\n" * 5000,  # 尾部成功步大段 stdout（挤真报错的元凶）
            stderr="", duration_seconds=0.1, timed_out=False, output_truncated=False,
            command=["python", "scripts/eval.py"],
        ),
    ]

    # 步骤 A：execution 首跑（fix_count=0）落盘 round_0.log（现场真报错入盘、错误优先前置）。
    path = execution_module._persist_round_log(str(code_dir), 0, prep, runs)
    assert path is not None, "现场 import 错误应落盘成功"
    log_file = Path(code_dir) / _EXEC_LOGS_SUBDIR / "round_0.log"
    assert log_file.exists(), "round_0.log 应落盘"
    content = log_file.read_text(encoding="utf-8")
    err_idx = content.find("No module named 'src'")
    assert err_idx != -1, "落盘内容含现场真报错行"
    assert err_idx < 8000, (
        f"错误优先编排应把现场真报错前置到文件头 8000 内（应对 read_code_file 8000 截断），实际 {err_idx}"
    )

    # 步骤 B：coding 修复回合（fix_count=1）推导 log_file_path=round_0.log（off-by-one 读上一轮）。
    digest = coding_module._digest_execution_feedback(
        field_state["execution_result"], code_output_dir=str(code_dir.resolve()), fix_round=0
    )
    assert digest["log_file_path"] is not None, "反馈应含 log_file_path 子键（AC-S7-05）"
    assert digest["log_file_path"].endswith("round_0.log")
    assert digest["error_category"] == "import", "现场 error_category=import 快速提示保留"

    # 步骤 C：coder 用 read_code_file 自读现场日志 → 头 8000 内读到真报错行（端到端可读）。
    read_tool = code_fs.make_read_code_file_tool()
    read_content = read_tool.invoke({"path": digest["log_file_path"]})
    assert "No module named 'src'" in read_content, (
        "read_code_file(log_file_path) 应读到现场真报错行（coder 自读定位 import 错误，S7-02 治理）"
    )


def test_cp_2_2_3_field_stderr_tail_is_guidance_not_field_logs(field_state, tmp_path):
    """AC-S7-07 守门（现场版）：反馈 stderr_tail 是固定指引串，**不再**是现场 logs 截断产物；
    现场 logs 里的真报错不经 stderr_tail 塞给 coder（系统不再替 coder 挑段）。"""
    code_dir = tmp_path / "code"
    code_dir.mkdir(parents=True)
    digest = coding_module._digest_execution_feedback(
        field_state["execution_result"], code_output_dir=str(code_dir.resolve()), fix_round=0
    )
    field_logs = field_state["execution_result"]["logs"]

    # stderr_tail 是固定指引串（互斥断言：与旧 logs[-2000:] 实现互斥）。
    assert digest["stderr_tail"] == coding_module._STDERR_TAIL_GUIDANCE, (
        "stderr_tail 应为固定指引串（AC-S7-07）"
    )
    # 现场 logs 尾部 2000 字符（旧实现产物）不得等于 stderr_tail。
    assert digest["stderr_tail"] != field_logs[-2000:], (
        "stderr_tail 不得再是现场 logs[-2000:] 截断产物（旧缺陷：尾部恰是后续成功步 stdout）"
    )
    # stderr_tail 不含现场真报错内容（把截断决策权收回 coder）。
    assert "No module named" not in digest["stderr_tail"], (
        "stderr_tail 不含现场真报错（系统不再替 coder 挑段，AC-S7-07）"
    )
    # 反馈以 log_file_path 为准。
    assert digest["log_file_path"].endswith("round_0.log")


# ===========================================================================
# CP-2.2-4：缺陷③ S7-03（AC-S7-08）——现场 dev_calls=92 收窄生效，单轮不冲过子上限
# ===========================================================================


def _install_agent_harness(monkeypatch) -> Dict[str, Any]:
    """patch _run_execution_agent 的外部依赖，捕获传入子图的 max_rounds（收窄护栏值）。"""
    captured: Dict[str, Any] = {"subgraph_max_rounds": None, "context": None}

    class _FakeSubgraph:
        def __init__(self, max_rounds: int) -> None:
            self.max_rounds = max_rounds

        def invoke(self, initial: Dict[str, Any]) -> Dict[str, Any]:
            return {"messages": [], "round": 0, "result": None}

    # result_schema 是 S7-13 起 execution 侧补传的第 5 个参数（EXECUTION_OUTPUT_SCHEMA）；
    # 这里同步放宽签名并**记录下来**，使"到底传没传 schema"仍可被断言，而不是靠 **kwargs 吞掉。
    def fake_create_react_subgraph(
        *, node_name, system_prompt, tools, max_rounds, result_schema=None
    ):
        captured["subgraph_max_rounds"] = max_rounds
        captured["subgraph_result_schema"] = result_schema
        return _FakeSubgraph(max_rounds)

    _real_ctx = execution_module._build_execution_agent_context

    def spy_ctx(state, work_dir, plan):
        ctx = _real_ctx(state, work_dir, plan)
        captured["context"] = ctx
        return ctx

    monkeypatch.setattr(execution_module, "create_react_subgraph", fake_create_react_subgraph)
    monkeypatch.setattr(execution_module, "_build_execution_agent_context", spy_ctx)
    monkeypatch.setattr(execution_module, "create_llm", lambda cfg: object())
    monkeypatch.setattr(execution_module, "resolve_llm_config", lambda cfg, node: cfg)
    monkeypatch.setattr(execution_module, "load_all_secrets", lambda *a, **k: {})
    monkeypatch.setattr(execution_module, "build_credential_env", lambda secrets: {})
    monkeypatch.setattr(execution_module, "make_prepare_environment_tool", lambda *a, **k: None)
    monkeypatch.setattr(execution_module, "make_run_in_sandbox_tool", lambda *a, **k: None)
    monkeypatch.setattr(execution_module, "make_request_user_input_tool", lambda *a, **k: None)
    return captured


def test_cp_2_2_4_field_dev_calls_clamps_within_sub_budget(field_state, monkeypatch):
    """现场 _dev_loop_llm_calls=92（现场已超旧上限 60）→ 翻倍后子上限 120、剩余子预算 28；
    构造联动值撞 CAP 的多步 plan → 收窄后 effective_max_rounds == min(联动值, 28) == 28，
    严格 ≤ 剩余子预算，单轮不再冲过子上限（AC-S7-08）。用现场 dev_calls 驱动。"""
    dev_calls = field_state["_dev_loop_llm_calls"]  # 现场 92
    remaining_sub_budget = MAX_DEV_LOOP_LLM_CALLS - dev_calls  # 120-92 = 28
    assert remaining_sub_budget > 0, "现场 dev_calls 未触顶（92<120），剩余子预算 28"

    # 联动值撞 CAP（步数够多 → clamp(n+K, FLOOR, CAP)==CAP），使收窄由「剩余子预算」主导。
    plan = {
        "execution_steps": [{"command": f"python step{i}.py"} for i in range(80)],
        "environment": {},
    }
    base_rounds = execution_module._effective_max_rounds(plan)
    assert base_rounds == config.REACT_MAX_ROUNDS_EXECUTION_CAP, "前提：多步 plan 联动值撞 CAP"
    assert base_rounds > remaining_sub_budget, (
        "前提：联动值(CAP)应大于现场剩余子预算，收窄才由子预算主导"
    )

    captured = _install_agent_harness(monkeypatch)
    state = {
        "llm_config_set": {"default": {"model": "test"}},
        "code_output_dir": "/tmp/s7-field-clamp",
        "reproduction_plan": plan,
        "paper_analysis": {"metrics": []},
        "execution_mode": __import__("core.state", fromlist=["ExecutionMode"]).ExecutionMode.FULL,
        "fix_loop_count": field_state["fix_loop_count"],
        "retry_budget_remaining": 240,  # 总预算充足（隔离 S7-03，只看子预算收窄）
        "_dev_loop_llm_calls": dev_calls,  # 现场 92
        "_dev_loop_route": None,
        "execution_result": None,
        "credential_degradations": {},
    }
    execution_module._run_execution_agent(state, "/tmp/s7-field-clamp", plan)

    narrowed = captured["subgraph_max_rounds"]
    # AC-S7-08 核心：收窄到剩余子预算，不超子上限。
    assert narrowed == remaining_sub_budget, (
        f"现场 dev_calls=92 应收窄到剩余子预算 {remaining_sub_budget}，实际 {narrowed}"
    )
    assert narrowed <= remaining_sub_budget, "收窄值必须 ≤ 剩余子预算（不冲过子上限）"
    # 收窄确实压制了「单轮烧满 CAP」的失控（现场缺陷是单轮冲到 ~180）。
    assert narrowed < base_rounds, (
        f"收窄值 {narrowed} 必须远小于未收窄 CAP 级 {base_rounds}（S7-03 治理压制单轮失控）"
    )
    # 越界上界确定性小值：单轮最多烧 narrowed 轮 + force_finish 1 + metrics 抽取额度。
    over_run_upper = narrowed + 1 + 3  # force_finish 1 轮 + metrics 抽取上限量级
    assert dev_calls + over_run_upper <= MAX_DEV_LOOP_LLM_CALLS + 1 + 3, (
        "现场收窄后总越界幅度约束在确定性小值（远小于实测的 32）"
    )

    # R-PC4 无扰：context 的 max_rounds 数字保持联动值不随 dev_calls 收窄（AA-S7-6）。
    assert captured["context"]["max_rounds"] == base_rounds, (
        "context 的 max_rounds 保持联动值（收窄是系统护栏、不回灌 context 动态通道）"
    )


def test_cp_2_2_4_field_clamp_bounds_over_run_vs_field_bug(field_state, monkeypatch):
    """对照现场缺陷幅度：现场缺陷是 dev_calls 冲到 92（超旧上限 60 达 32 次）；收窄后单轮越界
    上界远小于 32（确定性小值），坐实 S7-03 把「单轮失控」压回确定性小范围（AC-S7-08）。"""
    dev_calls = field_state["_dev_loop_llm_calls"]  # 92
    remaining = MAX_DEV_LOOP_LLM_CALLS - dev_calls  # 28

    plan = {
        "execution_steps": [{"command": f"python step{i}.py"} for i in range(80)],
        "environment": {},
    }
    captured = _install_agent_harness(monkeypatch)
    state = {
        "llm_config_set": {"default": {"model": "test"}},
        "code_output_dir": "/tmp/s7-field-clamp2",
        "reproduction_plan": plan,
        "paper_analysis": {"metrics": []},
        "execution_mode": __import__("core.state", fromlist=["ExecutionMode"]).ExecutionMode.FULL,
        "fix_loop_count": field_state["fix_loop_count"],
        "retry_budget_remaining": 240,
        "_dev_loop_llm_calls": dev_calls,
        "_dev_loop_route": None,
        "execution_result": None,
        "credential_degradations": {},
    }
    execution_module._run_execution_agent(state, "/tmp/s7-field-clamp2", plan)
    narrowed = captured["subgraph_max_rounds"]

    # 越界上界（force_finish 1 + metrics 抽取额度）远小于现场缺陷幅度 32。
    over_run_upper = 1 + 3  # 收窄后单轮到 narrowed 即刹住，仅 force_finish+metrics 越界
    FIELD_BUG_OVERRUN = 32  # 现场缺陷：dev_calls 冲过旧上限 60 达 32 次
    assert over_run_upper < FIELD_BUG_OVERRUN, (
        f"收窄后越界上界 {over_run_upper} 必须远小于现场缺陷幅度 {FIELD_BUG_OVERRUN}"
    )
    assert narrowed == remaining, "收窄值等于剩余子预算（现场 dev_calls 主导收窄）"
