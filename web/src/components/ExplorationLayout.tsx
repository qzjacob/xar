import { useEffect, useState } from "react";
import { RefreshCw } from "lucide-react";
import { Outlet, useLocation, useNavigate } from "react-router-dom";
import { ExplorationSidebar } from "./ExplorationSidebar";
import { exploration } from "../lib/exploration";
import type { ExploreOverview } from "../types-exploration";

/**
 * Standalone Exploration shell — frontier-of-knowledge module, peer to the
 * Research Terminal and Operations Console. Its own indigo chrome + section nav
 * (data-driven from /api/exploration/overview), independent of the terminal's
 * investment data context.
 */
export function ExplorationLayout() {
  const nav = useNavigate();
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

  const sectionId = loc.pathname.startsWith("/explore/")
    ? loc.pathname.slice("/explore/".length)
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
    <div className="flex h-full w-full overflow-hidden bg-canvas text-brand-900">
      <ExplorationSidebar
        sections={ov?.sections ?? []}
        currentPath={loc.pathname}
        onNavigate={(r) => nav(r)}
        onBack={() => nav("/genny")}
      />
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex h-14 shrink-0 items-center justify-between gap-4 border-b border-line bg-surface px-5">
          <div className="flex min-w-0 items-center gap-2">
            <span className="text-2xs uppercase tracking-wide text-slate-400">Exploration</span>
            <span className="text-slate-300">/</span>
            <span className="truncate text-sm font-semibold text-brand-900">
              {current ? current.name : "Frontier Overview"}
            </span>
            {current && <span className="truncate text-xs text-slate-400">{current.nameCn}</span>}
          </div>
          <div className="flex items-center gap-3">
            <div className="hidden items-center gap-3 text-2xs text-slate-500 lg:flex">
              <span className="tnum">{t.papers ?? 0} preprints</span>
              <span className="tnum">{t.articles ?? 0} articles</span>
              <span className="tnum">{t.voices ?? 0} voices</span>
              <span className="tnum">{t.fronts ?? 0} fronts</span>
              <span className="rounded bg-explore-50 px-1.5 py-0.5 text-explore-700 ring-1 ring-inset ring-explore/20">
                arXiv · Journals · X
              </span>
            </div>
            <button
              type="button"
              onClick={refresh}
              disabled={busy}
              className="inline-flex items-center gap-1.5 rounded-lg border border-line bg-surface px-2.5 py-1.5 text-xs font-medium text-slate-400 transition hover:bg-canvas disabled:opacity-50"
              title="Ingest latest + re-synthesize fronts"
            >
              <RefreshCw size={13} strokeWidth={2.5} className={busy ? "animate-spin" : ""} />
              {busy ? "Refreshing…" : "Refresh"}
            </button>
          </div>
        </div>
        <main className="scroll-thin min-w-0 flex-1 overflow-y-auto px-5 py-5">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
