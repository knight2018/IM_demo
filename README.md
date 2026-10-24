# 飞书 IM 助手

飞书长连接接收消息，LangChain 组织 API、Codex 和自动路由。无需为收消息准备公网 IP、域名或内网穿透。

## 常用指令

| 指令 | 作用 |
| --- | --- |
| `/ping` | 检查机器人是否在线 |
| `/help` | 查看功能与指令 |
| `/use_model` | 查看当前通道及用法 |
| `/use_model api` | 固定使用 API |
| `/use_model codex` | 固定使用本机 Codex |
| `/use_model auto` | 根据问题难度自动选择：简单问题用 API，复杂问题用 Codex |
| `/model` | 查看当前通道和两边的模型 |
| `/model <模型ID>` | 修改当前固定通道的模型；auto 模式需要明确指定通道 |
| `/model api gemini-3.8-flash` | 设置 API 模型 |
| `/model codex gpt-6.1-sol` | 设置 Codex 模型，推理强度保持 medium |
| `/model list api` | 查看 API 模型目录 |
| `/model list codex` | 查看支持 medium 的 Codex 模型 |
| `/model list api deepseek` | 用关键词筛选模型目录 |

设置按“飞书会话＋用户”分别保存，重启后保留。更换模型不会自动改变通道模式。模型 ID 必须存在于对应目录；目录可见并不保证该模型支持文字对话或当前账号能够成功调用。

当前默认使用 Codex `gpt-6.1-sol`，推理强度 `medium`。API 默认使用 AGICTO 的 `gemini-3.8-flash`。

## auto 如何选择

所选 API 模型结合本次问题和最近对话判断难度，不只按关键词或文本长度判断。

- 简单：闲聊、单一常识问答、简单翻译改写、一步计算。
- 复杂：多步骤推理、复杂代码调试、系统架构设计、多个约束的方案权衡。
- 自动模式在生成正文前需要先完成难度判断。
- 最终回复标明通道、模型和自动选择依据。
- 判断或生成失败时显示失败状态，不自动切换模型或通道。

## 流式回复与记忆

普通对话先出现一张“正在生成”卡片。API 返回文本增量、Codex 返回正文增量后，机器人持续更新同一张卡片，通常最多每秒更新一次。完成后卡片显示“回复完成”，并补上通道和模型信息。

这是真实模型增量驱动的更新，不是把已生成的整段答案逐字播放。模型思考期间可能暂时没有正文；很短的回答可能一次就显示完成。命令回复仍一次性发送。

如果生成中断，原卡片标记“生成失败”，已展示片段注明可能不完整，不把片段作为成功答案保存，也不另发重复消息。若飞书卡片创建或更新接口返回错误，错误码记录在运行日志中。

API 与 Codex 共用自该功能启用后保存的最近 12 轮完整对话，便于切换通道后继续交流。不同用户、不同群聊和私聊互相隔离。旧的 Codex 线程仍会继续恢复使用。

## Codex 里的聊天在哪里

飞书首次调用 Codex 时会创建后台线程，后续恢复同一线程，不是每条消息都重新开聊。API 模式不会为该消息新建 Codex 线程。

后台线程独立于开发助手当前聊天，可能采用首次问题作为标题，也不一定归入当前项目。可在 Codex 的全部聊天或最近聊天中查找；联调测试也可能留下测试会话。桌面客户端与 App Server 的视图、筛选和刷新时机可能不同。

连接使用 Codex App Server 的 WebSocket JSON-RPC 协议，不是 ACP。当前本地地址为 `ws://127.0.0.1:4500`。

## 安装与配置

在项目目录打开终端，安装依赖：

```powershell
python -m pip install -r requirements.txt
```

复制 `.env.example` 为 `.env`，填写飞书配置。API 密钥从环境变量 `AGI_KEY` 读取；已有 `LLM_API_KEY` 配置也可使用。不要把真实密钥提交到仓库。

```ini
FEISHU_APP_ID=你的应用ID
FEISHU_APP_SECRET=你的应用密钥
REPLY_BACKEND=codex
LLM_BASE_URL=https://api.agicto.cn/v1/
LLM_MODEL=gemini-3.8-flash
CODEX_APP_SERVER_URL=ws://127.0.0.1:4500
CODEX_MODEL=gpt-6.1-sol
CODEX_REASONING_EFFORT=medium
CODEX_TIMEOUT_SECONDS=180
GROUP_REPLY_ALL=false
```

Codex 使用本机已有的 Codex 登录。旧 Agently 实现保留，只有显式配置 `REPLY_BACKEND=agently` 时走原流程。

## 启动

先启动 Codex 服务，并保持终端运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File start-codex.ps1
```

该脚本默认让 Codex 通过 `http://127.0.0.1:7897` 代理连接上游，代理仅作用于该进程，不修改系统设置。本机 Python 与 Codex 之间仍直接连接。代理端口不同时可传入：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File start-codex.ps1 -Proxy http://127.0.0.1:你的端口
```

另开终端启动飞书机器人：

```powershell
python feishu_bot.py
```

## 更新程序与停止

更新机器人代码前，先平滑停止，等待正在生成的回复发送完成；不要直接结束进程。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File stop-bot.ps1
```

停止后再运行 `python feishu_bot.py`。如果等待超时，脚本只报错，不会强行杀死仍在处理消息的进程。运行日志记录等待卡片创建、正文生成完成与最终发送完成的耗时，便于区分模型延迟和发送延迟。

## 飞书配置与验证

1. 创建企业自建应用并开启机器人能力，配置 App ID 和 App Secret。
2. 开通接收私聊消息的权限 `im:message.p2p_msg:readonly`；需要群聊时开通 `im:message.group_at_msg:readonly`。
3. 开通以机器人身份发送消息的权限 `im:message:send_as_bot`；流式更新还要求应用具备更新自身消息卡片的接口权限。
4. 使用长连接接收事件，订阅 `im.message.receive_v1`，发布版本并完成管理员审批。
5. 私聊发送 `/ping`，再发送普通问题。群聊默认需要 @ 机器人。
6. 发送 `/use_model auto`，分别尝试简单问答和复杂分析，观察卡片更新及最终通道标记。

收到事件后，耗时生成在线程池中处理。重复消息按消息 ID 去重；同一用户会话的生成串行执行。

运行自动化回归：

```powershell
python -m pytest tests -q
```

测试会模拟飞书发送和卡片更新，不向真实聊天发送测试消息。
