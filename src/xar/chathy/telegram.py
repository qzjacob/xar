"""Chathy 的 Telegram 通道 — 长轮询 bot,复用与前端完全相同的会话/消息/日志管线。

核心原则:**零旁路**。每条 Telegram 消息走 `agent.run_turn(session_id, text)` —— 与
`/api/chathy/sessions/{id}/chat` 同一个入口,用户/助手/工具消息全部落到同一份
`chat_sessions`/`chat_messages`,前端页面能直接看到 bot 会话的完整记录;LLM 计费与
`xar.chathy.agent` 日志也天然一致。

设计选择:
  * **getUpdates 长轮询**(25s),不用 webhook —— 部署无需公网 HTTPS 回调。
  * **ack-before-process**:先用 offset 确认再处理,崩溃时宁可丢一条也不重复烧 LLM。
  * chat_id ↔ session 的持久映射在 `chat_channels` 表;`/new` 重开会话。
  * 白名单 `TELEGRAM_ALLOWED_CHATS`(逗号分隔);留空=不限,但每个新 chat 首次进线
    在日志打出 chat id,便于事后收紧。
  * 回复用 Markdown 尝试、400 时退回纯文本;超过 4096 按行分块。
  * HTTP 传输可注入(`transport`),全部逻辑可离线测试(house 惯例,同 FMP getter)。
"""
from __future__ import annotations

import json
import threading
import time
import urllib.parse
import urllib.request
from collections.abc import Callable

from ..config import get_settings
from ..logging import get_logger

log = get_logger("xar.chathy.telegram")

_API = "https://api.telegram.org"
_POLL_TIMEOUT = 25          # getUpdates 服务端挂起秒数
_SEND_LIMIT = 4096          # Telegram 单条消息上限
_CHUNK_AT = 3900            # 留余量,按换行切
_MAX_TURN_CHARS = 4000      # 单条进线文本上限(防粘贴超长文档打爆一轮)

_WELCOME = (
    "你好,我是 Chathy —— XAR 产业链投研终端的对话分析师。\n"
    "直接提问即可(公司/产业链/催化剂/市场状态…),我会调用平台工具作答。\n"
    "命令: /new 开新会话 · /id 查看本 chat id"
)


def _default_transport(method: str, payload: dict, token: str, timeout: float) -> dict:
    """POST api.telegram.org/bot<token>/<method>;返回解析后的 JSON(错误也返回 dict)。"""
    url = f"{_API}/bot{token}/{method}"
    data = urllib.parse.urlencode(payload).encode()
    req = urllib.request.Request(url, data=data)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:  # Telegram 把业务错误编码在 4xx body 里
        try:
            return json.loads(e.read().decode())
        except Exception:  # noqa: BLE001
            return {"ok": False, "description": f"HTTP {e.code}"}


class TelegramBot:
    """一个 bot 实例:轮询、路由到 Chathy、回发。transport 可注入以离线测试。"""

    def __init__(self, token: str | None = None, *,
                 allowed_chats: set[str] | None = None,
                 transport: Callable[[str, dict, str, float], dict] | None = None) -> None:
        s = get_settings()
        self.token = token or s.telegram_bot_token
        if allowed_chats is not None:
            self.allowed = allowed_chats
        else:
            raw = (s.telegram_allowed_chats or "").strip()
            self.allowed = {c.strip() for c in raw.split(",") if c.strip()} if raw else set()
        self._transport = transport or _default_transport
        self._offset = 0
        self._seen_chats: set[str] = set()

    # --- telegram api -----------------------------------------------------------
    def api(self, method: str, payload: dict, timeout: float = 35.0) -> dict:
        return self._transport(method, payload, self.token, timeout)

    def send(self, chat_id: str, text: str) -> None:
        """分块发送;Markdown 解析失败(Telegram 对半截实体报 400)则退回纯文本。"""
        for chunk in _split(text or "(空回复)"):
            r = self.api("sendMessage", {"chat_id": chat_id, "text": chunk,
                                         "parse_mode": "Markdown"})
            if not r.get("ok"):
                r2 = self.api("sendMessage", {"chat_id": chat_id, "text": chunk})
                if not r2.get("ok"):
                    log.warning("telegram send failed chat=%s: %s", chat_id,
                                r2.get("description"))

    def _typing(self, chat_id: str) -> None:
        self.api("sendChatAction", {"chat_id": chat_id, "action": "typing"}, timeout=10.0)

    # --- session mapping ---------------------------------------------------------
    def _session_for(self, chat_id: str, *, fresh: bool = False) -> str:
        from ..storage import db
        from . import sessions

        if not fresh:
            rows = db.query(
                "SELECT c.session_id FROM chat_channels c JOIN chat_sessions s "
                "ON s.id = c.session_id WHERE c.channel='telegram' AND c.external_id=%s",
                (chat_id,))
            if rows:
                return rows[0]["session_id"]
        sid = sessions.create(title=f"Telegram · {chat_id}")["id"]
        db.execute(
            "INSERT INTO chat_channels(channel, external_id, session_id) "
            "VALUES('telegram', %s, %s) "
            "ON CONFLICT (channel, external_id) DO UPDATE SET session_id=EXCLUDED.session_id, "
            "created_at=now()",
            (chat_id, sid))
        return sid

    # --- one message -------------------------------------------------------------
    def handle_text(self, chat_id: str, text: str) -> None:
        text = (text or "").strip()
        if not text:
            return
        if self.allowed and chat_id not in self.allowed:
            log.warning("telegram: rejected chat %s (not in TELEGRAM_ALLOWED_CHATS)", chat_id)
            self.send(chat_id, "此 bot 为私有部署,未授权的会话。")
            return
        if chat_id not in self._seen_chats:
            self._seen_chats.add(chat_id)
            log.info("telegram: chat %s active (allowlist %s)",
                     chat_id, "on" if self.allowed else "OFF — set TELEGRAM_ALLOWED_CHATS to restrict")

        if text.startswith("/start"):
            self._session_for(chat_id)
            self.send(chat_id, _WELCOME)
            return
        if text.startswith("/new"):
            self._session_for(chat_id, fresh=True)
            self.send(chat_id, "已开新会话。")
            return
        if text.startswith("/id"):
            self.send(chat_id, f"chat id: `{chat_id}`")
            return
        if len(text) > _MAX_TURN_CHARS:
            self.send(chat_id, f"消息过长(>{_MAX_TURN_CHARS} 字),请精简后再发。")
            return

        from . import agent  # 延迟导入,避免模块加载即拉起 LLM 栈

        sid = self._session_for(chat_id)
        self._typing(chat_id)
        buf: list[str] = []
        last_typing = time.monotonic()
        try:
            for ev in agent.run_turn(sid, text):
                kind = ev.get("type")
                if kind == "delta":
                    buf.append(ev.get("text") or "")
                elif kind == "tool_start":
                    # 与前端一致:工具活动不单发消息,仅维持 typing 指示
                    if time.monotonic() - last_typing > 5:
                        self._typing(chat_id)
                        last_typing = time.monotonic()
                elif kind == "error":
                    self.send(chat_id, f"⚠️ {ev.get('message')}")
                    return
        except Exception as e:  # noqa: BLE001 — 单条消息失败不拖垮轮询循环
            log.warning("telegram turn failed chat=%s: %s", chat_id, e)
            self.send(chat_id, f"⚠️ 处理失败: {e}")
            return
        self.send(chat_id, "".join(buf).strip() or "(无内容)")

    # --- poll loop ----------------------------------------------------------------
    def poll_once(self) -> int:
        """一轮 getUpdates:先 ack(offset 前移)再处理。返回处理的消息数。"""
        r = self.api("getUpdates", {"offset": self._offset, "timeout": _POLL_TIMEOUT,
                                    "allowed_updates": json.dumps(["message"])},
                     timeout=_POLL_TIMEOUT + 10)
        if not r.get("ok"):
            log.warning("telegram getUpdates failed: %s", r.get("description"))
            time.sleep(3)
            return 0
        updates = r.get("result") or []
        if not updates:
            return 0
        # ack-before-process:宁可崩溃丢一批,不重复处理烧 LLM
        self._offset = max(u["update_id"] for u in updates) + 1
        n = 0
        for u in updates:
            msg = u.get("message") or {}
            chat = msg.get("chat") or {}
            chat_id = str(chat.get("id", "")).strip()
            text = msg.get("text")
            if chat_id and text:
                n += 1
                self.handle_text(chat_id, text)
        return n

    def run_forever(self) -> None:
        me = self.api("getMe", {}, timeout=15.0)
        if not me.get("ok"):
            log.warning("telegram getMe failed (token bad / network?): %s", me.get("description"))
        else:
            log.info("telegram bot @%s polling started (allowlist: %s)",
                     (me.get("result") or {}).get("username", "?"),
                     ",".join(sorted(self.allowed)) or "OFF")
        while True:
            try:
                self.poll_once()
            except Exception as e:  # noqa: BLE001 — 轮询循环永不死
                log.warning("telegram poll error: %s", e)
                time.sleep(5)


def _split(text: str) -> list[str]:
    """按 Telegram 4096 限制分块,优先在换行处切。"""
    if len(text) <= _SEND_LIMIT:
        return [text]
    out: list[str] = []
    rest = text
    while len(rest) > _CHUNK_AT:
        cut = rest.rfind("\n", 0, _CHUNK_AT)
        if cut < _CHUNK_AT // 2:
            cut = _CHUNK_AT
        out.append(rest[:cut])
        rest = rest[cut:].lstrip("\n")
    if rest:
        out.append(rest)
    return out


_started = threading.Event()


def start_background() -> bool:
    """app 启动钩子:token 在场且未显式关闭时,拉起守护轮询线程(幂等)。"""
    s = get_settings()
    if not s.telegram_bot_token or not s.enable_telegram:
        log.info("telegram channel off (token %s, enable=%s)",
                 "set" if s.telegram_bot_token else "unset", s.enable_telegram)
        return False
    if _started.is_set():
        return True
    _started.set()

    def _run() -> None:
        while True:  # 外层自愈:run_forever 内部已兜底,这里防御构造期异常
            try:
                TelegramBot().run_forever()
            except Exception as e:  # noqa: BLE001
                log.warning("telegram bot crashed, restarting in 15s: %s", e)
                time.sleep(15)

    threading.Thread(target=_run, name="chathy-telegram", daemon=True).start()
    log.info("telegram channel armed (bot=%s)", s.telegram_bot_id or "?")
    return True
