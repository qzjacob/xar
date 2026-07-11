"""FastAPI app: quote / solve / greeks / scenario / quote-sheet, plus the SPA.

Synchronous ``/quote`` and ``/solve`` remain for scripting; interactive clients use
the async job endpoints (``/jobs/quote``, ``/jobs/solve``, ``/jobs/{id}``) which
stream the fast PV first and fill Greeks in afterwards (see jobs.py).
"""

from __future__ import annotations

import functools
from datetime import date
from pathlib import Path

import numpy as np
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, Response

from fcn.envtools import load_dotenv

from fcn.api.jobs import JobQueueFull, get_job, new_job, submit, update
from fcn.api.runner import build_snapshot, make_engine, product_context
from fcn.api.schemas import (
    AdvisorRequest,
    BlotterAddRequest,
    BlotterUpdateRequest,
    ChainRequest,
    MarketReadRequest,
    OptionsAnalyzeRequest,
    PresetRequest,
    QuoteRequest,
    RankRequest,
    SolveRequest,
    StrategyBuildRequest,
)
from fcn.marketdata.cache import MARKET_CACHE
from fcn.marketdata.finnhub import FinnhubProvider, FinnhubUnavailable
from fcn.marketdata.fmp import FMPUnavailable
from fcn.marketdata.massive import MassiveUnavailable, MassiveProvider
from fcn.marketdata.provider import ManualProvider
from fcn.marketdata.volsurface import ParametricSkewSurface
from fcn.service.market_read import build_market_read
from fcn.service.ranking import RankStructure, rank_underlyings
from fcn.pricing.fees import FeeModel
from fcn.pricing.greeks import GreeksEngine
from fcn.pricing.solver import solve_coupon
from fcn.product.enums import Frequency, KIStyle, Settlement
from fcn.product.presets import (
    build_booster,
    build_fcn,
    build_phoenix,
    build_sharkfin,
    build_snowball,
)
from fcn.product.termsheet import TermSheet
from fcn.reporting.quotesheet import DISCLAIMER, build_quote_sheet_html, render_pdf
from fcn.service.pricing_service import PricingService

# Equity Options Desk
from fcn.options.advisor import advise
from fcn.options.analytics import SurfaceAnalytics, analyze_surface
from fcn.options.blotter import BlotterStore, new_entry
from fcn.options.chain import OptionChain
from fcn.options.strategy_engine import value_strategy
from fcn.options.strategies import StrategySpec
from fcn.options.view import FundamentalView
from fcn.options.blotter import _valuation_from_dict

load_dotenv()  # pick up MASSIVE / FMP / FINNHUB / ANTHROPIC keys from .env at startup

app = FastAPI(title="FCN Quoter", version="0.1.0")
_FEES = FeeModel()
_STATIC = Path(__file__).parent / "static"


def _reoffer_target(req) -> float:
    """Reoffer (issue) fraction the coupon solves to. If the request carries a Note Price and/or
    Gross Margin (reference-grid FCN columns), the note is struck so that PV = (note_price -
    gross_margin)/100; otherwise fall back to the standard fee model. Keeps those two grid cells
    price-moving (honest) instead of decorative."""
    np_ = getattr(req, "note_price_pct", None)
    gm = getattr(req, "gross_margin_pct", None)
    if np_ is None and gm is None:
        return _FEES.breakdown().reoffer_fraction
    base = np_ if np_ is not None else 100.0
    margin = gm if gm is not None else 0.0
    return max(0.50, (base - margin) / 100.0)


@app.exception_handler(Exception)
async def _unavailable_handler(request, exc):  # noqa: ANN001
    if isinstance(exc, (MassiveUnavailable, FMPUnavailable, FinnhubUnavailable)):
        return JSONResponse(
            status_code=503,
            content={
                "detail": f"Live market data unavailable ({exc}). "
                "Set the relevant API key, switch the data source to Manual, or retry."
            },
        )
    if isinstance(exc, JobQueueFull):
        return JSONResponse(
            status_code=503,
            content={"detail": f"Pricing queue busy ({exc}). Retry shortly."},
        )
    raise exc


def _build_context(req, ts, market, pricing, coupon_label, greeks=None, scenario=None):
    snap = build_snapshot(ts, market)
    fees = _FEES.breakdown()
    engine = make_engine(req.mc)
    svc = PricingService(engine=engine)
    payoff = svc.payoff_diagram(ts, snap)
    return {
        "product": product_context(ts),
        "pricing": pricing.to_dict(),
        "fees": {
            "par": fees.par, "structuring": fees.structuring, "distribution": fees.distribution,
            "hedging_reserve": fees.hedging_reserve, "reoffer": fees.reoffer,
        },
        "payoff_diagram": payoff,
        "scenario_table": scenario,
        "greeks": greeks.to_dict() if greeks else None,
        "coupon_label": coupon_label,
        "market": market.model_dump(),
        "disclaimer": DISCLAIMER,
    }


@app.get("/api/v1/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/api/v1/market/live")
def market_live(tickers: list[str], max_maturity_years: float = 1.5) -> dict:
    """Resolve live spot, real IV skew surface and correlation from Massive.

    Returns spot, a small surface preview (ATM vol per tenor + put/call-wing skew)
    and the correlation matrix so the UI can confirm the live inputs before pricing.
    Returns HTTP 503 if Massive is unavailable. Dividends/borrow stay user inputs.
    """
    prov = MassiveProvider(max_maturity_years=max_maturity_years)
    out = {"source": "live", "assets": [], "surface_source": {}}
    for t in tickers:
        spot = prov.spot(t)
        surf = prov.vol_surface(t)
        info = {"ticker": t, "spot": spot}
        if surf is not None:
            info["atm_6m"] = surf.atm_vol(0.5)
            info["atm_1y"] = surf.atm_vol(1.0)
            info["skew_put_6m"] = float(surf.implied_vol(np.array([-0.2]), 0.5)[0])
            info["skew_call_6m"] = float(surf.implied_vol(np.array([0.1]), 0.5)[0])
            out["surface_source"][t] = "live"
        else:
            out["surface_source"][t] = "parametric_fallback"
        out["assets"].append(info)
    out["correlation"] = prov.correlation(tickers).matrix.tolist()
    return out


@app.post("/api/v1/resolve_market")
def resolve_market_ep(tickers: list[str], rate: float | None = None) -> dict:
    """Resolve a live MarketInput (real spot + realized vol + correlation from FMP) for the given
    tickers — so the desk/finder/market-read/options need no manual price or vol input."""
    from fcn.service.market_resolver import resolve_market

    return resolve_market(tickers, rate=rate)


@app.get("/api/v1/schema/termsheet")
def termsheet_schema() -> dict:
    return TermSheet.model_json_schema()


@app.post("/api/v1/build_termsheet")
def build_termsheet(req: PresetRequest) -> dict:
    """Build a full TermSheet from high-level preset params (reuses presets module)."""
    dates = dict(
        tickers=req.tickers, notional=req.notional,
        trade_date=date.fromisoformat(req.trade_date),
        strike_date=date.fromisoformat(req.strike_date),
        maturity=date.fromisoformat(req.maturity),
    )
    if req.variant == "sharkfin":
        ts = build_sharkfin(
            **dates, participation=req.participation, ko_barrier=req.ko_barrier,
            cap=req.cap, coupon_floor=req.coupon_floor, ko_style=KIStyle(req.ki_style),
        )
    elif req.variant == "booster":
        ts = build_booster(
            **dates, participation=req.participation, buffer=req.buffer, cap=req.cap,
        )
    else:
        common = dict(
            **dates, coupon_rate=req.coupon_rate, frequency=Frequency(req.frequency),
            autocall_barrier=req.autocall_barrier, ki_barrier=req.ki_barrier,
            settlement=Settlement(req.settlement),
        )
        if req.variant == "phoenix":
            ts = build_phoenix(
                **common, step_down_per_period=req.step_down_per_period,
                coupon_barrier=req.coupon_barrier, memory=req.memory, ki_style=KIStyle(req.ki_style),
            )
        elif req.variant == "snowball":
            ts = build_snowball(**common)
        else:
            ts = build_fcn(
                **common, step_down_per_period=req.step_down_per_period,
                ki_style=KIStyle(req.ki_style),
            )
    ts = ts.model_copy(update={"currency": req.currency})
    return ts.model_dump(mode="json")


@app.post("/api/v1/quote")
def quote(req: QuoteRequest) -> dict:
    ts, market = req.termsheet, req.market
    snap = build_snapshot(ts, market)
    engine = make_engine(req.mc)
    svc = PricingService(engine=engine)
    rate = req.coupon_rate if req.coupon_rate is not None else (ts.coupon.rate or 0.0)
    pricing = svc.quote(ts, snap, rate)
    scenario = svc.scenario_table(ts, snap, rate) if req.include_scenario else None
    greeks = GreeksEngine(engine).compute(ts, snap, rate) if req.include_greeks else None
    ctx = _build_context(req, ts, market, pricing, f"{rate*100:.2f}% p.a.", greeks, scenario)
    return {
        "pricing": ctx["pricing"], "fees": ctx["fees"], "payoff_diagram": ctx["payoff_diagram"],
        "scenario_table": scenario, "greeks": ctx["greeks"], "product": ctx["product"],
        "disclaimer": DISCLAIMER,
    }


@app.post("/api/v1/solve")
def solve(req: SolveRequest) -> dict:
    ts, market = req.termsheet, req.market
    snap = build_snapshot(ts, market)
    engine = make_engine(req.mc)
    reoffer = _reoffer_target(req)
    if ts.participation is not None:
        # Participation notes have no coupon to solve — quote at the given terms instead.
        pricing = engine.price(ts, snap, 0.0)
        svc0 = PricingService(engine=engine)
        scenario = svc0.scenario_table(ts, snap, 0.0) if req.include_scenario else None
        greeks0 = GreeksEngine(engine).compute(ts, snap, 0.0) if req.include_greeks else None
        ctx0 = _build_context(req, ts, market, pricing, "n/a (participation)", greeks0, scenario)
        return {
            "coupon_rate": 0.0, "coupon_rate_se": 0.0, "reoffer_fraction": reoffer,
            "pricing": ctx0["pricing"], "fees": ctx0["fees"],
            "payoff_diagram": ctx0["payoff_diagram"], "scenario_table": scenario,
            "greeks": ctx0["greeks"], "product": ctx0["product"], "disclaimer": DISCLAIMER,
        }
    svc = PricingService(engine=engine)
    if req.solve_for == "strike":
        # solve the fair STRIKE at the given coupon (bidirectional); scenario/greeks on the adjusted TS
        from fcn.pricing.solver import _with_strike, solve_strike
        cpn = ts.coupon.rate or 0.0
        ss = solve_strike(engine, ts, snap, cpn, reoffer, couple_ki=req.couple_ki_to_strike)
        ts2 = _with_strike(ts, ss.strike, req.couple_ki_to_strike)
        scenario = svc.scenario_table(ts2, snap, cpn) if req.include_scenario else None
        greeks = GreeksEngine(engine).compute(ts2, snap, cpn) if req.include_greeks else None
        ctx = _build_context(req, ts2, market, ss.pricing, f"strike {ss.strike*100:.1f}%", greeks, scenario)
        return {
            "coupon_rate": cpn, "coupon_rate_se": 0.0, "solved_strike": ss.strike,
            "strike_bracketed": ss.bracketed, "reoffer_fraction": ss.reoffer_fraction,
            "pricing": ctx["pricing"], "fees": ctx["fees"], "payoff_diagram": ctx["payoff_diagram"],
            "scenario_table": scenario, "greeks": ctx["greeks"], "product": ctx["product"],
            "disclaimer": DISCLAIMER,
        }
    sol = solve_coupon(engine, ts, snap, reoffer)
    scenario = (
        svc.scenario_table(ts, snap, sol.coupon_rate) if req.include_scenario else None
    )
    greeks = (
        GreeksEngine(engine).compute(ts, snap, sol.coupon_rate) if req.include_greeks else None
    )
    ctx = _build_context(
        req, ts, market, sol.pricing,
        f"{sol.coupon_rate*100:.2f}% p.a.", greeks, scenario,
    )
    return {
        "coupon_rate": sol.coupon_rate, "coupon_rate_se": sol.coupon_rate_se,
        "infeasible": sol.infeasible,
        "reoffer_fraction": sol.reoffer_fraction, "pricing": ctx["pricing"], "fees": ctx["fees"],
        "payoff_diagram": ctx["payoff_diagram"], "scenario_table": scenario,
        "greeks": ctx["greeks"], "product": ctx["product"], "disclaimer": DISCLAIMER,
    }


@app.post("/api/v1/report/quotesheet")
def quotesheet(req: SolveRequest, fmt: str = "html") -> Response:
    """Render the indicative quote sheet (solve the fair coupon, or price the
    participation note at its given terms — participation notes have no coupon)."""
    ts, market = req.termsheet, req.market
    snap = build_snapshot(ts, market)
    engine = make_engine(req.mc)
    reoffer = _reoffer_target(req)
    if ts.participation is not None:
        pricing, rate, coupon_label = engine.price(ts, snap, 0.0), 0.0, "n/a (participation)"
    else:
        sol = solve_coupon(engine, ts, snap, reoffer)
        pricing, rate, coupon_label = sol.pricing, sol.coupon_rate, f"{sol.coupon_rate*100:.2f}% p.a."
    svc = PricingService(engine=engine)
    scenario = svc.scenario_table(ts, snap, rate)
    greeks = GreeksEngine(engine).compute(ts, snap, rate)
    ctx = _build_context(req, ts, market, pricing, coupon_label, greeks, scenario)
    html = build_quote_sheet_html(ctx)
    if fmt == "pdf":
        pdf = render_pdf(html)
        if pdf is not None:
            return Response(content=pdf, media_type="application/pdf")
    return HTMLResponse(content=html)


def _fees_dict() -> dict:
    f = _FEES.breakdown()
    return {"par": f.par, "structuring": f.structuring, "distribution": f.distribution,
            "hedging_reserve": f.hedging_reserve, "reoffer": f.reoffer}


def _run_job(req, jid: str, solve_mode: bool) -> None:
    """Staged pricing: PV first (status=partial), then scenario, then Greeks (status=done).
    Each stage is published atomically (whole-dict replace under the jobs lock)."""
    ts, market = req.termsheet, req.market
    snap = build_snapshot(ts, market)
    engine = make_engine(req.mc)
    svc = PricingService(engine=engine)
    update(jid, stage="pricing")

    reoffer = _reoffer_target(req)
    ts_eff = ts   # strike-solve replaces this with the strike-adjusted termsheet for all downstream surfaces
    if solve_mode and ts.participation is None and getattr(req, "solve_for", "coupon") == "strike":
        from fcn.pricing.solver import _with_strike, solve_strike
        rate = ts.coupon.rate or 0.0
        couple = getattr(req, "couple_ki_to_strike", False)
        ss = solve_strike(engine, ts, snap, rate, reoffer, couple_ki=couple)
        ts_eff = _with_strike(ts, ss.strike, couple)
        pricing = ss.pricing
        extra = {"coupon_rate": rate, "coupon_rate_se": 0.0, "solved_strike": ss.strike,
                 "strike_bracketed": ss.bracketed, "reoffer_fraction": ss.reoffer_fraction}
    elif solve_mode and ts.participation is None:
        sol = solve_coupon(engine, ts, snap, reoffer)
        rate, pricing = sol.coupon_rate, sol.pricing
        extra = {"coupon_rate": sol.coupon_rate, "coupon_rate_se": sol.coupon_rate_se,
                 "infeasible": sol.infeasible, "reoffer_fraction": sol.reoffer_fraction}
    else:
        rate = 0.0 if ts.participation is not None else (
            req.coupon_rate if (not solve_mode and getattr(req, "coupon_rate", None) is not None)
            else (ts.coupon.rate or 0.0)
        )
        pricing = engine.price(ts, snap, rate)
        extra = {"coupon_rate": rate, "coupon_rate_se": 0.0, "reoffer_fraction": reoffer}

    partial = {
        **extra, "pricing": pricing.to_dict(), "fees": _fees_dict(),
        "payoff_diagram": svc.payoff_diagram(ts_eff, snap), "product": product_context(ts_eff),
        "disclaimer": DISCLAIMER, "scenario_table": None, "greeks": None,
    }
    update(jid, status="partial", stage="scenario", partial=dict(partial))  # PV/ladder ready

    if req.include_scenario:
        partial["scenario_table"] = svc.scenario_table(ts_eff, snap, rate)
        update(jid, stage="greeks", partial=dict(partial))
    if req.include_greeks:
        partial["greeks"] = GreeksEngine(engine).compute(ts_eff, snap, rate).to_dict()
        update(jid, partial=dict(partial))
    update(jid, status="done", stage="done", partial=dict(partial))


@app.post("/api/v1/jobs/quote")
def jobs_quote(req: QuoteRequest) -> dict:
    jid = new_job()
    submit(jid, lambda j: _run_job(req, j, solve_mode=False))
    return {"job_id": jid}


@app.post("/api/v1/jobs/solve")
def jobs_solve(req: SolveRequest) -> dict:
    jid = new_job()
    submit(jid, lambda j: _run_job(req, j, solve_mode=True))
    return {"job_id": jid}


@app.get("/api/v1/jobs/{jid}")
def jobs_get(jid: str) -> dict:
    job = get_job(jid)
    if job is None:
        raise HTTPException(status_code=404, detail="unknown job id")
    return job


# --- Market Read + Underlying Finder (parallel modules) ---

def _parse_asof(asof: str):
    try:
        return date.fromisoformat(asof)
    except (ValueError, TypeError):
        return None


def _manual_provider(assets, rate: float, funding: float | None) -> ManualProvider:
    """ManualProvider from request assets (manual/offline path for ranking + read)."""
    assets = assets or []
    return ManualProvider(
        spots={a.ticker: a.spot for a in assets},
        surfaces={
            a.ticker: ParametricSkewSurface(atm=a.atm_vol, slope=a.skew_slope, curv=a.skew_curv)
            for a in assets
        },
        rate=rate, funding=funding,
        div_yields={a.ticker: a.div_yield for a in assets},
        borrows={a.ticker: a.borrow for a in assets},
    )


def _run_rank_job(req: RankRequest, jid: str) -> None:
    s = req.structure
    structure = RankStructure(
        product=s.product, tenor_months=s.tenor_months, frequency=s.frequency,
        protection_pct=s.protection_pct, strike_pct=s.strike_pct, reoffer_pct=s.reoffer_pct,
        div_yield=s.div_yield, borrow=s.borrow,
    )
    update(jid, stage="screening")
    if req.source == "manual":
        provider = _manual_provider(req.assets, req.rate, req.funding)
        universe = req.tickers or [a.ticker for a in (req.assets or [])]
        use_cache = False
    else:
        years = max(0.5, s.tenor_months / 12.0)
        provider = MassiveProvider(
            rate=req.rate, funding=req.funding, asof=_parse_asof(req.asof), max_maturity_years=years
        )
        # Universe from Finnhub-sourced seed (FMP/Finnhub screeners are paywalled);
        # Massive supplies the per-name barrier vol that drives the coupon.
        universe = MARKET_CACHE.get_or_compute(
            ("universe", req.min_market_cap),
            lambda: FinnhubProvider(rate=req.rate).screen_universe(min_market_cap=req.min_market_cap),
        )
        use_cache = True

    res = rank_underlyings(
        provider, structure, universe=universe, top_n=req.top_n, rank_by=req.rank_by,
        filters=req.filters, max_candidates=req.max_candidates, use_cache=use_cache,
        max_workers=16,  # live ranking is bottlenecked by one option-chain fetch per name
        on_progress=lambda done, total: update(jid, stage=f"ranking {done}/{total}"),
    )
    res["source"] = req.source
    update(jid, status="done", stage="done", partial=res)


def _run_market_read_job(req: MarketReadRequest, jid: str) -> None:
    update(jid, stage="reading")
    if req.source == "manual":
        provider = _manual_provider(req.assets, req.rate, None)
    else:
        provider = MassiveProvider(rate=req.rate, asof=_parse_asof(req.asof), max_maturity_years=1.5)
    res = build_market_read(provider, indices=tuple(req.indices), lang=req.lang)
    res["source"] = req.source
    update(jid, status="done", stage="done", partial=res)


@app.post("/api/v1/jobs/rank")
def jobs_rank(req: RankRequest) -> dict:
    jid = new_job()
    submit(jid, lambda j: _run_rank_job(req, j))
    return {"job_id": jid}


@app.post("/api/v1/jobs/market_read")
def jobs_market_read(req: MarketReadRequest) -> dict:
    jid = new_job()
    submit(jid, lambda j: _run_market_read_job(req, j))
    return {"job_id": jid}


# --- Equity Options Desk (parallel module) --------------------------------
#
# Four async job endpoints reuse the existing job runner, plus synchronous CRUD
# for the local blotter. Live data comes from Massive; manual mode synthesises
# a chain from a parametric skew (the same path used elsewhere for offline tests).

def _parse_asof_options(req):
    """Parse an asof string ('today' or ISO date) for options jobs."""
    if req.asof in (None, "today", ""):
        return date.today()
    try:
        return date.fromisoformat(req.asof)
    except ValueError:
        return date.today()


def _options_ticker_spot(req) -> tuple[str | None, float]:
    """Resolve (ticker, manual-spot) from any options request without falsy-zero
    or AttributeError traps. Ticker prefers the request, then the strategy dict;
    spot prefers the request (only when truthy), then the strategy dict, then 100.
    """
    ticker = getattr(req, "ticker", None)
    strat = getattr(req, "strategy", None) or {}
    if not ticker:
        ticker = strat.get("ticker")
    # When a strategy payload is present, ITS spot is authoritative for the
    # (manual) chain — the spec's strikes are defined relative to it, so building
    # the chain at a different req.spot would price the strikes at the wrong
    # moneyness. Fall back to req.spot, then 100.
    spot = strat.get("spot") or getattr(req, "spot", None) or 100.0
    return ticker, float(spot)


def _options_provider(req, ticker: str, asof: date) -> MassiveProvider:
    """One authenticated provider per job for the options chain (listed options
    discount/forward off the risk-free rate; funding/repo is not modeled here)."""
    return MassiveProvider(
        rate=req.rate,
        div_yields={ticker: req.div_yield}, borrows={ticker: req.borrow},
        asof=asof, max_maturity_years=req.max_maturity_years,
    )


def _build_options_chain(req, *, provider: "MassiveProvider | None" = None) -> "OptionChain":
    """Build an OptionChain from a request — live (Massive) or abstract.

    In live mode the caller may pass a shared ``provider`` so the chain and the
    analytics history don't each construct (and authenticate) a separate client.
    """
    asof = _parse_asof_options(req)
    ticker, spot = _options_ticker_spot(req)
    if getattr(req, "source", "live") == "live":
        prov = provider or _options_provider(req, ticker, asof)
        return OptionChain.from_massive(prov, ticker, rate=req.rate,
                                        div_yield=req.div_yield, borrow=req.borrow,
                                        max_maturity_years=req.max_maturity_years, asof=asof)
    # Manual mode: synthesise from a parametric surface.
    surface = ParametricSkewSurface(atm=req.atm_vol, slope=req.skew_slope, curv=req.skew_curv)
    return OptionChain.abstract(ticker, spot, surface, rate=req.rate,
                                div_yield=req.div_yield, borrow=req.borrow, asof=asof)


def _build_options_analytics(
    req, chain, *, provider: "MassiveProvider | None" = None,
) -> tuple[SurfaceAnalytics, str]:
    """Build analytics from the chain (zero extra chain-fetch).

    Returns ``(analytics, source)`` where source is "chain" (derived from the
    chain's own IV marks), "parametric" (manual-mode fallback), or
    "live-history" (chain surface + live RV history from Massive aggregates).
    Never fabricates — if the chain is too sparse we use the parametric surface
    and label it as such, so the UI can disclose the real provenance.
    """
    ticker = chain.ticker
    # Try building the surface from the chain's own contracts first (free).
    surface = chain.to_surface()
    if surface is not None:
        # Fetch realized-vol history only (single Massive call, best-effort),
        # reusing the chain's provider rather than building a second one.
        history = None
        if getattr(req, "source", "live") == "live":
            prov = provider or _options_provider(req, ticker, _parse_asof_options(req))
            try:
                raw = prov._daily_closes(ticker)
                if raw is not None and len(raw) > 0:
                    history = np.asarray(raw, dtype=float)
            except Exception:  # noqa: BLE001 - history is best-effort
                history = None
        source = "live-history" if history is not None else "chain"
        return analyze_surface(
            surface, ticker=ticker, spot=chain.spot, rate=chain.rate,
            div_yield=chain.div_yield, borrow=chain.borrow,
            asof=chain.asof, history=history,
        ), source
    # Chain too sparse — fall back to parametric (manual-mode path).
    surface = ParametricSkewSurface(atm=req.atm_vol, slope=req.skew_slope, curv=req.skew_curv)
    return analyze_surface(
        surface, ticker=ticker, spot=chain.spot, rate=req.rate,
        div_yield=req.div_yield, borrow=req.borrow, asof=chain.asof,
    ), "parametric"


def _options_job_provider(req) -> "MassiveProvider | None":
    """Build the single shared provider for a live job (None in manual mode)."""
    if getattr(req, "source", "live") != "live":
        return None
    ticker, _ = _options_ticker_spot(req)
    return _options_provider(req, ticker, _parse_asof_options(req))


def _run_options_analyze_job(req: OptionsAnalyzeRequest, jid: str) -> None:
    update(jid, stage="loading chain")
    prov = _options_job_provider(req)
    chain = _build_options_chain(req, provider=prov)
    update(jid, stage="analysing surface")
    analytics, analytics_source = _build_options_analytics(req, chain, provider=prov)
    update(jid, status="done", stage="done", partial={
        "chain": chain.summary(),
        "analytics": analytics.to_dict(),
        "liquidity": chain.liquidity_summary(),
        "source": req.source,
        "analytics_source": analytics_source,
    })


def _run_advisor_job(req: AdvisorRequest, jid: str) -> None:
    update(jid, stage="loading chain")
    prov = _options_job_provider(req)
    chain = _build_options_chain(req, provider=prov)
    update(jid, stage="analysing surface")
    analytics, _ = _build_options_analytics(req, chain, provider=prov)
    update(jid, stage="advising")
    view = FundamentalView(
        ticker=req.ticker, direction=req.direction, horizon=req.horizon,
        conviction=req.conviction, vol_view=req.vol_view,
        risk_budget_pct=req.risk_budget_pct, holding_shares=req.holding_shares,
        income_preference=req.income_preference, language=req.language,
        free_text=req.free_text,
    )
    res = advise(view, chain, analytics, asof=chain.asof)
    update(jid, status="done", stage="done", partial=res.to_dict())


def _run_strategy_build_job(req: StrategyBuildRequest, jid: str) -> None:
    update(jid, stage="loading chain")
    chain = _build_options_chain(req)
    update(jid, stage="valuing strategy")
    spec = StrategySpec.model_validate(req.strategy)
    base = value_strategy(spec, chain)
    out = base.to_dict()
    # Auto-compare a liquidity-optimised variant (snap strikes to fillable
    # contracts) and surface whether it's the better trade after slippage.
    if getattr(req, "optimize_liquidity", True):
        update(jid, stage="comparing liquidity")
        opt = value_strategy(spec, chain, optimize_liquidity=True)
        base_liq, opt_liq = base.liquidity or {}, opt.liquidity or {}
        # Only nudge to a different strike when the REQUESTED one is genuinely
        # thin/untradable (the stated goal) — never chase a trivial modeled gain
        # on an already-fillable strike, which would needlessly change the thesis.
        base_thin = (not base_liq.get("tradable")) or (base_liq.get("score") or 0) < 40
        improved = (
            bool(opt.liquidity_remaps)
            and bool(opt_liq.get("tradable"))
            and base_thin
            and (base.slippage - opt.slippage) > 0.01   # material ≥1¢ saving
        )
        out["optimization"] = {
            "compared": True,
            "recommended": "optimized" if improved else "requested",
            "slippage_saved": round(base.slippage - opt.slippage, 2),
            "requested": {"net_debit": round(base.net_debit, 2),
                          "slippage": round(base.slippage, 2), "liquidity": base.liquidity},
            "optimized": {"net_debit": round(opt.net_debit, 2),
                          "slippage": round(opt.slippage, 2), "liquidity": opt.liquidity,
                          "remaps": opt.liquidity_remaps,
                          "strategy": opt.effective_strategy,
                          "valuation": opt.to_dict()},
        }
    update(jid, status="done", stage="done", partial=out)


def _run_chain_job(req: ChainRequest, jid: str) -> None:
    update(jid, stage="loading chain")
    chain = _build_options_chain(req)
    # Send back the chain in a compact form (UI option-chain viewer).
    contracts = [
        {
            "expiry": c.expiry.isoformat(), "strike": c.strike, "kind": c.kind,
            "iv": c.iv, "last": c.last, "bid": c.bid, "ask": c.ask,
            "volume": c.volume, "open_interest": c.open_interest, "source": c.source,
        }
        for c in chain.contracts
    ]
    update(jid, status="done", stage="done", partial={
        "summary": chain.summary(), "contracts": contracts,
    })


@app.post("/api/v1/jobs/options_analyze")
def jobs_options_analyze(req: OptionsAnalyzeRequest) -> dict:
    jid = new_job()
    submit(jid, lambda j: _run_options_analyze_job(req, j))
    return {"job_id": jid}


@app.post("/api/v1/jobs/options_advise")
def jobs_options_advise(req: AdvisorRequest) -> dict:
    jid = new_job()
    submit(jid, lambda j: _run_advisor_job(req, j))
    return {"job_id": jid}


@app.post("/api/v1/jobs/strategy_build")
def jobs_strategy_build(req: StrategyBuildRequest) -> dict:
    jid = new_job()
    submit(jid, lambda j: _run_strategy_build_job(req, j))
    return {"job_id": jid}


@app.post("/api/v1/jobs/chain")
def jobs_chain(req: ChainRequest) -> dict:
    jid = new_job()
    submit(jid, lambda j: _run_chain_job(req, j))
    return {"job_id": jid}


# --- Blotter CRUD ---------------------------------------------------------

# A host (e.g. XAR) may inject a DB-backed store by setting `blotter_factory` to a
# zero-arg callable returning a BlotterStore-compatible object, then calling
# `_get_blotter.cache_clear()`. Default = the file-backed store (standalone / tests).
blotter_factory = None


@functools.lru_cache(maxsize=1)
def _get_blotter():
    """Lazy singleton — avoids touching ~/.fcn at import time."""
    return (blotter_factory or BlotterStore)()


@app.get("/api/v1/blotter")
def blotter_list() -> dict:
    return {"entries": [e.to_dict() for e in _get_blotter().all()]}


@app.post("/api/v1/blotter")
def blotter_add(req: BlotterAddRequest) -> dict:
    spec = StrategySpec.model_validate(req.strategy)
    # Recompute the valuation server-side from the spec + market inputs so the
    # stored risk snapshot can't be a client-fabricated number. Fall back to a
    # client-supplied valuation only if recomputation fails (e.g. live data out).
    snap = None
    try:
        chain = _build_options_chain(req)
        snap = value_strategy(spec, chain)
    except Exception:  # noqa: BLE001 - degrade to the supplied snapshot, never 500 the add
        if req.valuation is not None:
            snap = _valuation_from_dict(req.valuation)
    if snap is None:
        raise HTTPException(status_code=422,
                            detail="could not value strategy and no valuation supplied")
    entry = new_entry(spec, snap, notes=req.notes)
    _get_blotter().add(entry)
    return {"entry": entry.to_dict()}


# Specific route BEFORE the parameterized /{entry_id} routes — FastAPI matches
# in definition order, so /blotter/greeks must come before /blotter/{entry_id}.
@app.get("/api/v1/blotter/greeks")
def blotter_greeks() -> dict:
    """Aggregate snapshot Greeks across open positions."""
    agg = _get_blotter().aggregate()
    return agg.to_dict()


@app.delete("/api/v1/blotter/{entry_id}")
def blotter_remove(entry_id: str) -> dict:
    removed = _get_blotter().remove(entry_id)
    return {"removed": removed, "id": entry_id}


@app.put("/api/v1/blotter/{entry_id}")
def blotter_update(entry_id: str, body: BlotterUpdateRequest) -> dict:
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    if not fields:
        raise HTTPException(status_code=400, detail="no fields to update")
    updated = _get_blotter().update(entry_id, **fields)
    if updated is None:
        raise HTTPException(status_code=404, detail="entry not found")
    return {"entry": updated.to_dict()}


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    path = _STATIC / "index.html"
    return path.read_text() if path.exists() else "<h1>FCN Quoter</h1><p>SPA not built.</p>"
