"""handler 基类：定义一条消息如何被匹配与处理。

BaseHandler      所有处理器的协议
CommandHandler   按命令前缀匹配（如 /ping）
AgentHandler     使用 LangChain 工具循环 的处理器，子类只需声明自己的工具与提示词
"""

from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import TYPE_CHECKING

from core import llm
from langchain_core.tools import BaseTool
if TYPE_CHECKING:
    from core.dispatcher import MessageContext


class BaseHandler(ABC):
    name = "base"
    priority = 0
    description = ""
    streaming = False
    local_only = False

    def handle_stream(self, ctx: 'MessageContext', on_update: Callable[[str], None]) -> str | None:
        """原有处理器保持一次性回复，只有显式启用的处理器走流式通道。"""
        return self.handle(ctx)

    @abstractmethod
    def match(self, ctx) -> bool:
        """是否由本 handler 处理该消息。"""

    @abstractmethod
    def handle(self, ctx) -> str | None:
        """返回要发送的回复文本；返回 None/空则不发送。"""


class CommandHandler(BaseHandler):
    """命令式匹配：`/echo hello` 这类前缀触发。"""

    def __init__(self, command: str, priority: int = 100) -> None:
        self.command = command
        self.priority = priority

    def match(self, ctx) -> bool:
        return ctx.text.startswith(self.command)

    @abstractmethod
    def handle(self, ctx) -> str | None:
        ...


class AgentHandler(BaseHandler):
    """LangChain 工具助手，子类声明提示词与工具列表。"""

    system_prompt = "你是 IM 中的智能助手，请用简洁清晰的中文回答。"

    def tools(self) -> list[BaseTool]:
        return []

    def handle(self, ctx: 'MessageContext') -> str:
        return llm.run_agent(self.system_prompt, ctx.text, self.tools())
