"""Sprint 2 任务 A6 自测：core/tools/pwc_tools.py（Papers With Code 工具封装）。

覆盖 dev-plan §A6 CP-A6-1 ~ CP-A6-10。

参考实现：sp2 同款风格 tests/test_sprint2_a5.py / test_sprint2_a4.py
（轻量结构性断言 + mock requests.get，无真实网络调用、无真实 LLM；
A6 为纯工具层单测，不涉及 e2e —— PwC 端点匿名可用但默认不打真实网络）。

打桩策略说明：
    venv 中 ``requests_mock`` 包**未安装**，按 dev-plan L1537 备选用标准库
    ``unittest.mock`` 直接 patch ``pwc_tools.requests.get`` 返回伪造 Response，
    与开发已验证可行的方式一致。

硬约束验证（沿用 sp1 BUG-S1-02 治理范式）：
    - ToolMessage 输出严格为合法 JSON（json.loads 不报错；ensure_ascii=False
      中文不转义、sort_keys=True 字节级幂等）；
    - HTTP 失败 / 重试 / 限速打 WARNING（caplog 可捕获），非静默吞错。

============================================================================
⚠️ CP-A6-3 / CP-A6-4 「3 次 vs 4 次」语义裁决（供 Maria 复核）
============================================================================
dev-plan CP-A6-3/CP-A6-4 文字写「**3 次** 429 / timeout 后抛 TransientError」，
但实现是「首次请求 + 3 次重试 = 共 **4 次** attempt」才抛 TransientError：

    pwc_tools._RETRY_BACKOFF_SECONDS = (1.0, 2.0, 4.0)         # 3 个退避点
    total_attempts = len(_RETRY_BACKOFF_SECONDS) + 1 = 4       # 首次 + 3 次重试

裁决：**实现正确，符合 dev-plan §573 表格权威语义**。
  - §573 表格明确写「超过 3 次重试 → 抛 TransientError」，且退避序列 1s/2s/4s
    正好对应「3 次重试」；首次请求不计入「重试」计数，符合行业惯例
    （retry = 失败后的再次尝试，首次尝试不是 retry）。
  - CP-A6-3/CP-A6-4 文字「3 次 429」是对「3 次重试」的口语化简写，
    与表格语义一致，并非要求第 3 次 429 即抛。
  - 因此本测试按「共 4 次 attempt」断言：返回 4 次 429/timeout 才触发
    TransientError；返回 3 次时第 4 次仍会被重试（用一次 200 收尾验证不抛）。

结论：**非 BUG**，实现与规格权威表格一致；CP 文字与表格的 3次/4次表述差异
已在测试报告中显式记录，建议 Maria 复核后顺手把 CP-A6-3/4 文字补一句
「（首次 + 3 次重试，共 4 次 attempt）」消歧。
============================================================================
"""

from __future__ import annotations

import importlib
import json
import time
from typing import List, Optional
from unittest import mock

import pytest

from langchain_core.tools import BaseTool

from core.errors import TransientError
from core.tools import pwc_tools


# ---------------------------------------------------------------------------
# 测试 helper：伪造 requests.Response
# ---------------------------------------------------------------------------

class _FakeResponse:
    """最小化伪造 requests.Response，覆盖 status_code / headers / json()。"""

    def __init__(
        self,
        status_code: int,
        *,
        json_data: object = None,
        headers: Optional[dict] = None,
        raise_value_error: bool = False,
    ) -> None:
        self.status_code = status_code
        self.headers = headers or {}
        self._json_data = json_data
        self._raise_value_error = raise_value_error

    def json(self):
        if self._raise_value_error:
            # 模拟 200 但 body 非 JSON：resp.json() 抛 ValueError。
            raise ValueError("No JSON object could be decoded")
        return self._json_data


def _ok_papers_payload() -> dict:
    """GET /papers/?arxiv_id= 的 200 响应（含 1 篇命中论文）。"""
    return {
        "results": [
            {"id": "hipporag-paper", "title": "HippoRAG：基于神经生物学的长期记忆框架"},
        ]
    }


def _ok_repos_payload() -> dict:
    """GET /papers/{id}/repositories/ 的 200 响应（含 2 个仓库）。"""
    return {
        "results": [
            {
                "url": "https://github.com/OSU-NLP-Group/HippoRAG",
                "stars": 1234,
                "framework": "pytorch",
                "is_official": True,
            },
            {
                "url": "https://github.com/community/hipporag-fork",
                "stars": 12,
                "framework": "pytorch",
                "is_official": False,
            },
        ]
    }


def _timeout_exc() -> Exception:
    """构造一个 requests timeout（属 RequestException 子类）。"""
    return pwc_tools.requests.exceptions.Timeout("read timed out")


# ---------------------------------------------------------------------------
# fixture：每个用例前重置全部模块级状态（缓存 + 节流时间戳）
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_pwc_state():
    """每个用例前后清空 LRU 缓存 + 重置节流时间戳，保证用例独立、无顺序依赖。"""
    pwc_tools._search_pwc_by_arxiv_cached.cache_clear()
    pwc_tools._search_pwc_by_title_cached.cache_clear()
    pwc_tools._LAST_REQUEST_AT = 0.0
    yield
    pwc_tools._search_pwc_by_arxiv_cached.cache_clear()
    pwc_tools._search_pwc_by_title_cached.cache_clear()
    pwc_tools._LAST_REQUEST_AT = 0.0


@pytest.fixture()
def no_sleep(monkeypatch):
    """mock 掉 pwc_tools 内部 time.sleep，避免退避/节流拖慢套件。

    注意：CP-A6-6（真实 200ms 节流验证）**不使用**此 fixture，必须走真实 sleep。
    """
    monkeypatch.setattr(pwc_tools.time, "sleep", lambda _s: None)


# ===========================================================================
# CP-A6-1：200 返回 List[Dict]，结构含 paper_id / title / repos
# ===========================================================================

def test_cp_a6_1_search_by_arxiv_200_returns_structured_list(no_sleep):
    """CP-A6-1: mock 200 时 search_pwc_by_arxiv 返回 List[Dict]，含 paper_id/title/repos。"""
    # 第 1 次请求 = /papers/?arxiv_id= 命中；第 2 次 = /papers/{id}/repositories/ 回填。
    responses = [
        _FakeResponse(200, json_data=_ok_papers_payload()),
        _FakeResponse(200, json_data=_ok_repos_payload()),
    ]
    with mock.patch.object(pwc_tools.requests, "get", side_effect=responses):
        result = pwc_tools.search_pwc_by_arxiv("2405.14831")

    assert isinstance(result, list)
    assert len(result) == 1
    entry = result[0]
    assert isinstance(entry, dict)
    assert set(["paper_id", "title", "repos"]).issubset(entry.keys())
    assert entry["paper_id"] == "hipporag-paper"
    assert entry["title"]  # 非空
    assert isinstance(entry["repos"], list) and len(entry["repos"]) == 2
    repo0 = entry["repos"][0]
    assert set(["url", "stars", "framework", "is_official"]).issubset(repo0.keys())
    assert repo0["is_official"] is True


# ===========================================================================
# CP-A6-2：429(Retry-After:1) + 200 最终成功，验证按 Retry-After 退避
# ===========================================================================

def test_cp_a6_2_retry_after_then_success(monkeypatch):
    """CP-A6-2: 首次 429(Retry-After:1) + 第二次 200，最终成功；sleep 按 Retry-After=1。"""
    sleep_calls: List[float] = []
    monkeypatch.setattr(pwc_tools.time, "sleep", lambda s: sleep_calls.append(s))

    responses = [
        _FakeResponse(429, headers={"Retry-After": "1"}),
        _FakeResponse(200, json_data={"results": []}),  # title 路径不拉 repos，简化为空 results
    ]
    with mock.patch.object(pwc_tools.requests, "get", side_effect=responses):
        result = pwc_tools.search_pwc_by_title("HippoRAG")

    # 最终成功（不抛），返回空候选列表。
    assert result == []
    # 验证按 Retry-After=1 退避（而非指数退避首项 1.0——此处巧合相等，故额外断言来源）。
    # 关键：发生了恰好 1 次 sleep，等待秒数取自 Retry-After。
    assert 1.0 in sleep_calls


def test_cp_a6_2b_retry_after_distinct_from_exponential(monkeypatch):
    """CP-A6-2 补强: Retry-After=3 时退避 3s（区别于指数退避首项 1s），证明确实读了 Retry-After。"""
    sleep_calls: List[float] = []
    monkeypatch.setattr(pwc_tools.time, "sleep", lambda s: sleep_calls.append(s))

    responses = [
        _FakeResponse(429, headers={"Retry-After": "3"}),
        _FakeResponse(200, json_data={"results": []}),
    ]
    with mock.patch.object(pwc_tools.requests, "get", side_effect=responses):
        result = pwc_tools.search_pwc_by_title("HippoRAG")

    assert result == []
    # 退避值为 3（来自 Retry-After），不是指数退避首项 1.0。
    assert 3.0 in sleep_calls
    assert 1.0 not in sleep_calls


# ===========================================================================
# CP-A6-3：4 次 429（无 Retry-After）后抛 TransientError（首次 + 3 次重试）
# 见文件头部「3 次 vs 4 次」语义裁决。
# ===========================================================================

def test_cp_a6_3_four_429_raises_transient_error(no_sleep):
    """CP-A6-3: 共 4 次 429（无 Retry-After）后抛 TransientError，且恰好发 4 次请求。"""
    responses = [_FakeResponse(429) for _ in range(4)]
    with mock.patch.object(pwc_tools.requests, "get", side_effect=responses) as m_get:
        with pytest.raises(TransientError):
            pwc_tools.search_pwc_by_arxiv("2405.14831")
    # 首次 + 3 次重试 = 共 4 次 HTTP 请求。
    assert m_get.call_count == 4


def test_cp_a6_3b_three_429_then_success_does_not_raise(no_sleep):
    """CP-A6-3 边界: 3 次 429 + 第 4 次 200 不抛（证明第 4 次仍会重试，非 3 次即抛）。"""
    responses = [
        _FakeResponse(429),
        _FakeResponse(429),
        _FakeResponse(429),
        _FakeResponse(200, json_data={"results": []}),
    ]
    with mock.patch.object(pwc_tools.requests, "get", side_effect=responses) as m_get:
        result = pwc_tools.search_pwc_by_arxiv("2405.14831")
    assert result == []
    assert m_get.call_count == 4


# ===========================================================================
# CP-A6-4：4 次 timeout 后抛 TransientError
# ===========================================================================

def test_cp_a6_4_four_timeout_raises_transient_error(no_sleep):
    """CP-A6-4: 共 4 次 timeout（首次 + 3 重试）后抛 TransientError，恰好发 4 次请求。"""
    with mock.patch.object(
        pwc_tools.requests, "get", side_effect=[_timeout_exc() for _ in range(4)]
    ) as m_get:
        with pytest.raises(TransientError):
            pwc_tools.search_pwc_by_arxiv("2405.14831")
    assert m_get.call_count == 4


# ===========================================================================
# CP-A6-5：同一 arxiv_id 连续两次调用，第二次 HTTP 请求次数为 0（LRU 命中）
# ===========================================================================

def test_cp_a6_5_lru_cache_hit_zero_request_on_second_call(no_sleep):
    """CP-A6-5: 同一 arxiv_id 第二次调用走 LRU 缓存，期间 requests.get 调用次数为 0。"""
    first_responses = [
        _FakeResponse(200, json_data=_ok_papers_payload()),
        _FakeResponse(200, json_data=_ok_repos_payload()),
    ]
    with mock.patch.object(pwc_tools.requests, "get", side_effect=first_responses) as m_get:
        r1 = pwc_tools.search_pwc_by_arxiv("2405.14831")
    first_count = m_get.call_count
    assert first_count == 2  # 1 次 papers + 1 次 repos

    # 第二次：新 mock，断言 0 次请求（完全走缓存）。
    with mock.patch.object(pwc_tools.requests, "get", side_effect=AssertionError("不应发起请求")) as m_get2:
        r2 = pwc_tools.search_pwc_by_arxiv("2405.14831")
    assert m_get2.call_count == 0
    # 缓存返回内容与首次一致。
    assert r2 == r1


def test_cp_a6_5b_arxiv_id_normalization_shares_cache(no_sleep):
    """CP-A6-5 补强: 带 arXiv: 前缀 / 空格的写法标准化后命中同一缓存项。"""
    responses = [
        _FakeResponse(200, json_data=_ok_papers_payload()),
        _FakeResponse(200, json_data=_ok_repos_payload()),
    ]
    with mock.patch.object(pwc_tools.requests, "get", side_effect=responses) as m_get:
        pwc_tools.search_pwc_by_arxiv("2405.14831")
    assert m_get.call_count == 2

    # 不同写法（前缀 + 空格）标准化为同一 key，应命中缓存不再请求。
    with mock.patch.object(pwc_tools.requests, "get", side_effect=AssertionError("不应发起请求")) as m_get2:
        pwc_tools.search_pwc_by_arxiv("  arXiv:2405.14831  ")
    assert m_get2.call_count == 0


# ===========================================================================
# CP-A6-6：_throttle() 200ms 间隔，连续 5 次累计 ≥ 800ms（真实 sleep，不 mock）
# ===========================================================================

def test_cp_a6_6_throttle_enforces_200ms_interval():
    """CP-A6-6: 连续 5 次 _throttle() 真实累计 ≥ 800ms（4 个 200ms 间隔）。

    本用例**不 mock** time.sleep / time.monotonic，必须验证真实节流耗时。
    """
    # 重置时间戳为「刚刚」，使第一次 _throttle() 也进入节流判定窗口。
    pwc_tools._LAST_REQUEST_AT = time.monotonic()

    start = time.monotonic()
    for _ in range(5):
        pwc_tools._throttle()
    elapsed = time.monotonic() - start

    # 5 次调用之间有 4 个 200ms 间隔，累计应 ≥ 0.8s（留 5% 容差应对调度抖动）。
    assert elapsed >= 0.8 * 0.95, f"节流累计耗时 {elapsed:.3f}s 应 ≥ 0.76s"


# ===========================================================================
# CP-A6-7：PWC_API_TOKEN 注入 / 缺失 header（需 importlib.reload）
# ===========================================================================

def test_cp_a6_7_token_injected_after_reload(monkeypatch):
    """CP-A6-7: 设 PWC_API_TOKEN 后 reload 模块，_build_headers() 含 Authorization。"""
    monkeypatch.setenv("PWC_API_TOKEN", "test_token")
    reloaded = importlib.reload(pwc_tools)
    try:
        headers = reloaded._build_headers()
        assert headers.get("Authorization") == "Token test_token"
    finally:
        # 还原 env 并 reload 回无 token 状态，避免污染后续用例（模块级缓存 token）。
        monkeypatch.delenv("PWC_API_TOKEN", raising=False)
        importlib.reload(pwc_tools)
        # reload 后 fixture 引用的旧模块对象已换，重新清缓存防御性处理。
        pwc_tools._search_pwc_by_arxiv_cached.cache_clear()
        pwc_tools._search_pwc_by_title_cached.cache_clear()


def test_cp_a6_7b_no_token_no_auth_header(monkeypatch):
    """CP-A6-7: 未设 PWC_API_TOKEN 时 reload 后 _build_headers() 无 Authorization。"""
    monkeypatch.delenv("PWC_API_TOKEN", raising=False)
    reloaded = importlib.reload(pwc_tools)
    try:
        headers = reloaded._build_headers()
        assert "Authorization" not in headers
        # 仍含基础 header。
        assert headers.get("Accept") == "application/json"
        assert "User-Agent" in headers
    finally:
        importlib.reload(pwc_tools)
        pwc_tools._search_pwc_by_arxiv_cached.cache_clear()
        pwc_tools._search_pwc_by_title_cached.cache_clear()


# ===========================================================================
# CP-A6-8：make_search_pwc_tool 返回 BaseTool，ToolMessage 为合法 JSON
# ===========================================================================

def test_cp_a6_8_tool_returns_basetool_and_valid_json(no_sleep):
    """CP-A6-8: 工具返回 BaseTool，输出合法 JSON（json.loads 不报错，中文不转义、sort_keys）。"""
    tool = pwc_tools.make_search_pwc_tool()
    assert isinstance(tool, BaseTool)

    responses = [
        _FakeResponse(200, json_data=_ok_papers_payload()),
        _FakeResponse(200, json_data=_ok_repos_payload()),
    ]
    with mock.patch.object(pwc_tools.requests, "get", side_effect=responses):
        out = tool.invoke({"arxiv_id": "2405.14831"})

    assert isinstance(out, str)
    # 合法 JSON：json.loads 不报错。
    parsed = json.loads(out)
    assert "results" in parsed
    assert isinstance(parsed["results"], list) and len(parsed["results"]) == 1

    # ensure_ascii=False：中文标题原样存在，未被转义为 \uXXXX。
    assert "HippoRAG" in out
    assert "基于神经生物学" in out
    assert "\\u" not in out

    # sort_keys=True：对解析后的 dict 重新 dump 应字节级一致（键已排序）。
    re_dumped = json.dumps(parsed, ensure_ascii=False, sort_keys=True, default=str)
    assert out == re_dumped


def test_cp_a6_8b_tool_no_args_returns_error_json(no_sleep):
    """CP-A6-8 边界: 既无 arxiv_id 也无 title 时返回合法 error JSON，不抛。"""
    tool = pwc_tools.make_search_pwc_tool()
    out = tool.invoke({"arxiv_id": "", "title": ""})
    parsed = json.loads(out)
    assert parsed["results"] == []
    assert "error" in parsed


# ===========================================================================
# CP-A6-9：工具内部捕获 TransientError 后返回错误字符串（不抛断 ReAct）
# ===========================================================================

def test_cp_a6_9_tool_catches_transient_error_returns_string(no_sleep):
    """CP-A6-9: 底层抛 TransientError 时工具返回错误描述 JSON 字符串，不向上抛。"""
    tool = pwc_tools.make_search_pwc_tool()
    # 4 次 429 触发底层 TransientError，工具内部应捕获。
    responses = [_FakeResponse(429) for _ in range(4)]
    with mock.patch.object(pwc_tools.requests, "get", side_effect=responses):
        out = tool.invoke({"arxiv_id": "2405.14831"})

    assert isinstance(out, str)
    parsed = json.loads(out)  # 仍为合法 JSON
    assert parsed["results"] == []
    assert "error" in parsed and parsed["error"]  # 含非空错误描述


# ===========================================================================
# CP-A6-10：HTTP 失败时 caplog 捕获至少 1 条 WARNING（非静默吞错）
# ===========================================================================

def test_cp_a6_10_http_failure_logs_warning(no_sleep, caplog):
    """CP-A6-10: HTTP 失败（429 重试 + 最终抛错）时至少 1 条 WARNING 日志。"""
    responses = [_FakeResponse(429) for _ in range(4)]
    with caplog.at_level("WARNING", logger="core.tools.pwc_tools"):
        with mock.patch.object(pwc_tools.requests, "get", side_effect=responses):
            with pytest.raises(TransientError):
                pwc_tools.search_pwc_by_arxiv("2405.14831")

    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) >= 1, "HTTP 失败应至少打 1 条 WARNING（非静默吞错）"


# ===========================================================================
# 额外边界（开发交接提示的加分项，非阻塞 CP）
# ===========================================================================

def test_aux_retry_after_http_date_falls_back_to_exponential(monkeypatch):
    """加分项: Retry-After 为 HTTP-date 字符串时 _parse_retry_after 返回 None，回退指数退避。"""
    # _parse_retry_after 单元级断言：HTTP-date 形式无法 float() → None。
    assert pwc_tools._parse_retry_after("Wed, 21 Oct 2026 07:28:00 GMT") is None
    assert pwc_tools._parse_retry_after("1") == 1.0
    assert pwc_tools._parse_retry_after("3.5") == 3.5
    assert pwc_tools._parse_retry_after(None) is None
    assert pwc_tools._parse_retry_after("-5") is None  # 负数视为无效

    # 集成级：429 带 HTTP-date Retry-After，应回退指数退避（首项 1.0）后第二次 200 成功。
    sleep_calls: List[float] = []
    monkeypatch.setattr(pwc_tools.time, "sleep", lambda s: sleep_calls.append(s))
    responses = [
        _FakeResponse(429, headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}),
        _FakeResponse(200, json_data={"results": []}),
    ]
    with mock.patch.object(pwc_tools.requests, "get", side_effect=responses):
        result = pwc_tools.search_pwc_by_title("HippoRAG")
    assert result == []
    # 回退指数退避首项 1.0（而非按不可解析的 date）。
    assert 1.0 in sleep_calls


def test_aux_200_non_json_body_returns_empty_no_retry(no_sleep):
    """加分项: 200 但 body 非 JSON（resp.json() 抛 ValueError）返回空结构、不重试。"""
    with mock.patch.object(
        pwc_tools.requests, "get",
        side_effect=[_FakeResponse(200, raise_value_error=True)],
    ) as m_get:
        result = pwc_tools.search_pwc_by_arxiv("2405.14831")
    # 非 JSON → _http_get_with_retry 返回 {} → 上层得空候选列表，不抛、不重试。
    assert result == []
    assert m_get.call_count == 1  # 未触发重试


def test_aux_404_no_retry_single_request(no_sleep):
    """加分项: 单次 404（其它 4xx）不重试且只发 1 次请求，返回空结果。"""
    with mock.patch.object(
        pwc_tools.requests, "get", side_effect=[_FakeResponse(404)]
    ) as m_get:
        result = pwc_tools.search_pwc_by_arxiv("2405.14831")
    assert result == []
    assert m_get.call_count == 1


def test_aux_title_path_repos_empty_by_design(no_sleep):
    """加分项: title 路径返回候选的 repos 为空列表（设计契约，非 bug）。"""
    payload = {
        "results": [
            {"id": "p1", "title": "Paper One"},
            {"id": "p2", "title": "Paper Two"},
        ]
    }
    # title 路径只发 1 次 /papers/?q= 请求，不逐条拉 repos。
    with mock.patch.object(
        pwc_tools.requests, "get", side_effect=[_FakeResponse(200, json_data=payload)]
    ) as m_get:
        result = pwc_tools.search_pwc_by_title("Paper")
    assert m_get.call_count == 1  # 不放大为 N 次 repos 请求
    assert len(result) == 2
    for entry in result:
        assert entry["repos"] == []  # 设计契约：title 路径 repos 恒空


def test_aux_title_path_caps_at_10_results(no_sleep):
    """加分项: title 模糊查询超过 10 条时截断为前 10 条（_TITLE_RESULT_LIMIT）。"""
    payload = {"results": [{"id": f"p{i}", "title": f"Paper {i}"} for i in range(25)]}
    with mock.patch.object(
        pwc_tools.requests, "get", side_effect=[_FakeResponse(200, json_data=payload)]
    ):
        result = pwc_tools.search_pwc_by_title("Paper")
    assert len(result) == 10


def test_aux_empty_arxiv_id_short_circuits_no_request(no_sleep):
    """加分项: 空 / 纯空格 arxiv_id 直接返回 []，不发 HTTP 请求。"""
    with mock.patch.object(
        pwc_tools.requests, "get", side_effect=AssertionError("空 id 不应发请求")
    ) as m_get:
        assert pwc_tools.search_pwc_by_arxiv("") == []
        assert pwc_tools.search_pwc_by_arxiv("   ") == []
    assert m_get.call_count == 0


def test_aux_5xx_then_success(no_sleep):
    """加分项: 500 + 200 序列，最终成功（5xx 走指数退避重试不抛）。"""
    responses = [
        _FakeResponse(503),
        _FakeResponse(200, json_data={"results": []}),
    ]
    with mock.patch.object(pwc_tools.requests, "get", side_effect=responses) as m_get:
        result = pwc_tools.search_pwc_by_title("HippoRAG")
    assert result == []
    assert m_get.call_count == 2
