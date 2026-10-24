"""handler 基类：定义一条消息如何被匹配与处理。

BaseHandler      所有处理器的协议
CommandHandler   按命令前缀匹配（如 /ping）
AgentHandler     持有 Agently Agent 的处理器，子类只需声明自己的工具与提示词
"""

from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import TYPE_CHECKING

from core import llm
if TYPE_CHECKING:
    from core.dispatcher import MessageContext


class BaseHandler(ABC):
    name = "base"
    priority = 0
    description = ""
    streaming = False

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
    """带 Agently Agent 的处理器。

    子类覆写 `system_prompt` 和 `register_actions()` 即可拥有自己的工具集，
    模型配置与通道细节由基类/框架负责。
    """

    system_prompt = "你是飞书里的智能助手，请用简洁清晰的中文回答。"

    def __init__(self) -> None:
        self._agent = None

    @property
    def agent(self):
        if self._agent is None:
            self._agent = self._build_agent()
        return self._agent

    def register_actions(self, agent) -> None:
        """子类在这里挂载自己的工具，默认不带工具。"""

    def _build_agent(self):
        agent = llm.create_agent()
        self.register_actions(agent)
        return agent

    def handle(self, ctx) -> str | None:
        result = (
            self.agent.instruct(self.system_prompt)
            .input(ctx.text)
            .start()
        )
        return str(result)
