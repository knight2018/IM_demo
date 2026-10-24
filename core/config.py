"""集中读取配置，缺关键项时快速失败。"""

import os
from pathlib import Path
from typing import Literal

from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv())


def _require(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"缺少配置项 {name}，请在 .env 或环境变量中补充")
    return value


# ---------- 飞书 ----------
FEISHU_APP_ID = os.getenv("FEISHU_APP_ID", "")
FEISHU_APP_SECRET = os.getenv("FEISHU_APP_SECRET", "")

# ---------- LLM（OpenAI 兼容协议） ----------
REPLY_BACKEND = os.getenv("REPLY_BACKEND", "codex")
if REPLY_BACKEND not in {"agently", "langchain", "api", "codex", "auto"}:
    raise RuntimeError("REPLY_BACKEND 只能为 langchain、api、codex 或 auto（兼容旧 agently 配置）")
# 优先用 LLM_API_KEY，未设置时回落到环境变量 AGI_KEY
LLM_API_KEY = os.getenv("LLM_API_KEY") or os.getenv("AGI_KEY")
LLM_BASE_URL = _require("LLM_BASE_URL") if REPLY_BACKEND == "agently" else os.getenv("LLM_BASE_URL", "https://api.agicto.cn/v1/")
LLM_MODEL = os.getenv("LLM_MODEL", "gemini-3.8-flash")

if REPLY_BACKEND == "agently" and not LLM_API_KEY:
    raise RuntimeError("缺少模型密钥，请设置 LLM_API_KEY 或 AGI_KEY")

# Codex 模式使用本机 Codex 登录，不使用 OpenAI 兼容模型接口的密钥。
PROJECT_ROOT = Path(__file__).resolve().parents[1]
CODEX_APP_SERVER_URL = os.getenv("CODEX_APP_SERVER_URL", "ws://127.0.0.1:4500")
CODEX_MODEL = os.getenv("CODEX_MODEL", "gpt-6.1-sol")
if os.getenv("CODEX_REASONING_EFFORT", "medium") != "medium":
    raise RuntimeError("本项目的 Codex 推理强度固定为 medium")
CODEX_REASONING_EFFORT: Literal['medium'] = 'medium'
CODEX_TIMEOUT_SECONDS = float(os.getenv("CODEX_TIMEOUT_SECONDS", "180"))
CODEX_STATE_PATH = PROJECT_ROOT / "data" / "codex-sessions.sqlite3"
CODEX_WORKSPACE = Path(os.getenv("CODEX_WORKSPACE", str(PROJECT_ROOT))).expanduser().resolve()

# ---------- 运行参数 ----------
# 飞书要求事件 3 秒内响应，耗时的 Agent 调用放到线程池执行
WORKER_THREADS = int(os.getenv("WORKER_THREADS", "8"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# 群聊里是否对所有消息都回复（true），还是只在被 @ 时回复（false）
GROUP_REPLY_ALL = os.getenv("GROUP_REPLY_ALL", "false").lower() == "true"
