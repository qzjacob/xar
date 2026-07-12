import { Activity } from "lucide-react";
import { REGIME_LABEL, type Regime, type Segment } from "../types";
import {
  cn,
  fmtScore,
  fmtSigned,
  heat,
  type HeatScheme,
  polarityChip,
  regimeChip,
} from "../lib/format";
import { Badge, Card, ScoreBar } from "./ui";

/**
 * Top-of-dashboard regime banner: at a glance — what cycle phase the AI-Capex
 * chain is in, the composite score + breadth, the key drivers, and a pulse of
 * the highest-momentum segments.
 */
export function RegimeSummaryCard({ regime, segments }: { regime: Regime; segments: Segment[] }) {
  const pulse = [...segments].sort((a, b) => b.momentum - a.momentum).slice(0, 4);

  return (
    <Card className="p-5">
      <div className="flex flex-col gap-5 lg:flex-row lg:items-stretch">
        {/* regime label + drivers */}
        <div className="flex min-w-0 flex-1 flex-col justify-between gap-4">
          <div>
            <div className="flex items-center gap-1.5 text-2xs font-medium uppercase tracking-wide text-brand-500">
              <Activity size={13} /> Chain Regime · 产业链景气
            </div>
            <div className="mt-2 flex flex-wrap items-baseline gap-x-3 gap-y-1">
              <span className="text-2xl font-semibold tracking-tight text-brand-900">
                {regime.label}
              </span>
              <Badge className={regimeChip(regime.phase)}>{REGIME_LABEL[regime.phase].en}</Badge>
            </div>
            <div className="mt-1 text-sm text-brand-200">
              {regime.labelCn} · {REGIME_LABEL[regime.phase].cn}
            </div>
          </div>
          <div className="flex flex-wrap gap-1.5">
            {regime.drivers.map((d, i) => (
              <Badge key={i} className={polarityChip(d.polarity)}>
                <span
                  className={cn(
                    "h-1.5 w-1.5 rounded-full",
                    d.polarity === "positive"
                      ? "bg-pos"
                      : d.polarity === "negative"
                        ? "bg-neg"
                        : "bg-brand-200",
                  )}
                />
                {d.label}
              </Badge>
            ))}
          </div>
        </div>

        {/* composite + breadth */}
        <div className="grid grid-cols-2 gap-4 lg:w-64 lg:border-l lg:border-line lg:pl-5">
          <Stat label="Composite" value={fmtScore(regime.score)} raw={regime.score} trend={regime.trend} accent />
          <Stat label="Breadth" value={`${fmtScore(regime.breadth)}%`} raw={regime.breadth} />
        </div>

        {/* chain pulse */}
        <div className="lg:w-64 lg:border-l lg:border-line lg:pl-5">
          <div className="text-2xs font-medium uppercase tracking-wide text-brand-500">
            Chain Pulse · 环节动能
          </div>
          <div className="mt-2.5 flex flex-col gap-1.5">
            {pulse.map((s) => (
              <div key={s.id} className="flex items-center gap-2">
                <span className="w-24 shrink-0 truncate text-xs text-brand-500">{s.name}</span>
                <ScoreBar value={s.momentum} scheme="divergent" className="flex-1" />
                <span
                  className="tnum w-8 shrink-0 text-right text-2xs font-semibold"
                  style={{ color: heat(s.momentum, "divergent", 1).color }}
                >
                  {fmtSigned(s.momentum)}
                </span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </Card>
  );
}

function Stat({
  label,
  value,
  raw,
  trend,
  scheme = "good-high",
  accent = false,
}: {
  label: string;
  value: string;
  raw: number;
  trend?: number;
  scheme?: HeatScheme;
  accent?: boolean;
}) {
  return (
    <div>
      <div className="text-2xs uppercase tracking-wide text-brand-500">{label}</div>
      <div className="flex items-baseline gap-1.5">
        <span
          className={cn("tnum text-2xl font-semibold", accent ? "text-accent" : "text-brand-900")}
        >
          {value}
        </span>
        {trend != null && (
          <span className={cn("tnum text-xs font-medium", trend >= 0 ? "text-pos" : "text-neg")}>
            {fmtSigned(trend)}
          </span>
        )}
      </div>
      <ScoreBar value={raw} scheme={scheme} className="mt-2" />
    </div>
  );
}
