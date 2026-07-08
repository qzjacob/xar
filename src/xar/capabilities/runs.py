"""统一异步触发(UA-P1)—— capability_runs 表 + schedule/execute。

修复四种分裂的触发风格:所有 build/slow 能力经此一条道跑(BackgroundTasks/CLI/Chathy 共用),
run_id 可轮询。running 去重靠部分唯一索引 `uq_capruns_active`(同能力+同参活跃时唯一);
进程死亡遗孤(>30min running)在下次 schedule 时被收割成 error,放行新 run。
"""
from __future__ import annotations

import hashlib
import json
from uuid import uuid4

from ..logging import get_logger
from ..storage import db

log = get_logger("xar.capabilities.runs")

_STALE_SECONDS = 1800   # >30min 的 running = 进程死亡遗孤


def _args_hash(args: dict) -> str:
    return hashlib.sha256(json.dumps(args or {}, sort_keys=True, default=str).encode()).hexdigest()


def _active(name: str, h: str) -> dict | None:
    rows = db.query("SELECT id, status FROM capability_runs WHERE capability=%s AND args_hash=%s "
                    "AND status IN ('queued','running') ORDER BY created_at DESC LIMIT 1", (name, h))
    return rows[0] if rows else None


def schedule(name: str, args: dict | None = None, *, origin: str = "api") -> dict:
    """收割陈旧 running → 活跃去重(命中返回既有 run_id + dedup)→ INSERT queued。
    并发撞唯一索引 → 读回既有行。返回 {run_id, status[, dedup]}。"""
    args = args or {}
    # 收割:进程死掉遗留的 running 标 error,让新 run 能起
    db.execute("UPDATE capability_runs SET status='error', error='stale (reaped)', finished_at=now() "
               "WHERE status='running' AND started_at < now() - (%s || ' seconds')::interval",
               (_STALE_SECONDS,))
    h = _args_hash(args)
    hit = _active(name, h)
    if hit:
        return {"run_id": hit["id"], "status": hit["status"], "dedup": True}
    rid = uuid4().hex
    try:
        db.execute("INSERT INTO capability_runs(id, capability, args, args_hash, status, origin) "
                   "VALUES(%s,%s,%s::jsonb,%s,'queued',%s)",
                   (rid, name, json.dumps(args, default=str), h, origin))
    except Exception as e:  # noqa: BLE001 — 并发撞 uq_capruns_active
        if "unique" in str(e).lower() or "duplicate" in str(e).lower():
            hit = _active(name, h)
            if hit:
                return {"run_id": hit["id"], "status": hit["status"], "dedup": True}
        raise
    return {"run_id": rid, "status": "queued"}


def execute_run(run_id: str) -> dict:
    """执行体(BackgroundTasks/CLI 调):queued→running→fn→done+result / error。**绝不 raise**。"""
    from .registry import by_name

    rows = db.query("SELECT capability, args, status FROM capability_runs WHERE id=%s", (run_id,))
    if not rows:
        return {"status": "error", "error": "run not found"}
    r = rows[0]
    if r["status"] != "queued":
        return {"status": r["status"], "note": "already started/finished"}
    spec = by_name(r["capability"])
    if spec is None:
        db.execute("UPDATE capability_runs SET status='error', error='unknown capability', "
                   "finished_at=now() WHERE id=%s", (run_id,))
        return {"status": "error", "error": f"unknown capability {r['capability']}"}
    # 抢占式置 running(只在仍 queued 时,避免重复执行)
    db.execute("UPDATE capability_runs SET status='running', started_at=now() "
               "WHERE id=%s AND status='queued'", (run_id,))
    try:
        result = spec.fn(**(r["args"] or {}))
        db.execute("UPDATE capability_runs SET status='done', result=%s::jsonb, finished_at=now() "
                   "WHERE id=%s", (json.dumps(result, ensure_ascii=False, default=str), run_id))
        log.info("capability run %s (%s) done", run_id, r["capability"])
        return {"status": "done", "result": result}
    except Exception as e:  # noqa: BLE001 — 记 error 不上抛(fire-and-forget 安全)
        log.warning("capability run %s (%s) failed: %s", run_id, r["capability"], str(e)[:160])
        db.execute("UPDATE capability_runs SET status='error', error=%s, finished_at=now() "
                   "WHERE id=%s", (str(e)[:500], run_id))
        return {"status": "error", "error": str(e)[:200]}


def status(run_id: str) -> dict | None:
    rows = db.query("SELECT id AS run_id, capability, args, status, result, error, origin, "
                    "created_at, started_at, finished_at FROM capability_runs WHERE id=%s", (run_id,))
    return rows[0] if rows else None


def recent(capability: str | None = None, limit: int = 20) -> list[dict]:
    if capability:
        return db.query("SELECT id AS run_id, capability, status, origin, created_at, finished_at "
                        "FROM capability_runs WHERE capability=%s ORDER BY created_at DESC LIMIT %s",
                        (capability, limit))
    return db.query("SELECT id AS run_id, capability, status, origin, created_at, finished_at "
                    "FROM capability_runs ORDER BY created_at DESC LIMIT %s", (limit,))
