"""Local position blotter: CRUD + aggregated Greeks.

The blotter is the *persistent* counterpart to the in-memory strategy
valuations. Saved entries (a :class:`BlotterEntry` wraps a
:class:`fcn.options.strategies.StrategySpec` plus the valuation snapshot taken
at entry) are written atomically to a JSON file under ``~/.fcn/blotter.json``,
so closing and reopening the desk doesn't lose positions.

Aggregated Greeks across open positions are computed by revaluing each entry
against either a freshly-built chain (live) or the stored snapshot (offline).

Scope for v1:
  * Add / remove / list entries; mark open/closed/rolled.
  * Aggregate delta/gamma/vega/theta across open positions, with current
    mark-to-market P&L when a live chain is supplied.
  * No auto-rollover, no tax lots, no multi-account (those are v2).
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from uuid import uuid4

from fcn.options.strategy_engine import StrategyValuation
from fcn.options.strategies import StrategySpec


DEFAULT_BLOTTER_PATH = Path.home() / ".fcn" / "blotter.json"

EntryStatus = Literal["open", "closed", "rolled"]


@dataclass
class BlotterEntry:
    """One persisted position."""

    id: str
    ts: str                          # ISO timestamp
    strategy: StrategySpec
    valuation_snapshot: StrategyValuation
    notes: str = ""
    status: EntryStatus = "open"

    def to_dict(self) -> dict:
        return {
            "id": self.id, "ts": self.ts,
            "strategy": self.strategy.model_dump(mode="json"),
            "valuation_snapshot": self.valuation_snapshot.to_dict(),
            "notes": self.notes, "status": self.status,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "BlotterEntry":
        spec = StrategySpec.model_validate(d["strategy"])
        snap_dict = d["valuation_snapshot"]
        # Rebuild a StrategyValuation from the dict (light-weight; no chain).
        snap = _valuation_from_dict(snap_dict)
        return cls(
            id=d["id"], ts=d["ts"], strategy=spec, valuation_snapshot=snap,
            notes=d.get("notes", ""), status=d.get("status", "open"),
        )


@dataclass
class PortfolioGreeks:
    """Portfolio-level Greeks + P&L across open entries.

    Distinct from :class:`fcn.options.strategy_engine.AggregatedGreeks` (which is
    per-strategy and carries vanna/vomma): this is a book-level roll-up that adds
    notional / position count / P&L / per-underlying sub-totals.
    """

    delta: float
    gamma: float
    vega: float
    theta: float
    rho: float
    notional_exposure: float
    n_positions: int
    current_pnl: float | None        # None when no live revaluation was performed
    by_underlying: dict[str, dict]   # per-ticker sub-totals

    def to_dict(self) -> dict:
        return {
            "delta": round(self.delta, 4),
            "gamma": round(self.gamma, 4),
            "vega": round(self.vega, 4),
            "theta": round(self.theta, 4),
            "rho": round(self.rho, 4),
            "notional_exposure": round(self.notional_exposure, 2),
            "n_positions": self.n_positions,
            "current_pnl": None if self.current_pnl is None else round(self.current_pnl, 2),
            "by_underlying": self.by_underlying,
        }


class BlotterStore:
    """File-backed blotter with a process-local lock.

    Atomic writes via ``tmp + rename`` so a crashed process can't corrupt the
    file. Designed for the single-user, single-node desk workflow — for a
    multi-user backend this is the seam to swap in a DB.
    """

    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path else DEFAULT_BLOTTER_PATH
        self._lock = threading.Lock()
        self._ensure_parent()

    def _ensure_parent(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)

    # ---- CRUD -----------------------------------------------------------

    def all(self) -> list[BlotterEntry]:
        with self._lock:
            if not self.path.exists():
                return []
            data = json.loads(self.path.read_text() or "[]")
        return _parse_entries(data)

    def add(self, entry: BlotterEntry) -> BlotterEntry:
        with self._lock:
            entries = self._read_locked()
            entries.append(entry)
            self._write_locked(entries)
        return entry

    def remove(self, entry_id: str) -> bool:
        with self._lock:
            entries = self._read_locked()
            new = [e for e in entries if e.id != entry_id]
            removed = len(new) < len(entries)
            if removed:
                self._write_locked(new)
        return removed

    def update(self, entry_id: str, **fields) -> BlotterEntry | None:
        with self._lock:
            entries = self._read_locked()
            for e in entries:
                if e.id == entry_id:
                    if "notes" in fields:
                        e.notes = fields["notes"]
                    if "status" in fields:
                        e.status = fields["status"]
                    self._write_locked(entries)
                    return e
            return None

    # ---- Aggregated Greeks ---------------------------------------------

    def aggregate(
        self, *, live_valuations: dict[str, StrategyValuation] | None = None,
    ) -> PortfolioGreeks:
        """Sum Greeks across open entries.

        ``live_valuations`` is an optional ``{entry_id: current_valuation}``
        map — when supplied, ``current_pnl`` is computed by revaluing each
        position against fresh market data. When omitted, only the snapshot
        Greeks are summed and ``current_pnl`` is None.
        """
        agg = {"delta": 0.0, "gamma": 0.0, "vega": 0.0, "theta": 0.0, "rho": 0.0,
               "notional": 0.0, "pnl": 0.0}
        per_ticker: dict[str, dict] = {}
        n_open = 0

        for entry in self.all():
            if entry.status != "open":
                continue
            n_open += 1
            snap = entry.valuation_snapshot
            agg["delta"] += snap.greeks.delta
            agg["gamma"] += snap.greeks.gamma
            agg["vega"] += snap.greeks.vega
            agg["theta"] += snap.greeks.theta
            agg["rho"] += snap.greeks.rho
            agg["notional"] += abs(snap.net_debit)

            ticker = entry.strategy.ticker
            t = per_ticker.setdefault(ticker, {
                "delta": 0.0, "vega": 0.0, "theta": 0.0, "notional": 0.0,
                "n_positions": 0, "pnl": 0.0,
            })
            t["delta"] += snap.greeks.delta
            t["vega"] += snap.greeks.vega
            t["theta"] += snap.greeks.theta
            t["notional"] += abs(snap.net_debit)
            t["n_positions"] += 1

            if live_valuations and entry.id in live_valuations:
                # P&L = current portfolio value − entry value.
                # For long (debit) positions: pnl = -(current.debit − entry.debit)
                #   (collecting credit by paying less is profit)
                # For short (credit) positions: pnl = (current.debit − entry.debit)
                #   (buying back cheaper is profit)
                cur = live_valuations[entry.id]
                # Generic: profit = entry_value − current_value (you sold high or bought low)
                entry_value = -snap.net_debit    # what you received (credit positive)
                cur_value = -cur.net_debit
                pnl = entry_value - cur_value
                agg["pnl"] += pnl
                t["pnl"] += pnl

        # Round per-ticker sub-totals.
        for t in per_ticker.values():
            for k in ("delta", "vega", "theta", "notional", "pnl"):
                t[k] = round(t[k], 4)

        return PortfolioGreeks(
            delta=agg["delta"], gamma=agg["gamma"], vega=agg["vega"],
            theta=agg["theta"], rho=agg["rho"],
            notional_exposure=agg["notional"], n_positions=n_open,
            current_pnl=agg["pnl"] if live_valuations else None,
            by_underlying=per_ticker,
        )

    # ---- internal ------------------------------------------------------

    def _read_locked(self) -> list[BlotterEntry]:
        if not self.path.exists():
            return []
        data = json.loads(self.path.read_text() or "[]")
        return _parse_entries(data)

    def _write_locked(self, entries: list[BlotterEntry]) -> None:
        """Atomic write: tmp file in same dir + rename."""
        payload = json.dumps([e.to_dict() for e in entries], default=str)
        fd, tmp_path = tempfile.mkstemp(dir=str(self.path.parent), prefix=".blotter-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                f.write(payload)
            os.replace(tmp_path, self.path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_entries(data: list) -> list[BlotterEntry]:
    """Parse stored rows into entries, SKIPPING any corrupt/legacy row.

    A single malformed entry (hand-edited file, schema drift) must not 500 the
    whole blotter — the desk needs to still list and remove the good rows.
    """
    out: list[BlotterEntry] = []
    for d in data:
        try:
            out.append(BlotterEntry.from_dict(d))
        except Exception:  # noqa: BLE001 - skip the bad row, keep the book usable
            continue
    return out


def new_entry(strategy: StrategySpec, valuation: StrategyValuation, *,
              notes: str = "") -> BlotterEntry:
    """Create a fresh blotter entry (caller then ``store.add(entry)``)."""
    return BlotterEntry(
        id=uuid4().hex[:12],
        ts=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        strategy=strategy, valuation_snapshot=valuation, notes=notes, status="open",
    )


def _valuation_from_dict(d: dict) -> StrategyValuation:
    """Rebuild a StrategyValuation from its serialized form (no live market)."""
    from fcn.options.strategy_engine import AggregatedGreeks
    g = d.get("greeks", {})
    return StrategyValuation(
        net_debit=d.get("net_debit", 0.0),
        greeks=AggregatedGreeks(
            delta=g.get("delta", 0.0), gamma=g.get("gamma", 0.0),
            vega=g.get("vega", 0.0), theta=g.get("theta", 0.0),
            rho=g.get("rho", 0.0), vanna=g.get("vanna", 0.0), vomma=g.get("vomma", 0.0),
        ),
        payoff_at_expiry=[],  # discarded — recomputed on demand from the spec
        breakevens=d.get("breakevens", []),
        max_profit=d.get("max_profit"),
        max_loss=d.get("max_loss"),
        prob_profit=d.get("prob_profit", 0.0),
        margin_estimate=d.get("margin_estimate"),
        contracts_audit=[],
        days_to_expiry=d.get("days_to_expiry", 0),
        underlying_price=d.get("underlying_price", 0.0),
        valuation_date=d.get("valuation_date", ""),
        exec_net_debit=d.get("exec_net_debit", d.get("net_debit", 0.0)),
        slippage=d.get("slippage", 0.0),
        liquidity=d.get("liquidity"),
        liquidity_remaps=d.get("liquidity_remaps", []),
        effective_strategy=d.get("effective_strategy"),
    )
