// Control-plane API client — maps to /api/ops/* (xar/api/ops.py).
import type {
  ActionResult,
  AltDataInfo,
  ConnectorsInfo,
  DataLakeInfo,
  FetchyConfig,
  FetchyInfo,
  HealthInfo,
  LakeDocsPage,
  LlmInfo,
  LlmTestResult,
  OntologyInfo,
  OpsCoverageInfo,
  SelfTest,
  SkillsInfo,
  SourcesInfo,
} from "../types-ops";
import type { AltTrackers } from "../types-alt";

async function get<T>(path: string): Promise<T> {
  const r = await fetch(path, { headers: { Accept: "application/json" } });
  if (!r.ok) throw new Error(`${path} -> ${r.status}`);
  return (await r.json()) as T;
}
async function post<T>(path: string, body?: unknown): Promise<T> {
  const r = await fetch(path, {
    method: "POST",
    headers: { Accept: "application/json", ...(body ? { "Content-Type": "application/json" } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!r.ok) throw new Error(`${path} -> ${r.status}`);
  return (await r.json()) as T;
}
async function put<T>(path: string, body: unknown): Promise<T> {
  const r = await fetch(path, {
    method: "PUT",
    headers: { Accept: "application/json", "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`${path} -> ${r.status}`);
  return (await r.json()) as T;
}

export const ops = {
  health: () => get<HealthInfo>("/api/health"),
  ontology: () => get<OntologyInfo>("/api/ops/ontology"),
  sources: () => get<SourcesInfo>("/api/ops/sources"),
  runSource: (id: string) => post<ActionResult>(`/api/ops/sources/${encodeURIComponent(id)}/run`),
  llm: () => get<LlmInfo>("/api/ops/llm"),
  testLlm: () => post<LlmTestResult>("/api/ops/llm/test"),
  fetchy: () => get<FetchyInfo>("/api/ops/fetchy"),
  setFetchy: (cfg: Partial<FetchyConfig>) => put<{ config: FetchyConfig }>("/api/ops/fetchy", cfg),
  wechatReview: (gh_id: string, action: "approve" | "block" | "pending") =>
    post<{ ok: boolean; gh_id: string; review_status: string }>(
      "/api/ops/fetchy/wechat-review", { gh_id, action }),
  connectors: () => get<ConnectorsInfo>("/api/ops/connectors"),
  skills: () => get<SkillsInfo>("/api/ops/skills"),
  datalake: () => get<DataLakeInfo>("/api/ops/datalake"),
  documents: (p: { limit?: number; offset?: number; source?: string; q?: string } = {}) => {
    const qs = new URLSearchParams();
    if (p.limit != null) qs.set("limit", String(p.limit));
    if (p.offset != null) qs.set("offset", String(p.offset));
    if (p.source) qs.set("source", p.source);
    if (p.q) qs.set("q", p.q);
    return get<LakeDocsPage>(`/api/ops/datalake/documents?${qs.toString()}`);
  },
  process: () => post<ActionResult>("/api/ops/datalake/process"),
  altdata: () => get<AltDataInfo>("/api/ops/altdata"),
  processAltdata: () => post<ActionResult>("/api/ops/altdata/process"),
  altTrackers: () => get<AltTrackers>("/api/ops/altdata/trackers"),
  selftest: () => get<SelfTest>("/api/ops/selftest"),
  coverage: () => get<OpsCoverageInfo>("/api/ops/coverage"),
};
