import { CheckCircle2, Loader2, Wrench } from "lucide-react";
import { useState } from "react";
import type { ToolActivity } from "../../types-chathy";

/** A collapsible chip showing one tool invocation (name + args, expandable result). */
export function ToolChip({ t }: { t: ToolActivity }) {
  const [open, setOpen] = useState(false);
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
      {open && t.preview && (
        <pre className="max-h-40 overflow-auto border-t border-line px-2 py-1 font-mono text-slate-400">
          {t.preview}
        </pre>
      )}
    </div>
  );
}
