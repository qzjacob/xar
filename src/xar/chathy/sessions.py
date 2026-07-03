"""Chathy chat persistence — `chat_sessions` + `chat_messages` (see storage/schema.sql).

Messages are stored in the OpenAI wire shape (role/content + optional tool_calls /
tool_call_id / name) so a session round-trips straight back into `complete_stream`.
"""
from __future__ import annotations

import json
import uuid

from ..storage import db


def create(title: str | None = None) -> dict:
    sid = uuid.uuid4().hex[:16]
    db.execute("INSERT INTO chat_sessions(id, title) VALUES(%s, %s)", (sid, title))
    return {"id": sid, "title": title}


def list_sessions(limit: int = 50) -> list[dict]:
    return db.query(
        "SELECT id, title, created_at, updated_at, "
        "(SELECT count(*) FROM chat_messages m WHERE m.session_id=s.id) AS n_messages "
        "FROM chat_sessions s ORDER BY updated_at DESC LIMIT %s", (limit,))


def exists(session_id: str) -> bool:
    return bool(db.query("SELECT 1 FROM chat_sessions WHERE id=%s", (session_id,)))


def delete(session_id: str) -> bool:
    return bool(db.query("DELETE FROM chat_sessions WHERE id=%s RETURNING id", (session_id,)))


def touch(session_id: str, *, title: str | None = None) -> None:
    if title is not None:
        db.execute("UPDATE chat_sessions SET updated_at=now(), title=COALESCE(title, %s) "
                   "WHERE id=%s", (title, session_id))
    else:
        db.execute("UPDATE chat_sessions SET updated_at=now() WHERE id=%s", (session_id,))


def append(session_id: str, *, role: str, content: str | None = None,
           tool_calls: list | None = None, tool_call_id: str | None = None,
           name: str | None = None, usage: dict | None = None) -> None:
    db.execute(
        "INSERT INTO chat_messages(session_id, role, content, tool_calls, tool_call_id, name, usage) "
        "VALUES(%s, %s, %s, %s::jsonb, %s, %s, %s::jsonb)",
        (session_id, role, content,
         json.dumps(tool_calls) if tool_calls is not None else None,
         tool_call_id, name,
         json.dumps(usage) if usage is not None else None),
    )


def messages(session_id: str) -> list[dict]:
    """Full stored log (includes tool traffic) — for the UI transcript."""
    rows = db.query(
        "SELECT role, content, tool_calls, tool_call_id, name, usage, created_at "
        "FROM chat_messages WHERE session_id=%s ORDER BY id", (session_id,))
    return rows


def history_for_llm(session_id: str) -> list[dict]:
    """The stored log reshaped into OpenAI-style messages for `complete_stream`."""
    out: list[dict] = []
    for r in messages(session_id):
        role = r["role"]
        if role == "tool":
            out.append({"role": "tool", "tool_call_id": r["tool_call_id"],
                        "name": r["name"], "content": r["content"] or ""})
        elif role == "assistant":
            m: dict = {"role": "assistant", "content": r["content"] or ""}
            if r["tool_calls"]:
                m["tool_calls"] = r["tool_calls"]
            out.append(m)
        else:  # user / system
            out.append({"role": role, "content": r["content"] or ""})
    return out
