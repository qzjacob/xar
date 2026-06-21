// Control-plane API client — maps to /api/ops/* (xar/api/ops.py).
import type {
  ActionResult,
  AltDataInfo,
  ConnectorsInfo,
  DataLakeInfo,
  HealthInfo,
  LakeDocsPage,
  LlmInfo,
  LlmTestResult,
  OntologyInfo,
  SelfTest,
  SkillsInfo,
  SourcesInfo,
} from "../types-ops";

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

export const ops = {
  health: () => get<HealthInfo>("/api/health"),
  ontology: () => get<OntologyInfo>("/api/ops/ontology"),
  sources: () => get<SourcesInfo>("/api/ops/sources"),
  runSource: (id: string) => post<ActionResult>(`/api/ops/sources/${encodeURIComponent(id)}/run`),
  llm: () => get<LlmInfo>("/api/ops/llm"),
  testLlm: () => post<LlmTestResult>("/api/ops/llm/test"),
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
  selftest: () => get<SelfTest>("/api/ops/selftest"),
};
