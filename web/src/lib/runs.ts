// Shared capability-run client (UA-P4) — the DB-backed, minutes-scale port of Fenny's
// runJob poll (lib/fenny.ts). Any UI "run this analysis" button uses this: POST /api/run/{name}
// then poll GET /api/run/{run_id} until done/error. Fast (read) capabilities return inline.
import type { RunScheduled, RunStatus } from "../types-runs";

async function jpost<T>(path: string, body: unknown): Promise<T> {
  const r = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(body ?? {}),
  });
  if (!r.ok) throw new Error(`${path} -> ${r.status}`);
  return (await r.json()) as T;
}

async function jget<T>(path: string): Promise<T> {
  const r = await fetch(path, { headers: { Accept: "application/json" } });
  if (!r.ok) throw new Error(`${path} -> ${r.status}`);
  return (await r.json()) as T;
}

/**
 * Run a capability end-to-end. Fast reads resolve immediately with {status:'done', result};
 * slow builds schedule then poll GET /api/run/{run_id} @1.5s up to `maxPolls` (default 400 ≈ 10min).
 */
export async function runCapability(
  name: string,
  args: unknown,
  onPoll?: (r: RunStatus) => void,
  signal?: AbortSignal,
  opts?: { intervalMs?: number; maxPolls?: number },
): Promise<RunStatus> {
  const sched = await jpost<RunScheduled>(`/api/run/${encodeURIComponent(name)}`, args);
  // fast/read capability → done inline (no run_id)
  if (!sched.run_id) {
    return {
      run_id: "", capability: name, status: (sched.status as RunStatus["status"]) ?? "done",
      result: sched.result ?? null,
    };
  }
  const interval = opts?.intervalMs ?? 1500;
  const maxPolls = opts?.maxPolls ?? 400;
  for (let i = 0; i < maxPolls; i++) {
    if (signal?.aborted) throw new Error("aborted");
    const st = await jget<RunStatus>(`/api/run/${sched.run_id}`);
    onPoll?.(st);
    if (st.status === "done" || st.status === "error") return st;
    await new Promise((r) => setTimeout(r, interval));
  }
  throw new Error("run timed out");
}
