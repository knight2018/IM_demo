"""网关、桥接与 LangChain 迁移回归，不发送真实 IM 消息。"""

import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock, patch

import httpx
import pytest
from langchain_core.messages import AIMessage, ToolMessage

from bridges.feishu import attach_gateway
from core import llm
from core.dispatcher import HandlerRegistry, MessageContext
from gateway.client import GatewayClient
from gateway.protocol import IncomingMessage, ReplyEvent
from gateway.server import make_server
from gateway.service import Gateway, RequestConflict
from handlers.base import BaseHandler
from handlers.codex_chat import prepare
from handlers.tools_agent import ToolAgentHandler


class FakeHandler(BaseHandler):
    streaming = True

    def __init__(self) -> None:
        self.calls = 0
        self.fail = False

    def match(self, ctx: MessageContext) -> bool:
        return True

    def handle(self, ctx: MessageContext) -> str:
        raise AssertionError('必须使用流式入口')

    def handle_stream(self, ctx: MessageContext, on_update) -> str:
        self.calls += 1
        on_update('部分回答')
        if self.fail:
            raise RuntimeError('内部敏感诊断')
        return '完整回答'


def message(**changes: object) -> IncomingMessage:
    return IncomingMessage.model_validate(dict(channel='feishu', message_id='id',
        chat_id='chat', sender_id='user', text='问题') | changes)


@pytest.fixture
def service(tmp_path: Path):
    registry = HandlerRegistry()
    handler = FakeHandler()
    registry.register(handler, default=True)
    gateway = Gateway(registry, tmp_path/'requests.sqlite3')
    yield gateway, handler
    registry.shutdown()


@pytest.fixture
def endpoint(service):
    gateway, handler = service
    token = 'test-token-'+'x'*32
    server = make_server(('127.0.0.1', 0), gateway, token)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    url = f'http://127.0.0.1:{server.server_port}'
    yield GatewayClient(url, token), url, handler
    server.shutdown()
    server.server_close()
    thread.join()


def test_http_streaming_and_duplicate_replay(endpoint) -> None:
    client, url, handler = endpoint
    seen: list[str] = []
    assert client.reply(message(), seen.append) == '完整回答'
    assert seen == ['部分回答']
    assert client.reply(message()) == '完整回答'
    assert handler.calls == 1
    with httpx.Client(trust_env=False) as http:
        assert http.get(url+'/healthz').json()['status'] == 'ok'
        assert http.post(url+'/v1/messages', json=message().model_dump()).status_code == 401
        assert http.post(url+'/v1/messages', headers={'Authorization': 'Bearer '+client.token},
                         json={'channel': 'x'}).status_code == 400


def test_concurrent_duplicates_run_once(service) -> None:
    gateway, handler = service
    def request(_: int) -> list[ReplyEvent]:
        events: list[ReplyEvent] = []
        gateway.reply(message(), events.append)
        return events
    with ThreadPoolExecutor(4) as pool:
        replies = list(pool.map(request, range(4)))
    assert handler.calls == 1
    assert all(events[-1].type == 'completed' for events in replies)


def test_replay_after_restart_and_conflict(service) -> None:
    gateway, handler = service
    gateway.reply(message(), lambda event: None)
    reopened = Gateway(gateway.registry, gateway.path)
    events: list[ReplyEvent] = []
    reopened.reply(message(), events.append)
    assert events[0].type == 'completed'
    assert handler.calls == 1
    with pytest.raises(RequestConflict):
        reopened.reply(message(text='不同内容'), events.append)


def test_error_is_terminal_without_fallback_or_secret(service) -> None:
    gateway, handler = service
    handler.fail = True
    for _ in range(2):
        events: list[ReplyEvent] = []
        gateway.reply(message(), events.append)
        assert events[-1].type == 'error'
        assert '敏感诊断' not in events[-1].text
    assert handler.calls == 1


def test_crashed_request_not_reported_complete_or_rerun(service) -> None:
    gateway, handler = service
    incoming = message()
    with sqlite3.connect(gateway.path) as db:
        db.execute('INSERT INTO requests VALUES (?,?,NULL)',
                   (gateway.request_id(incoming), incoming.model_dump_json()))
    events: list[ReplyEvent] = []
    gateway.reply(incoming, events.append)
    assert events[-1].type == 'error'
    assert handler.calls == 0


def test_platform_account_isolation_and_old_session_compatibility(service) -> None:
    gateway, handler = service
    messages = [message(), message(channel='wecom'), message(account_id='other')]
    keys = [prepare(item.context())['conversation_key'] for item in messages]
    assert keys[0] == 'chat:user'
    assert len(set(keys)) == 3
    for incoming in messages:
        gateway.reply(incoming, lambda event: None)
    assert handler.calls == 3


def test_feishu_proxy_preserves_command_and_stream_behaviour(endpoint) -> None:
    from handlers import build_registry
    client, _, _ = endpoint
    registry = attach_gateway(build_registry(), client)
    ctx = message().context()
    with patch.object(client, 'reply', return_value='完成') as call:
        handler = registry.resolve(ctx)
        assert handler.streaming
        updates = Mock()
        assert handler.handle_stream(ctx, updates) == '完成'
        assert call.call_args.args[1] is updates
        ctx.text = '/ping'
        handler = registry.resolve(ctx)
        assert not handler.streaming
        call.reset_mock()
        assert handler.handle(ctx) == 'pong，机器人在线'
        call.assert_not_called()
    registry.shutdown()


def test_client_rejects_truncated_stream() -> None:
    with patch('gateway.client.httpx.Client') as http:
        response = http.return_value.__enter__.return_value.stream.return_value.__enter__.return_value
        response.status_code = 200
        response.iter_lines.return_value = [ReplyEvent(type='snapshot', text='部分', request_id='id').model_dump_json()]
        with pytest.raises(RuntimeError, match='未收到完成事件'):
            GatewayClient('http://localhost', 'token').reply(message())


def test_langchain_tool_round_trip() -> None:
    tools = ToolAgentHandler().tools()
    model = Mock()
    model.bind_tools.return_value.invoke.side_effect = [
        AIMessage(content='', tool_calls=[{'name': 'calculate', 'args': {'expression': '12*35'}, 'id': 'call-1'}]),
        AIMessage(content='420')]
    with patch('core.llm.create_request', return_value=model):
        assert llm.run_agent('计算助手', '12*35', tools) == '420'
    messages = model.bind_tools.return_value.invoke.call_args.args[0]
    assert any(isinstance(item, ToolMessage) and item.content == '420' for item in messages)
    assert len(tools[0].invoke({})) == 19
    with pytest.raises(ValueError):
        tools[1].invoke({'expression': '__import__("os")'})


def test_langchain_unknown_tool_and_round_limit() -> None:
    model = Mock()
    model.bind_tools.return_value.invoke.return_value = AIMessage(content='',
        tool_calls=[{'name': 'missing', 'args': {}, 'id': 'c'}])
    with patch('core.llm.create_request', return_value=model), pytest.raises(RuntimeError, match='未注册'):
        llm.run_agent('工具', '问题', ToolAgentHandler().tools())
    model.bind_tools.return_value.invoke.return_value = AIMessage(content='',
        tool_calls=[{'name': 'calculate', 'args': {'expression': '1+1'}, 'id': 'c'}])
    with patch('core.llm.create_request', return_value=model), pytest.raises(RuntimeError, match='轮数上限'):
        llm.run_agent('工具', '问题', ToolAgentHandler().tools())


def test_http_snapshot_arrives_before_generation_finishes(endpoint) -> None:
    client, _, handler = endpoint
    release, received = threading.Event(), threading.Event()
    def generate(ctx, update):
        update('尚未完成')
        assert release.wait(5)
        return '完成'
    with patch.object(handler, 'handle_stream', side_effect=generate):
        with ThreadPoolExecutor(1) as pool:
            future = pool.submit(client.reply, message(), lambda text: received.set())
            try:
                assert received.wait(3)
                assert not future.done()
            finally:
                release.set()
            assert future.result(3) == '完成'


def test_http_failure_and_conflict_are_explicit(endpoint) -> None:
    client, _, handler = endpoint
    handler.fail = True
    with pytest.raises(RuntimeError, match='处理失败'):
        client.reply(message())
    with pytest.raises(RuntimeError, match='编号冲突'):
        client.reply(message(text='变更'))
    assert handler.calls == 1


def test_gateway_refuses_public_bind_and_unknown_sender(service) -> None:
    gateway, _ = service
    with pytest.raises(ValueError, match='本机'):
        make_server(('0.0.0.0', 8787), gateway, 'x'*32)
    with pytest.raises(ValueError, match='用户标识'):
        gateway.reply(message(sender_id='unknown'), lambda event: None)


def test_gateway_shutdown_waits_for_http_generation(service) -> None:
    gateway, handler = service
    server = make_server(('127.0.0.1', 0), gateway, 'x'*32)
    serving = threading.Thread(target=server.serve_forever)
    serving.start()
    client = GatewayClient(f'http://127.0.0.1:{server.server_port}', 'x'*32)
    release, started, stopped = threading.Event(), threading.Event(), threading.Event()
    def generate(ctx, update):
        started.set()
        assert release.wait(5)
        return '已结束'
    def stop():
        server.shutdown()
        server.server_close()
        stopped.set()
    with patch.object(handler, 'handle_stream', side_effect=generate):
        with ThreadPoolExecutor(1) as pool:
            future = pool.submit(client.reply, message())
            try:
                assert started.wait(3)
                stopping = threading.Thread(target=stop)
                stopping.start()
                assert not stopped.wait(0.7)
            finally:
                release.set()
            assert future.result(3) == '已结束'
            stopping.join(3)
            assert stopped.is_set()
    serving.join(3)


def test_gateway_runs_without_feishu_credentials() -> None:
    import os
    import subprocess
    import sys
    env = dict(os.environ, FEISHU_APP_ID='', FEISHU_APP_SECRET='', REPLY_BACKEND='codex')
    result = subprocess.run([sys.executable, '-c',
        'import gateway_server; from handlers import build_registry; r=build_registry(); r.shutdown()'],
        env=env, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr.decode(errors='replace')


def test_disconnect_keeps_result_available_for_replay(endpoint) -> None:
    client, url, handler = endpoint
    release = threading.Event()
    def generate(ctx, update):
        handler.calls += 1
        update('生成中')
        assert release.wait(5)
        return '断线后保存的结果'
    with patch.object(handler, 'handle_stream', side_effect=generate):
        try:
            with httpx.Client(trust_env=False, timeout=3) as http:
                with http.stream('POST', url+'/v1/messages', json=message().model_dump(),
                                 headers={'Authorization': 'Bearer '+client.token}) as response:
                    for line in response.iter_lines():
                        if json.loads(line)['type'] == 'snapshot':
                            break
        finally:
            release.set()
        assert client.reply(message()) == '断线后保存的结果'
        assert handler.calls == 1


def test_capacity_rejects_extra_generation(service) -> None:
    gateway, handler = service
    server = make_server(('127.0.0.1', 0), gateway, 'x'*32, workers=1)
    serving = threading.Thread(target=server.serve_forever)
    serving.start()
    client = GatewayClient(f'http://127.0.0.1:{server.server_port}', 'x'*32)
    started, release = threading.Event(), threading.Event()
    def generate(ctx, update):
        started.set()
        assert release.wait(5)
        return '完成'
    try:
        with patch.object(handler, 'handle_stream', side_effect=generate):
            with ThreadPoolExecutor(1) as pool:
                pending = pool.submit(client.reply, message())
                try:
                    assert started.wait(3)
                    with pytest.raises(RuntimeError, match='429'):
                        client.reply(message(message_id='another'))
                finally:
                    release.set()
                assert pending.result(3) == '完成'
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        serving.join(3)
