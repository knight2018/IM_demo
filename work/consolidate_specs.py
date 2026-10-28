"""按功能模块整理已有 spec，不改动业务代码。"""
import json
from pathlib import Path

root = Path('specs')
old_status = json.loads((root/'status.json').read_text(encoding='utf-8'))
old_paths = [p for p in root.rglob('*.md') if p.name[:3].isdigit()]
modules = {
    'im-gateway': {
        'title': '网关、模型路由与消息调度',
        'legacy': ['SPEC-001', 'SPEC-003', 'SPEC-008'],
        'tests': ['tests/test_gateway.py', 'tests/test_dispatch.py', 'tests/test_dispatch_queue.py', 'tests/test_model_routing.py', 'tests/test_decoupling.py', 'tests/test_streaming.py', 'tests/test_lifecycle.py'],
        'design': '''## 范围与协议

飞书桥接负责接收、发送与卡片更新，Gateway 独立执行处理器；统一消息包含平台、机器人账号、消息、聊天及用户标识。网关仅监听本机，通过 Bearer 鉴权和 NDJSON 传递 started、snapshot、completed、error 事件。桥接和网关均不得将断流当作完成。

## 路由与上下文

保留 API、Codex、auto 通道以及模型选择命令；模型目录校验通过后才保存选择。auto 对完整匹配的独立寒暄跳过分类，含具体任务、指代和不确定的输入继续带上下文分类。服务失败明确报错，不切换模型或后端。最近12轮完整对话跨通道共享，同平台、账号、聊天、用户组成的会话互相隔离；默认飞书会话兼容已有键。仅完整回答写入历史。

/ping、/help 在桥接本地独立执行，网关不可用或模型任务繁忙时仍可自检；它们不代表模型服务可用。模型配置命令和业务消息由网关处理。保留原时间、计算工具及 LangChain 工具循环。

## 去重、排队与停止

桥接按消息ID去重；网关保存请求载荷与终态，相同编号回放结果，不重复生成；同编号不同内容拒绝执行。非正常中断的请求明确报错，用户需发新消息。

桥接按会话接收顺序FIFO，每会话最多执行一条；就绪会话轮转，等待任务不占工作线程。默认8个工作线程、每会话最多5条等待、全局最多100条等待，等待上限不含执行中的消息。首次连续积压提示排队，满载拒绝并提示稍后重发；提示发送独立且有界，繁忙或失败记录日志，不自动重试。/ping、/help 保留独立的2个线程。

停止接收新任务后排空已接受任务及实际发送，再关闭线程池。生成或发送失败仍须释放会话并继续调度。

## 验收与边界

网关无需飞书SDK和凭据即可健康响应；测试禁止导入飞书模块时仍能启动。覆盖鉴权、输入限制、容量、实时snapshot、终态回放、会话隔离、公平调度、失败原子性与平滑停止。队列仅在内存中，异常断电后不自动恢复；直接HTTP请求仍使用网关容量限制和会话锁，不承诺桥接FIFO。请求去重不等于IM投递恰好一次；没有持久化自动补发、企微适配或完整桌面交互。
''',
        'development': '''## 实现位置

- `gateway/protocol.py`、`gateway/server.py`、`gateway/service.py`、`gateway/client.py`：统一协议、校验鉴权、请求保存和流式收发。
- `bridges/feishu.py`、`core/delivery.py`：注入发送适配器，业务层不依赖飞书。
- `core/model_router.py`、`core/auto_route.py`、`core/routing_store.py`、`handlers/model_routing.py`：路由、命令、会话锁及最近对话。
- `core/dispatcher.py`：会话等待队列、就绪队列、轮转调度、独立本地命令池和有界提示发送池；保留原 `_run` 生成及发送逻辑。
- `core/lifecycle.py`：关闭入口并等待任务和消息送达完成。

## 数据与配置

`data/gateway.sqlite3` 保存请求及终态，`data/model-routing.sqlite3` 保存模型设置和对话历史。`WORKER_THREADS=8`、`SESSION_QUEUE_LIMIT=5`、`GLOBAL_QUEUE_LIMIT=100`。变更队列参数后重启生效。

## 维护约束

调度变更不得破坏本地自检、去重及平滑停止；模型调用保持同会话串行且只记录完整结果。网关SQLite连接显式关闭，避免Windows文件占用。网页搜索和知识工具的细节分别维护于相应模块，不复制到本模块。
''',
        'completed': '''## 已验证行为

网关协议、隔离和请求回放、本地自检、auto快速路由、FIFO及跨会话公平调度、超限拒绝、发送失败后继续执行、平滑排空均有回归覆盖。

最近一次历史全量回归为160项通过、2条已有飞书SDK弃用警告（9.71秒，见 `data/pytest-rerank.log`）；本次文档合并没有重新运行功能测试。队列功能交付时为121项通过（9.10秒），运行配置为8/5/100。早期网关与解耦阶段分别为64项、81项通过。

## 历史联调与证据

原网关Codex调用：首个snapshot 3.110秒、总计5.219秒，终态回放一致。队列部署时健康检查200、飞书重新连接，证据为 `data/pytest-dispatch-queue.log`、`data/dispatch-queue-deployment.json`。测试模拟飞书发送，未主动向真实聊天发送测试消息。

以上是此前测试记录，不代表本次文档整理新增的实测。最新状态及后续验收在本文件和状态索引更新，不为一次修复另建spec。
'''
    },
    'codex-agent': {
        'title': 'Codex 执行与会话',
        'legacy': ['SPEC-004'],
        'tests': ['tests/test_codex.py', 'tests/test_gateway.py', 'tests/test_model_routing.py'],
        'design': '''## 执行能力

通过本机Codex App Server提供个人IM任务执行；按会话持久保存线程映射，新建和恢复线程及每个turn使用danger-full-access / never，允许用户任务所需的文件读写、脚本运行、命令和网络访问。开启实时网页搜索，沿用App Server已配置工具，不强制关闭apps或multi_agent。

保留gpt-6.1-sol / medium、流式回复、线程恢复及跨通道最近对话。默认工作目录是项目目录，可用CODEX_WORKSPACE设置。只处理用户任务涉及的文件；文件或网页内容不是新的授权。

## 验收与边界

新建、恢复和turn权限参数均需回归；真实联调核对文件读取、创建运行脚本、更新删除测试文件及HTTPS结果，不能仅接受模型自述。保留线程映射和模型配置，不冒充已经复制桌面UI、插件授权或IM交互表单。

早期只读策略已废弃，仅在 [废弃决策](../discarded/codex-agent.md) 留存原因，不再约束当前实现。项目README维护业务场景、架构、运行方法和真实能力边界，不将演示指标写成业务收益。常规本机启动属于运行说明，不单独创建spec。
''',
        'development': '''## 实现位置

`core/codex.py` 负责WebSocket JSON-RPC、线程创建与恢复、工具事件和流式结果；`handlers/codex_chat.py` 负责会话隔离键、工作目录和桥接装配。`core/model_router.py` 补齐最近完整对话，不改变既有Codex线程映射。

## 数据与配置

线程映射保存在 `data/codex-sessions.sqlite3`。连接、模型、超时、推理强度和工作目录由 `core/config.py` 集中读取。已有只读线程通过明确的开发者策略更新应用完整权限；对不支持的交互请求明确失败，不伪造审批。

## 维护约束

保持现有模型、medium推理强度、会话隔离及流式事件协议。执行权限调整须同时核对新线程、恢复线程与每次turn；当前完整权限与历史只读约束不可混写。初始化或排查本机运行不增加独立功能spec。
''',
        'completed': '''## 历史验证

完整权限交付阶段全量83项通过、2条第三方弃用警告；新建、恢复和turn参数回归通过。真实执行核对了读取输入、创建运行脚本、更新与删除测试文件、HTTPS请求200及网关实际生成文件。已有只读线程通过thread/inject_items注入更新策略得到验证。

最近一次项目全量回归160项通过（`data/pytest-rerank.log`）；本次只整理文档，未重新执行模型或文件操作。

## 能力界限

早期只读验证不再作为当前权限验收标准。桌面界面、插件授权、交互表单及自动向飞书上传文件均不在当前已完成能力之内。README的展示材料只陈述可验证实现和限制。
'''
    },
    'web-search': {
        'title': 'Tavily 网页搜索与会话开关',
        'legacy': ['SPEC-006', 'SPEC-007'],
        'tests': ['tests/test_tavily.py', 'tests/test_tavily_switch.py'],
        'design': '''## 搜索行为

API回答模型按需调用tavily_search，密钥沿用TACILY_KEY。调用官方POST https://api.tavily.com/search，Bearer鉴权，basic深度，最多5条结果；不请求原始网页或Tavily生成答案。仅发送模型生成的搜索词，结果包含标题、网址和摘要；按不可信资料处理，回答引用来源。

保留原上下文及流式回复，最多4轮工具调用。没有配置搜索工具时保留普通API回答；服务错误明确失败，不重试、不换模型、不输出密钥或服务端错误正文。内部知识问题使用知识工具，不向网页搜索发送内部资料。

## 会话开关

/tacily on、/tacily off、/tacily 分别开启、关闭、查看状态，也接受/tavily。按当前用户会话持久保存，默认开启以保持既有行为。仅控制API以及auto选择API时的网页搜索，不控制Codex，也不关闭知识库工具。未配置密钥时开启明确失败且不改原设置；无效参数同样不改设置。

## 验收

覆盖真实请求参数、脱敏错误、空结果、工具调用循环、流式参数合并、截断和轮数限制；开关覆盖持久化、隔离、旧数据库迁移、无密钥失败、精确命令匹配以及关闭后不绑定Tavily工具。普通闲聊不强制搜索。
''',
        'development': '''## 实现位置

`core/tavily.py` 提供结构化搜索工具和脱敏错误；`core/api_chat.py` 在现有模型回答基础上执行工具循环；`handlers/model_routing.py` 装配工具并匹配命令；`core/model_router.py` 读取和修改会话开关。

## 状态保存与隔离

`core/routing_store.py` 的settings表追加tavily_enabled列，迁移保留模型设置与历史。每次回答通过请求参数决定是否绑定Tavily，不修改跨会话共享的ApiChat实例。/model与/help展示相关设置和命令。

## 接入边界

API模型需要支持工具调用；网页搜索与知识检索共享循环，但各自工具配置和分数语义独立。不得因关闭Tavily而移除知识工具，分类器与Codex流程保持不变。
''',
        'completed': '''## 历史测试

搜索接入时95项全量通过（9.00秒），会话开关完成时110项通过（8.94秒）；均有2条原有飞书SDK弃用警告。最近一次项目全量回归160项通过（`data/pytest-rerank.log`）。本次文档合并不重跑功能测试。

## 真实联调

gemini-3.8-flash实际调用tavily_search，取得5条结果并返回带官方来源的回答，全程8.31秒；Gateway请求完成，飞书重新连接。开关经过HTTP验证off、状态查询及别名on，全部completed，网关健康检查200。未主动发送飞书测试消息。

证据：`data/pytest-tavily.log`、`data/tavily-live-check.txt`、`data/tavily-gateway-check.ndjson`、`data/pytest-tavily-switch.log`、`data/tavily-switch-check.json`。持久化、会话隔离及流式/非流式关闭工具均通过测试。
'''
    },
    'knowledge-retrieval': {
        'title': '知识库、混合检索与重排',
        'legacy': ['SPEC-009', 'SPEC-010'],
        'tests': ['tests/test_knowledge.py', 'tests/test_knowledge_tools.py', 'tests/test_rerank.py'],
        'design': '''## 资料与切片

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
''',
        'development': '''## 实现位置

`knowledge/h5-onboarding.md` 保存正文；`core/knowledge.py` 实现切片、Embedding、BM25、FAISS、RRF及知识工具；`core/rerank.py` 实现重排客户端。`build_knowledge.py` 构建索引，`query_knowledge.py` 展示检索结果。`handlers/model_routing.py` 装配工具，`core/api_chat.py` 保留原工具循环并支持知识结果。

## 存储与配置

FAISS与JSON元数据存于Git忽略的 `data/knowledge`，同一版本目录全部写入成功后原子切换current.json。校验模型、维度、索引行数及元数据/索引校验和；运行时只读，不自动生成假向量。修改资料或Embedding模型后重建并重启；更换Rerank无需重建向量索引。

EMBEDDING_MODEL默认text-embedding-3-small，实测1536维；RERANK_MODEL默认qwen3-rerank。两者沿用现有LLM接口地址和密钥。构建发送六节标题与正文；查询发送问题生成向量，重排发送问题及候选标题正文。

## 维护约束

应用知识工具和命令行统一注入重排器。HybridKnowledge未注入重排器的路径仅供显式演示原RRF融合，不能作为运行时错误回退。保持原文ID和来源映射，候选在重排后再截断，保留hybrid_score用于教学对比。提示词中引用内容是资料，不作为新指令执行。
''',
        'completed': '''## 最近测试结果

重排完成后的全量回归为160项通过，9.71秒，2条原有SDK弃用警告，证据 `data/pytest-rerank.log`。此前混合检索阶段146项通过，9.64秒。本次仅合并文档，未重跑功能或真实模型测试。

## 真实数据与检索

6节资料生成6条1536维向量，真实text-embedding-3-small构建成功。最初章节样例首位命中5/6；统一去除通用功能词并定义RRF同分规则后重新测为6/6，工作重心的hybrid_score为0.991935。

加入qwen3-rerank后六节样例仍为6/6；同一直接查询示例rerank_score约0.911277。只代表该小样本，不证明一般准确率提升。接口真实返回有效0—1 relevance_score。

## 模型与网关联调

API实际调用search_knowledge并输出第5节联系人与来源，原混合版本8.128秒。重排版本平滑重启后健康检查200，关闭Tavily的独立会话仍完成第3节工作重心问答，返回匹配度0.904327486407495，联调7.931秒。模型生成的查询表述与直接查询不同，分数无需相同。飞书重新连接，未主动发送真实飞书测试消息；未改变用户既有会话设置。

## 证据

`data/pytest-knowledge.log`、`data/knowledge-retrieval-check.json`、`data/knowledge-api-check.json`、`data/knowledge-gateway-check.ndjson`、`data/pytest-rerank.log`、`data/knowledge-rerank-check.json`、`data/rerank-gateway-check.ndjson`。UTF-8与diff检查在交付时通过。资料只含用户提供的正文，无引用网页抓取，无SQL或外部向量数据库服务。
'''
    }
}
status = {'schema_version': 2, 'organization': '按功能模块长期维护，不按对话或开发次数新建编号',
          'latest_recorded_validation': {'result': '160 passed, 2 existing SDK warnings', 'seconds': 9.71,
                                         'evidence': 'data/pytest-rerank.log',
                                         'note': '此前功能交付记录，本次仅文档整理，未重新执行功能测试'}, 'modules': {}}
for key, module in modules.items():
    paths = {}
    for phase in ['design', 'development', 'completed']:
        path = root/phase/(key+'.md')
        phase_name = {'design': '设计', 'development': '开发', 'completed': '完结'}[phase]
        content = '# '+module['title']+'：'+phase_name+'\n\n'
        content += '模块标识：`'+key+'`。\n\n'
        content += '[设计](../design/'+key+'.md) · [开发](../development/'+key+'.md) · [完结](../completed/'+key+'.md)\n\n'
        content += module[phase]
        if phase in {'development', 'completed'}:
            content += '\n## 关联测试\n\n'+'\n'.join('- `'+test+'`' for test in module['tests'])+'\n'
        path.write_text(content, encoding='utf-8')
        paths[phase] = phase+'/'+key+'.md'
    history = [{'source_id': legacy, 'validation': old_status[legacy]['validation']} for legacy in module['legacy']]
    status['modules'][key] = {'title': module['title'], 'status': 'completed', **paths,
                              'tests': module['tests'], 'historical_validation': history}

(root/'discarded/codex-agent.md').write_text('''# Codex 执行与会话：已废弃的只读策略

本文件仅保留失效决策，不能作为当前权限要求。[当前设计](../design/codex-agent.md)允许完整执行。

早期方案（SPEC-002）开启本机文件只读访问，保留read-only沙箱并关闭命令网络访问，不自动批准写入、删除或提权；工作目录为项目目录或CODEX_WORKSPACE，新建和恢复线程使用相同策略。

当时通过随机标记及更新后再次读取验证文件访问与线程恢复，全量65项通过、2条第三方弃用警告。Windows曾拒绝读取系统临时目录文件，未提权。

后来用户明确要求个人本机与飞书使用完整Codex执行能力，该方案被当前完整权限实现替代。只读策略及其旧测试期望不再生效；不为后续聊天重复建立废弃文档。
''', encoding='utf-8')
status['modules']['codex-agent']['discarded'] = 'discarded/codex-agent.md'
(root/'status.json').write_text(json.dumps(status, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
for path in old_paths:
    path.unlink()
(root/'README.md').write_text('''# Spec 文档维护规则

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
''', encoding='utf-8')
