import { CandlestickChart, LineChart, ListOrdered, Sparkles } from "lucide-react";
import { Navigate, Outlet, Route, Routes } from "react-router-dom";
import { ModuleShell } from "../../components/shell/ModuleShell";
import { SidebarFrame } from "../../components/shell/SidebarFrame";
import { SidebarNav, type SideNavItem } from "../../components/shell/SidebarNav";
import { Finder } from "./Finder";
import { MarketRead } from "./MarketRead";
import { OptionsDesk } from "./OptionsDesk";
import { QuoteDesk } from "./QuoteDesk";

const NAV: SideNavItem[] = [
  { to: "/fenny", label: "Quotation Desk", cn: "报价台", icon: CandlestickChart, exact: true },
  { to: "/fenny/market", label: "Market Read", cn: "市场解读", icon: Sparkles },
  { to: "/fenny/finder", label: "Underlying Finder", cn: "标的筛选", icon: ListOrdered },
  { to: "/fenny/options", label: "Options Desk", cn: "期权台", icon: LineChart },
];

/** 统一外壳:左栏承载 4 个工作台(原页签),路由化后可深链。 */
function FennyLayout() {
  return (
    <ModuleShell
      sidebar={
        <SidebarFrame title="Fenny" titleCn="结构化票据" badge="Desk">
          <SidebarNav heading="Workspaces · 工作台" items={NAV} />
        </SidebarFrame>
      }
    >
      <Outlet />
    </ModuleShell>
  );
}

/** XAR Fenny — structured-notes / options desk (lazy-loaded; carries plotly in its chunk). */
export default function FennyApp() {
  return (
    <Routes>
      <Route element={<FennyLayout />}>
        <Route index element={<QuoteDesk />} />
        <Route path="market" element={<MarketRead />} />
        <Route path="finder" element={<Finder />} />
        <Route path="options" element={<OptionsDesk />} />
        <Route path="*" element={<Navigate to="/fenny" replace />} />
      </Route>
    </Routes>
  );
}
