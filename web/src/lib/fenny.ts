import type { Job } from "../types-fenny";

const BASE = "/api/fenny/api/v1";

async function jget<T>(path: string): Promise<T> {
  const r = await fetch(BASE + path);
  if (!r.ok) throw new Error(`${path} -> ${r.status}`);
  return (await r.json()) as T;
}

async function jpost<T>(path: string, body: unknown): Promise<T> {
  const r = await fetch(BASE + path, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  if (!r.ok) {
    const d = await r.json().catch(() => ({}));
    throw new Error((d as { detail?: string }).detail || `${path} -> ${r.status}`);
  }
  return (await r.json()) as T;
}

/** Submit a `/jobs/{kind}` job, poll until terminal, surfacing staged partial results. */
export async function runJob(
  kind: string, body: unknown, onPartial?: (j: Job) => void, signal?: AbortSignal,
): Promise<Record<string, unknown>> {
  const { job_id } = await jpost<{ job_id: string }>(`/jobs/${kind}`, body);
  for (let i = 0; i < 240; i++) {
    if (signal?.aborted) throw new Error("aborted");
    const j = await jget<Job>(`/jobs/${job_id}`);
    onPartial?.(j);
    if (j.status === "done") return j.partial;
    if (j.status === "error") throw new Error(j.error || "job failed");
    await new Promise((r) => setTimeout(r, 700));
  }
  throw new Error("job timed out");
}

export const fennyApi = {
  health: () => jget<{ status: string }>("/health"),
  presetTermsheet: (preset: unknown) => jpost<Record<string, unknown>>("/build_termsheet", preset),
  // resolve REAL spot + realized vol + correlation from FMP for the given tickers (no manual input)
  resolveMarket: (tickers: string[]) => jpost<Record<string, unknown>>("/resolve_market", tickers),
  // async jobs
  quote: (body: unknown, onP?: (j: Job) => void, s?: AbortSignal) => runJob("quote", body, onP, s),
  solve: (body: unknown, onP?: (j: Job) => void, s?: AbortSignal) => runJob("solve", body, onP, s),
  rank: (body: unknown, onP?: (j: Job) => void, s?: AbortSignal) => runJob("rank", body, onP, s),
  marketRead: (body: unknown, onP?: (j: Job) => void, s?: AbortSignal) => runJob("market_read", body, onP, s),
  optionsAnalyze: (body: unknown, onP?: (j: Job) => void, s?: AbortSignal) => runJob("options_analyze", body, onP, s),
  optionsAdvise: (body: unknown, onP?: (j: Job) => void, s?: AbortSignal) => runJob("options_advise", body, onP, s),
  strategyBuild: (body: unknown, onP?: (j: Job) => void, s?: AbortSignal) => runJob("strategy_build", body, onP, s),
  chain: (body: unknown, onP?: (j: Job) => void, s?: AbortSignal) => runJob("chain", body, onP, s),
  // blotter (sync)
  blotter: () => jget<{ entries: Record<string, unknown>[] }>("/blotter"),
  blotterGreeks: () => jget<Record<string, unknown>>("/blotter/greeks"),
  blotterAdd: (body: unknown) => jpost<Record<string, unknown>>("/blotter", body),
  blotterUpdate: (id: string, body: unknown) =>
    fetch(`${BASE}/blotter/${id}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }).then((r) => r.json()),
  blotterRemove: (id: string) => fetch(`${BASE}/blotter/${id}`, { method: "DELETE" }).then((r) => r.json()),
};
