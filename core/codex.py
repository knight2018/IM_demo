"""通过 Codex App Server 为飞书生成回复，协议为 JSON-RPC WebSocket。"""

import hashlib
import json
import sqlite3
import threading
import time
from collections.abc import Callable
from collections import deque
from pathlib import Path
from typing import Literal, NotRequired, TypedDict
from urllib.parse import urlparse

from websockets.sync.client import ClientConnection, connect


class ReplyInput(TypedDict):
    conversation_key: str
    text: str
    model: NotRequired[str]


class CodexError(RuntimeError):
    """可展示给用户的连接或生成错误，不包含服务端敏感诊断。"""


def object_value(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise CodexError("Codex 返回了不符合协议的数据。")
    return value


def string_value(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise CodexError("Codex 返回的数据缺少必要字段。")
    return value


class RpcConnection:
    """在等待请求响应时缓存通知，避免丢失提前到达的模型回复。"""

    def __init__(self, ws: ClientConnection, deadline: float) -> None:
        self.ws = ws
        self.deadline = deadline
        self.sequence = 0
        self.events: deque[dict[str, object]] = deque()

    def send(self, payload: dict[str, object]) -> None:
        self.ws.send(json.dumps(payload, ensure_ascii=False))

    def receive(self) -> dict[str, object]:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError
        message = object_value(json.loads(self.ws.recv(timeout=remaining)))
        # IM 入口不自动批准操作，也不把服务端交互请求留到无限等待。
        if 'method' in message and 'id' in message:
            self.send({'id': message['id'], 'error': {
                'code': -32601, 'message': 'This IM client does not support interactive requests.'}})
            raise CodexError("本次任务需要交互确认，请在 Codex 中处理。")
        return message

    def request(self, method: str, params: dict[str, object]) -> dict[str, object]:
        self.sequence += 1
        request_id = self.sequence
        self.send({'id': request_id, 'method': method, 'params': params})
        while True:
            message = self.receive()
            if message.get('id') == request_id:
                if 'error' in message:
                    raise CodexError(f"Codex 请求 {method} 失败，请检查本地服务。")
                return object_value(message.get('result'))
            if 'method' in message:
                self.events.append(message)

    def next_event(self) -> dict[str, object]:
        return self.events.popleft() if self.events else self.receive()


class CodexBridge:
    """每个飞书用户会话对应独立线程；同一会话内串行生成并持久保存映射。"""

    def __init__(self, url: str, state_path: Path, workspace: Path,
                 model: str = 'gpt-6.1-sol', effort: Literal['medium'] = 'medium',
                 timeout: float = 180) -> None:
        parsed = urlparse(url)
        if parsed.scheme != 'ws' or parsed.hostname not in {'127.0.0.1', 'localhost', '::1'}:
            raise ValueError('当前接入只支持本机 Codex WebSocket 地址')
        if timeout <= 0:
            raise ValueError('Codex 超时必须大于零')
        if not model.strip() or effort != 'medium':
            raise ValueError('模型不能为空，推理强度必须为 medium')
        self.url, self.model, self.effort, self.timeout = url, model, effort, timeout
        self.state_path, self.workspace = state_path.resolve(), workspace.resolve()
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.workspace.mkdir(parents=True, exist_ok=True)
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()
        with sqlite3.connect(self.state_path) as db:
            db.execute('CREATE TABLE IF NOT EXISTS conversations (key TEXT PRIMARY KEY, thread_id TEXT NOT NULL)')

    def _lock(self, key: str) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(key, threading.Lock())

    def _thread(self, key: str) -> str | None:
        with sqlite3.connect(self.state_path) as db:
            row = db.execute('SELECT thread_id FROM conversations WHERE key=?', (key,)).fetchone()
            return str(row[0]) if row else None

    def _save(self, key: str, thread_id: str) -> None:
        with sqlite3.connect(self.state_path) as db:
            db.execute('INSERT INTO conversations (key,thread_id) VALUES (?,?)', (key, thread_id))

    def reply(self, request: ReplyInput, on_update: Callable[[str], None] | None = None) -> str:
        if not request['text'].strip() or not request['conversation_key']:
            raise ValueError('会话和消息不能为空')
        key = hashlib.sha256(request['conversation_key'].encode('utf-8')).hexdigest()
        with self._lock(key):
            if on_update is not None:
                return self._reply(key, request['text'], request.get('model'), on_update)
            if 'model' in request:
                return self._reply(key, request['text'], request['model'])
            return self._reply(key, request['text'])

    def _reply(self, key: str, text: str, model: str | None = None,
               on_update: Callable[[str], None] | None = None) -> str:
        selected_model = self.model if model is None else string_value(model)
        thread_id: str | None = None
        turn_id: str | None = None
        rpc: RpcConnection | None = None
        try:
            with connect(self.url, open_timeout=10, close_timeout=2, max_size=16*1024*1024) as ws:
                rpc = RpcConnection(ws, time.monotonic() + self.timeout)
                rpc.request('initialize', {'clientInfo': {'name': 'feishu_langchain', 'version': '0.1.0'}})
                rpc.send({'method': 'initialized', 'params': {}})
                params: dict[str, object] = {
                    'model': selected_model, 'cwd': str(self.workspace),
                    'sandbox': 'read-only', 'approvalPolicy': 'on-request',
                    'developerInstructions': '你是飞书里的中文对话助手。直接回答收到的消息，保持上下文连贯。'
                    '这里只提供文字回复，不执行本机文件或命令操作，不声称消息已经发送。',
                    'config': {'features.shell_tool': False, 'features.apps': False,
                               'features.multi_agent': False, 'web_search': 'disabled'},
                }
                thread_id = self._thread(key)
                if thread_id:
                    params.update(threadId=thread_id, excludeTurns=True)
                    result = rpc.request('thread/resume', params)
                    if string_value(object_value(result.get('thread')).get('id')) != thread_id:
                        raise CodexError('Codex 恢复的会话与本地记录不一致。')
                else:
                    result = rpc.request('thread/start', params)
                    thread_id = string_value(object_value(result.get('thread')).get('id'))
                    self._save(key, thread_id)
                result = rpc.request('turn/start', {
                    'threadId': thread_id, 'model': selected_model, 'effort': self.effort,
                    'input': [{'type': 'text', 'text': text}],
                    'approvalPolicy': 'on-request',
                    # 本机 0.164 生成的协议仅接受 type/networkAccess；旧版 access 字段已被移除。
                    'sandboxPolicy': {'type': 'readOnly', 'networkAccess': False},
                })
                turn_id = string_value(object_value(result.get('turn')).get('id'))
                try:
                    return self._answer(rpc, thread_id, turn_id, on_update)
                except Exception:
                    # 超时或协议失败后停止当前生成；不重试、不另建会话或切换模型。
                    rpc.send({'id': rpc.sequence + 1, 'method': 'turn/interrupt',
                              'params': {'threadId': thread_id, 'turnId': turn_id}})
                    raise
        except CodexError:
            raise
        except TimeoutError:
            raise CodexError('Codex 回复超时，本次没有发送不完整答案。') from None
        except Exception:
            raise CodexError('Codex 连接或响应失败，请确认本地服务仍在运行。') from None

    def _answer(self, rpc: RpcConnection, thread_id: str, turn_id: str,
                on_update: Callable[[str], None] | None = None) -> str:
        answers: dict[str, str] = {}
        partials: dict[str, str] = {}
        phases: dict[str, object] = {}
        last_snapshot = ''
        while True:
            message = rpc.next_event()
            params = object_value(message.get('params', {}))
            if params.get('threadId') != thread_id:
                continue
            method = message.get('method')
            if method == 'item/started' and params.get('turnId') == turn_id:
                item = object_value(params.get('item'))
                if item.get('type') == 'agentMessage':
                    phases[string_value(item.get('id'))] = item.get('phase')
            elif method == 'item/agentMessage/delta' and params.get('turnId') == turn_id:
                identity = string_value(params.get('itemId'))
                # 只展示正文；进度通知和推理过程不作为飞书答案发送。
                if identity in phases and phases[identity] in {None, 'final_answer'}:
                    delta = params.get('delta')
                    if isinstance(delta, str):
                        partials[identity] = partials.get(identity, '') + delta
            elif method == 'item/completed' and params.get('turnId') == turn_id:
                item = object_value(params.get('item'))
                if item.get('type') == 'agentMessage' and item.get('phase') in {None, 'final_answer'}:
                    answers[string_value(item.get('id'))] = string_value(item.get('text'))
                    partials[string_value(item.get('id'))] = string_value(item.get('text'))
            elif method == 'turn/completed':
                turn = object_value(params.get('turn'))
                if turn.get('id') != turn_id:
                    continue
                if turn.get('status') != 'completed':
                    raise CodexError('Codex 未完成本次回复，请检查服务状态后重试。')
                if not answers:
                    raise CodexError('Codex 本次没有返回可发送的完整文字回复。')
                return '\n\n'.join(answers.values())
            snapshot = '\n\n'.join(partials.values())
            if on_update is not None and snapshot and snapshot != last_snapshot:
                on_update(snapshot)
                last_snapshot = snapshot

    def available_models(self) -> list[str]:
        """仅返回服务端列出且支持 medium 的模型，不把目录可见等同于调用成功。"""
        models: list[str] = []
        with connect(self.url, open_timeout=10, close_timeout=2) as ws:
            rpc = RpcConnection(ws, time.monotonic() + 30)
            rpc.request('initialize', {'clientInfo': {'name': 'feishu_model_list', 'version': '0.1.0'}})
            rpc.send({'method': 'initialized', 'params': {}})
            cursor: str | None = None
            while True:
                result = rpc.request('model/list', {'cursor': cursor})
                entries = result.get('data')
                if not isinstance(entries, list):
                    raise CodexError('Codex 模型目录格式不正确。')
                for entry in entries:
                    item = object_value(entry)
                    efforts = item.get('supportedReasoningEfforts', [])
                    if isinstance(efforts, list) and any(object_value(e).get('reasoningEffort') == 'medium' for e in efforts):
                        models.append(string_value(item.get('model')))
                next_cursor = result.get('nextCursor')
                if not next_cursor:
                    return sorted(set(models))
                cursor = string_value(next_cursor)
