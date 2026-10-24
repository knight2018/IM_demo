"""Agently 模型工厂：统一创建已配置好的 Agent。

模型配置只在这里落地一次，业务 handler 按需派生自己的 Agent，
避免每个 handler 重复处理 provider/auth/base_url 细节。
"""

from functools import lru_cache

from agently import Agently
from agently.types.settings import OpenAICompatibleSettings

from . import config

@lru_cache(maxsize=1)
def provider_settings() -> OpenAICompatibleSettings:
    """返回 OpenAI 兼容服务的 settings。"""
    return OpenAICompatibleSettings(
        base_url=config.LLM_BASE_URL,
        model=config.LLM_MODEL,
        auth={"api_key": config.LLM_API_KEY},
    )


def create_agent():
    """创建一个带全局模型配置的 Agent。"""
    agent = Agently.create_agent()
    agent.set_settings(provider_settings())
    return agent


def create_request():
    """创建一次性的模型请求对象（不经过 Agent 会话状态）。"""
    request = Agently.create_request()
    request.set_settings(provider_settings())
    return request
