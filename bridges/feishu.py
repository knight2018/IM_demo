"""飞书桥接保留原匹配和发送行为，把实际执行委托给独立网关。"""

from collections.abc import Callable
from core.dispatcher import HandlerRegistry, MessageContext
from gateway.client import GatewayClient
from gateway.protocol import IncomingMessage
from handlers.base import BaseHandler
from core import feishu
from core.streaming import CardStream


class FeishuDelivery:
    """只有飞书桥接装配飞书发送能力。"""

    def send_text(self, chat_id: str, text: str) -> None:
        feishu.send_text(chat_id, text)

    def start_stream(self, chat_id: str) -> CardStream:
        return CardStream(chat_id)


class RemoteHandler(BaseHandler):
    def __init__(self, original: BaseHandler, client: GatewayClient) -> None:
        self.original, self.client = original, client
        self.name, self.description = original.name, original.description
        self.priority, self.streaming = original.priority, original.streaming

    def match(self, ctx: MessageContext) -> bool:
        return self.original.match(ctx)

    def handle(self, ctx: MessageContext) -> str:
        return self.client.reply(IncomingMessage.from_context(ctx))

    def handle_stream(self, ctx: MessageContext, on_update: Callable[[str], None]) -> str:
        return self.client.reply(IncomingMessage.from_context(ctx), on_update)


def attach_gateway(registry: HandlerRegistry, client: GatewayClient) -> HandlerRegistry:
    registry._handlers = [handler if handler.local_only else RemoteHandler(handler, client)
                          for handler in registry.handlers]
    if registry._default is not None:
        registry._default = RemoteHandler(registry._default, client)
    return registry
