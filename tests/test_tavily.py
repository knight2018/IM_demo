"""搜索协议、错误边界和 API 工具循环回归。"""

import json
from unittest.mock import Mock, patch

import httpx
import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage

from core.api_chat import ApiChat
from core.tavily import SearchError, create_search_tool


def test_search_request_and_sources():
    tool = create_search_tool('secret')
    with patch('core.tavily.httpx.Client') as factory:
        client = factory.return_value.__enter__.return_value
        client.post.return_value = httpx.Response(200, request=httpx.Request('POST', 'https://api.tavily.com/search'),
            json={'results': [{'title': '来源', 'url': 'https://example.com', 'content': '资料', 'raw_content': '不应返回'}]})
        result = json.loads(tool.invoke({'query': '搜索词'}))
        assert result['results'][0] == {'title': '来源', 'url': 'https://example.com', 'content': '资料'}
        kwargs = client.post.call_args.kwargs
        assert kwargs['headers']['Authorization'] == 'Bearer secret'
        assert kwargs['json']['max_results'] == 5
        assert kwargs['json']['include_raw_content'] is False


@pytest.mark.parametrize('status,payload', [(401, {}), (429, {}), (200, {'results': '错误'})])
def test_search_failure_is_redacted(status, payload):
    with patch('core.tavily.httpx.Client') as factory:
        factory.return_value.__enter__.return_value.post.return_value = httpx.Response(status,
            request=httpx.Request('POST', 'https://api.tavily.com/search'), json=payload)
        with pytest.raises(SearchError, match='Tavily 搜索失败') as error:
            create_search_tool('private-secret').invoke({'query': '测试'})
        assert 'private-secret' not in str(error.value)


def test_empty_results_are_not_fabricated():
    with patch('core.tavily.httpx.Client') as factory:
        factory.return_value.__enter__.return_value.post.return_value = httpx.Response(200,
            request=httpx.Request('POST', 'https://api.tavily.com/search'), json={'results': []})
        assert json.loads(create_search_tool('secret').invoke({'query': '测试'})) == {'results': []}


def setup_client():
    tool = Mock()
    tool.name = 'tavily_search'
    tool.invoke.return_value = '{"results": []}'
    client = ApiChat('https://example.invalid', 'key', search_tool=tool)
    model = Mock()
    client._model = Mock(return_value=model)
    return client, tool, model.bind_tools.return_value


def call_message():
    return AIMessage(content='', tool_calls=[{'name': 'tavily_search', 'args': {'query': '资料'}, 'id': 'c1'}],
                     response_metadata={'finish_reason': 'tool_calls'})


def test_tool_loop_keeps_context_and_result():
    client, tool, model = setup_client()
    model.invoke.side_effect = [call_message(), AIMessage(content='未找到资料')]
    try:
        assert client.answer('model', '查询', [{'user': '前文', 'assistant': '回答'}]) == '未找到资料'
        messages = model.invoke.call_args.args[0]
        assert messages[1].content == '前文'
        assert isinstance(messages[-1], ToolMessage)
        assert messages[-1].tool_call_id == 'c1'
        tool.invoke.assert_called_once_with({'query': '资料'})
    finally:
        client.http.close()


def test_smalltalk_does_not_search():
    client, tool, model = setup_client()
    model.invoke.return_value = AIMessage(content='你好')
    try:
        assert client.answer('model', '你好', []) == '你好'
        tool.invoke.assert_not_called()
    finally:
        client.http.close()


def test_stream_merges_tool_arguments_and_streams_answer():
    client, tool, model = setup_client()
    model.stream.side_effect = [iter([
        AIMessageChunk(content='', tool_call_chunks=[{'name': 'tavily_search', 'args': '{"query":', 'id': 'c1', 'index': 0}]),
        AIMessageChunk(content='', tool_call_chunks=[{'name': None, 'args': '"资料"}', 'id': None, 'index': 0}], response_metadata={'finish_reason': 'tool_calls'})]),
        iter([AIMessageChunk(content='找到'), AIMessageChunk(content='资料', response_metadata={'finish_reason': 'stop'})])]
    updates = []
    try:
        assert client.answer('model', '查询', [], updates.append) == '找到资料'
        assert updates == ['找到', '找到资料']
        tool.invoke.assert_called_once_with({'query': '资料'})
    finally:
        client.http.close()


@pytest.mark.parametrize('mode', ['limit', 'failure', 'truncated', 'unknown'])
def test_errors_do_not_generate_success(mode):
    client, tool, model = setup_client()
    model.invoke.return_value = call_message()
    if mode == 'failure':
        tool.invoke.side_effect = SearchError('Tavily 搜索失败')
    elif mode == 'truncated':
        model.invoke.return_value = AIMessage(content='不完整', response_metadata={'finish_reason': 'length'})
    elif mode == 'unknown':
        model.invoke.return_value = AIMessage(content='', tool_calls=[{'name': 'unknown', 'args': {}, 'id': 'c1'}])
    try:
        with pytest.raises(RuntimeError):
            client.answer('model', '查询', [])
        assert tool.invoke.call_count == {'limit': 4, 'failure': 1, 'truncated': 0, 'unknown': 0}[mode]
    finally:
        client.http.close()
