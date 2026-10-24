"""流式回归：模型增量、同一张卡片、频率控制、失败标记和原命令兼容。"""

import json
from collections.abc import Callable
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from langchain_core.messages import AIMessageChunk

from core.api_chat import ApiChat
from core.codex import CodexBridge, CodexError
from core.dispatcher import HandlerRegistry, MessageContext
from core import feishu
from core.streaming import CardStream
from handlers.base import BaseHandler


def test_api_stream_emits_cumulative_snapshots() -> None:
    api = ApiChat('https://example.invalid/v1', 'fake-key')
    model = Mock()
    model.stream.return_value = iter([AIMessageChunk(content='你'), AIMessageChunk(content='好'),
        AIMessageChunk(content='', response_metadata={'finish_reason': 'stop'})])
    seen: list[str] = []
    with patch.object(api, '_model', return_value=model):
        assert api.answer('test', '问题', [], seen.append) == '你好'
    assert seen == ['你', '你好']
    model.invoke.assert_not_called()
    api.http.close()


@pytest.mark.parametrize('finish', [None, 'length'])
def test_api_incomplete_stream_fails(finish: str | None) -> None:
    api = ApiChat('https://example.invalid/v1', 'fake-key')
    model = Mock()
    model.stream.return_value = iter([AIMessageChunk(content='半句话', response_metadata={'finish_reason': finish})])
    with patch.object(api, '_model', return_value=model):
        with pytest.raises(RuntimeError, match='完整回复'):
            api.answer('test', '问题', [], lambda snapshot: None)
    api.http.close()


def test_codex_stream_filters_commentary_and_other_turns(tmp_path: Path) -> None:
    bridge = CodexBridge('ws://127.0.0.1:4500', tmp_path/'db', tmp_path/'workspace')
    def event(method: str, **payload: object) -> dict[str, object]:
        return {'method': method, 'params': {'threadId': 'thread', 'turnId': 'turn', **payload}}
    rpc = Mock()
    rpc.next_event.side_effect = [
        event('item/started', item={'id': 'progress', 'type': 'agentMessage', 'phase': 'commentary'}),
        event('item/agentMessage/delta', itemId='progress', delta='内部进度'),
        event('item/started', item={'id': 'answer', 'type': 'agentMessage', 'phase': 'final_answer'}),
        event('item/agentMessage/delta', itemId='answer', delta='其他轮内容', turnId='other'),
        event('item/agentMessage/delta', itemId='answer', delta='你'),
        event('item/agentMessage/delta', itemId='answer', delta='好'),
        event('item/completed', item={'id': 'answer', 'type': 'agentMessage', 'phase': 'final_answer', 'text': '你好。'}),
        event('turn/completed', turn={'id': 'turn', 'status': 'completed'}),
    ]
    seen: list[str] = []
    assert bridge._answer(rpc, 'thread', 'turn', seen.append) == '你好。'
    assert seen == ['你', '你好', '你好。']


def test_codex_failure_does_not_return_partial(tmp_path: Path) -> None:
    bridge = CodexBridge('ws://127.0.0.1:4500', tmp_path/'db', tmp_path/'workspace')
    rpc = Mock()
    rpc.next_event.return_value = {'method': 'turn/completed', 'params': {'threadId': 'thread',
        'turn': {'id': 'turn', 'status': 'failed'}}}
    with pytest.raises(CodexError, match='未完成'):
        bridge._answer(rpc, 'thread', 'turn', lambda snapshot: None)


def test_one_card_throttles_and_flushes_final() -> None:
    now = [0.0]
    with patch('core.feishu.create_stream_card', return_value='message-id') as create, patch('core.feishu.update_stream_card') as update:
        stream = CardStream('chat', clock=lambda: now[0])
        stream.update('你')
        now[0] = 1.1
        stream.update('你好')
        stream.update('你好世界')
        stream.finish('你好世界\n[模型：test]')
        create.assert_called_once_with('chat')
        assert update.call_count == 2
        assert all(call.args[0] == 'message-id' for call in update.call_args_list)
        assert update.call_args_list[-1].args[2] == '回复完成'
        assert '[模型：test]' in update.call_args_list[-1].args[1]


def test_failed_generation_marks_same_card_incomplete() -> None:
    with patch('core.feishu.create_stream_card', return_value='message-id'), patch('core.feishu.update_stream_card') as update:
        stream = CardStream('chat')
        stream.update('部分内容')
        stream.fail('模型超时')
        assert update.call_args.args[0] == 'message-id'
        assert '部分内容' in update.call_args.args[1] and '可能不完整' in update.call_args.args[1]
        assert update.call_args.args[2] == '生成失败'


class StreamingHandler(BaseHandler):
    name = 'stream-test'
    streaming = True

    def match(self, ctx: MessageContext) -> bool:
        return True

    def handle(self, ctx: MessageContext) -> str:
        raise AssertionError('不应调用一次性回复入口')

    def handle_stream(self, ctx: MessageContext, on_update: Callable[[str], None]) -> str:
        on_update('部分')
        return '完整回复'


def test_dispatcher_streams_without_duplicate_text_message() -> None:
    registry = HandlerRegistry()
    ctx = MessageContext('id', 'chat', 'p2p', 'user', '问题', True)
    with patch('core.dispatcher.CardStream') as stream, patch('core.feishu.send_text') as send:
        registry._run(StreamingHandler(), ctx)
        stream.return_value.update.assert_called_once_with('部分')
        stream.return_value.finish.assert_called_once_with('完整回复')
        send.assert_not_called()
    registry._executor.shutdown(wait=True)


def test_card_api_requests_and_errors() -> None:
    response = Mock()
    response.success.return_value = True
    response.data.message_id = 'message-id'
    with patch.object(feishu.api_client.im.v1.message, 'create', return_value=response) as create:
        assert feishu.create_stream_card('chat') == 'message-id'
        body = create.call_args.args[0].request_body
        assert body.msg_type == 'interactive'
        assert json.loads(body.content)['config']['update_multi'] is True
    with patch.object(feishu.api_client.im.v1.message, 'patch', return_value=response) as update:
        feishu.update_stream_card('message-id', '实时正文', '正在生成')
        request = update.call_args.args[0]
        assert request.message_id == 'message-id'
        assert json.loads(request.request_body.content)['body']['elements'][0]['content'] == '实时正文'
        response.success.return_value = False
        response.code = 99991672
        with pytest.raises(RuntimeError, match='99991672'):
            feishu.update_stream_card('message-id', '正文', '完成')
