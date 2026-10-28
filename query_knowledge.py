"""命令行查看两路排名、来源和归一化混合匹配度。"""

import argparse
import json
from core import config
from core.rerank import Reranker
from core.knowledge import Embeddings, HybridKnowledge

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('query')
    parser.add_argument('--top-k', type=int, default=3)
    args = parser.parse_args()
    knowledge = HybridKnowledge(config.KNOWLEDGE_PATH,
        Embeddings(config.LLM_BASE_URL, config.LLM_API_KEY, config.EMBEDDING_MODEL),
        Reranker(config.LLM_BASE_URL, config.LLM_API_KEY, config.RERANK_MODEL))
    print(json.dumps(knowledge.search(args.query, args.top_k), ensure_ascii=False, indent=2))
