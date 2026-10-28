"""从六节培训文档构建真实 Embedding 与 FAISS 索引。"""

from core import config
from core.knowledge import Embeddings, build_index

if __name__ == '__main__':
    print(build_index(config.PROJECT_ROOT/'knowledge/h5-onboarding.md', config.KNOWLEDGE_PATH,
                      Embeddings(config.LLM_BASE_URL, config.LLM_API_KEY, config.EMBEDDING_MODEL)))
