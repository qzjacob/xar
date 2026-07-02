"""Discount and forward curves.

Two curves are kept deliberately separate (a classic source of bugs, see plan §2.2):

* :class:`DiscountCurve` discounts the note's cashflows on the *issuer funding* rate.
* :class:`ForwardCurve` builds the equity forward from spot, the *risk-free* rate,
  the (continuous + discrete) dividends and the borrow/repo cost. The path
  generator drives drift off ``log_drift`` so the simulated spot is a martingale
  to the forward regardless of the diffusion (flat vol or local vol).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass(frozen=True)
class DiscountCurve:
    """Flat-rate discount curve (continuously compounded)."""

    rate: float

    def df(self, t: np.ndarray | float) -> np.ndarray | float:
        return np.exp(-self.rate * np.asarray(t, dtype=float))

    def zero(self, t: np.ndarray | float) -> float:  # noqa: ARG002 - flat curve
        return self.rate


@dataclass(frozen=True)
class DiscreteDividend:
    t: float  # year fraction to ex-date
    amount: float  # cash amount (currency units, absolute)


@dataclass(frozen=True)
class ForwardCurve:
    """Equity forward ``F(t)``.

    Standard discrete-dividend forward, consistent with the path model (capital
    grows at ``mu = r - q - borrow`` and drops by each cash dividend on its
    ex-date — see pathgen):

        F(t) = S0 * e^{mu*t} - sum_{t_i <= t} d_i * e^{mu*(t - t_i)}

    With no discrete dividends this is the usual ``S0 * e^{mu*t}``. ``rate`` is the
    growth rate, distinct from the funding rate used to *discount* cashflows.
    """

    spot: float
    rate: float  # risk-free growth rate
    div_yield: float = 0.0  # continuous dividend yield
    borrow: float = 0.0  # borrow / repo cost
    dividends: tuple[DiscreteDividend, ...] = field(default_factory=tuple)

    @property
    def mu(self) -> float:
        return self.rate - self.div_yield - self.borrow

    def forward(self, t: np.ndarray | float) -> np.ndarray:
        t = np.asarray(t, dtype=float)
        base = self.spot * np.exp(self.mu * t)
        if not self.dividends:
            return base
        flat = np.atleast_1d(t)
        sub = np.array(
            [sum(d.amount * np.exp(self.mu * (x - d.t)) for d in self.dividends if d.t <= x)
             for x in flat]
        )
        out = base - sub.reshape(t.shape) if t.shape else float(base) - float(sub[0])
        return out

    def log_drift(self, t0: float, t1: float) -> float:
        """``ln F(t1) - ln F(t0)`` — deterministic log drift (continuous-div case)."""
        f0 = float(np.atleast_1d(self.forward(t0))[0])
        f1 = float(np.atleast_1d(self.forward(t1))[0])
        return float(np.log(f1) - np.log(f0))
