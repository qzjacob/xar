import { useMemo } from "react";
import { Link } from "react-router-dom";
import { ArrowRight, Cpu, GitBranch, Landmark } from "lucide-react";
import { andy } from "../../lib/andy";
import { cn } from "../../lib/format";
import { Badge, Card, SectionHeader, Sparkline } from "../../components/ui";
import { useAndy } from "../../components/andy/AndyLayout";
import { HardnessBadge, familyMeta } from "../../components/andy/constants";
import { AndyContainer, AndyError, AndyLoading, useAsync } from "./_shared";
import type { MacroMetricRow, MacroTransmissionEdge } from "../../types-andy";

/** /andy/macro — 宏观数据库台（AM）：以硅基经济学为核心的宏观外环。
 * (a) 9 个宏观族的批量 PIT 读数面板;(b) 传导链视图（利率→贴现→capex→算力…,
 * 端点勾到 Genny 主题与资金流台）;(c) 硅基核心族入口。全部经 as-of PIT 边界。 */

// 单位短后缀:混单位族(流动性 mil/bil 并存)不带单位裸数会误导量级
const UNIT_SUFFIX: Record<string, string> = {
  pct: "%", usd: "$", mil_usd: " M$", bil_usd: " B$", thousands: " k",
  usd_per_barrel: " $/bbl", usd_per_tonne: " $/t",
};

const fmtVal = (v: number | null, unit: string | null) => {
  if (v == null) return "—";
  const abs = Math.abs(v);
  const s = abs >= 100_000 ? Intl.NumberFormat("en", { notation: "compact" }).format(v)
    : abs >= 100 ? v.toFixed(1) : v.toFixed(2);
  return `${s}${UNIT_SUFFIX[unit ?? ""] ?? ""}`;
};

/** 斜率箭头着色:good_when × 斜率符号(与 macro_bridge 事件极性同口径)。 */
const slopeTone = (slope: number | null, goodWhen: "rising" | "falling" | null) => {
  if (slope == null || slope === 0 || goodWhen == null) return "text-brand-500";
  const aligned = goodWhen === "rising" ? slope : -slope;
  return aligned > 0 ? "text-pos" : "text-neg";
};

/** 火花线颜色:同一 good_when 口径——Sparkline 默认"涨绿跌红"会跟旁边的趋势箭头
 * 对着干(利率/OAS/VIX 这类 falling=利好的指标占一半);方向不定给中性灰。 */
const sparkColor = (m: MacroMetricRow) => {
  if (m.series.length < 2 || m.good_when == null) return "#9caabe";
  const up = m.series[m.series.length - 1].v >= m.series[0].v;
  const good = m.good_when === "rising" ? up : !up;
  return good ? "#2dc876" : "#f46060";
};

function MetricRow({ m, withAsOf }: { m: MacroMetricRow; withAsOf: (p: string) => string }) {
  return (
    <tr className="transition-colors hover:bg-canvas">
      <td className="max-w-[260px] px-3 py-1.5">
        <Link to={withAsOf(`/andy/metrics/${encodeURIComponent(m.metric_key)}`)}
          className="block truncate text-xs font-medium text-brand-800 hover:text-accent-100">
          {m.name_cn}
          {m.has_chain && (
            <GitBranch size={11} className="ml-1 inline text-accent-100" aria-label="有传导链" />
          )}
        </Link>
        <span className="block truncate font-mono text-2xs text-brand-200">{m.metric_key}</span>
      </td>
      <td className="tnum px-2 py-1.5 text-right text-sm font-semibold text-brand-900">
        {fmtVal(m.value, m.unit)}
      </td>
      <td className={cn("tnum px-2 py-1.5 text-right text-xs", slopeTone(m.slope, m.good_when))}>
        {m.slope == null ? "—" : m.slope > 0 ? "↗" : m.slope < 0 ? "↘" : "→"}
      </td>
      <td className="px-2 py-1.5">
        {m.series.length > 1 && (
          <Sparkline data={m.series.map((p) => p.v)} width={84} height={20} color={sparkColor(m)} />
        )}
      </td>
      <td className="tnum px-2 py-1.5 text-right text-2xs text-brand-200">{m.valid_time ?? "—"}</td>
    </tr>
  );
}

export function AndyMacroPage() {
  const { asOf, withAsOf } = useAndy();
  const macroQ = useAsync(() => andy.macro(asOf), [asOf]);

  const metricName = useMemo(() => {
    const map = new Map<string, string>();
    for (const f of macroQ.data?.families ?? []) {
      for (const m of f.metrics) map.set(m.metric_key, m.name_cn);
    }
    return map;
  }, [macroQ.data]);

  if (macroQ.loading) return <AndyLoading label="Loading macro database…" />;
  if (macroQ.error) return <AndyError error={macroQ.error} />;
  const d = macroQ.data;
  if (!d) return null;

  const nodeLabel = (k: string) => metricName.get(k) ?? d.labels[k] ?? k;
  const nodeLink = (k: string) =>
    k.startsWith("flow:") ? withAsOf("/andy/flow")
      : k.startsWith("theme:") ? "/genny"
        : withAsOf(`/andy/metrics/${encodeURIComponent(k)}`);
  const hasData = d.families.some((f) => f.metrics.some((m) => m.value != null));

  return (
    <AndyContainer wide>
      <div className="flex flex-col gap-4">
        {!hasData && (
          <div className="rounded-lg border border-dashed border-warn/40 bg-warn-50/40 px-3 py-2 text-2xs text-warn-700">
            宏观序列尚未回填 —— 工人 "andy_macro" 源(日频)或 `xar andy ingest --connector fred`
            完成首轮后此页填充实数据(FRED/ALFRED vintage,PIT 完备)。
          </div>
        )}

        {/* (a) 宏观族面板 */}
        {d.families.map((f) => {
          const meta = familyMeta(f.family);
          return (
            <Card key={f.family}>
              <SectionHeader
                title={meta.en}
                titleCn={meta.cn}
                icon={<Landmark size={15} strokeWidth={2} />}
                right={
                  <span className="flex items-center gap-2">
                    {/* 族级 hardness 徽章只在全族同 hardness 时展示(混族不冒充) */}
                    {new Set(f.metrics.map((m) => m.hardness)).size === 1 && (
                      <HardnessBadge hardness={f.metrics[0]?.hardness} withEn={false} />
                    )}
                    <Badge className="bg-surface-2 text-brand-500 ring-1 ring-inset ring-line">
                      {f.metrics.length}
                    </Badge>
                  </span>
                }
              />
              <div className="overflow-x-auto">
                <table className="w-full min-w-[620px] text-xs">
                  <thead>
                    <tr className="text-left text-2xs uppercase tracking-wide text-brand-200">
                      <th className="px-3 py-1.5 font-medium">指标</th>
                      <th className="px-2 py-1.5 text-right font-medium">最新值(PIT)</th>
                      <th className="px-2 py-1.5 text-right font-medium" title="末12期简单斜率;着色=good_when×方向">趋势</th>
                      <th className="px-2 py-1.5 font-medium">12 期</th>
                      <th className="px-2 py-1.5 text-right font-medium">观测期</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-line">
                    {f.metrics.map((m) => (
                      <MetricRow key={m.metric_key} m={m} withAsOf={withAsOf} />
                    ))}
                  </tbody>
                </table>
              </div>
            </Card>
          );
        })}

        {/* (b) 传导链视图 */}
        <Card>
          <SectionHeader
            title="Transmission Chains"
            titleCn="宏观传导链 · 外环闭合到硅基核心"
            icon={<GitBranch size={15} strokeWidth={2} />}
            right={
              <Badge className="bg-surface-2 text-brand-500 ring-1 ring-inset ring-line">
                {d.transmissions.length} 条
              </Badge>
            }
          />
          <ul className="divide-y divide-line">
            {d.transmissions.map((t: MacroTransmissionEdge, i: number) => (
              <li key={`${t.from}-${t.to}-${i}`} className="flex items-start gap-2 px-3 py-2" title={t.rationale_zh}>
                <div className="flex min-w-0 flex-wrap items-center gap-1.5 text-xs">
                  <Link to={nodeLink(t.from)} className="truncate font-medium text-brand-800 hover:text-accent-100">
                    {nodeLabel(t.from)}
                  </Link>
                  <span className={cn("tnum rounded px-1 text-2xs font-semibold ring-1 ring-inset",
                    t.sign === "+" ? "bg-pos/10 text-pos ring-pos/30"
                      : t.sign === "-" ? "bg-neg/10 text-neg ring-neg/30"
                        : "bg-surface-2 text-brand-500 ring-line")}>
                    {t.sign}
                  </span>
                  <ArrowRight size={12} className="shrink-0 text-brand-200" />
                  <Link to={nodeLink(t.to)} className="truncate font-medium text-brand-800 hover:text-accent-100">
                    {nodeLabel(t.to)}
                  </Link>
                  {t.lag_hint && <span className="tnum text-2xs text-brand-200">({t.lag_hint})</span>}
                </div>
              </li>
            ))}
          </ul>
          <div className="border-t border-line/60 px-3 py-1.5 text-2xs text-brand-200">
            悬停看传导机制;点端点进审讯页/资金流台/Genny。链式展开走 /api/andy/link/chain/:metric。
          </div>
        </Card>

        {/* (c) 硅基核心入口 */}
        <Card>
          <SectionHeader
            title="Silicon Core"
            titleCn="硅基经济学核心 · 理论本体族"
            icon={<Cpu size={15} strokeWidth={2} />}
          />
          <div className="flex flex-wrap gap-2 p-3">
            {d.silicon_families
              .slice()
              .sort((a, b) => familyMeta(a.family).order - familyMeta(b.family).order)
              .map((f) => (
                <Link
                  key={f.family}
                  to={withAsOf(`/andy/metrics?family=${encodeURIComponent(f.family)}`)}
                  className="flex items-center gap-2 rounded-lg border border-line bg-canvas px-3 py-1.5 text-xs transition-colors hover:border-accent/40"
                >
                  <span className="text-brand-800">{familyMeta(f.family).cn}</span>
                  <span className="tnum text-brand-200">{f.count}</span>
                </Link>
              ))}
          </div>
          <div className="border-t border-line/60 px-3 py-1.5 text-2xs text-brand-200">
            宏观是外环,硅基是核心:每条宏观指标的 mechanism 都写明对算力/能源/资本开支的传导,
            理论锚点 META_transmission / META_liquidity 与 A1-A8 同受三重词表把关。
          </div>
        </Card>
      </div>
    </AndyContainer>
  );
}
