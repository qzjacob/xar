import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ArrowLeft, ChevronRight, Layers, TrendingUp } from "lucide-react";
import { useData } from "../context";
import { api } from "../lib/api";
import {
  cn,
  fmtPct,
  fmtScore,
  fmtSigned,
  heat,
  type HeatScheme,
  regimeChip,
  regimeDot,
} from "../lib/format";
import { REGIME_LABEL, type SegmentDetail } from "../types";
import { CompanyWatchlist } from "../components/CompanyWatchlist";
import { MacroStrip } from "../components/MacroStrip";
import { SignalFeed } from "../components/SignalFeed";
import { Badge, Card, SectionHeader, Sparkline } from "../components/ui";

/**
 * Segment detail page (route /segment/:id) — the chain-environment drill-down:
 * regime header + heat-tinted metric grid mirroring the Chain Heatmap, the
 * segment's member companies, and its live signal tape.
 */
export function SegmentPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { overview, market, theme } = useData();
  const [detail, setDetail] = useState<SegmentDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  useEffect(() => {
    if (!id) return;
    let on = true;
    setLoading(true);
    setError(false);
    setDetail(null);
    api
      .getSegment(id)
      .then((d) => {
        if (!on) return;
        setDetail(d);
        setLoading(false);
      })
      .catch(() => {
        if (!on) return;
        setError(true);
        setLoading(false);
      });
    return () => {
      on = false;
    };
  }, [id]);

  if (loading) {
    return (
      <div className="mx-auto flex min-h-[60vh] max-w-[1200px] items-center justify-center text-sm text-brand-500">
        Loading…
      </div>
    );
  }

  if (error || !detail) {
    return (
      <div className="mx-auto max-w-[1200px]">
        <Card className="flex flex-col items-center gap-3 px-6 py-16 text-center">
          <Layers size={28} strokeWidth={1.75} className="text-brand-700" />
          <div className="text-base font-semibold text-brand-900">Segment not found</div>
          <div className="max-w-sm text-sm text-brand-200">
            We couldn&apos;t load this chain segment. It may have been recategorized or is outside
            current coverage.
          </div>
          <Link
            to="/"
            className="mt-1 inline-flex items-center gap-1.5 rounded-lg border border-line bg-canvas px-3 py-1.5 text-sm font-medium text-brand-900 transition hover:bg-surface focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
          >
            <ArrowLeft size={15} strokeWidth={2} /> Back to dashboard
          </Link>
        </Card>
      </div>
    );
  }

  const { segment, companies, signals } = detail;
  const segments = overview?.segments ?? [];

  const tiles: { label: string; labelCn: string; value: string; raw: number; scheme: HeatScheme }[] =
    [
      { label: "Alpha", labelCn: "机会", value: fmtScore(segment.alpha), raw: segment.alpha, scheme: "good-high" },
      { label: "Momentum", labelCn: "动能", value: fmtSigned(segment.momentum), raw: segment.momentum, scheme: "divergent" },
      { label: "Δ 1M", labelCn: "月涨幅", value: fmtPct(segment.changeM), raw: segment.changeM * 5, scheme: "divergent" },
      { label: "Val %ile", labelCn: "估值分位", value: fmtScore(segment.valuationPctile), raw: segment.valuationPctile, scheme: "good-low" },
      { label: "Crowding", labelCn: "拥挤度", value: fmtScore(segment.crowding), raw: segment.crowding, scheme: "good-low" },
      { label: "Supply Tight", labelCn: "供给紧张", value: fmtScore(segment.supplyTightness), raw: segment.supplyTightness, scheme: "good-high" },
      { label: "Earn Rev", labelCn: "盈利修正", value: fmtSigned(segment.earningsRevision), raw: segment.earningsRevision, scheme: "divergent" },
      { label: "Companies", labelCn: "覆盖公司", value: String(segment.companies), raw: 100, scheme: "good-high" },
    ];

  return (
    <div className="mx-auto flex max-w-[1200px] flex-col gap-5">
      {/* breadcrumb + back */}
      <div className="flex items-center gap-2 text-2xs text-brand-500">
        <button
          type="button"
          onClick={() => navigate(-1)}
          className="inline-flex items-center gap-1 rounded-md px-1.5 py-1 font-medium text-brand-200 transition hover:bg-canvas hover:text-brand-900 focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
          aria-label="Go back"
        >
          <ArrowLeft size={14} strokeWidth={2.25} /> Back
        </button>
        <span className="text-brand-700" aria-hidden="true">|</span>
        <nav className="flex items-center gap-1 uppercase tracking-wide" aria-label="Breadcrumb">
          <Link to="/" className="transition hover:text-brand-900">
            Dashboard
          </Link>
          <ChevronRight size={12} strokeWidth={2} className="text-brand-700" />
          <span className="text-brand-200">Chain</span>
          <ChevronRight size={12} strokeWidth={2} className="text-brand-700" />
          <span className="font-semibold text-brand-900">{segment.name}</span>
        </nav>
      </div>

      {/* header card */}
      <Card className="p-5">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-stretch">
          <div className="flex min-w-0 flex-1 flex-col gap-3">
            <div className="flex items-center gap-1.5 text-2xs font-medium uppercase tracking-wide text-brand-500">
              <Layers size={13} /> Chain Segment · 产业链环节
            </div>
            <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
              <h1 className="text-2xl font-semibold tracking-tight text-brand-900">
                {segment.name}
              </h1>
              <span className="text-base text-brand-200">{segment.nameCn}</span>
              <Badge className={regimeChip(segment.regime)}>
                <span
                  className={cn("h-1.5 w-1.5 rounded-full", regimeDot(segment.regime))}
                  aria-hidden="true"
                />
                {REGIME_LABEL[segment.regime].en} · {REGIME_LABEL[segment.regime].cn}
              </Badge>
            </div>
            <div className="flex flex-wrap items-center gap-1.5">
              <Badge className="bg-brand-50 text-brand-700 ring-1 ring-inset ring-brand-100">
                {segment.cycle ? (
                  <>
                    <span className="tnum">{segment.cycle.label}</span>
                    <span className="text-brand-400">
                      · {segment.cycle.labelCn} · 周期位次 {segment.cycle.rank}/5
                    </span>
                  </>
                ) : (
                  <>
                    <span className="tnum">Tier {segment.tier}</span>
                    <span className="text-brand-400">· upstream→downstream</span>
                  </>
                )}
              </Badge>
              {segment.markets.map((m) => (
                <Badge
                  key={m}
                  className="bg-surface-2 text-brand-500 ring-1 ring-inset ring-line"
                >
                  {m}
                </Badge>
              ))}
            </div>
            {segment.thesisCn && (
              <p className="max-w-2xl rounded-lg bg-brand-50/60 px-3 py-2 text-sm leading-relaxed text-brand-800 ring-1 ring-inset ring-brand-100">
                {segment.thesisCn}
              </p>
            )}
            {segment.note && (
              <p className="max-w-2xl text-sm leading-snug text-brand-500">{segment.note}</p>
            )}
          </div>

          {/* trend sparkline */}
          <div className="flex flex-col justify-between gap-2 lg:w-56 lg:border-l lg:border-line lg:pl-5">
            <div className="flex items-center gap-1.5 text-2xs font-medium uppercase tracking-wide text-brand-500">
              <TrendingUp size={13} /> Trend · 走势
            </div>
            <Sparkline data={segment.spark} width={208} height={56} className="w-full" />
            <div className="flex items-center justify-between text-2xs">
              <span className="uppercase tracking-wide text-brand-500">Δ 1W / 1M</span>
              <span className="tnum font-semibold">
                <span style={{ color: heat(segment.changeW * 5, "divergent", 1).color }}>
                  {fmtPct(segment.changeW)}
                </span>
                <span className="text-brand-700"> / </span>
                <span style={{ color: heat(segment.changeM * 5, "divergent", 1).color }}>
                  {fmtPct(segment.changeM)}
                </span>
              </span>
            </div>
          </div>
        </div>
      </Card>

      {/* metric grid */}
      <Card>
        <SectionHeader
          title="Segment Metrics"
          titleCn="环节指标"
          icon={<Layers size={15} strokeWidth={2} />}
          right={
            <div className="flex items-center gap-1.5 text-2xs uppercase tracking-wide text-brand-500">
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
        <div className="grid grid-cols-2 gap-2 p-4 sm:grid-cols-4">
          {tiles.map((t) => (
            <div
              key={t.label}
              className="flex flex-col gap-1.5 rounded-lg border border-line/60 px-3 py-2.5"
              style={heat(t.raw, t.scheme, 0.16)}
            >
              <div className="flex items-baseline justify-between gap-1">
                <span className="text-2xs font-medium uppercase tracking-wide opacity-70">
                  {t.label}
                </span>
                <span className="text-2xs opacity-50">{t.labelCn}</span>
              </div>
              <span className="tnum text-2xl font-semibold leading-none">{t.value}</span>
            </div>
          ))}
        </div>
      </Card>

      {/* macro crosswalk (renders nothing if the Andy link API is unavailable) */}
      <MacroStrip theme={theme} compact />

      {/* members */}
      <CompanyWatchlist
        companies={companies}
        segments={segments}
        selectedSegmentId={null}
        market={market}
        onCompany={(cid) => navigate(`/genny/company/${cid}`)}
      />

      {/* signals */}
      <SignalFeed
        signals={signals}
        segments={segments}
        selectedSegmentId={null}
        onCompany={(cid) => navigate(`/genny/company/${cid}`)}
      />
    </div>
  );
}
