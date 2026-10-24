"""通道与模型命令、难度路由、跨通道上下文及失败原子性。"""

from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from langchain_core.messages import AIMessage

from core.api_chat import ApiChat, Difficulty
from core.model_router import ModelRouter
from core.routing_store import ModelSettings, RoutingStore
from core.dispatcher import MessageContext
from handlers.model_routing import ModelCommandHandler


@pytest.fixture
def service(tmp_path: Path) -> ModelRouter:
    api = Mock(spec=ApiChat)
    api.models.return_value = ['gemini-3.8-flash', 'deepseek-flash']
    api.answer.return_value = 'API回答'
    codex = Mock()
    codex.available_models.return_value = ['gpt-6.1-sol', 'another-codex-model']
    codex.reply.return_value = 'Codex回答'
    return ModelRouter(RoutingStore(tmp_path/'routing.sqlite3',
        ModelSettings('codex', 'gemini-3.8-flash', 'gpt-6.1-sol')), api, codex)


@pytest.mark.parametrize('mode', ['api', 'codex', 'auto'])
def test_switch_mode_persistent_and_isolated(service: ModelRouter, mode: str) -> None:
    assert f'当前通道：{mode}' in service.command('a', '/use_model '+mode)
    reopened = RoutingStore(service.store.path, service.store.defaults)
    assert reopened.get('a').mode == mode
    assert reopened.get('b').mode == 'codex'


def test_invalid_commands_do_not_change_settings(service: ModelRouter) -> None:
    before = service.store.get('a')
    for command in ['/use_model invalid', '/use_model api extra', '/model api missing', '/model api x extra']:
        service.command('a', command)
        assert service.store.get('a') == before


def test_select_model_in_fixed_mode_and_auto(service: ModelRouter) -> None:
    service.command('a', '/model another-codex-model')
    service.answer('a', '你好')
    assert service.codex.reply.call_args.args[0]['model'] == 'another-codex-model'
    service.command('a', '/use_model auto')
    assert '需要指定' in service.command('a', '/model deepseek-flash')
    service.command('a', '/model api deepseek-flash')
    assert service.store.get('a').api_model == 'deepseek-flash'
    assert service.store.get('a').mode == 'auto'


def test_model_listing_and_exact_command_boundaries(service: ModelRouter) -> None:
    text = service.command('a', '/model list api deepseek')
    assert 'deepseek-flash' in text and 'gemini-3.8-flash' not in text
    handler = ModelCommandHandler()
    for command, expected in [('/model', True), (' /use_model auto ', True), ('/modelXYZ', False), ('/use_models', False), ('', False)]:
        ctx = MessageContext('id', 'chat', 'p2p', 'user', command, True)
        assert handler.match(ctx) is expected


def test_commands_registered_without_model_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    from core import config
    from handlers import build_registry
    monkeypatch.setattr(config, 'REPLY_BACKEND', 'codex')
    registry = build_registry()
    ctx = MessageContext('id', 'chat', 'p2p', 'user', '/use_model auto', True)
    with patch('handlers.model_routing.router') as factory:
        factory.return_value.command.return_value = '已切换'
        selected = registry.resolve(ctx)
        assert selected.name == 'model-commands' and selected.handle(ctx) == '已切换'
        factory.return_value.command.assert_called_once_with('chat:user', '/use_model auto')
        factory.return_value.answer.assert_not_called()
    ctx.text = '/help'
    assert '/use_model' in registry.resolve(ctx).handle(ctx)
    registry._executor.shutdown(wait=True)


@pytest.mark.parametrize('difficulty,backend', [('simple', 'api'), ('complex', 'codex')])
def test_auto_chooses_only_one_answer_backend(service: ModelRouter, difficulty: str, backend: str) -> None:
    service.command('a', '/use_model auto')
    service.api.classify.return_value = Difficulty(difficulty=difficulty, reason='测试判断')
    result = service.answer('a', '测试问题')
    assert f'auto → {backend}' in result
    assert service.api.answer.call_count == int(backend == 'api')
    assert service.codex.reply.call_count == int(backend == 'codex')
    service.api.classify.assert_called_once_with('gemini-3.8-flash', '测试问题', [])


def test_switch_keeps_shared_recent_context(service: ModelRouter) -> None:
    service.answer('a', '记住青竹42')
    service.command('a', '/use_model api')
    service.answer('a', '刚才说了什么')
    history = service.api.answer.call_args.args[2]
    assert history == [{'user': '记住青竹42', 'assistant': 'Codex回答'}]
    service.command('a', '/use_model codex')
    service.answer('a', '继续')
    assert '刚才说了什么' in service.codex.reply.call_args.args[0]['text']
    assert '[通道：' not in service.codex.reply.call_args.args[0]['text']
    assert service.store.history('b') == []


def test_auto_followup_gets_context(service: ModelRouter) -> None:
    service.answer('a', '设计复杂的多租户系统')
    service.command('a', '/use_model auto')
    service.api.classify.return_value = Difficulty(difficulty='complex', reason='继续讨论系统设计')
    service.answer('a', '继续详细讲')
    assert service.api.classify.call_args.args[2][0]['user'] == '设计复杂的多租户系统'


@pytest.mark.parametrize('stage', ['classification', 'api', 'codex', 'catalog'])
def test_failure_no_switch_or_partial_history(service: ModelRouter, stage: str) -> None:
    if stage == 'classification':
        service.command('a', '/use_model auto')
        service.api.classify.side_effect = RuntimeError('分类失败')
    elif stage == 'api':
        service.command('a', '/use_model api')
        service.api.answer.side_effect = RuntimeError('API失败')
    elif stage == 'codex':
        service.codex.reply.side_effect = RuntimeError('Codex失败')
    else:
        service.api.models.side_effect = RuntimeError('目录失败')
    settings = service.store.get('a')
    with pytest.raises(RuntimeError):
        if stage == 'catalog':
            service.command('a', '/model api deepseek-flash')
        else:
            service.answer('a', '问题')
    assert service.store.get('a') == settings and service.store.history('a') == []
    if stage in {'classification', 'catalog'}:
        service.api.answer.assert_not_called()
        service.codex.reply.assert_not_called()
    elif stage == 'api':
        service.codex.reply.assert_not_called()
    else:
        service.api.answer.assert_not_called()


def test_history_budget_keeps_latest_complete_turns(service: ModelRouter) -> None:
    for i in range(15):
        service.store.append('a', str(i), 'reply'+str(i))
    history = service.store.history('a')
    assert len(history) == 12 and history[0]['user'] == '3' and history[-1]['assistant'] == 'reply14'


@pytest.mark.parametrize('backend', ['api', 'codex'])
def test_router_stream_saves_only_complete_answer(service: ModelRouter, backend: str) -> None:
    service.command('a', '/use_model '+backend)
    snapshots: list[str] = []
    def generate(*args: object, **kwargs: object) -> str:
        callback = kwargs['on_update']
        callback('部分正文')
        assert service.store.history('a') == []
        return '完整正文'
    target = service.api.answer if backend == 'api' else service.codex.reply
    target.side_effect = generate
    reply = service.answer('a', '问题', snapshots.append)
    assert snapshots == ['部分正文']
    assert '[通道：' in reply
    assert service.store.history('a') == [{'user': '问题', 'assistant': '完整正文'}]


def test_api_uses_langchain_messages_and_explicit_model() -> None:
    client = ApiChat('https://example.invalid/v1', 'test-key')
    model = Mock()
    model.invoke.return_value = AIMessage(content='回答', response_metadata={'finish_reason': 'stop'})
    with patch.object(client, '_model', return_value=model) as factory:
        assert client.answer('selected-model', '继续', [{'user': '先前提问', 'assistant': '先前回答'}]) == '回答'
        factory.assert_called_once_with('selected-model')
        assert [m.content for m in model.invoke.call_args.args[0]][1:] == ['先前提问', '先前回答', '继续']
        model.invoke.return_value = AIMessage(content='截断', response_metadata={'finish_reason': 'length'})
        with pytest.raises(RuntimeError, match='完整回复'):
            client.answer('selected-model', '问题', [])
    client.http.close()


def test_invalid_classifier_output_fails_explicitly() -> None:
    from langchain_core.runnables import RunnableLambda
    client = ApiChat('https://example.invalid/v1', 'test-key')
    with patch.object(client, '_model', return_value=RunnableLambda(lambda messages: AIMessage(content='不是JSON'))):
        with pytest.raises(RuntimeError, match='难度判断失败'):
            client.classify('selected-model', '问题', [])
    client.http.close()
