"""Pytest 全局配置：自动加载 .env + sys.path 注入。

设计：e2e 测试只靠凭证存在与否决定是否跑——
- `.env` / `~/.env` 中有 LLM_API_KEY + DEEPXIV_TOKEN → 真跑（直接 pytest 即可，IDE 插件同样生效）
- 任一凭证缺失 → 自动 skip，reason 可见

这样不再依赖命令行 flag，IDE 插件、CLI、CI 行为一致。
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# 自动加载 .env：项目根优先 > ~/.env（deepxiv CLI 自动注册写入位置）。
# 已存在的 env 变量（如 shell export）不会被覆盖。
try:
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env", override=False)
    load_dotenv(Path.home() / ".env", override=False)
except ImportError:
    pass
