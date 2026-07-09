"""T-S5-1-1 自测：config 4 常量 + FLOOR 语义（dev-plan CP-1.1-1 ~ CP-1.1-3）。

- CP-1.1-1 四常量可导入、值/类型逐项断言；REACT_MAX_ROUNDS_EXECUTION==10 不动
- CP-1.1-2 账本边界：CAP == MAX_DEV_LOOP_LLM_CALLS // 2 且 CAP > FLOOR > 0（AC-S5-12 常量面）
- CP-1.1-3 既有常量基线零改动（git diff 纯追加实证由开发自测流程执行，见交付说明）
"""

import config


class TestCP111NewConstants:
    """CP-1.1-1：四常量可导入、值/类型逐项断言；FLOOR 值不动。"""

    def test_react_execution_rounds_margin(self):
        assert type(config.REACT_EXECUTION_ROUNDS_MARGIN) is int
        assert config.REACT_EXECUTION_ROUNDS_MARGIN == 5

    def test_react_max_rounds_execution_cap(self):
        assert type(config.REACT_MAX_ROUNDS_EXECUTION_CAP) is int
        assert config.REACT_MAX_ROUNDS_EXECUTION_CAP == 30

    def test_activity_stream_max_events(self):
        assert type(config.ACTIVITY_STREAM_MAX_EVENTS) is int
        assert config.ACTIVITY_STREAM_MAX_EVENTS == 500

    def test_activity_stream_render_tail(self):
        assert type(config.ACTIVITY_STREAM_RENDER_TAIL) is int
        assert config.ACTIVITY_STREAM_RENDER_TAIL == 30

    def test_floor_value_unchanged(self):
        """REACT_MAX_ROUNDS_EXECUTION 值不动（仅注释语义收窄为 FLOOR）。"""
        assert type(config.REACT_MAX_ROUNDS_EXECUTION) is int
        assert config.REACT_MAX_ROUNDS_EXECUTION == 10


class TestCP112BudgetLedgerBoundary:
    """CP-1.1-2：账本边界断言（AC-S5-12 常量面）。"""

    def test_cap_equals_half_dev_loop_budget(self):
        assert (
            config.REACT_MAX_ROUNDS_EXECUTION_CAP
            == config.MAX_DEV_LOOP_LLM_CALLS // 2
        )

    def test_cap_gt_floor_gt_zero(self):
        assert (
            config.REACT_MAX_ROUNDS_EXECUTION_CAP
            > config.REACT_MAX_ROUNDS_EXECUTION
            > 0
        )


class TestCP113ExistingBaselineUnchanged:
    """CP-1.1-3：既有常量基线零改动。"""

    def test_budget_constants_baseline(self):
        assert config.MAX_NODE_LLM_CALLS == 10
        assert config.MAX_TOTAL_LLM_CALLS == 120
        assert config.MAX_FIX_LOOP_COUNT == 10
        assert config.MAX_DEV_LOOP_LLM_CALLS == 60
        assert config.DEV_LOOP_MIN_CALLS_PER_ROUND == 2

    def test_react_rounds_baseline(self):
        assert config.REACT_MAX_ROUNDS_PAPER_INTAKE == 5
        assert config.REACT_MAX_ROUNDS_PAPER_ANALYSIS == 12
        assert config.REACT_MAX_ROUNDS_RESOURCE_SCOUT == 10
        assert config.REACT_MAX_ROUNDS_PLANNING == 8
        assert config.REACT_MAX_ROUNDS_CODING == 12
