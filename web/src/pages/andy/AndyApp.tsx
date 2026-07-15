import { Navigate, Route, Routes } from "react-router-dom";
import { AndyLayout } from "../../components/andy/AndyLayout";
import { AndyOverviewPage } from "./AndyOverviewPage";
import { AndyFlowPage } from "./AndyFlowPage";
import { AndyMetricsPage } from "./AndyMetricsPage";
import { AndyMetricDetailPage } from "./AndyMetricDetailPage";
import { AndyOverclaimsPage } from "./AndyOverclaimsPage";
import { AndyWallsPage } from "./AndyWallsPage";
import { AndySourcesPage } from "./AndySourcesPage";

/** XAR Andy — theory-anchored macro-indicator terminal (lazy-loaded; shares the
 * plotly chunk with Fenny via components/charts/PlotlyChart). */
export default function AndyApp() {
  return (
    <Routes>
      <Route element={<AndyLayout />}>
        <Route index element={<AndyOverviewPage />} />
        <Route path="flow" element={<AndyFlowPage />} />
        <Route path="metrics" element={<AndyMetricsPage />} />
        <Route path="metrics/:key" element={<AndyMetricDetailPage />} />
        <Route path="overclaims" element={<AndyOverclaimsPage />} />
        <Route path="walls" element={<AndyWallsPage />} />
        <Route path="sources" element={<AndySourcesPage />} />
        <Route path="*" element={<Navigate to="/andy" replace />} />
      </Route>
    </Routes>
  );
}
