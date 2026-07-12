import { useMemo, useState } from "react";
import { ChevronDown, ChevronUp, ListOrdered } from "lucide-react";
import { REGIME_LABEL, type Segment } from "../types";
import { cn, fmtScore, fmtSigned, heat, regimeChip, regimeDot, signClass } from "../lib/format";
import { Badge, Card, DeltaTag, ScoreBar, SectionHeader } from "./ui";

type SortKey = "alpha" | "momentum" | "changeM" | "valuationPctile" | "crowding";
type SortDir = "asc" | "desc";

const SORT_LABEL: Record<SortKey, string> = {
  alpha: "Alpha",
  momentum: "Momentum",
  changeM: "Δ1M",
  valuationPctile: "Valuation",
  crowding: "Crowding",
};

/**
 * Client-side sortable ranking of chain segments by opportunity quality.
 * Selecting a row lifts the segment into the parent's focus context.
 */
export function SegmentRankingTable({
  segments,
  selectedSegmentId,
  onSelectSegment,
}: {
  segments: Segment[];
  selectedSegmentId: string | null;
  onSelectSegment: (id: string | null) => void;
}) {
  const [sortKey, setSortKey] = useState<SortKey>("alpha");
  const [sortDir, setSortDir] = useState<SortDir>("desc");

  const sorted = useMemo(() => {
    const rows = [...segments];
    rows.sort((a, b) => {
      const diff = a[sortKey] - b[sortKey];
      return sortDir === "asc" ? diff : -diff;
    });
    return rows;
  }, [segments, sortKey, sortDir]);

  function applySort(key: SortKey) {
    if (key === sortKey) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      // sensible default direction per metric
      setSortDir(key === "valuationPctile" || key === "crowding" ? "asc" : "desc");
    }
  }

  const hasSelection = selectedSegmentId != null;

  return (
    <Card>
      <SectionHeader
        title="Segment Opportunity Ranking"
        titleCn="环节机会排序"
        icon={<ListOrdered size={15} strokeWidth={2} />}
        right={
          <span className="hidden items-center gap-1 text-2xs uppercase tracking-wide text-brand-500 sm:flex">
            sorted by
            <span className="font-semibold text-brand-200">{SORT_LABEL[sortKey]}</span>
          </span>
        }
      />

      <div className="overflow-x-auto">
        <table className="w-full min-w-[680px] border-collapse text-sm">
          <thead>
            <tr className="border-b border-line text-2xs uppercase tracking-wide text-brand-500">
              <th className="w-8 px-3 py-2 text-right font-medium">#</th>
              <th className="px-3 py-2 text-left font-medium">Segment</th>
              <SortTh
                label="Alpha"
                col="alpha"
                sortKey={sortKey}
                sortDir={sortDir}
                onSort={applySort}
                className="w-[150px]"
              />
              <SortTh
                label="Mom"
                col="momentum"
                sortKey={sortKey}
                sortDir={sortDir}
                onSort={applySort}
              />
              <SortTh
                label="Δ1M"
                col="changeM"
                sortKey={sortKey}
                sortDir={sortDir}
                onSort={applySort}
              />
              <SortTh
                label="Val %ile"
                col="valuationPctile"
                sortKey={sortKey}
                sortDir={sortDir}
                onSort={applySort}
              />
              <SortTh
                label="Crowding"
                col="crowding"
                sortKey={sortKey}
                sortDir={sortDir}
                onSort={applySort}
              />
              <th className="w-12 px-3 py-2 text-right font-medium">#Cos</th>
            </tr>
          </thead>
          <tbody>
            {sorted.length === 0 && (
              <tr>
                <td colSpan={8} className="px-3 py-8 text-center text-sm text-brand-500">
                  No matches
                </td>
              </tr>
            )}
            {sorted.map((s, i) => {
              const selected = s.id === selectedSegmentId;
              const dimmed = hasSelection && !selected;
              return (
                <tr
                  key={s.id}
                  role="button"
                  tabIndex={0}
                  aria-pressed={selected}
                  onClick={() => onSelectSegment(selected ? null : s.id)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      onSelectSegment(selected ? null : s.id);
                    }
                  }}
                  className={cn(
                    "h-11 cursor-pointer border-b border-line/70 transition-colors focus-visible:outline-none",
                    selected ? "bg-accent/5 ring-1 ring-inset ring-accent/30" : "hover:bg-canvas",
                    dimmed && "opacity-55 hover:opacity-100",
                  )}
                >
                  {/* rank */}
                  <td className="px-3 text-right">
                    <span className="tnum text-xs font-semibold text-brand-500">{i + 1}</span>
                  </td>

                  {/* segment identity */}
                  <td className="px-3">
                    <div className="flex items-center gap-2">
                      <span
                        className={cn("h-2 w-2 shrink-0 rounded-full", regimeDot(s.regime))}
                        title={REGIME_LABEL[s.regime].en}
                      />
                      <div className="min-w-0">
                        <div className="flex items-center gap-1.5">
                          <span className="truncate text-sm font-medium text-brand-900">
                            {s.name}
                          </span>
                          <span className="truncate text-2xs text-brand-500">{s.nameCn}</span>
                        </div>
                      </div>
                      <Badge className={cn("ml-1 shrink-0", regimeChip(s.regime))}>
                        {REGIME_LABEL[s.regime].en}
                      </Badge>
                    </div>
                  </td>

                  {/* alpha */}
                  <td className="px-3">
                    <div className="flex items-center gap-2">
                      <ScoreBar value={s.alpha} scheme="good-high" className="flex-1" />
                      <span
                        className="tnum w-6 shrink-0 text-right text-xs font-semibold"
                        style={{ color: heat(s.alpha, "good-high", 1).color }}
                      >
                        {fmtScore(s.alpha)}
                      </span>
                    </div>
                  </td>

                  {/* momentum */}
                  <td className="px-3 text-right">
                    <span className={cn("tnum text-xs font-semibold", signClass(s.momentum))}>
                      {fmtSigned(s.momentum)}
                    </span>
                  </td>

                  {/* Δ1M */}
                  <td className="px-3 text-right">
                    <DeltaTag value={s.changeM} className="justify-end" />
                  </td>

                  {/* valuation percentile */}
                  <td className="px-3 text-right">
                    <HeatCell value={s.valuationPctile} scheme="good-low" />
                  </td>

                  {/* crowding */}
                  <td className="px-3 text-right">
                    <HeatCell value={s.crowding} scheme="good-low" />
                  </td>

                  {/* #companies */}
                  <td className="px-3 text-right">
                    <span className="tnum text-xs text-brand-200">{s.companies}</span>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

/** Sortable, right-aligned numeric header cell with an active-state caret. */
function SortTh({
  label,
  col,
  sortKey,
  sortDir,
  onSort,
  className,
}: {
  label: string;
  col: SortKey;
  sortKey: SortKey;
  sortDir: SortDir;
  onSort: (key: SortKey) => void;
  className?: string;
}) {
  const active = col === sortKey;
  return (
    <th className={cn("px-3 py-2 font-medium", className)}>
      <button
        type="button"
        onClick={() => onSort(col)}
        className={cn(
          "ml-auto flex w-full items-center justify-end gap-0.5 text-2xs uppercase tracking-wide transition-colors",
          active ? "text-accent" : "text-brand-500 hover:text-brand-500",
        )}
      >
        {label}
        {active ? (
          sortDir === "desc" ? (
            <ChevronDown size={13} strokeWidth={2.5} />
          ) : (
            <ChevronUp size={13} strokeWidth={2.5} />
          )
        ) : (
          <span className="w-[13px]" aria-hidden="true" />
        )}
      </button>
    </th>
  );
}

/** Subtle heat-tinted numeric pill for 0..100 percentile-style metrics. */
function HeatCell({ value, scheme }: { value: number; scheme: "good-low" | "good-high" }) {
  const h = heat(value, scheme, 0.14);
  return (
    <span
      className="tnum inline-block min-w-[30px] rounded px-1.5 py-0.5 text-right text-xs font-semibold"
      style={{ backgroundColor: h.backgroundColor, color: h.color }}
    >
      {fmtScore(value)}
    </span>
  );
}
