import { Radar, RefreshCw } from "lucide-react";
import { ops } from "../../lib/ops";
import { cn, heat } from "../../lib/format";
import type { OpsCoverageInfo } from "../../types-ops";
import { Card, MetricPill, SectionHeader } from "../../components/ui";
import { OpsContainer, OpsError, OpsHeader, OpsLoading, useAsync } from "./_shared";

/**
 * Operations console: 360° coverage dashboard — theme × dimension heat table
 * of fill rates (share of names in a theme whose per-dimension score clears
 * the bar) plus the average weighted composite per theme.
 */
export function CoveragePage() {
  const { data, loading, error, reload } = useAsync<OpsCoverageInfo>(() => ops.coverage(), []);

  return (
    <OpsContainer>
      <OpsHeader
        icon={<Radar size={18} />}
        title="Coverage 360"
        titleCn="覆盖度"
        subtitle="Universe data completeness — per-theme fill rate across the 16 coverage dimensions (identity → thesis)."
        right={
          <button
            type="button"
            onClick={reload}
            disabled={loading}
            className={cn(
              "inline-flex items-center gap-1.5 rounded-lg border border-line bg-surface px-2.5 py-1.5 text-xs font-medium text-brand-900",
              "transition-colors hover:border-accent/40 hover:text-accent",
              loading && "cursor-not-allowed opacity-60",
            )}
          >
            <RefreshCw size={13} className={cn(loading && "animate-spin")} />
            Refresh
          </button>
        }
      />

      {loading && <OpsLoading />}
      {error && <OpsError error={error} />}

      {data && <CoverageBody data={data} />}
    </OpsContainer>
  );
}

function CoverageBody({ data }: { data: OpsCoverageInfo }) {
  const themes = data.themes ?? [];
  const dims = data.dimensions ?? [];
  const totalNames = themes.reduce((s, t) => s + t.companies, 0);
  const avgComposite =
    themes.length > 0
      ? themes.reduce((s, t) => s + t.avg_composite * t.companies, 0) / Math.max(1, totalNames)
      : 0;

  return (
    <div className="flex flex-col gap-4">
      {/* summary strip */}
      <div className="flex flex-wrap gap-2">
        <MetricPill label="Themes" value={themes.length} sub="主题链" />
        <MetricPill label="Companies" value={totalNames.toLocaleString()} sub="覆盖名单" />
        <MetricPill label="Dimensions" value={dims.length} sub="覆盖维度" />
        <MetricPill
          label="Avg Composite"
          value={
            <span style={{ color: heat(avgComposite * 100, "good-high", 1).color }}>
              {Math.round(avgComposite * 100)}
            </span>
          }
          sub="全宇宙加权"
        />
      </div>

      {/* theme × dimension heat table */}
      <Card>
        <SectionHeader
          title="Theme × Dimension"
          titleCn="主题链 × 维度 · 满足率热力表"
          icon={<Radar size={15} strokeWidth={2} />}
          right={<HeatLegend />}
        />
        {themes.length === 0 ? (
          <div className="px-4 py-10 text-center text-xs text-slate-400">暂无数据 · 采集中</div>
        ) : (
          <div className="scroll-thin overflow-x-auto">
            <table className="w-full min-w-[1080px] border-collapse text-xs">
              <thead>
                <tr className="border-b border-line">
                  <th className="sticky left-0 z-10 bg-surface px-4 py-2 text-left text-2xs font-medium uppercase tracking-wide text-slate-400">
                    Theme 主题链
                  </th>
                  <th
                    className="px-2 py-2 text-right text-2xs font-medium uppercase tracking-wide text-slate-400"
                    title="Average weighted composite across member companies"
                  >
                    综合
                  </th>
                  {dims.map((d) => (
                    <th
                      key={d.key}
                      title={`${d.name} · weight ${(d.weight * 100).toFixed(0)}%`}
                      className="cursor-help whitespace-nowrap px-1 py-2 text-center text-2xs font-medium text-slate-400"
                    >
                      {d.name_cn}
                      <div className="tnum font-normal text-slate-500">
                        {(d.weight * 100).toFixed(0)}%
                      </div>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {themes.map((t) => (
                  <tr key={t.theme} className="border-b border-line/60 last:border-b-0">
                    <td className="sticky left-0 z-10 bg-surface px-4 py-2">
                      <div className="whitespace-nowrap text-xs font-medium text-brand-900">
                        {t.name}
                      </div>
                      <div className="whitespace-nowrap text-2xs text-slate-400">
                        {t.name_cn} · <span className="tnum">{t.companies}</span> names
                      </div>
                    </td>
                    <td className="px-2 py-2 text-right">
                      <span
                        className="tnum text-sm font-semibold"
                        style={{ color: heat(t.avg_composite * 100, "good-high", 1).color }}
                        title={`平均综合分 ${(t.avg_composite * 100).toFixed(1)} / 100`}
                      >
                        {Math.round(t.avg_composite * 100)}
                      </span>
                    </td>
                    {dims.map((d) => {
                      const v = Math.max(0, Math.min(1, t.dims?.[d.key] ?? 0));
                      return (
                        <td key={d.key} className="p-[3px] text-center">
                          <div
                            className="tnum cursor-help rounded px-1 py-1.5 font-medium"
                            style={heat(v * 100, "good-high", 0.28)}
                            title={`${t.name_cn} · ${d.name_cn} ${d.name} · 满足率 ${Math.round(v * 100)}%`}
                          >
                            {Math.round(v * 100)}
                          </div>
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <div className="text-2xs text-slate-500">
        满足率口径:主题内单维度得分 ≥ 0.34 的公司占比;综合分为 16 维加权(权重合计 100%)。
      </div>
    </div>
  );
}

/** 0 → 100 fill-rate color legend. */
function HeatLegend() {
  const stops = [0, 25, 50, 75, 100];
  return (
    <span className="flex items-center gap-1 text-2xs text-slate-400">
      <span className="tnum">0</span>
      {stops.map((s) => (
        <span
          key={s}
          className="h-2.5 w-4 rounded-sm"
          style={{ backgroundColor: heat(s, "good-high", 0.55).backgroundColor }}
        />
      ))}
      <span className="tnum">100</span>
    </span>
  );
}
