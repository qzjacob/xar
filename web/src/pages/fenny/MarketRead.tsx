import { useState } from "react";
import { Activity, CalendarClock, Gauge, LineChart, Plus, Sparkles, X } from "lucide-react";
import { fennyApi } from "../../lib/fenny";
import { PlotlyChart } from "../../components/charts/PlotlyChart";
import { Card } from "../../components/ui/Card";
import { SectionHeader } from "../../components/ui/SectionHeader";
import { Badge } from "../../components/ui/Badge";
import { cn } from "../../lib/format";
import type { Job } from "../../types-fenny";
import { FENNY_TERMS as T } from "./glossary";
import { InfoDot } from "./InfoDot";

// ── result shape (from /jobs/market_read → build_market_read) ─────────────────
interface IndexMetric {
  ticker: string;
  spot: number;
  atm_1m: number;
  atm_3m: number;
  atm_1y: number;
  term_slope: number;
  put_skew_3m: number;
  realized_21d?: number;
  iv_rv_gap?: number;
  resolved_as?: string; // data proxy actually used (e.g. QQQ → ^IXIC index)
}
interface Metrics {
  per_index: IndexMetric[];
  vol_level: number;
  skew: number;
  term_slope: number;
  vix_proxy: number;
  rate: number;
  vol_basis?: string; // "realized" (auto) | "implied"
}
interface Suit {
  score: number;
  label: string;
  drivers: string[];
}
interface TrendSample {
  month: string;
  spot: number;
  rv21: number;
}
interface Trend {
  per_index: { ticker: string; samples: TrendSample[] }[];
  vol_now: number;
  vol_mom: number;
  px_3m: number;
  months: string[];
}
interface Timing {
  stance: "enter_now" | "wait" | "neutral";
  label: string;
  score: number;
  drivers: string[];
}
interface MarketReadResult {
  metrics: Metrics;
  suitability: Record<string, Suit>;
  trend: Trend | null;
  timing: Record<string, Timing> | null;
  narrative: string;
  narrative_source: string;
  indices: string[];
  source: string;
  unresolved?: string[]; // requested indices the data source could not serve
}

const INPUT = "rounded-lg border border-line bg-surface-2 px-2 py-1.5 text-xs text-brand-900";

function labelTone(label: string): string {
  if (label === "favorable") return "bg-pos/10 text-pos";
  if (label === "unfavorable") return "bg-neg/10 text-neg";
  return "bg-warn-100/10 text-warn-100";
}

function stanceTone(stance: string): string {
  if (stance === "enter_now") return "bg-pos/10 text-pos";
  if (stance === "wait") return "bg-neg/10 text-neg";
  return "bg-warn-100/10 text-warn-100";
}

function pct(x: number, d = 1): string {
  return `${(x * 100).toFixed(d)}%`;
}

function Metric({
  label,
  cn: cnLabel,
  value,
  hint,
  tip,
}: {
  label: string;
  cn?: string;
  value: string;
  hint?: string;
  tip?: string;
}) {
  return (
    <div className="rounded-lg border border-line bg-surface-2 px-3 py-2">
      <div className="flex items-center gap-1 text-2xs uppercase tracking-wide text-brand-200">
        {label}
        {cnLabel && <span className="normal-case text-brand-500">{cnLabel}</span>}
        {tip && <InfoDot tip={tip} />}
      </div>
      <div className="mt-0.5 text-lg font-semibold text-brand-900 tnum">{value}</div>
      {hint && <div className="text-2xs text-brand-500">{hint}</div>}
    </div>
  );
}

export function MarketRead() {
  // 全自动取数:指数 spot / 实际波动率 / 利率全部实时抓取(FMP),无需手工输入。
  const [tickers, setTickers] = useState<string[]>(["SPY", "QQQ"]);
  const [draft, setDraft] = useState("");
  const [lang, setLang] = useState<"en" | "zh">("zh");
  const [loading, setLoading] = useState(false);
  const [stage, setStage] = useState<string>("");
  const [err, setErr] = useState<string | null>(null);
  const [res, setRes] = useState<MarketReadResult | null>(null);

  function addTicker() {
    const t = draft.trim().toUpperCase();
    if (t && !tickers.includes(t) && tickers.length < 6) setTickers((a) => [...a, t]);
    setDraft("");
  }
  function removeTicker(t: string) {
    setTickers((a) => (a.length > 1 ? a.filter((x) => x !== t) : a));
  }

  async function run() {
    setLoading(true);
    setErr(null);
    setStage("fetching real market data");
    try {
      const body = { indices: tickers, source: "auto" as const, lang };
      const out = (await fennyApi.marketRead(body, (j: Job) => setStage(j.stage || j.status))) as unknown as MarketReadResult;
      setRes(out);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
      setStage("");
    }
  }

  const m = res?.metrics;
  const realized = m?.vol_basis === "realized";
  const chart = m
    ? [
        { x: m.per_index.map((p) => p.ticker), y: m.per_index.map((p) => p.atm_1m * 100), type: "bar", name: "1M" },
        { x: m.per_index.map((p) => p.ticker), y: m.per_index.map((p) => p.atm_3m * 100), type: "bar", name: "3M" },
        { x: m.per_index.map((p) => p.ticker), y: m.per_index.map((p) => p.atm_1y * 100), type: "bar", name: "1Y" },
      ]
    : [];
  const trendChart = res?.trend
    ? res.trend.per_index.map((p) => ({
        x: p.samples.map((s) => s.month),
        y: p.samples.map((s) => s.rv21 * 100),
        type: "scatter",
        mode: "lines+markers",
        name: p.ticker,
      }))
    : [];

  return (
    <div className="flex flex-col gap-4 p-4 sm:p-6">
      {/* ── header + zero-input controls ─────────────────────────── */}
      <Card>
        <div className="flex flex-wrap items-center gap-3 p-4">
          <div className="mr-auto">
            <h1 className="text-base font-semibold text-brand-900">Market Read · 市场解读</h1>
            <p className="text-xs text-brand-500">
              实时抓取指数真实数据(现价·实际波动率·国债利率),生成适配度与
              <span className="text-accent-100">月度择时</span>解读 — 无需手工输入。
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-1.5">
            {tickers.map((t) => (
              <span key={t} className="flex items-center gap-1 rounded-lg border border-line bg-surface-2 px-2 py-1 text-xs text-brand-900">
                {t}
                <button onClick={() => removeTicker(t)} className="text-brand-200 hover:text-neg" aria-label={`remove ${t}`}>
                  <X size={11} />
                </button>
              </span>
            ))}
            <input
              className={cn(INPUT, "w-20 uppercase")}
              placeholder="+指数"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && addTicker()}
              onBlur={addTicker}
            />
            <button onClick={addTicker} className="text-brand-200 hover:text-accent-100" aria-label="add index">
              <Plus size={14} />
            </button>
          </div>
          <div className="flex overflow-hidden rounded-lg border border-line">
            {(["zh", "en"] as const).map((l) => (
              <button
                key={l}
                onClick={() => setLang(l)}
                className={cn(
                  "px-2.5 py-1 text-2xs font-medium",
                  lang === l ? "bg-accent-600 text-white" : "bg-surface-2 text-brand-500",
                )}
              >
                {l === "en" ? "EN" : "中文"}
              </button>
            ))}
          </div>
          <button
            onClick={run}
            disabled={loading}
            className="rounded-lg bg-accent-600 px-3 py-1.5 text-xs font-medium text-white disabled:opacity-50"
          >
            {loading ? "解读中…" : "解读市场"}
          </button>
        </div>
      </Card>

      {loading && (
        <div className="text-xs text-brand-500">
          抓取实时数据并解读… <span className="text-accent-100">{stage}</span>
        </div>
      )}
      {err && <div className="text-xs text-neg">Error: {err}</div>}
      {res?.unresolved && res.unresolved.length > 0 && (
        <div className="text-xs text-warn-100">
          ⚠ {res.unresolved.join("、")} 无可用实时数据,已从本次解读中剔除(其余为真实数据)
        </div>
      )}

      {res && m && (
        <>
          {/* ── narrative first — the plain-language market read (client-facing) ── */}
          <Card>
            <SectionHeader
              title="Market Read"
              titleCn="市场解读"
              icon={<Sparkles size={15} />}
              right={
                <Badge
                  className={res.narrative_source === "llm" ? "bg-accent-600/15 text-accent-100" : "bg-surface-2 text-brand-500"}
                  title={res.narrative_source === "llm" ? "AI 撰写(择优模型)" : "无 LLM 时的确定性模板"}
                >
                  {res.narrative_source === "llm" ? "AI 解读" : "模板"}
                </Badge>
              }
            />
            <p className="whitespace-pre-wrap p-4 text-sm leading-relaxed text-brand-900">{res.narrative}</p>
          </Card>

          {/* ── 择时 — per product family timing from monthly trends ── */}
          {res.timing && (
            <Card>
              <SectionHeader
                title="Timing"
                titleCn="择时观点(月度趋势)"
                icon={<CalendarClock size={15} />}
                right={
                  res.trend && (
                    <span className="text-2xs text-brand-200 tnum">
                      波动率环比 {(res.trend.vol_mom * 100).toFixed(1)}pt · 近3月 {(res.trend.px_3m * 100).toFixed(1)}%
                    </span>
                  )
                }
              />
              <div className="grid grid-cols-1 gap-3 p-4 sm:grid-cols-2 lg:grid-cols-5">
                {Object.entries(res.timing).map(([fam, t]) => (
                  <div key={fam} className="rounded-lg border border-line bg-surface-2 p-3">
                    <div className="flex items-center justify-between">
                      <span className="text-sm font-semibold text-brand-900">{fam}</span>
                      <Badge className={stanceTone(t.stance)}>{t.label}</Badge>
                    </div>
                    <ul className="mt-2 space-y-1">
                      {t.drivers.map((d, i) => (
                        <li key={i} className="text-2xs leading-snug text-brand-500">· {d}</li>
                      ))}
                    </ul>
                  </div>
                ))}
              </div>
            </Card>
          )}

          {/* ── monthly trend: realized vol by month + table ── */}
          {res.trend && (
            <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
              <Card>
                <SectionHeader title="Realized Vol by Month (%)" titleCn="月度实际波动率" icon={<LineChart size={15} />} />
                <div className="p-2">
                  <PlotlyChart data={trendChart} height={240} layout={{ yaxis: { ticksuffix: "%" } }} />
                </div>
              </Card>
              <Card>
                <SectionHeader title="Month-End Levels" titleCn="月末数据" icon={<CalendarClock size={15} />} />
                <div className="overflow-x-auto p-4">
                  <table className="w-full min-w-[380px] text-xs tnum">
                    <thead>
                      <tr className="text-right text-2xs uppercase tracking-wide text-brand-200">
                        <th className="pb-2 text-left font-medium">Month</th>
                        {res.trend.per_index.map((p) => (
                          <th key={p.ticker} className="pb-2 font-medium" colSpan={2}>{p.ticker} (px / vol)</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {res.trend.months.map((mo) => (
                        <tr key={mo} className="border-t border-line text-right">
                          <td className="py-1.5 text-left font-medium text-brand-900">{mo}</td>
                          {res.trend!.per_index.map((p) => {
                            const s = p.samples.find((x) => x.month === mo);
                            return (
                              <td key={p.ticker} className="py-1.5 text-brand-500" colSpan={2}>
                                {s ? `${s.spot.toFixed(0)} / ${(s.rv21 * 100).toFixed(0)}%` : "—"}
                              </td>
                            );
                          })}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </Card>
            </div>
          )}

          {/* ── suitability — which note fits, with plain drivers ── */}
          <Card>
            <SectionHeader
              title="Which note fits now"
              titleCn="现在适合哪种票据"
              icon={<Gauge size={15} />}
              right={<InfoDot tip={T.suitability.tip} />}
            />
            <div className="grid grid-cols-1 gap-3 p-4 sm:grid-cols-2 lg:grid-cols-3">
              {Object.entries(res.suitability).map(([fam, s]) => (
                <div key={fam} className="rounded-lg border border-line bg-surface-2 p-3">
                  <div className="flex items-center justify-between">
                    <span className="text-sm font-semibold text-brand-900">{fam}</span>
                    <Badge className={labelTone(s.label)}>
                      {s.label === "favorable" ? "适合" : s.label === "unfavorable" ? "不适合" : "一般"}
                    </Badge>
                  </div>
                  <div className="mt-1 flex items-baseline gap-1">
                    <span className="text-2xl font-semibold text-brand-900 tnum">{s.score}</span>
                    <span className="text-2xs text-brand-200">/100</span>
                  </div>
                  <div className="mt-1.5 h-1.5 w-full overflow-hidden rounded-full bg-canvas">
                    <div
                      className={cn(
                        "h-full rounded-full",
                        s.label === "favorable" ? "bg-pos" : s.label === "unfavorable" ? "bg-neg" : "bg-warn-100",
                      )}
                      style={{ width: `${s.score}%` }}
                    />
                  </div>
                  <ul className="mt-2 space-y-1">
                    {s.drivers.map((d, i) => (
                      <li key={i} className="text-2xs leading-snug text-brand-500">· {d}</li>
                    ))}
                  </ul>
                </div>
              ))}
            </div>
          </Card>

          {/* ── headline metrics (the technical read, plain-labelled) ── */}
          <Card>
            <SectionHeader
              title="Market Metrics"
              titleCn="市场指标(技术面)"
              icon={<Activity size={15} />}
              right={
                <Badge className="bg-surface-2 text-brand-500" title={realized ? "无期权隐含波动率数据源时,以历史实际波动率为诚实代理" : undefined}>
                  {realized ? "实际波动率(历史)" : `source · ${res.source}`}
                </Badge>
              }
            />
            <div className="grid grid-cols-2 gap-2 p-4 sm:grid-cols-3 lg:grid-cols-5">
              <Metric label={T.volLevel.label} cn={T.volLevel.cn} value={pct(m.vol_level, 0)} hint="avg 3M" tip={T.volLevel.tip} />
              <Metric label={T.putSkew.label} cn={T.putSkew.cn} value={`+${(m.skew * 100).toFixed(1)}pt`} hint="at 90%" tip={T.putSkew.tip} />
              <Metric label={T.termSlope.label} cn={T.termSlope.cn} value={`${(m.term_slope * 100).toFixed(1)}pt`} hint="1Y−1M" tip={T.termSlope.tip} />
              <Metric label={T.vixProxy.label} cn={T.vixProxy.cn} value={m.vix_proxy.toFixed(1)} hint="SPY 30D" tip={T.vixProxy.tip} />
              <Metric label={T.riskFree.label} cn={T.riskFree.cn} value={pct(m.rate, 2)} hint="1Y treasury·实时" tip={T.riskFree.tip} />
            </div>
          </Card>

          {/* ── per-index table + chart ──────────────────────── */}
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            <Card>
              <SectionHeader title="Per-Index Surface" titleCn="各指数波动率" icon={<LineChart size={15} />} />
              <div className="overflow-x-auto p-4">
                <table className="w-full min-w-[420px] text-xs tnum">
                  <thead>
                    <tr className="text-right text-2xs uppercase tracking-wide text-brand-200">
                      <th className="pb-2 text-left font-medium">Index</th>
                      <th className="pb-2 font-medium">Spot</th>
                      <th className="pb-2 font-medium">1M</th>
                      <th className="pb-2 font-medium">3M</th>
                      <th className="pb-2 font-medium">1Y</th>
                      <th className="pb-2 font-medium">Term</th>
                      <th className="pb-2 font-medium">Skew</th>
                    </tr>
                  </thead>
                  <tbody>
                    {m.per_index.map((p) => (
                      <tr key={p.ticker} className="border-t border-line text-right">
                        <td className="py-1.5 text-left font-medium text-brand-900">
                          {p.ticker}
                          {p.resolved_as && (
                            <span className="ml-1 text-2xs text-brand-200" title="数据代理:该代码在数据源被限制,使用其对应指数的真实数据">
                              →{p.resolved_as}
                            </span>
                          )}
                        </td>
                        <td className="py-1.5 text-brand-500">{p.spot.toFixed(2)}</td>
                        <td className="py-1.5 text-brand-900">{pct(p.atm_1m, 1)}</td>
                        <td className="py-1.5 text-brand-900">{pct(p.atm_3m, 1)}</td>
                        <td className="py-1.5 text-brand-900">{pct(p.atm_1y, 1)}</td>
                        <td className={cn("py-1.5", p.term_slope >= 0 ? "text-pos" : "text-neg")}>
                          {(p.term_slope * 100).toFixed(1)}
                        </td>
                        <td className="py-1.5 text-warn-100">+{(p.put_skew_3m * 100).toFixed(1)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Card>

            <Card>
              <SectionHeader title="Vol Term (%)" titleCn="波动率期限" icon={<LineChart size={15} />} />
              <div className="p-2">
                <PlotlyChart data={chart} height={260} layout={{ barmode: "group", yaxis: { ticksuffix: "%" } }} />
              </div>
            </Card>
          </div>
        </>
      )}
    </div>
  );
}
