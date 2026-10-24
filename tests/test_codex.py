"""Codex 接入测试：协议、固定模型、中等推理、会话隔离和完整回复。"""

import json
import time
from collections import deque
from pathlib import Path
from typing import Self
from unittest.mock import patch

import pytest

from core.codex import CodexBridge, CodexError
from core.dispatcher import MessageContext
from handlers.codex_chat import CodexChatHandler, prepare


class FakeSocket:
    def __init__(self, sent: list[dict[str, object]], mode: str = 'success') -> None:
        self.sent, self.mode = sent, mode
        self.queue: deque[dict[str, object]] = deque()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        pass

    def send(self, raw: str) -> None:
        m = json.loads(raw)
        self.sent.append(m)
        method, params = m.get('method'), m.get('params', {})
        result: dict[str, object] = {}
        if method in {'thread/start', 'thread/resume'}:
            self.thread_id = params.get('threadId', 'thread-'+str(len(self.sent)))
            result = {'thread': {'id': self.thread_id}}
        elif method == 'turn/start':
            result = {'turn': {'id': 'turn-1'}}
            # 通知可能先于请求响应到达，不能丢失或把进度当成最终答案。
            self.queue.append({'method': 'item/completed', 'params': {'threadId': self.thread_id,
                'turnId': 'turn-1', 'item': {'type': 'agentMessage', 'id': 'note',
                                          'phase': 'commentary', 'text': '不应发送的进度'}}})
            if self.mode not in {'timeout', 'empty'}:
                self.queue.append({'method': 'item/completed', 'params': {'threadId': self.thread_id,
                    'turnId': 'turn-1', 'item': {'type': 'agentMessage', 'id': 'answer',
                                              'phase': 'final_answer', 'text': '完整回复'}}})
            if self.mode == 'approval':
                self.queue.append({'id': 999, 'method': 'item/commandExecution/requestApproval', 'params': {}})
            if self.mode != 'timeout':
                self.queue.append({'method': 'turn/completed', 'params': {'threadId': self.thread_id,
                    'turn': {'id': 'turn-1', 'status': 'failed' if self.mode == 'failed' else 'completed'}}})
        if 'id' in m and method:
            if self.mode == 'rpc_error' and method == 'thread/start':
                self.queue.append({'id': m['id'], 'error': {'message': 'private diagnostic'}})
            else:
                self.queue.append({'id': m['id'], 'result': result})

    def recv(self, timeout: float) -> str:
        if not self.queue:
            raise TimeoutError
        return json.dumps(self.queue.popleft())


def client(tmp_path: Path) -> CodexBridge:
    return CodexBridge('ws://127.0.0.1:4500', tmp_path/'state.sqlite3', tmp_path/'workspace')


def test_reply_resume_model_and_effort(tmp_path: Path) -> None:
    sent: list[dict[str, object]] = []
    with patch('core.codex.connect', side_effect=lambda *a, **k: FakeSocket(sent)):
        assert client(tmp_path).reply({'conversation_key': 'chat:user', 'text': '你好'}) == '完整回复'
        assert client(tmp_path).reply({'conversation_key': 'chat:user', 'text': '继续'}) == '完整回复'
    methods = [m.get('method') for m in sent]
    assert methods.count('thread/start') == 1 and methods.count('thread/resume') == 1
    for m in sent:
        if m.get('method') == 'turn/start':
            assert m['params']['model'] == 'gpt-6.1-sol'
            assert m['params']['effort'] == 'medium'
            assert m['params']['sandboxPolicy'] == {'type': 'readOnly', 'networkAccess': False}


def test_users_have_independent_threads(tmp_path: Path) -> None:
    sent: list[dict[str, object]] = []
    with patch('core.codex.connect', side_effect=lambda *a, **k: FakeSocket(sent)):
        bridge = client(tmp_path)
        bridge.reply({'conversation_key': 'group:user-a', 'text': '第一人'})
        bridge.reply({'conversation_key': 'group:user-b', 'text': '第二人'})
    assert sum(m.get('method') == 'thread/start' for m in sent) == 2
    assert b'group:user-a' not in (tmp_path/'state.sqlite3').read_bytes()


@pytest.mark.parametrize('mode', ['timeout', 'empty', 'failed', 'rpc_error', 'approval'])
def test_failures_no_partial_or_switch(tmp_path: Path, mode: str) -> None:
    sent: list[dict[str, object]] = []
    with patch('core.codex.connect', side_effect=lambda *a, **k: FakeSocket(sent, mode)) as connect:
        with pytest.raises(CodexError) as error:
            client(tmp_path).reply({'conversation_key': 'test', 'text': '你好'})
        assert 'private diagnostic' not in str(error.value)
        assert connect.call_count == 1
    if mode == 'timeout':
        assert any(m.get('method') == 'turn/interrupt' for m in sent)


def test_connection_error_explicit(tmp_path: Path) -> None:
    with patch('core.codex.connect', side_effect=OSError('private transport error')):
        with pytest.raises(CodexError, match='连接或响应失败'):
            client(tmp_path).reply({'conversation_key': 'test', 'text': '你好'})


@pytest.mark.parametrize('model,effort', [('', 'medium'), ('gpt-6.1-sol', 'low')])
def test_reject_empty_model_or_wrong_effort(tmp_path: Path, model: str, effort: str) -> None:
    with pytest.raises(ValueError, match='medium'):
        CodexBridge('ws://127.0.0.1:4500', tmp_path/'state', tmp_path/'workspace', model, effort)


def test_explicit_model_switch_changes_rpc_without_changing_default(tmp_path: Path) -> None:
    sent: list[dict[str, object]] = []
    bridge = client(tmp_path)
    with patch('core.codex.connect', side_effect=lambda *a, **k: FakeSocket(sent)):
        bridge.reply({'conversation_key': 'switch', 'text': '你好', 'model': 'chosen-codex-model'})
    for message in sent:
        if message.get('method') in {'thread/start', 'turn/start'}:
            assert message['params']['model'] == 'chosen-codex-model'
    assert bridge.model == 'gpt-6.1-sol'


def test_langchain_pipeline() -> None:
    ctx = MessageContext('message', 'chat', 'group', 'user', '你好', True)
    with patch('handlers.codex_chat.bridge') as factory:
        factory.return_value.reply.return_value = '来自Codex'
        assert CodexChatHandler().handle(ctx) == '来自Codex'
        factory.return_value.reply.assert_called_once_with({'conversation_key': 'chat:user', 'text': '你好'})
    ctx.sender_id = 'unknown'
    with pytest.raises(ValueError):
        prepare(ctx)


def test_same_conversation_serialized(tmp_path: Path) -> None:
    from concurrent.futures import ThreadPoolExecutor
    import threading
    bridge = client(tmp_path)
    active = 0
    maximum = 0
    guard = threading.Lock()
    def generate(key: str, text: str) -> str:
        nonlocal active, maximum
        with guard:
            active += 1
            maximum = max(maximum, active)
        time.sleep(0.03)
        with guard:
            active -= 1
        return text
    with patch.object(bridge, '_reply', side_effect=generate), ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(bridge.reply, [{'conversation_key': 'same', 'text': str(i)} for i in range(2)]))
    assert maximum == 1 and sorted(results) == ['0', '1']
