"""Sprint 5 任务 T-S5-2-1 自测：core/secrets_store.py（S5-01 地基，安全关键）。

覆盖 dev-plan #### 任务 T-S5-2-1 CP-2.1-1 ~ CP-2.1-4（architecture(s5) §5 / §9.2）：
    - CP-2.1-1 `env:X` 通用规则：`env:OPENAI_API_KEY` → env 注入；非 `env:` 未知
      key 忽略语义与 sp4 一致（零回归）；GIT_TERMINAL_PROMPT=0 无条件带不回归；
    - CP-2.1-2 第 7 接口 stash_session_secret：load_all_secrets 合并可见、
      `.secrets` 文件不含该值（不落盘断言）、mask_value 覆盖（脱敏地基）；
    - CP-2.1-3 会话层与 `.secrets` 同 key 覆盖语义（会话层优先，最后提交者胜）
      + 模块重载模拟"进程重启即失"；
    - CP-2.1-4 本模块日志只打 purpose_key 无 value 明文（caplog 审计，沿用
      CP-A3-5 范式）；既有六接口零退化由 tests/test_sprint4_a3.py 等既有子集
      回归覆盖（本文件补 lookup_secret 两级查找的架构师指定回归用例）。

架构师决策（2026-07-09，T-S5-2-1 咨询）：lookup_secret 扩展为"会话覆盖层优先 →
未命中再只读 `.secrets`"，与 load_all_secrets 合并语义严格一致——否则「不记住」
凭证在 gate 重跑重算 missing 时永不命中 → 无限 interrupt 幂等死循环。

测试策略：全 mock / tmp_path，禁真实网络；config.WORKSPACE_DIR monkeypatch 到
tmp_path 受控目录；模块级会话 dict 与 sensitive set 每用例前后清空，绝不污染
真实 workspace/.secrets（真实凭证在里面）；一律用带辨识后缀的哨兵假值。
"""

from __future__ import annotations

import importlib
import json
import logging
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
    remember_secret,
    stash_session_secret,
)

# 哨兵假值（带可辨识后缀防误撞真值；断言泄漏路径用）。
_FAKE_API_KEY = "t21-fake-openai-key-do-not-leak"
_FAKE_WANDB_KEY = "t21-fake-wandb-key-do-not-leak"
_FAKE_SESSION_VALUE = "t21-fake-session-secret-do-not-leak"
_FAKE_DISK_VALUE = "t21-fake-disk-secret-do-not-leak"


# ---------------------------------------------------------------------------
# fixtures（隔离纪律：模块级会话 dict + sensitive set + WORKSPACE_DIR 全隔离）
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clean_module_level_state():
    """每条用例前后清空进程内 sensitive set 与会话覆盖层（模块级全局，
    防跨用例污染；也防本文件污染同 session 其它测试文件）。"""
    secrets_store._SENSITIVE_VALUES.clear()
    secrets_store._SESSION_SECRETS.clear()
    yield
    secrets_store._SENSITIVE_VALUES.clear()
    secrets_store._SESSION_SECRETS.clear()


@pytest.fixture()
def secrets_workspace(tmp_path, monkeypatch):
    """config.WORKSPACE_DIR patch 到 tmp_path 受控目录（默认路径回退基准），
    绝不触碰真实 workspace/.secrets。"""
    ws = tmp_path / "workspace"
    ws.mkdir()
    monkeypatch.setattr(config, "WORKSPACE_DIR", ws)
    return ws


def _secrets_file(ws: Path) -> Path:
    return ws / config.SECRETS_FILE_NAME


# ===========================================================================
# CP-2.1-1 `env:X` 通用规则 + 未知 key 零回归 + GIT_TERMINAL_PROMPT 不回归
# ===========================================================================

def test_cp_2_1_1_env_purpose_key_injected(secrets_workspace):
    """`env:OPENAI_API_KEY` → env["OPENAI_API_KEY"]=value；无多余键。"""
    env = build_credential_env({"env:OPENAI_API_KEY": _FAKE_API_KEY})
    assert env == {
        "GIT_TERMINAL_PROMPT": "0",
        "OPENAI_API_KEY": _FAKE_API_KEY,
    }


def test_cp_2_1_1_multiple_env_keys_all_injected(secrets_workspace):
    """多个 `env:` key 全部注入（通用规则，不为 provider 做枚举映射）。"""
    env = build_credential_env({
        "env:OPENAI_API_KEY": _FAKE_API_KEY,
        "env:WANDB_API_KEY": _FAKE_WANDB_KEY,
    })
    assert env["OPENAI_API_KEY"] == _FAKE_API_KEY
    assert env["WANDB_API_KEY"] == _FAKE_WANDB_KEY
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert len(env) == 3


def test_cp_2_1_1_unknown_non_env_key_ignored_sp4_semantics(secrets_workspace):
    """非 `env:` 未知 purpose_key 行为与 sp4 完全一致：忽略、不污染 env
    （复刻 test_cp_a3_4_unknown_purpose_key_ignored 断言，零回归）。"""
    env = build_credential_env({"wandb_api_key": "some-value", "dataset_name": "cifar10"})
    assert env == {"GIT_TERMINAL_PROMPT": "0"}
    assert "some-value" not in json.dumps(env)


def test_cp_2_1_1_git_terminal_prompt_unconditional(secrets_workspace):
    """GIT_TERMINAL_PROMPT=0 无条件带不回归：空凭证 / 仅 env: 凭证两态均在。"""
    assert build_credential_env({}) == {"GIT_TERMINAL_PROMPT": "0"}
    env = build_credential_env({"env:OPENAI_API_KEY": _FAKE_API_KEY})
    assert env["GIT_TERMINAL_PROMPT"] == "0"


def test_cp_2_1_1_env_rule_coexists_with_sp4_rules(secrets_workspace):
    """`env:` 规则与既有 git/hf 规则共存互不干扰（既有映射零回归）。"""
    env = build_credential_env({
        "hf_token": "t21-fake-hf-token",
        "env:OPENAI_API_KEY": _FAKE_API_KEY,
        "dataset_name": "cifar10",  # 未知 key 仍忽略
    })
    assert env["HF_TOKEN"] == "t21-fake-hf-token"
    assert env["HUGGING_FACE_HUB_TOKEN"] == "t21-fake-hf-token"
    assert env["OPENAI_API_KEY"] == _FAKE_API_KEY
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert "cifar10" not in json.dumps(env)


def test_cp_2_1_1_env_empty_var_name_ignored_with_warning(secrets_workspace, caplog):
    """裸 `env:`（变量名为空）→ 忽略 + WARNING（非静默）；日志无 value 明文。"""
    with caplog.at_level(logging.WARNING, logger="core.secrets_store"):
        env = build_credential_env({"env:": _FAKE_API_KEY})
    assert env == {"GIT_TERMINAL_PROMPT": "0"}
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings, "空变量名必须 WARNING（非静默）"
    for record in caplog.records:
        assert _FAKE_API_KEY not in record.getMessage()


def test_cp_2_1_1_env_empty_value_skipped(secrets_workspace):
    """`env:X` 值为空 → 跳过（与 git/hf 规则跳过空值语义一致，不注入空串）。"""
    env = build_credential_env({"env:OPENAI_API_KEY": ""})
    assert env == {"GIT_TERMINAL_PROMPT": "0"}


# ===========================================================================
# CP-2.1-2 stash_session_secret：合并可见 + 不落盘 + mask 覆盖
# ===========================================================================

def test_cp_2_1_2_stash_visible_via_load_all_secrets(secrets_workspace):
    """stash → load_all_secrets 合并可见（.secrets 缺失时仅会话层）。"""
    stash_session_secret("env:OPENAI_API_KEY", _FAKE_SESSION_VALUE)
    assert load_all_secrets() == {"env:OPENAI_API_KEY": _FAKE_SESSION_VALUE}


def test_cp_2_1_2_stash_never_touches_secrets_file(secrets_workspace):
    """不落盘断言（其一）：stash + load + lookup 全链不创建 .secrets 文件。"""
    ws = secrets_workspace
    stash_session_secret("env:OPENAI_API_KEY", _FAKE_SESSION_VALUE)
    load_all_secrets()
    assert lookup_secret("env:OPENAI_API_KEY") == _FAKE_SESSION_VALUE
    assert not _secrets_file(ws).exists(), "会话覆盖层绝不落盘"


def test_cp_2_1_2_stash_not_written_into_existing_secrets_file(secrets_workspace):
    """不落盘断言（其二）：.secrets 已存在时，stash 后文件字节不变、
    不含会话值明文。"""
    ws = secrets_workspace
    remember_secret("hf_token", "t21-fake-hf-token", True, workspace_dir=ws)
    before = _secrets_file(ws).read_bytes()

    stash_session_secret("env:OPENAI_API_KEY", _FAKE_SESSION_VALUE)
    load_all_secrets()

    after = _secrets_file(ws).read_bytes()
    assert after == before, "stash 不得改动 .secrets 文件"
    assert _FAKE_SESSION_VALUE.encode() not in after


def test_cp_2_1_2_stash_value_masked(secrets_workspace):
    """mask_value 覆盖会话层值（值同步 register_sensitive_value，脱敏地基）。"""
    stash_session_secret("env:OPENAI_API_KEY", _FAKE_SESSION_VALUE)
    assert _FAKE_SESSION_VALUE in set(iter_sensitive_values())
    masked = mask_value(f"export OPENAI_API_KEY={_FAKE_SESSION_VALUE}")
    assert _FAKE_SESSION_VALUE not in masked
    assert masked == "export OPENAI_API_KEY=****"


def test_cp_2_1_2_stash_then_lookup_hits_without_file(secrets_workspace, caplog):
    """架构师指定回归用例：stash 后 lookup_secret 命中（会话层优先，gate 重跑
    不再重复 interrupt 的地基）、.secrets 文件不被创建、且不打"文件缺失"WARNING。"""
    ws = secrets_workspace
    with caplog.at_level(logging.WARNING, logger="core.secrets_store"):
        stash_session_secret("env:OPENAI_API_KEY", _FAKE_SESSION_VALUE)
        assert lookup_secret("env:OPENAI_API_KEY") == _FAKE_SESSION_VALUE
    assert not _secrets_file(ws).exists()
    assert not [r for r in caplog.records if r.levelno == logging.WARNING], (
        "会话层命中不触碰文件，不得打文件缺失 WARNING"
    )


def test_cp_2_1_2_stash_flows_into_credential_env(secrets_workspace):
    """闭环：stash（不记住）→ load_all_secrets 合并 → build_credential_env
    `env:` 规则注入沙箱 env（§9.2 扩接动机的端到端验证）。"""
    stash_session_secret("env:OPENAI_API_KEY", _FAKE_SESSION_VALUE)
    env = build_credential_env()  # secrets=None → load_all_secrets()（含会话层）
    assert env["OPENAI_API_KEY"] == _FAKE_SESSION_VALUE
    assert env["GIT_TERMINAL_PROMPT"] == "0"


def test_cp_2_1_2_stash_rejects_empty_key_or_value(secrets_workspace, caplog):
    """空 purpose_key / 空 value → 忽略 + WARNING（防空值遮蔽 .secrets 真值；
    非静默）；会话层保持为空。"""
    with caplog.at_level(logging.WARNING, logger="core.secrets_store"):
        stash_session_secret("", _FAKE_SESSION_VALUE)
        stash_session_secret("env:OPENAI_API_KEY", "")
    assert secrets_store._SESSION_SECRETS == {}
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 2
    for record in caplog.records:
        assert _FAKE_SESSION_VALUE not in record.getMessage()


# ===========================================================================
# CP-2.1-3 同 key 覆盖语义（会话层优先，最后提交者胜）+ 进程重启即失
# ===========================================================================

def test_cp_2_1_3_session_layer_overrides_disk_same_key(secrets_workspace):
    """会话层与 .secrets 同 key：load_all_secrets 与 lookup_secret 均会话层
    优先（两接口对同一 key 答案严格一致，无语义分叉）。"""
    ws = secrets_workspace
    remember_secret("env:OPENAI_API_KEY", _FAKE_DISK_VALUE, True, workspace_dir=ws)
    stash_session_secret("env:OPENAI_API_KEY", _FAKE_SESSION_VALUE)

    assert load_all_secrets()["env:OPENAI_API_KEY"] == _FAKE_SESSION_VALUE
    assert lookup_secret("env:OPENAI_API_KEY") == _FAKE_SESSION_VALUE
    # 磁盘值未被改动（覆盖只发生在读取面，不回写文件）。
    on_disk = json.loads(_secrets_file(ws).read_text(encoding="utf-8"))
    assert on_disk["env:OPENAI_API_KEY"]["value"] == _FAKE_DISK_VALUE


def test_cp_2_1_3_last_writer_wins_within_session(secrets_workspace):
    """同 key 重复 stash：最后提交者胜。"""
    stash_session_secret("env:OPENAI_API_KEY", "t21-first-value")
    stash_session_secret("env:OPENAI_API_KEY", _FAKE_SESSION_VALUE)
    assert load_all_secrets() == {"env:OPENAI_API_KEY": _FAKE_SESSION_VALUE}
    assert lookup_secret("env:OPENAI_API_KEY") == _FAKE_SESSION_VALUE
    # 旧值仍在 sensitive set（已经进过内存的值必须永远可 mask）。
    assert {"t21-first-value", _FAKE_SESSION_VALUE} <= set(iter_sensitive_values())


def test_cp_2_1_3_module_reload_simulates_process_restart(secrets_workspace):
    """模块重载模拟"进程重启即失"：reload 后会话层清空，读取面回落 .secrets
    基础值；磁盘「记住」的值不受影响。"""
    ws = secrets_workspace
    remember_secret("env:OPENAI_API_KEY", _FAKE_DISK_VALUE, True, workspace_dir=ws)
    stash_session_secret("env:OPENAI_API_KEY", _FAKE_SESSION_VALUE)
    stash_session_secret("env:WANDB_API_KEY", _FAKE_WANDB_KEY)  # 纯会话 key
    assert load_all_secrets()["env:OPENAI_API_KEY"] == _FAKE_SESSION_VALUE

    importlib.reload(secrets_store)  # 进程重启模拟：模块级 dict 全部重建
    try:
        assert secrets_store._SESSION_SECRETS == {}, "会话覆盖层进程重启即失"
        # 会话层消失：同 key 回落磁盘值；纯会话 key 彻底消失。
        merged = secrets_store.load_all_secrets()
        assert merged["env:OPENAI_API_KEY"] == _FAKE_DISK_VALUE
        assert "env:WANDB_API_KEY" not in merged
        assert secrets_store.lookup_secret("env:OPENAI_API_KEY") == _FAKE_DISK_VALUE
        assert secrets_store.lookup_secret("env:WANDB_API_KEY") is None
    finally:
        # 再 reload 一次恢复干净初态，防污染后续用例 / 其它测试文件。
        importlib.reload(secrets_store)


# ===========================================================================
# CP-2.1-4 日志审计：新增路径只打 purpose_key 无 value 明文（CP-A3-5 范式）
# ===========================================================================

def test_cp_2_1_4_new_paths_logs_never_contain_secret_values(secrets_workspace, caplog):
    """遍历本任务全部新增/改动路径（stash / 两级 lookup / 合并 load /
    `env:` 注入 / 空 key WARNING 分支），审计 core.secrets_store 全级别日志：
    任何记录不含敏感值明文（只打 purpose_key）。"""
    ws = secrets_workspace
    with caplog.at_level(logging.DEBUG, logger="core.secrets_store"):
        remember_secret("env:OPENAI_API_KEY", _FAKE_DISK_VALUE, True, workspace_dir=ws)
        stash_session_secret("env:OPENAI_API_KEY", _FAKE_SESSION_VALUE)
        stash_session_secret("env:WANDB_API_KEY", _FAKE_WANDB_KEY)
        stash_session_secret("", "t21-rejected-value")          # WARNING 分支
        lookup_secret("env:OPENAI_API_KEY")                     # 会话层命中
        lookup_secret("env:MISSING_KEY")                        # 两级都未命中
        load_all_secrets()                                      # 合并路径
        build_credential_env(load_all_secrets())                # env: 注入
        build_credential_env({"env:": "t21-empty-var-value"})   # 空变量名 WARNING

    secret_values = (
        _FAKE_DISK_VALUE, _FAKE_SESSION_VALUE, _FAKE_WANDB_KEY,
        "t21-rejected-value", "t21-empty-var-value",
    )
    store_records = [r for r in caplog.records if r.name == "core.secrets_store"]
    assert store_records, "应有 secrets_store 日志产生（审计对象非空）"
    for record in store_records:
        msg = record.getMessage()
        for value in secret_values:
            assert value not in msg, f"日志泄漏 value 明文: {record.levelname} {msg!r}"


def test_cp_2_1_4_session_layer_empty_zero_behavior_change(secrets_workspace):
    """会话层为空时六接口行为与 sp4 完全等价的抽查锚点（全量零退化由
    tests/test_sprint4_a3.py 等既有子集回归覆盖）：lookup 未命中 None、
    load 空 dict、build 仅 GIT_TERMINAL_PROMPT。"""
    ws = secrets_workspace
    assert lookup_secret("any_key", workspace_dir=ws) is None
    assert load_all_secrets(workspace_dir=ws) == {}
    assert build_credential_env() == {"GIT_TERMINAL_PROMPT": "0"}
    assert not _secrets_file(ws).exists()
