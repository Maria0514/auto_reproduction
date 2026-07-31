"""Sprint 7 S7-06（T-S7-4-8）CP 测试 —— 探测结论落点与送达（``local_env_facts``）。

覆盖 AC-S7-15 / 17 / 18 / 19 / 20。姊妹文件 ``tests/test_sprint7_s706_probe_tool.py``
覆盖 AC-S7-16 / 21 / 22 / 23 / 24 / 26（工具层）。

设计权威：
    - docs/sprint7/architecture.md §15.6（AC-S7-18 四环验红设计 + 四条补充守门）、
      §15.4/§15.5（渲染形态与失败缺席规则）、§16.5（AC-S7-20 测试点细化）；
    - docs/sprint7/prd.md §3 AC 表 AC-S7-15~26；
    - docs/sprint7/dev-plan.md §26 T-S7-4-8（CP-4.8-1/3/7）+ §31 P-3 / R-S7-30。

**本文件承载四道命门中的命门 2（AC-S7-18 防白探四环）**：
    ① 产出环 `_map_resource_scout_result` 写出 `local_env_facts`
    ② 送达环 `_format_planning_context` 把它放进规划 payload（**命门**）
    ③ 反证环 只写 `analysis_notes` 的假解法必然到不了规划
    ④ 端到端环 模型真的收到了（HumanMessage 里读得到），且 SystemMessage 字节不变
逐环验红操作与结果见 docs/sprint7/test-reports/。

**AC-S7-19 为什么要在这里新写断言**（§31 P-3 / R-S7-30）：既有守门
`tests/test_e2e2_message_guard.py` **只扫 `make_node_error(...)` 的 message 实参**，
而 S7-06 按 AC-S7-17 **零新增该调用** ⇒ 光靠 `resource_scout` 在 `_GUARDED_MODULES`
里等于**零覆盖却 passed**，且该文件 `:155` 的 `assert literals` 保险因既有 3 条调用
在册**不会响**。故本文件复用它的 `_BLACKLIST` 与 `_hits` 口径，对新增文案独立扫一遍，
并沿"扫不到即报红"设计断言扫描对象非空 + 扫描器活性金丝雀。

离线维：零 LLM、零 deepxiv 配额、零网络。
"""

from __future__ import annotations

import importlib
import inspect
import json
import logging
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest
from langchain_core.messages import ToolMessage

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import config  # noqa: E402
import core.react_base as react_base  # noqa: E402
import core.tools.env_probe_tool as ept  # noqa: E402
import sandbox.local_venv as lv  # noqa: E402
from core.tools.env_probe_tool import PROBE_TOOL_NAME, make_probe_environment_tool  # noqa: E402

# 已知坑 #6：core/nodes/__init__.py 显式 export 会用 callable 遮蔽同名子模块，
# 需要访问模块属性时一律走 importlib.import_module。
resource_scout_module = importlib.import_module("core.nodes.resource_scout")
planning_module = importlib.import_module("core.nodes.planning")

resource_scout = resource_scout_module.resource_scout
_map_resource_scout_result = resource_scout_module._map_resource_scout_result
_digest_env_probe = resource_scout_module._digest_env_probe
_build_resource_scout_system_prompt = resource_scout_module._build_resource_scout_system_prompt
_SCOUT_BODY = resource_scout_module._RESOURCE_SCOUT_SYSTEM_PROMPT_BODY
NODE_NAME = resource_scout_module.NODE_NAME
_format_planning_context = planning_module._format_planning_context
_planning_react = planning_module._planning_react

# AC-S7-19：复用既有守门的黑名单与命中口径（大小写不敏感 + 词边界），不另写一份。
from tests.test_e2e2_message_guard import _BLACKLIST, _hits  # noqa: E402

_GPU_FACT = "GPU 0: NVIDIA A100-SXM4-80GB (UUID: GPU-xxxx)"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _probe_content(
    command: str,
    *,
    stdout: str = "",
    stderr: str = "",
    exit_code: int = 0,
    timed_out: bool = False,
    truncated: bool = False,
) -> str:
    """构造 probe_environment 工厂层同款 6 键返回 JSON（真实序列化形态）。"""
    return json.dumps(
        {
            "command": command,
            "exit_code": exit_code,
            "stdout_tail": stdout,
            "stderr_tail": stderr,
            "timed_out": timed_out,
            "truncated": truncated,
        },
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )


def _probe_msg(command: str, idx: int = 0, **kwargs: Any) -> ToolMessage:
    return ToolMessage(
        content=_probe_content(command, **kwargs),
        name=PROBE_TOOL_NAME,
        tool_call_id=f"call-{idx}",
    )


def _gpu_history() -> List[ToolMessage]:
    return [_probe_msg("nvidia-smi -L", stdout=_GPU_FACT)]


def _scout_state(**overrides: Any) -> Dict[str, Any]:
    state: Dict[str, Any] = {
        "llm_config_set": {
            "default": {
                "base_url": "http://x",
                "model": "m",
                "api_key": "k",
                "temperature": 0.0,
                "max_tokens": 1024,
            },
            "overrides": {},
        },
        "paper_meta": {"arxiv_id": "2405.14831", "title": "HippoRAG"},
        "paper_analysis": {"framework": "PyTorch"},
        "node_errors": [],
        "degraded_nodes": [],
        "retry_budget_remaining": 50,
    }
    state.update(overrides)
    return state


def _planning_state(**overrides: Any) -> Dict[str, Any]:
    state: Dict[str, Any] = {
        "llm_config_set": {
            "default": {
                "base_url": "http://x",
                "model": "m",
                "api_key": "k",
                "temperature": 0.0,
                "max_tokens": 1024,
            },
            "overrides": {},
        },
        "paper_meta": {"arxiv_id": "2405.14831", "title": "HippoRAG"},
        "paper_analysis": {"method_summary": "中文摘要", "metrics": ["EM"]},
        "resource_info": {
            "repos": [{"url": "https://github.com/a/repo", "quality_score": 0.8}],
            "selected_repo": {"url": "https://github.com/a/repo", "quality_score": 0.8},
            "external_resources": [],
            "resource_strategy": "use_repo",
        },
        "node_errors": [],
        "degraded_nodes": [],
        "retry_budget_remaining": 50,
        "_planning_user_feedback": None,
        "_planning_pending_repo_url": None,
    }
    state.update(overrides)
    return state


def _good_scout_result() -> Dict[str, Any]:
    return {
        "repos": [{
            "url": "https://github.com/a/repo",
            "source": "git_clone",
            "is_official": True,
            "quality_score": 0.8,
            "local_path": "/w/repos/repo",
        }],
        "selected_repo": {
            "url": "https://github.com/a/repo",
            "quality_score": 0.8,
            "local_path": "/w/repos/repo",
        },
        "external_resources": [],
        "resource_strategy": "use_repo",
    }


class _CapturingSubgraph:
    """脚本化 ReAct 子图：捕获 initial 并回放固定 result / messages。"""

    def __init__(self, captured: Dict[str, Any], result, messages, rounds: int):
        self._captured = captured
        self._result = result
        self._messages = messages
        self._rounds = rounds

    def invoke(self, initial):
        self._captured["initial"] = initial
        return {
            "result": self._result,
            "messages": list(self._messages),
            "round": self._rounds,
            "status": "done",
        }


def _patch_subgraph(monkeypatch, result=None, messages=None, rounds: int = 3) -> Dict[str, Any]:
    captured: Dict[str, Any] = {}

    def _factory(**kw):
        captured.update(kw)
        return _CapturingSubgraph(captured, result, messages or [], rounds)

    monkeypatch.setattr(react_base, "create_react_subgraph", _factory)
    monkeypatch.setattr(react_base, "create_llm", lambda cfg: object())
    return captured


# ===========================================================================
# AC-S7-15 工具集 5→6（正向） + 计划制定侧不变（负向守门） + base_dir 取值
# ===========================================================================


def test_ac_s7_15_scout_tool_set_is_six_with_probe(monkeypatch):
    """正向：资源探索装配出的工具集恰 6 个，含 probe_environment；max_rounds 为 30（S7-07 上调）。"""
    captured = _patch_subgraph(monkeypatch)
    resource_scout(_scout_state())

    names = sorted(t.name for t in captured["tools"])
    assert names == [
        "check_url_reachable_tool", "get_paper_brief", "git_clone_and_analyze",
        "probe_environment", "search_papers", "web_search",
    ], names
    assert captured["max_rounds"] == 30 == config.REACT_MAX_ROUNDS_RESOURCE_SCOUT


def test_ac_s7_15_planning_tool_set_unchanged_no_probe(monkeypatch):
    """负向守门：计划制定节点工具集**不变**（仍 5 个、不含 probe_environment）。

    PRD 非目标 1：探测能力只给资源探索侧，不扩到 planning。
    """
    captured = _patch_subgraph(monkeypatch)
    _planning_react(_planning_state())

    names = sorted(t.name for t in captured["tools"])
    assert names == [
        "check_url_reachable_tool", "get_paper_structure",
        "git_clone_and_analyze", "read_section", "web_search",
    ], names
    assert PROBE_TOOL_NAME not in names


def _probe_tool_of(captured: Dict[str, Any]):
    return next(t for t in captured["tools"] if t.name == PROBE_TOOL_NAME)


def test_ac_s7_15_base_dir_bound_to_state_workspace_dir(tmp_path, monkeypatch):
    """base_dir 闭包绑定 state["workspace_dir"]（cwd 不可由模型指定）。"""
    ws = tmp_path / "workspace"
    ws.mkdir()
    task_dir = ws / "task-1"
    task_dir.mkdir()
    monkeypatch.setattr(config, "WORKSPACE_DIR", ws)
    monkeypatch.setattr(lv, "WORKSPACE_DIR", ws)

    captured = _patch_subgraph(monkeypatch)
    resource_scout(_scout_state(workspace_dir=str(task_dir)))

    calls: List[Dict[str, Any]] = []
    monkeypatch.setattr(ept, "_run_subprocess", lambda cmd, **kw: calls.append(kw) or _FakeRR(cmd))
    _probe_tool_of(captured).invoke({"command": "uname -srm"})
    assert calls and calls[0]["cwd"] == str(task_dir)


def test_ac_s7_15_base_dir_falls_back_to_workspace_dir(tmp_path, monkeypatch):
    """state 无 workspace_dir 时回退 config.WORKSPACE_DIR（P-2 的 import 补齐守门）。"""
    ws = tmp_path / "workspace"
    ws.mkdir()
    monkeypatch.setattr(config, "WORKSPACE_DIR", ws)
    monkeypatch.setattr(lv, "WORKSPACE_DIR", ws)
    # resource_scout 在 import 期绑定了 WORKSPACE_DIR，需就地改写模块属性。
    monkeypatch.setattr(resource_scout_module, "WORKSPACE_DIR", ws)

    captured = _patch_subgraph(monkeypatch)
    resource_scout(_scout_state())

    calls: List[Dict[str, Any]] = []
    monkeypatch.setattr(ept, "_run_subprocess", lambda cmd, **kw: calls.append(kw) or _FakeRR(cmd))
    _probe_tool_of(captured).invoke({"command": "uname -srm"})
    assert calls and calls[0]["cwd"] == str(ws)


class _FakeRR:
    """最小 SandboxRunResult 替身（只为让工具走完返回路径，不启动进程）。"""

    def __init__(self, cmd):
        self.exit_code = 0
        self.stdout = "ok"
        self.stderr = ""
        self.timed_out = False
        self.output_truncated = False
        self.command = list(cmd)


# ===========================================================================
# AC-S7-18 ①产出环 + 三 return 点全覆盖
# ===========================================================================


def test_ac_s7_18_ring1_map_result_emits_local_env_facts():
    """①产出环：工具历史里有成功探测 → update 含 local_env_facts 且含事实与命令。

    只写 analysis_notes 的假解法在这里就红（它根本不产出该键）。
    """
    update = _map_resource_scout_result(_good_scout_result(), _scout_state(), _gpu_history())
    assert "local_env_facts" in update, "①产出环断：未写出 local_env_facts"
    facts = update["local_env_facts"]
    assert "A100" in facts
    assert "nvidia-smi -L" in facts


@pytest.mark.parametrize("result", [None, {"error": "all tools failed"}])
def test_ac_s7_18_ring1_three_return_points_all_write(result):
    """三 return 点全覆盖：agent 的 <result> 崩了，机器事实照样送出去。

    防"agent 崩了顺带把机器事实也丢了"（架构 §15.3(b)）。
    """
    update = _map_resource_scout_result(result, _scout_state(), _gpu_history())
    assert update["resource_info"]["resource_strategy"] == "from_scratch"
    assert NODE_NAME in update["degraded_nodes"]
    assert "A100" in update["local_env_facts"], "降级 return 点漏写 local_env_facts"


def test_ac_s7_18_ring1_absent_probe_writes_no_key():
    """缺席不造哨兵值：无探测历史 → update **不含**该键（"未知"= 键不存在）。"""
    update = _map_resource_scout_result(_good_scout_result(), _scout_state(), [])
    assert "local_env_facts" not in update
    update_none = _map_resource_scout_result(_good_scout_result(), _scout_state(), None)
    assert "local_env_facts" not in update_none


# ===========================================================================
# AC-S7-18 ②送达环（命门） + ③反证环（负向守门）
# ===========================================================================


def test_ac_s7_18_ring2_planning_context_carries_env_facts():
    """②送达环（命门）：①的产出合进 state 后，规划上下文 payload 必须带着它。

    ⚠ 验红目标：注掉 planning.py 的 build_context lambda 第 6 实参 → ②④必红。
    """
    update = _map_resource_scout_result(_good_scout_result(), _scout_state(), _gpu_history())
    # 承接 update 的**全部**产出（含备注通道），这样"改写进 analysis_notes"的假解法
    # 在这里会得到一条干净的断言失败而非 KeyError。
    state = _planning_state(
        local_env_facts=update.get("local_env_facts", ""),
        analysis_notes=update.get("analysis_notes", ""),
    )

    payload = _format_planning_context(
        state["paper_meta"],
        state["paper_analysis"],
        state["resource_info"],
        state["_planning_user_feedback"],
        state["_planning_pending_repo_url"],
        state.get("local_env_facts"),
    )
    assert "local_env_facts" in payload, "②送达环断：规划上下文没有环境事实落点键"
    assert "A100" in payload["local_env_facts"]


def test_ac_s7_18_ring3_analysis_notes_channel_never_reaches_planning():
    """③反证环：事实只落在 analysis_notes（给人看的备注通道）时，规划**收不到**。

    把"备注通道到不了规划"钉成常驻断言——任何"改回备注通道"的实现必然同时打红 ②③，
    无法靠调 ② 的断言绕过。
    """
    state = _planning_state(
        analysis_notes=f"[ENV] {_GPU_FACT}",
        local_env_facts="",
    )
    payload = _format_planning_context(
        state["paper_meta"],
        state["paper_analysis"],
        state["resource_info"],
        state["_planning_user_feedback"],
        state["_planning_pending_repo_url"],
        state.get("local_env_facts"),
    )
    assert "local_env_facts" not in payload
    assert "A100" not in json.dumps(payload, ensure_ascii=False)


# ===========================================================================
# AC-S7-18 ④端到端环（防接线漏）
# ===========================================================================


def test_ac_s7_18_ring4_model_actually_receives_env_facts(monkeypatch):
    """④端到端环：模型真的收到了 —— HumanMessage 里读得到 local_env_facts。

    守的就是"_format_planning_context 改对了、但 build_context lambda 忘了传第 6 参"
    这一档假绿。同时断言 SystemMessage 字节与不带该键时**完全一致**（Prompt Cache
    冻结前缀不受影响）。
    ⚠ 验红目标：注掉 lambda 第 6 实参 → 本用例必红。
    """
    update = _map_resource_scout_result(_good_scout_result(), _scout_state(), _gpu_history())

    cap_with = _patch_subgraph(monkeypatch)
    _planning_react(_planning_state(
        local_env_facts=update.get("local_env_facts", ""),
        analysis_notes=update.get("analysis_notes", ""),
    ))
    initial_with = cap_with["initial"]

    payload = json.loads(initial_with["messages"][1].content)
    assert "local_env_facts" in payload, "④端到端环断：模型没收到环境事实"
    assert "A100" in payload["local_env_facts"]

    cap_without = _patch_subgraph(monkeypatch)
    _planning_react(_planning_state())
    initial_without = cap_without["initial"]

    assert "local_env_facts" not in json.loads(initial_without["messages"][1].content)
    assert initial_with["messages"][0].content == initial_without["messages"][0].content, \
        "SystemMessage（冻结前缀）字节必须与不带该键时完全一致"


def test_ac_s7_18_ring4_analysis_notes_never_enters_human_message(monkeypatch):
    """④+③ 合验：只写 analysis_notes 时，模型收到的 HumanMessage 里没有该事实。"""
    cap = _patch_subgraph(monkeypatch)
    _planning_react(_planning_state(analysis_notes=f"[ENV] {_GPU_FACT}", local_env_facts=""))
    human = cap["initial"]["messages"][1].content
    assert "A100" not in human


# ===========================================================================
# AC-S7-18 补充守门（架构 §15.6）：字节幂等 / 单一真相源 / 渲染规则 / 失败兜底
# ===========================================================================


def test_digest_is_byte_idempotent_and_has_no_nondeterminism():
    """字节幂等：同一历史两次 digest 字节相同；不含 duration / 时间戳 / uuid。

    非确定性成分会让 checkpoint 重放与 revise 重入字节抖动，把 Prompt Cache 的
    "破一次"退化成"破每次"。
    """
    history = [
        _probe_msg("nvidia-smi -L", 0, stdout=_GPU_FACT),
        _probe_msg("df -h .", 1, stdout="Filesystem  Size  Avail\n/dev/sda1  278G  241G"),
    ]
    first = _digest_env_probe(history)
    second = _digest_env_probe(history)
    assert first == second and first

    for token in ("duration", "duration_seconds", "uuid", "elapsed", "耗时"):
        assert token not in first, token
    assert not re.search(r"\d{4}-\d{2}-\d{2}", first), "digest 不得含日期时间戳"
    assert not re.search(r"\d+\.\d+s\b", first), "digest 不得含耗时"


def test_probe_tool_name_is_single_source_of_truth(tmp_path, monkeypatch):
    """工具名单一真相源：工具实例名 == PROBE_TOOL_NAME == digest 扫描所用常量。

    防"工具改名 → digest 悄悄失效 → 白探回潮"（R-S7-21）。
    """
    ws = tmp_path / "workspace"
    ws.mkdir()
    monkeypatch.setattr(config, "WORKSPACE_DIR", ws)
    monkeypatch.setattr(lv, "WORKSPACE_DIR", ws)
    assert make_probe_environment_tool(base_dir=str(ws)).name == PROBE_TOOL_NAME
    assert resource_scout_module.PROBE_TOOL_NAME is PROBE_TOOL_NAME


def test_digest_render_rules_order_dedup_and_cap():
    """渲染规则：首次出现顺序 + 同命令保留最后一次 + 单条截断到上限 + 总长封顶。

    S7-08（T-S7-5-6，架构 §18.3.4 / dev-plan §32.4 第 11 条）**只换不弱化**地同步
    两处既有断言，并顺带成为新的总长截断的正向覆盖：

    1. 原"逐行 ``len(line) <= max(cap, 60)``"在 cap 400 -> 2600 后会退化成"几乎
       不可能失败"⇒ 改为对**单条命令块整体**断言（形态更严，不是放宽）；
    2. 原结构性上界"清单条数 × 单条上限"依赖"清单恰 15 条"这个分母，S7-09 放开
       允许清单后该分母消失 ⇒ 换成显式总长常量 ``_PROBE_DIGEST_MAX_CHARS`` 断言；
    3. 本用例的 15 条 × ``cap * 3`` 恰好触顶总长上限，故一并断言"截尾 + 追加说明
       行"（**不静默**，架构 §18.3.2 / R-S7-42）。
    """
    cap = resource_scout_module._PROBE_OUTPUT_MAX_CHARS
    digest_cap = resource_scout_module._PROBE_DIGEST_MAX_CHARS
    note = resource_scout_module._PROBE_DIGEST_TRUNCATED_NOTE

    history = [
        _probe_msg("lscpu", 0, stdout="CPU-FIRST"),
        _probe_msg("free -h", 1, stdout="MEM"),
        _probe_msg("lscpu", 2, stdout="CPU-LAST"),
    ]
    digest = _digest_env_probe(history)
    assert digest.index("$ lscpu") < digest.index("$ free -h"), "命令须按首次出现顺序"
    assert "CPU-LAST" in digest and "CPU-FIRST" not in digest, "同命令须保留最后一次"

    long_history = [
        _probe_msg(cmd, i, stdout="X" * (cap * 3))
        for i, cmd in enumerate(ept._PROBE_COMMANDS)
    ]
    long_digest = _digest_env_probe(long_history)

    # 总长确定性上界（AC-S7-42 后半句）+ 触顶不静默
    assert len(long_digest) <= digest_cap, f"digest 总长未封顶：{len(long_digest)}"
    assert long_digest.endswith(note), "触顶截尾必须在末尾追加说明行（禁止静默截断）"
    assert long_digest.startswith("本机环境实测"), "截尾保头：抬头行必须留下"

    # 单条命令块整体（剥掉尾部说明行后）不得超过单条上限
    body = long_digest[: -len(note)].rstrip("\n")
    blocks = body.split("\n$ ")
    assert len(blocks) >= 2, "digest 应含段首行 + 至少一条命令块"
    for block in blocks[1:]:
        out = block.split("\n", 1)[1] if "\n" in block else ""
        assert len(out) <= cap, f"单条命令块输出未截断到 {cap}: {len(out)}"


def test_digest_failure_fallbacks_never_block_node(caplog):
    """失败兜底：全不可解析 → "" 且不阻断；存在目标 ToolMessage 却提不出记录 → WARNING。"""
    assert _digest_env_probe(None) == ""
    assert _digest_env_probe([]) == ""

    unusable = [
        ToolMessage(content="Error in probe_environment: boom", name=PROBE_TOOL_NAME,
                    tool_call_id="c0"),
        ToolMessage(content="not json at all", name=PROBE_TOOL_NAME, tool_call_id="c1"),
        ToolMessage(content=ept._reject_with_list(), name=PROBE_TOOL_NAME, tool_call_id="c2"),
    ]
    with caplog.at_level(logging.WARNING, logger=resource_scout_module.logger.name):
        assert _digest_env_probe(unusable) == ""
    assert any("env probe digest skipped" in r.message or "env probe digest skipped" in r.getMessage()
               for r in caplog.records), "解析失败但存在目标 ToolMessage 时必须打 WARNING（禁止静默吞错）"

    # 无目标 ToolMessage 时不打 WARNING（避免噪声）
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger=resource_scout_module.logger.name):
        assert _digest_env_probe([ToolMessage(content="{}", name="web_search",
                                              tool_call_id="c3")]) == ""
    assert not caplog.records


# ===========================================================================
# AC-S7-17 探测失败 / 超时 / 命令不存在 → 主链路零污染
# ===========================================================================


_FAILING_PROBE_HISTORIES = {
    "timeout": [_probe_msg("nvidia-smi", exit_code=-1, timed_out=True)],
    "command_not_found": [_probe_msg(
        "nvidia-smi", exit_code=-1,
        stderr="subprocess start failed: [Errno 2] No such file or directory: 'nvidia-smi'",
    )],
    "rejected": [ToolMessage(content=ept._reject_with_list(), name=PROBE_TOOL_NAME,
                             tool_call_id="c9")],
    "tool_error": [ToolMessage(content="Error in probe_environment: boom",
                               name=PROBE_TOOL_NAME, tool_call_id="c8")],
}


@pytest.mark.parametrize("form", sorted(_FAILING_PROBE_HISTORIES))
def test_ac_s7_17_probe_failure_does_not_pollute_main_path(form):
    """探测恒失败（四形态）→ resource_info 与基线一致、不降级、不改写策略。"""
    baseline = _map_resource_scout_result(_good_scout_result(), _scout_state(), [])
    degraded = _map_resource_scout_result(
        _good_scout_result(), _scout_state(), _FAILING_PROBE_HISTORIES[form]
    )

    assert degraded["resource_info"] == baseline["resource_info"]
    assert degraded["resource_info"]["resource_strategy"] == "use_repo"
    assert NODE_NAME not in degraded["degraded_nodes"]
    assert degraded["node_errors"] == baseline["node_errors"] == []


def test_ac_s7_17_no_gpu_is_a_valid_conclusion():
    """"本机无 GPU"是有效结论：照常写入事实、不降级、不改成从零实现。"""
    history = [_probe_msg(
        "nvidia-smi -L", exit_code=-1,
        stderr="subprocess start failed: [Errno 2] No such file or directory: 'nvidia-smi'",
    )]
    update = _map_resource_scout_result(_good_scout_result(), _scout_state(), history)
    assert update["local_env_facts"], "命令不可用也是事实，应照常写入"
    assert "该命令在本机不可用" in update["local_env_facts"]
    assert "subprocess start failed" not in update["local_env_facts"]
    assert update["resource_info"]["resource_strategy"] == "use_repo"
    assert NODE_NAME not in update["degraded_nodes"]


def test_ac_s7_17_full_node_run_with_failing_probe(monkeypatch):
    """整节点跑一遍：探测失败历史不改变 resource_info、不进 degraded_nodes。"""
    result = _good_scout_result()
    cap_base = _patch_subgraph(monkeypatch, result=result, messages=[])
    baseline = resource_scout(_scout_state())
    assert cap_base["initial"]["messages"]  # 脚本化子图确实被调用

    _patch_subgraph(monkeypatch, result=result,
                    messages=_FAILING_PROBE_HISTORIES["command_not_found"])
    with_probe = resource_scout(_scout_state())

    assert with_probe["resource_info"] == baseline["resource_info"]
    assert NODE_NAME not in with_probe["degraded_nodes"]
    assert with_probe["node_errors"] == []


# ===========================================================================
# AC-S7-19 用户可见文案零内部标识符（新增独立断言，R-S7-30）
# ===========================================================================


def _ac19_scan_targets(tmp_path, monkeypatch) -> List[tuple]:
    """收集 S7-06 新增的、有可能流向用户可见文案的字符串。"""
    ws = tmp_path / "workspace"
    ws.mkdir()
    monkeypatch.setattr(config, "WORKSPACE_DIR", ws)
    monkeypatch.setattr(lv, "WORKSPACE_DIR", ws)

    targets: List[tuple] = []

    # 1) digest 产出（会整段进规划上下文 → 规划写出的 plan_summary 用户可见）
    digest = _digest_env_probe([
        _probe_msg("nvidia-smi -L", 0, stdout=_GPU_FACT),
        _probe_msg("nvcc --version", 1, exit_code=-1,
                   stderr="subprocess start failed: [Errno 2] No such file or directory: 'nvcc'"),
        _probe_msg("df -h .", 2, stdout="Filesystem Size Avail\n/dev/sda1 278G 241G"),
    ])
    targets.append(("digest", digest))

    # 2) 不在清单内的拒绝文案（含 allowed_commands 清单文本）
    reject_list = json.loads(ept._reject_with_list())
    targets.append(("_reject_with_list.error", reject_list["error"]))
    for i, cmd in enumerate(reject_list["allowed_commands"]):
        targets.append((f"allowed_commands[{i}]", cmd))

    # 3) 解析失败 / 越界两条 _reject 文案（真实调用产出，非手抄）
    tool_ok = make_probe_environment_tool(base_dir=str(ws))
    targets.append((
        "_reject.parse_error",
        json.loads(tool_ok.invoke({"command": 'nvidia-smi "unbalanced'}))["error"],
    ))
    outside = tmp_path / "outside"
    outside.mkdir()
    tool_bad = make_probe_environment_tool(base_dir=str(outside))
    targets.append((
        "_reject.outside_workspace",
        json.loads(tool_bad.invoke({"command": "uname -srm"}))["error"],
    ))

    return targets


def test_ac_s7_19_blacklist_scanner_is_alive():
    """金丝雀：先证明扫描器活着（否则"零命中"是假绿）。"""
    assert _BLACKLIST, "既有守门的黑名单为空——扫描口径已失效"
    assert _hits("已降级为 from_scratch") , "扫描器对已知违例应命中"
    assert _hits("resource_scout 节点失败"), "扫描器对节点名应命中"
    assert _hits("ReAct 轮次耗尽"), "扫描器对术语应命中"
    assert _hits("已降级为从零实现") == [], "通俗中文不应误报"


def test_ac_s7_19_new_user_facing_text_has_no_internal_jargon(tmp_path, monkeypatch):
    """S7-06 新增文案过同一份 _BLACKLIST：零命中，且扫描对象非空。

    §31 P-3 / R-S7-30：既有 message guard 只扫 make_node_error 实参，S7-06 零新增
    该调用 ⇒ 光靠模块名在册等于零覆盖却 passed。本条是 AC-S7-19 的真守门。
    """
    targets = _ac19_scan_targets(tmp_path, monkeypatch)

    # "扫不到即报红"保险：扫描对象必须非空，防范围指错扫到 0 条却 passed
    assert targets, "AC-S7-19 扫描对象为空——扫描范围指错了"
    assert len(targets) >= 5, f"扫描对象条数异常偏少：{len(targets)}"
    for label, text in targets:
        assert isinstance(text, str) and text.strip(), f"扫描对象 {label} 为空串"

    violations = [
        f"  {label} 命中 {_hits(text)} -> {text!r}"
        for label, text in targets
        if _hits(text)
    ]
    assert not violations, (
        "S7-06 新增的用户可见文案泄漏了内部枚举 / 节点名 / 技术术语：\n"
        + "\n".join(violations)
    )


def test_ac_s7_19_digest_does_not_leak_tool_or_node_name():
    """digest 额外守门：不含工具名 / 节点名 / 策略枚举 / 内部英文兜底串。"""
    digest = _digest_env_probe([
        _probe_msg("nvidia-smi -L", 0, stdout=_GPU_FACT),
        _probe_msg("nvcc --version", 1, exit_code=-1,
                   stderr="subprocess start failed: no such file"),
    ])
    assert digest
    for forbidden in (PROBE_TOOL_NAME, NODE_NAME, "from_scratch", "use_repo", "hybrid",
                      "subprocess start failed", "ToolMessage"):
        assert forbidden not in digest, forbidden


# ===========================================================================
# AC-S7-20 Prompt Cache 与预算零退化
# ===========================================================================

_PROBE_TOOL_LINE_PREFIX = "- probe_environment(command)"
_PROBE_SECTION_HEADER = "【环境探测（必做步骤）】"  # S7-07：由"可选补充步"改为"必做步骤"


def _new_prompt_text() -> str:
    """精确切出 S7-06 新增的两处文案（工具清单一行 + 环境探测段落）。"""
    lines = _SCOUT_BODY.splitlines()
    tool_lines = [ln for ln in lines if ln.startswith(_PROBE_TOOL_LINE_PREFIX)]
    assert len(tool_lines) == 1, "工具清单里的探测工具说明应恰有一行"

    idx = lines.index(_PROBE_SECTION_HEADER)
    section = [lines[idx]]
    for ln in lines[idx + 1:]:
        if not ln.startswith("- "):
            break
        section.append(ln)
    # S7-08（T-S7-5-7）：探测段落改写为 6 项必探维度后由"标题 + 5 条"变为
    # "标题 + 11 条"（1 条总起 + 6 条必探维度 + 1 条节制 + 3 条既有约束）。
    # **只换不弱化**：仍是精确 `==`，不改成范围/下界断言。
    assert len(section) == 12, f"环境探测段落应为标题 + 11 条要点，实得 {len(section)}"
    return "\n".join(tool_lines + section)


def test_ac_s7_20_scout_prompt_body_byte_identical_across_papers():
    """跨论文 SystemMessage 主体字节一致（CP-B2-10 口径不破）。"""
    ctx_a = {"arxiv_id": "2405.14831", "title": "HippoRAG"}
    ctx_b = {"arxiv_id": "1706.03762", "title": "Attention Is All You Need"}
    assert _build_resource_scout_system_prompt(ctx_a) == _build_resource_scout_system_prompt(ctx_b)
    assert _build_resource_scout_system_prompt(ctx_a) == _SCOUT_BODY


def test_ac_s7_20_new_prompt_text_has_no_interpolation_traces():
    """负向：新增两处文案零插值痕迹（无 {}、无 arxiv、无绝对路径）。"""
    text = _new_prompt_text()
    assert text.strip()
    assert "{" not in text and "}" not in text
    assert "arxiv" not in text.lower()
    for token in ("/data/", "/home/", "/tmp/", str(config.WORKSPACE_DIR)):
        assert token not in text, token
    assert not re.search(r"(?:(?<=\s)|^)/[A-Za-z0-9_.\-]+/", text), "不得含绝对路径串"


def test_ac_s7_20_probe_section_is_outside_the_three_step_chain():
    """探测段落位于三步降级链之后、仓库评分段之前（S7-07 改必做步骤后位置不变，三步链字节仍不动）。"""
    body = _SCOUT_BODY
    step3 = body.index('3. 全部失败 -- 在 <result> 中输出 resource_strategy = "from_scratch"')
    probe = body.index(_PROBE_SECTION_HEADER)
    scoring = body.index(resource_scout_module.REPO_QUALITY_SCORING_SECTION[:20])
    assert step3 < probe < scoring
    for keyword in ("deepxiv github_url", "Web Search", "check_url_reachable_tool"):
        assert keyword in body, keyword


def test_s7_07_round_budget_raised_to_30():
    """S7-07（2026-07-29 Maria 拍板）合法推翻 AC-S7-20 的"轮次预算零退化"分句：

    环境探测由"可选补充步"改为"必做步骤"，配套把 resource_scout 轮次上限 20 -> 30，
    给探测留出宽裕余量。**断言只换不弱化**：仍为精确 `==`，不改成范围/下界断言。
    AC-S7-20 的其余分句（跨论文 SystemMessage 字节一致、新增文案无插值痕迹）不受影响。
    """
    assert config.REACT_MAX_ROUNDS_RESOURCE_SCOUT == 30


# ===========================================================================
# S7-08（T-S7-5-6 / T-S7-5-7）：探测摘要上限与 6 项必探维度
#   —— CP-5.6-2 / CP-5.6-3 / CP-5.6-4 / CP-5.7-1 / CP-5.7-4
# ===========================================================================


def test_s708_probe_output_cap_covers_return_side_hard_bound():
    """CP-5.6-2（架构 §18.7(4) + dev-plan §40 P-12）：**关系断言，不断言字面量 2600**。

    返回端 ``sandbox/local_venv.py`` 截断后**前置一行 marker**，故真实硬上界是
    ``_PROBE_OUTPUT_MAX_BYTES + len(marker)``（2542）而非 2500 —— 按架构原文字面写
    ``>= 2500`` 时把渲染端上限调到 2520 仍能过、而关键包已可能被切（R-S7-45）。
    marker **无具名常量**（内联 f-string），故在测试内按 ``local_venv`` 同一 f-string
    **就地计算**（不新增生产常量）：S7-09 改返回端字节数时本断言自动跟随；marker
    本身被改写时，下面那条"f-string 仍在源码里"的断言会发现。
    """
    max_bytes = ept._PROBE_OUTPUT_MAX_BYTES
    marker = f"... [truncated, kept last {max_bytes} bytes] ...\n"

    src = inspect.getsource(lv._truncate_output)
    assert "[truncated, kept last {max_bytes} bytes]" in src, (
        "sandbox/local_venv.py 的截断 marker 已被改写——本关系断言里就地计算的 "
        "marker 长度已失真，须同步更新（dev-plan §40 P-12）"
    )

    assert resource_scout_module._PROBE_OUTPUT_MAX_CHARS >= max_bytes + len(marker), (
        "渲染端单条上限必须覆盖返回端单路硬上界（外层上限 >= 内层上限），"
        "否则返回端刻意保尾留下的 torch / transformers 被渲染端取头原样作废"
    )
    # 同一条结构性原则再上一层：整份总长上限是单条上限的外层。
    assert (
        resource_scout_module._PROBE_DIGEST_MAX_CHARS
        >= resource_scout_module._PROBE_OUTPUT_MAX_CHARS
    ), "整份总长上限必须 >= 单条上限（外层 >= 内层）"


def test_s708_digest_truncated_note_is_named_user_facing_constant():
    """CP-5.6-3 负向 + CP-5.6-4（dev-plan §40 P-13）：截尾说明是**模块级具名常量**的
    用户可见文案；未触顶时说明行不出现（零扰动）。

    S7-08 起 ``local_env_facts`` 经计划审核中断 payload 直达审核页只读展示块，
    用户会亲眼看到这句话 ⇒ 它是新增用户可见文案，必须具名（守门按名 import）
    且零内部术语。
    """
    note = resource_scout_module._PROBE_DIGEST_TRUNCATED_NOTE
    assert isinstance(note, str) and note.strip(), "截尾说明不得为空（清空即失去说明力）"

    # 复用既有守门口径 + 本节点内部标识：零内部术语泄漏
    assert _hits(note) == [], f"截尾说明泄漏内部枚举 / 节点名 / 技术术语：{_hits(note)}"
    for forbidden in (PROBE_TOOL_NAME, NODE_NAME, "digest", "probe",
                      "_PROBE_DIGEST_MAX_CHARS", "字节", "truncate"):
        assert forbidden.lower() not in note.lower(), forbidden
    assert not re.search(r"\d", note), "说明行不得写死字节数 / 阈值这类内部数字"

    # 未触顶：说明行不出现（零扰动）
    short = _digest_env_probe([_probe_msg("nvidia-smi -L", 0, stdout=_GPU_FACT)])
    assert short, "正常路径 digest 不应为空"
    assert note not in short, "未触顶时不得追加截尾说明（零扰动）"
    assert len(short) <= resource_scout_module._PROBE_DIGEST_MAX_CHARS


_SIX_REQUIRED_PROBE_COMMANDS = (
    "nvidia-smi",                 # GPU / 显存与占用
    "nvcc --version",             # CUDA
    "free -h",                    # 内存
    "df -h .",                    # 磁盘
    "python3 --version",          # Python 版本
    "pip list --format=freeze",   # 关键包版本
)


def test_s708_probe_section_lists_six_required_dimensions():
    """CP-5.7-1（AC-S7-41 / PRD §10.8 第 2 条）：探测段落列全 6 项必探维度，
    且"3~5 条"这类硬数字已删。

    背景：真跑实证只探到 GPU / CUDA / 磁盘三项，因为原段落只点名了这三项；
    而"一般探 3~5 条即可"与 AC-S7-25 原上界 ``≤5`` 是本次编造内存的共犯。

    **AC-S7-25 同步登记**：其上界已修订为 ``≤10``（PRD §10.8 第 2 条 / AC-S7-41），
    三条负向状态断言（未 force_finish / 未进 ``degraded_nodes`` / ``resource_strategy``
    未被改写为从零实现）**一字不动**。该 AC 是**真跑观测项**，代码侧无断言承载
    （S7-06 由主控用临时计数包装脚本执行），故本次代码侧无同步动作。
    """
    section = _new_prompt_text()
    for cmd in _SIX_REQUIRED_PROBE_COMMANDS:
        assert cmd in section, f"必探维度对应命令未写进探测段落：{cmd}"

    for token in ("3~5", "3-5", "3～5", "3 ~ 5"):
        assert token not in _SCOUT_BODY, f"探测节制不得再写成硬数字：{token}"

    # prompt 里点名的命令必须逐字在允许清单内，否则模型照做会被工具整条拒绝。
    for cmd in _SIX_REQUIRED_PROBE_COMMANDS:
        assert cmd in ept._PROBE_COMMANDS, f"{cmd} 不在允许清单内（照 prompt 发必被拒）"


def test_s708_ac_s7_41_digest_records_command_even_when_unavailable():
    """CP-5.7-4（架构 §18.5(2)）：AC-S7-41 判定口径 = **digest 中存在该命令的记录**，
    而非"出现该维度的数值"。

    本机缺 ``free`` 时 digest 只会写"该命令在本机不可用"——按数值判定则该 AC
    **永远不过且无法修**（``env_probe_tool.py`` 已被 S7-08 零改动红线冻结）。
    """
    history = [
        _probe_msg("nvidia-smi", 0, stdout=_GPU_FACT),
        _probe_msg("nvcc --version", 1, stdout="Cuda compilation tools, release 12.1"),
        # 本机缺 free：命令记录仍须在，只是输出归一为"该命令在本机不可用"
        _probe_msg("free -h", 2, exit_code=-1,
                   stderr="subprocess start failed: no such file"),
        _probe_msg("df -h .", 3, stdout="Filesystem  Size  Avail\nsda1  278G  241G"),
        _probe_msg("python3 --version", 4, stdout="Python 3.11.9"),
        _probe_msg("pip list --format=freeze", 5,
                   stdout="torch==2.3.0\ntransformers==4.41.0"),
    ]
    digest = _digest_env_probe(history)
    for cmd in _SIX_REQUIRED_PROBE_COMMANDS:
        assert f"$ {cmd}" in digest, f"必探维度 {cmd} 的命令记录未进 digest"
    assert "该命令在本机不可用" in digest, (
        "命令不可用时仍须留下记录（这正是判定口径不能按数值断的原因）"
    )
