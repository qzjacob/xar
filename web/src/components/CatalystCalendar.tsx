import { CalendarDays } from "lucide-react";
import { catalystLabel, type Catalyst } from "../types";
import { cn, daysUntil, fmtDate, fmtWeekday, polarityChip } from "../lib/format";
import { Badge, Card, SectionHeader } from "./ui";

/**
 * Forward catalyst calendar: the filings, prints, and events that can re-rate
 * the chain over the next weeks. Optionally scoped to a single segment, sorted
 * by date and grouped into ISO weeks so the read is calendar-native.
 */
export function CatalystCalendar({
  catalysts,
  selectedSegmentId,
}: {
  catalysts: Catalyst[];
  selectedSegmentId: string | null;
}) {
  const filtered = (
    selectedSegmentId
      ? catalysts.filter((c) => c.segmentId === selectedSegmentId)
      : catalysts
  )
    .slice()
    .sort((a, b) => a.date.localeCompare(b.date));

  const groups = groupByWeek(filtered);

  return (
    <Card className="flex flex-col">
      <SectionHeader
        title="Catalyst Calendar"
        titleCn="催化剂日历"
        icon={<CalendarDays size={15} strokeWidth={2} />}
        right={
          <Badge className="bg-surface-2 text-brand-500 ring-1 ring-inset ring-line">
            {filtered.length} {filtered.length === 1 ? "event" : "events"}
          </Badge>
        }
      />

      {filtered.length === 0 ? (
        <div className="px-4 py-6 text-center text-sm text-brand-500">No matching catalysts</div>
      ) : (
        <div className="scroll-thin max-h-[460px] overflow-y-auto">
          {groups.map((g) => (
            <div key={g.weekStart}>
              {/* week header */}
              <div className="sticky top-0 z-10 flex items-center gap-2 border-b border-line bg-surface/95 px-4 py-1.5 backdrop-blur">
                <span className="text-2xs font-medium uppercase tracking-wide text-brand-500">
                  Week of {fmtDate(g.weekStart)}
                </span>
                <span className="h-px flex-1 bg-line" />
                <span className="text-2xs tnum text-brand-700">
                  {g.items.length}
                </span>
              </div>

              {/* rows */}
              {g.items.map((c) => (
                <CatalystRow key={c.id} c={c} />
              ))}
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}

function CatalystRow({ c }: { c: Catalyst }) {
  const d = daysUntil(c.date);
  const countdown = d < 0 ? "past" : d === 0 ? "today" : `in ${d}d`;
  const label = catalystLabel(c.type);

  return (
    <div className="flex items-start gap-3 border-b border-line px-4 py-2.5 transition-colors last:border-b-0 hover:bg-canvas">
      {/* date block */}
      <div className="w-10 shrink-0 text-center leading-tight">
        <div className="text-2xs uppercase tracking-wide text-brand-500">{fmtWeekday(c.date)}</div>
        <div className="tnum text-xs font-semibold text-brand-900">{fmtDate(c.date)}</div>
      </div>

      {/* importance dots */}
      <div className="flex w-3 shrink-0 flex-col items-center gap-0.5 pt-1" title={`Importance ${c.importance}/3`}>
        {[3, 2, 1].map((lvl) => {
          const on = c.importance >= lvl;
          return (
            <span
              key={lvl}
              className={cn(
                "h-1 w-1 rounded-full",
                !on
                  ? "bg-line"
                  : c.importance === 3
                    ? "bg-accent"
                    : "bg-warn",
              )}
            />
          );
        })}
      </div>

      {/* body */}
      <div className="flex min-w-0 flex-1 flex-col gap-1">
        <div className="flex flex-wrap items-center gap-1.5">
          <Badge className={polarityChip(c.polarity)} title={label.cn}>
            {label.en}
          </Badge>
          {c.ticker && (
            <Badge className="tnum bg-brand-50 text-brand-700 ring-1 ring-inset ring-brand-100">
              {c.ticker}
            </Badge>
          )}
        </div>
        <span className="text-sm leading-snug text-brand-900">{c.title}</span>
      </div>

      {/* countdown */}
      <div
        className={cn(
          "tnum w-12 shrink-0 pt-0.5 text-right text-2xs font-semibold",
          d < 0 ? "text-brand-700" : d === 0 ? "text-accent" : "text-brand-500",
        )}
      >
        {countdown}
      </div>
    </div>
  );
}

interface WeekGroup {
  weekStart: string; // ISO date (YYYY-MM-DD) of the Monday
  items: Catalyst[];
}

/** Bucket date-sorted catalysts into ISO weeks keyed by their Monday. */
function groupByWeek(items: Catalyst[]): WeekGroup[] {
  const out: WeekGroup[] = [];
  let current: WeekGroup | null = null;
  for (const c of items) {
    const ws = mondayOf(c.date);
    if (!current || current.weekStart !== ws) {
      current = { weekStart: ws, items: [] };
      out.push(current);
    }
    current.items.push(c);
  }
  return out;
}

/** ISO date (YYYY-MM-DD) of the Monday that starts the week containing `iso`. */
function mondayOf(iso: string): string {
  const d = new Date(iso + "T00:00:00");
  const dow = d.getDay(); // 0 = Sun .. 6 = Sat
  const back = (dow + 6) % 7; // days since Monday
  d.setDate(d.getDate() - back);
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}
