"""季报 → 个股投资观点的**反哺回路**(Genny 个股持续更新的动力源)。

此前 Genny 与 Phanny/ET 只有**单向**耦合:季报 dossier 会注入 Genny 的论点与主题争论
(research/earnings.py 的 §8c/§9),但季报本身的结论从不回流 —— 一份论点里的
「AI 变现能否兑现」争论,哪怕连着两个季度被财报证伪,论点也不会知道。本模块补上反向。

## 为什么走 kg_events 总线,而不直接写 thesis_fact_links
1. `thesis_health.debate_health` 只认 `origin ∈ {llm, rule}` 两条道;新增第三种 origin
   会在天平计算里**完全隐形**,除非同时改评分数学(以及随之而来的双计风险)。
2. Phanny 的 6 个维度到某家公司的支柱/争论键**没有确定性映射** —— 那正是
   `evidence_link` 的 LLM 相对主张道存在的理由,不该在这里硬编码。
3. `thesis_signals.sync_alt_events` 已经证明了这个模式:合成一条接地事实 → 下游
   (Genny 信号块、论点 dossier、link 道、health_v3、challenged→重建、Chathy 工具)
   **零改动**全部看得见。

## 铁律:只有**已兑现**的结果成为事实,赛前观点永不入库
这斩断了 phanny↔thesis 的互引循环:季报 dossier 读论点、论点读季报事实,若把
「裁决怎么看」也写成事实,两边就会互相强化自己的猜测。已实现的市场反应是客观数据,
循环终止于现实。
"""
from __future__ import annotations

import json
from datetime import date, timedelta

from ..logging import get_logger
from ..storage import db

log = get_logger("xar.quarterly_feedback")

_DEDUP_PREFIX = "quarterly_print"


def _dedup_key(cid: str, event_date) -> str:
    """一次财报 = 一条事实,无论有几个裁决系统(Phanny / ET)先后回验它。
    这是双计的第一道闸:两条 12h 回验节拍谁先跑都只写出一条。"""
    return f"{_DEDUP_PREFIX}:{cid}:{event_date}"


def _verdicts_for(cid: str, event_date) -> dict:
    """取该次财报已回验的裁决(Phanny 1-10 与 ET 0-10 **各自独立**,刻度绝不互换)。"""
    out: dict = {}
    for tbl, key in (("phanny_verdicts", "phanny"), ("earnings_verdicts", "earnings")):
        try:
            rows = db.query(
                f"SELECT id, direction, conviction, outcome FROM {tbl} "  # noqa: S608 — 表名是代码常量
                "WHERE company_id=%s AND event_date=%s AND outcome->>'status'='scored' "
                "ORDER BY version DESC LIMIT 1", (cid, event_date))
        except Exception as e:  # noqa: BLE001
            log.warning("verdict lookup failed (%s/%s): %s", tbl, cid, str(e)[:120])
            continue
        if rows:
            out[key] = rows[0]
    return out


def _summary_zh(cid: str, event_date, reaction: float | None, v: dict,
                surprise=None) -> str:
    bits = [f"{cid} {event_date} 季报兑现"]
    if reaction is not None:
        bits.append(f"盘后反应 {reaction:+.2f}%")
    if surprise is not None:
        bits.append(f"业绩超预期 {float(surprise):+.1f}%")
    for key, label in (("phanny", "Phanny"), ("earnings", "ET")):
        row = v.get(key)
        if not row:
            continue
        oc = row.get("outcome") or {}
        hit = oc.get("direction_hit")
        mark = "命中" if hit else ("未中" if hit is False else "未判定")
        bits.append(f"{label} 判 {row['direction']}(conviction {row['conviction']},{label}刻度)"
                    f"→ {mark}")
    return ";".join(bits) + "。"


def on_outcome(cid: str, event_date, *, run_id: str | None = None) -> dict:
    """一次财报兑现后的反哺(零 LLM、幂等、fail-soft)。

    ① 合成一条 `quarterly_print` 事实进 kg_events(dedup 保证一次财报只一条);
    ② 对该公司定向跑一次 VerificationPoint 数值校验 —— 财报刚落地正是季度级 VP 该读数的
       时刻,不必排队等 link 道的公司轮询轮到它。
    """
    from ..ingestion.registry import company_by_id

    out = {"company_id": cid, "event_date": str(event_date),
           "event_inserted": False, "vp_checks": 0}
    dedup = _dedup_key(cid, event_date)
    try:
        if db.query("SELECT 1 FROM kg_events WHERE dedup_key=%s", (dedup,)):
            out["deduped"] = True
        else:
            v = _verdicts_for(cid, event_date)
            occ = _occurred_meta(cid, event_date)
            reaction = _reaction_pct(v)
            if reaction is None:
                # 全覆盖库道(公司没有任何裁决):直接从价格算市场反应。没有它,这些事实
                # 只剩「业绩超预期 x%」且极性恒为中性 —— 对 link 道几乎没有信号。
                reaction = _reaction_from_prices(cid, event_date, (occ or {}).get("session"))
            surprise = (occ or {}).get("surprise_pct")
            # 极性 = 市场对这份财报的裁决(客观),不是任何模型的观点
            pol = "neutral"
            if reaction is not None:
                pol = "positive" if reaction > 0 else ("negative" if reaction < 0 else "neutral")
            c = company_by_id(cid) or {}
            theme = (c.get("themes") or [None])[0]
            attrs = {"reaction_pct": reaction, "surprise_pct": surprise,
                     "phanny_verdict_id": (v.get("phanny") or {}).get("id"),
                     "earnings_verdict_id": (v.get("earnings") or {}).get("id"),
                     "phanny_hit": ((v.get("phanny") or {}).get("outcome") or {}).get("direction_hit"),
                     "earnings_hit": ((v.get("earnings") or {}).get("outcome") or {}).get("direction_hit"),
                     "session": (occ or {}).get("session")}
            db.execute(
                "INSERT INTO kg_events(company_id, event_type, event_date, polarity, summary, "
                "narrative, attrs, confidence, license_tag, dedup_key, theme, time_orientation) "
                "VALUES (%s,'earnings',%s,%s,%s,%s,%s::jsonb,0.9,'internal',%s,%s,'backward_looking') "
                "ON CONFLICT (dedup_key) DO NOTHING",
                (cid, event_date, pol, _summary_zh(cid, event_date, reaction, v, surprise),
                 "季报兑现回验:市场实际反应 + 各裁决系统的命中情况(已实现结果,非赛前观点)",
                 json.dumps(attrs, ensure_ascii=False, default=str), dedup, theme))
            out["event_inserted"] = True
    except Exception as e:  # noqa: BLE001 — 反哺失败绝不拖垮回验批
        log.warning("quarterly_feedback event failed (%s): %s", cid, str(e)[:160])
        out["error"] = str(e)[:160]
    # ② 定向 VP 扫描:财报把新的 fundamentals 实际值带进来了,季度级 VP 此刻该读数
    try:
        from . import evidence_link, thesis
        th = thesis.latest(cid)
        if th:
            # check_verification_points 返回**结果列表**(不是计数)——取长度
            out["vp_checks"] = len(evidence_link.check_verification_points(cid, th) or [])
    except Exception as e:  # noqa: BLE001
        log.warning("quarterly_feedback VP sweep failed (%s): %s", cid, str(e)[:160])
    return out


def _occurred_meta(cid: str, event_date) -> dict | None:
    rows = db.query(
        "SELECT meta FROM event_calendar WHERE company_id=%s AND event_type='earnings' "
        "AND status='occurred' AND scheduled_for BETWEEN %s AND %s "
        "ORDER BY abs(scheduled_for - %s) LIMIT 1",
        (cid, event_date - timedelta(days=3), event_date + timedelta(days=3), event_date))
    return (rows[0]["meta"] or {}) if rows else None


def _reaction_pct(v: dict) -> float | None:
    for key in ("phanny", "earnings"):
        oc = (v.get(key) or {}).get("outcome") or {}
        if oc.get("reaction_pct") is not None:
            return float(oc["reaction_pct"])
    return None


def _reaction_from_prices(cid: str, event_date, session: str | None) -> float | None:
    """无裁决公司的市场反应:复用 ET 的 reaction_return(同一套 amc/bmo 口径,不另造轮子)。"""
    try:
        from . import earnings
        rr = earnings.reaction_return(cid, event_date, session)
        return float(rr["reaction_pct"]) if rr else None
    except Exception as e:  # noqa: BLE001
        log.warning("reaction lookup failed (%s): %s", cid, str(e)[:120])
        return None


def recent_print_companies(days: int = 5, limit: int = 20) -> list[tuple[str, str]]:
    """财报感知的重建队列:已兑现且论点比这次财报更旧的公司 → [(cid, because)]。

    此前重建候选只看信号/争论压力(challenged_companies_v2),财报**完全不参与** ——
    一家公司刚出完季报、论点还停在上季度,系统对此毫无反应。"""
    try:
        rows = db.query(
            "WITH scored AS ("
            "  SELECT company_id, event_date, outcome_at FROM phanny_verdicts"
            "   WHERE outcome->>'status'='scored' AND outcome_at >= now() - (%s || ' days')::interval"
            "  UNION"
            "  SELECT company_id, event_date, outcome_at FROM earnings_verdicts"
            "   WHERE outcome->>'status'='scored' AND outcome_at >= now() - (%s || ' days')::interval)"
            " SELECT s.company_id, max(s.event_date) AS event_date FROM scored s"
            " JOIN LATERAL (SELECT max(as_of) AS mx FROM company_thesis t"
            "               WHERE t.company_id = s.company_id) th ON true"
            " WHERE th.mx IS NOT NULL AND th.mx < s.event_date"
            " GROUP BY s.company_id ORDER BY max(s.outcome_at) DESC LIMIT %s",
            (days, days, limit))
    except Exception as e:  # noqa: BLE001
        log.warning("recent_print_companies failed: %s", str(e)[:160])
        return []
    return [(r["company_id"], f"季报兑现 {r['event_date']}(见 quarterly_print 事实)")
            for r in rows]


def sweep(days: int = 5, limit: int = 200) -> dict:
    """**全覆盖库**的零 LLM 反哺道:任何持有论点的公司,只要近 N 天出过财报(不论有没有
    Phanny/ET 裁决),都合成一条 quarterly_print 事实并扫一次 VP。

    这是把季报体系铺到 Genny 全部覆盖公司的关键 —— 完整的多空辩论很贵(每名 ~40 次
    订阅调用),但「财报兑现了什么、论点该不该动」这件事本身零成本,不该只有 31 家享有。"""
    since = date.today() - timedelta(days=days)
    try:
        rows = db.query(
            "SELECT DISTINCT ec.company_id, ec.scheduled_for FROM event_calendar ec "
            "JOIN company_thesis t ON t.company_id = ec.company_id "
            "WHERE ec.event_type='earnings' AND ec.status='occurred' "
            "AND ec.scheduled_for BETWEEN %s AND current_date "
            "ORDER BY ec.scheduled_for DESC LIMIT %s", (since, limit))
    except Exception as e:  # noqa: BLE001
        log.warning("quarterly sweep query failed: %s", str(e)[:160])
        return {"error": str(e)[:160]}
    out = {"seen": len(rows), "events": 0, "vp_checks": 0}
    for r in rows:
        res = on_outcome(r["company_id"], r["scheduled_for"])
        out["events"] += 1 if res.get("event_inserted") else 0
        out["vp_checks"] += res.get("vp_checks") or 0
    log.info("quarterly feedback sweep: %s", out)
    return out


def lineage(cid: str, limit: int = 8) -> list[dict]:
    """某公司的「季报如何改写了论点」血缘:quarterly_print 事实 → 它触发的证据裁决。
    供 Genny 个股页与 Chathy 的 quarterly_review 能力直接展示。"""
    try:
        return db.query(
            "SELECT e.event_date, e.summary, e.attrs, l.target_kind, l.target_key, "
            "       l.verdict, l.rationale_zh, l.origin "
            "FROM kg_events e "
            "LEFT JOIN thesis_fact_links l ON l.fact_kind='event' AND l.fact_ref = e.id::text "
            "WHERE e.company_id=%s AND e.dedup_key LIKE %s "
            "ORDER BY e.event_date DESC LIMIT %s",
            (cid, f"{_DEDUP_PREFIX}:%", limit))
    except Exception as e:  # noqa: BLE001
        log.warning("lineage failed (%s): %s", cid, str(e)[:120])
        return []
