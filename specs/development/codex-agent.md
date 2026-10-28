# Codex 执行与会话：开发

模块标识：`codex-agent`。

[设计](../design/codex-agent.md) · [开发](../development/codex-agent.md) · [完结](../completed/codex-agent.md)

## 实现位置

`core/codex.py` 负责WebSocket JSON-RPC、线程创建与恢复、工具事件和流式结果；`handlers/codex_chat.py` 负责会话隔离键、工作目录和桥接装配。`core/model_router.py` 补齐最近完整对话，不改变既有Codex线程映射。

## 数据与配置

线程映射保存在 `data/codex-sessions.sqlite3`。连接、模型、超时、推理强度和工作目录由 `core/config.py` 集中读取。已有只读线程通过明确的开发者策略更新应用完整权限；对不支持的交互请求明确失败，不伪造审批。

## 维护约束

保持现有模型、medium推理强度、会话隔离及流式事件协议。执行权限调整须同时核对新线程、恢复线程与每次turn；当前完整权限与历史只读约束不可混写。初始化或排查本机运行不增加独立功能spec。

## 关联测试

- `tests/test_codex.py`
- `tests/test_gateway.py`
- `tests/test_model_routing.py`
