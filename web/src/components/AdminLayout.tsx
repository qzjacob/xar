import { useEffect, useState } from "react";
import { ArrowLeft } from "lucide-react";
import { Outlet, useLocation, useNavigate } from "react-router-dom";
import { AdminSidebar } from "./AdminSidebar";
import { ops } from "../lib/ops";
import { cn } from "../lib/format";
import type { HealthInfo } from "../types-ops";

const TITLES: Record<string, { en: string; cn: string }> = {
  "/ops": { en: "Overview", cn: "总览" },
  "/ops/ontology": { en: "Ontology", cn: "本体" },
  "/ops/sources": { en: "Data Sources", cn: "数据源" },
  "/ops/datalake": { en: "Data Lake", cn: "数据湖" },
  "/ops/altdata": { en: "Alt-Data AI", cn: "另类数据加工" },
  "/ops/models": { en: "Models & LLM", cn: "模型" },
  "/ops/connectors": { en: "MCP & API", cn: "连接器" },
  "/ops/skills": { en: "Agent Skills", cn: "技能" },
};

/**
 * Standalone admin shell for the /ops/* control plane — its own sidebar + top
 * bar (live health), independent of the research terminal's chrome and data
 * context. Cross-linked to the terminal via the top-bar + sidebar back links.
 */
export function AdminLayout() {
  const nav = useNavigate();
  const loc = useLocation();
  const [health, setHealth] = useState<HealthInfo | null>(null);

  useEffect(() => {
    let on = true;
    ops.health().then((h) => on && setHealth(h)).catch(() => {});
    return () => {
      on = false;
    };
  }, []);

  const title = TITLES[loc.pathname] ?? { en: "Operations", cn: "控制台" };
  const provActive = health ? Object.values(health.providers).filter(Boolean).length : 0;
  const provTotal = health ? Object.keys(health.providers).length : 0;

  return (
    <div className="flex h-full w-full overflow-hidden bg-canvas text-brand-900">
      <AdminSidebar currentPath={loc.pathname} onNavigate={(r) => nav(r)} onBack={() => nav("/genny")} />
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex h-14 shrink-0 items-center justify-between gap-4 border-b border-line bg-surface px-5">
          <div className="flex min-w-0 items-center gap-2">
            <span className="text-2xs uppercase tracking-wide text-slate-400">Operations</span>
            <span className="text-slate-300">/</span>
            <span className="truncate text-sm font-semibold text-brand-900">{title.en}</span>
            <span className="truncate text-xs text-slate-400">{title.cn}</span>
          </div>
          <div className="flex items-center gap-3">
            {health && (
              <div className="hidden items-center gap-3 text-2xs text-slate-500 lg:flex">
                <span className="inline-flex items-center gap-1">
                  <span className={cn("h-1.5 w-1.5 rounded-full", health.ok ? "bg-pos" : "bg-neg")} />
                  {health.ok ? "healthy" : "degraded"}
                </span>
                <span className="tnum">
                  LLM {health.llm_configured ? health.model_strong : "—"}
                </span>
                <span className="tnum">
                  {provActive}/{provTotal} providers
                </span>
                <span className="rounded bg-surface-2 px-1.5 py-0.5 ring-1 ring-inset ring-line">
                  {health.data_posture}
                </span>
              </div>
            )}
            <button
              type="button"
              onClick={() => nav("/genny")}
              className="inline-flex items-center gap-1.5 rounded-lg border border-line bg-surface px-2.5 py-1.5 text-xs font-medium text-slate-400 transition hover:bg-canvas focus-visible:ring-2 focus-visible:ring-accent/40"
            >
              <ArrowLeft size={13} strokeWidth={2.5} /> Terminal
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
