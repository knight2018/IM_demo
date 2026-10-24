"""入口：建立飞书长连接，把收到的消息交给注册分发器处理。

本文件只做三件事：
1. 建立并维护 WebSocket 长连接
2. 把飞书事件转换成框架内的 MessageContext
3. 交给 dispatcher 分发（具体由哪个功能处理，与本文件无关）
"""

import lark_oapi as lark
from lark_oapi.api.im.v1 import P2ImMessageReceiveV1

from core import config, feishu
from core.dispatcher import MessageContext
from core.lifecycle import start_control
from handlers import build_registry

registry = build_registry()


def build_context(data: P2ImMessageReceiveV1) -> MessageContext | None:
    """飞书事件 -> 统一的消息上下文；无法处理时返回 None。"""
    event = data.event
    if event is None or event.message is None:
        return None

    message = event.message
    if not message.chat_id:
        return None

    sender_id = "unknown"
    if event.sender and event.sender.sender_id:
        sender_id = event.sender.sender_id.open_id or "unknown"

    raw_text = feishu.parse_text(message)
    return MessageContext(
        message_id=message.message_id,
        chat_id=message.chat_id,
        chat_type=message.chat_type,
        sender_id=sender_id,
        text=feishu.strip_mentions(raw_text),
        mentioned=bool(message.mentions),
        raw={"raw_text": raw_text},
    )


def on_message_receive(data: P2ImMessageReceiveV1) -> None:
    """接收消息回调：只负责转译与投递，不做任何耗时操作。"""
    ctx = build_context(data)
    if ctx is None:
        return

    # 群聊默认只在被 @ 时响应，避免打扰群里其他人
    if ctx.chat_type == "group" and not ctx.mentioned and not config.GROUP_REPLY_ALL:
        return

    print(f"[message] chat={ctx.chat_id} from={ctx.sender_id} text={ctx.text!r}")
    registry.dispatch(ctx)


def main() -> None:
    start_control(registry, config.PROJECT_ROOT/'data')
    event_handler = (
        lark.EventDispatcherHandler.builder("", "")  # 长连接模式无需 Encryption Key / Token
        .register_p2_im_message_receive_v1(on_message_receive)
        .build()
    )

    ws_client = lark.ws.Client(
        config.FEISHU_APP_ID,
        config.FEISHU_APP_SECRET,
        event_handler=event_handler,
        log_level=getattr(lark.LogLevel, config.LOG_LEVEL, lark.LogLevel.INFO),
    )
    print("正在建立飞书长连接...")
    ws_client.start()  # 阻塞主线程，SDK 自动重连


if __name__ == "__main__":
    main()
