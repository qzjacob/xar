import { useState } from "react";
import {
  Boxes,
  CheckCircle2,
  Cpu,
  DollarSign,
  Gauge,
  Loader2,
  Sparkles,
  Tag,
  XCircle,
  Zap,
} from "lucide-react";
import { ops } from "../../lib/ops";
import { cn } from "../../lib/format";
import type { LlmInfo, LlmTestResult, LlmUsageRow, LlmVendor } from "../../types-ops";
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

/** Compact USD formatter for prices and aggregate spend. */
function fmtUsd(n: number): string {
  if (n >= 100) return `$${n.toFixed(0)}`;
  if (n >= 1) return `$${n.toFixed(2)}`;
  if (n === 0) return "$0";
  return `$${n.toFixed(4)}`;
}

/** Thousands-grouped integer (tnum-friendly). */
function fmtInt(n: number): string {
  return n.toLocaleString("en-US");
}

/** Tokens → "1.2M" / "340K" / "812". */
function fmtTok(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

const VENDOR_ICON: Record<string, string> = {
  anthropic: "Anthropic",
  deepseek: "DeepSeek",
  openai: "OpenAI",
};

export function ModelsPage() {
  const { data, loading, error } = useAsync<LlmInfo>(() => ops.llm(), []);
  const [testing, setTesting] = useState(false);
  const [test, setTest] = useState<LlmTestResult | null>(null);

  async function runTest() {
    setTesting(true);
    try {
      const r = await ops.testLlm();
      setTest(r);
    } catch (e) {
      setTest({ ok: false, detail: String(e) });
    } finally {
      setTesting(false);
    }
  }

  const testRight = (
    <div className="flex items-center gap-2">
      {test && (
        <Badge
          className={cn(statusChip(test.ok ? "ok" : "fail"), "max-w-[18rem] gap-1")}
          title={test.ok ? test.reply : test.detail}
        >
          {test.ok ? <CheckCircle2 size={12} /> : <XCircle size={12} />}
          <span className="truncate">
            {test.ok
              ? `${test.model ? `${test.model} · ` : ""}${test.reply ?? "OK"}`
              : (test.detail ?? "failed")}
          </span>
        </Badge>
      )}
      <button
        type="button"
        onClick={runTest}
        disabled={testing}
        className={cn(
          "inline-flex items-center gap-1.5 rounded-lg border border-line bg-surface px-2.5 py-1.5 text-xs font-medium text-brand-900",
          "hover:border-accent/40 hover:text-accent disabled:cursor-not-allowed disabled:opacity-60",
        )}
      >
        {testing ? <Loader2 size={13} className="animate-spin" /> : <Zap size={13} />}
        Test LLM
      </button>
    </div>
  );

  return (
    <OpsContainer>
      <OpsHeader
        title="LLM Models"
        titleCn="模型路由"
        icon={<Cpu size={18} />}
        subtitle="Pluggable vendor/model layer · LiteLLM routing · token & cost telemetry"
        right={testRight}
      />

      {loading && <OpsLoading />}
      {error && <OpsError error={error} />}
      {data && !loading && !error && (
        <div className="space-y-5">
          <Routing info={data} />
          <Vendors info={data} />
          <Pricing info={data} />
          <Usage info={data} />
        </div>
      )}
    </OpsContainer>
  );
}

/* ----------------------------------------------------------------- Routing */

function Routing({ info }: { info: LlmInfo }) {
  const r = info.routing;
  return (
    <Card>
      <SectionHeader
        title="Routing"
        titleCn="路由策略"
        icon={<Gauge size={15} />}
        right={
          <Badge className={statusChip(info.configured ? "ok" : "unconfigured")}>
            <StatusDot status={info.configured ? "ok" : "unconfigured"} />
            {info.configured ? "configured" : "unconfigured"}
          </Badge>
        }
      />
      <div className="grid grid-cols-2 gap-2.5 p-4 sm:grid-cols-3 lg:grid-cols-6">
        <RoutePill label="Strong model" value={r.strong} icon={<Sparkles size={12} />} accent />
        <RoutePill label="Fast model" value={r.fast} icon={<Zap size={12} />} accent />
        <RoutePill label="Reasoning effort" value={r.effort} icon={<Gauge size={12} />} />
        <RoutePill label="Budget / run" value={fmtUsd(r.budgetUsdPerRun)} icon={<DollarSign size={12} />} />
        <RoutePill label="Embedding" value={r.embedModel} icon={<Boxes size={12} />} />
        <RoutePill label="Embed dim" value={fmtInt(r.embedDim)} icon={<Boxes size={12} />} />
      </div>
    </Card>
  );
}

function RoutePill({
  label,
  value,
  icon,
  accent = false,
}: {
  label: string;
  value: string;
  icon: React.ReactNode;
  accent?: boolean;
}) {
  return (
    <div className="rounded-lg border border-line bg-canvas px-2.5 py-2">
      <div className="flex items-center gap-1 text-2xs uppercase tracking-wide text-slate-400">
        <span className="text-slate-400">{icon}</span>
        {label}
      </div>
      <div
        className={cn(
          "tnum mt-1 truncate text-sm font-semibold leading-tight",
          accent ? "text-accent" : "text-brand-900",
        )}
        title={value}
      >
        {value}
      </div>
    </div>
  );
}

/* ----------------------------------------------------------------- Vendors */

function Vendors({ info }: { info: LlmInfo }) {
  const routed = new Set([info.routing.strong, info.routing.fast]);
  return (
    <Card>
      <SectionHeader
        title="Vendors"
        titleCn="模型供应商"
        icon={<Boxes size={15} />}
        right={
          <span className="tnum text-2xs text-slate-400">
            {info.vendors.filter((v) => v.configured).length}/{info.vendors.length} configured
          </span>
        }
      />
      <div className="grid grid-cols-1 gap-3 p-4 lg:grid-cols-3">
        {info.vendors.map((v) => (
          <VendorCard key={v.id} vendor={v} routed={routed} />
        ))}
      </div>
    </Card>
  );
}

function VendorCard({ vendor, routed }: { vendor: LlmVendor; routed: Set<string> }) {
  const status = vendor.configured ? "ok" : "unconfigured";
  return (
    <div className="flex flex-col gap-3 rounded-lg border border-line bg-canvas p-3.5">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="text-sm font-semibold text-brand-900">
            {VENDOR_ICON[vendor.id] ?? vendor.name}
          </div>
          <code className="mt-0.5 block truncate text-2xs text-slate-400" title={vendor.keyEnv}>
            {vendor.keyEnv}
          </code>
        </div>
        <Badge className={statusChip(status)}>
          <StatusDot status={status} />
          {vendor.configured ? "set" : "missing"}
        </Badge>
      </div>
      <div className="flex flex-wrap gap-1.5">
        {vendor.models.length === 0 && (
          <span className="text-2xs text-slate-400">no models registered</span>
        )}
        {vendor.models.map((m) => {
          const active = routed.has(m);
          return (
            <span
              key={m}
              className={cn(
                "tnum inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-2xs font-medium ring-1 ring-inset",
                active
                  ? "bg-accent-50 text-accent-700 ring-accent/30"
                  : "bg-surface text-slate-600 ring-line",
              )}
              title={m}
            >
              {m}
              {active && (
                <span className="rounded bg-accent px-1 text-[9px] font-semibold uppercase leading-4 tracking-wide text-white">
                  active
                </span>
              )}
            </span>
          );
        })}
      </div>
    </div>
  );
}

/* ----------------------------------------------------------------- Pricing */

function Pricing({ info }: { info: LlmInfo }) {
  const rows = info.prices;
  return (
    <Card>
      <SectionHeader
        title="Pricing"
        titleCn="单价"
        icon={<Tag size={15} />}
        right={<span className="tnum text-2xs text-slate-400">USD / 1M tokens</span>}
      />
      {rows.length === 0 ? (
        <Empty>No price sheet loaded.</Empty>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-line text-2xs uppercase tracking-wide text-slate-400">
                <th className="px-4 py-2 text-left font-medium">Model</th>
                <th className="px-4 py-2 text-right font-medium">Input $/1M</th>
                <th className="px-4 py-2 text-right font-medium">Output $/1M</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((p) => (
                <tr key={p.model} className="border-b border-line/60 last:border-0 hover:bg-canvas">
                  <td className="px-4 py-2 font-medium text-brand-900">{p.model}</td>
                  <td className="tnum px-4 py-2 text-right text-slate-600">{fmtUsd(p.inUsd)}</td>
                  <td className="tnum px-4 py-2 text-right text-slate-600">{fmtUsd(p.outUsd)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

/* ------------------------------------------------------------------- Usage */

function Usage({ info }: { info: LlmInfo }) {
  const { total, byModel } = info.usage;
  const rows: LlmUsageRow[] = [...byModel].sort((a, b) => b.usd - a.usd);
  return (
    <Card>
      <SectionHeader title="Usage" titleCn="用量与成本" icon={<DollarSign size={15} />} />
      <div className="grid grid-cols-3 gap-2.5 p-4">
        <MetricPill label="Total calls" value={fmtInt(total.calls)} />
        <MetricPill
          label="Tokens"
          value={fmtTok(total.inTok + total.outTok)}
          sub={`${fmtTok(total.inTok)} in · ${fmtTok(total.outTok)} out`}
        />
        <MetricPill label="Spend (USD)" value={fmtUsd(total.usd)} />
      </div>
      {rows.length === 0 ? (
        <Empty>No usage recorded yet.</Empty>
      ) : (
        <div className="overflow-x-auto border-t border-line">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-line text-2xs uppercase tracking-wide text-slate-400">
                <th className="px-4 py-2 text-left font-medium">Model</th>
                <th className="px-4 py-2 text-right font-medium">Calls</th>
                <th className="px-4 py-2 text-right font-medium">In tok</th>
                <th className="px-4 py-2 text-right font-medium">Out tok</th>
                <th className="px-4 py-2 text-right font-medium">USD</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((u) => (
                <tr key={u.model} className="border-b border-line/60 last:border-0 hover:bg-canvas">
                  <td className="px-4 py-2 font-medium text-brand-900">{u.model}</td>
                  <td className="tnum px-4 py-2 text-right text-slate-600">{fmtInt(u.calls)}</td>
                  <td className="tnum px-4 py-2 text-right text-slate-600">{fmtTok(u.inTok)}</td>
                  <td className="tnum px-4 py-2 text-right text-slate-600">{fmtTok(u.outTok)}</td>
                  <td className="tnum px-4 py-2 text-right font-semibold text-brand-900">
                    {fmtUsd(u.usd)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

function Empty({ children }: { children: React.ReactNode }) {
  return <div className="px-4 py-8 text-center text-2xs text-slate-400">{children}</div>;
}
