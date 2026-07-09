"""Sprint 5 任务 T-S5-4-3 单测：执行监控页活动流尾部渲染区（S5-07 / AC-S5-13）。

覆盖 dev-plan §T-S5-4-3 检查点：
    - CP-4.3-1 渲染断言：尾部 30 行（构造 45 条事件，断言只渲染最后 30 条 seq
      16..45）、seq 递增顺序、等宽块形态（st.code 单块整体输出）、text 零再处理
      （采集侧已脱敏/压缩/截断，渲染原样透传）、空流占位文案（不空白）；
    - 消费契约：get_activity_tail(thread_id, ACTIVITY_STREAM_RENDER_TAIL) 精确
      入参（T-S5-4-2 交接接口）；tail 切片语义走真实 snapshot_tail（side_effect
      接线，非 mock 自造切片）；
    - 保护令守门（附带）：活动流区仅在 case⑦ 正常渲染路径出现——interrupt#3
      用户输入面板（含 T-S5-2-3 降级按钮五键 payload 共存态）不渲染该区；
      防御式空态：controller 未实现 get_activity_tail（MagicMock 默认返回非
      tuple/list）→ 按空态占位不崩（老 mock controller 兼容面）。
    - CP-4.3-2 零回归**不在本文件**：主控收口后跑该页全量用例（test_sprint3_e2
      系列 + test_sprint4_f1 + test_sprint5_s5_08_routing/t23/t35/t36 + 本文件）。
    - CP-4.3-3 手动 happy path 走查（AC-S5-13 UI 部分）**不在本文件**——留待
      批次 5 与测试工程师协作（本代理无法做浏览器手动走查）。

测试策略（沿用 sp5 T-3-6 范式）：AppTest + mock GraphController
（patch("app._get_controller")）跑真实 render()；最小 case⑦ state 下
execution_result=None（无 sandbox 日志 code 块）且产物路径双缺失（占位 caption
不产 code 块）→ 页面唯一 st.code 来源即活动流区，可做精确断言。

运行::

    .venv/bin/pytest tests/test_sprint5_t43_activity_render.py -q
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import MagicMock, patch

from streamlit.testing.v1 import AppTest

from config import ACTIVITY_STREAM_RENDER_TAIL
from core.activity_stream import ActivityEvent, snapshot_tail

# 文案锚点（与 execution_monitor._render_activity_stream_section 实现严格对齐，防漂移）。
_SECTION_HEADER = f"Agent 活动流（最近 {ACTIVITY_STREAM_RENDER_TAIL} 行）"
_EMPTY_NOTICE_ANCHOR = "暂无活动"


# --------------------------------------------------------------------------- #
# 夹具：事件 / state / controller 工厂（沿用 test_sprint5_t36 范式）
# --------------------------------------------------------------------------- #
def _make_event(seq: int, node: str = "execution", kind: str = "tool",
                text: Optional[str] = None) -> ActivityEvent:
    """构造单条活动事件（schema 与 core/activity_stream.ActivityEvent 对齐）。"""
    return {
        "seq": seq,
        "ts": 1_720_000_000.0 + seq,
        "node": node,
        "kind": kind,
        "text": text if text is not None else f"⏺ run_in_sandbox(cmd-{seq})",
    }


def _make_monitor_state(**overrides: Any) -> Dict[str, Any]:
    """case⑦ 正常渲染态最小 state（current_step=execution，不触发跳转/终态）。"""
    state: Dict[str, Any] = {
        "current_step": "execution",
        "fix_loop_count": 0,
        "fix_loop_history": [],
        "execution_result": None,
        "node_errors": [],
        "degraded_nodes": [],
        "report_path": None,
        "error": None,
    }
    state.update(overrides)
    return state


def _make_controller(
    state: Optional[Dict[str, Any]],
    events: Optional[Tuple[ActivityEvent, ...]] = None,
    wire_tail: bool = True,
) -> MagicMock:
    """case⑦ controller：无 worker 异常、非 interrupt、未结束。

    wire_tail=True 时 get_activity_tail 经**真实 snapshot_tail** 接线（tail 切片
    语义与 T-S5-4-2 生产实现一致，mock 不自造切片）；False 时保留 MagicMock 默认
    返回（非 tuple/list——老 mock controller 兼容面 / 防御式空态用例）。
    """
    controller = MagicMock()
    controller.poll_state.return_value = state
    controller.get_worker_error.return_value = None
    controller.is_interrupted.return_value = False
    controller.is_finished.return_value = False
    if wire_tail:
        from collections import deque
        dq = deque(events or ())
        controller.get_activity_tail.side_effect = (
            lambda thread_id, n=None: snapshot_tail(dq, n)
        )
    return controller


def _make_interrupt_controller(payload: Dict[str, Any],
                               state: Dict[str, Any]) -> MagicMock:
    """interrupt#3 controller（保护令守门用例，沿用 t36 范式）。"""
    controller = MagicMock()
    controller.poll_state.return_value = state
    controller.get_worker_error.return_value = None
    controller.is_interrupted.return_value = True
    controller.interrupt_kind.return_value = "user_input_request"
    controller.get_interrupt_payload.return_value = payload
    return controller


# AppTest 脚本：按 current_page 路由防 st.rerun 无限循环（沿用 t36 范式）。
_MONITOR_SCRIPT = """
import streamlit as st
st.session_state.setdefault("thread_id", "task-t43-001")
st.session_state.setdefault("current_page", "execution")
page = st.session_state.get("current_page", "execution")
if page == "execution":
    from ui.pages.execution_monitor import render
    render()
else:
    st.write("OTHER_STUB")
"""

_THREAD_ID = "task-t43-001"


def _run(controller: MagicMock) -> AppTest:
    with patch("app._get_controller", return_value=controller):
        at = AppTest.from_string(_MONITOR_SCRIPT)
        at.run()
    return at


def _page_text(at: AppTest) -> str:
    parts: List[str] = []
    for collection in (at.title, at.subheader, at.caption, at.markdown,
                       at.error, at.warning, at.info, at.code):
        parts.extend(str(el.value) for el in collection)
    return "\n".join(parts)


def _activity_code_block(at: AppTest) -> str:
    """取活动流 st.code 块内容（最小 case⑦ state 下页面唯一 code 块）。"""
    codes = [str(c.value) for c in at.code]
    assert len(codes) == 1, f"期望唯一 st.code 块（活动流区），实得 {len(codes)}: {codes!r}"
    return codes[0]


def _parse_seq(line: str) -> int:
    """从渲染行 ``#{seq:>4} [{node}] {text}`` 解析 seq。"""
    return int(line.split("[", 1)[0].lstrip("#").strip())


# =========================================================================== #
# CP-4.3-1：尾部 30 行 + seq 递增 + 等宽块形态
# =========================================================================== #
def test_cp_4_3_1_tail_renders_exactly_last_30_of_45():
    """构造 45 条事件 → 只渲染最后 30 条（seq 16..45），等宽块单块输出。"""
    events = tuple(_make_event(i) for i in range(1, 46))
    at = _run(_make_controller(_make_monitor_state(), events=events))
    assert not at.exception, at.exception
    assert _SECTION_HEADER in _page_text(at)

    block = _activity_code_block(at)          # 等宽块形态：唯一 st.code 单块
    lines = block.splitlines()
    assert len(lines) == ACTIVITY_STREAM_RENDER_TAIL == 30
    seqs = [_parse_seq(ln) for ln in lines]
    assert seqs == list(range(16, 46))        # 恰为最后 30 条
    # 头部 15 条（seq 1..15）不得出现。
    assert "cmd-1)" not in block and "cmd-15)" not in block


def test_cp_4_3_1_seq_ascending_and_line_prefix_format():
    """seq 严格递增顺序渲染；行前缀含 seq + [node] 锚点（等宽日志区口径）。"""
    events = (
        _make_event(3, node="coding", kind="llm", text="正在生成训练脚本…"),
        _make_event(4, node="execution", text="⏺ run_in_sandbox(python train.py)"),
        _make_event(5, node="", text="⏺ read_file(main.py)"),  # node 空 → "-" 占位
    )
    at = _run(_make_controller(_make_monitor_state(), events=events))
    assert not at.exception, at.exception

    lines = _activity_code_block(at).splitlines()
    seqs = [_parse_seq(ln) for ln in lines]
    assert seqs == sorted(seqs) == [3, 4, 5]
    assert "[coding]" in lines[0] and "正在生成训练脚本…" in lines[0]
    assert "[execution]" in lines[1]
    assert "[-]" in lines[2]                  # node 缺失占位，不裸露空串


def test_cp_4_3_1_text_rendered_verbatim_zero_reprocessing():
    """渲染侧零再处理：text 已在采集侧脱敏/压缩/截断，渲染原样透传（含掩码串）。"""
    masked = '⏺ run_in_sandbox({"command": "export HF_TOKEN=***MASKED***"})'
    events = (_make_event(7, text=masked),)
    at = _run(_make_controller(_make_monitor_state(), events=events))
    assert not at.exception, at.exception
    assert masked in _activity_code_block(at)  # 原样出现，未被二次加工


def test_cp_4_3_1_fewer_than_tail_renders_all():
    """事件数 < 30 → 全量渲染（snapshot_tail 越界安全语义透传）。"""
    events = tuple(_make_event(i) for i in range(1, 6))
    at = _run(_make_controller(_make_monitor_state(), events=events))
    assert not at.exception, at.exception
    lines = _activity_code_block(at).splitlines()
    assert [_parse_seq(ln) for ln in lines] == [1, 2, 3, 4, 5]


# =========================================================================== #
# CP-4.3-1：空流占位文案（不空白）
# =========================================================================== #
def test_cp_4_3_1_empty_stream_placeholder_not_blank():
    """空流（未知 thread / 尚无事件 → () ）→ 区块标题 + 占位 caption，无 code 块。"""
    at = _run(_make_controller(_make_monitor_state(), events=()))
    assert not at.exception, at.exception
    text = _page_text(at)
    assert _SECTION_HEADER in text            # 区块不因空流消失
    assert _EMPTY_NOTICE_ANCHOR in text       # 占位文案（"暂无活动"级别，不空白）
    assert [str(c.value) for c in at.code] == []


def test_cp_4_3_1_non_sequence_tail_defensive_empty():
    """controller 未接线 get_activity_tail（MagicMock 默认非 tuple/list 返回）→
    防御式按空态占位不崩（老 mock controller / 异常形态兼容面）。"""
    at = _run(_make_controller(_make_monitor_state(), wire_tail=False))
    assert not at.exception, at.exception
    assert _EMPTY_NOTICE_ANCHOR in _page_text(at)
    assert [str(c.value) for c in at.code] == []


# =========================================================================== #
# 消费契约：get_activity_tail 精确入参（T-S5-4-2 交接接口）
# =========================================================================== #
def test_get_activity_tail_called_with_thread_id_and_render_tail():
    """render 以 (thread_id, ACTIVITY_STREAM_RENDER_TAIL) 调 get_activity_tail，
    且 case⑦ 单次渲染恰调用一次（复用既有轮询节奏，不新增轮询/重复取数）。"""
    controller = _make_controller(_make_monitor_state(), events=())
    at = _run(controller)
    assert not at.exception, at.exception
    calls = controller.get_activity_tail.call_args_list
    assert len(calls) == 1
    assert calls[0].args == (_THREAD_ID, ACTIVITY_STREAM_RENDER_TAIL)


# =========================================================================== #
# 保护令守门（附带）：活动流区仅在 case⑦；interrupt#3 面板零污染
# =========================================================================== #
def test_activity_section_absent_in_user_input_panel():
    """interrupt#3（含 allow_degrade=True 五键 payload）→ 无活动流区；
    降级按钮与提交按钮原样（T-S5-2-3 零回归的本文件侧证）。"""
    payload = {
        "interrupt_kind": "user_input_request",
        "question": "复现计划声明需要凭证「env:OPENAI_API_KEY」，请提供该凭证。",
        "is_sensitive": True,
        "purpose_key": "env:OPENAI_API_KEY",
        "allow_degrade": True,
    }
    state = _make_monitor_state(current_step="coding")
    at = _run(_make_interrupt_controller(payload, state))
    assert not at.exception, at.exception
    text = _page_text(at)
    assert _SECTION_HEADER not in text
    assert _EMPTY_NOTICE_ANCHOR not in text
    button_keys = [b.key for b in at.button]
    assert "btn_user_input_degrade" in button_keys
    assert "btn_user_input_submit" in button_keys
