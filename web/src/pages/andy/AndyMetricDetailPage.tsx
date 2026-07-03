import { useState, type ReactNode } from "react";
import { Link, useParams } from "react-router-dom";
import { ArrowLeft, ChevronRight, Crosshair, LineChart, Link2, Microscope } from "lucide-react";
import { andy } from "../../lib/andy";
import { cn } from "../../lib/format";
import { PlotlyChart } from "../../components/charts/PlotlyChart";
import { Badge, Card, MetricPill, SectionHeader } from "../../components/ui";
import { useAndy } from "../../components/andy/AndyLayout";
import {
  AnchorChip,
  HardnessBadge,
  SoftWatermark,
  fmtMetric,
  slopeInfo,
  sourceGradeLabel,
} from "../../components/andy/constants";
import { AndyContainer, AndyError, AndyLoading, LinkUnavailable, useAsync } from "./_shared";

const N_POINTS_OPTIONS = [4, 8, 12, 24, 36, 60];

/** /andy/metrics/:key — Metric interrogation 审讯页: point-in-time chart +
 * identification notes + 勾稽 industry-chain crosswalk. */
export function AndyMetricDetailPage() {
  const { key = "" } = useParams<{ key: string }>();
  const { asOf, withAsOf } = useAndy();
  const [nPoints, setNPoints] = useState(12);

  const readingQ = useAsync(() => andy.metric(key, asOf, nPoints), [key, asOf, nPoints]);
  const linkQ = useAsync(() => andy.linkMetric(key), [key]);

  if (readingQ.loading && !readingQ.data) return <AndyLoading label="Interrogating…" />;
  if (readingQ.error || !readingQ.data) {
    return (
      <AndyContainer>
        <Card className="flex flex-col items-center gap-3 px-6 py-14 text-center">
          <Crosshair size={26} strokeWidth={1.75} className="text-slate-400" />
          <div className="text-sm font-semibold text-brand-900">指标不存在或加载失败</div>
          <div className="font-mono text-2xs text-slate-500">{readingQ.error ?? key}</div>
          <Link
            to={withAsOf("/andy/metrics")}
            className="mt-1 inline-flex items-center gap-1.5 rounded-lg border border-line bg-canvas px-3 py-1.5 text-xs font-medium text-brand-900 transition hover:bg-surface"
          >
            <ArrowLeft size={14} strokeWidth={2} /> 返回指标库
          </Link>
        </Card>
      </AndyContainer>
    );
  }

  const r = readingQ.data;
  const reg = r.registry;
  const isSoft = reg.hardness === "soft";
  const isWall = reg.hardness === "wall" || reg.is_quantifiable === false;
  const series = r.series ?? [];
  const goodWhen = linkQ.data?.good_when ?? null;
  const slope = slopeInfo(r.slope, goodWhen);

  return (
    <AndyContainer wide>
      <div className="flex flex-col gap-4">
        {/* breadcrumb */}
        <nav className="flex items-center gap-1 text-2xs uppercase tracking-wide text-slate-400">
          <Link to={withAsOf("/andy")} className="transition hover:text-brand-900">Andy</Link>
          <ChevronRight size={12} strokeWidth={2} className="text-slate-300" />
          <Link to={withAsOf("/andy/metrics")} className="transition hover:text-brand-900">指标库</Link>
          <ChevronRight size={12} strokeWidth={2} className="text-slate-300" />
          <span className="font-mono normal-case text-slate-500">{r.metric_key}</span>
        </nav>

        {/* header */}
        <Card className="p-5">
          <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
            <h1 className="text-xl font-semibold tracking-tight text-brand-900">{reg.display_name_zh}</h1>
            <span className="font-mono text-xs text-slate-500">{reg.metric_key}</span>
            <HardnessBadge hardness={reg.hardness} />
            {reg.theory_anchor.map((a) => (
              <AnchorChip key={a} anchor={a} />
            ))}
          </div>
          <div className="mt-3 flex flex-wrap gap-2">
            <MetricPill label="约束稀缺 Scarcity" value={reg.binding_scarcity ?? "—"} />
            <MetricPill label="Phase" value={reg.phase ?? "—"} />
            <MetricPill label="Geo" value={reg.geo_scope ?? "—"} />
            <MetricPill
              label="来源级 Source"
              value={sourceGradeLabel(reg.source_grade)}
              sub={reg.source_grade ?? undefined}
            />
            <MetricPill label="Status" value={reg.status ?? "—"} />
          </div>
          {isSoft && (
            <SoftWatermark watermark={r.identification.watermark} className="mt-3" />
          )}
        </Card>

        <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
          {/* PIT chart */}
          <Card className="xl:col-span-2">
            <SectionHeader
              title="Point-in-time Series"
              titleCn={`观测序列 · as-of ${r.as_of}`}
              icon={<LineChart size={15} strokeWidth={2} />}
              right={
                <div className="flex items-center gap-1">
                  <span className="mr-1 text-2xs uppercase tracking-wide text-slate-500">points</span>
                  {N_POINTS_OPTIONS.map((n) => (
                    <button
                      key={n}
                      type="button"
                      onClick={() => setNPoints(n)}
                      className={cn(
                        "tnum rounded-md px-1.5 py-0.5 text-2xs font-medium ring-1 ring-inset transition-colors",
                        n === nPoints
                          ? "bg-andy-50 text-andy-500 ring-andy/30"
                          : "bg-surface text-slate-500 ring-line hover:bg-surface-2",
                      )}
                    >
                      {n}
                    </button>
                  ))}
                </div>
              }
            />
            <div className="p-4">
              {/* value + slope readout */}
              <div className="mb-3 flex flex-wrap items-baseline gap-x-4 gap-y-1">
                <span className="tnum text-3xl font-semibold leading-none text-brand-900">
                  {fmtMetric(r.value)}
                </span>
                {r.unit && <span className="text-xs text-slate-400">{r.unit}</span>}
                <span className={cn("tnum text-sm font-semibold", slope.cls)} title="linear slope over the PIT window">
                  {slope.arrow} {slope.label}
                </span>
                {goodWhen && (
                  <span className="text-2xs text-slate-500">
                    good_when · {goodWhen === "rising" ? "上行利好" : "下行利好"}
                  </span>
                )}
              </div>

              {isWall ? (
                <div className="flex h-48 flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-line bg-surface-2/50 text-center">
                  <span className="rounded bg-surface-2 px-2 py-1 text-xs font-semibold text-slate-400 ring-1 ring-inset ring-line">
                    🧱 不可量化 · value 恒为 NULL
                  </span>
                  <span className="max-w-md px-4 text-2xs leading-relaxed text-slate-500">
                    {r.note ?? "承重墙不可量化项：无数值读数，仅作定性边界。"}
                  </span>
                </div>
              ) : series.length >= 2 ? (
                <PlotlyChart
                  height={280}
                  data={[
                    {
                      x: series.map((p) => p.valid_time),
                      y: series.map((p) => p.value),
                      type: "scatter",
                      mode: "lines+markers",
                      line: { color: "#2dd4bf", width: 2 },
                      marker: { size: 5, color: "#2dd4bf" },
                      hovertemplate: "%{x}<br>%{y}<extra></extra>",
                      name: reg.display_name_zh,
                    },
                  ]}
                  layout={{ showlegend: false, yaxis: { title: { text: r.unit ?? "", font: { size: 10 } } } }}
                />
              ) : (
                <div className="flex h-48 flex-col items-center justify-center gap-1.5 rounded-lg border border-dashed border-line text-center">
                  <span className="text-xs font-medium text-slate-400">暂无观测 · no observations</span>
                  <span className="max-w-md px-4 text-2xs text-slate-500">
                    {r.note ?? `as_of=${r.as_of} 之前无可用读数（point-in-time 视图为空）。`}
                  </span>
                </div>
              )}

              <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1 text-2xs text-slate-500">
                <span>unit · {r.unit ?? "—"}</span>
                <span>geo · {reg.geo_scope ?? "—"}</span>
                <span>point_in_time · knowledge_time ≤ {r.as_of}</span>
                {isSoft && <span className="text-warn-700">soft · 相关≠因果</span>}
              </div>
            </div>
          </Card>

          {/* interrogation panel */}
          <Card>
            <SectionHeader
              title="Interrogation"
              titleCn="识别注记"
              icon={<Microscope size={15} strokeWidth={2} />}
            />
            <div className="flex flex-col gap-3 p-4 text-xs leading-relaxed">
              <div>
                <div className="text-2xs uppercase tracking-wide text-slate-500">机制 Mechanism</div>
                <p className="mt-0.5 text-brand-800">{reg.mechanism ?? "—"}</p>
              </div>
              <div>
                <div className="text-2xs uppercase tracking-wide text-slate-500">
                  识别策略 Identification strategy
                </div>
                <p className={cn("mt-0.5", reg.identification_strategy ? "text-brand-800" : "text-slate-500")}>
                  {reg.identification_strategy ?? "—（未声明识别策略）"}
                </p>
              </div>
              {reg.caveat && (
                <div className="rounded-lg border border-warn/30 bg-warn-50 px-2.5 py-2">
                  <div className="text-2xs font-semibold uppercase tracking-wide text-warn-700">Caveat 口径限度</div>
                  <p className="mt-0.5 text-warn-100">{reg.caveat}</p>
                </div>
              )}
              {reg.falsification_condition && (
                <div className="rounded-lg border border-andy/25 bg-andy-50/60 px-2.5 py-2">
                  <div className="text-2xs font-semibold uppercase tracking-wide text-andy-500">
                    证伪条件 Falsification
                  </div>
                  <p className="mt-0.5 text-brand-800">{reg.falsification_condition}</p>
                </div>
              )}
              <div className="flex flex-wrap gap-2">
                <MetricPill label="判定窗 Window" value={reg.decision_window ?? "—"} className="flex-1" />
                <MetricPill label="识别状态" value={r.identification.identification_status} className="flex-1" />
              </div>
            </div>
          </Card>
        </div>

        {/* 勾稽 industry-chain crosswalk */}
        <Card>
          <SectionHeader
            title="Industry Crosswalk"
            titleCn="关联产业链 · 勾稽"
            icon={<Link2 size={15} strokeWidth={2} />}
            right={
              linkQ.data?.scope && (
                <Badge className="bg-surface-2 text-slate-400 ring-1 ring-inset ring-line">
                  scope · {linkQ.data.scope === "platform" ? "平台级" : "链级"}
                </Badge>
              )
            }
          />
          {linkQ.loading || linkQ.error || !linkQ.data ? (
            <div className="p-3">
              <LinkUnavailable loading={linkQ.loading} />
            </div>
          ) : (
            <div className="flex flex-col gap-3 p-4">
              {linkQ.data.rationale_zh && (
                <p className="max-w-3xl rounded-lg bg-brand-50/60 px-3 py-2 text-xs leading-relaxed text-brand-800 ring-1 ring-inset ring-brand-100">
                  {linkQ.data.rationale_zh}
                </p>
              )}
              <div className="grid gap-3 md:grid-cols-2">
                <ChipGroup label="主题 Themes">
                  {linkQ.data.themes.map((t) => (
                    <GennyChip key={t.theme} to={t.genny_link} title={t.name}>
                      {t.name_cn}
                    </GennyChip>
                  ))}
                </ChipGroup>
                <ChipGroup label="环节 Segments">
                  {linkQ.data.segments.map((s) => (
                    <GennyChip key={s.id} to={s.genny_link} title={s.name}>
                      {s.name_cn}
                    </GennyChip>
                  ))}
                </ChipGroup>
                <ChipGroup label="技术路线 Tech routes">
                  {linkQ.data.tech_routes.map((t) => (
                    <span
                      key={t.id}
                      title={t.name}
                      className="inline-flex items-center rounded-md bg-surface-2 px-1.5 py-0.5 text-2xs text-brand-700 ring-1 ring-inset ring-line"
                    >
                      {t.name_cn}
                    </span>
                  ))}
                </ChipGroup>
                <ChipGroup label="公司 Companies">
                  {linkQ.data.companies.map((c) => (
                    <GennyChip key={c.id} to={c.genny_link} title={c.name}>
                      <span className="tnum font-medium">{c.ticker}</span>
                      <span className="opacity-70">{c.name}</span>
                    </GennyChip>
                  ))}
                </ChipGroup>
              </div>
              {linkQ.data.recent_events.length > 0 && (
                <div>
                  <div className="mb-1 text-2xs uppercase tracking-wide text-slate-500">
                    近期事件 Recent events
                  </div>
                  <ul className="divide-y divide-line rounded-lg border border-line">
                    {linkQ.data.recent_events.map((e, i) => (
                      <li key={i} className="flex items-center gap-2 px-3 py-1.5 text-xs">
                        <span
                          className={cn(
                            "h-1.5 w-1.5 shrink-0 rounded-full",
                            e.polarity === "positive" ? "bg-pos" : e.polarity === "negative" ? "bg-neg" : "bg-slate-500",
                          )}
                          aria-hidden="true"
                        />
                        <span className="tnum shrink-0 text-2xs text-slate-500">{e.event_date}</span>
                        <span className="min-w-0 flex-1 truncate text-brand-800" title={e.summary}>
                          {e.summary}
                        </span>
                        <span className="shrink-0 text-2xs text-slate-500">{e.theme}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          )}
        </Card>
      </div>
    </AndyContainer>
  );
}

function ChipGroup({ label, children }: { label: string; children: ReactNode }) {
  const items = Array.isArray(children) ? children : [children];
  const empty = items.length === 0 || (Array.isArray(children) && children.length === 0);
  return (
    <div>
      <div className="mb-1 text-2xs uppercase tracking-wide text-slate-500">{label}</div>
      <div className="flex flex-wrap gap-1.5">
        {empty ? <span className="text-2xs text-slate-600">—</span> : children}
      </div>
    </div>
  );
}

function GennyChip({ to, title, children }: { to: string; title?: string; children: ReactNode }) {
  return (
    <Link
      to={to}
      title={title}
      className="inline-flex items-center gap-1 rounded-md bg-surface-2 px-1.5 py-0.5 text-2xs text-brand-700 ring-1 ring-inset ring-line transition-colors hover:bg-andy-50 hover:text-andy-500 hover:ring-andy/30"
    >
      {children}
    </Link>
  );
}
