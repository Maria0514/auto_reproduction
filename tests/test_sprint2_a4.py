"""Sprint 2 任务 A4 自测：config.py 新增 sp2 常量。

覆盖 dev-plan §A4 CP-A4-1 ~ CP-A4-4（程序化可验证的 4 个 checkpoint）。

参考实现：sp1/sp2 同款风格 tests/test_sprint2_a1.py（轻量结构性断言，无真实 LLM）。

约束：A4 只追加常量，禁止修改 sp1 既有常量（尤其 MAX_TOTAL_LLM_CALLS = 50）。
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest


# ========== CP-A4-1：sp2 新增常量全部可导入 ==========


def test_cp_a4_1_all_new_constants_importable() -> None:
    """CP-A4-1: dev-plan 列出的 sp2 新增常量全部可从 config 顶层导入。
    注：Sprint 6 MF-5 已删除 PWC_* 四常量（PwC 下线），本测试同步移除对应项。
    """
    from config import (  # noqa: F401
        GIT_CLONE_TIMEOUT,
        PLANNING_SOFT_HINT_THRESHOLD,
        REACT_MAX_ROUNDS_PLANNING,
        REACT_MAX_ROUNDS_RESOURCE_SCOUT,
        STREAMLIT_POLL_INTERVAL,
        WORKSPACE_REPOS_DIR,
    )

    # 顺带覆盖 dev-plan 表格里其余新增常量，确保无遗漏
    from config import (  # noqa: F401
        GIT_CLONE_DEPTH,
        STREAMLIT_PAGE_INPUT,
        STREAMLIT_PAGE_PROGRESS,
        STREAMLIT_PAGE_REVIEW,
        URL_REACHABLE_TIMEOUT,
    )


# ========== CP-A4-2：关键常量值正确 ==========


def test_cp_a4_2_constant_values() -> None:
    """CP-A4-2: PLANNING_SOFT_HINT_THRESHOLD/REACT_MAX_ROUNDS_* 默认值与 dev-plan 一致。
    注：Sprint 6 MF-5 已删除 PWC_* 四常量（PwC 下线），本测试同步移除对应项。
    """
    import config

    assert config.PLANNING_SOFT_HINT_THRESHOLD == 5
    assert config.REACT_MAX_ROUNDS_RESOURCE_SCOUT == 10
    assert config.REACT_MAX_ROUNDS_PLANNING == 8

    # 全表值逐项断言（防回归 / 防默认值漂移）
    assert config.GIT_CLONE_TIMEOUT == 60
    assert config.GIT_CLONE_DEPTH == 1
    assert config.URL_REACHABLE_TIMEOUT == 5
    assert config.STREAMLIT_POLL_INTERVAL == 1500
    assert config.STREAMLIT_PAGE_INPUT == "input"
    assert config.STREAMLIT_PAGE_PROGRESS == "progress"
    assert config.STREAMLIT_PAGE_REVIEW == "review"


def test_cp_a4_2_types_are_strict() -> None:
    """CP-A4-2 补：数值常量为严格 int（非 bool），路径常量为 Path，URL/路由为 str。
    注：Sprint 6 MF-5 已删除 PWC_* 四常量（PwC 下线），本测试同步移除对应项。
    """
    import config

    for name in (
        "PLANNING_SOFT_HINT_THRESHOLD",
        "REACT_MAX_ROUNDS_RESOURCE_SCOUT",
        "REACT_MAX_ROUNDS_PLANNING",
        "GIT_CLONE_TIMEOUT",
        "GIT_CLONE_DEPTH",
        "URL_REACHABLE_TIMEOUT",
        "STREAMLIT_POLL_INTERVAL",
    ):
        assert type(getattr(config, name)) is int, f"{name} 应为严格 int"

    assert isinstance(config.WORKSPACE_REPOS_DIR, Path)
    for name in (
        "STREAMLIT_PAGE_INPUT",
        "STREAMLIT_PAGE_PROGRESS",
        "STREAMLIT_PAGE_REVIEW",
    ):
        assert isinstance(getattr(config, name), str), f"{name} 应为 str"


def test_cp_a4_2_workspace_repos_dir_under_workspace() -> None:
    """CP-A4-2 补：WORKSPACE_REPOS_DIR == WORKSPACE_DIR / 'repos'。"""
    import config

    assert config.WORKSPACE_REPOS_DIR == config.WORKSPACE_DIR / "repos"


# ========== CP-A4-3：ensure_directories 创建 repos 目录 ==========


def test_cp_a4_3_ensure_directories_creates_repos_dir() -> None:
    """CP-A4-3: 调用 ensure_directories() 后 WORKSPACE_REPOS_DIR.is_dir() 为 True。"""
    import config

    config.ensure_directories()
    assert config.WORKSPACE_REPOS_DIR.is_dir(), (
        "ensure_directories() 未创建 WORKSPACE_REPOS_DIR"
    )


# ========== CP-A4-4：sp1 既有常量零修改 ==========


def test_cp_a4_4_sp1_constants_unchanged() -> None:
    """CP-A4-4: sp1 既有关键常量基线断言（MAX_TOTAL_LLM_CALLS / MAX_FIX_LOOP_COUNT
    默认值已于 2026-06 经 Maria 拍板放大为 120 / 10）。"""
    import config

    assert config.MAX_TOTAL_LLM_CALLS == 120, "MAX_TOTAL_LLM_CALLS 默认放大为 120（2026-06 Maria 拍板）"
    assert config.MAX_NODE_LLM_CALLS == 10
    assert config.MAX_FIX_LOOP_COUNT == 10, "MAX_FIX_LOOP_COUNT 默认放大为 10（2026-06 Maria 拍板）"
    assert config.REACT_MAX_ROUNDS_PAPER_INTAKE == 5
    assert config.REACT_MAX_ROUNDS_PAPER_ANALYSIS == 12
    assert config.REACT_LLM_TEMPERATURE == 0.3
    assert config.REACT_RESULT_TAG_OPEN == "<result>"
    assert config.REACT_RESULT_TAG_CLOSE == "</result>"
    assert config.TOOL_RESULT_MAX_LENGTH == 8000
    assert config.LLM_REQUEST_TIMEOUT == 60
    assert config.DEFAULT_LLM_TEMPERATURE == 0.3
    assert config.DEFAULT_LLM_MAX_TOKENS == 8192
    assert config.LLM_MAX_RETRIES == 3
    assert config.LLM_INITIAL_RETRY_DELAY == 2.0


# ========== 测试工程师独立补强（Aux）：A4→A5 契约 / 语义 / env 声明 ==========


def test_aux_1_repos_dir_is_resolved_subdir_of_workspace() -> None:
    """Aux-1（A4→A5 关键不变量）：WORKSPACE_REPOS_DIR.resolve() 严格位于
    WORKSPACE_DIR.resolve() 之下。

    A5 git_clone 的越界校验（dev-plan L468）用 ``resolve() + is_relative_to``
    判定 dest_dir 是否在 WORKSPACE_DIR 内；若 WORKSPACE_REPOS_DIR 经 resolve
    后（含符号链接 / .. 解析）不再落在 WORKSPACE_DIR 之下，A5 会把合法的
    clone 目标误判为越界。开发代理的 ``==`` 断言只比较未 resolve 的拼接路径，
    不覆盖 resolve 后的真实包含关系，这里补上。
    """
    import config

    repos = config.WORKSPACE_REPOS_DIR.resolve()
    workspace = config.WORKSPACE_DIR.resolve()

    assert repos.is_relative_to(workspace), (
        f"WORKSPACE_REPOS_DIR({repos}) 必须位于 WORKSPACE_DIR({workspace}) 之下"
    )
    # 必须是真子目录，不能等于 WORKSPACE_DIR 本身
    assert repos != workspace
    assert repos.parent == workspace


def test_aux_3_timeout_and_threshold_semantics() -> None:
    """Aux-3（语义合理性）：超时 / 阈值均为正数。
    注：Sprint 6 MF-5 已删除 PWC_* 四常量（PwC 下线），Aux-2 也一同移除。
    """
    import config

    for name in (
        "GIT_CLONE_TIMEOUT",
        "GIT_CLONE_DEPTH",
        "URL_REACHABLE_TIMEOUT",
        "STREAMLIT_POLL_INTERVAL",
        "PLANNING_SOFT_HINT_THRESHOLD",
        "REACT_MAX_ROUNDS_RESOURCE_SCOUT",
        "REACT_MAX_ROUNDS_PLANNING",
    ):
        assert getattr(config, name) > 0, f"{name} 应为正数"


def test_aux_4_streamlit_page_constants_distinct() -> None:
    """Aux-4：三个 UI 路由常量互不相同（否则页面路由会撞键）。"""
    import config

    pages = {
        config.STREAMLIT_PAGE_INPUT,
        config.STREAMLIT_PAGE_PROGRESS,
        config.STREAMLIT_PAGE_REVIEW,
    }
    assert len(pages) == 3, "STREAMLIT_PAGE_* 三个路由常量必须互不相同"


def test_aux_5_no_env_override_for_sp2_literals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Aux-5（env 覆盖设计声明）：sp2 新增常量为纯字面量，无 env 覆盖。

    A4 dev 决策声明 "沿用 sp1 字面量风格，无 os.getenv 覆盖"（区别于 sp1
    base_url/model 走 getter、LLM_ENABLE_PROMPT_CACHE 走 _parse_bool_env）。
    这里设置同名 env 后 reload config，断言常量值不被 env 撬动，锁定该设计声明。
    """
    import config as config_module

    monkeypatch.setenv("REACT_MAX_ROUNDS_RESOURCE_SCOUT", "999")
    monkeypatch.setenv("GIT_CLONE_TIMEOUT", "999")
    monkeypatch.setenv("STREAMLIT_POLL_INTERVAL", "999")
    monkeypatch.setenv("PLANNING_SOFT_HINT_THRESHOLD", "999")

    reloaded = importlib.reload(config_module)
    try:
        assert reloaded.REACT_MAX_ROUNDS_RESOURCE_SCOUT == 10
        assert reloaded.GIT_CLONE_TIMEOUT == 60
        assert reloaded.STREAMLIT_POLL_INTERVAL == 1500
        assert reloaded.PLANNING_SOFT_HINT_THRESHOLD == 5
    finally:
        # 清理 env 并 reload 回到干净状态，保证不污染后续 test
        for key in (
            "REACT_MAX_ROUNDS_RESOURCE_SCOUT",
            "GIT_CLONE_TIMEOUT",
            "STREAMLIT_POLL_INTERVAL",
            "PLANNING_SOFT_HINT_THRESHOLD",
        ):
            monkeypatch.delenv(key, raising=False)
        importlib.reload(config_module)


def test_aux_6_ensure_directories_idempotent() -> None:
    """Aux-6：ensure_directories() 二次调用幂等（exist_ok），不抛异常。"""
    import config

    config.ensure_directories()
    config.ensure_directories()  # 再调一次不应因目录已存在报错
    assert config.WORKSPACE_REPOS_DIR.is_dir()
    assert config.WORKSPACE_DIR.is_dir()
    assert config.LOG_DIR.is_dir()
