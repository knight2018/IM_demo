"""知识切片、真实 FAISS 运算、排名融合和接口边界；不请求外部模型。"""

import json
from pathlib import Path
from unittest.mock import Mock, patch

import httpx
import numpy as np
import pytest

from core.knowledge import (Embeddings, HybridKnowledge, KnowledgeError, build_index,
                            fuse_ranks, normalized_vectors, split_source, tokenize)

SOURCE = Path(__file__).resolve().parents[1]/'knowledge/h5-onboarding.md'


def test_exact_six_sections_and_source():
    chunks = split_source(SOURCE)
    assert [item['section'] for item in chunks] == [1, 2, 3, 4, 5, 6]
    assert len({item['id'] for item in chunks}) == 6
    assert '诸葛来来' in chunks[2]['content'] and '大屏电视-C端' in chunks[2]['content']
    assert 'Vue' in chunks[1]['content'] and 'Vant-UI' in chunks[1]['content']
    assert 'GtiLab' in chunks[4]['content'] and '@薛坚榆' in chunks[4]['content']
    assert len(chunks[4]['source']['references']) == 4
    assert all(item['source']['document'] == 'H5前端组入职培训文档' for item in chunks)
    assert '综合评价' in chunks[5]['content']


def test_invalid_section_numbering_rejected(tmp_path):
    path = tmp_path/'source.md'
    path.write_text('# 标题\n## 1、标题\n正文\n## 3、标题\n正文', encoding='utf-8')
    with pytest.raises(KnowledgeError):
        split_source(path)


def test_tokenization_is_shared_and_skips_urls():
    assert tokenize('Vue 技术') == tokenize('vue 技术')
    assert '的' not in tokenize('前端的工作')
    assert 'example' not in tokenize('[规范](https://example.com/path)')


def test_rrf_normalization_hand_calculation():
    result = fuse_ranks([0, 1], [1, 2])
    assert result[0]['index'] == 1
    assert result[0]['match_score'] == pytest.approx((1/62+1/61)/(2/61), abs=1e-6)
    assert next(item for item in result if item['index'] == 0)['match_score'] == .5
    assert fuse_ranks([0], [0])[0]['match_score'] == 1
    assert len(result) == 3
    assert fuse_ranks([], [0])[0]['match_score'] == .5


@pytest.mark.parametrize('values', [[[0, 0]], [[float('nan'), 1]], [[float('inf'), 1]], [1, 2], []])
def test_invalid_vectors_fail(values):
    with pytest.raises(KnowledgeError):
        normalized_vectors(values, 1)


def test_embedding_rest_reorders_indices():
    client = Embeddings('https://example.invalid/v1/', 'test-key', 'embedding-model')
    with patch('core.knowledge.httpx.Client') as factory:
        http = factory.return_value.__enter__.return_value
        http.post.return_value = httpx.Response(200, request=httpx.Request('POST', 'https://example.invalid'),
            json={'data': [{'index': 1, 'embedding': [0, 3]}, {'index': 0, 'embedding': [2, 0]}]})
        np.testing.assert_allclose(client.embed(['甲', '乙']), np.eye(2))
        assert http.post.call_args.args[0] == 'https://example.invalid/v1/embeddings'
        assert http.post.call_args.kwargs['json']['input'] == ['甲', '乙']


@pytest.mark.parametrize('status,entries', [(401, []), (200, [{'index': 1, 'embedding': [1]}]),
                                           (200, [{'index': 0, 'embedding': [0]}])])
def test_embedding_error_redaction(status, entries):
    with patch('core.knowledge.httpx.Client') as factory:
        factory.return_value.__enter__.return_value.post.return_value = httpx.Response(status,
            request=httpx.Request('POST', 'https://example.invalid'), json={'data': entries})
        with pytest.raises(KnowledgeError, match='未降级') as error:
            Embeddings('https://example.invalid', 'private-secret', 'model').embed(['内容'])
        assert 'private-secret' not in str(error.value)


@pytest.fixture
def built(tmp_path):
    embeddings = Mock()
    embeddings.model = 'test-embedding'
    embeddings.embed.return_value = np.eye(6, dtype='float32')
    build_index(SOURCE, tmp_path, embeddings)
    return tmp_path, embeddings


def test_real_faiss_and_lexical_fusion_with_metadata(built):
    directory, embeddings = built
    embeddings.embed.return_value = np.array([[0, 0, 0, 0, 1, 0]], dtype='float32')
    result = HybridKnowledge(directory, embeddings).search('Gitlab 代码仓库权限', 3)
    assert result['results'][0]['section'] == 5
    assert result['results'][0]['vector_rank'] == 1
    assert result['results'][0]['cosine_similarity'] == pytest.approx(1)
    assert all(0 <= item['match_score'] <= 1 and item['source'] for item in result['results'])
    assert len({item['id'] for item in result['results']}) == 3


def test_no_lexical_overlap_is_still_vector_retrieval(built):
    directory, embeddings = built
    embeddings.embed.return_value = np.array([[0, 0, 0, 0, 0, 1]], dtype='float32')
    result = HybridKnowledge(directory, embeddings).search('zzzzzzunknown', 1)
    assert result['results'][0]['section'] == 6
    assert result['results'][0]['match_score'] == .5
    assert result['results'][0]['bm25_rank'] is None


def test_query_embedding_failure_never_degrades(built):
    directory, embeddings = built
    embeddings.embed.side_effect = KnowledgeError('模拟服务失败')
    with pytest.raises(KnowledgeError):
        HybridKnowledge(directory, embeddings).search('代码仓库')


def test_failed_build_preserves_current_index(built):
    directory, embeddings = built
    before = (directory/'current.json').read_bytes()
    embeddings.embed.side_effect = KnowledgeError('模拟构建失败')
    with pytest.raises(KnowledgeError):
        build_index(SOURCE, directory, embeddings)
    assert (directory/'current.json').read_bytes() == before


@pytest.mark.parametrize('target', ['documents.json', 'vectors.faiss', 'model'])
def test_inconsistent_index_fails_explicitly(built, target):
    directory, embeddings = built
    if target == 'model':
        embeddings.model = 'different-model'
    else:
        manifest = json.loads((directory/'current.json').read_text())
        file = directory/manifest['version']/target
        file.write_bytes(file.read_bytes()+b'broken')
    with pytest.raises(KnowledgeError, match='索引缺失或不一致'):
        HybridKnowledge(directory, embeddings)


def test_query_dimension_mismatch(built):
    directory, embeddings = built
    embeddings.embed.return_value = np.array([[1, 0]], dtype='float32')
    with pytest.raises(KnowledgeError, match='维度'):
        HybridKnowledge(directory, embeddings).search('代码仓库')


def test_rrf_tie_prefers_keyword_evidence():
    result = fuse_ranks([1, 0], [0, 1])
    assert result[0]['index'] == 1
    assert result[0]['match_score'] == result[1]['match_score']
