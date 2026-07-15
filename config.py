import os
from pathlib import Path
from typing import Optional


# ========== 路径配置 ==========

PROJECT_ROOT: Path = Path(__file__).parent.resolve()
CHECKPOINT_DB_PATH: Path = PROJECT_ROOT / "checkpoints.db"
WORKSPACE_DIR: Path = PROJECT_ROOT / "workspace"
LOG_DIR: Path = WORKSPACE_DIR / "logs"
# Sprint 2：resource_scout git clone 落盘目录（加入 ensure_directories 自动创建）
WORKSPACE_REPOS_DIR: Path = WORKSPACE_DIR / "repos"


# ========== LLM 默认配置 ==========

DEFAULT_LLM_TEMPERATURE: float = 0.3
DEFAULT_LLM_MAX_TOKENS: int = 8192
# 2026-05-14 4096 -> 8192：为 reasoning 模型（如 GPT-5 系列）的 reasoning_tokens 占用留余量；
# paper_analysis (C2) 输出体量较大，4096 边界过紧；
# max_tokens 不参与 prompt cache key，不影响命中率。
DEFAULT_LLM_BASE_URL: str = "https://inference-api.nvidia.com/v1"
DEFAULT_LLM_MODEL: str = "azure/openai/gpt-5.4"
LLM_REQUEST_TIMEOUT: int = 60


# ========== 重试预算 ==========

MAX_NODE_LLM_CALLS: int = 10
MAX_TOTAL_LLM_CALLS: int = 120
MAX_FIX_LOOP_COUNT: int = 10


# ========== LLM 客户端重试配置 ==========

LLM_MAX_RETRIES: int = 3
LLM_INITIAL_RETRY_DELAY: float = 2.0


# ========== Prompt Cache 配置（方案 A：前缀治理） ==========
# 决策来源：架构文档 §2.6.6 / 技术架构文档 §10.5
# 仅作为只读开关使用，不影响 create_llm 签名，不引入 provider 分支。

def _parse_bool_env(name: str, default: bool) -> bool:
    """解析 env 中的 bool 值；"false"/"0"/"no"/"off" (大小写不敏感) 视为 False。"""
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"false", "0", "no", "off", ""}


LLM_ENABLE_PROMPT_CACHE: bool = _parse_bool_env("LLM_ENABLE_PROMPT_CACHE", True)


# ========== ReAct 配置 ==========

REACT_MAX_ROUNDS_PAPER_INTAKE: int = 5
REACT_MAX_ROUNDS_PAPER_ANALYSIS: int = 12
REACT_LLM_TEMPERATURE: float = 0.3
REACT_RESULT_TAG_OPEN: str = "<result>"
REACT_RESULT_TAG_CLOSE: str = "</result>"
TOOL_RESULT_MAX_LENGTH: int = 8000

# Sprint 2 新增 ReAct 轮数上限（沿用 sp1 字面量风格，无 env 覆盖）
REACT_MAX_ROUNDS_RESOURCE_SCOUT: int = 10
REACT_MAX_ROUNDS_PLANNING: int = 8


# ========== Sprint 2：planning 人在回路 ==========
# revise 无次数硬上限（依赖 MAX_TOTAL_LLM_CALLS 总预算兜底）；
# 仅当 revise_count >= 此阈值时 UI 展示"是否切 code_only"软提示卡片（不锁按钮）。
# 决策来源：PRD §2.3 / AC-S2-06，硬上限语义已废弃。

PLANNING_SOFT_HINT_THRESHOLD: int = 5


# ========== Sprint 2：git_tools（resource_scout 仓库克隆与探测） ==========

GIT_CLONE_TIMEOUT: int = 60  # git clone 子进程超时（秒）
GIT_CLONE_DEPTH: int = 1  # 浅克隆 depth
URL_REACHABLE_TIMEOUT: int = 5  # check_url_reachable HEAD 探测超时（秒）



# ========== Sprint 2：Streamlit UI ==========

STREAMLIT_POLL_INTERVAL: int = 1500  # st_autorefresh 间隔（毫秒）
STREAMLIT_PAGE_INPUT: str = "input"  # UI 路由常量：论文输入页
STREAMLIT_PAGE_PROGRESS: str = "progress"  # UI 路由常量：分析进度页
STREAMLIT_PAGE_REVIEW: str = "review"  # UI 路由常量：计划审核页


# ========== Sprint 3：sandbox 本地 venv 执行护栏（S3-01 / architecture §2.1.1） ==========
# 沿用 sp1/sp2 字面量风格（同款 timeout/max_rounds 均为纯字面量，不加 os.getenv 覆盖）。
# sandbox venv/code/report 均落在 WORKSPACE_DIR/<thread>/ 下，复用既有 WORKSPACE_DIR，
# 无需独立常量目录，ensure_directories() 不变。

# Sprint 6 MF-1：pip 缓存落 /data 卷，防止打爆 home 配额（架构 §7.8 MF-1 / A-2）。
# 沙箱子进程 _build_sandbox_env 无条件覆盖 PIP_CACHE_DIR 为此路径，不用 --no-cache-dir
# 方案（环境变量单点覆盖覆盖所有路径，含 agent 自行敲的 pip install）。
SANDBOX_PIP_CACHE_DIR: Path = WORKSPACE_DIR / "pip-cache"

SANDBOX_EXEC_TIMEOUT: int = 1800  # 单条执行步骤子进程超时（秒，30 分钟；疑似死循环判据）
SANDBOX_VENV_CREATE_TIMEOUT: int = 300  # `python -m venv` 创建超时（秒）
SANDBOX_PIP_INSTALL_TIMEOUT: int = 1200  # 单次 pip install 超时（秒，20 分钟）
SANDBOX_OUTPUT_MAX_BYTES: int = 1_048_576  # stdout/stderr 各自捕获字节上限（1 MiB），超限截断
SANDBOX_PIP_MAX_RETRIES: int = 2  # pip install 网络瞬态失败重试次数


# ========== Sprint 3：dev_loop 修复循环子预算（S3-08 / architecture §2.1.1） ==========
# MAX_DEV_LOOP_LLM_CALLS 强约束 < MAX_TOTAL_LLM_CALLS（60 < 120），修复循环子预算天花板。

MAX_DEV_LOOP_LLM_CALLS: int = 60  # 修复循环子预算天花板（强约束 < MAX_TOTAL_LLM_CALLS=120）
DEV_LOOP_MIN_CALLS_PER_ROUND: int = 2  # 入口预算门：单回合最小 LLM 调用数
REACT_MAX_ROUNDS_CODING: int = 12  # coding 节点 ReAct max_rounds


# ========== Sprint 3：Streamlit UI 新增页面路由常量（S3-07 / architecture §2.5） ==========

STREAMLIT_PAGE_EXECUTION: str = "execution"  # UI 路由常量：执行监控页
STREAMLIT_PAGE_REPORT: str = "report"  # UI 路由常量：结果报告页


# ========== Sprint 4：execution 内嵌子图 / run_command / secrets（S4-10 / architecture §12.2） ==========
# 沿用 sp1~sp3 字面量风格（无 env 覆盖）。不新增交互超时常量（Q-F1 Maria 已定一直暂停）。

REACT_MAX_ROUNDS_EXECUTION: int = 10  # execution 内嵌子图轮次 FLOOR（sp5 起语义收窄为预算联动公式下限，见 Sprint 5 段落；budget_check_node 消费）
RUN_COMMAND_TIMEOUT: int = 120  # coding run_command 短超时（秒，机制上防跑重活；远小于 SANDBOX_EXEC_TIMEOUT=1800）
SECRETS_FILE_NAME: str = ".secrets"  # secrets 文件名；实际路径 = Path(workspace_dir) / SECRETS_FILE_NAME（运行期 state 优先，回退 config.WORKSPACE_DIR）


# ========== Sprint 5：execution 预算联动 / agent 活动流（S5-06 / S5-07 / architecture §3 / §4 / §8） ==========
# 沿用 sp1~sp4 字面量风格（无 env 覆盖）。
# 预算联动公式（architecture §3）：effective_max_rounds = clamp(len(execution_steps) + K, FLOOR, CAP)
#   K = REACT_EXECUTION_ROUNDS_MARGIN；FLOOR = REACT_MAX_ROUNDS_EXECUTION（值 10 不变，语义收窄为下限）；
#   CAP = REACT_MAX_ROUNDS_EXECUTION_CAP。

REACT_EXECUTION_ROUNDS_MARGIN: int = 5  # 预算联动 K 裕量（prepare 1 + 收尾 1 + 兜底 3）
REACT_MAX_ROUNDS_EXECUTION_CAP: int = 30  # 联动硬上限（= MAX_DEV_LOOP_LLM_CALLS/2 = 60/2，保修复循环余量）
ACTIVITY_STREAM_MAX_EVENTS: int = 500  # per-thread deque maxlen（单事件 ≤~300B，内存上界 ~150KB/任务）
ACTIVITY_STREAM_RENDER_TAIL: int = 30  # 执行监控页活动流尾部渲染行数（复用 st_autorefresh 1500ms 节奏）

# S6-B2（T-S6-2-5）：NO_METRICS 早停阈值——连续此轮数零指标则跳过 retry_coding，
# 走 interrupt#2 通道（无进展口径 = 类别连续复现）。
NO_METRICS_EARLY_STOP_ROUNDS: int = 2


# ========== 环境变量读取 ==========

def get_deepxiv_token() -> Optional[str]:
    return os.environ.get("DEEPXIV_TOKEN")


def get_llm_api_key() -> Optional[str]:
    return os.environ.get("LLM_API_KEY")


def get_llm_base_url() -> str:
    return os.environ.get("LLM_BASE_URL", DEFAULT_LLM_BASE_URL)


def get_llm_model() -> str:
    return os.environ.get("LLM_MODEL", DEFAULT_LLM_MODEL)


# ========== 目录初始化 ==========

def ensure_directories() -> None:
    WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    WORKSPACE_REPOS_DIR.mkdir(parents=True, exist_ok=True)
    SANDBOX_PIP_CACHE_DIR.mkdir(parents=True, exist_ok=True)
