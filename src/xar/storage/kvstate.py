"""共享的小型持久 key/value 状态(JSONB)—— 常驻工人额度状态、回填游标、节拍等。

单一实现取代 glm_worker/history 各自的私有拷贝:DDL 的权威在 schema.sql
(glm_worker_state 表);这里仅保留一次性的防御式建表(进程内只执行一次),
供 `xar init` 之前的裸 CLI 调用兜底。
"""
from __future__ import annotations

import json

from . import db

_DDL = ("CREATE TABLE IF NOT EXISTS glm_worker_state ("
        "key TEXT PRIMARY KEY, value JSONB NOT NULL, "
        "updated_at TIMESTAMPTZ NOT NULL DEFAULT now())")
_ensured = False


def _ensure() -> None:
    global _ensured
    if not _ensured:
        db.execute(_DDL)
        _ensured = True


def get_state(key: str, default: dict | None = None) -> dict:
    _ensure()
    rows = db.query("SELECT value FROM glm_worker_state WHERE key=%s", (key,))
    return rows[0]["value"] if rows else (default if default is not None else {})


def save_state(key: str, value: dict) -> None:
    _ensure()
    db.execute("INSERT INTO glm_worker_state(key, value, updated_at) "
               "VALUES (%s, %s::jsonb, now()) "
               "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=now()",
               (key, json.dumps(value, ensure_ascii=False, default=str)))


def delete_state(key: str) -> None:
    _ensure()
    db.execute("DELETE FROM glm_worker_state WHERE key=%s", (key,))
