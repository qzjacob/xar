import { Sparkles } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { chathyApi, streamChat } from "../../lib/chathy";
import { ModuleShell } from "../../components/shell/ModuleShell";
import { ChatMessage as MsgView } from "../../components/chathy/ChatMessage";
import { Composer } from "../../components/chathy/Composer";
import { SessionList } from "../../components/chathy/SessionList";
import type { ChathyEvent, ChathySession, ChatMessage } from "../../types-chathy";

const SUGGESTIONS = [
  "What changed in the HBM memory segment this month?",
  "Summarize NVIDIA's supply chain and single-source risks.",
  "Which ai_optical companies have the strongest momentum?",
  "What are the upcoming catalysts for the ai_chip theme?",
];

/** XAR Chathy — the conversational, tool-calling analyst (default home). */
export function ChathyPage() {
  const [sessions, setSessions] = useState<ChathySession[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [streaming, setStreaming] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const [searchParams, setSearchParams] = useSearchParams();
  const consumedRef = useRef(false);   // 一次性消费 ?q=(从 Genny 公司页「问 Chathy」深链而来)

  const refreshSessions = useCallback(async () => {
    try { setSessions(await chathyApi.listSessions()); } catch { /* ignore */ }
  }, []);
  useEffect(() => { refreshSessions(); }, [refreshSessions]);
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages]);

  const loadSession = useCallback(async (id: string) => {
    setActiveId(id);
    try {
      const stored = await chathyApi.getMessages(id);
      const ui: ChatMessage[] = [];
      for (const m of stored) {
        if (m.role === "user") ui.push({ role: "user", content: m.content || "", tools: [] });
        else if (m.role === "assistant" && (m.content || "").trim())
          ui.push({ role: "assistant", content: m.content || "", tools: [] });
      }
      setMessages(ui);
    } catch { setMessages([]); }
  }, []);

  const newSession = useCallback(async () => {
    const s = await chathyApi.createSession();
    setActiveId(s.id);
    setMessages([]);
    refreshSessions();
    return s.id;
  }, [refreshSessions]);

  const patchAssistant = (fn: (a: ChatMessage) => ChatMessage) =>
    setMessages((m) => {
      const c = [...m];
      for (let i = c.length - 1; i >= 0; i--) {
        if (c[i].role === "assistant") { c[i] = fn(c[i]); break; }
      }
      return c;
    });

  const send = useCallback(async (text: string) => {
    let sid = activeId;
    if (!sid) sid = await newSession();
    setMessages((m) => [...m,
      { role: "user", content: text, tools: [] },
      { role: "assistant", content: "", tools: [], streaming: true }]);
    setStreaming(true);
    const ac = new AbortController();
    abortRef.current = ac;
    try {
      await streamChat(sid, text, (e: ChathyEvent) => {
        if (e.type === "delta") patchAssistant((a) => ({ ...a, content: a.content + e.text }));
        else if (e.type === "tool_start")
          patchAssistant((a) => ({ ...a, tools: [...a.tools, { id: e.id, name: e.name, args: e.args, done: false }] }));
        else if (e.type === "tool_result")
          patchAssistant((a) => ({ ...a, tools: a.tools.map((t) => t.id === e.id ? { ...t, done: true, preview: e.preview } : t) }));
        else if (e.type === "error")
          patchAssistant((a) => ({ ...a, content: `${a.content}\n\n⚠️ ${e.message}`, error: true }));
      }, ac.signal);
    } catch (err) {
      if (!ac.signal.aborted) patchAssistant((a) => ({ ...a, content: a.content || `⚠️ ${String(err)}`, error: true }));
    } finally {
      patchAssistant((a) => ({ ...a, streaming: false }));
      setStreaming(false);
      abortRef.current = null;
      refreshSessions();
    }
  }, [activeId, newSession, refreshSessions]);

  const stop = useCallback(() => { abortRef.current?.abort(); setStreaming(false); }, []);
  const del = useCallback(async (id: string) => {
    await chathyApi.deleteSession(id);
    if (id === activeId) { setActiveId(null); setMessages([]); }
    refreshSessions();
  }, [activeId, refreshSessions]);

  // 深链入口:/?q=… 从 Genny 公司页「问 Chathy」而来 → 自动发起一轮(消费后清参,只跑一次)
  useEffect(() => {
    const q = searchParams.get("q");
    if (q && !consumedRef.current) {
      consumedRef.current = true;
      setSearchParams({}, { replace: true });
      void send(q);
    }
  }, [searchParams, setSearchParams, send]);

  return (
    <ModuleShell
      sidebar={
        <SessionList sessions={sessions} activeId={activeId} onSelect={loadSession}
          onNew={() => { void newSession(); }} onDelete={(id) => { void del(id); }} />
      }
    >
      <div className="flex h-full min-h-0 flex-col">
        <div ref={scrollRef} className="scroll-thin min-h-0 flex-1 overflow-y-auto">
          <div className="mx-auto max-w-3xl px-4 py-6">
            {messages.length === 0 ? (
              <Welcome onPick={(t) => { void send(t); }} />
            ) : (
              <div className="flex flex-col gap-5">
                {messages.map((m, i) => <MsgView key={i} m={m} />)}
              </div>
            )}
          </div>
        </div>
        <Composer onSend={(t) => { void send(t); }} onStop={stop} streaming={streaming} />
      </div>
    </ModuleShell>
  );
}

function Welcome({ onPick }: { onPick: (t: string) => void }) {
  return (
    <div className="flex flex-col items-center gap-5 py-12 text-center">
      <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-accent-50 text-accent-500 ring-1 ring-inset ring-accent/25">
        <Sparkles size={22} />
      </div>
      <div>
        <div className="text-lg font-semibold text-brand-900">Ask Chathy</div>
        <div className="mt-1 max-w-md text-xs text-brand-500">
          Grounded in the XAR platform — semantic facts, dashboards, the supply-chain graph, and your Data Room.
        </div>
      </div>
      <div className="grid w-full max-w-xl grid-cols-1 gap-2 sm:grid-cols-2">
        {SUGGESTIONS.map((s) => (
          <button key={s} type="button" onClick={() => onPick(s)}
            className="rounded-lg border border-line bg-surface px-3 py-2.5 text-left text-xs text-brand-800 transition-colors hover:border-accent/40 hover:bg-surface-2">
            {s}
          </button>
        ))}
      </div>
    </div>
  );
}
