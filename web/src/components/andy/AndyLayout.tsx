import { createContext, useCallback, useContext, useMemo } from "react";
import { Activity, ArrowLeft, BrickWall, Gauge, Scale, Table2, type LucideIcon } from "lucide-react";
import { Link, Outlet, useLocation, useSearchParams } from "react-router-dom";
import { ModuleNav } from "../ModuleNav";
import { cn } from "../../lib/format";
import { todayISO } from "../../lib/andy";

/**
 * XAR Andy shell — theory-anchored macro-indicator terminal (siliconomics).
 * Clone of the ExplorationLayout pattern: own teal sidebar + top bar with the
 * shared ModuleNav. Owns the module-wide `as_of` observation boundary,
 * persisted in the URL (?as_of=YYYY-MM-DD, default today) and provided via
 * context so every point-in-time fetch shares one look-ahead boundary.
 */

interface AndyState {
  /** Observation date (YYYY-MM-DD): the point-in-time look-ahead boundary. */
  asOf: string;
  setAsOf: (d: string) => void;
  /** Append the current as_of to an in-module path so the boundary survives navigation. */
  withAsOf: (path: string) => string;
}

const Ctx = createContext<AndyState | null>(null);

export function useAndy(): AndyState {
  const c = useContext(Ctx);
  if (!c) throw new Error("useAndy must be used within AndyLayout");
  return c;
}

const NAV: { to: string; label: string; cn: string; icon: LucideIcon; exact?: boolean }[] = [
  { to: "/andy", label: "Overview", cn: "总览", icon: Gauge, exact: true },
  { to: "/andy/metrics", label: "Metrics", cn: "指标库", icon: Table2 },
  { to: "/andy/overclaims", label: "Overclaims", cn: "过度宣称登记簿", icon: Scale },
  { to: "/andy/walls", label: "Walls", cn: "承重墙", icon: BrickWall },
];

export function AndyLayout() {
  const loc = useLocation();
  const [sp, setSp] = useSearchParams();

  const urlAsOf = sp.get("as_of");
  const asOf = urlAsOf && /^\d{4}-\d{2}-\d{2}$/.test(urlAsOf) ? urlAsOf : todayISO();

  const setAsOf = useCallback(
    (d: string) => {
      setSp(
        (prev) => {
          const next = new URLSearchParams(prev);
          if (d) next.set("as_of", d);
          else next.delete("as_of");
          return next;
        },
        { replace: true },
      );
    },
    [setSp],
  );

  const withAsOf = useCallback(
    (path: string) => {
      if (!urlAsOf) return path; // default (today) stays implicit — clean URLs
      const sep = path.includes("?") ? "&" : "?";
      return `${path}${sep}as_of=${encodeURIComponent(asOf)}`;
    },
    [urlAsOf, asOf],
  );

  const value = useMemo<AndyState>(() => ({ asOf, setAsOf, withAsOf }), [asOf, setAsOf, withAsOf]);

  const current =
    NAV.find((n) => (n.exact ? loc.pathname === n.to : loc.pathname.startsWith(n.to))) ?? NAV[0];

  return (
    <Ctx.Provider value={value}>
      <div className="flex h-full w-full overflow-hidden bg-canvas text-brand-900">
        {/* sidebar */}
        <div className="flex w-60 shrink-0 flex-col bg-surface text-brand-100">
          <div className="shrink-0 px-4 pb-4 pt-5">
            <Link to={withAsOf("/andy")} className="flex items-center gap-2.5 text-left">
              <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-andy text-white shadow-card">
                <Activity size={17} strokeWidth={2.2} />
              </span>
              <div className="min-w-0">
                <div className="text-lg font-bold leading-none tracking-tight text-white">XAR</div>
                <div className="mt-1 flex items-center gap-1.5">
                  <span className="text-2xs uppercase tracking-wide text-brand-200/70">Andy 宏观指标</span>
                  <span className="rounded bg-andy/25 px-1 py-0.5 text-2xs font-semibold uppercase text-andy-500">
                    Silicon-Index
                  </span>
                </div>
              </div>
            </Link>
          </div>

          <div className="scroll-thin flex-1 overflow-y-auto px-2 pb-3">
            <div className="px-2 pb-1.5 pt-1 text-2xs uppercase tracking-wide text-brand-200/60">
              Macro Console · 宏观台
            </div>
            <nav className="flex flex-col gap-0.5">
              {NAV.map((n) => {
                const active = n.exact ? loc.pathname === n.to : loc.pathname.startsWith(n.to);
                const Icon = n.icon;
                return (
                  <Link
                    key={n.to}
                    to={withAsOf(n.to)}
                    aria-current={active ? "page" : undefined}
                    className={cn(
                      "flex w-full items-center gap-2.5 rounded-md border-l-2 py-1.5 pl-2 pr-2 text-left text-xs font-medium transition-colors",
                      active
                        ? "border-andy bg-andy/15 text-white"
                        : "border-transparent text-brand-100/80 hover:bg-surface/5 hover:text-white",
                    )}
                  >
                    <Icon
                      size={15}
                      strokeWidth={2}
                      className={cn("shrink-0", active ? "text-andy-500" : "text-brand-200/70")}
                    />
                    <span className="flex-1 truncate">{n.label}</span>
                    <span className="truncate text-2xs text-brand-200/50">{n.cn}</span>
                  </Link>
                );
              })}
            </nav>

            <div className="mt-4 rounded-lg border border-line/60 bg-canvas/40 px-2.5 py-2 text-2xs leading-relaxed text-brand-200/70">
              <div className="font-semibold text-brand-100/80">Point-in-time 纪律</div>
              所有读数满足 knowledge_time ≤ as-of；soft 指标一律带
              <span className="text-warn-700">「未识别 · 勿作因果」</span>水印。
            </div>
          </div>

          <div className="mt-auto shrink-0 border-t border-white/10 px-2 py-3">
            <Link
              to="/genny"
              className="flex w-full items-center gap-2 rounded-md py-2 pl-2 pr-2 text-left text-xs font-medium text-brand-100/80 transition-colors hover:bg-surface/5 hover:text-white"
            >
              <ArrowLeft size={15} strokeWidth={2} className="shrink-0 text-brand-200/70" />
              <span>Research Terminal</span>
            </Link>
          </div>
        </div>

        {/* main column */}
        <div className="flex min-w-0 flex-1 flex-col">
          <div className="flex h-14 shrink-0 items-center justify-between gap-4 border-b border-line bg-surface px-5">
            <div className="flex min-w-0 items-center gap-2">
              <span className="text-2xs uppercase tracking-wide text-slate-400">Andy</span>
              <span className="text-slate-300">/</span>
              <span className="truncate text-sm font-semibold text-brand-900">{current.label}</span>
              <span className="truncate text-xs text-slate-400">{current.cn}</span>
            </div>
            <div className="flex items-center gap-3">
              <label
                className="hidden items-center gap-2 text-2xs text-slate-400 md:flex"
                title="Point-in-time 截面日：所有读数只取 knowledge_time ≤ as-of（防前视）"
              >
                <span className="whitespace-nowrap uppercase tracking-wide">
                  As-of 观察日 <span className="normal-case text-slate-500">(look-ahead 边界)</span>
                </span>
                <input
                  type="date"
                  value={asOf}
                  max={todayISO()}
                  onChange={(e) => setAsOf(e.target.value)}
                  className="tnum rounded-lg border border-line bg-surface-2 px-2 py-1 text-xs text-brand-900 outline-none transition-colors focus:border-andy/50"
                />
              </label>
              <ModuleNav variant="bar" />
            </div>
          </div>
          <main className="scroll-thin min-w-0 flex-1 overflow-y-auto px-5 py-5">
            <Outlet />
          </main>
        </div>
      </div>
    </Ctx.Provider>
  );
}
