"""health_v3 —— 争论感知的论点机器健康度(在 health_v2 之上叠加争论天平 + 相对主张链接)。

三层健康度(互补,逐层增量,旧层保持稳定):
  health    (thesis.py)         事件桶 × 公司情绪极性 → 支柱 confirming/challenging/quiet
  health_v2 (thesis_signals.py) ⊕ 另类信号 z-score 面 → 支柱可被信号面提级 challenging
  health_v3 (本模块)            ⊕ 争论天平(debate.lean_now,来自 LLM 相对主张链接 + VP 数值规则)
                                + 支柱级 LLM 链接只做**升降级**(防与事件桶双计)

裁决口径(THESIS_ONTOLOGY_PLAN §裁决4):争论只用自己的两条道(LLM 链接 + VP 规则)——它在事件桶
道根本不存在,天然无双计;支柱层的 LLM 链接只把 quiet/mixed 提级到 challenging,不参与计分。
天平翻转(flipped)= lean_now 与作者立场反号且 |lean_now|≥0.3 → 喂 challenged_companies_v2 触发重写。
"""
from __future__ import annotations

import json

from ..storage import db

_FLIP_MIN = 0.3           # |lean_now| 达此且反号 → flipped
_CONFIRM_MIN = 0.15       # |lean_now| 达此 → confirming_bull/bear
_PILLAR_FALSIFY_MIN = 0.5 # 支柱 net 证伪强度达此 → quiet/mixed 提级 challenging
_DEBATE_WEIGHT_MIN = 0.3  # 只有权重达此的争论翻转才拉响 overall
_SIGN = {"confirms_bull": 1, "confirms_bear": -1, "neutral": 0}


def _content(row: dict) -> dict:
    c = row["content"]
    return c if isinstance(c, dict) else json.loads(c)


def _clip(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _latest_thesis_rows(where_ids: list[str] | None = None) -> list[dict]:
    """每公司最新版论点行(id/company_id/content);where_ids 给定则只取这些公司。
    单点定义'最新版'语义,免在多处重抄 DISTINCT ON(评审 #14)。"""
    q = "SELECT DISTINCT ON (company_id) company_id, id, content FROM company_thesis"
    params: tuple = ()
    if where_ids is not None:
        q += " WHERE company_id = ANY(%s)"
        params = (where_ids,)
    q += " ORDER BY company_id, version DESC"
    return db.query(q, params)


def debate_health(thesis_id: int, content: dict) -> list[dict]:
    """每个争论只合并自己的两条道 → lean_now、status、top_facts。"""
    debates = content.get("debates") or []
    if not debates:
        return []
    links = db.query(
        "SELECT target_key, verdict, strength, rationale_zh, origin, fact_kind, fact_ref, as_of "
        "FROM thesis_fact_links WHERE thesis_id=%s AND target_kind='debate'", (thesis_id,))
    by_key: dict[str, list[dict]] = {}
    for lk in links:
        by_key.setdefault(lk["target_key"], []).append(lk)

    out: list[dict] = []
    for d in debates:
        rows = by_key.get(d["key"], [])
        llm = [r for r in rows if r["origin"] == "llm" and r["verdict"] != "neutral"]
        rules = [r for r in rows if r["origin"] == "rule"]
        # LLM 道:Σ strength·sign / max(n,3),截断 ±1
        llm_score = 0.0
        if llm:
            s = sum((r["strength"] or 0) * _SIGN.get(r["verdict"], 0) for r in llm)
            llm_score = _clip(s / max(len(llm), 3))
        # VP 规则道:每个 VP(fact_ref='<vpkey>:<metric>:<period_end>')取最新一期,再取均值。
        # 分组键 = 去掉末段 period_end 的前缀(= vpkey:metric,逐 VP 唯一)。
        latest_by_metric: dict[str, dict] = {}
        for r in rules:
            gkey = r["fact_ref"].rsplit(":", 1)[0]
            cur = latest_by_metric.get(gkey)
            if cur is None or (r["as_of"] and cur["as_of"] and r["as_of"] > cur["as_of"]):
                latest_by_metric[gkey] = r
        vp_score = 0.0
        if latest_by_metric:
            vp_score = _clip(sum(_SIGN.get(r["verdict"], 0) for r in latest_by_metric.values())
                             / len(latest_by_metric))
        n = len(llm) + len(latest_by_metric)
        lean_now = _clip(0.6 * llm_score + 0.4 * vp_score)
        authored = float(d.get("lean") or 0.0)
        _w = d.get("weight")                    # 显式 0 = 作者判"无关",不能被 or 吞成 0.5(评审 #9)
        weight = 0.5 if _w is None else float(_w)

        if n == 0:
            status = "quiet"
        elif (abs(lean_now) >= _FLIP_MIN and authored != 0
              and (lean_now > 0) != (authored > 0)):
            status = "flipped"
        elif lean_now >= _CONFIRM_MIN:
            status = "confirming_bull"
        elif lean_now <= -_CONFIRM_MIN:
            status = "confirming_bear"
        else:
            status = "quiet"

        top = sorted(llm, key=lambda r: r["strength"] or 0, reverse=True)[:3]
        out.append({
            "key": d["key"], "question_zh": d.get("question_zh", ""), "weight": weight,
            "lean_authored": authored, "lean_now": round(lean_now, 3),
            "delta": round(lean_now - authored, 3), "status": status,
            "n_facts": n, "vp_readings": [
                {"metric": m.split(":")[-1], "vp": m.split(":")[0], "verdict": r["verdict"],
                 "as_of": r["as_of"], "note": r["rationale_zh"]}
                for m, r in latest_by_metric.items()],
            "top_facts": [{"ref": f"{r['fact_kind']}:{r['fact_ref']}", "verdict": r["verdict"],
                           "strength": r["strength"], "rationale_zh": r["rationale_zh"]} for r in top],
        })
    return out


def _pillar_link_escalation(thesis_id: int, base: dict) -> int:
    """支柱级 LLM 链接:net 证伪 → 把 quiet/mixed 提级 challenging(只升不降,防双计)。"""
    rows = db.query(
        "SELECT target_key, verdict, strength FROM thesis_fact_links "
        "WHERE thesis_id=%s AND target_kind='pillar' AND origin='llm'", (thesis_id,))
    net: dict[str, float] = {}
    for r in rows:
        s = r["strength"] or 0
        net[r["target_key"]] = net.get(r["target_key"], 0.0) + (
            -s if r["verdict"] == "falsifies" else (s if r["verdict"] == "confirms" else 0))
    escalated = 0
    for p in base["pillars"]:
        if net.get(p["key"], 0.0) <= -_PILLAR_FALSIFY_MIN and p["status"] in ("quiet", "mixed"):
            p["status"] = "challenging"
            p["link_escalated"] = True
            escalated += 1
    return escalated


def health_v3(company_id: str) -> dict | None:
    """health_v2 ⊕ 争论天平 ⊕ 支柱链接提级。旧无 debates 的论点 → 形状退化为 v2(+空 debates)。"""
    from . import thesis as th
    from . import thesis_signals as ts

    base = ts.health_v2(company_id)
    if base is None:
        return None
    row = th.latest(company_id)
    content = _content(row)
    debates = debate_health(row["id"], content)
    escalated = _pillar_link_escalation(row["id"], base)

    flipped = [d for d in debates if d["status"] == "flipped" and d["weight"] >= _DEBATE_WEIGHT_MIN]
    if escalated or flipped:
        base["overall"] = "challenged"
    base["debates"] = debates
    base["debate_challenged"] = bool(flipped)
    base["version"] = "v3"
    return base


def theme_debate_health(theme: str) -> dict:
    """主题级争论健康度(零 LLM):成员旗舰同 key 争论的 lean_now 聚合 + 翻转清单。"""
    from ..ingestion.registry import COMPANIES
    from ..ontology.debates import theme_debates_for

    tds = theme_debates_for([theme])
    if not tds:
        return {"theme": theme, "debates": []}
    members = [c["id"] for c in COMPANIES if theme in (c.get("themes") or [])]
    rows = _latest_thesis_rows(members)
    buckets: dict[str, list[tuple[str, dict]]] = {td.key: [] for td in tds}
    for r in rows:
        for d in debate_health(r["id"], _content(r)):
            if d["key"] in buckets:
                buckets[d["key"]].append((r["company_id"], d))
    out = []
    for td in tds:
        scored = buckets[td.key]
        leans = [d["lean_now"] for _, d in scored]
        out.append({
            "key": td.key, "question_zh": td.question_zh,
            "bull_zh": td.bull_zh, "bear_zh": td.bear_zh,
            "members_scored": len(scored),
            "mean_lean": round(sum(leans) / len(leans), 3) if leans else None,
            "flipped": [cid for cid, d in scored if d["status"] == "flipped"],
            "by_company": [{"company_id": cid, "lean_now": d["lean_now"], "status": d["status"]}
                           for cid, d in scored],
        })
    return {"theme": theme, "debates": out}


def challenged_companies_v2(limit: int = 2) -> list[str]:
    """信号面 + 争论翻转面挑战最重的论点(供 glm_worker 重建)。零 LLM。"""
    from . import thesis_signals as ts

    rows = _latest_thesis_rows()
    scored: list[tuple[float, str]] = []
    for r in rows:
        cid = r["company_id"]
        content = _content(r)
        # 信号面 gate 与 health_v2 一致:一个 kind 的负信号只在**该 kind 有权重≥0.15 的支柱**时
        # 才算"被挑战"(评审 #5:否则给非重仓支柱排昂贵 LLM 重建)。
        kind_weight: dict[str, float] = {}
        for p in content.get("pillars", []):
            k = p.get("kind")
            if k:
                kind_weight[k] = max(kind_weight.get(k, 0.0), float(p.get("weight") or 0))
        sig = None
        try:
            ks = ts.pillar_signal_scores(cid)
            bad = [v["score"] for k, v in ks.items()
                   if v["score"] <= ts._CHALLENGE_SCORE and kind_weight.get(k, 0.0) >= 0.15]
            if bad:
                sig = min(bad)
        except Exception:  # noqa: BLE001
            pass
        flips = [d for d in debate_health(r["id"], content)
                 if d["status"] == "flipped" and d["weight"] >= _DEBATE_WEIGHT_MIN]
        sig_bad = sig is not None
        if not sig_bad and not flips:
            continue
        score = sig if sig is not None else 0.0
        if flips:                                   # 翻转的争论优先重建(合成一个强负分)
            score = min(score, -0.6 - max(abs(d["lean_now"]) * d["weight"] for d in flips))
        scored.append((score, cid))
    return [cid for _, cid in sorted(scored)[:limit]]
