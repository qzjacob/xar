import { useEffect, useState } from "react";
import { Activity, ChevronRight } from "lucide-react";
import { Link } from "react-router-dom";
import { andy, todayISO } from "../lib/andy";
import { cn } from "../lib/format";
import { Card } from "./ui";
import { fmtMetric, hardnessMeta, slopeInfo } from "./andy/constants";
import type { LinkThemeResponse } from "../types-andy";

/**
 * Genny → Andy reverse hook: compact strip of the macro indicators 勾稽-linked to
 * the current theme. Each pill deep-links into the Andy interrogation page.
 * Renders NOTHING until the crosswalk API answers — zero layout jank if the
 * backend isn't up yet.
 */
export function MacroStrip({ theme, compact }: { theme: string; compact?: boolean }) {
  const [data, setData] = useState<LinkThemeResponse | null>(null);

  useEffect(() => {
    let on = true;
    setData(null);
    andy
      .linkTheme(theme, todayISO())
      .then((d) => on && setData(d))
      .catch(() => {
        /* silent — macro crosswalk is optional chrome */
      });
    return () => {
      on = false;
    };
  }, [theme]);

  if (!data || data.metrics.length === 0) return null;

  // top 3–5: metrics with an actual reading first, keep contract order otherwise
  const metrics = [...data.metrics]
    .sort((a, b) => Number(b.value !== null) - Number(a.value !== null))
    .slice(0, compact ? 4 : 5);

  return (
    <Card className={cn(compact ? "px-3 py-2" : "px-4 py-2.5")}>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
        <Link
          to="/andy"
          className="group flex shrink-0 items-center gap-1.5 text-2xs font-semibold uppercase tracking-wide text-brand-500 transition-colors hover:text-andy-500"
          title="XAR Andy · 宏观指标终端"
        >
          <Activity size={13} strokeWidth={2.25} className="text-andy-500" />
          Macro 宏观勾稽
          <ChevronRight size={12} strokeWidth={2.25} className="opacity-0 transition-opacity group-hover:opacity-100" />
        </Link>
        <div className="flex min-w-0 flex-1 flex-wrap items-center gap-1.5">
          {metrics.map((m) => {
            const s = slopeInfo(m.slope, m.good_when);
            return (
              <Link
                key={m.metric_key}
                to={`/andy/metrics/${encodeURIComponent(m.metric_key)}`}
                title={`${m.metric_key}${m.rationale_zh ? ` — ${m.rationale_zh}` : ""}`}
                className="inline-flex max-w-full items-center gap-1.5 rounded-md border border-line bg-canvas px-2 py-1 text-2xs transition-colors hover:border-andy/40 hover:bg-andy-50"
              >
                <span
                  className={cn("h-1.5 w-1.5 shrink-0 rounded-full", hardnessMeta(m.hardness).dot)}
                  aria-hidden="true"
                />
                <span className="truncate text-brand-800">{m.display_name_zh}</span>
                <span className="tnum shrink-0 font-semibold text-brand-900">
                  {fmtMetric(m.value)}
                  {m.value !== null && m.unit ? <span className="ml-0.5 font-normal text-brand-200">{m.unit}</span> : null}
                </span>
                <span className={cn("tnum shrink-0 font-semibold", s.cls)}>{s.arrow}</span>
              </Link>
            );
          })}
        </div>
      </div>
    </Card>
  );
}
