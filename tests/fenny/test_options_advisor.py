"""Advisor: view → ranked candidates + narrative. Tests the deterministic
mapping rules and the LLM/template narrative paths."""

from __future__ import annotations

from datetime import date

import numpy as np
import pytest

from fcn.marketdata.volsurface import FlatVolSurface, ParametricSkewSurface
from fcn.options.advisor import (
    AdvisorResult,
    StrategyCandidate,
    _apply_llm_payload,
    _build_prompt,
    _template_narrative,
    advise,
)
from fcn.options.analytics import SurfaceAnalytics, analyze_surface
from fcn.options.chain import OptionChain
from fcn.options.view import (
    FundamentalView,
    conviction_to_quantity,
    horizon_to_expiry,
    map_view_to_families,
)


@pytest.fixture(scope="module")
def high_vol_setup():
    asof = date(2026, 6, 18)
    surf = ParametricSkewSurface(atm=0.40, slope=-0.5, curv=0.3)
    # Local RNG (no global np.random.seed pollution across tests). Calm for ~10
    # months then a recent vol storm, so the *current* realized vol sits at a
    # high percentile of its own history → high/extreme regime under the
    # (corrected) realized-vs-realized vol_percentile metric.
    rng = np.random.default_rng(42)
    calm = rng.normal(0, 0.008, 230)
    storm = rng.normal(0, 0.032, 30)
    closes = 100 * np.exp(np.cumsum(np.concatenate([calm, storm])))
    chain = OptionChain.abstract("X", 100.0, surf, rate=0.045, div_yield=0.005, asof=asof)
    a = analyze_surface(surf, ticker="X", spot=100.0, rate=0.045,
                        div_yield=0.005, asof=asof, history=closes)
    return chain, a, asof


@pytest.fixture(scope="module")
def low_vol_setup():
    asof = date(2026, 6, 18)
    surf = ParametricSkewSurface(atm=0.15, slope=-0.3, curv=0.2)
    chain = OptionChain.abstract("X", 100.0, surf, rate=0.045, asof=asof)
    a = analyze_surface(surf, ticker="X", spot=100.0, rate=0.045, asof=asof)
    return chain, a, asof


# --- deterministic mapping rules ------------------------------------------

def test_bullish_years_no_holding_prefers_leaps(high_vol_setup):
    chain, a, _ = high_vol_setup
    view = FundamentalView(ticker="X", direction="bullish", horizon="years", conviction=4)
    short = map_view_to_families(view, a)
    assert short[0].name == "long_leaps_call"


def test_bullish_months_high_vol_holding_prefers_collar(high_vol_setup):
    chain, a, _ = high_vol_setup
    view = FundamentalView(ticker="X", direction="bullish", horizon="months",
                           conviction=4, holding_shares=100)
    short = map_view_to_families(view, a)
    assert short[0].name == "collar"


def test_neutral_weeks_spiked_vol_prefers_iron_condor(high_vol_setup):
    chain, a, _ = high_vol_setup
    view = FundamentalView(ticker="X", direction="neutral", horizon="weeks",
                           conviction=3, vol_view="spiked")
    short = map_view_to_families(view, a)
    top_names = [s.name for s in short[:3]]
    assert "iron_condor" in top_names


def test_bearish_weeks_prefers_long_put(low_vol_setup):
    chain, a, _ = low_vol_setup
    view = FundamentalView(ticker="X", direction="bearish", horizon="weeks", conviction=3)
    short = map_view_to_families(view, a)
    assert "long_put" in {s.name for s in short[:3]}


def test_vol_up_view_picks_long_straddle(low_vol_setup):
    chain, a, _ = low_vol_setup
    view = FundamentalView(ticker="X", direction="neutral", horizon="weeks",
                           conviction=3, vol_view="rising")
    short = map_view_to_families(view, a)
    top_names = [s.name for s in short[:4]]
    assert "long_straddle" in top_names or "long_strangle" in top_names


def test_overlay_strategies_penalised_without_holding(high_vol_setup):
    chain, a, _ = high_vol_setup
    view = FundamentalView(ticker="X", direction="bullish", horizon="months", conviction=3,
                           holding_shares=0)
    short = map_view_to_families(view, a)
    names = [s.name for s in short]
    # Covered call / collar should not be in the top 3 without a stock holding.
    assert "covered_call" not in names[:3]
    assert "collar" not in names[:3]


# --- end-to-end advise() with template (no LLM) ---------------------------

def test_advise_template_path(high_vol_setup):
    chain, a, _ = high_vol_setup
    view = FundamentalView(ticker="X", direction="bullish", horizon="months",
                           conviction=4, holding_shares=100, language="zh")
    res = advise(view, chain, a)
    assert res.narrative_source == "template"
    assert len(res.candidates) >= 1
    assert res.candidates[0].fit_score == max(c.fit_score for c in res.candidates)
    # Narrative mentions the ticker.
    assert "X" in res.narrative
    # Each candidate carries a non-empty rationale in template mode.
    for c in res.candidates:
        assert c.rationale


def test_advise_english_template(high_vol_setup):
    chain, a, _ = high_vol_setup
    view = FundamentalView(ticker="X", direction="bearish", horizon="weeks",
                           conviction=2, language="en")
    res = advise(view, chain, a)
    assert res.narrative_source == "template"
    # English narrative should mention "IV" or "vol".
    assert "vol" in res.narrative.lower() or "iv" in res.narrative.lower()


# --- LLM path with mocked caller ------------------------------------------

def test_advise_llm_path(high_vol_setup):
    chain, a, _ = high_vol_setup
    view = FundamentalView(ticker="X", direction="bullish", horizon="months", conviction=4)

    captured = {}

    def mock_caller(prompt, *, system=None):
        captured["prompt"] = prompt
        captured["system"] = system
        return {
            "narrative": "Test narrative for X.",
            "selections": [
                {"name": "long_call", "rationale": "Direct delta exposure."},
            ],
        }

    res = advise(view, chain, a, llm_caller=mock_caller)
    assert res.narrative_source == "llm"
    assert res.narrative == "Test narrative for X."
    # Prompt carries the view, analytics and candidates.
    assert "X" in captured["prompt"]
    # Rationale applied to the named candidate.
    lc = next(c for c in res.candidates if c.spec.name == "long_call")
    assert lc.rationale == "Direct delta exposure."


def test_advise_llm_failure_falls_back_to_template(high_vol_setup):
    """If the LLM returns malformed JSON / None, we fall back to template."""
    chain, a, _ = high_vol_setup
    view = FundamentalView(ticker="X", direction="bullish", horizon="months", conviction=3)

    def mock_caller(prompt, *, system=None):
        return None  # simulates no key / malformed response

    res = advise(view, chain, a, llm_caller=mock_caller)
    assert res.narrative_source == "template"


# --- prompt building -------------------------------------------------------

def test_build_prompt_includes_all_candidates(high_vol_setup):
    chain, a, _ = high_vol_setup
    view = FundamentalView(ticker="X", direction="neutral", horizon="weeks", conviction=3)
    # Build a fake candidate list
    from datetime import date
    from fcn.options.strategies import long_call
    from fcn.options.strategy_engine import value_strategy
    spec = long_call("X", 100, date(2026, 9, 18), 100)
    val = value_strategy(spec, chain)
    cand = StrategyCandidate(spec=spec, valuation=val, fit_score=80.0)
    prompt, system = _build_prompt(view, a, [cand])
    assert "long_call" in prompt
    assert "JSON" in system


def test_apply_llm_payload_parses_selections():
    payload = {
        "narrative": "Top-level summary.",
        "selections": [
            {"name": "iron_condor", "rationale": "Range-bound income."},
            {"name": "long_call", "rationale": "Directional."},
        ],
    }
    narrative, per = _apply_llm_payload(payload, [])
    assert narrative == "Top-level summary."
    assert per == {"iron_condor": "Range-bound income.", "long_call": "Directional."}


# --- horizon/conviction helpers -------------------------------------------

def test_horizon_to_expiry():
    asof = date(2026, 6, 18)
    assert horizon_to_expiry("weeks", asof) == date(2026, 7, 18)
    assert horizon_to_expiry("months", asof) == date(2026, 9, 16)   # 90d
    assert horizon_to_expiry("years", asof) == date(2027, 6, 18)


def test_conviction_to_quantity_in_range():
    for c in (1, 2, 3, 4, 5):
        q = conviction_to_quantity(c)
        assert 1 <= q <= 5
    assert conviction_to_quantity(0) == 1     # clamp
    assert conviction_to_quantity(99) == 5
