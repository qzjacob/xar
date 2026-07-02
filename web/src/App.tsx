import { lazy, Suspense } from "react";
import { BrowserRouter, Navigate, Route, Routes, useParams } from "react-router-dom";
import { DataProvider } from "./context";

const FennyApp = lazy(() => import("./pages/fenny/FennyApp"));
import { Layout } from "./components/Layout";
import { AdminLayout } from "./components/AdminLayout";
import { ExplorationLayout } from "./components/ExplorationLayout";
import { ExplorationOverviewPage } from "./pages/exploration/ExplorationOverviewPage";
import { ExplorationSectionPage } from "./pages/exploration/ExplorationSectionPage";
import { AndyPage } from "./pages/andy/AndyPage";
import { DataRoomPage } from "./pages/genny/DataRoomPage";
import { DashboardPage } from "./pages/DashboardPage";
import { CompanyPage } from "./pages/CompanyPage";
import { SegmentPage } from "./pages/SegmentPage";
import { OpsOverviewPage } from "./pages/ops/OpsOverviewPage";
import { OntologyPage } from "./pages/ops/OntologyPage";
import { SourcesPage } from "./pages/ops/SourcesPage";
import { DataLakePage } from "./pages/ops/DataLakePage";
import { AltDataPage } from "./pages/ops/AltDataPage";
import { ModelsPage } from "./pages/ops/ModelsPage";
import { ConnectorsPage } from "./pages/ops/ConnectorsPage";
import { SkillsPage } from "./pages/ops/SkillsPage";

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

function FennyFallback() {
  return (
    <div className="flex h-full items-center justify-center bg-canvas text-xs text-slate-400">
      Loading Fenny…
    </div>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        {/* Andy — conversational analyst, the default home */}
        <Route path="/" element={<AndyPage />} />
        <Route path="/andy" element={<Navigate to="/" replace />} />

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

        {/* Fenny — structured-notes / options desk (lazy chunk isolates plotly) */}
        <Route path="/fenny/*" element={<Suspense fallback={<FennyFallback />}><FennyApp /></Suspense>} />

        {/* Exploration — frontier module (indigo shell) */}
        <Route path="/explore" element={<ExplorationLayout />}>
          <Route index element={<ExplorationOverviewPage />} />
          <Route path=":sectionId" element={<ExplorationSectionPage />} />
        </Route>

        {/* Operations console */}
        <Route path="/ops" element={<AdminLayout />}>
          <Route index element={<OpsOverviewPage />} />
          <Route path="ontology" element={<OntologyPage />} />
          <Route path="sources" element={<SourcesPage />} />
          <Route path="datalake" element={<DataLakePage />} />
          <Route path="altdata" element={<AltDataPage />} />
          <Route path="models" element={<ModelsPage />} />
          <Route path="connectors" element={<ConnectorsPage />} />
          <Route path="skills" element={<SkillsPage />} />
        </Route>

        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  );
}
