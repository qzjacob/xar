import {
  Activity,
  ArrowLeft,
  Atom,
  Brain,
  Compass,
  Cpu,
  Globe,
  Sigma,
  Telescope,
  type LucideIcon,
} from "lucide-react";
import { cn } from "../lib/format";
import type { ExploreSectionCard } from "../types-exploration";

const ICONS: Record<string, LucideIcon> = {
  brain: Brain,
  atom: Atom,
  sigma: Sigma,
  cpu: Cpu,
  activity: Activity,
  globe: Globe,
};

/** Standalone Exploration navigation (indigo-accented) — one entry per frontier
 * section, pinned "back to terminal" at the bottom. */
export function ExplorationSidebar({
  sections,
  currentPath,
  onNavigate,
  onBack,
}: {
  sections: ExploreSectionCard[];
  currentPath: string;
  onNavigate: (route: string) => void;
  onBack: () => void;
}) {
  const isActive = (id: string) => currentPath === `/explore/${id}`;
  const onOverview = currentPath === "/explore" || currentPath === "/explore/";

  return (
    <div className="flex w-60 shrink-0 flex-col bg-surface text-brand-100">
      <div className="shrink-0 px-4 pb-4 pt-5">
        <button
          type="button"
          onClick={() => onNavigate("/explore")}
          className="flex items-center gap-2.5 text-left focus-visible:ring-white/50"
        >
          <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-explore text-white shadow-card">
            <Telescope size={17} strokeWidth={2.2} />
          </span>
          <div className="min-w-0">
            <div className="text-lg font-bold leading-none tracking-tight text-white">XAR</div>
            <div className="mt-1 flex items-center gap-1.5">
              <span className="text-2xs uppercase tracking-wide text-brand-200/70">Exploration</span>
              <span className="rounded bg-explore/30 px-1 py-0.5 text-2xs font-semibold uppercase text-explore-100">
                Frontier
              </span>
            </div>
          </div>
        </button>
      </div>

      <div className="scroll-thin flex-1 overflow-y-auto px-2 pb-3">
        <button
          type="button"
          onClick={() => onNavigate("/explore")}
          aria-current={onOverview ? "page" : undefined}
          className={cn(
            "mb-1 flex w-full items-center gap-2.5 rounded-md border-l-2 py-1.5 pl-2 pr-2 text-left text-xs font-medium transition-colors focus-visible:ring-white/50",
            onOverview
              ? "border-explore bg-explore/15 text-white"
              : "border-transparent text-brand-100/80 hover:bg-surface/5 hover:text-white",
          )}
        >
          <Compass size={15} strokeWidth={2} className={cn("shrink-0", onOverview ? "text-explore-100" : "text-brand-200/70")} />
          <span className="flex-1 truncate">Overview</span>
        </button>

        <div className="px-2 pb-1.5 pt-3 text-2xs uppercase tracking-wide text-brand-200/60">
          Frontier Sections
        </div>
        <nav className="flex flex-col gap-0.5">
          {sections.map((s) => {
            const active = isActive(s.id);
            const Icon = ICONS[s.icon ?? ""] ?? Compass;
            return (
              <button
                key={s.id}
                type="button"
                onClick={() => onNavigate(`/explore/${s.id}`)}
                aria-current={active ? "page" : undefined}
                className={cn(
                  "flex w-full items-center gap-2.5 rounded-md border-l-2 py-1.5 pl-2 pr-2 text-left text-xs font-medium transition-colors focus-visible:ring-white/50",
                  active
                    ? "border-explore bg-explore/15 text-white"
                    : "border-transparent text-brand-100/80 hover:bg-surface/5 hover:text-white",
                )}
              >
                <Icon size={15} strokeWidth={2} className={cn("shrink-0", active ? "text-explore-100" : "text-brand-200/70")} />
                <span className="flex-1 truncate">{s.name}</span>
                {s.frontCount > 0 && (
                  <span className="tnum rounded bg-surface/10 px-1 py-0.5 text-2xs text-brand-100/70">
                    {s.frontCount}
                  </span>
                )}
              </button>
            );
          })}
        </nav>
      </div>

      <div className="mt-auto shrink-0 border-t border-white/10 px-2 py-3">
        <button
          type="button"
          onClick={onBack}
          className="flex w-full items-center gap-2 rounded-md py-2 pl-2 pr-2 text-left text-xs font-medium text-brand-100/80 transition-colors hover:bg-surface/5 hover:text-white focus-visible:ring-white/50"
        >
          <ArrowLeft size={15} strokeWidth={2} className="shrink-0 text-brand-200/70" />
          <span>Research Terminal</span>
        </button>
      </div>
    </div>
  );
}
