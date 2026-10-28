# Tavily 网页搜索与会话开关：完结

模块标识：`web-search`。

[设计](../design/web-search.md) · [开发](../development/web-search.md) · [完结](../completed/web-search.md)

## 历史测试

搜索接入时95项全量通过（9.00秒），会话开关完成时110项通过（8.94秒）；均有2条原有飞书SDK弃用警告。最近一次项目全量回归160项通过（`data/pytest-rerank.log`）。本次文档合并不重跑功能测试。

## 真实联调

gemini-3.8-flash实际调用tavily_search，取得5条结果并返回带官方来源的回答，全程8.31秒；Gateway请求完成，飞书重新连接。开关经过HTTP验证off、状态查询及别名on，全部completed，网关健康检查200。未主动发送飞书测试消息。

证据：`data/pytest-tavily.log`、`data/tavily-live-check.txt`、`data/tavily-gateway-check.ndjson`、`data/pytest-tavily-switch.log`、`data/tavily-switch-check.json`。持久化、会话隔离及流式/非流式关闭工具均通过测试。

## 关联测试

- `tests/test_tavily.py`
- `tests/test_tavily_switch.py`
