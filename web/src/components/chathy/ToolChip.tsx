import { CheckCircle2, Loader2, Wrench } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import type { ToolActivity } from "../../types-chathy";

// 从工具结果里挑出 company id → 深链回 Genny 公司页(UA-P4:跨模块闭环)。
// 只取形如 "id"/"company_id" 的值,去重;错配 id 落到公司 not-found 态,无害。
function companyIds(preview?: string): string[] {
  if (!preview) return [];
  const out = new Set<string>();
  const re = /"(?:company_)?id"\s*:\s*"([a-z0-9_]{2,40})"/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(preview)) !== null) out.add(m[1]);
  return [...out].slice(0, 8);
}

/** A collapsible chip showing one tool invocation (name + args, expandable result). */
export function ToolChip({ t }: { t: ToolActivity }) {
  const [open, setOpen] = useState(false);
  const cids = companyIds(t.preview);
  return (
    <div className="rounded-md border border-line bg-surface-2/60 text-2xs">
      <button type="button" onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-1.5 px-2 py-1 text-left">
        {t.done ? <CheckCircle2 size={12} className="shrink-0 text-pos" />
          : <Loader2 size={12} className="shrink-0 animate-spin text-accent-500" />}
        <Wrench size={11} className="shrink-0 text-slate-500" />
        <span className="font-semibold text-brand-800">{t.name}</span>
        {t.args && Object.keys(t.args).length > 0 && (
          <span className="truncate text-slate-500">{JSON.stringify(t.args)}</span>
        )}
      </button>
      {cids.length > 0 && (
        <div className="flex flex-wrap gap-1 border-t border-line px-2 py-1">
          {cids.map((id) => (
            <Link key={id} to={`/genny/company/${id}`}
              className="rounded bg-white/5 px-1.5 py-0.5 text-brand-800 hover:bg-white/10">
              {id} →
            </Link>
          ))}
        </div>
      )}
      {open && t.preview && (
        <pre className="max-h-40 overflow-auto border-t border-line px-2 py-1 font-mono text-slate-400">
          {t.preview}
        </pre>
      )}
    </div>
  );
}
