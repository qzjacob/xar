import { useState } from "react";
import {
  Calculator,
  LineChart,
  Loader2,
  Play,
  Plus,
  Sigma,
  Trash2,
  Wallet,
} from "lucide-react";
import { fennyApi } from "../../lib/fenny";
import { PlotlyChart } from "../../components/fenny/PlotlyChart";
import { Card } from "../../components/ui/Card";
import { SectionHeader } from "../../components/ui/SectionHeader";
import { Badge } from "../../components/ui/Badge";
import { cn } from "../../lib/format";
import type { Job } from "../../types-fenny";

// --- shapes returned by /jobs/quote + /jobs/solve (see main.py _run_job) -----
interface Pricing {
  notional: number;
  coupon_rate: number;
  pv: number;
  pv_se: number;
  price_pct: number;
  redemption_pv: number;
  coupon_factor: number;
  prob_autocall: number;
  prob_knock_in: number;
  expected_life: number;
  n_paths: number;
  method: string;
}
interface Payoff {
  worst_of: number[];
  redemption: number[];
  ki: number;
  strike: number;
}
interface ScenarioRow {
  shock: number;
  pv: number;
  price_pct: number;
  prob_autocall: number;
  prob_knock_in: number;
}
interface Greeks {
  delta: number[];
  gamma: number[];
  vega: number[];
  theta: number;
  rho: number;
  carry: number;
  corr_sens: number;
  skew_vega?: number;
  bucketed_vega?: Record<string, number>;
}
interface Product {
  variant: string;
  n_assets: number;
  basket: string;
  currency: string;
  notional: number;
  maturity: string;
  tickers: string[];
}
interface QuoteResult {
  coupon_rate: number;
  coupon_rate_se: number;
  reoffer_fraction: number;
  pricing: Pricing;
  fees: Record<string, number>;
  payoff_diagram: Payoff;
  scenario_table: ScenarioRow[] | null;
  greeks: Greeks | null;
  product: Product;
}

interface Asset {
  ticker: string;
  spot: number;
  atm_vol: number;
  skew_slope: number;
  skew_curv: number;
}

type Variant = "fcn" | "phoenix" | "snowball";

const INPUT =
  "w-full rounded-lg border border-line bg-surface-2 px-2 py-1.5 text-xs text-brand-900 outline-none focus:border-accent-500";

// six months out from today, iso yyyy-mm-dd
function isoPlusMonths(base: string, months: number): string {
  const d = new Date(base + "T00:00:00Z");
  d.setUTCMonth(d.getUTCMonth() + months);
  return d.toISOString().slice(0, 10);
}
const TODAY = new Date().toISOString().slice(0, 10);

function money(n: number, ccy = "USD"): string {
  const sym = ccy === "USD" ? "$" : ccy === "EUR" ? "€" : ccy === "GBP" ? "£" : "";
  return sym + n.toLocaleString(undefined, { maximumFractionDigits: 0 });
}
function pct(n: number, d = 1): string {
  return (n * 100).toFixed(d) + "%";
}

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <label className="block">
      <span className="mb-1 flex items-baseline gap-1">
        <span className="text-2xs font-medium text-slate-400">{label}</span>
        {hint && <span className="text-[10px] text-slate-500">{hint}</span>}
      </span>
      {children}
    </label>
  );
}

function Stat({
  label,
  value,
  sub,
  tone,
}: {
  label: string;
  value: string;
  sub?: string;
  tone?: "pos" | "neg" | "warn";
}) {
  const t =
    tone === "pos"
      ? "text-pos"
      : tone === "neg"
        ? "text-neg"
        : tone === "warn"
          ? "text-warn-100"
          : "text-brand-900";
  return (
    <div className="rounded-lg border border-line bg-surface-2 px-3 py-2">
      <div className="text-2xs uppercase tracking-wide text-slate-500">{label}</div>
      <div className={cn("mt-0.5 text-lg font-semibold tnum", t)}>{value}</div>
      {sub && <div className="mt-0.5 text-2xs text-slate-400">{sub}</div>}
    </div>
  );
}

export function QuoteDesk() {
  const [variant, setVariant] = useState<Variant>("fcn");
  const [notional, setNotional] = useState(1_000_000);
  const [tradeDate, setTradeDate] = useState(TODAY);
  const [strikeDate, setStrikeDate] = useState(TODAY);
  const [maturity, setMaturity] = useState(isoPlusMonths(TODAY, 6));
  const [kiBarrier, setKiBarrier] = useState(0.65);
  const [autocallBarrier, setAutocallBarrier] = useState(1.0);
  const [couponBarrier, setCouponBarrier] = useState(0.7);
  const [memory, setMemory] = useState(true);
  const [targetCoupon, setTargetCoupon] = useState(12); // % p.a.
  const [rate, setRate] = useState(0.045);
  const [rho, setRho] = useState(0.5);
  const [assets, setAssets] = useState<Asset[]>([
    { ticker: "AAPL", spot: 100, atm_vol: 0.3, skew_slope: -0.4, skew_curv: 0.3 },
  ]);

  const [loading, setLoading] = useState(false);
  const [mode, setMode] = useState<"quote" | "solve">("quote");
  const [stage, setStage] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [res, setRes] = useState<QuoteResult | null>(null);

  function patchAsset(i: number, patch: Partial<Asset>) {
    setAssets((a) => a.map((x, j) => (j === i ? { ...x, ...patch } : x)));
  }
  function addAsset() {
    setAssets((a) =>
      a.length >= 3
        ? a
        : [...a, { ticker: "", spot: 100, atm_vol: 0.3, skew_slope: -0.4, skew_curv: 0.3 }],
    );
  }
  function removeAsset(i: number) {
    setAssets((a) => (a.length <= 1 ? a : a.filter((_, j) => j !== i)));
  }

  async function run(which: "quote" | "solve") {
    setLoading(true);
    setMode(which);
    setError(null);
    setStage("building termsheet");
    try {
      const tickers = assets.map((a) => a.ticker.trim().toUpperCase()).filter(Boolean);
      if (tickers.length === 0) throw new Error("add at least one ticker");
      const presetReq = {
        variant,
        tickers,
        notional,
        currency: "USD",
        trade_date: tradeDate,
        strike_date: strikeDate,
        maturity,
        coupon_rate: which === "solve" ? null : targetCoupon / 100,
        frequency: "quarterly",
        autocall_barrier: autocallBarrier,
        ki_barrier: kiBarrier,
        coupon_barrier: couponBarrier,
        memory,
      };
      const termsheet = await fennyApi.presetTermsheet(presetReq);
      setStage("pricing");
      const market = {
        source: "manual",
        rate,
        assets: assets.map((a) => ({
          ticker: a.ticker.trim().toUpperCase(),
          spot: a.spot,
          atm_vol: a.atm_vol,
          skew_slope: a.skew_slope,
          skew_curv: a.skew_curv,
        })),
        rho,
      };
      const body = {
        termsheet,
        market,
        mc: { n_paths: 40_000 },
        include_greeks: true,
        include_scenario: true,
        ...(which === "quote" ? { coupon_rate: targetCoupon / 100 } : {}),
      };
      const onP = (j: Job) => setStage(j.stage || "pricing");
      const out =
        which === "solve"
          ? await fennyApi.solve(body, onP)
          : await fennyApi.quote(body, onP);
      setRes(out as unknown as QuoteResult);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
      setStage("");
    }
  }

  const p = res?.pricing;
  const pd = res?.payoff_diagram;
  const g = res?.greeks;
  const prod = res?.product;
  const ccy = prod?.currency ?? "USD";
  const isSolve = mode === "solve";

  return (
    <div className="grid grid-cols-1 gap-4 p-4 xl:grid-cols-[380px_1fr]">
      {/* ------------------------------------------------------------ FORM */}
      <Card className="self-start">
        <SectionHeader
          title="Structure"
          titleCn="票据结构"
          icon={<Calculator size={15} />}
          right={<Badge className="bg-surface-2 text-slate-400">manual</Badge>}
        />
        <div className="space-y-3 p-4">
          <Field label="Variant" hint="产品类型">
            <div className="grid grid-cols-3 gap-1">
              {(["fcn", "phoenix", "snowball"] as Variant[]).map((v) => (
                <button
                  key={v}
                  type="button"
                  onClick={() => setVariant(v)}
                  className={cn(
                    "rounded-lg border px-2 py-1.5 text-2xs font-semibold capitalize transition-colors",
                    variant === v
                      ? "border-accent-500 bg-accent-600 text-white"
                      : "border-line bg-surface-2 text-slate-400 hover:text-brand-900",
                  )}
                >
                  {v}
                </button>
              ))}
            </div>
          </Field>

          {/* underlyings */}
          <div>
            <div className="mb-1 flex items-center justify-between">
              <span className="text-2xs font-medium text-slate-400">
                Underlyings <span className="text-[10px] text-slate-500">worst-of · 1–3</span>
              </span>
              <button
                type="button"
                onClick={addAsset}
                disabled={assets.length >= 3}
                className="inline-flex items-center gap-0.5 rounded-md border border-line px-1.5 py-0.5 text-[10px] text-slate-400 hover:text-brand-900 disabled:opacity-40"
              >
                <Plus size={11} /> add
              </button>
            </div>
            <div className="space-y-2">
              {assets.map((a, i) => (
                <div key={i} className="rounded-lg border border-line bg-surface-2 p-2">
                  <div className="mb-1.5 flex items-center gap-1.5">
                    <input
                      className={cn(INPUT, "flex-1 font-semibold uppercase")}
                      value={a.ticker}
                      placeholder="TICKER"
                      onChange={(e) => patchAsset(i, { ticker: e.target.value })}
                    />
                    {assets.length > 1 && (
                      <button
                        type="button"
                        onClick={() => removeAsset(i)}
                        className="rounded-md p-1 text-slate-500 hover:text-neg"
                      >
                        <Trash2 size={13} />
                      </button>
                    )}
                  </div>
                  <div className="grid grid-cols-2 gap-1.5">
                    <Field label="Spot">
                      <input
                        type="number"
                        className={INPUT}
                        value={a.spot}
                        onChange={(e) => patchAsset(i, { spot: +e.target.value })}
                      />
                    </Field>
                    <Field label="ATM vol">
                      <input
                        type="number"
                        step="0.01"
                        className={INPUT}
                        value={a.atm_vol}
                        onChange={(e) => patchAsset(i, { atm_vol: +e.target.value })}
                      />
                    </Field>
                    <Field label="Skew slope">
                      <input
                        type="number"
                        step="0.05"
                        className={INPUT}
                        value={a.skew_slope}
                        onChange={(e) => patchAsset(i, { skew_slope: +e.target.value })}
                      />
                    </Field>
                    <Field label="Skew curv">
                      <input
                        type="number"
                        step="0.05"
                        className={INPUT}
                        value={a.skew_curv}
                        onChange={(e) => patchAsset(i, { skew_curv: +e.target.value })}
                      />
                    </Field>
                  </div>
                </div>
              ))}
            </div>
          </div>

          <Field label="Notional" hint="USD">
            <input
              type="number"
              className={INPUT}
              value={notional}
              onChange={(e) => setNotional(+e.target.value)}
            />
          </Field>

          <div className="grid grid-cols-3 gap-1.5">
            <Field label="Trade">
              <input
                type="date"
                className={INPUT}
                value={tradeDate}
                onChange={(e) => setTradeDate(e.target.value)}
              />
            </Field>
            <Field label="Strike">
              <input
                type="date"
                className={INPUT}
                value={strikeDate}
                onChange={(e) => setStrikeDate(e.target.value)}
              />
            </Field>
            <Field label="Maturity">
              <input
                type="date"
                className={INPUT}
                value={maturity}
                onChange={(e) => setMaturity(e.target.value)}
              />
            </Field>
          </div>

          <div className="grid grid-cols-2 gap-1.5">
            <Field label="KI barrier" hint="% start">
              <input
                type="number"
                step="0.05"
                className={INPUT}
                value={kiBarrier}
                onChange={(e) => setKiBarrier(+e.target.value)}
              />
            </Field>
            <Field label="Autocall" hint="% start">
              <input
                type="number"
                step="0.05"
                className={INPUT}
                value={autocallBarrier}
                onChange={(e) => setAutocallBarrier(+e.target.value)}
              />
            </Field>
            {variant === "phoenix" && (
              <>
                <Field label="Coupon barrier" hint="% start">
                  <input
                    type="number"
                    step="0.05"
                    className={INPUT}
                    value={couponBarrier}
                    onChange={(e) => setCouponBarrier(+e.target.value)}
                  />
                </Field>
                <Field label="Memory">
                  <button
                    type="button"
                    onClick={() => setMemory((m) => !m)}
                    className={cn(
                      "w-full rounded-lg border px-2 py-1.5 text-2xs font-semibold",
                      memory
                        ? "border-accent-500 bg-accent-600 text-white"
                        : "border-line bg-surface-2 text-slate-400",
                    )}
                  >
                    {memory ? "On" : "Off"}
                  </button>
                </Field>
              </>
            )}
            <Field label="Target coupon" hint="% p.a.">
              <input
                type="number"
                step="0.25"
                className={INPUT}
                value={targetCoupon}
                onChange={(e) => setTargetCoupon(+e.target.value)}
              />
            </Field>
            <Field label="Rate" hint="risk-free">
              <input
                type="number"
                step="0.005"
                className={INPUT}
                value={rate}
                onChange={(e) => setRate(+e.target.value)}
              />
            </Field>
            {assets.length > 1 && (
              <Field label="Correlation ρ">
                <input
                  type="number"
                  step="0.05"
                  className={INPUT}
                  value={rho}
                  onChange={(e) => setRho(+e.target.value)}
                />
              </Field>
            )}
          </div>

          <div className="grid grid-cols-2 gap-2 pt-1">
            <button
              type="button"
              onClick={() => run("quote")}
              disabled={loading}
              className="inline-flex items-center justify-center gap-1.5 rounded-lg bg-accent-600 px-3 py-2 text-xs font-semibold text-white hover:bg-accent-500 disabled:opacity-50"
            >
              {loading && mode === "quote" ? (
                <Loader2 size={14} className="animate-spin" />
              ) : (
                <Play size={14} />
              )}
              Get quote
            </button>
            <button
              type="button"
              onClick={() => run("solve")}
              disabled={loading}
              className="inline-flex items-center justify-center gap-1.5 rounded-lg border border-accent-500 px-3 py-2 text-xs font-semibold text-accent-100 hover:bg-surface-2 disabled:opacity-50"
            >
              {loading && mode === "solve" ? (
                <Loader2 size={14} className="animate-spin" />
              ) : (
                <Sigma size={14} />
              )}
              Solve coupon
            </button>
          </div>
          {loading && (
            <div className="text-2xs text-slate-400">
              running… <span className="text-accent-100">{stage || "pricing"}</span>
            </div>
          )}
          {error && <div className="text-2xs text-neg">Error: {error}</div>}
        </div>
      </Card>

      {/* --------------------------------------------------------- RESULTS */}
      <div className="min-w-0 space-y-4">
        {!res && !loading && (
          <Card>
            <div className="p-10 text-center text-xs text-slate-400">
              Configure the note and press{" "}
              <span className="font-semibold text-brand-900">Get quote</span> to price a single
              structured note in manual mode.
            </div>
          </Card>
        )}

        {res && p && (
          <>
            {/* headline pricing */}
            <Card>
              <SectionHeader
                title="Pricing"
                titleCn="定价"
                icon={<Wallet size={15} />}
                right={
                  <div className="flex items-center gap-1.5">
                    <Badge className="bg-surface-2 capitalize text-slate-400">
                      {prod?.variant}
                    </Badge>
                    <Badge className="bg-surface-2 text-slate-400">
                      {prod?.tickers.join(" · ")}
                    </Badge>
                  </div>
                }
              />
              <div className="grid grid-cols-2 gap-2 p-4 sm:grid-cols-4">
                <Stat
                  label={isSolve ? "Fair coupon" : "Coupon"}
                  value={pct(res.coupon_rate, 2) + " p.a."}
                  sub={
                    isSolve && res.coupon_rate_se
                      ? "± " + (res.coupon_rate_se * 100).toFixed(3) + "%"
                      : undefined
                  }
                  tone="pos"
                />
                <Stat
                  label="Fair value"
                  value={p.price_pct.toFixed(2) + "%"}
                  sub={"of par"}
                />
                <Stat
                  label="PV"
                  value={money(p.pv, ccy)}
                  sub={"± " + money(p.pv_se, ccy)}
                />
                <Stat
                  label="Reoffer"
                  value={(res.reoffer_fraction * 100).toFixed(2) + "%"}
                  sub="issue price"
                />
                <Stat label="Prob. autocall" value={pct(p.prob_autocall)} tone="pos" />
                <Stat label="Prob. knock-in" value={pct(p.prob_knock_in)} tone="warn" />
                <Stat label="Expected life" value={p.expected_life.toFixed(2) + " yr"} />
                <Stat
                  label="Redemption PV"
                  value={money(p.redemption_pv, ccy)}
                  sub={`coupon leg ${money(p.coupon_factor, ccy)}`}
                />
              </div>
              {/* fees */}
              {res.fees && (
                <div className="flex flex-wrap gap-x-4 gap-y-1 border-t border-line px-4 py-2.5 text-2xs text-slate-400">
                  {Object.entries(res.fees).map(([k, v]) => (
                    <span key={k}>
                      {k.replace(/_/g, " ")}:{" "}
                      <span className="tnum text-brand-900">{v.toFixed(2)}</span>
                    </span>
                  ))}
                  <span className="ml-auto text-slate-500">
                    {p.n_paths.toLocaleString()} paths · {p.method}
                  </span>
                </div>
              )}
            </Card>

            {/* payoff diagram */}
            {pd && (
              <Card>
                <SectionHeader
                  title="Payoff diagram"
                  titleCn="到期收益"
                  icon={<LineChart size={15} />}
                  right={
                    <span className="text-2xs text-slate-400">
                      KI {pct(pd.ki, 0)} · strike {pct(pd.strike, 0)}
                    </span>
                  }
                />
                <div className="p-2">
                  <PlotlyChart
                    height={300}
                    data={[
                      {
                        x: pd.worst_of,
                        y: pd.redemption,
                        type: "scatter",
                        mode: "lines",
                        line: { color: "#f59e0b", width: 2.5, shape: "spline" },
                        fill: "tozeroy",
                        fillcolor: "rgba(245,158,11,0.06)",
                        name: "redemption",
                        hovertemplate:
                          "worst-of %{x:.0%}<br>redeem %{y:,.0f}<extra></extra>",
                      },
                    ]}
                    layout={{
                      xaxis: {
                        title: { text: "worst performer at maturity (% of start)", font: { size: 10 } },
                        tickformat: ".0%",
                      },
                      yaxis: { tickformat: ",.2s", tickprefix: ccy === "USD" ? "$" : "" },
                      shapes: [
                        {
                          type: "line",
                          x0: pd.ki,
                          x1: pd.ki,
                          y0: 0,
                          y1: 1,
                          yref: "paper",
                          line: { color: "#f46060", dash: "dot", width: 1.3 },
                        },
                        {
                          type: "line",
                          x0: pd.strike,
                          x1: pd.strike,
                          y0: 0,
                          y1: 1,
                          yref: "paper",
                          line: { color: "#7c9cf5", dash: "dot", width: 1 },
                        },
                      ],
                    }}
                  />
                </div>
              </Card>
            )}

            {/* scenario table */}
            {res.scenario_table && res.scenario_table.length > 0 && (
              <Card>
                <SectionHeader title="Scenario table" titleCn="情景分析" />
                <div className="overflow-x-auto p-2">
                  <table className="w-full min-w-[420px] text-xs">
                    <thead>
                      <tr className="text-slate-500">
                        <th className="px-2 py-1.5 text-left font-medium">Spot shock</th>
                        {res.scenario_table.map((r, i) => (
                          <th key={i} className="px-2 py-1.5 text-right font-medium tnum">
                            {r.shock > 0 ? "+" : ""}
                            {(r.shock * 100).toFixed(0)}%
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody className="text-brand-900">
                      <tr className="border-t border-line">
                        <td className="px-2 py-1.5 text-slate-400">Value (% par)</td>
                        {res.scenario_table.map((r, i) => (
                          <td key={i} className="px-2 py-1.5 text-right tnum">
                            {r.price_pct.toFixed(1)}
                          </td>
                        ))}
                      </tr>
                      <tr className="border-t border-line">
                        <td className="px-2 py-1.5 text-slate-400">Prob. autocall</td>
                        {res.scenario_table.map((r, i) => (
                          <td key={i} className="px-2 py-1.5 text-right tnum text-pos">
                            {(r.prob_autocall * 100).toFixed(0)}%
                          </td>
                        ))}
                      </tr>
                      <tr className="border-t border-line">
                        <td className="px-2 py-1.5 text-slate-400">Prob. capital loss</td>
                        {res.scenario_table.map((r, i) => (
                          <td key={i} className="px-2 py-1.5 text-right tnum text-neg">
                            {(r.prob_knock_in * 100).toFixed(0)}%
                          </td>
                        ))}
                      </tr>
                    </tbody>
                  </table>
                </div>
              </Card>
            )}

            {/* greeks */}
            {g && (
              <Card>
                <SectionHeader
                  title="Greeks & risk"
                  titleCn="希腊值"
                  icon={<Sigma size={15} />}
                />
                <div className="overflow-x-auto p-2">
                  <table className="w-full min-w-[360px] text-xs">
                    <thead>
                      <tr className="text-slate-500">
                        <th className="px-2 py-1.5 text-left font-medium">Ticker</th>
                        <th className="px-2 py-1.5 text-right font-medium">Delta</th>
                        <th className="px-2 py-1.5 text-right font-medium">Gamma</th>
                        <th className="px-2 py-1.5 text-right font-medium">Vega</th>
                      </tr>
                    </thead>
                    <tbody className="text-brand-900">
                      {(prod?.tickers ?? g.delta.map((_, i) => `A${i + 1}`)).map((t, i) => (
                        <tr key={i} className="border-t border-line">
                          <td className="px-2 py-1.5 font-semibold">{t}</td>
                          <td className="px-2 py-1.5 text-right tnum">
                            {g.delta[i]?.toFixed(1)}
                          </td>
                          <td className="px-2 py-1.5 text-right tnum">
                            {g.gamma[i]?.toFixed(1)}
                          </td>
                          <td className="px-2 py-1.5 text-right tnum text-neg">
                            {g.vega[i]?.toFixed(1)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <div className="flex flex-wrap gap-x-4 gap-y-1 border-t border-line px-4 py-2.5 text-2xs text-slate-400">
                  <span>
                    θ <span className="tnum text-brand-900">{g.theta.toFixed(2)}</span>
                  </span>
                  <span>
                    ρ <span className="tnum text-brand-900">{g.rho.toFixed(2)}</span>
                  </span>
                  <span>
                    carry <span className="tnum text-brand-900">{g.carry.toFixed(2)}</span>
                  </span>
                  <span>
                    corr-sens{" "}
                    <span className="tnum text-brand-900">{g.corr_sens.toFixed(2)}</span>
                  </span>
                  {g.skew_vega != null && (
                    <span>
                      skew-vega{" "}
                      <span className="tnum text-brand-900">{g.skew_vega.toFixed(1)}</span>
                    </span>
                  )}
                </div>
                {g.bucketed_vega && (
                  <div className="overflow-x-auto border-t border-line p-2">
                    <table className="w-full min-w-[360px] text-xs">
                      <thead>
                        <tr className="text-slate-500">
                          <th className="px-2 py-1.5 text-left font-medium">Vega by moneyness</th>
                          {Object.keys(g.bucketed_vega).map((k) => (
                            <th key={k} className="px-2 py-1.5 text-right font-medium tnum">
                              {k}
                            </th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        <tr className="border-t border-line">
                          <td className="px-2 py-1.5 text-slate-400">Basket</td>
                          {Object.values(g.bucketed_vega).map((v, i) => (
                            <td
                              key={i}
                              className={cn(
                                "px-2 py-1.5 text-right tnum",
                                v < 0 ? "text-neg" : "text-pos",
                              )}
                            >
                              {v.toFixed(0)}
                            </td>
                          ))}
                        </tr>
                      </tbody>
                    </table>
                  </div>
                )}
              </Card>
            )}
          </>
        )}
      </div>
    </div>
  );
}
