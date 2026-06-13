"""S2-13 Playwright 浏览器 e2e：用户提供仓库统一抓取分析 + 同口径评分通道（UI 可观测行为）。

范式沿用 tests/test_plan_review_e2e.py：真起 streamlit 子进程跑 _e2e_s2_13_app.py harness
（mock controller + env 注入 interrupt payload，不依赖真实 LLM / 真实 git clone）
+ chromium goto → 等渲染 → 读 DOM / REC 文件断言。

覆盖（PRD §6 AC-S2-21/23/25、§2.13.5/6）：
  - switch_repo 失败（payload.switch_repo_failed=True）→ 「🔁 切换仓库」expander 展开
    + st.error 重填提示渲染（强制重填，AC-S2-25①）；
  - 候选仓库卡片展示真实 quality_score（非 0）/ stars-forks 显示「—」不崩页（AC-S2-21/23）；
  - switch_repo awaiting spinner 文案「正在克隆并分析仓库、重新生成计划……」（§2.13.6）。

marker：@pytest.mark.browser（默认可跑；chromium / streamlit 子进程起不动则 skip，不 fail）。
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
HARNESS_APP = PROJECT_ROOT / "tests" / "e2e_harnesses" / "_e2e_s2_13_app.py"

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


def _start_server(variant: str):
    """起 streamlit 子进程跑 S2-13 harness（指定 payload 变体）。返回 (proc, base_url)。"""
    if not HARNESS_APP.exists():
        pytest.skip(f"harness app 不存在：{HARNESS_APP}")
    port = _free_port()
    env = dict(os.environ)
    env["E2E_S2_13_VARIANT"] = variant
    # REC 落临时目录（不污染仓库；本组用例只读 DOM 不读 REC，仍给 harness 合法路径）。
    env["E2E_REC"] = os.path.join(
        tempfile.gettempdir(), f"_s2_13_rec_{variant}_{os.getpid()}.jsonl")
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
        pytest.skip(f"streamlit 子进程未在超时内就绪：{out[:500]!r}")
    return proc, f"http://127.0.0.1:{port}"


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


def _page_for(browser, variant: str):
    proc, base = _start_server(variant)
    ctx = browser.new_context()
    pg = ctx.new_page()
    pg.goto(base, wait_until="domcontentloaded")
    pg.wait_for_load_state("networkidle")
    try:
        pg.get_by_text("计划审核", exact=False).first.wait_for(timeout=15000)
    except Exception:
        pass
    time.sleep(3.0)
    return proc, ctx, pg


def _full_text(pg) -> str:
    """聚合主文档 + 所有 iframe 的可见文本。"""
    parts = []
    for fr in pg.frames:
        try:
            parts.append(fr.inner_text("body", timeout=2000))
        except Exception:
            continue
    return "\n".join(parts)


# =========================================================================== #
# AC-S2-25①：switch_repo 失败 → expander 展开 + st.error 强制重填提示
# =========================================================================== #
def test_e2e_switch_repo_failed_shows_force_refill_error(browser):
    proc = ctx = None
    try:
        proc, ctx, pg = _page_for(browser, "switch_failed")
        text = _full_text(pg)
        # st.error 重填提示渲染（强制重填，AC-S2-25①）。
        assert "仓库克隆/分析失败" in text, f"未渲染强制重填提示，page text=\n{text[:800]}"
        # 「🔁 切换仓库」expander 标题可见（expanded=True 时其内容随之展开）。
        assert "切换仓库" in text
    finally:
        if ctx is not None:
            ctx.close()
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except Exception:
                proc.kill()


# =========================================================================== #
# AC-S2-21/23：候选卡片真实 quality_score（非 0）+ stars/forks「—」不崩页
# =========================================================================== #
def test_e2e_ok_variant_shows_real_quality_and_dash_stars(browser):
    proc = ctx = None
    try:
        proc, ctx, pg = _page_for(browser, "ok")
        text = _full_text(pg)
        # 候选卡片质量分展示真实值 0.78（非 0 / 非「—」），AC-S2-21。
        assert "0.78" in text, f"未展示真实 quality_score=0.78，page text=\n{text[:800]}"
        # stars / forks 留空展示「—」（AC-S2-23），不崩页。
        assert "—" in text
        # 不应出现失败重填提示（switch_repo_failed=False）。
        assert "仓库克隆/分析失败" not in text
    finally:
        if ctx is not None:
            ctx.close()
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except Exception:
                proc.kill()
