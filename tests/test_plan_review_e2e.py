"""plan_review 页「Playwright e2e」（新范式：真起 streamlit 子进程 + chromium 点 iframe 按钮）。

为什么 e2e
==========
ui/pages/plan_review.py 已迁到 streamlit-shadcn-ui，决策按钮渲染在 iframe 里。
``streamlit.testing.v1.AppTest`` 看不到 iframe 组件、点击不回写 session_state，
故「点击决策按钮 → 断言 controller 被以正确 payload 调用」类用例只能用真实浏览器跑。

落盘断言范式（harness：tests/e2e_harnesses/_e2e_app.py，已亲自跑通）
--------------------------------------------------
harness app 用 mock controller ``RecCtrl``：
- get_interrupt_payload 返回固定 _PAYLOAD；
- resume_with(tid, decision) / cancel_task(tid) 各 append 一行 JSON 到 REC 文件
  （env E2E_REC 指定路径）。
顶层 monkeypatch app._get_controller=lambda: RecCtrl()，预置 thread_id="tid-e2e"、
current_page="review"，再 from ui.pages.plan_review import render; render()。

测试用 playwright sync API 起 chromium → goto 该 streamlit app → 等 iframe 加载完
→ 遍历 page.frames 找含目标按钮文案的 frame → frame.get_by_text(文案).click()
→ 等 rerun + 文件 flush → 读 REC 文件断言 payload。

marker：@pytest.mark.browser（pytest.ini 已注册，默认可跑，chromium 已装）；
chromium 起不动则 skip（不 fail）。

运行::

    .venv/bin/python -m pytest tests/test_plan_review_e2e.py -v -m browser
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
HARNESS_APP = PROJECT_ROOT / "tests" / "e2e_harnesses" / "_e2e_app.py"

# thread_id 与 _e2e_app.py harness 一致（harness 用 "tid-e2e"）。
TID = "tid-e2e"

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


def _wait_port(port: int, timeout: float = 40.0) -> bool:
    """等 streamlit 子进程监听端口就绪。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1.0)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.3)
    return False


# --------------------------------------------------------------------------- #
# fixtures：REC 文件、streamlit 子进程、chromium 浏览器
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def rec_file(tmp_path_factory):
    """模块级 REC 落盘文件路径（每个用例开头会用空 write 清空，不 rm）。"""
    p = tmp_path_factory.mktemp("e2e") / "rec.jsonl"
    p.write_text("")  # 预创建空文件
    return p


@pytest.fixture(scope="module")
def streamlit_server(rec_file):
    """起 streamlit 子进程跑 harness app（独立端口），用完关掉。"""
    if not HARNESS_APP.exists():
        pytest.skip(f"harness app 不存在：{HARNESS_APP}")

    port = _free_port()
    env = dict(os.environ)
    env["E2E_REC"] = str(rec_file)
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
    try:
        if not _wait_port(port, timeout=45.0):
            proc.terminate()
            out = b""
            try:
                out = proc.stdout.read() if proc.stdout else b""
            except Exception:
                pass
            pytest.skip(f"streamlit 子进程未在超时内就绪：{out[:500]!r}")
        yield f"http://127.0.0.1:{port}"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()


@pytest.fixture(scope="module")
def browser():
    """起 chromium；起不动则 skip 整组（不 fail）。"""
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


@pytest.fixture()
def page(browser, streamlit_server, rec_file):
    """每个用例：清空 REC（空 write，不 rm）→ 新 page → goto app → 等 iframe 加载。"""
    rec_file.write_text("")  # 清空落盘记录（空 write，不 rm）
    ctx = browser.new_context()
    pg = ctx.new_page()
    pg.goto(streamlit_server, wait_until="domcontentloaded")
    _wait_app_ready(pg)
    try:
        yield pg
    finally:
        ctx.close()


# --------------------------------------------------------------------------- #
# 页面交互工具
# --------------------------------------------------------------------------- #
def _wait_app_ready(pg) -> None:
    """等 streamlit 主体 + shadcn iframe 渲染完。"""
    pg.wait_for_load_state("networkidle")
    # 等页面标题文本出现（render() 顶部 st.title）
    try:
        pg.get_by_text("计划审核", exact=False).first.wait_for(timeout=15000)
    except Exception:
        pass
    # shadcn 组件在 iframe 里，给足时间加载
    time.sleep(3.0)


def _click_in_main(pg, text: str, timeout: float = 15.0) -> bool:
    """在主文档（非 iframe）点含 text 的按钮。

    D5 后续：批准计划 / 终止任务 / 确认终止 三个按钮为命中 mock 配色（.btn-primary
    蓝底白字、.btn-danger 白底红字）改用原生 st.button + .st-key CSS 注入,渲染在
    主文档而非 shadcn iframe。故这些按钮要在 main_frame 里用 button 角色点击,
    不能走 _click_in_frame（它专门跳过 main_frame）。用 role=button + name 精确匹配,
    避免命中顶部 st.caption 指引文案里的同名子串。
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            btn = pg.get_by_role("button", name=text, exact=False).first
            btn.click(timeout=3000)
            return True
        except Exception:
            time.sleep(0.5)
    return False


def _click_in_frame(pg, text: str, timeout: float = 15.0) -> bool:
    """遍历 page.frames 找 inner_text 含 text 的 iframe，点其中含该文案的元素。

    注意：必须跳过 main_frame——streamlit 页面顶部的 st.caption 指引文案里含
    「仅复现代码」「终止任务」等子串，会误命中主文档导致点到 caption 而非 iframe 按钮。
    shadcn 决策按钮一律渲染在独立 component iframe 里。
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        for fr in pg.frames:
            if fr is pg.main_frame:
                continue  # 跳过主文档（caption 指引文案会误命中）
            try:
                body = fr.inner_text("body", timeout=1000)
            except Exception:
                continue
            if text in body:
                try:
                    fr.get_by_text(text, exact=False).first.click(timeout=3000)
                    return True
                except Exception:
                    continue
        time.sleep(0.5)
    return False


def _fill_textarea_in_frames(pg, value: str) -> bool:
    """在所有 frame 中找到第一个空 textarea 填入 value（shadcn textarea 渲染为 <textarea>）。"""
    for fr in pg.frames:
        try:
            tas = fr.query_selector_all("textarea")
        except Exception:
            continue
        for ta in tas:
            try:
                ta.fill(value)
                return True
            except Exception:
                continue
    return False


def _read_rec(rec_file) -> list:
    """读 REC 文件，返回每行解析后的 dict 列表。"""
    txt = rec_file.read_text()
    return [json.loads(ln) for ln in txt.splitlines() if ln.strip()]


def _wait_rec(rec_file, predicate, timeout: float = 12.0):
    """轮询等 REC 文件出现满足 predicate 的记录，返回该记录（超时返回 None）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        for rec in _read_rec(rec_file):
            if predicate(rec):
                return rec
        time.sleep(0.4)
    return None


def _expand_streamlit_expander(pg, label: str) -> None:
    """展开主文档里的 streamlit expander（label 含给定文案）。expander 在主 DOM 不在 iframe。"""
    try:
        summary = pg.get_by_text(label, exact=False).first
        summary.click(timeout=3000)
        time.sleep(2.0)  # 等 expander 内 iframe 加载
    except Exception:
        pass


# =========================================================================== #
# 决策点击 e2e（5 决策 + 二次确认）
# =========================================================================== #
def test_e2e_approve(page, rec_file):
    """点「✅ 批准计划」→ REC 出现 resume_with，decision={"decision":"approve"}。

    批准按钮 D5 后改原生 st.button（主文档），用 _click_in_main 点。
    """
    assert _click_in_main(page, "批准计划"), "未找到/点不到「批准计划」按钮"
    rec = _wait_rec(
        rec_file,
        lambda r: r.get("m") == "resume_with"
        and r.get("decision", {}).get("decision") == "approve",
    )
    assert rec is not None, f"未捕获 approve resume_with，REC={_read_rec(rec_file)}"
    assert rec["tid"] == TID
    assert rec["decision"] == {"decision": "approve"}


def test_e2e_code_only(page, rec_file):
    """点「📄 仅复现代码」→ REC 出现 resume_with，decision={"decision":"code_only"}。"""
    assert _click_in_frame(page, "仅复现代码"), "未找到/点不到「仅复现代码」按钮"
    rec = _wait_rec(
        rec_file,
        lambda r: r.get("m") == "resume_with"
        and r.get("decision", {}).get("decision") == "code_only",
    )
    assert rec is not None, f"未捕获 code_only resume_with，REC={_read_rec(rec_file)}"
    assert rec["decision"] == {"decision": "code_only"}


def test_e2e_revise_carries_feedback(page, rec_file):
    """展开「✏️ 修改计划」→ textarea 填字 → 点「提交修改」→ decision=revise 带 user_feedback。"""
    fb = "请把数据集换成 2WikiMultiHopQA"
    _expand_streamlit_expander(page, "✏️ 修改计划")
    assert _fill_textarea_in_frames(page, fb), "未能在任何 frame 填入 revise textarea"
    time.sleep(1.0)
    assert _click_in_frame(page, "提交修改"), "未找到/点不到「提交修改」按钮"
    rec = _wait_rec(
        rec_file,
        lambda r: r.get("m") == "resume_with"
        and r.get("decision", {}).get("decision") == "revise",
    )
    assert rec is not None, f"未捕获 revise resume_with，REC={_read_rec(rec_file)}"
    assert rec["decision"]["decision"] == "revise"
    assert rec["decision"].get("user_feedback") == fb


def test_e2e_switch_repo_carries_feedback_and_url(page, rec_file):
    """展开「🔁 切换仓库」→ 填原因+URL → 点「提交切换」→ decision=switch_repo 带三字段。"""
    reason = "官方仓库缺训练脚本"
    new_url = "https://github.com/alt/HippoRAG-repro"
    _expand_streamlit_expander(page, "🔁 切换仓库")
    # 该 expander 内含一个 textarea（feedback）和一个 input（url）。
    filled_ta = False
    filled_input = False
    for fr in page.frames:
        try:
            tas = fr.query_selector_all("textarea")
        except Exception:
            tas = []
        for ta in tas:
            try:
                if (ta.input_value() or "") == "":
                    ta.fill(reason)
                    filled_ta = True
                    break
            except Exception:
                continue
        if filled_ta:
            break
    for fr in page.frames:
        try:
            inputs = fr.query_selector_all("input[type='text'], input:not([type])")
        except Exception:
            inputs = []
        for inp in inputs:
            try:
                inp.fill(new_url)
                filled_input = True
                break
            except Exception:
                continue
        if filled_input:
            break
    assert filled_ta, "未能填 switch feedback textarea"
    assert filled_input, "未能填 switch repo url input"
    time.sleep(1.0)
    assert _click_in_frame(page, "提交切换"), "未找到/点不到「提交切换」按钮"
    rec = _wait_rec(
        rec_file,
        lambda r: r.get("m") == "resume_with"
        and r.get("decision", {}).get("decision") == "switch_repo",
    )
    assert rec is not None, f"未捕获 switch_repo resume_with，REC={_read_rec(rec_file)}"
    d = rec["decision"]
    assert d["decision"] == "switch_repo"
    assert d.get("user_feedback") == reason
    assert d.get("new_repo_url") == new_url


def test_e2e_cancel_first_click_no_cancel_task(page, rec_file):
    """首点「⛔ 终止任务」→ 不调 cancel_task（REC 无记录）、页面出现「确认终止」warning。

    终止按钮 D5 后改原生 st.button（主文档），用 _click_in_main 点。
    """
    assert _click_in_main(page, "终止任务"), "未找到/点不到「终止任务」按钮"
    # 给足时间让 rerun + 可能的落盘 flush
    time.sleep(4.0)
    recs = _read_rec(rec_file)
    assert all(r.get("m") != "cancel_task" for r in recs), \
        f"首点不应触发 cancel_task，REC={recs}"
    # 二次确认 warning 在主 DOM（st.warning）
    page.wait_for_load_state("networkidle")
    body = page.inner_text("body")
    assert "确认终止" in body, "首点后页面未出现「确认终止」二次确认文案"


def test_e2e_cancel_confirm_calls_cancel_task(page, rec_file):
    """首点「⛔ 终止任务」→ 再点「确认终止」→ REC 出现 cancel_task。

    终止/确认终止 D5 后均改原生 st.button（主文档），用 _click_in_main 点。
    """
    assert _click_in_main(page, "终止任务"), "未找到/点不到「终止任务」按钮"
    time.sleep(3.0)
    page.wait_for_load_state("networkidle")
    assert "确认终止" in page.inner_text("body"), "首点后未进入二次确认态"
    # 点「确认终止」（精确文案，避免命中「⛔ 终止任务」）
    assert _click_in_main(page, "确认终止"), "未找到/点不到「确认终止」按钮"
    rec = _wait_rec(rec_file, lambda r: r.get("m") == "cancel_task")
    assert rec is not None, f"确认后未捕获 cancel_task，REC={_read_rec(rec_file)}"
    assert rec["tid"] == TID
