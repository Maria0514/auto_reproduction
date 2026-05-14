import os
from pathlib import Path
from typing import Optional


# ========== 路径配置 ==========

PROJECT_ROOT: Path = Path(__file__).parent.resolve()
CHECKPOINT_DB_PATH: Path = PROJECT_ROOT / "checkpoints.db"
WORKSPACE_DIR: Path = PROJECT_ROOT / "workspace"
LOG_DIR: Path = WORKSPACE_DIR / "logs"


# ========== LLM 默认配置 ==========

DEFAULT_LLM_TEMPERATURE: float = 0.3
DEFAULT_LLM_MAX_TOKENS: int = 4096
DEFAULT_LLM_BASE_URL: str = "https://inference-api.nvidia.com/v1"
DEFAULT_LLM_MODEL: str = "aws/anthropic/claude-opus-4-6"
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
