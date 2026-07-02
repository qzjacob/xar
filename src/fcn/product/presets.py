"""Convenience builders that *fill the form* for the common variants.

These are presets, not engine branches: each returns a plain :class:`TermSheet`.
"""

from __future__ import annotations

from datetime import date

from fcn.core.calendar import frequency_to_months, periodic_schedule, roll
from fcn.product.enums import (
    CouponType,
    Frequency,
    KIStyle,
    ParticipationStyle,
    Settlement,
)
from fcn.product.termsheet import (
    AutocallSchedule,
    CouponSpec,
    KnockInSpec,
    ParticipationSpec,
    TermSheet,
    Underlying,
)

_ZERO_COUPON = CouponSpec(type=CouponType.FIXED, rate=0.0, frequency=Frequency.QUARTERLY)


def _autocall_schedule(
    strike_date: date,
    maturity: date,
    frequency: Frequency,
    barrier: float,
    step_down_per_period: float = 0.0,
    first_call_after_periods: int = 1,
) -> AutocallSchedule:
    months = frequency_to_months(frequency.value)
    dates = periodic_schedule(strike_date, maturity, months)
    obs = dates[first_call_after_periods - 1 :]
    barriers = [max(0.01, barrier - step_down_per_period * i) for i in range(len(obs))]
    return AutocallSchedule(dates=obs, barriers=barriers)


def build_fcn(
    *,
    tickers: list[str],
    notional: float,
    trade_date: date,
    strike_date: date,
    maturity: date,
    coupon_rate: float | None,
    frequency: Frequency = Frequency.QUARTERLY,
    autocall_barrier: float = 1.0,
    step_down_per_period: float = 0.0,
    ki_barrier: float = 0.65,
    ki_style: KIStyle = KIStyle.EUROPEAN,
    settlement: Settlement = Settlement.CASH,
    strike: float = 1.0,
) -> TermSheet:
    """Classic FCN: fixed/guaranteed coupon + (step-down) autocall + KI."""
    maturity = roll(maturity, "preceding")
    return TermSheet(
        notional=notional,
        trade_date=trade_date,
        strike_date=strike_date,
        maturity=maturity,
        underlyings=[Underlying(ticker=t, strike=strike) for t in tickers],
        autocall=_autocall_schedule(
            strike_date, maturity, frequency, autocall_barrier, step_down_per_period
        ),
        coupon=CouponSpec(type=CouponType.FIXED, rate=coupon_rate, frequency=frequency),
        knock_in=KnockInSpec(barrier=ki_barrier, style=ki_style, settlement=settlement),
    )


def build_phoenix(
    *,
    tickers: list[str],
    notional: float,
    trade_date: date,
    strike_date: date,
    maturity: date,
    coupon_rate: float | None,
    coupon_barrier: float = 0.7,
    memory: bool = True,
    frequency: Frequency = Frequency.QUARTERLY,
    autocall_barrier: float = 1.0,
    step_down_per_period: float = 0.0,
    ki_barrier: float = 0.65,
    ki_style: KIStyle = KIStyle.AMERICAN,
    settlement: Settlement = Settlement.CASH,
) -> TermSheet:
    """Phoenix: conditional coupon (with memory) + autocall + KI."""
    maturity = roll(maturity, "preceding")
    return TermSheet(
        notional=notional,
        trade_date=trade_date,
        strike_date=strike_date,
        maturity=maturity,
        underlyings=[Underlying(ticker=t) for t in tickers],
        autocall=_autocall_schedule(
            strike_date, maturity, frequency, autocall_barrier, step_down_per_period
        ),
        coupon=CouponSpec(
            type=CouponType.CONDITIONAL,
            rate=coupon_rate,
            frequency=frequency,
            barrier=coupon_barrier,
            memory=memory,
        ),
        knock_in=KnockInSpec(barrier=ki_barrier, style=ki_style, settlement=settlement),
    )


def build_snowball(
    *,
    tickers: list[str],
    notional: float,
    trade_date: date,
    strike_date: date,
    maturity: date,
    coupon_rate: float | None,
    frequency: Frequency = Frequency.MONTHLY,
    autocall_barrier: float = 1.0,
    ki_barrier: float = 0.7,
    settlement: Settlement = Settlement.CASH,
) -> TermSheet:
    """Snowball (雪球): continuous KI + autocall, accruing coupon."""
    maturity = roll(maturity, "preceding")
    return TermSheet(
        notional=notional,
        trade_date=trade_date,
        strike_date=strike_date,
        maturity=maturity,
        underlyings=[Underlying(ticker=t) for t in tickers],
        autocall=_autocall_schedule(strike_date, maturity, frequency, autocall_barrier),
        coupon=CouponSpec(
            type=CouponType.CONDITIONAL,
            rate=coupon_rate,
            frequency=frequency,
            barrier=0.01,  # snowball accrues whenever alive & not knocked-in
            memory=False,
            accrual_snowball=True,
        ),
        knock_in=KnockInSpec(barrier=ki_barrier, style=KIStyle.AMERICAN, settlement=settlement),
    )


def build_sharkfin(
    *,
    tickers: list[str],
    notional: float,
    trade_date: date,
    strike_date: date,
    maturity: date,
    participation: float = 1.0,
    ko_barrier: float = 1.30,
    cap: float | None = None,
    coupon_floor: float = 0.0,
    ko_style: KIStyle = KIStyle.AMERICAN,
) -> TermSheet:
    """SharkFin: principal-protected capped participation + up-and-out knock-out."""
    maturity = roll(maturity, "preceding")
    return TermSheet(
        notional=notional, trade_date=trade_date, strike_date=strike_date, maturity=maturity,
        underlyings=[Underlying(ticker=t) for t in tickers],
        autocall=None, coupon=_ZERO_COUPON, knock_in=None,
        participation=ParticipationSpec(
            style=ParticipationStyle.SHARKFIN, participation=participation,
            cap=cap, coupon_floor=coupon_floor, ko_barrier=ko_barrier, ko_style=ko_style,
        ),
    )


def build_booster(
    *,
    tickers: list[str],
    notional: float,
    trade_date: date,
    strike_date: date,
    maturity: date,
    participation: float = 1.5,
    buffer: float = 0.20,
    cap: float | None = 1.40,
) -> TermSheet:
    """Booster / Airbag: downside buffer + capped upside participation."""
    maturity = roll(maturity, "preceding")
    return TermSheet(
        notional=notional, trade_date=trade_date, strike_date=strike_date, maturity=maturity,
        underlyings=[Underlying(ticker=t) for t in tickers],
        autocall=None, coupon=_ZERO_COUPON, knock_in=None,
        participation=ParticipationSpec(
            style=ParticipationStyle.BOOSTER, participation=participation, cap=cap, buffer=buffer,
        ),
    )
