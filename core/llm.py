"""LangChain 模型与工具循环，沿用已配置的 AGICTO 服务。"""

from functools import lru_cache

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool
from langchain_openai import ChatOpenAI
from core import config
from core.api_chat import ApiChat


@lru_cache(maxsize=1)
def create_request() -> ChatOpenAI:
    return ApiChat(config.LLM_BASE_URL, config.LLM_API_KEY)._model(config.LLM_MODEL)


def run_agent(prompt: str, text: str, tools: list[BaseTool]) -> str:
    """限制工具轮数；工具错误直接上报，不改模型也不伪造工具结果。"""
    model = create_request()
    runnable = model.bind_tools(tools) if tools else model
    messages: list[BaseMessage] = [SystemMessage(content=prompt), HumanMessage(content=text)]
    available = {tool.name: tool for tool in tools}
    for _ in range(8):
        response = runnable.invoke(messages)
        if not isinstance(response, AIMessage):
            raise RuntimeError('模型未返回 AI 消息')
        messages.append(response)
        if not response.tool_calls:
            if not isinstance(response.content, str) or not response.content.strip():
                raise RuntimeError('模型未返回文字')
            if response.response_metadata.get('finish_reason') not in {None, 'stop', 'end_turn'}:
                raise RuntimeError('模型回复未完整结束')
            return response.content
        for call in response.tool_calls:
            if call['name'] not in available:
                raise RuntimeError('模型请求了未注册的工具')
            result = available[call['name']].invoke(call['args'])
            messages.append(ToolMessage(content=str(result), tool_call_id=call['id']))
    raise RuntimeError('工具调用达到轮数上限，本次未完成')
