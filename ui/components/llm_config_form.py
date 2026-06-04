"""S2-04 LLM 配置表单组件（多模型版本）。

架构参考：sprint2/architecture.md §2.8（§2.8.1 布局形态 A / §2.8.2 函数签名与校验
/ §2.8.3 校验逻辑伪代码 / §2.8.4 PRD §5.4 接入）。
PRD 对齐：§2.4 / §5.4 / AC-S2-11（Q-S2-01 RESOLVED 2026-05-18）。

布局形态（架构 §2.8.1 最终选择 A）::

    ┌─ 全局默认 LLM 配置（必填）─────────────────────────┐
    │ base_url / model / api_key (password) /          │
    │ temperature (slider 0~1) / max_tokens            │
    └──────────────────────────────────────────────────┘
    ▼ 为 paper_intake 节点单独配置（默认折叠，展开 = 覆写）
    ▼ 为 paper_analysis 节点单独配置
    ▼ 为 resource_scout 节点单独配置
    ▼ 为 planning 节点单独配置

关键约束（架构 §2.8.2 / dev-plan D1）::

- **不实现"测试连接"按钮**（Q-S2-01 RESOLVED，归 Sprint 3）；
- 4+1 条 api_key 各自独立 password mask，**仅写入 st.session_state**，**不持久化到磁盘**；
- 浏览器刷新（F5）后 session_state 丢失需重新输入（沿用 sp1 L-01 限制）；
- widget key 加前缀（``default_*`` / ``override_<node>_*``）避免 streamlit 同名冲突；
- ``default`` 形参允许回显已保存的 LLMConfigSet（页面重新渲染时）。
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import streamlit as st

from core.state import LLMConfig, LLMConfigSet, NodeName

__all__ = ["render_llm_config_form"]

# 支持节点级覆写的 4 个节点名（与 core.state.NodeName 强一致）。
_OVERRIDE_NODES: Tuple[NodeName, ...] = (
    "paper_intake",
    "paper_analysis",
    "resource_scout",
    "planning",
)

# 单条 LLMConfig 的字段默认值 / 范围（沿用 sp1 LLMConfig 5 字段）。
_BASE_URL_PLACEHOLDER = "https://api.openai.com/v1"
_MODEL_PLACEHOLDER = "gpt-4o-mini"
_TEMPERATURE_DEFAULT = 0.3
_TEMPERATURE_MIN = 0.0
_TEMPERATURE_MAX = 1.0
_MAX_TOKENS_DEFAULT = 4096
_MAX_TOKENS_MIN = 256
_MAX_TOKENS_MAX = 16384

# 状态键：组装成功的 LLMConfigSet 唯一权威落点（GraphController 据此读取）。
SESSION_KEY = "llm_config_set"


def _render_panel_widgets(
    prefix: str,
    prefill: Optional[LLMConfig],
) -> Dict[str, object]:
    """渲染单个 LLMConfig 的 5 个控件，返回原始（未校验）输入值。

    所有 widget 均带前缀 key，避免 streamlit 同名冲突（架构 §2.8.2 关键约束）。
    为避免 "value 参数 + session_state key 双源冲突" 告警（streamlit
    session-state 最佳实践），prefill 只通过 ``value=`` 注入，不预写 session_state。
    """
    base_url = st.text_input(
        "base_url",
        value=(prefill or {}).get("base_url", "") if prefill else "",
        placeholder=_BASE_URL_PLACEHOLDER,
        key=f"{prefix}_base_url",
    )
    model = st.text_input(
        "model",
        value=(prefill or {}).get("model", "") if prefill else "",
        placeholder=_MODEL_PLACEHOLDER,
        key=f"{prefix}_model",
    )
    api_key = st.text_input(
        "api_key",
        value=(prefill or {}).get("api_key", "") if prefill else "",
        type="password",  # CP-D1-10：独立 mask，仅入 session_state，不落盘。
        key=f"{prefix}_api_key",
    )
    temperature = st.slider(
        "temperature",
        min_value=_TEMPERATURE_MIN,
        max_value=_TEMPERATURE_MAX,
        value=float((prefill or {}).get("temperature", _TEMPERATURE_DEFAULT))
        if prefill
        else _TEMPERATURE_DEFAULT,
        step=0.05,
        key=f"{prefix}_temperature",
    )
    max_tokens = st.number_input(
        "max_tokens",
        min_value=_MAX_TOKENS_MIN,
        max_value=_MAX_TOKENS_MAX,
        value=int((prefill or {}).get("max_tokens", _MAX_TOKENS_DEFAULT))
        if prefill
        else _MAX_TOKENS_DEFAULT,
        step=128,
        key=f"{prefix}_max_tokens",
    )
    return {
        "base_url": base_url,
        "model": model,
        "api_key": api_key,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }


def _validate_panel(raw: Dict[str, object], scope_label: str) -> Tuple[Optional[LLMConfig], List[str]]:
    """校验单条 panel 的原始输入，返回 (LLMConfig | None, 错误信息列表)。

    校验规则（架构 §2.8.2）：base_url / model / api_key 非空（trim 后）；
    temperature ∈ [0.0, 1.0]；max_tokens ∈ [256, 16384]。
    任一不合法则返回 (None, [行内错误信息...])。
    """
    errors: List[str] = []

    base_url = str(raw.get("base_url", "")).strip()
    model = str(raw.get("model", "")).strip()
    # api_key 不做 strip（密钥两端理论上不应有空白，但避免把含义性字符吃掉，
    # 仅判定"是否为空"用 strip 后的结果，存储用原值）。
    api_key_raw = str(raw.get("api_key", ""))
    api_key = api_key_raw if api_key_raw.strip() else ""

    if not base_url:
        errors.append(f"[{scope_label}] base_url 不能为空")
    if not model:
        errors.append(f"[{scope_label}] model 不能为空")
    if not api_key:
        errors.append(f"[{scope_label}] api_key 不能为空")

    try:
        temperature = float(raw.get("temperature", _TEMPERATURE_DEFAULT))
    except (TypeError, ValueError):
        errors.append(f"[{scope_label}] temperature 非法")
        temperature = _TEMPERATURE_DEFAULT
    else:
        if not (_TEMPERATURE_MIN <= temperature <= _TEMPERATURE_MAX):
            errors.append(
                f"[{scope_label}] temperature 必须在 "
                f"[{_TEMPERATURE_MIN}, {_TEMPERATURE_MAX}] 之间（当前 {temperature}）"
            )

    try:
        max_tokens = int(raw.get("max_tokens", _MAX_TOKENS_DEFAULT))
    except (TypeError, ValueError):
        errors.append(f"[{scope_label}] max_tokens 非法")
        max_tokens = _MAX_TOKENS_DEFAULT
    else:
        if not (_MAX_TOKENS_MIN <= max_tokens <= _MAX_TOKENS_MAX):
            errors.append(
                f"[{scope_label}] max_tokens 必须在 "
                f"[{_MAX_TOKENS_MIN}, {_MAX_TOKENS_MAX}] 之间（当前 {max_tokens}）"
            )

    if errors:
        return None, errors

    cfg: LLMConfig = {
        "base_url": base_url,
        "model": model,
        "api_key": api_key,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    return cfg, []


def _panel_is_blank(raw: Dict[str, object]) -> bool:
    """判定 override 卡片是否 "全空"（视为不覆写）。

    "全空" = 3 个文本字段（base_url / model / api_key）全部为空白。
    temperature / max_tokens 控件天然有默认值不可能为空，故不参与"是否开启覆写"判定，
    只在已开启覆写时参与合法性校验（架构 §2.8.2：任填即视为开启覆写）。
    """
    return not any(
        str(raw.get(field, "")).strip()
        for field in ("base_url", "model", "api_key")
    )


def render_llm_config_form(
    default: Optional[LLMConfigSet] = None,
) -> Optional[LLMConfigSet]:
    """渲染 LLM 配置侧栏表单（多模型版本，Q-S2-01 RESOLVED 2026-05-18）。

    Args:
        default: 可选，已保存的 LLMConfigSet，用于页面重新渲染时回显（prefill）。

    Returns:
        校验全部通过时返回组装好的 LLMConfigSet，并写入
        ``st.session_state["llm_config_set"]``；任一条校验失败则行内 ``st.error()``
        提示并返回 ``None``（不阻塞主页面渲染）。
    """
    st.subheader("LLM 配置")

    # --- 全局默认 panel（必填）---
    st.markdown("**全局默认 LLM 配置（必填）**")
    default_prefill = default.get("default") if default else None
    raw_default = _render_panel_widgets(prefix="default", prefill=default_prefill)
    cfg_default, default_errors = _validate_panel(raw_default, scope_label="全局默认")

    if cfg_default is None:
        # 必填未通过：行内逐条提示，不返回。
        for msg in default_errors:
            st.error(msg)
        return None

    # --- 4 个节点级 override expander（默认折叠，展开=覆写）---
    overrides: Dict[str, LLMConfig] = {}
    override_failed = False

    for node_name in _OVERRIDE_NODES:
        prefill = (
            default.get("overrides", {}).get(node_name) if default else None
        )
        # 若该节点有 prefill（曾覆写过），expander 默认展开方便用户看到已有配置。
        with st.expander(
            f"为 {node_name} 节点单独配置（可选，展开并填写 = 覆写）",
            expanded=prefill is not None,
        ):
            raw = _render_panel_widgets(
                prefix=f"override_{node_name}", prefill=prefill
            )
            if _panel_is_blank(raw):
                # 全空 = 不覆写，跳过（不写入 overrides）。
                continue
            cfg, errors = _validate_panel(raw, scope_label=node_name)
            if cfg is None:
                # 开启了覆写但校验失败：行内提示并标记失败。
                for msg in errors:
                    st.error(msg)
                override_failed = True
                continue
            overrides[node_name] = cfg

    if override_failed:
        return None

    config_set: LLMConfigSet = {"default": cfg_default, "overrides": overrides}
    st.session_state[SESSION_KEY] = config_set
    return config_set
