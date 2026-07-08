// Unified capability-run status — mirrors capability_runs (xar/capabilities/runs.py) +
// GET /api/run/{run_id}. Used by the shared poll client (lib/runs.ts) for slow (build) capabilities.

export type RunState = "queued" | "running" | "done" | "error";

export interface RunStatus {
  run_id: string;
  capability: string;
  status: RunState;
  result?: Record<string, unknown> | null;
  error?: string | null;
  origin?: string | null;
}

// POST /api/run/{name} response: fast reads return {status:'done', result}; slow builds
// return {run_id, status, dedup?}.
export interface RunScheduled {
  run_id?: string;
  status: RunState | "done";
  result?: Record<string, unknown>;
  dedup?: boolean;
}
