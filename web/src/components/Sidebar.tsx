import { FolderOpen, LayoutDashboard, type LucideIcon } from "lucide-react";
import { REGIME_LABEL, type Company, type CoverageMeta, type Segment } from "../types";
import { cn, fmtSigned, regimeDot, relTime, signClass } from "../lib/format";
import { ModuleNav } from "./ModuleNav";

/** Workspace navigation — research-terminal pages. The Operations control plane
 * is a separate admin console reached via the entry-point button below. */
const NAV_GROUPS: { label: string; items: { id: string; label: string; icon: LucideIcon; route: string }[] }[] = [
  {
    label: "Workspace",
    items: [
      { id: "dashboard", label: "Dashboard", icon: LayoutDashboard, route: "/genny" },
      { id: "dataroom", label: "Data Room", icon: FolderOpen, route: "/genny/dataroom" },
    ],
  },
];

/**
 * The terminal frame's left navigation column — the single navy surface.
 * Wordmark + active theme, the Research Universe (chain segments, upstream→
 * downstream), the Workspace + Operations navigation, a company quick-jump, and
 * a coverage footer.
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
  const { coverage, segments, currentPath, onNavigate, selectedSegmentId, onSelectSegment, onCompany } = props;
  const activeTheme = coverage.themes.find((t) => t.active);
  const inactiveThemes = coverage.themes.filter((t) => !t.active);
  const orderedSegments = [...segments].sort((a, b) => a.tier - b.tier);
  const topNames = [...props.companies]
    .sort((a, b) => Number(b.watched) - Number(a.watched) || b.conviction - a.conviction)
    .slice(0, 6);
  const isActive = (route: string) =>
    route === "/" ? currentPath === "/" : currentPath.startsWith(route);

  return (
    <div className="flex w-60 shrink-0 flex-col bg-surface text-brand-100">
      {/* wordmark + active theme */}
      <div className="shrink-0 px-4 pb-4 pt-5">
        <button
          type="button"
          onClick={() => onNavigate("/genny")}
          className="flex items-center gap-2.5 text-left focus-visible:ring-white/50"
        >
          <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-accent text-sm font-bold tracking-tight text-white shadow-card">
            X
          </span>
          <div className="min-w-0">
            <div className="text-lg font-bold leading-none tracking-tight text-brand-900">XAR Genny</div>
            <div className="mt-0.5 text-2xs uppercase tracking-wide text-brand-200/70">
              Research Terminal
            </div>
          </div>
        </button>

        <div className="mt-4 flex flex-col gap-1">
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
      </div>

      {/* scrollable middle */}
      <div className="scroll-thin flex-1 overflow-y-auto px-2 pb-3">
        {/* research universe */}
        <div className="px-2 pb-1.5 pt-2 text-2xs uppercase tracking-wide text-brand-200/60">
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

        {/* nav groups */}
        {NAV_GROUPS.map((group) => (
          <div key={group.label}>
            <div className="px-2 pb-1.5 pt-4 text-2xs uppercase tracking-wide text-brand-200/60">
              {group.label}
            </div>
            <nav className="flex flex-col gap-0.5">
              {group.items.map((item) => {
                const active = isActive(item.route);
                const Icon = item.icon;
                return (
                  <button
                    key={item.id}
                    type="button"
                    onClick={() => onNavigate(item.route)}
                    aria-current={active ? "page" : undefined}
                    className={cn(
                      "flex w-full items-center gap-2.5 rounded-md border-l-2 py-1.5 pl-2 pr-2 text-left text-xs font-medium transition-colors focus-visible:ring-white/50",
                      active
                        ? "border-accent bg-accent/15 text-white"
                        : "border-transparent text-brand-100/80 hover:bg-surface/5 hover:text-white",
                    )}
                  >
                    <Icon
                      size={15}
                      strokeWidth={2}
                      className={cn("shrink-0", active ? "text-accent" : "text-brand-200/70")}
                    />
                    <span className="flex-1 truncate">{item.label}</span>
                  </button>
                );
              })}
            </nav>
          </div>
        ))}

        {/* module switcher (Andy / Genny / Fenny + Explore / Ops) */}
        <div className="px-2 pt-4">
          <div className="mb-1 px-1 text-2xs font-semibold uppercase tracking-wide text-slate-500">
            Modules
          </div>
          <ModuleNav />
        </div>

        {/* company quick-jump */}
        {onCompany && topNames.length > 0 && (
          <>
            <div className="px-2 pb-1.5 pt-4 text-2xs uppercase tracking-wide text-brand-200/60">
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
      </div>

      {/* coverage footer */}
      <div className="mt-auto shrink-0 border-t border-white/10 px-4 py-3 text-2xs text-brand-200/60">
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
    </div>
  );
}
