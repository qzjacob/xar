import { BrowserRouter, Route, Routes } from "react-router-dom";
import { DataProvider } from "./context";
import { Layout } from "./components/Layout";
import { AdminLayout } from "./components/AdminLayout";
import { ExplorationLayout } from "./components/ExplorationLayout";
import { ExplorationOverviewPage } from "./pages/exploration/ExplorationOverviewPage";
import { ExplorationSectionPage } from "./pages/exploration/ExplorationSectionPage";
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

/** Research terminal shell — its own chrome + shared dashboard data context. */
function TerminalShell() {
  return (
    <DataProvider>
      <Layout />
    </DataProvider>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        {/* Research terminal (navy chrome, dashboard data context) */}
        <Route element={<TerminalShell />}>
          <Route path="/" element={<DashboardPage />} />
          <Route path="/segment/:id" element={<SegmentPage />} />
          <Route path="/company/:id" element={<CompanyPage />} />
          <Route path="*" element={<DashboardPage />} />
        </Route>

        {/* Exploration — frontier-of-knowledge module (its own indigo shell) */}
        <Route path="/explore" element={<ExplorationLayout />}>
          <Route index element={<ExplorationOverviewPage />} />
          <Route path=":sectionId" element={<ExplorationSectionPage />} />
        </Route>

        {/* Standalone admin / operations console (its own shell, no terminal data) */}
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
      </Routes>
    </BrowserRouter>
  );
}
