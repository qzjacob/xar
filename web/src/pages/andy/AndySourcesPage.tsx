import { Database, Plug, RefreshCw, Timer } from "lucide-react";
import { andy } from "../../lib/andy";
import { cn } from "../../lib/format";
import { Badge, Card, SectionHeader } from "../../components/ui";
import { HardnessBadge } from "../../components/andy/constants";
import { AndyContainer, AndyError, AndyHeader, AndyLoading, useAsync } from "./_shared";
import type { AndyConnectorRun } from "../../types-andy";

/** "Mar 4, 09:12" — compact date+time for run timestamps; plain dates pass through. */
function fmtWhen(iso: string | null | undefined): string {
  if (!iso) return "—";
  if (!iso.includes("T") && !iso.includes(" ")) return iso; // date-only stays verbatim
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

function fmtInt(n: number): string {
  return n.toLocaleString("en-US");
}

/** Section-header refresh button — manual re-fetch, no polling. */
function RefreshButton({ onClick, loading }: { onClick: () => void; loading: boolean }) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={loading}
      title="手动刷新 · re-fetch"
      className={cn(
        "inline-flex items-center gap-1 rounded-md border border-line bg-surface-2 px-2 py-1 text-2xs font-medium text-brand-500",
        "transition-colors hover:bg-andy-50 hover:text-andy-500 disabled:cursor-not-allowed disabled:opacity-60",
      )}
    >
      <RefreshCw size={11} strokeWidth={2.5} className={cn(loading && "animate-spin")} />
      刷新
    </button>
  );
}

/** Last-run cell: status dot (ok / error / running-pulse) + start time + rows. */
function LastRunCell({ run }: { run: AndyConnectorRun | null }) {
  if (!run) return <span className="text-brand-200">—</span>;
  const dot =
    run.status === "ok" ? "bg-pos" : run.status === "error" ? "bg-neg" : "animate-pulse bg-warn";
  const title =
    run.status === "error"
      ? `error · ${run.error}`
      : run.status === "running"
        ? "运行中 · running"
        : `ok${run.finished_at ? ` · 完成 ${fmtWhen(run.finished_at)}` : ""}`;
  return (
    <span className="flex items-center gap-2" title={title}>
      <span className={cn("h-2 w-2 shrink-0 rounded-full", dot)} aria-hidden="true" />
      <span className="tnum whitespace-nowrap text-brand-500">{fmtWhen(run.started_at)}</span>
      <span className="tnum whitespace-nowrap text-2xs text-brand-200">
        {run.rows_written !== null ? `${fmtInt(run.rows_written)} 行` : "—"}
      </span>
    </span>
  );
}

/** /andy/sources — Sources 数据源: connector run health (key 配置 / last run /
 * observation counts) + per-metric freshness. Manual refresh only — no polling. */
export function AndySourcesPage() {
  const sourcesQ = useAsync(() => andy.sources(), []);

  if (sourcesQ.loading && !sourcesQ.data) return <AndyLoading label="Loading sources…" />;
  if (sourcesQ.error && !sourcesQ.data) return <AndyError error={sourcesQ.error} />;

  const connectors = sourcesQ.data?.connectors ?? [];
  const freshness = sourcesQ.data?.metrics_freshness ?? [];

  return (
    <AndyContainer wide>
      <AndyHeader
        icon={<Database size={18} />}
        title="Sources"
        titleCn="数据源"
        subtitle="连接器运行健康 + 指标新鲜度 — key 配置、最近一次拉取、最新 valid/knowledge 时间。"
        right={
          <Badge className="bg-surface-2 text-brand-500 ring-1 ring-inset ring-line">
            <span className="tnum">{connectors.length}</span> 连接器 ·{" "}
            <span className="tnum">{freshness.length}</span> 指标
          </Badge>
        }
      />

      <div className="flex flex-col gap-4">
        {/* 连接器 connectors */}
        <Card className="overflow-hidden">
          <SectionHeader
            title="Connectors"
            titleCn="连接器"
            icon={<Plug size={14} />}
            right={<RefreshButton onClick={sourcesQ.reload} loading={sourcesQ.loading} />}
          />
          <div className="scroll-thin overflow-x-auto">
            <table className="w-full min-w-[880px] border-collapse text-xs">
              <thead>
                <tr className="border-b border-line text-left text-2xs uppercase tracking-wide text-brand-200">
                  <th className="px-3 py-2 font-medium">连接器 Source</th>
                  <th className="px-3 py-2 font-medium">Key 凭证</th>
                  <th className="px-3 py-2 font-medium">最近运行 Last run</th>
                  <th className="px-3 py-2 text-right font-medium">观测数 Obs</th>
                  <th className="px-3 py-2 font-medium">指标 Metrics</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {connectors.map((c) => (
                  <tr key={c.source_id} className="transition-colors hover:bg-canvas">
                    <td className="px-3 py-2">
                      <span className="flex items-center gap-1.5">
                        <span className="font-mono text-xs text-brand-900">{c.source_id}</span>
                        {!c.is_primary && (
                          <Badge
                            className="bg-surface-2 text-brand-500 ring-1 ring-inset ring-line"
                            title="次源 · secondary source"
                          >
                            次源
                          </Badge>
                        )}
                      </span>
                    </td>
                    <td className="px-3 py-2">
                      {c.key_env === null ? (
                        <Badge
                          className="bg-andy-50 text-andy-500 ring-1 ring-inset ring-andy/25"
                          title="无需 API key · keyless connector"
                        >
                          零key
                        </Badge>
                      ) : (
                        <span className="flex items-center gap-1.5">
                          <Badge
                            className={
                              c.key_present
                                ? "bg-pos-50 text-pos-700 ring-1 ring-inset ring-pos/25"
                                : "bg-warn-50 text-warn-700 ring-1 ring-inset ring-warn/25"
                            }
                          >
                            {c.key_present ? "✓ key就绪" : "✗ 未配置"}
                          </Badge>
                          <span className="truncate font-mono text-2xs text-brand-200" title={c.key_env}>
                            {c.key_env}
                          </span>
                        </span>
                      )}
                    </td>
                    <td className="whitespace-nowrap px-3 py-2">
                      <LastRunCell run={c.last_run} />
                    </td>
                    <td className="tnum whitespace-nowrap px-3 py-2 text-right text-brand-900">
                      {fmtInt(c.observations)}
                    </td>
                    <td className="px-3 py-2">
                      <Badge
                        className="bg-surface-2 text-brand-500 ring-1 ring-inset ring-line"
                        title={c.metrics.length > 0 ? c.metrics.join(" · ") : undefined}
                      >
                        <span className="tnum">{c.metrics.length}</span> 指标
                      </Badge>
                    </td>
                  </tr>
                ))}
                {connectors.length === 0 && (
                  <tr>
                    <td colSpan={5} className="px-3 py-10 text-center text-brand-200">
                      无已注册连接器 · no connectors registered
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </Card>

        {/* 指标新鲜度 metric freshness */}
        <Card className="overflow-hidden">
          <SectionHeader
            title="Metric freshness"
            titleCn="指标新鲜度"
            icon={<Timer size={14} />}
            right={<RefreshButton onClick={sourcesQ.reload} loading={sourcesQ.loading} />}
          />
          <div className="scroll-thin overflow-x-auto">
            <table className="w-full min-w-[880px] border-collapse text-xs">
              <thead>
                <tr className="border-b border-line text-left text-2xs uppercase tracking-wide text-brand-200">
                  <th className="px-3 py-2 font-medium">指标 Metric</th>
                  <th className="px-3 py-2 font-medium">硬度</th>
                  <th className="px-3 py-2 text-right font-medium">观测数 Obs</th>
                  <th className="px-3 py-2 font-medium">最新 valid_time</th>
                  <th className="px-3 py-2 font-medium">最新 knowledge_time</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {freshness.map((m) => (
                  <tr
                    key={m.metric_key}
                    className={cn("transition-colors hover:bg-canvas", m.observations === 0 && "opacity-50")}
                  >
                    <td className="max-w-[320px] px-3 py-2">
                      <div className="truncate font-medium text-brand-900">{m.display_name_zh}</div>
                      <div className="truncate font-mono text-2xs text-brand-200">{m.metric_key}</div>
                    </td>
                    <td className="whitespace-nowrap px-3 py-2">
                      <HardnessBadge hardness={m.hardness} withEn={false} />
                    </td>
                    <td className="whitespace-nowrap px-3 py-2 text-right">
                      {m.observations === 0 ? (
                        <span className="text-2xs text-brand-200">无观测</span>
                      ) : (
                        <span className="tnum text-brand-900">{fmtInt(m.observations)}</span>
                      )}
                    </td>
                    <td className="tnum whitespace-nowrap px-3 py-2 text-brand-500">
                      {fmtWhen(m.latest_valid_time)}
                    </td>
                    <td className="tnum whitespace-nowrap px-3 py-2 text-brand-500">
                      {fmtWhen(m.latest_knowledge_time)}
                    </td>
                  </tr>
                ))}
                {freshness.length === 0 && (
                  <tr>
                    <td colSpan={5} className="px-3 py-10 text-center text-brand-200">
                      暂无指标新鲜度数据 · no freshness data
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </Card>
      </div>
    </AndyContainer>
  );
}
