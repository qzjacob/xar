import { Sparkles, User } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { ChatMessage as Msg } from "../../types-chathy";
import { cn } from "../../lib/format";
import { ToolChip } from "./ToolChip";

export function ChatMessage({ m }: { m: Msg }) {
  const isUser = m.role === "user";
  return (
    <div className={cn("flex gap-3", isUser && "flex-row-reverse")}>
      <div className={cn(
        "mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg",
        isUser ? "bg-surface-2 text-brand-500"
          : "bg-accent-50 text-accent-500 ring-1 ring-inset ring-accent/25")}>
        {isUser ? <User size={14} /> : <Sparkles size={14} />}
      </div>
      <div className={cn("min-w-0 max-w-[85%]", isUser && "flex flex-col items-end")}>
        {m.tools.length > 0 && (
          <div className="mb-1.5 flex w-full flex-col gap-1">
            {m.tools.map((t) => <ToolChip key={t.id} t={t} />)}
          </div>
        )}
        <div className={cn(
          "inline-block rounded-xl px-3.5 py-2 text-left",
          isUser ? "bg-accent-600 text-white"
            : m.error ? "bg-neg-50 text-neg-100 ring-1 ring-inset ring-neg/20"
              : "bg-surface ring-1 ring-inset ring-line")}>
          {isUser ? (
            <span className="whitespace-pre-wrap text-sm">{m.content}</span>
          ) : (
            <div className="chathy-md">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>
                {m.content || (m.streaming ? "" : "")}
              </ReactMarkdown>
            </div>
          )}
          {m.streaming && !isUser && (
            <span className="ml-0.5 inline-block h-3.5 w-1.5 animate-pulse bg-accent-500 align-middle" />
          )}
        </div>
      </div>
    </div>
  );
}
