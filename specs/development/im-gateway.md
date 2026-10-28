# 网关、模型路由与消息调度：开发

模块标识：`im-gateway`。

[设计](../design/im-gateway.md) · [开发](../development/im-gateway.md) · [完结](../completed/im-gateway.md)

## 实现位置

- `gateway/protocol.py`、`gateway/server.py`、`gateway/service.py`、`gateway/client.py`：统一协议、校验鉴权、请求保存和流式收发。
- `bridges/feishu.py`、`core/delivery.py`：注入发送适配器，业务层不依赖飞书。
- `core/model_router.py`、`core/auto_route.py`、`core/routing_store.py`、`handlers/model_routing.py`：路由、命令、会话锁及最近对话。
- `core/dispatcher.py`：会话等待队列、就绪队列、轮转调度、独立本地命令池和有界提示发送池；保留原 `_run` 生成及发送逻辑。
- `core/lifecycle.py`：关闭入口并等待任务和消息送达完成。

## 数据与配置

`data/gateway.sqlite3` 保存请求及终态，`data/model-routing.sqlite3` 保存模型设置和对话历史。`WORKER_THREADS=8`、`SESSION_QUEUE_LIMIT=5`、`GLOBAL_QUEUE_LIMIT=100`。变更队列参数后重启生效。

## 维护约束

调度变更不得破坏本地自检、去重及平滑停止；模型调用保持同会话串行且只记录完整结果。网关SQLite连接显式关闭，避免Windows文件占用。网页搜索和知识工具的细节分别维护于相应模块，不复制到本模块。

## 关联测试

- `tests/test_gateway.py`
- `tests/test_dispatch.py`
- `tests/test_dispatch_queue.py`
- `tests/test_model_routing.py`
- `tests/test_decoupling.py`
- `tests/test_streaming.py`
- `tests/test_lifecycle.py`
