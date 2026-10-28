# 知识库、混合检索与重排：设计

模块标识：`knowledge-retrieval`。

[设计](../design/knowledge-retrieval.md) · [开发](../development/knowledge-retrieval.md) · [完结](../completed/knowledge-retrieval.md)

## 资料与切片

《H5前端组入职培训文档》严格按顶层1—6节切为6条，不按子标题拆分。保留人名、项目、原文拼写及引用链接。来源为用户提供的文本，不虚构原文URL，也不声称读取引用网页。资料不自动上传GitHub。

## 检索流程

jieba统一分词及通用功能词过滤用于BM25；原始标题与正文用于text-embedding-3-small。文档和问题使用同一模型，向量归一化后由FAISS IndexFlatIP按内积取得余弦相似度。两路各取前5条，BM25只保留正分，按知识ID去重，以等权RRF(k=60)融合。同分优先关键词排名，再按章节编号稳定排序。

融合后的全部候选（当前最多6条）交给qwen3-rerank，通过现有API服务POST /rerank按问题和候选标题正文重评分，再截取top_k；默认3，可指定1—6。不能在重排前先截为3条。

## 结果与分数

每条结果保留正文、文档名、章节、来源、引用链接、BM25原始分数、余弦相似度、两路排名和rrf_score。最终还包含hybrid_rank、rerank_rank、hybrid_score、rerank_score和match_score。

```text
rrf_score = 1/(60+关键词排名) + 1/(60+向量排名)
hybrid_score = rrf_score / (2/61)
match_score = rerank_score = 接口返回的 relevance_score
```

未进入某路时该路贡献为0。hybrid_score按固定理论最大值归一化，rerank_score为模型原生0—1分数、不再拉伸。最终排序只使用重排分数，不与hybrid_score再加权。所有分数均不是正确概率或可靠性保证，六条教学资料不用于推断一般准确率，也不设未经校准的拒答阈值。

## 工具及错误边界

search_knowledge提供给API模型；公司入职问题优先查知识库，模型根据正文作答并附实际来源章节和match_score，资料不支持时明确说明，不编造步骤。/tacily off只关闭网页搜索，保留知识工具，Codex保持原执行流程。

Embedding失败不得降级成纯关键词；重排失败、漏项、重复或越界索引、非法分数均报错，不退回RRF。没有关键词命中但正常取得向量结果是正常检索，不属于失败降级。

## 验收

覆盖六节切片、分词、Embedding协议、FAISS计算、RRF公式、索引一致性、完整候选先重排后截断、结果映射与同分稳定排序，以及工具调用、流式回复和Tavily开关隔离。单元测试、六节真实检索与模型/网关联调分别记录。
