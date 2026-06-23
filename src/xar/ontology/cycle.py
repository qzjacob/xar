"""Economic-cycle positioning — a code-as-truth ontology dimension that organizes
the consumer-facing themes (internet / retail / restaurants) the way the
supply-chain `tier` axis organizes the AI-chain themes.

The five AI themes track an industry *chain* (upstream→downstream, tier-ordered).
Consumer themes have no such chain; what differentiates their sub-segments is where
they sit in the macro/consumer cycle — discount retail is *counter-cyclical* (it
benefits from trade-down and falls last), grocery is *defensive*, apparel is
*early-cycle* (high beta, rolls over first). This module supplies that vocabulary:
a 5-state `CyclePosition`, a coarse `Cyclicality` bucket, a per-segment
`CycleProfile`, and a monotonic `CYCLE_RANK` ("the later you fall, the higher the
rank") that doubles as the segment `tier` so the existing dashboard / heatmap
ordering renders the cycle axis with ZERO changes.

A company inherits its profile from its segment unless it carries an explicit
override — mirroring how `sectors.industry_of_company` resolves industry. To avoid
an ontology↔registry import cycle, the segment lookup is a lazy import.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .sectors import _primary_seg


class CyclePosition(str, Enum):
    EARLY = "early_cycle"        # recovers first, rolls over first; high beta (apparel, online travel, casual dining)
    MID = "mid_cycle"            # mid-expansion (fast-casual, payments, marketplace e-commerce)
    LATE = "late_cycle"          # peaks late, leads into the slowdown
    DEFENSIVE = "defensive"      # non-discretionary, low beta (grocery, subscription streaming, auto-parts repair)
    COUNTER = "counter_cyclical"  # benefits in a downturn, falls last (discount / off-price / warehouse / QSR trade-down)


class Cyclicality(str, Enum):
    CYCLICAL = "cyclical"
    DEFENSIVE = "defensive"
    COUNTER_CYCLICAL = "counter_cyclical"


@dataclass(frozen=True)
class CycleProfile:
    position: str            # CyclePosition value
    cyclicality: str         # Cyclicality value
    sensitivity: float = 1.0  # beta hint (1.4 high / 1.0 market / 0.6 low) — tie-breaker within a rank
    note: str = ""
    noteCn: str = ""


# Monotonic "how late do you fall" axis. Reused verbatim as the segment `tier`
# for cycle themes, so _theme_segment_ids() / ChainHeatmap sort along the cycle.
CYCLE_RANK: dict[str, int] = {
    CyclePosition.EARLY.value: 1,
    CyclePosition.MID.value: 2,
    CyclePosition.LATE.value: 3,
    CyclePosition.DEFENSIVE.value: 4,
    CyclePosition.COUNTER.value: 5,
}

CYCLE_LABELS: dict[str, dict] = {
    CyclePosition.EARLY.value: {"en": "Early-Cycle", "cn": "早周期", "short": "EC"},
    CyclePosition.MID.value: {"en": "Mid-Cycle", "cn": "中周期", "short": "MC"},
    CyclePosition.LATE.value: {"en": "Late-Cycle", "cn": "晚周期", "short": "LC"},
    CyclePosition.DEFENSIVE.value: {"en": "Defensive", "cn": "防御", "short": "DEF"},
    CyclePosition.COUNTER.value: {"en": "Counter-Cyclical", "cn": "逆周期", "short": "CC"},
}

CYCLE_POSITIONS = [p.value for p in CyclePosition]

# Default cyclicality bucket for a position — lets a *partial* company-level
# override (e.g. {"position": "mid_cycle"}) still serialize to the full shape.
_CYCLICALITY_BY_POSITION: dict[str, str] = {
    CyclePosition.EARLY.value: Cyclicality.CYCLICAL.value,
    CyclePosition.MID.value: Cyclicality.CYCLICAL.value,
    CyclePosition.LATE.value: Cyclicality.CYCLICAL.value,
    CyclePosition.DEFENSIVE.value: Cyclicality.DEFENSIVE.value,
    CyclePosition.COUNTER.value: Cyclicality.COUNTER_CYCLICAL.value,
}


def profile(position: CyclePosition | str, cyclicality: Cyclicality | str,
            sensitivity: float = 1.0, note: str = "", noteCn: str = "") -> CycleProfile:
    """Factory used inline in registry.SEGMENTS — accepts enums or raw strings."""
    return CycleProfile(
        position=position.value if isinstance(position, CyclePosition) else position,
        cyclicality=cyclicality.value if isinstance(cyclicality, Cyclicality) else cyclicality,
        sensitivity=sensitivity, note=note, noteCn=noteCn,
    )


def rank(position: str | None) -> int:
    """Cycle rank (== segment tier for cycle themes). Unknown → 0 (sorts first)."""
    return CYCLE_RANK.get(position or "", 0)


def label(position: str | None) -> dict:
    return CYCLE_LABELS.get(position or "", {"en": position or "", "cn": "", "short": ""})


def as_dict(p: CycleProfile | dict | None) -> dict | None:
    """Serialize a CycleProfile — or normalize an already-serialized / partial
    override dict — to the EXACT frontend `CycleInfo` shape (position, cyclicality,
    sensitivity, label, labelCn, short, rank, note, noteCn).

    A single normalization path so a `CycleProfile`, a fully round-tripped dict
    (read back from `companies.meta.cycle`), and a partial company-level override
    like `{"position": "mid_cycle"}` all produce the identical shape — never a
    missing-field dict that renders `undefined` on the client (CODE_REVIEW B.1.1)."""
    if p is None:
        return None
    if isinstance(p, CycleProfile):
        p = {"position": p.position, "cyclicality": p.cyclicality, "sensitivity": p.sensitivity,
             "note": p.note, "noteCn": p.noteCn}
    pos = p.get("position")
    lbl = label(pos)
    return {
        "position": pos,
        "cyclicality": p.get("cyclicality") or _CYCLICALITY_BY_POSITION.get(pos, ""),
        "sensitivity": p.get("sensitivity", 1.0),
        "label": p.get("label") or lbl["en"],
        "labelCn": p.get("labelCn") or lbl["cn"],
        "short": p.get("short") or lbl["short"],
        "rank": p.get("rank", rank(pos)),
        "note": p.get("note", ""),
        "noteCn": p.get("noteCn", ""),
    }


def cycle_of_company(company: dict | None) -> dict | None:
    """Resolve a company's cycle profile: an explicit `cycle` override on the
    company/meta wins; otherwise inherit from its segment's CycleProfile. Returns
    the serialized dict (or None for chain-theme / unclassified names)."""
    if not company:
        return None
    override = company.get("cycle") or (company.get("meta") or {}).get("cycle")
    if override:
        return as_dict(override)
    seg = _primary_seg(company)
    if not seg:
        return None
    from ..ingestion.registry import SEGMENTS  # lazy: avoids ontology↔registry import cycle

    prof = (SEGMENTS.get(seg) or {}).get("cycle")
    return as_dict(prof)
