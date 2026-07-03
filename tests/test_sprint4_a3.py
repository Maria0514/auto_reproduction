"""Sprint 4 任务 A3 自测：core/secrets_store.py（S4-07，安全关键）。

覆盖 dev-plan §4 任务 A3 CP-A3-1 ~ CP-A3-5（architecture §2.3 / §6 / §9.3-9.4）：
    - CP-A3-1 remember → lookup 闭环 + 0600 权限 + 不记住不落盘；
    - CP-A3-2 `.secrets` 在 gitignore 内（git check-ignore）+ JSON 损坏 / 缺失
      lookup 返回 None + caplog WARNING（失败非静默）；
    - CP-A3-3 mask_value：已记住 + 进程内未记住敏感值全替换、非敏感不 mask、
      子串 / 多值 / 长短混合无明文残留（AC-S4-12 地基）；
    - CP-A3-4 build_credential_env：无凭证仍含 GIT_TERMINAL_PROMPT=0；
      GIT_ASKPASS 脚本 0700 + token 不出现在 env 值以外任何路径；hf_token 双变量；
    - CP-A3-5 模块内 logger 全部输出经审计无 value 明文（caplog 断言）。

测试策略：全 mock / tmp_path，禁真实网络；config.WORKSPACE_DIR monkeypatch 到
tmp_path 受控目录（secrets_store 运行期动态读取 config.WORKSPACE_DIR，不在
import 期快照），GIT_ASKPASS 脚本等一切产物落 tmp_path，不污染真实 workspace。
"""

from __future__ import annotations

import json
import logging
import os
import stat
import subprocess
from pathlib import Path

import pytest

import config
from core import secrets_store
from core.secrets_store import (
    build_credential_env,
    iter_sensitive_values,
    load_all_secrets,
    lookup_secret,
    mask_value,
    register_sensitive_value,
    remember_secret,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 模拟敏感值（断言明文泄漏路径用；带可辨识后缀防误撞）。
_TOKEN = "a3-fake-git-token-do-not-leak"
_HF_TOKEN = "a3-fake-hf-token-do-not-leak"


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clean_process_sensitive_set():
    """每条用例前后清空进程内 sensitive set（模块级全局，防跨用例污染）。"""
    secrets_store._SENSITIVE_VALUES.clear()
    yield
    secrets_store._SENSITIVE_VALUES.clear()


@pytest.fixture()
def secrets_workspace(tmp_path, monkeypatch):
    """config.WORKSPACE_DIR patch 到 tmp_path 受控目录（默认路径回退基准）。"""
    ws = tmp_path / "workspace"
    ws.mkdir()
    monkeypatch.setattr(config, "WORKSPACE_DIR", ws)
    return ws


# ===========================================================================
# CP-A3-1 remember → lookup 闭环 + 0600 权限 + 不记住不落盘
# ===========================================================================

def test_cp_a3_1_remember_then_lookup_roundtrip(secrets_workspace):
    """落盘后命中返回原值（敏感 / 非敏感均可闭环；同 key 覆盖更新）。"""
    ws = secrets_workspace
    remember_secret("git_credential:github.com", _TOKEN, True, workspace_dir=ws)
    remember_secret("dataset_name", "cifar10", False, workspace_dir=ws)

    assert lookup_secret("git_credential:github.com", workspace_dir=ws) == _TOKEN
    assert lookup_secret("dataset_name", workspace_dir=ws) == "cifar10"
    assert lookup_secret("no_such_key", workspace_dir=ws) is None

    # 同 key 覆盖更新（多条目合并保留，不互相冲掉）。
    remember_secret("dataset_name", "imagenet", False, workspace_dir=ws)
    assert lookup_secret("dataset_name", workspace_dir=ws) == "imagenet"
    assert lookup_secret("git_credential:github.com", workspace_dir=ws) == _TOKEN


def test_cp_a3_1_default_workspace_fallback(secrets_workspace):
    """workspace_dir 不传时回退 config.WORKSPACE_DIR（运行期动态读取）。"""
    remember_secret("hf_token", _HF_TOKEN, True)  # 不传 workspace_dir
    path = secrets_workspace / config.SECRETS_FILE_NAME
    assert path.exists(), "默认路径应落在 config.WORKSPACE_DIR 下"
    assert lookup_secret("hf_token") == _HF_TOKEN


def test_cp_a3_1_file_permission_is_0600(secrets_workspace):
    """文件权限恰为 0600（stat.S_IMODE），覆盖写后仍保持。"""
    ws = secrets_workspace
    remember_secret("hf_token", _HF_TOKEN, True, workspace_dir=ws)
    path = ws / config.SECRETS_FILE_NAME
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    # 二次落盘（O_TRUNC 覆盖写路径）后权限不漂移。
    remember_secret("hf_token", "rotated-value", True, workspace_dir=ws)
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


def test_cp_a3_1_json_structure_on_disk(secrets_workspace):
    """落盘 JSON 结构恰为 {purpose_key: {"value", "is_sensitive"}}。"""
    ws = secrets_workspace
    remember_secret("hf_token", _HF_TOKEN, True, workspace_dir=ws)
    remember_secret("dataset_name", "cifar10", False, workspace_dir=ws)
    on_disk = json.loads((ws / config.SECRETS_FILE_NAME).read_text(encoding="utf-8"))
    assert on_disk == {
        "hf_token": {"value": _HF_TOKEN, "is_sensitive": True},
        "dataset_name": {"value": "cifar10", "is_sensitive": False},
    }


def test_cp_a3_1_no_remember_no_file(secrets_workspace):
    """不勾「记住」（即不调 remember_secret）则不落盘：lookup / load / mask
    等只读路径绝不创建 .secrets 文件。"""
    ws = secrets_workspace
    register_sensitive_value("session-only-value")
    assert lookup_secret("any_key", workspace_dir=ws) is None
    assert load_all_secrets(workspace_dir=ws) == {}
    assert mask_value("text with session-only-value") == "text with ****"
    assert not (ws / config.SECRETS_FILE_NAME).exists(), "只读路径不得创建 .secrets"


def test_cp_a3_1_load_all_secrets_returns_value_map_only(secrets_workspace):
    """load_all_secrets 只返回 {purpose_key: value} 映射（不带 is_sensitive）。"""
    ws = secrets_workspace
    remember_secret("hf_token", _HF_TOKEN, True, workspace_dir=ws)
    remember_secret("dataset_name", "cifar10", False, workspace_dir=ws)
    assert load_all_secrets(workspace_dir=ws) == {
        "hf_token": _HF_TOKEN,
        "dataset_name": "cifar10",
    }


# ===========================================================================
# CP-A3-2 gitignore 断言 + JSON 损坏 / 缺失非静默
# ===========================================================================

def test_cp_a3_2_secrets_covered_by_gitignore():
    """默认 workspace 路径下 .secrets 已被 `workspace/` 规则覆盖（AC-S4-09 部分）：
    git check-ignore 命中即忽略（returncode 0），无需新增 gitignore 行。"""
    proc = subprocess.run(
        ["git", "check-ignore", "-q", "workspace/.secrets"],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
    )
    assert proc.returncode == 0, "workspace/.secrets 必须被 .gitignore 覆盖"


def test_cp_a3_2_missing_file_returns_none_with_warning(secrets_workspace, caplog):
    """文件缺失：lookup 返回 None + caplog WARNING（失败非静默）。"""
    with caplog.at_level(logging.WARNING, logger="core.secrets_store"):
        assert lookup_secret("hf_token", workspace_dir=secrets_workspace) is None
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings, "文件缺失必须打 WARNING（非静默）"


def test_cp_a3_2_corrupt_json_returns_none_with_warning(secrets_workspace, caplog):
    """JSON 损坏：lookup 返回 None + WARNING；load_all_secrets 返回 {} + WARNING；
    损坏内容（可能含敏感值碎片）绝不进日志。"""
    ws = secrets_workspace
    path = ws / config.SECRETS_FILE_NAME
    path.write_text('{"broken": ' + _TOKEN, encoding="utf-8")  # 非法 JSON 且含 token

    with caplog.at_level(logging.WARNING, logger="core.secrets_store"):
        assert lookup_secret("broken", workspace_dir=ws) is None
        assert load_all_secrets(workspace_dir=ws) == {}
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) >= 2, "lookup 与 load 两路径均须 WARNING（非静默）"
    for record in caplog.records:
        assert _TOKEN not in record.getMessage(), "损坏文件内容不得进日志"


def test_cp_a3_2_top_level_not_dict_returns_none_with_warning(secrets_workspace, caplog):
    """结构异常（顶层非 dict 的合法 JSON）同样按损坏处理：None + WARNING。"""
    ws = secrets_workspace
    (ws / config.SECRETS_FILE_NAME).write_text('["not", "a", "dict"]', encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="core.secrets_store"):
        assert lookup_secret("any", workspace_dir=ws) is None
    assert any(r.levelno == logging.WARNING for r in caplog.records)


def test_cp_a3_2_corrupt_file_rebuilt_by_remember(secrets_workspace, caplog):
    """损坏文件不阻塞新「记住」：remember 以空表重建（WARNING 非静默），闭环恢复。"""
    ws = secrets_workspace
    (ws / config.SECRETS_FILE_NAME).write_text("not-json-at-all", encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="core.secrets_store"):
        remember_secret("hf_token", _HF_TOKEN, True, workspace_dir=ws)
    assert lookup_secret("hf_token", workspace_dir=ws) == _HF_TOKEN
    assert any(r.levelno == logging.WARNING for r in caplog.records)


# ===========================================================================
# CP-A3-3 mask_value（AC-S4-12 地基）
# ===========================================================================

def test_cp_a3_3_masks_remembered_sensitive_value(secrets_workspace):
    """已记住（.secrets is_sensitive=True）敏感值被替换为 ****。"""
    remember_secret("git_credential:github.com", _TOKEN, True, workspace_dir=secrets_workspace)
    text = f"fatal: could not clone https://user:{_TOKEN}@github.com/x/y"
    masked = mask_value(text)
    assert _TOKEN not in masked
    assert "****" in masked


def test_cp_a3_3_masks_process_registered_unremembered_value(secrets_workspace):
    """本次会话未「记住」的敏感值（仅进程内 set）同样被 mask（§9.4 内存旁路）。"""
    register_sensitive_value("session-secret-xyz")
    assert set(iter_sensitive_values()) == {"session-secret-xyz"}
    masked = mask_value("stderr echoes session-secret-xyz twice: session-secret-xyz")
    assert "session-secret-xyz" not in masked
    assert masked == "stderr echoes **** twice: ****"


def test_cp_a3_3_non_sensitive_value_not_masked(secrets_workspace):
    """非敏感项（is_sensitive=False）不 mask。"""
    remember_secret("dataset_name", "cifar10", False, workspace_dir=secrets_workspace)
    assert mask_value("training on cifar10 now") == "training on cifar10 now"


def test_cp_a3_3_substring_long_value_first_no_residue(secrets_workspace):
    """子串场景：短值是长值前缀时，长值优先替换，无 `****` + 明文尾巴残留。"""
    short = "tok-abc123"
    long = "tok-abc123456789"  # short 是 long 的前缀
    register_sensitive_value(short)
    register_sensitive_value(long)
    masked = mask_value(f"a={long} b={short}")
    assert masked == "a=**** b=****"
    assert "456789" not in masked, "短值先替换会留下长值尾巴明文"


def test_cp_a3_3_multi_source_mixed_lengths_no_plaintext(secrets_workspace):
    """多值混合（已记住 + 进程内、长短混合）全替换，无任何明文残留。"""
    ws = secrets_workspace
    remember_secret("hf_token", _HF_TOKEN, True, workspace_dir=ws)
    remember_secret("git_credential:github.com", _TOKEN, True, workspace_dir=ws)
    register_sensitive_value("shrt")
    text = f"log: {_HF_TOKEN} | {_TOKEN} | shrt | end"
    masked = mask_value(text)
    for secret in (_HF_TOKEN, _TOKEN, "shrt"):
        assert secret not in masked
    assert masked == "log: **** | **** | **** | end"


def test_cp_a3_3_none_and_empty_passthrough(secrets_workspace):
    """text 为 None / 空串返回原值（不炸、不误替换）。"""
    register_sensitive_value("whatever")
    assert mask_value(None) is None
    assert mask_value("") == ""


def test_cp_a3_3_register_ignores_none_and_empty():
    """空串 / None 不注册（防 mask 时把空串替换搞坏全文）。"""
    register_sensitive_value(None)
    register_sensitive_value("")
    assert list(iter_sensitive_values()) == []


def test_cp_a3_3_register_dedups_by_value():
    """值级去重：同值多次注册只保留一份。"""
    register_sensitive_value("dup-value")
    register_sensitive_value("dup-value")
    assert list(iter_sensitive_values()) == ["dup-value"]


# ===========================================================================
# CP-A3-4 build_credential_env（architecture §9.3）
# ===========================================================================

def test_cp_a3_4_no_credentials_still_has_git_terminal_prompt(secrets_workspace):
    """无凭证时仍无条件含 GIT_TERMINAL_PROMPT=0（R-S4-08），且不产生其它键 /
    不落任何脚本文件。"""
    env = build_credential_env()  # secrets=None → load_all_secrets() → {}
    assert env == {"GIT_TERMINAL_PROMPT": "0"}
    leftovers = [p for p in secrets_workspace.iterdir() if p.name.startswith(".git_askpass")]
    assert leftovers == [], "无 git 凭证不得生成 GIT_ASKPASS 脚本"


def test_cp_a3_4_git_credential_generates_askpass_script(secrets_workspace):
    """git_credential:github.com → GIT_ASKPASS 脚本：0700、落 workspace 下、
    内容仅 echo token；token 不出现在 env 的任何值中（只在 0700 脚本文件内）。"""
    env = build_credential_env({"git_credential:github.com": _TOKEN})

    assert env["GIT_TERMINAL_PROMPT"] == "0"
    script_path = Path(env["GIT_ASKPASS"])
    # 落 workspace 下。
    assert script_path.is_relative_to(secrets_workspace)
    assert script_path.exists()
    # 0700 权限。
    assert stat.S_IMODE(os.stat(script_path).st_mode) == 0o700
    # 内容仅 echo token（shebang + echo）。
    content = script_path.read_text(encoding="utf-8")
    assert content.startswith("#!/bin/sh")
    assert _TOKEN in content
    # token 不出现在 env 值以外任何路径：env 所有键值均无 token 明文。
    assert _TOKEN not in json.dumps(env), "token 只允许存在于 0700 脚本文件内"


def test_cp_a3_4_askpass_script_echoes_token(secrets_workspace):
    """脚本可执行且输出恰为 token（含单双引号的刁钻 token 经 shlex.quote 安全转义）。

    本地 /bin/sh echo，无网络。"""
    tricky = "tok'en\"$x`y"
    env = build_credential_env({"git_credential:github.com": tricky})
    proc = subprocess.run(
        ["/bin/sh", env["GIT_ASKPASS"]], capture_output=True, text=True, timeout=10,
    )
    assert proc.returncode == 0
    assert proc.stdout.strip() == tricky


def test_cp_a3_4_hf_token_maps_to_both_env_vars(secrets_workspace):
    """hf_token → HF_TOKEN + HUGGING_FACE_HUB_TOKEN 双变量映射。"""
    env = build_credential_env({"hf_token": _HF_TOKEN})
    assert env["HF_TOKEN"] == _HF_TOKEN
    assert env["HUGGING_FACE_HUB_TOKEN"] == _HF_TOKEN
    assert env["GIT_TERMINAL_PROMPT"] == "0"


def test_cp_a3_4_secrets_none_loads_from_dot_secrets(secrets_workspace):
    """secrets=None 时从 .secrets 装载（remember → build 闭环）。"""
    ws = secrets_workspace
    remember_secret("hf_token", _HF_TOKEN, True, workspace_dir=ws)
    remember_secret("git_credential:github.com", _TOKEN, True, workspace_dir=ws)
    env = build_credential_env()
    assert env["HF_TOKEN"] == _HF_TOKEN
    assert "GIT_ASKPASS" in env
    assert env["GIT_TERMINAL_PROMPT"] == "0"


def test_cp_a3_4_unknown_purpose_key_ignored(secrets_workspace):
    """未知 purpose_key（无 env 映射）忽略，不污染 env。"""
    env = build_credential_env({"wandb_api_key": "some-value", "dataset_name": "cifar10"})
    assert env == {"GIT_TERMINAL_PROMPT": "0"}
    assert "some-value" not in json.dumps(env)


def test_cp_a3_4_multiple_git_hosts_deterministic_first_with_warning(
    secrets_workspace, caplog,
):
    """多 host 时 GIT_ASKPASS 单值限制：取排序首个 + WARNING（日志只打 key 不打值）。"""
    with caplog.at_level(logging.WARNING, logger="core.secrets_store"):
        env = build_credential_env({
            "git_credential:gitlab.com": "token-b",
            "git_credential:github.com": "token-a",
        })
    # 排序首个 = github.com。
    content = Path(env["GIT_ASKPASS"]).read_text(encoding="utf-8")
    assert "token-a" in content
    assert "token-b" not in content
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings, "多 git 凭证必须 WARNING"
    for record in caplog.records:
        msg = record.getMessage()
        assert "token-a" not in msg and "token-b" not in msg


# ===========================================================================
# CP-A3-5 日志审计：全模块 logger 输出无 value 明文
# ===========================================================================

def test_cp_a3_5_all_module_logs_never_contain_secret_values(secrets_workspace, caplog):
    """遍历全部接口（remember / lookup / load / mask / build + 损坏路径），
    审计 core.secrets_store 全级别日志：任何记录不含敏感值明文（只打 purpose_key）。"""
    ws = secrets_workspace
    with caplog.at_level(logging.DEBUG, logger="core.secrets_store"):
        remember_secret("git_credential:github.com", _TOKEN, True, workspace_dir=ws)
        remember_secret("hf_token", _HF_TOKEN, True, workspace_dir=ws)
        remember_secret("dataset_name", "a3-nonsensitive-value", False, workspace_dir=ws)
        register_sensitive_value("a3-session-only-secret")
        lookup_secret("git_credential:github.com", workspace_dir=ws)
        lookup_secret("missing_key", workspace_dir=ws)
        load_all_secrets(workspace_dir=ws)
        mask_value(f"echo {_TOKEN} and a3-session-only-secret")
        build_credential_env(load_all_secrets(workspace_dir=ws))
        # 损坏路径（WARNING 分支）也纳入审计。
        (ws / config.SECRETS_FILE_NAME).write_text('{"x": "' + _TOKEN, encoding="utf-8")
        lookup_secret("x", workspace_dir=ws)

    secret_values = (_TOKEN, _HF_TOKEN, "a3-session-only-secret", "a3-nonsensitive-value")
    store_records = [r for r in caplog.records if r.name == "core.secrets_store"]
    assert store_records, "应有 secrets_store 日志产生（审计对象非空）"
    for record in store_records:
        msg = record.getMessage()
        for value in secret_values:
            assert value not in msg, f"日志泄漏 value 明文: {record.levelname} {msg!r}"


# ===========================================================================
# 测试工程师补强（2026-07-02 A3 验收，攻击者视角边界；反过度工程——只补真实攻击面）
# ===========================================================================

def test_hardening_mask_value_secret_with_regex_special_chars(secrets_workspace):
    """敏感值含正则/替换特殊字符（真实 token 常含 +/=$ 等 base64 类字符）：
    mask 必须按字面量替换（锚定 str.replace 语义——若未来重构为 re.sub 而未
    escape，本用例翻转，防正则注入/误匹配）。"""
    tricky_secret = r"tok.$[a-z]*\d+^(x)|y"
    register_sensitive_value(tricky_secret)
    masked = mask_value(f"stderr: auth failed for {tricky_secret} retry")
    assert tricky_secret not in masked
    assert masked == "stderr: auth failed for **** retry"
    # 形近但非字面量命中的文本不得被误 mask（正则解释才会波及）。
    lookalike = "tokX$Aaz]Qd+^_x_|y"
    assert mask_value(f"plain {lookalike}") == f"plain {lookalike}"


def test_hardening_mask_value_cross_overlapping_secrets_invariant(secrets_workspace):
    """交叉重叠敏感值（等长，排序不确定）：不变量 = 任何已知敏感值不得以
    明文完整出现在输出中（无论替换顺序）。"""
    register_sensitive_value("aaabbb")
    register_sensitive_value("bbbccc")
    masked = mask_value("prefix aaabbbccc suffix")
    assert "aaabbb" not in masked
    assert "bbbccc" not in masked
    assert "****" in masked


def test_hardening_mask_value_empty_sensitive_entry_no_explosion(secrets_workspace):
    """`.secrets` 中空值敏感条目（用户提交空输入被「记住」的真实场景）不得
    参与替换——空串 str.replace 会在每个字符间插入 ****，摧毁全部输出。"""
    remember_secret("empty_key", "", True, workspace_dir=secrets_workspace)
    assert mask_value("abc") == "abc"


@pytest.mark.parametrize("purpose_key,expected_marker", [
    ("git_credential:../../evil", "evil"),   # 路径穿越企图（purpose_key 可源自 LLM 工具调用）
    ("git_credential:", "default"),           # 空 host → default
    ("git_credential:host;rm -rf /", "rm"),   # shell 元字符 host
])
def test_hardening_askpass_host_sanitized_no_path_escape(
    secrets_workspace, purpose_key, expected_marker,
):
    """GIT_ASKPASS 脚本文件名的 host 段必须净化：`/` 等字符替换为 `_`，脚本
    只能落在 workspace 目录**正下方**，穿越企图不得逃逸（purpose_key 来自
    agent 工具调用，属不可信输入面）。"""
    env = build_credential_env({purpose_key: _TOKEN})
    script_path = Path(env["GIT_ASKPASS"])
    # 脚本必须直接位于 workspace 下（resolve 后仍在，杜绝 .. 逃逸）。
    assert script_path.resolve().parent == secrets_workspace.resolve()
    assert script_path.name.startswith(".git_askpass_")
    assert "/" not in script_path.name
    assert script_path.exists()
    assert expected_marker in script_path.name
    # 脚本仍可正确回显 token。
    proc = subprocess.run(
        ["/bin/sh", str(script_path)], capture_output=True, text=True, timeout=10,
    )
    assert proc.stdout.rstrip("\n") == _TOKEN


def test_hardening_remember_relocks_preexisting_loose_permissions(secrets_workspace):
    """`.secrets` 预先以宽权限存在（0666，手工创建/历史残留）时，remember
    必须把权限重锁回 0600（os.open 的 mode 仅在创建时生效，靠显式 chmod 兜底）。"""
    ws = secrets_workspace
    path = ws / config.SECRETS_FILE_NAME
    path.write_text("{}", encoding="utf-8")
    os.chmod(path, 0o666)
    remember_secret("hf_token", _HF_TOKEN, True, workspace_dir=ws)
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert lookup_secret("hf_token", workspace_dir=ws) == _HF_TOKEN
