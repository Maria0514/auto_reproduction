# 论文自动复现 — UI 界面优化方案(Sprint 2)

> 面向产品负责人 Maria · Streamlit 前端 · 中文界面
> 配套高保真静态效果页:`./ui-mockup/index.html`(双击即可在浏览器打开)

## 目录

1. [设计目标与风格基调](#1-设计目标与风格基调)
2. [技术方案选型](#2-技术方案选型)
3. [逐页优化方案](#3-逐页优化方案)
4. [可加入的交互组件建议](#4-可加入的交互组件建议)
5. [待修 Bug 备注](#5-待修-bug-备注)
6. [落地节奏与风险/工作量评估](#6-落地节奏与风险工作量评估)

---

## 1. 设计目标与风格基调

### 设计目标
当前前端基本是 Streamlit 原生控件堆叠,信息密度高但缺乏层次与品牌感,看起来像"内部脚本工具"而非"产品"。本次优化目标:

- **专业但有温度**:保留工程工具的严谨与信息完整,同时通过留白、圆角、柔和分割线和友好图标(emoji)降低距离感。
- **亲和、不死板**:避免大段灰底表单的压抑感,用卡片化把信息分块、分组,给眼睛"呼吸空间"。
- **清晰层次**:用卡片、徽章(badge)、分隔线(separator)和指标卡(metric_card)建立主次关系,让用户一眼看到"现在在哪一步、要做什么决策"。
- **一致性**:三页共用同一套色板、间距、圆角与组件语义,形成统一视觉语言。

### 风格基调(关键词)
> 亲和 · 有温度 · 不死板 · 专业 · 蓝色主色 · 卡片化 · 适度圆角与留白 · 清晰层次

### 色板
| 角色 | 取值 | 用途 |
| --- | --- | --- |
| 主色 Primary | `#2563eb`(蓝) | 主按钮、链接、选中态、进度条、强调 |
| 主色浅 | `#dbeafe` / `#eff6ff` | 选中卡片底、徽章底、hover |
| 文字主 | `#0f172a` | 标题、正文 |
| 文字次 | `#64748b` | caption、说明、次要信息 |
| 边框 | `#e2e8f0` | 卡片细边框、分割线 |
| 成功/警示/危险 | `#16a34a` / `#d97706` / `#dc2626` | 状态徽章、alert、终止按钮 |
| 背景 | `#ffffff` 卡片 / `#f8fafc` 页面 | 白底为主,页面浅灰打底 |

## 2. 技术方案选型

采用"**主题打底 + 组件库主力 + 零件补缺**"三层叠加,改动小、收益大:

### 2.1 Streamlit 原生主题(打底层)— `.streamlit/config.toml`
负责全局基调:统一主色、背景、文字色、字体与圆角,让连原生控件也自动带上蓝色系品牌感。无需改业务代码即可全站生效。

```toml
[theme]
primaryColor = "#2563eb"          # 蓝色主色:按钮/选中/链接
backgroundColor = "#ffffff"        # 主区白底
secondaryBackgroundColor = "#f8fafc" # 侧栏/次级区浅灰
textColor = "#0f172a"             # 主文字
font = "sans serif"               # 走 system-ui 栈,不引外网字体
```
> 职责:**全局色彩/字体基线**。即使某些区域来不及换组件,也不会"半旧半新"割裂。

### 2.2 streamlit-shadcn-ui(主力组件库)
白底 / 卡片化 / 细边框圆角 / 高对比的现代风,与本方案视觉目标高度一致,且**与现有原生控件几乎一一对应**(迁移成本低)。

本项目实际会用到的组件(均为该库真实存在的 API):
`ui.card` · `ui.metric_card` · `ui.button(variant=...)` · `ui.link_button` · `ui.tabs` · `ui.badges` · `ui.alert` · `ui.alert_dialog` · `ui.accordion` · `ui.table` · `ui.input` · `ui.textarea` · `ui.select` · `ui.switch` · `ui.checkbox` · `ui.radio_group` · `ui.hover_card` · `ui.popover` · `ui.progress` · `ui.separator` · `ui.avatar` · `ui.pagination`。

调用示例:
```python
import streamlit_shadcn_ui as ui
ui.button(text="批准计划", key="approve", variant="default")
with ui.card(key="paper_card"):
    ui.element("span", children=["cs.LG"], className="...")
ui.metric_card(title="质量分", content="0.92", description="综合评估")
```
> 职责:**主力呈现层**。承担卡片、指标、按钮组、tabs、徽章、折叠、二次确认弹窗等绝大多数 UI。

### 2.3 streamlit-extras(零件补缺层)
只在 shadcn-ui 没有对应物、或想锦上添花时少量使用,例如:`stylable_container`(给个别容器加自定义渐变/阴影)、`grid` 布局微调、`add_vertical_space` 调节间距、`colored_header` 给分区标题加色条。
> 职责:**补缺与微调**,不喧宾夺主,能用 shadcn-ui 就不用它。

### 选型小结
| 层 | 工具 | 职责 | 占比 |
| --- | --- | --- | --- |
| 打底 | config.toml 主题 | 全局色/字体基线 | 一次性 |
| 主力 | streamlit-shadcn-ui | 卡片/指标/按钮/tabs/折叠/弹窗 | ~85% |
| 补缺 | streamlit-extras | 间距/容器样式/标题色条微调 | ~15% |

---

## 3. 逐页优化方案

### 3.1 paper_input(输入论文页 · 入口)

**现状(原生 st 控件)**:`st.title` 标题;侧栏 `st.form` + `st.text_input/st.selectbox` 做 LLM 配置;主区 `st.text_input`(arxiv id)+ `st.button`(获取论文信息);论文信息用 `st.markdown/st.caption/st.expander`(摘要折叠)拼;关键词搜索用 `st.expander` 包 `st.text_input + st.button`,结果用 `st.write` 逐行 + `st.button`(选用);非 CS 论文 `st.warning`。

**建议替换**:

| 区域 | 现在 | 建议 shadcn-ui |
| --- | --- | --- |
| arxiv 输入 + 获取 | text_input + button | `ui.input` + `ui.button(variant="default")`(蓝色主按钮) |
| 论文卡片 | markdown 拼 | `ui.card` 包裹;分类/作者用 `ui.badges`;摘要用 `ui.accordion`(默认收起) |
| 官方代码仓库链接 | markdown 链接 | `ui.link_button(text="🔗 官方代码仓库", url=...)` |
| 关键词搜索 | expander | `ui.accordion`(标题"🔍 按关键词搜索论文")内放 `ui.input`+`ui.button` |
| 搜索结果行 | write + button | 每行一个轻量 `ui.card`,右侧 `ui.button(variant="outline", text="选用")` |
| 非 CS 提示 | st.warning | `ui.alert`(warning 变体,带 ⚠️ 图标) |
| LLM 配置侧栏 | form | 保留 form,内部控件换 `ui.input/ui.select/ui.switch`,顶部加 `ui.avatar`+标题 |

**视觉/交互改进点**:论文卡片用 TL;DR 做视觉焦点(加粗、浅蓝底引用块);摘要默认折叠减少首屏噪音;"选用"按钮 hover 变蓝;非 CS 用柔和橙色 alert 而非生硬黄条。

### 3.2 analysis_progress(分析进度页)

**现状**:`st.title`;论文卡片同上;复现进度用 `st.columns` 一行 N 列,每列 `st.markdown`(emoji+名称+状态);实时日志用循环 `st.expander` 包 `st.code`;异常态 `st.error` + 两个 `st.button`(重试/返回);终止态 `st.warning`。

**建议替换**:

| 区域 | 现在 | 建议 shadcn-ui |
| --- | --- | --- |
| 论文卡片 | markdown | 复用 3.1 的 `ui.card` 组件(抽成公共函数) |
| 阶段进度 | columns+markdown | 顶部加 `ui.progress`(整体百分比);每阶段用小 `ui.card` 或 `ui.badges`(已完成=绿、进行中=蓝、待开始=灰) |
| 实时日志 | expander+code | `ui.accordion` 列表,标题=`节点名`+状态徽章,展开内嵌代码块;长列表配 `ui.pagination` |
| 异常态 | st.error+button | `ui.alert`(destructive)+ `ui.button(variant="default" 重试)` / `ui.button(variant="outline" 返回)` |
| 终止态 | st.warning | `ui.alert`(warning)+ 说明文案 |

**视觉/交互改进点**:进度条 + 阶段徽章让"进行到哪"一目了然;日志折叠默认收起、当前节点自动展开;状态用颜色编码统一(绿/蓝/灰/红)。

### 3.3 plan_review(计划审核页 · 最重要)

**现状**:标题区 markdown;`## 复现计划`(概述 + `st.columns` 两列 + `st.expander` 包 `st.json` 环境依赖 + `st.write` 数据准备列表 + `st.table`/`st.dataframe` 执行步骤 + `st.expander` 预期结果 json + 交付物列表);`## 候选代码仓库`(`st.caption` 资源策略 + 每仓库一个 `st.expander` 卡,选中态展开,内含 star/fork/质量分);`## 透明化信息`(`st.info` + `st.warning` + `st.expander` 错误列表);`## 决策`(5 个 `st.button`,批准为 primary,修改/切换内含 textarea/url,终止需二次确认)。

**建议替换**:

| 区域 | 现在 | 建议 shadcn-ui |
| --- | --- | --- |
| 复现计划概述 | markdown | `ui.card` + 顶部 `ui.badges`(策略 use_repo / 预估 6~8h) |
| 环境依赖 / 预期结果 json | expander+json | `ui.accordion`(标题清爽,内放格式化 code)— **顺带修标签叠字 bug** |
| 执行步骤 | st.table | `ui.table`(细边框、斑马纹) |
| 数据准备 / 交付物列表 | write | `ui.card` 内列表 + `ui.badges` 标记类型 |
| 候选仓库 | expander 卡 | 每仓库一个 `ui.card`,选中态浅蓝底+蓝边框;内嵌 3 个 `ui.metric_card`(质量分/Star/Fork);仓库名旁 `ui.link_button` 跳 GitHub;`ui.hover_card` 悬浮看更多指标 |
| 透明化信息 | info+warning | `ui.alert`(info)展示修改轮次/LLM 上限/降级节点;`ui.accordion` 收起最近错误 |
| 决策按钮组 | 5 个 button | `ui.button` 横向按钮组:批准=`variant="default"`(蓝/主)、仅复现代码/修改/切换=`variant="outline"`、终止=`variant="destructive"`(红);修改/切换点开用 `ui.popover` 弹出 `ui.textarea`/`ui.input`;终止用 `ui.alert_dialog` 二次确认 |

**视觉/交互改进点**:这页信息最密,卡片化分区后层次清晰;metric_card 让仓库质量"数字化、可比较";决策区按钮颜色语义化(蓝=推进、灰=备选、红=危险),终止有 alert_dialog 兜底防误触。

---

## 4. 可加入的交互组件建议

让界面更生动,但坚持"**不喧宾夺主**"——以下都是低打扰、提升信息获取效率的交互:

- **`ui.tabs` 分段切换**:plan_review 内"复现计划 / 候选仓库 / 透明化信息"可用 tabs 分页,减少长滚动;effect 页也可用 tabs 切"进度 / 日志"。
- **`ui.hover_card` 悬浮卡**:仓库名、降级节点名上悬浮显示更多上下文(如降级原因、仓库更新时间),不占首屏空间。
- **`ui.accordion` 可折叠**:摘要、环境依赖、预期结果、最近错误列表统一用折叠,默认收起、按需展开。
- **`ui.alert_dialog` 二次确认**:终止任务、切换仓库等"不可逆/高风险"操作弹确认框,防误触。
- **`ui.progress` 进度条**:分析进度页顶部整体百分比;获取论文/计划生成时给即时反馈。
- **`ui.popover` 行内输入**:修改计划反馈、切换仓库 URL 用 popover 就地展开,避免页面跳动。
- **`ui.badges` 状态徽章**:阶段状态、策略类型、分类标签统一徽章化,颜色编码一致。
- **`ui.separator` 柔和分割**:替代生硬的 `---`,分区更轻盈。

> 原则:动效克制(仅 hover/展开过渡),颜色克制(主色蓝 + 少量语义色),信息密度优先于花哨。

---

## 5. 待修 Bug 备注

**问题**:`plan_review` 页当前存在**控件标签文字重叠的渲染 bug**——折叠/卡片区域出现类似 `_arr` / `_array(environment)` / `card环境依赖` 的叠字(疑似 expander label 与内部变量名/key 串接渲染,或 markdown 标题与控件 label 重叠)。

**影响**:"环境依赖""预期结果"等折叠块标题显示错乱,影响专业观感与可读性。

**修复时机**:本次换组件时**一并修掉**。把这些 `st.expander` 迁移到 `ui.accordion` 后,标题改为显式、干净的中文字符串(如 `title="环境依赖"`),并与内部 `key`(用英文、唯一,如 `key="acc_env"`)彻底分离,避免 label 与 key/变量名串渲染。迁移后逐一目检三个折叠块标题正常。

**验收**:plan_review 页所有折叠块/卡片标题文字无叠字、无英文变量名泄漏。

---

## 6. 落地节奏与风险/工作量评估

### 落地节奏(三步走)
1. **演示层验证(本交付)**:先用本目录 `ui-mockup/index.html` 静态高保真页给 Maria 过效果与色板,确认风格基调,**零代码风险**。
2. **落地源码**:确认后再动 Streamlit 源码——先加 `.streamlit/config.toml` 主题打底(全站立即变蓝),再逐页把原生控件替换为 shadcn-ui 组件(建议顺序:plan_review → analysis_progress → paper_input,先啃最重要也最复杂的页),公共论文卡片抽成函数复用。
3. **重测**:每页替换后跑一遍真实流程(取论文 → 分析 → 审核 → 决策),重点回归:决策按钮回调、popover/alert_dialog 的状态回传、折叠默认态、叠字 bug 是否消除。

### 风险评估
| 风险 | 说明 | 缓解 |
| --- | --- | --- |
| 组件回调机制差异 | shadcn-ui 部分组件用 key + session_state 取值,与原生 button 的 `if st.button()` 写法不同 | 决策按钮先做一页打通回调模式,再复制 |
| 弹窗/popover 状态 | alert_dialog/popover 的开合与确认需走 session_state | 演示层已验证交互逻辑,落地照搬 |
| 主题与组件叠加 | config.toml 主色与组件默认色需对齐 | 统一 `#2563eb`,演示页已定基线 |
| 版本兼容 | shadcn-ui 与当前 Streamlit 版本兼容性 | 落地前先在分支锁版本冒烟测试 |

### 工作量评估
- **总体小于 antd 方案**:因为页面元素与 shadcn-ui 组件**一一对应**(card/metric_card/button/tabs/accordion/alert_dialog 直接覆盖现有控件),无需自定义封装大量组件,也不必处理 antd 那种重型布局/栅格体系的迁移。
- 粗估:主题打底 0.5 人日;plan_review 1.5 人日;analysis_progress 1 人日;paper_input 1 人日;联调+重测 1 人日。**合计约 5 人日**。
- 相较 antd(需引入更重的依赖、自封装中文适配与主题、栅格重排,预估 8~10 人日),本方案**省 40%+ 工作量**且视觉一致性更高。

---

> 📌 配套效果页:打开 `./ui-mockup/index.html` 即可预览三页高保真原型(顶部一句话标注为静态 mock)。
