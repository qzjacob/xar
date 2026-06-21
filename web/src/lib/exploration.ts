// Exploration data layer — talks to /api/exploration/* (xar/api/exploration.py).
import type { ExploreOverview, ExploreSectionDetail } from "../types-exploration";

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

export const exploration = {
  overview: () => get<ExploreOverview>("/api/exploration/overview"),
  section: (id: string) => get<ExploreSectionDetail>(`/api/exploration/section/${encodeURIComponent(id)}`),
  refresh: (domain?: string) =>
    post<{ status: string; domain: string }>(
      `/api/exploration/refresh${domain ? `?domain=${encodeURIComponent(domain)}` : ""}`,
    ),
};
