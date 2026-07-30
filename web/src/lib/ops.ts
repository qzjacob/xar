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
  MonitorAlert,
  MonitorHistoryRow,
  MonitorInfo,
  MonitorSummary,
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
  wechatPromote: (gh_id: string, action: "approve" | "reject" | "reset") =>
    post<{ ok: boolean; gh_id: string; promote_status: string }>(
      "/api/ops/fetchy/wechat-promote", { gh_id, action }),
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
  // 任务监控。monitor() 读的是巡检持久化的快照(与告警判定同源);fresh 仅供排障。
  monitor: (fresh = false) => get<MonitorInfo>(`/api/ops/monitor${fresh ? "?fresh=1" : ""}`),
  monitorSummary: () => get<MonitorSummary>("/api/ops/monitor/summary"),
  monitorAlerts: (scope: "open" | "recent" = "open") =>
    get<{ alerts: MonitorAlert[]; openAlerts: number; openCritical: number }>(
      `/api/ops/monitor/alerts?scope=${scope}`),
  monitorHistory: (p: { task?: string; hours?: number } = {}) => {
    const qs = new URLSearchParams();
    if (p.task) qs.set("task", p.task);
    if (p.hours != null) qs.set("hours", String(p.hours));
    return get<{ rows: MonitorHistoryRow[]; task: string | null; hours: number }>(
      `/api/ops/monitor/history?${qs.toString()}`);
  },
  monitorAck: (id: number) => post<{ alert: MonitorAlert }>(`/api/ops/monitor/alerts/${id}/ack`),
  monitorResolveAlert: (id: number) =>
    post<{ alert: MonitorAlert }>(`/api/ops/monitor/alerts/${id}/resolve`),
  monitorAction: (action: string) =>
    post<{ action: string; result: Record<string, unknown> }>("/api/ops/monitor/actions", { action }),
  monitorMute: (hours: number, tasks?: string[]) =>
    put<{ muted: boolean; until?: string; tasks?: string[] }>("/api/ops/monitor/mute",
      { hours, ...(tasks ? { tasks } : {}) }),
};
