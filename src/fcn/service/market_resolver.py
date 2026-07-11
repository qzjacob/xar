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

# Some dual-class / dotted tickers are gated on FMP's lower tiers (e.g. GOOG returns HTTP 402 while
# GOOGL is free; BRK.B needs the dash form). Resolving a requested ticker to a data-equivalent sibling
# lets the desk keep REAL spot+vol instead of silently degrading the whole basket to a flat assumption
# (which was mis-pricing worst-of baskets — a lower flat vol read as a LOWER coupon for MORE names).
# Index ETFs (except SPY) are also tier-gated, but the RAW index symbols are free — and for vol /
# correlation / trend purposes the index IS the ETF's data-equivalent (levels differ; returns match).
_ALIASES = {
    "GOOG": "GOOGL",   # Alphabet Class C → Class A (same issuer; near-identical price & vol)
    "BRK.B": "BRK-B", "BRK.A": "BRK-A",
    "BF.B": "BF-B", "BF.A": "BF-A",
    "HEI.A": "HEI-A", "LEN.B": "LEN-B",
    # ETF → underlying index (entitled on the current FMP tier; SPY itself is entitled)
    "QQQ": "^IXIC", "QQQM": "^IXIC", "ONEQ": "^IXIC",   # Nasdaq composite as the Nasdaq proxy
    "IWM": "^RUT", "DIA": "^DJI",
    "VOO": "^GSPC", "VTI": "^GSPC", "SPLG": "^GSPC", "IVV": "^GSPC",
}


def _resolve_symbol(prov, ticker: str) -> tuple[str, float]:
    """Resolve ``ticker`` to (effective_symbol, spot), trying the ticker itself, a known dual-class
    alias, then a dot→dash variant. Raises the last error if no candidate is entitled/available."""
    seen: list[str] = []
    last: Exception = RuntimeError(f"no resolvable symbol for {ticker!r}")
    for cand in (ticker, _ALIASES.get(ticker), ticker.replace(".", "-") if "." in ticker else None):
        if not cand or cand in seen:
            continue
        seen.append(cand)
        try:
            return cand, float(prov.spot(cand))
        except Exception as exc:  # noqa: BLE001 — try the next candidate; re-raise below if all fail
            last = exc
    raise last


def realized_vol(rets: np.ndarray | None, window: int = 63) -> float | None:
    """Annualised realized vol from log returns (default ~3-month window). None if too little data."""
    if rets is None or len(rets) < 20:
        return None
    r = np.asarray(rets, dtype=float)[-window:]
    if r.size < 20:
        return None
    v = float(np.std(r, ddof=1) * np.sqrt(_TRADING_DAYS))
    if not np.isfinite(v):   # a zero/negative close upstream → ±inf log-return → NaN vol
        return None
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
    eff_syms: list[str] = []   # effective symbol per resolved asset (for the correlation fetch)
    for t in tickers:
        t = t.strip().upper()
        if not t:
            continue
        try:
            sym, spot = _resolve_symbol(prov, t)   # sym may be an alias of t (e.g. GOOG→GOOGL)
        except Exception:  # noqa: BLE001 — unresolvable ticker: skip it (caller sees fewer assets)
            meta.append({"ticker": t, "resolved": False})
            continue
        try:
            vol = realized_vol(prov.history_returns(sym), window=vol_window) or _VOL_FALLBACK
            vol_src = "realized"
        except Exception:  # noqa: BLE001
            vol, vol_src = _VOL_FALLBACK, "fallback"
        try:
            dy = float(prov.div_yield(sym))
        except Exception:  # noqa: BLE001
            dy = 0.0
        try:
            bw = float(prov.borrow(sym))
        except Exception:  # noqa: BLE001
            bw = 0.0
        # keep the REQUESTED ticker on the asset (the term sheet is keyed by it); record the alias
        assets.append({"ticker": t, "spot": round(spot, 4), "atm_vol": round(vol, 4),
                       "skew_slope": -0.4, "skew_curv": 0.3, "div_yield": dy, "borrow": bw})
        eff_syms.append(sym)
        m = {"ticker": t, "resolved": True, "spot": round(spot, 2),
             "atm_vol": round(vol, 4), "vol_source": vol_src}
        if sym != t:
            m["resolved_as"] = sym
        meta.append(m)

    corr = None
    if len(eff_syms) > 1:
        try:
            corr = prov.correlation(eff_syms).matrix.tolist()
        except Exception:  # noqa: BLE001 — fall back to the uniform rho the caller supplies
            corr = None
    try:
        r = float(rate) if rate is not None else float(prov.risk_free_rate())
    except Exception:  # noqa: BLE001
        r = 0.045
    return {"source": "manual", "rate": round(r, 5), "assets": assets,
            "correlation": corr, "resolved": meta}
