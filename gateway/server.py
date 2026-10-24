"""本机 HTTP 网关：Bearer 认证与 NDJSON 实时事件。"""

import hmac
import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from pydantic import ValidationError
from gateway.protocol import IncomingMessage, ReplyEvent
from gateway.service import Gateway, RequestConflict


def load_token(path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        try:
            with path.open('x', encoding='utf-8') as file:
                file.write(secrets.token_urlsafe(32))
        except FileExistsError:
            pass
    token = path.read_text(encoding='utf-8').strip()
    if len(token) < 32:
        raise ValueError('网关令牌长度不足')
    return token


def make_server(address: tuple[str, int], gateway: Gateway, token: str,
                workers: int = 8) -> ThreadingHTTPServer:
    if address[0] not in {'127.0.0.1', 'localhost'}:
        raise ValueError('当前网关仅允许本机监听')
    if len(token) < 32:
        raise ValueError('网关令牌长度不足')
    slots = threading.BoundedSemaphore(workers)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            # 默认访问日志可能包含用户提交的路径，不记录请求正文或认证头。
            pass

        def setup(self) -> None:
            super().setup()
            self.connection.settimeout(15)

        def send_json(self, status: int, body: dict[str, object]) -> None:
            raw = json.dumps(body).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self) -> None:
            if self.path == '/healthz':
                self.send_json(200, {'status': 'ok', 'protocol': 'im-gateway-v1'})
            else:
                self.send_json(404, {'error': 'not_found'})

        def do_POST(self) -> None:
            if self.path != '/v1/messages':
                self.send_json(404, {'error': 'not_found'})
                return
            if not hmac.compare_digest(self.headers.get('Authorization', ''), 'Bearer '+token):
                self.send_json(401, {'error': 'unauthorized'})
                return
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 256000:
                    raise ValueError('请求大小不合法')
                message = IncomingMessage.model_validate_json(self.rfile.read(length))
                message.context()
            except (ValueError, ValidationError):
                self.send_json(400, {'error': 'invalid_message'})
                return
            if not slots.acquire(blocking=False):
                self.send_json(429, {'error': 'gateway_busy'})
                return
            try:
                self.send_response(200)
                self.send_header('Content-Type', 'application/x-ndjson; charset=utf-8')
                self.send_header('Cache-Control', 'no-store')
                self.send_header('Connection', 'close')
                self.end_headers()
                disconnected = False
                def emit(event: ReplyEvent) -> None:
                    nonlocal disconnected
                    if disconnected:
                        return
                    try:
                        self.wfile.write((event.model_dump_json()+'\n').encode('utf-8'))
                        self.wfile.flush()
                    except OSError:
                        # 桥接断线后仍完成本次生成并保存结果；同编号重连可取回终态。
                        disconnected = True
                try:
                    gateway.reply(message, emit)
                except RequestConflict:
                    emit(ReplyEvent(type='error', request_id=Gateway.request_id(message), text='消息编号冲突，拒绝执行。'))
            finally:
                self.close_connection = True
                slots.release()

    # 非守护请求线程使 server_close 等待正在生成的回复结束。
    class Server(ThreadingHTTPServer):
        daemon_threads = False
        block_on_close = True
    return Server(address, Handler)
