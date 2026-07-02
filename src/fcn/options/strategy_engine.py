"""Strategy valuation: price, aggregate Greeks, payoff, breakevens, MoP/MoL.

The engine is deliberately BS-Europe for valuation and payoff-at-expiry. For
the live path the per-leg IV is pulled from the chain (so the surface is
respected even though the Greeks formula is BS-at-that-point); for the abstract
path the same surface is read by the chain. American exercise, discrete
dividends, and path-dependence are out of scope for v1 (documented in
``docs/OPTIONS_REVIEW.md``) — the FCN MC engine covers those cases for the
structured-notes side; for *vanilla listed options* BS is the desk standard.

Payoff is computed at the *earliest* option-leg expiry (most strategies are
single-tenor; calendars/diagonals are priced at the near leg's expiry with the
far leg marked-to-model at that point — a one-step approximation that is
clearly disclosed).

Position sizes: each contract = 100 shares; stock legs are absolute shares.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np

from fcn.options.chain import OptionChain
from fcn.options.greeks import Greeks, bs_greeks, bs_price
from fcn.options.liquidity import aggregate_liquidity, half_spread_cost
from fcn.options.strategies import StrategySpec

CONTRACT_SIZE = 100  # standard US equity option


@dataclass(frozen=True)
class AggregatedGreeks:
    """Portfolio-level Greeks across all legs (option + stock)."""

    delta: float       # per 1.0 spot move, in shares-equivalent
    gamma: float
    vega: float        # per 1.00 vol
    theta: float       # per calendar day
    rho: float
    vanna: float
    vomma: float

    def to_dict(self) -> dict:
        return {k: round(getattr(self, k), 6) for k in
                ("delta", "gamma", "vega", "theta", "rho", "vanna", "vomma")}


@dataclass(frozen=True)
class LegValuation:
    """Per-leg audit trail — surfaced to the UI."""

    kind: str
    expiry: str
    strike: float
    quantity: int
    iv: float | None
    unit_price: float
    source: str           # 'live' or 'bs'
    delta: float          # leg-level (per-contract)
    vega: float
    gamma: float
    theta: float
    liquidity: dict | None = None   # per-leg liquidity read (rel_spread, score, …)


@dataclass(frozen=True)
class StrategyValuation:
    """Full valuation of a :class:`StrategySpec`."""

    net_debit: float                       # +paid / −received, on MID (per-strategy)
    greeks: AggregatedGreeks
    payoff_at_expiry: list[tuple[float, float]]  # (spot, pnl)
    breakevens: list[float]
    max_profit: float | None               # None = unbounded
    max_loss: float | None
    prob_profit: float                     # risk-neutral P(pnl > 0)
    margin_estimate: float | None          # conservative span-like estimate
    contracts_audit: list[LegValuation]
    days_to_expiry: int
    underlying_price: float
    valuation_date: str
    # Liquidity / execution dimension --------------------------------------
    exec_net_debit: float = 0.0            # net debit crossing mid→touch (real fill)
    slippage: float = 0.0                  # exec_net_debit − net_debit (≥ 0, cost to enter)
    liquidity: dict | None = None          # strategy-level liquidity roll-up
    liquidity_remaps: list[dict] | None = None  # strikes moved to liquid contracts
    effective_strategy: dict | None = None      # remapped spec when optimised (else None)

    def to_dict(self) -> dict:
        return {
            "net_debit": round(self.net_debit, 4),
            "exec_net_debit": round(self.exec_net_debit, 4),
            "slippage": round(self.slippage, 4),
            "liquidity": self.liquidity,
            "liquidity_remaps": self.liquidity_remaps or [],
            "effective_strategy": self.effective_strategy,
            "greeks": self.greeks.to_dict(),
            "payoff_at_expiry": [[round(s, 2), round(p, 2)] for s, p in self.payoff_at_expiry],
            "breakevens": [round(b, 2) for b in self.breakevens],
            "max_profit": None if self.max_profit is None else round(self.max_profit, 2),
            "max_loss": None if self.max_loss is None else round(self.max_loss, 2),
            "prob_profit": round(self.prob_profit, 4),
            "margin_estimate": None if self.margin_estimate is None else round(self.margin_estimate, 2),
            "contracts_audit": [
                {
                    "kind": a.kind, "expiry": a.expiry, "strike": a.strike,
                    "quantity": a.quantity, "iv": a.iv, "unit_price": a.unit_price,
                    "source": a.source,
                    "delta": a.delta, "vega": a.vega, "gamma": a.gamma, "theta": a.theta,
                    "liquidity": a.liquidity,
                }
                for a in self.contracts_audit
            ],
            "days_to_expiry": self.days_to_expiry,
            "underlying_price": round(self.underlying_price, 4),
            "valuation_date": self.valuation_date,
        }


# ---------------------------------------------------------------------------
# Valuation
# ---------------------------------------------------------------------------

def value_strategy(
    spec: StrategySpec, chain: OptionChain, *, asof: date | None = None,
    spot_grid: tuple[float, float] | None = None, n_grid: int = 161,
    n_mc: int = 50_000, optimize_liquidity: bool = False,
) -> StrategyValuation:
    """Full valuation of a :class:`StrategySpec` against a chain.

    Each option leg is matched to the nearest live/abstract contract (by strike
    + expiry). If the leg's ``unit_price`` is set, it overrides the chain mark
    (the user has typed a specific price); otherwise we use the chain mid if
    available, else BS at the contract IV. The payoff diagram is computed at
    the *earliest* option expiry.

    Liquidity dimension: every leg also gets a liquidity read (real or modeled
    bid/ask), from which we report the **execution net debit** (buy at the ask,
    sell at the bid), the **slippage** to enter, and a strategy-level liquidity
    roll-up. With ``optimize_liquidity=True`` each leg's strike is first snapped
    to the most liquid contract in a tight moneyness band, so the valuation
    reflects a structure that can actually be filled.
    """
    asof = asof or chain.asof
    spot = chain.spot
    if spot_grid is None:
        # Sample down to spot = 0 so the reported max_loss is the TRUE worst case
        # for short-premium strategies (covered call / CSP / short put lose the
        # most as spot → 0, not at 0.05×S). BS marks and intrinsics are finite at
        # S=0, so the grid endpoint is safe.
        spot_grid = (0.0, 1.5 * spot)

    # ---- Optionally snap each leg to the most liquid nearby strike ------
    # Only the STRIKE moves (never the expiry), and the remap is applied as a
    # whole only if it PRESERVES the structure (same-strike legs stay equal,
    # strike ordering is kept) — so a calendar can't silently become a diagonal
    # nor a vertical collapse/invert. Otherwise we keep the exact requested spec.
    remaps: list[dict] = []
    effective_strategy: dict | None = None
    if optimize_liquidity and spec.option_legs:
        orig = list(spec.option_legs)
        proposed = [
            chain.most_liquid_near(kind=leg.kind, strike=leg.strike, expiry=leg.expiry).strike
            for leg in orig
        ]
        if _preserves_structure(orig, proposed):
            new_legs = []
            for leg, k in zip(orig, proposed, strict=True):
                if abs(k - leg.strike) > 1e-9:
                    remaps.append({"kind": leg.kind, "from_strike": leg.strike,
                                   "to_strike": k, "quantity": leg.quantity})
                    new_legs.append(leg.model_copy(update={"strike": k}))
                else:
                    new_legs.append(leg)
            if remaps:
                spec = spec.model_copy(update={"option_legs": new_legs})
                effective_strategy = spec.model_dump(mode="json")

    # ---- Per-leg valuation + audit -------------------------------------
    leg_audit: list[LegValuation] = []
    leg_liqs = []
    greeks_acc = _PerLeg()
    net_debit = 0.0          # on MID
    exec_net_debit = 0.0     # crossing mid → touch (real fill)
    slippage = 0.0

    for leg in spec.option_legs:
        contract = chain.select(kind=leg.kind, strike=leg.strike, expiry=leg.expiry)
        iv = contract.iv if contract.iv is not None else _fallback_iv(chain, contract)
        t = contract.years_to(asof)
        # Greeks (and the BS price) at the leg's IV — computed once, reused.
        g_leg = bs_greeks(spot, contract.strike, t, iv or 0.30, chain.rate,
                          chain.div_yield, chain.borrow, kind=leg.kind)
        # If the leg carries an explicit unit_price (user override), use it.
        # Otherwise prefer the chain mid if live; else the BS mark from g_leg.
        if leg.unit_price is not None:
            unit_price = leg.unit_price
            source = "manual"
        elif contract.mid is not None and contract.source == "live":
            unit_price = float(contract.mid)
            source = "live"
        else:
            unit_price = float(np.asarray(g_leg.price).item())
            source = "bs"
        # Per-contract (100-share) positions:
        delta_per_contract = float(np.asarray(g_leg.delta).item()) * CONTRACT_SIZE
        gamma_per_contract = float(np.asarray(g_leg.gamma).item()) * CONTRACT_SIZE
        vega_per_contract = float(np.asarray(g_leg.vega).item()) * CONTRACT_SIZE
        theta_per_contract = float(np.asarray(g_leg.theta).item()) * CONTRACT_SIZE
        vanna_per_contract = float(np.asarray(g_leg.vanna).item()) * CONTRACT_SIZE
        vomma_per_contract = float(np.asarray(g_leg.vomma).item()) * CONTRACT_SIZE
        rho_per_contract = float(np.asarray(g_leg.rho).item()) * CONTRACT_SIZE

        signed = leg.quantity
        # Liquidity + execution: cross mid → touch (buy@ask / sell@bid).
        # For a quoted contract use the TRUE half-spread (ask−bid)/2 — the rel
        # spread is clamped for scoring, so it would understate penny/blown
        # quotes; fall back to ½·rel_spread·mark for modeled (no absolute quote).
        liq = chain.contract_liquidity(contract)
        half = (0.5 * liq.spread_abs) if liq.spread_abs is not None \
            else half_spread_cost(liq.rel_spread, unit_price)
        exec_unit = unit_price + half if signed > 0 else unit_price - half
        leg_liqs.append(liq)
        net_debit += signed * unit_price * CONTRACT_SIZE
        exec_net_debit += signed * exec_unit * CONTRACT_SIZE
        slippage += abs(signed) * half * CONTRACT_SIZE
        greeks_acc = greeks_acc + _PerLeg(
            delta=signed * delta_per_contract,
            gamma=signed * gamma_per_contract,
            vega=signed * vega_per_contract,
            theta=signed * theta_per_contract,
            rho=signed * rho_per_contract,
            vanna=signed * vanna_per_contract,
            vomma=signed * vomma_per_contract,
        )
        leg_audit.append(LegValuation(
            kind=leg.kind, expiry=contract.expiry.isoformat(), strike=contract.strike,
            quantity=leg.quantity, iv=iv, unit_price=unit_price, source=source,
            delta=delta_per_contract * signed,
            vega=vega_per_contract * signed,
            gamma=gamma_per_contract * signed,
            theta=theta_per_contract * signed,
            liquidity=liq.to_dict(),
        ))

    if spec.stock_leg is not None:
        sl = spec.stock_leg
        net_debit += sl.quantity * sl.entry_price
        exec_net_debit += sl.quantity * sl.entry_price   # equities: ~no option slippage
        # Stock: delta = shares, other Greeks = 0.
        greeks_acc = greeks_acc + _PerLeg(
            delta=sl.quantity, gamma=0, vega=0, theta=0, rho=0, vanna=0, vomma=0,
        )

    # ---- Payoff at earliest expiry -------------------------------------
    if spec.option_legs:
        payoffs, spots = _payoff_at_expiry(spec, chain, asof, spot_grid, n_grid)
    else:
        # Pure-stock position: linear payoff.
        assert spec.stock_leg is not None
        spots = np.linspace(spot_grid[0], spot_grid[1], n_grid)
        payoffs = (spots - spec.stock_leg.entry_price) * spec.stock_leg.quantity

    # ---- Breakevens / MoP / MoL ---------------------------------------
    breakevens = _zero_crossings(spots, payoffs)
    max_profit, max_loss = _extremes(payoffs, spots, spec)

    # ---- Probability of profit (risk-neutral lognormal) ----------------
    prob_profit = _prob_profit(spec, chain, asof, payoffs, spots, n_mc)

    # ---- Margin (rough; clearly disclosed) -----------------------------
    margin = _margin_estimate(spec, chain, max_loss)

    # ---- Days to expiry (earliest leg) ---------------------------------
    if spec.option_legs:
        dte = min(
            (chain.select(kind=leg.kind, strike=leg.strike, expiry=leg.expiry).expiry - asof).days
            for leg in spec.option_legs
        )
    else:
        dte = 0

    # ---- Liquidity roll-up ---------------------------------------------
    strat_liq = aggregate_liquidity(leg_liqs, slippage, net_debit, spot, CONTRACT_SIZE)

    agg = AggregatedGreeks(
        delta=greeks_acc.delta, gamma=greeks_acc.gamma, vega=greeks_acc.vega,
        theta=greeks_acc.theta, rho=greeks_acc.rho,
        vanna=greeks_acc.vanna, vomma=greeks_acc.vomma,
    )

    return StrategyValuation(
        net_debit=float(net_debit),
        greeks=agg,
        payoff_at_expiry=list(zip(spots.tolist(), payoffs.tolist(), strict=True)),
        breakevens=breakevens,
        max_profit=max_profit,
        max_loss=max_loss,
        prob_profit=float(prob_profit),
        margin_estimate=margin,
        contracts_audit=leg_audit,
        days_to_expiry=int(dte),
        underlying_price=float(spot),
        valuation_date=asof.isoformat(),
        exec_net_debit=float(exec_net_debit),
        slippage=float(slippage),
        liquidity=strat_liq.to_dict(),
        liquidity_remaps=remaps,
        effective_strategy=effective_strategy,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@dataclass
class _PerLeg:
    delta: float = 0.0
    gamma: float = 0.0
    vega: float = 0.0
    theta: float = 0.0
    rho: float = 0.0
    vanna: float = 0.0
    vomma: float = 0.0

    def __add__(self, other: "_PerLeg") -> "_PerLeg":
        return _PerLeg(
            delta=self.delta + other.delta, gamma=self.gamma + other.gamma,
            vega=self.vega + other.vega, theta=self.theta + other.theta,
            rho=self.rho + other.rho, vanna=self.vanna + other.vanna,
            vomma=self.vomma + other.vomma,
        )


def _preserves_structure(legs, new_strikes) -> bool:
    """True if remapping each leg's strike to ``new_strikes`` keeps the structure.

    For every leg pair, the original strike RELATIONSHIP must hold after the
    remap: legs that shared a strike must stay equal (else a calendar/straddle
    becomes a diagonal/strangle), and any strictly-ordered pair must keep its
    order (else a vertical collapses or inverts). Relationships are checked
    across all pairs regardless of expiry, so same-strike-different-expiry
    structures (calendars) are protected too.
    """
    n = len(legs)
    for i in range(n):
        for j in range(i + 1, n):
            so = legs[i].strike - legs[j].strike
            sn = new_strikes[i] - new_strikes[j]
            if abs(so) < 1e-9:
                if abs(sn) > 1e-9:
                    return False          # were equal, now differ
            elif so > 0:
                if sn <= 0:
                    return False          # ordering collapsed/inverted
            else:  # so < 0
                if sn >= 0:
                    return False
    return True


def _fallback_iv(chain: OptionChain, contract) -> float:
    """If a live contract has no IV, use ATM surface vol at its expiry."""
    # Best effort: average IV of other contracts at the same expiry.
    same_exp = [c.iv for c in chain.contracts if c.expiry == contract.expiry and c.iv]
    if same_exp:
        return float(np.mean(same_exp))
    return 0.30


def _payoff_at_expiry(
    spec: StrategySpec, chain: OptionChain, asof: date,
    spot_grid: tuple[float, float], n_grid: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Terminal P&L at the earliest option-leg expiry across a spot grid."""
    # Build a grid that always includes the current spot exactly — most
    # strategies peak there, and a missing spot point visibly flattens the
    # reported max_profit/max_loss.
    lo, hi = spot_grid
    n_below = max(n_grid // 3, 10)
    n_above = max(n_grid - n_below, 10)
    spots = np.unique(np.concatenate([
        np.linspace(lo, chain.spot, n_below),
        np.linspace(chain.spot, hi, n_above),
    ]))
    pnl = np.zeros_like(spots)

    # For calendar/diagonal: at the near-leg expiry, the far leg still has time
    # value. Approximate by BS-marking the far leg at the near expiry using the
    # spot grid. (Disclosed as a one-step approximation.)
    near_expiry = min(leg.expiry for leg in spec.option_legs)
    far_legs = [leg for leg in spec.option_legs if leg.expiry > near_expiry]
    near_legs = [leg for leg in spec.option_legs if leg.expiry == near_expiry]

    for leg in near_legs:
        contract = chain.select(kind=leg.kind, strike=leg.strike, expiry=leg.expiry)
        # Intrinsic at expiry: call → max(S−K,0), put → max(K−S,0).
        if leg.kind == "call":
            intrinsic = np.maximum(spots - contract.strike, 0.0)
        else:
            intrinsic = np.maximum(contract.strike - spots, 0.0)
        # Subtract entry cost (already paid for longs, received for shorts):
        unit_cost = leg.unit_price
        if unit_cost is None:
            t = contract.years_to(asof)
            iv = contract.iv or _fallback_iv(chain, contract)
            unit_cost = float(np.asarray(bs_price(
                chain.spot, contract.strike, t, iv, chain.rate,
                chain.div_yield, chain.borrow, kind=leg.kind)).item())
        pnl += leg.quantity * CONTRACT_SIZE * (intrinsic - unit_cost)

    for leg in far_legs:
        contract = chain.select(kind=leg.kind, strike=leg.strike, expiry=leg.expiry)
        iv = contract.iv or _fallback_iv(chain, contract)
        t_far_at_near = max((contract.expiry - near_expiry).days, 1) / 365.0
        # Far-leg mark at the near-leg expiry, across the spot grid. Price-only
        # (bs_price) so evaluating at spot=0 doesn't churn 0/0 in 2nd-order Greeks.
        mark = np.asarray(bs_price(spots, contract.strike, t_far_at_near, iv,
                                   chain.rate, chain.div_yield, chain.borrow, kind=leg.kind))
        unit_cost = leg.unit_price
        if unit_cost is None:
            t0 = contract.years_to(asof)
            unit_cost = float(np.asarray(bs_price(
                chain.spot, contract.strike, t0, iv, chain.rate,
                chain.div_yield, chain.borrow, kind=leg.kind)).item())
        pnl += leg.quantity * CONTRACT_SIZE * (mark - unit_cost)

    if spec.stock_leg is not None:
        pnl += spec.stock_leg.quantity * (spots - spec.stock_leg.entry_price)

    return pnl, spots


def _zero_crossings(spots: np.ndarray, pnl: np.ndarray) -> list[float]:
    """Linear-interpolate spot values where P&L crosses zero."""
    out: list[float] = []
    for i in range(len(spots) - 1):
        if pnl[i] == 0:
            out.append(float(spots[i]))
        elif pnl[i] * pnl[i + 1] < 0:
            # Linear interp: spot0 + (0 − p0) · (s1 − s0) / (p1 − p0)
            s0, s1, p0, p1 = spots[i], spots[i + 1], pnl[i], pnl[i + 1]
            out.append(float(s0 + (-p0) * (s1 - s0) / (p1 - p0)))
    return sorted(set(round(b, 4) for b in out))


def _extremes(pnl: np.ndarray, spots: np.ndarray, spec: StrategySpec) -> tuple[float | None, float | None]:
    """Max profit / max loss from the payoff scan, with unbounded detection.

    Spot is bounded below by 0 but unbounded above, so unboundedness can only
    manifest at the *top* of the grid. We look at the slope at the top boundary:

      * positive slope at top → max_profit is unbounded (long call / long stock)
      * negative slope at top → max_loss is unbounded (short call / short stock)
      * otherwise both are bounded and read off max/min of the scan

    The bottom boundary is always bounded (spot ≥ 0 → finite payoff).
    """
    max_p = float(np.max(pnl))
    min_p = float(np.min(pnl))

    # Slope at the top boundary (per $1 spot move, total position).
    dx = spots[-1] - spots[-2]
    slope_top = (pnl[-1] - pnl[-2]) / dx if dx > 0 else 0.0
    # Unbounded iff slope is materially non-zero at the top boundary. Use an
    # absolute test: > 50¢ per $1 spot (i.e. ≥ 0.5 share-equivalent of residual
    # directional exposure). Defined-risk spreads pin to ~0 slope at the edges.
    threshold = 0.5
    max_profit: float | None = max_p
    max_loss: float | None = -min_p if min_p < 0 else 0.0
    if slope_top > threshold:
        max_profit = None       # still rising → unbounded upside
    if slope_top < -threshold:
        max_loss = None         # still falling at top → unbounded downside
    return max_profit, max_loss


def _prob_profit(
    spec: StrategySpec, chain: OptionChain, asof: date,
    pnl: np.ndarray, spots: np.ndarray, n_mc: int,
) -> float:
    """Risk-neutral P(strategy P&L > 0) at the earliest expiry.

    Sample terminal spot from a lognormal with the chain's ATM vol at the
    nearest expiry; interpolate the payoff function; return the fraction of
    samples with positive P&L. Disclosed as a BS risk-neutral estimate.
    """
    if not spec.option_legs:
        # Pure-stock: defer to lognormal directly with a 30-day horizon.
        assert spec.stock_leg is not None  # validator guarantees this
        sl = spec.stock_leg
        T = 30 / 365.0
        mu = chain.rate - chain.div_yield - chain.borrow
        sigma = 0.30
        rng = np.random.default_rng(0xC0FFEE)
        z = rng.standard_normal(n_mc)
        s_T = chain.spot * np.exp((mu - 0.5 * sigma**2) * T + sigma * np.sqrt(T) * z)
        return float(np.mean((s_T - sl.entry_price) * sl.quantity > 0))

    near_expiry = min(leg.expiry for leg in spec.option_legs)
    T = max((near_expiry - asof).days, 1) / 365.0
    mu = chain.rate - chain.div_yield - chain.borrow
    # Centre the lognormal on the TRUE ATM vol (nearest-to-spot contract at the
    # near expiry), not on a strategy strike — otherwise a wide condor/strangle
    # would sample a skewed deep-wing vol and mis-state P(profit).
    near_contracts = [c for c in chain.contracts if c.expiry == near_expiry and c.iv]
    if near_contracts:
        atm_c = min(near_contracts, key=lambda c: abs(c.strike - chain.spot))
        atm_ivs = [c.iv for c in near_contracts
                   if abs(c.strike - atm_c.strike) < 0.02 * chain.spot]
        sigma = float(np.mean(atm_ivs)) if atm_ivs else float(atm_c.iv)
    else:
        sigma = 0.30

    rng = np.random.default_rng(0xC0FFEE)
    z = rng.standard_normal(n_mc)
    s_T = chain.spot * np.exp((mu - 0.5 * sigma**2) * T + sigma * np.sqrt(T) * z)
    # Linearly extrapolate beyond the grid instead of clamping — the payoff is
    # piecewise-linear at the tails, so the boundary slope gives a good estimate.
    pnl_fn = np.interp(s_T, spots, pnl)
    below = s_T < spots[0]
    above = s_T > spots[-1]
    if np.any(below):
        slope_lo = (pnl[1] - pnl[0]) / (spots[1] - spots[0])
        pnl_fn[below] = pnl[0] + slope_lo * (s_T[below] - spots[0])
    if np.any(above):
        slope_hi = (pnl[-1] - pnl[-2]) / (spots[-1] - spots[-2])
        pnl_fn[above] = pnl[-1] + slope_hi * (s_T[above] - spots[-1])
    return float(np.mean(pnl_fn > 0))


def _margin_estimate(
    spec: StrategySpec, chain: OptionChain, max_loss: float | None,
) -> float | None:
    """Conservative margin estimate derived from STRUCTURE, not strategy name.

    Disclosed as rough; broker rules vary. The rule is structural so it works
    for every (current and future) strategy without a per-name ladder:

      * **Defined-risk** (``max_loss`` is finite, e.g. spreads, condors,
        collar, covered call, protective put, cash-secured put): the worst-case
        loss already computed by :func:`_extremes` *is* the capital at risk.
      * **Unbounded downside** (``max_loss is None`` — naked short call/put,
        short straddle/strangle): Reg-T-style per short leg,
        ``premium + max(20%·S − OTM, 10%·S)`` × 100 × qty, summed.
      * **Long-only** (no short legs, unbounded but no margin): ``None``
        (paid in full as premium/cash).
    """
    if not spec.option_legs:
        return None  # pure stock position
    spot = chain.spot
    has_short = any(leg.quantity < 0 for leg in spec.option_legs)

    # Defined-risk: the computed worst-case loss is the margin requirement.
    if max_loss is not None:
        return float(max_loss)

    # Unbounded downside with no short option ⇒ long-only (no margin posted).
    if not has_short:
        return None

    # Naked / unbounded short legs: Reg-T per-leg approximation.
    margin = 0.0
    for leg in spec.option_legs:
        if leg.quantity >= 0:
            continue
        n = abs(leg.quantity) * CONTRACT_SIZE
        otm = max(leg.strike - spot, 0.0) if leg.kind == "call" else max(spot - leg.strike, 0.0)
        premium = (leg.unit_price or 0.0) * CONTRACT_SIZE * abs(leg.quantity)
        per_share = max(0.20 * spot - otm, 0.10 * spot)
        margin += premium + per_share * n
    return float(margin)
