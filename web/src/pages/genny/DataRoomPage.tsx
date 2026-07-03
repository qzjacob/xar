import { Download, FileText, FolderOpen, Loader2, Trash2, UploadCloud } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useData } from "../../context";
import { dataroomApi, type DataRoomDoc } from "../../lib/dataroom";
import { Card } from "../../components/ui/Card";
import { SectionHeader } from "../../components/ui/SectionHeader";
import { cn } from "../../lib/format";

function fmtSize(n?: number): string {
  if (!n) return "—";
  if (n >= 1e6) return `${(n / 1e6).toFixed(1)} MB`;
  if (n >= 1e3) return `${(n / 1e3).toFixed(0)} KB`;
  return `${n} B`;
}

export function DataRoomPage() {
  const { overview } = useData();
  const themes = overview?.coverage?.themes ?? [];
  const segments = overview?.segments ?? [];
  const [params, setParams] = useSearchParams();
  const theme = params.get("theme") || "";
  const segment = params.get("segment") || "";

  const [docs, setDocs] = useState<DataRoomDoc[]>([]);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const [pending, setPending] = useState<File | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    try { setDocs(await dataroomApi.list({ theme: theme || undefined, segment: segment || undefined })); }
    catch (e) { setErr(String(e)); }
    finally { setLoading(false); }
  }, [theme, segment]);
  useEffect(() => { void refresh(); }, [refresh]);

  const setFilter = (k: string, v: string) => {
    const p = new URLSearchParams(params);
    if (v) p.set(k, v); else p.delete(k);
    setParams(p, { replace: true });
  };

  const doUpload = useCallback(async () => {
    if (!pending || !theme) { setErr(!theme ? "Pick a theme first" : "Pick a file"); return; }
    setBusy(true); setErr(null);
    try {
      await dataroomApi.upload(pending, { theme, segment: segment || undefined });
      setPending(null);
      if (fileRef.current) fileRef.current.value = "";
      // poll for chunking to complete (indexing runs in the background)
      await refresh();
      setTimeout(() => { void refresh(); }, 2500);
    } catch (e) { setErr(String(e instanceof Error ? e.message : e)); }
    finally { setBusy(false); }
  }, [pending, theme, segment, refresh]);

  const del = useCallback(async (id: string) => {
    await dataroomApi.remove(id);
    setDocs((d) => d.filter((x) => x.id !== id));
  }, []);

  const segOptions = useMemo(() => segments, [segments]);

  return (
    <div className="mx-auto max-w-[1100px] px-5 py-5">
      <SectionHeader icon={<FolderOpen size={16} />} title="Data Room"
        titleCn="研究文档库 · 上传报告/纪要，自动入库并可被 Chathy 检索引用" />

      {/* upload + filters */}
      <Card className="mt-3 p-4">
        <div className="flex flex-wrap items-end gap-3">
          <label className="flex flex-col gap-1">
            <span className="text-2xs uppercase tracking-wide text-slate-500">Theme</span>
            <select value={theme} onChange={(e) => setFilter("theme", e.target.value)}
              className="rounded-lg border border-line bg-surface-2 px-2 py-1.5 text-xs text-brand-900">
              <option value="">All themes</option>
              {themes.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
            </select>
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-2xs uppercase tracking-wide text-slate-500">Segment</span>
            <select value={segment} onChange={(e) => setFilter("segment", e.target.value)}
              className="rounded-lg border border-line bg-surface-2 px-2 py-1.5 text-xs text-brand-900">
              <option value="">All segments</option>
              {segOptions.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
            </select>
          </label>
          <div className="mx-1 h-8 w-px bg-line" />
          <input ref={fileRef} type="file" accept=".pdf,.txt,.md,.markdown,text/*,application/pdf"
            onChange={(e) => setPending(e.target.files?.[0] ?? null)}
            className="text-xs text-slate-400 file:mr-2 file:rounded-lg file:border-0 file:bg-surface-2 file:px-3 file:py-1.5 file:text-xs file:font-semibold file:text-brand-900" />
          <button type="button" onClick={() => void doUpload()} disabled={busy || !pending}
            className="flex items-center gap-1.5 rounded-lg bg-accent-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-accent-500 disabled:opacity-40">
            {busy ? <Loader2 size={14} className="animate-spin" /> : <UploadCloud size={14} />} Upload
          </button>
        </div>
        {err && <div className="mt-2 text-2xs text-neg-100">{err}</div>}
        <div className="mt-2 text-[10px] text-slate-500">PDF / TXT / Markdown · tagged to the selected theme·segment, chunked &amp; embedded for retrieval.</div>
      </Card>

      {/* docs table */}
      <Card className="mt-3 overflow-hidden">
        <div className="grid grid-cols-[1fr_auto_auto_auto_auto_auto] items-center gap-3 border-b border-line px-4 py-2 text-2xs uppercase tracking-wide text-slate-500">
          <span>Document</span><span>Theme·Seg</span><span>Type</span><span>Size</span><span>Index</span><span></span>
        </div>
        {loading ? (
          <div className="px-4 py-8 text-center text-xs text-slate-500">Loading…</div>
        ) : docs.length === 0 ? (
          <div className="px-4 py-10 text-center text-xs text-slate-500">No documents yet — upload a report to start the room.</div>
        ) : docs.map((d) => (
          <div key={d.id} className="grid grid-cols-[1fr_auto_auto_auto_auto_auto] items-center gap-3 border-b border-line px-4 py-2 text-xs last:border-0">
            <div className="flex min-w-0 items-center gap-2">
              <FileText size={14} className="shrink-0 text-slate-500" />
              <span className="truncate font-medium text-brand-900">{d.title}</span>
            </div>
            <span className="text-slate-400">{d.theme || "—"}{d.segment ? `·${d.segment}` : ""}</span>
            <span className="text-slate-400">{d.doc_type}</span>
            <span className="tnum text-slate-400">{fmtSize(d.meta?.size)}</span>
            <span className={cn("tnum text-2xs font-semibold", d.chunk_count > 0 ? "text-pos" : "text-warn-100")}>
              {d.chunk_count > 0 ? `${d.chunk_count} chunks` : "indexing…"}
            </span>
            <span className="flex items-center gap-2">
              <a href={dataroomApi.downloadUrl(d.id)} className="text-slate-500 hover:text-accent-100" title="Download"><Download size={14} /></a>
              <button type="button" onClick={() => void del(d.id)} className="text-slate-500 hover:text-neg" title="Delete"><Trash2 size={14} /></button>
            </span>
          </div>
        ))}
      </Card>
    </div>
  );
}
