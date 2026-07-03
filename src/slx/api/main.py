"""Silicon-Index 服务层入口 —— `uvicorn api.main:app`。

装配三组路由（metrics / registry / overclaims），提供健康检查与根目录。
全局纪律（见 api.deps）：所有读数走 point-in-time（防前视），soft 指标响应必带
identification_status=unidentified + caveat 水印，服务层从不输出因果断言。
"""
from __future__ import annotations

from fastapi import FastAPI

from slx.db import connect, database_url

from .routers import metrics, overclaims, registry

app = FastAPI(
    title="Silicon-Index API",
    version="0.1.0",
    description=(
        "硅基经济学 AI 经济指标库的服务层。\n\n"
        "纪律：读数严格 point-in-time（knowledge_time<=as_of，防回测前视）；"
        "soft 指标一律 identification_status=unidentified 并带 caveat 水印；"
        "服务层不输出因果结论，只暴露观测与理论本体。"
    ),
)

app.include_router(metrics.router)
app.include_router(registry.router)
app.include_router(overclaims.router)


@app.get("/", tags=["meta"])
def root() -> dict:
    """根：服务自述 + 路由索引。"""
    return {
        "service": "Silicon-Index API",
        "version": "0.1.0",
        "discipline": [
            "point-in-time：所有读数 knowledge_time<=as_of，严禁 SELECT latest",
            "soft 指标 identification_status=unidentified，带 caveat 水印",
            "服务层不输出因果断言，只暴露观测与理论本体",
        ],
        "routes": {
            "metrics": ["GET /metrics", "GET /metrics/{metric_key}?as_of=YYYY-MM-DD"],
            "registry": ["GET /registry/anchors", "GET /registry/anchors/{anchor_key}",
                         "GET /registry/metrics"],
            "overclaims": ["GET /overclaims", "GET /overclaims/{claim_key}",
                           "POST /overclaims/evaluate?as_of=YYYY-MM-DD"],
            "meta": ["GET /health", "GET /docs"],
        },
    }


@app.get("/health", tags=["meta"])
def health() -> dict:
    """健康检查：探活 DB（SELECT 1）。DB 不可达时 status=degraded（仍 200，便于探针区分进程存活）。"""
    db_ok = False
    db_error = None
    try:
        with connect() as conn:
            conn.execute("SELECT 1").fetchone()
        db_ok = True
    except Exception as exc:  # noqa: BLE001
        db_error = str(exc)
    return {
        "status": "ok" if db_ok else "degraded",
        "database": {
            "reachable": db_ok,
            # 只回主机/库名层面的连接串（不含口令）——database_url() 可能含密码，故不直接回显。
            "url_configured": bool(database_url()),
            "error": db_error,
        },
    }
