"""数据库连接助手（XAR vendor 本地化改造 —— 见 ANDY_UPSTREAM.md）。

与上游的差异（唯一有意 local-mod 文件之一）：
  · 去掉 dotenv/.env 加载 —— host（XAR）负责环境；
  · DSN 读 SLX_DATABASE_URL，回落 DATABASE_URL（由 xar.api.andy_mount 桥接注入）；
  · 所有连接固定 search_path=slx,public —— slx 的全部对象隔离在专用 schema `slx`，
    仅作用于本模块发出的裸连接，不触碰 xar 的连接池；
  · 新增 init_schema()：CREATE SCHEMA IF NOT EXISTS slx + 执行去 TimescaleDB 的 schema.sql。
依赖方向保持单向：slx 永不 import xar。
"""
from __future__ import annotations

import os
from pathlib import Path

DEFAULT_URL = "postgresql://siliconomics:siliconomics@localhost:5432/siliconomics"


def database_url() -> str:
    return os.environ.get("SLX_DATABASE_URL") or os.environ.get("DATABASE_URL", DEFAULT_URL)


def schema_name() -> str:
    """专用 schema 名;默认 slx。测试夹具设 SLX_SCHEMA=slx_test 获得与真实数据
    完全隔离的干净沙箱(vendored 测试假设自己独占指标观测)。"""
    return os.environ.get("SLX_SCHEMA", "slx")


def connect():
    """返回一个 psycopg 连接（autocommit=False，search_path={schema},public）。调用方负责 commit/close 或用 with。"""
    import psycopg

    return psycopg.connect(database_url(), options=f"-c search_path={schema_name()},public")


def init_schema() -> None:
    """幂等建 schema：CREATE SCHEMA + 执行 schema.sql（本身已全幂等）。"""
    with connect() as conn:
        conn.execute(f"CREATE SCHEMA IF NOT EXISTS {schema_name()}")
        conn.execute(Path(__file__).with_name("schema.sql").read_text())
        conn.commit()
