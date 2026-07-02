import { Building2, Star } from "lucide-react";
import {
  catalystLabel,
  type Company,
  type Market,
  type Segment,
} from "../types";
import {
  cn,
  fmtMktCap,
  fmtPct,
  fmtSigned,
  marketLabel,
  signClass,
} from "../lib/format";
import { Badge, Card, DeltaTag, SectionHeader, Sparkline } from "./ui";

/**
 * Company Watchlist — the name-level companion to the segment views: a dense,
 * sortable roster of covered companies (optionally scoped to the selected
 * chain segment) with the key fundamental + signal columns an analyst scans:
 * conviction, growth, margins, estimate revisions and recent catalyst tags.
 */
export function CompanyWatchlist({
  companies,
  segments,
  selectedSegmentId,
  market,
  onCompany,
}: {
  companies: Company[];
  segments: Segment[];
  selectedSegmentId: string | null;
  market: Market;
  /** Navigate to a company's detail page. */
  onCompany?: (id: string) => void;
}) {
  const segName = (id: string): string =>
    segments.find((s) => s.id === id)?.name ?? id;

  const rows = companies
    .filter((c) => !selectedSegmentId || c.segmentId === selectedSegmentId)
    .sort(
      (a, b) =>
        Number(b.watched) - Number(a.watched) ||
        b.conviction - a.conviction ||
        b.marketCap - a.marketCap,
    );

  const watchedCount = rows.filter((c) => c.watched).length;

  return (
    <Card>
      <SectionHeader
        title="Company Watchlist"
        titleCn="重点公司"
        icon={<Star size={15} strokeWidth={2} />}
        right={
          <>
            {market !== "ALL" && (
              <Badge className="bg-brand-50 text-brand-700 ring-1 ring-inset ring-brand-100">
                <Building2 size={11} strokeWidth={2} />
                {marketLabel(market)}
              </Badge>
            )}
            <Badge className="bg-surface-2 text-slate-400 ring-1 ring-inset ring-line">
              <Star size={11} strokeWidth={2} className="text-accent" />
              {watchedCount}/{rows.length}
            </Badge>
          </>
        }
      />

      <div className="overflow-x-auto scroll-thin">
        <table className="w-full min-w-[760px] border-collapse text-sm">
          <thead>
            <tr className="text-2xs uppercase tracking-wide text-slate-400">
              <Th className="w-7 pl-4" />
              <Th className="text-left">Company</Th>
              <Th className="text-left">Segment</Th>
              <Th className="text-center">Mkt</Th>
              <Th className="text-right">MktCap</Th>
              <Th className="text-right">&Delta;Price</Th>
              <Th className="text-right">Rev YoY</Th>
              <Th className="text-right">GM</Th>
              <Th className="text-right">Est Rev</Th>
              <Th className="text-center">Conv</Th>
              <Th className="text-left">Signals</Th>
              <Th className="pr-4 text-right">Trend</Th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 ? (
              <tr>
                <td
                  colSpan={12}
                  className="px-4 py-8 text-center text-sm text-slate-400"
                >
                  No matches{selectedSegmentId ? " in this segment" : ""}.
                </td>
              </tr>
            ) : (
              rows.map((c) => (
                <tr
                  key={c.id}
                  onClick={onCompany ? () => onCompany(c.id) : undefined}
                  onKeyDown={
                    onCompany
                      ? (e) => {
                          if (e.key === "Enter" || e.key === " ") {
                            e.preventDefault();
                            onCompany(c.id);
                          }
                        }
                      : undefined
                  }
                  role={onCompany ? "button" : undefined}
                  tabIndex={onCompany ? 0 : undefined}
                  aria-label={onCompany ? `Open ${c.ticker} ${c.name}` : undefined}
                  className={cn(
                    "border-b border-line/70 transition-colors last:border-0 hover:bg-canvas",
                    onCompany && "cursor-pointer focus-visible:bg-canvas",
                  )}
                >
                  {/* star */}
                  <td className="py-2 pl-4 align-middle">
                    <Star
                      size={13}
                      strokeWidth={2}
                      className={cn(
                        c.watched ? "text-accent" : "text-slate-300",
                      )}
                      fill={c.watched ? "currentColor" : "none"}
                      aria-label={c.watched ? "Watched" : "Not watched"}
                    />
                  </td>

                  {/* ticker + name */}
                  <td className="py-2 pr-3 align-middle">
                    <div className="tnum text-xs font-bold leading-tight text-brand-900">
                      {c.ticker}
                    </div>
                    <div className="flex items-baseline gap-1.5 leading-tight">
                      <span className="truncate text-xs text-slate-400">
                        {c.name}
                      </span>
                      {c.nameCn && (
                        <span className="truncate text-2xs text-slate-400">
                          {c.nameCn}
                        </span>
                      )}
                    </div>
                  </td>

                  {/* segment */}
                  <td className="py-2 pr-3 align-middle">
                    <Badge
                      className="bg-surface-2 text-slate-400 ring-1 ring-inset ring-line"
                      title={segName(c.segmentId)}
                    >
                      <span className="max-w-[7.5rem] truncate">
                        {segName(c.segmentId)}
                      </span>
                    </Badge>
                  </td>

                  {/* market */}
                  <td className="py-2 pr-3 text-center align-middle">
                    <Badge className="bg-surface-2 text-slate-400 ring-1 ring-inset ring-line">
                      {marketLabel(c.market)}
                    </Badge>
                  </td>

                  {/* market cap */}
                  <td className="tnum py-2 pr-3 text-right align-middle font-medium text-brand-900">
                    {fmtMktCap(c.marketCap)}
                  </td>

                  {/* price change */}
                  <td className="py-2 pr-3 text-right align-middle">
                    <DeltaTag value={c.priceChange} className="justify-end" />
                  </td>

                  {/* rev growth YoY */}
                  <td
                    className={cn(
                      "tnum py-2 pr-3 text-right align-middle font-semibold",
                      signClass(c.revGrowth),
                    )}
                  >
                    {fmtPct(c.revGrowth, 0)}
                  </td>

                  {/* gross margin */}
                  <td className="tnum py-2 pr-3 text-right align-middle text-slate-400">
                    {c.grossMargin.toFixed(0)}%
                  </td>

                  {/* est revision */}
                  <td
                    className={cn(
                      "tnum py-2 pr-3 text-right align-middle font-medium",
                      signClass(c.estRevision),
                    )}
                  >
                    {fmtSigned(c.estRevision)}
                  </td>

                  {/* conviction dots */}
                  <td className="py-2 pr-3 align-middle">
                    <span
                      className="flex items-center justify-center gap-0.5"
                      title={`Conviction ${c.conviction}/5`}
                      aria-label={`Conviction ${c.conviction} of 5`}
                    >
                      {[1, 2, 3, 4, 5].map((n) => (
                        <span
                          key={n}
                          className={cn(
                            "h-1.5 w-1.5 rounded-full",
                            n <= c.conviction ? "bg-accent" : "bg-line",
                          )}
                        />
                      ))}
                    </span>
                  </td>

                  {/* signals */}
                  <td className="py-2 pr-3 align-middle">
                    <div className="flex flex-wrap items-center gap-1">
                      {c.signals.slice(0, 3).map((t) => (
                        <Badge
                          key={t}
                          className="bg-surface-2 text-slate-400 ring-1 ring-inset ring-line"
                          title={catalystLabel(t).en}
                        >
                          {abbrevCatalyst(catalystLabel(t).en)}
                        </Badge>
                      ))}
                      {c.signals.length > 3 && (
                        <span
                          className="text-2xs font-medium text-slate-400"
                          title={c.signals
                            .slice(3)
                            .map((t) => catalystLabel(t).en)
                            .join(", ")}
                        >
                          +{c.signals.length - 3}
                        </span>
                      )}
                      {c.signals.length === 0 && (
                        <span className="text-2xs text-slate-300">&mdash;</span>
                      )}
                    </div>
                  </td>

                  {/* trend sparkline */}
                  <td className="py-2 pr-4 align-middle">
                    <div className="flex justify-end">
                      <Sparkline data={c.spark} width={64} height={20} />
                    </div>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

/** Header cell with shared padding + weight. */
function Th({
  children,
  className,
}: {
  children?: import("react").ReactNode;
  className?: string;
}) {
  return (
    <th
      className={cn(
        "whitespace-nowrap border-b border-line px-0 pr-3 pb-2 pt-2.5 font-medium",
        className,
      )}
      scope="col"
    >
      {children}
    </th>
  );
}

/** Compact a catalyst EN label so up to 3 fit the dense Signals cell. */
function abbrevCatalyst(label: string): string {
  const map: Record<string, string> = {
    "Capex Guidance": "Capex",
    "Order": "Order",
    "Qualification": "Qual",
    "Product Ramp": "Ramp",
    "Accelerator Launch": "Accel",
    "Capacity Expansion": "Capacity",
    "Supply Constraint": "Supply",
    "Earnings": "Earnings",
    "Equity Investment": "Equity",
    "Tech Substitution": "Tech Sub",
  };
  return map[label] ?? label;
}
