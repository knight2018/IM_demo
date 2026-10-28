# 知识库、混合检索与重排：开发

模块标识：`knowledge-retrieval`。

[设计](../design/knowledge-retrieval.md) · [开发](../development/knowledge-retrieval.md) · [完结](../completed/knowledge-retrieval.md)

## 实现位置

`knowledge/h5-onboarding.md` 保存正文；`core/knowledge.py` 实现切片、Embedding、BM25、FAISS、RRF及知识工具；`core/rerank.py` 实现重排客户端。`build_knowledge.py` 构建索引，`query_knowledge.py` 展示检索结果。`handlers/model_routing.py` 装配工具，`core/api_chat.py` 保留原工具循环并支持知识结果。

## 存储与配置

FAISS与JSON元数据存于Git忽略的 `data/knowledge`，同一版本目录全部写入成功后原子切换current.json。校验模型、维度、索引行数及元数据/索引校验和；运行时只读，不自动生成假向量。修改资料或Embedding模型后重建并重启；更换Rerank无需重建向量索引。

EMBEDDING_MODEL默认text-embedding-3-small，实测1536维；RERANK_MODEL默认qwen3-rerank。两者沿用现有LLM接口地址和密钥。构建发送六节标题与正文；查询发送问题生成向量，重排发送问题及候选标题正文。

## 维护约束

应用知识工具和命令行统一注入重排器。HybridKnowledge未注入重排器的路径仅供显式演示原RRF融合，不能作为运行时错误回退。保持原文ID和来源映射，候选在重排后再截断，保留hybrid_score用于教学对比。提示词中引用内容是资料，不作为新指令执行。

## 关联测试

- `tests/test_knowledge.py`
- `tests/test_knowledge_tools.py`
- `tests/test_rerank.py`
