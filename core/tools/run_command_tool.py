"""run_command_tool.py -- coding agent 轻量验证命令工具（S4-01）。

设计权威：docs/sprint4/dev-plan.md §4 任务 C1 + architecture §2.2（参考实现骨架）
/ §5（Q-B1 全部结论）/ §9.4（脱敏落点）。

职责：给 coding agent 一个"跑一下"的能力，闭合"写→跑→看→改"小循环。
**复用 `sandbox/local_venv.py::_run_subprocess` 的全部护栏**（禁 shell=True、
进程组隔离、超时杀子树、输出字节截断），零改动直接调用，不新造执行通道。

Q-B1 边界（architecture §5，职责红线）：
    - **系统解释器直跑 argv，不共用 venv**——依赖判定交 execution 的
      prepare_venv，coding 不做 pip install / 训练（路线丙两 agent 职责分离）；
    - 超时 ``RUN_COMMAND_TIMEOUT``（120s << SANDBOX_EXEC_TIMEOUT=1800s），从
      机制上封顶防跑重活，配合 docstring 约束双保险；
    - cwd 强制锚定工厂入参 ``base_dir``（coding 传 code_output_dir），经
      ``_require_within_workspace`` 校验，越界转结构化错误 + WARNING；
    - **不写 execution_result**（Q-B1 红线 3）：返回结构中无 metrics / success
      语义键，B 档判定无从消费——coding smoke 成功≠复现成功。

序列化治理（BUG-S1-02 范式）：返回值一律
``json.dumps(..., ensure_ascii=False, sort_keys=True, default=str)``；
解析失败 / 越界 / 启动失败均转结构化错误 JSON 返回，绝不抛异常炸子图
（``_run_subprocess`` 本身 OSError 兜底转 exit_code=-1，不逃逸）。

脱敏（architecture §9.4 落点表）：stdout/stderr 返回前经
``secrets_store.mask_value``——smoke 中 git clone 失败回显可能含 token URL。

Prompt Cache 纪律：``run_command`` 的 docstring 是工具 schema 的一部分，作为
稳定前缀参与 Prompt Cache——docstring 内零论文级 / 任务级动态变量。
"""

from __future__ import annotations

import json
import logging
import shlex
from typing import Dict, Optional

import config
from config import SANDBOX_PIP_CACHE_DIR
from core.secrets_store import mask_value
from sandbox.local_venv import _require_within_workspace, _run_subprocess

logger = logging.getLogger(__name__)


def _error_json(message: str) -> str:
    """结构化错误 JSON（不抛异常；BUG-S1-02：合法 JSON + sort_keys）。

    注意：错误结构中同样无 metrics / success 语义键（Q-B1 红线 3）。
    """
    return json.dumps(
        {"error": message, "exit_code": -1},
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )


def make_run_command_tool(base_dir: str, extra_env: Optional[Dict[str, str]] = None):
    """工厂：闭包绑定 ``base_dir`` 作 cwd + 已收集凭证 ``extra_env``。

    coding 节点传 ``base_dir=code_output_dir``、
    ``extra_env=build_credential_env(load_all_secrets())``（凭证注入 smoke，
    如 clone 私有仓库；GIT_TERMINAL_PROMPT=0 保证认证失败立即返回不挂起）。
    """
    from langchain_core.tools import tool

    @tool
    def run_command(command: str) -> str:
        """在代码目录下运行一条【轻量验证】命令（如 python -c "import x" / python -m py_compile x.py）。

        仅用于 smoke 级自查：import 能否通过、脚本能否启动、语法是否正确。
        禁止用于完整训练 / 评估 / 下载大数据集——那是 execution 阶段的职责，
        且本工具超时很短（约 2 分钟），超时会强制终止命令。
        命令用系统解释器执行（无项目 venv 依赖）；工作目录固定为代码输出目录。

        Args:
            command: 单条 shell 风格命令字符串（内部按 shlex 解析为 argv，
                不经 shell，管道 / 重定向 / && 等 shell 语法不可用）。

        Returns:
            JSON 字符串 {exit_code, stdout_tail, stderr_tail, timed_out, truncated}。
        """
        # 1) shlex 解析：失败 → 结构化错误 JSON（不抛异常炸子图）。
        try:
            argv = shlex.split(command)
        except ValueError as exc:
            return _error_json(f"命令解析失败: {exc}")
        if not argv:
            return _error_json("空命令")

        # 2) 护栏：cwd 锚定 base_dir 并校验在 WORKSPACE_DIR 下
        #    （越界抛 SandboxCreationError → 捕获转结构化错误 + WARNING）。
        try:
            _require_within_workspace(base_dir, label="run_command cwd")
        except Exception as exc:  # noqa: BLE001 — SandboxCreationError 等一律转结构化错误
            logger.warning(
                "run_command: 工作目录越界，拒绝执行: base_dir=%s, error=%s",
                base_dir, exc,
            )
            return _error_json(f"工作目录越界: {exc}")

        # 3) 系统解释器直跑 argv（4 护栏全量复用；config 常量运行期动态读取，
        #    便于测试 monkeypatch 短超时验证超时护栏）。
        # Sprint 6 MF-1：注入 PIP_CACHE_DIR（覆盖沙箱子进程环境），与 _build_sandbox_env
        # 保持同点注入语义，防止 coding smoke 中 pip install 打爆 home 配额。
        effective_env = dict(extra_env) if extra_env else {}
        effective_env["PIP_CACHE_DIR"] = str(SANDBOX_PIP_CACHE_DIR)
        rr = _run_subprocess(
            argv,
            cwd=base_dir,
            timeout=config.RUN_COMMAND_TIMEOUT,
            output_max_bytes=config.SANDBOX_OUTPUT_MAX_BYTES,
            extra_env=effective_env,
        )

        # 4) 返回 JSON（BUG-S1-02 范式）；stdout/stderr 经 mask_value 脱敏
        #    （§9.4：clone 失败回显可能含 token）。
        #    结构中无 metrics / success 语义键（Q-B1 红线 3：不写 execution_result）。
        return json.dumps(
            {
                "exit_code": rr.exit_code,
                "stdout_tail": mask_value(rr.stdout),
                "stderr_tail": mask_value(rr.stderr),
                "timed_out": rr.timed_out,
                "truncated": rr.output_truncated,
            },
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )

    return run_command
