"""Money flow — 谱系不变式 / 序列数学 / market 行幂等 / 事件 dedup / API shape /
Massive 解析(注入 get_json)/ 语义抽取(stub LLM)。全部离线:不外呼、不烧真 LLM;
落库用 2099 期末或即删的合成行,与共享 dev 库隔离。"""
from __future__ import annotations

import datetime as dt

import pytest

from xar.ontology import flow as fo
from xar.research import flow as fl
from xar.storage import db


# ── 谱系不变式 ────────────────────────────────────────────────────────────────
def test_flow_specs_valid():
    from xar.ingestion.registry import THEMES

    keys = [s.key for s in fo.FLOW_SIGNALS]
    assert len(keys) == len(set(keys)) and all(k.startswith("flow.") for k in keys)
    # theme 列身份编码前缀含 ":",注册表主题 id 永不含 ":" → 无冲突
    assert ":" in fo.etf_theme("X") and ":" in fo.pair_theme("A-B")
    assert all(":" not in t for t in THEMES)
    # 取数宇宙覆盖:资产篮全员 + 风格对两腿
    uni = set(fo.FLOW_ETF_UNIVERSE)
    assert {e.ticker for e in fo.ASSET_ETFS} <= uni
    for p in fo.STYLE_PAIRS:
        assert p.long in uni and (p.short is None or p.short in uni)
    # risk-on/off 分组是资产篮成员(面板 drivers 可解释)
    basket = {e.ticker for e in fo.ASSET_ETFS}
    assert set(fo.RISK_ON_TICKERS) <= basket and set(fo.RISK_OFF_TICKERS) <= basket


# ── 序列数学(合成 bars,确定性)────────────────────────────────────────────────
def _mk_bars(n: int = 260) -> list[dict]:
    d0 = dt.date(2098, 1, 1)
    return [{"d": d0 + dt.timedelta(days=i), "close": 100 + i * 0.1,
             "volume": 5000 if i >= n - 5 else 1000} for i in range(n)]


def test_tail_signal_math():
    bars = _mk_bars()
    tail = fl._tail_signals(bars)
    # 63 日动量逐点核对(最后一根)
    d, v = tail["flow.mom_63d"][-1]
    assert d == bars[-1]["d"]
    assert v == round(bars[-1]["close"] / bars[-64]["close"] - 1.0, 4)
    # 尾部放量上涨 → OBV 20日增量对历史突增 → 正 z;全序列 clip ±3
    assert tail["flow.obv_z"][-1][1] > 1.0
    assert tail["flow.dollar_vol_z"][-1][1] > 1.0
    assert all(-3 <= v <= 3 for _, v in tail["flow.obv_z"])
    # 历史不足 → 空(不产出半吊子 z)
    assert fl._tail_signals(bars[:30]) == {"flow.obv_z": [], "flow.dollar_vol_z": [],
                                           "flow.mom_63d": []}


def test_pair_tail_math():
    a = _mk_bars()
    # 两腿同价 → log 比值恒 0 → 变化 z 恒 0
    tail = fl._pair_tail(a, [dict(x) for x in a])
    assert tail and all(v == 0.0 for _, v in tail)
    # 单腿(BTAL 形态)可算且有值
    assert fl._pair_tail(a, None)


# ── market/style 行落库(共享 dev 库,2099 隔离 + 复原)─────────────────────────
@pytest.fixture()
def _clean_sig(seeded_db):
    def wipe():
        db.execute("DELETE FROM alt_signals WHERE signal_key LIKE %s "
                   "AND period_end >= '2099-01-01'", ("flow.%",))
    wipe()
    yield
    wipe()


def test_market_row_upsert_idempotent(_clean_sig):
    pe = dt.date(2099, 1, 15)
    for _ in range(2):                              # 双跑幂等
        fl._put("flow.obv_z", pe, 1.5, theme="etf:TEST99", meta={"ticker": "TEST99"})
        fl._put("flow.risk_on_composite", pe, 0.4)  # company/theme 双 NULL 的 market 单行
    n1 = db.query("SELECT count(*) c FROM alt_signals WHERE signal_key='flow.obv_z' "
                  "AND theme='etf:TEST99'")[0]["c"]
    n2 = db.query("SELECT count(*) c FROM alt_signals WHERE signal_key='flow.risk_on_composite' "
                  "AND company_id IS NULL AND theme IS NULL AND period_end=%s", (pe,))[0]["c"]
    assert n1 == 1 and n2 == 1


def test_sync_flow_events_dedup(seeded_db):
    cid = db.query("SELECT id FROM companies ORDER BY id LIMIT 1")[0]["id"]
    pe = dt.date.today()
    dk = f"flow:flow.obv_z:{cid}:{pe}"
    try:
        fl._put("flow.obv_z", pe, 2.6, company_id=cid, meta={"ticker": "TESTX"})
        fl.sync_flow_events()
        assert db.query("SELECT count(*) c FROM kg_events WHERE dedup_key=%s", (dk,))[0]["c"] == 1
        fl.sync_flow_events()                       # 再跑不复插
        assert db.query("SELECT count(*) c FROM kg_events WHERE dedup_key=%s", (dk,))[0]["c"] == 1
        pol = db.query("SELECT polarity FROM kg_events WHERE dedup_key=%s", (dk,))[0]["polarity"]
        assert pol == "positive"                    # obv good_when=rising, z>0
    finally:
        db.execute("DELETE FROM kg_events WHERE dedup_key=%s", (dk,))
        db.execute("DELETE FROM alt_signals WHERE signal_key='flow.obv_z' "
                   "AND company_id=%s AND period_end=%s", (cid, pe))


# ── API shape ─────────────────────────────────────────────────────────────────
def test_api_flow_shape(seeded_db):
    from fastapi.testclient import TestClient

    from xar.api.app import app

    c = TestClient(app)
    r = c.get("/api/andy/flow")
    assert r.status_code == 200
    body = r.json()
    assert set(body) >= {"as_of", "assets", "styles", "sentiment", "strategy", "themes"}
    assert len(body["assets"]) == len(fo.ASSET_ETFS)
    assert len(body["styles"]) == len(fo.STYLE_PAIRS)
    assert {t["asset_class"] for t in body["strategy"]["tilts"]} >= {"equity_us", "gold"}
    r2 = c.get("/api/andy/flow/series/flow.obv_z", params={"theme": "etf:SPY"})
    assert r2.status_code == 200 and r2.json()["signal_key"] == "flow.obv_z"
    assert c.get("/api/andy/flow/series/nope").status_code == 404


# ── Massive provider(注入 get_json,零外呼)───────────────────────────────────
def test_massive_parsers(monkeypatch):
    from xar.providers import massive as mv

    def fake(url, *, params=None, headers=None, host=None, timeout=30):
        assert "Bearer" in (headers or {}).get("Authorization", "")
        if "short-interest" in url:
            return {"results": [
                {"settlement_date": "2099-01-15", "short_interest": 1000.0,
                 "avg_daily_volume": 100.0},
                {"settlement_date": "2098-12-31", "short_interest": 900.0,
                 "avg_daily_volume": 100.0, "days_to_cover": 9.0}]}
        if "v3/snapshot/options" in url:
            return {"results": [
                {"details": {"contract_type": "put"}, "day": {"volume": 30}, "open_interest": 300},
                {"details": {"contract_type": "call"}, "day": {"volume": 60}, "open_interest": 200}]}
        if "v2/aggs" in url:
            return {"results": [{"t": 4070908800000, "o": 1, "h": 1, "l": 1, "c": 1.0, "v": 10}]}
        return None

    monkeypatch.setattr(mv, "get_json", fake)
    rows = mv.short_interest("XX")
    assert rows[0]["days_to_cover"] == 10.0          # 缺 dtc → si/adv 推导
    assert rows[1]["days_to_cover"] == 9.0           # 有 dtc → 原样
    pc = mv.pc_snapshot("SPY")
    assert pc == {"ticker": "SPY", "pc": 0.5, "basis": "volume", "contracts": 2}

    # 无量 → OI 口径;全缺 → None
    def fake_oi(url, **kw):
        return {"results": [
            {"details": {"contract_type": "put"}, "open_interest": 300},
            {"details": {"contract_type": "call"}, "open_interest": 200}]}
    monkeypatch.setattr(mv, "get_json", fake_oi)
    assert mv.pc_snapshot("SPY")["basis"] == "oi"
    monkeypatch.setattr(mv, "get_json", lambda *a, **k: {"results": []})
    assert mv.pc_snapshot("SPY") is None

    # ETF 日线:available 门 + upsert 计数(不真写库)
    monkeypatch.setattr(mv, "get_json", fake)
    monkeypatch.setattr(mv, "available", lambda: True)
    monkeypatch.setattr(mv.structured, "upsert_prices",
                        lambda cid, t, bars, source="": len(list(bars)))
    out = mv.pull_etf_prices(("SPY",))
    assert out == {"tickers": 1, "bars": 1}
    monkeypatch.setattr(mv, "available", lambda: False)
    assert mv.pull_etf_prices(("SPY",)) == {"skipped": "no MASSIVE_API_KEY"}


# ── Chathy 能力 + worker 源注册 ────────────────────────────────────────────────
def test_capital_flow_capability(seeded_db):
    from xar.capabilities import registry as caps

    spec = next(s for s in caps.CAPABILITIES if s.name == "capital_flow")
    assert spec.chathy and spec.kind == "read"
    out = caps._capital_flow(scope="market")         # Chathy 压缩:序列被剥掉
    assert "assets" in out and all("spark" not in a for a in out["assets"])
    assert all("series" not in s for s in out["styles"])


def test_fetchy_flow_source_registered():
    from xar.orchestration import glm_worker as gw

    assert "flow" in gw.FETCHY_SOURCES
    assert gw.fetchy_defaults()["sources"]["flow"] is True


# ── 语义抽取(stub LLM;真文档行,即删)─────────────────────────────────────────
def test_flow_extract_stubbed(seeded_db, monkeypatch):
    from xar.ingestion.base import Doc, save
    from xar.kg import flow_extract as fx
    from xar.ontology.flow import FlowInsight

    did = save(Doc(company_id=None, source="rss", doc_type="news",
                   title="GS desk: CTA fund flow rotation TESTFLOW99",
                   text="Goldman desk notes CTA fund flow rotation out of megacap tech "
                        "into small caps; positioning stretched, short covering begins.",
                   permission="grey", license_tag="rss"))
    try:
        monkeypatch.setattr(fx.llm, "complete_json", lambda *a, **k: FlowInsight(
            relevant=True, direction="rotation",
            asset_or_sector="out of megacap tech into small caps",
            investor_type="CTA", strength=0.8, horizon="weeks",
            evidence="CTA fund flow rotation"))
        row = dict(db.query("SELECT id, source, title, text, published_at "
                            "FROM documents WHERE id=%s", (did,))[0])
        assert fx.process_document(row) == 1
        ev = db.query("SELECT attrs, polarity FROM kg_events WHERE dedup_key=%s",
                      (f"flowdoc:{did}",))
        assert ev and ev[0]["attrs"]["investor_type"] == "CTA"
        assert ev[0]["polarity"] == "neutral"        # rotation → neutral
        # 幂等盖戳:meta.flow_extract=true → process() 不再选中该文档
        meta = db.query("SELECT meta FROM documents WHERE id=%s", (did,))[0]["meta"]
        assert meta.get("flow_extract") is True
        # 低强度/不相关 → 不入库但仍盖戳(负例不复烧 LLM)
        monkeypatch.setattr(fx.llm, "complete_json",
                            lambda *a, **k: FlowInsight(relevant=False))
        assert fx.process_document(row) == 0
    finally:
        db.execute("DELETE FROM kg_events WHERE dedup_key=%s", (f"flowdoc:{did}",))
        db.execute("DELETE FROM documents WHERE id=%s", (did,))
