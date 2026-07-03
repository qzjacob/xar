"""CompanyThesis 生成/刷新管线 —— 从平台事实到类型化投资论点(360° 决策对象)。

流程(全部可溯源、宁缺毋滥):
  dossier(cid)  —— 汇编该公司在平台内的**全部**接地事实并给每条一个稳定 id
                  ([event:261] / [chunk:8f2] / [fundamental:cid:revenue] …);
  build(cid)    —— THESIS 任务类(订阅池优先,成本有界)经 complete_json 产出
                  ontology.thesis.CompanyThesis → validate_thesis 纪律校验(证据 id
                  必须存在、conviction 受证据密度约束)→ 不过则带违规清单重试一次,
                  仍不过就**拒绝入库**;通过则版本化写入 company_thesis + thesis_evidence。
  health(cid)   —— 论点健康度:thesis.as_of 之后新到事实按支柱 watch_event_types
                  聚合极性,机器判 confirming / challenging / quiet,零 LLM。

刷新策略:已有版本且此后无新事实 → 跳过(幂等);--force 或新事实 ≥1 → 重建并写
changed_because 差异注记。批量入口 build_batch(theme=…) 供 CLI / 夜批调用。
"""
from __future__ import annotations

import json
from datetime import date

from ..logging import get_logger
from ..models import llm
from ..models.router import TaskClass
from ..ontology.thesis import CompanyThesis, validate_thesis
from ..storage import db

log = get_logger("xar.thesis")

_SYSTEM = """你是一家机构投研平台的研究总监,为覆盖公司维护**类型化投资论点**。纪律(违反即废稿):
1. 每个支柱的每条主张必须引用 dossier 中真实存在的事实 id(格式 kind + ref_id,逐字照抄清单里的 id);禁止编造 id、禁止引用清单之外的"常识"。
2. 主张必须可证伪:写清 falsifier(什么事实出现即推翻)。
3. conviction 与证据密度一致:证据薄弱(总锚点 <5)时 conviction ≤3;coverage_gaps 必须诚实列出 dossier 标注的缺口,不许假装知道。
4. 区分事实与推断:narrative 可以推断,pillar.claim 必须贴着证据;数字尽量进入 claim。
5. 用中文;metric key / event type / id 保持英文原样。"""


# ── dossier ──────────────────────────────────────────────────────────────────
def dossier(cid: str) -> dict | None:
    """汇编接地事实包。返回 {text, known_ids, kpis, coverage_gaps, n_facts, as_of} 或 None。"""
    from ..ingestion.registry import company_by_id
    from ..ontology import coverage360
    from ..ontology.metric_packs import kpis_for_company
    from ..retrieval import graphrag, vector

    c = company_by_id(cid)
    if c is None:
        return None
    known: set[str] = set()
    parts: list[str] = []
    today = date.today().isoformat()

    seg = ", ".join(f"{t}:{s}" for t, s in (c.get("seg") or {}).items())
    parts.append(f"## 公司\n{c['name']} (id={cid}, tickers={','.join(c.get('tickers') or [])}, "
                 f"region={c.get('region')}, chain_role={c.get('chain_role')}, "
                 f"themes={','.join(c.get('themes') or [])}, segments=[{seg}])")
    meta = c.get("meta") or {}
    onto = {k: meta[k] for k in ("cycle", "moat", "one_liner", "kpis") if isinstance(meta, dict) and k in meta}
    if onto:
        parts.append(f"本体注记: {json.dumps(onto, ensure_ascii=False, default=str)[:600]}")

    facts = graphrag.semantic(company_id=cid, limit=40)
    if facts:
        lines = []
        for f in facts:
            fid = f"{'insight' if f['kind'] == 'insight' else 'event'}:{f['id']}"
            known.add(fid)
            lines.append(f"[{fid}] {f['as_of'] or ''} {f['category']} ({f['polarity']}) "
                         f"{str(f['content'])[:160]}"
                         + (f" | 叙事: {str(f['narrative'])[:120]}" if f.get("narrative") else "")
                         + (f" | 兑现: {f['resolution']}" if f.get("resolution") else ""))
        parts.append("## 语义事实(倒序)\n" + "\n".join(lines))

    try:
        sc = graphrag.supply_chain(cid)
        lines = []
        for key in ("suppliers", "customers", "invests_in", "tech_routes", "single_source_risks"):
            for e in (sc.get(key) or [])[:8]:
                eid = e.get("id") or e.get("edge_id")
                tag = f"edge:{eid}" if eid else "registry:supply_chain"
                if eid:
                    known.add(tag)
                lines.append(f"[{tag}] {key}: {json.dumps(e, ensure_ascii=False, default=str)[:150]}")
        if lines:
            parts.append("## 供应链关系\n" + "\n".join(lines))
    except Exception as e:  # noqa: BLE001
        log.warning("dossier supply_chain %s: %s", cid, e)

    rows = db.query(
        "SELECT metric, value, unit, period_end, as_of FROM fundamentals WHERE company_id=%s "
        "ORDER BY metric, period_end DESC NULLS LAST, as_of DESC", (cid,))
    if rows:
        by_metric: dict[str, list] = {}
        for r in rows:
            by_metric.setdefault(r["metric"], []).append(r)
        lines = []
        for m, rs in sorted(by_metric.items()):
            known.add(f"fundamental:{cid}:{m}")
            series = "; ".join(
                f"{r['period_end'] or r['as_of']}={r['value']:g}" for r in rs[:6] if r["value"] is not None)
            lines.append(f"[fundamental:{cid}:{m}] {m} ({rs[0]['unit'] or ''}): {series}")
        parts.append("## 财务(最新在前)\n" + "\n".join(lines[:30]))

    est = db.query("SELECT metric, period, value, as_of FROM estimates WHERE company_id=%s "
                   "ORDER BY as_of DESC LIMIT 12", (cid,))
    if est:
        for r in est:
            known.add(f"estimate:{cid}:{r['metric']}")
        parts.append("## 分析师预期\n" + "\n".join(
            f"[estimate:{cid}:{r['metric']}] {r['metric']} {r['period'] or ''} = {r['value']}"
            for r in est))

    try:
        hits = vector.hybrid_search(f"{c['name']} 投资 论点 竞争 增长 风险 outlook", company_id=cid, k=6)
        if hits:
            lines = []
            for h in hits:
                known.add(f"chunk:{h.chunk_id}")
                lines.append(f"[chunk:{h.chunk_id}] ({h.title or h.source}) {h.text[:220]}")
            parts.append("## 文档段落\n" + "\n".join(lines))
    except Exception as e:  # noqa: BLE001
        log.warning("dossier chunks %s: %s", cid, e)

    # 兑现记录(校准 conviction 的经验输入)
    tr = db.query(
        "SELECT resolution, count(*) AS n FROM kg_events "
        "WHERE company_id=%s AND resolution IN ('hit','miss') GROUP BY 1", (cid,))
    if tr:
        parts.append("## 前瞻声明兑现记录\n" + ", ".join(f"{r['resolution']}={r['n']}" for r in tr))

    # 宏观勾稽(该公司主题关联的 Andy 指标——论点的宏观语境)
    try:
        from ..ontology.macro_links import THEME_TO_METRICS

        mlines = []
        for t in (c.get("themes") or [])[:2]:
            for li in (THEME_TO_METRICS.get(t) or ())[:5]:
                mlines.append(f"[registry:macro:{li.metric_key}] {li.metric_key} — {li.rationale_zh[:80]}")
        if mlines:
            parts.append("## 宏观勾稽指标(语境,可引用为 registry 证据)\n" + "\n".join(mlines))
    except Exception:  # noqa: BLE001
        pass

    kpis = {s.key for s in kpis_for_company(c, include_core=True)}
    gaps = coverage360.gaps_for(cid)
    parts.append(f"## 覆盖缺口(必须回声到 coverage_gaps_zh)\n{', '.join(gaps) or '无重大缺口'}")
    parts.append(f"## 可用 watch_metrics(canonical keys)\n{', '.join(sorted(kpis))}")

    n_facts = len(known)
    return {"text": "\n\n".join(parts), "known_ids": known, "kpis": kpis,
            "coverage_gaps": gaps, "n_facts": n_facts, "as_of": today}


# ── build / store ────────────────────────────────────────────────────────────
def _quality(t: CompanyThesis, known: set[str]) -> dict:
    n_claims = len(t.pillars) + len(t.risks)
    anchored = sum(1 for p in t.pillars if p.evidence) + sum(1 for r in t.risks if r.evidence)
    digits = sum(1 for p in t.pillars if any(ch.isdigit() for ch in p.claim_zh))
    return {"evidence_coverage": round(anchored / n_claims, 3) if n_claims else 0.0,
            "numeric_grounding": round(digits / len(t.pillars), 3) if t.pillars else 0.0,
            "evidence_anchors": sum(len(p.evidence) for p in t.pillars)
                                + sum(len(r.evidence) for r in t.risks),
            "dossier_facts": len(known)}


def _changed_because(prev: dict | None, t: CompanyThesis) -> str:
    if prev is None:
        return "首版"
    notes = []
    if prev.get("stance") != t.stance:
        notes.append(f"stance {prev.get('stance')}→{t.stance}")
    dc = t.conviction - float(prev.get("conviction") or 0)
    if abs(dc) >= 0.5:
        notes.append(f"conviction {prev.get('conviction')}→{t.conviction}")
    prev_pillars = {p.get("key") for p in (prev.get("content") or {}).get("pillars", [])}
    new_pillars = {p.key for p in t.pillars} - prev_pillars
    if new_pillars:
        notes.append(f"新支柱 {','.join(sorted(new_pillars))}")
    return "; ".join(notes) or "证据刷新,结构未变"


def latest(cid: str) -> dict | None:
    rows = db.query(
        "SELECT id, company_id, version, as_of, stance, conviction, one_liner, content, "
        "quality, changed_because, model, created_at FROM company_thesis "
        "WHERE company_id=%s ORDER BY version DESC LIMIT 1", (cid,))
    return rows[0] if rows else None


def _new_facts_since(cid: str, as_of) -> int:
    r = db.query("SELECT count(*) AS n FROM semantic_facts WHERE company_id=%s "
                 "AND COALESCE(as_of, observed_at::date) > %s", (cid, as_of))
    return int(r[0]["n"]) if r else 0


def build(cid: str, *, force: bool = False, quality_tier: bool = False,
          run_id: str | None = None) -> dict:
    """生成/刷新一家公司的论点。返回 {status: built|skipped|rejected|no_data, ...}。"""
    prev = latest(cid)
    if prev and not force and _new_facts_since(cid, prev["as_of"]) == 0:
        return {"status": "skipped", "company_id": cid, "version": prev["version"],
                "reason": "no new facts since last thesis"}
    d = dossier(cid)
    if d is None:
        return {"status": "no_data", "company_id": cid, "reason": "unknown company"}
    if d["n_facts"] < 3:
        return {"status": "no_data", "company_id": cid,
                "reason": f"only {d['n_facts']} grounded facts — 宁缺毋滥"}

    task = TaskClass.EDITOR if quality_tier else TaskClass.THESIS
    prompt = (f"为下述公司生成完整 CompanyThesis(as_of={d['as_of']}):\n\n{d['text']}")
    problems: list[str] = []
    t: CompanyThesis | None = None
    for attempt in (1, 2):
        suffix = ("\n\n上一稿违规,必须修正:\n- " + "\n- ".join(problems)) if problems else ""
        try:
            t = llm.complete_json(prompt + suffix, CompanyThesis, system=_SYSTEM,
                                  task=task, node="thesis", run_id=run_id, max_tokens=6000)
        except Exception as e:  # noqa: BLE001
            return {"status": "rejected", "company_id": cid, "reason": f"llm: {e}"}
        problems = validate_thesis(t, known_evidence_ids=d["known_ids"], known_kpis=d["kpis"])
        if not problems:
            break
        log.warning("thesis %s attempt %d: %d violations", cid, attempt, len(problems))
    if t is None or problems:
        return {"status": "rejected", "company_id": cid, "reason": "; ".join(problems[:6])}

    q = _quality(t, d["known_ids"])
    version = (prev["version"] + 1) if prev else 1
    with db.tx() as conn:
        row = conn.execute(
            "INSERT INTO company_thesis(company_id, version, as_of, stance, conviction, "
            "one_liner, content, quality, changed_because, model, run_id) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s,%s) RETURNING id",
            (cid, version, d["as_of"], t.stance, t.conviction, t.one_liner_zh,
             t.model_dump_json(), json.dumps(q), _changed_because(prev, t),
             "editor-tier" if quality_tier else "thesis-bulk", run_id)).fetchone()
        tid = row[0]
        ev_rows = []
        for p in t.pillars:
            ev_rows += [(tid, p.key, e.kind, e.ref_id, e.quote) for e in p.evidence]
        for r in t.risks:
            ev_rows += [(tid, f"risk:{r.type}", e.kind, e.ref_id, e.quote) for e in r.evidence]
        for er in ev_rows:
            conn.execute("INSERT INTO thesis_evidence(thesis_id, slot, kind, ref_id, quote) "
                         "VALUES (%s,%s,%s,%s,%s)", er)
    log.info("thesis %s v%d: %s conviction=%.1f anchors=%d", cid, version, t.stance,
             t.conviction, q["evidence_anchors"])
    return {"status": "built", "company_id": cid, "version": version, "stance": t.stance,
            "conviction": t.conviction, "quality": q}


def build_batch(theme: str | None = None, *, limit: int | None = None,
                force: bool = False) -> dict:
    """批量生成(幂等走查)。按覆盖度综合分从高到低——先做证据厚的公司。"""
    from ..ingestion.registry import COMPANIES
    from ..models.llm import new_batch_run_id
    from ..ontology import coverage360

    ids = [c["id"] for c in COMPANIES if theme is None or theme in (c.get("themes") or ())]
    cov = coverage360.coverage_all()
    ids.sort(key=lambda i: cov.get(i, {}).get("composite", 0), reverse=True)
    if limit:
        ids = ids[:limit]
    run_id = new_batch_run_id("synth")
    stats = {"built": 0, "skipped": 0, "rejected": 0, "no_data": 0}
    for cid in ids:
        try:
            out = build(cid, force=force, run_id=run_id)
            stats[out["status"]] = stats.get(out["status"], 0) + 1
        except Exception as e:  # noqa: BLE001 — 单司失败不沉批
            log.warning("thesis batch %s: %s", cid, e)
            stats["rejected"] += 1
    stats["run_id"] = run_id
    stats["candidates"] = len(ids)
    return stats


# ── thesis health(零 LLM 的机器复核)────────────────────────────────────────
def health(cid: str) -> dict | None:
    """论点健康度:as_of 之后的新事实按支柱 watch_event_types 聚合极性。"""
    th = latest(cid)
    if th is None:
        return None
    content = th["content"] if isinstance(th["content"], dict) else json.loads(th["content"])
    fresh = db.query(
        "SELECT category, polarity, count(*) AS n FROM semantic_facts "
        "WHERE company_id=%s AND COALESCE(as_of, observed_at::date) > %s "
        "GROUP BY 1, 2", (cid, th["as_of"]))
    by_cat: dict[str, dict[str, int]] = {}
    for r in fresh:
        by_cat.setdefault(r["category"], {})[r["polarity"] or "neutral"] = int(r["n"])
    pillars = []
    sign = {"positive": 1, "negative": -1, "neutral": 0}
    for p in content.get("pillars", []):
        score = n = 0
        for et in p.get("watch_event_types", []):
            for pol, cnt in by_cat.get(et, {}).items():
                score += sign.get(pol, 0) * cnt
                n += cnt
        # 支柱主张方向:score>0 的支柱期待正极性事实
        expect = 1 if p.get("score", 0) >= 0 else -1
        status = "quiet" if n == 0 else ("confirming" if score * expect > 0 else
                                         ("challenging" if score * expect < 0 else "mixed"))
        pillars.append({"key": p.get("key"), "title_zh": p.get("title_zh"),
                        "new_facts": n, "net_polarity": score, "status": status})
    challenged = sum(1 for p in pillars if p["status"] == "challenging")
    overall = ("challenged" if challenged else
               ("confirming" if any(p["status"] == "confirming" for p in pillars) else "quiet"))
    return {"thesis_version": th["version"], "as_of": str(th["as_of"]),
            "overall": overall, "pillars": pillars}
