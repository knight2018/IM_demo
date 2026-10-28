# Tavily 网页搜索与会话开关：开发

模块标识：`web-search`。

[设计](../design/web-search.md) · [开发](../development/web-search.md) · [完结](../completed/web-search.md)

## 实现位置

`core/tavily.py` 提供结构化搜索工具和脱敏错误；`core/api_chat.py` 在现有模型回答基础上执行工具循环；`handlers/model_routing.py` 装配工具并匹配命令；`core/model_router.py` 读取和修改会话开关。

## 状态保存与隔离

`core/routing_store.py` 的settings表追加tavily_enabled列，迁移保留模型设置与历史。每次回答通过请求参数决定是否绑定Tavily，不修改跨会话共享的ApiChat实例。/model与/help展示相关设置和命令。

## 接入边界

API模型需要支持工具调用；网页搜索与知识检索共享循环，但各自工具配置和分数语义独立。不得因关闭Tavily而移除知识工具，分类器与Codex流程保持不变。

## 关联测试

- `tests/test_tavily.py`
- `tests/test_tavily_switch.py`
