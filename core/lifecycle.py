"""本机停止信号：等待正在处理的回复发送完毕后再结束进程。"""

import json
import os
import threading
import uuid
from collections.abc import Callable
from pathlib import Path

from typing import Protocol


class StopTarget(Protocol):
    def shutdown(self) -> None: ...


def watch_stop(request_file: Path, registry: StopTarget,
               exit_process: Callable[[int], None] = os._exit,
               wait: Callable[[float], object] = threading.Event().wait) -> None:
    while not request_file.exists():
        wait(0.5)
    registry.shutdown()
    request_file.unlink(missing_ok=True)
    # 所有回复任务已经结束；显式退出关闭 SDK 的阻塞长连接，不调用其私有方法。
    print('[lifecycle] graceful stop completed', flush=True)
    exit_process(0)


def start_control(registry: StopTarget, directory: Path, record_name: str = 'feishu-bot-process.json') -> None:
    directory.mkdir(parents=True, exist_ok=True)
    name = f'stop-{os.getpid()}-{uuid.uuid4().hex}.request'
    record = {'pid': os.getpid(), 'stop_file': name}
    (directory/record_name).write_text(json.dumps(record), encoding='utf-8')
    threading.Thread(target=watch_stop, args=(directory/name, registry),
                     name='graceful-stop', daemon=True).start()
