"""只对完整匹配的独立寒暄快速分流，不用消息长度推断任务难度。"""

import unicodedata
from core.api_chat import Difficulty

# 不加入“好的”“继续”“为什么”等依赖上一轮任务的短句。
_SMALL_TALK = frozenset({'你好', '您好', '嗨', '哈喽', '早上好', '早安', '晚上好',
                         '晚安', 'hello', 'hi', '在吗', '谢谢', '谢谢你', '多谢', '再见', 'bye'})


def local_difficulty(text: str) -> Difficulty | None:
    normalized = unicodedata.normalize('NFKC', text).strip().casefold()
    normalized = normalized.rstrip('!！?？。．.').strip()
    if normalized in _SMALL_TALK:
        return Difficulty(difficulty='simple', reason='本地识别独立寒暄，跳过分类调用')
    return None
