import { useState } from "react";
import { BrainCircuit, MessageSquare, Play, Sparkles } from "lucide-react";
import { ops } from "../../lib/ops";
import { cn, polarityChip, relTime } from "../../lib/format";
import type { Polarity } from "../../types";
import { Badge, Card, MetricPill, ScoreBar, SectionHeader } from "../../components/ui";
import { OpsContainer, OpsError, OpsHeader, OpsLoading, useAsync } from "./_shared";

const SOURCE_LABEL: Record<string, string> = {
  x: "X / Twitter",
  wechat: "微信公众号",
  news: "News",
  aifinmarket: "AIFINmarket",
  social: "Social",
  product: "Product",
};

export function AltDataPage() {
  const { data, loading, error, reload } = useAsync(() => ops.altdata(), []);
  const [running, setRunning] = useState(false);

  const runProcess = async () => {
    setRunning(true);
    try {
      await ops.processAltdata();
      setTimeout(() => {
        setRunning(false);
        reload();
      }, 2500);
    } catch {
      setRunning(false);
    }
  };

  if (loading) return <OpsLoading />;
  if (error || !data) return <OpsContainer><OpsError error={error ?? "no data"} /></OpsContainer>;
  const { stats, insights } = data;

  return (
    <OpsContainer>
      <OpsHeader
        title="Alt-Data Expert Processing"
        titleCn="另类数据专家加工"
        icon={<BrainCircuit size={18} strokeWidth={2} />}
        subtitle={`AI domain-expert refinement of X / WeChat / news / AIFINmarket → high-SNR ontology insights · quality gate ≥ ${stats.qualityMin}`}
        right={
          <button
            type="button"
            onClick={runProcess}
            disabled={running}
            className={cn(
              "inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium transition focus-visible:ring-2 focus-visible:ring-accent/40",
              running ? "bg-surface-2 text-slate-400" : "bg-surface text-white hover:bg-surface-2",
            )}
          >
            <Play size={13} strokeWidth={2.5} /> {running ? "Processing…" : "Process pending"}
          </button>
        }
      />

      {/* totals */}
      <div className="mb-5 grid grid-cols-2 gap-3 sm:grid-cols-4">
        <MetricPill label="Processed" value={stats.totals.processed} />
        <MetricPill
          label="Kept (high-SNR)"
          value={<span className="text-pos">{stats.totals.kept}</span>}
          sub={stats.totals.processed ? `${Math.round((stats.totals.kept / stats.totals.processed) * 100)}% keep-rate` : undefined}
        />
        <MetricPill label="Pending" value={stats.totals.pending} />
        <MetricPill label="Ontology events" value={stats.totals.expertEvents} sub="license=expert" />
      </div>

      {/* by source */}
      <Card className="mb-5">
        <SectionHeader title="By Source" titleCn="按来源" icon={<MessageSquare size={15} strokeWidth={2} />} />
        <div className="divide-y divide-line">
          {stats.bySource.length === 0 ? (
            <div className="px-4 py-6 text-center text-2xs text-slate-400">No alt-data processed yet.</div>
          ) : (
            stats.bySource.map((b) => (
              <div key={b.source} className="flex items-center gap-3 px-4 py-2.5 text-xs">
                <span className="w-32 shrink-0 font-medium text-brand-900">{SOURCE_LABEL[b.source] ?? b.source}</span>
                <span className="tnum w-24 shrink-0 text-slate-500">{b.kept}/{b.processed} kept</span>
                <div className="flex-1">
                  <ScoreBar value={b.avgQuality * 100} scheme="good-high" />
                </div>
                <span className="tnum w-16 shrink-0 text-right text-slate-500">q {b.avgQuality.toFixed(2)}</span>
              </div>
            ))
          )}
        </div>
      </Card>

      {/* top expert insights */}
      <Card>
        <SectionHeader
          title="Top Expert Insights"
          titleCn="高信噪比专家观点"
          icon={<Sparkles size={15} strokeWidth={2} />}
          right={
            <Badge className="bg-surface-2 text-slate-400 ring-1 ring-inset ring-line">
              {insights.length}
            </Badge>
          }
        />
        {insights.length === 0 ? (
          <div className="px-4 py-8 text-center text-sm text-slate-400">
            No kept insights yet — run processing over X / WeChat / AIFINmarket alt-data.
          </div>
        ) : (
          <ul className="divide-y divide-line">
            {insights.map((it, i) => (
              <li key={i} className="flex gap-3 px-4 py-3">
                <span
                  className="mt-0.5 w-0.5 shrink-0 self-stretch rounded-full"
                  style={{ backgroundColor: it.polarity === "positive" ? "#16A34A" : it.polarity === "negative" ? "#DC2626" : "#64748b" }}
                  aria-hidden="true"
                />
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <Badge className={polarityChip(it.polarity as Polarity)}>{it.catalystType}</Badge>
                    {it.company && (
                      <span className="tnum text-xs font-semibold text-brand-900">{it.company}</span>
                    )}
                    <Badge className="bg-surface-2 text-slate-400 ring-1 ring-inset ring-line">
                      {SOURCE_LABEL[it.source] ?? it.source}
                    </Badge>
                    {it.techRoute && (
                      <Badge className="bg-accent-50 text-accent-700 ring-1 ring-inset ring-accent/20">
                        {it.techRoute}
                      </Badge>
                    )}
                    <span className="tnum ml-auto shrink-0 text-2xs font-semibold text-pos">
                      q {it.signalQuality.toFixed(2)}
                    </span>
                  </div>
                  <p className="mt-1 text-sm leading-snug text-brand-900">{it.thesis}</p>
                  {it.ts && <div className="mt-0.5 text-2xs text-slate-400">{relTime(it.ts)}</div>}
                </div>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </OpsContainer>
  );
}
