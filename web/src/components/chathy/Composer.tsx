import { ArrowUp, Square } from "lucide-react";
import { useState } from "react";

export function Composer({ onSend, onStop, streaming }: {
  onSend: (t: string) => void; onStop: () => void; streaming: boolean;
}) {
  const [text, setText] = useState("");
  const send = () => {
    const t = text.trim();
    if (!t || streaming) return;
    onSend(t);
    setText("");
  };
  return (
    <div className="border-t border-line bg-canvas px-4 py-3">
      <div className="mx-auto flex max-w-3xl items-end gap-2 rounded-xl border border-line bg-surface p-2 focus-within:ring-1 focus-within:ring-accent/40">
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          rows={1}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              send();
            }
          }}
          placeholder="Ask Chathy about a company, theme, catalyst, supply chain…"
          className="max-h-40 min-h-[24px] flex-1 resize-none bg-transparent px-1.5 py-1 text-sm text-brand-900 placeholder:text-slate-500 focus:outline-none"
        />
        {streaming ? (
          <button type="button" onClick={onStop}
            className="flex h-8 w-8 items-center justify-center rounded-lg bg-surface-2 text-slate-300 hover:text-brand-900">
            <Square size={14} />
          </button>
        ) : (
          <button type="button" onClick={send} disabled={!text.trim()}
            className="flex h-8 w-8 items-center justify-center rounded-lg bg-accent-600 text-white transition-opacity disabled:opacity-40">
            <ArrowUp size={16} />
          </button>
        )}
      </div>
      <div className="mx-auto mt-1.5 max-w-3xl text-center text-[10px] text-slate-500">
        Chathy grounds answers in the XAR platform · research aide, not financial advice
      </div>
    </div>
  );
}
