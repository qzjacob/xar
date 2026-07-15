import { createContext, useCallback, useContext, useMemo } from "react";
import { BrickWall, Database, Gauge, Scale, Table2, Waves } from "lucide-react";
import { Outlet, useLocation, useSearchParams } from "react-router-dom";
import { ModuleHeader } from "../shell/ModuleHeader";
import { ModuleShell } from "../shell/ModuleShell";
import { SidebarFrame } from "../shell/SidebarFrame";
import { SidebarNav, type SideNavItem } from "../shell/SidebarNav";
import { todayISO } from "../../lib/andy";

/**
 * XAR Andy shell — theory-anchored macro-indicator terminal (siliconomics).
 * 统一外壳(ModuleShell + SidebarFrame/SidebarNav)。Owns the module-wide `as_of`
 * observation boundary, persisted in the URL (?as_of=YYYY-MM-DD, default today)
 * and provided via context so every point-in-time fetch shares one look-ahead
 * boundary; in-module nav preserves it via SidebarNav's `transform`.
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

const NAV: SideNavItem[] = [
  { to: "/andy", label: "Overview", cn: "总览", icon: Gauge, exact: true },
  { to: "/andy/flow", label: "Money Flow", cn: "资金流策略", icon: Waves },
  { to: "/andy/metrics", label: "Metrics", cn: "指标库", icon: Table2 },
  { to: "/andy/overclaims", label: "Overclaims", cn: "过度宣称登记簿", icon: Scale },
  { to: "/andy/walls", label: "Walls", cn: "承重墙", icon: BrickWall },
  { to: "/andy/sources", label: "Sources", cn: "数据源", icon: Database },
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
      <ModuleShell
        sidebar={
          <SidebarFrame title="Andy" titleCn="宏观指标" badge="Silicon-Index">
            <SidebarNav heading="Macro Console · 宏观台" items={NAV} transform={withAsOf} />
            <div className="mt-4 rounded-lg border border-line/60 bg-canvas/40 px-2.5 py-2 text-2xs leading-relaxed text-brand-200/70">
              <div className="font-semibold text-brand-100/80">Point-in-time 纪律</div>
              所有读数满足 knowledge_time ≤ as-of；soft 指标一律带
              <span className="text-warn-700">「未识别 · 勿作因果」</span>水印。
            </div>
          </SidebarFrame>
        }
        header={
          <ModuleHeader crumb="Andy" title={current.label} titleCn={current.cn}>
            <label
              className="hidden items-center gap-2 text-2xs text-brand-500 md:flex"
              title="Point-in-time 截面日：所有读数只取 knowledge_time ≤ as-of（防前视）"
            >
              <span className="whitespace-nowrap uppercase tracking-wide">
                As-of 观察日 <span className="normal-case text-brand-200">(look-ahead 边界)</span>
              </span>
              <input
                type="date"
                value={asOf}
                max={todayISO()}
                onChange={(e) => setAsOf(e.target.value)}
                className="tnum rounded-lg border border-line bg-surface-2 px-2 py-1 text-xs text-brand-900 outline-none transition-colors focus:border-accent/50"
              />
            </label>
          </ModuleHeader>
        }
      >
        <div className="px-5 py-5">
          <Outlet />
        </div>
      </ModuleShell>
    </Ctx.Provider>
  );
}
