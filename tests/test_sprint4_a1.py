"""Sprint 4 任务 A1 自测：config.py 新增 sp4 三常量（S4-10）。

覆盖 dev-plan §4 任务 A1 CP-A1-1 ~ CP-A1-3（程序化可验证的 3 个 checkpoint）。

参考实现：sp2 同款风格 tests/test_sprint2_a4.py（轻量结构性断言，无真实 LLM）。

约束：A1 只追加常量（Sprint 4 独立段落），禁止修改 sp1~sp3 既有常量；
纯追加以 `git diff -- config.py` 人工核实为准（CP-A1-3 附加项）。
"""

from __future__ import annotations


# ========== CP-A1-1：三常量可导入且值 / 类型逐项断言 ==========


def test_cp_a1_1_new_constants_importable() -> None:
    """CP-A1-1: sp4 新增三常量全部可从 config 顶层导入。"""
    from config import (  # noqa: F401
        REACT_MAX_ROUNDS_EXECUTION,
        RUN_COMMAND_TIMEOUT,
        SECRETS_FILE_NAME,
    )


def test_cp_a1_1_new_constants_values_and_types() -> None:
    """CP-A1-1: 值与类型逐项断言（10 int / 120 int / ".secrets" str）。

    注意：bool 是 int 子类，用 `type(x) is int` 严格排除 bool 漂移。
    """
    import config

    assert config.REACT_MAX_ROUNDS_EXECUTION == 10
    assert type(config.REACT_MAX_ROUNDS_EXECUTION) is int

    assert config.RUN_COMMAND_TIMEOUT == 120
    assert type(config.RUN_COMMAND_TIMEOUT) is int

    assert config.SECRETS_FILE_NAME == ".secrets"
    assert type(config.SECRETS_FILE_NAME) is str


# ========== CP-A1-2：边界断言 ==========


def test_cp_a1_2_run_command_timeout_below_sandbox_exec_timeout() -> None:
    """CP-A1-2: RUN_COMMAND_TIMEOUT < SANDBOX_EXEC_TIMEOUT（120 < 1800，AC-S4-02 直接验收点）。"""
    import config

    assert config.RUN_COMMAND_TIMEOUT < config.SANDBOX_EXEC_TIMEOUT
    assert config.SANDBOX_EXEC_TIMEOUT == 1800


def test_cp_a1_2_execution_rounds_within_dev_loop_budget() -> None:
    """CP-A1-2: REACT_MAX_ROUNDS_EXECUTION <= MAX_DEV_LOOP_LLM_CALLS（10 <= 60）。

    含义：单次内嵌子图 invoke 不可能一次击穿修复循环子预算。
    """
    import config

    assert config.REACT_MAX_ROUNDS_EXECUTION <= config.MAX_DEV_LOOP_LLM_CALLS


# ========== CP-A1-3：既有常量基线不动 ==========


def test_cp_a1_3_existing_constants_baseline_unchanged() -> None:
    """CP-A1-3: sp1~sp3 既有关键常量基线值不动（防 A1 追加时误改）。"""
    import config

    assert config.MAX_TOTAL_LLM_CALLS == 120
    assert config.MAX_FIX_LOOP_COUNT == 10
    assert config.MAX_DEV_LOOP_LLM_CALLS == 60
    assert config.DEV_LOOP_MIN_CALLS_PER_ROUND == 2
    assert config.REACT_MAX_ROUNDS_CODING == 12
