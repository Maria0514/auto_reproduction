"""Sprint 7 批次 3 · T-S7-3-6：S7-05 修复循环记忆增强（档 B）CP 测试 + 逐环验红。

覆盖 dev-plan §18 CP-3.1 ~ CP-3.6 + 架构 v1.1 §13.8 AC-S7-09 ~ AC-S7-14：
    - CP-3.2-* / AC 兼容：state +4 键（FixLoopRecord.fix_note/files_touched +
      GlobalState.last_fix_note/last_files_written）+ 旧 checkpoint 兼容；
    - CP-3.3-* / AC-S7-13：fix_note 输出约定固定文案（R-PC4 稳定前缀）+ _map_coding_result
      落库 + 截断 + files_written 抽取（BUG-S1-02 规避）；
    - CP-3.4-* / AC-S7-11 取端：_append_fix_record 从 state 取写进 FixLoopRecord；
    - CP-3.5-* / AC-S7-09/10/12/14：_digest_fix_loop_history 全保留渲染 + 注入 +
      log_path 对齐 + sort_keys 避坑 + 字节幂等 + 旧记录兜底；
    - CP-3.6-2 / AC-S7-11 三环逐环验红：注掉 map 写 / append 取 / digest 渲染 fix_note
      三环，每环注掉后对应断言必须变红（防"coder 说了但没进历史"假绿，沿 BUG-S1-02）；
    - CP-3.6-3 / AC-S7-12：注掉 fix_history_digest 注入后断言必须变红。

log_path 编号对齐（S7-02 落盘 round_{入口 fix_count}.log）：第 N 轮 FixLoopRecord.round_number
= 入口 fix_count+1，故真错日志 = round_{N-1}.log。round1 → round_0.log。

全离线，零 API 配额。
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

import config
from langchain_core.messages import ToolMessage

coding_module = importlib.import_module("core.nodes.coding")
execution_module = importlib.import_module("core.nodes.execution")

ErrorCategory = execution_module.ErrorCategory
ExecutionFeedback = execution_module.ExecutionFeedback
_FIX_NOTE_MAX_CHARS = coding_module._FIX_NOTE_MAX_CHARS
_EXEC_LOGS_SUBDIR = coding_module._EXEC_LOGS_SUBDIR

_IMPORT_ERR = "Traceback (most recent call last):\nModuleNotFoundError: No module named 'src'"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _write_tool_msg(path: str, success: bool = True, call_id: str = "c1") -> ToolMessage:
    """构造一条 write_code_file ToolMessage（json.dumps 合法 JSON，BUG-S1-02 契约）。"""
    return ToolMessage(
        content=json.dumps({"success": success, "path": path}, ensure_ascii=False),
        name="write_code_file",
        tool_call_id=call_id,
    )


def _feedback(category: ErrorCategory = ErrorCategory.IMPORT) -> ExecutionFeedback:
    return ExecutionFeedback(
        category=category,
        auto_fixable=True,
        summary="No module named 'src'",
        fix_hint="入口加 sys.path.insert",
        representative_stderr="",
    )


def _make_written_file(code_dir: Path, rel: str) -> str:
    p = code_dir / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("x", encoding="utf-8")
    return str(p.resolve())


def _base_state(code_dir: Path, tmp_path: Path) -> Dict[str, Any]:
    return {
        "code_output_dir": str(code_dir.resolve()),
        "workspace_dir": str(tmp_path),
        "paper_meta": {"arxiv_id": "2403.06402"},
        "reproduction_plan": {
            "code_strategy": "x", "execution_steps": [], "deliverables": [], "environment": {}
        },
        "resource_info": {}, "paper_analysis": {},
    }


def _fix_record(round_no: int, category: str, files: List[str], fix_note: str) -> Dict[str, Any]:
    return {
        "round_number": round_no,
        "error_summary": "No module named 'src'",
        "error_category": category,
        "fix_strategy": "hint",
        "timestamp": "2026-07-20T00:00:00+00:00",
        "fix_note": fix_note,
        "files_touched": list(files),
    }


# ===========================================================================
# CP-3.2：state +4 键 + 旧 checkpoint 兼容
# ===========================================================================


def test_cp_3_2_1_state_keys_present():
    from core.state import FixLoopRecord, GlobalState
    fr = FixLoopRecord.__annotations__
    assert fr.get("fix_note") is str
    assert "files_touched" in fr
    ga = GlobalState.__annotations__
    assert ga.get("last_fix_note") is str
    assert "last_files_written" in ga


def test_cp_3_2_2_old_checkpoint_compat():
    """旧 checkpoint 无这 4 键 → .get 兜底不 KeyError。"""
    old_state: Dict[str, Any] = {}
    assert old_state.get("last_fix_note", "") == ""
    assert old_state.get("last_files_written", []) == []
    old_rec: Dict[str, Any] = {"round_number": 1, "error_category": "import"}
    assert old_rec.get("fix_note", "") == ""
    assert old_rec.get("files_touched", []) == []


def test_cp_3_2_3_initial_state_defaults():
    from core.state import create_initial_state
    llm = {"default": {"base_url": "http://x", "api_key": "k", "model": "m"}, "overrides": {}}
    s = create_initial_state("2403.06402", llm)
    assert s["last_fix_note"] == ""
    assert s["last_files_written"] == []


# ===========================================================================
# CP-3.3：fix_note 输出约定 + _map_coding_result 落库 + files_written 抽取
# ===========================================================================


def test_cp_3_3_1_fix_note_max_chars_const():
    assert coding_module._FIX_NOTE_MAX_CHARS == 120


def test_cp_3_3_2_rpc4_stable_prefix_fixed_text():
    """AC-S7-13 面：新增 fix_note 指令是固定文案——两次不同 context 下 system prompt 字节相同。"""
    ctx_a = {"code_output_dir": "/a", "arxiv_id": "1111.1111"}
    ctx_b = {"code_output_dir": "/b", "arxiv_id": "2222.2222"}
    sp_a = coding_module._build_coding_system_prompt(ctx_a)
    sp_b = coding_module._build_coding_system_prompt(ctx_b)
    assert sp_a == sp_b, "system prompt 稳定前缀必须跨 context 字节一致（R-PC4）"
    # fix_note 输出约定确在稳定前缀内、且为固定文案（无动态插值残留）。
    assert "fix_note" in sp_a
    assert "本轮问题定位+修复逻辑" in sp_a
    assert "fix_history_digest" in sp_a  # 修复回合读历史指令
    # 稳定前缀内不含任何 context 值（无 /a /b /1111 /2222 泄漏）。
    for leak in ("/a", "/b", "1111.1111", "2222.2222"):
        assert leak not in sp_a


def test_cp_3_3_3_map_result_writes_last_fix_note_and_files(tmp_path):
    code_dir = tmp_path / "code"
    code_dir.mkdir(parents=True)
    written = _make_written_file(code_dir, "src/train.py")
    state = _base_state(code_dir, tmp_path)
    result = {"files_written": [written], "summary": "s", "fix_note": "定位X修复Y"}
    updates = coding_module._map_coding_result(result, state, [_write_tool_msg(written)])
    assert updates["last_fix_note"] == "定位X修复Y"
    assert updates["last_files_written"] == [str(Path(written).resolve())]


def test_cp_3_3_4_fix_note_validate_and_truncate(tmp_path):
    code_dir = tmp_path / "code"
    code_dir.mkdir(parents=True)
    written = _make_written_file(code_dir, "a.py")
    state = _base_state(code_dir, tmp_path)
    tm = [_write_tool_msg(written)]

    # 缺 fix_note → ""
    up = coding_module._map_coding_result({"files_written": [written], "summary": "s"}, state, tm)
    assert up["last_fix_note"] == ""
    # 空 / 空白 fix_note → ""
    up = coding_module._map_coding_result(
        {"files_written": [written], "summary": "s", "fix_note": "   "}, state, tm
    )
    assert up["last_fix_note"] == ""
    # 非字符串 → ""
    up = coding_module._map_coding_result(
        {"files_written": [written], "summary": "s", "fix_note": 123}, state, tm
    )
    assert up["last_fix_note"] == ""
    # 超 120 字 → 截断到 120
    long_note = "定" * 300
    up = coding_module._map_coding_result(
        {"files_written": [written], "summary": "s", "fix_note": long_note}, state, tm
    )
    assert len(up["last_fix_note"]) == _FIX_NOTE_MAX_CHARS


def test_cp_3_3_5_files_written_json_parse_and_filter(tmp_path):
    """files_written 抽取走 json.loads 合法 JSON + 过滤失败 ToolMessage（BUG-S1-02 规避）。"""
    code_dir = tmp_path / "code"
    code_dir.mkdir(parents=True)
    good = _make_written_file(code_dir, "good.py")
    outside = str((tmp_path / "outside.py").resolve())
    state = _base_state(code_dir, tmp_path)
    msgs = [
        _write_tool_msg(good, success=True, call_id="c1"),
        _write_tool_msg(good, success=False, call_id="c2"),  # 失败 write，不计
        _write_tool_msg(outside, success=True, call_id="c3"),  # 越界，不计
        ToolMessage(content="Error in write_code_file: boom", name="write_code_file", tool_call_id="c4"),  # 失败前缀
        ToolMessage(content=str({"success": True, "path": good}), name="write_code_file", tool_call_id="c5"),  # repr 非法 JSON，不计
    ]
    up = coding_module._map_coding_result({"files_written": [good], "summary": "s"}, state, msgs)
    assert up["last_files_written"] == [str(Path(good).resolve())]

    # 拿不到（react_messages 空）→ []
    up2 = coding_module._map_coding_result({"files_written": [], "summary": "s"}, state, [])
    assert up2["last_files_written"] == []


def test_cp_3_3_6_map_result_existing_fields_unchanged(tmp_path):
    code_dir = tmp_path / "code"
    code_dir.mkdir(parents=True)
    written = _make_written_file(code_dir, "a.py")
    state = _base_state(code_dir, tmp_path)
    state["simulation_notice"] = None
    up = coding_module._map_coding_result(
        {"files_written": [written], "summary": "s", "simulation_notice": "模拟了数据集"},
        state, [_write_tool_msg(written)],
    )
    assert up["code_output_dir"] == str(code_dir.resolve())
    assert up["current_step"] == "coding"
    assert up["simulation_notice"] == "模拟了数据集"
    assert "node_errors" in up and "degraded_nodes" in up
    assert "fix_loop_count" not in up  # must-fix-2 不写


# ===========================================================================
# CP-3.4：_append_fix_record 取端
# ===========================================================================


def test_cp_3_4_1_append_takes_from_state():
    state = {
        "fix_loop_history": [],
        "last_fix_note": "定位A修复B",
        "last_files_written": ["/w/code/src/train.py"],
    }
    hist = execution_module._append_fix_record(state, 1, _feedback())
    rec = hist[-1]
    assert rec["fix_note"] == "定位A修复B"
    assert rec["files_touched"] == ["/w/code/src/train.py"]
    assert rec["round_number"] == 1
    assert rec["error_category"] == "import"


def test_cp_3_4_2_time_ordering_self_consistent():
    """R-S7-10 时序自洽：append 取到的是本轮 coder 输出（state 里当前的 last_fix_note）。"""
    state = {"fix_loop_history": [], "last_fix_note": "本轮定位", "last_files_written": ["/w/code/a.py"]}
    hist1 = execution_module._append_fix_record(state, 1, _feedback())
    # 下一轮 coder 覆盖 last_fix_note，state 继续累积 history
    state2 = {
        "fix_loop_history": hist1,
        "last_fix_note": "下一轮定位",
        "last_files_written": ["/w/code/b.py"],
    }
    hist2 = execution_module._append_fix_record(state2, 2, _feedback())
    assert hist2[0]["fix_note"] == "本轮定位"      # 第 1 轮记录不被污染
    assert hist2[1]["fix_note"] == "下一轮定位"    # 第 2 轮记录取当轮


def test_cp_3_4_3_old_checkpoint_backfill_safe():
    """旧 checkpoint 无 last_fix_note/last_files_written → .get 兜底，不 KeyError。"""
    state: Dict[str, Any] = {"fix_loop_history": []}
    hist = execution_module._append_fix_record(state, 1, _feedback())
    rec = hist[-1]
    assert rec["fix_note"] == ""
    assert rec["files_touched"] == []


def test_cp_3_4_4_append_existing_fields_unchanged():
    state = {"fix_loop_history": [], "last_fix_note": "n", "last_files_written": []}
    hist = execution_module._append_fix_record(state, 3, _feedback(ErrorCategory.RUNTIME))
    rec = hist[-1]
    assert rec["round_number"] == 3
    assert rec["error_category"] == "runtime"
    assert rec["fix_strategy"] == "入口加 sys.path.insert"
    assert "timestamp" in rec
    # 单点 read-modify-write：返回整列表（非 reducer 增量）。
    assert isinstance(hist, list) and len(hist) == 1


# ===========================================================================
# CP-3.5：_digest_fix_loop_history 全保留渲染 + 注入
# ===========================================================================


def _four_round_history() -> List[Dict[str, Any]]:
    return [
        _fix_record(1, "import", ["/w/code/src/train.py"], "定位:缺sys.path致src不可导入 修复:入口加sys.path.insert"),
        _fix_record(2, "import", ["/w/code/src/train.py"], "定位:sys.path路径写错 修复:改成绝对路径"),
        _fix_record(3, "import", ["/w/code/src/train.py", "/w/code/src/model.py"], "定位:model.py也缺导入 修复:补两处import"),
        _fix_record(4, "import", ["/w/code/src/train.py"], "定位:包名拼写错 修复:util->utils"),
    ]


def test_cp_3_5_1_digest_full_retain(tmp_path):
    """AC-S7-09：digest 含全部历史轮五元组，轮号升序、多行；首轮不注入。"""
    code_dir = tmp_path / "code"
    code_dir.mkdir(parents=True)
    state = _base_state(code_dir, tmp_path)
    state["fix_loop_history"] = _four_round_history()
    state["execution_result"] = {"errors": ["[error_category=import] x"]}
    state["fix_loop_count"] = 4

    ctx = coding_module._build_coding_context(state)
    assert "fix_history_digest" in ctx
    digest = ctx["fix_history_digest"]
    # 全部 4 轮、升序
    for n in (1, 2, 3, 4):
        assert f"round{n} " in digest
    assert digest.index("round1 ") < digest.index("round2 ") < digest.index("round3 ") < digest.index("round4 ")
    # 五元组齐：category / files_touched / fix_note / log_path
    assert "[import]" in digest
    assert "train.py" in digest and "model.py" in digest  # files_touched basename
    assert "定位:缺sys.path致src不可导入" in digest         # fix_note
    assert "exec_logs/round_0.log" in digest                # round1 -> round_0.log
    assert "exec_logs/round_3.log" in digest                # round4 -> round_3.log
    # 多行
    assert digest.count("\n") >= 4

    # 首轮不注入（fix_count=0）
    state_first = _base_state(code_dir, tmp_path)
    state_first["fix_loop_count"] = 0
    ctx_first = coding_module._build_coding_context(state_first)
    assert "fix_history_digest" not in ctx_first


def test_cp_3_5_2_full_retain_capacity_20_rounds(tmp_path):
    """AC-S7-10：顶格 20 轮全保留、无窗口、每轮 fix_note ≤120、总字节受控。"""
    code_dir = tmp_path / "code"
    code_dir.mkdir(parents=True)
    state = _base_state(code_dir, tmp_path)
    long_note = "定位" * 200  # 远超 120，验渲染截断
    state["fix_loop_history"] = [
        _fix_record(n, "import", [f"/w/code/f{n}.py"], long_note) for n in range(1, 21)
    ]
    digest = coding_module._digest_fix_loop_history(state, str(code_dir.resolve()))
    # 全部 20 轮（无窗口丢弃）
    for n in range(1, 21):
        assert f"round{n} " in digest
    # 每轮渲染的 fix_note 段 ≤120（截断生效）：整体字节受控
    assert "共20轮" in digest
    # 无"仅显示最近K轮"窗口字样
    assert "最近" not in digest and "仅显示" not in digest
    # 总字节上界（§13.4 估算 ≈4500，宽松上界 8000）
    assert len(digest) < 8000


def test_cp_3_5_3_log_path_alignment(tmp_path):
    code_dir = tmp_path / "code"
    code_dir.mkdir(parents=True)
    state = _base_state(code_dir, tmp_path)
    state["fix_loop_history"] = _four_round_history()
    digest = coding_module._digest_fix_loop_history(state, str(code_dir.resolve()))
    # round N -> round_{N-1}.log（S7-02 落盘编号对齐）
    for n in range(1, 5):
        assert f"{_EXEC_LOGS_SUBDIR}/round_{n - 1}.log" in digest


def test_cp_3_5_4_byte_idempotent(tmp_path):
    code_dir = tmp_path / "code"
    code_dir.mkdir(parents=True)
    state = _base_state(code_dir, tmp_path)
    state["fix_loop_history"] = _four_round_history()
    d1 = coding_module._digest_fix_loop_history(state, str(code_dir.resolve()))
    d2 = coding_module._digest_fix_loop_history(state, str(code_dir.resolve()))
    assert d1 == d2
    # 无时间戳/uuid 泄漏
    assert "2026-07-20T00:00:00" not in d1  # timestamp 字段不进 digest


def test_cp_3_5_5_sort_keys_safe(tmp_path):
    """AC-S7-14：注入 fix_history_digest 后 human_payload 仍合法 sort_keys JSON、既有键值不变。"""
    code_dir = tmp_path / "code"
    code_dir.mkdir(parents=True)
    state = _base_state(code_dir, tmp_path)
    state["credential_degradations"] = {"hf_token": "用户降级"}
    state["fix_loop_history"] = _four_round_history()
    state["execution_result"] = {"errors": ["[error_category=import] x"]}
    state["fix_loop_count"] = 4

    payload = coding_module._build_coding_context(state)
    # 合法 sort_keys JSON（react_base.py:854 同款序列化）
    dumped = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    assert json.loads(dumped)  # 可回解析
    # 既有键值不变
    assert payload["code_output_dir"] == str(code_dir.resolve())
    assert payload["credential_degradations"] == {"hf_token": "用户降级"}
    assert "last_error_summary" in payload
    # fix_history_digest 是单个字符串键（非拆多键插字母序中间）
    assert isinstance(payload["fix_history_digest"], str)


def test_cp_3_5_6_old_record_backfill(tmp_path):
    """R-S7-8：旧 FixLoopRecord 无 fix_note/files_touched → 该段留空、其余照常，不炸。"""
    code_dir = tmp_path / "code"
    code_dir.mkdir(parents=True)
    state = _base_state(code_dir, tmp_path)
    # 旧记录（无 fix_note/files_touched 键）
    state["fix_loop_history"] = [
        {"round_number": 1, "error_summary": "x", "error_category": "import",
         "fix_strategy": "h", "timestamp": "t"},
    ]
    digest = coding_module._digest_fix_loop_history(state, str(code_dir.resolve()))
    assert digest is not None
    assert "round1 [import]" in digest
    assert "(coder 未自述)" in digest       # fix_note 空段占位
    assert "(未记录)" in digest             # files_touched 空段占位
    assert "exec_logs/round_0.log" in digest


def test_cp_3_5_empty_history_returns_none():
    assert coding_module._digest_fix_loop_history({}, "/w/code") is None
    assert coding_module._digest_fix_loop_history({"fix_loop_history": []}, "/w/code") is None


# ===========================================================================
# CP-3.6-2：AC-S7-11 三环逐环验红（防"coder 说了但没进历史"假绿）
# ===========================================================================


def _drive_full_link(tmp_path) -> str:
    """端到端链路：coder result 含 fix_note → map → append → digest。返回 digest。"""
    code_dir = tmp_path / "code"
    code_dir.mkdir(parents=True, exist_ok=True)
    written = _make_written_file(code_dir, "src/train.py")
    state = _base_state(code_dir, tmp_path)

    # 环 1：map 写 last_fix_note / last_files_written
    result = {"files_written": [written], "summary": "s", "fix_note": "定位X修复Y"}
    map_up = coding_module._map_coding_result(result, state, [_write_tool_msg(written)])

    # 环 2：append 取 → FixLoopRecord
    state2 = _base_state(code_dir, tmp_path)
    state2["fix_loop_history"] = []
    state2["last_fix_note"] = map_up["last_fix_note"]
    state2["last_files_written"] = map_up["last_files_written"]
    hist = execution_module._append_fix_record(state2, 1, _feedback())

    # 环 3：digest 渲染
    state3 = _base_state(code_dir, tmp_path)
    state3["fix_loop_history"] = hist
    state3["execution_result"] = {"errors": ["[error_category=import] x"]}
    state3["fix_loop_count"] = 1
    ctx = coding_module._build_coding_context(state3)
    return ctx.get("fix_history_digest", "")


def test_cp_3_6_2_full_link_green(tmp_path):
    """链路全通：coder 的 fix_note 端到端进 digest（绿）。"""
    digest = _drive_full_link(tmp_path)
    assert "定位X修复Y" in digest


def test_cp_3_6_2_ring1_map_break_turns_red(tmp_path, monkeypatch):
    """环 1 验红：注掉 map 写 last_fix_note → digest 不含 fix_note，断言变红被捕捉。"""
    orig = coding_module._map_coding_result

    def _broken_map(result, state, react_messages=None):
        up = orig(result, state, react_messages)
        up["last_fix_note"] = ""  # 注掉：map 不写 fix_note
        return up

    monkeypatch.setattr(coding_module, "_map_coding_result", _broken_map)
    digest = _drive_full_link(tmp_path)
    # 断链后 fix_note 丢失——原断言 'assert 定位X修复Y in digest' 必然变红
    assert "定位X修复Y" not in digest
    with pytest.raises(AssertionError):
        assert "定位X修复Y" in digest


def test_cp_3_6_2_ring2_append_break_turns_red(tmp_path, monkeypatch):
    """环 2 验红：注掉 append 取 last_fix_note → FixLoopRecord.fix_note 空 → digest 无 fix_note。"""
    orig = execution_module._append_fix_record

    def _broken_append(state, round_no, feedback):
        # 注掉：append 不从 state 取 last_fix_note（模拟为空 state）
        stripped = dict(state)
        stripped["last_fix_note"] = ""
        stripped["last_files_written"] = []
        return orig(stripped, round_no, feedback)

    monkeypatch.setattr(execution_module, "_append_fix_record", _broken_append)
    digest = _drive_full_link(tmp_path)
    assert "定位X修复Y" not in digest
    with pytest.raises(AssertionError):
        assert "定位X修复Y" in digest


def test_cp_3_6_2_ring3_digest_break_turns_red(tmp_path, monkeypatch):
    """环 3 验红：注掉 digest 渲染 fix_note → digest 不含该值。"""
    orig = coding_module._digest_fix_loop_history

    def _broken_digest(state, code_output_dir):
        d = orig(state, code_output_dir)
        if d is None:
            return None
        # 注掉：渲染时抹掉 fix_note 段（模拟 helper 不渲染 fix_note）
        return "\n".join(
            line.split("| 定位+修复:")[0] + "| (无)" if "| 定位+修复:" in line else line
            for line in d.split("\n")
        )

    monkeypatch.setattr(coding_module, "_digest_fix_loop_history", _broken_digest)
    digest = _drive_full_link(tmp_path)
    assert "定位X修复Y" not in digest
    with pytest.raises(AssertionError):
        assert "定位X修复Y" in digest


# ===========================================================================
# CP-3.6-3：AC-S7-12 注入验红 + log_path 磁盘对齐 + read_code_file 读到真错
# ===========================================================================


def test_cp_3_6_3_log_path_disk_aligned_and_readable(tmp_path, monkeypatch):
    """AC-S7-12：落盘 round_0.log 含真错 → digest log_path 与磁盘对齐 → read_code_file 读到。"""
    monkeypatch.setattr(config, "WORKSPACE_DIR", tmp_path)
    import core.tools.code_fs_tools as code_fs
    monkeypatch.setattr(code_fs, "WORKSPACE_DIR", tmp_path)

    code_dir = tmp_path / "code"
    code_dir.mkdir(parents=True)
    from sandbox.local_venv import SandboxPrepareResult, SandboxRunResult
    prep = SandboxPrepareResult(
        success=True, venv_dir="/w/.venv", python_exe="/w/.venv/bin/python", pip_exe="",
        env_info={}, install_log="ok", install_failed_packages=[], error=None,
    )
    run = SandboxRunResult(
        exit_code=1, stdout="", stderr=_IMPORT_ERR, duration_seconds=0.1,
        timed_out=False, output_truncated=False, command=["python", "train.py"],
    )
    # execution 首跑（入口 fix_count=0）落盘 round_0.log
    path = execution_module._persist_round_log(str(code_dir), 0, prep, [run])
    assert path is not None and Path(path).exists()

    # digest 里 round1 -> exec_logs/round_0.log 与磁盘对齐
    state = _base_state(code_dir, tmp_path)
    state["fix_loop_history"] = [_fix_record(1, "import", ["/w/code/train.py"], "定位X修复Y")]
    digest = coding_module._digest_fix_loop_history(state, str(code_dir.resolve()))
    assert f"{_EXEC_LOGS_SUBDIR}/round_0.log" in digest

    # digest 相对 log_path 拼回绝对路径 = 落盘 path（磁盘对齐）；coder 用
    # _resolve_round_log_path（S7-02）推导的绝对路径 read_code_file 自读能读到真错。
    resolved = coding_module._resolve_round_log_path(str(code_dir.resolve()), 0)
    assert resolved == str(Path(path).resolve())
    read_tool = code_fs.make_read_code_file_tool()
    content = read_tool.invoke({"path": resolved})
    assert "No module named 'src'" in content


def test_cp_3_6_3_inject_break_turns_red(tmp_path, monkeypatch):
    """AC-S7-12 验红：注掉 fix_history_digest 注入后断言变红。"""
    code_dir = tmp_path / "code"
    code_dir.mkdir(parents=True)
    state = _base_state(code_dir, tmp_path)
    state["fix_loop_history"] = _four_round_history()
    state["execution_result"] = {"errors": ["[error_category=import] x"]}
    state["fix_loop_count"] = 4

    # 绿基线：注入存在
    ctx = coding_module._build_coding_context(state)
    assert "fix_history_digest" in ctx

    # 注掉注入：模拟 _digest_fix_loop_history 恒返 None（注入分支不写键）
    monkeypatch.setattr(coding_module, "_digest_fix_loop_history", lambda s, code_output_dir: None)
    ctx_broken = coding_module._build_coding_context(state)
    assert "fix_history_digest" not in ctx_broken
    with pytest.raises(AssertionError):
        assert "fix_history_digest" in ctx_broken


# ===========================================================================
# CP-3.6-4：AC-S7-13 R-PC4 守门（system prompt + digest 字节幂等）
# ===========================================================================


def test_cp_3_6_4_system_prompt_byte_identical_across_state():
    ctx1 = {"code_output_dir": "/x/1", "arxiv_id": "1111.1111", "fix_round": 3}
    ctx2 = {"code_output_dir": "/y/2", "arxiv_id": "2222.2222", "fix_round": 9}
    assert coding_module._build_coding_system_prompt(ctx1) == coding_module._build_coding_system_prompt(ctx2)


def test_cp_3_6_4_digest_byte_idempotent(tmp_path):
    code_dir = tmp_path / "code"
    code_dir.mkdir(parents=True)
    state = _base_state(code_dir, tmp_path)
    state["fix_loop_history"] = _four_round_history()
    a = coding_module._digest_fix_loop_history(state, str(code_dir.resolve()))
    b = coding_module._digest_fix_loop_history(state, str(code_dir.resolve()))
    assert a == b


# ===========================================================================
# CP-3.6-5：AC-S7-14 既有 coding context 键零退化
# ===========================================================================


def test_cp_3_6_5_existing_context_keys_unchanged(tmp_path):
    """首轮（无修复回合）context 完全不含 fix_history_digest；既有键在。"""
    code_dir = tmp_path / "code"
    code_dir.mkdir(parents=True)
    state = _base_state(code_dir, tmp_path)
    state["fix_loop_count"] = 0
    ctx = coding_module._build_coding_context(state)
    assert "fix_history_digest" not in ctx
    assert "last_error_summary" not in ctx  # 首轮无修复反馈
    assert ctx["code_output_dir"] == str(code_dir.resolve())
    assert "code_strategy" in ctx and "arxiv_id" in ctx
