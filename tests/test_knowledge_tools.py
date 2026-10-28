"""知识工具接入已有 API 工具循环，不改变 Tavily 开关语义。"""

import json
from unittest.mock import Mock, patch

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage

from core.api_chat import ApiChat
from core.knowledge import KnowledgeError, create_knowledge_tool


def test_tavily_off_preserves_knowledge_tool():
    knowledge, tavily, model = Mock(), Mock(), Mock()
    knowledge.name, tavily.name = 'search_knowledge', 'tavily_search'
    knowledge.invoke.return_value = json.dumps({'results': [{'source': {'document': '培训文档', 'section': '5'}, 'match_score': .98}]})
    runnable = model.bind_tools.return_value
    runnable.invoke.side_effect = [AIMessage(content='', tool_calls=[
        {'name': 'search_knowledge', 'args': {'query': '代码仓库'}, 'id': 'k1'}]),
        AIMessage(content='答案。来源：培训文档第5节，检索匹配度0.98。')]
    client = ApiChat('https://example.invalid', 'key', search_tool=tavily, knowledge_tool=knowledge)
    try:
        with patch.object(client, '_model', return_value=model):
            reply = client.answer('model', '仓库权限', [], search_enabled=False)
        assert '0.98' in reply
        model.bind_tools.assert_called_once_with([knowledge])
        tavily.invoke.assert_not_called()
        messages = runnable.invoke.call_args.args[0]
        assert isinstance(messages[-1], ToolMessage)
        assert json.loads(messages[-1].content)['results'][0]['match_score'] == .98
    finally:
        client.http.close()


def test_streaming_knowledge_call_and_both_tools_available():
    knowledge, tavily, model = Mock(), Mock(), Mock()
    knowledge.name, tavily.name = 'search_knowledge', 'tavily_search'
    knowledge.invoke.return_value = '{"results": []}'
    runnable = model.bind_tools.return_value
    runnable.stream.side_effect = [iter([AIMessageChunk(content='', tool_call_chunks=[
        {'name': 'search_knowledge', 'args': '{"query":"绩效"}', 'id': 'k1', 'index': 0}],
        response_metadata={'finish_reason': 'tool_calls'})]),
        iter([AIMessageChunk(content='未找到', response_metadata={'finish_reason': 'stop'})])]
    client = ApiChat('https://example.invalid', 'key', search_tool=tavily, knowledge_tool=knowledge)
    try:
        with patch.object(client, '_model', return_value=model):
            assert client.answer('model', '绩效', [], lambda text: None) == '未找到'
        model.bind_tools.assert_called_once_with([tavily, knowledge])
        knowledge.invoke.assert_called_once_with({'query': '绩效'})
    finally:
        client.http.close()


def test_missing_knowledge_index_is_explicit(tmp_path):
    tool = create_knowledge_tool(tmp_path, Mock())
    with pytest.raises(KnowledgeError, match='索引缺失'):
        tool.invoke({'query': '公司前端框架'})
