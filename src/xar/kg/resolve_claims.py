"""Close the forward-claim loop.

The semantic layer records `time_orientation='forward_looking'` for catalysts that are
expectations about the future (guidance, order pipeline, forecasts), but that mark is
write-only: nothing ever says whether the expectation came true. This stage closes it —
the one expectation→realization capability that is un-derivable from the structured
`fundamentals`/`estimates`/`prices` tables and was the single real gap in the shipped
semantic DB.

Design constraints (lean, no new table):
- Writes resolution ONLY on `forward_looking` rows. Backward hard-fact catalysts (the
  majority) are never touched, so the event log stays append-only where it matters.
- `resolution`: NULL = unresolved; terminal `hit | miss`; `stale` is non-terminal — it is
  re-checked every run so a backdated / late-ingested realizer can still upgrade it.
- Dated by `COALESCE(event_date, observed_at::date)` on BOTH the claim and the realizer:
  most real forward claims (esp. expert-mirrored news/social) have `event_date NULL`, so
  matching on event_date alone would never fire — observed_at (when we learned it) is the
  available, PIT-safe anchor.
- Precise, not noisy: only DIRECTIONAL claims (polarity ±) resolve, only against a later
  DIRECTIONAL realizer of a *realization* event_type (a `_REALIZER_TYPES` hard outcome —
  not an unrelated litigation/short-report/management-change headline). hit if the
  polarities agree, miss if they oppose.
"""
from __future__ import annotations

from datetime import date, timedelta

from ..logging import get_logger
from ..storage import db

log = get_logger("xar.resolve_claims")

# Backward catalyst types that actually constitute the *realization* of a forward demand /
# guidance / order / capacity expectation. Noise types (litigation, short_report,
# management_change, regulatory_action, mna, buyback, dividend, index_inclusion, …) never
# confirm or refute such an expectation and are excluded as realizers — temporal adjacency
# alone must not fabricate an expectation→realization link.
_REALIZER_TYPES = (
    "earnings", "guidance_change", "capex_guidance", "order", "contract_win",
    "product_ramp", "accelerator_launch", "capacity_expansion", "qualification",
    "supply_constraint", "pricing_change", "tech_substitution",
)
_SIGN = {"positive": 1, "negative": -1}  # neutral excluded: non-directional, no hit/miss


def resolve_forward_claims(window_days: int = 120, grace_days: int = 21) -> dict:
    """Resolve directional forward-looking catalysts whose realization window has opened.

    For each forward_looking event with a directional polarity, not yet terminally resolved
    (NULL or 'stale'), older than `grace_days`: find the earliest later same-company realized
    (backward_looking, directional, realization-type) event within `window_days`, dated by
    COALESCE(event_date, observed_at) on both sides. hit if polarities agree, miss if they
    oppose; else once the window fully lapses, 'stale' (still re-checked next run). Writes
    ONLY forward rows; idempotent. Returns counts."""
    today = date.today()
    claims = db.query(
        """SELECT id, company_id, polarity, resolution,
                  COALESCE(event_date, observed_at::date) AS base
             FROM kg_events
            WHERE time_orientation = 'forward_looking'
              AND (resolution IS NULL OR resolution = 'stale')
              AND invalidated_at IS NULL
              AND company_id IS NOT NULL
              AND polarity IN ('positive', 'negative')
              AND COALESCE(event_date, observed_at::date) < (CURRENT_DATE - (%s || ' days')::interval)""",
        (grace_days,),
    )
    stats = {"evaluated": len(claims), "hit": 0, "miss": 0, "stale": 0, "still_open": 0}
    for c in claims:
        base = c["base"]
        realizer = db.query(
            """SELECT id, polarity FROM kg_events
                WHERE company_id = %s AND id <> %s AND invalidated_at IS NULL
                  AND time_orientation = 'backward_looking'
                  AND polarity IN ('positive', 'negative')
                  AND event_type = ANY(%s)
                  AND COALESCE(event_date, observed_at::date) > %s
                  AND COALESCE(event_date, observed_at::date) <= (%s::date + (%s || ' days')::interval)
                ORDER BY COALESCE(event_date, observed_at::date) ASC LIMIT 1""",
            (c["company_id"], c["id"], list(_REALIZER_TYPES), base, base, window_days),
        )
        if realizer:
            res = "hit" if _SIGN[c["polarity"]] == _SIGN[realizer[0]["polarity"]] else "miss"
            db.execute(
                "UPDATE kg_events SET resolution=%s, resolved_at=now(), realizes_event_id=%s WHERE id=%s",
                (res, realizer[0]["id"], c["id"]),
            )
            stats[res] += 1
        elif base + timedelta(days=window_days) < today:
            if c["resolution"] != "stale":   # only write the first time the window lapses
                db.execute("UPDATE kg_events SET resolution='stale', resolved_at=now() WHERE id=%s", (c["id"],))
            stats["stale"] += 1
        else:
            stats["still_open"] += 1
    log.info("resolve_forward_claims: %s", stats)
    return stats
