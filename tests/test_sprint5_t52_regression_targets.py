"""T-S5-5-2（其一）：回归样本靶测收口——五条靶测 AC 聚合 + AC-S5-03 三落点串联。

覆盖 dev-plan §批次5 T-S5-5-2 检查点 CP-5.2-1 / CP-5.2-2：

    CP-5.2-1 五条靶测 AC（全部走 tests/fixtures/ 固化副本，原样本只读）：
        - AC-S5-01 2604.01687 场景计划 required_credentials 非空（mock planning
          输出 → 真实 _map_planning_result 落 plan 断言）；
        - AC-S5-05 fixture 审计命中 → reporting 全链：≥2 类 smell + 结论降档 +
          报告显著标注"模拟/未验证"（真实 audit + 真实 reporting 渲染串联）；
        - AC-S5-07 以回归 thread 终态形态的 state 快照 mock（旧 7 键 exec_result +
          旧 dict expected_results）重生成报告：新措辞红线 vs 固化旧 report.md
          措辞对照（旧报告有"✅ 复现成功"/巨型 dict，新报告禁）；
        - AC-S5-15 回归 thread 的 state 序列驱动路由断言（coding/execution →
          监控页；reporting ∧ report_path 非空 → 报告页，双通道）；
        - AC-S5-20 fixture outputs 三组真实解析 → 报告"本次复现值"非空 +
          组展开 + 无巨型 dict + key_packages 渲染（解析→渲染串联）。

    CP-5.2-2 AC-S5-03 三落点 mock e2e 串联（同一份降级事实流经三层）：
        gate degrade resume → state.credential_degradations（落点①）
        → execution() 快照 exec_result.degraded_credentials（落点②）
        → reporting() 报告"重要声明/凭证降级"块（落点③）。

原样本零写入实证：module 级 autouse fixture 对固化副本 7 文件做 md5 前后对账，
并对 `workspace/2604.01687/` 原样本关键文件 + `checkpoints.db` 做 stat 快照对账
（只读 stat，不改任何字节）——见 _fixture_integrity_guard。

范式来源：t34 _state/_render（reporting 驱动）、t26 _base_state/_patch_agent
（execution 驱动）、t22 fake interrupt + _ReactStub（gate 驱动）、s5_08 AppTest
脚本（路由驱动）。全离线 mock，零 LLM、零配额。
"""

from __future__ import annotations

import copy
import hashlib
import importlib
import json
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import MagicMock, patch

import pytest
from streamlit.testing.v1 import AppTest

import config
from core import secrets_store
from core.honesty_audit import audit_code_dir
from core.state import ExecutionMode
from config import STREAMLIT_PAGE_EXECUTION, STREAMLIT_PAGE_REPORT

# core/nodes/__init__.py 显式 export 同名 callable 会遮蔽子模块（已知坑 #6），
# 统一 importlib 取模块对象。
planning_module = importlib.import_module("core.nodes.planning")
coding_module = importlib.import_module("core.nodes.coding")
execution_module = importlib.import_module("core.nodes.execution")
reporting_module = importlib.import_module("core.nodes.reporting")

from core.nodes.execution import execution  # noqa: E402
from sandbox.local_venv import SandboxPrepareResult, SandboxRunResult  # noqa: E402

_map_planning_result = planning_module._map_planning_result
reporting = reporting_module.reporting

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_ROOT = PROJECT_ROOT / "tests" / "fixtures" / "regression_2604_01687"
FIXTURE_CODE_DIR = FIXTURE_ROOT / "code"
FIXTURE_REPORT = FIXTURE_ROOT / "report.md"

#: 原样本（严格只读，仅 stat/md5 对账，永不作为任何被测函数的输入）
ORIGINAL_SAMPLE_DIR = PROJECT_ROOT / "workspace" / "2604.01687"
CHECKPOINTS_DB = PROJECT_ROOT / "checkpoints.db"

#: 回归 thread（真库只做手动验证——本文件仅把 id 用作 AppTest 会话命名，零 db 访问）
REGRESSION_THREAD_ID = "task-9208a1a4b4f5"

_ARXIV_ID = "2604.01687"


# ---------------------------------------------------------------------------
# 只读完整性守门（CP-5.2-1"原样本零写入实证"）
# ---------------------------------------------------------------------------


def _md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def _fixture_fingerprint() -> Dict[str, str]:
    """固化副本 7 文件（README 除外）md5 指纹。"""
    files = [
        FIXTURE_CODE_DIR / "src" / "skill_generator.py",
        FIXTURE_CODE_DIR / "src" / "task_executor.py",
        FIXTURE_CODE_DIR / "data" / "skillsbench_manifest.json",
        FIXTURE_CODE_DIR / "outputs" / "evoskills_smoke" / "summary.json",
        FIXTURE_CODE_DIR / "outputs" / "baselines" / "no_skill" / "summary.json",
        FIXTURE_CODE_DIR / "outputs" / "baselines" / "self_generated" / "summary.json",
        FIXTURE_ROOT / "report.md",
    ]
    return {str(p.relative_to(FIXTURE_ROOT)): _md5(p) for p in files}


def _original_fingerprint() -> Dict[str, Tuple[int, float]]:
    """原样本关键文件 + checkpoints.db 的 (size, mtime) 快照（只读 stat）。"""
    fp: Dict[str, Tuple[int, float]] = {}
    if ORIGINAL_SAMPLE_DIR.is_dir():
        for rel in (
            "code/src/skill_generator.py",
            "code/src/task_executor.py",
            "report.md",
        ):
            p = ORIGINAL_SAMPLE_DIR / rel
            if p.is_file():
                st = p.stat()
                fp[f"workspace:{rel}"] = (st.st_size, st.st_mtime)
    if CHECKPOINTS_DB.is_file():
        st = CHECKPOINTS_DB.stat()
        fp["checkpoints.db"] = (st.st_size, st.st_mtime)
    return fp


@pytest.fixture(autouse=True, scope="module")
def _fixture_integrity_guard():
    """模块前后对账：固化副本 md5 与原样本 stat 均不得变化（零写入实证）。"""
    before_fixture = _fixture_fingerprint()
    before_original = _original_fingerprint()
    yield
    assert _fixture_fingerprint() == before_fixture, (
        "固化 fixture 在测试执行期间被修改——tests/fixtures/** 是只读回归基线"
    )
    assert _original_fingerprint() == before_original, (
        "原样本 workspace/2604.01687 或 checkpoints.db 在测试执行期间被触碰"
    )


# ---------------------------------------------------------------------------
# 隔离夹具（沿用 t22 / t26 / t34 纪律）
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_module_level_state():
    secrets_store._SENSITIVE_VALUES.clear()
    secrets_store._SESSION_SECRETS.clear()
    yield
    secrets_store._SENSITIVE_VALUES.clear()
    secrets_store._SESSION_SECRETS.clear()


@pytest.fixture(autouse=True)
def secrets_workspace(tmp_path, monkeypatch):
    """config.WORKSPACE_DIR 隔离到 tmp（gate/.secrets 落点），绝不碰真实 workspace。"""
    ws = tmp_path / "cfg-workspace"
    ws.mkdir()
    monkeypatch.setattr(config, "WORKSPACE_DIR", ws)
    return ws


@pytest.fixture()
def report_workspace(tmp_path, monkeypatch):
    """reporting 模块 WORKSPACE_DIR 指向 tmp（报告落盘隔离，t34 同范式）。"""
    ws = tmp_path / "report-workspace"
    ws.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(reporting_module, "WORKSPACE_DIR", ws)
    return ws


@pytest.fixture(autouse=True)
def _no_artifacts(monkeypatch):
    monkeypatch.setattr(execution_module, "collect_artifacts", lambda *a, **k: [])


# ---------------------------------------------------------------------------
# state / 驱动 helpers
# ---------------------------------------------------------------------------

#: 与固化 outputs/**/summary.json 逐字一致的三组指标（t33/t34 同源常量）
FIXTURE_GROUPS: Dict[str, Dict[str, Any]] = {
    "evoskills_smoke": {
        "experiment_name": "evoskills_smoke",
        "num_tasks": 3,
        "pass_rate": 0.6666666666666666,
        "mean_oracle_score": 0.9666666666666667,
    },
    "baselines/no_skill": {
        "experiment_name": "baseline_no_skill",
        "baseline_type": "no_skill",
        "num_tasks": 3,
        "pass_rate": 0.0,
        "mean_score": 0.06666666666666667,
    },
    "baselines/self_generated": {
        "experiment_name": "baseline_self_generated",
        "baseline_type": "self_generated",
        "num_tasks": 3,
        "pass_rate": 0.6666666666666666,
        "mean_score": 0.9666666666666667,
    },
}

#: 对 FIXTURE_GROUPS 恒判"符合"的定性预期（t33 GOOD_EXPECTED 同源）
GOOD_EXPECTED: List[Dict[str, Any]] = [
    {
        "description": "EvoSkills 组 pass_rate 应高于 no_skill 基线",
        "trend": {
            "metric": "pass_rate",
            "greater": "evoskills_smoke",
            "lesser": "baselines/no_skill",
        },
    },
]


def _exec_result_11key(**overrides: Any) -> Dict[str, Any]:
    """11 键 ExecutionResult（sp5 全量形态），默认干净 success=True。"""
    result: Dict[str, Any] = {
        "success": True,
        "metrics": {"pass_rate": 0.6666666666666666},
        "logs": "",
        "errors": [],
        "artifacts": [],
        "runtime_seconds": 11.5,
        "environment_info": {},
        "step_reconciliation": {
            "planned": 2,
            "executed": 2,
            "completed": 2,
            "unexecuted_steps": [],
            "extra_commands": [],
            "attribution_unavailable": False,
        },
        "budget_truncated": False,
        "metrics_groups": copy.deepcopy(FIXTURE_GROUPS),
        "degraded_credentials": [],
    }
    result.update(overrides)
    return result


def _reporting_state(
    code_dir: Path,
    exec_result: Optional[Dict[str, Any]],
    *,
    expected_results: Any = "default",
    simulation_notice: Optional[str] = None,
    **overrides: Any,
) -> Dict[str, Any]:
    """reporting() 可消费完整 state（t34 _state 同范式；code_dir 显式传入）。"""
    if expected_results == "default":
        expected_results = copy.deepcopy(GOOD_EXPECTED)
    state: Dict[str, Any] = {
        "workspace_dir": str(code_dir.parent),
        "code_output_dir": str(code_dir.resolve()),
        "execution_mode": ExecutionMode.FULL,
        "paper_meta": {"arxiv_id": _ARXIV_ID, "title": "EvoSkills"},
        "paper_analysis": {"baseline_results": {}},
        "reproduction_plan": {"expected_results": expected_results, "deliverables": []},
        "execution_result": exec_result,
        "simulation_notice": simulation_notice,
        "node_errors": [],
        "degraded_nodes": [],
        "fix_loop_history": [],
        "fix_loop_count": 0,
        "user_fix_decision": None,
    }
    state.update(overrides)
    return state


def _render(state: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    """跑真实 reporting()（真实审计 + 真实渲染），返回 (markdown, node 输出)。"""
    out = reporting(state)
    assert set(out.keys()) == {"report_path", "current_step", "honesty_audit"}, out.keys()
    return Path(out["report_path"]).read_text(encoding="utf-8"), out


def _fixture_code_scratch_copy(dest_root: Path) -> Path:
    """把固化 code/ 复制到 tmp 作为被测输入（fixture 本体只读红线）。

    reporting() 落盘规则为 ``Path(code_output_dir).parent / "report.md"``
    （reporting.py _resolve_report_path 规则 1）——code_output_dir 绝不可
    直接指向 fixture，否则报告会覆写固化 report.md（本文件首跑即被
    _fixture_integrity_guard 抓获的事故，此 helper 是修复方案）。
    """
    dest = dest_root / _ARXIV_ID / "code"
    shutil.copytree(FIXTURE_CODE_DIR, dest)
    return dest


def _execution_base_state(**overrides: Any) -> Dict[str, Any]:
    """execution() 可消费最小 state（t26 _base_state 同范式）。"""
    state: Dict[str, Any] = {
        "llm_config_set": {"default": {"model": "test"}},
        "code_output_dir": "/tmp/t52-workdir",
        "reproduction_plan": {
            "execution_steps": [{"command": "python run_evoskills.py"}],
            "environment": {"dependencies": ["numpy"]},
        },
        "paper_analysis": {"metrics": []},
        "execution_mode": ExecutionMode.FULL,
        "node_errors": [],
        "degraded_nodes": [],
        "fix_loop_history": [],
        "fix_loop_count": 0,
        "retry_budget_remaining": 40,
        "_dev_loop_llm_calls": 0,
        "_dev_loop_route": None,
        "execution_result": None,
        "current_step": "coding",
    }
    state.update(overrides)
    return state


def _patch_exec_agent(monkeypatch) -> None:
    """execution agent 替身：单命令成功 + <METRICS> 主通道输出。"""
    prep = SandboxPrepareResult(
        success=True, venv_dir="/w/.venv", python_exe="/w/.venv/bin/python", pip_exe="",
        env_info={"python_version": "Python 3.11"},
        install_log="ok", install_failed_packages=[], error=None,
    )
    run = SandboxRunResult(
        exit_code=0, stdout='<METRICS>{"pass_rate": 0.6667}</METRICS>', stderr="",
        duration_seconds=0.1, timed_out=False,
        output_truncated=False, command=["python", "run_evoskills.py"],
    )
    out = execution_module.ExecAgentOutput(
        prep=prep, run_results=[run], rounds_used=2, llm_calls=2,
    )
    monkeypatch.setattr(
        execution_module, "_run_execution_agent", lambda state, work_dir, plan: out,
    )


class _InterruptRecorder:
    """mock interrupt：记录 payload，按剧本返回 resume（t22 同范式）。"""

    def __init__(self, resumes: List[Any]):
        self._resumes = list(resumes)
        self.calls: List[Dict[str, Any]] = []

    def __call__(self, payload: Dict[str, Any]) -> Any:
        self.calls.append(payload)
        assert self._resumes, "interrupt 被意外多调（剧本已耗尽）"
        return self._resumes.pop(0)


class _ReactStub:
    """替身 coding ReAct wrapper：记录 state 视图，返回固定 update（t22 同范式）。"""

    def __init__(self) -> None:
        self.seen: List[Dict[str, Any]] = []

    def __call__(self, state: Dict[str, Any]) -> Dict[str, Any]:
        self.seen.append(dict(state))
        return {"current_step": "coding"}


# ===========================================================================
# CP-5.2-1 靶测一：AC-S5-01 计划凭证声明非空（2604.01687 场景 mock planning 输出）
# ===========================================================================


def test_cp_5_2_1_ac01_regression_scenario_plan_credentials_nonempty():
    """以 2604.01687 真跑场景形态的 planning LLM 输出（论文方法依赖外部 LLM API）
    过真实 _map_planning_result：required_credentials 非空落 plan，二键契约齐全。

    对照 manual-run-feedback #10 失守链第 1 环：当时的计划无凭证声明；sp5 后
    同场景计划必须携带非空声明（AC-S5-01 回归样本口径）。
    """
    llm_result = {
        "plan_summary": "复现 EvoSkills：技能自进化 + 关键词 verifier 评测",
        "environment": {"python": "3.11"},
        "data_preparation": ["构建 SkillsBench 任务清单"],
        "code_strategy": "from_scratch",
        "execution_steps": [
            {"step_name": "安装依赖", "command": "pip install openai anthropic",
             "expected_output": "成功"},
            {"step_name": "smoke 实验", "command": "python run_evoskills.py --smoke",
             "expected_output": "outputs/evoskills_smoke/summary.json"},
        ],
        "expected_results": [
            {
                "description": "EvoSkills 组 pass_rate 应高于 no_skill 基线",
                "trend": {
                    "metric": "pass_rate",
                    "greater": "evoskills_smoke",
                    "lesser": "baselines/no_skill",
                },
            },
        ],
        "required_credentials": [
            {"purpose_key": "env:OPENAI_API_KEY",
             "purpose": "论文方法依赖真实 LLM 调用（技能生成与任务执行）"},
            {"purpose_key": "env:ANTHROPIC_API_KEY",
             "purpose": "跨模型迁移实验需要第二家模型 API"},
        ],
        "estimated_time": "1 天",
        "deliverables": ["run_evoskills.py", "requirements.txt"],
    }
    state = {
        "llm_config_set": {"default": {"model": "m"}},
        "paper_meta": {"arxiv_id": _ARXIV_ID, "title": "EvoSkills"},
        "paper_analysis": {"method_summary": "自进化技能", "metrics": ["pass_rate"]},
        "resource_info": {"repos": [], "selected_repo": None,
                          "external_resources": [], "resource_strategy": "from_scratch"},
        "node_errors": [],
        "degraded_nodes": [],
        "_planning_user_feedback": None,
    }

    out = _map_planning_result(llm_result, state)
    plan = out["reproduction_plan"]

    # AC-S5-01 核心：依赖外部 LLM API 的论文 → 声明非空
    creds = plan["required_credentials"]
    assert isinstance(creds, list) and len(creds) == 2
    for item in creds:
        assert set(item.keys()) == {"purpose_key", "purpose"}, item
        assert item["purpose_key"].strip() and item["purpose"].strip()
    assert creds[0]["purpose_key"] == "env:OPENAI_API_KEY"
    # 声明的是"名称 + 用途"（PRD 原文口径），purpose 为用户可读中文
    assert "LLM" in creds[0]["purpose"]
    # 定性 expected_results 同步在场（同一 map 通道，不互相挤掉）
    assert plan["expected_results"][0]["trend"]["metric"] == "pass_rate"
    assert planning_module.NODE_NAME not in out.get("degraded_nodes", [])


# ===========================================================================
# CP-5.2-1 靶测二：AC-S5-05 fixture 审计命中 → 报告降档 + 显著标注（全链）
# ===========================================================================


def test_cp_5_2_1_ac05_fixture_audit_to_report_annotation_chain(report_workspace):
    """审计（真实 audit_code_dir 扫固化造假现场）→ reporting 全链：
    ≥2 类 smell、结论降档为工程复现措辞、报告顶部"重要声明"显著标注模拟/未验证。"""
    # 前置自证：固化靶独立审计命中 ≥2 类（与 CP-3.1-1 同口径，链路输入非空保证）
    standalone = audit_code_dir(FIXTURE_CODE_DIR)
    assert standalone["clean"] is False
    assert {h["rule"] for h in standalone["hits"]} >= {"answer_leakage", "hardcoded_score"}

    # 全链：造假现场经 tmp 散件副本进 reporting()（fixture 只读），内部重跑真实审计
    code_copy = _fixture_code_scratch_copy(report_workspace)
    state = _reporting_state(code_copy, _exec_result_11key())
    md, out = _render(state)

    # 落点 A：节点输出 honesty_audit 与独立审计一致（非空、≥2 类）
    audit = out["honesty_audit"]
    assert audit["clean"] is False
    assert {h["rule"] for h in audit["hits"]} >= {"answer_leakage", "hardcoded_score"}

    # 落点 B：结论降档（AC-S5-05"非最高档"）——工程复现措辞，禁"复现成功"
    assert "代码跑通（工程复现），论文实验结论未验证" in md
    assert "复现成功" not in md

    # 落点 C：显著标注——顶部声明块 + 模拟/未验证语义 + 证据可追溯
    assert "重要声明" in md
    assert "模拟" in md
    assert "未验证" in md
    assert "src/task_executor.py" in md  # 审计证据文件名进报告（可人工复核）


# ===========================================================================
# CP-5.2-1 靶测三：AC-S5-07 回归 thread 终态快照重生成报告——新旧措辞对照
# ===========================================================================


def test_cp_5_2_1_ac07_snapshot_regen_wording_vs_frozen_old_report(report_workspace):
    """以回归 thread（task-9208a1a4b4f5）终态形态的 state 快照 mock（旧 7 键
    exec_result + 旧 dict expected_results + 嵌套 baseline_results）重生成报告：

    - 旧固化 report.md（对照靶）：含"✅ **复现成功**"、full_success、巨型 dict 行；
    - 新逻辑重生成：全文禁"复现成功"、工程复现措辞、嵌套降维无巨型 dict、
      审计标注在场（当时的造假代码如今必被标注）。
    """
    # ---- 对照靶：固化旧报告的三个"假成功"措辞特征仍冻结在场 ----
    old_md = FIXTURE_REPORT.read_text(encoding="utf-8")
    assert "✅ **复现成功**" in old_md
    assert "full_success" in old_md
    assert "{'" in old_md  # L24-26 巨型 dict 行（渲染反例）

    # ---- 快照 mock：贴回归 thread 真实终态形态 ----
    legacy_exec_result = {  # 旧 7 键形态（sp4 checkpoint 快照，无 sp5 新键）
        "success": True,
        "metrics": dict(FIXTURE_GROUPS["evoskills_smoke"]),
        "logs": "",
        "errors": [],
        "artifacts": [],
        "runtime_seconds": 11.5,
        "environment_info": {},
    }
    legacy_expected = {  # 旧 dict 形态（当时计划复述论文数字的编造链路）
        "main_comparison": {
            "target": "在 SkillsBench 上复现 EvoSkills 相对各基线的显著优势",
            "paper_reference": {"EvoSkills": 71.1, "No-Skill_Baseline": 30.6},
        },
        "primary_metric": "pass rate",
    }
    state = _reporting_state(
        _fixture_code_scratch_copy(report_workspace),  # 造假现场 tmp 散件副本
        legacy_exec_result,
        expected_results=legacy_expected,
    )
    state["paper_analysis"] = {
        "baseline_results": {
            "main_comparison": {"EvoSkills": 71.1, "No-Skill_Baseline": 30.6},
        }
    }
    new_md, out = _render(state)

    # ---- 新措辞红线（AC-S5-07）----
    assert "复现成功" not in new_md          # 旧报告主罪：宽松口径宣告成功
    assert "✅ **复现成功**" not in new_md
    assert "代码跑通（工程复现），论文实验结论未验证" in new_md
    # 旧 dict expected → 回验不崩、全"未验证"路径（R-5 兼容）
    assert "计划目标回验" in new_md
    # 审计标注：当时一路绿灯的造假代码，如今显著标注
    assert out["honesty_audit"]["clean"] is False
    assert "重要声明" in new_md
    # 渲染修复：嵌套 baseline 降维，指标对比节无巨型 dict 字符串（对照旧报告
    # L24-26；AC-S5-20 口径限定"嵌套指标降维渲染"，故断言收窄到该节）。
    # 已知容忍边界（记录于 t52 测试报告，供 T-S5-5-3 handoff）：回验表对旧 dict
    # expected_results 条目按 repr 原样展示 + "不做机验"注记——R-5 兼容路径的
    # 既定设计（CP-3.4-5 口径），仅旧 checkpoint 重生成时出现，不在本红线内。
    metrics_section = new_md.split("## 指标对比", 1)[1]
    assert "{'" not in metrics_section and '{"' not in metrics_section
    assert "main_comparison.EvoSkills" in new_md
    assert "旧形态数值预期，不做机验" in new_md  # 兼容注记在场（容忍边界有痕）


# ===========================================================================
# CP-5.2-1 靶测四：AC-S5-15 回归 thread state 序列驱动路由（mock 部分）
# ===========================================================================

_PROGRESS_SCRIPT = f"""
import streamlit as st
st.session_state.setdefault("thread_id", "{REGRESSION_THREAD_ID}")
st.session_state.setdefault("current_page", "progress")
page = st.session_state.get("current_page", "progress")
if page == "progress":
    from ui.pages.analysis_progress import render
    render()
elif page == "execution":
    st.write("EXECUTION_STUB")
elif page == "report":
    st.write("REPORT_STUB")
else:
    st.write("OTHER_STUB")
"""

_MONITOR_SCRIPT = f"""
import streamlit as st
st.session_state.setdefault("thread_id", "{REGRESSION_THREAD_ID}")
st.session_state.setdefault("current_page", "execution")
page = st.session_state.get("current_page", "execution")
if page == "execution":
    from ui.pages.execution_monitor import render
    render()
elif page == "report":
    st.write("REPORT_STUB")
else:
    st.write("OTHER_STUB")
"""


def _thread_state(current_step: str, report_path: Optional[str] = None) -> Dict[str, Any]:
    """回归 thread 各阶段 state 形态（页面消费字段最小并集，s5_08 同范式）。"""
    return {
        "current_step": current_step,
        "degraded_nodes": [],
        "error": None,
        "node_errors": [],
        "paper_meta": {"arxiv_id": _ARXIV_ID, "title": "EvoSkills"},
        "fix_loop_count": 0,
        "fix_loop_history": [],
        "execution_result": None,
        "report_path": report_path,
    }


def _controller(state: Dict[str, Any]) -> MagicMock:
    c = MagicMock()
    c.poll_state.return_value = state
    c.is_interrupted.return_value = False
    c.get_interrupt_payload.return_value = None
    c.interrupt_kind.return_value = None
    c.get_worker_error.return_value = None
    c.is_finished.return_value = False
    return c


def _run_page(script: str, module_ar: str, controller: MagicMock) -> AppTest:
    with patch("app._get_controller", return_value=controller), patch(module_ar):
        at = AppTest.from_string(script)
        at.run()
    assert not at.exception, at.exception
    return at


def test_cp_5_2_1_ac15_regression_thread_state_sequence_full_route():
    """回归 thread 顺利执行全程 state 序列（planning 批准后 coding → execution →
    reporting+report_path）逐段驱动：每段路由落点与 AC-S5-15 双通道一致。

    正是 manual-run-feedback #4 现场的反面证：当时 UI 停在进度页 5/5 永久轮询，
    修复后同一 state 序列必须一路到达报告页。report_path 用固化 report.md 真路径。
    """
    report_path = str(FIXTURE_REPORT)

    # 段 1/2：coding / execution → progress 页 case④bis 切执行监控页
    for step in ("coding", "execution"):
        at = _run_page(
            _PROGRESS_SCRIPT, "ui.pages.analysis_progress.st_autorefresh",
            _controller(_thread_state(step)),
        )
        assert at.session_state["current_page"] == STREAMLIT_PAGE_EXECUTION, step

    # 段 3 通道一：progress 页 case④ter 兜底——reporting ∧ report_path → 报告页
    at = _run_page(
        _PROGRESS_SCRIPT, "ui.pages.analysis_progress.st_autorefresh",
        _controller(_thread_state("reporting", report_path)),
    )
    assert at.session_state["current_page"] == STREAMLIT_PAGE_REPORT

    # 段 3 通道二：monitor 页 case⑥——reporting ∧ report_path → 报告页
    at = _run_page(
        _MONITOR_SCRIPT, "ui.pages.execution_monitor.st_autorefresh",
        _controller(_thread_state("reporting", report_path)),
    )
    assert at.session_state["current_page"] == STREAMLIT_PAGE_REPORT


# ===========================================================================
# CP-5.2-1 靶测五：AC-S5-20 fixture outputs 真实解析 → 报告非空列（全链）
# ===========================================================================


def test_cp_5_2_1_ac20_fixture_parse_to_report_columns_chain(report_workspace):
    """真实 _collect_grouped_metrics 扫固化 outputs → 三组对齐 → 真实 reporting
    渲染：组逐一展开、"本次复现值"非空（fixture 数字进表）、无巨型 dict、
    key_packages 逐包渲染。解析与渲染两环真实串联（t26/t34 各自单证的合龙）。"""
    groups = execution_module._collect_grouped_metrics(str(FIXTURE_CODE_DIR))

    # 解析环：与固化 summary.json 全对齐
    assert set(groups.keys()) == {
        "evoskills_smoke", "baselines/no_skill", "baselines/self_generated",
    }
    assert groups["evoskills_smoke"]["pass_rate"] == pytest.approx(2 / 3)
    assert groups["baselines/no_skill"]["pass_rate"] == 0.0
    assert groups["baselines/self_generated"]["mean_score"] == pytest.approx(
        0.9666666666666667
    )

    # 渲染环：真实解析结果原样进 exec_result → 报告
    state = _reporting_state(
        report_workspace / _ARXIV_ID / "code",
        _exec_result_11key(
            metrics_groups=groups,
            environment_info={
                "python_version": "Python 3.11.9",
                "key_packages": "numpy==1.26.4, httpx==0.27.0",
            },
        ),
    )
    Path(state["code_output_dir"]).mkdir(parents=True, exist_ok=True)  # 空目录→审计 clean
    md, out = _render(state)

    assert "指标对比" in md and "本次复现值" in md
    assert "计划 expected" not in md  # AC-S5-09 删列（同表共证）
    for group in ("evoskills_smoke", "baselines/no_skill", "baselines/self_generated"):
        assert f"组 `{group}`" in md
    # "本次复现值"列非空：fixture 数字（4g 格式化）逐一进表
    assert "0.6667" in md      # pass_rate（smoke / self_generated）
    assert "0.9667" in md      # mean_oracle_score / mean_score
    assert "0.06667" in md     # no_skill mean_score
    # 嵌套降维红线 + environment 节
    assert "{'" not in md and '{"' not in md
    assert "关键依赖包（key_packages）" in md
    assert "`numpy==1.26.4`" in md and "`httpx==0.27.0`" in md
    assert out["honesty_audit"]["clean"] is True  # 空 code 目录不误报（旁证）


# ===========================================================================
# CP-5.2-2 AC-S5-03 三落点 mock e2e 串联（state → exec_result → 报告声明）
# ===========================================================================


def test_cp_5_2_2_ac03_degrade_marker_three_landing_chain(
    tmp_path, monkeypatch, report_workspace,
):
    """同一份降级事实流经三层的完整串联（缺一即断链）：

    落点① gate degrade resume → coding update.credential_degradations；
    落点② 该 state 进 execution() → exec_result.degraded_credentials 快照；
    落点③ 该 exec_result 进 reporting() → 报告"重要声明/凭证降级"块 + 强制降档。
    """
    purpose_key = "env:OPENAI_API_KEY"
    purpose = "论文方法依赖真实 LLM 调用（EvoSkills 评测）"

    # ---- 落点①：用户显式选择降级 → 标记落 state ----
    rec = _InterruptRecorder([{"value": "", "remember": False, "degrade": True}])
    monkeypatch.setattr(coding_module, "interrupt", rec)
    stub = _ReactStub()
    monkeypatch.setattr(coding_module, "_coding_react", stub)

    coding_state = {
        "reproduction_plan": {
            "code_strategy": "from_scratch",
            "execution_steps": [{"name": "smoke", "command": "python run_evoskills.py"}],
            "deliverables": ["run_evoskills.py"],
            "environment": {"python": "3.11"},
            "required_credentials": [{"purpose_key": purpose_key, "purpose": purpose}],
        },
    }
    update = coding_module.coding(coding_state)

    assert len(rec.calls) == 1, "缺凭证应恰好一次 interrupt"
    assert rec.calls[0].get("allow_degrade") is True
    degradations = update["credential_degradations"]
    assert degradations == {purpose_key: purpose}
    # 降级 ≠ 记住：不落盘、不进会话层（用户未提供值）
    assert purpose_key not in secrets_store._SESSION_SECRETS

    # ---- 落点②：降级标记随 state 进 execution → exec_result 快照 ----
    _patch_exec_agent(monkeypatch)
    work_dir = tmp_path / "code"
    work_dir.mkdir()
    exec_out = execution(
        _execution_base_state(
            code_output_dir=str(work_dir),
            credential_degradations=dict(degradations),
        )
    )
    exec_result = exec_out["execution_result"]
    assert exec_result["success"] is True
    assert exec_result["degraded_credentials"] == [purpose_key]

    # ---- 落点③：exec_result 进 reporting → 报告强制声明 ----
    report_code_dir = report_workspace / _ARXIV_ID / "code"
    report_code_dir.mkdir(parents=True, exist_ok=True)
    report_state = _reporting_state(report_code_dir, exec_result)
    report_state["reproduction_plan"]["required_credentials"] = [
        {"purpose_key": purpose_key, "purpose": purpose},
    ]
    md, _ = _render(report_state)

    assert "重要声明" in md
    assert "凭证降级" in md
    assert purpose_key in md
    assert purpose in md          # purpose 中文说明经 plan 查表命中
    # 降级标注强制降档（AC-S5-11 联动红线）：成功也不得宣告"复现成功"
    assert "复现成功" not in md
    assert "代码跑通（工程复现），论文实验结论未验证" in md
