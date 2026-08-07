"""真跑证据归档机制的**离线**验收（不需凭证、不触网、零配额）。

被测对象 `tests/realrun_evidence.py` + 它在 `tests/test_sprint7_s705_realrun.py` 里的接线。

## 这套用例证明什么 / 不证明什么

**证明（离线可复现）**：
- 归档函数在给定 state 上**确实写出**预期形状的 bundle JSON（T-EV-2x）；
- 凭证**不会**进归档物：白名单导出 + 三重脱敏 + 写盘前 fail-closed 复扫（T-EV-1x）；
- **默认 pytest 下不写盘**：真 pytest 子进程两态对照（deselect ⇒ 零产物 / `-m e2e` ⇒ 有产物，T-EV-40）；
- 真跑用例的归档发生在 `finally` 里（断言失败也留证，T-EV-31 用 AST 钉死结构）。

**证不了（只能等下一次真跑）**：
- 真实链路上 `graph.get_state()` 返回的 state 是否真含非空 `fix_loop_history`
  （那取决于真实 LLM 是否走到修复循环，本文件全部用构造 state）；
- `workspace/runs/` 在长时间真跑下的磁盘占用；
- 真实日志里是否存在本模块脱敏规则没覆盖的凭证形态（规则是**已知形态**的白名单）。
⇒ 归档机制"离线验过"**不等于**"真实链路已生效"。
"""
from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests import realrun_evidence as ev

PROJECT_ROOT = Path(__file__).resolve().parent.parent


# --------------------------------------------------------------------------- #
# 假 request（模拟 pytest 的 request fixture 的 marker 查询面）
# --------------------------------------------------------------------------- #
class _FakeNode:
    def __init__(self, markers):
        self._markers = set(markers)

    def get_closest_marker(self, name):
        return object() if name in self._markers else None


class _FakeRequest:
    def __init__(self, markers=()):
        self.node = _FakeNode(markers)


def _e2e_request():
    return _FakeRequest(markers=("e2e",))


SECRET = "sk-UNITTESTsecret0123456789abcdef"


@pytest.fixture()
def fake_secret_env(monkeypatch):
    """注入一个假凭证到环境变量，让 env_secret_values() 把它当作要抹掉的密钥。"""
    monkeypatch.setenv("UNITTEST_FAKE_API_KEY", SECRET)
    return SECRET


# --------------------------------------------------------------------------- #
# T-EV-1x：凭证卫生
# --------------------------------------------------------------------------- #
def test_ev_10_env_secret_values_picks_up_key_named_vars(fake_secret_env):
    values = ev.env_secret_values()
    assert SECRET in values
    # 只回值、不回变量名（防归档物反推出"哪个变量是密钥"）
    assert all(isinstance(v, str) for v in values)


def test_ev_11_env_secret_values_ignores_short_and_stopword_values(monkeypatch):
    monkeypatch.setenv("SOME_TOKEN", "false")
    monkeypatch.setenv("OTHER_SECRET", "abc")
    values = ev.env_secret_values()
    assert "false" not in values
    assert "abc" not in values


def test_ev_12_bundle_only_exports_curated_keys():
    """`llm_config_set`（含 api_key）这类通道**根本不进** bundle —— 白名单第一道防线。"""
    state = {
        "fix_loop_history": [{"round_number": 1, "fix_note": "改了 sys.path"}],
        "llm_config_set": {"default": {"api_key": SECRET, "model": "gpt-x"}},
        "messages": [{"role": "user", "content": "..."}],
    }
    bundle = ev.build_bundle(name="n", sprint="sprint7", state=state, secrets=[SECRET])
    assert "llm_config_set" not in bundle["state"]
    assert "messages" not in bundle["state"]
    assert "fix_loop_history" in bundle["state"]
    assert "llm_config_set" not in json.dumps(bundle, ensure_ascii=False)
    assert "llm_config_set" not in ev.CURATED_STATE_KEYS


def test_ev_13_sensitive_key_names_are_redacted_recursively():
    state = {
        "execution_result": {
            "env": {"api_key": SECRET, "AUTHORIZATION": "Basic zzzz", "credentials": {"a": "b"}},
            "exit_ok": True,
        }
    }
    bundle = ev.build_bundle(name="n", sprint="sprint7", state=state, secrets=[])
    env_block = bundle["state"]["execution_result"]["env"]
    assert env_block["api_key"].startswith("<redacted:")
    assert env_block["AUTHORIZATION"].startswith("<redacted:")
    assert env_block["credentials"].startswith("<redacted:")  # 整个子树被砍
    assert bundle["state"]["execution_result"]["exit_ok"] is True
    assert bundle["redaction"]["key_hits"] == 3


def test_ev_14_benign_token_counter_keys_survive():
    """`max_tokens` / `token_usage` 不是凭证，不许被误伤成马赛克。"""
    state = {"execution_result": {"max_tokens": 4096, "token_usage": {"total_tokens": 12}}}
    bundle = ev.build_bundle(name="n", sprint="sprint7", state=state, secrets=[])
    assert bundle["state"]["execution_result"]["max_tokens"] == 4096
    assert bundle["state"]["execution_result"]["token_usage"] == {"total_tokens": 12}
    assert bundle["redaction"]["key_hits"] == 0


def test_ev_15_env_secret_value_removed_from_free_text():
    """凭证被日志原样打出来（键名无从判断）时，靠**值匹配**抹掉。"""
    state = {
        "execution_result": {
            "stdout": f"curl -H 'X-Auth: {SECRET}' https://api.example.com\nok\n",
        }
    }
    bundle = ev.build_bundle(name="n", sprint="sprint7", state=state, secrets=[SECRET])
    text = json.dumps(bundle, ensure_ascii=False)
    assert SECRET not in text
    assert "<redacted>" in bundle["state"]["execution_result"]["stdout"]
    assert bundle["redaction"]["value_hits"] >= 1


def test_ev_16_shape_patterns_redacted_even_when_value_unknown():
    """密钥不在本机环境变量里（例如别人机器上跑出来的日志）也要按**形态**拦。"""
    state = {
        "analysis_notes": (
            "export OPENAI_API_KEY=sk-abcdefghijklmnopqrstuv\n"
            "Authorization: Bearer ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345\n"
            "token: 9f8e7d6c5b4a39281706\n"
        )
    }
    bundle = ev.build_bundle(name="n", sprint="sprint7", state=state, secrets=[])
    notes = bundle["state"]["analysis_notes"]
    assert "sk-abcdefghijklmnopqrstuv" not in notes
    assert "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345" not in notes
    assert "9f8e7d6c5b4a39281706" not in notes
    assert bundle["redaction"]["pattern_hits"] >= 3


def test_ev_17_truncation_runs_after_redaction_so_no_secret_fragment_survives():
    """截断在脱敏**之后**：否则密钥可能骑在截断边界上、留下前缀残渣。

    刻意把密钥摆在**头部保留窗口的边界上**（只有前 10 个字符落在保留区内）——
    这是唯一能把"先截断后脱敏"和"先脱敏后截断"区分开的位置：前者会留下 10 字符残渣。
    """
    half = ev.MAX_STR_CHARS // 2
    head_filler = "x" * (half - 10)          # 密钥前 10 字符正好落在保留窗口内
    state = {"analysis_notes": head_filler + SECRET + "y" * 30000}
    bundle = ev.build_bundle(name="n", sprint="sprint7", state=state, secrets=[SECRET])
    notes = bundle["state"]["analysis_notes"]
    assert "截断" in notes
    assert len(notes) < ev.MAX_STR_CHARS + 200
    assert SECRET not in notes
    assert SECRET[:10] not in notes, "密钥残渣骑在截断边界上活下来了（说明截断发生在脱敏之前）"


def test_ev_18_assert_no_known_secret_is_fail_closed_and_does_not_echo_secret():
    with pytest.raises(ev.RealRunEvidenceError) as excinfo:
        ev.assert_no_known_secret(f"...{SECRET}...", [SECRET])
    assert SECRET not in str(excinfo.value)
    assert str(len(SECRET)) in str(excinfo.value)
    # 没有密钥时静默通过
    ev.assert_no_known_secret("clean text", [SECRET])


def test_ev_19_write_bundle_refuses_and_writes_nothing_when_secret_survives(tmp_path):
    """兜底闸门实证：手工造一个"脱敏漏了"的 bundle ⇒ 拒写，且**目录都不建**。"""
    sink = tmp_path / "sink"
    leaking = {"name": "leak", "state": {"analysis_notes": SECRET}}
    with pytest.raises(ev.RealRunEvidenceError):
        ev.write_bundle(leaking, sink, secrets=[SECRET])
    assert not sink.exists()


# --------------------------------------------------------------------------- #
# T-EV-2x：产物形状
# --------------------------------------------------------------------------- #
def test_ev_20_write_bundle_produces_parseable_dated_json(tmp_path):
    bundle = ev.build_bundle(
        name="probe", sprint="sprint7", state={"fix_loop_count": 3}, secrets=[]
    )
    path = ev.write_bundle(bundle, tmp_path, secrets=[])
    assert path.parent == tmp_path
    assert path.name.endswith("_probe.json")
    assert path.name[:10].count("-") == 2  # YYYY-MM-DD 前缀
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded["schema"] == "realrun-evidence/1"
    assert loaded["state"]["fix_loop_count"] == 3
    assert loaded["sprint"] == "sprint7"
    assert loaded["repo_head"] != ""


def test_ev_21_second_write_same_second_does_not_overwrite(tmp_path):
    from datetime import datetime

    fixed = datetime(2026, 8, 7, 3, 0, 0)
    b = ev.build_bundle(name="probe", sprint="sprint7", state={}, secrets=[], now=fixed)
    first = ev.write_bundle(b, tmp_path, secrets=[], now=fixed)
    second = ev.write_bundle(b, tmp_path, secrets=[], now=fixed)
    assert first != second
    assert first.exists() and second.exists()


def test_ev_22_bundle_records_which_curated_keys_were_absent():
    bundle = ev.build_bundle(
        name="n", sprint="sprint7", state={"fix_loop_count": 0}, secrets=[]
    )
    assert bundle["state_keys_present"] == ["fix_loop_count"]
    assert "fix_loop_history" in bundle["state_keys_absent"]
    assert bundle["redaction"]["note"]


def test_ev_23_non_json_objects_are_coerced_not_crashed():
    class Weird:
        def __repr__(self):
            return "<weird obj>"

    bundle = ev.build_bundle(
        name="n", sprint="sprint7", state={"execution_result": {"o": Weird(), "p": Path("/a/b")}},
        secrets=[],
    )
    json.dumps(bundle)  # 不抛即证明可序列化
    assert bundle["state"]["execution_result"]["p"] == "/a/b"
    assert "Weird" in bundle["state"]["execution_result"]["o"]


def test_ev_24_evidence_dir_defaults_under_sprint_test_reports(monkeypatch):
    monkeypatch.delenv(ev.EVIDENCE_DIR_ENV, raising=False)
    d = ev.evidence_dir("sprint7")
    assert d == PROJECT_ROOT / "docs" / "sprint7" / "test-reports" / "realrun-bundles"
    monkeypatch.setenv(ev.EVIDENCE_DIR_ENV, "/tmp/elsewhere")
    assert ev.evidence_dir("sprint8") == Path("/tmp/elsewhere")
    with pytest.raises(ev.RealRunEvidenceError):
        ev.evidence_dir("../../etc")


# --------------------------------------------------------------------------- #
# T-EV-3x：e2e 闸门（默认 pytest 零副作用）
# --------------------------------------------------------------------------- #
def test_ev_30_dump_without_e2e_marker_raises_and_touches_no_disk(tmp_path):
    sink = tmp_path / "sink"
    with pytest.raises(ev.RealRunEvidenceError, match="e2e"):
        ev.dump_realrun_bundle(
            _FakeRequest(), sprint="sprint7", name="x", state={}, sink_dir=sink
        )
    assert not sink.exists()


def test_ev_30b_dump_without_request_object_raises(tmp_path):
    sink = tmp_path / "sink"
    with pytest.raises(ev.RealRunEvidenceError):
        ev.dump_realrun_bundle(None, sprint="sprint7", name="x", state={}, sink_dir=sink)
    assert not sink.exists()


def test_ev_31_durable_run_dir_without_e2e_marker_raises_and_creates_nothing(
    tmp_path, monkeypatch
):
    """⚠ 必须先把 `PROJECT_ROOT` 打到 tmp：本用例**验红时会真的去建目录**（那正是它守的失效
    模式）。若落点是仓库里的 `workspace/runs/`，红态就会污染真实路径——那就是 P-S8-20 同款
    的坑。2026-08-07 本用例第一版正是这么写的，红态实测漏出 5 个空目录，随即改成这样。
    """
    monkeypatch.setattr(ev, "PROJECT_ROOT", tmp_path)
    with pytest.raises(ev.RealRunEvidenceError, match="e2e"):
        ev.durable_run_dir(_FakeRequest(), "should_not_appear")
    assert not (tmp_path / "workspace").exists()
    # 真实仓库路径同样一个字节都没碰（落点算式没被 monkeypatch 绕过去的反证）
    assert not (PROJECT_ROOT / "workspace" / "runs" / "should_not_appear").exists()


def test_ev_31b_durable_run_dir_lands_under_workspace_runs(tmp_path, monkeypatch):
    """落点契约：``<repo>/workspace/runs/<name>_<ts>/``——`workspace/` 已 gitignore
    ⇒ 原始产物留得住、又不进版本控制。"""
    monkeypatch.setattr(ev, "PROJECT_ROOT", tmp_path)
    run_dir = ev.durable_run_dir(_e2e_request(), "s705_realrun")
    assert run_dir.parent == tmp_path / "workspace" / "runs"
    assert run_dir.name.startswith("s705_realrun_")
    assert run_dir.is_dir()
    with pytest.raises(ev.RealRunEvidenceError):
        ev.durable_run_dir(_e2e_request(), "../escape")


def test_ev_32_dump_with_e2e_marker_writes_expected_artifact(tmp_path):
    sink = tmp_path / "sink"
    state = {
        "fix_loop_history": [
            {"round_number": 1, "error_category": "import", "fix_note": "", "files_touched": []},
            {"round_number": 2, "error_category": "import", "fix_note": "加了 sys.path", "files_touched": ["a.py"]},
        ],
        "fix_loop_count": 2,
    }
    path = ev.dump_realrun_bundle(
        _e2e_request(), sprint="sprint7", name="probe", state=state, sink_dir=sink,
        extra={"task": "T-EV-32"},
    )
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded["state"]["fix_loop_history"][1]["fix_note"] == "加了 sys.path"
    assert loaded["extra"]["task"] == "T-EV-32"
    assert loaded["name"] == "probe"


# --------------------------------------------------------------------------- #
# T-EV-4x：真 pytest 子进程两态对照（默认 deselect ⇒ 零产物；-m e2e ⇒ 有产物）
# --------------------------------------------------------------------------- #
_SUBPROC_TEST = '''
import sys
from pathlib import Path
import pytest

sys.path.insert(0, {root!r})
from tests.realrun_evidence import dump_realrun_bundle

@pytest.mark.e2e
def test_probe(request):
    p = dump_realrun_bundle(
        request, sprint="sprint7", name="subproc",
        state={{"fix_loop_history": [{{"round_number": 1, "fix_note": "n"}}]}},
        sink_dir=Path({sink!r}),
    )
    assert p.exists()
'''

_SUBPROC_INI = """[pytest]
addopts = -m "not e2e"
markers =
    e2e: 真实端到端测试
"""


def _run_pytest(workdir: Path, args):
    env = dict(os.environ)
    env["PYTHONPATH"] = str(PROJECT_ROOT)
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:randomly", "-p", "no:cacheprovider", *args],
        cwd=str(workdir),
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )


def test_ev_40_default_run_deselects_e2e_and_writes_nothing(tmp_path):
    """**这条是"默认 pytest 零副作用"的正面证据**：真 pytest、真 addopts、真 marker。"""
    work = tmp_path / "proj"
    work.mkdir()
    sink = tmp_path / "sink"
    (work / "pytest.ini").write_text(_SUBPROC_INI, encoding="utf-8")
    (work / "test_probe_gen.py").write_text(
        _SUBPROC_TEST.format(root=str(PROJECT_ROOT), sink=str(sink)), encoding="utf-8"
    )

    default_run = _run_pytest(work, [])
    assert default_run.returncode in (0, 5), default_run.stdout + default_run.stderr
    assert "1 deselected" in default_run.stdout or "no tests ran" in default_run.stdout
    assert not sink.exists(), "默认 pytest 下归档目录都不该被建出来"

    e2e_run = _run_pytest(work, ["-m", "e2e"])
    assert e2e_run.returncode == 0, e2e_run.stdout + e2e_run.stderr
    assert "1 passed" in e2e_run.stdout
    written = sorted(sink.glob("*_subproc.json"))
    assert len(written) == 1, f"期望恰好 1 份归档物，实际 {written}"
    assert json.loads(written[0].read_text(encoding="utf-8"))["state"]["fix_loop_history"]


# --------------------------------------------------------------------------- #
# T-EV-5x：接线到 S7-05 真跑用例
# --------------------------------------------------------------------------- #
def test_ev_50_realrun_archive_helper_writes_bundle(tmp_path):
    """真跑用例里那段归档代码**本体**的行为验证（不是 grep 源码字符串）。"""
    from tests import test_sprint7_s705_realrun as rr

    state = {
        "fix_loop_history": [
            {"round_number": 1, "error_category": "import", "fix_note": "", "files_touched": []},
            {"round_number": 2, "error_category": "import", "fix_note": "note2", "files_touched": ["a.py"]},
            {"round_number": 3, "error_category": "import", "fix_note": " ", "files_touched": []},
        ],
        "fix_loop_count": 3,
        "llm_config_set": {"default": {"api_key": SECRET}},
    }
    run_dir = tmp_path / "run"
    path = rr._archive_evidence(
        _e2e_request(),
        state_values=state,
        run_dir=run_dir,
        meta={"thread_id": "t-1", "elapsed_seconds": 1.5, "raised": None},
        sink_dir=tmp_path / "sink",
    )
    loaded = json.loads(path.read_text(encoding="utf-8"))
    # 主证据：逐轮 fix_loop_history 全文在归档物里
    assert len(loaded["state"]["fix_loop_history"]) == 3
    assert loaded["state"]["fix_loop_history"][1]["fix_note"] == "note2"
    # 度量随附且与 §2.1 口径一致（空白串不算遵守）
    assert loaded["extra"]["adherence"]["rounds"] == 3
    assert loaded["extra"]["adherence"]["with_fix_note"] == 1
    assert loaded["extra"]["adherence"]["rate"] == pytest.approx(1 / 3)
    # 指回原始产物，报告可据此复核
    assert loaded["extra"]["run_dir"] == str(run_dir)
    assert loaded["extra"]["checkpoint_db"].endswith(rr._DB_NAME)
    assert loaded["extra"]["thread_id"] == "t-1"
    # 凭证不进归档物
    assert SECRET not in path.read_text(encoding="utf-8")


def test_ev_51_measure_adherence_matches_forensics_report_definition():
    from tests import test_sprint7_s705_realrun as rr

    stats = rr.measure_adherence(
        [{"fix_note": "x"}, {"fix_note": "   "}, {"fix_note": None}, {}]
    )
    assert stats == {
        "rounds": 4,
        "with_fix_note": 1,
        "rate": 0.25,
        "per_round": [
            {"round_number": None, "error_category": None, "has_fix_note": True, "files_touched": None},
            {"round_number": None, "error_category": None, "has_fix_note": False, "files_touched": None},
            {"round_number": None, "error_category": None, "has_fix_note": False, "files_touched": None},
            {"round_number": None, "error_category": None, "has_fix_note": False, "files_touched": None},
        ],
    }
    assert rr.measure_adherence([])["rate"] is None


def _realrun_ast():
    from tests import test_sprint7_s705_realrun as rr

    return ast.parse(Path(rr.__file__).read_text(encoding="utf-8"))


def test_ev_52_archive_is_called_inside_finally_so_failures_still_leave_evidence():
    """**结构性断言**：归档必须在 `finally` 里——断言失败那次的证据往往最值钱。

    用 AST 而不是字符串 grep：位置（在不在 finally 里）才是要守的性质。
    """
    tree = _realrun_ast()
    fn = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "test_s705_fix_note_adherence"
    )
    calls_in_finally = [
        node.func.id
        for tr in ast.walk(fn) if isinstance(tr, ast.Try)
        for stmt in tr.finalbody
        for node in ast.walk(stmt)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    ]
    assert "_archive_evidence" in calls_in_finally, (
        "归档不在 finally 里 ⇒ 用例失败时又会留不下证据（本次修复的核心性质）"
    )


def test_ev_53_realrun_no_longer_parks_evidence_in_tmp_path():
    """结构性根因回归门：真跑用例不得再把 checkpoint 库 / workspace 落 `tmp_path`。"""
    tree = _realrun_ast()
    fn = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "test_s705_fix_note_adherence"
    )
    arg_names = [a.arg for a in fn.args.args]
    assert "tmp_path" not in arg_names, "tmp_path 会被 pytest 回收，是证据灭失的结构性根因"
    names = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name)}
    assert "tmp_path" not in names
    assert "durable_run_dir" in names


# --------------------------------------------------------------------------- #
# T-EV-6x：事后补档（覆盖手工真跑，如 S7-10 的 UMAP 那次）
# --------------------------------------------------------------------------- #
def _make_checkpoint_db(db_path: Path, thread_id: str, channel_values: dict) -> None:
    import sqlite3

    from langgraph.checkpoint.base import empty_checkpoint
    from langgraph.checkpoint.sqlite import SqliteSaver

    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    try:
        saver = SqliteSaver(conn)
        saver.setup()
        ckpt = empty_checkpoint()
        ckpt["channel_values"] = channel_values
        saver.put(
            {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}},
            ckpt,
            {"source": "update", "step": 1},
            {},
        )
        conn.commit()
    finally:
        conn.close()


@pytest.fixture()
def checkpoint_db(tmp_path):
    db = tmp_path / "checkpoints.db"
    _make_checkpoint_db(
        db,
        "thread-umap",
        {
            "reproduction_plan": {
                "plan_summary": "复现 UMAP 可视化基准",
                "code_strategy": "use_repo",
                "execution_steps": [
                    {"step": 1, "command": "python scripts/prepare_datasets.py"},
                    {"step": 2, "command": "python -m scripts.run_visualization_benchmarks"},
                ],
            },
            "fix_loop_count": 1,
            "llm_config_set": {"default": {"api_key": SECRET}},
        },
    )
    return db


def test_ev_60_dump_from_checkpoint_db_archives_plan_and_redacts(checkpoint_db, tmp_path):
    """S7-10 那类**手工真跑**（证据只在 checkpoints.db 里）的事后补档路径。"""
    sink = tmp_path / "sink"
    path = ev.dump_from_checkpoint_db(
        checkpoint_db, "thread-umap", sprint="sprint7", name="s710-umap", sink_dir=sink
    )
    raw = path.read_text(encoding="utf-8")
    loaded = json.loads(raw)
    plan = loaded["state"]["reproduction_plan"]
    assert plan["code_strategy"] == "use_repo"
    assert len(plan["execution_steps"]) == 2  # 计划原文逐条落盘（dev-plan:2739-2740 要的就是这个）
    assert plan["execution_steps"][1]["command"] == "python -m scripts.run_visualization_benchmarks"
    assert loaded["extra"]["thread_id"] == "thread-umap"
    assert "llm_config_set" not in loaded["state"]
    assert SECRET not in raw


def test_ev_61_dump_from_checkpoint_db_rejects_unknown_thread(checkpoint_db, tmp_path):
    with pytest.raises(ev.RealRunEvidenceError):
        ev.dump_from_checkpoint_db(
            checkpoint_db, "no-such-thread", sprint="sprint7", name="x", sink_dir=tmp_path / "s"
        )
    assert not (tmp_path / "s").exists()


def test_ev_62_cli_lists_threads_and_dumps(checkpoint_db, tmp_path, capsys):
    assert ev.main(["--db", str(checkpoint_db), "--list"]) == 0
    assert "thread-umap" in capsys.readouterr().out

    sink = tmp_path / "cli-sink"
    rc = ev.main(
        [
            "--db", str(checkpoint_db),
            "--thread", "thread-umap",
            "--sprint", "sprint7",
            "--name", "cli-probe",
            "--out", str(sink),
        ]
    )
    assert rc == 0
    assert len(list(sink.glob("*_cli-probe.json"))) == 1

    assert ev.main(["--db", str(tmp_path / "missing.db")]) == 2


def test_ev_63_checkpoint_db_opened_read_only(checkpoint_db):
    """只读打开：补档动作不得改动原始证据库。"""
    before = checkpoint_db.stat().st_size, checkpoint_db.stat().st_mtime_ns
    ev.read_state_from_checkpoint(checkpoint_db, "thread-umap")
    after = checkpoint_db.stat().st_size, checkpoint_db.stat().st_mtime_ns
    assert before == after
