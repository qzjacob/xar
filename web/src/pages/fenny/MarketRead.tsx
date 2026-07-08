import { useState } from "react";
import { Activity, Gauge, LineChart, Plus, Sparkles, Trash2 } from "lucide-react";
import { fennyApi } from "../../lib/fenny";
import { PlotlyChart } from "../../components/charts/PlotlyChart";
import { Card } from "../../components/ui/Card";
import { SectionHeader } from "../../components/ui/SectionHeader";
import { Badge } from "../../components/ui/Badge";
import { cn } from "../../lib/format";
import type { Job, AssetMarketInput } from "../../types-fenny";
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
}
interface Metrics {
  per_index: IndexMetric[];
  vol_level: number;
  skew: number;
  term_slope: number;
  vix_proxy: number;
  rate: number;
}
interface Suit {
  score: number;
  label: string;
  drivers: string[];
}
interface MarketReadResult {
  metrics: Metrics;
  suitability: Record<string, Suit>;
  narrative: string;
  narrative_source: string;
  indices: string[];
  source: string;
}

const INPUT = "w-full rounded-lg border border-line bg-surface-2 px-2 py-1.5 text-xs text-brand-900 tnum";

const DEFAULT_ASSETS: AssetMarketInput[] = [
  { ticker: "SPY", spot: 560, atm_vol: 0.16, skew_slope: -0.6, skew_curv: 0.4 },
  { ticker: "QQQ", spot: 480, atm_vol: 0.2, skew_slope: -0.7, skew_curv: 0.5 },
];

function labelTone(label: string): string {
  if (label === "favorable") return "bg-pos/10 text-pos";
  if (label === "unfavorable") return "bg-neg/10 text-neg";
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
      <div className="flex items-center gap-1 text-2xs uppercase tracking-wide text-slate-500">
        {label}
        {cnLabel && <span className="normal-case text-slate-400">{cnLabel}</span>}
        {tip && <InfoDot tip={tip} />}
      </div>
      <div className="mt-0.5 text-lg font-semibold text-brand-900 tnum">{value}</div>
      {hint && <div className="text-2xs text-slate-400">{hint}</div>}
    </div>
  );
}

export function MarketRead() {
  const [assets, setAssets] = useState<AssetMarketInput[]>(DEFAULT_ASSETS);
  const [lang, setLang] = useState<"en" | "zh">("en");
  const [rate, setRate] = useState(0.045);
  const [loading, setLoading] = useState(false);
  const [stage, setStage] = useState<string>("");
  const [err, setErr] = useState<string | null>(null);
  const [res, setRes] = useState<MarketReadResult | null>(null);

  function patch(i: number, k: keyof AssetMarketInput, v: string) {
    setAssets((a) =>
      a.map((row, j) =>
        j === i ? { ...row, [k]: k === "ticker" ? v.toUpperCase() : Number(v) } : row,
      ),
    );
  }
  function addRow() {
    setAssets((a) => [...a, { ticker: "IWM", spot: 200, atm_vol: 0.22, skew_slope: -0.6, skew_curv: 0.5 }]);
  }
  function removeRow(i: number) {
    setAssets((a) => (a.length > 1 ? a.filter((_, j) => j !== i) : a));
  }

  async function run() {
    setLoading(true);
    setErr(null);
    setStage("submitting");
    try {
      const body = {
        indices: assets.map((a) => a.ticker),
        source: "manual" as const,
        rate,
        lang,
        assets,
      };
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
  const chart = m
    ? [
        { x: m.per_index.map((p) => p.ticker), y: m.per_index.map((p) => p.atm_1m * 100), type: "bar", name: "1M ATM" },
        { x: m.per_index.map((p) => p.ticker), y: m.per_index.map((p) => p.atm_3m * 100), type: "bar", name: "3M ATM" },
        { x: m.per_index.map((p) => p.ticker), y: m.per_index.map((p) => p.atm_1y * 100), type: "bar", name: "1Y ATM" },
      ]
    : [];

  return (
    <div className="flex flex-col gap-4 p-4 sm:p-6">
      <div>
        <h1 className="text-base font-semibold text-brand-900">Market Read</h1>
        <p className="text-xs text-slate-400">
          Map index vol surfaces to note-suitability, with an LLM market commentary.
        </p>
      </div>

      {/* ── inputs ─────────────────────────────────────────────── */}
      <Card>
        <SectionHeader
          title="Index Inputs"
          titleCn="指数输入"
          icon={<Gauge size={15} />}
          right={
            <div className="flex items-center gap-2">
              <div className="flex overflow-hidden rounded-lg border border-line">
                {(["en", "zh"] as const).map((l) => (
                  <button
                    key={l}
                    onClick={() => setLang(l)}
                    className={cn(
                      "px-2.5 py-1 text-2xs font-medium",
                      lang === l ? "bg-accent-600 text-white" : "bg-surface-2 text-slate-400",
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
                {loading ? "Reading…" : "Read Market"}
              </button>
            </div>
          }
        />
        <div className="overflow-x-auto p-4">
          <table className="w-full min-w-[560px] text-xs">
            <thead>
              <tr className="text-left text-2xs uppercase tracking-wide text-slate-500">
                <th className="pb-2 pr-3 font-medium">Index</th>
                <th className="pb-2 pr-3 font-medium">Spot</th>
                <th className="pb-2 pr-3 font-medium">ATM Vol</th>
                <th className="pb-2 pr-3 font-medium">Skew Slope</th>
                <th className="pb-2 pr-3 font-medium">Skew Curv</th>
                <th className="pb-2" />
              </tr>
            </thead>
            <tbody>
              {assets.map((a, i) => (
                <tr key={i}>
                  <td className="py-1 pr-3">
                    <input className={INPUT} value={a.ticker} onChange={(e) => patch(i, "ticker", e.target.value)} />
                  </td>
                  <td className="py-1 pr-3">
                    <input className={INPUT} type="number" value={a.spot} onChange={(e) => patch(i, "spot", e.target.value)} />
                  </td>
                  <td className="py-1 pr-3">
                    <input className={INPUT} type="number" step="0.01" value={a.atm_vol} onChange={(e) => patch(i, "atm_vol", e.target.value)} />
                  </td>
                  <td className="py-1 pr-3">
                    <input className={INPUT} type="number" step="0.1" value={a.skew_slope ?? -0.5} onChange={(e) => patch(i, "skew_slope", e.target.value)} />
                  </td>
                  <td className="py-1 pr-3">
                    <input className={INPUT} type="number" step="0.1" value={a.skew_curv ?? 0.5} onChange={(e) => patch(i, "skew_curv", e.target.value)} />
                  </td>
                  <td className="py-1">
                    <button onClick={() => removeRow(i)} className="text-slate-500 hover:text-neg" title="Remove">
                      <Trash2 size={14} />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="mt-3 flex items-center gap-4">
            <button onClick={addRow} className="flex items-center gap-1 text-2xs text-accent-100 hover:underline">
              <Plus size={13} /> add index
            </button>
            <label className="flex items-center gap-2 text-2xs text-slate-400">
              risk-free rate
              <input
                className={cn(INPUT, "w-24")}
                type="number"
                step="0.005"
                value={rate}
                onChange={(e) => setRate(Number(e.target.value))}
              />
            </label>
          </div>
        </div>
      </Card>

      {loading && (
        <div className="text-xs text-slate-400">
          Reading surfaces… <span className="text-accent-100">{stage}</span>
        </div>
      )}
      {err && <div className="text-xs text-neg">Error: {err}</div>}

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
                  className={res.narrative_source === "llm" ? "bg-accent-600/15 text-accent-100" : "bg-surface-2 text-slate-400"}
                  title={res.narrative_source === "llm" ? "AI 撰写(Opus→Codex→GLM→DeepSeek 择优)" : "无 LLM 时的确定性模板"}
                >
                  {res.narrative_source === "llm" ? "AI 解读" : "模板"}
                </Badge>
              }
            />
            <p className="whitespace-pre-wrap p-4 text-sm leading-relaxed text-brand-900">{res.narrative}</p>
          </Card>

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
                    <span className="text-2xs text-slate-500">/100</span>
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
                      <li key={i} className="text-2xs leading-snug text-slate-400">· {d}</li>
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
              right={<Badge className="bg-surface-2 text-slate-400">source · {res.source}</Badge>}
            />
            <div className="grid grid-cols-2 gap-2 p-4 sm:grid-cols-3 lg:grid-cols-5">
              <Metric label={T.volLevel.label} cn={T.volLevel.cn} value={pct(m.vol_level, 0)} hint="avg ATM" tip={T.volLevel.tip} />
              <Metric label={T.putSkew.label} cn={T.putSkew.cn} value={`+${(m.skew * 100).toFixed(1)}pt`} hint="at 90%" tip={T.putSkew.tip} />
              <Metric label={T.termSlope.label} cn={T.termSlope.cn} value={`${(m.term_slope * 100).toFixed(1)}pt`} hint="1Y−1M" tip={T.termSlope.tip} />
              <Metric label={T.vixProxy.label} cn={T.vixProxy.cn} value={m.vix_proxy.toFixed(1)} hint="SPY 30D ATM" tip={T.vixProxy.tip} />
              <Metric label={T.riskFree.label} cn={T.riskFree.cn} value={pct(m.rate, 2)} tip={T.riskFree.tip} />
            </div>
          </Card>

          {/* ── per-index table + chart ──────────────────────── */}
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            <Card>
              <SectionHeader title="Per-Index Surface" titleCn="各指数波动率" icon={<LineChart size={15} />} />
              <div className="overflow-x-auto p-4">
                <table className="w-full min-w-[420px] text-xs tnum">
                  <thead>
                    <tr className="text-right text-2xs uppercase tracking-wide text-slate-500">
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
                        <td className="py-1.5 text-left font-medium text-brand-900">{p.ticker}</td>
                        <td className="py-1.5 text-slate-400">{p.spot.toFixed(2)}</td>
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
              <SectionHeader title="ATM Vol Term (%)" titleCn="平价波动率期限" icon={<LineChart size={15} />} />
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
