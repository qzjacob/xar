export interface DataRoomDoc {
  id: string;
  title: string;
  doc_type: string;
  source: string;
  theme: string | null;
  segment: string | null;
  company_id: string | null;
  published_at: string | null;
  meta: { filename?: string; content_type?: string; size?: number } | null;
  chunk_count: number;
}

async function j<T>(path: string): Promise<T> {
  const r = await fetch(path);
  if (!r.ok) throw new Error(`${path} -> ${r.status}`);
  return (await r.json()) as T;
}

export const dataroomApi = {
  list: (p: { theme?: string; segment?: string; company_id?: string; q?: string }) => {
    const qs = new URLSearchParams();
    Object.entries(p).forEach(([k, v]) => { if (v) qs.set(k, v); });
    return j<DataRoomDoc[]>(`/api/genny/dataroom/docs?${qs.toString()}`);
  },
  upload: async (file: File, fields: { theme: string; segment?: string; company_id?: string; doc_type?: string; title?: string }) => {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("theme", fields.theme);
    if (fields.segment) fd.append("segment", fields.segment);
    if (fields.company_id) fd.append("company_id", fields.company_id);
    fd.append("doc_type", fields.doc_type || "report");
    if (fields.title) fd.append("title", fields.title);
    const r = await fetch("/api/genny/dataroom/upload", { method: "POST", body: fd });
    if (!r.ok) {
      const detail = await r.json().catch(() => ({}));
      throw new Error((detail as { detail?: string }).detail || `upload -> ${r.status}`);
    }
    return r.json();
  },
  remove: (id: string) =>
    fetch(`/api/genny/dataroom/docs/${id}`, { method: "DELETE" }).then((r) => r.json()),
  downloadUrl: (id: string) => `/api/genny/dataroom/docs/${id}/download`,
};
