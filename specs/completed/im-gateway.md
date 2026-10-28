# 网关、模型路由与消息调度：完结

模块标识：`im-gateway`。

[设计](../design/im-gateway.md) · [开发](../development/im-gateway.md) · [完结](../completed/im-gateway.md)

## 已验证行为

网关协议、隔离和请求回放、本地自检、auto快速路由、FIFO及跨会话公平调度、超限拒绝、发送失败后继续执行、平滑排空均有回归覆盖。

最近一次历史全量回归为160项通过、2条已有飞书SDK弃用警告（9.71秒，见 `data/pytest-rerank.log`）；本次文档合并没有重新运行功能测试。队列功能交付时为121项通过（9.10秒），运行配置为8/5/100。早期网关与解耦阶段分别为64项、81项通过。

## 历史联调与证据

原网关Codex调用：首个snapshot 3.110秒、总计5.219秒，终态回放一致。队列部署时健康检查200、飞书重新连接，证据为 `data/pytest-dispatch-queue.log`、`data/dispatch-queue-deployment.json`。测试模拟飞书发送，未主动向真实聊天发送测试消息。

以上是此前测试记录，不代表本次文档整理新增的实测。最新状态及后续验收在本文件和状态索引更新，不为一次修复另建spec。

## 关联测试

- `tests/test_gateway.py`
- `tests/test_dispatch.py`
- `tests/test_dispatch_queue.py`
- `tests/test_model_routing.py`
- `tests/test_decoupling.py`
- `tests/test_streaming.py`
- `tests/test_lifecycle.py`
