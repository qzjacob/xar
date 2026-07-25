import type { PhannyCalBucket, PhannyPortfolio, PhannySchedule } from "../types-phanny";

const BASE = "/api/phanny";

async function jget<T>(path: string): Promise<T> {
  const r = await fetch(BASE + path);
  if (!r.ok) throw new Error(`${path} -> ${r.status}`);
  return (await r.json()) as T;
}

async function jpost<T>(path: string, body?: unknown): Promise<T> {
  const r = await fetch(BASE + path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body ?? {}),
  });
  if (!r.ok) {
    const d = await r.json().catch(() => ({}));
    throw new Error((d as { detail?: string }).detail || `${path} -> ${r.status}`);
  }
  return (await r.json()) as T;
}

export const phannyApi = {
  portfolio: () => jget<PhannyPortfolio>("/portfolio"),
  calibration: () => jget<Record<string, PhannyCalBucket>>("/calibration"),
  buildBook: (force = false) => jpost<PhannySchedule>(`/book/build?force=${force}`),
  buildVerdict: (cid: string, force = false) => jpost<PhannySchedule>(`/verdict/${cid}/build?force=${force}`),
};
