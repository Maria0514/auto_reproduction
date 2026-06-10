"""S2-05 Streamlit 页面 1：论文输入（Sprint 2 任务 D3）。

架构参考：sprint2/architecture.md §2.9（session_state 字段 / 关键交互流程）。
dev-plan：sprint2/dev-plan.md 任务 D3（页面布局 / session_state 字段表 / CP-D3-1~6）。

页面职责（架构 §2.9）::

    侧栏：render_llm_config_form()（D1 组件）
    主区上半：arXiv ID 输入框 + "获取论文信息"按钮 → 即时展示论文卡片
    主区下半（P1 可选）：关键词搜索框 → reader.search 前 10 条候选
    底部："开始复现"按钮 → controller.start_task → 跳转 progress 页

页面入口约定（dev-plan CP-D3-1）::

    页面主函数命名为 ``render()``，可 ``from ui.pages.paper_input import render`` 导入。
    同时导出别名 ``render_paper_input_page = render`` 兼容 D2 app.py 路由 page_map
    （app.py L283 page_map 用 ("ui.pages.paper_input", "render_paper_input_page") 动态加载）。
    —— 这是与 D2 已落地路由对齐的唯一适配点，详见交付汇报"上游对接结论"。

关键硬约束（OBS-D1-01，D3 为最终落地点）::

    "开始复现"用的 llm_config_set 必须来自 render_llm_config_form() 的**返回值** cfg，
    **禁止直接读 st.session_state["llm_config_set"]**：D1 组件校验失败返回 None 时不清
    该 stale 键，直读会拿到过期配置。落地方式：cfg is None（配置未填全/不合法）或
    arxiv_id 为空时，**禁用"开始复现"按钮**（disabled=True）；即使被点到也在回调中
    再校验一次、st.error 提示且不调用 start_task（双保险）。
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional, Tuple

import streamlit as st
import streamlit_shadcn_ui as ui

from core.errors import AutoReproError
from core.state import LLMConfigSet
from core.tools.deepxiv_tools import DeepxivTools
from ui.components.llm_config_form import render_llm_config_form

logger = logging.getLogger(__name__)

__all__ = ["render", "render_paper_input_page"]


# session_state 键（架构 §2.9 表 + dev-plan §D3 表，与 D2 app.py 约定一致）。
_KEY_SELECTED_ARXIV = "selected_arxiv_id"
# arXiv ID 输入框 widget 的 key（唯一权威输入源；不再叠加 value= 双源反模式）。
_KEY_ARXIV_WIDGET = "arxiv_id_input"
# 搜索"选用"的待回填中间键（BUG-S2-D3-01）：非 widget 键，承载"选用"结果。
# 点"选用"时写本键 + st.rerun()；下一次 run 在 arxiv_id_input widget 实例化**之前**
# 消费本键灌入 widget key（实例化前写 widget key 合法）—— 绝不直写已实例化的 widget key。
_KEY_PENDING_ARXIV = "_input_pending_arxiv"
_KEY_CURRENT_PAGE = "current_page"
_KEY_THREAD_ID = "thread_id"
# 已提交标记：提交后所有控件 disabled=True，避免重复提交（dev-plan §D3「关键交互」3）。
_KEY_SUBMITTED = "_input_submitted"
# 已获取的论文卡片数据（brief + head 合并），跨 rerun 暂存供展示与 categories 校验。
_KEY_PAPER_CARD = "_input_paper_card"
# 获取论文卡片时的错误信息（跨 rerun 暂存）。
_KEY_FETCH_ERROR = "_input_fetch_error"


def _init_page_state() -> None:
    """初始化本页用到的 session_state 字段（不覆盖已有值）。"""
    st.session_state.setdefault(_KEY_SELECTED_ARXIV, "")
    # arxiv_id_input widget 初值经 setdefault 注入（单源：仅 key，无 value= 反模式）。
    st.session_state.setdefault(_KEY_ARXIV_WIDGET, "")
    st.session_state.setdefault(_KEY_CURRENT_PAGE, "input")
    st.session_state.setdefault(_KEY_THREAD_ID, None)
    st.session_state.setdefault(_KEY_SUBMITTED, False)
    st.session_state.setdefault(_KEY_PAPER_CARD, None)
    st.session_state.setdefault(_KEY_FETCH_ERROR, None)


def _get_controller():
    """从 session_state 取 D2 GraphController 单例（与 app.py::_get_controller 一致）。

    复用 app.py 的惰性单例逻辑，避免每次 rerun 重建（架构 §2.7 风险标注）。
    """
    from app import _get_controller as _app_get_controller

    return _app_get_controller()


def _is_non_cs(categories: List[str]) -> bool:
    """判定论文是否**不属于** CS 领域（无任一 ``cs.*`` 分类）。

    与 paper_intake 学科范围校验行为一致（非 CS 仅 WARNING 不阻塞，
    dev-plan §D3「关键交互」2 / paper_intake._map_intake_result）。
    """
    if not categories:
        # categories 为空无法判定，保守视为"不确定" → 不弹 WARNING（避免误报阻塞体验）。
        return False
    return not any(str(c).lower().startswith("cs.") for c in categories)


def _fetch_paper_card(arxiv_id: str) -> Tuple[Optional[Dict], Optional[str]]:
    """调用 deepxiv 即时拉取论文卡片数据，返回 (card | None, error | None)。

    deepxiv ``brief`` 仅返回 title / tldr / github_url / keywords 等快速摘要（reader.py
    L587-613），**不含 abstract / authors / categories**；后三者来自 ``head``（reader.py
    L560-585）。D3 卡片需要 title/abstract/authors/tldr/github_url + non-CS 校验需要
    categories，故合并 brief + head 两路结果（与 paper_intake "brief 为主 + head 补充"
    同源）。任一路失败时尽量降级展示已拿到的字段，不让整页崩溃。

    所有 deepxiv 异常已由 DeepxivTools 统一映射为 AutoReproError 子类
    （PermanentError / TransientError），此处捕获后转为 UI 错误文案。
    """
    arxiv_id = arxiv_id.strip()
    if not arxiv_id:
        return None, "请先输入 arXiv ID"

    tools = DeepxivTools()
    card: Dict = {"arxiv_id": arxiv_id}

    # --- brief（title / tldr / github_url）---
    try:
        brief = tools.get_paper_brief(arxiv_id)
    except AutoReproError as exc:
        logger.warning("[paper_input] get_paper_brief 失败: %s", exc)
        return None, f"获取论文摘要失败：{exc}"
    except Exception as exc:  # noqa: BLE001 - UI 层兜底，任何异常都转成可读文案
        logger.exception("[paper_input] get_paper_brief 未预期异常")
        return None, f"获取论文摘要失败：{exc}"

    card["title"] = brief.get("title") or ""
    card["tldr"] = brief.get("tldr")
    card["github_url"] = brief.get("github_url")
    card["keywords"] = brief.get("keywords") or []

    # --- head（abstract / authors / categories）；head 失败不阻断，降级展示 brief 字段 ---
    try:
        head = tools.get_paper_head(arxiv_id)
    except Exception as exc:  # noqa: BLE001 - head 是补充信息，失败仅降级不报死
        logger.warning("[paper_input] get_paper_head 失败（降级展示 brief）: %s", exc)
        head = {}

    if not card["title"]:
        card["title"] = head.get("title") or ""
    card["abstract"] = head.get("abstract") or ""
    card["authors"] = head.get("authors") or []
    card["categories"] = head.get("categories") or []

    return card, None


def _render_paper_card(card: Dict, disabled: bool) -> None:
    """渲染论文信息卡片（title / authors / categories / abstract / tldr / github_url）。

    non-CS 论文（无 cs.* 分类）显示 WARNING 卡片但不阻塞"开始复现"
    （dev-plan §D3「关键交互」2 / CP-D3-5）。
    """
    title = card.get("title") or "(无标题)"
    authors = card.get("authors") or []
    categories = card.get("categories") or []
    tldr = card.get("tldr")
    abstract = card.get("abstract")
    github_url = card.get("github_url")

    with st.container(border=True):
        st.markdown(f"### 📄 {title}")
        # 标题下方 caption 兜底（保留旧文案，AppTest 可见）。
        if authors:
            st.caption("作者：" + ", ".join(str(a) for a in authors))
        if categories:
            st.caption("分类：" + ", ".join(str(c) for c in categories))
            # 分类同时用 shadcn badges 上色（视觉），不影响 caption 兜底。
            ui.badges(
                badge_list=[(str(c), "outline") for c in categories],
                class_name="bg-blue-50 text-blue-700 border border-blue-200",
                key="b_paper_categories",
            )

        if _is_non_cs(categories):
            # 非 CS 领域：醒目 WARNING，但不阻塞（按钮可点，CP-D3-5）。
            # ui.alert 走 React 组件路径（ui-optimization-plan §3.1：warning 变体 + ⚠️ 图标）。
            # ⚠️ AppTest 不可见 ui.alert（它不会出现在 at.warning 元素树里），但保留
            # "不属于 CS" 关键文案在 description，文档化告知该测试断言需迁 e2e。
            ui.alert(
                title="⚠️ 该论文不属于 CS（cs.*）领域",
                description=(
                    "本系统针对 CS 论文复现优化，复现效果可能不佳。"
                    "仍可继续，但请知悉风险。"
                ),
                class_name="border-amber-300 bg-amber-50 text-amber-800",
                key="alert_non_cs",
            )

        if tldr:
            st.markdown(f"**TL;DR**：{tldr}")

        if abstract:
            # ui-optimization-plan §3.1：摘要从 st.expander 改为 ui.accordion（默认收起）。
            # 前端是 data.map(r=>...)，期待 list[{"title","content"}]，传 dict 会抛
            # "n.map is not a function"。
            ui.accordion(
                data=[{"title": "摘要（Abstract）", "content": abstract}],
                key="acc_abstract",
            )

        if github_url:
            # ui-optimization-plan §3.1：官方代码仓库链接 → ui.link_button。
            ui.link_button(
                text="🔗 官方代码仓库",
                url=str(github_url),
                variant="outline",
                class_name="border-blue-600 text-blue-700 hover:bg-blue-50",
                key="lb_github",
            )


def _render_search_section(disabled: bool) -> None:
    """主区下半（P1 可选）：关键词搜索 → reader.search 前 10 条候选。

    时间预算内已实现（reader.search size=10）；点击某条候选可一键填入上方 arXiv ID 框。
    """
    # ⚠️ ui-optimization-plan §3.1 建议外层用 ui.accordion，但 ui.accordion 仅接受
    # {标题: 字符串} 字典，无法在折叠面板内嵌入 ui.input/ui.button/搜索结果列表
    # 等子组件（streamlit-shadcn-ui 0.1.19 的 React 组件实现限制）。
    # TODO(ui-opt): 如未来 ui.accordion 支持嵌入子组件，再迁外层；当前保留 st.expander。
    with st.expander("🔍 按关键词搜索论文（可选）", expanded=False):
        # ui.input 不支持 disabled 参数；submitted 后会立即跳 progress 页，
        # 实际 paper_input 不会在 disabled 状态下被渲染，故无需退化处理。
        query = ui.input(
            default_value=st.session_state.get("search_query", ""),
            key="search_query",
            placeholder="例如：retrieval augmented generation",
        ) or ""
        do_search = ui.button(
            text="搜索",
            key="btn_search",
            variant="outline",
            class_name="border-blue-600 text-blue-700 hover:bg-blue-50",
        )
        if do_search and query.strip() and not disabled:
            try:
                tools = DeepxivTools()
                results = tools.search_papers(query.strip(), size=10)
            except Exception as exc:  # noqa: BLE001 - UI 层兜底
                logger.warning("[paper_input] search_papers 失败: %s", exc)
                st.error(f"搜索失败：{exc}")
                results = []
            st.session_state["_input_search_results"] = results

        results = st.session_state.get("_input_search_results") or []
        for idx, item in enumerate(results[:10]):
            aid = str(item.get("arxiv_id") or item.get("id") or "")
            title = item.get("title") or "(无标题)"
            with st.container(border=True):
                cols = st.columns([5, 1])
                cols[0].markdown(f"`{aid}` {title}")
                with cols[1]:
                    picked = aid and ui.button(
                        text="选用",
                        key=f"pick_{idx}",
                        variant="outline",
                        class_name="border-blue-600 text-blue-700 hover:bg-blue-50",
                    )
                if picked and not disabled:
                    # BUG-S2-D3-01 修复：禁止直写已实例化的 widget key arxiv_id_input
                    # （Streamlit 抛 StreamlitAPIException）。改写非 widget 的待回填中间键
                    # + rerun；由 render() 在 widget 实例化**之前**消费该键灌入 widget。
                    st.session_state[_KEY_PENDING_ARXIV] = aid
                    st.rerun()


def render() -> None:
    """页面主入口（dev-plan CP-D3-1：``from ui.pages.paper_input import render``）。

    渲染顺序：侧栏 LLM 配置表单 → 主区论文检索/卡片 → 底部"开始复现"按钮。
    """
    _init_page_state()

    submitted = bool(st.session_state.get(_KEY_SUBMITTED))

    # --- 侧栏：D1 LLM 配置表单。OBS-D1-01：用返回值 cfg，禁止直读 session_state ---
    with st.sidebar:
        prefill = st.session_state.get("llm_config_set")
        cfg: Optional[LLMConfigSet] = render_llm_config_form(default=prefill)

    st.title("论文自动复现 — 输入论文")

    # --- 主区上半：arXiv ID 输入 + 获取论文信息 ---
    # BUG-S2-D3-01 修复：在 widget 实例化**之前**消费搜索"选用"的待回填中间键。
    # 此刻 arxiv_id_input widget 尚未实例化，写其 session_state key 合法（作初值）。
    # 单源治理：text_input 仅用 key（无 value= 双源反模式），权威输入即 widget 自身 state。
    pending = st.session_state.pop(_KEY_PENDING_ARXIV, None)
    if pending is not None and not submitted:
        st.session_state[_KEY_ARXIV_WIDGET] = pending

    with st.container(border=True):
        st.markdown("### 🔎 输入 arXiv 论文 ID")
        # TODO(ui-opt §3.1): arxiv id 输入框计划迁 ui.input，但 streamlit-shadcn-ui
        # 0.1.19 的 ui.input 把 default_value 作为 React 组件 mount 时的 defaultValue，
        # 不会从 session_state[key] 读取预写值，且组件 re-mount 时机不可控——
        # BUG-S2-D3-01 的"在 widget 实例化前预写 session_state 灌初值"机制在
        # ui.input 上不可靠（搜索"选用"回填会失效）。同时 AppTest 通过
        # at.text_input(key="arxiv_id_input") 直接驱动该 widget，迁 ui.input 也会
        # 让 D3 单测整套断言失效。综合保留 st.text_input，以保 BUG-S2-D3-01 修复 +
        # AppTest 兼容；视觉上嵌入 ui.card（st.container border=True）已具备 shadcn 风格。
        arxiv_id = st.text_input(
            "arXiv ID",
            key=_KEY_ARXIV_WIDGET,
            placeholder="例如：2405.14831",
            disabled=submitted,
        )
        # selected_arxiv_id 作为对外暴露镜像（供其它页面 / 测试旁证读取），跟随 widget 当前值。
        st.session_state[_KEY_SELECTED_ARXIV] = arxiv_id

        # ui-optimization-plan §3.1：'获取论文信息' → ui.button(default 蓝色实心)。
        # ui.button 不支持 disabled；submitted 后会立即跳 progress 页，
        # 实际不会在 disabled 状态下被渲染，故无需退化处理。
        # FIX BUG-S2-D5-01：btn_fetch 两路径不同 key——
        # ui.button 写 dict 到 session_state["btn_fetch_go"]，
        # st.button 写 bool 到 session_state["btn_fetch"]，
        # 两个状态来回翻不会再串货（dict vs bool 类型冲突 → 'bool' object is not subscriptable）。
        if submitted:
            # 已提交但仍停留本页（极端边界）：退化为 st.button 以保 disabled 语义。
            # 旧 key "btn_fetch" 留给 disabled 路径（AppTest 看得到，断言/兼容靠它）。
            fetch = st.button("获取论文信息", key="btn_fetch", disabled=True)
        else:
            fetch = ui.button(
                text="获取论文信息",
                key="btn_fetch_go",
                variant="default",
                class_name="bg-blue-600 hover:bg-blue-700 text-white font-semibold",
            )
        if fetch:
            card, err = _fetch_paper_card(arxiv_id)
            st.session_state[_KEY_PAPER_CARD] = card
            st.session_state[_KEY_FETCH_ERROR] = err

        fetch_error = st.session_state.get(_KEY_FETCH_ERROR)
        if fetch_error:
            st.error(fetch_error)

    card = st.session_state.get(_KEY_PAPER_CARD)
    if card:
        _render_paper_card(card, disabled=submitted)

    # --- 主区下半（P1 可选）：关键词搜索 ---
    _render_search_section(disabled=submitted)

    st.divider()

    # --- 底部："开始复现" ---
    # OBS-D1-01 落地：cfg is None（配置未填全/不合法）或 arxiv_id 为空 → 禁用按钮。
    can_start = (cfg is not None) and bool(arxiv_id.strip()) and (not submitted)

    if cfg is None and not submitted:
        st.info("请在左侧侧栏填写有效的 LLM 配置后再开始复现。")
    if not arxiv_id.strip() and not submitted:
        st.info("请输入 arXiv ID 后再开始复现。")

    # FIX BUG-S2-D5-01：btn_start 两路径不同 key——
    # ui.button 写 dict 到 session_state["btn_start_go"]，
    # st.button 写 bool 到 session_state["btn_start"]，
    # 两个状态来回翻（侧栏配 LLM / 输入 arxiv_id 触发 can_start 翻 False→True）
    # 不会再串货（dict vs bool 类型冲突 → 'bool' object is not subscriptable）。
    start = ui.button(
        text="🚀 开始复现",
        key="btn_start_go",
        variant="default",
        class_name=(
            "bg-blue-600 hover:bg-blue-700 text-white font-bold "
            "px-6 py-3 text-base"
        ),
    ) if can_start else st.button(
        # disabled 路径：ui.button 不支持 disabled 参数；
        # 退化 st.button(disabled=True) 以保 OBS-D1-01 双保险 + AppTest 断言。
        # 旧 key "btn_start" 保留给 disabled 路径（AppTest 用此 key 断言）。
        "🚀 开始复现",
        key="btn_start",
        type="primary",
        disabled=True,
    )

    if start:
        # 双保险：即便按钮被点到（disabled 由前端约束，回调仍再校验一次）。
        if cfg is None:
            st.error("LLM 配置无效，请检查侧栏配置后重试。")
            return
        if not arxiv_id.strip():
            st.error("请输入 arXiv ID。")
            return

        controller = _get_controller()
        thread_id = controller.start_task(arxiv_id.strip(), cfg)

        st.session_state[_KEY_THREAD_ID] = thread_id
        st.session_state[_KEY_SUBMITTED] = True
        st.session_state[_KEY_CURRENT_PAGE] = "progress"
        st.rerun()


# D2 app.py 路由 page_map 期望函数名 render_paper_input_page（app.py L283）。
# 别名导出，避免改动已落地的 D2 路由（详见交付汇报"上游对接结论"）。
render_paper_input_page = render
