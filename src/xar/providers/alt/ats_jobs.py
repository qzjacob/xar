"""ATS 在招职位 -> alt.hiring_velocity(招聘速度,公司级)。

Greenhouse / Lever 官方招聘板(公开、无 key):数一家公司在招职位总数,作为
扩张/收缩的先行几个季度信号。AI 岗位数 / 工程岗位数 / Top-3 地点入 meta。

绑定来自 xar.ontology.altdata.bindings()[cid].ats = (kind, slug),
kind ∈ {greenhouse, lever}。Greenhouse 返回 {"jobs":[{title, location:{name}}]},
Lever 返回 [{text, categories:{location}}]。解析为纯函数(离线可测),写入走
storage.altstore.upsert_signal 单一写路。逐条失败记录并跳过,永不抛出;每公司
间隔 1s 节流(叠加 providers.base 的 per-host polite)。
"""
from __future__ import annotations

import re
import time
from collections import Counter
from datetime import date

from ...ontology.altdata import bindings
from ...storage.altstore import upsert_signal
from ..base import get_json, log

SIGNAL_KEY = "alt.hiring_velocity"
SOURCE = "ats_jobs"
UNIT = "count"
_PACE_SECONDS = 1.0  # 每公司间隔(任务口径:Pace 1s)

_GH_URL = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
_LEVER_URL = "https://api.lever.co/v0/postings/{slug}?mode=json"
_GH_HOST = "boards-api.greenhouse.io"
_LEVER_HOST = "api.lever.co"

# 岗位标题分类(与任务给定的 AI 正则一致;工程为合理补充)。
_AI_RE = re.compile(r"(machine learning|\bML\b|\bAI\b|LLM|deep learning|data scientist)", re.I)
_ENG_RE = re.compile(
    r"(engineer|developer|\bSWE\b|software|infrastructure|back-?end|front-?end|"
    r"full[\s-]?stack|dev\s?ops|\bSRE\b|platform|architect)",
    re.I,
)


def available() -> bool:
    return True  # 公开招聘板,无 key


# --- 纯解析(离线可测,无网络/无 DB)------------------------------------------
def normalize_jobs(kind: str, payload) -> list[dict]:
    """把 Greenhouse/Lever 原始 JSON 规范化为 [{title, location}]。未知 kind/坏形状 -> []。"""
    out: list[dict] = []
    if kind == "greenhouse":
        jobs = (payload or {}).get("jobs") if isinstance(payload, dict) else None
        for j in jobs or []:
            loc = j.get("location") or {}
            out.append({
                "title": (j.get("title") or "").strip(),
                "location": (loc.get("name") if isinstance(loc, dict) else "") or "",
            })
    elif kind == "lever":
        for p in payload or []:
            if not isinstance(p, dict):
                continue
            cats = p.get("categories") or {}
            out.append({
                "title": (p.get("text") or "").strip(),
                "location": (cats.get("location") if isinstance(cats, dict) else "") or "",
            })
    return out


def metrics(jobs: list[dict]) -> dict:
    """在招总数 + AI/工程岗位数 + Top-3 地点(纯函数)。"""
    titles = [j.get("title", "") for j in jobs]
    ai = sum(1 for t in titles if _AI_RE.search(t))
    eng = sum(1 for t in titles if _ENG_RE.search(t))
    locs = Counter((j.get("location") or "").strip() for j in jobs)
    locs.pop("", None)
    top3 = [[name, n] for name, n in locs.most_common(3)]
    return {"total": len(jobs), "ai_roles": ai, "eng_roles": eng, "locations_top3": top3}


# --- 取数 + 写入 -------------------------------------------------------------
def _fetch(kind: str, slug: str):
    if kind == "greenhouse":
        return get_json(_GH_URL.format(slug=slug), host=_GH_HOST)
    if kind == "lever":
        return get_json(_LEVER_URL.format(slug=slug), host=_LEVER_HOST)
    log.warning("ats_jobs: unknown kind %r for slug %r", kind, slug)
    return None


def ingest_one(company_id: str, kind: str, slug: str,
               *, period_end: date | None = None) -> dict | None:
    """取一家公司的招聘板并写入 alt.hiring_velocity。失败 -> 记录并返回 None(不抛)。"""
    try:
        payload = _fetch(kind, slug)
        if payload is None:
            return None  # 网络/HTTP 失败已在 get_json 记录
        jobs = normalize_jobs(kind, payload)
        m = metrics(jobs)
        pe = period_end or date.today()
        meta = {**m, "kind": kind, "slug": slug}
        upsert_signal(
            SIGNAL_KEY, period_end=pe, value=float(m["total"]),
            company_id=company_id, unit=UNIT, source=SOURCE, meta=meta,
        )
        log.info("ats_jobs %s (%s:%s): %d postings (ai=%d eng=%d)",
                 company_id, kind, slug, m["total"], m["ai_roles"], m["eng_roles"])
        return {"company_id": company_id, **meta}
    except Exception as e:  # noqa: BLE001  逐条隔离,永不下沉整轮
        log.warning("ats_jobs %s (%s:%s) failed: %s", company_id, kind, slug, type(e).__name__)
        return None


def _targets() -> list[tuple[str, str, str]]:
    """从本体绑定派生 (company_id, kind, slug);仅取设了 .ats 的公司。"""
    out: list[tuple[str, str, str]] = []
    for cid, b in bindings().items():
        if not b.ats:
            continue
        kind, slug = b.ats
        if kind in ("greenhouse", "lever") and slug:
            out.append((cid, kind, slug))
    return out


def pull(limit: int | None = None) -> dict:
    """扇出全部 ATS 绑定公司,写入 alt.hiring_velocity。返回运行统计。"""
    targets = _targets()
    if limit is not None:
        targets = targets[:limit]
    stats = {"attempted": 0, "ok": 0, "skipped": 0, "postings": 0,
             "ai_roles": 0, "companies": len(targets)}
    for i, (cid, kind, slug) in enumerate(targets):
        if i:
            time.sleep(_PACE_SECONDS)  # 节流:每公司 1s
        stats["attempted"] += 1
        r = ingest_one(cid, kind, slug)
        if r is None:
            stats["skipped"] += 1
            continue
        stats["ok"] += 1
        stats["postings"] += int(r.get("total", 0))
        stats["ai_roles"] += int(r.get("ai_roles", 0))
    log.info("ats_jobs pull: %s", stats)
    return stats
