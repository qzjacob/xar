"""Exploration module — the frontier-of-knowledge research layer.

A third top-level module (peer to the Research Portal and the Operations Console).
It tracks the leading edge of human knowledge — AI, physics, math, CS, neuro/
cognition, complex systems / geopolitics — by ingesting arXiv preprints + expert
voices (X) + professional sources, then synthesizing forward-looking *research
fronts* per domain (a "section"). Emphasis is long-horizon direction, not trades.

It deliberately reuses the existing stack: `documents` (storage), embeddings,
`models.llm` (synthesis), and the same FastAPI/SPA chrome. AI is the first section.
"""
from .domains import DOMAINS, DOMAINS_BY_ID, domain_by_id  # noqa: F401
