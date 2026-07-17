"""Sprint 6 · MF-2 / AC-S6-18：论文卡片作者字段人名友好展示（渲染函数单测）。

覆盖 AC-S6-18：作者字段人名友好展示，任意异形结构**不裸渲染 dict** 且有兜底。
批次 5 收口补测——`ui/pages/paper_input.py::_humanize_authors`（批次 0 MF-2 实现，此前零单测，
AC 覆盖矩阵审计标为缺测）。含走查异形样本 + 缺 name dict 占位（不泄漏 repr）。
"""

from __future__ import annotations

import pytest

from ui.pages.paper_input import _humanize_authors, _AUTHOR_UNKNOWN


# ======================================================================
# 五形态友好展示
# ======================================================================
def test_str_passthrough():
    assert _humanize_authors("Alice, Bob") == "Alice, Bob"


def test_none_returns_empty_no_crash():
    assert _humanize_authors(None) == ""


def test_dict_with_name():
    assert _humanize_authors({"name": "Alice Zhang"}) == "Alice Zhang"


def test_dict_walkthrough_forensic_sample():
    """走查异形样本 {'misc':{},'name':'Bernal...'}：有 name → 取 name（AC-S6-18 现场靶）。"""
    sample = {"misc": {}, "name": "Bernal Jiménez"}
    assert _humanize_authors(sample) == "Bernal Jiménez"


def test_list_of_strings():
    assert _humanize_authors(["Alice", "Bob"]) == "Alice, Bob"


def test_list_of_dicts_with_name():
    assert _humanize_authors([{"name": "Alice"}, {"name": "Bob"}]) == "Alice, Bob"


def test_list_mixed_forms():
    got = _humanize_authors(["Alice", {"name": "Bob"}, None])
    assert got == "Alice, Bob"  # None 跳过


def test_scalar_fallback():
    assert _humanize_authors(123) == "123"


# ======================================================================
# AC-S6-18 核心：任意异形结构不裸渲染 dict repr
# ======================================================================
def test_dict_without_name_no_raw_repr():
    """缺 name 的 dict → 占位兜底，**不泄漏裸 dict repr**（AC-S6-18 核心）。"""
    got = _humanize_authors({"misc": {}})
    assert got == _AUTHOR_UNKNOWN
    assert "{" not in got and "}" not in got, "不得裸渲染 dict repr"
    assert "misc" not in got


def test_list_item_dict_without_name_no_raw_repr():
    """列表内缺 name 的 dict 项同样占位，不泄漏 repr。"""
    got = _humanize_authors([{"misc": {}}, {"name": "Alice"}])
    assert got == f"{_AUTHOR_UNKNOWN}, Alice"
    assert "{" not in got and "}" not in got


def test_dict_empty_no_raw_repr():
    got = _humanize_authors({})
    assert got == _AUTHOR_UNKNOWN
    assert "{" not in got


# ======================================================================
# 超长截断
# ======================================================================
def test_long_authors_truncated():
    long_authors = ", ".join(f"Author{i}" for i in range(50))
    got = _humanize_authors(long_authors)
    assert len(got) == 100  # 97 + "..."
    assert got.endswith("...")


@pytest.mark.parametrize("bad", [{"misc": {}}, [{"x": 1}], {}, [{}]])
def test_various_abnormal_never_leak_dict_repr(bad):
    """任意异形结构渲染结果均不含裸 dict repr（AC-S6-18 泛化守门）。"""
    got = _humanize_authors(bad)
    assert isinstance(got, str)
    assert "{" not in got and "}" not in got, f"{bad!r} 泄漏了 dict repr: {got!r}"
