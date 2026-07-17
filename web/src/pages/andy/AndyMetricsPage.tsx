import { useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { Search, Table2, X } from "lucide-react";
import { andy } from "../../lib/andy";
import { cn } from "../../lib/format";
import { Badge, Card } from "../../components/ui";
import { useAndy } from "../../components/andy/AndyLayout";
import {
  AnchorChip,
  HARDNESS_META,
  HardnessBadge,
  familyMeta,
  sourceGradeLabel,
} from "../../components/andy/constants";
import { AndyContainer, AndyError, AndyHeader, AndyLoading, useAsync } from "./_shared";
import type { Hardness } from "../../types-andy";

const HARDNESS_ORDER: Hardness[] = ["hard", "medium", "soft", "wall"];
const INPUT =
  "rounded-lg border border-line bg-surface-2 px-2 py-1.5 text-xs text-brand-900 outline-none transition-colors focus:border-andy/50";

/** /andy/metrics — Metrics browser 指标库: dense registry table with family /
 * hardness / text / theme(勾稽) filters. Row click → interrogation page. */
export function AndyMetricsPage() {
  const nav = useNavigate();
  const { withAsOf } = useAndy();
  const [sp] = useSearchParams();

  const metricsQ = useAsync(() => andy.metrics(), []);
  const linksQ = useAsync(() => andy.linkThemes(), []);

  // filter state — seeded from the URL once (overview tiles / 勾稽 rows deep-link here)
  const [family, setFamily] = useState<string>(() => sp.get("family") ?? "");
  const [hardnessSel, setHardnessSel] = useState<Hardness[]>(() => {
    const raw = sp.get("hardness");
    return raw
      ? (raw.split(",").filter((h) => HARDNESS_ORDER.includes(h as Hardness)) as Hardness[])
      : [];
  });
  const [query, setQuery] = useState("");
  const [theme, setTheme] = useState<string | null>(() => sp.get("theme"));

  const families = useMemo(
    () =>
      [...new Set((metricsQ.data?.metrics ?? []).map((m) => m.family))].sort(
        (a, b) => familyMeta(a).order - familyMeta(b).order || a.localeCompare(b),
      ),
    [metricsQ.data],
  );

  // 勾稽 joins: metric_key -> linked theme names; theme -> its metric_key set
  const themeInfo = useMemo(() => {
    const byMetric = new Map<string, string[]>();
    const byTheme = new Map<string, { name_cn: string; keys: Set<string> }>();
    const data = linksQ.data;
    if (data) {
      for (const t of data.themes) {
        const keys = new Set<string>();
        for (const m of t.metrics) {
          keys.add(m.metric_key);
          const cur = byMetric.get(m.metric_key) ?? [];
          if (!cur.includes(t.name_cn)) cur.push(t.name_cn);
          byMetric.set(m.metric_key, cur);
        }
        byTheme.set(t.theme, { name_cn: t.name_cn, keys });
      }
    }
    return { byMetric, byTheme, available: Boolean(data) };
  }, [linksQ.data]);

  const rows = useMemo(() => {
    let list = metricsQ.data?.metrics ?? [];
    if (family) list = list.filter((m) => m.family === family);
    if (hardnessSel.length > 0) list = list.filter((m) => hardnessSel.includes(m.hardness));
    if (theme) {
      const t = themeInfo.byTheme.get(theme);
      if (t) list = list.filter((m) => t.keys.has(m.metric_key));
      // theme param present but crosswalk not loaded yet / 404 → don't filter (graceful)
    }
    const q = query.trim().toLowerCase();
    if (q) {
      list = list.filter(
        (m) =>
          m.display_name_zh.toLowerCase().includes(q) ||
          m.metric_key.toLowerCase().includes(q) ||
          m.family.toLowerCase().includes(q),
      );
    }
    return list;
  }, [metricsQ.data, family, hardnessSel, theme, themeInfo, query]);

  const toggleHardness = (h: Hardness) =>
    setHardnessSel((cur) => (cur.includes(h) ? cur.filter((x) => x !== h) : [...cur, h]));

  if (metricsQ.loading) return <AndyLoading label="Loading registry…" />;
  if (metricsQ.error) return <AndyError error={metricsQ.error} />;

  const themeChip = theme ? themeInfo.byTheme.get(theme) : null;

  return (
    <AndyContainer wide>
      <AndyHeader
        icon={<Table2 size={18} />}
        title="Metrics Registry"
        titleCn="指标库"
        subtitle="理论本体目录 — 每个指标带 hardness / 识别策略 / 证伪条件；点击行进入审讯页。"
        right={
          <Badge className="bg-surface-2 text-brand-500 ring-1 ring-inset ring-line">
            <span className="tnum">{rows.length}</span> / {metricsQ.data?.count ?? 0}
          </Badge>
        }
      />

      {/* filter bar */}
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <div className="relative">
          <Search size={13} className="pointer-events-none absolute left-2 top-1/2 -translate-y-1/2 text-brand-200" />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="搜索名称 / metric_key…"
            className={cn(INPUT, "w-56 pl-7")}
          />
        </div>
        <select value={family} onChange={(e) => setFamily(e.target.value)} className={INPUT}>
          <option value="">全部 family</option>
          {families.map((f) => (
            <option key={f} value={f}>{familyMeta(f).cn} · {f}</option>
          ))}
        </select>
        <div className="flex items-center gap-1">
          {HARDNESS_ORDER.map((h) => {
            const m = HARDNESS_META[h];
            const on = hardnessSel.includes(h);
            return (
              <button
                key={h}
                type="button"
                onClick={() => toggleHardness(h)}
                className={cn(
                  "inline-flex items-center gap-1 rounded-md px-2 py-1 text-2xs font-medium ring-1 ring-inset transition-colors",
                  on ? m.chip : "bg-surface text-brand-200 ring-line hover:bg-surface-2",
                )}
              >
                <span className={cn("h-1.5 w-1.5 rounded-full", m.dot)} aria-hidden="true" />
                {m.cn}
              </button>
            );
          })}
        </div>
        {theme && (
          <button
            type="button"
            onClick={() => setTheme(null)}
            className="inline-flex items-center gap-1 rounded-md bg-andy-50 px-2 py-1 text-2xs font-medium text-andy-500 ring-1 ring-inset ring-andy/30 transition-colors hover:bg-andy-100"
            title="清除主题勾稽过滤"
          >
            主题: {themeChip?.name_cn ?? theme}
            <X size={11} strokeWidth={2.5} />
          </button>
        )}
      </div>

      {/* dense registry table */}
      <Card className="overflow-hidden">
        <div className="scroll-thin overflow-x-auto">
          <table className="w-full min-w-[980px] border-collapse text-xs">
            <thead>
              <tr className="border-b border-line text-left text-2xs uppercase tracking-wide text-brand-200">
                <th className="px-3 py-2 font-medium">指标 Metric</th>
                <th className="px-3 py-2 font-medium">Family</th>
                <th className="px-3 py-2 font-medium">硬度</th>
                <th className="px-3 py-2 font-medium">约束稀缺 Scarcity</th>
                <th className="px-3 py-2 font-medium">Phase</th>
                <th className="px-3 py-2 font-medium">来源级</th>
                <th className="px-3 py-2 font-medium">锚点</th>
                {themeInfo.available && <th className="px-3 py-2 font-medium">勾稽主题</th>}
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {rows.map((m) => (
                <tr
                  key={m.metric_key}
                  onClick={() => nav(withAsOf(`/andy/metrics/${encodeURIComponent(m.metric_key)}`))}
                  className="cursor-pointer transition-colors hover:bg-canvas"
                >
                  <td className="max-w-[280px] px-3 py-2">
                    <div className="truncate font-medium text-brand-900">{m.display_name_zh}</div>
                    <div className="truncate font-mono text-2xs text-brand-200">{m.metric_key}</div>
                  </td>
                  <td className="whitespace-nowrap px-3 py-2 text-brand-500">{m.family}</td>
                  <td className="whitespace-nowrap px-3 py-2">
                    <HardnessBadge hardness={m.hardness} withEn={false} />
                  </td>
                  <td className="max-w-[160px] truncate px-3 py-2 text-brand-500" title={m.binding_scarcity ?? undefined}>
                    {m.binding_scarcity ?? "—"}
                  </td>
                  <td className="whitespace-nowrap px-3 py-2 text-brand-500">{m.phase ?? "—"}</td>
                  <td className="whitespace-nowrap px-3 py-2 text-brand-500" title={m.source_grade ?? undefined}>
                    {sourceGradeLabel(m.source_grade)}
                  </td>
                  <td className="whitespace-nowrap px-3 py-2">
                    <span className="flex items-center gap-1">
                      {m.theory_anchor.slice(0, 3).map((a) => (
                        <AnchorChip key={a} anchor={a} />
                      ))}
                      {m.theory_anchor.length > 3 && (
                        <span className="text-2xs text-brand-200">+{m.theory_anchor.length - 3}</span>
                      )}
                    </span>
                  </td>
                  {themeInfo.available && (
                    <td className="max-w-[180px] px-3 py-2">
                      <span
                        className="line-clamp-1 text-2xs text-brand-500"
                        title={(themeInfo.byMetric.get(m.metric_key) ?? []).join(" · ")}
                      >
                        {(themeInfo.byMetric.get(m.metric_key) ?? []).join(" · ") || "—"}
                      </span>
                    </td>
                  )}
                </tr>
              ))}
              {rows.length === 0 && (
                <tr>
                  <td colSpan={themeInfo.available ? 8 : 7} className="px-3 py-10 text-center text-brand-200">
                    无匹配指标 · no metrics match the current filters
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </Card>
    </AndyContainer>
  );
}
