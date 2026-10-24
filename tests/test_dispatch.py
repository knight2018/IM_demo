"""临时冒烟脚本：验证注册分发路由是否正确（不调用真实模型）。"""

import time
from unittest import mock

from bridges.feishu import FeishuDelivery
from core.dispatcher import MessageContext
from handlers import build_registry


def make_ctx(text: str, chat_type: str = "p2p", mentioned: bool = True) -> MessageContext:
    return MessageContext(
        message_id=f"test-{abs(hash(text))}",
        chat_id="chat-test",
        chat_type=chat_type,
        sender_id="user-test",
        text=text,
        mentioned=mentioned,
    )


registry = build_registry(FeishuDelivery())

print("--- 路由 ---")
cases = ["/ping", "/help", "现在几点了", "12*35 等于多少", "给我讲个笑话"]
for text in cases:
    handler = registry.resolve(make_ctx(text))
    print(f"{text!r:20} -> {handler.name}")

print("--- 幂等去重 ---")
with mock.patch("core.feishu.send_text") as send:
    ctx = make_ctx("/ping")
    registry.dispatch(ctx)
    registry.dispatch(ctx)  # 同一个 message_id，应被跳过
    time.sleep(0.5)
    print("实际发送次数:", send.call_count, "内容:", send.call_args)

print("--- /help 输出 ---")
with mock.patch("core.feishu.send_text") as send:
    registry.dispatch(make_ctx("/help"))
    time.sleep(0.5)
    print(send.call_args[0][1] if send.call_args else "无输出")


# 以下断言将原有冒烟检查纳入全量回归，不改变已有路由实现。
def test_original_agently_routes(monkeypatch) -> None:
    from core import config
    monkeypatch.setattr(config, 'REPLY_BACKEND', 'agently')
    current = build_registry(FeishuDelivery())
    for text, name in [('/ping', 'ping'), ('/help', 'help'), ('现在几点了', 'tool-agent'),
                       ('12*35 等于多少', 'tool-agent'), ('讲个笑话', 'chat')]:
        assert current.resolve(make_ctx(text)).name == name
    current.shutdown()


def test_codex_routes_and_deduplication(monkeypatch) -> None:
    from core import config
    monkeypatch.setattr(config, 'REPLY_BACKEND', 'codex')
    current = build_registry(FeishuDelivery())
    assert current.resolve(make_ctx('现在几点了')).name == 'model-router'
    assert current.resolve(make_ctx('/help')).name == 'help'
    with mock.patch('core.feishu.send_text') as send:
        ctx = make_ctx('/ping codex test')
        current.dispatch(ctx)
        current.dispatch(ctx)
        current.shutdown()
        send.assert_called_once_with('chat-test', 'pong，机器人在线')


def test_help_and_failed_generation(monkeypatch) -> None:
    from core import config
    monkeypatch.setattr(config, 'REPLY_BACKEND', 'codex')
    current = build_registry(FeishuDelivery())
    ctx = make_ctx('/help')
    assert '/ping' in current.resolve(ctx).handle(ctx)
    handler = current.resolve(make_ctx('普通问题'))
    with mock.patch.object(handler, 'handle_stream', side_effect=RuntimeError('服务不可用')):
        with mock.patch('bridges.feishu.CardStream') as stream:
            current._run(handler, make_ctx('普通问题'))
            stream.assert_called_once_with('chat-test')
            stream.return_value.fail.assert_called_once_with('服务不可用')
            stream.return_value.finish.assert_not_called()
    current.shutdown()
