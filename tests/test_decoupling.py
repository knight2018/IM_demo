"""SPEC-003：桥接本地命令与网关无飞书依赖回归。"""

import os
import subprocess
import sys
import threading
from unittest.mock import Mock

from bridges.feishu import attach_gateway
from core import config
from core.dispatcher import MessageContext
from gateway.client import GatewayClient
from handlers import build_registry


def context(text: str) -> MessageContext:
    return MessageContext('local-'+text, 'chat', 'p2p', 'user', text, True)


def test_local_commands_work_when_gateway_is_unavailable() -> None:
    client = Mock(spec=GatewayClient)
    client.reply.side_effect = RuntimeError('网关已停止')
    registry = attach_gateway(build_registry(), client)
    try:
        for text in ['/ping', '/help']:
            handler = registry.resolve(context(text))
            assert handler.local_only
            reply = handler.handle(context(text))
            assert reply
        client.reply.assert_not_called()
        ctx = context('/model')
        assert not registry.resolve(ctx).local_only
    finally:
        registry.shutdown()


def test_ping_not_queued_behind_busy_gateway_workers(monkeypatch) -> None:
    monkeypatch.setattr(config, 'WORKER_THREADS', 1)
    delivery = Mock()
    client = Mock(spec=GatewayClient)
    entered, release, pong = threading.Event(), threading.Event(), threading.Event()
    def remote(*args):
        entered.set()
        assert release.wait(5)
        return '设置'
    client.reply.side_effect = remote
    delivery.send_text.side_effect = lambda chat, text: pong.set() if text.startswith('pong') else None
    registry = attach_gateway(build_registry(delivery), client)
    try:
        registry.dispatch(context('/model busy'))
        assert entered.wait(3)
        registry.dispatch(context('/ping independent'))
        assert pong.wait(2)
    finally:
        release.set()
        registry.shutdown()


def test_gateway_starts_with_feishu_imports_forbidden() -> None:
    script = r"""
import importlib.abc
import sys
import threading
import http.client
class BlockIM(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in {'core.feishu', 'core.streaming'} or fullname.startswith('lark_oapi'):
            raise AssertionError('网关尝试导入飞书模块: '+fullname)
sys.meta_path.insert(0, BlockIM())
import gateway_server
from handlers import build_registry
from gateway.service import Gateway
from gateway.server import make_server
from pathlib import Path
from tempfile import TemporaryDirectory
registry = build_registry()
with TemporaryDirectory() as directory:
    server = make_server(('127.0.0.1', 0), Gateway(registry, Path(directory)/'requests.db'), 'x'*32)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        http = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)
        http.request('GET', '/healthz')
        response = http.getresponse()
        assert response.status == 200
        response.read()
        http.close()
        assert 'core.feishu' not in sys.modules
        assert not any(name.startswith('lark_oapi') for name in sys.modules)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
registry.shutdown()
"""
    result = subprocess.run([sys.executable, '-c', script], capture_output=True, timeout=30,
        env=dict(os.environ, FEISHU_APP_ID='', FEISHU_APP_SECRET='', REPLY_BACKEND='codex', PYTHONIOENCODING='utf-8'))
    assert result.returncode == 0, result.stderr.decode(errors='replace')
