"""S7-10 独立验收补测（测试工程师侧的覆盖缺口回填，非开发交付件）。

本文件**只补开发交付里缺的那几刀**，不重复 `tests/test_sprint7_s710_exec_locality.py`
已经覆盖到的维度，也**不弱化**其中任何一条断言。补的四类缺口：

1. **Q-S7-23 语料残缺**（架构 `architecture.md` §19.7 定稿"必须命中 5 条"：
   127 / 144 / **183** / **510** / **1304**）。交付件的 ``CORPUS_MUST_HIT`` 只落了
   前 3 条，`510` / `1304` 两条**在整个 tests/ 里零出现**，而 dev-plan CP-6.5-1
   与该文件用例 docstring 都自称"5 条"。⇒ 本文件把漏掉的两条逐字补回，并加一条
   **完整性守门**，日后再有人删语料会当场红。

2. **"共用同一条谓词"从未跨到工具层**。交付件的
   ``test_cp_6_5_4_w5_shares_one_predicate_with_tool_layer`` 两侧都在
   ``core.plan_checks`` 内（`is_inline_code_write` vs `check_plan` 的 W5），
   **一次都没碰工具层**。实测：把工具层那句判定改成恒不拦，该用例仍绿。
   ⇒ 本文件补一条**真·跨层一致性**断言（计划期判定 ⟺ 执行期是否被拒）。

3. **AC-S7-47② 的"5 条合法探针"实际只喂了 3 条语料探针 + 1 条脚本运行**（共 4 条）。
   ⇒ 本文件按 AC 原文补足 5 条探针，并断言它们**真的进了 runner**（不是被静默吞掉）。

4. **形态 2（把整条实验流水线塞进命令行）从未在工具层验过**。交付件只把语料 183
   喂给了纯谓词，工具层只验过 127 那条写占位符的。而架构 §19.5 把 183 重标为
   "PRD §12.5.3 定义的形态 2 本身"——它才是"按动词判会整个漏掉"的那一类。

⚠ 另有一条**已在真起子进程下复现的生产缺陷**（BUG-S7-10-01，见文末）：
在 ``-c`` 之前加任何解释器 flag（``python -u -c`` / ``-B`` / ``-X utf8`` / ``python3 -uc``）
即可整个绕过约束 C 的**唯一硬防线**——同一条原始罪证载荷会被真正执行、文件真落盘、
且**进 step_ledger**。测试工程师当时把它钉成 strict xfail 追踪位（不修生产代码）。

**2026-07-31 已由 @全栈开发代理修复**：`is_inline_code_write` 改为在 argv 里**扫描
定位 `-c` 的载荷位**（`core/plan_checks.py::_inline_python_payload`），不再硬编码
`argv[1] == "-c"`；仍是**单一规则**（判据只有长度），未加动词 / 后缀枚举、未动阈值。
修好那一刻 5 条 xfail 如设计般 XPASS 当场红（实测 5 failed）⇒ 标记**转正为常规断言**，
并按缺陷的真实后果面补了两组：①绕过形态在**工具层**必须被拒且不进台账；
②**误伤边界**（`bash -c` / `pip install -c` / `python -m pip install -c` / 脚本自己的
`-c` 一律不得触发）——把漏放修成误伤同样是缺陷。

全离线维（零 LLM、零网络、零 deepxiv 配额）。
"""
from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pytest

from sandbox import local_venv
from tests.test_sprint7_s710_exec_locality import (
    CORPUS_EXPECTED_HIT_RECOVERABLE,
    CORPUS_MUST_HIT,
    CORPUS_MUST_PASS,
    _wrap_inline,
)

execution_module = importlib.import_module("core.nodes.execution")
plan_checks = importlib.import_module("core.plan_checks")


# =========================================================================== #
# 缺口 1：Q-S7-23 判定补入"必须命中"清单的那两条真实载荷
#
# 出处：`round_1.log:106`（510 字符，写 eval_knn_on_embeddings.py 的真实实现）与
# `round_1.log:92`（1304 字符，写 run_repro_basics.py 的真实实现）。
# 两条与 127 / 144 **同一形态**（`p.write_text('<内容>')`），只是把载荷从占位符换成
# 了真实实现 —— 正是 dev-plan §48 P-20 实测证明的"执行环节在履行计划自己写下的
# 那个写文件步骤"。载荷**逐字抄自归档日志**（脚本抽取，非手抄），不得改写。
# =========================================================================== #

#: 被交付件漏掉的两条「必须命中」语料（Q-S7-23 判定补入），(载荷长度, 载荷)。
OMITTED_MUST_HIT: Tuple[Tuple[int, str], ...] = (
    (  # round_1.log:106 —— 写 eval_knn_on_embeddings.py 的**真实实现**（非占位符）
        510,
        "from pathlib import Path; p=Path('eval_knn_on_embeddings.py'); p.write_text('import js"
        "on\\nfrom pathlib import Path\\nmetrics_path = Path(\\'repro_outputs/metrics/summary.json"
        "\\')\\nif metrics_path.exists():\\n    metrics = json.loads(metrics_path.read_text())\\n  "
        "  print(\\'<METRICS>\\' + json.dumps({\\'copied_test_accuracy\\': metrics.get(\\'test_accur"
        "acy\\'), \\'source\\': str(metrics_path), \\'scale\\': metrics.get(\\'scale\\')}) + \\'</METRI"
        "CS>\\')\\nelse:\\n    raise SystemExit(\\'missing metrics file\\')\\n'); print(str(p))"
    ),
    (  # round_1.log:92 —— 写 run_repro_basics.py 的**真实实现**（非占位符）
        1304,
        "from pathlib import Path; p=Path('run_repro_basics.py'); p.write_text('from pathlib im"
        'port Path\\nimport json\\nimport numpy as np\\nfrom sklearn.datasets import load_digits\\n'
        'from sklearn.model_selection import train_test_split\\nfrom sklearn.neighbors import KN'
        'eighborsClassifier\\nfrom sklearn.metrics import accuracy_score\\nimport umap\\n\\nout_dir'
        " = Path(\\'repro_outputs\\')\\nmetrics_dir = out_dir / \\'metrics\\'\\nmetrics_dir.mkdir(par"
        'ents=True, exist_ok=True)\\nX, y = load_digits(return_X_y=True)\\nX_train, X_test, y_tra'
        'in, y_test = train_test_split(X, y, test_size=0.2, random_state=42, stratify=y)\\nreduc'
        'er = umap.UMAP(n_neighbors=15, min_dist=0.1, n_components=2, random_state=42)\\nX_train'
        '_emb = reducer.fit_transform(X_train)\\nX_test_emb = reducer.transform(X_test)\\nclf = K'
        'NeighborsClassifier(n_neighbors=5)\\nclf.fit(X_train_emb, y_train)\\npreds = clf.predict'
        "(X_test_emb)\\nacc = float(accuracy_score(y_test, preds))\\nmetrics = {\\n    \\'dataset\\'"
        ": \\'sklearn_digits\\',\\n    \\'scale\\': \\'reduced\\',\\n    \\'n_samples\\': int(X.shape[0])"
        ",\\n    \\'n_features\\': int(X.shape[1]),\\n    \\'embedding_dim\\': 2,\\n    \\'knn_k\\': 5,\\"
        "n    \\'test_accuracy\\': acc\\n}\\n(metrics_dir / \\'summary.json\\').write_text(json.dumps"
        "(metrics, indent=2))\\nprint(\\'<METRICS>\\' + json.dumps(metrics) + \\'</METRICS>\\')\\n');"
        ' print(str(p))'
    ),
)

#: 架构 §19.7 定稿的"必须命中"全集（长度维度）。
MANDATED_MUST_HIT_LENGTHS = frozenset({127, 144, 183, 510, 1304})


@pytest.mark.parametrize("length,payload", OMITTED_MUST_HIT, ids=lambda v: str(v)[:16])
def test_q_s7_23_predicate_hits_the_two_omitted_corpus_entries(
    length: int, payload: str,
) -> None:
    """Q-S7-23：被交付件漏掉的两条"必须命中"语料，谓词必须判 True。

    Q-S7-23 的原话是"属事实遗漏、非有意排除"——给不出"收 92 不收 106"的原则性
    理由。它们对阈值零影响（510 / 1304 都 > 127，窗口不变），但**清单本身必须完整**：
    ground truth 缺项会让"标定过了"这句话失去可核对的基准。
    """
    assert len(payload) == length, "载荷被改写了——必须逐字抄自归档日志"
    assert plan_checks.is_inline_code_write(_wrap_inline(payload)) is True


def test_q_s7_23_must_hit_ground_truth_is_complete() -> None:
    """完整性守门：交付件语料 + 本文件补入的两条，必须**恰好**覆盖架构定稿的 5 条。

    这条断言存在的意义是防"语料被悄悄删成能过的子集"——ground truth 一旦可被裁剪，
    "阈值经真实语料检验"这句话就退化成了自证。**禁止放宽为 `>=` 或 `issubset`。**
    """
    covered = {length for length, _ in CORPUS_MUST_HIT} | {
        length for length, _ in OMITTED_MUST_HIT
    }
    assert covered == MANDATED_MUST_HIT_LENGTHS, (
        f"「必须命中」语料集与架构 §19.7 定稿不符：实际 {sorted(covered)}，"
        f"应为 {sorted(MANDATED_MUST_HIT_LENGTHS)}"
    )


# =========================================================================== #
# 缺口 2 / 3 / 4：工具层（真·执行期）
# =========================================================================== #


@pytest.fixture()
def fake_runner_tool(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Dict[str, Any]:
    """工具层夹具：runner 被替身接管，只观测"拒 / 不拒"与"是否真进了 runner"。

    此处刻意**不起真子进程**——本组断言测的是判定与放行，不是磁盘副作用；
    磁盘副作用由交付件 `test_cp_6_6_1_*`（真子进程）与其阳性对照负责，不重复。
    """
    ws = tmp_path / "workspace"
    work = ws / "code"
    work.mkdir(parents=True)
    monkeypatch.setattr(local_venv, "WORKSPACE_DIR", ws)

    calls: List[List[str]] = []

    def _fake_run(python_exe: str, command: List[str], work_dir: str, *a: Any, **k: Any):
        calls.append(list(command))
        return local_venv.SandboxRunResult(
            exit_code=0, stdout="", stderr="", duration_seconds=0.0,
            timed_out=False, output_truncated=False, command=list(command),
        )

    monkeypatch.setattr(execution_module, "run_in_venv", _fake_run)
    collector = execution_module._SandboxRunCollector()
    tool = execution_module.make_run_in_sandbox_tool(
        str(work), collector, None, {"python_exe": str(work / ".venv" / "bin" / "python")},
    )
    return {"tool": tool, "calls": calls, "collector": collector, "work": work}


def _rejected(tool: Any, command: str) -> bool:
    """跑一条命令，返回"是否被工具层拒绝"。"""
    return json.loads(tool.invoke({"command": command})).get("tool_error") is True


def test_audit_tool_layer_and_plan_layer_agree_on_every_corpus_command(
    fake_runner_tool: Dict[str, Any],
) -> None:
    """跨层一致性（**这才是"一处定义、两处调用"的真断言**）。

    交付件那条同名用例两侧都在 `plan_checks` 内、一次都没碰工具层：实测把工具层
    判定换成恒不拦，它照样绿。本用例逐条比对**计划期谓词**与**执行期是否被拒**，
    任一侧改用另一套判定（或被摘掉）当场红。

    语料取全集（必须命中 5 条 + 必须放行 3 条 + 预期命中可恢复 1 条），
    ⇒ 同时覆盖"该拒的拒"与"不该拒的不拒"两向。
    """
    corpus = tuple(CORPUS_MUST_HIT) + tuple(OMITTED_MUST_HIT) + tuple(
        CORPUS_MUST_PASS
    ) + tuple(CORPUS_EXPECTED_HIT_RECOVERABLE)
    assert len(corpus) == 9, "语料全集条数变了——请同步核对架构 §19.5 那张标定表"

    for _, payload in corpus:
        command = _wrap_inline(payload)
        via_plan = plan_checks.is_inline_code_write(command)
        via_tool = _rejected(fake_runner_tool["tool"], command)
        assert via_plan == via_tool, (
            f"计划期与执行期判定分叉（计划期={via_plan} 执行期={via_tool}）："
            f"{payload[:60]!r} —— 同一条不变量必须一处定义两处调用"
        )


def test_ac_s7_47_five_legal_probes_and_a_script_run_are_not_blocked(
    fake_runner_tool: Dict[str, Any],
) -> None:
    """AC-S7-47② 按原文补足**5 条**合法探针 + 1 条"跑一个既有脚本"。

    交付件只喂了 3 条语料探针 + 1 条脚本运行（共 4 条）——语料在 Q-S7-21 重标后
    只剩 3 条真探针，缺的两条**该补而不是该减**（AC 原文未把这 5 条绑定到语料行）。
    另断言它们**真的进了 runner**：只断"没被拒"证不了它被执行，静默吞掉也不会被拒。
    """
    probes = [_wrap_inline(payload) for _, payload in CORPUS_MUST_PASS]
    probes.append(_wrap_inline("import sys; print(sys.version)"))
    probes.append(_wrap_inline("import numpy; print(numpy.__version__)"))
    assert len(probes) == 5, "AC-S7-47② 要求 5 条合法探针"
    commands = probes + ["python run_repro_basics.py --dataset digits"]

    for command in commands:
        assert not _rejected(fake_runner_tool["tool"], command), f"合法命令被误拒：{command}"

    assert len(fake_runner_tool["calls"]) == len(commands), (
        "有命令没被真正交给 runner —— 只断'没被拒'证不了它被执行"
    )
    assert len(fake_runner_tool["collector"].run_results) == len(commands)


def test_audit_tool_layer_rejects_form_two_whole_pipeline_in_one_command(
    fake_runner_tool: Dict[str, Any],
) -> None:
    """形态 2 必须在**工具层**被拒（交付件只在纯谓词层验过它）。

    形态 2 = 不写文件、直接把整条实验流水线塞进命令行算完打印结果
    （架构 §19.5 把语料 183 重标为形态 2 本身）。它是"按写文件动词判会整个漏掉"
    的那一类 —— 只在谓词层验、不在工具层验，等于没验到它真的跑不起来。
    """
    form_two = [payload for length, payload in CORPUS_MUST_HIT if length == 183]
    assert form_two, "语料里形态 2 那条（183）不见了"
    tool = fake_runner_tool["tool"]
    assert _rejected(tool, _wrap_inline(form_two[0])), "形态 2 在工具层没被拒"
    assert fake_runner_tool["calls"] == [], "形态 2 被交给了 runner —— 早退没生效"
    assert fake_runner_tool["collector"].run_results == []
    assert fake_runner_tool["collector"].step_ledger == []


def test_audit_rejection_payload_keeps_the_existing_tool_error_shape(
    fake_runner_tool: Dict[str, Any],
) -> None:
    """拒绝返回**沿既有 `_tool_error_json` 形态，不新增字段**（dev-plan T-6-6 第 2 条）。

    与"同一路径下的另一种拒绝"（沙箱未准备好）逐键比对：键集合必须完全相同。
    新增字段会打破 ToolMessage 的既有消费面（回读 / backfill 都按现有键写的）。
    """
    long_payload = "x" * (plan_checks._INLINE_PY_MAX_CHARS + 1)
    rejected = json.loads(
        fake_runner_tool["tool"].invoke({"command": f'python -c "{long_payload}"'})
    )
    baseline = json.loads(
        execution_module._tool_error_json(
            "基线形态", exit_code=-1, results=[], timed_out=False,
        )
    )
    assert set(rejected) == set(baseline), (
        f"拒绝返回的键集合与既有 _tool_error_json 形态不一致："
        f"多出 {set(rejected) - set(baseline)}，缺少 {set(baseline) - set(rejected)}"
    )
    assert rejected["exit_code"] == -1 and rejected["tool_error"] is True


# =========================================================================== #
# BUG-S7-10-01：解释器 flag 绕过约束 C 的唯一硬防线
# =========================================================================== #


#: BUG-S7-10-01 的全部已知绕过形态（测试工程师 2026-07-31 真起子进程逐条复现过）。
#: `-W ignore` 一条是修复时按同族推演补入的（`-W` 与 `-X` 同属"吃一个参数"的短选项）。
BYPASS_PREFIXES: Tuple[str, ...] = (
    "python -u -c",
    "python -B -c",
    "python -X utf8 -c",
    "python -W ignore -c",
    "python3 -uc",
    "env python -c",
)


@pytest.mark.parametrize("command_prefix", BYPASS_PREFIXES)
def test_bug_s7_10_01_interpreter_flag_before_dash_c_must_not_bypass_hard_gate(
    command_prefix: str,
) -> None:
    """约束 C 的硬防线不得被"在 -c 前插一个 flag"绕过。

    判定应当走「内容从哪来」（PRD §12.5.3 / 架构 §19.1）——超长行内载荷就是超长
    行内载荷，与解释器带不带 flag 无关。`-u`（不缓冲输出）在本领域是模型最常写出
    的 flag 之一，这条绕过路径的现实触发概率不低。

    ⚠ 本用例原为 `@pytest.mark.xfail(strict=True)` 的**追踪位**（缺陷未修时钉在此处）。
    2026-07-31 由 @全栈开发代理修复：谓词改为**在 argv 里扫描定位 `-c` 的载荷位**
    （`core/plan_checks.py::_inline_python_payload`），不再硬编码 `argv[1] == "-c"`。
    修好那一刻这 5 条当场 XPASS 转红（实测 5 failed），**标记随之转正为常规断言**——
    断言原文一字未改、参数化形态只增不减（新增 `-W ignore`）。
    """
    long_payload = "x" * (plan_checks._INLINE_PY_MAX_CHARS + 1)
    assert plan_checks.is_inline_code_write(f'{command_prefix} "{long_payload}"') is True


@pytest.mark.parametrize("command_prefix", BYPASS_PREFIXES)
def test_bug_s7_10_01_bypass_forms_are_rejected_by_the_tool_layer(
    command_prefix: str, fake_runner_tool: Dict[str, Any],
) -> None:
    """BUG-S7-10-01 的**后果面**回归：绕过形态必须在工具层被真正拒绝。

    缺陷的危害不在谓词判 False 这一步，而在其下游——测试工程师实测：绕过后
    `tool_error=None`、`exit_code=0`、**文件真落盘**、**且进 step_ledger 1 条**，
    于是会被 `exit_ok` 计入、被步骤对账当成"完成"（正是 R-S7-49 那类假绿）。
    ⇒ 只在谓词层断"判 True"证不到这一层，必须断**命令没进 runner、两个台账皆空**。
    """
    long_payload = "x" * (plan_checks._INLINE_PY_MAX_CHARS + 1)
    tool = fake_runner_tool["tool"]
    assert _rejected(tool, f'{command_prefix} "{long_payload}"'), (
        f"{command_prefix} 形态没被工具层拒绝 —— 硬防线仍可被一个 flag 绕过"
    )
    assert fake_runner_tool["calls"] == [], "绕过形态被交给了 runner —— 早退没生效"
    assert fake_runner_tool["collector"].run_results == []
    assert fake_runner_tool["collector"].step_ledger == [], (
        "被拒命令进了 step_ledger —— 会被 exit_ok 计入、被步骤对账当成'完成'"
    )


#: `-c` 属于**别的程序**（而非 Python 解释器）的形态：修 BUG-S7-10-01 时**不得误伤**。
#: 载荷一律取超阈值长度 —— 若判定退化成"命令里出现过 -c 就拦"，这些会当场变红。
NON_PYTHON_DASH_C_PREFIXES: Tuple[str, ...] = (
    "bash -c",            # shell 的 -c，与 Python 无关
    "sh -c",
    "node -c",            # 其它语言的解释器
    "pip install -c",     # pip 的 -c 是 constraints 文件
    "python -m pip install -c",  # -m 之后归模块，那个 -c 是 pip 的
    "python train.py -c",        # -c 是被执行脚本自己的参数
)


@pytest.mark.parametrize("command_prefix", NON_PYTHON_DASH_C_PREFIXES)
def test_bug_s7_10_01_fix_does_not_misfire_on_other_programs_dash_c(
    command_prefix: str, fake_runner_tool: Dict[str, Any],
) -> None:
    """修复的**误伤边界**：`-c` 不属于 Python 解释器时，两侧都不得触发。

    "在 argv 里定位 -c"这个修法若写成"扫到 `-c` 就算"，会把 `bash -c` /
    `pip install -c constraints.txt` / `python -m pip install -c ...` 一并拦死 ——
    那是把一个漏放缺陷换成一个更贵的误伤缺陷（约束 C 的产品前提是**零误伤正常复现**）。
    谓词层与工具层两侧同判，缺任一侧都说明修法把边界画错了。
    """
    long_arg = "x" * (plan_checks._INLINE_PY_MAX_CHARS + 1)
    command = f'{command_prefix} "{long_arg}"'
    assert plan_checks.is_inline_code_write(command) is False, (
        f"{command_prefix} 的 -c 不是 Python 解释器的 -c，谓词不该命中"
    )
    assert not _rejected(fake_runner_tool["tool"], command), f"合法命令被误拒：{command}"
