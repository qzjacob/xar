import {
  Cpu,
  Database,
  Gauge,
  Layers3,
  Network,
  Plug,
  Workflow,
  type LucideIcon,
} from "lucide-react";
import { useNavigate } from "react-router-dom";
import { ops } from "../../lib/ops";
import { cn, relTime } from "../../lib/format";
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

const GROUP_LABEL: Record<string, string> = {
  platform: "Platform",
  ontology: "Ontology",
  sources: "Data Sources",
};

export function OpsOverviewPage() {
  const nav = useNavigate();
  const { data, loading, error } = useAsync(
    () =>
      Promise.all([
        ops.selftest(),
        ops.sources(),
        ops.datalake(),
        ops.llm(),
        ops.ontology(),
        ops.connectors(),
        ops.skills(),
      ]),
    [],
  );

  if (loading) return <OpsLoading />;
  if (error || !data) return <OpsContainer><OpsError error={error ?? "no data"} /></OpsContainer>;
  const [st, sr, dl, lm, ont, conn, sk] = data;

  const cards: { id: string; label: string; icon: LucideIcon; route: string; stat: string }[] = [
    { id: "ontology", label: "Ontology", icon: Network, route: "/ops/ontology",
      stat: `${ont.totals.nodes} nodes · ${ont.totals.edges} edges · ${ont.totals.events} events` },
    { id: "sources", label: "Data Sources", icon: Database, route: "/ops/sources",
      stat: `${sr.summary.available}/${sr.summary.total} active · ${sr.summary.rows.toLocaleString()} rows` },
    { id: "datalake", label: "Data Lake", icon: Layers3, route: "/ops/datalake",
      stat: `${dl.totals.documents} docs · ${dl.totals.chunks.toLocaleString()} chunks` },
    { id: "models", label: "Models & LLM", icon: Cpu, route: "/ops/models",
      stat: lm.configured ? `${lm.routing.strong} · $${lm.usage.total.usd}` : "not configured" },
    { id: "connectors", label: "MCP & API", icon: Plug, route: "/ops/connectors",
      stat: `${conn.summary.configured}/${conn.summary.outbound} connectors` },
    { id: "skills", label: "Agent Skills", icon: Workflow, route: "/ops/skills",
      stat: `${sk.summary.skills} skills · ${sk.summary.stages} stages` },
  ];

  const order = ["platform", "ontology", "sources"];
  const grouped = order.map((g) => ({ group: g, checks: st.checks.filter((c) => c.group === g) }));

  return (
    <OpsContainer>
      <OpsHeader
        title="Operations Overview"
        titleCn="控制台总览"
        icon={<Gauge size={18} strokeWidth={2} />}
        subtitle={`Self-test ${relTime(st.ranAt)} · backend control plane`}
      />

      {/* self-test summary pills */}
      <div className="mb-5 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
        {(["ok", "degraded", "unconfigured", "fail", "empty"] as const).map((k) => (
          <MetricPill
            key={k}
            label={k}
            value={
              <span className="inline-flex items-center gap-1.5">
                <StatusDot status={k} />
                {st.summary[k] ?? 0}
              </span>
            }
          />
        ))}
      </div>

      {/* section cards */}
      <div className="mb-5 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {cards.map((c) => {
          const Icon = c.icon;
          return (
            <button
              key={c.id}
              type="button"
              onClick={() => nav(c.route)}
              className="group flex items-center gap-3 rounded-xl border border-line bg-surface p-4 text-left shadow-card transition hover:border-accent/40 hover:shadow-pop focus-visible:ring-2 focus-visible:ring-accent/40"
            >
              <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg border border-line bg-canvas text-accent transition group-hover:border-accent/30">
                <Icon size={18} strokeWidth={2} />
              </span>
              <span className="min-w-0">
                <span className="block text-sm font-semibold text-brand-900">{c.label}</span>
                <span className="tnum block truncate text-2xs text-slate-400">{c.stat}</span>
              </span>
            </button>
          );
        })}
      </div>

      {/* self-test detail */}
      <Card>
        <SectionHeader
          title="System Self-Test"
          titleCn="跑通自检"
          icon={<Gauge size={15} strokeWidth={2} />}
          right={
            <Badge className="bg-surface-2 text-slate-400 ring-1 ring-inset ring-line">
              {st.checks.length} checks
            </Badge>
          }
        />
        <div className="divide-y divide-line">
          {grouped.map(({ group, checks }) =>
            checks.length === 0 ? null : (
              <div key={group} className="px-4 py-3">
                <div className="mb-2 text-2xs uppercase tracking-wide text-slate-400">
                  {GROUP_LABEL[group] ?? group}
                </div>
                <div className="grid grid-cols-1 gap-1.5 md:grid-cols-2">
                  {checks.map((c) => (
                    <div key={c.id} className="flex items-center gap-2 text-xs">
                      <StatusDot status={c.status} />
                      <span className="tnum w-40 shrink-0 truncate text-slate-400">{c.id}</span>
                      <Badge className={cn("shrink-0", statusChip(c.status))}>{c.status}</Badge>
                      <span className="min-w-0 truncate text-2xs text-slate-400">{c.detail}</span>
                    </div>
                  ))}
                </div>
              </div>
            ),
          )}
        </div>
      </Card>

      <p className="mt-3 text-2xs text-slate-400">
        Embedding <span className="tnum">{lm.routing.embedModel}</span> ({lm.routing.embedDim}d) ·{" "}
        <span className="tnum">{sr.summary.rows.toLocaleString()}</span> source rows under management
      </p>
    </OpsContainer>
  );
}
