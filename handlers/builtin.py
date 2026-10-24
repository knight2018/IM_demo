"""内置功能：/ping 回声、/help 功能列表。"""

from core.dispatcher import HandlerRegistry

from .base import CommandHandler


class PingHandler(CommandHandler):
    """链路自检：确认机器人在线且分发链路可用。"""

    local_only = True
    name = "ping"
    description = "/ping 检测机器人是否在线"
    priority = 100

    def __init__(self) -> None:
        super().__init__("/ping", priority=self.priority)

    def handle(self, ctx) -> str:
        return "pong，机器人在线"


class HelpHandler(CommandHandler):
    """列出当前注册的所有功能，方便知道有哪些能力。"""

    local_only = True
    name = "help"
    description = "/help 查看已注册的功能"
    priority = 200

    def __init__(self, registry: HandlerRegistry) -> None:
        super().__init__("/help", priority=self.priority)
        self._registry = registry

    def handle(self, ctx) -> str:
        lines = [h.description for h in self._registry.handlers if h.description]
        return "已注册功能：\n" + "\n".join(f"- {line}" for line in lines)
