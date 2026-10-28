"""六节入职资料的 BM25、FAISS 向量检索及可解释排名融合。"""

import hashlib
import json
import logging
import re
import uuid
from pathlib import Path

import faiss
import httpx
import jieba
import numpy as np
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ConfigDict, Field
from rank_bm25 import BM25Okapi
from core.rerank import Reranker


class KnowledgeError(RuntimeError):
    """不包含密钥和上游响应正文的知识库错误。"""


jieba.setLogLevel(logging.WARNING)
# 通用功能词不表达知识主题；两路分词中的文档和问题统一处理。
STOPWORDS = set('的 了 是 什么 哪些 怎么 如何 请 问 一下 我 我们 你 你们 吗 呢 啊 有 在 和 与 及 或 就 都 也 会 要 可以 需要'.split())


def tokenize(text: str) -> list[str]:
    """文档和问题使用相同分词；网址仅作出处，不参加关键词匹配。"""
    text = re.sub(r'https?://[^\s)]+', '', text).lower()
    return [word for word in jieba.cut_for_search(text) if word not in STOPWORDS and re.search(r'[\w\u4e00-\u9fff]', word)]


def split_source(path: Path) -> list[dict]:
    text = path.read_text(encoding='utf-8')
    title = text.splitlines()[0].removeprefix('# ').strip()
    headings = list(re.finditer(r'^## ([1-6])、(.+)$', text, re.MULTILINE))
    if [match[1] for match in headings] != list('123456'):
        raise KnowledgeError('知识资料必须严格包含按顺序排列的 1—6 节')
    chunks = []
    for i, match in enumerate(headings):
        content = text[match.end():headings[i+1].start() if i+1 < len(headings) else len(text)].strip()
        if not content:
            raise KnowledgeError('知识章节不能为空')
        chunks.append({'id': f'h5-onboarding-{match[1]}', 'section': int(match[1]),
            'title': match[2], 'content': content,
            'source': {'document': title, 'section': f'{match[1]}、{match[2]}',
                       'origin': '用户提供的入职培训文档文本', 'file': 'knowledge/'+path.name,
                       'references': re.findall(r'\[([^\]]+)\]\((https?://[^)]+)\)', content)}})
    return chunks


def normalized_vectors(values, count: int) -> np.ndarray:
    matrix = np.asarray(values, dtype='float32')
    if matrix.ndim != 2 or matrix.shape[0] != count or matrix.shape[1] < 1:
        raise KnowledgeError('Embedding 返回数量或维度不合法')
    norms = np.linalg.norm(matrix, axis=1)
    if not np.isfinite(matrix).all() or not np.isfinite(norms).all() or (norms <= 0).any():
        raise KnowledgeError('Embedding 返回无效向量')
    matrix = np.ascontiguousarray(matrix)
    faiss.normalize_L2(matrix)
    return matrix


class Embeddings:
    def __init__(self, base_url: str, api_key: str | None, model: str):
        self.base_url, self.api_key, self.model = base_url, api_key, model

    def embed(self, texts: list[str]) -> np.ndarray:
        if not self.api_key:
            raise KnowledgeError('知识库 Embedding 密钥未配置')
        try:
            with httpx.Client(timeout=60, trust_env=False) as client:
                response = client.post(self.base_url.rstrip('/')+'/embeddings',
                    headers={'Authorization': 'Bearer '+self.api_key},
                    json={'model': self.model, 'input': texts, 'encoding_format': 'float'})
                response.raise_for_status()
                entries = response.json()['data']
            if sorted(item['index'] for item in entries) != list(range(len(texts))):
                raise ValueError('向量序号错误')
            return normalized_vectors([item['embedding'] for item in sorted(entries, key=lambda item: item['index'])], len(texts))
        except Exception:
            raise KnowledgeError('Embedding 请求失败或向量无效；未降级为纯关键词检索。') from None


def build_index(source: Path, directory: Path, embeddings: Embeddings) -> dict:
    chunks = split_source(source)
    vectors = embeddings.embed([chunk['title']+'\n'+chunk['content'] for chunk in chunks])
    index = faiss.IndexFlatIP(vectors.shape[1])
    index.add(vectors)
    directory.mkdir(parents=True, exist_ok=True)
    version = directory/uuid.uuid4().hex
    version.mkdir()
    metadata = {'model': embeddings.model, 'dimension': index.d, 'chunks': chunks}
    raw = json.dumps(metadata, ensure_ascii=False, indent=2).encode('utf-8')
    (version/'documents.json').write_bytes(raw)
    faiss.write_index(index, str(version/'vectors.faiss'))
    manifest = {'version': version.name, 'documents_sha256': hashlib.sha256(raw).hexdigest(),
                'index_sha256': hashlib.sha256((version/'vectors.faiss').read_bytes()).hexdigest()}
    # 新索引完整落盘后才切换指针，构建失败不会破坏正在使用的版本。
    pending = directory/(version.name+'.json')
    pending.write_text(json.dumps(manifest), encoding='utf-8')
    pending.replace(directory/'current.json')
    return {'chunks': len(chunks), 'dimension': index.d, 'model': embeddings.model}


def fuse_ranks(keyword: list[int], vector: list[int]) -> list[dict]:
    """按两路排名计算 RRF，再除以两路同时第一的理论最大值。"""
    records = {}
    for name, ranking in [('bm25_rank', keyword), ('vector_rank', vector)]:
        for rank, index in enumerate(ranking, 1):
            record = records.setdefault(index, {'index': index, 'rrf_score': 0.0,
                                               'bm25_rank': None, 'vector_rank': None})
            record[name] = rank
            record['rrf_score'] += 1/(60+rank)
    for record in records.values():
        record['match_score'] = round(record['rrf_score']/(2/61), 6)
    return sorted(records.values(), key=lambda record: (-record['rrf_score'], record['bm25_rank'] or 1000000, record['index']))


class HybridKnowledge:
    def __init__(self, directory: Path, embeddings: Embeddings, reranker: Reranker | None = None):
        self.embeddings = embeddings
        self.reranker = reranker
        try:
            manifest = json.loads((directory/'current.json').read_text())
            if not re.fullmatch(r'[0-9a-f]{32}', manifest['version']):
                raise ValueError('版本错误')
            version = directory/manifest['version']
            raw = (version/'documents.json').read_bytes()
            if hashlib.sha256(raw).hexdigest() != manifest['documents_sha256']:
                raise ValueError('元数据校验失败')
            if hashlib.sha256((version/'vectors.faiss').read_bytes()).hexdigest() != manifest['index_sha256']:
                raise ValueError('索引校验失败')
            metadata = json.loads(raw)
            self.chunks = metadata['chunks']
            self.index = faiss.read_index(str(version/'vectors.faiss'))
            if metadata['model'] != embeddings.model or metadata['dimension'] != self.index.d:
                raise ValueError('模型或维度不一致')
            if self.index.ntotal != len(self.chunks) or not self.chunks:
                raise ValueError('索引与资料数量不一致')
            self.bm25 = BM25Okapi([tokenize(chunk['title']+'\n'+chunk['content']) for chunk in self.chunks])
        except Exception:
            raise KnowledgeError('知识索引缺失或不一致，请运行 build_knowledge.py 后重启网关。') from None

    def search(self, query: str, top_k: int = 3) -> dict:
        if not query.strip() or not 1 <= top_k <= 6:
            raise KnowledgeError('查询不能为空，top_k 必须为 1—6')
        vectors = self.embeddings.embed([query])
        if vectors.shape[1] != self.index.d:
            raise KnowledgeError('查询向量维度与知识索引不一致，请重新构建索引')
        # 小样本库检索全部向量，按文档编号稳定处理同分，再取前五条。
        distances, indices = self.index.search(vectors, len(self.chunks))
        cosine = {int(i): float(np.clip(score, -1, 1)) for i, score in zip(indices[0], distances[0])}
        vector_rank = sorted(cosine, key=lambda i: (-cosine[i], i))[:5]
        bm25_scores = self.bm25.get_scores(tokenize(query))
        keyword_rank = sorted((i for i, score in enumerate(bm25_scores) if score > 0),
                              key=lambda i: (-bm25_scores[i], i))[:5]
        results = []
        for item in fuse_ranks(keyword_rank, vector_rank):
            i = item.pop('index')
            results.append({**self.chunks[i], **item,
                            'bm25_score': float(bm25_scores[i]), 'cosine_similarity': cosine[i]})
        if self.reranker is not None:
            ranked = self.reranker.rank(query, [item['title']+'\n'+item['content'] for item in results])
            reranked = []
            for rank, item in enumerate(ranked, 1):
                candidate = results[item['index']]
                reranked.append({**candidate, 'hybrid_score': candidate['match_score'],
                    'hybrid_rank': item['index']+1, 'rerank_rank': rank,
                    'rerank_score': item['score'], 'match_score': item['score']})
            return {'query': query, 'method': 'BM25 + FAISS cosine + RRF(k=60) + Rerank',
                    'rerank_model': self.reranker.model, 'candidate_count': len(results),
                    'score_note': 'match_score=rerank_score，为重排模型原生0—1相关性分数；hybrid_score为原RRF归一化分数，均非正确概率。',
                    'results': reranked[:top_k]}
        return {'query': query, 'method': 'BM25 + FAISS cosine + RRF(k=60)',
                'score_note': 'match_score 为固定理论最大值归一化的排名融合分数（0—1），不是相关概率；候选资料可能无法回答问题。',
                'results': results[:top_k]}


class KnowledgeInput(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    query: str = Field(min_length=1, max_length=1000, description='完整的入职培训知识查询')
    top_k: int = Field(default=3, ge=1, le=6)


def create_knowledge_tool(directory: Path, embeddings: Embeddings,
                          reranker: Reranker | None = None) -> StructuredTool:
    # 延迟加载，缺少索引时仅知识调用报错，不影响原有命令与其他通道。
    def search_knowledge(query: str, top_k: int = 3) -> str:
        return json.dumps(HybridKnowledge(directory, embeddings, reranker).search(query, top_k), ensure_ascii=False)
    return StructuredTool.from_function(search_knowledge, args_schema=KnowledgeInput,
        description='查询内部《H5前端组入职培训文档》：同事介绍、Vue及组件库、项目、代码规范、Gitee/Gerrit/GitLab权限与VPN办理、绩效。返回原文、来源章节和0—1匹配分数；公司入职问题必须先用本工具，不用网页搜索替代。')
