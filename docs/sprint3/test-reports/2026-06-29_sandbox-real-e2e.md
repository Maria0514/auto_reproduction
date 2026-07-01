# 测试执行报告 - sandbox-real-e2e（真实执行「最后一跳」补缺）

- **日期**：2026-06-29
- **执行人**：@测试工程师代理
- **Sprint**：sprint3
- **触发原因**：补 `sandbox/local_venv.py` 真实执行路径的 e2e 覆盖缺口。项目至今所有 e2e
  都把 sandbox 三入口（prepare_venv / run_in_venv / collect_artifacts）mock 掉（见
  `tests/test_sprint3_c3.py::_patch_sandbox`、`tests/test_sprint3_e2e.py::FakeRunResult`），
  即 local_venv.py 真实实现（真建 venv / 真 pip install / 真 subprocess / 真收产物）
  从未在任何测试里被真实执行过。本次零 mock 真跑补上这条「最后一跳」。
- **commit**：3f885f6

## 执行范围

- 新增测试文件：`tests/test_sandbox_real_e2e.py`（8 条真实执行用例，双 marker `e2e` + `sandbox_real`）。
- marker 约定：`pytest.ini` 新增 `sandbox_real` marker；这些用例**同时打 `e2e`** 以便被
  `-m "not e2e"` 快速回归排除（不拖慢回归），显式 `-m sandbox_real` 触发真跑。
- 是否包含真实外部依赖：**真 subprocess + 真 pip install（联网装 numpy）**；
  **不需凭证、不耗 deepxiv/LLM 配额**（sandbox 层纯本地基础设施，无 LLM / 无 deepxiv 依赖）。
- 命令：
  ```
  .venv/bin/pytest tests/test_sandbox_real_e2e.py -m sandbox_real -v
  ```
- work_dir 全部落在 monkeypatch 后的受控 WORKSPACE_DIR（pytest tmp_path 下），
  绝不污染真实 workspace（沿用 `tests/test_sprint3_b1.py::sandbox_workspace` 范式）。

### 覆盖的真实场景（零 mock，全部真跑）

| 用例 | 真实场景 | 关键断言 |
|------|---------|---------|
| test_real_1 | prepare_venv 真建 venv + 真 pip 装 numpy | success=True；python_exe 真实存在且可执行；env_info 含 python_version + key_packages（numpy 在 freeze 里）；install_failed_packages 空；venv 内真 import numpy 成功 |
| test_real_2 | run_in_venv 真跑迷你复现脚本（import numpy 算 accuracy） | exit_code=0；stdout 含 `<METRICS>`/`</METRICS>` 标记 + accuracy=0.80；duration_seconds>0；timed_out=False；output_truncated=False；command 列表回填 |
| test_real_3 | collect_artifacts 真收 result.json / metrics.csv | 收到绝对路径；均真实存在且在 workspace 下；跳过 .venv 下文件（反证 venv 内文件零混入） |
| test_real_4 | **端到端迷你复现串联**（prepare→run→collect 全真实） | 真指标从真实 stdout 抽出（accuracy=0.8）；产物落盘内容自洽（result.json n=10）；stdout 指标 == 落盘指标 |
| test_real_5a | 超时强杀：脚本 sleep 30s + timeout=2s | timeout_out=True；duration≈timeout（1.5~10s）；后续代码未执行；exit_code≠0 |
| test_real_5b | pip 装不存在的包 | success=False；failed_packages 含该包；error 非空；不抛异常；venv 本身仍创建成功 |
| test_real_5c | work_dir 越界（/tmp，workspace 外） | prepare/run/collect 三入口均抛 SandboxCreationError |
| test_real_5d | 输出截断：脚本打印 200KB + output_max_bytes=10000 | output_truncated=True；含 truncated 标记；stdout 字节数受限 |

> 迷你复现脚本为 self-contained 确定性脚本（无随机、无 GPU 重训练），是任务「随便用一个论文试验」的务实版。
> 场景 3/5a/5c/5d 用纯标准库脚本，不依赖网络；场景 1/2/4/5b 真实联网装 numpy / 探测 pip 失败。

## 结果摘要

- 通过：8（连跑 3 次：8/8 → 8/8 → 8/8）
- 失败：0
- 跳过：0
- 警告：1（`LangChainPendingDeprecationWarning`，来自导入 `core.nodes.execution` → langgraph
  序列化模块，**非本测试代码引入的项目级既有 warning**，全量回归同样出现；记录在案，非本次缺陷）
- 单次耗时：run1 28.01s / run2 27.30s / run3 27.80s（**稳定，非 flaky**）

### 稳定性复跑（3 次）

| 轮次 | 结果 | 耗时 |
|------|------|------|
| run 1 | 8 passed | 28.01s |
| run 2 | 8 passed | 27.30s |
| run 3 | 8 passed | 27.80s |

结论：3/3 全绿，耗时方差 < 1s，无 flaky 迹象（含真 pip 联网装 numpy 的用例稳定）。

## 失败排查

无。8 条用例真实执行全部通过，**未发现 `sandbox/local_venv.py` 真实 bug**。

真实执行额外印证的契约（mock 此前无法验证）：
- venv `bin/python` 是指向系统解释器的符号链接，`_require_python_exe_within_workspace`
  的 lexical 校验（不解符号链接）在真实 venv 上正确放行——这是设计文档 §lexical 注释的真实落地验证。
- 护栏 1 超时 `start_new_session=True` + `os.killpg(SIGKILL)` 真实杀掉真 subprocess，
  duration 收敛在 timeout 附近，后续代码确未执行。
- pip 装不存在的包真实走「非瞬态 → 不重试 → 记入 failed_packages → success=False → 不抛异常」分级。

## 回归影响

- 全量非 e2e 回归：`.venv/bin/pytest -q -m "not e2e" --ignore=tests/test_paper_intake.py`
  → **1087 passed / 25 skipped / 43 deselected / 122.03s**。
- 与 handoff §5.1 基线（1087 passed / 25 skipped / 35 deselected）对比：passed 与 skipped
  **零退化**；deselected 35 → 43（+8 = 本次新增用例被 `-m "not e2e"` 正确排除），符合预期，
  **不拖慢快速回归**。
- 校验排除：`pytest tests/test_sandbox_real_e2e.py -m "not e2e" --co` → `8 deselected`（确认不进快速回归）。

## 后续动作

- 无遗留缺陷。`sandbox/local_venv.py` 真实执行路径首次被真实测试覆盖，「最后一跳」缺口已闭合。
- 触发约定：本套用例为本地真实执行，不耗配额，可随时 `-m sandbox_real` 复跑；后续若改动
  `sandbox/local_venv.py`，应把本套作为真实执行回归基线复跑一次。
