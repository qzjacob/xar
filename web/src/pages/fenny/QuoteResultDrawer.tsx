import { useState } from "react";
import { ChevronDown, LineChart, ShieldCheck, Sigma, Timer, TrendingDown, TrendingUp, Wallet } from "lucide-react";
import { CHART, PlotlyChart } from "../../components/charts/PlotlyChart";
import { cn } from "../../lib/format";
import { InfoDot } from "./InfoDot";

// Per-row expandable result (the client-facing FN-4 summary, parameterised by one row's result).
// Shown under a grid row when its quote is priced. Numbers only ever come from the backend result.

export interface Pricing {
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
export interface Payoff { worst_of: number[]; redemption: number[]; ki: number; strike: number }
export interface ScenarioRow { shock: number; price_pct: number; prob_autocall: number; prob_knock_in: number }
export interface Greeks {
  delta: number[]; gamma: number[]; vega: number[]; theta: number; rho: number;
  carry: number; corr_sens: number; skew_vega?: number; bucketed_vega?: Record<string, number>;
}
export interface QuoteResult {
  coupon_rate?: number;
  coupon_rate_se?: number;
  reoffer_fraction?: number;
  infeasible?: boolean;        // fair coupon would be < 0 (floored to 0)
  solved_strike?: number;      // fraction of fixing, when Solve For = Strike
  strike_bracketed?: boolean;  // false = the solved strike hit the [50%,120%] bound
  data_note?: string;          // set client-side when some names couldn't resolve to live data
  pricing: Pricing;
  fees?: Record<string, number>;
  payoff_diagram: Payoff;
  scenario_table: ScenarioRow[] | null;
  greeks: Greeks | null;
  product: { variant: string; currency: string; tickers: string[] };
}

type Tone = "pos" | "neg" | "warn";
function toneClass(tone?: Tone): string {
  return tone === "pos" ? "text-pos" : tone === "neg" ? "text-neg" : tone === "warn" ? "text-warn-100" : "text-brand-900";
}
function money(n: number, ccy = "USD"): string {
  const sym = ccy === "USD" ? "$" : ccy === "EUR" ? "€" : ccy === "GBP" ? "£" : ccy === "HKD" ? "HK$" : "";
  return sym + n.toLocaleString(undefined, { maximumFractionDigits: 0 });
}
function pct(n: number, d = 1): string {
  return (n * 100).toFixed(d) + "%";
}
function verdict(coupon: number, touchProb: number, probAutocall: number): string {
  const income = coupon >= 0.1 ? "票息较高" : coupon >= 0.06 ? "票息中等" : "票息偏低";
  const risk = touchProb < 0.1 ? "很少触及保护线" : touchProb < 0.25 ? "偶尔触及保护线" : "较易触及保护线";
  const exit = probAutocall >= 0.5 ? "较可能提前收回" : "多半持有到期";
  return `${income}、${risk},${exit}。`;
}

function BigTile({ icon, label, value, sub, tone, tip }: {
  icon: React.ReactNode; label: string; value: string; sub?: string; tone?: Tone; tip?: string;
}) {
  return (
    <div className="rounded-xl border border-line bg-surface-2 p-3">
      <div className="mb-1 flex items-center gap-1 text-2xs text-brand-500">
        <span className="text-brand-200">{icon}</span>
        {label}
        {tip && <InfoDot tip={tip} />}
      </div>
      <div className={cn("text-xl font-semibold tnum", toneClass(tone))}>{value}</div>
      {sub && <div className="mt-1 text-[11px] leading-snug text-brand-500">{sub}</div>}
    </div>
  );
}

export function QuoteResultDrawer({ result, ccy, variant, barrierNone }: {
  result: QuoteResult; ccy: string; variant: string; barrierNone?: boolean;
}) {
  const [adv, setAdv] = useState(false);
  const p = result.pricing;
  const pd = result.payoff_diagram;
  const g = result.greeks;
  const couponVal = result.coupon_rate ?? p.coupon_rate ?? 0;
  const reoffer = result.reoffer_fraction;
  const conditional = variant !== "fcn"; // phoenix/snowball coupons are conditional
  // Barrier NONE = KI sits at the strike (no buffer): a breach at maturity IS a loss.
  const noKi = barrierNone || !pd || pd.ki <= 0;

  return (
    <div className="space-y-3 border-t border-line bg-surface/40 p-3">
      {/* client-facing tiles */}
      <div className="grid grid-cols-2 gap-2 lg:grid-cols-3">
        <BigTile icon={<Wallet size={13} />} label="票息 Coupon" value={pct(couponVal, 2) + " p.a."}
          sub={conditional ? "达标时才派发的有条件票息" : "每年固定利息"} tone="pos"
          tip="你每年可收到的利息(年化)。" />
        <BigTile icon={<ShieldCheck size={13} />} label="本金保护线 Protection"
          value={noKi ? "行权价 " + pct(pd?.strike ?? 1, 0) : "≥ " + pct(pd.ki, 0)}
          sub={noKi ? "无独立敲入线,按行权价结算下行" : `最差股票不跌破 ${pct(pd.ki, 0)} 即保本`}
          tip="到期最差股票只要不跌破此线即保本;跌破才按跌幅承受损失。越低越安全。" />
        <BigTile icon={<TrendingUp size={13} />} label="提前收回概率" value={pct(p.prob_autocall)}
          sub="通常是好事:拿回本金 + 已计票息" tone="pos"
          tip="票据在到期前被提前收回(敲出)的估计概率。" />
        <BigTile icon={<TrendingDown size={13} />} label={noKi ? "本金亏损概率" : "触及保护线概率"}
          value={pct(p.prob_knock_in)} tone="warn"
          sub={noKi ? "到期最差股票低于行权价的概率" : "触及后到期仍低于行权价才亏损"}
          tip="最差股票触及保护线的概率;触及不等于一定亏损。" />
        <BigTile icon={<Timer size={13} />} label="预计存续期" value={p.expected_life.toFixed(1) + " 年"}
          sub="考虑提前收回后的平均存续" tip="平均存续年数。" />
        <BigTile icon={<Wallet size={13} />} label="公平价值 Fair value" value={p.price_pct.toFixed(1) + "%"}
          sub={reoffer != null ? `占面值;发行价约 ${(reoffer * 100).toFixed(1)}%` : "占面值;100% = 平价"}
          tip="模型算出的票据当前价值,占面值%。" />
      </div>
      <div className="text-xs text-brand-700">
        <span className="font-semibold text-brand-900">一句话:</span>{" "}
        {verdict(couponVal, p.prob_knock_in, p.prob_autocall)}
        <span className="ml-1 text-brand-200">指示性报价,最终条款以成交日为准。</span>
      </div>

      {/* payoff + scenario side by side */}
      <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
        {pd && (
          <div className="rounded-lg border border-line bg-surface-2 p-2">
            <div className="mb-1 flex items-center gap-1 px-1 text-2xs text-brand-500">
              <LineChart size={12} /> 到期能拿回多少
              <InfoDot tip="横轴 = 到期最差股票相对定价日的价格。" />
            </div>
            <PlotlyChart height={220}
              data={[{
                x: pd.worst_of, y: pd.redemption, type: "scatter", mode: "lines",
                line: { color: CHART.accent, width: 2.2, shape: "spline" }, fill: "tozeroy",
                fillcolor: "rgba(129,140,248,0.08)",
                hovertemplate: "最差股票 %{x:.0%}<br>拿回 %{y:,.0f}<extra></extra>",
              }]}
              layout={{
                margin: { l: 40, r: 8, t: 6, b: 30 },
                xaxis: { title: { text: "最差股票到期价(相对定价日 %)", font: { size: 9 } }, tickformat: ".0%" },
                yaxis: { tickformat: ",.2s", tickprefix: ccy === "USD" ? "$" : "" },
                shapes: [
                  ...(pd.ki > 0 ? [{ type: "line" as const, x0: pd.ki, x1: pd.ki, y0: 0, y1: 1, yref: "paper" as const, line: { color: "#f46060", dash: "dot", width: 1.2 } }] : []),
                  { type: "line", x0: pd.strike, x1: pd.strike, y0: 0, y1: 1, yref: "paper", line: { color: "#7c9cf5", dash: "dot", width: 1 } },
                ],
              }} />
          </div>
        )}
        {result.scenario_table && result.scenario_table.length > 0 && (
          <div className="overflow-x-auto rounded-lg border border-line bg-surface-2 p-2">
            <div className="mb-1 px-1 text-2xs text-brand-500">不同行情下的结果</div>
            <table className="w-full min-w-[360px] text-xs">
              <thead>
                <tr className="text-brand-200">
                  <th className="px-1.5 py-1 text-left font-medium">标的涨跌</th>
                  {result.scenario_table.map((r, i) => (
                    <th key={i} className="px-1.5 py-1 text-right font-medium tnum">{r.shock > 0 ? "+" : ""}{(r.shock * 100).toFixed(0)}%</th>
                  ))}
                </tr>
              </thead>
              <tbody className="text-brand-900">
                <tr className="border-t border-line">
                  <td className="px-1.5 py-1 text-brand-500">票据价值%</td>
                  {result.scenario_table.map((r, i) => (<td key={i} className="px-1.5 py-1 text-right tnum">{r.price_pct.toFixed(1)}</td>))}
                </tr>
                <tr className="border-t border-line">
                  <td className="px-1.5 py-1 text-brand-500">提前收回</td>
                  {result.scenario_table.map((r, i) => (<td key={i} className="px-1.5 py-1 text-right tnum text-pos">{(r.prob_autocall * 100).toFixed(0)}%</td>))}
                </tr>
                <tr className="border-t border-line">
                  <td className="px-1.5 py-1 text-brand-500">触及保护线</td>
                  {result.scenario_table.map((r, i) => (<td key={i} className="px-1.5 py-1 text-right tnum text-neg">{(r.prob_knock_in * 100).toFixed(0)}%</td>))}
                </tr>
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* advanced / greeks */}
      {g && (
        <div className="rounded-lg border border-line bg-surface-2">
          <button type="button" onClick={() => setAdv((s) => !s)} className="flex w-full items-center gap-2 px-3 py-2 text-left">
            <ChevronDown size={14} className={cn("text-brand-500 transition-transform", adv && "rotate-180")} />
            <span className="text-xs font-semibold text-brand-900">专业指标</span>
            <span className="text-2xs text-brand-200">Advanced · 定价 / 希腊值 / 费用</span>
          </button>
          {adv && (
            <div className="space-y-2 border-t border-line p-3 text-2xs text-brand-500">
              <div className="flex flex-wrap gap-x-4 gap-y-1">
                <span>PV <span className="tnum text-brand-900">{money(p.pv, ccy)}</span> ± {money(p.pv_se, ccy)}</span>
                <span>公平价值 <span className="tnum text-brand-900">{p.price_pct.toFixed(2)}%</span></span>
                {reoffer != null && <span>发行价 <span className="tnum text-brand-900">{(reoffer * 100).toFixed(2)}%</span></span>}
                <span className="text-brand-200">{p.n_paths.toLocaleString()} paths · {p.method}</span>
              </div>
              <div className="overflow-x-auto">
                <table className="w-full min-w-[320px]">
                  <thead><tr className="text-brand-200">
                    <th className="px-1.5 py-1 text-left font-medium">Ticker</th>
                    <th className="px-1.5 py-1 text-right font-medium">Delta</th>
                    <th className="px-1.5 py-1 text-right font-medium">Gamma</th>
                    <th className="px-1.5 py-1 text-right font-medium">Vega</th>
                  </tr></thead>
                  <tbody className="text-brand-900">
                    {(result.product?.tickers ?? g.delta.map((_, i) => `A${i + 1}`)).map((t, i) => (
                      <tr key={i} className="border-t border-line">
                        <td className="px-1.5 py-1 font-semibold">{t}</td>
                        <td className="px-1.5 py-1 text-right tnum">{g.delta[i]?.toFixed(1)}</td>
                        <td className="px-1.5 py-1 text-right tnum">{g.gamma[i]?.toFixed(1)}</td>
                        <td className="px-1.5 py-1 text-right tnum text-neg">{g.vega[i]?.toFixed(1)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div className="flex flex-wrap gap-x-4 gap-y-1">
                <span><Sigma size={10} className="inline" /> θ <span className="tnum text-brand-900">{g.theta.toFixed(2)}</span></span>
                <span>ρ <span className="tnum text-brand-900">{g.rho.toFixed(2)}</span></span>
                <span>carry <span className="tnum text-brand-900">{g.carry.toFixed(2)}</span></span>
                <span>corr-sens <span className="tnum text-brand-900">{g.corr_sens.toFixed(2)}</span></span>
                {g.skew_vega != null && <span>skew-vega <span className="tnum text-brand-900">{g.skew_vega.toFixed(1)}</span></span>}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
