"""Simulation time grid.

Autocall/coupon use a sparse observation grid; American KI needs a dense daily
grid. ``TimeGrid`` carries both: a single ``times`` axis (year fractions from the
strike date) plus the indices of the coupon and autocall observations within it.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from fcn.core.calendar import frequency_to_months, periodic_schedule
from fcn.core.daycount import DayCount, year_fraction, year_fractions
from fcn.product.enums import KIStyle
from fcn.product.termsheet import TermSheet


@dataclass(frozen=True)
class TimeGrid:
    times: np.ndarray  # (n_steps+1,), times[0] == 0.0
    coupon_idx: np.ndarray  # indices into times of coupon observations
    autocall_idx: np.ndarray  # indices into times of autocall observations
    coupon_tau: np.ndarray  # year fraction of each coupon period
    maturity_idx: int
    daily: bool  # dense daily grid present (American KI)

    @property
    def n_steps(self) -> int:
        return self.times.size - 1


def build_grid(
    ts: TermSheet,
    daycount: DayCount = DayCount.ACT_365F,
    steps_per_year: int = 252,
) -> TimeGrid:
    base = ts.strike_date
    maturity_t = year_fraction(base, ts.maturity, daycount)

    coupon_months = frequency_to_months(ts.coupon.frequency.value)
    coupon_dates = periodic_schedule(base, ts.maturity, coupon_months)
    coupon_times = np.array(year_fractions(base, coupon_dates, daycount), dtype=float)
    prev = 0.0
    taus = []
    for t in coupon_times:
        taus.append(float(t) - prev)
        prev = float(t)
    coupon_tau = np.array(taus, dtype=float)

    if ts.autocall is not None and ts.autocall.dates:
        autocall_times = np.array(year_fractions(base, ts.autocall.dates, daycount), dtype=float)
    else:
        autocall_times = np.array([], dtype=float)

    american = ts.knock_in is not None and ts.knock_in.style is KIStyle.AMERICAN
    if ts.participation is not None and ts.participation.ko_barrier is not None:
        american = american or ts.participation.ko_style is KIStyle.AMERICAN

    node_set: set[float] = set(coupon_times.tolist()) | set(autocall_times.tolist())
    node_set.add(maturity_t)
    if american:
        n = max(1, int(round(maturity_t * steps_per_year)))
        daily = np.arange(1, n + 1, dtype=float) / steps_per_year
        node_set |= set(daily[daily < maturity_t].tolist())

    times = np.unique(np.concatenate([[0.0], np.array(sorted(node_set), dtype=float)]))

    coupon_idx = np.searchsorted(times, coupon_times)
    autocall_idx = (
        np.searchsorted(times, autocall_times)
        if autocall_times.size
        else np.array([], dtype=int)
    )
    maturity_idx = int(times.size - 1)

    return TimeGrid(
        times=times,
        coupon_idx=coupon_idx.astype(int),
        autocall_idx=autocall_idx.astype(int),
        coupon_tau=coupon_tau,
        maturity_idx=maturity_idx,
        daily=american,
    )
