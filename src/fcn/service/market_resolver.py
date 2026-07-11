"""Resolve a live MarketInput from real data (FMP) — spot + realized vol + correlation + rates,
so the desk / finder / market-read / options pages need NO manual price or vol input.

Implied-vol surfaces (Massive) are not always entitled; the honest, always-available proxy is the
**annualised realized vol** from FMP daily history. Callers should label it "realized" so a desk
knows it is a proxy for implied. Everything degrades gracefully (per-name fallback, never raises the
whole request) and returns the exact ``MarketInput`` shape the pricer consumes.
"""
from __future__ import annotations

import numpy as np

_TRADING_DAYS = 252
_VOL_FALLBACK = 0.28          # used only when a name has too little history
_VOL_FLOOR, _VOL_CAP = 0.08, 1.50


def realized_vol(rets: np.ndarray | None, window: int = 63) -> float | None:
    """Annualised realized vol from log returns (default ~3-month window). None if too little data."""
    if rets is None or len(rets) < 20:
        return None
    r = np.asarray(rets, dtype=float)[-window:]
    if r.size < 20:
        return None
    v = float(np.std(r, ddof=1) * np.sqrt(_TRADING_DAYS))
    return float(np.clip(v, _VOL_FLOOR, _VOL_CAP))


def resolve_market(tickers: list[str], rate: float | None = None,
                   vol_window: int = 63) -> dict:
    """Return a MarketInput dict {source, rate, assets:[{ticker,spot,atm_vol,...}], correlation}
    with REAL spot + realized vol + correlation from FMP. source='manual' because we hand the
    resolved real numbers straight to the pricer (no Massive dependency). Per-name failures fall
    back to sensible defaults; a name we cannot price at all is dropped from ``assets``."""
    from fcn.marketdata.fmp import FMPProvider

    prov = FMPProvider()
    assets: list[dict] = []
    meta: list[dict] = []
    for t in tickers:
        t = t.strip().upper()
        if not t:
            continue
        try:
            spot = float(prov.spot(t))
        except Exception:  # noqa: BLE001 — unresolvable ticker: skip it (caller sees fewer assets)
            meta.append({"ticker": t, "resolved": False})
            continue
        try:
            vol = realized_vol(prov.history_returns(t), window=vol_window) or _VOL_FALLBACK
            vol_src = "realized"
        except Exception:  # noqa: BLE001
            vol, vol_src = _VOL_FALLBACK, "fallback"
        try:
            dy = float(prov.div_yield(t))
        except Exception:  # noqa: BLE001
            dy = 0.0
        try:
            bw = float(prov.borrow(t))
        except Exception:  # noqa: BLE001
            bw = 0.0
        assets.append({"ticker": t, "spot": round(spot, 4), "atm_vol": round(vol, 4),
                       "skew_slope": -0.4, "skew_curv": 0.3, "div_yield": dy, "borrow": bw})
        meta.append({"ticker": t, "resolved": True, "spot": round(spot, 2),
                     "atm_vol": round(vol, 4), "vol_source": vol_src})

    corr = None
    resolved = [a["ticker"] for a in assets]
    if len(resolved) > 1:
        try:
            corr = prov.correlation(resolved).matrix.tolist()
        except Exception:  # noqa: BLE001 — fall back to the uniform rho the caller supplies
            corr = None
    try:
        r = float(rate) if rate is not None else float(prov.risk_free_rate())
    except Exception:  # noqa: BLE001
        r = 0.045
    return {"source": "manual", "rate": round(r, 5), "assets": assets,
            "correlation": corr, "resolved": meta}
