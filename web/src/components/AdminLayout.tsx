import { useEffect, useState } from "react";
import { Outlet, useLocation } from "react-router-dom";
import { AdminSidebar } from "./AdminSidebar";
import { ModuleHeader } from "./shell/ModuleHeader";
import { ModuleShell } from "./shell/ModuleShell";
import { ops } from "../lib/ops";
import { cn } from "../lib/format";
import type { HealthInfo } from "../types-ops";

const TITLES: Record<string, { en: string; cn: string }> = {
  "/jarvy": { en: "Overview", cn: "总览" },
  "/jarvy/fetchy": { en: "Fetchy", cn: "抓取工人管理" },
  "/jarvy/ontology": { en: "Ontology", cn: "本体" },
  "/jarvy/coverage": { en: "Coverage 360", cn: "覆盖度" },
  "/jarvy/sources": { en: "Data Sources", cn: "数据源" },
  "/jarvy/datalake": { en: "Data Lake", cn: "数据湖" },
  "/jarvy/altdata": { en: "Alt-Data AI", cn: "另类数据加工" },
  "/jarvy/models": { en: "Models & LLM", cn: "模型" },
  "/jarvy/connectors": { en: "MCP & API", cn: "连接器" },
  "/jarvy/skills": { en: "Agent Skills", cn: "技能" },
};

/**
 * Jarvy — 后端管理中心(曾用名 Ops)。统一外壳:GlobalTopBar 之下的
 * ModuleShell(统一 w-60 左栏 + header 条);模块切换由全局顶栏接管。
 */
export function AdminLayout() {
  const loc = useLocation();
  const [health, setHealth] = useState<HealthInfo | null>(null);

  useEffect(() => {
    let on = true;
    ops.health().then((h) => on && setHealth(h)).catch(() => {});
    return () => {
      on = false;
    };
  }, []);

  const title = TITLES[loc.pathname] ?? { en: "Jarvy", cn: "后台管理" };
  const provActive = health ? Object.values(health.providers).filter(Boolean).length : 0;
  const provTotal = health ? Object.keys(health.providers).length : 0;

  return (
    <ModuleShell
      sidebar={<AdminSidebar />}
      header={
        <ModuleHeader crumb="Jarvy" title={title.en} titleCn={title.cn}>
          {health && (
            <div className="hidden items-center gap-3 text-2xs text-brand-200 lg:flex">
              <span className="inline-flex items-center gap-1">
                <span className={cn("h-1.5 w-1.5 rounded-full", health.ok ? "bg-pos" : "bg-neg")} />
                {health.ok ? "healthy" : "degraded"}
              </span>
              <span className="tnum">LLM {health.llm_configured ? health.model_strong : "—"}</span>
              <span className="tnum">
                {provActive}/{provTotal} providers
              </span>
              <span className="rounded bg-surface-2 px-1.5 py-0.5 ring-1 ring-inset ring-line">
                {health.data_posture}
              </span>
            </div>
          )}
        </ModuleHeader>
      }
    >
      <div className="px-5 py-5">
        <Outlet />
      </div>
    </ModuleShell>
  );
}
