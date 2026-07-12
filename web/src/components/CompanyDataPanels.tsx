import type { ReactNode } from "react";
import {
  BarChart3,
  CalendarDays,
  CircleDollarSign,
  Landmark,
  Rocket,
  Scale,
  Target,
  Users,
} from "lucide-react";
import { cn, daysUntil, fmtCount, fmtDate, fmtUsdCompact } from "../lib/format";
import type { CalendarRow, EstimateRow, HoldingRow } from "../types-thesis";
import { Badge, Card, SectionHeader } from "./ui";

/** Shared empty state — most names have no rows yet (collection in flight). */
function EmptyNote() {
  return <div className="px-4 py-8 text-center text-xs text-brand-500">暂无数据 · 采集中</div>;
}

function CountBadge({ n }: { n: number }) {
  return (
    <Badge className="bg-surface-2 text-brand-500 ring-1 ring-inset ring-line">
      <span className="tnum">{n}</span>
    </Badge>
  );
}

// ===========================================================================
// Analyst estimates 分析师预期
// ===========================================================================

/** Big currency-like values compact ($4.2B); per-share style values 2dp. */
function fmtEstimateValue(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  if (Math.abs(v) >= 1e6) return fmtUsdCompact(v);
  return v.toLocaleString("en-US", { maximumFractionDigits: 2 });
}

export function EstimatesPanel({ rows }: { rows: EstimateRow[] }) {
  return (
    <Card>
      <SectionHeader
        title="Analyst Estimates"
        titleCn="分析师预期"
        icon={<Target size={15} strokeWidth={2} />}
        right={<CountBadge n={rows.length} />}
      />
      {rows.length === 0 ? (
        <EmptyNote />
      ) : (
        <table className="w-full text-xs">
          <thead>
            <tr className="text-2xs uppercase tracking-wide text-brand-500">
              <th className="py-2 pl-4 text-left font-medium">Metric</th>
              <th className="px-2 py-2 text-left font-medium">Period</th>
              <th className="px-2 py-2 text-right font-medium">Value</th>
              <th className="py-2 pr-4 text-right font-medium" title="Number of analysts">
                N
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr
                key={`${r.metric}-${r.period}-${i}`}
                className="border-t border-line/70"
                title={`区间 ${fmtEstimateValue(r.low)} – ${fmtEstimateValue(r.high)} · as of ${r.as_of}`}
              >
                <td className="max-w-0 truncate py-1.5 pl-4 text-brand-700">{r.metric}</td>
                <td className="tnum whitespace-nowrap px-2 py-1.5 text-brand-500">{r.period}</td>
                <td className="tnum whitespace-nowrap px-2 py-1.5 text-right font-semibold text-brand-900">
                  {fmtEstimateValue(r.value)}
                </td>
                <td className="tnum whitespace-nowrap py-1.5 pr-4 text-right text-brand-500">
                  {r.n_analysts ?? "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Card>
  );
}

// ===========================================================================
// Institutional ownership 机构持仓
// ===========================================================================

export function HoldingsPanel({ rows }: { rows: HoldingRow[] }) {
  return (
    <Card>
      <SectionHeader
        title="Ownership"
        titleCn="机构持仓"
        icon={<Landmark size={15} strokeWidth={2} />}
        right={<CountBadge n={rows.length} />}
      />
      {rows.length === 0 ? (
        <EmptyNote />
      ) : (
        <table className="w-full text-xs">
          <thead>
            <tr className="text-2xs uppercase tracking-wide text-brand-500">
              <th className="py-2 pl-4 text-left font-medium">Holder</th>
              <th className="px-2 py-2 text-right font-medium">Shares</th>
              <th className="py-2 pr-4 text-right font-medium">Value</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={`${r.holder}-${i}`} className="border-t border-line/70" title={`as of ${r.as_of}`}>
                <td className="max-w-0 truncate py-1.5 pl-4 font-medium text-brand-700">{r.holder}</td>
                <td className="tnum whitespace-nowrap px-2 py-1.5 text-right text-brand-500">
                  {fmtCount(r.shares)}
                </td>
                <td className="tnum whitespace-nowrap py-1.5 pr-4 text-right font-semibold text-brand-900">
                  {fmtUsdCompact(r.value_usd)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Card>
  );
}

// ===========================================================================
// Forward calendar 前瞻日历
// ===========================================================================

function calendarIcon(eventType: string): ReactNode {
  const t = (eventType || "").toLowerCase();
  if (/earn|result|report|guidance/.test(t)) return <BarChart3 size={13} strokeWidth={2} />;
  if (/product|launch|ramp|ship/.test(t)) return <Rocket size={13} strokeWidth={2} />;
  if (/investor|conference|agm|shareholder|analyst/.test(t)) return <Users size={13} strokeWidth={2} />;
  if (/dividend|buyback|split|offering/.test(t)) return <CircleDollarSign size={13} strokeWidth={2} />;
  if (/regulat|court|litig|ruling/.test(t)) return <Scale size={13} strokeWidth={2} />;
  return <CalendarDays size={13} strokeWidth={2} />;
}

function statusChipClass(status: string): string {
  const s = status.toLowerCase();
  if (/confirm|final|done|reported/.test(s)) return "bg-pos-50 text-pos-700 ring-1 ring-inset ring-pos/20";
  if (/tentative|estimated|expected|tbd/.test(s)) return "bg-warn-50 text-warn-700 ring-1 ring-inset ring-warn/20";
  return "bg-surface-2 text-brand-500 ring-1 ring-inset ring-line";
}

export function CalendarPanel({ rows }: { rows: CalendarRow[] }) {
  return (
    <Card>
      <SectionHeader
        title="Forward Calendar"
        titleCn="前瞻日历"
        icon={<CalendarDays size={15} strokeWidth={2} />}
        right={<CountBadge n={rows.length} />}
      />
      {rows.length === 0 ? (
        <EmptyNote />
      ) : (
        <ul className="flex flex-col px-4 py-1.5">
          {rows.map((r, i) => {
            const d = daysUntil(r.event_date);
            return (
              <li
                key={`${r.event_date}-${i}`}
                className="flex items-center gap-2.5 border-b border-line/70 py-2 last:border-b-0"
              >
                <span className="shrink-0 text-brand-500" title={r.event_type}>
                  {calendarIcon(r.event_type)}
                </span>
                <span className="tnum w-14 shrink-0 text-2xs text-brand-500">
                  {fmtDate(r.event_date)}
                </span>
                <span className="min-w-0 truncate text-xs font-medium text-brand-700" title={r.title}>
                  {r.title}
                </span>
                <span className="ml-auto flex shrink-0 items-center gap-1.5">
                  {r.status && <Badge className={statusChipClass(r.status)}>{r.status}</Badge>}
                  <span
                    className={cn(
                      "tnum text-2xs",
                      d < 0 ? "text-brand-200" : d <= 7 ? "font-semibold text-accent" : "text-brand-500",
                    )}
                  >
                    {d === 0 ? "today" : d > 0 ? `in ${d}d` : `${-d}d ago`}
                  </span>
                </span>
              </li>
            );
          })}
        </ul>
      )}
    </Card>
  );
}
