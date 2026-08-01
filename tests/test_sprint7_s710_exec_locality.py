"""S7-10（计划与编码/执行的落点对齐）收口测试 —— AC-S7-44 ~ AC-S7-53。

本批修的是**计划、编码、执行三者之间的落点契约**：代码在哪写、实验在哪跑、代码由谁写。
三条约束**必须同批生效**（AC-S7-52 / R-S7-47），本文件按约束分区组织：

  A 约束 A（落点，**软保证**）  ── planning 冻结区删 `cd` 授权 + 正面口径（AC-S7-44）
  B 约束 B（计划不越权，软保证）── planning 冻结区禁"先写占位文件再运行"（AC-S7-45）
  C 约束 C（执行不写码，**硬保证**）
      C1 execution 冻结区收窄（AC-S7-46）
      C2 共用纯谓词 `is_inline_code_write`（Q-S7-21 真实语料标定）
      C3 **工具层硬拦截**（AC-S7-47 ★命门，须验红）
  W 计划期确定性告警 W4 / W5（AC-S7-48）
  R 参考仓库不接收复现代码与复现产物（AC-S7-50）
  X 边界不放宽、无扰动（AC-S7-53）

⚠ 三条口径纪律（PRD §12.7，写断言前必须先读）
==========================================================================
1. **步骤对账满分不作证**：它把「实际执行的 argv」与「agent **自报的** step_index」
   绑定后回查 ⇒ 换了命令再自报同一下标照样判"完成"（R-S7-49）。
   **本文件任何断言都不得引 `step_reconciliation` 作"计划被忠实执行"的证据。**
2. **主断言是"孤儿消失"，不是"结果与论文表格对上"**（R-S7-54）——那属 T-6-9 真跑。
3. **首轮真跑失败是预期且正确的行为**（R-S7-52）。

⚠ 已知 bug 模式 #6：`core/nodes/__init__.py` 的显式 export 会遮蔽同名子模块，
访问模块级私有常量一律走 `importlib.import_module`，不得 `from core.nodes import x`。

全离线维（零 LLM、零网络、零 deepxiv 配额）。C3 分区**真起子进程**（本机 python），
这是"磁盘上确实没被写出来"唯一可信的证明方式——只断返回值不断磁盘等于没证明。
"""
from __future__ import annotations

import importlib
import logging
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pytest

from sandbox import local_venv

execution_module = importlib.import_module("core.nodes.execution")
planning_module = importlib.import_module("core.nodes.planning")
plan_checks = importlib.import_module("core.plan_checks")


# =========================================================================== #
# 真实语料（Q-S7-21 标定基准）
#
# 出处：workspace/1802.03426/code/exec_logs/round_0.log + round_1.log 的全部
# `python -c` 子命令（两轮各 7 条，**去重后 9 条**——架构 §19.5 那张标定表正好 9 行；
# 早先文档里的"8 条"是笔误，已由 dev-plan §48 P-34 订正，可行窗口 [98,126] 与
# 定稿 120 均不受影响）。**逐字抄自日志，不得改写**——
# 一改写就退化成"断言自己造的分布"，正是 dev-plan T-6-1 明令禁止的做法。
#
# ⚠ 分类经架构 Q-S7-21 重标（dev-plan §48 P-29 留档）：
#   - 183 那条不是探针：它加载真实数据集、按论文超参跑完整降维、打印结果
#     = PRD §12.5.3 定义的**形态 2**（把整条实验流水线塞进命令行）⇒ 必须命中；
#   - 181 那条（三连 mkdir）在可行窗口 [98,126] 内任何取值下都会被拒 ⇒
#     **预期命中且可恢复**（拒绝文案指路"拆短或先落成脚本"），不计入误伤。
# =========================================================================== #

#: 必须命中（在命令行里写代码 / 塞整条流水线），(载荷长度, 载荷) —— 5 条。
CORPUS_MUST_HIT: Tuple[Tuple[int, str], ...] = (
    (
        127,
        "from pathlib import Path; p=Path('eval_knn_on_embeddings.py'); "
        "p.write_text('print(\\'knn eval placeholder\\')\\n'); print(str(p))",
    ),
    (
        144,
        "from pathlib import Path; p=Path('run_repro_basics.py'); "
        "p.write_text('print(\\'please implement reproduction pipeline here\\')\\n'); print(str(p))",
    ),
    (
        183,
        "from sklearn.datasets import load_digits; import umap; X,y=load_digits(return_X_y=True); "
        "emb=umap.UMAP(n_neighbors=15, min_dist=0.1, n_components=2).fit_transform(X); print(emb.shape)",
    ),
)

#: 必须放行（真·短探针），(载荷长度, 载荷) —— 3 条。
CORPUS_MUST_PASS: Tuple[Tuple[int, str], ...] = (
    (36, "import umap; print('UMAP import ok')"),
    (46, "import os; print(sorted(os.listdir('.'))[:20])"),
    (
        98,
        "from sklearn.datasets import load_digits; X,y=load_digits(return_X_y=True); "
        "print(X.shape, len(y))",
    ),
)

#: 预期命中且可恢复（不算误伤，Q-S7-21 重标）。
CORPUS_EXPECTED_HIT_RECOVERABLE: Tuple[Tuple[int, str], ...] = (
    (
        181,
        "import os; os.makedirs('repro_outputs', exist_ok=True); "
        "os.makedirs('repro_outputs/figures', exist_ok=True); "
        "os.makedirs('repro_outputs/metrics', exist_ok=True); print('dirs ready')",
    ),
)

#: `round_0.log:121` 那条**原始罪证**的完整命令（本批立项的直接物证）。
ORIGINAL_PLACEHOLDER_WRITE_COMMAND: str = (
    "python -c \"from pathlib import Path; p=Path('run_repro_basics.py'); "
    "p.write_text('print(\\\\'please implement reproduction pipeline here\\\\')\\\\n'); print(str(p))\""
)


def _wrap_inline(payload: str) -> str:
    """把裸载荷包成一条 `python -c \"<payload>\"` 命令（shlex 可解析的形态）。"""
    return 'python -c "%s"' % payload.replace('"', '\\"')


# =========================================================================== #
# A / B：planning 冻结区（AC-S7-44 / AC-S7-45）
# =========================================================================== #

_PLANNING_BODY: str = planning_module._PLANNING_SYSTEM_PROMPT_BODY


def test_ac_s7_44_planning_body_no_longer_authorizes_cd() -> None:
    """AC-S7-44 负向：切目录授权那半句**已不存在**。

    改法是"删授权"而非"加禁令"（Q-S7-16 补充 E）：系统侧落点默认已对
    （`execution.py` 的 work_dir 恒为 code_output_dir），删掉授权即可把这个动作
    从"默认允许"变成"从未被授权"——省字节、少一条要被服从的规则。
    """
    assert "`cd <子目录>`" not in _PLANNING_BODY, (
        "planning 主体仍在授权切目录——约束 A 的计划侧改动没落地"
    )
    assert "仅限工作区内" not in _PLANNING_BODY, (
        "planning 主体仍留着切目录授权的限定语——半句删干净了才算"
    )


def test_ac_s7_44_planning_body_has_four_positive_statements() -> None:
    """AC-S7-44 正向四类措辞缺一不可。

    ⚠ 第四条尤其重要：**不得要求模型写绝对路径**——code_output_dir 由下游编码环节
    创建，planning 运行时它还不存在（dev-plan §41.4 事实 20），要求引用一个拿不到
    的路径必然导致编造。
    """
    assert "相对代码目录" in _PLANNING_BODY, "缺「命令相对代码目录书写」口径"
    assert "pip install -e" in _PLANNING_BODY, "缺「以可编辑方式安装参考仓库」口径"
    assert "不要进入仓库目录" in _PLANNING_BODY, "缺「不要进入仓库目录」口径"
    assert "系统已把执行的工作目录设为" in _PLANNING_BODY, "缺「系统已把工作目录设为代码目录」口径"
    assert "不要写绝对路径" in _PLANNING_BODY, (
        "缺「不要写绝对路径」——code_output_dir 在规划期尚不存在，要求写绝对路径必致编造"
    )


def test_ac_s7_45_planning_body_forbids_placeholder_then_run_step() -> None:
    """AC-S7-45：措辞必须针对**步骤形态**，而不是"占位内容"。

    dev-plan §48 P-20 已实测证明：执行环节第二轮不是"偏离计划自救"，而是在**履行
    计划自己写下的那个写文件步骤**，只把载荷从占位符换成了真实实现。⇒ 只说"别写
    占位符内容"，模型完全可以换成真代码继续写，步骤形态没变、越权照旧。
    """
    assert "不得生成\"先写一个占位文件、再运行该占位文件\"这类步骤" in _PLANNING_BODY
    assert "步骤形态本身" in _PLANNING_BODY, (
        "约束 B 措辞必须点明针对的是**步骤形态**，否则换个载荷就绕过去了（P-20）"
    )
    assert "无论写进去的是占位符" in _PLANNING_BODY, "须显式覆盖'换成真实实现'这一变体"


def test_ac_s7_44_planning_new_text_has_zero_interpolation() -> None:
    """AC-S7-44：新增文案零插值（无花括号 / 无论文标识 / 无绝对路径）。"""
    start = _PLANNING_BODY.index("【执行落点")
    end = _PLANNING_BODY.index("6. expected_results")
    segment = _PLANNING_BODY[start:end]
    assert "{" not in segment and "}" not in segment, "新增文案含花括号（Prompt Cache 插值风险）"
    assert "arxiv" not in segment.lower(), "新增文案含论文标识"
    assert "/data" not in segment and "/workspace" not in segment, "新增文案含绝对路径"


def test_ac_s7_44_planning_body_byte_identical_across_papers() -> None:
    """AC-S7-44：跨两篇不同论文，planning 主体字节一致（Prompt Cache 前缀不破）。"""
    a = planning_module._build_planning_system_prompt({"arxiv_id": "1802.03426"})
    b = planning_module._build_planning_system_prompt({"arxiv_id": "2405.14831"})
    assert a == b == _PLANNING_BODY


def test_s710_outputs_dir_convention_present() -> None:
    """CP-6.3-6（二选一留痕）：产出目录口径**保留未砍**。

    砍掉它则多组指标的收集通道依然恒空（`_collect_grouped_metrics` 只扫
    `<work_dir>/outputs`，R-S7-55）——"修了但没全修"。本批选择保留，故此断言存在；
    若日后有人删掉这句提示词，本断言会红，逼他去 handoff 显式登记那条代价。
    """
    assert "outputs/" in _PLANNING_BODY, (
        "产出目录口径被删——删它必须在交接文档显式登记「多组指标通道仍恒空」（R-S7-55）"
    )


# =========================================================================== #
# C1：execution 冻结区收窄（AC-S7-46）
# =========================================================================== #

_EXECUTION_BODY: str = execution_module._EXECUTION_SYSTEM_PROMPT_BODY


def test_ac_s7_46_execution_body_drops_inline_fix_authorization() -> None:
    """AC-S7-46 负向：「修正相对路径」这个**内联写码的授权口**已撤。

    保留的是"补装缺失包 / 调整依赖版本 / 重试"——界线是「**适配环境 ≠ 代写代码**」，
    与全局文档 §4.5.3 把界画在装包一侧的既有口径对齐，不是新收窄。
    """
    assert "修正相对路径" not in _EXECUTION_BODY
    assert "补装缺失包" in _EXECUTION_BODY, "装包类就地修正是保留项，不得连带删掉"


def test_ac_s7_46_execution_body_forbids_writing_code() -> None:
    """AC-S7-46 正向：明写"不得写入或修改任何代码文件" + 交回代码生成环节。"""
    assert "不得写入或修改任何代码文件" in _EXECUTION_BODY
    assert "交回代码生成环节修复" in _EXECUTION_BODY


def test_ac_s7_46_tool_description_says_not_for_writing_code() -> None:
    """AC-S7-46：工具说明补"本工具不用于写代码"，且**切目录表述仍在**。

    ⚠ `cd（限工作区内）` **不得顺手删**：工具层确实支持它，硬拦 `cd` 已被架构明确
    否决（部分仓库依赖以仓库根为工作目录的相对资源路径，硬拦会打死这类复现）。
    A 的收敛在计划侧做，**硬防线只给 C**。
    """
    assert "本工具不用于写代码" in _EXECUTION_BODY
    assert "cd（限工作区内）" in _EXECUTION_BODY, (
        "切目录表述被顺手删了——架构已明确否决硬拦 cd（A 只走软防线）"
    )


def test_q_s7_22_tool_hint_states_shape_not_a_number() -> None:
    """Q-S7-22：提示词写**形态表述**，刻意**不写阈值数字**。

    写数字会造出"双源真相"：正文里的 120 与 `_INLINE_PY_MAX_CHARS` 没有任何机械
    链条绑定，下一个调值的人改了常量、提示词就无声说谎，而所有守门全绿——这正是
    R-S7-41 那道恒真断言换了层皮。且 LLM 数不准自己正要生成的载荷有多少字符，
    给一个它算不出来的预算等于没给。
    """
    assert "行内 -c 只用于简短探针" in _EXECUTION_BODY
    assert "超长载荷会被直接拒绝" in _EXECUTION_BODY
    assert str(plan_checks._INLINE_PY_MAX_CHARS) not in _EXECUTION_BODY, (
        "提示词里出现了阈值数字——它与 _INLINE_PY_MAX_CHARS 无绑定，必然漂移成假话"
    )


def test_ac_s7_46_execution_new_text_has_zero_interpolation() -> None:
    """AC-S7-46：本批**新增**的两处文案零插值（无花括号 / 无论文标识 / 无绝对路径）。

    ⚠ 只能扫新增段落，不能扫整个主体：主体的【输出要求】里本来就有一段 JSON 结构
    示例（合法的字面花括号，`_EXECUTION_SYSTEM_PROMPT_BODY` 不是 f-string）。
    整体扫会把既有内容误判成插值。跨任务字节一致由
    `tests/test_sprint5_t14_execution_prompt.py::test_cp_1_4_1_system_message_byte_identical_across_tasks`
    与该文件的哈希基线共同守。
    """
    new_segments = [
        _EXECUTION_BODY.split("计划外命令（调试/兜底）不带该参数即可。")[1].split("\n")[0],
        [line for line in _EXECUTION_BODY.splitlines() if line.startswith("4. 命令失败时")][0],
    ]
    for segment in new_segments:
        assert segment.strip(), "新增段落定位失败（提示词被改写后本用例须同步）"
        assert "{" not in segment and "}" not in segment, f"新增文案含花括号：{segment!r}"
        assert "arxiv" not in segment.lower(), f"新增文案含论文标识：{segment!r}"
        assert "/data" not in segment, f"新增文案含绝对路径：{segment!r}"


# =========================================================================== #
# C2：共用纯谓词 is_inline_code_write（Q-S7-21）
# =========================================================================== #


@pytest.mark.parametrize("length,payload", CORPUS_MUST_HIT, ids=lambda v: str(v)[:24])
def test_cp_6_5_1_predicate_hits_real_corpus_writes(length: int, payload: str) -> None:
    """CP-6.5-1 正向：真实语料里"在命令行里写码"的那几条必判 True。

    ⚠ 127 / 144 那两条正是 §12.1 认定的缺陷根因（计划自己写下的占位符步骤）。
    **漏放它们 = 约束 C 对它被创造出来要解决的那个实例完全失效。**

    ⚠ 架构 §19.7 定稿的「必须命中」是 **5 条**：127 / 144 / 183 / **510** / **1304**。
    本处 `CORPUS_MUST_HIT` 只落了前 3 条（本 docstring 先前自称"5 条"，与参数化实际
    不符，2026-07-31 独立验收指出）。缺的两条（`round_1.log:106` / `:92` 的真实实现
    载荷）逐字落在 `tests/test_sprint7_s710_gap_audit.py::OMITTED_MUST_HIT`，并由
    同文件 `::test_q_s7_23_must_hit_ground_truth_is_complete` 做**集合相等**守门
    （禁 `issubset`）⇒ ground truth 在 `tests/` 下完整且不可被悄悄裁剪。
    **此处刻意不复制那两条超长字面量**：同一份逐字语料存两份必然漂移，
    而独立那份正是"交付件语料被改写"时唯一还能报警的东西。
    """
    assert len(payload) == length, "语料被改写了——必须逐字抄自日志"
    assert plan_checks.is_inline_code_write(_wrap_inline(payload)) is True


@pytest.mark.parametrize("length,payload", CORPUS_MUST_PASS, ids=lambda v: str(v)[:24])
def test_cp_6_5_1_predicate_passes_real_corpus_probes(length: int, payload: str) -> None:
    """CP-6.5-1 负向：真实语料里 3 条真·短探针必判 False（零误伤）。"""
    assert len(payload) == length, "语料被改写了——必须逐字抄自日志"
    assert plan_checks.is_inline_code_write(_wrap_inline(payload)) is False


@pytest.mark.parametrize(
    "length,payload", CORPUS_EXPECTED_HIT_RECOVERABLE, ids=lambda v: str(v)[:24]
)
def test_cp_6_5_1_predicate_hits_recoverable_long_probe(length: int, payload: str) -> None:
    """CP-6.5-1 重标项：181 那条长探针**预期命中**，登记为可恢复而非误伤（Q-S7-21）。

    它在可行窗口 [98,126] 内任何阈值下都会被拒 ⇒ 一个对阈值不敏感的量，逻辑上
    不可能是"调阈值"的触发条件。缓解走拒绝文案指路（拆短 / 先落成脚本），
    真跑时按 R-S7-58 计数观测。
    """
    assert plan_checks.is_inline_code_write(_wrap_inline(payload)) is True


def test_cp_6_5_1_predicate_never_raises_on_degenerate_input() -> None:
    """CP-6.5-1：空串 / 非 `python -c` 形态 / argv 缺载荷 / 非字符串 —— 一律 False 不抛。"""
    degenerate: List[Any] = [
        "", "   ", None, 123, [], {},
        "python", "python -c", "python run_repro_basics.py",
        "pip install numpy", "echo 'unclosed quote",
        _wrap_inline("short probe"),
    ]
    for value in degenerate:
        assert plan_checks.is_inline_code_write(value) is False, f"{value!r} 不该命中"


def test_cp_6_5_2_predicate_splits_top_level_before_judging() -> None:
    """CP-6.5-2：**先按顶层 `&&` / `;` 拆分再逐条判**——漏了这条谓词形同虚设。

    `pip install x && python -c "<超长载荷>"` 整条串首 token 是 `pip`，不拆分就永远
    判不到后半段。引号内的 `&&` 由 shlex 语义保证不误拆。
    """
    long_payload = "x" * (plan_checks._INLINE_PY_MAX_CHARS + 1)
    assert plan_checks.is_inline_code_write(f'pip install numpy && python -c "{long_payload}"')
    assert plan_checks.is_inline_code_write(f'cd sub ; python -c "{long_payload}"')
    # 引号内的 && 只是普通 token，不产生新子命令（短载荷 ⇒ 仍 False）。
    assert plan_checks.is_inline_code_write('python -c "a && b"') is False


def test_cp_6_5_2_predicate_recognizes_absolute_interpreter_paths() -> None:
    """CP-6.5-2：venv 绝对路径解释器与带版本号的 python 同样被识别。

    执行期日志里的 argv[0] 正是 `<work_dir>/.venv/bin/python`（`_rewrite_interpreter`
    改写后的形态），只认裸 `python` 会在执行侧整个漏判。
    """
    long_payload = "x" * (plan_checks._INLINE_PY_MAX_CHARS + 1)
    for exe in ("python", "python3", "python3.11", "/a/b/.venv/bin/python", "py"):
        assert plan_checks.is_inline_code_write(f'{exe} -c "{long_payload}"') is True, exe
    # 非解释器不误判（哪怕载荷超长）。
    assert plan_checks.is_inline_code_write(f'node -c "{long_payload}"') is False


def test_q_s7_21_threshold_is_inside_the_calibrated_window() -> None:
    """Q-S7-21：阈值必须落在真实语料标定出的可行窗口 [98, 126] 内。

    这道断言是**阈值的机械护栏**：把语料实测的两个端点钉死在测试里，日后任何人
    调值只要出窗就当场红，逼他回去重跑标定，而不是拍一个新数字。
    R-S7-48 原回退列写的"上调 200 + 补 OR 分支"**已由 Q-S7-24 作废**——T=200 会在
    [120,200] 区间给形态 2 开门，而 183 那条正是这扇门里真实存在的样本。
    """
    max_must_pass = max(length for length, _ in CORPUS_MUST_PASS)
    min_must_hit = min(length for length, _ in CORPUS_MUST_HIT)
    assert max_must_pass == 98 and min_must_hit == 127, "语料端点被改动，标定结论作废"
    assert max_must_pass <= plan_checks._INLINE_PY_MAX_CHARS < min_must_hit, (
        f"_INLINE_PY_MAX_CHARS={plan_checks._INLINE_PY_MAX_CHARS} 已出可行窗口 "
        f"[{max_must_pass}, {min_must_hit - 1}]——请重跑真实语料标定，不要拍数字"
    )


def test_q_s7_21_single_rule_no_verb_or_suffix_enumeration() -> None:
    """PRD §12.3 非目标 5 + dev-plan §41.3 红线末条：**单一规则**，禁动词 / 后缀枚举。

    机制化守门（不是靠人自觉）：谓词对"含写文件动词且目标 .py、但载荷很短"的命令
    必须判 False。一旦有人偷偷补了 OR 动词分支，本断言当场红。
    """
    short_write = "open('x.py','w').write('pass')"
    assert len(short_write) <= plan_checks._INLINE_PY_MAX_CHARS
    assert plan_checks.is_inline_code_write(_wrap_inline(short_write)) is False, (
        "谓词命中了短写码 ⇒ 说明混入了动词 / 后缀枚举分支，违反单一规则红线。"
        "该形态是 R-S7-57 已登记且被接受的残留，由约束 B + W5 + 人在回路兜。"
    )


# =========================================================================== #
# C3：工具层硬拦截（AC-S7-47 ★命门）
# =========================================================================== #


@pytest.fixture()
def sandbox_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Dict[str, Any]:
    """搭一个**能真正起子进程**的最小沙箱：workspace 边界 + venv python 符号链接。

    真起子进程是刻意的：CP-6.6-1 要断的是"磁盘上那个文件**没被创建**"，用 mock
    runner 断这一条等于什么都没证明（mock 本来就不会写盘）。
    """
    ws = tmp_path / "workspace"
    work = ws / "code"
    venv_bin = work / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    python_link = venv_bin / "python"
    python_link.symlink_to(sys.executable)
    monkeypatch.setattr(local_venv, "WORKSPACE_DIR", ws)

    collector = execution_module._SandboxRunCollector()
    tool = execution_module.make_run_in_sandbox_tool(
        str(work), collector, None, {"python_exe": str(python_link)},
    )
    return {"work": work, "tool": tool, "collector": collector}


@pytest.fixture()
def mock_runner_tool(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Dict[str, Any]:
    """工具层夹具（runner 换成替身）：只观测"拒 / 不拒"与"是否真进了 runner"。

    刻意**不起真子进程**——磁盘副作用由 `sandbox_workspace` 那组真跑用例负责，
    这里要的是"能被别的用例当积木调用"的轻量工具层入口（F2 / F3 两处假绿的解药）。
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


def _tool_rejects(harness: Dict[str, Any], command: str) -> bool:
    """跑一条命令，返回工具层"是否真的拦住了它"。

    ⚠ 判据是**三件同时成立**：返回 `tool_error is True` **且**命令没进 runner
    **且**两个台账容器都还空着。只看 `tool_error` 不够——拦截若下沉到执行之后，
    命令会先跑完再报错，那正是这条硬防线自己制造 R-S7-49 假绿的方式。
    """
    import json as _json

    parsed = _json.loads(harness["tool"].invoke({"command": command}))
    return bool(
        parsed.get("tool_error") is True
        and harness["calls"] == []
        and harness["collector"].run_results == []
        and harness["collector"].step_ledger == []
    )


def test_ac_s7_47_harness_can_really_write_files(sandbox_workspace: Dict[str, Any]) -> None:
    """★ 阳性对照（防"文件没被创建"这条断言空转）：**短**写文件命令确实会落盘。

    没有这一条，CP-6.6-1 的"文件未被创建"可能只是因为夹具根本跑不动子进程——
    那是 S7-06「扫 0 条却 passed」同款假绿。先证明这套夹具真能写盘，
    再去证明拦截让它写不成。
    """
    work: Path = sandbox_workspace["work"]
    short_write = "open('probe_marker.txt','w').write('x')"
    assert len(short_write) <= plan_checks._INLINE_PY_MAX_CHARS, "对照命令必须在阈值之下"
    raw = sandbox_workspace["tool"].invoke({"command": _wrap_inline(short_write)})
    import json as _json

    parsed = _json.loads(raw)
    assert parsed.get("tool_error") is not True, f"阳性对照被误拒：{parsed}"
    assert parsed["exit_code"] == 0, parsed
    assert (work / "probe_marker.txt").exists(), "夹具跑不动子进程 —— CP-6.6-1 会空转"


def test_cp_6_6_1_original_placeholder_write_is_rejected_and_never_lands(
    sandbox_workspace: Dict[str, Any],
) -> None:
    """CP-6.6-1 ★命门正向：喂 `round_0.log:121` 那条**原命令** → 三者缺一不可。

    ①返回 `exit_code == -1` 且 `tool_error is True`（结构化错误，不炸子图）；
    ②磁盘上 `run_repro_basics.py` **未被创建**（真起子进程才证得了）；
    ③返回文案**明确指路**（误伤可恢复，防 agent 空转，R-S7-58）。
    """
    import json as _json

    work: Path = sandbox_workspace["work"]
    raw = sandbox_workspace["tool"].invoke({"command": ORIGINAL_PLACEHOLDER_WRITE_COMMAND})
    parsed = _json.loads(raw)  # 单引号 repr 在此必炸（BUG-S1-02 自查）

    assert parsed["tool_error"] is True, parsed
    assert parsed["exit_code"] == -1, parsed
    assert not (work / "run_repro_basics.py").exists(), (
        "拦截返回了错误、文件却真被写出来了 —— 拦截点位置错了（必须早于实际执行）"
    )
    assert "本工具不用于写代码" in parsed["error"]
    assert "交回代码生成环节" in parsed["error"], "拒绝文案须说清后续怎么走"
    assert "拆得更短" in parsed["error"] or "落成脚本" in parsed["error"], (
        "拒绝文案须给出可执行的恢复动作，否则 agent 只能空转（R-S7-58）"
    )


def test_cp_6_6_3_rejected_command_pollutes_no_ledger(
    sandbox_workspace: Dict[str, Any],
) -> None:
    """CP-6.6-3：被拒命令**不进 run_results、不进 step_ledger**。

    ⚠ 这是拦截点位置的核心验收：早退必须在 `_resolve_python_exe()` 之后、
    `_run_step_subcommands` 之前 —— 放错位置，被拒命令会进台账，从而
    **污染 exit_ok、被步骤对账当成"完成"**，这条硬防线会自己制造 R-S7-49 那类假绿。
    """
    collector = sandbox_workspace["collector"]
    assert collector.run_results == [] and collector.step_ledger == []
    sandbox_workspace["tool"].invoke(
        {"command": ORIGINAL_PLACEHOLDER_WRITE_COMMAND, "step_index": 7}
    )
    assert collector.run_results == [], "被拒命令进了 run_results ⇒ 会污染 exit_ok"
    assert collector.step_ledger == [], "被拒命令进了 step_ledger ⇒ 会被对账当成'完成'"


def test_cp_6_6_2_legal_probes_and_script_runs_are_not_blocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CP-6.6-2 ★命门负向：**5 条**合法短探针 + "跑一个既有脚本" → 正常执行不误伤。

    「跑一个既有脚本」是约束 C 产品意义的核心：判定对象是命令串本身、不是文件系统
    副作用 ⇒ 脚本运行时写出多少结果文件和图**永远合规，零误伤正常复现**。

    ⚠ 探针条数：AC-S7-47② 原文要求 **5 条**，而 Q-S7-21 重标后语料里的真探针只剩
    3 条（183 那条被重标为形态 2、181 那条重标为"预期命中"）。正确做法是**另补 2 条
    短探针补足**，不是把 AC 的数字降到 4 —— AC 原文并未把这 5 条绑定到语料那几行。
    本用例先前只喂 3 条语料探针 + 1 条脚本运行（共 4 条），2026-07-31 独立验收指出
    该缺口后补齐。
    """
    import json as _json

    ws = tmp_path / "workspace"
    work = ws / "code"
    work.mkdir(parents=True)
    monkeypatch.setattr(local_venv, "WORKSPACE_DIR", ws)

    calls: List[List[str]] = []

    def _fake_run(python_exe: str, command: List[str], work_dir: str, *a: Any, **k: Any):
        calls.append(list(command))
        return local_venv.SandboxRunResult(
            exit_code=0, stdout="ok", stderr="", duration_seconds=0.1,
            timed_out=False, output_truncated=False, command=list(command),
        )

    monkeypatch.setattr(execution_module, "run_in_venv", _fake_run)
    collector = execution_module._SandboxRunCollector()
    tool = execution_module.make_run_in_sandbox_tool(
        str(work), collector, None, {"python_exe": str(work / ".venv" / "bin" / "python")},
    )

    probes = [_wrap_inline(payload) for _, payload in CORPUS_MUST_PASS]
    # 补足 AC-S7-47② 要求的 5 条（语料重标后真探针只剩 3 条，缺的该补不该减）。
    probes.append(_wrap_inline("import sys; print(sys.version)"))
    probes.append(_wrap_inline("import numpy; print(numpy.__version__)"))
    assert len(probes) == 5, "AC-S7-47② 要求 5 条合法探针"
    commands = list(probes)
    # 「跑一个既有脚本」——它写多少产物都与本拦截无关。
    commands.append("python run_repro_basics.py --dataset digits")
    for cmd in commands:
        parsed = _json.loads(tool.invoke({"command": cmd}))
        assert parsed.get("tool_error") is not True, f"合法命令被误拒：{cmd}\n{parsed}"
        assert parsed["exit_code"] == 0

    assert len(calls) == len(commands), "有命令没被真正执行"
    assert len(collector.run_results) == len(commands)


def test_cp_6_6_4_compound_command_is_rejected_whole(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CP-6.6-4：复合命令夹带超长行内载荷时**整条被拒**，前半段也不执行。

    早退在拆分执行之前 ⇒ `pip install` 一次都没跑。若前半段被执行，说明拦截被下沉
    到了逐子命令层，那会留下"装了一半的环境 + 半条被拒的命令"这种脏状态。
    """
    import json as _json

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

    long_payload = "x" * (plan_checks._INLINE_PY_MAX_CHARS + 1)
    parsed = _json.loads(
        tool.invoke({"command": f'pip install numpy && python -c "{long_payload}"'})
    )
    assert parsed["tool_error"] is True
    assert calls == [], "前半段 pip install 被执行了 —— 早退点下沉到了子命令层"
    assert collector.run_results == [] and collector.step_ledger == []


def test_cp_6_6_6_rejection_logs_warning_with_masked_command(
    sandbox_workspace: Dict[str, Any], caplog: pytest.LogCaptureFixture,
) -> None:
    """CP-6.6-6：拒绝路径必须打 WARNING 且命令串**已脱敏 + 已截断**。

    已知 bug 模式 #3：禁止静默吞错。BUG-S1-02 整整两次诊断才定位到根因，就因为
    关键分支没日志。
    """
    from core import secrets_store

    secret = "ghp_s710_supersecrettoken"
    secrets_store._SENSITIVE_VALUES.add(secret)
    try:
        payload = f"import os; token='{secret}'; " + "y=1; " * 40
        assert len(payload) > plan_checks._INLINE_PY_MAX_CHARS
        with caplog.at_level(logging.WARNING, logger=execution_module.logger.name):
            sandbox_workspace["tool"].invoke({"command": _wrap_inline(payload)})
    finally:
        secrets_store._SENSITIVE_VALUES.discard(secret)

    hits = [r for r in caplog.records if "拒绝内联写码命令" in r.getMessage()]
    assert hits, "拒绝路径没打 WARNING 日志（禁止静默吞错）"
    message = hits[0].getMessage()
    assert secret not in message, "日志里泄漏了敏感值 —— 必须过 mask_value"
    assert len(message) < 400, "日志回显了超长载荷全文 —— 必须截断"


def test_cp_6_6_7_execution_prompt_hash_untouched_by_tool_layer_change() -> None:
    """CP-6.6-7：工具层拦截只动函数体，**不得连带改 prompt 主体**。

    与 `tests/test_sprint5_t14_execution_prompt.py` 的字节基线互为双保险：
    那边锁哈希，这边锁"改动没有溢出到冻结区"。

    ⚠ S7-11 / T-S7-7-4 更新基线：`f82f3938cf31f882` → `c73e1e6e3cfc1280`（1698 → 1979
    字符）。**本次是刻意改冻结区**（修法 B：`step_index` 声明升为必须 + 纪律 4/5 收窄
    + 新增"修复回合全量重跑"纪律），走的是 dev-plan §48.1 的哈希三件套。**改动本身
    没有溢出到 S7-10 的关注面**：AC-S7-46 点名保留的三句仍在、点名禁止的"修正相对
    路径"仍不存在（下方 T-6-4 的正负向用例全绿即为证）。
    """
    import hashlib

    actual = hashlib.sha256(_EXECUTION_BODY.encode("utf-8")).hexdigest()[:16]
    assert actual == "c73e1e6e3cfc1280", (
        f"execution 冻结区又变了（{actual}）—— 改冻结区必须走哈希三件套"
        "（重算写死 + dev-plan §48.1 留档 + 验红）"
    )


def test_cp_6_6_6_cd_resolution_is_untouched() -> None:
    """CP-6.6-6：`_resolve_cd` 一字未动（Q-S7-19 明确否决硬拦 `cd` 进仓库）。

    误伤面真实存在：部分仓库依赖以仓库根为工作目录的相对资源路径（配置 / 数据软链），
    硬拦会**打死这类复现**。A 只走软防线，硬防线只给 C。
    """
    inside = execution_module._resolve_cd("subdir", str(local_venv.WORKSPACE_DIR / "code"))
    assert inside.endswith("subdir"), "cd 进工作区内的子目录仍应放行"
    repo_like = str(local_venv.WORKSPACE_DIR / "repos" / "some__repo")
    assert execution_module._resolve_cd(repo_like, str(local_venv.WORKSPACE_DIR)) == str(
        Path(repo_like).resolve()
    ), "cd 进参考仓库目录**没有被硬拦**（这是架构裁决，不是遗漏）"


# =========================================================================== #
# W：计划期确定性告警 W4 / W5（AC-S7-48）
# =========================================================================== #


def _plan(steps: Sequence[Any]) -> Dict[str, Any]:
    """构造一份"只会触发本批新增告警"的干净计划（W1/W2/W3 全部不触发）。"""
    return {
        "data_preparation": ["下载 digits 数据集"],
        "execution_steps": list(steps),
        "expected_results": [{"description": "指标应接近论文量级", "trend": None}],
    }


_RESOURCE_WITH_REPO: Dict[str, Any] = {
    "selected_repo": {"local_path": "/data/proj/workspace/repos/lmcinnes__umap"},
    "external_resources": [{"type": "dataset", "name": "digits"}],
}


def _rules(warnings: List[Dict[str, str]]) -> List[str]:
    return [w["rule"] for w in warnings]


def test_cp_6_5_3_w4_fires_on_cd_into_selected_repo() -> None:
    """CP-6.5-3 W4 正向：步骤切进**选中的那个**参考仓库目录 → 必产 W4。"""
    plan = _plan([
        {"step_name": "进仓库", "command": "cd /data/proj/workspace/repos/lmcinnes__umap && python -m pip install -e ."},
        {"step_name": "跑实验", "command": "python run_repro_basics.py"},
    ])
    assert "W4" in _rules(plan_checks.check_plan(plan, _RESOURCE_WITH_REPO))


def test_cp_6_5_3_w4_fires_on_repos_marker_without_resource_info() -> None:
    """CP-6.5-3 W4：`resource_info` 里没有仓库信息时，靠路径标记仍能识别。

    真跑里 `resource_info` 未必总带 `local_path`（重规划 / 切换仓库路径上尤其如此），
    只认精确路径会在这些场合整个失效。
    """
    plan = _plan([{"command": "cd ../repos/lmcinnes__umap ; python setup.py build"}])
    assert "W4" in _rules(plan_checks.check_plan(plan, {}))


def test_cp_6_5_3_w4_silent_on_clean_relative_plan() -> None:
    """CP-6.5-3 W4 负向：纯相对路径 + 以路径参数装仓库的干净计划 → **必不产 W4**。

    这正是约束 A 期望的计划形态：仓库只作路径参数出现，不切进去。
    """
    plan = _plan([
        {"command": "pip install -e /data/proj/workspace/repos/lmcinnes__umap"},
        {"command": "python run_repro_basics.py --dataset digits"},
        {"command": "cd outputs"},  # 切进代码目录内的子目录不算违规
    ])
    assert "W4" not in _rules(plan_checks.check_plan(plan, _RESOURCE_WITH_REPO))


def test_cp_6_5_3_w4_survives_missing_resource_info() -> None:
    """CP-6.5-3 W4：`resource_info` 为 {} / `selected_repo` 为 None / 结构异常 → 不抛异常。"""
    plan = _plan([{"command": "python run.py"}])
    for resource in ({}, {"selected_repo": None}, {"selected_repo": "not-a-dict"},
                     {"selected_repo": {"local_path": None}}, {"selected_repo": {}}):
        assert isinstance(plan_checks.check_plan(plan, resource), list)


def test_cp_6_5_4_w5_fires_on_real_corpus_command() -> None:
    """CP-6.5-4 W5 正向：喂 `round_0.log:121` 那条真实命令 → 必产 W5。"""
    plan = _plan([
        {"step_name": "编写首轮复现实验脚本", "command": ORIGINAL_PLACEHOLDER_WRITE_COMMAND},
        {"step_name": "运行", "command": "python run_repro_basics.py"},
    ])
    assert "W5" in _rules(plan_checks.check_plan(plan, _RESOURCE_WITH_REPO))


def test_cp_6_5_4_w5_silent_on_script_run() -> None:
    """CP-6.5-4 W5 负向：`python run_repro_basics.py` → 必不产 W5。"""
    plan = _plan([{"command": "python run_repro_basics.py --dataset digits"}])
    assert "W5" not in _rules(plan_checks.check_plan(plan, _RESOURCE_WITH_REPO))


def test_cp_6_5_4_w5_shares_one_predicate_with_tool_layer(
    mock_runner_tool: Dict[str, Any],
) -> None:
    """CP-6.5-4：W5 与工具层硬拦截**共用同一条谓词**（一处定义、两处调用）。

    机制化守门：逐条比对**计划期 W5 是否触发**与**执行期是否被真的拒掉**，
    三侧（纯谓词 / W5 / 工具层）必须完全一致。一旦有人在任一侧另写一套判定、
    或把某一侧摘掉，本断言当场红——同一条不变量在计划期与执行期各查一次，
    **不是造两套机制**（Q-S7-19「一处定义两处调用」）。

    ⚠ 本用例原来两侧都在 `core/plan_checks.py` 内（`is_inline_code_write` vs W5，
    而 W5 的实现本就是直接调那个函数），**一次都没碰工具层**：2026-07-31 独立验收
    实测把工具层改成恒不拦，它照样绿 ⇒ 名字承诺的"与工具层共用"从未被验证过。
    现补上工具层这一侧（`_tool_rejects` 真调 `run_in_sandbox`），名实相符。
    """
    samples = [payload for _, payload in CORPUS_MUST_HIT + CORPUS_MUST_PASS
               + CORPUS_EXPECTED_HIT_RECOVERABLE]
    for payload in samples:
        command = _wrap_inline(payload)
        via_predicate = plan_checks.is_inline_code_write(command)
        via_w5 = "W5" in _rules(plan_checks.check_plan(_plan([{"command": command}]), {}))
        assert via_predicate == via_w5, f"W5 与谓词判定分叉：{payload[:40]!r}"
        # 执行期这一侧：被拒 ⟺ 计划期命中。每条命令一套干净台账，避免互相污染。
        via_tool = _tool_rejects(mock_runner_tool, command)
        assert via_tool == via_w5, (
            f"计划期 W5={via_w5} 与执行期拦截={via_tool} 分叉：{payload[:40]!r} —— "
            "同一条不变量必须一处定义两处调用，不得任一侧另写判定或被摘掉"
        )
        mock_runner_tool["calls"].clear()
        mock_runner_tool["collector"].run_results.clear()
        mock_runner_tool["collector"].step_ledger.clear()


def test_cp_6_5_5_check_plan_contract_unbroken() -> None:
    """CP-6.5-5：`check_plan` 契约一字不破 —— 签名 / 返回项结构 / 不阻断审批。"""
    import inspect

    sig = inspect.signature(plan_checks.check_plan)
    assert list(sig.parameters) == ["plan", "resource_info"], "check_plan 签名被改了"

    plan = _plan([{"command": ORIGINAL_PLACEHOLDER_WRITE_COMMAND}])
    warnings = plan_checks.check_plan(plan, _RESOURCE_WITH_REPO)
    for item in warnings:
        assert set(item) == {"rule", "message"}, f"返回项结构变了：{item}"
        assert isinstance(item["rule"], str) and isinstance(item["message"], str)

    source = inspect.getsource(plan_checks)
    assert "interrupt(" not in source, "plan_checks 不得引入新的中断门（AC-S6-12）"
    # ⚠ 括号位置是要害：`in plan_checks.__doc__ or ""` 的实际语义是 `(X) or ""`，
    # 读起来像"__doc__ 为 None 时兜底成空串"，实则 `__doc__` 一旦为 None 会抛
    # TypeError 而不是优雅失败（F8，2026-07-31 独立验收指出）。兜底要真兜住。
    assert "不阻断审批" in (plan_checks.__doc__ or ""), (
        "plan_checks 模块 docstring 丢了「不阻断审批」这条契约声明"
    )


def test_ac_s7_48_w4_w5_messages_are_plain_chinese() -> None:
    """AC-S7-49 的本地前哨：两条文案通俗中文、零内部标识符。

    真正的账目闭合守门在 `tests/test_s708_user_text_guard.py`（`EXPECTED_N` 精确 +2），
    这里只做"文案本身没写成技术黑话"的就近断言。
    """
    from tests.test_s708_user_text_guard import _all_hits

    for name in ("_W4_MESSAGE", "_W5_MESSAGE"):
        text = getattr(plan_checks, name)
        assert isinstance(text, str) and text.strip()
        assert _all_hits(text) == [], f"{name} 含内部术语：{_all_hits(text)}"
        for banned in ("cd ", "python -c", "code_output_dir", "execution_steps",
                       "plan_checks", "run_in_sandbox", "W4", "W5"):
            assert banned not in text, f"{name} 含内部标识 {banned!r}"


# =========================================================================== #
# R：参考仓库不接收复现代码与复现产物（AC-S7-50 / T-6-7 可复用 helper）
# =========================================================================== #

#: 构建残留白名单 —— `pip install -e <repo>` **必然**在仓库源码树里落这些东西。
#:
#: ⚠ 口径必须是「仓库不接收复现代码与复现产物」，**不是**「仓库只读」（R-S7-53）：
#: 约束 A 明确允许可编辑安装。本次真跑只见 3 条残留，是 umap 仓库的忽略规则恰好
#: 盖住了其余的——**是仓库特定的运气，不是系统性质**。写成 `git status` 为空，
#: 换一个忽略规则不全的仓库会直接假红。
BUILD_RESIDUE_SUFFIXES: Tuple[str, ...] = (".egg-info", ".eggs")
BUILD_RESIDUE_NAMES: Tuple[str, ...] = ("__pycache__", "build", ".pytest_cache")

#: 无论如何都不许出现的复现痕迹（"特别地"那一档）。
FORBIDDEN_NAME_MARKERS: Tuple[str, ...] = ("repro_outputs", "outputs", "summary.json")


def _is_build_residue(entry: str) -> bool:
    """该 untracked 条目是否属于可编辑安装的构建残留（白名单放行）。"""
    parts = [p for p in entry.strip().rstrip("/").split("/") if p]
    for part in parts:
        if part in BUILD_RESIDUE_NAMES:
            return True
        if any(part.endswith(suffix) for suffix in BUILD_RESIDUE_SUFFIXES):
            return True
    return False


def repo_cleanliness_violations(untracked: Sequence[str]) -> List[str]:
    """把 untracked 条目按 AC-S7-50 口径判违规，返回违规条目列表（空 = 干净）。

    T-6-7 落的**可复用 helper**，T-6-9 真跑验收直接调它。刻意做成"吃字符串列表"的
    纯函数：正负两向都能用构造用例验，不必真有个 git 仓库。
    """
    violations: List[str] = []
    for entry in untracked:
        item = entry.strip()
        if not item:
            continue
        name = item.rstrip("/").split("/")[-1]
        # 「特别地」一档：即使命中白名单也一律违规（防 outputs/ 藏在 build/ 里）。
        if any(marker in item for marker in FORBIDDEN_NAME_MARKERS):
            violations.append(item)
            continue
        if name.endswith(".py"):
            violations.append(item)
            continue
        if _is_build_residue(item):
            continue
        violations.append(item)
    return violations


def git_untracked_entries(repo_dir: Path) -> List[str]:
    """跑 `git status --porcelain` 取该仓库的 untracked 条目（供 T-6-9 真跑调用）。"""
    proc = subprocess.run(
        ["git", "status", "--porcelain"], cwd=str(repo_dir),
        capture_output=True, text=True, check=True,
    )
    return [line[3:] for line in proc.stdout.splitlines() if line.startswith("?? ")]


def test_cp_6_7_3_invariant_whitelist_lets_build_residue_through() -> None:
    """CP-6.7-3 负向：可编辑安装的构建残留**确实被放行**（否则换个仓库就假红）。"""
    assert repo_cleanliness_violations([
        "umap_learn.egg-info/", "umap/__pycache__/", "build/", ".eggs/",
        "umap/__pycache__/umap_.cpython-311.pyc",
    ]) == []


def test_cp_6_7_3_invariant_catches_reproduction_artifacts() -> None:
    """CP-6.7-3 正向：复现产物目录 / 复现入口脚本 / 指标汇总文件**一条都跑不掉**。

    三条正是本次真跑在共享克隆缓存里留下的那 3 条残留（dev-plan §48 P-21）。
    """
    residues = ["run_repro_basics.py", "eval_knn_on_embeddings.py", "repro_outputs/"]
    assert sorted(repo_cleanliness_violations(residues)) == sorted(residues)
    # 藏进白名单目录里也不放过。
    assert repo_cleanliness_violations(["build/repro_outputs/metrics/summary.json"])
    assert repo_cleanliness_violations(["outputs/summary.json"])


def test_cp_6_7_2_shared_clone_cache_is_clean_now() -> None:
    """CP-6.7-2：磁盘上那个共享克隆缓存现在是干净的（验收前置已完成）。

    不清理则 AC-S7-50 在第一次真跑**之前**就已非空、验收根本无法成立。
    仓库不在磁盘上时跳过（本用例是环境事实核对，不是逻辑断言）。
    """
    repo = Path(__file__).resolve().parents[1] / "workspace" / "repos" / "lmcinnes__umap"
    if not (repo / ".git").exists():
        pytest.skip("共享克隆缓存不在本机磁盘上（CI / 干净检出环境）")
    violations = repo_cleanliness_violations(git_untracked_entries(repo))
    assert violations == [], (
        f"参考仓库里仍有复现残留：{violations}\n"
        "参考仓库是跨论文共享缓存，下一篇论文选中它就会读到上一篇的残留。"
    )


# =========================================================================== #
# X：边界不放宽、无扰动（AC-S7-53）
# =========================================================================== #


def test_ac_s7_53_coding_write_boundary_untouched() -> None:
    """AC-S7-53①：编码环节的写入隔离**一字未动**（Maria 硬约束）。

    它拦住的正是"往**跨论文共享**的克隆缓存里写"——放开它等于允许 A 论文的复现
    代码污染 B 论文将要读到的公共缓存。本批**不得**以"让编码环节直接写进仓库"作为修法。
    """
    code_fs = importlib.import_module("core.tools.code_fs_tools")
    coding = importlib.import_module("core.nodes.coding")
    assert hasattr(code_fs, "_is_within_base"), "越界判定函数被删/改名了"
    assert code_fs._is_within_base(Path("/a/b/c.py"), Path("/a/b")) is True
    assert code_fs._is_within_base(Path("/a/x/c.py"), Path("/a/b")) is False
    assert code_fs._is_within_base(Path("/a/b/../x/c.py"), Path("/a/b")) is False
    import inspect

    assert "base_dir=code_dir" in inspect.getsource(coding), (
        "coding 的写文件工具不再锚定在 code_output_dir 上 —— 隔离被放宽了"
    )


def test_ac_s7_53_no_repo_path_is_undisturbed() -> None:
    """AC-S7-53②（反向守门，防"改过头"）：无仓库路径的复现**行为与本批之前一致**。

    从零实现的计划里既不会 `cd` 进仓库、也不会内联写码 ⇒ 本批新增的 W4/W5 一条都
    不该冒出来，警示集合必须与本批之前逐字相同（只有 W1/W2/W3 的既有判定）。
    """
    from_scratch_plan = {
        "data_preparation": ["下载 digits 数据集"],
        "code_strategy": "from_scratch",
        "execution_steps": [
            {"step_name": "准备数据", "command": "python prepare_data.py"},
            {"step_name": "跑实验", "command": "python train.py --epochs 1"},
            {"step_name": "评测", "command": "python evaluate.py"},
        ],
        "expected_results": [{"description": "loss 应收敛", "trend": None}],
    }
    warnings = plan_checks.check_plan(from_scratch_plan, {})
    # 无仓库 + 有 data_preparation + 无 dataset 资源 ⇒ 本批之前就恒产 W3，且仅 W3。
    assert _rules(warnings) == ["W3"], (
        f"无仓库路径的警示集合被本批改动扰动了：{_rules(warnings)}"
    )


def test_ac_s7_53_plan_structure_and_context_untouched() -> None:
    """AC-S7-53③：计划结构 / 上下文组装 **一字不动**（本批不给计划加字段）。"""
    import inspect

    schema = planning_module.REPRODUCTION_PLAN_SCHEMA
    assert set(schema["required"]) == {"plan_summary", "code_strategy", "deliverables"}
    assert "code_output_dir" not in schema["properties"], "本批不得给计划加字段"
    params = list(inspect.signature(planning_module._format_planning_context).parameters)
    assert len(params) == 6, f"_format_planning_context 形参数被改了：{params}"


def test_ac_s7_52_all_three_constraints_landed_together(
    mock_runner_tool: Dict[str, Any],
) -> None:
    """AC-S7-52 连坐交付：A / B / C 三条**必须在同一次收口中全部为绿**。

    这是本批唯一的流程级验收。理由（§12.1.3 的直接推论）：本次真跑**恰恰是执行环节
    违规写代码才救回一个数字**。只上 C 不修 A/B ⇒ 计划仍是占位符计划、执行又不许
    补救 ⇒ **结果直接归零，比现状更糟**。只修 A/B 不上 C ⇒ 执行仍可在命令行里现编
    代码把落点绕回去。**三条一起上，或者整批延后，没有第三条路。**

    ⚠ C 臂原为**源码子串检查**（`"is_inline_code_write(command)" in getsource(...)`），
    2026-07-31 独立验收实测：把工具层改成 `if False and is_inline_code_write(command)`
    这样的**死代码**后本用例仍绿 ⇒ 它只能证"这段字符出现在源码里"，证不到"C 生效"。
    现改为**真调工具层跑一条超阈值命令**、断它被拦下且没进 runner / 台账 ——
    死代码当场红。（"缺任一条当场红"这个定位不许再被源码扫描偷换。）
    """
    landed = {
        "A-删授权": "`cd <子目录>`" not in _PLANNING_BODY,
        "A-正面口径": "相对代码目录" in _PLANNING_BODY and "不要进入仓库目录" in _PLANNING_BODY,
        "B-禁占位步骤": "步骤形态本身" in _PLANNING_BODY,
        "C-提示词收窄": "不得写入或修改任何代码文件" in _EXECUTION_BODY
        and "修正相对路径" not in _EXECUTION_BODY,
        "C-工具层硬拦截": _tool_rejects(
            mock_runner_tool,
            _wrap_inline("x" * (plan_checks._INLINE_PY_MAX_CHARS + 1)),
        ),
    }
    missing = [k for k, ok in landed.items() if not ok]
    assert not missing, (
        f"三条约束未同批落地，缺：{missing}。"
        "交付物中不得出现「C 已上、A/B 延后」或「A/B 已上、C 延后」的任何组合。"
    )
