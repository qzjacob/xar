import { ArrowDownToLine, ArrowUpFromLine, Plug, Webhook } from "lucide-react";
import { ops } from "../../lib/ops";
import { cn } from "../../lib/format";
import type { ConnectorsInfo, InboundGroup, OutboundConnector } from "../../types-ops";
import { Badge, Card, SectionHeader } from "../../components/ui";
import { OpsContainer, OpsError, OpsHeader, OpsLoading, StatusDot, useAsync } from "./_shared";

/** Soft chip tone for an outbound connector category (stable hash → palette). */
function categoryChip(category: string): string {
  const palette = [
    "bg-accent-50 text-accent-700 ring-1 ring-inset ring-accent/20",
    "bg-pos-50 text-pos-700 ring-1 ring-inset ring-pos/20",
    "bg-warn-50 text-warn-700 ring-1 ring-inset ring-warn/20",
    "bg-brand-50 text-brand-700 ring-1 ring-inset ring-brand-200/60",
    "bg-surface-2 text-brand-500 ring-1 ring-inset ring-line",
  ];
  let h = 0;
  for (let i = 0; i < category.length; i++) h = (h * 31 + category.charCodeAt(i)) >>> 0;
  return palette[h % palette.length];
}

/**
 * /ops/connectors — MCP & API interface registry.
 * Outbound = third-party APIs the platform consumes; Inbound = MCP-ready
 * endpoint groups the platform exposes to agents / external callers.
 */
export function ConnectorsPage() {
  const { data, loading, error } = useAsync<ConnectorsInfo>(() => ops.connectors(), []);

  if (loading) return <OpsLoading />;
  if (error) return <OpsError error={error} />;
  if (!data) return <OpsError error="No connector data" />;

  const { outbound, inbound, mcpNote, summary } = data;
  const mcpReady = outbound.filter((c) => c.mcp).length;

  return (
    <OpsContainer>
      <OpsHeader
        title="MCP & API Connectors"
        titleCn="接口注册表"
        icon={<Plug size={18} />}
        subtitle={
          <>
            {summary.configured}/{summary.outbound} outbound configured · {summary.inboundGroups}{" "}
            inbound groups · {mcpReady} MCP-ready. {mcpNote}
          </>
        }
      />

      {/* Outbound — APIs the platform consumes */}
      <Card>
        <SectionHeader
          title="Outbound API connectors"
          titleCn="对外数据源"
          icon={<ArrowUpFromLine size={14} />}
          right={
            <span className="tnum text-2xs text-brand-500">
              {summary.configured}/{summary.outbound} configured
            </span>
          }
        />
        {outbound.length === 0 ? (
          <div className="px-4 py-8 text-center text-2xs text-brand-500">No outbound connectors</div>
        ) : (
          <div className="divide-y divide-line">
            {outbound.map((c) => (
              <OutboundRow key={c.id} c={c} />
            ))}
          </div>
        )}
      </Card>

      {/* Inbound — MCP-ready endpoints the platform exposes */}
      <Card className="mt-4">
        <SectionHeader
          title="Inbound API (MCP-ready)"
          titleCn="对外暴露接口"
          icon={<ArrowDownToLine size={14} />}
          right={
            <span className="tnum text-2xs text-brand-500">{summary.inboundGroups} groups</span>
          }
        />
        {inbound.length === 0 ? (
          <div className="px-4 py-8 text-center text-2xs text-brand-500">No inbound groups</div>
        ) : (
          <div className="divide-y divide-line">
            {inbound.map((g) => (
              <InboundRow key={g.group} g={g} />
            ))}
          </div>
        )}
      </Card>
    </OpsContainer>
  );
}

function OutboundRow({ c }: { c: OutboundConnector }) {
  return (
    <div className="flex items-center gap-3 px-4 py-2.5">
      <StatusDot status={c.configured ? "ok" : "unconfigured"} className="shrink-0" />
      <div className="flex min-w-0 flex-1 items-center gap-2">
        <span className="shrink-0 text-sm font-medium text-brand-900">{c.name}</span>
        <span className="truncate font-mono text-2xs text-brand-500" title={c.baseUrl}>
          {c.baseUrl}
        </span>
      </div>
      <div className="flex shrink-0 items-center gap-2">
        {c.mcp && (
          <Badge className="bg-accent-50 text-accent-700 ring-1 ring-inset ring-accent/20" title="Exposed over MCP">
            <Webhook size={11} /> MCP
          </Badge>
        )}
        <Badge className={categoryChip(c.category)}>{c.category}</Badge>
        <span className="tnum w-16 shrink-0 text-right font-mono text-2xs text-brand-200" title={`auth: ${c.auth}`}>
          {c.auth}
        </span>
        <span
          className={cn(
            "w-20 shrink-0 text-right text-2xs font-medium",
            c.configured ? "text-pos-700" : "text-brand-500",
          )}
        >
          {c.configured ? "configured" : "no key"}
        </span>
      </div>
    </div>
  );
}

function InboundRow({ g }: { g: InboundGroup }) {
  return (
    <div className="px-4 py-3">
      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
        <span className="text-sm font-medium text-brand-900">{g.group}</span>
        <span className="tnum text-2xs text-brand-500">{g.endpoints.length} endpoints</span>
      </div>
      <div className="mt-0.5 text-2xs text-brand-200">{g.desc}</div>
      <div className="mt-2 flex flex-wrap gap-1.5">
        {g.endpoints.map((ep) => (
          <span
            key={ep}
            className="inline-flex items-center rounded-md bg-surface-2 px-1.5 py-0.5 font-mono text-2xs text-brand-500 ring-1 ring-inset ring-line"
          >
            {ep}
          </span>
        ))}
      </div>
    </div>
  );
}
