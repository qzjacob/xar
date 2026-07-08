import { useState } from "react";
import { CalendarClock, Gauge, Loader2, Play, Target, TrendingDown, TrendingUp } from "lucide-react";

import { runCapability } from "../lib/runs";
import type { EarningsBlock } from "../types";
import { Badge, Card, SectionHeader } from "./ui";

// 季报事件交易面板(公司 360 页)——方向 + conviction(≥7 高亮)+ implied vs 历史 |move|
// + beat 习惯序列 + 财报倒计时 + 锁后漂移 chip + 近 4 次 outcome hit/miss。仅美股 universe 名字有。

const DIR_LABEL: Record<string, string> = { long: "做多", short: "做空", no_trade: "不交易" };

function dirColor(d?: string | null): string {
  if (d === "long") return "text-emerald-400";
  if (d === "short") return "text-rose-400";
  return "text-neutral-400";
}

function pct(x?: number | null, digits = 1): string {
  return x == null ? "—" : `${(x * 100).toFixed(digits)}%`;
}

export function EarningsSection({
  cid,
  earnings,
  onRefetch,
}: {
  cid: string;
  earnings: EarningsBlock | null | undefined;
  onRefetch?: () => Promise<void> | void;
}) {
  const [running, setRunning] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  if (!earnings) return null;
  const { event, verdict, beat, impliedMove, recentOutcomes } = earnings;
  const actionable = (verdict?.conviction ?? 0) >= 7;

  async function runVerdict() {
    setRunning(true);
    setErr(null);
    try {
      const st = await runCapability("build_earnings_verdict", { company_id: cid, force: !!verdict });
      if (st.status === "error") setErr(String(st.error ?? "run failed"));
      else await onRefetch?.();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setRunning(false);
    }
  }

  return (
    <Card>
      <SectionHeader
        icon={<CalendarClock className="h-4 w-4" />}
        title="季报事件交易"
        titleCn={
          event
            ? `财报日 ${event.date}${event.session ? ` · ${event.session.toUpperCase()}` : ""} · T${
                event.daysTo >= 0 ? "+" : ""
              }${event.daysTo}`
            : "窗口外(未临近财报)"
        }
        right={
          <button
            type="button"
            onClick={runVerdict}
            disabled={running}
            className="inline-flex items-center gap-1 rounded-md border border-line px-2 py-1 text-xs text-slate-300 hover:bg-white/5 disabled:opacity-50"
          >
            {running ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Play className="h-3.5 w-3.5" />}
            {running ? "裁决生成中…" : verdict ? "重跑 (force)" : "跑 ET 裁决"}
          </button>
        }
      />
      {err && <div className="px-4 pt-2 text-xs text-rose-400">{err}</div>}

      <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
        {/* 裁决 */}
        <div className="rounded-lg border border-neutral-800 p-3">
          <div className="mb-1 flex items-center gap-2 text-xs text-neutral-400">
            <Target className="h-3.5 w-3.5" /> LLM 裁决
          </div>
          {verdict ? (
            <>
              <div className="flex items-baseline gap-2">
                <span className={`text-lg font-semibold ${dirColor(verdict.direction)}`}>
                  {DIR_LABEL[verdict.direction] ?? verdict.direction}
                </span>
                <span
                  className={`inline-flex items-center gap-1 text-sm ${
                    actionable ? "font-bold text-amber-300" : "text-neutral-400"
                  }`}
                >
                  <Gauge className="h-3.5 w-3.5" />
                  {verdict.conviction}/10
                </span>
                {actionable && (
                  <Badge className="bg-amber-500/15 text-amber-300">可操作</Badge>
                )}
              </div>
              <div className="mt-1 text-[11px] text-neutral-500">
                v{verdict.version} · {verdict.asOf}
                {verdict.model ? ` · ${verdict.model}` : ""}
                {verdict.impliedDriftPp != null && (
                  <span className="ml-1 text-neutral-400">
                    锁后隐含波动 {verdict.impliedDriftPp >= 0 ? "+" : ""}
                    {verdict.impliedDriftPp}pp
                  </span>
                )}
              </div>
            </>
          ) : (
            <div className="text-sm text-neutral-500">尚无裁决(T-3 生成)</div>
          )}
        </div>

        {/* implied move */}
        <div className="rounded-lg border border-neutral-800 p-3">
          <div className="mb-1 text-xs text-neutral-400">期权隐含波动</div>
          <div className="text-lg font-semibold text-sky-300">{pct(impliedMove)}</div>
          <div className="mt-1 text-[11px] text-neutral-500">
            ATM straddle / 现价 · 财报隐含单日跳空
          </div>
        </div>

        {/* beat 习惯 */}
        <div className="rounded-lg border border-neutral-800 p-3">
          <div className="mb-1 text-xs text-neutral-400">beat 习惯</div>
          {beat && beat.n > 0 ? (
            <>
              <div className="text-sm text-neutral-200">
                beat 率 {beat.beat_rate != null ? `${(beat.beat_rate * 100).toFixed(0)}%` : "—"} · 连击{" "}
                {beat.streak} 季
              </div>
              <div className="mt-1 flex gap-0.5">
                {beat.rows.slice(0, 8).map((r) => (
                  <span
                    key={r.date}
                    title={`${r.date}: ${r.surprise_pct > 0 ? "+" : ""}${r.surprise_pct}%`}
                    className={`h-3 w-3 rounded-sm ${
                      r.surprise_pct > 0 ? "bg-emerald-500/70" : "bg-rose-500/70"
                    }`}
                  />
                ))}
              </div>
            </>
          ) : (
            <div className="text-sm text-neutral-500">无历史 surprise</div>
          )}
        </div>
      </div>

      {/* 近期 outcome 战绩 */}
      {recentOutcomes && recentOutcomes.length > 0 && (
        <div className="mt-3 flex flex-wrap items-center gap-2 text-xs text-neutral-400">
          <span>近期战绩:</span>
          {recentOutcomes.map((o) => {
            const hit = o.hit === true;
            const abstain = o.hit === "abstain";
            return (
              <span
                key={o.date}
                title={`${o.date} ${o.direction}@${o.conviction} 反应 ${o.reactionPct ?? "—"}%`}
                className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 ${
                  abstain
                    ? "bg-neutral-800 text-neutral-400"
                    : hit
                      ? "bg-emerald-500/15 text-emerald-300"
                      : "bg-rose-500/15 text-rose-300"
                }`}
              >
                {abstain ? "—" : hit ? <TrendingUp className="h-3 w-3" /> : <TrendingDown className="h-3 w-3" />}
                {o.reactionPct != null ? `${o.reactionPct >= 0 ? "+" : ""}${o.reactionPct}%` : o.date.slice(5)}
              </span>
            );
          })}
        </div>
      )}
    </Card>
  );
}
