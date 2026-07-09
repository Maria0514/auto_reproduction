"""Sprint 5 T-S5-2-2：coding 前置 gate——复合节点 + interrupt#3 增量 + 降级标记（S5-01 主体）。

自测检查点（dev-plan T-S5-2-2）：
- CP-2.2-1 缺凭证 → interrupt payload 五键契约（四键 + allow_degrade=True）；
  `.secrets`/会话层命中 → 零 interrupt 静默通过（AC-S5-02 无静默绕过）；
  agent 工具路径 payload 永不含 allow_degrade（红线）
- CP-2.2-2 resume 四分支：记住落盘 / 不记住进会话层（不落盘）/ degrade →
  credential_degradations 落 state 且该 key 不再拦 / 非法 resume → WARNING 非静默
- CP-2.2-3 单项串行幂等：两缺失项 → 两次串行 interrupt、resume 值不串位、
  第二次重跑 missing 重算正确——连跑 3 次一致（真实 mini graph + InMemorySaver）
- CP-2.2-4 GraphBubbleUp 直通：AST 断言 gate/复合函数体无 try + mock 主图暂停实证
- CP-2.2-5 wrapper 复合零回归：graph.py 零改动（git status 空）、
  required_credentials==[] / 旧 checkpoint 无键 → gate 零开销直通、
  update 键集零扰动、元数据契约（__name__/__module__/签名）守门
- CP-2.2-6 gate 日志无 value 明文（caplog）；HumanMessage 含降级摘要且 sort_keys 幂等
- 附：run_command extra_env 装配点确认（每次节点执行重组 build_credential_env，
  会话层与 env: 规则自动生效——实现内容第 6 条的实证）

测试隔离纪律：tmp_path + monkeypatch 隔离 config.WORKSPACE_DIR 与
secrets_store 模块级 dict（_SESSION_SECRETS / _SENSITIVE_VALUES），绝不碰真实
`.secrets`；一律哨兵假值。

注意：core/nodes/__init__.py 显式 export 会让 callable 遮蔽子模块，统一用
importlib.import_module 访问模块属性（已知坑 #6）。
"""

from __future__ import annotations

import ast
import importlib
import inspect
import json
import logging
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, TypedDict

import pytest

import config
from core import secrets_store
from core.secrets_store import (
    load_all_secrets,
    lookup_secret,
    remember_secret,
    stash_session_secret,
)

coding_module = importlib.import_module("core.nodes.coding")
interaction_tools = importlib.import_module("core.tools.interaction_tools")

_REPO_ROOT = Path(__file__).resolve().parents[1]
_CODING_SRC = _REPO_ROOT / "core" / "nodes" / "coding.py"

# 哨兵假值（带可辨识后缀防误撞真值；泄漏断言用）。
_FAKE_VALUE_A = "t22-fake-cred-A-do-not-leak"
_FAKE_VALUE_B = "t22-fake-cred-B-do-not-leak"

_PK_A = "env:T22_FAKE_KEY_A"
_PK_B = "git_credential:t22.example.com"
_PURPOSE_A = "论文方法依赖真实 LLM 调用（T22 假场景）"
_PURPOSE_B = "克隆私有参考仓库（T22 假场景）"


# ---------------------------------------------------------------------------
# fixtures（隔离纪律）
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_module_level_state():
    """每条用例前后清空进程内 sensitive set 与会话覆盖层（防跨用例/跨文件污染）。"""
    secrets_store._SENSITIVE_VALUES.clear()
    secrets_store._SESSION_SECRETS.clear()
    yield
    secrets_store._SENSITIVE_VALUES.clear()
    secrets_store._SESSION_SECRETS.clear()


@pytest.fixture()
def secrets_workspace(tmp_path, monkeypatch):
    """config.WORKSPACE_DIR 指向 tmp 受控目录，绝不触碰真实 workspace/.secrets。"""
    ws = tmp_path / "workspace"
    ws.mkdir()
    monkeypatch.setattr(config, "WORKSPACE_DIR", ws)
    return ws


def _secrets_file(ws: Path) -> Path:
    return ws / config.SECRETS_FILE_NAME


def _make_state(
    required: Optional[List[Dict[str, str]]] = None,
    degradations: Optional[Dict[str, str]] = None,
    **overrides: Any,
) -> Dict[str, Any]:
    """最小 state：reproduction_plan.required_credentials + 可选降级标记。"""
    plan: Dict[str, Any] = {
        "code_strategy": "from scratch",
        "execution_steps": [{"name": "train", "command": "python run.py"}],
        "deliverables": ["run.py"],
        "environment": {"python": "3.11"},
    }
    if required is not None:
        plan["required_credentials"] = required
    state: Dict[str, Any] = {"reproduction_plan": plan}
    if degradations is not None:
        state["credential_degradations"] = degradations
    state.update(overrides)
    return state


class _InterruptRecorder:
    """mock interrupt：记录每次 payload，按剧本顺序返回 resume 值。

    剧本耗尽仍被调用 → AssertionError（等价"不应再 interrupt"守门）。
    """

    def __init__(self, resumes: List[Any]):
        self.calls: List[Dict[str, Any]] = []
        self._resumes = list(resumes)

    def __call__(self, payload: Any) -> Any:
        self.calls.append(payload)
        assert self._resumes, (
            f"剧本 resume 值耗尽仍发生第 {len(self.calls)} 次 interrupt: {payload!r}"
        )
        return self._resumes.pop(0)


@pytest.fixture()
def fake_interrupt(monkeypatch):
    """工厂：install(*resumes) → 把 coding 模块内 interrupt 符号换成 recorder。"""

    def install(*resumes: Any) -> _InterruptRecorder:
        rec = _InterruptRecorder(list(resumes))
        monkeypatch.setattr(coding_module, "interrupt", rec)
        return rec

    return install


class _ReactStub:
    """替身 ReAct wrapper：记录收到的 state 视图，返回固定 update。"""

    def __init__(self, update: Optional[Dict[str, Any]] = None):
        self.seen: List[Dict[str, Any]] = []
        self._update = update if update is not None else {"current_step": "coding"}

    def __call__(self, state: Dict[str, Any]) -> Dict[str, Any]:
        self.seen.append(dict(state))
        return dict(self._update)


# ===========================================================================
# CP-2.2-1 payload 五键契约 + 命中静默通过 + agent 路径无 allow_degrade（红线）
# ===========================================================================


def test_cp_2_2_1_missing_credential_payload_five_keys(secrets_workspace, fake_interrupt):
    """缺凭证 → 恰一次 interrupt，payload 恰五键（四键契约 + allow_degrade=True）。"""
    rec = fake_interrupt({"value": _FAKE_VALUE_A, "remember": False})
    state = _make_state(required=[{"purpose_key": _PK_A, "purpose": _PURPOSE_A}])

    deg = coding_module._credential_gate(state)

    assert len(rec.calls) == 1, "单缺失项必须恰好 interrupt 一次"
    payload = rec.calls[0]
    assert set(payload.keys()) == {
        "interrupt_kind", "question", "is_sensitive", "purpose_key", "allow_degrade",
    }, f"payload 必须恰五键，实为 {sorted(payload.keys())}"
    assert (
        payload["interrupt_kind"]
        == interaction_tools.INTERRUPT_KIND_USER_INPUT
        == "user_input_request"
    )
    assert payload["is_sensitive"] is True
    assert payload["purpose_key"] == _PK_A
    assert payload["allow_degrade"] is True
    assert _PK_A in payload["question"] and _PURPOSE_A in payload["question"]
    assert deg == {}, "正常提交路径不产生降级标记"


def test_cp_2_2_1_secrets_hit_zero_interrupt(secrets_workspace, fake_interrupt):
    """`.secrets` 命中 → 零 interrupt 静默通过（跨任务复用零打扰）。"""
    remember_secret(_PK_A, _FAKE_VALUE_A, is_sensitive=True)
    rec = fake_interrupt()  # 无剧本：任何调用都会炸
    deg = coding_module._credential_gate(
        _make_state(required=[{"purpose_key": _PK_A, "purpose": _PURPOSE_A}])
    )
    assert rec.calls == []
    assert deg == {}


def test_cp_2_2_1_session_hit_zero_interrupt(secrets_workspace, fake_interrupt):
    """会话覆盖层命中（「不记住」凭证）→ 零 interrupt 静默通过。

    T-S5-2-1 架构师裁决落地面：lookup_secret 同步感知会话层，gate 重跑
    命中即消失，不会死循环。
    """
    stash_session_secret(_PK_A, _FAKE_VALUE_A)
    rec = fake_interrupt()
    deg = coding_module._credential_gate(
        _make_state(required=[{"purpose_key": _PK_A, "purpose": _PURPOSE_A}])
    )
    assert rec.calls == []
    assert deg == {}


def test_cp_2_2_1_degraded_key_zero_interrupt(secrets_workspace, fake_interrupt):
    """已在 credential_degradations 的项 → 排除不再拦（interrupt 或降级标记
    二者必居其一，AC-S5-02：无第三条静默绕过路径）。"""
    rec = fake_interrupt()
    deg = coding_module._credential_gate(
        _make_state(
            required=[{"purpose_key": _PK_A, "purpose": _PURPOSE_A}],
            degradations={_PK_A: _PURPOSE_A},
        )
    )
    assert rec.calls == []
    assert deg == {_PK_A: _PURPOSE_A}, "既有降级标记原样保留（整 dict 回传）"


def test_cp_2_2_1_mixed_hit_and_missing_only_missing_asked(secrets_workspace, fake_interrupt):
    """两项声明、一项已命中 → 只对缺失项 interrupt。"""
    remember_secret(_PK_A, _FAKE_VALUE_A, is_sensitive=True)
    rec = fake_interrupt({"value": _FAKE_VALUE_B, "remember": False})
    coding_module._credential_gate(
        _make_state(required=[
            {"purpose_key": _PK_A, "purpose": _PURPOSE_A},
            {"purpose_key": _PK_B, "purpose": _PURPOSE_B},
        ])
    )
    assert len(rec.calls) == 1
    assert rec.calls[0]["purpose_key"] == _PK_B


def test_cp_2_2_1_agent_tool_payload_never_contains_allow_degrade(
    secrets_workspace, monkeypatch,
):
    """红线：agent 经 request_user_input 产生的 payload 永不含 allow_degrade
    （四键原样）——allow_degrade 只由 gate 设置，agent 无降级入口。"""
    rec = _InterruptRecorder([{"value": "t22-agent-path-value", "remember": False}])
    monkeypatch.setattr(interaction_tools, "interrupt", rec)
    interaction_tools.request_user_input.invoke({
        "question": "需要一个假凭证",
        "is_sensitive": True,
        "purpose_key": "env:T22_AGENT_PATH",
    })
    assert len(rec.calls) == 1
    payload = rec.calls[0]
    assert "allow_degrade" not in payload
    assert set(payload.keys()) == {
        "interrupt_kind", "question", "is_sensitive", "purpose_key",
    }


# ===========================================================================
# CP-2.2-2 resume 四分支
# ===========================================================================


def test_cp_2_2_2_remember_persists_to_secrets_file(
    secrets_workspace, fake_interrupt, monkeypatch,
):
    """提交且 remember=True → remember_secret 0600 落盘；会话层不写；重跑不再拦。"""
    fake_interrupt({"value": _FAKE_VALUE_A, "remember": True})
    state = _make_state(required=[{"purpose_key": _PK_A, "purpose": _PURPOSE_A}])
    deg = coding_module._credential_gate(state)

    assert deg == {}
    sf = _secrets_file(secrets_workspace)
    assert sf.exists(), "remember 分支必须落盘 .secrets"
    entries = json.loads(sf.read_text(encoding="utf-8"))
    assert entries[_PK_A] == {"value": _FAKE_VALUE_A, "is_sensitive": True}
    assert _PK_A not in secrets_store._SESSION_SECRETS, "remember 分支不写会话层"
    # 重跑重算 missing：.secrets 命中 → 零 interrupt。
    rec2 = _InterruptRecorder([])
    monkeypatch.setattr(coding_module, "interrupt", rec2)
    assert coding_module._credential_gate(state) == {}
    assert rec2.calls == []


def test_cp_2_2_2_no_remember_goes_to_session_layer_not_disk(
    secrets_workspace, fake_interrupt,
):
    """提交不 remember → stash_session_secret（不落盘）+ 敏感值登记；重跑不再拦。"""
    fake_interrupt({"value": _FAKE_VALUE_A, "remember": False})
    state = _make_state(required=[{"purpose_key": _PK_A, "purpose": _PURPOSE_A}])
    deg = coding_module._credential_gate(state)

    assert deg == {}
    assert secrets_store._SESSION_SECRETS.get(_PK_A) == _FAKE_VALUE_A
    assert not _secrets_file(secrets_workspace).exists(), "不记住绝不落盘"
    assert _FAKE_VALUE_A in set(secrets_store.iter_sensitive_values()), (
        "gate 收集的敏感值必须进 mask 集（脱敏地基）"
    )
    assert lookup_secret(_PK_A) == _FAKE_VALUE_A, "会话层命中 → gate 重跑不死循环"


def test_cp_2_2_2_degrade_lands_in_state_and_stops_blocking(
    secrets_workspace, fake_interrupt, monkeypatch,
):
    """degrade=True → credential_degradations[purpose_key]=purpose 整 dict 单点
    回写 state（复合节点 update）且该 key 不再拦。"""
    fake_interrupt({"value": "", "remember": False, "degrade": True})
    stub = _ReactStub()
    monkeypatch.setattr(coding_module, "_coding_react", stub)
    state = _make_state(required=[{"purpose_key": _PK_A, "purpose": _PURPOSE_A}])

    update = coding_module.coding(state)

    assert update["credential_degradations"] == {_PK_A: _PURPOSE_A}
    assert _PK_A not in secrets_store._SESSION_SECRETS
    assert not _secrets_file(secrets_workspace).exists()
    # ReAct 视图携带降级事实（HumanMessage 注入的数据源）。
    assert stub.seen and stub.seen[0].get("credential_degradations") == {_PK_A: _PURPOSE_A}

    # 该 key 不再拦：带降级标记的 state 重跑 → 零 interrupt。
    rec2 = _InterruptRecorder([])
    monkeypatch.setattr(coding_module, "interrupt", rec2)
    deg2 = coding_module._credential_gate(
        _make_state(
            required=[{"purpose_key": _PK_A, "purpose": _PURPOSE_A}],
            degradations=dict(update["credential_degradations"]),
        )
    )
    assert rec2.calls == []
    assert deg2 == {_PK_A: _PURPOSE_A}


@pytest.mark.parametrize(
    "bad_resume",
    [
        {"remember": True},                       # 缺 value
        {"value": None, "remember": False},       # value=None
        {"value": "   ", "remember": False},      # 空白 value
        "oops-not-a-dict",                        # 非 dict
        None,                                     # None
    ],
)
def test_cp_2_2_2_illegal_resume_warns_and_reasks_same_item(
    secrets_workspace, fake_interrupt, caplog, bad_resume,
):
    """非法 resume（缺 value 且非 degrade）→ WARNING 非静默 + 重新 interrupt 同一项。"""
    rec = fake_interrupt(bad_resume, {"value": _FAKE_VALUE_A, "remember": False})
    with caplog.at_level(logging.WARNING, logger="core.nodes.coding"):
        deg = coding_module._credential_gate(
            _make_state(required=[{"purpose_key": _PK_A, "purpose": _PURPOSE_A}])
        )
    assert len(rec.calls) == 2, "非法 resume 后必须对同一项再次 interrupt"
    assert rec.calls[0]["purpose_key"] == rec.calls[1]["purpose_key"] == _PK_A
    warnings = [
        r for r in caplog.records
        if r.levelno == logging.WARNING and "非法 resume" in r.getMessage()
    ]
    assert warnings, "非法 resume 必须打 WARNING（非静默）"
    assert deg == {}
    assert secrets_store._SESSION_SECRETS.get(_PK_A) == _FAKE_VALUE_A


# ===========================================================================
# CP-2.2-3 单项串行幂等（真实 mini graph，连跑 3 次一致）
# ===========================================================================


class _GateGraphState(TypedDict, total=False):
    reproduction_plan: Dict[str, Any]
    credential_degradations: Dict[str, str]
    current_step: str


def _build_gate_graph(react_stub):
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.graph import END, START, StateGraph

    builder = StateGraph(_GateGraphState)
    builder.add_node("coding", coding_module.coding)
    builder.add_edge(START, "coding")
    builder.add_edge("coding", END)
    return builder.compile(checkpointer=InMemorySaver())


def _two_missing_state() -> Dict[str, Any]:
    return _make_state(required=[
        {"purpose_key": _PK_A, "purpose": _PURPOSE_A},
        {"purpose_key": _PK_B, "purpose": _PURPOSE_B},
    ])


def test_cp_2_2_3_serial_interrupts_no_crosstalk_three_consecutive_runs(
    tmp_path, monkeypatch,
):
    """两缺失项 → 两次串行 interrupt；resume 值不串位；第二次重跑 missing 重算
    正确（第二次暂停问的是 B 而非 A）——连跑 3 次一致（interrupt 幂等类判据）。"""
    from langgraph.types import Command

    for run in range(3):
        # —— 每轮全新隔离：workspace / 会话层 / mask 集 / thread ——
        ws = tmp_path / f"workspace-{run}"
        ws.mkdir()
        monkeypatch.setattr(config, "WORKSPACE_DIR", ws)
        secrets_store._SESSION_SECRETS.clear()
        secrets_store._SENSITIVE_VALUES.clear()

        stub = _ReactStub()
        monkeypatch.setattr(coding_module, "_coding_react", stub)
        app = _build_gate_graph(stub)
        cfg = {"configurable": {"thread_id": f"t22-cp3-{run}"}}

        # 第 1 次执行：暂停在第一项 A。
        paused1 = app.invoke(_two_missing_state(), cfg)
        intr1 = paused1.get("__interrupt__")
        assert intr1, f"run#{run}: 首次执行必须暂停"
        assert intr1[0].value["purpose_key"] == _PK_A, (
            f"run#{run}: 第一次 interrupt 必须是声明序第一项"
        )
        assert intr1[0].value["allow_degrade"] is True
        assert stub.seen == [], f"run#{run}: gate 未放行前 ReAct 不得启动"

        # resume A（不记住）→ 节点重跑：重放 A、暂停在重算后的第一项 B。
        paused2 = app.invoke(
            Command(resume={"value": _FAKE_VALUE_A, "remember": False}), cfg,
        )
        intr2 = paused2.get("__interrupt__")
        assert intr2, f"run#{run}: 第二项缺失必须再次暂停"
        assert intr2[0].value["purpose_key"] == _PK_B, (
            f"run#{run}: 重跑后 missing 重算必须推进到 B（而非重复 A / 跳空）"
        )
        # 副作用后置的可观测面：暂停期间 A 尚未入会话层（防重放串位的机制本身）。
        assert _PK_A not in secrets_store._SESSION_SECRETS, (
            f"run#{run}: 副作用必须后置到快照收齐之后（幂等纪律①）"
        )

        # resume B（记住）→ gate 收齐放行，节点完成。
        final = app.invoke(
            Command(resume={"value": _FAKE_VALUE_B, "remember": True}), cfg,
        )
        assert "__interrupt__" not in final, f"run#{run}: 收齐后必须放行完成"

        # 不串位：A 的值在会话层、B 的值在 .secrets，各归各值。
        assert secrets_store._SESSION_SECRETS.get(_PK_A) == _FAKE_VALUE_A, (
            f"run#{run}: A 的 resume 值串位"
        )
        entries = json.loads(_secrets_file(ws).read_text(encoding="utf-8"))
        assert entries[_PK_B]["value"] == _FAKE_VALUE_B, f"run#{run}: B 的 resume 值串位"
        assert _PK_B not in secrets_store._SESSION_SECRETS
        assert _PK_A not in entries, f"run#{run}: A（不记住）绝不落盘"

        # 放行后 ReAct 恰执行一次；零降级路径 state 视图无降级标记。
        assert len(stub.seen) == 1, f"run#{run}: ReAct 必须恰好执行一次"
        assert not stub.seen[0].get("credential_degradations")
        assert final.get("current_step") == "coding"
        # 完成后 missing 重算为空（收齐生效）。
        assert coding_module._compute_missing_credentials(_two_missing_state(), {}) == []


def test_cp_2_2_3_degrade_second_item_lands_in_graph_state(tmp_path, monkeypatch):
    """串行两项：A 提交、B 显式降级 → 图终态 credential_degradations 恰含 B。"""
    from langgraph.types import Command

    ws = tmp_path / "workspace-deg"
    ws.mkdir()
    monkeypatch.setattr(config, "WORKSPACE_DIR", ws)
    stub = _ReactStub()
    monkeypatch.setattr(coding_module, "_coding_react", stub)
    app = _build_gate_graph(stub)
    cfg = {"configurable": {"thread_id": "t22-cp3-degrade"}}

    paused1 = app.invoke(_two_missing_state(), cfg)
    assert paused1["__interrupt__"][0].value["purpose_key"] == _PK_A
    paused2 = app.invoke(
        Command(resume={"value": _FAKE_VALUE_A, "remember": False}), cfg,
    )
    assert paused2["__interrupt__"][0].value["purpose_key"] == _PK_B
    final = app.invoke(
        Command(resume={"value": "", "remember": False, "degrade": True}), cfg,
    )
    assert "__interrupt__" not in final
    assert final.get("credential_degradations") == {_PK_B: _PURPOSE_B}
    assert secrets_store._SESSION_SECRETS.get(_PK_A) == _FAKE_VALUE_A
    # ReAct 视图携带降级事实。
    assert stub.seen[0].get("credential_degradations") == {_PK_B: _PURPOSE_B}


# ===========================================================================
# CP-2.2-4 GraphBubbleUp 直通（AST 守门 + mock 主图暂停实证）
# ===========================================================================


def test_cp_2_2_4_ast_no_try_except_in_gate_and_composite():
    """gate 与复合节点函数体内不得出现任何 try/except（interrupt 冒泡红线，
    BUG-S4-B1-01 同款守门）；并确认 interrupt 调用确实位于 gate 函数体内。"""
    tree = ast.parse(_CODING_SRC.read_text(encoding="utf-8"))
    checked = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in ("_credential_gate", "coding"):
            checked.add(node.name)
            trys = [n for n in ast.walk(node) if isinstance(n, (ast.Try, ast.TryStar))]
            assert not trys, f"{node.name} 函数体内出现 try/except（GraphBubbleUp 红线）"
            if node.name == "_credential_gate":
                calls = [
                    n for n in ast.walk(node)
                    if isinstance(n, ast.Call)
                    and isinstance(n.func, ast.Name)
                    and n.func.id == "interrupt"
                ]
                assert calls, "_credential_gate 内必须存在 interrupt() 调用（守门对象在位）"
    assert checked == {"_credential_gate", "coding"}, f"目标函数缺失: {checked}"


def test_cp_2_2_4_gate_interrupt_bubbles_and_pauses_graph(tmp_path, monkeypatch):
    """mock 主图实证：gate 的 GraphInterrupt 不被任何层捕获，主图进入暂停态
    （invoke 返回 __interrupt__、checkpoint 存在续跑任务、ReAct 未启动）。"""
    ws = tmp_path / "workspace-bubble"
    ws.mkdir()
    monkeypatch.setattr(config, "WORKSPACE_DIR", ws)
    stub = _ReactStub()
    monkeypatch.setattr(coding_module, "_coding_react", stub)
    app = _build_gate_graph(stub)
    cfg = {"configurable": {"thread_id": "t22-cp4-bubble"}}

    paused = app.invoke(
        _make_state(required=[{"purpose_key": _PK_A, "purpose": _PURPOSE_A}]), cfg,
    )
    intr = paused.get("__interrupt__")
    assert intr, "gate interrupt 必须以主图暂停呈现（而非被捕获吞掉后继续执行）"
    assert intr[0].value["purpose_key"] == _PK_A
    assert stub.seen == [], "暂停期间 ReAct 不得执行（节点未放行）"
    snapshot = app.get_state(cfg)
    assert snapshot.next, "checkpoint 必须停留在待续跑节点（暂停态而非终态）"
    assert "coding" in snapshot.next


# ===========================================================================
# CP-2.2-5 wrapper 复合零回归
# ===========================================================================


def test_cp_2_2_5_graph_py_zero_change():
    """graph.py 零改动是验收面：工作区该文件必须干净（git status 空输出）。"""
    out = subprocess.run(
        ["git", "status", "--porcelain", "--", "core/graph.py"],
        cwd=_REPO_ROOT, capture_output=True, text=True, check=True,
    )
    assert out.stdout.strip() == "", f"core/graph.py 不得有任何改动: {out.stdout!r}"


def test_cp_2_2_5_graph_registers_composite_same_symbol():
    """主图仍以同一符号注册 coding（import 面零改动即复合生效）。"""
    graph_module = importlib.import_module("core.graph")
    assert graph_module.coding is coding_module.coding


def test_cp_2_2_5_empty_required_zero_cost_passthrough(secrets_workspace, monkeypatch):
    """required_credentials==[] → gate 零开销直通（零 interrupt、零 lookup）。"""
    calls = {"lookup": 0}

    def counting_lookup(*a, **k):
        calls["lookup"] += 1
        return None

    monkeypatch.setattr(coding_module, "lookup_secret", counting_lookup)
    rec = _InterruptRecorder([])
    monkeypatch.setattr(coding_module, "interrupt", rec)

    assert coding_module._credential_gate(_make_state(required=[])) == {}
    assert rec.calls == [] and calls["lookup"] == 0, "空声明必须零开销直通"


def test_cp_2_2_5_old_checkpoint_without_keys_zero_cost_passthrough(
    secrets_workspace, monkeypatch,
):
    """旧 checkpoint：plan 无 required_credentials 键 / state 无
    credential_degradations 键 → 同样零开销直通（防御读，R-6 口径）。"""
    rec = _InterruptRecorder([])
    monkeypatch.setattr(coding_module, "interrupt", rec)
    assert coding_module._credential_gate(_make_state(required=None)) == {}
    assert rec.calls == []
    # plan 整体缺失也不炸。
    assert coding_module._credential_gate({}) == {}


def test_cp_2_2_5_update_keyset_unperturbed_without_gate_activity(
    secrets_workspace, monkeypatch,
):
    """无声明、无降级 → 复合节点 update 键集与 ReAct 返回完全一致
    （不额外写 credential_degradations，旧行为字节级零扰动）。"""
    marker = {"current_step": "coding", "code_output_dir": "/tmp/t22-x"}
    stub = _ReactStub(update=marker)
    monkeypatch.setattr(coding_module, "_coding_react", stub)

    update = coding_module.coding(_make_state(required=None))

    assert update == marker
    assert "credential_degradations" not in update
    assert len(stub.seen) == 1


def test_cp_2_2_5_composite_metadata_contract():
    """既有验收面元数据契约（test_sprint3_d1 CP-D1-2 / test_sprint4_c2）守门：
    复合后 __name__/__module__/签名不变。"""
    assert coding_module.coding.__name__ == f"react_wrapper_{coding_module.NODE_NAME}"
    assert coding_module.coding.__module__ == "core.react_base"
    assert list(inspect.signature(coding_module.coding).parameters) == ["state"]


# ===========================================================================
# CP-2.2-6 日志脱敏 + HumanMessage 降级摘要 sort_keys 幂等
# ===========================================================================


def test_cp_2_2_6_gate_logs_never_contain_value_plaintext(
    secrets_workspace, fake_interrupt, caplog,
):
    """gate 全分支（remember / stash / degrade / 非法重问）日志只打 purpose_key，
    绝不出现 value 明文。"""
    fake_interrupt(
        {"remember": True},                                    # 非法 → WARNING
        {"value": _FAKE_VALUE_A, "remember": True},            # A remember
        {"value": _FAKE_VALUE_B, "remember": False},           # B stash
        {"value": "", "remember": False, "degrade": True},     # C degrade
    )
    state = _make_state(required=[
        {"purpose_key": _PK_A, "purpose": _PURPOSE_A},
        {"purpose_key": _PK_B, "purpose": _PURPOSE_B},
        {"purpose_key": "hf_token", "purpose": "下载数据集（T22 假场景）"},
    ])
    with caplog.at_level(logging.DEBUG):
        deg = coding_module._credential_gate(state)

    assert deg == {"hf_token": "下载数据集（T22 假场景）"}
    joined = "\n".join(r.getMessage() for r in caplog.records)
    assert _FAKE_VALUE_A not in joined, "gate 日志泄漏了凭证值明文（A）"
    assert _FAKE_VALUE_B not in joined, "gate 日志泄漏了凭证值明文（B）"
    # 正向面：purpose_key 出现在日志（可审计）。
    assert _PK_A in joined and _PK_B in joined


def test_cp_2_2_6_context_contains_degradation_summary_sort_keys_idempotent(
    tmp_path,
):
    """HumanMessage 动态上下文含降级摘要；两次不同插入序渲染字节级一致
    （sort_keys 幂等，R-PC4 无扰）；零降级路径键不出现（字节零扰动）。"""
    base = {
        "paper_meta": {"arxiv_id": "2409.05591"},
        "workspace_dir": str(tmp_path / "ws"),
        "reproduction_plan": {"code_strategy": "from scratch"},
    }
    deg_order1 = {_PK_A: _PURPOSE_A, _PK_B: _PURPOSE_B}
    deg_order2 = {_PK_B: _PURPOSE_B, _PK_A: _PURPOSE_A}

    def render(state: Dict[str, Any]) -> str:
        payload = coding_module._build_coding_context(state)
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)

    s1 = dict(base, credential_degradations=deg_order1)
    s2 = dict(base, credential_degradations=deg_order2)
    r1, r2 = render(s1), render(s2)
    assert r1 == r2, "降级摘要渲染必须与 dict 插入序无关（sort_keys 字节幂等）"
    assert _PK_A in r1 and _PURPOSE_A in r1 and _PK_B in r1 and _PURPOSE_B in r1

    payload1 = coding_module._build_coding_context(s1)
    assert payload1.get("credential_degradations") == deg_order1

    # 零降级路径：键不注入（既有 HumanMessage 字节零扰动）。
    payload0 = coding_module._build_coding_context(dict(base))
    assert "credential_degradations" not in payload0


# ===========================================================================
# 附：run_command extra_env 装配点确认（实现内容 6，无新生产代码的实证）
# ===========================================================================


def test_extra_env_reassembled_per_node_execution(secrets_workspace, tmp_path, monkeypatch):
    """_get_coding_tools 每次调用（= 每次节点执行）都重组
    build_credential_env(load_all_secrets())：会话层与 `env:` 通用规则自动生效，
    且非 import 期快照（两次调用可见中途新增凭证）。"""
    captured: List[Dict[str, str]] = []
    real_factory = coding_module.make_run_command_tool

    def recording_factory(base_dir, extra_env=None):
        captured.append(dict(extra_env or {}))
        return real_factory(base_dir=base_dir, extra_env=extra_env)

    monkeypatch.setattr(coding_module, "make_run_command_tool", recording_factory)
    state = _make_state(
        required=None,
        paper_meta={"arxiv_id": "2409.05591"},
        workspace_dir=str(tmp_path / "ws"),
    )

    # 第一次装配：会话层凭证（不落盘）经 env: 规则注入。
    stash_session_secret("env:T22_PROBE_VAR", _FAKE_VALUE_A)
    tools1 = coding_module._get_coding_tools(state)
    assert len(tools1) == 7, "工具集仍为 7 个（AC-S4-01 零回归）"
    assert captured[0].get("T22_PROBE_VAR") == _FAKE_VALUE_A, (
        "会话层「不记住」凭证必须经 extra_env 进入 run_command"
    )
    assert captured[0].get("GIT_TERMINAL_PROMPT") == "0"

    # 第二次装配：中途新记住的凭证同样可见（每次执行重组，非快照）。
    remember_secret("env:T22_PROBE_VAR_2", _FAKE_VALUE_B, is_sensitive=True)
    coding_module._get_coding_tools(state)
    assert captured[1].get("T22_PROBE_VAR_2") == _FAKE_VALUE_B
    assert captured[1].get("T22_PROBE_VAR") == _FAKE_VALUE_A
    # 交叉验证 load_all_secrets 合并语义（.secrets 基础 + 会话层覆盖）。
    merged = load_all_secrets()
    assert merged["env:T22_PROBE_VAR"] == _FAKE_VALUE_A
    assert merged["env:T22_PROBE_VAR_2"] == _FAKE_VALUE_B
