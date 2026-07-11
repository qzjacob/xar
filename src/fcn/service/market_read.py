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
    # disclose data-equivalent proxies (e.g. QQQ → ^IXIC index) so an index level shown
    # under an ETF ticker is never silent
    resolver = getattr(provider, "resolved_symbol", None)
    if callable(resolver):
        try:
            sym = resolver(ticker)
            if sym and sym != ticker:
                out["resolved_as"] = sym
        except Exception:  # noqa: BLE001
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


def monthly_trend(provider, indices) -> dict | None:
    """Month-over-month trend of the fetched data (择时的原始素材).

    Uses ``provider.monthly_samples`` when available (the FMP live provider) —
    month-end spot + trailing 21d realized vol per index. Returns ``None`` for
    providers without history (manual/tests), in which case timing falls back to
    level-only signals.
    """
    sampler = getattr(provider, "monthly_samples", None)
    if not callable(sampler):
        return None
    per_index = []
    for t in indices:
        try:
            samples = sampler(t)
        except Exception:  # noqa: BLE001 — per-name history failure is not fatal
            samples = []
        if len(samples) >= 2:
            per_index.append({"ticker": t, "samples": samples})
    if not per_index:
        return None

    def _avg_across(fn) -> float:
        return float(np.mean([fn(p["samples"]) for p in per_index]))

    # avg MoM change in realized vol over the last ≤3 transitions (vol pts, fraction)
    def _vol_mom(samples):
        rv = [s["rv21"] for s in samples][-4:]
        return float(np.mean(np.diff(rv))) if len(rv) >= 2 else 0.0

    # ~3-month price return (last vs 3 month-ends back, clamped by available months)
    def _px_3m(samples):
        px = [s["spot"] for s in samples]
        base = px[-4] if len(px) >= 4 else px[0]
        return px[-1] / base - 1.0 if base else 0.0

    return {
        "per_index": per_index,
        "vol_now": _avg_across(lambda s: s[-1]["rv21"]),
        "vol_mom": round(_avg_across(_vol_mom), 4),
        "px_3m": round(_avg_across(_px_3m), 4),
        "months": [s["month"] for s in per_index[0]["samples"]],
    }


def timing_view(metrics: dict, trend: dict | None, lang: str = "en") -> dict:
    """择时 — per product family: enter now / wait / neutral, from monthly trends.

    Deterministic and auditable (the LLM narrative colours it, never overrides it):
      * income notes (sell downside vol): high vol = rich coupons; vol *fading from a
        spike* is the classic entry (lock the coupon before it fades); vol still
        spiking or a deep 3m drawdown = falling knife → wait.
      * participation notes (buy upside): low/falling vol = cheap optionality;
        positive momentum favours upside participation.
    """
    zh = lang == "zh"
    vol = metrics["vol_level"]
    vol_mom = (trend or {}).get("vol_mom")
    px_3m = (trend or {}).get("px_3m")

    income = 50.0 + 180.0 * (vol - 0.18)
    part = 50.0 - 200.0 * (vol - 0.18)
    inc_drv: list[str] = [
        (f"3M波动率 {vol*100:.0f}%——{'票息丰厚' if vol >= 0.22 else '票息一般' if vol >= 0.16 else '票息偏薄'}" if zh
         else f"3M vol {vol*100:.0f}% — {'rich coupons' if vol >= 0.22 else 'moderate coupons' if vol >= 0.16 else 'thin coupons'}"),
    ]
    part_drv: list[str] = [
        (f"3M波动率 {vol*100:.0f}%——{'期权便宜,上行参与成本低' if vol < 0.20 else '期权偏贵,封顶更紧'}" if zh
         else f"3M vol {vol*100:.0f}% — {'cheap upside optionality' if vol < 0.20 else 'pricey upside, tighter caps'}"),
    ]
    if vol_mom is not None:
        if vol_mom < -0.005:
            income += 12
            inc_drv.append("月度波动率回落中——趁高锁定票息的窗口" if zh
                           else "Vol fading month-over-month — window to lock coupons before they thin")
            part += 8
            part_drv.append("波动率回落——期权成本在下降" if zh else "Vol fading — option costs easing")
        elif vol_mom > 0.01:
            income -= 12
            inc_drv.append("波动率逐月抬升——或有进一步下行风险,宜观望" if zh
                           else "Vol building month-over-month — possible further downside, patience pays")
            part -= 8
            part_drv.append("波动率抬升——上行期权变贵" if zh else "Vol building — upside options getting pricier")
    if px_3m is not None:
        if px_3m < -0.08:
            income -= 15
            inc_drv.append(f"近3月指数回撤 {px_3m*100:.0f}%——敲入风险抬升" if zh
                           else f"3m index drawdown {px_3m*100:.0f}% — knock-in risk elevated")
        elif px_3m > 0.02:
            income += 6
            part += 10
            part_drv.append(f"近3月上涨 {px_3m*100:+.0f}%——动能利于上行参与" if zh
                            else f"3m momentum {px_3m*100:+.0f}% — favours upside participation")
        elif px_3m < -0.05:
            part -= 8

    def _stance(score: float) -> tuple[str, str]:
        if score >= 66:
            return "enter_now", ("现在配置" if zh else "enter now")
        if score <= 40:
            return "wait", ("观望" if zh else "wait")
        return "neutral", ("中性" if zh else "neutral")

    out: dict = {}
    for fam in _INCOME_FAMILIES:
        stance, label = _stance(income)
        out[fam] = {"stance": stance, "label": label, "score": round(_clamp(income)), "drivers": inc_drv}
    for fam in _PARTICIPATION_FAMILIES:
        stance, label = _stance(part)
        out[fam] = {"stance": stance, "label": label, "score": round(_clamp(part)), "drivers": part_drv}
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


def _build_prompt(
    metrics: dict, suit: dict, lang: str,
    trend: dict | None = None, timing: dict | None = None,
) -> tuple[str, str]:
    lang_line = "Respond in Simplified Chinese." if lang == "zh" else "Respond in English."
    has_trend = bool(trend)
    system = (
        "You are a structured-products desk strategist at a private bank. Write a tight, "
        "professional market read for relationship managers selling equity-linked notes "
        "(FCN/Phoenix/Snowball/SharkFin/Booster). Ground every claim in the provided numbers; "
        "do not invent figures. "
        + ("170-260 words in two parts: (1) the market read; (2) a timing (择时) section that "
           "compares the month-over-month trends and gives a one-line 1-3 month timing view per "
           "product family (enter now / wait / neutral), consistent with the timing scores given. "
          if has_trend else "130-180 words, ")
        + "No bullet lists, no disclaimers. " + lang_line
    )
    idx = "; ".join(
        f"{m['ticker']}: 3M ATM {m['atm_3m']*100:.0f}%, 1Y ATM {m['atm_1y']*100:.0f}%, "
        f"put-skew +{m['put_skew_3m']*100:.1f}pts"
        for m in metrics["per_index"]
    )
    suit_txt = "; ".join(f"{k} {v['score']}/100 ({v['label']})" for k, v in suit.items())
    basis = metrics.get("vol_basis", "implied")
    prompt = (
        f"Market metrics ({basis} vol basis) — averaged 3M ATM vol {metrics['vol_level']*100:.0f}%, "
        f"put skew +{metrics['skew']*100:.1f} vol pts, term slope (1Y−1M) {metrics['term_slope']*100:+.1f} pts, "
        f"VIX proxy {metrics['vix_proxy']:.0f}, risk-free {metrics['rate']*100:.1f}%. "
        f"Per index — {idx}. Suitability scores — {suit_txt}."
    )
    if has_trend:
        rows = []
        for p in trend["per_index"]:
            series = ", ".join(f"{s['month']}: px {s['spot']:.0f} / rv {s['rv21']*100:.0f}%"
                               for s in p["samples"])
            rows.append(f"{p['ticker']} — {series}")
        tm = "; ".join(f"{k} {v['label']} ({v['score']})" for k, v in (timing or {}).items())
        prompt += (
            f" Month-end trend (spot / trailing 21d realized vol) — {' | '.join(rows)}. "
            f"Aggregate: vol MoM {trend['vol_mom']*100:+.1f} pts, 3m price move {trend['px_3m']*100:+.1f}%. "
            f"Deterministic timing scores — {tm}."
        )
    prompt += (
        " Write the market read: the vol regime and its likely drivers, whether the short term is "
        "suitable for issuing notes, and which product family fits best and why."
        + (" Then the timing section per product family grounded in the monthly trends." if has_trend else "")
    )
    return system, prompt


def build_market_read(provider, indices=("SPY", "QQQ"), lang: str = "en", llm_caller=None) -> dict:
    """Full Market Read payload: metrics + suitability + monthly trend + 择时 timing +
    narrative (LLM or template)."""
    metrics = compute_metrics(provider, indices)
    metrics["vol_basis"] = getattr(provider, "vol_basis", "implied")
    suit = suitability(metrics)
    trend = monthly_trend(provider, indices)        # None for providers without history
    timing = timing_view(metrics, trend, lang)
    system, prompt = _build_prompt(metrics, suit, lang, trend=trend, timing=timing)
    # 面向客户的措辞 → 用叙述钉扎链(Opus→Codex→GLM→DeepSeek);注入的测试 caller 不受影响。
    # max_tokens=6000:链里的 GLM-5.2/DeepSeek 是思考型模型,700 的预算会被思考耗尽而
    # 返回空文(实测 2200 仍空、6000 正常)——预算只设上限,非思考模型不受影响。
    caller = (llm_caller if llm_caller is not None
              else functools.partial(llm.generate, narrative=True, max_tokens=6000))
    text = caller(prompt, system=system) if caller is not None else None
    narrative = text if text else _template_narrative(metrics, suit)
    return {
        "metrics": metrics,
        "suitability": suit,
        "trend": trend,
        "timing": timing,
        "narrative": narrative,
        "narrative_source": "llm" if text else "template",
        "indices": list(indices),
    }
