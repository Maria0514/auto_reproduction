"""Sprint 4 任务 B1 自测：core/tools/interaction_tools.py（S4-05，interrupt#3）。

覆盖 dev-plan §4 任务 B1 CP-B1-1 ~ CP-B1-5（architecture §2.1 / §7.1 / §8.3）：
    - CP-B1-1 mock interrupt：payload 四键契约完整（interrupt_kind / question /
      is_sensitive / purpose_key），purpose_key 空串规整为 None；
    - CP-B1-2 resume 契约：remember=False 不落盘；remember=True + purpose_key
      非空落 `.secrets`；非法 resume（None / 缺 value / 非 dict）→ 空串 +
      caplog WARNING（失败非静默）；
    - CP-B1-3 purpose_key 命中 `.secrets` → mock interrupt **0 次调用**，直接
      返回缓存值（AC-S4-09 后半，跨任务复用）；
    - CP-B1-4 敏感值旁路：is_sensitive=True 的值经 register_sensitive_value
      进程内可查、mask_value 可脱敏；返回值是纯 str（非 JSON，BUG-S1-02 刻意例外）；
    - CP-B1-5 docstring 字节级稳定（模块级常量逐字节断言，无动态变量）。

测试策略：全离线 / mock interrupt（monkeypatch 模块内 interrupt 符号）；
config.WORKSPACE_DIR monkeypatch 到 tmp_path 受控目录（secrets_store 运行期
动态读取，不在 import 期快照），进程内 sensitive set 前后清空，不污染真实
workspace（fixture 范式沿用 tests/test_sprint4_a3.py）。

模块导入用 importlib.import_module 拿真实子模块（防 __init__ callable 遮蔽，
known-bug 模式 6；core/tools/__init__.py 虽刻意不 re-export，此处仍锚定该范式）。
"""

from __future__ import annotations

import importlib
import json
import logging

import pytest

import config
from core import secrets_store
from core.secrets_store import (
    iter_sensitive_values,
    lookup_secret,
    mask_value,
    remember_secret,
)

interaction_tools = importlib.import_module("core.tools.interaction_tools")
request_user_input = interaction_tools.request_user_input
INTERRUPT_KIND_USER_INPUT = interaction_tools.INTERRUPT_KIND_USER_INPUT

_LOGGER_NAME = "core.tools.interaction_tools"

# CP-B1-5 锚定常量：request_user_input 的 docstring（= 工具 schema description，
# 参与 Prompt Cache 稳定前缀）。逐字节比较——模块侧任何改动（含空白 / 标点）
# 都会翻转本断言，防止动态变量 / 无意识 drift 破坏字节级幂等。
_EXPECTED_TOOL_DESCRIPTION = (
    "当缺少继续任务所需的信息（凭证 / 参数 / 决策 / 路径）时，向用户索要一条信息。\n"
    "\n"
    "    仅在确实无法从已有上下文推断、且信息缺失会阻塞任务时调用。一次只问一个信息项。\n"
    "    本工具会暂停任务等待用户回答：请单独一轮调用，不要与写文件 / 运行命令等\n"
    "    其他工具放在同一轮 tool_calls 中。\n"
    "\n"
    "    Args:\n"
    "        question: 给用户看的问题文本（中文叙述，URL/包名等事实层保留英文）。\n"
    "        is_sensitive: 凭证/密钥类置 True（UI 用 password 输入、全程脱敏、可「记住」）。\n"
    "        purpose_key: 信息项稳定标识（如 \"git_credential:github.com\" / \"hf_token\"），\n"
    "            用作 .secrets 的 key + 去重（同 key 命中已存则直接返回，不再打断用户）。\n"
    "\n"
    "    Returns:\n"
    "        用户输入的字符串值（敏感值不进 state / checkpoint）。"
)


# ---------------------------------------------------------------------------
# fixtures（范式沿用 tests/test_sprint4_a3.py）
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clean_process_sensitive_set():
    """每条用例前后清空进程内 sensitive set（模块级全局，防跨用例污染）。"""
    secrets_store._SENSITIVE_VALUES.clear()
    yield
    secrets_store._SENSITIVE_VALUES.clear()


@pytest.fixture()
def secrets_workspace(tmp_path, monkeypatch):
    """config.WORKSPACE_DIR patch 到 tmp_path 受控目录。

    B1 消费 A3 走挂账 L-A3-01 口径：工具内 lookup/remember 不透传 workspace_dir，
    一律回退 config.WORKSPACE_DIR（与 mask_value 基准一致），故测试只需 patch
    config 即可完全隔离 `.secrets` 落点。
    """
    ws = tmp_path / "workspace"
    ws.mkdir()
    monkeypatch.setattr(config, "WORKSPACE_DIR", ws)
    return ws


class _FakeInterrupt:
    """mock interrupt：记录每次 payload，按预设 resume 值返回（模拟
    Command(resume=...) 恢复后 interrupt() 的返回语义）。"""

    def __init__(self, resume):
        self.resume = resume
        self.calls: list = []

    def __call__(self, payload):
        self.calls.append(payload)
        return self.resume


@pytest.fixture()
def fake_interrupt(monkeypatch):
    """工厂 fixture：install(resume) → 把模块内 interrupt 符号替换为 recorder。"""

    def _install(resume):
        fake = _FakeInterrupt(resume)
        monkeypatch.setattr(interaction_tools, "interrupt", fake)
        return fake

    return _install


# ===========================================================================
# CP-B1-1 payload 四键契约 + purpose_key 空串转 None
# ===========================================================================

def test_cp_b1_1_payload_contract_four_keys(secrets_workspace, fake_interrupt):
    """interrupt payload 恰为四键（不多不少），各键值与入参一致，
    interrupt_kind 与模块常量 / §7.1 契约字面量三方相等。"""
    fake = fake_interrupt({"value": "x", "remember": False})
    result = request_user_input.invoke({
        "question": "需要 HuggingFace token 才能下载模型权重",
        "is_sensitive": True,
        "purpose_key": "hf_token",
    })
    assert result == "x"
    assert len(fake.calls) == 1, "无缓存命中时必须恰好 interrupt 一次"
    payload = fake.calls[0]
    assert set(payload.keys()) == {
        "interrupt_kind", "question", "is_sensitive", "purpose_key",
    }
    assert payload["interrupt_kind"] == INTERRUPT_KIND_USER_INPUT == "user_input_request"
    assert payload["question"] == "需要 HuggingFace token 才能下载模型权重"
    assert payload["is_sensitive"] is True
    assert payload["purpose_key"] == "hf_token"


def test_cp_b1_1_empty_purpose_key_normalized_to_none(secrets_workspace, fake_interrupt):
    """purpose_key 缺省（空串）→ payload 中规整为 None（§7.1：
    `"purpose_key": ... | null`）；is_sensitive 缺省为 False（严格 bool）。"""
    fake = fake_interrupt({"value": "cifar10", "remember": False})
    result = request_user_input.invoke({"question": "复现用哪个数据集？"})
    assert result == "cifar10"
    payload = fake.calls[0]
    assert payload["purpose_key"] is None
    assert payload["is_sensitive"] is False


# ===========================================================================
# CP-B1-2 resume 契约：不记住不落盘 / 记住落盘 / 非法 resume 空串 + WARNING
# ===========================================================================

def test_cp_b1_2_no_remember_returns_value_without_persist(secrets_workspace, fake_interrupt):
    """resume {"value": "x", "remember": False} → 返回 "x"，`.secrets` 不创建。"""
    fake_interrupt({"value": "x", "remember": False})
    result = request_user_input.invoke({
        "question": "q", "is_sensitive": True, "purpose_key": "hf_token",
    })
    assert result == "x"
    assert not (secrets_workspace / config.SECRETS_FILE_NAME).exists(), \
        "remember=False 不得落盘"
    assert lookup_secret("hf_token") is None


def test_cp_b1_2_remember_with_purpose_key_persists_then_returns(secrets_workspace, fake_interrupt):
    """resume {"value": "x", "remember": True} + purpose_key 非空 → 落 `.secrets`
    （结构 / is_sensitive 透传正确）后返回值。"""
    fake_interrupt({"value": "x", "remember": True})
    result = request_user_input.invoke({
        "question": "q", "is_sensitive": True, "purpose_key": "hf_token",
    })
    assert result == "x"
    path = secrets_workspace / config.SECRETS_FILE_NAME
    assert path.exists()
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk == {"hf_token": {"value": "x", "is_sensitive": True}}
    assert lookup_secret("hf_token") == "x"


def test_cp_b1_2_remember_without_purpose_key_not_persisted(secrets_workspace, fake_interrupt):
    """remember=True 但 purpose_key 空 → 无落盘 key，不写 `.secrets`（骨架
    `remember and purpose_key` 短路语义）。"""
    fake_interrupt({"value": "x", "remember": True})
    result = request_user_input.invoke({"question": "q"})
    assert result == "x"
    assert not (secrets_workspace / config.SECRETS_FILE_NAME).exists()


@pytest.mark.parametrize("bad_resume", [
    None,                  # resume 为 None（如 resume 通道异常）
    "just-a-string",       # 非 dict：裸字符串
    42,                    # 非 dict：标量
    ["value"],             # 非 dict：list
    {"remember": True},    # dict 但缺 value 键
    {},                    # 空 dict（缺 value 键）
])
def test_cp_b1_2_illegal_resume_returns_empty_with_warning(
    secrets_workspace, fake_interrupt, caplog, bad_resume,
):
    """非法 resume（非 dict / 缺 value）→ 返回空串 + WARNING（Q-F2 无前端
    降级语义，失败非静默），且不产生任何落盘副作用。"""
    fake_interrupt(bad_resume)
    with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
        result = request_user_input.invoke({"question": "q", "purpose_key": "hf_token"})
    assert result == ""
    warnings = [
        r for r in caplog.records
        if r.name == _LOGGER_NAME and r.levelno == logging.WARNING
    ]
    assert warnings, "非法 resume 必须打 WARNING（失败非静默）"
    assert not (secrets_workspace / config.SECRETS_FILE_NAME).exists()


def test_cp_b1_2_resume_value_none_coerced_to_empty_no_warning(
    secrets_workspace, fake_interrupt, caplog,
):
    """边界锁定：resume 含 value 键但值为 None → 合法 resume（不 WARNING），
    骨架 `str(resume.get("value") or "")` 规整为空串返回。"""
    fake_interrupt({"value": None, "remember": False})
    with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
        result = request_user_input.invoke({"question": "q"})
    assert result == ""
    assert not [
        r for r in caplog.records
        if r.name == _LOGGER_NAME and r.levelno == logging.WARNING
    ], "含 value 键即合法 resume，不得误报 WARNING"


# ===========================================================================
# CP-B1-3 purpose_key 命中 .secrets → 0 次 interrupt，直接返回缓存值
# ===========================================================================

def test_cp_b1_3_cache_hit_returns_without_interrupt(secrets_workspace, fake_interrupt):
    """`.secrets` 已存同 purpose_key → mock interrupt 0 次调用断言，直接返回
    缓存值（AC-S4-09 后半：跨任务复用，不重复打断用户）。"""
    remember_secret("git_credential:github.com", "cached-token-b1", True)
    fake = fake_interrupt({"value": "should-not-be-used", "remember": False})
    result = request_user_input.invoke({
        "question": "需要 GitHub token 才能 clone 私有仓库",
        "is_sensitive": True,
        "purpose_key": "git_credential:github.com",
    })
    assert result == "cached-token-b1"
    assert fake.calls == [], "缓存命中必须 0 次 interrupt"


def test_cp_b1_3_cache_miss_on_other_key_still_interrupts(secrets_workspace, fake_interrupt):
    """`.secrets` 有其它 key 但目标 purpose_key 未命中 → 正常 interrupt 一次
    （命中判定按 key 精确匹配，不误命中）。"""
    remember_secret("hf_token", "cached-hf", True)
    fake = fake_interrupt({"value": "fresh-value", "remember": False})
    result = request_user_input.invoke({
        "question": "q", "purpose_key": "git_credential:github.com",
    })
    assert result == "fresh-value"
    assert len(fake.calls) == 1


# ===========================================================================
# CP-B1-4 敏感值旁路：register_sensitive_value 进程内可查 + mask 可脱敏 + 纯 str
# ===========================================================================

def test_cp_b1_4_sensitive_value_registered_even_without_remember(
    secrets_workspace, fake_interrupt,
):
    """is_sensitive=True + remember=False：值不落盘，但**必须**进进程内
    sensitive set（本会话 mask 覆盖，§9.4 内存旁路），mask_value 可脱敏。"""
    fake_interrupt({"value": "sens-value-b1-do-not-leak", "remember": False})
    result = request_user_input.invoke({"question": "q", "is_sensitive": True})
    assert result == "sens-value-b1-do-not-leak"
    assert "sens-value-b1-do-not-leak" in set(iter_sensitive_values())
    masked = mask_value("stderr: auth with sens-value-b1-do-not-leak failed")
    assert "sens-value-b1-do-not-leak" not in masked
    assert masked == "stderr: auth with **** failed"
    assert not (secrets_workspace / config.SECRETS_FILE_NAME).exists()


def test_cp_b1_4_sensitive_value_registered_with_remember_too(
    secrets_workspace, fake_interrupt,
):
    """is_sensitive=True + remember=True：登记与落盘**双生效**（「无论是否
    记住均登记」的另一半），mask 经 .secrets 与进程内 set 双通道均覆盖。"""
    fake_interrupt({"value": "sens-remembered-b1", "remember": True})
    result = request_user_input.invoke({
        "question": "q", "is_sensitive": True, "purpose_key": "hf_token",
    })
    assert result == "sens-remembered-b1"
    assert "sens-remembered-b1" in set(iter_sensitive_values())
    assert lookup_secret("hf_token") == "sens-remembered-b1"
    assert mask_value("leak sens-remembered-b1 end") == "leak **** end"


def test_cp_b1_4_non_sensitive_value_not_registered(secrets_workspace, fake_interrupt):
    """is_sensitive=False：值不进 sensitive set、不被误 mask（脱敏面最小化，
    非敏感信息保持日志可读）。"""
    fake_interrupt({"value": "public-dataset-name", "remember": False})
    result = request_user_input.invoke({"question": "q", "is_sensitive": False})
    assert result == "public-dataset-name"
    assert list(iter_sensitive_values()) == []
    assert mask_value("using public-dataset-name now") == "using public-dataset-name now"


def test_cp_b1_4_return_is_plain_str_not_json(secrets_workspace, fake_interrupt):
    """返回值是纯 str（用户值裸串）：不带 json.dumps 引号包裹（架构 §2.1
    序列化治理注记——BUG-S1-02 JSON 范式适用于 dict/list 返回工具，本工具刻意例外）。"""
    fake_interrupt({"value": "plain-value", "remember": False})
    result = request_user_input.invoke({"question": "q", "is_sensitive": True})
    assert isinstance(result, str)
    assert result == "plain-value"
    assert result != json.dumps("plain-value"), "不得经 json.dumps 包裹（'\"plain-value\"'）"


# ===========================================================================
# CP-B1-5 docstring 字节级稳定（Prompt Cache 稳定前缀守门）
# ===========================================================================

def test_cp_b1_5_docstring_byte_identical_to_module_constant():
    """工具 description（= docstring，进工具 schema 参与 Prompt Cache）与
    测试侧锚定常量逐字节相等——docstring 内零动态变量的机制性证明。"""
    assert request_user_input.description == _EXPECTED_TOOL_DESCRIPTION
    assert request_user_input.name == "request_user_input"
    assert INTERRUPT_KIND_USER_INPUT == "user_input_request"


def test_cp_b1_5_docstring_contains_three_discipline_clauses():
    """docstring 纪律三重约束（dev-plan B1 第 3 点 / 架构 §8.3 缓解 2）：
    仅在阻塞时调用 / 一次只问一项 / 单独一轮调用不与其他工具同轮。"""
    desc = request_user_input.description
    assert "阻塞任务" in desc
    assert "一次只问一个信息项" in desc
    assert "单独一轮调用" in desc
    assert "不要与写文件 / 运行命令" in desc


def test_cp_b1_5_tool_schema_stable_three_args_with_defaults():
    """工具 schema 恰三参（question / is_sensitive / purpose_key）且缺省值
    稳定（is_sensitive=False / purpose_key=""）——多任务间 schema 字节级一致
    的结构面断言。"""
    args = request_user_input.args
    assert set(args.keys()) == {"question", "is_sensitive", "purpose_key"}
    assert args["is_sensitive"].get("default") is False
    assert args["purpose_key"].get("default") == ""


# ===========================================================================
# 验收补强（2026-07-03 B1 acceptance，@测试工程师代理）——只补真盲区：
#   1. GraphInterrupt 穿透工具包装（B1 侧不吞的机制锚定；react_base L599 误吞
#      为 BUG-S4-B1-01，属 react_base 缺口，由 B2 文件承接，本文件不放常红用例）；
#   2. 真实 interrupt 闭环（不 mock interrupt：暂停→payload→resume→落盘→跨线程
#      cache-hit，普通节点形态 = F1/GraphController 消费形态）；
#   3. 空值卡死去重 characterization（锚定现状：空值 remember 落盘 → 同 key
#      永久 cache-hit 空串不再问；F1 提交前非空校验为约定防线，若未来 B1 加
#      真值守卫本用例翻转即提醒更新）；
#   4. 偏差 e 声称核证：cache-hit 不重复 register，mask 由 .secrets
#      is_sensitive=True 条目直接覆盖；
#   5. 日志纪律动态审计：全路径 DEBUG 全捕无 value / question 全文；
#   6. 纯 str ToolMessage 下游语义：extract_last_tool_result 返回 None 不抛
#      （sp3 B2 验收先例，BUG-S1-02 例外的下游影响锚定）。
# ===========================================================================

def test_hardening_graphinterrupt_propagates_through_tool_wrapper(
    secrets_workspace, monkeypatch,
):
    """interrupt 抛 GraphInterrupt 时必须穿透 @tool 包装向上冒泡（无 resume 时
    暂停主图的机制前提）。若未来工具体内误加 try/except 吞掉，本用例翻红。

    注：react_base.tool_executor_node L596-601 的 `except Exception` 会在子图层
    吞掉本异常（BUG-S4-B1-01，B1 验收实证），修复与常驻断言归属 B2 harness 文件。
    """
    from langgraph.errors import GraphInterrupt

    def _raising_interrupt(payload):
        raise GraphInterrupt()

    monkeypatch.setattr(interaction_tools, "interrupt", _raising_interrupt)
    with pytest.raises(GraphInterrupt):
        request_user_input.invoke({"question": "q", "purpose_key": "hf_token"})


def test_integration_real_interrupt_closed_loop_plain_node(secrets_workspace):
    """真实 langgraph interrupt 闭环（不 mock interrupt，mock 层盲区补缺）：
    普通 StateGraph 节点内调工具 → __interrupt__ 暂停且 payload 四键穿透主图 →
    Command(resume) 恢复返回用户值 + remember 落盘 → 新线程同 key cache-hit
    0 interrupt（AC-S4-09 全闭环的图内形态）。全离线（InMemorySaver）。"""
    from typing import TypedDict

    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.graph import END, START, StateGraph
    from langgraph.types import Command

    class _S(TypedDict):
        got: str

    def _ask(state: _S) -> _S:
        val = request_user_input.invoke({
            "question": "需要 HF token",
            "is_sensitive": True,
            "purpose_key": "hf_token",
        })
        return {"got": val}

    builder = StateGraph(_S)
    builder.add_node("ask", _ask)
    builder.add_edge(START, "ask")
    builder.add_edge("ask", END)
    app = builder.compile(checkpointer=InMemorySaver())

    cfg = {"configurable": {"thread_id": "b1-real-1"}}
    paused = app.invoke({"got": ""}, cfg)
    intr = paused.get("__interrupt__")
    assert intr, "真实 interrupt 必须暂停图（__interrupt__ 存在）"
    payload = intr[0].value
    assert set(payload.keys()) == {
        "interrupt_kind", "question", "is_sensitive", "purpose_key",
    }
    assert payload["interrupt_kind"] == INTERRUPT_KIND_USER_INPUT

    resumed = app.invoke(Command(resume={"value": "real-tok", "remember": True}), cfg)
    assert resumed.get("got") == "real-tok"
    on_disk = json.loads(
        (secrets_workspace / config.SECRETS_FILE_NAME).read_text(encoding="utf-8")
    )
    assert on_disk == {"hf_token": {"value": "real-tok", "is_sensitive": True}}

    # 新线程（跨任务）同 purpose_key：cache-hit，不再 interrupt。
    cfg2 = {"configurable": {"thread_id": "b1-real-2"}}
    hit = app.invoke({"got": ""}, cfg2)
    assert "__interrupt__" not in hit
    assert hit.get("got") == "real-tok"


def test_characterization_empty_value_remember_dedup_deadlock(
    secrets_workspace, fake_interrupt,
):
    """【characterization，非期望行为背书】空值 + remember=True + purpose_key →
    落盘空串条目；后续同 key cache-hit 返回空串且 0 interrupt（"空值卡死去重"）。

    B1 层判定：符合架构 §2.1 骨架逐字语义（`cached is not None` / `remember and
    purpose_key`），不阻断；防线约定 = F1 UI 提交前非空校验（F1 验收必查）。
    若未来 B1 加真值守卫（如 `remember and purpose_key and value`），本用例
    翻红即提醒同步更新此锚定。value=None 经 `or ""` 规整后同样落空串，同一卡死面。"""
    fake_interrupt({"value": "", "remember": True})
    assert request_user_input.invoke({
        "question": "q", "is_sensitive": True, "purpose_key": "gh_tok",
    }) == ""
    on_disk = json.loads(
        (secrets_workspace / config.SECRETS_FILE_NAME).read_text(encoding="utf-8")
    )
    assert on_disk == {"gh_tok": {"value": "", "is_sensitive": True}}

    # 卡死实证：同 key 再问 → cache-hit 空串，0 次 interrupt，用户再无机会补值。
    fake = fake_interrupt({"value": "SHOULD-NOT-BE-ASKED", "remember": False})
    assert request_user_input.invoke({
        "question": "q", "is_sensitive": True, "purpose_key": "gh_tok",
    }) == ""
    assert fake.calls == [], "空串条目命中去重：卡死行为锚定"
    # 联动守卫：空串不进 sensitive set（register 空值守卫）、不摧毁 mask。
    assert "" not in set(iter_sensitive_values())
    assert mask_value("abc") == "abc"


def test_hardening_cache_hit_sensitive_masked_via_secrets_no_reregister(
    secrets_workspace, fake_interrupt,
):
    """偏差 e 声称核证：cache-hit 不重复 register_sensitive_value——mask 覆盖
    由 `.secrets` is_sensitive=True 条目直接提供（mask_value 敏感值全集 =
    .secrets is_sensitive 项 ∪ 进程内 set）。模拟重启恢复（进程内 set 为空）
    后 cache-hit，值仍可被脱敏。"""
    remember_secret("hf_token", "CACHED-SENS-TOK-B1", True)
    secrets_store._SENSITIVE_VALUES.clear()  # 模拟进程重启：内存旁路清零

    fake = fake_interrupt({"value": "X", "remember": False})
    result = request_user_input.invoke({
        "question": "q", "is_sensitive": True, "purpose_key": "hf_token",
    })
    assert result == "CACHED-SENS-TOK-B1"
    assert fake.calls == []
    assert list(iter_sensitive_values()) == [], "cache-hit 路径确实不重复 register"
    assert mask_value("leak CACHED-SENS-TOK-B1 end") == "leak **** end", \
        "mask 必须经 .secrets is_sensitive=True 条目直接覆盖（开发声称的机制）"


def test_hardening_no_value_or_question_plaintext_in_logs(
    secrets_workspace, fake_interrupt, caplog,
):
    """日志纪律动态审计（interrupt / cache-hit / 非法 resume 三路径 DEBUG 全捕）：
    value 与 question 全文绝不进任何日志（question 可能内嵌上下文片段）。"""
    q_marker = "UNIQUE-Q-MARKER-B1 内嵌敏感上下文"
    v_marker = "UNIQUE-V-MARKER-B1"
    with caplog.at_level(logging.DEBUG):
        # 路径 1：interrupt + remember 落盘
        fake_interrupt({"value": v_marker, "remember": True})
        request_user_input.invoke({
            "question": q_marker, "is_sensitive": True, "purpose_key": "hf_token",
        })
        # 路径 2：cache-hit（INFO 日志）
        fake_interrupt({"value": "X", "remember": False})
        request_user_input.invoke({
            "question": q_marker, "is_sensitive": True, "purpose_key": "hf_token",
        })
        # 路径 3：非法 resume（WARNING 日志）
        fake_interrupt(None)
        request_user_input.invoke({"question": q_marker, "purpose_key": "other_key"})
    joined = " || ".join(r.getMessage() for r in caplog.records)
    assert v_marker not in joined, "value 明文泄漏进日志"
    assert "UNIQUE-Q-MARKER-B1" not in joined, "question 全文泄漏进日志"


def test_hardening_extract_last_tool_result_plain_str_returns_none():
    """纯 str 返回例外的下游影响锚定（sp3 B2 验收先例）：request_user_input 的
    ToolMessage 是裸文本（非 JSON dict），`extract_last_tool_result` 对其返回
    None 且不抛——下游"从工具历史回填 dict 字段"的兜底对本工具天然不适用。"""
    from langchain_core.messages import ToolMessage

    from core.react_base import extract_last_tool_result

    msgs = [
        ToolMessage(
            content="my-secret-user-answer",
            tool_call_id="c1",
            name="request_user_input",
        ),
    ]
    assert extract_last_tool_result(msgs, "request_user_input") is None
    # json 可解析但非 dict 的用户输入（如纯数字）同样 None 语义。
    msgs2 = [
        ToolMessage(content="12345", tool_call_id="c2", name="request_user_input"),
    ]
    assert extract_last_tool_result(msgs2, "request_user_input") is None
