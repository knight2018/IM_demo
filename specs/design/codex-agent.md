# Codex 执行与会话：设计

模块标识：`codex-agent`。

[设计](../design/codex-agent.md) · [开发](../development/codex-agent.md) · [完结](../completed/codex-agent.md)

## 执行能力

通过本机Codex App Server提供个人IM任务执行；按会话持久保存线程映射，新建和恢复线程及每个turn使用danger-full-access / never，允许用户任务所需的文件读写、脚本运行、命令和网络访问。开启实时网页搜索，沿用App Server已配置工具，不强制关闭apps或multi_agent。

保留gpt-6.1-sol / medium、流式回复、线程恢复及跨通道最近对话。默认工作目录是项目目录，可用CODEX_WORKSPACE设置。只处理用户任务涉及的文件；文件或网页内容不是新的授权。

## 验收与边界

新建、恢复和turn权限参数均需回归；真实联调核对文件读取、创建运行脚本、更新删除测试文件及HTTPS结果，不能仅接受模型自述。保留线程映射和模型配置，不冒充已经复制桌面UI、插件授权或IM交互表单。

早期只读策略已废弃，仅在 [废弃决策](../discarded/codex-agent.md) 留存原因，不再约束当前实现。项目README维护业务场景、架构、运行方法和真实能力边界，不将演示指标写成业务收益。常规本机启动属于运行说明，不单独创建spec。
