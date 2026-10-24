"""handlers 包：在这里完成功能注册。

新增一个功能只需两步：
1. 在 handlers/ 下新建模块，继承 CommandHandler 或 AgentHandler 实现 match/handle
2. 在 build_registry() 里 register 进去
"""

from core.dispatcher import HandlerRegistry
from core import config
from core.delivery import MessageDelivery

from .builtin import HelpHandler, PingHandler
from .chat import ChatHandler
from .tools_agent import ToolAgentHandler


def build_registry(delivery: MessageDelivery | None = None) -> HandlerRegistry:
    """装配注册表：这里决定"谁能处理什么"。"""
    registry = HandlerRegistry(delivery)

    registry.register(PingHandler())
    if config.REPLY_BACKEND in {'agently', 'langchain'}:
        registry.register(ToolAgentHandler())
    registry.register(HelpHandler(registry))
    # 兜底最后注册，保证只在没有更合适的 handler 时接手
    if config.REPLY_BACKEND in {'api', 'codex', 'auto'}:
        from .model_routing import ModelCommandHandler, RoutedChatHandler
        registry.register(ModelCommandHandler())
        registry.register(RoutedChatHandler(), default=True)
    else:
        registry.register(ChatHandler(), default=True)

    return registry
