import { useEffect, useState } from "react";
import { RefreshCw } from "lucide-react";
import { Outlet, useLocation } from "react-router-dom";
import { ExplorationSidebar } from "./ExplorationSidebar";
import { ModuleHeader } from "./shell/ModuleHeader";
import { ModuleShell } from "./shell/ModuleShell";
import { exploration } from "../lib/exploration";
import type { ExploreOverview } from "../types-exploration";

/**
 * Romy — 前沿探索模块(曾用名 Exploration)。统一外壳:GlobalTopBar 之下的
 * ModuleShell;左栏导航由 /api/exploration/overview 数据驱动。
 */
export function ExplorationLayout() {
  const loc = useLocation();
  const [ov, setOv] = useState<ExploreOverview | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let on = true;
    exploration.overview().then((o) => on && setOv(o)).catch(() => {});
    return () => {
      on = false;
    };
  }, []);

  const sectionId = loc.pathname.startsWith("/romy/")
    ? loc.pathname.slice("/romy/".length)
    : null;
  const current = ov?.sections.find((s) => s.id === sectionId);
  const t = ov?.totals ?? {};

  async function refresh() {
    setBusy(true);
    try {
      await exploration.refresh(sectionId || undefined);
    } catch {
      /* ignore */
    } finally {
      setBusy(false);
    }
  }

  return (
    <ModuleShell
      sidebar={<ExplorationSidebar sections={ov?.sections ?? []} />}
      header={
        <ModuleHeader
          crumb="Romy"
          title={current ? current.name : "Frontier Overview"}
          titleCn={current?.nameCn}
        >
          <div className="hidden items-center gap-3 text-2xs text-brand-200 lg:flex">
            <span className="tnum">{t.papers ?? 0} preprints</span>
            <span className="tnum">{t.articles ?? 0} articles</span>
            <span className="tnum">{t.voices ?? 0} voices</span>
            <span className="tnum">{t.fronts ?? 0} fronts</span>
            <span className="rounded bg-accent-50 px-1.5 py-0.5 text-accent-100 ring-1 ring-inset ring-accent/20">
              arXiv · Journals · X
            </span>
          </div>
          <button
            type="button"
            onClick={refresh}
            disabled={busy}
            className="inline-flex items-center gap-1.5 rounded-lg border border-line bg-surface px-2.5 py-1.5 text-xs font-medium text-brand-500 transition hover:bg-canvas disabled:opacity-50"
            title="Ingest latest + re-synthesize fronts"
          >
            <RefreshCw size={13} strokeWidth={2.5} className={busy ? "animate-spin" : ""} />
            {busy ? "Refreshing…" : "Refresh"}
          </button>
        </ModuleHeader>
      }
    >
      <div className="px-5 py-5">
        <Outlet />
      </div>
    </ModuleShell>
  );
}
