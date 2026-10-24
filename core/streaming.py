"""把模型增量合并到同一条飞书卡片，限制更新频率并保留最终完成状态。"""

import time
from collections.abc import Callable

from core import feishu


class CardStream:
    def __init__(self, chat_id: str, interval: float = 1.0,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.message_id = feishu.create_stream_card(chat_id)
        self.interval, self.clock = interval, clock
        self.last_update = clock()
        self.latest = ''

    def update(self, snapshot: str) -> None:
        self.latest = snapshot
        if self.clock() - self.last_update >= self.interval:
            feishu.update_stream_card(self.message_id, snapshot, '正在生成')
            self.last_update = self.clock()

    def finish(self, text: str) -> None:
        feishu.update_stream_card(self.message_id, text, '回复完成')

    def fail(self, reason: str) -> None:
        # 已展示的片段不会冒充完整答案；错误仍更新原消息，不另发重复回复。
        content = (self.latest+'\n\n' if self.latest else '')+'生成失败，以上内容可能不完整。\n'+reason
        feishu.update_stream_card(self.message_id, content, '生成失败')
