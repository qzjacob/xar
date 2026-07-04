"""pytest 夹具：DB 可用性探测、注册表+seed 装载、登记簿断言加载。

XAR vendor 适配（见 ANDY_UPSTREAM.md）：REG 指向 slx 包内 registry/；DSN 桥接
XAR 开发库（SLX_DATABASE_URL ← xar settings.database_url）；seeded 先跑 init_schema()。
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

import slx

REG = Path(slx.__file__).resolve().parent / "registry"

# 与 xar 测试共用同一开发库：slx 对象隔离在 schema `slx`，互不相扰。
if not os.environ.get("SLX_DATABASE_URL") and not os.environ.get("DATABASE_URL"):
    try:
        from xar.config import get_settings

        os.environ["SLX_DATABASE_URL"] = get_settings().database_url
    except Exception:
        pass

# 测试沙箱：vendored 测试断言 seed 数据独占指标(如 labor_share 的 0.582/0.575 双 vintage);
# 真实 ALFRED 等数据落入生产 schema `slx` 后会叠加同键。跑测试固定用独立 `slx_test`。
os.environ["SLX_SCHEMA"] = "slx_test"


def _db_available() -> bool:
    try:
        from slx.db import connect

        with connect() as c:
            c.execute("SELECT 1")
        return True
    except Exception:
        return False


DB = _db_available()
requires_db = pytest.mark.skipif(not DB, reason="无 Postgres（先 docker compose up -d）")


@pytest.fixture(scope="session")
def seeded():
    """加载理论本体 + 注入确定性 seed + 跑识别（写回派生估计），全幂等。"""
    from datetime import date

    from slx.db import init_schema
    from slx.ingestion.identification_panels import run_identification
    from slx.ingestion.seed import SeedConnector
    from slx.tools.load_registry import main as load_registry

    init_schema()
    load_registry()
    SeedConnector().run()
    # Phase 2.2：识别在 as_of 之前留痕（knowledge_time=2026-06-23），供 PIT 判定可见。
    run_identification(date(2026, 6, 23))
    yield


@pytest.fixture
def conn():
    from slx.db import connect

    c = connect()
    try:
        yield c
    finally:
        c.close()


@pytest.fixture(scope="session")
def claims() -> dict:
    doc = yaml.safe_load((REG / "overclaim_registry.yml").read_text("utf-8"))
    return {c["claim_key"]: c for c in doc["claims"]}
