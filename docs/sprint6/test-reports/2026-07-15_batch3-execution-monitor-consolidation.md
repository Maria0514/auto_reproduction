# Sprint 6 批次 3 测试执行报告——过渡态批 G1 / execution_monitor 单收口窗口

- **日期**：2026-07-15
- **批次**：Sprint 6 批次 3（T-S6-3-1 ~ T-S6-3-4）
- **执行者**：主控（收口）
- **测试命令口径**：`.venv/bin/pytest -q -m "not e2e"`
- **相对基线**：sp6 批次 2 收官 1835 绿

## 1. 范围

全 Sprint 最高回归风险批（R-S6-1）。四任务：

| 任务 | 内容 | 落点文件 |
|---|---|---|
| T-S6-3-1 | 换代判定原语（死锁命门）：`get_interrupt_token`（复合三元 `id:指纹`）+ `resume_with` 三道防线（token 校验 + 原子 check-and-set）→bool + 模块级 `_THREAD_WORKERS` 登记表 + `has_active_worker` + `_reset_for_tests` | `app.py` |
| T-S6-3-2 | `get_phase(thread_id)->Dict` 阶段推导（snapshot.next 只读推断） | `app.py` |
| T-S6-3-3 | execution_monitor 单收口窗口五处共触碰：S6-01 过渡态 awaiting + S6-02 换代渲染 + §4.2 case 通则修订 + MF-7 logs 尾部（config `DEV_LOOP_PANEL_LOG_TAIL_CHARS=4000`）+ MF-4 裸标签 term_map + R7 孤儿卡片 | `ui/pages/execution_monitor.py` + `config.py` |
| T-S6-3-4 | analysis_progress 在途切页（case④bis 扩 active_node）+ `_segment_status` 段状态（active_node override，只向前升级不向后降级） | `ui/pages/analysis_progress.py` |

## 2. 新增测试（CP 覆盖）

| 文件 | 用例数 | 覆盖 CP |
|---|---|---|
| `tests/test_sprint6_s6_01_controller.py` | 17 | CP-3.1-1~5（换代 token 四场景 / 指纹只哈希 + R-S6-A1 退化 / resume_with token 校验 / 第三道防线存活 worker / _reset_for_tests）+ CP-3.2-1~2（get_phase） |
| `tests/test_sprint6_s6_01_execution_monitor.py` | 29 | CP-3.3-1~8（过渡态 + 换代反例防死锁 + 分发通则 + 在途标签 + MF-7 logs 尾部/占位/键零触碰 + MF-4 裸标签 + R7 孤儿卡片 + case 矩阵 ×3） |
| `tests/test_sprint6_s6_02_progress.py` | 8 | CP-3.4-1~2（case④bis active_node 切页 + _segment_status override 守卫） |

## 3. 回归适配（§9.4 只换不弱化）

生产两处行为变更引发的既有断言适配：

- **变更 A（resume_with 增 `expected_interrupt_token` kwarg + 返回 None→bool）**：
  - `test_sprint3_e1.py` / `test_sprint3_e1_reinforce.py`：签名黄金断言 + 公开方法白名单（纳入 get_interrupt_token/has_active_worker/get_phase）；
  - `test_sprint3_e2.py`（terminate/export/revise）、`test_sprint4_f1.py`（submit×2）、`test_sprint5_t23_degrade_button.py`（degrade×2 + normal_submit）：`resume_with` 实参断言追加 `expected_interrupt_token=controller.get_interrupt_token.return_value`（精确化非弱化）。
- **变更 B（MF-4 裸标签消除，AC-S6-20）**：`test_sprint3_e2_reinforce.py::test_g10`：`assert "hardware" in text`（原来自 execution_errors 裸标签）换为 `assert "硬件资源不足" in text`（humanize）+ `assert "[error_category=hardware]" not in text`（裸标签消失，语义更强）。

## 4. 过程发现（两个坑）

1. **R7 孤儿卡片对既有 case⑦ 测试的误伤规避**：新判据 `active_node 非空 ∧ not has_active_worker`。既有 7 个页面测试用 MagicMock controller，`get_phase` 返回 MagicMock（非 dict）。生产侧用 `isinstance(phase, dict)` 守卫 → MagicMock 直接回落 active_node=None → 孤儿分支不误触 → 既有 case⑦ 测试**零改动**通过。
2. **`_segment_status` 误降级修正**：集成测试 `test_i1_real_sqlite_saver_poll_state_roundtrip` 用 `update_state` 预置 checkpoint，导致 snapshot.next 被重置回图入口（paper_intake）与 current_step（paper_analysis）不一致。初版 active_node override 无脑把 paper_intake 段从 done 降级为 running（破断言）。修正为**只向前升级**（`node_idx >= cur_idx` 守卫）——比架构原文更严谨，只修滞后不降级历史段。

## 5. flaky 防护（R-S6-1）

execution_monitor + progress 页面级套件 ×3 连跑：**每轮 168 passed，零抖动**。
CP-3.3-8 case 矩阵（await/dev_panel/orphan/normal/cancelled）经 pytest parametrize ×3 内建。

## 6. 全量回归结果

- **`.venv/bin/pytest -q -m "not e2e"`**：**1888 passed / 25 skipped / 45 deselected / 0 failed**（137s，exit 0）。
- 相对 1835 基线净增 53 用例（controller 17 + monitor 29 + progress 8 = 54 新增，减历史微调），账目闭合，**零退化零失败**。
- **浏览器 flaky 说明**：`test_plan_review_e2e::test_e2e_code_only`（`@pytest.mark.browser`，chromium 点 iframe/expander 内「仅复现代码」按钮）在早前两次隔离跑中失败，本次全量跑通过——**间歇性时序 flaky**。已用 `git stash` 把批次 3 全部改动移除、在 baseline 上复现同样失败，**证明与批次 3 触碰的四个文件（app/config/execution_monitor/analysis_progress）无关，非本批引入**。

## 7. 结论

批次 3 CP-3.1~3.4 全绿 + execution_monitor 页面级 ×3 稳定 + 全量非 e2e 零退化（唯一失败为预存在浏览器 flaky）。**零 state / 零节点 / 零 interrupt payload 契约改动**红线守住。批次边界停手，等 Maria 确认再开批次 4。
