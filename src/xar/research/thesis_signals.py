"""另类数据 → 论点高频校正引擎(零 LLM,纯统计)。

流水:providers/alt/* 写 alt_signals(period_end=经济期,observed_at=知晓时)→
本模块按公司/主题计算每个信号的 **z-score 与动量**,经 ontology.altdata 的
pillar_kinds 映射聚合到 CompanyThesis 的支柱上,产出:

  · health_v2(cid)   —— 事件健康度(thesis.health,叙事面)⊕ 信号健康度(本模块,
                        数量面)的合并视图:每支柱 {event_status, signal_score, signals[]};
  · sync_alt_events  —— |z|≥2 的新期信号 → kg_events(alt_signal)(幂等 dedup)→
                        semantic_facts → Genny 信号流 / Chathy 工具零改动可见;
  · challenged_companies —— 信号面被证伪压力最大的论点 → glm_worker 每轮取 N 家
                        重建(LLM 校正闭环:高频信号定"何时重写",LLM 定"写成什么")。

统计口径(可审计,拒绝黑箱):z = (latest - mean(hist)) / std(hist),hist 为往期
period_end 序列(≥ spec.min_history 才计分),clip ±3 后 /3 归一;方向语义按
spec.good_when 翻转;theme 级信号对链内公司按 0.5 权重摊入。
"""
from __future__ import annotations

import json
import statistics
from datetime import date

from ..logging import get_logger
from ..ontology.altdata import ALT_SIGNALS, SIGNALS_BY_KEY, bindings
from ..storage import altstore, db

log = get_logger("xar.thesis_signals")

_THEME_WEIGHT = 0.5     # theme 级信号摊入公司支柱的折减
_EVENT_Z = 2.0          # 触发 kg_events(alt_signal) 的阈值
_CHALLENGE_SCORE = -0.5  # 支柱信号分低于此视为"被信号挑战"


def _zscore(series_desc: list[dict], min_history: int) -> dict | None:
    """series 为 period_end 倒序。返回 {latest, z, momentum, n, period_end} 或 None。"""
    vals = [r["value"] for r in series_desc if r["value"] is not None]
    if len(vals) < max(min_history, 3):
        return None
    latest, hist = vals[0], vals[1:]
    mean = statistics.fmean(hist)
    stdev = statistics.pstdev(hist)
    if stdev == 0:
        z = 0.0
    else:
        z = max(-3.0, min(3.0, (latest - mean) / stdev))
    momentum = (latest / mean - 1.0) if mean else 0.0
    return {"latest": latest, "z": round(z, 2), "momentum": round(momentum, 4),
            "n": len(vals), "period_end": str(series_desc[0]["period_end"])}


def _contribution(spec, z: float) -> float:
    """方向语义 → [-1,1] 贡献。good_when=None 的信号不计分(仅注意力旗标)。"""
    if spec.good_when is None or z == 0:
        return 0.0
    signed = z / 3.0
    return signed if spec.good_when == "rising" else -signed


def signal_snapshot(company_id: str, *, as_of: date | None = None) -> list[dict]:
    """一家公司当前全部另类信号的统计快照(含 theme 级摊入)。"""
    b = bindings().get(company_id)
    out: list[dict] = []
    if b:
        for key in b.signals():
            spec = SIGNALS_BY_KEY[key]
            stats = _zscore(altstore.series(key, company_id=company_id, as_of=as_of),
                            spec.min_history)
            if stats is None:
                continue
            out.append({"signal_key": key, "name_cn": spec.name_cn, "scope": "company",
                        "good_when": spec.good_when, "pillar_kinds": list(spec.pillar_kinds),
                        "contribution": round(_contribution(spec, stats["z"]), 3), **stats})
    # theme 级信号(韩国出口/全球出货)摊入链内公司
    from ..ingestion.registry import company_by_id

    c = company_by_id(company_id) or {}
    themes = set(c.get("themes") or ())
    for spec in ALT_SIGNALS:
        if spec.scope != "theme" or not (themes & set(spec.themes)):
            continue
        for theme in themes & set(spec.themes):
            stats = _zscore(altstore.series(spec.key, theme=theme, as_of=as_of),
                            spec.min_history)
            if stats is None:
                continue
            out.append({"signal_key": spec.key, "name_cn": spec.name_cn, "scope": "theme",
                        "theme": theme, "good_when": spec.good_when,
                        "pillar_kinds": list(spec.pillar_kinds),
                        "contribution": round(_contribution(spec, stats["z"]) * _THEME_WEIGHT, 3),
                        **stats})
            break
    return out


def pillar_signal_scores(company_id: str, *, as_of: date | None = None) -> dict:
    """支柱 kind → {score, signals[]}。score = 该 kind 全部信号贡献均值 ∈ [-1,1]。"""
    snap = signal_snapshot(company_id, as_of=as_of)
    by_kind: dict[str, list[dict]] = {}
    for s in snap:
        for k in s["pillar_kinds"]:
            by_kind.setdefault(k, []).append(s)
    return {k: {"score": round(statistics.fmean(x["contribution"] for x in sigs), 3),
                "signals": sigs}
            for k, sigs in by_kind.items()}


def health_v2(company_id: str) -> dict | None:
    """事件健康度 ⊕ 信号健康度的合并视图(公司页/工具/重建排序共用口径)。"""
    from . import thesis as th

    base = th.health(company_id)
    if base is None:
        return None
    kind_scores = pillar_signal_scores(company_id)
    row = th.latest(company_id)
    content = row["content"] if isinstance(row["content"], dict) else json.loads(row["content"])
    kinds_by_key = {p.get("key"): p.get("kind") for p in content.get("pillars", [])}
    weights = {p.get("key"): float(p.get("weight") or 0) for p in content.get("pillars", [])}

    challenged = 0
    for p in base["pillars"]:
        ks = kind_scores.get(kinds_by_key.get(p["key"]) or "", {})
        p["signal_score"] = ks.get("score")
        p["signals"] = [{k: s[k] for k in ("signal_key", "name_cn", "z", "momentum",
                                           "contribution", "period_end", "scope")}
                        for s in ks.get("signals", [])]
        sig_challenged = (p["signal_score"] is not None
                          and p["signal_score"] <= _CHALLENGE_SCORE
                          and weights.get(p["key"], 0) >= 0.15)
        if p["status"] == "challenging" or sig_challenged:
            challenged += 1
            if sig_challenged and p["status"] in ("quiet", "mixed"):
                p["status"] = "challenging"     # 信号面提级:事件静默但数量面证伪
    if challenged:
        base["overall"] = "challenged"
    elif base["overall"] == "quiet" and any(
            (p.get("signal_score") or 0) >= 0.4 for p in base["pillars"]):
        base["overall"] = "confirming"          # 事件静默但信号面强确认
    base["signal_based"] = True
    return base


# ── 语义流桥接:阈值信号 → kg_events(alt_signal) ────────────────────────────────
def sync_alt_events(*, as_of: date | None = None, z_threshold: float = _EVENT_Z) -> dict:
    """|z|≥阈值 的新期公司信号 → kg_events(幂等)。theme 级信号发链级事件(company NULL)。"""
    from ..ingestion.registry import company_by_id

    inserted = skipped = 0
    for cid in bindings():
        for s in signal_snapshot(cid, as_of=as_of):
            if s["scope"] != "company" or abs(s["z"]) < z_threshold:
                continue
            spec = SIGNALS_BY_KEY[s["signal_key"]]
            pol = "neutral"
            if spec.good_when:
                aligned = s["z"] if spec.good_when == "rising" else -s["z"]
                pol = "positive" if aligned > 0 else "negative"
            c = company_by_id(cid) or {}
            theme = (c.get("themes") or [None])[0]
            dedup = f"alt:{s['signal_key']}:{cid}:{s['period_end']}"
            before = db.query("SELECT 1 FROM kg_events WHERE dedup_key=%s", (dedup,))
            if before:
                skipped += 1
                continue
            db.execute(
                "INSERT INTO kg_events(company_id, event_type, event_date, polarity, summary, "
                "narrative, attrs, confidence, license_tag, dedup_key, theme, time_orientation) "
                "VALUES (%s,'alt_signal',%s,%s,%s,%s,%s::jsonb,0.85,'alt',%s,%s,"
                "'backward_looking') ON CONFLICT (dedup_key) DO NOTHING",
                (cid, s["period_end"], pol,
                 f"另类信号:{s['name_cn']} z={s['z']:+.1f}(动量 {s['momentum']:+.1%},"
                 f"期末 {s['period_end']})",
                 spec.rationale_zh[:200],
                 json.dumps({k: s[k] for k in ("signal_key", "latest", "z", "momentum", "n")},
                            ensure_ascii=False, default=str),
                 dedup, theme))
            inserted += 1
    out = {"inserted": inserted, "skipped": skipped}
    log.info("alt-signal events: %s", out)
    return out


def challenged_companies(limit: int = 2) -> list[str]:
    """信号面挑战最重的既有论点(供 glm_worker 每轮重建 N 家)。零 LLM。"""
    rows = db.query(
        "SELECT DISTINCT ON (company_id) company_id FROM company_thesis ORDER BY company_id")
    scored: list[tuple[float, str]] = []
    for r in rows:
        cid = r["company_id"]
        try:
            ks = pillar_signal_scores(cid)
        except Exception:  # noqa: BLE001
            continue
        if not ks:
            continue
        worst = min(v["score"] for v in ks.values())
        if worst <= _CHALLENGE_SCORE:
            scored.append((worst, cid))
    return [cid for _, cid in sorted(scored)[:limit]]
