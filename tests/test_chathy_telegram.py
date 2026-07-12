"""Chathy Telegram 通道 — 离线测试(注入 transport;不触网)。

覆盖:命令路由、白名单、chat↔session 持久映射(与前端同一 chat_sessions 表)、
delta 聚合回发、长文分块、ack-before-process 的 offset 前移。
"""
from __future__ import annotations


from xar.chathy.telegram import TelegramBot, _split


class FakeTransport:
    """记录全部 API 调用;按 method 返回预置响应。"""

    def __init__(self, updates: list[dict] | None = None):
        self.calls: list[tuple[str, dict]] = []
        self.updates = updates or []

    def __call__(self, method: str, payload: dict, token: str, timeout: float) -> dict:
        self.calls.append((method, payload))
        if method == "getUpdates":
            out, self.updates = self.updates, []
            return {"ok": True, "result": out}
        if method == "getMe":
            return {"ok": True, "result": {"username": "test_bot"}}
        return {"ok": True, "result": {}}

    def sent_texts(self) -> list[str]:
        return [p["text"] for m, p in self.calls if m == "sendMessage"]


def _bot(transport, allowed=None):
    return TelegramBot(token="t0ken", allowed_chats=allowed, transport=transport)


def test_split_chunks_long_text_on_newlines():
    text = "\n".join(f"line {i} " + "x" * 80 for i in range(120))
    chunks = _split(text)
    assert len(chunks) > 1
    assert all(len(c) <= 4096 for c in chunks)
    assert "".join(c + "\n" for c in chunks).replace("\n\n", "\n").strip().startswith("line 0")


def test_commands_and_allowlist(monkeypatch, seeded_db):
    tr = FakeTransport()
    bot = _bot(tr, allowed={"111"})
    # 白名单外:拒绝,不建会话
    bot.handle_text("999", "hello")
    assert any("未授权" in t for t in tr.sent_texts())
    # 白名单内:/start 欢迎;/id 返回 chat id
    bot.handle_text("111", "/start")
    bot.handle_text("111", "/id")
    texts = tr.sent_texts()
    assert any("Chathy" in t for t in texts)
    assert any("111" in t for t in texts)


def test_chat_maps_to_persistent_session_and_new_resets(seeded_db):
    tr = FakeTransport()
    bot = _bot(tr)
    sid1 = bot._session_for("42")
    sid2 = bot._session_for("42")
    assert sid1 == sid2                      # 持久映射:同 chat 复用同会话
    sid3 = bot._session_for("42", fresh=True)
    assert sid3 != sid1                      # /new 重开
    assert bot._session_for("42") == sid3
    # 会话就在前端使用的 chat_sessions 表里(记录同源)
    rows = seeded_db.query("SELECT title FROM chat_sessions WHERE id=%s", (sid3,))
    assert rows and "Telegram" in (rows[0]["title"] or "")


def test_turn_streams_deltas_and_replies(monkeypatch, seeded_db):
    tr = FakeTransport()
    bot = _bot(tr)

    def fake_run_turn(sid, text):
        assert text == "NVDA 怎么样?"
        yield {"type": "delta", "text": "NVDA "}
        yield {"type": "tool_start", "id": "1", "name": "find_company", "args": {}}
        yield {"type": "tool_result", "id": "1", "name": "find_company", "preview": "..."}
        yield {"type": "delta", "text": "看起来不错。"}
        yield {"type": "done", "usage": {}}

    import xar.chathy.agent as agent
    monkeypatch.setattr(agent, "run_turn", fake_run_turn)
    bot.handle_text("77", "NVDA 怎么样?")
    texts = tr.sent_texts()
    assert texts and texts[-1] == "NVDA 看起来不错。"
    # typing indicator 至少发过一次
    assert any(m == "sendChatAction" for m, _ in tr.calls)


def test_turn_error_relayed_only_to_allowlisted(monkeypatch, seeded_db):
    def broken(sid, text):
        yield {"type": "error", "message": "quota exhausted"}

    import xar.chathy.agent as agent
    monkeypatch.setattr(agent, "run_turn", broken)
    # 白名单会话:转述细节(与 web 同显)
    tr1 = FakeTransport()
    _bot(tr1, allowed={"77"}).handle_text("77", "hi")
    assert any("quota exhausted" in t for t in tr1.sent_texts())
    # 开放会话:通用语,不外泄
    tr2 = FakeTransport()
    _bot(tr2).handle_text("77", "hi")
    assert not any("quota exhausted" in t for t in tr2.sent_texts())
    assert any("稍后重试" in t for t in tr2.sent_texts())


def test_exception_sends_generic_error_not_details(monkeypatch, seeded_db):
    def explode(sid, text):
        raise RuntimeError("postgresql://user:pass@db/secret dsn")
        yield  # pragma: no cover

    import xar.chathy.agent as agent
    monkeypatch.setattr(agent, "run_turn", explode)
    tr = FakeTransport()
    _bot(tr).handle_text("77", "hi")
    texts = tr.sent_texts()
    assert any("内部错误" in t for t in texts)
    assert not any("postgresql" in t for t in texts)   # 细节只进日志


def test_poll_persists_offset_before_processing(monkeypatch, seeded_db):
    updates = [
        {"update_id": 10, "message": {"chat": {"id": 5}, "text": "/id"}},
        {"update_id": 11, "message": {"chat": {"id": 5}, "text": "/id"}},
    ]
    tr = FakeTransport(updates=updates)
    bot = _bot(tr)
    n = bot.poll_once()
    assert n == 2
    assert bot._offset == 12                 # max(update_id)+1
    # 偏移已持久化:新实例(模拟重启)直接从 12 续读,不重放
    bot2 = _bot(FakeTransport())
    assert bot2._offset == 12
    assert bot.poll_once() == 0


def test_throttle_min_gap_and_exact_commands(monkeypatch, seeded_db):
    import xar.chathy.agent as agent

    def ok(sid, text):
        yield {"type": "delta", "text": "ok"}
        yield {"type": "done", "usage": {}}

    monkeypatch.setattr(agent, "run_turn", ok)
    tr = FakeTransport()
    bot = _bot(tr)
    bot.handle_text("9", "第一问")
    bot.handle_text("9", "紧跟着第二问")     # < 2s 间隔 → 节流
    texts = tr.sent_texts()
    assert any("太快" in t for t in texts)
    # 命令精确匹配:"/newport…" 是普通提问,不得吞成 /new
    tr2 = FakeTransport()
    bot2 = _bot(tr2)
    bot2.handle_text("10", "/newport analysis?")
    assert not any("已开新会话" in t for t in tr2.sent_texts())


def test_markdown_fallback_to_plain(monkeypatch, seeded_db):
    class Failing(FakeTransport):
        def __call__(self, method, payload, token, timeout):
            self.calls.append((method, payload))
            if method == "sendMessage" and payload.get("parse_mode") == "Markdown":
                return {"ok": False, "description": "can't parse entities"}
            return {"ok": True, "result": {}}

    tr = Failing()
    bot = _bot(tr)
    bot.send("5", "some _broken markdown")
    sends = [(m, p) for m, p in tr.calls if m == "sendMessage"]
    assert len(sends) == 2                    # Markdown 尝试 + 纯文本回退
    assert "parse_mode" not in sends[1][1]
