"""重排接口校验、全候选映射及失败不降级。"""

from pathlib import Path
from unittest.mock import Mock, patch

import httpx
import numpy as np
import pytest

from core.knowledge import HybridKnowledge, build_index
from core.rerank import Reranker, RerankError


def response(results, status=200):
    return httpx.Response(status, request=httpx.Request('POST', 'https://example.invalid'), json={'results': results})


def test_rerank_protocol_and_stable_score_sort():
    with patch('core.rerank.httpx.Client') as factory:
        client = factory.return_value.__enter__.return_value
        client.post.return_value = response([{'index': 1, 'relevance_score': .8}, {'index': 0, 'relevance_score': .8}])
        result = Reranker('https://example.invalid/v1/', 'secret', 'rerank-model').rank('问题', ['甲', '乙'])
        assert result == [{'index': 0, 'score': .8}, {'index': 1, 'score': .8}]
        assert client.post.call_args.args[0] == 'https://example.invalid/v1/rerank'
        assert client.post.call_args.kwargs['json'] == {'model': 'rerank-model', 'query': '问题', 'documents': ['甲', '乙'], 'top_n': 2}


@pytest.mark.parametrize('results', [[],
    [{'index': 0, 'relevance_score': .5}, {'index': 0, 'relevance_score': .3}],
    [{'index': 2, 'relevance_score': .5}, {'index': 0, 'relevance_score': .3}],
    [{'index': False, 'relevance_score': .5}, {'index': 1, 'relevance_score': .3}],
    [{'index': 0, 'relevance_score': 1.1}, {'index': 1, 'relevance_score': .3}],
    [{'index': 0, 'relevance_score': -.1}, {'index': 1, 'relevance_score': .3}],
    [{'index': 0, 'relevance_score': '0.5'}, {'index': 1, 'relevance_score': .3}],
    [{'index': 0}, {'index': 1, 'relevance_score': .3}]])
def test_invalid_results_do_not_degrade(results):
    with patch('core.rerank.httpx.Client') as factory:
        factory.return_value.__enter__.return_value.post.return_value = response(results)
        with pytest.raises(RerankError, match='未退回'):
            Reranker('https://example.invalid', 'secret', 'model').rank('问题', ['甲', '乙'])


@pytest.mark.parametrize('status', [401, 429, 500])
def test_http_error_does_not_expose_key(status):
    with patch('core.rerank.httpx.Client') as factory:
        factory.return_value.__enter__.return_value.post.return_value = response([], status)
        with pytest.raises(RerankError) as error:
            Reranker('https://example.invalid', 'private-secret', 'model').rank('问题', ['甲'])
        assert 'private-secret' not in str(error.value)


def test_missing_key_fails():
    with pytest.raises(RerankError, match='密钥未配置'):
        Reranker('https://example.invalid', None, 'model').rank('问题', ['甲'])


def test_rerank_all_candidates_before_top_k_and_preserve_source(tmp_path):
    embeddings = Mock()
    embeddings.model = 'test'
    embeddings.embed.return_value = np.eye(6, dtype='float32')
    build_index(Path(__file__).resolve().parents[1]/'knowledge/h5-onboarding.md', tmp_path, embeddings)
    embeddings.embed.return_value = np.array([[1, 0, 0, 0, 0, 0]], dtype='float32')
    reranker = Mock()
    reranker.model = 'rerank-model'
    def rank(query, docs):
        assert len(docs) > 3
        return [{'index': i, 'score': (i+1)/(len(docs)+1)} for i in reversed(range(len(docs)))]
    reranker.rank.side_effect = rank
    kb = HybridKnowledge(tmp_path, embeddings, reranker)
    result = kb.search('zzzzunknown', 1)
    top = result['results'][0]
    assert top['hybrid_rank'] == result['candidate_count']
    assert top['rerank_rank'] == 1
    assert top['match_score'] == top['rerank_score'] < 1
    assert top['hybrid_score'] != top['match_score']
    assert top['source']['section'].startswith(str(top['section']))
    assert 'rrf_score' in top and 'cosine_similarity' in top
    reranker.rank.side_effect = RerankError('服务失败')
    with pytest.raises(RerankError):
        kb.search('问题')
