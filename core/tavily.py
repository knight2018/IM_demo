"""Tavily 网页搜索工具，仅返回可引用的搜索资料。"""

import json

import httpx
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ConfigDict, Field


class SearchError(RuntimeError):
    """不包含密钥或上游响应正文的搜索错误。"""


class SearchInput(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    query: str = Field(min_length=1, max_length=1000, description='独立完整的网页搜索词')


def create_search_tool(api_key: str) -> StructuredTool:
    def search(query: str) -> str:
        try:
            with httpx.Client(timeout=30, trust_env=False) as client:
                response = client.post('https://api.tavily.com/search',
                    headers={'Authorization': 'Bearer '+api_key},
                    json={'query': query, 'search_depth': 'basic', 'max_results': 5,
                          'include_answer': False, 'include_raw_content': False})
                response.raise_for_status()
                results = response.json()['results']
                if not isinstance(results, list):
                    raise ValueError('搜索结果格式错误')
                sources = []
                for item in results[:5]:
                    if not all(isinstance(item[key], str) for key in ('title', 'url', 'content')):
                        raise ValueError('搜索资料格式错误')
                    sources.append({'title': item['title'], 'url': item['url'],
                                    'content': item['content'][:6000]})
                return json.dumps({'results': sources}, ensure_ascii=False)
        except Exception:
            raise SearchError('Tavily 搜索失败，请检查 TACILY_KEY、网络或额度；本次未切换模型。') from None

    return StructuredTool.from_function(search, name='tavily_search', args_schema=SearchInput,
        description='搜索互联网以核实最新信息或查找来源。用户明确要求搜索时使用；普通闲聊无需搜索。返回网页标题、网址与摘要。')
