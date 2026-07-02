"""Closed-form benchmarks used to validate the Monte Carlo engine."""

from fcn.analytics.closed_form import (
    black_scholes,
    booster_value,
    down_and_in_put_european,
    reiner_rubinstein_down_in_put,
    sharkfin_no_ko,
    single_name_european_note,
)

__all__ = [
    "black_scholes",
    "booster_value",
    "down_and_in_put_european",
    "reiner_rubinstein_down_in_put",
    "sharkfin_no_ko",
    "single_name_european_note",
]
