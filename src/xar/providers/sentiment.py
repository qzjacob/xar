"""Dependency-free lexicon sentiment scorer for social/news snippets.

Deliberately tiny and finance-tuned: it is a cheap pre-filter (which posts are
worth surfacing / mirroring into RAG), not a trading signal. The LLM analysts do
the real reading. Returns a score in [-1, 1]."""
from __future__ import annotations

import re

_POS = {"beat", "beats", "surge", "surged", "record", "ramp", "ramping", "design win",
        "win", "wins", "upgrade", "upgraded", "outperform", "strong", "growth", "demand",
        "bullish", "tailwind", "accelerate", "raised", "guidance up", "sold out", "shortage"}
_NEG = {"miss", "missed", "cut", "cuts", "downgrade", "downgraded", "weak", "slump",
        "plunge", "plunged", "bearish", "headwind", "glut", "oversupply", "delay",
        "delayed", "warning", "lawsuit", "recall", "loss", "losses", "decline", "slowdown"}
_TOK = re.compile(r"[a-z][a-z'\-]+")


def score(text: str) -> float:
    if not text:
        return 0.0
    toks = _TOK.findall(text.lower())
    if not toks:
        return 0.0
    pos = sum(t in _POS for t in toks)
    neg = sum(t in _NEG for t in toks)
    if pos + neg == 0:
        return 0.0
    return round((pos - neg) / (pos + neg), 3)
