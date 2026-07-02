import { Boxes, Clock } from "lucide-react";
import {
  MARKETS,
  PERIODS,
  REGIME_LABEL,
  type CoverageMeta,
  type Market,
  type Period,
  type Regime,
} from "../types";
import { cn, fmtScore, marketLabel, regimeChip, relTime } from "../lib/format";
import { Badge } from "./ui";

/**
 * Global terminal top bar: anchors the active research theme + chain regime on
 * the left, the period control center-left, and coverage stats + market filter
 * on the right. Stateless — all selection is driven through the callbacks.
 */
export function TopBar({
  coverage,
  regime,
  theme,
  onTheme,
  market,
  onMarket,
  period,
  onPeriod,
}: {
  coverage: CoverageMeta;
  regime: Regime;
  theme: string;
  onTheme: (t: string) => void;
  market: Market;
  onMarket: (m: Market) => void;
  period: Period;
  onPeriod: (p: Period) => void;
}) {
  const themes = coverage.themes.filter((t) => t.active);

  return (
    <div className="flex h-14 shrink-0 items-center justify-between gap-4 border-b border-line bg-surface px-5">
      {/* Left: theme selector + chain regime */}
      <div className="flex min-w-0 items-center gap-3">
        <div className="flex min-w-0 items-center gap-1.5 text-brand-900">
          <Boxes size={16} strokeWidth={2} className="shrink-0 text-accent" />
          <select
            value={theme}
            onChange={(e) => onTheme(e.target.value)}
            aria-label="Industry-chain theme"
            className="max-w-[210px] cursor-pointer truncate rounded-md border border-line bg-surface py-1 pl-1.5 pr-6 text-sm font-semibold tracking-tight text-brand-900 transition hover:bg-canvas focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
          >
            {themes.map((t) => (
              <option key={t.id} value={t.id}>
                {t.name}
              </option>
            ))}
          </select>
        </div>
        <span className="hidden h-5 w-px shrink-0 bg-line sm:block" />
        <div className="hidden shrink-0 items-center gap-1.5 sm:flex">
          <Badge className={regimeChip(regime.phase)}>{REGIME_LABEL[regime.phase].en}</Badge>
          <span className="text-2xs uppercase tracking-wide text-slate-400">
            score <span className="tnum font-semibold text-slate-400">{fmtScore(regime.score)}</span>
          </span>
        </div>
      </div>

      {/* Center-left: period control */}
      <Segmented
        items={PERIODS}
        active={period}
        onSelect={onPeriod}
        label={(p) => p}
        ariaLabel="Period"
        activeClass="bg-surface text-white"
      />

      {/* Right: coverage stats + market filter */}
      <div className="flex min-w-0 items-center gap-4">
        <div className="hidden min-w-0 items-center gap-3 text-2xs text-slate-400 lg:flex">
          <span className="whitespace-nowrap">
            <span className="tnum font-semibold text-slate-400">{coverage.companyCount}</span>{" "}
            companies ·{" "}
            <span className="tnum font-semibold text-slate-400">{coverage.segmentCount}</span>{" "}
            segments
          </span>
          <span className="flex items-center gap-1 whitespace-nowrap">
            <Clock size={13} strokeWidth={2} className="text-slate-400" />
            Updated {relTime(coverage.updatedAt)}
          </span>
        </div>
        <Segmented
          items={MARKETS}
          active={market}
          onSelect={onMarket}
          label={(m) => marketLabel(m)}
          ariaLabel="Market filter"
          activeClass="bg-accent text-white"
        />
      </div>
    </div>
  );
}

/** Compact segmented pill group sharing the terminal control vocabulary. */
function Segmented<T extends string>({
  items,
  active,
  onSelect,
  label,
  ariaLabel,
  activeClass,
}: {
  items: readonly T[];
  active: T;
  onSelect: (value: T) => void;
  label: (value: T) => string;
  ariaLabel: string;
  activeClass: string;
}) {
  return (
    <div
      role="group"
      aria-label={ariaLabel}
      className="flex shrink-0 items-center gap-0.5 rounded-lg border border-line bg-canvas p-0.5"
    >
      {items.map((item) => {
        const isActive = item === active;
        return (
          <button
            key={item}
            type="button"
            aria-pressed={isActive}
            onClick={() => onSelect(item)}
            className={cn(
              "tnum rounded-md px-2 py-1 text-2xs font-semibold uppercase tracking-wide transition-colors",
              isActive
                ? cn(activeClass, "shadow-sm")
                : "text-slate-400 hover:bg-surface-2 hover:text-brand-900",
            )}
          >
            {label(item)}
          </button>
        );
      })}
    </div>
  );
}
