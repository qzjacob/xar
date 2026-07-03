import { MessageSquarePlus, Trash2 } from "lucide-react";
import type { ChathySession } from "../../types-chathy";
import { cn, relTime } from "../../lib/format";

export function SessionList({ sessions, activeId, onSelect, onNew, onDelete }: {
  sessions: ChathySession[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onNew: () => void;
  onDelete: (id: string) => void;
}) {
  return (
    <div className="flex h-full w-60 shrink-0 flex-col border-r border-line bg-surface">
      <div className="p-2">
        <button type="button" onClick={onNew}
          className="flex w-full items-center gap-2 rounded-lg bg-accent-600 px-3 py-2 text-xs font-semibold text-white hover:bg-accent-500">
          <MessageSquarePlus size={15} /> New chat
        </button>
      </div>
      <div className="scroll-thin min-h-0 flex-1 overflow-y-auto px-2 pb-2">
        {sessions.length === 0 && (
          <div className="px-2 py-4 text-center text-2xs text-slate-500">No conversations yet</div>
        )}
        {sessions.map((s) => (
          <div key={s.id}
            className={cn("group flex items-center gap-1 rounded-lg px-2 py-1.5",
              s.id === activeId ? "bg-surface-2" : "hover:bg-surface-2/60")}>
            <button type="button" onClick={() => onSelect(s.id)} className="min-w-0 flex-1 text-left">
              <div className="truncate text-xs font-medium text-brand-900">{s.title || "New chat"}</div>
              <div className="text-[10px] text-slate-500">{s.n_messages} msgs · {relTime(s.updated_at)}</div>
            </button>
            <button type="button" onClick={() => onDelete(s.id)}
              className="text-slate-500 opacity-0 transition-opacity hover:text-neg group-hover:opacity-100">
              <Trash2 size={13} />
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}
