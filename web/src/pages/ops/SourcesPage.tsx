import { useState } from "react";
import { Database, Play, Loader2, CheckCircle2 } from "lucide-react";
import { ops } from "../../lib/ops";
import { cn, relTime } from "../../lib/format";
import type { SelfTest, SourceInfo } from "../../types-ops";
import { Badge, Card, MetricPill, SectionHeader } from "../../components/ui";
import {
  OpsContainer,
  OpsError,
  OpsHeader,
  OpsLoading,
  StatusDot,
  statusChip,
  useAsync,
} from "./_shared";

/** Permission → soft chip classes. green→pos, red→neg, else slate. */
function permissionChip(permission: string): string {
  const p = permission.toLowerCase();
  if (/(public|open|free|green|granted|licensed|ok)/.test(p))
    return "bg-pos-50 text-pos-700 ring-1 ring-inset ring-pos/20";
  if (/(restricted|blocked|denied|red|paid|forbidden)/.test(p))
    return "bg-neg-50 text-neg-700 ring-1 ring-inset ring-neg/20";
  return "bg-surface-2 text-brand-500 ring-1 ring-inset ring-line";
}

type RunState = "started" | "running";

/** Operations console: data sources control plane — inspect & RUN ingest jobs. */
export function SourcesPage() {
  const { data, loading, error, reload } = useAsync(() => ops.sources(), []);
  const [test, setTest] = useState<SelfTest | null>(null);
  const [testing, setTesting] = useState(false);
  const [runMap, setRunMap] = useState<Record<string, RunState>>({});

  const runSelfTest = () => {
    setTesting(true);
    ops
      .selftest()
      .then((t) => setTest(t))
      .catch(() => undefined)
      .finally(() => setTesting(false));
  };

  const runSource = (id: string) => {
    setRunMap((m) => ({ ...m, [id]: "started" }));
    ops
      .runSource(id)
      .then(() => {
        setRunMap((m) => ({ ...m, [id]: "running" }));
        window.setTimeout(() => {
          setRunMap((m) => {
            const next = { ...m };
            delete next[id];
            return next;
          });
          reload();
        }, 1500);
      })
      .catch(() => {
        setRunMap((m) => {
          const next = { ...m };
          delete next[id];
          return next;
        });
      });
  };

  return (
    <OpsContainer>
      <OpsHeader
        icon={<Database size={18} />}
        title="Data Sources"
        titleCn="数据源"
        subtitle="Ingest connectors & ETL jobs — inspect availability, keys and row counts; run sources on demand."
        right={
          <div className="flex items-center gap-2">
            {test && (
              <div className="flex items-center gap-1.5 text-2xs">
                {Object.entries(test.summary).map(([k, v]) => (
                  <Badge key={k} className={statusChip(k)}>
                    <StatusDot status={k} />
                    {v} {k}
                  </Badge>
                ))}
              </div>
            )}
            <button
              type="button"
              onClick={runSelfTest}
              disabled={testing}
              className={cn(
                "inline-flex items-center gap-1.5 rounded-lg border border-line bg-surface px-2.5 py-1.5 text-xs font-medium text-brand-900",
                "transition-colors hover:border-accent/40 hover:text-accent",
                testing && "cursor-not-allowed opacity-60",
              )}
            >
              {testing ? <Loader2 size={13} className="animate-spin" /> : <CheckCircle2 size={13} />}
              Self-test
            </button>
          </div>
        }
      />

      {loading && <OpsLoading />}
      {error && <OpsError error={error} />}

      {data && (
        <div className="flex flex-col gap-4">
          {/* summary strip */}
          <div className="flex flex-wrap gap-2">
            <MetricPill label="Sources" value={data.summary.total} sub="registered" />
            <MetricPill
              label="Available"
              value={`${data.summary.available}/${data.summary.total}`}
              sub="configured"
            />
            <MetricPill
              label="Total rows"
              value={data.summary.rows.toLocaleString()}
              sub="ingested"
            />
            <MetricPill label="Categories" value={data.categories.length} sub="groups" />
          </div>

          {/* sources table */}
          <Card>
            <SectionHeader
              icon={<Database size={14} />}
              title="Source registry"
              titleCn="数据源注册表"
              right={
                <span className="text-2xs text-brand-500">
                  {data.sources.filter((s) => s.runnable).length} runnable
                </span>
              }
            />
            <div className="overflow-x-auto">
              <table className="w-full min-w-[840px] text-sm">
                <thead>
                  <tr className="border-b border-line text-left text-2xs uppercase tracking-wide text-brand-500">
                    <th className="px-4 py-2 font-medium">Source</th>
                    <th className="px-4 py-2 font-medium">Category</th>
                    <th className="px-4 py-2 font-medium">Permission</th>
                    <th className="px-4 py-2 font-medium">Key</th>
                    <th className="px-4 py-2 text-right font-medium">Rows</th>
                    <th className="px-4 py-2 font-medium">Last run</th>
                    <th className="px-4 py-2 text-right font-medium">Run</th>
                  </tr>
                </thead>
                <tbody>
                  {[...data.sources]
                    .sort(
                      (a, b) =>
                        a.category.localeCompare(b.category) || a.name.localeCompare(b.name),
                    )
                    .map((s, i, arr) => {
                      const newGroup = i === 0 || arr[i - 1].category !== s.category;
                      return (
                        <SourceRow
                          key={s.id}
                          source={s}
                          showCategory={newGroup}
                          runState={runMap[s.id]}
                          onRun={() => runSource(s.id)}
                        />
                      );
                    })}
                </tbody>
              </table>
            </div>
          </Card>

          {/* self-test checks */}
          {test && (
            <Card>
              <SectionHeader
                icon={<CheckCircle2 size={14} />}
                title="Self-test checks"
                titleCn="自检结果"
                right={
                  <span className="text-2xs text-brand-500">
                    {test.checks.length} checks · {relTime(test.ranAt)}
                  </span>
                }
              />
              <ul className="divide-y divide-line">
                {test.checks.map((c) => (
                  <li key={c.id} className="flex items-center gap-3 px-4 py-2.5">
                    <Badge className={statusChip(c.status)}>
                      <StatusDot status={c.status} />
                      {c.status}
                    </Badge>
                    <span className="font-mono text-xs font-medium text-brand-900">{c.id}</span>
                    <span className="truncate text-2xs text-brand-200">{c.detail}</span>
                    <span className="ml-auto shrink-0 text-2xs uppercase tracking-wide text-brand-500">
                      {c.group}
                    </span>
                  </li>
                ))}
              </ul>
            </Card>
          )}
        </div>
      )}
    </OpsContainer>
  );
}

function SourceRow({
  source,
  showCategory,
  runState,
  onRun,
}: {
  source: SourceInfo;
  showCategory: boolean;
  runState?: RunState;
  onRun: () => void;
}) {
  const busy = runState != null;
  const canRun = source.runnable && source.available && !busy;
  return (
    <tr className="border-b border-line/60 last:border-0 hover:bg-canvas/60">
      <td className="px-4 py-2.5">
        <div className="flex items-start gap-2">
          <StatusDot
            status={source.available ? "ok" : "unconfigured"}
            className="mt-1.5"
          />
          <div className="min-w-0">
            <div className="truncate font-medium text-brand-900">{source.name}</div>
            <div className="truncate text-2xs text-brand-500" title={source.desc}>
              {source.desc}
            </div>
          </div>
        </div>
      </td>
      <td className="px-4 py-2.5 align-top">
        {showCategory ? (
          <Badge className="bg-surface-2 text-brand-500 ring-1 ring-inset ring-line">
            {source.category}
          </Badge>
        ) : (
          <span className="text-2xs text-brand-700">·</span>
        )}
      </td>
      <td className="px-4 py-2.5 align-top">
        <Badge className={permissionChip(source.permission)}>{source.permission}</Badge>
      </td>
      <td className="px-4 py-2.5 align-top">
        {source.keyEnv ? (
          <span className="font-mono text-2xs text-brand-200">{source.keyEnv}</span>
        ) : (
          <span className="text-2xs text-brand-700">—</span>
        )}
      </td>
      <td className="tnum px-4 py-2.5 text-right align-top text-brand-700">
        {source.rows > 0 ? source.rows.toLocaleString() : <span className="text-brand-700">—</span>}
      </td>
      <td className="px-4 py-2.5 align-top text-2xs text-brand-200">
        {source.lastRun ? relTime(source.lastRun) : <span className="text-brand-700">—</span>}
      </td>
      <td className="px-4 py-2.5 text-right align-top">
        {source.runnable ? (
          <button
            type="button"
            onClick={onRun}
            disabled={!canRun}
            className={cn(
              "inline-flex items-center gap-1 rounded-md px-2 py-1 text-2xs font-medium transition-colors",
              canRun
                ? "border border-accent/30 bg-accent-50 text-accent-700 hover:bg-accent-50/80"
                : busy
                  ? "border border-accent/20 bg-accent-50/60 text-accent-700"
                  : "cursor-not-allowed border border-line bg-canvas text-brand-700",
            )}
            title={
              !source.available
                ? "Source not configured"
                : busy
                  ? "Job in progress"
                  : "Run ingest job"
            }
          >
            {busy ? (
              <>
                <Loader2 size={11} className="animate-spin" />
                {runState === "started" ? "Started" : "Running"}
              </>
            ) : (
              <>
                <Play size={11} />
                Run
              </>
            )}
          </button>
        ) : (
          <span className="text-2xs text-brand-700">—</span>
        )}
      </td>
    </tr>
  );
}
