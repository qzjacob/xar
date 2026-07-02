"""Product domain model: the single composable TermSheet schema for all variants."""

from fcn.product.enums import (
    BasketMode,
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

__all__ = [
    "BasketMode",
    "CouponType",
    "Frequency",
    "KIStyle",
    "ParticipationStyle",
    "Settlement",
    "AutocallSchedule",
    "CouponSpec",
    "KnockInSpec",
    "ParticipationSpec",
    "TermSheet",
    "Underlying",
]
