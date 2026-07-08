"""统一异步触发(UA-P1)—— capability_runs 表 + schedule/launch/execute。

修复四种分裂的触发风格:所有 build/slow 能力经此一条道跑(API/CLI/Chathy 共用),run_id 可轮询。
- running 去重靠部分唯一索引 `uq_capruns_active`(同能力+**归一化**参数活跃时唯一);
- 进程死亡遗孤(>30min running)在下次 schedule 时被收割成 error,放行新 run;
- `execute_run` **原子认领**(UPDATE…WHERE status='queued' RETURNING,只有抢到的那个执行),
  终态 UPDATE 带 `status='running'` 守卫(防被收割的 run 复活),整体 **绝不 raise**;
- `launch` = schedule + (新建时)后台线程 execute_run,让没有 FastAPI BackgroundTasks 的
  Chathy/内部调用也能真正把队列排空。
"""
from __future__ import annotations

import hashlib
import json
import threading
from uuid import uuid4

from ..logging import get_logger
from ..storage import db

log = get_logger("xar.capabilities.runs")

_STALE_SECONDS = 1800   # >30min 的 running = 进程死亡遗孤


def _normalize_args(name: str, args: dict) -> dict:
    """用能力 schema 的默认值补全参数,再哈希 —— 否则 {cid} 与 {cid, force:false} 会哈希不等、
    同一逻辑 run 去重失败(评审 #4)。未知键原样保留。"""
    from .registry import by_name

    spec = by_name(name)
    out = dict(args or {})
    if spec:
        for k, meta in (spec.parameters.get("properties") or {}).items():
            if k not in out and isinstance(meta, dict) and "default" in meta:
                out[k] = meta["default"]
    return out


def _args_hash(args: dict) -> str:
    return hashlib.sha256(json.dumps(args or {}, sort_keys=True, default=str).encode()).hexdigest()


def _active(name: str, h: str) -> dict | None:
    rows = db.query("SELECT id, status FROM capability_runs WHERE capability=%s AND args_hash=%s "
                    "AND status IN ('queued','running') ORDER BY created_at DESC LIMIT 1", (name, h))
    return rows[0] if rows else None


def _latest(name: str, h: str) -> dict | None:
    rows = db.query("SELECT id, status FROM capability_runs WHERE capability=%s AND args_hash=%s "
                    "ORDER BY created_at DESC LIMIT 1", (name, h))
    return rows[0] if rows else None


def schedule(name: str, args: dict | None = None, *, origin: str = "api") -> dict:
    """收割陈旧 running → 活跃去重(命中返回既有 run_id + dedup)→ INSERT queued。
    并发撞唯一索引 → 读回既有活跃行(已结束则读回最近行,绝不把竞态变 500,评审 #6)。
    返回 {run_id, status[, dedup]}。参数在入库前按 schema 默认值归一化(评审 #4)。"""
    args = _normalize_args(name, args or {})
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
            row = _active(name, h) or _latest(name, h)   # 竞态对手仍活跃 → 用它;已结束 → 用最近行
            if row:
                return {"run_id": row["id"], "status": row["status"], "dedup": True}
        raise
    return {"run_id": rid, "status": "queued"}


def execute_run(run_id: str) -> dict:
    """执行体(BackgroundTasks/线程/CLI 调):**原子认领** queued→running,只有抢到的执行;
    终态 UPDATE 带 status='running' 守卫;整体 **绝不 raise**(评审 #1/#3/#5/#7/#8)。"""
    from .registry import by_name

    try:
        # 原子认领:只有把 queued 翻成 running 的那个调用拿到 RETURNING 行,其余判定输、直接返回
        claimed = db.query("UPDATE capability_runs SET status='running', started_at=now() "
                           "WHERE id=%s AND status='queued' RETURNING capability, args", (run_id,))
        if not claimed:
            cur = db.query("SELECT status FROM capability_runs WHERE id=%s", (run_id,))
            return {"status": (cur[0]["status"] if cur else "error"), "note": "not claimed"}
        cap, cargs = claimed[0]["capability"], claimed[0]["args"]
        spec = by_name(cap)
        if spec is None:
            db.execute("UPDATE capability_runs SET status='error', error='unknown capability', "
                       "finished_at=now() WHERE id=%s AND status='running'", (run_id,))
            return {"status": "error", "error": f"unknown capability {cap}"}
    except Exception as e:  # noqa: BLE001 — 认领阶段 DB 抖动:记日志不上抛
        log.warning("capability run %s claim failed: %s", run_id, str(e)[:160])
        return {"status": "error", "error": f"claim: {str(e)[:120]}"}
    try:
        result = spec.fn(**(cargs or {}))
        db.execute("UPDATE capability_runs SET status='done', result=%s::jsonb, finished_at=now() "
                   "WHERE id=%s AND status='running'",
                   (json.dumps(result, ensure_ascii=False, default=str), run_id))
        log.info("capability run %s (%s) done", run_id, cap)
        return {"status": "done", "result": result}
    except Exception as e:  # noqa: BLE001 — fn 失败:记 error 不上抛(never-raise 契约)
        log.warning("capability run %s (%s) failed: %s", run_id, cap, str(e)[:160])
        try:
            db.execute("UPDATE capability_runs SET status='error', error=%s, finished_at=now() "
                       "WHERE id=%s AND status='running'", (str(e)[:500], run_id))
        except Exception as e2:  # noqa: BLE001 — 连记 error 都失败也不炸(never-raise)
            log.warning("capability run %s error-record failed: %s", run_id, str(e2)[:120])
        return {"status": "error", "error": str(e)[:200]}


def launch(name: str, args: dict | None = None, *, origin: str = "api") -> dict:
    """schedule + (仅新建时)后台守护线程 execute_run —— 供没有 FastAPI BackgroundTasks 的
    调用方(Chathy 工具、内部触发)真正排空队列;去重命中则不重复起线程(评审 #2)。
    原子认领保证即便多方并发驱动同一 run 也只执行一次。"""
    sched = schedule(name, args, origin=origin)
    if not sched.get("dedup") and sched.get("status") == "queued":
        threading.Thread(target=execute_run, args=(sched["run_id"],), daemon=True).start()
    return sched


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
