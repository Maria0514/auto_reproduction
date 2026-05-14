"""C1 - paper_intake ReAct agent 节点自测。

覆盖 dev-plan.md C1 任务的 8 个自测检查点：
1. paper_intake 是 _make_react_wrapper 生成的 callable
2. ReAct wrapper 正确映射 user_input 到 context
3. Mock LLM + Mock 工具后，agent 可通过工具调用获取 PaperMeta
4. 正常路径：brief + head 完整时，PaperMeta 字段全部填充
5. head 获取失败：agent 仅使用 brief 数据，不中断
6. 学科校验：agent 在结果中标注非 CS 论文警告
7. 错误路径：论文不存在时返回 error 字段和 node_errors
8. ID 格式清洗：完整 URL 输入可被正确处理
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.tools import tool

from core.nodes import paper_intake as paper_intake_module
from core.nodes.paper_intake import (
    NODE_NAME,
    PAPER_META_SCHEMA,
    _build_intake_system_prompt,
    _map_intake_result,
    paper_intake,
)


# ---------- 工具：构造 fake GlobalState / fake LLM ----------


def _make_state(user_input: str) -> Dict[str, Any]:
    return {
        "user_input": user_input,
        "input_type": "arxiv_id",
        "llm_config": {
            "base_url": "https://example.test/v1",
            "model": "test-model",
            "api_key": "sk-test",
            "temperature": 0.3,
            "max_tokens": 1024,
        },
        "retry_budget_remaining": 50,
        "node_errors": [],
        "degraded_nodes": [],
        "messages": [],
    }


class FakeLLM:
    """脚本化 LLM：按 invoke 调用顺序返回预设 AIMessage。"""

    def __init__(self, responses: List[AIMessage]):
        self._responses = list(responses)
        self.calls: List[List[BaseMessage]] = []
        self.bind_tools_calls = 0

    def bind_tools(self, tools):
        self.bind_tools_calls += 1
        return self

    def invoke(self, messages):
        self.calls.append(list(messages))
        if not self._responses:
            return AIMessage(content="<result>{}</result>")
        return self._responses.pop(0)


def _tool_call(name: str, args: Dict[str, Any], call_id: str) -> Dict[str, Any]:
    return {"name": name, "args": args, "id": call_id}


# ---------- 工具 mocks：可被脚本替换返回值 ----------


class ToolScripts:
    """三个 deepxiv 工具的脚本化返回值。"""

    brief: Optional[Callable[[str], Any]] = None
    head: Optional[Callable[[str], Any]] = None
    search: Optional[Callable[[str, int], Any]] = None


def _install_tool_mocks(monkey_attrs: Dict[str, Any]) -> None:
    @tool
    def get_paper_brief(arxiv_id: str) -> str:
        """Mock brief."""
        fn = ToolScripts.brief
        if fn is None:
            return "Error: brief not scripted"
        try:
            res = fn(arxiv_id)
        except Exception as exc:
            return f"Error in get_paper_brief: {exc}"
        return res if isinstance(res, str) else json.dumps(res, ensure_ascii=False)

    @tool
    def get_paper_head(arxiv_id: str) -> str:
        """Mock head."""
        fn = ToolScripts.head
        if fn is None:
            return "Error: head not scripted"
        try:
            res = fn(arxiv_id)
        except Exception as exc:
            return f"Error in get_paper_head: {exc}"
        return res if isinstance(res, str) else json.dumps(res, ensure_ascii=False)

    @tool
    def search_papers(query: str, size: int = 10) -> str:
        """Mock search."""
        fn = ToolScripts.search
        if fn is None:
            return "Error: search not scripted"
        try:
            res = fn(query, size)
        except Exception as exc:
            return f"Error in search_papers: {exc}"
        return res if isinstance(res, str) else json.dumps(res, ensure_ascii=False)

    monkey_attrs["get_paper_brief_tool"] = lambda token=None: get_paper_brief
    monkey_attrs["get_paper_head_tool"] = lambda token=None: get_paper_head
    monkey_attrs["search_papers_tool"] = lambda token=None: search_papers


# ---------- 测试结果汇总 ----------


class Report:
    def __init__(self) -> None:
        self.items: List[tuple] = []

    def add(self, name: str, ok: bool, detail: str = "") -> None:
        self.items.append((name, ok, detail))

    def summary(self) -> str:
        lines = []
        for name, ok, detail in self.items:
            mark = "PASS" if ok else "FAIL"
            line = f"[{mark}] {name}"
            if detail and not ok:
                line += f"  -- {detail}"
            lines.append(line)
        passed = sum(1 for _, ok, _ in self.items if ok)
        lines.append(f"\nTotal: {passed}/{len(self.items)} passed")
        return "\n".join(lines)

    def all_passed(self) -> bool:
        return all(ok for _, ok, _ in self.items)


# ---------- 用例 1：paper_intake 是 callable wrapper ----------


def case_callable(report: Report) -> None:
    name = "[CP1] paper_intake 是 _make_react_wrapper 生成的 callable"
    try:
        assert callable(paper_intake), "paper_intake 不可调用"
        assert paper_intake.__name__ == f"react_wrapper_{NODE_NAME}", (
            f"wrapper __name__ 不符: {paper_intake.__name__}"
        )
        report.add(name, True)
    except Exception as exc:  # noqa: BLE001
        report.add(name, False, str(exc))


# ---------- 用例 2：user_input 正确映射到 context ----------


def case_context_mapping(monkeypatch, report: Report) -> None:
    name = "[CP2] ReAct wrapper 正确映射 user_input 到 context (HumanMessage)"
    captured_initials: List[Dict[str, Any]] = []

    class FakeSubgraph:
        def invoke(self, initial):
            captured_initials.append(initial)
            return {
                "messages": initial["messages"],
                "round": 1,
                "max_rounds": initial["max_rounds"],
                "status": "done",
                "result": {
                    "arxiv_id": "2409.05591",
                    "title": "Stub",
                    "authors": ["A"],
                    "abstract": "x",
                    "categories": ["cs.AI"],
                },
                "context": initial["context"],
            }

    from core import react_base
    monkeypatch.setattr(react_base, "create_react_subgraph", lambda **kwargs: FakeSubgraph())
    monkey_set_llm(monkeypatch, lambda config: FakeLLM([]))

    try:
        state = _make_state("2409.05591")
        update = paper_intake(state)
        assert captured_initials, "subgraph 未被调用"
        msgs = captured_initials[0]["messages"]
        assert isinstance(msgs[0], SystemMessage), "首条不是 SystemMessage"
        # SystemMessage 中不应含动态 user_input 字符串
        assert "2409.05591" not in msgs[0].content, (
            "SystemMessage 含动态 user_input，破坏 Prompt Cache 前缀稳定"
        )
        # HumanMessage 中必须包含 user_input
        assert any(
            isinstance(m, HumanMessage) and "2409.05591" in m.content for m in msgs
        ), "HumanMessage 未携带 user_input"
        assert update.get("current_step") == NODE_NAME
        report.add(name, True)
    except Exception as exc:  # noqa: BLE001
        report.add(name, False, repr(exc))


# ---------- 用例 3-8：使用真实 ReAct 子图 + 脚本化 LLM ----------


def _run_with_scripted_llm(
    monkeypatch,
    user_input: str,
    llm_responses: List[AIMessage],
    brief_fn=None,
    head_fn=None,
    search_fn=None,
) -> tuple:
    """安装脚本化工具 + LLM，运行真实 paper_intake wrapper。"""
    from core import react_base

    # 安装工具 mock
    attrs: Dict[str, Any] = {}
    _install_tool_mocks(attrs)
    monkey_save(monkeypatch, paper_intake_module, "get_paper_brief_tool", attrs["get_paper_brief_tool"])
    monkey_save(monkeypatch, paper_intake_module, "get_paper_head_tool", attrs["get_paper_head_tool"])
    monkey_save(monkeypatch, paper_intake_module, "search_papers_tool", attrs["search_papers_tool"])

    # 设置脚本工具行为
    ToolScripts.brief = brief_fn
    ToolScripts.head = head_fn
    ToolScripts.search = search_fn

    # 安装 LLM mock
    fake = FakeLLM(llm_responses)
    monkey_set_llm(monkeypatch, lambda config: fake)

    state = _make_state(user_input)
    update = paper_intake(state)
    return update, fake


def case_tool_call_path(monkeypatch, report: Report) -> None:
    name = "[CP3] Mock LLM + Mock 工具后，agent 可通过工具调用获取 PaperMeta"
    try:
        brief_data = {
            "arxiv_id": "2409.05591",
            "title": "Attention Is All You Need",
            "tldr": "Transformer",
            "keywords": ["transformer"],
            "citations": 12345,
            "github_url": "https://github.com/foo/bar",
            "publish_at": "2017-06-12",
            "src_url": "https://arxiv.org/pdf/2409.05591",
        }
        head_data = {
            "title": "Attention Is All You Need",
            "authors": ["Vaswani et al."],
            "abstract": "We propose ...",
            "categories": ["cs.CL", "cs.LG"],
            "publish_at": "2017-06-12",
        }
        responses = [
            AIMessage(
                content="",
                tool_calls=[_tool_call("get_paper_brief", {"arxiv_id": "2409.05591"}, "c1")],
            ),
            AIMessage(
                content="",
                tool_calls=[_tool_call("get_paper_head", {"arxiv_id": "2409.05591"}, "c2")],
            ),
            AIMessage(
                content=(
                    "<result>"
                    + json.dumps({
                        "arxiv_id": "2409.05591",
                        "title": "Attention Is All You Need",
                        "authors": ["Vaswani et al."],
                        "abstract": "We propose ...",
                        "categories": ["cs.CL", "cs.LG"],
                        "tldr": "Transformer",
                        "keywords": ["transformer"],
                        "citation_count": 12345,
                        "github_url": "https://github.com/foo/bar",
                        "publish_date": "2017-06-12",
                        "pdf_url": "https://arxiv.org/pdf/2409.05591",
                        "notes": None,
                    })
                    + "</result>"
                ),
            ),
        ]
        update, fake = _run_with_scripted_llm(
            monkeypatch, "2409.05591", responses,
            brief_fn=lambda aid: brief_data,
            head_fn=lambda aid: head_data,
        )
        assert update.get("paper_meta"), f"未填充 paper_meta: {update}"
        pm = update["paper_meta"]
        assert pm["arxiv_id"] == "2409.05591"
        assert pm["title"] == "Attention Is All You Need"
        assert pm["authors"] == ["Vaswani et al."]
        assert pm["abstract"].startswith("We propose")
        assert pm["categories"] == ["cs.CL", "cs.LG"]
        assert pm["citation_count"] == 12345
        assert pm["github_url"] == "https://github.com/foo/bar"
        assert pm["pdf_url"] == "https://arxiv.org/pdf/2409.05591"
        assert update.get("current_step") == NODE_NAME
        # 至少调用过 LLM 3 次（含两次工具调用 + 一次输出 result）
        assert len(fake.calls) >= 3, f"LLM 调用次数过少: {len(fake.calls)}"
        report.add(name, True)
    except Exception as exc:  # noqa: BLE001
        report.add(name, False, repr(exc))


def case_full_path(monkeypatch, report: Report) -> None:
    name = "[CP4] 正常路径：brief + head 完整时，PaperMeta 字段全部填充"
    try:
        # 已在 CP3 覆盖。这里用更紧凑的路径再校验一次：所有 PaperMeta 字段非默认值。
        responses = [
            AIMessage(
                content=(
                    "<result>"
                    + json.dumps({
                        "arxiv_id": "2305.12345",
                        "title": "Some Paper",
                        "authors": ["Alice", "Bob"],
                        "abstract": "abstract text",
                        "categories": ["cs.AI"],
                        "tldr": "tldr text",
                        "keywords": ["kw1", "kw2"],
                        "citation_count": 42,
                        "github_url": "https://github.com/x/y",
                        "publish_date": "2023-05-01",
                        "pdf_url": "https://arxiv.org/pdf/2305.12345",
                        "notes": None,
                    })
                    + "</result>"
                ),
            ),
        ]
        update, _ = _run_with_scripted_llm(monkeypatch, "2305.12345", responses)
        pm = update.get("paper_meta") or {}
        required = [
            "arxiv_id", "title", "authors", "abstract", "categories",
            "tldr", "keywords", "citation_count", "github_url",
            "publish_date", "pdf_url",
        ]
        missing = [k for k in required if not pm.get(k)]
        assert not missing, f"字段未填充: {missing}, pm={pm}"
        report.add(name, True)
    except Exception as exc:  # noqa: BLE001
        report.add(name, False, repr(exc))


def case_head_fail(monkeypatch, report: Report) -> None:
    name = "[CP5] head 获取失败：agent 仅使用 brief 数据，不中断"
    try:
        brief_data = {
            "arxiv_id": "2409.99999",
            "title": "Brief Only Title",
            "tldr": "x",
            "citations": 7,
            "publish_at": "2024-09-01",
            "src_url": "https://arxiv.org/pdf/2409.99999",
        }
        # head 工具抛错（被 deepxiv_tools 工厂层捕获后返回 "Error in get_paper_head: ..."）
        def head_raise(aid):
            raise RuntimeError("head endpoint 5xx after retries")

        responses = [
            AIMessage(
                content="",
                tool_calls=[_tool_call("get_paper_brief", {"arxiv_id": "2409.99999"}, "c1")],
            ),
            AIMessage(
                content="",
                tool_calls=[_tool_call("get_paper_head", {"arxiv_id": "2409.99999"}, "c2")],
            ),
            # head 失败后，agent 仅用 brief 给出结果
            AIMessage(
                content=(
                    "<result>"
                    + json.dumps({
                        "arxiv_id": "2409.99999",
                        "title": "Brief Only Title",
                        "authors": [],
                        "abstract": "",
                        "categories": [],
                        "tldr": "x",
                        "keywords": None,
                        "citation_count": 7,
                        "github_url": None,
                        "publish_date": "2024-09-01",
                        "pdf_url": "https://arxiv.org/pdf/2409.99999",
                        "notes": "head 获取失败，仅依赖 brief 数据",
                    })
                    + "</result>"
                ),
            ),
        ]
        update, _ = _run_with_scripted_llm(
            monkeypatch, "2409.99999", responses,
            brief_fn=lambda aid: brief_data,
            head_fn=head_raise,
        )
        pm = update.get("paper_meta") or {}
        assert update.get("error") is None, f"不应有 error: {update.get('error')}"
        assert pm.get("arxiv_id") == "2409.99999"
        assert pm.get("title") == "Brief Only Title"
        # authors/abstract/categories 允许为空（head 失败）
        assert pm.get("citation_count") == 7
        report.add(name, True)
    except Exception as exc:  # noqa: BLE001
        report.add(name, False, repr(exc))


def case_non_cs_warning(monkeypatch, report: Report, caplog_msgs: List[str]) -> None:
    name = "[CP6] 学科校验：非 CS 论文在结果中标注 + 节点层 WARNING 日志"
    try:
        responses = [
            AIMessage(
                content=(
                    "<result>"
                    + json.dumps({
                        "arxiv_id": "2401.00001",
                        "title": "A Physics Paper",
                        "authors": ["X"],
                        "abstract": "physics abstract",
                        "categories": ["physics.gen-ph"],
                        "tldr": None,
                        "keywords": None,
                        "citation_count": None,
                        "github_url": None,
                        "publish_date": None,
                        "pdf_url": None,
                        "notes": "WARNING: 非 CS 领域论文，复现效果可能不佳",
                    })
                    + "</result>"
                ),
            ),
        ]
        # 安装 log capture
        import logging
        logger = logging.getLogger("core.nodes.paper_intake")
        handler = _ListHandler(caplog_msgs)
        logger.addHandler(handler)
        try:
            update, _ = _run_with_scripted_llm(monkeypatch, "2401.00001", responses)
        finally:
            logger.removeHandler(handler)
        pm = update.get("paper_meta") or {}
        assert pm.get("categories") == ["physics.gen-ph"]
        # 节点层应该打印 WARNING（_map_intake_result 中实现的非 CS 检查）
        assert any("非 CS 领域" in m for m in caplog_msgs), (
            f"未发现非 CS WARNING 日志: {caplog_msgs}"
        )
        report.add(name, True)
    except Exception as exc:  # noqa: BLE001
        report.add(name, False, repr(exc))


def case_paper_not_found(monkeypatch, report: Report) -> None:
    name = "[CP7] 错误路径：论文不存在时返回 error 字段和 node_errors"
    try:
        def brief_404(aid):
            # 工厂层包装的工具会把异常字符串化为 "Error in get_paper_brief: ..."
            raise RuntimeError("NotFoundError: paper not found")

        def search_empty(q, size):
            return []

        responses = [
            AIMessage(
                content="",
                tool_calls=[_tool_call("get_paper_brief", {"arxiv_id": "0000.00000"}, "c1")],
            ),
            AIMessage(
                content="",
                tool_calls=[_tool_call("search_papers", {"query": "0000.00000", "size": 5}, "c2")],
            ),
            AIMessage(
                content=(
                    "<result>"
                    + json.dumps({
                        "error": "论文不存在：尝试 brief 与 search 均失败",
                        "arxiv_id": "0000.00000",
                    })
                    + "</result>"
                ),
            ),
        ]
        update, _ = _run_with_scripted_llm(
            monkeypatch, "0000.00000", responses,
            brief_fn=brief_404,
            search_fn=search_empty,
        )
        assert update.get("error"), f"应返回 error，实际 update={update}"
        assert update.get("paper_meta") is None
        assert update.get("current_step") == NODE_NAME
        ne = update.get("node_errors") or []
        assert any(e.get("node_name") == NODE_NAME for e in ne), (
            f"node_errors 未包含 paper_intake 错误: {ne}"
        )
        report.add(name, True)
    except Exception as exc:  # noqa: BLE001
        report.add(name, False, repr(exc))


def case_url_cleanup(monkeypatch, report: Report) -> None:
    name = "[CP8] ID 格式清洗：完整 URL 输入可被正确处理"
    try:
        url_input = "https://arxiv.org/abs/2409.05591v2"
        observed_brief_args: List[str] = []

        def brief_capture(aid):
            observed_brief_args.append(aid)
            return {
                "arxiv_id": "2409.05591",
                "title": "Cleaned ID Paper",
                "tldr": "ok",
            }

        responses = [
            AIMessage(
                content="",
                tool_calls=[_tool_call("get_paper_brief", {"arxiv_id": "2409.05591"}, "c1")],
            ),
            AIMessage(
                content=(
                    "<result>"
                    + json.dumps({
                        "arxiv_id": "2409.05591",
                        "title": "Cleaned ID Paper",
                        "authors": ["A"],
                        "abstract": "a",
                        "categories": ["cs.AI"],
                        "tldr": "ok",
                        "keywords": None,
                        "citation_count": None,
                        "github_url": None,
                        "publish_date": None,
                        "pdf_url": None,
                        "notes": None,
                    })
                    + "</result>"
                ),
            ),
        ]
        update, _ = _run_with_scripted_llm(
            monkeypatch, url_input, responses,
            brief_fn=brief_capture,
        )
        pm = update.get("paper_meta") or {}
        assert pm.get("arxiv_id") == "2409.05591", f"未清洗 ID: {pm.get('arxiv_id')}"
        assert observed_brief_args == ["2409.05591"], (
            f"agent 未把清洗后的 ID 传给 brief: {observed_brief_args}"
        )
        report.add(name, True)
    except Exception as exc:  # noqa: BLE001
        report.add(name, False, repr(exc))


# ---------- 自实现极简 monkeypatch（兼容直接 python 运行） ----------


class SimpleMonkey:
    def __init__(self) -> None:
        self._undo: List[Callable[[], None]] = []

    def setattr(self, target, name, value):
        old = getattr(target, name)
        setattr(target, name, value)
        self._undo.append(lambda t=target, n=name, v=old: setattr(t, n, v))

    def undo(self) -> None:
        while self._undo:
            self._undo.pop()()


def monkey_save(monkey, target, name, value):
    monkey.setattr(target, name, value)


def monkey_set_llm(monkey, factory):
    """patch react_base.create_llm。"""
    from core import react_base
    monkey.setattr(react_base, "create_llm", factory)


import logging as _logging


class _ListHandler(_logging.Handler):
    def __init__(self, sink: List[str]):
        super().__init__(level=_logging.DEBUG)
        self.sink = sink

    def emit(self, record):
        try:
            self.sink.append(record.getMessage())
        except Exception:
            pass


# ---------- 主入口 ----------


def main() -> int:
    report = Report()

    # CP1: 不需要 mock
    case_callable(report)

    # CP2: 用 dict 形式的轻量 monkey（仅覆盖 react_base.create_react_subgraph / create_llm）
    m = SimpleMonkey()
    try:
        case_context_mapping(m, report)
    finally:
        m.undo()

    # CP3-8: 各自独立的 monkey 周期
    for case in (
        case_tool_call_path,
        case_full_path,
        case_head_fail,
        case_paper_not_found,
        case_url_cleanup,
    ):
        m = SimpleMonkey()
        ToolScripts.brief = None
        ToolScripts.head = None
        ToolScripts.search = None
        try:
            case(m, report)
        finally:
            m.undo()

    # CP6 需要 caplog
    m = SimpleMonkey()
    ToolScripts.brief = None
    ToolScripts.head = None
    ToolScripts.search = None
    caplog_msgs: List[str] = []
    try:
        case_non_cs_warning(m, report, caplog_msgs)
    finally:
        m.undo()

    print(report.summary())
    return 0 if report.all_passed() else 1


if __name__ == "__main__":
    sys.exit(main())
