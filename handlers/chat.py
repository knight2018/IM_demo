"""默认对话处理器：所有未被其它 handler 接走的消息最终落到这里。"""

from .base import AgentHandler


class ChatHandler(AgentHandler):
    name = "chat"
    description = "其它情况进入通用对话（不带工具）"

    # 兜底处理器，始终匹配
    def match(self, ctx) -> bool:
        return True

    def handle(self, ctx) -> str | None:
        if not ctx.text:
            return "你可以直接发送文字，或试试 /help"
        return super().handle(ctx)
