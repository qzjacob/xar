"""LLM advisor: view → ranked strategy candidates + narrative.

Two stages, mirroring :mod:`fcn.service.market_read`:

  1. **Deterministic** — :func:`fcn.options.view.map_view_to_families` short-
     lists the top-3 strategy families from the catalog. Each is then
     *parameterised* against the live chain (ATM strike for straddles, 25Δ for
     wings, the appropriate expiry for the view's horizon) and valued by
     :func:`fcn.options.strategy_engine.value_strategy`.

  2. **LLM** — given the view + analytics + the 3 valued candidates, Claude is
     asked to (a) pick 1-3 to recommend, (b) write a per-candidate rationale in
     the user's language, (c) write a top-level narrative. It cannot invent new
     structures or change the numbers. With no key, a deterministic template
     produces the same payload (no narrative prose).

The contract is identical to market_read: numbers are computed in Python, the
LLM only writes the prose and the selection rationale. Every figure the LLM
cites comes from the structured payload it was handed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Literal

import numpy as np

from fcn.options.analytics import SurfaceAnalytics
from fcn.options.chain import OptionChain
from fcn.options.greeks import delta_to_strike
from fcn.options.strategies import (
    STRATEGY_CATALOG,
    StrategySpec,
    bear_call_spread,
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
)
from fcn.options.strategy_engine import StrategyValuation, value_strategy
from fcn.options.view import (
    FundamentalView,
    StrategyFamilyScore,
    conviction_to_quantity,
    horizon_to_expiry,
    map_view_to_families,
)
from fcn.service import llm


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------

@dataclass
class StrategyCandidate:
    spec: StrategySpec
    valuation: StrategyValuation
    fit_score: float                          # view-fit only (0-100)
    rationale: str = ""
    liquidity_adjusted_score: float = 0.0     # fit × liquidity multiplier (ranking key)

    def to_dict(self) -> dict:
        return {
            "name": self.spec.name,
            "family": self.spec.family,
            "view_tag": self.spec.view_tag,
            "fit_score": round(self.fit_score, 1),
            "liquidity_adjusted_score": round(self.liquidity_adjusted_score, 1),
            "rationale": self.rationale,
            "strategy": self.spec.model_dump(mode="json"),
            "valuation": self.valuation.to_dict(),
        }


@dataclass
class AdvisorResult:
    view: FundamentalView
    analytics: SurfaceAnalytics
    candidates: list[StrategyCandidate]
    shortlist: list[dict]            # family-name, score, reasons (audit trail)
    narrative: str
    narrative_source: Literal["llm", "template"]
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "view": self.view.model_dump(mode="json"),
            "analytics": self.analytics.to_dict(),
            "candidates": [c.to_dict() for c in self.candidates],
            "shortlist": self.shortlist,
            "narrative": self.narrative,
            "narrative_source": self.narrative_source,
            "warnings": self.warnings,
        }


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def advise(
    view: FundamentalView,
    chain: OptionChain,
    analytics: SurfaceAnalytics,
    *,
    top_n: int = 3,
    asof: date | None = None,
    llm_caller=None,
) -> AdvisorResult:
    """Two-stage view → ranked candidates + narrative.

    The ``llm_caller`` is injectable for unit tests; it must accept ``(prompt,
    system)`` and return ``dict | None`` (the JSON payload). Defaults to
    :func:`fcn.service.llm.generate_structured`.
    """
    asof = asof or chain.asof
    shortlist = map_view_to_families(view, analytics)[:top_n]
    warnings: list[str] = []

    # ---- Stage 1: parameterise each short-listed family -----------------
    candidates: list[StrategyCandidate] = []
    for fam in shortlist:
        try:
            spec = _parameterise(fam.name, view, chain, analytics, asof)
        except Exception as exc:  # noqa: BLE001 - never let one bad build crash advise()
            warnings.append(f"could not build {fam.name}: {exc}")
            continue
        if spec is None:
            warnings.append(f"could not parameterise {fam.name} on the chain")
            continue
        try:
            # Value the FILLABLE structure: snap legs to liquid strikes so the
            # recommendation reflects what can actually be executed.
            val = value_strategy(spec, chain, asof=asof, optimize_liquidity=True)
        except Exception as exc:  # noqa: BLE001 - skip a candidate on any failure
            warnings.append(f"{fam.name} valuation failed: {exc}")
            continue
        liq = val.liquidity or {}
        mult = float(liq.get("multiplier", 1.0))
        # Penalise dollar slippage DIRECTLY (not only via the spread→score
        # multiplier): a multi-leg structure with the same per-leg liquidity
        # still costs more to enter, so it should rank lower after costs.
        slip_haircut = 1.0 - min(0.30, float(liq.get("slippage_pct", 0.0) or 0.0))
        candidates.append(StrategyCandidate(
            spec=spec, valuation=val, fit_score=fam.score, rationale="",
            liquidity_adjusted_score=fam.score * mult * slip_haircut,
        ))

    # Re-rank AFTER liquidity/slippage: a thinly-traded structure that prices
    # well on mid must sink below a genuinely fillable alternative.
    candidates.sort(key=lambda c: c.liquidity_adjusted_score, reverse=True)
    if any((c.valuation.liquidity or {}).get("tradable") is False for c in candidates):
        warnings.append("some candidates are thinly traded; ranking is liquidity-adjusted")

    # ---- Stage 2: LLM picks within candidates + writes rationale --------
    caller = llm_caller if llm_caller is not None else llm.generate_structured
    prompt, system = _build_prompt(view, analytics, candidates)
    llm_payload = caller(prompt, system=system) if caller is not None else None

    if llm_payload is not None:
        narrative, per_candidate = _apply_llm_payload(llm_payload, candidates)
        for c in candidates:
            c.rationale = per_candidate.get(c.spec.name, "")
        source: Literal["llm", "template"] = "llm"
    else:
        narrative = _template_narrative(view, analytics, candidates)
        for c in candidates:
            c.rationale = _template_candidate_rationale(c, view, analytics)
        source = "template"

    return AdvisorResult(
        view=view, analytics=analytics, candidates=candidates,
        shortlist=[{
            "name": s.name, "score": round(s.score, 1), "reasons": s.reasons,
            "family": s.family, "view": s.view, "description": s.description,
        } for s in shortlist],
        narrative=narrative, narrative_source=source, warnings=warnings,
    )


# ---------------------------------------------------------------------------
# Strategy parameterisation (family name → concrete spec)
# ---------------------------------------------------------------------------

def _parameterise(
    name: str, view: FundamentalView, chain: OptionChain,
    analytics: SurfaceAnalytics, asof: date,
) -> StrategySpec | None:
    """Translate a family name into a concrete :class:`StrategySpec`.

    Idiom: ATM strikes for straddles; ±5% (or 25Δ) for spreads/wings; the
    near-end of the horizon window for expiry. Quantity from conviction.
    """
    ticker = view.ticker
    spot = chain.spot
    qty = conviction_to_quantity(view.conviction)
    exp = horizon_to_expiry(view.horizon, asof)
    q = chain.div_yield
    b = chain.borrow
    rate = chain.rate
    shares = int(view.holding_shares) if view.holding_shares > 0 else 100

    # Standard wing offsets.
    atm = spot
    otm_call = spot * 1.05
    otm_put = spot * 0.95
    deep_put = spot * 0.90
    deep_call = spot * 1.10
    # 25Δ strike (from the surface, falling back to ±5% if no surface).
    try:
        t = max((exp - asof).days, 1) / 365.0
        atm_iv = analytics.iv_1m_atm or 0.30
        k_put25 = delta_to_strike(-0.25, spot, t, atm_iv, rate, q, b, kind="put")
        k_call25 = delta_to_strike(0.25, spot, t, atm_iv, rate, q, b, kind="call")
    except Exception:  # noqa: BLE001
        k_put25, k_call25 = deep_put, deep_call

    if name == "long_call":
        return long_call(ticker, spot, exp, atm, qty=qty)
    if name == "long_put":
        return long_put(ticker, spot, exp, atm, qty=qty)
    if name == "long_leaps_call":
        # LEAPS: 1Y expiry regardless of horizon (long_call handles short horizons).
        return long_leaps_call(ticker, spot, asof + timedelta(days=365), atm, qty=qty)
    if name == "bull_call_spread":
        return bull_call_spread(ticker, spot, exp, atm, otm_call, qty=qty)
    if name == "bull_put_spread":
        return bull_put_spread(ticker, spot, exp, otm_put, deep_put, qty=qty)
    if name == "bear_call_spread":
        return bear_call_spread(ticker, spot, exp, atm, otm_call, qty=qty)
    if name == "bear_put_spread":
        return bear_put_spread(ticker, spot, exp, atm, otm_put, qty=qty)
    if name == "long_straddle":
        return long_straddle(ticker, spot, exp, atm, qty=qty)
    if name == "long_strangle":
        return long_strangle(ticker, spot, exp, otm_call, otm_put, qty=qty)
    if name == "short_straddle":
        return short_straddle(ticker, spot, exp, atm, qty=qty)
    if name == "short_strangle":
        return short_strangle(ticker, spot, exp, otm_call, otm_put, qty=qty)
    if name == "iron_condor":
        return iron_condor(ticker, spot, exp, deep_put, otm_put, otm_call, deep_call, qty=qty)
    if name == "iron_butterfly":
        from fcn.options.strategies import iron_butterfly
        return iron_butterfly(ticker, spot, exp, atm, wing_width=spot * 0.05, qty=qty)
    if name == "calendar_spread":
        # Far leg is always strictly later than the near leg (a fixed +180d
        # offset), so this stays valid even when the horizon is 'years'
        # (exp == asof+365) — previously near==far crashed the factory.
        near = exp
        far = exp + timedelta(days=180)
        return calendar_spread(ticker, spot, near_expiry=near, far_expiry=far,
                               strike=atm, qty=qty)
    if name == "diagonal_spread":
        from fcn.options.strategies import diagonal_spread
        return diagonal_spread(ticker, spot, near_expiry=exp,
                               far_expiry=exp + timedelta(days=180),
                               near_strike=atm, far_strike=otm_call,
                               kind="call", qty=qty)
    if name == "covered_call":
        return covered_call(ticker, spot, exp, otm_call, shares=shares, qty=qty)
    if name == "cash_secured_put":
        return cash_secured_put(ticker, spot, exp, otm_put, qty=qty)
    if name == "wheel":
        from fcn.options.strategies import wheel
        return wheel(ticker, spot, exp, otm_put, qty=qty)
    if name == "protective_put":
        return protective_put(ticker, spot, exp, otm_put, shares=shares, qty=qty)
    if name == "collar":
        return collar(ticker, spot, exp, otm_put, otm_call, shares=shares, qty=qty)
    if name == "risk_reversal":
        return risk_reversal(ticker, spot, exp, k_call25, k_put25, qty=qty)
    return None

# ---------------------------------------------------------------------------
# LLM prompt construction & application
# ---------------------------------------------------------------------------

def _build_prompt(
    view: FundamentalView, analytics: SurfaceAnalytics,
    candidates: list[StrategyCandidate],
) -> tuple[str, str]:
    """Return (prompt, system_prompt) — the prompt is in the view's language."""
    is_zh = view.language == "zh"
    sys_msg = (
        "You are an equity-derivatives strategist at a hedge fund. You receive a fundamental "
        "view, live IV-surface analytics, and a short-list of fully-valued option strategies. "
        "Each candidate also carries LIQUIDITY: a 0-100 liquidity_score, a tradable flag, and the "
        "slippage ($) to enter. PREFER fillable structures — a strategy that looks good on mid but "
        "is thinly traded (low score / not tradable / high slippage) is a worse recommendation than "
        "a slightly-lower-fit but liquid one; the candidates are already ranked liquidity-adjusted. "
        "Pick 1-3 strategies to recommend, write a one-sentence rationale for each (referencing the "
        "numbers, including liquidity/slippage where relevant), and write a 120-180 word top-level "
        "narrative. Do NOT invent new structures or change any numbers. Respond ONLY with a JSON "
        'object of shape {"narrative": str, "selections": [{"name": str, "rationale": str}, ...]}. ' +
        ("Respond in Simplified Chinese." if is_zh else "Respond in English.")
    )
    view_d = {
        "ticker": view.ticker, "direction": view.direction, "horizon": view.horizon,
        "conviction": view.conviction, "vol_view": view.vol_view,
        "holding_shares": view.holding_shares, "income_preference": view.income_preference,
    }
    cand_d = [{
        "name": c.spec.name, "family": c.spec.family, "fit_score": round(c.fit_score, 1),
        "liquidity_adjusted_score": round(c.liquidity_adjusted_score, 1),
        "net_debit": round(c.valuation.net_debit, 2),
        "exec_net_debit": round(c.valuation.exec_net_debit, 2),
        "slippage": round(c.valuation.slippage, 2),
        "liquidity_score": (c.valuation.liquidity or {}).get("score"),
        "tradable": (c.valuation.liquidity or {}).get("tradable"),
        "max_profit": c.valuation.max_profit, "max_loss": c.valuation.max_loss,
        "prob_profit": round(c.valuation.prob_profit, 3),
        "delta": round(c.valuation.greeks.delta, 1),
        "vega": round(c.valuation.greeks.vega, 1),
        "theta": round(c.valuation.greeks.theta, 2),
        "margin_estimate": c.valuation.margin_estimate,
    } for c in candidates]
    analytics_d = {
        "iv_1m_atm": round(analytics.iv_1m_atm, 4),
        "iv_rv_gap": analytics.iv_rv_gap, "vol_regime": analytics.vol_regime,
        "term_structure": analytics.term_structure,
        "risk_reversal_25d": round(analytics.risk_reversal_25d_3m, 4),
        "skew_90": round(analytics.skew_90_3m, 4),
    }
    prompt = (
        f"View: {view_d}\n"
        f"Surface analytics: {analytics_d}\n"
        f"Candidates (all pre-valued): {cand_d}\n"
        "Return the JSON."
    )
    return prompt, sys_msg


def _apply_llm_payload(
    payload: dict, candidates: list[StrategyCandidate],
) -> tuple[str, dict[str, str]]:
    """Extract narrative + per-candidate rationale from the LLM JSON payload."""
    narrative = str(payload.get("narrative", "")).strip()
    selections = payload.get("selections", []) if isinstance(payload.get("selections"), list) else []
    per: dict[str, str] = {}
    for sel in selections:
        if isinstance(sel, dict):
            n = str(sel.get("name", ""))
            r = str(sel.get("rationale", ""))
            if n:
                per[n] = r
    return narrative, per


def _template_narrative(
    view: FundamentalView, analytics: SurfaceAnalytics,
    candidates: list[StrategyCandidate],
) -> str:
    """Deterministic fallback when no LLM key is set."""
    is_zh = view.language == "zh"
    top = candidates[0] if candidates else None
    if top is None:
        return "No suitable strategies for the given view and surface." if not is_zh else \
               "当前观点与隐含波动率曲面下暂无匹配的策略。"
    liq = top.valuation.liquidity or {}
    if is_zh:
        return (
            f"{view.ticker} 当前 1M ATM IV {analytics.iv_1m_atm*100:.0f}%，"
            f"波动率环境 {analytics.vol_regime}，期限结构 {analytics.term_structure}。"
            f"基于「{view.direction} · {view.horizon}期 · 信心{view.conviction}/5」"
            f"观点，经流动性调整后最优结构为 {top.spec.name}（fit {top.fit_score:.0f}/100，"
            f"流动性 {liq.get('score', 0):.0f}/100{'·成交稀薄' if liq.get('tradable') is False else ''}），"
            f"预计{'支付' if top.valuation.net_debit > 0 else '收取'} "
            f"${abs(top.valuation.net_debit):.0f}，预估滑点 ${top.valuation.slippage:.0f}，"
            f"盈利概率 {top.valuation.prob_profit*100:.0f}%。"
            "排名已计入滑点与成交活跃度；数字均为代码确定计算，建议结合自身风险预算调整规模。"
        )
    return (
        f"{view.ticker}: 1M ATM IV {analytics.iv_1m_atm*100:.0f}%, vol regime "
        f"{analytics.vol_regime}, term {analytics.term_structure}. For a "
        f"{view.direction}/{view.horizon}/conviction-{view.conviction} view, the "
        f"liquidity-adjusted best structure is {top.spec.name} (fit {top.fit_score:.0f}/100, "
        f"liquidity {liq.get('score', 0):.0f}/100{', thinly traded' if liq.get('tradable') is False else ''}), "
        f"{'costing' if top.valuation.net_debit > 0 else 'crediting'} "
        f"${abs(top.valuation.net_debit):.0f} with ~${top.valuation.slippage:.0f} slippage and P(profit) "
        f"{top.valuation.prob_profit*100:.0f}%. Ranking already accounts for slippage and trading "
        "activity; all figures are code-computed — size to your own risk budget."
    )


def _template_candidate_rationale(
    candidate: StrategyCandidate, view: FundamentalView, analytics: SurfaceAnalytics,
) -> str:
    is_zh = view.language == "zh"
    name = candidate.spec.name
    meta = STRATEGY_CATALOG.get(name, {})
    desc = meta.get("desc", "")
    liq = candidate.valuation.liquidity or {}
    liq_score = liq.get("score", 0) or 0
    if is_zh:
        return (
            f"{name} — {desc}。匹配分 {candidate.fit_score:.0f}/100，"
            f"流动性 {liq_score:.0f}/100{'·成交稀薄' if liq.get('tradable') is False else ''}；"
            f"净{'支出' if candidate.valuation.net_debit > 0 else '收入'} "
            f"${abs(candidate.valuation.net_debit):.0f}（滑点 ${candidate.valuation.slippage:.0f}），"
            f"最大{'亏损' if candidate.valuation.max_loss is None else '亏损 $' + f'{candidate.valuation.max_loss:.0f}'}"
            f"{'（无限）' if candidate.valuation.max_loss is None else ''}，"
            f"胜率 {candidate.valuation.prob_profit*100:.0f}%。"
        )
    return (
        f"{name} — {desc}. Fit {candidate.fit_score:.0f}/100, "
        f"liquidity {liq_score:.0f}/100{', thin' if liq.get('tradable') is False else ''}; "
        f"net {'debit' if candidate.valuation.net_debit > 0 else 'credit'} "
        f"${abs(candidate.valuation.net_debit):.0f} (slippage ${candidate.valuation.slippage:.0f}), "
        f"max loss {'unlimited' if candidate.valuation.max_loss is None else f'${candidate.valuation.max_loss:.0f}'}, "
        f"P(profit) {candidate.valuation.prob_profit*100:.0f}%."
    )
