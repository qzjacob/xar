import {
  Activity,
  BookOpen,
  Compass,
  Layers,
  Loader2,
  Trash2,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { PlotlyChart } from "../../components/charts/PlotlyChart";
import { Badge } from "../../components/ui/Badge";
import { Card } from "../../components/ui/Card";
import { SectionHeader } from "../../components/ui/SectionHeader";
import { fennyApi } from "../../lib/fenny";
import { cn } from "../../lib/format";

// ---------------------------------------------------------------------------
// helpers
// ---------------------------------------------------------------------------

type Dict = Record<string, unknown>;

const num = (v: unknown): number | null =>
  typeof v === "number" && Number.isFinite(v) ? v : null;
const str = (v: unknown): string => (typeof v === "string" ? v : "");
const arr = (v: unknown): unknown[] => (Array.isArray(v) ? v : []);
const rec = (v: unknown): Dict => (v && typeof v === "object" ? (v as Dict) : {});

const f = (v: unknown, d = 2): string => {
  const n = num(v);
  return n === null ? "—" : n.toFixed(d);
};
const pct = (v: unknown, d = 1): string => {
  const n = num(v);
  return n === null ? "—" : `${(n * 100).toFixed(d)}%`;
};
const money = (v: unknown): string => {
  const n = num(v);
  if (n === null) return "—";
  const s = n < 0 ? "-" : "";
  return `${s}$${Math.abs(n).toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
};

const inputCls =
  "rounded-lg border border-line bg-surface-2 px-2 py-1.5 text-xs text-brand-900";
const labelCls = "text-2xs uppercase tracking-wide text-slate-500";

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <label className="flex flex-col gap-1">
      <span className={labelCls}>{label}</span>
      {children}
    </label>
  );
}

function Stat({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone?: "pos" | "neg" | "warn";
}) {
  return (
    <div className="rounded-lg border border-line bg-surface-2 px-3 py-2">
      <div className={labelCls}>{label}</div>
      <div
        className={cn(
          "mt-0.5 text-sm font-semibold tnum",
          tone === "pos" && "text-pos",
          tone === "neg" && "text-neg",
          tone === "warn" && "text-warn-100",
          !tone && "text-brand-900",
        )}
      >
        {value}
      </div>
    </div>
  );
}

function ErrLine({ msg }: { msg: string | null }) {
  if (!msg) return null;
  return <div className="mt-2 text-xs text-neg">{msg}</div>;
}

// ---------------------------------------------------------------------------
// shared market-input state
// ---------------------------------------------------------------------------

interface Market {
  ticker: string;
  spot: number;
  atm_vol: number;
  skew_slope: number;
  skew_curv: number;
  rate: number;
}

const DEFAULT_MARKET: Market = {
  ticker: "AAPL",
  spot: 230,
  atm_vol: 0.3,
  skew_slope: -0.4,
  skew_curv: 0.3,
  rate: 0.045,
};

function marketBody(m: Market): Dict {
  return {
    ticker: m.ticker.trim().toUpperCase() || "AAPL",
    source: "manual",
    spot: m.spot,
    atm_vol: m.atm_vol,
    skew_slope: m.skew_slope,
    skew_curv: m.skew_curv,
    rate: m.rate,
  };
}

function MarketFields({
  m,
  set,
}: {
  m: Market;
  set: (patch: Partial<Market>) => void;
}) {
  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
      <Field label="Ticker">
        <input
          className={inputCls}
          value={m.ticker}
          onChange={(e) => set({ ticker: e.target.value })}
        />
      </Field>
      <Field label="Spot">
        <input
          type="number"
          className={cn(inputCls, "tnum")}
          value={m.spot}
          onChange={(e) => set({ spot: Number(e.target.value) })}
        />
      </Field>
      <Field label="ATM Vol">
        <input
          type="number"
          step="0.01"
          className={cn(inputCls, "tnum")}
          value={m.atm_vol}
          onChange={(e) => set({ atm_vol: Number(e.target.value) })}
        />
      </Field>
      <Field label="Skew Slope">
        <input
          type="number"
          step="0.1"
          className={cn(inputCls, "tnum")}
          value={m.skew_slope}
          onChange={(e) => set({ skew_slope: Number(e.target.value) })}
        />
      </Field>
      <Field label="Skew Curv">
        <input
          type="number"
          step="0.1"
          className={cn(inputCls, "tnum")}
          value={m.skew_curv}
          onChange={(e) => set({ skew_curv: Number(e.target.value) })}
        />
      </Field>
      <Field label="Rate">
        <input
          type="number"
          step="0.005"
          className={cn(inputCls, "tnum")}
          value={m.rate}
          onChange={(e) => set({ rate: Number(e.target.value) })}
        />
      </Field>
    </div>
  );
}

function RunBtn({
  loading,
  stage,
  label,
  onClick,
}: {
  loading: boolean;
  stage: string;
  label: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      disabled={loading}
      onClick={onClick}
      className={cn(
        "inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-semibold text-white transition-colors",
        loading
          ? "cursor-not-allowed bg-surface-2 text-slate-400"
          : "bg-accent-600 hover:brightness-110",
      )}
    >
      {loading && <Loader2 size={13} className="animate-spin" />}
      {loading ? stage || "running…" : label}
    </button>
  );
}

// ===========================================================================
// (1) ANALYZE
// ===========================================================================

function AnalyzeSection({ m, set }: { m: Market; set: (p: Partial<Market>) => void }) {
  const [loading, setLoading] = useState(false);
  const [stage, setStage] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [res, setRes] = useState<Dict | null>(null);

  const run = useCallback(async () => {
    setLoading(true);
    setErr(null);
    setStage("");
    try {
      const out = await fennyApi.optionsAnalyze(marketBody(m), (j) =>
        setStage(j.stage),
      );
      setRes(out);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [m]);

  const analytics = rec(res?.analytics);
  const chain = rec(res?.chain);
  const liq = rec(res?.liquidity);
  const wings = rec(analytics.wing_marks);
  const term = arr(analytics.atm_term) as [number, number][];

  const termTrace = term.length
    ? [
        {
          x: term.map((t) => t[0]),
          y: term.map((t) => t[1] * 100),
          type: "scatter",
          mode: "lines+markers",
          name: "ATM IV",
          line: { color: "#f59e0b", width: 2 },
        },
      ]
    : [];

  const wingOrder = ["10Δ_put", "25Δ_put", "ATM", "25Δ_call", "10Δ_call"];
  const wingPairs = wingOrder
    .filter((k) => k in wings)
    .map((k) => [k, num(wings[k])] as [string, number | null]);

  return (
    <Card>
      <SectionHeader
        title="Analyze"
        titleCn="波动率曲面分析"
        icon={<Activity size={15} />}
        right={<RunBtn loading={loading} stage={stage} label="Analyze" onClick={run} />}
      />
      <div className="space-y-4 p-4">
        <MarketFields m={m} set={set} />
        <ErrLine msg={err} />

        {res && (
          <>
            <div className="flex flex-wrap items-center gap-2 text-xs text-slate-400">
              <Badge className="bg-surface-2 text-accent-100">
                {str(chain.ticker) || m.ticker}
              </Badge>
              <span className="tnum">spot {f(chain.spot)}</span>
              <span>·</span>
              <span>
                {num(chain.n_contracts) ?? "—"} contracts / {num(chain.n_expiries) ?? "—"}{" "}
                expiries
              </span>
              <span>·</span>
              <span>asof {str(analytics.asof)}</span>
              <span>·</span>
              <Badge
                className={cn(
                  "capitalize",
                  str(analytics.vol_regime) === "elevated"
                    ? "bg-warn-100/10 text-warn-100"
                    : "bg-surface-2 text-slate-400",
                )}
              >
                regime: {str(analytics.vol_regime) || "—"}
              </Badge>
              <Badge className="bg-surface-2 capitalize text-slate-400">
                term: {str(analytics.term_structure) || "—"}
              </Badge>
            </div>

            <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
              <Stat label="1M ATM IV" value={pct(analytics.iv_1m_atm)} />
              <Stat label="Skew 90 / 3M" value={pct(analytics.skew_90_3m)} />
              <Stat
                label="RR 25Δ 3M"
                value={pct(analytics.risk_reversal_25d_3m)}
                tone={
                  (num(analytics.risk_reversal_25d_3m) ?? 0) < 0 ? "neg" : "pos"
                }
              />
              <Stat label="RR 10Δ 3M" value={pct(analytics.risk_reversal_10d_3m)} />
              <Stat label="BF 25Δ 3M" value={pct(analytics.butterfly_25d_3m)} />
              <Stat label="BF 10Δ 3M" value={pct(analytics.butterfly_10d_3m)} />
              <Stat label="Term slope 1Y/1M" value={pct(analytics.term_slope_1y_1m)} />
              <Stat
                label="IV-RV gap"
                value={
                  num(analytics.iv_rv_gap) === null
                    ? "n/a"
                    : pct(analytics.iv_rv_gap)
                }
              />
              <Stat
                label="Vol %ile"
                value={
                  num(analytics.vol_1y_percentile) === null
                    ? "n/a"
                    : f(analytics.vol_1y_percentile, 0)
                }
              />
            </div>

            <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
              <div>
                <div className="mb-2 text-2xs uppercase tracking-wide text-slate-500">
                  ATM term structure
                </div>
                {termTrace.length ? (
                  <PlotlyChart
                    data={termTrace}
                    height={240}
                    layout={{
                      xaxis: { title: "maturity (yrs)" },
                      yaxis: { title: "IV %", ticksuffix: "%" },
                    }}
                  />
                ) : (
                  <div className="text-xs text-slate-500">no term data</div>
                )}
              </div>
              <div>
                <div className="mb-2 text-2xs uppercase tracking-wide text-slate-500">
                  Wing marks (3M IV by delta)
                </div>
                <div className="overflow-hidden rounded-lg border border-line">
                  <table className="w-full text-xs">
                    <tbody>
                      {wingPairs.map(([k, v]) => (
                        <tr key={k} className="border-b border-line last:border-0">
                          <td className="px-3 py-1.5 text-slate-400">{k}</td>
                          <td className="px-3 py-1.5 text-right font-semibold tnum text-brand-900">
                            {pct(v)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <div className="mt-3 grid grid-cols-2 gap-2">
                  <Stat label="Liquidity score" value={f(liq.median_score, 1)} />
                  <Stat label="% tradable" value={f(liq.pct_tradable, 0)} tone="pos" />
                  <Stat label="Median rel spread" value={pct(liq.median_rel_spread)} />
                  <Stat
                    label="Open interest"
                    value={money(liq.total_open_interest).replace("$", "")}
                  />
                </div>
              </div>
            </div>
          </>
        )}
      </div>
    </Card>
  );
}

// ===========================================================================
// (2) ADVISE
// ===========================================================================

const DIRECTIONS = ["bullish", "bearish", "neutral"] as const;
const HORIZONS = ["weeks", "months", "years"] as const;
const VOL_VIEWS = ["rising", "falling", "stable", "spiked", "depressed"] as const;

function AdviseSection({ m, set }: { m: Market; set: (p: Partial<Market>) => void }) {
  const [loading, setLoading] = useState(false);
  const [stage, setStage] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [res, setRes] = useState<Dict | null>(null);

  const [direction, setDirection] =
    useState<(typeof DIRECTIONS)[number]>("bullish");
  const [horizon, setHorizon] = useState<(typeof HORIZONS)[number]>("months");
  const [conviction, setConviction] = useState(3);
  const [volView, setVolView] = useState<(typeof VOL_VIEWS)[number]>("stable");
  const [riskBudget, setRiskBudget] = useState(5);
  const [language, setLanguage] = useState<"en" | "zh">("en");

  const run = useCallback(async () => {
    setLoading(true);
    setErr(null);
    setStage("");
    try {
      const out = await fennyApi.optionsAdvise(
        {
          ...marketBody(m),
          direction,
          horizon,
          conviction,
          vol_view: volView,
          risk_budget_pct: riskBudget,
          language,
        },
        (j) => setStage(j.stage),
      );
      setRes(out);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [m, direction, horizon, conviction, volView, riskBudget, language]);

  const candidates = arr(res?.candidates) as Dict[];
  const shortlist = arr(res?.shortlist) as Dict[];
  const narrative = str(res?.narrative);
  const top = candidates[0];

  return (
    <Card>
      <SectionHeader
        title="Advise"
        titleCn="观点转策略"
        icon={<Compass size={15} />}
        right={<RunBtn loading={loading} stage={stage} label="Advise" onClick={run} />}
      />
      <div className="space-y-4 p-4">
        <MarketFields m={m} set={set} />

        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
          <Field label="Direction">
            <select
              className={inputCls}
              value={direction}
              onChange={(e) =>
                setDirection(e.target.value as (typeof DIRECTIONS)[number])
              }
            >
              {DIRECTIONS.map((d) => (
                <option key={d} value={d}>
                  {d}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Horizon">
            <select
              className={inputCls}
              value={horizon}
              onChange={(e) =>
                setHorizon(e.target.value as (typeof HORIZONS)[number])
              }
            >
              {HORIZONS.map((h) => (
                <option key={h} value={h}>
                  {h}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Conviction (1-5)">
            <input
              type="number"
              min={1}
              max={5}
              className={cn(inputCls, "tnum")}
              value={conviction}
              onChange={(e) => setConviction(Number(e.target.value))}
            />
          </Field>
          <Field label="Vol View">
            <select
              className={inputCls}
              value={volView}
              onChange={(e) =>
                setVolView(e.target.value as (typeof VOL_VIEWS)[number])
              }
            >
              {VOL_VIEWS.map((v) => (
                <option key={v} value={v}>
                  {v}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Risk Budget %">
            <input
              type="number"
              step="0.5"
              className={cn(inputCls, "tnum")}
              value={riskBudget}
              onChange={(e) => setRiskBudget(Number(e.target.value))}
            />
          </Field>
          <Field label="Language">
            <select
              className={inputCls}
              value={language}
              onChange={(e) => setLanguage(e.target.value as "en" | "zh")}
            >
              <option value="en">en</option>
              <option value="zh">zh</option>
            </select>
          </Field>
        </div>

        <ErrLine msg={err} />

        {top && <RecommendedStrategy candidate={top} />}

        {narrative && (
          <div className="rounded-lg border border-line bg-surface-2 p-3 text-xs leading-relaxed text-slate-400">
            <div className="mb-1 text-2xs uppercase tracking-wide text-slate-500">
              Rationale
            </div>
            {narrative}
          </div>
        )}

        {shortlist.length > 1 && (
          <div>
            <div className="mb-2 text-2xs uppercase tracking-wide text-slate-500">
              Shortlist
            </div>
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
              {shortlist.map((s, i) => (
                <div
                  key={`${str(s.name)}-${i}`}
                  className="rounded-lg border border-line bg-surface-2 p-3"
                >
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-semibold capitalize text-brand-900">
                      {str(s.name).replace(/_/g, " ")}
                    </span>
                    <Badge className="bg-surface text-accent-100 tnum">
                      {f(s.score, 0)}
                    </Badge>
                  </div>
                  <div className="mt-1 text-2xs text-slate-500">
                    {str(s.family)} · {str(s.view)}
                  </div>
                  <div className="mt-1 text-xs text-slate-400">
                    {str(s.description)}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </Card>
  );
}

function RecommendedStrategy({ candidate }: { candidate: Dict }) {
  const strat = rec(candidate.strategy);
  const val = rec(candidate.valuation);
  const greeks = rec(val.greeks);
  const legs = arr(strat.option_legs) as Dict[];
  const payoff = arr(val.payoff_at_expiry) as [number, number][];
  const breakevens = arr(val.breakevens) as number[];

  const payoffTrace = payoff.length
    ? [
        {
          x: payoff.map((p) => p[0]),
          y: payoff.map((p) => p[1]),
          type: "scatter",
          mode: "lines",
          name: "P/L at expiry",
          line: { color: "#2dc876", width: 2 },
          fill: "tozeroy",
          fillcolor: "rgba(45,200,118,0.08)",
        },
      ]
    : [];

  const greekRows: [string, unknown][] = [
    ["Delta", greeks.delta],
    ["Gamma", greeks.gamma],
    ["Vega", greeks.vega],
    ["Theta", greeks.theta],
    ["Rho", greeks.rho],
  ];

  return (
    <div className="rounded-lg border border-accent-500/40 bg-surface-2 p-4">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-bold capitalize text-brand-900">
          {str(strat.name).replace(/_/g, " ") || "strategy"}
        </span>
        <Badge className="bg-surface capitalize text-accent-100">
          {str(candidate.family)}
        </Badge>
        <Badge className="bg-surface capitalize text-slate-400">
          {str(candidate.view_tag)}
        </Badge>
        <span className="ml-auto text-2xs text-slate-500">
          fit{" "}
          <span className="font-semibold tnum text-brand-900">
            {f(candidate.fit_score, 1)}
          </span>{" "}
          · liq-adj{" "}
          <span className="font-semibold tnum text-brand-900">
            {f(candidate.liquidity_adjusted_score, 1)}
          </span>
        </span>
      </div>

      {str(candidate.rationale) && (
        <p className="mt-2 text-xs leading-relaxed text-slate-400">
          {str(candidate.rationale)}
        </p>
      )}

      <div className="mt-3 grid grid-cols-1 gap-4 lg:grid-cols-2">
        <div className="space-y-3">
          <div>
            <div className="mb-1 text-2xs uppercase tracking-wide text-slate-500">
              Legs
            </div>
            <div className="overflow-hidden rounded-lg border border-line">
              <table className="w-full text-xs">
                <thead>
                  <tr className="bg-surface text-2xs uppercase text-slate-500">
                    <th className="px-2 py-1.5 text-left">Kind</th>
                    <th className="px-2 py-1.5 text-right">Strike</th>
                    <th className="px-2 py-1.5 text-right">Qty</th>
                    <th className="px-2 py-1.5 text-right">Expiry</th>
                  </tr>
                </thead>
                <tbody>
                  {legs.map((l, i) => (
                    <tr key={i} className="border-t border-line">
                      <td className="px-2 py-1.5 capitalize text-brand-900">
                        {str(l.kind)}
                      </td>
                      <td className="px-2 py-1.5 text-right tnum">{f(l.strike)}</td>
                      <td
                        className={cn(
                          "px-2 py-1.5 text-right tnum",
                          (num(l.quantity) ?? 0) < 0 ? "text-neg" : "text-pos",
                        )}
                      >
                        {num(l.quantity) ?? "—"}
                      </td>
                      <td className="px-2 py-1.5 text-right text-slate-400">
                        {str(l.expiry)}
                      </td>
                    </tr>
                  ))}
                  {str(strat.stock_leg) && (
                    <tr className="border-t border-line">
                      <td className="px-2 py-1.5 text-brand-900">stock</td>
                      <td className="px-2 py-1.5 text-right text-slate-500" colSpan={3}>
                        {str(strat.stock_leg)}
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>

          <div className="grid grid-cols-3 gap-2">
            <Stat label="Net debit" value={money(val.net_debit)} />
            <Stat
              label="Max profit"
              value={num(val.max_profit) === null ? "∞" : money(val.max_profit)}
              tone="pos"
            />
            <Stat label="Max loss" value={money(val.max_loss)} tone="neg" />
            <Stat label="Prob profit" value={pct(val.prob_profit)} />
            <Stat label="Breakeven" value={breakevens.map((b) => f(b)).join(", ")} />
            <Stat label="Days to exp" value={f(val.days_to_expiry, 0)} />
          </div>

          <div>
            <div className="mb-1 text-2xs uppercase tracking-wide text-slate-500">
              Greeks
            </div>
            <div className="grid grid-cols-5 gap-1.5">
              {greekRows.map(([k, v]) => (
                <div
                  key={k}
                  className="rounded-md border border-line bg-surface px-1.5 py-1 text-center"
                >
                  <div className="text-2xs text-slate-500">{k}</div>
                  <div className="text-xs font-semibold tnum text-brand-900">
                    {f(v, 1)}
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>

        <div>
          <div className="mb-1 text-2xs uppercase tracking-wide text-slate-500">
            Payoff at expiry
          </div>
          {payoffTrace.length ? (
            <PlotlyChart
              data={payoffTrace}
              height={260}
              layout={{
                xaxis: { title: "underlying" },
                yaxis: { title: "P/L ($)" },
              }}
            />
          ) : (
            <div className="text-xs text-slate-500">no payoff data</div>
          )}
        </div>
      </div>
    </div>
  );
}

// ===========================================================================
// (3) BLOTTER
// ===========================================================================

function BlotterSection() {
  const [entries, setEntries] = useState<Dict[]>([]);
  const [greeks, setGreeks] = useState<Dict | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setErr(null);
    try {
      const [b, g] = await Promise.all([
        fennyApi.blotter(),
        fennyApi.blotterGreeks(),
      ]);
      setEntries(arr(b.entries) as Dict[]);
      setGreeks(g);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const remove = useCallback(
    async (id: string) => {
      try {
        await fennyApi.blotterRemove(id);
        await load();
      } catch (e) {
        setErr(e instanceof Error ? e.message : String(e));
      }
    },
    [load],
  );

  const g = greeks ?? {};
  const byUnderlying = rec(g.by_underlying);

  return (
    <Card>
      <SectionHeader
        title="Blotter"
        titleCn="持仓与组合希腊值"
        icon={<BookOpen size={15} />}
        right={
          <button
            type="button"
            onClick={load}
            disabled={loading}
            className="inline-flex items-center gap-1.5 rounded-lg border border-line bg-surface-2 px-3 py-1.5 text-xs font-semibold text-brand-900 hover:brightness-110"
          >
            {loading && <Loader2 size={13} className="animate-spin" />}
            Refresh
          </button>
        }
      />
      <div className="space-y-4 p-4">
        <ErrLine msg={err} />

        <div className="grid grid-cols-3 gap-2 sm:grid-cols-4 lg:grid-cols-7">
          <Stat label="Positions" value={f(g.n_positions, 0)} />
          <Stat
            label="Delta"
            value={f(g.delta, 1)}
            tone={(num(g.delta) ?? 0) < 0 ? "neg" : "pos"}
          />
          <Stat label="Gamma" value={f(g.gamma, 2)} />
          <Stat label="Vega" value={f(g.vega, 0)} />
          <Stat label="Theta" value={f(g.theta, 1)} tone="neg" />
          <Stat label="Rho" value={f(g.rho, 0)} />
          <Stat label="Notional" value={money(g.notional_exposure)} />
        </div>

        {num(g.current_pnl) !== null && (
          <Stat
            label="Current P/L"
            value={money(g.current_pnl)}
            tone={(num(g.current_pnl) ?? 0) < 0 ? "neg" : "pos"}
          />
        )}

        {Object.keys(byUnderlying).length > 0 && (
          <div className="flex flex-wrap gap-2 text-2xs text-slate-400">
            {Object.entries(byUnderlying).map(([k, v]) => (
              <Badge key={k} className="bg-surface-2 text-slate-400">
                {k}: Δ {f(rec(v).delta, 1)}
              </Badge>
            ))}
          </div>
        )}

        {entries.length === 0 ? (
          <div className="rounded-lg border border-dashed border-line bg-surface-2 px-4 py-8 text-center text-xs text-slate-500">
            <Layers size={18} className="mx-auto mb-2 opacity-60" />
            No positions on the blotter.
          </div>
        ) : (
          <div className="overflow-x-auto rounded-lg border border-line">
            <table className="w-full min-w-[560px] text-xs">
              <thead>
                <tr className="bg-surface-2 text-2xs uppercase text-slate-500">
                  <th className="px-3 py-2 text-left">Ticker</th>
                  <th className="px-3 py-2 text-left">Strategy</th>
                  <th className="px-3 py-2 text-left">Status</th>
                  <th className="px-3 py-2 text-right">Net debit</th>
                  <th className="px-3 py-2 text-right">Delta</th>
                  <th className="px-3 py-2 text-left">Notes</th>
                  <th className="px-3 py-2" />
                </tr>
              </thead>
              <tbody>
                {entries.map((e, i) => {
                  const id = str(e.id) || String(i);
                  const strat = rec(e.strategy);
                  const val = rec(e.valuation);
                  const eg = rec(val.greeks);
                  return (
                    <tr key={id} className="border-t border-line">
                      <td className="px-3 py-2 font-semibold text-brand-900">
                        {str(strat.ticker) || str(e.ticker) || "—"}
                      </td>
                      <td className="px-3 py-2 capitalize text-slate-400">
                        {str(strat.name).replace(/_/g, " ") || "—"}
                      </td>
                      <td className="px-3 py-2">
                        <Badge className="bg-surface-2 capitalize text-slate-400">
                          {str(e.status) || "open"}
                        </Badge>
                      </td>
                      <td className="px-3 py-2 text-right tnum">
                        {money(val.net_debit)}
                      </td>
                      <td className="px-3 py-2 text-right tnum">{f(eg.delta, 1)}</td>
                      <td className="max-w-[180px] truncate px-3 py-2 text-slate-500">
                        {str(e.notes)}
                      </td>
                      <td className="px-3 py-2 text-right">
                        <button
                          type="button"
                          onClick={() => remove(id)}
                          className="text-slate-500 hover:text-neg"
                          title="Remove"
                        >
                          <Trash2 size={14} />
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </Card>
  );
}

// ===========================================================================
// root
// ===========================================================================

const SUBTABS = [
  { key: "analyze", label: "Analyze", cn: "曲面" },
  { key: "advise", label: "Advise", cn: "策略" },
  { key: "blotter", label: "Blotter", cn: "持仓" },
] as const;

export function OptionsDesk() {
  const [sub, setSub] = useState<(typeof SUBTABS)[number]["key"]>("analyze");
  const [market, setMarket] = useState<Market>(DEFAULT_MARKET);
  const set = useCallback(
    (patch: Partial<Market>) => setMarket((m) => ({ ...m, ...patch })),
    [],
  );

  return (
    <div className="space-y-4 p-4">
      <div className="flex gap-1 rounded-lg border border-line bg-surface p-1">
        {SUBTABS.map((t) => (
          <button
            key={t.key}
            type="button"
            onClick={() => setSub(t.key)}
            className={cn(
              "flex-1 rounded-md px-3 py-1.5 text-xs font-semibold transition-colors",
              sub === t.key
                ? "bg-accent-600 text-white"
                : "text-slate-400 hover:text-brand-900",
            )}
          >
            {t.label}
            <span className="ml-1 text-[10px] opacity-70">{t.cn}</span>
          </button>
        ))}
      </div>

      {sub === "analyze" && <AnalyzeSection m={market} set={set} />}
      {sub === "advise" && <AdviseSection m={market} set={set} />}
      {sub === "blotter" && <BlotterSection />}
    </div>
  );
}
