import {
  ArrowLeft,
  BrainCircuit,
  Cpu,
  Database,
  Gauge,
  Layers3,
  Network,
  Plug,
  Workflow,
  type LucideIcon,
} from "lucide-react";
import { cn } from "../lib/format";

const ADMIN_NAV: { id: string; label: string; icon: LucideIcon; route: string }[] = [
  { id: "overview", label: "Overview", icon: Gauge, route: "/ops" },
  { id: "ontology", label: "Ontology", icon: Network, route: "/ops/ontology" },
  { id: "sources", label: "Data Sources", icon: Database, route: "/ops/sources" },
  { id: "datalake", label: "Data Lake", icon: Layers3, route: "/ops/datalake" },
  { id: "altdata", label: "Alt-Data AI", icon: BrainCircuit, route: "/ops/altdata" },
  { id: "models", label: "Models & LLM", icon: Cpu, route: "/ops/models" },
  { id: "connectors", label: "MCP & API", icon: Plug, route: "/ops/connectors" },
  { id: "skills", label: "Agent Skills", icon: Workflow, route: "/ops/skills" },
];

/** Standalone admin-console navigation (amber-accented to distinguish it from
 * the blue research terminal); pinned "back to terminal" at the bottom. */
export function AdminSidebar({
  currentPath,
  onNavigate,
  onBack,
}: {
  currentPath: string;
  onNavigate: (route: string) => void;
  onBack: () => void;
}) {
  const isActive = (route: string) =>
    route === "/ops" ? currentPath === "/ops" : currentPath.startsWith(route);

  return (
    <div className="flex w-60 shrink-0 flex-col bg-surface text-brand-100">
      <div className="shrink-0 px-4 pb-4 pt-5">
        <button
          type="button"
          onClick={() => onNavigate("/ops")}
          className="flex items-center gap-2.5 text-left focus-visible:ring-white/50"
        >
          <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-warn text-sm font-bold tracking-tight text-white shadow-card">
            X
          </span>
          <div className="min-w-0">
            <div className="text-lg font-bold leading-none tracking-tight text-white">XAR</div>
            <div className="mt-1 flex items-center gap-1.5">
              <span className="text-2xs uppercase tracking-wide text-brand-200/70">
                Operations Console
              </span>
              <span className="rounded bg-warn/20 px-1 py-0.5 text-2xs font-semibold uppercase text-warn">
                Admin
              </span>
            </div>
          </div>
        </button>
      </div>

      <div className="scroll-thin flex-1 overflow-y-auto px-2 pb-3">
        <div className="px-2 pb-1.5 pt-2 text-2xs uppercase tracking-wide text-brand-200/60">
          Control Plane
        </div>
        <nav className="flex flex-col gap-0.5">
          {ADMIN_NAV.map((item) => {
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
                    ? "border-warn bg-warn/15 text-white"
                    : "border-transparent text-brand-100/80 hover:bg-surface/5 hover:text-white",
                )}
              >
                <Icon
                  size={15}
                  strokeWidth={2}
                  className={cn("shrink-0", active ? "text-warn" : "text-brand-200/70")}
                />
                <span className="flex-1 truncate">{item.label}</span>
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
