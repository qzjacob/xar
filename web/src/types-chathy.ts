export interface ChathySession {
  id: string;
  title: string | null;
  created_at: string;
  updated_at: string;
  n_messages: number;
}

export interface StoredMessage {
  role: string;
  content: string | null;
  tool_calls?: unknown;
  tool_call_id?: string | null;
  name?: string | null;
  usage?: unknown;
  created_at: string;
}

export interface ToolActivity {
  id: string;
  name: string;
  args?: Record<string, unknown>;
  preview?: string;
  done: boolean;
}

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  tools: ToolActivity[];
  streaming?: boolean;
  error?: boolean;
}

export type ChathyEvent =
  | { type: "delta"; text: string }
  | { type: "tool_start"; id: string; name: string; args?: Record<string, unknown> }
  | { type: "tool_result"; id: string; name: string; preview?: string }
  | { type: "done"; usage?: unknown }
  | { type: "error"; message: string };
