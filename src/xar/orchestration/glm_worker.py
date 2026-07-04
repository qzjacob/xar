"""GLM 订阅额度感知的常驻抽取工人 —— 对 Coding Plan 的机制化利用。

前提:GLM Coding Plan(glm-5.2-sub)按订阅计费,**没有账单超额风险**,但受
5 小时窗/周额度限制;额度耗尽表现为 RateLimitError(余额不足/无可用资源包),
窗口滚动后自动恢复。

因此正确的形状是一个**常驻循环**,而非固定 cron:
  1. 每轮先做零 LLM 的工作(语义源增量拉取 Twitter/微信/Finnhub、10 年历史回填
     推进、本地嵌入解析)——额度耗尽也不停;
  2. LLM 抽取阶段前先发一次极小探针;通过 → 在 llm.pinned(GLM_PIN) 内跑
     build_kg + expert 批次(**钉扎到订阅模型,链外零回退**——额度耗尽=等待,
     绝不落到按 token 计费的模型);失败 → 记录 exhausted,进入探针节奏
     (默认 15 分钟)直至额度恢复,随即自动重启抽取;
  3. 全部阶段幂等(pending 游标 / 内容哈希去重 / ON CONFLICT),崩溃即续。

状态持久化在 glm_worker_state(key/value JSONB);`xar glm-worker status` 可视;
docker compose 的 `glmworker` 常驻服务(restart: unless-stopped)即"自动定时任务"。
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone

from ..logging import get_logger
from ..models import llm
from ..storage import db

log = get_logger("xar.glm_worker")

# 钉扎链:只允许 GLM 订阅池(5.2 优先,4.6 兜底)。没有 kimi/deepseek —— 那是
# 夜批的回退语义;本工人的存在意义就是"额度内白嫖到底,额度外分文不花"。
GLM_PIN: tuple[str, ...] = ("glm-5.2-sub", "glm-4.6-sub")

_QUOTA_MARKERS = ("余额不足", "无可用资源包", "rate limit", "ratelimit", "quota",
                  "too many requests", "429", "exceed")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _ensure_table() -> None:
    db.execute("CREATE TABLE IF NOT EXISTS glm_worker_state ("
               "key TEXT PRIMARY KEY, value JSONB NOT NULL, "
               "updated_at TIMESTAMPTZ NOT NULL DEFAULT now())")


def get_state(key: str, default: dict | None = None) -> dict:
    _ensure_table()
    rows = db.query("SELECT value FROM glm_worker_state WHERE key=%s", (key,))
    return rows[0]["value"] if rows else (default or {})


def save_state(key: str, value: dict) -> None:
    _ensure_table()
    db.execute("INSERT INTO glm_worker_state(key, value, updated_at) "
               "VALUES (%s, %s::jsonb, now()) "
               "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=now()",
               (key, json.dumps(value, ensure_ascii=False, default=str)))


def is_quota_error(e: Exception) -> bool:
    if type(e).__name__ in ("RateLimitError",):
        return True
    msg = str(e).lower()
    return any(m in msg or m in str(e) for m in _QUOTA_MARKERS)


# ── 额度治理器 ─────────────────────────────────────────────────────────────────
def probe() -> bool:
    """极小探针(≤8 token,订阅计费=0 成本):GLM 池当前是否可用。"""
    try:
        with llm.pinned(GLM_PIN):
            llm.complete("reply: ok", task="adhoc_fast", node="glm_worker", max_tokens=8)
        return True
    except Exception as e:  # noqa: BLE001
        if is_quota_error(e):
            log.info("glm quota probe: exhausted (%s)", str(e)[:100])
        else:
            log.warning("glm quota probe non-quota failure: %s", str(e)[:160])
        return False


def _mark_exhausted(q: dict, reason: str) -> dict:
    if q.get("status") != "exhausted":
        q.update({"status": "exhausted", "exhausted_at": _now(),
                  "exhaust_count": int(q.get("exhaust_count", 0)) + 1,
                  "last_reason": reason[:200]})
        log.warning("GLM quota EXHAUSTED — worker enters probe mode (%s)", reason[:120])
    q["last_probe_at"] = _now()
    save_state("quota", q)
    return q


def _mark_ok(q: dict) -> dict:
    if q.get("status") == "exhausted":
        q["resumed_at"] = _now()
        q["resume_count"] = int(q.get("resume_count", 0)) + 1
        log.info("GLM quota RECOVERED — extraction resumes automatically")
    q.update({"status": "ok", "last_probe_at": _now()})
    save_state("quota", q)
    return q


# ── 工作阶段 ──────────────────────────────────────────────────────────────────
def _due(key: str, every_seconds: int) -> bool:
    st = get_state("cadence")
    last = st.get(key)
    if last:
        try:
            prev = datetime.fromisoformat(last)
            if (datetime.now(timezone.utc) - prev).total_seconds() < every_seconds:
                return False
        except ValueError:
            pass
    st[key] = _now()
    save_state("cadence", st)
    return True


def _pull_fresh() -> dict:
    """语义源增量拉取(零 LLM):Twitter 专家声音 / 微信公众号 / Finnhub 新闻。
    各自带节拍(不逐轮硬打源);单源失败不沉轮。"""
    out: dict = {}
    if _due("twitter", 3600):
        try:
            from ..providers import twitter

            out["twitter"] = twitter.pull()
        except Exception as e:  # noqa: BLE001
            out["twitter"] = {"error": str(e)[:120]}
    if _due("wechat", 3600):
        try:
            from ..ingestion import ingest_wechat, wechat

            if wechat.available():
                out["wechat"] = {"docs": len(ingest_wechat())}
        except Exception as e:  # noqa: BLE001
            out["wechat"] = {"error": str(e)[:120]}
    if _due("finnhub_news", 4 * 3600):
        try:
            from datetime import date, timedelta

            from ..providers import finnhub

            out["finnhub_news"] = finnhub.pull_news_basket(since=date.today() - timedelta(days=2))
        except Exception as e:  # noqa: BLE001
            out["finnhub_news"] = {"error": str(e)[:120]}
    if _due("rss", 2 * 3600):
        try:
            from ..providers import rss

            out["rss"] = {"docs": rss.pull()}
        except Exception as e:  # noqa: BLE001
            out["rss"] = {"error": str(e)[:120]}
    return out


def _backfill(units: int) -> dict:
    """10 年历史回填推进(零 LLM;游标断点续走)。"""
    try:
        from ..ingestion import history

        return history.backfill_step(units=units)
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)[:160]}


def _llm_stage(batch_docs: int, q: dict) -> tuple[dict, dict]:
    """钉扎 GLM 的抽取批次:KG 语义抽取 + 专家洞见。额度中途耗尽 → 单文档失败被
    build_kg 各自吞掉,pending 游标保证下轮重试;本轮结束后由下一次探针定性。"""
    from ..kg import expert
    from ..kg import extract as kg_extract

    out: dict = {}
    run_id = llm.new_batch_run_id("kg")
    try:
        with llm.pinned(GLM_PIN):
            out["kg"] = kg_extract.build_kg(limit=batch_docs, run_id=run_id)
            out["expert"] = expert.process(limit=max(batch_docs // 2, 5), run_id=run_id)
    except Exception as e:  # noqa: BLE001
        out["aborted"] = str(e)[:160]
        if is_quota_error(e):
            q = _mark_exhausted(q, str(e))
    return out, q


def run_once(*, batch_docs: int | None = None, backfill_units: int | None = None) -> dict:
    """单轮:非 LLM 工作(拉取/回填/解析)必做;LLM 抽取按探针结果做或跳。"""
    from ..config import get_settings
    from ..parsing import parse

    s = get_settings()
    batch_docs = batch_docs or s.glm_worker_batch_docs
    backfill_units = backfill_units if backfill_units is not None else s.glm_worker_backfill_units
    q = get_state("quota", {"status": "ok"})
    out: dict = {"ts": _now()}

    out["pulls"] = _pull_fresh()
    out["backfill"] = _backfill(backfill_units)
    try:
        out["parsed_chunks"] = parse.parse_pending(limit=200)   # 本地嵌入,零 LLM
    except Exception as e:  # noqa: BLE001
        out["parsed_chunks"] = {"error": str(e)[:120]}

    if probe():
        q = _mark_ok(q)
        out["extract"], q = _llm_stage(batch_docs, q)
    else:
        q = _mark_exhausted(q, "probe failed")
        out["extract"] = {"skipped": "quota exhausted — waiting for window reset"}

    counters = get_state("counters")
    counters["cycles"] = int(counters.get("cycles", 0)) + 1
    kg_stats = (out.get("extract") or {}).get("kg") or {}
    counters["docs_extracted"] = int(counters.get("docs_extracted", 0)) + int(
        kg_stats.get("docs", kg_stats.get("processed", 0)) or 0)
    counters["last_cycle_at"] = _now()
    save_state("counters", counters)
    out["quota"] = q["status"]
    return out


def run_daemon() -> None:
    """常驻循环:额度可用 → 每 cycle_seconds 一轮;耗尽 → 每 probe_seconds 一探。
    SIGTERM/Ctrl-C 干净退出;所有阶段幂等,重启即续。"""
    from ..config import get_settings

    s = get_settings()
    log.info("glm-worker daemon up: pin=%s cycle=%ss probe=%ss batch=%s",
             ",".join(GLM_PIN), s.glm_worker_cycle_seconds, s.glm_worker_probe_seconds,
             s.glm_worker_batch_docs)
    while True:
        try:
            out = run_once()
            log.info("cycle done: quota=%s extract=%s backfill=%s",
                     out.get("quota"),
                     json.dumps(out.get("extract", {}), ensure_ascii=False, default=str)[:200],
                     json.dumps(out.get("backfill", {}), ensure_ascii=False, default=str)[:160])
            exhausted = out.get("quota") == "exhausted"
        except KeyboardInterrupt:
            log.info("glm-worker interrupted — exiting cleanly")
            return
        except Exception as e:  # noqa: BLE001 — 常驻进程绝不因单轮异常退出
            log.warning("cycle failed: %s", e)
            exhausted = False
        time.sleep(s.glm_worker_probe_seconds if exhausted else s.glm_worker_cycle_seconds)


def status() -> dict:
    """CLI/看板口径:额度状态 + 计数器 + 回填游标 + 待抽取积压。"""
    pending = db.query(
        "SELECT count(*) AS n FROM documents d WHERE EXISTS "
        "(SELECT 1 FROM chunks c WHERE c.doc_id=d.id) AND NOT EXISTS "
        "(SELECT 1 FROM kg_events e WHERE e.source_doc_id=d.id)")
    backlog = int(pending[0]["n"]) if pending else None
    try:
        from ..ingestion import history

        backfill = history.backfill_status()
    except Exception as e:  # noqa: BLE001
        backfill = {"error": str(e)[:120]}
    return {"quota": get_state("quota", {"status": "unknown"}),
            "counters": get_state("counters"),
            "cadence": get_state("cadence"),
            "backfill": backfill,
            "extraction_backlog_docs": backlog,
            "pin": list(GLM_PIN)}
