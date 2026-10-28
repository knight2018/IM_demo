"""注册分发机制：把一条消息交给最合适的 handler 处理。

设计要点：
- handler 通过 `registry.register(...)` 注册，携带 matcher 与 priority
- dispatch 时按 priority 降序找第一个 match 的 handler，没有则用 default
- handler 只负责"产出回复文本"，发送动作统一由 dispatcher 完成，
  保证不同功能的 handler 不需要重复关心通道细节
"""

import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

from . import config
from .messages import MessageContext
from .delivery import MessageDelivery, ReplyStream
if TYPE_CHECKING:
    from handlers.base import BaseHandler

# 已处理过的 message_id，飞书可能因超时重试重复推送，需要幂等
_seen_message_ids: set[str] = set()
_seen_lock = threading.Lock()


class HandlerRegistry:
    """handler 注册表 + 分发器。"""

    def __init__(self, delivery: MessageDelivery | None = None) -> None:
        self.delivery = delivery
        self._handlers: list[BaseHandler] = []
        self._default: BaseHandler | None = None
        self._state_lock = threading.Lock()
        self._closing = False
        self._drained = threading.Condition(self._state_lock)
        self._queues: dict[tuple[str, str, str, str], deque] = {}
        self._ready: deque[tuple[str, str, str, str]] = deque()
        self._active: set[tuple[str, str, str, str]] = set()
        self._notified: set[tuple[str, str, str, str]] = set()
        self._waiting = 0
        self._workers = config.WORKER_THREADS
        self._session_limit = config.SESSION_QUEUE_LIMIT
        self._global_limit = config.GLOBAL_QUEUE_LIMIT
        self._notice_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="queue-notice")
        self._notice_slots = threading.BoundedSemaphore(64)
        self._executor = ThreadPoolExecutor(
            max_workers=config.WORKER_THREADS, thread_name_prefix="handler"
        )

        self._local_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="local-command")

    def register(self, handler: 'BaseHandler', *, default: bool = False) -> None:
        """注册 handler；default=True 表示作为兜底处理器。"""
        if default:
            self._default = handler
        else:
            self._handlers.append(handler)
        # priority 高的先匹配，同优先级按注册顺序
        self._handlers.sort(key=lambda h: h.priority, reverse=True)

    @property
    def handlers(self) -> list['BaseHandler']:
        """已注册的可匹配处理器（不含兜底）。"""
        return list(self._handlers)

    def resolve(self, ctx: MessageContext) -> 'BaseHandler | None':
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
            if self.delivery is None:
                raise RuntimeError("分发发送需要显式配置 IM 适配器")
            self._submit(ctx)

    def _submit(self, ctx: MessageContext) -> None:
        if not _mark_seen(f"{ctx.channel}:{ctx.account_id}:{ctx.message_id}"):
            print(f"[skip] 重复消息 {ctx.message_id}")
            return

        handler = self.resolve(ctx)
        if handler is None:
            print(f"[skip] 无可用 handler: {ctx.text!r}")
            return

        print(f"[dispatch] handler={handler.name} text={ctx.text!r}")
        if handler.local_only:
            self._local_executor.submit(self._run, handler, ctx)
            return
        key = (ctx.channel, ctx.account_id, ctx.chat_id, ctx.sender_id)
        queue = self._queues.get(key)
        can_start = key not in self._active and not queue and len(self._active) < self._workers
        if not can_start and ((queue is not None and len(queue) >= self._session_limit)
                              or self._waiting >= self._global_limit):
            self._notice(ctx, '消息未受理：等待队列已满，请稍后重新发送。')
            return
        if queue is None:
            queue = self._queues[key] = deque()
        queue.append((handler, ctx))
        self._waiting += 1
        if key not in self._active and len(queue) == 1:
            self._ready.append(key)
        if not can_start and key not in self._notified:
            self._notified.add(key)
            self._notice(ctx, '消息已排队，将按接收顺序处理，请勿重复发送。')
        self._schedule()

    def _notice(self, ctx: MessageContext, text: str) -> None:
        """提示发送独立且有界，不阻塞接收回调、自检或模型任务。"""
        if not self._notice_slots.acquire(blocking=False):
            print('[queue] notice capacity reached', flush=True)
            return
        def send() -> None:
            try:
                assert self.delivery is not None
                self.delivery.send_text(ctx.chat_id, text)
            except Exception as exc:
                print(f'[queue] notice failed: {type(exc).__name__}', flush=True)
            finally:
                self._notice_slots.release()
        self._notice_executor.submit(send)

    def _schedule(self) -> None:
        """调用方持有状态锁；每个就绪会话仅提交一条任务。"""
        while self._ready and len(self._active) < self._workers:
            key = self._ready.popleft()
            handler, ctx = self._queues[key].popleft()
            self._waiting -= 1
            self._active.add(key)
            self._executor.submit(self._run_queued, key, handler, ctx)

    def _run_queued(self, key: tuple[str, str, str, str],
                    handler: 'BaseHandler', ctx: MessageContext) -> None:
        try:
            self._run(handler, ctx)
        finally:
            # 生成或发送异常都必须释放会话，避免后续消息永久挂起。
            with self._drained:
                self._active.remove(key)
                if self._queues[key]:
                    self._ready.append(key)
                else:
                    del self._queues[key]
                    self._notified.discard(key)
                self._schedule()
                if not self._queues:
                    self._drained.notify_all()

    def shutdown(self) -> None:
        """先停止接收新任务，再等待已有任务生成和发送结束，不取消排队任务。"""
        with self._drained:
            self._closing = True
            print('[lifecycle] draining existing replies', flush=True)
            # 排空期间继续调度已接受消息，不能提前关闭工作线程池。
            self._drained.wait_for(lambda: not self._queues)
        self._executor.shutdown(wait=True, cancel_futures=False)
        self._local_executor.shutdown(wait=True, cancel_futures=False)
        self._notice_executor.shutdown(wait=True, cancel_futures=False)
        print('[lifecycle] replies drained', flush=True)

    def _run(self, handler: 'BaseHandler', ctx: MessageContext) -> None:
        if self.delivery is None:
            raise RuntimeError("未配置 IM 发送适配器")
        started = time.monotonic()
        print(f'[timing] message={ctx.message_id} stage=worker_started', flush=True)
        if handler.streaming:
            stream: ReplyStream | None = None
            try:
                stream = self.delivery.start_stream(ctx.chat_id)
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
            self.delivery.send_text(ctx.chat_id, str(reply))
            print(f'[timing] message={ctx.message_id} stage=text_send_returned seconds={time.monotonic()-started:.3f}', flush=True)


def _mark_seen(message_id: str) -> bool:
    """返回 True 表示首次处理，False 表示重复推送。"""
    with _seen_lock:
        if message_id in _seen_message_ids:
            return False
        _seen_message_ids.add(message_id)
        return True
