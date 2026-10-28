# Spec 文档维护规则

Spec按功能模块长期维护，不按一次对话、一次开发或一次修复创建编号文件。同一类型的功能迭代更新同一模块；只有新增独立职责时才新增模块。

## 模块索引

| 模块 | 设计 | 开发 | 完结 |
| --- | --- | --- | --- |
| 网关、模型路由与消息调度 | [设计](design/im-gateway.md) | [开发](development/im-gateway.md) | [完结](completed/im-gateway.md) |
| Codex执行与会话 | [设计](design/codex-agent.md) | [开发](development/codex-agent.md) | [完结](completed/codex-agent.md) |
| Tavily网页搜索与开关 | [设计](design/web-search.md) | [开发](development/web-search.md) | [完结](completed/web-search.md) |
| 知识库、混合检索与重排 | [设计](design/knowledge-retrieval.md) | [开发](development/knowledge-retrieval.md) | [完结](completed/knowledge-retrieval.md) |

## 阶段职责

- design：当前需求、行为、边界和验收条件；新迭代先更新本模块设计。
- development：实现位置、数据变更、开发进度及必要决策，不复制整篇设计。
- completed：最近通过的测试与真实联调、证据和限制；测试未全绿不能将新迭代记为完成。
- discarded：失效决策及替代原因，明确标记不能作为当前需求。

`status.json`按模块记录状态、文档路径和关联测试。测试文件继续按功能划分，不按对话复制。模块再次开发时更新状态为development，保留此前完成记录，完成新验收后再更新completed；历史数据不能写成新一次实测。开发前阅读索引及对应模块的设计、开发、完结文档。

常规克隆、安装、启动和环境排查写入README运行说明，不独立建立功能spec。agent.md或AGENTS.md可以记录入口和当前进度，不重复规格正文。

## 本次合并对应关系

- SPEC-001、SPEC-003、SPEC-008归入im-gateway。
- SPEC-004归入codex-agent，SPEC-002的失效只读决策归入discarded/codex-agent.md。
- SPEC-006、SPEC-007归入web-search。
- SPEC-009、SPEC-010归入knowledge-retrieval，当前分数和能力定义以合并后的最新设计为准。

旧编号仅用于追溯已有测试注释和历史验证，不再用于新文档命名。本机启动记录已按用户要求删除，不另建同类spec。
