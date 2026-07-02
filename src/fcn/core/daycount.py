"""Day-count conventions and date -> year-fraction helpers.

MVP ships ACT/365F and 30/360; a QuantLib adapter can be slotted behind
:func:`year_fraction` later (see plan §3.1). Holiday calendars are a known P0 gap
documented in the plan — only weekend rolling is handled here (see calendar.py).
"""

from __future__ import annotations

from datetime import date
from enum import Enum


class DayCount(str, Enum):
    ACT_365F = "ACT/365F"
    ACT_360 = "ACT/360"
    THIRTY_360 = "30/360"


def year_fraction(start: date, end: date, convention: DayCount = DayCount.ACT_365F) -> float:
    """Year fraction between ``start`` and ``end`` under ``convention``."""
    if end < start:
        raise ValueError("end must not precede start")
    if convention is DayCount.ACT_365F:
        return (end - start).days / 365.0
    if convention is DayCount.ACT_360:
        return (end - start).days / 360.0
    if convention is DayCount.THIRTY_360:
        d1 = min(start.day, 30)
        d2 = min(end.day, 30) if d1 == 30 else end.day
        return (
            360 * (end.year - start.year)
            + 30 * (end.month - start.month)
            + (d2 - d1)
        ) / 360.0
    raise ValueError(f"unknown day-count: {convention!r}")


def year_fractions(base: date, dates: list[date], convention: DayCount = DayCount.ACT_365F) -> list[float]:
    """Year fractions from a common ``base`` to each date in ``dates``."""
    return [year_fraction(base, d, convention) for d in dates]
