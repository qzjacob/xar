import { useState } from "react";
import {
  Database,
  ExternalLink,
  FileText,
  Layers3,
  RefreshCw,
  Search,
} from "lucide-react";
import { ops } from "../../lib/ops";
import { cn, relTime } from "../../lib/format";
import type { DataLakeInfo, LakeDocsPage } from "../../types-ops";
import { Badge, Card, MetricPill, SectionHeader } from "../../components/ui";
import {
  OpsContainer,
  OpsError,
  OpsHeader,
  OpsLoading,
  StatusDot,
  useAsync,
} from "./_shared";

const LIMIT = 25;

/** Soft chip classes for a document permission tier (green / grey / red). */
function permChip(permission: string): string {
  switch (permission) {
    case "green":
      return "bg-pos-50 text-pos-700 ring-1 ring-inset ring-pos/20";
    case "red":
      return "bg-neg-50 text-neg-700 ring-1 ring-inset ring-neg/20";
    default:
      return "bg-slate-100 text-slate-600 ring-1 ring-inset ring-slate-200";
  }
}
function permDot(permission: string): string {
  return permission === "green" ? "bg-pos" : permission === "red" ? "bg-neg" : "bg-slate-300";
}

function fmtInt(n: number | null | undefined): string {
  return (n ?? 0).toLocaleString("en-US");
}

/** Control-plane view for the unstructured data lake: corpus stats + document browser. */
export function DataLakePage() {
  // browser controls (local state drives the documents fetch)
  const [q, setQ] = useState("");
  const [qInput, setQInput] = useState("");
  const [source, setSource] = useState<string>("");
  const [offset, setOffset] = useState(0);
  const [processing, setProcessing] = useState(false);

  const lake = useAsync<DataLakeInfo>(() => ops.datalake(), []);
  const docs = useAsync<LakeDocsPage>(
    () => ops.documents({ limit: LIMIT, offset, source: source || undefined, q: q || undefined }),
    [offset, source, q],
  );

  async function onProcess() {
    setProcessing(true);
    try {
      await ops.process();
      setOffset(0);
      lake.reload();
      docs.reload();
    } finally {
      setProcessing(false);
    }
  }

  function applySearch() {
    setOffset(0);
    setQ(qInput.trim());
  }
  function pickSource(s: string) {
    setOffset(0);
    setSource((cur) => (cur === s ? "" : s));
  }

  const info = lake.data;
  const page = docs.data;
  const total = page?.total ?? 0;
  const showingFrom = total === 0 ? 0 : offset + 1;
  const showingTo = Math.min(offset + LIMIT, total);
  const canPrev = offset > 0;
  const canNext = offset + LIMIT < total;

  return (
    <OpsContainer>
      <OpsHeader
        title="Data Lake"
        titleCn="数据湖"
        icon={<Layers3 size={18} />}
        subtitle="Unstructured corpus — parse · chunk · embed · knowledge-graph extraction"
        right={
          <button
            type="button"
            onClick={onProcess}
            disabled={processing}
            className={cn(
              "inline-flex items-center gap-1.5 rounded-lg border border-accent/30 bg-accent-50 px-3 py-1.5 text-xs font-medium text-accent-700 transition hover:bg-accent-100 disabled:cursor-not-allowed disabled:opacity-60",
            )}
          >
            <RefreshCw size={13} className={cn(processing && "animate-spin")} />
            {processing ? "Processing…" : "Process pending"}
            {info && info.pending > 0 && !processing && (
              <span className="tnum rounded bg-accent/15 px-1 py-px text-2xs font-semibold text-accent-700">
                {fmtInt(info.pending)}
              </span>
            )}
          </button>
        }
      />

      {lake.loading ? (
        <OpsLoading />
      ) : lake.error ? (
        <OpsError error={lake.error} />
      ) : info ? (
        <div className="flex flex-col gap-4">
          {/* corpus totals */}
          <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-3 lg:grid-cols-5">
            <MetricPill label="Documents" value={fmtInt(info.totals.documents)} />
            <MetricPill label="Chunks" value={fmtInt(info.totals.chunks)} />
            <MetricPill label="Parsed" value={fmtInt(info.totals.parsed)} />
            <MetricPill label="Extracted" value={fmtInt(info.totals.extracted)} />
            <MetricPill
              label="Pending"
              value={fmtInt(info.pending)}
              className={info.pending > 0 ? "border-accent/30 bg-accent-50" : undefined}
            />
          </div>

          {/* by source + by permission */}
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
            <Card className="lg:col-span-2">
              <SectionHeader
                title="By source"
                titleCn="按来源"
                icon={<Database size={14} />}
                right={
                  <span className="tnum text-2xs text-slate-400">{info.bySource.length} sources</span>
                }
              />
              <div className="flex flex-col">
                {info.bySource.length === 0 ? (
                  <div className="px-4 py-8 text-center text-xs text-slate-400">No documents yet.</div>
                ) : (
                  info.bySource.map((s) => {
                    const max = Math.max(1, ...info.bySource.map((x) => x.docs));
                    const pct = Math.round((s.docs / max) * 100);
                    const parsedPct = s.docs > 0 ? Math.round((s.parsed / s.docs) * 100) : 0;
                    return (
                      <div
                        key={s.source}
                        className="flex items-center gap-3 border-b border-line px-4 py-2 last:border-b-0"
                      >
                        <span className="w-28 shrink-0 truncate text-xs font-medium text-slate-700">
                          {s.source}
                        </span>
                        <div className="relative h-2 flex-1 overflow-hidden rounded-full bg-canvas">
                          <div
                            className="absolute inset-y-0 left-0 rounded-full bg-accent/70"
                            style={{ width: `${pct}%` }}
                          />
                        </div>
                        <span className="tnum w-14 shrink-0 text-right text-2xs font-semibold text-brand-900">
                          {fmtInt(s.docs)}
                        </span>
                        <span
                          className="tnum w-20 shrink-0 text-right text-2xs text-slate-400"
                          title="parsed / docs"
                        >
                          {fmtInt(s.parsed)} ({parsedPct}%)
                        </span>
                        <span
                          className="tnum w-16 shrink-0 text-right text-2xs text-slate-400"
                          title="chunks"
                        >
                          {fmtInt(s.chunks)} ch
                        </span>
                      </div>
                    );
                  })
                )}
              </div>
            </Card>

            <Card>
              <SectionHeader title="By permission" titleCn="按授权" />
              <div className="flex flex-col gap-2 p-4">
                {info.byPermission.length === 0 ? (
                  <div className="py-4 text-center text-xs text-slate-400">—</div>
                ) : (
                  info.byPermission.map((p) => (
                    <div key={p.permission} className="flex items-center justify-between">
                      <Badge className={permChip(p.permission)}>
                        <span className={cn("h-1.5 w-1.5 rounded-full", permDot(p.permission))} />
                        {p.permission}
                      </Badge>
                      <span className="tnum text-sm font-semibold text-brand-900">{fmtInt(p.c)}</span>
                    </div>
                  ))
                )}
                <div className="mt-1 border-t border-line pt-2 text-2xs text-slate-400">
                  green = licensed · grey = derived/observed · red = restricted
                </div>
              </div>
            </Card>
          </div>

          {/* document browser */}
          <Card>
            <SectionHeader
              title="Documents"
              titleCn="文档浏览"
              icon={<FileText size={14} />}
              right={
                <span className="tnum text-2xs text-slate-400">
                  {total > 0 ? `${showingFrom}–${showingTo} of ${fmtInt(total)}` : "0 results"}
                </span>
              }
            />

            {/* controls: search + source filter */}
            <div className="flex flex-col gap-3 border-b border-line px-4 py-3">
              <div className="flex flex-wrap items-center gap-2">
                <div className="relative flex-1 min-w-[200px]">
                  <Search
                    size={14}
                    className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400"
                  />
                  <input
                    value={qInput}
                    onChange={(e) => setQInput(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") applySearch();
                    }}
                    placeholder="Search title / content…"
                    className="w-full rounded-lg border border-line bg-surface py-1.5 pl-8 pr-3 text-xs text-brand-900 placeholder:text-slate-400 focus:border-accent/40"
                  />
                </div>
                <button
                  type="button"
                  onClick={applySearch}
                  className="rounded-lg border border-line bg-surface px-3 py-1.5 text-xs font-medium text-slate-600 transition hover:bg-canvas"
                >
                  Search
                </button>
                {(q || source) && (
                  <button
                    type="button"
                    onClick={() => {
                      setQ("");
                      setQInput("");
                      setSource("");
                      setOffset(0);
                    }}
                    className="rounded-lg px-2 py-1.5 text-2xs font-medium text-slate-400 transition hover:text-neg"
                  >
                    Clear
                  </button>
                )}
              </div>

              <div className="flex flex-wrap items-center gap-1.5">
                <button
                  type="button"
                  onClick={() => {
                    setSource("");
                    setOffset(0);
                  }}
                  className={cn(
                    "rounded-md px-2 py-1 text-2xs font-medium transition",
                    source === ""
                      ? "bg-brand-900 text-white"
                      : "border border-line bg-surface text-slate-500 hover:bg-canvas",
                  )}
                >
                  All sources
                </button>
                {info.bySource.map((s) => (
                  <button
                    key={s.source}
                    type="button"
                    onClick={() => pickSource(s.source)}
                    className={cn(
                      "rounded-md px-2 py-1 text-2xs font-medium transition",
                      source === s.source
                        ? "bg-accent text-white"
                        : "border border-line bg-surface text-slate-500 hover:bg-canvas",
                    )}
                  >
                    {s.source}
                  </button>
                ))}
              </div>
            </div>

            {/* table */}
            {docs.loading ? (
              <div className="px-4 py-10 text-center text-sm text-slate-400">Loading…</div>
            ) : docs.error ? (
              <div className="px-4 py-6 text-center text-xs text-neg">{docs.error}</div>
            ) : !page || page.documents.length === 0 ? (
              <div className="px-4 py-10 text-center text-sm text-slate-400">
                No documents match the current filter.
              </div>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full border-collapse text-left">
                  <thead>
                    <tr className="border-b border-line text-2xs uppercase tracking-wide text-slate-400">
                      <th className="px-4 py-2 font-medium">Source</th>
                      <th className="px-2 py-2 font-medium">Type</th>
                      <th className="px-2 py-2 font-medium">Title</th>
                      <th className="px-2 py-2 font-medium">Perm</th>
                      <th className="px-2 py-2 text-right font-medium">Chars</th>
                      <th className="px-2 py-2 text-right font-medium">Chunks</th>
                      <th className="px-2 py-2 text-center font-medium">KG</th>
                      <th className="px-4 py-2 text-right font-medium">Ingested</th>
                    </tr>
                  </thead>
                  <tbody>
                    {page.documents.map((d) => (
                      <tr
                        key={d.id}
                        className="border-b border-line text-xs last:border-b-0 hover:bg-canvas/60"
                      >
                        <td className="px-4 py-2">
                          <Badge className="bg-slate-100 text-slate-600 ring-1 ring-inset ring-slate-200">
                            {d.source}
                          </Badge>
                        </td>
                        <td className="px-2 py-2 text-slate-500">{d.doc_type ?? "—"}</td>
                        <td className="max-w-[280px] px-2 py-2">
                          <div className="flex items-center gap-1">
                            <span
                              className="truncate font-medium text-brand-900"
                              title={d.title ?? undefined}
                            >
                              {d.title ?? <span className="text-slate-400">untitled</span>}
                            </span>
                            {d.url && (
                              <a
                                href={d.url}
                                target="_blank"
                                rel="noreferrer"
                                className="shrink-0 text-slate-400 transition hover:text-accent"
                                title="Open source"
                              >
                                <ExternalLink size={12} />
                              </a>
                            )}
                          </div>
                        </td>
                        <td className="px-2 py-2">
                          <Badge className={permChip(d.permission)}>
                            <span
                              className={cn("h-1.5 w-1.5 rounded-full", permDot(d.permission))}
                            />
                            {d.permission}
                          </Badge>
                        </td>
                        <td className="tnum px-2 py-2 text-right text-slate-500">
                          {d.chars != null ? fmtInt(d.chars) : "—"}
                        </td>
                        <td className="tnum px-2 py-2 text-right text-slate-700">
                          {fmtInt(d.chunks)}
                        </td>
                        <td className="px-2 py-2 text-center">
                          <span className="inline-flex items-center justify-center">
                            <StatusDot status={d.extracted ? "ok" : "unconfigured"} />
                          </span>
                        </td>
                        <td className="tnum px-4 py-2 text-right text-slate-400">
                          {d.ingested_at ? relTime(d.ingested_at) : "—"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            {/* pagination */}
            <div className="flex items-center justify-between border-t border-line px-4 py-2.5">
              <span className="tnum text-2xs text-slate-400">
                {total > 0 ? `Showing ${showingFrom}–${showingTo} of ${fmtInt(total)}` : "—"}
              </span>
              <div className="flex items-center gap-1.5">
                <button
                  type="button"
                  disabled={!canPrev || docs.loading}
                  onClick={() => setOffset((o) => Math.max(0, o - LIMIT))}
                  className="rounded-lg border border-line bg-surface px-3 py-1 text-xs font-medium text-slate-600 transition hover:bg-canvas disabled:cursor-not-allowed disabled:opacity-40"
                >
                  Prev
                </button>
                <button
                  type="button"
                  disabled={!canNext || docs.loading}
                  onClick={() => setOffset((o) => o + LIMIT)}
                  className="rounded-lg border border-line bg-surface px-3 py-1 text-xs font-medium text-slate-600 transition hover:bg-canvas disabled:cursor-not-allowed disabled:opacity-40"
                >
                  Next
                </button>
              </div>
            </div>
          </Card>
        </div>
      ) : null}
    </OpsContainer>
  );
}
