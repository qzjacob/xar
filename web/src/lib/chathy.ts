import type { ChathyEvent, ChathySession, StoredMessage } from "../types-chathy";

async function j<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(path, init);
  if (!r.ok) throw new Error(`${path} -> ${r.status}`);
  return (await r.json()) as T;
}

const JSON_HEADERS = { "Content-Type": "application/json" };

export const chathyApi = {
  listSessions: () => j<ChathySession[]>("/api/chathy/sessions"),
  createSession: (title?: string) =>
    j<{ id: string; title: string | null }>("/api/chathy/sessions", {
      method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ title: title ?? null }),
    }),
  getMessages: (sid: string) => j<StoredMessage[]>(`/api/chathy/sessions/${sid}/messages`),
  deleteSession: (sid: string) =>
    j<{ deleted: boolean }>(`/api/chathy/sessions/${sid}`, { method: "DELETE" }),
};

/** POST a message and stream the SSE reply, invoking `onEvent` per agent event. */
export async function streamChat(
  sid: string, message: string, onEvent: (e: ChathyEvent) => void, signal?: AbortSignal,
): Promise<void> {
  const r = await fetch(`/api/chathy/sessions/${sid}/chat`, {
    method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ message }), signal,
  });
  if (!r.ok || !r.body) throw new Error(`chat -> ${r.status}`);
  const reader = r.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let idx: number;
    while ((idx = buf.indexOf("\n\n")) >= 0) {
      const frame = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      const line = frame.split("\n").find((l) => l.startsWith("data: "));
      if (!line) continue;
      try {
        onEvent(JSON.parse(line.slice(6)) as ChathyEvent);
      } catch {
        /* ignore keep-alive / partial frames */
      }
    }
  }
}
