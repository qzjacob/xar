import { MessageSquarePlus, Trash2 } from "lucide-react";
import type { ChathySession } from "../../types-chathy";
import { cn, relTime } from "../../lib/format";
import { SidebarFrame } from "../shell/SidebarFrame";

/** Chathy 左栏 — 会话即"子功能":统一 SidebarFrame 框架内的新会话按钮 + 会话列表。 */
export function SessionList({ sessions, activeId, onSelect, onNew, onDelete }: {
  sessions: ChathySession[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onNew: () => void;
  onDelete: (id: string) => void;
}) {
  return (
    <SidebarFrame
      title="Chathy"
      titleCn="对话分析"
      badge="Analyst"
      pinned={
        <button type="button" onClick={onNew}
          className="flex w-full items-center gap-2 rounded-lg bg-accent-600 px-3 py-2 text-xs font-semibold text-white hover:bg-accent-500">
          <MessageSquarePlus size={15} /> New chat · 新会话
        </button>
      }
    >
      <div className="px-2 pb-1 pt-1 text-[10px] font-semibold uppercase tracking-wider text-brand-200">
        Conversations
      </div>
      {sessions.length === 0 && (
        <div className="px-2 py-4 text-center text-2xs text-brand-200">No conversations yet</div>
      )}
      {sessions.map((s) => (
        <div key={s.id}
          className={cn("group flex items-center gap-1 rounded-md border-l-2 px-2 py-1.5",
            s.id === activeId
              ? "border-accent bg-accent/15"
              : "border-transparent hover:bg-surface-2/60")}>
          <button type="button" onClick={() => onSelect(s.id)} className="min-w-0 flex-1 text-left">
            <div className="truncate text-xs font-medium text-brand-900">{s.title || "New chat"}</div>
            <div className="text-[10px] text-brand-200">{s.n_messages} msgs · {relTime(s.updated_at)}</div>
          </button>
          <button type="button" onClick={() => onDelete(s.id)}
            className="text-brand-200 opacity-0 transition-opacity hover:text-neg group-hover:opacity-100">
            <Trash2 size={13} />
          </button>
        </div>
      ))}
    </SidebarFrame>
  );
}
