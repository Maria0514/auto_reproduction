# Sprint 3 核心架构设计文档

**文档版本**：v1.0
**日期**：2026-06-15
**作者**：架构师代理
**对应 PRD**：`docs/sprint3/prd.md` v1.0（正式版）
**前置评估**：`docs/sprint3/dev-loop-compatibility-matrix.md`（§A 字段处置 / §B 预算总账 / §C 主图骨架不变性 / §D.3 两个 must-fix / §D.6 设计约束）
**体例参照**：`docs/sprint2/architecture.md`

> 本文档把 PRD 中 6 处「建议转架构师」的技术实现细节全部落地为可执行的架构设计：① sandbox 护栏跨平台落点；② 错误分类到执行反馈层的映射；③ 第二个 interrupt 触发位置 + resume 三态路由；④ code_only 路由衔接；⑤ 预算单点回写点；⑥ 主图加边方式。所有设计满足 PRD 的 8 项已拍板决策（Q-S3-01~Q-S3-07 + UI 范围），不推翻需求；技术上的疑问/风险单列于 §7。

---

## 目录

1. [Sprint 3 架构总览](#1-sprint-3-架构总览)
2. [模块详细设计](#2-模块详细设计)
3. [数据流图](#3-数据流图)
4. [关键设计决策](#4-关键设计决策)
5. [state 字段处置表](#5-state-字段处置表)
6. [对 sp1sp2 既有代码的改动点清单](#6-对-sp1sp2-既有代码的改动点清单)
7. [架构师回问与风险提示](#7-架构师回问与风险提示)
8. [给开发代理的实现约束清单](#8-给开发代理的实现约束清单)

---

## 1. Sprint 3 架构总览

### 1.1 Sprint 3 涉及的新模块与扩展模块

| 类型 | 路径 | 来源 | 说明 |
|---|---|---|---|
| **新增** | `sandbox/__init__.py` | S3-01 | 新建 sandbox 包 |
| **新增** | `sandbox/local_venv.py` | S3-01 | 本地 venv 执行环境 + 4 项护栏 |
| **新增** | `core/nodes/coding.py` | S3-02 | coding 真实现（ReAct wrapper，复用 `_make_react_wrapper`） |
| **新增** | `core/nodes/execution.py` | S3-03 | execution 真实现 + 错误分类 + metrics 解析 |
| **新增** | `core/nodes/reporting.py` | S3-05 | reporting 真实现（三形态 Markdown） |
| **改动** | `core/graph.py` | S3-04/06/07 | coding/execution/reporting 位换真实现 + 加修复回边 + code_only 路由 + 第二个 interrupt 触发点 |
| **改动** | `config.py` | S3-08 | 新增 `MAX_DEV_LOOP_LLM_CALLS=20` + sandbox 护栏常量；`MAX_FIX_LOOP_COUNT=3` 首次接线 |
| **零改动（仅消费）** | `core/state.py` | S3-09 | 字段全部复用；**严禁加 reducer**（must-fix-1），单 agent 下 `FixLoopRecord` 不追加 Optional 字段 |
| **新增** | `ui/pages/execution_monitor.py` | S3-10 | 执行监控页（含第二个 interrupt 三选一交互） |
| **新增** | `ui/pages/result_report.py` | S3-10 | 结果报告页（三形态渲染） |
| **改动** | `app.py` | S3-10 | UI 路由常量 + 第二个 interrupt 区分（沿用 `poll_state`/`is_interrupted`/`resume_with`/`get_interrupt_payload`，新增 `interrupt_kind` 判定） |

> **硬约束（贯穿全文）**：主图保持 **严格 7 节点 DAG**（`paper_intake / paper_analysis / resource_scout / planning / coding / execution / reporting`）。sp3 只做「加边 / 改条件路由 / 节点位换真实现」，**不新增 `coding_only` / `dev_loop` / `exit_dev_loop` 任何节点**（AC-S3-10）。第二个 interrupt 落在已有 `execution` 节点函数体内，不是独立节点（详见 §2.5 / §4.3）。

### 1.2 模块依赖关系与初始化顺序

```
config.py（+MAX_DEV_LOOP_LLM_CALLS / +SANDBOX_* 护栏常量）
   │
   ├─→ sandbox/local_venv.py（依赖 config.WORKSPACE_DIR + 护栏常量 + core/errors.py）
   │        │
   │        └─→ core/nodes/execution.py（依赖 sandbox + state + errors）
   │
core/state.py（字段权威定义，零改动）
core/errors.py（SandboxError 家族已在 sp1 定义，sp3 首次使用）
   │
core/react_base.py（_make_react_wrapper 唯一预算扣减点，零改动）
   │        │
   │        └─→ core/nodes/coding.py（复用 _make_react_wrapper 范式）
   │
core/nodes/reporting.py（仅依赖 state，纯函数式生成 Markdown）
   │
core/graph.py（注册 coding/execution/reporting 真实现 + 修复回边 + code_only + 第二个 interrupt 路由）
   │
app.py + ui/pages/*（UI 层消费 report_path + 承载第二个 interrupt 决策）
```

**关键路径（与 CLAUDE.md 依赖链对齐）**：`config.py` → `sandbox/local_venv.py` → `core/nodes/execution.py`；`core/react_base.py` → `core/nodes/coding.py`；三节点齐备后改 `core/graph.py` 路由。

### 1.3 与整体架构的映射

```
七步流程节点位          sp3 形态
─────────────────────────────────────────────────────
paper_intake     ReAct wrapper     （sp1，零改动）
paper_analysis   ReAct wrapper     （sp1，零改动）
resource_scout   ReAct wrapper     （sp2，零改动）
planning         手写复合 + interrupt#1（sp2，零改动；新增 revise_plan 回流接受方）
coding           ReAct wrapper     （sp3 新增，复用 _make_react_wrapper）
execution        手写复合节点      （sp3 新增：sandbox 执行 + 错误分类 + interrupt#2）
reporting        手写函数式节点    （sp3 新增：三形态 Markdown）
```

`coding` 与 `paper_intake/analysis/resource_scout` 同构（都是 `_make_react_wrapper` 产物，自动获得节点级 LLM 路由与预算扣减）。`execution` 与 `planning` 同构（都是手写复合节点：内部跑业务逻辑 + 在函数体内调用 `interrupt()`）。`reporting` 是纯函数式节点（无 LLM，无 interrupt，只读状态写 Markdown）。

---

## 2. 模块详细设计

### 2.1 `sandbox/local_venv.py`（S3-01，PRD §2.1 / §5.2「建议转架构师」落地）

**职责**：提供受控的本地 venv 代码执行能力 + 4 项硬护栏（超时 / 输出上限 / 工作目录限定 / 子进程隔离）。**不引入 Docker，不做跨机执行**（Q-S3-02）。本模块是纯基础设施（无 LLM、无 GlobalState 依赖），只接收路径/命令/护栏参数，返回结构化 dict，由 `execution` 节点映射为 `ExecutionResult`。

#### 2.1.1 config 护栏常量（S3-08，PRD §5.2 TBD 项落地）

在 `config.py` 新增以下常量（沿用 sp2 git_tools 常量命名风格，无 env 覆盖）：

```python
# ========== Sprint 3：sandbox 本地 venv 护栏 ==========
SANDBOX_EXEC_TIMEOUT: int = 1800          # 单条执行步骤子进程超时（秒），默认 30 分钟
SANDBOX_VENV_CREATE_TIMEOUT: int = 300    # python -m venv 创建超时（秒）
SANDBOX_PIP_INSTALL_TIMEOUT: int = 1200   # 单次 pip install 超时（秒），默认 20 分钟
SANDBOX_OUTPUT_MAX_BYTES: int = 1_048_576 # stdout/stderr 各自捕获字节上限（1 MiB），超限截断
SANDBOX_PIP_MAX_RETRIES: int = 2          # pip install 瞬态失败重试次数（网络类）

# ========== Sprint 3：修复循环（dev_loop）预算 ==========
MAX_DEV_LOOP_LLM_CALLS: int = 20          # 修复循环子预算天花板；强约束 < MAX_TOTAL_LLM_CALLS(50)
DEV_LOOP_MIN_CALLS_PER_ROUND: int = 2     # 入口预算门：单回合最小 LLM 调用数（< 此值则降级不进循环）
```

> **取值依据**：
> - `SANDBOX_EXEC_TIMEOUT=1800`：复现任务可能含训练，30 分钟是「疑似死循环」与「正常长训练」的折中默认值；可被 `ReproductionPlan.estimated_time` 上下文调高（v1.x 优化）。
> - `SANDBOX_OUTPUT_MAX_BYTES=1 MiB`：防 checkpoint 撑爆（SqliteSaver 写 `ExecutionResult.logs`）。1 MiB 足够保留尾部错误栈。
> - `DEV_LOOP_MIN_CALLS_PER_ROUND=2`：一个修复回合 coding 至少 1 次 ReAct round + execution 解析可能 1 次，保守取 2；`retry_budget_remaining < 2` 时入口预算门直接降级（§2.5.4）。
> - `MAX_DEV_LOOP_LLM_CALLS=20 < MAX_TOTAL_LLM_CALLS=50`：满足 PRD §5.2 强约束（AC-S3-04 断言）。

#### 2.1.2 模块接口签名

```python
# sandbox/local_venv.py

from dataclasses import dataclass, field
from typing import Dict, List, Optional

@dataclass
class SandboxRunResult:
    """单次 sandbox 子进程执行的结构化结果（execution 节点映射为 ExecutionResult 的原料）。"""
    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float
    timed_out: bool
    output_truncated: bool
    command: List[str]                     # 实际执行的命令（列表形式，便于审计）

@dataclass
class SandboxPrepareResult:
    """venv 创建 + 依赖安装的结构化结果。"""
    success: bool
    venv_dir: str                          # venv 绝对路径（workspace_dir 下）
    python_exe: str                        # venv 内 python 解释器绝对路径
    pip_exe: str                           # venv 内 pip 绝对路径
    env_info: Dict[str, str]               # {"python_version": ..., "key_packages": "..."}
    install_log: str                       # pip 安装日志（受 OUTPUT_MAX_BYTES 截断）
    install_failed_packages: List[str]     # 装不上的包（供 execution 分类为 dependency 类错误）
    error: Optional[str]                   # 创建/安装失败摘要（success=False 时填）


def prepare_venv(
    work_dir: str,                         # 工作目录绝对路径（= code_output_dir 或其父）
    requirements: Optional[List[str]] = None,        # 显式依赖列表（来自 ReproductionPlan.environment）
    requirements_files: Optional[List[str]] = None,  # requirements.txt / environment.yml / pyproject.toml 绝对路径
    reuse_existing: bool = True,           # 同目录已存在 venv 时复用（边界条件，PRD §2.1）
    venv_timeout: int = SANDBOX_VENV_CREATE_TIMEOUT,
    pip_timeout: int = SANDBOX_PIP_INSTALL_TIMEOUT,
) -> SandboxPrepareResult:
    """在 work_dir 下创建（或复用）隔离 venv 并安装依赖。

    护栏：
        - work_dir 经 resolve()+is_relative_to(WORKSPACE_DIR) 校验，越界抛 SandboxCreationError；
        - venv 落在 work_dir/.venv（位于 workspace_dir 下）；
        - python -m venv / pip install 均经 _run_subprocess（列表形式，禁 shell=True）；
        - pip 安装网络瞬态失败按 SANDBOX_PIP_MAX_RETRIES 指数退避重试；
        - reuse_existing=True 且 .venv/pyvenv.cfg 存在时跳过创建（复用），仍执行增量 pip install。
    """


def run_in_venv(
    python_exe: str,                       # prepare_venv 返回的 venv python 绝对路径
    command: List[str],                    # 执行命令（列表形式，如 ["{python}", "train.py", "--epochs", "1"]）
    work_dir: str,                         # 子进程 cwd（resolve+is_relative_to 校验）
    timeout: int = SANDBOX_EXEC_TIMEOUT,
    output_max_bytes: int = SANDBOX_OUTPUT_MAX_BYTES,
    extra_env: Optional[Dict[str, str]] = None,
) -> SandboxRunResult:
    """在隔离 venv 中以独立子进程执行单条命令，捕获 stdout/stderr/exit_code/时长。

    4 项护栏实现（见 §2.1.3）：执行超时强杀进程树 / 输出字节截断 / cwd 限定 workspace_dir / 独立子进程隔离。
    """


def collect_artifacts(
    work_dir: str,
    patterns: Optional[List[str]] = None,  # 产物 glob（默认 *.pt/*.pth/*.ckpt/*.png/*.json/*.csv 等）
) -> List[str]:
    """扫描 work_dir 收集执行产物路径（绝对路径，限定 workspace_dir 下）。供 ExecutionResult.artifacts。"""
```

#### 2.1.3 4 项护栏的跨平台实现（PRD §2.1「建议转架构师」逐条落地）

**护栏 1 — 执行超时 + 跨平台子进程树 kill**（这是最关键的跨平台落点）：

`subprocess.run(timeout=...)` 在超时时只 kill 直接子进程，**不杀其孙进程**（如 `python train.py` 内部 `torch.distributed` 派生的子进程会泄漏）。本模块用「进程组」做跨平台子树杀灭：

```python
import os, signal, subprocess, sys

def _run_subprocess(cmd, cwd, timeout, output_max_bytes, extra_env=None):
    """统一子进程封装：列表形式 + 禁 shell=True + 进程组隔离 + 超时杀子树 + 输出截断。

    跨平台进程组：
        - POSIX：start_new_session=True 让子进程成为新会话/进程组首领；
          超时时 os.killpg(os.getpgid(proc.pid), SIGKILL) 杀整组（含孙进程）。
        - Windows：creationflags=CREATE_NEW_PROCESS_GROUP；
          超时时 proc.send_signal(CTRL_BREAK_EVENT) 后 proc.kill()（taskkill /T 兜底）。
    """
    popen_kwargs = dict(
        cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env={**os.environ, **(extra_env or {})},
        # 关键：不传 shell=True；cmd 是列表
    )
    if sys.platform == "win32":
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_kwargs["start_new_session"] = True   # setsid，新进程组

    proc = subprocess.Popen(cmd, **popen_kwargs)
    timed_out = False
    try:
        stdout_b, stderr_b = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_process_tree(proc)        # 跨平台杀子树（killpg / CTRL_BREAK + kill）
        stdout_b, stderr_b = proc.communicate()   # 回收残余输出，避免管道死锁
    # ... 截断 + 解码 ...
```

> 复用 sp2 `git_tools._run_git` 的「列表形式 + 禁 shell=True + 捕获文本」范式，但因需要超时后杀子树，改用 `Popen` + `communicate(timeout=)` 而非 `subprocess.run`（`run` 无法访问 pid 做 killpg）。

**护栏 2 — 输出字节截断**：在 `communicate` 返回 `bytes` 后，对 stdout/stderr **各自**按 `output_max_bytes` 截断，**保留尾部**（错误栈通常在末尾），置 `output_truncated=True`：

```python
def _truncate_output(raw: bytes, max_bytes: int) -> tuple[str, bool]:
    if len(raw) <= max_bytes:
        return raw.decode("utf-8", errors="replace"), False
    tail = raw[-max_bytes:]   # 保留尾部（错误信息）
    marker = f"... [truncated, kept last {max_bytes} bytes] ...\n"
    return marker + tail.decode("utf-8", errors="replace"), True
```

> 字节阈值在 `communicate` 之后做（`communicate` 已读全量到内存）。**注意**：超大输出在 `communicate` 阶段已进内存——MVP 接受此风险（1 MiB 子进程正常输出不会爆，疑似失控输出靠超时护栏兜底杀进程）。流式逐块截断写文件归 v1.x（§7 风险提示）。

**护栏 3 — 工作目录限定**：路径越界校验在执行前完成，越界抛 `SandboxCreationError`（sp1 已定义于 errors.py L126）；子进程 `cwd` 强制设为校验后的 `work_dir`，子进程的相对路径副作用天然落在 workspace 下。**按路径的使用方式分两类校验（sp3-B1 备案，见 §2.1.5）**：

- **写入 / 产物类路径（`work_dir` / `venv_dir` / `requirements_files` / `artifacts`）**：经 `resolve() + is_relative_to(WORKSPACE_DIR.resolve())` 校验（**复用 git_tools `_is_within_workspace` 范式**，对应 `local_venv._is_within_workspace` / `_require_within_workspace`）。这类路径会产生文件系统副作用，必须 `resolve()` 解开符号链接看**真实落点**，防"符号链接指向 workspace 外再往里写"的逃逸。
- **可执行入口（`python_exe` 等）**：经 lexical `os.path.abspath()`（仅规范化 `..`/`.`、**不解符号链接**）+ `is_relative_to(WORKSPACE_DIR.resolve())` 校验（对应 `local_venv._is_within_workspace_lexical` / `_require_python_exe_within_workspace`）。**理由**：venv 内 `bin/python` 本身就是指向系统解释器（如 `/usr/bin/python3.11`，位于 workspace 外）的符号链接，用 `resolve()` 会解开到 workspace 外导致**误判越界、拒掉合法 venv python**，护栏卡死自己。可执行入口"只读被 exec、不被写入"，护栏意图是"确认传的是 prepare_venv 在 workspace 下创建的 venv python，而非任意系统二进制"，故只需对其**字面路径**做包含校验即可。lexical 校验仍能拦 `../` 逃逸（如 `.venv/../../../../etc/python`）与绝对系统路径（如 `/usr/bin/python`），护栏意图未削弱。

> **选择规则（适用于后续所有路径校验点，含 C3 execution 复用 sandbox）**：路径**会被写入 / 产生副作用 / 收集为产物** → 用 `resolve()`；路径**仅作可执行入口被 exec 且预期本身是指向系统解释器的符号链接（venv python/pip）** → 用 lexical `abspath`。git_tools 的 `dest_dir` / `local_path` 均为写入/读取路径、无可执行符号链接场景，**保持 resolve 不变**。

**注意**：本护栏限定 cwd 与入参路径，无法阻止子进程用绝对路径写任意位置——MVP 接受（与 Docker 隔离的差距，归 §7 + sp4+）。

**护栏 4 — 子进程隔离**：每条命令独立 `Popen` 子进程（新进程组/会话），子进程崩溃（段错误/OOM）只反映为非 0 exit_code，不抛进主工作线程；`_run_subprocess` 内部 `try/except` 兜底任何 OSError，转 `SandboxRunResult(exit_code=-1, ...)` 返回，绝不让异常逃逸到 execution 节点之外（沿用 ReAct 子图「工具异常不杀子图」治理）。

#### 2.1.4 venv 复用 / pip 失败降级策略

- **复用**：`reuse_existing=True`（默认）时，若 `work_dir/.venv/pyvenv.cfg` 已存在则跳过创建，仅做增量 `pip install`（修复回合复跑同目录时避免重建 venv）。与 git_tools「同 URL 重复克隆跳过」幂等范式一致。
- **pip 失败分级**：
  - 网络瞬态（连接超时 / 解析失败）→ 按 `SANDBOX_PIP_MAX_RETRIES` 指数退避重试（复用 git_tools `_RETRY_BACKOFF_SECONDS` 思路）；
  - 包不存在 / 版本冲突 / 编译失败 → 记入 `install_failed_packages`，`success=False`，**不抛异常**，交 execution 节点分类为 `dependency` 类错误（可自动修复，送回 coding，PRD §2.7）；
  - venv 创建本身失败（磁盘满 / python 缺失）→ `SandboxPrepareResult(success=False, error=...)`，execution 据此分类（磁盘满归不可修复，python 缺失归环境硬约束）。

#### 2.1.5 实现偏差备案：护栏3 路径校验二分（sp3-B1，2026-06-24）

> **背景**：B1 开发时发现 §2.1.3 原文"所有 `work_dir`/`venv_dir`/`python_exe` 入参统一用 `resolve()+is_relative_to` 校验"在真实环境下不可行：venv 内 `bin/python` 是指向系统解释器（`/usr/bin/python3.11`，workspace 外）的**符号链接**，对 `python_exe` 用 `resolve()` 会解开到 workspace 外、误判越界、拒掉合法 venv python，护栏卡死自己。

> **处置**（commit `750e49e`，`sandbox/local_venv.py`）：按路径使用方式拆为两类校验——写入/产物路径（work_dir/venv_dir/requirements_files/artifacts）仍用 `resolve()`（防符号链接逃逸写）；可执行入口（python_exe）改用 lexical `os.path.abspath`（不解符号链接，仅规范化 `..`/`.`）。新增 helper `_is_within_workspace_lexical` / `_require_python_exe_within_workspace`，与既有 `_is_within_workspace` / `_require_within_workspace` 并存。§2.1.3 已据此更新。

> **架构评估结论**：二分合理、安全性等价于原始意图，无新增攻击面。可执行入口"只读被 exec、不被写入"，无需 resolve 的真实落点保证，只需 lexical 的"声明路径在域内"保证。本二分作为通用规则推广至 C3 execution 复用 sandbox；git_tools 的 dest_dir/local_path 无可执行符号链接场景，**保持 resolve 不推广 lexical**（与 sp1/sp2 范式一致）。

> **测试独立验证（PASS，`tests/test_sprint3_b1.py`，已随 commit 750e49e 落盘）**：① 实测 venv python 确为指向系统 python3.11 的符号链接；② lexical 校验仍拦 `../` 逃逸（`.venv/../../../../etc/python` → workspace 外被拒）；③ 仍拦绝对系统路径（`/usr/bin/python` 被拒）；护栏"防传任意系统二进制"意图未削弱。

---

### 2.2 `core/nodes/coding.py`（S3-02，PRD §2.2）

**职责**：把 `coding` 占位（graph.py L57-60）替换为真实 ReAct 编码节点。**复用 `_make_react_wrapper`**（与 paper_intake/analysis/resource_scout/planning 内部 ReAct 完全同构），自动获得节点级 LLM 路由（`resolve_llm_config`）+ 预算扣减（react_base.py L889-894 唯一扣减点）。

#### 2.2.1 节点形态与预算（must-fix-2 关键）

```python
NODE_NAME: str = "coding"
REACT_MAX_ROUNDS_CODING: int = 12     # 新增 config 常量；编码生成/适配需多轮工具调用

coding = _make_react_wrapper(
    node_name=NODE_NAME,
    build_context=_build_coding_context,      # 见 §2.2.2
    build_system_prompt=_build_coding_system_prompt,
    get_tools=_get_coding_tools,              # 见 §2.2.3
    map_result=_map_coding_result,            # 见 §2.2.4（3 参签名，含 react_messages）
    max_rounds=REACT_MAX_ROUNDS_CODING,
    result_schema=CODING_OUTPUT_SCHEMA,
)
```

> **预算扣减归属（must-fix-2，§4.5 详述）**：coding 走 `_make_react_wrapper`，其内部 `_wrapper` 在 L889-894 **自动按实际 ReAct round 数扣减 `retry_budget_remaining`** 并写入返回 dict。因此 **coding 节点的预算扣减是合规的、自动的**，无需手写。修复循环的预算缺口只剩 execution（手写节点，不走 wrapper）—— execution 不消耗 LLM 时不扣，消耗时（metrics LLM 抽取兜底）按实际次数单点回写（§2.3.4 / §4.5）。这与兼容性矩阵 §B.1「占位节点零扣减」的缺口正好对上：换成真实现后 coding 自动扣减，缺口收敛。

#### 2.2.2 build_context（修复回合反馈注入，PRD §2.2「建议转架构师」落地）

`coding` 在 **首轮** 与 **修复回合** 共用同一 context 构造器，靠读 `state["execution_result"]` 是否为 None + `fix_loop_count` 区分：

```python
def _build_coding_context(state: GlobalState) -> Dict[str, Any]:
    """curated 上下文（HumanMessage 通道，sort_keys 字节幂等，对齐 planning 范式）。"""
    payload = {}
    # 计划 + 选定仓库本地路径（sp2 落地，直接复用，无需重 clone）
    plan = state.get("reproduction_plan") or {}
    payload["code_strategy"] = plan.get("code_strategy")
    payload["execution_steps"] = plan.get("execution_steps")
    payload["deliverables"] = plan.get("deliverables")
    payload["environment"] = plan.get("environment")
    resource = state.get("resource_info") or {}
    selected = resource.get("selected_repo") or {}
    payload["selected_repo_local_path"] = selected.get("local_path")  # 关键：本地仓库绝对路径
    # 英文事实层字段（避免中英混杂喂代码生成，sp2 §4.7.5）
    analysis = state.get("paper_analysis") or {}
    payload["method_summary_en"] = analysis.get("method_summary_en") or analysis.get("method_summary")
    payload["datasets"] = analysis.get("datasets")
    payload["framework"] = analysis.get("framework")
    payload["hardware_requirements_en"] = analysis.get("hardware_requirements_en")

    # === 修复回合：注入上一轮 execution 反馈（裁剪后）===
    exec_result = state.get("execution_result")
    fix_count = state.get("fix_loop_count", 0) or 0
    if exec_result and fix_count > 0:
        payload["fix_round"] = fix_count
        payload["last_error_summary"] = _digest_execution_feedback(exec_result)  # 见下
        payload["code_output_dir"] = state.get("code_output_dir")  # 在原代码上改，非重生成
    return payload


def _digest_execution_feedback(exec_result: dict) -> Dict[str, Any]:
    """把上一轮 ExecutionResult 裁剪为修复用的精简反馈（上下文裁剪策略，PRD §2.2）。

    裁剪策略（防 stderr 撑爆 context）：
        - errors: 取 ExecutionResult.errors 全部（已是摘要级，每条一句话）；
        - error_category: 取上一轮 execution 分类结果（驱动有针对性修复）；
        - stderr_tail: logs 取尾部 ~2000 字符（错误栈通常在末尾）；
        - 不注入完整 logs / stdout（已被 sandbox 截断，仍可能很大）。
    """
```

> **回答 PRD「coding 修复回合如何拿到反馈」**：**直接读 `state["execution_result"]`**（不新增精简 feedback 字段——避免增加 state 字段、避免 reducer 风险）。上一轮 execution 已把分类结果写进 `ExecutionResult.errors[0]`（带 `[error_category=...]` 前缀，§2.3.2），coding 读出后裁剪注入 HumanMessage。`fix_loop_count > 0` 即判定为修复回合，prompt 切到「在 `code_output_dir` 现有代码上有针对性修改」模式，而非从头重生成。

#### 2.2.3 coding 工具集

```python
def _get_coding_tools(state) -> List[BaseTool]:
    return [
        make_write_code_file_tool(),      # 新增：写文件到 code_output_dir（resolve+is_relative_to 校验）
        make_read_code_file_tool(),       # 新增：读 code_output_dir / selected_repo.local_path 下文件
        make_list_dir_tool(),             # 新增：列目录（限定 workspace）
        read_section_tool(),              # 复用 deepxiv：回读论文章节核对实现细节
        web_search_tool(),                # 复用：查依赖/API 用法
    ]
```

> **新增工具 `core/tools/code_fs_tools.py`**（写/读/列代码文件）：**严格复用 git_tools 安全范式** —— `resolve()+is_relative_to(WORKSPACE_DIR)` 路径越界校验、`_serialize_tool_result(json.dumps ensure_ascii=False sort_keys=True default=str)` 序列化（禁 `str(dict)`，BUG-S1-02 治理）、`@tool` 工厂 + `try/except` 兜底不杀子图。

#### 2.2.4 map_result 与 state 写入（must-fix-1）

```python
def _map_coding_result(result, state, react_messages=None) -> dict:
    """把 coding ReAct 结果映射为 GlobalState 局部更新。

    写入字段：
        - code_output_dir: 代码目录绝对路径（首轮新建，修复回合复用同目录）；
        - current_step: "coding"；
        - node_errors / degraded_nodes: 仅在 coding 自身 ReAct 失败时写，走单点 read-modify-write。
    不写：fix_loop_count（自增点在 execution 出口路由判定，§2.5.2，避免双点写）。
    """
    node_errors = list(state.get("node_errors", []))      # read-modify-write（must-fix-1）
    degraded_nodes = list(state.get("degraded_nodes", []))
    code_dir = _resolve_code_output_dir(state)            # workspace_dir/<thread>/code，幂等
    # ReAct 失败（result 空/无文件产出）→ degraded，不死循环
    if not result or not _has_written_any_file(react_messages, code_dir):
        if NODE_NAME not in degraded_nodes:
            degraded_nodes.append(NODE_NAME)
        node_errors.append(make_node_error(NODE_NAME, "degraded", "coding 未产出代码文件，降级", None))
        logger.warning("[%s] 未产出代码文件，标记 degraded", NODE_NAME)
    return {
        "code_output_dir": code_dir,
        "current_step": NODE_NAME,
        "node_errors": node_errors,
        "degraded_nodes": degraded_nodes,
        # retry_budget_remaining 由 _make_react_wrapper 自动 setdefault 回写（不在此覆盖）
    }
```

---

### 2.3 `core/nodes/execution.py`（S3-03，PRD §2.3 / §2.7「建议转架构师」核心落地）

**职责**：手写复合节点。调 sandbox → 准备 venv → 装依赖 → 按 `execution_steps` 执行 → 捕获 → **错误分类** → 产出 `ExecutionResult` → **B 档 success 判定**。**且在修复耗尽/不可修复时，在本节点函数体内触发第二个 `interrupt()`**（§2.5.1 / §4.3 决策依据）。

#### 2.3.1 节点函数骨架

```python
NODE_NAME: str = "execution"

def execution(state: GlobalState) -> dict:
    """步骤 6：sandbox 执行 + 错误分类 + B 档判定 + 修复耗尽时 interrupt#2。"""
    # 1. 准备 venv + 装依赖（sandbox）
    prep = prepare_venv(work_dir=state["code_output_dir"], requirements=..., requirements_files=...)
    # 2. 执行 execution_steps（逐条 run_in_venv），聚合
    run_results = [run_in_venv(prep.python_exe, _to_cmd(step), state["code_output_dir"]) for step in steps]
    # 3. 错误分类（执行反馈层载体，不污染 NodeError 三态，§2.3.2）
    feedback = _classify_execution(prep, run_results)   # -> ExecutionFeedback（节点内本地对象）
    # 4. metrics 解析（结构化优先 / 正则兜底 / LLM 抽取兜底，§2.3.3）
    metrics = _parse_metrics(run_results, plan=state.get("reproduction_plan"), state=state)
    # 5. 构造 ExecutionResult + B 档 success（§2.3.5）
    exec_result = _build_execution_result(prep, run_results, feedback, metrics)
    # 6. 写 state（单点 read-modify-write）
    updates = _map_execution_result(exec_result, feedback, state)
    # 7. 修复循环边界判定（是否在本节点 interrupt#2，§2.5.1）：
    #    仅当「修复耗尽 or 不可修复」时本节点函数体内调用 interrupt()；
    #    可修复且未超限 → 不 interrupt，正常返回，由出边路由送回 coding（§2.5.2）。
    return _maybe_interrupt_or_return(updates, exec_result, feedback, state)
```

> **execution 的 LLM 用量与预算（must-fix-2）**：execution 主体不调 LLM（纯子进程执行）。**唯一可能的 LLM 调用 = metrics 的 LLM 抽取兜底**（§2.3.3 第 3 档）。若触发，按实际调用次数（通常 1 次）单点回写 `retry_budget_remaining`（§4.5）；不触发则 execution 对预算零消耗（正确语义——它没花 LLM）。

#### 2.3.2 错误分类载体设计（PRD §2.3「建议转架构师」 + 兼容性矩阵 §A.1 落地）

**核心约束**：执行期错误细分类（syntax/import/dependency/path/runtime/data_missing/hardware/timeout）**不进 `NodeError.error_type`**（后者只保留 sp1/sp2 既有三态 `transient/permanent/degraded`），落在 **节点内本地对象 `ExecutionFeedback`**，冒泡到 GlobalState 时再映射为三态之一。`ExecutionFeedback` 是 **节点内本地 dataclass，不是 GlobalState 字段、不进 state.py**（单 agent 下无需子图私有 TypedDict，与 §4.4 顺延的 multi-agent `ExecutionFeedback` 区分）：

```python
# core/nodes/execution.py（节点本地，不写入 state.py）
from enum import Enum
from dataclasses import dataclass

class ErrorCategory(str, Enum):
    # —— 可自动修复类（送回 coding，计入 fix_loop_count，PRD §2.7 / AC-S3-08）——
    SYNTAX = "syntax"
    IMPORT = "import"
    DEPENDENCY = "dependency"
    PATH = "path"
    RUNTIME = "runtime"
    # —— 不可自动修复类（不进重试，走 interrupt#2 / 降级）——
    DATA_MISSING = "data_missing"
    HARDWARE = "hardware"
    TIMEOUT = "timeout"
    UNRESOLVED_RESOURCE = "unresolved_resource"  # 需论文未公开资源
    NONE = "none"                                # 执行成功，无错误

# 可自动修复类集合（驱动 §2.5.2 路由）
AUTO_FIXABLE = {ErrorCategory.SYNTAX, ErrorCategory.IMPORT, ErrorCategory.DEPENDENCY,
                ErrorCategory.PATH, ErrorCategory.RUNTIME}

@dataclass
class ExecutionFeedback:
    category: ErrorCategory
    auto_fixable: bool                 # = category in AUTO_FIXABLE
    summary: str                       # 一句话错误摘要（供 fix_loop_history.error_summary + coding 反馈）
    fix_hint: str                      # 给 coding 的修复建议
    representative_stderr: str         # 代表性 stderr 片段（裁剪）
```

**分类判定逻辑（基于 exit_code / stderr 关键字 / timed_out）**：

```python
def _classify_execution(prep: SandboxPrepareResult, run_results: List[SandboxRunResult]) -> ExecutionFeedback:
    # 0) 全部 exit 0 → NONE（成功）
    if prep.success and all(r.exit_code == 0 for r in run_results):
        return ExecutionFeedback(ErrorCategory.NONE, False, "执行成功", "", "")
    # 1) 超时优先（疑似死循环，不可修复）
    if any(r.timed_out for r in run_results):
        return ExecutionFeedback(ErrorCategory.TIMEOUT, False, "执行超时（疑似死循环）", "无", _tail(...))
    # 2) 依赖装不上（可修复，送回 coding 调整版本/换包）
    if not prep.success and prep.install_failed_packages:
        return ExecutionFeedback(ErrorCategory.DEPENDENCY, True, f"依赖安装失败: {prep.install_failed_packages}", ...)
    failed = next(r for r in run_results if r.exit_code != 0)
    stderr = (failed.stderr or "").lower()
    # 3) 关键字匹配（顺序敏感：硬件/数据缺失先于通用 runtime）
    if any(k in stderr for k in _HARDWARE_KEYWORDS):       # cuda out of memory / no cuda gpus / device-side assert
        return ExecutionFeedback(ErrorCategory.HARDWARE, False, "硬件/显存约束", ...)
    if any(k in stderr for k in _DATA_MISSING_KEYWORDS):   # filenotfounderror + dataset/data 路径 / no such file (数据集)
        return ExecutionFeedback(ErrorCategory.DATA_MISSING, False, "数据集缺失需人工下载", ...)
    if "modulenotfounderror" in stderr or "importerror" in stderr:
        return ExecutionFeedback(ErrorCategory.IMPORT, True, "import 错误（缺包/路径）", ...)
    if "syntaxerror" in stderr or "indentationerror" in stderr:
        return ExecutionFeedback(ErrorCategory.SYNTAX, True, "语法错误", ...)
    if "filenotfounderror" in stderr or "no such file" in stderr:   # 非数据集的路径错
        return ExecutionFeedback(ErrorCategory.PATH, True, "文件路径错误", ...)
    # 4) 兜底：通用运行时错误（可修复，给 coding 一次机会）
    return ExecutionFeedback(ErrorCategory.RUNTIME, True, "运行时异常", ...)
```

> 关键字表（`_HARDWARE_KEYWORDS` / `_DATA_MISSING_KEYWORDS` 等）作为模块级静态常量，复用 git_tools `_TRANSIENT_STDERR_KEYWORDS` 的小写匹配范式。**分类不准的风险（R-S3-04）**：兜底归 RUNTIME（可修复，给一次机会），由 `MAX_FIX_LOOP_COUNT=3` 上限拦截，不会无限重试。

#### 2.3.3 metrics 异构输出解析策略（PRD §2.3「建议转架构师」三档落地）

```python
def _parse_metrics(run_results, plan, state) -> Dict[str, Any]:
    """三档降级解析（结构化约定优先 → 正则兜底 → LLM 抽取兜底）。"""
    stdout = "\n".join(r.stdout for r in run_results)
    # 档 1（首选）：结构化输出约定 —— 约定复现脚本在 stdout 打印
    #   <METRICS>{"accuracy": 0.91, "f1": 0.88}</METRICS>（类比 react_base 的 <result> 标签）
    m = _extract_metrics_block(stdout)   # 复用 _RESULT_TAG_PATTERN 同款正则范式
    if m: return m
    # 档 2（兜底）：正则按指标名扫描 —— 指标名取 paper_analysis.metrics（英文事实字段）做锚点
    #   形如 "accuracy: 0.91" / "Acc = 91.2%" / "F1 0.88"
    metric_names = (state.get("paper_analysis") or {}).get("metrics") or []
    m = _regex_scan_metrics(stdout, metric_names)
    if m: return m
    # 档 3（最后兜底）：LLM 抽取 —— 仅当 exit 0（值得抽）且 stdout 非空时触发；
    #   按实际 1 次 LLM 调用回写 retry_budget_remaining（must-fix-2，§4.5）。
    if all(r.exit_code == 0 for r in run_results) and stdout.strip():
        return _llm_extract_metrics(stdout, metric_names, state)  # 标记本次消耗 1 次 LLM
    return {}
```

> **档 1 的约定要求 coding 生成的脚本打印 `<METRICS>...</METRICS>`**：coding system prompt 中明确要求复现入口脚本末尾以该标签打印关键指标 JSON。这把「异构输出解析」难题前移到代码生成阶段，提升 B 档成功率（缓解 R-S3-05）。LLM 抽取兜底是最后保险。

#### 2.3.4 map_result + ExecutionResult 构造（must-fix-1）

```python
def _map_execution_result(exec_result, feedback, state) -> dict:
    node_errors = list(state.get("node_errors", []))       # read-modify-write（must-fix-1）
    degraded_nodes = list(state.get("degraded_nodes", []))
    if not exec_result["success"]:
        # 冒泡映射：执行细分类 → NodeError 三态（兼容性矩阵 §A.1）
        three_state = _map_category_to_error_type(feedback.category)
        #   可修复(syntax/import/dependency/path/runtime) → "transient"
        #   不可修复(timeout/hardware/data_missing/unresolved_resource) → "permanent"
        node_errors.append(make_node_error(
            NODE_NAME, three_state,
            f"[error_category={feedback.category.value}] {feedback.summary}",  # 细分类进 message，不进 error_type
            feedback.representative_stderr,
        ))
        logger.warning("[%s] 执行失败 category=%s three_state=%s", NODE_NAME, feedback.category.value, three_state)
    return {
        "execution_result": exec_result,
        "current_step": NODE_NAME,
        "node_errors": node_errors,
        "degraded_nodes": degraded_nodes,
    }
```

> **细分类承载位置**：`ErrorCategory` 写进 `NodeError.error_message` 的 `[error_category=...]` 前缀（reporting / coding 反馈可解析），`NodeError.error_type` 严格保持三态。冒泡映射规则：可修复类 → `transient`（还能重试语义），不可修复类 → `permanent`（放弃语义），coding 自身降级 → `degraded`。这与兼容性矩阵 §A.1 完全一致。

#### 2.3.5 B 档 success 判定（Q-S3-01）

```python
def _build_execution_result(prep, run_results, feedback, metrics) -> ExecutionResult:
    exit_ok = prep.success and all(r.exit_code == 0 for r in run_results)
    success = bool(exit_ok and len(metrics) >= 1)   # B 档：exit 0 且至少 1 个指标
    return ExecutionResult(
        success=success,
        metrics=metrics,
        logs=_aggregate_logs(prep, run_results),     # 受 output_truncated 护栏约束
        errors=[f"[error_category={feedback.category.value}] {feedback.summary}"] if not success else [],
        artifacts=collect_artifacts(work_dir),
        runtime_seconds=sum(r.duration_seconds for r in run_results),
        environment_info=prep.env_info,
    )
```

---

### 2.4 `core/nodes/reporting.py`（S3-05，PRD §2.5）

**职责**：纯函数式节点（无 LLM、无 interrupt），读全局状态生成 Markdown 报告，写 `report_path`，支持 **full / code_only / 降级三形态**。

```python
NODE_NAME: str = "reporting"

def reporting(state: GlobalState) -> dict:
    """步骤 7：生成三形态 Markdown 复现报告。"""
    form = _determine_report_form(state)        # 见下，三形态判定
    md = _render_report(state, form)            # 按形态拼 Markdown
    report_path = _write_report(state, md)      # workspace_dir/<thread>/report.md（resolve+is_relative_to）
    logger.info("[%s] 报告生成: form=%s -> %s", NODE_NAME, form, report_path)
    return {"report_path": report_path, "current_step": NODE_NAME}


def _determine_report_form(state) -> str:
    """三形态判定（优先级从上到下）。"""
    if state.get("execution_mode") == ExecutionMode.CODE_ONLY:
        return "code_only"
    exec_result = state.get("execution_result")
    if exec_result and exec_result.get("success"):
        return "full_success"
    return "degraded"   # 修复耗尽 / 不可修复 / 预算耗尽 / export_code 都落这里
```

**三形态内容映射（读字段，不写）**：

| 形态 | 触发 | 报告章节 | 读取字段 |
|---|---|---|---|
| **full_success** | `execution_result.success == True` | 结论卡片(成功) + 指标对比表 + artifact 清单 + 执行概况 | `execution_result.metrics` vs `paper_analysis.baseline_results`/`reproduction_plan.expected_results`；`execution_result.artifacts`/`runtime_seconds`/`environment_info` |
| **code_only** | `execution_mode == CODE_ONLY` | 结论卡片(仅生成代码) + 代码位置 + deliverables 清单（**无指标章节**，标注"仅生成代码、未执行"） | `code_output_dir`、`reproduction_plan.deliverables` |
| **degraded** | 其余（含 `execution_result is None` 但非 code_only、success=False、export_code） | 结论卡片(未成功复现+降级原因) + 降级原因 + node_errors 摘要 + **fix_loop_history 修复历程** + 保留的代码与产物 | `degraded_nodes`、`node_errors`（解析 `[error_category=...]`）、`fix_loop_history`、`code_output_dir`、`user_fix_decision` |

> **指标对比表为「展示」不为「判定」**（Q-S3-01 B 档）：并列论文 baseline 与本次复现值，不做硬达标红绿判定。**语言策略**（sp2）：叙述中文，事实层（数据集名/指标名/仓库 URL）英文。`execution_result is None` 时（code_only 或 execution 未跑）reporting 仍产有效报告（边界条件，AC-S3-09）。

---

### 2.5 `core/graph.py` 升级（S3-04 / S3-06 / S3-07，PRD §2.4/§2.6/§2.7「建议转架构师」核心落地）

这是 sp3 唯一改主图的地方。**严格保持 7 节点**，只做：① coding/execution/reporting 注册真实现；② 新增 execution 后条件路由（修复回边 / 第二个 interrupt 衔接 / 降级）；③ 新增 code_only 路由（coding 后按 mode 分流）；④ 保留 sp2 既有 3 路 planning 路由不动。

#### 2.5.1 第二个 interrupt 触发位置决策（PRD §2.7「建议转架构师」核心）

> **结论：第二个 interrupt 放在 `execution` 节点函数体内调用 `interrupt()`，不新增独立 `exit_dev_loop` 节点。**

**决策依据**：
1. **硬约束**：PRD AC-S3-10 要求主图严格 7 节点、不新增 `dev_loop`/`exit_dev_loop` 节点。独立退出节点违反此约束。
2. **范式一致**：sp2 planning 的 interrupt 就是在节点函数体内调用 `interrupt()`（planning.py L781），不用 `interrupt_before/after`（graph.py L149-151 注释）。execution 沿用同款，把 interrupt 收在 execution 函数体内最自然。
3. **app.py 天然兼容**：`is_interrupted`（app.py L191-210）基于 `snapshot.tasks[].interrupts` 元数据判定，**与哪个节点 interrupt 无关**——execution 在节点内 interrupt 时 `is_interrupted` 自动返回 True，UI 无需改判定逻辑。
4. **路由判据**：execution 在函数体内先做修复循环边界判定，**只有「修复耗尽 or 不可修复」才 `interrupt()`**；「可修复且未超限且预算够」则不 interrupt、正常返回，由出边条件路由送回 coding。

```python
def _maybe_interrupt_or_return(updates, exec_result, feedback, state) -> dict:
    """execution 函数体内：修复循环边界判定 + 可能的 interrupt#2。"""
    if exec_result["success"]:
        return updates                                  # → 出边路由到 reporting
    fix_count = state.get("fix_loop_count", 0) or 0
    budget = state.get("retry_budget_remaining", 0) or 0
    # 可修复 + 未超限 + 预算够一回合 → 不 interrupt，返回（出边路由送回 coding，fix_count 在出边自增）
    if (feedback.auto_fixable
            and fix_count < MAX_FIX_LOOP_COUNT
            and budget >= DEV_LOOP_MIN_CALLS_PER_ROUND):
        return updates                                  # → 出边路由回 coding（修复回合）
    # 预算不足以启动一回合 → 入口预算门：直接降级（不 interrupt，§2.5.4 / PRD §5）
    if budget < DEV_LOOP_MIN_CALLS_PER_ROUND:
        return _mark_degraded_for_report(updates, state, reason="budget_exhausted")  # → 出边路由到 reporting
    # 修复耗尽 or 不可修复 → 第二个 interrupt（三选一）
    decision = interrupt(_build_dev_loop_interrupt_payload(exec_result, feedback, state))
    return _route_user_fix_decision(decision, updates, state)   # 写 user_fix_decision + 路由准备
```

> **降级与 interrupt 的边界（PRD §5 留给架构师协调）**：**纯预算耗尽 → 直接降级**（标 degraded + 出边到 reporting，不 interrupt，避免预算已尽还让用户空决策）；**修复耗尽 / 不可修复类 → 走 interrupt 三选一**（A 方案）。这是对 PRD §2.7「降级与 interrupt 的关系」与 §5.1 入口预算门的统一裁定。

#### 2.5.2 fix_loop_count 自增点决策（PRD §2.4「建议转架构师」）

> **结论：`fix_loop_count` 自增写在 execution 后的条件路由「回 coding」分支对应的状态更新里，由 execution 返回 dict 携带，而非 coding 入口。**

**决策依据**：
- **单点写避免竞争**：自增放在 execution 出口（返回 dict 里 `fix_loop_count: fix_count + 1`），coding 入口零写。因为「回不回 coding」的判定在 execution 已完成（§2.5.1），自增与该判定同点最一致，杜绝双点写。
- **粒度对齐 Q-S3-03**：一个 coding→execution 完整回合 = +1。execution 是回合的「出口」，在出口 +1 恰好计「已完成的修复回合数」。
- **不可修复/超限不自增**（AC-S3-08）：只有走「回 coding」分支才 +1；interrupt/降级分支不自增。

```python
def _maybe_interrupt_or_return(...):
    ...
    if feedback.auto_fixable and fix_count < MAX_FIX_LOOP_COUNT and budget >= MIN:
        updates["fix_loop_count"] = fix_count + 1                # 自增（单点）
        updates["fix_loop_history"] = _append_fix_record(state, fix_count + 1, feedback)  # read-modify-write
        return updates   # 出边路由回 coding
```

```python
def _append_fix_record(state, round_no, feedback) -> List[FixLoopRecord]:
    """单点 read-modify-write 追加 FixLoopRecord（must-fix-1，严禁 reducer）。"""
    history = list(state.get("fix_loop_history", []))           # 读出整列表
    history.append(FixLoopRecord(
        round_number=round_no,
        error_summary=feedback.summary,
        error_category=feedback.category.value,
        fix_strategy=feedback.fix_hint,
        timestamp=datetime.now(timezone.utc).isoformat(),
    ))
    return history                                              # return 整列表（last-write-wins，安全）
```

#### 2.5.3 execution 后条件路由函数 `_route_after_execution`

```python
def _route_after_execution(state: GlobalState) -> str:
    """execution 出边路由（4 路）。判定基于 execution 返回后写入 state 的字段。

    判定优先级（execution 函数体内已决定语义，路由只读结果字段映射目的地）：
        1. user_fix_decision == "revise_plan"          -> "planning"   （interrupt#2 改计划）
        2. user_fix_decision == "terminate"            -> "end"        （interrupt#2 终止）
        3. user_fix_decision == "export_code" / degraded标记 -> "reporting" （降级/导出/成功都到 reporting）
        4. 需要修复回合（fix_loop_count 本轮自增 + 未触发 interrupt） -> "coding"  （修复回边）
        5. execution_result.success == True            -> "reporting"  （B 档成功）
        其余兜底                                         -> "reporting"
    """
    decision = state.get("user_fix_decision")
    if decision == "revise_plan":
        return "planning"
    if decision == "terminate":
        return "end"
    if decision == "export_code":
        return "reporting"
    # 修复回边：execution 判定为「可修复且本轮已自增 fix_loop_count」时回 coding
    if state.get("_dev_loop_route") == "retry_coding":   # execution 返回 dict 携带的路由意图标记
        return "coding"
    return "reporting"
```

> **路由意图标记 `_dev_loop_route`**：execution 返回 dict 携带一个 **临时路由意图字段**（如 `_dev_loop_route="retry_coding"`），让出边路由函数无歧义区分「回 coding 修复」与「到 reporting」。该字段是 **GlobalState 既有通道之外的新增字段** —— 需在 state.py 加一个 `Optional[str]` 内部字段 `_dev_loop_route`（下划线前缀，单值字段，无 reducer，last-write-wins 安全）。**这是 sp3 对 state.py 唯一的微改动**（见 §5 / §7 回问）。
>
> **备选（避免改 state.py）**：路由函数直接重算判据（读 `execution_result.success` / 重新跑分类）——但重算分类需要 stderr，execution 已把分类写进 `node_errors[-1]` 的 `[error_category=...]`，路由函数可解析它判定 auto_fixable + 读 `fix_loop_count`/`retry_budget_remaining` 复算。**架构推荐用 `_dev_loop_route` 显式标记**（语义清晰、不重算、不依赖 message 解析）；若 Maria 倾向 state.py 零改动，则采备选重算方案（§7 回问点 1）。

#### 2.5.4 第二个 interrupt 的 resume 三态路由（PRD §2.7 表落地）

execution 函数体内 `interrupt()` 返回 resume payload 后，`_route_user_fix_decision` 写 `user_fix_decision` 并准备出边路由所需字段：

```python
def _route_user_fix_decision(decision, updates, state) -> dict:
    """interrupt#2 resume 三态（与 sp2 planning resume 范式一致：dict + "decision" 键）。"""
    if not isinstance(decision, dict) or "decision" not in decision:
        # 防御兜底：非法 payload 视为终止（不空转）
        decision = {"decision": "terminate"}
    kind = decision["decision"]
    if kind == "terminate":
        updates["user_fix_decision"] = "terminate"
        updates["current_step"] = "cancelled_by_user"   # 复用 sp2 cancel 终止态（_route_after_execution → end）
        # checkpoint 保留（不抛异常，沿用 sp2 cancel 语义）
    elif kind == "revise_plan":
        updates["user_fix_decision"] = "revise_plan"
        # 带修复失败上下文回 planning：写 _planning_user_feedback（planning 既有回流字段）
        updates["_planning_user_feedback"] = _build_revise_context(state)
        # 关键：清掉 reproduction_plan.approved，否则 planning 重入后 _route_after_planning 直接 next
        updates["reproduction_plan"] = {**(state.get("reproduction_plan") or {}), "approved": False}
        # 注意：不重置 fix_loop_count（保留修复历史；重规划后是否清零归 §7 回问点 2）
    elif kind == "export_code":
        updates["user_fix_decision"] = "export_code"
        updates = _mark_degraded_for_report(updates, state, reason="export_code")  # 标 degraded → reporting
    return updates
```

**三态路由落点（与 PRD §2.7 表一一对应）**：

| user_fix_decision | execution 写入 | `_route_after_execution` 目的地 | 后续 |
|---|---|---|---|
| `terminate` | `current_step="cancelled_by_user"` | `end`（→ END） | checkpoint 保留，复用 sp2 cancel 语义 |
| `revise_plan` | `_planning_user_feedback`(修复上下文) + `approved=False` | `planning` | planning 重入（revise 回流），用户调计划后重走 coding/execution |
| `export_code` | `degraded_nodes` 追加 + `user_fix_decision` | `reporting` | reporting 降级形态，保留 `code_output_dir`，出报告并 END |

> **区分两个 interrupt（PRD §2.10 / R-S3-06）**：第一个（planning）与第二个（execution）共享同一 thread_id。UI 区分靠 **interrupt payload 内的 `interrupt_kind` 字段** + `current_step`：planning payload 含 `reproduction_plan`/`revise_count` 等键且 `current_step` 在 planning 阶段；execution payload 含 `interrupt_kind="dev_loop_failure"` + `fix_loop_history`/`execution_result` 失败上下文。app.py 新增 `interrupt_kind(thread_id)` helper 读 `get_interrupt_payload` 的 `interrupt_kind` 键，UI 据此渲染不同决策面板（§2.6）。

#### 2.5.5 code_only 路由衔接（PRD §2.6「建议转架构师」 + 兼容性矩阵 §C.3）

> **结论：在 `coding` 后新增条件路由 `_route_after_coding`，按 `execution_mode` 分流；保持 sp2 `_route_after_planning` 三路条件边完全不动。**

**决策依据（兼容性矩阵 §C.3 指出 approve 和 code_only 都走 `next→coding`）**：
- sp2 `_route_after_planning` 的 `next` 分支同时承载 approve（FULL）与 code_only，**不在 planning 出边区分 mode**（保持 sp2 既有 3 路边零改动，AC-S3-10）。
- 区分点 **后移到 coding 出边**：`coding → execution`（FULL）vs `coding → reporting`（CODE_ONLY）。coding 节点本身不判 mode（PRD §2.2：无论 FULL/CODE_ONLY 都正常产代码），不新建 `coding_only` 节点（Q-S3-05）。

```python
def _route_after_coding(state: GlobalState) -> str:
    """coding 出边路由（2 路）：按 execution_mode 区分。"""
    if state.get("execution_mode") == ExecutionMode.CODE_ONLY:
        return "skip_execution"    # → reporting（跳过 execution + 修复循环，AC-S3-06）
    return "to_execution"          # → execution（FULL，进入修复循环）
```

#### 2.5.6 主图加边完整代码

```python
def build_graph(checkpointer=None):
    ...
    graph = StateGraph(GlobalState)
    # 7 节点（节点集合与名称不变；coding/execution/reporting 换真实现）
    graph.add_node("paper_intake", paper_intake)
    graph.add_node("paper_analysis", paper_analysis)
    graph.add_node("resource_scout", resource_scout)
    graph.add_node("planning", planning)
    graph.add_node("coding", coding)            # sp3 真实现（ReAct wrapper）
    graph.add_node("execution", execution)      # sp3 真实现（手写 + interrupt#2）
    graph.add_node("reporting", reporting)      # sp3 真实现（三形态）

    graph.add_edge(START, "paper_intake")
    graph.add_edge("paper_intake", "paper_analysis")
    graph.add_edge("paper_analysis", "resource_scout")
    graph.add_edge("resource_scout", "planning")
    # planning 3 路条件边：sp2 既有，零改动（revise_plan 回流复用 self/next 语义）
    graph.add_conditional_edges("planning", _route_after_planning,
        {"self": "planning", "next": "coding", "end": END})
    # 【sp3 新增】coding 出边：code_only 分流（替换原 coding→execution 顺序边）
    graph.add_conditional_edges("coding", _route_after_coding,
        {"to_execution": "execution", "skip_execution": "reporting"})
    # 【sp3 新增】execution 出边：4 路（修复回边 / interrupt#2 三态 / 降级 / 成功）
    graph.add_conditional_edges("execution", _route_after_execution,
        {"coding": "coding", "planning": "planning", "reporting": "reporting", "end": END})
    graph.add_edge("reporting", END)
    # 仍不使用 interrupt_before/after：interrupt() 在 planning / execution 函数体内调用。
    compiled = graph.compile(checkpointer=checkpointer)
    logger.info("LangGraph main graph compiled successfully with %d nodes", 7)
    return compiled
```

> **改动 vs 删除**：原 `coding→execution`、`execution→reporting` 两条**顺序边被替换为条件边**（不是删节点）。`reporting→END` 与 planning 3 路边保持。节点数仍为 7（AC-S3-10 断言 `len(nodes)==7` + 编译成功）。

---

### 2.6 UI 层（S3-10，`ui/pages/execution_monitor.py` + `ui/pages/result_report.py` + `app.py` 微调）

#### 2.6.1 app.py 改动（区分两个 interrupt）

沿用 sp2 `GraphController` 全部接口（`start_task`/`resume_with`/`poll_state`/`is_interrupted`/`get_interrupt_payload`），新增一个只读 helper：

```python
def interrupt_kind(self, thread_id: str) -> Optional[str]:
    """区分当前 interrupt 是 planning(interrupt#1) 还是 dev_loop_failure(interrupt#2)。

    读 get_interrupt_payload(thread_id)，返回 payload.get("interrupt_kind"):
        - "planning"          → 计划审核页（sp2 plan_review）
        - "dev_loop_failure"  → 执行监控页 dev_loop 失败决策面板（sp3）
    无 interrupt 返回 None。planning payload 无 interrupt_kind 键时默认按 "planning" 兜底（向后兼容 sp2）。
    """
```

> planning interrupt payload（planning.py L770-780）当前无 `interrupt_kind` 键。sp3 在 planning payload 加 `"interrupt_kind": "planning"`（planning.py 微改一行）以显式化；execution interrupt payload 带 `"interrupt_kind": "dev_loop_failure"`。**UI 路由分发靠此键**，不靠 `current_step`（更稳）。

新增 UI 路由常量（config.py）：
```python
STREAMLIT_PAGE_EXECUTION: str = "execution"   # 执行监控页
STREAMLIT_PAGE_REPORT: str = "report"         # 结果报告页
```

#### 2.6.2 执行监控页 `ui/pages/execution_monitor.py`

承接 plan_review「确认计划并开始编码」后的流程。沿用 sp2 `st_autorefresh`（`STREAMLIT_POLL_INTERVAL=1500ms`）轮询，主线程只读不阻塞工作线程（sp2 §4.3/§4.4 范式）。

- **进度展示**：`poll_state` 读 `current_step`（coding/execution/reporting）+ `fix_loop_count`/`MAX_FIX_LOOP_COUNT`（"修复第 N / 3 轮"）+ `fix_loop_history` 每轮摘要（错了什么 + 修复策略）。
- **sandbox 实时信息**：读 `execution_result.logs`（受 `output_truncated` 护栏约束，截断时标注）+ `runtime_seconds`。
- **错误/降级**：滚动展示 `node_errors`（解析 `[error_category=...]` 前缀）/ `degraded_nodes` 最近条目（沿用 sp2 一句话摘要 + 可展开详情）。
- **dev_loop 失败决策面板（承载 interrupt#2，UI 可简化）**：`is_interrupted(thread_id) and interrupt_kind(thread_id)=="dev_loop_failure"` 时，展示失败上下文摘要（`get_interrupt_payload` 取 `fix_loop_history`/`execution_result.errors`）+ 三个按钮 → 点击调 `resume_with(thread_id, {"decision": "terminate"|"revise_plan"|"export_code", ...})`（revise_plan 可附 `user_feedback` 文本框）。
- **流程结束跳转**：`current_step=="reporting"` 且 `report_path` 非空且非 interrupt → 自动跳 `STREAMLIT_PAGE_REPORT`。

> **轮询不阻塞 + interrupt 检测**：完全复用 sp2 `is_interrupted`（基于 snapshot.tasks interrupt 元数据，与节点无关），无需新机制。区分两个 interrupt 仅靠 `interrupt_kind`。execution 长耗时阶段轮询只读 snapshot，工作线程独立 SqliteSaver 写，互不阻塞（sp2 §4.3 已验证）。

#### 2.6.3 结果报告页 `ui/pages/result_report.py`

- `poll_state` 读 `report_path` → 读文件 → `st.markdown` 完整渲染。
- 顶部结论卡片（三形态：复现成功 / 仅生成代码 / 未成功复现 + 降级原因）。
- 指标对比表（论文 baseline vs 本次复现，B 档展示，不硬判定）+ artifact 清单 + 修复历程（`fix_loop_history`）+ 代码位置（`code_output_dir`）+ deliverables。
- "返回输入页开启新任务"出口（沿用 sp2 终止后出口范式）。
- F5 后 session_state 丢失但 SqliteSaver 保留（沿用 sp2 限制，不提供 thread_id 恢复入口）。

---

## 3. 数据流图

### 3.1 Sprint 3 完整数据流（含修复循环 + 两个 interrupt + code_only）

```
START
  │
  ▼
paper_intake ─→ paper_analysis ─→ resource_scout ─→ planning
                                                       │ (interrupt#1: 计划审核, sp2)
                                                       │  resume: approve/code_only/cancel/revise/switch_repo
                                                       ▼
                                          _route_after_planning (sp2 3 路, 零改动)
                                          ┌──────────┼───────────┐
                                       self(revise) next(approve  end(cancel)
                                          │          /code_only)     │
                                          ▼          ▼               ▼
                                       planning    coding          END
                                                     │
                                                     ▼
                                          _route_after_coding (sp3 新增)
                                          ┌──────────────┴───────────────┐
                                  to_execution(FULL)            skip_execution(CODE_ONLY)
                                          │                               │
                                          ▼                               │
                                     execution                            │
                          （sandbox 执行 + 错误分类 + B 档判定）            │
                          （函数体内: 修复耗尽/不可修复 → interrupt#2）      │
                                          │                               │
                                          ▼                               │
                            _route_after_execution (sp3 新增 4 路)         │
              ┌──────────┬───────────────┬──────────────┬────────────────┤
        coding(可修复    reporting(成功   planning(改计  end(终止          ▼
        +未超限+预算够)  /降级/导出代码)  划 revise_plan) terminate)   reporting
              │  ▲                                                   （三形态: full/code_only/降级）
              └──┘  修复回边                                              │
        (fix_loop_count+1,                                               ▼
         fix_loop_history append,                                       END
         走单点 read-modify-write)

interrupt#2 三态 resume (写 user_fix_decision, 经 _route_after_execution):
   terminate    → current_step="cancelled_by_user" → END (checkpoint 保留)
   revise_plan  → _planning_user_feedback + approved=False → planning 重入
   export_code  → degraded 标记 → reporting 降级形态 → END
```

### 3.2 修复循环回合的状态变迁（单 agent，整节点重跑语义）

```
回合 N (coding → execution):
  coding:    写 code_output_dir; 修复回合读 state.execution_result 反馈注入 HumanMessage;
             retry_budget_remaining 由 _make_react_wrapper 自动扣减 (按 ReAct round 数)
  execution: 跑 sandbox → 分类 ExecutionFeedback (节点本地, 不进 state);
             失败且可修复且 fix_count<3 且 budget>=2:
                fix_loop_count += 1 (单点);  fix_loop_history.append (read-modify-write);
                _dev_loop_route="retry_coding";  → 回 coding
             失败且(修复耗尽 or 不可修复): interrupt#2;  按 resume 三态路由
             失败且预算<2: 标 degraded → reporting (入口预算门)
             成功(B 档): → reporting

断点续跑语义 (兼容性矩阵 §C.2): 修复循环对主图是「整节点重跑」, 不做回合级 checkpoint_ns;
   dev_loop 不挂独立 checkpointer; 主图 SqliteSaver 在每个节点边界持久化 (与 sp2 一致)。
```

### 3.3 Streamlit 主线程 ↔ 工作线程数据流（沿用 sp2，新增第二个 interrupt 分发）

```
主线程 (UI, 只读 main_checkpointer)          工作线程 (独立 SqliteSaver, 写)
  poll_state(thread_id) ──读 snapshot.values──→ coding/execution/reporting 推进
  is_interrupted(thread_id) ──读 tasks[].interrupts──→ planning OR execution interrupt()
  interrupt_kind(thread_id):
     "planning"         → plan_review 页 (sp2)
     "dev_loop_failure" → execution_monitor 失败决策面板 (sp3)
  resume_with(thread_id, {"decision": ...}) ──新 daemon worker──→ Command(resume=...) 注入
```

---

## 4. 关键设计决策

### 4.1 coding 复用 `_make_react_wrapper`，execution 手写复合节点

coding 是「生成代码」的 ReAct agent，与 paper_intake/analysis 同构（工具循环 + 结构化产出），**直接复用 `_make_react_wrapper`** 白嫖节点级 LLM 路由 + 自动预算扣减 + Prompt Cache 前缀治理。execution 是「跑子进程 + 分类 + interrupt」的复合逻辑（无固定 ReAct 循环，metrics LLM 抽取是可选兜底），与 planning 同构 → **手写复合节点**。reporting 无 LLM 无 interrupt → **纯函数式**。三者形态各异但都满足 `(GlobalState) -> dict` 签名，可直接注册主图。

### 4.2 错误分类落「节点本地 ExecutionFeedback」而非 state 字段或 NodeError.error_type

**为什么不进 `NodeError.error_type`**：sp1/sp2 全链路约定 `error_type ∈ {transient, permanent, degraded}`（errors.py / 兼容性矩阵 §A.1）。把 syntax/import/timeout 等 8 类塞进去会污染既有三态语义，破坏 reporting/降级决策对三态的依赖。**为什么不进 GlobalState 字段**：单 agent 修复循环的分类是「过程量」，回合间不需要跨节点共享原始分类对象（coding 读的是裁剪后的反馈摘要 + `node_errors` 里的 `[error_category=...]` 前缀）。落 **节点本地 dataclass** 最轻：零 state 字段、零 reducer 风险、冒泡时映射三态 + 把细分类编码进 `error_message` 前缀。这与兼容性矩阵 §A.4「ExecutionFeedback 子图私有」的 multi-agent 版思路一致，单 agent 下进一步简化为节点本地对象。

### 4.3 第二个 interrupt 在 execution 函数体内（不新增节点）

见 §2.5.1 决策依据：硬约束（7 节点）+ 范式一致（sp2 planning 函数体内 interrupt）+ app.py 天然兼容（`is_interrupted` 与节点无关）。**关键正确性**：LangGraph `interrupt()` 在节点函数体内调用时，节点首次执行到 `interrupt()` 暂停；resume 后**整个 execution 节点从头重跑**到 `interrupt()` 处再拿到 resume 值（这是 LangGraph 语义）。因此 execution 必须保证 `interrupt()` **之前的副作用幂等**（sandbox 执行已写 `execution_result`，重跑会重复执行子进程）。**缓解**：execution 在 `interrupt()` 前先把 `execution_result` 写好（通过返回前的状态），但 interrupt 重跑会重跑 sandbox —— 这是 sp2 planning 已有的同款问题（planning ReAct 重跑），sp2 接受「interrupt 前逻辑重跑」。**sp3 缓解**：execution 在进入「修复耗尽/不可修复」分支前，先检查 `state` 是否已有本回合 `execution_result`（resume 重跑时复用，不重跑 sandbox）—— 见 §7 回问点 3（需 spike 验证 LangGraph 1.1.10 interrupt 重跑边界）。

### 4.4 单 agent 边界：不做 multi-agent / DevLoopState / 子图

严格遵守 PRD §1.1 / 硬约束：**不设计** supervisor/Coder/Executor/Reviewer 三 agent、**不设计** `DevLoopState`/`CodingOutput`/`ExecutionFeedback`（multi-agent 版）/ 共享 scratchpad（`Annotated[List, operator.add]`）、**不建** `core/subgraphs/dev_loop/`。修复循环是主图层 `coding↔execution` 回边（§2.5），过程数据走 coding 的 ReAct 私有 messages（react_base.py 已隔离）+ GlobalState 既有字段。`FixLoopRecord` **不追加** `reviewer_verdict`/`coder_confidence`/`agent_trace`（multi-agent 专属，顺延 sp4+，PRD §4.2）。

### 4.5 预算单点回写点（must-fix-2，PRD §5 落地）

| 节点 | 走 `_make_react_wrapper`？ | 预算扣减方式 | 回写点 |
|---|---|---|---|
| **coding** | 是 | 自动：react_base.py L889-894 按 ReAct round 数扣减，`setdefault("retry_budget_remaining", remaining)` | wrapper 内单点（既有，无需改） |
| **execution** | 否（手写） | 仅 metrics LLM 抽取兜底触发时扣减（实际调用次数，通常 1） | execution 返回 dict 单点 read-modify-write |
| **reporting** | 否（手写，无 LLM） | 零扣减（不调 LLM） | 无 |

```python
# execution 内 metrics LLM 抽取兜底触发时（§2.3.3 档 3）：
def _llm_extract_metrics(stdout, metric_names, state):
    calls_used = 0
    ... llm.invoke(...) ...; calls_used += 1
    # 把消耗次数挂到节点返回，execution 在 _map_execution_result 单点回写：
    #   updates["retry_budget_remaining"] = max(0, state.get("retry_budget_remaining",0) - calls_used)
```

**双重约束 + 子预算累计**（PRD §5.1）：修复循环子图内累计 LLM 调用数 = 各回合 coding 的 ReAct rounds + execution metrics 抽取次数之和。**记账方式**：用 `MAX_TOTAL_LLM_CALLS - retry_budget_remaining` 反推已用总量不够（含前序节点）；**新增一个内部累计字段 `_dev_loop_llm_calls: int`**（state.py 微增，下划线前缀，单值 int，last-write-wins 安全），每次 coding/execution 在修复回合内按实际调用数 read-modify-write 累加；execution 路由判定时检查 `_dev_loop_llm_calls < MAX_DEV_LOOP_LLM_CALLS`。
- **入口预算门**（§2.5.4）：execution 失败后判 `retry_budget_remaining < DEV_LOOP_MIN_CALLS_PER_ROUND(2)` → 直接降级，不回 coding 空转。
- **子预算触顶**：`_dev_loop_llm_calls >= MAX_DEV_LOOP_LLM_CALLS(20)` → 视同修复耗尽，走 interrupt#2。

> **回问点 4（§7）**：`_dev_loop_llm_calls` 是否必须新增 state 字段，还是可由 `fix_loop_history` 长度 × 平均回合成本估算？架构推荐显式字段（精确、AC-S3-04 可断言「累计达 20 终止」）。

### 4.6 list 字段单点 read-modify-write（must-fix-1，零 reducer）

`node_errors`/`degraded_nodes`/`fix_loop_history` 在 state.py 保持普通 `List`，**严禁加 `Annotated[List, operator.add]`**（AC-S3-05 grep 断言）。所有写入复用 sp1/sp2 范式（paper_analysis.py L436 / planning.py L523）：`list(state.get(field, []))` → append → `return` 整列表。修复循环涉及 coding + execution 两节点写这三字段，**各自在自己的 map_result 里 read-modify-write，单点写回**（coding 失败时写 node_errors/degraded_nodes；execution 失败/降级时写 node_errors/degraded_nodes/fix_loop_history）。因主图是顺序执行（coding → execution，非并发），无并发覆盖；LangGraph 对无 reducer 字段 last-write-wins，每节点 return 的是「读出旧值 + 本节点新增」的完整列表，不丢不重。

### 4.7 修复循环对 checkpointer 的协同（整节点重跑语义）

沿用兼容性矩阵 §C.2：修复循环对主图是「整节点重跑」，**不做回合级 `checkpoint_ns` 断点续跑**（MVP 不做，sp4+）。主图 SqliteSaver 在每个节点边界持久化（sp2 既有 WAL 模型，checkpointer.py 零改动）。中途崩溃从最近节点边界重跑。第二个 interrupt 复用同一 thread_id + 同一 SqliteSaver（app.py 每 worker 独立实例 + 共享 SQLite 文件 + WAL，sp2 §4.3 验证），不破坏既有持久化模型。

---

## 5. state 字段处置表

| 字段 | 类型 | sp3 处置 | 写入节点 | reducer | 备注 |
|---|---|---|---|---|---|
| `code_output_dir` | `Optional[str]` | 复用，首次写入 | coding | 无 | 代码目录绝对路径（workspace 下） |
| `execution_result` | `Optional[ExecutionResult]` | 复用，首次写入 | execution | 无 | B 档 success；coding 修复回合读它取反馈 |
| `report_path` | `Optional[str]` | 复用，首次写入 | reporting | 无 | 报告绝对路径 |
| `execution_mode` | `ExecutionMode` | 复用，首次消费 | （sp2 planning 写） | 无 | `_route_after_coding` 读它分流 code_only |
| `fix_loop_count` | `int` | 复用，首次激活 | execution（出口 +1） | 无 | 单点自增，粒度=1 回合（§2.5.2） |
| `fix_loop_history` | `List[FixLoopRecord]` | 复用，首次激活 | execution | **无（严禁加）** | read-modify-write（must-fix-1） |
| `user_fix_decision` | `Optional[str]` | 复用，首次激活 | execution（interrupt#2 resume） | 无 | terminate/revise_plan/export_code |
| `retry_budget_remaining` | `int` | 复用，新增扣减 | coding(wrapper 自动) / execution(LLM 抽取时) | 无 | last-write-wins，单点（§4.5） |
| `node_errors` | `List[NodeError]` | 复用 | coding / execution | **无（严禁加）** | read-modify-write；细分类进 message 前缀 |
| `degraded_nodes` | `List[str]` | 复用 | coding / execution | **无（严禁加）** | read-modify-write 去重追加 |
| `current_step` | `str` | 复用 | coding/execution/reporting | 无 | "coding"/"execution"/"reporting"/"cancelled_by_user" |
| `reproduction_plan` | `Optional[ReproductionPlan]` | 复用 | execution(revise_plan 时改 approved=False) | 无 | revise_plan 回流需清 approved |
| `_planning_user_feedback` | `Optional[str]` | 复用 | execution(revise_plan 时写修复上下文) | 无 | 复用 sp2 planning 回流字段 |
| **`_dev_loop_route`**（候选新增） | `Optional[str]` | **微增（下划线内部字段）** | execution | 无 | 路由意图标记（§2.5.3）；或采备选重算方案不加（§7 回问 1） |
| **`_dev_loop_llm_calls`**（候选新增） | `int` | **微增（下划线内部字段）** | coding/execution | 无 | 子预算累计（§4.5）；AC-S3-04 断言用 |

> **严禁加 reducer 强约束**：以上所有字段 **零 `Annotated`/`operator.add`**。候选新增的 2 个下划线内部字段都是单值（str/int），last-write-wins 正确，无需 reducer。`FixLoopRecord` **不追加** Optional 字段（单 agent 下 5 字段足够，PRD §4.2）。

> **state.py 改动范围**：理想为**零改动**（PRD §2.9 / 兼容性矩阵 §D.2 主张复用）。但路由意图标记 `_dev_loop_route` 与子预算累计 `_dev_loop_llm_calls` 若采显式字段方案，需在 `GlobalState` + `create_initial_state` 各加 2 行（向后兼容，单值默认 None/0，零回归）。**这是 sp3 对 state.py 的全部可能改动，且不碰任何 List 字段、不加任何 reducer**。是否采显式字段见 §7 回问点 1/4。

---

## 6. 对 sp1/sp2 既有代码的改动点清单（最小 diff 原则）

| 文件 | 改动类型 | 具体改动 | 风险 |
|---|---|---|---|
| `sandbox/__init__.py` | **新增** | 空包标识 | 无 |
| `sandbox/local_venv.py` | **新增** | venv 创建/执行/护栏/产物收集（§2.1） | 中（跨平台子进程） |
| `core/tools/code_fs_tools.py` | **新增** | 写/读/列代码文件工具（复用 git_tools 安全范式） | 低 |
| `core/nodes/coding.py` | **新增** | ReAct wrapper（§2.2） | 低（同构既有节点） |
| `core/nodes/execution.py` | **新增** | 手写复合 + 错误分类 + interrupt#2（§2.3） | 中（interrupt 重跑边界，§7） |
| `core/nodes/reporting.py` | **新增** | 三形态 Markdown（§2.4） | 低（纯函数式） |
| `core/graph.py` | **改路由** | ① import coding/execution/reporting 真实现替换占位函数；② `coding→execution` 顺序边换 `_route_after_coding` 条件边；③ `execution→reporting` 顺序边换 `_route_after_execution` 条件边；④ 删 L57-72 占位函数 | 中（路由正确性，AC-S3-10 断言节点数=7） |
| `config.py` | **新增常量** | `SANDBOX_*`（5 个）+ `MAX_DEV_LOOP_LLM_CALLS=20` + `DEV_LOOP_MIN_CALLS_PER_ROUND=2` + `REACT_MAX_ROUNDS_CODING=12` + 2 个 UI 路由常量；`MAX_FIX_LOOP_COUNT=3` 首次接线引用（值不变） | 低 |
| `core/state.py` | **微改（候选）** | 视 §7 回问 1/4 决定是否加 `_dev_loop_route`/`_dev_loop_llm_calls` 2 个下划线字段；**绝不碰 List 字段、绝不加 reducer** | 低（向后兼容） |
| `core/nodes/planning.py` | **微改** | interrupt payload 加一行 `"interrupt_kind": "planning"`（UI 区分两 interrupt，§2.6.1）；接收 revise_plan 回流（既有 self-loop 语义，无需改逻辑） | 低 |
| `app.py` | **新增方法** | `interrupt_kind(thread_id)` helper（§2.6.1）；UI 路由分发新增 2 页 | 低（沿用既有 GraphController） |
| `ui/pages/execution_monitor.py` | **新增** | 执行监控页 + dev_loop 失败决策面板（§2.6.2） | 低（沿用 sp2 轮询范式） |
| `ui/pages/result_report.py` | **新增** | 结果报告页三形态渲染（§2.6.3） | 低 |
| **零改动** | — | `core/react_base.py`（预算扣减点不动）、`core/checkpointer.py`、`core/errors.py`（SandboxError 家族已备）、`core/tools/git_tools.py`、sp1/sp2 既有节点 | 无 |

---

## 7. 架构师回问与风险提示

> 以下为架构师在落地 PRD 时识别的技术疑问/风险，**不擅自改需求**，列出供 Maria 拍板。
>
> **[决策记录 2026-06-16 Maria 拍板，全部采纳架构师推荐]**：
> - **回问点 1 + 4 = 接受**新增 2 个下划线内部字段 `_dev_loop_route`（路由意图：回 coding 修复 vs 到 reporting）+ `_dev_loop_llm_calls`（子预算累计计数）。均为单值、无 reducer、向后兼容；PRD §2.9 主张 state.py 零改动，此处**微破例已批准**（AC-S3-04 精确记账 + 路由不耦合 message 格式所必需）。
> - **回问点 2 = revise_plan 回 planning 时 `fix_loop_count` 清零、`fix_loop_history` 保留**（供报告审计），避免改完计划立刻因旧计数触顶。
> - **回问点 3 = 启动前置一个 spike**：验证 execution 函数体内 `interrupt()` 的 resume 重跑行为 + 「复用本回合 `execution_result`、不重跑 sandbox」的幂等保护方案，类比 sp2 S-1 spike，列入 sp3 dev-plan 启动前置第一项。

**回问点 1（state.py 是否加 `_dev_loop_route` 路由意图字段）**：`_route_after_execution` 需区分「回 coding 修复」与「到 reporting」。推荐用显式 `_dev_loop_route` 下划线字段（清晰、不重算）；备选是路由函数解析 `node_errors[-1]` 的 `[error_category=...]` + 复算 auto_fixable（state.py 零改动但路由函数耦合 message 格式）。**倾向显式字段（向后兼容、单值无 reducer 风险）**，请 Maria 确认是否接受 state.py 加 1 个下划线字段（PRD §2.9 主张零改动，此处需微破例）。

**回问点 2（revise_plan 回流后 fix_loop_count 是否清零）**：用户选 revise_plan 改计划重走 coding/execution，新计划的修复循环应从 `fix_loop_count=0` 重新计数，还是延续旧计数？PRD §2.7 未明确。**架构倾向：revise_plan 回 planning 时清零 `fix_loop_count`（但保留 `fix_loop_history` 供报告审计）**，否则改完计划立刻又因旧计数触顶。请 Maria 确认。

**回问点 3（interrupt#2 在 execution 函数体内的重跑幂等）**：LangGraph `interrupt()` 在节点函数体内调用时，resume 后整节点从头重跑到 interrupt 处。execution 在 interrupt 前已跑 sandbox（重操作），重跑会重复执行子进程（耗时 + 副作用）。**缓解方案**：execution 进入失败分支前检查 `state.execution_result` 是否为本回合已有结果，resume 重跑时复用不重跑 sandbox。**这需要一个启动前 spike 验证**（类比 sp2 S-1 spike 验证 planning interrupt 行为）。这是 sp3 唯一需要 spike 的高风险点，建议列入 dev-plan 启动前置。

**回问点 4（子预算累计 `_dev_loop_llm_calls` 字段）**：AC-S3-04 要求断言「修复循环累计 LLM 调用达 20 时终止」。精确记账需显式累计字段。备选是用 `MAX_TOTAL_LLM_CALLS - retry_budget_remaining`（含前序节点消耗，不纯）或 `fix_loop_history` 长度估算（不精确）。**推荐显式 `_dev_loop_llm_calls` 字段**（单值 int，无 reducer）。请 Maria 确认是否接受。

**风险提示（非阻塞，归 v1.x / sp4+）**：
- **R-A1 输出截断的内存窗口**：`communicate()` 先把全量输出读进内存再截断，疑似失控输出在截断前已占内存。MVP 靠超时护栏杀进程兜底；流式逐块截断写文件归 v1.x。
- **R-A2 工作目录限定的边界**：护栏只限定子进程 cwd，无法阻止子进程用绝对路径写 workspace 外（无 Docker 隔离）。MVP 接受，与 PRD §2.1「不上 Docker」一致；真隔离归 sp4+。
- **R-A3 B 档 metrics 解析**：依赖 coding 生成脚本打印 `<METRICS>` 约定标签提升成功率；LLM 抽取兜底消耗预算。极端异构输出仍可能解析失败 → success 误判 False，但 reporting 降级形态仍交付（R-S3-05 缓解）。

---

## 8. 给开发代理的实现约束清单（TODO / 文件级）

> 以下为文件级实现约束，供开发代理按依赖顺序落地。**不含生产代码**，仅约束与签名。

**P0 基础设施（无依赖，先行）**
- [ ] `config.py`：新增 `SANDBOX_EXEC_TIMEOUT/SANDBOX_VENV_CREATE_TIMEOUT/SANDBOX_PIP_INSTALL_TIMEOUT/SANDBOX_OUTPUT_MAX_BYTES/SANDBOX_PIP_MAX_RETRIES`、`MAX_DEV_LOOP_LLM_CALLS=20`、`DEV_LOOP_MIN_CALLS_PER_ROUND=2`、`REACT_MAX_ROUNDS_CODING=12`、`STREAMLIT_PAGE_EXECUTION/STREAMLIT_PAGE_REPORT`。`MAX_FIX_LOOP_COUNT=3` 值不变（首次接线引用）。`ensure_directories` 视需要加 sandbox 目录。
- [ ] `sandbox/__init__.py` + `sandbox/local_venv.py`：实现 `prepare_venv`/`run_in_venv`/`collect_artifacts`（§2.1.2 签名）；`_run_subprocess` 跨平台进程组 + 超时杀子树 + 输出字节截断（§2.1.3）；路径越界校验分两类（§2.1.3 / §2.1.5 sp3-B1 备案）：写入/产物路径用 `resolve()+is_relative_to`（复用 git_tools `_is_within_workspace` 范式），可执行入口 python_exe 用 lexical `abspath+is_relative_to`（避免 venv 符号链接误判）；子进程列表形式、禁 `shell=True`；任何异常兜底不逃逸。
- [ ] `core/tools/code_fs_tools.py`：`make_write_code_file_tool`/`make_read_code_file_tool`/`make_list_dir_tool`；序列化用 `json.dumps(ensure_ascii=False, sort_keys=True, default=str)`（禁 `str(dict)`）；路径越界校验；`@tool` + `try/except` 兜底。

**P1 节点真实现**
- [ ] `core/nodes/coding.py`：`coding = _make_react_wrapper(...)`；`_build_coding_context` 含修复回合反馈注入（读 `state.execution_result` + `fix_loop_count>0` 判定）；`_map_coding_result` 3 参签名（含 react_messages），写 `code_output_dir`/`current_step`，失败 degraded 走 read-modify-write；**不写 fix_loop_count**；coding system prompt 要求生成脚本打印 `<METRICS>...</METRICS>`。
- [ ] `core/nodes/execution.py`：手写 `execution(state)`；节点本地 `ErrorCategory`/`ExecutionFeedback`/`AUTO_FIXABLE`（不进 state.py）；`_classify_execution`（exit_code/stderr 关键字/timed_out）；`_parse_metrics` 三档（结构化标签/正则/LLM 抽取，LLM 抽取按实际次数回写预算）；`_build_execution_result`（B 档 success）；`_map_execution_result`（细分类进 `error_message` 前缀，三态映射，read-modify-write）；`_maybe_interrupt_or_return`（修复耗尽/不可修复 → 函数体内 `interrupt()`；可修复+未超限+预算够 → `fix_loop_count+1`+`fix_loop_history` append+路由标记）；`_route_user_fix_decision` 三态。**interrupt 重跑幂等需 spike（§7 回问 3）**。
- [ ] `core/nodes/reporting.py`：纯函数式 `reporting(state)`；`_determine_report_form` 三形态判定；`_render_report` 三形态 Markdown（语言策略：叙述中文、事实层英文）；写 `report_path`（resolve+is_relative_to）；`execution_result is None` 时仍产有效报告。

**P2 主图编排 + state 微调**
- [ ] `core/state.py`（视 §7 回问 1/4 拍板）：可能加 `_dev_loop_route: Optional[str]`/`_dev_loop_llm_calls: int` 2 个下划线字段 + `create_initial_state` 默认值；**绝不碰 node_errors/degraded_nodes/fix_loop_history、绝不加 reducer**（AC-S3-05 grep 断言保护）；`FixLoopRecord` 不追加字段。
- [ ] `core/graph.py`：import 三节点真实现替换占位；删 L57-72 占位函数；`coding→execution` 顺序边换 `_route_after_coding`（2 路）；`execution→reporting` 顺序边换 `_route_after_execution`（4 路）；planning 3 路边 + reporting→END 不动；保持节点数=7、编译成功（AC-S3-10）。
- [ ] `core/nodes/planning.py`：interrupt payload 加 `"interrupt_kind": "planning"`；确认 revise_plan 回流走既有 self-loop（execution 已清 `approved=False`，planning 重入正常重规划）。

**P3 UI**
- [ ] `app.py`：新增 `interrupt_kind(thread_id)` helper；UI 路由分发新增 execution_monitor / result_report 两页。
- [ ] `ui/pages/execution_monitor.py`：沿用 sp2 `st_autorefresh` 轮询；展示 `fix_loop_count`/history/logs（截断标注）/node_errors/degraded；`interrupt_kind=="dev_loop_failure"` 时三按钮 → `resume_with({"decision": ...})`；reporting 完成跳转报告页。
- [ ] `ui/pages/result_report.py`：渲染 `report_path` Markdown；三形态结论卡片 + 指标对比表 + artifact + 修复历程 + deliverables；返回输入页出口。

**P4 验收对齐（测试工程师协作）**
- [ ] 五条 e2e（happy path B 档 / 修复循环上限 3 / interrupt#2 三选一 / code_only / 降级）+ sandbox 4 护栏 mock + must-fix-1 grep 断言 + must-fix-2 预算回写断言；报告归档 `docs/sprint3/test-reports/`。

---

**文档结束**

*本文档为 Sprint 3 架构设计文档。数据结构权威定义以 `core/state.py` 为准；预算扣减唯一点以 `core/react_base.py` L889-894 为准；主图骨架基线以 `core/graph.py::build_graph` 为准；安全/序列化范式以 `core/tools/git_tools.py` 为准；interrupt/checkpointer 范式以 `core/nodes/planning.py` + `core/checkpointer.py` + `app.py` GraphController 为准。PRD 8 项已拍板决策据此落地，技术疑问/风险见 §7。*
