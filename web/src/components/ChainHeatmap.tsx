import { LayoutGrid } from "lucide-react";
import { REGIME_LABEL, type Segment } from "../types";
import {
  cn,
  fmtPct,
  fmtScore,
  heat,
  type HeatScheme,
  regimeDot,
  signClass,
} from "../lib/format";
import { Card, SectionHeader, Sparkline } from "./ui";

/**
 * Chain Heatmap — the centerpiece matrix. One row per chain segment (sorted
 * upstream -> downstream), with heat-colored metric tiles so the cycle reads at
 * a glance: greener = stronger, with valuation & crowding inverted (red = stretched).
 * Clicking a row drives cross-component selection.
 */
type MetricKey =
  | "momentum"
  | "earningsRevision"
  | "valuationPctile"
  | "crowding"
  | "supplyTightness"
  | "alpha";

const METRIC_COLS: { key: MetricKey; label: string; scheme: HeatScheme }[] = [
  { key: "momentum", label: "Momentum", scheme: "divergent" },
  { key: "earningsRevision", label: "Earn Rev", scheme: "divergent" },
  { key: "valuationPctile", label: "Val %ile", scheme: "good-low" },
  { key: "crowding", label: "Crowd", scheme: "good-low" },
  { key: "supplyTightness", label: "Supply", scheme: "good-high" },
  { key: "alpha", label: "Alpha", scheme: "good-high" },
];

export function ChainHeatmap({
  segments,
  selectedSegmentId,
  onSelectSegment,
}: {
  segments: Segment[];
  selectedSegmentId: string | null;
  onSelectSegment: (id: string | null) => void;
}) {
  const rows = [...segments].sort((a, b) => a.tier - b.tier);
  const hasSelection = selectedSegmentId != null;
  // Cycle themes reuse this matrix but order by cycle rank instead of chain tier.
  const isCycle = rows.some((s) => s.axis === "cycle" || s.cycle != null);
  const title = isCycle ? "Cycle Map" : "Chain Heatmap";
  const titleCn = isCycle ? "经济周期图" : "产业链热力图";
  const axisHint = isCycle
    ? "Early-cycle → late-cycle / defensive · greener = stronger; valuation & crowding inverted (red = stretched)"
    : "Upstream → downstream · greener = stronger; valuation & crowding inverted (red = stretched)";

  return (
    <Card>
      <SectionHeader
        title={title}
        titleCn={titleCn}
        icon={<LayoutGrid size={15} strokeWidth={2} />}
        right={
          <div className="flex items-center gap-1.5 text-2xs uppercase tracking-wide text-slate-400">
            <span>cold</span>
            <span
              className="h-2 w-16 rounded-full ring-1 ring-inset ring-line"
              style={{
                background:
                  "linear-gradient(90deg, rgb(220,38,38), rgb(217,119,6), rgb(22,163,74))",
              }}
            />
            <span>hot</span>
          </div>
        }
      />

      <div className="px-4 pt-2.5 text-2xs text-slate-400">{axisHint}</div>

      {isCycle && (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 px-4 pt-1 text-2xs text-slate-400">
          {[
            ["EC", "Early 早周期"],
            ["MC", "Mid 中周期"],
            ["LC", "Late 晚周期"],
            ["DEF", "Defensive 防御"],
            ["CC", "Counter 逆周期"],
          ].map(([code, lbl]) => (
            <span key={code} className="flex items-center gap-1">
              <span className="rounded bg-brand-50 px-1 font-semibold text-brand-900">
                {code}
              </span>
              {lbl}
            </span>
          ))}
        </div>
      )}

      <div className="scroll-thin overflow-x-auto px-2 pb-3 pt-1.5">
        <div className="min-w-[760px]">
          {/* column header row */}
          <div className="flex items-end gap-1 px-1 pb-1.5">
            <div className="flex-1 pl-1 text-2xs uppercase tracking-wide text-slate-400">
              Segment
            </div>
            {METRIC_COLS.map((c) => (
              <div
                key={c.key}
                className="w-[68px] shrink-0 text-center text-2xs uppercase tracking-wide text-slate-400"
              >
                {c.label}
              </div>
            ))}
            <div className="w-[92px] shrink-0 pr-1 text-right text-2xs uppercase tracking-wide text-slate-400">
              Trend
            </div>
          </div>

          {/* segment rows */}
          {rows.length === 0 ? (
            <div className="px-2 py-8 text-center text-sm text-slate-400">No matches</div>
          ) : (
            <div className="flex flex-col gap-1">
              {rows.map((s) => {
                const selected = s.id === selectedSegmentId;
                return (
                  <button
                    key={s.id}
                    type="button"
                    onClick={() => onSelectSegment(selected ? null : s.id)}
                    aria-pressed={selected}
                    className={cn(
                      "flex items-center gap-1 rounded-lg px-1 py-1 text-left transition",
                      "hover:bg-canvas focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/40",
                      selected
                        ? "bg-canvas ring-2 ring-accent/40 shadow-card"
                        : hasSelection
                          ? "opacity-70 hover:opacity-100"
                          : "",
                    )}
                  >
                    {/* label cell */}
                    <div className="flex min-w-0 flex-1 items-center gap-2 pl-1">
                      <span
                        className="tnum flex h-5 min-w-5 shrink-0 items-center justify-center rounded-md bg-brand-50 px-1 text-2xs font-semibold text-brand-900"
                        title={s.cycle ? `${s.cycle.label} · ${s.cycle.labelCn}` : undefined}
                      >
                        {s.cycle ? s.cycle.short : s.tier}
                      </span>
                      <span
                        className={cn("h-1.5 w-1.5 shrink-0 rounded-full", regimeDot(s.regime))}
                        title={REGIME_LABEL[s.regime].en}
                      />
                      <div className="min-w-0">
                        <div className="truncate text-sm font-semibold leading-tight text-brand-900">
                          {s.name}
                        </div>
                        <div className="truncate text-2xs leading-tight text-slate-400">
                          {s.nameCn}
                        </div>
                      </div>
                    </div>

                    {/* metric tiles */}
                    {METRIC_COLS.map((c) => {
                      const v = s[c.key];
                      return (
                        <div
                          key={c.key}
                          className="tnum w-[68px] shrink-0 rounded-md py-2 text-center text-sm font-semibold"
                          style={heat(v, c.scheme, 0.16)}
                        >
                          {fmtScore(v)}
                        </div>
                      );
                    })}

                    {/* trend cell */}
                    <div className="flex w-[92px] shrink-0 items-center justify-end gap-2 pr-1">
                      <Sparkline data={s.spark} width={48} height={22} />
                      <span
                        className={cn(
                          "tnum w-[40px] text-right text-2xs font-semibold",
                          signClass(s.changeM),
                        )}
                      >
                        {fmtPct(s.changeM)}
                      </span>
                    </div>
                  </button>
                );
              })}
            </div>
          )}
        </div>
      </div>
    </Card>
  );
}
