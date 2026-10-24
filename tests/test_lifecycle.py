"""重启期间必须等待实际发送完成，不能仅等待模型生成完成。"""

import threading
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from bridges.feishu import FeishuDelivery
from core.dispatcher import HandlerRegistry, MessageContext, _seen_message_ids
from core.lifecycle import watch_stop
from handlers.base import BaseHandler


class BlockingHandler(BaseHandler):
    name = 'restart-test'

    def match(self, ctx: MessageContext) -> bool:
        return True

    def handle(self, ctx: MessageContext) -> str:
        return '已生成回复'


def test_shutdown_waits_until_reply_sent() -> None:
    registry = HandlerRegistry(FeishuDelivery())
    registry.register(BlockingHandler(), default=True)
    sending = threading.Event()
    release = threading.Event()
    closed = threading.Event()
    def send(chat: str, text: str) -> None:
        sending.set()
        assert release.wait(3)
    ctx = MessageContext('shutdown-original', 'chat', 'p2p', 'user', '问题', True)
    with patch('core.feishu.send_text', side_effect=send) as sender:
        registry.dispatch(ctx)
        assert sending.wait(3)
        def stop() -> None:
            registry.shutdown()
            closed.set()
        task = threading.Thread(target=stop)
        task.start()
        assert not closed.wait(0.1)
        rejected = MessageContext('shutdown-rejected', 'chat', 'p2p', 'user', '新问题', True)
        with pytest.raises(RuntimeError, match='平滑重启'):
            registry.dispatch(rejected)
        assert 'shutdown-rejected' not in _seen_message_ids
        release.set()
        task.join(3)
        assert closed.is_set()
        sender.assert_called_once_with('chat', '已生成回复')


def test_stop_signal_drains_before_exit(tmp_path: Path) -> None:
    request = tmp_path/'stop.request'
    request.write_text('stop')
    events: list[str] = []
    registry = Mock()
    registry.shutdown.side_effect = lambda: events.append('drained')
    watch_stop(request, registry, exit_process=lambda code: events.append('exit'))
    assert events == ['drained', 'exit'] and not request.exists()
