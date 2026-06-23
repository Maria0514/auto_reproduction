"""Sprint 3 任务 A1 自测：config.py 新增 sp3 常量（S3-08）。

覆盖 dev-plan §A1 CP-A1-1 ~ CP-A1-4（程序化可验证的 4 个 checkpoint）。

参考实现：sp2 同款风格 tests/test_sprint2_a4.py（轻量结构性断言，无真实 LLM）。

约束：A1 只追加常量，禁止修改 sp1/sp2 既有常量（尤其 MAX_TOTAL_LLM_CALLS=50 /
MAX_FIX_LOOP_COUNT=3）。CP-A1-4 用 git diff 实证 config.py 为纯追加 0 删改。
"""

from __future__ import annotations

import importlib
import re
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent


# ========== CP-A1-1：sp3 新增 10 个常量全部可导入 ==========


def test_cp_a1_1_all_new_constants_importable() -> None:
    """CP-A1-1: dev-plan L227-238 列出的 sp3 新增 10 个常量全部可从 config 顶层导入。"""
    from config import (  # noqa: F401
        DEV_LOOP_MIN_CALLS_PER_ROUND,
        MAX_DEV_LOOP_LLM_CALLS,
        REACT_MAX_ROUNDS_CODING,
        SANDBOX_EXEC_TIMEOUT,
        SANDBOX_OUTPUT_MAX_BYTES,
        SANDBOX_PIP_INSTALL_TIMEOUT,
        SANDBOX_PIP_MAX_RETRIES,
        SANDBOX_VENV_CREATE_TIMEOUT,
        STREAMLIT_PAGE_EXECUTION,
        STREAMLIT_PAGE_REPORT,
    )


# ========== CP-A1-2：全表值逐项断言 + 严格类型断言 ==========


def test_cp_a1_2_constant_values() -> None:
    """CP-A1-2: sp3 新增 10 个常量值与 dev-plan L227-238 给定默认值逐项一致。"""
    import config

    assert config.SANDBOX_EXEC_TIMEOUT == 1800
    assert config.SANDBOX_VENV_CREATE_TIMEOUT == 300
    assert config.SANDBOX_PIP_INSTALL_TIMEOUT == 1200
    assert config.SANDBOX_OUTPUT_MAX_BYTES == 1_048_576
    assert config.SANDBOX_OUTPUT_MAX_BYTES == 1048576  # 1 MiB 双形态确认
    assert config.SANDBOX_PIP_MAX_RETRIES == 2
    assert config.MAX_DEV_LOOP_LLM_CALLS == 20
    assert config.DEV_LOOP_MIN_CALLS_PER_ROUND == 2
    assert config.REACT_MAX_ROUNDS_CODING == 12
    assert config.STREAMLIT_PAGE_EXECUTION == "execution"
    assert config.STREAMLIT_PAGE_REPORT == "report"


def test_cp_a1_2_types_are_strict() -> None:
    """CP-A1-2 补：数值常量为严格 int（非 bool），路由常量为 str。"""
    import config

    for name in (
        "SANDBOX_EXEC_TIMEOUT",
        "SANDBOX_VENV_CREATE_TIMEOUT",
        "SANDBOX_PIP_INSTALL_TIMEOUT",
        "SANDBOX_OUTPUT_MAX_BYTES",
        "SANDBOX_PIP_MAX_RETRIES",
        "MAX_DEV_LOOP_LLM_CALLS",
        "DEV_LOOP_MIN_CALLS_PER_ROUND",
        "REACT_MAX_ROUNDS_CODING",
    ):
        assert type(getattr(config, name)) is int, f"{name} 应为严格 int（非 bool）"

    for name in ("STREAMLIT_PAGE_EXECUTION", "STREAMLIT_PAGE_REPORT"):
        assert type(getattr(config, name)) is str, f"{name} 应为 str"


def test_cp_a1_2_aux_semantics_positive() -> None:
    """CP-A1-2 补：sandbox 超时 / 预算 / 轮数均为正数（防 0 / 负数语义错误）。"""
    import config

    for name in (
        "SANDBOX_EXEC_TIMEOUT",
        "SANDBOX_VENV_CREATE_TIMEOUT",
        "SANDBOX_PIP_INSTALL_TIMEOUT",
        "SANDBOX_OUTPUT_MAX_BYTES",
        "SANDBOX_PIP_MAX_RETRIES",
        "MAX_DEV_LOOP_LLM_CALLS",
        "DEV_LOOP_MIN_CALLS_PER_ROUND",
        "REACT_MAX_ROUNDS_CODING",
    ):
        assert getattr(config, name) > 0, f"{name} 应为正数"


def test_cp_a1_2_aux_execution_report_pages_distinct() -> None:
    """CP-A1-2 补：execution / report 两页路由常量互不相同，且与 sp2 三页不撞键。"""
    import config

    assert config.STREAMLIT_PAGE_EXECUTION != config.STREAMLIT_PAGE_REPORT
    all_pages = {
        config.STREAMLIT_PAGE_INPUT,
        config.STREAMLIT_PAGE_PROGRESS,
        config.STREAMLIT_PAGE_REVIEW,
        config.STREAMLIT_PAGE_EXECUTION,
        config.STREAMLIT_PAGE_REPORT,
    }
    assert len(all_pages) == 5, "sp1/sp2/sp3 五个 UI 路由常量必须互不相同"


# ========== CP-A1-3：强约束断言 MAX_DEV_LOOP_LLM_CALLS < MAX_TOTAL_LLM_CALLS ==========


def test_cp_a1_3_dev_loop_budget_strictly_less_than_total() -> None:
    """CP-A1-3（AC-S3-04 ② 直接验收点）: 子预算 20 < 总预算 50。"""
    import config

    assert config.MAX_DEV_LOOP_LLM_CALLS < config.MAX_TOTAL_LLM_CALLS, (
        "MAX_DEV_LOOP_LLM_CALLS 必须严格小于 MAX_TOTAL_LLM_CALLS（修复循环子预算不得超总预算）"
    )
    assert config.MAX_DEV_LOOP_LLM_CALLS == 20
    assert config.MAX_TOTAL_LLM_CALLS == 50


# ========== CP-A1-4：sp1/sp2 既有常量零修改 + git diff 实证纯追加 ==========


def test_cp_a1_4_sp1_sp2_constants_unchanged() -> None:
    """CP-A1-4: sp1/sp2 既有关键常量值零修改（尤其 MAX_TOTAL_LLM_CALLS=50 /
    MAX_FIX_LOOP_COUNT=3）。"""
    import config

    # sp1 核心预算 / 重试常量
    assert config.MAX_TOTAL_LLM_CALLS == 50, "禁止修改 sp1 MAX_TOTAL_LLM_CALLS"
    assert config.MAX_NODE_LLM_CALLS == 10
    assert config.MAX_FIX_LOOP_COUNT == 3, "禁止修改 MAX_FIX_LOOP_COUNT（sp3 仅引用不改值）"
    assert config.LLM_REQUEST_TIMEOUT == 60
    assert config.DEFAULT_LLM_MAX_TOKENS == 8192
    assert config.LLM_MAX_RETRIES == 3
    assert config.LLM_INITIAL_RETRY_DELAY == 2.0

    # sp1 ReAct 常量
    assert config.REACT_MAX_ROUNDS_PAPER_INTAKE == 5
    assert config.REACT_MAX_ROUNDS_PAPER_ANALYSIS == 12
    assert config.TOOL_RESULT_MAX_LENGTH == 8000

    # sp2 常量
    assert config.PLANNING_SOFT_HINT_THRESHOLD == 5
    assert config.REACT_MAX_ROUNDS_RESOURCE_SCOUT == 10
    assert config.REACT_MAX_ROUNDS_PLANNING == 8
    assert config.GIT_CLONE_TIMEOUT == 60
    assert config.PWC_BASE_URL == "https://paperswithcode.com/api/v1"
    assert config.STREAMLIT_PAGE_INPUT == "input"
    assert config.STREAMLIT_PAGE_PROGRESS == "progress"
    assert config.STREAMLIT_PAGE_REVIEW == "review"


def test_cp_a1_4_git_diff_config_is_pure_append() -> None:
    """CP-A1-4: git diff 实证 config.py 为纯追加（0 删除行）。

    sp3 A1 约束 config.py 只追加常量，不动任何既有行。用 `git diff HEAD -- config.py`
    的 unified diff 统计删除行（以 '-' 开头但非 '---' 文件头），断言为 0。
    """
    proc = subprocess.run(
        ["git", "diff", "HEAD", "--", "config.py"],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        check=True,
    )
    diff = proc.stdout

    deletions = [
        line
        for line in diff.splitlines()
        if line.startswith("-") and not line.startswith("---")
    ]
    assert deletions == [], (
        f"config.py 必须为纯追加（0 删改），实测删除行：{deletions}"
    )

    # 至少应有新增行（防误判：若 diff 为空说明改动未落盘，也算异常）
    additions = [
        line
        for line in diff.splitlines()
        if line.startswith("+") and not line.startswith("+++")
    ]
    assert additions, "config.py 应存在新增行（sp3 A1 追加的常量），diff 为空说明改动未落盘"


# ========== Aux：env 覆盖设计声明（sp3 同类常量沿用 sp1/sp2 字面量风格） ==========


def test_aux_no_env_override_for_sp3_literals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Aux（env 覆盖设计声明）：sp3 新增常量为纯字面量，无 env 覆盖。

    A1 dev 决策声明 "沿用 sp1/sp2 字面量风格，无 os.getenv 覆盖"。
    设置同名 env 后 reload config，断言常量值不被 env 撬动。
    """
    import config as config_module

    monkeypatch.setenv("SANDBOX_EXEC_TIMEOUT", "999")
    monkeypatch.setenv("MAX_DEV_LOOP_LLM_CALLS", "999")
    monkeypatch.setenv("REACT_MAX_ROUNDS_CODING", "999")
    monkeypatch.setenv("STREAMLIT_PAGE_EXECUTION", "evil")

    reloaded = importlib.reload(config_module)
    try:
        assert reloaded.SANDBOX_EXEC_TIMEOUT == 1800
        assert reloaded.MAX_DEV_LOOP_LLM_CALLS == 20
        assert reloaded.REACT_MAX_ROUNDS_CODING == 12
        assert reloaded.STREAMLIT_PAGE_EXECUTION == "execution"
    finally:
        for key in (
            "SANDBOX_EXEC_TIMEOUT",
            "MAX_DEV_LOOP_LLM_CALLS",
            "REACT_MAX_ROUNDS_CODING",
            "STREAMLIT_PAGE_EXECUTION",
        ):
            monkeypatch.delenv(key, raising=False)
        importlib.reload(config_module)
