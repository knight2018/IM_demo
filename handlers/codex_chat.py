"""LangChain 调用链：将飞书消息转为 Codex 会话输入，返回完整回复。"""

from functools import lru_cache

from langchain_core.runnables import RunnableLambda

from core import config
from core.codex import CodexBridge, ReplyInput
from core.dispatcher import MessageContext
from .base import BaseHandler


@lru_cache(maxsize=1)
def bridge() -> CodexBridge:
    return CodexBridge(config.CODEX_APP_SERVER_URL, config.CODEX_STATE_PATH,
                       config.CODEX_WORKSPACE, config.CODEX_MODEL, config.CODEX_REASONING_EFFORT,
                       timeout=config.CODEX_TIMEOUT_SECONDS)


def prepare(ctx: MessageContext) -> ReplyInput:
    # 群内不同用户不共享私有上下文，同一用户在不同群或私聊也互相隔离。
    if not ctx.chat_id or not ctx.sender_id or ctx.sender_id == 'unknown':
        raise ValueError('缺少用户或会话标识，无法建立独立对话')
    return {'conversation_key': f'{ctx.chat_id}:{ctx.sender_id}', 'text': ctx.text}


def generate(request: ReplyInput) -> str:
    return bridge().reply(request)


class CodexChatHandler(BaseHandler):
    name = 'codex-chat'
    description = '文字对话使用 Codex GPT-6.1-Sol（medium），同一用户会话保持上下文'

    def __init__(self) -> None:
        self.chain = RunnableLambda(prepare) | RunnableLambda(generate)

    def match(self, ctx: MessageContext) -> bool:
        return True

    def handle(self, ctx: MessageContext) -> str:
        if not ctx.text.strip():
            return '你可以直接发送文字，或试试 /help'
        return self.chain.invoke(ctx)
