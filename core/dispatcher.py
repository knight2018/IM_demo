"""注册分发机制：把一条消息交给最合适的 handler 处理。

设计要点：
- handler 通过 `registry.register(...)` 注册，携带 matcher 与 priority
- dispatch 时按 priority 降序找第一个 match 的 handler，没有则用 default
- handler 只负责"产出回复文本"，发送动作统一由 dispatcher 完成，
  保证不同功能的 handler 不需要重复关心通道细节
"""

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from . import config, feishu
from .streaming import CardStream
if TYPE_CHECKING:
    from handlers.base import BaseHandler

# 已处理过的 message_id，飞书可能因超时重试重复推送，需要幂等
_seen_message_ids: set[str] = set()
_seen_lock = threading.Lock()


@dataclass
class MessageContext:
    """一次消息处理的上下文，handler 只能读这里的数据。"""

    message_id: str
    chat_id: str
    chat_type: str
    sender_id: str
    text: str          # 已去掉 @占位符的用户文本
    mentioned: bool    # 群聊中是否被 @
    raw: dict = field(default_factory=dict)


class HandlerRegistry:
    """handler 注册表 + 分发器。"""

    def __init__(self) -> None:
        self._handlers: list = []
        self._default = None
        self._state_lock = threading.Lock()
        self._closing = False
        self._executor = ThreadPoolExecutor(
            max_workers=config.WORKER_THREADS, thread_name_prefix="handler"
        )

    def register(self, handler, *, default: bool = False) -> None:
        """注册 handler；default=True 表示作为兜底处理器。"""
        if default:
            self._default = handler
        else:
            self._handlers.append(handler)
        # priority 高的先匹配，同优先级按注册顺序
        self._handlers.sort(key=lambda h: h.priority, reverse=True)

    @property
    def handlers(self) -> list:
        """已注册的可匹配处理器（不含兜底）。"""
        return list(self._handlers)

    def resolve(self, ctx: MessageContext):
        """按优先级挑选第一个匹配的 handler。"""
        for handler in self._handlers:
            if handler.match(ctx):
                return handler
        return self._default

    def dispatch(self, ctx: MessageContext) -> None:
        """异步执行 handler：立即返回以满足飞书 3 秒约束。"""
        # 关闭门闩与提交任务互斥，避免检查后刚好遇到线程池关闭。
        with self._state_lock:
            if self._closing:
                # 异常交给飞书 SDK 返回失败确认，不将未处理事件标为已处理。
                raise RuntimeError('机器人正在平滑重启，请稍后重试消息')
            self._submit(ctx)

    def _submit(self, ctx: MessageContext) -> None:
        if not _mark_seen(ctx.message_id):
            print(f"[skip] 重复消息 {ctx.message_id}")
            return

        handler = self.resolve(ctx)
        if handler is None:
            print(f"[skip] 无可用 handler: {ctx.text!r}")
            return

        print(f"[dispatch] handler={handler.name} text={ctx.text!r}")
        self._executor.submit(self._run, handler, ctx)

    def shutdown(self) -> None:
        """先停止接收新任务，再等待已有任务生成和发送结束，不取消排队任务。"""
        with self._state_lock:
            self._closing = True
        print('[lifecycle] draining existing replies', flush=True)
        self._executor.shutdown(wait=True, cancel_futures=False)
        print('[lifecycle] replies drained', flush=True)

    def _run(self, handler: 'BaseHandler', ctx: MessageContext) -> None:
        started = time.monotonic()
        print(f'[timing] message={ctx.message_id} stage=worker_started', flush=True)
        if handler.streaming:
            stream: CardStream | None = None
            try:
                stream = CardStream(ctx.chat_id)
                print(f'[timing] message={ctx.message_id} stage=card_created seconds={time.monotonic()-started:.3f}', flush=True)
                reply = handler.handle_stream(ctx, stream.update)
                print(f'[timing] message={ctx.message_id} stage=generation_completed seconds={time.monotonic()-started:.3f}', flush=True)
                stream.finish(reply or '本次未生成文字回复。')
                print(f'[timing] message={ctx.message_id} stage=delivery_completed seconds={time.monotonic()-started:.3f}', flush=True)
            except Exception as exc:
                print(f'[stream error] {handler.name}: {exc!r}')
                if stream is not None:
                    try:
                        stream.fail(str(exc))
                    except Exception as send_error:
                        print(f'[stream update error] {send_error!r}')
            return
        try:
            reply = handler.handle(ctx)
        except Exception as exc:  # 不让单个 handler 的异常影响整体连接
            print(f"[handler error] {handler.name}: {exc!r}")
            reply = f"处理失败：{exc}"
        if reply:
            feishu.send_text(ctx.chat_id, str(reply))
            print(f'[timing] message={ctx.message_id} stage=text_send_returned seconds={time.monotonic()-started:.3f}', flush=True)


def _mark_seen(message_id: str) -> bool:
    """返回 True 表示首次处理，False 表示重复推送。"""
    with _seen_lock:
        if message_id in _seen_message_ids:
            return False
        _seen_message_ids.add(message_id)
        return True
