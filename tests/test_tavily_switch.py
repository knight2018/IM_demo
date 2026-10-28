"""会话搜索开关、老数据库迁移及禁用工具回归。"""

import sqlite3
from unittest.mock import Mock, patch

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk

from core.api_chat import ApiChat
from core.model_router import ModelRouter
from core.routing_store import ModelSettings, RoutingStore
from core.messages import MessageContext
from handlers.model_routing import ModelCommandHandler


@pytest.fixture
def router(tmp_path):
    api = Mock()
    api.search_tool = Mock()
    api.answer.return_value = '回答'
    return ModelRouter(RoutingStore(tmp_path/'routing.db', ModelSettings('api', 'api-model', 'codex-model')), api, Mock())


def test_persistent_isolated_switch_and_alias(router):
    assert '关闭' in router.command('a', '/tacily off')
    reopened = RoutingStore(router.store.path, router.store.defaults)
    assert reopened.get('a').tavily_enabled is False
    assert reopened.get('b').tavily_enabled is True
    assert '关闭' in router.command('a', '/tacily')
    assert '开启' in router.command('a', '/tavily on')
    assert router.store.get('a').tavily_enabled is True


@pytest.mark.parametrize('text', ['/tacily bad', '/tacily on extra'])
def test_invalid_switch_does_not_change_state(router, text):
    before = router.store.get('a')
    assert '用法' in router.command('a', text)
    assert router.store.get('a') == before


def test_missing_key_does_not_enable(router):
    router.command('a', '/tacily off')
    router.api.search_tool = None
    assert '未配置 TACILY_KEY' in router.command('a', '/tacily on')
    assert router.store.get('a').tavily_enabled is False


@pytest.mark.parametrize('stream', [False, True])
@pytest.mark.parametrize('mode', ['api', 'auto'])
def test_router_applies_switch_per_request(router, stream, mode):
    router.command('a', '/use_model '+mode)
    router.command('a', '/tacily off')
    router.answer('a', '你好', Mock() if stream else None)
    assert router.api.answer.call_args.kwargs['search_enabled'] is False
    router.answer('b', '你好')
    assert 'search_enabled' not in router.api.answer.call_args.kwargs


@pytest.mark.parametrize('stream', [False, True])
def test_disabled_search_never_binds_tools(stream):
    tool = Mock()
    client = ApiChat('https://example.invalid', 'key', search_tool=tool)
    model = Mock()
    model.invoke.return_value = AIMessage(content='回答')
    model.stream.return_value = iter([AIMessageChunk(content='回答', response_metadata={'finish_reason': 'stop'})])
    try:
        with patch.object(client, '_model', return_value=model):
            assert client.answer('model', '问题', [], Mock() if stream else None, search_enabled=False) == '回答'
        model.bind_tools.assert_not_called()
        tool.invoke.assert_not_called()
        assert client.search_tool is tool
    finally:
        client.http.close()


def test_migrate_old_settings_without_losing_data(tmp_path):
    path = tmp_path/'old.db'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE settings (key TEXT PRIMARY KEY, mode TEXT, api_model TEXT, codex_model TEXT)')
        db.execute('INSERT INTO settings VALUES (?,?,?,?)', (RoutingStore.key('a'), 'codex', 'old-api', 'old-codex'))
    store = RoutingStore(path, ModelSettings('api', 'new-api', 'new-codex'))
    assert store.get('a') == ModelSettings('codex', 'old-api', 'old-codex', True)
    store.append('a', '问题', '回答')
    store.save('a', ModelSettings('codex', 'old-api', 'old-codex', False))
    assert store.history('a') == [{'user': '问题', 'assistant': '回答'}]


@pytest.mark.parametrize('text,expected', [('/tacily', True), ('/tacily off', True), ('/tavily on', True), ('/tacilyXYZ', False)])
def test_command_exact_match(text, expected):
    assert ModelCommandHandler().match(MessageContext('m', 'c', 'p2p', 'u', text, True)) is expected
