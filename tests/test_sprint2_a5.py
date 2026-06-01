"""Sprint 2 任务 A5 自测：core/tools/git_tools.py。

覆盖 dev-plan §A5 CP-A5-1 ~ CP-A5-10。

参考实现：sp2 同款风格 tests/test_sprint2_a1.py / test_sprint2_a4.py
（轻量结构性断言 + mock subprocess/requests，无真实 LLM、无真实 clone；
真实 clone e2e 留给 E1）。

硬约束验证：
    - BUG-S1-02 治理范式：_serialize_tool_result 输出合法 JSON
      （json.loads 不报错，ensure_ascii=False / sort_keys=True）；
    - 安全约束：subprocess.run 全部列表形式不用 shell=True；dest_dir 越界拒绝。
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from typing import List
from unittest import mock

import pytest

from langchain_core.tools import BaseTool

import config
from core.errors import PermanentError, TransientError
from core.tools import git_tools


# ---------------------------------------------------------------------------
# 测试 helper
# ---------------------------------------------------------------------------

def _completed(returncode: int, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=["git", "clone"], returncode=returncode, stdout=stdout, stderr=stderr
    )


@pytest.fixture()
def workspace_dest(tmp_path, monkeypatch):
    """提供一个位于（patch 后的）WORKSPACE_DIR 之下的合法 dest 目录。

    把 WORKSPACE_DIR / WORKSPACE_REPOS_DIR 指到 tmp_path，避免污染真实 workspace，
    并保证越界校验在受控目录上判定。
    """
    ws = tmp_path / "workspace"
    repos = ws / "repos"
    repos.mkdir(parents=True)
    monkeypatch.setattr(git_tools, "WORKSPACE_DIR", ws)
    monkeypatch.setattr(git_tools, "WORKSPACE_REPOS_DIR", repos)
    return ws, repos


# ========== CP-A5-1：git_clone 网络可达成功 ==========


def test_cp_a5_1_git_clone_success(workspace_dest):
    """CP-A5-1: 网络可达时 git_clone 返回 success=True + 合法 local_path。"""
    ws, repos = workspace_dest
    dest = str(repos / "owner__repo")

    with mock.patch.object(git_tools.subprocess, "run", return_value=_completed(0)) as m_run:
        result = git_tools.git_clone("https://github.com/owner/repo", dest)

    assert result["success"] is True
    assert result["local_path"]  # 非空
    assert result["error"] is None
    assert isinstance(result["duration_seconds"], float)
    # 命令是 git clone 列表形式
    called_cmd = m_run.call_args.args[0]
    assert called_cmd[:3] == ["git", "clone", "--depth"]
    assert "https://github.com/owner/repo" in called_cmd


# ========== CP-A5-2：死链 URL 抛 PermanentError 不重试 ==========


def test_cp_a5_2_dead_link_permanent_no_retry(workspace_dest):
    """CP-A5-2: stderr=Repository not found + exit 128 抛 PermanentError，不重试。"""
    ws, repos = workspace_dest
    dest = str(repos / "owner__dead")

    with mock.patch.object(
        git_tools.subprocess,
        "run",
        return_value=_completed(128, stderr="fatal: Repository not found"),
    ) as m_run, mock.patch.object(git_tools.time, "sleep") as m_sleep:
        with pytest.raises(PermanentError):
            git_tools.git_clone("https://github.com/owner/dead", dest)

    # 不重试：subprocess.run 只调一次，且从未 sleep。
    assert m_run.call_count == 1
    assert m_sleep.call_count == 0


# ========== CP-A5-3：网络瞬态 3 次指数退避后抛 TransientError ==========


def test_cp_a5_3_transient_retry_then_raise(workspace_dest):
    """CP-A5-3: TimeoutExpired 触发 1s/2s/4s 退避，4 次执行后抛 TransientError。"""
    ws, repos = workspace_dest
    dest = str(repos / "owner__slow")

    with mock.patch.object(
        git_tools.subprocess,
        "run",
        side_effect=subprocess.TimeoutExpired(cmd=["git"], timeout=60),
    ) as m_run, mock.patch.object(git_tools.time, "sleep") as m_sleep:
        with pytest.raises(TransientError):
            git_tools.git_clone("https://github.com/owner/slow", dest)

    # 首次 + 3 次重试 = 4 次执行；退避 sleep 3 次（1/2/4）。
    assert m_run.call_count == 4
    assert m_sleep.call_count == 3
    sleep_args = [c.args[0] for c in m_sleep.call_args_list]
    assert sleep_args == [1.0, 2.0, 4.0]


def test_cp_a5_3b_transient_stderr_retry(workspace_dest):
    """CP-A5-3 补：stderr 含网络瞬态关键字同样走退避重试。"""
    ws, repos = workspace_dest
    dest = str(repos / "owner__net")

    with mock.patch.object(
        git_tools.subprocess,
        "run",
        return_value=_completed(128, stderr="fatal: could not resolve host: github.com"),
    ) as m_run, mock.patch.object(git_tools.time, "sleep"):
        with pytest.raises(TransientError):
            git_tools.git_clone("https://github.com/owner/net", dest)

    assert m_run.call_count == 4


# ========== CP-A5-4：dest_dir 越界拒绝 ==========


def test_cp_a5_4_dest_dir_out_of_bounds(workspace_dest):
    """CP-A5-4: git_clone(url, "/etc/passwd") 抛 PermanentError(dest_dir 越界)。"""
    with mock.patch.object(git_tools.subprocess, "run") as m_run:
        with pytest.raises(PermanentError) as exc_info:
            git_tools.git_clone("https://github.com/owner/repo", "/etc/passwd")

    assert "越界" in exc_info.value.message
    # 越界校验在 subprocess 之前，绝不应触发 clone。
    assert m_run.call_count == 0


# ========== CP-A5-5：同 URL 二次调用跳过 ==========


def test_cp_a5_5_duplicate_url_skip(workspace_dest):
    """CP-A5-5: 同 URL（local_path 已存在）二次调用返回 success=True + duration=0.0。"""
    ws, repos = workspace_dest
    url = "https://github.com/owner/repo"
    # 预先创建对应 slug 目录，模拟已克隆。
    (repos / "owner__repo").mkdir()
    dest = str(repos / "owner__repo")

    with mock.patch.object(git_tools.subprocess, "run") as m_run:
        result = git_tools.git_clone(url, dest)

    assert result["success"] is True
    assert result["duration_seconds"] == 0.0
    assert result["local_path"] == str(repos / "owner__repo")
    # 跳过克隆：不调 subprocess。
    assert m_run.call_count == 0


# ========== CP-A5-6：analyze_local_repo 完整 RepoInfo ==========


def test_cp_a5_6_analyze_full_repoinfo():
    """CP-A5-6: git init + README + commit 的小仓库返回完整 RepoInfo。"""
    with tempfile.TemporaryDirectory() as td:
        repo = Path(td)
        # 真实 git init + commit（不 mock，验证 git log 解析）。
        env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
        import os as _os
        full_env = {**_os.environ, **env}
        subprocess.run(["git", "init"], cwd=td, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "t@t"], cwd=td, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=td, check=True, capture_output=True)
        (repo / "README.md").write_text("# hello", encoding="utf-8")
        (repo / "requirements.txt").write_text("numpy\n", encoding="utf-8")
        (repo / "zzz_dir").mkdir()
        subprocess.run(["git", "add", "-A"], cwd=td, check=True, capture_output=True, env=full_env)
        subprocess.run(["git", "commit", "-m", "init"], cwd=td, check=True, capture_output=True, env=full_env)

        info = git_tools.analyze_local_repo(td)

    assert info["local_path"] == td
    assert info["has_readme"] is True
    assert info["has_requirements"] is True
    assert info["is_official"] is False
    assert info["last_commit_date"]  # ISO 8601 非空
    assert info["commit_count_recent"] == 1
    # dir_structure 字典序
    assert info["dir_structure"] == sorted(info["dir_structure"])
    assert "README.md" in info["dir_structure"]
    # RepoInfo 全字段齐备（与 core.state.RepoInfo TypedDict 对齐）
    expected_keys = {
        "url", "source", "is_official", "stars", "forks", "last_commit_date",
        "commit_count_recent", "has_readme", "has_requirements", "dir_structure",
        "quality_score", "local_path",
    }
    assert set(info.keys()) == expected_keys


# ========== CP-A5-7：无 README 仓库 ==========


def test_cp_a5_7_analyze_no_readme():
    """CP-A5-7: 无 README / 无 requirements 仓库返回 has_readme=False / has_requirements=False。"""
    with tempfile.TemporaryDirectory() as td:
        repo = Path(td)
        (repo / "main.py").write_text("print(1)\n", encoding="utf-8")
        info = git_tools.analyze_local_repo(td)

    assert info["has_readme"] is False
    assert info["has_requirements"] is False
    # 非 git 仓库：commit 指标降级为 None / 0，不抛异常。
    assert info["local_path"] == td
    assert "main.py" in info["dir_structure"]


# ========== CP-A5-8：check_url_reachable 真假两路径 ==========


def test_cp_a5_8_url_reachable_true():
    """CP-A5-8: HTTP 200 -> True。"""
    fake_resp = mock.Mock()
    fake_resp.status_code = 200
    with mock.patch("requests.head", return_value=fake_resp) as m_head:
        assert git_tools.check_url_reachable("https://github.com") is True
    # allow_redirects=True 传入
    assert m_head.call_args.kwargs.get("allow_redirects") is True


def test_cp_a5_8_url_reachable_false_404():
    """CP-A5-8: HTTP 404 -> False。"""
    fake_resp = mock.Mock()
    fake_resp.status_code = 404
    with mock.patch("requests.head", return_value=fake_resp):
        assert git_tools.check_url_reachable("https://github.com/dead") is False


def test_cp_a5_8_url_reachable_false_exception():
    """CP-A5-8: 网络异常 -> False（不抛）。"""
    with mock.patch("requests.head", side_effect=Exception("conn refused")):
        assert git_tools.check_url_reachable("https://nope.invalid") is False


def test_cp_a5_8_url_reachable_redirect():
    """CP-A5-8 补：301/302 -> True。"""
    for code in (301, 302):
        fake_resp = mock.Mock()
        fake_resp.status_code = code
        with mock.patch("requests.head", return_value=fake_resp):
            assert git_tools.check_url_reachable("https://github.com") is True


# ========== CP-A5-9：3 个工具工厂 + 序列化合规 ==========


def test_cp_a5_9_factories_return_basetool():
    """CP-A5-9: 3 个工具工厂均返回 BaseTool 实例。"""
    t1 = git_tools.make_git_clone_and_analyze_tool()
    t2 = git_tools.make_check_url_reachable_tool()
    t3 = git_tools.make_git_clone_tool()
    assert isinstance(t1, BaseTool)
    assert isinstance(t2, BaseTool)
    assert isinstance(t3, BaseTool)


def test_cp_a5_9_serialize_tool_result_valid_json():
    """CP-A5-9: _serialize_tool_result 输出合法 JSON（含 sort_keys / ensure_ascii=False）。"""
    payload = {"success": True, "local_path": "/x", "名称": "中文"}
    out = git_tools._serialize_tool_result(payload)
    # 合法 JSON：json.loads 不报错。
    parsed = json.loads(out)
    assert parsed == payload
    # ensure_ascii=False：中文不被转义为 \uXXXX。
    assert "中文" in out
    # sort_keys=True：键有序。
    keys_in_order = [k for k in parsed.keys()]  # dict 保留插入序，需从原始字符串验证排序
    assert out.index('"local_path"') < out.index('"success"')  # 字典序 l < s


def test_cp_a5_9_factory_output_is_valid_json(workspace_dest):
    """CP-A5-9 补：工具工厂在成功/失败两路径下 ToolMessage 输出都是合法 JSON（禁止 str(dict)）。"""
    ws, repos = workspace_dest

    # 成功路径：复合工具
    tool_ca = git_tools.make_git_clone_and_analyze_tool()
    with mock.patch.object(git_tools.subprocess, "run", return_value=_completed(0)), \
            mock.patch.object(git_tools, "analyze_local_repo") as m_analyze:
        m_analyze.return_value = {"url": "", "source": "git_clone", "local_path": "/x"}
        out = tool_ca.invoke({"url": "https://github.com/o/r"})
    parsed = json.loads(out)  # 合法 JSON
    assert parsed["url"] == "https://github.com/o/r"

    # 失败路径：clone 抛 PermanentError -> {"success": false, ...}
    tool_clone = git_tools.make_git_clone_tool()
    with mock.patch.object(
        git_tools.subprocess, "run",
        return_value=_completed(128, stderr="fatal: Repository not found"),
    ):
        out_fail = tool_clone.invoke({"url": "https://github.com/o/dead"})
    parsed_fail = json.loads(out_fail)
    assert parsed_fail["success"] is False
    assert "error" in parsed_fail

    # check_url_reachable 工具
    tool_url = git_tools.make_check_url_reachable_tool()
    fake_resp = mock.Mock()
    fake_resp.status_code = 200
    with mock.patch("requests.head", return_value=fake_resp):
        out_url = tool_url.invoke({"url": "https://github.com"})
    parsed_url = json.loads(out_url)
    assert parsed_url["reachable"] is True


# ========== CP-A5-10：subprocess.run 全部不用 shell=True ==========


def test_cp_a5_10_no_shell_true(workspace_dest):
    """CP-A5-10: 所有 subprocess.run 调用均为列表形式且不传 shell=True。"""
    ws, repos = workspace_dest
    dest = str(repos / "owner__repo")

    recorded: List = []

    real_run = subprocess.run

    def _spy(*args, **kwargs):
        recorded.append((args, kwargs))
        return _completed(0)

    with mock.patch.object(git_tools.subprocess, "run", side_effect=_spy):
        git_tools.git_clone("https://github.com/owner/repo", dest)

    assert recorded, "应至少有一次 subprocess.run 调用"
    for args, kwargs in recorded:
        # 命令是列表（位置参数第 0 个）
        cmd = args[0] if args else kwargs.get("args")
        assert isinstance(cmd, list), f"subprocess.run 命令必须是列表，实际: {cmd!r}"
        # 绝不传 shell=True
        assert kwargs.get("shell", False) is False


def test_cp_a5_10_analyze_no_shell_true():
    """CP-A5-10 补：analyze_local_repo 的 git log 调用同样不用 shell=True。"""
    recorded: List = []

    def _spy(*args, **kwargs):
        recorded.append((args, kwargs))
        return _completed(0, stdout="")

    with mock.patch.object(git_tools.subprocess, "run", side_effect=_spy):
        with tempfile.TemporaryDirectory() as td:
            git_tools.analyze_local_repo(td)

    assert recorded
    for args, kwargs in recorded:
        cmd = args[0] if args else kwargs.get("args")
        assert isinstance(cmd, list)
        assert kwargs.get("shell", False) is False
        assert cmd[0] == "git"


# ========== 补充：git 二进制缺失 -> PermanentError ==========


def test_aux_git_binary_missing(workspace_dest):
    """补充：FileNotFoundError（git 缺失）-> PermanentError，提示安装 git。"""
    ws, repos = workspace_dest
    dest = str(repos / "owner__repo")
    with mock.patch.object(git_tools.subprocess, "run", side_effect=FileNotFoundError("git")):
        with pytest.raises(PermanentError) as exc_info:
            git_tools.git_clone("https://github.com/owner/repo", dest)
    assert "git" in exc_info.value.message.lower()


def test_aux_repo_slug_parsing():
    """补充：_repo_slug 对 https / ssh / .git 后缀的解析。"""
    assert git_tools._repo_slug("https://github.com/owner/repo") == "owner__repo"
    assert git_tools._repo_slug("https://github.com/owner/repo.git") == "owner__repo"
    assert git_tools._repo_slug("https://github.com/owner/repo/") == "owner__repo"
    assert git_tools._repo_slug("git@github.com:owner/repo.git") == "owner__repo"


# ===========================================================================
# 测试工程师补强用例（独立验收，覆盖开发代理 19 用例未触及的边界）
# ===========================================================================


# ---- 失败分类 _classify_clone_failure 边界 ----


def test_te_classify_permanent_priority_over_transient():
    """永久关键字与瞬态关键字同时出现时，永久优先（dev-plan §A5.2）。

    stderr 同时含 "Repository not found"（永久）与 "could not resolve host"
    （瞬态），分类器必须返回 PermanentError（避免对死链浪费 3 次退避重试）。
    """
    err = git_tools._classify_clone_failure(
        "fatal: Repository not found; could not resolve host: github.com",
        128,
    )
    assert isinstance(err, git_tools.PermanentError)


def test_te_classify_case_insensitive():
    """关键字匹配大小写无关（实现用 .lower()）。"""
    # 全大写永久关键字
    err_p = git_tools._classify_clone_failure("FATAL: REPOSITORY NOT FOUND", 128)
    assert isinstance(err_p, git_tools.PermanentError)
    # 大小写混合瞬态关键字
    err_t = git_tools._classify_clone_failure("Could Not Resolve Host: x", 128)
    assert isinstance(err_t, git_tools.TransientError)


def test_te_classify_unrecognized_defaults_permanent():
    """未识别错误默认归 PermanentError（不对未知错误盲目重试）。

    开发自报"未识别→PermanentError"，此处固化该契约：避免未来误改为
    transient 导致对真实永久错误浪费退避时间。
    """
    err = git_tools._classify_clone_failure("fatal: some totally novel git error", 1)
    assert isinstance(err, git_tools.PermanentError)


def test_te_classify_disk_full_permanent():
    """磁盘空间不足（no space left on device）→ PermanentError，不重试。"""
    err = git_tools._classify_clone_failure("error: no space left on device", 128)
    assert isinstance(err, git_tools.PermanentError)


def test_te_disk_full_no_retry_via_git_clone(workspace_dest):
    """端到端：磁盘满经 git_clone 走永久路径（run 1 次、sleep 0 次）。"""
    ws, repos = workspace_dest
    dest = str(repos / "owner__dfull")
    with mock.patch.object(
        git_tools.subprocess, "run",
        return_value=_completed(128, stderr="fatal: write error: No space left on device"),
    ) as m_run, mock.patch.object(git_tools.time, "sleep") as m_sleep:
        with pytest.raises(PermanentError):
            git_tools.git_clone("https://github.com/owner/dfull", dest)
    assert m_run.call_count == 1
    assert m_sleep.call_count == 0


# ---- _is_within_workspace 越界校验边界 ----


def test_te_within_workspace_rejects_parent_escape(workspace_dest):
    """通过 .. 逃逸到 WORKSPACE_DIR 之外应被拒绝（resolve 后判定）。"""
    ws, repos = workspace_dest
    escape = str(repos / ".." / ".." / "etc_passwd")
    with mock.patch.object(git_tools.subprocess, "run") as m_run:
        with pytest.raises(PermanentError) as exc:
            git_tools.git_clone("https://github.com/owner/repo", escape)
    assert "越界" in exc.value.message
    assert m_run.call_count == 0


def test_te_within_workspace_invariant_matches_a4(workspace_dest):
    """A4→A5 路径不变量联动：WORKSPACE_REPOS_DIR 在 WORKSPACE_DIR 之下，
    且 repos 下的合法 dest 通过 _is_within_workspace 校验（与 A4 验收
    test_aux_1 的 resolve()+is_relative_to 同一判定路径）。"""
    ws, repos = workspace_dest
    assert git_tools.WORKSPACE_REPOS_DIR.resolve().is_relative_to(
        git_tools.WORKSPACE_DIR.resolve()
    )
    assert git_tools._is_within_workspace(repos / "owner__repo") is True
    assert git_tools._is_within_workspace(Path("/etc/passwd")) is False


# ---- analyze_local_repo dir_structure 截断 / 字典序 / 空目录 ----


def test_te_analyze_dir_structure_truncated_to_30(workspace_dest):
    """dir_structure 顶层超过 30 项时截断到 30，且为字典序前 30 项。"""
    ws, repos = workspace_dest
    big = repos / "bigrepo"
    big.mkdir()
    # 创建 50 个文件名，乱序数字命名（确保排序非偶然）
    names = [f"item_{i:03d}.txt" for i in range(50)]
    import random
    shuffled = names[:]
    random.shuffle(shuffled)
    for n in shuffled:
        (big / n).write_text("x", encoding="utf-8")

    info = git_tools.analyze_local_repo(str(big))
    assert len(info["dir_structure"]) == 30
    # 字典序前 30 项
    assert info["dir_structure"] == sorted(names)[:30]


def test_te_analyze_empty_dir(workspace_dest):
    """空目录：dir_structure 为空列表，commit 指标降级，不抛异常。"""
    ws, repos = workspace_dest
    empty = repos / "empty"
    empty.mkdir()
    info = git_tools.analyze_local_repo(str(empty))
    assert info["dir_structure"] == []
    assert info["has_readme"] is False
    assert info["has_requirements"] is False
    # 非 git 目录：git log 返回非 0 退出码，commit 指标保持初始 None（降级，不抛异常）。
    # 契约澄清：实现降级值为 None（非 0）；开发自报 CP-A5-7 "None / 0" 含糊，实测为 None。
    assert info["commit_count_recent"] is None
    assert info["last_commit_date"] is None
    assert info["is_official"] is False
    assert info["quality_score"] == 0.0


def test_te_analyze_repoinfo_fields_match_state_typeddict(workspace_dest):
    """RepoInfo 产出字段与 core.state.RepoInfo TypedDict 注解键逐字段一致。"""
    from core.state import RepoInfo as StateRepoInfo
    ws, repos = workspace_dest
    d = repos / "x"
    d.mkdir()
    info = git_tools.analyze_local_repo(str(d))
    assert set(info.keys()) == set(StateRepoInfo.__annotations__.keys())


# ---- repo_slug 大小写保留 ----


def test_te_repo_slug_preserves_case():
    """slug 保留原始大小写（GitHub 仓库名大小写敏感，不应被规整）。"""
    assert git_tools._repo_slug("https://github.com/Owner/RepoName") == "Owner__RepoName"
    assert git_tools._repo_slug("https://github.com/HippoRAG/HippoRAG.git") == "HippoRAG__HippoRAG"


# ---- check_url_reachable 各异常类型 + 状态码边界 ----


def test_te_url_reachable_requests_timeout():
    """requests.Timeout → False（不抛）。"""
    import requests
    with mock.patch("requests.head", side_effect=requests.Timeout("timed out")):
        assert git_tools.check_url_reachable("https://slow.example") is False


def test_te_url_reachable_requests_connection_error():
    """requests.ConnectionError → False（不抛）。"""
    import requests
    with mock.patch("requests.head", side_effect=requests.ConnectionError("refused")):
        assert git_tools.check_url_reachable("https://nope.invalid") is False


def test_te_url_reachable_500_false():
    """HTTP 500 → False（非 200/301/302）。"""
    fake = mock.Mock()
    fake.status_code = 500
    with mock.patch("requests.head", return_value=fake):
        assert git_tools.check_url_reachable("https://err.example") is False


def test_te_url_reachable_403_false():
    """HTTP 403 → False。"""
    fake = mock.Mock()
    fake.status_code = 403
    with mock.patch("requests.head", return_value=fake):
        assert git_tools.check_url_reachable("https://forbidden.example") is False


# ---- 复合工具 clone 失败路径：跳过 analyze ----


def test_te_compound_tool_skips_analyze_on_clone_failure(workspace_dest):
    """复合工具 git_clone_and_analyze：clone 失败时不调用 analyze_local_repo，
    且 ToolMessage 仍是合法 JSON 的失败 dict（不打断 ReAct 子图）。"""
    ws, repos = workspace_dest
    tool_ca = git_tools.make_git_clone_and_analyze_tool()
    with mock.patch.object(
        git_tools.subprocess, "run",
        return_value=_completed(128, stderr="fatal: Repository not found"),
    ), mock.patch.object(git_tools, "analyze_local_repo") as m_analyze:
        out = tool_ca.invoke({"url": "https://github.com/o/dead"})

    # clone 永久失败 → 跳过 analyze
    assert m_analyze.call_count == 0
    parsed = json.loads(out)  # 合法 JSON，不抛
    assert parsed["success"] is False
    assert "error" in parsed


def test_te_compound_tool_transient_failure_is_json(workspace_dest):
    """复合工具：clone 瞬态最终失败（退避耗尽抛 TransientError）也被兜底为合法 JSON。"""
    ws, repos = workspace_dest
    tool_ca = git_tools.make_git_clone_and_analyze_tool()
    with mock.patch.object(
        git_tools.subprocess, "run",
        side_effect=subprocess.TimeoutExpired(cmd=["git"], timeout=60),
    ), mock.patch.object(git_tools.time, "sleep"), \
            mock.patch.object(git_tools, "analyze_local_repo") as m_analyze:
        out = tool_ca.invoke({"url": "https://github.com/o/slow"})
    assert m_analyze.call_count == 0
    parsed = json.loads(out)
    assert parsed["success"] is False


def test_te_compound_tool_success_sets_url_field(workspace_dest):
    """复合工具成功路径：repo_info["url"] 被回填为传入 URL（resource_scout 依赖）。"""
    ws, repos = workspace_dest
    tool_ca = git_tools.make_git_clone_and_analyze_tool()
    fake_info = {
        "url": "", "source": "git_clone", "is_official": False, "stars": None,
        "forks": None, "last_commit_date": None, "commit_count_recent": 0,
        "has_readme": True, "has_requirements": True, "dir_structure": ["README.md"],
        "quality_score": 0.0, "local_path": "/x",
    }
    with mock.patch.object(git_tools.subprocess, "run", return_value=_completed(0)), \
            mock.patch.object(git_tools, "analyze_local_repo", return_value=dict(fake_info)):
        out = tool_ca.invoke({"url": "https://github.com/Owner/Repo"})
    parsed = json.loads(out)
    assert parsed["url"] == "https://github.com/Owner/Repo"
    assert parsed["has_readme"] is True


# ---- 退避序列上限 = 3 次（不会无限重试） ----


def test_te_backoff_sequence_capped_at_three(workspace_dest):
    """退避序列硬上限 3 次：即便始终瞬态失败，sleep 也恰好 3 次、总执行 4 次。

    与 CP-A5-3 互补：CP-A5-3 断言 sleep 入参，本用例固化"上限 3 次"语义
    （防未来误改 _RETRY_BACKOFF_SECONDS 长度）。"""
    ws, repos = workspace_dest
    dest = str(repos / "owner__cap")
    assert git_tools._RETRY_BACKOFF_SECONDS == (1.0, 2.0, 4.0)
    with mock.patch.object(
        git_tools.subprocess, "run",
        side_effect=subprocess.TimeoutExpired(cmd=["git"], timeout=60),
    ) as m_run, mock.patch.object(git_tools.time, "sleep") as m_sleep:
        with pytest.raises(TransientError):
            git_tools.git_clone("https://github.com/owner/cap", dest)
    assert m_run.call_count == len(git_tools._RETRY_BACKOFF_SECONDS) + 1 == 4
    assert m_sleep.call_count == 3
