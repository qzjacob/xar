"""Enumerations for the product model.

Variants are expressed by *combining* these flags — there is deliberately no
``product_type`` enum that switches engine code paths (see plan §1.5).
"""

from __future__ import annotations

from enum import Enum


class BasketMode(str, Enum):
    WORST_OF = "worst_of"
    WEIGHTED = "weighted"


class CouponType(str, Enum):
    FIXED = "fixed"  # classic FCN: guaranteed coupon while alive
    CONDITIONAL = "conditional"  # Phoenix: paid only if worst-of >= coupon barrier


class Frequency(str, Enum):
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    SEMIANNUAL = "semiannual"
    ANNUAL = "annual"


class KIStyle(str, Enum):
    EUROPEAN = "european"  # observed only at maturity
    AMERICAN = "american"  # daily/continuous monitoring (Brownian-bridge corrected)


class Settlement(str, Enum):
    CASH = "cash"  # cash shortfall
    PHYSICAL = "physical"  # deliver worst performer at strike


class ParticipationStyle(str, Enum):
    SHARKFIN = "sharkfin"  # capped participation + up-and-out knock-out + coupon floor
    BOOSTER = "booster"  # downside buffer (airbag) + capped upside participation
