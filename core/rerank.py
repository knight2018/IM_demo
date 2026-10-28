"""独立重排模型客户端，按查询与候选正文的相关性重新评分。"""

import math
import httpx


class RerankError(RuntimeError):
    """不包含服务端响应或密钥的重排错误。"""


class Reranker:
    def __init__(self, base_url: str, api_key: str | None, model: str):
        self.base_url, self.api_key, self.model = base_url, api_key, model

    def rank(self, query: str, documents: list[str]) -> list[dict]:
        if not self.api_key:
            raise RerankError('Rerank 密钥未配置，未退回混合排名。')
        if not documents:
            return []
        try:
            with httpx.Client(timeout=60, trust_env=False) as client:
                response = client.post(self.base_url.rstrip('/')+'/rerank',
                    headers={'Authorization': 'Bearer '+self.api_key},
                    json={'model': self.model, 'query': query, 'documents': documents,
                          'top_n': len(documents)})
                response.raise_for_status()
                results = response.json()['results']
            if not isinstance(results, list) or len(results) != len(documents):
                raise ValueError('返回候选数量不一致')
            indices = [item['index'] for item in results]
            if any(type(i) is not int for i in indices) or sorted(indices) != list(range(len(documents))):
                raise ValueError('候选索引不完整或重复')
            for item in results:
                score = item['relevance_score']
                if type(score) not in {int, float} or not math.isfinite(score) or not 0 <= score <= 1:
                    raise ValueError('重排分数必须为0—1有限数值')
            # 同分保留传入的混合排名顺序，不依赖上游返回数组顺序。
            return sorted([{'index': item['index'], 'score': float(item['relevance_score'])}
                           for item in results], key=lambda item: (-item['score'], item['index']))
        except Exception:
            raise RerankError('Rerank 请求失败或返回结果无效，未退回混合排名。') from None
