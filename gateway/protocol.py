"""统一消息协议；平台与机器人账号参与会话隔离。"""

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
from core.messages import MessageContext


class IncomingMessage(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    channel: str = Field(min_length=1, max_length=64, pattern=r'^[a-z][a-z0-9_-]*$')
    account_id: str = Field(default='default', min_length=1, max_length=256)
    message_id: str = Field(min_length=1, max_length=256)
    chat_id: str = Field(min_length=1, max_length=256)
    sender_id: str = Field(min_length=1, max_length=256)
    chat_type: Literal['p2p', 'group'] = 'p2p'
    text: str = Field(max_length=50000)
    mentioned: bool = False

    def context(self) -> MessageContext:
        if self.sender_id == 'unknown':
            raise ValueError('必须提供有效用户标识')
        return MessageContext(**self.model_dump())

    @classmethod
    def from_context(cls, ctx: MessageContext) -> 'IncomingMessage':
        return cls(**{name: getattr(ctx, name) for name in cls.model_fields})


class ReplyEvent(BaseModel):
    model_config = ConfigDict(extra='forbid')
    type: Literal['started', 'snapshot', 'completed', 'error']
    text: str = ''
    request_id: str
