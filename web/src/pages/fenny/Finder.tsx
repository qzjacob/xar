import { useState } from "react";
import { ListOrdered, Play, Plus, X, Trophy, AlertTriangle } from "lucide-react";
import { fennyApi } from "../../lib/fenny";
import { Card } from "../../components/ui/Card";
import { SectionHeader } from "../../components/ui/SectionHeader";
import { cn, heat } from "../../lib/format";
import type { Job } from "../../types-fenny";

// One candidate row in the manual (offline) universe. spot + atm_vol drive the coupon;
// skew params default to the parametric put-skew surface used across the desk.
interface Candidate {
  ticker: string;
  spot: number;
  atm_vol: number;
}

interface RankedRow {
  ticker: string;
  spot: number;
  coupon: number; // fair annualized coupon, fraction (0.38 = 38.1% p.a.)
  iv_at_barrier: number; // fraction
  prob_capital_at_risk: number; // fraction
  buffer_pct: number; // fraction
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
}

const DEFAULT_CANDIDATES: Candidate[] = [
  { ticker: "AAPL", spot: 230, atm_vol: 0.28 },
  { ticker: "NVDA", spot: 120, atm_vol: 0.5 },
  { ticker: "MSFT", spot: 440, atm_vol: 0.24 },
  { ticker: "TSLA", spot: 250, atm_vol: 0.6 },
  { ticker: "AMD", spot: 160, atm_vol: 0.45 },
];

const RANK_BY_OPTIONS: { value: string; label: string }[] = [
  { value: "coupon", label: "Indicative coupon" },
  { value: "prob_capital_at_risk", label: "Prob. capital at risk (safest first)" },
  { value: "iv_at_barrier", label: "IV at barrier" },
];

const FREQUENCIES = ["monthly", "quarterly", "semiannual", "annual"];

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

export function Finder() {
  // Fixed structure every candidate is screened against.
  const [tenorMonths, setTenorMonths] = useState(6);
  const [frequency, setFrequency] = useState("quarterly");
  const [protectionPct, setProtectionPct] = useState(0.7);
  const [strikePct, setStrikePct] = useState(1.0);
  const [rate, setRate] = useState(0.045);
  const [topN, setTopN] = useState(10);
  const [rankBy, setRankBy] = useState("coupon");
  const [candidates, setCandidates] = useState<Candidate[]>(DEFAULT_CANDIDATES);

  const [loading, setLoading] = useState(false);
  const [stage, setStage] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<RankResult | null>(null);

  function updateCandidate(i: number, patch: Partial<Candidate>) {
    setCandidates((cs) => cs.map((c, idx) => (idx === i ? { ...c, ...patch } : c)));
  }
  function addCandidate() {
    setCandidates((cs) => [...cs, { ticker: "", spot: 100, atm_vol: 0.3 }]);
  }
  function removeCandidate(i: number) {
    setCandidates((cs) => cs.filter((_, idx) => idx !== i));
  }

  async function run() {
    setLoading(true);
    setError(null);
    setStage("submitting");
    const clean = candidates.filter((c) => c.ticker.trim());
    try {
      const body = {
        structure: {
          product: "fcn",
          tenor_months: tenorMonths,
          frequency,
          protection_pct: protectionPct,
          strike_pct: strikePct,
        },
        source: "manual" as const,
        rate,
        top_n: topN,
        rank_by: rankBy,
        tickers: clean.map((c) => c.ticker.trim().toUpperCase()),
        assets: clean.map((c) => ({
          ticker: c.ticker.trim().toUpperCase(),
          spot: c.spot,
          atm_vol: c.atm_vol,
          skew_slope: -0.4,
          skew_curv: 0.3,
        })),
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
  // Coupon heat is normalized within the visible set so the leader is greenest.
  const coupons = ranked.map((r) => r.coupon);
  const minC = Math.min(...coupons);
  const maxC = Math.max(...coupons);
  const heatFor = (c: number) => {
    const t = maxC > minC ? ((c - minC) / (maxC - minC)) * 100 : 60;
    return heat(t, "good-high", 0.2);
  };

  return (
    <div className="min-h-full bg-canvas p-4">
      <div className="mx-auto flex max-w-6xl flex-col gap-4">
        {/* ---- Structure + screen controls ---- */}
        <Card>
          <SectionHeader
            title="Underlying Finder"
            titleCn="标的筛选"
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
                {loading ? stage || "ranking…" : "Rank"}
              </button>
            }
          />
          <div className="p-4">
            <p className="mb-3 text-xs text-slate-400">
              Fix the protection barrier and tenor, then rank a candidate set by which
              underlying pays the richest indicative FCN coupon. Manual mode — works offline
              off a parametric skew surface.
            </p>
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
              <label className="flex flex-col gap-1 text-2xs text-slate-500">
                Tenor (months)
                <input
                  type="number"
                  min={1}
                  value={tenorMonths}
                  onChange={(e) => setTenorMonths(Number(e.target.value))}
                  className={inputCls}
                />
              </label>
              <label className="flex flex-col gap-1 text-2xs text-slate-500">
                Frequency
                <select
                  value={frequency}
                  onChange={(e) => setFrequency(e.target.value)}
                  className={inputCls}
                >
                  {FREQUENCIES.map((f) => (
                    <option key={f} value={f}>
                      {f}
                    </option>
                  ))}
                </select>
              </label>
              <label className="flex flex-col gap-1 text-2xs text-slate-500">
                Protection %
                <input
                  type="number"
                  step={0.05}
                  value={protectionPct}
                  onChange={(e) => setProtectionPct(Number(e.target.value))}
                  className={inputCls}
                />
              </label>
              <label className="flex flex-col gap-1 text-2xs text-slate-500">
                Strike %
                <input
                  type="number"
                  step={0.05}
                  value={strikePct}
                  onChange={(e) => setStrikePct(Number(e.target.value))}
                  className={inputCls}
                />
              </label>
              <label className="flex flex-col gap-1 text-2xs text-slate-500">
                Rate
                <input
                  type="number"
                  step={0.005}
                  value={rate}
                  onChange={(e) => setRate(Number(e.target.value))}
                  className={inputCls}
                />
              </label>
              <label className="flex flex-col gap-1 text-2xs text-slate-500">
                Top N
                <input
                  type="number"
                  min={1}
                  max={50}
                  value={topN}
                  onChange={(e) => setTopN(Number(e.target.value))}
                  className={inputCls}
                />
              </label>
            </div>
            <div className="mt-3 flex flex-col gap-1 text-2xs text-slate-500 sm:max-w-xs">
              Rank by
              <select
                value={rankBy}
                onChange={(e) => setRankBy(e.target.value)}
                className={inputCls}
              >
                {RANK_BY_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
            </div>
          </div>
        </Card>

        {/* ---- Candidate universe editor ---- */}
        <Card>
          <SectionHeader
            title="Candidate universe"
            titleCn="候选池"
            right={
              <button
                onClick={addCandidate}
                className="flex items-center gap-1 rounded-lg border border-line bg-surface-2 px-2 py-1 text-2xs text-brand-900 hover:bg-surface-2/70"
              >
                <Plus size={12} /> Add name
              </button>
            }
          />
          <div className="p-3">
            <div className="grid grid-cols-[1fr_1fr_1fr_auto] gap-2 px-1 pb-1 text-2xs uppercase tracking-wide text-slate-500">
              <span>Ticker</span>
              <span>Spot</span>
              <span>ATM vol</span>
              <span />
            </div>
            <div className="flex flex-col gap-1.5">
              {candidates.map((c, i) => (
                <div key={i} className="grid grid-cols-[1fr_1fr_1fr_auto] items-center gap-2">
                  <input
                    value={c.ticker}
                    onChange={(e) => updateCandidate(i, { ticker: e.target.value })}
                    placeholder="TICK"
                    className={cn(inputCls, "uppercase")}
                  />
                  <input
                    type="number"
                    step={1}
                    value={c.spot}
                    onChange={(e) => updateCandidate(i, { spot: Number(e.target.value) })}
                    className={cn(inputCls, "tnum")}
                  />
                  <input
                    type="number"
                    step={0.01}
                    value={c.atm_vol}
                    onChange={(e) => updateCandidate(i, { atm_vol: Number(e.target.value) })}
                    className={cn(inputCls, "tnum")}
                  />
                  <button
                    onClick={() => removeCandidate(i)}
                    className="rounded-lg p-1.5 text-slate-500 hover:bg-surface-2 hover:text-neg"
                    aria-label="remove"
                  >
                    <X size={13} />
                  </button>
                </div>
              ))}
              {candidates.length === 0 && (
                <p className="px-1 py-2 text-xs text-slate-500">
                  No candidates — add at least one name to rank.
                </p>
              )}
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
                  {result?.ranked_count ?? ranked.length} of {result?.considered ?? "—"} screened ·
                  by {result?.rank_by ?? rankBy}
                </span>
              }
            />
            <div className="overflow-x-auto">
              <table className="w-full min-w-[640px] text-xs">
                <thead>
                  <tr className="border-b border-line text-2xs uppercase tracking-wide text-slate-500">
                    <th className="px-3 py-2 text-left">#</th>
                    <th className="px-3 py-2 text-left">Ticker</th>
                    <th className="px-3 py-2 text-right">Spot</th>
                    <th className="px-3 py-2 text-right">Coupon p.a.</th>
                    <th className="px-3 py-2 text-right">Prob. cap. at risk</th>
                    <th className="px-3 py-2 text-right">IV @ barrier</th>
                    <th className="px-3 py-2 text-right">Buffer</th>
                    <th className="px-3 py-2 text-right">Mkt cap</th>
                  </tr>
                </thead>
                <tbody>
                  {ranked.map((r) => (
                    <tr key={r.ticker} className="border-b border-line/60 last:border-0">
                      <td className="px-3 py-2 text-slate-500 tnum">{r.rank}</td>
                      <td className="px-3 py-2">
                        <span className="font-medium text-brand-900">{r.ticker}</span>
                        {r.isEtf && (
                          <span className="ml-1.5 text-2xs text-slate-500">ETF</span>
                        )}
                      </td>
                      <td className="px-3 py-2 text-right text-brand-900 tnum">
                        {r.spot.toFixed(2)}
                      </td>
                      <td
                        className="px-3 py-2 text-right font-semibold tnum"
                        style={heatFor(r.coupon)}
                      >
                        {pct(r.coupon)}
                      </td>
                      <td className="px-3 py-2 text-right text-warn-100 tnum">
                        {pct(r.prob_capital_at_risk)}
                      </td>
                      <td className="px-3 py-2 text-right text-slate-400 tnum">
                        {pct(r.iv_at_barrier)}
                      </td>
                      <td className="px-3 py-2 text-right text-slate-400 tnum">
                        {pct(r.buffer_pct, 0)}
                      </td>
                      <td className="px-3 py-2 text-right text-slate-400 tnum">
                        {mktCap(r.marketCap)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {result?.skipped && result.skipped.length > 0 && (
              <p className="border-t border-line px-4 py-2 text-2xs text-slate-500">
                Skipped {result.skipped.length}:{" "}
                {result.skipped.map((s) => s.ticker).join(", ")}
              </p>
            )}
            {result?.liquidity_note && (
              <p className="border-t border-line px-4 py-2 text-2xs text-slate-500">
                {result.liquidity_note}
              </p>
            )}
          </Card>
        )}

        {loading && ranked.length === 0 && (
          <p className="px-1 text-xs text-slate-400">Ranking… {stage}</p>
        )}
      </div>
    </div>
  );
}
