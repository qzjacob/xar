import { FolderOpen, LayoutDashboard } from "lucide-react";
import { REGIME_LABEL, type Company, type CoverageMeta, type Segment } from "../types";
import { cn, fmtSigned, regimeDot, relTime, signClass } from "../lib/format";
import { SidebarFrame } from "./shell/SidebarFrame";
import { SidebarNav, type SideNavItem } from "./shell/SidebarNav";

/** Workspace navigation — research-terminal pages(模块切换在全局顶栏)。 */
const WORKSPACE_NAV: SideNavItem[] = [
  { to: "/genny", label: "Dashboard", cn: "仪表盘", icon: LayoutDashboard, exact: true },
  { to: "/genny/dataroom", label: "Data Room", cn: "资料室", icon: FolderOpen },
];

/**
 * Genny 左栏 — 统一 SidebarFrame 体系:主题卡、Research Universe(链段)、
 * Workspace 导航、公司快跳、覆盖度脚注。品牌区已上移全局顶栏。
 */
export function Sidebar(props: {
  coverage: CoverageMeta;
  segments: Segment[];
  companies: Company[];
  currentPath: string;
  onNavigate: (route: string) => void;
  selectedSegmentId: string | null;
  onSelectSegment: (id: string | null) => void;
  onCompany?: (id: string) => void;
}) {
  const { coverage, segments, selectedSegmentId, onSelectSegment, onCompany } = props;
  const activeTheme = coverage.themes.find((t) => t.active);
  const inactiveThemes = coverage.themes.filter((t) => !t.active);
  const orderedSegments = [...segments].sort((a, b) => a.tier - b.tier);
  const topNames = [...props.companies]
    .sort((a, b) => Number(b.watched) - Number(a.watched) || b.conviction - a.conviction)
    .slice(0, 6);

  return (
    <SidebarFrame
      title="Genny"
      titleCn="研究终端"
      badge="Terminal"
      footer={
        <div className="text-2xs text-brand-200/60">
          <div className="flex items-center justify-between">
            <span className="tnum">
              <span className="font-semibold text-brand-100">{coverage.companyCount}</span> companies
            </span>
            <span className="tnum">
              <span className="font-semibold text-brand-100">{coverage.segmentCount}</span> segments
            </span>
          </div>
          <div className="mt-1.5 flex items-center gap-1.5">
            <span className="h-1.5 w-1.5 shrink-0 animate-pulse rounded-full bg-pos" />
            <span>Updated {relTime(coverage.updatedAt)}</span>
          </div>
        </div>
      }
    >
      {/* active theme */}
      <div className="flex flex-col gap-1 px-2 pb-1">
        {activeTheme && (
          <div className="rounded-lg border border-accent/40 bg-accent/15 px-2.5 py-2">
            <div className="flex items-center gap-1.5">
              <span className="h-1.5 w-1.5 rounded-full bg-accent" />
              <span className="truncate text-xs font-semibold text-white">{activeTheme.name}</span>
            </div>
            <div className="mt-0.5 truncate pl-3 text-2xs text-brand-200/70">{activeTheme.nameCn}</div>
          </div>
        )}
        {inactiveThemes.map((theme) => (
          <div
            key={theme.id}
            title={`${theme.name} · coming soon`}
            className="flex cursor-default items-center justify-between gap-2 rounded-lg px-2.5 py-1.5 opacity-60"
          >
            <span className="block min-w-0 truncate text-xs font-medium text-brand-100">
              {theme.name}
            </span>
            <span className="shrink-0 rounded bg-surface/10 px-1.5 py-0.5 text-2xs font-medium uppercase tracking-wide text-brand-200/70">
              soon
            </span>
          </div>
        ))}
      </div>

      {/* research universe */}
      <div className="px-2 pb-1.5 pt-2 text-[10px] font-semibold uppercase tracking-wider text-brand-200">
        Research Universe
      </div>
      <div className="flex flex-col gap-0.5">
        {orderedSegments.length === 0 ? (
          <div className="px-2 py-2 text-xs text-brand-200/50">No segments</div>
        ) : (
          orderedSegments.map((seg) => {
            const selected = selectedSegmentId === seg.id;
            return (
              <button
                key={seg.id}
                type="button"
                onClick={() => onSelectSegment(selected ? null : seg.id)}
                aria-pressed={selected}
                title={`${seg.nameCn} · ${REGIME_LABEL[seg.regime].en}`}
                className={cn(
                  "group flex w-full items-center gap-2 rounded-md border-l-2 py-1.5 pl-2 pr-2 text-left transition-colors focus-visible:ring-white/50",
                  selected ? "border-accent bg-surface/10" : "border-transparent hover:bg-surface/5",
                )}
              >
                <span className={cn("h-1.5 w-1.5 shrink-0 rounded-full", regimeDot(seg.regime))} />
                <span
                  className={cn(
                    "min-w-0 flex-1 truncate text-xs",
                    selected ? "font-semibold text-white" : "text-brand-100",
                  )}
                >
                  {seg.name}
                </span>
                <span className={cn("tnum shrink-0 text-2xs font-semibold", signClass(seg.momentum))}>
                  {fmtSigned(seg.momentum)}
                </span>
              </button>
            );
          })
        )}
      </div>

      {/* workspace nav */}
      <div className="pt-2">
        <SidebarNav heading="Workspace" items={WORKSPACE_NAV} />
      </div>

      {/* company quick-jump */}
      {onCompany && topNames.length > 0 && (
        <>
          <div className="px-2 pb-1.5 pt-4 text-[10px] font-semibold uppercase tracking-wider text-brand-200">
            Top Names
          </div>
          <div className="flex flex-col gap-0.5">
            {topNames.map((c) => (
              <button
                key={c.id}
                type="button"
                onClick={() => onCompany(c.id)}
                title={`${c.name} · ${c.role}`}
                className="group flex w-full items-center gap-2 rounded-md py-1.5 pl-2 pr-2 text-left transition-colors hover:bg-surface/5 focus-visible:ring-white/50"
              >
                <span className="tnum w-16 shrink-0 truncate text-2xs font-semibold text-brand-100">
                  {c.ticker}
                </span>
                <span className="min-w-0 flex-1 truncate text-xs text-brand-100/80 group-hover:text-white">
                  {c.name}
                </span>
                <span className={cn("tnum shrink-0 text-2xs font-semibold", signClass(c.priceChange))}>
                  {fmtSigned(Math.round(c.priceChange))}
                </span>
              </button>
            ))}
          </div>
        </>
      )}
    </SidebarFrame>
  );
}
