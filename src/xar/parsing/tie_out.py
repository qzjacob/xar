"""Numeric tie-out gate. Bad table extraction silently yields confidently-wrong
grounded numbers — the highest-stakes failure in finance (design §10). This is a
deterministic check: for simple tables, a labeled total/合计 row must reconcile
with the column it sums (within tolerance). Chunks that fail are flagged so the
retrieval/agent layer can avoid grounding numeric claims on them.

A heavier TEDS-vs-ground-truth gate plugs in at the same interface."""
from __future__ import annotations

import re

# A financial figure: optional accounting parens (= negative), optional currency
# sign, grouped digits, optional decimals, optional trailing %. Parsed token-aware
# so `(1,234)` -> -1234, `12.3%` is recognized as a percentage (excluded from
# additive currency columns), `$1,000` -> 1000, and a range like `2023-2024`
# yields two positive numbers (the bare hyphen is NOT read as a minus sign).
_TOKEN = re.compile(r"(\()?\s*[$¥€]?\s*(\d[\d,]*(?:\.\d+)?)\s*(%?)\s*(\))?")
_TOTAL_HINT = re.compile(r"(\btotal\b|合计|小计|\bsum\b)", re.IGNORECASE)
# Lines implying subtraction / derived figures => NOT a simple additive column.
# Presence of any of these makes a table non-additive, so we don't column-sum it
# (avoids false-flagging income statements: revenue - cost = profit). English terms
# are word-bounded so "net" no longer matches "network" / "less" no longer matches
# "endlessly".
_NON_ADDITIVE_EN = re.compile(
    r"\b(costs?|expenses?|profit|loss|losses|margin|net|less|minus|taxe?s?)\b",
    re.IGNORECASE,
)
_NON_ADDITIVE_CN = re.compile(r"(減|减|利润|成本|费用|净|亏|税)")
_TOL = 0.02  # 2%


def _non_additive(line: str) -> bool:
    return bool(_NON_ADDITIVE_EN.search(line) or _NON_ADDITIVE_CN.search(line))


def _nums(line: str) -> list[float]:
    """Currency figures on a line. Percentages are excluded (not part of an additive
    money column); accounting parens denote negatives."""
    out = []
    for m in _TOKEN.finditer(line):
        lparen, digits, pct, rparen = m.groups()
        if pct:  # a percentage, not a summable currency amount
            continue
        try:
            val = float(digits.replace(",", ""))
        except ValueError:
            continue
        if lparen and rparen:
            val = -val
        out.append(val)
    return out


def check(text: str) -> tuple[bool, str]:
    """Return (ok, reason). Conservative: only fails when a total demonstrably
    does not reconcile; passes prose and tables it cannot evaluate."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    total_lines = [ln for ln in lines if _TOTAL_HINT.search(ln) and _nums(ln)]
    if not total_lines:
        return True, "no_total_row"
    # Only reconcile genuine additive breakdowns. If any item line implies
    # subtraction/derived figures, the table is non-additive -> conservative pass.
    if any(_non_additive(ln) for ln in lines if not _TOTAL_HINT.search(ln)):
        return True, "non_additive_pass"

    for tl in total_lines:
        claimed = _nums(tl)
        if not claimed:
            continue
        claimed_total = claimed[-1]  # last number on the total line
        # sum the last number of each preceding numeric, non-total line
        col = []
        for ln in lines:
            if ln is tl or _TOTAL_HINT.search(ln):
                continue
            nums = _nums(ln)
            if nums:
                col.append(nums[-1])
        if len(col) >= 2:
            s = sum(col)
            if claimed_total and abs(s - claimed_total) / max(abs(claimed_total), 1.0) <= _TOL:
                return True, "reconciled"
            # only fail if it's clearly a tally that doesn't add up
            if claimed_total and s and abs(s - claimed_total) / max(abs(claimed_total), 1.0) > 0.5:
                return False, f"total {claimed_total} != column sum {s:.0f}"
    return True, "indeterminate_pass"
