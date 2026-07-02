"""Strategy engine: per-structure invariants (MoP/MoL signs, breakeven counts, Greek signs)."""

from __future__ import annotations

from datetime import date

import numpy as np
import pytest

from fcn.marketdata.volsurface import FlatVolSurface, ParametricSkewSurface
from fcn.options.chain import OptionChain
from fcn.options.strategies import (
    bear_put_spread,
    bull_call_spread,
    bull_put_spread,
    calendar_spread,
    cash_secured_put,
    collar,
    covered_call,
    iron_condor,
    long_call,
    long_leaps_call,
    long_put,
    long_straddle,
    long_strangle,
    protective_put,
    risk_reversal,
    short_straddle,
    short_strangle,
    STRATEGY_CATALOG,
)
from fcn.options.strategy_engine import value_strategy

ASOF = date(2026, 6, 18)


@pytest.fixture(scope="module")
def chain():
    surf = ParametricSkewSurface(atm=0.30, slope=-0.4, curv=0.3)
    return OptionChain.abstract("X", 100.0, surf, rate=0.045, div_yield=0.005, asof=ASOF)


def _v(spec, chain):
    return value_strategy(spec, chain)


# --- long-only strategies: max loss = premium paid ------------------------

def test_long_call_max_loss_is_premium(chain):
    v = _v(long_call("X", 100, date(2026, 12, 18), 100), chain)
    assert v.max_loss is not None
    assert abs(v.max_loss - v.net_debit) < 1e-6
    assert v.max_profit is None              # unbounded upside
    assert v.greeks.delta > 0
    assert v.greeks.gamma > 0
    assert v.greeks.vega > 0
    assert v.greeks.theta < 0


def test_long_put_max_loss_is_premium(chain):
    v = _v(long_put("X", 100, date(2026, 12, 18), 100), chain)
    assert v.max_loss is not None and abs(v.max_loss - v.net_debit) < 1e-6
    # Max profit is bounded (spot → 0): strike×100 − premium.
    assert v.max_profit is not None
    assert v.greeks.delta < 0
    assert v.greeks.gamma > 0
    assert v.greeks.vega > 0


# --- short premium strategies: max profit = credit received ---------------

def test_short_straddle_credit_is_max_profit(chain):
    v = _v(short_straddle("X", 100, date(2026, 9, 18), 100), chain)
    assert v.net_debit < 0                   # credit
    assert v.max_profit is not None and abs(v.max_profit - (-v.net_debit)) < 1e-6
    assert v.max_loss is None                # unbounded
    assert v.greeks.delta < 1.0              # roughly flat at ATM
    assert v.greeks.vega < 0


def test_short_strangle_unbounded_loss(chain):
    v = _v(short_strangle("X", 100, date(2026, 9, 18), 105, 95), chain)
    assert v.max_loss is None
    assert v.max_profit is not None


# --- defined-risk spreads: both extremes bounded --------------------------

def test_bull_call_spread_both_bounded(chain):
    v = _v(bull_call_spread("X", 100, date(2026, 12, 18), 95, 105), chain)
    assert v.max_profit is not None and v.max_loss is not None
    assert v.max_profit > 0 and v.max_loss > 0
    assert v.net_debit > 0                   # debit spread
    assert v.greeks.delta > 0


def test_bear_put_spread_both_bounded(chain):
    v = _v(bear_put_spread("X", 100, date(2026, 12, 18), 105, 95), chain)
    assert v.max_profit is not None and v.max_loss is not None
    assert v.net_debit > 0
    assert v.greeks.delta < 0


def test_bull_put_spread_credit(chain):
    v = _v(bull_put_spread("X", 100, date(2026, 9, 18), 100, 95), chain)
    assert v.net_debit < 0                   # credit
    assert v.greeks.delta > 0                # bullish


def test_iron_condor_bounded_with_two_breakevens(chain):
    v = _v(iron_condor("X", 100, date(2026, 9, 18), 85, 95, 105, 115), chain)
    assert v.max_profit is not None and v.max_loss is not None
    assert v.net_debit < 0                   # credit
    assert v.max_profit < v.max_loss         # asymmetric risk/reward on IC
    # Iron condor has two breakevens (inside the put spread and call spread).
    assert len(v.breakevens) == 2
    assert v.breakevens[0] < 100 < v.breakevens[1]


# --- overlay strategies (with stock leg) ----------------------------------

def test_covered_call_net_debit_includes_stock(chain):
    v = _v(covered_call("X", 100, date(2026, 12, 18), 105, shares=100), chain)
    assert v.net_debit > 0                   # cost of stock minus premium
    assert v.greeks.delta > 50               # ~100 shares − small call delta
    assert v.greeks.vega < 0                 # short the call
    assert v.max_profit is not None          # capped by short call
    assert v.max_loss is not None            # stock can fall but is bounded


def test_cash_secured_put_margin_is_capital_at_risk(chain):
    """Margin for a CSP = capital at risk = the (structural) max loss.

    The structural margin rule ties defined-risk margin to the computed
    worst-case loss (strike×100 − credit), not the gross strike notional — the
    premium received is also cash in the account, so net capital posted is the
    max loss. It must equal max_loss and sit just below strike×100.
    """
    v = _v(cash_secured_put("X", 100, date(2026, 9, 18), 95), chain)
    assert v.margin_estimate is not None and v.max_loss is not None
    assert v.margin_estimate == pytest.approx(v.max_loss, rel=1e-9)
    assert 0.90 * 95 * 100 <= v.margin_estimate < 95 * 100


def test_cash_secured_put_max_loss_close_to_analytical(chain):
    """Max loss for a CSP ≈ strike×100 − credit (worst case spot → 0).

    The grid now samples down to spot = 0, so the reported max loss matches the
    analytical value to within interpolation error (not the old 0.05×S floor).
    """
    v = _v(cash_secured_put("X", 100, date(2026, 9, 18), 95), chain)
    credit = abs(v.net_debit)                      # net_debit < 0 for a credit
    analytical_max_loss = 95 * 100 - credit        # strike×100 − credit
    assert v.max_loss is not None
    assert v.max_loss == pytest.approx(analytical_max_loss, rel=0.02)


def test_collar_caps_both_sides(chain):
    v = _v(collar("X", 100, date(2026, 12, 18), 90, 110, shares=100), chain)
    assert v.max_profit is not None and v.max_loss is not None
    assert v.greeks.delta > 0


def test_protective_put_limits_downside(chain):
    base = _v(covered_call("X", 100, date(2026, 12, 18), 105, shares=100), chain)
    pp = _v(protective_put("X", 100, date(2026, 12, 18), 95, shares=100), chain)
    # Protective put adds positive vega vs. covered call's negative.
    assert pp.greeks.vega > base.greeks.vega


# --- calendar/diagonal -----------------------------------------------------

def test_calendar_spread_vega_positive_long_far_leg(chain):
    v = _v(calendar_spread("X", 100, near_expiry=date(2026, 9, 18),
                           far_expiry=date(2027, 6, 18), strike=100), chain)
    assert v.greeks.vega > 0                 # long the far-leg vol


# --- risk reversal / LEAPS -------------------------------------------------

def test_risk_reversal_low_cost_bullish(chain):
    v = _v(risk_reversal("X", 100, date(2026, 12, 18), 105, 95), chain)
    # Funded structure → small net debit/credit.
    assert abs(v.net_debit) < 5 * 100
    assert v.greeks.delta > 0
    assert v.max_profit is None              # long call side unbounded
    assert v.max_loss is not None            # short put floored at strike>0


def test_long_leaps_call_high_vega(chain):
    v = _v(long_leaps_call("X", 100, date(2027, 6, 18), 100), chain)
    # LEAPS has much more vega than a 3-month call (vega scales ~√T → ~2× here).
    v_short = _v(long_call("X", 100, date(2026, 9, 18), 100), chain)
    assert v.greeks.vega > 1.5 * v_short.greeks.vega


# --- breakeven count sanity -----------------------------------------------

@pytest.mark.parametrize("name, spec_builder", [
    ("long_call", lambda: long_call("X", 100, date(2026, 9, 18), 100)),
    ("long_put", lambda: long_put("X", 100, date(2026, 9, 18), 100)),
    ("bull_call_spread", lambda: bull_call_spread("X", 100, date(2026, 9, 18), 95, 105)),
])
def test_single_breakeven_for_mono_directional(name, spec_builder, chain):
    v = _v(spec_builder(), chain)
    assert len(v.breakevens) == 1


# --- prob_profit in (0, 1) ------------------------------------------------

def test_prob_profit_bounded(chain):
    for spec_builder in [
        lambda: long_call("X", 100, date(2026, 9, 18), 100),
        lambda: iron_condor("X", 100, date(2026, 9, 18), 85, 95, 105, 115),
        lambda: short_straddle("X", 100, date(2026, 9, 18), 100),
    ]:
        v = _v(spec_builder(), chain)
        assert 0.0 < v.prob_profit < 1.0


# --- catalog completeness --------------------------------------------------

def test_strategy_catalog_lists_all_factories():
    # Every entry in STRATEGY_CATALOG must correspond to an importable factory.
    import fcn.options.strategies as s
    for name in STRATEGY_CATALOG:
        assert hasattr(s, name), f"missing factory: {name}"


def test_strategy_specs_have_view_tag():
    # All factories assign a view_tag so the advisor can filter them.
    from datetime import date
    cases = [
        long_call("X", 100, date(2026, 9, 18), 100),
        long_put("X", 100, date(2026, 9, 18), 100),
        iron_condor("X", 100, date(2026, 9, 18), 85, 95, 105, 115),
        long_straddle("X", 100, date(2026, 9, 18), 100),
        long_strangle("X", 100, date(2026, 9, 18), 105, 95),
    ]
    for s in cases:
        assert s.view_tag is not None
