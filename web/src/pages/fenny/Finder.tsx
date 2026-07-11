import { useState } from "react";
import { ListOrdered, Play, Trophy, AlertTriangle } from "lucide-react";
import { fennyApi } from "../../lib/fenny";
import { Card } from "../../components/ui/Card";
import { SectionHeader } from "../../components/ui/SectionHeader";
import { cn, heat } from "../../lib/format";
import type { Job } from "../../types-fenny";

// Ranked row from /jobs/rank. coupon (rank_by=coupon) or strike (rank_by=strike) is
// the headline; MC rows (敲出/美式敲入/解行权价) additionally carry autocall stats.
interface RankedRow {
  ticker: string;
  name?: string;
  spot: number;
  coupon?: number; // fair annualized coupon, fraction
  strike?: number; // fair strike as fraction of spot (rank_by=strike)
  bracketed?: boolean; // false = strike solve clamped at the [40%,120%] bound (target unreachable)
  prob_autocall?: number;
  expected_life?: number;
  iv_at_barrier: number;
  prob_capital_at_risk: number;
  buffer_pct: number;
  marketCap: number;
  sector: string;
  isEtf: boolean;
  rank: number;
}

interface RankResult {
  ranked?: RankedRow[];
  universe_size?: number;
  considered?: number;
  ranked_count?: number;
  skipped?: { ticker: string; reason: string }[];
  rank_by?: string;
  liquidity_note?: string;
  vol_basis?: string;
  rate?: number;
  universe_source?: string; // "fmp-screener" (full market) | "seed-large-cap" (bundled ~200)
}

// 观察频率 — mirrors the quote desk's obs-frequency choices
const FREQUENCIES: { value: string; label: string }[] = [
  { value: "monthly", label: "每月 Monthly" },
  { value: "quarterly", label: "每季 Quarterly" },
  { value: "semiannual", label: "半年 Semiannual" },
  { value: "annual", label: "每年 Annual" },
];

const KI_STYLES: { value: string; label: string }[] = [
  { value: "none", label: "无保护 NONE" },
  { value: "european", label: "欧式(到期观察)" },
  { value: "american", label: "美式(每日观察)" },
];

const inputCls =
  "rounded-lg border border-line bg-surface-2 px-2 py-1.5 text-xs text-brand-900 focus:border-accent-600 focus:outline-none";

function pct(n: number | undefined, digits = 2): string {
  return n == null || !Number.isFinite(n) ? "—" : `${(n * 100).toFixed(digits)}%`;
}

function mktCap(n: number | undefined): string {
  if (!n || !Number.isFinite(n)) return "—";
  if (n >= 1e12) return `$${(n / 1e12).toFixed(2)}T`;
  if (n >= 1e9) return `$${(n / 1e9).toFixed(1)}B`;
  return `$${(n / 1e6).toFixed(0)}M`;
}

function Lab({ children, tip }: { children: React.ReactNode; tip?: string }) {
  return (
    <span className="flex items-center gap-1" title={tip}>
      {children}
    </span>
  );
}

export function Finder() {
  // 结构参数 — 与报价台同名:敲出线/期限/敲入类型/敲入线/观察频率/行权价
  const [tenorMonths, setTenorMonths] = useState(6);          // 期限
  const [frequency, setFrequency] = useState("monthly");      // 观察频率
  const [koPct, setKoPct] = useState<string>("100");          // 敲出线 (% of spot; 空 = 无敲出)
  const [kiStyle, setKiStyle] = useState("european");         // 敲入类型
  const [kiPct, setKiPct] = useState(65);                     // 敲入线
  const [strikePct, setStrikePct] = useState(80);             // 行权价 (排票息时固定)
  const [couponPa, setCouponPa] = useState(12);               // 票息 % p.a. (排行权价时固定)
  const [rankBy, setRankBy] = useState<"coupon" | "strike">("coupon");
  // 底层标的 = 全部美股+ETF(FMP 全市场筛选),按市值下限过滤(亿美元)
  const [mktCapYi, setMktCapYi] = useState(200);              // 市值下限,亿美元 (200亿 = $20bn)
  const [kind, setKind] = useState<"all" | "stock" | "etf">("all");
  const [topN, setTopN] = useState(10);

  const [loading, setLoading] = useState(false);
  const [stage, setStage] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<RankResult | null>(null);

  async function run() {
    setLoading(true);
    setError(null);
    setStage("fetching universe");
    try {
      const body = {
        structure: {
          product: "fcn",
          tenor_months: tenorMonths,
          frequency,
          protection_pct: kiStyle === "none" ? strikePct / 100 : kiPct / 100,
          strike_pct: strikePct / 100,
          // NaN 守卫:非数字文本(如粘贴 "100%")不可静默变成 null(无敲出)
          ko_pct: koPct.trim() === "" || !Number.isFinite(Number(koPct)) ? null : Number(koPct) / 100,
          ki_style: kiStyle,
          coupon_pa: rankBy === "strike" ? couponPa / 100 : null,
        },
        source: "auto" as const, // 实时真实数据:FMP spot + 实际波动率 + 全市场筛选器
        top_n: topN,
        rank_by: rankBy,
        min_market_cap: mktCapYi * 1e8, // 亿美元 → USD
        max_candidates: 100,
        filters: kind === "all" ? null : { kind },
      };
      const res = (await fennyApi.rank(body, (j: Job) => setStage(j.stage))) as RankResult;
      setResult(res);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
      setStage("");
    }
  }

  const ranked = result?.ranked ?? [];
  const byStrike = (result?.rank_by ?? rankBy) === "strike";
  // Heat: coupon → higher greener; strike → lower greener (bigger buffer)
  const vals = ranked.map((r) => (byStrike ? r.strike ?? 0 : r.coupon ?? 0));
  const minV = Math.min(...vals);
  const maxV = Math.max(...vals);
  const heatFor = (v: number) => {
    const t = maxV > minV ? ((v - minV) / (maxV - minV)) * 100 : 60;
    return heat(byStrike ? 100 - t : t, "good-high", 0.2);
  };

  return (
    <div className="min-h-full bg-canvas p-4">
      <div className="mx-auto flex max-w-6xl flex-col gap-4">
        {/* ---- Structure (desk params) + universe controls ---- */}
        <Card>
          <SectionHeader
            title="Underlying Finder"
            titleCn="标的筛选 · 全美股+ETF 实时数据"
            icon={<ListOrdered size={16} />}
            right={
              <button
                onClick={run}
                disabled={loading}
                className={cn(
                  "flex items-center gap-1.5 rounded-lg bg-accent-600 px-3 py-1.5 text-xs font-medium text-white",
                  "hover:bg-accent-600/90 disabled:cursor-not-allowed disabled:opacity-50",
                )}
              >
                <Play size={13} />
                {loading ? stage || "筛选中…" : "筛选 Rank"}
              </button>
            }
          />
          <div className="p-4">
            <p className="mb-3 text-xs text-slate-400">
              固定结构参数(与报价台同名),在<span className="text-brand-900">全部美股+ETF</span>
              (按市值下限过滤)中筛选:按<span className="text-brand-900">票息最高</span>
              或同票息下<span className="text-brand-900">行权价最低(下行缓冲最大)</span>排序。
              现价与波动率为实时真实数据。
            </p>
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
              <label className="flex flex-col gap-1 text-2xs text-slate-500">
                <Lab tip="票据期限,月">期限 (月)</Lab>
                <input type="number" min={1} value={tenorMonths}
                  onChange={(e) => setTenorMonths(Number(e.target.value))} className={inputCls} />
              </label>
              <label className="flex flex-col gap-1 text-2xs text-slate-500">
                <Lab tip="敲出/派息观察频率">观察频率</Lab>
                <select value={frequency} onChange={(e) => setFrequency(e.target.value)} className={inputCls}>
                  {FREQUENCIES.map((f) => (
                    <option key={f.value} value={f.value}>{f.label}</option>
                  ))}
                </select>
              </label>
              <label className="flex flex-col gap-1 text-2xs text-slate-500">
                <Lab tip="自动敲出线,% 期初价;留空 = 无敲出">敲出线 KO %</Lab>
                <input type="number" step={1} value={koPct} placeholder="无"
                  onChange={(e) => setKoPct(e.target.value)} className={cn(inputCls, "tnum")} />
              </label>
              <label className="flex flex-col gap-1 text-2xs text-slate-500">
                <Lab tip="下行保护的观察方式">敲入类型</Lab>
                <select value={kiStyle} onChange={(e) => setKiStyle(e.target.value)} className={inputCls}>
                  {KI_STYLES.map((k) => (
                    <option key={k.value} value={k.value}>{k.label}</option>
                  ))}
                </select>
              </label>
              <label className="flex flex-col gap-1 text-2xs text-slate-500">
                <Lab tip="敲入/保护线,% 期初价">敲入线 KI %</Lab>
                <input type="number" step={1} value={kiPct} disabled={kiStyle === "none"}
                  onChange={(e) => setKiPct(Number(e.target.value))}
                  className={cn(inputCls, "tnum", kiStyle === "none" && "opacity-40")} />
              </label>
              {rankBy === "coupon" ? (
                <label className="flex flex-col gap-1 text-2xs text-slate-500">
                  <Lab tip="转换行权价,% 期初价(排序票息时固定)">行权价 %</Lab>
                  <input type="number" step={1} value={strikePct}
                    onChange={(e) => setStrikePct(Number(e.target.value))} className={cn(inputCls, "tnum")} />
                </label>
              ) : (
                <label className="flex flex-col gap-1 text-2xs text-slate-500">
                  <Lab tip="固定年化票息(排序行权价时固定)">票息 % p.a.</Lab>
                  <input type="number" step={0.5} value={couponPa}
                    onChange={(e) => setCouponPa(Number(e.target.value))} className={cn(inputCls, "tnum")} />
                </label>
              )}
            </div>
            <div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
              <label className="flex flex-col gap-1 text-2xs text-slate-500">
                <Lab tip="两种排序:票息最高优先,或同票息下行权价最低优先">排序类型</Lab>
                <select value={rankBy} onChange={(e) => setRankBy(e.target.value as "coupon" | "strike")} className={inputCls}>
                  <option value="coupon">票息最高 Coupon</option>
                  <option value="strike">行权价最低 Strike</option>
                </select>
              </label>
              <label className="flex flex-col gap-1 text-2xs text-slate-500">
                <Lab tip="按总市值过滤底层标的池,单位:亿美元(如 100 = $10bn)">市值下限 (亿美元)</Lab>
                <input type="number" step={50} min={10} value={mktCapYi}
                  onChange={(e) => setMktCapYi(Number(e.target.value))} className={cn(inputCls, "tnum")} />
              </label>
              <label className="flex flex-col gap-1 text-2xs text-slate-500">
                <Lab tip="标的类型">类型</Lab>
                <select value={kind} onChange={(e) => setKind(e.target.value as typeof kind)} className={inputCls}>
                  <option value="all">全部(股票+ETF)</option>
                  <option value="stock">仅股票</option>
                  <option value="etf">仅 ETF</option>
                </select>
              </label>
              <label className="flex flex-col gap-1 text-2xs text-slate-500">
                <Lab tip="返回前 N 名">Top N</Lab>
                <input type="number" min={1} max={50} value={topN}
                  onChange={(e) => setTopN(Number(e.target.value))} className={inputCls} />
              </label>
            </div>
          </div>
        </Card>

        {error && (
          <div className="flex items-center gap-2 rounded-lg border border-neg/30 bg-neg-50 px-3 py-2 text-xs text-neg">
            <AlertTriangle size={14} /> {error}
          </div>
        )}

        {/* ---- Rankings ---- */}
        {ranked.length > 0 && (
          <Card>
            <SectionHeader
              title="Rankings"
              titleCn="排名"
              icon={<Trophy size={16} />}
              right={
                <span className="text-2xs text-slate-500">
                  宇宙 {result?.universe_size ?? "—"} · 已筛 {result?.considered ?? "—"} ·
                  {byStrike ? " 行权价最低优先" : " 票息最高优先"}
                  {result?.vol_basis === "realized" && " · 实际波动率"}
                </span>
              }
            />
            <div className="overflow-x-auto">
              <table className="w-full min-w-[720px] text-xs">
                <thead>
                  <tr className="border-b border-line text-2xs uppercase tracking-wide text-slate-500">
                    <th className="px-3 py-2 text-left">#</th>
                    <th className="px-3 py-2 text-left">标的</th>
                    <th className="px-3 py-2 text-right">现价</th>
                    <th className="px-3 py-2 text-right">{byStrike ? "行权价" : "票息 p.a."}</th>
                    {ranked.some((r) => r.prob_autocall != null) && (
                      <th className="px-3 py-2 text-right">敲出概率</th>
                    )}
                    <th className="px-3 py-2 text-right">亏损概率</th>
                    <th className="px-3 py-2 text-right">波动率</th>
                    <th className="px-3 py-2 text-right">缓冲</th>
                    <th className="px-3 py-2 text-right">市值</th>
                  </tr>
                </thead>
                <tbody>
                  {ranked.map((r) => (
                    <tr key={r.ticker} className="border-b border-line/60 last:border-0">
                      <td className="px-3 py-2 text-slate-500 tnum">{r.rank}</td>
                      <td className="px-3 py-2">
                        <span className="font-medium text-brand-900">{r.ticker}</span>
                        {r.isEtf && <span className="ml-1.5 text-2xs text-slate-500">ETF</span>}
                        {r.name && r.name !== r.ticker && (
                          <span className="ml-1.5 text-2xs text-slate-500">{r.name.slice(0, 22)}</span>
                        )}
                      </td>
                      <td className="px-3 py-2 text-right text-brand-900 tnum">{r.spot.toFixed(2)}</td>
                      <td className="px-3 py-2 text-right font-semibold tnum"
                        style={heatFor(byStrike ? r.strike ?? 0 : r.coupon ?? 0)}>
                        {byStrike ? pct(r.strike, 1) : pct(r.coupon)}
                        {byStrike && r.bracketed === false && (
                          <span className="ml-1 text-2xs font-normal text-warn-100"
                            title="该票息下目标价无法在 [40%,120%] 行权价区间内实现,显示为夹逼值 — 已排到最后">
                            ⚠夹逼
                          </span>
                        )}
                      </td>
                      {ranked.some((x) => x.prob_autocall != null) && (
                        <td className="px-3 py-2 text-right text-slate-400 tnum">{pct(r.prob_autocall, 0)}</td>
                      )}
                      <td className="px-3 py-2 text-right text-warn-100 tnum">{pct(r.prob_capital_at_risk)}</td>
                      <td className="px-3 py-2 text-right text-slate-400 tnum">{pct(r.iv_at_barrier, 0)}</td>
                      <td className="px-3 py-2 text-right text-slate-400 tnum">{pct(r.buffer_pct, 0)}</td>
                      <td className="px-3 py-2 text-right text-slate-400 tnum">{mktCap(r.marketCap)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {result?.skipped && result.skipped.length > 0 && (
              <p className="border-t border-line px-4 py-2 text-2xs text-slate-500">
                跳过 {result.skipped.length} 个(无实时数据):{" "}
                {result.skipped.slice(0, 20).map((s) => s.ticker).join(", ")}
                {result.skipped.length > 20 ? " …" : ""}
              </p>
            )}
            {result?.universe_source === "seed-large-cap" && (
              <p className="border-t border-line px-4 py-2 text-2xs text-slate-500">
                注:全市场筛选器在当前数据档位不可用,已退回内置大盘股+ETF 种子池(约 200 名,含真实市值);市值下限仍生效。
              </p>
            )}
            {result?.liquidity_note && (
              <p className="border-t border-line px-4 py-2 text-2xs text-slate-500">{result.liquidity_note}</p>
            )}
          </Card>
        )}

        {loading && ranked.length === 0 && (
          <p className="px-1 text-xs text-slate-400">全市场筛选中(首次约 20-40 秒)… {stage}</p>
        )}
      </div>
    </div>
  );
}
