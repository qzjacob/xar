"""The single composable TermSheet schema (Pydantic v2).

Every variant — classic FCN, Phoenix (conditional + memory), snowball — is one
composition of optional blocks (``autocall``, ``knock_in``) and flags
(``memory``, ``accrual_snowball``, KI ``style``/``settlement``). The payoff engine
reads the blocks/flags; the same code prices all variants.

Levels (barriers, strike) are expressed as a *fraction of the initial fixing*
(e.g. ``0.65`` = 65%), which is how desks quote them and keeps the engine
scale-free across underlyings.
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field, model_validator

from fcn.product.enums import (
    BasketMode,
    CouponType,
    Frequency,
    KIStyle,
    ParticipationStyle,
    Settlement,
)


class Underlying(BaseModel):
    ticker: str
    strike: float = Field(1.0, gt=0, description="conversion price as fraction of initial fixing")
    initial_fixing: float | None = Field(
        None, gt=0, description="absolute fixing level; if None, set to spot at pricing time"
    )
    weight: float = Field(1.0, gt=0, description="used only for weighted (non worst-of) baskets")


class AutocallSchedule(BaseModel):
    """Per-observation autocall barriers (step-down expressed directly per date)."""

    dates: list[date]
    barriers: list[float] = Field(..., description="autocall barrier per date, fraction of fixing")

    @model_validator(mode="after")
    def _check(self) -> "AutocallSchedule":
        if len(self.dates) != len(self.barriers):
            raise ValueError("autocall dates and barriers must have equal length")
        if any(b <= 0 for b in self.barriers):
            raise ValueError("autocall barriers must be positive")
        if list(self.dates) != sorted(self.dates):
            raise ValueError("autocall dates must be ascending")
        return self


class CouponSpec(BaseModel):
    type: CouponType = CouponType.FIXED
    rate: float | None = Field(
        None, ge=0, description="annualised coupon rate; None when solving for it"
    )
    frequency: Frequency = Frequency.QUARTERLY
    barrier: float | None = Field(None, gt=0, description="coupon barrier (conditional only)")
    memory: bool = False  # Phoenix memory: missed coupons paid when barrier next met
    accrual_snowball: bool = False  # snowball: coupon accrues, paid on autocall/maturity

    @model_validator(mode="after")
    def _check(self) -> "CouponSpec":
        if self.type is CouponType.CONDITIONAL and self.barrier is None:
            raise ValueError("conditional coupon requires a coupon barrier")
        if self.type is CouponType.FIXED and self.memory:
            raise ValueError("memory only applies to conditional coupons")
        return self


class KnockInSpec(BaseModel):
    barrier: float = Field(..., gt=0, description="KI barrier as fraction of fixing, e.g. 0.65")
    style: KIStyle = KIStyle.EUROPEAN
    settlement: Settlement = Settlement.CASH


class ParticipationSpec(BaseModel):
    """Participation/structured-return notes (SharkFin, Booster) — a separate,
    principal-protected payoff family from the short-put FCN line. Levels are
    fractions of the initial fixing."""

    style: ParticipationStyle
    participation: float = Field(1.0, ge=0)
    cap: float | None = Field(None, gt=0, description="upside cap level, e.g. 1.30; None = uncapped")
    coupon_floor: float = Field(0.0, ge=0, description="fixed rebate coupon (SharkFin floor)")
    ko_barrier: float | None = Field(None, gt=0, description="up-and-out barrier (SharkFin)")
    ko_style: KIStyle = KIStyle.AMERICAN
    buffer: float = Field(0.0, ge=0, lt=1, description="downside buffer/airbag (Booster), e.g. 0.20")

    @model_validator(mode="after")
    def _check(self) -> "ParticipationSpec":
        if self.style is ParticipationStyle.SHARKFIN and self.ko_barrier is None:
            raise ValueError("SharkFin requires a knock-out barrier")
        return self


class TermSheet(BaseModel):
    notional: float = Field(..., gt=0)
    currency: str = "USD"
    trade_date: date
    strike_date: date
    maturity: date

    basket_mode: BasketMode = BasketMode.WORST_OF
    underlyings: list[Underlying] = Field(..., min_length=1, max_length=3)

    autocall: AutocallSchedule | None = None
    coupon: CouponSpec
    knock_in: KnockInSpec | None = None
    participation: ParticipationSpec | None = None  # if set -> SharkFin/Booster (ignores FCN blocks)

    @model_validator(mode="after")
    def _check(self) -> "TermSheet":
        if self.maturity <= self.strike_date:
            raise ValueError("maturity must be after strike_date")
        if self.autocall is not None:
            if any(d > self.maturity for d in self.autocall.dates):
                raise ValueError("autocall dates cannot exceed maturity")
            if any(d <= self.strike_date for d in self.autocall.dates):
                raise ValueError("autocall dates must be after strike_date")
        return self

    @property
    def n_assets(self) -> int:
        return len(self.underlyings)
