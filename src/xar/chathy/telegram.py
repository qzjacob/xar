"""Chathy 的 Telegram 通道 — 长轮询 bot,复用与前端完全相同的会话/消息/日志管线。

核心原则:**零旁路**。每条 Telegram 消息走 `agent.run_turn(session_id, text)` —— 与
`/api/chathy/sessions/{id}/chat` 同一个入口,用户/助手/工具消息全部落到同一份
`chat_sessions`/`chat_messages`,前端页面能直接看到 bot 会话的完整记录;LLM 计费与
`xar.chathy.agent` 日志也天然一致。

设计选择(含对抗评审后的加固):
  * **getUpdates 长轮询**(25s),不用 webhook —— 部署无需公网 HTTPS 回调;启动时
    `deleteWebhook` 清掉历史注册,409(另一轮询者)退避 30s。
  * **偏移先落库再处理**(`channel_state` 表):崩溃/重发布后从持久化偏移续读,
    已处理的批次绝不重放(至多一次语义跨进程成立;宁丢不重,不重复烧 LLM)。
  * chat_id ↔ session 的持久映射在 `chat_channels`;`/new` 重开会话;web 端删除
    该会话 = 解绑,bot 下条消息自动重开(与 CASCADE 语义一致,预期行为)。
  * **节流**:每 chat 最小间隔 + 滚动小时上限,全局滚动日上限 —— 空白名单(私人
    bot)被陌生人发现时也烧不穿配额。
  * 异常**不外泄**:发给用户的只有通用错误语,细节只进日志;LLM error 事件仅对
    白名单会话转述。
  * 回复:429 按 retry_after 退避重试;仅解析类 400 才退纯文本;分块间 ~1s 步调。
  * HTTP 传输可注入(`transport`),全部逻辑可离线测试(house 惯例,同 FMP getter)。
"""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from collections.abc import Callable

from ..config import get_settings
from ..logging import get_logger

log = get_logger("xar.chathy.telegram")

_API = "https://api.telegram.org"
_POLL_TIMEOUT = 25          # getUpdates 服务端挂起秒数
_SEND_LIMIT = 4096          # Telegram 单条消息上限
_CHUNK_AT = 3900            # 留余量,按换行切
_MAX_TURN_CHARS = 4000      # 单条进线文本上限(防粘贴超长文档打爆一轮)
_MIN_GAP_S = 2.0            # 每 chat 两次提问的最小间隔
_CHAT_HOURLY = 30           # 每 chat 滚动一小时内的最大轮数
_GLOBAL_DAILY = 300         # 全局滚动 24h 的最大轮数(所有 chat 合计)

_WELCOME = (
    "你好,我是 Chathy —— XAR 产业链投研终端的对话分析师。\n"
    "直接提问即可(公司/产业链/催化剂/市场状态…),我会调用平台工具作答。\n"
    "命令: /new 开新会话 · /id 查看本 chat id"
)


def _default_transport(method: str, payload: dict, token: str, timeout: float) -> dict:
    """POST api.telegram.org/bot<token>/<method>;任何失败都返回 {"ok": False, ...},
    绝不向调用方抛异常(评审 #2:一次网络抖动不得炸穿轮询批次)。"""
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
            return {"ok": False, "error_code": e.code, "description": f"HTTP {e.code}"}
    except Exception as e:  # noqa: BLE001 — URLError/timeout/非 JSON body…
        return {"ok": False, "description": f"transport: {e}"}


class _Throttle:
    """内存节流:每 chat 最小间隔 + 滚动小时限,全局滚动日限。"""

    def __init__(self) -> None:
        self._last: dict[str, float] = {}
        self._hourly: dict[str, deque[float]] = {}
        self._daily: deque[float] = deque()

    def check(self, chat_id: str, now: float | None = None) -> str | None:
        """可通过返回 None,否则返回给用户的节流提示。通过即记账。"""
        t = time.monotonic() if now is None else now
        if t - self._last.get(chat_id, -1e9) < _MIN_GAP_S:
            return "问得太快了,请稍候几秒。"
        dq = self._hourly.setdefault(chat_id, deque())
        while dq and t - dq[0] > 3600:
            dq.popleft()
        if len(dq) >= _CHAT_HOURLY:
            return f"本会话已达每小时 {_CHAT_HOURLY} 轮上限,请稍后再试。"
        while self._daily and t - self._daily[0] > 86400:
            self._daily.popleft()
        if len(self._daily) >= _GLOBAL_DAILY:
            return "服务今日额度已用尽,请明天再试。"
        self._last[chat_id] = t
        dq.append(t)
        self._daily.append(t)
        return None


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
        self._offset = self._load_offset()
        self._seen_chats: set[str] = set()
        self._throttle = _Throttle()

    # --- offset persistence(评审 #1:重启不重放)-------------------------------
    def _load_offset(self) -> int:
        try:
            from ..storage import db
            rows = db.query("SELECT next_offset FROM channel_state WHERE channel='telegram'")
            return int(rows[0]["next_offset"]) if rows else 0
        except Exception:  # noqa: BLE001 — DB 未就绪:从 0 起(Telegram 只重发未确认的)
            return 0

    def _save_offset(self, offset: int) -> None:
        try:
            from ..storage import db
            db.execute(
                "INSERT INTO channel_state(channel, next_offset) VALUES('telegram', %s) "
                "ON CONFLICT (channel) DO UPDATE SET next_offset=EXCLUDED.next_offset, "
                "updated_at=now()", (offset,))
        except Exception as e:  # noqa: BLE001 — 落库失败退化为内存偏移,只影响重启语义
            log.warning("telegram offset persist failed: %s", e)

    # --- telegram api -----------------------------------------------------------
    def api(self, method: str, payload: dict, timeout: float = 35.0) -> dict:
        return self._transport(method, payload, self.token, timeout)

    def send(self, chat_id: str, text: str) -> None:
        """分块发送。429 按 retry_after 退避重试;仅解析类 400 退回纯文本;
        多块之间 ~1s 步调(Telegram 每 chat ≈1 msg/s)。"""
        chunks = _split(text or "(空回复)")
        for i, chunk in enumerate(chunks):
            if i:
                time.sleep(1.05)
            self._send_one(chat_id, chunk)

    def _send_one(self, chat_id: str, chunk: str) -> None:
        payload = {"chat_id": chat_id, "text": chunk, "parse_mode": "Markdown"}
        for _ in range(3):
            r = self.api("sendMessage", payload)
            if r.get("ok"):
                return
            if r.get("error_code") == 429:
                wait = float((r.get("parameters") or {}).get("retry_after", 3))
                time.sleep(min(wait, 30.0))
                continue
            desc = (r.get("description") or "").lower()
            if "parse" in desc and "parse_mode" in payload:
                payload = {"chat_id": chat_id, "text": chunk}   # 纯文本重试一次
                continue
            log.warning("telegram send failed chat=%s: %s", chat_id, r.get("description"))
            return
        log.warning("telegram send gave up chat=%s", chat_id)

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
        """整体兜底:任何异常只进日志 + 给用户通用错误语(评审 #7/#8),绝不外泄细节、
        绝不炸穿轮询线程。"""
        try:
            self._handle_text(chat_id, text)
        except Exception as e:  # noqa: BLE001
            log.warning("telegram message failed chat=%s: %s", chat_id, e, exc_info=True)
            try:
                self.send(chat_id, "⚠️ 内部错误,已记录日志,请稍后重试。")
            except Exception:  # noqa: BLE001
                pass

    def _handle_text(self, chat_id: str, text: str) -> None:
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

        # 命令按第一个词精确匹配(评审 #5:"/newport…" 不得吞成 /new);兼容 /cmd@BotName
        cmd = text.split()[0].split("@")[0]
        if cmd == "/start":
            self._session_for(chat_id)
            self.send(chat_id, _WELCOME)
            return
        if cmd == "/new":
            self._session_for(chat_id, fresh=True)
            self.send(chat_id, "已开新会话。")
            return
        if cmd == "/id":
            self.send(chat_id, f"chat id: `{chat_id}`")
            return
        if len(text) > _MAX_TURN_CHARS:
            self.send(chat_id, f"消息过长(>{_MAX_TURN_CHARS} 字),请精简后再发。")
            return
        deny = self._throttle.check(chat_id)
        if deny:
            self.send(chat_id, deny)
            return

        from . import agent  # 延迟导入,避免模块加载即拉起 LLM 栈

        sid = self._session_for(chat_id)
        self._typing(chat_id)
        buf: list[str] = []
        last_typing = time.monotonic()
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
                # LLM/工具错误事件:白名单会话转述(与 web 同显),开放会话给通用语
                msg = ev.get("message") if self.allowed else "服务暂时不可用,请稍后重试。"
                self.send(chat_id, f"⚠️ {msg}")
                return
        self.send(chat_id, "".join(buf).strip() or "(无内容)")

    # --- poll loop ----------------------------------------------------------------
    def poll_once(self) -> int:
        """一轮 getUpdates:偏移先落库(重启不重放)再逐条处理(逐条兜底)。"""
        r = self.api("getUpdates", {"offset": self._offset, "timeout": _POLL_TIMEOUT,
                                    "allowed_updates": json.dumps(["message"])},
                     timeout=_POLL_TIMEOUT + 10)
        if not r.get("ok"):
            if r.get("error_code") == 409:
                # webhook 已注册或另一个轮询者在跑:清 webhook + 长退避(评审 #3/#9)
                log.warning("telegram 409 (webhook/another poller); deleteWebhook + backoff 30s")
                self.api("deleteWebhook", {})
                time.sleep(30)
            else:
                log.warning("telegram getUpdates failed: %s", r.get("description"))
                time.sleep(3)
            return 0
        updates = r.get("result") or []
        if not updates:
            return 0
        # 先持久化新偏移:即使处理中途宕机,重启也从这里续读 —— 至多一次
        self._offset = max(u["update_id"] for u in updates) + 1
        self._save_offset(self._offset)
        # 按 chat 分组:组间并发(一个 chat 的长回合不再阻塞其他 chat),组内保序
        by_chat: dict[str, list[str]] = {}
        for u in updates:
            msg = u.get("message") or {}
            chat_id = str((msg.get("chat") or {}).get("id", "")).strip()
            text = msg.get("text")
            if chat_id and text:
                by_chat.setdefault(chat_id, []).append(text)
        if not by_chat:
            return 0

        def _drain(chat_id: str, texts: list[str]) -> None:
            for t in texts:
                self.handle_text(chat_id, t)   # 自带整体兜底

        if len(by_chat) == 1:
            (cid, texts), = by_chat.items()
            _drain(cid, texts)
        else:
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=min(3, len(by_chat))) as ex:
                list(ex.map(lambda kv: _drain(*kv), by_chat.items()))
        return sum(len(v) for v in by_chat.values())

    def run_forever(self) -> None:
        self.api("deleteWebhook", {})   # 长轮询与 webhook 互斥,先清历史注册
        me = self.api("getMe", {}, timeout=15.0)
        if not me.get("ok"):
            log.warning("telegram getMe failed (token bad / network?): %s", me.get("description"))
        else:
            log.info("telegram bot @%s polling started (allowlist: %s, offset: %s)",
                     (me.get("result") or {}).get("username", "?"),
                     ",".join(sorted(self.allowed)) or "OFF", self._offset)
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
    """app 启动钩子:token 在场且未显式关闭时,拉起守护轮询线程(进程内幂等)。
    注意:跨进程不去重 —— 不要在 `xar serve` 之外再跑 `xar telegram`(Telegram 会
    对并发 getUpdates 报 409,本实现以退避应对,但消息会在两个轮询者间跳动)。"""
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
