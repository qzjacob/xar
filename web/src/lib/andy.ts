// Andy data layer — talks to /api/andy/* (vendored slx app + XAR-native 勾稽 routes).
// All reading endpoints are point-in-time: pass the module-wide as_of so nothing
// can look ahead of the observation date. link/* is a frozen contract that may
// 404 until the backend lands — callers must catch and degrade gracefully.
import type {
  AndyAnchorsList,
  AndyChainResponse,
  AndyClaimsList,
  AndyEvaluateResult,
  AndyFlowResponse,
  AndyMacroResponse,
  AndyMetricReading,
  AndyMetricsList,
  AndySourcesResponse,
  LinkMetricDetail,
  LinkThemeResponse,
  LinkThemesResponse,
} from "../types-andy";

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

const q = (params: Record<string, string | number | undefined>): string => {
  const usp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== "") usp.set(k, String(v));
  const s = usp.toString();
  return s ? `?${s}` : "";
};

/** Local calendar date (YYYY-MM-DD) — the default as-of boundary. */
export function todayISO(): string {
  const d = new Date();
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

export const andy = {
  metrics: (opts: { family?: string; hardness?: string } = {}) =>
    get<AndyMetricsList>(`/api/andy/metrics${q(opts)}`),
  metric: (key: string, asOf: string, nPoints = 12) =>
    get<AndyMetricReading>(
      `/api/andy/metrics/${encodeURIComponent(key)}${q({ as_of: asOf, n_points: nPoints })}`,
    ),
  anchors: () => get<AndyAnchorsList>("/api/andy/registry/anchors"),
  sources: () => get<AndySourcesResponse>("/api/andy/sources"),
  overclaims: (logLimit = 5) => get<AndyClaimsList>(`/api/andy/overclaims${q({ log_limit: logLimit })}`),
  evaluate: (asOf: string) => post<AndyEvaluateResult>(`/api/andy/overclaims/evaluate${q({ as_of: asOf })}`),
  // 勾稽 crosswalk (frozen contract; may 404 until the backend lands)
  linkThemes: () => get<LinkThemesResponse>("/api/andy/link/themes"),
  linkTheme: (theme: string, asOf: string) =>
    get<LinkThemeResponse>(`/api/andy/link/theme/${encodeURIComponent(theme)}${q({ as_of: asOf })}`),
  linkMetric: (key: string) =>
    get<LinkMetricDetail>(`/api/andy/link/metric/${encodeURIComponent(key)}`),
  // 资金流策略面(XAR-native shadow route;与勾稽同约:404 时调用方优雅降级)
  flow: (asOf: string) => get<AndyFlowResponse>(`/api/andy/flow${q({ as_of: asOf })}`),
  // 宏观数据库台 + 传导链(AM;XAR-native shadow routes)
  macro: (asOf: string) => get<AndyMacroResponse>(`/api/andy/macro${q({ as_of: asOf })}`),
  chain: (key: string, asOf: string, depth = 3) =>
    get<AndyChainResponse>(
      `/api/andy/link/chain/${encodeURIComponent(key)}${q({ as_of: asOf, depth })}`,
    ),
};
