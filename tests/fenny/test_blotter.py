"""Blotter: CRUD, atomic persistence, aggregated Greeks."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from fcn.marketdata.volsurface import ParametricSkewSurface
from fcn.options.blotter import BlotterStore, new_entry
from fcn.options.chain import OptionChain
from fcn.options.strategies import (
    bull_call_spread,
    iron_condor,
    long_call,
)
from fcn.options.strategy_engine import value_strategy


@pytest.fixture
def store(tmp_path):
    return BlotterStore(tmp_path / "blotter.json")


@pytest.fixture
def chain():
    asof = date(2026, 6, 18)
    surf = ParametricSkewSurface(atm=0.30, slope=-0.4, curv=0.3)
    return OptionChain.abstract("X", 100.0, surf, rate=0.045, asof=asof)


def _entry(name, chain):
    asof = date(2026, 6, 18)
    spec_map = {
        "long_call": long_call("X", 100, date(2026, 12, 18), 100),
        "iron_condor": iron_condor("X", 100, date(2026, 9, 18), 85, 95, 105, 115),
        "bull_call_spread": bull_call_spread("X", 100, date(2026, 12, 18), 95, 105),
    }
    spec = spec_map[name]
    val = value_strategy(spec, chain)
    return new_entry(spec, val, notes=f"test {name}")


# --- CRUD -----------------------------------------------------------------

def test_empty_store_returns_empty_list(store):
    assert store.all() == []


def test_add_then_list(store, chain):
    e = _entry("long_call", chain)
    store.add(e)
    listed = store.all()
    assert len(listed) == 1
    assert listed[0].id == e.id
    assert listed[0].strategy.name == "long_call"


def test_remove(store, chain):
    e = _entry("long_call", chain)
    store.add(e)
    assert store.remove(e.id) is True
    assert store.all() == []
    assert store.remove("nonexistent") is False


def test_update_status_and_notes(store, chain):
    e = _entry("iron_condor", chain)
    store.add(e)
    updated = store.update(e.id, status="closed", notes="rolled to next expiry")
    assert updated is not None
    assert updated.status == "closed"
    assert updated.notes == "rolled to next expiry"
    listed = store.all()
    assert listed[0].status == "closed"


def test_update_nonexistent(store):
    assert store.update("nonexistent", status="closed") is None


# --- persistence across instances -----------------------------------------

def test_persistence_across_instances(store, chain, tmp_path):
    """A new BlotterStore at the same path reads what the old one wrote."""
    e = _entry("bull_call_spread", chain)
    store.add(e)
    # New instance, same path:
    store2 = BlotterStore(tmp_path / "blotter.json")
    listed = store2.all()
    assert len(listed) == 1
    assert listed[0].id == e.id


def test_atomic_write_no_corruption_on_missing_parent(tmp_path):
    """The store creates the parent dir if needed."""
    p = tmp_path / "subdir" / "nested" / "blotter.json"
    s = BlotterStore(p)
    assert p.parent.exists()


# --- aggregation ----------------------------------------------------------

def test_aggregate_sums_open_positions(store, chain):
    for name in ("long_call", "iron_condor", "bull_call_spread"):
        store.add(_entry(name, chain))
    agg = store.aggregate()
    assert agg.n_positions == 3
    # Long call has positive delta; iron condor roughly flat; bull call spread positive.
    assert agg.delta > 0
    assert agg.vega != 0
    assert agg.current_pnl is None           # no live revaluation supplied
    # Per-ticker breakdown:
    assert "X" in agg.by_underlying
    assert agg.by_underlying["X"]["n_positions"] == 3


def test_aggregate_skips_closed(store, chain):
    e1 = _entry("long_call", chain)
    e2 = _entry("iron_condor", chain)
    store.add(e1)
    store.add(e2)
    store.update(e1.id, status="closed")
    agg = store.aggregate()
    assert agg.n_positions == 1


def test_aggregate_with_live_pnl(store, chain):
    """When live valuations are supplied, current_pnl is non-None."""
    e = _entry("long_call", chain)
    store.add(e)
    # Build a fresh valuation (simulating a re-mark under new market data).
    live_val = value_strategy(e.strategy, chain)
    agg = store.aggregate(live_valuations={e.id: live_val})
    assert agg.current_pnl is not None
    # On identical market: P&L ~ 0.
    assert abs(agg.current_pnl) < 1.0


def test_aggregate_by_underlying_separates_tickers(store, chain):
    """Positions on different underlyings report per-ticker sub-totals."""
    # Build a second chain on a different ticker.
    asof = date(2026, 6, 18)
    surf = ParametricSkewSurface(atm=0.25, slope=-0.3)
    chain_y = OptionChain.abstract("Y", 50.0, surf, rate=0.04, asof=asof)
    e1 = _entry("long_call", chain)
    e2 = new_entry(
        long_call("Y", 50, date(2026, 9, 18), 50),
        value_strategy(long_call("Y", 50, date(2026, 9, 18), 50), chain_y),
    )
    store.add(e1)
    store.add(e2)
    agg = store.aggregate()
    assert set(agg.by_underlying.keys()) == {"X", "Y"}
