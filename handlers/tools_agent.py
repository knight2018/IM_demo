"""工具型 Agent：演示"不同功能挂载不同工具"。

只有匹配到时间/计算类问题才进入这个 handler，
因此它的工具（获取当前时间、四则运算）不会被别的对话白白带上。
"""

import ast
import operator
from datetime import datetime

from .base import AgentHandler
from core.dispatcher import MessageContext
from langchain_core.tools import BaseTool, tool

_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPS = {ast.USub: operator.neg, ast.UAdd: operator.pos}

# 触发关键词：命中任意其一才路由到本 handler
KEYWORDS = ("几点", "时间", "日期", "今天", "算", "等于", "计算")


def _safe_eval(node: ast.AST) -> int | float:
    """只允许数值常量与基本运算符，杜绝任意代码执行。"""
    if isinstance(node, ast.Expression):
        return _safe_eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN_OPS:
        return _BIN_OPS[type(node.op)](_safe_eval(node.left), _safe_eval(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPS:
        return _UNARY_OPS[type(node.op)](_safe_eval(node.operand))
    raise ValueError("表达式只支持数字与 + - * / % **")


class ToolAgentHandler(AgentHandler):
    name = "tool-agent"
    description = "问「现在几点」「12*35 等于多少」这类问题会走带工具的助手"
    priority = 50

    system_prompt = (
        "你是 IM 中的效率助手。涉及当前时间、日期或数值计算时，"
        "必须调用工具获取准确结果，不要凭记忆猜测；回答保持简短。"
    )

    def match(self, ctx: MessageContext) -> bool:
        return any(keyword in ctx.text for keyword in KEYWORDS)

    def tools(self) -> list[BaseTool]:
        @tool
        def get_current_datetime() -> str:
            """获取当前本地日期时间，返回 YYYY-MM-DD HH:MM:SS 格式。

            当用户询问现在几点、今天是几号等与时间相关的问题时使用。
            """
            return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        @tool
        def calculate(expression: str) -> float:
            """计算数学表达式，例如 '(12 + 8) * 3' 或 '2 ** 10'。

            Args:
                expression: 只包含数字与 + - * / % ** 的表达式字符串
            """
            return _safe_eval(ast.parse(expression, mode="eval"))

        return [get_current_datetime, calculate]
