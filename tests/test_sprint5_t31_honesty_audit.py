"""T-S5-3-1 自测：core/honesty_audit.py 三规则 + 三重误报防线（S5-03，P0）。

覆盖 dev-plan §批次3 CP-3.1-1 ~ CP-3.1-5：
    - CP-3.1-1 回归样本 fixture 命中 ≥2 类（answer_leakage + hardcoded_score）；
    - CP-3.1-2 干净 fixture 零命中（AC-S5-06 误报红线，与命中同权重）；
    - CP-3.1-3 三重豁免各一断言（评估器读答案 / score 初始化后更新 / return 引用入参），
      每条豁免配一个"同形态但确属作弊"的正控制，证明豁免不是规则失效；
    - CP-3.1-4 hit 证据三元组齐全可人工复核；snippet 脱敏（脱敏出口③）；
      语法错误/坏 JSON 跳过 + WARNING（失败非静默）；
    - CP-3.1-5 纯函数确定性红线：模块无 LLM 客户端 import、无 state 读写（源码结构断言，
      与 test_sprint5_t24 结构守门同范式）+ 同输入同输出。

fixture 只读消费（tests/fixtures/** 一个字节不许改）：
    - tests/fixtures/regression_2604_01687/code/ —— 审计命中靶（路径勘误后为 code/src/）
    - tests/fixtures/clean_code_sample/ —— 误报防线靶
"""

from __future__ import annotations

import ast
import inspect
import json
import logging
import textwrap
from pathlib import Path

import pytest

from core.honesty_audit import audit_code_dir
from core.secrets_store import register_sensitive_value

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REGRESSION_CODE_DIR = (
    PROJECT_ROOT / "tests" / "fixtures" / "regression_2604_01687" / "code"
)
CLEAN_CODE_DIR = PROJECT_ROOT / "tests" / "fixtures" / "clean_code_sample"

#: 哨兵敏感值（唯一化，避免与其他测试的进程级 sensitive set 交叉污染）
_SENTINEL = "S5T31-FAKE-SECRET-cafebabe"


def _write_py(dir_path: Path, name: str, source: str) -> Path:
    dir_path.mkdir(parents=True, exist_ok=True)
    path = dir_path / name
    path.write_text(textwrap.dedent(source).lstrip("\n"), encoding="utf-8")
    return path


# ===========================================================================
# CP-3.1-1 回归样本 fixture 命中 ≥2 类（AC-S5-05 审计部分）
# ===========================================================================


def test_cp_3_1_1_regression_fixture_hits_two_categories():
    """回归靶（2604.01687 假成功现场）命中 answer_leakage + hardcoded_score 两类。

    断言贴真实代码（fixture 已固化不可变，故锁定精确三元组集合，兼防误报蔓延）：
    - skill_generator.py L23 抄 verifier 的 expected_skill_keywords 进 SKILL.md（R1），
      L32 refine 回流 missing_keywords 属同一泄漏链（R1）；
    - task_executor.py L24/L26/L27 非评估角色读答案键自评分（R1）；
      L29 `score = 0.0`（no_skill baseline 写死）、L31 `score = 0.3 if ... else 0.1`
      （其他 baseline 写死）挂在 `baseline_type == "no_skill"` 分派下（R2）；
    - R3 constant_outcome 在本靶为 0 条（execute 非评估角色名、返回非常量——规则如实
      不虚报）；manifest JSON 与 outputs/ 均零命中（outputs 目录排除）。
    """
    result = audit_code_dir(REGRESSION_CODE_DIR)

    assert result["clean"] is False
    triples = {(h["rule"], h["file"], h["line"]) for h in result["hits"]}
    assert triples == {
        ("answer_leakage", "src/skill_generator.py", 23),
        ("answer_leakage", "src/skill_generator.py", 32),
        ("answer_leakage", "src/task_executor.py", 24),
        ("answer_leakage", "src/task_executor.py", 26),
        ("answer_leakage", "src/task_executor.py", 27),
        ("hardcoded_score", "src/task_executor.py", 29),
        ("hardcoded_score", "src/task_executor.py", 31),
    }

    # ≥2 类 smell（AC-S5-05 核心口径）
    rules = {h["rule"] for h in result["hits"]}
    assert rules == {"answer_leakage", "hardcoded_score"}
    assert len(rules) >= 2

    # 证据 snippet 贴真实代码
    by_key = {(h["file"], h["line"]): h["snippet"] for h in result["hits"]}
    assert "expected_skill_keywords" in by_key[("src/skill_generator.py", 23)]
    assert by_key[("src/task_executor.py", 29)] == "score = 0.0"
    assert by_key[("src/task_executor.py", 31)] == "score = 0.3 if skill_package else 0.1"


# ===========================================================================
# CP-3.1-2 干净 fixture 零命中（AC-S5-06 误报红线，与 CP-3.1-1 同权重）
# ===========================================================================


def test_cp_3_1_2_clean_fixture_zero_hits():
    """干净靶（stdlib-only 情感分类 eval，真读真算）必须零命中、结论 clean。

    关键防线现场：evaluate.py 的 `record["label"]` 读答案键——靠评估角色文件豁免
    挡住；metrics.py 的 `scores = {}` + 逐键算术赋值——非字面量绑定不触发 R2。
    """
    result = audit_code_dir(CLEAN_CODE_DIR)
    assert result == {"clean": True, "hits": []}


# ===========================================================================
# CP-3.1-3 三重豁免各一断言（每条配正控制，证明豁免 ≠ 规则失效）
# ===========================================================================


def test_cp_3_1_3_exemption_evaluator_reads_answers(tmp_path):
    """豁免一（R1）：评估角色读答案合法——文件级与函数级各一；正控制：非评估角色命中。"""
    exempt_dir = tmp_path / "exempt"
    # 文件级豁免：verifier.py 读 expected_answer / label
    _write_py(
        exempt_dir,
        "verifier.py",
        """
        def check(task, submission):
            expected = task["expected_answer"]
            gold = task.get("label")
            return submission == expected and gold is not None
        """,
    )
    # 函数级豁免：普通文件里的评估函数读 ground_truth_labels
    _write_py(
        exempt_dir,
        "pipeline.py",
        """
        def evaluate_predictions(task, predictions):
            gold = task.get("ground_truth_labels", [])
            return [p == g for p, g in zip(predictions, gold)]
        """,
    )
    assert audit_code_dir(exempt_dir) == {"clean": True, "hits": []}

    # 正控制：非评估角色（generator）读答案键 → R1 命中
    control_dir = tmp_path / "control"
    _write_py(
        control_dir,
        "generator.py",
        """
        def build_prompt(task):
            kws = task["expected_keywords"]
            return "hint: " + ", ".join(kws)
        """,
    )
    control = audit_code_dir(control_dir)
    assert [(h["rule"], h["file"], h["line"]) for h in control["hits"]] == [
        ("answer_leakage", "generator.py", 2)
    ]


def test_cp_3_1_3_exemption_score_init_then_update(tmp_path):
    """豁免二（R2）：score = 0.0 初始化 + 同函数后续更新（增量赋值/重绑定）不命中；
    正控制：绑定后不再变的字面量 score 命中。"""
    exempt_dir = tmp_path / "exempt"
    _write_py(
        exempt_dir,
        "runner.py",
        """
        def compute_score(preds, golds):
            score = 0.0
            for p, g in zip(preds, golds):
                if p == g:
                    score += 1.0
            score = score / len(golds)
            return score
        """,
    )
    assert audit_code_dir(exempt_dir) == {"clean": True, "hits": []}

    control_dir = tmp_path / "control"
    _write_py(
        control_dir,
        "runner.py",
        """
        def compute_score(preds):
            score = 0.95
            return score
        """,
    )
    control = audit_code_dir(control_dir)
    assert [(h["rule"], h["file"], h["line"]) for h in control["hits"]] == [
        ("hardcoded_score", "runner.py", 2)
    ]


def test_cp_3_1_3_exemption_return_references_input(tmp_path):
    """豁免三（R3）：return 引用入参（或非常量中间值）即不命中；
    正控制：评估函数常量结局（入参完全未参与）命中。"""
    exempt_dir = tmp_path / "exempt"
    _write_py(
        exempt_dir,
        "eval_metrics.py",
        """
        def evaluate(results):
            return sum(results) / len(results)
        """,
    )
    assert audit_code_dir(exempt_dir) == {"clean": True, "hits": []}

    control_dir = tmp_path / "control"
    _write_py(
        control_dir,
        "eval_metrics.py",
        """
        def evaluate_run(samples):
            return 0.95
        """,
    )
    control = audit_code_dir(control_dir)
    assert ("constant_outcome", "eval_metrics.py", 2) in {
        (h["rule"], h["file"], h["line"]) for h in control["hits"]
    }


# ===========================================================================
# CP-3.1-4 证据三元组 / 脱敏出口③ / 语法错误容忍 + WARNING
# ===========================================================================


def test_cp_3_1_4_evidence_triple_complete_and_reviewable():
    """每条 hit 的 (rule, file, line, snippet) 齐全，且 snippet 与源文件该行
    严格一致（可人工复核）；file 为相对 code_dir 的实存路径。"""
    result = audit_code_dir(REGRESSION_CODE_DIR)
    assert result["hits"], "回归靶必须有命中，否则本断言无意义"
    for hit in result["hits"]:
        assert set(hit.keys()) == {"rule", "file", "line", "snippet"}
        assert hit["rule"] in {"answer_leakage", "hardcoded_score", "constant_outcome"}
        src_path = REGRESSION_CODE_DIR / hit["file"]
        assert src_path.is_file()
        assert isinstance(hit["line"], int) and hit["line"] >= 1
        source_line = src_path.read_text(encoding="utf-8").splitlines()[hit["line"] - 1]
        assert hit["snippet"] == source_line.strip()
        assert hit["snippet"], "无证据不产 hit"


def test_cp_3_1_4_snippet_masked_with_sentinel(tmp_path):
    """脱敏出口③：hit 命中行含已登记敏感值 → snippet 与序列化结果均无明文。"""
    register_sensitive_value(_SENTINEL)
    _write_py(
        tmp_path,
        "runner.py",
        f"""
        def compute_score(preds):
            score = 0.93  # scripted, token {_SENTINEL}
            return score
        """,
    )
    result = audit_code_dir(tmp_path)
    snippets = [h["snippet"] for h in result["hits"]]
    assert snippets, "正控制：字面量 score 必须命中，脱敏断言才有对象"
    assert any("****" in s for s in snippets)
    assert _SENTINEL not in json.dumps(result, ensure_ascii=False)


def test_cp_3_1_4_syntax_error_skipped_with_warning(tmp_path, caplog):
    """语法错误 .py / 坏 JSON 清单容忍跳过 + WARNING（失败非静默），
    其余文件照常审计不受影响。"""
    (tmp_path / "broken.py").write_text("def broken(:\n    pass\n", encoding="utf-8")
    (tmp_path / "tasks_manifest.json").write_text("{not json", encoding="utf-8")
    _write_py(
        tmp_path,
        "generator.py",
        """
        def build_prompt(task):
            return task["expected_keywords"]
        """,
    )
    with caplog.at_level(logging.WARNING, logger="core.honesty_audit"):
        result = audit_code_dir(tmp_path)

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("broken.py" in r.getMessage() for r in warnings)
    assert any("tasks_manifest.json" in r.getMessage() for r in warnings)
    # 坏文件不拖垮整体：有效文件的命中照常产出
    assert [(h["rule"], h["file"]) for h in result["hits"]] == [
        ("answer_leakage", "generator.py")
    ]


def test_cp_3_1_4_missing_code_dir_tolerated_with_warning(tmp_path, caplog):
    """code_dir 不存在 → WARNING + 空结果不炸（T-S5-3-2 reporting 集成的容忍前提）。"""
    with caplog.at_level(logging.WARNING, logger="core.honesty_audit"):
        result = audit_code_dir(tmp_path / "no_such_dir")
    assert result == {"clean": True, "hits": []}
    assert any(
        "no_such_dir" in r.getMessage()
        for r in caplog.records
        if r.levelno == logging.WARNING
    )


def test_cp_3_1_4_manifest_json_in_scan_scope(tmp_path):
    """扫描范围含数据清单类 JSON：baseline → 数字字面量映射（预写结局清单）命中，
    证据三元组同样齐全。"""
    (tmp_path / "results_manifest.json").write_text(
        '{\n  "dataset": "demo",\n  "baselines": {"no_skill": 0.0, "self_generated": 0.3}\n}\n',
        encoding="utf-8",
    )
    result = audit_code_dir(tmp_path)
    assert [(h["rule"], h["file"], h["line"]) for h in result["hits"]] == [
        ("hardcoded_score", "results_manifest.json", 3)
    ]
    assert "no_skill" in result["hits"][0]["snippet"]


# ===========================================================================
# CP-3.1-5 纯函数确定性红线（与既有结构守门断言同范式）
# ===========================================================================


def test_cp_3_1_5_purity_no_llm_no_state():
    """源码结构断言：import 全集限于 stdlib + core.secrets_store；
    无 LLM 客户端、无 langchain/langgraph、无 GlobalState 读写。"""
    import core.honesty_audit as ha_module

    src = inspect.getsource(ha_module)
    tree = ast.parse(src)
    imported: set = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")

    allowed = {
        "__future__",
        "ast",
        "json",
        "logging",
        "re",
        "pathlib",
        "typing",
        "core.secrets_store",
    }
    assert imported <= allowed, f"越界 import: {imported - allowed}"

    forbidden_prefixes = ("core.llm_client", "core.state", "langchain", "langgraph", "openai")
    assert not any(m.startswith(forbidden_prefixes) for m in imported)
    assert "GlobalState" not in src
    assert "create_llm" not in src


def test_cp_3_1_5_deterministic_same_input_same_output():
    """确定性：同一输入两次审计输出逐字节一致（hits 有序且去重）。"""
    first = audit_code_dir(REGRESSION_CODE_DIR)
    second = audit_code_dir(REGRESSION_CODE_DIR)
    assert first == second
    assert json.dumps(first, ensure_ascii=False, sort_keys=True) == json.dumps(
        second, ensure_ascii=False, sort_keys=True
    )
    # 可 JSON 序列化（作为 state 单值字段 honesty_audit 落 checkpoint 的前提）
    assert json.loads(json.dumps(first)) == first
