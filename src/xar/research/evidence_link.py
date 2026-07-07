"""相对主张的证据链接(语义升级核心)+ 验证点数值检查器。

两条道把新证据回归到论点的争论天平:
  · 语义道(LLM,TaskClass.THESIS_LINK):对每条新 semantic_facts,判断它证实/证伪了哪个
    争论的哪一边(confirms_bull/confirms_bear)或哪个支柱(confirms/falsifies)——**相对主张**,
    与"对公司是利好还是利空"解耦(大客户取消订阅对公司 negative,却 confirms 空方叙事)。
  · 规则道(零 LLM):对带 metric+双阈值的验证点,取最新数值 vs 阈值 → 机器裁决(origin='rule')。

裁决落 thesis_fact_links;health_v3(research/thesis_health.py)据此算 debate.lean_now、判 flipped。
成本纪律:每公司一批(≤20 条新事实)一次 LLM 调用,全走 GLM 订阅池;表本身即游标
(LEFT JOIN 找未链接事实),rebuild 推进 as_of 天然收敛,无重分类风暴。
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from ..logging import get_logger
from ..models import llm
from ..models.router import TaskClass
from ..ontology.thesis import DEBATE_VERDICTS, PILLAR_VERDICTS
from ..storage import db, structured

log = get_logger("xar.evidence_link")

_MAX_FACTS = 20
_VERDICTS = set(DEBATE_VERDICTS) | set(PILLAR_VERDICTS)

_SYSTEM = """你是投研平台的证据裁判。给你一家公司的**核心争论(debate)**与**支柱(pillar)**,
再给你若干条新到事实(新闻/研报/纪要/专家洞察)。逐条判断:这条事实**相对某个论点主张**是证实还是证伪。

铁律:
1. 裁决是**相对主张**的,与"对公司股价利好/利空"无关。反例:"某财富500强弃用其产品转向自研 Agent"——
   对公司是利空,但它 confirms_bear(证实了'被颠覆'的空方叙事)。反过来,"宣布 AI 新品被大客户采用扩容"
   通常 confirms_bull。判 debate 用 confirms_bull / confirms_bear;判 pillar 用 confirms / falsifies。
2. 只对**给定的** target_key(debate.key 或 pillar.key)裁决;target_kind 要与 key 的类型一致。
3. 一条事实可对多个 target 各出一条裁决;与任何论点都无关就不要为它输出(留空)。
4. 拿不准、证据太弱 → verdict=neutral,strength 给低分。禁止硬判、禁止编造。
5. ref_id 必须逐字回抄我给的事实编号(形如 'event:123' / 'insight:45')。strength ∈ [0,1]。"""


class FactLink(BaseModel):
    ref_id: str = Field(description="事实编号,逐字回抄(如 'event:123')")
    target_kind: str = Field(description="debate | pillar")
    target_key: str = Field(description="debate.key 或 pillar.key,逐字来自给定清单")
    verdict: str = Field(description="debate: confirms_bull|confirms_bear|neutral;pillar: confirms|falsifies|neutral")
    strength: float = Field(default=0.5, ge=0, le=1)
    rationale_zh: str = Field(default="", description="一句话:凭什么这么判(相对主张)")


class FactLinkBatch(BaseModel):
    links: list[FactLink] = Field(default_factory=list)


# ── 论点摘要(喂给分类器)─────────────────────────────────────────────────────
def _thesis_brief(content: dict) -> tuple[str, dict[str, str]]:
    """返回 (给 LLM 的论点摘要文本, {target_key: target_kind})。"""
    targets: dict[str, str] = {}
    lines: list[str] = []
    debates = content.get("debates") or []
    if debates:
        lines.append("## 核心争论(debate)")
        for d in debates:
            targets[d["key"]] = "debate"
            vps = "; ".join(vp.get("question_zh", "") for vp in d.get("verification_points", []))
            lines.append(f"- [{d['key']}] {d.get('question_zh', '')}\n"
                         f"  多方: {d.get('bull_zh', '')}\n  空方: {d.get('bear_zh', '')}\n  验证点: {vps}")
    lines.append("## 支柱(pillar)")
    for p in content.get("pillars") or []:
        targets[p["key"]] = "pillar"
        lines.append(f"- [{p['key']}] {p.get('claim_zh', '')}"
                     + (f"  证伪条件: {p['falsifier_zh']}" if p.get("falsifier_zh") else ""))
    return "\n".join(lines), targets


# ── 待链接的新事实(表即游标)─────────────────────────────────────────────────
def _pending_facts(thesis_id: int, cid: str, as_of, limit: int = _MAX_FACTS) -> list[dict]:
    return db.query(
        "SELECT sf.kind, sf.id, sf.category, sf.polarity, sf.content, sf.narrative, "
        "       COALESCE(sf.as_of, sf.observed_at::date) AS fact_date "
        "FROM semantic_facts sf "
        "LEFT JOIN thesis_fact_links l "
        "  ON l.thesis_id=%s AND l.fact_kind=sf.kind AND l.fact_ref=sf.id "
        "WHERE sf.company_id=%s AND COALESCE(sf.as_of, sf.observed_at::date) > %s "
        "  AND l.id IS NULL "
        "ORDER BY COALESCE(sf.as_of, sf.observed_at::date) DESC LIMIT %s",
        (thesis_id, cid, as_of, limit))


def _insert_link(thesis_id, cid, fact_kind, fact_ref, target_kind, target_key,
                 verdict, strength, rationale, origin, model, run_id, as_of,
                 *, refresh: bool = False) -> None:
    # LLM 链接:DO NOTHING(一条事实一经分类不再重判,fact_ref=不可变的事实 id)。
    # 规则道 VP:refresh=True → DO UPDATE,因为 fact_ref='<metric>:<period_end>' 会被同期财报
    # 重述覆盖(如季报预披 0.19 → 正式 10-Q 重述 0.21 跨阈),裁决必须随之刷新(评审 #3)。
    # refresh 时只在 verdict 真变了才写(WHERE 守卫),避免每轮把 created_at 刷成 now() 造成
    # 无谓写放大 + /links 时序抖动(评审 #2/#11)。
    conflict = (
        "DO UPDATE SET verdict=EXCLUDED.verdict, strength=EXCLUDED.strength, "
        "rationale_zh=EXCLUDED.rationale_zh, as_of=EXCLUDED.as_of, created_at=now() "
        "WHERE thesis_fact_links.verdict IS DISTINCT FROM EXCLUDED.verdict"
        if refresh else "DO NOTHING")
    db.execute(
        "INSERT INTO thesis_fact_links(thesis_id, company_id, fact_kind, fact_ref, target_kind, "
        "target_key, verdict, strength, rationale_zh, origin, model, run_id, as_of) "
        "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
        "ON CONFLICT (thesis_id, fact_kind, fact_ref, target_kind, target_key) " + conflict,
        (thesis_id, cid, fact_kind, fact_ref, target_kind, target_key, verdict,
         strength, rationale, origin, model, run_id, as_of))


def link_company(cid: str, thesis_row: dict, *, run_id: str | None = None) -> dict:
    """对一家公司的新事实做相对主张分类,写入 thesis_fact_links。"""
    content = thesis_row["content"]
    facts = _pending_facts(thesis_row["id"], cid, thesis_row["as_of"])
    if not facts:
        return {"company": cid, "facts": 0, "links": 0}
    brief, targets = _thesis_brief(content)
    presented = {f"{f['kind']}:{f['id']}": f for f in facts}
    numbered = "\n".join(
        f"- [{f['kind']}:{f['id']}] ({f['category']}, {f['fact_date']}, polarity={f['polarity']}) "
        f"{(f['narrative'] or f['content'] or '')[:240]}" for f in facts)
    prompt = (f"公司论点:\n{brief}\n\n## 新到事实(逐条判 target + verdict)\n{numbered}")
    try:
        out = llm.complete_json(prompt, FactLinkBatch, system=_SYSTEM,
                                task=TaskClass.THESIS_LINK, node="thesis_link",
                                run_id=run_id, max_tokens=2500)
    except Exception:
        raise            # 让 glm_worker 的额度错误定性接手(RateLimitError → exhausted)
    linked_refs: set[str] = set()
    n = 0
    for lk in out.links:
        f = presented.get(lk.ref_id)
        kind = targets.get(lk.target_key)
        if f is None or kind is None or kind != lk.target_kind or lk.verdict not in _VERDICTS:
            continue                       # 行级宁缺毋滥:非法行静默丢弃(下周期自然重试)
        # verdict 必须匹配 target 类型的词表
        if lk.target_kind == "debate" and lk.verdict not in DEBATE_VERDICTS:
            continue
        if lk.target_kind == "pillar" and lk.verdict not in PILLAR_VERDICTS:
            continue
        _insert_link(thesis_row["id"], cid, f["kind"], f["id"], lk.target_kind, lk.target_key,
                     lk.verdict, lk.strength, lk.rationale_zh, "llm", "thesis-link", run_id,
                     f["fact_date"])
        linked_refs.add(f"{f['kind']}:{f['id']}")
        n += 1
    # 已呈现但无裁决的事实 → 打"已处理"哨兵行,防每轮重复分类(表即游标)
    for ref, f in presented.items():
        if ref not in linked_refs:
            _insert_link(thesis_row["id"], cid, f["kind"], f["id"], "none", "none",
                         "neutral", 0.0, "", "llm", "thesis-link", run_id, f["fact_date"])
    return {"company": cid, "facts": len(facts), "links": n}


def _latest_theses() -> list[dict]:
    return db.query(
        "SELECT DISTINCT ON (company_id) id, company_id, as_of, content "
        "FROM company_thesis ORDER BY company_id, version DESC")


def link_pending(limit_companies: int = 15, *, run_id: str | None = None) -> dict:
    """给待链接新事实最多的若干家有论点公司做分类(订阅池,成本有界)。"""
    ranked = []
    for th in _latest_theses():
        r = db.query(
            "SELECT count(*) c FROM semantic_facts sf "
            "LEFT JOIN thesis_fact_links l ON l.thesis_id=%s AND l.fact_kind=sf.kind AND l.fact_ref=sf.id "
            "WHERE sf.company_id=%s AND COALESCE(sf.as_of, sf.observed_at::date) > %s AND l.id IS NULL",
            (th["id"], th["company_id"], th["as_of"]))
        n = int(r[0]["c"]) if r else 0
        if n:
            ranked.append((n, th))
    ranked.sort(key=lambda x: x[0], reverse=True)
    out = {"companies": 0, "links": 0}
    for _, th in ranked[:limit_companies]:
        res = link_company(th["company_id"], th, run_id=run_id)
        out["companies"] += 1
        out["links"] += res["links"]
    return out


# ── 验证点数值检查器(零 LLM 规则道)──────────────────────────────────────────
def _latest_metric(cid: str, metric: str) -> dict | None:
    # 确定性取值:优先 derived(衍生指标只此一源)→ 最新期 → **权威源优先级**(评审 #4)。
    # 源优先级与 indicators._series 共用同一张表(structured.FUNDAMENTAL_SOURCE_PRIORITY),防漂移
    # (评审 #3);末位再按 source 名保证全序。
    rows = db.query(
        "SELECT value, period_end FROM fundamentals WHERE company_id=%s AND metric=%s "
        "AND value IS NOT NULL AND period_end IS NOT NULL "
        "ORDER BY (source='derived') DESC, period_end DESC, "
        f"{structured.source_priority_sql()} DESC, source DESC LIMIT 1", (cid, metric))
    return rows[0] if rows else None


def _vp_verdict(value: float, direction: str, bull_t, bear_t) -> str:
    if direction == "higher_is_bull":
        if bull_t is not None and value >= bull_t:
            return "confirms_bull"
        if bear_t is not None and value <= bear_t:
            return "confirms_bear"
    else:  # lower_is_bull
        if bull_t is not None and value <= bull_t:
            return "confirms_bull"
        if bear_t is not None and value >= bear_t:
            return "confirms_bear"
    return "neutral"


def check_verification_points(cid: str, thesis_row: dict, *, run_id: str | None = None) -> list[dict]:
    """对每个带 metric+阈值的验证点,最新数值 vs 双阈值 → 规则裁决(write-once per period)。"""
    content = thesis_row["content"]
    results: list[dict] = []
    for d in content.get("debates") or []:
        for vp in d.get("verification_points") or []:
            metric = vp.get("metric")
            bt, br = vp.get("bull_threshold"), vp.get("bear_threshold")
            if not metric or (bt is None and br is None):
                continue
            latest = _latest_metric(cid, metric)
            if latest is None:
                continue
            verdict = _vp_verdict(float(latest["value"]), vp.get("direction", "higher_is_bull"), bt, br)
            # fact_ref 含 vp.key:同一争论下两个 VP 引用同一 metric 时不撞唯一键(评审 #9)
            fref = f"{vp.get('key', '')}:{metric}:{latest['period_end']}"
            _insert_link(thesis_row["id"], cid, "fundamental", fref,
                         "debate", d["key"], verdict, 1.0,
                         f"{metric}={latest['value']:.4g} @ {latest['period_end']}",
                         "rule", "rule", run_id, latest["period_end"], refresh=True)
            results.append({"debate": d["key"], "vp": vp.get("key"), "metric": metric,
                            "value": float(latest["value"]), "verdict": verdict})
    return results


def check_pending(limit_companies: int = 15, *, run_id: str | None = None) -> dict:
    """给有论点的公司跑 VP 数值检查(零 LLM,每轮做)。honor limit_companies(评审 #2)。"""
    out = {"companies": 0, "checks": 0}
    debate_theses = [th for th in _latest_theses() if th["content"].get("debates")]
    for th in debate_theses[:limit_companies]:
        res = check_verification_points(th["company_id"], th, run_id=run_id)
        if res:
            out["companies"] += 1
            out["checks"] += len(res)
    return out
