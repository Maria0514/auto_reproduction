"""Sprint 4 任务 D2 自测：HOTFIX-2 备忘闭环——PIP_* 白名单 vs 显式注入复核（S4-06 配套）。

==============================================================================
CP-D2-1 架构师确认记录（2026-07-02，架构师代理定案，全文另见 G3 handoff）
==============================================================================

**背景**：`_SANDBOX_ENV_ALLOWLIST_PREFIXES` 含 `PIP_*` 前缀透传（HOTFIX-2 白名单），
理论上 `PIP_INDEX_URL=https://user:token@host/` 会把私有源凭证随白名单继承带进
沙箱，暴露给沙箱内 LLM 生成的不可信代码——与 HOTFIX-2 "凭证注入只走 extra_env
显式口" 设计意图矛盾（TODO HOTFIX-2 备忘挂账项）。

**实证**（2026-07-02 当前环境）：`env | grep -i '^PIP'` 零命中；无 ~/.pip/pip.conf、
~/.config/pip/pip.conf、/etc/pip.conf；.env 仅含 DEEPXIV_TOKEN/LLM_API_KEY；
`env | grep -i proxy` 零命中（架构师定案前置条件：proxy 变量无 userinfo 形态，满足）。
即当前不存在任何 PIP_*/proxy 变量，方案变更零即时破坏面。

**定案：(a')-修正版**（(a) 整体剔除 PIP_ 前缀、(b) 保留透传仅靠 mask 均被否决）：
    - 保留 `PIP_` 前缀白名单（非凭证 PIP_INDEX_URL / PIP_CACHE_DIR 等合理运维
      配置继续透传，既有 9 条隔离测试语义零弱化）；
    - `_build_sandbox_env` 白名单继承环节对**所有**继承值统一做值级凭证形态
      否决（不特判 PIP_ 前缀，单一规则）：正则 `://[^/\\s@]+@`（URL userinfo，
      同时覆盖 `user:pass@` 与 token-only `<PAT>@` 两形态；多 URL 空格串命中
      任一段即整变量剔除）；剔除打 WARNING（只打变量名绝不打值，失败非静默）；
    - extra_env 显式注入口**不过滤**（凭证注入唯一正规路径 = .secrets →
      build_credential_env / 编排层 → extra_env，与 test_cp_d1_2 既有断言兼容）。

**否决理由**：(a) 直接弱化既有测试语义且破坏面 > 收益；(b) mask_value 只防日志
/state 文本落点泄漏，凭证仍进沙箱 env 被不可信代码 os.environ 直读，形同不修。

**YAGNI 挂账**：`build_credential_env` 暂不加 `pip_index_url → PIP_INDEX_URL`
映射（当前环境零 PIP_* 实证 + A3 "六接口不多不少" 硬约束）；真实私有源需求出现
时单行映射 + 一条测试即闭合（本文件有 characterization 锚定现状）。

==============================================================================

覆盖 dev-plan §4 任务 D2 CP-D2-1 / CP-D2-2：
    - CP-D2-1 定案落地断言：PIP_ 前缀仍在白名单（语义不弱化）+ 凭证形态正则
      行为锚定（既有 9 条零修改 + test_sandbox_env_isolation.py 增量 4 条互补）；
    - CP-D2-2 构造 `PIP_INDEX_URL` 含 `user:token@` 环境，断言 token 不出现在
      沙箱子进程可见 env（Popen 边界 spy），且显式注入路径（extra_env）可接管
      （AC-S4-11 pip 分支地基）。

测试策略：全 mock / tmp_path，禁真实网络；Popen 全程 fake，不跑真实子进程。
"""

from __future__ import annotations

import json
import logging
import os
from typing import List
from unittest import mock

import pytest

import config
from core import secrets_store
from core.secrets_store import build_credential_env, mask_value, register_sensitive_value
from sandbox import local_venv
from sandbox.local_venv import (
    _CREDENTIAL_URL_USERINFO_RE,
    _SANDBOX_ENV_ALLOWLIST_PREFIXES,
    _build_sandbox_env,
    prepare_venv,
)

# 模拟私有源内嵌凭证。
_TOKEN = "d2-private-index-token-do-not-leak"
_CRED_PIP_URL = f"https://user:{_TOKEN}@pypi.private.example/simple"


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clean_process_sensitive_set():
    secrets_store._SENSITIVE_VALUES.clear()
    yield
    secrets_store._SENSITIVE_VALUES.clear()


@pytest.fixture()
def sandbox_workspace(tmp_path, monkeypatch):
    """WORKSPACE_DIR patch 到 tmp_path 受控目录（local_venv + config 双落点）。"""
    ws = tmp_path / "workspace"
    work = ws / "thread-d2" / "code"
    work.mkdir(parents=True)
    monkeypatch.setattr(local_venv, "WORKSPACE_DIR", ws)
    monkeypatch.setattr(config, "WORKSPACE_DIR", ws)
    return ws, work


def _preseed_venv(work) -> None:
    """预置 venv 结构走 reuse_existing 复用路径（Popen 全 mock，不跑真实子进程）。"""
    venv_dir = work / ".venv"
    (venv_dir / "bin").mkdir(parents=True)
    (venv_dir / "pyvenv.cfg").write_text("home = /usr\n", encoding="utf-8")
    (venv_dir / "bin" / "python").write_text("#!/bin/sh\n", encoding="utf-8")


class _FakeProc:
    pid = 99999
    returncode = 0

    def communicate(self, timeout=None):
        return b"", b""


# ===========================================================================
# CP-D2-1 定案落地断言（语义不弱化 + 正则行为锚定）
# ===========================================================================

def test_cp_d2_1_pip_prefix_still_in_allowlist():
    """定案 (a')：PIP_ 前缀保留在白名单（非凭证 PIP_* 继续透传，语义不弱化；
    方案 (a) 整体剔除被架构师否决）。"""
    assert "PIP_" in _SANDBOX_ENV_ALLOWLIST_PREFIXES
    assert "LC_" in _SANDBOX_ENV_ALLOWLIST_PREFIXES


@pytest.mark.parametrize("value,should_hit", [
    (f"https://user:{_TOKEN}@pypi.private.example/simple", True),   # user:pass 形态
    ("https://sometoken@pypi.private.example/simple", True),        # token-only 形态
    ("https://pypi.org/simple https://u:p@private.example/simple", True),  # 多 URL 任一段
    ("https://pypi.example/simple", False),                         # 无 userinfo
    ("/tmp/pipcache", False),                                       # 非 URL（PIP_CACHE_DIR）
    ("60", False),                                                  # 数值（PIP_TIMEOUT）
    ("http://proxy.example:3128", False),                           # 端口冒号非 userinfo
])
def test_cp_d2_1_credential_userinfo_regex_behavior(value, should_hit):
    """凭证形态判定正则锚定：`://[^/\\s@]+@` 命中 user:pass 与 token-only，
    放过无 userinfo URL / 非 URL / 纯端口冒号。"""
    assert bool(_CREDENTIAL_URL_USERINFO_RE.search(value)) is should_hit


def test_cp_d2_1_non_credential_pip_vars_still_passthrough(monkeypatch):
    """兼容面：非凭证类 PIP_INDEX_URL 仍透传（(a') 相对 (a) 的核心差异，
    既有运维配置零破坏）。

    注：Sprint 6 MF-1 后，PIP_CACHE_DIR 无条件覆盖为 SANDBOX_PIP_CACHE_DIR（/data 卷），
    即使宿主传入 /tmp/pipcache 也会被压制——此为故意设计（防止打爆 home 配额）。
    """
    from config import SANDBOX_PIP_CACHE_DIR
    monkeypatch.setenv("PIP_INDEX_URL", "https://pypi.example/simple")
    monkeypatch.setenv("PIP_CACHE_DIR", "/tmp/pipcache")
    env = _build_sandbox_env()
    assert env.get("PIP_INDEX_URL") == "https://pypi.example/simple"
    # MF-1：PIP_CACHE_DIR 被无条件覆盖为 SANDBOX_PIP_CACHE_DIR
    assert env.get("PIP_CACHE_DIR") == str(SANDBOX_PIP_CACHE_DIR)


def test_cp_d2_1_yagni_pip_index_url_purpose_key_not_mapped_yet(sandbox_workspace):
    """Characterization（YAGNI 挂账锚定）：build_credential_env 现不映射
    `pip_index_url` purpose_key（架构师定案推迟；未来单行扩展点——本断言翻转时
    说明映射已补，需同步复核 D2 结论与 A3 接口清单）。"""
    env = build_credential_env({"pip_index_url": _CRED_PIP_URL})
    assert env == {"GIT_TERMINAL_PROMPT": "0"}
    assert _TOKEN not in json.dumps(env)


# ===========================================================================
# CP-D2-2 token 不进沙箱子进程可见 env / 显式注入路径接管
# ===========================================================================

def test_cp_d2_2_parent_pip_credential_not_visible_in_sandbox_popen_env(
    sandbox_workspace, monkeypatch, caplog,
):
    """核心验收：父环境 PIP_INDEX_URL 含 user:token@ → prepare_venv 全路径
    （pip install 等）子进程 env 无该变量、无 token 明文；剔除有 WARNING 且
    WARNING 不含 token。"""
    ws, work = sandbox_workspace
    monkeypatch.setenv("PIP_INDEX_URL", _CRED_PIP_URL)
    _preseed_venv(work)
    recorded: List = []

    def _spy_popen(cmd, *args, **kwargs):
        recorded.append((list(cmd), kwargs.get("env")))
        return _FakeProc()

    with caplog.at_level(logging.WARNING, logger="sandbox.local_venv"), \
         mock.patch.object(local_venv.subprocess, "Popen", side_effect=_spy_popen):
        result = prepare_venv(str(work), requirements=["numpy"], reuse_existing=True)

    assert result.success is True
    assert recorded, "应有子进程调用"
    for cmd, env in recorded:
        assert env is not None, "必须显式传 env（env=None 即全量继承）"
        assert "PIP_INDEX_URL" not in env, "凭证形态 PIP_* 不得随白名单继承进沙箱"
        assert _TOKEN not in json.dumps(env), "token 不得出现在沙箱子进程可见 env"
    # 剔除非静默 + 日志无 token 明文。
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("PIP_INDEX_URL" in r.getMessage() for r in warnings)
    for record in caplog.records:
        assert _TOKEN not in record.getMessage()


def test_cp_d2_2_explicit_extra_env_injection_takes_over(sandbox_workspace, monkeypatch):
    """显式注入路径接管：父环境凭证形态 PIP_INDEX_URL 被剔除的同时，
    `.secrets` → extra_env 显式注入的私有源 URL（含凭证）在子进程 env 可见
    （显式口不过滤，唯一正规凭证通道，与 CP-D1-2 合并语义一致）。"""
    ws, work = sandbox_workspace
    inherited_token = "d2-inherited-should-drop"
    monkeypatch.setenv("PIP_INDEX_URL", f"https://user:{inherited_token}@bad.example/simple")
    _preseed_venv(work)
    recorded: List = []

    def _spy_popen(cmd, *args, **kwargs):
        recorded.append((list(cmd), kwargs.get("env")))
        return _FakeProc()

    with mock.patch.object(local_venv.subprocess, "Popen", side_effect=_spy_popen):
        result = prepare_venv(
            str(work), requirements=["numpy"], reuse_existing=True,
            extra_env={"PIP_INDEX_URL": _CRED_PIP_URL},  # 显式注入（模拟 .secrets 路径）
        )

    assert result.success is True
    pip_envs = [env for cmd, env in recorded if "install" in cmd]
    assert pip_envs, "应有 pip install 子进程"
    for env in pip_envs:
        # 继承路径被否决：父环境 token 不可见。
        assert inherited_token not in json.dumps(env)
        # 显式注入路径接管：extra_env 值可见（不被值级过滤误伤）。
        assert env.get("PIP_INDEX_URL") == _CRED_PIP_URL


def test_cp_d2_2_mask_value_covers_pip_credential_echo(sandbox_workspace):
    """§9.4 mask 链路互补（非替代）：显式注入的 pip 凭证若被 pip 输出回显，
    登记进程内 sensitive set 后 mask_value 可脱敏（E3 logs 收尾消费的地基）。"""
    register_sensitive_value(_TOKEN)
    echo = f"Looking in indexes: {_CRED_PIP_URL}"
    masked = mask_value(echo)
    assert _TOKEN not in masked
    assert "****" in masked


# ===========================================================================
# 测试工程师补强（2026-07-02 D2 验收，攻击者视角边界；反过度工程——只补真实攻击面）
# ===========================================================================

def test_hardening_uppercase_named_allowlist_var_also_filtered(monkeypatch, caplog):
    """大小写绕过检查：值级否决按值判定、与变量名大小写无关——具名白名单的
    大写 HTTPS_PROXY 带 token-only userinfo 同样剔除 + WARNING 只打变量名。"""
    monkeypatch.setenv("HTTPS_PROXY", "http://sometoken@proxy.example:3128")
    with caplog.at_level(logging.WARNING, logger="sandbox.local_venv"):
        env = _build_sandbox_env()
    assert "HTTPS_PROXY" not in env
    assert any(
        "HTTPS_PROXY" in r.getMessage()
        for r in caplog.records if r.levelno == logging.WARNING
    )
    for record in caplog.records:
        assert "sometoken" not in record.getMessage()


@pytest.mark.parametrize("value,kept", [
    ("http://[::1]:8080/simple", True),                  # IPv6 host 无 userinfo，不误杀
    ("https://mirror.example/simple/pkg@v2.0", True),    # 路径中 @（非 userinfo），不误杀
    ("https://@pypi.example/simple", True),              # 空 userinfo（无凭证），放行
    ("http://user@[::1]:8080/simple", False),            # IPv6 host 带 userinfo，剔除
    ("git+https://tok@host.example/x.git", False),       # git+https scheme userinfo，剔除
])
def test_hardening_env_level_userinfo_boundary(monkeypatch, value, kept):
    """正则边界在 _build_sandbox_env 端到端行为层锚定（非仅正则单元）：
    合法运维配置（IPv6 源 / 路径含 @ / 空 userinfo）不被误杀导致 pip 断源；
    真凭证形态（IPv6 userinfo / git+https userinfo）不放行。"""
    monkeypatch.setenv("PIP_INDEX_URL", value)
    env = _build_sandbox_env()
    if kept:
        assert env.get("PIP_INDEX_URL") == value
    else:
        assert "PIP_INDEX_URL" not in env
