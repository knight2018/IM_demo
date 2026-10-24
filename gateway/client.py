"""供各 IM 桥接复用的同步流式客户端，不继承系统代理。"""

from collections.abc import Callable
import httpx
from gateway.protocol import IncomingMessage, ReplyEvent


class GatewayClient:
    def __init__(self, url: str, token: str, timeout: float = 300) -> None:
        self.url, self.token, self.timeout = url.rstrip('/'), token, timeout

    def reply(self, message: IncomingMessage,
              on_update: Callable[[str], None] | None = None) -> str:
        with httpx.Client(trust_env=False, timeout=self.timeout) as client:
            with client.stream('POST', self.url+'/v1/messages', json=message.model_dump(),
                               headers={'Authorization': 'Bearer '+self.token}) as response:
                if response.status_code != 200:
                    raise RuntimeError(f'网关请求失败（HTTP {response.status_code}）')
                for line in response.iter_lines():
                    if not line:
                        continue
                    event = ReplyEvent.model_validate_json(line)
                    if event.type == 'snapshot' and on_update:
                        on_update(event.text)
                    elif event.type == 'completed':
                        return event.text
                    elif event.type == 'error':
                        raise RuntimeError(event.text)
        raise RuntimeError('网关连接提前结束，未收到完成事件')
