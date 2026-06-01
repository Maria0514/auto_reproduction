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
MAX_TOTAL_LLM_CALLS: int = 50
MAX_FIX_LOOP_COUNT: int = 3


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


# ========== Sprint 2：Papers With Code API（pwc_tools） ==========

PWC_BASE_URL: str = "https://paperswithcode.com/api/v1"
PWC_RATE_LIMIT_RPS: int = 5  # 本地节流速率（5 req/s 即 200ms 间隔）
PWC_TIMEOUT_CONNECT: int = 5  # HTTP connect 超时（秒）
PWC_TIMEOUT_READ: int = 10  # HTTP read 超时（秒）


# ========== Sprint 2：Streamlit UI ==========

STREAMLIT_POLL_INTERVAL: int = 1500  # st_autorefresh 间隔（毫秒）
STREAMLIT_PAGE_INPUT: str = "input"  # UI 路由常量：论文输入页
STREAMLIT_PAGE_PROGRESS: str = "progress"  # UI 路由常量：分析进度页
STREAMLIT_PAGE_REVIEW: str = "review"  # UI 路由常量：计划审核页


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
