import { Outlet, useLocation, useNavigate } from "react-router-dom";
import { AppShell } from "./AppShell";
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
        <div className="max-w-md text-2xs text-slate-400">{error}</div>
        <div className="text-2xs text-slate-400">确认 XAR 后端在 :8000 运行（/api/ui/overview）。</div>
      </div>
    );
  }
  if (loading || !overview) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-slate-400">
        Loading XAR terminal…
      </div>
    );
  }

  const { segments, coverage, decision, regime } = overview;
  const selectedSegmentId = loc.pathname.startsWith("/segment/")
    ? decodeURIComponent(loc.pathname.split("/")[2] || "")
    : null;

  return (
    <AppShell
      sidebar={
        <Sidebar
          coverage={coverage}
          segments={segments}
          companies={companies}
          currentPath={loc.pathname}
          onNavigate={(route) => nav(route)}
          selectedSegmentId={selectedSegmentId}
          onSelectSegment={(id) => nav(id ? `/segment/${id}` : "/")}
          onCompany={(id) => nav(`/company/${id}`)}
        />
      }
      topbar={
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
          onSelectSegment={(id) => id && nav(`/segment/${id}`)}
        />
      }
    >
      <Outlet />
    </AppShell>
  );
}
