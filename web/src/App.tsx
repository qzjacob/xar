import { lazy, Suspense } from "react";
import { BrowserRouter, Navigate, Outlet, Route, Routes, useLocation, useParams } from "react-router-dom";
import { DataProvider } from "./context";

const FennyApp = lazy(() => import("./pages/fenny/FennyApp"));
const AndyApp = lazy(() => import("./pages/andy/AndyApp"));
import { Layout } from "./components/Layout";
import { AdminLayout } from "./components/AdminLayout";
import { ExplorationLayout } from "./components/ExplorationLayout";
import { GlobalTopBar } from "./components/shell/GlobalTopBar";
import { ExplorationOverviewPage } from "./pages/exploration/ExplorationOverviewPage";
import { ExplorationSectionPage } from "./pages/exploration/ExplorationSectionPage";
import { ChathyPage } from "./pages/chathy/ChathyPage";
import { DataRoomPage } from "./pages/genny/DataRoomPage";
import { DashboardPage } from "./pages/DashboardPage";
import { CompanyPage } from "./pages/CompanyPage";
import { SegmentPage } from "./pages/SegmentPage";
import { OpsOverviewPage } from "./pages/ops/OpsOverviewPage";
import { OntologyPage } from "./pages/ops/OntologyPage";
import { CoveragePage } from "./pages/ops/CoveragePage";
import { SourcesPage } from "./pages/ops/SourcesPage";
import { DataLakePage } from "./pages/ops/DataLakePage";
import { AltDataPage } from "./pages/ops/AltDataPage";
import { ModelsPage } from "./pages/ops/ModelsPage";
import { ConnectorsPage } from "./pages/ops/ConnectorsPage";
import { SkillsPage } from "./pages/ops/SkillsPage";

/** 全局外壳:常驻顶栏(logo + 模块页签)渲染一次,切模块不重挂;下方是模块自己的行框架。 */
function AppChrome() {
  return (
    <div className="flex h-screen flex-col overflow-hidden bg-canvas">
      <GlobalTopBar />
      <div className="flex min-h-0 flex-1 flex-col">
        <Outlet />
      </div>
    </div>
  );
}

/** Genny — research terminal (chrome + shared dashboard data context). */
function GennyShell() {
  return (
    <DataProvider>
      <Layout />
    </DataProvider>
  );
}

/** Redirect a legacy top-level path (/segment/:id, /company/:id) under /genny. */
function LegacyRedirect({ to }: { to: "segment" | "company" }) {
  const { id } = useParams();
  return <Navigate to={`/genny/${to}/${id ?? ""}`} replace />;
}

/** Redirect a whole legacy prefix (e.g. /ops/* → /jarvy/*) keeping the deep path. */
function LegacyPrefixRedirect({ from, to }: { from: string; to: string }) {
  const { pathname, search } = useLocation();
  return <Navigate to={pathname.replace(from, to) + search} replace />;
}

function LazyFallback({ name }: { name: string }) {
  return (
    <div className="flex h-full items-center justify-center bg-canvas text-xs text-brand-500">
      Loading {name}…
    </div>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<AppChrome />}>
          {/* Chathy — conversational analyst, the default home */}
          <Route path="/" element={<ChathyPage />} />
          <Route path="/chathy" element={<Navigate to="/" replace />} />

          {/* Genny — research terminal */}
          <Route path="/genny" element={<GennyShell />}>
            <Route index element={<DashboardPage />} />
            <Route path="dataroom" element={<DataRoomPage />} />
            <Route path="segment/:id" element={<SegmentPage />} />
            <Route path="company/:id" element={<CompanyPage />} />
            <Route path="*" element={<DashboardPage />} />
          </Route>

          {/* legacy path redirects (old bookmarks) */}
          <Route path="/segment/:id" element={<LegacyRedirect to="segment" />} />
          <Route path="/company/:id" element={<LegacyRedirect to="company" />} />

          {/* Andy — macro-indicator terminal (lazy chunk; shares the plotly chunk with Fenny) */}
          <Route path="/andy/*" element={<Suspense fallback={<LazyFallback name="Andy" />}><AndyApp /></Suspense>} />

          {/* Fenny — structured-notes / options desk (lazy chunk isolates plotly) */}
          <Route path="/fenny/*" element={<Suspense fallback={<LazyFallback name="Fenny" />}><FennyApp /></Suspense>} />

          {/* Romy — frontier-exploration module (曾用名 Explore) */}
          <Route path="/romy" element={<ExplorationLayout />}>
            <Route index element={<ExplorationOverviewPage />} />
            <Route path=":sectionId" element={<ExplorationSectionPage />} />
          </Route>
          <Route path="/explore/*" element={<LegacyPrefixRedirect from="/explore" to="/romy" />} />

          {/* Jarvy — 后端管理中心 (曾用名 Ops) */}
          <Route path="/jarvy" element={<AdminLayout />}>
            <Route index element={<OpsOverviewPage />} />
            <Route path="ontology" element={<OntologyPage />} />
            <Route path="coverage" element={<CoveragePage />} />
            <Route path="sources" element={<SourcesPage />} />
            <Route path="datalake" element={<DataLakePage />} />
            <Route path="altdata" element={<AltDataPage />} />
            <Route path="models" element={<ModelsPage />} />
            <Route path="connectors" element={<ConnectorsPage />} />
            <Route path="skills" element={<SkillsPage />} />
          </Route>
          <Route path="/ops/*" element={<LegacyPrefixRedirect from="/ops" to="/jarvy" />} />

          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
