import { ArrowUp, Square } from "lucide-react";
import { useRef, useState } from "react";

export function Composer({ onSend, onStop, streaming }: {
  onSend: (t: string) => void; onStop: () => void; streaming: boolean;
}) {
  const [text, setText] = useState("");
  // 中文/日文等输入法组合态:组合期间的 Enter 是"确认候选字",绝不能发送。
  // 双保险:composition 事件标志 + keydown 的 isComposing/keyCode 229(Safari 在
  // compositionend 之后才派发该次 Enter 的 keydown,此时 isComposing 已为 false,
  // 故再用一帧延迟的标志兜底)。
  const composingRef = useRef(false);
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
          onCompositionStart={() => { composingRef.current = true; }}
          onCompositionEnd={() => {
            // Safari 会在 compositionend 之后才派发确认那一下的 keydown(isComposing=false),
            // 延迟一帧再清标志,让那次 Enter 仍被视为"确认候选"而不是发送。
            window.setTimeout(() => { composingRef.current = false; }, 0);
          }}
          onKeyDown={(e) => {
            if (e.key !== "Enter" || e.shiftKey) return;
            if (composingRef.current || e.nativeEvent.isComposing || e.keyCode === 229) return;
            e.preventDefault();
            send();
          }}
          placeholder="Ask Chathy about a company, theme, catalyst, supply chain…"
          className="max-h-40 min-h-[24px] flex-1 resize-none bg-transparent px-1.5 py-1 text-sm text-brand-900 placeholder:text-brand-200 focus:outline-none"
        />
        {streaming ? (
          <button type="button" onClick={onStop}
            className="flex h-8 w-8 items-center justify-center rounded-lg bg-surface-2 text-brand-700 hover:text-brand-900">
            <Square size={14} />
          </button>
        ) : (
          <button type="button" onClick={send} disabled={!text.trim()}
            className="flex h-8 w-8 items-center justify-center rounded-lg bg-accent-600 text-white transition-opacity disabled:opacity-40">
            <ArrowUp size={16} />
          </button>
        )}
      </div>
      <div className="mx-auto mt-1.5 max-w-3xl text-center text-[10px] text-brand-200">
        Chathy grounds answers in the XAR platform · research aide, not financial advice
      </div>
    </div>
  );
}
