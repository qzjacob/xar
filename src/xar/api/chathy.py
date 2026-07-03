"""HTTP surface for Chathy — session CRUD + the streaming chat endpoint.

The chat endpoint streams Server-Sent Events (`data: {json}\\n\\n`), one per agent event
(delta / tool_start / tool_result / done / error), so the browser renders tokens and tool
activity live. Session CRUD is plain JSON.
"""
from __future__ import annotations

import json
from collections.abc import Iterator

from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from ..chathy import agent, sessions


def create_session(title: str | None = None) -> dict:
    return sessions.create(title)


def list_sessions() -> list[dict]:
    return sessions.list_sessions()


def get_messages(session_id: str) -> list[dict]:
    if not sessions.exists(session_id):
        raise HTTPException(status_code=404, detail="session not found")
    return sessions.messages(session_id)


def delete_session(session_id: str) -> dict:
    return {"deleted": sessions.delete(session_id)}


def _sse(events: Iterator[dict]) -> Iterator[str]:
    for ev in events:
        yield f"data: {json.dumps(ev, ensure_ascii=False, default=str)}\n\n"


def chat_stream(session_id: str, message: str) -> StreamingResponse:
    if not sessions.exists(session_id):
        raise HTTPException(status_code=404, detail="session not found")
    if not (message or "").strip():
        raise HTTPException(status_code=400, detail="empty message")

    def gen() -> Iterator[str]:
        try:
            yield from _sse(agent.run_turn(session_id, message))
        except Exception as e:  # noqa: BLE001 - surface as a terminal SSE error, never 500 mid-stream
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                                      "Connection": "keep-alive"})
