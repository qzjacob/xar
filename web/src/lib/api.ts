// Data access layer — talks to the real XAR backend (/api/ui/*). Each method
// maps 1:1 to a FastAPI endpoint in xar/api/dashboard.py, which computes these
// shapes from the live database (companies/prices/fundamentals/kg_events/...).
import type {
  Catalyst,
  Company,
  CompanyDetail,
  Overview,
  SegmentDetail,
  Signal,
} from "../types";
import type { ThesisBuildResult } from "../types-thesis";
import type { AltData } from "../types-alt";

async function get<T>(path: string): Promise<T> {
  const r = await fetch(path, { headers: { Accept: "application/json" } });
  if (!r.ok) throw new Error(`${path} -> ${r.status}`);
  return (await r.json()) as T;
}

async function post<T>(path: string): Promise<T> {
  const r = await fetch(path, { method: "POST", headers: { Accept: "application/json" } });
  if (!r.ok) throw new Error(`${path} -> ${r.status}`);
  return (await r.json()) as T;
}

const T = (theme: string) => `?theme=${encodeURIComponent(theme)}`;

export const api = {
  getOverview: (theme = "ai_optical") => get<Overview>(`/api/ui/overview${T(theme)}`),
  getCompanies: (theme = "ai_optical") => get<Company[]>(`/api/ui/companies${T(theme)}`),
  getSignals: (theme = "ai_optical") => get<Signal[]>(`/api/ui/signals${T(theme)}`),
  getCatalysts: (theme = "ai_optical") => get<Catalyst[]>(`/api/ui/catalysts${T(theme)}`),
  getCompany: (id: string, theme?: string) =>
    get<CompanyDetail>(`/api/ui/company/${encodeURIComponent(id)}${theme ? T(theme) : ""}`),
  getSegment: (id: string) => get<SegmentDetail>(`/api/ui/segment/${encodeURIComponent(id)}`),
  /** Build/refresh the investment thesis — sync, can take ~60s; await it. */
  buildThesis: (cid: string, force = false) =>
    post<ThesisBuildResult>(
      `/api/thesis/${encodeURIComponent(cid)}/build?force=${force ? "true" : "false"}`,
    ),
  /** Standalone alt-data refetch — same shape as the company payload's `alt` block. */
  altCompany: (cid: string) => get<AltData>(`/api/alt/company/${encodeURIComponent(cid)}`),
};
