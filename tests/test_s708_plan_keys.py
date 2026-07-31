"""S7-08 机制性防线：ReproductionPlan 三方键集合相等。

架构 §18.1.1 / dev-plan §34.3(1)。本文件**只承载一条断言**，刻意极小。

存在理由（一次性关死"加键只改一处"）：
`ReproductionPlan` 有两个独立构造点——`_build_reproduction_plan`（正常路径）与
`_minimal_plan`（降级路径）。给 TypedDict 加键时只改其中一处，正常路径全绿、
降级路径悄悄丢键，mock 测试照样 passed。本断言让"漏改"在写错那一刻变红，
而不是等收口回归时才发现。

⚠ 已知 bug 模式 #6（S7-06 撞过两次）：`core/nodes/__init__.py` 显式 export 的
callable 会遮蔽同名子模块属性，访问模块级私有函数必须走 importlib.import_module，
不得写 `from core.nodes import planning`。
"""

from __future__ import annotations

import importlib

from core.state import ReproductionPlan


def test_reproduction_plan_three_way_key_sets_are_equal() -> None:
    """TypedDict 声明 / 正常构造 / 降级构造 三方键集合必须逐字相等。"""
    planning = importlib.import_module("core.nodes.planning")

    declared = set(ReproductionPlan.__annotations__)
    built = set(planning._build_reproduction_plan({}, {}).keys())
    minimal = set(planning._minimal_plan({}, "x").keys())

    assert declared == built, (
        "ReproductionPlan 声明键与 _build_reproduction_plan 构造键不一致；"
        f"声明多出={sorted(declared - built)}，构造多出={sorted(built - declared)}"
    )
    assert declared == minimal, (
        "ReproductionPlan 声明键与 _minimal_plan 降级构造键不一致；"
        f"声明多出={sorted(declared - minimal)}，降级多出={sorted(minimal - declared)}"
    )
