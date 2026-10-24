"""独立启动网关；无需飞书凭据，其他本机 IM 桥接可共用。"""

import threading

from core import config
from gateway.server import load_token, make_server
from gateway.service import Gateway
from handlers import build_registry
from core.lifecycle import start_control


def main() -> None:
    registry = build_registry()
    gateway = Gateway(registry, config.PROJECT_ROOT/'data/gateway.sqlite3')
    token = load_token(config.PROJECT_ROOT/'data/gateway.token')
    server = make_server(('127.0.0.1', 8787), gateway, token, config.WORKER_THREADS)
    drained = threading.Event()

    class Control:
        def shutdown(self) -> None:
            server.shutdown()
            drained.wait()

    start_control(Control(), config.PROJECT_ROOT/'data', 'gateway-process.json')
    print('Gateway listening: http://127.0.0.1:8787', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        registry.shutdown()
        drained.set()


if __name__ == '__main__':
    main()
