"""Pytest 全局配置：自动加载 .env + sys.path 注入。

设计：e2e 测试只靠凭证存在与否决定是否跑——
- `.env` / `~/.env` 中有 LLM_API_KEY + DEEPXIV_TOKEN → 真跑（直接 pytest 即可，IDE 插件同样生效）
- 任一凭证缺失 → 自动 skip，reason 可见

这样不再依赖命令行 flag，IDE 插件、CLI、CI 行为一致。
"""
from __future__ import annotations

import os
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


# LangSmith 追踪：测试一律不上传（Maria 2026-08-01 拍板）。
#
# 起因：上面两行 load_dotenv 会把 .env 里的 LANGSMITH_TRACING=true 读进来，于是**每个
# 单元测试都在往 LangSmith 上传 trace**——跑一轮全量回归（2287 用例）就能刷掉上千条，
# 而免费额度只有 5000 条/月。2026-07-30 配额耗尽（429 Monthly usage limit exceeded）
# 正是这么烧掉的：项目里出现 `ScriptedLLM`（测试专用假模型）记录即铁证。后果是**真跑
# 反而没配额可用**，可观测性被自己的回归测试挤没了。
#
# 必须放在 load_dotenv **之后**：放前面会被 .env 里的 true 覆盖回去。
# 两套变量名都要覆盖（LangChain 新旧命名并存），并抹掉 key —— 双保险。
# 逃生舱：确需在测试里追踪时，跑之前设 LANGSMITH_TRACING_IN_TESTS=1。
if os.getenv("LANGSMITH_TRACING_IN_TESTS") != "1":
    for _tracing_var in ("LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2", "LANGCHAIN_TRACING"):
        os.environ[_tracing_var] = "false"
    for _key_var in ("LANGSMITH_API_KEY", "LANGCHAIN_API_KEY"):
        os.environ.pop(_key_var, None)
