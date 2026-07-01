"""sandbox/local_venv.py 真实执行 e2e —— 补「最后一跳」缺口。

背景：Sprint 3 coding → execution → reporting 段所有 e2e 都把 sandbox 三入口
（prepare_venv / run_in_venv / collect_artifacts）mock 掉了（见
tests/test_sprint3_c3.py::_patch_sandbox 与 tests/test_sprint3_e2e.py::FakeRunResult）。
即 local_venv.py 的**真实实现**（真建 venv、真 pip install、真 subprocess 跑脚本、
真收产物）从未在任何测试里被真实执行过。本文件用**零 mock 的真实执行**把这条链路补上。

约束与定位：
    - 真实执行（真 subprocess / 真 pip）：慢，但**不需凭证、不耗 deepxiv/LLM 配额**
      （sandbox 层纯本地基础设施，无 LLM / 无 deepxiv 依赖）。
    - 同时打 `e2e` + `sandbox_real` 两个 marker：`e2e` 使其被 `-m "not e2e"` 快速回归
      排除（不拖慢回归）；`sandbox_real` 用于 `-m sandbox_real` 显式真跑。
    - 所有 work_dir 落在 monkeypatch 后的受控 WORKSPACE_DIR（tmp_path 下，pytest 自动清理），
      **绝不污染真实 workspace**（沿用 tests/test_sprint3_b1.py 的 sandbox_workspace 范式）。
    - 不真跑 GPU 重训练：用 self-contained「迷你复现脚本」（import numpy 算个简单指标 +
      打印 <METRICS> 标签 + 写 result.json / metrics.csv），这是「随便用一个论文试验」的务实版。
    - pip install 真实联网装 numpy（复现任务常见依赖）。若环境无网络导致 pip 失败，
      依赖网络的用例会 fail（不伪造通过）；不依赖网络的用例（系统标准库脚本执行 /
      收产物 / 护栏 / 超时 / 截断）仍真跑覆盖。

运行：
    .venv/bin/pytest tests/test_sandbox_real_e2e.py -m sandbox_real -v -s
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from config import SANDBOX_OUTPUT_MAX_BYTES
from core.errors import SandboxCreationError
from sandbox import local_venv
from sandbox.local_venv import (
    SandboxPrepareResult,
    SandboxRunResult,
    collect_artifacts,
    prepare_venv,
    run_in_venv,
)

# 全文件真实执行：双 marker（e2e 排除于快速回归；sandbox_real 显式触发）。
pytestmark = [pytest.mark.e2e, pytest.mark.sandbox_real]


# ---------------------------------------------------------------------------
# fixtures：受控 WORKSPACE_DIR（tmp_path 下，绝不碰真实 workspace）
# ---------------------------------------------------------------------------


@pytest.fixture()
def sandbox_workspace(tmp_path, monkeypatch):
    """把 WORKSPACE_DIR patch 到 tmp_path 下的受控目录（沿用 b1 范式，pytest 自动清理）。

    返回 (workspace_dir, work_dir)；work_dir 是 workspace 下的合法子目录。
    """
    ws = tmp_path / "workspace"
    work = ws / "thread-real-e2e" / "code"
    work.mkdir(parents=True)
    monkeypatch.setattr(local_venv, "WORKSPACE_DIR", ws)
    return ws, work


# ---------------------------------------------------------------------------
# 迷你复现脚本（self-contained，真实可跑）
# ---------------------------------------------------------------------------

# 依赖 numpy 的迷你复现脚本：算个确定性「准确率」指标，打 <METRICS> 标签 + 写产物。
_MINI_REPRO_SCRIPT_NUMPY = r"""
import json
import csv
import numpy as np

# 确定性「迷你复现」：构造预测 vs 真值，算 accuracy（无随机，结果可重复）。
y_true = np.array([1, 0, 1, 1, 0, 1, 0, 0, 1, 1])
y_pred = np.array([1, 0, 1, 0, 0, 1, 0, 1, 1, 1])  # 8/10 命中
accuracy = float((y_true == y_pred).mean())

with open("result.json", "w") as f:
    json.dump({"accuracy": accuracy, "n": int(y_true.size)}, f)

with open("metrics.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["metric", "value"])
    w.writerow(["accuracy", accuracy])

print("mini reproduction done, numpy", np.__version__)
print('<METRICS>{"accuracy": %.2f}</METRICS>' % accuracy)
"""

# 纯标准库脚本（不依赖 numpy / 网络）：用于 collect / 护栏等不需联网的真实执行。
_MINI_REPRO_SCRIPT_STDLIB = r"""
import json, csv
acc = 0.95
with open("result.json", "w") as f:
    json.dump({"accuracy": acc}, f)
with open("metrics.csv", "w", newline="") as f:
    w = csv.writer(f); w.writerow(["metric", "value"]); w.writerow(["accuracy", acc])
print('<METRICS>{"accuracy": 0.95}</METRICS>')
"""


def _write_script(work: Path, name: str, body: str) -> Path:
    p = work / name
    p.write_text(body, encoding="utf-8")
    return p


# ===========================================================================
# 场景 1：prepare_venv 真建环境 + 真 pip 装 numpy（联网）
# ===========================================================================


def test_real_1_prepare_venv_creates_real_env_with_numpy(sandbox_workspace):
    ws, work = sandbox_workspace
    result = prepare_venv(str(work), requirements=["numpy"])

    assert isinstance(result, SandboxPrepareResult)
    assert result.success is True, f"prepare 应成功: error={result.error} log_tail={result.install_log[-500:]}"
    assert result.install_failed_packages == [], f"不应有装失败的包: {result.install_failed_packages}"

    # python_exe 真实存在且可执行。
    py = Path(result.python_exe)
    assert py.exists(), f"venv python 不存在: {py}"
    assert py.is_file()
    import os

    assert os.access(str(py), os.X_OK), "venv python 应可执行"

    # venv_dir / pip_exe 真实就位，且在 workspace 下。
    assert Path(result.venv_dir).exists()
    assert Path(result.venv_dir).is_relative_to(ws.resolve())
    assert Path(result.pip_exe).exists()

    # env_info 含 python_version + key_packages，且 numpy 真在 freeze 里。
    assert "python_version" in result.env_info
    assert "Python" in result.env_info["python_version"]
    assert "key_packages" in result.env_info
    assert "numpy" in result.env_info["key_packages"].lower(), (
        f"freeze 应含 numpy: {result.env_info['key_packages']}"
    )

    # 真在 venv 里 import numpy 成功（终极证明 venv + 装包真生效）。
    check = run_in_venv(
        result.python_exe,
        [result.python_exe, "-c", "import numpy; print(numpy.__version__)"],
        str(work),
    )
    assert check.exit_code == 0, f"venv 内 import numpy 失败: {check.stderr}"
    assert check.stdout.strip(), "应打印 numpy 版本"


# ===========================================================================
# 场景 2：run_in_venv 真跑脚本出指标（依赖场景1的真实 venv）
# ===========================================================================


def test_real_2_run_in_venv_executes_script_emits_metrics(sandbox_workspace):
    ws, work = sandbox_workspace
    prep = prepare_venv(str(work), requirements=["numpy"])
    assert prep.success is True, f"前置 venv 失败: {prep.error}"

    script = _write_script(work, "mini_repro.py", _MINI_REPRO_SCRIPT_NUMPY)
    run = run_in_venv(prep.python_exe, [prep.python_exe, str(script)], str(work))

    assert isinstance(run, SandboxRunResult)
    assert run.exit_code == 0, f"脚本应成功: stderr={run.stderr}"
    assert "<METRICS>" in run.stdout and "</METRICS>" in run.stdout, f"stdout 应含 METRICS 标记: {run.stdout!r}"
    assert '"accuracy": 0.80' in run.stdout, f"应打出 0.80 的 accuracy: {run.stdout!r}"
    assert run.duration_seconds > 0, "真实执行 duration 应 > 0"
    assert run.timed_out is False
    assert run.output_truncated is False
    # command 审计：列表形式回填。
    assert isinstance(run.command, list) and str(script) in run.command


# ===========================================================================
# 场景 3：collect_artifacts 真收产物（跳过 .venv、限定 workspace）
# ===========================================================================


def test_real_3_collect_artifacts_real_outputs_skip_venv(sandbox_workspace):
    ws, work = sandbox_workspace
    prep = prepare_venv(str(work))  # 不装包也能跑标准库脚本（避免本用例依赖网络）
    assert prep.success is True

    script = _write_script(work, "gen.py", _MINI_REPRO_SCRIPT_STDLIB)
    run = run_in_venv(prep.python_exe, [prep.python_exe, str(script)], str(work))
    assert run.exit_code == 0, f"产物生成脚本失败: {run.stderr}"

    artifacts = collect_artifacts(str(work))

    # 真收到 result.json / metrics.csv 的绝对路径。
    names = {Path(a).name for a in artifacts}
    assert "result.json" in names, f"未收到 result.json: {artifacts}"
    assert "metrics.csv" in names, f"未收到 metrics.csv: {artifacts}"

    # 每个产物：绝对路径 + 真实存在 + 在 workspace 下 + 不在 .venv 下。
    ws_resolved = ws.resolve()
    for a in artifacts:
        p = Path(a)
        assert p.is_absolute(), f"产物应为绝对路径: {a}"
        assert p.exists()
        assert p.resolve().is_relative_to(ws_resolved), f"产物越界 workspace: {a}"
        assert ".venv" not in p.parts, f"不应收 .venv 下的文件: {a}"

    # 反证：venv 里有大量 .py（如 pip 内部模块），但 collect *.txt 等不应混入 venv 文件。
    venv_artifacts = [a for a in collect_artifacts(str(work)) if ".venv" in Path(a).parts]
    assert venv_artifacts == [], f"collect 不应包含 venv 内文件: {venv_artifacts}"


# ===========================================================================
# 场景 4：端到端「迷你复现」串联（核心 —— 缺失的最后一跳）
# prepare_venv → run_in_venv → collect_artifacts 全真实链路
# ===========================================================================


def test_real_4_end_to_end_mini_reproduction_chain(sandbox_workspace):
    ws, work = sandbox_workspace

    # 1) 真建 venv + 真装 numpy。
    prep = prepare_venv(str(work), requirements=["numpy"])
    assert prep.success is True, f"prepare 失败: {prep.error}"
    assert prep.install_failed_packages == []

    # 2) 真跑迷你复现脚本。
    script = _write_script(work, "mini_repro.py", _MINI_REPRO_SCRIPT_NUMPY)
    run = run_in_venv(prep.python_exe, [prep.python_exe, str(script)], str(work))
    assert run.exit_code == 0, f"复现脚本执行失败: {run.stderr}"
    assert run.timed_out is False

    # 3) 从真实 stdout 抽出真指标（复用 execution 节点的解析路径同口径）。
    from core.nodes.execution import _extract_metrics_block

    metrics = _extract_metrics_block(run.stdout)
    assert metrics == {"accuracy": 0.8}, f"应从真实执行抽出真指标: {metrics}"

    # 4) 真收产物 + 校验产物内容（指标确实落盘）。
    artifacts = collect_artifacts(str(work))
    result_json = next((a for a in artifacts if Path(a).name == "result.json"), None)
    assert result_json is not None, f"未收到 result.json: {artifacts}"
    loaded = json.loads(Path(result_json).read_text())
    assert loaded["accuracy"] == 0.8
    assert loaded["n"] == 10

    # 全链路自洽：stdout 抽的指标 == 产物落盘的指标。
    assert metrics["accuracy"] == loaded["accuracy"]


# ===========================================================================
# 场景 5a：超时强杀 —— 脚本 sleep 超过 timeout（不依赖网络）
# ===========================================================================


def test_real_5a_timeout_kills_process(sandbox_workspace):
    ws, work = sandbox_workspace
    prep = prepare_venv(str(work))
    assert prep.success is True

    # 脚本 sleep 30s，但 timeout=2s → 应被强杀。
    sleep_script = _write_script(
        work, "sleeper.py", "import time\nprint('start', flush=True)\ntime.sleep(30)\nprint('should not reach')\n"
    )
    run = run_in_venv(prep.python_exe, [prep.python_exe, str(sleep_script)], str(work), timeout=2)

    assert run.timed_out is True, "应标记超时"
    assert run.duration_seconds < 10, f"应在 timeout 附近被杀，实际 {run.duration_seconds}s"
    assert run.duration_seconds >= 1.5, f"应至少跑到 timeout 才被杀: {run.duration_seconds}s"
    assert "should not reach" not in run.stdout, "超时后续代码不应执行"
    assert run.exit_code != 0, "被杀的进程 exit_code 应非 0"


# ===========================================================================
# 场景 5b：pip 装不存在的包 → 失败降级（不抛异常）
# ===========================================================================


def test_real_5b_pip_install_nonexistent_package_degrades(sandbox_workspace):
    ws, work = sandbox_workspace
    bogus = "this-package-surely-does-not-exist-xyzzy-20260629"
    result = prepare_venv(str(work), requirements=[bogus], pip_timeout=120)

    # 不抛异常，结构化降级。
    assert isinstance(result, SandboxPrepareResult)
    assert result.success is False
    assert bogus in result.install_failed_packages, f"装失败的包应记录: {result.install_failed_packages}"
    assert result.error is not None and bogus in result.error
    # venv 本身仍创建成功（python_exe 存在）—— 只是依赖装不上。
    assert Path(result.python_exe).exists()


# ===========================================================================
# 场景 5c：work_dir 越界（WORKSPACE_DIR 外）→ SandboxCreationError
# ===========================================================================


def test_real_5c_work_dir_outside_workspace_raises(sandbox_workspace):
    ws, work = sandbox_workspace
    # /tmp 在受控 workspace 之外。
    outside = "/tmp/definitely-outside-workspace-xyzzy"

    with pytest.raises(SandboxCreationError):
        prepare_venv(outside)

    # run_in_venv 同样护栏（用合法 python_exe 但越界 work_dir）。
    prep = prepare_venv(str(work))
    assert prep.success is True
    with pytest.raises(SandboxCreationError):
        run_in_venv(prep.python_exe, [prep.python_exe, "-c", "print(1)"], outside)

    # collect_artifacts 越界同样拦截。
    with pytest.raises(SandboxCreationError):
        collect_artifacts(outside)


# ===========================================================================
# 场景 5d：输出截断 —— 脚本打印超大输出 → output_truncated=True（不依赖网络）
# ===========================================================================


def test_real_5d_output_truncation(sandbox_workspace):
    ws, work = sandbox_workspace
    prep = prepare_venv(str(work))
    assert prep.success is True

    # 打印远超 output_max_bytes 的输出；这里用一个小的 max_bytes 加速（不必真打 1MiB）。
    flood = _write_script(
        work,
        "flood.py",
        "import sys\nsys.stdout.write('A' * 200000)\nsys.stdout.flush()\n",
    )
    run = run_in_venv(
        prep.python_exe, [prep.python_exe, str(flood)], str(work), output_max_bytes=10000
    )

    assert run.exit_code == 0
    assert run.output_truncated is True, "超大输出应被标记截断"
    # 截断保留尾部 + 截断标记。
    assert "truncated" in run.stdout
    # 截断后 stdout 字节数受限（含标记，远小于原始 200000）。
    assert len(run.stdout.encode("utf-8")) < 200000


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-m", "sandbox_real", "-v", "-s"]))
