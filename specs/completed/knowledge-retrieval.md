# 知识库、混合检索与重排：完结

模块标识：`knowledge-retrieval`。

[设计](../design/knowledge-retrieval.md) · [开发](../development/knowledge-retrieval.md) · [完结](../completed/knowledge-retrieval.md)

## 最近测试结果

重排完成后的全量回归为160项通过，9.71秒，2条原有SDK弃用警告，证据 `data/pytest-rerank.log`。此前混合检索阶段146项通过，9.64秒。本次仅合并文档，未重跑功能或真实模型测试。

## 真实数据与检索

6节资料生成6条1536维向量，真实text-embedding-3-small构建成功。最初章节样例首位命中5/6；统一去除通用功能词并定义RRF同分规则后重新测为6/6，工作重心的hybrid_score为0.991935。

加入qwen3-rerank后六节样例仍为6/6；同一直接查询示例rerank_score约0.911277。只代表该小样本，不证明一般准确率提升。接口真实返回有效0—1 relevance_score。

## 模型与网关联调

API实际调用search_knowledge并输出第5节联系人与来源，原混合版本8.128秒。重排版本平滑重启后健康检查200，关闭Tavily的独立会话仍完成第3节工作重心问答，返回匹配度0.904327486407495，联调7.931秒。模型生成的查询表述与直接查询不同，分数无需相同。飞书重新连接，未主动发送真实飞书测试消息；未改变用户既有会话设置。

## 证据

`data/pytest-knowledge.log`、`data/knowledge-retrieval-check.json`、`data/knowledge-api-check.json`、`data/knowledge-gateway-check.ndjson`、`data/pytest-rerank.log`、`data/knowledge-rerank-check.json`、`data/rerank-gateway-check.ndjson`。UTF-8与diff检查在交付时通过。资料只含用户提供的正文，无引用网页抓取，无SQL或外部向量数据库服务。

## 关联测试

- `tests/test_knowledge.py`
- `tests/test_knowledge_tools.py`
- `tests/test_rerank.py`
