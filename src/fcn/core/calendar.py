"""Minimal schedule generation and business-day rolling.

MVP scope: weekend rolling only (modified-following). A named holiday calendar
(NYSE/TARGET/24x7) is a documented P0 gap to be filled via a QuantLib adapter.
"""

from __future__ import annotations

from datetime import date, timedelta


def is_business_day(d: date) -> bool:
    """True on Mon–Fri (weekend-only calendar; holidays not yet modelled)."""
    return d.weekday() < 5


def roll(d: date, convention: str = "following") -> date:
    """Roll ``d`` to a business day.

    ``following`` -> next business day; ``modified_following`` -> next business day
    unless it crosses into the next month, in which case the previous business day;
    ``preceding`` -> previous business day.
    """
    if is_business_day(d):
        return d
    if convention == "preceding":
        out = d
        while not is_business_day(out):
            out -= timedelta(days=1)
        return out
    out = d
    while not is_business_day(out):
        out += timedelta(days=1)
    if convention == "modified_following" and out.month != d.month:
        out = d
        while not is_business_day(out):
            out -= timedelta(days=1)
    return out


def _add_months(d: date, months: int) -> date:
    m0 = d.month - 1 + months
    year = d.year + m0 // 12
    month = m0 % 12 + 1
    # Clamp day to month length.
    day = d.day
    for last in (31, 30, 29, 28):
        try:
            return date(year, month, min(day, last))
        except ValueError:
            continue
    raise ValueError("could not build date")


def periodic_schedule(
    start: date,
    end: date,
    months: int,
    convention: str = "modified_following",
    include_start: bool = False,
) -> list[date]:
    """Generate observation/coupon dates from ``start`` to ``end`` every ``months``.

    The final date is forced to ``end`` (rolled). Dates are business-day adjusted.
    """
    if months <= 0:
        raise ValueError("months must be positive")
    out: list[date] = []
    if include_start:
        out.append(roll(start, convention))
    k = 1
    while True:
        d = _add_months(start, months * k)
        if d >= end:
            break
        out.append(roll(d, convention))
        k += 1
    out.append(roll(end, convention))
    # Deduplicate while preserving order (rolling can collide near month ends).
    seen: set[date] = set()
    unique: list[date] = []
    for d in out:
        if d not in seen:
            seen.add(d)
            unique.append(d)
    return unique


def frequency_to_months(frequency: str) -> int:
    table = {"monthly": 1, "quarterly": 3, "semiannual": 6, "annual": 12}
    if frequency not in table:
        raise ValueError(f"unknown frequency: {frequency!r}")
    return table[frequency]
