"""SharkFin / Booster participation notes: closed-form validation + economics."""

from __future__ import annotations

from datetime import date

import numpy as np
import pytest

from fcn.analytics.closed_form import booster_value, sharkfin_no_ko
from fcn.core.daycount import year_fraction
from fcn.core.rng import RNGSpec
from fcn.marketdata.provider import ManualProvider, assemble_snapshot
from fcn.marketdata.volsurface import FlatVolSurface
from fcn.pricing.grid import build_grid
from fcn.pricing.mcengine import MCConfig, MCEngine
from fcn.product.enums import KIStyle
from fcn.product.presets import build_booster, build_sharkfin

TRADE = date(2026, 6, 20)
MAT = date(2027, 6, 18)
SPOT, SIGMA, RATE = 100.0, 0.22, 0.03
ENGINE = MCEngine(config=MCConfig(n_paths=200_000, rng=RNGSpec(method="pseudo")))


def _snap(ts):
    prov = ManualProvider(spots={"X": SPOT}, surfaces={"X": FlatVolSurface(SIGMA)}, rate=RATE)
    return assemble_snapshot(prov, ts, "2026-06-20")


def test_sharkfin_no_ko_matches_closed_form():
    ts = build_sharkfin(tickers=["X"], notional=100.0, trade_date=TRADE, strike_date=TRADE,
                        maturity=MAT, participation=1.0, ko_barrier=10.0, cap=1.30)
    snap = _snap(ts)
    t = year_fraction(TRADE, ts.maturity)
    cf = sharkfin_no_ko(SPOT, 1.0, 1.30, t, SIGMA, RATE)
    res = ENGINE.price(ts, snap)
    assert abs(res.pv - cf) < 4 * res.pv_se + 1e-2, f"MC {res.pv:.3f} vs CF {cf:.3f}"


def test_booster_matches_closed_form():
    ts = build_booster(tickers=["X"], notional=100.0, trade_date=TRADE, strike_date=TRADE,
                       maturity=MAT, participation=1.5, buffer=0.20, cap=1.40)
    snap = _snap(ts)
    t = year_fraction(TRADE, ts.maturity)
    cf = booster_value(SPOT, 1.5, 1.40, 0.20, t, SIGMA, RATE)
    res = ENGINE.price(ts, snap)
    assert abs(res.pv - cf) < 4 * res.pv_se + 1e-2, f"MC {res.pv:.3f} vs CF {cf:.3f}"


def test_sharkfin_ko_lowers_value():
    def pv(ko):
        ts = build_sharkfin(tickers=["X"], notional=100.0, trade_date=TRADE, strike_date=TRADE,
                            maturity=MAT, participation=1.0, ko_barrier=ko, cap=None, coupon_floor=0.0)
        return ENGINE.price(ts, _snap(ts)).pv
    assert pv(1.15) < pv(2.50)  # a nearer knock-out destroys participation -> worth less


def test_booster_buffer_adds_value():
    def pv(buffer):
        ts = build_booster(tickers=["X"], notional=100.0, trade_date=TRADE, strike_date=TRADE,
                           maturity=MAT, participation=1.0, buffer=buffer, cap=1.40)
        return ENGINE.price(ts, _snap(ts)).pv
    assert pv(0.30) > pv(0.10)  # a bigger airbag is worth more


def test_sharkfin_ko_rebate_discounted_at_first_hit():
    """An early up-and-out KO pays par + rebate AT the KO time, so it must be
    discounted from the first-passage step — not from the level's peak (the argmax
    bug) nor from maturity. Crafted path: crosses the 130% barrier early, peaks far
    later, so first-hit / peak / maturity discount factors are all distinct."""
    from fcn.pricing.grid import build_grid
    from fcn.pricing.payoff import PayoffEngine
    from fcn.pricing.pathgen import PathBundle

    floor, ko = 0.10, 1.30
    ts = build_sharkfin(tickers=["X"], notional=100.0, trade_date=TRADE, strike_date=TRADE,
                        maturity=MAT, participation=1.0, ko_barrier=ko, cap=None,
                        coupon_floor=floor, ko_style=KIStyle.AMERICAN)
    # high rate so the discount-factor gap between first-hit, peak and maturity is large
    prov = ManualProvider(spots={"X": SPOT}, surfaces={"X": FlatVolSurface(SIGMA)}, rate=0.12)
    snap = assemble_snapshot(prov, ts, "2026-06-20")
    grid = build_grid(ts)
    spec = PayoffEngine.compile(ts, snap, grid)
    K, mat = len(grid.times), grid.maturity_idx
    cross, peak = 5, mat - 5
    S = np.full((1, K, 1), SPOT)
    S[0, cross:, 0] = 1.35 * SPOT   # first reaches the 130% KO at step `cross`
    S[0, peak, 0] = 1.55 * SPOT     # unique peak -> argmax(level) = peak (much later)
    res = PayoffEngine.evaluate(PathBundle(S=S), spec)
    df = spec.df_grid
    expected = 100.0 * (1.0 + floor) * df[cross]      # rebate discounted at first hit
    assert res.redemption_pv[0] == pytest.approx(expected, rel=1e-9)
    # must NOT be the peak (argmax bug) or maturity (un-corrected) discounting
    assert res.redemption_pv[0] > 100.0 * (1.0 + floor) * df[peak] + 1e-6
    assert res.redemption_pv[0] > 100.0 * (1.0 + floor) * df[mat] + 1e-6


def test_participation_does_not_touch_fcn_path():
    """A SharkFin and an FCN price independently (no cross-contamination)."""
    from fcn.product.presets import build_fcn
    fcn = build_fcn(tickers=["X"], notional=100.0, trade_date=TRADE, strike_date=TRADE,
                    maturity=MAT, coupon_rate=0.08, ki_barrier=0.65, ki_style=KIStyle.EUROPEAN)
    shark = build_sharkfin(tickers=["X"], notional=100.0, trade_date=TRADE, strike_date=TRADE,
                           maturity=MAT, ko_barrier=1.3)
    assert build_grid(shark).daily  # American KO -> daily grid
    assert ENGINE.price(fcn, _snap(fcn)).pv > 0
    assert ENGINE.price(shark, _snap(shark)).pv > 0
