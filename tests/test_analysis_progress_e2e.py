"""进度页「Playwright e2e」：真起 streamlit 子进程 + chromium 读 iframe 内终态文本。

为什么 e2e
==========
ui/pages/analysis_progress.py D5 迁到 streamlit-shadcn-ui 后，终态卡片（致命错误 /
任务已终止 / 工作线程异常）从 st.error/st.warning 改 ui.alert、实时日志从
st.expander+st.code 改 ui.accordion，全部渲染在 shadcn iframe 里。
``streamlit.testing.v1.AppTest`` 看不到 iframe 内容，故下列原 AppTest 终态文本断言
（在 tests/test_analysis_progress_integration.py 中标 @pytest.mark.skip）迁到这里用
真浏览器遍历 page.frames 读 inner_text 断言：

  - test_priority_error_over_cancelled_and_interrupted → state_error 场景
    （iframe ui.alert 含「致命错误」/「STATE-ERR」）
  - test_i456[I4_error] → worker_error 场景（iframe 含「工作线程异常」/「WORKER-BOOM」）
  - test_i456[I5_cancelled] → cancelled 场景（iframe 含「任务已终止」）
  - test_priority_all_four_terminal_true_picks_worker_error → worker_error 场景
  - test_many_same_label_detail_expanders_no_exception → many_errors 场景
    （8 段 ui.accordion 不抛异常，页面无 Python traceback）

harness app：_e2e_progress_app.py，按 E2E_SCENE 环境变量注入不同终态 state。

marker：@pytest.mark.browser（pytest.ini 已注册）；streamlit/chromium 起不动则 skip。

运行::

    .venv/bin/python -m pytest tests/test_analysis_progress_e2e.py -v -m browser
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
HARNESS_APP = PROJECT_ROOT / "_e2e_progress_app.py"

pytestmark = pytest.mark.browser


# --------------------------------------------------------------------------- #
# 工具
# --------------------------------------------------------------------------- #
def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _wait_port(port: int, timeout: float = 45.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1.0)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.3)
    return False


def _all_frames_text(pg, timeout: float = 12.0) -> str:
    """轮询合并主文档 + 所有 iframe 的 inner_text（shadcn 文本在 iframe 内）。

    iframe 加载有延迟，轮询到文本里出现 ui.alert/accordion 的内容或超时为止。
    """
    deadline = time.time() + timeout
    last = ""
    while time.time() < deadline:
        parts = []
        for fr in pg.frames:
            try:
                parts.append(fr.inner_text("body", timeout=1000))
            except Exception:
                continue
        last = "\n".join(parts)
        # iframe 已渲染出实质内容（非仅主文档标题）就够了
        if len(parts) > 1 and any(len(p) > 0 for p in parts[1:]):
            return last
        time.sleep(0.4)
    return last


# --------------------------------------------------------------------------- #
# fixtures：按场景起 streamlit 子进程 + chromium
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def browser():
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"playwright 不可用：{exc}")

    pw = None
    br = None
    try:
        pw = sync_playwright().start()
        br = pw.chromium.launch(headless=True, args=["--no-sandbox"])
    except Exception as exc:  # noqa: BLE001
        if br is not None:
            br.close()
        if pw is not None:
            pw.stop()
        pytest.skip(f"chromium 起不动，跳过浏览器 e2e：{exc}")
    try:
        yield br
    finally:
        br.close()
        pw.stop()


def _run_scene(browser, scene: str):
    """起一个注入了 E2E_SCENE=scene 的 streamlit 子进程 + 新 page，返回 (page, ctx, proc)。"""
    if not HARNESS_APP.exists():
        pytest.skip(f"harness app 不存在：{HARNESS_APP}")

    port = _free_port()
    env = dict(os.environ)
    env["E2E_SCENE"] = scene
    env["PYTHONPATH"] = str(PROJECT_ROOT) + os.pathsep + env.get("PYTHONPATH", "")

    proc = subprocess.Popen(
        [
            sys.executable, "-m", "streamlit", "run", str(HARNESS_APP),
            "--server.port", str(port),
            "--server.headless", "true",
            "--server.address", "127.0.0.1",
            "--browser.gatherUsageStats", "false",
            "--global.developmentMode", "false",
        ],
        cwd=str(PROJECT_ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if not _wait_port(port, timeout=45.0):
        proc.terminate()
        out = b""
        try:
            out = proc.stdout.read() if proc.stdout else b""
        except Exception:
            pass
        pytest.skip(f"streamlit 子进程({scene})未就绪：{out[:500]!r}")

    ctx = browser.new_context()
    pg = ctx.new_page()
    pg.goto(f"http://127.0.0.1:{port}", wait_until="domcontentloaded")
    pg.wait_for_load_state("networkidle")
    # 等页面标题出现 + 给 shadcn iframe 加载时间
    try:
        pg.get_by_text("分析进度", exact=False).first.wait_for(timeout=15000)
    except Exception:
        pass
    time.sleep(3.0)
    return pg, ctx, proc


@pytest.fixture()
def scene(browser, request):
    """参数化场景 fixture：request.param 为 E2E_SCENE 名，yield 渲染好的 page。"""
    pg, ctx, proc = _run_scene(browser, request.param)
    try:
        yield pg
    finally:
        ctx.close()
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()


# =========================================================================== #
# 终态文本断言 e2e（迁自 test_analysis_progress_integration.py 的 5 个 skip 用例）
# =========================================================================== #
@pytest.mark.parametrize("scene", ["state_error"], indirect=True)
def test_e2e_state_error_shows_fatal_text(scene):
    """state.error 致命态：iframe ui.alert 含「致命错误」与「STATE-ERR」。

    迁自 test_priority_error_over_cancelled_and_interrupted /
    test_priority_all_four_terminal_true_picks_worker_error 的 state_error 断言。
    """
    txt = _all_frames_text(scene)
    assert "致命错误" in txt, f"未在 iframe 找到「致命错误」，全文本={txt[:800]!r}"
    assert "STATE-ERR" in txt, f"未在 iframe 找到「STATE-ERR」，全文本={txt[:800]!r}"


@pytest.mark.parametrize("scene", ["worker_error"], indirect=True)
def test_e2e_worker_error_shows_fatal_card(scene):
    """worker 异常致命态：iframe ui.alert 含「工作线程异常」与「WORKER-BOOM」。

    迁自 test_i456[I4_error] /
    test_priority_all_four_terminal_true_picks_worker_error。
    """
    txt = _all_frames_text(scene)
    assert "工作线程异常" in txt, f"未在 iframe 找到「工作线程异常」，全文本={txt[:800]!r}"
    assert "WORKER-BOOM" in txt, f"未在 iframe 找到「WORKER-BOOM」，全文本={txt[:800]!r}"


@pytest.mark.parametrize("scene", ["cancelled"], indirect=True)
def test_e2e_cancelled_shows_terminated_text(scene):
    """取消终态：iframe ui.alert 含「任务已终止」。

    迁自 test_i456[I5_cancelled]。
    """
    txt = _all_frames_text(scene)
    assert "任务已终止" in txt, f"未在 iframe 找到「任务已终止」，全文本={txt[:800]!r}"


@pytest.mark.parametrize("scene", ["many_errors"], indirect=True)
def test_e2e_many_errors_no_python_traceback(scene):
    """8 段同类 node_errors 的 ui.accordion 不抛异常（无 Python traceback 漏到页面）。

    迁自 test_many_same_label_detail_expanders_no_exception：原断言「不抛
    DuplicateElementId 异常」，e2e 版断言页面主体无 streamlit 异常红框/traceback。
    """
    body = scene.inner_text("body")
    # streamlit 未捕获异常会在主文档渲染 traceback 文本
    for bad in ["Traceback (most recent call last)", "DuplicateElementId",
                "StreamlitDuplicateElementId"]:
        assert bad not in body, f"页面出现异常文本「{bad}」，body={body[:800]!r}"
    # 正常路径应渲染出页面标题，证明没整页崩
    assert "分析进度" in body, f"页面主体未渲染标题，body={body[:500]!r}"
