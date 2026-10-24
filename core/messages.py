"""平台无关的消息上下文。"""

from dataclasses import dataclass, field
from typing import TypedDict


class MessageMetadata(TypedDict, total=False):
    raw_text: str


@dataclass
class MessageContext:
    """一次消息处理的上下文，handler 只能读这里的数据。"""

    message_id: str
    chat_id: str
    chat_type: str
    sender_id: str
    text: str          # 已去掉 @占位符的用户文本
    mentioned: bool    # 群聊中是否被 @
    raw: MessageMetadata = field(default_factory=dict)
    channel: str = "feishu"
    account_id: str = "default"

