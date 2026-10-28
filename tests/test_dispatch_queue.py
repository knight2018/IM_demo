"""桥接会话队列的顺序、公平性、容量与关闭回归。"""

import threading
import uuid
from unittest.mock import Mock

import pytest

from core import config
from core.dispatcher import HandlerRegistry, MessageContext
from handlers.base import BaseHandler


class Handler(BaseHandler):
    name = 'queue-test'

    def __init__(self, callback):
        self.callback = callback

    def match(self, ctx):
        return True

    def handle(self, ctx):
        return self.callback(ctx)


def ctx(text, user='a', chat='chat', channel='feishu', account='default'):
    return MessageContext(str(uuid.uuid4()), chat, 'p2p', user, text, True,
                          channel=channel, account_id=account)


def registry(monkeypatch, callback, workers=1, session=5, total=100):
    monkeypatch.setattr(config, 'WORKER_THREADS', workers)
    monkeypatch.setattr(config, 'SESSION_QUEUE_LIMIT', session)
    monkeypatch.setattr(config, 'GLOBAL_QUEUE_LIMIT', total)
    result = HandlerRegistry(Mock())
    result.register(Handler(callback), default=True)
    return result


def test_fifo_and_fair_rotation(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    order = []
    def run(message):
        order.append(message.text)
        if message.text == 'a1':
            entered.set()
            assert release.wait(5)
        return message.text
    current = registry(monkeypatch, run)
    try:
        current.dispatch(ctx('a1'))
        assert entered.wait(3)
        current.dispatch(ctx('a2'))
        current.dispatch(ctx('a3'))
        current.dispatch(ctx('b1', 'b'))
        current.dispatch(ctx('b2', 'b'))
        release.set()
        current.shutdown()
        assert order == ['a1', 'b1', 'a2', 'b2', 'a3']
        assert current._waiting == 0 and not current._active and not current._queues
        notices = [call.args[1] for call in current.delivery.send_text.call_args_list if '已排队' in call.args[1]]
        assert len(notices) == 2
    finally:
        release.set()
        current.shutdown()


def test_waiting_same_user_does_not_occupy_other_worker(monkeypatch):
    entered, release, other = threading.Event(), threading.Event(), threading.Event()
    def run(message):
        if message.text == 'a1':
            entered.set()
            assert release.wait(5)
        if message.sender_id == 'b':
            other.set()
        return message.text
    current = registry(monkeypatch, run, workers=2)
    try:
        current.dispatch(ctx('a1'))
        assert entered.wait(3)
        for text in ['a2', 'a3', 'a4']:
            current.dispatch(ctx(text))
        current.dispatch(ctx('b1', 'b'))
        assert other.wait(2)
    finally:
        release.set()
        current.shutdown()


@pytest.mark.parametrize('scope', ['session', 'global'])
def test_capacity_rejects_without_model_execution(monkeypatch, scope):
    entered, release = threading.Event(), threading.Event()
    calls = []
    def run(message):
        calls.append(message.text)
        if message.text == 'running':
            entered.set()
            assert release.wait(5)
        return message.text
    current = registry(monkeypatch, run, session=1, total=1 if scope == 'global' else 100)
    try:
        current.dispatch(ctx('running'))
        assert entered.wait(3)
        current.dispatch(ctx('waiting'))
        rejected = ctx('rejected', 'b' if scope == 'global' else 'a')
        current.dispatch(rejected)
        current.dispatch(rejected)
        assert current._waiting == 1
    finally:
        release.set()
        current.shutdown()
    assert calls == ['running', 'waiting']
    notices = [call.args[1] for call in current.delivery.send_text.call_args_list if '未受理' in call.args[1]]
    assert len(notices) == 1


def test_shutdown_drains_all_accepted_messages_and_rejects_new(monkeypatch):
    entered, release, closing, closed = [threading.Event() for _ in range(4)]
    calls = []
    def run(message):
        calls.append(message.text)
        if message.text == 'first':
            entered.set()
            assert release.wait(5)
        return message.text
    current = registry(monkeypatch, run)
    current.dispatch(ctx('first'))
    assert entered.wait(3)
    current.dispatch(ctx('second'))
    current.dispatch(ctx('third'))
    def stop():
        closing.set()
        current.shutdown()
        closed.set()
    thread = threading.Thread(target=stop)
    thread.start()
    try:
        assert closing.wait(2)
        assert not closed.wait(.1)
        with pytest.raises(RuntimeError, match='平滑重启'):
            current.dispatch(ctx('new'))
    finally:
        release.set()
        thread.join(5)
    assert closed.is_set()
    assert calls == ['first', 'second', 'third']
    for reply in calls:
        assert any(call.args[1] == reply for call in current.delivery.send_text.call_args_list)


def test_send_failure_releases_queue(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    calls = []
    def run(message):
        calls.append(message.text)
        if message.text == 'first':
            entered.set()
            assert release.wait(5)
        return message.text
    current = registry(monkeypatch, run)
    def send(chat, text):
        if text == 'first':
            raise RuntimeError('模拟发送失败')
    current.delivery.send_text.side_effect = send
    try:
        current.dispatch(ctx('first'))
        assert entered.wait(3)
        current.dispatch(ctx('second'))
    finally:
        release.set()
        current.shutdown()
    assert calls == ['first', 'second']


@pytest.mark.parametrize('field,value', [('sender_id', 'b'), ('chat_id', 'other'),
                                         ('channel', 'other'), ('account_id', 'other')])
def test_isolation_key_uses_all_dimensions(monkeypatch, field, value):
    entered, release, other = threading.Event(), threading.Event(), threading.Event()
    def run(message):
        if message.text == 'first':
            entered.set()
            assert release.wait(5)
        else:
            other.set()
        return message.text
    current = registry(monkeypatch, run, workers=2)
    try:
        current.dispatch(ctx('first'))
        assert entered.wait(3)
        second = ctx('second')
        setattr(second, field, value)
        current.dispatch(second)
        assert other.wait(2)
    finally:
        release.set()
        current.shutdown()


def test_notice_failure_does_not_drop_accepted_work(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    calls = []
    def run(message):
        calls.append(message.text)
        if message.text == 'first':
            entered.set()
            assert release.wait(5)
        return message.text
    current = registry(monkeypatch, run)
    def send(chat, text):
        if '已排队' in text:
            raise RuntimeError('提示失败')
    current.delivery.send_text.side_effect = send
    try:
        message = ctx('first')
        current.dispatch(message)
        assert entered.wait(3)
        second = ctx('second')
        current.dispatch(second)
        current.dispatch(second)
    finally:
        release.set()
        current.shutdown()
    assert calls == ['first', 'second']
