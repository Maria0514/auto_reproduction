"""C2 - paper_analysis ReAct agent 节点自测。

覆盖 dev-plan.md C2 任务的 10 个自测检查点 + BUG-S1-03 回归用例：
1.  paper_analysis 是 _make_react_wrapper 生成的 callable
2.  ReAct wrapper 正确映射 paper_meta 到 context (HumanMessage)
3.  前置校验：paper_meta 为 None 时返回 error
4.  Mock LLM + Mock 工具后，agent 自主制定阅读策略并输出 PaperAnalysis
5.  agent 可通过多轮工具调用（structure → read_section × N）完成分析
6.  非标准章节名：agent 根据章节结构自主匹配
7.  降级处理：所有章节读取失败时 agent 调用 get_full_paper 兜底
8.  预算耗尽：达到 max_rounds 后 force_finish 触发，产出部分结果
9.  结果不完整时正确填充默认值并标记 degraded_nodes
10. Prompt Cache 前缀稳定：两篇不同 arxiv_id 的 system prompt 主体字节级一致
11. [BUG-S1-03] LLM 漏写 sections_read 时，从 ReAct 工具历史回填，节点不进入 degraded_nodes
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

import importlib

# 必须用 importlib 拿真实的子模块对象——core/nodes/__init__.py 会把同名 callable
# 注册为 core.nodes 包的属性，遮蔽掉子模块引用；普通 import 语法此后只能拿到
# callable 而非模块。
paper_analysis_module = importlib.import_module("core.nodes.paper_analysis")

from core.nodes.paper_analysis import (  # noqa: E402
    NODE_NAME,
    PAPER_ANALYSIS_SCHEMA,
    _ANALYSIS_SYSTEM_PROMPT_BODY,
    _backfill_analysis_from_tools,
    _build_analysis_system_prompt,
    _map_analysis_result,
    paper_analysis,
)


# ---------- fake GlobalState / fake LLM ----------


def _make_state(paper_meta: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "user_input": "2409.05591",
        "input_type": "arxiv_id",
        "paper_meta": paper_meta,
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


_PAPER_META_DEFAULT = {
    "arxiv_id": "2409.05591",
    "title": "Attention Is All You Need",
    "authors": ["Vaswani et al."],
    "abstract": "We propose the Transformer.",
    "categories": ["cs.CL", "cs.LG"],
    "tldr": "Transformer",
    "keywords": ["transformer"],
    "citation_count": 12345,
    "github_url": "https://github.com/foo/bar",
    "publish_date": "2017-06-12",
    "pdf_url": "https://arxiv.org/pdf/2409.05591",
}


class FakeLLM:
    """脚本化 LLM：按 invoke 调用顺序返回预设 AIMessage。

    forced_dict: 当 react_base 用 `with_structured_output` 强制 JSON 输出时，
    返回该 dict（用于 force_finish_node / finalize_node 的 schema 路径）；为
    None 时让 `with_structured_output` 显式失败（走 fallback path）。
    """

    def __init__(
        self,
        responses: List[AIMessage],
        forced_dict: Optional[Dict[str, Any]] = None,
    ):
        self._responses = list(responses)
        self.calls: List[List[BaseMessage]] = []
        self.bind_tools_calls = 0
        self._forced_dict = forced_dict

    def bind_tools(self, tools):
        self.bind_tools_calls += 1
        return self

    def invoke(self, messages):
        self.calls.append(list(messages))
        if not self._responses:
            # 预算耗尽场景下的兜底输出
            return AIMessage(content="<result>{}</result>")
        return self._responses.pop(0)

    def with_structured_output(self, schema, method=None):
        """模拟 langchain_openai 的 structured output 接口。

        - forced_dict 为 None：抛异常让 react_base 走 fallback。
        - 否则返回一个 Runnable-like 对象，invoke 直接产出 forced_dict。
        """
        if self._forced_dict is None:
            raise RuntimeError("FakeLLM has no forced_dict scripted")
        parent = self

        class _Runnable:
            def invoke(self, messages):
                parent.calls.append(list(messages))
                return dict(parent._forced_dict)

        return _Runnable()


def _tool_call(name: str, args: Dict[str, Any], call_id: str) -> Dict[str, Any]:
    return {"name": name, "args": args, "id": call_id}


# ---------- 工具 mocks：可被脚本替换返回值 ----------


class ToolScripts:
    structure: Optional[Callable[[str], Any]] = None
    section: Optional[Callable[[str, str], Any]] = None
    full_paper: Optional[Callable[[str], Any]] = None
    search: Optional[Callable[[str, int], Any]] = None


def _install_tool_mocks(monkey_attrs: Dict[str, Any]) -> None:
    @tool
    def get_paper_structure(arxiv_id: str) -> str:
        """Mock structure."""
        fn = ToolScripts.structure
        if fn is None:
            return "Error: structure not scripted"
        try:
            res = fn(arxiv_id)
        except Exception as exc:
            return f"Error in get_paper_structure: {exc}"
        return res if isinstance(res, str) else json.dumps(res, ensure_ascii=False)

    @tool
    def read_section(arxiv_id: str, section_name: str) -> str:
        """Mock read_section."""
        fn = ToolScripts.section
        if fn is None:
            return "Error: section not scripted"
        try:
            res = fn(arxiv_id, section_name)
        except Exception as exc:
            return f"Error in read_section: {exc}"
        return res if isinstance(res, str) else json.dumps(res, ensure_ascii=False)

    @tool
    def get_full_paper(arxiv_id: str) -> str:
        """Mock get_full_paper."""
        fn = ToolScripts.full_paper
        if fn is None:
            return "Error: full_paper not scripted"
        try:
            res = fn(arxiv_id)
        except Exception as exc:
            return f"Error in get_full_paper: {exc}"
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

    monkey_attrs["get_paper_structure_tool"] = lambda token=None: get_paper_structure
    monkey_attrs["read_section_tool"] = lambda token=None: read_section
    monkey_attrs["get_full_paper_tool"] = lambda token=None: get_full_paper
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


# ---------- 通用 runner ----------


def _run_with_scripted_llm(
    monkeypatch,
    paper_meta: Optional[Dict[str, Any]],
    llm_responses: List[AIMessage],
    *,
    structure_fn=None,
    section_fn=None,
    full_paper_fn=None,
    search_fn=None,
) -> tuple:
    """安装脚本化工具 + LLM，运行真实 paper_analysis wrapper。"""

    attrs: Dict[str, Any] = {}
    _install_tool_mocks(attrs)
    monkey_save(monkeypatch, paper_analysis_module, "get_paper_structure_tool",
                attrs["get_paper_structure_tool"])
    monkey_save(monkeypatch, paper_analysis_module, "read_section_tool",
                attrs["read_section_tool"])
    monkey_save(monkeypatch, paper_analysis_module, "get_full_paper_tool",
                attrs["get_full_paper_tool"])
    monkey_save(monkeypatch, paper_analysis_module, "search_papers_tool",
                attrs["search_papers_tool"])

    ToolScripts.structure = structure_fn
    ToolScripts.section = section_fn
    ToolScripts.full_paper = full_paper_fn
    ToolScripts.search = search_fn

    fake = FakeLLM(llm_responses)
    monkey_set_llm(monkeypatch, lambda config: fake)

    state = _make_state(paper_meta)
    update = paper_analysis(state)
    return update, fake


# ---------- 用例 1：paper_analysis 是 callable wrapper ----------


def case_callable(report: Report) -> None:
    name = "[CP1] paper_analysis 是 _make_react_wrapper 生成的 callable"
    try:
        assert callable(paper_analysis), "paper_analysis 不可调用"
        assert paper_analysis.__name__ == f"react_wrapper_{NODE_NAME}", (
            f"wrapper __name__ 不符: {paper_analysis.__name__}"
        )
        # PAPER_ANALYSIS_SCHEMA 与 PaperAnalysis TypedDict 对齐校验
        from core.state import PaperAnalysis
        ann_keys = set(PaperAnalysis.__annotations__.keys())
        schema_keys = set(PAPER_ANALYSIS_SCHEMA.get("properties", {}).keys())
        assert ann_keys == schema_keys, (
            f"Schema 字段与 PaperAnalysis 不一致: "
            f"only_ann={ann_keys - schema_keys}, only_schema={schema_keys - ann_keys}"
        )
        report.add(name, True)
    except Exception as exc:  # noqa: BLE001
        report.add(name, False, repr(exc))


# ---------- 用例 2：paper_meta 正确映射到 context ----------


def case_context_mapping(monkeypatch, report: Report) -> None:
    name = "[CP2] ReAct wrapper 正确映射 paper_meta 到 context (HumanMessage)"
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
                    "method_summary": "stub",
                    "datasets": ["d1"],
                    "metrics": ["m1"],
                    "sections_read": ["Method"],
                    "analysis_notes": "ok",
                },
                "context": initial["context"],
            }

    from core import react_base
    monkeypatch.setattr(
        react_base, "create_react_subgraph", lambda **kwargs: FakeSubgraph()
    )
    monkey_set_llm(monkeypatch, lambda config: FakeLLM([]))

    try:
        state = _make_state(_PAPER_META_DEFAULT)
        update = paper_analysis(state)
        assert captured_initials, "subgraph 未被调用"
        msgs = captured_initials[0]["messages"]
        assert isinstance(msgs[0], SystemMessage), "首条不是 SystemMessage"
        # HumanMessage 中必须包含 arxiv_id
        assert any(
            isinstance(m, HumanMessage) and "2409.05591" in m.content for m in msgs
        ), "HumanMessage 未携带 arxiv_id"
        assert update.get("current_step") == NODE_NAME
        report.add(name, True)
    except Exception as exc:  # noqa: BLE001
        report.add(name, False, repr(exc))


# ---------- 用例 3：前置校验 paper_meta=None ----------


def case_missing_paper_meta(report: Report) -> None:
    name = "[CP3] 前置校验：paper_meta 为 None 时返回 error + NodeError"
    try:
        state = _make_state(None)
        update = paper_analysis(state)
        assert update.get("error"), f"应返回 error，实际 update={update}"
        assert update.get("paper_analysis") is None
        assert update.get("current_step") == NODE_NAME
        ne = update.get("node_errors") or []
        assert any(e.get("node_name") == NODE_NAME for e in ne), (
            f"node_errors 未包含 paper_analysis 错误: {ne}"
        )
        report.add(name, True)
    except Exception as exc:  # noqa: BLE001
        report.add(name, False, repr(exc))


# ---------- 用例 4：normal path（agent 输出完整 PaperAnalysis） ----------


def case_normal_path(monkeypatch, report: Report) -> None:
    name = "[CP4] Mock LLM + Mock 工具后，agent 输出完整 PaperAnalysis"
    try:
        structure_data = {
            "arxiv_id": "2409.05591",
            "title": "Attention Is All You Need",
            "sections": ["Introduction", "Method", "Experiments", "Results"],
            "token_count": 12000,
        }
        method_text = "We propose a Transformer architecture using multi-head self-attention."
        experiments_text = "We train on WMT 2014 dataset and report BLEU score on newstest2014."
        results_text = "Our model achieves BLEU 28.4 on EN-DE."

        def section_dispatch(aid, sec):
            mapping = {
                "Method": method_text,
                "Experiments": experiments_text,
                "Results": results_text,
            }
            return mapping.get(sec, f"unknown section {sec}")

        responses = [
            AIMessage(
                content="",
                tool_calls=[_tool_call("get_paper_structure",
                                       {"arxiv_id": "2409.05591"}, "c1")],
            ),
            AIMessage(
                content="",
                tool_calls=[_tool_call("read_section",
                                       {"arxiv_id": "2409.05591",
                                        "section_name": "Method"}, "c2")],
            ),
            AIMessage(
                content="",
                tool_calls=[_tool_call("read_section",
                                       {"arxiv_id": "2409.05591",
                                        "section_name": "Experiments"}, "c3")],
            ),
            AIMessage(
                content="",
                tool_calls=[_tool_call("read_section",
                                       {"arxiv_id": "2409.05591",
                                        "section_name": "Results"}, "c4")],
            ),
            AIMessage(
                content=(
                    "<result>"
                    + json.dumps({
                        "method_summary": (
                            "Transformer based on multi-head self-attention."
                        ),
                        "key_formulas": ["Attention(Q,K,V)=softmax(QK^T/sqrt(d_k))V"],
                        "datasets": ["WMT 2014 EN-DE"],
                        "metrics": ["BLEU"],
                        "hyperparams": {"d_model": 512, "heads": 8},
                        "hardware_requirements": "8x P100 GPUs",
                        "framework": "PyTorch",
                        "baseline_results": {"BLEU_EN_DE": 28.4},
                        "sections_read": ["Method", "Experiments", "Results"],
                        "analysis_notes": "normal path",
                    })
                    + "</result>"
                ),
            ),
        ]
        update, fake = _run_with_scripted_llm(
            monkeypatch, _PAPER_META_DEFAULT, responses,
            structure_fn=lambda aid: structure_data,
            section_fn=section_dispatch,
        )
        assert update.get("error") is None, f"不应有 error: {update.get('error')}"
        pa = update.get("paper_analysis")
        assert pa, f"未填充 paper_analysis: {update}"
        assert pa["method_summary"].startswith("Transformer")
        assert pa["datasets"] == ["WMT 2014 EN-DE"]
        assert pa["metrics"] == ["BLEU"]
        assert pa["hyperparams"]["d_model"] == 512
        assert pa["framework"] == "PyTorch"
        assert pa["baseline_results"]["BLEU_EN_DE"] == 28.4
        assert pa["sections_read"] == ["Method", "Experiments", "Results"]
        # 未被标记 degraded
        assert NODE_NAME not in (update.get("degraded_nodes") or [])
        # 工具被调用了 4 次
        assert len(fake.calls) >= 5, f"LLM 调用次数过少: {len(fake.calls)}"
        report.add(name, True)
    except Exception as exc:  # noqa: BLE001
        report.add(name, False, repr(exc))


# ---------- 用例 5：多轮工具调用路径 ----------


def case_multi_tool_calls(monkeypatch, report: Report) -> None:
    name = "[CP5] agent 可通过多轮工具调用 (structure -> read_section x N) 完成分析"
    try:
        structure_data = {
            "arxiv_id": "2409.05591",
            "title": "Stub",
            "sections": ["Introduction", "Method", "Experiments"],
        }
        observed_section_names: List[str] = []

        def section_dispatch(aid, sec):
            observed_section_names.append(sec)
            return f"content of {sec}"

        responses = [
            AIMessage(
                content="",
                tool_calls=[_tool_call("get_paper_structure",
                                       {"arxiv_id": "2409.05591"}, "c1")],
            ),
            AIMessage(
                content="",
                tool_calls=[_tool_call("read_section",
                                       {"arxiv_id": "2409.05591",
                                        "section_name": "Method"}, "c2")],
            ),
            AIMessage(
                content="",
                tool_calls=[_tool_call("read_section",
                                       {"arxiv_id": "2409.05591",
                                        "section_name": "Experiments"}, "c3")],
            ),
            AIMessage(
                content="",
                tool_calls=[_tool_call("read_section",
                                       {"arxiv_id": "2409.05591",
                                        "section_name": "Introduction"}, "c4")],
            ),
            AIMessage(
                content=(
                    "<result>"
                    + json.dumps({
                        "method_summary": "summary",
                        "key_formulas": [],
                        "datasets": ["d"],
                        "metrics": ["m"],
                        "hyperparams": {},
                        "hardware_requirements": "",
                        "framework": None,
                        "baseline_results": {},
                        "sections_read": ["Method", "Experiments", "Introduction"],
                        "analysis_notes": "multi-call path",
                    })
                    + "</result>"
                ),
            ),
        ]
        update, _ = _run_with_scripted_llm(
            monkeypatch, _PAPER_META_DEFAULT, responses,
            structure_fn=lambda aid: structure_data,
            section_fn=section_dispatch,
        )
        pa = update.get("paper_analysis") or {}
        assert observed_section_names == ["Method", "Experiments", "Introduction"], (
            f"section 调用顺序不符: {observed_section_names}"
        )
        assert pa.get("sections_read") == [
            "Method", "Experiments", "Introduction"
        ]
        report.add(name, True)
    except Exception as exc:  # noqa: BLE001
        report.add(name, False, repr(exc))


# ---------- 用例 6：非标准章节名 ----------


def case_nonstandard_section_names(monkeypatch, report: Report) -> None:
    name = "[CP6] 非标准章节名：agent 根据章节结构自主匹配"
    try:
        # 这篇论文用 "Our Framework" / "Ablation Study" 代替 Method / Results
        structure_data = {
            "arxiv_id": "2409.05591",
            "title": "Stub",
            "sections": ["Introduction", "Our Framework", "Ablation Study"],
        }
        observed: List[str] = []

        def section_dispatch(aid, sec):
            observed.append(sec)
            return f"content of {sec}"

        responses = [
            AIMessage(
                content="",
                tool_calls=[_tool_call("get_paper_structure",
                                       {"arxiv_id": "2409.05591"}, "c1")],
            ),
            # agent 识别 "Our Framework" 即 Method
            AIMessage(
                content="",
                tool_calls=[_tool_call("read_section",
                                       {"arxiv_id": "2409.05591",
                                        "section_name": "Our Framework"}, "c2")],
            ),
            # agent 识别 "Ablation Study" 即 Results
            AIMessage(
                content="",
                tool_calls=[_tool_call("read_section",
                                       {"arxiv_id": "2409.05591",
                                        "section_name": "Ablation Study"}, "c3")],
            ),
            AIMessage(
                content=(
                    "<result>"
                    + json.dumps({
                        "method_summary": "from Our Framework",
                        "key_formulas": [],
                        "datasets": ["d"],
                        "metrics": ["m"],
                        "hyperparams": {},
                        "hardware_requirements": "",
                        "framework": None,
                        "baseline_results": {},
                        "sections_read": ["Our Framework", "Ablation Study"],
                        "analysis_notes": (
                            "matched Method -> Our Framework, "
                            "Results -> Ablation Study via section structure"
                        ),
                    })
                    + "</result>"
                ),
            ),
        ]
        update, _ = _run_with_scripted_llm(
            monkeypatch, _PAPER_META_DEFAULT, responses,
            structure_fn=lambda aid: structure_data,
            section_fn=section_dispatch,
        )
        assert observed == ["Our Framework", "Ablation Study"], (
            f"非标准章节匹配失败: {observed}"
        )
        pa = update.get("paper_analysis") or {}
        assert pa.get("sections_read") == ["Our Framework", "Ablation Study"]
        assert "matched" in (pa.get("analysis_notes") or "")
        report.add(name, True)
    except Exception as exc:  # noqa: BLE001
        report.add(name, False, repr(exc))


# ---------- 用例 7：降级到 get_full_paper ----------


def case_full_paper_fallback(monkeypatch, report: Report) -> None:
    name = "[CP7] 所有章节读取失败时 agent 调用 get_full_paper 兜底"
    try:
        structure_data = {
            "arxiv_id": "2409.05591",
            "title": "Stub",
            "sections": ["Method", "Experiments"],
        }

        def section_raise(aid, sec):
            raise RuntimeError("section endpoint 5xx")

        full_text_observed = {"called": False}

        def full_paper_ok(aid):
            full_text_observed["called"] = True
            return "Full paper markdown text..."

        responses = [
            AIMessage(
                content="",
                tool_calls=[_tool_call("get_paper_structure",
                                       {"arxiv_id": "2409.05591"}, "c1")],
            ),
            AIMessage(
                content="",
                tool_calls=[_tool_call("read_section",
                                       {"arxiv_id": "2409.05591",
                                        "section_name": "Method"}, "c2")],
            ),
            AIMessage(
                content="",
                tool_calls=[_tool_call("read_section",
                                       {"arxiv_id": "2409.05591",
                                        "section_name": "Experiments"}, "c3")],
            ),
            AIMessage(
                content="",
                tool_calls=[_tool_call("get_full_paper",
                                       {"arxiv_id": "2409.05591"}, "c4")],
            ),
            AIMessage(
                content=(
                    "<result>"
                    + json.dumps({
                        "method_summary": "extracted from full text",
                        "key_formulas": [],
                        "datasets": ["ds"],
                        "metrics": ["m"],
                        "hyperparams": {},
                        "hardware_requirements": "",
                        "framework": None,
                        "baseline_results": {},
                        "sections_read": [],
                        "analysis_notes": "section api failed; fell back to full paper",
                    })
                    + "</result>"
                ),
            ),
        ]
        update, _ = _run_with_scripted_llm(
            monkeypatch, _PAPER_META_DEFAULT, responses,
            structure_fn=lambda aid: structure_data,
            section_fn=section_raise,
            full_paper_fn=full_paper_ok,
        )
        assert full_text_observed["called"], "未触发 get_full_paper 兜底"
        pa = update.get("paper_analysis") or {}
        assert "full" in (pa.get("analysis_notes") or "").lower()
        # 因 sections_read 为空，被标记 degraded
        assert NODE_NAME in (update.get("degraded_nodes") or []), (
            f"应被标记 degraded: {update.get('degraded_nodes')}"
        )
        report.add(name, True)
    except Exception as exc:  # noqa: BLE001
        report.add(name, False, repr(exc))


# ---------- 用例 8：预算耗尽 force_finish ----------


def case_budget_exhausted(monkeypatch, report: Report) -> None:
    name = "[CP8] 预算耗尽：达到 max_rounds 后 force_finish 触发"
    try:
        # max_rounds=12，budget_check 在 round >= 11 时触发 force_finish。
        # 让 reasoning_node 11 次都返回 tool_call，第 12 轮 force_finish 通过
        # schema 强制路径产出降级结果（FakeLLM.with_structured_output 提供
        # forced_dict）。这种方式同时覆盖 force_finish_node 的 schema 优先路径。
        structure_data = {
            "arxiv_id": "2409.05591",
            "title": "Stub",
            "sections": ["Method"],
        }

        responses: List[AIMessage] = []
        for i in range(11):
            responses.append(AIMessage(
                content="",
                tool_calls=[_tool_call("read_section",
                                       {"arxiv_id": "2409.05591",
                                        "section_name": "Method"}, f"c{i}")],
            ))

        forced_dict = {
            "method_summary": "partial summary from budget exhaust",
            "key_formulas": [],
            "datasets": [],
            "metrics": [],
            "hyperparams": {},
            "hardware_requirements": "",
            "framework": None,
            "baseline_results": {},
            "sections_read": ["Method"],
            "analysis_notes": "budget exhausted",
        }

        # 手动安装工具 + LLM（无法用通用 _run_with_scripted_llm，因要 forced_dict）
        attrs: Dict[str, Any] = {}
        _install_tool_mocks(attrs)
        for tname, factory in attrs.items():
            monkey_save(monkeypatch, paper_analysis_module, tname, factory)

        ToolScripts.structure = lambda aid: structure_data
        ToolScripts.section = lambda aid, sec: f"content of {sec}"
        ToolScripts.full_paper = None
        ToolScripts.search = None

        fake = FakeLLM(responses, forced_dict=forced_dict)
        monkey_set_llm(monkeypatch, lambda config: fake)

        state = _make_state(_PAPER_META_DEFAULT)
        update = paper_analysis(state)

        pa = update.get("paper_analysis") or {}
        assert pa, f"force_finish 后应仍产出结果: {update}"
        assert pa.get("method_summary") == "partial summary from budget exhaust"
        assert pa.get("analysis_notes", "").startswith(("[DEGRADED]", "budget exhausted")) or \
               "budget exhausted" in pa.get("analysis_notes", ""), (
            f"analysis_notes 未保留预算耗尽信息: {pa.get('analysis_notes')}"
        )
        # 预算耗尽路径下，因 datasets+metrics 全空且 method 有，会被标记 degraded
        # （sections_read=['Method'] 非空，所以核心字段只缺 datasets+metrics 一项）
        # —— 这里不强制 degraded 标记，因为 method_summary 非空可能恰好通过校验
        # 至少要有 retry_budget_remaining 字段
        assert update.get("retry_budget_remaining") is not None
        # LLM 至少调用了 11 轮 reasoning + 1 次 force_finish schema 路径
        assert len(fake.calls) >= 11, f"LLM 调用次数过少: {len(fake.calls)}"
        report.add(name, True)
    except Exception as exc:  # noqa: BLE001
        report.add(name, False, repr(exc))


# ---------- 用例 9：结果不完整 → degraded ----------


def case_incomplete_result_degraded(monkeypatch, report: Report) -> None:
    name = "[CP9] 结果不完整时正确填充默认值并标记 degraded_nodes"
    try:
        # agent 只产出极少字段，method_summary 为空，sections_read 为空
        responses = [
            AIMessage(
                content=(
                    "<result>"
                    + json.dumps({
                        "method_summary": "",
                        "datasets": [],
                        "metrics": [],
                        "hyperparams": {},
                        "sections_read": [],
                        "analysis_notes": "structure fetch failed",
                    })
                    + "</result>"
                ),
            ),
        ]
        update, _ = _run_with_scripted_llm(
            monkeypatch, _PAPER_META_DEFAULT, responses,
        )
        pa = update.get("paper_analysis") or {}
        # 字段类型补齐：缺失字段填默认值
        assert pa.get("key_formulas") == [], f"key_formulas 未填默认: {pa.get('key_formulas')}"
        assert pa.get("hardware_requirements") == ""
        assert pa.get("framework") is None
        assert pa.get("baseline_results") == {}
        # degraded 标记
        assert NODE_NAME in (update.get("degraded_nodes") or []), (
            f"应被标记 degraded: {update.get('degraded_nodes')}"
        )
        # analysis_notes 含降级标记
        assert "[DEGRADED]" in (pa.get("analysis_notes") or "")
        # node_errors 中包含 degraded 类型记录
        ne = update.get("node_errors") or []
        assert any(
            e.get("node_name") == NODE_NAME and e.get("error_type") == "degraded"
            for e in ne
        ), f"node_errors 未含 degraded 记录: {ne}"
        report.add(name, True)
    except Exception as exc:  # noqa: BLE001
        report.add(name, False, repr(exc))


# ---------- 用例 10：Prompt Cache 前缀稳定 ----------


def case_prompt_cache_prefix_stable(report: Report) -> None:
    name = "[CP10] Prompt Cache 前缀稳定：不同 arxiv_id 的 system prompt 主体字节级一致"
    try:
        ctx_a = {
            "arxiv_id": "2409.05591",
            "paper_meta": {
                "arxiv_id": "2409.05591",
                "title": "Attention Is All You Need",
                "authors": ["Vaswani et al."],
                "abstract": "We propose the Transformer.",
                "categories": ["cs.CL"],
            },
        }
        ctx_b = {
            "arxiv_id": "2305.99999",
            "paper_meta": {
                "arxiv_id": "2305.99999",
                "title": "Completely Different Paper",
                "authors": ["Doe"],
                "abstract": "Another abstract.",
                "categories": ["cs.AI", "cs.LG"],
            },
        }
        prompt_a = _build_analysis_system_prompt(ctx_a)
        prompt_b = _build_analysis_system_prompt(ctx_b)

        # 1) 整个 system prompt 必须不同（因尾部上下文不同）
        assert prompt_a != prompt_b, "两次 prompt 完全相同？尾部上下文未生效"

        # 2) 截去尾部独立段落后，主体必须完全相同
        separator = "\n--- 当前论文上下文 ---\n"
        assert separator in prompt_a and separator in prompt_b, (
            "未发现尾部分隔符，前缀治理失败"
        )
        body_a = prompt_a.split(separator, 1)[0]
        body_b = prompt_b.split(separator, 1)[0]
        assert body_a == body_b, "主体字节级不一致，破坏 Prompt Cache 前缀稳定"

        # 3) 主体应与导出的常量 _ANALYSIS_SYSTEM_PROMPT_BODY 字节级一致
        #    （split 后 body 含 BODY 末尾的换行符；只比较 rstrip 后的内容）
        assert body_a.rstrip("\n") == _ANALYSIS_SYSTEM_PROMPT_BODY.rstrip("\n"), (
            "主体与 _ANALYSIS_SYSTEM_PROMPT_BODY 不一致；任何修改都应同步常量"
        )

        # 4) 主体里不得出现任何论文级动态变量（防止意外硬编码）
        for needle in ["2409.05591", "2305.99999", "Vaswani", "Doe",
                       "Attention Is All You Need", "Completely Different"]:
            assert needle not in body_a, f"主体含动态变量 {needle!r}，破坏前缀稳定"

        report.add(name, True)
    except Exception as exc:  # noqa: BLE001
        report.add(name, False, repr(exc))


# ---------- 用例 11：BUG-S1-03 回归——LLM 漏写 sections_read 时从工具历史回填 ----------


def case_backfill_sections_read_from_tools(report: Report) -> None:
    """BUG-S1-03 同形态于 BUG-S1-02：LLM 偶发在最终 ``<result>`` JSON 中漏写
    ``sections_read``（输出空数组），即便 ReAct 子图已经成功调用过多次
    ``read_section``。本用例在不动 LLM 服从度的前提下，验证节点层
    ``_map_analysis_result`` 能用 3 参签名通过 ``_backfill_analysis_from_tools``
    回填 sections_read，且节点不被错误标记进 ``degraded_nodes``。
    """
    name = "[CP11][BUG-S1-03] LLM 漏写 sections_read 时从工具历史回填，不进入 degraded"
    try:
        from langchain_core.messages import AIMessage, ToolMessage

        # LLM 输出（模拟）：完整的 method/datasets/metrics/hyperparams 等，但
        # sections_read=[] —— 这正是真实 e2e 中 25% 复现率踩到的形态。
        result = {
            "method_summary": (
                "HippoRAG is a neurobiologically inspired retrieval framework "
                "that combines a knowledge graph with personalized PageRank to "
                "improve multi-hop retrieval over passages."
            ),
            "key_formulas": ["score(q, p) = PageRank(KG, seed=q)"],
            "datasets": ["MuSiQue", "2WikiMultiHopQA", "HotpotQA"],
            "metrics": ["Recall@2", "Recall@5", "EM", "F1"],
            "hyperparams": {
                "retrievers": ["Contriever", "ColBERTv2"],
                "damping": 0.5,
                "threshold": 0.5,
            },
            "hardware_requirements": "4 NVIDIA RTX A6000",
            "framework": "PyTorch",
            "baseline_results": {"BM25_R2": 0.43},
            # 关键：LLM 漏写——按字段填充优先级原本应列出实际读过的章节名
            "sections_read": [],
            "analysis_notes": (
                "Read strategy followed Method/Experiments/Results, "
                "with Introduction for context."
            ),
        }

        # 构造 react_messages：模拟 ReAct 子图在 ``read_section`` 上的真实历史。
        # 每条 ToolMessage 配对一条带 tool_calls 的 AIMessage，tool_call_id 对齐。
        # 还混入一条失败的 ToolMessage 验证过滤逻辑。
        react_messages = [
            AIMessage(
                content="",
                tool_calls=[{
                    "name": "read_section",
                    "args": {"arxiv_id": "2405.14831", "section_name": "Method"},
                    "id": "rs1",
                }],
            ),
            ToolMessage(
                content=(
                    "HippoRAG augments LLM retrievers with a knowledge graph "
                    "and personalized PageRank to integrate information across "
                    "passages..."
                ),
                tool_call_id="rs1",
                name="read_section",
            ),
            AIMessage(
                content="",
                tool_calls=[{
                    "name": "read_section",
                    "args": {"arxiv_id": "2405.14831", "section_name": "Experiments"},
                    "id": "rs2",
                }],
            ),
            ToolMessage(
                content="We evaluate on MuSiQue, 2WikiMultiHopQA, HotpotQA ...",
                tool_call_id="rs2",
                name="read_section",
            ),
            # 失败 ToolMessage（deepxiv 工具工厂层异常时的格式）——应被过滤
            AIMessage(
                content="",
                tool_calls=[{
                    "name": "read_section",
                    "args": {"arxiv_id": "2405.14831", "section_name": "NonExistent"},
                    "id": "rs_fail",
                }],
            ),
            ToolMessage(
                content="Error in read_section: section NonExistent not found",
                tool_call_id="rs_fail",
                name="read_section",
            ),
            AIMessage(
                content="",
                tool_calls=[{
                    "name": "read_section",
                    "args": {"arxiv_id": "2405.14831", "section_name": "Results"},
                    "id": "rs3",
                }],
            ),
            ToolMessage(
                content="Our model achieves SOTA on multi-hop QA ...",
                tool_call_id="rs3",
                name="read_section",
            ),
            # 最终 <result> 标签（仅作历史记录）
            AIMessage(content="<result>{}</result>"),
        ]

        # state 仿真：retry_budget 充足，无既有 degraded
        state: Dict[str, Any] = {
            "user_input": "2405.14831",
            "input_type": "arxiv_id",
            "paper_meta": _PAPER_META_DEFAULT,
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

        # 直接调 3 参 _map_analysis_result，绕过 ReAct 子图，单测聚焦兜底逻辑
        update = _map_analysis_result(result, state, react_messages)

        pa = update.get("paper_analysis") or {}
        # 1) sections_read 应被工具历史回填（仅成功的 3 个：Method/Experiments/Results）
        assert pa.get("sections_read") == ["Method", "Experiments", "Results"], (
            f"sections_read 未从工具历史回填，实际值：{pa.get('sections_read')}"
        )
        # 2) 失败的 ToolMessage 不应被纳入回填
        assert "NonExistent" not in (pa.get("sections_read") or []), (
            "失败的 ToolMessage 不应被回填到 sections_read"
        )
        # 3) 既然 sections_read 已回填、method_summary/datasets/metrics 都齐，
        #    _missing_core_fields 应判定为空 → 不应进入 degraded
        assert NODE_NAME not in (update.get("degraded_nodes") or []), (
            f"sections_read 已回填但仍被错误标记 degraded: "
            f"degraded_nodes={update.get('degraded_nodes')}"
        )
        # 4) node_errors 不应包含 degraded 类型记录
        ne = update.get("node_errors") or []
        assert not any(
            e.get("node_name") == NODE_NAME and e.get("error_type") == "degraded"
            for e in ne
        ), f"误产生 degraded NodeError: {ne}"
        # 5) analysis_notes 不应包含 [DEGRADED] 机器标记
        assert "[DEGRADED]" not in (pa.get("analysis_notes") or ""), (
            "回填后不应再追加 [DEGRADED] 机器标记"
        )

        # ---- 边界验证：read_section 全失败时不应回填，应保持 degraded ----
        result_only_fails = dict(result)
        result_only_fails["sections_read"] = []
        only_fail_msgs = [
            AIMessage(
                content="",
                tool_calls=[{
                    "name": "read_section",
                    "args": {"arxiv_id": "2405.14831", "section_name": "Method"},
                    "id": "f1",
                }],
            ),
            ToolMessage(
                content="Error in read_section: 5xx",
                tool_call_id="f1",
                name="read_section",
            ),
        ]
        update2 = _map_analysis_result(result_only_fails, state, only_fail_msgs)
        pa2 = update2.get("paper_analysis") or {}
        assert pa2.get("sections_read") == [], (
            f"全失败 ToolMessage 不应被回填: {pa2.get('sections_read')}"
        )
        assert NODE_NAME in (update2.get("degraded_nodes") or []), (
            "工具历史全失败时仍应标记 degraded"
        )

        # ---- 边界验证：react_messages=None 时回填应安全跳过 ----
        update3 = _map_analysis_result(result_only_fails, state, None)
        assert NODE_NAME in (update3.get("degraded_nodes") or []), (
            "react_messages=None 时仍应保持原 degraded 判定"
        )

        report.add(name, True)
    except Exception as exc:  # noqa: BLE001
        report.add(name, False, repr(exc))


# ---------- 自实现极简 monkeypatch（兼容直接 python 运行 + pytest 调用） ----------


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


# ---------- pytest 单测入口 ----------


def test_paper_analysis_all_checkpoints() -> None:
    """pytest 入口：以一个用例聚合 10 个检查点，失败时打印完整摘要。"""
    rc = main()
    assert rc == 0, "至少一个 paper_analysis 自测检查点未通过；详见上方 stdout"


# ---------- 主入口 ----------


def main() -> int:
    report = Report()

    # CP1: 不需要 mock
    case_callable(report)

    # CP2: 需要 monkeypatch react_base
    m = SimpleMonkey()
    try:
        case_context_mapping(m, report)
    finally:
        m.undo()

    # CP3: 不需要 mock（前置校验直接走错误路径）
    case_missing_paper_meta(report)

    # CP4-9: 各自独立 monkey 周期
    for case in (
        case_normal_path,
        case_multi_tool_calls,
        case_nonstandard_section_names,
        case_full_paper_fallback,
        case_budget_exhausted,
        case_incomplete_result_degraded,
    ):
        m = SimpleMonkey()
        ToolScripts.structure = None
        ToolScripts.section = None
        ToolScripts.full_paper = None
        ToolScripts.search = None
        try:
            case(m, report)
        finally:
            m.undo()

    # CP10: 纯函数测试，不需要 mock
    case_prompt_cache_prefix_stable(report)

    # CP11: BUG-S1-03 回归——直接调 _map_analysis_result，无需 mock LLM / 工具
    case_backfill_sections_read_from_tools(report)

    print(report.summary())
    return 0 if report.all_passed() else 1


if __name__ == "__main__":
    sys.exit(main())
