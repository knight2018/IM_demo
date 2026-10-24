"""LangChain 路由入口和精确匹配的通道、模型命令。"""

from functools import lru_cache
from collections.abc import Callable

from langchain_core.runnables import RunnableLambda

from core import config
from core.api_chat import ApiChat
from core.dispatcher import MessageContext
from core.model_router import ModelRouter, USAGE
from core.routing_store import ModelSettings, RoutingStore
from handlers.base import BaseHandler
from handlers.codex_chat import bridge, prepare


@lru_cache(maxsize=1)
def router() -> ModelRouter:
    return ModelRouter(RoutingStore(config.PROJECT_ROOT/'data/model-routing.sqlite3',
        ModelSettings(config.REPLY_BACKEND, config.LLM_MODEL, config.CODEX_MODEL)),
        ApiChat(config.LLM_BASE_URL, config.LLM_API_KEY), bridge())


def generate(ctx: MessageContext) -> str:
    request = prepare(ctx)
    return router().answer(request['conversation_key'], request['text'])


class RoutedChatHandler(BaseHandler):
    name = 'model-router'
    description = 'API / Codex / auto 对话，使用 /model 查看当前配置'
    streaming = True

    def __init__(self) -> None:
        self.chain = RunnableLambda(generate)

    def match(self, ctx: MessageContext) -> bool:
        return True

    def handle(self, ctx: MessageContext) -> str:
        return self.chain.invoke(ctx) if ctx.text.strip() else '请发送文字或 /help'

    def handle_stream(self, ctx: MessageContext, on_update: Callable[[str], None]) -> str:
        if not ctx.text.strip():
            return '请发送文字或 /help'
        def stream(message: MessageContext) -> str:
            request = prepare(message)
            return router().answer(request['conversation_key'], request['text'], on_update=on_update)
        return RunnableLambda(stream).invoke(ctx)


class ModelCommandHandler(BaseHandler):
    name = 'model-commands'
    priority = 300
    description = USAGE

    def match(self, ctx: MessageContext) -> bool:
        words = ctx.text.strip().split()
        return bool(words) and words[0] in {'/use_model', '/model'}

    def handle(self, ctx: MessageContext) -> str:
        request = prepare(ctx)
        return router().command(request['conversation_key'], request['text'])
