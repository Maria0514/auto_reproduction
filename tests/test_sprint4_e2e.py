"""Sprint 4 任务 G2：e2e（零配额部分）—— mock 层三 interrupt 串行 + 哨兵脱敏 grep
+ 凭证注入 subprocess spy + 真实链路转正骨架。

覆盖 dev-plan §4 任务 G2 自测检查点的零配额子集：
    CP-G2-1（AC-S4-13）：mock LLM + **真实 build_graph()** + 真实 SqliteSaver(WAL)，
        同一 thread_id 下依次触发并恢复三类 interrupt：
            planning interrupt#1 → coding 内 request_user_input interrupt#3
            → 修复循环触顶 dev_loop_failure interrupt#2（MAX_FIX_LOOP_COUNT patch 为 1，
              保留「触顶」语义同时压缩链路长度）
        断言 payload 契约 / resume 路由 / 互不串扰 / 副作用幂等，连跑 3 次零抖动。
    CP-G2-2（AC-S4-11）：哨兵值 ``FAKE-TOKEN-sp4-e2e-*``（非真凭证）经
        request_user_input（is_sensitive=True, remember=True）进入系统，跑完整
        mock 修复循环（fetch 认证失败 → interrupt#3 → 同 argv 重试成功 → train
        import 失败 → 修复回合 → 成功收尾 → reporting），然后 grep 四落点：
            ① 工作目录生成代码 ② checkpoint DB 文件字节 ③ 报告 Markdown ④ caplog
        `.secrets` / GIT_ASKPASS 脚本存明文是设计内（0600/0700+gitignore），
        不在落点清单，但断言其权限位。
    CP-G2-3（AC-S4-08）：spy 断言凭证经 .secrets → build_credential_env →
        extra_env → run_in_venv → sandbox `_run_subprocess` → **subprocess.Popen
        env** 全链路透传（patch subprocess 层查 env）；另落真实链路转正骨架
        （@pytest.mark.e2e + 凭证 skipif，真跑待 Maria 授权，本次仅 --collect-only
        验证收集，绝不真跑）。

范式沿用（B2/C2/E4 实证纪律）：
    - 脚本 LLM 为 BaseChatModel 纯数据字段（msgpack round-trip 安全），路由完全
      基于输入 messages（ToolMessage 计数 + HumanMessage 内容），replay 安全；
    - request_user_input 单独一轮调用（B2 断言点 2：同批混调会整体重放）；
    - 闭包收集器 resume 后丢 pre-interrupt 值 → 副作用观测走模块级 dict + 磁盘
      双通道（R-S4-10）；
    - 所有 mock 场景零 API 配额（真实凭证仅由 e2e 骨架消费，且本次不跑）。

已知缺口处置记录（2026-07-06 探针实证 → 同日修复/裁决闭环，原 strict xfail 均已摘标）：
    - F1 / BUG-S4-G2-01（**已修复转正 2026-07-06，主控直修**）：原缺口 =
      `_map_execution_result` 把 `feedback.representative_stderr` 原文（未过
      mask_value）写进 NodeError.error_detail → 入 GlobalState / checkpoint。
      修复 = execution.py 全部 5 处 tainted summary/stderr 落 state/日志点统一
      mask_value（errors 列表 / NodeError message+detail / warning 日志 /
      FixLoopRecord.error_summary）。state 投影用例已摘 xfail 转回归锚点。
    - F2 / ADJ-S4-G2-02（**已裁决 2026-07-06，方案 (a+)：降格为已知限制**）：
      request_user_input 的敏感 resume 值以明文进入 checkpoint DB——探针定位
      两条机制固有通道：子图 ns `execution:<task>` 的 messages channel（工具
      返回值 ToolMessage）与 root/子图 ns 的 `__resume__` channel（resume
      Command 值）。裁决论证：能读 DB 的主体同样能读明文 `.secrets`，DB 级
      脱敏净收益≈零；mask serde 破坏 checkpoint 往返一致性（B2 实证 messages
      回读真值），永久排除；引用键传值留作多用户部署升级路径。加固 =
      checkpoints.db chmod 0600（主控落地）+ interaction_tools docstring /
      architecture 勘误（主控落地）。DB 用例已改写为 characterization
      （`test_cp_g2_2_sentinel_checkpoint_db_known_limitation`：锚定现状 +
      SQL 级白名单收窄，白名单外命中即翻红升级）。

运行方式：
    .venv/bin/pytest tests/test_sprint4_e2e.py -v                 # mock 层（默认）
    .venv/bin/pytest tests/test_sprint4_e2e.py --collect-only -q -m e2e
        # 仅验证真实链路骨架被收集；真跑须 Maria 明确授权后由主控执行
"""

from __future__ import annotations

import importlib
import json
import logging
import os
import sqlite3
import stat
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import config  # noqa: E402
import core.graph as graph_module  # noqa: E402
import sandbox.local_venv as lv  # noqa: E402
from core import react_base, secrets_store  # noqa: E402
from core.graph import build_graph  # noqa: E402
from core.state import GlobalState  # noqa: E402
from core.tools.interaction_tools import INTERRUPT_KIND_USER_INPUT  # noqa: E402
from sandbox.local_venv import SandboxPrepareResult, SandboxRunResult  # noqa: E402

execution_module = importlib.import_module("core.nodes.execution")
coding_module = importlib.import_module("core.nodes.coding")
planning_module = importlib.import_module("core.nodes.planning")
reporting_module = importlib.import_module("core.nodes.reporting")

INTERRUPT_KIND_DEV_LOOP = execution_module.INTERRUPT_KIND  # "dev_loop_failure"
INTERRUPT_KIND_PLANNING = "planning"

PAPER_ARXIV_ID = "2405.14831"  # HippoRAG（e2e 骨架靶论文，deepxiv 大概率已缓存）

# 哨兵值（dev-plan §G2：FAKE-TOKEN-sp4-e2e 形态，绝非真凭证）。
_SENTINEL = "FAKE-TOKEN-sp4-e2e-sentinel-9f3d7c2a"
_GIT_TOKEN = "FAKE-TOKEN-sp4-e2e-git-1a2b3c4d"
_HF_TOKEN = "FAKE-TOKEN-sp4-e2e-hf-5e6f7a8b"

# 进程级副作用观测通道（跨 resume 重跑节点体存活，R-S4-10：闭包计数不可靠）。
_WRITE_CALLS: Dict[str, int] = {"n": 0}


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_process_state():
    secrets_store._SENSITIVE_VALUES.clear()
    _WRITE_CALLS["n"] = 0
    yield
    secrets_store._SENSITIVE_VALUES.clear()
    _WRITE_CALLS["n"] = 0


def _isolate_workspace(monkeypatch, ws: Path) -> None:
    """WORKSPACE_DIR 全落点隔离（C2 范式）：config（.secrets / askpass）+ sandbox
    越界基准 + code_fs 越界基准 + coding 目录解析。"""
    from core.tools import code_fs_tools

    ws.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(config, "WORKSPACE_DIR", ws)
    monkeypatch.setattr(lv, "WORKSPACE_DIR", ws)
    monkeypatch.setattr(code_fs_tools, "WORKSPACE_DIR", ws)
    monkeypatch.setattr(coding_module, "WORKSPACE_DIR", ws)


# ---------------------------------------------------------------------------
# mock 脚手架：inert 工具工厂 / 上游 fake / CountingRunner / 计数 write 工具
# ---------------------------------------------------------------------------


def _install_inert_coding_tools(monkeypatch) -> None:
    """coding 的 deepxiv 工具工厂换惰性 mock（零网络 / 零 token 依赖，C2 范式）。"""

    @tool
    def read_section(arxiv_id: str, section_name: str) -> str:
        """Mock read_section."""
        return f"section {section_name}"

    @tool
    def web_search(query: str) -> str:
        """Mock web_search."""
        return "search result"

    monkeypatch.setattr(coding_module, "read_section_tool", lambda *a, **k: read_section)
    monkeypatch.setattr(coding_module, "web_search_tool", lambda *a, **k: web_search)


def _install_inert_planning_tools(monkeypatch) -> None:
    """planning 的 5 个工具工厂换惰性 mock（脚本 LLM 零工具调用，仅防工厂期外呼）。"""

    @tool
    def read_section(arxiv_id: str, section_name: str) -> str:
        """Mock read_section."""
        return "section"

    @tool
    def get_paper_structure(arxiv_id: str) -> str:
        """Mock get_paper_structure."""
        return "structure"

    @tool
    def web_search(query: str) -> str:
        """Mock web_search."""
        return "result"

    @tool
    def check_url_reachable_tool(url: str) -> str:
        """Mock check_url_reachable."""
        return "reachable"

    @tool
    def git_clone_and_analyze(repo_url: str) -> str:
        """Mock git_clone_and_analyze."""
        return "{}"

    monkeypatch.setattr(planning_module, "read_section_tool", lambda *a, **k: read_section)
    monkeypatch.setattr(
        planning_module, "get_paper_structure_tool", lambda *a, **k: get_paper_structure)
    monkeypatch.setattr(planning_module, "web_search_tool", lambda *a, **k: web_search)
    monkeypatch.setattr(
        planning_module, "make_check_url_reachable_tool",
        lambda *a, **k: check_url_reachable_tool)
    monkeypatch.setattr(
        planning_module, "make_git_clone_and_analyze_tool",
        lambda *a, **k: git_clone_and_analyze)


def _patch_upstream_fakes(monkeypatch, *, fake_planning_plan: Optional[Dict[str, Any]] = None):
    """patch graph_module 上游 3 节点为 fake（sp3 F2 范式）；planning 默认保持**真实**
    （CP-G2-1 需要真实 interrupt#1），传 fake_planning_plan 时替换为直接 approve 的
    fake（CP-G2-2 聚焦脱敏，不需要 interrupt#1）。"""

    def fake_intake(state):
        return {
            "paper_meta": {"arxiv_id": PAPER_ARXIV_ID, "title": "G2 mock paper"},
            "current_step": "paper_intake",
        }

    def fake_analysis(state):
        return {
            "paper_analysis": {
                "method_summary": "mock 方法概述",
                "method_summary_en": "mock method",
                "metrics": ["accuracy"],
                "baseline_results": {"accuracy": 0.91},
                "datasets": ["mock-ds"],
                "framework": "PyTorch",
            },
            "current_step": "paper_analysis",
        }

    def fake_scout(state):
        return {"resource_info": {"selected_repo": None}, "current_step": "resource_scout"}

    monkeypatch.setattr(graph_module, "paper_intake", fake_intake)
    monkeypatch.setattr(graph_module, "paper_analysis", fake_analysis)
    monkeypatch.setattr(graph_module, "resource_scout", fake_scout)

    if fake_planning_plan is not None:
        def fake_planning_node(state):
            return {
                "reproduction_plan": {**fake_planning_plan, "approved": True},
                "current_step": "planning",
            }

        monkeypatch.setattr(graph_module, "planning", fake_planning_node)


class CountingRunner:
    """run_in_venv mock（E4 范式）：按命令关键 token 分桶计数；spec 支持 dict（每次
    同一结果）或 list[dict]（第 N 次尝试取第 N 个，越界取末个）。跨 interrupt/resume
    持久（模块级 patch 同一实例），计数覆盖整个 graph 生命周期。"""

    def __init__(self, specs: Dict[str, Any]) -> None:
        self.specs = specs
        self.counts: Dict[str, int] = {}
        self.calls: List[List[str]] = []
        self.last_extra_env: Optional[Dict[str, str]] = None

    def __call__(self, python_exe, command, work_dir, *a, **k):
        self.calls.append(list(command))
        self.last_extra_env = dict(k.get("extra_env") or {})
        spec: Dict[str, Any] = {}
        for token, s in self.specs.items():
            if any(token in str(c) for c in command):
                self.counts[token] = self.counts.get(token, 0) + 1
                if isinstance(s, list):
                    spec = s[min(self.counts[token] - 1, len(s) - 1)]
                else:
                    spec = s
                break
        return SandboxRunResult(
            exit_code=spec.get("exit_code", 0),
            stdout=spec.get("stdout", ""),
            stderr=spec.get("stderr", ""),
            duration_seconds=0.1,
            timed_out=False,
            output_truncated=False,
            command=list(command),
        )


def _install_counting_write_tool(monkeypatch, disk_log: Path) -> None:
    """coding write 工具工厂换计数包装（委托真实工具；模块级 dict + 磁盘双通道，
    C2 范式，跨 resume 重建存活）。"""
    from core.tools.code_fs_tools import make_write_code_file_tool as real_factory

    def counting_factory(base_dir: Optional[str] = None):
        real_tool = real_factory(base_dir=base_dir)

        @tool
        def write_code_file(path: str, content: str) -> str:
            """写入代码文件（真实工具的计数探针包装）。"""
            _WRITE_CALLS["n"] += 1
            with open(disk_log, "a", encoding="utf-8") as f:
                f.write(path + "\n")
            return real_tool.invoke({"path": path, "content": content})

        return write_code_file

    monkeypatch.setattr(coding_module, "make_write_code_file_tool", counting_factory)


# ---------------------------------------------------------------------------
# DispatchScriptLLM：节点感知脚本 LLM（planning / coding / execution 分发）
# ---------------------------------------------------------------------------


class DispatchScriptLLM(BaseChatModel):
    """按 SystemMessage 身份短语分发到对应节点脚本（B2/E4 范式合体）。

    纯数据字段（msgpack round-trip 安全）；路由完全基于输入 messages（ToolMessage
    计数 + HumanMessage 是否含 "fix_round"），replay 安全。scenario：
        "chain"    —— CP-G2-1 三 interrupt 串行（coding 内 interrupt#3 非敏感）；
        "sentinel" —— CP-G2-2 哨兵链（execution 内 interrupt#3 敏感 + remember）。
    """

    scenario: str

    @property
    def _llm_type(self) -> str:
        return "g2-dispatch-scripted"

    def bind_tools(self, tools: Any, **kwargs: Any) -> "DispatchScriptLLM":
        return self

    # ---- helpers ----

    @staticmethod
    def _count_tool(messages, name: str) -> int:
        return sum(
            1 for m in messages
            if isinstance(m, ToolMessage) and getattr(m, "name", None) == name
        )

    @staticmethod
    def _first_human_text(messages) -> str:
        for m in messages:
            if isinstance(m, HumanMessage):
                c = m.content
                return c if isinstance(c, str) else str(c)
        return ""

    @staticmethod
    def _call(name: str, args: Dict[str, Any], cid: str) -> AIMessage:
        return AIMessage(content="", tool_calls=[
            {"name": name, "args": args, "id": cid, "type": "tool_call"},
        ])

    @staticmethod
    def _final(payload: Dict[str, Any]) -> AIMessage:
        body = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        return AIMessage(content=(
            f"{config.REACT_RESULT_TAG_OPEN}{body}{config.REACT_RESULT_TAG_CLOSE}"))

    # ---- per-node scripts ----

    def _planning_step(self, messages) -> AIMessage:
        return self._final({
            "plan_summary": "从零实现策略复现主要指标（G2 脚本计划）",
            "environment": {"python": "3.11"},
            "data_preparation": ["下载 mock 数据集"],
            "code_strategy": "from_scratch",
            "execution_steps": [
                {"step_name": "train", "command": "python train.py",
                 "expected_output": "metrics"},
            ],
            "expected_results": {"accuracy": 0.9},
            "estimated_time": "1h",
            "deliverables": ["train.py"],
        })

    def _coding_step(self, messages) -> AIMessage:
        human = self._first_human_text(messages)
        repair = '"fix_round"' in human
        n_write = self._count_tool(messages, "write_code_file")
        n_rui = self._count_tool(messages, "request_user_input")

        if repair:
            if n_write == 0:
                # 忠实复读器（leak 探针）：把观察到的修复上下文（含 last_error_summary
                # 的 stderr_tail，机制上应已脱敏）原样写进代码注释——若上游任一 mask
                # 环节失效，哨兵值会经此进入生成代码，被 CP-G2-2 grep 捕获。
                echo = human.replace("\n", " ")
                content = (
                    "# G2 repair round: fix ModuleNotFoundError\n"
                    f"# OBSERVED_CONTEXT_ECHO: {echo}\n"
                    "print('fixed')\n"
                )
                return self._call(
                    "write_code_file", {"path": "train.py", "content": content},
                    "call_w_fix")
            return self._final({
                "files_written": ["train.py"], "entry_script": "train.py",
                "summary": "修复回合：针对 import 错误最小修改", "notes": None,
            })

        if n_write == 0:
            return self._call(
                "write_code_file",
                {"path": "train.py", "content": "print('v1')\n"},
                "call_w_1")
        if self.scenario == "chain" and n_rui == 0:
            # 单独一轮调用（B2 缓解 2 纪律：不与其他工具混一轮）。
            return self._call(
                "request_user_input",
                {"question": "训练需要 batch_size 参数，请提供",
                 "is_sensitive": False, "purpose_key": ""},
                "call_rui_c")
        last_tool = [m for m in messages if isinstance(m, ToolMessage)]
        note = str(last_tool[-1].content) if last_tool else ""
        return self._final({
            "files_written": ["train.py"], "entry_script": "train.py",
            "summary": f"生成 train.py（补充信息: {note[:60]}）", "notes": None,
        })

    def _execution_step(self, messages) -> AIMessage:
        human = self._first_human_text(messages)
        repair = '"fix_round"' in human
        n_run = self._count_tool(messages, "run_in_sandbox")
        n_rui = self._count_tool(messages, "request_user_input")

        if self.scenario == "sentinel" and not repair:
            # fetch(认证失败) → request_user_input(敏感,单独一轮) → fetch 同 argv 重试
            # → train(import 失败) → 收尾。
            if n_run == 0:
                return self._call("run_in_sandbox", {"command": "python fetch.py"}, "c_r1")
            if n_run == 1 and n_rui == 0:
                return self._call("request_user_input", {
                    "question": "克隆私有仓库需要 GitHub 访问令牌，请提供",
                    "is_sensitive": True,
                    "purpose_key": "git_credential:github.com",
                }, "c_rui")
            if n_run == 1:
                return self._call("run_in_sandbox", {"command": "python fetch.py"}, "c_r2")
            if n_run == 2:
                return self._call("run_in_sandbox", {"command": "python train.py"}, "c_r3")
            return self._final({
                "steps_attempted": 3, "all_exit_zero": False,
                "summary": "凭证到手重试成功，train 仍失败", "notes": None,
            })

        # chain / spy / sentinel 修复回合：单命令 train → 收尾。
        if n_run == 0:
            return self._call("run_in_sandbox", {"command": "python train.py"}, "c_r1")
        return self._final({
            "steps_attempted": 1, "all_exit_zero": bool(repair),
            "summary": "执行 train 完成", "notes": None,
        })

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        sys_text = str(messages[0].content) if messages else ""
        if "论文复现规划专家" in sys_text:
            ai = self._planning_step(messages)
        elif "资深机器学习复现工程师" in sys_text:
            ai = self._coding_step(messages)
        elif "复现执行工程师" in sys_text:
            ai = self._execution_step(messages)
        else:  # pragma: no cover - 脚本分发防御
            raise AssertionError(f"未知 system prompt，无法分发脚本: {sys_text[:60]!r}")
        return ChatResult(generations=[ChatGeneration(message=ai)])


def _wire_llms(monkeypatch, llm: DispatchScriptLLM, runner: CountingRunner) -> None:
    """LLM / sandbox 注入：react_base（planning+coding wrapper）与 execution 模块
    双落点 create_llm；execution 模块 run_in_venv / collect_artifacts / 档 3 抽取。"""
    monkeypatch.setattr(react_base, "create_llm", lambda cfg: llm)
    monkeypatch.setattr(execution_module, "create_llm", lambda cfg: llm)
    monkeypatch.setattr(execution_module, "run_in_venv", runner)
    monkeypatch.setattr(execution_module, "collect_artifacts", lambda *a, **k: [])
    monkeypatch.setattr(execution_module, "_llm_extract_metrics", lambda *a, **k: ({}, 0))


# ---------------------------------------------------------------------------
# 通用 harness
# ---------------------------------------------------------------------------


def _make_wal_saver(db_path: Path) -> Tuple[sqlite3.Connection, SqliteSaver]:
    """WAL 模式 SqliteSaver（与 core/checkpointer.py / sp2 c1_e2e 范式一致）。"""
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    return conn, SqliteSaver(conn)


def _interrupt_values(graph, cfg) -> List[Dict[str, Any]]:
    snap = graph.get_state(cfg)
    return [
        iv.value for task in (snap.tasks or [])
        for iv in (getattr(task, "interrupts", None) or [])
    ]


def _initial_graph_state(ws: Path) -> Dict[str, Any]:
    return {
        "user_input": PAPER_ARXIV_ID,
        "workspace_dir": str(ws),
        "llm_config_set": {
            "default": {
                "base_url": "https://example.test/v1",
                "model": "scripted-model",
                "api_key": "sk-test",
                "temperature": 0.3,
                "max_tokens": 1024,
            },
            "overrides": {},
        },
        "node_errors": [],
        "degraded_nodes": [],
        "fix_loop_history": [],
        "fix_loop_count": 0,
        "retry_budget_remaining": 40,
        "_dev_loop_llm_calls": 0,
        "_dev_loop_route": None,
        "execution_result": None,
        "messages": [],
    }


def _prepare_code_dir(ws: Path) -> Path:
    """预置 code_output_dir + .venv 痕迹（run_in_sandbox python_exe 确定性推导路径，
    E4 范式；剧本无需 prepare_environment，resume 重建收集器后仍可解析解释器）。"""
    code_dir = ws / PAPER_ARXIV_ID / "code"
    (code_dir / ".venv" / "bin").mkdir(parents=True, exist_ok=True)
    (code_dir / ".venv" / "pyvenv.cfg").write_text("home = /usr\n")
    return code_dir


def _read_dir_files(root: Path) -> Dict[str, bytes]:
    """递归读目录下全部文件字节（grep 落点 ① 用）。"""
    out: Dict[str, bytes] = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            out[str(p)] = p.read_bytes()
    return out


# ===========================================================================
# CP-G2-1 场景 harness：三 interrupt 串行（真实 build_graph + 真实 SqliteSaver）
# ===========================================================================


def _run_three_interrupt_chain(monkeypatch, tmp_path: Path, tag: str) -> Dict[str, Any]:
    """同一 thread_id 依次：interrupt#1(planning) → approve → interrupt#3(coding)
    → resume 值 → 修复循环（fail → fix#1 → fail → 触顶）→ interrupt#2(execution)
    → export_code → reporting → END。返回全部观测量。"""
    _WRITE_CALLS["n"] = 0
    ws = tmp_path / f"ws_{tag}"
    _isolate_workspace(monkeypatch, ws)
    _install_inert_coding_tools(monkeypatch)
    _install_inert_planning_tools(monkeypatch)
    _patch_upstream_fakes(monkeypatch)  # planning 保持真实（interrupt#1 真触发）

    runner = CountingRunner({
        "train.py": {"exit_code": 1,
                     "stderr": "ModuleNotFoundError: No module named 'torchx'"},
    })
    _wire_llms(monkeypatch, DispatchScriptLLM(scenario="chain"), runner)
    # 触顶语义保留、链路压缩：MAX_FIX_LOOP_COUNT=1 → 恰 1 个真实修复回合后触顶。
    monkeypatch.setattr(execution_module, "MAX_FIX_LOOP_COUNT", 1)
    disk_log = tmp_path / f"writes_{tag}.log"
    _install_counting_write_tool(monkeypatch, disk_log)
    code_dir = _prepare_code_dir(ws)

    db_path = tmp_path / f"chain_{tag}.db"
    conn, saver = _make_wal_saver(db_path)
    obs: Dict[str, Any] = {"code_dir": code_dir, "runner": runner, "db_path": db_path}
    try:
        graph = build_graph(checkpointer=saver)
        cfg = {"configurable": {"thread_id": f"g2-chain-{tag}-{uuid.uuid4().hex[:8]}"}}

        out1 = graph.invoke(_initial_graph_state(ws), cfg)
        obs["pause1_out"] = out1
        obs["pause1_ivs"] = _interrupt_values(graph, cfg)
        obs["pause1_next"] = graph.get_state(cfg).next

        out2 = graph.invoke(Command(resume={"decision": "approve"}), cfg)
        obs["pause2_out"] = out2
        obs["pause2_ivs"] = _interrupt_values(graph, cfg)
        obs["pause2_next"] = graph.get_state(cfg).next
        obs["writes_at_pause2"] = _WRITE_CALLS["n"]

        out3 = graph.invoke(Command(resume={"value": "128", "remember": False}), cfg)
        obs["pause3_out"] = out3
        obs["pause3_ivs"] = _interrupt_values(graph, cfg)
        obs["pause3_next"] = graph.get_state(cfg).next
        obs["pause3_values"] = graph.get_state(cfg).values
        obs["run_counts_at_pause3"] = dict(runner.counts)

        final = graph.invoke(Command(resume={"decision": "export_code"}), cfg)
        obs["final_out"] = final
        obs["final_next"] = graph.get_state(cfg).next
        obs["final_values"] = graph.get_state(cfg).values
        obs["writes_final"] = _WRITE_CALLS["n"]
        obs["disk_lines"] = (disk_log.read_text(encoding="utf-8").splitlines()
                             if disk_log.exists() else [])
        obs["kinds"] = [
            obs["pause1_ivs"][0]["interrupt_kind"] if obs["pause1_ivs"] else None,
            obs["pause2_ivs"][0]["interrupt_kind"] if obs["pause2_ivs"] else None,
            obs["pause3_ivs"][0]["interrupt_kind"] if obs["pause3_ivs"] else None,
        ]
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return obs


def test_cp_g2_1_three_interrupts_serial_same_thread(monkeypatch, tmp_path):
    """AC-S4-13 主证：同 thread 三类 interrupt 依次触发、payload 契约正确、
    resume 后路由正确、互不串扰。"""
    obs = _run_three_interrupt_chain(monkeypatch, tmp_path, "main")

    # —— 三类 interrupt 依次、各恰一个、种类互不串扰。
    assert obs["kinds"] == [
        INTERRUPT_KIND_PLANNING, INTERRUPT_KIND_USER_INPUT, INTERRUPT_KIND_DEV_LOOP,
    ], f"interrupt 种类序列不符: {obs['kinds']}"
    for key in ("pause1_ivs", "pause2_ivs", "pause3_ivs"):
        assert len(obs[key]) == 1, f"{key} 应恰 1 个 pending interrupt: {obs[key]}"

    # —— pause#1（planning interrupt#1）payload 契约 + 暂停位置。
    assert "__interrupt__" in obs["pause1_out"]
    assert obs["pause1_next"] == ("planning",)
    p1 = obs["pause1_ivs"][0]
    assert set(p1.keys()) == {
        "interrupt_kind", "reproduction_plan", "resource_info",
        "paper_analysis_summary", "degraded_nodes", "node_errors", "revise_count",
        "soft_hint_threshold", "max_total_llm_calls", "switch_repo_failed",
    }, f"interrupt#1 payload 键集不符: {sorted(p1.keys())}"
    assert p1["revise_count"] == 0
    plan = p1["reproduction_plan"]
    assert plan.get("plan_summary") and plan.get("code_strategy") == "from_scratch"

    # —— pause#2（coding interrupt#3）四键契约 + 暂停位置 + 不携带其它类 payload 残留。
    assert "__interrupt__" in obs["pause2_out"]
    assert obs["pause2_next"] == ("coding",)
    p2 = obs["pause2_ivs"][0]
    assert set(p2.keys()) == {"interrupt_kind", "question", "is_sensitive", "purpose_key"}, \
        f"interrupt#3 payload 应恰四键（§7.1）: {sorted(p2.keys())}"
    assert p2["question"] == "训练需要 batch_size 参数，请提供"
    assert p2["is_sensitive"] is False
    assert p2["purpose_key"] is None, "空串 purpose_key 应规整为 None"

    # —— pause#3（execution interrupt#2）payload 契约（触顶：auto_fixable=True + 修复耗尽）。
    assert "__interrupt__" in obs["pause3_out"]
    assert obs["pause3_next"] == ("execution",)
    p3 = obs["pause3_ivs"][0]
    assert set(p3.keys()) == {
        "interrupt_kind", "fix_loop_count", "error_category", "error_summary",
        "fix_hint", "auto_fixable", "fix_loop_history", "execution_errors",
        "representative_stderr", "options",
    }, f"interrupt#2 payload 键集不符: {sorted(p3.keys())}"
    assert p3["error_category"] == "import"
    assert p3["auto_fixable"] is True, "触顶（修复耗尽）场景 error 本身仍是可修复类"
    assert p3["fix_loop_count"] == 1, "MAX_FIX_LOOP_COUNT=1 触顶时恰 1 个修复回合"
    assert p3["options"] == ["terminate", "revise_plan", "export_code"]
    # 触顶前 self-loop commit 边界已置位（L-C3-01 命门旁证）。
    assert obs["pause3_values"].get("_dev_loop_route") == "await_dev_loop_interrupt"

    # —— resume 路由正确：export_code → reporting → END。
    assert "__interrupt__" not in obs["final_out"], "第三次 resume 后不得再暂停"
    assert obs["final_next"] == (), f"export_code 后应到 END: {obs['final_next']}"
    fv = obs["final_values"]
    assert fv.get("user_fix_decision") == "export_code"
    assert "execution" in (fv.get("degraded_nodes") or [])
    report_path = fv.get("report_path")
    assert report_path and Path(report_path).exists(), \
        f"export_code 应产出降级报告: {report_path}"


def test_cp_g2_1_side_effects_and_repair_loop_across_chain(monkeypatch, tmp_path):
    """AC-S4-14 链内复证：interrupt#3 resume 不重放 write（coding#1 恰 1 次）、
    修复回合恰 1 次（共 2 次 write）；sandbox 恰 2 跑（guard 重入 / interrupt#2
    resume 均不重跑）；fix_loop_history 恰 1 条 import 记录；预算单调扣减。"""
    obs = _run_three_interrupt_chain(monkeypatch, tmp_path, "sfx")

    # write 双通道：pause#2 时恰 1（pre-interrupt 独立轮次）；终态恰 2（+修复回合 1 次）。
    assert obs["writes_at_pause2"] == 1, \
        f"interrupt#3 暂停时 write 应恰 1 次: {obs['writes_at_pause2']}"
    assert obs["writes_final"] == 2, \
        f"全链 write 应恰 2 次（首轮+修复轮，resume 不重放）: {obs['writes_final']}"
    assert obs["disk_lines"] == ["train.py", "train.py"]
    # 修复轮内容为最后一次写入（覆盖写幂等末态）。
    content = (obs["code_dir"] / "train.py").read_text(encoding="utf-8")
    assert "repair round" in content

    # sandbox：exec#1 + exec#2 恰 2 跑；guard 重入与 interrupt#2 resume 零新增。
    assert obs["run_counts_at_pause3"] == {"train.py": 2}
    assert obs["runner"].counts == {"train.py": 2}, \
        f"interrupt#2 resume 后 sandbox 不得重跑: {obs['runner'].counts}"

    fv = obs["final_values"]
    history = fv.get("fix_loop_history") or []
    assert len(history) == 1 and history[0]["error_category"] == "import"
    assert fv.get("fix_loop_count") == 1
    # 预算/子预算发生扣减（精确额度由 CP-E3-1 覆盖，此处验证链路生效）。
    assert fv.get("retry_budget_remaining", 40) < 40
    assert fv.get("_dev_loop_llm_calls", 0) > 0


def test_cp_g2_1_chain_stable_across_3_runs(monkeypatch, tmp_path):
    """CP-G2-1 稳定性纪律：全链连跑 3 次结论一致（interrupt/重跑幂等类判据）。"""
    for i in range(3):
        obs = _run_three_interrupt_chain(monkeypatch, tmp_path, f"stab{i}")
        assert obs["kinds"] == [
            INTERRUPT_KIND_PLANNING, INTERRUPT_KIND_USER_INPUT, INTERRUPT_KIND_DEV_LOOP,
        ], f"run#{i}: interrupt 序列漂移: {obs['kinds']}"
        assert obs["writes_final"] == 2, f"run#{i}: write 副作用漂移"
        assert obs["runner"].counts == {"train.py": 2}, f"run#{i}: sandbox 计数漂移"
        assert obs["final_next"] == (), f"run#{i}: 未到 END"
        assert "__interrupt__" not in obs["final_out"], f"run#{i}: 终态残留 interrupt"


# ===========================================================================
# CP-G2-2 场景 harness：哨兵值全链路（execution 内敏感 interrupt#3 + remember）
# ===========================================================================


_SENTINEL_PLAN = {
    "plan_summary": "含私有仓库拉取的复现计划（G2 哨兵链）",
    "code_strategy": "from_scratch",
    "execution_steps": [
        {"step_name": "fetch", "command": "python fetch.py"},
        {"step_name": "train", "command": "python train.py"},
    ],
    "deliverables": ["train.py"],
    "expected_results": {"accuracy": 0.9},
    "environment": {},
}


def _run_sentinel_scenario(monkeypatch, tmp_path: Path, tag: str) -> Dict[str, Any]:
    """哨兵链：coding#1 写码 → execution#1 fetch 认证失败 → interrupt#3（敏感）
    → resume(哨兵, remember=True) → 同 argv 重试成功（stdout 带原文哨兵）→ train
    import 失败（stderr 带原文哨兵）→ 修复回合（coding#2 忠实复读上下文进代码）
    → execution#2 train 成功 → reporting → END。"""
    ws = tmp_path / f"ws_{tag}"
    _isolate_workspace(monkeypatch, ws)
    _install_inert_coding_tools(monkeypatch)
    _patch_upstream_fakes(monkeypatch, fake_planning_plan=_SENTINEL_PLAN)

    runner = CountingRunner({
        "fetch.py": [
            {"exit_code": 128,
             "stderr": "fatal: could not read Username for 'https://github.com': "
                       "terminal prompts disabled"},
            {"exit_code": 0,
             "stdout": f"fetch ok Authorization: Bearer {_SENTINEL}"},
        ],
        "train.py": [
            {"exit_code": 1,
             "stderr": "Traceback (most recent call last):\n"
                       "ModuleNotFoundError: No module named 'torchx'\n"
                       f"(debug token={_SENTINEL})"},
            {"exit_code": 0, "stdout": '<METRICS>{"accuracy": 0.91}</METRICS>'},
        ],
    })
    _wire_llms(monkeypatch, DispatchScriptLLM(scenario="sentinel"), runner)
    code_dir = _prepare_code_dir(ws)

    db_path = tmp_path / f"sentinel_{tag}.db"
    conn, saver = _make_wal_saver(db_path)
    obs: Dict[str, Any] = {"ws": ws, "code_dir": code_dir, "runner": runner,
                           "db_path": db_path}
    try:
        graph = build_graph(checkpointer=saver)
        cfg = {"configurable": {"thread_id": f"g2-sent-{tag}-{uuid.uuid4().hex[:8]}"}}

        paused = graph.invoke(_initial_graph_state(ws), cfg)
        obs["pause_out"] = paused
        obs["pause_ivs"] = _interrupt_values(graph, cfg)

        final = graph.invoke(
            Command(resume={"value": _SENTINEL, "remember": True}), cfg)
        obs["final_out"] = final
        obs["final_next"] = graph.get_state(cfg).next
        obs["final_values"] = graph.get_state(cfg).values
        # 历史帧的 execution_result.logs（修复回合会覆盖 execution_result，
        # exec#1 的含哨兵行只存在于历史帧——mask 阳性对照的观测点）。
        obs["history_exec_logs"] = [
            (frame.values.get("execution_result") or {}).get("logs") or ""
            for frame in graph.get_state_history(cfg)
            if frame.values.get("execution_result")
        ]
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
            conn.commit()
        except Exception:
            pass
    finally:
        try:
            conn.close()
        except Exception:
            pass

    db_bytes = db_path.read_bytes() if db_path.exists() else b""
    for suffix in ("-wal", "-shm"):
        side = Path(str(db_path) + suffix)
        if side.exists():
            db_bytes += side.read_bytes()
    obs["db_bytes"] = db_bytes
    return obs


def test_cp_g2_2_sentinel_zero_plaintext_in_code_report_caplog(
    monkeypatch, tmp_path, caplog,
):
    """AC-S4-11 落点 ①③④：生成代码 / 报告 Markdown / caplog 全程零哨兵明文；
    且忠实复读器确实把（已脱敏的）执行反馈带进了代码（防"从未流经"假绿）。"""
    with caplog.at_level(logging.DEBUG):
        obs = _run_sentinel_scenario(monkeypatch, tmp_path, "sinks")

    # 前置健全：interrupt#3 敏感 payload 契约 + 链路成功闭环。
    p = obs["pause_ivs"][0]
    assert p["interrupt_kind"] == INTERRUPT_KIND_USER_INPUT
    assert p["is_sensitive"] is True
    assert p["purpose_key"] == "git_credential:github.com"
    assert _SENTINEL not in json.dumps(p, ensure_ascii=False, default=str), \
        "interrupt payload 不得含哨兵（值只在 resume 中出现）"
    assert obs["final_next"] == (), "哨兵链应成功闭环到 END"
    er = obs["final_values"]["execution_result"]
    assert er["success"] is True and er["metrics"] == {"accuracy": 0.91}

    # 落点 ①：工作目录生成代码零明文 + 复读器阳性对照（观察到脱敏后的错误上下文）。
    files = _read_dir_files(obs["code_dir"])
    sentinel_b = _SENTINEL.encode("utf-8")
    for path, blob in files.items():
        assert sentinel_b not in blob, f"生成代码含哨兵明文: {path}"
    train_txt = (obs["code_dir"] / "train.py").read_text(encoding="utf-8")
    assert "OBSERVED_CONTEXT_ECHO" in train_txt and "ModuleNotFoundError" in train_txt, \
        "复读器应把执行反馈带进代码（否则落点 ① 为假绿）"
    assert "****" in train_txt, "复读进代码的 stderr_tail 应携带 mask 占位符"

    # 落点 ③：报告 Markdown 零明文。
    report_path = obs["final_values"].get("report_path")
    assert report_path and Path(report_path).exists()
    assert _SENTINEL not in Path(report_path).read_text(encoding="utf-8"), \
        "报告 Markdown 含哨兵明文"

    # 落点 ④：caplog 全程零明文（生产 logger 纪律：只打 key / 摘要，不打值）。
    assert caplog.records, "caplog 应捕获到日志（空捕获=假绿）"
    assert _SENTINEL not in caplog.text, "日志含哨兵明文（AC-S4-12 违背）"


def test_cp_g2_2_mask_engaged_and_bydesign_stores(monkeypatch, tmp_path):
    """mask 阳性对照 + 设计内明文存储的权限位：execution_result.logs 中哨兵被替换
    为 ****（原文确经 runner 注入）；`.secrets` 0600 且含哨兵（remember=True 落盘，
    设计内）；GIT_ASKPASS 脚本 0700 且含哨兵（token 不进 env 值的间接注入，设计内）。"""
    obs = _run_sentinel_scenario(monkeypatch, tmp_path, "mask")

    # 终态 execution_result 是修复回合（exec#2 仅 train 成功）的结果；exec#1 的
    # 含哨兵行在历史帧——从 state history 取 mask 阳性对照。
    final_logs = obs["final_values"]["execution_result"]["logs"]
    assert _SENTINEL not in final_logs, "终态 execution_result.logs 含哨兵明文"
    evidence = [logs for logs in obs["history_exec_logs"] if "Bearer" in logs]
    assert evidence, "历史帧应含 exec#1 的 fetch 证据行（否则阳性对照失效）"
    for logs in evidence:
        assert _SENTINEL not in logs, "历史帧 execution_result.logs 含哨兵明文"
        assert "****" in logs, "logs 应含 mask 占位符（证明哨兵确实流经并被替换）"
        assert "exit=128" in logs, "logs 应保留失败证据（mask 只替换敏感值）"

    # `.secrets`：remember=True 落盘（设计内明文），0600 强制。
    secrets_path = obs["ws"] / config.SECRETS_FILE_NAME
    assert secrets_path.exists(), "remember=True 应落 .secrets"
    mode = stat.S_IMODE(secrets_path.stat().st_mode)
    assert mode == 0o600, f".secrets 权限应为 0600，实为 {oct(mode)}"
    entries = json.loads(secrets_path.read_text(encoding="utf-8"))
    assert entries["git_credential:github.com"] == {
        "value": _SENTINEL, "is_sensitive": True,
    }

    # GIT_ASKPASS 脚本：修复回合 build_credential_env 生成，0700（设计内明文）。
    askpass = obs["ws"] / ".git_askpass_github.com.sh"
    assert askpass.exists(), "含 git 凭证时修复回合应生成 GIT_ASKPASS 脚本"
    mode = stat.S_IMODE(askpass.stat().st_mode)
    assert mode == 0o700, f"GIT_ASKPASS 脚本权限应为 0700，实为 {oct(mode)}"
    assert _SENTINEL in askpass.read_text(encoding="utf-8")
    # 设计内明文文件都不在 code_output_dir（落点 ① 边界确认）。
    assert not str(askpass).startswith(str(obs["code_dir"])), "askpass 不应落在代码目录"


def test_cp_g2_2_sentinel_not_in_global_state_dump(monkeypatch, tmp_path):
    """AC-S4-11 GlobalState 投影：终态 state 全量序列化零哨兵明文。

    F1（BUG-S4-G2-01）已于 2026-07-06 修复转正（原 strict xfail 摘标）：
    `_map_execution_result` 等 5 处 tainted summary/stderr 落 state/日志点统一
    `mask_value`（errors 列表 / NodeError message+detail / warning 日志 /
    FixLoopRecord.error_summary）。本用例即回归锚点，再翻红即脱敏兜底退化。
    """
    obs = _run_sentinel_scenario(monkeypatch, tmp_path, "state")
    dump = json.dumps(obs["final_values"], ensure_ascii=False, default=str)
    offenders = [
        f"node_errors[{i}].{k}"
        for i, e in enumerate(obs["final_values"].get("node_errors") or [])
        for k in ("error_message", "error_detail")
        if isinstance(e, dict) and _SENTINEL in str(e.get(k) or "")
    ]
    assert _SENTINEL not in dump, (
        "GlobalState 终态序列化含哨兵明文（AC-S4-11 违背）；"
        f"已定位字段: {offenders or '（非 node_errors 通道，需进一步排查）'}"
    )


# F2 / ADJ-S4-G2-02 裁决内通道白名单（2026-07-06 架构裁决 (a+)）：敏感 resume 值
# 允许经且仅经这两类通道以明文入库（加固 = DB 文件 0600 + 文档修正，DB 级脱敏不做）。
_ADJUDICATED_LEAK_CHANNELS_DOC = (
    "① 内嵌子图 ns（如 execution:<task>）的 messages channel"
    "（request_user_input 返回值 ToolMessage——B2 实证 checkpoint 往返真值是 "
    "resume 精确恢复的机制前提，mask serde 永久排除）；"
    "② 任意 ns 的 __resume__ channel（Command(resume=...) pending write）"
)


def _is_adjudicated_leak_channel(ns: str, channel: str) -> bool:
    """判定 writes 表命中是否落在 ADJ-S4-G2-02 裁决内通道白名单。

    ns 为空串 = root ns（父图 GlobalState 通道）；root ns 的 messages 通道属父图
    业务字段（本项目恒空），**不在**白名单——只有内嵌子图 ns 的 messages 在裁决内。
    """
    if channel == "__resume__":
        return True  # 裁决内通道 ②（root 与子图 ns 均在裁决范围）
    if channel == "messages" and ns:
        return True  # 裁决内通道 ①（内嵌子图 ns 的消息历史）
    return False


def test_cp_g2_2_sentinel_checkpoint_db_known_limitation(monkeypatch, tmp_path):
    """AC-S4-11 落点 ② 收窄后的真规格 + 已知限制锚定（ADJ-S4-G2-02 裁决 (a+)）。

    裁决（2026-07-06 架构师，方案 (a+)）：checkpoint DB 字节级零明文**降格为已知
    限制**——能读 DB 的主体同样能读明文 `.secrets`，DB 级脱敏净收益≈零；mask
    serde 因破坏 checkpoint 往返一致性（B2 实证 messages 回读真值）永久排除；
    引用键传值留作多用户部署升级路径。加固 = checkpoints.db chmod 0600（主控
    落地）+ 文档修正。本用例由原 strict xfail 摘标改写为 characterization
    （断言粒度经架构师授权由测试代理定）。

    两条断言方向：
    1. **锚定现状**：DB 字节含哨兵——若未来 langgraph 序列化/checkpoint 行为
       变化使其消失，本断言翻红提醒同步清理已知限制/勘误文档（届时落点 ② 可
       转正为零明文断言）；
    2. **收窄后的真规格**（F1 修复后成立，SQL 级探针）：泄漏被精确限制在裁决内
       白名单通道——
       a. checkpoints 表 **root ns 零哨兵**（node_errors / execution_result /
          fix_loop_history 等父图业务通道全 mask；F1 修复的 DB 侧回归锚点），
          命中只允许出现在内嵌子图 ns 的 blob（= messages 通道 ①）；
       b. writes 表逐行审计：任何命中行必须落在白名单（__resume__ / 子图
          messages），**node_errors 业务通道显式零哨兵**——出现白名单外命中
          即存在未知第三通道，立即翻红升级排查。
    """
    obs = _run_sentinel_scenario(monkeypatch, tmp_path, "knownlim")
    sentinel_b = _SENTINEL.encode("utf-8")
    assert obs["db_bytes"], "checkpoint DB 应非空"

    # —— 断言 1：锚定现状（已知限制存在性；消失 = 行为变化，需同步清理文档）。
    assert sentinel_b in obs["db_bytes"], (
        "checkpoint DB 字节不再含哨兵——langgraph 序列化/checkpoint 行为已变化，"
        "ADJ-S4-G2-02 已知限制可能自然消失：请复核并同步清理 architecture 勘误、"
        "interaction_tools docstring 与本用例"
    )

    # —— 断言 2：SQL 级收窄——泄漏仅限裁决内通道，无未知第三通道。
    conn = sqlite3.connect(str(obs["db_path"]))
    try:
        # 2a. checkpoints 表：root ns 零哨兵（F1 修复真规格）；命中仅限子图 ns。
        ckpt_hit_ns = sorted({
            (ns or "(root)")
            for ns, blob in conn.execute(
                "SELECT checkpoint_ns, checkpoint FROM checkpoints")
            if sentinel_b in (blob if isinstance(blob, bytes)
                              else str(blob).encode("utf-8"))
        })
        assert "(root)" not in ckpt_hit_ns, (
            "root ns 的 checkpoint blob 含哨兵——父图业务通道（node_errors / "
            "execution_result / fix_loop_history 等）存在未 mask 落点：F1"
            "（BUG-S4-G2-01）回归或出现新泄漏点，升级排查"
        )
        assert ckpt_hit_ns and all(ns.startswith("execution:") for ns in ckpt_hit_ns), (
            f"checkpoints 表命中 ns 应仅限内嵌子图（裁决内通道 ①），实为 {ckpt_hit_ns}"
        )

        # 2b. writes 表：逐行审计，命中必须全部落在白名单通道。
        offending = sorted({
            (ns or "(root)", channel)
            for ns, channel, blob in conn.execute(
                "SELECT checkpoint_ns, channel, value FROM writes")
            if sentinel_b in (blob if isinstance(blob, bytes)
                              else str(blob).encode("utf-8"))
            and not _is_adjudicated_leak_channel(ns, channel)
        })
        assert not offending, (
            f"writes 表存在裁决白名单外的哨兵命中（未知第三通道，升级排查）: "
            f"{offending}；白名单 = {_ADJUDICATED_LEAK_CHANNELS_DOC}"
        )
        # node_errors 业务通道显式零命中（F1 的 writes 侧回归锚点，主控指定粒度）。
        node_err_hits = sorted({
            (ns or "(root)")
            for ns, blob in conn.execute(
                "SELECT checkpoint_ns, value FROM writes WHERE channel = 'node_errors'")
            if sentinel_b in (blob if isinstance(blob, bytes)
                              else str(blob).encode("utf-8"))
        })
        assert not node_err_hits, (
            f"writes 表 node_errors 通道含哨兵（F1 回归）: {node_err_hits}"
        )
    finally:
        conn.close()


# ===========================================================================
# CP-G2-3：AC-S4-08 mock 注入断言（subprocess spy）+ 白名单收口
# ===========================================================================


def _build_exec_self_loop_graph():
    """E4 同款最小 self-loop 图（真实 execution 节点 + D1 关键分支）。"""
    g = StateGraph(GlobalState)
    g.add_node("execution", execution_module.execution)
    g.add_edge(START, "execution")

    def route(state: Dict[str, Any]) -> str:
        if state.get("_dev_loop_route") == execution_module._ROUTE_AWAIT_INTERRUPT:
            return "execution"
        return "end"

    g.add_conditional_edges("execution", route, {"execution": "execution", "end": END})
    return g.compile(checkpointer=InMemorySaver())


def _install_popen_spy(monkeypatch) -> List[Dict[str, Any]]:
    """patch subprocess.Popen（sandbox `_run_subprocess` 的最深零配额边界），
    捕获 cmd/env/cwd 并返回受控成功结果（不真起子进程）。"""
    calls: List[Dict[str, Any]] = []

    class SpyPopen:
        def __init__(self, cmd, **kwargs):
            calls.append({
                "cmd": [str(c) for c in cmd],
                "env": dict(kwargs.get("env") or {}),
                "cwd": kwargs.get("cwd"),
            })
            self.returncode = 0
            self.pid = 999999

        def communicate(self, timeout=None):
            return (b'<METRICS>{"accuracy": 0.9}</METRICS>\n', b"")

        def kill(self):  # pragma: no cover - 护栏兜底接口
            pass

    monkeypatch.setattr(lv.subprocess, "Popen", SpyPopen)
    return calls


def _run_credential_env_spy_scenario(monkeypatch, tmp_path: Path) -> Dict[str, Any]:
    """种子 .secrets（git + hf 双凭证）→ 真实 execution 节点 → 真实
    _run_execution_agent → build_credential_env → 真实 run_in_venv →
    真实 `_run_subprocess` → SpyPopen 捕获最终子进程 env。"""
    ws = tmp_path / "ws_spy"
    _isolate_workspace(monkeypatch, ws)
    secrets_store.remember_secret("git_credential:github.com", _GIT_TOKEN, True, ws)
    secrets_store.remember_secret("hf_token", _HF_TOKEN, True, ws)

    calls = _install_popen_spy(monkeypatch)
    llm = DispatchScriptLLM(scenario="chain")  # exec 剧本：run train → 收尾
    monkeypatch.setattr(execution_module, "create_llm", lambda cfg: llm)
    monkeypatch.setattr(execution_module, "collect_artifacts", lambda *a, **k: [])
    monkeypatch.setattr(execution_module, "_llm_extract_metrics", lambda *a, **k: ({}, 0))
    # 注意：execution_module.run_in_venv / lv._run_subprocess 均保持真实（全链路透传）。

    code_dir = _prepare_code_dir(ws)
    state = _initial_graph_state(ws)
    state.update({
        "code_output_dir": str(code_dir),
        "reproduction_plan": {"execution_steps": [{"command": "python train.py"}],
                              "environment": {}},
        "paper_analysis": {"metrics": ["accuracy"]},
        "current_step": "coding",
    })
    graph = _build_exec_self_loop_graph()
    cfg = {"configurable": {"thread_id": f"g2-spy-{uuid.uuid4().hex[:8]}"}}
    final = graph.invoke(state, cfg)
    return {"ws": ws, "calls": calls, "final": final,
            "values": graph.get_state(cfg).values}


def test_cp_g2_3_credential_env_reaches_subprocess_via_spy(monkeypatch, tmp_path):
    """AC-S4-08 mock 注入主证：.secrets → build_credential_env → extra_env →
    run_in_venv → `_run_subprocess` → subprocess.Popen env 全链路透传。"""
    obs = _run_credential_env_spy_scenario(monkeypatch, tmp_path)

    assert "__interrupt__" not in obs["final"], "spy 剧本应一次跑完"
    assert obs["values"]["execution_result"]["success"] is True
    calls = obs["calls"]
    assert len(calls) == 1, f"应恰 1 次子进程调用: {[c['cmd'] for c in calls]}"
    cmd, env = calls[0]["cmd"], calls[0]["env"]

    # 命令改写到 venv 解释器（确定性推导链路旁证）。
    assert cmd[0].endswith(".venv/bin/python") and cmd[1] == "train.py"

    # 凭证注入四断言（architecture §9.3 映射表）。
    assert env.get("GIT_TERMINAL_PROMPT") == "0", "R-S4-08：无条件注入未生效"
    assert env.get("HF_TOKEN") == _HF_TOKEN
    assert env.get("HUGGING_FACE_HUB_TOKEN") == _HF_TOKEN
    askpass = env.get("GIT_ASKPASS")
    assert askpass, "git 凭证应经 GIT_ASKPASS 脚本注入"
    askpass_path = Path(askpass)
    assert askpass_path.exists() and str(askpass_path).startswith(str(obs["ws"]))
    assert stat.S_IMODE(askpass_path.stat().st_mode) == 0o700
    assert _GIT_TOKEN in askpass_path.read_text(encoding="utf-8"), \
        "askpass 脚本应含 token（0700 设计内）"
    # git token 本体不进 env 值（间接注入语义命门）。
    assert all(_GIT_TOKEN not in v for v in env.values()), \
        "git token 不得以明文出现在子进程 env 值中（GIT_ASKPASS 间接注入被绕过）"


def test_cp_g2_3_sandbox_env_whitelist_blocks_ambient_credentials(monkeypatch, tmp_path):
    """白名单收口（HOTFIX-2 / D2 语义在真实链路上的旁证）：宿主进程环境中的
    LLM/deepxiv 凭证等非白名单变量不得透传进沙箱子进程 env。"""
    obs = _run_credential_env_spy_scenario(monkeypatch, tmp_path)
    env = obs["calls"][0]["env"]

    for forbidden in ("LLM_API_KEY", "DEEPXIV_TOKEN", "OPENAI_API_KEY",
                      "PYTHONPATH", "VIRTUAL_ENV"):
        assert forbidden not in env, f"非白名单变量 {forbidden} 泄入沙箱 env"
    # 白名单基础变量仍在（收口不是清空）。
    assert "PATH" in env
    # 显式注入的凭证变量与白名单变量共存。
    assert "HF_TOKEN" in env and "GIT_ASKPASS" in env


# ===========================================================================
# CP-G2-3 后半：真实链路转正骨架（真跑待 Maria 授权；本次仅验证收集）
# ===========================================================================


def _has_credentials() -> bool:
    from config import get_deepxiv_token, get_llm_api_key

    return bool(get_llm_api_key()) and bool(get_deepxiv_token())


skip_if_no_creds = pytest.mark.skipif(
    not _has_credentials(),
    reason="缺少 LLM_API_KEY 或 DEEPXIV_TOKEN（G2 真实链路待 Maria 授权后补跑）",
)

_PRIVATE_REPO_URL_ENV = "SP4_E2E_PRIVATE_REPO_URL"
_PRIVATE_REPO_TOKEN_ENV = "SP4_E2E_GIT_TOKEN"


def _make_real_llm_config():
    """真实 LLMConfig（读 config getter，不写凭证值；sp3 e2e 范式）。"""
    from config import (
        DEFAULT_LLM_MAX_TOKENS,
        DEFAULT_LLM_TEMPERATURE,
        get_llm_api_key,
        get_llm_base_url,
        get_llm_model,
    )
    from core.state import LLMConfig

    return LLMConfig(
        base_url=get_llm_base_url(),
        model=get_llm_model(),
        api_key=get_llm_api_key() or "",
        temperature=DEFAULT_LLM_TEMPERATURE,
        max_tokens=DEFAULT_LLM_MAX_TOKENS,
    )


class TestSprint4RealChainE2E:
    """G2 真实链路转正骨架（AC-S4-08/11/13）——**任何真跑须 Maria 授权**。

    交付基线：`-m e2e --collect-only` 正确收集、默认回归 deselected、
    凭证缺失 skip；绝不在未授权时真跑（耗 LLM token + deepxiv 日配额）。

    [转正记录 2026-07-06] Maria 授权"全跑"后由主控执行：real_s4_1 有效 3/3 PASS
    （含 2 处 harness 直修，3 次作废跑留档）、real_s4_3 3/3 PASS；real_s4_2 待
    Maria 提供私有仓库资源。详见
    `docs/sprint4/test-reports/2026-07-06_g2-real-run-conversion.md`。

    基调（沿用 sp3 TestRealChainE2E）：真实 LLM + 真实 deepxiv + mock sandbox；
    靶论文 HippoRAG（大概率已缓存）。复跑要求（dev-plan §G2）：interrupt 链路类
    3 次；LLM 服从度依赖类（agent 是否主动调 request_user_input）按实测复现率
    3~5 次。授权后运行：
        .venv/bin/pytest "tests/test_sprint4_e2e.py::TestSprint4RealChainE2E" -m e2e -v -s
    """

    pytestmark = [pytest.mark.e2e, skip_if_no_creds]

    def test_real_s4_1_three_interrupt_serial_llm_compliance(self, monkeypatch, tmp_path):
        """AC-S4-13 真跑转正：真实 LLM 全链路三 interrupt 串行。

        真跑待 Maria 授权。mock sandbox 状态机：敏感值未注册前一律返回 git 认证
        失败（诱导真实 execution agent 按 prompt 纪律调 request_user_input →
        interrupt#3，**LLM 服从度依赖**，按实测复现率 3~5 次）；注册后转 CUDA OOM
        （不可修复 → interrupt#2）。链：interrupt#1(planning) → approve →
        interrupt#3(execution) → resume(假 token) → interrupt#2 → terminate。
        预估消耗：1 次 paper_analysis 级 LLM token + deepxiv read_section 配额
        （HippoRAG 已缓存则近零）。

        [harness 修复 2026-07-06，主控直修] 首版漏 `_isolate_workspace`：workspace
        落 pytest tmp_path 违反 sandbox 护栏 3（work_dir 须在 config.WORKSPACE_DIR
        下），prepare_environment 永远"越界"失败 → 修复循环空转烧穿 retry_budget，
        agent 根本收不到 mock 认证失败（真跑 run1/run2 因此 FAIL，非 LLM 服从度）。
        修复 = 补 `_isolate_workspace` + `_prepare_code_dir` 预置 + `prepare_venv`
        确定性 fake（mock sandbox 本意，不真跑 pip）。
        """
        from core.state import create_initial_state

        ws = tmp_path / "ws_real1"
        _isolate_workspace(monkeypatch, ws)
        _prepare_code_dir(ws)

        def fake_prepare(work_dir, *a, **k):
            venv = Path(work_dir) / ".venv"
            return SandboxPrepareResult(
                success=True, venv_dir=str(venv),
                python_exe=str(venv / "bin" / "python"),
                pip_exe=str(venv / "bin" / "pip"),
                env_info={"python_version": "3.11 (mock)"},
            )

        def stateful_run(python_exe, command, work_dir, *a, **k):
            if not list(secrets_store.iter_sensitive_values()):
                return SandboxRunResult(
                    exit_code=128, stdout="",
                    stderr="fatal: Authentication failed for "
                           "'https://github.com/private/repo.git'",
                    duration_seconds=0.1, timed_out=False,
                    output_truncated=False, command=list(command))
            return SandboxRunResult(
                exit_code=1, stdout="",
                stderr="RuntimeError: CUDA out of memory",
                duration_seconds=0.1, timed_out=False,
                output_truncated=False, command=list(command))

        monkeypatch.setattr(execution_module, "prepare_venv", fake_prepare)
        monkeypatch.setattr(execution_module, "run_in_venv", stateful_run)
        monkeypatch.setattr(execution_module, "collect_artifacts", lambda *a, **k: [])

        conn, saver = _make_wal_saver(tmp_path / "real1.db")
        try:
            graph = build_graph(checkpointer=saver)
            cfg = {"configurable": {"thread_id": f"g2-real1-{uuid.uuid4().hex[:8]}"}}
            initial = create_initial_state(
                user_input=PAPER_ARXIV_ID,
                llm_config=_make_real_llm_config(),
                workspace_dir=str(ws),
            )
            kinds: List[str] = []

            out = graph.invoke(initial, cfg)
            assert "__interrupt__" in out, "真实链路应在 planning 暂停"
            kinds.append(_interrupt_values(graph, cfg)[0]["interrupt_kind"])
            assert kinds[0] == INTERRUPT_KIND_PLANNING

            # [harness 修复 2 / 2026-07-06] 固定 4 步剧本扛不住真实 LLM 非确定行为
            # （run3 实证：coding 真 run_command 对真 github 用假哨兵 token clone
            # 失败 → agent 合理地对同 purpose_key 再次 #3 问询，terminate 误打进
            # request_user_input 被降级空串）。改容忍循环：#3 一律喂哨兵值、见 #2
            # 才 terminate、上限 8 防死循环。断言收敛到 AC-S4-13 本质 = 三种
            # interrupt 同线程各至少一次、路由互不串扰、terminate 干净收尾。
            out = graph.invoke(Command(resume={"decision": "approve"}), cfg)
            for _ in range(8):
                assert "__interrupt__" in out, (
                    "approve 后链路应依次经 interrupt#3（LLM 服从度依赖，按 3~5 "
                    f"次复现率统计）与 interrupt#2 暂停；实际序列: {kinds}"
                )
                kind = _interrupt_values(graph, cfg)[0]["interrupt_kind"]
                kinds.append(kind)
                if kind == INTERRUPT_KIND_USER_INPUT:
                    out = graph.invoke(
                        Command(resume={"value": _SENTINEL, "remember": False}), cfg)
                elif kind == INTERRUPT_KIND_DEV_LOOP:
                    graph.invoke(Command(resume={"decision": "terminate"}), cfg)
                    break
                else:
                    pytest.fail(f"approve 后不应再现 planning interrupt: {kinds}")
            assert graph.get_state(cfg).next == (), f"terminate 后应到 END: {kinds}"
            assert INTERRUPT_KIND_USER_INPUT in kinds, (
                f"链路未出现 interrupt#3（LLM 服从度问题，按复现率统计）: {kinds}")
            assert kinds[-1] == INTERRUPT_KIND_DEV_LOOP, (
                f"最终应停在 interrupt#2 后 terminate: {kinds}")
        finally:
            try:
                conn.close()
            except Exception:
                pass

    @pytest.mark.skipif(
        not (os.environ.get(_PRIVATE_REPO_URL_ENV)
             and os.environ.get(_PRIVATE_REPO_TOKEN_ENV)),
        reason=f"缺少 {_PRIVATE_REPO_URL_ENV} 或 {_PRIVATE_REPO_TOKEN_ENV}"
               "（AC-S4-08 真跑需真私有仓库 + 真 token，待 Maria 提供并授权）",
    )
    def test_real_s4_2_credential_injection_private_repo_clone(self, tmp_path):
        """AC-S4-08 真跑转正：真 token 经 GIT_ASKPASS 注入，真实 git clone 私有仓库。

        真跑待 Maria 授权（需 env：SP4_E2E_PRIVATE_REPO_URL + SP4_E2E_GIT_TOKEN）。
        真实网络 + 真实 git 子进程（不经 LLM，零 LLM/deepxiv 配额）；预估 <2 分钟。
        断言：clone exit 0 + 仓库落地 + token 零明文（stdout/stderr/命令行）。
        """
        import shutil

        from core.secrets_store import build_credential_env, remember_secret

        url = os.environ[_PRIVATE_REPO_URL_ENV]
        token = os.environ[_PRIVATE_REPO_TOKEN_ENV]
        # 护栏 3 要求 work_dir 在真实 WORKSPACE_DIR 下：建临时子目录，结束清理。
        work_dir = Path(config.WORKSPACE_DIR) / f"g2_real2_{uuid.uuid4().hex[:8]}"
        (work_dir / ".venv" / "bin").mkdir(parents=True)
        (work_dir / ".venv" / "pyvenv.cfg").write_text("home = /usr\n")
        (work_dir / ".venv" / "bin" / "python").write_text("")  # 仅路径校验用
        try:
            remember_secret("git_credential:github.com", token, True)
            env = build_credential_env()
            result = lv.run_in_venv(
                str(work_dir / ".venv" / "bin" / "python"),
                ["git", "clone", "--depth", "1", url, "cloned_repo"],
                str(work_dir),
                extra_env=env,
            )
            assert result.exit_code == 0, f"私有仓库 clone 失败: {result.stderr[-500:]}"
            assert (work_dir / "cloned_repo" / ".git").exists()
            assert token not in result.stdout and token not in result.stderr
            assert all(token not in c for c in result.command), \
                "token 不得出现在命令行（GIT_ASKPASS 间接注入）"
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    def test_real_s4_3_sentinel_masking_with_real_llm(self, monkeypatch, tmp_path):
        """AC-S4-11 真跑转正：真实 LLM execution agent + 哨兵值全链路脱敏 grep。

        真跑待 Maria 授权。与 mock 版 CP-G2-2 同构，但 execution 内嵌子图用真实
        LLM（验证真实 agent 的复述行为不泄漏哨兵）；sandbox 仍 mock（哨兵经
        interrupt#3 resume 注入后出现在 mock stdout）。断言 state / logs /
        interrupt payload 零明文。预估消耗：execution 子图 ~5-10 轮 LLM 调用。
        """
        ws = tmp_path / "ws_real3"
        _isolate_workspace(monkeypatch, ws)

        def stateful_run(python_exe, command, work_dir, *a, **k):
            if not list(secrets_store.iter_sensitive_values()):
                return SandboxRunResult(
                    exit_code=128, stdout="",
                    stderr="fatal: could not read Username for 'https://github.com': "
                           "terminal prompts disabled",
                    duration_seconds=0.1, timed_out=False,
                    output_truncated=False, command=list(command))
            return SandboxRunResult(
                exit_code=0,
                stdout=f"fetch ok Authorization: Bearer {_SENTINEL}\n"
                       '<METRICS>{"accuracy": 0.9}</METRICS>',
                stderr="", duration_seconds=0.1, timed_out=False,
                output_truncated=False, command=list(command))

        monkeypatch.setattr(execution_module, "run_in_venv", stateful_run)
        monkeypatch.setattr(execution_module, "collect_artifacts", lambda *a, **k: [])

        code_dir = _prepare_code_dir(ws)
        state = _initial_graph_state(ws)
        state["llm_config_set"] = {"default": dict(_make_real_llm_config()),
                                   "overrides": {}}
        state.update({
            "code_output_dir": str(code_dir),
            "reproduction_plan": {
                "execution_steps": [
                    {"command": "python fetch.py"}, {"command": "python train.py"}],
                "environment": {},
            },
            "paper_analysis": {"metrics": ["accuracy"]},
            "current_step": "coding",
        })
        graph = _build_exec_self_loop_graph()
        cfg = {"configurable": {"thread_id": f"g2-real3-{uuid.uuid4().hex[:8]}"}}

        out = graph.invoke(state, cfg)
        if "__interrupt__" not in out:
            pytest.fail(
                "真实 agent 未按 prompt 纪律触发 interrupt#3"
                "（LLM 服从度问题，按 3~5 次复现率统计后定性）")
        final = graph.invoke(
            Command(resume={"value": _SENTINEL, "remember": False}), cfg)
        values = graph.get_state(cfg).values
        assert _SENTINEL not in json.dumps(
            {k: v for k, v in values.items() if k != "node_errors"},
            ensure_ascii=False, default=str,
        ), "真实链路 GlobalState（除 F1 挂账通道）含哨兵明文"
        assert "__interrupt__" not in final or _SENTINEL not in json.dumps(
            _interrupt_values(graph, cfg), ensure_ascii=False, default=str)


# ===========================================================================
# 骨架收集守门（mock 层，默认回归可跑）
# ===========================================================================


def test_cp_g2_3_real_chain_skeleton_marks():
    """真实链路骨架的 mark 审计：类级 pytestmark 含 e2e + skipif（G1 元断言范式），
    保证默认回归 deselected、`-m e2e` 可收集、凭证缺失 skip。"""
    marks = getattr(TestSprint4RealChainE2E, "pytestmark", [])
    names = [m.name for m in marks]
    assert "e2e" in names, "骨架类必须打 e2e mark（否则默认回归会误跑真实链路）"
    assert "skipif" in names, "骨架类必须带凭证 skipif（凭证缺失需 skip 而非报错）"
    test_fns = [n for n in dir(TestSprint4RealChainE2E) if n.startswith("test_")]
    assert sorted(test_fns) == [
        "test_real_s4_1_three_interrupt_serial_llm_compliance",
        "test_real_s4_2_credential_injection_private_repo_clone",
        "test_real_s4_3_sentinel_masking_with_real_llm",
    ], f"骨架用例集不符: {test_fns}"
    # 真跑纪律锚定：本文件任何 mock 用例不得依赖真实凭证。
    assert not any(
        m.name == "e2e" for m in globals().get("pytestmark", [])
    ), "模块级不得整体标 e2e（mock 层必须进默认回归）"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
