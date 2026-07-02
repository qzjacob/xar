import { useEffect, useState, type ReactNode } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  AlertTriangle,
  ArrowLeft,
  Boxes,
  ChevronRight,
  GitBranch,
  LineChart,
  Network,
  PieChart,
  Truck,
  Users,
} from "lucide-react";
import { useData } from "../context";
import { api } from "../lib/api";
import {
  cn,
  fmtDate,
  fmtMktCap,
  fmtPct,
  fmtSigned,
  heat,
  marketLabel,
  signClass,
} from "../lib/format";
import type { CompanyDetail, SupplyEdge } from "../types";
import { Badge, Card, DeltaTag, MetricPill, SectionHeader, Sparkline } from "../components/ui";
import { SignalFeed } from "../components/SignalFeed";

/** Company detail — name-level drilldown: price, fundamentals, KG supply chain, signals. */
export function CompanyPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { overview, market, theme } = useData();

  const [detail, setDetail] = useState<CompanyDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [notFound, setNotFound] = useState(false);

  useEffect(() => {
    if (!id) return;
    let on = true;
    setLoading(true);
    setNotFound(false);
    setDetail(null);
    api
      .getCompany(id, theme)
      .then((d) => {
        if (!on) return;
        setDetail(d);
        setLoading(false);
      })
      .catch(() => {
        if (!on) return;
        setNotFound(true);
        setLoading(false);
      });
    return () => {
      on = false;
    };
  }, [id, theme]);

  // --- loading -------------------------------------------------------------
  if (loading) {
    return (
      <div className="flex h-[60vh] items-center justify-center text-sm text-slate-400">
        Loading…
      </div>
    );
  }

  // --- 404 / null after load ----------------------------------------------
  if (notFound || !detail) {
    return (
      <div className="mx-auto flex max-w-[1200px] flex-col gap-5">
        <Card className="flex flex-col items-center gap-3 px-6 py-16 text-center">
          <span className="flex h-12 w-12 items-center justify-center rounded-full bg-neg-50 text-neg">
            <AlertTriangle size={22} strokeWidth={2} />
          </span>
          <div className="text-base font-semibold text-brand-900">Company not found</div>
          <div className="max-w-sm text-sm text-slate-500">
            No coverage record for{" "}
            <span className="tnum font-medium text-slate-400">{id}</span>. It may have been
            de-listed or never tracked in the XAR basket.
          </div>
          <Link
            to="/"
            className="mt-1 inline-flex items-center gap-1.5 rounded-lg border border-line bg-canvas px-3 py-1.5 text-sm font-medium text-brand-900 transition hover:bg-surface-2"
          >
            <ArrowLeft size={14} strokeWidth={2.5} /> Back to dashboard
          </Link>
        </Card>
      </div>
    );
  }

  const { company, segment, prices, fundamentals, signals, supplyChain } = detail;
  const segments = overview?.segments ?? [];
  const closes = prices.map((p) => p.close);
  const first = prices[0];
  const last = prices[prices.length - 1];
  const rangePct =
    first && last && first.close ? ((last.close - first.close) / first.close) * 100 : 0;

  return (
    <div className="mx-auto flex max-w-[1200px] flex-col gap-5">
      {/* ============================ HEADER ============================ */}
      <Card className="p-5">
        {/* breadcrumb + back */}
        <div className="flex items-center gap-2 text-2xs text-slate-400">
          <button
            type="button"
            onClick={() => navigate(-1)}
            aria-label="Go back"
            className="flex h-6 w-6 items-center justify-center rounded-md border border-line text-slate-500 transition hover:bg-canvas hover:text-brand-900"
          >
            <ArrowLeft size={14} strokeWidth={2.5} />
          </button>
          <Link to="/" className="transition hover:text-brand-900">
            Dashboard
          </Link>
          <ChevronRight size={12} className="text-slate-300" aria-hidden="true" />
          <button
            type="button"
            onClick={() => navigate(`/genny/segment/${segment.id}`)}
            className="truncate transition hover:text-brand-900"
          >
            {segment.name}
          </button>
        </div>

        <div className="mt-3 flex flex-col gap-5 lg:flex-row lg:items-start lg:justify-between">
          {/* identity */}
          <div className="min-w-0">
            <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
              <span className="tnum text-3xl font-semibold tracking-tight text-brand-900">
                {company.ticker}
              </span>
              <span className="truncate text-lg font-medium text-slate-300">{company.name}</span>
              {company.nameCn && (
                <span className="truncate text-sm text-slate-400">{company.nameCn}</span>
              )}
            </div>
            <div className="mt-2.5 flex flex-wrap items-center gap-1.5">
              <Badge className="bg-brand-50 text-brand-900 ring-1 ring-inset ring-brand-100">
                {marketLabel(company.market)}
              </Badge>
              <button
                type="button"
                onClick={() => navigate(`/genny/segment/${segment.id}`)}
                aria-label={`Open ${segment.name} segment`}
                className="rounded-md outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
              >
                <Badge className="bg-accent-50 text-accent-700 ring-1 ring-inset ring-accent/20 transition hover:bg-accent-100">
                  <Network size={11} strokeWidth={2.5} />
                  {segment.name}
                  <ChevronRight size={11} aria-hidden="true" />
                </Badge>
              </button>
              <span className="text-2xs text-slate-400">·</span>
              <span className="text-2xs font-medium uppercase tracking-wide text-slate-400">
                {company.role}
              </span>
            </div>
          </div>

          {/* price delta + sparkline */}
          <div className="flex shrink-0 items-center gap-4 lg:flex-col lg:items-end">
            <DeltaTag value={company.priceChange} size={18} className="text-2xl" />
            {closes.length >= 2 && (
              <Sparkline data={closes} width={240} height={48} className="w-[200px] sm:w-[240px]" />
            )}
          </div>
        </div>

        {/* ----------------------- METRIC STRIP ----------------------- */}
        <div className="mt-5 grid grid-cols-2 gap-2.5 border-t border-line pt-4 sm:grid-cols-3 lg:grid-cols-6">
          <MetricPill label="Market Cap" value={fmtMktCap(company.marketCap)} />
          <MetricPill
            label="Rev YoY"
            value={<span className={signClass(company.revGrowth)}>{fmtPct(company.revGrowth, 0)}</span>}
          />
          <MetricPill label="Gross Margin" value={`${company.grossMargin}%`} />
          <MetricPill
            label="Est Rev"
            value={
              <span style={{ color: heat(company.estRevision, "divergent", 1).color }}>
                {fmtSigned(company.estRevision)}
              </span>
            }
          />
          <MetricPill
            label="Conviction"
            value={
              <span className="flex items-baseline gap-0.5">
                {company.conviction}
                <span className="text-2xs font-normal text-slate-400">/5</span>
              </span>
            }
          />
          <div
            className="rounded-lg border border-line px-2.5 py-1.5"
            style={heat(clampMomentum(company.priceChange), "divergent", 0.16)}
          >
            <div className="text-2xs uppercase tracking-wide opacity-70">Momentum</div>
            <div className="tnum text-sm font-semibold leading-tight">
              {fmtSigned(company.priceChange, 1)}
            </div>
          </div>
        </div>
      </Card>

      {/* ============================ BODY GRID ============================ */}
      <div className="grid grid-cols-1 gap-5 xl:grid-cols-2">
        {/* ---------- LEFT: price + fundamentals ---------- */}
        <div className="flex min-w-0 flex-col gap-5">
          {/* PRICE */}
          <Card>
            <SectionHeader
              title="Price"
              titleCn="价格走势"
              icon={<LineChart size={15} strokeWidth={2} />}
              right={
                first && last ? (
                  <span className="flex items-center gap-2 text-2xs text-slate-400">
                    <span className="tnum">
                      {fmtDate(first.d)} → {fmtDate(last.d)}
                    </span>
                    <span
                      className={cn("tnum font-semibold", signClass(rangePct))}
                    >
                      {fmtPct(rangePct)}
                    </span>
                  </span>
                ) : undefined
              }
            />
            <div className="px-4 py-4">
              {closes.length >= 2 ? (
                <Sparkline data={closes} width={640} height={90} className="h-[90px] w-full" />
              ) : (
                <div className="py-10 text-center text-sm text-slate-400">No price history.</div>
              )}
              {first && last && (
                <div className="mt-3 flex items-center justify-between text-2xs text-slate-400">
                  <span className="tnum">
                    {fmtDate(first.d)} · {first.close.toFixed(2)}
                  </span>
                  <span className="tnum">
                    {fmtDate(last.d)} · {last.close.toFixed(2)}
                  </span>
                </div>
              )}
            </div>
          </Card>

          {/* FUNDAMENTALS */}
          <Card>
            <SectionHeader
              title="Fundamentals"
              titleCn="基本面"
              icon={<PieChart size={15} strokeWidth={2} />}
              right={
                <Badge className="bg-surface-2 text-slate-400 ring-1 ring-inset ring-line">
                  {fundamentals.length}
                </Badge>
              }
            />
            {fundamentals.length === 0 ? (
              <div className="px-4 py-10 text-center text-sm text-slate-400">
                No fundamentals available.
              </div>
            ) : (
              <div className="grid grid-cols-1 gap-x-6 px-4 py-2 sm:grid-cols-2">
                {fundamentals.slice(0, 12).map((row) => (
                  <div
                    key={row.metric}
                    className="flex items-center justify-between gap-2 border-b border-line/70 py-2 last:border-b-0"
                  >
                    <span className="truncate text-xs text-slate-500">
                      {humanizeMetric(row.metric)}
                    </span>
                    <span className="tnum shrink-0 text-sm font-semibold text-brand-900">
                      {fmtFundamental(row.value, row.unit)}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </Card>
        </div>

        {/* ---------- RIGHT: supply chain + signals ---------- */}
        <div className="flex min-w-0 flex-col gap-5">
          {/* SUPPLY CHAIN */}
          <Card>
            <SectionHeader
              title="Supply Chain"
              titleCn="供应链图谱"
              icon={<Network size={15} strokeWidth={2} />}
              right={
                <span className="flex items-center gap-1 text-2xs uppercase tracking-wide text-slate-400">
                  <GitBranch size={11} strokeWidth={2.5} /> KG edges
                </span>
              }
            />
            <div className="flex flex-col gap-4 px-4 py-4">
              <EdgeGroup
                label="Suppliers"
                cn="上游供应商"
                icon={<Truck size={13} strokeWidth={2} />}
                edges={supplyChain.suppliers}
                tone="bg-accent-50 text-accent-700 ring-1 ring-inset ring-accent/20"
              />
              <EdgeGroup
                label="Customers"
                cn="下游客户"
                icon={<Users size={13} strokeWidth={2} />}
                edges={supplyChain.customers}
                tone="bg-pos-50 text-pos-700 ring-1 ring-inset ring-pos/20"
              />
              <EdgeGroup
                label="Tech Routes"
                cn="技术路线"
                icon={<GitBranch size={13} strokeWidth={2} />}
                edges={supplyChain.tech_routes}
                tone="bg-surface-2 text-slate-400 ring-1 ring-inset ring-line"
              />
              <EdgeGroup
                label="Strategic Stakes"
                cn="战略投资"
                icon={<Boxes size={13} strokeWidth={2} />}
                edges={supplyChain.invests_in}
                tone="bg-brand-50 text-brand-900 ring-1 ring-inset ring-brand-100"
              />
              <SingleSourceRisks risks={supplyChain.single_source_risks} />
            </div>
          </Card>

          {/* SIGNALS */}
          <SignalFeed
            signals={signals}
            segments={segments}
            selectedSegmentId={null}
            onCompany={(cid) => navigate(`/genny/company/${cid}`)}
          />
        </div>
      </div>
    </div>
  );
}

// ===========================================================================
// Supply-chain sub-components
// ===========================================================================

function EdgeGroup({
  label,
  cn: cnLabel,
  icon,
  edges,
  tone,
}: {
  label: string;
  cn: string;
  icon: ReactNode;
  edges: SupplyEdge[];
  tone: string;
}) {
  return (
    <div>
      <div className="mb-1.5 flex items-center gap-1.5">
        <span className="text-slate-400">{icon}</span>
        <span className="text-2xs font-medium uppercase tracking-wide text-slate-500">{label}</span>
        <span className="text-2xs text-slate-400">{cnLabel}</span>
        <span className="tnum ml-auto text-2xs text-slate-400">{edges.length}</span>
      </div>
      {edges.length === 0 ? (
        <div className="rounded-lg border border-dashed border-line px-2.5 py-2 text-2xs text-slate-400">
          None mapped.
        </div>
      ) : (
        <ul className="flex flex-col gap-1">
          {edges.map((e) => (
            <li
              key={e.id}
              className="flex items-center gap-2 rounded-lg border border-line bg-canvas px-2.5 py-1.5"
            >
              <span className="truncate text-sm font-medium text-brand-900">{e.name}</span>
              <Badge className={cn("ml-auto shrink-0", tone)}>{humanizeRel(e.rel)}</Badge>
              <span
                className="tnum shrink-0 text-2xs font-semibold"
                style={{ color: heat(e.confidence * 100, "good-high", 1).color }}
                title="Edge confidence"
              >
                {Math.round(e.confidence * 100)}%
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function SingleSourceRisks({
  risks,
}: {
  risks: { src: string | null; dst: string | null }[];
}) {
  const valid = risks.filter((r) => r.src || r.dst);
  return (
    <div>
      <div className="mb-1.5 flex items-center gap-1.5">
        <span className="text-warn">
          <AlertTriangle size={13} strokeWidth={2} />
        </span>
        <span className="text-2xs font-medium uppercase tracking-wide text-slate-500">
          Single-Source Risks
        </span>
        <span className="text-2xs text-slate-400">单一来源风险</span>
        <span className="tnum ml-auto text-2xs text-slate-400">{valid.length}</span>
      </div>
      {valid.length === 0 ? (
        <div className="rounded-lg border border-dashed border-line px-2.5 py-2 text-2xs text-slate-400">
          No single-source dependencies flagged.
        </div>
      ) : (
        <ul className="flex flex-col gap-1">
          {valid.map((r, i) => (
            <li
              key={i}
              className="flex items-center gap-2 rounded-lg bg-warn-50 px-2.5 py-1.5 ring-1 ring-inset ring-warn/20"
            >
              <AlertTriangle size={12} className="shrink-0 text-warn" strokeWidth={2.5} />
              <span className="truncate text-sm font-medium text-warn-700">{r.src ?? "—"}</span>
              <ChevronRight size={12} className="shrink-0 text-warn/60" aria-hidden="true" />
              <span className="truncate text-sm font-medium text-warn-700">{r.dst ?? "—"}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// ===========================================================================
// Formatting helpers
// ===========================================================================

/** snake_case / camelCase metric key -> Title Case ("gross_margin" -> "Gross Margin"). */
function humanizeMetric(key: string): string {
  return key
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    .split(/[_\s]+/)
    .filter(Boolean)
    .map((w) => (KNOWN_ACRONYMS.has(w.toLowerCase()) ? w.toUpperCase() : w.charAt(0).toUpperCase() + w.slice(1)))
    .join(" ");
}

const KNOWN_ACRONYMS = new Set(["roe", "roa", "roic", "eps", "fcf", "ebit", "ebitda", "pe", "ps", "pb", "yoy", "ttm", "capex"]);

/** Relationship code -> readable chip label ("invests_in" -> "Invests In"). */
function humanizeRel(rel: string): string {
  return rel
    .split(/[_\s]+/)
    .filter(Boolean)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
}

/** Format a fundamental value by unit: ratio -> %, USD -> compact $, else raw. */
function fmtFundamental(value: number, unit: string): string {
  if (unit === "ratio") return `${(value * 100).toFixed(1)}%`;
  if (unit === "USD") return fmtUsd(value);
  if (unit === "pct" || unit === "%") return `${value.toFixed(1)}%`;
  return formatPlain(value);
}

/** Compact $ magnitude: 1.2B / 340M / 5.4K / 87. */
function fmtUsd(v: number): string {
  const n = Math.abs(v);
  const sign = v < 0 ? "-" : "";
  if (n >= 1e12) return `${sign}$${(n / 1e12).toFixed(1)}T`;
  if (n >= 1e9) return `${sign}$${(n / 1e9).toFixed(1)}B`;
  if (n >= 1e6) return `${sign}$${(n / 1e6).toFixed(0)}M`;
  if (n >= 1e3) return `${sign}$${(n / 1e3).toFixed(0)}K`;
  return `${sign}$${n.toFixed(0)}`;
}

function formatPlain(v: number): string {
  if (Number.isInteger(v)) return v.toLocaleString("en-US");
  return v.toLocaleString("en-US", { maximumFractionDigits: 2 });
}

/** Scale a price-change % into the -100..100 band the divergent heat expects. */
function clampMomentum(pct: number): number {
  return Math.max(-100, Math.min(100, pct * 5));
}
