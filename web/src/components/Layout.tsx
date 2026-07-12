import { Outlet, useLocation, useNavigate } from "react-router-dom";
import { ModuleShell } from "./shell/ModuleShell";
import { Sidebar } from "./Sidebar";
import { TopBar } from "./TopBar";
import { DecisionRail } from "./DecisionRail";
import { useData } from "../context";

/** Persistent chrome (sidebar / topbar / decision rail) around the routed page. */
export function Layout() {
  const nav = useNavigate();
  const loc = useLocation();
  const { overview, companies, theme, setTheme, market, setMarket, period, setPeriod, loading, error } =
    useData();

  if (error) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-2 text-center">
        <div className="text-sm font-semibold text-neg">无法连接后端 API</div>
        <div className="max-w-md text-2xs text-brand-500">{error}</div>
        <div className="text-2xs text-brand-500">确认 XAR 后端在 :8000 运行（/api/ui/overview）。</div>
      </div>
    );
  }
  if (loading || !overview) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-brand-500">
        Loading XAR terminal…
      </div>
    );
  }

  const { segments, coverage, decision, regime } = overview;
  const selectedSegmentId = loc.pathname.startsWith("/genny/segment/")
    ? decodeURIComponent(loc.pathname.split("/")[3] || "")
    : null;

  return (
    <ModuleShell
      sidebar={
        <Sidebar
          coverage={coverage}
          segments={segments}
          companies={companies}
          selectedSegmentId={selectedSegmentId}
          onSelectSegment={(id) => nav(id ? `/genny/segment/${id}` : "/genny")}
          onCompany={(id) => nav(`/genny/company/${id}`)}
        />
      }
      header={
        <TopBar
          coverage={coverage}
          regime={regime}
          theme={theme}
          onTheme={setTheme}
          market={market}
          onMarket={setMarket}
          period={period}
          onPeriod={setPeriod}
        />
      }
      rail={
        <DecisionRail
          decision={decision}
          segments={segments}
          onSelectSegment={(id) => id && nav(`/genny/segment/${id}`)}
        />
      }
    >
      <div className="px-5 py-5">
        <Outlet />
      </div>
    </ModuleShell>
  );
}
