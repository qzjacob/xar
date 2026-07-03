"""连接器基类 —— 把每次摄取包进 audit_log，并以双时态 upsert 写入 observation。

每个连接器只需实现 fetch() 返回 observation 行（dict）。基类负责：
  - 注入 knowledge_time（默认=摄取时刻；seed 可显式给历史 knowledge_time 以复刻修订）
  - 计算 snapshot_hash（载荷内容 hash，可复现）+ git_commit（代码版本）
  - 写 audit_log（running→ok/error），observation.ingest_run_id 指向它
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import uuid
from datetime import date, datetime, timezone

from slx.db import connect

SENTINEL_VINTAGE = date(1, 1, 1)  # 无独立 vintage 的哨兵


def sha256_payload(payload) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def git_commit() -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], stderr=subprocess.DEVNULL, text=True
        ).strip()
    except Exception:
        return None


def _as_dt(v):
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    if isinstance(v, date):
        return datetime(v.year, v.month, v.day, tzinfo=timezone.utc)
    return v


class Connector:
    source_id: str = "base"          # 该连接器的【主】source_id
    connector: str = "base"
    # 一个连接器一次拉取可填充【多个】source_id（如 iea_eia_ember 一次写 iea/eia/ember 三源行）。
    # 此处声明除主源外**额外覆盖**的 source_id；编排据此把次源 asset 标记为"由主源 run 覆盖"，
    # 避免重复拉取。留空=只覆盖主源。
    covers_sources: tuple[str, ...] = ()

    def fetch(self) -> list[dict]:
        """返回 observation 行：必填 metric_key, valid_time, value；
        选填 source_id, knowledge_time, vintage_date, unit, value_low, value_high。"""
        raise NotImplementedError

    def run(self) -> uuid.UUID:
        run_id = uuid.uuid4()
        now = datetime.now(timezone.utc)
        with connect() as conn:
            conn.execute(
                "INSERT INTO audit_log (ingest_run_id, source_id, connector, git_commit, started_at, status) "
                "VALUES (%s,%s,%s,%s,%s,'running')",
                (run_id, self.source_id, self.connector, git_commit(), now),
            )
            conn.commit()
            try:
                rows = self.fetch()
                payload_hash = sha256_payload(rows)
                self._write(conn, run_id, rows, payload_hash)
                conn.execute(
                    "UPDATE audit_log SET status='ok', finished_at=now(), rows_written=%s, payload_hash=%s "
                    "WHERE ingest_run_id=%s",
                    (len(rows), payload_hash, run_id),
                )
                conn.commit()
            except Exception as exc:  # noqa: BLE001
                conn.execute(
                    "UPDATE audit_log SET status='error', finished_at=now(), error=%s WHERE ingest_run_id=%s",
                    (str(exc), run_id),
                )
                conn.commit()
                raise
        return run_id

    def _write(self, conn, run_id, rows: list[dict], payload_hash: str) -> None:
        now = datetime.now(timezone.utc)
        for r in rows:
            conn.execute(
                """INSERT INTO observation
                   (metric_key, source_id, value, value_low, value_high, unit,
                    valid_time, knowledge_time, vintage_date, snapshot_hash, ingest_run_id)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (metric_key, source_id, valid_time, knowledge_time, vintage_date)
                   DO UPDATE SET value=EXCLUDED.value, value_low=EXCLUDED.value_low,
                     value_high=EXCLUDED.value_high, unit=EXCLUDED.unit,
                     snapshot_hash=EXCLUDED.snapshot_hash, ingest_run_id=EXCLUDED.ingest_run_id""",
                (
                    r["metric_key"], r.get("source_id", self.source_id), r.get("value"),
                    r.get("value_low"), r.get("value_high"), r.get("unit"),
                    _as_dt(r["valid_time"]), _as_dt(r.get("knowledge_time", now)),
                    r.get("vintage_date", SENTINEL_VINTAGE), r.get("snapshot_hash", payload_hash), run_id,
                ),
            )
