import { useState } from "react";
import {
  Activity,
  BrainCircuit,
  Database,
  Layers3,
  MessageSquare,
  Play,
  Radar,
  Sparkles,
} from "lucide-react";
import { ops } from "../../lib/ops";
import { cn, polarityChip, relTime } from "../../lib/format";
import type { Polarity } from "../../types";
import { goodWhenLabel, scopeLabel, signalShort } from "../../types-alt";
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

      {/* structured high-frequency trackers (/api/ops/altdata/trackers) */}
      <AltTrackersSection />
    </OpsContainer>
  );
}

/**
 * Structured alt-data trackers — the numeric high-frequency signal layer
 * (revenue prints / chip exports / OSS momentum / hiring) as opposed to the
 * expert-processed narrative insights above. Self-fetching + degrades to
 * nothing if the trackers endpoint is absent.
 */
function AltTrackersSection() {
  const { data, loading, error } = useAsync(() => ops.altTrackers(), []);
  if (loading) {
    return (
      <div className="mt-6 flex h-24 items-center justify-center border-t border-line text-2xs text-slate-400">
        Loading trackers…
      </div>
    );
  }
  if (error || !data) return null;

  const signals = data.signals ?? [];
  const stock = data.stock ?? [];
  const coverage = data.coverage;
  const meta = new Map(signals.map((s) => [s.key, s]));
  const stockByKey = new Map(stock.map((s) => [s.signal_key, s]));
  const bySignal = Object.entries(coverage.by_signal ?? {}).sort((a, b) => b[1] - a[1]);
  const maxCov = Math.max(1, ...bySignal.map(([, v]) => v));
  const totalRows = stock.reduce((s, r) => s + r.rows, 0);

  return (
    <div className="mt-6 flex flex-col gap-5">
      <div className="flex items-center gap-2.5 border-t border-line pt-5">
        <span className="flex h-8 w-8 items-center justify-center rounded-lg border border-line bg-surface text-accent">
          <Radar size={16} strokeWidth={2} />
        </span>
        <div className="flex items-baseline gap-2">
          <h2 className="text-base font-semibold tracking-tight text-brand-900">Trackers</h2>
          <span className="text-2xs text-slate-400">追踪器 · 高频信号登记与库存</span>
        </div>
      </div>

      {/* summary strip */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <MetricPill label="Companies" value={coverage.companies} sub="绑定名单" />
        <MetricPill label="Signals" value={signals.length} sub="信号种类" />
        <MetricPill label="Theme-scope" value={(coverage.theme_signals ?? []).length} sub="链级信号" />
        <MetricPill label="Stock rows" value={totalRows.toLocaleString()} sub="库存记录" />
      </div>

      {/* coverage.by_signal bars */}
      <Card>
        <SectionHeader
          title="Signal Coverage"
          titleCn="信号覆盖 · 绑定名单数"
          icon={<Activity size={15} strokeWidth={2} />}
        />
        {bySignal.length === 0 ? (
          <div className="px-4 py-6 text-center text-2xs text-slate-400">No signal bindings yet.</div>
        ) : (
          <div className="flex flex-col gap-2 px-4 py-3">
            {bySignal.map(([key, count]) => {
              const m = meta.get(key);
              const st = stockByKey.get(key);
              return (
                <div key={key} className="flex items-center gap-3 text-xs">
                  <span
                    className="w-32 shrink-0 truncate font-medium text-brand-900"
                    title={key}
                  >
                    {m?.name_cn ?? signalShort(key).short}
                  </span>
                  <span className="tnum w-12 shrink-0 text-slate-500">{count} 家</span>
                  <div className="flex-1">
                    <ScoreBar value={(count / maxCov) * 100} scheme="good-high" />
                  </div>
                  <span className="tnum w-32 shrink-0 text-right text-2xs text-slate-400">
                    {st ? `${st.rows.toLocaleString()} rows · ${st.latest}` : "—"}
                  </span>
                </div>
              );
            })}
          </div>
        )}
      </Card>

      {/* signals registry table */}
      <Card>
        <SectionHeader
          title="Signal Registry"
          titleCn="信号登记 · 频率 / 范围 / 来源"
          icon={<Database size={15} strokeWidth={2} />}
          right={
            <Badge className="bg-surface-2 text-slate-400 ring-1 ring-inset ring-line">
              {signals.length}
            </Badge>
          }
        />
        {signals.length === 0 ? (
          <div className="px-4 py-6 text-center text-2xs text-slate-400">No signals registered.</div>
        ) : (
          <div className="scroll-thin overflow-x-auto">
            <table className="w-full min-w-[720px] text-xs">
              <thead>
                <tr className="border-b border-line text-left text-2xs uppercase tracking-wide text-slate-400">
                  <th className="px-4 py-2 font-medium">信号 Signal</th>
                  <th className="px-3 py-2 font-medium">Key</th>
                  <th className="px-3 py-2 font-medium">频率 Cadence</th>
                  <th className="px-3 py-2 font-medium">范围 Scope</th>
                  <th className="px-3 py-2 font-medium">方向 Direction</th>
                  <th className="px-3 py-2 font-medium">来源 Source</th>
                </tr>
              </thead>
              <tbody>
                {signals.map((s) => {
                  const scope = scopeLabel(s.scope);
                  const gw = goodWhenLabel(s.good_when);
                  return (
                    <tr key={s.key} className="border-b border-line/60 last:border-b-0">
                      <td className="px-4 py-2 font-medium text-brand-900">{s.name_cn}</td>
                      <td className="tnum px-3 py-2 font-mono text-2xs text-slate-500">
                        {s.key.replace(/^alt\./, "")}
                      </td>
                      <td className="px-3 py-2 text-slate-400">{s.cadence}</td>
                      <td className="px-3 py-2">
                        <Badge
                          className={
                            s.scope === "theme"
                              ? "bg-accent-50 text-accent-700 ring-1 ring-inset ring-accent/20"
                              : "bg-surface-2 text-slate-400 ring-1 ring-inset ring-line"
                          }
                        >
                          {scope.cn}
                        </Badge>
                      </td>
                      <td className="px-3 py-2 text-slate-400">{gw.cn}</td>
                      <td className="px-3 py-2 text-slate-400">{s.source}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {/* stock table */}
      <Card>
        <SectionHeader
          title="Data Stock"
          titleCn="数据库存 · 信号 × 记录 × 名单 × 最新"
          icon={<Layers3 size={15} strokeWidth={2} />}
        />
        {stock.length === 0 ? (
          <div className="px-4 py-6 text-center text-2xs text-slate-400">No stock yet.</div>
        ) : (
          <div className="scroll-thin overflow-x-auto">
            <table className="w-full min-w-[560px] text-xs">
              <thead>
                <tr className="border-b border-line text-left text-2xs uppercase tracking-wide text-slate-400">
                  <th className="px-4 py-2 font-medium">信号 Signal</th>
                  <th className="px-3 py-2 text-right font-medium">Rows</th>
                  <th className="px-3 py-2 text-right font-medium">Companies</th>
                  <th className="px-3 py-2 text-right font-medium">Latest</th>
                </tr>
              </thead>
              <tbody>
                {stock.map((r) => (
                  <tr key={r.signal_key} className="border-b border-line/60 last:border-b-0">
                    <td className="px-4 py-2">
                      <span className="font-medium text-brand-900">
                        {meta.get(r.signal_key)?.name_cn ?? signalShort(r.signal_key).short}
                      </span>
                      <span className="tnum ml-2 font-mono text-2xs text-slate-500">
                        {r.signal_key.replace(/^alt\./, "")}
                      </span>
                    </td>
                    <td className="tnum px-3 py-2 text-right font-semibold text-brand-900">
                      {r.rows.toLocaleString()}
                    </td>
                    <td className="tnum px-3 py-2 text-right text-slate-400">{r.companies}</td>
                    <td className="tnum px-3 py-2 text-right text-slate-400">{r.latest}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
