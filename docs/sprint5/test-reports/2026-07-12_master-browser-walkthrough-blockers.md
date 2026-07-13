# 2026-07-12 主控 Playwright 浏览器真实走查——"正常流程跑不通"卡点取证报告

- **执行人**：主控（Claude，Playwright 1.60 + chromium headless 驱动真实 Streamlit 应用）
- **背景**：Maria 反馈"正常流程根本跑不通"，授权主控用 Playwright 实际操作一遍找卡点
- **靶**：HippoRAG arXiv:2405.14831（deepxiv 已缓存，省配额）；真实 LLM + 真实 deepxiv + **真实 sandbox**（与 t53 的 mock sandbox 不同，本次是纯用户视角真跑）
- **走查线程**：`task-cdcd432cda49`（checkpoints.db 内可查，终态 = execution 节点挂起 user_input_request interrupt）
- **截图存档**：job tmp `shots/`（会话级，不入库；关键状态见下文文字取证）

## 一、走查时间线（真实用户路径）

| # | 动作 | 结果 | 耗时 |
|---|------|------|------|
| 1 | 打开首页 | 冷启动白屏 ~40s 后正常（次要） | ~40s |
| 2 | 填 arXiv ID → 获取论文信息 | ✅ 卡片正常（作者字段裸渲染 dict，次要缺陷） | ~20s |
| 3 | 🚀 开始复现 | ✅ intake→analysis→scout→planning 顺利，自动到计划审核页 | 131s |
| 4 | ✅ 批准计划 | ✅ 5s 后凭证 gate 出现（S5-01 设计生效，OPENAI_API_KEY） | 5s |
| 5 | 点「无此凭证，降级为模拟实验」 | ❌ **UI 从此永久冻结在凭证面板**（观察 15+ 分钟零变化、零反馈） | ∞ |
| 6 | （后端暗中推进） | coding 产码 → execution 真实 pip install → 修复循环 ×2 → agent **再次 interrupt 要同一个 OPENAI_API_KEY**；期间 pip 缓存 2.4GB 打爆 home 5G 配额 | ~15min |
| 7 | 用户唯一自救：F5 刷新 | ❌ session 全丢 → 回空白输入页，**运行中任务永久失联**（无任务列表/重连入口） | — |

结论：**正常流程在第 5 步（gate 降级提交）后对用户而言即死**——批准计划前的体验是完整顺畅的，问题全部集中在 approve 之后的"UI ↔ worker 异步协同"面上。

## 二、卡点清单（按严重度）

### 卡点 A（阻断级）：case⑤ 面板提交后停轮询死锁 → UI 永久冻结
- **现象**：凭证面板点「降级」后页面 15+ 分钟零变化，仍显示"当前阶段：制定计划 · 任务已暂停"，凭证表单原样挂着（再点一次 = 对新 interrupt 的误提交，双重 resume 风险）。
- **机制**（`ui/pages/execution_monitor.py`）：`_render_user_input_panel` 提交/降级 → `resume_with + st.rerun()`（:713/:733）；rerun 瞬间 worker 尚未消费 resume，`is_interrupted` 仍真 → 再次命中 case⑤ 重画面板；而 **case⑤ 是"停轮询"分支**（`st_autorefresh` 仅 case⑦ 注册，render() 头注释"停轮询正确性根基"）→ 无任何后续 rerun，永久冻结。竞态窗口 = worker 重放节点到 interrupt 点的全程（秒~分钟级），**实际必中**。
- **对照**：plan_review 的 approve 有 awaiting 轮询态兜底（本次走查 approve→gate 5s 自动转场正常），execution_monitor 的两类面板（user_input + dev_loop 决策）都没有。dev_loop `_submit`（:535）注释写"提交后 st.rerun() 让本页轮询自愈"——但 case⑤ 根本没有轮询可自愈，注释与实现矛盾。
- **修法候选**（待 PRD/架构）：提交后置 session_state awaiting 标志，命中 case⑤ 且 awaiting → 渲染"处理中"占位并注册 autorefresh，直到 interrupt 消失或换代（payload id 变化）再清标志。

### 卡点 B（环境级）：真实 pip install 缓存打爆 home 5G 配额
- **现象**：execution 装 HippoRAG 依赖（torch 全家桶，单轮 634M/865M 大轮子），pip HTTP 缓存默认写 `~/.cache/pip`，20:47–20:48 两分钟灌入 2.4GB → home 100% → 本机所有写 home 的进程遭殃（本次连截图都写失败）；execution 步骤相应报"文件路径错误/运行时异常"。
- **根因**：sandbox venv 建在 /data workspace 下没错，但 `run_command` 的 pip install 未加 `--no-cache-dir`（或未设 `PIP_CACHE_DIR` 到 workspace）。
- **走查后已手工 `rm -rf ~/.cache/pip` 恢复（2.4G 释放）**。
- **修法候选**：sandbox pip 统一 `--no-cache-dir` 或 `PIP_CACHE_DIR=<workspace>/pip-cache`（随任务清理）。

### 卡点 C（流程级）：gate 降级决策不贯穿下游——agent 把同一凭证再要一遍
- **现象**：用户已在 gate 显式声明"无此凭证，降级为模拟实验"，但 coding 仍产出真实调用 OpenAI 的代码；execution 跑挂 → 修复循环烧 2 轮真配额 → execution agent 通过 interaction 工具**再次发起 user_input_request 要 OPENAI_API_KEY**（问题原文："运行 HippoRAG 的 main.py 需要 OpenAI 兼容接口凭证…"）。checkpoint 取证：`fix_loop_count=2`，`degraded_nodes=[]`（降级未记账）。
- **定性**：t53 已留档"degrade 产码方差"（方差 4 现 2）；本次实锤方差之外的更深一层——**降级语义没有进入 coding prompt/execution 上下文**，agent 对"用户已拒绝过该凭证"零记忆，理论上可无限循环要钥匙+烧配额。与 TODO 里"no_metrics 修复循环无针对性引导"同族（下游收不到关键决策信息）。
- **修法候选**：降级决策写入 state（如 `degraded_credentials` 清单）→ 注入 coding/execution prompt（"必须产出不依赖该凭证的 mock 路径"）+ interaction 工具对已拒绝 purpose_key 直接短路返回降级指令。

### 卡点 D（恢复级）：刷新即失联——运行中任务无重连入口
- **现象**：UI 冻结后用户唯一操作是 F5；刷新 = 新 Streamlit session，`thread_id` 只存 session_state → 回空白输入页，运行中/挂起的任务从 UI 层面永久孤儿（checkpoint 里仍挂着 interrupt 等人）。
- **修法候选**：thread_id 入 query params（`st.query_params`）或落地"任务列表页"（checkpoints.db 枚举 thread + 状态 + 一键挂回）——后者对作品集叙事价值更高（会话恢复能力）。

## 三、次要观察（顺手取证，非阻断）

1. **作者字段裸渲染 dict**：论文卡片"作者：{'misc': {}, 'name': 'Bernal...'}"——paper_intake 的 authors 结构未 humanize（`ui/pages/paper_input.py` 卡片渲染处）。
2. **仓库卡片 Stars/Forks 显示"—"**：官方仓库选中但 stars/forks 数据缺失（质量分 0.82 正常），GitHub API 取数链路值得核查。
3. **paperswithcode API 已死**：`pwc _http_get_with_retry: 200 响应非 JSON`（pwc 网站 2025 年中已下线，接口回 HTML）——resource_scout 的 pwc 通道整体失效，靠 GitHub 兜底掩盖；建议摘除或换源。
4. **ReAct finalize JSON 解析失败重试**：`[paper_analysis]/[execution] finalize: failed to parse JSON inside <result> tag` 各现 1 次（有重试自愈，纯成本项）。
5. **首屏冷启动 ~40s 白屏**：import 期建图/checkpointer 所致，无 loading 提示（次要体验项）。
6. **章节名硬猜 miss**：日志多次 `Section 'Experiments'/'method' not found`——agent 猜章节名未命中真实目录（有 available sections 回显，agent 可自愈，纯成本项）。

## 四、与既有留档的关系

- 卡点 A 与 TODO「current_step 标签滞后/进度页 case④bis 不自动切页」（2026-07-10）同族但更重：那条是"显示滞后"，本条是"整页死锁"。sp5 修的是终态/interrupt 路由，**面板提交后的过渡态**是共同盲区。
- 卡点 C 与 TODO「no_metrics 修复循环无针对性引导」（2026-07-10）同构：均为"关键决策/判定信息不进 agent 上下文 → 修复循环空烧配额"。Sprint 6 若立"评测体系+引导信息贯穿"可一并治。
- 本次走查**未覆盖** AC-S5-13（活动流滚动）/AC-S5-21（产物路径区）：页面从未到达 case⑦ 正常渲染路径——被卡点 A 挡死，该两条继续挂账（也侧证卡点 A 的阻断性）。

## 五、配额与副作用账目

- deepxiv：HippoRAG 全缓存命中，增量消耗≈0；LLM：一次完整 intake→planning + coding×3（初次+修复2轮）+ execution agent 若干轮。
- 副作用：workspace/2405.14831/code 留有产物；checkpoints.db 新增 thread `task-cdcd432cda49`（挂起态，无后台进程）；home pip 缓存已清；playwright chromium 装于 /data/myproj/pw-browsers（~640M，home 以符号链接指向，后续走查可复用）。
- 走查用 streamlit/chromium 进程已全部停止，残留 0。
