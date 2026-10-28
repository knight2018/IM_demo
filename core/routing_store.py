"""按会话和用户保存通道选择、模型及跨通道的最近对话。"""

import hashlib
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TypedDict

Mode = Literal['api', 'codex', 'auto']
Backend = Literal['api', 'codex']


@dataclass(frozen=True)
class ModelSettings:
    mode: Mode
    api_model: str
    codex_model: str
    tavily_enabled: bool = True


class ChatTurn(TypedDict):
    user: str
    assistant: str


class RoutingStore:
    def __init__(self, path: Path, defaults: ModelSettings) -> None:
        self.path, self.defaults = path, defaults
        path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(path) as db:
            db.execute('CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, mode TEXT, api_model TEXT, codex_model TEXT)')
            # 老数据库仅追加开关字段，保留所有已有会话设置和历史。
            if 'tavily_enabled' not in {row[1] for row in db.execute('PRAGMA table_info(settings)')}:
                db.execute('ALTER TABLE settings ADD COLUMN tavily_enabled INTEGER NOT NULL DEFAULT 1')
            db.execute('CREATE TABLE IF NOT EXISTS turns (id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT, user TEXT, assistant TEXT)')

    @staticmethod
    def key(conversation: str) -> str:
        return hashlib.sha256(conversation.encode('utf-8')).hexdigest()

    def get(self, conversation: str) -> ModelSettings:
        with sqlite3.connect(self.path) as db:
            row = db.execute('SELECT mode,api_model,codex_model,tavily_enabled FROM settings WHERE key=?', (self.key(conversation),)).fetchone()
        if row is None:
            return self.defaults
        if row[0] not in {'api', 'codex', 'auto'}:
            raise ValueError('保存的通道设置无效')
        return ModelSettings(*row[:3], tavily_enabled=bool(row[3]))

    def save(self, conversation: str, settings: ModelSettings) -> None:
        with sqlite3.connect(self.path) as db:
            db.execute('INSERT INTO settings (key,mode,api_model,codex_model,tavily_enabled) VALUES (?,?,?,?,?) ON CONFLICT(key) DO UPDATE SET '
                       'mode=excluded.mode,api_model=excluded.api_model,codex_model=excluded.codex_model, '
                       'tavily_enabled=excluded.tavily_enabled',
                       (self.key(conversation), settings.mode, settings.api_model, settings.codex_model, int(settings.tavily_enabled)))

    def history(self, conversation: str) -> list[ChatTurn]:
        with sqlite3.connect(self.path) as db:
            rows = db.execute('SELECT user,assistant FROM turns WHERE key=? ORDER BY id DESC LIMIT 12',
                              (self.key(conversation),)).fetchall()
        return [ChatTurn(user=row[0], assistant=row[1]) for row in reversed(rows)]

    def append(self, conversation: str, user: str, assistant: str) -> None:
        with sqlite3.connect(self.path) as db:
            db.execute('INSERT INTO turns (key,user,assistant) VALUES (?,?,?)',
                       (self.key(conversation), user, assistant))
