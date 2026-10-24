"""飞书通道层：客户端、消息解析与发送。"""

import json

import lark_oapi as lark
from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody
from lark_oapi.api.im.v1 import PatchMessageRequest, PatchMessageRequestBody

from . import config


def build_client() -> lark.Client:
    """构建飞书 OpenAPI 客户端，用于主动调用接口。"""
    return (
        lark.Client.builder()
        .app_id(config.FEISHU_APP_ID)
        .app_secret(config.FEISHU_APP_SECRET)
        .timeout(15)
        .log_level(getattr(lark.LogLevel, config.LOG_LEVEL, lark.LogLevel.INFO))
        .build()
    )


api_client = build_client()


def parse_text(message) -> str:
    """取出纯文本；非文本消息返回空串。"""
    if message.message_type != "text" or not message.content:
        return ""
    try:
        return json.loads(message.content).get("text", "").strip()
    except (json.JSONDecodeError, AttributeError):
        return ""


def strip_mentions(text: str) -> str:
    """去掉群消息里的 @占位符（形如 @_user_1），返回用户真正输入的内容。"""
    return " ".join(part for part in text.split() if not part.startswith("@_")).strip()


def send_text(chat_id: str, text: str) -> None:
    """向指定会话（私聊 chat_id 或群 chat_id）发送文本消息。"""
    body = (
        CreateMessageRequestBody.builder()
        .receive_id(chat_id)
        .msg_type("text")
        .content(json.dumps({"text": text}, ensure_ascii=False))
        .build()
    )
    request = (
        CreateMessageRequest.builder()
        .receive_id_type("chat_id")
        .request_body(body)
        .build()
    )
    response = api_client.im.v1.message.create(request)
    if not response.success():
        print(f"[send failed] chat={chat_id} code={response.code} msg={response.msg}")


def card_content(text: str, status: str) -> str:
    """使用可共享更新的卡片，避免普通文本消息编辑次数限制。"""
    return json.dumps({'schema': '2.0', 'config': {'update_multi': True},
        'header': {'title': {'tag': 'plain_text', 'content': status}},
        'body': {'elements': [{'tag': 'markdown', 'content': text}]}}, ensure_ascii=False)


def create_stream_card(chat_id: str) -> str:
    body = (CreateMessageRequestBody.builder().receive_id(chat_id).msg_type('interactive')
            .content(card_content('正在理解问题并生成回复…', '正在生成')).build())
    request = CreateMessageRequest.builder().receive_id_type('chat_id').request_body(body).build()
    response = api_client.im.v1.message.create(request)
    if not response.success() or response.data is None or not response.data.message_id:
        raise RuntimeError(f'创建飞书流式消息失败，错误码：{response.code}')
    return response.data.message_id


def update_stream_card(message_id: str, text: str, status: str) -> None:
    request = (PatchMessageRequest.builder().message_id(message_id)
               .request_body(PatchMessageRequestBody.builder().content(card_content(text, status)).build()).build())
    response = api_client.im.v1.message.patch(request)
    if not response.success():
        raise RuntimeError(f'更新飞书流式消息失败，错误码：{response.code}')
