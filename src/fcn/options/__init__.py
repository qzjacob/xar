"""Single-name equity options module.

Layered on top of the existing market-data stack (Massive live option chains,
:class:`fcn.marketdata.volsurface.VolSurface`, :class:`fcn.analytics.closed_form`)
to deliver:

* :mod:`fcn.options.greeks`     — Black–Scholes Greeks (vectorised, incl. vanna/vomma/charm)
* :mod:`fcn.options.chain`      — :class:`OptionChain` (live Massive or abstract fallback)
* :mod:`fcn.options.strategies` — :class:`StrategySpec` + 21 named-strategy factories
* :mod:`fcn.options.strategy_engine` — composition, aggregate Greeks, payoff, MoP/MoL
* :mod:`fcn.options.analytics`  — IV-surface analytics (skew, term, RR, BF, IV–RV)
* :mod:`fcn.options.view`       — :class:`FundamentalView` + deterministic view→family map
* :mod:`fcn.options.advisor`    — LLM advisor: view → ranked candidates + narrative
* :mod:`fcn.options.blotter`    — local position blotter (aggregated Greeks)

Scope and limitations are documented in ``docs/OPTIONS_REVIEW.md``.
"""
