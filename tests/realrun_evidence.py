"""真跑证据归档（**结构性防线**，非 test 文件、不被 pytest 收集）。

## 为什么有这个模块

`tests/test_sprint7_s705_realrun.py` 曾把 checkpoint 库与 workspace 都落在 ``tmp_path``：
pytest 只保留最近 3 轮临时目录 ⇒ **真跑一结束，证据就开始倒计时灭失**。2026-08-07 的
取证（`docs/sprint7/test-reports/2026-08-07_s705-b3-realrun-forensics.md` §1.1）实测确认
2026-07-22 那次真跑的 `fix_loop_history` 原始记录**已永久不可复原**。

同一教训 S7-10 也吃过一次并写进了计划（`docs/sprint7/dev-plan.md:2739-2740`：
「必须把 `reproduction_plan` 全文 + 关键 state 快照落盘成 bundle JSON」），**但那条要求
从未被执行**——因为它只是"写在计划里的一句话"，没有任何代码强制它发生。

⇒ 本模块把这条要求**从文档搬进代码**：真跑用例调用 :func:`dump_realrun_bundle`，
证据在跑完（**含失败时**）自动落到版本控制内的归档目录。

## 三条硬约束（改这个文件前先读）

1. **默认 pytest 下零副作用**：:func:`dump_realrun_bundle` / :func:`durable_run_dir`
   在调用方**不是** ``@pytest.mark.e2e`` 用例时**直接抛异常、且在抛之前不碰任何磁盘**。
   第一道防线是 `pytest.ini` 的 ``addopts = -m "not e2e"``（e2e 用例体根本不执行），
   本模块的 marker 断言是第二道——防"有人把 marker 摘了"或"从单测里误调"。
2. **凭证卫生**：只导出白名单 state 键（``llm_config_set`` 这类含 ``api_key`` 的通道
   **根本不进 bundle**）；再对导出物做三重脱敏（敏感键名 / 已知环境变量密钥值 / 常见密钥
   形态正则）；最后 **fail-closed** ——序列化后的文本里若仍能搜到任何已知密钥值，
   **拒绝写盘并抛异常**。
3. **绝不因归档失败而掩盖真跑结论**：调用方应在 ``finally`` 里调用，并自行决定
   归档异常是否上抛（本模块只保证"要么写对、要么不写"，不做静默吞异常）。

## 用法

真跑用例（e2e）内::

    def test_xxx(request, ...):
        run_dir = durable_run_dir(request, "s705_realrun")   # workspace/runs/... （已 gitignore）
        ...
        finally:
            dump_realrun_bundle(request, sprint="sprint7", name="s705-fix-note",
                                state=snap.values, extra={"run_dir": str(run_dir)})

手工真跑（走 app / 脚本、证据在 checkpoints.db 里）事后补档::

    .venv/bin/python -m tests.realrun_evidence --db checkpoints.db --list
    .venv/bin/python -m tests.realrun_evidence --db checkpoints.db --thread <id> \\
        --sprint sprint7 --name s710-umap
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent

#: 归档目录的环境变量覆盖（留给"我不想写进仓库"的场景；不设则落 docs/<sprint>/test-reports/）
EVIDENCE_DIR_ENV = "REALRUN_EVIDENCE_DIR"

#: bundle 只导出这些 state 键。**白名单而非黑名单**——新增 state 通道默认不进归档物，
#: 想归档必须显式加进来，顺便强制作者过一遍"这个通道会不会带凭证"。
#: 特别地 ``llm_config_set``（含 api_key）/ ``pending_user_input`` 永不在列。
CURATED_STATE_KEYS: Tuple[str, ...] = (
    # 修复循环记忆（S7-05 真跑要留的主证据）
    "fix_loop_history",
    "fix_loop_count",
    "last_fix_note",
    "last_files_written",
    # 计划原文（S7-10 dev-plan:2739-2740 点名要求落盘的那份）
    "reproduction_plan",
    "code_output_dir",
    # 上下游快照
    "paper_meta",
    "resource_info",
    "execution_result",
    "report_path",
    "current_step",
    "error",
    "node_errors",
    "degraded_nodes",
    "retry_budget_remaining",
    "credential_degradations",
    "simulation_notice",
    "honesty_audit",
    "local_env_facts",
    "analysis_notes",
    "user_input",
    "input_type",
    "execution_mode",
    "sandbox_type",
    "workspace_dir",
    "_dev_loop_llm_calls",
    "_planning_revise_count",
)

#: 单个字符串的字节上限（超出取头尾、掐中间）。归档物进版本控制，不能让一份日志撑爆 diff。
MAX_STR_CHARS = 20000
#: 单个列表最多保留多少项
MAX_LIST_ITEMS = 300
#: 递归深度上限（防环 / 防病态结构）
MAX_DEPTH = 12

_REDACTED = "<redacted>"

#: 环境变量名命中即视为密钥，其**值**会被全文替换掉（覆盖"密钥被日志原样打出来"的情况）
_SECRET_ENV_NAME_RE = re.compile(
    r"(?i)(api[_-]?key|apikey|token|secret|password|passwd|credential|private[_-]?key)"
)
#: 太短或明显不是密钥的值不参与全文替换（否则会把正常文本打成马赛克）
_ENV_VALUE_STOPWORDS = frozenset(
    {"false", "true", "none", "null", "changeme", "unset", "default", "disabled", "enabled"}
)
_MIN_SECRET_LEN = 8

#: 字典**键名**命中即认为对应值是凭证
_SENSITIVE_KEY_RE = re.compile(
    r"(?i)(?:^|[_\-.])"
    r"(api[_-]?key|apikey|secret|token|password|passwd|credential|credentials"
    r"|authorization|cookie|private[_-]?key|access[_-]?key)"
    r"(?:$|[_\-.])"
)
#: 键名命中上面的正则、但确定不是凭证的白名单（防把 token 计数字段误伤成马赛克）
_BENIGN_KEY_NAMES = frozenset(
    {
        "max_tokens",
        "token_usage",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "tokens",
        "token_count",
    }
)

#: 常见密钥形态。(pattern, group) —— group=0 整体替换，>0 只替换该捕获组
_VALUE_PATTERNS: Tuple[Tuple[re.Pattern, int], ...] = (
    (re.compile(r"sk-[A-Za-z0-9_\-]{12,}"), 0),
    (re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"), 0),
    (re.compile(r"hf_[A-Za-z0-9]{20,}"), 0),
    (re.compile(r"AKIA[0-9A-Z]{16}"), 0),
    (re.compile(r"eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}"), 0),
    (re.compile(r"(?i)bearer\s+([A-Za-z0-9._\-]{12,})"), 1),
    (
        # `API_KEY=xxx` / `"token": "xxx"` 这类"日志把凭证连名带值打出来"的形态
        re.compile(
            r"(?i)\b(?:api[_-]?key|apikey|token|password|passwd|secret)\b\s*[=:]\s*"
            r"[\"']?([A-Za-z0-9._\-]{12,})"
        ),
        1,
    ),
)

_SPRINT_RE = re.compile(r"^sprint\d+$")
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-]{0,63}$")


class RealRunEvidenceError(RuntimeError):
    """归档前置条件不满足（非 e2e 上下文 / 参数非法 / 脱敏未通过）。"""


# --------------------------------------------------------------------------- #
# 脱敏
# --------------------------------------------------------------------------- #
def env_secret_values(environ: Optional[Dict[str, str]] = None) -> List[str]:
    """从环境变量里收集"要被全文抹掉的密钥值"。**只返回值本身，绝不返回变量名→值映射。**

    按长度降序返回，保证长密钥先被替换（防短密钥是长密钥前缀时替出残渣）。
    """
    env = os.environ if environ is None else environ
    found = set()
    for name, value in env.items():
        if not isinstance(value, str) or len(value) < _MIN_SECRET_LEN:
            continue
        if value.strip().lower() in _ENV_VALUE_STOPWORDS:
            continue
        if _SECRET_ENV_NAME_RE.search(name):
            found.add(value)
    return sorted(found, key=len, reverse=True)


def _is_sensitive_key(key: str) -> bool:
    if key.lower() in _BENIGN_KEY_NAMES:
        return False
    return bool(_SENSITIVE_KEY_RE.search(key))


def _redact_text(text: str, secrets: Sequence[str], stats: Dict[str, int]) -> str:
    for secret in secrets:
        if secret and secret in text:
            stats["value_hits"] += text.count(secret)
            text = text.replace(secret, _REDACTED)
    for pattern, group in _VALUE_PATTERNS:
        def _sub(match: re.Match) -> str:
            stats["pattern_hits"] += 1
            if group == 0:
                return _REDACTED
            whole, target = match.group(0), match.group(group)
            return whole.replace(target, _REDACTED)

        text = pattern.sub(_sub, text)
    return text


def redact(obj: Any, secrets: Sequence[str], stats: Dict[str, int]) -> Any:
    """递归脱敏。**必须在 :func:`jsonable` 之后、截断之前调用**（见模块 docstring 约束 2）。"""
    if isinstance(obj, str):
        return _redact_text(obj, secrets, stats)
    if isinstance(obj, dict):
        out: Dict[str, Any] = {}
        for key, value in obj.items():
            safe_key = _redact_text(str(key), secrets, stats)
            if _is_sensitive_key(str(key)) and isinstance(value, (str, dict, list)):
                stats["key_hits"] += 1
                out[safe_key] = f"<redacted:{safe_key}>"
                continue
            out[safe_key] = redact(value, secrets, stats)
        return out
    if isinstance(obj, list):
        return [redact(item, secrets, stats) for item in obj]
    return obj


def assert_no_known_secret(text: str, secrets: Sequence[str]) -> None:
    """**fail-closed 最后一道**：成品文本里还能搜到已知密钥值就拒绝写盘。

    报错信息里**只写密钥长度与前缀哈希位数，绝不回显密钥本身**。
    """
    for secret in secrets:
        if secret and secret in text:
            raise RealRunEvidenceError(
                "归档物中仍检出已知密钥值（长度 "
                f"{len(secret)}），拒绝写盘。请检查 CURATED_STATE_KEYS 与脱敏规则。"
            )


# --------------------------------------------------------------------------- #
# 结构化 + 截断
# --------------------------------------------------------------------------- #
def jsonable(obj: Any, depth: int = 0) -> Any:
    """把任意对象转成 JSON 可序列化结构（**不做截断、不做脱敏**）。"""
    if depth > MAX_DEPTH:
        return "<max-depth-exceeded>"
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if is_dataclass(obj) and not isinstance(obj, type):
        try:
            return jsonable(asdict(obj), depth + 1)
        except Exception:  # noqa: BLE001 —— 含不可 deepcopy 字段时退回 repr
            return f"<{type(obj).__name__}> {obj!r}"
    if isinstance(obj, dict):
        return {str(k): jsonable(v, depth + 1) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set, frozenset)):
        items = list(obj)
        out = [jsonable(item, depth + 1) for item in items[:MAX_LIST_ITEMS]]
        if len(items) > MAX_LIST_ITEMS:
            out.append(f"<省略 {len(items) - MAX_LIST_ITEMS} 项（上限 {MAX_LIST_ITEMS}）>")
        return out
    model_dump = getattr(obj, "model_dump", None)
    if callable(model_dump):
        try:
            return jsonable(model_dump(), depth + 1)
        except Exception:  # noqa: BLE001
            pass
    return f"<{type(obj).__name__}> {obj!r}"


def truncate_tree(obj: Any) -> Any:
    """对已脱敏结构做长字符串截断（取头尾、掐中间，保留报错尾巴）。"""
    if isinstance(obj, str):
        if len(obj) <= MAX_STR_CHARS:
            return obj
        half = MAX_STR_CHARS // 2
        return f"{obj[:half]}\n...<截断 {len(obj) - MAX_STR_CHARS} 字符>...\n{obj[-half:]}"
    if isinstance(obj, dict):
        return {k: truncate_tree(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [truncate_tree(v) for v in obj]
    return obj


# --------------------------------------------------------------------------- #
# bundle 构建
# --------------------------------------------------------------------------- #
def _repo_head() -> str:
    """读 .git 拿 HEAD sha（不起子进程，失败返回 unknown）。"""
    try:
        head = (PROJECT_ROOT / ".git" / "HEAD").read_text(encoding="utf-8").strip()
        if head.startswith("ref:"):
            ref = head.split(":", 1)[1].strip()
            ref_file = PROJECT_ROOT / ".git" / ref
            if ref_file.exists():
                return ref_file.read_text(encoding="utf-8").strip()[:12]
            packed = PROJECT_ROOT / ".git" / "packed-refs"
            if packed.exists():
                for line in packed.read_text(encoding="utf-8").splitlines():
                    if line.endswith(f" {ref}"):
                        return line.split()[0][:12]
            return "unknown"
        return head[:12]
    except Exception:  # noqa: BLE001
        return "unknown"


def build_bundle(
    *,
    name: str,
    sprint: str,
    state: Any,
    extra: Optional[Dict[str, Any]] = None,
    keys: Iterable[str] = CURATED_STATE_KEYS,
    secrets: Optional[Sequence[str]] = None,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """构建归档 bundle（**纯函数，不碰磁盘**）。

    只取 ``keys`` 白名单中的 state 键；随后 jsonable → redact → truncate 三步。
    """
    if secrets is None:
        secrets = env_secret_values()
    stamp = now or datetime.now()
    stats = {"key_hits": 0, "value_hits": 0, "pattern_hits": 0}

    state_map = state if isinstance(state, dict) else {}
    picked = {k: state_map[k] for k in keys if k in state_map}
    missing = [k for k in keys if k not in state_map]

    payload = {
        "state": jsonable(picked),
        "extra": jsonable(extra or {}),
    }
    payload = redact(payload, secrets, stats)
    payload = truncate_tree(payload)

    return {
        "schema": "realrun-evidence/1",
        "name": name,
        "sprint": sprint,
        "generated_at": stamp.isoformat(timespec="seconds"),
        "repo_head": _repo_head(),
        "state_keys_present": sorted(picked.keys()),
        "state_keys_absent": missing,
        "curated_key_whitelist": list(keys),
        "redaction": {
            **stats,
            "known_secret_count": len(secrets),  # 只记条数，不记值也不记变量名
            "note": "白名单导出 + 键名/环境密钥值/形态正则三重脱敏 + 写盘前 fail-closed 复扫",
        },
        "limits": {
            "max_str_chars": MAX_STR_CHARS,
            "max_list_items": MAX_LIST_ITEMS,
            "max_depth": MAX_DEPTH,
        },
        **payload,
    }


def evidence_dir(sprint: str) -> Path:
    """归档目录：``docs/<sprint>/test-reports/realrun-bundles/``（可被环境变量覆盖）。"""
    if not _SPRINT_RE.match(sprint or ""):
        raise RealRunEvidenceError(f"sprint 形如 sprint7，实际 {sprint!r}")
    override = os.getenv(EVIDENCE_DIR_ENV)
    if override:
        return Path(override)
    return PROJECT_ROOT / "docs" / sprint / "test-reports" / "realrun-bundles"


def write_bundle(
    bundle: Dict[str, Any],
    sink_dir: Path,
    *,
    secrets: Optional[Sequence[str]] = None,
    now: Optional[datetime] = None,
) -> Path:
    """把 bundle 写成 JSON。**先复扫密钥、通过才建目录**（不通过时磁盘上什么都不会多出来）。"""
    if secrets is None:
        secrets = env_secret_values()
    text = json.dumps(bundle, ensure_ascii=False, indent=2, sort_keys=False)
    assert_no_known_secret(text, secrets)

    name = str(bundle.get("name") or "bundle")
    if not _NAME_RE.match(name):
        raise RealRunEvidenceError(f"name 只允许 [A-Za-z0-9._-]，实际 {name!r}")
    stamp = (now or datetime.now()).strftime("%Y-%m-%d_%H%M%S")

    sink_dir = Path(sink_dir)
    sink_dir.mkdir(parents=True, exist_ok=True)
    target = sink_dir / f"{stamp}_{name}.json"
    seq = 1
    while target.exists():
        seq += 1
        target = sink_dir / f"{stamp}_{name}-{seq:02d}.json"
    target.write_text(text, encoding="utf-8")
    return target


# --------------------------------------------------------------------------- #
# e2e 上下文闸门
# --------------------------------------------------------------------------- #
def require_e2e_context(request: Any, what: str) -> None:
    """**第二道防线**：不是 e2e 用例就抛，且抛之前不碰磁盘。

    第一道防线是 `pytest.ini` 的 ``addopts = -m "not e2e"``（默认压根不执行 e2e 用例体）。
    这里再挡一次，是为了让"marker 被摘掉"或"从单测里误调"当场失败，而不是悄悄写盘——
    2026-08-07 刚修过一个"测试写全局固定路径"的坑（P-S8-20），不在隔壁再开一个。
    """
    node = getattr(request, "node", None)
    getter = getattr(node, "get_closest_marker", None)
    if not callable(getter):
        raise RealRunEvidenceError(
            f"{what} 需要 pytest 的 request fixture（拿不到 request.node.get_closest_marker）"
        )
    if getter("e2e") is None:
        raise RealRunEvidenceError(
            f"{what} 只允许在 @pytest.mark.e2e 用例中调用；当前用例无 e2e 标记，已拒绝写盘"
        )


def durable_run_dir(request: Any, name: str, *, now: Optional[datetime] = None) -> Path:
    """真跑用的**持久**运行目录：``workspace/runs/<name>_<时间戳>/``。

    ``workspace/`` 已在 `.gitignore` 内 ⇒ 原始产物（checkpoint 库、代码、日志）留在磁盘上
    可事后复核，但不进版本控制、不污染 ``git status``。**替代 ``tmp_path``**——后者被 pytest
    只保留最近 3 轮，是"跑完即灭失"的结构性根因。
    """
    require_e2e_context(request, "durable_run_dir()")
    if not _NAME_RE.match(name or ""):
        raise RealRunEvidenceError(f"name 只允许 [A-Za-z0-9._-]，实际 {name!r}")
    stamp = (now or datetime.now()).strftime("%Y%m%d_%H%M%S")
    target = PROJECT_ROOT / "workspace" / "runs" / f"{name}_{stamp}"
    target.mkdir(parents=True, exist_ok=True)
    return target


def dump_realrun_bundle(
    request: Any,
    *,
    sprint: str,
    name: str,
    state: Any,
    extra: Optional[Dict[str, Any]] = None,
    sink_dir: Optional[Path] = None,
    keys: Iterable[str] = CURATED_STATE_KEYS,
    now: Optional[datetime] = None,
) -> Path:
    """真跑用例的归档入口：构建 → 脱敏 → 复扫 → 写盘，返回落盘路径。

    只在 ``@pytest.mark.e2e`` 用例中可用（见 :func:`require_e2e_context`）。
    """
    require_e2e_context(request, "dump_realrun_bundle()")
    secrets = env_secret_values()
    bundle = build_bundle(
        name=name, sprint=sprint, state=state, extra=extra, keys=keys, secrets=secrets, now=now
    )
    return write_bundle(bundle, sink_dir or evidence_dir(sprint), secrets=secrets, now=now)


# --------------------------------------------------------------------------- #
# 事后补档：从 checkpoint 库取 state（覆盖"手工真跑"，如 S7-10 的 UMAP 那次）
# --------------------------------------------------------------------------- #
def list_threads(db_path: Path) -> List[str]:
    import sqlite3

    conn = sqlite3.connect(f"file:{Path(db_path).resolve()}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT thread_id, MAX(checkpoint_id) FROM checkpoints GROUP BY thread_id"
        ).fetchall()
    finally:
        conn.close()
    return [str(r[0]) for r in rows]


def read_state_from_checkpoint(db_path: Path, thread_id: str) -> Dict[str, Any]:
    """读某 thread 的最新 checkpoint 的 ``channel_values``（只读打开，不写原库）。"""
    import sqlite3

    from langgraph.checkpoint.sqlite import SqliteSaver

    conn = sqlite3.connect(
        f"file:{Path(db_path).resolve()}?mode=ro", uri=True, check_same_thread=False
    )
    try:
        saver = SqliteSaver(conn)
        tup = saver.get_tuple({"configurable": {"thread_id": thread_id}})
        if tup is None:
            raise RealRunEvidenceError(f"checkpoint 库中无 thread_id={thread_id!r}")
        return dict(tup.checkpoint.get("channel_values") or {})
    finally:
        conn.close()


def dump_from_checkpoint_db(
    db_path: Path,
    thread_id: str,
    *,
    sprint: str,
    name: str,
    sink_dir: Optional[Path] = None,
    now: Optional[datetime] = None,
) -> Path:
    """手工真跑事后补档（**不经 pytest，故无 e2e 闸门**；由人显式在命令行触发）。"""
    state = read_state_from_checkpoint(db_path, thread_id)
    secrets = env_secret_values()
    bundle = build_bundle(
        name=name,
        sprint=sprint,
        state=state,
        extra={"source": "checkpoint-db", "thread_id": thread_id, "db": str(db_path)},
        secrets=secrets,
        now=now,
    )
    return write_bundle(bundle, sink_dir or evidence_dir(sprint), secrets=secrets, now=now)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tests.realrun_evidence",
        description="把一次真跑的关键 state 落成脱敏 bundle JSON（证据归档）",
    )
    parser.add_argument("--db", required=True, help="checkpoint sqlite 路径（只读打开）")
    parser.add_argument("--thread", help="thread_id；不给则等价于 --list")
    parser.add_argument("--list", action="store_true", help="只列出库里的 thread_id")
    parser.add_argument("--sprint", default="sprint7", help="归档到 docs/<sprint>/test-reports/")
    parser.add_argument("--name", default="realrun", help="归档物名字（[A-Za-z0-9._-]）")
    parser.add_argument("--out", help="覆盖归档目录")
    args = parser.parse_args(argv)

    db = Path(args.db)
    if not db.exists():
        print(f"[evidence] 找不到 {db}", file=sys.stderr)
        return 2
    if args.list or not args.thread:
        for tid in list_threads(db):
            print(tid)
        return 0
    target = dump_from_checkpoint_db(
        db,
        args.thread,
        sprint=args.sprint,
        name=args.name,
        sink_dir=Path(args.out) if args.out else None,
    )
    print(f"[evidence] -> {target}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
