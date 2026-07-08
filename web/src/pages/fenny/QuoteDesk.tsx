import { useState } from "react";
import {
  Calculator,
  ChevronDown,
  LineChart,
  Loader2,
  Play,
  ShieldCheck,
  Sigma,
  Timer,
  TrendingDown,
  TrendingUp,
  Trash2,
  Wallet,
} from "lucide-react";
import { fennyApi } from "../../lib/fenny";
import { PlotlyChart } from "../../components/charts/PlotlyChart";
import { Card } from "../../components/ui/Card";
import { SectionHeader } from "../../components/ui/SectionHeader";
import { Badge } from "../../components/ui/Badge";
import { cn } from "../../lib/format";
import type { Job } from "../../types-fenny";
import { FENNY_TERMS as T } from "./glossary";
import { InfoDot } from "./InfoDot";

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
  // top-level coupon_rate / reoffer_fraction are present only on the SOLVE response;
  // on a plain QUOTE the coupon lives at pricing.coupon_rate (reads below are null-safe).
  coupon_rate?: number;
  coupon_rate_se?: number;
  reoffer_fraction?: number;
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

// A labelled control with a plain English + 中文 caption and a ⓘ plain-language tooltip.
function Field({
  label,
  cn: cnLabel,
  tip,
  children,
}: {
  label: string;
  cn?: string;
  tip?: string;
  children: React.ReactNode;
}) {
  return (
    <label className="block">
      <span className="mb-1 flex items-center gap-1">
        <span className="text-2xs font-medium text-slate-300">{label}</span>
        {cnLabel && <span className="text-[10px] text-slate-500">{cnLabel}</span>}
        {tip && <InfoDot tip={tip} />}
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
  tip,
}: {
  label: string;
  value: string;
  sub?: string;
  tone?: "pos" | "neg" | "warn";
  tip?: string;
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
      <div className="flex items-center gap-1 text-2xs uppercase tracking-wide text-slate-500">
        {label}
        {tip && <InfoDot tip={tip} />}
      </div>
      <div className={cn("mt-0.5 text-lg font-semibold tnum", t)}>{value}</div>
      {sub && <div className="mt-0.5 text-2xs text-slate-400">{sub}</div>}
    </div>
  );
}

// Plain-language one-line takeaway synthesised from the numbers (no jargon).
function verdict(coupon: number, probLoss: number, probAutocall: number): string {
  const income = coupon >= 0.1 ? "票息较高" : coupon >= 0.06 ? "票息中等" : "票息偏低";
  const risk =
    probLoss < 0.08 ? "本金风险较低" : probLoss < 0.2 ? "本金风险中等" : "本金风险偏高";
  const exit = probAutocall >= 0.5 ? "较可能提前收回" : "多半持有到期";
  return `${income}、${risk},${exit}。`;
}

// A big client-facing summary tile.
function BigTile({
  icon,
  label,
  value,
  sub,
  tone,
  tip,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
  sub?: string;
  tone?: "pos" | "neg" | "warn";
  tip?: string;
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
    <div className="rounded-xl border border-line bg-surface-2 p-3">
      <div className="mb-1 flex items-center gap-1 text-2xs text-slate-400">
        <span className="text-slate-500">{icon}</span>
        {label}
        {tip && <InfoDot tip={tip} />}
      </div>
      <div className={cn("text-2xl font-semibold tnum", t)}>{value}</div>
      {sub && <div className="mt-1 text-[11px] leading-snug text-slate-400">{sub}</div>}
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
  const [showVolCurve, setShowVolCurve] = useState(false); // per-asset skew fine-tuning
  const [showAdvInputs, setShowAdvInputs] = useState(false); // rate / correlation
  const [showAdvMetrics, setShowAdvMetrics] = useState(false); // pricing detail + greeks + fees

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
  // coupon is top-level on solve, at pricing.coupon_rate on quote; reoffer is solve-only.
  const couponVal = res?.coupon_rate ?? p?.coupon_rate ?? 0;
  const reoffer = res?.reoffer_fraction; // number on solve, undefined on quote

  return (
    <div className="grid grid-cols-1 gap-4 p-4 xl:grid-cols-[380px_1fr]">
      {/* ------------------------------------------------------------ FORM */}
      <Card className="self-start">
        <SectionHeader
          title="Build a note"
          titleCn="设计一张票据"
          icon={<Calculator size={15} />}
          right={<Badge className="bg-surface-2 text-slate-400">manual</Badge>}
        />
        <div className="space-y-3 p-4">
          {/* product type — tabs, like the reference desk */}
          <Field label={T.variant.label} cn={T.variant.cn} tip={T.variant.tip}>
            <div className="grid grid-cols-3 gap-1">
              {(["fcn", "phoenix", "snowball"] as Variant[]).map((v) => (
                <button
                  key={v}
                  type="button"
                  title={T[v].tip}
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
              <span className="flex items-center gap-1 text-2xs font-medium text-slate-300">
                {T.underlyings.label}
                <span className="text-[10px] text-slate-500">{T.underlyings.cn} · 看最差 1–3 只</span>
                <InfoDot tip={T.underlyings.tip} />
              </span>
              <button
                type="button"
                onClick={addAsset}
                disabled={assets.length >= 3}
                className="inline-flex items-center gap-0.5 rounded-md border border-line px-1.5 py-0.5 text-[10px] text-slate-400 hover:text-brand-900 disabled:opacity-40"
              >
                <TrendingUp size={11} /> 加一只
              </button>
            </div>
            <div className="space-y-2">
              {assets.map((a, i) => (
                <div key={i} className="rounded-lg border border-line bg-surface-2 p-2">
                  <div className="mb-1.5 flex items-center gap-1.5">
                    <input
                      className={cn(INPUT, "flex-1 font-semibold uppercase")}
                      value={a.ticker}
                      placeholder="股票代码 TICKER"
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
                    <Field label={T.spot.label} cn={T.spot.cn} tip={T.spot.tip}>
                      <input
                        type="number"
                        className={INPUT}
                        value={a.spot}
                        onChange={(e) => patchAsset(i, { spot: +e.target.value })}
                      />
                    </Field>
                    <Field label={T.volatility.label} cn={T.volatility.cn} tip={T.volatility.tip}>
                      <input
                        type="number"
                        step="0.01"
                        className={INPUT}
                        value={a.atm_vol}
                        onChange={(e) => patchAsset(i, { atm_vol: +e.target.value })}
                      />
                    </Field>
                    {showVolCurve && (
                      <>
                        <Field label="Skew slope" cn="下跌偏斜" tip="波动曲线的下跌斜率;一般用默认。Downside skew slope — usually leave default.">
                          <input
                            type="number"
                            step="0.05"
                            className={INPUT}
                            value={a.skew_slope}
                            onChange={(e) => patchAsset(i, { skew_slope: +e.target.value })}
                          />
                        </Field>
                        <Field label="Skew curv" cn="曲率" tip="波动曲线的弯曲度;一般用默认。Skew curvature — usually leave default.">
                          <input
                            type="number"
                            step="0.05"
                            className={INPUT}
                            value={a.skew_curv}
                            onChange={(e) => patchAsset(i, { skew_curv: +e.target.value })}
                          />
                        </Field>
                      </>
                    )}
                  </div>
                </div>
              ))}
            </div>
            <button
              type="button"
              onClick={() => setShowVolCurve((s) => !s)}
              className="mt-1 text-[10px] text-slate-500 hover:text-accent-100"
            >
              {showVolCurve ? "隐藏波动曲线微调" : "微调波动曲线(可选)"}
            </button>
          </div>

          <Field label={T.amount.label} cn={T.amount.cn} tip={T.amount.tip}>
            <input
              type="number"
              className={INPUT}
              value={notional}
              onChange={(e) => setNotional(+e.target.value)}
            />
          </Field>

          <div className="grid grid-cols-3 gap-1.5">
            <Field label={T.tradeDate.label} cn={T.tradeDate.cn} tip={T.tradeDate.tip}>
              <input
                type="date"
                className={INPUT}
                value={tradeDate}
                onChange={(e) => setTradeDate(e.target.value)}
              />
            </Field>
            <Field label={T.strikeDate.label} cn={T.strikeDate.cn} tip={T.strikeDate.tip}>
              <input
                type="date"
                className={INPUT}
                value={strikeDate}
                onChange={(e) => setStrikeDate(e.target.value)}
              />
            </Field>
            <Field label={T.maturity.label} cn={T.maturity.cn} tip={T.maturity.tip}>
              <input
                type="date"
                className={INPUT}
                value={maturity}
                onChange={(e) => setMaturity(e.target.value)}
              />
            </Field>
          </div>

          <div className="grid grid-cols-2 gap-1.5">
            <Field label={T.protection.label} cn={T.protection.cn} tip={T.protection.tip}>
              <input
                type="number"
                step="0.05"
                className={INPUT}
                value={kiBarrier}
                onChange={(e) => setKiBarrier(+e.target.value)}
              />
            </Field>
            <Field label={T.autocall.label} cn={T.autocall.cn} tip={T.autocall.tip}>
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
                <Field label={T.couponBarrier.label} cn={T.couponBarrier.cn} tip={T.couponBarrier.tip}>
                  <input
                    type="number"
                    step="0.05"
                    className={INPUT}
                    value={couponBarrier}
                    onChange={(e) => setCouponBarrier(+e.target.value)}
                  />
                </Field>
                <Field label={T.memory.label} cn={T.memory.cn} tip={T.memory.tip}>
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
                    {memory ? "开 On" : "关 Off"}
                  </button>
                </Field>
              </>
            )}
            <Field label={T.targetCoupon.label} cn={T.targetCoupon.cn} tip={T.targetCoupon.tip}>
              <input
                type="number"
                step="0.25"
                className={INPUT}
                value={targetCoupon}
                onChange={(e) => setTargetCoupon(+e.target.value)}
              />
            </Field>
          </div>

          {/* advanced inputs — risk-free rate + correlation, defaults are fine */}
          <div>
            <button
              type="button"
              onClick={() => setShowAdvInputs((s) => !s)}
              className="flex items-center gap-1 text-[10px] text-slate-500 hover:text-accent-100"
            >
              <ChevronDown size={11} className={cn("transition-transform", showAdvInputs && "rotate-180")} />
              高级设置(利率 / 相关性)
            </button>
            {showAdvInputs && (
              <div className="mt-1.5 grid grid-cols-2 gap-1.5">
                <Field label={T.riskFree.label} cn={T.riskFree.cn} tip={T.riskFree.tip}>
                  <input
                    type="number"
                    step="0.005"
                    className={INPUT}
                    value={rate}
                    onChange={(e) => setRate(+e.target.value)}
                  />
                </Field>
                {assets.length > 1 && (
                  <Field label={T.correlation.label} cn={T.correlation.cn} tip={T.correlation.tip}>
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
            )}
          </div>

          <div className="grid grid-cols-2 gap-2 pt-1">
            <button
              type="button"
              onClick={() => run("quote")}
              disabled={loading}
              title="用你填的票息给票据估值 / price the note at your coupon"
              className="inline-flex items-center justify-center gap-1.5 rounded-lg bg-accent-600 px-3 py-2 text-xs font-semibold text-white hover:bg-accent-500 disabled:opacity-50"
            >
              {loading && mode === "quote" ? (
                <Loader2 size={14} className="animate-spin" />
              ) : (
                <Play size={14} />
              )}
              查看报价
            </button>
            <button
              type="button"
              onClick={() => run("solve")}
              disabled={loading}
              title="算出当前条款下公平的票息 / solve the fair coupon for these terms"
              className="inline-flex items-center justify-center gap-1.5 rounded-lg border border-accent-500 px-3 py-2 text-xs font-semibold text-accent-100 hover:bg-surface-2 disabled:opacity-50"
            >
              {loading && mode === "solve" ? (
                <Loader2 size={14} className="animate-spin" />
              ) : (
                <Sigma size={14} />
              )}
              解出票息
            </button>
          </div>
          {loading && (
            <div className="text-2xs text-slate-400">
              计算中… <span className="text-accent-100">{stage || "pricing"}</span>
            </div>
          )}
          {error && <div className="text-2xs text-neg">出错: {error}</div>}
        </div>
      </Card>

      {/* --------------------------------------------------------- RESULTS */}
      <div className="min-w-0 space-y-4">
        {!res && !loading && (
          <Card>
            <div className="p-10 text-center text-xs text-slate-400">
              填好票据条款,点{" "}
              <span className="font-semibold text-brand-900">查看报价</span>{" "}
              就能得到一份客户看得懂的票据说明。
            </div>
          </Card>
        )}

        {res && p && (
          <>
            {/* ---------------------------------- client-facing summary card */}
            <Card>
              <SectionHeader
                title="If you buy this note"
                titleCn="如果你买入这张票据"
                icon={<ShieldCheck size={15} />}
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
              <div className="grid grid-cols-2 gap-2 p-4 lg:grid-cols-3">
                <BigTile
                  icon={<Wallet size={13} />}
                  label={`${T.coupon.cn} ${T.coupon.label}`}
                  value={pct(couponVal, 2) + " p.a."}
                  sub={isSolve ? "公平票息(模型解出)" : "你每年可收到的利息"}
                  tone="pos"
                  tip={T.coupon.tip}
                />
                <BigTile
                  icon={<ShieldCheck size={13} />}
                  label={`${T.protection.cn} Protection`}
                  value={pd ? "≥ " + pct(pd.ki, 0) : "—"}
                  sub={pd ? `最差股票不跌破 ${pct(pd.ki, 0)}(相对定价日)即保本` : undefined}
                  tip={T.protection.tip}
                />
                <BigTile
                  icon={<TrendingUp size={13} />}
                  label={`${T.chanceEarlyExit.cn}`}
                  value={pct(p.prob_autocall)}
                  sub="通常是好事:拿回本金 + 已计票息"
                  tone="pos"
                  tip={T.chanceEarlyExit.tip}
                />
                <BigTile
                  icon={<TrendingDown size={13} />}
                  label={`${T.chanceLoss.cn}`}
                  value={pct(p.prob_knock_in)}
                  sub="到期最差股票跌破保护线的估计概率"
                  tone="warn"
                  tip={T.chanceLoss.tip}
                />
                <BigTile
                  icon={<Timer size={13} />}
                  label={`${T.expectedLife.cn}`}
                  value={p.expected_life.toFixed(1) + " 年"}
                  sub="考虑提前收回后的平均存续期"
                  tip={T.expectedLife.tip}
                />
                {reoffer != null ? (
                  <BigTile
                    icon={<Wallet size={13} />}
                    label={`${T.issuePrice.cn} Issue`}
                    value={(reoffer * 100).toFixed(1) + "%"}
                    sub="认购价(占面值);100% = 平价"
                    tip={T.issuePrice.tip}
                  />
                ) : (
                  <BigTile
                    icon={<Wallet size={13} />}
                    label={`${T.fairValue.cn} Fair value`}
                    value={p.price_pct.toFixed(1) + "%"}
                    sub="模型公平价值(占面值);100% = 平价"
                    tip={T.fairValue.tip}
                  />
                )}
              </div>
              <div className="border-t border-line px-4 py-3 text-xs text-slate-300">
                <span className="font-semibold text-brand-900">一句话:</span>{" "}
                {verdict(couponVal, p.prob_knock_in, p.prob_autocall)}
                <span className="ml-1 text-slate-500">
                  指示性报价,最终条款以成交日为准。
                </span>
              </div>
            </Card>

            {/* payoff diagram */}
            {pd && (
              <Card>
                <SectionHeader
                  title="What you get back"
                  titleCn="到期能拿回多少"
                  icon={<LineChart size={15} />}
                  right={
                    <span className="flex items-center gap-1 text-2xs text-slate-400">
                      保护线 {pct(pd.ki, 0)} · 行权价 {pct(pd.strike, 0)}
                      <InfoDot tip="横轴 = 到期时最差股票相对定价日的价格;竖线是保护线与行权价。Payoff vs the worst stock at maturity." />
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
                          "最差股票 %{x:.0%}<br>拿回 %{y:,.0f}<extra></extra>",
                      },
                    ]}
                    layout={{
                      xaxis: {
                        title: { text: "最差股票到期价(相对定价日 %)", font: { size: 10 } },
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
                <SectionHeader
                  title="If the market moves"
                  titleCn="不同行情下的结果"
                  right={
                    <InfoDot tip="每列是标的涨跌情形;下面三行是票据价值、提前收回概率、本金亏损概率。Each column is a spot move." />
                  }
                />
                <div className="overflow-x-auto p-2">
                  <table className="w-full min-w-[420px] text-xs">
                    <thead>
                      <tr className="text-slate-500">
                        <th className="px-2 py-1.5 text-left font-medium">标的涨跌 Spot move</th>
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
                        <td className="px-2 py-1.5 text-slate-400">票据价值(占面值)</td>
                        {res.scenario_table.map((r, i) => (
                          <td key={i} className="px-2 py-1.5 text-right tnum">
                            {r.price_pct.toFixed(1)}
                          </td>
                        ))}
                      </tr>
                      <tr className="border-t border-line">
                        <td className="px-2 py-1.5 text-slate-400">提前收回概率</td>
                        {res.scenario_table.map((r, i) => (
                          <td key={i} className="px-2 py-1.5 text-right tnum text-pos">
                            {(r.prob_autocall * 100).toFixed(0)}%
                          </td>
                        ))}
                      </tr>
                      <tr className="border-t border-line">
                        <td className="px-2 py-1.5 text-slate-400">本金亏损概率</td>
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

            {/* -------------------------- advanced / professional metrics (collapsed) */}
            <Card>
              <button
                type="button"
                onClick={() => setShowAdvMetrics((s) => !s)}
                className="flex w-full items-center gap-2 px-4 py-3 text-left"
              >
                <ChevronDown
                  size={15}
                  className={cn("text-slate-400 transition-transform", showAdvMetrics && "rotate-180")}
                />
                <span className="text-sm font-semibold text-brand-900">专业指标</span>
                <span className="text-2xs text-slate-500">Advanced · 定价 / 希腊值 / 费用</span>
              </button>

              {showAdvMetrics && (
                <div className="space-y-4 border-t border-line p-4">
                  {/* full pricing detail */}
                  <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                    <Stat
                      label={isSolve ? "Fair coupon" : "Coupon"}
                      value={pct(couponVal, 2) + " p.a."}
                      sub={
                        isSolve && res.coupon_rate_se
                          ? "± " + (res.coupon_rate_se * 100).toFixed(3) + "%"
                          : undefined
                      }
                      tone="pos"
                    />
                    <Stat label="Fair value" value={p.price_pct.toFixed(2) + "%"} sub="of par" tip={T.fairValue.tip} />
                    <Stat label="PV" value={money(p.pv, ccy)} sub={"± " + money(p.pv_se, ccy)} />
                    <Stat
                      label="Reoffer"
                      value={reoffer != null ? (reoffer * 100).toFixed(2) + "%" : "—"}
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
                  {res.fees && (
                    <div className="flex flex-wrap gap-x-4 gap-y-1 border-t border-line pt-2.5 text-2xs text-slate-400">
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

                  {/* greeks */}
                  {g && (
                    <div className="border-t border-line pt-3">
                      <div className="mb-2 flex items-center gap-1.5 text-xs font-semibold text-brand-900">
                        <Sigma size={14} /> Greeks &amp; risk <span className="text-2xs font-normal text-slate-500">希腊值</span>
                      </div>
                      <div className="overflow-x-auto">
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
                                <td className="px-2 py-1.5 text-right tnum">{g.delta[i]?.toFixed(1)}</td>
                                <td className="px-2 py-1.5 text-right tnum">{g.gamma[i]?.toFixed(1)}</td>
                                <td className="px-2 py-1.5 text-right tnum text-neg">{g.vega[i]?.toFixed(1)}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                      <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-2xs text-slate-400">
                        <span>θ <span className="tnum text-brand-900">{g.theta.toFixed(2)}</span></span>
                        <span>ρ <span className="tnum text-brand-900">{g.rho.toFixed(2)}</span></span>
                        <span>carry <span className="tnum text-brand-900">{g.carry.toFixed(2)}</span></span>
                        <span>corr-sens <span className="tnum text-brand-900">{g.corr_sens.toFixed(2)}</span></span>
                        {g.skew_vega != null && (
                          <span>skew-vega <span className="tnum text-brand-900">{g.skew_vega.toFixed(1)}</span></span>
                        )}
                      </div>
                      {g.bucketed_vega && (
                        <div className="mt-2 overflow-x-auto">
                          <table className="w-full min-w-[360px] text-xs">
                            <thead>
                              <tr className="text-slate-500">
                                <th className="px-2 py-1.5 text-left font-medium">Vega by moneyness</th>
                                {Object.keys(g.bucketed_vega).map((k) => (
                                  <th key={k} className="px-2 py-1.5 text-right font-medium tnum">{k}</th>
                                ))}
                              </tr>
                            </thead>
                            <tbody>
                              <tr className="border-t border-line">
                                <td className="px-2 py-1.5 text-slate-400">Basket</td>
                                {Object.values(g.bucketed_vega).map((v, i) => (
                                  <td key={i} className={cn("px-2 py-1.5 text-right tnum", v < 0 ? "text-neg" : "text-pos")}>
                                    {v.toFixed(0)}
                                  </td>
                                ))}
                              </tr>
                            </tbody>
                          </table>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              )}
            </Card>
          </>
        )}
      </div>
    </div>
  );
}
