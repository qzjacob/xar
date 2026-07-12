import {
  ArrowRight,
  Boxes,
  GitBranch,
  Network,
  Ruler,
  Workflow,
  Zap,
} from "lucide-react";
import type { ReactNode } from "react";
import { cn, heat } from "../../lib/format";
import { ops } from "../../lib/ops";
import type {
  OntologyCatalystType,
  OntologyEdgeType,
  OntologyMetric,
  OntologyNodeType,
} from "../../types-ops";
import { Badge, Card, ScoreBar, SectionHeader } from "../../components/ui";
import { OpsContainer, OpsError, OpsHeader, OpsLoading, useAsync } from "./_shared";

/** Known FinMetric providers — fixed column order for the coverage chips. */
const PROVIDERS = ["fmp", "finnhub", "yahoo"] as const;

const PROVIDER_CHIP: Record<string, string> = {
  fmp: "bg-accent-50 text-accent-700 ring-1 ring-inset ring-accent/20",
  finnhub: "bg-pos-50 text-pos-700 ring-1 ring-inset ring-pos/20",
  yahoo: "bg-warn-50 text-warn-700 ring-1 ring-inset ring-warn/20",
};

/** Monospace IRI cell — truncated, full value on hover. */
function Iri({ value, className }: { value: string; className?: string }) {
  return (
    <span
      title={value}
      className={cn("block truncate font-mono text-2xs text-brand-500", className)}
    >
      {value || "—"}
    </span>
  );
}

/** Count badge that subtly fades when a type is unpopulated in the live KG. */
function CountBadge({ count }: { count: number }) {
  return (
    <Badge
      className={cn(
        "tnum tabular-nums",
        count > 0
          ? "bg-surface-2 text-brand-500 ring-1 ring-inset ring-line"
          : "bg-surface-2 text-brand-700 ring-1 ring-inset ring-line",
      )}
    >
      {count.toLocaleString()}
    </Badge>
  );
}

function SectionCard({
  title,
  titleCn,
  icon,
  right,
  children,
}: {
  title: string;
  titleCn?: string;
  icon: ReactNode;
  right?: ReactNode;
  children: ReactNode;
}) {
  return (
    <Card>
      <SectionHeader title={title} titleCn={titleCn} icon={icon} right={right} />
      {children}
    </Card>
  );
}

export function OntologyPage() {
  const { data, loading, error } = useAsync(() => ops.ontology(), []);

  return (
    <OpsContainer>
      <OpsHeader
        title="Ontology"
        titleCn="本体 · 知识图谱"
        icon={<Network size={18} />}
        subtitle={
          data ? (
            <span className="flex flex-wrap items-center gap-x-3 gap-y-0.5">
              <span>
                <span className="tnum font-medium text-brand-200">
                  {data.totals.nodes.toLocaleString()}
                </span>{" "}
                nodes ·{" "}
                <span className="tnum font-medium text-brand-200">
                  {data.totals.edges.toLocaleString()}
                </span>{" "}
                edges ·{" "}
                <span className="tnum font-medium text-brand-200">
                  {data.totals.events.toLocaleString()}
                </span>{" "}
                events ·{" "}
                <span className="tnum font-medium text-brand-200">
                  {data.totals.aliases.toLocaleString()}
                </span>{" "}
                aliases
              </span>
              <span className="text-brand-700">|</span>
              <span>
                anchored to <span className="font-medium text-brand-200">{data.standards.fibo}</span>{" "}
                + <span className="font-medium text-brand-200">{data.standards.schema}</span>
              </span>
            </span>
          ) : (
            "code-as-truth knowledge graph · FIBO + schema.org"
          )
        }
        right={
          <Badge className="bg-brand-50 text-brand-700 ring-1 ring-inset ring-brand-200">
            code-as-truth
          </Badge>
        }
      />

      {loading && <OpsLoading />}
      {error && <OpsError error={error} />}

      {data && (
        <div className="flex flex-col gap-4">
          {/* Node + Edge types side by side */}
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            <NodeTypesCard rows={data.nodeTypes} />
            <EdgeTypesCard rows={data.edgeTypes} />
          </div>

          <CatalystTypesCard rows={data.catalystTypes} />
          <FinMetricsCard rows={data.finMetrics} />
          <SignalMapCard map={data.signalMap} />
        </div>
      )}
    </OpsContainer>
  );
}

function NodeTypesCard({ rows }: { rows: OntologyNodeType[] }) {
  return (
    <SectionCard
      title="Node Types"
      titleCn="节点类型"
      icon={<Boxes size={14} />}
      right={
        <span className="text-2xs text-brand-500">
          schema.org + FIBO · {rows.length}
        </span>
      }
    >
      <div className="divide-y divide-line">
        {rows.map((n) => (
          <div key={n.type} className="flex items-center gap-3 px-4 py-2.5">
            <div className="min-w-0 flex-1">
              <div className="flex items-baseline gap-2">
                <span className="truncate text-xs font-semibold text-brand-900">{n.type}</span>
              </div>
              <div className="mt-0.5 grid grid-cols-1 gap-0.5">
                <Iri value={n.schemaIri} />
                <Iri value={n.fiboIri} />
              </div>
            </div>
            <CountBadge count={n.count} />
          </div>
        ))}
        {rows.length === 0 && <Empty />}
      </div>
    </SectionCard>
  );
}

function EdgeTypesCard({ rows }: { rows: OntologyEdgeType[] }) {
  return (
    <SectionCard
      title="Edge Types"
      titleCn="关系类型"
      icon={<GitBranch size={14} />}
      right={<span className="text-2xs text-brand-500">relations · {rows.length}</span>}
    >
      <div className="divide-y divide-line">
        {rows.map((e) => (
          <div key={e.type} className="flex items-center gap-3 px-4 py-2.5">
            <div className="min-w-0 flex-1">
              <span className="block truncate text-xs font-semibold text-brand-900">{e.type}</span>
              <Iri value={e.iri} className="mt-0.5" />
            </div>
            <CountBadge count={e.count} />
          </div>
        ))}
        {rows.length === 0 && <Empty />}
      </div>
    </SectionCard>
  );
}

function CatalystTypesCard({ rows }: { rows: OntologyCatalystType[] }) {
  const max = Math.max(1, ...rows.map((r) => r.count));
  return (
    <SectionCard
      title="Catalyst Types"
      titleCn="催化剂类型"
      icon={<Zap size={14} />}
      right={<span className="text-2xs text-brand-500">event taxonomy · {rows.length}</span>}
    >
      <div className="grid grid-cols-1 gap-px bg-line sm:grid-cols-2 lg:grid-cols-3">
        {rows.map((c) => {
          const pct = (c.count / max) * 100;
          const tint = heat(pct, "good-high", 0.16);
          return (
            <div key={c.type} className="flex flex-col gap-1.5 bg-surface px-4 py-3">
              <div className="flex items-center justify-between gap-2">
                <span className="truncate text-xs font-semibold text-brand-900" title={c.label}>
                  {c.label}
                </span>
                <span
                  className="tnum rounded px-1.5 py-0.5 text-2xs font-semibold"
                  style={c.count > 0 ? tint : { color: "#9caabe" }}
                >
                  {c.count.toLocaleString()}
                </span>
              </div>
              <div className="font-mono text-2xs text-brand-500">{c.type}</div>
              <ScoreBar value={pct} scheme="good-high" height={4} />
            </div>
          );
        })}
        {rows.length === 0 && (
          <div className="bg-surface">
            <Empty />
          </div>
        )}
      </div>
    </SectionCard>
  );
}

function FinMetricsCard({ rows }: { rows: OntologyMetric[] }) {
  return (
    <SectionCard
      title="Canonical Financial Metrics"
      titleCn="标准财务指标 · FinMetric"
      icon={<Ruler size={14} />}
      right={<span className="text-2xs text-brand-500">{rows.length} metrics</span>}
    >
      <div className="overflow-x-auto">
        <table className="w-full min-w-[640px] text-left">
          <thead>
            <tr className="border-b border-line text-2xs uppercase tracking-wide text-brand-500">
              <th className="px-4 py-2 font-medium">Metric</th>
              <th className="px-3 py-2 font-medium">Type</th>
              <th className="px-3 py-2 font-medium">Provider Coverage</th>
              <th className="px-4 py-2 text-right font-medium">Count</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            {rows.map((m) => (
              <tr key={m.metric} className="hover:bg-canvas/60">
                <td className="px-4 py-2">
                  <span className="font-mono text-xs font-medium text-brand-900">{m.metric}</span>
                </td>
                <td className="px-3 py-2">
                  <Badge
                    className={
                      m.isRatio
                        ? "bg-accent-50 text-accent-700 ring-1 ring-inset ring-accent/20"
                        : "bg-surface-2 text-brand-500 ring-1 ring-inset ring-line"
                    }
                  >
                    {m.isRatio ? "ratio" : "level"}
                  </Badge>
                </td>
                <td className="px-3 py-2">
                  <div className="flex flex-wrap gap-1">
                    {PROVIDERS.map((p) => {
                      const has = m.providers.includes(p);
                      return (
                        <Badge
                          key={p}
                          className={
                            has
                              ? PROVIDER_CHIP[p]
                              : "bg-surface-2 text-brand-700 ring-1 ring-inset ring-line"
                          }
                        >
                          {p}
                        </Badge>
                      );
                    })}
                  </div>
                </td>
                <td className="px-4 py-2 text-right">
                  <span className="tnum text-xs font-semibold text-brand-500">
                    {m.count.toLocaleString()}
                  </span>
                </td>
              </tr>
            ))}
            {rows.length === 0 && (
              <tr>
                <td colSpan={4}>
                  <Empty />
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </SectionCard>
  );
}

function SignalMapCard({ map }: { map: Record<string, string> }) {
  const entries = Object.entries(map);
  return (
    <SectionCard
      title="Signal → Catalyst Map"
      titleCn="信号到催化剂桥接"
      icon={<Workflow size={14} />}
      right={
        <span className="text-2xs text-brand-500">structured → ontology · {entries.length}</span>
      }
    >
      <div className="grid grid-cols-1 gap-px bg-line md:grid-cols-2">
        {entries.map(([k, v]) => (
          <div key={k} className="flex items-center gap-2 bg-surface px-4 py-2.5">
            <span className="truncate font-mono text-2xs font-medium text-brand-500" title={k}>
              {k}
            </span>
            <ArrowRight size={13} className="shrink-0 text-brand-700" />
            <span className="truncate text-xs text-brand-900" title={v}>
              {v}
            </span>
          </div>
        ))}
        {entries.length === 0 && (
          <div className="bg-surface">
            <Empty />
          </div>
        )}
      </div>
    </SectionCard>
  );
}

function Empty() {
  return <div className="px-4 py-6 text-center text-2xs text-brand-500">No entries.</div>;
}
