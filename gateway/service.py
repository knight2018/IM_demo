"""复用现有处理器；保存终态，重复请求回放，不重复调用模型。"""

import hashlib
import json
import sqlite3
import threading
from contextlib import closing
from collections.abc import Callable
from pathlib import Path
from core.dispatcher import HandlerRegistry
from gateway.protocol import IncomingMessage, ReplyEvent


class RequestConflict(ValueError):
    pass


class Gateway:
    def __init__(self, registry: HandlerRegistry, path: Path) -> None:
        self.registry, self.path = registry, path
        self._guard = threading.Lock()
        self._locks: dict[str, threading.Lock] = {}
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path)) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS requests (id TEXT PRIMARY KEY, payload TEXT NOT NULL, terminal TEXT)')

    @staticmethod
    def request_id(message: IncomingMessage) -> str:
        identity = json.dumps([message.channel, message.account_id, message.message_id])
        return hashlib.sha256(identity.encode()).hexdigest()

    def reply(self, message: IncomingMessage, emit: Callable[[ReplyEvent], None]) -> None:
        ctx = message.context()
        key, payload = self.request_id(message), message.model_dump_json()
        with self._guard:
            lock = self._locks.setdefault(key, threading.Lock())
        with lock:
            with closing(sqlite3.connect(self.path)) as db, db:
                row = db.execute('SELECT payload,terminal FROM requests WHERE id=?', (key,)).fetchone()
                if row and row[0] != payload:
                    raise RequestConflict('相同消息编号对应不同内容')
                if row:
                    # 非正常退出遗留的任务状态不代表成功，不自动重新调用模型。
                    terminal = (ReplyEvent.model_validate_json(row[1]) if row[1] else
                                ReplyEvent(type='error', request_id=key, text='上次任务中断，请发送一条新消息。'))
                    emit(terminal)
                    return
                db.execute('INSERT INTO requests VALUES (?,?,NULL)', (key, payload))
            emit(ReplyEvent(type='started', request_id=key))
            try:
                handler = self.registry.resolve(ctx)
                if handler is None:
                    raise RuntimeError('没有处理器')
                def update(text: str) -> None:
                    emit(ReplyEvent(type='snapshot', request_id=key, text=text))
                result = handler.handle_stream(ctx, update) if handler.streaming else handler.handle(ctx)
                terminal = ReplyEvent(type='completed', request_id=key, text=result or '')
            except Exception as exc:
                # 不把模型接口诊断、凭据或内部路径发送给桥接客户端。
                print(f'[gateway] request={key} error_type={type(exc).__name__}', flush=True)
                terminal = ReplyEvent(type='error', request_id=key, text='本次处理失败，通道和模型保持不变，请检查网关日志。')
            with closing(sqlite3.connect(self.path)) as db, db:
                db.execute('UPDATE requests SET terminal=? WHERE id=?', (terminal.model_dump_json(), key))
            emit(terminal)
