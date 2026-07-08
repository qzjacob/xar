"""ET-P3:裁决引擎 —— v1 入库 / 锁定 skipped / force→v2 / 幻觉 id rejected / host_only deferred。
mock dossier + complete_json,不发真 LLM。"""
from __future__ import annotations

import datetime as dt

import pytest

from xar.models import llm
from xar.ontology.earnings_events import EARNINGS_DIMENSIONS, DimensionRead, EarningsVerdict
from xar.research import earnings
from xar.storage import db

_EV = dt.date(2099, 6, 30)
_EVENT = {"id": 77777, "company_id": "now", "scheduled_for": _EV, "meta": {"session": "amc"},
          "event_type": "earnings"}
_KNOWN = {f"estimate:now:m{i}" for i in range(6)} | {"calendar:77777"}


def _verdict(direction="long", conviction=7.5, ghost=False):
    ev = ["estimate:now:m%d" % i for i in range(6)]
    if ghost:
        ev = ["estimate:now:GHOST"]
    dims = [DimensionRead(key=k, score=1.0, note_zh="x", evidence=[ev[i % len(ev)]])
            for i, k in enumerate(EARNINGS_DIMENSIONS[:6])]
    return EarningsVerdict(direction=direction, conviction=conviction, expected_surprise_zh="beat",
                           move_view_zh="implied 便宜", dimensions=dims, plan_zh="T-3 进",
                           falsifiers_zh=["指引下修"], asymmetry_zh="下行有限上行大")


@pytest.fixture()
def _clean(seeded_db, monkeypatch):
    def wipe():
        db.execute("DELETE FROM earnings_verdicts WHERE company_id='now' AND event_date=%s", (_EV,))
    wipe()
    monkeypatch.setattr(earnings, "dossier_earnings", lambda cid, ev: {
        "text": "d", "known_ids": _KNOWN, "panel": {}, "as_of": dt.date.today().isoformat(),
        "event_date": str(_EV), "n_facts": 8})
    monkeypatch.setattr(earnings, "_implied_series_for", lambda cid, ed: [{"value": 0.08}])
    monkeypatch.setattr(earnings, "_preferred_pin", lambda: None)   # 无订阅执行器 → 裸路由
    yield
    wipe()


def test_v1_built_and_locked_then_force_v2(_clean, monkeypatch):
    monkeypatch.setattr(llm, "complete_json", lambda *a, **k: _verdict())
    r1 = earnings.build_verdict("now", event=_EVENT)
    assert r1["status"] == "built" and r1["version"] == 1 and r1["direction"] == "long"
    row = db.query("SELECT expected_move, conviction FROM earnings_verdicts "
                   "WHERE company_id='now' AND event_date=%s AND version=1", (_EV,))
    assert row and abs(float(row[0]["expected_move"]) - 0.08) < 1e-6

    # 重跑 → 锁定 skipped
    r2 = earnings.build_verdict("now", event=_EVENT)
    assert r2["status"] == "skipped"

    # force → v2
    r3 = earnings.build_verdict("now", event=_EVENT, force=True)
    assert r3["status"] == "built" and r3["version"] == 2
    n = db.query("SELECT count(*) c FROM earnings_verdicts WHERE company_id='now' AND event_date=%s",
                 (_EV,))[0]["c"]
    assert n == 2


def test_hallucinated_id_rejected_not_inserted(_clean, monkeypatch):
    monkeypatch.setattr(llm, "complete_json", lambda *a, **k: _verdict(ghost=True))
    r = earnings.build_verdict("now", event=_EVENT)
    assert r["status"] == "rejected"
    n = db.query("SELECT count(*) c FROM earnings_verdicts WHERE company_id='now' AND event_date=%s",
                 (_EV,))[0]["c"]
    assert n == 0


def test_host_only_defers_without_executor(_clean, monkeypatch):
    from xar.config import get_settings

    monkeypatch.setattr(get_settings(), "earnings_verdict_host_only", True, raising=False)
    monkeypatch.setattr(earnings, "_preferred_pin", lambda: None)
    called = {"llm": False}
    monkeypatch.setattr(llm, "complete_json",
                        lambda *a, **k: called.update(llm=True) or _verdict())
    r = earnings.build_verdict("now", event=_EVENT)
    assert r["status"] == "deferred_host"
    assert called["llm"] is False       # 未发 LLM 调用
