"""Market Read — map index vol surfaces to a note-desk view of the market.

Clients of structured notes don't read option Greeks; they want: *is the market a
good place to issue/buy notes right now, and which kind?* This service reads the
**index proxies** (SPY ≈ S&P 500, QQQ ≈ Nasdaq-100) live option surfaces and turns
them into:

  * **metrics** — ATM vol (1M/3M/1Y), term-structure slope, downside put-skew,
    a VIX proxy (SPY 30D ATM IV), realized-vs-implied gap (when history is
    available), and the risk-free rate. All **deterministic and auditable**.
  * **suitability** — transparent rules mapping those metrics to a 0-100 score per
    *supported* product family (FCN/Phoenix/Snowball sell downside vol → favoured by
    high IV + steep skew; SharkFin/Booster buy upside → favoured by low IV), each
    with plain-language drivers.
  * **narrative** — an LLM-written market read *around* the numbers (Claude), with a
    deterministic template fallback when no ``ANTHROPIC_API_KEY`` is set.

Honest scope: indices are ETF proxies; VIX is the SPY 30D ATM IV proxy; Accumulator
(AQ) and other products Fenny does not yet price are **not scored** (the engine only
prices the families above). Dividends/borrow are not needed here.
"""

from __future__ import annotations

import functools

import numpy as np

from fcn.service import llm

_INCOME_FAMILIES = ("FCN", "Phoenix", "Snowball")
_PARTICIPATION_FAMILIES = ("SharkFin", "Booster")


def _clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return float(max(lo, min(hi, x)))


def _index_metrics(provider, ticker: str) -> dict | None:
    """Per-index vol metrics from the surface; ``None`` if no surface is available."""
    try:
        spot = provider.spot(ticker)
    except Exception:  # noqa: BLE001 - missing index -> skip it, not fatal
        return None
    surface = provider.vol_surface(ticker)
    if surface is None:
        return None
    atm_1m = float(surface.atm_vol(1.0 / 12))
    atm_3m = float(surface.atm_vol(0.25))
    atm_1y = float(surface.atm_vol(1.0))
    put_skew_3m = float(surface.implied_vol(np.array([-0.10]), 0.25)[0]) - atm_3m
    out = {
        "ticker": ticker,
        "spot": round(float(spot), 2),
        "atm_1m": round(atm_1m, 4),
        "atm_3m": round(atm_3m, 4),
        "atm_1y": round(atm_1y, 4),
        "term_slope": round(atm_1y - atm_1m, 4),  # >0 contango, <0 backwardation
        "put_skew_3m": round(put_skew_3m, 4),  # extra vol at 90% strike (usually >0)
    }
    closes = getattr(provider, "_daily_closes", None)
    if callable(closes):
        try:
            series = closes(ticker)
            if series is not None and len(series) > 22:
                rets = np.diff(np.log(series[-22:]))
                rv = float(np.std(rets) * np.sqrt(252))
                out["realized_21d"] = round(rv, 4)
                out["iv_rv_gap"] = round(atm_1m - rv, 4)  # >0 = implied richer than realized
        except Exception:  # noqa: BLE001 - realized vol is best-effort
            pass
    return out


def compute_metrics(provider, indices=("SPY", "QQQ")) -> dict:
    """Deterministic market metrics from the index proxies' vol surfaces."""
    per_index = [m for m in (_index_metrics(provider, t) for t in indices) if m is not None]
    if not per_index:
        raise ValueError("no index surfaces available for market read")
    avg = lambda k: float(np.mean([m[k] for m in per_index]))  # noqa: E731
    vix_src = next((m for m in per_index if m["ticker"] == "SPY"), per_index[0])
    return {
        "per_index": per_index,
        "vol_level": round(avg("atm_3m"), 4),  # headline 3M ATM, averaged
        "skew": round(avg("put_skew_3m"), 4),
        "term_slope": round(avg("term_slope"), 4),
        "vix_proxy": round(vix_src["atm_1m"] * 100, 2),  # SPY 30D ATM IV as VIX proxy
        "rate": round(float(provider.risk_free_rate()), 4),
    }


def suitability(metrics: dict) -> dict:
    """Map metrics to a 0-100 score + label + drivers per supported product family.

    Income families (sell a worst-of down-and-in put) are richer when implied vol is
    high and the put skew is steep. Participation families (buy a capped call spread,
    optionally with a buffer) are cheaper to build when implied vol is low.
    """
    vol, skew, term = metrics["vol_level"], metrics["skew"], metrics["term_slope"]

    income_score = _clamp(35 + 230 * (vol - 0.16) + 140 * skew)
    participation_score = _clamp(72 - 200 * (vol - 0.16) + 60 * max(0.0, term))

    income_drivers = [
        f"3M implied vol {vol*100:.0f}% — {'rich, fattens coupons' if vol >= 0.22 else 'subdued, thinner coupons'}",
        f"Put skew +{skew*100:.1f} vol pts at 90% — {'steep, the short down-in put earns more' if skew >= 0.03 else 'shallow, less skew premium to harvest'}",
    ]
    if term < -0.005:
        income_drivers.append("Backwardated term structure — near-dated vol elevated, favors shorter tenors")
    participation_drivers = [
        f"3M implied vol {vol*100:.0f}% — {'cheap optionality to buy upside' if vol < 0.20 else 'pricey upside, caps tighten'}",
        f"Term structure {'contango' if term >= 0 else 'backwardation'} ({term*100:+.1f} vol pts 1Y−1M)",
    ]

    def label(score: float) -> str:
        return "favorable" if score >= 66 else ("neutral" if score >= 40 else "unfavorable")

    out = {}
    for fam in _INCOME_FAMILIES:
        out[fam] = {"score": round(income_score), "label": label(income_score), "drivers": income_drivers}
    for fam in _PARTICIPATION_FAMILIES:
        out[fam] = {
            "score": round(participation_score),
            "label": label(participation_score),
            "drivers": participation_drivers,
        }
    return out


def _template_narrative(metrics: dict, suit: dict) -> str:
    vol, skew, term = metrics["vol_level"], metrics["skew"], metrics["term_slope"]
    top = max(suit.items(), key=lambda kv: kv[1]["score"])
    regime = "elevated" if vol >= 0.24 else ("moderate" if vol >= 0.17 else "subdued")
    ts = "backwardated (near-term stress priced in)" if term < -0.005 else (
        "in contango (calm, upward-sloping)" if term > 0.005 else "broadly flat")
    return (
        f"Index implied volatility is {regime} (3M ATM ~{vol*100:.0f}%, VIX proxy "
        f"{metrics['vix_proxy']:.0f}), with the term structure {ts} and a "
        f"{'steep' if skew >= 0.03 else 'shallow'} downside put skew (+{skew*100:.1f} vol pts at 90%). "
        f"For note issuance this {'richens' if vol >= 0.22 else 'thins'} coupons on income structures "
        f"that sell downside volatility. On balance the most suitable family right now looks like "
        f"{top[0]} ({top[1]['label']}). Risk-free rate ~{metrics['rate']*100:.1f}%. "
        "Indicative only; final terms are struck on the trade date."
    )


def _build_prompt(metrics: dict, suit: dict, lang: str) -> tuple[str, str]:
    lang_line = "Respond in Simplified Chinese." if lang == "zh" else "Respond in English."
    system = (
        "You are a structured-products desk strategist at a private bank. Write a tight, "
        "professional market read for relationship managers selling equity-linked notes "
        "(FCN/Phoenix/Snowball/SharkFin/Booster). Ground every claim in the provided numbers; "
        "do not invent figures. 130-180 words, no bullet lists, no disclaimers. " + lang_line
    )
    idx = "; ".join(
        f"{m['ticker']}: 3M ATM {m['atm_3m']*100:.0f}%, 1Y ATM {m['atm_1y']*100:.0f}%, "
        f"put-skew +{m['put_skew_3m']*100:.1f}pts"
        for m in metrics["per_index"]
    )
    suit_txt = "; ".join(f"{k} {v['score']}/100 ({v['label']})" for k, v in suit.items())
    prompt = (
        f"Market metrics — averaged 3M ATM vol {metrics['vol_level']*100:.0f}%, "
        f"put skew +{metrics['skew']*100:.1f} vol pts, term slope (1Y−1M) {metrics['term_slope']*100:+.1f} pts, "
        f"VIX proxy {metrics['vix_proxy']:.0f}, risk-free {metrics['rate']*100:.1f}%. "
        f"Per index — {idx}. Suitability scores — {suit_txt}. "
        "Write the market read: the vol regime and its likely drivers, whether the short term is "
        "suitable for issuing notes, and which product family fits best and why."
    )
    return system, prompt


def build_market_read(provider, indices=("SPY", "QQQ"), lang: str = "en", llm_caller=None) -> dict:
    """Full Market Read payload: metrics + suitability + narrative (LLM or template)."""
    metrics = compute_metrics(provider, indices)
    suit = suitability(metrics)
    system, prompt = _build_prompt(metrics, suit, lang)
    # 面向客户的措辞 → 用叙述钉扎链(Opus→Codex→GLM→DeepSeek);注入的测试 caller 不受影响。
    caller = (llm_caller if llm_caller is not None
              else functools.partial(llm.generate, narrative=True))
    text = caller(prompt, system=system) if caller is not None else None
    narrative = text if text else _template_narrative(metrics, suit)
    return {
        "metrics": metrics,
        "suitability": suit,
        "narrative": narrative,
        "narrative_source": "llm" if text else "template",
        "indices": list(indices),
    }
